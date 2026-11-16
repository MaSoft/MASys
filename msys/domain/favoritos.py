# -*- coding: utf-8 -*-
"""Motor del Gestor de favoritos (sin Qt) para la sección 🌐 Navegadores.

Hace de verdad lo que el visor de Marcadores (solo lectura) no hacía:
cargar, editar, reorganizar, comprobar qué enlaces están rotos y VOLVER A
ESCRIBIRLOS — sustituyendo los del propio navegador (solo Chromium:
Chrome/Edge) o migrándolos a cualquier navegador vía el HTML estándar
Netscape que todos saben importar.

Decisiones:
- Chromium guarda los marcadores en el archivo JSON «Bookmarks», con tres
  raíces permanentes (barra, otros, móvil) y un «checksum» MD5 que Chrome
  valida al abrir. Reescribimos SOLO los hijos de esas tres raíces,
  conservando el resto del archivo, y recalculamos el checksum con el mismo
  algoritmo que Chromium (bookmark_codec.cc) para que no avise de archivo
  dañado. Aun así, antes se hace copia de seguridad del archivo original y
  se exige el navegador CERRADO (si no, al salir lo sobrescribiría).
- Firefox es de SOLO LECTURA aquí (su places.sqlite es una base viva con
  índices; reescribirla a mano es frágil). Para Firefox se usa la vía de
  exportar a HTML e importar desde el propio Firefox.
- La comprobación de enlaces va en paralelo (varios a la vez) pero es el
  hilo de core/tareas.py quien la saca de la interfaz."""

import html
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable, Dict, List, Optional
from urllib.parse import urlparse

from msys.browser_data import PerfilNavegador, enumerar_perfiles, _copia_temporal, _borrar_temporal

# Estados del enlace (también se usan como texto en la tabla)
PENDIENTE, OK, REDIRIGE, ROTO, SINRED = "", "OK", "Redirige", "Roto", "Sin conexión"

# Nombre legible de cada raíz permanente de Chromium
RAICES = {
    "bookmark_bar": "Barra de marcadores",
    "other": "Otros marcadores",
    "synced": "Dispositivos / móvil",
}
_PROCESO = {"Chrome": "chrome.exe", "Edge": "msedge.exe"}


@dataclass
class Favorito:
    raiz: str          # "bookmark_bar" | "other" | "synced" | "" (Firefox / importado)
    carpeta: str       # ruta de carpetas bajo la raíz ("A/B"); "" = directo en la raíz
    titulo: str
    url: str
    estado: str = PENDIENTE
    detalle: str = ""


# ─────────────────────────── Cargar ──────────────────────────────────

def perfiles_favoritos() -> List[PerfilNavegador]:
    """Perfiles con marcadores (los tres navegadores)."""
    return enumerar_perfiles()


def es_chromium(perfil: PerfilNavegador) -> bool:
    return perfil.navegador in ("Chrome", "Edge")


def cargar(perfil: PerfilNavegador) -> List[Favorito]:
    """Favoritos del perfil, conservando raíz, carpeta y orden."""
    if es_chromium(perfil):
        return _cargar_chromium(perfil)
    return _cargar_firefox(perfil)


def _cargar_chromium(perfil: PerfilNavegador) -> List[Favorito]:
    ruta = os.path.join(perfil.ruta, "Bookmarks")
    if not os.path.isfile(ruta):
        return []
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    favoritos: List[Favorito] = []

    def _recorrer(nodo, raiz, carpeta):
        for hijo in nodo.get("children", []) or []:
            if hijo.get("type") == "url":
                favoritos.append(Favorito(raiz, carpeta, hijo.get("name", ""), hijo.get("url", "")))
            elif hijo.get("type") == "folder":
                sub = f"{carpeta}/{hijo.get('name', '')}" if carpeta else hijo.get("name", "")
                _recorrer(hijo, raiz, sub)

    roots = data.get("roots") or {}
    for clave in ("bookmark_bar", "other", "synced"):
        nodo = roots.get(clave)
        if isinstance(nodo, dict):
            _recorrer(nodo, clave, "")
    return favoritos


