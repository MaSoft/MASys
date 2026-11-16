"""Capa 3 (Red): ping y traceroute contra el destino."""
import re
import subprocess

from msys.core.utils import startupinfo_oculto
from typing import List

from msys.osi.models import AVISO, ERROR, OK, ResultadoPrueba

_RE_TIEMPO = re.compile(r"(?:tiempo|time)[=<]\s*(\d+)\s*ms", re.IGNORECASE)
_RE_IPV4 = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")


def _ping(destino: str) -> ResultadoPrueba:
    try:
        proc = subprocess.run(
            ["ping", "-n", "4", "-w", "1500", destino],
            capture_output=True, startupinfo=startupinfo_oculto(), creationflags=subprocess.CREATE_NO_WINDOW, text=True, timeout=15,
            encoding="cp850", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return ResultadoPrueba(3, "Ping", ERROR, "Sin respuesta en 15 s (timeout del comando)")
    except Exception as exc:
        return ResultadoPrueba(3, "Ping", ERROR, f"No se pudo ejecutar ping: {exc}")

    salida = proc.stdout
    tiempos = [int(t) for t in _RE_TIEMPO.findall(salida)]
    enviados = 4
    recibidos = len(tiempos)
    perdidos_pct = round((enviados - recibidos) / enviados * 100)

    if recibidos == 0:
        return ResultadoPrueba(
            3, "Ping", ERROR,
            f"Sin respuesta de {destino} (100% de paquetes perdidos — ¿nombre no resuelto o filtrado por firewall?)",
        )

    media = sum(tiempos) / len(tiempos)
    detalle = (
        f"{recibidos}/{enviados} respuestas · {perdidos_pct}% perdidos · "
        f"mín {min(tiempos)} ms · media {media:.0f} ms · máx {max(tiempos)} ms"
    )
    resultado = OK if perdidos_pct == 0 else AVISO
    return ResultadoPrueba(3, "Ping", resultado, detalle, valor_ms=media,
                            extra={"tiempos_ms": tiempos, "perdidos_pct": perdidos_pct})


def _traceroute(destino: str) -> List[ResultadoPrueba]:
    try:
        proc = subprocess.run(
            ["tracert", "-d", "-h", "15", "-w", "700", destino],
            capture_output=True, startupinfo=startupinfo_oculto(), creationflags=subprocess.CREATE_NO_WINDOW, text=True, timeout=30,
            encoding="cp850", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return [ResultadoPrueba(3, "Traceroute", AVISO, "El comando tracert tardó demasiado (>30 s)")]
    except Exception as exc:
        return [ResultadoPrueba(3, "Traceroute", ERROR, f"No se pudo ejecutar tracert: {exc}")]

    saltos = []
    for linea in proc.stdout.splitlines():
        m = re.match(r"\s*(\d{1,2})\s+(.*)", linea)
        if not m:
            continue
        numero, resto = m.group(1), m.group(2)
        ips = _RE_IPV4.findall(resto)
        if not ips and "*" not in resto:
            continue
        tiempos_salto = [int(t) for t in re.findall(r"(\d+)\s*ms", resto)]
        saltos.append({
            "salto": int(numero),
            "ip": ips[-1] if ips else "",
            "tiempos_ms": tiempos_salto,
            "timeout": not tiempos_salto,
        })

    if not saltos:
        return [ResultadoPrueba(3, "Traceroute", AVISO, "No se obtuvo ninguna ruta (posible bloqueo de ICMP)")]

    timeouts = sum(1 for s in saltos if s["timeout"])
    resultado = OK if timeouts == 0 else (AVISO if timeouts < len(saltos) else ERROR)
    return [ResultadoPrueba(
        3, "Traceroute", resultado,
        f"{len(saltos)} saltos · {timeouts} sin respuesta · destino {'alcanzado' if saltos[-1]['ip'] else 'no confirmado'}",
        extra={"saltos": saltos},
    )]


def ejecutar(destino: str) -> List[ResultadoPrueba]:
    return [_ping(destino)] + _traceroute(destino)
