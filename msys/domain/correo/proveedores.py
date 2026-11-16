# -*- coding: utf-8 -*-
"""Proveedores de correo conocidos (los mismos de GestCorreov4.ps1) y cómo reconocerlos por el dominio o por su MX."""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    name: str
    imap: str
    imap_port: int
    imap_ssl: bool
    pop: str
    pop_port: int
    pop_ssl: bool
    smtp: str
    smtp_port: int
    smtp_ssl: bool          # True = SSL/TLS directo; False = STARTTLS


PROVIDERS = [
    Provider("Gmail", "imap.gmail.com", 993, True, "pop.gmail.com", 995, True, "smtp.gmail.com", 587, False),
    Provider("Outlook.com", "outlook.office365.com", 993, True, "outlook.office365.com", 995, True, "smtp.office365.com", 587, False),
    Provider("Yahoo", "imap.mail.yahoo.com", 993, True, "pop.mail.yahoo.com", 995, True, "smtp.mail.yahoo.com", 465, True),
    Provider("iCloud", "imap.mail.me.com", 993, True, "", 0, False, "smtp.mail.me.com", 587, False),
    Provider("AOL", "imap.aol.com", 993, True, "pop.aol.com", 995, True, "smtp.aol.com", 587, False),
    Provider("GMX", "imap.gmx.com", 993, True, "pop.gmx.com", 995, True, "mail.gmx.com", 465, True),
    Provider("Web.de", "imap.web.de", 993, True, "pop3.web.de", 995, True, "smtp.web.de", 587, False),
    Provider("T-Online", "secureimap.t-online.de", 993, True, "securepop.t-online.de", 995, True, "securesmtp.t-online.de", 465, True),
    Provider("Mail.ru", "imap.mail.ru", 993, True, "pop.mail.ru", 995, True, "smtp.mail.ru", 465, True),
    Provider("Yandex", "imap.yandex.com", 993, True, "pop.yandex.com", 995, True, "smtp.yandex.com", 465, True),
    Provider("Zoho", "imap.zoho.com", 993, True, "pop.zoho.com", 995, True, "smtp.zoho.com", 465, True),
    Provider("Fastmail", "imap.fastmail.com", 993, True, "pop.fastmail.com", 995, True, "smtp.fastmail.com", 465, True),
    Provider("Posteo", "posteo.de", 993, True, "posteo.de", 995, True, "posteo.de", 587, False),
    Provider("Mailbox.org", "imap.mailbox.org", 993, True, "pop3.mailbox.org", 995, True, "smtp.mailbox.org", 465, True),
    Provider("Tutanota", "mail.tutanota.com", 993, True, "", 0, False, "mail.tutanota.com", 587, False),
    Provider("ProtonMail", "127.0.0.1", 1143, False, "", 0, False, "127.0.0.1", 1025, False),
    Provider("Movistar", "imap.movistar.es", 993, True, "pop3.movistar.es", 995, True, "smtp.movistar.es", 465, True),
    Provider("Orange", "imap.orange.es", 993, True, "pop.orange.es", 995, True, "smtp.orange.es", 587, False),
    Provider("Jazztel", "imap.jazztel.es", 993, True, "pop.jazztel.es", 995, True, "smtp.jazztel.es", 587, False),
    Provider("Vodafone", "imap.vodafone.es", 993, True, "pop.vodafone.es", 995, True, "smtp.vodafone.es", 587, False),
    Provider("Masmovil", "imap.masmovil.es", 993, True, "pop.masmovil.es", 995, True, "smtp.masmovil.es", 587, False),
    Provider("Arsys", "imap.serviciodecorreo.es", 993, True, "pop.serviciodecorreo.es", 995, True, "smtp.serviciodecorreo.es", 465, True),
    Provider("Dinahosting", "mail.dinahosting.com", 993, True, "mail.dinahosting.com", 995, True, "mail.dinahosting.com", 465, True),
    Provider("Ionos", "imap.ionos.es", 993, True, "pop.ionos.es", 995, True, "smtp.ionos.es", 587, False),
    Provider("Hostalia", "imap.hostalia.com", 993, True, "pop.hostalia.com", 995, True, "smtp.hostalia.com", 587, False),
    Provider("Acens", "imap.acens.net", 993, True, "pop.acens.net", 995, True, "smtp.acens.net", 465, True),
    Provider("CDmon", "imap.cdmon.com", 993, True, "pop.cdmon.com", 995, True, "smtp.cdmon.com", 587, False),
    Provider("DonDominio", "imap.dondominio.com", 993, True, "pop.dondominio.com", 995, True, "smtp.dondominio.com", 587, False),
    Provider("GoDaddy", "imap.secureserver.net", 993, True, "pop.secureserver.net", 995, True, "smtpout.secureserver.net", 465, True),
    Provider("One.com", "imap.one.com", 993, True, "pop.one.com", 995, True, "send.one.com", 587, False),
    Provider("OVH", "ssl0.ovh.net", 993, True, "ssl0.ovh.net", 995, True, "ssl0.ovh.net", 465, True),
    Provider("Rackspace", "secure.emailsrvr.com", 993, True, "secure.emailsrvr.com", 995, True, "secure.emailsrvr.com", 465, True),
    Provider("Strato", "imap.strato.com", 993, True, "pop3.strato.com", 995, True, "smtp.strato.com", 465, True),
    Provider("Hostinger", "imap.hostinger.com", 993, True, "pop.hostinger.com", 995, True, "smtp.hostinger.com", 465, True),
    Provider("SiteGround", "imap.siteground.com", 993, True, "pop3.siteground.com", 995, True, "smtp.siteground.com", 465, True),
    Provider("Bluehost", "imap.bluehost.com", 993, True, "pop3.bluehost.com", 995, True, "smtp.bluehost.com", 465, True),
    Provider("DreamHost", "imap.dreamhost.com", 993, True, "pop.dreamhost.com", 995, True, "smtp.dreamhost.com", 465, True),
    Provider("Namecheap", "mail.privateemail.com", 993, True, "mail.privateemail.com", 995, True, "mail.privateemail.com", 465, True),
    Provider("Hetzner", "imap.your-server.de", 993, True, "pop3.your-server.de", 995, True, "smtp.your-server.de", 587, False),
    Provider("Infomaniak", "mail.infomaniak.com", 993, True, "pop.infomaniak.com", 995, True, "mail.infomaniak.com", 465, True),
]
BY_NAME = {p.name: p for p in PROVIDERS}

