"""Modelos de datos del motor de diagnóstico. Sin dependencias de Qt para
poder probarlo y ejecutarlo fuera de la interfaz."""
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

OK = "OK"
AVISO = "AVISO"
ERROR = "ERROR"
INFO = "INFO"

NOMBRES_CAPA = {
    0: "Contexto",
    1: "1 · Física",
    2: "2 · Enlace",
    3: "3 · Red",
    4: "4 · Transporte",
    5: "5-6 · Sesión/Seguridad",
    6: "5-6 · Sesión/Seguridad",
    7: "7 · Aplicación",
    8: "M365 / Entra ID",
}


@dataclass
class ResultadoPrueba:
    capa: int
    prueba: str
    resultado: str
    detalle: str = ""
    valor_ms: Optional[float] = None
    extra: Dict[str, Any] = field(default_factory=dict)
    id: Optional[int] = None  # fila en la tabla `resultados`, solo tras guardarse en BD

    @property
    def capa_nombre(self) -> str:
        return NOMBRES_CAPA.get(self.capa, str(self.capa))
