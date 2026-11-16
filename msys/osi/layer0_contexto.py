"""Contexto de red: VPN o proxy activos que pueden alterar los resultados
del resto del diagnóstico sin que se note a simple vista."""
import winreg
from typing import List

import psutil

from msys.osi.models import AVISO, INFO, ResultadoPrueba

_PALABRAS_VPN = (
    "vpn", "tailscale", "wireguard", "openvpn", "nordvpn", "expressvpn",
    "anyconnect", "zerotier", "tap-windows", "wintun", "netextender",
    "forticlient", "globalprotect", "sonicwall", "radmin",
)


def _vpn_activas() -> List[str]:
    try:
        stats = psutil.net_if_stats()
    except Exception:
        return []
    encontradas = []
    for nombre, info in stats.items():
        if info.isup and any(palabra in nombre.lower() for palabra in _PALABRAS_VPN):
            encontradas.append(nombre)
    return encontradas


def _proxy_sistema():
    try:
        clave = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        )
        try:
            activado, _ = winreg.QueryValueEx(clave, "ProxyEnable")
            if not activado:
                return None
            servidor, _ = winreg.QueryValueEx(clave, "ProxyServer")
            return servidor or "activado (sin servidor especificado)"
        finally:
            winreg.CloseKey(clave)
    except OSError:
        return None


def ejecutar(destino: str) -> List[ResultadoPrueba]:
    resultados: List[ResultadoPrueba] = []

    vpns = _vpn_activas()
    if vpns:
        resultados.append(ResultadoPrueba(
            0, "VPN activa", AVISO,
            f"Interfaz(es) de tipo VPN activas: {', '.join(vpns)} — puede alterar rutas, "
            "latencias o la IP de salida del resto de pruebas.",
            extra={"interfaces": vpns},
        ))

    proxy = _proxy_sistema()
    if proxy:
        resultados.append(ResultadoPrueba(
            0, "Proxy del sistema activo", AVISO,
            f"Proxy configurado en Windows: {proxy} — puede interceptar TLS o bloquear destinos.",
        ))

    if not vpns and not proxy:
        resultados.append(ResultadoPrueba(0, "VPN / Proxy", INFO, "No se detectó VPN ni proxy del sistema activos"))

    return resultados
