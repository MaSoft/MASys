# -*- coding: utf-8 -*-
"""5.7.0 — Microparadas de lectura de disco (portado de MADisk 1.0.0, LAUNCHER\\SCRIPTS\\MADISK, misma idea y mismos
valores de serie): lee el disco FÍSICO (\\\\.\\PhysicalDriveN) sin la caché de Windows y mide cada operación con el reloj de
alta resolución. Aleatoria = bloques de 4 KB repartidos por todo el disco, siempre con la misma secuencia (para poder
comparar pruebas); Secuencial = bloques de 1 MB seguidos. Parada = operación igual o más lenta que el umbral (5 ms).
SOLO LEE: abre el disco con GENERIC_READ; nunca escribe. Necesita administrador (MASys ya se ejecuta así)."""

import ctypes
import random
import statistics
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Callable, List, Optional

GENERIC_READ = 0x80000000
FILE_SHARE_READ, FILE_SHARE_WRITE = 0x1, 0x2
OPEN_EXISTING = 3
FILE_FLAG_NO_BUFFERING, FILE_FLAG_RANDOM_ACCESS, FILE_FLAG_SEQUENTIAL_SCAN = 0x20000000, 0x10000000, 0x08000000
IOCTL_DISK_GET_LENGTH_INFO = 0x0007405C
MEM_COMMIT, MEM_RESERVE, MEM_RELEASE, PAGE_READWRITE = 0x1000, 0x2000, 0x8000, 0x04
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.CreateFileW.restype = wintypes.HANDLE
_k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                             wintypes.DWORD, wintypes.HANDLE]
_k32.VirtualAlloc.restype = ctypes.c_void_p
_k32.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD, wintypes.DWORD]
_k32.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD]
_k32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
_k32.SetFilePointerEx.argtypes = [wintypes.HANDLE, ctypes.c_longlong, ctypes.c_void_p, wintypes.DWORD]
_k32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                 wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
_k32.CloseHandle.argtypes = [wintypes.HANDLE]


@dataclass
class Parada:
    segundo: float      # desde el inicio de la prueba
    ms: float
    posicion_mb: float


@dataclass
class Prueba:
    disco: int
    patron: str
    umbral_ms: float
    segundos: float = 0.0
    operaciones: int = 0
    media_ms: float = 0.0
    p99_ms: float = 0.0
    maxima_ms: float = 0.0
    paradas: List[Parada] = field(default_factory=list)
    detenida: bool = False

    @property
    def veredicto(self) -> str:
        if not self.operaciones:
            return "Sin datos"
        if not self.paradas:
            return f"✅ Sin paradas: ninguna lectura llegó a {self.umbral_ms:g} ms"
        por_min = len(self.paradas) / max(self.segundos / 60.0, 1 / 60.0)
        base = f"{len(self.paradas)} lectura(s) de {self.umbral_ms:g} ms o más ({por_min:.1f}/min), la peor de {self.maxima_ms:.0f} ms"
        if self.maxima_ms >= 200:
            return f"🔴 {base}: paradas que el usuario NOTA (congelados). Si coinciden con sus quejas, el disco es la causa probable."
        if self.maxima_ms >= 50:
            return f"⚠️ {base}: lecturas lentas que pueden notarse en momentos de carga."
        return f"✅ {base}: solo retrasos cortos, que no se notan en el uso normal."


def _abrir(disco: int, patron: str):
    flags = FILE_FLAG_NO_BUFFERING | (FILE_FLAG_RANDOM_ACCESS if patron == "aleatoria" else FILE_FLAG_SEQUENTIAL_SCAN)
    h = _k32.CreateFileW(f"\\\\.\\PhysicalDrive{int(disco)}", GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                         OPEN_EXISTING, flags, None)
    if h in (None, INVALID_HANDLE_VALUE):
        err = ctypes.get_last_error()
        raise OSError(err, "No se pudo abrir el disco físico" + (" (hace falta ejecutar MASys como administrador)"
                                                                 if err == 5 else ""))
    return h


def tamano_disco(h) -> int:
    tam = ctypes.c_longlong(0)
    dev = wintypes.DWORD(0)
    if not _k32.DeviceIoControl(h, IOCTL_DISK_GET_LENGTH_INFO, None, 0, ctypes.byref(tam), 8, ctypes.byref(dev), None):
        raise OSError(ctypes.get_last_error(), "No se pudo leer el tamaño del disco")
    return tam.value


def medir(disco: int, segundos: float = 30, patron: str = "aleatoria", umbral_ms: float = 5.0,
          al_progreso: Optional[Callable[[float, float, int], None]] = None,
          debe_parar: Optional[Callable[[], bool]] = None) -> Prueba:
    """Prueba de lectura. al_progreso(fraccion, ms_ultima, paradas_hasta_ahora) ~10 veces por segundo."""
    bloque = 4096 if patron == "aleatoria" else 1024 * 1024
    h = _abrir(disco, patron)
    buf = _k32.VirtualAlloc(None, bloque, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)   # alineado a página
    r = Prueba(disco, patron, umbral_ms)
    lat: List[float] = []
    try:
        total = tamano_disco(h)
        bloques = max(1, total // bloque)
        rnd = random.Random(20261031)          # siempre la misma secuencia
        leidos = wintypes.DWORD(0)
        sig = 0
        inicio = time.perf_counter()
        ultimo_aviso = 0.0
        while True:
            ahora = time.perf_counter() - inicio
            if ahora >= segundos or (debe_parar and debe_parar()):
                r.detenida = ahora < segundos
                break
            n = rnd.randrange(bloques) if patron == "aleatoria" else sig
            sig = (sig + 1) % bloques
            t0 = time.perf_counter()
            ok = _k32.SetFilePointerEx(h, n * bloque, None, 0) and _k32.ReadFile(h, buf, bloque, ctypes.byref(leidos), None)
            ms = (time.perf_counter() - t0) * 1000.0
            if not ok:
                raise OSError(ctypes.get_last_error(), f"Error de lectura en la posición {n * bloque // 1048576} MB")
            lat.append(ms)
            if ms >= umbral_ms:
                r.paradas.append(Parada(round(ahora, 2), round(ms, 2), round(n * bloque / 1048576, 1)))
            if al_progreso and ahora - ultimo_aviso >= 0.1:
                ultimo_aviso = ahora
                al_progreso(min(1.0, ahora / segundos), ms, len(r.paradas))
        r.segundos = time.perf_counter() - inicio
    finally:
        _k32.VirtualFree(buf, 0, MEM_RELEASE)
        _k32.CloseHandle(h)
    r.operaciones = len(lat)
    if lat:
        r.media_ms = round(statistics.fmean(lat), 3)
        r.maxima_ms = round(max(lat), 2)
        r.p99_ms = round(sorted(lat)[max(0, int(len(lat) * 0.99) - 1)], 3)
    return r


def listar_discos() -> List[dict]:
    """[{indice, modelo, tamano_gb, interfaz}] con WMI (Win32_DiskDrive)."""
    from msys.core.utils import run_powershell_json
    datos = run_powershell_json("@(Get-CimInstance Win32_DiskDrive | Sort-Object Index | Select-Object Index, Model, Size, "
                                "InterfaceType) | ConvertTo-Json -Compress", timeout=30) or []
    if isinstance(datos, dict):
        datos = [datos]
    return [{"indice": int(d.get("Index", 0)), "modelo": str(d.get("Model") or "Disco"),
             "tamano_gb": round((d.get("Size") or 0) / 1e9, 1), "interfaz": str(d.get("InterfaceType") or "")}
            for d in datos]
