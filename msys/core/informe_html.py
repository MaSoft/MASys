# -*- coding: utf-8 -*-
"""Plantilla común de TODOS los informes HTML de MASys (sección, varias
secciones, informe completo, tablas genéricas, Soporte, Navegadores,
móvil...). Un solo diseño y un solo código para que todos se vean y
funcionen igual. 4.2.3: separado por MÓDULOS y CATEGORÍAS (petición del
usuario: «informes HTML profesionales separando categorías (módulos) con
búsquedas y filtros»):

- Cabecera corporativa (degradado azul marino) con equipo, fecha y versión.
- Índice lateral AGRUPADO por categoría (Sistema, Red, Usuarios y
  Seguridad...), plegable, con el nº de filas de cada módulo y un punto
  rojo / ámbar si tiene críticos / avisos. «Todos los módulos» arriba.
- Dos vistas: todos los módulos seguidos, o UNO solo (clic en el índice, en
  el resumen o en el desplegable «Módulo» de la barra; «← Todos» vuelve).
- Resumen por módulo (con varias secciones): Categoría · Módulo ·
  Registros · Críticos · Avisos; clic en una fila = ver ese módulo.
- Barra fija: buscador en todo el informe (resalta lo encontrado), filtro
  por nivel (Crítico / Advertencia / OK), «Limpiar», «Expandir»,
  «Contraer», «Imprimir» (o PDF) y cuántas filas se ven.
- Cada módulo se pliega tocando su título y tiene: nº de filas (visibles de
  totales), «Filtros» (una caja por columna; desplegable con los valores
  cuando la columna tiene pocos distintos) y «CSV» (descarga lo que se ve).
  Con muchas filas los filtros salen ya abiertos.
- Tablas ordenables tocando la cabecera (fechas DD/MM/AAAA, números y tamaños).
- Textos largos completos (con salto de línea), sin cortar con «…».
- Al imprimir se despliega todo y se ocultan los controles.

Solo HTML + CSS + JavaScript sin bibliotecas externas: funciona sin internet.
Todo el texto de las celdas se escapa; para HTML propio (p. ej. el icono de
nota con su texto) se usa Html("...")."""

import html as _html
import socket
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple


class Html(str):
    """Contenido que ya es HTML y no se debe escapar."""


@dataclass
class Seccion:
    titulo: str
    cabeceras: Sequence[str]
    filas: Sequence[Sequence[Any]]
    nota: str = ""
    # ("info" | "warning" | "danger" | "success", texto o Html) bajo el título
    aviso: Optional[Tuple[str, Any]] = None
    abierta: bool = True
    # Columnas cuyo texto se clasifica por nivel (badge de color). None = todas.
    columnas_nivel: Optional[Sequence[int]] = None
    # HTML libre en lugar de tabla (resúmenes, texto)
    contenido: Optional[Html] = None
    # Categoría del módulo («💻 Sistema», «🌐 Red»...): agrupa el índice y el informe
    categoria: str = ""


@dataclass
class Kpi:
    valor: Any
    etiqueta: str
    nivel: str = ""     # "" | "crit" | "warn" | "ok"


FILAS_FILTROS_ABIERTOS = 15     # con más filas, los filtros por columna salen ya abiertos
MAX_OPCIONES_DESPLEGABLE = 12   # columna con 2..12 valores distintos = desplegable en vez de caja


def esc(v: Any) -> str:
    if isinstance(v, Html):
        return str(v)
    return _html.escape("" if v is None else str(v))


def nivel_celda(valor: str) -> str:
    """crit / err / warn / ok / '' — mismo criterio que ReportingMixin._nivel_class_celda
    (inactivo/deshabilitado antes que activo: «activo» está dentro de «inactivo»),
    más los iconos de estado que usan las secciones nuevas."""
    t = (valor or "").strip()
    if t[:1] in ("🔴", "❌") or t.startswith("⛔"):
        return "crit"
    if t.startswith("⚠") or t.startswith("🟡"):
        return "warn"
    if t.startswith("✅") or t.startswith("🟢"):
        return "ok"
    v = t.lower()
    if "critico" in v or "crítico" in v:
        return "crit"
    if "error" in v:
        return "err"
    if "advertencia" in v or "fallo" in v:
        return "warn"
    if "inactivo" in v or "deshabilitado" in v:
        return ""
    if v in ("ok", "correcto", "activo", "habilitado", "éxito", "exito", "sí", "si") or v.startswith("ok ") or "éxito" in v:
        return "ok"
    return ""


_RANGO = {"crit": 4, "err": 3, "warn": 2, "ok": 1, "": 0}


def _texto(v: Any) -> str:
    return str(v) if isinstance(v, Html) else ("" if v is None else str(v))


def _opciones_columna(s: Seccion, ci: int) -> Optional[List[str]]:
    """Valores distintos de una columna si son pocos y cortos (para el desplegable del filtro)."""
    if len(s.filas) < 4:
        return None
    vistos: Dict[str, None] = {}
    for fila in s.filas:
        if ci >= len(fila) or isinstance(fila[ci], Html):
            return None
        t = _texto(fila[ci]).strip()
        if len(t) > 40:
            return None
        vistos[t] = None
        if len(vistos) > MAX_OPCIONES_DESPLEGABLE:
            return None
    return sorted(vistos, key=lambda x: x.lower()) if len(vistos) >= 2 else None


