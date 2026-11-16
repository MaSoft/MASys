# -*- coding: utf-8 -*-
"""5.6.x — Herramientas de diagnóstico de Windows integradas en MASys.

Los .ps1 de msys/tools/win/ son una COPIA PROPIA de las herramientas universales de _TEMP\\SCRIPTS_2 (commit 5d223d8):
no se propagan cambios en ningún sentido. Este módulo (sin Qt) los ejecuta con sus parámetros, deja los resultados en
INFORMES\\<herramienta>\\<fecha> y devuelve lo que han generado. stdin cerrado: si un script preguntara algo, falla en vez
de quedarse colgado (por eso las que cambian algo se lanzan siempre con -SinConfirmar DESPUÉS de confirmar en MASys)."""

import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple, Union

from msys import config as msys_config

CREATE_NO_WINDOW = 0x08000000

# clave: (script, carpeta de INFORMES, guía, título)
HERRAMIENTAS = {
    "accesos": ("Auditar-Accesos.ps1", "AUDITORIA_ACCESOS", "Guia_AuditoriaAccesos.html", "Auditoría de accesos"),
    "aviso_rdp": ("Configurar-AvisoRDP.ps1", "AVISO_RDP", "Guia_AvisoRDP.html", "Aviso de Escritorio remoto"),
    "bitlocker": ("Obtener-ClavesBitLocker.ps1", "BITLOCKER", "Guia_ClavesBitLocker.html", "Claves de recuperación BitLocker"),
    "tls": ("Diagnosticar-TLS.ps1", "DIAGNOSTICO_TLS", "Guia_DiagnosticoTLS.html", "Diagnóstico HTTPS / TLS"),
    "win11": ("Evaluar-Windows11.ps1", "WINDOWS11", "Guia_EvaluacionWindows11.html", "Evaluación de Windows 11"),
    "thunderbird": ("Exportar-Thunderbird.ps1", "THUNDERBIRD", "Guia_ExportarThunderbird.html", "Exportar Thunderbird"),
    "office": ("Instalar-Office.ps1", "INSTALAR_OFFICE", "Guia_InstalarOffice.html", "Instalar Microsoft 365 / Office"),
    "microcortes": ("Vigilar-Red.ps1", "MICROCORTES", "Guia_MicrocortesRed.html", "Vigilar microcortes de red"),
    "impresoras": ("Vigilar-Impresoras.ps1", "VIGILAR_IMPRESORAS", "Guia_MonitorImpresoras.html", "Vigilar impresoras de red"),
    "rendimiento": ("Monitor-Rendimiento.ps1", "RENDIMIENTO", "Guia_MonitorRendimiento.html", "Monitor de rendimiento"),
}

Argumento = Union[str, Tuple[str, object]]   # "-Interruptor" o ("-Parametro", valor | lista de valores)


@dataclass
class Resultado:
    codigo: int
    salida: str
    carpeta: str
    archivos: List[str] = field(default_factory=list)
    parado: bool = False

    def de_tipo(self, extension: str) -> List[str]:
        return [a for a in self.archivos if a.lower().endswith(extension)]


def ruta_script(clave: str) -> str:
    return msys_config.ruta_recurso("msys", "tools", "win", HERRAMIENTAS[clave][0])


def ruta_guia(clave: str) -> str:
    return msys_config.ruta_recurso("msys", "tools", "win", HERRAMIENTAS[clave][2])


def titulo(clave: str) -> str:
    return HERRAMIENTAS[clave][3]


def carpeta_resultados(clave: str, nueva: bool = True) -> str:
    base = os.path.join(msys_config.app_base_dir(), "INFORMES", HERRAMIENTAS[clave][1])
    carpeta = os.path.join(base, time.strftime("%Y%m%d_%H%M%S")) if nueva else base
    os.makedirs(carpeta, exist_ok=True)
    return carpeta


def _literal(valor) -> str:
    if isinstance(valor, bool):
        return "$true" if valor else "$false"
    if isinstance(valor, int):
        return str(valor)
    return "'" + str(valor).replace("'", "''") + "'"


