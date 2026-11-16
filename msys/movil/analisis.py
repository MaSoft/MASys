# -*- coding: utf-8 -*-
"""Análisis (sin Qt) de los informes que manda MASysMov.

Formato del informe (lo genera MASYSMOV/.../link/Snapshot.kt; versión 1):
  formato="masysmov_informe", version, generado (ms), app ("MASysMov x.y.z"),
  dispositivo{id,nombre,fabricante,modelo,android,sdk,parche,soc,nucleos,encendidoMs,acceso},
  bateria{nivel,estado,salud,temperatura,ciclos,capacidadMah,cargando}, memoria{total,disponible},
  almacenamiento{total,libre}, red{tipo,medida,vpn,wifi{ssid,rssi,enlaceMbps,frecuenciaMhz,seguridad,ip,puerta,dns}},
  apps[{pkg,nombre,version,sistema,activa,instalador,instalada,actualizada,targetSdk,tamano,permisos[],internet,
        ultimoUso,primerPlanoMs,bloqueada}],
  auditoria[{clave,titulo,detalle,riesgo,apps[]}], cuentas[{nombre,tipo}],
  conexiones{dias,apps[{pkg,consultas,dominios,bloqueadas,ultima,top[{d,n,b}]}]},
  trafico{dias,total{wifi,movil},apps[{pkg,wifi,movil}]}, velocidad[{ts,bajada,subida,ping,jitter,red,servidor}],
  alertas[{ts,tipo,pkg,titulo,texto}]
  Desde MASysMov 1.2.4 (bloques opcionales; los informes anteriores no los traen):
  bateriaExtra{tension,tecnologia,enchufe,corrienteMa,gastoHabitualPorHora,horasRestantes,historial[[ts,nivel,cargando]]},
  bateriaApps{totalMah,apps[{pkg,mah,pct}]}, dnsPrivado{modo,servidor,texto,activo,servidorActivo},
  rastreadores{dias,cortar,excepciones[],consultas,cortadas,servicios[{servicio,consultas,cortadas,apps,seCorta}],
               apps[{pkg,consultas,cortadas,servicios}]},
  procesos{cpuTotal,procesos,termico,apps[{pkg,nombre,memoriaKb,cpu,procesos}]},
  bluetooth{activo,nombre,dispositivos[{nombre,tipo,conectado,bateria}]},
  espacio{total,libre,liberado{total,ultimo,fecha},temporales,apps[{pkg,app,datos,cache,total}],sinUsar[{pkg,ultimoUso,bytes}],
          copias{carpeta,numero,bytes,lista[{pkg,app,version,creada,bytes,contenido,apks}]},eliminados{numero,bytes,dias},
          recorrido{fecha,archivos,bytes,sinLeer,carpetas[{ruta,bytes,archivos}],tipos[{tipo,bytes,archivos}],
                    grandes[{ruta,bytes,modificado,tipo}],mensajeria[{app,carpeta,ruta,bytes,archivos}]}}
  Desde MASysMov 1.3.2 (MASys 5.3.0):
  hardware[{id,titulo,datos[[dato,valor]]}],
  bateriaSalud{ciclos,capacidadOriginal,capacidadActual,estimada,salud},
  sim{permiso,ubicacion,lineas[{ranura,nombre,operador,pais,red,generacion,itinerancia,datos,dbm,nivel,calidad,banda,antena}]},
  wifiExtra{encendida,estandar,capacidades[{nombre,tiene}],actual{ssid,canal,frecuenciaMhz,estandar,subidaMbps,bajadaMbps},
            cercanas[{ssid,rssi,canal,frecuenciaMhz,seguridad,estandar}],consejo[{banda,mejor,actual,cambiar}],
            guardadas[{ssid,seguridad}] | null},
  pruebas[{id,grupo,titulo,resultado,fecha,detalle}]
  Aparte (encargo «datos», no va en el informe): datos_personales{nivel,fecha,contactos{permiso,numero,lista},
  llamadas{permiso,numero,lista},sms{permiso,numero,conversaciones,lista}} (MASysMov link/PersonalPayload.kt).

Todo lo que se dice sale de los datos del informe: nada se inventa."""

import time
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

OK, AVISO, ERROR, INFO = "OK", "AVISO", "ERROR", "INFO"

# Tiendas de confianza (instalador). Lo instalado por otra vía se marca «fuera de tienda».
TIENDAS = {
    "com.android.vending": "Play Store", "com.sec.android.app.samsungapps": "Galaxy Store", "com.huawei.appmarket": "AppGallery",
    "com.xiaomi.market": "GetApps (Xiaomi)", "com.xiaomi.mipicks": "GetApps (Xiaomi)", "com.amazon.venezia": "Amazon Appstore",
    "com.heytap.market": "App Market (OPPO)", "com.oppo.market": "App Market (OPPO)", "com.bbk.appstore": "V-Appstore (vivo)",
    "com.google.android.packageinstaller": "APK a mano", "com.android.packageinstaller": "APK a mano",
}
_TIENDAS_DE_CONFIANZA = {k for k, v in TIENDAS.items() if v != "APK a mano"}

