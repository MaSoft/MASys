# -*- coding: utf-8 -*-
"""Ejecución de «🔎 Comprobar ahora» (4.8.0) en la ventana principal de MASys.

Por cada sección de la ficha (msys/domain/comprobar.py:secciones_de), una
detrás de otra y sin bloquear la ventana:
1. espera a que MASys no esté leyendo otra cosa (self.worker libre);
2. vuelve a leer la sección con su refresh_* (el de «🔄 Sincronizar Todo»);
3. espera a que termine y pasa el detector de «🛠️ Soporte» de esa sección,
   quitando los avisos que el técnico ya validó.

Solo lee. Las acciones de cada aviso son las del Soporte y piden
confirmación ellas mismas."""

from typing import Callable, Iterable, List, Optional

from PyQt5.QtCore import QObject, QTimer, pyqtSignal

from msys.domain import comprobar as cp

ESPERA_MS = 300
TOPE_LIBRE = 400       # ~2 min esperando a que MASys termine otra lectura
TOPE_LECTURA = 4000    # ~20 min para una lectura (como «Sincronizar Todo»: red de seguridad, no un límite normal)


def _validados_reales() -> Iterable[str]:
    from msys.core.utils import _get_avisos_soporte_validados
    return _get_avisos_soporte_validados().keys()


class Comprobador(QObject):
    cambio = pyqtSignal()
    terminado = pyqtSignal()

    def __init__(self, host, ficha_id: str, secciones: List[str],
                 validados: Optional[Callable[[], Iterable[str]]] = None, espera_ms: int = ESPERA_MS):
        super().__init__()
        self.host = host
        self.ficha_id = ficha_id
        self.resultados = [cp.Resultado(s) for s in secciones]
        self._validados = validados or _validados_reales
        self._espera = espera_ms
        self._i = 0
        self._cancelado = False
        self._error_lectura = ""
        self.activo = False

    # ── Control ─────────────────────────────────────────────────────────
    def iniciar(self):
        self.activo = True
        QTimer.singleShot(0, self._siguiente)

    def cancelar(self):
        if not self.activo:
            return
        self._cancelado = True
        self.activo = False
        for r in self.resultados:
            if r.estado in (cp.PENDIENTE, cp.LEYENDO):
                r.estado = cp.CANCELADA
        self.cambio.emit()
        self.terminado.emit()

    def resumen(self) -> str:
        return cp.resumen(self.resultados)

    # ── Secuencia ───────────────────────────────────────────────────────
    def _ocupado(self) -> bool:
        w = getattr(self.host, "worker", None)
        try:
            return w is not None and w.isRunning()
        except RuntimeError:     # objeto Qt ya destruido
            return False

    def _actual(self) -> cp.Resultado:
        return self.resultados[self._i]

    def _siguiente(self):
        if self._cancelado:
            return
        if self._i >= len(self.resultados):
            self.activo = False
            self.cambio.emit()
            self.terminado.emit()
            return
        self._actual().estado = cp.LEYENDO
        self.cambio.emit()
        self._esperar_libre(TOPE_LIBRE)

    def _fallo(self, texto: str):
        r = self._actual()
        r.estado, r.error = cp.ERROR, texto
        self._i += 1
        self.cambio.emit()
        QTimer.singleShot(0, self._siguiente)

    def _esperar_libre(self, intentos: int):
        if self._cancelado:
            return
        if self._ocupado():
            if intentos > 0:
                QTimer.singleShot(self._espera, lambda: self._esperar_libre(intentos - 1))
            else:
                self._fallo("MASys sigue ocupado leyendo otra sección")
            return
        self._recargar()

    def _recargar(self):
        seccion = self._actual().seccion
        metodo = getattr(self.host, cp.RECARGA.get(seccion, ""), None)
        if metodo is None:
            self._fallo("Esta sección no se puede volver a leer")
            return
        antes = getattr(self.host, "worker", None)
        self._error_lectura = ""
        try:
            metodo()
        except Exception as e:
            self._fallo(f"No se pudo leer: {e}")
            return
        nuevo = getattr(self.host, "worker", None)
        if nuevo is None or nuevo is antes:
            self._fallo("No se pudo volver a leer la sección (MASys estaba ocupado)")
            return
        try:
            nuevo.finished_error.connect(lambda texto: setattr(self, "_error_lectura", str(texto) or "Error"))
        except (AttributeError, TypeError):
            pass
        self._esperar_fin(TOPE_LECTURA)

    def _esperar_fin(self, intentos: int):
        if self._cancelado:
            return
        if self._ocupado():
            if intentos > 0:
                QTimer.singleShot(self._espera, lambda: self._esperar_fin(intentos - 1))
            else:
                self._fallo("La lectura no terminó")
            return
        # Las señales de datos del hilo llegan en cola: un instante para que se pinten en el modelo
        QTimer.singleShot(250, self._detectar)

    def _detectar(self):
        if self._cancelado:
            return
        r = self._actual()
        if self._error_lectura:
            self._fallo(f"No se pudo leer: {self._error_lectura}")
            return
        try:
            entrada = self.host._registro_soporte_por_seccion().get(r.seccion)
            detector = entrada[1] if entrada else None
            if detector is None:
                self._fallo("Esta sección no tiene comprobaciones")
                return
            detectados = detector()
            r.avisos, r.validados = cp.avisos_de(detectados, self._validados())
        except Exception as e:
            self._fallo(f"No se pudo comprobar: {e}")
            return
        r.estado = cp.AVISOS if r.avisos else cp.BIEN
        self._i += 1
        self.cambio.emit()
        QTimer.singleShot(0, self._siguiente)