def orden_powershell(script: str, argumentos: Sequence[Argumento]) -> str:
    """Línea de PowerShell con cada valor entre comillas simples (sin interpretar nada del valor)."""
    partes = ["[Console]::OutputEncoding = [Text.Encoding]::UTF8;", "&", _literal(script)]
    for a in argumentos:
        if isinstance(a, str):
            if not a.startswith("-") or " " in a:
                raise ValueError(f"Interruptor no válido: {a!r}")
            partes.append(a)
        else:
            nombre, valor = a
            if not str(nombre).startswith("-") or " " in str(nombre):
                raise ValueError(f"Parámetro no válido: {nombre!r}")
            if isinstance(valor, (list, tuple)):
                partes.append(f"{nombre} " + ",".join(_literal(v) for v in valor))
            else:
                partes.append(f"{nombre} {_literal(valor)}")
    return " ".join(partes)


def ejecutar(clave: str, argumentos: Sequence[Argumento], carpeta: str, timeout: Optional[int] = None,
             al_linea: Optional[Callable[[str], None]] = None, debe_parar: Optional[Callable[[], bool]] = None) -> Resultado:
    """Ejecuta la herramienta y devuelve su salida y los archivos que ha dejado en `carpeta`."""
    antes = _archivos(carpeta)
    orden = orden_powershell(ruta_script(clave), argumentos)
    senal = os.path.join(carpeta, ".parar")
    entorno = dict(os.environ, MASYS_PARAR=senal)
    proc = subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", orden],
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            creationflags=CREATE_NO_WINDOW, env=entorno)
    lineas: List[str] = []
    inicio = time.time()
    parado = False
    import threading

    def _leer():
        for crudo in iter(proc.stdout.readline, b""):
            try:
                linea = crudo.decode("utf-8")
            except UnicodeDecodeError:
                linea = crudo.decode("cp850", errors="replace")
            linea = linea.rstrip("\r\n")
            lineas.append(linea)
            if al_linea and linea.strip():
                al_linea(linea)

    lector = threading.Thread(target=_leer, daemon=True)
    lector.start()
    pedida = None
    while proc.poll() is None:
        if pedida is None and ((debe_parar and debe_parar()) or (timeout and time.time() - inicio > timeout)):
            # Primero la señal: las herramientas de vigilancia terminan su bucle y generan el informe
            parado, pedida = True, time.time()
            try:
                open(senal, "w").close()
            except OSError:
                pass
        if pedida is not None and time.time() - pedida > 90:
            _terminar_arbol(proc.pid)
            break
        time.sleep(0.3)
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        pass
    lector.join(timeout=5)
    try:
        os.remove(senal)
    except OSError:
        pass
    nuevos = sorted(_archivos(carpeta) - antes)
    return Resultado(proc.returncode if proc.returncode is not None else -1, "\n".join(lineas), carpeta, nuevos, parado)


def _archivos(carpeta: str) -> set:
    total = set()
    for raiz, _d, archivos in os.walk(carpeta):
        for a in archivos:
            total.add(os.path.join(raiz, a))
    return total


def _terminar_arbol(pid: int) -> None:
    """Para PowerShell y lo que haya lanzado (ping, setup.exe…)."""
    try:
        import psutil
        p = psutil.Process(pid)
        for h in p.children(recursive=True):
            try:
                h.kill()
            except psutil.Error:
                pass
        p.kill()
    except Exception:
        pass


def leer_csv(ruta: str, maximo: int = 5000) -> Tuple[List[str], List[List[str]]]:
    """Cabeceras y filas de un CSV de las herramientas (separador ; o ,; UTF-8 con o sin BOM)."""
    import csv
    for codificacion in ("utf-8-sig", "cp1252"):
        try:
            with open(ruta, encoding=codificacion, newline="") as f:
                muestra = f.read(4096)
                f.seek(0)
                sep = ";" if muestra.count(";") >= muestra.count(",") else ","
                filas = list(csv.reader(f, delimiter=sep))
            break
        except UnicodeDecodeError:
            continue
    else:
        return [], []
    if not filas:
        return [], []
    return filas[0], filas[1:maximo + 1]
