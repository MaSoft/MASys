# -*- coding: utf-8 -*-
"""Diálogo "Cola de impresión" — trabajos pendientes de una impresora
concreta (Get-PrintJob), con pausar/reanudar/eliminar por trabajo desde
menú contextual, mismo patrón que HiddenDevicesDialog.

Se instancia bajo demanda desde ImpresorasMixin._abrir_cola_impresion_seleccionada
en printers.py, menú contextual de la tabla de Impresoras."""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QMessageBox, QAbstractItemView, QMenu, QApplication,
)


class PrintQueueDialog(QDialog):
    HEADERS = ["ID", "Documento", "Usuario", "Estado", "Páginas", "Tamaño", "Enviado"]

    def __init__(self, parent, printer_manager, nombre_impresora: str):
        super().__init__(parent)
        self.printer_manager = printer_manager
        self.nombre_impresora = nombre_impresora
        self.setWindowTitle(f"Cola de impresión — {nombre_impresora}")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(760, 460)
        self._trabajos = []

        c = (parent._dash_colors() if parent and hasattr(parent, "_dash_colors") else {
            "title": "#1a2634", "muted": "#8a94a6",
        })

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(22, 20, 22, 18)

        cabecera = QHBoxLayout()
        cabecera.setSpacing(12)
        lbl_icono = QLabel("📋")
        lbl_icono.setStyleSheet("font-size: 26pt; background: transparent; border: none;")
        cabecera.addWidget(lbl_icono)
        col_titulo = QVBoxLayout()
        col_titulo.setSpacing(2)
        lbl_titulo = QLabel(f"Cola de impresión — {nombre_impresora}")
        lbl_titulo.setWordWrap(True)
        lbl_titulo.setStyleSheet(
            f"font-size: 13pt; font-weight: 600; color: {c['title']}; background: transparent; border: none;")
        col_titulo.addWidget(lbl_titulo)
        lbl_subtitulo = QLabel("Clic derecho sobre un trabajo para pausarlo, reanudarlo o eliminarlo")
        lbl_subtitulo.setWordWrap(True)
        lbl_subtitulo.setStyleSheet(f"color: {c['muted']}; font-size: 9.5pt; background: transparent; border: none;")
        col_titulo.addWidget(lbl_subtitulo)
        cabecera.addLayout(col_titulo, 1)
        layout.addLayout(cabecera)

        self.lbl_estado = QLabel("🔄 Cargando...")
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
        self.tabla.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabla.customContextMenuRequested.connect(self._ctx_menu)
        layout.addWidget(self.tabla, 1)

        botones = QHBoxLayout()
        self.btn_actualizar = QPushButton("🔄 Actualizar")
        self.btn_actualizar.clicked.connect(self._cargar)
        botones.addWidget(self.btn_actualizar)
        botones.addStretch()
        self.btn_cerrar = QPushButton("Cerrar")
        self.btn_cerrar.clicked.connect(self.accept)
        botones.addWidget(self.btn_cerrar)
        layout.addLayout(botones)

        self._cargar()

    def _cargar(self):
        self.lbl_estado.setText("🔄 Cargando...")
        self.btn_actualizar.setEnabled(False)
        QApplication.processEvents()

        ok, datos = self.printer_manager.get_print_jobs(self.nombre_impresora)
        self.btn_actualizar.setEnabled(True)

        if not ok:
            self.lbl_estado.setText(f"❌ {datos}")
            return

        self._trabajos = datos
        self.tabla.setSortingEnabled(False)
        self.tabla.setRowCount(len(self._trabajos))
        for i, t in enumerate(self._trabajos):
            valores = [
                str(t.get("Id", "")), t.get("DocumentName", "") or "", t.get("Owner", "") or "",
                t.get("JobStatus", "") or "", str(t.get("TotalPages", "") or ""),
                str(t.get("Size", "") or ""), str(t.get("Enviado", "") or ""),
            ]
            for col, val in enumerate(valores):
                self.tabla.setItem(i, col, QTableWidgetItem(val))
        self.tabla.setSortingEnabled(True)

        if self._trabajos:
            self.lbl_estado.setText(f"✅ {len(self._trabajos)} trabajo(s) en cola")
        else:
            self.lbl_estado.setText("✅ No hay trabajos en la cola")

    def _trabajo_seleccionado(self):
        sel = self.tabla.selectionModel()
        if not sel or not sel.hasSelection():
            return None
        fila = sel.selectedRows()[0].row()
        if 0 <= fila < len(self._trabajos):
            return self._trabajos[fila]
        return None

    def _ctx_menu(self, pos):
        idx = self.tabla.indexAt(pos)
        if not idx.isValid():
            return
        self.tabla.selectRow(idx.row())
        trabajo = self._trabajo_seleccionado()
        if not trabajo or trabajo.get("Id") is None:
            return

        menu = QMenu(self)
        act_pausar = menu.addAction("⏸️ Pausar trabajo")
        act_reanudar = menu.addAction("▶️ Reanudar trabajo")
        menu.addSeparator()
        act_eliminar = menu.addAction("✕ Eliminar trabajo")
        action = menu.exec_(self.tabla.viewport().mapToGlobal(pos))

        job_id = trabajo["Id"]
        if action == act_pausar:
            self._ejecutar(self.printer_manager.pause_job, job_id, "Pausar trabajo de impresión")
        elif action == act_reanudar:
            self._ejecutar(self.printer_manager.resume_job, job_id, "Reanudar trabajo de impresión")
        elif action == act_eliminar:
            self._eliminar(job_id)

    def _ejecutar(self, metodo, job_id, etiqueta):
        ok, msg = metodo(self.nombre_impresora, job_id)
        parent = self.parent()
        if parent and hasattr(parent, "repair_audit_db"):
            parent.repair_audit_db.registrar(
                seccion="🖨️ Impresoras", accion=etiqueta,
                objetivo=f"{self.nombre_impresora} (trabajo #{job_id})", exito=ok, detalle=msg or "OK",
            )
        if ok:
            self._cargar()
        else:
            QMessageBox.warning(self, "Con avisos", f"No se pudo completar la acción:\n\n{msg}")

    def _eliminar(self, job_id):
        parent = self.parent()
        if parent and hasattr(parent, "_confirmar_accion"):
            if not parent._confirmar_accion(
                "Eliminar trabajo",
                f"¿Eliminar el trabajo #{job_id} de la cola de «{self.nombre_impresora}»?",
                icono=QMessageBox.Warning,
            ):
                return
        self._ejecutar(self.printer_manager.remove_job, job_id, "Eliminar trabajo de impresión")
