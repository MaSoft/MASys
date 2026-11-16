"""Capa 4 (Transporte): apertura de conexión TCP a puertos habituales."""
import socket
import time
from typing import List, Optional, Tuple

from msys.osi.models import AVISO, ERROR, OK, ResultadoPrueba

PUERTOS_COMUNES = {
    80: "HTTP",
    443: "HTTPS",
    22: "SSH",
    3389: "RDP",
    445: "SMB",
    25: "SMTP",
}


def _probar_puerto(destino: str, puerto: int, nombre: str) -> ResultadoPrueba:
    etiqueta = f"Puerto {puerto} ({nombre})" if nombre else f"Puerto {puerto}"
    inicio = time.perf_counter()
    try:
        with socket.create_connection((destino, puerto), timeout=3):
            ms = (time.perf_counter() - inicio) * 1000
            return ResultadoPrueba(4, etiqueta, OK, f"Abierto — conectado en {ms:.0f} ms", valor_ms=ms)
    except socket.timeout:
        return ResultadoPrueba(4, etiqueta, AVISO, "Sin respuesta (filtrado o firewall)")
    except ConnectionRefusedError:
        return ResultadoPrueba(4, etiqueta, AVISO, "Cerrado (conexión rechazada)")
    except socket.gaierror:
        return ResultadoPrueba(4, etiqueta, ERROR, "No se pudo resolver el nombre de destino")
    except OSError as exc:
        return ResultadoPrueba(4, etiqueta, ERROR, str(exc))


def ejecutar(destino: str, puertos: Optional[List[Tuple[int, str]]] = None) -> List[ResultadoPrueba]:
    """`puertos`: lista de (puerto, nombre) ya filtrada a los activos --
    quien llama (DashboardTab/MainWindow) decide cuáles están activos."""
    objetivo = puertos if puertos else list(PUERTOS_COMUNES.items())
    resultados = [_probar_puerto(destino, puerto, nombre) for puerto, nombre in objetivo]

    if resultados and all(r.resultado == ERROR for r in resultados):
        resultados.insert(0, ResultadoPrueba(
            4, "Resolución de destino", ERROR,
            f"No se pudo contactar con '{destino}' en ningún puerto — revisa el nombre/IP",
        ))
    return resultados
