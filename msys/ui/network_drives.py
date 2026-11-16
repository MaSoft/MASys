# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña Unidades Remotas: tabla, filtros, conectar/desconectar/probar unidad, historial, exportar.
Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).

Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

import subprocess
from typing import List

from PyQt5.QtCore import Qt, QRegExp
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QCheckBox, QPushButton, QMessageBox, QTableView, QTextEdit,
    QDialog, QLineEdit, QMenu,
)

from msys.core.utils import run_powershell_json
from msys.models.rows import NetworkDriveRow
from msys.models.table_models import NetworkDriveTableModel, NetworkDriveFilterProxy
from msys.widgets.delegates import _setup_header
from msys.collectors import NetworkDriveCollector


class UnidadesRemotasMixin:
    def crear_tab_unidades_remotas(self):
        """Crea la pestaña de unidades remotas."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        # Barra de herramientas — mismo patrón que el resto de secciones:
        # hostname, búsqueda, acciones sobre la unidad seleccionada, hueco
        # elástico, botón principal "Actualizar" a la derecha (nunca
        # "Actualizar <Sección>").
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        import socket
        hostname = socket.gethostname()
        lbl_hostname = QLabel(f"🖥️ {hostname}")
        lbl_hostname.setObjectName("hostnameLabel")
        toolbar.addWidget(lbl_hostname)

        self.search_drives = QLineEdit()
        self.search_drives.setPlaceholderText("🔎 Buscar por letra, ruta, descripción o servidor...")
        self.search_drives.textChanged.connect(self.apply_drives_filter)
        toolbar.addWidget(self.search_drives, 3)

        self.btn_open_drive = QPushButton("📂 Abrir")
        self.btn_open_drive.setObjectName("ghost")
        self.btn_open_drive.setFixedWidth(100)
        self.btn_open_drive.setToolTip("Abrir unidad seleccionada en el Explorador")
        self.btn_open_drive.clicked.connect(self.open_selected_drive)
        toolbar.addWidget(self.btn_open_drive)

        self.btn_test_drive = QPushButton("🔍 Probar")
        self.btn_test_drive.setObjectName("ghost")
        self.btn_test_drive.setFixedWidth(100)
        self.btn_test_drive.setToolTip("Probar conectividad de la unidad")
        self.btn_test_drive.clicked.connect(self.test_selected_drive)
        toolbar.addWidget(self.btn_test_drive)

        self.btn_disconnect_drive = QPushButton("🔌 Desconectar")
        self.btn_disconnect_drive.setObjectName("danger")
        self.btn_disconnect_drive.setFixedWidth(150)
        self.btn_disconnect_drive.setToolTip("Desconectar unidad seleccionada")
        self.btn_disconnect_drive.clicked.connect(self.disconnect_selected_drive)
        toolbar.addWidget(self.btn_disconnect_drive)

        self.btn_historial_drives = QPushButton("🕓 Historial")
        self.btn_historial_drives.setObjectName("ghost")
        self.btn_historial_drives.setFixedWidth(120)
        self.btn_historial_drives.setToolTip("Ver historial de unidades remotas conectadas anteriormente")
        self.btn_historial_drives.clicked.connect(self.mostrar_historial_unidades)
        toolbar.addWidget(self.btn_historial_drives)

        toolbar.addStretch()

        self.btn_refresh_drives = QPushButton("🔄 Actualizar")
        self.btn_refresh_drives.setObjectName("primary")
        self.btn_refresh_drives.clicked.connect(self.refresh_network_drives)
        toolbar.addWidget(self.btn_refresh_drives)

        layout.addLayout(toolbar)

        # Fila de filtros: Conectada / Desconectada / Error / Letra / Servidor / Persistente / Temporal
        second_row = QHBoxLayout()
        second_row.setSpacing(15)

        self.chk_drives_conectada = QCheckBox("✅ Conectada")
        self.chk_drives_conectada.setChecked(True)
        self.chk_drives_conectada.stateChanged.connect(self._on_drives_state_filter_changed)
        second_row.addWidget(self.chk_drives_conectada)

        self.chk_drives_desconectada = QCheckBox("❌ Desconectada")
        self.chk_drives_desconectada.setChecked(True)
        self.chk_drives_desconectada.stateChanged.connect(self._on_drives_state_filter_changed)
        second_row.addWidget(self.chk_drives_desconectada)

        self.chk_drives_error = QCheckBox("⚠️ Error")
        self.chk_drives_error.setChecked(True)
        self.chk_drives_error.stateChanged.connect(self._on_drives_state_filter_changed)
        second_row.addWidget(self.chk_drives_error)

        lbl_letra = QLabel("Letra:")
        lbl_letra.setStyleSheet("font-weight: bold;")
        second_row.addWidget(lbl_letra)

        self.combo_letra_filter = QComboBox()
        self.combo_letra_filter.setFixedWidth(80)
        self.combo_letra_filter.addItem("Todas")
        self.combo_letra_filter.currentTextChanged.connect(self._on_letra_filter_changed)
        second_row.addWidget(self.combo_letra_filter)

        lbl_servidor = QLabel("Servidor:")
        lbl_servidor.setStyleSheet("font-weight: bold;")
        second_row.addWidget(lbl_servidor)

        self.combo_servidor_filter = QComboBox()
        self.combo_servidor_filter.setFixedWidth(130)
        self.combo_servidor_filter.addItem("Todos")
        self.combo_servidor_filter.currentTextChanged.connect(self._on_servidor_filter_changed)
        second_row.addWidget(self.combo_servidor_filter)

        self.chk_filter_persistente = QCheckBox("Persistente")
        self.chk_filter_persistente.setChecked(True)
        self.chk_filter_persistente.stateChanged.connect(self._on_persistencia_filter_changed)
        second_row.addWidget(self.chk_filter_persistente)

        self.chk_filter_temporal = QCheckBox("Temporal")
        self.chk_filter_temporal.setChecked(True)
        self.chk_filter_temporal.stateChanged.connect(self._on_persistencia_filter_changed)
        second_row.addWidget(self.chk_filter_temporal)

        second_row.addStretch()
        layout.addLayout(second_row)

        # Tabla de unidades
        self.drives_model = NetworkDriveTableModel([])

        self.drives_proxy = NetworkDriveFilterProxy(self)
        self.drives_proxy.setSourceModel(self.drives_model)
        self.drives_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.drives_proxy.setFilterKeyColumn(-1)

        self.drives_table = QTableView()
        self.drives_table.setModel(self.drives_proxy)
        self.drives_table.setAlternatingRowColors(True)
        self.drives_table.setSelectionBehavior(QTableView.SelectRows)
        self.drives_table.verticalHeader().setVisible(False)
        self.drives_table.setSortingEnabled(True)
        self.drives_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.drives_table.customContextMenuRequested.connect(self._mostrar_menu_contextual_drives)

        _setup_header(self.drives_table, 14, stretch_col=1)

        layout.addWidget(self.drives_table)

        # Inicializar filtros (todos los estados habilitados por defecto)
        self._on_drives_state_filter_changed()

        return tab

    def apply_drives_filter(self):
        """Aplica filtro de búsqueda en unidades remotas."""
        pattern = self.search_drives.text()
        rx = QRegExp(pattern, Qt.CaseInsensitive, QRegExp.FixedString)  # literal, como el resto de MASys: «C:\Program Files» u «Office (64» no encontraban nada
        self.drives_proxy.setFilterRegExp(rx)
        self._update_drives_counter()

    def _on_drives_state_filter_changed(self):
        """Actualiza filtros de estado de unidades remotas."""
        enabled_states = set()
        if self.chk_drives_conectada.isChecked():
            enabled_states.add("Conectada")
        if self.chk_drives_desconectada.isChecked():
            enabled_states.add("Desconectada")
        if self.chk_drives_error.isChecked():
            enabled_states.add("Error")

        self.drives_proxy.setEnabledStates(enabled_states)

        # Solo actualizar contador si lbl_status ya existe (después de __init__)
        if hasattr(self, 'lbl_status'):
            self._update_drives_counter()

    def _update_drives_counter(self):
        """Actualiza el contador de unidades mostradas."""
        total = self.drives_model.rowCount()
        visible = self.drives_proxy.rowCount()
        if total == visible:
            self.set_status(f"✅ {total} unidades remotas")
        else:
            self.set_status(f"✅ Mostrando {visible} de {total} unidades remotas")

    def _populate_drive_filters(self, rows: List[NetworkDriveRow]):
        """Puebla los filtros dinámicos (Letra y Servidor) con valores únicos de los datos."""
        # Guardar selección actual
        current_letra = self.combo_letra_filter.currentText()
        current_servidor = self.combo_servidor_filter.currentText()

        # Limpiar y repoblar Letra filter
        self.combo_letra_filter.clear()
        self.combo_letra_filter.addItem("Todas")
        letras = sorted(set(row.letra for row in rows if row.letra))
        for letra in letras:
            self.combo_letra_filter.addItem(letra)

        # Restaurar selección si sigue disponible
        idx = self.combo_letra_filter.findText(current_letra)
        if idx >= 0:
            self.combo_letra_filter.setCurrentIndex(idx)

        # Limpiar y repoblar Servidor filter
        self.combo_servidor_filter.clear()
        self.combo_servidor_filter.addItem("Todos")
        servidores = set()
        for row in rows:
            servidor = self.drives_proxy._extract_server_from_unc(row.ruta_remota)
            if servidor:
                servidores.add(servidor)
        for servidor in sorted(servidores):
            self.combo_servidor_filter.addItem(servidor)

        # Restaurar selección si sigue disponible
        idx = self.combo_servidor_filter.findText(current_servidor)
        if idx >= 0:
            self.combo_servidor_filter.setCurrentIndex(idx)

    def _on_letra_filter_changed(self, letra: str):
        """Maneja cambios en el filtro de letra."""
        self.drives_proxy.setLetraFilter(letra)
        self._update_drives_counter()

    def _on_servidor_filter_changed(self, servidor: str):
        """Maneja cambios en el filtro de servidor."""
        self.drives_proxy.setServidorFilter(servidor)
        self._update_drives_counter()

    def _on_persistencia_filter_changed(self):
        """Maneja cambios en el filtro de persistencia."""
        persistente = self.chk_filter_persistente.isChecked()
        temporal = self.chk_filter_temporal.isChecked()
        self.drives_proxy.setPersistenciaFilter(persistente, temporal)
        self._update_drives_counter()

    def _mostrar_menu_contextual_drives(self, position):
        """Menú contextual de la tabla: mismas acciones que la barra de
        herramientas (Abrir/Probar/Desconectar) más Copiar, para no
        depender solo de los botones de arriba."""
        if not self.drives_table.selectionModel().selectedRows():
            return

        menu = QMenu(self)
        menu.addAction("📂 Abrir", self.open_selected_drive)
        menu.addAction("🔍 Probar conectividad", self.test_selected_drive)
        menu.addAction("🔌 Desconectar", self.disconnect_selected_drive)
        menu.addSeparator()
        menu.addAction("📋 Copiar ruta UNC", self.copy_unc_path)
        menu.addAction("📋 Copiar selección", self.copy_drives_selection)
        menu.exec_(self.drives_table.viewport().mapToGlobal(position))

    def _ejecutar_reconectar_unidad(self, row, seccion_origen: str) -> bool:
        """Reconecta una unidad de red por letra + ruta remota con 'net use',
        respetando si era Persistente o Temporal — punto único de ejecución
        + auditoría + refresco, usado desde el detector de Soporte."""
        letra = (row.letra or "").strip()
        ruta_remota = (row.ruta_remota or "").strip()
        persistente = "yes" if (row.persistente or "").strip().upper() == "PERSISTENTE" else "no"

        ps_command = r"""
