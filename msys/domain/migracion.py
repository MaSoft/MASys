# -*- coding: utf-8 -*-
"""Motor (sin Qt) de la sección 🧳 Inventario de migración.

Lo mismo que LAUNCHER\\SCRIPTS\\MIGRACION\\MIGRACION.PS1 (inventario del equipo
para pasar a uno nuevo) pero más completo y con dos cosas que el script no
tenía:
- Cada elemento dice QUÉ HACER en el equipo nuevo.
- Se guarda como archivo (.msysinv, JSON) y en el equipo nuevo se COMPARA:
  presente / falta / distinto, y los programas que faltan se reinstalan con
  winget a partir de la lista exportada en el equipo viejo.

Solo inventario: no copia datos. Nunca guarda contraseñas (de Wi-Fi,
credenciales o correo solo se apunta el nombre / destino / usuario)."""

import glob
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import winreg
from dataclasses import asdict, dataclass
from typing import Callable, Dict, List, Optional, Tuple

from msys.core.utils import run_powershell_json, run_system_command, startupinfo_oculto
from msys.domain.migracion_ps import PS_INVENTARIO

FORMATO = "inventario_migracion"
VERSION_FORMATO = 1
EXTENSION = ".msysinv"

C_EQUIPO = "💻 Equipo"
C_SEGURIDAD = "🔐 Seguridad y licencias"
C_RED = "🌐 Red"
C_UNIDADES = "💾 Unidades y carpetas compartidas"
C_USUARIOS = "👥 Usuarios y perfiles"
C_CREDENCIALES = "🔑 Credenciales y certificados"
C_CORREO = "📧 Correo"
C_NAVEGADORES = "🌍 Navegadores"
C_NUBE = "☁️ Nube"
C_OFFICE = "📝 Office"
C_PROGRAMAS = "📦 Programas"
C_WINGET = "🔁 Reinstalables con winget"
C_IMPRESORAS = "🖨️ Impresoras"
C_TAREAS = "⏰ Tareas programadas"
C_DISPOSITIVOS = "🔌 Dispositivos y monitores"
C_DATOS = "📁 Datos del usuario"
C_PERSONAL = "🎨 Personalización"
C_ASOC = "🔗 Programas predeterminados"
CATEGORIAS = [C_EQUIPO, C_SEGURIDAD, C_RED, C_UNIDADES, C_USUARIOS, C_CREDENCIALES, C_CORREO, C_NAVEGADORES, C_NUBE,
              C_OFFICE, C_PROGRAMAS, C_WINGET, C_IMPRESORAS, C_TAREAS, C_DISPOSITIVOS, C_DATOS, C_PERSONAL, C_ASOC]
_VIRTUAL = re.compile(r"Hyper-V|Virtual|VPN|TAP-|Wintun|WireGuard|Tailscale|ZeroTier|Loopback|VMware|VirtualBox|Bluetooth|Teredo|WARP", re.I)

PRESENTE, FALTA, DISTINTO, INFORMATIVO = "Presente", "Falta", "Distinto", "—"


@dataclass
class Elemento:
    categoria: str
    elemento: str
    detalle: str
    accion: str          # qué hacer en el equipo nuevo
    clave: str = ""      # para comparar entre equipos ("" = solo informativo)
    importante: bool = False


def _gb(n: float) -> str:
    return f"{n:,.1f} GB".replace(",", "X").replace(".", ",").replace("X", ".")


def _tam(n: int) -> str:
    for u in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.0f} {u}" if u == "bytes" else f"{n:,.1f} {u}".replace(",", "X").replace(".", ",").replace("X", ".")
        n /= 1024
    return str(n)


def _lista(v) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").strip().lower())


# ─────────────────────────────────────────────────────────────────
# Bloque PowerShell -> elementos
# ─────────────────────────────────────────────────────────────────

