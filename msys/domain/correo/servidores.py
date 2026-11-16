# -*- coding: utf-8 -*-
"""Prueba de servidores IMAP, POP3 y SMTP (lo que hacía Test-MailConnection de la v4) y detección del proveedor.

Diferencias con la v4:
- El certificado se comprueba de verdad: si no es válido se sigue probando, pero se avisa («certificado no válido»).
- Las credenciales solo se mandan a los servidores del proveedor reconocido (dominio, MX, registros SRV o la base de datos
  de proveedores de Thunderbird). Probarlas contra toda la lista exige confirmarlo (la contraseña llega a terceros).
"""
import imaplib
import logging
import poplib
import smtplib
import socket
import ssl
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from msys.domain.correo import proveedores as prov

log = logging.getLogger("msys.correo.mailtest")
TIMEOUT = 8

# Textos de resultado (los mismos que la v4 para que se reconozcan)
OK = "OK"
OK_BANNER = "OK (sin probar usuario)"
CREDS = "Credenciales"
NA = "N/D"


@dataclass
class Result:
    status: str                 # OK, OK (sin probar usuario), Credenciales, Timeout, Sin conexión, Error SSL…
    detail: str = ""            # respuesta del servidor o error, para la ayuda
    cert_ok: bool | None = None  # None = no aplica (sin cifrado)

    @property
    def ok(self) -> bool:
        return self.status.startswith("OK")

    def text(self) -> str:
        return self.status + ("" if self.cert_ok in (None, True) else " · certificado no válido")


def _contexts():
    """Primero con verificación; si el certificado falla se repite sin ella para poder seguir probando."""
    good = ssl.create_default_context()
    bad = ssl.create_default_context()
    bad.check_hostname = False
    bad.verify_mode = ssl.CERT_NONE
    return good, bad


def _decode(x) -> str:
    if isinstance(x, (list, tuple)):
        x = b" ".join(i if isinstance(i, bytes) else str(i).encode() for i in x)
    if isinstance(x, bytes):
        return x.decode("utf-8", "replace")
    return str(x)


def _net_error(e: Exception) -> Result:
    if isinstance(e, (socket.timeout, TimeoutError)):
        return Result("Timeout", str(e))
    if isinstance(e, ssl.SSLError):
        return Result("Error SSL", str(e))
    if isinstance(e, socket.gaierror):
        return Result("Servidor desconocido", str(e))
    if isinstance(e, (ConnectionRefusedError, ConnectionResetError, OSError)):
        return Result("Sin conexión", str(e))
    return Result("Error", str(e))


def test_server(protocol: str, host: str, port: int, ssl_direct: bool, user: str = "", password: str = "",
                timeout: float = TIMEOUT) -> Result:
    """protocol: IMAP, POP3 o SMTP. ssl_direct: True = SSL/TLS desde el principio; False = STARTTLS si el servidor lo ofrece."""
    if not host or port <= 0:
        return Result(NA)
    good, bad = _contexts()
    try:
        return _try(protocol.upper(), host, port, ssl_direct, user, password, timeout, good)
    except ssl.SSLCertVerificationError as e:
        try:
            r = _try(protocol.upper(), host, port, ssl_direct, user, password, timeout, bad)
            r.cert_ok = False
            r.detail = (r.detail + " · " if r.detail else "") + f"Certificado: {e.verify_message or e}"
            return r
        except Exception as e2:  # noqa: BLE001 - se informa el error tal cual
            return _net_error(e2)
    except Exception as e:  # noqa: BLE001
        return _net_error(e)


def _try(proto, host, port, ssl_direct, user, password, timeout, ctx) -> Result:
    if proto == "IMAP":
        return _imap(host, port, ssl_direct, user, password, timeout, ctx)
    if proto in ("POP", "POP3"):
        return _pop(host, port, ssl_direct, user, password, timeout, ctx)
    if proto == "SMTP":
        return _smtp(host, port, ssl_direct, user, password, timeout, ctx)
    raise ValueError(proto)


def _imap(host, port, ssl_direct, user, password, timeout, ctx) -> Result:
    if ssl_direct:
        c = imaplib.IMAP4_SSL(host, port, ssl_context=ctx, timeout=timeout)
        cert = True
    else:
        c = imaplib.IMAP4(host, port, timeout=timeout)
        cert = None
        if "STARTTLS" in c.capabilities:
            c.starttls(ssl_context=ctx)
            cert = True
    try:
        banner = _decode(c.welcome)
        if not user:
            return Result(OK_BANNER, banner, cert)
        try:
            c.login(user, password)
            return Result(OK, banner, cert)
        except imaplib.IMAP4.error as e:
            return Result(CREDS, _decode(e.args[0] if e.args else e), cert)
    finally:
        try:
            c.logout()
        except Exception:  # noqa: BLE001 - cerrar sin más
            pass


def _pop(host, port, ssl_direct, user, password, timeout, ctx) -> Result:
    if ssl_direct:
        c = poplib.POP3_SSL(host, port, context=ctx, timeout=timeout)
        cert = True
    else:
        c = poplib.POP3(host, port, timeout=timeout)
        cert = None
        try:
            if "STLS" in c.capa():
                c.stls(context=ctx)
                cert = True
        except poplib.error_proto:
            pass
    try:
        banner = _decode(c.getwelcome())
        if not user:
            return Result(OK_BANNER, banner, cert)
        try:
            c.user(user)
        except poplib.error_proto as e:
            return Result("Usuario rechazado", _decode(e.args[0] if e.args else e), cert)
        try:
            c.pass_(password)
            return Result(OK, banner, cert)
        except poplib.error_proto as e:
            return Result(CREDS, _decode(e.args[0] if e.args else e), cert)
    finally:
        try:
            c.quit()
        except Exception:  # noqa: BLE001
            pass


