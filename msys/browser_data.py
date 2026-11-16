# -*- coding: utf-8 -*-
"""Lectura directa de datos reales de navegadores instalados (Chrome/Edge/
Firefox): perfiles, marcadores, historial, descargas, extensiones y
contraseñas guardadas. Todo lectura de disco (SQLite/JSON) — no se
automatiza ningún navegador, así que funciona con el navegador abierto o
cerrado (los SQLite bloqueados se copian primero a un temporal).

Contraseñas guardadas (Chrome/Edge): el cifrado real es DPAPI (la clave
maestra en "Local State" -> os_crypt.encrypted_key) + una capa AES-256-GCM
por contraseña desde Chrome 80 — se descifra con win32crypt (ya usado en
core/utils.py para los checkpoints) + cryptography.hazmat AESGCM, sin
dependencias nuevas.

Firefox NSS (logins.json + key4.db) NO está implementado: requeriría
reimplementar a mano la derivación de claves PKCS#12 Appendix B (SHA-1)
sin librería NSS ni pyasn1/pycryptodome instalados en el proyecto, y no
hay ningún perfil de Firefox con credenciales guardadas en esta máquina
para poder verificarlo en vivo — antes de escribir código críptico sin
forma de comprobar que descifra bien, se deja pendiente (ver lectura de
logins.json sin descifrar, al menos para saber qué sitios tienen cuenta
guardada aunque no se pueda mostrar la contraseña)."""

import os
import re
import json
import base64
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional

import win32crypt
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


@dataclass
class PerfilNavegador:
    navegador: str  # "Chrome" | "Edge" | "Firefox"
    nombre_perfil: str
    ruta: str
    carpeta_user_data: str = ""  # raíz del navegador (para Local State en Chromium)


def _carpetas_chromium() -> List[tuple]:
    return [
        ("Chrome", os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "User Data")),
        ("Edge", os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Edge", "User Data")),
    ]


def enumerar_perfiles() -> List[PerfilNavegador]:
    """Todos los perfiles reales encontrados en disco, de los 3 navegadores."""
    perfiles: List[PerfilNavegador] = []

    for nombre, carpeta_base in _carpetas_chromium():
        if not os.path.isdir(carpeta_base):
            continue
        default_path = os.path.join(carpeta_base, "Default")
        if os.path.isdir(default_path):
            perfiles.append(PerfilNavegador(nombre, "Default", default_path, carpeta_base))
        for entrada in os.listdir(carpeta_base):
            if re.match(r'^Profile \d+$', entrada):
                ruta = os.path.join(carpeta_base, entrada)
                if os.path.isdir(ruta):
                    perfiles.append(PerfilNavegador(nombre, entrada, ruta, carpeta_base))

    carpeta_ff = os.path.join(os.environ.get("APPDATA", ""), "Mozilla", "Firefox", "Profiles")
    if os.path.isdir(carpeta_ff):
        for entrada in os.listdir(carpeta_ff):
            ruta = os.path.join(carpeta_ff, entrada)
            if os.path.isdir(ruta):
                perfiles.append(PerfilNavegador("Firefox", entrada, ruta, carpeta_ff))

    return perfiles


def _copia_temporal(ruta_original: str) -> Optional[str]:
    """Copia un archivo (típicamente SQLite bloqueado por el navegador
    abierto) a un temporal, para poder leerlo sin conflicto de locks.
    Llamada responsable de borrar el temporal cuando termine."""
    if not os.path.isfile(ruta_original):
        return None
    try:
        fd, destino = tempfile.mkstemp(suffix=".sqlite", prefix="msys_")
        os.close(fd)
        shutil.copy2(ruta_original, destino)
        return destino
    except OSError:
        return None


def _borrar_temporal(ruta: Optional[str]):
    if ruta:
        try:
            os.remove(ruta)
        except OSError:
            pass