def _cargar_firefox(perfil: PerfilNavegador) -> List[Favorito]:
    import sqlite3
    ruta_copia = _copia_temporal(os.path.join(perfil.ruta, "places.sqlite"))
    if not ruta_copia:
        return []
    favoritos: List[Favorito] = []
    try:
        conn = sqlite3.connect(f"file:{ruta_copia}?mode=ro", uri=True)
        cur = conn.cursor()
        # Carpeta de cada marcador reconstruida subiendo por moz_bookmarks.parent
        cur.execute("SELECT id, parent, title FROM moz_bookmarks WHERE type = 2")
        carpetas = {cid: (parent, title or "") for cid, parent, title in cur.fetchall()}

        def ruta_carpeta(pid):
            partes = []
            visto = set()
            while pid in carpetas and pid not in visto:
                visto.add(pid)
                padre, nombre = carpetas[pid]
                if nombre and nombre not in ("", "root________"):
                    partes.append(nombre)
                pid = padre
            return "/".join(reversed(partes))

        cur.execute("""
            SELECT b.title, p.url, b.parent FROM moz_bookmarks b
            JOIN moz_places p ON b.fk = p.id
            WHERE b.type = 1 AND p.url IS NOT NULL ORDER BY b.parent, b.position
        """)
        for titulo, url, parent in cur.fetchall():
            favoritos.append(Favorito("", ruta_carpeta(parent), titulo or "(sin título)", url or ""))
        conn.close()
    except sqlite3.Error:
        pass
    finally:
        _borrar_temporal(ruta_copia)
    return favoritos


# ──────────────────── Comprobar enlaces rotos ────────────────────────

def comprobar_enlace(url: str, timeout: int = 8) -> tuple:
    """(estado, detalle) de un enlace. No sigue descargando el cuerpo."""
    import requests
    esquema = urlparse(url).scheme.lower()
    if esquema in ("javascript", "data", "about", "chrome", "edge", "file"):
        return OK, f"Enlace interno ({esquema})"
    if esquema not in ("http", "https"):
        return SINRED, "No es una dirección web"
    cabeceras = {"User-Agent": "Mozilla/5.0 (MASys)"}
    try:
        try:
            r = requests.head(url, timeout=timeout, allow_redirects=True, headers=cabeceras)
            if r.status_code >= 400 or r.status_code == 405:
                r = requests.get(url, timeout=timeout, allow_redirects=True, stream=True, headers=cabeceras)
                r.close()
        except requests.RequestException:
            r = requests.get(url, timeout=timeout, allow_redirects=True, stream=True, headers=cabeceras)
            r.close()
    except requests.exceptions.SSLError as e:
        return ROTO, f"Error de certificado: {e}"[:120]
    except requests.RequestException as e:
        return SINRED, f"No responde: {e}"[:120]
    destino_final = r.url
    hubo_salto = bool(r.history) and urlparse(destino_final).netloc != urlparse(url).netloc
    if r.status_code >= 400:
        return ROTO, f"{r.status_code} {r.reason}"
    if hubo_salto:
        return REDIRIGE, f"→ {destino_final}"[:120]
    return OK, f"{r.status_code} {r.reason}"


def comprobar_enlaces(favoritos: List[Favorito], progreso: Optional[Callable[[int, int], None]] = None,
                      debe_parar: Optional[Callable[[], bool]] = None, hilos: int = 16) -> List[Favorito]:
    """Rellena estado/detalle de cada favorito, en paralelo. Devuelve la
    misma lista (modificada in situ) para encadenar con la señal del hilo."""
    total = len(favoritos)
    hechos = [0]

    def _uno(fav: Favorito):
        if debe_parar and debe_parar():
            return
        fav.estado, fav.detalle = comprobar_enlace(fav.url)
        hechos[0] += 1
        if progreso:
            progreso(hechos[0], total)

    with ThreadPoolExecutor(max_workers=max(1, hilos)) as ex:
        list(ex.map(_uno, favoritos))
    return favoritos


# ─────────────────────── Revisión / duplicados ───────────────────────

def _url_normalizada(url: str) -> str:
    u = url.strip().lower()
    if u.endswith("/"):
        u = u[:-1]
    return u


def duplicados(favoritos: List[Favorito]) -> Dict[str, List[Favorito]]:
    """URL (normalizada) -> favoritos que la repiten (solo los grupos de 2 o más)."""
    por_url: Dict[str, List[Favorito]] = {}
    for f in favoritos:
        if f.url:
            por_url.setdefault(_url_normalizada(f.url), []).append(f)
    return {u: lst for u, lst in por_url.items() if len(lst) > 1}


def resumen(favoritos: List[Favorito]) -> dict:
    dup = duplicados(favoritos)
    return {
        "total": len(favoritos),
        "rotos": sum(1 for f in favoritos if f.estado == ROTO),
        "redirigen": sum(1 for f in favoritos if f.estado == REDIRIGE),
        "sin_conexion": sum(1 for f in favoritos if f.estado == SINRED),
        "sin_titulo": sum(1 for f in favoritos if not f.titulo.strip()),
        "duplicados": sum(len(v) for v in dup.values()),
    }


