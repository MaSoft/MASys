"""Capa 1 (Física): estado real de las interfaces de red locales."""
from typing import List

import psutil

from msys.osi.models import AVISO, ERROR, INFO, OK, ResultadoPrueba

_DUPLEX = {
    0: "desconocido",
    1: "medio",
    2: "completo",
}


def ejecutar(destino: str) -> List[ResultadoPrueba]:
    resultados: List[ResultadoPrueba] = []
    try:
        stats = psutil.net_if_stats()
        addrs = psutil.net_if_addrs()
    except Exception as exc:
        return [ResultadoPrueba(1, "Interfaces de red", ERROR, f"No se pudo leer el estado de red: {exc}")]

    if not stats:
        return [ResultadoPrueba(1, "Interfaces de red", ERROR, "No se detectó ninguna interfaz de red")]

    activas = 0
    for nombre, info in stats.items():
        ipv4 = next(
            (a.address for a in addrs.get(nombre, []) if a.family.name == "AF_INET"),
            "",
        )
        mac = next(
            (a.address for a in addrs.get(nombre, []) if a.family.name in ("AF_LINK", "AF_PACKET")),
            "",
        )
        if not info.isup:
            resultados.append(ResultadoPrueba(
                1, f"Interfaz: {nombre}", INFO, "Desactivada / sin cable",
                extra={"mac": mac, "ipv4": ipv4},
            ))
            continue

        activas += 1
        duplex = _DUPLEX.get(getattr(info.duplex, "value", info.duplex), "desconocido")
        if info.speed <= 0:
            resultados.append(ResultadoPrueba(
                1, f"Interfaz: {nombre}", AVISO,
                f"Activa pero sin velocidad reportada (virtual o inalámbrica) — MTU {info.mtu}",
                extra={"mac": mac, "ipv4": ipv4, "mtu": info.mtu},
            ))
        else:
            resultados.append(ResultadoPrueba(
                1, f"Interfaz: {nombre}", OK,
                f"{info.speed} Mbps · dúplex {duplex} · MTU {info.mtu} · IPv4 {ipv4 or 'sin asignar'}",
                extra={"mac": mac, "ipv4": ipv4, "mtu": info.mtu, "speed": info.speed},
            ))

    if activas == 0:
        resultados.append(ResultadoPrueba(1, "Conectividad local", ERROR, "Ninguna interfaz de red está activa"))

    return resultados
