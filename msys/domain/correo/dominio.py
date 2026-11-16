# -*- coding: utf-8 -*-
"""Salud del correo de un dominio (lo que mira MXToolbox): DNS, MX, SPF, DMARC, DKIM, MTA-STS, TLS-RPT, BIMI, inverso (PTR),
servidor SMTP (saludo, STARTTLS y certificado) y listas negras. Cada comprobación da un nivel (ok / warn / bad / info), un
resumen, los registros encontrados y qué hacer para arreglarlo.

Solo se leen registros públicos del DNS y se saluda al servidor de correo (sin enviar nada). No se prueba el relay abierto.
"""
import concurrent.futures as cf
import datetime as _dt
import logging
import re
import socket
import ssl
import urllib.request
from dataclasses import dataclass, field

log = logging.getLogger("msys.correo.domaincheck")
LIFETIME = 6

# Selectores DKIM habituales (proveedor que los usa); se puede añadir uno a mano
DKIM_SELECTORS = [
    ("selector1", "Microsoft 365"), ("selector2", "Microsoft 365"), ("google", "Google Workspace"), ("default", "cPanel y otros"),
    ("dkim", "varios"), ("mail", "varios"), ("k1", "Mailchimp"), ("k2", "Mailchimp"), ("k3", "Mailchimp"), ("s1", "SendGrid"),
    ("s2", "SendGrid"), ("smtp", "varios"), ("mandrill", "Mandrill"), ("zoho", "Zoho"), ("zmail", "Zoho"),
    ("protonmail", "Proton"), ("protonmail2", "Proton"), ("protonmail3", "Proton"), ("sig1", "iCloud"), ("mxvault", "MXroute"),
    ("x", "varios"), ("key1", "varios"), ("dk", "varios"), ("ionos", "IONOS"), ("ovh", "OVH"), ("everlytickey1", "Everlytic"),
    ("cm", "Campaign Monitor"), ("pm", "Postmark"), ("mailjet", "Mailjet"), ("mesmtp", "Mailersend"),
]
# Listas negras de IP y de dominios (gratuitas). Spamhaus no responde a resolvedores públicos (devuelve 127.255.255.x).
IP_LISTS = [("zen.spamhaus.org", "Spamhaus ZEN"), ("bl.spamcop.net", "SpamCop"), ("b.barracudacentral.org", "Barracuda"),
            ("psbl.surriel.com", "PSBL"), ("bl.mailspike.net", "Mailspike"), ("dnsbl-1.uceprotect.net", "UCEPROTECT nivel 1")]
DOMAIN_LISTS = [("dbl.spamhaus.org", "Spamhaus DBL"), ("multi.surbl.org", "SURBL")]


@dataclass
class Check:
    key: str
    name: str
    level: str = "info"         # ok / warn / bad / info
    summary: str = ""
    records: list[str] = field(default_factory=list)
    advice: str = ""
    items: list[tuple[str, str]] = field(default_factory=list)   # (nivel, texto) detalles con su propio color

    def to_dict(self):
        return dict(self.__dict__)

    @staticmethod
    def from_dict(d):
        c = Check(d["key"], d["name"])
        c.__dict__.update(d)
        c.items = [tuple(x) for x in d.get("items", [])]
        return c


@dataclass
class Report:
    domain: str
    when: str = ""
    checks: list[Check] = field(default_factory=list)

    def counts(self) -> dict:
        out = {"ok": 0, "warn": 0, "bad": 0, "info": 0}
        for c in self.checks:
            out[c.level] = out.get(c.level, 0) + 1
        return out

    def to_dict(self):
        return {"domain": self.domain, "when": self.when, "checks": [c.to_dict() for c in self.checks]}

    @staticmethod
    def from_dict(d):
        return Report(d["domain"], d.get("when", ""), [Check.from_dict(c) for c in d.get("checks", [])])


# ============================================================================ DNS
def _res():
    import dns.resolver
    r = dns.resolver.Resolver()
    r.lifetime = LIFETIME
    return r


def query(name: str, rtype: str) -> list:
    """Lista de respuestas (vacía si no hay). Lanza LookupError('nx') si el nombre no existe."""
    import dns.exception
    import dns.resolver
    try:
        return list(_res().resolve(name, rtype))
    except dns.resolver.NXDOMAIN as e:
        raise LookupError("nx") from e
    except (dns.resolver.NoAnswer, dns.resolver.NoNameservers):
        return []
    except dns.exception.Timeout as e:
        raise TimeoutError(f"El DNS no responde a {name} {rtype}") from e


