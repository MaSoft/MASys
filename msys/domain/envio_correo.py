# -*- coding: utf-8 -*-
"""Envío de correos a los clientes (5.1.0): cuentas configurables por quien usa MASys, plantillas e historial.

- Tipos de cuenta:
    · «smtp»: cualquier servidor SMTP (el del dominio, Exchange local, proveedores…). STARTTLS, SSL directo o sin
      cifrar (este último solo para relés internos). Si se pide STARTTLS y el servidor no lo ofrece, NO se envía la
      contraseña (misma regla que msys/domain/correo/servidores.py). Certificados validados con el almacén del sistema.
    · «gmail»: smtp.gmail.com:587 STARTTLS con una CONTRASEÑA DE APLICACIÓN (requiere verificación en dos pasos).
    · «m365»: Microsoft 365 por Microsoft Graph con OAuth 2.0 (credenciales de cliente): aplicación registrada en
      Entra ID con el permiso de APLICACIÓN Mail.Send y consentimiento de administrador. POST
      /v1.0/users/{remitente}/sendMail → 202. (Exchange Online desactiva por defecto el SMTP AUTH «básico» desde
      diciembre de 2026, MC786329: por eso no se usa usuario y contraseña.)
- Contraseñas y secretos de cliente: DPAPI del usuario de Windows (como el resto de MASys). Nunca en claro.
- Plantillas con variables {empresa} {usuario} {correo} {telefono} {equipo} {enlace} {codigo} {tecnico} {fecha}.
- Los .exe NO se adjuntan (Gmail y Exchange los bloquean y el agente pesa más de 25 MB): van como {enlace}.

Sin Qt: las ventanas están en msys/ui/correo_envio_dialog.py."""

import os
import re
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import Callable, Dict, List, Optional

from msys import config as msys_config
from msys.core.utils import _sqlite_connect
from msys.domain import clientes

SMTP, GMAIL, M365 = "smtp", "gmail", "m365"
TIPOS = [(SMTP, "Servidor SMTP"), (GMAIL, "Gmail (contraseña de aplicación)"), (M365, "Microsoft 365 (Graph, OAuth)")]
NOMBRE_TIPO = dict(TIPOS)
STARTTLS, SSL_DIRECTO, SIN_CIFRAR = "starttls", "ssl", "ninguna"
SEGURIDADES = [(STARTTLS, "STARTTLS"), (SSL_DIRECTO, "SSL/TLS directo"), (SIN_CIFRAR, "Sin cifrar (solo relé interno)")]
GMAIL_SERVIDOR, GMAIL_PUERTO = "smtp.gmail.com", 587
URL_TOKEN = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
URL_ENVIO = "https://graph.microsoft.com/v1.0/users/{remitente}/sendMail"
VARIABLES = [("empresa", "Empresa del destinatario"), ("usuario", "Nombre del usuario"), ("correo", "Su correo"),
             ("telefono", "Su teléfono"), ("equipo", "Nombre del equipo"), ("enlace", "Enlace de descarga"),
             ("codigo", "Código (del agente o de envío)"), ("tecnico", "Tu nombre (el de la cuenta)"),
             ("fecha", "Fecha de hoy")]
_RE_GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_RE_DOMINIO = re.compile(r"^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")
_RE_HOST = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9\-.]{0,252})$")

PLANTILLAS_INICIALES = [
    ("Programa de RustDesk", "Acceso remoto para {empresa}",
     "Hola {usuario}:\n\nPara que pueda ayudarte en tu equipo, descarga este programa:\n{enlace}\n\n"
     "1. Guárdalo sin cambiarle el nombre: lleva la configuración para conectar conmigo.\n"
     "2. Ábrelo (no hace falta instalarlo).\n"
     "3. Cuando me conecte, pulsa «Aceptar» en la ventana que aparece.\n\nUn saludo,\n{tecnico}"),
    ("Agente de análisis", "Revisión de tu equipo · {empresa}",
     "Hola {usuario}:\n\nPara revisar tu equipo sin tener que conectarme, descarga este programa:\n{enlace}\n\n"
     "1. Ábrelo con doble clic (no hace falta instalarlo) y acepta el aviso de permisos.\n"
     "2. Pega este código cuando te lo pida:\n{codigo}\n"
     "3. Espera a que termine: me llegará el informe cifrado.\n\nUn saludo,\n{tecnico}"),
    ("En blanco", "", "Hola {usuario}:\n\n\n\nUn saludo,\n{tecnico}"),
]


