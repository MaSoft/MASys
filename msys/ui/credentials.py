# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña Credenciales: tabla, filtros, exportar CSV/HTML, probar/añadir/editar/eliminar credencial.
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
    QCheckBox, QPushButton, QMessageBox, QTableView, QMenu,
)

from msys.core.utils import ruta_guardado_predeterminada, dialogo_guardar_archivo, _SafeCsvWriter, run_system_command
from msys.models.rows import CredentialRow, SharePermissionRow, NetworkDriveRow
from msys.models.table_models import CredentialTableModel, CredentialFilterProxy
from msys.widgets.delegates import _setup_header
from msys.collectors import CredentialCollector
from msys.version import NOMBRE_VERSION



def _host_de_destino(destino: str) -> str:
    """Equipo de una credencial: «\\\\servidor\\recurso», «TERMSRV/servidor», «servidor.dominio»."""
    d = (destino or "").strip()
    if d.upper().startswith("TERMSRV/"):
        d = d[8:]
    d = d.lstrip("\\").split("\\")[0].split("/")[0]
    return d if d and " " not in d and "=" not in d and ":" not in d else ""


def _probar_destino_credencial(destino: str, host: str, es_rdp: bool) -> str:
    """Prueba real y sin escribir nada: el equipo responde (DNS + puerto 3389 o 445) y, si es un recurso
    compartido, Windows lo abre con las credenciales guardadas (Test-Path sobre la ruta UNC)."""
    import socket
    import subprocess
    try:
        ip = socket.gethostbyname(host)
    except OSError:
        return f"❌ No se encuentra el equipo «{host}» (no se resuelve su nombre)."
    puerto = 3389 if es_rdp else 445
    try:
        with socket.create_connection((ip, puerto), timeout=3):
            pass
    except OSError:
        return (f"❌ «{host}» ({ip}) no responde en el puerto {puerto} "
                f"({'Escritorio remoto' if es_rdp else 'archivos compartidos'}).")
    if es_rdp:
        return (f"✅ «{host}» ({ip}) responde en el puerto 3389 (Escritorio remoto).\n\n"
                "La contraseña solo se comprueba al conectar: «mstsc /v:" + host + "».")
    partes = destino.strip("\\").split("\\")
    if not destino.startswith("\\\\") or len(partes) < 2 or partes[1].upper() in ("IPC$", "ADMIN$"):
        # Solo el equipo: listar sus recursos compartidos con la credencial guardada (NetShareEnum)
        try:
            import win32net
            recursos, _t, _r = win32net.NetShareEnum(host, 0)
            nombres = [x.get("netname", "") for x in recursos if not x.get("netname", "").endswith("$")]
            return (f"✅ «{host}» acepta la credencial guardada. Recursos compartidos: "
                    f"{', '.join(nombres[:8]) if nombres else 'solo los administrativos'}.")
        except Exception as e:
            return f"⚠️ «{host}» responde, pero no deja ver sus recursos: {getattr(e, 'strerror', None) or e}"
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"if (Test-Path -LiteralPath '{destino.replace(chr(39), chr(39) * 2)}') {{ 'SI' }} else {{ 'NO' }}"],
                       capture_output=True, text=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
    if "SI" in (r.stdout or ""):
        return f"✅ «{destino}» se abre con las credenciales guardadas."
    return f"⚠️ «{host}» responde, pero «{destino}» no se puede abrir (credencial incorrecta, sin permiso o no existe)."

