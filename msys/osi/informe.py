"""Genera un informe HTML autocontenido (sin dependencias externas) con el
resultado de un diagnóstico, listo para guardar o enviar a un cliente.

Dos modos:
- "tecnico": todas las pruebas, igual que se ven en la aplicación.
- "cliente": resumen con veredicto y hallazgos en lenguaje llano al
  principio, y la tabla sin las filas más técnicas (ver
  msys/osi/analisis.py -- basado 100% en los datos reales, nunca inventa).
"""
import base64
import html
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import List

from msys.osi.analisis import filtrar_para_cliente, generar_hallazgos, generar_veredicto
from msys.osi.models import ResultadoPrueba


def cargar_logo_data_uri(ruta: str) -> str:
    """Lee un fichero de imagen local y lo devuelve como data URI embebible
    en el HTML del informe -- así el informe sigue siendo un único fichero
    autocontenido, sin depender de una ruta externa que puede no existir en
    el equipo de quien lo reciba."""
    if not ruta or not Path(ruta).is_file():
        return ""
    tipo, _ = mimetypes.guess_type(ruta)
    if not tipo or not tipo.startswith("image/"):
        return ""
    try:
        datos = Path(ruta).read_bytes()
    except OSError:
        return ""
    return f"data:{tipo};base64,{base64.b64encode(datos).decode('ascii')}"

_COLOR_ESTADO = {
    "OK":    ("#DCFCE7", "#166534"),
    "AVISO": ("#FEF3C7", "#92400E"),
    "ERROR": ("#FEE2E2", "#991B1B"),
    "INFO":  ("#DBEAFE", "#1E40AF"),
}

_COLOR_SEVERIDAD = {
    "critico": ("#FEE2E2", "#991B1B", "#dc2626"),
    "aviso":   ("#FEF3C7", "#92400E", "#ca8a04"),
    "info":    ("#DBEAFE", "#1E40AF", "#2563eb"),
}

_COLOR_VEREDICTO = {
    "veredicto-ok": "#16a34a",
    "veredicto-aviso": "#ca8a04",
    "veredicto-critico": "#dc2626",
}


def _fila_html(r: ResultadoPrueba) -> str:
    bg, fg = _COLOR_ESTADO.get(r.resultado, ("#E5E7EB", "#374151"))
    ms = f"{r.valor_ms:.0f} ms" if r.valor_ms is not None else "—"
    detalle = html.escape(r.detalle)
    investigacion = r.extra.get("investigacion") if r.extra else None
    if investigacion:
        detalle += (
            f'<br><span class="nota-investigacion">🔍 Investigación: '
            f'{html.escape(investigacion.get("conclusion", ""))}</span>'
        )
    return f"""
    <tr>
        <td>{html.escape(r.capa_nombre)}</td>
        <td>{html.escape(r.prueba)}</td>
        <td><span class="badge" style="background:{bg}; color:{fg};">{html.escape(r.resultado)}</span></td>
        <td>{ms}</td>
        <td>{detalle}</td>
    </tr>"""


def _hallazgo_html(h) -> str:
    bg, fg, borde = _COLOR_SEVERIDAD[h.severidad]
    return f"""
    <div class="hallazgo" style="background:{bg}; border-left-color:{borde};">
        <div class="hallazgo-titulo" style="color:{fg};">{html.escape(h.titulo)}</div>
        <div class="hallazgo-texto">{html.escape(h.explicacion)}</div>
    </div>"""


def _seccion_cliente(resultados: List[ResultadoPrueba]) -> str:
    hallazgos = generar_hallazgos(resultados)
    veredicto = generar_veredicto(hallazgos)
    color_veredicto = _COLOR_VEREDICTO[veredicto["clase"]]

    if hallazgos:
        cuerpo_hallazgos = "\n".join(_hallazgo_html(h) for h in hallazgos)
    else:
        cuerpo_hallazgos = '<p class="sin-hallazgos">No se ha detectado ningún punto a destacar.</p>'

    return f"""
    <div class="veredicto" style="border-left-color:{color_veredicto};">
        <span class="veredicto-punto" style="background:{color_veredicto};"></span>
        <span class="veredicto-texto">{html.escape(veredicto['texto'])}</span>
    </div>
    <h2 class="titulo-seccion">Resumen para el cliente</h2>
    {cuerpo_hallazgos}
    <h2 class="titulo-seccion">Detalle de las comprobaciones relevantes</h2>
"""