def _chrome_timestamp_a_texto(valor) -> str:
    """Chrome/Edge guardan fechas como microsegundos desde 1601-01-01
    (epoch de Windows FILETIME), NO desde el epoch Unix — un error clásico
    sería tratarlo como segundos Unix y obtener una fecha en el año 60000."""
    if not valor:
        return ""
    try:
        return (datetime(1601, 1, 1) + timedelta(microseconds=int(valor))).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return ""


def _firefox_timestamp_a_texto(valor) -> str:
    """Firefox guarda microsegundos desde el epoch Unix (distinto de Chrome)."""
    if not valor:
        return ""
    try:
        return datetime.fromtimestamp(int(valor) / 1_000_000).strftime("%Y-%m-%d %H:%M:%S")
    except (OverflowError, OSError, ValueError):
        return ""


# ─────────────────────────── Marcadores ──────────────────────────────

@dataclass
class MarcadorRow:
    navegador: str
    perfil: str
    carpeta: str
    titulo: str
    url: str


def leer_marcadores(perfil: PerfilNavegador) -> List[MarcadorRow]:
    if perfil.navegador == "Firefox":
        return _leer_marcadores_firefox(perfil)
    return _leer_marcadores_chromium(perfil)


def _leer_marcadores_chromium(perfil: PerfilNavegador) -> List[MarcadorRow]:
    ruta = os.path.join(perfil.ruta, "Bookmarks")
    if not os.path.isfile(ruta):
        return []
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []

    marcadores: List[MarcadorRow] = []

    def _recorrer(nodo, ruta_carpeta):
        tipo = nodo.get("type")
        if tipo == "url":
            marcadores.append(MarcadorRow(
                navegador=perfil.navegador, perfil=perfil.nombre_perfil,
                carpeta=ruta_carpeta, titulo=nodo.get("name", ""), url=nodo.get("url", ""),
            ))
        elif tipo == "folder":
            nueva = f"{ruta_carpeta}/{nodo.get('name', '')}" if ruta_carpeta else nodo.get("name", "")
            for hijo in nodo.get("children", []) or []:
                _recorrer(hijo, nueva)

    for clave, nodo_raiz in (data.get("roots") or {}).items():
        if isinstance(nodo_raiz, dict):
            _recorrer(nodo_raiz, nodo_raiz.get("name", clave))
    return marcadores


