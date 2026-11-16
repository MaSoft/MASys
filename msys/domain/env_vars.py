# -*- coding: utf-8 -*-
"""Dominio de Variables de Entorno: dataclass EnvVarRow, deteccion de
problemas (referencias sin resolver, rutas rotas, posible secuestro de
PATH, solapes Usuario/Sistema...) y los managers de lectura/escritura via
registro (VarManager) y de notas/historial (EnvVarNotesDB/EnvVarLogDB).
Se mantienen juntos porque en el original viven pegados y estan fuertemente
acoplados entre si (la logica de deteccion opera directamente sobre
EnvVarRow, VarManager la invoca al listar variables).
Extraido de multisys.py (Fase 1 de modularizacion)."""

import os
import tempfile
import subprocess
import ctypes
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Dict, Tuple

from msys import config as msys_config
from msys.config import logger
from msys.core.utils import _sqlite_connect, startupinfo_oculto

@dataclass
class EnvVarRow:
    """Variable de entorno leída directamente del registro (ver VarManager),
    no del entorno fusionado que heredó el propio proceso de MASys al
    arrancar — ese entorno fusionado no distingue solapes entre Usuario y
    Sistema (si ambos definen la misma variable con valores distintos, solo
    se ve el resultado final) ni se entera de cambios hechos en el registro
    después de que MASys ya estuviera abierto. Una misma variable puede
    aparecer en dos filas (una por 'scope') si está definida en ambos
    ámbitos."""
    name: str
    value: str
    scope: str  # "user" | "system"
    issues: List[str] = field(default_factory=list)
    severity: str = "ok"  # "ok" | "info" | "warning" | "critical"

# Alias retrocompatible: nada más del código usa el nombre antiguo tras el
# rediseño, pero se deja documentado por si aparece en un snapshot antiguo.
EnvironmentVarRow = EnvVarRow

_SEVERITY_ORDER = {"ok": 0, "info": 1, "warning": 2, "critical": 3}

def _peor_severidad(a: str, b: str) -> str:
    return a if _SEVERITY_ORDER[a] >= _SEVERITY_ORDER[b] else b

# Variables cuyo valor se interpreta como lista de rutas separadas por ';'.
_PATH_LIKE_VARS = {"PATH", "PATHEXT", "PSMODULEPATH"}

# Extensiones estándar de Windows en PATHEXT — cualquier otra se marca como
# informativa (no necesariamente mala, pero fuera de lo habitual).
_STANDARD_PATHEXT = {
    ".COM", ".EXE", ".BAT", ".CMD", ".VBS", ".VBE",
    ".JS", ".JSE", ".WSF", ".WSH", ".MSC", ".CPL",
}

# Binarios críticos del sistema — si una carpeta de PATH que aparece ANTES
# que System32 contiene un archivo con alguno de estos nombres, cualquier
# programa que invoque el comando por nombre corto ejecutaría ese archivo
# en vez del real: es la técnica de "PATH hijacking".
_CRITICAL_SYSTEM_BINARIES = {
    "cmd.exe", "powershell.exe", "powershell_ise.exe",
    "svchost.exe", "rundll32.exe", "conhost.exe", "explorer.exe",
}

_PROXY_VAR_NAMES = {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
                    "http_proxy", "https_proxy", "all_proxy"}

# Mismo criterio que _is_windows_process (Procesos): rutas dentro de una
# carpeta temporal son una señal débil pero real de persistencia de malware.
_SUSPICIOUS_PATH_FRAGMENTS = ("\\temp\\", "\\tmp\\", "\\appdata\\local\\temp\\")


def _buscar_ci(d: Dict[str, str], nombre: str) -> str:
    """Búsqueda case-insensitive en un dict de variables (Windows no
    distingue mayúsculas/minúsculas en nombres de variable, pero el
    registro conserva la grafía original, p.ej. 'Path' en vez de 'PATH')."""
    nombre_lower = nombre.lower()
    for k, v in d.items():
        if k.lower() == nombre_lower:
            return v
    return ""


