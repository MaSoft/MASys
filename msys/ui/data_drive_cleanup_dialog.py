# -*- coding: utf-8 -*-
"""Ventana de progreso para la limpieza 'adaptada' de una partición de
DATOS (no la de sistema) — diálogo autónomo, se instancia bajo demanda
desde SoporteMixin._soporte_limpiar_particion_datos, con su propio hilo
(DataDriveCleanupThread).

A diferencia de SystemCleanupDialog, aquí SÍ hay un bucle propio (pila de
carpetas recorridas una a una) con el que request_stop() puede cortar de
verdad entre elementos — mismo patrón de Detener que RecycleBinMoveThread,
sin barra de progreso numérica porque no se conoce el total de antemano
sin un preescaneo completo."""

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar, QTextEdit,
    QPushButton,
)

from msys.collectors import DataDriveCleanupThread, _tam_legible


class DataDriveCleanupDialog(QDialog):
    """Tras cerrarse (exec_()), el llamador debe leer self.resultado
    (None si hubo error o se cerró sin terminar — ver self.resultado_error)
    con las claves 'categorias' y 'espacio_liberado' — este diálogo no
    toca RepairAuditDB."""

    def __init__(self, letra: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Limpieza de partición de datos — {letra}:")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(560, 420)
        self.resultado = None
        self.resultado_error = ""
        self._letra = letra
        self._thread = None

        layout = QVBoxLayout(self)

        info = QLabel(
            f"Unidad: {letra}:\n\n"
            "Se vaciará la papelera de reciclaje de esta unidad y se eliminarán "
            "archivos basura reconocibles y seguros dispersos por toda ella "
            "(*.tmp, Thumbs.db, bloqueos de Office ~$*.doc/xls/ppt) — nunca "
            "archivos propios del usuario.\n\n"
            "⏳ Puede tardar según el tamaño de la unidad."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.lbl_etapa = QLabel("🔄 Iniciando...")
        self.lbl_etapa.setObjectName("fieldLabel")
        layout.addWidget(self.lbl_etapa)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # indeterminado: sin total fiable sin preescanear
        layout.addWidget(self.progress)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFontFamily("Consolas")
        layout.addWidget(self.log_view, 1)

        botones = QHBoxLayout()
        botones.addStretch()
        self.btn_cerrar = QPushButton("⏹ Detener")
        self.btn_cerrar.clicked.connect(self._on_cerrar_clicked)
        botones.addWidget(self.btn_cerrar)
        layout.addLayout(botones)

        QTimer.singleShot(0, self._iniciar)

    def _iniciar(self):
        self._thread = DataDriveCleanupThread(self._letra)
        self._thread.progreso.connect(self._on_progreso)
        self._thread.finished_ok.connect(self._on_finalizado)
        self._thread.finished_error.connect(self._on_error)
        self._thread.start()

    def _on_progreso(self, mensaje: str):
        self.lbl_etapa.setText(f"🔄 {mensaje}")

    def _on_finalizado(self, data: dict):
        self.resultado = data
        self.progress.setRange(0, 100)
        self.progress.setValue(100)

        categorias = data.get("categorias", {})
        liberado = data.get("espacio_liberado", 0)
        if categorias:
            self.lbl_etapa.setText("✅ Finalizado")
            lineas = [f"Espacio en disco recuperado: {_tam_legible(liberado)}", ""]
            for nombre, bytes_liberados in sorted(categorias.items(), key=lambda kv: -kv[1]):
                lineas.append(f"  • {nombre}: {_tam_legible(bytes_liberados)}")
        else:
            self.lbl_etapa.setText("✅ Finalizado — nada que limpiar")
            lineas = ["No se encontró papelera con contenido ni archivos basura en esta unidad."]
        self.log_view.setPlainText("\n".join(lineas))
        self.btn_cerrar.setText("Cerrar")
        self.btn_cerrar.setEnabled(True)

    def _on_error(self, mensaje: str):
        self.resultado_error = mensaje
        self.progress.setRange(0, 100)
        self.lbl_etapa.setText("❌ Error")
        self.log_view.setPlainText(mensaje)
        self.btn_cerrar.setText("Cerrar")
        self.btn_cerrar.setEnabled(True)

    def _hilo_en_marcha(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def _on_cerrar_clicked(self):
        if not self._hilo_en_marcha():
            self.accept()
            return
        self._thread.request_stop()
        self.lbl_etapa.setText("⏹ Deteniendo tras el elemento en curso...")
        self.btn_cerrar.setEnabled(False)

    def closeEvent(self, event):
        if self._hilo_en_marcha():
            self._thread.request_stop()
            self._thread.wait(10000)
        event.accept()
