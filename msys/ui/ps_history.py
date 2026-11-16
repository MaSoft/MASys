# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña Historial PS: tabla, filtros, detección
de bypass/ofuscación, exportar.
Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).

Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

import os
import csv
import time
import subprocess

from PyQt5.QtCore import Qt, QRegExp
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QCheckBox, QComboBox, QMenu, QMessageBox, QTableView,
)

from msys.core.utils import ruta_guardado_predeterminada, dialogo_guardar_archivo, _SafeCsvWriter
from msys.models.table_models import PSHistoryTableModel, PSHistoryFilterProxy
from msys.widgets.delegates import _setup_header
from msys.collectors import PSHistoryCollector
from msys.ui.script_launcher_dialog import ScriptLauncherDialog


class HistorialPSMixin:
    def crear_tab_ps_history(self):
        """Crea la pestaña de historial de comandos PowerShell."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        # Barra de búsqueda y acciones — una sola fila.
        actions = QHBoxLayout()
        actions.setSpacing(8)

        self.search_ps_history = QLineEdit()
        self.search_ps_history.setPlaceholderText("🔎 Buscar comando...")
        self.search_ps_history.textChanged.connect(lambda txt: (
            self.ps_history_proxy.setFilterRegExp(QRegExp(txt, Qt.CaseInsensitive, QRegExp.FixedString)),
            self._update_ps_history_count()
        ))
        actions.addWidget(self.search_ps_history, 3)

        actions.addStretch()

        self.btn_ps_history_admin = QPushButton("🔵 Abrir PowerShell (Admin)")
        self.btn_ps_history_admin.setObjectName("ghost")
        self.btn_ps_history_admin.setToolTip("Abre una consola de PowerShell elevada (UAC).")
        self.btn_ps_history_admin.clicked.connect(self._abrir_powershell_admin)
        actions.addWidget(self.btn_ps_history_admin)

        self.btn_ps_history_scripts = QPushButton("🚀 Lanzar Scripts")
        self.btn_ps_history_scripts.setObjectName("ghost")
        self.btn_ps_history_scripts.setToolTip(
            "Gestiona y ejecuta scripts .ps1 propios (carpeta SCRIPTS del programa).")
        self.btn_ps_history_scripts.clicked.connect(self._abrir_lanzador_scripts)
        actions.addWidget(self.btn_ps_history_scripts)

        self.btn_refresh_ps_history = QPushButton("🔄 Actualizar")
        self.btn_refresh_ps_history.setObjectName("primary")
        self.btn_refresh_ps_history.clicked.connect(self.refresh_ps_history)
        actions.addWidget(self.btn_refresh_ps_history)

        layout.addLayout(actions)

        # Segunda fila: filtros (checkboxes + combo, todos aplican a la vez
        # salvo las 4 categorías de tipo, que van en OR entre sí — ver
        # PSHistoryFilterProxy) + contador a la derecha.
        filtros = QHBoxLayout()
        filtros.setSpacing(8)

        self.chk_ps_history_sospechosos = QCheckBox("Solo sospechosos")
        self.chk_ps_history_sospechosos.setToolTip(
            "Comandos con parámetros típicos de bypass/ofuscación: -EncodedCommand, "
            "-ExecutionPolicy Bypass, Invoke-Expression + descarga, certutil -decode...")
        self.chk_ps_history_sospechosos.stateChanged.connect(self._filtrar_ps_history)
        filtros.addWidget(self.chk_ps_history_sospechosos)

        self.chk_ps_history_script_local = QCheckBox("📄 Scripts locales")
        self.chk_ps_history_script_local.setToolTip("Invocaciones tipo .\\script.ps1")
        self.chk_ps_history_script_local.stateChanged.connect(self._filtrar_ps_history)
        filtros.addWidget(self.chk_ps_history_script_local)

        self.chk_ps_history_paquetes = QCheckBox("📦 Gestores de paquetes")
        self.chk_ps_history_paquetes.setToolTip("pip, npm, choco, winget, Install-Module...")
        self.chk_ps_history_paquetes.stateChanged.connect(self._filtrar_ps_history)
        filtros.addWidget(self.chk_ps_history_paquetes)

        self.chk_ps_history_red = QCheckBox("🌐 Descargas/red")
        self.chk_ps_history_red.setToolTip("Invoke-WebRequest, Invoke-RestMethod, curl, wget, Start-BitsTransfer")
        self.chk_ps_history_red.stateChanged.connect(self._filtrar_ps_history)
        filtros.addWidget(self.chk_ps_history_red)

        self.chk_ps_history_cmdlet = QCheckBox("🔧 Cmdlets nativos")
        self.chk_ps_history_cmdlet.setToolTip("Get-/Set-/New-/Remove-/Enable-/Disable-/Start-/Stop-/Restart-")
        self.chk_ps_history_cmdlet.stateChanged.connect(self._filtrar_ps_history)
        filtros.addWidget(self.chk_ps_history_cmdlet)

        self.combo_ps_history_origen = QComboBox()
        self.combo_ps_history_origen.addItems([
            "Todos los orígenes", "Registro de eventos (bloque de script)", "Historial local (PSReadLine)",
        ])
        self.combo_ps_history_origen.currentIndexChanged.connect(self._filtrar_ps_history)
        filtros.addWidget(self.combo_ps_history_origen)

        filtros.addStretch()

        self.lbl_ps_history_count = QLabel("Comandos: 0")
        self.lbl_ps_history_count.setObjectName("countLabel")
        filtros.addWidget(self.lbl_ps_history_count)

        layout.addLayout(filtros)

        self.ps_history_model = PSHistoryTableModel([])
        self.ps_history_proxy = PSHistoryFilterProxy(self)
        self.ps_history_proxy.setSourceModel(self.ps_history_model)
        self.ps_history_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.ps_history_proxy.setFilterKeyColumn(-1)

        self.ps_history_table = QTableView()
        self.ps_history_table.setModel(self.ps_history_proxy)
        self.ps_history_table.setAlternatingRowColors(True)
        self.ps_history_table.setSelectionBehavior(QTableView.SelectRows)
        self.ps_history_table.setSelectionMode(QTableView.SingleSelection)
        self.ps_history_table.verticalHeader().setVisible(False)
        self.ps_history_table.setSortingEnabled(True)
        self.ps_history_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.ps_history_table.customContextMenuRequested.connect(self._ctx_ps_history)

        _setup_header(self.ps_history_table, len(PSHistoryTableModel.HEADERS), stretch_col=3)

        layout.addWidget(self.ps_history_table)
        return tab

    def _filtrar_ps_history(self):
        origen_idx = self.combo_ps_history_origen.currentIndex()
        origen = "Todos" if origen_idx == 0 else self.combo_ps_history_origen.currentText()
        self.ps_history_proxy.setFiltros(
            self.chk_ps_history_sospechosos.isChecked(), origen,
            self.chk_ps_history_script_local.isChecked(),
            self.chk_ps_history_paquetes.isChecked(),
            self.chk_ps_history_red.isChecked(),
            self.chk_ps_history_cmdlet.isChecked(),
        )
        self._update_ps_history_count()

    def _abrir_powershell_admin(self):
        """Abre una consola de PowerShell elevada (UAC) — mismo mecanismo
        que 'Abrir Programador de tareas' en Tareas Programadas."""
        try:
            subprocess.Popen(
                ["powershell", "-Command", "Start-Process powershell -Verb RunAs"],
                startupinfo=self._get_startupinfo()
            )
            self.set_status("🔵 Abriendo PowerShell como administrador...")
        except Exception as e:
            self._avisar("Error", f"No se pudo abrir PowerShell como administrador:\n\n{e}", QMessageBox.Warning)

    def _abrir_lanzador_scripts(self):
        dialog = ScriptLauncherDialog(self)
        dialog.exec_()

    def refresh_ps_history(self):
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.set_status("🔄 Leyendo historial de PowerShell...")
        self.btn_refresh_ps_history.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.worker = PSHistoryCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_ps_history_data)
        self.worker.finished_error.connect(self._on_ps_history_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_ps_history_data(self, rows):
        self.ps_history_model.setRows(rows)
        self.btn_refresh_ps_history.setEnabled(True)
        self.progress_bar.setVisible(False)
        self._update_ps_history_count()
        sospechosos = sum(1 for r in rows if r.es_sospechoso)
        if sospechosos:
            self.set_status(f"✅ {len(rows)} comandos en historial de PowerShell (⚠️ {sospechosos} con indicios sospechosos)")
        else:
            self.set_status(f"✅ {len(rows)} comandos en historial de PowerShell")

    def _on_ps_history_error(self, msg):
        self.btn_refresh_ps_history.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al leer historial de PowerShell")
        self._avisar("Error", f"No se pudo leer el historial de PowerShell:\n\n{msg}", QMessageBox.Warning)

    def _update_ps_history_count(self):
        self.lbl_ps_history_count.setText(f"Comandos: {self.ps_history_proxy.rowCount()}/{self.ps_history_model.rowCount()}")

    def _ps_history_seleccionado(self):
        sel = self.ps_history_table.selectionModel()
        if not sel or not sel.hasSelection():
            return None
        idx = sel.selectedRows()[0]
        source_index = self.ps_history_proxy.mapToSource(idx)
        rows = self.ps_history_model.rows()
        if 0 <= source_index.row() < len(rows):
            return rows[source_index.row()]
        return None

    def _ver_comando_completo_seleccionado(self):
        row = self._ps_history_seleccionado()
        if not row:
            return
        titulo = f"Comando #{row.numero} — {row.fecha_hora}"
        if row.motivo_sospecha:
            texto = row.comando + "\n\n⚠️ Motivo: " + "; ".join(row.motivo_sospecha)
        else:
            texto = row.comando
        self._avisar(titulo, texto)

    def _ctx_ps_history(self, pos):
        idx = self.ps_history_table.indexAt(pos)
        if not idx.isValid():
            return
        self.ps_history_table.selectRow(idx.row())
        row = self._ps_history_seleccionado()
        if not row:
            return

        menu = QMenu(self)
        act_ver = menu.addAction("📄 Ver comando completo")
        menu.addSeparator()
        act_copy_cmd = menu.addAction("📋 Copiar comando")
        act_copy_row = menu.addAction("📋 Copiar fila")

        action = menu.exec_(self.ps_history_table.viewport().mapToGlobal(pos))
        if action == act_ver:
            self._ver_comando_completo_seleccionado()
        elif action == act_copy_cmd:
            QApplication.clipboard().setText(row.comando)
        elif action == act_copy_row:
            cols = [self.ps_history_proxy.data(self.ps_history_proxy.index(idx.row(), c), Qt.DisplayRole)
                    for c in range(len(PSHistoryTableModel.HEADERS))]
            QApplication.clipboard().setText("\t".join(str(v or "") for v in cols))

    def copy_ps_history_selection(self):
        selected = self.ps_history_table.selectionModel().selectedRows()
        if not selected:
            self._avisar("Copiar", "No hay filas seleccionadas.")
            return
        lines = []
        for idx in selected:
            row = idx.row()
            cmd = str(self.ps_history_proxy.data(self.ps_history_proxy.index(row, 3), Qt.DisplayRole) or "")
            lines.append(cmd)
        QApplication.clipboard().setText("\n".join(lines))
        self.set_status(f"✅ Copiados {len(selected)} comandos al portapapeles")

    def export_ps_history_csv(self):
        if self.ps_history_proxy.rowCount() == 0:
            self._avisar("Exportar CSV", "No hay datos para exportar.")
            return
        path = dialogo_guardar_archivo(self, "Guardar CSV",
            ruta_guardado_predeterminada(f"HistorialPS_{time.strftime('%Y%m%d_%H%M%S')}.csv"), "CSV (*.csv)")
        if not path:
            return
        try:
            headers = PSHistoryTableModel.HEADERS
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = _SafeCsvWriter(csv.writer(f, delimiter=";"))
                writer.writerow(headers)
                for r in range(self.ps_history_proxy.rowCount()):
                    writer.writerow([
                        str(self.ps_history_proxy.data(self.ps_history_proxy.index(r, c), Qt.DisplayRole) or "")
                        for c in range(len(headers))
                    ])
            self.set_status(f"✅ Exportado: {os.path.basename(path)}")
            self._avisar("Éxito", f"Exportado a:\n{path}")
        except Exception as e:
            self._avisar("Error", f"No se pudo exportar:\n\n{e}", QMessageBox.Critical)