# Dominios de publicidad y analítica conocidos (sufijo -> servicio). Lista corta y conservadora.
RASTREADORES = {
    "doubleclick.net": "Google (publicidad)", "googlesyndication.com": "Google (publicidad)", "googleadservices.com": "Google (publicidad)",
    "google-analytics.com": "Google Analytics", "app-measurement.com": "Firebase Analytics", "crashlytics.com": "Crashlytics",
    "graph.facebook.com": "Facebook SDK", "an.facebook.com": "Facebook Audience", "adjust.com": "Adjust", "adjust.world": "Adjust",
    "appsflyer.com": "AppsFlyer", "appsflyersdk.com": "AppsFlyer", "branch.io": "Branch", "onesignal.com": "OneSignal",
    "amplitude.com": "Amplitude", "mixpanel.com": "Mixpanel", "segment.io": "Segment", "segment.com": "Segment",
    "unityads.unity3d.com": "Unity Ads", "applovin.com": "AppLovin", "applvn.com": "AppLovin", "supersonicads.com": "ironSource",
    "ironsrc.mobi": "ironSource", "vungle.com": "Vungle", "chartboost.com": "Chartboost", "moatads.com": "Moat",
    "scorecardresearch.com": "comScore", "adcolony.com": "AdColony", "inmobi.com": "InMobi", "mopub.com": "MoPub",
    "taboola.com": "Taboola", "outbrain.com": "Outbrain", "criteo.com": "Criteo", "criteo.net": "Criteo",
    "hotjar.com": "Hotjar", "clarity.ms": "Microsoft Clarity", "bat.bing.com": "Bing Ads", "analytics.tiktok.com": "TikTok (analítica)",
    "ads.tiktok.com": "TikTok (publicidad)", "ads.linkedin.com": "LinkedIn (publicidad)", "pangle.io": "Pangle (publicidad)",
    "startappservice.com": "StartApp", "tapjoy.com": "Tapjoy", "flurry.com": "Flurry", "yandex.ru/ads": "Yandex (publicidad)",
}


def rastreador(dominio: str) -> str:
    d = (dominio or "").lower().rstrip(".")
    for sufijo, servicio in RASTREADORES.items():
        if d == sufijo or d.endswith("." + sufijo):
            return servicio
    return ""


def _fecha(ms) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000).strftime("%d/%m/%Y")
    except (TypeError, ValueError, OSError):
        return ""


def _fecha_hora(ms) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000).strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError, OSError):
        return ""


def tam(n) -> str:
    n = float(n or 0)
    for u in ("bytes", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.0f} {u}" if u == "bytes" else f"{n:,.1f} {u}".replace(",", "X").replace(".", ",").replace("X", ".")
        n /= 1024
    return str(n)


def num(v, decimales: int = 1) -> str:
    """31.5 -> '31,5' (coma decimal)."""
    try:
        return f"{float(v):.{decimales}f}".replace(".", ",")
    except (TypeError, ValueError):
        return str(v or "")


def _pct(usado, total) -> float:
    return 100.0 * usado / total if total else 0.0


def dias_desde_parche(parche: str, hoy: Optional[date] = None) -> Optional[int]:
    try:
        return ((hoy or date.today()) - datetime.strptime(parche[:10], "%Y-%m-%d").date()).days
    except (TypeError, ValueError):
        return None


def nombre_app(inf: dict, pkg: str) -> str:
    for a in inf.get("apps", []):
        if a.get("pkg") == pkg:
            return a.get("nombre") or pkg
    if pkg.startswith("sistema:"):
        return "Sistema"
    return pkg


