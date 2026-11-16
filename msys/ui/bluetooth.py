# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña Bluetooth: tabla, refresco, exportar.
Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).

Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

import os
import csv
import time

from PyQt5.QtCore import Qt, QRegExp
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QMessageBox, QTableView, QMenu,
)

from msys.core.utils import ruta_guardado_predeterminada, dialogo_guardar_archivo, _SafeCsvWriter, is_admin
from msys.models.table_models import BluetoothTableModel, _FechaAwareSortProxy
from msys.widgets.delegates import _setup_header
from msys.collectors import BluetoothCollector


class BluetoothMixin:
    def crear_tab_bluetooth(self):
        """Crea la pestaña de dispositivos Bluetooth."""
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

        self.search_bluetooth = QLineEdit()
        self.search_bluetooth.setPlaceholderText("🔎 Buscar por nombre, fabricante, estado...")
        self.search_bluetooth.textChanged.connect(lambda txt: (
            self.bluetooth_proxy.setFilterRegExp(QRegExp(txt, Qt.CaseInsensitive, QRegExp.FixedString)),
            self._update_bluetooth_count()
        ))
        actions_layout.addWidget(self.search_bluetooth, 3)

        actions_layout.addStretch()

        self.btn_refresh_bluetooth = QPushButton("🔄 Actualizar")
        self.btn_refresh_bluetooth.setObjectName("primary")
        self.btn_refresh_bluetooth.clicked.connect(self.refresh_bluetooth)
        actions_layout.addWidget(self.btn_refresh_bluetooth)

        layout.addLayout(actions_layout)

        # Fila secundaria: solo el contador (detalle informativo, no acción).
        info_layout = QHBoxLayout()
        info_layout.setSpacing(8)
        self.lbl_bluetooth_count = QLabel("Dispositivos: 0")
        self.lbl_bluetooth_count.setObjectName("countLabel")
        info_layout.addWidget(self.lbl_bluetooth_count)
        info_layout.addStretch()
        layout.addLayout(info_layout)

        self.bluetooth_model = BluetoothTableModel([])
        self.bluetooth_proxy = _FechaAwareSortProxy(self)
        self.bluetooth_proxy.setSourceModel(self.bluetooth_model)
        self.bluetooth_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.bluetooth_proxy.setFilterKeyColumn(-1)

        self.bluetooth_table = QTableView()
        self.bluetooth_table.setModel(self.bluetooth_proxy)
        self.bluetooth_table.setAlternatingRowColors(True)
        self.bluetooth_table.setSelectionBehavior(QTableView.SelectRows)
        self.bluetooth_table.setSelectionMode(QTableView.SingleSelection)
        self.bluetooth_table.verticalHeader().setVisible(False)
        self.bluetooth_table.setSortingEnabled(True)
        self.bluetooth_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.bluetooth_table.customContextMenuRequested.connect(self._ctx_bluetooth)

        _setup_header(self.bluetooth_table, len(BluetoothTableModel.HEADERS), stretch_col=0)

        layout.addWidget(self.bluetooth_table)
        return tab

    def refresh_bluetooth(self):
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.set_status("🔄 Escaneando Bluetooth...")
        self.btn_refresh_bluetooth.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.worker = BluetoothCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_bluetooth_data)
        self.worker.finished_error.connect(self._on_bluetooth_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_bluetooth_data(self, rows):
        self.bluetooth_model.setRows(rows)
        self.btn_refresh_bluetooth.setEnabled(True)
        self.progress_bar.setVisible(False)
        self._update_bluetooth_count()
        self.set_status(f"✅ {len(rows)} dispositivos Bluetooth encontrados")

    def _on_bluetooth_error(self, msg):
        self.btn_refresh_bluetooth.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al escanear Bluetooth")
        self._avisar("Error", f"No se pudieron obtener dispositivos Bluetooth:\n\n{msg}", QMessageBox.Warning)

    def _update_bluetooth_count(self):
        self.lbl_bluetooth_count.setText(f"Dispositivos: {self.bluetooth_proxy.rowCount()}")

    def _bluetooth_seleccionado(self):
        sel = self.bluetooth_table.selectionModel()
        if not sel or not sel.hasSelection():
            return None
        idx = sel.selectedRows()[0]
        source_index = self.bluetooth_proxy.mapToSource(idx)
        rows = self.bluetooth_model.rows()
        if 0 <= source_index.row() < len(rows):
            return rows[source_index.row()]
        return None

    def _ejecutar_accion_bluetooth(self, accion: str, row, metodo) -> bool:
        """Único punto de verdad para ejecutar+auditar+refrescar una acción
        sobre un dispositivo Bluetooth — reutiliza self.driver_manager: es
        un dispositivo PnP como cualquiera de Adm. Dispositivos."""
        ok, msg = metodo(row.instance_id)
        self.repair_audit_db.registrar(
            seccion="🔵 Bluetooth", accion=accion,
            objetivo=f"{row.nombre} ({row.instance_id})", exito=ok, detalle=msg or "OK",
        )
        if ok:
            self.set_status(f"✅ {accion}: «{row.nombre}»")
            if hasattr(self, 'refresh_bluetooth'):
                self.refresh_bluetooth()
        else:
            self._avisar("Con avisos", f"No se pudo completar «{accion}»:\n\n{msg}", QMessageBox.Warning)
        return ok

    def _reiniciar_bluetooth_seleccionado(self):
        row = self._bluetooth_seleccionado()
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
            "Reiniciar dispositivo Bluetooth",
            f"¿Deshabilitar y volver a habilitar «{row.nombre}»?\n\n"
            "Puede provocar una breve desconexión mientras se reinicia (p. ej. si es el "
            "propio adaptador Bluetooth, perderás todas las conexiones activas un instante).",
        ):
            return
        ok_disable, msg_disable = self.driver_manager.disable_device(row.instance_id)
        ok_enable, msg_enable = self.driver_manager.enable_device(row.instance_id)
        ok = ok_disable and ok_enable
        detalle = "OK" if ok else f"deshabilitar: {msg_disable} | habilitar: {msg_enable}"
        self.repair_audit_db.registrar(
            seccion="🔵 Bluetooth", accion="Reiniciar dispositivo",
            objetivo=f"{row.nombre} ({row.instance_id})", exito=ok, detalle=detalle,
        )
        if ok:
            self.set_status(f"✅ Dispositivo «{row.nombre}» reiniciado")
            self.refresh_bluetooth()
        else:
            self._avisar("Con avisos", f"No se pudo reiniciar completamente el dispositivo:\n\n{detalle}", QMessageBox.Warning)

    def _desinstalar_bluetooth_seleccionado(self):
        row = self._bluetooth_seleccionado()
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
            "Desinstalar dispositivo Bluetooth",
            f"¿Desinstalar «{row.nombre}»?\n\n"
            "Si es el adaptador Bluetooth del equipo, se perderá toda conexión Bluetooth "
            "hasta que Windows lo vuelva a detectar (a veces requiere reiniciar el equipo).",
            icono=QMessageBox.Warning,
        ):
            return
        self._ejecutar_accion_bluetooth("Desinstalar dispositivo", row, self.driver_manager.remove_device)

    def _ctx_bluetooth(self, pos):
        idx = self.bluetooth_table.indexAt(pos)
        if not idx.isValid():
            return
        self.bluetooth_table.selectRow(idx.row())
        row = self._bluetooth_seleccionado()
        if not row:
            return

        menu = QMenu(self)
        act_reiniciar = menu.addAction("🔁 Reiniciar dispositivo (deshabilitar y habilitar)")
        act_reiniciar.setEnabled(bool(row.instance_id))
        act_desinstalar = menu.addAction("✕ Desinstalar dispositivo")
        act_desinstalar.setEnabled(bool(row.instance_id))
        menu.addSeparator()
        act_copy = menu.addAction("📋 Copiar fila")

        action = menu.exec_(self.bluetooth_table.viewport().mapToGlobal(pos))
        if action == act_reiniciar:
            self._reiniciar_bluetooth_seleccionado()
        elif action == act_desinstalar:
            self._desinstalar_bluetooth_seleccionado()
        elif action == act_copy:
            cols = [self.bluetooth_proxy.data(self.bluetooth_proxy.index(idx.row(), c), Qt.DisplayRole)
                    for c in range(len(BluetoothTableModel.HEADERS))]
            QApplication.clipboard().setText("\t".join(str(v or "") for v in cols))

    def copy_bluetooth_selection(self):
        selected = self.bluetooth_table.selectionModel().selectedRows()
        if not selected:
            self._avisar("Copiar", "No hay filas seleccionadas.")
            return
        headers = BluetoothTableModel.HEADERS
        lines = ["DISPOSITIVOS BLUETOOTH", "=" * 60, ""]
        for idx in selected:
            row = idx.row()
            vals = [str(self.bluetooth_proxy.data(self.bluetooth_proxy.index(row, c), Qt.DisplayRole) or "")
                    for c in range(len(headers))]
            lines.append("  ".join(f"{h}: {v}" for h, v in zip(headers, vals)))
        QApplication.clipboard().setText("\n".join(lines))
        self.set_status(f"✅ Copiados {len(selected)} dispositivos al portapapeles")

    def export_bluetooth_csv(self):
        if self.bluetooth_proxy.rowCount() == 0:
            self._avisar("Exportar CSV", "No hay datos para exportar.")
            return
        path = dialogo_guardar_archivo(self, "Guardar CSV",
            ruta_guardado_predeterminada(f"Bluetooth_{time.strftime('%Y%m%d_%H%M%S')}.csv"), "CSV (*.csv)")
        if not path:
            return
        try:
            headers = BluetoothTableModel.HEADERS
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = _SafeCsvWriter(csv.writer(f, delimiter=";"))
                writer.writerow(headers)
                for r in range(self.bluetooth_proxy.rowCount()):
                    writer.writerow([
                        str(self.bluetooth_proxy.data(self.bluetooth_proxy.index(r, c), Qt.DisplayRole) or "")
                        for c in range(len(headers))
                    ])
            self.set_status(f"✅ Exportado: {os.path.basename(path)}")
            self._avisar("Éxito", f"Exportado a:\n{path}")
        except Exception as e:
            self._avisar("Error", f"No se pudo exportar:\n\n{e}", QMessageBox.Critical)
