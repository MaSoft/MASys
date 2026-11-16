# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Motor transversal de exportacion e informes: CSV/HTML/PDF/Excel por seccion o multiseccion, snapshots, busqueda global, calculo del informe de seguridad. Es la maquinaria mas grande y mas acoplada de ShareInspector — todas las pestañas dependen de ella para exportar.
Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).

Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

import os
import csv
import re
import json
import time
from html import escape as html_escape

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QDialog, QHBoxLayout, QLabel,
    QLineEdit, QMessageBox, QProgressBar, QPushButton, QVBoxLayout,
)

from msys.config import CATEGORIES, CATEGORY_ORDER, SECTION_CATEGORY, logger

# Títulos de exportación que no son el nombre exacto de una sección del árbol
_SECCION_DE_TITULO = {
    "🎮 Gráficas": "🖥️ Gráficas y Monitores", "🖥️ Monitores": "🖥️ Gráficas y Monitores",
    "🌐 Navegadores": "☁️ Navegadores y Nube", "☁️ Nube": "☁️ Navegadores y Nube",
    "✅ Actualizaciones Instaladas": "🔄 Actualizaciones", "⏳ Actualizaciones Pendientes": "🔄 Actualizaciones",
    "💿 Discos": "💽 Discos Duros", "📁 Particiones": "💽 Discos Duros",
}


def _categoria_de(titulo: str):
    """(clave, etiqueta) de la categoría del árbol a la que pertenece un título de exportación."""
    seccion = _SECCION_DE_TITULO.get(titulo, titulo)
    clave = SECTION_CATEGORY.get(seccion)
    if clave is None:   # «🧪 Herramientas de red · Diagnóstico», «💽 Discos Duros - Particiones»…
        clave = next((c for nombre, c in sorted(SECTION_CATEGORY.items(), key=lambda x: -len(x[0]))
                      if titulo.startswith(nombre)), None)
    if clave is None:
        return ("zz", "📋 Otros")
    return (clave, CATEGORIES.get(clave, {}).get("label", clave))
from msys.core import informe_html
from msys.core.utils import (
    _SafeCsvWriter, _get_software_blacklist, _is_blacklisted_software,
    _is_eol_software, get_informes_folder, render_html_a_pdf,
    dialogo_guardar_archivo, dialogo_abrir_archivo, dialogo_elegir_carpeta,
    ruta_guardado_predeterminada,
)
from msys.models.rows import STARTUP_LABELS
from msys.models.table_models import (
    ADInfoTableModel, GroupPolicyTableModel, StartupProgramTableModel, BrowserCloudAccountTableModel, CertificateTableModel, ContentTableModel, CredentialTableModel, DiscoFisicoTableModel, DriverTableModel,
    EnvVarTableModel, GPUTableModel, InstalledUpdateTableModel, MonitorTableModel,
    NetworkAdapterTableModel, NetworkDeviceTableModel, NetworkDriveTableModel,
    PackageUpgradeTableModel, PartitionTableModel, PendingUpdateTableModel,
    ProcessTableModel, RegistryResultTableModel, RemoteConnectionTableModel,
    ServiceTableModel, ShareTableModel, SqlInstanceTableModel,
)
from msys.widgets.delegates import _setup_header


# Servicios de seguridad reales (nombre corto del servicio): si están en automático y
# parados, es un hallazgo. Nada de subcadenas («update» marcaba edgeupdate/gupdate).
_SERVICIOS_SEGURIDAD = {"windefend", "mpssvc", "wscsvc", "securityhealthservice", "sense", "wdnissvc", "bfe",
                        "wuauserv"}