# ── Ficha ───────────────────────────────────────────────────────────
def ficha(inf: dict) -> List[Tuple[str, str, str]]:
    d = inf.get("dispositivo") or {}
    b = inf.get("bateria") or {}
    m = inf.get("memoria") or {}
    s = inf.get("almacenamiento") or {}
    r = inf.get("red") or {}
    filas = [("Móvil", f"{d.get('fabricante', '')} {d.get('modelo', '')}".strip(), INFO),
             ("Android", f"{d.get('android', '')} (API {d.get('sdk', '')})", AVISO if (d.get("sdk") or 99) < 31 else OK)]
    dias = dias_desde_parche(d.get("parche", ""))
    if d.get("parche"):
        filas.append(("Parche de seguridad", f"{d['parche']}" + (f" (hace {dias} días)" if dias is not None else ""),
                      ERROR if dias and dias > 180 else AVISO if dias and dias > 90 else OK))
    filas += [("Procesador", f"{d.get('soc', '')} · {d.get('nucleos', '')} núcleos", INFO),
              ("Nivel de acceso de MASysMov", d.get("acceso", ""), INFO),
              ("Encendido desde hace", f"{int((d.get('encendidoMs') or 0) / 3_600_000)} h", INFO)]
    if b:
        filas.append(("Batería", f"{b.get('nivel', '')} % · {b.get('estado', '')} · salud {b.get('salud', '')} · {num(b.get('temperatura'))} ºC"
                      + (f" · {b['ciclos']} ciclos" if b.get("ciclos") else ""),
                      AVISO if str(b.get("salud", "")).lower() not in ("buena", "good", "") or (b.get("temperatura") or 0) > 42 else OK))
    if m:
        filas.append(("Memoria", f"{tam(m.get('total', 0) - m.get('disponible', 0))} usados de {tam(m.get('total'))}", INFO))
    if s:
        p = _pct(s.get("total", 0) - s.get("libre", 0), s.get("total", 0))
        filas.append(("Almacenamiento", f"{tam(s.get('libre'))} libres de {tam(s.get('total'))} ({p:.0f} % usado)",
                      ERROR if p > 95 else AVISO if p > 85 else OK))
    if r:
        w = r.get("wifi") or {}
        filas.append(("Red", f"{r.get('tipo', '')}" + (f" · {w.get('ssid', '')} ({w.get('rssi', '')} dBm, {w.get('enlaceMbps', '')} Mbps, "
                                                        f"{w.get('seguridad', '')})" if w else "") + (" · VPN activa" if r.get("vpn") else ""),
                      AVISO if w and str(w.get("seguridad", "")).lower() in ("abierta", "open", "wep") else INFO))
        if w.get("ip"):
            filas.append(("IP / puerta / DNS", f"{w.get('ip')} · {w.get('puerta', '')} · {', '.join(w.get('dns', []))}", INFO))
    bx = inf.get("bateriaExtra") or {}
    if bx:
        partes = [f"{bx.get('tension')} mV" if bx.get("tension") else "", bx.get("tecnologia") or ""]
        if bx.get("gastoHabitualPorHora") is not None:
            partes.append(f"gasto habitual {num(bx.get('gastoHabitualPorHora'))} % por hora")
        if bx.get("horasRestantes") is not None:
            partes.append(f"quedan unas {num(bx.get('horasRestantes'))} h")
        filas.append(("Batería (detalle)", " · ".join(p for p in partes if p), INFO))
    dp = inf.get("dnsPrivado") or {}
    if dp:
        srv = dp.get("servidor") or ""
        filas.append(("DNS privado", (dp.get("texto") or "") + (f" ({srv})" if srv else ""), INFO))
    bt = inf.get("bluetooth") or {}
    if bt:
        disp = bt.get("dispositivos") or []
        nombres = ", ".join((d.get("nombre") or "") + (" (conectado)" if d.get("conectado") else "") for d in disp[:10])
        filas.append(("Bluetooth", ("activado" if bt.get("activo") else "apagado") + f" · {len(disp)} emparejados" + (f": {nombres}" if nombres else ""), INFO))
    pr = inf.get("procesos") or {}
    if pr:
        filas.append(("Procesos", f"{pr.get('procesos', '')} en marcha · CPU {num(pr.get('cpuTotal'))} % · temperatura: {pr.get('termico', '')}", INFO))
    esp = inf.get("espacio") or {}
    if "temporales" in esp:
        filas.append(("Temporales de las apps", tam(esp.get("temporales")), AVISO if (esp.get("temporales") or 0) > 2 * 1024 ** 3 else INFO))
    lib = esp.get("liberado") or {}
    if lib.get("total"):
        cuando = f" · último {tam(lib.get('ultimo'))} el {_fecha(lib.get('fecha'))}" if lib.get("fecha") else ""
        filas.append(("Liberado con MASysMov", f"{tam(lib.get('total'))} en total{cuando}", INFO))
    eli = esp.get("eliminados") or {}
    if eli.get("numero"):
        filas.append(("Eliminados recuperables", f"{eli.get('numero')} archivos · {tam(eli.get('bytes'))} · se guardan {eli.get('dias')} días", INFO))
    apps = inf.get("apps", [])
    usuario = [a for a in apps if not a.get("sistema")]
    filas.append(("Apps", f"{len(usuario)} instaladas por el usuario · {len(apps)} en total", INFO))
    cuentas = inf.get("cuentas", [])
    if cuentas:
        filas.append(("Cuentas", ", ".join(f"{c.get('nombre', '')} ({c.get('tipo', '')})" for c in cuentas[:12]), INFO))
    filas.append(("Informe", f"{inf.get('app', '')} · {_fecha_hora(inf.get('generado'))}", INFO))
    return filas


# ── Apps ────────────────────────────────────────────────────────────
def notas_app(a: dict, ahora_ms: Optional[int] = None) -> Tuple[str, str]:
    """(notas, estado) de una app: fuera de tienda, permisos sensibles, antigua, sin usar."""
    ahora_ms = ahora_ms or int(time.time() * 1000)
    notas, peor = [], OK
    if not a.get("sistema"):
        inst = a.get("instalador") or ""
        if inst not in _TIENDAS_DE_CONFIANZA:
            notas.append("fuera de tienda" + (f" ({TIENDAS.get(inst, inst)})" if inst else ""))
            peor = AVISO
        if (a.get("targetSdk") or 99) < 29:
            notas.append(f"hecha para Android antiguo (API {a.get('targetSdk')})")
            peor = AVISO
        if a.get("ultimoUso") and ahora_ms - a["ultimoUso"] > 60 * 86_400_000:
            notas.append(f"sin usar desde {_fecha(a['ultimoUso'])}")
    sensibles = a.get("permisos") or []
    if len(sensibles) >= 4 and not a.get("sistema"):
        notas.append(f"{len(sensibles)} permisos sensibles")
        peor = AVISO
    if a.get("bloqueada"):
        notas.append("sin internet (bloqueada en MASysMov)")
    return "; ".join(notas), peor


def filas_apps(inf: dict, solo_usuario: bool = True) -> List[dict]:
    out = []
    ahora = int(inf.get("generado") or time.time() * 1000)
    for a in inf.get("apps", []):
        if solo_usuario and a.get("sistema"):
            continue
        notas, estado = notas_app(a, ahora)
        out.append(dict(a, notas=notas, estado=estado, origen=TIENDAS.get(a.get("instalador") or "", a.get("instalador") or "—")))
    return sorted(out, key=lambda x: (x["estado"] != AVISO, (x.get("nombre") or "").lower()))