def _smtp(host, port, ssl_direct, user, password, timeout, ctx) -> Result:
    if ssl_direct:
        c = smtplib.SMTP_SSL(host, port, timeout=timeout, context=ctx, local_hostname="masys.local")
        cert = True
    else:
        c = smtplib.SMTP(host, port, timeout=timeout, local_hostname="masys.local")
        cert = None
    try:
        code, msg = c.ehlo()
        if not ssl_direct and c.has_extn("starttls"):
            c.starttls(context=ctx)
            c.ehlo()
            cert = True
        banner = _decode(msg)
        if not user:
            return Result(OK_BANNER, banner, cert)
        if not c.has_extn("auth"):
            return Result("Sin AUTH", "El servidor no ofrece inicio de sesión en este puerto", cert)
        try:
            c.login(user, password)
            return Result(OK, banner, cert)
        except smtplib.SMTPAuthenticationError as e:
            return Result(CREDS, _decode(e.smtp_error), cert)
        except smtplib.SMTPNotSupportedError as e:
            return Result("Sin AUTH", str(e), cert)
    finally:
        try:
            c.quit()
        except Exception:  # noqa: BLE001
            pass


# =============================================================================== detección del proveedor
@dataclass
class Detection:
    domain: str
    names: list[str] = field(default_factory=list)       # proveedores conocidos reconocidos, por orden de confianza
    how: dict = field(default_factory=dict)               # nombre -> «por el dominio», «por el MX»…
    mx: list[str] = field(default_factory=list)
    extra: list[prov.Provider] = field(default_factory=list)  # configuraciones encontradas fuera de la lista (SRV, Thunderbird)


def mx_hosts(domain: str) -> list[str]:
    try:
        import dns.resolver
        ans = dns.resolver.resolve(domain, "MX", lifetime=6)
        return [str(r.exchange).rstrip(".") for r in sorted(ans, key=lambda r: r.preference)]
    except Exception as e:  # noqa: BLE001 - sin MX o sin red: se sigue con lo demás
        log.info("MX de %s: %s", domain, e)
        return []


def _srv(name: str):
    try:
        import dns.resolver
        ans = dns.resolver.resolve(name, "SRV", lifetime=6)
        best = sorted(ans, key=lambda r: (r.priority, -r.weight))[0]
        target = str(best.target).rstrip(".")
        return (target, int(best.port)) if target and target != "." else None
    except Exception:  # noqa: BLE001
        return None


def srv_config(domain: str) -> prov.Provider | None:
    """RFC 6186: _imaps._tcp / _imap._tcp, _pop3s._tcp, _submissions._tcp / _submission._tcp."""
    imaps, imap = _srv(f"_imaps._tcp.{domain}"), None
    if not imaps:
        imap = _srv(f"_imap._tcp.{domain}")
    pops = _srv(f"_pop3s._tcp.{domain}")
    subs, sub = _srv(f"_submissions._tcp.{domain}"), None
    if not subs:
        sub = _srv(f"_submission._tcp.{domain}")
    i, s = imaps or imap, subs or sub
    if not i and not s:
        return None
    return prov.Provider(f"{domain} (registros SRV)", i[0] if i else "", i[1] if i else 0, bool(imaps),
                         pops[0] if pops else "", pops[1] if pops else 0, bool(pops),
                         s[0] if s else "", s[1] if s else 0, bool(subs))


def ispdb_config(domain: str, timeout: float = 8) -> prov.Provider | None:
    """Base de datos pública de proveedores de Thunderbird (solo se envía el dominio)."""
    try:
        req = urllib.request.Request(f"https://autoconfig.thunderbird.net/v1.1/{domain}", headers={"User-Agent": "MASys"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            root = ET.fromstring(r.read())
    except Exception as e:  # noqa: BLE001 - no está en la base o sin red
        log.info("ISPDB %s: %s", domain, e)
        return None
    imap = pop = smtp = None
    for s in root.iter("incomingServer"):
        t = s.get("type")
        row = (s.findtext("hostname") or "", int(s.findtext("port") or 0), (s.findtext("socketType") or "") == "SSL")
        if t == "imap" and imap is None:
            imap = row
        elif t == "pop3" and pop is None:
            pop = row
    for s in root.iter("outgoingServer"):
        if s.get("type") == "smtp" and smtp is None:
            smtp = (s.findtext("hostname") or "", int(s.findtext("port") or 0), (s.findtext("socketType") or "") == "SSL")
    if not (imap or pop or smtp):
        return None
    z = ("", 0, False)
    i, p, s = imap or z, pop or z, smtp or z
    return prov.Provider(f"{domain} (Thunderbird)", *i, *p, *s)


def detect(email: str, use_ispdb: bool = True) -> Detection:
    domain = email.rsplit("@", 1)[-1].strip().lower()
    d = Detection(domain)
    for n in prov.by_domain(domain):
        d.names.append(n)
        d.how[n] = "por el dominio"
    d.mx = mx_hosts(domain)
    for n in prov.by_mx(d.mx):
        if n not in d.names:
            d.names.append(n)
            d.how[n] = "por su servidor de correo (MX)"
    if not d.names:
        s = srv_config(domain)
        if s:
            d.extra.append(s)
        if use_ispdb:
            t = ispdb_config(domain)
            if t:
                d.extra.append(t)
    return d


def valid_email(s: str) -> bool:
    import re
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", s.strip()))