class ReportingMixin:
    def _copiar_seleccion_generica(self, table_view, proxy, headers):
        """Copia al portapapeles (formato TSV) las filas seleccionadas de
        cualquier QTableView respaldado por un proxy con headers conocidos."""
        selection = table_view.selectionModel()
        if not selection or not selection.hasSelection():
            self._avisar("Copiar", "No hay elementos seleccionados")
            return
        rows_text = []
        for idx in selection.selectedRows():
            row = idx.row()
            vals = [str(proxy.data(proxy.index(row, c), Qt.DisplayRole) or "") for c in range(len(headers))]
            rows_text.append("\t".join(vals))
        QApplication.clipboard().setText("\n".join(rows_text))

    def _export_generic_csv(self, proxy, headers, filename_prefix):
        """Exporta cualquier tabla (proxy con rowCount/data) a CSV, reutilizando
        _SafeCsvWriter (protección contra CSV Formula Injection)."""
        path = dialogo_guardar_archivo(
            self, "Guardar CSV",
            ruta_guardado_predeterminada(f"{filename_prefix}_{time.strftime('%Y%m%d_%H%M%S')}.csv"), "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = _SafeCsvWriter(csv.writer(f, delimiter=";"))
                writer.writerow(headers)
                for r in range(proxy.rowCount()):
                    row = [str(proxy.data(proxy.index(r, c), Qt.DisplayRole) or "")
                           for c in range(len(headers))]
                    writer.writerow(row)
            self.set_status(f"✅ Exportado a {os.path.basename(path)}")
            self._avisar("Exportar", f"Datos exportados correctamente a:\n\n{path}")
        except Exception as e:
            logger.exception(f"Error exportando CSV: {e}")
            self._avisar("Error", f"No se pudo exportar a CSV:\n\n{e}", QMessageBox.Critical)

    def _export_rows_csv(self, headers, rows_data, filename_prefix):
        """Exporta filas ya materializadas (list[list[str]]) a CSV, reutilizando
        _SafeCsvWriter. A diferencia de _export_generic_csv, no depende de un
        proxy con rowCount/data — sirve tanto para tablas planas como para
        secciones basadas en árbol (Sistema, Licencias, etc.) o sub-pestañas."""
        path = dialogo_guardar_archivo(
            self, "Guardar CSV",
            ruta_guardado_predeterminada(f"{filename_prefix}_{time.strftime('%Y%m%d_%H%M%S')}.csv"), "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = _SafeCsvWriter(csv.writer(f, delimiter=";"))
                writer.writerow(headers)
                for row in rows_data:
                    writer.writerow(row)
            self.set_status(f"✅ Exportado a {os.path.basename(path)}")
            self._avisar("Exportar", f"Datos exportados correctamente a:\n\n{path}")
        except Exception as e:
            logger.exception(f"Error exportando CSV: {e}")
            self._avisar("Error", f"No se pudo exportar a CSV:\n\n{e}", QMessageBox.Critical)

    def _export_generic_html(self, proxy, headers, title, filename_prefix):
        """Exporta cualquier tabla (proxy con rowCount/data) con la plantilla común de informes
        (msys/core/informe_html.py: búsqueda, filtros por columna, orden, CSV e imprimir)."""
        path = dialogo_guardar_archivo(
            self, "Guardar informe HTML",
            ruta_guardado_predeterminada(f"{filename_prefix}_{time.strftime('%Y%m%d_%H%M%S')}.html"), "HTML (*.html)")
        if not path:
            return
        filas = [[str(proxy.data(proxy.index(r, c), Qt.DisplayRole) or "") for c in range(len(headers))]
                 for r in range(proxy.rowCount())]
        html = informe_html.documento(title, [informe_html.Seccion(title, headers, filas)], indice=False)
        self._guardar_informe_html(path, html, f"{len(filas)} registro(s).", f"✅ Informe exportado a {os.path.basename(path)}")

    def export_csv(self):
        """Exporta TODOS los datos filtrados a CSV."""
        if self.proxy.rowCount() == 0:
            self._avisar("Exportar CSV", "No hay datos para exportar.")
            return

        default_name = f"PermisosCompartidos_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        path = dialogo_guardar_archivo(self, "Guardar CSV", ruta_guardado_predeterminada(default_name), "CSV (*.csv)")
        if not path:
            return

        try:
            headers = ShareTableModel.HEADERS
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = _SafeCsvWriter(csv.writer(f, delimiter=";"))
                writer.writerow(headers)
                # Exportar TODAS las filas filtradas (sin límite)
                for r in range(self.proxy.rowCount()):
                    row_data = []
                    for c in range(len(headers)):
                        idx = self.proxy.index(r, c)
                        val = self.proxy.data(idx, Qt.DisplayRole)
                        row_data.append(str(val) if val else "")
                    writer.writerow(row_data)

            total_exported = self.proxy.rowCount()
            self.set_status(f"✅ Exportado: {os.path.basename(path)} ({total_exported} registros)")
            self._avisar("Éxito", f"Exportados {total_exported} registros a:\n\n{path}")
        except Exception as e:
            self._avisar("Error", f"No se pudo exportar:\n\n{e}", QMessageBox.Critical)

    def export_html(self):
        """Exporta datos a HTML con formato profesional para presentar a cliente."""
        if self.proxy.rowCount() == 0:
            self._avisar("Exportar HTML", "No hay datos para exportar.")
            return

        # Verificar límite de 50,000 entradas
        total_rows = self.proxy.rowCount()
        MAX_HTML_ENTRIES = 50000
        if total_rows > MAX_HTML_ENTRIES:
            msgBox = QMessageBox(self)
            msgBox.setWindowFlags(msgBox.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            msgBox.setWindowTitle('⚠️ Advertencia: Demasiadas Entradas')
            msgBox.setText(f"La exportación supera el máximo de entradas permitidas.\n\n"
                          f"Total de registros: {total_rows:,}\n"
                          f"Máximo permitido: {MAX_HTML_ENTRIES:,}\n\n"
                          f"Se recomienda:\n"
                          f"• Usar filtros para reducir resultados\n"
                          f"• Exportar a CSV para grandes volúmenes\n"
                          f"• Analizar por secciones específicas")
            msgBox.setIcon(QMessageBox.Warning)
            btn_aceptar = msgBox.addButton('Aceptar', QMessageBox.AcceptRole)
            msgBox.exec_()
            return

        # Solicitar nombre de empresa
        from PyQt5.QtWidgets import QInputDialog
        dlg_empresa = QInputDialog(self)
        dlg_empresa.setWindowFlags(dlg_empresa.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dlg_empresa.setWindowTitle("Nombre de Empresa")
        dlg_empresa.setLabelText("Introduce el nombre de la empresa para el informe:")
        ok = dlg_empresa.exec_() == QDialog.Accepted
        empresa = dlg_empresa.textValue()
        if not ok or not empresa.strip():
            empresa = "Cliente"
        # Va dentro del HTML en 4 sitios (título, cabecera, resumen, pie): escapado como el resto de datos del informe
        empresa_texto = empresa.strip()
        empresa = html_escape(empresa_texto)

        default_name = f"InformePermisos_{time.strftime('%Y%m%d_%H%M%S')}.html"
        path = dialogo_guardar_archivo(self, "Guardar HTML", ruta_guardado_predeterminada(default_name), "HTML (*.html)")
        if not path:
            return

        try:
            # Organizar datos por recurso
            resources_data = {}
            for r in range(self.proxy.rowCount()):
                recurso = str(self.proxy.data(self.proxy.index(r, 0), Qt.DisplayRole) or "")
                ruta = str(self.proxy.data(self.proxy.index(r, 1), Qt.DisplayRole) or "")
                identidad = str(self.proxy.data(self.proxy.index(r, 2), Qt.DisplayRole) or "")
                permiso = str(self.proxy.data(self.proxy.index(r, 3), Qt.DisplayRole) or "")
                tipo = str(self.proxy.data(self.proxy.index(r, 4), Qt.DisplayRole) or "")
                origen = str(self.proxy.data(self.proxy.index(r, 5), Qt.DisplayRole) or "")

                if recurso not in resources_data:
                    resources_data[recurso] = {"ruta": ruta, "permisos": []}

                resources_data[recurso]["permisos"].append({
                    "identidad": identidad,
                    "permiso": permiso,
                    "tipo": tipo,
                    "origen": origen
                })

            # Generar HTML profesional
            html = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Informe de Permisos - {empresa}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 40px 20px;
            color: #2c3e50;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            border-radius: 12px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            overflow: hidden;
        }}
        .header {{
            background: linear-gradient(135deg, #2c3e50 0%, #34495e 100%);
            color: white;
            padding: 40px;
            text-align: center;
        }}
        .header h1 {{
            font-size: 32px;
            margin-bottom: 10px;
            text-transform: uppercase;
            letter-spacing: 2px;
        }}
        .header .subtitle {{
            font-size: 16px;
            opacity: 0.9;
            font-weight: 300;
        }}
        .info-box {{
            background: #ecf0f1;
            padding: 30px 40px;
            border-bottom: 3px solid #3498db;
        }}
        .info-box p {{
            margin: 8px 0;
            font-size: 14px;
        }}
        .info-box strong {{
            color: #2c3e50;
            font-weight: 600;
        }}
        .content {{
            padding: 40px;
        }}
        .resource-block {{
            margin-bottom: 40px;
            border: 1px solid #dcdde1;
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
        }}
        .resource-header {{
            background: linear-gradient(135deg, #3498db 0%, #2980b9 100%);
            color: white;
            padding: 20px 25px;
            font-size: 18px;
            font-weight: 600;
        }}
        .resource-header .icon {{
            font-size: 24px;
            margin-right: 10px;
        }}
        .resource-path {{
            background: #f8f9fa;
            padding: 15px 25px;
            font-size: 13px;
            color: #7f8c8d;
            border-bottom: 1px solid #ecf0f1;
        }}
        .permissions-table {{
            width: 100%;
            border-collapse: collapse;
        }}
        .permissions-table thead {{
            background: #34495e;
            color: white;
        }}
        .permissions-table th {{
            padding: 15px;
            text-align: left;
            font-weight: 600;
            font-size: 13px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }}
        .permissions-table tbody tr {{
            border-bottom: 1px solid #ecf0f1;
            transition: background 0.2s;
        }}
        .permissions-table tbody tr:hover {{
            background: #f8f9fa;
        }}
        .permissions-table td {{
            padding: 15px;
            font-size: 14px;
        }}
        .permission-cell {{
            font-weight: 600;
            color: #27ae60;
        }}
        .type-permitir {{
            color: #27ae60;
            font-weight: 600;
        }}
        .type-denegar {{
            color: #e74c3c;
            font-weight: 600;
        }}
        .user-icon {{
            margin-right: 8px;
            font-size: 16px;
        }}
        .footer {{
            background: #ecf0f1;
            padding: 25px 40px;
            text-align: center;
            font-size: 13px;
            color: #7f8c8d;
            border-top: 3px solid #3498db;
        }}
        .summary {{
            background: #e8f4f8;
            border-left: 5px solid #3498db;
            padding: 20px 25px;
            margin-bottom: 30px;
            border-radius: 6px;
        }}
        .summary h2 {{
            color: #2c3e50;
            margin-bottom: 10px;
            font-size: 20px;
        }}
        .summary p {{
            color: #34495e;
            font-size: 14px;
            margin: 5px 0;
        }}
        .search-box {{
            background: white;
            padding: 20px;
            margin-bottom: 30px;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            border-left: 5px solid #27ae60;
        }}
        .search-box input {{
            width: 100%;
            padding: 12px 20px;
            font-size: 16px;
            border: 2px solid #dcdde1;
            border-radius: 6px;
            transition: all 0.3s;
        }}
        .search-box input:focus {{
            outline: none;
            border-color: #27ae60;
            box-shadow: 0 0 0 3px rgba(39,174,96,0.1);
        }}
        .search-label {{
            display: block;
            margin-bottom: 10px;
            font-weight: 600;
            color: #2c3e50;
            font-size: 14px;
        }}
        .hidden {{
            display: none !important;
        }}
        .filter-container {{
            background: white;
            padding: 20px;
            margin-bottom: 30px;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            border-left: 5px solid #9b59b6;
            display: flex;
            flex-wrap: wrap;
            gap: 15px;
            align-items: center;
        }}
        .filter-label {{
            font-weight: 600;
            color: #2c3e50;
            font-size: 14px;
            margin-right: 10px;
        }}
        .filter-checkbox {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 8px 15px;
            border: 2px solid #dcdde1;
            border-radius: 6px;
            cursor: pointer;
            transition: all 0.2s;
            user-select: none;
            background: white;
        }}
        .filter-checkbox:hover {{
            background: #f8f9fa;
            border-color: #9b59b6;
        }}
        .filter-checkbox input[type="checkbox"] {{
            cursor: pointer;
            width: 18px;
            height: 18px;
        }}
        .filter-checkbox.active {{
            background: #e8daef;
            border-color: #9b59b6;
        }}
        .filter-btn {{
            padding: 8px 20px;
            background: linear-gradient(135deg, #e74c3c 0%, #c0392b 100%);
            color: white;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-weight: 600;
            font-size: 13px;
            transition: all 0.2s;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .filter-btn:hover {{
            transform: translateY(-1px);
            box-shadow: 0 4px 8px rgba(0,0,0,0.15);
        }}
        .filter-btn:active {{
            transform: translateY(0);
        }}
        .stats-display {{
            margin-left: auto;
            padding: 8px 15px;
            background: #e8f4f8;
            border-radius: 6px;
            color: #2c3e50;
            font-weight: 600;
            font-size: 13px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>📊 Informe de Permisos</h1>
            <p class="subtitle">Análisis completo de recursos compartidos y permisos de acceso</p>
        </div>

        <div class="info-box">
            <p><strong>📌 Empresa:</strong> {empresa}</p>
            <p><strong>📅 Fecha de generación:</strong> {time.strftime('%d/%m/%Y %H:%M:%S')}</p>
            <p><strong>💻 Sistema:</strong> Análisis de recursos compartidos de Windows</p>
            <p><strong>📈 Total de registros:</strong> {self.proxy.rowCount()} permisos analizados</p>
        </div>

        <div class="content">
            <div class="summary">
                <h2>🔍 Resumen Ejecutivo</h2>
                <p>Este informe presenta un análisis detallado de los permisos asignados a cada recurso compartido en la organización <strong>{empresa}</strong>.</p>
                <p>Se han identificado <strong>{len(resources_data)}</strong> recursos compartidos con un total de <strong>{self.proxy.rowCount()}</strong> asignaciones de permisos.</p>
                <p>La información incluye usuarios individuales, grupos de dominio y cuentas del sistema con sus respectivos niveles de acceso.</p>
            </div>

            <div class="search-box">
                <label class="search-label">🔎 Buscar en resultados:</label>
                <input type="text" id="searchInput" placeholder="Escribe para filtrar por recurso, usuario, permiso, ruta...">
            </div>

            <div class="filter-container">
                <span class="filter-label">Filtros:</span>

                <label class="filter-checkbox">
                    <input type="checkbox" id="filterAdminShares">
                    <span>🔒 Ocultar Admin (C$)</span>
                </label>

                <label class="filter-checkbox">
                    <input type="checkbox" id="filterSystem">
                    <span>🖥️ Ocultar Sistema</span>
                </label>

                <label class="filter-checkbox">
                    <input type="checkbox" id="filterEveryone">
                    <span>👥 Ocultar Todos</span>
                </label>

                <label class="filter-checkbox">
                    <input type="checkbox" id="filterHideUsers">
                    <span>🚫 Ocultar Usuarios</span>
                </label>

                <label class="filter-checkbox">
                    <input type="checkbox" id="filterShowOnlyUsers">
                    <span>👤 Mostrar Solo Usuarios</span>
                </label>

                <button class="filter-btn" id="clearFilters">🔄 Limpiar Filtros</button>

                <span class="stats-display" id="statsDisplay">Mostrando: -</span>
            </div>
"""

            # Añadir cada recurso
            for recurso, data in sorted(resources_data.items()):
                recurso_safe = html_escape(str(recurso))
                ruta_safe = html_escape(str(data['ruta']))
                html += f"""
            <div class="resource-block">
                <div class="resource-header">
                    <span class="icon">📂</span> {recurso_safe}
                </div>
                <div class="resource-path">
                    <strong>Ruta física:</strong> {ruta_safe}
                </div>
                <table class="permissions-table">
                    <thead>
                        <tr>
                            <th>Usuario / Grupo</th>
                            <th>Permiso</th>
                            <th>Tipo</th>
                            <th>Origen</th>
                        </tr>
                    </thead>
                    <tbody>
"""
                for perm in data["permisos"]:
                    identidad = perm["identidad"]
                    # Determinar icono
                    if "BUILTIN" in identidad.upper() or "NT AUTHORITY" in identidad.upper():
                        icon = "🖥️"
                    elif "TODOS" in identidad.upper() or "EVERYONE" in identidad.upper():
                        icon = "👥"
                    else:
                        icon = "👤"

                    tipo_class = "type-permitir" if "Permitir" in perm["tipo"] else "type-denegar"

                    identidad_safe = html_escape(str(identidad))
                    permiso_safe = html_escape(str(perm['permiso']))
                    tipo_safe = html_escape(str(perm['tipo']))
                    origen_safe = html_escape(str(perm['origen']))

                    html += f"""
                        <tr>
                            <td><span class="user-icon">{icon}</span> {identidad_safe}</td>
                            <td class="permission-cell">{permiso_safe}</td>
                            <td class="{tipo_class}">{tipo_safe}</td>
                            <td>{origen_safe}</td>
                        </tr>
"""
                html += """
                    </tbody>
                </table>
            </div>
"""

            html += f"""
        </div>

        <div class="footer">
            <p><strong>MASys</strong> · Herramienta de análisis de recursos compartidos</p>
            <p>Generado automáticamente el {time.strftime('%d de %B de %Y a las %H:%M:%S')}</p>
            <p style="margin-top: 15px; font-style: italic;">Este informe es confidencial y está destinado únicamente para uso interno de {empresa}</p>
        </div>
    </div>

    <script>
        // Estado de filtros
        const filterState = {{
            hideAdminShares: false,
            hideSystem: false,
            hideEveryone: false,
            hideUsers: false,
            showOnlyUsers: false,
            searchTerm: ''
        }};

        // Elementos DOM
        const searchInput = document.getElementById('searchInput');
        const resourceBlocks = document.querySelectorAll('.resource-block');
        const filterAdminShares = document.getElementById('filterAdminShares');
        const filterSystem = document.getElementById('filterSystem');
        const filterEveryone = document.getElementById('filterEveryone');
        const filterHideUsers = document.getElementById('filterHideUsers');
        const filterShowOnlyUsers = document.getElementById('filterShowOnlyUsers');
        const clearFiltersBtn = document.getElementById('clearFilters');
        const statsDisplay = document.getElementById('statsDisplay');

        // Cargar estado de localStorage
        function loadFilterState() {{
            const saved = localStorage.getItem('buscacompartidos_filters');
            if (saved) {{
                try {{
                    const state = JSON.parse(saved);
                    filterState.hideAdminShares = state.hideAdminShares || false;
                    filterState.hideSystem = state.hideSystem || false;
                    filterState.hideEveryone = state.hideEveryone || false;
                    filterState.hideUsers = state.hideUsers || false;
                    filterState.showOnlyUsers = state.showOnlyUsers || false;

                    filterAdminShares.checked = filterState.hideAdminShares;
                    filterSystem.checked = filterState.hideSystem;
                    filterEveryone.checked = filterState.hideEveryone;
                    filterHideUsers.checked = filterState.hideUsers;
                    filterShowOnlyUsers.checked = filterState.showOnlyUsers;
                }} catch (e) {{
                    console.error('Error loading filter state:', e);
                }}
            }}
        }}

        // Guardar estado a localStorage
        function saveFilterState() {{
            try {{
                localStorage.setItem('buscacompartidos_filters', JSON.stringify(filterState));
            }} catch (e) {{
                console.error('Error saving filter state:', e);
            }}
        }}

        // Función para determinar si una fila debe mostrarse (EXACTAMENTE como CustomFilterProxy)
        function shouldShowRow(row, resourceName) {{
            const identidad = row.cells[0].textContent.trim();
            const identidadUpper = identidad.toUpperCase();

            // Verificar si el recurso es administrativo (termina en $)
            const isAdminResource = resourceName.trim().endsWith('$');

            // FILTRO 1: Recursos administrativos (basado en nombre del recurso)
            if (filterState.hideAdminShares && isAdminResource) {{
                return false;
            }}

            // FILTRO 2: Permisos de sistema (NO aplicar a recursos administrativos)
            if (filterState.hideSystem && !isAdminResource) {{
                if (identidadUpper.includes('NT AUTHORITY') ||
                    identidadUpper.includes('BUILTIN') ||
                    identidadUpper.includes('NT SERVICE')) {{
                    return false;
                }}
            }}

            // FILTRO 3: Grupo "Todos" (NO aplicar a recursos administrativos)
            if (filterState.hideEveryone && !isAdminResource) {{
                if (identidadUpper.includes('TODOS') || identidadUpper.includes('EVERYONE')) {{
                    return false;
                }}
            }}

            // FILTRO 4: Ocultar usuarios (NO aplicar a recursos administrativos)
            if (filterState.hideUsers && !isAdminResource) {{
                // Ocultar si NO es sistema Y NO es "Todos"
                const isSystem = identidadUpper.includes('NT AUTHORITY') ||
                                identidadUpper.includes('BUILTIN') ||
                                identidadUpper.includes('NT SERVICE');
                const isEveryone = identidadUpper.includes('TODOS') ||
                                  identidadUpper.includes('EVERYONE');

                if (!isSystem && !isEveryone) {{
                    return false;
                }}
            }}

            // FILTRO 5: Mostrar solo usuarios (NO aplicar a recursos administrativos)
            if (filterState.showOnlyUsers && !isAdminResource) {{
                // Mostrar solo si NO es sistema Y NO es "Todos"
                const isSystem = identidadUpper.includes('NT AUTHORITY') ||
                                identidadUpper.includes('BUILTIN') ||
                                identidadUpper.includes('NT SERVICE');
                const isEveryone = identidadUpper.includes('TODOS') ||
                                  identidadUpper.includes('EVERYONE');

                if (isSystem || isEveryone) {{
                    return false;
                }}
            }}

            // FILTRO 6: Búsqueda de texto
            if (filterState.searchTerm) {{
                const rowText = row.textContent.toLowerCase();
                if (!rowText.includes(filterState.searchTerm)) {{
                    return false;
                }}
            }}

            return true;
        }}

        // Aplicar filtros a todos los bloques
        function applyFilters() {{
            let totalRows = 0;
            let visibleRows = 0;
            let totalBlocks = resourceBlocks.length;
            let visibleBlocks = 0;

            resourceBlocks.forEach(block => {{
                // Obtener nombre del recurso (quitar icono)
                const resourceHeader = block.querySelector('.resource-header').textContent;
                const resourceName = resourceHeader.replace('📂', '').trim();
                const resourcePath = block.querySelector('.resource-path').textContent.toLowerCase();

                // Verificar si la búsqueda coincide con nombre o ruta del recurso
                const resourceMatches = filterState.searchTerm === '' ||
                    resourceName.toLowerCase().includes(filterState.searchTerm) ||
                    resourcePath.includes(filterState.searchTerm);

                // Procesar filas
                const rows = block.querySelectorAll('.permissions-table tbody tr');
                let hasVisibleRows = false;

                rows.forEach(row => {{
                    totalRows++;
                    if (shouldShowRow(row, resourceName)) {{
                        row.classList.remove('hidden');
                        hasVisibleRows = true;
                        visibleRows++;
                    }} else {{
                        row.classList.add('hidden');
                    }}
                }});

                // Mostrar bloque si tiene filas visibles O si el recurso coincide con búsqueda
                if (hasVisibleRows || resourceMatches) {{
                    block.classList.remove('hidden');
                    visibleBlocks++;
                }} else {{
                    block.classList.add('hidden');
                }}
            }});

            // Actualizar estadísticas
            if (totalRows === visibleRows) {{
                statsDisplay.textContent = `Mostrando: ${{visibleRows}} permisos`;
            }} else {{
                statsDisplay.textContent = `Mostrando: ${{visibleRows}} de ${{totalRows}} permisos`;
            }}
        }}

        // Event listeners para filtros
        filterAdminShares.addEventListener('change', function() {{
            filterState.hideAdminShares = this.checked;
            this.parentElement.classList.toggle('active', this.checked);
            saveFilterState();
            applyFilters();
        }});

        filterSystem.addEventListener('change', function() {{
            filterState.hideSystem = this.checked;
            this.parentElement.classList.toggle('active', this.checked);
            saveFilterState();
            applyFilters();
        }});

        filterEveryone.addEventListener('change', function() {{
            filterState.hideEveryone = this.checked;
            this.parentElement.classList.toggle('active', this.checked);
            saveFilterState();
            applyFilters();
        }});

        filterHideUsers.addEventListener('change', function() {{
            filterState.hideUsers = this.checked;
            this.parentElement.classList.toggle('active', this.checked);
            // Mutual exclusivity
            if (this.checked && filterState.showOnlyUsers) {{
                filterState.showOnlyUsers = false;
                filterShowOnlyUsers.checked = false;
                filterShowOnlyUsers.parentElement.classList.remove('active');
            }}
            saveFilterState();
            applyFilters();
        }});

        filterShowOnlyUsers.addEventListener('change', function() {{
            filterState.showOnlyUsers = this.checked;
            this.parentElement.classList.toggle('active', this.checked);
            // Mutual exclusivity
            if (this.checked && filterState.hideUsers) {{
                filterState.hideUsers = false;
                filterHideUsers.checked = false;
                filterHideUsers.parentElement.classList.remove('active');
            }}
            saveFilterState();
            applyFilters();
        }});

        searchInput.addEventListener('input', function() {{
            filterState.searchTerm = this.value.toLowerCase().trim();
            applyFilters();
        }});

        clearFiltersBtn.addEventListener('click', function() {{
            // Resetear todos los filtros
            filterState.hideAdminShares = false;
            filterState.hideSystem = false;
            filterState.hideEveryone = false;
            filterState.hideUsers = false;
            filterState.showOnlyUsers = false;
            filterState.searchTerm = '';

            filterAdminShares.checked = false;
            filterSystem.checked = false;
            filterEveryone.checked = false;
            filterHideUsers.checked = false;
            filterShowOnlyUsers.checked = false;
            searchInput.value = '';

            document.querySelectorAll('.filter-checkbox').forEach(el => el.classList.remove('active'));

            saveFilterState();
            applyFilters();
        }});

        // Inicialización
        window.addEventListener('load', () => {{
            loadFilterState();
            applyFilters();
            searchInput.focus();
        }});
    </script>
</body>
</html>
"""

            # Guardar archivo
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)

            self.set_status(f"✅ Informe HTML generado: {os.path.basename(path)}")
            self._avisar("Éxito", f"Informe HTML generado para {empresa_texto}:\n\n{path}")

        except Exception as e:
            self._avisar("Error", f"No se pudo generar el informe HTML:\n\n{e}", QMessageBox.Critical)

    def copy_selection(self):
        """Copia selección al portapapeles en formato legible."""
        current_widget = self.view_stack.currentWidget()
        selected_items = current_widget.selectedItems()

        if not selected_items:
            self._avisar("Copiar", "No hay elementos seleccionados.")
            return

        # Crear texto formateado con estructura jerárquica
        lines = []
        lines.append("=" * 80)
        lines.append("PERMISOS DE RECURSOS COMPARTIDOS")
        lines.append("=" * 80)
        lines.append("")

        processed = set()  # Para evitar duplicados

        for item in selected_items:
            if item in processed:
                continue

            # Si es un elemento raíz (recurso o carpeta)
            if item.parent() is None:
                lines.append(f"📂 {item.text(0)}")
                if item.childCount() > 0:
                    # Recorrer hijos (usuarios/permisos)
                    for i in range(item.childCount()):
                        child = item.child(i)
                        user = child.text(0)
                        perm = child.text(1)
                        tipo = child.text(2) if child.text(2) else ""
                        origen = child.text(3) if child.text(3) else ""
                        lines.append(f"  └─ {user}")
                        lines.append(f"     Permiso: {perm}")
                        if tipo:
                            lines.append(f"     Tipo: {tipo}")
                        if origen:
                            lines.append(f"     Origen: {origen}")
                        lines.append("")
                        processed.add(child)
                lines.append("")
            # Si es un elemento hijo (usuario/permiso)
            else:
                parent = item.parent()
                if parent and parent not in processed:
                    lines.append(f"📂 {parent.text(0)}")
                    processed.add(parent)

                user = item.text(0)
                perm = item.text(1)
                tipo = item.text(2) if item.text(2) else ""
                origen = item.text(3) if item.text(3) else ""
                lines.append(f"  └─ {user}")
                lines.append(f"     Permiso: {perm}")
                if tipo:
                    lines.append(f"     Tipo: {tipo}")
                if origen:
                    lines.append(f"     Origen: {origen}")
                lines.append("")

            processed.add(item)

        lines.append("=" * 80)
        lines.append(f"Total elementos copiados: {len(selected_items)}")
        lines.append("=" * 80)

        QApplication.clipboard().setText("\n".join(lines))
        self.set_status(f"✅ Copiados {len(selected_items)} elementos")

    def abrir_busqueda_global(self):
        """Abre el diálogo de búsqueda global en todos los datos cargados."""
        dlg = QDialog(self)
        dlg.setWindowTitle("🔎 Búsqueda Global")
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dlg.setMinimumSize(900, 560)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)

        # Barra de búsqueda
        search_row = QHBoxLayout()
        txt = QLineEdit()
        txt.setPlaceholderText("Escribe el término a buscar en todos los datos cargados...")
        txt.setFixedHeight(34)
        search_row.addWidget(txt, 1)
        btn_search = QPushButton("🔍 Buscar")
        btn_search.setObjectName("primary")
        btn_search.setFixedWidth(110)
        search_row.addWidget(btn_search)
        layout.addLayout(search_row)

        lbl_info = QLabel("Resultados: —")
        lbl_info.setObjectName("mutedInfoLabel")
        layout.addWidget(lbl_info)

        from PyQt5.QtWidgets import QTableWidget, QTableWidgetItem
        tabla = QTableWidget(0, 4)
        tabla.setHorizontalHeaderLabels(["Pestaña", "Columna", "Valor encontrado", "Fila completa"])
        tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        tabla.setSelectionBehavior(QTableWidget.SelectRows)
        tabla.setAlternatingRowColors(True)
        _setup_header(tabla, 4, stretch_col=3)
        layout.addWidget(tabla)

        # Navegar a la sección al hacer doble clic
        def _navigate(row, _col):
            tab_name = tabla.item(row, 0).text() if tabla.item(row, 0) else ""
            if tab_name in self.tab_widgets:
                self.seleccionar_seccion(tab_name)
            dlg.close()

        tabla.doubleClicked.connect(lambda idx: _navigate(idx.row(), idx.column()))

        # Mapa de modelos: (tab_name, proxy_or_model, headers). 5.4.0: sale de la lista única
        # _tabla_secciones_exportables (antes una lista propia con encabezados escritos a mano que
        # ya no coincidían, «network_adapter_proxy» que no existe —Redes nunca se buscaba— y sin
        # Dispositivos, Impresoras, Inicio, Variables, Discos, Red local...). Encabezados reales.
        def _collect_sources():
            sources = []
            for titulo, proxy, hdrs in self._tabla_secciones_exportables():
                if proxy is None or not hasattr(proxy, 'rowCount'):
                    continue
                sources.append((_SECCION_DE_TITULO.get(titulo, titulo), proxy,
                                self._encabezados_reales(proxy, hdrs)))
            return sources

        def _do_search():
            term = txt.text().strip()
            if len(term) < 2:
                lbl_info.setText("Escribe al menos 2 caracteres.")
                return
            tabla.setRowCount(0)
            term_lower = term.lower()
            total = 0
            sources = _collect_sources()
            for tab_name, proxy, hdrs in sources:
                try:
                    nrows = proxy.rowCount()
                    ncols = proxy.columnCount()
                except Exception:
                    continue
                for r in range(nrows):
                    row_vals = []
                    matched_cols = []
                    for c in range(ncols):
                        try:
                            val = str(proxy.data(proxy.index(r, c), Qt.DisplayRole) or "")
                        except Exception:
                            val = ""
                        row_vals.append(val)
                        if term_lower in val.lower():
                            matched_cols.append((c, val))
                    if matched_cols:
                        for col_idx, col_val in matched_cols:
                            hdr = hdrs[col_idx] if col_idx < len(hdrs) else f"Col {col_idx}"
                            fila_resume = " | ".join(row_vals[:6])
                            tr = tabla.rowCount()
                            tabla.insertRow(tr)
                            tabla.setItem(tr, 0, QTableWidgetItem(tab_name))
                            tabla.setItem(tr, 1, QTableWidgetItem(hdr))
                            tabla.setItem(tr, 2, QTableWidgetItem(col_val))
                            tabla.setItem(tr, 3, QTableWidgetItem(fila_resume))
                            total += 1
                            if total >= 500:
                                break
                    if total >= 500:
                        break
                if total >= 500:
                    break
            lbl_info.setText(
                f"{'⚠️ Mostrando primeros 500 — refina la búsqueda' if total >= 500 else f'{total} resultado(s) encontrado(s)'}"
                f"  —  doble clic para ir a la pestaña"
            )

        btn_search.clicked.connect(_do_search)
        txt.returnPressed.connect(_do_search)

        btn_close = QPushButton("Cerrar")
        btn_close.setFixedWidth(100)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)
        btn_close.clicked.connect(dlg.close)

        dlg.exec_()

    def _calcular_hallazgos_seguridad(self):
        """Analiza los datos ya cargados en busca de configuraciones
        potencialmente peligrosas. Devuelve una lista de tuplas
        (nivel, categoria, descripcion, detalle), nivel en {🔴,⚠️,ℹ️}.

        Único punto de verdad para los hallazgos de seguridad: lo usan tanto
        el diálogo "Análisis de Seguridad Básico" como el resumen ejecutivo
        de los informes HTML/PDF/Excel, para que todos muestren siempre lo
        mismo."""
        findings = []

        def _add(nivel, categoria, descripcion, detalle=""):
            findings.append((nivel, categoria, descripcion, detalle))

        def _activo(estado) -> bool:
            """Compara el campo Estado con igualdad exacta, NO con 'in' por
            subcadena: 'activo' in 'inactivo' da True en Python, así que una
            comprobación por subcadena confunde cuentas/dispositivos inactivos
            con activos (bug detectado: usuarios deshabilitados se marcaban
            como administradores activos o sin contraseña requerida)."""
            return (estado or "").strip().lower() == "activo"

        # ── Procesos ──────────────────────────────────────────
        if hasattr(self, 'processes_model'):
            for row in self.processes_model.rows():
                ruta = (row.exe_path or "").lower()
                if ruta and any(p in ruta for p in ["\\temp\\", "\\tmp\\", "\\appdata\\local\\temp\\"]):
                    _add("🔴", "Procesos", f"Proceso ejecutándose desde carpeta temporal",
                         f"{row.name} (PID {row.pid}) — {row.exe_path}")

        # ── Servicios ─────────────────────────────────────────
        if hasattr(self, 'services_model'):
            for row in self.services_model.rows():
                # 5.4.0: nombres exactos de servicios de seguridad. Antes cualquier nombre con
                # «update» contaba (edgeupdate, gupdate... se paran solos y es lo normal).
                if row.startup_type in ("auto", "auto_delayed") and row.status == "stopped":
                    if row.name.lower() in _SERVICIOS_SEGURIDAD:
                        _add("⚠️", "Servicios", "Servicio de seguridad detenido con inicio automático",
                             f"{row.name} ({row.display_name}) — {STARTUP_LABELS.get(row.startup_type, row.startup_type)}")

        # ── Firewall ──────────────────────────────────────────
        if hasattr(self, 'firewall_model'):
            # FirewallRuleRow: nombre, direccion, accion, protocolo, puerto, perfil.
            # es_regla_amplia reutiliza la misma lógica ya validada que usa la
            # pestaña de Firewall y el Dashboard, en vez de reinventar un
            # criterio de "demasiadas reglas" que no distinguía reglas
            # inofensivas de reglas realmente peligrosas (Any/Any).
            for row in self.firewall_model.rows():
                if row.es_regla_amplia:
                    _add("🔴", "Firewall", "Regla de entrada demasiado amplia (Any/Any)",
                         f"Regla: {row.nombre} — Perfil: {row.perfil} — "
                         "permite cualquier puerto/protocolo entrante.")

            sensitive_ports = {"3389", "22", "23", "445", "135", "5900", "5985", "5986"}
            for row in self.firewall_model.rows():
                # 5.4.0: una regla DESHABILITADA no expone nada (antes contaba igual)
                if (row.habilitada or "").strip().lower() in ("no", "false"):
                    continue
                if ("entrada" in (row.direccion or "").lower()
                        and "permitir" in (row.accion or "").lower()):
                    for p in (row.puerto or "").replace(" ", "").split(","):
                        if p in sensitive_ports:
                            _add("🔴", "Firewall", f"Puerto sensible expuesto por regla de firewall: {p}",
                                 f"Regla: {row.nombre} — Perfil: {row.perfil}")

        # ── Carpetas compartidas ────────────────────────────────
        if hasattr(self, 'model'):
            # SharePermissionRow.es_riesgo_everyone_escritura: mismo criterio
            # ya usado en la pestaña de Carpetas Compartidas y el Dashboard.
            for row in self.model.rows():
                if getattr(row, 'es_riesgo_everyone_escritura', False):
                    _add("🔴", "Carpetas Compartidas", "Permiso de escritura para 'Todos/Everyone'",
                         f"{row.recurso} ({row.ruta}) — {row.identidad}: {row.permiso}")

        # ── Tareas programadas ──────────────────────────────────
        if hasattr(self, 'tasks_model'):
            for row in self.tasks_model.rows():
                if getattr(row, 'es_sospechosa', False):
                    motivo = "oculta" if row.oculta else \
                        f"se ejecuta como {row.ejecuta_como or 'SYSTEM'} sin autor identificado"
                    _add("🔴", "Tareas Programadas", f"Tarea programada sospechosa ({motivo})",
                         f"{row.nombre} — Último resultado: {row.ultimo_resultado}")

        # ── Software instalado ──────────────────────────────────
        if hasattr(self, 'software_model'):
            blacklist = _get_software_blacklist()
            for row in self.software_model.rows():
                if _is_blacklisted_software(row.nombre, blacklist):
                    _add("🔴", "Software", "Software no autorizado instalado (lista negra)",
                         f"{row.nombre} {row.version}")
                elif _is_eol_software(row.nombre):
                    _add("⚠️", "Software", "Software sin soporte del fabricante (fin de vida)",
                         f"{row.nombre} {row.version}")

        # ── Conexiones de red ─────────────────────────────────
        if hasattr(self, 'connections_model'):
            # NetworkConnectionRow: protocolo, direccion_local, direccion_remota, estado, proceso, pid
            sensitive_ports = {"3389", "22", "23", "5900", "4444", "31337", "1080"}
            for row in self.connections_model.rows():
                # direccion_local puede ser "IP:puerto" o "0.0.0.0:3389"
                dir_local = (row.direccion_local or "")
                puerto_local = dir_local.rsplit(":", 1)[-1].strip() if ":" in dir_local else ""
                if puerto_local in sensitive_ports and "escucha" in (row.estado or "").lower():
                    _add("⚠️", "Conexiones", f"Puerto sensible en escucha: {puerto_local}",
                         f"Proto: {row.protocolo} — Local: {row.direccion_local} — Proceso: {row.proceso}")

        # ── Usuarios ──────────────────────────────────────────
        if hasattr(self, 'users_model'):
            # UserRow: nombre, nombre_completo, tipo, dominio, estado, ultima_conexion,
            #          fecha_creacion, descripcion, grupos, sid, password_expires, password_required
            admins_activos = []
            for row in self.users_model.rows():
                nombre_lower = (row.nombre or "").lower()
                grupos_lower = (row.grupos or "").lower()
                es_admin = any(g in grupos_lower for g in ["administrador", "administrators", "admins"])
                activo = _activo(row.estado)

                if es_admin and activo and nombre_lower not in ("administrador", "administrator"):
                    admins_activos.append(row)
                    _add("⚠️", "Usuarios", "Usuario activo con privilegios de administrador",
                         f"{row.nombre} — Grupos: {row.grupos}")

                # PasswordRequired="No" indica que la POLÍTICA de la cuenta
                # permite una contraseña en blanco; no significa que la
                # cuenta carezca ahora mismo de contraseña. Se avisa con ese
                # matiz para no dar a entender algo que no se puede verificar
                # desde aquí (habría que comprobarlo con 'net user <nombre>').
                if activo and (row.password_required or "").strip().lower() == "no":
                    _add("🔴", "Usuarios", "Cuenta activa que permite contraseña en blanco",
                         f"{row.nombre} — Tipo: {row.tipo}. La política de la cuenta no exige contraseña "
                         "(no implica que esté en blanco ahora mismo; revísalo con 'net user').")

                if nombre_lower in ("invitado", "guest") and activo:
                    _add("🔴", "Usuarios", "Cuenta de invitado (Guest) habilitada",
                         f"{row.nombre} — debería estar deshabilitada salvo necesidad explícita.")

            if len(admins_activos) > 3:
                _add("⚠️", "Usuarios", f"{len(admins_activos)} cuentas activas con privilegios de administrador",
                     "; ".join(r.nombre for r in admins_activos[:8]))

        # ── Credenciales ──────────────────────────────────────
        if hasattr(self, 'creds_model'):
            creds = self.creds_model.rows()
            if len(creds) > 0:
                _add("ℹ️", "Credenciales", f"{len(creds)} credenciales almacenadas en Windows Credential Manager",
                     "Revisa que no haya credenciales obsoletas o de servicios desconocidos.")

        # ── Conexiones remotas recientes ──────────────────────
        if hasattr(self, 'remote_model'):
            fallos = [r for r in self.remote_model.rows() if "fallo" in (r.resultado or "").lower()]
            if len(fallos) >= 10:
                _add("🔴", "Acceso Remoto", f"{len(fallos)} intentos de acceso remoto fallidos (últimos 30 días)",
                     "Posible intento de fuerza bruta. Revisa IPs de origen.")
            ips_unicas = set(r.ip_origen for r in fallos if r.ip_origen and r.ip_origen not in ("-", "", "::1", "127.0.0.1"))
            if len(ips_unicas) >= 5:
                _add("🔴", "Acceso Remoto", f"Intentos fallidos desde {len(ips_unicas)} IPs distintas",
                     ", ".join(list(ips_unicas)[:10]))

        # ── Escritorio remoto (RDP) ─────────────────────────────
        if getattr(self, '_last_system_info', None):
            rdp_info = self._last_system_info.get('EscritorioRemoto', {}) or {}
            if rdp_info.get('Habilitado') == 'Sí' and 'INACTIVO' in (rdp_info.get('NLA') or ''):
                _add("🔴", "Escritorio Remoto", "RDP habilitado sin NLA (autenticación a nivel de red)",
                     f"Puerto: {rdp_info.get('Puerto', 'N/A')}")

        # ── Estado de seguridad del sistema ──────────────────────
        # (pestaña "Seguridad": Defender, BitLocker, Secure Boot — hay que
        # haberla actualizado antes para que estos datos existan)
        if getattr(self, '_last_seguridad_info', None):
            sec = self._last_seguridad_info
            defender = sec.get('Defender', {}) or {}
            otros_av = [a.get('Nombre', '') for a in (sec.get('Antivirus') or [])
                        if isinstance(a, dict) and a.get('Nombre')
                        and not re.search(r'windows defender|microsoft defender', a.get('Nombre', ''), re.I)]
            if otros_av:
                # Con otro antivirus registrado, Defender apagado es lo normal (Windows lo apaga solo)
                if defender.get('Habilitado') == 'No':
                    _add("ℹ️", "Antivirus", "Windows Defender desactivado: el equipo usa otro antivirus",
                         ", ".join(otros_av))
            elif defender.get('Habilitado') == 'No':
                _add("🔴", "Antivirus", "Windows Defender deshabilitado y ningún otro antivirus registrado", "")
            elif 'INACTIVA' in (defender.get('ProteccionTiempoReal') or ''):
                _add("🔴", "Antivirus", "Protección en tiempo real de Windows Defender inactiva", "")

            perfiles_off = [n for n, e in (sec.get('Firewall') or {}).items() if 'INACTIVO' in str(e).upper()]
            if perfiles_off:
                _add("🔴", "Firewall", "Firewall de Windows desactivado en algún perfil", ", ".join(perfiles_off))

            end = sec.get('Endurecimiento') or {}
            if 'INACTIVO' in str(end.get('UAC', '')):
                _add("🔴", "Configuración", "Control de cuentas de usuario (UAC) desactivado", "")
            if 'HABILITADO' in str(end.get('SMBv1', '')):
                _add("🔴", "Configuración", "SMBv1 habilitado (vector de WannaCry)", "")
            # (la cuenta de Invitado ya la avisa el bloque de Usuarios)
            if 'HABILITADA' in str(end.get('CuentaAdmin', '')):
                _add("⚠️", "Usuarios", "Cuenta de Administrador integrada habilitada", str(end.get('CuentaAdmin', '')))

            hosts = sec.get('ArchivoHosts') or {}
            if hosts.get('Sospechoso'):
                entradas = hosts.get('EntradasAnadidas') or []
                _add("⚠️", "Archivo hosts", f"{len(entradas)} entrada(s) añadida(s) al archivo hosts",
                     "; ".join(str(e.get("value", e)) if isinstance(e, dict) else str(e) for e in entradas[:5]))

            pirata = sec.get('SoftwarePirata') or {}
            if pirata.get('TieneIndicios'):
                ind = pirata.get('Indicios') or []
                _add("⚠️", "Licencias", f"{len(ind)} indicio(s) de activadores o software sin licencia",
                     "; ".join(str(i) for i in ind[:3]))

            # 5.4.2: un solo hallazgo con todas las unidades (antes uno por unidad: 7 en el equipo de pruebas)
            sin_cifrar = [str(vol.get('Unidad', '?')) for vol in (sec.get('BitLocker') or [])
                          if isinstance(vol, dict) and ('Off' in (vol.get('Estado') or '')
                                                        or 'Unprotected' in (vol.get('Estado') or ''))]
            if sin_cifrar:
                _add("⚠️", "Cifrado de disco", f"{len(sin_cifrar)} unidad(es) sin cifrar con BitLocker",
                     ", ".join(sin_cifrar))

            if (sec.get('SecureBoot') or '') == 'Deshabilitado':
                _add("ℹ️", "Arranque seguro", "Secure Boot deshabilitado", "")

        # ── Controladores ─────────────────────────────────────
        if hasattr(self, 'drivers_model'):
            # DriverRow: dispositivo, fabricante, version, fecha, clase, firmado, estado, proveedor
            # 5.4.0: comparación exacta (antes «Desconocido» contaba como «no» firmado)
            unsigned = [r for r in self.drivers_model.rows()
                        if (r.firmado or "").strip().lower() == "no" or "sin firmar" in (r.estado or "").lower()]
            if unsigned:
                _add("⚠️", "Controladores", f"{len(unsigned)} controladores sin firma digital",
                     "; ".join(r.dispositivo for r in unsigned[:5]))

        # ── Historial PowerShell ──────────────────────────────
        if hasattr(self, 'ps_history_model'):
            dangerous_cmds = ["invoke-expression", "iex ", "downloadstring", "bypass", "encodedcommand",
                              "-enc ", "invoke-webrequest", "start-bitstransfer", "net user ", "net localgroup"]
            # 5.4.0: un solo hallazgo con el total (antes uno por comando: 30 filas en el equipo de
            # pruebas) y sin el código interno que PowerShell genera para sus módulos ($__cmdletization...).
            sensibles = []
            for row in self.ps_history_model.rows():
                cmd_lower = (row.comando or "").lower()
                if "$__cmdletization" in cmd_lower:
                    continue
                if any(d in cmd_lower for d in dangerous_cmds):
                    sensibles.append(row.comando or "")
            if sensibles:
                _add("⚠️", "Historial PS", f"{len(sensibles)} comando(s) potencialmente sensible(s) en el historial",
                     " · ".join(c[:80] for c in sensibles[:3]))

        # ── Actualizaciones críticas pendientes ───────────────
        if hasattr(self, 'pending_updates_model'):
            criticas = [r for r in self.pending_updates_model.rows() if 'crít' in (r.criticidad or '').lower()]
            if criticas:
                _add("⚠️", "Actualizaciones", f"{len(criticas)} actualización(es) crítica(s) pendiente(s)",
                     "; ".join(r.titulo or r.kb for r in criticas[:3]))

        return findings

    def abrir_analisis_seguridad(self):
        """Ejecuta el análisis de seguridad y muestra el resultado en un diálogo."""
        findings = self._calcular_hallazgos_seguridad()

        # ── Resultado ─────────────────────────────────────────
        dlg = QDialog(self)
        dlg.setWindowTitle("🛡️ Análisis de Seguridad Básico")
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dlg.setMinimumSize(950, 580)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)

        if not findings:
            txt_ok = QLabel("✅ No se detectaron problemas de seguridad en los datos cargados.\n\n"
                            "Nota: Este análisis sólo evalúa los datos ya cargados en las pestañas. "
                            "Usa 🔄 Sincronizar Todo para cargar todos los datos antes de analizar.")
            txt_ok.setObjectName("successNoticeLabel")
            txt_ok.setWordWrap(True)
            layout.addWidget(txt_ok)
        else:
            lbl_resumen = QLabel(
                f"Se encontraron {len(findings)} hallazgo(s)   —   "
                f"🔴 Críticos: {sum(1 for f in findings if f[0]=='🔴')}   "
                f"⚠️ Advertencias: {sum(1 for f in findings if f[0]=='⚠️')}   "
                f"ℹ️ Info: {sum(1 for f in findings if f[0]=='ℹ️')}"
            )
            lbl_resumen.setObjectName("dialogSummaryLabel")
            layout.addWidget(lbl_resumen)

            from PyQt5.QtWidgets import QTableWidget, QTableWidgetItem
            tabla = QTableWidget(len(findings), 4)
            tabla.setHorizontalHeaderLabels(["Nivel", "Categoría", "Descripción", "Detalle"])
            tabla.setEditTriggers(QTableWidget.NoEditTriggers)
            tabla.setSelectionBehavior(QTableWidget.SelectRows)
            tabla.setAlternatingRowColors(True)
            tabla.verticalHeader().setVisible(False)
            _setup_header(tabla, 4, stretch_col=3)

            for r, (nivel, cat, desc, det) in enumerate(findings):
                tabla.setItem(r, 0, QTableWidgetItem(nivel))
                tabla.setItem(r, 1, QTableWidgetItem(cat))
                tabla.setItem(r, 2, QTableWidgetItem(desc))
                tabla.setItem(r, 3, QTableWidgetItem(det))
                if nivel == "🔴":
                    for c in range(4):
                        item = tabla.item(r, c)
                        if item:
                            item.setBackground(QColor("#f8d7da"))
                            item.setForeground(QColor("#58151c"))
                elif nivel == "⚠️":
                    for c in range(4):
                        item = tabla.item(r, c)
                        if item:
                            item.setBackground(QColor("#fff3cd"))
                            item.setForeground(QColor("#664d03"))

            layout.addWidget(tabla)

            note = QLabel("ℹ️ Este análisis evalúa los datos actualmente cargados. "
                          "Usa 🔄 Sincronizar Todo para cargar todos los módulos primero.")
            note.setStyleSheet("color:#6c757d; font-size:9pt;")
            note.setWordWrap(True)
            layout.addWidget(note)

        btn_close = QPushButton("Cerrar")
        btn_close.setFixedWidth(100)
        row_btns = QHBoxLayout()

        btn_export_seg = QPushButton("📄 Exportar CSV")
        btn_export_seg.setFixedWidth(150)
        btn_export_seg.setEnabled(bool(findings))

        def _export_seg():
            path = dialogo_guardar_archivo(dlg, "Guardar análisis",
                ruta_guardado_predeterminada(f"Seguridad_{time.strftime('%Y%m%d_%H%M%S')}.csv"), "CSV (*.csv)")
            if not path: return
            try:
                with open(path, "w", newline="", encoding="utf-8-sig") as f:
                    writer = _SafeCsvWriter(csv.writer(f, delimiter=";"))
                    writer.writerow(["Nivel","Categoría","Descripción","Detalle"])
                    writer.writerows(findings)
                QMessageBox.information(dlg, "Exportado", f"Guardado en:\n{path}")
            except Exception as e:
                QMessageBox.critical(dlg, "Error", str(e))

        btn_export_seg.clicked.connect(_export_seg)

        btn_informe_seg = QPushButton("📕 Generar Informe")
        btn_informe_seg.setFixedWidth(170)
        btn_informe_seg.setToolTip("Genera un informe PDF profesional del análisis de seguridad")

        def _generar_informe_seg():
            import socket

            path = dialogo_guardar_archivo(dlg, "Guardar informe PDF",
                ruta_guardado_predeterminada(f"InformeSeguridad_{time.strftime('%Y%m%d_%H%M%S')}.pdf"),
                "PDF (*.pdf)")
            if not path:
                return
            try:
                hostname = getattr(self, "_nombre_equipo", socket.gethostname)()   # 5.2.0: el del cliente en modo remoto
                ts = time.strftime('%d/%m/%Y %H:%M:%S')
                nivel_texto = {"🔴": "Crítico", "⚠️": "Advertencia", "ℹ️": "Información"}
                nivel_color = {"🔴": ("#f8d7da", "#58151c"), "⚠️": ("#fff3cd", "#664d03")}

                n_crit = sum(1 for f in findings if f[0] == '🔴')
                n_warn = sum(1 for f in findings if f[0] == '⚠️')
                n_info = sum(1 for f in findings if f[0] == 'ℹ️')

                html = f"""<html><head><meta charset="UTF-8">
<style>
    body {{ font-family: Arial, sans-serif; font-size: 9pt; color:#2c2c2c; }}
    h1 {{ color:#2c3e50; font-size:16pt; margin-bottom:2px; }}
    .meta {{ color:#6c757d; font-size:8.5pt; margin-bottom:14px; }}
    .resumen {{ margin-bottom:14px; font-size:9pt; }}
    table {{ border-collapse: collapse; width:100%; margin-bottom:10px; }}
    th {{ background:#2c3e50; color:white; padding:5px 6px; text-align:left; font-size:8pt; }}
    td {{ padding:4px 6px; border-bottom:1px solid #ddd; font-size:8pt; vertical-align:top; }}
</style></head><body>
<h1>🛡️ Informe de Seguridad Básico — MASys</h1>
<div class="meta">Equipo: <b>{html_escape(hostname)}</b> &nbsp;|&nbsp; Generado: {ts}</div>
"""
                if not findings:
                    html += ('<p style="color:#155724;background:#d4edda;padding:10px;border-radius:4px;">'
                             '✅ No se detectaron problemas de seguridad en los datos cargados '
                             'en el momento del análisis.</p>')
                else:
                    html += (f'<div class="resumen">Se encontraron <b>{len(findings)}</b> hallazgo(s) '
                             f'&nbsp;—&nbsp; 🔴 Críticos: <b>{n_crit}</b> '
                             f'&nbsp; ⚠️ Advertencias: <b>{n_warn}</b> '
                             f'&nbsp; ℹ️ Info: <b>{n_info}</b></div>')
                    html += "<table><tr><th>Nivel</th><th>Categoría</th><th>Descripción</th><th>Detalle</th></tr>"
                    for nivel, cat, desc, det in findings:
                        bg, fg = nivel_color.get(nivel, ("#ffffff", "#2c2c2c"))
                        etiqueta = f"{nivel} {nivel_texto.get(nivel, '')}"
                        html += (f'<tr style="background:{bg};color:{fg};">'
                                 f"<td>{html_escape(etiqueta)}</td>"
                                 f"<td>{html_escape(cat)}</td>"
                                 f"<td>{html_escape(desc)}</td>"
                                 f"<td>{html_escape(det)}</td></tr>")
                    html += "</table>"
                html += ('<p style="color:#6c757d;font-size:8pt;margin-top:10px;">'
                         'Este análisis evalúa únicamente los datos cargados en las pestañas en el momento de '
                         'ejecutar el análisis. Generado automáticamente por MASys.</p>')
                html += "</body></html>"

                render_html_a_pdf(html, path)

                self.set_status(f"✅ Informe de seguridad PDF generado: {os.path.basename(path)}")
                resp = QMessageBox.information(dlg, "Informe generado",
                    f"Informe PDF guardado en:\n{path}",
                    QMessageBox.Ok | QMessageBox.Open, QMessageBox.Ok)
                if resp == QMessageBox.Open:
                    import webbrowser
                    webbrowser.open(path)
            except Exception as e:
                logger.exception(f"Error generando informe PDF de seguridad: {e}")
                QMessageBox.critical(dlg, "Error", f"No se pudo generar el informe PDF:\n\n{e}")

        btn_informe_seg.clicked.connect(_generar_informe_seg)

        row_btns.addWidget(btn_export_seg)
        row_btns.addWidget(btn_informe_seg)
        row_btns.addStretch()
        row_btns.addWidget(btn_close)
        layout.addLayout(row_btns)
        btn_close.clicked.connect(dlg.close)

        dlg.exec_()

    def buscar_todo(self):
        """Ejecuta todas las operaciones de refresh llamando directamente a los métodos."""
        if getattr(self, "_remoto", None) is not None:
            # 5.2.0: leería ESTE equipo y guardaría una visita mezclando datos del cliente y de este PC
            self.set_status("🔒 Modo equipo remoto: «Sincronizar Todo» no está disponible. Usa «↩ Volver a este "
                            "equipo».")
            return
        progress_dialog = QDialog(self)
        progress_dialog.setWindowTitle("Sincronizando toda la información...")
        progress_dialog.setWindowFlags(progress_dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        progress_dialog.setModal(True)
        progress_dialog.setFixedSize(500, 200)

        dialog_layout = QVBoxLayout(progress_dialog)
        dialog_layout.setSpacing(15)
        dialog_layout.setContentsMargins(20, 20, 20, 20)

        title_label = QLabel("🔄 Sincronizando toda la información del sistema")
        title_label.setFont(QFont("Segoe UI", 11, QFont.Bold))
        title_label.setAlignment(Qt.AlignCenter)
        dialog_layout.addWidget(title_label)

        status_label = QLabel("Iniciando...")
        status_label.setAlignment(Qt.AlignCenter)
        dialog_layout.addWidget(status_label)

        progress_bar_dialog = QProgressBar()
        progress_bar_dialog.setMinimum(0)
        progress_bar_dialog.setMaximum(100)
        progress_bar_dialog.setValue(0)
        dialog_layout.addWidget(progress_bar_dialog)

        btn_cancel = QPushButton("Cancelar")
        btn_cancel.setFixedWidth(100)
        btn_cancel_layout = QHBoxLayout()
        btn_cancel_layout.addStretch()
        btn_cancel_layout.addWidget(btn_cancel)
        btn_cancel_layout.addStretch()
        dialog_layout.addLayout(btn_cancel_layout)

        refresh_methods = [
            ("Sistema",               "refresh_system_info"),
            ("Unidades remotas",      "refresh_network_drives"),
            ("Credenciales",          "refresh_credentials"),
            ("WiFi",                  "refresh_wifi"),
            ("Certificados",          "refresh_certificates"),
            ("Variables de entorno",  "refresh_environment_vars"),
            ("Procesos",              "refresh_processes"),
            ("Servicios",             "refresh_services"),
            ("Conexiones de red",     "refresh_network_connections"),
            ("Software instalado",    "refresh_installed_software"),
            ("Tareas programadas",    "refresh_scheduled_tasks"),
            ("Impresoras",            "refresh_printers"),
            ("Reglas de firewall",    "refresh_firewall_rules"),
            ("Cuentas de correo",     "refresh_email_accounts"),
            ("Usuarios",              "refresh_users"),
            ("Conexiones remotas",    "refresh_remote_connections"),
            ("Sesiones activas",      "refresh_sesiones_activas"),
            ("Bluetooth",             "refresh_bluetooth"),
            ("Historial PS",          "refresh_ps_history"),
            ("Eventos sistema",       "refresh_eventos_sistema"),
            ("Dispositivos USB",      "refresh_usb_devices"),
            ("Monitores",             "refresh_monitors"),
            ("Actualizaciones",       "refresh_windows_updates"),
            ("Controladores",         "refresh_drivers"),
            ("Active Directory",      "refresh_ad_info"),
            ("Políticas de Grupo",    "refresh_politicas_grupo"),
            ("Programas de Inicio",   "refresh_programas_inicio"),
            ("Adaptadores de red",    "refresh_network_adapters"),
            ("Seguridad",             "refresh_seguridad"),
            ("Discos duros",          "refresh_salud_disco"),
            ("Licencias",             "refresh_licencias"),
            ("Gestor de paquetes",    "refresh_gestor_paquetes"),
            ("Red local",             "refresh_red_local"),
        ]
        # Fuera a propósito (5.4.0, medido): Carpetas Compartidas (hay que elegir ruta y modo; puede tardar
        # horas), Registro de Windows y Contenido (son búsquedas con un texto o una ubicación), Herramientas
        # de red (necesitan un destino) e Inventario de migración (≈1 min y solo sirve al migrar).

        def _worker_libre():
            return self.worker is None or not self.worker.isRunning()

        def _tarea_libre(dueno, atributo):
            def _f():
                t = getattr(dueno, atributo, None)
                return t is None or not t.isRunning()
            return _f

        operations = [(name, getattr(self, method), _worker_libre)
                      for name, method in refresh_methods if hasattr(self, method)]

        # 5.4.0: secciones que antes NO se cargaban y que tardan poco (medido en el equipo de pruebas):
        # Navegadores y Nube 2 s, SQL Server <1 s, Características 7 s, Estabilidad 2 s, Extensiones 9 s,
        # Iconos 3 s. Las nuevas usan su propia tarea (no self.worker): se espera a esa tarea.
        # (Van con lambda a propósito: tests/test_agente.py cuenta las tuplas («…», "refresh_…") de arriba,
        # que son las que el agente sabe leer en el equipo del cliente.)
        extras = []
        if hasattr(self, 'refresh_navegadores_nube'):
            extras.append(("Navegadores y Nube", lambda: self.refresh_navegadores_nube(), _worker_libre))
        if hasattr(self, 'refresh_sql_server'):
            extras.append(("SQL Server", lambda: self.refresh_sql_server(), _worker_libre))
        if hasattr(self, 'refresh_caracteristicas'):
            extras.append(("Características de Windows", lambda: self.refresh_caracteristicas(),
                           _tarea_libre(self, "_carac_tarea")))
        if hasattr(self, '_estab_cargar'):
            extras.append(("Estabilidad", lambda: self._estab_cargar(), _tarea_libre(self, "_estab_tarea")))
        for lista in (getattr(self, 'asoc_ext', None), getattr(self, 'asoc_proto', None)):
            if lista is not None and hasattr(lista, 'actualizar'):
                extras.append(("Extensiones y programas", lista.actualizar, _tarea_libre(lista, "_tarea")))
        if hasattr(self, 'iconos_tabs'):
            for i in range(self.iconos_tabs.count()):
                pestana = self.iconos_tabs.widget(i)
                if pestana is getattr(self, 'iconos_bib', None) or not hasattr(pestana, 'actualizar'):
                    continue
                extras.append((f"Iconos · {self.iconos_tabs.tabText(i)}", pestana.actualizar,
                               _tarea_libre(pestana, "_tarea")))
        operations += extras

        cancelled = [False]
        terminado = [False]

        def cancel_operation():
            if terminado[0] or cancelled[0]:
                return
            cancelled[0] = True
            self.set_status("⏹️ «Sincronizar Todo» cancelado: lo que estaba en marcha termina y no se lanza nada más")
            progress_dialog.close()

        btn_cancel.clicked.connect(cancel_operation)
        # 5.4.0: cerrar con la X o con Esc también cancela (antes la sincronización seguía escondida
        # y se podían lanzar otras cargas a la vez, que se saltaban en silencio).
        progress_dialog.rejected.connect(cancel_operation)

        current_op = [0]

        def execute_next_operation():
            if cancelled[0]:
                return

            if current_op[0] >= len(operations):
                terminado[0] = True
                progress_bar_dialog.setValue(100)
                status_label.setText("✅ Sincronización completada")
                QApplication.processEvents()
                QTimer.singleShot(500, progress_dialog.close)
                if hasattr(self, '_refresh_dashboard'):
                    QTimer.singleShot(600, self._refresh_dashboard)
                # Guardar la visita y compararla con la anterior de este equipo (panel del Dashboard)
                if hasattr(self, '_visita_registrar'):
                    QTimer.singleShot(700, self._visita_registrar)
                return

            op_name, lanzar, _listo = operations[current_op[0]]
            status_label.setText(f"Sincronizando: {op_name}...")
            progress_value = int((current_op[0] / len(operations)) * 100)
            progress_bar_dialog.setValue(progress_value)
            QApplication.processEvents()

            try:
                lanzar()
                QApplication.processEvents()
            except Exception as e:
                logger.error(f"Error en operación '{op_name}': {e}")

            current_op[0] += 1
            _esperar_worker_y_continuar()

        def _esperar_worker_y_continuar(intentos_restantes=4000):
            """Espera a que termine lo que lanzó la operación anterior (self.worker o la tarea propia de
            la sección) antes de lanzar la siguiente: cada refresh_* abandona en silencio si ve
            self.worker todavía corriendo. Límite de ~20 min solo como red de seguridad ante algo colgado;
            el botón Cancelar (o cerrar la ventana) para toda la secuencia.
            5.4.0: también se usa ANTES de la primera operación: si al pulsar ya había otra carga en
            marcha, «Sistema» se saltaba sin avisar."""
            if cancelled[0]:
                return
            anterior = operations[current_op[0] - 1][2] if current_op[0] > 0 else _worker_libre
            if not anterior() or not _worker_libre():
                if current_op[0] == 0:
                    status_label.setText("Esperando a que termine la carga que ya estaba en marcha...")
                if intentos_restantes > 0:
                    QTimer.singleShot(300, lambda: _esperar_worker_y_continuar(intentos_restantes - 1))
                else:
                    logger.error("Sincronizar Todo: una operación no terminó tras ~20 min de espera; "
                                 "se omite para no bloquear el resto de la sincronización.")
                    QTimer.singleShot(200, execute_next_operation)
            else:
                QTimer.singleShot(200, execute_next_operation)

        progress_dialog.show()
        QTimer.singleShot(100, _esperar_worker_y_continuar)

    def _tabla_secciones_exportables(self):
        """(nombre_sección, proxy, headers) de cada sección con una tabla
        exportable. nombre_sección coincide exactamente con las claves de
        self.tab_widgets / self._current_section. Único punto de verdad
        para el informe consolidado y para la exportación adaptada a la
        sección activa (evita mantener dos listas que puedan desincronizarse)."""
        return [
            ("⚙️ Procesos", getattr(self, 'processes_proxy', None), ProcessTableModel.HEADERS),
            ("🛠️ Servicios", getattr(self, 'services_proxy', None), ServiceTableModel.HEADERS),
            ("🌐 Conexiones Red", getattr(self, 'connections_proxy', None), ["Protocolo","Dir. Local","Dir. Remota","Estado","Proceso","PID"]),
            ("📦 Software Instalado", getattr(self, 'software_proxy', None), ["Nombre","Versión","Editor","Fecha","Carpeta"]),
            ("👥 Usuarios", getattr(self, 'users_proxy', None), ["Nombre","Nombre Completo","Tipo","Estado","Último Login","Grupos"]),
            ("🔑 Credenciales", getattr(self, 'creds_proxy', None), CredentialTableModel.HEADERS),
            ("📶 WiFi", getattr(self, 'wifi_proxy', None), ["SSID","Contraseña","Autenticación","Cifrado"]),
            ("🔐 Certificados", getattr(self, 'certs_proxy', None), CertificateTableModel.HEADERS),
            ("🔥 Firewall", getattr(self, 'firewall_proxy', None), ["Nombre","Dirección","Acción","Protocolo","Puerto","Perfil"]),
            ("🔒 Conexiones Remotas", getattr(self, 'remote_proxy', None), RemoteConnectionTableModel.HEADERS),
            ("⚠️ Eventos Sistema", getattr(self, 'eventos_proxy', None), ["Fecha","Nivel","Log","Fuente","ID","Mensaje"]),
            ("👤 Sesiones Activas", getattr(self, 'sesiones_proxy', None), ["Usuario","Sesión","ID","Estado","Inactividad","Inicio","Tipo"]),
            ("🔵 Bluetooth", getattr(self, 'bluetooth_proxy', None), ["Nombre","Fabricante","Estado","Clase","Instance ID"]),
            ("📜 Historial PS", getattr(self, 'ps_history_proxy', None), ["#","Comando"]),
            ("🔌 Dispositivos USB", getattr(self, 'usb_proxy', None), ["Nombre","DeviceID","Fabricante","Estado","Clase"]),
            ("🎮 Gráficas", getattr(self, 'gpu_proxy', None), GPUTableModel.HEADERS),
            ("🖥️ Monitores", getattr(self, 'monitors_proxy', None), MonitorTableModel.HEADERS),
            ("🖥️ Adm. Dispositivos", getattr(self, 'drivers_proxy', None), DriverTableModel.HEADERS),
            ("⏰ Tareas Programadas", getattr(self, 'tasks_proxy', None), ["Nombre","Estado","Próxima Ejecución","Última","Resultado"]),
            ("🖨️ Impresoras", getattr(self, 'printers_proxy', None), ["Nombre","Tipo","Puerto","Estado","Compartida","Predeterminada"]),
            ("📧 Cuentas Correo", getattr(self, 'email_proxy', None), ["Cliente","Cuenta","Servidor","Tipo"]),
            ("🌐 Navegadores", getattr(self, 'navegadores_proxy', None), BrowserCloudAccountTableModel.HEADERS),
            ("☁️ Nube", getattr(self, 'nube_proxy', None), BrowserCloudAccountTableModel.HEADERS),
            ("🌐 Variables Entorno", getattr(self, 'env_vars_proxy', None), EnvVarTableModel.HEADERS),
            ("🌐 Redes", getattr(self, 'network_proxy', None), NetworkAdapterTableModel.HEADERS),
            ("🏢 Active Directory", getattr(self, 'ad_proxy', None), ADInfoTableModel.HEADERS),
            ("🏛️ Políticas de Grupo", getattr(self, 'gp_proxy', None), GroupPolicyTableModel.HEADERS),
            ("🚀 Programas de Inicio", getattr(self, 'startup_proxy', None), StartupProgramTableModel.HEADERS),
            ("📦 Gestor de Paquetes", getattr(self, 'paquetes_proxy', None), PackageUpgradeTableModel.HEADERS),
            ("💾 Unidades Remotas", getattr(self, 'drives_proxy', None), NetworkDriveTableModel.HEADERS),
            ("📁 Contenido", getattr(self, 'content_proxy', None), ContentTableModel.HEADERS),
            ("🔍 Red Local", getattr(self, 'red_local_proxy', None), NetworkDeviceTableModel.HEADERS),
            ("📂 Carpetas Compartidas", getattr(self, 'proxy', None), ShareTableModel.HEADERS),
            ("✅ Actualizaciones Instaladas", getattr(self, 'installed_updates_proxy', None), InstalledUpdateTableModel.HEADERS),
            ("⏳ Actualizaciones Pendientes", getattr(self, 'pending_updates_proxy', None), PendingUpdateTableModel.HEADERS),
            ("🗃️ Registro de Windows", getattr(self, 'reg_proxy', None), RegistryResultTableModel.HEADERS),
            ("🗄️ SQL Server", getattr(self, 'sql_proxy', None), SqlInstanceTableModel.HEADERS),
            # 5.4.0: Discos Duros no salía en el informe completo aunque estuviera cargado
            ("💿 Discos", getattr(self, 'disk_inventory_proxy', None), DiscoFisicoTableModel.HEADERS),
            ("📁 Particiones", getattr(self, 'partitions_proxy', None), PartitionTableModel.HEADERS),
        ]

    def _tabla_secciones_arbol(self):
        """(nombre_sección, atributo_QTreeWidget) de las secciones que usan
        un árbol de 2 columnas (Componente/Valor) en vez de una tabla plana.
        Se aplanan a filas (Categoría, Componente, Valor) igual que ya hacía
        export_system_csv, para poder alimentar los mismos exportadores
        HTML/PDF/CSV/Excel de la barra adaptativa."""
        return [
            ("💻 Sistema", "system_tree"),
            ("🛡️ Seguridad", "seguridad_tree"),
            ("🔑 Licencias", "licencias_tree"),
        ]

    def _tabla_secciones_subtabs(self):
        """Secciones con sub-pestañas (QTabWidget) cuyo nombre nunca coincide
        con self._current_section: mapea el nombre de la sección padre al
        widget de sub-pestañas y a la lista de (titulo, proxy, headers) de
        cada sub-pestaña EN EL MISMO ORDEN que fueron añadidas con addTab,
        para poder resolver por currentIndex() cuál está visible."""
        from msys.ui.permisos import T_ACCESO, T_ANALISIS, T_COPIAS, T_PERMISOS
        return {
            # 4.4.0: Carpetas Compartidas tiene pestañas (Recursos compartidos + las de permisos)
            "📂 Carpetas Compartidas": ("compartidas_tabs", [
                ("📂 Carpetas Compartidas", getattr(self, 'proxy', None), ShareTableModel.HEADERS),
                (T_PERMISOS, getattr(self, 'perm_plano_proxy', None), getattr(getattr(self, 'perm_plano', None), 'HEADERS', [])),
                (T_ANALISIS, getattr(self, 'perm_an_proxy', None), getattr(getattr(self, 'perm_an_modelo', None), 'HEADERS', [])),
                (T_ACCESO, getattr(self, 'perm_ac_proxy', None), getattr(getattr(self, 'perm_ac_modelo', None), 'HEADERS', [])),
                (T_COPIAS, getattr(self, 'perm_cmp_proxy', None), getattr(getattr(self, 'perm_cmp_modelo', None), 'HEADERS', [])),
            ]),
            # "🖥️ Gráficas y Monitores" ya no vive aquí: Gráficas y
            # Monitores se muestran las dos a la vez en un splitter, sin
            # sub-pestañas (mismo motivo que Discos Duros/Actualizaciones)
            # — ver _obtener_datos_multiseccion_actual.
        }

    @staticmethod
    def _encabezados_reales(modelo, respaldo):
        """Encabezados tal y como los muestra la tabla. 5.4.0: antes cada sección
        llevaba una lista fija escrita a mano que ya no coincidía con las columnas
        (Usuarios exportaba el Dominio bajo «Estado», Historial PS la fecha bajo
        «Comando», USB el fabricante bajo «DeviceID»...). Solo si el modelo no da
        encabezados se usa la lista de respaldo."""
        try:
            n = modelo.columnCount()
            reales = [str(modelo.headerData(c, Qt.Horizontal, Qt.DisplayRole) or "") for c in range(n)]
        except Exception:
            return list(respaldo)
        return reales if n and any(reales) else list(respaldo)

    @staticmethod
    def _filas_desde_proxy(proxy, headers, limite=5000, todas=False):
        """(headers, rows_data) desde un proxy con rowCount/data, o None si
        el proxy no existe o no tiene filas todavía. Columnas y encabezados
        salen de la propia tabla. todas=True lee el modelo de origen (todas
        las filas cargadas, sin los filtros de la vista): lo usa el informe
        completo, que antes dejaba fuera lo oculto por los filtros (Software
        87 de 234, Redes 3 de 20...)."""
        if not hasattr(proxy, 'rowCount'):
            return None
        modelo = proxy
        if todas and hasattr(proxy, 'sourceModel') and proxy.sourceModel() is not None:
            modelo = proxy.sourceModel()
        n = modelo.rowCount()
        if n == 0:
            return None
        headers = ReportingMixin._encabezados_reales(modelo, headers)
        get = lambda r, c: str(modelo.data(modelo.index(r, c), Qt.DisplayRole) or "")
        tope = n if limite is None else min(n, limite)
        rows_data = [[get(r, c) for c in range(len(headers))] for r in range(tope)]
        return headers, rows_data

    @staticmethod
    def _filas_desde_arbol(tree):
        """(headers, rows_data) aplanando un QTreeWidget de 2 columnas
        (Componente/Valor) en filas (Categoría, Componente, Valor), o None
        si el árbol todavía no tiene datos. Mismo criterio que
        export_system_csv (hasta nietos, con sangría de 2 espacios)."""
        if tree is None or tree.topLevelItemCount() == 0:
            return None
        headers = ["Categoría", "Componente", "Valor"]
        rows_data = []
        for i in range(tree.topLevelItemCount()):
            top_item = tree.topLevelItem(i)
            categoria = top_item.text(0)
            rows_data.append([categoria, '', top_item.text(1)])
            for j in range(top_item.childCount()):
                child = top_item.child(j)
                rows_data.append([categoria, child.text(0), child.text(1)])
                for k in range(child.childCount()):
                    grandchild = child.child(k)
                    rows_data.append([categoria, f"  {grandchild.text(0)}", grandchild.text(1)])
        return headers, rows_data

    @staticmethod
    def _filas_por_categoria_desde_arbol(tree):
        """Como _filas_desde_arbol, pero SIN aplanar en una tabla única: cada
        categoría de primer nivel del árbol (💻 Equipo, 🪟 Sistema Operativo,
        ⚙️ Procesador, 💾 Memoria RAM...) se devuelve como su propia sección
        (título, headers, filas) — para que los informes las muestren
        separadas y con apariencia clara, no todas mezcladas en una tabla
        Categoría/Componente/Valor. Usado por Sistema, cuyas categorías son
        lo bastante distintas (Equipo, SO, CPU, RAM...) como para merecer
        secciones propias en vez de una tabla plana."""
        if tree is None or tree.topLevelItemCount() == 0:
            return []
        headers = ["Componente", "Valor"]
        secciones = []
        for i in range(tree.topLevelItemCount()):
            top_item = tree.topLevelItem(i)
            rows_data = []
            if top_item.text(1):
                rows_data.append(["Resumen", top_item.text(1)])
            for j in range(top_item.childCount()):
                child = top_item.child(j)
                rows_data.append([child.text(0), child.text(1)])
                for k in range(child.childCount()):
                    grandchild = child.child(k)
                    rows_data.append([f"  {grandchild.text(0)}", grandchild.text(1)])
            if rows_data:
                secciones.append((top_item.text(0), headers, rows_data))
        return secciones

    def _collect_all_sections_data(self, limite: int = 20000):
        """Recoge todas las secciones con datos cargados (proxy + headers +
        filas) para generar informes consolidados (HTML completo, PDF y
        Excel). Incluye también las secciones basadas en árbol (Sistema,
        Seguridad, Copias de Seguridad, Licencias) — antes solo se recogían
        las tablas planas y esas cuatro quedaban fuera del informe
        consolidado. Sistema se reparte en una sección por categoría en vez
        de una única tabla Categoría/Componente/Valor."""
        sections = []
        for titulo, proxy, headers in self._tabla_secciones_exportables():
            try:
                # Todas las filas cargadas (sin los filtros de la vista), con tope alto de seguridad
                resultado = self._filas_desde_proxy(proxy, headers, limite=limite + 1, todas=True)
                if resultado is not None and len(resultado[1]) > limite:
                    # 5.4.2: tope solo para el PDF (pensado para imprimir): se avisa en la propia tabla
                    filas_tope = resultado[1][:limite]
                    filas_tope.append([f"… hay más filas: el informe HTML y el Excel las llevan todas"]
                                      + [""] * (len(resultado[0]) - 1))
                    resultado = (resultado[0], filas_tope)
                if resultado is None:
                    continue
                headers, rows_data = resultado
                sections.append((titulo, headers, rows_data))
            except Exception:
                pass
        for titulo, tree_attr in self._tabla_secciones_arbol():
            try:
                tree = getattr(self, tree_attr, None)
                if titulo == "💻 Sistema":
                    sections.extend(self._filas_por_categoria_desde_arbol(tree))
                else:
                    resultado = self._filas_desde_arbol(tree)
                    if resultado is None:
                        continue
                    headers, rows_data = resultado
                    sections.append((titulo, headers, rows_data))
            except Exception:
                pass
        for nombre, proveedor in getattr(self, '_proveedores_export', {}).items():
            try:
                sections.extend(s for s in (proveedor() or []) if s and s[2])
            except Exception:
                logger.exception(f"Informe completo: «{nombre}»")
        return sections

    def registrar_export(self, nombre_seccion: str, proveedor):
        """Para los apartados nuevos: proveedor() -> [(titulo, headers, filas)]."""
        if not hasattr(self, '_proveedores_export'):
            self._proveedores_export = {}
        self._proveedores_export[nombre_seccion] = proveedor

    def registrar_html(self, nombre_seccion: str, generador):
        """Sección con informe HTML propio: el botón HTML de la barra inferior llama a generador() en vez de la tabla genérica."""
        if not hasattr(self, '_html_propio'):
            self._html_propio = {}
        self._html_propio[nombre_seccion] = generador

    def _obtener_datos_seccion_actual(self):
        """(titulo, headers, rows_data) de la sección actualmente
        seleccionada (self._current_section), o None si esa sección no
        tiene todavía datos exportables cargados. Cubre tablas planas
        (proxy), secciones basadas en árbol (Sistema/Seguridad/Copias de
        Seguridad/Licencias) y secciones con sub-pestañas (Discos Duros/
        Gráficas y Monitores/Actualizaciones), resolviendo estas últimas
        según la sub-pestaña que esté visible en ese momento."""
        nombre = getattr(self, '_current_section', None)
        if not nombre:
            return None

        info_subtabs = self._tabla_secciones_subtabs().get(nombre)
        if info_subtabs:
            subtabs_attr, entradas = info_subtabs
            subtabs_widget = getattr(self, subtabs_attr, None)
            if subtabs_widget is None:
                return None
            idx = subtabs_widget.currentIndex()
            if idx < 0 or idx >= len(entradas):
                return None
            sub_titulo, proxy, headers = entradas[idx]
            try:
                # Sin el tope de 5000 filas: los permisos de un servidor pasan de eso
                resultado = self._filas_desde_proxy(proxy, headers, limite=10 ** 7)
            except Exception:
                return None
            if resultado is None:
                return None
            headers, rows_data = resultado
            return sub_titulo, headers, rows_data

        for titulo, proxy, headers in self._tabla_secciones_exportables():
            if titulo != nombre:
                continue
            try:
                resultado = self._filas_desde_proxy(proxy, headers)
            except Exception:
                return None
            if resultado is None:
                return None
            headers, rows_data = resultado
            return titulo, headers, rows_data

        for titulo, tree_attr in self._tabla_secciones_arbol():
            if titulo != nombre:
                continue
            try:
                resultado = self._filas_desde_arbol(getattr(self, tree_attr, None))
            except Exception:
                return None
            if resultado is None:
                return None
            headers, rows_data = resultado
            return titulo, headers, rows_data

        return None

    def _obtener_datos_multiseccion_actual(self):
        """Como _obtener_datos_seccion_actual, pero como LISTA de secciones:
        para Sistema devuelve una por categoría (Equipo, Sistema Operativo,
        Procesador, Memoria RAM, Placa Base, BIOS/Firmware...); para
        Actualizaciones devuelve Instaladas y Pendientes por separado (las
        dos se muestran a la vez en la pestaña, sin sub-pestañas, así que no
        hay un "índice actual" que resolver); para el resto de secciones
        devuelve una lista de un único elemento con los mismos datos que ya
        daba _obtener_datos_seccion_actual. None (o lista vacía) si la
        sección activa no tiene datos exportables. Es lo que usan los
        botones adaptativos Informe HTML/PDF/Guardar Resultados para decidir
        entre el informe de una sola tabla (el de siempre) o el informe con
        navegación por categorías (Sistema, Actualizaciones)."""
        nombre = getattr(self, '_current_section', None)
        # Apartados nuevos (Iconos, Extensiones, Características...): cada uno
        # registra en self._proveedores_export una función que devuelve sus
        # tablas [(titulo, headers, filas)] — así no hay que tocar este archivo
        # por cada sección nueva.
        proveedor = getattr(self, '_proveedores_export', {}).get(nombre)
        if proveedor is not None:
            try:
                secciones = [s for s in (proveedor() or []) if s and s[2]]
            except Exception:
                logger.exception(f"Exportación de «{nombre}»")
                return None
            # 4.9.0: una sección de siempre con pestañas nuevas (📧 Cuentas Correo) devuelve [] en su pestaña
            # de siempre y sigue con su tabla de siempre (abajo); las demás secciones nuevas no tienen tabla abajo.
            if secciones:
                return secciones
        if nombre == "💻 Sistema":
            secciones = self._filas_por_categoria_desde_arbol(getattr(self, 'system_tree', None))
            return secciones or None
        if nombre == "🔄 Actualizaciones":
            # No usa _filas_desde_proxy: esa devuelve None tanto si la
            # sección nunca se cargó como si se cargó y dio 0 filas (p. ej.
            # "0 actualizaciones pendientes", un resultado real y válido) —
            # con eso, Pendientes desaparecía sin más del informe en cuanto
            # estaba vacía de verdad, en vez de aparecer como "0 registros".
            # Antes de la primera actualización sí se sigue ocultando del
            # todo (_updates_cargadas), igual que el resto de secciones.
            if not getattr(self, '_updates_cargadas', False):
                return None
            secciones = []
            for titulo, proxy, headers in (
                ("✅ Actualizaciones Instaladas", getattr(self, 'installed_updates_proxy', None), InstalledUpdateTableModel.HEADERS),
                ("⏳ Actualizaciones Pendientes", getattr(self, 'pending_updates_proxy', None), PendingUpdateTableModel.HEADERS),
            ):
                if proxy is None or not hasattr(proxy, 'rowCount'):
                    continue
                n = proxy.rowCount()
                rows_data = [[str(proxy.data(proxy.index(r, c), Qt.DisplayRole) or "")
                              for c in range(len(headers))] for r in range(n)]
                secciones.append((titulo, headers, rows_data))
            return secciones or None
        if nombre == "💽 Discos Duros":
            # Discos (inventario+salud fusionados) y Particiones se
            # muestran las dos a la vez en la misma ventana (sin
            # sub-pestañas), igual que Actualizaciones.
            secciones = []
            for titulo, proxy, headers in (
                ("💿 Discos", getattr(self, 'disk_inventory_proxy', None), DiscoFisicoTableModel.HEADERS),
                ("📁 Particiones", getattr(self, 'partitions_proxy', None), PartitionTableModel.HEADERS),
            ):
                if proxy is None or not hasattr(proxy, 'rowCount'):
                    continue
                n = proxy.rowCount()
                rows_data = [[str(proxy.data(proxy.index(r, c), Qt.DisplayRole) or "")
                              for c in range(len(headers))] for r in range(n)]
                secciones.append((titulo, headers, rows_data))
            return secciones or None
        if nombre == "🖥️ Gráficas y Monitores":
            # Gráficas y Monitores se muestran las dos a la vez en un
            # splitter, sin sub-pestañas — mismo criterio que Discos Duros.
            secciones = []
            for titulo, proxy, headers in (
                ("🎮 Gráficas", getattr(self, 'gpu_proxy', None), GPUTableModel.HEADERS),
                ("🖥️ Monitores", getattr(self, 'monitors_proxy', None), MonitorTableModel.HEADERS),
            ):
                if proxy is None or not hasattr(proxy, 'rowCount'):
                    continue
                n = proxy.rowCount()
                rows_data = [[str(proxy.data(proxy.index(r, c), Qt.DisplayRole) or "")
                              for c in range(len(headers))] for r in range(n)]
                secciones.append((titulo, headers, rows_data))
            return secciones or None
        datos = self._obtener_datos_seccion_actual()
        return [datos] if datos is not None else None

    @staticmethod
    def _nombre_archivo_seccion(titulo: str) -> str:
        """Convierte el título de una sección ('⚙️ Procesos') en un nombre de
        archivo seguro ('Procesos'), sin emoji ni caracteres prohibidos."""
        import re
        nombre = re.sub(r'[^\w\s-]', '', titulo, flags=re.UNICODE).strip()
        nombre = re.sub(r'\s+', '_', nombre)
        return nombre or "Seccion"

    def _actualizar_barra_exportacion(self):
        """Ajusta la barra inferior según la sección activa: en Dashboard se
        mantienen los 5 botones actuales (Seguridad/Informe HTML/PDF/
        Snapshots/Guardar Resultado) operando sobre toda la app; en el
        resto se muestran sólo 4 (Informe HTML/PDF/CSV/Guardar Resultados),
        deshabilitados si la sección no tiene todavía una tabla exportable."""
        if not hasattr(self, 'btn_export_csv_actual'):
            return
        # Se actualiza aquí (no en un sitio aparte) para aprovechar las ~140
        # conexiones ya existentes de worker.finished a este método: el
        # botón Soporte se refresca solo con los datos recién cargados,
        # sin tener que tocar cada refresh_x() de cada sección.
        self._actualizar_boton_soporte()
        es_dashboard = getattr(self, '_current_section', None) == "📊 Dashboard"

        self.btn_seguridad.setVisible(es_dashboard)
        self.btn_snapshots.setVisible(es_dashboard)
        self.btn_export_csv_actual.setVisible(not es_dashboard)
        self.lbl_auto.setVisible(es_dashboard)
        self.combo_autorefresh.setVisible(es_dashboard)

        if es_dashboard:
            self.btn_export_html_full.setEnabled(True)
            self.btn_export_pdf_full.setEnabled(True)
            self.btn_guardar_resultados.setEnabled(True)
            self.btn_export_html_full.setToolTip("Exportar todos los datos cargados a un informe HTML completo")
            self.btn_export_pdf_full.setToolTip("Exportar todos los datos cargados a un informe PDF")
            self.btn_guardar_resultados.setToolTip("Exportar todos los datos cargados a Excel")
            return

        secciones = self._obtener_datos_multiseccion_actual()
        hay_datos = bool(secciones)
        nombre = getattr(self, '_current_section', '') or "esta sección"
        self.btn_export_html_full.setEnabled(hay_datos)
        self.btn_export_pdf_full.setEnabled(hay_datos)
        self.btn_export_csv_actual.setEnabled(hay_datos)
        self.btn_guardar_resultados.setEnabled(hay_datos)
        tooltip = (f"Exportar los datos de «{nombre}»" if hay_datos
                   else f"«{nombre}» no tiene datos cargados todavía (o no admite exportación adaptada aún)")
        for btn in (self.btn_export_html_full, self.btn_export_pdf_full,
                    self.btn_export_csv_actual, self.btn_guardar_resultados):
            btn.setToolTip(tooltip)

    def _exportar_html_adaptativo(self):
        if getattr(self, '_current_section', None) == "📊 Dashboard":
            self.exportar_informe_html_completo()
            return
        propio = getattr(self, '_html_propio', {}).get(getattr(self, '_current_section', None))
        if propio is not None:
            propio()
            return
        secciones = self._obtener_datos_multiseccion_actual()
        if not secciones:
            self._avisar("Sin datos", f"No hay datos cargados en «{self._current_section}» para exportar.\n"
                "Actualiza la sección primero.")
            return
        if len(secciones) == 1:
            titulo, headers, rows_data = secciones[0]
            self._exportar_html_seccion(titulo, headers, rows_data)
        else:
            self._exportar_html_multiseccion(self._current_section, secciones)

    def _exportar_pdf_adaptativo(self):
        if getattr(self, '_current_section', None) == "📊 Dashboard":
            self.exportar_informe_pdf()
            return
        secciones = self._obtener_datos_multiseccion_actual()
        if not secciones:
            self._avisar("Sin datos", f"No hay datos cargados en «{self._current_section}» para exportar.\n"
                "Actualiza la sección primero.")
            return
        if len(secciones) == 1:
            titulo, headers, rows_data = secciones[0]
            self._exportar_pdf_seccion(titulo, headers, rows_data)
        else:
            self._exportar_pdf_multiseccion(self._current_section, secciones)

    def _exportar_csv_adaptativo(self):
        secciones = self._obtener_datos_multiseccion_actual()
        if not secciones:
            self._avisar("Sin datos", f"No hay datos cargados en «{self._current_section}» para exportar.\n"
                "Actualiza la sección primero.")
            return
        if len(secciones) == 1:
            titulo, headers, rows_data = secciones[0]
            self._export_rows_csv(headers, rows_data, self._nombre_archivo_seccion(titulo))
        else:
            self._exportar_csv_multiseccion(self._current_section, secciones)

    def _exportar_csv_multiseccion(self, titulo_pagina, sections):
        """Exporta varias secciones a un único CSV, cada una precedida por
        una fila de título — para el botón adaptativo 'CSV' cuando la
        sección activa se reparte en varias categorías (Sistema,
        Actualizaciones), reutilizando _SafeCsvWriter."""
        prefijo = self._nombre_archivo_seccion(titulo_pagina)
        path = dialogo_guardar_archivo(self, "Guardar CSV",
            ruta_guardado_predeterminada(f"{prefijo}_{time.strftime('%Y%m%d_%H%M%S')}.csv"),
            "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = _SafeCsvWriter(csv.writer(f, delimiter=";"))
                for i, (titulo, headers, rows_data) in enumerate(sections):
                    if i > 0:
                        writer.writerow([])
                    writer.writerow([f"Sección: {titulo}"])
                    writer.writerow(headers)
                    for row in rows_data:
                        writer.writerow(row)
            self.set_status(f"✅ Exportado a {os.path.basename(path)}")
            self._avisar("Exportar", f"Datos exportados correctamente a:\n\n{path}")
        except Exception as e:
            logger.exception(f"Error exportando CSV: {e}")
            self._avisar("Error", f"No se pudo exportar a CSV:\n\n{e}", QMessageBox.Critical)

    def _guardar_resultados_adaptativo(self):
        if getattr(self, '_current_section', None) == "📊 Dashboard":
            self.guardar_resultados_excel()
            return
        secciones = self._obtener_datos_multiseccion_actual()
        if not secciones:
            self._avisar("Sin datos", f"No hay datos cargados en «{self._current_section}» para exportar.\n"
                "Actualiza la sección primero.")
            return
        if len(secciones) == 1:
            titulo, headers, rows_data = secciones[0]
            self._guardar_excel_seccion(titulo, headers, rows_data)
        else:
            self._guardar_excel_multiseccion(self._current_section, secciones)

    def _celda_nombre_con_nota_html(self, titulo: str, nombre_celda: str) -> str:
        """Contenido de la celda Nombre para HTML cuando lleva el icono 📝:
        el icono lleva el texto completo de la nota en el atributo title,
        así que el propio navegador muestra el tooltip nativo con la nota
        al pasar el ratón por encima — sin popup ni JavaScript."""
        nombre_limpio = nombre_celda.replace(" 📝", "").strip()
        nota = self._nota_para_fila_export(titulo, nombre_limpio)
        if not nota:
            return html_escape(nombre_celda)
        return (f'{html_escape(nombre_limpio)} '
                f'<span class="nota-icon" title="{html_escape(nota)}">📝</span>')

    def _nota_para_fila_export(self, titulo: str, nombre_celda: str) -> str:
        """Texto completo de la nota para una fila de Procesos/Servicios/
        Variables Entorno en los informes exportados (el icono 📝 de la
        columna Nombre solo avisa de que existe, no lleva el texto). '' si
        la sección no lleva notas o esa fila no tiene nota."""
        if titulo == "⚙️ Procesos":
            notas = getattr(self, 'process_notes', None)
        elif titulo == "🛠️ Servicios":
            notas = getattr(self, 'service_notes', None)
        elif titulo == "🌐 Variables Entorno":
            notas = getattr(self, 'env_var_notes', None)
        else:
            return ""
        if not notas:
            return ""
        nombre_limpio = nombre_celda.replace(" 📝", "").strip().lower()
        for nombre, texto in notas.items():
            if nombre.lower() == nombre_limpio:
                return texto
        return ""

    def _fila_nota_pdf(self, titulo: str, nombre_celda: str, num_columnas: int) -> str:
        """Fila extra de tabla PDF con el texto completo de la nota,
        insertada justo debajo de la fila de Procesos/Servicios/Variables
        Entorno que la tiene — el PDF es estático, no admite popups, así
        que la nota va pegada a su fila en vez de en un icono clickable.
        '' si esa fila no tiene nota."""
        nota = self._nota_para_fila_export(titulo, nombre_celda)
        if not nota:
            return ""
        return (f'<tr><td colspan="{num_columnas}" '
                f'style="background:#f4f7fb;font-style:italic;color:#5b6472;">'
                f'📝 Nota: {html_escape(nota)}</td></tr>')

    def _adjuntar_nota_excel(self, celda, titulo: str, texto_celda: str) -> None:
        """Si la celda de Nombre lleva el icono 📝, le adjunta una Nota real
        de Excel (el mecanismo nativo: triángulo rojo en la esquina, texto
        visible al pasar el ratón o pulsarlo) — el equivalente en Excel al
        popup de HTML, sin añadir una columna aparte."""
        if " 📝" not in texto_celda:
            return
        nota = self._nota_para_fila_export(titulo, texto_celda)
        if not nota:
            return
        from openpyxl.comments import Comment
        comentario = Comment(nota, "MASys")
        comentario.width = 260
        comentario.height = 120
        celda.comment = comentario

    @staticmethod
    def _nivel_class_celda(val: str) -> str:
        """Clasifica el texto de una celda en un nivel visual para badges e
        informes (crit/err/warn/ok/''). Único punto de verdad reutilizado por
        HTML, PDF y el resaltado de filas.

        Importante: 'inactivo'/'deshabilitado' se comprueban ANTES que
        'activo', porque 'activo' in 'inactivo' es True por subcadena y
        pintaría en verde ('ok') algo que en realidad es lo contrario."""
        v = (val or "").lower()
        if 'critico' in v or 'crítico' in v: return 'crit'
        if 'error' in v: return 'err'
        if 'advertencia' in v or 'fallo' in v: return 'warn'
        if 'inactivo' in v or 'deshabilitado' in v: return ''
        if 'ok' in v or 'éxito' in v or 'exito' in v or 'activo' in v: return 'ok'
        return ''

    @staticmethod
    def _nivel_hallazgo_a_clase(nivel: str) -> str:
        """Traduce el emoji de nivel de _calcular_hallazgos_seguridad() a la
        misma clase CSS (crit/warn/ok) usada en el resto del informe."""
        return {"🔴": "crit", "⚠️": "warn", "ℹ️": "ok"}.get(nivel, "")

    def _resumen_hallazgos_seguridad_html(self, findings) -> str:
        """Construye el bloque de 'Resumen ejecutivo de seguridad' (tarjetas +
        tabla de hallazgos) reutilizado por el informe HTML y el PDF."""
        n_crit = sum(1 for f in findings if f[0] == '🔴')
        n_warn = sum(1 for f in findings if f[0] == '⚠️')
        n_info = sum(1 for f in findings if f[0] == 'ℹ️')

        if not findings:
            return ("""<div class="alert alert-success">
  <strong>✅ Sin hallazgos de seguridad.</strong> No se detectaron configuraciones
  potencialmente peligrosas en los datos actualmente cargados.
  <div class="alert-note">Este resumen sólo evalúa los módulos ya sincronizados en MASys
  en el momento de generar el informe; usa 🔄 Sincronizar Todo antes de generarlo para una
  cobertura completa.</div>
</div>""")

        interpretacion = (
            f"Se detectaron <strong>{len(findings)}</strong> hallazgo(s): "
            f"<strong>{n_crit} crítico(s)</strong> que conviene resolver de forma prioritaria, "
            f"{n_warn} advertencia(s) a revisar y {n_info} de carácter informativo. "
            "El detalle completo está más abajo, agrupado por categoría."
        ) if n_crit else (
            f"Se detectaron <strong>{len(findings)}</strong> hallazgo(s), ninguno crítico: "
            f"{n_warn} advertencia(s) y {n_info} de carácter informativo. Revísalos cuando puedas."
        )

        filas = ""
        for nivel, cat, desc, det in findings:
            cls = self._nivel_hallazgo_a_clase(nivel)
            filas += (f'<tr class="finding-row {cls}">'
                      f"<td>{nivel}</td><td>{html_escape(cat)}</td>"
                      f"<td>{html_escape(desc)}</td><td>{html_escape(det)}</td></tr>\n")

        return f"""<div class="alert {'alert-danger' if n_crit else 'alert-warning'}">
  <strong>{'🔴' if n_crit else '⚠️'} {len(findings)} hallazgo(s) de seguridad detectado(s).</strong>
  <div class="alert-note">{interpretacion}</div>
</div>
<div class="table-wrap">
<table class="findings-table">
  <thead><tr><th>Nivel</th><th>Categoría</th><th>Descripción</th><th>Detalle</th></tr></thead>
  <tbody>{filas}</tbody>
</table>
</div>"""

    @staticmethod
    def _css_informe_corporativo(con_nav: bool = True) -> str:
        """CSS corporativo compartido por los informes HTML (el consolidado
        y el de una sola sección): misma paleta, tarjetas KPI, tablas con
        badges de nivel y barra de búsqueda. Único punto de verdad para no
        mantener dos hojas de estilo que puedan desincronizarse.
        con_nav=False quita la barra lateral (no hace falta con 1 sección
        sola) y pone los márgenes a 0 en vez de a los 280px que reserva."""
        margin = "280px" if con_nav else "0"
        nav_css = """
nav{position:fixed;top:0;left:0;bottom:0;width:280px;background:var(--primary);
     overflow-y:auto;padding:20px 0;z-index:200}
nav .nav-title{color:#fff;font-size:16px;font-weight:600;padding:0 20px 4px}
nav .nav-subtitle{color:#9fb3c8;font-size:11px;padding:0 20px 16px;border-bottom:1px solid rgba(255,255,255,.12);margin-bottom:8px}
nav ul{list-style:none}
nav a{display:flex;justify-content:space-between;align-items:center;text-decoration:none;
       color:#dce6f0;font-size:12.5px;padding:8px 20px;border-left:3px solid transparent}
nav a:hover, nav a.active{background:rgba(255,255,255,.08);border-left-color:var(--accent)}
nav .badge{background:rgba(255,255,255,.15);color:#fff;border-radius:10px;padding:1px 7px;font-size:10px}
""" if con_nav else ""

        return f"""
:root{{
  --primary:#1a3a5c; --primary-light:#2c5f8a; --accent:#2563eb;
  --bg:#f5f7fa; --card-bg:#ffffff; --text:#1a1a2e; --text-light:#5b6472;
  --border:#dde3ea;
  --success:#16a34a; --success-bg:#e8f8ee;
  --warning:#ca8a04; --warning-bg:#fff8e6;
  --danger:#dc2626; --danger-bg:#fdf2f2;
}}
*{{margin:0;padding:0;box-sizing:border-box}}
/* Este informe está diseñado sólo para tema claro: le decimos al navegador
   que no lo re-coloree con su "modo oscuro forzado" (p. ej. Edge/Chrome con
   "Forzar tema oscuro en el contenido web"), que si no invierte colores de
   forma inconsistente y deja tarjetas oscuras con texto casi ilegible. */
:root{{color-scheme:light}}
html{{scroll-behavior:smooth;scroll-padding-top:90px}}
body{{font-family:'Segoe UI',Arial,sans-serif;background:var(--bg);color:var(--text)}}
header{{background:linear-gradient(135deg,var(--primary),#0f2439);color:#fff;padding:30px 40px;margin-left:{margin}}}
header h1{{font-size:24px;font-weight:300;margin-bottom:8px}}
header .meta{{font-size:12px;opacity:.85}}
header .meta strong{{opacity:1}}
{nav_css}
.toolbar{{position:sticky;top:0;background:var(--card-bg);border-bottom:1px solid var(--border);
          padding:12px 40px;margin-left:{margin};display:flex;gap:10px;align-items:center;
          flex-wrap:wrap;z-index:100;box-shadow:0 2px 6px rgba(0,0,0,.04)}}
.toolbar input[type=text]{{flex:1;min-width:220px;padding:9px 14px;border:1px solid var(--border);
          border-radius:6px;font-size:13px;outline:none}}
.toolbar input[type=text]:focus{{border-color:var(--accent)}}
.chip{{border:1px solid var(--border);background:#fff;color:var(--text-light);border-radius:16px;
       padding:6px 14px;font-size:12px;cursor:pointer;user-select:none;white-space:nowrap}}
.chip:hover{{background:#f1f4f8}}
.chip.active{{background:var(--primary);color:#fff;border-color:var(--primary)}}
.toolbar .result-count{{font-size:12px;color:var(--text-light);white-space:nowrap}}

main{{margin-left:{margin};max-width:1500px;padding:24px 40px}}

.kpi-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:14px;margin-bottom:24px}}
.kpi-card{{background:var(--card-bg);border-radius:10px;padding:16px 18px;box-shadow:0 2px 8px rgba(0,0,0,.06);
           border-top:4px solid var(--accent)}}
.kpi-card.crit{{border-top-color:var(--danger);background:var(--danger-bg)}}
.kpi-card.warn{{border-top-color:var(--warning)}}
.kpi-card.ok{{border-top-color:var(--success)}}
.kpi-value{{font-size:26px;font-weight:600;color:var(--primary)}}
.kpi-card.crit .kpi-value{{color:#1a1a1a}}
.kpi-label{{font-size:11.5px;color:var(--text-light);margin-top:2px}}
.kpi-card.crit .kpi-label{{color:#5a2323}}

h2.exec-title{{font-size:16px;color:var(--primary);border-bottom:2px solid var(--primary);
     padding-bottom:8px;margin-bottom:14px;font-weight:600}}

.alert{{border-radius:8px;padding:14px 18px;margin-bottom:16px;font-size:13px}}
.alert-success{{background:var(--success-bg);color:#0f5132;border:1px solid #b7e4c7}}
.alert-warning{{background:var(--warning-bg);color:#7a5b00;border:1px solid #f5d98b}}
.alert-danger{{background:var(--danger-bg);color:#1a1a1a;border:1px solid #f3b3b3}}
.alert-note{{font-weight:400;margin-top:6px;font-size:12px;opacity:.9}}

section{{background:var(--card-bg);border-radius:10px;margin-bottom:22px;box-shadow:0 2px 8px rgba(0,0,0,.06);overflow:hidden}}
section.hidden-by-filter{{display:none}}
h2{{padding:15px 20px;background:#f8f9fb;border-bottom:1px solid var(--border);font-size:14px;color:var(--primary);font-weight:600}}
h2 .count{{font-size:11px;font-weight:normal;color:var(--text-light);margin-left:8px}}
.table-wrap{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
thead{{background:var(--primary);color:#fff}}
th{{padding:9px 12px;text-align:left;font-weight:600;white-space:nowrap;cursor:pointer;user-select:none}}
th:hover{{background:rgba(255,255,255,.12)}}
th[data-orden="asc"]::after{{content:' \25B2';font-size:9px}}
th[data-orden="desc"]::after{{content:' \25BC';font-size:9px}}
tbody tr{{border-bottom:1px solid #f1f3f5}}
tbody tr:hover{{background:#f4f7fb}}
tbody tr.row-hidden{{display:none}}
td{{padding:7px 12px;color:#414952;max-width:400px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.badge-cell{{display:inline-block;padding:2px 7px;border-radius:4px;font-size:11px;font-weight:600}}
.crit{{background:#721c24;color:#fff}}
.err{{background:#f8d7da;color:#721c24}}
.warn{{background:#fff3cd;color:#856404}}
.ok{{background:#d4edda;color:#155724}}

.findings-table td{{white-space:normal}}
tr.finding-row.crit{{background:#fdf2f2;color:#1a1a1a}}
tr.finding-row.warn{{background:#fff8e6;color:#1a1a1a}}

.nota-icon{{cursor:help;margin-left:2px}}

footer{{text-align:center;padding:22px;color:var(--text-light);font-size:11px;margin-left:{margin}}}

@media (max-width:900px){{
  nav{{display:none}}
  header, .toolbar, main, footer{{margin-left:0}}
}}
@media print{{
  nav, .toolbar{{display:none}}
  header, main, footer{{margin-left:0}}
  section{{box-shadow:none;break-inside:avoid}}
}}
"""

    @staticmethod
    def _js_tablas_ordenables() -> str:
        """Hace clicables las cabeceras de TODAS las tablas del informe para
        ordenar por esa columna (asc/desc alternando en cada clic). Detecta
        fechas dd/MM/yyyy (con hora opcional) y las ordena cronológicamente
        en vez de por texto — mismo motivo que _FechaAwareSortProxy en la
        app: comparar el texto tal cual hace que el día mande sobre el mes y
        el año. Números puros (PID, tamaños, días...) ordenan numéricamente;
        el resto, como texto. No interfiere con el buscador/filtro de nivel:
        ordenar reordena filas en el DOM, filtrar solo las oculta con CSS."""
        return """
(function(){
  function valorCelda(td) {
    var t = (td.textContent || '').trim();
    var mFecha = t.match(/^(\\d{2})\\/(\\d{2})\\/(\\d{4})(?:[ T](\\d{1,2}):(\\d{2})(?::(\\d{2}))?)?$/);
    if (mFecha) {
      return { tipo: 1, val: Date.UTC(+mFecha[3], +mFecha[2] - 1, +mFecha[1], +(mFecha[4] || 0), +(mFecha[5] || 0), +(mFecha[6] || 0)) };
    }
    var comoNumero = t.replace(',', '.');
    if (t !== '' && /^-?[\\d.]+$/.test(comoNumero) && !isNaN(parseFloat(comoNumero))) {
      return { tipo: 1, val: parseFloat(comoNumero) };
    }
    return { tipo: 0, val: t.toLowerCase() };
  }

  function ordenarTabla(table, colIdx, asc) {
    var tbody = table.querySelector('tbody');
    if (!tbody) return;
    var filas = Array.prototype.slice.call(tbody.querySelectorAll('tr'));
    filas.sort(function(a, b) {
      var ca = a.children[colIdx], cb = b.children[colIdx];
      if (!ca || !cb) return 0;
      var va = valorCelda(ca), vb = valorCelda(cb);
      if (va.tipo !== vb.tipo) return va.tipo - vb.tipo;
      if (va.val < vb.val) return asc ? -1 : 1;
      if (va.val > vb.val) return asc ? 1 : -1;
      return 0;
    });
    filas.forEach(function(tr) { tbody.appendChild(tr); });
  }

  document.querySelectorAll('table').forEach(function(table) {
    var ths = table.querySelectorAll('thead th');
    var estado = {};
    ths.forEach(function(th, idx) {
      th.title = 'Ordenar por esta columna';
      th.addEventListener('click', function() {
        var asc = !estado[idx];
        estado = {};
        estado[idx] = asc;
        ths.forEach(function(otro) { otro.removeAttribute('data-orden'); });
        th.setAttribute('data-orden', asc ? 'asc' : 'desc');
        ordenarTabla(table, idx, asc);
      });
    });
  });
})();
"""

    def _guardar_informe_html(self, path, html, resumen, estado):
        """Escribe el informe y ofrece abrirlo (mismo diálogo para todos los informes HTML)."""
        try:
            informe_html.guardar(path, html)
        except Exception as e:
            logger.exception(f"Error guardando informe HTML: {e}")
            self._avisar("Error", f"No se pudo guardar:\n\n{e}", QMessageBox.Critical)
            return
        self.set_status(estado)
        resp = QMessageBox.information(self, "Informe exportado", f"Informe guardado en:\n{path}\n\n{resumen}",
                                       QMessageBox.Ok | QMessageBox.Open, QMessageBox.Ok)
        if resp == QMessageBox.Open:
            import webbrowser
            webbrowser.open(path)

    def _filas_con_notas(self, titulo, rows_data):
        """Celda Nombre con 📝: icono con el texto de la nota en el tooltip (HTML propio)."""
        return [[informe_html.Html(self._celda_nombre_con_nota_html(titulo, str(c))) if ci == 0 and " 📝" in str(c) else c
                 for ci, c in enumerate(fila)] for fila in rows_data]

    def exportar_informe_html_completo(self):
        """Informe HTML con todos los datos cargados: resumen ejecutivo (KPIs y hallazgos de
        seguridad) y una sección plegable por módulo, con la plantilla común de informes."""
        sections = self._collect_all_sections_data()
        if not sections:
            self._avisar("Sin datos", "No hay datos cargados para exportar.\nUsa 🔄 Sincronizar Todo primero.")
            return
        path = dialogo_guardar_archivo(self, "Guardar informe HTML",
            ruta_guardado_predeterminada(f"InformeMASys_{time.strftime('%Y%m%d_%H%M%S')}.html"), "HTML (*.html)")
        if not path:
            return
        total_rows = sum(len(s[2]) for s in sections)
        findings = self._calcular_hallazgos_seguridad()
        n_crit = sum(1 for f in findings if f[0] == '🔴')
        n_warn = sum(1 for f in findings if f[0] == '⚠️')
        n_info = sum(1 for f in findings if f[0] == 'ℹ️')
        kpis = [informe_html.Kpi(len(sections), "Módulos con datos"), informe_html.Kpi(total_rows, "Registros totales"),
                informe_html.Kpi(n_crit, "Hallazgos críticos", "crit"), informe_html.Kpi(n_warn, "Advertencias", "warn"),
                informe_html.Kpi(n_info, "Informativos", "ok")]
        # Un bloque por categoría (como el árbol de MASys) y, dentro, cada módulo
        orden = {c: n for n, c in enumerate(CATEGORY_ORDER)}
        con_cat = sorted(((t, h, r, _categoria_de(t)) for t, h, r in sections),
                         key=lambda x: orden.get(x[3][0], len(orden)))
        secs = [informe_html.Seccion(t, h, self._filas_con_notas(t, r), columnas_nivel=(1, 3, 4), abierta=(i == 0), categoria=cat[1])
                for i, (t, h, r, cat) in enumerate(con_cat)]
        html = informe_html.documento("📊 Informe del sistema", secs, kpis=kpis,
                                      bloque_inicial=self._resumen_hallazgos_seguridad_html(findings), titulo_bloque="Resumen ejecutivo")
        self._guardar_informe_html(path, html, f"{len(sections)} módulos, {total_rows} registros totales.",
                                   f"✅ Informe HTML exportado: {os.path.basename(path)} ({total_rows} registros)")

    def _exportar_html_seccion(self, titulo, headers, rows_data):
        """Informe HTML de una sola sección (botón «Informe HTML» de la barra inferior)
        con la plantilla común de informes."""
        prefijo = self._nombre_archivo_seccion(titulo)
        path = dialogo_guardar_archivo(self, "Guardar informe HTML",
            ruta_guardado_predeterminada(f"{prefijo}_{time.strftime('%Y%m%d_%H%M%S')}.html"), "HTML (*.html)")
        if not path:
            return
        html = informe_html.documento(titulo, [informe_html.Seccion(titulo, headers, self._filas_con_notas(titulo, rows_data),
                                                                     columnas_nivel=(1, 3, 4))], indice=False)
        self._guardar_informe_html(path, html, f"{len(rows_data)} registro(s).",
                                   f"✅ Informe HTML de «{titulo}» exportado: {os.path.basename(path)}")

    def _exportar_html_multiseccion(self, titulo_pagina, sections):
        """Informe HTML de una sección repartida en varias tablas (Sistema: Equipo, Procesador,
        Memoria...; las secciones nuevas con pestañas) con índice lateral y la plantilla común."""
        prefijo = self._nombre_archivo_seccion(titulo_pagina)
        path = dialogo_guardar_archivo(self, "Guardar informe HTML",
            ruta_guardado_predeterminada(f"{prefijo}_{time.strftime('%Y%m%d_%H%M%S')}.html"), "HTML (*.html)")
        if not path:
            return
        total_rows = sum(len(r) for _t, _h, r in sections)
        secs = [informe_html.Seccion(t, h, r, columnas_nivel=(1, 3, 4)) for t, h, r in sections]
        html = informe_html.documento(titulo_pagina, secs)
        self._guardar_informe_html(path, html, f"{len(sections)} categorías, {total_rows} registros totales.",
                                   f"✅ Informe HTML de «{titulo_pagina}» exportado: {os.path.basename(path)}")

    def _pdf_en_segundo_plano(self, html: str, path: str, estado_ok: str, mensaje_ok: str) -> None:
        """Genera el PDF con el cursor de espera y la barra de estado al día. (En un hilo no sirve: PyQt5
        no suelta el GIL mientras QTextDocument maqueta y la ventana se congelaba igual; ahora
        render_html_a_pdf maqueta por secciones y deja respirar a la ventana entre una y otra.)"""
        from PyQt5.QtGui import QCursor
        self.set_status(f"🔄 Generando el PDF «{os.path.basename(path)}»...")
        QApplication.setOverrideCursor(QCursor(Qt.WaitCursor))
        QApplication.processEvents()
        try:
            render_html_a_pdf(html, path)
        finally:
            QApplication.restoreOverrideCursor()
        self.set_status(estado_ok)
        resp = QMessageBox.information(self, "Informe exportado", mensaje_ok,
                                       QMessageBox.Ok | QMessageBox.Open, QMessageBox.Ok)
        if resp == QMessageBox.Open:
            import webbrowser
            webbrowser.open(path)

    def exportar_informe_pdf(self):
        """Genera un informe PDF corporativo con todos los datos cargados:
        portada con resumen ejecutivo (KPIs + hallazgos de seguridad) y una
        sección por módulo, cada una empezando siempre en página nueva
        (ninguna categoría arranca a media página)."""
        import socket

        sections = self._collect_all_sections_data(limite=500)
        if not sections:
            self._avisar("Sin datos", "No hay datos cargados para exportar.\nUsa 🔄 Sincronizar Todo primero.")
            return

        path = dialogo_guardar_archivo(self, "Guardar informe PDF",
            ruta_guardado_predeterminada(f"InformeMASys_{time.strftime('%Y%m%d_%H%M%S')}.pdf"), "PDF (*.pdf)")
        if not path:
            return

        try:
            hostname = getattr(self, "_nombre_equipo", socket.gethostname)()   # 5.2.0: el del cliente en modo remoto
            ts = time.strftime('%d/%m/%Y %H:%M:%S')
            total_rows = sum(len(s[2]) for s in sections)
            findings = self._calcular_hallazgos_seguridad()
            n_crit = sum(1 for f in findings if f[0] == '🔴')
            n_warn = sum(1 for f in findings if f[0] == '⚠️')
            n_info = sum(1 for f in findings if f[0] == 'ℹ️')

            # ── Portada: KPIs mediante tabla (Qt no soporta flex/grid en su
            # subconjunto de HTML para impresión, así que las "tarjetas" se
            # simulan con celdas de una tabla) ──
            def _kpi_cell(valor, etiqueta, color):
                return (f'<td class="kpi" style="border-top:3px solid {color};">'
                        f'<div class="kpi-val">{valor}</div><div class="kpi-lbl">{etiqueta}</div></td>')

            kpi_table = ("<table class=\"kpis\"><tr>"
                + _kpi_cell(len(sections), "Módulos con datos", "#2563eb")
                + _kpi_cell(total_rows, "Registros totales", "#2563eb")
                + _kpi_cell(n_crit, "Hallazgos críticos", "#dc2626")
                + _kpi_cell(n_warn, "Advertencias", "#ca8a04")
                + _kpi_cell(n_info, "Informativos", "#16a34a")
                + "</tr></table>")

            if not findings:
                hallazgos_html = ('<div class="alert alert-ok">✅ <b>Sin hallazgos de seguridad.</b> '
                                   'No se detectaron configuraciones potencialmente peligrosas en los '
                                   'datos cargados en el momento de generar este informe.</div>')
            else:
                interpretacion = (
                    f"Se detectaron {len(findings)} hallazgo(s): <b>{n_crit} crítico(s)</b> a resolver de forma "
                    f"prioritaria, {n_warn} advertencia(s) y {n_info} informativo(s)."
                ) if n_crit else (
                    f"Se detectaron {len(findings)} hallazgo(s), ninguno crítico: {n_warn} advertencia(s) "
                    f"y {n_info} informativo(s)."
                )
                filas_hallazgos = ""
                for nivel, cat, desc, det in findings:
                    cls = self._nivel_hallazgo_a_clase(nivel)
                    bg = {"crit": "#fdecec", "warn": "#fff8e6"}.get(cls, "#ffffff")
                    filas_hallazgos += (f'<tr style="background:{bg};">'
                                        f"<td>{nivel}</td><td>{html_escape(cat)}</td>"
                                        f"<td>{html_escape(desc)}</td><td>{html_escape(det)}</td></tr>")
                hallazgos_html = (
                    f'<div class="alert {"alert-crit" if n_crit else "alert-warn"}">'
                    f'{"🔴" if n_crit else "⚠️"} <b>{len(findings)} hallazgo(s) de seguridad.</b> {interpretacion}</div>'
                    '<table class="datos"><tr><th>Nivel</th><th>Categoría</th><th>Descripción</th><th>Detalle</th></tr>'
                    + filas_hallazgos + "</table>"
                )

            html = f"""<html><head><meta charset="UTF-8">
<style>
    body {{ font-family: Arial, sans-serif; font-size: 9pt; color:#2c2c2c; }}
    h1 {{ color:#1a3a5c; font-size:20pt; font-weight:normal; margin-bottom:4px; }}
    .subtitulo {{ color:#5b6472; font-size:10pt; margin-bottom:18px; }}
    h2.cat-title {{ color:#ffffff; background:#1a3a5c; font-size:12pt; padding:8px 10px; margin:0; }}
    h2.exec {{ color:#1a3a5c; font-size:12pt; border-bottom:2px solid #1a3a5c; padding-bottom:4px; margin:14px 0 10px 0; }}
    table.kpis {{ width:100%; margin-bottom:14px; }}
    table.kpis .kpi {{ background:#f5f7fa; padding:10px 8px; text-align:center; width:20%; }}
    .kpi-val {{ font-size:16pt; color:#1a3a5c; font-weight:bold; }}
    .kpi-lbl {{ font-size:7.5pt; color:#5b6472; }}
    .alert {{ padding:8px 10px; margin-bottom:10px; border-radius:3px; font-size:9pt; }}
    .alert-ok {{ background:#e8f8ee; color:#0f5132; }}
    .alert-warn {{ background:#fff8e6; color:#7a5b00; }}
    .alert-crit {{ background:#fdecec; color:#7a1a1a; }}
    table.datos {{ border-collapse: collapse; width:100%; margin-top:14px; margin-bottom:10px; }}
    table.datos th {{ background:#1a3a5c; color:white; padding:4px 6px; text-align:left; font-size:8pt; }}
    table.datos td {{ padding:3px 6px; border-bottom:1px solid #ddd; font-size:8pt; }}
    .cat-count {{ color:#dce6f0; font-size:8pt; font-weight:normal; }}
    .cat-block {{ page-break-before: always; }}
    .pie {{ color:#868e96; font-size:7.5pt; margin-top:14px; }}
</style></head><body>

<h1>📊 Informe del sistema — MASys</h1>
<div class="subtitulo">Equipo: <b>{html_escape(hostname)}</b> &nbsp;|&nbsp; Generado: {ts}</div>

{kpi_table}

<h2 class="exec">Resumen ejecutivo de seguridad</h2>
{hallazgos_html}

<div class="pie">Detalle completo por módulo en las páginas siguientes — cada módulo empieza en una página nueva.</div>
"""
            for titulo, headers, rows_data in sections:
                html += f'<div class="cat-block"><h2 class="cat-title">{html_escape(titulo)} <span class="cat-count">({len(rows_data)} registro(s))</span></h2>'
                html += '<table class="datos"><tr>'
                html += "".join(f"<th>{html_escape(h)}</th>" for h in headers)
                html += "</tr>"
                for row_data in rows_data:
                    html += "<tr>"
                    for ci, c in enumerate(row_data):
                        celda = html_escape(str(c))
                        cls = self._nivel_class_celda(celda) if ci in (1, 3, 4) else ""
                        bg = {"crit": "#721c24", "err": "#f8d7da", "warn": "#fff3cd", "ok": "#d4edda"}.get(cls)
                        color = "#ffffff" if cls == "crit" else None
                        estilo = f' style="background:{bg};{"color:"+color+";" if color else ""}"' if bg else ""
                        html += f"<td{estilo}>{celda}</td>"
                    html += "</tr>"
                    if row_data and " 📝" in str(row_data[0]):
                        html += self._fila_nota_pdf(titulo, str(row_data[0]), len(headers))
                html += "</table></div>"
            html += f'<div class="pie">Generado por MASys · {ts}</div>'
            html += "</body></html>"

            # 5.4.2: PDF maquetado por secciones (ver render_html_a_pdf) con cursor de espera.
            self._pdf_en_segundo_plano(html, path, f"✅ Informe PDF generado: {os.path.basename(path)}", f"Informe PDF guardado en:\n{path}\n\n{len(sections)} módulos, {total_rows} registros totales.")
        except Exception as e:
            logger.exception(f"Error generando PDF: {e}")
            self._avisar("Error", f"No se pudo generar el informe PDF:\n\n{e}", QMessageBox.Critical)

    def _exportar_pdf_seccion(self, titulo, headers, rows_data):
        """Informe PDF corporativo de una sola sección (misma paleta y
        cabecera que el informe consolidado). Usado por el botón adaptativo
        'PDF' cuando la sección activa no es el Dashboard."""
        import socket

        prefijo = self._nombre_archivo_seccion(titulo)
        path = dialogo_guardar_archivo(self, "Guardar informe PDF",
            ruta_guardado_predeterminada(f"{prefijo}_{time.strftime('%Y%m%d_%H%M%S')}.pdf"),
            "PDF (*.pdf)")
        if not path:
            return

        try:
            hostname = getattr(self, "_nombre_equipo", socket.gethostname)()   # 5.2.0: el del cliente en modo remoto
            ts = time.strftime('%d/%m/%Y %H:%M:%S')

            html = f"""<html><head><meta charset="UTF-8">
<style>
    body {{ font-family: Arial, sans-serif; font-size: 9pt; color:#2c2c2c; }}
    h1 {{ color:#1a3a5c; font-size:20pt; font-weight:normal; margin-bottom:4px; }}
    .subtitulo {{ color:#5b6472; font-size:10pt; margin-bottom:18px; }}
    table.datos {{ border-collapse: collapse; width:100%; margin-bottom:10px; }}
    table.datos th {{ background:#1a3a5c; color:white; padding:4px 6px; text-align:left; font-size:8pt; }}
    table.datos td {{ padding:3px 6px; border-bottom:1px solid #ddd; font-size:8pt; }}
    .pie {{ color:#868e96; font-size:7.5pt; margin-top:14px; }}
</style></head><body>

<h1>{titulo}</h1>
<div class="subtitulo">Equipo: <b>{html_escape(hostname)}</b> &nbsp;|&nbsp; Generado: {ts}
&nbsp;|&nbsp; {len(rows_data)} registro(s)</div>

<table class="datos"><tr>"""
            html += "".join(f"<th>{html_escape(h)}</th>" for h in headers)
            html += "</tr>"
            for row_data in rows_data:
                html += "<tr>"
                for ci, c in enumerate(row_data):
                    celda = html_escape(str(c))
                    cls = self._nivel_class_celda(celda) if ci in (1, 3, 4) else ""
                    bg = {"crit": "#721c24", "err": "#f8d7da", "warn": "#fff3cd", "ok": "#d4edda"}.get(cls)
                    color = "#ffffff" if cls == "crit" else None
                    estilo = f' style="background:{bg};{"color:"+color+";" if color else ""}"' if bg else ""
                    html += f"<td{estilo}>{celda}</td>"
                html += "</tr>"
                if row_data and " 📝" in str(row_data[0]):
                    html += self._fila_nota_pdf(titulo, str(row_data[0]), len(headers))
            html += "</table>"
            html += f'<div class="pie">Generado por MASys · {ts}</div>'
            html += "</body></html>"

            # 5.4.2: PDF maquetado por secciones (ver render_html_a_pdf) con cursor de espera.
            self._pdf_en_segundo_plano(html, path, f"✅ Informe PDF de «{titulo}» generado: {os.path.basename(path)}", f"Informe PDF guardado en:\n{path}\n\n{len(rows_data)} registro(s).")
        except Exception as e:
            logger.exception(f"Error generando PDF de sección: {e}")
            self._avisar("Error", f"No se pudo generar el informe PDF:\n\n{e}", QMessageBox.Critical)

    def _exportar_pdf_multiseccion(self, titulo_pagina, sections):
        """Informe PDF con varias secciones SEGUIDAS, sin salto de página
        forzado entre categorías (misma paleta y cabecera que el informe
        consolidado, pero al contrario que este: aquí las categorías van
        una tras otra, no cada una en página nueva) — para el botón
        adaptativo 'PDF' cuando la sección activa se reparte en categorías
        propias (Sistema)."""
        import socket

        prefijo = self._nombre_archivo_seccion(titulo_pagina)
        path = dialogo_guardar_archivo(self, "Guardar informe PDF",
            ruta_guardado_predeterminada(f"{prefijo}_{time.strftime('%Y%m%d_%H%M%S')}.pdf"),
            "PDF (*.pdf)")
        if not path:
            return

        try:
            hostname = getattr(self, "_nombre_equipo", socket.gethostname)()   # 5.2.0: el del cliente en modo remoto
            ts = time.strftime('%d/%m/%Y %H:%M:%S')
            total_rows = sum(len(rows_data) for _, _, rows_data in sections)

            html = f"""<html><head><meta charset="UTF-8">
<style>
    body {{ font-family: Arial, sans-serif; font-size: 9pt; color:#2c2c2c; }}
    h1 {{ color:#1a3a5c; font-size:20pt; font-weight:normal; margin-bottom:4px; }}
    .subtitulo {{ color:#5b6472; font-size:10pt; margin-bottom:18px; }}
    h2.cat-title {{ color:#ffffff; background:#1a3a5c; font-size:12pt; padding:8px 10px; margin:0; }}
    table.datos {{ border-collapse: collapse; width:100%; margin-top:14px; margin-bottom:10px; }}
    table.datos th {{ background:#1a3a5c; color:white; padding:4px 6px; text-align:left; font-size:8pt; }}
    table.datos td {{ padding:3px 6px; border-bottom:1px solid #ddd; font-size:8pt; }}
    .cat-count {{ color:#dce6f0; font-size:8pt; font-weight:normal; }}
    .pie {{ color:#868e96; font-size:7.5pt; margin-top:14px; }}
</style></head><body>

<h1>{titulo_pagina}</h1>
<div class="subtitulo">Equipo: <b>{html_escape(hostname)}</b> &nbsp;|&nbsp; Generado: {ts}
&nbsp;|&nbsp; {len(sections)} categorías &nbsp;|&nbsp; {total_rows} registros totales</div>
"""
            for titulo, headers, rows_data in sections:
                html += f'<div><h2 class="cat-title">{html_escape(titulo)} <span class="cat-count">({len(rows_data)} registro(s))</span></h2>'
                html += '<table class="datos"><tr>'
                html += "".join(f"<th>{html_escape(h)}</th>" for h in headers)
                html += "</tr>"
                for row_data in rows_data:
                    html += "<tr>"
                    for ci, c in enumerate(row_data):
                        celda = html_escape(str(c))
                        cls = self._nivel_class_celda(celda) if ci in (1, 3, 4) else ""
                        bg = {"crit": "#721c24", "err": "#f8d7da", "warn": "#fff3cd", "ok": "#d4edda"}.get(cls)
                        color = "#ffffff" if cls == "crit" else None
                        estilo = f' style="background:{bg};{"color:"+color+";" if color else ""}"' if bg else ""
                        html += f"<td{estilo}>{celda}</td>"
                    html += "</tr>"
                    if row_data and " 📝" in str(row_data[0]):
                        html += self._fila_nota_pdf(titulo, str(row_data[0]), len(headers))
                html += "</table></div>"
            html += f'<div class="pie">Generado por MASys · {ts}</div>'
            html += "</body></html>"

            # 5.4.2: PDF maquetado por secciones (ver render_html_a_pdf) con cursor de espera.
            self._pdf_en_segundo_plano(html, path, f"✅ Informe PDF de «{titulo_pagina}» generado: {os.path.basename(path)}", f"Informe PDF guardado en:\n{path}\n\n{len(sections)} categorías, {total_rows} registros totales.")
        except Exception as e:
            logger.exception(f"Error generando PDF multisección: {e}")
            self._avisar("Error", f"No se pudo generar el informe PDF:\n\n{e}", QMessageBox.Critical)

    def abrir_snapshots(self):
        """Diálogo para guardar y comparar snapshots del estado del sistema."""
        dlg = QDialog(self)
        dlg.setWindowTitle("📸 Snapshots — Estado del sistema")
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dlg.setMinimumSize(860, 560)
        layout = QVBoxLayout(dlg)
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)

        info = QLabel("Guarda el estado actual del sistema en un archivo y compáralo con otro snapshot posterior para ver qué cambió.")
        info.setObjectName("mutedInfoLabel")
        info.setWordWrap(True)
        layout.addWidget(info)

        # Botonera
        btn_row = QHBoxLayout()
        btn_save = QPushButton("💾 Guardar snapshot actual")
        btn_save.setObjectName("primary")
        btn_load_a = QPushButton("📂 Cargar Snapshot A")
        btn_load_b = QPushButton("📂 Cargar Snapshot B")
        btn_compare = QPushButton("🔍 Comparar A vs B")
        btn_compare.setObjectName("success")
        for b in [btn_save, btn_load_a, btn_load_b, btn_compare]:
            btn_row.addWidget(b)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        lbl_status = QLabel("Sin snapshots cargados.")
        lbl_status.setStyleSheet("font-size:9pt; color:#6c757d;")
        layout.addWidget(lbl_status)

        from PyQt5.QtWidgets import QTableWidget, QTableWidgetItem
        tabla = QTableWidget(0, 4)
        tabla.setHorizontalHeaderLabels(["Módulo", "Tipo cambio", "Identificador", "Detalle"])
        tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        tabla.setSelectionBehavior(QTableWidget.SelectRows)
        tabla.setAlternatingRowColors(True)
        tabla.verticalHeader().setVisible(False)
        _setup_header(tabla, 4, stretch_col=3)
        layout.addWidget(tabla)

        snap_a = [None]
        snap_b = [None]

        def _collect_snapshot():
            snap = {"ts": time.strftime('%Y-%m-%d %H:%M:%S'), "data": {}}
            mappings = [
                ("procesos",    getattr(self, 'processes_model', None),  lambda r: f"{r.name}|{r.status}"),
                ("servicios",   getattr(self, 'services_model', None),   lambda r: f"{r.name}|{r.status}"),
                ("software",    getattr(self, 'software_model', None),   lambda r: f"{r.nombre}|{r.version}"),
                ("conexiones",  getattr(self, 'connections_model', None),lambda r: f"{r.protocolo}|{r.direccion_local}|{r.proceso}"),
                ("usuarios",    getattr(self, 'users_model', None),      lambda r: f"{r.nombre}|{r.estado}"),
                ("usb",         getattr(self, 'usb_model', None),        lambda r: r.nombre),
                ("firewall",    getattr(self, 'firewall_model', None),   lambda r: f"{r.nombre}|{r.accion}|{r.puerto}"),
                ("drivers",     getattr(self, 'drivers_model', None),    lambda r: f"{r.dispositivo}|{r.version}"),
                ("software_pendiente", getattr(self, 'pending_updates_model', None), lambda r: r.titulo),
                ("variables_entorno", getattr(self, 'env_vars_model', None), lambda r: f"{r.name}|{r.scope}|{r.value}"),
            ]
            for key, model, keyfn in mappings:
                if model and hasattr(model, 'rows'):
                    try:
                        snap["data"][key] = sorted(set(keyfn(r) for r in model.rows()))
                    except Exception:
                        snap["data"][key] = []
            return snap

        def _save_snap():
            snap = _collect_snapshot()
            if not any(snap["data"].values()):
                QMessageBox.information(dlg, "Sin datos",
                    "No hay datos cargados. Usa 🔄 Sincronizar Todo primero.")
                return
            path = dialogo_guardar_archivo(dlg, "Guardar snapshot",
                ruta_guardado_predeterminada(f"snapshot_{time.strftime('%Y%m%d_%H%M%S')}.json"), "JSON (*.json)")
            if not path:
                return
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(snap, f, ensure_ascii=False, indent=2)
                total = sum(len(v) for v in snap["data"].values())
                QMessageBox.information(dlg, "Guardado",
                    f"Snapshot guardado:\n{path}\n\n{total} entradas en {len(snap['data'])} módulos.")
            except Exception as e:
                QMessageBox.critical(dlg, "Error", str(e))

        def _load_snap(slot):
            path = dialogo_abrir_archivo(dlg, "Cargar snapshot", get_informes_folder(), "JSON (*.json)")
            if not path:
                return
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    snap = json.load(f)
                slot[0] = snap
                ts = snap.get('ts', '?')
                total = sum(len(v) for v in snap.get('data', {}).values())
                lbl_status.setText(
                    f"A: {snap_a[0]['ts'] if snap_a[0] else '—'}   |   "
                    f"B: {snap_b[0]['ts'] if snap_b[0] else '—'}"
                )
                QMessageBox.information(dlg, "Cargado", f"Snapshot del {ts}\n{total} entradas.")
            except Exception as e:
                QMessageBox.critical(dlg, "Error", f"No se pudo cargar:\n{e}")

        def _compare():
            if not snap_a[0] or not snap_b[0]:
                QMessageBox.warning(dlg, "Faltan snapshots", "Carga un Snapshot A y un Snapshot B.")
                return
            tabla.setRowCount(0)
            a_data = snap_a[0].get('data', {})
            b_data = snap_b[0].get('data', {})
            all_keys = set(a_data) | set(b_data)
            changes = 0

            labels = {
                "procesos": "Procesos", "servicios": "Servicios", "software": "Software",
                "conexiones": "Conexiones", "usuarios": "Usuarios", "usb": "USB",
                "firewall": "Firewall", "drivers": "Controladores", "software_pendiente": "Actualizaciones Pendientes",
                "variables_entorno": "Variables de Entorno",
            }
            colors = {"🟢 Añadido": QColor("#d4edda"), "🔴 Eliminado": QColor("#f8d7da"),
                      "🟡 Modificado": QColor("#fff3cd")}

            for key in sorted(all_keys):
                a_set = set(a_data.get(key, []))
                b_set = set(b_data.get(key, []))
                label = labels.get(key, key)

                added = b_set - a_set
                removed = a_set - b_set

                for item in sorted(added):
                    r = tabla.rowCount(); tabla.insertRow(r)
                    tabla.setItem(r, 0, QTableWidgetItem(label))
                    tabla.setItem(r, 1, QTableWidgetItem("🟢 Añadido"))
                    tabla.setItem(r, 2, QTableWidgetItem(item.split("|")[0]))
                    tabla.setItem(r, 3, QTableWidgetItem(item))
                    for c in range(4):
                        if tabla.item(r, c):
                            tabla.item(r, c).setBackground(colors["🟢 Añadido"])
                            tabla.item(r, c).setForeground(QColor("#0f5132"))
                    changes += 1

                for item in sorted(removed):
                    r = tabla.rowCount(); tabla.insertRow(r)
                    tabla.setItem(r, 0, QTableWidgetItem(label))
                    tabla.setItem(r, 1, QTableWidgetItem("🔴 Eliminado"))
                    tabla.setItem(r, 2, QTableWidgetItem(item.split("|")[0]))
                    tabla.setItem(r, 3, QTableWidgetItem(item))
                    for c in range(4):
                        if tabla.item(r, c):
                            tabla.item(r, c).setBackground(colors["🔴 Eliminado"])
                            tabla.item(r, c).setForeground(QColor("#58151c"))
                    changes += 1

            if changes == 0:
                lbl_status.setText("✅ Sin diferencias entre los dos snapshots.")
            else:
                lbl_status.setText(f"🔍 {changes} diferencia(s) encontrada(s) entre A ({snap_a[0]['ts']}) y B ({snap_b[0]['ts']})")

        btn_save.clicked.connect(_save_snap)
        btn_load_a.clicked.connect(lambda: _load_snap(snap_a))
        btn_load_b.clicked.connect(lambda: _load_snap(snap_b))
        btn_compare.clicked.connect(_compare)

        # ── Capturas automáticas programadas ─────────────────────
        from PyQt5.QtWidgets import QGroupBox, QSpinBox
        auto_group = QGroupBox("⏱️ Capturas Automáticas Programadas")
        auto_layout = QVBoxLayout(auto_group)

        auto_row1 = QHBoxLayout()
        auto_row1.addWidget(QLabel("Carpeta destino:"))
        auto_folder_edit = QLineEdit()
        auto_folder_edit.setPlaceholderText("Selecciona dónde guardar los snapshots automáticos...")
        auto_folder_edit.setReadOnly(True)
        auto_folder_edit.setMaximumWidth(280)
        auto_row1.addWidget(auto_folder_edit)
        btn_select_folder = QPushButton("📂")
        btn_select_folder.setFixedWidth(44)
        btn_select_folder.setStyleSheet("padding: 4px 6px;")
        btn_select_folder.setToolTip("Seleccionar carpeta destino")
        auto_row1.addWidget(btn_select_folder)
        auto_row1.addStretch()
        auto_layout.addLayout(auto_row1)

        auto_row2 = QHBoxLayout()
        auto_row2.addWidget(QLabel("Intervalo:"))
        spin_interval = QSpinBox()
        spin_interval.setRange(1, 1440)
        spin_interval.setValue(30)
        spin_interval.setSuffix(" min")
        spin_interval.setFixedWidth(100)
        auto_row2.addWidget(spin_interval)

        auto_row2.addSpacing(20)
        auto_row2.addWidget(QLabel("Máx. snapshots:"))
        spin_max = QSpinBox()
        spin_max.setRange(1, 999)
        spin_max.setValue(48)
        spin_max.setFixedWidth(80)
        auto_row2.addWidget(spin_max)

        auto_row2.addSpacing(20)
        chk_only_changes = QCheckBox("Solo si hay cambios")
        chk_only_changes.setChecked(True)
        chk_only_changes.setToolTip("Solo guardar snapshot si el estado del sistema difiere del anterior")
        auto_row2.addWidget(chk_only_changes)
        auto_row2.addStretch()
        auto_layout.addLayout(auto_row2)

        auto_row3 = QHBoxLayout()
        btn_start_auto = QPushButton("▶️ Iniciar capturas automáticas")
        btn_start_auto.setObjectName("success")
        btn_stop_auto = QPushButton("⏹️ Detener")
        btn_stop_auto.setEnabled(False)
        lbl_auto_status = QLabel("Detenido")
        lbl_auto_status.setStyleSheet("color:#6c757d; font-size:9pt;")
        auto_row3.addWidget(btn_start_auto)
        auto_row3.addWidget(btn_stop_auto)
        auto_row3.addSpacing(15)
        auto_row3.addWidget(lbl_auto_status)
        auto_row3.addStretch()
        auto_layout.addLayout(auto_row3)

        layout.addWidget(auto_group)

        auto_timer = [None]
        auto_last_snap = [None]
        auto_count = [0]

        def _select_auto_folder():
            folder = dialogo_elegir_carpeta(dlg, "Carpeta para snapshots automáticos", get_informes_folder())
            if folder:
                auto_folder_edit.setText(folder)
                auto_folder_edit.setToolTip(folder)

        def _auto_capture():
            folder = auto_folder_edit.text()
            if not folder or not os.path.isdir(folder):
                return
            snap = _collect_snapshot()
            if not any(snap["data"].values()):
                return

            if chk_only_changes.isChecked() and auto_last_snap[0]:
                if snap["data"] == auto_last_snap[0]["data"]:
                    lbl_auto_status.setText(
                        f"⏭️ Sin cambios — {time.strftime('%H:%M:%S')} — {auto_count[0]} guardados")
                    return

            max_snaps = spin_max.value()
            existing = sorted([f for f in os.listdir(folder) if f.startswith("auto_snap_") and f.endswith(".json")])
            while len(existing) >= max_snaps:
                try:
                    os.remove(os.path.join(folder, existing.pop(0)))
                except Exception:
                    pass

            filename = f"auto_snap_{time.strftime('%Y%m%d_%H%M%S')}.json"
            filepath = os.path.join(folder, filename)
            try:
                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(snap, f, ensure_ascii=False, indent=2)
                auto_last_snap[0] = snap
                auto_count[0] += 1
                total = sum(len(v) for v in snap["data"].values())
                lbl_auto_status.setText(
                    f"✅ Captura #{auto_count[0]} — {time.strftime('%H:%M:%S')} — {total} entradas — {filename}")
            except Exception as e:
                lbl_auto_status.setText(f"❌ Error: {e}")

        def _start_auto():
            folder = auto_folder_edit.text()
            if not folder or not os.path.isdir(folder):
                QMessageBox.warning(dlg, "Sin carpeta", "Selecciona una carpeta destino primero.")
                return
            interval_ms = spin_interval.value() * 60 * 1000
            auto_count[0] = 0
            auto_last_snap[0] = None
            timer = QTimer(dlg)
            timer.timeout.connect(_auto_capture)
            timer.start(interval_ms)
            auto_timer[0] = timer
            btn_start_auto.setEnabled(False)
            btn_stop_auto.setEnabled(True)
            spin_interval.setEnabled(False)
            spin_max.setEnabled(False)
            chk_only_changes.setEnabled(False)
            lbl_auto_status.setText(
                f"▶️ Activo — cada {spin_interval.value()} min — próxima captura a las "
                f"{time.strftime('%H:%M', time.localtime(time.time() + spin_interval.value() * 60))}")
            _auto_capture()

        def _stop_auto():
            if auto_timer[0]:
                auto_timer[0].stop()
                auto_timer[0] = None
            btn_start_auto.setEnabled(True)
            btn_stop_auto.setEnabled(False)
            spin_interval.setEnabled(True)
            spin_max.setEnabled(True)
            chk_only_changes.setEnabled(True)
            lbl_auto_status.setText(f"⏹️ Detenido — {auto_count[0]} capturas realizadas")

        btn_select_folder.clicked.connect(_select_auto_folder)
        btn_start_auto.clicked.connect(_start_auto)
        btn_stop_auto.clicked.connect(_stop_auto)

        btn_close = QPushButton("Cerrar")
        btn_close.setFixedWidth(100)
        close_row = QHBoxLayout()
        close_row.addStretch()
        close_row.addWidget(btn_close)
        layout.addLayout(close_row)

        def _on_close():
            _stop_auto()
            dlg.close()

        btn_close.clicked.connect(_on_close)
        # Asegura que el timer de capturas automáticas se detenga también si el
        # diálogo se cierra con la X de la ventana o con Escape, no solo con
        # el botón "Cerrar".
        dlg.finished.connect(lambda _result: _stop_auto())

        dlg.exec_()

    @staticmethod
    def _nombre_hoja_excel_valido(titulo: str, usados: set) -> str:
        """Convierte un título de sección ('⚙️ Procesos') en un nombre de hoja
        Excel válido: sin emoji/caracteres prohibidos (: \\ / ? * [ ]), máx.
        31 caracteres, y sin colisionar con nombres ya usados en el libro."""
        import re
        nombre = re.sub(r'[^\w\s.\-]', '', titulo, flags=re.UNICODE).strip()
        nombre = re.sub(r'\s+', ' ', nombre) or "Hoja"
        nombre = nombre[:31]
        base, i = nombre, 2
        while nombre.lower() in usados:
            sufijo = f" ({i})"
            nombre = base[:31 - len(sufijo)] + sufijo
            i += 1
        usados.add(nombre.lower())
        return nombre

    def guardar_resultados_excel(self):
        """Exporta todos los datos cargados a un Excel (.xlsx) profesional:
        una hoja 'Resumen' inicial (KPIs + hallazgos de seguridad + índice
        con enlaces) y una hoja por módulo, cada una como Tabla de Excel
        real (con autofiltro, franjas de color y encabezado fijo)."""
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
            from openpyxl.utils import get_column_letter
            from openpyxl.worksheet.table import Table, TableStyleInfo
        except ImportError:
            self._avisar("Error", "El módulo 'openpyxl' no está instalado.\n\n"
                "Para usar esta función, instala openpyxl:\n"
                "pip install openpyxl", QMessageBox.Critical)
            return

        sections = self._collect_all_sections_data()
        if not sections:
            self._avisar("Sin datos", "No hay datos cargados para exportar.\nUsa 🔄 Sincronizar Todo primero.")
            return

        # Seleccionar archivo de destino
        default_name = f"Resultados_Completos_{time.strftime('%Y%m%d_%H%M%S')}.xlsx"
        path = dialogo_guardar_archivo(
            self,
            "Guardar Resultados en Excel",
            ruta_guardado_predeterminada(default_name),
            "Excel (*.xlsx)")

        if not path:
            return

        # Crear diálogo de progreso
        progress_dialog = QDialog(self)
        progress_dialog.setWindowTitle("Exportando a Excel...")
        progress_dialog.setWindowFlags(progress_dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        progress_dialog.setModal(True)
        progress_dialog.setFixedSize(400, 150)

        dialog_layout = QVBoxLayout(progress_dialog)
        dialog_layout.setSpacing(10)
        dialog_layout.setContentsMargins(20, 20, 20, 20)

        status_label = QLabel("Preparando exportación...")
        status_label.setAlignment(Qt.AlignCenter)
        dialog_layout.addWidget(status_label)

        progress_bar_dialog = QProgressBar()
        progress_bar_dialog.setMinimum(0)
        progress_bar_dialog.setMaximum(100)
        progress_bar_dialog.setValue(0)
        dialog_layout.addWidget(progress_bar_dialog)

        progress_dialog.show()
        QApplication.processEvents()

        try:
            import socket
            hostname = getattr(self, "_nombre_equipo", socket.gethostname)()   # 5.2.0: el del cliente en modo remoto
            ts = time.strftime('%d/%m/%Y %H:%M:%S')
            findings = self._calcular_hallazgos_seguridad()
            n_crit = sum(1 for f in findings if f[0] == '🔴')
            n_warn = sum(1 for f in findings if f[0] == '⚠️')
            n_info = sum(1 for f in findings if f[0] == 'ℹ️')

            wb = Workbook()
            wb.remove(wb.active)  # Eliminar hoja por defecto

            # ── Paleta y estilos corporativos ──
            PRIMARY, DANGER, WARNING, SUCCESS = "1A3A5C", "DC2626", "CA8A04", "16A34A"
            header_font = Font(bold=True, color="FFFFFF", size=11)
            header_fill = PatternFill(start_color=PRIMARY, end_color=PRIMARY, fill_type="solid")
            header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell_alignment = Alignment(vertical="top", wrap_text=True)
            thin = Side(style='thin', color="D9D9D9")
            border = Border(left=thin, right=thin, top=thin, bottom=thin)
            fill_crit = PatternFill(start_color="FDECEC", end_color="FDECEC", fill_type="solid")
            fill_warn = PatternFill(start_color="FFF8E6", end_color="FFF8E6", fill_type="solid")

            # Nombres de hoja válidos y únicos, calculados de antemano para
            # poder enlazarlos desde el índice de la hoja Resumen.
            usados = {"resumen"}
            nombres_hoja = [self._nombre_hoja_excel_valido(titulo, usados) for titulo, _, _ in sections]

            # ══════════════════════════════════════════════════════
            # Hoja "Resumen" (siempre la primera)
            # ══════════════════════════════════════════════════════
            status_label.setText("Generando hoja de resumen...")
            QApplication.processEvents()

            ws_resumen = wb.create_sheet("Resumen", 0)
            ws_resumen.sheet_view.showGridLines = False
            ws_resumen.column_dimensions['A'].width = 2
            ws_resumen.column_dimensions['B'].width = 34
            ws_resumen.column_dimensions['C'].width = 14
            ws_resumen.column_dimensions['D'].width = 60

            ws_resumen['B2'] = "📊 Informe MASys — Resumen"
            ws_resumen['B2'].font = Font(bold=True, size=16, color=PRIMARY)
            ws_resumen.merge_cells('B2:D2')

            ws_resumen['B3'] = f"Equipo: {hostname}   |   Generado: {ts}"
            ws_resumen['B3'].font = Font(italic=True, size=10, color="5B6472")
            ws_resumen.merge_cells('B3:D3')

            fila = 5
            kpis = [
                ("Módulos con datos", len(sections), None),
                ("Registros totales", sum(len(s[2]) for s in sections), None),
                ("Hallazgos críticos 🔴", n_crit, DANGER),
                ("Advertencias ⚠️", n_warn, WARNING),
                ("Informativos ℹ️", n_info, SUCCESS),
            ]
            for etiqueta, valor, color in kpis:
                ws_resumen.cell(row=fila, column=2, value=etiqueta).font = Font(bold=True, color=color or "1A1A2E")
                ws_resumen.cell(row=fila, column=3, value=valor).font = Font(bold=True, size=12, color=color or "1A1A2E")
                fila += 1

            fila += 1
            ws_resumen.cell(row=fila, column=2, value="Hallazgos de seguridad").font = Font(bold=True, size=13, color=PRIMARY)
            fila += 1
            if not findings:
                ws_resumen.cell(row=fila, column=2,
                    value="✅ No se detectaron problemas de seguridad en los datos cargados.").font = Font(color=SUCCESS)
                fila += 2
            else:
                for col, titulo in enumerate(["Nivel", "Categoría", "Descripción", "Detalle"], 2):
                    c = ws_resumen.cell(row=fila, column=col, value=titulo)
                    c.font = header_font; c.fill = header_fill; c.alignment = header_alignment; c.border = border
                fila_inicio_hallazgos = fila
                fila += 1
                for nivel, cat, desc, det in findings:
                    fill = fill_crit if nivel == "🔴" else (fill_warn if nivel == "⚠️" else None)
                    for col, valor in zip((2, 3, 4, 5), (nivel, cat, desc, det)):
                        c = ws_resumen.cell(row=fila, column=col, value=valor)
                        c.alignment = cell_alignment; c.border = border
                        if fill: c.fill = fill
                    fila += 1
                ws_resumen.column_dimensions['E'].width = 60
                ws_resumen.auto_filter.ref = f"B{fila_inicio_hallazgos}:E{fila - 1}"
                fila += 1

            fila += 1
            ws_resumen.cell(row=fila, column=2, value="Contenido del informe").font = Font(bold=True, size=13, color=PRIMARY)
            fila += 1
            for col, titulo in enumerate(["Módulo", "Registros"], 2):
                c = ws_resumen.cell(row=fila, column=col, value=titulo)
                c.font = header_font; c.fill = header_fill; c.alignment = header_alignment; c.border = border
            fila += 1
            for (titulo, _headers, rows_data), nombre_hoja in zip(sections, nombres_hoja):
                c_link = ws_resumen.cell(row=fila, column=2, value=titulo)
                c_link.hyperlink = f"#'{nombre_hoja}'!A1"
                c_link.font = Font(color="2563EB", underline="single")
                c_link.border = border
                c_count = ws_resumen.cell(row=fila, column=3, value=len(rows_data))
                c_count.alignment = Alignment(horizontal="center")
                c_count.border = border
                fila += 1

            # ══════════════════════════════════════════════════════
            # Una hoja por módulo, como Tabla de Excel real
            # ══════════════════════════════════════════════════════
            total = len(sections)
            for idx, ((titulo, headers, rows_data), nombre_hoja) in enumerate(zip(sections, nombres_hoja)):
                status_label.setText(f"Exportando: {titulo}...")
                progress_bar_dialog.setValue(int((idx / total) * 90) + 5)
                QApplication.processEvents()

                ws = wb.create_sheet(nombre_hoja)

                for col_idx, header in enumerate(headers, 1):
                    c = ws.cell(row=1, column=col_idx, value=header)
                    c.font = header_font; c.fill = header_fill
                    c.alignment = header_alignment; c.border = border

                anchos = [len(str(h)) for h in headers]
                for row_idx, row_data in enumerate(rows_data, 2):
                    for col_idx, valor in enumerate(row_data, 1):
                        texto = str(valor) if valor is not None else ""
                        c = ws.cell(row=row_idx, column=col_idx, value=texto)
                        c.alignment = cell_alignment; c.border = border
                        anchos[col_idx - 1] = max(anchos[col_idx - 1], min(len(texto), 60))
                        if col_idx == 1:
                            self._adjuntar_nota_excel(c, titulo, texto)

                for col_idx, ancho in enumerate(anchos, 1):
                    ws.column_dimensions[get_column_letter(col_idx)].width = max(10, min(ancho + 2, 60))

                ultima_fila = len(rows_data) + 1
                ultima_col = get_column_letter(len(headers))
                if rows_data:
                    tabla = Table(displayName=f"Tabla{idx + 1}", ref=f"A1:{ultima_col}{ultima_fila}")
                    tabla.tableStyleInfo = TableStyleInfo(
                        name="TableStyleMedium2", showRowStripes=True,
                        showFirstColumn=False, showLastColumn=False, showColumnStripes=False)
                    ws.add_table(tabla)

                ws.freeze_panes = "A2"

            # Guardar archivo
            status_label.setText("Guardando archivo...")
            progress_bar_dialog.setValue(97)
            QApplication.processEvents()

            wb.save(path)

            progress_bar_dialog.setValue(100)
            progress_dialog.close()

            total_rows = sum(len(s[2]) for s in sections)
            self.set_status(f"✅ Resultados exportados a {os.path.basename(path)} ({total_rows} registros)")
            resp = QMessageBox.information(
                self,
                "Exportación completada",
                f"Todos los resultados han sido exportados correctamente a:\n\n{path}\n\n"
                f"{len(sections)} módulos, {total_rows} registros totales.",
                QMessageBox.Ok | QMessageBox.Open, QMessageBox.Ok
            )
            if resp == QMessageBox.Open:
                os.startfile(path)

        except Exception as e:
            progress_dialog.close()
            logger.exception(f"Error al exportar a Excel: {e}")
            self._avisar("Error", f"No se pudo exportar a Excel:\n\n{e}", QMessageBox.Critical)

    def _guardar_excel_seccion(self, titulo, headers, rows_data):
        """Exporta una sola sección a Excel como una Tabla real (autofiltro,
        franjas de color, encabezado fijo) — misma presentación que cada
        hoja de 'Guardar Resultados', pero para un único módulo. Usado por
        el botón adaptativo cuando la sección activa no es el Dashboard."""
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
            from openpyxl.utils import get_column_letter
            from openpyxl.worksheet.table import Table, TableStyleInfo
        except ImportError:
            self._avisar("Error", "El módulo 'openpyxl' no está instalado.\n\n"
                "Para usar esta función, instala openpyxl:\npip install openpyxl", QMessageBox.Critical)
            return

        prefijo = self._nombre_archivo_seccion(titulo)
        path = dialogo_guardar_archivo(
            self, "Guardar Resultados en Excel",
            ruta_guardado_predeterminada(f"{prefijo}_{time.strftime('%Y%m%d_%H%M%S')}.xlsx"),
            "Excel (*.xlsx)")
        if not path:
            return

        try:
            wb = Workbook()
            ws = wb.active
            usados = set()
            ws.title = self._nombre_hoja_excel_valido(titulo, usados)

            PRIMARY = "1A3A5C"
            header_font = Font(bold=True, color="FFFFFF", size=11)
            header_fill = PatternFill(start_color=PRIMARY, end_color=PRIMARY, fill_type="solid")
            header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell_alignment = Alignment(vertical="top", wrap_text=True)
            thin = Side(style='thin', color="D9D9D9")
            border = Border(left=thin, right=thin, top=thin, bottom=thin)

            for col_idx, header in enumerate(headers, 1):
                c = ws.cell(row=1, column=col_idx, value=header)
                c.font = header_font; c.fill = header_fill
                c.alignment = header_alignment; c.border = border

            anchos = [len(str(h)) for h in headers]
            for row_idx, row_data in enumerate(rows_data, 2):
                for col_idx, valor in enumerate(row_data, 1):
                    texto = str(valor) if valor is not None else ""
                    c = ws.cell(row=row_idx, column=col_idx, value=texto)
                    c.alignment = cell_alignment; c.border = border
                    anchos[col_idx - 1] = max(anchos[col_idx - 1], min(len(texto), 60))
                    if col_idx == 1:
                        self._adjuntar_nota_excel(c, titulo, texto)

            for col_idx, ancho in enumerate(anchos, 1):
                ws.column_dimensions[get_column_letter(col_idx)].width = max(10, min(ancho + 2, 60))

            ultima_fila = len(rows_data) + 1
            ultima_col = get_column_letter(len(headers))
            if rows_data:
                tabla = Table(displayName="Tabla1", ref=f"A1:{ultima_col}{ultima_fila}")
                tabla.tableStyleInfo = TableStyleInfo(
                    name="TableStyleMedium2", showRowStripes=True,
                    showFirstColumn=False, showLastColumn=False, showColumnStripes=False)
                ws.add_table(tabla)

            ws.freeze_panes = "A2"
            wb.save(path)

            self.set_status(f"✅ «{titulo}» exportado a {os.path.basename(path)} ({len(rows_data)} registros)")
            resp = QMessageBox.information(
                self, "Exportación completada",
                f"Datos de «{titulo}» exportados correctamente a:\n\n{path}\n\n{len(rows_data)} registro(s).",
                QMessageBox.Ok | QMessageBox.Open, QMessageBox.Ok
            )
            if resp == QMessageBox.Open:
                os.startfile(path)
        except Exception as e:
            logger.exception(f"Error al exportar sección a Excel: {e}")
            self._avisar("Error", f"No se pudo exportar a Excel:\n\n{e}", QMessageBox.Critical)

    def _guardar_excel_multiseccion(self, titulo_pagina, sections):
        """Exporta varias secciones a Excel como distintas Tablas reales
        (cada una con su propio autofiltro) apiladas en LA MISMA hoja, cada
        una precedida por una franja de título con el nombre de la
        categoría — no una hoja por categoría. Usado por el botón
        adaptativo cuando la sección activa se reparte en categorías
        propias (Sistema)."""
        try:
            from openpyxl import Workbook
            from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
            from openpyxl.utils import get_column_letter
            from openpyxl.worksheet.table import Table, TableStyleInfo
        except ImportError:
            self._avisar("Error", "El módulo 'openpyxl' no está instalado.\n\n"
                "Para usar esta función, instala openpyxl:\npip install openpyxl", QMessageBox.Critical)
            return

        prefijo = self._nombre_archivo_seccion(titulo_pagina)
        path = dialogo_guardar_archivo(
            self, "Guardar Resultados en Excel",
            ruta_guardado_predeterminada(f"{prefijo}_{time.strftime('%Y%m%d_%H%M%S')}.xlsx"),
            "Excel (*.xlsx)")
        if not path:
            return

        try:
            PRIMARY = "1A3A5C"
            title_font = Font(bold=True, color="FFFFFF", size=12)
            title_fill = PatternFill(start_color=PRIMARY, end_color=PRIMARY, fill_type="solid")
            header_font = Font(bold=True, color="FFFFFF", size=11)
            header_fill = PatternFill(start_color=PRIMARY, end_color=PRIMARY, fill_type="solid")
            header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell_alignment = Alignment(vertical="top", wrap_text=True)
            thin = Side(style='thin', color="D9D9D9")
            border = Border(left=thin, right=thin, top=thin, bottom=thin)

            wb = Workbook()
            ws = wb.active
            ws.title = self._nombre_hoja_excel_valido(titulo_pagina, set())

            max_cols = max((len(headers) for _, headers, _ in sections), default=1)
            anchos = [10] * max_cols

            fila = 1
            for idx, (titulo, headers, rows_data) in enumerate(sections):
                # Franja de título de la categoría, ocupando el ancho de sus columnas.
                ws.merge_cells(start_row=fila, start_column=1, end_row=fila, end_column=len(headers))
                for col in range(1, len(headers) + 1):
                    c = ws.cell(row=fila, column=col)
                    c.fill = title_fill
                ws.cell(row=fila, column=1, value=f"{titulo}  ({len(rows_data)} registro(s))").font = title_font
                fila += 1

                fila_header = fila
                for col_idx, header in enumerate(headers, 1):
                    c = ws.cell(row=fila, column=col_idx, value=header)
                    c.font = header_font; c.fill = header_fill
                    c.alignment = header_alignment; c.border = border
                    if col_idx - 1 < max_cols:
                        anchos[col_idx - 1] = max(anchos[col_idx - 1], min(len(str(header)) + 2, 60))
                fila += 1

                for row_data in rows_data:
                    for col_idx, valor in enumerate(row_data, 1):
                        texto = str(valor) if valor is not None else ""
                        c = ws.cell(row=fila, column=col_idx, value=texto)
                        c.alignment = cell_alignment; c.border = border
                        if col_idx - 1 < max_cols:
                            anchos[col_idx - 1] = max(anchos[col_idx - 1], min(len(texto) + 2, 60))
                    fila += 1

                if rows_data:
                    # displayName de Tabla: a diferencia de un nombre de hoja, NO
                    # admite espacios — _nombre_archivo_seccion ya los convierte
                    # a "_" y quita emoji, así que es la sanitización correcta aquí.
                    nombre_tabla = f"Tabla_{self._nombre_archivo_seccion(titulo)}_{idx + 1}"
                    tabla = Table(displayName=nombre_tabla,
                                   ref=f"A{fila_header}:{get_column_letter(len(headers))}{fila - 1}")
                    tabla.tableStyleInfo = TableStyleInfo(
                        name="TableStyleMedium2", showRowStripes=True,
                        showFirstColumn=False, showLastColumn=False, showColumnStripes=False)
                    ws.add_table(tabla)

                fila += 1  # fila en blanco antes de la siguiente categoría

            for col_idx, ancho in enumerate(anchos, 1):
                ws.column_dimensions[get_column_letter(col_idx)].width = ancho
            ws.freeze_panes = "A1"

            wb.save(path)

            total_rows = sum(len(rows_data) for _, _, rows_data in sections)
            self.set_status(f"✅ Resultados de «{titulo_pagina}» exportados a {os.path.basename(path)} ({total_rows} registros)")
            resp = QMessageBox.information(
                self, "Exportación completada",
                f"Resultados de «{titulo_pagina}» exportados correctamente a:\n\n{path}\n\n"
                f"{len(sections)} categorías, {total_rows} registros totales.",
                QMessageBox.Ok | QMessageBox.Open, QMessageBox.Ok
            )
            if resp == QMessageBox.Open:
                os.startfile(path)
        except Exception as e:
            logger.exception(f"Error al exportar a Excel: {e}")
            self._avisar("Error", f"No se pudo exportar a Excel:\n\n{e}", QMessageBox.Critical)