# ── Conexiones ──────────────────────────────────────────────────────
def filas_conexiones(inf: dict) -> List[dict]:
    """Una fila por app y dominio (los más consultados), con el rastreador si lo es."""
    out = []
    for c in (inf.get("conexiones") or {}).get("apps", []):
        app = nombre_app(inf, c.get("pkg", ""))
        for t in c.get("top", []):
            out.append({"app": app, "pkg": c.get("pkg", ""), "dominio": t.get("d", ""), "consultas": t.get("n", 0),
                        "bloqueadas": t.get("b", 0), "rastreador": rastreador(t.get("d", ""))})
    return sorted(out, key=lambda x: -x["consultas"])


def resumen_rastreadores(inf: dict) -> List[Tuple[str, int, List[str]]]:
    """[(app, consultas a rastreadores, servicios)] ordenado de más a menos."""
    por_app: Dict[str, list] = {}
    for f in filas_conexiones(inf):
        if f["rastreador"]:
            e = por_app.setdefault(f["app"], [0, set()])
            e[0] += f["consultas"]
            e[1].add(f["rastreador"])
    return sorted(((a, n, sorted(s)) for a, (n, s) in por_app.items()), key=lambda x: -x[1])


# ── Seguridad ───────────────────────────────────────────────────────
def hallazgos(inf: dict) -> List[Tuple[str, str, str]]:
    """[(estado, título, detalle)]: auditoría del propio móvil + lo que se deduce del informe."""
    out = []
    riesgo = {"ALTO": ERROR, "HIGH": ERROR, "MEDIO": AVISO, "MEDIUM": AVISO}
    for h in inf.get("auditoria", []):
        apps = ", ".join(nombre_app(inf, p) for p in h.get("apps", [])[:8])
        out.append((riesgo.get(str(h.get("riesgo", "")).upper(), INFO), h.get("titulo", ""),
                    (h.get("detalle", "") + (f" — {apps}" if apps else "")).strip(" —")))
    d = inf.get("dispositivo") or {}
    dias = dias_desde_parche(d.get("parche", ""))
    if dias is not None and dias > 90:
        out.append((ERROR if dias > 180 else AVISO, "Parche de seguridad antiguo",
                    f"El último parche es del {d.get('parche')} (hace {dias} días). Buscar actualizaciones del sistema; si el fabricante "
                    "ya no las publica, el móvil queda expuesto."))
    fuera = [a for a in filas_apps(inf) if "fuera de tienda" in a["notas"]]
    if fuera:
        out.append((AVISO, f"{len(fuera)} apps instaladas fuera de una tienda",
                    ", ".join(a.get("nombre", a["pkg"]) for a in fuera[:12]) + (" …" if len(fuera) > 12 else "")))
    rastreo = resumen_rastreadores(inf)
    if rastreo:
        n = len(rastreo)
        out.append((INFO, f"{n} app contacta con servicios de publicidad o analítica" if n == 1 else
                    f"{n} apps contactan con servicios de publicidad o analítica",
                    "; ".join(f"{a}: {', '.join(s)}" for a, _n, s in rastreo[:6])))
    s = inf.get("almacenamiento") or {}
    if s and _pct(s.get("total", 0) - s.get("libre", 0), s.get("total", 0)) > 90:
        out.append((AVISO, "Almacenamiento casi lleno", f"Quedan {tam(s.get('libre'))}: el móvil irá lento y puede no actualizarse."))
    w = (inf.get("red") or {}).get("wifi") or {}
    if str(w.get("seguridad", "")).lower() in ("abierta", "open", "wep"):
        out.append((AVISO, "Conectado a una Wi-Fi sin cifrar", f"«{w.get('ssid', '')}»: cualquiera cercano puede ver el tráfico sin cifrar."))
    ras = inf.get("rastreadores") or {}
    if ras.get("consultas") and not ras.get("cortar"):
        out.append((INFO, "Rastreadores sin cortar", f"{ras.get('consultas')} consultas a servicios de publicidad o analítica en "
                    f"{ras.get('dias', 7)} días. MASysMov puede cortarlos con su opción de cortar rastreadores."))
    esp = inf.get("espacio") or {}
    if (esp.get("temporales") or 0) > 2 * 1024 ** 3:
        out.append((INFO, "Muchos temporales", f"Las apps guardan {tam(esp.get('temporales'))} de temporales: se pueden vaciar en MASysMov "
                    "(Equipo › Espacio › Liberar espacio › Temporales)."))
    sin = esp.get("sinUsar") or []
    if len(sin) >= 3:
        out.append((INFO, f"{len(sin)} apps sin usar en 30 días", f"Ocupan {tam(sum(u.get('bytes', 0) for u in sin))}: "
                    + ", ".join(nombre_app(inf, u.get("pkg", "")) for u in sin[:8]) + (" …" if len(sin) > 8 else "")))
    bs = inf.get("bateriaSalud") or {}
    if bs.get("salud") is not None and int(bs["salud"]) < 80:
        out.append((AVISO, "Batería gastada", f"Le queda el {bs['salud']} % de su capacidad original"
                    + (f" ({bs.get('capacidadActual')} de {bs.get('capacidadOriginal')} mAh)" if bs.get("capacidadOriginal") else "")
                    + ": por debajo del 80 % conviene cambiarla."))
    fallos = [p.get("titulo", "") for p in inf.get("pruebas", []) or [] if p.get("resultado") == "Fallo"]
    if fallos:
        out.append((AVISO, f"{len(fallos)} prueba(s) del móvil con fallo", ", ".join(fallos)))
    for c in (inf.get("wifiExtra") or {}).get("consejo", []) or []:
        if c.get("cambiar") and c.get("actual") is not None:
            out.append((INFO, f"Wi-Fi de {c.get('banda', '')} en un canal saturado",
                        f"Está en el canal {c.get('actual')}; el {c.get('mejor')} está mucho más libre (se cambia en el router)."))
    if not out:
        out.append((OK, "Sin hallazgos", "No se ha detectado nada que revisar en este informe."))
    orden = {ERROR: 0, AVISO: 1, INFO: 2, OK: 3}
    return sorted(out, key=lambda x: orden.get(x[0], 9))


