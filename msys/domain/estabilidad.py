# -*- coding: utf-8 -*-
"""Motor (sin Qt) de la sección 🩺 Estabilidad: pantallazos azules, apagados
inesperados y cuelgues de aplicaciones.

Fuentes (todas locales, solo lectura):
- Registro de eventos «System»:
    41   Kernel-Power: el equipo se reinició sin apagarse bien. Si trae
         BugcheckCode ≠ 0 fue un pantallazo; si es 0, corte de luz, botón
         de encendido o cuelgue total.
    1001 WER-SystemErrorReporting (o BugCheck): «el equipo se reinició tras
         un error grave», con el código y la ruta del volcado.
    6008 EventLog: «el apagado anterior fue inesperado» (se anota al arrancar).
- Registro «Application»: 1000 Application Error (la app se cerró) y
  1002 Application Hang (dejó de responder y se cerró).
- Volcados: %SystemRoot%\\Minidump\\*.dmp y MEMORY.DMP.
- Índice de estabilidad de Windows (Win32_ReliabilityStabilityMetrics, 1-10),
  el mismo del Monitor de confiabilidad; puede no existir.

El controlador culpable de un pantallazo solo se puede saber analizando el
volcado con kd.exe (Herramientas de depuración de Windows, parte del Windows
SDK): si está instalado se usa; si no, se dice claramente."""

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from msys.core.utils import run_powershell_json

TIPO_PANTALLAZO = "Pantallazo azul"
TIPO_APAGADO = "Apagado inesperado"
TIPO_CIERRE = "La app se cerró"
TIPO_BLOQUEO = "La app dejó de responder"

# Código de detención -> (nombre técnico, qué significa, causa habitual)
BUGCHECKS: Dict[int, Tuple[str, str, str]] = {
    0x0A: ("IRQL_NOT_LESS_OR_EQUAL", "Un controlador usó memoria que no debía", "Controlador defectuoso o memoria RAM"),
    0x1A: ("MEMORY_MANAGEMENT", "Error grave en la gestión de la memoria", "Memoria RAM o controlador"),
    0x1E: ("KMODE_EXCEPTION_NOT_HANDLED", "Un controlador provocó un error que nadie atendió", "Controlador"),
    0x3B: ("SYSTEM_SERVICE_EXCEPTION", "Fallo al ejecutar una función del sistema", "Controlador, antivirus o programa de seguridad"),
    0x50: ("PAGE_FAULT_IN_NONPAGED_AREA", "Se pidió memoria que no existe", "Memoria RAM, disco o antivirus"),
    0x77: ("KERNEL_STACK_INPAGE_ERROR", "No se pudo leer del disco una parte del sistema", "Disco o cable del disco"),
    0x7A: ("KERNEL_DATA_INPAGE_ERROR", "No se pudo leer del disco una parte de la memoria", "Disco, cable o memoria RAM"),
    0x7B: ("INACCESSIBLE_BOOT_DEVICE", "Windows no puede acceder al disco de arranque", "Controlador de disco o cambio en la BIOS (AHCI/RAID)"),
    0x7E: ("SYSTEM_THREAD_EXCEPTION_NOT_HANDLED", "Un proceso del sistema falló sin control", "Controlador"),
    0x7F: ("UNEXPECTED_KERNEL_MODE_TRAP", "El procesador detectó un error grave", "Hardware: procesador, memoria o overclock"),
    0x9C: ("MACHINE_CHECK_EXCEPTION", "El procesador detectó un fallo de hardware", "Hardware: procesador, temperatura o alimentación"),
    0x9F: ("DRIVER_POWER_STATE_FAILURE", "Un controlador falló al suspender o reanudar", "Controlador (a menudo de red, USB o gráfica)"),
    0xA0: ("INTERNAL_POWER_ERROR", "Error grave en la gestión de energía", "Controlador o BIOS"),
    0xC2: ("BAD_POOL_CALLER", "Un controlador pidió memoria de forma incorrecta", "Controlador"),
    0xC5: ("DRIVER_CORRUPTED_EXPOOL", "Un controlador dañó la memoria del sistema", "Controlador"),
    0xD1: ("DRIVER_IRQL_NOT_LESS_OR_EQUAL", "Un controlador usó memoria que no debía", "Controlador (a menudo de red o wifi)"),
    0xEF: ("CRITICAL_PROCESS_DIED", "Se cerró un proceso imprescindible de Windows", "Archivos del sistema dañados o disco"),
    0xED: ("UNMOUNTABLE_BOOT_VOLUME", "No se pudo abrir el disco de arranque", "Disco dañado o sistema de archivos"),
    0xF4: ("CRITICAL_OBJECT_TERMINATION", "Terminó un proceso imprescindible", "Disco o archivos del sistema dañados"),
    0xFC: ("ATTEMPTED_EXECUTE_OF_NOEXECUTE_MEMORY", "Se intentó ejecutar memoria protegida", "Controlador"),
    0x101: ("CLOCK_WATCHDOG_TIMEOUT", "Un núcleo del procesador dejó de responder", "Procesador, BIOS o overclock"),
    0x10E: ("VIDEO_MEMORY_MANAGEMENT_INTERNAL", "Error en la memoria de la tarjeta gráfica", "Tarjeta gráfica o su controlador"),
    0x116: ("VIDEO_TDR_FAILURE", "La tarjeta gráfica dejó de responder y no se recuperó", "Controlador de la gráfica, temperatura u overclock"),
    0x117: ("VIDEO_TDR_TIMEOUT_DETECTED", "La tarjeta gráfica dejó de responder", "Controlador de la gráfica"),
    0x119: ("VIDEO_SCHEDULER_INTERNAL_ERROR", "Error grave de la tarjeta gráfica", "Controlador de la gráfica"),
    0x124: ("WHEA_UNCORRECTABLE_ERROR", "El hardware avisó de un error que no pudo corregir", "Hardware: procesador, memoria, temperatura o alimentación"),
    0x133: ("DPC_WATCHDOG_VIOLATION", "Un controlador tardó demasiado en responder", "Controlador o firmware del disco SSD"),
    0x139: ("KERNEL_SECURITY_CHECK_FAILURE", "Se detectó memoria del sistema dañada", "Controlador o memoria RAM"),
    0x13A: ("KERNEL_MODE_HEAP_CORRUPTION", "Un controlador dañó la memoria del sistema", "Controlador"),
    0x154: ("UNEXPECTED_STORE_EXCEPTION", "Error grave al leer del disco", "Disco (sobre todo SSD) o antivirus"),
    0x1C4: ("DRIVER_VERIFIER_DETECTED_VIOLATION", "El comprobador de controladores encontró un fallo", "Controlador (está activo «Driver Verifier»)"),
    0xC000021A: ("STATUS_SYSTEM_PROCESS_TERMINATED", "Se cerró un proceso imprescindible de Windows", "Archivos del sistema dañados o actualización a medias"),
}