# ─────────────────────── Exportar / importar HTML ────────────────────

def _arbol(favoritos: List[Favorito]) -> dict:
    """Árbol anidado {._hijos, ._favs} por ruta legible (raíz + carpeta),
    conservando el orden de aparición."""
    raiz: dict = {"_hijos": {}, "_favs": []}
    for f in favoritos:
        partes = []
        if f.raiz:
            partes.append(RAICES.get(f.raiz, f.raiz))
        if f.carpeta:
            partes += [p for p in f.carpeta.split("/") if p]
        nodo = raiz
        for parte in partes:
            nodo = nodo["_hijos"].setdefault(parte, {"_hijos": {}, "_favs": []})
        nodo["_favs"].append(f)
    return raiz


def exportar_netscape(favoritos: List[Favorito], ruta: str):
    """HTML estándar Netscape (lo importa Chrome, Edge, Firefox, Brave...)."""
    lineas = [
        "<!DOCTYPE NETSCAPE-Bookmark-file-1>",
        '<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">',
        "<TITLE>Bookmarks</TITLE>", "<H1>Marcadores</H1>", "<DL><p>",
    ]

    def _emitir(nodo, nivel):
        sangria = "    " * nivel
        for f in nodo["_favs"]:
            lineas.append(f'{sangria}<DT><A HREF="{html.escape(f.url, quote=True)}">{html.escape(f.titulo)}</A>')
        for nombre, hijo in nodo["_hijos"].items():
            lineas.append(f"{sangria}<DT><H3>{html.escape(nombre)}</H3>")
            lineas.append(f"{sangria}<DL><p>")
            _emitir(hijo, nivel + 1)
            lineas.append(f"{sangria}</DL><p>")

    _emitir(_arbol(favoritos), 1)
    lineas.append("</DL><p>")
    with open(ruta, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lineas))


class _NetscapeParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.favoritos: List[Favorito] = []
        self._pila: List[str] = []
        self._en_h3 = False
        self._en_a = False
        self._href = ""
        self._texto = ""

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t == "h3":
            self._en_h3, self._texto = True, ""
        elif t == "a":
            self._en_a, self._texto = True, ""
            self._href = dict(attrs).get("href", "") or ""
        elif t == "dl":
            self._pila.append(self._ultimo_h3 if getattr(self, "_ultimo_h3", "") else "")

    def handle_endtag(self, tag):
        t = tag.lower()
        if t == "h3":
            self._en_h3 = False
            self._ultimo_h3 = self._texto.strip()
        elif t == "a":
            self._en_a = False
            if self._href:
                carpeta = "/".join(p for p in self._pila if p)
                self.favoritos.append(Favorito("", carpeta, self._texto.strip() or "(sin título)", self._href))
        elif t == "dl" and self._pila:
            self._pila.pop()

    def handle_data(self, data):
        if self._en_h3 or self._en_a:
            self._texto += data


def importar_netscape(ruta: str) -> List[Favorito]:
    """Lee un HTML de marcadores exportado de cualquier navegador."""
    with open(ruta, "r", encoding="utf-8", errors="replace") as fh:
        p = _NetscapeParser()
        p.feed(fh.read())
    return p.favoritos


# ─────────────── Volver a escribir en el navegador (Chromium) ─────────

def navegador_en_ejecucion(navegador: str) -> bool:
    """True si Chrome/Edge está abierto (reescribir con él abierto se
    perdería al cerrarlo). Si no se puede comprobar, asume que NO lo está
    para no bloquear, pero el aviso de cerrar el navegador sigue mostrándose."""
    proceso = _PROCESO.get(navegador)
    if not proceso:
        return False
    try:
        from msys.core.utils import run_system_command
        ok, salida = run_system_command(["tasklist", "/FI", f"IMAGENAME eq {proceso}", "/NH"], timeout=10)
        return ok and proceso.lower() in salida.lower()
    except Exception:
        return False


def _chrome_ahora() -> str:
    """Marca de tiempo de Chromium: microsegundos desde 1601-01-01."""
    return str(int((time.time() + 11644473600) * 1_000_000))