def _expandir_valor_env(valor: str, vars_conocidas: Dict[str, str]) -> str:
    """Expande referencias %NOMBRE% con un diccionario combinado (registro
    Usuario + Sistema + entorno real del proceso). Hace falta el entorno
    real del proceso porque variables como %SystemRoot%, %ProgramFiles% o
    %USERPROFILE% NO están guardadas como valor explícito en ninguna de las
    dos claves de registro — las calcula Windows en otro sitio — y sin
    expandirlas antes de comprobar 'existe la ruta' cualquier valor que las
    use (p.ej. ComSpec = '%SystemRoot%\\system32\\cmd.exe') daría un falso
    positivo de 'ruta inexistente'."""
    import re

    def _reemplazar(m):
        return _buscar_ci(vars_conocidas, m.group(1)) or m.group(0)

    anterior, actual = None, valor
    for _ in range(5):  # tope de iteraciones por si hay una referencia circular
        if actual == anterior:
            break
        anterior = actual
        actual = re.sub(r"%([^%]+)%", _reemplazar, actual)
    return actual


def _detectar_problemas_env_vars(
    rows: List[EnvVarRow], user_vars: Dict[str, str], system_vars: Dict[str, str]
) -> None:
    """Rellena row.issues/row.severity para cada fila. Todas las
    comprobaciones son de solo lectura — nunca modifican nada, solo marcan."""
    import re

    # Diccionario de expansión: entorno real del proceso como base (incluye
    # variables "computadas" por Windows que no viven en el registro) más
    # el registro por encima (más actual que lo que este proceso heredó).
    vars_conocidas = {**os.environ, **system_vars, **user_vars}
    nombres_conocidos_lower = {n.lower() for n in vars_conocidas}

    # PATH efectivo combinado (Sistema + Usuario), en el mismo orden en que
    # Windows lo construye realmente para un proceso nuevo.
    path_system = _buscar_ci(system_vars, "PATH")
    path_user = _buscar_ci(user_vars, "PATH")
    path_efectivo = ";".join(p for p in (path_system, path_user) if p)
    path_entries = [p for p in path_efectivo.split(";") if p.strip()]

    system32 = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32")
    idx_system32 = next(
        (i for i, p in enumerate(path_entries) if p.strip().rstrip("\\").lower() == system32.lower()),
        None,
    )

    for row in rows:
        name_upper = row.name.upper()
        value = row.value or ""

        if not value.strip():
            row.issues.append("Valor vacío")
            row.severity = _peor_severidad(row.severity, "warning")
            continue

        for ref in re.findall(r"%([^%]+)%", value):
            if ref.lower() not in nombres_conocidos_lower:
                row.issues.append(f"Referencia sin resolver: %{ref}%")
                row.severity = _peor_severidad(row.severity, "warning")

        parece_lista_rutas = name_upper in _PATH_LIKE_VARS or (";" in value and "\\" in value)
        if parece_lista_rutas:
            partes = [p for p in value.split(";") if p.strip()]
            vistas = set()
            for p in partes:
                p_strip = _expandir_valor_env(p.strip().strip('"'), vars_conocidas)
                p_norm = p_strip.rstrip("\\").lower()
                if p_norm in vistas:
                    row.issues.append(f"Entrada duplicada: {p_strip}")
                    row.severity = _peor_severidad(row.severity, "warning")
                vistas.add(p_norm)
                if name_upper == "PATHEXT":
                    continue
                if not os.path.exists(p_strip):
                    row.issues.append(f"Ruta inexistente: {p_strip}")
                    row.severity = _peor_severidad(row.severity, "warning")
                elif any(frag in p_strip.lower() + "\\" for frag in _SUSPICIOUS_PATH_FRAGMENTS):
                    row.issues.append(f"Ruta sospechosa (carpeta temporal): {p_strip}")
                    row.severity = _peor_severidad(row.severity, "critical")

            if name_upper == "PATHEXT":
                extra = {p.strip().upper() for p in partes} - _STANDARD_PATHEXT
                if extra:
                    row.issues.append(f"Extensiones no estándar en PATHEXT: {', '.join(sorted(extra))}")
                    row.severity = _peor_severidad(row.severity, "info")

        elif "\\" in value or "/" in value:
            valor_expandido = _expandir_valor_env(value.strip().strip('"'), vars_conocidas)
            # Algunas variables no son una ruta pura sino "ruta + argumentos"
            # (p.ej. EDITOR='C:\Herramienta\app.exe -multiInst'): si el valor
            # completo no existe como ruta, probar solo el primer token antes
            # de marcarlo como roto, para no dar un falso positivo.
            primer_token = valor_expandido.split(" ", 1)[0].strip('"')
            existe = os.path.exists(valor_expandido) or (
                primer_token != valor_expandido and os.path.exists(primer_token))
            if len(valor_expandido) < 260 and not existe:
                row.issues.append("Ruta inexistente")
                row.severity = _peor_severidad(row.severity, "warning")

        if row.name in _PROXY_VAR_NAMES:
            row.issues.append("Variable de proxy definida — verificar que sea intencional")
            row.severity = _peor_severidad(row.severity, "info")

    # ── Solapes entre ámbitos (mismo nombre, valores distintos) ──
    # PATH/PATHEXT/PSModulePath están DISEÑADAS para concatenarse entre
    # ámbitos, y TEMP/TMP están diseñadas para ser distintas a propósito
    # (temporal de sistema vs. temporal del usuario) — avisar de eso en
    # todos los equipos sería ruido permanente, no una detección útil.
    _SOLAPE_ESPERADO = _PATH_LIKE_VARS | {"TEMP", "TMP"}
    comunes_lower = {n.lower() for n in user_vars} & {n.lower() for n in system_vars}
    for name_lower in comunes_lower:
        if name_lower.upper() in _SOLAPE_ESPERADO:
            continue
        val_user = _buscar_ci(user_vars, name_lower)
        val_system = _buscar_ci(system_vars, name_lower)
        if val_user != val_system:
            for row in rows:
                if row.name.lower() == name_lower:
                    row.issues.append("Definida en Usuario y Sistema con valores distintos")
                    row.severity = _peor_severidad(row.severity, "warning")

    # ── PATH combinado: longitud excesiva ──
    if len(path_efectivo) > 2047:
        for row in rows:
            if row.name.upper() == "PATH":
                row.issues.append(
                    f"PATH combinado muy largo ({len(path_efectivo)} caracteres, "
                    "límite práctico clásico ~2047)"
                )
                row.severity = _peor_severidad(row.severity, "critical")

    # ── Posible secuestro de PATH ──
    if idx_system32 is not None:
        for carpeta in path_entries[:idx_system32]:
            carpeta = carpeta.strip().strip('"')
            if not carpeta or not os.path.isdir(carpeta):
                continue
            try:
                archivos = {f.lower() for f in os.listdir(carpeta)}
            except OSError:
                continue
            interceptados = _CRITICAL_SYSTEM_BINARIES & archivos
            if interceptados:
                for row in rows:
                    if row.name.upper() == "PATH":
                        row.issues.append(
                            f"Posible secuestro de PATH: «{carpeta}» contiene "
                            f"{', '.join(sorted(interceptados))} y aparece antes que System32"
                        )
                        row.severity = _peor_severidad(row.severity, "critical")


