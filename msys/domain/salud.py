# -*- coding: utf-8 -*-
"""Puntuación de SALUD del equipo (0-100) para el Dashboard, distinta de la de
seguridad que ya existía: aquí cuenta lo que hace que el equipo vaya mal o
falle, no lo que lo hace vulnerable.

Motivos y pesos (cada uno con tope para que un solo problema repetido no lo
hunda todo):
- Pantallazos azules del periodo: 10 cada uno (máx. 30).
- Apagados inesperados: 3 cada uno (máx. 12).
- Fallos de apps: 3 si hay 5 o más, 6 si hay 20 o más.
- Avisos de Soporte de las secciones de salud (discos, espacio, dispositivos,
  impresoras, actualizaciones, servicios atascados, Bluetooth, eventos
  críticos, unidades de red, wifi, red, PATH): 5 cada uno (máx. 15 por sección).
  Los avisos que el técnico ya validó no cuentan.
- Espacio libre en la unidad del sistema (si la sección Discos no se ha
  cargado, para no contarlo dos veces): 15 por debajo del 10 %, 5 del 20 %."""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# Sección -> nombre del detector de Soporte (msys/ui/soporte.py) que mide salud, no seguridad
DETECTORES_SALUD: List[Tuple[str, str]] = [
    ("💽 Discos Duros", "_detectar_soporte_discos"),
    ("🖥️ Adm. Dispositivos", "_detectar_soporte_controladores"),
    ("🖨️ Impresoras", "_detectar_soporte_impresoras"),
    ("🔄 Actualizaciones", "_detectar_soporte_actualizaciones"),
    ("🛠️ Servicios", "_detectar_soporte_servicios_atascados"),
    ("🔵 Bluetooth", "_detectar_soporte_bluetooth"),
    ("⚠️ Eventos Sistema", "_detectar_soporte_eventos_criticos"),
    ("💾 Unidades Remotas", "_detectar_soporte_unidades_remotas"),
    ("📶 WiFi", "_detectar_soporte_wifi"),
    ("🌐 Redes", "_detectar_soporte_redes"),
    ("🌐 Variables Entorno", "_detectar_soporte_env_vars"),
    ("🌐 Variables Entorno", "_detectar_soporte_path_largo"),
]

SECCION_ESTABILIDAD = "🩺 Estabilidad"


@dataclass
class Motivo:
    texto: str
    seccion: str
    puntos: int


def motivos_estabilidad(resumen: Optional[Dict[str, int]], dias: int) -> List[Motivo]:
    if not resumen:
        return []
    res = []
    n = resumen.get("pantallazos", 0)
    if n:
        res.append(Motivo(f"🔴 {n} pantallazo{'s' if n != 1 else ''} azul{'es' if n != 1 else ''} en {dias} días",
                          SECCION_ESTABILIDAD, min(30, 10 * n)))
    n = resumen.get("apagados", 0)
    if n:
        res.append(Motivo(f"⚠️ {n} apagado{'s' if n != 1 else ''} inesperado{'s' if n != 1 else ''} en {dias} días",
                          SECCION_ESTABILIDAD, min(12, 3 * n)))
    n = resumen.get("fallos_apps", 0)
    if n >= 5:
        res.append(Motivo(f"⚠️ {n} cierres o bloqueos de apps en {dias} días ({resumen.get('apps', 0)} apps)",
                          SECCION_ESTABILIDAD, 6 if n >= 20 else 3))
    return res


def motivos_soporte(avisos_por_seccion: Dict[str, List[str]]) -> List[Motivo]:
    """{sección: [texto de cada aviso]} -> motivos (5 puntos cada uno, máx. 15 por sección)."""
    res = []
    for seccion, avisos in avisos_por_seccion.items():
        restante = 15
        for texto in avisos:
            puntos = min(5, restante)
            restante -= puntos
            res.append(Motivo(texto if texto[:1] in "⚠🔴ℹ" else f"⚠️ {texto}", seccion, puntos))
    return res


def motivo_espacio(libre_pct: Optional[float], unidad: str) -> List[Motivo]:
    if libre_pct is None:
        return []
    if libre_pct < 10:
        return [Motivo(f"🔴 Solo queda un {libre_pct:.0f} % libre en {unidad}", "💽 Discos Duros", 15)]
    if libre_pct < 20:
        return [Motivo(f"⚠️ Queda un {libre_pct:.0f} % libre en {unidad}", "💽 Discos Duros", 5)]
    return []


def puntuar(motivos: List[Motivo]) -> Tuple[int, List[Motivo]]:
    """(puntuación 0-100, motivos ordenados de más a menos grave)."""
    orden = sorted(motivos, key=lambda m: m.puntos, reverse=True)
    return max(0, 100 - sum(m.puntos for m in motivos)), orden


def nivel(puntuacion: int) -> Tuple[str, str]:
    """(color, icono) con los mismos cortes que la puntuación de seguridad."""
    if puntuacion >= 90:
        return "#2ecc71", "🟢"
    if puntuacion >= 70:
        return "#f1c40f", "🟡"
    return "#e74c3c", "🔴"
