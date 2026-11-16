# -*- coding: utf-8 -*-
"""Motor (sin Qt) de la sección 🔗 Extensiones y programas.

Qué programa abre cada extensión y cada protocolo (http, mailto...), según el
propio Windows (AssocQueryString: la misma respuesta que da el Explorador), de
dónde sale esa elección y si está rota.

Lo que NO se hace, a propósito: fijar un programa predeterminado escribiendo
en el registro. Desde Windows 8 esa elección (UserChoice) lleva una firma que
solo pone el propio Windows; falsificarla es un truco no soportado que Windows
deshace. En su lugar:
- «Abrir con…»: el diálogo de Windows («¿Cómo quieres abrir…?» + «Usar siempre»).
- «Restablecer»: quitar la elección del usuario (Windows vuelve a preguntar).
- XML de asociaciones con DISM (exportar; importar = para usuarios nuevos o por GPO)."""

import ctypes
import os
import subprocess
import tempfile
import winreg
from ctypes import wintypes
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from msys.core.utils import startupinfo_oculto

_shlwapi = ctypes.WinDLL("shlwapi")
_AssocQueryStringW = _shlwapi.AssocQueryStringW
_AssocQueryStringW.restype = ctypes.c_long
_AssocQueryStringW.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                               wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
_SHLoadIndirectString = _shlwapi.SHLoadIndirectString
_SHLoadIndirectString.restype = ctypes.c_long
_SHLoadIndirectString.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.UINT, ctypes.c_void_p]

ASSOCF_IS_PROTOCOL = 0x1000
ASSOCSTR_EXECUTABLE = 2
ASSOCSTR_FRIENDLYDOCNAME = 3
ASSOCSTR_FRIENDLYAPPNAME = 4
ASSOCSTR_PROGID = 20

K_FILEEXTS = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts"
K_URL = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations"

ORIGEN_USUARIO = "Elegido por el usuario"
ORIGEN_SISTEMA = "Del sistema o del programa"

ESTADO_OK = "Correcto"
ESTADO_SIN = "Sin programa (Windows pregunta)"
ESTADO_NO_ENCONTRADO = "Programa no encontrado"
ESTADO_ROTA = "Elección rota (el programa ya no está)"

PROTOCOLOS_HABITUALES = ["http", "https", "ftp", "mailto", "tel", "callto", "sms", "ms-teams", "msteams", "zoommtg",
                         "skype", "steam", "magnet", "webcal", "ms-outlook", "tg", "whatsapp", "spotify", "vscode",
                         "git", "ssh", "rdp", "ms-rd", "ms-settings", "search-ms", "microsoft-edge"]


@dataclass
class Asociacion:
    clave: str          # ".pdf" o "mailto"
    protocolo: bool
    tipo: str           # "Documento Adobe Acrobat"
    programa: str       # "Adobe Acrobat"
    ejecutable: str
    progid: str
    origen: str
    estado: str


def _consulta(flags: int, que: int, clave: str) -> Optional[str]:
    n = wintypes.DWORD(2048)
    buf = ctypes.create_unicode_buffer(2048)
    hr = _AssocQueryStringW(flags, que, clave, None, buf, ctypes.byref(n))
    return buf.value if hr == 0 else None


def texto_indirecto(texto: str) -> str:
    """'@{Paquete?ms-resource://...}' o '@dll,-123' -> texto legible (o el mismo)."""
    if not texto or not texto.startswith("@"):
        return texto or ""
    buf = ctypes.create_unicode_buffer(1024)
    return buf.value if _SHLoadIndirectString(texto, buf, 1024, None) == 0 and buf.value else texto


def _existe_progid(progid: str) -> bool:
    try:
        winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid).Close()
        return True
    except OSError:
        return False


_paquetes: Optional[Dict[str, str]] = None


def _paquetes_instalados() -> Dict[str, str]:
    """Familia -> nombre completo del paquete instalado (una consulta por sesión)."""
    global _paquetes
    if _paquetes is None:
        _paquetes = {}
        try:
            import json
            r = subprocess.run(["powershell", "-NoProfile", "-Command",
                                "Get-AppxPackage | Select-Object PackageFamilyName,PackageFullName | ConvertTo-Json -Compress"],
                               capture_output=True, timeout=60, startupinfo=startupinfo_oculto(),
                               creationflags=subprocess.CREATE_NO_WINDOW)
            datos = json.loads((r.stdout or b"[]").decode("utf-8", errors="replace") or "[]")
            for d in datos if isinstance(datos, list) else [datos]:
                _paquetes[d.get("PackageFamilyName", "")] = d.get("PackageFullName", "")
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return _paquetes