def _desde_ps(d: dict) -> List[Elemento]:
    out: List[Elemento] = []
    e = d.get("equipo") or {}
    if e:
        out.append(Elemento(C_EQUIPO, "Equipo", f"{e.get('nombre', '')} · {e.get('fabricante', '')} {e.get('modelo', '')} · nº de serie {e.get('serie', '')}",
                            "Apunta el nombre si el equipo nuevo debe llamarse igual (y quita el viejo del dominio antes)"))
        out.append(Elemento(C_EQUIPO, "Windows", f"{e.get('so', '')} {e.get('version', '')} (compilación {e.get('build', '')}, {e.get('arquitectura', '')})",
                            "El equipo nuevo debería tener la misma edición (Pro/Home) o superior"))
        out.append(Elemento(C_EQUIPO, "Hardware", f"{e.get('cpu', '')} · {e.get('ramGB', '')} GB de RAM", "Informativo"))
        if e.get("enDominio"):
            out.append(Elemento(C_EQUIPO, "Dominio", e.get("dominio", ""), f"Unir el equipo nuevo al dominio {e.get('dominio', '')}",
                                clave=f"dominio:{_norm(e.get('dominio', ''))}", importante=True))
        else:
            out.append(Elemento(C_EQUIPO, "Grupo de trabajo", e.get("dominio", ""), "Poner el mismo grupo de trabajo si se comparten carpetas",
                                clave=f"grupo:{_norm(e.get('dominio', ''))}"))
        if e.get("entra"):
            out.append(Elemento(C_EQUIPO, "Microsoft Entra ID", ("unión híbrida" if e.get("entraHibrido") else "unido") +
                                (f" · {e['inquilino']}" if e.get("inquilino") else ""), "Unir el equipo nuevo a Entra ID con la cuenta de la empresa",
                                clave="entra", importante=True))
    lic = d.get("licencia") or {}
    if lic:
        canal = lic.get("canal", "")
        oem = canal.upper().startswith("OEM")
        out.append(Elemento(C_SEGURIDAD, "Licencia de Windows", f"Canal {canal} · {'activada' if lic.get('estado') == 1 else 'NO activada'}",
                            "OEM: va unida a este equipo y NO se puede pasar al nuevo" if oem else
                            "Retail: se puede pasar al nuevo (desactívala antes en este)" if "RETAIL" in canal.upper() else
                            "Licencia por volumen o de empresa: la gestiona la organización", importante=oem))
    for b in _lista(d.get("bitlocker")):
        if str(b.get("estado")) == "On" or b.get("cifrado") not in ("FullyDecrypted", None, ""):
            out.append(Elemento(C_SEGURIDAD, f"BitLocker en {b.get('letra', '')}", f"{b.get('cifrado', '')} · protectores: {b.get('protectores', '')}",
                                "Guarda la CLAVE DE RECUPERACIÓN antes de formatear o sacar el disco", importante=True))
    for r in _lista(d.get("red")):
        fija = str(r.get("dhcp", "")).lower() == "disabled"
        virtual = bool(_VIRTUAL.search(f"{r.get('nombre', '')} {r.get('descripcion', '')}"))
        out.append(Elemento(C_RED, f"Adaptador {r.get('nombre', '')}",
                            f"{'IP FIJA' if fija else 'IP automática (DHCP)'} {r.get('ip', '')} · puerta {r.get('puerta', '') or '—'} · DNS {r.get('dns', '') or '—'}",
                            "Lo crea su programa (VPN o virtualización) al instalarlo" if virtual else
                            "Poner la misma IP fija, puerta de enlace y DNS (y quitársela a este equipo)" if fija else "Nada (DHCP)",
                            importante=fija and not virtual))
    px = d.get("proxy") or {}
    if px.get("activo") or px.get("pac"):
        out.append(Elemento(C_RED, "Proxy", f"{px.get('servidor', '')} {('· PAC ' + px['pac']) if px.get('pac') else ''} · excepciones: {px.get('excepciones', '')}",
                            "Configurar el mismo proxy en Configuración › Red › Proxy", clave="proxy", importante=True))
    for v in _lista(d.get("vpn")) + _lista(d.get("vpnTodos")):
        out.append(Elemento(C_RED, f"VPN «{v.get('nombre', '')}»", f"{v.get('servidor', '')} · {v.get('tipo', '')} · {v.get('autenticacion', '')}",
                            "Crear la misma conexión VPN (Configuración › Red › VPN) o instalar su programa", clave=f"vpn:{_norm(v.get('nombre', ''))}"))
    for u in _lista(d.get("unidadesRed")):
        out.append(Elemento(C_UNIDADES, f"Unidad {u.get('letra', '')}", u.get("ruta", ""),
                            f"Conectar {u.get('ruta', '')} como {u.get('letra', '')} (con «Volver a conectar al iniciar sesión»)",
                            clave=f"unidad:{_norm(u.get('letra', ''))}:{_norm(u.get('ruta', ''))}"))
    for s in _lista(d.get("compartidas")):
        out.append(Elemento(C_UNIDADES, f"Carpeta compartida «{s.get('nombre', '')}»", f"{s.get('ruta', '')} {s.get('descripcion', '')}".strip(),
                            "Copiar la carpeta y volver a compartirla con los mismos permisos (sección 📂 Carpetas Compartidas)",
                            clave=f"compartida:{_norm(s.get('nombre', ''))}"))
    admins = {_norm(a.split("\\")[-1]) for a in _lista(d.get("administradores"))}
    for u in _lista(d.get("usuarios")):
        if not u.get("activo"):
            continue
        es_admin = _norm(u.get("nombre", "")) in admins
        out.append(Elemento(C_USUARIOS, f"Usuario local {u.get('nombre', '')}", f"{'administrador' if es_admin else 'estándar'} · último inicio {u.get('ultimo', '') or '—'}",
                            "Crear el mismo usuario" + (" como administrador" if es_admin else ""), clave=f"usuario:{_norm(u.get('nombre', ''))}"))
    for p in _lista(d.get("perfiles")):
        out.append(Elemento(C_USUARIOS, f"Perfil {p.get('cuenta', '') or p.get('sid', '')}", p.get("ruta", ""),
                            "Los datos de este perfil hay que copiarlos (ver Datos del usuario)"))
    for c in _lista(d.get("certificados")):
        nombre = re.sub(r"^(CN|OU|O)=", "", (c.get("sujeto") or "").split(",")[0]).strip()
        out.append(Elemento(C_CREDENCIALES, f"Certificado {nombre}", f"emisor {(c.get('emisor') or '').split(',')[0].replace('CN=', '')} · caduca {c.get('caduca', '')}"
                            + (" · con clave privada" if c.get("clavePrivada") else ""),
                            ("Exportar a .pfx con contraseña (certmgr.msc › Personal) e importarlo" if c.get("exportable") else
                             "La clave privada NO es exportable: volver a pedir el certificado (p. ej. FNMT) en el equipo nuevo")
                            if c.get("clavePrivada") else "Informativo", clave=f"cert:{c.get('huella', '')}", importante=bool(c.get("clavePrivada"))))
    pred = None
    for i in _lista(d.get("impresoras")):
        nombre = i.get("nombre", "")
        if re.search(r"OneNote|PDF|XPS|Fax|Send To", nombre, re.I) and "Microsoft" in (i.get("driver") or ""):
            continue  # virtuales de Windows/Office: vienen solas
        if i.get("servidor") or nombre.startswith("\\\\"):
            accion = f"Añadir la impresora de red {nombre}"
        elif str(i.get("puerto", "")).upper().startswith("WSD") or "IPP Class" in (i.get("driver") or ""):
            accion = "Añadirla desde Configuración › Impresoras: Windows la encuentra sola en la red"
        elif i.get("ip"):
            accion = f"Instalar el driver «{i.get('driver', '')}» con puerto TCP/IP {i.get('ip')}"
        else:
            accion = f"Instalar el driver «{i.get('driver', '')}» (puerto {i.get('puerto', '')})"
        if i.get("predeterminada"):
            pred = nombre
            accion += " y dejarla como predeterminada"
        out.append(Elemento(C_IMPRESORAS, nombre, f"driver {i.get('driver', '')} · puerto {i.get('puerto', '')}" + (f" ({i['ip']})" if i.get("ip") else "")
                            + (" · compartida" if i.get("compartida") else "") + (" · PREDETERMINADA" if i.get("predeterminada") else ""),
                            accion, clave=f"impresora:{_norm(nombre)}"))
    for t in _lista(d.get("tareas")):
        propia_de_programa = (t.get("autor") or "").lower().startswith(("microsoft", "adobe", "google", "mozilla"))
        if propia_de_programa or re.search(r"update|updater|\{[0-9A-F-]{36}\}|^S-1-5-", t.get("nombre", ""), re.I):
            continue  # tareas que crea cada programa al instalarse
        out.append(Elemento(C_TAREAS, t.get("nombre", ""), f"{t.get('ruta', '')} · {t.get('accion', '')}",
                            "Volver a crear la tarea (o exportarla desde el Programador de tareas)", clave=f"tarea:{_norm(t.get('nombre', ''))}"))
    for m in _lista(d.get("monitores")):
        out.append(Elemento(C_DISPOSITIVOS, f"Monitor {m.get('modelo', '')}", f"{m.get('fabricante', '')} · nº de serie {m.get('serie', '')}", "Informativo"))
    for u in _lista(d.get("usb")):
        out.append(Elemento(C_DISPOSITIVOS, u.get("nombre", ""), f"{u.get('clase', '')} · {u.get('fabricante', '')}",
                            "Comprobar que funciona en el equipo nuevo (puede necesitar su driver)", clave=f"usb:{_norm(u.get('nombre', ''))}"))
    for idi in _lista(d.get("idiomas")):
        out.append(Elemento(C_PERSONAL, f"Idioma {idi.get('nombre', '')}", f"{idi.get('idioma', '')} · teclado {idi.get('teclados', '')}",
                            "Añadir el mismo idioma y teclado", clave=f"idioma:{_norm(idi.get('idioma', ''))}"))
    if d.get("zonaHoraria"):
        out.append(Elemento(C_PERSONAL, "Zona horaria", d["zonaHoraria"], "Poner la misma zona horaria",
                            clave=f"zona:{_norm(str(d['zonaHoraria']).split(' (')[0])}"))
    for v in _lista(d.get("volumenes")):
        out.append(Elemento(C_DATOS, f"Disco {v.get('letra', '')}: {v.get('etiqueta', '')}", f"{v.get('sistema', '')} · {_gb(v.get('totalGB') or 0)} · libres {_gb(v.get('libreGB') or 0)}",
                            "Comprobar que el equipo nuevo tiene sitio para los datos", clave=f"volumen:{_norm(v.get('letra', ''))}"))
    return out


