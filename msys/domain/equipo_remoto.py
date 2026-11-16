# -*- coding: utf-8 -*-
"""Modo «equipo remoto» (5.2.0): ver en las secciones normales de MASys lo que leyó el agente en el equipo de un
cliente, como si MASys estuviera abierto allí. Sin Qt: la parte de la ventana está en msys/ui/equipo_remoto.py.

Cada apartado del agente (agente.LECTORES) es EXACTAMENTE el recolector que usa la sección de MASys al pulsar
«Actualizar»: en modo remoto, la clase del recolector se sustituye en su módulo de la interfaz por una que entrega
los datos del informe (reconstruidos, sin la conversión de agente.preparar: esa la hace la propia sección)."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List

from msys.domain import agente as ag

# clave del agente → (módulo de la interfaz que crea el recolector, método refresh_* de la ventana).
# Comprobado: cada recolector se crea en un único módulo de msys/ui (5.2.0).
SECCIONES: Dict[str, tuple] = {
    "sistema": ("msys.ui.system_info", "refresh_system_info"),
    "unidades": ("msys.ui.network_drives", "refresh_network_drives"),
    "credenciales": ("msys.ui.credentials", "refresh_credentials"),
    "wifi": ("msys.ui.wifi", "refresh_wifi"),
    "certificados": ("msys.ui.certificates", "refresh_certificates"),
    "variables": ("msys.ui.environment_vars", "refresh_environment_vars"),
    "procesos": ("msys.ui.processes", "refresh_processes"),
    "servicios": ("msys.ui.services", "refresh_services"),
    "conexiones": ("msys.ui.network_connections", "refresh_network_connections"),
    "software": ("msys.ui.installed_software", "refresh_installed_software"),
    "tareas": ("msys.ui.scheduled_tasks", "refresh_scheduled_tasks"),
    "impresoras": ("msys.ui.printers", "refresh_printers"),
    "firewall": ("msys.ui.firewall", "refresh_firewall_rules"),
    "correo": ("msys.ui.email_accounts", "refresh_email_accounts"),
    "usuarios": ("msys.ui.users", "refresh_users"),
    "remotas": ("msys.ui.remote_connections", "refresh_remote_connections"),
    "sesiones": ("msys.ui.active_sessions", "refresh_sesiones_activas"),
    "bluetooth": ("msys.ui.bluetooth", "refresh_bluetooth"),
    "historial_ps": ("msys.ui.ps_history", "refresh_ps_history"),
    "eventos": ("msys.ui.system_events", "refresh_eventos_sistema"),
    "usb": ("msys.ui.usb_devices", "refresh_usb_devices"),
    "monitores": ("msys.ui.monitors", "refresh_monitors"),
    "actualizaciones": ("msys.ui.windows_updates", "refresh_windows_updates"),
    "controladores": ("msys.ui.drivers", "refresh_drivers"),
    "ad": ("msys.ui.ad_info", "refresh_ad_info"),
    "directivas": ("msys.ui.group_policy", "refresh_politicas_grupo"),
    "inicio": ("msys.ui.startup_programs", "refresh_programas_inicio"),
    "adaptadores": ("msys.ui.network_adapters", "refresh_network_adapters"),
    "seguridad": ("msys.ui.security", "refresh_seguridad"),
    "discos": ("msys.ui.disk_health", "refresh_salud_disco"),
    "licencias": ("msys.ui.licenses", "refresh_licencias"),
    "paquetes": ("msys.ui.package_manager", "refresh_gestor_paquetes"),
    "red_local": ("msys.ui.local_network", "refresh_red_local"),
}
# Hilos que, al recibir los datos, consultan desde ESTE PC (no deben correr con datos de otro equipo)
HILOS_LOCALES = [("msys.ui.local_network", "DeviceEnrichmentThread")]


@dataclass
class Apartado:
    clave: str
    seccion: str          # nombre de la sección de MASys («⚙️ Procesos»)
    modulo: str
    clase: str            # recolector
    refresco: str         # método refresh_* de la ventana
    datos: Any


def apartados(informe: dict) -> List[Apartado]:
    """Los apartados del informe que se pueden enseñar (con datos y sin error), en el orden de Sincronizar Todo."""
    secciones = informe.get("secciones") or {}
    res = []
    for clave, _titulo, seccion, clase, _args in ag.LECTORES:
        s = secciones.get(clave)
        if not s or s.get("error") or s.get("datos") is None or clave not in SECCIONES:
            continue
        modulo, refresco = SECCIONES[clave]
        res.append(Apartado(clave, seccion, modulo, clase, refresco, ag.reconstruir(s.get("datos"))))
    return res


def sin_datos(informe: dict) -> List[str]:
    """Títulos de los apartados que el agente no pudo leer (para avisar)."""
    secciones = informe.get("secciones") or {}
    return [s.get("titulo") or c for c, s in secciones.items() if s.get("error") or s.get("datos") is None]


def fecha_legible(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
        return iso or ""


def rotulo(informe: dict, cliente: str = "") -> str:
    """«PC-RECEPCION · Clínica Sur · datos del agente del 31/10/2026 09:40»."""
    partes = [informe.get("equipo") or "Equipo remoto"]
    if cliente:
        partes.append(cliente)
    fecha = fecha_legible(informe.get("fin") or informe.get("inicio") or "")
    if fecha:
        partes.append(f"datos del agente del {fecha}")
    return " · ".join(partes)