def _leer_marcadores_firefox(perfil: PerfilNavegador) -> List[MarcadorRow]:
    ruta_copia = _copia_temporal(os.path.join(perfil.ruta, "places.sqlite"))
    if not ruta_copia:
        return []
    marcadores: List[MarcadorRow] = []
    try:
        conn = sqlite3.connect(f"file:{ruta_copia}?mode=ro", uri=True)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT b.title, p.url, parent.title
            FROM moz_bookmarks b
            JOIN moz_places p ON b.fk = p.id
            LEFT JOIN moz_bookmarks parent ON b.parent = parent.id
            WHERE b.type = 1 AND p.url IS NOT NULL
        """)
        for titulo, url, carpeta in cursor.fetchall():
            marcadores.append(MarcadorRow(
                navegador="Firefox", perfil=perfil.nombre_perfil,
                carpeta=carpeta or "", titulo=titulo or "(sin título)", url=url or "",
            ))
        conn.close()
    except sqlite3.Error:
        pass
    finally:
        _borrar_temporal(ruta_copia)
    return marcadores


# ─────────────────────────── Historial ───────────────────────────────

@dataclass
class HistorialRow:
    navegador: str
    perfil: str
    titulo: str
    url: str
    visitas: int
    ultima_visita: str


def leer_historial(perfil: PerfilNavegador, limite: int = 500) -> List[HistorialRow]:
    if perfil.navegador == "Firefox":
        return _leer_historial_firefox(perfil, limite)
    return _leer_historial_chromium(perfil, limite)


def _leer_historial_chromium(perfil: PerfilNavegador, limite: int) -> List[HistorialRow]:
    ruta_copia = _copia_temporal(os.path.join(perfil.ruta, "History"))
    if not ruta_copia:
        return []
    filas: List[HistorialRow] = []
    try:
        conn = sqlite3.connect(f"file:{ruta_copia}?mode=ro", uri=True)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT title, url, visit_count, last_visit_time FROM urls "
            "ORDER BY last_visit_time DESC LIMIT ?", (limite,))
        for titulo, url, visitas, last_visit_time in cursor.fetchall():
            filas.append(HistorialRow(
                navegador=perfil.navegador, perfil=perfil.nombre_perfil,
                titulo=titulo or "", url=url or "", visitas=visitas or 0,
                ultima_visita=_chrome_timestamp_a_texto(last_visit_time),
            ))
        conn.close()
    except sqlite3.Error:
        pass
    finally:
        _borrar_temporal(ruta_copia)
    return filas


def _leer_historial_firefox(perfil: PerfilNavegador, limite: int) -> List[HistorialRow]:
    ruta_copia = _copia_temporal(os.path.join(perfil.ruta, "places.sqlite"))
    if not ruta_copia:
        return []
    filas: List[HistorialRow] = []
    try:
        conn = sqlite3.connect(f"file:{ruta_copia}?mode=ro", uri=True)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT title, url, visit_count, last_visit_date FROM moz_places "
            "WHERE last_visit_date IS NOT NULL ORDER BY last_visit_date DESC LIMIT ?", (limite,))
        for titulo, url, visitas, last_visit_date in cursor.fetchall():
            filas.append(HistorialRow(
                navegador="Firefox", perfil=perfil.nombre_perfil,
                titulo=titulo or "", url=url or "", visitas=visitas or 0,
                ultima_visita=_firefox_timestamp_a_texto(last_visit_date),
            ))
        conn.close()
    except sqlite3.Error:
        pass
    finally:
        _borrar_temporal(ruta_copia)
    return filas


# ─────────────────────────── Descargas ───────────────────────────────

@dataclass
class DescargaRow:
    navegador: str
    perfil: str
    nombre_archivo: str
    ruta: str
    fecha: str


def leer_descargas(perfil: PerfilNavegador, limite: int = 200) -> List[DescargaRow]:
    if perfil.navegador == "Firefox":
        return _leer_descargas_firefox(perfil, limite)
    return _leer_descargas_chromium(perfil, limite)


def _leer_descargas_chromium(perfil: PerfilNavegador, limite: int) -> List[DescargaRow]:
    ruta_copia = _copia_temporal(os.path.join(perfil.ruta, "History"))
    if not ruta_copia:
        return []
    filas: List[DescargaRow] = []
    try:
        conn = sqlite3.connect(f"file:{ruta_copia}?mode=ro", uri=True)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT target_path, tab_url, start_time FROM downloads "
            "ORDER BY start_time DESC LIMIT ?", (limite,))
        for target_path, tab_url, start_time in cursor.fetchall():
            filas.append(DescargaRow(
                navegador=perfil.navegador, perfil=perfil.nombre_perfil,
                nombre_archivo=os.path.basename(target_path or ""), ruta=target_path or "",
                fecha=_chrome_timestamp_a_texto(start_time),
            ))
        conn.close()
    except sqlite3.Error:
        pass
    finally:
        _borrar_temporal(ruta_copia)
    return filas


def _leer_descargas_firefox(perfil: PerfilNavegador, limite: int) -> List[DescargaRow]:
    ruta_copia = _copia_temporal(os.path.join(perfil.ruta, "places.sqlite"))
    if not ruta_copia:
        return []
    filas: List[DescargaRow] = []
    try:
        conn = sqlite3.connect(f"file:{ruta_copia}?mode=ro", uri=True)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT a.content, a.dateAdded
            FROM moz_annos a
            JOIN moz_anno_attributes attr ON a.anno_attribute_id = attr.id
            WHERE attr.name = 'downloads/destinationFileURI'
            ORDER BY a.dateAdded DESC LIMIT ?
        """, (limite,))
        for destino, date_added in cursor.fetchall():
            filas.append(DescargaRow(
                navegador="Firefox", perfil=perfil.nombre_perfil,
                nombre_archivo=os.path.basename(destino or ""), ruta=destino or "",
                fecha=_firefox_timestamp_a_texto(date_added),
            ))
        conn.close()
    except sqlite3.Error:
        pass
    finally:
        _borrar_temporal(ruta_copia)
    return filas