def generar_html(
    destino: str, fecha_iso: str, resultados: List[ResultadoPrueba], modo: str = "tecnico",
    cliente: str = "", marca_nombre: str = "", marca_logo_data_uri: str = "",
) -> str:
    ok = sum(1 for r in resultados if r.resultado == "OK")
    aviso = sum(1 for r in resultados if r.resultado == "AVISO")
    error = sum(1 for r in resultados if r.resultado == "ERROR")

    try:
        fecha_fmt = datetime.fromisoformat(fecha_iso).strftime("%d/%m/%Y %H:%M:%S")
    except ValueError:
        fecha_fmt = fecha_iso

    es_cliente = modo == "cliente"
    resultados_tabla = filtrar_para_cliente(resultados) if es_cliente else resultados
    filas = "\n".join(_fila_html(r) for r in resultados_tabla)
    seccion_cliente = _seccion_cliente(resultados) if es_cliente else ""
    subtitulo = "Resumen para cliente" if es_cliente else "Informe técnico completo"

    nombre_pie = marca_nombre.strip() or "Masoft.es"
    pie_texto = f"{html.escape(nombre_pie)} — informe generado con MASys"
    logo_html = (
        f'<img src="{marca_logo_data_uri}" alt="Logo" class="logo-cabecera">'
        if marca_logo_data_uri else ""
    )
    linea_cliente = f"<p>Cliente: <strong>{html.escape(cliente)}</strong></p>" if cliente.strip() else ""

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Informe de diagnóstico — {html.escape(destino)}</title>
<style>
    :root {{
        --primary: #1a3a5c; --accent: #2979FF; --bg: #f7f9fc; --card-bg: #ffffff;
        --text: #1a1a2e; --text-light: #555; --border: #dde3ea;
    }}
    * {{ box-sizing: border-box; }}
    body {{
        margin: 0; padding: 30px; background: var(--bg); color: var(--text);
        font-family: 'Segoe UI', Arial, sans-serif;
    }}
    .contenedor {{ max-width: 1100px; margin: 0 auto; }}
    .cabecera {{
        background: linear-gradient(135deg, var(--primary), #0e2438);
        color: #fff; padding: 28px 32px; border-radius: 10px; margin-bottom: 22px;
        display: flex; align-items: center; gap: 20px;
    }}
    .logo-cabecera {{ max-height: 56px; max-width: 140px; border-radius: 6px; }}
    .cabecera h1 {{ margin: 0 0 6px 0; font-size: 26px; font-weight: 300; }}
    .cabecera .subtitulo {{ font-size: 12px; text-transform: uppercase; letter-spacing: .06em; color: #9fc0dd; margin-bottom: 6px; }}
    .cabecera p {{ margin: 2px 0; color: #cfe0f0; font-size: 13px; }}
    .resumen {{ display: flex; gap: 14px; margin-bottom: 22px; }}
    .tarjeta {{
        flex: 1; background: var(--card-bg); border: 1px solid var(--border);
        border-radius: 8px; padding: 16px; text-align: center;
    }}
    .tarjeta .numero {{ font-size: 28px; font-weight: 700; }}
    .tarjeta .etiqueta {{ color: var(--text-light); font-size: 12px; text-transform: uppercase; }}
    .ok .numero {{ color: #166534; }}
    .aviso .numero {{ color: #92400E; }}
    .error .numero {{ color: #991B1B; }}
    .veredicto {{
        display: flex; align-items: center; gap: 10px; background: var(--card-bg);
        border: 1px solid var(--border); border-left: 5px solid; border-radius: 8px;
        padding: 14px 18px; margin-bottom: 20px; font-size: 16px; font-weight: 600;
    }}
    .veredicto-punto {{ width: 12px; height: 12px; border-radius: 50%; flex: none; }}
    .titulo-seccion {{ font-size: 17px; color: var(--primary); margin: 22px 0 10px; }}
    .hallazgo {{
        border-left: 4px solid; border-radius: 6px; padding: 10px 14px; margin-bottom: 10px;
    }}
    .hallazgo-titulo {{ font-weight: 700; font-size: 14px; margin-bottom: 3px; }}
    .hallazgo-texto {{ font-size: 13px; color: var(--text); }}
    .sin-hallazgos {{ color: var(--text-light); font-style: italic; }}
    .nota-investigacion {{ color: var(--primary); font-size: 12px; font-style: italic; }}
    table {{
        width: 100%; border-collapse: collapse; background: var(--card-bg);
        border: 1px solid var(--border); border-radius: 8px; overflow: hidden;
    }}
    th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid var(--border); font-size: 13px; }}
    th {{ background: var(--primary); color: #fff; font-weight: 600; }}
    tr:nth-child(even) {{ background: #fafbfd; }}
    .badge {{ padding: 2px 10px; border-radius: 10px; font-weight: 700; font-size: 11px; }}
    footer {{ text-align: center; color: var(--text-light); font-size: 12px; margin-top: 26px; }}
    @media print {{ body {{ background: #fff; padding: 0; }} .cabecera {{ border-radius: 0; }} }}
</style>
</head>
<body>
<div class="contenedor">
    <div class="cabecera">
        {logo_html}
        <div>
            <div class="subtitulo">{html.escape(subtitulo)}</div>
            <h1>Informe de diagnóstico de red</h1>
            <p>Destino: <strong>{html.escape(destino)}</strong></p>
            {linea_cliente}
            <p>Generado: {html.escape(fecha_fmt)}</p>
        </div>
    </div>

    {seccion_cliente}

    <div class="resumen">
        <div class="tarjeta ok"><div class="numero">{ok}</div><div class="etiqueta">Correctas</div></div>
        <div class="tarjeta aviso"><div class="numero">{aviso}</div><div class="etiqueta">Avisos</div></div>
        <div class="tarjeta error"><div class="numero">{error}</div><div class="etiqueta">Errores</div></div>
    </div>

    <table>
        <thead>
            <tr><th>Capa</th><th>Prueba</th><th>Estado</th><th>Tiempo</th><th>Detalle</th></tr>
        </thead>
        <tbody>{filas}
        </tbody>
    </table>

    <footer>{pie_texto}</footer>
</div>
</body>
</html>
"""
