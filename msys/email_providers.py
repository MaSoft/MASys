# -*- coding: utf-8 -*-
"""Base de datos de proveedores de correo conocidos y comprobación real de
conectividad (IMAP/POP3/SMTP) para autodetectar la configuración de una
cuenta de correo dado usuario+contraseña.

Portado de GestCorreov4.ps1 (Get-EmailProviderConfigurations +
Test-MailConnection), con una corrección de seguridad importante respecto
al original: el script de origen deshabilitaba la validación del
certificado TLS (RemoteCertificateValidationCallback devolvía $true
siempre, aceptando cualquier certificado) y, si el STARTTLS de SMTP
fallaba, seguía enviando las credenciales igualmente sin cifrar. Aquí:
  - se valida el certificado con el almacén de confianza real del sistema
    (ssl.create_default_context, sin aceptar nada a ciegas);
  - si STARTTLS falla, se aborta esa prueba sin enviar la contraseña.
"""

import base64
import re
import socket
import ssl
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ProviderConfig:
    nombre: str
    imap_host: str = ""
    imap_port: int = 993
    imap_ssl: bool = True
    pop_host: str = ""
    pop_port: int = 995
    pop_ssl: bool = True
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_ssl: bool = False
    dominios: List[str] = field(default_factory=list)


PROVEEDORES: List[ProviderConfig] = [
    # ── Grandes proveedores internacionales ─────────────────────────────
    ProviderConfig("Gmail", "imap.gmail.com", 993, True, "pop.gmail.com", 995, True,
                   "smtp.gmail.com", 587, False, ["gmail.com", "googlemail.com"]),
    ProviderConfig("Outlook.com", "outlook.office365.com", 993, True, "outlook.office365.com", 995, True,
                   "smtp.office365.com", 587, False, ["outlook.com", "hotmail.com", "live.com", "msn.com"]),
    ProviderConfig("Yahoo", "imap.mail.yahoo.com", 993, True, "pop.mail.yahoo.com", 995, True,
                   "smtp.mail.yahoo.com", 465, True, ["yahoo.com", "yahoo.es", "ymail.com"]),
    ProviderConfig("iCloud", "imap.mail.me.com", 993, True, "", 0, False,
                   "smtp.mail.me.com", 587, False, ["icloud.com", "me.com", "mac.com"]),
    ProviderConfig("AOL", "imap.aol.com", 993, True, "pop.aol.com", 995, True,
                   "smtp.aol.com", 587, False, ["aol.com"]),
    ProviderConfig("GMX", "imap.gmx.com", 993, True, "pop.gmx.com", 995, True,
                   "mail.gmx.com", 465, True, ["gmx.com", "gmx.net", "gmx.de"]),
    ProviderConfig("Web.de", "imap.web.de", 993, True, "pop3.web.de", 995, True,
                   "smtp.web.de", 587, False, ["web.de"]),
    ProviderConfig("T-Online", "secureimap.t-online.de", 993, True, "securepop.t-online.de", 995, True,
                   "securesmtp.t-online.de", 465, True, ["t-online.de"]),
    ProviderConfig("Mail.ru", "imap.mail.ru", 993, True, "pop.mail.ru", 995, True,
                   "smtp.mail.ru", 465, True, ["mail.ru"]),
    ProviderConfig("Yandex", "imap.yandex.com", 993, True, "pop.yandex.com", 995, True,
                   "smtp.yandex.com", 465, True, ["yandex.com", "yandex.ru"]),
    ProviderConfig("Zoho", "imap.zoho.com", 993, True, "pop.zoho.com", 995, True,
                   "smtp.zoho.com", 465, True, ["zoho.com"]),
    ProviderConfig("Fastmail", "imap.fastmail.com", 993, True, "pop.fastmail.com", 995, True,
                   "smtp.fastmail.com", 465, True, ["fastmail.com"]),
    ProviderConfig("Posteo", "posteo.de", 993, True, "posteo.de", 995, True,
                   "posteo.de", 587, False, ["posteo.de"]),
    ProviderConfig("Mailbox.org", "imap.mailbox.org", 993, True, "pop3.mailbox.org", 995, True,
                   "smtp.mailbox.org", 465, True, ["mailbox.org"]),
    ProviderConfig("Tutanota", "mail.tutanota.com", 993, True, "", 0, False,
                   "mail.tutanota.com", 587, False, ["tutanota.com", "tutamail.com", "tuta.io"]),
    # ── ISPs españoles ───────────────────────────────────────────────────
    ProviderConfig("Movistar", "imap.movistar.es", 993, True, "pop3.movistar.es", 995, True,
                   "smtp.movistar.es", 465, True, ["movistar.es", "telefonica.net"]),
    ProviderConfig("Orange", "imap.orange.es", 993, True, "pop.orange.es", 995, True,
                   "smtp.orange.es", 587, False, ["orange.es"]),
    ProviderConfig("Jazztel", "imap.jazztel.es", 993, True, "pop.jazztel.es", 995, True,
                   "smtp.jazztel.es", 587, False, ["jazztel.es"]),
    ProviderConfig("Vodafone", "imap.vodafone.es", 993, True, "pop.vodafone.es", 995, True,
                   "smtp.vodafone.es", 587, False, ["vodafone.es", "vodafonemail.es"]),
    ProviderConfig("Masmovil", "imap.masmovil.es", 993, True, "pop.masmovil.es", 995, True,
                   "smtp.masmovil.es", 587, False, ["masmovil.es"]),
    # ── Hosting español ──────────────────────────────────────────────────
    ProviderConfig("Arsys", "imap.serviciodecorreo.es", 993, True, "pop.serviciodecorreo.es", 995, True,
                   "smtp.serviciodecorreo.es", 465, True, ["arsys.es", "arsys.net"]),
    ProviderConfig("Dinahosting", "mail.dinahosting.com", 993, True, "mail.dinahosting.com", 995, True,
                   "mail.dinahosting.com", 465, True, ["dinahosting.com"]),
    ProviderConfig("Ionos", "imap.ionos.es", 993, True, "pop.ionos.es", 995, True,
                   "smtp.ionos.es", 587, False, ["ionos.es", "1and1.es"]),
    ProviderConfig("Hostalia", "imap.hostalia.com", 993, True, "pop.hostalia.com", 995, True,
                   "smtp.hostalia.com", 587, False, ["hostalia.com"]),
    ProviderConfig("Acens", "imap.acens.net", 993, True, "pop.acens.net", 995, True,
                   "smtp.acens.net", 465, True, ["acens.com", "acens.net"]),
    ProviderConfig("CDmon", "imap.cdmon.com", 993, True, "pop.cdmon.com", 995, True,
                   "smtp.cdmon.com", 587, False, ["cdmon.com"]),
    ProviderConfig("DonDominio", "imap.dondominio.com", 993, True, "pop.dondominio.com", 995, True,
                   "smtp.dondominio.com", 587, False, ["dondominio.com"]),
    # ── Hosting internacional ────────────────────────────────────────────
    ProviderConfig("GoDaddy", "imap.secureserver.net", 993, True, "pop.secureserver.net", 995, True,
                   "smtpout.secureserver.net", 465, True, ["godaddy.com", "secureserver.net"]),
    ProviderConfig("One.com", "imap.one.com", 993, True, "pop.one.com", 995, True,
                   "send.one.com", 587, False, ["one.com"]),
    ProviderConfig("OVH", "ssl0.ovh.net", 993, True, "ssl0.ovh.net", 995, True,
                   "ssl0.ovh.net", 465, True, ["ovh.com", "ovh.net"]),
    ProviderConfig("Rackspace", "secure.emailsrvr.com", 993, True, "secure.emailsrvr.com", 995, True,
                   "secure.emailsrvr.com", 465, True, ["emailsrvr.com", "rackspace.com"]),
    ProviderConfig("Strato", "imap.strato.com", 993, True, "pop3.strato.com", 995, True,
                   "smtp.strato.com", 465, True, ["strato.com", "strato.de"]),
    ProviderConfig("Hostinger", "imap.hostinger.com", 993, True, "pop.hostinger.com", 995, True,
                   "smtp.hostinger.com", 465, True, ["hostinger.com"]),
    ProviderConfig("SiteGround", "imap.siteground.com", 993, True, "pop3.siteground.com", 995, True,
                   "smtp.siteground.com", 465, True, ["siteground.com"]),
    ProviderConfig("Bluehost", "imap.bluehost.com", 993, True, "pop3.bluehost.com", 995, True,
                   "smtp.bluehost.com", 465, True, ["bluehost.com"]),
    ProviderConfig("DreamHost", "imap.dreamhost.com", 993, True, "pop.dreamhost.com", 995, True,
                   "smtp.dreamhost.com", 465, True, ["dreamhost.com"]),
    ProviderConfig("Namecheap", "mail.privateemail.com", 993, True, "mail.privateemail.com", 995, True,
                   "mail.privateemail.com", 465, True, ["privateemail.com"]),
    ProviderConfig("Hetzner", "imap.your-server.de", 993, True, "pop3.your-server.de", 995, True,
                   "smtp.your-server.de", 587, False, ["your-server.de", "hetzner.com"]),
    ProviderConfig("Infomaniak", "mail.infomaniak.com", 993, True, "pop.infomaniak.com", 995, True,
                   "mail.infomaniak.com", 465, True, ["infomaniak.com"]),
]