def _broadcast_environment_change() -> None:
    """Notifica a los demás procesos (Explorer, nuevas consolas...) que el
    entorno cambió, para que no haga falta cerrar sesión. Sin esto, el
    cambio queda escrito en el registro pero ningún proceso ya abierto —ni
    siquiera uno nuevo lanzado desde Explorer— se entera hasta el próximo
    inicio de sesión."""
    try:
        HWND_BROADCAST = 0xFFFF
        WM_SETTINGCHANGE = 0x001A
        SMTO_ABORTIFHUNG = 0x0002
        resultado = ctypes.c_long()
        ctypes.windll.user32.SendMessageTimeoutW(
            HWND_BROADCAST, WM_SETTINGCHANGE, 0, "Environment",
            SMTO_ABORTIFHUNG, 5000, ctypes.byref(resultado),
        )
    except Exception as exc:
        logger.warning("No se pudo notificar el cambio de entorno (WM_SETTINGCHANGE): %s", exc)


class VarManager:
    """Lectura/escritura de variables de entorno directamente vía el
    registro (winreg) — no PowerShell, mismo criterio que ServiceManager/
    ProcessManager. Escribir en ámbito Sistema requiere privilegios de
    administrador (MASys ya se relanza elevado, ver uac_admin=True en
    multisys.spec)."""

    _HKCU_SUBKEY = r"Environment"
    _HKLM_SUBKEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"

    def _read_scope(self, hive, subkey: str) -> Dict[str, str]:
        import winreg
        result: Dict[str, str] = {}
        try:
            with winreg.OpenKey(hive, subkey) as key:
                i = 0
                while True:
                    try:
                        nombre, valor, _tipo = winreg.EnumValue(key, i)
                        result[nombre] = valor
                        i += 1
                    except OSError:
                        break
        except OSError as exc:
            logger.warning("No se pudo leer variables de entorno (%s): %s", subkey, exc)
        return result

    def list_all_vars(self) -> List[EnvVarRow]:
        import winreg
        user_vars = self._read_scope(winreg.HKEY_CURRENT_USER, self._HKCU_SUBKEY)
        system_vars = self._read_scope(winreg.HKEY_LOCAL_MACHINE, self._HKLM_SUBKEY)

        rows: List[EnvVarRow] = []
        for nombre, valor in user_vars.items():
            rows.append(EnvVarRow(name=nombre, value=valor, scope="user"))
        for nombre, valor in system_vars.items():
            rows.append(EnvVarRow(name=nombre, value=valor, scope="system"))

        _detectar_problemas_env_vars(rows, user_vars, system_vars)
        return sorted(rows, key=lambda r: (r.name.lower(), r.scope))

    def set_value(self, name: str, value: str, scope: str) -> Tuple[bool, str]:
        import winreg
        hive, subkey = (
            (winreg.HKEY_CURRENT_USER, self._HKCU_SUBKEY) if scope == "user"
            else (winreg.HKEY_LOCAL_MACHINE, self._HKLM_SUBKEY)
        )
        try:
            with winreg.OpenKey(hive, subkey, 0, winreg.KEY_SET_VALUE) as key:
                tipo = winreg.REG_EXPAND_SZ if "%" in value else winreg.REG_SZ
                winreg.SetValueEx(key, name, 0, tipo, value)
            _broadcast_environment_change()
            return True, "OK"
        except OSError as exc:
            return False, str(exc)

    def delete_value(self, name: str, scope: str) -> Tuple[bool, str]:
        import winreg
        hive, subkey = (
            (winreg.HKEY_CURRENT_USER, self._HKCU_SUBKEY) if scope == "user"
            else (winreg.HKEY_LOCAL_MACHINE, self._HKLM_SUBKEY)
        )
        try:
            with winreg.OpenKey(hive, subkey, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
            _broadcast_environment_change()
            return True, "OK"
        except OSError as exc:
            return False, str(exc)

    def export_reg_backup(self, path: str) -> Tuple[bool, str]:
        """Exporta ambas claves (Usuario y Sistema) a un único .reg
        restaurable con doble clic, vía el propio 'reg export' de Windows
        (más fiable que reconstruir el formato .reg a mano)."""
        try:
            partes = []
            with tempfile.TemporaryDirectory() as tmp:
                user_reg = os.path.join(tmp, "user.reg")
                system_reg = os.path.join(tmp, "system.reg")
                r1 = subprocess.run(
                    ["reg", "export", "HKCU\\" + self._HKCU_SUBKEY, user_reg, "/y"],
                    capture_output=True, timeout=15,
                    startupinfo=startupinfo_oculto(), creationflags=subprocess.CREATE_NO_WINDOW,
                )
                r2 = subprocess.run(
                    ["reg", "export",
                     "HKLM\\" + self._HKLM_SUBKEY, system_reg, "/y"],
                    capture_output=True, timeout=15,
                    startupinfo=startupinfo_oculto(), creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if r1.returncode != 0 and r2.returncode != 0:
                    return False, "No se pudo exportar ninguna de las dos claves de registro."
                with open(path, "w", encoding="utf-16") as out:
                    for i, fpath in enumerate((user_reg, system_reg)):
                        if not os.path.isfile(fpath):
                            continue
                        contenido = open(fpath, encoding="utf-16").read()
                        if i == 0:
                            out.write(contenido)
                        else:
                            # El segundo export repite la cabecera
                            # "Windows Registry Editor Version 5.00" — se
                            # omite para poder concatenar ambos en un .reg
                            # único válido.
                            cuerpo = contenido.split("\n", 1)[1] if "\n" in contenido else contenido
                            out.write("\n" + cuerpo)
            return True, "OK"
        except Exception as exc:
            return False, str(exc)


class EnvVarNotesDB:
    """Notas de texto libre por nombre de variable (independiente del
    ámbito), persistidas en multisys.db — misma base que ServiceNotesDB/
    ProcessNotesDB."""

    def __init__(self):
        base_dir = msys_config.app_base_dir()
        self.path = os.path.join(base_dir, "multisys.db")
        self._init_schema()

    def _conn(self):
        return _sqlite_connect(self.path)

    def _init_schema(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS env_var_notes (
                    var_name TEXT PRIMARY KEY,
                    note_text TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

    def get_all_notes(self) -> Dict[str, str]:
        with self._conn() as conn:
            cur = conn.execute("SELECT var_name, note_text FROM env_var_notes")
            return dict(cur.fetchall())

    def save_note(self, var_name: str, note_text: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO env_var_notes (var_name, note_text, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(var_name) DO UPDATE SET note_text=excluded.note_text, updated_at=excluded.updated_at",
                (var_name, note_text, datetime.now().isoformat(timespec='seconds')),
            )

    def delete_note(self, var_name: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM env_var_notes WHERE var_name = ?", (var_name,))


class EnvVarLogDB:
    """Historial de cambios (crear/editar/borrar) hechos desde MASys
    sobre variables de entorno, con el valor anterior para poder deshacer
    manualmente si algo se rompe — más importante aquí que en Servicios/
    Procesos porque tocar el registro es más delicado que reiniciar un
    servicio."""

    def __init__(self):
        base_dir = msys_config.app_base_dir()
        self.path = os.path.join(base_dir, "multisys.db")
        self._init_schema()

    def _conn(self):
        return _sqlite_connect(self.path)

    def _init_schema(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS env_var_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    var_name TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    action TEXT NOT NULL,
                    valor_anterior TEXT NOT NULL DEFAULT '',
                    valor_nuevo TEXT NOT NULL DEFAULT '',
                    timestamp TEXT NOT NULL
                )
            """)

    def add_entry(self, var_name: str, scope: str, action: str, valor_anterior: str, valor_nuevo: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO env_var_log (var_name, scope, action, valor_anterior, valor_nuevo, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (var_name, scope, action, valor_anterior, valor_nuevo,
                 datetime.now().isoformat(timespec='seconds')),
            )

    def get_entries(self, limit: int = 500) -> List[Tuple]:
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT var_name, scope, action, valor_anterior, valor_nuevo, timestamp "
                "FROM env_var_log ORDER BY id DESC LIMIT ?", (limit,)
            )
            return cur.fetchall()

