# -*- coding: utf-8 -*-
"""Hilo genérico para los apartados nuevos (Iconos, Extensiones, Características,
Herramientas de red, Migración, Móvil): ejecuta una función fuera de la
interfaz y devuelve su resultado o el error por señales.

Cada apartado guarda su propia Tarea (no usa self.worker de la ventana
principal), así una consulta larga de un apartado no bloquea al resto ni a
«Sincronizar Todo». com=True inicializa COM en el hilo (WScript.Shell, WMI...)."""

import logging
import traceback

from PyQt5.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)


class Tarea(QThread):
    ok = pyqtSignal(object)
    error = pyqtSignal(str)
    progreso = pyqtSignal(int, str)

    def __init__(self, funcion, *args, com: bool = False, **kwargs):
        super().__init__()
        self._funcion, self._args, self._kwargs, self._com = funcion, args, kwargs, com
        self._parar = False

    def parar(self):
        self._parar = True

    def debe_parar(self) -> bool:
        return self._parar

    def run(self):
        if self._com:
            import pythoncom
            pythoncom.CoInitialize()
        try:
            self.ok.emit(self._funcion(*self._args, **self._kwargs))
        except Exception as e:  # el error se enseña al usuario; el detalle va al registro
            logger.error("Tarea %s: %s\n%s", getattr(self._funcion, "__name__", "?"), e, traceback.format_exc())
            self.error.emit(str(e) or e.__class__.__name__)
        finally:
            if self._com:
                import pythoncom
                pythoncom.CoUninitialize()


def lanzar(dueno, atributo: str, funcion, *args, al_terminar=None, al_fallar=None, com=False, **kwargs) -> Tarea:
    """Crea, conecta y arranca una Tarea guardándola en dueno.<atributo> (para que
    no la recoja el recolector de basura). Si ya hay una en marcha en ese
    atributo, la devuelve sin lanzar otra."""
    actual = getattr(dueno, atributo, None)
    if actual is not None and actual.isRunning():
        return actual
    t = Tarea(funcion, *args, com=com, **kwargs)
    if al_terminar:
        t.ok.connect(al_terminar)
    if al_fallar:
        t.error.connect(al_fallar)
    setattr(dueno, atributo, t)
    t.start()
    return t
