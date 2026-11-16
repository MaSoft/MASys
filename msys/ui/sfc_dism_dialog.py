# -*- coding: utf-8 -*-
"""Ventana de progreso para la reparación SFC/DISM. Diálogo autónomo (no es
un mixin: se instancia bajo demanda desde SoporteMixin._soporte_sfc_dism),
porque a diferencia de las demás acciones de soporte esta es larga
(10-30 minutos) y necesita su propia barra de progreso y log en vivo en
vez de un QMessageBox de confirmar/avisar."""

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar, QTextEdit,
    QPushButton, QMessageBox,
)

from msys.collectors import SFCDISMRepairThread


class SFCDISMDialog(QDialog):
    """Tras cerrarse (exec_()), el llamador debe leer self.resultado_exito
    (None si se cerró sin haber terminado) y self.resultado_log para
    registrar la acción en RepairAuditDB — este diálogo no toca la BD."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Reparar archivos de sistema (SFC/DISM)")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(640, 460)
        self.resultado_exito = None
        self.resultado_log = ""
        self._thread = None

        layout = QVBoxLayout(self)

        info = QLabel(
            "Se ejecutará, en este orden:\n"
            "  1. DISM /RestoreHealth — repara la imagen de Windows\n"
            "  2. SFC /scannow — verifica y repara archivos de sistema\n\n"
            "⏳ Puede tardar entre 10 y 30 minutos. No apagues el equipo."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.lbl_etapa = QLabel("Iniciando...")
        self.lbl_etapa.setObjectName("fieldLabel")
        layout.addWidget(self.lbl_etapa)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
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

        # Arrancar tras el primer repintado, no en __init__: así el diálogo
        # ya es visible con su contenido inicial antes de que llegue el
        # primer progreso.
        QTimer.singleShot(0, self._iniciar)

    def _iniciar(self):
        self._thread = SFCDISMRepairThread()
        self._thread.etapa_cambiada.connect(self.lbl_etapa.setText)
        self._thread.progreso_porcentaje.connect(self._on_progreso)
        self._thread.progreso_texto.connect(self._on_texto)
        self._thread.finished_ok.connect(self._on_finalizado)
        self._thread.start()

    def _on_progreso(self, etapa, pct):
        self.progress.setValue(pct)

    def _on_texto(self, linea):
        self.log_view.append(linea)
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def _on_finalizado(self, exito, log_completo):
        self.resultado_exito = exito
        self.resultado_log = log_completo
        self.lbl_etapa.setText("✅ Finalizado" if exito else "⚠️ Finalizado con errores — revisa el registro")
        self.btn_cerrar.setText("Cerrar")

    def _hilo_en_marcha(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def _confirmar_y_detener_si_hace_falta(self) -> bool:
        """True si es seguro cerrar ya (hilo parado o el usuario confirmó
        detenerlo). No cierra el diálogo por sí misma — eso lo decide quien
        la llama (el botón hace accept(), closeEvent acepta/ignora el evento)."""
        if not self._hilo_en_marcha():
            return True
        if QMessageBox.question(
            self, "Detener reparación",
            "La reparación sigue en curso. ¿Seguro que quieres detenerla y cerrar?\n\n"
            "El resultado NO se registrará como completado.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        ) != QMessageBox.Yes:
            return False
        self._thread.request_stop()
        self._thread.wait(10000)
        return True

    def _on_cerrar_clicked(self):
        if self._confirmar_y_detener_si_hace_falta():
            self.accept()

    def closeEvent(self, event):
        if self._confirmar_y_detener_si_hace_falta():
            event.accept()
        else:
            event.ignore()