# ── Cambios entre dos informes ──────────────────────────────────────
def cambios(nuevo: dict, anterior: Optional[dict]) -> List[Tuple[str, str, str]]:
    """[(tipo, app, detalle)] entre el informe anterior y este."""
    if not anterior:
        return []
    a_old = {a["pkg"]: a for a in anterior.get("apps", []) if not a.get("sistema")}
    a_new = {a["pkg"]: a for a in nuevo.get("apps", []) if not a.get("sistema")}
    out = []
    for p in sorted(set(a_new) - set(a_old)):
        out.append(("App nueva", a_new[p].get("nombre", p), f"versión {a_new[p].get('version', '')} · {TIENDAS.get(a_new[p].get('instalador') or '', 'fuera de tienda')}"))
    for p in sorted(set(a_old) - set(a_new)):
        out.append(("App quitada", a_old[p].get("nombre", p), ""))
    for p in sorted(set(a_new) & set(a_old)):
        nuevos = sorted(set(a_new[p].get("permisos") or []) - set(a_old[p].get("permisos") or []))
        if nuevos:
            out.append(("Permisos nuevos", a_new[p].get("nombre", p), ", ".join(nuevos)))
        if a_new[p].get("version") != a_old[p].get("version"):
            out.append(("Actualizada", a_new[p].get("nombre", p), f"{a_old[p].get('version', '')} → {a_new[p].get('version', '')}"))
    return out


# ── Evolución (todos los informes de un móvil) ─────────────────────
def evolucion(informes: List[dict]) -> List[List[str]]:
    """Una fila por informe (del más reciente al más antiguo)."""
    filas = []
    for reg in informes:
        inf = reg["informe"]
        b = inf.get("bateria") or {}
        s = inf.get("almacenamiento") or {}
        v = (inf.get("velocidad") or [{}])[0] if inf.get("velocidad") else {}
        filas.append([_fecha_hora(inf.get("generado")), f"{b.get('nivel', '')} %", str(b.get("salud", "")),
                      f"{num(b.get('temperatura'))} ºC", str(b.get("ciclos") or "—"),
                      f"{_pct(s.get('total', 0) - s.get('libre', 0), s.get('total', 0)):.0f} %" if s else "",
                      str(len([a for a in inf.get("apps", []) if not a.get("sistema")])),
                      f"{v.get('bajada', 0):.0f} / {v.get('subida', 0):.0f} Mbps" if v else "—",
                      reg.get("origen", "")])
    return filas


# ── Informe HTML ────────────────────────────────────────────────────
# ── Bloques de MASysMov 1.2.4: espacio, batería, rastreadores, procesos ──
def _hora(ms) -> str:
    try:
        return datetime.fromtimestamp(int(ms) / 1000).strftime("%H:%M")
    except (TypeError, ValueError, OSError):
        return ""


def _n(v) -> str:
    # Número alineado a la derecha para que la columna ordene bien
    try:
        return f"{int(v or 0):>8}"
    except (TypeError, ValueError):
        return str(v or "")


