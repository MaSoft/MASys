# -*- coding: utf-8 -*-
"""Acceso remoto a equipos de clientes (4.6.0), con servidor de RustDesk propio opcional (4.11.0).

- Agenda de equipos por cliente y método preferido, en multisys.db
  (acceso_equipos). La contraseña, si se guarda, va cifrada con DPAPI del
  usuario de Windows (como movil.db): nunca en claro.
- Historial de cada conexión lanzada (acceso_historial).
- Métodos (comprobados en su fuente oficial):
    · RustDesk: «rustdesk.exe --connect <ID o IP> [--password <clave>]»
      (src/core_main.rs del repositorio oficial). Con sus servidores públicos
      o por IP directa. MASys NO lo incluye: lo busca o lleva a la descarga.
    · Asistencia rápida: app de la Tienda MicrosoftCorporationII.QuickAssist
      (Id. 9P7BP5VNWKX5, Microsoft Learn). Se localiza con Get-StartApps.
    · Escritorio remoto: «mstsc /v:<equipo>[:<puerto>]».
    · Ver la sesión de un usuario: «mstsc /shadow:<id> /v:<equipo> /control»
      (sin /noconsentprompt: el usuario acepta). Sesiones con «quser /server:».
    · Ofrecer asistencia: «msra /offerRA <equipo>».
- Nada se lanza con el intérprete de órdenes: listas de argumentos y destinos
  validados (nada que empiece por «-»).
- Servidor propio (4.11.0), comprobado en el código de RustDesk 1.5.0 y de
  rustdesk-server:
    · El cliente que se envía es el RustDesk oficial (el .exe de un solo archivo
      que se descarga) con la configuración en el NOMBRE: «rustdesk--<cadena>.exe»,
      cadena = JSON {"host","key"[,"relay"]} en base64 URL sin relleno y del revés
      (src/custom_server.rs; mismo resultado que su caso de prueba). RustDesk la
      lee de la ruta de su .exe (src/platform/windows.rs, get_license_from_exe_name).
    · El técnico conecta con «--connect <ID>@<servidor>?key=<clave>» (src/client.rs,
      initialize): no hace falta cambiar la configuración de su RustDesk.
    · Puertos de hbbs/hbbr: 21115 TCP (prueba del tipo de red = 21116 - 1,
      src/common.rs), 21116 TCP y UDP (RENDEZVOUS_PORT), 21117 TCP (RELAY_PORT,
      hbb_common/src/config.rs). Clave pública: id_ed25519.pub junto a hbbs.exe
      (base64 estándar de 32 bytes, rustdesk-server src/common.rs).

Sin Qt: la ventana está en msys/ui/acceso_remoto_dialog.py."""

import base64
import json
import os
import re
import shutil
import socket
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

from msys import config as msys_config
from msys.core.utils import _sqlite_connect
from msys.domain import clientes

RUSTDESK = "rustdesk"
ASISTENCIA = "asistencia"
RDP = "rdp"
SOMBRA = "sombra"
OFRECER = "ofrecer"

# (clave, nombre, qué dato del equipo usa: "id" = ID de RustDesk o dirección, "direccion", "" = ninguno)
METODOS = [
    (RUSTDESK, "RustDesk", "id"),
    (ASISTENCIA, "Asistencia rápida", ""),
    (RDP, "Escritorio remoto", "direccion"),
    (SOMBRA, "Ver la sesión de un usuario", "direccion"),
    (OFRECER, "Ofrecer asistencia", "direccion"),
]
NOMBRE_METODO = {k: n for k, n, _d in METODOS}
DATO_METODO = {k: d for k, _n, d in METODOS}

URL_RUSTDESK = "https://rustdesk.com/download"
URI_TIENDA_ASISTENCIA = "ms-windows-store://pdp/?ProductId=9P7BP5VNWKX5"
PAQUETE_ASISTENCIA = "MicrosoftCorporationII.QuickAssist"

_RE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]{2,63}$")
_RE_HOST = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9\-_.]{0,252})$")