def txt(name: str) -> list[str]:
    try:
        return [b"".join(r.strings).decode("utf-8", "replace") for r in query(name, "TXT")]
    except LookupError:
        return []


def ips_of(host: str) -> list[str]:
    out = []
    for rt in ("A", "AAAA"):
        try:
            out += [r.to_text() for r in query(host, rt)]
        except (LookupError, TimeoutError):
            pass
    return out


def normalize_domain(s: str) -> str:
    s = s.strip().lower()
    s = s.rsplit("@", 1)[-1]
    s = re.sub(r"^[a-z]+://", "", s).split("/")[0].strip(".")
    try:
        return s.encode("idna").decode("ascii")
    except UnicodeError:
        return s


# ============================================================================ comprobaciones
def check_dns(domain: str) -> Check:
    c = Check("dns", "Dominio y DNS")
    try:
        ns = [str(r.target).rstrip(".") for r in query(domain, "NS")]
    except LookupError:
        c.level, c.summary = "bad", "El dominio no existe"
        c.advice = "Revisa que está bien escrito y que el dominio está registrado y en vigor."
        return c
    c.records = [f"NS {x}" for x in ns]
    if len(ns) >= 2:
        c.level, c.summary = "ok", f"{len(ns)} servidores DNS"
    elif ns:
        c.level, c.summary = "warn", "Solo un servidor DNS"
        c.advice = "Lo recomendable son al menos dos servidores DNS, a ser posible en redes distintas."
    else:
        c.level, c.summary = "warn", "No se ven sus servidores DNS (puede ser un subdominio)"
    try:
        import dns.resolver
        soa = list(dns.resolver.resolve(domain, "SOA", lifetime=LIFETIME))
        if soa:
            c.records.append(f"SOA {soa[0].mname} serie {soa[0].serial}")
    except Exception:  # noqa: BLE001 - el SOA es informativo
        pass
    return c


def check_mx(domain: str) -> tuple[Check, list[tuple[int, str]]]:
    c = Check("mx", "Servidores de correo (MX)")
    try:
        mx = sorted((r.preference, str(r.exchange).rstrip(".")) for r in query(domain, "MX"))
    except LookupError:
        c.level, c.summary = "bad", "El dominio no existe"
        return c, []
    if len(mx) == 1 and mx[0][1] in ("", "."):
        c.level, c.summary = "info", "El dominio dice que no recibe correo (MX nulo)"
        c.records = ["MX 0 ."]
        return c, []
    if not mx:
        a = ips_of(domain)
        c.level = "warn" if a else "bad"
        c.summary = "Sin MX: el correo iría a la dirección del dominio" if a else "No recibe correo (sin MX ni dirección)"
        c.advice = "Si el dominio recibe correo, crea sus registros MX con los servidores de tu proveedor."
        return c, ([(0, domain)] if a else [])
    for pref, host in mx:
        ips = ips_of(host)
        c.records.append(f"MX {pref} {host}  →  {', '.join(ips) if ips else 'sin dirección'}")
        if not ips:
            c.items.append(("bad", f"{host} no tiene dirección IP"))
        if re.fullmatch(r"[\d.]+", host):
            c.items.append(("bad", f"{host} es una IP: el MX tiene que ser un nombre"))
    bad = [x for x in c.items if x[0] == "bad"]
    if bad:
        c.level, c.summary = ("bad" if len(bad) == len(mx) else "warn"), f"{len(mx)} MX, {len(bad)} con problemas"
        c.advice = "Cada MX debe ser un nombre que resuelva a una IP. Corrige o quita los que fallan."
    else:
        c.level, c.summary = "ok", f"{len(mx)} servidor(es): " + ", ".join(h for _p, h in mx[:3])
        if len(mx) == 1:
            c.items.append(("info", "Un solo MX: si cae, el correo se reintenta desde el que lo envía (lo normal en servicios en la nube)"))
    return c, mx


_SPF_LOOKUP = re.compile(r"^[+~?-]?(include|a|mx|ptr|exists|redirect)\b", re.I)


