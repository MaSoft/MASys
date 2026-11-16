# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña Certificados: tabla, filtros, refresco, exportar CSV/HTML.
Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).

Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

import os
import re
import csv
import time
import subprocess
import tempfile
from typing import List
from html import escape as html_escape

from PyQt5.QtCore import Qt, QRegExp
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QCheckBox, QPushButton, QMessageBox, QTableView, QMenu,
)

from msys.core.utils import (
    ruta_guardado_predeterminada, dialogo_guardar_archivo, _SafeCsvWriter,
    run_powershell_command_params,
)
from msys.models.rows import CertificateRow
from msys.models.table_models import CertificateTableModel, CertificateFilterProxy
from msys.widgets.delegates import _setup_header
from msys.collectors import CertificateCollector


class CertificadosMixin:
    def crear_tab_certificados(self):
        """Crea la pestaña de certificados."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        # ── Barra de herramientas ────────────────────────────────────────
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        import socket
        hostname = socket.gethostname()
        lbl_hostname_certs = QLabel(f"🖥️ {hostname}")
        lbl_hostname_certs.setObjectName("hostnameLabel")
        toolbar.addWidget(lbl_hostname_certs)

        self.search_certs = QLineEdit()
        self.search_certs.setPlaceholderText("🔎 Buscar certificados...")
        self.search_certs.textChanged.connect(self._apply_certs_search)
        toolbar.addWidget(self.search_certs, 3)

        toolbar.addStretch()

        self.btn_refresh_certs = QPushButton("🔄 Actualizar")
        self.btn_refresh_certs.setObjectName("primary")
        self.btn_refresh_certs.clicked.connect(self.refresh_certificates)
        toolbar.addWidget(self.btn_refresh_certs)

        layout.addLayout(toolbar)

        # Filtros por estado
        filters_layout = QHBoxLayout()
        filters_layout.setSpacing(20)

        self.chk_certs_validos = QCheckBox("✅ Válidos")
        self.chk_certs_validos.setChecked(True)
        self.chk_certs_validos.stateChanged.connect(self.apply_certs_filter)
        filters_layout.addWidget(self.chk_certs_validos)

        self.chk_certs_proximos = QCheckBox("⚠️ Próxima Caducidad")
        self.chk_certs_proximos.setChecked(True)
        self.chk_certs_proximos.stateChanged.connect(self.apply_certs_filter)
        filters_layout.addWidget(self.chk_certs_proximos)

        self.chk_certs_caducados = QCheckBox("❌ Caducados")
        self.chk_certs_caducados.setChecked(True)
        self.chk_certs_caducados.stateChanged.connect(self.apply_certs_filter)
        filters_layout.addWidget(self.chk_certs_caducados)

        self.chk_certs_personales = QCheckBox("👤 Solo Personales")
        self.chk_certs_personales.setChecked(False)
        self.chk_certs_personales.stateChanged.connect(self.apply_certs_filter)
        filters_layout.addWidget(self.chk_certs_personales)

        self.chk_certs_exportables = QCheckBox("🔓 Solo Exportables")
        self.chk_certs_exportables.setChecked(False)
        self.chk_certs_exportables.stateChanged.connect(self.apply_certs_filter)
        filters_layout.addWidget(self.chk_certs_exportables)

        filters_layout.addStretch()
        layout.addLayout(filters_layout)

        # Filtros por almacén y por sospecha
        filters_layout2 = QHBoxLayout()
        filters_layout2.setSpacing(20)

        self.chk_certs_sistema = QCheckBox("💻 Sistema (equipo)")
        self.chk_certs_sistema.setToolTip("Certificados de LocalMachine — afectan a todos los usuarios del equipo")
        self.chk_certs_sistema.setChecked(True)
        self.chk_certs_sistema.stateChanged.connect(self.apply_certs_filter)
        filters_layout2.addWidget(self.chk_certs_sistema)

        self.chk_certs_usuario = QCheckBox("👤 Usuario")
        self.chk_certs_usuario.setToolTip("Certificados de CurrentUser — solo tu perfil de usuario")
        self.chk_certs_usuario.setChecked(True)
        self.chk_certs_usuario.stateChanged.connect(self.apply_certs_filter)
        filters_layout2.addWidget(self.chk_certs_usuario)

        self.chk_certs_sospechosos = QCheckBox("🚨 Solo Sospechosos")
        self.chk_certs_sospechosos.setToolTip(
            "Certificados personales (\\My) con algoritmo de firma débil (MD5/SHA1/MD2) o "
            "autofirmados — los certificados raíz quedan fuera a propósito (Windows trae "
            "decenas ya así de fábrica sin que sea un problema real)."
        )
        self.chk_certs_sospechosos.setChecked(False)
        self.chk_certs_sospechosos.stateChanged.connect(self.apply_certs_filter)
        filters_layout2.addWidget(self.chk_certs_sospechosos)

        filters_layout2.addStretch()

        self.lbl_certs_count = QLabel("Certificados: 0/0")
        self.lbl_certs_count.setObjectName("countLabel")
        filters_layout2.addWidget(self.lbl_certs_count)

        layout.addLayout(filters_layout2)

        # Tabla de certificados
        self.certs_model = CertificateTableModel([])
        self.certs_proxy = CertificateFilterProxy(self)
        self.certs_proxy.setSourceModel(self.certs_model)
        self.certs_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.certs_proxy.setFilterKeyColumn(-1)

        self.certs_table = QTableView()
        self.certs_table.setModel(self.certs_proxy)
        self.certs_table.setAlternatingRowColors(True)
        self.certs_table.setSelectionBehavior(QTableView.SelectRows)
        self.certs_table.verticalHeader().setVisible(False)
        self.certs_table.setSortingEnabled(True)
        self.certs_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.certs_table.customContextMenuRequested.connect(self._ctx_certificados)

        _setup_header(self.certs_table, 10, stretch_col=0)

        layout.addWidget(self.certs_table)

        return tab

    def refresh_certificates(self):
        """Actualiza lista de certificados."""
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.set_status("🔄 Escaneando certificados...")
        self.btn_refresh_certs.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.worker = CertificateCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_certs_data)
        self.worker.finished_error.connect(self._on_certs_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_certs_data(self, rows: List[CertificateRow]):
        """Procesa datos de certificados."""
        self.certs_model.setRows(rows)
        self.btn_refresh_certs.setEnabled(True)
        self.progress_bar.setVisible(False)
        total = len(rows)
        self.set_status(f"✅ {total} certificados encontrados")
        self._update_certs_counter()

    def _on_certs_error(self, msg: str):
        """Maneja errores de certificados."""
        self.btn_refresh_certs.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al escanear certificados")
        self._avisar("Error", f"No se pudieron obtener los certificados:\n\n{msg}", QMessageBox.Warning)

    def apply_certs_filter(self):
        """Aplica los filtros de estado/uso/exportable — el texto de
        búsqueda se aplica aparte, vía setFilterRegExp (ver conexión de
        search_certs), como en el resto de pestañas de MASys. Antes esto
        recreaba una clase de proxy entera y hacía setModel() en cada
        cambio, reiniciando selección y orden de la tabla en cada
        pulsación de tecla o checkbox — ver CertificateFilterProxy."""
        self.certs_proxy.setFilters(
            self.chk_certs_validos.isChecked(),
            self.chk_certs_proximos.isChecked(),
            self.chk_certs_caducados.isChecked(),
            self.chk_certs_personales.isChecked(),
            self.chk_certs_exportables.isChecked(),
            self.chk_certs_sistema.isChecked(),
            self.chk_certs_usuario.isChecked(),
            self.chk_certs_sospechosos.isChecked(),
        )
        self._update_certs_counter()

    def _apply_certs_search(self):
        """Búsqueda de texto — aparte de apply_certs_filter porque el
        proxy delega el filtro de texto en el mecanismo estándar de
        QSortFilterProxyModel (setFilterRegExp), pero el contador visible
        necesita refrescarse también cuando cambia el texto, no solo los
        checkboxes."""
        self.certs_proxy.setFilterRegExp(
            QRegExp(self.search_certs.text(), Qt.CaseInsensitive, QRegExp.FixedString))
        self._update_certs_counter()

    def _update_certs_counter(self):
        """Actualiza la etiqueta de contador visible en la barra — antes
        Certificados era la única pestaña (junto a Credenciales) sin un
        contador fijo como el de Usuarios, solo el mensaje de abajo, que
        desaparece en cuanto pasa cualquier otra cosa."""
        self.lbl_certs_count.setText(f"Certificados: {self.certs_proxy.rowCount()}/{self.certs_model.rowCount()}")

    def _ctx_certificados(self, pos):
        """Menú contextual: ver con el visor nativo de Windows, exportar a
        .cer, eliminar (solo CurrentUser, con aviso reforzado si es un
        certificado raíz) y copiar fila."""
        idx = self.certs_table.indexAt(pos)
        if not idx.isValid():
            return
        self.certs_table.selectRow(idx.row())
        row = idx.row()

        asunto = str(self.certs_proxy.data(self.certs_proxy.index(row, 0), Qt.DisplayRole) or "")
        huella = str(self.certs_proxy.data(self.certs_proxy.index(row, 4), Qt.DisplayRole) or "").strip()
        ubicacion = str(self.certs_proxy.data(self.certs_proxy.index(row, 6), Qt.DisplayRole) or "").strip()
        es_current_user = ubicacion.lower().startswith("currentuser")

        menu = QMenu(self)
        act_ver = menu.addAction("👁️ Ver certificado (visor de Windows)")
        act_exportar = menu.addAction("📤 Exportar a archivo (.cer)")
        menu.addSeparator()
        if es_current_user:
            act_eliminar = menu.addAction("✕ Eliminar certificado")
        else:
            act_eliminar = menu.addAction("✕ Eliminar certificado (solo disponible para CurrentUser)")
            act_eliminar.setEnabled(False)
        menu.addSeparator()
        act_copiar = menu.addAction("📋 Copiar fila")

        action = menu.exec_(self.certs_table.viewport().mapToGlobal(pos))

        if action in (act_ver, act_exportar, act_eliminar) and not huella:
            self._avisar("Sin huella digital", "Este certificado no tiene una huella digital válida.",
                         QMessageBox.Warning)
            return

        if action == act_ver:
            self._ver_certificado_nativo(asunto, huella, ubicacion)
        elif action == act_exportar:
            self._exportar_certificado_individual(asunto, huella, ubicacion)
        elif action == act_eliminar and es_current_user:
            self._eliminar_certificado(asunto, huella, ubicacion)
        elif action == act_copiar:
            self.copy_certs_selection()

    def _ver_certificado_nativo(self, asunto: str, huella: str, ubicacion: str):
        """Exporta el certificado a un fichero temporal y abre el visor
        NATIVO de Windows (el mismo que usa el Explorador al hacer doble
        clic sobre un .cer, vía cryptext.dll) — no un visor propio: ya
        muestra cadena de confianza, usos y huellas de forma fiable y
        localizada; reimplementarlo aquí sería peor y con más riesgo de
        error que abrir el que ya existe."""
        tmp_path = os.path.join(tempfile.gettempdir(), f"msys_cert_{huella}.cer")
        ok, salida = run_powershell_command_params(
            "Export-Certificate -Cert (Get-Item 'Cert:\\{ubicacion}\\{huella}') -FilePath '{ruta}' | Out-Null",
            ubicacion=ubicacion, huella=huella, ruta=tmp_path,
        )
        if not ok:
            self._avisar("Error", f"No se pudo preparar el certificado para verlo:\n\n{salida}", QMessageBox.Warning)
            return
        try:
            subprocess.Popen(["rundll32.exe", "cryptext.dll,CryptExtOpenCER", tmp_path])
            self.set_status(f"👁️ Abriendo visor de certificados para «{asunto}»...")
        except Exception as e:
            self._avisar("Error", f"No se pudo abrir el visor:\n\n{e}", QMessageBox.Critical)

    def _exportar_certificado_individual(self, asunto: str, huella: str, ubicacion: str):
        """Exporta SOLO la clave pública (Export-Certificate nunca expone
        clave privada) a un .cer que el usuario elige dónde guardar."""
        nombre_base = re.sub(r'[\\/:*?"<>|,=]+', '_', asunto).strip('_ ')[:60] or "certificado"
        default_name = f"{nombre_base}.cer"
        path = dialogo_guardar_archivo(self, "Exportar certificado",
                                        ruta_guardado_predeterminada(default_name), "Certificado (*.cer)")
        if not path:
            return

        ok, salida = run_powershell_command_params(
            "Export-Certificate -Cert (Get-Item 'Cert:\\{ubicacion}\\{huella}') -FilePath '{ruta}' | Out-Null",
            ubicacion=ubicacion, huella=huella, ruta=path,
        )
        if ok:
            self.set_status(f"✅ Certificado exportado: {os.path.basename(path)}")
            self._avisar("Éxito", f"Certificado exportado a:\n\n{path}")
        else:
            self._avisar("Error", f"No se pudo exportar el certificado:\n\n{salida}", QMessageBox.Warning)

    def _eliminar_certificado(self, asunto: str, huella: str, ubicacion: str):
        """Elimina el certificado del almacén — restringido a CurrentUser
        (ya filtrado en _ctx_certificados): LocalMachine afecta a todos
        los usuarios del equipo y normalmente exige administrador; para
        eso, usar certmgr.msc directamente en vez de esta acción rápida.
        Aviso reforzado si es un certificado raíz: quitarlo de la lista
        de confianza puede romper la validación de otros sitios/servicios
        que dependan de él."""
        explicacion = ("Esta acción quita el certificado del almacén — no es recuperable salvo que "
                       "vuelvas a importarlo desde su origen original.")
        if "root" in ubicacion.lower():
            explicacion += ("\n\n⚠️ Es un certificado RAÍZ (autoridad de confianza): quitarlo puede "
                            "hacer que Windows deje de confiar en sitios o servicios que dependan de "
                            "él. Solo hazlo si sabes con certeza que no hace falta.")

        if not self._confirmar_accion("Eliminar certificado", f"¿Eliminar el certificado?\n\n{asunto}\n\n{explicacion}"):
            return

        # 5.5.0: copia .cer (parte pública) antes de borrar; la clave privada no se puede exportar sin contraseña
        ok_copia, copia = self._copia_previa(
            "CERTIFICADOS", f"{huella}.cer",
            "Export-Certificate -Cert (Get-Item 'Cert:\\{ubicacion}\\{huella}') -FilePath '{ruta}' | Out-Null",
            ubicacion=ubicacion, huella=huella)
        if not ok_copia and not self._seguir_sin_copia("el certificado", copia):
            return
        ok, salida = run_powershell_command_params(
            "Remove-Item -Path 'Cert:\\{ubicacion}\\{huella}' -Force",
            ubicacion=ubicacion, huella=huella,
        )
        self._registrar_historial("🔐 Certificados", "Eliminar certificado", f"{asunto} ({ubicacion}\\{huella})", ok, salida)
        if ok:
            self.set_status(f"✅ Certificado eliminado: {asunto}")
            self.refresh_certificates()
        else:
            self._avisar("Error", f"No se pudo eliminar el certificado:\n\n{salida}", QMessageBox.Warning)

    def copy_certs_selection(self):
        """Copia certificados seleccionados al portapapeles."""
        selected_indexes = self.certs_table.selectionModel().selectedRows()
        if not selected_indexes:
            self._avisar("Copiar", "No hay certificados seleccionados.")
            return

        lines = []
        lines.append("=" * 80)
        lines.append("CERTIFICADOS")
        lines.append("=" * 80)
        lines.append("")

        for index in selected_indexes:
            row = index.row()
            asunto = str(self.certs_proxy.data(self.certs_proxy.index(row, 0), Qt.DisplayRole) or "")
            emisor = str(self.certs_proxy.data(self.certs_proxy.index(row, 1), Qt.DisplayRole) or "")
            valido_desde = str(self.certs_proxy.data(self.certs_proxy.index(row, 2), Qt.DisplayRole) or "")
            valido_hasta = str(self.certs_proxy.data(self.certs_proxy.index(row, 3), Qt.DisplayRole) or "")
            huella = str(self.certs_proxy.data(self.certs_proxy.index(row, 4), Qt.DisplayRole) or "")

            lines.append(f"Nombre: {asunto}")
            lines.append(f"  Emisor: {emisor}")
            lines.append(f"  Válido desde: {valido_desde}")
            lines.append(f"  Válido hasta: {valido_hasta}")
            lines.append(f"  Huella: {huella}")
            lines.append("")

        lines.append("=" * 80)

        text = "\n".join(lines)
        QApplication.clipboard().setText(text)
        self.set_status(f"✅ Copiados {len(selected_indexes)} certificados al portapapeles")

    def export_certs_csv(self):
        """Exporta certificados a CSV."""
        if self.certs_proxy.rowCount() == 0:
            self._avisar("Exportar CSV", "No hay certificados para exportar.")
            return

        default_name = f"Certificados_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        path = dialogo_guardar_archivo(self, "Guardar CSV", ruta_guardado_predeterminada(default_name), "CSV (*.csv)")
        if not path:
            return

        try:
            headers = CertificateTableModel.HEADERS
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = _SafeCsvWriter(csv.writer(f, delimiter=";"))
                writer.writerow(headers)
                for r in range(self.certs_proxy.rowCount()):
                    row_data = []
                    for c in range(len(headers)):
                        idx = self.certs_proxy.index(r, c)
                        val = self.certs_proxy.data(idx, Qt.DisplayRole)
                        row_data.append(str(val) if val else "")
                    writer.writerow(row_data)

            total_exported = self.certs_proxy.rowCount()
            self.set_status(f"✅ Exportado: {os.path.basename(path)} ({total_exported} certificados)")
            self._avisar("Éxito", f"Exportados {total_exported} certificados a:\n\n{path}")
        except Exception as e:
            self._avisar("Error", f"No se pudo exportar:\n\n{e}", QMessageBox.Critical)

    def export_certs_html(self):
        """Exporta certificados a HTML."""
        if self.certs_proxy.rowCount() == 0:
            self._avisar("Exportar HTML", "No hay certificados para exportar.")
            return

        default_name = f"Certificados_{time.strftime('%Y%m%d_%H%M%S')}.html"
        path = dialogo_guardar_archivo(self, "Guardar HTML", ruta_guardado_predeterminada(default_name), "HTML (*.html)")
        if not path:
            return

        try:
            headers = CertificateTableModel.HEADERS
            total_rows = self.certs_proxy.rowCount()

            rows_data = []
            for r in range(total_rows):
                row_data = []
                for c in range(len(headers)):
                    idx = self.certs_proxy.index(r, c)
                    val = self.certs_proxy.data(idx, Qt.DisplayRole)
                    row_data.append(str(val) if val else "")
                rows_data.append(row_data)

            html = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Certificados - {time.strftime('%Y-%m-%d %H:%M:%S')}</title>
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
        .badge {{ display: inline-block; padding: 4px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
        .badge-success {{ background: #d4edda; color: #155724; }}
        .badge-warning {{ background: #fff3cd; color: #856404; }}
        .badge-danger {{ background: #f8d7da; color: #721c24; }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Certificados Digitales</h1>
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
                for i, cell in enumerate(row):
                    cell = html_escape(str(cell))
                    if i == 7:  # Estado
                        if "valido" in cell.lower() or "válido" in cell.lower():
                            cell_html = f'<span class="badge badge-success">{cell}</span>'
                        elif "caducado" in cell.lower():
                            cell_html = f'<span class="badge badge-danger">{cell}</span>'
                        elif "proxima" in cell.lower() or "próxima" in cell.lower():
                            cell_html = f'<span class="badge badge-warning">{cell}</span>'
                        else:
                            cell_html = cell
                    else:
                        cell_html = cell
                    html += f"                    <td>{cell_html}</td>\n"
                html += "                </tr>\n"

            html += """            </tbody>
        </table>
    </div>
</body>
</html>
"""

            with open(path, "w", encoding="utf-8") as f:
                f.write(html)

            self.set_status(f"✅ Exportado: {os.path.basename(path)} ({total_rows} certificados)")
            self._avisar("Éxito", f"Exportados {total_rows} certificados a:\n\n{path}")
        except Exception as e:
            self._avisar("Error", f"No se pudo exportar:\n\n{e}", QMessageBox.Critical)