# (clave, pestaña, subpestaña, columnas): MASys pinta una tabla por clave y el HTML una sección
EXTRA = [
    ("esp_apps", "💽 Espacio", "Apps", ["App", "Total", "Programa", "Datos", "Temporales"]),
    ("esp_carpetas", "💽 Espacio", "Carpetas", ["Carpeta", "Tamaño", "Archivos"]),
    ("esp_tipos", "💽 Espacio", "Tipos de archivo", ["Tipo", "Tamaño", "Archivos"]),
    ("esp_grandes", "💽 Espacio", "Archivos grandes", ["Archivo", "Carpeta", "Tamaño", "Fecha", "Hora", "Tipo"]),
    ("esp_chats", "💽 Espacio", "Mensajería", ["App", "Carpeta", "Tamaño", "Archivos", "Ruta"]),
    ("esp_sinuso", "💽 Espacio", "Apps sin usar", ["App", "Último uso", "Tamaño"]),
    ("esp_copias", "💽 Espacio", "Copias en el móvil", ["App", "Versión", "Fecha", "Hora", "Tamaño", "Contenido", "Instalable"]),
    ("bat_apps", "🔋 Batería", "Consumo por app", ["App", "mAh", "% del gasto"]),
    ("bat_hist", "🔋 Batería", "Historial (7 días)", ["Fecha", "Hora", "Nivel", "Cargando"]),
    ("ras_serv", "🕵️ Rastreadores", "Servicios", ["Servicio", "Consultas", "Cortadas", "Apps", "Se corta"]),
    ("ras_apps", "🕵️ Rastreadores", "Apps", ["App", "Consultas", "Cortadas", "Servicios"]),
    ("procesos", "⚙️ Procesos", "Procesos", ["App", "Memoria", "CPU", "Procesos"]),
    # 5.3.0: lo que MASysMov ya mandaba y no se enseñaba (tráfico, velocidad, Bluetooth) y lo de MASysMov 1.3.2
    ("bat_salud", "🔋 Batería", "Salud", ["Dato", "Valor"]),
    ("hardware", "🔧 Hardware", "Información", ["Grupo", "Dato", "Valor"]),
    ("red_sim", "📶 Red", "Líneas SIM", ["Línea", "Operador", "Red", "Señal", "Calidad", "Banda", "Antena", "Datos", "Itinerancia"]),
    ("red_wifi", "📶 Red", "Wi-Fi", ["Dato", "Valor"]),
    ("red_cercanas", "📶 Red", "Redes cercanas", ["Red", "Señal", "Canal", "Banda", "Seguridad", "Estándar"]),
    ("red_guardadas", "📶 Red", "Redes guardadas", ["Red", "Seguridad"]),
    ("red_bt", "📶 Red", "Bluetooth", ["Dispositivo", "Tipo", "Conectado", "Batería"]),
    ("red_velocidad", "📶 Red", "Velocidad", ["Fecha", "Hora", "Bajada", "Subida", "Ping", "Jitter", "Red", "Servidor"]),
    ("trafico", "📊 Consumo de datos", "Consumo de datos", ["App", "Wi-Fi", "Datos móviles", "Total"]),
    ("pruebas", "✅ Pruebas", "Pruebas", ["Grupo", "Prueba", "Resultado", "Fecha", "Hora", "Detalle"]),
]


def _si_no(v) -> str:
    return "Sí" if v is True else "No" if v is False else "No lo dice"


def _mbps(v) -> str:
    try:
        return f"{float(v):.0f} Mbps"
    except (TypeError, ValueError):
        return "—"


def _banda(mhz) -> str:
    try:
        f = int(mhz or 0)
    except (TypeError, ValueError):
        return ""
    return "2,4 GHz" if 2400 <= f <= 2500 else "5 GHz" if 4900 <= f <= 5900 else "6 GHz" if 5925 <= f <= 7125 else ""


def alertas(inf: dict) -> List[Tuple[str, str, str]]:
    """5.3.0: avisos que el propio móvil fue guardando (apps y permisos nuevos), como filas de la pestaña Cambios."""
    out = []
    for a in inf.get("alertas", []) or []:
        app = nombre_app(inf, a.get("pkg") or "") if a.get("pkg") else ""
        detalle = " · ".join(x for x in (_fecha_hora(a.get("ts")), a.get("texto") or "") if x)
        out.append((f"🔔 {a.get('titulo') or a.get('tipo') or 'Aviso'}", app, detalle))
    return out


def datos_personales(dp: Optional[dict]) -> Dict[str, List[List[str]]]:
    """5.3.0: filas de la pestaña 👥 Datos personales (encargo «datos» de MASysMov 1.3.2). dp = None → todo vacío."""
    out = {"resumen": [], "contactos": [], "llamadas": [], "sms": []}
    if not dp:
        return out
    nivel = {"recuentos": "Solo recuentos", "completo": "Completo"}.get(dp.get("nivel"), str(dp.get("nivel", "")))
    out["resumen"].append(["Recibido", _fecha_hora(dp.get("recibido") or dp.get("fecha"))])
    out["resumen"].append(["Nivel", nivel])
    for clave, nombre in (("contactos", "Contactos"), ("llamadas", "Llamadas"), ("sms", "SMS")):
        b = dp.get(clave) or {}
        if not b.get("permiso"):
            out["resumen"].append([nombre, "Sin permiso en el móvil"])
            continue
        valor = f"{b.get('numero', 0)}"
        if clave == "sms" and b.get("conversaciones") is not None:
            valor += f" en {b.get('conversaciones')} conversaciones"
        out["resumen"].append([nombre, valor])
    for c in (dp.get("contactos") or {}).get("lista", []) or []:
        out["contactos"].append([c.get("nombre", ""), ", ".join(c.get("telefonos") or []), ", ".join(c.get("correos") or []),
                                 ", ".join(c.get("cuentas") or []), "Sí" if c.get("favorito") else ""])
    for c in (dp.get("llamadas") or {}).get("lista", []) or []:
        seg = int(c.get("duracion") or 0)
        out["llamadas"].append([_fecha(c.get("fecha")), _hora(c.get("fecha")), c.get("numero", ""), c.get("nombre", ""), c.get("tipo", ""),
                                f"{seg // 60}:{seg % 60:02d}"])
    for m in (dp.get("sms") or {}).get("lista", []) or []:
        out["sms"].append([_fecha(m.get("fecha")), _hora(m.get("fecha")), m.get("direccion", ""), m.get("tipo", ""),
                           "Sí" if m.get("leido") else "No", m.get("texto", "")])
    return out