# Código de excepción de una app -> qué significa
EXCEPCIONES: Dict[str, str] = {
    "c0000005": "Usó memoria que no debía",
    "c0000409": "Se cerró por un error interno grave",
    "c0000374": "Memoria del programa dañada",
    "e0434352": "Error .NET no controlado",
    "e06d7363": "Error de C++ no controlado",
    "80000003": "Fallo interno del programa",
    "c000001d": "Instrucción no válida para el procesador",
    "c00000fd": "Se quedó sin pila",
    "c0000135": "Falta una biblioteca DLL",
    "c0000142": "No se pudo iniciar una biblioteca DLL",
    "c06d007e": "No se encontró un módulo que necesita",
    "c0000417": "Parámetro no válido",
    "c0000602": "Cierre pedido por el propio programa",
    "cfffffff": "Cierre forzado al no responder",
    "c000027b": "Error en una app de la Tienda",
    "c000000d": "Parámetro no válido",
}

# Controladores conocidos (imagen del volcado) -> qué son
CONTROLADORES: Dict[str, str] = {
    "nvlddmkm.sys": "Tarjeta gráfica NVIDIA", "atikmdag.sys": "Tarjeta gráfica AMD", "amdkmdag.sys": "Tarjeta gráfica AMD",
    "igdkmd64.sys": "Gráfica Intel", "igdkmdn64.sys": "Gráfica Intel", "igdkmdnd64.sys": "Gráfica Intel",
    "dxgkrnl.sys": "Núcleo de gráficos de Windows (la causa suele ser el controlador de la gráfica)",
    "ntoskrnl.exe": "Núcleo de Windows (la causa real suele ser otra: memoria RAM o un controlador)",
    "hal.dll": "Capa de hardware de Windows (suele ser hardware o BIOS)",
    "storport.sys": "Almacenamiento (disco)", "stornvme.sys": "Disco NVMe", "storahci.sys": "Disco SATA",
    "iastora.sys": "Intel Rapid Storage (discos)", "iastorac.sys": "Intel Rapid Storage (discos)",
    "ndis.sys": "Red de Windows (la causa suele ser el controlador de red)", "tcpip.sys": "Red de Windows",
    "netwtw04.sys": "Wifi Intel", "netwtw06.sys": "Wifi Intel", "netwtw08.sys": "Wifi Intel", "netwtw10.sys": "Wifi Intel",
    "rtwlane.sys": "Wifi Realtek", "rtwlanu.sys": "Wifi Realtek (USB)", "rt640x64.sys": "Red Realtek", "rtux64w10.sys": "Red Realtek (USB)",
    "e1d68x64.sys": "Red Intel", "e1i68x64.sys": "Red Intel", "athw10x.sys": "Wifi Qualcomm Atheros", "bcmwl63a.sys": "Wifi Broadcom",
    "usbxhci.sys": "USB 3", "wdf01000.sys": "Base de controladores de Windows (la causa suele ser otro controlador)",
    "win32kfull.sys": "Interfaz de Windows", "win32kbase.sys": "Interfaz de Windows",
    "wdfilter.sys": "Antivirus Microsoft Defender", "klif.sys": "Antivirus Kaspersky", "aswsp.sys": "Antivirus Avast/AVG",
    "bdselfpr.sys": "Antivirus Bitdefender", "mfehidk.sys": "Antivirus McAfee", "epfwwfp.sys": "Antivirus ESET",
}


