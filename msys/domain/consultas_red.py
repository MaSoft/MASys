# -*- coding: utf-8 -*-
"""Consultas de red sueltas (idea de yougetsignal.com/tools) para la sección
🧪 Herramientas de red. Sin Qt; cada consulta devuelve una lista de filas
(campo, valor, estado) donde estado es OK / AVISO / ERROR / INFO.

Incluido: DNS por tipo de registro, WHOIS por RDAP (el protocolo oficial que
sustituye a WHOIS, sin clave), cabeceras y redirecciones HTTP con las
cabeceras de seguridad, certificado TLS y mi IP pública.
No incluido a propósito: IP inversa (bases de datos de pago), ubicación de
una IP (imprecisa) y búsqueda de personas por correo o teléfono (RGPD)."""

import ipaddress
import re
import socket
import ssl
import time
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from urllib.parse import urlparse

OK, AVISO, ERROR, INFO = "OK", "AVISO", "ERROR", "INFO"
Fila = Tuple[str, str, str]

TIPOS_DNS = ["A", "AAAA", "MX", "TXT", "NS", "CNAME", "SOA", "SRV", "CAA", "PTR"]
SERVIDORES_DNS = [("Sistema", None), ("Google 8.8.8.8", "8.8.8.8"), ("Cloudflare 1.1.1.1", "1.1.1.1"), ("Quad9 9.9.9.9", "9.9.9.9")]

_RE_HOST = re.compile(r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})*\.?$")


def es_ip(texto: str) -> bool:
    try:
        ipaddress.ip_address(texto)
        return True
    except ValueError:
        return False


def limpiar_destino(texto: str) -> str:
    """Dominio o IP a partir de lo que se escriba (acepta una URL). Lanza
    ValueError si no es un nombre de equipo / dominio / IP válido: protege a
    ping/tracert de argumentos que empiecen por '-' u otros caracteres."""
    t = (texto or "").strip()
    if "://" in t:
        t = urlparse(t).hostname or ""
    t = t.strip().strip("/").split("/")[0]
    if t.startswith("[") and t.endswith("]"):
        t = t[1:-1]
    if es_ip(t):
        return t
    if ":" in t and not es_ip(t):
        t = t.rsplit(":", 1)[0]
    if not t or t.startswith("-") or not _RE_HOST.match(t):
        raise ValueError(f"«{texto}» no es un nombre de dominio, equipo o IP válido")
    return t.rstrip(".").lower()