def extra(inf: dict) -> Dict[str, List[List[str]]]:
    # Filas de cada tabla de EXTRA (vacías si el informe es anterior a MASysMov 1.2.4)
    out = {k: [] for k, _p, _s, _c in EXTRA}
    esp = inf.get("espacio") or {}
    rec = esp.get("recorrido") or {}
    for a in esp.get("apps", []):
        out["esp_apps"].append([nombre_app(inf, a.get("pkg", "")), tam(a.get("total")), tam(a.get("app")), tam(a.get("datos")), tam(a.get("cache"))])
    for c in rec.get("carpetas", []):
        out["esp_carpetas"].append([c.get("ruta", ""), tam(c.get("bytes")), _n(c.get("archivos"))])
    for t in rec.get("tipos", []):
        out["esp_tipos"].append([t.get("tipo", ""), tam(t.get("bytes")), _n(t.get("archivos"))])
    for f in rec.get("grandes", []):
        ruta = f.get("ruta", "")
        carpeta, _sep, nombre = ruta.rpartition("/")
        out["esp_grandes"].append([nombre, carpeta, tam(f.get("bytes")), _fecha(f.get("modificado")), _hora(f.get("modificado")), f.get("tipo", "")])
    for c in rec.get("mensajeria", []):
        out["esp_chats"].append([c.get("app", ""), c.get("carpeta", ""), tam(c.get("bytes")), _n(c.get("archivos")), c.get("ruta", "")])
    for u in esp.get("sinUsar", []):
        out["esp_sinuso"].append([nombre_app(inf, u.get("pkg", "")), _fecha(u.get("ultimoUso")) if u.get("ultimoUso") else "Sin uso en el último año",
                                  tam(u.get("bytes"))])
    for c in (esp.get("copias") or {}).get("lista", []):
        n = c.get("apks") or 0
        out["esp_copias"].append([c.get("app", ""), c.get("version", ""), _fecha(c.get("creada")), _hora(c.get("creada")), tam(c.get("bytes")),
                                  c.get("contenido", ""), f"{n} .apk" if n else "Formato antiguo (.zip)"])
    for d in (inf.get("bateriaApps") or {}).get("apps", []):
        out["bat_apps"].append([nombre_app(inf, d.get("pkg", "")), num(d.get("mah"), 0), num(d.get("pct")) + " %"])
    for s in reversed((inf.get("bateriaExtra") or {}).get("historial", [])):
        if isinstance(s, list) and len(s) >= 3:
            out["bat_hist"].append([_fecha(s[0]), _hora(s[0]), f"{s[1]} %", "Sí" if s[2] else "No"])
    ras = inf.get("rastreadores") or {}
    for s in ras.get("servicios", []):
        out["ras_serv"].append([s.get("servicio", ""), _n(s.get("consultas")), _n(s.get("cortadas")), _n(s.get("apps")), "Sí" if s.get("seCorta") else "No"])
    for a in ras.get("apps", []):
        out["ras_apps"].append([nombre_app(inf, a.get("pkg", "")), _n(a.get("consultas")), _n(a.get("cortadas")), _n(a.get("servicios"))])
    for p in (inf.get("procesos") or {}).get("apps", []):
        out["procesos"].append([p.get("nombre") or nombre_app(inf, p.get("pkg", "")), tam((p.get("memoriaKb") or 0) * 1024),
                                num(p.get("cpu")) + " %", _n(p.get("procesos"))])
    # ── 5.3.0 ──
    bs = inf.get("bateriaSalud") or {}
    if bs:
        est = " (aproximada)" if bs.get("estimada") else ""
        for dato, v in (("Salud real", f"{bs['salud']} %" if bs.get("salud") is not None else None),
                        ("Capacidad original", f"{bs['capacidadOriginal']} mAh" if bs.get("capacidadOriginal") else None),
                        ("Capacidad actual", f"{bs['capacidadActual']} mAh{est}" if bs.get("capacidadActual") else None),
                        ("Ciclos de carga", str(bs["ciclos"]) if bs.get("ciclos") is not None else None)):
            out["bat_salud"].append([dato, v or "El móvil no lo informa"])
    for g in inf.get("hardware", []) or []:
        for par in g.get("datos", []) or []:
            if isinstance(par, list) and len(par) >= 2:
                out["hardware"].append([g.get("titulo", ""), str(par[0]), str(par[1])])
    sim = inf.get("sim") or {}
    for l in sim.get("lineas", []) or []:
        senal = f"{l['dbm']} dBm" if l.get("dbm") is not None else "—"
        out["red_sim"].append([l.get("nombre", ""), l.get("operador", ""), " ".join(x for x in (l.get("generacion"), l.get("red")) if x),
                               senal, l.get("nivel", ""), l.get("banda", ""), l.get("antena", ""),
                               "Sí" if l.get("datos") else "No", "Sí" if l.get("itinerancia") else "No"])
    if sim and not sim.get("permiso"):
        out["red_sim"].append(["Sin permiso de teléfono en el móvil", "", "", "", "", "", "", "", ""])
    w = inf.get("wifiExtra") or {}
    if w:
        out["red_wifi"].append(["Wi-Fi encendida", "Sí" if w.get("encendida") else "No"])
        out["red_wifi"].append(["Estándar más alto que admite", w.get("estandar") or "No lo dice"])
        for c in w.get("capacidades", []) or []:
            out["red_wifi"].append([c.get("nombre", ""), _si_no(c.get("tiene"))])
        a = w.get("actual") or {}
        if a:
            out["red_wifi"].append(["Conectada a", f"{a.get('ssid', '')} · canal {a.get('canal', '')} ({_banda(a.get('frecuenciaMhz'))})"
                                    + (f" · {a.get('estandar')}" if a.get("estandar") else "")])
            out["red_wifi"].append(["Velocidad del enlace", f"bajada {_mbps(a.get('bajadaMbps'))} · subida {_mbps(a.get('subidaMbps'))}"])
        for c in w.get("consejo", []) or []:
            if c.get("actual") is not None and c.get("cambiar"):
                txt = f"Cambiar del canal {c.get('actual')} al {c.get('mejor')} (el actual tiene mucho más ruido)"
            elif c.get("actual") is not None:
                txt = f"El canal {c.get('actual')} está bien (el más libre es el {c.get('mejor')})"
            else:
                txt = f"El canal más libre es el {c.get('mejor')}"
            out["red_wifi"].append([f"Canal recomendado {c.get('banda', '')}", txt])
        for n in w.get("cercanas", []) or []:
            out["red_cercanas"].append([n.get("ssid") or "(oculta)", f"{n.get('rssi', '')} dBm", _n(n.get("canal")),
                                        _banda(n.get("frecuenciaMhz")), n.get("seguridad", ""), n.get("estandar", "")])
        if w.get("guardadas") is None:
            out["red_guardadas"].append(["Solo con el modo avanzado o root en MASysMov", ""])
        for n in w.get("guardadas") or []:
            out["red_guardadas"].append([n.get("ssid", ""), n.get("seguridad", "")])
    for d in (inf.get("bluetooth") or {}).get("dispositivos", []) or []:
        bat = f"{d['bateria']} %" if d.get("bateria") is not None else "—"
        out["red_bt"].append([d.get("nombre", ""), d.get("tipo", ""), "Sí" if d.get("conectado") else "No", bat])
    for v in inf.get("velocidad", []) or []:
        out["red_velocidad"].append([_fecha(v.get("ts")), _hora(v.get("ts")), _mbps(v.get("bajada")), _mbps(v.get("subida")),
                                     f"{num(v.get('ping'), 0)} ms", f"{num(v.get('jitter'), 0)} ms", v.get("red", ""), v.get("servidor", "")])
    for t in (inf.get("trafico") or {}).get("apps", []) or []:
        wi, mo = int(t.get("wifi") or 0), int(t.get("movil") or 0)
        out["trafico"].append([nombre_app(inf, t.get("pkg", "")), tam(wi), tam(mo), tam(wi + mo)])
    for p in inf.get("pruebas", []) or []:
        hecha = bool(p.get("resultado"))
        out["pruebas"].append([p.get("grupo", ""), p.get("titulo", ""), p.get("resultado") or "Sin hacer",
                               _fecha(p.get("fecha")) if hecha else "", _hora(p.get("fecha")) if hecha else "", p.get("detalle", "")])
    return out


