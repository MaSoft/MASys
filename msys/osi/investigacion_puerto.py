"""Investigación activa de un puerto no accesible: relanza pruebas
correlacionadas (puerto de referencia, traceroute fresco, ping fresco,
reintentos, contexto VPN/proxy) para acotar el motivo real, en vez de
quedarse con el timeout/rechazo puntual del diagnóstico original."""
import re
from datetime import datetime
from typing import Callable, List, Optional, Tuple

from msys.osi import layer0_contexto, layer3_red
from msys.osi.layer4_transporte import _probar_puerto
from msys.osi.models import AVISO, ERROR, OK, ResultadoPrueba

_PUERTOS_REFERENCIA = [443, 80, 22, 8080]
_RE_PUERTO = re.compile(r"^Puerto (\d+)(?: \((.+)\))?$")


def extraer_puerto(prueba: str) -> Tuple[Optional[int], str]:
    m = _RE_PUERTO.match(prueba)
    if not m:
        return None, ""
    return int(m.group(1)), (m.group(2) or "")


def _elegir_puerto_referencia(puerto_investigado: int) -> int:
    for p in _PUERTOS_REFERENCIA:
        if p != puerto_investigado:
            return p
    return 53


def _serializar(r: ResultadoPrueba) -> dict:
    return {
        "capa": r.capa, "prueba": r.prueba, "resultado": r.resultado,
        "detalle": r.detalle, "valor_ms": r.valor_ms,
    }


def _traceroute_alcanza_destino(traceroute: List[ResultadoPrueba]) -> bool:
    if not traceroute:
        return False
    saltos = traceroute[0].extra.get("saltos", [])
    return bool(saltos and saltos[-1].get("ip"))


def _generar_conclusion(
    puerto: int, nombre: str, puerto_ref: int,
    exitos_reintento: int, total_reintentos: int,
    ref: ResultadoPrueba, ping: ResultadoPrueba,
    traceroute: List[ResultadoPrueba], contexto: List[ResultadoPrueba],
) -> str:
    etiqueta = f"{puerto} ({nombre})" if nombre else str(puerto)
    host_alcanzable = ping.resultado in (OK, AVISO) or _traceroute_alcanza_destino(traceroute)
    otro_puerto_abierto = ref.resultado == OK
    intermitente = 0 < exitos_reintento < total_reintentos

    if intermitente:
        conclusion = (
            f"El puerto {etiqueta} respondió en {exitos_reintento} de {total_reintentos} intentos: "
            "parece un problema intermitente (red inestable o servicio que se reinicia), no un "
            "bloqueo permanente."
        )
    elif exitos_reintento == total_reintentos and total_reintentos > 0:
        conclusion = (
            f"El puerto {etiqueta} respondió correctamente en todos los reintentos — puede que el "
            "fallo original del diagnóstico fuera puntual (corte de red momentáneo)."
        )
    elif host_alcanzable and otro_puerto_abierto:
        conclusion = (
            f"El host es alcanzable y responde en el puerto de referencia {puerto_ref}, pero no en "
            f"el {etiqueta}. El bloqueo parece específico de ese puerto — revisa el firewall o el "
            "servicio en el propio destino."
        )
    elif host_alcanzable and not otro_puerto_abierto:
        conclusion = (
            f"El host responde al ping/traceroute pero tampoco se pudo conectar al puerto de "
            f"referencia {puerto_ref}. Puede que ningún puerto TCP esté accesible desde aquí "
            "(firewall perimetral, salida a internet restringida) más que un problema del "
            f"puerto {etiqueta} en concreto."
        )
    else:
        conclusion = (
            f"Ni el ping ni el traceroute alcanzan el destino: el problema parece ser de alcance de "
            f"red general, no específico del puerto {etiqueta}."
        )

    if any(r.prueba == "VPN activa" for r in contexto):
        conclusion += " Ten en cuenta que hay una VPN activa en este equipo que puede estar afectando la ruta."
    if any(r.prueba == "Proxy del sistema activo" for r in contexto):
        conclusion += " También hay un proxy del sistema activo que puede interferir en esta prueba."

    return conclusion


def investigar(
    destino: str, puerto: int, nombre: str,
    on_progreso: Optional[Callable[[str], None]] = None,
) -> dict:
    def _avisar(msg: str) -> None:
        if on_progreso:
            on_progreso(msg)

    pruebas: List[ResultadoPrueba] = []

    _avisar(f"Reintentando el puerto {puerto}...")
    total_reintentos = 3
    intentos = [_probar_puerto(destino, puerto, nombre) for _ in range(total_reintentos)]
    exitos = sum(1 for r in intentos if r.resultado == OK)
    pruebas.append(ResultadoPrueba(
        4, "Reintentos del puerto original",
        OK if exitos == total_reintentos else (AVISO if exitos else ERROR),
        f"{exitos}/{total_reintentos} intentos consiguieron conectar",
    ))

    puerto_ref = _elegir_puerto_referencia(puerto)
    _avisar(f"Probando el puerto de referencia {puerto_ref} en el mismo host...")
    ref = _probar_puerto(destino, puerto_ref, "referencia")
    pruebas.append(ref)

    _avisar("Comprobando si el host responde (ping)...")
    ping = layer3_red._ping(destino)
    pruebas.append(ping)

    _avisar("Repitiendo traceroute (puede tardar unos segundos)...")
    traceroute = layer3_red._traceroute(destino)
    pruebas.extend(traceroute)

    _avisar("Comprobando VPN/proxy activos en este equipo...")
    contexto = layer0_contexto.ejecutar("")
    pruebas.extend(contexto)

    conclusion = _generar_conclusion(
        puerto, nombre, puerto_ref, exitos, total_reintentos, ref, ping, traceroute, contexto,
    )

    return {
        "puerto": puerto,
        "nombre": nombre,
        "puerto_referencia": puerto_ref,
        "generado": datetime.now().isoformat(timespec="seconds"),
        "pruebas": [_serializar(r) for r in pruebas],
        "conclusion": conclusion,
    }