def _tabla(i: int, s: Seccion) -> Tuple[str, bool, int, int]:
    """(html de la tabla, hay niveles, filas críticas/error, filas de aviso)."""
    hay_nivel = False
    n_crit = n_warn = 0
    cab = "".join(f"<th>{esc(h)}</th>" for h in s.cabeceras)
    filtros = []
    for ci, h in enumerate(s.cabeceras):
        ops = _opciones_columna(s, ci)
        if ops:
            opciones = "".join(f'<option value="{esc(o)}">{esc(o) or "(vacío)"}</option>' for o in ops)
            filtros.append(f'<th><select aria-label="Filtrar {esc(h)}"><option value="" selected>Todos</option>{opciones}</select></th>')
        else:
            filtros.append(f'<th><input type="search" placeholder="Filtrar…" aria-label="Filtrar {esc(h)}"></th>')
    cuerpo = []
    for fila in s.filas:
        celdas = []
        nivel_fila = ""
        for ci, v in enumerate(fila):
            texto = _texto(v)
            cls = "" if isinstance(v, Html) or (s.columnas_nivel is not None and ci not in s.columnas_nivel) else nivel_celda(texto)
            if cls:
                hay_nivel = True
                if _RANGO[cls] > _RANGO[nivel_fila]:
                    nivel_fila = cls
            contenido = f'<span class="badge-cell {cls}">{esc(v)}</span>' if cls else esc(v)
            larga = ' class="larga"' if not isinstance(v, Html) and len(texto) > 70 else ""
            celdas.append(f"<td{larga}>{contenido}</td>")
        if nivel_fila in ("crit", "err"):
            n_crit += 1
        elif nivel_fila == "warn":
            n_warn += 1
        # data-level: «err» cuenta como crítico en el filtro por nivel
        cuerpo.append(f'<tr data-level="{"crit" if nivel_fila == "err" else nivel_fila}">{"".join(celdas)}</tr>')
    abiertos = " con-filtros" if len(s.filas) >= FILAS_FILTROS_ABIERTOS else ""
    tabla = (f'<div class="table-wrap"><table id="t{i}" class="datos-t{abiertos}"><thead><tr class="cab">{cab}</tr>'
             f'<tr class="filtros">{"".join(filtros)}</tr></thead><tbody>{"".join(cuerpo)}</tbody></table></div>')
    vacia = '<p class="vacia">Sin datos en este módulo.</p>' if not s.filas else ""
    return tabla + vacia, hay_nivel, n_crit, n_warn


def _cifra(n: int, cls: str) -> str:
    return f'<span class="badge-cell {cls}">{n}</span>' if n else "0"


def _orden_por_categoria(secciones: List[Seccion]) -> List[Tuple[int, Seccion]]:
    """Agrupa por categoría respetando el orden en que aparece cada una (estable)."""
    if not any(s.categoria for s in secciones):
        return list(enumerate(secciones))
    orden: Dict[str, int] = {}
    for s in secciones:
        orden.setdefault(s.categoria or "Otros", len(orden))
    return sorted(enumerate(secciones), key=lambda x: (orden[x[1].categoria or "Otros"], x[0]))


# Modo equipo remoto (5.2.0, msys/ui/equipo_remoto.py): el informe lleva el nombre del equipo del cliente
EQUIPO_FORZADO = ""