def informe_html(nombre: str, inf: dict, anterior: Optional[dict] = None) -> str:
    """Informe del móvil con la plantilla común de informes de MASys (msys/core/informe_html.py):
    búsqueda, filtros por columna, orden, secciones plegables, CSV e imprimir."""
    from msys.core import informe_html as ih
    icono = {OK: "✅", AVISO: "⚠️", ERROR: "🔴", INFO: "ℹ️"}
    hall = hallazgos(inf)
    apps = filas_apps(inf)
    rast = resumen_rastreadores(inf)
    cam = cambios(inf, anterior) + alertas(inf)
    kpis = [ih.Kpi(sum(1 for s, _t, _d in hall if s == ERROR), "Importantes", "crit"),
            ih.Kpi(sum(1 for s, _t, _d in hall if s == AVISO), "A revisar", "warn"),
            ih.Kpi(len(apps), "Apps instaladas por el usuario"),
            ih.Kpi(len(rast), "Apps con publicidad o analítica"),
            ih.Kpi(len(cam), "Cambios desde el anterior")]
    secciones = [
        ih.Seccion("Hallazgos", ["Nivel", "Hallazgo", "Detalle"], [[f"{icono.get(s, '')} {s}", t_, d] for s, t_, d in hall], columnas_nivel=(0,)),
        ih.Seccion("Ficha", ["Dato", "Valor"], [[c, f"{icono[s]} {v}" if s in (AVISO, ERROR) else v] for c, v, s in ficha(inf)], columnas_nivel=(1,)),
        ih.Seccion("Cambios desde el informe anterior", ["Cambio", "App", "Detalle"], [list(x) for x in cam],
                   nota="" if cam else "Sin cambios respecto al informe anterior."),
        ih.Seccion("Publicidad y analítica", ["App", "Consultas", "Servicios"], [[a, str(n), ", ".join(s)] for a, n, s in rast],
                   nota="" if rast else "Ninguna detectada."),
        ih.Seccion("Apps instaladas por el usuario", ["App", "Versión", "Origen", "Permisos sensibles", "Notas"],
                   [[a.get("nombre", ""), a.get("version", ""), a["origen"], ", ".join(a.get("permisos") or []), a["notas"]] for a in apps],
                   columnas_nivel=()),
    ]
    ex = extra(inf)
    for clave, pest, sub, cols in EXTRA:
        if ex[clave]:
            secciones.append(ih.Seccion(f"{pest} · {sub}", cols, ex[clave]))
    return ih.documento(f"📱 Informe del móvil — {nombre}", secciones, kpis=kpis, titulo_bloque="Resumen",
                        subtitulo=f"{inf.get('app', '')} · datos del {_fecha_hora(inf.get('generado'))} · enviados por MASysMov",
                        equipo=nombre)