class ErrorEnvio(Exception):
    pass


@dataclass
class Perfil:
    id: int = 0
    nombre: str = ""
    tipo: str = SMTP
    remitente: str = ""            # dirección desde la que se envía
    nombre_remitente: str = ""     # «Nombre · Masoft.es» (también {tecnico})
    servidor: str = ""
    puerto: int = 587
    seguridad: str = STARTTLS
    usuario: str = ""              # SMTP: vacío = el remitente
    tenant: str = ""               # Microsoft 365: id del inquilino o dominio
    cliente_id: str = ""           # Microsoft 365: id de la aplicación
    predeterminado: bool = False
    con_secreto: bool = False


@dataclass
class Plantilla:
    id: int = 0
    nombre: str = ""
    asunto: str = ""
    cuerpo: str = ""
    enlace: str = ""               # último enlace usado con esta plantilla


@dataclass
class Mensaje:
    para: List[str]
    asunto: str
    cuerpo: str


@dataclass
class Enviado:
    fecha: str
    cuenta: str
    para: str
    asunto: str
    empresa: str
    usuario: str
    resultado: str


# ── Validación ───────────────────────────────────────────────────────────
def con_preajuste(p: Perfil) -> Perfil:
    """Gmail siempre con su servidor; el usuario de Gmail es el propio remitente."""
    if p.tipo == GMAIL:
        p.servidor, p.puerto, p.seguridad, p.usuario = GMAIL_SERVIDOR, GMAIL_PUERTO, STARTTLS, ""
    return p


def errores_perfil(p: Perfil, tiene_secreto: bool) -> List[str]:
    errores = []
    if not p.nombre.strip():
        errores.append("Falta el nombre de la cuenta")
    if p.tipo not in NOMBRE_TIPO:
        errores.append("Tipo de cuenta desconocido")
    try:
        if not clientes.validar_correo(p.remitente):
            errores.append("Falta el correo del remitente")
    except ValueError as e:
        errores.append(str(e))
    if p.tipo == SMTP:
        if not p.servidor.strip() or p.servidor.strip().startswith("-") or not _RE_HOST.match(p.servidor.strip()):
            errores.append("Falta el servidor SMTP o no es válido")
        if not 1 <= int(p.puerto or 0) <= 65535:
            errores.append("El puerto va de 1 a 65535")
        if p.seguridad not in dict(SEGURIDADES):
            errores.append("Seguridad desconocida")
    if p.tipo == GMAIL and not tiene_secreto:
        errores.append("Gmail necesita una contraseña de aplicación")
    if p.tipo == M365:
        if not (_RE_GUID.match(p.tenant.strip()) or _RE_DOMINIO.match(p.tenant.strip())):
            errores.append("Microsoft 365: el inquilino es su id (GUID) o su dominio (empresa.onmicrosoft.com)")
        if not _RE_GUID.match(p.cliente_id.strip()):
            errores.append("Microsoft 365: el id de la aplicación es un GUID")
        if not tiene_secreto:
            errores.append("Microsoft 365 necesita el secreto de la aplicación")
    return errores


def destinatarios(texto: str) -> List[str]:
    """«a@b.es; c@d.es» → lista validada. ValueError si alguna no vale o no hay ninguna."""
    lista = [x.strip() for x in re.split(r"[;,\s]+", texto or "") if x.strip()]
    if not lista:
        raise ValueError("Falta el destinatario")
    for x in lista:
        clientes.validar_correo(x)
    return lista


