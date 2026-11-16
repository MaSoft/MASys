"""Capa 2 (Enlace): tabla ARP y puerta de enlace predeterminada."""
import re
import subprocess

from msys.core.utils import startupinfo_oculto
from typing import List, Optional

from msys.osi.models import AVISO, ERROR, INFO, OK, ResultadoPrueba

_RE_ARP = re.compile(
    r"(?P<ip>\d{1,3}(?:\.\d{1,3}){3})\s+"
    r"(?P<mac>[0-9a-fA-F]{2}(?:-[0-9a-fA-F]{2}){5})\s+(?P<tipo>\S+)"
)
_RE_GATEWAY_LINE = re.compile(
    r"(puerta de enlace predeterminada|default gateway)\s*[.\s]*:\s*(?P<ip>\d{1,3}(?:\.\d{1,3}){3})",
    re.IGNORECASE,
)


def _puerta_enlace() -> Optional[str]:
    try:
        salida = subprocess.run(
            ["ipconfig"], capture_output=True, startupinfo=startupinfo_oculto(), creationflags=subprocess.CREATE_NO_WINDOW, text=True,
            timeout=5, encoding="cp850", errors="replace",
        ).stdout
    except Exception:
        return None
    m = _RE_GATEWAY_LINE.search(salida)
    return m.group("ip") if m else None


def ejecutar(destino: str) -> List[ResultadoPrueba]:
    resultados: List[ResultadoPrueba] = []

    gateway = _puerta_enlace()
    if gateway:
        resultados.append(ResultadoPrueba(2, "Puerta de enlace predeterminada", OK, gateway))
    else:
        resultados.append(ResultadoPrueba(
            2, "Puerta de enlace predeterminada", AVISO,
            "No se pudo determinar (¿sin conexión de red o adaptador virtual sin gateway?)",
        ))

    try:
        salida = subprocess.run(
            ["arp", "-a"], capture_output=True, startupinfo=startupinfo_oculto(), creationflags=subprocess.CREATE_NO_WINDOW, text=True,
            timeout=5, encoding="cp850", errors="replace",
        ).stdout
    except Exception as exc:
        resultados.append(ResultadoPrueba(2, "Tabla ARP", ERROR, f"No se pudo leer la tabla ARP: {exc}"))
        return resultados

    entradas = _RE_ARP.findall(salida)
    if not entradas:
        resultados.append(ResultadoPrueba(2, "Tabla ARP", AVISO, "La tabla ARP está vacía"))
        return resultados

    resultados.append(ResultadoPrueba(
        2, "Tabla ARP", INFO, f"{len(entradas)} entradas detectadas",
        extra={"entradas": [{"ip": ip, "mac": mac, "tipo": tipo} for ip, mac, tipo in entradas]},
    ))

    if gateway:
        gw_mac = next((mac for ip, mac, _ in entradas if ip == gateway), None)
        if gw_mac:
            resultados.append(ResultadoPrueba(2, "MAC de la puerta de enlace", OK, gw_mac))
        else:
            resultados.append(ResultadoPrueba(
                2, "MAC de la puerta de enlace", AVISO,
                "La puerta de enlace no está en la tabla ARP (aún no se ha resuelto)",
            ))

    return resultados
