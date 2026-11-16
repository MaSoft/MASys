# -*- coding: utf-8 -*-
"""«🔎 Comprobar ahora» de la Base de conocimiento (4.8.0).

Una ficha se comprueba con lo que MASys YA sabe detectar: los avisos del
«🛠️ Soporte» de cada sección (msys/ui/soporte.py,
_registro_soporte_por_seccion). Para cada sección que citan los pasos de la
ficha y que tiene detector, se vuelven a leer sus datos en ESTE equipo (el
mismo refresh_* que usa «🔄 Sincronizar Todo») y se pasa su detector. No se
inventa ninguna regla nueva y no se cambia nada: solo se lee.

Sin Qt: la ejecución está en msys/ui/comprobar_ficha.py."""

from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from msys.domain import conocimiento as kb

# Sección -> método de recarga (los mismos que «🔄 Sincronizar Todo», msys/ui/reporting.py:buscar_todo)
RECARGA: Dict[str, str] = {
    "💻 Sistema": "refresh_system_info",
    "💾 Unidades Remotas": "refresh_network_drives",
    "🔑 Credenciales": "refresh_credentials",
    "📶 WiFi": "refresh_wifi",
    "🔐 Certificados": "refresh_certificates",
    "🌐 Variables Entorno": "refresh_environment_vars",
    "⚙️ Procesos": "refresh_processes",
    "🛠️ Servicios": "refresh_services",
    "🌐 Conexiones Red": "refresh_network_connections",
    "📦 Software Instalado": "refresh_installed_software",
    "⏰ Tareas Programadas": "refresh_scheduled_tasks",
    "🖨️ Impresoras": "refresh_printers",
    "🔥 Firewall": "refresh_firewall_rules",
    "📧 Cuentas Correo": "refresh_email_accounts",
    "👥 Usuarios": "refresh_users",
    "🔒 Conexiones Remotas": "refresh_remote_connections",
    "👤 Sesiones Activas": "refresh_sesiones_activas",
    "🔵 Bluetooth": "refresh_bluetooth",
    "📜 Historial PS": "refresh_ps_history",
    "⚠️ Eventos Sistema": "refresh_eventos_sistema",
    "🔌 Dispositivos USB": "refresh_usb_devices",
    "🔄 Actualizaciones": "refresh_windows_updates",
    "🖥️ Adm. Dispositivos": "refresh_drivers",
    "🚀 Programas de Inicio": "refresh_programas_inicio",
    "🌐 Redes": "refresh_network_adapters",
    "🛡️ Seguridad": "refresh_seguridad",
    "💽 Discos Duros": "refresh_salud_disco",
    "🔍 Red Local": "refresh_red_local",
}

# Estados de una sección comprobada
PENDIENTE, LEYENDO, BIEN, AVISOS, ERROR, CANCELADA = "pendiente", "leyendo", "bien", "avisos", "error", "cancelada"


@dataclass
class Aviso:
    clave: str          # clave estable del aviso de Soporte (la misma que usa «✅ Validar»)
    texto: str
    resolver: Optional[Callable] = None   # la acción de Soporte de ese aviso (pide confirmación ella misma)


@dataclass
class Resultado:
    seccion: str
    estado: str = PENDIENTE
    avisos: List[Aviso] = field(default_factory=list)
    validados: int = 0        # avisos que el técnico ya validó (no se enseñan)
    error: str = ""


def secciones_de(ficha: kb.Ficha, con_detector: Iterable[str]) -> List[str]:
    """Secciones que se pueden comprobar para esta ficha, en el orden de sus pasos y sin repetir:
    las que citan sus pasos, tienen detector de Soporte y se pueden volver a leer."""
    detector = set(con_detector)
    res: List[str] = []
    for p in ficha.pasos:
        s = p.seccion
        if s and s in detector and s in RECARGA and s not in res:
            res.append(s)
    return res


def avisos_de(detectados: Iterable[Tuple], validados: Iterable[str]) -> Tuple[List[Aviso], int]:
    """Avisos del detector (clave, texto, acción) sin los ya validados. (avisos, cuántos validados se ocultan)."""
    ya = set(validados)
    avisos, ocultos = [], 0
    for t in detectados or []:
        if not isinstance(t, (tuple, list)) or len(t) < 2:
            continue
        clave, texto = str(t[0]), str(t[1])
        if clave in ya:
            ocultos += 1
            continue
        avisos.append(Aviso(clave, texto, t[2] if len(t) > 2 and callable(t[2]) else None))
    return avisos, ocultos


def resumen(resultados: List[Resultado]) -> str:
    """Una línea: cómo ha ido la comprobación completa."""
    if not resultados:
        return "Esta ficha no tiene nada que se pueda comprobar automáticamente"
    n_avisos = sum(len(r.avisos) for r in resultados)
    errores = sum(1 for r in resultados if r.estado == ERROR)
    if any(r.estado in (PENDIENTE, LEYENDO) for r in resultados):
        hechas = sum(1 for r in resultados if r.estado not in (PENDIENTE, LEYENDO))
        return f"Comprobando… {hechas} de {len(resultados)}"
    if any(r.estado == CANCELADA for r in resultados):
        return "Comprobación detenida"
    partes = []
    if n_avisos:
        partes.append(f"{n_avisos} aviso{'s' if n_avisos != 1 else ''}")
    if errores:
        partes.append(f"{errores} sección{'es' if errores != 1 else ''} sin poder leer")
    if not partes:
        return "✔ Sin problemas detectados en este equipo"
    return "⚠️ " + " · ".join(partes)
