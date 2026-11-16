# -*- coding: utf-8 -*-
"""Activar/desactivar programas de inicio: mismo mecanismo binario que usa
el Administrador de tareas de Windows (clave StartupApproved) — no borra
ni mueve nada, el valor Run/acceso directo original se queda intacto;
Windows simplemente no lo ejecuta en el próximo inicio de sesión si el
primer byte de esta clave vale 0x03 (formato comprobado en real contra
las entradas ya existentes de StartupApproved en un equipo real).

Toggle vía winreg directo (no PowerShell): es una escritura de registro
local instantánea, mismo criterio que ServiceManager para servicios
(win32service directo) — no justifica lanzar un proceso de PowerShell
aparte para esto."""

import struct
import winreg
from datetime import datetime, timezone

_HIVES = {"HKLM": winreg.HKEY_LOCAL_MACHINE, "HKCU": winreg.HKEY_CURRENT_USER}

_SUBCLAVE_APROBACION = {
    "Run": r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run",
    "Run32": r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run32",
    "StartupFolder": r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder",
}


def _filetime_actual() -> bytes:
    """FILETIME de Windows (intervalos de 100ns desde 1601-01-01 UTC) del
    momento actual, empaquetado en 8 bytes little-endian — mismo formato
    que usa Windows en el resto de la clave de aprobación."""
    epoca_windows = datetime(1601, 1, 1, tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - epoca_windows
    intervalos_100ns = int(delta.total_seconds() * 10_000_000)
    return struct.pack('<Q', intervalos_100ns)


def _resolver_hive_y_subclave(hive_nombre: str, clave_origen: str):
    # La aprobación de carpeta de inicio SIEMPRE vive en HKCU, aunque el
    # .lnk esté físicamente en la carpeta común de Todos los Usuarios —
    # así lo gestiona el propio Explorador de Windows (por eso el enumerador
    # ya etiqueta Hive='HKCU' para StartupFolder, pero se fuerza aquí
    # también por si acaso).
    hive = _HIVES[hive_nombre] if clave_origen != "StartupFolder" else winreg.HKEY_CURRENT_USER
    return hive, _SUBCLAVE_APROBACION[clave_origen]


def set_startup_enabled(hive_nombre: str, clave_origen: str, nombre_valor: str, habilitar: bool) -> None:
    """Activa/desactiva una entrada de inicio. clave_origen es 'Run',
    'Run32' o 'StartupFolder' — determina bajo qué subclave de
    StartupApproved vive el flag.

    Puede lanzar PermissionError si la entrada es de Todos los Usuarios
    (HKLM) y MASys no se está ejecutando como administrador — el
    llamador debe capturarlo y avisar, no asumir que siempre funciona."""
    hive, subclave = _resolver_hive_y_subclave(hive_nombre, clave_origen)

    llave = winreg.CreateKeyEx(hive, subclave, 0, winreg.KEY_ALL_ACCESS | winreg.KEY_WOW64_64KEY)
    try:
        try:
            valor_actual, _tipo = winreg.QueryValueEx(llave, nombre_valor)
        except FileNotFoundError:
            valor_actual = None

        if valor_actual and len(valor_actual) >= 12:
            # Ya existe: solo se cambia el primer byte (flag), se respeta
            # el resto tal cual (incluida la marca de tiempo original).
            nuevo = bytes([0x02 if habilitar else 0x03]) + bytes(valor_actual[1:])
        else:
            nuevo = bytes([0x02 if habilitar else 0x03, 0x00, 0x00, 0x00]) + _filetime_actual()

        winreg.SetValueEx(llave, nombre_valor, 0, winreg.REG_BINARY, nuevo)
    finally:
        winreg.CloseKey(llave)


def get_startup_enabled(hive_nombre: str, clave_origen: str, nombre_valor: str) -> bool:
    """Lee el estado actual — usado para refrescar la fila tras el toggle
    sin tener que relanzar todo el colector de PowerShell."""
    hive, subclave = _resolver_hive_y_subclave(hive_nombre, clave_origen)
    try:
        llave = winreg.OpenKey(hive, subclave, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
    except FileNotFoundError:
        return True
    try:
        try:
            valor, _tipo = winreg.QueryValueEx(llave, nombre_valor)
        except FileNotFoundError:
            return True
        return not (valor and len(valor) >= 1 and valor[0] == 0x03)
    finally:
        winreg.CloseKey(llave)