class _SocketLineIO:
    """Envoltorio mínimo de socket con lectura por líneas y buffer propio,
    para poder re-envolver el socket en TLS a mitad de conversación
    (STARTTLS) sin perder bytes ya leídos por un BufferedReader/makefile."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self._buf = b""

    def readline(self, timeout: float) -> str:
        self.sock.settimeout(timeout)
        while b"\n" not in self._buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                break
            self._buf += chunk
        linea, _, self._buf = self._buf.partition(b"\n")
        return linea.rstrip(b"\r").decode("utf-8", errors="replace")

    def writeline(self, texto: str) -> None:
        self.sock.sendall(texto.encode("utf-8") + b"\r\n")


def test_mail_connection(server: str, port: int, protocol: str, use_ssl: bool,
                          username: str, password: str, timeout: float = 6.0) -> str:
    """Prueba real de conectividad + autenticación contra un servidor IMAP,
    POP3 o SMTP. Devuelve un texto de estado corto ("OK", "Credenciales",
    "Timeout", "Error SSL", "N/D", ...), igual que el script original."""
    if not server or port <= 0:
        return "N/D"

    protocol = protocol.upper()
    try:
        raw = socket.create_connection((server, port), timeout=timeout)
    except socket.timeout:
        return "Timeout"
    except OSError:
        return "Sin conexión"

    ctx = ssl.create_default_context()
    try:
        if use_ssl:
            try:
                raw = ctx.wrap_socket(raw, server_hostname=server)
            except (ssl.SSLError, OSError):
                return "Error SSL"

        io = _SocketLineIO(raw)

        try:
            banner = io.readline(timeout)
        except (socket.timeout, OSError):
            return "Sin respuesta"

        if protocol == "IMAP":
            return _probar_imap(io, banner, username, password, timeout)
        if protocol == "POP3":
            return _probar_pop3(io, banner, username, password, timeout)
        if protocol == "SMTP":
            return _probar_smtp(io, raw, banner, server, use_ssl, username, password, timeout, ctx)
        return "Protocolo desconocido"
    except Exception:
        return "Error"
    finally:
        try:
            raw.close()
        except Exception:
            pass


def _probar_imap(io: _SocketLineIO, banner: str, username: str, password: str, timeout: float) -> str:
    if not banner.startswith("* OK"):
        return "No IMAP"
    if not username:
        return "OK (banner)"
    # Cadena entre comillas de IMAP (RFC 3501): «\» y «"» van escapados; sin esto una contraseña con comillas
    # rompía el LOGIN y salía como error aunque fuera correcta.
    def _q(s: str) -> str:
        return s.replace("\\", "\\\\").replace('"', '\\"')
    io.writeline(f'A1 LOGIN "{_q(username)}" "{_q(password)}"')
    resp = ""
    try:
        while not (resp.startswith("A1 OK") or resp.startswith("A1 NO") or resp.startswith("A1 BAD")):
            resp = io.readline(timeout)
    except (socket.timeout, OSError):
        return "Timeout auth"
    try:
        io.writeline("A2 LOGOUT")
    except OSError:
        pass
    if resp.startswith("A1 OK"):
        return "OK"
    if resp.startswith("A1 NO"):
        return "Credenciales"
    return "Error IMAP"


def _probar_pop3(io: _SocketLineIO, banner: str, username: str, password: str, timeout: float) -> str:
    if not banner.startswith("+OK"):
        return "No POP3"
    if not username:
        return "OK (banner)"
    io.writeline(f"USER {username}")
    try:
        r1 = io.readline(timeout)
    except (socket.timeout, OSError):
        return "Timeout"
    if not r1.startswith("+OK"):
        return "USER rechazado"
    io.writeline(f"PASS {password}")
    try:
        r2 = io.readline(timeout)
    except (socket.timeout, OSError):
        return "Timeout"
    try:
        io.writeline("QUIT")
    except OSError:
        pass
    return "OK" if r2.startswith("+OK") else "Credenciales"


def _probar_smtp(io: _SocketLineIO, raw, banner: str, server: str, use_ssl: bool,
                  username: str, password: str, timeout: float, ctx: ssl.SSLContext) -> str:
    if not banner.startswith("220"):
        return "No SMTP"

    io.writeline("EHLO masys")
    try:
        linea = io.readline(timeout)
        while linea.startswith("250-"):
            linea = io.readline(timeout)
    except (socket.timeout, OSError):
        return "Timeout EHLO"

    if not use_ssl:
        io.writeline("STARTTLS")
        try:
            tls_r = io.readline(timeout)
        except (socket.timeout, OSError):
            tls_r = ""
        if not tls_r.startswith("220"):
            # A diferencia del script original: si STARTTLS falla, se
            # aborta aquí. NO se sigue para no enviar credenciales en claro.
            return "STARTTLS fallido"
        try:
            raw2 = ctx.wrap_socket(raw, server_hostname=server)
        except (ssl.SSLError, OSError):
            return "Error SSL"
        io.sock = raw2
        io._buf = b""
        io.writeline("EHLO masys")
        linea = io.readline(timeout)
        while linea.startswith("250-"):
            linea = io.readline(timeout)

    if not username:
        return "OK (banner)"

    io.writeline("AUTH LOGIN")
    try:
        r1 = io.readline(timeout)
    except (socket.timeout, OSError):
        return "Timeout auth"
    if r1.startswith("334"):
        io.writeline(base64.b64encode(username.encode("utf-8")).decode("ascii"))
        try:
            io.readline(timeout)
        except (socket.timeout, OSError):
            return "Timeout"
        io.writeline(base64.b64encode(password.encode("utf-8")).decode("ascii"))
        try:
            r3 = io.readline(timeout)
        except (socket.timeout, OSError):
            return "Timeout"
        try:
            io.writeline("QUIT")
        except OSError:
            pass
        return "OK" if r3.startswith("235") else "Credenciales"

    plano = base64.b64encode(f"\0{username}\0{password}".encode("utf-8")).decode("ascii")
    io.writeline(f"AUTH PLAIN {plano}")
    try:
        rp = io.readline(timeout)
    except (socket.timeout, OSError):
        return "Timeout"
    try:
        io.writeline("QUIT")
    except OSError:
        pass
    return "OK" if rp.startswith("235") else "Sin AUTH"


def proveedores_prioritarios(email: str) -> List[ProviderConfig]:
    """Proveedores cuyo dominio coincide con el de la cuenta — se prueban
    primero porque es lo más probable que acierten a la primera."""
    dominio = email.rsplit("@", 1)[-1].lower() if "@" in email else ""
    if not dominio:
        return []
    # Dominio exacto o subdominio («mail.movistar.es»), nunca subcadena: con «in», «acme.com» contenía «me.com» y se
    # tomaba por iCloud (y «microphone.com» por One.com): la contraseña iba a un proveedor que no era el de la cuenta.
    return [p for p in PROVEEDORES if any(dominio == d or dominio.endswith("." + d) for d in p.dominios)]