# ─────────────────────────── Extensiones ─────────────────────────────

@dataclass
class ExtensionRow:
    navegador: str
    perfil: str
    nombre: str
    version: str
    habilitada: bool
    id_extension: str


def _resolver_nombre_localizado(ruta_extension_id: str, version_dir: str, plantilla: str) -> Optional[str]:
    m = re.match(r'__MSG_(.+)__$', plantilla)
    if not m:
        return None
    clave = m.group(1)
    carpeta_locales = os.path.join(ruta_extension_id, version_dir, "_locales")
    if not os.path.isdir(carpeta_locales):
        return None
    try:
        idiomas_disponibles = os.listdir(carpeta_locales)
    except OSError:
        return None
    for idioma in ["en", "en_US"] + idiomas_disponibles:
        ruta_msg = os.path.join(carpeta_locales, idioma, "messages.json")
        if os.path.isfile(ruta_msg):
            try:
                with open(ruta_msg, "r", encoding="utf-8-sig") as f:
                    mensajes = json.load(f)
                valor = (mensajes.get(clave) or {}).get("message")
                if valor:
                    return valor
            except (OSError, json.JSONDecodeError):
                continue
    return None


def leer_extensiones(perfil: PerfilNavegador) -> List[ExtensionRow]:
    if perfil.navegador == "Firefox":
        return _leer_extensiones_firefox(perfil)
    return _leer_extensiones_chromium(perfil)


def _leer_extensiones_chromium(perfil: PerfilNavegador) -> List[ExtensionRow]:
    carpeta_ext = os.path.join(perfil.ruta, "Extensions")
    if not os.path.isdir(carpeta_ext):
        return []

    estado_habilitadas = {}
    for nombre_prefs in ("Secure Preferences", "Preferences"):
        ruta_prefs = os.path.join(perfil.ruta, nombre_prefs)
        if os.path.isfile(ruta_prefs):
            try:
                with open(ruta_prefs, "r", encoding="utf-8") as f:
                    prefs = json.load(f)
                settings = (prefs.get("extensions") or {}).get("settings") or {}
                for ext_id, datos in settings.items():
                    estado = datos.get("state")
                    if estado is not None:
                        estado_habilitadas[ext_id] = (estado == 1)
            except (OSError, json.JSONDecodeError):
                pass
            break

    extensiones: List[ExtensionRow] = []
    try:
        ids_extension = os.listdir(carpeta_ext)
    except OSError:
        return []

    for ext_id in ids_extension:
        ruta_id = os.path.join(carpeta_ext, ext_id)
        if not os.path.isdir(ruta_id):
            continue
        try:
            versiones = os.listdir(ruta_id)
        except OSError:
            continue
        for version_dir in versiones:
            ruta_manifest = os.path.join(ruta_id, version_dir, "manifest.json")
            if not os.path.isfile(ruta_manifest):
                continue
            try:
                with open(ruta_manifest, "r", encoding="utf-8-sig") as f:
                    manifest = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            nombre = manifest.get("name", ext_id)
            if isinstance(nombre, str) and nombre.startswith("__MSG_"):
                nombre = _resolver_nombre_localizado(ruta_id, version_dir, nombre) or ext_id
            extensiones.append(ExtensionRow(
                navegador=perfil.navegador, perfil=perfil.nombre_perfil,
                nombre=nombre, version=manifest.get("version", version_dir),
                habilitada=estado_habilitadas.get(ext_id, True), id_extension=ext_id,
            ))
    return extensiones