def _construir_hijos(favoritos: List[Favorito], contador: list) -> list:
    """Reconstruye la lista de hijos (carpetas anidadas + urls) de una raíz
    a partir de los favoritos de esa raíz, conservando el orden."""
    arbol = {"_hijos": {}, "_favs": []}
    for f in favoritos:
        nodo = arbol
        for parte in [p for p in f.carpeta.split("/") if p]:
            nodo = nodo["_hijos"].setdefault(parte, {"_hijos": {}, "_favs": [], "_orden": len(nodo["_hijos"])})
        nodo["_favs"].append(f)

    def _emitir(nodo) -> list:
        hijos = []
        for f in nodo["_favs"]:
            contador[0] += 1
            hijos.append({"type": "url", "id": str(contador[0]), "name": f.titulo,
                          "url": f.url, "date_added": _chrome_ahora()})
        for nombre, sub in nodo["_hijos"].items():
            contador[0] += 1
            hijos.append({"type": "folder", "id": str(contador[0]), "name": nombre,
                          "date_added": _chrome_ahora(), "date_modified": _chrome_ahora(),
                          "children": _emitir(sub)})
        return hijos

    return _emitir(arbol)


def _checksum_chromium(roots: dict) -> str:
    """MD5 con el mismo algoritmo que bookmark_codec.cc de Chromium."""
    import hashlib
    md5 = hashlib.md5()

    def _nodo(n):
        _id = str(n.get("id", ""))
        nombre = n.get("name", "")
        if n.get("type") == "url":
            md5.update(_id.encode("utf-8"))
            md5.update(nombre.encode("utf-8"))
            md5.update(b"url")
            md5.update(n.get("url", "").encode("utf-8"))
        else:
            md5.update(_id.encode("utf-8"))
            md5.update(nombre.encode("utf-8"))
            md5.update(b"folder")
            for h in n.get("children", []):
                _nodo(h)

    for clave in ("bookmark_bar", "other", "synced"):
        if isinstance(roots.get(clave), dict):
            _nodo(roots[clave])
    return md5.hexdigest()


def _max_id(data: dict) -> int:
    mayor = [0]

    def _rec(n):
        try:
            mayor[0] = max(mayor[0], int(n.get("id", 0)))
        except (TypeError, ValueError):
            pass
        for h in n.get("children", []) or []:
            _rec(h)

    for n in (data.get("roots") or {}).values():
        if isinstance(n, dict):
            _rec(n)
    return mayor[0]


def escribir_chromium(perfil: PerfilNavegador, favoritos: List[Favorito],
                      respaldar: Optional[Callable[[str], tuple]] = None) -> str:
    """Sustituye los marcadores del perfil Chromium por 'favoritos'.
    'respaldar(ruta)->(ok,destino)' se llama antes de escribir (copia de
    seguridad obligatoria). Devuelve el mensaje de resultado. Lanza
    ValueError/OSError si no se puede."""
    if not es_chromium(perfil):
        raise ValueError("Solo se puede reescribir en Chrome o Edge (Firefox es de solo lectura).")
    ruta = os.path.join(perfil.ruta, "Bookmarks")
    if not os.path.isfile(ruta):
        raise OSError(f"No se encontró el archivo de marcadores:\n{ruta}")

    with open(ruta, "r", encoding="utf-8") as f:
        data = json.load(f)
    roots = data.get("roots") or {}

    copia = ""
    if respaldar:
        ok, destino = respaldar(ruta)
        if not ok:
            raise OSError(f"No se pudo hacer la copia de seguridad: {destino}")
        copia = destino

    contador = [_max_id(data)]
    por_raiz: Dict[str, List[Favorito]] = {"bookmark_bar": [], "other": [], "synced": []}
    for f in favoritos:
        por_raiz.get(f.raiz if f.raiz in por_raiz else "other").append(f)

    for clave, lista in por_raiz.items():
        nodo = roots.get(clave)
        if not isinstance(nodo, dict):
            continue
        nodo["children"] = _construir_hijos(lista, contador)

    data["checksum"] = _checksum_chromium(roots)
    data["version"] = 1
    # Escritura atómica. No se toca «Bookmarks.bak» (lo gestiona Chrome); la
    # copia de seguridad del original ya se hizo arriba con respaldar().
    tmp = ruta + ".masys.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    try:
        os.replace(tmp, ruta)
    except OSError:
        if os.path.isfile(tmp):
            os.remove(tmp)
        raise
    return f"Escritos {len(favoritos)} marcadores en {perfil.navegador} ({perfil.nombre_perfil})." + (
        f"\nCopia de seguridad: {copia}" if copia else "")