def spf_lookups(record: str, seen: set | None = None, depth: int = 0) -> tuple[int, list[str]]:
    """Consultas DNS que gasta un SPF (máximo 10, RFC 7208), siguiendo include y redirect. Devuelve (n, avisos)."""
    seen = seen or set()
    n, warn = 0, []
    for term in record.split()[1:]:
        t = term.lower()
        if not _SPF_LOOKUP.match(t):
            continue
        n += 1
        if t.lstrip("+~?-").startswith(("include:", "redirect=")):
            target = t.split(":", 1)[1] if ":" in t else t.split("=", 1)[1]
            if target in seen or depth > 10:
                warn.append(f"bucle o demasiada profundidad en {target}")
                continue
            seen.add(target)
            sub = [x for x in txt(target) if x.lower().startswith("v=spf1")]
            if not sub:
                warn.append(f"{target} no tiene SPF (el include falla)")
                continue
            m, w = spf_lookups(sub[0], seen, depth + 1)
            n += m
            warn += w
    return n, warn


def check_spf(domain: str) -> Check:
    c = Check("spf", "SPF (quién puede enviar)")
    recs = [r for r in txt(domain) if r.lower().startswith("v=spf1")]
    c.records = recs
    if not recs:
        c.level, c.summary = "bad", "No tiene SPF"
        c.advice = ("Añade un registro TXT en el dominio con los servidores que envían su correo, por ejemplo "
                    "«v=spf1 include:<el de tu proveedor> -all». Sin SPF, Gmail y Outlook lo tratan peor.")
        return c
    if len(recs) > 1:
        c.level, c.summary = "bad", f"Hay {len(recs)} registros SPF: no vale ninguno"
        c.advice = "Solo puede haber un SPF. Junta todos los include en un único registro."
        return c
    r = recs[0]
    terms = r.split()
    all_t = next((t for t in terms if t.lower().lstrip("+~?-") == "all"), None)
    # Sin «all» pero con redirect=: manda el del registro al que redirige (p. ej. gmail.com → _spf.google.com)
    seen, cur = {domain}, terms
    while all_t is None:
        red = next((t.split("=", 1)[1].lower() for t in cur if t.lower().startswith("redirect=")), None)
        if not red or red in seen:
            break
        seen.add(red)
        sub = [x for x in txt(red) if x.lower().startswith("v=spf1")]
        if not sub:
            break
        cur = sub[0].split()
        all_t = next((t for t in cur if t.lower().lstrip("+~?-") == "all"), None)
        if all_t:
            c.items.append(("info", f"El «{all_t}» viene de {red} (redirect)"))
    q = (all_t[0] if all_t and all_t[0] in "+~?-" else "+") if all_t else None
    n, w = spf_lookups(r)
    for x in w:
        c.items.append(("warn", x))
    if any(t.lower().lstrip("+~?-").startswith("ptr") for t in terms):
        c.items.append(("warn", "Usa «ptr», que está desaconsejado (lento y poco fiable)"))
    c.items.append(("ok" if n <= 10 else "bad", f"Gasta {n} consultas DNS de 10 permitidas"))
    if n > 10:
        c.level, c.summary = "bad", f"Pasa de 10 consultas DNS ({n}): da error y no vale"
        c.advice = "Quita include que no uses o cambia algunos por las IP (ip4:/ip6:) para bajar de 10."
    elif q == "+":
        c.level, c.summary = "bad", "Termina en «+all»: deja enviar a cualquiera"
        c.advice = "Cámbialo a «-all» (o «~all» mientras pruebas)."
    elif q == "?" or q is None:
        c.level, c.summary = "warn", "No dice qué hacer con el resto" if q is None else "Termina en «?all» (neutro): no protege"
        c.advice = "Termínalo en «-all» o «~all»."
    elif q == "~":
        c.level, c.summary = "ok", "Correcto (termina en «~all»: lo demás, como sospechoso)"
    else:
        c.level, c.summary = "ok", "Correcto (termina en «-all»: lo demás, rechazar)"
    if w and c.level == "ok":
        c.level = "warn"
    return c


def _tags(rec: str) -> dict:
    out = {}
    for part in rec.split(";"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip().lower()] = v.strip()
    return out


