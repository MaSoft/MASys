# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña Navegadores y Nube — DOS tablas
independientes: "🌐 Navegadores" (Firefox/Chrome/Edge) arriba y "☁️ Nube"
(OneDrive, y en el futuro Google Drive/Dropbox si se detectan) debajo.
Antes era una única tabla mezclando ambos conceptos con filtros de
cliente; se separó porque son datos de naturaleza distinta (cuentas de
navegador vs. almacenamiento en la nube), no una sola categoría con
subtipos — ver PS_GET_BROWSER_CLOUD_ACCOUNTS para el origen de los datos,
que sigue siendo una única consulta, repartida aquí en dos modelos.

Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

import os
import re
import csv
import time
import subprocess
from typing import List
from html import escape as html_escape

from PyQt5.QtCore import Qt, QRegExp
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QMessageBox, QTableView, QCheckBox, QMenu, QSplitter,
)

from msys.core import informe_html
from msys.core.utils import (
    ruta_guardado_predeterminada, dialogo_guardar_archivo, _SafeCsvWriter,
    run_powershell_json_params,
)
from msys.models.rows import BrowserCloudAccountRow
from msys.models.table_models import (
    BrowserCloudAccountTableModel, NavegadoresFilterProxy, NubeFilterProxy,
)
from msys.widgets.delegates import _setup_header
from msys.collectors import BrowserCloudAccountCollector, BrowserProfileSizeThread


def _es_cuenta_nube(cliente: str) -> bool:
    """Único punto de verdad para decidir si una fila del collector va a
    la tabla Navegadores o a la tabla Nube."""
    cliente_lower = (cliente or "").lower()
    return any(p in cliente_lower for p in ("onedrive", "drive", "dropbox"))


