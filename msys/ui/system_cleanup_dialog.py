# -*- coding: utf-8 -*-
"""Ventana de progreso para la limpieza de la partición de sistema —
diálogo autónomo (no es un mixin), se instancia bajo demanda desde
SoporteMixin._soporte_limpiar_particion_sistema, con su propio hilo
(SystemCleanupThread).

A diferencia de CHKDSKDialog/SFCDISMDialog, SystemCleanupThread es UNA
única llamada bloqueante a PowerShell (sin proceso propio leído carácter
a carácter), así que no hay forma de interrumpirla a mitad sin arriesgar
dejar el hilo de Qt emitiendo señales hacia un diálogo ya destruido —
deliberadamente NO se ofrece 'Detener': se ignora el cierre de la ventana
mientras siga en curso, igual que se ignoraría en cualquier operación
atómica sin punto de corte seguro."""

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar, QTextEdit,
    QPushButton,
)

from msys.collectors import SystemCleanupThread, _tam_legible


class SystemCleanupDialog(QDialog):
    """Tras cerrarse (exec_()), el llamador debe leer self.resultado
    (None si hubo error — ver self.resultado_error) con las claves
    'categorias' (dict nombre->bytes liberados) y 'espacio_liberado'
    (bytes) — este diálogo no toca RepairAuditDB."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Limpieza de partición de sistema")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(560, 420)
        self.resultado = None
        self.resultado_error = ""
        self._thread = None
        self._terminado = False

        layout = QVBoxLayout(self)

        info = QLabel(
            "Se limpiarán temporales y cachés de todos los perfiles, hibernación "
            "(si estaba activa), Prefetch (solo en SSD), Windows.old, versiones "
            "antiguas del DriverStore, caché de Delivery Optimization, logs CBS "
            "archivados y la papelera de reciclaje (todas las unidades).\n\n"
            "⏳ No se puede interrumpir a mitad: es una operación atómica, "
            "espera a que termine."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.lbl_etapa = QLabel("🔄 Iniciando...")
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
        self.btn_cerrar = QPushButton("Cerrar")
        self.btn_cerrar.setEnabled(False)
        self.btn_cerrar.clicked.connect(self.accept)
        botones.addWidget(self.btn_cerrar)
        layout.addLayout(botones)

        QTimer.singleShot(0, self._iniciar)

    def _iniciar(self):
        self._thread = SystemCleanupThread()
        self._thread.progress.connect(self._on_progreso)
        self._thread.finished_ok.connect(self._on_finalizado)
        self._thread.finished_error.connect(self._on_error)
        self._thread.start()

    def _on_progreso(self, pct: int, mensaje: str):
        self.progress.setValue(pct)
        self.lbl_etapa.setText(f"🔄 {mensaje}")

    def _on_finalizado(self, data: dict):
        self._terminado = True
        self.resultado = data
        self.lbl_etapa.setText("✅ Finalizado")
        self.progress.setValue(100)

        categorias = data.get("categorias", {})
        liberado = data.get("espacio_liberado", 0)
        lineas = [f"Espacio en disco recuperado: {_tam_legible(liberado)}", ""]
        for nombre, bytes_liberados in sorted(categorias.items(), key=lambda kv: -kv[1]):
            valor = _tam_legible(bytes_liberados) if bytes_liberados > 0 else "hecho"
            lineas.append(f"  • {nombre}: {valor}")
        self.log_view.setPlainText("\n".join(lineas))
        self.btn_cerrar.setEnabled(True)

    def _on_error(self, mensaje: str):
        self._terminado = True
        self.resultado_error = mensaje
        self.lbl_etapa.setText("❌ Error")
        self.log_view.setPlainText(mensaje)
        self.btn_cerrar.setEnabled(True)

    def closeEvent(self, event):
        if self._terminado:
            event.accept()
        else:
            event.ignore()