# ─────────────────────────────────────────────────────────────────
# Recolectores en Python
# ─────────────────────────────────────────────────────────────────

def _carpeta_usuario(nombre_valor: str, defecto: str) -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
            return os.path.expandvars(winreg.QueryValueEx(k, nombre_valor)[0])
    except OSError:
        return os.path.join(os.environ.get("USERPROFILE", ""), defecto)


def tamano_carpeta(ruta: str, limite_s: float = 60.0) -> Tuple[int, int, bool]:
    """(bytes, nº de archivos, completo). Se corta a los 'limite_s' segundos para no bloquear."""
    total = n = 0
    fin = time.time() + limite_s
    pila = [ruta]
    while pila:
        if time.time() > fin:
            return total, n, False
        actual = pila.pop()
        try:
            with os.scandir(actual) as it:
                for e in it:
                    try:
                        if e.is_symlink():
                            continue
                        if e.is_dir(follow_symlinks=False):
                            pila.append(e.path)
                        elif e.is_file(follow_symlinks=False):
                            total += e.stat(follow_symlinks=False).st_size
                            n += 1
                    except OSError:
                        continue
        except OSError:
            continue
    return total, n, True


def _datos_usuario() -> List[Elemento]:
    out = []
    carpetas = [("Escritorio", "Desktop", "Desktop"), ("Documentos", "Personal", "Documents"), ("Imágenes", "My Pictures", "Pictures"),
                ("Vídeos", "My Video", "Videos"), ("Música", "My Music", "Music"),
                ("Descargas", "{374DE290-123F-4565-9164-39C4925E467B}", "Downloads"), ("Favoritos", "Favorites", "Favorites")]
    for nombre, valor, defecto in carpetas:
        ruta = _carpeta_usuario(valor, defecto)
        if not os.path.isdir(ruta):
            continue
        b, n, completo = tamano_carpeta(ruta)
        en_nube = "onedrive" in ruta.lower()
        out.append(Elemento(C_DATOS, nombre, f"{ruta} · {_tam(b)}{'' if completo else ' o más'} en {n:,} archivos".replace(",", "."),
                            "Se recupera sola al iniciar OneDrive con la misma cuenta (espera a que termine de sincronizar)" if en_nube
                            else "Copiar al equipo nuevo", importante=not en_nube and b > 0))
    escritorio = _carpeta_usuario("Desktop", "Desktop")
    try:
        for e in sorted(os.scandir(escritorio), key=lambda x: x.name.lower()):
            if e.name.lower() == "desktop.ini":
                continue
            tipo = "carpeta" if e.is_dir() else (os.path.splitext(e.name)[1] or "archivo")
            out.append(Elemento(C_DATOS, f"Escritorio: {e.name}", tipo,
                                "Instalar el programa (acceso directo)" if e.name.lower().endswith(".lnk") else "Copiar",
                                clave=f"escritorio:{_norm(e.name)}"))
    except OSError:
        pass
    excluir = {"windows", "program files", "program files (x86)", "programdata", "perflogs", "$recycle.bin", "system volume information",
               "recovery", "boot", "documents and settings", "msocache", "intel", "amd", "nvidia", "drivers", "users", "onedrivetemp",
               "$windows.~bt", "$windows.~ws", "$winreagent", "inetpub", "xboxgames", "esd"}
    try:
        for e in os.scandir("C:\\"):
            if not e.is_dir() or e.name.lower() in excluir or e.name.startswith("$"):
                continue
            b, n, completo = tamano_carpeta(e.path, 30)
            out.append(Elemento(C_DATOS, f"C:\\{e.name}", f"{_tam(b)}{'' if completo else ' o más'} en {n:,} archivos".replace(",", "."),
                                "Revisar: carpeta propia fuera del perfil (datos de un programa o del usuario) — copiar si hace falta",
                                clave=f"carpetac:{_norm(e.name)}", importante=b > 0))
    except OSError:
        pass
    return out


def _correo() -> List[Elemento]:
    from msys.ps_scripts import PS_GET_EMAIL_ACCOUNTS
    out = []
    for c in _lista(run_powershell_json(PS_GET_EMAIL_ACCOUNTS, timeout=60)):
        cuenta = c.get("Cuenta", "")
        if not cuenta:
            continue
        out.append(Elemento(C_CORREO, cuenta, f"{c.get('Cliente', '')} · {c.get('Tipo', '')} · {c.get('Servidor', '')}",
                            f"Añadir la cuenta en {c.get('Cliente', 'el programa de correo')} (servidor {c.get('Servidor', '')})",
                            clave=f"correo:{_norm(cuenta)}"))
    local = os.environ.get("LOCALAPPDATA", "")
    docs = _carpeta_usuario("Personal", "Documents")
    archivos = glob.glob(os.path.join(local, "Microsoft", "Outlook", "*.[po]st"))
    for sub in ("Archivos de Outlook", "Outlook Files"):
        archivos += glob.glob(os.path.join(docs, sub, "*.pst"))
    for a in sorted(set(archivos)):
        try:
            tam = os.path.getsize(a)
        except OSError:
            continue
        pst = a.lower().endswith(".pst")
        out.append(Elemento(C_CORREO, os.path.basename(a), f"{a} · {_tam(tam)}",
                            "Copiar y abrir en Outlook (Archivo › Abrir y exportar › Abrir archivo de datos)" if pst
                            else "No se copia: Outlook lo vuelve a descargar del servidor", importante=pst))
    firmas = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Signatures")
    n = len(glob.glob(os.path.join(firmas, "*.htm")))
    if n:
        out.append(Elemento(C_CORREO, f"Firmas de Outlook ({n})", firmas, f"Copiar la carpeta a %APPDATA%\\Microsoft\\Signatures",
                            clave="firmas", importante=True))
    tb = os.path.join(os.environ.get("APPDATA", ""), "Thunderbird", "Profiles")
    if os.path.isdir(tb):
        out.append(Elemento(C_CORREO, "Perfil de Thunderbird", tb, "Copiar la carpeta del perfil (con Thunderbird cerrado)", clave="thunderbird"))
    return out


_CHROMIUM = [("Chrome", r"Google\Chrome\User Data"), ("Edge", r"Microsoft\Edge\User Data"), ("Brave", r"BraveSoftware\Brave-Browser\User Data"),
             ("Opera", r"Opera Software\Opera Stable")]


def _contar_favoritos(nodo) -> int:
    """Nº de favoritos (URL) de un archivo Bookmarks de Chrome/Edge/Brave."""
    if not isinstance(nodo, dict):
        return 0
    if nodo.get("type") == "url":
        return 1
    total = sum(_contar_favoritos(h) for h in nodo.get("children", []))
    raices = nodo.get("roots")
    if isinstance(raices, dict):
        total += sum(_contar_favoritos(v) for v in raices.values())
    return total