@dataclass
class Equipo:
    id: str
    cliente: str
    nombre: str
    metodo: str = RUSTDESK
    rustdesk_id: str = ""
    direccion: str = ""
    usuario: str = ""
    notas: str = ""
    con_clave: bool = False
    ultima: str = ""          # ISO de la última conexión lanzada
    servidor_propio: bool = False   # RustDesk por el servidor propio (4.11.0)
    # 4.13.0: «cliente» es la EMPRESA; usuario del cliente y sus datos (copia: el catálogo de clientes.py es para elegir).
    # «usuario» (arriba) sigue siendo la cuenta de Windows para Escritorio remoto.
    contacto: str = ""
    correo: str = ""
    telefono: str = ""


@dataclass
class Conexion:
    fecha: str                # ISO
    cliente: str
    equipo: str
    metodo: str
    usuario: str
    resultado: str


@dataclass
class Sesion:
    id: str
    usuario: str
    nombre: str
    estado: str


# ── Validación ───────────────────────────────────────────────────────────
def es_ip(texto: str) -> bool:
    for familia in (socket.AF_INET, socket.AF_INET6):
        try:
            socket.inet_pton(familia, texto)
            return True
        except (OSError, ValueError):
            pass
    return False


def separar_direccion(texto: str) -> Tuple[str, Optional[int]]:
    """«equipo», «equipo:3390», «192.168.1.5», «[fe80::1]:3390» -> (equipo, puerto).
    ValueError si no es un nombre de equipo, dominio o IP válido."""
    t = (texto or "").strip()
    puerto = None
    if t.startswith("["):
        cierre = t.find("]")
        if cierre < 0:
            raise ValueError(f"«{texto}» no es una dirección válida")
        host, resto = t[1:cierre], t[cierre + 1:]
        if resto:
            if not resto.startswith(":") or not resto[1:].isdigit():
                raise ValueError(f"«{texto}» no es una dirección válida")
            puerto = int(resto[1:])
    elif t.count(":") == 1:
        host, p = t.split(":")
        if not p.isdigit():
            raise ValueError(f"«{texto}»: el puerto debe ser un número")
        puerto = int(p)
    else:
        host = t
    if puerto is not None and not 1 <= puerto <= 65535:
        raise ValueError(f"«{texto}»: el puerto va de 1 a 65535")
    if not host or host.startswith("-") or not (es_ip(host) or _RE_HOST.match(host)):
        raise ValueError(f"«{texto}» no es un nombre de equipo, dominio o IP válido")
    return host, puerto


def validar_id_rustdesk(texto: str) -> str:
    """ID de RustDesk (números, o alias) o una IP / nombre (acceso directo por IP)."""
    t = re.sub(r"\s+", "", texto or "")
    if t.isdigit() and len(t) < 6:
        raise ValueError(f"«{texto}» es demasiado corto para un ID de RustDesk")
    if _RE_ID.match(t) or es_ip(t):
        return t
    try:
        host, puerto = separar_direccion(t)
        return t if puerto else host
    except ValueError:
        raise ValueError(f"«{texto}» no es un ID de RustDesk ni una dirección válida") from None


def errores_equipo(e: Equipo) -> List[str]:
    """Lo que falta o está mal para poder guardar el equipo."""
    errores = []
    if not e.cliente.strip():
        errores.append("Falta la empresa")
    if not e.contacto.strip():
        errores.append("Falta el usuario")
    for validar, valor in ((clientes.validar_correo, e.correo), (clientes.validar_telefono, e.telefono)):
        try:
            validar(valor)
        except ValueError as ex:
            errores.append(str(ex))
    if not e.nombre.strip():
        errores.append("Falta el nombre del equipo")
    if e.metodo not in NOMBRE_METODO:
        errores.append("Método desconocido")
    if e.rustdesk_id.strip():
        try:
            validar_id_rustdesk(e.rustdesk_id)
        except ValueError as ex:
            errores.append(str(ex))
    if e.direccion.strip():
        try:
            separar_direccion(e.direccion)
        except ValueError as ex:
            errores.append(str(ex))
    dato = DATO_METODO.get(e.metodo, "")
    if dato == "id" and not (e.rustdesk_id.strip() or e.direccion.strip()):
        errores.append("RustDesk necesita el ID (o la dirección)")
    if dato == "direccion" and not e.direccion.strip():
        errores.append(f"{NOMBRE_METODO[e.metodo]} necesita la dirección del equipo")
    return errores