def rellenar(texto: str, datos: Dict[str, str]) -> str:
    """Sustituye {variable} con los datos; las que no se conocen se dejan tal cual."""
    return re.sub(r"\{(\w+)\}", lambda m: str(datos[m.group(1)]) if m.group(1) in datos else m.group(0), texto or "")


def datos_basicos(perfil: Optional[Perfil] = None, **extra) -> Dict[str, str]:
    datos = {"fecha": datetime.now().strftime("%d/%m/%Y"),
             "tecnico": (perfil.nombre_remitente or perfil.remitente) if perfil else ""}
    datos.update({k: (v or "") for k, v in extra.items()})
    return datos


# ── Envío ────────────────────────────────────────────────────────────────
def _correo_mime(p: Perfil, m: Mensaje) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((p.nombre_remitente, p.remitente)) if p.nombre_remitente else p.remitente
    msg["To"] = ", ".join(m.para)
    msg["Subject"] = m.asunto
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=p.remitente.split("@")[-1])
    msg.set_content(m.cuerpo)
    return msg


def _enviar_smtp(p: Perfil, secreto: str, m: Mensaje, timeout: float, smtp_ssl=smtplib.SMTP_SSL,
                 smtp=smtplib.SMTP) -> None:
    ctx = ssl.create_default_context()
    try:
        if p.seguridad == SSL_DIRECTO:
            c = smtp_ssl(p.servidor, int(p.puerto), timeout=timeout, context=ctx)
        else:
            c = smtp(p.servidor, int(p.puerto), timeout=timeout)
        try:
            c.ehlo()
            if p.seguridad == STARTTLS:
                if not c.has_extn("starttls"):
                    raise ErrorEnvio("El servidor no ofrece STARTTLS: no se envía la contraseña sin cifrar")
                c.starttls(context=ctx)
                c.ehlo()
            if secreto:
                c.login(p.usuario.strip() or p.remitente, secreto)
            rechazados = c.send_message(_correo_mime(p, m))
            if rechazados:
                raise ErrorEnvio("El servidor rechazó: " + ", ".join(rechazados))
        finally:
            try:
                c.quit()
            except Exception:
                pass
    except ErrorEnvio:
        raise
    except smtplib.SMTPAuthenticationError as e:
        pista = " (Gmail: usa una contraseña de aplicación, no la de tu cuenta)" if p.tipo == GMAIL else ""
        raise ErrorEnvio(f"Usuario o contraseña rechazados{pista}: {_texto(e)}") from None
    except smtplib.SMTPRecipientsRefused as e:
        raise ErrorEnvio("Destinatario rechazado: " + ", ".join(e.recipients)) from None
    except smtplib.SMTPException as e:
        raise ErrorEnvio(f"El servidor respondió con un error: {_texto(e)}") from None
    except ssl.SSLError as e:
        raise ErrorEnvio(f"Error de cifrado o certificado no válido: {e}") from None
    except (OSError, TimeoutError) as e:
        raise ErrorEnvio(f"No se pudo conectar con {p.servidor}:{p.puerto}: {e}") from None


def _texto(e: smtplib.SMTPException) -> str:
    detalle = getattr(e, "smtp_error", b"")
    if isinstance(detalle, bytes):
        detalle = detalle.decode("utf-8", errors="replace")
    return str(detalle or e).strip()


def _error_graph(r) -> str:
    try:
        j = r.json()
        return j.get("error_description") or (j.get("error") or {}).get("message") or str(j)[:300]
    except Exception:
        return f"HTTP {r.status_code}"


