"""Orquestador: ejecuta cada capa en orden y va reportando avance/resultados."""
from typing import Callable, List, Optional

from msys.osi import (
    layer0_contexto, layer1_fisica, layer2_enlace, layer3_red,
    layer4_transporte, layer56_seguridad, layer7_aplicacion, layer8_m365,
)
from msys.osi.models import ERROR, ResultadoPrueba

# (etiqueta de progreso, función ejecutar(destino) -> List[ResultadoPrueba])
_FASES = [
    ("Comprobando VPN/proxy activos...", layer0_contexto.ejecutar),
    ("Comprobando interfaces de red locales (capa 1)...", layer1_fisica.ejecutar),
    ("Consultando tabla ARP y puerta de enlace (capa 2)...", layer2_enlace.ejecutar),
    ("Ping y traceroute al destino (capa 3)...", layer3_red.ejecutar),
    ("Probando puertos TCP habituales (capa 4)...", layer4_transporte.ejecutar),
    ("Validando TLS y certificado (capas 5-6)...", layer56_seguridad.ejecutar),
    ("Resolviendo DNS y probando HTTP (capa 7)...", layer7_aplicacion.ejecutar),
    ("Comprobando endpoints de Microsoft 365/Entra ID...", layer8_m365.ejecutar),
]


def ejecutar_diagnostico(
    destino: str,
    incluir_m365: bool = True,
    puertos: Optional[List[int]] = None,
    on_progreso: Optional[Callable[[str], None]] = None,
    on_resultado: Optional[Callable[[ResultadoPrueba], None]] = None,
) -> List[ResultadoPrueba]:
    """Ejecuta todas las fases en orden. Pensado para correr en un QThread:
    no depende de Qt, solo de callbacks simples."""
    todos: List[ResultadoPrueba] = []

    for etiqueta, funcion in _FASES:
        if funcion is layer8_m365.ejecutar and not incluir_m365:
            continue
        if on_progreso:
            on_progreso(etiqueta)
        try:
            if funcion is layer4_transporte.ejecutar:
                parciales = funcion(destino, puertos)
            else:
                parciales = funcion(destino)
        except Exception as exc:
            parciales = [ResultadoPrueba(0, funcion.__module__, ERROR, f"Fallo interno de la prueba: {exc}")]
        for r in parciales:
            todos.append(r)
            if on_resultado:
                on_resultado(r)

    return todos
