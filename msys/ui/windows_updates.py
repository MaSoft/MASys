# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña Actualizaciones: tabla, filtro, instalar KB, refresco.
Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).

Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

import subprocess

from PyQt5.QtCore import Qt, QRegExp, QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QCheckBox,
    QPushButton, QMessageBox, QSplitter, QTableView,
)

from msys.core.utils import escape_powershell_string, is_admin
from msys.models.rows import InstalledUpdateRow, PendingUpdateRow
from msys.models.table_models import (
    InstalledUpdateTableModel, PendingUpdateTableModel, PendingUpdateFilterProxy,
    _FechaAwareSortProxy,
)
from msys.widgets.delegates import _setup_header
from msys.collectors import WindowsUpdatesCollector


class ActualizacionesMixin:
    def crear_tab_windows_updates(self):
        """Crea la pestaña de Actualizaciones de Windows."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        # Banners fijos en la propia pestaña (no solo texto en la barra de
        # estado + un diálogo que se puede cerrar sin fijarse): un reinicio
        # pendiente o una búsqueda fallida quedan siempre visibles mientras
        # sigan siéndolo, no solo un instante tras pulsar Actualizar.
        self.lbl_reboot_pendiente = QLabel(
            "⚠️ Hay un reinicio pendiente para completar la instalación de actualizaciones de Windows.")
        self.lbl_reboot_pendiente.setStyleSheet(
            "background:#fff3cd; color:#664d03; padding:8px 12px; border-radius:5px; font-weight:600;")
        self.lbl_reboot_pendiente.setWordWrap(True)
        self.lbl_reboot_pendiente.setVisible(False)
        layout.addWidget(self.lbl_reboot_pendiente)

        self.lbl_updates_error = QLabel("")
        self.lbl_updates_error.setStyleSheet(
            "background:#f8d7da; color:#58151c; padding:8px 12px; border-radius:5px; font-weight:600;")
        self.lbl_updates_error.setWordWrap(True)
        self.lbl_updates_error.setVisible(False)
        layout.addWidget(self.lbl_updates_error)

        # Una sola barra de acciones para las dos tablas — nada de buscador
        # ni botón Actualizar duplicados por tabla. La búsqueda filtra las
        # dos a la vez; "Solo críticas/importantes", "Instalar
        # seleccionada" y "Abrir Windows Update" solo tienen sentido para
        # Pendientes, así que solo afectan a esa (Instaladas no se toca).
        actions_layout = QHBoxLayout()
        self.search_updates = QLineEdit()
        self.search_updates.setPlaceholderText("🔎 Buscar en actualizaciones...")
        self.search_updates.textChanged.connect(self._filtrar_actualizaciones)
        actions_layout.addWidget(self.search_updates, 3)

        self.chk_solo_graves_updates = QCheckBox("Solo críticas/importantes")
        self.chk_solo_graves_updates.setToolTip("Solo afecta a Pendientes")
        self.chk_solo_graves_updates.stateChanged.connect(
            lambda: self.pending_updates_proxy.setSoloGraves(self.chk_solo_graves_updates.isChecked()))
        actions_layout.addWidget(self.chk_solo_graves_updates)

        actions_layout.addStretch()

        self.btn_install_selected = QPushButton("⬇️ Instalar seleccionada")
        self.btn_install_selected.setObjectName("primary")
        self.btn_install_selected.setToolTip("Solo afecta a Pendientes")
        self.btn_install_selected.clicked.connect(self._install_selected_update)
        actions_layout.addWidget(self.btn_install_selected)

        self.btn_open_wu = QPushButton("🪟 Abrir Windows Update")
        self.btn_open_wu.setObjectName("ghost")
        self.btn_open_wu.clicked.connect(lambda: subprocess.Popen(
            ["powershell", "-Command", "Start-Process 'ms-settings:windowsupdate'"],
            startupinfo=self._get_startupinfo()
        ))
        actions_layout.addWidget(self.btn_open_wu)

        self.btn_refresh_updates = QPushButton("🔄 Actualizar")
        self.btn_refresh_updates.setObjectName("primary")
        self.btn_refresh_updates.clicked.connect(self.refresh_windows_updates)
        actions_layout.addWidget(self.btn_refresh_updates)

        layout.addLayout(actions_layout)

        # Instaladas y Pendientes en la misma ventana, una tabla debajo de
        # la otra — Pendientes primero, Instaladas debajo — con un
        # QSplitter vertical para repartir el alto entre las dos a gusto.
        splitter = QSplitter(Qt.Vertical)

        # ── Bloque: Pendientes ──
        pending_container = QWidget()
        pending_layout = QVBoxLayout(pending_container)
        pending_layout.setSpacing(4)
        pending_layout.setContentsMargins(0, 0, 0, 0)

        self.lbl_pending_header = QLabel("⏳ Actualizaciones pendientes")
        self.lbl_pending_header.setStyleSheet("font-weight:600; font-size:11pt;")
        pending_layout.addWidget(self.lbl_pending_header)

        self.pending_updates_model = PendingUpdateTableModel([])
        self.pending_updates_proxy = PendingUpdateFilterProxy(self)
        self.pending_updates_proxy.setSourceModel(self.pending_updates_model)
        self.pending_updates_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.pending_updates_proxy.setFilterKeyColumn(-1)

        self.pending_updates_table = QTableView()
        self.pending_updates_table.setModel(self.pending_updates_proxy)
        self.pending_updates_table.setAlternatingRowColors(True)
        self.pending_updates_table.setSelectionBehavior(QTableView.SelectRows)
        self.pending_updates_table.verticalHeader().setVisible(False)
        self.pending_updates_table.setSortingEnabled(True)

        _setup_header(self.pending_updates_table, len(PendingUpdateTableModel.HEADERS), stretch_col=1)

        pending_layout.addWidget(self.pending_updates_table)
        splitter.addWidget(pending_container)

        # ── Bloque: Instaladas ──
        installed_container = QWidget()
        installed_layout = QVBoxLayout(installed_container)
        installed_layout.setSpacing(4)
        installed_layout.setContentsMargins(0, 0, 0, 0)

        self.lbl_installed_header = QLabel("✅ Actualizaciones instaladas")
        self.lbl_installed_header.setStyleSheet("font-weight:600; font-size:11pt;")
        installed_layout.addWidget(self.lbl_installed_header)

        self.installed_updates_model = InstalledUpdateTableModel([])
        self.installed_updates_proxy = _FechaAwareSortProxy(self)
        self.installed_updates_proxy.setSourceModel(self.installed_updates_model)
        self.installed_updates_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.installed_updates_proxy.setFilterKeyColumn(-1)

        self.installed_updates_table = QTableView()
        self.installed_updates_table.setModel(self.installed_updates_proxy)
        self.installed_updates_table.setAlternatingRowColors(True)
        self.installed_updates_table.setSelectionBehavior(QTableView.SelectRows)
        self.installed_updates_table.verticalHeader().setVisible(False)
        self.installed_updates_table.setSortingEnabled(True)

        _setup_header(self.installed_updates_table, len(InstalledUpdateTableModel.HEADERS), stretch_col=1)

        installed_layout.addWidget(self.installed_updates_table)
        splitter.addWidget(installed_container)

        splitter.setSizes([400, 400])
        layout.addWidget(splitter, 1)

        return tab

    def _filtrar_actualizaciones(self):
        """Un único buscador para Instaladas y Pendientes: filtra las dos
        tablas a la vez con el mismo texto."""
        regex = QRegExp(self.search_updates.text(), Qt.CaseInsensitive, QRegExp.FixedString)
        self.installed_updates_proxy.setFilterRegExp(regex)
        self.pending_updates_proxy.setFilterRegExp(regex)

    def _install_selected_update(self):
        selected = self.pending_updates_table.selectionModel().selectedRows()
        if not selected:
            self._avisar("Instalar actualización", "Selecciona primero una actualización de la lista.")
            return

        if not is_admin():
            self._avisar("Permisos insuficientes", "Instalar actualizaciones de Windows requiere ejecutar MASys como "
                "administrador.\n\nCierra la aplicación y vuelve a abrirla con "
                "'Ejecutar como administrador'.", QMessageBox.Warning)
            return

        source_index = self.pending_updates_proxy.mapToSource(selected[0])
        row = self.pending_updates_model.rows()[source_index.row()]
        titulo, kb, update_id = row.titulo, row.kb, row.update_id

        if not update_id:
            self._avisar("Instalar actualización", "Esta actualización no tiene un identificador único disponible (dato no "
                "devuelto por Windows Update) y no se puede instalar de forma segura desde "
                "aquí.\n\nUsa 🪟 Abrir Windows Update para instalarla manualmente.", QMessageBox.Warning)
            return

        if not self._confirmar_reparacion(
            "Instalar actualización",
            cambios=[f"Descargar e instalar «{titulo}» ({kb})"],
            reversible=False,
            explicacion="Puede tardar varios minutos y puede requerir reiniciar el equipo "
                        "para completarse.",
        ):
            return

        # Se re-busca por UpdateID (no por título truncado) en cada fase: los
        # objetos COM de una búsqueda no sobreviven entre procesos de
        # PowerShell distintos, así que descarga e instalación son dos
        # invocaciones separadas, cada una localizando la MISMA actualización
        # exacta por su identificador — nunca por coincidencia de texto, que
        # con títulos truncados podía emparejar más de una actualización
        # distinta a la seleccionada.
        update_id_escaped = escape_powershell_string(update_id)
        ps_descarga = f"""
