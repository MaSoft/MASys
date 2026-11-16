# -*- coding: utf-8 -*-
"""Lectura de archivos PST y OST SIN Outlook con libpff (pypff, LGPL-3.0-or-later). Solo lectura: el archivo nunca se modifica.

- Árbol de carpetas con el número de correos, lista de cada carpeta, cuerpo (HTML, texto o RTF), cabeceras, destinatarios y
  adjuntos (guardar o abrir), exportar a .eml (uno o una carpeta entera) y buscar en todo el archivo.
- Un OST abierto por Outlook está bloqueado: hay que cerrar Outlook (o abrir una copia).
- libpff falla con rutas con «/»: siempre se le pasa la ruta absoluta normalizada.
- Todo el acceso al archivo pasa por un cerrojo: se lee desde hilos de trabajo, pero nunca desde dos a la vez.
"""
import datetime as _dt
import email.policy
import html
import logging
import os
import re
import threading
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.parser import HeaderParser
from email.utils import format_datetime, getaddresses

log = logging.getLogger("msys.correo.pstview")
RISKY = {".exe", ".com", ".bat", ".cmd", ".scr", ".pif", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".ps1", ".msi", ".msp",
         ".hta", ".cpl", ".jar", ".lnk", ".reg", ".docm", ".xlsm", ".pptm", ".iso", ".img"}
RECIPIENT_TYPE = {1: "Para", 2: "CC", 3: "CCO"}


class PstError(Exception):
    pass


def available() -> bool:
    import importlib.util
    return importlib.util.find_spec("pypff") is not None


@dataclass
class FolderNode:
    path: tuple            # índices desde la raíz
    name: str
    count: int
    children: list = field(default_factory=list)

    def total(self) -> int:
        return self.count + sum(c.total() for c in self.children)


@dataclass
class MsgRow:
    index: int
    subject: str
    sender: str
    date: _dt.datetime | None
    attachments: int


@dataclass
class Attachment:
    index: int
    name: str
    size: int

    @property
    def risky(self) -> bool:
        return os.path.splitext(self.name)[1].lower() in RISKY


@dataclass
class Message:
    subject: str
    sender: str
    date: _dt.datetime | None
    to: list[str]
    cc: list[str]
    headers: str
    html: str
    text: str
    attachments: list[Attachment]


def _dec(b, codepage: str = "cp1252") -> str:
    if b is None:
        return ""
    if isinstance(b, str):
        return b
    for enc in ("utf-8", codepage):
        try:
            return b.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return b.decode("latin-1", "replace")


def _html_charset(b: bytes) -> str | None:
    m = re.search(rb'charset=["\']?([A-Za-z0-9_-]+)', b[:3000] if b else b"")
    return m.group(1).decode("ascii") if m else None


