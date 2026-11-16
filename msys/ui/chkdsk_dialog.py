# -*- coding: utf-8 -*-
"""Ventana de progreso para CHKDSK — mismo patrón que SFCDISMDialog:
diálogo autónomo (no es un mixin), se instancia bajo demanda desde
SoporteMixin._soporte_verificar_disco, con su propio hilo
(CHKDSKRepairThread) porque puede tardar (sobre todo con /r) y necesita
barra de progreso y log en vivo en vez de un QMessageBox de confirmar/
avisar."""

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar, QTextEdit,
    QPushButton, QMessageBox,
)

from msys.collectors import CHKDSKRepairThread


class CHKDSKDialog(QDialog):
    """Tras cerrarse (exec_()), el llamador debe leer self.resultado_exito
    (None si se cerró sin haber terminado), self.resultado_log y
    self.resultado_programado (True si Windows programó la comprobación
    para el próximo reinicio en vez de ejecutarla, por tener la unidad en
    uso con /f o /r) — este diálogo no toca RepairAuditDB."""

    def __init__(self, letra: str, reparar: bool, buscar_sectores: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Verificar disco (CHKDSK) — {letra}:")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(640, 440)
        self.resultado_exito = None
        self.resultado_log = ""
        self.resultado_programado = False
        self._letra = letra
        self._reparar = reparar
        self._buscar_sectores = buscar_sectores
        self._thread = None

        layout = QVBoxLayout(self)

        modo = ("Buscar sectores defectuosos (/r, implica /f)" if buscar_sectores
                else "Reparar errores automáticamente (/f)" if reparar
                else "Solo lectura — no modifica nada, no bloquea la unidad")
        info = QLabel(
            f"Unidad: {letra}:\nModo: {modo}\n\n"
            "⏳ Puede tardar varios minutos (más con /r en discos grandes). "
            "Si la unidad está en uso y se pidió /f o /r, Windows puede "
            "programar la comprobación para el próximo reinicio en vez de "
            "ejecutarla ahora."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.lbl_etapa = QLabel("🔄 Verificando...")
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
        self._thread = CHKDSKRepairThread(self._letra, self._reparar, self._buscar_sectores)
        self._thread.progreso_porcentaje.connect(self.progress.setValue)
        self._thread.progreso_texto.connect(self._on_texto)
        self._thread.finished_ok.connect(self._on_finalizado)
        self._thread.start()

    def _on_texto(self, linea):
        self.log_view.append(linea)
        self.log_view.verticalScrollBar().setValue(self.log_view.verticalScrollBar().maximum())

    def _on_finalizado(self, exito, log_completo, programado):
        self.resultado_exito = exito
        self.resultado_log = log_completo
        self.resultado_programado = programado
        if programado:
            self.lbl_etapa.setText("📅 Programado para el próximo reinicio — la unidad estaba en uso")
        else:
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
            self, "Detener verificación",
            "CHKDSK sigue en curso. ¿Seguro que quieres detenerlo y cerrar?\n\n"
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