def _navegadores() -> List[Elemento]:
    out = []
    local = os.environ.get("LOCALAPPDATA", "")
    for nav, sub in _CHROMIUM:
        base = os.path.join(local, sub)
        if not os.path.isdir(base):
            continue
        for perfil in sorted(glob.glob(os.path.join(base, "Default")) + glob.glob(os.path.join(base, "Profile *"))):
            cuenta, nombre, favs, ext = "", os.path.basename(perfil), 0, 0
            try:
                with open(os.path.join(perfil, "Preferences"), encoding="utf-8") as f:
                    prefs = json.load(f)
                nombre = (prefs.get("profile") or {}).get("name", nombre)
                info = prefs.get("account_info") or []
                cuenta = info[0].get("email", "") if info else ""
                ext = len([e for e in ((prefs.get("extensions") or {}).get("settings") or {}).values()
                           if isinstance(e, dict) and e.get("location") == 1])
            except (OSError, ValueError, AttributeError):
                pass
            try:
                with open(os.path.join(perfil, "Bookmarks"), encoding="utf-8") as f:
                    favs = _contar_favoritos(json.load(f))
            except (OSError, ValueError):
                pass
            out.append(Elemento(C_NAVEGADORES, f"{nav} · {nombre}", f"{cuenta or 'sin cuenta'} · {favs} favoritos · {ext} extensiones",
                                f"Iniciar sesión en {nav} con {cuenta}: favoritos y extensiones vuelven solos" if cuenta else
                                f"Sin cuenta: exportar los favoritos (Favoritos › Exportar) e instalar las extensiones a mano",
                                clave=f"nav:{nav.lower()}:{_norm(cuenta or nombre)}", importante=not cuenta and favs > 0))
    ff = os.path.join(os.environ.get("APPDATA", ""), "Mozilla", "Firefox", "Profiles")
    if os.path.isdir(ff):
        for p in sorted(os.listdir(ff)):
            out.append(Elemento(C_NAVEGADORES, f"Firefox · {p}", os.path.join(ff, p),
                                "Iniciar sesión en la cuenta de Firefox o copiar la carpeta del perfil", clave=f"nav:firefox:{_norm(p)}"))
    return out


def _nube() -> List[Elemento]:
    out = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\OneDrive\Accounts") as k:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(k, sub) as a:
                        correo = winreg.QueryValueEx(a, "UserEmail")[0]
                        try:
                            carpeta = winreg.QueryValueEx(a, "UserFolder")[0]
                        except OSError:
                            carpeta = ""
                    out.append(Elemento(C_NUBE, f"OneDrive ({'empresa' if sub.lower().startswith('business') else 'personal'})", f"{correo} · {carpeta}",
                                        f"Iniciar OneDrive con {correo}", clave=f"nube:onedrive:{_norm(correo)}"))
                except OSError:
                    continue
    except OSError:
        pass
    gd = os.path.join(os.environ.get("APPDATA", ""), "Google", "DriveFS")
    if os.path.isdir(gd):
        for n in os.listdir(gd):
            if re.match(r"\S+@\S+\.\S+", n):
                out.append(Elemento(C_NUBE, "Google Drive", n, f"Instalar Google Drive e iniciar sesión con {n}", clave=f"nube:gdrive:{_norm(n)}"))
    info = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Dropbox", "info.json")
    try:
        with open(info, encoding="utf-8") as f:
            dbx = json.load(f)
        for tipo in ("personal", "business"):
            if (dbx.get(tipo) or {}).get("path"):
                out.append(Elemento(C_NUBE, f"Dropbox ({tipo})", dbx[tipo]["path"], "Instalar Dropbox e iniciar sesión", clave=f"nube:dropbox:{tipo}"))
    except (OSError, ValueError):
        pass
    if os.path.isdir(os.path.join(os.environ.get("USERPROFILE", ""), "iCloudDrive")):
        out.append(Elemento(C_NUBE, "iCloud Drive", "", "Instalar iCloud para Windows e iniciar sesión con el ID de Apple", clave="nube:icloud"))
    return out


def _office() -> List[Elemento]:
    out = []
    producto = ""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Office\ClickToRun\Configuration") as k:
            ids = winreg.QueryValueEx(k, "ProductReleaseIds")[0]
            producto = "Microsoft 365" if re.search(r"O365|Microsoft365", ids) else f"Office ({ids})"
    except OSError:
        for ver in ("16.0", "15.0"):
            for vista in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\Office\{ver}\Common\InstallRoot", 0, winreg.KEY_READ | vista):
                        producto = producto or f"Office {'2016/2019' if ver == '16.0' else '2013'} (MSI)"
                except OSError:
                    continue
    if not producto:
        return out
    cuenta = ""
    oneauth = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "OneAuth", "accounts")
    conectada = ""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Office\16.0\Common\Identity") as k:
            conectada = winreg.QueryValueEx(k, "ConnectedOneAuthAccountId")[0]
    except OSError:
        pass
    candidatos = [os.path.join(oneauth, conectada)] if conectada else []
    candidatos += glob.glob(os.path.join(oneauth, "*"))
    for f in candidatos:
        try:
            with open(f, encoding="utf-8") as h:
                j = json.load(h)
            correo = j.get("preferred_username") or ""
            if not correo and j.get("additional_properties"):
                extra = json.loads(j["additional_properties"]) if isinstance(j["additional_properties"], str) else j["additional_properties"]
                correo = extra.get("preferred_username") or extra.get("email") or ""
            if re.match(r"\S+@\S+\.\S+", correo):
                cuenta = correo
                break
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    msi = "MSI" in producto
    out.append(Elemento(C_OFFICE, producto, f"Cuenta: {cuenta or 'no encontrada'}",
                        "Necesitas el instalador y la clave de producto" if msi else
                        f"Instalar Office e iniciar sesión con {cuenta}" if cuenta else "Instalar Office e iniciar sesión con la cuenta de la licencia",
                        clave="office", importante=True))
    return out