def check_dmarc(domain: str) -> Check:
    c = Check("dmarc", "DMARC (qué hacer con el correo falso)")
    recs = [r for r in txt(f"_dmarc.{domain}") if r.lower().replace(" ", "").startswith("v=dmarc1")]
    c.records = recs
    if not recs:
        c.level, c.summary = "bad", "No tiene DMARC"
        c.advice = ("Crea un TXT en _dmarc." + domain + " empezando por «v=DMARC1; p=none; rua=mailto:<un buzón tuyo>» para ver los "
                    "informes y, cuando todo pase, sube a p=quarantine y luego a p=reject. Gmail y Yahoo lo exigen a quien envía mucho.")
        return c
    if len(recs) > 1:
        c.level, c.summary = "bad", "Hay más de un DMARC: no vale ninguno"
        c.advice = "Deja un solo registro DMARC."
        return c
    t = _tags(recs[0])
    p = t.get("p", "").lower()
    if p == "reject":
        c.level, c.summary = "ok", "Política «reject»: el correo falso se rechaza"
    elif p == "quarantine":
        c.level, c.summary = "ok", "Política «quarantine»: el correo falso va a correo no deseado"
    elif p == "none":
        c.level, c.summary = "warn", "Política «none»: solo vigila, no protege"
        c.advice = "Cuando los informes muestren que tu correo legítimo pasa, sube a p=quarantine y después a p=reject."
    else:
        c.level, c.summary = "bad", "Falta la política (p=) o no es válida"
        c.advice = "Añade p=none, p=quarantine o p=reject."
    if "rua" in t:
        c.items.append(("ok", f"Informes a {t['rua']}"))
    else:
        c.items.append(("info", "Sin rua=: no recibes informes de quién envía en tu nombre"))
    if t.get("pct") and t["pct"] != "100":
        c.items.append(("info", f"Solo se aplica al {t['pct']} % del correo"))
    if t.get("sp"):
        c.items.append(("info", f"Subdominios: {t['sp']}"))
    return c


def dkim_key_bits(rec: str) -> int | None:
    m = re.search(r"\bp=([A-Za-z0-9+/=\s]*)", rec)
    if not m:
        return None
    b64 = re.sub(r"\s", "", m.group(1))
    if not b64:
        return 0
    try:
        import base64
        der = base64.b64decode(b64 + "=" * (-len(b64) % 4))
    except ValueError:
        return None
    try:
        from cryptography.hazmat.primitives.serialization import load_der_public_key
        k = load_der_public_key(der)
        return getattr(k, "key_size", None)
    except Exception:  # noqa: BLE001 - sin cryptography o clave ed25519: se estima por el tamaño
        return (len(der) - 38) * 8 if len(der) > 100 else len(der) * 8


def check_dkim(domain: str, extra: list[str] | None = None) -> Check:
    c = Check("dkim", "DKIM (firma de los correos)")
    sels = [(s, "a mano") for s in (extra or []) if s] + DKIM_SELECTORS
    found = []

    def one(sel):
        name = f"{sel}._domainkey.{domain}"
        try:
            recs = txt(name)
            if not recs:
                cn = query(name, "CNAME")
                if cn:
                    recs = txt(str(cn[0].target).rstrip("."))
        except (LookupError, TimeoutError):
            recs = []
        return [r for r in recs if "p=" in r or "v=dkim1" in r.lower()]
    # Comodín (*._domainkey): un selector inventado también responde; se informa una vez y no por cada selector
    import secrets
    wild = one(f"comprueba-{secrets.token_hex(4)}")
    if wild:
        bits = dkim_key_bits(wild[0])
        c.records.append(f"*._domainkey  {wild[0][:120]}")
        if bits == 0:
            c.level, c.summary = "info", "Claves anuladas para todo el dominio (comodín): no firma correo"
            c.items.append(("info", "Es lo correcto si el dominio no envía correo; si envía, publica la clave de su selector"))
        else:
            c.level, c.summary = "warn", "Responde cualquier selector (comodín con clave)"
            c.advice = "Un comodín con clave no es lo normal: publica cada selector por separado."
        return c
    with cf.ThreadPoolExecutor(8) as ex:
        res = dict(zip([s for s, _w in sels], ex.map(one, [s for s, _w in sels])))
    for sel, who in sels:
        for r in res.get(sel, []):
            bits = dkim_key_bits(r)
            found.append((sel, who, bits))
            c.records.append(f"{sel}._domainkey  {r[:120]}{'…' if len(r) > 120 else ''}")
    if not found:
        c.level, c.summary = "info", "No se encontró con los selectores habituales"
        c.advice = ("No quiere decir que no tenga: el selector lo elige quien envía. Míralo en la cabecera «DKIM-Signature: … s=…» "
                    "de un correo del dominio y escríbelo en «Selector DKIM».")
        return c
    revoked = [f for f in found if f[2] == 0]
    weak = [f for f in found if f[2] and f[2] < 1024]
    for sel, who, bits in found:
        lvl = "bad" if bits == 0 else "warn" if bits and bits < 1024 else "ok"
        c.items.append((lvl, f"{sel} ({who}): " + ("clave revocada (p= vacío)" if bits == 0 else f"clave de {bits} bits" if bits else "clave encontrada")))
    if weak:
        c.level, c.summary = "warn", "Hay claves de menos de 1024 bits"
        c.advice = "Genera una clave nueva de 2048 bits en tu proveedor de correo."
    elif len(revoked) == len(found):
        c.level, c.summary = "warn", "Solo hay claves revocadas"
    else:
        c.level, c.summary = "ok", f"{len(found) - len(revoked)} selector(es) con clave: " + ", ".join(s for s, _w, b in found if b != 0)
    return c


