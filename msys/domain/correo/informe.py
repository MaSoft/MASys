# -*- coding: utf-8 -*-
"""Informe HTML de la comprobación de un dominio: cabecera con marca, cifras con icono y una tarjeta por comprobación con su
semáforo, detalles, registros y qué hacer. Un solo archivo, sin nada externo, listo para imprimir o mandar."""
import html

from msys.version import APP_VERSION

COL = {"ok": "#16A34A", "warn": "#CA8A04", "bad": "#DC2626", "info": "#2563EB"}
BG = {"ok": "#E8F6EC", "warn": "#FBF3DC", "bad": "#FCE8E8", "info": "#E6EEFC"}
TXT = {"ok": "Correcto", "warn": "Mejorable", "bad": "Problema", "info": "Información"}
# Iconos SVG (círculo con su símbolo)
ICON = {
    "ok": '<path d="M7 12.5l3.2 3.2L17 9" fill="none" stroke="#fff" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>',
    "warn": '<path d="M12 7v6.5" stroke="#fff" stroke-width="2.4" stroke-linecap="round"/><circle cx="12" cy="17" r="1.4" fill="#fff"/>',
    "bad": '<path d="M8.5 8.5l7 7M15.5 8.5l-7 7" stroke="#fff" stroke-width="2.4" stroke-linecap="round"/>',
    "info": '<circle cx="12" cy="7.6" r="1.4" fill="#fff"/><path d="M12 11v6" stroke="#fff" stroke-width="2.4" stroke-linecap="round"/>',
}


def _icon(level: str, size: int = 26) -> str:
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="12" '
            f'fill="{COL[level]}"/>{ICON[level]}</svg>')


def domain_html(rep) -> str:
    e = html.escape
    n = rep.counts()
    tiles = "".join(
        f'<div class="tile"><div class="ti">{_icon(k, 30)}</div><div><div class="tv">{n.get(k, 0)}</div><div class="tl">{TXT[k]}</div></div></div>'
        for k in ("ok", "warn", "bad", "info"))
    order = {"bad": 0, "warn": 1, "ok": 2, "info": 3}
    cards = []
    for c in rep.checks:
        items = "".join(f'<li><span class="dot" style="background:{COL.get(lv, "#888")}"></span>{e(t)}</li>' for lv, t in c.items)
        recs = "".join(f"<div>{e(r)}</div>" for r in c.records)
        cards.append(f"""
<section class="card" style="border-left-color:{COL[c.level]}">
  <header>{_icon(c.level)}<div class="h"><h2>{e(c.name)}</h2><p>{e(c.summary)}</p></div>
  <span class="pill" style="color:{COL[c.level]};background:{BG[c.level]}">{TXT[c.level]}</span></header>
  {f'<ul>{items}</ul>' if items else ''}
  {f'<div class="recs">{recs}</div>' if recs else ''}
  {f'<div class="adv"><b>Qué hacer:</b> {e(c.advice)}</div>' if c.advice else ''}
</section>""")
    worst = sorted(rep.checks, key=lambda c: order[c.level])
    first = [c for c in worst if c.level in ("bad", "warn")]
    todo = "".join(f"<li><b>{e(c.name)}</b>: {e(c.summary)}</li>" for c in first)
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Correo de {e(rep.domain)} · MASys</title>
<style>
:root {{ --primary:#1a3a5c; --bg:#f7f9fc; --card:#fff; --text:#1a1a2e; --muted:#555b66; --border:#dde3ea; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font:15px/1.5 "Segoe UI", system-ui, sans-serif; }}
.top {{ background:linear-gradient(135deg,#1a3a5c,#0d2038); color:#fff; padding:30px 40px 26px; }}
.top .brand {{ opacity:.8; font-size:13px; letter-spacing:.04em; text-transform:uppercase; }}
.top h1 {{ margin:6px 0 4px; font-weight:300; font-size:32px; }}
.top .sub {{ opacity:.85; }}
main {{ max-width:1040px; margin:0 auto; padding:24px 24px 40px; }}
.tiles {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(200px,1fr)); gap:14px; margin:-46px 0 22px; }}
.tile {{ background:var(--card); border:1px solid var(--border); border-radius:14px; padding:14px 16px; display:flex; gap:12px;
  align-items:center; box-shadow:0 2px 10px rgba(26,58,92,.08); }}
.tv {{ font-size:24px; font-weight:600; line-height:1; }} .tl {{ color:var(--muted); font-size:13px; }}
.todo {{ background:var(--card); border:1px solid var(--border); border-radius:14px; padding:14px 20px; margin-bottom:18px; }}
.todo h2 {{ margin:0 0 6px; font-size:16px; color:var(--primary); }} .todo ul {{ margin:0; padding-left:20px; }}
.card {{ background:var(--card); border:1px solid var(--border); border-left:5px solid; border-radius:14px; padding:16px 20px;
  margin-bottom:14px; break-inside:avoid; }}
.card header {{ display:flex; gap:12px; align-items:flex-start; }}
.card .h {{ flex:1; }} .card h2 {{ margin:0; font-size:17px; color:var(--primary); }} .card p {{ margin:2px 0 0; }}
.pill {{ border-radius:20px; padding:2px 12px; font-size:12px; font-weight:600; white-space:nowrap; }}
ul {{ list-style:none; padding:0; margin:10px 0 0 38px; }} li {{ margin:3px 0; }}
.dot {{ display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:8px; }}
.recs {{ margin:10px 0 0 38px; background:#0d1117; color:#e6edf3; border-radius:10px; padding:10px 14px;
  font:12.5px/1.55 Consolas, monospace; overflow-wrap:anywhere; }}
.adv {{ margin:10px 0 0 38px; background:#f1f4f9; border-radius:10px; padding:10px 14px; }}
footer {{ text-align:center; color:var(--muted); font-size:12px; padding:10px 0 30px; }}
@media print {{ body {{ font-size:10pt; background:#fff; }} .tiles {{ margin-top:12px; }} .top {{ -webkit-print-color-adjust:exact;
  print-color-adjust:exact; }} .recs {{ -webkit-print-color-adjust:exact; print-color-adjust:exact; }} }}
</style></head><body>
<div class="top"><div class="brand">MASys · informe de correo</div><h1>{e(rep.domain)}</h1>
<div class="sub">Comprobado el {e(rep.when)} · DNS, MX, SPF, DMARC, DKIM, MTA-STS, TLS-RPT, BIMI, inverso, servidor y listas negras</div></div>
<main>
<div class="tiles">{tiles}</div>
{f'<div class="todo"><h2>Lo primero que revisar</h2><ul>{todo}</ul></div>' if todo else ''}
{''.join(cards)}
</main>
<footer>MASys {APP_VERSION} · solo se han leído registros públicos y saludado al servidor de correo, sin enviar nada · Masoft.es</footer>
</body></html>"""
