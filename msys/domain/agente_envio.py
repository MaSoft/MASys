# -*- coding: utf-8 -*-
"""Envío directo del agente al MASys del técnico (4.7.1), sin servidor propio:
el técnico abre UN puerto en su router hacia su PC y MASys escucha en él.

- «Código de envío» (MSE1-…), uno por cliente: clave pública del técnico +
  dirección (IP pública del momento o nombre de DNS dinámico) + puerto +
  ticket de 16 bytes de UN SOLO USO que caduca. El código lleva también la
  clave pública, así que el agente cifra igual que con el código MSA1.
- El ticket nunca viaja: el agente manda el .msysinf (ya cifrado para el
  técnico) con la firma HMAC-SHA256(ticket, archivo) y la huella del ticket.
  MASys solo lo acepta si el ticket existe, no está usado ni caducado, la
  firma cuadra y el archivo se descifra con su clave privada. Entonces lo
  guarda en el cliente del ticket y lo marca como usado.
- HTTP sin TLS a propósito: el contenido ya va cifrado y firmado; lo único
  visible es que alguien envía un archivo.

Sin Qt: el servidor es un ThreadingHTTPServer en su propio hilo (como el del
móvil, msys/movil/servidor.py) y avisa con una función."""

import base64
import hashlib
import hmac
import json
import logging
import re
import secrets
import socket
import subprocess
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, List, Optional, Tuple

from msys.domain import agente as ag

log = logging.getLogger(__name__)

PUERTO = 47885
RUTA = "/api/agente/informe"
PREFIJO = "MSE1"
MAX_CUERPO = 64 * 1024 * 1024
REGLA_FIREWALL = "MASys - informes del agente"
# Puertos que ya usa MASys para otra cosa (4.8.1: el puerto de recepción se puede cambiar)
PUERTOS_OCUPADOS = {47880: "el enlace con el móvil de MASys"}
CADUCIDADES = [(1, "1 día"), (3, "3 días"), (7, "7 días"), (30, "30 días")]
_RE_HOST = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9\-.]{0,98})$")


def huella_ticket(ticket: bytes) -> str:
    return hashlib.sha256(ticket).hexdigest()[:16]


def firmar(ticket: bytes, contenido: bytes) -> str:
    return hmac.new(ticket, contenido, hashlib.sha256).hexdigest()


# ── Código de envío ─────────────────────────────────────────────────────
def _host_valido(host: str) -> str:
    h = (host or "").strip().lower()
    if not h or h.startswith("-") or not _RE_HOST.match(h) or ".." in h:
        raise ValueError(f"«{host}» no es una dirección válida (IP pública o nombre de DNS dinámico)")
    return h


def codigo_envio(publica: bytes, host: str, puerto: int, ticket: bytes) -> str:
    if len(publica) != 32 or len(ticket) != 16:
        raise ValueError("Clave o ticket con tamaño incorrecto")
    if not 1 <= int(puerto) <= 65535:
        raise ValueError("Puerto fuera de rango")
    h = _host_valido(host).encode("ascii")
    datos = publica + ticket + int(puerto).to_bytes(2, "big") + bytes([len(h)]) + h
    datos += hashlib.sha256(datos).digest()[:2]
    cuerpo = base64.b32encode(datos).decode("ascii").rstrip("=")
    return PREFIJO + "-" + "-".join(cuerpo[i:i + 5] for i in range(0, len(cuerpo), 5))


def leer_codigo_envio(texto: str) -> Tuple[bytes, str, int, bytes]:
    """(clave pública, dirección, puerto, ticket). ErrorAgente si no es un código MSE1 válido."""
    t = re.sub(r"[\s\-]", "", (texto or "").upper())
    if not t.startswith(PREFIJO):
        raise ag.ErrorAgente("No es un código de envío")
    t = t[len(PREFIJO):]
    try:
        crudo = base64.b32decode(t + "=" * (-len(t) % 8))
    except (ValueError, base64.binascii.Error):
        raise ag.ErrorAgente("El código del técnico no es válido (revisa que esté completo)") from None
    try:
        if len(crudo) < 53 or hashlib.sha256(crudo[:-2]).digest()[:2] != crudo[-2:]:
            raise ValueError
        publica, ticket = crudo[:32], crudo[32:48]
        puerto = int.from_bytes(crudo[48:50], "big")
        largo = crudo[50]
        host = crudo[51:51 + largo].decode("ascii")
        if 51 + largo + 2 != len(crudo):
            raise ValueError
        host = _host_valido(host)
        if re.sub("-", "", codigo_envio(publica, host, puerto, ticket))[len(PREFIJO):] != t:
            raise ValueError
    except (ValueError, IndexError, UnicodeDecodeError):
        raise ag.ErrorAgente("El código del técnico no es válido (revisa que esté completo)") from None
    return publica, host, puerto, ticket