# Dominio del correo -> proveedor (los de la v4). Anclados a etiquetas completas del dominio: antes eran subcadenas y
# «acme.com» se reconocía como iCloud («me.com»), «citylive.com» como Outlook.com y «kaol.com» como AOL, así que
# «Probar cuenta» mandaba la contraseña a un proveedor que no era el de la cuenta.
_TLD = r"\.[a-z]{2,3}(\.[a-z]{2})?$"
DOMAIN_PATTERNS = {
    "Gmail": r"^(gmail|googlemail)\.com$",
    "Outlook.com": r"(^|\.)(outlook|hotmail|live|msn)" + _TLD,
    "Yahoo": r"(^|\.)(yahoo|ymail)" + _TLD,
    "iCloud": r"^(icloud|me|mac)\.com$",
    "AOL": r"(^|\.)aol" + _TLD,
    "GMX": r"(^|\.)gmx" + _TLD,
    "Web.de": r"^web\.de$",
    "T-Online": r"(^|\.)t-online\.de$",
    "Yandex": r"(^|\.)yandex" + _TLD,
    "Mail.ru": r"^mail\.ru$",
    "Zoho": r"(^|\.)zoho(mail)?" + _TLD,
    "Fastmail": r"(^|\.)fastmail\.(com|fm)$",
    "Movistar": r"(^|\.)(movistar|telefonica)\.(es|net)$",
    "Orange": r"(^|\.)orange\.es$",
    "Jazztel": r"(^|\.)jazztel\.(es|net)$",
}

# Servidor de correo (MX) del dominio -> proveedor: reconoce dominios propios alojados en esos servicios
MX_PATTERNS = {
    "Gmail": r"(^|\.)(google\.com|googlemail\.com)$",
    "Outlook.com": r"(^|\.)(protection\.outlook\.com|outlook\.com|hotmail\.com)$",
    "Yahoo": r"(^|\.)yahoodns\.net$",
    "iCloud": r"(^|\.)icloud\.com$",
    "Zoho": r"(^|\.)zoho\.(com|eu)$",
    "Fastmail": r"(^|\.)messagingengine\.com$",
    "Ionos": r"(^|\.)(ionos\.(es|com|de)|1and1\.(es|com))$",
    "OVH": r"(^|\.)ovh\.net$",
    "GoDaddy": r"(^|\.)secureserver\.net$",
    "Arsys": r"(^|\.)serviciodecorreo\.es$",
    "Dinahosting": r"(^|\.)dinahosting\.com$",
    "Hostinger": r"(^|\.)hostinger\.com$",
    "Strato": r"(^|\.)strato\.(de|com)$",
    "Infomaniak": r"(^|\.)infomaniak\.ch$",
    "Hetzner": r"(^|\.)your-server\.de$",
    "Namecheap": r"(^|\.)privateemail\.com$",
    "Rackspace": r"(^|\.)emailsrvr\.com$",
    "Mailbox.org": r"(^|\.)mailbox\.org$",
    "Yandex": r"(^|\.)yandex\.(net|ru|com)$",
}


def by_domain(domain: str) -> list[str]:
    d = domain.lower().strip()
    return [n for n, pat in DOMAIN_PATTERNS.items() if re.search(pat, d)]


def by_mx(mx_hosts: list[str]) -> list[str]:
    out = []
    for h in mx_hosts:
        h = h.lower().rstrip(".")
        for n, pat in MX_PATTERNS.items():
            if re.search(pat, h) and n not in out:
                out.append(n)
    return out


def security_name(ssl_direct: bool) -> str:
    return "SSL/TLS" if ssl_direct else "STARTTLS"