# ── Órdenes de cada método (listas de argumentos, nunca una cadena para el intérprete) ──
def orden_rustdesk(exe: str, e: Equipo, clave: str = "", servidor: Optional["Servidor"] = None) -> List[str]:
    destino = validar_id_rustdesk(e.rustdesk_id or e.direccion)
    if e.servidor_propio and e.rustdesk_id.strip():
        # Por IP directa no hay servidor de por medio; con ID, el del cliente está dado de alta en el nuestro
        if servidor is None or not servidor.configurado:
            raise ValueError("Este equipo conecta por tu servidor propio y aún no está configurado "
                             "(«🛰️ Servidor propio»)")
        destino = destino_por_servidor(destino, servidor)
    argv = [exe, "--connect", destino]
    if clave:
        # RustDesk solo admite la clave por argumento: queda visible para los administradores de ESTE
        # equipo mientras dura el arranque; sin clave guardada, RustDesk la pide en su ventana.
        argv += ["--password", clave]
    return argv


def orden_rdp(e: Equipo) -> List[str]:
    host, puerto = separar_direccion(e.direccion)
    destino = f"[{host}]" if ":" in host else host
    return ["mstsc", f"/v:{destino}" + (f":{puerto}" if puerto else "")]


def orden_sombra(e: Equipo, sesion_id: str) -> List[str]:
    if not str(sesion_id).isdigit():
        raise ValueError("Sesión no válida")
    host, _p = separar_direccion(e.direccion)
    return ["mstsc", f"/shadow:{sesion_id}", f"/v:{host}", "/control"]


def orden_ofrecer(e: Equipo) -> List[str]:
    host, _p = separar_direccion(e.direccion)
    return ["msra", "/offerRA", host]


# ── Programas ────────────────────────────────────────────────────────────
def buscar_rustdesk(ruta_guardada: str = "") -> str:
    candidatos = [ruta_guardada,
                  os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "RustDesk", "rustdesk.exe"),
                  os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), "RustDesk", "rustdesk.exe"),
                  shutil.which("rustdesk") or ""]
    for c in candidatos:
        if c and os.path.isfile(c) and os.path.basename(c).lower().startswith("rustdesk") and c.lower().endswith(".exe"):
            return c
    return ""


def _sin_ventana():
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    return si


def buscar_asistencia_rapida() -> str:
    """AppID de Asistencia rápida (app de la Tienda) o "" si no está instalada."""
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                            "(Get-StartApps | Where-Object { $_.AppID -like '*QuickAssist*' } | "
                            "Select-Object -First 1 -ExpandProperty AppID)"],
                           capture_output=True, text=True, timeout=20, startupinfo=_sin_ventana(),
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        appid = (r.stdout or "").strip().splitlines()
        return appid[0].strip() if appid and PAQUETE_ASISTENCIA.lower() in appid[0].lower() else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def lanzar(argv: List[str]) -> None:
    """Abre el programa y no espera a que termine. OSError si no se puede."""
    subprocess.Popen(argv, close_fds=True, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))


def lanzar_asistencia_rapida(appid: str) -> None:
    os.startfile(f"shell:AppsFolder\\{appid}")


