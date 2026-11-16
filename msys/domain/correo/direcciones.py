# -*- coding: utf-8 -*-
"""¿Existe esta dirección de correo? Por pasos y sin enviar nada:

1. Escrita bien (forma de la dirección y longitudes).
2. El dominio existe y recibe correo (MX; si no tiene, su dirección A; un «MX nulo» dice que no recibe).
3. Avisos: dirección temporal (desechable) o de grupo (info@, admin@…).
4. El buzón: se pregunta al servidor de correo del dominio (puerto 25) si aceptaría un correo para esa dirección
   (EHLO, MAIL FROM, RCPT TO y QUIT, sin DATA: no se envía nada). Si también acepta una dirección inventada, es un
   servidor que acepta todo y no se puede saber.

Resultado: existe / no existe / no se puede saber / no válida.
"""
import logging
import re
import secrets
import smtplib
import socket
import time
from dataclasses import dataclass, field

log = logging.getLogger("msys.correo.emailcheck")

EXISTS, MISSING, UNKNOWN, INVALID = "existe", "no existe", "no se puede saber", "no válida"
VERDICT_LEVEL = {EXISTS: "ok", MISSING: "bad", UNKNOWN: "warn", INVALID: "bad"}

# Servicios conocidos de direcciones temporales (lista corta de los más usados; se amplía en Ajustes)
DISPOSABLE = {
    "mailinator.com", "guerrillamail.com", "guerrillamail.net", "sharklasers.com", "10minutemail.com", "10minutemail.net",
    "yopmail.com", "yopmail.net", "temp-mail.org", "tempmail.com", "tempmail.net", "trashmail.com", "trashmail.de",
    "getnada.com", "nada.email", "maildrop.cc", "dispostable.com", "throwawaymail.com", "mohmal.com", "fakeinbox.com",
    "mintemail.com", "mailnesia.com", "emailondeck.com", "spamgourmet.com", "mytemp.email", "tempr.email", "discard.email",
    "moakt.com", "burnermail.io", "33mail.com", "inboxkitten.com", "mail.tm", "tmail.ws", "luxusmail.org",
}
ROLE = {"admin", "administrador", "info", "informacion", "contacto", "contact", "soporte", "support", "ventas", "sales",
        "webmaster", "postmaster", "hostmaster", "abuse", "noreply", "no-reply", "facturacion", "billing", "rrhh", "hr",
        "marketing", "office", "oficina", "hola", "hello", "team", "equipo", "help", "ayuda", "compras", "pedidos"}