@dataclass
class Evento:
    fecha: datetime
    tipo: str
    titulo: str
    detalle: str = ""
    codigo: Optional[int] = None      # pantallazo: código de detención
    app: str = ""
    ruta: str = ""
    modulo: str = ""
    excepcion: str = ""
    volcado: str = ""


@dataclass
class Volcado:
    ruta: str
    fecha: datetime
    tamano: int


@dataclass
class Pantallazo:
    fecha: datetime
    codigo: Optional[int]
    nombre: str
    significado: str
    causa: str
    volcado: str = ""
    controlador: str = ""


@dataclass
class AppFallos:
    app: str
    cierres: int
    bloqueos: int
    ultimo: datetime
    modulo: str
    excepcion: str
    ruta: str

    @property
    def total(self) -> int:
        return self.cierres + self.bloqueos


@dataclass
class Estabilidad:
    eventos: List[Evento] = field(default_factory=list)
    volcados: List[Volcado] = field(default_factory=list)
    indice: Optional[float] = None
    dias: int = 30

    @property
    def pantallazos(self) -> List[Pantallazo]:
        return pantallazos(self.eventos, self.volcados)

    @property
    def apps(self) -> List[AppFallos]:
        return apps_que_fallan(self.eventos)

    def resumen(self) -> Dict[str, int]:
        return {
            "pantallazos": len(self.pantallazos),
            "apagados": sum(1 for e in self.eventos if e.tipo == TIPO_APAGADO),
            "fallos_apps": sum(1 for e in self.eventos if e.tipo in (TIPO_CIERRE, TIPO_BLOQUEO)),
            "apps": len(self.apps),
        }


# ---------------------------------------------------------------------------------------------
# Traducciones
# ---------------------------------------------------------------------------------------------

def codigo_de_texto(texto: str) -> Optional[int]:
    """«0x0000009f (0x..., ...)» -> 0x9F; «159» -> 0x9F."""
    t = (texto or "").strip()
    m = re.search(r"0x([0-9a-fA-F]{1,16})", t)
    if m:
        return int(m.group(1), 16)
    if t.isdigit():
        return int(t)
    return None


def explicar_codigo(codigo: Optional[int]) -> Tuple[str, str, str]:
    if codigo is None:
        return ("Desconocido", "Windows no guardó el código", "—")
    if codigo in BUGCHECKS:
        return BUGCHECKS[codigo]
    return (f"0x{codigo:X}", "Error grave poco habitual", "Buscar el código en la documentación de Microsoft")


def texto_codigo(codigo: Optional[int]) -> str:
    return "—" if codigo is None else f"0x{codigo:X}"


def explicar_excepcion(codigo: str) -> str:
    c = (codigo or "").lower().replace("0x", "").zfill(8)[-8:]
    return EXCEPCIONES.get(c, f"Código 0x{c}" if c.strip("0") else "")