def check_mta_sts(domain: str) -> Check:
    c = Check("mta_sts", "MTA-STS (exigir cifrado al recibir)")
    recs = [r for r in txt(f"_mta-sts.{domain}") if r.lower().startswith("v=stsv1")]
    c.records = recs
    if not recs:
        c.level, c.summary = "info", "No tiene MTA-STS (opcional)"
        c.advice = "Opcional: obliga a los demás servidores a mandarte el correo cifrado. Lo ofrecen Microsoft 365 y Google."
        return c
    try:
        req = urllib.request.Request(f"https://mta-sts.{domain}/.well-known/mta-sts.txt", headers={"User-Agent": "MASys"})
        with urllib.request.urlopen(req, timeout=8) as r:
            policy = r.read(4000).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        c.level, c.summary = "bad", "Tiene el registro pero no se puede leer la política"
        c.items.append(("bad", f"https://mta-sts.{domain}/.well-known/mta-sts.txt: {e}"))
        c.advice = "Publica la política en esa dirección con un certificado válido, o quita el registro _mta-sts."
        return c
    c.records += [x.strip() for x in policy.splitlines() if x.strip()]
    mode = (re.search(r"^mode:\s*(\w+)", policy, re.M | re.I) or [None, ""])[1].lower()
    if mode == "enforce":
        c.level, c.summary = "ok", "Activo y exigido (enforce)"
    elif mode == "testing":
        c.level, c.summary = "info", "En pruebas (testing): avisa pero no exige"
    else:
        c.level, c.summary = "warn", f"Política en modo «{mode or 'desconocido'}»"
    return c


def check_tlsrpt(domain: str) -> Check:
    c = Check("tlsrpt", "TLS-RPT (informes de cifrado)")
    recs = [r for r in txt(f"_smtp._tls.{domain}") if r.lower().startswith("v=tlsrptv1")]
    c.records = recs
    if recs:
        c.level, c.summary = "ok", "Recibe informes de fallos de cifrado"
    else:
        c.level, c.summary = "info", "Sin TLS-RPT (opcional)"
        c.advice = "Opcional: TXT en _smtp._tls." + domain + " con «v=TLSRPTv1; rua=mailto:<buzón>» para saber si falla el cifrado."
    return c


def check_bimi(domain: str) -> Check:
    c = Check("bimi", "BIMI (logotipo en el correo)")
    recs = [r for r in txt(f"default._bimi.{domain}") if r.lower().startswith("v=bimi1")]
    c.records = recs
    if not recs:
        c.level, c.summary = "info", "Sin BIMI (opcional)"
        c.advice = "Opcional: muestra el logotipo junto a tus correos. Necesita DMARC en quarantine o reject y, en Gmail, certificado VMC o CMC."
        return c
    t = _tags(recs[0])
    c.level, c.summary = "ok", "Tiene BIMI" + (" con certificado" if t.get("a") else " (sin certificado: Gmail no lo muestra)")
    if t.get("l"):
        c.items.append(("info", f"Logotipo: {t['l']}"))
    return c


