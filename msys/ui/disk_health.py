# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña Discos Duros / Salud de disco: tabla, filtro, refresco.
Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).

Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

import re

from PyQt5.QtCore import Qt, QRegExp
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QMessageBox, QSplitter, QTableView, QComboBox,
)

from msys.models.table_models import DiscoFisicoTableModel, PartitionTableModel, DiscoColumnFilterProxy
from msys.widgets.delegates import _setup_header
from msys.collectors import DiskHealthCollector


class SaludDiscoMixin:
    def crear_tab_salud_disco(self):
        """Crea la sección de Discos Duros: antes 3 sub-pestañas (Discos /
        Salud de Disco / Particiones) que obligaban a cambiar de vista para
        comparar datos; ahora Discos y Salud de Disco (mismo disco, 1:1) se
        fusionan en una sola tabla, y Particiones (varias por disco, otra
        granularidad) se muestra debajo en la misma ventana — todo visible
        a la vez, sin pestañas."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)

        import socket
        lbl_hostname = QLabel(f"🖥️ {socket.gethostname()}")
        lbl_hostname.setObjectName("hostnameLabel")
        actions_layout.addWidget(lbl_hostname)

        self.search_disk_health = QLineEdit()
        self.search_disk_health.setPlaceholderText("🔎 Buscar disco o partición...")
        self.search_disk_health.textChanged.connect(self._filter_disk_health)
        actions_layout.addWidget(self.search_disk_health, 3)

        actions_layout.addWidget(QLabel("Disco:"))
        self.combo_filtro_disco = QComboBox()
        self.combo_filtro_disco.addItem("Todos")
        self.combo_filtro_disco.currentTextChanged.connect(self._on_filtro_disco_changed)
        actions_layout.addWidget(self.combo_filtro_disco)

        actions_layout.addStretch()

        self.btn_refresh_disk_health = QPushButton("🔄 Actualizar")
        self.btn_refresh_disk_health.setObjectName("primary")
        self.btn_refresh_disk_health.setToolTip(
            "Inventario, salud S.M.A.R.T. y particiones de los discos instalados")
        self.btn_refresh_disk_health.clicked.connect(self.refresh_salud_disco)
        actions_layout.addWidget(self.btn_refresh_disk_health)

        layout.addLayout(actions_layout)

        splitter = QSplitter(Qt.Vertical)

        discos_container = QWidget()
        discos_layout = QVBoxLayout(discos_container)
        discos_layout.setContentsMargins(0, 0, 0, 0)
        discos_layout.setSpacing(4)
        discos_layout.addWidget(QLabel("💿 Discos"))
        self.disk_inventory_model = DiscoFisicoTableModel([])
        self.disk_inventory_proxy = DiscoColumnFilterProxy(self)
        self.disk_inventory_proxy.setSourceModel(self.disk_inventory_model)
        self.disk_inventory_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.disk_inventory_proxy.setFilterKeyColumn(-1)
        self.disk_inventory_table = QTableView()
        self.disk_inventory_table.setModel(self.disk_inventory_proxy)
        self.disk_inventory_table.setAlternatingRowColors(True)
        self.disk_inventory_table.setSelectionBehavior(QTableView.SelectRows)
        self.disk_inventory_table.verticalHeader().setVisible(False)
        self.disk_inventory_table.setSortingEnabled(True)
        _setup_header(self.disk_inventory_table, len(DiscoFisicoTableModel.HEADERS), stretch_col=1)
        discos_layout.addWidget(self.disk_inventory_table)
        splitter.addWidget(discos_container)

        particiones_container = QWidget()
        particiones_layout = QVBoxLayout(particiones_container)
        particiones_layout.setContentsMargins(0, 0, 0, 0)
        particiones_layout.setSpacing(4)
        particiones_layout.addWidget(QLabel("📁 Particiones"))
        self.partitions_model = PartitionTableModel([])
        self.partitions_proxy = DiscoColumnFilterProxy(self)
        self.partitions_proxy.setSourceModel(self.partitions_model)
        self.partitions_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.partitions_proxy.setFilterKeyColumn(-1)
        self.partitions_table = QTableView()
        self.partitions_table.setModel(self.partitions_proxy)
        self.partitions_table.setAlternatingRowColors(True)
        self.partitions_table.setSelectionBehavior(QTableView.SelectRows)
        self.partitions_table.verticalHeader().setVisible(False)
        self.partitions_table.setSortingEnabled(True)
        _setup_header(self.partitions_table, len(PartitionTableModel.HEADERS), stretch_col=2)
        particiones_layout.addWidget(self.partitions_table)
        splitter.addWidget(particiones_container)

        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

        return tab

    def _filter_disk_health(self):
        """Un único cuadro de búsqueda filtra las dos tablas a la vez
        (Discos y Particiones viven en la misma ventana ahora, no en
        sub-pestañas separadas con su propio buscador)."""
        rx = QRegExp(self.search_disk_health.text(), Qt.CaseInsensitive, QRegExp.FixedString)
        self.disk_inventory_proxy.setFilterRegExp(rx)
        self.partitions_proxy.setFilterRegExp(rx)

    def _on_filtro_disco_changed(self, texto: str):
        """El combo 'Disco' filtra las dos tablas a la vez, igual que el
        buscador de texto — 'Todos' equivale a no filtrar nada."""
        disco = "" if texto == "Todos" else texto
        self.disk_inventory_proxy.set_disco_filtro(disco)
        self.partitions_proxy.set_disco_filtro(disco)

    def _actualizar_combo_filtro_disco(self):
        """Repuebla el combo con los discos realmente presentes (Disco 0,
        Disco 1...) tras cada actualización — se conserva la selección
        actual si sigue existiendo, en vez de resetear el filtro del
        usuario en cada refresco."""
        discos = sorted(
            {row.disco.strip() for row in self.disk_inventory_model.rows() if row.disco.strip()},
            key=lambda d: [int(n) for n in re.findall(r'\d+', d)] or [0],
        )
        seleccion_previa = self.combo_filtro_disco.currentText()
        self.combo_filtro_disco.blockSignals(True)
        self.combo_filtro_disco.clear()
        self.combo_filtro_disco.addItem("Todos")
        self.combo_filtro_disco.addItems(discos)
        idx = self.combo_filtro_disco.findText(seleccion_previa)
        self.combo_filtro_disco.setCurrentIndex(idx if idx >= 0 else 0)
        self.combo_filtro_disco.blockSignals(False)

    def refresh_salud_disco(self):
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.set_status("🔄 Consultando discos y particiones...")
        self.btn_refresh_disk_health.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.worker = DiskHealthCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_disk_health_data)
        self.worker.finished_error.connect(self._on_disk_health_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_disk_health_data(self, data: dict):
        self.disk_inventory_model.setRows(data.get("discos", []))
        self.partitions_model.setRows(data.get("particiones", []))
        self._actualizar_combo_filtro_disco()
        self._on_filtro_disco_changed(self.combo_filtro_disco.currentText())
        self.btn_refresh_disk_health.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status(f"✅ {len(data.get('discos', []))} disco(s), "
                         f"{len(data.get('particiones', []))} partición(es) analizados")

    def _on_disk_health_error(self, msg):
        self.btn_refresh_disk_health.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al obtener salud de disco")
        self._avisar("Error", f"No se pudo obtener la salud de los discos:\n\n{msg}", QMessageBox.Warning)

