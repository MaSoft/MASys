# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña Dispositivos USB: tabla, refresco, exportar.
Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).

Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

import csv
import time
from typing import List

from PyQt5.QtCore import Qt, QRegExp
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QMessageBox, QTableView, QMenu,
)

from msys.core.utils import ruta_guardado_predeterminada, dialogo_guardar_archivo, _SafeCsvWriter, is_admin
from msys.models.rows import USBDeviceRow
from msys.models.table_models import USBDeviceTableModel, _FechaAwareSortProxy
from msys.widgets.delegates import _setup_header
from msys.collectors import USBDeviceCollector


class USBMixin:
    def crear_tab_usb_devices(self):
        """Crea la pestaña de Dispositivos USB."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        # Barra de búsqueda y acciones — una sola fila: hostname, búsqueda,
        # hueco elástico, Actualizar.
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)

        import socket
        hostname = socket.gethostname()
        lbl_hostname = QLabel(f"🖥️ {hostname}")
        lbl_hostname.setObjectName("hostnameLabel")
        actions_layout.addWidget(lbl_hostname)

        self.search_usb = QLineEdit()
        self.search_usb.setPlaceholderText("🔎 Buscar dispositivos...")
        self.search_usb.textChanged.connect(lambda: self.usb_proxy.setFilterRegExp(QRegExp(self.search_usb.text(), Qt.CaseInsensitive, QRegExp.FixedString)))
        actions_layout.addWidget(self.search_usb, 3)

        actions_layout.addStretch()

        self.btn_refresh_usb = QPushButton("🔄 Actualizar")
        self.btn_refresh_usb.setObjectName("primary")
        self.btn_refresh_usb.clicked.connect(self.refresh_usb_devices)
        actions_layout.addWidget(self.btn_refresh_usb)

        layout.addLayout(actions_layout)

        # Tabla
        self.usb_model = USBDeviceTableModel([])
        self.usb_proxy = _FechaAwareSortProxy(self)
        self.usb_proxy.setSourceModel(self.usb_model)
        self.usb_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.usb_proxy.setFilterKeyColumn(-1)

        self.usb_table = QTableView()
        self.usb_table.setModel(self.usb_proxy)
        self.usb_table.setAlternatingRowColors(True)
        self.usb_table.setSelectionBehavior(QTableView.SelectRows)
        self.usb_table.setSelectionMode(QTableView.SingleSelection)
        self.usb_table.verticalHeader().setVisible(False)
        self.usb_table.setSortingEnabled(True)
        self.usb_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.usb_table.customContextMenuRequested.connect(self._ctx_usb)

        _setup_header(self.usb_table, len(USBDeviceTableModel.HEADERS), stretch_col=0)

        layout.addWidget(self.usb_table)

        return tab

    def refresh_usb_devices(self):
        """Actualiza información de dispositivos USB."""
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.set_status("🔄 Escaneando dispositivos USB...")
        self.btn_refresh_usb.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.worker = USBDeviceCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_usb_data)
        self.worker.finished_error.connect(self._on_usb_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_usb_data(self, rows: List[USBDeviceRow]):
        """Procesa datos de dispositivos USB."""
        self.usb_model.setRows(rows)
        self.btn_refresh_usb.setEnabled(True)
        self.progress_bar.setVisible(False)
        total = len(rows)
        self.set_status(f"✅ {total} dispositivos USB encontrados")

    def _on_usb_error(self, msg: str):
        """Maneja errores de dispositivos USB."""
        self.btn_refresh_usb.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al escanear dispositivos USB")
        self._avisar("Error", f"No se pudo obtener información de USB:\n\n{msg}", QMessageBox.Warning)

    def _usb_seleccionado(self):
        sel = self.usb_table.selectionModel()
        if not sel or not sel.hasSelection():
            return None
        idx = sel.selectedRows()[0]
        source_index = self.usb_proxy.mapToSource(idx)
        rows = self.usb_model.rows()
        if 0 <= source_index.row() < len(rows):
            return rows[source_index.row()]
        return None

    def _ejecutar_accion_usb(self, accion: str, row, metodo) -> bool:
        """Único punto de verdad para ejecutar+auditar+refrescar una acción
        sobre un dispositivo USB — reutiliza self.driver_manager: un USB es
        un dispositivo PnP como cualquiera de Adm. Dispositivos, mismo
        pnputil por debajo, solo cambia de dónde se llama."""
        ok, msg = metodo(row.instance_id)
        self.repair_audit_db.registrar(
            seccion="🔌 Dispositivos USB", accion=accion,
            objetivo=f"{row.nombre} ({row.instance_id})", exito=ok, detalle=msg or "OK",
        )
        if ok:
            self.set_status(f"✅ {accion}: «{row.nombre}»")
            if hasattr(self, 'refresh_usb_devices'):
                self.refresh_usb_devices()
        else:
            self._avisar("Con avisos", f"No se pudo completar «{accion}»:\n\n{msg}", QMessageBox.Warning)
        return ok

    def _reiniciar_usb_seleccionado(self):
        row = self._usb_seleccionado()
        if not row or not row.instance_id:
            return
        if not is_admin():
            self._avisar(
                "Se requieren privilegios de administrador",
                "Reiniciar un dispositivo requiere ejecutar MASys como administrador.",
                QMessageBox.Warning,
            )
            return
        if not self._confirmar_accion(
            "Reiniciar dispositivo USB",
            f"¿Deshabilitar y volver a habilitar «{row.nombre}»?\n\n"
            "Puede provocar una breve desconexión del dispositivo mientras se reinicia.",
        ):
            return
        ok_disable, msg_disable = self.driver_manager.disable_device(row.instance_id)
        ok_enable, msg_enable = self.driver_manager.enable_device(row.instance_id)
        ok = ok_disable and ok_enable
        detalle = "OK" if ok else f"deshabilitar: {msg_disable} | habilitar: {msg_enable}"
        self.repair_audit_db.registrar(
            seccion="🔌 Dispositivos USB", accion="Reiniciar dispositivo",
            objetivo=f"{row.nombre} ({row.instance_id})", exito=ok, detalle=detalle,
        )
        if ok:
            self.set_status(f"✅ Dispositivo «{row.nombre}» reiniciado")
            self.refresh_usb_devices()
        else:
            self._avisar("Con avisos", f"No se pudo reiniciar completamente el dispositivo:\n\n{detalle}", QMessageBox.Warning)

    def _desinstalar_usb_seleccionado(self):
        """Desinstala el dispositivo USB seleccionado. A diferencia de Adm.
        Dispositivos, aquí no hay bloqueo por clase de riesgo (USBDeviceRow
        no distingue clase de hardware) — el USB es, por naturaleza, un
        bus de periféricos, no de disco/sistema/red."""
        row = self._usb_seleccionado()
        if not row or not row.instance_id:
            return
        if not is_admin():
            self._avisar(
                "Se requieren privilegios de administrador",
                "Desinstalar un dispositivo requiere ejecutar MASys como administrador.",
                QMessageBox.Warning,
            )
            return
        if not self._confirmar_accion(
            "Desinstalar dispositivo USB",
            f"¿Desinstalar «{row.nombre}»?\n\n"
            "El dispositivo dejará de funcionar hasta que se vuelva a conectar o Windows lo "
            "vuelva a detectar.",
            icono=QMessageBox.Warning,
        ):
            return
        self._ejecutar_accion_usb("Desinstalar dispositivo", row, self.driver_manager.remove_device)

    def _ctx_usb(self, pos):
        idx = self.usb_table.indexAt(pos)
        if not idx.isValid():
            return
        self.usb_table.selectRow(idx.row())
        row = self._usb_seleccionado()
        if not row:
            return

        menu = QMenu(self)
        act_reiniciar = menu.addAction("🔁 Reiniciar dispositivo (deshabilitar y habilitar)")
        act_reiniciar.setEnabled(bool(row.instance_id))
        act_desinstalar = menu.addAction("✕ Desinstalar dispositivo")
        act_desinstalar.setEnabled(bool(row.instance_id))
        menu.addSeparator()
        act_copy = menu.addAction("📋 Copiar fila")

        action = menu.exec_(self.usb_table.viewport().mapToGlobal(pos))
        if action == act_reiniciar:
            self._reiniciar_usb_seleccionado()
        elif action == act_desinstalar:
            self._desinstalar_usb_seleccionado()
        elif action == act_copy:
            cols = [self.usb_proxy.data(self.usb_proxy.index(idx.row(), c), Qt.DisplayRole)
                    for c in range(len(USBDeviceTableModel.HEADERS))]
            QApplication.clipboard().setText("\t".join(str(v or "") for v in cols))

    def copy_usb_selection(self):
        """Copia dispositivos USB seleccionados al portapapeles."""
        selected_indexes = self.usb_table.selectionModel().selectedRows()
        if not selected_indexes:
            self._avisar("Copiar", "No hay dispositivos seleccionados.")
            return

        lines = []
        lines.append("=" * 80)
        lines.append("DISPOSITIVOS USB")
        lines.append("=" * 80)
        lines.append("")

        for index in selected_indexes:
            row = index.row()
            nombre = str(self.usb_proxy.data(self.usb_proxy.index(row, 0), Qt.DisplayRole) or "")
            fabricante = str(self.usb_proxy.data(self.usb_proxy.index(row, 1), Qt.DisplayRole) or "")
            estado = str(self.usb_proxy.data(self.usb_proxy.index(row, 2), Qt.DisplayRole) or "")

            lines.append(f"{nombre}")
            lines.append(f"  Fabricante: {fabricante}")
            lines.append(f"  Estado: {estado}")
            lines.append("")

        lines.append("=" * 80)
        QApplication.clipboard().setText("\n".join(lines))
        self.set_status(f"📋 {len(selected_indexes)} dispositivos copiados al portapapeles")

    def export_usb_csv(self):
        """Exporta dispositivos USB a CSV."""
        if self.usb_proxy.rowCount() == 0:
            self._avisar("Exportar CSV", "No hay dispositivos para exportar.")
            return

        default_name = f"DispositivosUSB_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        path = dialogo_guardar_archivo(self, "Guardar CSV", ruta_guardado_predeterminada(default_name), "CSV (*.csv)")
        if not path:
            return

        try:
            headers = USBDeviceTableModel.HEADERS
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = _SafeCsvWriter(csv.writer(f, delimiter=";"))
                writer.writerow(headers)
                for r in range(self.usb_proxy.rowCount()):
                    row_data = []
                    for c in range(len(headers)):
                        idx = self.usb_proxy.index(r, c)
                        val = self.usb_proxy.data(idx, Qt.DisplayRole)
                        row_data.append(str(val) if val else "")
                    writer.writerow(row_data)

            self.set_status(f"✅ Dispositivos USB exportados a {path}")
            self._avisar("Exportar CSV", f"Dispositivos USB exportados correctamente a:\n{path}")
        except Exception as e:
            self._avisar("Error", f"Error al exportar CSV:\n{e}", QMessageBox.Critical)