try {
    net use 'LETRA_PH' 'RUTA_PH' /persistent:PERSIST_PH 2>&1 | Out-Null
    if (Test-Path 'LETRA_PH\') {
        Write-Output (@{Success=$true; Message="Reconectado correctamente"} | ConvertTo-Json)
    } else {
        Write-Output (@{Success=$false; Message="net use no generó error pero la unidad sigue sin ser accesible"} | ConvertTo-Json)
    }
} catch {
    Write-Output (@{Success=$false; Message=$_.Exception.Message} | ConvertTo-Json)
}
""".replace("LETRA_PH", letra.replace("'", "''")) \
   .replace("RUTA_PH", ruta_remota.replace("'", "''")) \
   .replace("PERSIST_PH", persistente)

        data = run_powershell_json(ps_command, timeout=20)
        ok = bool(data and data.get("Success"))
        detalle = data.get("Message", "Sin respuesta del comando") if data else "Sin respuesta del comando"
        self.repair_audit_db.registrar(
            seccion=seccion_origen, accion="Reconectar unidad de red",
            objetivo=f"{letra} ({ruta_remota})", exito=ok, detalle=detalle,
        )
        if ok:
            self.set_status(f"✅ Unidad «{letra}» reconectada")
            self.refresh_network_drives()
        else:
            self._avisar("Con avisos", f"No se pudo reconectar la unidad:\n\n{detalle}", QMessageBox.Warning)
        return ok

    def open_selected_drive(self):
        """Abre la unidad seleccionada en el Explorador de Windows."""
        selected_indexes = self.drives_table.selectionModel().selectedRows()
        if not selected_indexes:
            self._avisar("Abrir Unidad", "Por favor, seleccione una unidad para abrir.")
            return

        # Solo abrir la primera seleccionada
        row = selected_indexes[0].row()
        letra = str(self.drives_proxy.data(self.drives_proxy.index(row, 0), Qt.DisplayRole) or "").strip()

        if not letra:
            self._avisar("Abrir Unidad", "No se pudo determinar la letra de la unidad.", QMessageBox.Warning)
            return

        # Abrir directamente con explorer.exe (sin PowerShell para evitar timeouts)
        try:
            subprocess.Popen(["explorer.exe", f"{letra}\\"])
        except Exception as e:
            self._avisar("Error", f"Error al abrir la unidad:\n\n{str(e)}", QMessageBox.Critical)

    def disconnect_selected_drive(self):
        """Desconecta la unidad seleccionada."""
        selected_indexes = self.drives_table.selectionModel().selectedRows()
        if not selected_indexes:
            self._avisar("Desconectar Unidad", "Por favor, seleccione una unidad para desconectar.")
            return

        # Solo desconectar la primera seleccionada
        row = selected_indexes[0].row()
        letra = str(self.drives_proxy.data(self.drives_proxy.index(row, 0), Qt.DisplayRole) or "").strip()
        ruta = str(self.drives_proxy.data(self.drives_proxy.index(row, 1), Qt.DisplayRole) or "").strip()

        if not letra:
            self._avisar("Desconectar Unidad", "No se pudo determinar la letra de la unidad.", QMessageBox.Warning)
            return

        # Confirmar desconexión con botones en español
        msgBox = QMessageBox(self)
        msgBox.setWindowFlags(msgBox.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        msgBox.setWindowTitle("Confirmar desconexión")
        msgBox.setText(f"¿Seguro que quieres desconectar la unidad {letra}?\n\nRuta: {ruta}")
        msgBox.setIcon(QMessageBox.Question)
        btn_si = msgBox.addButton("Sí", QMessageBox.YesRole)
        btn_no = msgBox.addButton("No", QMessageBox.NoRole)
        msgBox.setDefaultButton(btn_no)
        msgBox.exec_()

        if msgBox.clickedButton() != btn_si:
            return

        # Ejecutar PowerShell para desconectar
        ps_command = r"""