def _fecha(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().strftime("%d/%m/%Y %H:%M")
    except (ValueError, AttributeError):
        return iso or ""


# ── DNS ─────────────────────────────────────────────────────────────────────
def dns_consulta(nombre: str, tipo: str = "A", servidor: Optional[str] = None) -> List[Fila]:
    import dns.exception
    import dns.resolver
    import dns.reversename
    tipo = tipo.upper()
    if tipo not in TIPOS_DNS:
        raise ValueError(f"Tipo de registro no admitido: {tipo}")
    destino = limpiar_destino(nombre)
    if tipo == "PTR":
        if not es_ip(destino):
            raise ValueError("Para PTR (DNS inverso) escribe una IP")
        destino = dns.reversename.from_address(destino).to_text()
    r = dns.resolver.Resolver(configure=servidor is None)
    if servidor:
        r.nameservers = [servidor]
    r.timeout = r.lifetime = 4
    inicio = time.perf_counter()
    try:
        respuesta = r.resolve(destino, tipo)
    except dns.resolver.NXDOMAIN:
        return [("Resultado", "El nombre no existe (NXDOMAIN)", ERROR)]
    except dns.resolver.NoAnswer:
        return [("Resultado", f"El nombre existe pero no tiene registros {tipo}", AVISO)]
    except dns.exception.Timeout:
        return [("Resultado", "Sin respuesta del servidor DNS en 4 s", ERROR)]
    except dns.resolver.NoNameservers as e:
        return [("Resultado", f"Ningún servidor DNS respondió bien: {e}", ERROR)]
    ms = (time.perf_counter() - inicio) * 1000
    filas = [(tipo, rr.to_text(), OK) for rr in respuesta]
    filas.append(("TTL", f"{respuesta.rrset.ttl} s", INFO))
    filas.append(("Servidor", f"{servidor or 'el del sistema'} · {ms:.0f} ms", INFO))
    if tipo == "TXT":
        txt = " ".join(v for _t, v, _e in filas)
        filas.append(("SPF", "Publicado" if "v=spf1" in txt else "No hay registro SPF (correo falsificable)", OK if "v=spf1" in txt else AVISO))
    return filas


def dns_comparar(nombre: str, tipo: str = "A") -> List[Fila]:
    """El mismo registro preguntado a varios servidores (propagación / DNS secuestrado)."""
    filas, vistos = [], set()
    for etiqueta, servidor in SERVIDORES_DNS:
        try:
            res = [v for t, v, _e in dns_consulta(nombre, tipo, servidor) if t == tipo.upper()]
            texto = ", ".join(sorted(res)) or "(sin registros)"
            vistos.add(texto)
            filas.append((etiqueta, texto, OK if res else AVISO))
        except Exception as e:
            filas.append((etiqueta, str(e), ERROR))
    if len(vistos) > 1:
        filas.append(("Coherencia", "Los servidores responden distinto (CDN, cambio reciente o DNS manipulado)", AVISO))
    return filas


# ── WHOIS por RDAP ──────────────────────────────────────────────────────────
def _vcard_nombre(entidad: dict) -> str:
    for campo in (entidad.get("vcardArray") or [None, []])[1]:
        if campo and campo[0] == "fn":
            return str(campo[3])
    return entidad.get("handle", "")


def rdap(consulta: str) -> List[Fila]:
    import requests
    destino = limpiar_destino(consulta)
    ip = es_ip(destino)
    if not ip and destino.count(".") >= 2:
        # www.ejemplo.com -> ejemplo.com (el RDAP es del dominio registrado)
        partes = destino.split(".")
        destino = ".".join(partes[-3:]) if partes[-2] in ("com", "co", "org", "net", "gob", "edu") and len(partes[-1]) == 2 else ".".join(partes[-2:])
    url = f"https://rdap.org/{'ip' if ip else 'domain'}/{destino}"
    try:
        r = requests.get(url, timeout=15, headers={"Accept": "application/rdap+json"})
    except requests.RequestException as e:
        return [("Resultado", f"No se pudo consultar: {e}", ERROR)]
    if r.status_code == 404:
        tld = destino.rsplit(".", 1)[-1]
        if not ip and tld == "es":
            return [("Resultado", "El registro de .es (Red.es) no ofrece RDAP: consulta en https://www.dominios.es", AVISO)]
        return [("Resultado", "No encontrado (dominio libre o su registro no ofrece RDAP)", AVISO)]
    if r.status_code != 200:
        return [("Resultado", f"El servidor RDAP respondió {r.status_code}", ERROR)]
    d = r.json()
    filas: List[Fila] = []
    if ip:
        filas.append(("Red", f"{d.get('startAddress', '')} – {d.get('endAddress', '')}", INFO))
        filas.append(("Nombre", d.get("name", ""), INFO))
        if d.get("country"):
            filas.append(("País", d["country"], INFO))
    else:
        filas.append(("Dominio", d.get("ldhName", destino), INFO))
    for e in d.get("entities", []):
        roles = ", ".join(e.get("roles", []))
        nombre = _vcard_nombre(e)
        if nombre and any(x in roles for x in ("registrar", "registrant", "abuse", "administrative")):
            filas.append((f"Entidad ({roles})", nombre, INFO))
    eventos = {ev.get("eventAction"): ev.get("eventDate") for ev in d.get("events", [])}
    for clave, etiqueta in (("registration", "Registrado"), ("last changed", "Último cambio"), ("expiration", "Caduca")):
        if eventos.get(clave):
            filas.append((etiqueta, _fecha(eventos[clave]), INFO))
    if eventos.get("expiration"):
        try:
            dias = (datetime.fromisoformat(eventos["expiration"].replace("Z", "+00:00")) - datetime.now(timezone.utc)).days
            filas.append(("Días hasta caducar", str(dias), ERROR if dias < 15 else AVISO if dias < 45 else OK))
        except ValueError:
            pass
    ns = [n.get("ldhName", "") for n in d.get("nameservers", [])]
    if ns:
        filas.append(("Servidores DNS", ", ".join(ns), INFO))
    if d.get("status"):
        filas.append(("Estado", ", ".join(d["status"]), INFO))
    return filas


# ── HTTP ────────────────────────────────────────────────────────────────────
CABECERAS_SEGURIDAD = [
    ("Strict-Transport-Security", "Obliga a usar HTTPS (HSTS)"),
    ("Content-Security-Policy", "Limita de dónde se cargan scripts (CSP)"),
    ("X-Frame-Options", "Impide meter la web en marcos de otra (clickjacking)"),
    ("X-Content-Type-Options", "Evita que el navegador adivine tipos de archivo"),
    ("Referrer-Policy", "Controla qué se cuenta al pulsar enlaces"),
    ("Permissions-Policy", "Limita cámara, micrófono, ubicación…"),
]


def http_cabeceras(url: str) -> List[Fila]:
    import requests
    if "://" not in url:
        url = "https://" + limpiar_destino(url)
    elif urlparse(url).scheme not in ("http", "https"):
        raise ValueError("Solo direcciones http:// o https://")
    else:
        limpiar_destino(url)
    inicio = time.perf_counter()
    try:
        r = requests.get(url, timeout=12, allow_redirects=True, stream=True,
                         headers={"User-Agent": "Mozilla/5.0 (MASys)"})
        r.close()
    except requests.exceptions.SSLError as e:
        return [("Resultado", f"Error de certificado: {e}", ERROR)]
    except requests.RequestException as e:
        return [("Resultado", f"No se pudo conectar: {e}", ERROR)]
    ms = (time.perf_counter() - inicio) * 1000
    filas: List[Fila] = []
    for i, h in enumerate(r.history, start=1):
        filas.append((f"Redirección {i}", f"{h.status_code} {h.url} → {h.headers.get('Location', '')}", INFO))
    estado = OK if r.status_code < 400 else AVISO if r.status_code < 500 else ERROR
    filas.append(("Respuesta final", f"{r.status_code} {r.reason} · {r.url} · {ms:.0f} ms", estado))
    for cab, que in CABECERAS_SEGURIDAD:
        valor = r.headers.get(cab)
        filas.append((cab, valor if valor else f"Falta — {que}", OK if valor else AVISO))
    for cab in ("Server", "X-Powered-By"):
        if r.headers.get(cab):
            filas.append((cab, r.headers[cab] + " (revela qué software usa el servidor)", AVISO))
    vistos = {c for c, _q in CABECERAS_SEGURIDAD} | {"Server", "X-Powered-By"}
    for cab, valor in r.headers.items():
        if cab not in vistos:
            filas.append((cab, valor, INFO))
    return filas


# ── Certificado TLS ─────────────────────────────────────────────────────────
def certificado(destino: str) -> List[Fila]:
    texto = destino.strip()
    puerto = 443
    m = re.match(r"^(.*):(\d{1,5})$", texto.split("://")[-1].split("/")[0])
    if m and not es_ip(texto):
        texto, puerto = m.group(1), int(m.group(2))
    host = limpiar_destino(texto)
    if not 0 < puerto < 65536:
        raise ValueError("Puerto no válido")
    filas: List[Fila] = []
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, puerto), timeout=8) as s, ctx.wrap_socket(s, server_hostname=host) as t:
            cert, proto, cifrado = t.getpeercert(), t.version(), t.cipher()
    except ssl.SSLCertVerificationError as e:
        return [("Certificado", f"NO válido: {e.verify_message}", ERROR)]
    except OSError as e:
        return [("Conexión", f"No se pudo conectar a {host}:{puerto}: {e}", ERROR)]
    sujeto = dict(x[0] for x in cert.get("subject", []))
    emisor = dict(x[0] for x in cert.get("issuer", []))
    desde = datetime.strptime(cert["notBefore"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    hasta = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    dias = (hasta - datetime.now(timezone.utc)).days
    filas.append(("Para", sujeto.get("commonName", "?"), OK))
    filas.append(("Emitido por", f"{emisor.get('organizationName', '')} · {emisor.get('commonName', '')}".strip(" ·"), INFO))
    filas.append(("Válido desde", desde.astimezone().strftime("%d/%m/%Y"), INFO))
    filas.append(("Caduca", f"{hasta.astimezone().strftime('%d/%m/%Y')} (en {dias} días)", ERROR if dias < 15 else AVISO if dias < 30 else OK))
    nombres = [v for k, v in cert.get("subjectAltName", []) if k == "DNS"]
    filas.append(("Nombres incluidos", ", ".join(nombres[:30]) + (" …" if len(nombres) > 30 else ""), INFO))
    debil = proto in ("TLSv1", "TLSv1.1", "SSLv3")
    filas.append(("Protocolo", f"{proto}" + (" (obsoleto)" if debil else ""), AVISO if debil else OK))
    filas.append(("Cifrado", f"{cifrado[0]} · {cifrado[2]} bits" if cifrado else "?", INFO))
    return filas


# ── Mi IP pública ───────────────────────────────────────────────────────────
def mi_ip() -> List[Fila]:
    import requests
    filas: List[Fila] = []
    for etiqueta, url in (("IPv4 pública", "https://api.ipify.org"), ("IPv6 pública", "https://api6.ipify.org")):
        try:
            ip = requests.get(url, timeout=8).text.strip()
            if not es_ip(ip):
                raise ValueError(ip[:60])
            filas.append((etiqueta, ip, OK))
            try:
                filas.append((f"Nombre de {etiqueta.split()[0]}", socket.gethostbyaddr(ip)[0] + " (suele indicar el proveedor)", INFO))
            except OSError:
                pass
        except Exception:
            filas.append((etiqueta, "Sin conexión por " + etiqueta.split()[0], INFO if "IPv6" in etiqueta else ERROR))
    try:
        local = socket.gethostbyname(socket.gethostname())
        filas.append(("IP local", local, INFO))
    except OSError:
        pass
    return filas


HERRAMIENTAS = [
    ("DNS", "Registros DNS (elige el tipo)"),
    ("DNS en varios servidores", "El mismo registro en Sistema, Google, Cloudflare y Quad9"),
    ("WHOIS (RDAP)", "Quién tiene el dominio o la red, cuándo caduca"),
    ("Web: cabeceras y redirecciones", "Respuesta, redirecciones y cabeceras de seguridad"),
    ("Certificado TLS", "Para quién es, quién lo emite y cuándo caduca (host o host:puerto)"),
    ("Mi IP pública", "La IP con la que este equipo sale a internet"),
]


def consultar(herramienta: str, texto: str, tipo_dns: str = "A") -> List[Fila]:
    if herramienta == "DNS":
        return dns_consulta(texto, tipo_dns)
    if herramienta == "DNS en varios servidores":
        return dns_comparar(texto, tipo_dns)
    if herramienta == "WHOIS (RDAP)":
        return rdap(texto)
    if herramienta == "Web: cabeceras y redirecciones":
        return http_cabeceras(texto)
    if herramienta == "Certificado TLS":
        return certificado(texto)
    if herramienta == "Mi IP pública":
        return mi_ip()
    raise ValueError(f"Herramienta desconocida: {herramienta}")