class NavegadoresNubeMixin:
    def crear_tab_navegadores_nube(self):
        """Crea la pestaña con las dos tablas independientes."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(10)
        layout.setContentsMargins(10, 10, 10, 10)

        # ── Barra superior compartida ────────────────────────────────────
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        import socket
        hostname = socket.gethostname()
        lbl_hostname = QLabel(f"🖥️ {hostname}")
        lbl_hostname.setObjectName("hostnameLabel")
        toolbar.addWidget(lbl_hostname)

        toolbar.addStretch()

        self.btn_marcadores = QPushButton("🔖 Favoritos")
        self.btn_marcadores.setToolTip("Gestor de favoritos: editar, comprobar enlaces rotos, reorganizar, "
                                       "exportar/importar y sustituirlos en el navegador")
        self.btn_marcadores.clicked.connect(self.abrir_marcadores)
        toolbar.addWidget(self.btn_marcadores)

        self.btn_historial = QPushButton("📜 Historial")
        self.btn_historial.clicked.connect(self.abrir_historial)
        toolbar.addWidget(self.btn_historial)

        self.btn_descargas = QPushButton("⬇️ Descargas")
        self.btn_descargas.clicked.connect(self.abrir_descargas)
        toolbar.addWidget(self.btn_descargas)

        self.btn_extensiones = QPushButton("🧩 Extensiones")
        self.btn_extensiones.clicked.connect(self.abrir_extensiones)
        toolbar.addWidget(self.btn_extensiones)

        self.btn_passwords_navegador = QPushButton("🔑 Contraseñas guardadas")
        self.btn_passwords_navegador.clicked.connect(self.abrir_passwords_navegador)
        toolbar.addWidget(self.btn_passwords_navegador)

        self.btn_auditar_passwords = QPushButton("🛡️ Auditar contraseñas")
        self.btn_auditar_passwords.setToolTip("Fortaleza, reutilización y exposición en filtraciones (Have I Been Pwned)")
        self.btn_auditar_passwords.clicked.connect(self.abrir_auditoria_passwords)
        toolbar.addWidget(self.btn_auditar_passwords)

        self.btn_refresh_navegadores_nube = QPushButton("🔄 Actualizar")
        self.btn_refresh_navegadores_nube.setObjectName("primary")
        self.btn_refresh_navegadores_nube.clicked.connect(self.refresh_navegadores_nube)
        toolbar.addWidget(self.btn_refresh_navegadores_nube)

        layout.addLayout(toolbar)

        # Dos tablas independientes en un splitter vertical (mismo patrón
        # que Discos Duros): el usuario puede redimensionar o colapsar cada
        # sección con el tirador, en vez de repartirse el espacio a partes
        # fijas siempre.
        splitter = QSplitter(Qt.Vertical)

        nav_container = QWidget()
        nav_layout = QVBoxLayout(nav_container)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(6)

        # ── Sección "Navegadores" ────────────────────────────────────────
        lbl_seccion_nav = QLabel("🌐 Navegadores")
        lbl_seccion_nav.setObjectName("fieldLabel")
        nav_layout.addWidget(lbl_seccion_nav)

        filtros_nav = QHBoxLayout()
        filtros_nav.setSpacing(8)
        self.search_navegadores = QLineEdit()
        self.search_navegadores.setPlaceholderText("🔎 Buscar en navegadores...")
        self.search_navegadores.textChanged.connect(self._apply_navegadores_search)
        filtros_nav.addWidget(self.search_navegadores, 3)

        self.chk_nav_firefox = QCheckBox("🦊 Firefox")
        self.chk_nav_firefox.setChecked(True)
        self.chk_nav_firefox.stateChanged.connect(self.apply_navegadores_filter)
        filtros_nav.addWidget(self.chk_nav_firefox)

        self.chk_nav_chromium = QCheckBox("🌐 Chrome / Edge")
        self.chk_nav_chromium.setChecked(True)
        self.chk_nav_chromium.stateChanged.connect(self.apply_navegadores_filter)
        filtros_nav.addWidget(self.chk_nav_chromium)

        filtros_nav.addStretch()
        self.lbl_navegadores_count = QLabel("Cuentas: 0/0")
        self.lbl_navegadores_count.setObjectName("countLabel")
        filtros_nav.addWidget(self.lbl_navegadores_count)
        nav_layout.addLayout(filtros_nav)

        self.navegadores_model = BrowserCloudAccountTableModel([])
        self.navegadores_proxy = NavegadoresFilterProxy(self)
        self.navegadores_proxy.setSourceModel(self.navegadores_model)
        self.navegadores_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.navegadores_proxy.setFilterKeyColumn(-1)

        self.navegadores_table = QTableView()
        self.navegadores_table.setModel(self.navegadores_proxy)
        self.navegadores_table.setAlternatingRowColors(True)
        self.navegadores_table.setSelectionBehavior(QTableView.SelectRows)
        self.navegadores_table.verticalHeader().setVisible(False)
        self.navegadores_table.setSortingEnabled(True)
        _setup_header(self.navegadores_table, 4, stretch_col=1)
        self.navegadores_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.navegadores_table.customContextMenuRequested.connect(self._ctx_navegadores)
        nav_layout.addWidget(self.navegadores_table, 1)

        splitter.addWidget(nav_container)

        nube_container = QWidget()
        nube_layout = QVBoxLayout(nube_container)
        nube_layout.setContentsMargins(0, 0, 0, 0)
        nube_layout.setSpacing(6)

        # ── Sección "Nube" ───────────────────────────────────────────────
        lbl_seccion_nube = QLabel("☁️ Almacenamiento en la nube")
        lbl_seccion_nube.setObjectName("fieldLabel")
        nube_layout.addWidget(lbl_seccion_nube)

        filtros_nube = QHBoxLayout()
        filtros_nube.setSpacing(8)
        self.search_nube = QLineEdit()
        self.search_nube.setPlaceholderText("🔎 Buscar en la nube...")
        self.search_nube.textChanged.connect(self._apply_nube_search)
        filtros_nube.addWidget(self.search_nube, 3)

        self.chk_nube_onedrive = QCheckBox("☁️ OneDrive")
        self.chk_nube_onedrive.setChecked(True)
        self.chk_nube_onedrive.stateChanged.connect(self.apply_nube_filter)
        filtros_nube.addWidget(self.chk_nube_onedrive)

        self.chk_nube_dropbox = QCheckBox("📦 Dropbox")
        self.chk_nube_dropbox.setChecked(True)
        self.chk_nube_dropbox.stateChanged.connect(self.apply_nube_filter)
        filtros_nube.addWidget(self.chk_nube_dropbox)

        self.chk_nube_google_drive = QCheckBox("🔷 Google Drive")
        self.chk_nube_google_drive.setChecked(True)
        self.chk_nube_google_drive.stateChanged.connect(self.apply_nube_filter)
        filtros_nube.addWidget(self.chk_nube_google_drive)

        self.chk_nube_otros = QCheckBox("❓ Otros")
        self.chk_nube_otros.setChecked(True)
        self.chk_nube_otros.stateChanged.connect(self.apply_nube_filter)
        filtros_nube.addWidget(self.chk_nube_otros)

        filtros_nube.addStretch()
        self.lbl_nube_count = QLabel("Cuentas: 0/0")
        self.lbl_nube_count.setObjectName("countLabel")
        filtros_nube.addWidget(self.lbl_nube_count)
        nube_layout.addLayout(filtros_nube)

        self.nube_model = BrowserCloudAccountTableModel([])
        self.nube_proxy = NubeFilterProxy(self)
        self.nube_proxy.setSourceModel(self.nube_model)
        self.nube_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.nube_proxy.setFilterKeyColumn(-1)

        self.nube_table = QTableView()
        self.nube_table.setModel(self.nube_proxy)
        self.nube_table.setAlternatingRowColors(True)
        self.nube_table.setSelectionBehavior(QTableView.SelectRows)
        self.nube_table.verticalHeader().setVisible(False)
        self.nube_table.setSortingEnabled(True)
        _setup_header(self.nube_table, 4, stretch_col=1)
        self.nube_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.nube_table.customContextMenuRequested.connect(self._ctx_nube)
        nube_layout.addWidget(self.nube_table, 1)

        splitter.addWidget(nube_container)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

        return tab

    def abrir_marcadores(self):
        from msys.ui.favoritos_dialog import abrir_gestor_favoritos
        abrir_gestor_favoritos(self)

    def abrir_historial(self):
        from msys.ui.browser_data_dialog import abrir_dialogo_historial
        abrir_dialogo_historial(self)

    def abrir_descargas(self):
        from msys.ui.browser_data_dialog import abrir_dialogo_descargas
        abrir_dialogo_descargas(self)

    def abrir_extensiones(self):
        from msys.ui.browser_data_dialog import abrir_dialogo_extensiones
        abrir_dialogo_extensiones(self)

    def abrir_passwords_navegador(self):
        from msys.ui.browser_data_dialog import abrir_dialogo_passwords
        abrir_dialogo_passwords(self)

    def abrir_auditoria_passwords(self):
        from msys.ui.passwords_auditoria_dialog import abrir_auditoria_passwords
        abrir_auditoria_passwords(self)

    def apply_navegadores_filter(self):
        """Checkboxes de filtro por cliente — patrón persistente
        (setFilters + invalidateFilter, sin recrear el proxy)."""
        self.navegadores_proxy.setFilters(
            self.chk_nav_firefox.isChecked(),
            self.chk_nav_chromium.isChecked(),
        )
        self._update_navegadores_counter()

    def apply_nube_filter(self):
        """Checkboxes de filtro por proveedor de nube — mismo patrón
        persistente."""
        self.nube_proxy.setFilters(
            self.chk_nube_onedrive.isChecked(),
            self.chk_nube_dropbox.isChecked(),
            self.chk_nube_google_drive.isChecked(),
            self.chk_nube_otros.isChecked(),
        )
        self._update_nube_counter()

    def refresh_navegadores_nube(self):
        """Actualiza ambas tablas (Navegadores y Nube) desde una única
        consulta al collector."""
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.set_status("🔄 Buscando cuentas de navegadores y nube...")
        self.btn_refresh_navegadores_nube.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.worker = BrowserCloudAccountCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_navegadores_nube_data)
        self.worker.finished_error.connect(self._on_navegadores_nube_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

        # Cálculo de tamaño de perfil (os.walk de varios GB) SIEMPRE en su
        # propio hilo aparte, nunca en el detector de Soporte (bug real
        # corregido — ver BrowserProfileSizeThread). No comparte el slot
        # self.worker: es independiente y no bloquea el refresco principal.
        if getattr(self, "_size_thread", None) is None or not self._size_thread.isRunning():
            self._size_thread = BrowserProfileSizeThread()
            self._size_thread.finished_ok.connect(self._on_tamanos_perfiles_navegador)
            self._size_thread.start()

    def _on_tamanos_perfiles_navegador(self, tamanos: dict):
        self._tamanos_perfiles_navegador = tamanos

    def _on_navegadores_nube_data(self, rows: List[BrowserCloudAccountRow]):
        """Reparte los resultados del collector entre las dos tablas."""
        filas_nav = [r for r in rows if not _es_cuenta_nube(r.cliente)]
        filas_nube = [r for r in rows if _es_cuenta_nube(r.cliente)]
        self.navegadores_model.setRows(filas_nav)
        self.nube_model.setRows(filas_nube)
        self.btn_refresh_navegadores_nube.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status(f"✅ {len(filas_nav)} cuenta(s) de navegador, {len(filas_nube)} de nube")
        self._update_navegadores_counter()
        self._update_nube_counter()

    def _on_navegadores_nube_error(self, msg: str):
        self.btn_refresh_navegadores_nube.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al buscar cuentas de navegadores y nube")
        self._avisar("Error", f"No se pudieron obtener las cuentas:\n\n{msg}", QMessageBox.Warning)

    def _apply_navegadores_search(self):
        self.navegadores_proxy.setFilterRegExp(
            QRegExp(self.search_navegadores.text(), Qt.CaseInsensitive, QRegExp.FixedString))
        self._update_navegadores_counter()

    def _apply_nube_search(self):
        self.nube_proxy.setFilterRegExp(
            QRegExp(self.search_nube.text(), Qt.CaseInsensitive, QRegExp.FixedString))
        self._update_nube_counter()

    def _update_navegadores_counter(self):
        self.lbl_navegadores_count.setText(
            f"Cuentas: {self.navegadores_proxy.rowCount()}/{self.navegadores_model.rowCount()}")

    def _update_nube_counter(self):
        self.lbl_nube_count.setText(
            f"Cuentas: {self.nube_proxy.rowCount()}/{self.nube_model.rowCount()}")

    # ── Menús contextuales ────────────────────────────────────────────

    def _ctx_navegadores(self, pos):
        idx = self.navegadores_table.indexAt(pos)
        if not idx.isValid():
            return
        self.navegadores_table.selectRow(idx.row())
        source_row = self.navegadores_proxy.mapToSource(idx).row()
        row = self.navegadores_model.rows()[source_row]

        menu = QMenu(self)
        act_abrir = menu.addAction("📂 Abrir carpeta de datos")
        menu.addSeparator()
        act_copiar = menu.addAction("📋 Copiar fila")
        action = menu.exec_(self.navegadores_table.viewport().mapToGlobal(pos))
        if action == act_abrir:
            self._abrir_carpeta_navegador(row)
        elif action == act_copiar:
            self.copy_navegadores_selection()

    def _ctx_nube(self, pos):
        idx = self.nube_table.indexAt(pos)
        if not idx.isValid():
            return
        self.nube_table.selectRow(idx.row())
        source_row = self.nube_proxy.mapToSource(idx).row()
        row = self.nube_model.rows()[source_row]

        menu = QMenu(self)
        act_abrir = menu.addAction("📂 Abrir carpeta sincronizada")
        menu.addSeparator()
        act_copiar = menu.addAction("📋 Copiar fila")
        action = menu.exec_(self.nube_table.viewport().mapToGlobal(pos))
        if action == act_abrir:
            self._abrir_carpeta_nube(row)
        elif action == act_copiar:
            self.copy_nube_selection()

    def _abrir_carpeta_navegador(self, row: BrowserCloudAccountRow):
        """Localiza la carpeta de perfil real: Chrome/Edge se deduce del
        Tipo (p. ej. "- Profile 1 ("); Firefox no tiene identificador de
        perfil por fila en los logins detectados, así que abre la carpeta
        base de perfiles."""
        cliente_lower = row.cliente.lower()
        if "firefox" in cliente_lower:
            carpeta = os.path.join(os.environ.get("APPDATA", ""), "Mozilla", "Firefox", "Profiles")
        elif "chrome" in cliente_lower or "edge" in cliente_lower:
            m = re.search(r'-\s*(.+?)\s*\(', row.tipo or "")
            perfil = m.group(1).strip() if m else "Default"
            subcarpeta = ("Google", "Chrome") if "chrome" in cliente_lower else ("Microsoft", "Edge")
            carpeta = os.path.join(os.environ.get("LOCALAPPDATA", ""), *subcarpeta, "User Data", perfil)
        else:
            self._avisar("Abrir carpeta", "No se pudo determinar la carpeta de datos para este navegador.",
                         QMessageBox.Warning)
            return
        self._abrir_carpeta_en_explorador(carpeta)

    def _abrir_carpeta_nube(self, row: BrowserCloudAccountRow):
        """Por ahora solo sabe localizar la carpeta real de OneDrive
        (lectura de registro) — si en el futuro se detecta Google
        Drive/Dropbox, añadir su propia rama aquí."""
        cliente_lower = row.cliente.lower()
        if "onedrive" not in cliente_lower:
            self._avisar("Abrir carpeta", "Todavía no se puede localizar automáticamente la carpeta "
                         "de este proveedor de nube.", QMessageBox.Warning)
            return
        tipo_cuenta = "Business" if "business" in cliente_lower else "Personal"
        resultado = run_powershell_json_params(
            r'''
$cuenta = Get-ChildItem "HKCU:\Software\Microsoft\OneDrive\Accounts" -ErrorAction SilentlyContinue |
    Where-Object {{ $_.PSChildName -match '^{tipo_cuenta}' }} | Select-Object -First 1
if ($cuenta) {{
    $props = Get-ItemProperty $cuenta.PSPath -ErrorAction SilentlyContinue
    if ($props.UserFolder) {{
        [PSCustomObject]@{{ Ok = $true; Mensaje = $props.UserFolder }} | ConvertTo-Json -Compress
    }} else {{
        [PSCustomObject]@{{ Ok = $false; Mensaje = "Esta cuenta no tiene una carpeta local asociada." }} | ConvertTo-Json -Compress
    }}
}} else {{
    [PSCustomObject]@{{ Ok = $false; Mensaje = "No se encontró la cuenta de OneDrive en el registro." }} | ConvertTo-Json -Compress
}}
''', timeout=15, tipo_cuenta=tipo_cuenta,
        )
        if not (isinstance(resultado, dict) and resultado.get("Ok")):
            mensaje = resultado.get("Mensaje", "No se pudo localizar la carpeta.") \
                if isinstance(resultado, dict) else "No se pudo consultar el registro de OneDrive."
            self._avisar("Abrir carpeta", mensaje, QMessageBox.Warning)
            return
        self._abrir_carpeta_en_explorador(resultado["Mensaje"])

    def _abrir_carpeta_en_explorador(self, carpeta: str):
        if not os.path.isdir(carpeta):
            self._avisar("Abrir carpeta", f"La carpeta no existe o no está accesible:\n\n{carpeta}",
                         QMessageBox.Warning)
            return
        subprocess.Popen(["explorer.exe", carpeta])

    # ── Copiar / exportar: Navegadores ────────────────────────────────

    def copy_navegadores_selection(self):
        self._copiar_seleccion(self.navegadores_table, self.navegadores_proxy, "NAVEGADORES")

    def export_navegadores_csv(self):
        self._exportar_csv(self.navegadores_proxy, "Navegadores")

    def export_navegadores_html(self):
        self._exportar_html(self.navegadores_proxy, "Navegadores")

    # ── Copiar / exportar: Nube ────────────────────────────────────────

    def copy_nube_selection(self):
        self._copiar_seleccion(self.nube_table, self.nube_proxy, "NUBE")

    def export_nube_csv(self):
        self._exportar_csv(self.nube_proxy, "Nube")

    def export_nube_html(self):
        self._exportar_html(self.nube_proxy, "Nube")

    # ── Helpers compartidos por ambas tablas ──────────────────────────

    def _copiar_seleccion(self, tabla, proxy, titulo):
        selected_indexes = tabla.selectionModel().selectedRows()
        if not selected_indexes:
            self._avisar("Copiar", "No hay cuentas seleccionadas.")
            return

        lines = ["=" * 80, f"CUENTAS DE {titulo}", "=" * 80, ""]
        for index in selected_indexes:
            row = index.row()
            cliente = str(proxy.data(proxy.index(row, 0), Qt.DisplayRole) or "")
            cuenta = str(proxy.data(proxy.index(row, 1), Qt.DisplayRole) or "")
            servidor = str(proxy.data(proxy.index(row, 2), Qt.DisplayRole) or "")
            tipo = str(proxy.data(proxy.index(row, 3), Qt.DisplayRole) or "")
            lines.append(f"{cuenta}")
            lines.append(f"  Cliente: {cliente} | Tipo: {tipo}")
            lines.append(f"  Servidor: {servidor}")
            lines.append("")
        lines.append("=" * 80)

        QApplication.clipboard().setText("\n".join(lines))
        self.set_status(f"✅ Copiadas {len(selected_indexes)} cuentas al portapapeles")

    def _exportar_csv(self, proxy, nombre_seccion):
        if proxy.rowCount() == 0:
            self._avisar("Exportar CSV", "No hay cuentas para exportar.")
            return

        default_name = f"{nombre_seccion}_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        path = dialogo_guardar_archivo(self, "Guardar CSV", ruta_guardado_predeterminada(default_name), "CSV (*.csv)")
        if not path:
            return

        try:
            headers = BrowserCloudAccountTableModel.HEADERS
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = _SafeCsvWriter(csv.writer(f, delimiter=";"))
                writer.writerow(headers)
                for r in range(proxy.rowCount()):
                    row_data = []
                    for c in range(len(headers)):
                        idx = proxy.index(r, c)
                        val = proxy.data(idx, Qt.DisplayRole)
                        row_data.append(str(val) if val else "")
                    writer.writerow(row_data)

            total_exported = proxy.rowCount()
            self.set_status(f"✅ Exportado: {os.path.basename(path)} ({total_exported} cuentas)")
            self._avisar("Éxito", f"Exportadas {total_exported} cuentas a:\n\n{path}")
        except Exception as e:
            self._avisar("Error", f"No se pudo exportar:\n\n{e}", QMessageBox.Critical)

    def _exportar_html(self, proxy, nombre_seccion):
        """Cuentas de navegadores y nube con la plantilla común de informes."""
        if proxy.rowCount() == 0:
            self._avisar("Exportar HTML", "No hay cuentas para exportar.")
            return
        default_name = f"{nombre_seccion}_{time.strftime('%Y%m%d_%H%M%S')}.html"
        path = dialogo_guardar_archivo(self, "Guardar HTML", ruta_guardado_predeterminada(default_name), "HTML (*.html)")
        if not path:
            return
        headers = BrowserCloudAccountTableModel.HEADERS
        filas = [[str(proxy.data(proxy.index(r, c), Qt.DisplayRole) or "") for c in range(len(headers))]
                 for r in range(proxy.rowCount())]
        html = informe_html.documento(nombre_seccion, [informe_html.Seccion(nombre_seccion, headers, filas)], indice=False)
        self._guardar_informe_html(path, html, f"{len(filas)} cuentas.", f"✅ Exportado: {os.path.basename(path)} ({len(filas)} cuentas)")

