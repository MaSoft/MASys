# -*- coding: utf-8 -*-
"""Agente de análisis remoto (4.7.0): el equipo del cliente se lee con los
MISMOS recolectores que «🔄 Sincronizar Todo» de MASys y se genera un archivo
cifrado que solo puede abrir el MASys del técnico.

Sin Qt (salvo para pintar el informe, que se hace en el lado de MASys con
sus propios modelos de tabla). El recorrido de los recolectores está en
msys/agente/recolector.py y la ventana del agente en msys/agente/ventana.py.

Cifrado (cryptography, como el enlace del móvil):
- El técnico tiene un par X25519. La privada se guarda en multisys.db
  protegida con DPAPI (agente_claves); la pública viaja como «código del
  técnico» (MSA1-…, no es secreto) que el cliente pega en el agente.
- Archivo .msysinf: MAGIA + huella(8) + pública efímera(32) + nonce(12) +
  AES-256-GCM(gzip(JSON)), clave = HKDF-SHA256(X25519(efímera, técnico)),
  datos asociados = MAGIA + huella. Sin la privada del técnico no se lee.

Antes de cifrar se QUITAN los secretos (sanear): campos de contraseña, claves
de producto (solo quedan los 5 últimos caracteres, como enseña Windows), claves
de recuperación de BitLocker y contraseñas escritas en líneas de órdenes."""

import base64
import dataclasses
import gzip
import hashlib
import json
import os
import re
import socket
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from msys import config as msys_config
from msys.core.utils import _sqlite_connect

MAGIA = b"MSYI1"
EXTENSION = ".msysinf"
FORMATO = "masys-agente"
VERSION_FORMATO = 1
PREFIJO_CODIGO = "MSA1"
_INFO_HKDF = b"masys-agente-v1"
OCULTO = "(no se incluye)"


class ErrorAgente(Exception):
    """Código del técnico no válido, archivo dañado o de otro técnico."""


# ── Qué se lee: los mismos recolectores que «🔄 Sincronizar Todo» (reporting.buscar_todo) ──
# (clave, título, sección de MASys, clase en msys/collectors.py, argumentos)
LECTORES: List[Tuple[str, str, str, str, Dict[str, Any]]] = [
    ("sistema", "Sistema", "💻 Sistema", "SystemInfoCollector", {}),
    ("unidades", "Unidades de red", "💾 Unidades Remotas", "NetworkDriveCollector", {}),
    ("credenciales", "Credenciales guardadas (sin contraseñas)", "🔑 Credenciales", "CredentialCollector", {}),
    ("wifi", "Redes WiFi guardadas (sin contraseñas)", "📶 WiFi", "WiFiCollector", {}),
    ("certificados", "Certificados", "🔐 Certificados", "CertificateCollector", {}),
    ("variables", "Variables de entorno", "🌐 Variables Entorno", "EnvironmentVarCollector", {}),
    ("procesos", "Procesos", "⚙️ Procesos", "ProcessCollector", {}),
    ("servicios", "Servicios", "🛠️ Servicios", "ServiceCollector", {}),
    ("conexiones", "Conexiones de red", "🌐 Conexiones Red", "NetworkConnectionCollector", {}),
    ("software", "Programas instalados", "📦 Software Instalado", "InstalledSoftwareCollector", {}),
    ("tareas", "Tareas programadas", "⏰ Tareas Programadas", "ScheduledTaskCollector", {}),
    ("impresoras", "Impresoras", "🖨️ Impresoras", "PrinterCollector", {}),
    ("firewall", "Firewall", "🔥 Firewall", "FirewallRuleCollector", {}),
    ("correo", "Cuentas de correo", "📧 Cuentas Correo", "EmailAccountCollector", {}),
    ("usuarios", "Usuarios", "👥 Usuarios", "UsersCollector", {}),
    ("remotas", "Conexiones remotas", "🔒 Conexiones Remotas", "RemoteConnectionCollector", {}),
    ("sesiones", "Sesiones activas", "👤 Sesiones Activas", "ActiveSessionCollector", {}),
    ("bluetooth", "Bluetooth", "🔵 Bluetooth", "BluetoothCollector", {}),
    ("historial_ps", "Historial de PowerShell", "📜 Historial PS", "PSHistoryCollector", {}),
    ("eventos", "Eventos del sistema (7 días)", "⚠️ Eventos Sistema", "SystemEventCollector",
     {"dias": 7, "incluir_security": False}),
    ("usb", "Dispositivos USB", "🔌 Dispositivos USB", "USBDeviceCollector", {}),
    ("monitores", "Gráficas y monitores", "🖥️ Gráficas y Monitores", "MonitorCollector", {}),
    ("actualizaciones", "Actualizaciones de Windows", "🔄 Actualizaciones", "WindowsUpdatesCollector", {}),
    ("controladores", "Controladores", "🖥️ Adm. Dispositivos", "DriverCollector", {}),
    ("ad", "Active Directory", "🏢 Active Directory", "ADInfoCollector", {}),
    ("directivas", "Políticas de grupo", "🏛️ Políticas de Grupo", "GroupPolicyInfoCollector", {}),
    ("inicio", "Programas de inicio", "🚀 Programas de Inicio", "StartupProgramsCollector", {}),
    ("adaptadores", "Adaptadores de red", "🌐 Redes", "NetworkAdapterCollector", {}),
    ("seguridad", "Seguridad", "🛡️ Seguridad", "SecurityInfoCollector", {}),
    ("discos", "Discos duros", "💽 Discos Duros", "DiskHealthCollector", {}),
    ("licencias", "Licencias (claves recortadas)", "🔑 Licencias", "LicenseInfoCollector", {}),
    ("paquetes", "Actualizaciones de programas (winget)", "📦 Gestor de Paquetes", "PackageUpgradeCollector", {}),
    ("red_local", "Equipos de la red local", "🔍 Red Local", "NetworkDeviceCollector", {}),
]
TITULO_LECTOR = {c: t for c, t, _s, _k, _a in LECTORES}
SECCION_LECTOR = {c: s for c, _t, s, _k, _a in LECTORES}

