# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña Cuentas Correo: tabla, refresco, exportar.
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
    QPushButton, QMessageBox, QTableView, QMenu, QCheckBox,
)

from msys.core.utils import (
    ruta_guardado_predeterminada, dialogo_guardar_archivo, dialogo_elegir_carpeta,
    get_informes_folder, _SafeCsvWriter,
)
from msys.models.rows import EmailAccountRow
from msys.models.table_models import EmailAccountTableModel, EmailAccountFilterProxy
from msys.widgets.delegates import _setup_header
from msys.collectors import (
    EmailAccountCollector, OutlookConfigBackupThread, OutlookAccountDatafileThread,
)
from msys.ui.email_provider_dialog import EmailProviderDetectDialog
from msys.ui.outlook_pst_dialog import OutlookPSTExportDialog
from msys.ui import piezas


class EmailMixin:
    def crear_tab_email_accounts(self):
        """Crea la pestaña de cuentas de correo."""
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

        self.search_email = QLineEdit()
        self.search_email.setPlaceholderText("🔎 Buscar cuentas...")
        self.search_email.textChanged.connect(self._apply_email_search)
        toolbar.addWidget(self.search_email, 3)

        toolbar.addStretch()

        self.btn_detectar_email = QPushButton("🔍 Detectar cuenta")
        self.btn_detectar_email.clicked.connect(self.abrir_detector_cuenta_correo)
        toolbar.addWidget(self.btn_detectar_email)

        self.btn_backup_outlook = QPushButton("💾 Backup configuración")
        self.btn_backup_outlook.clicked.connect(self.backup_configuracion_outlook)
        toolbar.addWidget(self.btn_backup_outlook)

        self.btn_exportar_pst = QPushButton("📤 Exportar a PST")
        self.btn_exportar_pst.clicked.connect(self.abrir_exportar_pst)
        toolbar.addWidget(self.btn_exportar_pst)

        self.btn_refresh_email = QPushButton("🔄 Actualizar")
        self.btn_refresh_email.setObjectName("primary")
        self.btn_refresh_email.clicked.connect(self.refresh_email_accounts)
        toolbar.addWidget(self.btn_refresh_email)

        layout.addLayout(toolbar)

        # ── Fila de filtros por tipo de cuenta ───────────────────────────
        filters_layout = QHBoxLayout()
        filters_layout.setSpacing(8)

        self.chk_email_exchange = QCheckBox("📧 Exchange")
        self.chk_email_exchange.setChecked(True)
        self.chk_email_exchange.stateChanged.connect(self.apply_email_filter)
        filters_layout.addWidget(self.chk_email_exchange)

        self.chk_email_imap = QCheckBox("📥 IMAP")
        self.chk_email_imap.setChecked(True)
        self.chk_email_imap.stateChanged.connect(self.apply_email_filter)
        filters_layout.addWidget(self.chk_email_imap)

        self.chk_email_pop3 = QCheckBox("📨 POP3")
        self.chk_email_pop3.setChecked(True)
        self.chk_email_pop3.stateChanged.connect(self.apply_email_filter)
        filters_layout.addWidget(self.chk_email_pop3)

        self.chk_email_otros = QCheckBox("❓ Otros")
        self.chk_email_otros.setChecked(True)
        self.chk_email_otros.stateChanged.connect(self.apply_email_filter)
        filters_layout.addWidget(self.chk_email_otros)

        filters_layout.addStretch()

        self.lbl_email_count = QLabel("Cuentas: 0/0")
        self.lbl_email_count.setObjectName("countLabel")
        filters_layout.addWidget(self.lbl_email_count)

        layout.addLayout(filters_layout)

        # Tabla
        self.email_model = EmailAccountTableModel([])
        self.email_proxy = EmailAccountFilterProxy(self)
        self.email_proxy.setSourceModel(self.email_model)
        self.email_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.email_proxy.setFilterKeyColumn(-1)

        self.email_table = QTableView()
        self.email_table.setModel(self.email_proxy)
        self.email_table.setAlternatingRowColors(True)
        self.email_table.setSelectionBehavior(QTableView.SelectRows)
        self.email_table.verticalHeader().setVisible(False)
        self.email_table.setSortingEnabled(True)

        _setup_header(self.email_table, 4, stretch_col=1)
        self.email_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.email_table.customContextMenuRequested.connect(self._ctx_email_accounts)

        layout.addWidget(self.email_table)

        # 4.9.0: la pestaña de siempre pasa a ser la primera de un grupo de pestañas, junto a la operativa de
        # MACorreo adaptada a MASys (msys/ui/correo.py y correo_outlook.py).
        self.correo_tabs = piezas.pestanas()
        self.correo_tabs.addTab(tab, "📧 Cuentas")
        self.crear_pestanas_correo(self.correo_tabs)
        return self.correo_tabs

    def refresh_email_accounts(self):
        """Actualiza lista de cuentas de correo."""
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.set_status("🔄 Buscando cuentas de correo...")
        self.btn_refresh_email.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.worker = EmailAccountCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_email_data)
        self.worker.finished_error.connect(self._on_email_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_email_data(self, rows: List[EmailAccountRow]):
        """Procesa datos de cuentas de correo."""
        self.email_model.setRows(rows)
        self.btn_refresh_email.setEnabled(True)
        self.progress_bar.setVisible(False)
        total = len(rows)
        self.set_status(f"✅ {total} cuentas de correo encontradas")
        self._update_email_counter()

    def _on_email_error(self, msg: str):
        """Maneja errores de cuentas de correo."""
        self.btn_refresh_email.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al buscar cuentas de correo")
        self._avisar("Error", f"No se pudieron obtener las cuentas de correo:\n\n{msg}", QMessageBox.Warning)

    def apply_email_filter(self):
        """Checkboxes de filtro por tipo de cuenta — mismo patrón
        persistente que Certificados/Navegadores (setFilters +
        invalidateFilter, sin recrear el proxy)."""
        self.email_proxy.setFilters(
            self.chk_email_exchange.isChecked(),
            self.chk_email_imap.isChecked(),
            self.chk_email_pop3.isChecked(),
            self.chk_email_otros.isChecked(),
        )
        self._update_email_counter()

    def _apply_email_search(self):
        """Búsqueda de texto — actualiza el contador visible también,
        no solo al recargar datos."""
        self.email_proxy.setFilterRegExp(
            QRegExp(self.search_email.text(), Qt.CaseInsensitive, QRegExp.FixedString))
        self._update_email_counter()

    def _update_email_counter(self):
        """Actualiza la etiqueta de contador visible en la barra."""
        self.lbl_email_count.setText(
            f"Cuentas: {self.email_proxy.rowCount()}/{self.email_model.rowCount()}")

    def abrir_detector_cuenta_correo(self):
        """Abre el diálogo de detección de configuración de cuenta de
        correo (IMAP/POP3/SMTP), portado de GestCorreov4.ps1."""
        dlg = EmailProviderDetectDialog(self)
        dlg.exec_()

    def backup_configuracion_outlook(self):
        """Backup de la configuración de Outlook del usuario actual:
        perfiles/cuentas/reglas (.reg) + firmas (.zip). Portado de
        GestCorreov4.ps1 (pestaña "Exportar Config")."""
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        carpeta = dialogo_elegir_carpeta(self, "Carpeta de destino del backup de Outlook", get_informes_folder())
        if not carpeta:
            return

        self.set_status("🔄 Generando backup de configuración de Outlook...")
        self.btn_backup_outlook.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.worker = OutlookConfigBackupThread(carpeta)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_backup_outlook_data)
        self.worker.finished_error.connect(self._on_backup_outlook_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_backup_outlook_data(self, archivos: List[str]):
        self.btn_backup_outlook.setEnabled(True)
        self.progress_bar.setVisible(False)
        lista = "\n".join(f"  • {os.path.basename(a)}" for a in archivos)
        self.set_status(f"✅ Backup de Outlook completado ({len(archivos)} archivo(s))")
        self._avisar("Backup completado", f"Se generaron {len(archivos)} archivo(s):\n\n{lista}")

    def _on_backup_outlook_error(self, msg: str):
        self.btn_backup_outlook.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al generar el backup de Outlook")
        self._avisar("Error", f"No se pudo generar el backup:\n\n{msg}", QMessageBox.Warning)

    def abrir_exportar_pst(self):
        """Abre el diálogo de exportación de cuentas de Outlook a .pst,
        portado de GestCorreov4.ps1 (pestaña "Exportar a PST")."""
        dlg = OutlookPSTExportDialog(self)
        dlg.exec_()

    def _ctx_email_accounts(self, pos):
        """Menú contextual: ver configuración completa por protocolo,
        probar la cuenta, exportar a PST, abrir la carpeta del archivo de
        datos y saltar a las credenciales relacionadas."""
        idx = self.email_table.indexAt(pos)
        if not idx.isValid():
            return
        self.email_table.selectRow(idx.row())
        source_row = self.email_proxy.mapToSource(idx).row()
        row = self.email_model.rows()[source_row]
        es_outlook = row.cliente.startswith("Outlook")

        menu = QMenu(self)
        act_ver_config = menu.addAction("⚙️ Ver configuración")
        act_probar = menu.addAction("🔑 Probar esta cuenta")
        menu.addSeparator()
        if es_outlook:
            act_exportar_pst = menu.addAction("📤 Exportar esta cuenta a PST")
            act_abrir_carpeta = menu.addAction("📂 Abrir carpeta del archivo de datos")
        else:
            act_exportar_pst = menu.addAction("📤 Exportar esta cuenta a PST (solo Outlook)")
            act_exportar_pst.setEnabled(False)
            act_abrir_carpeta = menu.addAction("📂 Abrir carpeta del archivo de datos (solo Outlook)")
            act_abrir_carpeta.setEnabled(False)
        menu.addSeparator()
        act_credenciales = menu.addAction("🔗 Ver credenciales relacionadas")
        menu.addSeparator()
        act_copiar = menu.addAction("📋 Copiar fila")

        action = menu.exec_(self.email_table.viewport().mapToGlobal(pos))

        if action == act_ver_config:
            self._ver_configuracion_cuenta(row)
        elif action == act_probar:
            self._probar_cuenta_seleccionada(row)
        elif action == act_exportar_pst and es_outlook:
            self._exportar_cuenta_a_pst(row)
        elif action == act_abrir_carpeta and es_outlook:
            self._abrir_carpeta_datos_cuenta(row)
        elif action == act_credenciales:
            self._ver_credenciales_relacionadas(row)
        elif action == act_copiar:
            self.copy_email_selection()

    def _ver_configuracion_cuenta(self, row: EmailAccountRow):
        """Muestra el detalle completo de servidores IMAP/POP3/SMTP de la
        cuenta seleccionada — la tabla solo muestra un "Servidor" genérico."""
        partes = [f"<b>{row.cuenta}</b><br>{row.cliente} — {row.tipo}<br><br>"]
        if row.imap_host:
            partes.append(f"<b>IMAP:</b> {row.imap_host}:{row.imap_port} "
                           f"({'SSL/TLS' if row.imap_ssl else 'sin cifrar'})<br>")
        if row.pop_host:
            partes.append(f"<b>POP3:</b> {row.pop_host}:{row.pop_port} "
                           f"({'SSL/TLS' if row.pop_ssl else 'sin cifrar'})<br>")
        if row.smtp_host:
            partes.append(f"<b>SMTP:</b> {row.smtp_host}:{row.smtp_port} "
                           f"({'SSL/TLS' if row.smtp_ssl else 'sin cifrar/STARTTLS'})<br>")
        if not (row.imap_host or row.pop_host or row.smtp_host):
            if row.cliente.startswith("Outlook"):
                explicacion = ("Esta cuenta usa Exchange/Modern Auth — no expone servidores "
                                "IMAP/POP3/SMTP tradicionales en el registro.")
            else:
                explicacion = "No se encontró detalle de servidor para esta cuenta."
            partes.append(f"Servidor: {row.servidor}<br><i>{explicacion}</i>")
        self._avisar("Configuración de la cuenta", "".join(partes))

    def _probar_cuenta_seleccionada(self, row: EmailAccountRow):
        dlg = EmailProviderDetectDialog(self, email_inicial=row.cuenta)
        dlg.exec_()

    def _exportar_cuenta_a_pst(self, row: EmailAccountRow):
        dlg = OutlookPSTExportDialog(self, cuenta_preseleccionada=row.cuenta)
        dlg.exec_()

    def _abrir_carpeta_datos_cuenta(self, row: EmailAccountRow):
        if getattr(self, "_datafile_thread", None) is not None and self._datafile_thread.isRunning():
            self.set_status("⏳ Ya hay una búsqueda de archivo de datos en curso...")
            return
        self.set_status(f"🔄 Buscando el archivo de datos de «{row.cuenta}»...")
        self._datafile_thread = OutlookAccountDatafileThread(row.cuenta)
        self._datafile_thread.finished_ok.connect(self._on_datafile_encontrado)
        self._datafile_thread.finished_error.connect(self._on_datafile_error)
        self._datafile_thread.start()

    def _on_datafile_encontrado(self, ruta: str):
        self.set_status(f"✅ Archivo de datos: {ruta}")
        if os.path.exists(ruta):
            # "/select," debe ir PEGADO a la ruta en el mismo argumento —
            # como dos elementos separados del argv, Explorer no asocia el
            # path al select y abre una ventana genérica sin seleccionar nada.
            subprocess.Popen(["explorer.exe", f"/select,{ruta}"])
        else:
            self._avisar("Abrir carpeta", f"Outlook indica esta ruta, pero el archivo no está "
                          f"accesible ahora mismo:\n\n{ruta}", QMessageBox.Warning)

    def _on_datafile_error(self, msg: str):
        self.set_status("❌ No se pudo localizar el archivo de datos")
        self._avisar("Abrir carpeta", f"No se pudo localizar el archivo de datos:\n\n{msg}", QMessageBox.Warning)

    def _ver_credenciales_relacionadas(self, row: EmailAccountRow):
        if hasattr(self, "seleccionar_seccion"):
            self.seleccionar_seccion("🔑 Credenciales")
        if hasattr(self, "search_creds"):
            self.search_creds.setText(row.cuenta)

    def copy_email_selection(self):
        """Copia cuentas de correo seleccionadas al portapapeles."""
        selected_indexes = self.email_table.selectionModel().selectedRows()
        if not selected_indexes:
            self._avisar("Copiar", "No hay cuentas seleccionadas.")
            return

        lines = []
        lines.append("=" * 80)
        lines.append("CUENTAS DE CORREO")
        lines.append("=" * 80)
        lines.append("")

        for index in selected_indexes:
            row = index.row()
            cliente = str(self.email_proxy.data(self.email_proxy.index(row, 0), Qt.DisplayRole) or "")
            cuenta = str(self.email_proxy.data(self.email_proxy.index(row, 1), Qt.DisplayRole) or "")
            servidor = str(self.email_proxy.data(self.email_proxy.index(row, 2), Qt.DisplayRole) or "")
            tipo = str(self.email_proxy.data(self.email_proxy.index(row, 3), Qt.DisplayRole) or "")

            lines.append(f"{cuenta}")
            lines.append(f"  Cliente: {cliente} | Tipo: {tipo}")
            lines.append(f"  Servidor: {servidor}")
            lines.append("")

        lines.append("=" * 80)

        text = "\n".join(lines)
        QApplication.clipboard().setText(text)
        self.set_status(f"✅ Copiadas {len(selected_indexes)} cuentas al portapapeles")

    def export_email_csv(self):
        """Exporta cuentas de correo a CSV."""
        if self.email_proxy.rowCount() == 0:
            self._avisar("Exportar CSV", "No hay cuentas para exportar.")
            return

        default_name = f"CuentasCorreo_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        path = dialogo_guardar_archivo(self, "Guardar CSV", ruta_guardado_predeterminada(default_name), "CSV (*.csv)")
        if not path:
            return

        try:
            headers = EmailAccountTableModel.HEADERS
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = _SafeCsvWriter(csv.writer(f, delimiter=";"))
                writer.writerow(headers)
                for r in range(self.email_proxy.rowCount()):
                    row_data = []
                    for c in range(len(headers)):
                        idx = self.email_proxy.index(r, c)
                        val = self.email_proxy.data(idx, Qt.DisplayRole)
                        row_data.append(str(val) if val else "")
                    writer.writerow(row_data)

            total_exported = self.email_proxy.rowCount()
            self.set_status(f"✅ Exportado: {os.path.basename(path)} ({total_exported} cuentas)")
            self._avisar("Éxito", f"Exportadas {total_exported} cuentas a:\n\n{path}")
        except Exception as e:
            self._avisar("Error", f"No se pudo exportar:\n\n{e}", QMessageBox.Critical)

    def export_email_html(self):
        """Exporta cuentas de correo a HTML."""
        if self.email_proxy.rowCount() == 0:
            self._avisar("Exportar HTML", "No hay cuentas para exportar.")
            return

        default_name = f"CuentasCorreo_{time.strftime('%Y%m%d_%H%M%S')}.html"
        path = dialogo_guardar_archivo(self, "Guardar HTML", ruta_guardado_predeterminada(default_name), "HTML (*.html)")
        if not path:
            return

        try:
            headers = EmailAccountTableModel.HEADERS
            total_rows = self.email_proxy.rowCount()

            rows_data = []
            for r in range(total_rows):
                row_data = []
                for c in range(len(headers)):
                    idx = self.email_proxy.index(r, c)
                    val = self.email_proxy.data(idx, Qt.DisplayRole)
                    row_data.append(str(val) if val else "")
                rows_data.append(row_data)

            html = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Cuentas de Correo - {time.strftime('%Y-%m-%d %H:%M:%S')}</title>
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
            <h1>Cuentas de Correo</h1>
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

            self.set_status(f"✅ Exportado: {os.path.basename(path)} ({total_rows} cuentas)")
            self._avisar("Éxito", f"Exportadas {total_rows} cuentas a:\n\n{path}")
        except Exception as e:
            self._avisar("Error", f"No se pudo exportar:\n\n{e}", QMessageBox.Critical)

