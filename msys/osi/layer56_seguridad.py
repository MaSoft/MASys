"""Capas 5-6 (Sesión/Presentación): TLS y certificado del servidor."""
import socket
import ssl
from datetime import datetime, timezone
from typing import List

from msys.osi.models import AVISO, ERROR, OK, ResultadoPrueba

_PROTOCOLOS_DEBILES = {"TLSv1", "TLSv1.1", "SSLv3", "SSLv2"}


def ejecutar(destino: str, puerto: int = 443) -> List[ResultadoPrueba]:
    contexto = ssl.create_default_context()
    try:
        with socket.create_connection((destino, puerto), timeout=5) as sock:
            with contexto.wrap_socket(sock, server_hostname=destino) as tls:
                cert = tls.getpeercert()
                protocolo = tls.version()
                cifrado = tls.cipher()
    except ssl.SSLCertVerificationError as exc:
        return [ResultadoPrueba(5, "Certificado TLS", ERROR, f"Certificado no válido: {exc.verify_message}")]
    except (socket.timeout, ConnectionRefusedError, OSError) as exc:
        return [ResultadoPrueba(5, "Conexión TLS", AVISO, f"No se pudo establecer TLS en el puerto {puerto}: {exc}")]

    resultados: List[ResultadoPrueba] = []

    emisor = dict(x[0] for x in cert.get("issuer", []))
    sujeto = dict(x[0] for x in cert.get("subject", []))
    vence = datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
    dias_restantes = (vence - datetime.now(timezone.utc)).days

    if dias_restantes < 0:
        estado_cert = ERROR
        texto_venc = f"CADUCADO hace {abs(dias_restantes)} días"
    elif dias_restantes < 15:
        estado_cert = ERROR
        texto_venc = f"caduca en {dias_restantes} días"
    elif dias_restantes < 30:
        estado_cert = AVISO
        texto_venc = f"caduca en {dias_restantes} días"
    else:
        estado_cert = OK
        texto_venc = f"caduca en {dias_restantes} días ({vence.strftime('%d/%m/%Y')})"

    resultados.append(ResultadoPrueba(
        5, "Certificado TLS", estado_cert,
        f"{sujeto.get('commonName', '?')} · emitido por {emisor.get('commonName', '?')} · {texto_venc}",
        extra={"subject": sujeto, "issuer": emisor, "notAfter": cert["notAfter"]},
    ))

    estado_proto = AVISO if protocolo in _PROTOCOLOS_DEBILES else OK
    resultados.append(ResultadoPrueba(
        6, "Protocolo TLS negociado", estado_proto,
        f"{protocolo} · cifrado {cifrado[0] if cifrado else 'desconocido'}",
    ))

    return resultados
