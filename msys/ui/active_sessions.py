# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña Sesiones Activas: tabla, refresco, exportar.
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
    QPushButton, QMessageBox, QTableView, QCheckBox, QMenu, QDialog,
    QProgressBar,
)

from msys.core.utils import (
    ruta_guardado_predeterminada, dialogo_guardar_archivo, _SafeCsvWriter, run_system_command,
)
from msys.models.table_models import (
    ActiveSessionTableModel, ActiveSessionFilterProxy, RDPConnectionHistoryTableModel,
    _FechaAwareSortProxy,
)
from msys.widgets.delegates import _setup_header
from msys.collectors import ActiveSessionCollector, RDPConnectionHistoryCollector


class RDPHistoryDialog(QDialog):
    """Histórico de conexiones RDP autenticadas (evento 1149) — usuario,
    dominio, IP de origen y fecha. Diálogo autónomo, se instancia bajo
    demanda desde SesionesActivasMixin.abrir_historial_rdp."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Historial de conexiones RDP")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(640, 440)
        self._thread = None

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(22, 20, 22, 18)

        cabecera = QHBoxLayout()
        cabecera.setSpacing(12)
        lbl_icono = QLabel("🖧")
        lbl_icono.setStyleSheet("font-size: 26pt; background: transparent; border: none;")
        cabecera.addWidget(lbl_icono)
        col_titulo = QVBoxLayout()
        col_titulo.setSpacing(2)
        lbl_titulo = QLabel("Historial de conexiones RDP")
        lbl_titulo.setStyleSheet("font-size: 13pt; font-weight: 600; background: transparent; border: none;")
        col_titulo.addWidget(lbl_titulo)
        lbl_subtitulo = QLabel("Últimas conexiones autenticadas por Escritorio Remoto, con su IP de origen")
        lbl_subtitulo.setWordWrap(True)
        lbl_subtitulo.setObjectName("hintLabel")
        col_titulo.addWidget(lbl_subtitulo)
        cabecera.addLayout(col_titulo, 1)
        layout.addLayout(cabecera)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        layout.addWidget(self.progress)

        self.model = RDPConnectionHistoryTableModel([])
        self.proxy = _FechaAwareSortProxy(self)
        self.proxy.setSourceModel(self.model)

        self.tabla = QTableView()
        self.tabla.setModel(self.proxy)
        self.tabla.setAlternatingRowColors(True)
        self.tabla.setSelectionBehavior(QTableView.SelectRows)
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.setSortingEnabled(True)
        _setup_header(self.tabla, 4, stretch_col=0)
        layout.addWidget(self.tabla, 1)

        botones = QHBoxLayout()
        self.btn_actualizar = QPushButton("🔄 Actualizar")
        self.btn_actualizar.clicked.connect(self._cargar)
        botones.addWidget(self.btn_actualizar)
        botones.addStretch()
        self.btn_csv = QPushButton("📤 Exportar CSV")
        self.btn_csv.clicked.connect(self._exportar_csv)
        botones.addWidget(self.btn_csv)
        self.btn_cerrar = QPushButton("Cerrar")
        self.btn_cerrar.clicked.connect(self.accept)
        botones.addWidget(self.btn_cerrar)
        layout.addLayout(botones)

        self._cargar()

    def _cargar(self):
        if self._thread is not None and self._thread.isRunning():
            return
        self.progress.setVisible(True)
        self.btn_actualizar.setEnabled(False)
        self._thread = RDPConnectionHistoryCollector()
        self._thread.finished_ok.connect(self._on_datos)
        self._thread.finished_error.connect(self._on_error)
        self._thread.start()

    def _on_datos(self, rows):
        self.progress.setVisible(False)
        self.btn_actualizar.setEnabled(True)
        self.model.setRows(rows)

    def _on_error(self, msg):
        self.progress.setVisible(False)
        self.btn_actualizar.setEnabled(True)
        QMessageBox.warning(self, "Error", f"No se pudo obtener el historial:\n\n{msg}")

    def _exportar_csv(self):
        if self.model.rowCount() == 0:
            return
        default_name = f"HistorialRDP_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        path = dialogo_guardar_archivo(self, "Guardar CSV", ruta_guardado_predeterminada(default_name), "CSV (*.csv)")
        if not path:
            return
        try:
            headers = RDPConnectionHistoryTableModel.HEADERS
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = _SafeCsvWriter(csv.writer(f, delimiter=";"))
                writer.writerow(headers)
                for r in range(self.proxy.rowCount()):
                    writer.writerow([
                        str(self.proxy.data(self.proxy.index(r, c), Qt.DisplayRole) or "")
                        for c in range(len(headers))
                    ])
            QMessageBox.information(self, "Éxito", f"Exportado a:\n{path}")
        except OSError as e:
            QMessageBox.critical(self, "Error", f"No se pudo exportar:\n\n{e}")

    def closeEvent(self, event):
        if self._thread is not None and self._thread.isRunning():
            self._thread.wait(3000)
        event.accept()


class SesionesActivasMixin:
    def crear_tab_sesiones_activas(self):
        """Crea la pestaña de sesiones activas del sistema."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        # ── Barra de herramientas ────────────────────────────────────────
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        import socket
        hostname = socket.gethostname()
        lbl_hostname = QLabel(f"🖥️ {hostname}")
        lbl_hostname.setObjectName("hostnameLabel")
        toolbar.addWidget(lbl_hostname)

        self.search_sesiones = QLineEdit()
        self.search_sesiones.setPlaceholderText("🔎 Buscar por usuario, tipo, estado...")
        self.search_sesiones.textChanged.connect(self._apply_sesiones_search)
        toolbar.addWidget(self.search_sesiones, 3)

        toolbar.addStretch()

        self.btn_historial_rdp = QPushButton("🖧 Historial RDP")
        self.btn_historial_rdp.clicked.connect(self.abrir_historial_rdp)
        toolbar.addWidget(self.btn_historial_rdp)

        self.btn_refresh_sesiones = QPushButton("🔄 Actualizar")
        self.btn_refresh_sesiones.setObjectName("primary")
        self.btn_refresh_sesiones.clicked.connect(self.refresh_sesiones_activas)
        toolbar.addWidget(self.btn_refresh_sesiones)

        layout.addLayout(toolbar)

        # ── Fila de filtros por tipo de sesión ───────────────────────────
        filters_layout = QHBoxLayout()
        filters_layout.setSpacing(8)

        self.chk_sesiones_interactiva = QCheckBox("🖥️ Interactiva")
        self.chk_sesiones_interactiva.setChecked(True)
        self.chk_sesiones_interactiva.stateChanged.connect(self.apply_sesiones_filter)
        filters_layout.addWidget(self.chk_sesiones_interactiva)

        self.chk_sesiones_rdp = QCheckBox("🖧 RDP")
        self.chk_sesiones_rdp.setChecked(True)
        self.chk_sesiones_rdp.stateChanged.connect(self.apply_sesiones_filter)
        filters_layout.addWidget(self.chk_sesiones_rdp)

        self.chk_sesiones_red = QCheckBox("🌐 Red")
        self.chk_sesiones_red.setChecked(True)
        self.chk_sesiones_red.stateChanged.connect(self.apply_sesiones_filter)
        filters_layout.addWidget(self.chk_sesiones_red)

        filters_layout.addStretch()

        self.lbl_sesiones_count = QLabel("Sesiones: 0/0")
        self.lbl_sesiones_count.setObjectName("countLabel")
        filters_layout.addWidget(self.lbl_sesiones_count)

        layout.addLayout(filters_layout)

        self.sesiones_model = ActiveSessionTableModel([])
        self.sesiones_proxy = ActiveSessionFilterProxy(self)
        self.sesiones_proxy.setSourceModel(self.sesiones_model)
        self.sesiones_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.sesiones_proxy.setFilterKeyColumn(-1)

        self.sesiones_table = QTableView()
        self.sesiones_table.setModel(self.sesiones_proxy)
        self.sesiones_table.setAlternatingRowColors(True)
        self.sesiones_table.setSelectionBehavior(QTableView.SelectRows)
        self.sesiones_table.verticalHeader().setVisible(False)
        self.sesiones_table.setSortingEnabled(True)

        _setup_header(self.sesiones_table, 8, stretch_col=5)
        self.sesiones_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.sesiones_table.customContextMenuRequested.connect(self._ctx_sesiones)

        layout.addWidget(self.sesiones_table)
        return tab

    def apply_sesiones_filter(self):
        """Checkboxes de filtro por tipo — patrón persistente (setFilters
        + invalidateFilter, sin recrear el proxy)."""
        self.sesiones_proxy.setFilters(
            self.chk_sesiones_interactiva.isChecked(),
            self.chk_sesiones_rdp.isChecked(),
            self.chk_sesiones_red.isChecked(),
        )
        self._update_sesiones_count()

    def _apply_sesiones_search(self, txt):
        self.sesiones_proxy.setFilterRegExp(QRegExp(txt, Qt.CaseInsensitive, QRegExp.FixedString))
        self._update_sesiones_count()

    def _ctx_sesiones(self, pos):
        """Menú contextual: cerrar sesión (logoff) y copiar fila."""
        idx = self.sesiones_table.indexAt(pos)
        if not idx.isValid():
            return
        self.sesiones_table.selectRow(idx.row())
        source_row = self.sesiones_proxy.mapToSource(idx).row()
        row = self.sesiones_model.rows()[source_row]

        menu = QMenu(self)
        act_cerrar = menu.addAction("🚪 Cerrar sesión")
        menu.addSeparator()
        act_copiar = menu.addAction("📋 Copiar fila")

        action = menu.exec_(self.sesiones_table.viewport().mapToGlobal(pos))
        if action == act_cerrar:
            self._cerrar_sesion(row)
        elif action == act_copiar:
            self.copy_sesiones_selection()

    def _cerrar_sesion(self, row):
        """Cierra la sesión (logoff.exe <id>) — acción destructiva real:
        el usuario pierde cualquier trabajo sin guardar. Requiere
        confirmación reforzada y queda registrada en el historial de
        reparaciones, tanto si tuvo éxito como si falló.

        Si la fila es la sesión ACTUAL (la que está ejecutando MASys
        ahora mismo), cerrarla desconecta al propio usuario a mitad de
        uso de la herramienta — se avisa de esto explícitamente en vez de
        mostrar el mismo texto genérico que para cualquier otra sesión."""
        if not row.id:
            self._avisar("Cerrar sesión", "Esta sesión no tiene un ID válido.", QMessageBox.Warning)
            return

        es_sesion_propia = row.sesion.strip().lower() == os.environ.get("SESSIONNAME", "").strip().lower()

        if hasattr(self, "_confirmar_reparacion"):
            if es_sesion_propia:
                cambios = [f"⚠️ Esta es TU PROPIA sesión actual («{row.usuario}», {row.tipo}, ID {row.id}) — "
                           "se te desconectará a ti mismo ahora mismo, a mitad de usar MASys"]
            else:
                cambios = [f"Cerrar la sesión de «{row.usuario}» ({row.tipo}, ID {row.id}) — "
                           "cualquier trabajo sin guardar se perderá"]
            if not self._confirmar_reparacion(
                "Cerrar sesión",
                cambios=cambios,
                reversible=False, objetivo=row.usuario, es_alerta=True,
            ):
                return

        ok, salida = run_system_command(["logoff", str(row.id)], timeout=15)
        if hasattr(self, "repair_audit_db"):
            self.repair_audit_db.registrar(
                seccion="👤 Sesiones Activas", accion="Cerrar sesión",
                objetivo=f"{row.usuario} (ID {row.id})", exito=ok, detalle=salida,
            )
        if ok:
            self.set_status(f"✅ Sesión de «{row.usuario}» cerrada")
            self.refresh_sesiones_activas()
        else:
            self._avisar("Cerrar sesión", f"No se pudo cerrar la sesión:\n\n{salida}", QMessageBox.Warning)

    def abrir_historial_rdp(self):
        dlg = RDPHistoryDialog(self)
        dlg.exec_()

    def refresh_sesiones_activas(self):
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.set_status("🔄 Consultando sesiones activas...")
        self.btn_refresh_sesiones.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.worker = ActiveSessionCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_sesiones_data)
        self.worker.finished_error.connect(self._on_sesiones_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_sesiones_data(self, rows):
        self.sesiones_model.setRows(rows)
        self.btn_refresh_sesiones.setEnabled(True)
        self.progress_bar.setVisible(False)
        self._update_sesiones_count()
        self.set_status(f"✅ {len(rows)} sesiones activas encontradas")

    def _on_sesiones_error(self, msg):
        self.btn_refresh_sesiones.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al consultar sesiones activas")
        self._avisar("Error", f"No se pudieron obtener las sesiones activas:\n\n{msg}", QMessageBox.Warning)

    def _update_sesiones_count(self):
        self.lbl_sesiones_count.setText(
            f"Sesiones: {self.sesiones_proxy.rowCount()}/{self.sesiones_model.rowCount()}")

    def copy_sesiones_selection(self):
        selected = self.sesiones_table.selectionModel().selectedRows()
        if not selected:
            self._avisar("Copiar", "No hay filas seleccionadas.")
            return
        headers = ActiveSessionTableModel.HEADERS
        lines = ["SESIONES ACTIVAS", "=" * 60, ""]
        for idx in selected:
            row = idx.row()
            vals = [str(self.sesiones_proxy.data(self.sesiones_proxy.index(row, c), Qt.DisplayRole) or "")
                    for c in range(len(headers))]
            lines.append("  ".join(f"{h}: {v}" for h, v in zip(headers, vals)))
        QApplication.clipboard().setText("\n".join(lines))
        self.set_status(f"✅ Copiadas {len(selected)} sesiones al portapapeles")

    def export_sesiones_csv(self):
        if self.sesiones_proxy.rowCount() == 0:
            self._avisar("Exportar CSV", "No hay datos para exportar.")
            return
        path = dialogo_guardar_archivo(self, "Guardar CSV",
            ruta_guardado_predeterminada(f"SesionesActivas_{time.strftime('%Y%m%d_%H%M%S')}.csv"), "CSV (*.csv)")
        if not path:
            return
        try:
            headers = ActiveSessionTableModel.HEADERS
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = _SafeCsvWriter(csv.writer(f, delimiter=";"))
                writer.writerow(headers)
                for r in range(self.sesiones_proxy.rowCount()):
                    writer.writerow([
                        str(self.sesiones_proxy.data(self.sesiones_proxy.index(r, c), Qt.DisplayRole) or "")
                        for c in range(len(headers))
                    ])
            self.set_status(f"✅ Exportado: {os.path.basename(path)}")
            self._avisar("Éxito", f"Exportado a:\n{path}")
        except Exception as e:
            self._avisar("Error", f"No se pudo exportar:\n\n{e}", QMessageBox.Critical)

