# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña Active Directory: tabla, refresco, exportar.
Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).

Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

import os
import csv
import time
from typing import List
from html import escape as html_escape

import win32api

from PyQt5.QtCore import Qt, QRegExp
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QMessageBox, QTableView,
)

from msys.core.utils import ruta_guardado_predeterminada, dialogo_guardar_archivo, _SafeCsvWriter
from msys.models.rows import ADInfoRow
from msys.models.table_models import ADInfoTableModel, _FechaAwareSortProxy
from msys.widgets.delegates import _setup_header
from msys.collectors import ADInfoCollector


class ActiveDirectoryMixin:
    def crear_tab_ad_info(self):
        """Crea la pestaña de Active Directory."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        # Cabecera: TODO en una fila, mismo orden que el resto de la app
        # (hostname, búsqueda, resto de acciones, "Actualizar" a la derecha).
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)

        import socket
        hostname = socket.gethostname()
        lbl_hostname = QLabel(f"🖥️ {hostname}")
        lbl_hostname.setObjectName("hostnameLabel")
        actions_layout.addWidget(lbl_hostname)

        self.search_ad = QLineEdit()
        self.search_ad.setPlaceholderText("🔎 Buscar en AD...")
        self.search_ad.textChanged.connect(lambda: self.ad_proxy.setFilterRegExp(QRegExp(self.search_ad.text(), Qt.CaseInsensitive, QRegExp.FixedString)))
        actions_layout.addWidget(self.search_ad, 3)

        actions_layout.addStretch()

        btn_instalar_rsat = QPushButton("📥 Instalar RSAT")
        btn_instalar_rsat.setToolTip(
            "Instala las herramientas de administración remota de Active Directory y "
            "Directivas de Grupo (requiere administrador)")
        btn_instalar_rsat.clicked.connect(self._instalar_rsat)
        actions_layout.addWidget(btn_instalar_rsat)

        self.btn_refresh_ad = QPushButton("🔄 Consultar AD")
        self.btn_refresh_ad.setObjectName("primary")
        self.btn_refresh_ad.clicked.connect(self.refresh_ad_info)
        actions_layout.addWidget(self.btn_refresh_ad)

        layout.addLayout(actions_layout)

        # Tabla
        self.ad_model = ADInfoTableModel([])
        self.ad_proxy = _FechaAwareSortProxy(self)
        self.ad_proxy.setSourceModel(self.ad_model)
        self.ad_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.ad_proxy.setFilterKeyColumn(-1)

        self.ad_table = QTableView()
        self.ad_table.setModel(self.ad_proxy)
        self.ad_table.setAlternatingRowColors(True)
        self.ad_table.setSelectionBehavior(QTableView.SelectRows)
        self.ad_table.verticalHeader().setVisible(False)
        self.ad_table.setSortingEnabled(True)

        _setup_header(self.ad_table, 6, stretch_col=5)

        layout.addWidget(self.ad_table)

        return tab

    def _instalar_rsat(self):
        """Instala RSAT (AD + Directivas de Grupo) en una consola elevada
        aparte. Compartido con la pestaña 🏛️ Políticas de Grupo (ver
        msys/ui/group_policy.py) — misma acción, mismo botón, dos sitios
        donde tiene sentido ofrecerla."""
        respuesta = QMessageBox.question(
            self, "Instalar RSAT",
            "Se van a instalar las herramientas RSAT de Active Directory y de "
            "Directivas de Grupo.\n\n"
            "Requiere permisos de administrador y conexión a Windows Update — puede "
            "tardar varios minutos. Se abre una consola de PowerShell aparte; cuando "
            "termine, pulsa Actualizar en la pestaña correspondiente.\n\n¿Continuar?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        )
        if respuesta != QMessageBox.Yes:
            return

        comando = (
            "Write-Host 'Instalando RSAT: Active Directory...' -ForegroundColor Cyan; "
            "Add-WindowsCapability -Online -Name 'Rsat.ActiveDirectory.DS-LDS.Tools~~~~0.0.1.0'; "
            "Write-Host ''; Write-Host 'Instalando RSAT: Directivas de Grupo...' -ForegroundColor Cyan; "
            "Add-WindowsCapability -Online -Name 'Rsat.GroupPolicy.Management.Tools~~~~0.0.1.0'; "
            "Write-Host ''; Write-Host 'Completado. Puedes cerrar esta ventana y pulsar Actualizar en MASys.' -ForegroundColor Green"
        )
        argumentos = f'-NoExit -ExecutionPolicy Bypass -Command "{comando}"'
        try:
            win32api.ShellExecute(0, "runas", "powershell.exe", argumentos, None, 1)
            if hasattr(self, "set_status"):
                self.set_status("📥 Instalación de RSAT iniciada en consola aparte...")
        except Exception as e:
            QMessageBox.warning(self, "Error", f"No se pudo iniciar la instalación de RSAT:\n\n{e}")

    def refresh_ad_info(self):
        """Actualiza información de Active Directory."""
        if not hasattr(self, 'btn_refresh_ad'):
            # La pestaña de AD no se creó (RSAT no disponible en este equipo)
            return
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.set_status("🔄 Consultando Active Directory...")
        self.btn_refresh_ad.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.worker = ADInfoCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_ad_data)
        self.worker.finished_error.connect(self._on_ad_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_ad_data(self, rows: List[ADInfoRow]):
        """Procesa datos de Active Directory."""
        self.ad_model.setRows(rows)
        self.btn_refresh_ad.setEnabled(True)
        self.progress_bar.setVisible(False)
        total = len(rows)
        self.set_status(f"✅ {total} objetos de AD encontrados")

    def _on_ad_error(self, msg: str):
        """Maneja errores de Active Directory."""
        self.btn_refresh_ad.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al consultar Active Directory")
        self._avisar("Error", f"No se pudo obtener información de AD:\n\n{msg}", QMessageBox.Warning)

    def copy_ad_selection(self):
        """Copia objetos de AD seleccionados al portapapeles."""
        selected_indexes = self.ad_table.selectionModel().selectedRows()
        if not selected_indexes:
            self._avisar("Copiar", "No hay objetos seleccionados.")
            return

        lines = []
        lines.append("=" * 80)
        lines.append("ACTIVE DIRECTORY")
        lines.append("=" * 80)
        lines.append("")

        for index in selected_indexes:
            row = index.row()
            tipo = str(self.ad_proxy.data(self.ad_proxy.index(row, 0), Qt.DisplayRole) or "")
            nombre = str(self.ad_proxy.data(self.ad_proxy.index(row, 1), Qt.DisplayRole) or "")
            sam = str(self.ad_proxy.data(self.ad_proxy.index(row, 2), Qt.DisplayRole) or "")
            habilitado = str(self.ad_proxy.data(self.ad_proxy.index(row, 3), Qt.DisplayRole) or "")

            lines.append(f"{tipo}: {nombre}")
            lines.append(f"  SAM: {sam} | Habilitado: {habilitado}")
            lines.append("")

        lines.append("=" * 80)

        text = "\n".join(lines)
        QApplication.clipboard().setText(text)
        self.set_status(f"✅ Copiados {len(selected_indexes)} objetos al portapapeles")

    def export_ad_csv(self):
        """Exporta objetos de Active Directory a CSV."""
        if self.ad_proxy.rowCount() == 0:
            self._avisar("Exportar CSV", "No hay objetos para exportar.")
            return

        default_name = f"ActiveDirectory_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        path = dialogo_guardar_archivo(self, "Guardar CSV", ruta_guardado_predeterminada(default_name), "CSV (*.csv)")
        if not path:
            return

        try:
            headers = ADInfoTableModel.HEADERS
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = _SafeCsvWriter(csv.writer(f, delimiter=";"))
                writer.writerow(headers)
                for r in range(self.ad_proxy.rowCount()):
                    row_data = []
                    for c in range(len(headers)):
                        idx = self.ad_proxy.index(r, c)
                        val = self.ad_proxy.data(idx, Qt.DisplayRole)
                        row_data.append(str(val) if val else "")
                    writer.writerow(row_data)

            total_exported = self.ad_proxy.rowCount()
            self.set_status(f"✅ Exportado: {os.path.basename(path)} ({total_exported} objetos)")
            self._avisar("Éxito", f"Exportados {total_exported} objetos a:\n\n{path}")
        except Exception as e:
            self._avisar("Error", f"No se pudo exportar:\n\n{e}", QMessageBox.Critical)

    def export_ad_html(self):
        """Exporta objetos de Active Directory a HTML."""
        if self.ad_proxy.rowCount() == 0:
            self._avisar("Exportar HTML", "No hay objetos para exportar.")
            return

        default_name = f"ActiveDirectory_{time.strftime('%Y%m%d_%H%M%S')}.html"
        path = dialogo_guardar_archivo(self, "Guardar HTML", ruta_guardado_predeterminada(default_name), "HTML (*.html)")
        if not path:
            return

        try:
            headers = ADInfoTableModel.HEADERS
            total_rows = self.ad_proxy.rowCount()

            rows_data = []
            for r in range(total_rows):
                row_data = []
                for c in range(len(headers)):
                    idx = self.ad_proxy.index(r, c)
                    val = self.ad_proxy.data(idx, Qt.DisplayRole)
                    row_data.append(str(val) if val else "")
                rows_data.append(row_data)

            html = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Active Directory - {time.strftime('%Y-%m-%d %H:%M:%S')}</title>
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
            <h1>Active Directory</h1>
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

            self.set_status(f"✅ Exportado: {os.path.basename(path)} ({total_rows} objetos)")
            self._avisar("Éxito", f"Exportados {total_rows} objetos a:\n\n{path}")
        except Exception as e:
            self._avisar("Error", f"No se pudo exportar:\n\n{e}", QMessageBox.Critical)

