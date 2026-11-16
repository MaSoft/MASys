"""Pruebas específicas de conectividad a Microsoft 365 / Entra ID / Azure.

No sustituye al comprobador oficial de Microsoft (M365 Network Connectivity
Test), pero cubre los endpoints críticos de autenticación y los servicios
más habituales para detectar bloqueos de proxy/firewall corporativos.
"""
import time
from typing import List

import requests

from msys.osi.models import AVISO, ERROR, OK, ResultadoPrueba

_ENDPOINTS = {
    "Entra ID / login (login.microsoftonline.com)": "https://login.microsoftonline.com",
    "Microsoft Graph (graph.microsoft.com)": "https://graph.microsoft.com",
    "Exchange Online (outlook.office365.com)": "https://outlook.office365.com",
    "Portal Microsoft 365 (portal.office.com)": "https://portal.office.com",
    "SharePoint/OneDrive (login.live.com)": "https://login.live.com",
}


def ejecutar(destino: str = "") -> List[ResultadoPrueba]:
    resultados: List[ResultadoPrueba] = []
    for nombre, url in _ENDPOINTS.items():
        inicio = time.perf_counter()
        try:
            respuesta = requests.get(url, timeout=6, allow_redirects=True)
            ms = (time.perf_counter() - inicio) * 1000
            if respuesta.status_code < 500:
                resultados.append(ResultadoPrueba(
                    8, nombre, OK, f"Responde ({respuesta.status_code}) en {ms:.0f} ms", valor_ms=ms,
                ))
            else:
                resultados.append(ResultadoPrueba(
                    8, nombre, AVISO, f"Responde con error de servidor ({respuesta.status_code})",
                ))
        except requests.exceptions.SSLError:
            resultados.append(ResultadoPrueba(
                8, nombre, ERROR, "Error de TLS — posible inspección SSL de un proxy corporativo",
            ))
        except requests.exceptions.ConnectionError:
            resultados.append(ResultadoPrueba(
                8, nombre, ERROR, "No accesible — revisar proxy/firewall de salida",
            ))
        except requests.exceptions.Timeout:
            resultados.append(ResultadoPrueba(8, nombre, AVISO, "Sin respuesta en 6 s"))
    return resultados