# ── Sesiones de un equipo (para «Ver la sesión de un usuario») ───────────
def leer_quser(texto: str) -> List[Sesion]:
    """Filas de «quser» (en cualquier idioma): NOMBRE_USUARIO NOMBRE_SESIÓN ID ESTADO …
    El nombre de sesión puede faltar (sesiones desconectadas): el ID es el primer número."""
    sesiones = []
    for linea in texto.splitlines()[1:]:
        partes = linea.strip().lstrip(">").split()
        if len(partes) < 3:
            continue
        i = next((n for n, p in enumerate(partes) if p.isdigit()), -1)
        if i < 1 or i + 1 >= len(partes):
            continue
        usuario = partes[0]
        nombre = partes[1] if i == 2 else ""
        sesiones.append(Sesion(partes[i], usuario, nombre, partes[i + 1]))
    return sesiones


def sesiones_de(e: Equipo) -> List[Sesion]:
    host, _p = separar_direccion(e.direccion)
    try:
        r = subprocess.run(["quser", f"/server:{host}"], capture_output=True, timeout=30,
                           startupinfo=_sin_ventana(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except FileNotFoundError:
        raise RuntimeError("Este Windows no tiene «quser» (las ediciones Home no lo traen)") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"{host} no responde") from None
    salida = (r.stdout or b"").decode("cp850", errors="replace")
    sesiones = leer_quser(salida)
    if not sesiones:
        error = (r.stderr or b"").decode("cp850", errors="replace").strip()
        raise RuntimeError(error or f"No hay sesiones abiertas en {host}")
    return sesiones


# ── Servidor propio (4.11.0) ────────────────────────────────────────────
PUERTO_ID = 21116           # hbbs: identificación (TCP y UDP)
PUERTO_NAT = PUERTO_ID - 1  # hbbs: prueba del tipo de red (TCP)
PUERTO_RELAY = PUERTO_ID + 1  # hbbr: retransmisión (TCP)
ARCHIVO_CLAVE = "id_ed25519.pub"
PREFIJO_CLIENTE = "rustdesk--"
URL_SERVIDOR = "https://github.com/rustdesk/rustdesk-server/releases"


@dataclass
class Servidor:
    host: str = ""            # nombre o IP, con «:puerto» si no es el 21116
    clave: str = ""           # clave pública del servidor (id_ed25519.pub)
    relay: str = ""           # solo si hbbr está en otro equipo

    @property
    def configurado(self) -> bool:
        return bool(self.host and self.clave)


def _host_con_puerto(texto: str, que: str) -> str:
    t = re.sub(r"^[a-z]+://", "", (texto or "").strip(), flags=re.I).strip("/")
    try:
        host, puerto = separar_direccion(t)
    except ValueError:
        raise ValueError(f"{que}: «{texto}» no es un nombre de DNS o una IP válida") from None
    if ":" in host:
        host = f"[{host}]"
    return f"{host}:{puerto}" if puerto else host


def validar_clave_servidor(texto: str) -> str:
    """Clave pública de hbbs: base64 estándar de 32 bytes (lo que hay dentro de id_ed25519.pub)."""
    t = re.sub(r"\s+", "", texto or "")
    try:
        if len(base64.b64decode(t, validate=True)) == 32:
            return t
    except (ValueError, TypeError):
        pass
    raise ValueError("La clave no es válida: copia el contenido de id_ed25519.pub (la carpeta del servidor)")


def validar_servidor(host: str, clave: str, relay: str = "") -> Servidor:
    if not (host or "").strip():
        raise ValueError("Falta la dirección del servidor")
    return Servidor(_host_con_puerto(host, "Servidor"), validar_clave_servidor(clave),
                    _host_con_puerto(relay, "Retransmisión") if (relay or "").strip() else "")


def puertos_servidor(s: Servidor) -> List[Tuple[str, int, str, str]]:
    """(equipo, puerto, protocolo, para qué) que deben llegar al servidor. Con otro puerto en la dirección,
    RustDesk usa ese y los de al lado (increase_port de src/common.rs)."""
    host, puerto = separar_direccion(s.host)
    base = puerto or PUERTO_ID
    filas = [(host, base - 1, "TCP", "Prueba del tipo de red"),
             (host, base, "TCP y UDP", "Identificación (ID)")]
    if s.relay:
        rhost, rpuerto = separar_direccion(s.relay)
        filas.append((rhost, rpuerto or PUERTO_RELAY, "TCP", "Retransmisión (otro equipo)"))
    else:
        filas.append((host, base + 1, "TCP", "Retransmisión"))
    return filas


def comprobar_servidor(s: Servidor, timeout: float = 4.0) -> List[Tuple[str, int, str, str, bool, str]]:
    """Intenta abrir cada puerto TCP. El UDP no se puede comprobar así (no contesta sin un saludo de RustDesk)."""
    resultado = []
    for host, puerto, proto, uso in puertos_servidor(s):
        try:
            with socket.create_connection((host, puerto), timeout=timeout):
                pass
            bien, detalle = True, "Responde"
        except socket.gaierror:
            bien, detalle = False, f"No se encuentra «{host}» (DNS)"
        except socket.timeout:
            bien, detalle = False, "No contesta: ¿puerto sin redirigir en el router o firewall?"
        except ConnectionRefusedError:
            bien, detalle = False, "Rechazado: el equipo existe pero el servidor no está arrancado"
        except OSError as e:
            bien, detalle = False, str(e)
        resultado.append((host, puerto, proto, uso, bien, detalle))
    return resultado


def destino_por_servidor(destino: str, s: Servidor) -> str:
    """«<ID>@<servidor>?key=<clave>»: RustDesk busca el ID en ESE servidor sin tocar su configuración."""
    return f"{destino}@{s.host}?key={s.clave}"


def cadena_config(s: Servidor) -> str:
    """Lo que va en el nombre del cliente. Sin «--» dentro ni «-» en los extremos: RustDesk parte el nombre
    por «--». Si sale alguno, se cambia la codificación con espacios en el JSON (los admite)."""
    datos = {"host": s.host, "key": s.clave}
    if s.relay:
        datos["relay"] = s.relay
    compacto = json.dumps(datos, separators=(",", ":"))
    for espacios in range(0, 12):
        texto = "{" + " " * espacios + compacto[1:]
        cadena = base64.urlsafe_b64encode(texto.encode("utf-8")).decode("ascii").rstrip("=")[::-1]
        if "--" not in cadena and not cadena.startswith("-") and not cadena.endswith("-"):
            return cadena
    raise ValueError("No se pudo preparar el nombre del cliente con estos datos")


def leer_nombre_cliente(nombre: str) -> Optional[Servidor]:
    """Lo que RustDesk leerá del nombre (para comprobar el que generamos). None si no lleva configuración."""
    base = os.path.basename(nombre)
    if base.lower().endswith(".exe"):
        base = base[:-4]
    base = base.replace("-licensed---", "--").replace("-licensed--", "--").replace("-licensed-", "--")
    for parte in base.split("--"):
        parte = parte.strip()
        for trozo in [parte] + (parte.split("(") if "(" in parte else []):
            trozo = trozo.strip()[::-1]
            try:
                datos = json.loads(base64.urlsafe_b64decode(trozo + "=" * (-len(trozo) % 4)))
            except (ValueError, TypeError):
                continue
            if isinstance(datos, dict) and datos.get("host"):
                return Servidor(str(datos.get("host", "")), str(datos.get("key", "")), str(datos.get("relay", "")))
    return None


def nombre_cliente(s: Servidor) -> str:
    return f"{PREFIJO_CLIENTE}{cadena_config(s)}.exe"


def problema_origen_cliente(ruta: str) -> str:
    """"" si sirve para generar el cliente: el RustDesk oficial de un solo archivo que se descarga
    (rustdesk-1.x.y-x86_64.exe). El rustdesk.exe instalado NO sirve: necesita las DLL de su carpeta."""
    if not ruta or not os.path.isfile(ruta):
        return "No se encuentra el archivo"
    base = os.path.basename(ruta).lower()
    if not (base.startswith("rustdesk") and base.endswith(".exe")):
        return "Tiene que ser el RustDesk descargado (rustdesk-….exe)"
    if os.path.isfile(os.path.join(os.path.dirname(ruta), "librustdesk.dll")):
        return ("Ese es el RustDesk instalado (necesita los archivos de su carpeta). Elige el que se descarga "
                "de la página oficial, de un solo archivo")
    try:
        with open(ruta, "rb") as f:
            if f.read(2) != b"MZ":
                return "No es un programa de Windows"
    except OSError as e:
        return f"No se puede leer: {e}"
    return ""


def generar_cliente(origen: str, carpeta: str, s: Servidor, sobrescribir: bool = False) -> str:
    """Copia el RustDesk oficial con la configuración del servidor en el nombre. Devuelve la ruta creada.
    FileExistsError si ya existe y no se pide sobrescribir."""
    if not s.configurado:
        raise ValueError("Configura primero el servidor (dirección y clave)")
    problema = problema_origen_cliente(origen)
    if problema:
        raise ValueError(problema)
    if not carpeta or not os.path.isdir(carpeta):
        raise ValueError("La carpeta de destino no existe")
    destino = os.path.join(carpeta, nombre_cliente(s))
    if len(destino) >= 260:
        raise ValueError("La ruta queda demasiado larga para Windows: elige una carpeta más corta (p. ej. el Escritorio)")
    if "host=" in destino.lower():
        raise ValueError("La carpeta no puede contener «host=» (RustDesk lo leería como configuración)")
    if os.path.exists(destino) and not sobrescribir:
        raise FileExistsError(destino)
    if leer_nombre_cliente(destino) != s:
        raise ValueError("El nombre generado no se lee igual que la configuración (no se crea)")
    shutil.copy2(origen, destino)
    return destino


def leer_clave_archivo(ruta: str) -> str:
    """Clave pública desde id_ed25519.pub (nunca el id_ed25519 privado)."""
    if os.path.basename(ruta).lower() == "id_ed25519":
        raise ValueError("Ese es el archivo PRIVADO del servidor: elige id_ed25519.pub")
    try:
        if os.path.getsize(ruta) > 4096:
            raise ValueError("Ese archivo no es una clave pública de RustDesk")
        with open(ruta, encoding="ascii", errors="replace") as f:
            return validar_clave_servidor(f.read())
    except OSError as e:
        raise ValueError(f"No se puede leer: {e}") from None


# ── Texto para el cliente ────────────────────────────────────────────────
def texto_para_cliente(metodo: str, propio: bool = False) -> str:
    if metodo == RUSTDESK and propio:
        return ("Para que pueda conectarme a tu equipo:\n"
                "1. Guarda el programa que te envío (empieza por «rustdesk--»). No le cambies el nombre: "
                "lleva la configuración para conectar conmigo.\n"
                "2. Ábrelo (no hace falta instalarlo).\n"
                "3. Dime el ID y la contraseña que aparecen en la ventana.\n"
                "4. Cuando me conecte, acepta la conexión.")
    if metodo == ASISTENCIA:
        return ("Para que pueda ayudarte en tu equipo:\n"
                "1. Pulsa a la vez las teclas Ctrl + Windows + Q (o busca «Asistencia rápida» en Inicio).\n"
                "2. En «Obtener ayuda», escribe el código que te daré y pulsa «Enviar».\n"
                "3. Acepta compartir la pantalla cuando te lo pida.")
    return ("Para que pueda conectarme a tu equipo:\n"
            f"1. Descarga RustDesk desde su página oficial: {URL_RUSTDESK}\n"
            "2. Abre el archivo descargado (no hace falta instalarlo).\n"
            "3. Dime el ID y la contraseña que aparecen en la ventana.\n"
            "4. Cuando me conecte, acepta la conexión.")


# ── Base de datos ────────────────────────────────────────────────────────
def _proteger(texto: str) -> bytes:
    import win32crypt
    return win32crypt.CryptProtectData(texto.encode("utf-8"), "MASys acceso remoto", None, None, None, 0)


def _desproteger(datos: bytes) -> str:
    import win32crypt
    return win32crypt.CryptUnprotectData(datos, None, None, None, 0)[1].decode("utf-8")


class AccesoDB:
    """Agenda de equipos, historial y ajustes del acceso remoto en multisys.db."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or os.path.join(msys_config.app_base_dir(), "multisys.db")
        self._init_schema()

    def _conn(self):
        return _sqlite_connect(self.path)

    def _init_schema(self):
        with self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS acceso_equipos (
                id TEXT PRIMARY KEY, cliente TEXT NOT NULL, nombre TEXT NOT NULL, metodo TEXT NOT NULL,
                rustdesk_id TEXT NOT NULL DEFAULT '', direccion TEXT NOT NULL DEFAULT '',
                usuario TEXT NOT NULL DEFAULT '', clave BLOB, notas TEXT NOT NULL DEFAULT '',
                ultima TEXT NOT NULL DEFAULT '', creado TEXT NOT NULL, modificado TEXT NOT NULL)""")
            c.execute("""CREATE TABLE IF NOT EXISTS acceso_historial (
                id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT NOT NULL, equipo_id TEXT NOT NULL,
                cliente TEXT NOT NULL, equipo TEXT NOT NULL, metodo TEXT NOT NULL,
                usuario TEXT NOT NULL DEFAULT '', resultado TEXT NOT NULL)""")
            c.execute("CREATE TABLE IF NOT EXISTS acceso_ajustes (clave TEXT PRIMARY KEY, valor TEXT NOT NULL)")
            columnas = {f[1] for f in c.execute("PRAGMA table_info(acceso_equipos)")}
            if "servidor_propio" not in columnas:     # 4.11.0, aditiva
                c.execute("ALTER TABLE acceso_equipos ADD COLUMN servidor_propio INTEGER NOT NULL DEFAULT 0")
            for columna in ("contacto", "correo", "telefono"):     # 4.13.0, aditivas
                if columna not in columnas:
                    c.execute(f"ALTER TABLE acceso_equipos ADD COLUMN {columna} TEXT NOT NULL DEFAULT ''")

    # Equipos
    def listar(self) -> List[Equipo]:
        with self._conn() as c:
            filas = c.execute("""SELECT id, cliente, nombre, metodo, rustdesk_id, direccion, usuario, notas,
                                 clave IS NOT NULL AND length(clave) > 0, ultima, servidor_propio, contacto, correo,
                                 telefono
                                 FROM acceso_equipos ORDER BY cliente COLLATE NOCASE, nombre COLLATE NOCASE""").fetchall()
        return [Equipo(f[0], f[1], f[2], f[3], f[4], f[5], f[6], f[7], bool(f[8]), f[9], bool(f[10]), f[11], f[12], f[13])
                for f in filas]

    def nuevo_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def guardar(self, e: Equipo, clave: Optional[str] = None, quitar_clave: bool = False) -> None:
        """clave=None deja la guardada como estaba; quitar_clave la borra."""
        errores = errores_equipo(e)
        if errores:
            raise ValueError("\n".join(errores))
        ahora = datetime.now().isoformat(timespec="seconds")
        with self._conn() as c:
            c.execute("""INSERT INTO acceso_equipos (id, cliente, nombre, metodo, rustdesk_id, direccion, usuario,
                         notas, ultima, creado, modificado, servidor_propio, contacto, correo, telefono)
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?)
                         ON CONFLICT(id) DO UPDATE SET cliente = excluded.cliente, nombre = excluded.nombre,
                         metodo = excluded.metodo, rustdesk_id = excluded.rustdesk_id, direccion = excluded.direccion,
                         usuario = excluded.usuario, notas = excluded.notas, modificado = excluded.modificado,
                         servidor_propio = excluded.servidor_propio, contacto = excluded.contacto,
                         correo = excluded.correo, telefono = excluded.telefono""",
                      (e.id, clientes.limpiar(e.cliente), e.nombre.strip(), e.metodo, e.rustdesk_id.strip(),
                       e.direccion.strip(), e.usuario.strip(), e.notas.strip(), ahora, ahora, int(e.servidor_propio),
                       clientes.limpiar(e.contacto), e.correo.strip(), clientes.limpiar(e.telefono)))
            if quitar_clave:
                c.execute("UPDATE acceso_equipos SET clave = NULL WHERE id = ?", (e.id,))
            elif clave:
                c.execute("UPDATE acceso_equipos SET clave = ? WHERE id = ?", (_proteger(clave), e.id))

    def clave(self, equipo_id: str) -> str:
        """Contraseña guardada en claro (solo en memoria) o "" si no hay o no se puede descifrar
        (p. ej. multisys.db copiada de otro usuario de Windows)."""
        with self._conn() as c:
            f = c.execute("SELECT clave FROM acceso_equipos WHERE id = ?", (equipo_id,)).fetchone()
        if not f or not f[0]:
            return ""
        try:
            return _desproteger(bytes(f[0]))
        except Exception:
            return ""

    def eliminar(self, equipo_id: str) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM acceso_equipos WHERE id = ?", (equipo_id,))

    def clientes(self) -> List[str]:
        with self._conn() as c:
            return [f[0] for f in c.execute("SELECT DISTINCT cliente FROM acceso_equipos ORDER BY cliente COLLATE NOCASE")]

    # Historial
    def registrar(self, e: Equipo, metodo: str, resultado: str) -> None:
        ahora = datetime.now().isoformat(timespec="seconds")
        with self._conn() as c:
            c.execute("""INSERT INTO acceso_historial (fecha, equipo_id, cliente, equipo, metodo, usuario, resultado)
                         VALUES (?, ?, ?, ?, ?, ?, ?)""",
                      (ahora, e.id, e.cliente, e.nombre, NOMBRE_METODO.get(metodo, metodo),
                       os.environ.get("USERNAME", ""), resultado))
            if resultado == "Iniciada":
                c.execute("UPDATE acceso_equipos SET ultima = ? WHERE id = ?", (ahora, e.id))

    def historial(self, limite: int = 5000) -> List[Conexion]:
        with self._conn() as c:
            filas = c.execute("""SELECT fecha, cliente, equipo, metodo, usuario, resultado FROM acceso_historial
                                 ORDER BY id DESC LIMIT ?""", (limite,)).fetchall()
        return [Conexion(*f) for f in filas]

    # Ajustes
    def ajuste(self, clave: str, defecto: str = "") -> str:
        with self._conn() as c:
            f = c.execute("SELECT valor FROM acceso_ajustes WHERE clave = ?", (clave,)).fetchone()
        return f[0] if f else defecto

    def fijar_ajuste(self, clave: str, valor: str) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO acceso_ajustes (clave, valor) VALUES (?, ?) "
                      "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor", (clave, valor))

    # Servidor propio: la clave es la PÚBLICA del servidor (no es secreta: va en el nombre del cliente)
    def servidor(self) -> "Servidor":
        return Servidor(self.ajuste("servidor_host"), self.ajuste("servidor_clave"), self.ajuste("servidor_relay"))

    def direccion_ddns(self) -> str:
        """La dirección de «⚙️ Conexión…» (agente, 4.8.2), para proponerla. Solo lee: sin crear tablas."""
        try:
            with self._conn() as c:
                f = c.execute("SELECT valor FROM agente_ajustes WHERE clave = 'direccion'").fetchone()
            return f[0] if f else ""
        except Exception:
            return ""

    def fijar_servidor(self, s: "Servidor") -> None:
        with self._conn() as c:
            c.executemany("INSERT INTO acceso_ajustes (clave, valor) VALUES (?, ?) "
                          "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor",
                          [("servidor_host", s.host), ("servidor_clave", s.clave), ("servidor_relay", s.relay)])


def fecha_hora(iso: str) -> Tuple[str, str]:
    """ISO -> («DD/MM/YYYY», «HH:MM»); vacío si no hay."""
    if not iso:
        return "", ""
    try:
        d = datetime.fromisoformat(iso)
        return d.strftime("%d/%m/%Y"), d.strftime("%H:%M")
    except ValueError:
        return iso, ""