class CredencialesMixin:
    def crear_tab_credenciales(self):
        """Crea la pestaña de credenciales."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        # Primera línea: hostname + búsqueda + acciones + Actualizar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        import socket
        hostname = socket.gethostname()
        lbl_hostname_creds = QLabel(f"🖥️ {hostname}")
        lbl_hostname_creds.setObjectName("hostnameLabel")
        toolbar.addWidget(lbl_hostname_creds)

        self.search_creds = QLineEdit()
        self.search_creds.setPlaceholderText("🔎 Buscar credenciales...")
        self.search_creds.textChanged.connect(self.apply_creds_filter)
        toolbar.addWidget(self.search_creds, 3)

        self.btn_add_cred = QPushButton("➕ Agregar")
        self.btn_add_cred.setObjectName("ghost")
        self.btn_add_cred.clicked.connect(self.add_credential)
        toolbar.addWidget(self.btn_add_cred)

        self.btn_test_cred = QPushButton("🔍 Probar")
        self.btn_test_cred.setObjectName("ghost")
        self.btn_test_cred.setFixedWidth(110)
        self.btn_test_cred.clicked.connect(self.test_selected_credential)
        toolbar.addWidget(self.btn_test_cred)

        self.btn_delete_cred = QPushButton("✕ Eliminar")
        self.btn_delete_cred.setObjectName("danger")
        self.btn_delete_cred.setFixedWidth(110)
        self.btn_delete_cred.clicked.connect(self.delete_selected_credential)
        toolbar.addWidget(self.btn_delete_cred)

        toolbar.addStretch()

        self.btn_backup_creds = QPushButton("💾 Copia de seguridad (Windows)")
        self.btn_backup_creds.setObjectName("ghost")
        self.btn_backup_creds.setToolTip(
            "Abre el asistente NATIVO de Windows para hacer copia de seguridad o restaurar las "
            "credenciales del Administrador de Credenciales (las mismas que gestiona cmdkey).\n\n"
            "Pedirá pulsar Ctrl+Alt+Supr (medida de seguridad de Windows que ninguna aplicación "
            "puede saltarse) y una contraseña para proteger el archivo .crd generado.\n\n"
            "No incluye claves SSH, credenciales de Git ni archivos .rdp — esos no viven en el "
            "Administrador de Credenciales y se gestionan aparte."
        )
        self.btn_backup_creds.clicked.connect(self._abrir_menu_backup_credenciales)
        toolbar.addWidget(self.btn_backup_creds)

        self.btn_refresh_creds = QPushButton("🔄 Actualizar")
        self.btn_refresh_creds.setObjectName("primary")
        self.btn_refresh_creds.clicked.connect(self.refresh_credentials)
        toolbar.addWidget(self.btn_refresh_creds)

        layout.addLayout(toolbar)

        # Segunda línea: Filtros por categoría
        filters_layout = QHBoxLayout()
        filters_layout.setSpacing(15)

        self.chk_creds_rdp = QCheckBox("🖥️ Escritorio Remoto")
        self.chk_creds_rdp.setChecked(True)
        self.chk_creds_rdp.stateChanged.connect(self._on_creds_category_filter_changed)
        filters_layout.addWidget(self.chk_creds_rdp)

        self.chk_creds_red = QCheckBox("🌐 Recurso de Red")
        self.chk_creds_red.setChecked(True)
        self.chk_creds_red.stateChanged.connect(self._on_creds_category_filter_changed)
        filters_layout.addWidget(self.chk_creds_red)

        self.chk_creds_microsoft = QCheckBox("☁️ Microsoft/Office365")
        self.chk_creds_microsoft.setChecked(True)
        self.chk_creds_microsoft.stateChanged.connect(self._on_creds_category_filter_changed)
        filters_layout.addWidget(self.chk_creds_microsoft)

        self.chk_creds_dominio = QCheckBox("🔐 Contraseña de Dominio")
        self.chk_creds_dominio.setChecked(True)
        self.chk_creds_dominio.stateChanged.connect(self._on_creds_category_filter_changed)
        filters_layout.addWidget(self.chk_creds_dominio)

        self.chk_creds_generico = QCheckBox("📦 Genérico")
        self.chk_creds_generico.setChecked(True)
        self.chk_creds_generico.stateChanged.connect(self._on_creds_category_filter_changed)
        filters_layout.addWidget(self.chk_creds_generico)

        filters_layout.addStretch()

        self.lbl_creds_count = QLabel("Credenciales: 0/0")
        self.lbl_creds_count.setObjectName("countLabel")
        filters_layout.addWidget(self.lbl_creds_count)

        layout.addLayout(filters_layout)

        # Tabla de credenciales
        self.creds_model = CredentialTableModel([])

        self.creds_proxy = CredentialFilterProxy(self)
        self.creds_proxy.setSourceModel(self.creds_model)
        self.creds_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.creds_proxy.setFilterKeyColumn(-1)

        self.creds_table = QTableView()
        self.creds_table.setModel(self.creds_proxy)
        self.creds_table.setAlternatingRowColors(True)
        self.creds_table.setSelectionBehavior(QTableView.SelectRows)
        self.creds_table.verticalHeader().setVisible(False)
        self.creds_table.setSortingEnabled(True)
        self.creds_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.creds_table.customContextMenuRequested.connect(self._ctx_credenciales)

        _setup_header(self.creds_table, 9, stretch_col=0)

        layout.addWidget(self.creds_table)

        # Inicializar filtros (todas las categorías habilitadas por defecto)
        self._on_creds_category_filter_changed()

        return tab

    def _abrir_menu_backup_credenciales(self):
        """Ventana propia (estilo MASys) que explica la operación y
        ofrece Backup/Restauración — pero la propia lectura/escritura de
        las contraseñas la sigue haciendo credwiz.exe, la herramienta
        NATIVA de Windows. No es posible que esta ventana (ni ninguna otra
        aplicación) lea las contraseñas reales del Administrador de
        Credenciales: ni cmdkey ni la API pública de Credential Manager la
        exponen en texto plano, por diseño — solo credwiz.exe, con
        privilegio especial del sistema, puede hacerlo, y solo tras
        Ctrl+Alt+Supr (Secure Attention Sequence, que ninguna aplicación
        puede simular ni saltarse). Una 'ventana propia' que intentara
        hacer el export ella misma solo podría escribir los metadatos que
        ya se ven en la tabla (destino, usuario, tipo) — nunca la
        contraseña — y sería un backup falso: parecería completo pero no
        serviría para restaurar el acceso."""
        from PyQt5.QtWidgets import QDialog, QDialogButtonBox

        dialog = QDialog(self)
        dialog.setWindowTitle("Copia de seguridad de credenciales")
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setMinimumWidth(480)

        layout = QVBoxLayout(dialog)
        layout.setSpacing(14)
        layout.setContentsMargins(22, 20, 22, 18)

        lbl_titulo = QLabel("🔐 Copia de seguridad de credenciales de Windows")
        lbl_titulo.setStyleSheet("font-size: 15px; font-weight: 600;")
        layout.addWidget(lbl_titulo)

        lbl_info = QLabel(
            "Hace copia de seguridad o restaura las credenciales guardadas en el Administrador "
            "de Credenciales de Windows (las mismas que gestiona cmdkey).\n\n"
            "Al continuar se abrirá el asistente nativo de Windows: pedirá pulsar Ctrl+Alt+Supr "
            "(una protección de Windows que ninguna aplicación puede saltarse) y una contraseña "
            "para proteger el archivo .crd generado.\n\n"
            "No incluye claves SSH, credenciales de Git ni archivos .rdp — esos no viven en el "
            "Administrador de Credenciales y se gestionan aparte, desde esta misma pestaña."
        )
        lbl_info.setWordWrap(True)
        layout.addWidget(lbl_info)

        fila_botones = QHBoxLayout()
        fila_botones.setSpacing(8)
        btn_backup = QPushButton("💾 Hacer copia de seguridad...")
        btn_backup.setObjectName("primary")
        fila_botones.addWidget(btn_backup)
        btn_restore = QPushButton("📥 Restaurar desde copia...")
        fila_botones.addWidget(btn_restore)
        layout.addLayout(fila_botones)

        botones = QDialogButtonBox(QDialogButtonBox.Close)
        botones.rejected.connect(dialog.reject)
        botones.button(QDialogButtonBox.Close).clicked.connect(dialog.reject)
        layout.addWidget(botones)

        def _backup():
            dialog.accept()
            self._lanzar_credwiz("/backup")

        def _restore():
            dialog.accept()
            self._lanzar_credwiz("/restore")

        btn_backup.clicked.connect(_backup)
        btn_restore.clicked.connect(_restore)

        dialog.exec_()

    def _lanzar_credwiz(self, modo: str):
        ruta = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "credwiz.exe")
        if not os.path.exists(ruta):
            self._avisar(
                "No disponible",
                "No se encontró el asistente de copia de seguridad de credenciales de Windows "
                f"(credwiz.exe) en:\n\n{ruta}",
                QMessageBox.Warning,
            )
            return
        try:
            subprocess.Popen([ruta, modo])
            accion = "copia de seguridad" if modo == "/backup" else "restauración"
            self.set_status(f"🪟 Abriendo el asistente de {accion} de credenciales de Windows...")
        except Exception as e:
            self._avisar("Error", f"No se pudo abrir el asistente:\n\n{e}", QMessageBox.Critical)

    def _ctx_credenciales(self, pos):
        """Menú contextual con las acciones que no caben cómodamente en la
        barra (Editar) y accesos directos a las que sí están arriba."""
        idx = self.creds_table.indexAt(pos)
        if not idx.isValid():
            return
        self.creds_table.selectRow(idx.row())

        menu = QMenu(self)
        act_editar = menu.addAction("✏️ Editar credencial")
        act_probar = menu.addAction("🔍 Probar credencial")
        act_eliminar = menu.addAction("✕ Eliminar credencial")
        menu.addSeparator()
        act_copiar = menu.addAction("📋 Copiar fila")

        action = menu.exec_(self.creds_table.viewport().mapToGlobal(pos))
        if action == act_editar:
            self.edit_selected_credential()
        elif action == act_probar:
            self.test_selected_credential()
        elif action == act_eliminar:
            self.delete_selected_credential()
        elif action == act_copiar:
            self.copy_creds_selection()

    def apply_creds_filter(self):
        """Aplica filtro de búsqueda en credenciales."""
        pattern = self.search_creds.text()
        rx = QRegExp(pattern, Qt.CaseInsensitive, QRegExp.RegExp)
        self.creds_proxy.setFilterRegExp(rx)
        self._update_creds_counter()

    def _on_creds_category_filter_changed(self):
        """Actualiza filtros de categoría de credenciales."""
        enabled_categories = set()
        if self.chk_creds_rdp.isChecked():
            enabled_categories.add("Escritorio Remoto")
        if self.chk_creds_red.isChecked():
            enabled_categories.add("Recurso de Red")
        if self.chk_creds_microsoft.isChecked():
            enabled_categories.add("Microsoft/Office365")
        if self.chk_creds_dominio.isChecked():
            enabled_categories.add("Contraseña de Dominio")
        if self.chk_creds_generico.isChecked():
            enabled_categories.add("Genérico")
            enabled_categories.add("Control de Versiones")
            enabled_categories.add("Windows 365")
            enabled_categories.add("SSH")  # SSH keys incluidas en genérico

        self.creds_proxy.setEnabledCategories(enabled_categories)

        # Solo actualizar contador si lbl_status ya existe (después de __init__)
        if hasattr(self, 'lbl_status'):
            self._update_creds_counter()

    def _update_creds_counter(self):
        """Actualiza el contador de credenciales mostradas — tanto la
        etiqueta fija de la barra (visible siempre) como el mensaje de
        abajo (desaparece en cuanto pasa cualquier otra cosa)."""
        total = self.creds_model.rowCount()
        visible = self.creds_proxy.rowCount()
        self.lbl_creds_count.setText(f"Credenciales: {visible}/{total}")
        if total == visible:
            self.set_status(f"✅ {total} credenciales")
        else:
            self.set_status(f"✅ Mostrando {visible} de {total} credenciales")

    def copy_creds_selection(self):
        """Copia la selección de credenciales al portapapeles."""
        selected_indexes = self.creds_table.selectionModel().selectedRows()
        if not selected_indexes:
            self._avisar("Copiar", "No hay credenciales seleccionadas.")
            return

        lines = []
        lines.append("=" * 80)
        lines.append("CREDENCIALES DE WINDOWS")
        lines.append("=" * 80)
        lines.append("")

        for index in selected_indexes:
            row = index.row()
            destino = str(self.creds_proxy.data(self.creds_proxy.index(row, 0), Qt.DisplayRole) or "")
            usuario = str(self.creds_proxy.data(self.creds_proxy.index(row, 1), Qt.DisplayRole) or "")
            categoria = str(self.creds_proxy.data(self.creds_proxy.index(row, 2), Qt.DisplayRole) or "")
            tipo = str(self.creds_proxy.data(self.creds_proxy.index(row, 3), Qt.DisplayRole) or "")

            lines.append(f"Destino: {destino}")
            lines.append(f"  Usuario: {usuario}")
            lines.append(f"  Categoría: {categoria}")
            lines.append(f"  Tipo: {tipo}")
            lines.append("")

        lines.append("=" * 80)

        text = "\n".join(lines)
        QApplication.clipboard().setText(text)
        self.set_status(f"✅ Copiadas {len(selected_indexes)} credenciales al portapapeles")

    def export_creds_csv(self):
        """Exporta credenciales filtradas a CSV."""
        if self.creds_proxy.rowCount() == 0:
            self._avisar("Exportar CSV", "No hay credenciales para exportar.")
            return

        default_name = f"Credenciales_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        path = dialogo_guardar_archivo(self, "Guardar CSV", ruta_guardado_predeterminada(default_name), "CSV (*.csv)")
        if not path:
            return

        try:
            headers = CredentialTableModel.HEADERS
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = _SafeCsvWriter(csv.writer(f, delimiter=";"))
                writer.writerow(headers)
                for r in range(self.creds_proxy.rowCount()):
                    row_data = []
                    for c in range(len(headers)):
                        idx = self.creds_proxy.index(r, c)
                        val = self.creds_proxy.data(idx, Qt.DisplayRole)
                        row_data.append(str(val) if val else "")
                    writer.writerow(row_data)

            total_exported = self.creds_proxy.rowCount()
            self.set_status(f"✅ Exportado: {os.path.basename(path)} ({total_exported} credenciales)")
            self._avisar("Éxito", f"Exportadas {total_exported} credenciales a:\n\n{path}")
        except Exception as e:
            self._avisar("Error", f"No se pudo exportar:\n\n{e}", QMessageBox.Critical)

    def export_creds_html(self):
        """Exporta credenciales filtradas a HTML con diseño estilizado."""
        if self.creds_proxy.rowCount() == 0:
            self._avisar("Exportar HTML", "No hay credenciales para exportar.")
            return

        default_name = f"Credenciales_{time.strftime('%Y%m%d_%H%M%S')}.html"
        path = dialogo_guardar_archivo(self, "Guardar HTML", ruta_guardado_predeterminada(default_name), "HTML (*.html)")
        if not path:
            return

        try:
            import socket
            hostname = socket.gethostname()
            timestamp = time.strftime("%Y-%m-%d %H:%M:%S")

            html = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Credenciales de Windows - {hostname}</title>
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}
        body {{
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            padding: 20px;
            min-height: 100vh;
        }}
        .container {{
            max-width: 95%;
            width: auto;
            margin: 0 auto;
            background: white;
            border-radius: 15px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            overflow-x: auto;
        }}
        header {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 30px;
            text-align: center;
        }}
        header h1 {{
            font-size: 2.5em;
            margin-bottom: 10px;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.2);
        }}
        header p {{
            font-size: 1.1em;
            opacity: 0.9;
        }}
        .info-bar {{
            background: #f8f9fa;
            padding: 15px 30px;
            border-bottom: 1px solid #dee2e6;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
        }}
        .info-item {{
            display: flex;
            align-items: center;
            gap: 8px;
            margin: 5px 0;
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 0;
        }}
        thead {{
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            position: sticky;
            top: 0;
            z-index: 10;
        }}
        th {{
            padding: 15px;
            text-align: left;
            font-weight: 600;
            font-size: 0.9em;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }}
        td {{
            padding: 12px 15px;
            border-bottom: 1px solid #dee2e6;
            font-size: 0.95em;
        }}
        tbody tr {{
            transition: background-color 0.2s;
        }}
        tbody tr:nth-child(even) {{
            background-color: #f8f9fa;
        }}
        tbody tr:hover {{
            background-color: #e9ecef;
        }}
        .badge {{
            display: inline-block;
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 0.85em;
            font-weight: 600;
            text-align: center;
        }}
        .badge-rdp {{
            background-color: #d1ecf1;
            color: #0c5460;
        }}
        .badge-red {{
            background-color: #d4edda;
            color: #155724;
        }}
        .badge-microsoft {{
            background-color: #cfe2ff;
            color: #084298;
        }}
        .badge-dominio {{
            background-color: #f8d7da;
            color: #721c24;
        }}
        .badge-generico {{
            background-color: #e2e3e5;
            color: #383d41;
        }}
        .badge-ssh {{
            background-color: #fff3cd;
            color: #856404;
        }}
        .badge-git {{
            background-color: #f4e1ff;
            color: #6f42c1;
        }}
        .estado-valida {{
            background-color: #d4edda;
            color: #155724;
        }}
        .estado-invalida {{
            background-color: #f8d7da;
            color: #721c24;
        }}
        .estado-no-verificada {{
            background-color: #fff3cd;
            color: #856404;
        }}
        .password-cell {{
            font-family: 'Courier New', monospace;
            color: #6c757d;
            font-style: italic;
        }}
        footer {{
            text-align: center;
            padding: 20px;
            background: #f8f9fa;
            color: #6c757d;
            font-size: 0.9em;
        }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Credenciales de Windows</h1>
            <p>Reporte generado desde {NOMBRE_VERSION}</p>
        </header>

        <div class="info-bar">
            <div class="info-item">
                <strong>Equipo:</strong> {hostname}
            </div>
            <div class="info-item">
                <strong>Fecha:</strong> {timestamp}
            </div>
            <div class="info-item">
                <strong>Total:</strong> {self.creds_proxy.rowCount()} credenciales
            </div>
        </div>

        <table>
            <thead>
                <tr>
                    <th>Destino</th>
                    <th>Usuario</th>
                    <th>Categoría</th>
                    <th>Tipo</th>
                    <th>Contraseña</th>
                    <th>Última Modificación</th>
                    <th>Persistencia</th>
                    <th>Estado</th>
                    <th>Fuente</th>
                </tr>
            </thead>
            <tbody>
"""

            # Añadir filas de datos
            for r in range(self.creds_proxy.rowCount()):
                destino = html_escape(str(self.creds_proxy.data(self.creds_proxy.index(r, 0), Qt.DisplayRole) or ""))
                usuario = html_escape(str(self.creds_proxy.data(self.creds_proxy.index(r, 1), Qt.DisplayRole) or ""))
                categoria = html_escape(str(self.creds_proxy.data(self.creds_proxy.index(r, 2), Qt.DisplayRole) or ""))
                tipo = html_escape(str(self.creds_proxy.data(self.creds_proxy.index(r, 3), Qt.DisplayRole) or ""))
                password = html_escape(str(self.creds_proxy.data(self.creds_proxy.index(r, 4), Qt.DisplayRole) or ""))
                ultima_mod = html_escape(str(self.creds_proxy.data(self.creds_proxy.index(r, 5), Qt.DisplayRole) or ""))
                persistencia = html_escape(str(self.creds_proxy.data(self.creds_proxy.index(r, 6), Qt.DisplayRole) or ""))
                estado = html_escape(str(self.creds_proxy.data(self.creds_proxy.index(r, 7), Qt.DisplayRole) or ""))
                fuente = html_escape(str(self.creds_proxy.data(self.creds_proxy.index(r, 8), Qt.DisplayRole) or ""))

                # Determinar clase de badge según categoría
                cat_class = "badge-generico"
                if "Escritorio Remoto" in categoria:
                    cat_class = "badge-rdp"
                elif "Recurso de Red" in categoria:
                    cat_class = "badge-red"
                elif "Microsoft" in categoria or "Office365" in categoria:
                    cat_class = "badge-microsoft"
                elif "Dominio" in categoria:
                    cat_class = "badge-dominio"
                elif "SSH" in categoria:
                    cat_class = "badge-ssh"
                elif "Git" in categoria:
                    cat_class = "badge-git"

                # Determinar clase de estado
                estado_lower = estado.lower()
                estado_class = "badge estado-no-verificada"
                if "valida" in estado_lower or "verificada" in estado_lower:
                    estado_class = "badge estado-valida"
                elif "invalida" in estado_lower or "error" in estado_lower:
                    estado_class = "badge estado-invalida"

                html += f"""                <tr>
                    <td>{destino}</td>
                    <td>{usuario}</td>
                    <td><span class="badge {cat_class}">{categoria}</span></td>
                    <td>{tipo}</td>
                    <td class="password-cell">{password}</td>
                    <td>{ultima_mod}</td>
                    <td>{persistencia}</td>
                    <td><span class="{estado_class}">{estado}</span></td>
                    <td>{fuente}</td>
                </tr>
"""

            html += f"""            </tbody>
        </table>

        <footer>
            Generado por {NOMBRE_VERSION} - Herramienta de Auditoría de Windows
        </footer>
    </div>
</body>
</html>"""

            with open(path, "w", encoding="utf-8") as f:
                f.write(html)

            total_exported = self.creds_proxy.rowCount()
            self.set_status(f"✅ Exportado HTML: {os.path.basename(path)} ({total_exported} credenciales)")
            self._avisar("Éxito", f"Exportadas {total_exported} credenciales a HTML:\n\n{path}")
        except Exception as e:
            self._avisar("Error", f"No se pudo exportar HTML:\n\n{e}", QMessageBox.Critical)

    def test_selected_credential(self):
        """Prueba si la credencial seleccionada es válida."""
        selected_indexes = self.creds_table.selectionModel().selectedRows()
        if not selected_indexes:
            self._avisar("Probar Credencial", "Por favor, selecciona una credencial para probar.")
            return

        if len(selected_indexes) > 1:
            self._avisar("Probar Credencial", "Por favor, selecciona solo una credencial.")
            return

        index = selected_indexes[0]
        row = index.row()

        destino = str(self.creds_proxy.data(self.creds_proxy.index(row, 0), Qt.DisplayRole) or "")
        usuario = str(self.creds_proxy.data(self.creds_proxy.index(row, 1), Qt.DisplayRole) or "")
        categoria = str(self.creds_proxy.data(self.creds_proxy.index(row, 2), Qt.DisplayRole) or "")
        tipo = str(self.creds_proxy.data(self.creds_proxy.index(row, 3), Qt.DisplayRole) or "")
        fuente = str(self.creds_proxy.data(self.creds_proxy.index(row, 8), Qt.DisplayRole) or "")

        # 5.4.0: antes «Probar» solo enseñaba cómo probarla a mano. Ahora, para recursos de red y
        # Escritorio remoto, se comprueba de verdad (en segundo plano) y el resultado va delante.
        host = _host_de_destino(destino)
        if (destino.startswith("\\\\") or "Recurso de Red" in categoria or "RDP" in fuente
                or "TERMSRV" in tipo.upper() or "Escritorio Remoto" in categoria
                or "TERMSRV" in destino.upper()) and host:
            from msys.core import tareas
            es_rdp = "TERMSRV" in (tipo + destino).upper() or "RDP" in fuente or "Escritorio Remoto" in categoria
            self.set_status(f"🔄 Probando «{destino}»...")
            tareas.lanzar(self, "_tarea_probar_cred", _probar_destino_credencial, destino, host, es_rdp,
                          al_terminar=lambda r, d=destino, u=usuario: self._avisar(
                              "Prueba de credencial", f"Destino: {d}\nUsuario: {u}\n\n{r}"),
                          al_fallar=lambda m: self._avisar("Prueba de credencial", f"No se pudo probar:\n\n{m}",
                                                           QMessageBox.Warning))
            return

        # Determinar método de prueba según tipo, fuente y categoría
        if "SSH" in fuente or "SSH" in categoria:
            # SSH credential
            msg = f"Credencial SSH:\n\n"
            msg += f"Destino: {destino}\n"
            msg += f"Usuario: {usuario}\n"
            msg += f"Fuente: {fuente}\n\n"
            msg += "Nota: Para probar esta credencial SSH, usa:\n"
            msg += f"  ssh {usuario}@{destino}\n\n"
            msg += "La credencial se verificará al conectar."
            self._avisar("Prueba de Credencial - SSH", msg)

        elif "RDP" in fuente or "TERMSRV" in tipo.upper() or "Escritorio Remoto" in categoria:
            # RDP credential
            msg = f"Credencial de Escritorio Remoto (RDP):\n\n"
            msg += f"Destino: {destino}\n"
            msg += f"Usuario: {usuario}\n\n"
            msg += "Nota: Para probar esta credencial RDP, usa:\n"
            msg += f"  mstsc /v:{destino}\n\n"
            msg += "La credencial se verificará automáticamente al conectar."
            self._avisar("Prueba de Credencial - RDP", msg)

        elif "Git" in fuente or "Git" in categoria:
            # Git credential
            msg = f"Credencial Git:\n\n"
            msg += f"Destino: {destino}\n"
            msg += f"Usuario: {usuario}\n"
            msg += f"Fuente: {fuente}\n\n"
            msg += "Nota: Esta credencial se usa automáticamente por Git.\n"
            msg += "Para probar, ejecuta un comando git que requiera autenticación:\n"
            msg += f"  git ls-remote {destino}"
            self._avisar("Prueba de Credencial - Git", msg)

        elif destino.startswith("\\\\") or "Recurso de Red" in categoria:
            # Network share
            msg = f"Recurso de Red:\n\n"
            msg += f"Destino: {destino}\n"
            msg += f"Usuario: {usuario}\n\n"
            msg += "Nota: Para probar esta credencial de red, abre el Explorador de Windows y navega a:\n"
            msg += f"  {destino}\n\n"
            msg += "La credencial se verificará automáticamente."
            self._avisar("Prueba de Credencial - Red", msg)

        elif "DOMAIN" in tipo.upper() or "dominio" in tipo.lower():
            # Domain credential
            msg = f"Credencial de Dominio:\n\n"
            msg += f"Destino: {destino}\n"
            msg += f"Usuario: {usuario}\n"
            msg += f"Tipo: {tipo}\n\n"
            msg += "Nota: Esta es una credencial de dominio de Windows.\n"
            msg += "Se usa automáticamente para autenticación en recursos del dominio.\n"
            msg += "Para verificar, intenta acceder a un recurso del dominio."
            self._avisar("Prueba de Credencial - Dominio", msg)

        else:
            # Generic credential
            msg = f"Credencial Genérica:\n\n"
            msg += f"Destino: {destino}\n"
            msg += f"Usuario: {usuario}\n"
            msg += f"Categoría: {categoria}\n"
            msg += f"Tipo: {tipo}\n\n"
            msg += "Nota: Esta credencial se usa para autenticación en:\n"
            msg += f"  {destino}\n\n"
            msg += "Para probar, abre la aplicación o servicio correspondiente.\n"
            msg += "La credencial se verificará al usarla."
            self._avisar("Prueba de Credencial - Genérica", msg)

    def add_credential(self):
        """Abre diálogo para agregar nueva credencial al sistema."""
        from PyQt5.QtWidgets import QDialog, QFormLayout, QLineEdit, QComboBox, QDialogButtonBox

        dialog = QDialog(self)
        dialog.setWindowTitle("Agregar Nueva Credencial")
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setMinimumWidth(500)

        layout = QFormLayout(dialog)

        # Campos del formulario
        txt_destino = QLineEdit()
        txt_destino.setPlaceholderText("Ejemplo: \\\\servidor\\recurso o servidor.dominio.com")

        txt_usuario = QLineEdit()
        txt_usuario.setPlaceholderText("Ejemplo: DOMINIO\\usuario o usuario@dominio.com")

        txt_password = QLineEdit()
        txt_password.setEchoMode(QLineEdit.Password)
        txt_password.setPlaceholderText("Contraseña")

        cmb_tipo = QComboBox()
        cmb_tipo.addItems([
            "Credencial Genérica",
            "Credencial de Dominio",
            "Credencial de Recurso de Red"
        ])

        layout.addRow("Destino/Servidor:", txt_destino)
        layout.addRow("Usuario:", txt_usuario)
        layout.addRow("Contraseña:", txt_password)
        layout.addRow("Tipo:", cmb_tipo)

        # Botones
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)

        if dialog.exec_() == QDialog.Accepted:
            destino = txt_destino.text().strip()
            usuario = txt_usuario.text().strip()
            password = txt_password.text()
            tipo_idx = cmb_tipo.currentIndex()

            if not destino or not usuario or not password:
                self._avisar("Datos Incompletos", "Por favor, completa todos los campos obligatorios.", QMessageBox.Warning)
                return

            # cmdkey solo distingue dos tipos reales de destino: /generic
            # (credencial genérica) y /add (credencial de dominio — el
            # mismo modificador para "Dominio" y "Recurso de Red", según
            # la propia ayuda de cmdkey: no existe un /network aparte).
            # Antes se calculaba un "tipo_cmd" con /domain y /network, que
            # NO SON modificadores válidos de cmdkey, y ni siquiera se
            # llegaba a usar — el comando real llamaba siempre a /add.
            #
            # Además: /persistent:yes|no (que SIEMPRE se añadía, con
            # /persistent:yes por defecto ya que el checkbox nacía
            # marcado) NO es un modificador reconocido por cmdkey en
            # Windows 10/11 actuales — comprobado en vivo: cualquier uso
            # de /persistent hace fallar el comando entero con "parámetro
            # de comando duplicado". Es decir, "Agregar credencial" fallaba
            # SIEMPRE con la configuración por defecto. Eliminado — cmdkey
            # ya guarda las credenciales de forma persistente sin él.
            modificador_destino = "/generic" if tipo_idx == 0 else "/add"

            # Invocar cmdkey.exe directamente (sin pasar por powershell -Command):
            # destino/usuario/contraseña van como argumentos de proceso reales,
            # no como texto interpolado en un script que un interprete parsea.
            # Antes se montaba un -Command con comillas dobles sin escapar —
            # una contraseña con `" ; comando ; "` ejecutaba lo que fuera con
            # los mismos privilegios que MASys (normalmente administrador).
            ok, salida_o_error = run_system_command(
                ["cmdkey", f"{modificador_destino}:{destino}", f"/user:{usuario}", f"/pass:{password}"],
                timeout=10,
            )
            self._registrar_historial("🔑 Credenciales", "Agregar credencial", f"{destino} ({usuario})", ok,
                                      "" if ok else str(salida_o_error))
            if ok:
                self._avisar("Éxito", f"Credencial agregada correctamente:\n\n{destino}\nUsuario: {usuario}")
                self.refresh_credentials()
            else:
                self._avisar("Error", f"No se pudo agregar la credencial:\n\n{salida_o_error}", QMessageBox.Critical)

    def edit_selected_credential(self):
        """Permite editar la credencial seleccionada."""
        selected_indexes = self.creds_table.selectionModel().selectedRows()
        if not selected_indexes:
            self._avisar("Editar Credencial", "Por favor, selecciona una credencial para editar.")
            return

        if len(selected_indexes) > 1:
            self._avisar("Editar Credencial", "Por favor, selecciona solo una credencial.")
            return

        index = selected_indexes[0]
        row = index.row()

        destino = str(self.creds_proxy.data(self.creds_proxy.index(row, 0), Qt.DisplayRole) or "")
        usuario_actual = str(self.creds_proxy.data(self.creds_proxy.index(row, 1), Qt.DisplayRole) or "")
        fuente = str(self.creds_proxy.data(self.creds_proxy.index(row, 8), Qt.DisplayRole) or "")
        tipo_actual = str(self.creds_proxy.data(self.creds_proxy.index(row, 3), Qt.DisplayRole) or "")

        # Solo permitir editar credenciales del Credential Manager
        if "Credential Manager" not in fuente:
            self._avisar("Editar Credencial", f"Solo se pueden editar credenciales del Credential Manager.\n\nFuente actual: {fuente}", QMessageBox.Warning)
            return

        from PyQt5.QtWidgets import QDialog, QFormLayout, QLineEdit, QDialogButtonBox

        dialog = QDialog(self)
        dialog.setWindowTitle(f"Editar Credencial - {destino}")
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setMinimumWidth(500)

        layout = QFormLayout(dialog)

        # Mostrar destino (no editable)
        lbl_destino = QLabel(destino)
        lbl_destino.setStyleSheet("font-weight: bold;")
        layout.addRow("Destino:", lbl_destino)

        # Campo de usuario
        txt_usuario = QLineEdit(usuario_actual)
        layout.addRow("Usuario:", txt_usuario)

        # Campo de contraseña
        txt_password = QLineEdit()
        txt_password.setEchoMode(QLineEdit.Password)
        txt_password.setPlaceholderText("Nueva contraseña (dejar vacío para no cambiar)")
        layout.addRow("Nueva Contraseña:", txt_password)

        # Botones
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addRow(buttons)

        if dialog.exec_() == QDialog.Accepted:
            nuevo_usuario = txt_usuario.text().strip()
            nueva_password = txt_password.text()

            if not nuevo_usuario:
                self._avisar("Datos Incompletos", "El usuario no puede estar vacío.", QMessageBox.Warning)
                return

            # Windows no permite recuperar la contraseña actual de una
            # credencial guardada (ni cmdkey ni la API de Credential
            # Manager la exponen en texto plano) — no existe forma de
            # "solo cambiar el usuario" conservándola, así que se exige
            # directamente en vez de ofrecer una confirmación que no
            # llevaría a ningún resultado real.
            if not nueva_password:
                self._avisar(
                    "Falta la contraseña",
                    "Windows no permite recuperar la contraseña actual de una credencial guardada, "
                    "así que no es posible cambiar solo el usuario conservándola.\n\n"
                    "Para actualizar esta credencial, indica también la nueva contraseña.",
                    QMessageBox.Warning,
                )
                return

            # Actualizar credencial (eliminar y reagregar). cmdkey.exe
            # invocado directamente (mismo motivo que en add_credential:
            # nada de interpolar en un -Command de PowerShell sin escapar).
            # 5.4.0: (1) se conserva el tipo: antes SIEMPRE se volvía a crear con /add y una credencial
            # genérica pasaba a «Contraseña de dominio» (probado con una credencial de prueba);
            # (2) ya no se borra antes: cmdkey sobrescribe la credencial del mismo destino, así que si
            # la escritura falla la original sigue intacta (antes se perdía).
            generica = "gen" in tipo_actual.lower()
            ok, salida_o_error = run_system_command(
                ["cmdkey", f"{'/generic' if generica else '/add'}:{destino}", f"/user:{nuevo_usuario}",
                 f"/pass:{nueva_password}"],
                timeout=10,
            )
            self._registrar_historial("🔑 Credenciales", "Editar credencial", destino, ok,
                                      "" if ok else str(salida_o_error))
            if ok:
                self._avisar("Éxito", f"Credencial actualizada correctamente:\n\n{destino}")
            else:
                self._avisar("Advertencia", f"Resultado de la operación:\n\n{salida_o_error}", QMessageBox.Warning)
            self.refresh_credentials()

    def delete_selected_credential(self):
        """Elimina la credencial seleccionada del sistema."""
        selected_indexes = self.creds_table.selectionModel().selectedRows()
        if not selected_indexes:
            self._avisar("Eliminar Credencial", "Por favor, selecciona una credencial para eliminar.")
            return

        # Obtener información de las credenciales seleccionadas
        creds_to_delete = []
        for index in selected_indexes:
            row = index.row()
            destino = str(self.creds_proxy.data(self.creds_proxy.index(row, 0), Qt.DisplayRole) or "")
            usuario = str(self.creds_proxy.data(self.creds_proxy.index(row, 1), Qt.DisplayRole) or "")
            fuente = str(self.creds_proxy.data(self.creds_proxy.index(row, 8), Qt.DisplayRole) or "")

            # Solo permitir eliminar credenciales del Credential Manager
            if "Credential Manager" in fuente:
                creds_to_delete.append((destino, usuario))

        if not creds_to_delete:
            self._avisar("Eliminar Credencial", "Solo se pueden eliminar credenciales del Credential Manager.\n\nLas credenciales de SSH, Git, RDP, etc. deben eliminarse manualmente.", QMessageBox.Warning)
            return

        # Confirmar eliminación
        msg = f"¿Está seguro de eliminar {len(creds_to_delete)} credencial(es)?\n\n"
        for destino, usuario in creds_to_delete[:5]:  # Mostrar máximo 5
            msg += f"• {destino} ({usuario})\n"
        if len(creds_to_delete) > 5:
            msg += f"\n... y {len(creds_to_delete) - 5} más"

        if not self._confirmar_accion("Confirmar Eliminación", msg):
            return

        # Eliminar credenciales
        deleted_count = 0
        errors = []

        for destino, usuario in creds_to_delete:
            ok, salida_o_error = run_system_command(["cmdkey", f"/delete:{destino}"], timeout=10)
            self._registrar_historial("🔑 Credenciales", "Eliminar credencial", f"{destino} ({usuario})", ok,
                                      "" if ok else str(salida_o_error))
            if ok:
                deleted_count += 1
            else:
                errors.append(f"{destino}: {salida_o_error}")

        # Refrescar lista
        self.refresh_credentials()

        # Mostrar resultado
        if errors:
            msg = f"Se eliminaron {deleted_count} de {len(creds_to_delete)} credenciales.\n\nErrores:\n\n"
            msg += "\n".join(errors[:5])
            if len(errors) > 5:
                msg += f"\n\n... y {len(errors) - 5} errores más"
            self._avisar("Eliminación Parcial", msg, QMessageBox.Warning)
        else:
            self._avisar("Éxito", f"Se eliminaron correctamente {deleted_count} credencial(es).")
            self.set_status(f"✅ Eliminadas {deleted_count} credenciales")

    def refresh_credentials(self):
        """Actualiza lista de credenciales."""
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.set_status("🔄 Cargando credenciales...")
        self.btn_refresh_creds.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.worker = CredentialCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_creds_data)
        self.worker.finished_error.connect(self._on_creds_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_progress(self, percent: int, message: str):
        """Actualiza progreso."""
        self.progress_bar.setValue(percent)
        self.set_status(f"🔄 {message}")

    def _on_shares_data(self, rows: List[SharePermissionRow]):
        """Procesa datos de compartidas."""
        self.model.setRows(rows)
        # Rehabilitar controles específicos
        self.btn_analyze.setEnabled(True)
        self.btn_runas_shares.setEnabled(True)
        self.btn_browse.setEnabled(True)
        self.txt_path.setEnabled(True)
        self.chk_hide_admin.setEnabled(True)
        self.chk_include_hidden.setEnabled(True)
        self.chk_only_users.setEnabled(True)
        self.spin_depth.setEnabled(True)
        self.btn_stop.setEnabled(False)  # Deshabilitar botón Parar
        self.progress_bar.setVisible(False)
        # Actualizar contador con filtros
        self._update_shares_counter()
        # Refrescar vista activa automáticamente
        if self.view_stack.currentIndex() == 0:
            self._populate_hierarchical_tree()
        else:
            self._populate_folder_tree()

    def _on_drives_data(self, rows: List[NetworkDriveRow]):
        """Procesa datos de unidades remotas."""
        self.drives_model.setRows(rows)
        self.btn_refresh_drives.setEnabled(True)
        self.progress_bar.setVisible(False)
        self._update_drives_counter()
        # Poblar filtros dinámicos
        self._populate_drive_filters(rows)

    def _on_drives_error(self, msg: str):
        """Maneja errores de unidades remotas."""
        self.btn_refresh_drives.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al cargar unidades remotas")
        self._avisar("Error", f"No se pudieron cargar las unidades:\n\n{msg}", QMessageBox.Warning)

    def _on_creds_data(self, rows: List[CredentialRow]):
        """Procesa datos de credenciales."""
        self.creds_model.setRows(rows)
        self.btn_refresh_creds.setEnabled(True)
        self.progress_bar.setVisible(False)
        self._update_creds_counter()
        # No confundir "no se pudo consultar Credential Manager" (fallo real
        # del proceso de PowerShell) con "0 credenciales de verdad" — sin
        # esto, ambos casos mostraban el mismo contador silencioso.
        if getattr(self.worker, 'credential_manager_fallo', False):
            self.set_status(
                "⚠️ Credenciales cargadas, pero no se pudo consultar el Administrador de "
                "Credenciales (Credential Manager) de Windows — los datos de SSH/Git/RDP sí son "
                "completos.")

    def _on_creds_error(self, msg: str):
        """Maneja errores de credenciales."""
        self.btn_refresh_creds.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al cargar credenciales")
        self._avisar("Error", f"No se pudieron cargar las credenciales:\n\n{msg}", QMessageBox.Warning)