def rtf_to_text(rtf: str) -> str:
    """RTF a texto plano (lo justo para leerlo: quita grupos de control, \\par = salto, \\'xx = carácter)."""
    out, depth, skip = [], 0, []
    i = 0
    n = len(rtf)
    while i < n:
        ch = rtf[i]
        if ch == "{":
            depth += 1
            i += 1
            continue
        if ch == "}":
            if skip and skip[-1] == depth:
                skip.pop()
            depth -= 1
            i += 1
            continue
        if ch == "\\":
            m = re.match(r"\\([a-z]+)(-?\d+)? ?|\\'([0-9a-f]{2})|\\(.)", rtf[i:], re.I)
            if not m:
                i += 1
                continue
            word, hexv, sym = m.group(1), m.group(3), m.group(4)
            if word in ("fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "header", "footer", "themedata"):
                skip.append(depth)
            elif word in ("par", "line", "row"):
                out.append("\n")
            elif word == "tab":
                out.append("\t")
            elif hexv and not skip:
                out.append(bytes([int(hexv, 16)]).decode("cp1252", "replace"))
            elif sym and sym in "\\{}" and not skip:
                out.append(sym)
            elif sym == "*":
                skip.append(depth)
            i += len(m.group(0))
            continue
        if not skip and ch not in "\r\n":
            out.append(ch)
        i += 1
    return re.sub(r"\n{3,}", "\n\n", "".join(out)).strip()


class PstFile:
    def __init__(self, path: str):
        try:
            import pypff
        except ImportError as e:
            raise PstError("Falta la librería libpff-python (pip install libpff-python)") from e
        self.path = os.path.normpath(os.path.abspath(path))
        self.lock = threading.RLock()
        self.f = pypff.file()
        try:
            self.f.open(self.path)
        except OSError as e:
            msg = str(e)
            if "used by another process" in msg or "otro proceso" in msg or "being used" in msg:
                raise PstError("El archivo está abierto en Outlook: ciérralo o abre una copia del archivo") from e
            raise PstError(f"No se pudo abrir: {msg.split('.')[0]}") from e
        try:
            self.codepage = f"cp{self.f.ascii_codepage}" if str(self.f.ascii_codepage).isdigit() else "cp1252"
        except Exception:  # noqa: BLE001
            self.codepage = "cp1252"

    def close(self):
        with self.lock:
            try:
                self.f.close()
            except Exception:  # noqa: BLE001
                pass

    @property
    def kind(self) -> str:
        return "OST" if self.path.lower().endswith(".ost") else "PST"

    # ------------------------------------------------------------------ carpetas
    def _folder(self, path: tuple):
        f = self.f.get_root_folder()
        for i in path:
            f = f.get_sub_folder(i)
        return f

    def tree(self) -> FolderNode:
        with self.lock:
            def walk(folder, path):
                node = FolderNode(path, folder.name or "(raíz)", folder.number_of_sub_messages)
                for i in range(folder.number_of_sub_folders):
                    try:
                        node.children.append(walk(folder.get_sub_folder(i), path + (i,)))
                    except Exception as e:  # noqa: BLE001 - carpeta dañada: se salta
                        log.info("carpeta %s/%d: %s", path, i, e)
                return node
            return walk(self.f.get_root_folder(), ())

    def messages(self, path: tuple, cancelled=lambda: False) -> list[MsgRow]:
        with self.lock:
            folder = self._folder(path)
            out = []
            for i in range(folder.number_of_sub_messages):
                if cancelled():
                    break
                try:
                    m = folder.get_sub_message(i)
                    out.append(MsgRow(i, m.subject or "", m.sender_name or "", _when(m), m.number_of_attachments))
                except Exception as e:  # noqa: BLE001 - mensaje dañado
                    out.append(MsgRow(i, f"(no se puede leer: {e})", "", None, 0))
            return out

    # ------------------------------------------------------------------ mensaje
    def _msg(self, path: tuple, index: int):
        return self._folder(path).get_sub_message(index)

    def message(self, path: tuple, index: int) -> Message:
        with self.lock:
            m = self._msg(path, index)
            headers = _dec(m.transport_headers, self.codepage)
            h = HeaderParser(policy=email.policy.default).parsestr(headers) if headers else None    # decodifica =?utf-8?…
            to, cc = [], []
            if h is not None and (h.get("To") or h.get("Cc")):
                to = [_fmt(n, a) for n, a in getaddresses([str(x) for x in h.get_all("To", [])])]
                cc = [_fmt(n, a) for n, a in getaddresses([str(x) for x in h.get_all("Cc", [])])]
            else:
                for kind, name, addr in _recipients(m):
                    (cc if kind == 2 else to).append(_fmt(name, addr) + (" (CCO)" if kind == 3 else ""))
            hb = m.html_body
            html_s = _dec(hb, _html_charset(hb) or self.codepage) if hb else ""
            text = _dec(m.plain_text_body, self.codepage)
            if not html_s and not text and m.rtf_body:
                text = rtf_to_text(_dec(m.rtf_body, self.codepage))
            atts = []
            for i in range(m.number_of_attachments):
                try:
                    a = m.get_attachment(i)
                    atts.append(Attachment(i, a.long_filename or f"adjunto_{i + 1}", a.size or 0))
                except Exception as e:  # noqa: BLE001
                    atts.append(Attachment(i, f"(adjunto {i + 1} ilegible: {e})", 0))
            return Message(m.subject or "", m.sender_name or "", _when(m), to, cc, headers, html_s, text, atts)

    def attachment_bytes(self, path: tuple, index: int, att: int) -> bytes:
        with self.lock:
            a = self._msg(path, index).get_attachment(att)
            size = a.size or 0
            a.seek_offset(0)
            return a.read_buffer(size) if size else b""

    def save_attachment(self, path: tuple, index: int, att: Attachment, folder: str) -> str:
        data = self.attachment_bytes(path, index, att.index)
        dest = unique_path(os.path.join(folder, safe_file(att.name)))
        with open(dest, "wb") as f:
            f.write(data)
        return dest

    def to_eml(self, path: tuple, index: int) -> bytes:
        msg = self.message(path, index)
        e = EmailMessage(policy=email.policy.SMTP)
        h = HeaderParser().parsestr(msg.headers) if msg.headers else None
        keep = ("From", "To", "Cc", "Date", "Message-ID", "In-Reply-To", "References", "Reply-To")
        for k in keep:
            v = h.get(k) if h is not None else None
            if v:
                try:
                    e[k] = str(v)
                except (ValueError, TypeError):
                    pass
        if "From" not in e and msg.sender:
            e["From"] = msg.sender
        if "To" not in e and msg.to:
            e["To"] = ", ".join(msg.to)
        if "Cc" not in e and msg.cc:
            e["Cc"] = ", ".join(msg.cc)
        if "Date" not in e and msg.date:
            e["Date"] = format_datetime(msg.date.replace(tzinfo=_dt.timezone.utc) if msg.date.tzinfo is None else msg.date)
        e["Subject"] = msg.subject
        if msg.text:
            e.set_content(msg.text)
            if msg.html:
                e.add_alternative(msg.html, subtype="html")
        elif msg.html:
            e.set_content(msg.html, subtype="html")
        else:
            e.set_content("")
        for a in msg.attachments:
            try:
                data = self.attachment_bytes(path, index, a.index)
            except Exception as ex:  # noqa: BLE001 - adjunto ilegible: se sigue sin él
                log.info("adjunto %s: %s", a.name, ex)
                continue
            e.add_attachment(data, maintype="application", subtype="octet-stream", filename=a.name)
        return e.as_bytes()

    def export_folder(self, path: tuple, dest: str, progress=None, cancelled=lambda: False) -> tuple[int, list[str]]:
        rows = self.messages(path)
        os.makedirs(dest, exist_ok=True)
        ok, errors = 0, []
        for k, r in enumerate(rows):
            if cancelled():
                break
            if progress:
                progress(k, len(rows), r.subject[:60])
            name = (r.date.strftime("%Y%m%d_%H%M%S_") if r.date else "") + (safe_file(r.subject)[:80] or "sin_asunto")
            try:
                with open(unique_path(os.path.join(dest, name + ".eml")), "wb") as f:
                    f.write(self.to_eml(path, r.index))
                ok += 1
            except Exception as e:  # noqa: BLE001
                errors.append(f"{r.subject[:60]}: {e}")
        return ok, errors

    # ------------------------------------------------------------------ buscar
    def search(self, text: str, in_body: bool = False, progress=None, cancelled=lambda: False, limit: int = 2000) -> list[tuple]:
        """[(ruta_carpeta, nombre_carpeta, MsgRow)] cuyo asunto, remitente (o cuerpo) contiene el texto."""
        q = text.lower().strip()
        out = []
        tree = self.tree()
        folders = []

        def flat(n, names):
            folders.append((n.path, " › ".join(names[1:] + [n.name]) if names else n.name, n.count))
            for c in n.children:
                flat(c, names + [n.name])
        flat(tree, [])
        total = sum(c for _p, _n, c in folders) or 1
        done = 0
        with self.lock:
            for path, fname, count in folders:
                if cancelled() or len(out) >= limit:
                    break
                if not count:
                    continue
                folder = self._folder(path)
                for i in range(count):
                    if cancelled() or len(out) >= limit:
                        break
                    done += 1
                    if progress and done % 50 == 0:
                        progress(done, total, f"Buscando… {done} de {total}")
                    try:
                        m = folder.get_sub_message(i)
                        hay = f"{m.subject or ''} {m.sender_name or ''}".lower()
                        if q not in hay and in_body:
                            hay = _dec(m.plain_text_body, self.codepage).lower() or re.sub(r"<[^>]+>", " ", _dec(m.html_body, self.codepage)).lower()
                        if q in hay:
                            out.append((path, fname, MsgRow(i, m.subject or "", m.sender_name or "", _when(m), m.number_of_attachments)))
                    except Exception:  # noqa: BLE001 - mensaje dañado
                        continue
        return out


def _when(m) -> _dt.datetime | None:
    for attr in ("delivery_time", "client_submit_time", "creation_time"):
        try:
            v = getattr(m, attr)
        except Exception:  # noqa: BLE001
            v = None
        if v:
            return v
    return None


def _fmt(name: str, addr: str) -> str:
    name, addr = (name or "").strip(), (addr or "").strip()
    if name and addr and name.lower() != addr.lower():
        return f"{name} <{addr}>"
    return addr or name


def _recipients(m) -> list[tuple[int, str, str]]:
    """(tipo 1=Para 2=CC 3=CCO, nombre, dirección) desde los registros MAPI del mensaje."""
    out = []
    try:
        r = m.recipients
        if r is None:
            return out
        for k in range(r.number_of_record_sets):
            rs = r.get_record_set(k)
            name = addr = ""
            kind = 1
            for e in range(rs.number_of_entries):
                en = rs.get_entry(e)
                t = en.entry_type
                try:
                    if t == 0x3001:
                        name = en.data_as_string
                    elif t in (0x39FE, 0x3003) and not addr:
                        addr = en.data_as_string
                    elif t == 0x0C15:
                        kind = int.from_bytes(en.data[:4], "little")
                except Exception:  # noqa: BLE001
                    continue
            out.append((kind, name, addr))
    except Exception as e:  # noqa: BLE001
        log.info("destinatarios: %s", e)
    return out


def safe_file(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(" .") or "archivo"


def unique_path(p: str) -> str:
    if not os.path.exists(p):
        return p
    base, ext = os.path.splitext(p)
    n = 2
    while os.path.exists(f"{base} ({n}){ext}"):
        n += 1
    return f"{base} ({n}){ext}"


def html_for_view(msg: Message) -> str:
    """HTML seguro para enseñarlo: sin scripts, sin formularios ni imágenes remotas (no avisa al remitente de que se leyó)."""
    if msg.html:
        h = msg.html
        h = re.sub(r"(?is)<(script|iframe|object|embed|form)\b.*?</\1\s*>", "", h)
        h = re.sub(r"(?is)<(script|iframe|object|embed|link|meta)\b[^>]*>", "", h)
        h, n_img = re.subn(r"(?is)<img\b[^>]*\bsrc\s*=\s*[\"']?\s*https?://[^>]*>", "", h)
        h = re.sub(r'(?i)\s(src|background)\s*=\s*(["\'])\s*https?://[^"\']*\2', r" \1=\2\2", h)
        h = re.sub(r"(?i)\son\w+\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)", "", h)
        if n_img:
            h = (f"<p style='color:#8A909A; font-size:9pt'>{n_img} imagen(es) de Internet sin cargar (así el remitente no sabe "
                 f"que se ha abierto).</p>") + h
        return h
    return f"<pre style='white-space:pre-wrap; font-family:Segoe UI'>{html.escape(msg.text)}</pre>"
