# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña Gráficas y Monitores: tabla, refresco, exportar.
Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).

Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

import subprocess

from PyQt5.QtCore import Qt, QRegExp
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QMessageBox, QTableView, QSplitter,
)

from msys.models.table_models import MonitorTableModel, GPUTableModel, _FechaAwareSortProxy
from msys.widgets.delegates import _setup_header
from msys.collectors import MonitorCollector


class MonitoresMixin:
    def crear_tab_monitors(self):
        """Crea la pestaña de Gráficas y Monitores: dos tablas independientes
        en un splitter vertical (mismo patrón que Discos Duros), no
        sub-pestañas — así el buscador único filtra las dos a la vez y el
        botón Soporte / la barra de exportación adaptativa sí saben
        resolver la sección (las sub-pestañas nunca coincidían con
        self._current_section)."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        # Barra de búsqueda y acciones — una sola fila: hostname, búsqueda
        # (filtra las dos tablas a la vez), hueco elástico, Actualizar.
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)

        import socket
        hostname = socket.gethostname()
        lbl_hostname = QLabel(f"🖥️ {hostname}")
        lbl_hostname.setObjectName("hostnameLabel")
        actions_layout.addWidget(lbl_hostname)

        self.search_monitors = QLineEdit()
        self.search_monitors.setPlaceholderText("🔎 Buscar gráfica o monitor...")
        self.search_monitors.textChanged.connect(self._filter_monitors)
        actions_layout.addWidget(self.search_monitors, 3)

        actions_layout.addStretch()

        # Independiente de la fila seleccionada — mismo criterio que
        # "Abrir Administrador de dispositivos" / "Abrir Impresoras y
        # escáneres" en las otras pestañas de Hardware.
        self.btn_abrir_config_pantalla = QPushButton("🖥️ Abrir Configuración de pantalla")
        self.btn_abrir_config_pantalla.setObjectName("ghost")
        self.btn_abrir_config_pantalla.clicked.connect(lambda: subprocess.Popen(
            ["powershell", "-Command", "Start-Process ms-settings:display"],
            startupinfo=self._get_startupinfo()
        ))
        actions_layout.addWidget(self.btn_abrir_config_pantalla)

        self.btn_cambiar_resolucion = QPushButton("🖥️ Cambiar resolución")
        self.btn_cambiar_resolucion.setObjectName("ghost")
        self.btn_cambiar_resolucion.clicked.connect(self._abrir_cambiar_resolucion)
        actions_layout.addWidget(self.btn_cambiar_resolucion)

        self.btn_refresh_monitors = QPushButton("🔄 Actualizar")
        self.btn_refresh_monitors.setObjectName("primary")
        self.btn_refresh_monitors.clicked.connect(self.refresh_monitors)
        actions_layout.addWidget(self.btn_refresh_monitors)

        layout.addLayout(actions_layout)

        splitter = QSplitter(Qt.Vertical)

        # ── Sección "Gráficas" ───────────────────────────────────────────
        gpu_container = QWidget()
        gpu_layout = QVBoxLayout(gpu_container)
        gpu_layout.setContentsMargins(0, 0, 0, 0)
        gpu_layout.setSpacing(4)
        gpu_layout.addWidget(QLabel("🎮 Gráficas"))

        self.gpu_model = GPUTableModel([])
        self.gpu_proxy = _FechaAwareSortProxy(self)
        self.gpu_proxy.setSourceModel(self.gpu_model)
        self.gpu_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.gpu_proxy.setFilterKeyColumn(-1)

        self.gpu_table = QTableView()
        self.gpu_table.setModel(self.gpu_proxy)
        self.gpu_table.setAlternatingRowColors(True)
        self.gpu_table.setSelectionBehavior(QTableView.SelectRows)
        self.gpu_table.verticalHeader().setVisible(False)
        self.gpu_table.setSortingEnabled(True)
        _setup_header(self.gpu_table, len(GPUTableModel.HEADERS), stretch_col=0)
        gpu_layout.addWidget(self.gpu_table)

        splitter.addWidget(gpu_container)

        # ── Sección "Monitores" ──────────────────────────────────────────
        mon_container = QWidget()
        mon_layout = QVBoxLayout(mon_container)
        mon_layout.setContentsMargins(0, 0, 0, 0)
        mon_layout.setSpacing(4)
        mon_layout.addWidget(QLabel("🖥️ Monitores"))

        self.monitors_model = MonitorTableModel([])
        self.monitors_proxy = _FechaAwareSortProxy(self)
        self.monitors_proxy.setSourceModel(self.monitors_model)
        self.monitors_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.monitors_proxy.setFilterKeyColumn(-1)

        self.monitors_table = QTableView()
        self.monitors_table.setModel(self.monitors_proxy)
        self.monitors_table.setAlternatingRowColors(True)
        self.monitors_table.setSelectionBehavior(QTableView.SelectRows)
        self.monitors_table.verticalHeader().setVisible(False)
        self.monitors_table.setSortingEnabled(True)
        _setup_header(self.monitors_table, len(MonitorTableModel.HEADERS), stretch_col=0)
        mon_layout.addWidget(self.monitors_table)

        splitter.addWidget(mon_container)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

        return tab

    def _abrir_cambiar_resolucion(self):
        from msys.ui.display_resolution_dialog import DisplayResolutionDialog
        dlg = DisplayResolutionDialog(self)
        dlg.exec_()

    def _filter_monitors(self):
        """Un único cuadro de búsqueda filtra Gráficas y Monitores a la vez
        — mismo patrón que Discos Duros (_filter_disk_health)."""
        rx = QRegExp(self.search_monitors.text(), Qt.CaseInsensitive, QRegExp.FixedString)
        self.gpu_proxy.setFilterRegExp(rx)
        self.monitors_proxy.setFilterRegExp(rx)

    def refresh_monitors(self):
        """Actualiza información de gráficas y monitores."""
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.set_status("🔄 Escaneando gráficas y monitores...")
        self.btn_refresh_monitors.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.worker = MonitorCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_monitors_data)
        self.worker.finished_error.connect(self._on_monitors_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_monitors_data(self, data: dict):
        """Procesa datos de gráficas y monitores."""
        self.gpu_model.setRows(data.get("graficas", []))
        self.monitors_model.setRows(data.get("monitores", []))
        self.btn_refresh_monitors.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status(f"✅ {len(data.get('graficas', []))} gráfica(s), "
                         f"{len(data.get('monitores', []))} monitor(es) encontrados")

    def _on_monitors_error(self, msg: str):
        """Maneja errores de monitores."""
        self.btn_refresh_monitors.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al escanear monitores")
        self._avisar("Error", f"No se pudo obtener información de gráficas y monitores:\n\n{msg}", QMessageBox.Warning)