def explicar_controlador(imagen: str) -> str:
    i = (imagen or "").strip().lower()
    if not i:
        return ""
    return CONTROLADORES.get(i, f"Controlador «{imagen}»")


# ---------------------------------------------------------------------------------------------
# Lectura
# ---------------------------------------------------------------------------------------------

_PS = r"""
$d = (Get-Date).AddDays(-__DIAS__)
$r = New-Object System.Collections.ArrayList
function Add-Ev($e) {
  $p = @($e.Properties | ForEach-Object { "$($_.Value)" })
  $m = ""; try { $m = ($e.Message -split "`r?`n")[0] } catch {}
  [void]$r.Add([pscustomobject]@{ Log=$e.LogName; Id=$e.Id; Prov=$e.ProviderName; Fecha=$e.TimeCreated.ToString('s'); P=$p; Msg=$m })
}
try { Get-WinEvent -FilterHashtable @{LogName='System'; Id=@(41,1001,6008); StartTime=$d} -MaxEvents 2000 -ErrorAction Stop | ForEach-Object { Add-Ev $_ } } catch {}
try { Get-WinEvent -FilterHashtable @{LogName='Application'; ProviderName=@('Application Error','Application Hang'); StartTime=$d} -MaxEvents 3000 -ErrorAction Stop | Where-Object { $_.Id -eq 1000 -or $_.Id -eq 1002 } | ForEach-Object { Add-Ev $_ } } catch {}
$v = New-Object System.Collections.ArrayList
foreach ($f in @("$env:SystemRoot\Minidump\*.dmp", "$env:SystemRoot\MEMORY.DMP")) {
  Get-ChildItem $f -ErrorAction SilentlyContinue | ForEach-Object { [void]$v.Add([pscustomobject]@{ Ruta=$_.FullName; Fecha=$_.LastWriteTime.ToString('s'); Tam=$_.Length }) }
}
$i = $null
try { $m = Get-CimInstance Win32_ReliabilityStabilityMetrics -ErrorAction Stop | Sort-Object TimeGenerated | Select-Object -Last 1; if ($m) { $i = [double]$m.SystemStabilityIndex } } catch {}
[pscustomobject]@{ Eventos=@($r); Volcados=@($v); Indice=$i } | ConvertTo-Json -Depth 4 -Compress
"""