def check_ptr(mx: list[tuple[int, str]]) -> Check:
    c = Check("ptr", "Inverso de los servidores (PTR)")
    if not mx:
        c.level, c.summary = "info", "Sin servidores de correo que comprobar"
        return c
    import dns.reversename
    bad = 0
    total = 0
    for _p, host in mx[:4]:
        for ip in ips_of(host)[:2]:
            total += 1
            try:
                names = [str(r.target).rstrip(".") for r in query(dns.reversename.from_address(ip).to_text(), "PTR")]
            except (LookupError, TimeoutError):
                names = []
            if not names:
                bad += 1
                c.items.append(("bad", f"{ip} ({host}) no tiene nombre inverso"))
                continue
            back = ips_of(names[0])
            ok = ip in back
            c.items.append(("ok" if ok else "warn", f"{ip} → {names[0]}" + ("" if ok else " (ese nombre no vuelve a esta IP)")))
            c.records.append(f"PTR {ip} {names[0]}")
            if not ok:
                bad += 0.5
    if not total:
        c.level, c.summary = "warn", "Los MX no tienen IP"
    elif bad == 0:
        c.level, c.summary = "ok", "Todos tienen nombre inverso que coincide"
    else:
        c.level, c.summary = "warn", "Algún servidor sin inverso correcto"
        c.advice = "El PTR lo pone quien da la IP (tu proveedor o tu operador). Importa sobre todo en los servidores que envían."
    return c


def check_smtp(mx: list[tuple[int, str]], timeout: float = 10) -> Check:
    c = Check("smtp", "Servidor de correo (SMTP)")
    if not mx:
        c.level, c.summary = "info", "Sin servidores de correo que comprobar"
        return c
    import smtplib
    host = mx[0][1]
    try:
        s = smtplib.SMTP(timeout=timeout, local_hostname=socket.getfqdn())
        code, banner = s.connect(host, 25)
    except (OSError, smtplib.SMTPException) as e:
        c.level, c.summary = "info", "No se puede comprobar desde tu red (puerto 25 cerrado o sin respuesta)"
        c.items.append(("info", f"{host}:25 → {e}"))
        c.advice = "Muchos operadores bloquean el puerto 25 de salida. Pruébalo desde un servidor o desde otra red."
        return c
    try:
        c.records.append(f"{host}: {code} {banner.decode('utf-8', 'replace')[:160]}")
        s.ehlo()
        if not s.has_extn("starttls"):
            c.level, c.summary = "bad", "No ofrece cifrado (STARTTLS)"
            c.advice = "Activa STARTTLS en el servidor: sin él el correo viaja sin cifrar."
            return c
        ctx = ssl.create_default_context()
        try:
            s.starttls(context=ctx)
            cert = s.sock.getpeercert()
            ok_cert = True
        except ssl.SSLCertVerificationError as e:
            ok_cert = False
            c.items.append(("bad", f"Certificado no válido: {e.verify_message or e}"))
            cert = None
        except (ssl.SSLError, smtplib.SMTPException, OSError) as e:
            c.level, c.summary = "bad", f"Falla el cifrado: {e}"
            return c
        if cert:
            exp = _dt.datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
            days = (exp - _dt.datetime.utcnow()).days
            subj = dict(x[0] for x in cert.get("subject", ()))
            c.items.append(("ok" if days > 14 else "warn" if days > 0 else "bad",
                            f"Certificado de {subj.get('commonName', '?')} válido hasta {exp:%d/%m/%Y} ({days} días)"))
        if ok_cert and all(i[0] == "ok" for i in c.items):
            c.level, c.summary = "ok", "Responde, cifra (STARTTLS) y su certificado es válido"
        elif ok_cert:
            c.level, c.summary = "warn", "Cifra, pero el certificado caduca pronto"
            c.advice = "Renueva el certificado del servidor de correo."
        else:
            c.level, c.summary = "warn", "Cifra, pero con un certificado no válido"
            c.advice = "Pon un certificado válido para el nombre del servidor (muchos remitentes no lo exigen, pero MTA-STS sí)."
    finally:
        try:
            s.quit()
        except Exception:  # noqa: BLE001
            pass
    return c


