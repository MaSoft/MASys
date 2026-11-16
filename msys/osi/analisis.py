"""Interpreta los resultados de un diagnóstico en lenguaje llano, para un
informe "cliente" (no técnico). Basado 100% en reglas sobre los datos reales
de ResultadoPrueba -- nunca inventa nada que no esté en los resultados."""
from dataclasses import dataclass
from typing import List

from msys.osi.investigacion_puerto import extraer_puerto
from msys.osi.models import ResultadoPrueba

# Pruebas consideradas "de detalle técnico": se ocultan del informe cliente
# cuando su resultado es OK/INFO (si dan AVISO/ERROR sí se muestran, porque
# entonces sí son relevantes).
_PREFIJOS_TECNICOS_SI_OK = (
    "Interfaz: ", "Tabla ARP", "MAC de la puerta de enlace",
    "Puerta de enlace predeterminada", "DNS vía ",
)

# Puertos cuyo cierre SÍ se destaca como hallazgo (los demás -- 3389, 445,
# 25... -- suelen estar cerrados a propósito y no aportan nada a un cliente).
_PUERTOS_RELEVANTES = {80: "web (HTTP)", 443: "web segura (HTTPS)"}


@dataclass
class Hallazgo:
    severidad: str  # "critico" | "aviso" | "info"
    titulo: str
    explicacion: str


def filtrar_para_cliente(resultados: List[ResultadoPrueba]) -> List[ResultadoPrueba]:
    """Quita las filas puramente técnicas que están en OK/INFO -- si hay un
    problema real (AVISO/ERROR) esa fila se mantiene siempre."""
    filtrados = []
    for r in resultados:
        es_tecnica = any(r.prueba.startswith(p) for p in _PREFIJOS_TECNICOS_SI_OK)
        if es_tecnica and r.resultado in ("OK", "INFO"):
            continue
        filtrados.append(r)
    return filtrados


def _buscar(resultados: List[ResultadoPrueba], prefijo: str) -> List[ResultadoPrueba]:
    return [r for r in resultados if r.prueba.startswith(prefijo)]


def generar_hallazgos(resultados: List[ResultadoPrueba]) -> List[Hallazgo]:
    hallazgos: List[Hallazgo] = []

    ping = _buscar(resultados, "Ping")
    if ping and ping[0].resultado == "ERROR":
        hallazgos.append(Hallazgo(
            "critico", "Sin respuesta de red (ping)",
            "El destino no respondió a las comprobaciones básicas de conectividad. "
            "Puede deberse a que el equipo/servicio esté apagado, a un firewall que "
            "bloquea estas comprobaciones (algo habitual y no siempre un problema), "
            "o a un fallo real de conectividad.",
        ))

    http = _buscar(resultados, "Petición HTTP")
    if http:
        if http[0].resultado == "ERROR":
            hallazgos.append(Hallazgo(
                "critico", "El sitio web no responde",
                f"No se pudo cargar la página del destino: {http[0].detalle}",
            ))
        elif http[0].resultado == "AVISO":
            hallazgos.append(Hallazgo(
                "aviso", "El sitio web responde con un error",
                http[0].detalle,
            ))

    cert = _buscar(resultados, "Certificado TLS")
    if cert:
        if cert[0].resultado == "ERROR":
            hallazgos.append(Hallazgo(
                "critico", "Certificado de seguridad no válido",
                "El certificado del sitio tiene un problema que hará que los navegadores "
                "muestren un aviso de seguridad a los visitantes: " + cert[0].detalle,
            ))
        elif cert[0].resultado == "AVISO":
            hallazgos.append(Hallazgo(
                "aviso", "El certificado de seguridad caduca pronto",
                cert[0].detalle + " — conviene renovarlo con margen.",
            ))

    for r in resultados:
        if r.prueba == "VPN activa":
            hallazgos.append(Hallazgo(
                "info", "Hay una VPN activa en este equipo",
                "Esto es normal si la usas a propósito, pero puede hacer que las "
                "mediciones de velocidad/latencia de este informe no reflejen tu "
                "conexión real a internet.",
            ))
        if r.prueba == "Proxy del sistema activo":
            hallazgos.append(Hallazgo(
                "info", "Hay un proxy configurado en este equipo",
                "El tráfico pasa por un intermediario configurado en Windows, lo que "
                "puede afectar a los resultados de este diagnóstico.",
            ))

    m365_fallos = [
        r for r in resultados
        if r.capa == 8 and r.resultado == "ERROR"
    ]
    if m365_fallos:
        nombres = ", ".join(r.prueba.split(" (")[0] for r in m365_fallos)
        hallazgos.append(Hallazgo(
            "critico", "Servicios de Microsoft 365 no accesibles",
            f"No se pudo contactar con: {nombres}. Esto puede impedir el acceso a "
            "correo, archivos o inicio de sesión de Microsoft 365 — revisar el "
            "firewall o proxy de salida con el proveedor de la red.",
        ))

    for r in resultados:
        if not (r.prueba.startswith("Puerto ") and r.resultado != "OK"):
            continue
        puerto, nombre_puerto = extraer_puerto(r.prueba)
        investigacion = r.extra.get("investigacion") if r.extra else None

        if investigacion:
            hallazgos.append(Hallazgo(
                "critico" if r.resultado == "ERROR" else "aviso",
                f"Puerto {puerto}{f' ({nombre_puerto})' if nombre_puerto else ''} investigado",
                investigacion.get("conclusion", r.detalle),
            ))
        elif puerto in _PUERTOS_RELEVANTES:
            hallazgos.append(Hallazgo(
                "aviso", f"El servicio {_PUERTOS_RELEVANTES[puerto]} no responde en el puerto {puerto}",
                r.detalle,
            ))

    return hallazgos


def generar_veredicto(hallazgos: List[Hallazgo]) -> dict:
    if any(h.severidad == "critico" for h in hallazgos):
        return {"texto": "Se han detectado problemas que requieren atención", "clase": "veredicto-critico"}
    if any(h.severidad == "aviso" for h in hallazgos):
        return {"texto": "Correcto, con algunos puntos a vigilar", "clase": "veredicto-aviso"}
    return {"texto": "Todo correcto", "clase": "veredicto-ok"}