def _enviar_m365(p: Perfil, secreto: str, m: Mensaje, timeout: float, http=None) -> None:
    if http is None:
        import requests as http
    try:
        r = http.post(URL_TOKEN.format(tenant=p.tenant.strip()), timeout=timeout,
                      data={"client_id": p.cliente_id.strip(), "client_secret": secreto,
                            "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"})
        if r.status_code != 200:
            raise ErrorEnvio(f"Microsoft 365 no dio acceso (revisa inquilino, aplicación y secreto): {_error_graph(r)}")
        token = r.json()["access_token"]
        cuerpo = {"message": {"subject": m.asunto, "body": {"contentType": "Text", "content": m.cuerpo},
                              "toRecipients": [{"emailAddress": {"address": x}} for x in m.para]},
                  "saveToSentItems": True}
        r = http.post(URL_ENVIO.format(remitente=p.remitente.strip()), json=cuerpo, timeout=timeout,
                      headers={"Authorization": f"Bearer {token}"})
        if r.status_code != 202:
            pista = (" (¿la aplicación tiene el permiso de aplicación Mail.Send con consentimiento de administrador y "
                     "acceso a ese buzón?)" if r.status_code in (401, 403) else "")
            raise ErrorEnvio(f"Microsoft 365 no envió el correo{pista}: {_error_graph(r)}")
    except ErrorEnvio:
        raise
    except Exception as e:
        raise ErrorEnvio(f"No se pudo conectar con Microsoft 365: {e}") from None


def enviar(p: Perfil, secreto: str, m: Mensaje, timeout: float = 30, **inyectado) -> None:
    """ErrorEnvio con el motivo en español si no se puede. inyectado: smtp/smtp_ssl/http para las pruebas."""
    p = con_preajuste(Perfil(**vars(p)))
    if p.tipo == M365:
        _enviar_m365(p, secreto, m, timeout, http=inyectado.get("http"))
    else:
        _enviar_smtp(p, secreto, m, timeout, **{k: v for k, v in inyectado.items() if k in ("smtp", "smtp_ssl")})


def detectar_smtp(correo: str) -> Optional[Perfil]:
    """Servidor SMTP de un correo con la detección de MACorreo (SRV, configuración de Thunderbird, proveedores)."""
    from msys.domain.correo import proveedores, servidores
    try:
        det = servidores.detect(correo)
    except Exception:
        return None
    # Proveedores conocidos (por dominio o MX) primero; si no, lo encontrado por SRV o en la base de Thunderbird
    candidatos = [proveedores.BY_NAME[n] for n in det.names if n in proveedores.BY_NAME] + list(det.extra)
    prov = next((x for x in candidatos if x.smtp), None)
    if prov is None:
        return None
    return Perfil(servidor=prov.smtp, puerto=int(prov.smtp_port or 587),
                  seguridad=SSL_DIRECTO if prov.smtp_ssl else STARTTLS)


# ── Base de datos ────────────────────────────────────────────────────────
def _proteger(texto: str) -> bytes:
    import win32crypt
    return win32crypt.CryptProtectData(texto.encode("utf-8"), "MASys correo", None, None, None, 0)


def _desproteger(datos: bytes) -> str:
    import win32crypt
    return win32crypt.CryptUnprotectData(datos, None, None, None, 0)[1].decode("utf-8")


