# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña 🗄️ SQL Server — detección de instancias
(motor de base de datos SQL Server/Express + LocalDB) y control de sus
servicios (Iniciar/Detener/Reiniciar motor y agente).

Detección 100% por registro de Windows (ver detectar_sql_server en
msys/collectors.py) — deliberadamente SIN pyodbc ni sqlcmd: esta pestaña
es la fase de "qué hay instalado y en qué estado", no un cliente de
consultas. El control de servicio reutiliza ServiceManager (el mismo
motor que la pestaña Servicios): SQL Server no necesita un mecanismo de
control distinto, sus servicios son servicios de Windows normales. Las
copias de seguridad de bases de datos (fase 3) viven en
msys/backup_generators.py, como una categoría más del sistema de Copias
de Seguridad existente.

Como todos los mixins de msys/ui/, sus métodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demás) — no son autónomos ni instanciables por sí solos."""

import socket

from PyQt5.QtCore import Qt, QSortFilterProxyModel
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QMessageBox, QTableView, QMenu,
)

from msys.collectors import SqlServerCollector
from msys.models.table_models import SqlInstanceTableModel
from msys.widgets.delegates import _setup_header


# 5.4.0: antes «{verbo}ando» daba «Finalizarando», «Detenerando», «Activarando»...
_GERUNDIO = {"Finalizar": "Finalizando", "Suspender": "Suspendiendo", "Reanudar": "Reanudando", "Iniciar": "Iniciando", "Detener": "Deteniendo", "Reiniciar": "Reiniciando", "Activar": "Activando", "Desactivar": "Desactivando"}

class SqlServerMixin:
    def crear_tab_sql_server(self):
        """Crea la pestaña de SQL Server."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)

        hostname = socket.gethostname()
        lbl_hostname = QLabel(f"🖥️ {hostname}")
        lbl_hostname.setObjectName("hostnameLabel")
        actions_layout.addWidget(lbl_hostname)

        actions_layout.addStretch()

        self.lbl_sql_count = QLabel("0 instancias")
        self.lbl_sql_count.setObjectName("countLabel")
        actions_layout.addWidget(self.lbl_sql_count)

        self.btn_refresh_sql = QPushButton("🔄 Actualizar")
        self.btn_refresh_sql.setObjectName("primary")
        self.btn_refresh_sql.clicked.connect(self.refresh_sql_server)
        actions_layout.addWidget(self.btn_refresh_sql)

        layout.addLayout(actions_layout)

        lbl_alcance = QLabel(
            "ℹ️ Detección por registro de Windows (motor SQL Server/Express + LocalDB), sin "
            "necesidad de sqlcmd ni conexión a la base de datos. Si la tabla aparece vacía tras "
            "actualizar, no hay SQL Server instalado en este equipo.")
        lbl_alcance.setObjectName("hintLabel")
        lbl_alcance.setWordWrap(True)
        layout.addWidget(lbl_alcance)

        self.sql_model = SqlInstanceTableModel([])
        self.sql_proxy = QSortFilterProxyModel(self)
        self.sql_proxy.setSourceModel(self.sql_model)

        self.sql_table = QTableView()
        self.sql_table.setModel(self.sql_proxy)
        self.sql_table.setAlternatingRowColors(True)
        self.sql_table.setSelectionBehavior(QTableView.SelectRows)
        self.sql_table.setSelectionMode(QTableView.SingleSelection)
        self.sql_table.verticalHeader().setVisible(False)
        self.sql_table.setSortingEnabled(True)
        self.sql_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.sql_table.customContextMenuRequested.connect(self._ctx_sql_server)

        _setup_header(self.sql_table, len(SqlInstanceTableModel.HEADERS), stretch_col=0)

        layout.addWidget(self.sql_table)

        return tab

    def refresh_sql_server(self):
        """Busca instancias de SQL Server/LocalDB instaladas."""
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.set_status("🔄 Buscando instancias de SQL Server...")
        self.btn_refresh_sql.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.worker = SqlServerCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_sql_data)
        self.worker.finished_error.connect(self._on_sql_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_sql_data(self, rows):
        self.sql_model.setRows(rows)
        self.btn_refresh_sql.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.lbl_sql_count.setText(f"{len(rows)} instancia(s)")
        if rows:
            self.set_status(f"✅ {len(rows)} instancia(s) de SQL Server detectada(s)")
        else:
            self.set_status("✅ No se detectó SQL Server instalado en este equipo")

    def _on_sql_error(self, msg):
        self.btn_refresh_sql.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al buscar SQL Server")
        self._avisar("Error", f"No se pudo completar la detección de SQL Server:\n\n{msg}", QMessageBox.Warning)

    def _sql_seleccionada(self):
        sel = self.sql_table.selectionModel()
        if not sel or not sel.hasSelection():
            return None
        idx = sel.selectedRows()[0]
        source_index = self.sql_proxy.mapToSource(idx)
        rows = self.sql_model.rows()
        if 0 <= source_index.row() < len(rows):
            return rows[source_index.row()]
        return None

    def _ctx_sql_server(self, pos):
        idx = self.sql_table.indexAt(pos)
        if not idx.isValid():
            return
        self.sql_table.selectRow(idx.row())
        row = self._sql_seleccionada()
        if not row:
            return

        menu = QMenu(self)
        act_motor_start = act_motor_stop = act_motor_restart = None
        act_agente_start = act_agente_stop = act_agente_restart = None

        if row.service_name:
            act_motor_start = menu.addAction("▶️ Iniciar motor")
            act_motor_start.setEnabled(row.estado_servicio in ("stopped", "paused"))
            act_motor_stop = menu.addAction("⏹️ Detener motor")
            act_motor_stop.setEnabled(row.estado_servicio in ("running", "paused"))
            act_motor_restart = menu.addAction("🔄 Reiniciar motor")
            act_motor_restart.setEnabled(row.estado_servicio == "running")
            menu.addSeparator()
        else:
            info = menu.addAction("ℹ️ LocalDB no tiene servicio de Windows (arranca bajo demanda)")
            info.setEnabled(False)
            menu.addSeparator()

        if row.agent_service_name:
            act_agente_start = menu.addAction("▶️ Iniciar agente")
            act_agente_start.setEnabled(row.estado_agente in ("stopped", "paused"))
            act_agente_stop = menu.addAction("⏹️ Detener agente")
            act_agente_stop.setEnabled(row.estado_agente in ("running", "paused"))
            act_agente_restart = menu.addAction("🔄 Reiniciar agente")
            act_agente_restart.setEnabled(row.estado_agente == "running")
            menu.addSeparator()

        act_copy = menu.addAction("📋 Copiar fila")

        action = menu.exec_(self.sql_table.viewport().mapToGlobal(pos))
        if action is None:
            return
        if action == act_motor_start:
            self._accion_servicio_sql(row.service_name, "start", f"motor de «{row.instancia}»")
        elif action == act_motor_stop:
            self._accion_servicio_sql(row.service_name, "stop", f"motor de «{row.instancia}»")
        elif action == act_motor_restart:
            self._accion_servicio_sql(row.service_name, "restart", f"motor de «{row.instancia}»")
        elif action == act_agente_start:
            self._accion_servicio_sql(row.agent_service_name, "start", f"agente de «{row.instancia}»")
        elif action == act_agente_stop:
            self._accion_servicio_sql(row.agent_service_name, "stop", f"agente de «{row.instancia}»")
        elif action == act_agente_restart:
            self._accion_servicio_sql(row.agent_service_name, "restart", f"agente de «{row.instancia}»")
        elif action == act_copy:
            cols = [self.sql_proxy.data(self.sql_proxy.index(idx.row(), c), Qt.DisplayRole)
                    for c in range(len(SqlInstanceTableModel.HEADERS))]
            QApplication.clipboard().setText("\t".join(str(v or "") for v in cols))

    def _accion_servicio_sql(self, service_name: str, accion: str, etiqueta: str):
        """accion: 'start'|'stop'|'restart'. Reutiliza self.service_manager
        (mismo ServiceManager que la pestaña Servicios, inicializado una
        sola vez en __init__) — SQL Server no necesita un mecanismo de
        control aparte, sus servicios son servicios de Windows normales."""
        etiquetas = {"start": "Iniciar", "stop": "Detener", "restart": "Reiniciar"}
        verbo = etiquetas[accion]
        texto = f"¿{verbo} el {etiqueta}?"
        if accion in ("stop", "restart"):
            dependientes = self.service_manager.get_dependent_services(service_name)
            if dependientes:
                texto += ("\n\n⚠ Estos servicios dependen de este y se detendrán también: "
                          + ", ".join(dependientes))
        if not self._confirmar_accion(f"{verbo} — {etiqueta}", texto):
            return
        self.set_status(f"🔄 {_GERUNDIO.get(verbo, verbo)} {etiqueta}...")
        QApplication.processEvents()
        metodo = {
            "start": self.service_manager.start_service,
            "stop": self.service_manager.stop_service,
            "restart": self.service_manager.restart_service,
        }[accion]
        ok, msg = metodo(service_name)
        if hasattr(self, "repair_audit_db"):
            self.repair_audit_db.registrar(
                seccion="🗄️ SQL Server", accion=f"{verbo} servicio",
                objetivo=f"{etiqueta} ({service_name})", exito=ok, detalle=msg if not ok else "OK",
            )
        if ok:
            self.set_status(f"✅ {etiqueta} — {verbo} completado")
            self.refresh_sql_server()
        else:
            self._avisar("Error", f"No se pudo {verbo.lower()} el {etiqueta}.\n\n{msg}", QMessageBox.Warning)
