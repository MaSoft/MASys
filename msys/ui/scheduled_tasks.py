# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña Tareas Programadas: tabla, filtros,
detección de sospechosas, acciones, menu contextual, exportar.
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
    QPushButton, QCheckBox, QMenu, QMessageBox, QTableView,
)

from msys.core.utils import ruta_guardado_predeterminada, dialogo_guardar_archivo, _SafeCsvWriter, is_admin
from msys.models.rows import ScheduledTaskRow
from msys.models.table_models import ScheduledTaskTableModel, ScheduledTaskFilterProxy
from msys.widgets.delegates import _setup_header
from msys.collectors import ScheduledTaskCollector


class TareasProgramadasMixin:
    def crear_tab_scheduled_tasks(self):
        """Crea la pestaña de tareas programadas."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        # Barra de búsqueda y acciones — una sola fila: hostname, búsqueda,
        # filtros, hueco elástico, acción independiente, Actualizar.
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)

        import socket
        hostname = socket.gethostname()
        lbl_hostname = QLabel(f"🖥️ {hostname}")
        lbl_hostname.setObjectName("hostnameLabel")
        actions_layout.addWidget(lbl_hostname)

        self.search_tasks = QLineEdit()
        self.search_tasks.setPlaceholderText("🔎 Buscar tareas...")
        self.search_tasks.textChanged.connect(self._filtrar_tasks_texto)
        actions_layout.addWidget(self.search_tasks, 3)

        actions_layout.addStretch()

        self.btn_abrir_programador_tareas = QPushButton("🗓️ Abrir Programador de tareas")
        self.btn_abrir_programador_tareas.setObjectName("ghost")
        self.btn_abrir_programador_tareas.clicked.connect(lambda: subprocess.Popen(
            ["powershell", "-Command", "Start-Process taskschd.msc"],
            startupinfo=self._get_startupinfo()
        ))
        actions_layout.addWidget(self.btn_abrir_programador_tareas)

        self.btn_refresh_tasks = QPushButton("🔄 Actualizar")
        self.btn_refresh_tasks.setObjectName("primary")
        self.btn_refresh_tasks.clicked.connect(self.refresh_scheduled_tasks)
        actions_layout.addWidget(self.btn_refresh_tasks)

        layout.addLayout(actions_layout)

        # Segunda fila: filtros (checkboxes combinables en AND) + contador
        # a la derecha.
        filtros_layout = QHBoxLayout()
        filtros_layout.setSpacing(8)

        self.chk_tasks_sospechosas = QCheckBox("Solo sospechosas")
        self.chk_tasks_sospechosas.setToolTip(
            "Tareas cuya acción se ejecuta desde una carpeta temporal/de descargas, o "
            "contiene parámetros típicos de bypass/ofuscación — no 'ocultas' a secas "
            "(eso es normal en ~1 de cada 5 tareas de cualquier equipo).")
        self.chk_tasks_sospechosas.stateChanged.connect(self._filtrar_tasks)
        filtros_layout.addWidget(self.chk_tasks_sospechosas)

        self.chk_tasks_deshabilitadas = QCheckBox("Mostrar deshabilitadas")
        self.chk_tasks_deshabilitadas.stateChanged.connect(self._filtrar_tasks)
        filtros_layout.addWidget(self.chk_tasks_deshabilitadas)

        filtros_layout.addStretch()

        self.lbl_tasks_count = QLabel("0 / 0")
        self.lbl_tasks_count.setObjectName("countLabel")
        filtros_layout.addWidget(self.lbl_tasks_count)

        layout.addLayout(filtros_layout)

        # Tabla
        self.tasks_model = ScheduledTaskTableModel([])
        self.tasks_proxy = ScheduledTaskFilterProxy(self)
        self.tasks_proxy.setSourceModel(self.tasks_model)
        self.tasks_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.tasks_proxy.setFilterKeyColumn(-1)

        self.tasks_table = QTableView()
        self.tasks_table.setModel(self.tasks_proxy)
        self.tasks_table.setAlternatingRowColors(True)
        self.tasks_table.setSelectionBehavior(QTableView.SelectRows)
        self.tasks_table.setSelectionMode(QTableView.SingleSelection)
        self.tasks_table.verticalHeader().setVisible(False)
        self.tasks_table.setSortingEnabled(True)
        self.tasks_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tasks_table.customContextMenuRequested.connect(self._ctx_tasks)

        _setup_header(self.tasks_table, len(ScheduledTaskTableModel.HEADERS), stretch_col=0)

        layout.addWidget(self.tasks_table)

        return tab

    def _filtrar_tasks_texto(self):
        self.tasks_proxy.setFilterRegExp(QRegExp(self.search_tasks.text(), Qt.CaseInsensitive, QRegExp.FixedString))
        self._update_tasks_count()

    def _filtrar_tasks(self):
        self.tasks_proxy.setFiltros(
            self.chk_tasks_sospechosas.isChecked(),
            self.chk_tasks_deshabilitadas.isChecked(),
        )
        self._update_tasks_count()

    def _update_tasks_count(self):
        self.lbl_tasks_count.setText(f"{self.tasks_proxy.rowCount()} / {self.tasks_model.rowCount()}")

    def refresh_scheduled_tasks(self):
        """Actualiza lista de tareas programadas."""
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.set_status("🔄 Escaneando tareas programadas...")
        self.btn_refresh_tasks.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.worker = ScheduledTaskCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_tasks_data)
        self.worker.finished_error.connect(self._on_tasks_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_tasks_data(self, rows: List[ScheduledTaskRow]):
        """Procesa datos de tareas programadas."""
        self.tasks_model.setRows(rows)
        self.btn_refresh_tasks.setEnabled(True)
        self.progress_bar.setVisible(False)
        self._update_tasks_count()
        total = len(rows)
        sospechosas = sum(1 for r in rows if r.es_sospechosa)
        if sospechosas:
            self.set_status(f"✅ {total} tareas encontradas (⚠️ {sospechosas} con indicios sospechosos)")
        else:
            self.set_status(f"✅ {total} tareas encontradas")

    def _on_tasks_error(self, msg: str):
        """Maneja errores de tareas programadas."""
        self.btn_refresh_tasks.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al escanear tareas programadas")
        self._avisar("Error", f"No se pudieron obtener las tareas programadas:\n\n{msg}", QMessageBox.Warning)

    def _tarea_seleccionada(self):
        sel = self.tasks_table.selectionModel()
        if not sel or not sel.hasSelection():
            return None
        idx = sel.selectedRows()[0]
        source_index = self.tasks_proxy.mapToSource(idx)
        rows = self.tasks_model.rows()
        if 0 <= source_index.row() < len(rows):
            return rows[source_index.row()]
        return None

    def _ejecutar_accion_tarea(self, accion: str, row, metodo) -> bool:
        """Único punto de verdad para ejecutar+auditar+refrescar una acción
        sobre una tarea programada — usado desde el menú contextual y desde
        Soporte."""
        ok, msg = metodo(row.nombre, row.ruta)
        self.repair_audit_db.registrar(
            seccion="⏰ Tareas Programadas", accion=accion,
            objetivo=f"{row.ruta}{row.nombre}", exito=ok, detalle=msg or "OK",
        )
        if ok:
            self.set_status(f"✅ {accion}: «{row.nombre}»")
            if hasattr(self, 'refresh_scheduled_tasks'):
                self.refresh_scheduled_tasks()
        else:
            self._avisar("Con avisos", f"No se pudo completar «{accion}»:\n\n{msg}", QMessageBox.Warning)
        return ok

    def _habilitar_deshabilitar_tarea_seleccionada(self, row=None):
        """row=None usa la selección de la tabla principal (menú contextual
        de la pestaña); se le puede pasar una fila explícita desde otro
        sitio (p. ej. el menú contextual del calendario de ejecución)."""
        row = row or self._tarea_seleccionada()
        if not row:
            return
        if not is_admin():
            self._avisar(
                "Se requieren privilegios de administrador",
                "Habilitar o deshabilitar una tarea requiere ejecutar MASys como administrador.",
                QMessageBox.Warning,
            )
            return
        deshabilitada = "deshabilitada" in (row.estado or "").strip().lower()
        if deshabilitada:
            self._ejecutar_accion_tarea("Habilitar tarea", row, self.task_manager.enable)
        else:
            self._ejecutar_accion_tarea("Deshabilitar tarea", row, self.task_manager.disable)

    def _ejecutar_tarea_ahora_seleccionada(self, row=None):
        row = row or self._tarea_seleccionada()
        if not row:
            return
        if not self._confirmar_accion(
            "Ejecutar tarea ahora",
            f"¿Ejecutar «{row.nombre}» ahora mismo, fuera de su programación normal?",
        ):
            return
        self._ejecutar_accion_tarea("Ejecutar tarea ahora", row, self.task_manager.run_now)

    def _eliminar_tarea_seleccionada(self, row=None):
        row = row or self._tarea_seleccionada()
        if not row:
            return
        if not is_admin():
            self._avisar(
                "Se requieren privilegios de administrador",
                "Eliminar una tarea programada requiere ejecutar MASys como administrador.",
                QMessageBox.Warning,
            )
            return
        if not self._confirmar_accion(
            "Eliminar tarea programada",
            f"¿Eliminar definitivamente «{row.nombre}»?\n\nEsta acción no es reversible.",
            icono=QMessageBox.Warning,
        ):
            return
        # 5.5.0: copia del XML de la tarea antes de borrarla (se restaura con schtasks /create /xml)
        ok_copia, copia = self._copia_previa(
            "TAREAS", f"{row.nombre}.xml",
            "Export-ScheduledTask -TaskName '{nombre}' -TaskPath '{ruta_tarea}' | "
            "Out-File -LiteralPath '{ruta}' -Encoding Unicode",
            nombre=row.nombre, ruta_tarea=row.ruta or "\\")
        if not ok_copia and not self._seguir_sin_copia(f"la tarea «{row.nombre}»", copia):
            return
        if self._ejecutar_accion_tarea("Eliminar tarea", row, self.task_manager.delete) and ok_copia:
            self._avisar("Tarea eliminada", f"Se guardó una copia de la tarea en:\n{copia}\n\n"
                         f"Para recuperarla: schtasks /create /tn \"{row.nombre}\" /xml \"{copia}\"")

    def _ver_accion_tarea_seleccionada(self, row=None):
        row = row or self._tarea_seleccionada()
        if not row:
            return
        texto = row.accion.strip() if row.accion else "(esta tarea no tiene una acción de tipo 'Ejecutar programa', o no se pudo leer)"
        self._avisar(f"Acción de «{row.nombre}»", texto)

    def _ctx_tasks(self, pos):
        idx = self.tasks_table.indexAt(pos)
        if not idx.isValid():
            return
        self.tasks_table.selectRow(idx.row())
        row = self._tarea_seleccionada()
        if not row:
            return

        deshabilitada = "deshabilitada" in (row.estado or "").strip().lower()

        menu = QMenu(self)
        act_toggle = menu.addAction("▶️ Habilitar" if deshabilitada else "⏸️ Deshabilitar")
        act_run = menu.addAction("🚀 Ejecutar ahora")
        menu.addSeparator()
        act_ver_accion = menu.addAction("📄 Ver acción completa")
        act_ver_accion.setEnabled(bool(row.accion))
        menu.addSeparator()
        act_eliminar = menu.addAction("✕ Eliminar tarea")
        menu.addSeparator()
        act_copy = menu.addAction("📋 Copiar fila")

        action = menu.exec_(self.tasks_table.viewport().mapToGlobal(pos))
        if action == act_toggle:
            self._habilitar_deshabilitar_tarea_seleccionada()
        elif action == act_run:
            self._ejecutar_tarea_ahora_seleccionada()
        elif action == act_ver_accion:
            self._ver_accion_tarea_seleccionada()
        elif action == act_eliminar:
            self._eliminar_tarea_seleccionada()
        elif action == act_copy:
            cols = [self.tasks_proxy.data(self.tasks_proxy.index(idx.row(), c), Qt.DisplayRole)
                    for c in range(len(ScheduledTaskTableModel.HEADERS))]
            QApplication.clipboard().setText("\t".join(str(v or "") for v in cols))

    def copy_tasks_selection(self):
        """Copia tareas seleccionadas al portapapeles."""
        selected_indexes = self.tasks_table.selectionModel().selectedRows()
        if not selected_indexes:
            self._avisar("Copiar", "No hay tareas seleccionadas.")
            return

        lines = []
        lines.append("=" * 80)
        lines.append("TAREAS PROGRAMADAS")
        lines.append("=" * 80)
        lines.append("")

        for index in selected_indexes:
            row = index.row()
            nombre = str(self.tasks_proxy.data(self.tasks_proxy.index(row, 0), Qt.DisplayRole) or "")
            estado = str(self.tasks_proxy.data(self.tasks_proxy.index(row, 1), Qt.DisplayRole) or "")
            proxima = str(self.tasks_proxy.data(self.tasks_proxy.index(row, 2), Qt.DisplayRole) or "")

            lines.append(f"{nombre}")
            lines.append(f"  Estado: {estado} | Próxima ejecución: {proxima}")
            lines.append("")

        lines.append("=" * 80)

        text = "\n".join(lines)
        QApplication.clipboard().setText(text)
        self.set_status(f"✅ Copiadas {len(selected_indexes)} tareas al portapapeles")

    def export_tasks_csv(self):
        """Exporta tareas programadas a CSV."""
        if self.tasks_proxy.rowCount() == 0:
            self._avisar("Exportar CSV", "No hay tareas para exportar.")
            return

        default_name = f"TareasProgramadas_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        path = dialogo_guardar_archivo(self, "Guardar CSV", ruta_guardado_predeterminada(default_name), "CSV (*.csv)")
        if not path:
            return

        try:
            headers = ScheduledTaskTableModel.HEADERS
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = _SafeCsvWriter(csv.writer(f, delimiter=";"))
                writer.writerow(headers)
                for r in range(self.tasks_proxy.rowCount()):
                    row_data = []
                    for c in range(len(headers)):
                        idx = self.tasks_proxy.index(r, c)
                        val = self.tasks_proxy.data(idx, Qt.DisplayRole)
                        row_data.append(str(val) if val else "")
                    writer.writerow(row_data)

            total_exported = self.tasks_proxy.rowCount()
            self.set_status(f"✅ Exportado: {os.path.basename(path)} ({total_exported} tareas)")
            self._avisar("Éxito", f"Exportadas {total_exported} tareas a:\n\n{path}")
        except Exception as e:
            self._avisar("Error", f"No se pudo exportar:\n\n{e}", QMessageBox.Critical)

    def export_tasks_html(self):
        """Exporta tareas programadas a HTML."""
        if self.tasks_proxy.rowCount() == 0:
            self._avisar("Exportar HTML", "No hay tareas para exportar.")
            return

        default_name = f"TareasProgramadas_{time.strftime('%Y%m%d_%H%M%S')}.html"
        path = dialogo_guardar_archivo(self, "Guardar HTML", ruta_guardado_predeterminada(default_name), "HTML (*.html)")
        if not path:
            return

        try:
            headers = ScheduledTaskTableModel.HEADERS
            total_rows = self.tasks_proxy.rowCount()

            rows_data = []
            for r in range(total_rows):
                row_data = []
                for c in range(len(headers)):
                    idx = self.tasks_proxy.index(r, c)
                    val = self.tasks_proxy.data(idx, Qt.DisplayRole)
                    row_data.append(str(val) if val else "")
                rows_data.append(row_data)

            html = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Tareas Programadas - {time.strftime('%Y-%m-%d %H:%M:%S')}</title>
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
            <h1>Tareas Programadas</h1>
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

            self.set_status(f"✅ Exportado: {os.path.basename(path)} ({total_rows} tareas)")
            self._avisar("Éxito", f"Exportadas {total_rows} tareas a:\n\n{path}")
        except Exception as e:
            self._avisar("Error", f"No se pudo exportar:\n\n{e}", QMessageBox.Critical)