def _leer_extensiones_firefox(perfil: PerfilNavegador) -> List[ExtensionRow]:
    ruta = os.path.join(perfil.ruta, "extensions.json")
    if not os.path.isfile(ruta):
        return []
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []

    extensiones = []
    for addon in data.get("addons", []) or []:
        locale = addon.get("defaultLocale") or {}
        nombre = locale.get("name") or addon.get("id", "")
        extensiones.append(ExtensionRow(
            navegador="Firefox", perfil=perfil.nombre_perfil,
            nombre=nombre, version=addon.get("version", ""),
            habilitada=bool(addon.get("active", False)), id_extension=addon.get("id", ""),
        ))
    return extensiones


# ────────────────────── Contraseñas guardadas ────────────────────────

@dataclass
class PasswordGuardadaRow:
    navegador: str
    perfil: str
    sitio: str
    usuario: str
    password: Optional[str]  # None = no se pudo descifrar (distinto de "" vacía real)


def _obtener_clave_maestra_chromium(carpeta_user_data: str) -> Optional[bytes]:
    """Clave AES-256 real usada para las contraseñas desde Chrome 80+.
    Antes de esa versión no existe esta capa y cada password_value ya es
    directamente un blob DPAPI — leer_passwords() ya contempla ambos
    casos en _descifrar_password_chromium."""
    ruta_local_state = os.path.join(carpeta_user_data, "Local State")
    if not os.path.isfile(ruta_local_state):
        return None
    try:
        with open(ruta_local_state, "r", encoding="utf-8") as f:
            local_state = json.load(f)
        clave_b64 = (local_state.get("os_crypt") or {}).get("encrypted_key")
        if not clave_b64:
            return None
        clave_cifrada = base64.b64decode(clave_b64)
        if clave_cifrada[:5] != b"DPAPI":
            return None
        clave_cifrada = clave_cifrada[5:]
        _descr, clave = win32crypt.CryptUnprotectData(clave_cifrada, None, None, None, 0)
        return clave
    except Exception:
        return None


def _descifrar_password_chromium(blob: bytes, clave_maestra: Optional[bytes]) -> Optional[str]:
    if not blob:
        return ""
    try:
        if blob[:3] in (b"v10", b"v20") and clave_maestra:
            nonce = blob[3:15]
            ciphertext = blob[15:]
            plano = AESGCM(clave_maestra).decrypt(nonce, ciphertext, None)
            return plano.decode("utf-8", errors="replace")
        # Chrome < 80: DPAPI directo, sin capa AES-GCM.
        _descr, plano = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
        return plano.decode("utf-8", errors="replace")
    except Exception:
        return None


def leer_passwords(perfil: PerfilNavegador) -> List[PasswordGuardadaRow]:
    """Firefox no está implementado (ver docstring del módulo) — devuelve
    lista vacía para ese navegador en vez de fallar."""
    if perfil.navegador == "Firefox":
        return []

    ruta_copia = _copia_temporal(os.path.join(perfil.ruta, "Login Data"))
    if not ruta_copia:
        return []
    clave_maestra = _obtener_clave_maestra_chromium(perfil.carpeta_user_data)
    filas: List[PasswordGuardadaRow] = []
    try:
        conn = sqlite3.connect(f"file:{ruta_copia}?mode=ro", uri=True)
        cursor = conn.cursor()
        cursor.execute("SELECT origin_url, username_value, password_value FROM logins")
        for origin_url, username_value, password_value in cursor.fetchall():
            if not username_value:
                continue
            password = _descifrar_password_chromium(password_value, clave_maestra)
            filas.append(PasswordGuardadaRow(
                navegador=perfil.navegador, perfil=perfil.nombre_perfil,
                sitio=origin_url or "", usuario=username_value or "", password=password,
            ))
        conn.close()
    except sqlite3.Error:
        pass
    finally:
        _borrar_temporal(ruta_copia)
    return filas


# ────────────────────── Tamaño de perfil en disco ────────────────────

def tamano_perfil_mb(perfil: PerfilNavegador) -> float:
    total = 0
    for raiz, _dirs, ficheros in os.walk(perfil.ruta):
        for nombre in ficheros:
            try:
                total += os.path.getsize(os.path.join(raiz, nombre))
            except OSError:
                continue
    return total / (1024 ** 2)