class CorreoDB:
    def __init__(self, path: Optional[str] = None):
        self.path = path or os.path.join(msys_config.app_base_dir(), "multisys.db")
        with self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS correo_cuentas (
                id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL, tipo TEXT NOT NULL, remitente TEXT NOT NULL,
                nombre_remitente TEXT NOT NULL DEFAULT '', servidor TEXT NOT NULL DEFAULT '',
                puerto INTEGER NOT NULL DEFAULT 587, seguridad TEXT NOT NULL DEFAULT 'starttls',
                usuario TEXT NOT NULL DEFAULT '', tenant TEXT NOT NULL DEFAULT '', cliente_id TEXT NOT NULL DEFAULT '',
                secreto BLOB, predeterminado INTEGER NOT NULL DEFAULT 0, creado TEXT NOT NULL, modificado TEXT NOT NULL)""")
            c.execute("""CREATE TABLE IF NOT EXISTS correo_plantillas (
                id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL, asunto TEXT NOT NULL DEFAULT '',
                cuerpo TEXT NOT NULL DEFAULT '', enlace TEXT NOT NULL DEFAULT '', creado TEXT NOT NULL,
                modificado TEXT NOT NULL)""")
            c.execute("""CREATE TABLE IF NOT EXISTS correo_enviados (
                id INTEGER PRIMARY KEY AUTOINCREMENT, fecha TEXT NOT NULL, cuenta TEXT NOT NULL, para TEXT NOT NULL,
                asunto TEXT NOT NULL, empresa TEXT NOT NULL DEFAULT '', usuario TEXT NOT NULL DEFAULT '',
                resultado TEXT NOT NULL)""")
            c.execute("CREATE TABLE IF NOT EXISTS correo_ajustes (clave TEXT PRIMARY KEY, valor TEXT NOT NULL)")
            hecho = c.execute("SELECT valor FROM correo_ajustes WHERE clave = 'plantillas_iniciales'").fetchone()
            if not hecho:      # solo la primera vez: si el usuario las borra, no vuelven
                ahora = self._ahora()
                c.executemany("INSERT INTO correo_plantillas (nombre, asunto, cuerpo, creado, modificado) "
                              "VALUES (?, ?, ?, ?, ?)", [(n, a, t, ahora, ahora) for n, a, t in PLANTILLAS_INICIALES])
                c.execute("INSERT INTO correo_ajustes (clave, valor) VALUES ('plantillas_iniciales', '1')")

    def _conn(self):
        return _sqlite_connect(self.path)

    @staticmethod
    def _ahora() -> str:
        return datetime.now().isoformat(timespec="seconds")

    # Cuentas
    def cuentas(self) -> List[Perfil]:
        with self._conn() as c:
            filas = c.execute("""SELECT id, nombre, tipo, remitente, nombre_remitente, servidor, puerto, seguridad, usuario,
                                 tenant, cliente_id, predeterminado, secreto IS NOT NULL AND length(secreto) > 0
                                 FROM correo_cuentas ORDER BY predeterminado DESC, nombre COLLATE NOCASE""").fetchall()
        return [Perfil(f[0], f[1], f[2], f[3], f[4], f[5], f[6], f[7], f[8], f[9], f[10], bool(f[11]), bool(f[12]))
                for f in filas]

    def predeterminada(self) -> Optional[Perfil]:
        cuentas = self.cuentas()
        return next((p for p in cuentas if p.predeterminado), cuentas[0] if cuentas else None)

    def guardar_cuenta(self, p: Perfil, secreto: Optional[str] = None) -> Perfil:
        """secreto=None deja el guardado. ValueError con lo que falte."""
        p = con_preajuste(Perfil(**vars(p)))
        errores = errores_perfil(p, bool(secreto) or (p.con_secreto and secreto is None))
        if errores:
            raise ValueError("\n".join(errores))
        ahora = self._ahora()
        valores = (p.nombre.strip(), p.tipo, p.remitente.strip(), p.nombre_remitente.strip(), p.servidor.strip(),
                   int(p.puerto), p.seguridad, p.usuario.strip(), p.tenant.strip(), p.cliente_id.strip())
        with self._conn() as c:
            if not p.id:
                cur = c.execute("""INSERT INTO correo_cuentas (nombre, tipo, remitente, nombre_remitente, servidor, puerto,
                                   seguridad, usuario, tenant, cliente_id, creado, modificado)
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", valores + (ahora, ahora))
                p.id = cur.lastrowid
            else:
                c.execute("""UPDATE correo_cuentas SET nombre = ?, tipo = ?, remitente = ?, nombre_remitente = ?,
                             servidor = ?, puerto = ?, seguridad = ?, usuario = ?, tenant = ?, cliente_id = ?,
                             modificado = ? WHERE id = ?""", valores + (ahora, p.id))
            if secreto:
                c.execute("UPDATE correo_cuentas SET secreto = ? WHERE id = ?", (_proteger(secreto), p.id))
            if not c.execute("SELECT 1 FROM correo_cuentas WHERE predeterminado = 1").fetchone():
                c.execute("UPDATE correo_cuentas SET predeterminado = 1 WHERE id = ?", (p.id,))
        p.con_secreto = p.con_secreto or bool(secreto)
        return p

    def fijar_predeterminada(self, cuenta_id: int) -> None:
        with self._conn() as c:
            c.execute("UPDATE correo_cuentas SET predeterminado = CASE WHEN id = ? THEN 1 ELSE 0 END", (cuenta_id,))

    def secreto(self, cuenta_id: int) -> str:
        with self._conn() as c:
            f = c.execute("SELECT secreto FROM correo_cuentas WHERE id = ?", (cuenta_id,)).fetchone()
        if not f or not f[0]:
            return ""
        try:
            return _desproteger(bytes(f[0]))
        except Exception:
            return ""

    def eliminar_cuenta(self, cuenta_id: int) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM correo_cuentas WHERE id = ?", (cuenta_id,))
            if not c.execute("SELECT 1 FROM correo_cuentas WHERE predeterminado = 1").fetchone():
                c.execute("UPDATE correo_cuentas SET predeterminado = 1 WHERE id = (SELECT MIN(id) FROM correo_cuentas)")

    # Plantillas
    def plantillas(self) -> List[Plantilla]:
        with self._conn() as c:
            return [Plantilla(*f) for f in c.execute(
                "SELECT id, nombre, asunto, cuerpo, enlace FROM correo_plantillas ORDER BY id")]

    def guardar_plantilla(self, t: Plantilla) -> Plantilla:
        if not t.nombre.strip():
            raise ValueError("Falta el nombre de la plantilla")
        ahora = self._ahora()
        with self._conn() as c:
            if not t.id:
                cur = c.execute("""INSERT INTO correo_plantillas (nombre, asunto, cuerpo, enlace, creado, modificado)
                                   VALUES (?, ?, ?, ?, ?, ?)""", (t.nombre.strip(), t.asunto, t.cuerpo, t.enlace, ahora, ahora))
                t.id = cur.lastrowid
            else:
                c.execute("""UPDATE correo_plantillas SET nombre = ?, asunto = ?, cuerpo = ?, enlace = ?, modificado = ?
                             WHERE id = ?""", (t.nombre.strip(), t.asunto, t.cuerpo, t.enlace, ahora, t.id))
        return t

    def recordar_enlace(self, plantilla_id: int, enlace: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE correo_plantillas SET enlace = ? WHERE id = ?", (enlace.strip(), plantilla_id))

    def eliminar_plantilla(self, plantilla_id: int) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM correo_plantillas WHERE id = ?", (plantilla_id,))

    # Historial
    def registrar(self, cuenta: str, para: List[str], asunto: str, resultado: str, empresa: str = "",
                  usuario: str = "") -> None:
        with self._conn() as c:
            c.execute("""INSERT INTO correo_enviados (fecha, cuenta, para, asunto, empresa, usuario, resultado)
                         VALUES (?, ?, ?, ?, ?, ?, ?)""",
                      (self._ahora(), cuenta, ", ".join(para), asunto, empresa, usuario, resultado))

    def enviados(self, limite: int = 5000) -> List[Enviado]:
        with self._conn() as c:
            return [Enviado(*f) for f in c.execute(
                """SELECT fecha, cuenta, para, asunto, empresa, usuario, resultado FROM correo_enviados
                   ORDER BY id DESC LIMIT ?""", (limite,))]


def aplicar(cdb: CorreoDB, perfil: Perfil, mensaje: Mensaje, empresa: str = "", usuario: str = "",
            enviar_fn: Callable = None) -> None:
    """Envía con la cuenta y lo deja en el historial (también si falla). ErrorEnvio si falla."""
    enviar_fn = enviar_fn or enviar
    try:
        enviar_fn(perfil, cdb.secreto(perfil.id), mensaje)
    except ErrorEnvio as e:
        cdb.registrar(perfil.nombre, mensaje.para, mensaje.asunto, f"Error: {e}", empresa, usuario)
        raise
    cdb.registrar(perfil.nombre, mensaje.para, mensaje.asunto, "Enviado", empresa, usuario)