# Para comparar con visitas anteriores (msys/domain/visitas.py usa el atributo del modelo de la ventana):
# atributo del modelo -> (clave del lector, clave dentro del dict si el lector devuelve un dict)
MODELO_DE_VISITAS = {
    "software_model": ("software", None), "services_model": ("servicios", None),
    "startup_model": ("inicio", None), "tasks_model": ("tareas", None), "users_model": ("usuarios", None),
    "drivers_model": ("controladores", None), "firewall_model": ("firewall", "reglas"),
    "installed_updates_model": ("actualizaciones", "instaladas"), "pending_updates_model": ("actualizaciones", "pendientes"),
    "certs_model": ("certificados", None), "printers_model": ("impresoras", None),
    "env_vars_model": ("variables", None), "usb_model": ("usb", None),
}


# ── Código del técnico ───────────────────────────────────────────────────
def huella(publica: bytes) -> bytes:
    return hashlib.sha256(publica).digest()[:8]


def codigo_tecnico(publica: bytes) -> str:
    """MSA1-XXXXX-… : la clave pública (32 bytes) + 2 de control, en base32 sin relleno."""
    if len(publica) != 32:
        raise ValueError("La clave pública debe tener 32 bytes")
    cuerpo = base64.b32encode(publica + hashlib.sha256(publica).digest()[:2]).decode("ascii").rstrip("=")
    return PREFIJO_CODIGO + "-" + "-".join(cuerpo[i:i + 5] for i in range(0, len(cuerpo), 5))


def leer_codigo(texto: str) -> bytes:
    """Clave pública del técnico a partir del código pegado (tolera espacios, guiones y minúsculas)."""
    t = re.sub(r"[\s\-]", "", (texto or "").upper())
    if not t.startswith(PREFIJO_CODIGO):
        raise ErrorAgente("El código del técnico empieza por MSA1")
    t = t[len(PREFIJO_CODIGO):]
    try:
        crudo = base64.b32decode(t + "=" * (-len(t) % 8))
    except (ValueError, base64.binascii.Error):
        raise ErrorAgente("El código del técnico no es válido (revisa que esté completo)") from None
    if len(crudo) != 34 or hashlib.sha256(crudo[:32]).digest()[:2] != crudo[32:]:
        raise ErrorAgente("El código del técnico no es válido (revisa que esté completo)")
    # El último carácter lleva bits de relleno: solo vale la forma exacta (si no, una letra cambiada pasaría)
    if re.sub(r"-", "", codigo_tecnico(crudo[:32]))[len(PREFIJO_CODIGO):] != t:
        raise ErrorAgente("El código del técnico no es válido (revisa que esté completo)")
    return crudo[:32]