def _fecha(s: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat((s or "")[:19])
    except ValueError:
        return None


def _lista(x) -> list:
    if x is None:
        return []
    return x if isinstance(x, list) else [x]


def _p(props: list, i: int) -> str:
    return str(props[i]).strip() if i < len(props) and props[i] is not None else ""


def evento_de_registro(d: dict) -> Optional[Evento]:
    """Un registro del PowerShell -> Evento (None si no interesa)."""
    fecha = _fecha(d.get("Fecha"))
    if fecha is None:
        return None
    log, eid, prov = d.get("Log") or "", int(d.get("Id") or 0), d.get("Prov") or ""
    p = [str(v) for v in _lista(d.get("P"))]
    if log == "System" and eid == 41:
        codigo = int(_p(p, 0)) if _p(p, 0).isdigit() else 0
        if codigo:
            nombre, sig, _c = explicar_codigo(codigo)
            return Evento(fecha, TIPO_PANTALLAZO, f"Pantallazo azul {texto_codigo(codigo)}", sig, codigo=codigo)
        return Evento(fecha, TIPO_APAGADO, "Se reinició sin apagarse bien",
                      "Corte de luz, botón de encendido mantenido o el equipo se quedó colgado")
    if log == "System" and eid == 1001 and ("SystemErrorReporting" in prov or "BugCheck" in prov):
        codigo = codigo_de_texto(_p(p, 0)) or codigo_de_texto(d.get("Msg") or "")
        volcado = _p(p, 1)
        nombre, sig, _c = explicar_codigo(codigo)
        return Evento(fecha, TIPO_PANTALLAZO, f"Pantallazo azul {texto_codigo(codigo)}", sig, codigo=codigo,
                      volcado=volcado if volcado.lower().endswith(".dmp") else "")
    if log == "System" and eid == 6008:
        return Evento(fecha, TIPO_APAGADO, "El apagado anterior fue inesperado", "Windows lo anota al volver a arrancar")
    if log == "Application" and eid == 1000:
        app, modulo, exc = _p(p, 0), _p(p, 3), _p(p, 6)
        if modulo.lower() == "unknown":
            modulo = ""
        return Evento(fecha, TIPO_CIERRE, f"{app} se cerró", explicar_excepcion(exc), app=app, ruta=_p(p, 10),
                      modulo=modulo, excepcion=exc)
    if log == "Application" and eid == 1002:
        app = _p(p, 0)
        return Evento(fecha, TIPO_BLOQUEO, f"{app} dejó de responder", "Windows la cerró al no responder", app=app,
                      ruta=_p(p, 5))
    return None


def interpretar(datos, dias: int = 30) -> Estabilidad:
    """Respuesta JSON del PowerShell -> Estabilidad (tolerante a campos que falten)."""
    est = Estabilidad(dias=dias)
    if not isinstance(datos, dict):
        return est
    for d in _lista(datos.get("Eventos")):
        if isinstance(d, dict):
            e = evento_de_registro(d)
            if e:
                est.eventos.append(e)
    for d in _lista(datos.get("Volcados")):
        f = _fecha(d.get("Fecha")) if isinstance(d, dict) else None
        if f:
            est.volcados.append(Volcado(d.get("Ruta") or "", f, int(d.get("Tam") or 0)))
    try:
        est.indice = float(datos["Indice"]) if datos.get("Indice") is not None else None
    except (TypeError, ValueError):
        est.indice = None
    # Un pantallazo deja dos eventos (41 con el código y 1001 con el volcado) casi a la vez: se queda uno
    est.eventos = _sin_duplicados(est.eventos)
    est.eventos.sort(key=lambda e: e.fecha, reverse=True)
    return est


def _sin_duplicados(eventos: List[Evento]) -> List[Evento]:
    res: List[Evento] = []
    for e in sorted(eventos, key=lambda x: x.fecha):
        if e.tipo == TIPO_PANTALLAZO:
            previo = next((x for x in res if x.tipo == TIPO_PANTALLAZO and abs((x.fecha - e.fecha).total_seconds()) < 600
                           and (x.codigo == e.codigo or x.codigo is None or e.codigo is None)), None)
            if previo:
                previo.volcado = previo.volcado or e.volcado
                previo.codigo = previo.codigo or e.codigo
                continue
        if e.tipo == TIPO_APAGADO:
            # 41 y 6008 del mismo arranque; y un 41 sin código justo tras un pantallazo es el mismo reinicio
            if any(abs((x.fecha - e.fecha).total_seconds()) < 600 and x.tipo in (TIPO_APAGADO, TIPO_PANTALLAZO) for x in res):
                continue
        res.append(e)
    return res


def leer(dias: int = 30) -> Estabilidad:
    dias = max(1, min(int(dias), 365))
    datos = run_powershell_json(_PS.replace("__DIAS__", str(dias)), timeout=120)
    return interpretar(datos, dias)


# ---------------------------------------------------------------------------------------------
# Agrupaciones
# ---------------------------------------------------------------------------------------------

def pantallazos(eventos: List[Evento], volcados: List[Volcado]) -> List[Pantallazo]:
    res = []
    for e in eventos:
        if e.tipo != TIPO_PANTALLAZO:
            continue
        nombre, sig, causa = explicar_codigo(e.codigo)
        volcado = e.volcado
        if not volcado:
            # El volcado se escribe al reiniciar: el más cercano en las 2 horas siguientes
            cerca = [v for v in volcados if 0 <= (v.fecha - e.fecha).total_seconds() <= 7200 or abs((v.fecha - e.fecha).total_seconds()) < 600]
            if cerca:
                volcado = min(cerca, key=lambda v: abs((v.fecha - e.fecha).total_seconds())).ruta
        res.append(Pantallazo(e.fecha, e.codigo, nombre, sig, causa, volcado))
    return res


def apps_que_fallan(eventos: List[Evento]) -> List[AppFallos]:
    por_app: Dict[str, List[Evento]] = {}
    for e in eventos:
        if e.tipo in (TIPO_CIERRE, TIPO_BLOQUEO) and e.app:
            por_app.setdefault(e.app.lower(), []).append(e)
    res = []
    for _k, lista in por_app.items():
        modulos: Dict[str, int] = {}
        excs: Dict[str, int] = {}
        for e in lista:
            if e.modulo:
                modulos[e.modulo] = modulos.get(e.modulo, 0) + 1
            if e.excepcion:
                excs[e.excepcion] = excs.get(e.excepcion, 0) + 1
        mod = max(modulos, key=modulos.get) if modulos else ""
        exc = max(excs, key=excs.get) if excs else ""
        res.append(AppFallos(lista[0].app, sum(1 for e in lista if e.tipo == TIPO_CIERRE), sum(1 for e in lista if e.tipo == TIPO_BLOQUEO),
                             max(e.fecha for e in lista), mod, explicar_excepcion(exc) if exc else "",
                             next((e.ruta for e in lista if e.ruta), "")))
    res.sort(key=lambda a: (a.total, a.ultimo), reverse=True)
    return res


# ---------------------------------------------------------------------------------------------
# Análisis de un volcado (kd.exe, si está)
# ---------------------------------------------------------------------------------------------

def ruta_kd() -> Optional[str]:
    for base in (os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), os.environ.get("ProgramFiles", r"C:\Program Files")):
        for arq in ("x64", "arm64", "x86"):
            r = os.path.join(base, "Windows Kits", "10", "Debuggers", arq, "kd.exe")
            if os.path.isfile(r):
                return r
    return None