def programas_instalados() -> List[Tuple[str, str, str]]:
    """[(nombre, versión, editor)] del registro, sin componentes ni actualizaciones."""
    vistos, out = set(), []
    for raiz, ruta, vista in ((winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_64KEY),
                              (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall", winreg.KEY_WOW64_32KEY),
                              (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall", 0)):
        try:
            k = winreg.OpenKey(raiz, ruta, 0, winreg.KEY_READ | vista)
        except OSError:
            continue
        with k:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(k, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(k, sub) as s:
                        def v(n):
                            try:
                                return str(winreg.QueryValueEx(s, n)[0])
                            except OSError:
                                return ""
                        nombre = v("DisplayName")
                        if not nombre or v("SystemComponent") == "1" or v("ParentKeyName") or re.match(r"^(KB\d+|Security Update|Update for)", nombre):
                            continue
                        clave = _norm(nombre)
                        if clave in vistos:
                            continue
                        vistos.add(clave)
                        out.append((nombre, v("DisplayVersion"), v("Publisher")))
                except OSError:
                    continue
    return sorted(out, key=lambda x: x[0].lower())


def exportar_winget() -> Tuple[Optional[dict], str]:
    """(JSON de 'winget export', mensaje). Solo trae los programas que winget sabe reinstalar."""
    exe = shutil.which("winget")
    if not exe:
        return None, "winget no está instalado en este equipo (Instalador de aplicación de la Tienda)"
    ruta = os.path.join(tempfile.gettempdir(), f"msys_winget_{os.getpid()}.json")
    try:
        r = subprocess.run([exe, "export", "-o", ruta, "--accept-source-agreements", "--disable-interactivity"], capture_output=True,
                           timeout=180, startupinfo=startupinfo_oculto(), creationflags=subprocess.CREATE_NO_WINDOW)
        if not os.path.exists(ruta):
            return None, (r.stdout or b"").decode("utf-8", errors="replace").strip()[-200:] or f"winget devolvió {r.returncode}"
        with open(ruta, encoding="utf-8") as f:
            return json.load(f), ""
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        return None, str(e)
    finally:
        try:
            os.remove(ruta)
        except OSError:
            pass


def ids_winget(export: Optional[dict]) -> List[str]:
    out = []
    for fuente in (export or {}).get("Sources", []):
        out += [p.get("PackageIdentifier", "") for p in fuente.get("Packages", []) if p.get("PackageIdentifier")]
    return sorted(set(out), key=str.lower)


def _programas(export: Optional[dict], aviso_winget: str) -> List[Elemento]:
    out = [Elemento(C_PROGRAMAS, n, f"versión {v or '—'} · {e or '—'}", "Instalar (con su licencia si es de pago)", clave=f"prog:{_norm(n)}")
           for n, v, e in programas_instalados()]
    for pid in ids_winget(export):
        out.append(Elemento(C_WINGET, pid, "winget lo sabe instalar", "Se reinstala desde aquí con «Reinstalar con winget»",
                            clave=f"winget:{pid.lower()}"))
    if aviso_winget:
        out.append(Elemento(C_WINGET, "winget", aviso_winget, "Instalar los programas a mano"))
    return out


def _personalizacion() -> List[Elemento]:
    out = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Desktop") as k:
            fondo = winreg.QueryValueEx(k, "WallPaper")[0]
        if fondo:
            out.append(Elemento(C_PERSONAL, "Fondo de escritorio", fondo, "Copiar la imagen y ponerla de fondo"))
    except OSError:
        pass
    fuentes_dir = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts")
    for f in sorted(glob.glob(os.path.join(fuentes_dir, "*"))):
        out.append(Elemento(C_PERSONAL, f"Fuente {os.path.basename(f)}", "instalada solo para este usuario",
                            "Copiar e instalar la fuente", clave=f"fuente:{_norm(os.path.basename(f))}"))
    try:
        from msys.domain import asociaciones
        for a in asociaciones.listar_extensiones():
            if a.origen == asociaciones.ORIGEN_USUARIO and a.estado == asociaciones.ESTADO_OK:
                out.append(Elemento(C_ASOC, f"Abrir {a.clave} con", a.programa,
                                    f"Elegir {a.programa} para {a.clave} (🔗 Extensiones y programas › Abrir con…)",
                                    clave=f"asoc:{a.clave}"))
    except Exception:
        pass
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as k:
            i = 0
            while True:
                try:
                    n, v, _t = winreg.EnumValue(k, i)
                except OSError:
                    break
                i += 1
                if n.upper() in ("TEMP", "TMP", "ONEDRIVE", "ONEDRIVECONSUMER", "ONEDRIVECOMMERCIAL"):
                    continue
                out.append(Elemento(C_PERSONAL, f"Variable {n}", str(v)[:300], "Crear la misma variable de entorno del usuario",
                                    clave=f"env:{n.lower()}"))
    except OSError:
        pass
    try:
        hosts = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "drivers", "etc", "hosts")
        with open(hosts, encoding="utf-8", errors="replace") as f:
            for linea in f:
                linea = linea.strip()
                if linea and not linea.startswith("#"):
                    out.append(Elemento(C_RED, f"hosts: {linea.split()[-1]}", linea, "Añadir la misma línea al archivo hosts",
                                        clave=f"hosts:{_norm(linea)}"))
    except OSError:
        pass
    return out


def _wifi_y_credenciales() -> List[Elemento]:
    out = []
    ok, salida = run_system_command(["netsh", "wlan", "show", "profiles"], timeout=20, intentar_utf8=True)
    if ok:
        for m in re.finditer(r"(?:Perfil de todos los usuarios|All User Profile)\s*:\s*(.+)", salida):
            ssid = m.group(1).strip()
            out.append(Elemento(C_RED, f"Wi-Fi «{ssid}»", "red guardada (la contraseña no se apunta)",
                                "Conectarse a la red (la contraseña se ve en la sección 📶 WiFi de este equipo)", clave=f"wifi:{_norm(ssid)}"))
    ok, salida = run_system_command(["cmdkey", "/list"], timeout=20)
    if ok:
        bloques = re.split(r"\r?\n\s*\r?\n", salida)
        ruido = re.compile(r"MicrosoftAccount|virtualapp|OneDrive|MicrosoftOffice|Xbl|WindowsLive|msteams|SSO_POP|Adobe|"
                           r"Microsoft_OC|MSIX|AzureAD|LegacyGeneric:target=Microsoft", re.I)
        for b in bloques:
            dest = re.search(r"(?:Destino|Target)\s*:\s*(.+)", b)
            if not dest:
                continue
            destino = re.sub(r"^(Domain|LegacyGeneric):target=", "", dest.group(1).strip())
            if ruido.search(destino):
                continue
            usuario = re.search(r"(?:Usuario|User)\s*:\s*(.+)", b)
            out.append(Elemento(C_CREDENCIALES, f"Credencial {destino}", f"usuario {usuario.group(1).strip() if usuario else '—'} (sin contraseña)",
                                "Se vuelve a pedir al conectar; guardarla con «Recordar mis credenciales»", clave=f"cred:{_norm(destino)}"))
    return out


# ─────────────────────────────────────────────────────────────────
# Inventario completo, archivo y comparación
# ─────────────────────────────────────────────────────────────────

def hacer_inventario(progreso: Optional[Callable[[int, str], None]] = None, con_winget: bool = True) -> dict:
    def p(v, t):
        if progreso:
            progreso(v, t)
    elementos: List[Elemento] = []
    p(5, "Equipo, red, discos, impresoras, tareas y dispositivos (unos 30 s)...")
    elementos += _desde_ps(run_powershell_json(PS_INVENTARIO, timeout=300) or {})
    p(45, "Wi-Fi y credenciales...")
    elementos += _wifi_y_credenciales()
    p(50, "Correo, firmas y archivos de Outlook...")
    elementos += _correo()
    p(55, "Navegadores y nube...")
    elementos += _navegadores() + _nube()
    p(60, "Office...")
    elementos += _office()
    export, aviso = (None, "")
    if con_winget:
        p(62, "Programas que winget sabe reinstalar (hasta 2-3 minutos)...")
        export, aviso = exportar_winget()
    p(75, "Programas instalados...")
    elementos += _programas(export, aviso)
    p(80, "Personalización...")
    elementos += _personalizacion()
    p(85, "Tamaño de las carpetas del usuario (puede tardar)...")
    elementos += _datos_usuario()
    orden = {c: i for i, c in enumerate(CATEGORIAS)}
    elementos.sort(key=lambda e: orden.get(e.categoria, 99))
    p(100, "Hecho")
    return {"app": "MASys", "formato": FORMATO, "version": VERSION_FORMATO, "equipo": os.environ.get("COMPUTERNAME", ""),
            "usuario": os.environ.get("USERNAME", ""), "fecha": time.strftime("%d/%m/%Y %H:%M"),
            "elementos": [asdict(e) for e in elementos], "winget_export": export}


def elementos_de(inventario: dict) -> List[Elemento]:
    return [Elemento(**{k: e.get(k, "" if k != "importante" else False) for k in Elemento.__dataclass_fields__})
            for e in inventario.get("elementos", [])]


def guardar(inventario: dict, ruta: str):
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(inventario, f, ensure_ascii=False, indent=1)


def cargar(ruta: str) -> dict:
    with open(ruta, encoding="utf-8") as f:
        d = json.load(f)
    if d.get("formato") != FORMATO or not isinstance(d.get("elementos"), list):
        raise ValueError("No es un inventario de migración de MASys")
    return d


def comparar(origen: dict, destino: dict) -> List[Tuple[Elemento, str, str]]:
    """[(elemento del ORIGEN, estado, detalle en el destino)]. Las asociaciones y
    zonas se marcan «Distinto» si están pero con otro valor."""
    en_destino: Dict[str, Elemento] = {e.clave: e for e in elementos_de(destino) if e.clave}
    out = []
    for e in elementos_de(origen):
        if not e.clave:
            out.append((e, INFORMATIVO, ""))
            continue
        d = en_destino.get(e.clave)
        if d is None:
            out.append((e, FALTA, ""))
        elif e.clave.startswith(("asoc:",)) and _norm(d.detalle) != _norm(e.detalle):
            out.append((e, DISTINTO, d.detalle))
        else:
            out.append((e, PRESENTE, d.detalle))
    return out


def import_winget(origen: dict, ids: List[str], ruta: str) -> int:
    """Archivo para 'winget import' con solo los programas elegidos (manteniendo
    la fuente de cada uno tal como salió del equipo viejo)."""
    export = origen.get("winget_export") or {}
    elegidos = {i.lower() for i in ids}
    fuentes, n = [], 0
    for f in export.get("Sources", []):
        paquetes = [p for p in f.get("Packages", []) if p.get("PackageIdentifier", "").lower() in elegidos]
        if paquetes:
            fuentes.append(dict(f, Packages=paquetes))
            n += len(paquetes)
    with open(ruta, "w", encoding="utf-8") as fh:
        json.dump(dict(export, Sources=fuentes), fh, ensure_ascii=False, indent=1)
    return n


def lanzar_import_winget(ruta: str) -> Tuple[bool, str]:
    """Abre una consola VISIBLE con 'winget import' (para ver el avance y las preguntas)."""
    exe = shutil.which("winget")
    if not exe:
        return False, "winget no está instalado en este equipo"
    try:
        subprocess.Popen(["cmd", "/k", exe, "import", "-i", ruta, "--accept-package-agreements", "--accept-source-agreements",
                          "--ignore-unavailable"], creationflags=subprocess.CREATE_NEW_CONSOLE)
        return True, ""
    except OSError as e:
        return False, str(e)


# Avisos de cada apartado del informe, los mismos que pone MIGRACION.PS1 (nivel, texto)
_AVISOS_INFORME = {
    C_UNIDADES: ("warning", "<strong>IMPORTANTE:</strong> Volver a mapear estas unidades en el nuevo equipo con las mismas letras de unidad y rutas de red."),
    C_CREDENCIALES: ("warning", "<strong>IMPORTANTE:</strong> Las contraseñas de estas credenciales NO se exportan. Deben volver a escribirse en el nuevo equipo."),
    C_CORREO: ("warning", "<strong>IMPORTANTE:</strong> Las contraseñas NO se recopilan por seguridad. Deben documentarse manualmente con el usuario."),
    C_NAVEGADORES: ("warning", "<strong>RECOMENDACIÓN:</strong> Iniciar sesión con las mismas cuentas en el nuevo equipo para sincronizar favoritos, extensiones y configuración automáticamente."),
    C_NUBE: ("warning", "<strong>IMPORTANTE:</strong> Configurar estos servicios de nube en el nuevo equipo con las mismas cuentas para sincronizar archivos."),
    C_OFFICE: ("warning", "<strong>IMPORTANTE:</strong> Iniciar sesión con esta cuenta en Office del nuevo equipo para activar la licencia."),
    C_TAREAS: ("warning", "<strong>IMPORTANTE:</strong> Revisar y recrear estas tareas programadas en el nuevo equipo."),
    C_DATOS: ("warning", "<strong>IMPORTANTE:</strong> Revisar estas carpetas con el usuario y determinar cuáles deben migrarse al nuevo equipo. "
                         "Limpiar antes lo innecesario para acortar la migración."),
    C_WINGET: ("warning", "<strong>CONSEJO:</strong> En el equipo nuevo, MASys › Inventario de migración › «Reinstalar con winget» los instala de una vez."),
}


def informe_html(inventario: dict, comparacion: Optional[List[Tuple[Elemento, str, str]]] = None, destino: str = "") -> str:
    """Informe HTML con el MISMO diseño y comportamiento que el de MIGRACION.PS1 (petición del
    usuario): cabecera «INFORME DE MIGRACIÓN - equipo» con la fecha, buscador, «+ Expandir todo» /
    «- Contraer todo», un apartado plegable por categoría (abierto solo el primero), tablas con
    cabecera azul, insignias verde/roja y avisos bajo cada apartado. Lleva además lo propio de
    MASys: qué hacer en el equipo nuevo en cada elemento y, al comparar, cómo está en el nuevo."""
    e = html.escape
    filas = comparacion if comparacion is not None else [(x, "", "") for x in elementos_de(inventario)]
    por_cat: Dict[str, list] = {}
    for el, est, det in filas:
        por_cat.setdefault(el.categoria, []).append((el, est, det))
    insignia = {PRESENTE: "badge-ok", FALTA: "badge-no", DISTINTO: "badge-warning"}
    bloques = []
    if comparacion is not None:
        n = {k: sum(1 for _e, s, _d in comparacion if s == k) for k in (PRESENTE, FALTA, DISTINTO)}
        nivel = "alert-danger" if n[FALTA] else "alert-warning" if n[DISTINTO] else "alert-ok"
        bloques.append(f'<div class="alert {nivel}"><strong>COMPARADO CON {e(destino.upper())}:</strong> '
                       f'<span class="badge-ok">{n[PRESENTE]} presentes</span> <span class="badge-no">{n[FALTA]} faltan</span> '
                       f'<span class="badge-warning">{n[DISTINTO]} distintos</span></div>')
    for cat in [c for c in CATEGORIAS if c in por_cat] + [c for c in por_cat if c not in CATEGORIAS]:
        lista = por_cat[cat]
        cab_estado = "<th>En el equipo nuevo</th>" if comparacion is not None else ""
        cuerpo = []
        for el, est, det in lista:
            nombre = f"<strong>{e(el.elemento)}</strong>" + (' <span class="badge-no">IMPORTANTE</span>' if el.importante else "")
            estado = ""
            if comparacion is not None:
                estado = f'<td><span class="{insignia.get(est, "badge-gris")}">{e(est)}</span>' + \
                         (f"<br><small>{e(det)}</small>" if est == DISTINTO and det else "") + "</td>"
            cuerpo.append(f"<tr><td>{nombre}</td><td>{e(el.detalle)}</td><td>{e(el.accion)}</td>{estado}</tr>")
        tabla = (f"<table><tr><th>Elemento</th><th>Detalle</th><th>Qué hacer en el equipo nuevo</th>{cab_estado}</tr>"
                 + "".join(cuerpo) + "</table>")
        avisos = []
        if cat == C_EQUIPO:
            ram = next((re.search(r"([\d.,]+)\s*GB de RAM", x.detalle) for x, _s, _d in lista if x.elemento == "Hardware"), None)
            try:
                if ram and float(ram.group(1).replace(",", ".")) < 4:
                    avisos.append(("warning", "ADVERTENCIA: RAM insuficiente. Se recomienda ampliar en el nuevo equipo."))
            except ValueError:
                pass
        if cat == C_CREDENCIALES:
            con_clave = sum(1 for x, _s, _d in lista if "con clave privada" in x.detalle)
            if con_clave:
                avisos.append(("danger", f"<strong>CRÍTICO:</strong> Exportar {con_clave} certificado(s) con clave privada en formato PFX con "
                                         "contraseña antes de migrar. Usar certmgr.msc"))
        if cat in _AVISOS_INFORME:
            avisos.append(_AVISOS_INFORME[cat])
        importantes = sum(1 for x, _s, _d in lista if x.importante)
        if importantes and cat not in (C_CREDENCIALES,):
            avisos.append(("danger", f"<strong>ATENCIÓN:</strong> {importantes} elemento(s) marcados como IMPORTANTE en este apartado."))
        avisos_html = "".join(f'<div class="alert alert-{n}">{t}</div>' for n, t in avisos)
        titulo = re.sub(r"^\W+\s*", "", cat).upper() if cat else "OTROS"
        bloques.append(f"<h2>{e(titulo)} ({len(lista)})</h2>{tabla}{avisos_html}")

    equipo = inventario.get("equipo", "")
    fecha = inventario.get("fecha", "")
    # «04/10/2026 09:30» -> «04/10/2026 - 09:30», como MIGRACION.PS1
    fecha = re.sub(r"^(\d{2}/\d{2}/\d{4}) (\d{1,2}:\d{2})$", r"\1 - \2", str(fecha))
    usuario = inventario.get("usuario", "")
    return f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Informe Migración - {e(equipo)}</title>
<style>
body{{font-family:Arial,sans-serif;margin:20px;background:#f5f5f5}}
.container{{max-width:1200px;margin:0 auto;background:white;padding:30px;box-shadow:0 2px 10px rgba(0,0,0,0.1)}}
.header{{display:flex;justify-content:space-between;align-items:center;border-bottom:3px solid #3498db;padding-bottom:10px;margin-bottom:20px;gap:12px;flex-wrap:wrap}}
.header h1{{color:#2c3e50;margin:0;font-size:28px}}
.header .fecha{{color:#7f8c8d;font-size:14px;font-weight:normal}}
.subtitulo{{color:#7f8c8d;font-size:13px;margin:-10px 0 10px}}
.search-box{{margin:20px 0;padding:15px;background:#ecf0f1;border-radius:5px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}}
.search-box input{{flex:1;min-width:200px;padding:10px;border:2px solid #3498db;border-radius:5px;font-size:14px}}
.btn-toggle{{padding:10px 15px;background:#3498db;color:white;border:none;border-radius:5px;cursor:pointer;font-size:13px;font-weight:bold}}
.btn-toggle:hover{{background:#2980b9}}
h2{{color:#34495e;margin-top:25px;background:#ecf0f1;padding:10px;border-left:4px solid #3498db;cursor:pointer;user-select:none;position:relative}}
h2:hover{{background:#d5dbdb}}
h2::after{{content:'\\25BC';position:absolute;right:15px;transition:transform 0.3s}}
h2.collapsed::after{{content:'\\25B6';transform:rotate(0deg)}}
.section-content{{overflow:hidden;transition:max-height 0.3s ease-out}}
.section-content.hidden{{max-height:0;margin:0;padding:0}}
h3{{color:#34495e;margin-top:15px;font-size:16px}}
table{{width:100%;border-collapse:collapse;margin:15px 0}}
th{{background:#3498db;color:white;padding:12px;text-align:left}}
td{{padding:10px;border-bottom:1px solid #ddd;vertical-align:top}}
tr:hover{{background:#f8f9fa}}
.badge-ok{{background:#27ae60;color:white;padding:4px 8px;border-radius:3px;font-size:0.9em;white-space:nowrap}}
.badge-no{{background:#e74c3c;color:white;padding:4px 8px;border-radius:3px;font-size:0.9em;white-space:nowrap}}
.badge-warning{{background:#f39c12;color:white;padding:4px 8px;border-radius:3px;font-size:0.9em;white-space:nowrap}}
.badge-gris{{background:#95a5a6;color:white;padding:4px 8px;border-radius:3px;font-size:0.9em;white-space:nowrap}}
.alert{{padding:15px;margin:15px 0;border-radius:5px}}
.alert-warning{{background:#fff3cd;border-left:4px solid #f39c12}}
.alert-danger{{background:#f8d7da;border-left:4px solid #dc3545}}
.alert-ok{{background:#d4edda;border-left:4px solid #27ae60}}
.hidden{{display:none}}
@media print{{body{{background:white;margin:0}}.container{{box-shadow:none;padding:0}}.search-box{{display:none}}
.section-content.hidden{{display:block;max-height:none}}h2::after{{content:''}}tr{{page-break-inside:avoid}}}}
</style></head>
<body><div class="container">
<div class="header">
<h1>INFORME DE MIGRACIÓN - {e(equipo)}</h1>
<span class="fecha">{e(fecha)}</span>
</div>
<p class="subtitulo">Usuario {e(usuario)} · cada elemento dice qué hacer en el equipo nuevo · sin contraseñas (Wi-Fi, credenciales y correo no se incluyen)</p>
<div class="search-box">
<input type="text" id="searchInput" placeholder="Buscar en el informe..." onkeyup="buscarEnInforme()">
<button class="btn-toggle" onclick="expandirTodo()">+ Expandir todo</button>
<button class="btn-toggle" onclick="contraerTodo()">- Contraer todo</button>
<button class="btn-toggle" onclick="window.print()">Imprimir</button>
</div>
{''.join(bloques)}
<hr><p style='text-align:center;color:#7f8c8d;margin-top:30px'>
Informe generado automáticamente - {e(fecha)} · MASys · Masoft.es
</p></div>
<script>
// Inicializar categorías al cargar (igual que MIGRACION.PS1: abierta solo la primera)
window.onload = function() {{
  var allH2 = document.querySelectorAll("h2");
  for (var i = 0; i < allH2.length; i++) {{
    var h2 = allH2[i];
    var seccionId = "seccion-" + i;
    h2.setAttribute("data-seccion", seccionId);
    var wrapper = document.createElement("div");
    wrapper.className = "section-content";
    wrapper.id = seccionId;
    var siguienteH2 = allH2[i + 1];
    var elemento = h2.nextElementSibling;
    while (elemento && elemento !== siguienteH2 && elemento.tagName !== "HR") {{
      var siguiente = elemento.nextElementSibling;
      wrapper.appendChild(elemento);
      elemento = siguiente;
    }}
    h2.parentNode.insertBefore(wrapper, h2.nextSibling);
    if (i !== 0) {{
      h2.classList.add("collapsed");
      wrapper.classList.add("hidden");
    }}
    h2.onclick = function() {{ toggleSeccion(this.getAttribute("data-seccion")); }};
  }}
}};
function toggleSeccion(seccionId) {{
  var h2 = document.querySelector("[data-seccion='" + seccionId + "']");
  var contenido = document.getElementById(seccionId);
  if (contenido.classList.contains("hidden")) {{
    h2.classList.remove("collapsed");
    contenido.classList.remove("hidden");
  }} else {{
    h2.classList.add("collapsed");
    contenido.classList.add("hidden");
  }}
}}
function expandirTodo() {{
  var allH2 = document.querySelectorAll("h2");
  for (var i = 0; i < allH2.length; i++) {{
    var contenido = document.getElementById(allH2[i].getAttribute("data-seccion"));
    if (contenido) {{ allH2[i].classList.remove("collapsed"); contenido.classList.remove("hidden"); }}
  }}
}}
function contraerTodo() {{
  var allH2 = document.querySelectorAll("h2");
  for (var i = 0; i < allH2.length; i++) {{
    var contenido = document.getElementById(allH2[i].getAttribute("data-seccion"));
    if (contenido) {{ allH2[i].classList.add("collapsed"); contenido.classList.add("hidden"); }}
  }}
}}
function buscarEnInforme() {{
  var filter = document.getElementById("searchInput").value.toUpperCase();
  var allH2 = document.querySelectorAll("h2");
  for (var i = 0; i < allH2.length; i++) {{
    var h2 = allH2[i];
    var contenido = document.getElementById(h2.getAttribute("data-seccion"));
    if (filter.length === 0) {{
      h2.style.display = "";
      if (contenido) contenido.style.display = "";
      continue;
    }}
    var texto = (h2.textContent + " " + (contenido ? contenido.textContent : "")).toUpperCase();
    if (texto.indexOf(filter) > -1) {{
      h2.style.display = "";
      if (contenido) {{ contenido.style.display = ""; contenido.classList.remove("hidden"); h2.classList.remove("collapsed"); }}
    }} else {{
      h2.style.display = "none";
      if (contenido) contenido.style.display = "none";
    }}
  }}
}}
</script>
</body></html>"""


def lista_html(inventario: dict, comparacion: Optional[List[Tuple[Elemento, str, str]]] = None, destino: str = "") -> str:
    """Lista de comprobación imprimible: un apartado por categoría, casilla por elemento (botón «🌐 Lista de comprobación…»).
    El informe con el diseño de MIGRACION.PS1 es informe_html (botón HTML de la barra inferior)."""
    filas = comparacion if comparacion is not None else [(e, "", "") for e in elementos_de(inventario)]
    colores = {PRESENTE: "#16a34a", FALTA: "#dc2626", DISTINTO: "#ca8a04"}
    por_cat: Dict[str, list] = {}
    for e, est, det in filas:
        por_cat.setdefault(e.categoria, []).append((e, est, det))
    secciones = []
    for cat in [c for c in CATEGORIAS if c in por_cat] + [c for c in por_cat if c not in CATEGORIAS]:
        filas_html = []
        for e, est, det in por_cat[cat]:
            estado = (f'<td><b style="color:{colores.get(est, "#555")}">{html.escape(est)}</b>'
                      + (f'<br><small>{html.escape(det)}</small>' if est == DISTINTO else "") + "</td>") if comparacion is not None else ""
            filas_html.append(f'<tr class="{"imp" if e.importante else ""}"><td class="c">☐</td><td>{html.escape(e.elemento)}</td>'
                              f'<td>{html.escape(e.detalle)}</td><td>{html.escape(e.accion)}</td>{estado}</tr>')
        cab_estado = "<th>En el equipo nuevo</th>" if comparacion is not None else ""
        secciones.append(f"<h2>{html.escape(cat)} <span>({len(por_cat[cat])})</span></h2><table><tr><th></th><th>Elemento</th>"
                         f"<th>Detalle</th><th>Qué hacer en el equipo nuevo</th>{cab_estado}</tr>{''.join(filas_html)}</table>")
    resumen = ""
    if comparacion is not None:
        n = {k: sum(1 for _e, s, _d in comparacion if s == k) for k in (PRESENTE, FALTA, DISTINTO)}
        resumen = (f'<p class="res">Comparado con <b>{html.escape(destino)}</b>: <b style="color:#16a34a">{n[PRESENTE]} presentes</b> · '
                   f'<b style="color:#dc2626">{n[FALTA]} faltan</b> · <b style="color:#ca8a04">{n[DISTINTO]} distintos</b></p>')
    return f"""<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Inventario de migración — {html.escape(inventario.get('equipo', ''))}</title><style>
:root{{--primary:#1a3a5c;--accent:#2979FF;--bg:#f7f9fc;--border:#dde3ea;--text:#1a1a2e}}
body{{margin:0;padding:24px;background:var(--bg);color:var(--text);font-family:'Segoe UI',Arial,sans-serif;font-size:13px}}
.cab{{background:linear-gradient(135deg,var(--primary),#0e2438);color:#fff;padding:22px 28px;border-radius:10px}}
.cab h1{{margin:0 0 6px;font-weight:300;font-size:26px}}.cab p{{margin:2px 0;color:#cfe0f0}}
h2{{color:var(--primary);border-bottom:2px solid var(--primary);padding-bottom:4px;margin-top:26px;font-size:17px}}h2 span{{color:#888;font-weight:400}}
table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--border)}}
th{{background:var(--primary);color:#fff;text-align:left;padding:7px 9px}}td{{padding:6px 9px;border-bottom:1px solid var(--border);vertical-align:top}}
tr:nth-child(even) td{{background:#fafbfd}}tr.imp td{{background:#fff7e6}}td.c{{width:18px;font-size:16px}}
.res{{font-size:15px;background:#fff;border:1px solid var(--border);border-radius:8px;padding:10px 14px}}
footer{{text-align:center;color:#777;margin-top:24px}}@media print{{body{{background:#fff;padding:0}}h2{{page-break-after:avoid}}tr{{page-break-inside:avoid}}}}
</style></head><body>
<div class="cab"><h1>Inventario de migración</h1><p>Equipo <b>{html.escape(inventario.get('equipo', ''))}</b> · usuario {html.escape(inventario.get('usuario', ''))}
· {html.escape(inventario.get('fecha', ''))}</p><p>Lista de comprobación para preparar el equipo nuevo. Las filas en amarillo son las importantes.</p></div>
{resumen}{''.join(secciones)}
<footer>MASys · Masoft.es · sin contraseñas: las claves de Wi-Fi, credenciales y correo no se incluyen</footer></body></html>"""