try {
    $DriveLetter = 'LETRA_PLACEHOLDER'
    $DriveName = $DriveLetter -replace ':', ''
    Remove-PSDrive -Name $DriveName -Force -ErrorAction SilentlyContinue
    $salidaNet = (net use $DriveLetter /delete /yes 2>&1 | Out-String).Trim()
    # 5.4.0: comprobar de verdad (antes decía «desconectada» aunque net use fallara)
    $sigue = Get-CimInstance Win32_MappedLogicalDisk -Filter "DeviceID='$DriveLetter'" -ErrorAction SilentlyContinue
    if ($LASTEXITCODE -ne 0 -or $sigue) {
        Write-Output (@{Success=$false; Message=$(if ($salidaNet) { $salidaNet } else { "La unidad sigue conectada" })} | ConvertTo-Json)
    } else {
        Write-Output (@{Success=$true; Message="Desconectado correctamente"} | ConvertTo-Json)
    }
} catch {
    Write-Output (@{Success=$false; Message=$_.Exception.Message} | ConvertTo-Json)
}
""".replace('LETRA_PLACEHOLDER', letra)

        try:
            data = run_powershell_json(ps_command, timeout=15)
            self._registrar_historial("💾 Unidades Remotas", "Desconectar unidad de red", f"{letra} ({ruta})",
                                      bool(data and data.get("Success")),
                                      (data or {}).get("Message", "Sin respuesta del comando"))
            if data:
                if data.get("Success"):
                    self._avisar("Desconectar Unidad", f"Unidad {letra} desconectada correctamente.")
                    # Refrescar lista
                    self.refresh_network_drives()
                else:
                    self._avisar("Desconectar Unidad", f"No se pudo desconectar:\n\n{data.get('Message', 'Error desconocido')}", QMessageBox.Warning)
            else:
                self._avisar("Desconectar Unidad", "No se recibió respuesta del comando.", QMessageBox.Warning)
        except Exception as e:
            self._avisar("Error", f"Error al desconectar la unidad:\n\n{str(e)}", QMessageBox.Critical)

    def test_selected_drive(self):
        """Prueba la conectividad de la unidad seleccionada."""
        selected_indexes = self.drives_table.selectionModel().selectedRows()
        if not selected_indexes:
            self._avisar("Probar Unidad", "Por favor, seleccione una unidad para probar.")
            return

        # Solo probar la primera seleccionada
        row = selected_indexes[0].row()
        letra = str(self.drives_proxy.data(self.drives_proxy.index(row, 0), Qt.DisplayRole) or "").strip()

        if not letra:
            self._avisar("Probar Unidad", "No se pudo determinar la letra de la unidad.", QMessageBox.Warning)
            return

        # Ejecutar PowerShell para probar conexión (comando simple)
        ps_command = r"""
