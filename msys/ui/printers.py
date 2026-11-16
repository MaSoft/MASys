# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña Impresoras: tabla, refresco, exportar.
Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).

Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

import os
import csv
import time
import subprocess
from typing import List
from html import escape as html_escape

from PyQt5.QtCore import Qt, QRegExp
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QMessageBox, QTableView, QMenu,
)

from msys.core.utils import ruta_guardado_predeterminada, dialogo_guardar_archivo, _SafeCsvWriter, is_admin
from msys.models.rows import PrinterRow
from msys.models.table_models import PrinterTableModel, _FechaAwareSortProxy
from msys.widgets.delegates import _setup_header
from msys.collectors import PrinterCollector


class ImpresorasMixin:
    def crear_tab_printers(self):
        """Crea la pestaña de impresoras."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        # Barra de búsqueda y acciones — una sola fila: hostname, búsqueda,
        # hueco elástico, Actualizar (mismo patrón que el resto de pestañas).
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)

        import socket
        hostname = socket.gethostname()
        lbl_hostname = QLabel(f"🖥️ {hostname}")
        lbl_hostname.setObjectName("hostnameLabel")
        actions_layout.addWidget(lbl_hostname)

        self.search_printers = QLineEdit()
        self.search_printers.setPlaceholderText("🔎 Buscar impresoras...")
        self.search_printers.textChanged.connect(lambda: self.printers_proxy.setFilterRegExp(QRegExp(self.search_printers.text(), Qt.CaseInsensitive, QRegExp.FixedString)))
        actions_layout.addWidget(self.search_printers, 3)

        actions_layout.addStretch()

        # Independiente de la fila seleccionada — igual que "Abrir
        # Administrador de dispositivos" en Adm. Dispositivos.
        self.btn_abrir_impresoras_sistema = QPushButton("🖨️ Abrir Impresoras y escáneres")
        self.btn_abrir_impresoras_sistema.setObjectName("ghost")
        self.btn_abrir_impresoras_sistema.clicked.connect(lambda: subprocess.Popen(
            ["powershell", "-Command", "Start-Process ms-settings:printers"],
            startupinfo=self._get_startupinfo()
        ))
        actions_layout.addWidget(self.btn_abrir_impresoras_sistema)

        self.btn_refresh_printers = QPushButton("🔄 Actualizar")
        self.btn_refresh_printers.setObjectName("primary")
        self.btn_refresh_printers.clicked.connect(self.refresh_printers)
        actions_layout.addWidget(self.btn_refresh_printers)

        layout.addLayout(actions_layout)

        # Tabla
        self.printers_model = PrinterTableModel([])
        self.printers_proxy = _FechaAwareSortProxy(self)
        self.printers_proxy.setSourceModel(self.printers_model)
        self.printers_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.printers_proxy.setFilterKeyColumn(-1)

        self.printers_table = QTableView()
        self.printers_table.setModel(self.printers_proxy)
        self.printers_table.setAlternatingRowColors(True)
        self.printers_table.setSelectionBehavior(QTableView.SelectRows)
        self.printers_table.setSelectionMode(QTableView.SingleSelection)
        self.printers_table.verticalHeader().setVisible(False)
        self.printers_table.setSortingEnabled(True)
        self.printers_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.printers_table.customContextMenuRequested.connect(self._ctx_printers)

        _setup_header(self.printers_table, len(PrinterTableModel.HEADERS), stretch_col=0)

        layout.addWidget(self.printers_table)

        return tab

    def refresh_printers(self):
        """Actualiza lista de impresoras."""
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.set_status("🔄 Escaneando impresoras...")
        self.btn_refresh_printers.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.worker = PrinterCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_printers_data)
        self.worker.finished_error.connect(self._on_printers_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_printers_data(self, rows: List[PrinterRow]):
        """Procesa datos de impresoras."""
        self.printers_model.setRows(rows)
        self.btn_refresh_printers.setEnabled(True)
        self.progress_bar.setVisible(False)
        total = len(rows)
        self.set_status(f"✅ {total} impresoras encontradas")

    def _on_printers_error(self, msg: str):
        """Maneja errores de impresoras."""
        self.btn_refresh_printers.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al escanear impresoras")
        self._avisar("Error", f"No se pudieron obtener las impresoras:\n\n{msg}", QMessageBox.Warning)

    def _impresora_seleccionada(self):
        sel = self.printers_table.selectionModel()
        if not sel or not sel.hasSelection():
            return None
        idx = sel.selectedRows()[0]
        source_index = self.printers_proxy.mapToSource(idx)
        rows = self.printers_model.rows()
        if 0 <= source_index.row() < len(rows):
            return rows[source_index.row()]
        return None

    def _ejecutar_accion_impresora(self, accion: str, nombre: str, metodo, seccion_origen: str) -> bool:
        """Único punto de verdad para ejecutar+auditar+refrescar una acción
        sobre una impresora por nombre — usado tanto desde el menú
        contextual de esta pestaña como desde Soporte."""
        ok, msg = metodo(nombre)
        self.repair_audit_db.registrar(
            seccion=seccion_origen, accion=accion, objetivo=nombre, exito=ok, detalle=msg or "OK",
        )
        if ok:
            self.set_status(f"✅ {accion}: «{nombre}»")
            if hasattr(self, 'refresh_printers'):
                self.refresh_printers()
        else:
            self._avisar("Con avisos", f"No se pudo completar «{accion}»:\n\n{msg}", QMessageBox.Warning)
        return ok

    def _establecer_predeterminada_seleccionada(self):
        row = self._impresora_seleccionada()
        if not row:
            return
        self._ejecutar_accion_impresora(
            "Establecer como predeterminada", row.nombre, self.printer_manager.set_default,
            seccion_origen="🖨️ Impresoras")

    def _pausar_reanudar_seleccionada(self):
        row = self._impresora_seleccionada()
        if not row:
            return
        pausada = "pausada" in (row.estado or "").strip().lower()
        if pausada:
            self._ejecutar_accion_impresora(
                "Reanudar impresora", row.nombre, self.printer_manager.resume,
                seccion_origen="🖨️ Impresoras")
        else:
            self._ejecutar_accion_impresora(
                "Pausar impresora", row.nombre, self.printer_manager.pause,
                seccion_origen="🖨️ Impresoras")

    def _imprimir_pagina_prueba_seleccionada(self):
        row = self._impresora_seleccionada()
        if not row:
            return
        if not self._confirmar_accion(
            "Imprimir página de prueba",
            f"¿Enviar una página de prueba a «{row.nombre}»?\n\nConsume papel/tóner real de la impresora.",
        ):
            return
        self._ejecutar_accion_impresora(
            "Imprimir página de prueba", row.nombre, self.printer_manager.print_test_page,
            seccion_origen="🖨️ Impresoras")

    def _quitar_impresora_seleccionada(self):
        """Quita la impresora del sistema (Remove-Printer) — no reversible
        desde aquí: si es de red/compartida, habría que volver a añadirla
        con sus mismos datos de conexión."""
        row = self._impresora_seleccionada()
        if not row:
            return
        if not is_admin():
            self._avisar(
                "Se requieren privilegios de administrador",
                "Quitar una impresora requiere ejecutar MASys como administrador.",
                QMessageBox.Warning,
            )
            return
        if not self._confirmar_accion(
            "Quitar impresora",
            f"¿Quitar «{row.nombre}» del sistema?\n\n"
            "Si es una impresora de red o compartida, tendrás que volver a añadirla manualmente "
            "si hace falta en el futuro.",
            icono=QMessageBox.Warning,
        ):
            return
        self._ejecutar_accion_impresora(
            "Quitar impresora", row.nombre, self.printer_manager.remove,
            seccion_origen="🖨️ Impresoras")

    def _abrir_cola_impresion_seleccionada(self):
        row = self._impresora_seleccionada()
        if not row:
            return
        from msys.ui.print_queue_dialog import PrintQueueDialog
        dlg = PrintQueueDialog(self, self.printer_manager, row.nombre)
        dlg.exec_()

    def _ctx_printers(self, pos):
        idx = self.printers_table.indexAt(pos)
        if not idx.isValid():
            return
        self.printers_table.selectRow(idx.row())
        row = self._impresora_seleccionada()
        if not row:
            return

        pausada = "pausada" in (row.estado or "").strip().lower()
        es_predeterminada = (row.predeterminada or "").strip().lower() in ("sí", "si", "yes", "true")

        menu = QMenu(self)
        act_predeterminada = menu.addAction("⭐ Establecer como predeterminada")
        act_predeterminada.setEnabled(not es_predeterminada)
        act_pausar = menu.addAction("▶️ Reanudar impresora" if pausada else "⏸️ Pausar impresora")
        act_prueba = menu.addAction("📄 Imprimir página de prueba")
        menu.addSeparator()
        act_cola = menu.addAction("📋 Ver cola de impresión")
        menu.addSeparator()
        act_quitar = menu.addAction("✕ Quitar impresora")
        menu.addSeparator()
        act_copy = menu.addAction("📋 Copiar fila")

        action = menu.exec_(self.printers_table.viewport().mapToGlobal(pos))
        if action == act_predeterminada:
            self._establecer_predeterminada_seleccionada()
        elif action == act_pausar:
            self._pausar_reanudar_seleccionada()
        elif action == act_prueba:
            self._imprimir_pagina_prueba_seleccionada()
        elif action == act_cola:
            self._abrir_cola_impresion_seleccionada()
        elif action == act_quitar:
            self._quitar_impresora_seleccionada()
        elif action == act_copy:
            cols = [self.printers_proxy.data(self.printers_proxy.index(idx.row(), c), Qt.DisplayRole)
                    for c in range(len(PrinterTableModel.HEADERS))]
            QApplication.clipboard().setText("\t".join(str(v or "") for v in cols))

    def copy_printers_selection(self):
        """Copia impresoras seleccionadas al portapapeles."""
        selected_indexes = self.printers_table.selectionModel().selectedRows()
        if not selected_indexes:
            self._avisar("Copiar", "No hay impresoras seleccionadas.")
            return

        lines = []
        lines.append("=" * 80)
        lines.append("IMPRESORAS")
        lines.append("=" * 80)
        lines.append("")

        for index in selected_indexes:
            row = index.row()
            nombre = str(self.printers_proxy.data(self.printers_proxy.index(row, 0), Qt.DisplayRole) or "")
            tipo = str(self.printers_proxy.data(self.printers_proxy.index(row, 1), Qt.DisplayRole) or "")
            puerto = str(self.printers_proxy.data(self.printers_proxy.index(row, 2), Qt.DisplayRole) or "")
            estado = str(self.printers_proxy.data(self.printers_proxy.index(row, 3), Qt.DisplayRole) or "")

            lines.append(f"{nombre}")
            lines.append(f"  Tipo: {tipo} | Puerto: {puerto}")
            lines.append(f"  Estado: {estado}")
            lines.append("")

        lines.append("=" * 80)

        text = "\n".join(lines)
        QApplication.clipboard().setText(text)
        self.set_status(f"✅ Copiadas {len(selected_indexes)} impresoras al portapapeles")

    def export_printers_csv(self):
        """Exporta impresoras a CSV."""
        if self.printers_proxy.rowCount() == 0:
            self._avisar("Exportar CSV", "No hay impresoras para exportar.")
            return

        default_name = f"Impresoras_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        path = dialogo_guardar_archivo(self, "Guardar CSV", ruta_guardado_predeterminada(default_name), "CSV (*.csv)")
        if not path:
            return

        try:
            headers = PrinterTableModel.HEADERS
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = _SafeCsvWriter(csv.writer(f, delimiter=";"))
                writer.writerow(headers)
                for r in range(self.printers_proxy.rowCount()):
                    row_data = []
                    for c in range(len(headers)):
                        idx = self.printers_proxy.index(r, c)
                        val = self.printers_proxy.data(idx, Qt.DisplayRole)
                        row_data.append(str(val) if val else "")
                    writer.writerow(row_data)

            total_exported = self.printers_proxy.rowCount()
            self.set_status(f"✅ Exportado: {os.path.basename(path)} ({total_exported} impresoras)")
            self._avisar("Éxito", f"Exportadas {total_exported} impresoras a:\n\n{path}")
        except Exception as e:
            self._avisar("Error", f"No se pudo exportar:\n\n{e}", QMessageBox.Critical)

    def export_printers_html(self):
        """Exporta impresoras a HTML."""
        if self.printers_proxy.rowCount() == 0:
            self._avisar("Exportar HTML", "No hay impresoras para exportar.")
            return

        default_name = f"Impresoras_{time.strftime('%Y%m%d_%H%M%S')}.html"
        path = dialogo_guardar_archivo(self, "Guardar HTML", ruta_guardado_predeterminada(default_name), "HTML (*.html)")
        if not path:
            return

        try:
            headers = PrinterTableModel.HEADERS
            total_rows = self.printers_proxy.rowCount()

            rows_data = []
            for r in range(total_rows):
                row_data = []
                for c in range(len(headers)):
                    idx = self.printers_proxy.index(r, c)
                    val = self.printers_proxy.data(idx, Qt.DisplayRole)
                    row_data.append(str(val) if val else "")
                rows_data.append(row_data)

            html = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Impresoras - {time.strftime('%Y-%m-%d %H:%M:%S')}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 20px; }}
        .container {{ max-width: 100%; background: white; border-radius: 10px; box-shadow: 0 10px 40px rgba(0,0,0,0.3); overflow: hidden; }}
        header {{ background: linear-gradient(135deg, #2c3e50 0%, #34495e 100%); color: white; padding: 30px; }}
        h1 {{ font-size: 28px; margin-bottom: 10px; }}
        .subtitle {{ font-size: 14px; opacity: 0.9; }}
        table {{ width: 100%; border-collapse: collapse; }}
        thead {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; }}
        th {{ padding: 15px 10px; text-align: left; font-weight: 600; font-size: 13px; }}
        tbody tr {{ border-bottom: 1px solid #e9ecef; transition: background 0.2s; }}
        tbody tr:hover {{ background: #f8f9fa; }}
        td {{ padding: 12px 10px; font-size: 13px; color: #2c3e50; }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Impresoras</h1>
            <div class="subtitle">Generado el {time.strftime('%d/%m/%Y a las %H:%M:%S')}</div>
        </header>
        <table>
            <thead>
                <tr>
"""
            for h in headers:
                html += f"                    <th>{h}</th>\n"
            html += """                </tr>
            </thead>
            <tbody>
"""
            for row in rows_data:
                html += "                <tr>\n"
                for cell in row:
                    html += f"                    <td>{html_escape(str(cell))}</td>\n"
                html += "                </tr>\n"

            html += """            </tbody>
        </table>
    </div>
</body>
</html>
"""

            with open(path, "w", encoding="utf-8") as f:
                f.write(html)

            self.set_status(f"✅ Exportado: {os.path.basename(path)} ({total_rows} impresoras)")
            self._avisar("Éxito", f"Exportadas {total_rows} impresoras a:\n\n{path}")
        except Exception as e:
            self._avisar("Error", f"No se pudo exportar:\n\n{e}", QMessageBox.Critical)

