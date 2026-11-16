# -*- coding: utf-8 -*-
"""Diálogo "Exportar cuentas de Outlook a PST" — lista las cuentas del
perfil de Outlook activo (vía COM) y exporta las seleccionadas a ficheros
.pst independientes. Diálogo autónomo, se instancia bajo demanda desde
EmailMixin.abrir_exportar_pst.

Portado de GESTION DE CORREOS\\GestCorreov4.ps1 (pestaña "Exportar a
PST")."""

import os
import re
import time

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QMessageBox, QAbstractItemView, QProgressBar, QLineEdit,
    QCheckBox,
)

from msys.collectors import OutlookAccountsListThread, OutlookPSTExportThread
from msys.core.utils import dialogo_elegir_carpeta, get_informes_folder
from msys.widgets.delegates import _setup_header


def _nombre_seguro(nombre: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", nombre)


class OutlookPSTExportDialog(QDialog):
    def __init__(self, parent=None, cuenta_preseleccionada: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Exportar cuentas de Outlook a PST")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(680, 480)
        self._thread_listado = None
        self._thread_export = None
        self._cuenta_preseleccionada = cuenta_preseleccionada

        c = (parent._dash_colors() if parent and hasattr(parent, "_dash_colors") else {
            "title": "#1a2634", "muted": "#8a94a6",
        })

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(22, 20, 22, 18)

        cabecera = QHBoxLayout()
        cabecera.setSpacing(12)
        lbl_icono = QLabel("📤")
        lbl_icono.setStyleSheet("font-size: 26pt; background: transparent; border: none;")
        cabecera.addWidget(lbl_icono)
        col_titulo = QVBoxLayout()
        col_titulo.setSpacing(2)
        lbl_titulo = QLabel("Exportar cuentas de Outlook a PST")
        lbl_titulo.setWordWrap(True)
        lbl_titulo.setStyleSheet(
            f"font-size: 13pt; font-weight: 600; color: {c['title']}; background: transparent; border: none;")
        col_titulo.addWidget(lbl_titulo)
        lbl_subtitulo = QLabel("Cada cuenta seleccionada se exporta a su propio archivo .pst")
        lbl_subtitulo.setWordWrap(True)
        lbl_subtitulo.setStyleSheet(
            f"color: {c['muted']}; font-size: 9.5pt; background: transparent; border: none;")
        col_titulo.addWidget(lbl_subtitulo)
        cabecera.addLayout(col_titulo, 1)
        layout.addLayout(cabecera)

        fila_carpeta = QHBoxLayout()
        fila_carpeta.addWidget(QLabel("Carpeta de destino:"))
        self.txt_carpeta = QLineEdit(get_informes_folder())
        fila_carpeta.addWidget(self.txt_carpeta, 1)
        self.btn_elegir_carpeta = QPushButton("📁 Elegir...")
        self.btn_elegir_carpeta.clicked.connect(self._elegir_carpeta)
        fila_carpeta.addWidget(self.btn_elegir_carpeta)
        layout.addLayout(fila_carpeta)

        self.lbl_estado = QLabel("🔄 Conectando con Outlook...")
        self.lbl_estado.setObjectName("fieldLabel")
        layout.addWidget(self.lbl_estado)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        layout.addWidget(self.progress)

        self.tabla = QTableWidget(0, 2)
        self.tabla.setHorizontalHeaderLabels(["Cuenta", "Tipo"])
        self.tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tabla.setSelectionMode(QAbstractItemView.NoSelection)
        self.tabla.verticalHeader().setVisible(False)
        _setup_header(self.tabla, 2, stretch_col=0)
        layout.addWidget(self.tabla, 1)

        botones = QHBoxLayout()
        self.btn_marcar_todo = QPushButton("☑️ Marcar todo")
        self.btn_marcar_todo.clicked.connect(lambda: self._marcar_todo(True))
        botones.addWidget(self.btn_marcar_todo)
        self.btn_desmarcar_todo = QPushButton("☐ Desmarcar todo")
        self.btn_desmarcar_todo.clicked.connect(lambda: self._marcar_todo(False))
        botones.addWidget(self.btn_desmarcar_todo)
        botones.addStretch()
        self.btn_exportar = QPushButton("📤 Exportar seleccionadas")
        self.btn_exportar.setObjectName("primary")
        self.btn_exportar.setEnabled(False)
        self.btn_exportar.clicked.connect(self._exportar)
        botones.addWidget(self.btn_exportar)
        self.btn_cerrar = QPushButton("Cerrar")
        self.btn_cerrar.clicked.connect(self.reject)
        botones.addWidget(self.btn_cerrar)
        layout.addLayout(botones)

        self._iniciar_listado()

    def _elegir_carpeta(self):
        carpeta = dialogo_elegir_carpeta(self, "Carpeta de destino para los .pst", self.txt_carpeta.text())
        if carpeta:
            self.txt_carpeta.setText(carpeta)

    def _iniciar_listado(self):
        self._thread_listado = OutlookAccountsListThread()
        self._thread_listado.finished_ok.connect(self._on_cuentas_listadas)
        self._thread_listado.finished_error.connect(self._on_error_listado)
        self._thread_listado.start()

    def _on_cuentas_listadas(self, cuentas):
        self.progress.setVisible(False)
        self.tabla.setRowCount(len(cuentas))
        for fila, cuenta in enumerate(cuentas):
            item_check = QTableWidgetItem(cuenta["nombre"])
            item_check.setFlags(item_check.flags() | Qt.ItemIsUserCheckable)
            if self._cuenta_preseleccionada:
                marcar = cuenta["nombre"] == self._cuenta_preseleccionada
            else:
                marcar = cuenta["tiene_carpeta"]
            item_check.setCheckState(Qt.Checked if marcar else Qt.Unchecked)
            item_check.setData(Qt.UserRole, cuenta)
            self.tabla.setItem(fila, 0, item_check)
            self.tabla.setItem(fila, 1, QTableWidgetItem(cuenta["tipo"]))

        if cuentas:
            self.lbl_estado.setText(f"✅ {len(cuentas)} cuenta(s) encontrada(s) en el perfil de Outlook")
            self.btn_exportar.setEnabled(True)
        else:
            self.lbl_estado.setText("⚠️ No se encontró ninguna cuenta en el perfil de Outlook activo.")

    def _on_error_listado(self, mensaje):
        self.progress.setVisible(False)
        self.lbl_estado.setText(f"❌ No se pudo conectar con Outlook: {mensaje}")

    def _marcar_todo(self, marcar: bool):
        estado = Qt.Checked if marcar else Qt.Unchecked
        for fila in range(self.tabla.rowCount()):
            self.tabla.item(fila, 0).setCheckState(estado)

    def _cuentas_seleccionadas(self):
        seleccionadas = []
        for fila in range(self.tabla.rowCount()):
            item = self.tabla.item(fila, 0)
            if item.checkState() == Qt.Checked:
                seleccionadas.append(item.data(Qt.UserRole))
        return seleccionadas

    def _preguntar_conflicto(self, nombre_cuenta: str, pst_path: str) -> str:
        """Devuelve 'sobrescribir' / 'anadir' / 'renombrar' / 'cancelar'."""
        box = QMessageBox(self)
        box.setWindowFlags(box.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        box.setWindowTitle("Archivo PST existente")
        box.setIcon(QMessageBox.Warning)
        box.setText(f"Ya existe un archivo PST para «{nombre_cuenta}»:\n{pst_path}\n\n¿Qué deseas hacer?")
        btn_sobrescribir = box.addButton("Sobrescribir", QMessageBox.DestructiveRole)
        btn_anadir = box.addButton("Añadir al existente", QMessageBox.AcceptRole)
        btn_renombrar = box.addButton("Usar nombre alternativo", QMessageBox.ActionRole)
        box.addButton("Cancelar", QMessageBox.RejectRole)
        box.exec_()
        clicked = box.clickedButton()
        if clicked is btn_sobrescribir:
            return "sobrescribir"
        if clicked is btn_anadir:
            return "anadir"
        if clicked is btn_renombrar:
            return "renombrar"
        return "cancelar"

    def _exportar(self):
        seleccionadas = self._cuentas_seleccionadas()
        if not seleccionadas:
            self._avisar_local("Exportar a PST", "Selecciona al menos una cuenta.", QMessageBox.Warning)
            return

        carpeta = self.txt_carpeta.text().strip()
        if not carpeta:
            self._avisar_local("Exportar a PST", "Indica una carpeta de destino.", QMessageBox.Warning)
            return
        os.makedirs(carpeta, exist_ok=True)

        plan = []
        for cuenta in seleccionadas:
            nombre = cuenta["nombre"]
            pst_path = os.path.join(carpeta, f"{_nombre_seguro(nombre)}.pst")
            modo = "crear"
            if os.path.exists(pst_path):
                decision = self._preguntar_conflicto(nombre, pst_path)
                if decision == "cancelar":
                    continue
                if decision == "renombrar":
                    ts = time.strftime("%Y%m%d_%H%M%S")
                    pst_path = os.path.join(carpeta, f"{_nombre_seguro(nombre)}_{ts}.pst")
                    modo = "crear"
                elif decision == "sobrescribir":
                    modo = "sobrescribir"
                else:
                    modo = "anadir"
            plan.append({"nombre": nombre, "pst_path": pst_path, "modo": modo})

        if not plan:
            self.lbl_estado.setText("Exportación cancelada.")
            return

        parent = self.parent()
        if parent and hasattr(parent, "_confirmar_reparacion"):
            cambios = [f"Exportar «{p['nombre']}» a {p['pst_path']}" for p in plan]
            if not parent._confirmar_reparacion(
                "Exportar a PST", cambios=cambios, reversible=True, objetivo="Outlook",
            ):
                return

        self.btn_exportar.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.lbl_estado.setText("🔄 Exportando...")

        self._thread_export = OutlookPSTExportThread(plan)
        self._thread_export.progress.connect(self._on_progress_export)
        self._thread_export.cuenta_completada.connect(self._on_cuenta_completada)
        self._thread_export.finished_ok.connect(self._on_export_finalizado)
        self._thread_export.finished_error.connect(self._on_error_export)
        self._thread_export.start()

    def _on_progress_export(self, valor, texto):
        self.progress.setValue(valor)
        self.lbl_estado.setText(f"🔄 {texto}")

    def _on_cuenta_completada(self, nombre, exito, detalle):
        parent = self.parent()
        if parent and hasattr(parent, "repair_audit_db"):
            parent.repair_audit_db.registrar(
                seccion="📧 Cuentas de Correo", accion="Exportar cuenta a PST",
                objetivo=nombre, exito=exito, detalle=detalle,
            )

    def _on_export_finalizado(self, ok_count, total):
        self.progress.setVisible(False)
        self.btn_exportar.setEnabled(True)
        self.lbl_estado.setText(f"✅ Exportación completada: {ok_count}/{total} cuenta(s) correctamente")
        parent = self.parent()
        if parent and hasattr(parent, "refresh_email_accounts"):
            parent.refresh_email_accounts()

    def _on_error_export(self, mensaje):
        self.progress.setVisible(False)
        self.btn_exportar.setEnabled(True)
        self.lbl_estado.setText(f"❌ {mensaje}")

    def _avisar_local(self, titulo, texto, icono=QMessageBox.Information):
        box = QMessageBox(self)
        box.setWindowFlags(box.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        box.setWindowTitle(titulo)
        box.setText(texto)
        box.setIcon(icono)
        box.exec_()

    def closeEvent(self, event):
        # Esperar con timeout y cerrar igualmente si seguía vivo destruía
        # un QThread en ejecución (riesgo real de "QThread: Destroyed
        # while thread is still running") — aquí hay de por medio una
        # exportación real a Outlook (AddStore/CopyTo/RemoveStore), así
        # que además se pide confirmación explícita en vez de cancelar en
        # silencio.
        if self._thread_export is not None and self._thread_export.isRunning():
            if not self._confirmar_cerrar_con_exportacion_en_curso():
                event.ignore()
                return
            self._thread_export.request_stop()
            self._thread_export.wait(15000)
            if self._thread_export.isRunning():
                self._avisar_local(
                    "Exportar a PST",
                    "La exportación sigue en curso (terminando la cuenta actual) y no se puede "
                    "cerrar esta ventana todavía. Vuelve a intentarlo en un momento.",
                    QMessageBox.Warning)
                event.ignore()
                return

        if self._thread_listado is not None and self._thread_listado.isRunning():
            self._thread_listado.wait(3000)
        event.accept()

    def _confirmar_cerrar_con_exportacion_en_curso(self) -> bool:
        box = QMessageBox(self)
        box.setWindowFlags(box.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        box.setWindowTitle("Exportación en curso")
        box.setIcon(QMessageBox.Warning)
        box.setText("Hay una exportación a PST en curso. Si cierras ahora, la cuenta que se esté "
                    "exportando en este momento terminará, pero no se empezará con las siguientes. "
                    "¿Cerrar de todos modos?")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        return box.exec_() == QMessageBox.Yes