try {{
    $session = New-Object -ComObject Microsoft.Update.Session
    $searcher = $session.CreateUpdateSearcher()
    $result = $searcher.Search("UpdateID='{update_id_escaped}' and IsInstalled=0")
    if ($result.Updates.Count -eq 0) {{ 'NO_MATCH'; exit }}
    $update = $result.Updates.Item(0)
    if (-not $update.EulaAccepted) {{ $update.AcceptEula() }}
    $coll = New-Object -ComObject Microsoft.Update.UpdateColl
    $coll.Add($update) | Out-Null
    $downloader = $session.CreateUpdateDownloader()
    $downloader.Updates = $coll
    $downloadResult = $downloader.Download()
    if ($downloadResult.ResultCode -ne 2) {{ "ERROR: descarga fallida (código $($downloadResult.ResultCode))" }} else {{ 'OK' }}
}} catch {{ "ERROR: $($_.Exception.Message)" }}
"""
        ps_instalacion = f"""
try {{
    $session = New-Object -ComObject Microsoft.Update.Session
    $searcher = $session.CreateUpdateSearcher()
    $result = $searcher.Search("UpdateID='{update_id_escaped}' and IsInstalled=0")
    if ($result.Updates.Count -eq 0) {{ 'NO_MATCH'; exit }}
    $update = $result.Updates.Item(0)
    $coll = New-Object -ComObject Microsoft.Update.UpdateColl
    $coll.Add($update) | Out-Null
    $installer = $session.CreateUpdateInstaller()
    $installer.Updates = $coll
    $installResult = $installer.Install()
    if ($installResult.RebootRequired) {{ 'REBOOT_REQUIRED' }} else {{ 'OK' }}
}} catch {{ "ERROR: $($_.Exception.Message)" }}
"""
        self.set_status(f"🔄 Descargando {kb}...")
        self.btn_install_selected.setEnabled(False)
        self.btn_refresh_updates.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(15)

        class _Installer(QThread):
            fase = pyqtSignal(str, int)
            done = pyqtSignal(str)

            def __init__(self, ps_descarga, ps_instalacion):
                super().__init__()
                self._ps_descarga = ps_descarga
                self._ps_instalacion = ps_instalacion

            @staticmethod
            def _ejecutar(script, timeout):
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = subprocess.SW_HIDE
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                    capture_output=True, text=True, timeout=timeout,
                    startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW
                )
                return (r.stdout or "").strip() or (r.stderr or "").strip() or "ERROR"

            def run(self):
                try:
                    self.fase.emit("descargando", 30)
                    resultado_descarga = self._ejecutar(self._ps_descarga, 600)
                    if resultado_descarga != "OK":
                        self.done.emit(resultado_descarga)
                        return
                    self.fase.emit("instalando", 70)
                    resultado_instalacion = self._ejecutar(self._ps_instalacion, 900)
                    self.done.emit(resultado_instalacion)
                except Exception as e:
                    self.done.emit(f"ERROR: {e}")

        def _on_fase(nombre, valor):
            self.set_status(f"🔄 {nombre.capitalize()} {kb} — puede tardar varios minutos...")
            self.progress_bar.setValue(valor)

        def _on_done(output):
            self.btn_install_selected.setEnabled(True)
            self.btn_refresh_updates.setEnabled(True)
            self.progress_bar.setVisible(False)
            # 5.4.2: instalar una actualización también va al historial de reparaciones
            self._registrar_historial("🔄 Actualizaciones", "Instalar actualización de Windows", str(kb),
                                      str(output).startswith(("OK", "REBOOT")), str(output)[:500])
            if output == "OK":
                self.set_status(f"✅ {kb} instalado correctamente")
                self._avisar("Éxito", f"Actualización {kb} instalada correctamente.")
                self.refresh_windows_updates()
            elif output == "REBOOT_REQUIRED":
                self.set_status(f"✅ {kb} instalado — se requiere reinicio")
                self._avisar("Reinicio requerido", f"La actualización {kb} se instaló correctamente.\n\nSe requiere reiniciar el equipo.", QMessageBox.Warning)
                self.refresh_windows_updates()
            elif output == "NO_MATCH":
                self.set_status(f"⚠️ {kb} ya no está disponible")
                self._avisar("No encontrada", "Esta actualización ya no aparece como pendiente (puede que se haya "
                    "instalado ya por otra vía).\n\nPulsa 🔄 Actualizar para refrescar la lista.", QMessageBox.Warning)
            else:
                self.set_status(f"❌ Error al instalar {kb}")
                self._avisar("Error", f"No se pudo instalar «{titulo}»:\n\n{output}", QMessageBox.Critical)

        self._update_installer = _Installer(ps_descarga, ps_instalacion)
        self._update_installer.fase.connect(_on_fase)
        self._update_installer.done.connect(_on_done)
        self._update_installer.start()

    def refresh_windows_updates(self):
        """Actualiza información de actualizaciones de Windows."""
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.set_status("🔄 Buscando actualizaciones de Windows...")
        self.btn_refresh_updates.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.worker = WindowsUpdatesCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_windows_updates_data)
        self.worker.finished_error.connect(self._on_windows_updates_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_windows_updates_data(self, data: dict):
        """Procesa datos de actualizaciones de Windows."""
        self._updates_cargadas = True
        installed_rows = []
        for update in data.get('Installed') or []:
            installed_rows.append(InstalledUpdateRow(
                kb=update.get('KB', 'N/A'),
                descripcion=update.get('Descripcion', 'N/A'),
                instalacion=update.get('Instalacion', 'N/A'),
                usuario=update.get('Usuario', 'N/A')
            ))
        self.installed_updates_model.setRows(installed_rows)

        pending_rows = []
        for update in data.get('Pending') or []:
            pending_rows.append(PendingUpdateRow(
                kb=update.get('KB', 'N/A'),
                titulo=update.get('Titulo', 'N/A'),
                tamano=update.get('Tamano', 'N/A'),
                criticidad=update.get('Criticidad', 'N/A'),
                fecha=update.get('Fecha', 'N/A'),
                tipo=update.get('Tipo', 'Software'),
                update_id=update.get('UpdateID', ''),
            ))
        self.pending_updates_model.setRows(pending_rows)

        self.lbl_installed_header.setText(f"✅ Actualizaciones instaladas ({len(installed_rows)})")
        self.lbl_pending_header.setText(f"⏳ Actualizaciones pendientes ({len(pending_rows)})")

        self.btn_refresh_updates.setEnabled(True)
        self.progress_bar.setVisible(False)

        # Reinicio pendiente: un update ya instalado (IsInstalled=1) puede
        # seguir esperando reinicio para completarse — no aparece ni en
        # Instaladas ni en Pendientes con la API de búsqueda, es un estado
        # de sistema aparte. Banner fijo en la pestaña, no solo un aviso
        # puntual, porque sigue siendo cierto hasta que el usuario reinicie.
        # Se guarda también como atributo (no solo el estado visual del
        # banner) para que el detector de Soporte sepa si ofrecer o no
        # "Reiniciar el equipo ahora".
        self._reboot_pendiente = bool(data.get('RebootPending'))
        self.lbl_reboot_pendiente.setVisible(self._reboot_pendiente)

        # 'None' (fallo/timeout de la llamada PS) y '0 encontradas' (resultado
        # real) son estados distintos — antes eran indistinguibles y el
        # usuario veía "✅ 0 instaladas, 0 pendientes" aunque la búsqueda
        # hubiera fallado de verdad. El aviso va también en un banner fijo
        # en la pestaña (no solo la barra de estado + un diálogo que se
        # puede cerrar sin fijarse), para que no pase desapercibido.
        errores = []
        if data.get('InstalledError'):
            errores.append("instaladas")
        if data.get('PendingError'):
            errores.append("pendientes")

        if errores:
            detalle = " y ".join(errores)
            self.lbl_updates_error.setText(
                f"❌ Falló la búsqueda de actualizaciones {detalle} (tiempo agotado o error de "
                "Windows Update). Pulsa 🔄 Actualizar para reintentarlo — la búsqueda de "
                "pendientes puede tardar varios minutos, sobre todo la primera vez.")
            self.lbl_updates_error.setVisible(True)
            self.set_status(f"⚠️ {len(installed_rows)} instaladas, {len(pending_rows)} pendientes — falló la búsqueda de {detalle}")
        else:
            self.lbl_updates_error.setVisible(False)
            self.set_status(f"✅ {len(installed_rows)} actualizaciones instaladas, {len(pending_rows)} pendientes")

    def _on_windows_updates_error(self, msg: str):
        """Maneja errores de actualizaciones."""
        self.btn_refresh_updates.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al obtener actualizaciones")
        self._avisar("Error", f"No se pudo obtener información de actualizaciones:\n\n{msg}", QMessageBox.Warning)