def _nombre_appx(progid: str) -> str:
    """Nombre de una aplicación de la Tienda. El registro guarda el texto con la
    versión del paquete de cuando se registró; si la aplicación se actualizó,
    esa versión ya no existe y Windows no lo resuelve: se reintenta con la
    versión instalada (comprobado con Fotos 11080 -> 11090)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"{progid}\Application") as k:
            nombre = winreg.QueryValueEx(k, "ApplicationName")[0]
            try:
                aumid = winreg.QueryValueEx(k, "AppUserModelID")[0]
            except OSError:
                aumid = ""
    except OSError:
        return ""
    legible = texto_indirecto(nombre)
    if not legible.startswith("@{") or "?" not in legible:
        return legible
    paquete = nombre[2:nombre.index("?")]
    familia = aumid.split("!", 1)[0] if aumid else ""
    actual = _paquetes_instalados().get(familia)
    if actual and actual != paquete:
        legible = texto_indirecto(nombre.replace(paquete, actual, 1))
        if not legible.startswith("@{"):
            return legible
    # Último recurso: el nombre del paquete sin editor ni versión ("Microsoft.Windows.Photos" -> "Photos")
    return paquete.split("_", 1)[0].split(".")[-1]


def eleccion_usuario(clave: str, protocolo: bool) -> str:
    """ProgId que eligió el usuario (UserChoiceLatest en Windows 11 recientes, si no UserChoice)."""
    base = rf"{K_URL}\{clave}" if protocolo else rf"{K_FILEEXTS}\{clave}"
    for sub, valor in (("UserChoiceLatest\\ProgId", "ProgId"), ("UserChoiceLatest", "ProgId"), ("UserChoice", "ProgId")):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{base}\{sub}") as k:
                v = winreg.QueryValueEx(k, valor)[0]
                if v:
                    return v
        except OSError:
            continue
    return ""


def consultar(clave: str, protocolo: bool = False) -> Asociacion:
    flags = ASSOCF_IS_PROTOCOL if protocolo else 0
    progid = _consulta(flags, ASSOCSTR_PROGID, clave) or ""
    exe = _consulta(flags, ASSOCSTR_EXECUTABLE, clave) or ""
    programa = _consulta(flags, ASSOCSTR_FRIENDLYAPPNAME, clave) or ""
    tipo = "" if protocolo else (_consulta(flags, ASSOCSTR_FRIENDLYDOCNAME, clave) or "")
    if progid.startswith("AppX"):
        programa = _nombre_appx(progid) or programa
    usuario = eleccion_usuario(clave, protocolo)
    usuario_valido = bool(usuario) and _existe_progid(usuario)
    origen = ORIGEN_USUARIO if usuario_valido else ORIGEN_SISTEMA
    sin_programa = progid in ("Unknown", "Undecided") or os.path.basename(exe).lower() == "openwith.exe" or (not exe and not progid)
    if sin_programa and usuario and not usuario_valido:
        # El usuario eligió un programa que ya no existe y no hay otro: Windows pregunta cada vez
        estado = ESTADO_ROTA
        programa = ""
    elif sin_programa:
        estado = ESTADO_SIN
        programa = ""
    elif exe and os.path.isabs(exe) and not os.path.exists(exe) and not progid.startswith("AppX"):
        estado = ESTADO_NO_ENCONTRADO
    else:
        estado = ESTADO_OK
    if estado in (ESTADO_OK, ESTADO_NO_ENCONTRADO) and not programa:
        programa = os.path.splitext(os.path.basename(exe))[0] if exe else progid
    return Asociacion(clave, protocolo, tipo, programa, exe, progid, origen, estado)


def listar_extensiones(parar=None) -> List[Asociacion]:
    out = []
    i = 0
    while True:
        try:
            k = winreg.EnumKey(winreg.HKEY_CLASSES_ROOT, i)
        except OSError:
            break
        i += 1
        if not k.startswith("."):
            if out:  # HKCR está ordenado: tras las extensiones ya no hay más
                break
            continue
        if parar and parar():
            break
        out.append(consultar(k))
    return out


def _protocolos_registrados() -> List[str]:
    nombres = set(PROTOCOLOS_HABITUALES)
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, K_URL) as k:
            i = 0
            while True:
                try:
                    nombres.add(winreg.EnumKey(k, i).lower())
                except OSError:
                    break
                i += 1
    except OSError:
        pass
    return sorted(nombres)


def listar_protocolos() -> List[Asociacion]:
    out = []
    for p in _protocolos_registrados():
        try:
            winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, p).Close()
        except OSError:
            if not eleccion_usuario(p, True):
                continue  # ni registrado ni elegido: no existe en este equipo
        out.append(consultar(p, protocolo=True))
    return out


def por_programa(asociaciones: List[Asociacion]) -> List[Tuple[str, str, List[str]]]:
    """[(programa, ejecutable, [extensiones/protocolos])] ordenado por número de extensiones."""
    grupos: Dict[str, Tuple[str, List[str]]] = {}
    for a in asociaciones:
        if a.estado in (ESTADO_SIN, ESTADO_ROTA) or not a.programa:
            continue
        exe, lista = grupos.setdefault(a.programa, (a.ejecutable, []))
        lista.append(a.clave)
    return sorted(((p, e, l) for p, (e, l) in grupos.items()), key=lambda x: (-len(x[2]), x[0].lower()))


def resumen(asociaciones: List[Asociacion]) -> Dict[str, int]:
    r = {"total": len(asociaciones), "con_programa": 0, "usuario": 0, "problemas": 0}
    for a in asociaciones:
        if a.estado != ESTADO_SIN:
            r["con_programa"] += 1
        if a.origen == ORIGEN_USUARIO:
            r["usuario"] += 1
        if a.estado in (ESTADO_ROTA, ESTADO_NO_ENCONTRADO):
            r["problemas"] += 1
    return r


# ─────────────────────────────────────────────────────────────────
# Acciones
# ─────────────────────────────────────────────────────────────────

def _avisar_cambio():
    try:
        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # SHCNE_ASSOCCHANGED
    except Exception:
        pass


def restablecer(clave: str, protocolo: bool) -> Tuple[bool, str]:
    """Quita la elección del usuario: Windows vuelve a usar la del sistema o pregunta."""
    base = rf"{K_URL}\{clave}" if protocolo else rf"{K_FILEEXTS}\{clave}"
    quitadas, errores = 0, []
    for sub in ("UserChoiceLatest\\ProgId", "UserChoiceLatest", "UserChoice"):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf"{base}\{sub}")
            quitadas += 1
        except FileNotFoundError:
            continue
        except OSError as e:
            errores.append(f"{sub}: {e.strerror or e}")
    _avisar_cambio()
    if errores:
        return False, "Windows no dejó quitar la elección: " + "; ".join(errores)
    if not quitadas:
        return False, "No había ninguna elección del usuario que quitar"
    return True, ""


def abrir_con(clave: str) -> Tuple[bool, str]:
    """Muestra el diálogo «¿Cómo quieres abrir…?» de Windows con un archivo de
    ejemplo de esa extensión (marcando «Usar siempre» queda como predeterminado)."""
    if not clave.startswith(".") or any(c in clave for c in '\\/:*?"<>|'):
        return False, "Extensión no válida"
    carpeta = os.path.join(tempfile.gettempdir(), "MASys_abrir_con")
    os.makedirs(carpeta, exist_ok=True)
    ejemplo = os.path.join(carpeta, f"ejemplo{clave}")
    try:
        if not os.path.exists(ejemplo):
            open(ejemplo, "wb").close()
        subprocess.Popen([os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "OpenWith.exe"), ejemplo])
        return True, ""
    except OSError as e:
        return False, str(e)


def abrir_configuracion(protocolo: bool = False) -> None:
    os.startfile("ms-settings:defaultapps")


def _dism(argumentos: List[str]) -> Tuple[bool, str]:
    exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "Dism.exe")
    try:
        r = subprocess.run([exe, "/Online"] + argumentos, capture_output=True, timeout=180,
                           startupinfo=startupinfo_oculto(), creationflags=subprocess.CREATE_NO_WINDOW)
        salida = (r.stdout or b"").decode("cp850", errors="replace").strip()
        if r.returncode == 0:
            return True, salida
        if r.returncode == 740:
            return False, "Hace falta ejecutar MASys como administrador"
        return False, salida.splitlines()[-1] if salida else f"DISM devolvió {r.returncode}"
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)


def exportar_xml(ruta: str) -> Tuple[bool, str]:
    return _dism([f"/Export-DefaultAppAssociations:{ruta}"])


def importar_xml(ruta: str) -> Tuple[bool, str]:
    return _dism([f"/Import-DefaultAppAssociations:{ruta}"])


def quitar_xml_importado() -> Tuple[bool, str]:
    return _dism(["/Remove-DefaultAppAssociations"])