_LOCAL = re.compile(r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+(\.[A-Za-z0-9!#$%&'*+/=?^_`{|}~-]+)*$")
_LABEL = re.compile(r"^(?!-)[A-Za-z0-9-]{1,63}(?<!-)$")
FIND = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z0-9-]{2,}")


@dataclass
class Step:
    name: str
    level: str          # ok / warn / bad / info / none
    text: str


@dataclass
class EmailResult:
    address: str
    verdict: str = UNKNOWN
    reason: str = ""
    steps: list[Step] = field(default_factory=list)
    mx: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)      # «temporal», «de grupo», «acepta todo»

    def to_dict(self) -> dict:
        return {"address": self.address, "verdict": self.verdict, "reason": self.reason, "mx": self.mx, "flags": self.flags,
                "steps": [s.__dict__ for s in self.steps]}

    @staticmethod
    def from_dict(d: dict) -> "EmailResult":
        r = EmailResult(d["address"], d.get("verdict", UNKNOWN), d.get("reason", ""), mx=d.get("mx", []), flags=d.get("flags", []))
        r.steps = [Step(**s) for s in d.get("steps", [])]
        return r


# ------------------------------------------------------------------ 1. forma
def syntax(addr: str) -> tuple[bool, str, str]:
    """(válida, motivo, dominio en ASCII)."""
    a = addr.strip()
    if a.count("@") != 1:
        return False, "Tiene que llevar una sola @", ""
    local, domain = a.rsplit("@", 1)
    if not local or not domain:
        return False, "Falta lo de antes o lo de después de la @", ""
    if len(a) > 254:
        return False, "Más de 254 caracteres", ""
    if len(local) > 64:
        return False, "Lo de antes de la @ pasa de 64 caracteres", ""
    if not _LOCAL.match(local):
        return False, "Lo de antes de la @ tiene caracteres no válidos o puntos seguidos", ""
    try:
        ascii_domain = domain.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError:
        return False, "El dominio no es válido", ""
    labels = ascii_domain.split(".")
    if len(labels) < 2 or not all(_LABEL.match(x) for x in labels) or labels[-1].isdigit():
        return False, "El dominio no es válido", ""
    return True, "", ascii_domain


# ------------------------------------------------------------------ 2. dominio
@dataclass
class DomainMail:
    exists: bool
    accepts: bool
    mx: list[str]
    note: str


def domain_mail(domain: str, lifetime: float = 6) -> DomainMail:
    import dns.exception
    import dns.resolver
    try:
        ans = dns.resolver.resolve(domain, "MX", lifetime=lifetime)
        hosts = [(r.preference, str(r.exchange).rstrip(".")) for r in ans]
        hosts.sort()
        names = [h for _p, h in hosts]
        if names in ([""], ["."]) or (len(names) == 1 and not names[0]):
            return DomainMail(True, False, [], "El dominio dice que no recibe correo (MX nulo)")
        return DomainMail(True, True, names, "")
    except dns.resolver.NXDOMAIN:
        return DomainMail(False, False, [], "El dominio no existe")
    except dns.resolver.NoAnswer:
        pass
    except dns.resolver.NoNameservers:
        return DomainMail(True, False, [], "Los servidores DNS del dominio no responden")
    except dns.exception.Timeout:
        return DomainMail(True, False, [], "El DNS no responde (sin conexión o muy lento)")
    # Sin MX: vale su dirección A/AAAA (MX implícito)
    for rr in ("A", "AAAA"):
        try:
            dns.resolver.resolve(domain, rr, lifetime=lifetime)
            return DomainMail(True, True, [domain], "Sin MX: se usaría la dirección del propio dominio")
        except Exception:  # noqa: BLE001 - se prueba la siguiente
            continue
    return DomainMail(True, False, [], "El dominio existe pero no tiene servidor de correo")


# ------------------------------------------------------------------ 4. buzón (SMTP)
@dataclass
class Probe:
    code: int           # 0 = sin respuesta
    text: str
    catch_all: bool | None = None
    host: str = ""


def smtp_probe(mx: list[str], addr: str, helo: str, mail_from: str = "", timeout: float = 12, check_catch_all: bool = True,
               port: int = 25) -> Probe:
    """RCPT TO en el primer MX que conteste. Nunca se envía DATA."""
    last = Probe(0, "Ningún servidor de correo contestó")
    for host in mx[:3]:
        try:
            s = smtplib.SMTP(timeout=timeout, local_hostname=helo or socket.getfqdn())
            s.connect(host, port)
        except (OSError, smtplib.SMTPException) as e:
            last = Probe(0, f"{host}: {e}", host=host)
            continue
        try:
            s.ehlo_or_helo_if_needed()
            if s.has_extn("starttls"):
                try:
                    import ssl
                    ctx = ssl.create_default_context()
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE     # aquí solo se pregunta por el buzón; el certificado lo mira «Dominios»
                    s.starttls(context=ctx)
                    s.ehlo()
                except (smtplib.SMTPException, OSError):
                    pass
            code, msg = s.mail(mail_from or "")
            if code >= 400:
                return Probe(code, f"MAIL FROM rechazado: {_txt(msg)}", host=host)
            code, msg = s.rcpt(addr)
            p = Probe(code, _txt(msg), host=host)
            if code < 300 and check_catch_all:
                fake = f"no-existe-{secrets.token_hex(6)}@{addr.rsplit('@', 1)[1]}"
                c2, _m2 = s.rcpt(fake)
                p.catch_all = c2 < 300
            return p
        except smtplib.SMTPServerDisconnected as e:
            last = Probe(0, f"{host} cortó la conexión: {e}", host=host)
        except (OSError, smtplib.SMTPException) as e:
            last = Probe(0, f"{host}: {e}", host=host)
        finally:
            try:
                s.quit()
            except Exception:  # noqa: BLE001
                pass
    return last


def _txt(m) -> str:
    return m.decode("utf-8", "replace") if isinstance(m, bytes) else str(m)


def port25_open(host: str, timeout: float = 6, port: int = 25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# ------------------------------------------------------------------ comprobación completa
class Checker:
    """Comprueba direcciones reutilizando lo ya averiguado por dominio (MX, acepta todo) en una misma lista."""

    def __init__(self, probe_mailbox: bool = True, helo: str = "", mail_from: str = "", timeout: float = 12,
                 extra_disposable: set | None = None, port: int = 25):
        self.probe_mailbox = probe_mailbox
        self.helo = helo
        self.mail_from = mail_from
        self.timeout = timeout
        self.disposable = DISPOSABLE | {d.lower() for d in (extra_disposable or set())}
        self._domains: dict[str, DomainMail] = {}
        self._catch_all: dict[str, bool] = {}
        self._port25: bool | None = None
        self.port = port

    def check(self, addr: str) -> EmailResult:
        r = EmailResult(addr.strip())
        ok, why, domain = syntax(addr)
        if not ok:
            r.verdict, r.reason = INVALID, why
            r.steps.append(Step("Forma", "bad", why))
            return r
        r.steps.append(Step("Forma", "ok", "Bien escrita"))
        dm = self._domains.get(domain)
        if dm is None:
            dm = self._domains[domain] = domain_mail(domain)
        r.mx = dm.mx
        if not dm.exists:
            r.verdict, r.reason = INVALID, dm.note
            r.steps.append(Step("Dominio", "bad", dm.note))
            return r
        if not dm.accepts:
            # «sin conexión» o «DNS sin respuesta» no permiten decidir; MX nulo o sin servidor, sí
            unsure = "no responde" in dm.note
            r.verdict, r.reason = (UNKNOWN if unsure else INVALID), dm.note
            r.steps.append(Step("Dominio", "warn" if unsure else "bad", dm.note))
            return r
        r.steps.append(Step("Dominio", "ok", "Recibe correo en " + ", ".join(dm.mx[:3]) + (f" · {dm.note}" if dm.note else "")))
        local = r.address.rsplit("@", 1)[0].lower()
        if domain in self.disposable:
            r.flags.append("temporal")
            r.steps.append(Step("Aviso", "warn", "Es de un servicio de direcciones temporales (desechables)"))
        if local in ROLE:
            r.flags.append("de grupo")
            r.steps.append(Step("Aviso", "info", "Es una dirección de grupo o de departamento, no de una persona"))
        if not self.probe_mailbox:
            r.verdict, r.reason = UNKNOWN, "No se ha preguntado al servidor por el buzón (desactivado)"
            r.steps.append(Step("Buzón", "none", r.reason))
            return r
        if self._port25 is False:
            r.verdict, r.reason = UNKNOWN, "Tu red no deja salir por el puerto 25: no se puede preguntar por el buzón"
            r.steps.append(Step("Buzón", "warn", r.reason))
            return r
        p = smtp_probe(dm.mx, r.address, self.helo, self.mail_from, self.timeout, check_catch_all=domain not in self._catch_all,
                       port=self.port)
        if p.code == 0:
            if self._port25 is None and dm.mx and not port25_open(dm.mx[0], 5, self.port):
                self._port25 = False
                r.reason = "Tu red no deja salir por el puerto 25 (lo bloquean muchos proveedores de Internet)"
            else:
                r.reason = f"El servidor de correo no contestó: {p.text}"
            r.verdict = UNKNOWN
            r.steps.append(Step("Buzón", "warn", r.reason))
            return r
        self._port25 = True
        if p.catch_all is not None:
            self._catch_all[domain] = p.catch_all
        catch_all = self._catch_all.get(domain, False)
        if 200 <= p.code < 300:
            if catch_all:
                r.flags.append("acepta todo")
                r.verdict = UNKNOWN
                r.reason = "El servidor acepta cualquier dirección del dominio (también una inventada)"
                r.steps.append(Step("Buzón", "warn", r.reason))
            else:
                r.verdict, r.reason = EXISTS, f"El servidor acepta la dirección ({p.code})"
                r.steps.append(Step("Buzón", "ok", f"{p.host} acepta la dirección: {p.code} {p.text[:120]}"))
        elif p.code in (550, 551, 553) or (p.code == 554 and "user" in p.text.lower()):
            r.verdict, r.reason = MISSING, f"El servidor dice que no existe ({p.code})"
            r.steps.append(Step("Buzón", "bad", f"{p.host}: {p.code} {p.text[:160]}"))
        elif 400 <= p.code < 500:
            r.verdict, r.reason = UNKNOWN, f"El servidor pide volver a intentarlo más tarde ({p.code}, lista gris o límite)"
            r.steps.append(Step("Buzón", "warn", f"{p.host}: {p.code} {p.text[:160]}"))
        else:
            r.verdict, r.reason = UNKNOWN, f"Respuesta no concluyente ({p.code})"
            r.steps.append(Step("Buzón", "warn", f"{p.host}: {p.code} {p.text[:160]} (puede ser un bloqueo a tu IP, no al buzón)"))
        return r


def extract(text: str) -> list[str]:
    """Direcciones de un texto pegado o de un CSV, sin repetir y en el orden en que aparecen."""
    seen, out = set(), []
    for m in FIND.finditer(text):
        a = m.group(0).strip(".")
        k = a.lower()
        if k not in seen:
            seen.add(k)
            out.append(a)
    return out


def read_list_file(path: str) -> list[str]:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            with open(path, encoding=enc) as f:
                return extract(f.read())
        except UnicodeDecodeError:
            continue
    return []


def write_csv(path: str, results: list[EmailResult]):
    import csv
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Correo", "Resultado", "Motivo", "Avisos", "Servidor de correo"])
        for r in results:
            w.writerow([r.address, r.verdict, r.reason, ", ".join(r.flags), ", ".join(r.mx[:2])])


def pause_between(last: dict, domain: str, seconds: float, cancelled=lambda: False):
    """Pausa entre preguntas al MISMO servidor (evita que bloqueen tu IP). last: dominio -> momento de la última."""
    t = last.get(domain)
    if t is not None:
        wait = seconds - (time.time() - t)
        while wait > 0 and not cancelled():
            time.sleep(min(0.2, wait))
            wait -= 0.2
    last[domain] = time.time()
