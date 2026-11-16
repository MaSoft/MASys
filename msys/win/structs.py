# -*- coding: utf-8 -*-
"""Estructuras ctypes y helpers de metricas de rendimiento en vivo (CPU/RAM)
via GetSystemTimes/GlobalMemoryStatusEx, sin dependencias nuevas.
Extraido de multisys.py (Fase 1 de modularizacion)."""

import ctypes

class _FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", ctypes.c_uint32), ("dwHighDateTime", ctypes.c_uint32)]

def _filetime_to_int(ft) -> int:
    return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

def get_cpu_times():
    """Devuelve (idle, kernel, user) en unidades de 100ns vía GetSystemTimes."""
    idle, kernel, user = _FILETIME(), _FILETIME(), _FILETIME()
    ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
    return _filetime_to_int(idle), _filetime_to_int(kernel), _filetime_to_int(user)

def get_cpu_percent(prev_times) -> tuple:
    """Calcula el % de uso de CPU desde la última muestra (prev_times).
    Devuelve (porcentaje, nuevas_muestras) para encadenar la siguiente lectura."""
    idle, kernel, user = get_cpu_times()
    if prev_times is None:
        return 0.0, (idle, kernel, user)
    prev_idle, prev_kernel, prev_user = prev_times
    total = (kernel + user) - (prev_kernel + prev_user)
    idle_diff = idle - prev_idle
    if total <= 0:
        return 0.0, (idle, kernel, user)
    pct = max(0.0, min(100.0, (1 - idle_diff / total) * 100))
    return pct, (idle, kernel, user)

class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]

def get_memory_status():
    """Devuelve (porcentaje_uso, usado_gb, total_gb) vía GlobalMemoryStatusEx."""
    stat = _MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
    total_gb = stat.ullTotalPhys / (1024 ** 3)
    avail_gb = stat.ullAvailPhys / (1024 ** 3)
    return float(stat.dwMemoryLoad), total_gb - avail_gb, total_gb