def _dnsbl(name: str) -> str | None:
    """Respuesta A de la lista (None = no está)."""
    try:
        r = query(name, "A")
        return r[0].to_text() if r else None
    except (LookupError, TimeoutError):
        return None


def check_blacklists(domain: str, mx: list[tuple[int, str]]) -> Check:
    c = Check("bl", "Listas negras")
    ips = []
    for _p, h in mx[:4]:
        ips += [ip for ip in ips_of(h) if ":" not in ip]
    ips = list(dict.fromkeys(ips))[:6]
    jobs = []
    for ip in ips:
        rev = ".".join(reversed(ip.split(".")))
        jobs += [(ip, z, label, f"{rev}.{z}") for z, label in IP_LISTS]
    jobs += [(domain, z, label, f"{domain}.{z}") for z, label in DOMAIN_LISTS]
    with cf.ThreadPoolExecutor(12) as ex:
        answers = list(ex.map(lambda j: _dnsbl(j[3]), jobs))
    listed, unavailable = [], set()
    for (what, zone, label, _q), a in zip(jobs, answers):
        if a is None:
            continue
        # Respuestas de «no te contesto»: Spamhaus 127.255.255.x (resolvedor público o sin permiso), SURBL 127.0.0.1
        if a.startswith("127.255.255.") or (zone == "multi.surbl.org" and a == "127.0.0.1"):
            unavailable.add(label)
            continue
        listed.append((what, label, a))
    for what, label, a in listed:
        c.items.append(("bad", f"{what} está en {label} ({a})"))
    for label in sorted(unavailable):
        c.items.append(("info", f"{label}: no responde a tu servidor DNS (pasa con los DNS públicos)"))
    c.records = [f"IP comprobadas: {', '.join(ips) if ips else 'ninguna'}", f"Listas: {', '.join(l for _z, l in IP_LISTS + DOMAIN_LISTS)}"]
    if listed:
        c.level, c.summary = "bad", f"Aparece en {len(listed)} lista(s)"
        c.advice = "Busca el motivo (equipo infectado, envíos masivos, cuenta robada), corrígelo y pide la baja en la web de cada lista."
    else:
        c.level, c.summary = "ok", f"No aparece ({len(ips)} IP y el dominio)"
    return c


def run(domain: str, dkim_selectors: list[str] | None = None, progress=None, cancelled=lambda: False) -> Report:
    domain = normalize_domain(domain)
    rep = Report(domain, _dt.datetime.now().strftime("%d/%m/%Y %H:%M"))
    steps = ["DNS", "MX", "SPF", "DMARC", "DKIM", "MTA-STS", "TLS-RPT", "BIMI", "PTR", "SMTP", "Listas negras"]

    def step(i, fn):
        if cancelled():
            return None
        if progress:
            progress(i, len(steps), f"{domain}: {steps[i]}")
        try:
            return fn()
        except TimeoutError as e:
            return Check(steps[i].lower(), steps[i], "warn", str(e))
        except Exception as e:  # noqa: BLE001 - una comprobación que falla no para las demás
            log.exception("comprobación %s", steps[i])
            return Check(steps[i].lower(), steps[i], "warn", f"No se pudo comprobar: {e}")

    d = step(0, lambda: check_dns(domain))
    rep.checks.append(d)
    if d and d.summary == "El dominio no existe":
        return rep
    got = step(1, lambda: check_mx(domain))
    mx = []
    if isinstance(got, tuple):
        mxc, mx = got
        rep.checks.append(mxc)
    elif got:
        rep.checks.append(got)
    for i, fn in ((2, lambda: check_spf(domain)), (3, lambda: check_dmarc(domain)), (4, lambda: check_dkim(domain, dkim_selectors)),
                  (5, lambda: check_mta_sts(domain)), (6, lambda: check_tlsrpt(domain)), (7, lambda: check_bimi(domain)),
                  (8, lambda: check_ptr(mx)), (9, lambda: check_smtp(mx)), (10, lambda: check_blacklists(domain, mx))):
        r = step(i, fn)
        if r is not None:
            rep.checks.append(r)
    if progress:
        progress(len(steps), len(steps), f"{domain}: terminado")
    return rep