def leer_cualquier_codigo(texto: str):
    """Para el agente: (clave pública, destino o None). destino = (dirección, puerto, ticket)."""
    t = re.sub(r"\s", "", (texto or "").upper())
    if t.startswith(PREFIJO):
        publica, host, puerto, ticket = leer_codigo_envio(texto)
        return publica, (host, puerto, ticket)
    return ag.leer_codigo(texto), None


# ── Lado del agente: enviar ─────────────────────────────────────────────
def enviar(destino: Tuple[str, int, bytes], contenido: bytes, timeout: float = 60) -> None:
    """Envía el .msysinf. Lanza ag.ErrorAgente con un motivo legible si no llega."""
    host, puerto, ticket = destino
    url_host = f"[{host}]" if ":" in host else host
    peticion = urllib.request.Request(
        f"http://{url_host}:{int(puerto)}{RUTA}", data=contenido, method="POST",
        headers={"Content-Type": "application/octet-stream", "X-MASys-Ticket": huella_ticket(ticket),
                 "X-MASys-Firma": firmar(ticket, contenido)})
    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as r:
            if r.status != 200:
                raise ag.ErrorAgente(f"El equipo del técnico respondió {r.status}")
    except urllib.error.HTTPError as e:
        e.close()
        motivo = {401: "el código no es válido para ese equipo", 410: "el código ya se usó o ha caducado",
                  413: "el archivo es demasiado grande", 409: "el archivo no es para ese técnico"}.get(e.code, f"error {e.code}")
        raise ag.ErrorAgente(f"El técnico no aceptó el envío: {motivo}") from None
    except (urllib.error.URLError, OSError) as e:
        raise ag.ErrorAgente("No se pudo conectar con el equipo del técnico (¿está encendido y con MASys "
                             f"recibiendo?): {getattr(e, 'reason', e)}") from None


