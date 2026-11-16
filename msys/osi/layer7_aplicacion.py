"""Capa 7 (Aplicación): resolución DNS contra varios servidores + HTTP."""
import time
from typing import List

import dns.resolver
import requests

from msys.osi.models import AVISO, ERROR, OK, ResultadoPrueba

_SERVIDORES_DNS = {
    "Sistema (automático)": None,
    "Google (8.8.8.8)": "8.8.8.8",
    "Cloudflare (1.1.1.1)": "1.1.1.1",
}


def _resolver_dns(destino: str) -> List[ResultadoPrueba]:
    resultados: List[ResultadoPrueba] = []
    respuestas = {}

    for nombre, servidor in _SERVIDORES_DNS.items():
        resolver = dns.resolver.Resolver(configure=(servidor is None))
        if servidor:
            resolver.nameservers = [servidor]
        resolver.timeout = 3
        resolver.lifetime = 3
        inicio = time.perf_counter()
        try:
            respuesta = resolver.resolve(destino, "A")
            ms = (time.perf_counter() - inicio) * 1000
            ips = sorted(r.address for r in respuesta)
            respuestas[nombre] = ips
            resultados.append(ResultadoPrueba(
                7, f"DNS vía {nombre}", OK, f"{', '.join(ips)} ({ms:.0f} ms)", valor_ms=ms,
            ))
        except dns.resolver.NXDOMAIN:
            resultados.append(ResultadoPrueba(7, f"DNS vía {nombre}", ERROR, "El nombre no existe (NXDOMAIN)"))
        except dns.exception.Timeout:
            resultados.append(ResultadoPrueba(7, f"DNS vía {nombre}", AVISO, "Sin respuesta en 3 s"))
        except Exception as exc:
            resultados.append(ResultadoPrueba(7, f"DNS vía {nombre}", ERROR, str(exc)))

    validas = [tuple(v) for v in respuestas.values() if v]
    if len(set(validas)) > 1:
        resultados.append(ResultadoPrueba(
            7, "Coherencia DNS entre servidores", AVISO,
            "Los servidores DNS consultados devuelven IPs distintas (normal con CDN, revisar si no se espera)",
        ))

    return resultados


def _http(destino: str) -> List[ResultadoPrueba]:
    url = destino if destino.startswith(("http://", "https://")) else f"https://{destino}"
    try:
        respuesta = requests.get(url, timeout=10, allow_redirects=True)
    except requests.exceptions.SSLError as exc:
        return [ResultadoPrueba(7, "Petición HTTP", ERROR, f"Error de TLS: {exc}")]
    except requests.exceptions.ConnectionError as exc:
        return [ResultadoPrueba(7, "Petición HTTP", ERROR, f"No se pudo conectar: {exc}")]
    except requests.exceptions.Timeout:
        return [ResultadoPrueba(7, "Petición HTTP", ERROR, "Sin respuesta en 10 s")]

    ms = respuesta.elapsed.total_seconds() * 1000
    saltos = len(respuesta.history)
    resultado = OK if respuesta.ok else (AVISO if respuesta.status_code < 500 else ERROR)
    detalle = f"HTTP {respuesta.status_code} · {ms:.0f} ms"
    if saltos:
        detalle += f" · {saltos} redirección(es) → {respuesta.url}"
    return [ResultadoPrueba(7, "Petición HTTP", resultado, detalle, valor_ms=ms)]


def ejecutar(destino: str) -> List[ResultadoPrueba]:
    return _resolver_dns(destino) + _http(destino)