# ── Cifrado ──────────────────────────────────────────────────────────────
def generar_par() -> Tuple[bytes, bytes]:
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives import serialization as s
    priv = X25519PrivateKey.generate()
    return (priv.private_bytes(s.Encoding.Raw, s.PrivateFormat.Raw, s.NoEncryption()),
            priv.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw))


def _clave_sesion(compartido: bytes, efimera: bytes, destino: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=efimera + destino, info=_INFO_HKDF).derive(compartido)


def cifrar(informe: dict, publica_tecnico: bytes) -> bytes:
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives import serialization as s
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    efimera = X25519PrivateKey.generate()
    pub_efimera = efimera.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw)
    clave = _clave_sesion(efimera.exchange(X25519PublicKey.from_public_bytes(publica_tecnico)),
                          pub_efimera, publica_tecnico)
    datos = gzip.compress(json.dumps(informe, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    hu = huella(publica_tecnico)
    nonce = os.urandom(12)
    return MAGIA + hu + pub_efimera + nonce + AESGCM(clave).encrypt(nonce, datos, MAGIA + hu)


def huella_de_archivo(contenido: bytes) -> bytes:
    if not contenido.startswith(MAGIA) or len(contenido) < len(MAGIA) + 8 + 32 + 12 + 16:
        raise ErrorAgente("No es un archivo del agente de MASys")
    return contenido[len(MAGIA):len(MAGIA) + 8]


def descifrar(contenido: bytes, privadas: List[bytes]) -> dict:
    """Prueba las claves privadas del técnico (la actual y las anteriores)."""
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives import serialization as s
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    hu = huella_de_archivo(contenido)
    p = len(MAGIA) + 8
    pub_efimera, nonce, cuerpo = contenido[p:p + 32], contenido[p + 32:p + 44], contenido[p + 44:]
    for privada in privadas:
        priv = X25519PrivateKey.from_private_bytes(privada)
        publica = priv.public_key().public_bytes(s.Encoding.Raw, s.PublicFormat.Raw)
        if huella(publica) != hu:
            continue
        clave = _clave_sesion(priv.exchange(X25519PublicKey.from_public_bytes(pub_efimera)), pub_efimera, publica)
        try:
            datos = AESGCM(clave).decrypt(nonce, cuerpo, MAGIA + hu)
        except InvalidTag:
            raise ErrorAgente("El archivo está dañado o se ha modificado") from None
        informe = json.loads(gzip.decompress(datos).decode("utf-8"))
        if informe.get("formato") != FORMATO:
            raise ErrorAgente("No es un informe del agente de MASys")
        return informe
    raise ErrorAgente("Este archivo se generó con el código de otro técnico (o con una clave que ya no está)")


# ── Quitar secretos ──────────────────────────────────────────────────────
_CAMPO_SECRETO = re.compile(r"^(contrase(ñ|n)a|password|passwd|pwd|secret|secreto|token|clave_wifi|key_material|keymaterial)$",
                            re.IGNORECASE)
_CLAVE_PRODUCTO = re.compile(r"\b([A-Z0-9]{5}-){4}([A-Z0-9]{5})\b")
_CLAVE_BITLOCKER = re.compile(r"\b\d{6}(-\d{6}){7}\b")
_EN_ORDENES = [
    re.compile(r"(?i)(-(?:password|passwd|pwd|pass|contrase(?:ñ|n)a|secret|token|apikey|api_key)\s*[:=]?\s*)(\"[^\"]*\"|'[^']*'|\S+)"),
    re.compile(r"(?i)\b((?:password|passwd|pwd|pass|secret|token|apikey|api_key)\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|[^\s;&\"']+)"),
    re.compile(r"(?i)(ConvertTo-SecureString\s+(?:-String\s+)?)(\"[^\"]*\"|'[^']*')"),
    re.compile(r"(?i)(\bnet\s+user\s+\S+\s+)(?!/)([^\s/]+)"),
    re.compile(r"(?i)(/pass(?:word)?:)(\S+)"),
]


def sanear_texto(texto: str) -> str:
    t = _CLAVE_PRODUCTO.sub(lambda m: "*****-*****-*****-*****-" + m.group(2), texto)
    t = _CLAVE_BITLOCKER.sub("(clave de recuperación oculta)", t)
    for patron in _EN_ORDENES:
        t = patron.sub(lambda m: m.group(1) + "***", t)
    return t


def sanear(valor: Any, campo: str = "") -> Any:
    """Copia de los datos sin secretos (recorre listas, diccionarios y filas ya serializadas)."""
    if campo and _CAMPO_SECRETO.match(campo):
        return OCULTO if valor not in (None, "", "N/A", "(no disponible)") else valor
    if isinstance(valor, str):
        return sanear_texto(valor)
    if isinstance(valor, list):
        return [sanear(v) for v in valor]
    if isinstance(valor, dict):
        return {k: sanear(v, str(k)) for k, v in valor.items()}
    return valor


# ── Serializar lo que devuelven los recolectores ─────────────────────────
def serializar(valor: Any) -> Any:
    """A tipos JSON. Las filas (dataclasses) llevan su clase en «__clase» para poder
    reconstruirlas en MASys y pintarlas con el mismo modelo de tabla."""
    if dataclasses.is_dataclass(valor) and not isinstance(valor, type):
        d = {"__clase": type(valor).__name__}
        for f in dataclasses.fields(valor):
            d[f.name] = serializar(getattr(valor, f.name))
        return d
    if isinstance(valor, dict):
        return {str(k): serializar(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple, set)):
        return [serializar(v) for v in valor]
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    if isinstance(valor, bytes):
        return f"({len(valor)} bytes)"
    if valor is None or isinstance(valor, (str, int, float, bool)):
        return valor
    return str(valor)


def nuevo_informe(lectores_elegidos: List[str]) -> dict:
    from msys.version import APP_VERSION
    return {"formato": FORMATO, "version": VERSION_FORMATO, "app": f"MASys {APP_VERSION}", "id": uuid.uuid4().hex,
            "equipo": socket.gethostname(), "usuario": os.environ.get("USERNAME", ""),
            "dominio": os.environ.get("USERDOMAIN", ""), "inicio": datetime.now().isoformat(timespec="seconds"),
            "fin": "", "administrador": False, "lectores": list(lectores_elegidos), "secciones": {}}


def anadir_seccion(informe: dict, clave: str, datos: Any = None, error: str = "", segundos: float = 0.0) -> None:
    informe["secciones"][clave] = {"titulo": TITULO_LECTOR.get(clave, clave), "seccion": SECCION_LECTOR.get(clave, ""),
                                   "datos": sanear(serializar(datos)) if not error else None,
                                   "error": sanear_texto(error), "segundos": round(segundos, 1)}


def nombre_archivo(informe: dict) -> str:
    equipo = re.sub(r"[^A-Za-z0-9_.-]", "_", informe.get("equipo", "equipo")) or "equipo"
    fecha = (informe.get("fin") or informe.get("inicio") or "").replace("-", "").replace(":", "").replace("T", "_")[:13]
    return f"Informe_{equipo}_{fecha}{EXTENSION}"


# ── Lado de MASys: reconstruir filas y pintar ───────────────────────────
def reconstruir(valor: Any) -> Any:
    """Inverso de serializar: los dict con «__clase» vuelven a ser la fila de msys.models.rows
    (campos desconocidos se ignoran; los que falten toman su valor por defecto)."""
    if isinstance(valor, list):
        return [reconstruir(v) for v in valor]
    if isinstance(valor, dict):
        clase = valor.get("__clase")
        if clase:
            from msys.models import rows as _rows
            cls = getattr(_rows, clase, None)
            if cls is not None and dataclasses.is_dataclass(cls):
                campos = {f.name: f for f in dataclasses.fields(cls)}
                args = {k: reconstruir(v) for k, v in valor.items() if k in campos}
                faltan = [n for n, f in campos.items() if n not in args and f.default is dataclasses.MISSING
                          and f.default_factory is dataclasses.MISSING]
                for n in faltan:
                    args[n] = ""
                try:
                    return cls(**args)
                except TypeError:
                    pass
            return {k: reconstruir(v) for k, v in valor.items() if k != "__clase"}
        return {k: reconstruir(v) for k, v in valor.items()}
    return valor


def preparar(clave: str, datos: Any) -> Any:
    """Datos de un lector (ya reconstruidos) con la MISMA conversión que hace la ventana de
    MASys al recibirlos (ui/windows_updates._on_windows_updates_data, ui/package_manager._on_paquetes_data)."""
    from msys.models import rows as r
    if clave == "actualizaciones" and isinstance(datos, dict):
        res = {"Reinicio pendiente": bool(datos.get("RebootPending"))}
        for k_err, titulo in (("InstalledError", "Error al leer las instaladas"), ("PendingError", "Error al leer las pendientes")):
            if datos.get(k_err):
                res[titulo] = datos[k_err]
        res["instaladas"] = [r.InstalledUpdateRow(kb=u.get("KB", "N/A"), descripcion=u.get("Descripcion", "N/A"),
                                                  instalacion=u.get("Instalacion", "N/A"), usuario=u.get("Usuario", "N/A"))
                             for u in datos.get("Installed") or [] if isinstance(u, dict)]
        res["pendientes"] = [r.PendingUpdateRow(kb=u.get("KB", "N/A"), titulo=u.get("Titulo", "N/A"),
                                                tamano=u.get("Tamano", "N/A"), criticidad=u.get("Criticidad", "N/A"),
                                                fecha=u.get("Fecha", "N/A"), tipo=u.get("Tipo", "Software"),
                                                update_id=u.get("UpdateID", ""))
                             for u in datos.get("Pending") or [] if isinstance(u, dict)]
        return res
    if clave == "paquetes" and isinstance(datos, dict):
        return {"winget disponible": bool(datos.get("Disponible", False)),
                "paquetes con actualización": [r.PackageUpgradeRow(
                    nombre=str(p.get("Nombre", "")), id_paquete=str(p.get("Id", "")),
                    version_actual=str(p.get("VersionActual", "")), version_disponible=str(p.get("VersionDisponible", "")),
                    origen=str(p.get("Origen", "")), bloqueado=bool(p.get("Bloqueado", False)), motivo_bloqueo="")
                    for p in datos.get("Paquetes") or [] if isinstance(p, dict)]}
    return datos


def tablas_de_seccion(clave: str, seccion: dict) -> List[Tuple[str, List[str], List[List[str]]]]:
    return tablas_de(preparar(clave, reconstruir(seccion.get("datos"))), seccion.get("titulo", ""))


def _humano(clave: str) -> str:
    t = clave.replace("_", " ").strip()
    return t[:1].upper() + t[1:]


def tablas_de(datos: Any, titulo: str = "") -> List[Tuple[str, List[str], List[List[str]]]]:
    """Datos de una sección (ya reconstruidos) -> [(título, cabeceras, filas)].
    Listas de filas: con el MISMO modelo de tabla de MASys si existe (mismas columnas);
    diccionarios: los valores sueltos en una tabla Dato/Valor y cada lista en su tabla."""
    tablas: List[Tuple[str, List[str], List[List[str]]]] = []
    if isinstance(datos, list):
        if datos:
            tablas.append(_tabla_de_lista(titulo, datos))
        return tablas
    if isinstance(datos, dict):
        sueltos = []
        for k, v in datos.items():
            if isinstance(v, list):
                if v:
                    tablas.append(_tabla_de_lista(_humano(k), v))
            elif isinstance(v, dict) or dataclasses.is_dataclass(v):
                sub = vars(v) if dataclasses.is_dataclass(v) else v
                sueltos += [[f"{_humano(k)} › {_humano(str(kk))}", _texto(vv)] for kk, vv in sub.items()
                            if not isinstance(vv, (list, dict))]
            else:
                sueltos.append([_humano(k), _texto(v)])
        if sueltos:
            tablas.insert(0, (titulo or "Datos", ["Dato", "Valor"], sueltos))
    elif dataclasses.is_dataclass(datos):
        return tablas_de(vars(datos), titulo)
    return tablas


def _texto(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "Sí" if v else "No"
    if isinstance(v, (list, tuple)):
        return ", ".join(_texto(x) for x in v)
    return str(v)


def modelo_para(fila: Any):
    """Modelo de tabla de MASys que pinta esta clase de fila (por la anotación de setRows), o None."""
    import typing
    from msys.models import table_models as tm
    nombre = type(fila).__name__
    for _n, cls in vars(tm).items():
        if not isinstance(cls, type) or not hasattr(cls, "setRows") or not hasattr(cls, "HEADERS"):
            continue
        try:
            pista = typing.get_type_hints(cls.setRows).get("rows")
        except Exception:
            pista = None
        args = typing.get_args(pista) if pista is not None else ()
        if args and getattr(args[0], "__name__", "") == nombre:
            return cls
        if pista is None and cls.__name__ == nombre.replace("Row", "TableModel"):
            return cls
    return None


def _tabla_de_lista(titulo: str, filas: List[Any]) -> Tuple[str, List[str], List[List[str]]]:
    primera = filas[0]
    if dataclasses.is_dataclass(primera):
        modelo_cls = modelo_para(primera)
        if modelo_cls is not None:
            try:
                from PyQt5.QtCore import Qt
                m = modelo_cls()
                m.setRows(filas)
                cab = list(m.HEADERS)
                cuerpo = [[_texto(m.data(m.index(r, c), Qt.DisplayRole)) for c in range(len(cab))]
                          for r in range(m.rowCount())]
                return titulo, cab, cuerpo
            except Exception:
                pass
        campos = [f.name for f in dataclasses.fields(primera)]
        return titulo, [_humano(c) for c in campos], [[_texto(getattr(f, c, "")) for c in campos] for f in filas]
    if isinstance(primera, dict):
        campos = [k for k in primera.keys()]
        return titulo, [_humano(c) for c in campos], [[_texto(f.get(c, "")) if isinstance(f, dict) else _texto(f)
                                                       for c in campos] for f in filas]
    return titulo, ["Valor"], [[_texto(f)] for f in filas]


def foto_para_visitas(informe: dict) -> dict:
    """Misma «foto» que visitas.recoger() pero desde el informe del agente, para comparar
    dos informes del mismo equipo con visitas.comparar()."""
    from msys.domain import visitas

    class _Modelo:
        def __init__(self, filas):
            self._f = filas

        def rows(self):
            return self._f

    class _Host:
        pass

    host = _Host()
    secciones = informe.get("secciones", {})
    for atributo, (clave, sub) in MODELO_DE_VISITAS.items():
        datos = preparar(clave, reconstruir((secciones.get(clave) or {}).get("datos")))
        filas = None
        if isinstance(datos, list):
            filas = datos
        elif isinstance(datos, dict) and sub and isinstance(datos.get(sub), list):
            filas = datos[sub]
        if filas:
            setattr(host, atributo, _Modelo(filas))
    foto = visitas.recoger(host)
    foto["equipo"] = informe.get("equipo", "")
    foto["ts"] = informe.get("fin") or informe.get("inicio", "")
    return foto


# ── Base de datos del técnico ───────────────────────────────────────────
def _proteger(datos: bytes) -> bytes:
    import win32crypt
    return win32crypt.CryptProtectData(datos, "MASys agente", None, None, None, 0)


def _desproteger(datos: bytes) -> bytes:
    import win32crypt
    return win32crypt.CryptUnprotectData(datos, None, None, None, 0)[1]


class AgenteDB:
    """Claves del técnico e informes recibidos, en multisys.db (protegidos con DPAPI)."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or os.path.join(msys_config.app_base_dir(), "multisys.db")
        with self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS agente_claves (
                huella TEXT PRIMARY KEY, publica BLOB NOT NULL, privada BLOB NOT NULL,
                creada TEXT NOT NULL, activa INTEGER NOT NULL DEFAULT 1)""")
            c.execute("""CREATE TABLE IF NOT EXISTS agente_informes (
                id TEXT PRIMARY KEY, cliente TEXT NOT NULL, equipo TEXT NOT NULL, fecha TEXT NOT NULL,
                usuario TEXT NOT NULL DEFAULT '', apartados TEXT NOT NULL DEFAULT '', recibido TEXT NOT NULL,
                datos BLOB NOT NULL)""")

    def _conn(self):
        return _sqlite_connect(self.path)

    # Claves
    def publica_activa(self) -> bytes:
        """La clave pública activa; la primera vez crea el par."""
        with self._conn() as c:
            f = c.execute("SELECT publica FROM agente_claves WHERE activa = 1 ORDER BY creada DESC LIMIT 1").fetchone()
        if f:
            return bytes(f[0])
        return self.nueva_clave()

    def nueva_clave(self) -> bytes:
        privada, publica = generar_par()
        with self._conn() as c:
            c.execute("UPDATE agente_claves SET activa = 0")
            c.execute("INSERT INTO agente_claves (huella, publica, privada, creada, activa) VALUES (?, ?, ?, ?, 1)",
                      (huella(publica).hex(), publica, _proteger(privada), datetime.now().isoformat(timespec="seconds")))
        return publica

    def privadas(self) -> List[bytes]:
        with self._conn() as c:
            filas = c.execute("SELECT privada FROM agente_claves ORDER BY activa DESC, creada DESC").fetchall()
        res = []
        for (p,) in filas:
            try:
                res.append(_desproteger(bytes(p)))
            except Exception:
                continue  # de otro usuario de Windows
        return res

    # Informes
    def guardar_informe(self, informe: dict, cliente: str) -> str:
        if not cliente.strip():
            raise ValueError("Falta el cliente")
        iid = informe.get("id") or uuid.uuid4().hex
        secciones = informe.get("secciones", {})
        apartados = f"{sum(1 for s in secciones.values() if not s.get('error'))} de {len(secciones)}"
        datos = _proteger(gzip.compress(json.dumps(informe, ensure_ascii=False).encode("utf-8")))
        with self._conn() as c:
            c.execute("""INSERT INTO agente_informes (id, cliente, equipo, fecha, usuario, apartados, recibido, datos)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET cliente = excluded.cliente""",
                      (iid, cliente.strip(), informe.get("equipo", ""), informe.get("fin") or informe.get("inicio", ""),
                       informe.get("usuario", ""), apartados, datetime.now().isoformat(timespec="seconds"), datos))
        return iid

    def listar_informes(self) -> List[Dict[str, str]]:
        with self._conn() as c:
            filas = c.execute("""SELECT id, cliente, equipo, fecha, usuario, apartados, recibido FROM agente_informes
                                 ORDER BY cliente COLLATE NOCASE, equipo COLLATE NOCASE, fecha DESC""").fetchall()
        return [dict(zip(("id", "cliente", "equipo", "fecha", "usuario", "apartados", "recibido"), f)) for f in filas]

    def informe(self, iid: str) -> dict:
        with self._conn() as c:
            f = c.execute("SELECT datos FROM agente_informes WHERE id = ?", (iid,)).fetchone()
        if not f:
            raise ErrorAgente("Ese informe ya no está")
        try:
            return json.loads(gzip.decompress(_desproteger(bytes(f[0]))).decode("utf-8"))
        except Exception:
            raise ErrorAgente("No se puede leer el informe (¿se guardó con otro usuario de Windows?)") from None

    def anterior(self, iid: str) -> Optional[str]:
        """Informe anterior del mismo equipo y cliente."""
        with self._conn() as c:
            f = c.execute("SELECT cliente, equipo, fecha FROM agente_informes WHERE id = ?", (iid,)).fetchone()
            if not f:
                return None
            g = c.execute("""SELECT id FROM agente_informes WHERE cliente = ? AND equipo = ? AND fecha < ?
                             ORDER BY fecha DESC LIMIT 1""", f).fetchone()
        return g[0] if g else None

    def eliminar_informe(self, iid: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM agente_informes WHERE id = ?", (iid,))


def texto_para_cliente(codigo: str) -> str:
    return ("Para que pueda revisar tu equipo:\n"
            "1. Descarga el programa que te envío (MASys_Agente) y ábrelo; pedirá permiso de administrador.\n"
            f"2. Pega este código donde lo pide:\n   {codigo}\n"
            "3. Pulsa «Analizar y crear el archivo» y espera unos minutos.\n"
            "4. Envíame el archivo .msysinf que aparece en tu Escritorio.\n"
            "El programa solo lee: no cambia nada ni incluye contraseñas.")