# ── Lado del técnico: tickets ───────────────────────────────────────────
class TicketsDB:
    """Tickets de un solo uso en multisys.db (el secreto, protegido con DPAPI)."""

    def __init__(self, agente_db: ag.AgenteDB):
        self.db = agente_db
        with self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS agente_envios (
                huella TEXT PRIMARY KEY, ticket BLOB NOT NULL, cliente TEXT NOT NULL, creado TEXT NOT NULL,
                caduca TEXT NOT NULL, usado TEXT NOT NULL DEFAULT '', informe TEXT NOT NULL DEFAULT '')""")
            c.execute("CREATE TABLE IF NOT EXISTS agente_ajustes (clave TEXT PRIMARY KEY, valor TEXT NOT NULL)")

    def _conn(self):
        return self.db._conn()

    def nuevo(self, cliente: str, dias: int) -> Tuple[bytes, str]:
        """(ticket, fecha de caducidad ISO)."""
        if not cliente.strip():
            raise ValueError("Falta el cliente")
        ticket = secrets.token_bytes(16)
        ahora = datetime.now()
        caduca = (ahora + timedelta(days=int(dias))).isoformat(timespec="seconds")
        with self._conn() as c:
            c.execute("INSERT INTO agente_envios (huella, ticket, cliente, creado, caduca) VALUES (?, ?, ?, ?, ?)",
                      (huella_ticket(ticket), ag._proteger(ticket), cliente.strip(), ahora.isoformat(timespec="seconds"),
                       caduca))
        return ticket, caduca

    def comprobar(self, huella: str, firma: str, contenido: bytes) -> Tuple[int, str]:
        """(código HTTP, cliente). 200 si el ticket vale para este contenido."""
        with self._conn() as c:
            f = c.execute("SELECT ticket, cliente, caduca, usado FROM agente_envios WHERE huella = ?", (huella,)).fetchone()
        if not f:
            return 401, ""
        ticket_cifrado, cliente, caduca, usado = f
        if usado or datetime.fromisoformat(caduca) < datetime.now():
            return 410, cliente
        try:
            ticket = ag._desproteger(bytes(ticket_cifrado))
        except Exception:
            return 401, ""
        if not hmac.compare_digest(firmar(ticket, contenido), (firma or "").lower()):
            return 401, ""
        return 200, cliente

    def marcar_usado(self, huella: str, informe_id: str) -> bool:
        """Solo un envío por ticket: si dos llegan a la vez, el segundo no lo consigue."""
        with self._conn() as c:
            cur = c.execute("UPDATE agente_envios SET usado = ?, informe = ? WHERE huella = ? AND usado = ''",
                            (datetime.now().isoformat(timespec="seconds"), informe_id, huella))
            return cur.rowcount == 1

    def pendientes(self) -> List[dict]:
        ahora = datetime.now().isoformat(timespec="seconds")
        with self._conn() as c:
            filas = c.execute("""SELECT huella, cliente, creado, caduca FROM agente_envios
                                 WHERE usado = '' AND caduca > ? ORDER BY creado DESC""", (ahora,)).fetchall()
        return [dict(zip(("huella", "cliente", "creado", "caduca"), f)) for f in filas]

    def anular(self, huella: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE agente_envios SET usado = 'anulado' WHERE huella = ? AND usado = ''", (huella,))

    def ajuste(self, clave: str, defecto: str = "") -> str:
        with self._conn() as c:
            f = c.execute("SELECT valor FROM agente_ajustes WHERE clave = ?", (clave,)).fetchone()
        return f[0] if f else defecto

    def fijar_ajuste(self, clave: str, valor: str) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO agente_ajustes (clave, valor) VALUES (?, ?) "
                      "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor", (clave, valor))


# ── Lado del técnico: servidor ──────────────────────────────────────────
class _Manejador(BaseHTTPRequestHandler):
    server_version = "MASys"
    sys_version = ""
    timeout = 60          # sin conexiones colgadas eternamente

    def log_message(self, formato, *args):
        log.debug("agente %s - %s", self.client_address[0], formato % args)

    def _responder(self, codigo: int, cuerpo: dict = None):
        datos = json.dumps(cuerpo or {}).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def do_GET(self):
        self._responder(404)

    def do_POST(self):
        srv: "ServidorAgente" = self.server.dueno  # type: ignore[attr-defined]
        if self.path.split("?", 1)[0] != RUTA:
            self._responder(404)
            return
        try:
            largo = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            largo = -1
        if largo <= 0 or largo > MAX_CUERPO:
            self._responder(413 if largo > MAX_CUERPO else 400)
            return
        huella = (self.headers.get("X-MASys-Ticket") or "").strip().lower()
        firma = (self.headers.get("X-MASys-Firma") or "").strip()
        if not re.fullmatch(r"[0-9a-f]{16}", huella) or not re.fullmatch(r"[0-9a-fA-F]{64}", firma):
            self._responder(401)
            return
        contenido = self.rfile.read(largo)
        codigo, respuesta = srv.recibir(huella, firma, contenido, self.client_address[0])
        self._responder(codigo, respuesta)


class ServidorAgente:
    def __init__(self, tickets: TicketsDB, al_recibir: Optional[Callable[[str, str, str], None]] = None,
                 puerto: int = PUERTO, direccion: str = "0.0.0.0"):
        self.tickets = tickets
        self.al_recibir = al_recibir
        self.puerto = puerto
        self.direccion = direccion      # las pruebas usan 127.0.0.1: no abre nada hacia fuera
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._lock = threading.Lock()

    @property
    def activo(self) -> bool:
        return self._httpd is not None

    def arrancar(self):
        if self._httpd:
            return
        httpd = ThreadingHTTPServer((self.direccion, self.puerto), _Manejador)
        httpd.daemon_threads = True
        httpd.dueno = self  # type: ignore[attr-defined]
        self._httpd = httpd
        self.puerto = httpd.server_address[1]       # con puerto 0 (pruebas), el que haya dado Windows
        threading.Thread(target=httpd.serve_forever, name="MASys-agente", daemon=True).start()

    def parar(self):
        httpd, self._httpd = self._httpd, None
        if httpd:
            httpd.shutdown()
            httpd.server_close()

    def recibir(self, huella: str, firma: str, contenido: bytes, ip: str) -> Tuple[int, dict]:
        with self._lock:     # un envío detrás de otro: la base de datos y el «un solo uso» lo agradecen
            codigo, cliente = self.tickets.comprobar(huella, firma, contenido)
            if codigo != 200:
                log.info("Envío del agente rechazado (%s) desde %s", codigo, ip)
                return codigo, {}
            try:
                informe = ag.descifrar(contenido, self.tickets.db.privadas())
            except ag.ErrorAgente as e:
                log.info("Envío del agente con archivo no válido desde %s: %s", ip, e)
                return 409, {}
            iid = self.tickets.db.guardar_informe(informe, cliente)
            if not self.tickets.marcar_usado(huella, iid):
                self.tickets.db.eliminar_informe(iid)
                return 410, {}
        if self.al_recibir:
            try:
                self.al_recibir(cliente, informe.get("equipo", ""), iid)
            except Exception:
                log.exception("Aviso de informe recibido")
        return 200, {"ok": True}


# ── Firewall de Windows (como el del móvil) ─────────────────────────────
def _sin_ventana():
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    return si


def validar_puerto(valor) -> int:
    """El puerto de recepción elegido por el técnico. ValueError con el motivo si no vale."""
    try:
        puerto = int(str(valor).strip())
    except (TypeError, ValueError):
        raise ValueError("El puerto tiene que ser un número")
    if not 1024 <= puerto <= 65535:
        raise ValueError("El puerto tiene que estar entre 1024 y 65535")
    if puerto in PUERTOS_OCUPADOS:
        raise ValueError(f"El puerto {puerto} ya lo usa {PUERTOS_OCUPADOS[puerto]}")
    return puerto


def nombre_regla(puerto: int = PUERTO) -> str:
    """Una regla de firewall por puerto. La del puerto de siempre conserva el nombre que tenía en 4.7.1."""
    return REGLA_FIREWALL if int(puerto) == PUERTO else f"{REGLA_FIREWALL} (TCP {int(puerto)})"


def regla_firewall_existe(puerto: int = PUERTO) -> bool:
    try:
        r = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={nombre_regla(puerto)}"],
                           capture_output=True, timeout=15, startupinfo=_sin_ventana(),
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def crear_regla_firewall(puerto: int = PUERTO) -> Tuple[bool, str]:
    """Solo redes privadas y de dominio: el router reenvía el puerto al PC, que está en la red privada."""
    try:
        r = subprocess.run(["netsh", "advfirewall", "firewall", "add", "rule", f"name={nombre_regla(puerto)}", "dir=in",
                            "action=allow", "protocol=TCP", f"localport={int(puerto)}", "profile=private,domain"],
                           capture_output=True, timeout=20, startupinfo=_sin_ventana(),
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode == 0:
            return True, ""
        return False, (r.stdout or b"").decode("cp850", errors="replace").strip() or "netsh devolvió un error"
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)


def ip_publica(timeout: float = 8) -> str:
    """IPv4 pública de este equipo (la misma consulta que Herramientas de red › Mi IP pública)."""
    import requests
    ip = requests.get("https://api.ipify.org", timeout=timeout).text.strip()
    if not re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", ip):
        raise ValueError("Respuesta inesperada al consultar la IP pública")
    return ip


def comprobar_direccion(direccion: str, resolver=None, publica=None) -> Tuple[bool, str]:
    """¿La dirección de los códigos (nombre de DNS dinámico o IP) lleva a la IP pública de este equipo?
    (bien, texto). resolver(nombre) -> lista de IPv4 y publica() -> IPv4 se inyectan en las pruebas."""
    try:
        h = _host_valido(direccion)
    except ValueError as e:
        return False, str(e)
    publica = publica or ip_publica
    try:
        mia = publica()
    except Exception as e:
        return False, f"No se pudo consultar tu IP pública: {e}"
    if re.fullmatch(r"\d{1,3}(\.\d{1,3}){3}", h):
        if h == mia:
            return True, ("✔ Es tu IP pública. Si tu proveedor la cambia, los códigos dejarán de servir: "
                          "mejor un nombre de DNS dinámico.")
        return False, f"⚠️ {h} no es tu IP pública actual ({mia})."
    if resolver is None:
        def resolver(nombre):
            return sorted({i[4][0] for i in socket.getaddrinfo(nombre, None, socket.AF_INET)})
    try:
        ips = list(resolver(h))
    except (OSError, UnicodeError) as e:
        return False, f"⚠️ «{h}» no existe en el DNS ({e}). Revisa el nombre o el DNS dinámico del router."
    if mia in ips:
        return True, f"✔ {h} apunta a tu IP pública ({mia})."
    return False, (f"⚠️ {h} apunta a {', '.join(ips) or 'ninguna IP'}, pero tu IP pública es {mia}: el DNS dinámico "
                   "no se ha actualizado (revisa el router o el proveedor).")


def texto_para_cliente(codigo: str, caduca: str) -> str:
    return ("Para que pueda revisar tu equipo:\n"
            "1. Descarga el programa que te envío (MASys_Agente) y ábrelo; pedirá permiso de administrador.\n"
            f"2. Pega este código donde lo pide (vale una sola vez, hasta el {caduca}):\n   {codigo}\n"
            "3. Pulsa «Analizar y enviar» y espera unos minutos: me llegará directamente.\n"
            "El programa solo lee: no cambia nada ni incluye contraseñas.")


def tiempo_legible(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return iso
