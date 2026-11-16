# -*- coding: utf-8 -*-
"""Diálogo "Backup completo de controladores" — pensado para hacer copia
de TODOS los paquetes de controladores de terceros antes de reformatear/
reinstalar Windows, con vista previa de lo que se va a copiar y progreso
en vivo durante la exportación real (pnputil /export-driver * <destino>,
que puede tardar según el número de paquetes).

Mismo patrón que CHKDSKDialog: hilo aparte (DriverBackupThread) porque la
exportación no debe congelar la UI, con barra de progreso y estado en
texto; a diferencia de CHKDSK aquí SÍ hay un paso previo de elegir carpeta
antes de arrancar el hilo.

Se instancia bajo demanda desde el botón "🛠️ Soporte" de la sección
"🖥️ Adm. Dispositivos" — ver SoporteMixin._soporte_backup_controladores en
soporte.py, mismo sitio donde viven el resto de acciones clásicas."""

import csv
import io
import os
import subprocess
import sys

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QProgressBar,
    QTableWidget, QTableWidgetItem, QMessageBox, QAbstractItemView,
    QApplication,
)

from msys.core.utils import dialogo_elegir_carpeta
from msys.collectors import DriverBackupThread


class DriverBackupDialog(QDialog):
    HEADERS = ["Fabricante", "Paquete original", "Clase", "Versión", "Nombre INF"]

    def __init__(self, parent, driver_manager):
        super().__init__(parent)
        self.driver_manager = driver_manager
        self.setWindowTitle("Backup completo de controladores")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(820, 540)
        self._thread = None
        self._destino = ""

        c = (parent._dash_colors() if parent and hasattr(parent, "_dash_colors") else {
            "title": "#1a2634", "muted": "#8a94a6",
        })

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(22, 20, 22, 18)

        cabecera = QHBoxLayout()
        cabecera.setSpacing(12)
        lbl_icono = QLabel("💾")
        lbl_icono.setStyleSheet("font-size: 26pt; background: transparent; border: none;")
        cabecera.addWidget(lbl_icono)
        col_titulo = QVBoxLayout()
        col_titulo.setSpacing(2)
        lbl_titulo = QLabel("Backup completo de controladores")
        lbl_titulo.setStyleSheet(f"font-size: 13pt; font-weight: 600; color: {c['title']}; background: transparent; border: none;")
        col_titulo.addWidget(lbl_titulo)
        lbl_subtitulo = QLabel(
            "Copia todos los paquetes de controladores de terceros del almacén de Windows "
            "— para restaurarlos tras reformatear/reinstalar, sin tener que volver a buscar "
            "cada uno por separado"
        )
        lbl_subtitulo.setWordWrap(True)
        lbl_subtitulo.setStyleSheet(f"color: {c['muted']}; font-size: 9.5pt; background: transparent; border: none;")
        col_titulo.addWidget(lbl_subtitulo)
        cabecera.addLayout(col_titulo, 1)
        layout.addLayout(cabecera)

        self.lbl_estado = QLabel("🔄 Cargando vista previa...")
        self.lbl_estado.setObjectName("fieldLabel")
        layout.addWidget(self.lbl_estado)

        self.tabla = QTableWidget(0, len(self.HEADERS))
        self.tabla.setHorizontalHeaderLabels(self.HEADERS)
        self.tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tabla.setSelectionBehavior(QTableWidget.SelectRows)
        self.tabla.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.setSortingEnabled(True)
        self.tabla.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.tabla, 1)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        botones = QHBoxLayout()
        self.btn_actualizar = QPushButton("🔄 Actualizar vista previa")
        self.btn_actualizar.clicked.connect(self._cargar_preview)
        botones.addWidget(self.btn_actualizar)
        botones.addStretch()
        self.btn_iniciar = QPushButton("💾 Iniciar backup")
        self.btn_iniciar.setObjectName("primary")
        self.btn_iniciar.clicked.connect(self._elegir_carpeta_e_iniciar)
        botones.addWidget(self.btn_iniciar)
        self.btn_cerrar = QPushButton("Cerrar")
        self.btn_cerrar.clicked.connect(self._on_cerrar_clicked)
        botones.addWidget(self.btn_cerrar)
        layout.addLayout(botones)

        QTimer.singleShot(0, self._cargar_preview)

    def _cargar_preview(self):
        self.lbl_estado.setText("🔄 Cargando vista previa...")
        self.btn_actualizar.setEnabled(False)
        QApplication.processEvents()

        ok, salida = self.driver_manager.enum_third_party_drivers_csv()
        self.btn_actualizar.setEnabled(True)

        if not ok:
            self.lbl_estado.setText(f"❌ {salida}")
            return

        try:
            filas = list(csv.DictReader(io.StringIO(salida)))
        except csv.Error as e:
            self.lbl_estado.setText(f"❌ No se pudo interpretar la respuesta: {e}")
            return

        self.tabla.setSortingEnabled(False)
        self.tabla.setRowCount(len(filas))
        for i, fila in enumerate(filas):
            valores = [
                fila.get("ProviderName", ""), fila.get("OriginalName", ""),
                fila.get("ClassName", ""), fila.get("DriverVersion", ""),
                fila.get("DriverName", ""),
            ]
            for col, val in enumerate(valores):
                self.tabla.setItem(i, col, QTableWidgetItem(val))
        self.tabla.setSortingEnabled(True)

        if filas:
            self.lbl_estado.setText(f"✅ {len(filas)} paquete(s) de controladores de terceros listos para copiar")
        else:
            self.lbl_estado.setText("ℹ️ No se han encontrado controladores de terceros en el almacén de este equipo")
        self.btn_iniciar.setEnabled(bool(filas))

    def _elegir_carpeta_e_iniciar(self):
        destino = dialogo_elegir_carpeta(self, "Elegir carpeta destino del backup", "")
        if not destino:
            return
        try:
            if os.listdir(destino):
                if QMessageBox.question(
                    self, "Carpeta no vacía",
                    f"La carpeta elegida ya contiene archivos:\n\n{destino}\n\n"
                    "¿Continuar de todas formas? (no se borrará nada existente, "
                    "los controladores se añadirán junto a lo que ya haya)",
                    QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
                ) != QMessageBox.Yes:
                    return
        except OSError as e:
            QMessageBox.critical(self, "Error", f"No se pudo acceder a la carpeta:\n\n{e}")
            return

        self._destino = destino
        self.btn_iniciar.setEnabled(False)
        self.btn_actualizar.setEnabled(False)
        self.tabla.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.lbl_estado.setText("🔄 Exportando controladores...")

        self._thread = DriverBackupThread(destino, self.driver_manager)
        self._thread.progress.connect(self._on_progreso)
        self._thread.finished_ok.connect(self._on_finalizado_ok)
        self._thread.finished_error.connect(self._on_finalizado_error)
        self._thread.start()

    def _on_progreso(self, porcentaje, texto):
        self.progress.setValue(porcentaje)
        self.lbl_estado.setText(f"🔄 {texto}")

    def _on_finalizado_ok(self, destino):
        self.progress.setValue(100)
        self.lbl_estado.setText(f"✅ Backup completado en: {destino}")
        self.btn_cerrar.setText("Cerrar")

        parent = self.parent()
        if parent and hasattr(parent, "repair_audit_db"):
            parent.repair_audit_db.registrar(
                seccion="🖥️ Adm. Dispositivos", accion="Backup de controladores",
                objetivo=destino, exito=True, detalle="OK",
            )

        if QMessageBox.question(
            self, "Backup completado",
            f"Backup de controladores completado en:\n\n{destino}\n\n"
            "Se ha incluido un archivo COMO_RESTAURAR.txt con instrucciones. "
            "¿Abrir la carpeta ahora?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        ) == QMessageBox.Yes:
            try:
                if sys.platform == "win32":
                    os.startfile(destino)
                else:
                    subprocess.Popen(["xdg-open", destino])
            except OSError as e:
                QMessageBox.warning(self, "Aviso", f"No se pudo abrir la carpeta:\n\n{e}")

    def _on_finalizado_error(self, mensaje):
        self.progress.setVisible(False)
        self.lbl_estado.setText("❌ El backup no se pudo completar")
        self.btn_actualizar.setEnabled(True)
        self.btn_iniciar.setEnabled(True)
        self.tabla.setEnabled(True)

        parent = self.parent()
        if parent and hasattr(parent, "repair_audit_db"):
            parent.repair_audit_db.registrar(
                seccion="🖥️ Adm. Dispositivos", accion="Backup de controladores",
                objetivo=self._destino, exito=False, detalle=mensaje,
            )

        QMessageBox.warning(self, "Con avisos", f"No se pudo completar el backup:\n\n{mensaje}")

    def _hilo_en_marcha(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def _confirmar_y_detener_si_hace_falta(self) -> bool:
        if not self._hilo_en_marcha():
            return True
        if QMessageBox.question(
            self, "Backup en curso",
            "El backup sigue en curso. Cerrar ahora no lo detiene (pnputil sigue "
            "exportando en segundo plano hasta terminar), pero perderás la vista "
            "del progreso. ¿Cerrar de todas formas?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return False
        return True

    def _on_cerrar_clicked(self):
        if self._confirmar_y_detener_si_hace_falta():
            self.accept()

    def closeEvent(self, event):
        if self._confirmar_y_detener_si_hace_falta():
            event.accept()
        else:
            event.ignore()