def interpretar_analisis(salida: str) -> Dict[str, str]:
    """Campos útiles de «!analyze -v»: IMAGE_NAME, MODULE_NAME, BUGCHECK_CODE, PROCESS_NAME,
    FAILURE_BUCKET_ID y la línea «Probably caused by»."""
    res: Dict[str, str] = {}
    for clave in ("IMAGE_NAME", "MODULE_NAME", "BUGCHECK_CODE", "PROCESS_NAME", "FAILURE_BUCKET_ID"):
        m = re.search(rf"^\s*{clave}:\s*(\S.*?)\s*$", salida, re.M)
        if m:
            res[clave] = m.group(1)
    m = re.search(r"Probably caused by\s*:\s*(\S+)", salida, re.I)
    if m:
        res["PROBABLY"] = m.group(1)
    imagen = res.get("IMAGE_NAME") or (res.get("PROBABLY", "").split("(")[0] if res.get("PROBABLY") else "")
    if imagen:
        res["CONTROLADOR"] = imagen
        res["QUE_ES"] = explicar_controlador(imagen)
    return res


def analizar_volcado(ruta: str, timeout: int = 300) -> Tuple[bool, Dict[str, str], str]:
    """(ok, campos, mensaje). Necesita kd.exe y permiso de administrador para leer el volcado.
    Los símbolos de Microsoft se descargan la primera vez (hace falta internet) a una carpeta temporal."""
    kd = ruta_kd()
    if not kd:
        return False, {}, ("No se puede saber el controlador sin las «Herramientas de depuración para Windows» "
                           "(parte del Windows SDK de Microsoft, gratuito). Con ellas instaladas, este botón analiza el volcado.")
    if not os.path.isfile(ruta):
        return False, {}, f"No se encuentra el volcado:\n{ruta}"
    simbolos = os.path.join(tempfile.gettempdir(), "MASys_simbolos")
    env = dict(os.environ, _NT_SYMBOL_PATH=f"srv*{simbolos}*https://msdl.microsoft.com/download/symbols")
    try:
        r = subprocess.run([kd, "-z", ruta, "-c", "!analyze -v; q"], capture_output=True, timeout=timeout, env=env,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return False, {}, "El análisis tardó demasiado (la primera vez descarga símbolos de Microsoft: inténtalo de nuevo)."
    except OSError as e:
        return False, {}, f"No se pudo ejecutar kd.exe: {e}"
    salida = (r.stdout or b"").decode("utf-8", errors="replace")
    campos = interpretar_analisis(salida)
    if not campos.get("CONTROLADOR"):
        return False, campos, "El análisis no indicó el controlador (puede faltar permiso de administrador o los símbolos)."
    return True, campos, ""


def abrir_carpeta_volcados() -> Tuple[bool, str]:
    carpeta = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Minidump")
    if not os.path.isdir(carpeta):
        return False, "No hay carpeta de volcados (Windows no ha guardado ninguno)."
    try:
        os.startfile(carpeta)
        return True, ""
    except OSError as e:
        return False, str(e)


def a_json(est: Estabilidad) -> str:
    """Resumen serializable (para la puntuación de salud y las pruebas)."""
    return json.dumps({"dias": est.dias, "indice": est.indice, **est.resumen()})