def documento(titulo: str, secciones: List[Seccion], subtitulo: str = "", kpis: Optional[List[Kpi]] = None,
              bloque_inicial: str = "", titulo_bloque: str = "Resumen", indice: Optional[bool] = None,
              version: str = "", equipo: Optional[str] = None, fecha: Optional[str] = None) -> str:
    """HTML completo del informe. indice=None: con índice lateral si hay más de una sección."""
    if not version:
        try:
            from msys.version import NOMBRE_VERSION
            version = NOMBRE_VERSION
        except ImportError:
            version = "MASys"
    equipo = equipo if equipo is not None else (EQUIPO_FORZADO or socket.gethostname())
    fecha = fecha or time.strftime("%d/%m/%Y %H:%M")
    con_indice = (len(secciones) > 1) if indice is None else indice
    varias = len(secciones) > 1
    total = sum(len(s.filas) for s in secciones if s.contenido is None)
    con_cats = any(s.categoria for s in secciones)

    # ── módulos ─────────────────────────────────────────────────
    hay_nivel = False
    modulos = []        # (i, seccion, html, n_crit, n_warn)
    for i, s in _orden_por_categoria(secciones):
        aviso = f'<div class="alert alert-{s.aviso[0]}">{esc(s.aviso[1])}</div>' if s.aviso else ""
        nota = f'<p class="nota">{esc(s.nota)}</p>' if s.nota else ""
        nc = nw = 0
        if s.contenido is not None:
            cuerpo, n, botones = str(s.contenido), "", ""
        else:
            cuerpo, hn, nc, nw = _tabla(i, s)
            hay_nivel = hay_nivel or hn
            n = f'<span class="count" data-role="count">{len(s.filas)}</span>'
            marcas = ((f'<span class="marca crit" title="Filas críticas o con error">🔴 {nc}</span>' if nc else "")
                      + (f'<span class="marca warn" title="Filas con advertencia">⚠️ {nw}</span>' if nw else ""))
            n += marcas
            botones = ('<button type="button" class="mini" data-accion="filtros" title="Una caja de filtro por columna">Filtros</button>'
                       '<button type="button" class="mini" data-accion="csv" title="Descargar en CSV las filas que se ven">CSV</button>')
        plegada = "" if s.abierta else " plegada"
        cat = f' data-cat="{esc(s.categoria or "Otros")}"' if con_cats else ""
        html_sec = (f'<section id="s{i}" class="datos{plegada}"{cat}><h2 class="sec-titulo"><span class="flecha"></span>'
                    f'<span class="t">{esc(s.titulo)}</span>{n}<span class="hueco"></span>{botones}</h2>'
                    f'<div class="sec-cuerpo">{aviso}{cuerpo}{nota}</div></section>')
        modulos.append((i, s, html_sec, nc, nw))

    partes = []
    # Resumen: KPIs y bloque propio + tabla de módulos si hay varios
    resumen_modulos = ""
    if varias:
        filas_res = []
        for i, s, _h, nc, nw in modulos:
            reg = len(s.filas) if s.contenido is None else "—"
            filas_res.append(
                f'<tr data-ir="s{i}" title="Ver solo este módulo">'
                + (f"<td>{esc(s.categoria or 'Otros')}</td>" if con_cats else "")
                + f'<td class="mod">{esc(s.titulo)}</td><td class="num">{reg}</td>'
                f'<td class="num">{_cifra(nc, "crit")}</td><td class="num">{_cifra(nw, "warn")}</td></tr>')
        cab_cat = "<th>Categoría</th>" if con_cats else ""
        resumen_modulos = (f'<h3 class="res-t">Módulos del informe <small>(clic en uno para verlo solo)</small></h3>'
                           f'<div class="table-wrap res"><table class="resumen-mod"><thead><tr class="cab">{cab_cat}<th>Módulo</th>'
                           f'<th>Registros</th><th>Críticos</th><th>Avisos</th></tr></thead><tbody>{"".join(filas_res)}</tbody></table></div>')
    if bloque_inicial or kpis or resumen_modulos:
        tarjetas = "".join(f'<div class="kpi-card {k.nivel}"><div class="kpi-value">{esc(k.valor)}</div>'
                           f'<div class="kpi-label">{esc(k.etiqueta)}</div></div>' for k in (kpis or []))
        partes.append(f'<section id="resumen" class="bloque"><h2 class="sec-titulo"><span class="t">{esc(titulo_bloque)}</span></h2>'
                      f'<div class="sec-cuerpo">{"<div class=kpi-grid>" + tarjetas + "</div>" if tarjetas else ""}'
                      f'{bloque_inicial}{resumen_modulos}</div></section>')
    cat_actual = None
    for _i, s, html_sec, _nc, _nw in modulos:
        if con_cats and (s.categoria or "Otros") != cat_actual:
            cat_actual = s.categoria or "Otros"
            partes.append(f'<div class="cat-titulo" data-cat="{esc(cat_actual)}">{esc(cat_actual)}</div>')
        partes.append(html_sec)

    # ── índice lateral ──────────────────────────────────────────
    indice_html = ""
    if con_indice:
        def enlace(i, s, nc, nw):
            punto = '<i class="punto crit"></i>' if nc else ('<i class="punto warn"></i>' if nw else "")
            cuenta = f'<span class="badge" data-role="nav-count">{len(s.filas)}</span>' if s.contenido is None else ""
            return f'<li><a href="#s{i}" data-target="s{i}">{punto}<span class="nombre">{esc(s.titulo)}</span>{cuenta}</a></li>'
        cab = []
        if bloque_inicial or kpis or resumen_modulos:
            cab.append(f'<li><a href="#resumen" data-target="resumen"><span class="nombre">{esc(titulo_bloque)}</span></a></li>')
        if varias:
            cab.append('<li><a href="#" data-todos="1" class="todos"><span class="nombre">📚 Todos los módulos</span>'
                       f'<span class="badge">{len(secciones)}</span></a></li>')
        if con_cats:
            grupos: Dict[str, List[str]] = {}
            for i, s, _h, nc, nw in modulos:
                grupos.setdefault(s.categoria or "Otros", []).append(enlace(i, s, nc, nw))
            cuerpo_nav = "".join(
                f'<li class="grupo"><button type="button" class="grupo-t"><span>{esc(c)}</span><span class="badge">{len(ls)}</span></button>'
                f'<ul>{"".join(ls)}</ul></li>' for c, ls in grupos.items())
        else:
            cuerpo_nav = "".join(enlace(i, s, nc, nw) for i, s, _h, nc, nw in modulos)
        indice_html = (f'<nav><div class="nav-title">🛡️ MASys</div><div class="nav-subtitle">{esc(titulo)}</div>'
                       f'<ul>{"".join(cab)}{cuerpo_nav}</ul></nav>')

    # ── barra ───────────────────────────────────────────────────
    chips = ('<span class="chip active" data-level="todos">Todos</span><span class="chip" data-level="crit">🔴 Crítico</span>'
             '<span class="chip" data-level="warn">⚠️ Advertencia</span><span class="chip" data-level="ok">✅ OK</span>') if hay_nivel else ""
    selector = ""
    if varias:
        if con_cats:
            por_cat: Dict[str, List[str]] = {}
            for i, s, _h, _nc, _nw in modulos:
                por_cat.setdefault(s.categoria or "Otros", []).append(f'<option value="s{i}">{esc(s.titulo)}</option>')
            ops = "".join(f'<optgroup label="{esc(c)}">{"".join(o)}</optgroup>' for c, o in por_cat.items())
        else:
            ops = "".join(f'<option value="s{i}">{esc(s.titulo)}</option>' for i, s, _h, _nc, _nw in modulos)
        selector = (f'<select id="modulo" aria-label="Módulo"><option value="">📚 Todos los módulos</option>{ops}</select>'
                    '<button type="button" class="btn" id="ver-todos" title="Volver a ver todos los módulos">← Todos</button>')
    meta = f"Equipo: <strong>{esc(equipo)}</strong> &nbsp;·&nbsp; Generado: <strong>{esc(fecha)}</strong>"
    if varias:
        meta += f" &nbsp;·&nbsp; {len(secciones)} módulos"
    if total:
        meta += f" &nbsp;·&nbsp; {total} registro{'s' if total != 1 else ''}"
    sub = f'<div class="sub">{esc(subtitulo)}</div>' if subtitulo else ""
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(titulo)} · {esc(equipo)} · {esc(fecha)}</title>
<style>{_css(con_indice)}</style>
</head>
<body{' class="con-indice"' if con_indice else ''}>
{indice_html}
<header><h1>{esc(titulo)}</h1>{sub}<div class="meta">{meta}</div></header>
<div class="toolbar">
  {selector}
  <input type="search" id="buscar" placeholder="🔎 Buscar en todo el informe…" aria-label="Buscar">
  {chips}
  <button type="button" class="btn" data-accion="limpiar" title="Quitar búsqueda, nivel y filtros">✕ Limpiar</button>
  <button type="button" class="btn" data-accion="expandir" title="Desplegar todos los módulos">Expandir</button>
  <button type="button" class="btn" data-accion="contraer" title="Plegar todos los módulos">Contraer</button>
  <button type="button" class="btn primario" data-accion="imprimir" title="Imprimir o guardar en PDF">🖨️ Imprimir</button>
  <span class="result-count" id="cuenta"></span>