try {
    $drivePath = "LETRA_PLACEHOLDER\"
    if (Test-Path $drivePath) {
        $files = Get-ChildItem $drivePath -ErrorAction Stop | Measure-Object
        $result = @{
            Success = $true
            Message = "Accesible"
            Details = "Conexión correcta: $($files.Count) elementos encontrados"
        }
        Write-Output ($result | ConvertTo-Json)
    } else {
        $result = @{Success=$false; Message="No accesible"; Details="No se puede acceder a la ruta"}
        Write-Output ($result | ConvertTo-Json)
    }
} catch {
    $result = @{Success=$false; Message="Error"; Details=$_.Exception.Message}
    Write-Output ($result | ConvertTo-Json)
}
""".replace("LETRA_PLACEHOLDER", letra)

        try:
            data = run_powershell_json(ps_command, timeout=15)
            if data:
                icon = QMessageBox.Information if data.get("Success") else QMessageBox.Warning
                message = data.get("Details", data.get("Message", "Sin información"))
                self._avisar("Prueba de conectividad", f"Unidad {letra}:\n\n{message}", icon)
            else:
                self._avisar("Probar Unidad", "No se recibió respuesta del comando.", QMessageBox.Warning)
        except Exception as e:
            self._avisar("Error", f"Error al probar la unidad:\n\n{str(e)}", QMessageBox.Critical)

    def copy_unc_path(self):
        """Copia la ruta UNC de la unidad seleccionada al portapapeles."""
        selected_indexes = self.drives_table.selectionModel().selectedRows()
        if not selected_indexes:
            self._avisar("Copiar UNC", "Por favor, seleccione una unidad.")
            return

        # Solo copiar la primera seleccionada
        row = selected_indexes[0].row()
        ruta = str(self.drives_proxy.data(self.drives_proxy.index(row, 1), Qt.DisplayRole) or "").strip()

        if not ruta:
            self._avisar("Copiar UNC", "No se pudo determinar la ruta UNC.", QMessageBox.Warning)
            return

        # Copiar al portapapeles
        clipboard = QApplication.clipboard()
        clipboard.setText(ruta)
        self._avisar("Copiar UNC", f"Ruta UNC copiada al portapapeles:\n\n{ruta}")

    def copy_drives_selection(self):
        """Copia la selección de unidades remotas al portapapeles."""
        selected_indexes = self.drives_table.selectionModel().selectedRows()
        if not selected_indexes:
            self._avisar("Copiar", "No hay unidades seleccionadas.")
            return

        lines = []
        lines.append("=" * 80)
        lines.append("UNIDADES REMOTAS")
        lines.append("=" * 80)
        lines.append("")

        for index in selected_indexes:
            row = index.row()
            letra = str(self.drives_proxy.data(self.drives_proxy.index(row, 0), Qt.DisplayRole) or "")
            ruta = str(self.drives_proxy.data(self.drives_proxy.index(row, 1), Qt.DisplayRole) or "")
            descripcion = str(self.drives_proxy.data(self.drives_proxy.index(row, 2), Qt.DisplayRole) or "")
            estado = str(self.drives_proxy.data(self.drives_proxy.index(row, 3), Qt.DisplayRole) or "")

            lines.append(f"Letra: {letra}")
            lines.append(f"  Ruta Remota: {ruta}")
            lines.append(f"  Descripción: {descripcion}")
            lines.append(f"  Estado: {estado}")
            lines.append("")

        lines.append("=" * 80)

        text = "\n".join(lines)
        QApplication.clipboard().setText(text)
        self.set_status(f"✅ Copiadas {len(selected_indexes)} unidades al portapapeles")

    def refresh_network_drives(self):
        """Actualiza lista de unidades remotas."""
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.set_status("🔄 Cargando unidades remotas...")
        self.btn_refresh_drives.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.worker = NetworkDriveCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_drives_data)
        self.worker.finished_error.connect(self._on_drives_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def mostrar_historial_unidades(self):
        """Muestra un diálogo con el historial de unidades remotas del registro."""
        self.set_status("🔄 Leyendo historial de unidades remotas...")
        ps = r"""
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\MountPoints2'
    $items = Get-Item -Path $key -ErrorAction SilentlyContinue
    if (-not $items) { '[]'; exit }
    $rutas = $items.GetSubKeyNames() | Where-Object { $_ -match '^\\\\' -or $_ -match '^##' }
    $result = $rutas | ForEach-Object {
        $ruta = $_ -replace '#', '\'
        [PSCustomObject]@{ Ruta = $ruta }
    }
    if ($result) { $result | ConvertTo-Json -Depth 2 } else { '[]' }
} catch { '[]' }
"""
        data = run_powershell_json(ps, timeout=10)
        self.set_status("✅ Historial cargado")

        if not data or data == []:
            self._avisar("Historial de Unidades", "No se encontró historial de unidades remotas en el registro.")
            return
        if isinstance(data, dict):
            data = [data]

        dlg = QDialog(self)
        dlg.setWindowTitle("🕓 Historial de Unidades Remotas")
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dlg.setMinimumSize(600, 400)
        v = QVBoxLayout(dlg)

        lbl = QLabel(f"Rutas UNC registradas anteriormente ({len(data)} entradas):")
        lbl.setStyleSheet("font-weight: bold; margin-bottom: 4px;")
        v.addWidget(lbl)

        txt = QTextEdit()
        txt.setReadOnly(True)
        txt.setFont(QFont("Consolas", 9))
        lines = [item.get("Ruta", "") for item in data if item.get("Ruta")]
        txt.setPlainText("\n".join(sorted(lines)))
        v.addWidget(txt)

        btn_copy = QPushButton("📋 Copiar todo")
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(txt.toPlainText()))
        btn_close = QPushButton("Cerrar")
        btn_close.clicked.connect(dlg.accept)
        brow = QHBoxLayout()
        brow.addWidget(btn_copy)
        brow.addStretch()
        brow.addWidget(btn_close)
        v.addLayout(brow)
        dlg.exec_()

