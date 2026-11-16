# -*- coding: utf-8 -*-
"""Ejecuta, uno detrás de otro, los recolectores de «🔄 Sincronizar Todo»
(msys/collectors.py) SIN la ventana de MASys: sin registro, sin bases de
datos, sin bandeja ni servidor. Cada recolector es un QThread con las señales
finished_ok(datos) y finished_error(texto); aquí se espera a cada uno con un
QEventLoop y un tope de tiempo.

Lo usa la ventana del agente (msys/agente/ventana.py)."""

import time
from typing import Callable, Dict, List, Optional

from PyQt5.QtCore import QEventLoop, QTimer

from msys.domain import agente as ag

TOPE_SEGUNDOS = 240     # un recolector que no termina en 4 minutos se da por fallido
_CLASES: Dict[str, type] = {}


def clase_de(nombre: str) -> type:
    if not _CLASES:
        from msys import collectors
        for _c, _t, _s, k, _a in ag.LECTORES:
            _CLASES[k] = getattr(collectors, k)
    return _CLASES[nombre]


def ejecutar_uno(nombre_clase: str, argumentos: dict, tope: float = TOPE_SEGUNDOS):
    """(datos, error, segundos). Nunca lanza: un fallo de un recolector no para el resto."""
    inicio = time.time()
    resultado = {"datos": None, "error": ""}
    try:
        hilo = clase_de(nombre_clase)(**argumentos)
    except Exception as e:
        return None, f"No se pudo iniciar: {e}", 0.0
    bucle = QEventLoop()
    terminado = [False]

    def ok(datos):
        resultado["datos"] = datos
        terminado[0] = True
        bucle.quit()

    def error(texto):
        resultado["error"] = str(texto) or "Error"
        terminado[0] = True
        bucle.quit()

    hilo.finished_ok.connect(ok)
    hilo.finished_error.connect(error)
    hilo.finished.connect(lambda: (terminado.__setitem__(0, True), bucle.quit()))
    reloj = QTimer()
    reloj.setSingleShot(True)
    reloj.timeout.connect(bucle.quit)
    reloj.start(int(tope * 1000))
    hilo.start()
    if not terminado[0]:
        bucle.exec_()
    reloj.stop()
    if hilo.isRunning():
        if hasattr(hilo, "request_stop"):
            hilo.request_stop()
        hilo.wait(5000)
        if hilo.isRunning():
            hilo.terminate()
            hilo.wait(2000)
        if not resultado["error"] and resultado["datos"] is None:
            resultado["error"] = f"No terminó en {int(tope)} segundos"
    elif resultado["datos"] is None and not resultado["error"]:
        # Terminó sin emitir nada (algunos recolectores emiten justo antes de acabar)
        bucle2 = QEventLoop()
        QTimer.singleShot(200, bucle2.quit)
        bucle2.exec_()
        if resultado["datos"] is None and not resultado["error"]:
            resultado["error"] = "Terminó sin datos"
    return resultado["datos"], resultado["error"], time.time() - inicio


def recolectar(claves: List[str], progreso: Optional[Callable[[int, int, str], None]] = None,
               cancelado: Callable[[], bool] = lambda: False, ejecutar=ejecutar_uno) -> dict:
    """Informe con las secciones pedidas. progreso(hecho, total, título)."""
    informe = ag.nuevo_informe(claves)
    try:
        from msys.core.utils import is_admin
        informe["administrador"] = bool(is_admin())
    except Exception:
        pass
    elegidos = [x for x in ag.LECTORES if x[0] in claves]
    for i, (clave, titulo, _seccion, clase, args) in enumerate(elegidos):
        if cancelado():
            informe["cancelado"] = True
            break
        if progreso:
            progreso(i, len(elegidos), titulo)
        datos, error, segundos = ejecutar(clase, dict(args))
        ag.anadir_seccion(informe, clave, datos, error, segundos)
    if progreso:
        progreso(len(elegidos), len(elegidos), "")
    from datetime import datetime
    informe["fin"] = datetime.now().isoformat(timespec="seconds")
    return informe