</div>
<main>
<div class="modo-aviso" id="modo-aviso"></div>
{''.join(partes)}
</main>
<footer>{esc(version)} &nbsp;·&nbsp; {esc(fecha)} &nbsp;·&nbsp; Aplicación desarrollada por Masoft.es</footer>
<script>{_JS}</script>
</body></html>"""


def guardar(ruta: str, contenido: str):
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(contenido)


def _css(con_indice: bool) -> str:
    m = "280px" if con_indice else "0"
    return f"""
:root{{--primary:#1a3a5c;--primary-light:#2c5f8a;--accent:#2979FF;--bg:#f5f7fa;--card:#fff;--text:#1a1a2e;--light:#5b6472;
--border:#dde3ea;--ok:#16a34a;--ok-bg:#e8f8ee;--warn:#ca8a04;--warn-bg:#fff8e6;--danger:#dc2626;--danger-bg:#fdf2f2;--info:#2563eb;--info-bg:#eff6ff;
color-scheme:light}}
*{{margin:0;padding:0;box-sizing:border-box}}
html{{scroll-behavior:smooth;scroll-padding-top:80px}}
body{{font-family:'Segoe UI',Arial,sans-serif;background:var(--bg);color:var(--text);font-size:13px}}
nav{{position:fixed;top:0;left:0;bottom:0;width:280px;background:var(--primary);overflow-y:auto;padding:20px 0;z-index:200}}
nav .nav-title{{color:#fff;font-size:16px;font-weight:600;padding:0 20px 4px}}
nav .nav-subtitle{{color:#9fb3c8;font-size:11px;padding:0 20px 14px;border-bottom:1px solid rgba(255,255,255,.12);margin-bottom:8px}}
nav ul{{list-style:none}}
nav a{{display:flex;gap:8px;align-items:center;text-decoration:none;color:#dce6f0;font-size:12.5px;padding:6px 20px 6px 28px;border-left:3px solid transparent}}
nav > ul > li > a{{padding-left:20px}}
nav a .nombre{{flex:1;min-width:0;overflow-wrap:anywhere}}
nav a:hover,nav a.active{{background:rgba(255,255,255,.08);border-left-color:var(--accent)}}
nav a.todos{{font-weight:600;color:#fff}}
nav .badge{{background:rgba(255,255,255,.15);color:#fff;border-radius:10px;padding:1px 7px;font-size:10px;flex:none}}
nav .punto{{width:8px;height:8px;border-radius:50%;flex:none}}nav .punto.crit{{background:#f87171}}nav .punto.warn{{background:#fbbf24}}
nav .grupo-t{{display:flex;width:100%;align-items:center;gap:8px;background:none;border:none;color:#9fb3c8;font:inherit;font-size:11px;
font-weight:700;letter-spacing:.6px;text-transform:uppercase;padding:12px 20px 4px;cursor:pointer;text-align:left}}
nav .grupo-t span:first-child{{flex:1}}nav .grupo-t::before{{content:'\\25BC';font-size:8px;transition:transform .15s}}
nav .grupo.cerrado .grupo-t::before{{transform:rotate(-90deg)}}nav .grupo.cerrado ul{{display:none}}
header{{background:linear-gradient(135deg,var(--primary),#0f2439);color:#fff;padding:28px 40px;margin-left:{m}}}
header h1{{font-size:25px;font-weight:300;margin-bottom:6px}}
header .sub{{font-size:13px;opacity:.9;margin-bottom:6px}}
header .meta{{font-size:12px;opacity:.85}}
.toolbar{{position:sticky;top:0;background:var(--card);border-bottom:1px solid var(--border);padding:10px 40px;margin-left:{m};display:flex;gap:8px;
align-items:center;flex-wrap:wrap;z-index:100;box-shadow:0 2px 6px rgba(0,0,0,.05)}}
.toolbar input[type=search]{{flex:1 1 200px;min-width:180px;padding:8px 12px;border:1px solid var(--border);border-radius:6px;font-size:13px;outline:none}}
.toolbar input[type=search]:focus,.toolbar select:focus{{border-color:var(--accent)}}
.toolbar select{{padding:7px 10px;border:1px solid var(--border);border-radius:6px;font-size:13px;font-family:inherit;max-width:300px;background:#fff;color:var(--primary)}}
#ver-todos{{display:none}}body.modo-modulo #ver-todos{{display:inline-block}}
.chip{{border:1px solid var(--border);background:#fff;color:var(--light);border-radius:16px;padding:5px 12px;font-size:12px;cursor:pointer;user-select:none;white-space:nowrap}}
.chip:hover{{background:#f1f4f8}}.chip.active{{background:var(--primary);color:#fff;border-color:var(--primary)}}
.btn,.mini{{border:1px solid var(--border);background:#fff;color:var(--primary);border-radius:6px;padding:6px 11px;font-size:12px;cursor:pointer;white-space:nowrap;font-family:inherit}}
.btn:hover,.mini:hover{{background:#eef3f9}}.btn.primario{{background:var(--primary);color:#fff;border-color:var(--primary)}}
.mini{{padding:3px 9px;font-size:11px;margin-left:6px}}.mini.on{{background:var(--accent);color:#fff;border-color:var(--accent)}}
.result-count{{font-size:12px;color:var(--light);white-space:nowrap}}
main{{margin-left:{m};max-width:1600px;padding:22px 40px}}
.modo-aviso{{display:none}}body.modo-modulo .modo-aviso{{display:block;font-size:12px;color:var(--light);margin:-6px 0 12px}}
.cat-titulo{{font-size:12px;font-weight:700;letter-spacing:.8px;text-transform:uppercase;color:var(--primary-light);margin:26px 0 10px;
padding-bottom:6px;border-bottom:2px solid var(--primary)}}
section{{background:var(--card);border-radius:10px;margin-bottom:18px;box-shadow:0 2px 8px rgba(0,0,0,.06);overflow:hidden;border:1px solid #edf0f4}}
section.oculta,.cat-titulo.oculta,body.modo-modulo .solo-fuera{{display:none}}
h2.sec-titulo{{display:flex;align-items:center;gap:8px;padding:12px 18px;background:#f8f9fb;border-bottom:1px solid var(--border);font-size:14px;
color:var(--primary);font-weight:600;cursor:pointer;user-select:none}}
h2.sec-titulo .hueco{{flex:1}}
h2.sec-titulo .flecha::before{{content:'\\25BC';font-size:10px;color:var(--light);display:inline-block;transition:transform .15s}}
section.plegada h2.sec-titulo .flecha::before{{transform:rotate(-90deg)}}
section.plegada .sec-cuerpo{{display:none}}section.plegada h2.sec-titulo{{border-bottom:none}}
h2 .count{{font-size:11px;font-weight:normal;color:var(--light);background:#eef2f7;border-radius:10px;padding:1px 8px}}
h2 .marca{{font-size:11px;font-weight:600;border-radius:10px;padding:1px 8px}}h2 .marca.crit{{background:#fde2e2;color:#7f1d1d}}
h2 .marca.warn{{background:#fff3cd;color:#7a5b00}}
.sec-cuerpo{{padding:0}}#resumen .sec-cuerpo{{padding:16px 18px}}
.kpi-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:12px;margin-bottom:16px}}
.kpi-card{{background:var(--card);border-radius:10px;padding:14px 16px;box-shadow:0 1px 6px rgba(0,0,0,.06);border-top:4px solid var(--accent);border:1px solid #edf0f4;border-top-width:4px}}
.kpi-card.crit{{border-top-color:var(--danger);background:var(--danger-bg)}}.kpi-card.warn{{border-top-color:var(--warn)}}.kpi-card.ok{{border-top-color:var(--ok)}}
.kpi-value{{font-size:25px;font-weight:600;color:var(--primary)}}.kpi-label{{font-size:11.5px;color:var(--light);margin-top:2px}}
.res-t{{font-size:13px;color:var(--primary);margin:18px 0 8px}}.res-t small{{font-weight:400;color:var(--light)}}
.table-wrap.res{{max-height:none;border:1px solid var(--border);border-radius:8px}}
table.resumen-mod tbody tr{{cursor:pointer}}table.resumen-mod td.num{{text-align:right;width:90px}}table.resumen-mod td.mod{{font-weight:600;color:var(--primary)}}
.alert{{border-radius:8px;padding:11px 15px;margin:12px 18px;font-size:12.5px;border-left:4px solid}}
#resumen .alert{{margin:0 0 12px}}
.alert-success{{background:var(--ok-bg);color:#0f5132;border-color:var(--ok)}}.alert-warning{{background:var(--warn-bg);color:#6b4f00;border-color:var(--warn)}}
.alert-danger{{background:var(--danger-bg);color:#5a1717;border-color:var(--danger)}}.alert-info{{background:var(--info-bg);color:#1e3a8a;border-color:var(--info)}}
.alert-note{{font-weight:400;margin-top:5px;font-size:12px;opacity:.9}}
.table-wrap{{overflow:auto;max-height:78vh}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
thead th{{position:sticky;z-index:2}}
thead tr.cab th{{top:0;background:var(--primary);color:#fff;padding:8px 11px;text-align:left;font-weight:600;white-space:nowrap;cursor:pointer;user-select:none}}
thead tr.cab th:hover{{background:var(--primary-light)}}
thead tr.cab th[data-orden=asc]::after{{content:' \\25B2';font-size:9px}}thead tr.cab th[data-orden=desc]::after{{content:' \\25BC';font-size:9px}}
thead tr.filtros{{display:none}}table.con-filtros thead tr.filtros{{display:table-row}}
thead tr.filtros th{{top:33px;background:#eef3f9;padding:4px 6px}}
thead tr.filtros input,thead tr.filtros select{{width:100%;min-width:70px;padding:4px 6px;border:1px solid var(--border);border-radius:4px;font-size:11.5px;font-family:inherit;background:#fff}}
thead tr.filtros .activo{{border-color:var(--accent);background:#eef4ff}}
tbody tr{{border-bottom:1px solid #f1f3f5}}tbody tr:nth-child(even){{background:#fafbfd}}tbody tr:hover{{background:#eef4fb}}
tbody tr.fuera{{display:none}}
td{{padding:6px 11px;color:#2f3640;vertical-align:top;white-space:nowrap}}
td.larga{{white-space:normal;min-width:260px;max-width:620px;overflow-wrap:anywhere}}
::highlight(busqueda){{background:#ffe066;color:#000}}
.badge-cell{{display:inline-block;padding:2px 7px;border-radius:4px;font-size:11px;font-weight:600}}
.badge-cell.crit{{background:#7f1d1d;color:#fff}}.badge-cell.err{{background:#fde2e2;color:#7f1d1d}}
.badge-cell.warn{{background:#fff3cd;color:#7a5b00}}.badge-cell.ok{{background:#d4edda;color:#155724}}
.findings-table td{{white-space:normal}}
tr.finding-row.crit td{{background:#fdf2f2}}tr.finding-row.warn td{{background:#fff8e6}}
.nota{{font-size:11.5px;color:var(--light);padding:10px 18px}}.vacia{{padding:14px 18px;color:var(--light)}}
.nota-icon{{cursor:help;margin-left:2px}}
footer{{text-align:center;padding:20px;color:var(--light);font-size:11px;margin-left:{m}}}
@media (max-width:900px){{nav{{display:none}}header,.toolbar,main,footer{{margin-left:0}}header,.toolbar,main{{padding-left:14px;padding-right:14px}}}}
@media print{{nav,.toolbar,.mini,thead tr.filtros,.modo-aviso{{display:none!important}}header,main,footer{{margin-left:0}}
section{{box-shadow:none;break-inside:auto}}section.plegada .sec-cuerpo{{display:block}}.table-wrap{{max-height:none;overflow:visible}}
thead th{{position:static}}tbody tr.fuera{{display:none}}body{{background:#fff}}.cat-titulo{{break-after:avoid}}}}
"""


_JS = r"""
(function(){
  var buscar = document.getElementById('buscar'), cuenta = document.getElementById('cuenta');
  var selMod = document.getElementById('modulo'), aviso = document.getElementById('modo-aviso');
  var secciones = Array.prototype.slice.call(document.querySelectorAll('section.datos'));
  var cats = Array.prototype.slice.call(document.querySelectorAll('.cat-titulo'));
  var nivel = 'todos', soloId = '';
  function norm(s){ return (s||'').toLowerCase(); }
  function filtrosDe(tabla){
    return Array.prototype.map.call(tabla.querySelectorAll('tr.filtros input, tr.filtros select'), function(el){
      return {sel: el.tagName === 'SELECT', v: el.tagName === 'SELECT' ? el.value : norm(el.value.trim()), el: el};
    });
  }

  function resaltar(q){
    if (!window.CSS || !CSS.highlights || typeof Highlight === 'undefined') return;
    CSS.highlights.delete('busqueda');
    if (!q) return;
    var rangos = [];
    secciones.forEach(function(sec){
      if (sec.classList.contains('oculta') || sec.classList.contains('plegada') || sec.classList.contains('solo-fuera')) return;
      var w = document.createTreeWalker(sec.querySelector('tbody') || sec, NodeFilter.SHOW_TEXT), n;
      while ((n = w.nextNode()) && rangos.length < 3000){
        var tr = n.parentNode.closest('tr'); if (tr && tr.classList.contains('fuera')) continue;
        var t = norm(n.nodeValue), p = t.indexOf(q);
        while (p >= 0){ var r = new Range(); r.setStart(n, p); r.setEnd(n, p + q.length); rangos.push(r); p = t.indexOf(q, p + q.length); }
      }
    });
    if (rangos.length) CSS.highlights.set('busqueda', new Highlight(...rangos));
  }

  function aplicar(){
    var q = norm(buscar.value.trim()), vis = 0, tot = 0, activo = !!q || nivel !== 'todos';
    secciones.forEach(function(sec){
      var tabla = sec.querySelector('table.datos-t');
      if (!tabla) { sec.classList.toggle('oculta', !!q && norm(sec.textContent).indexOf(q) < 0); return; }
      var filtros = filtrosDe(tabla);
      var filas = tabla.tBodies[0].rows, v = 0;
      for (var r = 0; r < filas.length; r++){
        var tr = filas[r], ok = (!q || norm(tr.textContent).indexOf(q) >= 0) && (nivel === 'todos' || tr.getAttribute('data-level') === nivel);
        for (var c = 0; ok && c < filtros.length; c++){
          var f = filtros[c]; if (!f.v) continue;
          var txt = tr.cells[c] ? (tr.cells[c].textContent || '').trim() : '';
          if (f.sel ? txt !== f.v : norm(txt).indexOf(f.v) < 0) ok = false;
        }
        tr.classList.toggle('fuera', !ok);
        if (ok) v++;
      }
      filtros.forEach(function(f){ f.el.classList.toggle('activo', !!f.v); });
      if (!sec.classList.contains('solo-fuera')) { tot += filas.length; vis += v; }
      var conFiltro = activo || filtros.some(function(f){ return f.v; });
      sec.classList.toggle('oculta', activo && v === 0);
      if (conFiltro && v > 0) sec.classList.remove('plegada');
      var c1 = sec.querySelector('[data-role=count]');
      if (c1) c1.textContent = conFiltro ? (v + ' de ' + filas.length) : filas.length;
      var nav = document.querySelector('nav a[data-target="' + sec.id + '"] [data-role=nav-count]');
      if (nav) nav.textContent = conFiltro ? v : filas.length;
    });
    // Títulos de categoría: fuera si no queda ningún módulo suyo a la vista
    cats.forEach(function(ct){
      var c = ct.getAttribute('data-cat');
      var alguno = secciones.some(function(s){ return s.getAttribute('data-cat') === c && !s.classList.contains('oculta') && !s.classList.contains('solo-fuera'); });
      ct.classList.toggle('oculta', !alguno);
    });
    cuenta.textContent = activo ? (vis + ' de ' + tot + ' filas') : '';
    resaltar(q);
  }

  // ── vista de un solo módulo ──
  function ver(id){
    soloId = id || '';
    document.body.classList.toggle('modo-modulo', !!soloId);
    var res = document.getElementById('resumen');
    if (res) res.classList.toggle('solo-fuera', !!soloId);
    secciones.forEach(function(s){ s.classList.toggle('solo-fuera', !!soloId && s.id !== soloId); if (s.id === soloId) s.classList.remove('plegada'); });
    if (selMod) selMod.value = soloId;
    document.querySelectorAll('nav a').forEach(function(a){ a.classList.toggle('active', !!soloId && a.getAttribute('data-target') === soloId); });
    if (aviso){
      var s = soloId && document.getElementById(soloId);
      aviso.textContent = s ? ('Viendo solo «' + (s.querySelector('.t') || {}).textContent + '». «← Todos» o «📚 Todos los módulos» para ver el resto.') : '';
    }
    aplicar();
    window.scrollTo(0, 0);
  }
  if (selMod) selMod.addEventListener('change', function(){ ver(selMod.value); });
  var vt = document.getElementById('ver-todos'); if (vt) vt.addEventListener('click', function(){ ver(''); });
  document.querySelectorAll('nav a[data-target]').forEach(function(a){
    a.addEventListener('click', function(e){
      var id = a.getAttribute('data-target');
      if (id === 'resumen') { ver(''); return; }
      if (selMod) { e.preventDefault(); ver(id); }
      else { var s = document.getElementById(id); if (s) s.classList.remove('plegada'); }
    });
  });
  document.querySelectorAll('nav a[data-todos]').forEach(function(a){ a.addEventListener('click', function(e){ e.preventDefault(); ver(''); }); });
  document.querySelectorAll('nav .grupo-t').forEach(function(b){ b.addEventListener('click', function(){ b.parentNode.classList.toggle('cerrado'); }); });
  document.querySelectorAll('tr[data-ir]').forEach(function(tr){ tr.addEventListener('click', function(){ ver(tr.getAttribute('data-ir')); }); });

  buscar.addEventListener('input', aplicar);
  document.querySelectorAll('.chip').forEach(function(ch){
    ch.addEventListener('click', function(){
      document.querySelectorAll('.chip').forEach(function(x){ x.classList.remove('active'); });
      ch.classList.add('active'); nivel = ch.getAttribute('data-level'); aplicar();
    });
  });
  document.querySelectorAll('tr.filtros input').forEach(function(i){ i.addEventListener('input', aplicar); i.addEventListener('click', function(e){ e.stopPropagation(); }); });
  document.querySelectorAll('tr.filtros select').forEach(function(i){ i.addEventListener('change', aplicar); i.addEventListener('click', function(e){ e.stopPropagation(); }); });
  document.querySelector('[data-accion=limpiar]').addEventListener('click', function(){
    buscar.value = ''; nivel = 'todos';
    document.querySelectorAll('.chip').forEach(function(x){ x.classList.toggle('active', x.getAttribute('data-level') === 'todos'); });
    document.querySelectorAll('tr.filtros input, tr.filtros select').forEach(function(i){ i.value = ''; });
    aplicar();
  });

  document.querySelectorAll('h2.sec-titulo').forEach(function(h){
    h.addEventListener('click', function(e){
      if (e.target.closest('button')) return;
      var s = h.parentNode; if (s.id !== 'resumen') s.classList.toggle('plegada');
    });
  });
  function todas(plegar){ secciones.forEach(function(s){ s.classList.toggle('plegada', plegar); }); aplicar(); }
  document.querySelector('[data-accion=expandir]').addEventListener('click', function(){ todas(false); });
  document.querySelector('[data-accion=contraer]').addEventListener('click', function(){ todas(true); });
  document.querySelector('[data-accion=imprimir]').addEventListener('click', function(){ window.print(); });

  document.querySelectorAll('[data-accion=filtros]').forEach(function(b){
    var t0 = b.closest('section').querySelector('table'); if (t0 && t0.classList.contains('con-filtros')) b.classList.add('on');
    b.addEventListener('click', function(){
      var sec = b.closest('section'), t = sec.querySelector('table'); sec.classList.remove('plegada');
      var on = t.classList.toggle('con-filtros'); b.classList.toggle('on', on);
      if (!on) { t.querySelectorAll('tr.filtros input, tr.filtros select').forEach(function(i){ i.value = ''; }); aplicar(); }
      else { var p = t.querySelector('tr.filtros input, tr.filtros select'); if (p) p.focus(); }
    });
  });
  document.querySelectorAll('[data-accion=csv]').forEach(function(b){
    b.addEventListener('click', function(){
      var sec = b.closest('section'), t = sec.querySelector('table'), lineas = [];
      // Como la exportación CSV de MASys (_csv_safe_cell): lo que empieza por = + - @ tabulador o retorno no llega a Excel como fórmula
      function celda(x){ var s = (x.textContent || '').trim(); if (/^[=+\-@\t\r]/.test(s)) s = "'" + s; return '"' + s.replace(/"/g, '""') + '"'; }
      lineas.push(Array.prototype.map.call(t.querySelectorAll('tr.cab th'), celda).join(';'));
      Array.prototype.forEach.call(t.tBodies[0].rows, function(tr){ if (!tr.classList.contains('fuera')) lineas.push(Array.prototype.map.call(tr.cells, celda).join(';')); });
      var blob = new Blob(['﻿' + lineas.join('\r\n')], {type: 'text/csv;charset=utf-8'});
      var a = document.createElement('a'); a.href = URL.createObjectURL(blob);
      a.download = ((sec.querySelector('.t') || {}).textContent || 'tabla').replace(/[^\wÀ-ſ -]+/g, '').trim() + '.csv';
      document.body.appendChild(a); a.click(); setTimeout(function(){ URL.revokeObjectURL(a.href); a.remove(); }, 500);
    });
  });

  function valor(td){
    var t = (td.textContent || '').trim();
    var f = t.match(/^(\d{2})\/(\d{2})\/(\d{4})(?:[ T,]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$/);
    if (f) return {t: 1, v: Date.UTC(+f[3], +f[2] - 1, +f[1], +(f[4] || 0), +(f[5] || 0), +(f[6] || 0))};
    // Números con unidad («45 %», «1,5 GB»); los tamaños se pasan a bytes para que 2,5 GB > 45 MB.
    // Los puntos solo se quitan si son de miles (1.234.567): versiones (2.4.1) e IPs se quedan como texto
    var UNIDAD = {b: 1, bytes: 1, kb: 1024, mb: 1048576, gb: 1073741824, tb: 1099511627776};
    var u = t.match(/\s*(bytes|B|KB|MB|GB|TB)$/i), mult = u ? UNIDAD[u[1].toLowerCase()] : 1;
    var n = t.replace(/\s*(%|ms|bytes|B|KB|MB|GB|TB|mAh|días?)$/i, '');
    if (/^-?\d{1,3}(\.\d{3})+(,\d+)?$/.test(n)) n = n.replace(/\./g, '');
    n = n.replace(',', '.');
    if (t !== '' && /^-?\d+(\.\d+)?$/.test(n)) return {t: 1, v: parseFloat(n) * mult};
    return {t: 0, v: t.toLowerCase()};
  }
  document.querySelectorAll('table').forEach(function(tabla){
    if (!tabla.tBodies.length) return;
    var ths = tabla.querySelectorAll('tr.cab th'), estado = {};
    ths.forEach(function(th, idx){
      th.title = 'Ordenar por esta columna';
      th.addEventListener('click', function(){
        var asc = !estado[idx]; estado = {}; estado[idx] = asc;
        ths.forEach(function(o){ o.removeAttribute('data-orden'); }); th.setAttribute('data-orden', asc ? 'asc' : 'desc');
        var tb = tabla.tBodies[0], filas = Array.prototype.slice.call(tb.rows);
        filas.sort(function(a, b){
          var x = valor(a.cells[idx] || a), y = valor(b.cells[idx] || b);
          if (x.t !== y.t) return x.t - y.t;
          return x.v < y.v ? (asc ? -1 : 1) : x.v > y.v ? (asc ? 1 : -1) : 0;
        });
        filas.forEach(function(tr){ tb.appendChild(tr); });
      });
    });
  });

  var enlaces = document.querySelectorAll('nav a[data-target]');
  if (enlaces.length && 'IntersectionObserver' in window){
    var obs = new IntersectionObserver(function(es){ es.forEach(function(e){ if (!e.isIntersecting || soloId) return;
      enlaces.forEach(function(a){ a.classList.remove('active'); });
      var l = document.querySelector('nav a[data-target="' + e.target.id + '"]'); if (l) l.classList.add('active'); }); },
      {rootMargin: '-20% 0px -70% 0px'});
    document.querySelectorAll('main > section').forEach(function(s){ obs.observe(s); });
  }
  window.addEventListener('beforeprint', function(){ secciones.forEach(function(s){ s.dataset.p = s.classList.contains('plegada') ? '1' : ''; s.classList.remove('plegada'); }); });
  window.addEventListener('afterprint', function(){ secciones.forEach(function(s){ if (s.dataset.p) s.classList.add('plegada'); }); });
  // informe.html#s3 abre directamente ese módulo solo
  var h = (location.hash || '').slice(1);
  if (selMod && /^s\d+$/.test(h) && document.getElementById(h)) ver(h); else aplicar();
})();
"""
