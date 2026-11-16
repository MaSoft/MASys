# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña Redes: adaptadores de red, habilitar/deshabilitar/reiniciar, filtros.
Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).

Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

import shutil
from typing import Optional

from PyQt5.QtCore import Qt, QRegExp
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QCheckBox,
    QPushButton, QMessageBox, QTableView, QMenu,
)

from msys.config import logger
from msys.core.utils import escape_powershell_string, is_admin, run_powershell_command, run_system_command
from msys.models.rows import NetworkAdapterRow
from msys.models.table_models import NetworkAdapterTableModel, NetworkAdapterFilterProxy
from msys.widgets.delegates import _setup_header
from msys.win.structs import get_cpu_percent, get_memory_status
from msys.collectors import NetworkAdapterCollector


class RedesMixin:
    def crear_tab_redes(self):
        """Crea la pestaña de Adaptadores de Red."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        # Barra de búsqueda y acciones
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)

        import socket
        hostname = socket.gethostname()
        lbl_hostname = QLabel(f"🖥️ {hostname}")
        lbl_hostname.setObjectName("hostnameLabel")
        actions_layout.addWidget(lbl_hostname)

        self.search_network = QLineEdit()
        self.search_network.setPlaceholderText("🔎 Buscar adaptador...")
        self.search_network.textChanged.connect(self.apply_network_filter)
        actions_layout.addWidget(self.search_network, 3)

        actions_layout.addStretch()

        self.btn_habilitar_adaptador = QPushButton("▶️ Habilitar")
        self.btn_habilitar_adaptador.setObjectName("ghost")
        self.btn_habilitar_adaptador.setToolTip("Habilita el adaptador seleccionado (Enable-NetAdapter)")
        self.btn_habilitar_adaptador.clicked.connect(self._habilitar_adaptador_seleccionado)
        actions_layout.addWidget(self.btn_habilitar_adaptador)

        self.btn_deshabilitar_adaptador = QPushButton("⏸️ Deshabilitar")
        self.btn_deshabilitar_adaptador.setObjectName("ghost")
        self.btn_deshabilitar_adaptador.setToolTip("Deshabilita el adaptador seleccionado (Disable-NetAdapter)")
        self.btn_deshabilitar_adaptador.clicked.connect(self._deshabilitar_adaptador_seleccionado)
        actions_layout.addWidget(self.btn_deshabilitar_adaptador)

        self.btn_reiniciar_adaptador = QPushButton("🔄 Reiniciar Adaptador")
        self.btn_reiniciar_adaptador.setObjectName("ghost")
        self.btn_reiniciar_adaptador.setToolTip(
            "Deshabilita y vuelve a habilitar el adaptador seleccionado (Restart-NetAdapter)")
        self.btn_reiniciar_adaptador.clicked.connect(self._reiniciar_adaptador_seleccionado)
        actions_layout.addWidget(self.btn_reiniciar_adaptador)

        self.btn_liberar_renovar_ip = QPushButton("🔁 Liberar/Renovar IP")
        self.btn_liberar_renovar_ip.setObjectName("ghost")
        self.btn_liberar_renovar_ip.setToolTip(
            "Libera y renueva la concesión DHCP del adaptador seleccionado (solo si usa DHCP)")
        self.btn_liberar_renovar_ip.clicked.connect(self._liberar_renovar_ip_seleccionado)
        actions_layout.addWidget(self.btn_liberar_renovar_ip)

        self.btn_refresh_network = QPushButton("🔄 Actualizar")
        self.btn_refresh_network.setObjectName("primary")
        self.btn_refresh_network.setToolTip("Adaptadores de red y configuración IP")
        self.btn_refresh_network.clicked.connect(self.refresh_network_adapters)
        actions_layout.addWidget(self.btn_refresh_network)

        layout.addLayout(actions_layout)

        # Segunda línea: Filtros por estado
        filters_state_layout = QHBoxLayout()
        filters_state_layout.setSpacing(15)

        lbl_estado = QLabel("Estado:")
        lbl_estado.setObjectName("fieldLabel")
        filters_state_layout.addWidget(lbl_estado)

        self.chk_network_activo = QCheckBox("✅ Activo")
        self.chk_network_activo.setChecked(True)
        self.chk_network_activo.stateChanged.connect(self._on_network_filter_changed)
        filters_state_layout.addWidget(self.chk_network_activo)

        self.chk_network_inactivo = QCheckBox("❌ Inactivo")
        self.chk_network_inactivo.setChecked(True)
        self.chk_network_inactivo.stateChanged.connect(self._on_network_filter_changed)
        filters_state_layout.addWidget(self.chk_network_inactivo)

        self.chk_network_deshabilitado = QCheckBox("🚫 Deshabilitado")
        self.chk_network_deshabilitado.setChecked(False)
        self.chk_network_deshabilitado.stateChanged.connect(self._on_network_filter_changed)
        filters_state_layout.addWidget(self.chk_network_deshabilitado)

        self.chk_network_otros_estados = QCheckBox("❔ Otros (desconectado, no presente...)")
        self.chk_network_otros_estados.setChecked(False)
        self.chk_network_otros_estados.setToolTip(
            "Adaptadores en otros estados que Windows reporta: Desconectado, No presente, "
            "Capa inferior inactiva, En pruebas, Latente, Desconocido")
        self.chk_network_otros_estados.stateChanged.connect(self._on_network_filter_changed)
        filters_state_layout.addWidget(self.chk_network_otros_estados)

        filters_state_layout.addStretch()
        layout.addLayout(filters_state_layout)

        # Tercera línea: Filtros por tipo
        filters_type_layout = QHBoxLayout()
        filters_type_layout.setSpacing(15)

        lbl_tipo = QLabel("Tipo:")
        lbl_tipo.setObjectName("fieldLabel")
        filters_type_layout.addWidget(lbl_tipo)

        self.chk_network_ethernet = QCheckBox("🔌 Ethernet")
        self.chk_network_ethernet.setChecked(True)
        self.chk_network_ethernet.stateChanged.connect(self._on_network_filter_changed)
        filters_type_layout.addWidget(self.chk_network_ethernet)

        self.chk_network_wifi = QCheckBox("📶 WiFi")
        self.chk_network_wifi.setChecked(True)
        self.chk_network_wifi.stateChanged.connect(self._on_network_filter_changed)
        filters_type_layout.addWidget(self.chk_network_wifi)

        self.chk_network_vpn = QCheckBox("🔐 VPN/Túnel")
        self.chk_network_vpn.setChecked(True)
        self.chk_network_vpn.stateChanged.connect(self._on_network_filter_changed)
        filters_type_layout.addWidget(self.chk_network_vpn)

        self.chk_network_virtual = QCheckBox("💻 Virtual")
        self.chk_network_virtual.setChecked(False)
        self.chk_network_virtual.setToolTip(
            "Adaptadores virtuales que Windows crea automáticamente (Hyper-V, WSL, VPN "
            "internas...) — casi nunca tienen IP/MAC/puerta de enlace propios, porque no "
            "corresponden a hardware real. Desmarcado por defecto para no saturar la tabla.")
        self.chk_network_virtual.stateChanged.connect(self._on_network_filter_changed)
        filters_type_layout.addWidget(self.chk_network_virtual)

        self.chk_network_otros = QCheckBox("🔧 Otros")
        self.chk_network_otros.setChecked(False)
        self.chk_network_otros.setToolTip(
            "Adaptadores ocultos/de sistema (Teredo, ISATAP, filtros WFP, Bluetooth PAN...) "
            "— igual que 'Virtual', casi nunca tienen datos de red reales que mostrar. "
            "Desmarcado por defecto para no saturar la tabla.")
        self.chk_network_otros.stateChanged.connect(self._on_network_filter_changed)
        filters_type_layout.addWidget(self.chk_network_otros)

        filters_type_layout.addStretch()

        self.lbl_network_count = QLabel("0/0 adaptadores")
        self.lbl_network_count.setObjectName("countLabel")
        filters_type_layout.addWidget(self.lbl_network_count)

        layout.addLayout(filters_type_layout)

        # Tabla
        self.network_model = NetworkAdapterTableModel([])
        self.network_proxy = NetworkAdapterFilterProxy(self)
        self.network_proxy.setSourceModel(self.network_model)
        self.network_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.network_proxy.setFilterKeyColumn(-1)
        # Inicializar con todos los filtros habilitados
        self._on_network_filter_changed()

        self.network_table = QTableView()
        self.network_table.setModel(self.network_proxy)
        self.network_table.setAlternatingRowColors(True)
        self.network_table.setSelectionBehavior(QTableView.SelectRows)
        self.network_table.verticalHeader().setVisible(False)
        self.network_table.setSortingEnabled(True)
        self.network_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.network_table.customContextMenuRequested.connect(self._ctx_redes)

        _setup_header(self.network_table, 11, stretch_col=1)

        layout.addWidget(self.network_table)

        return tab

    def _tick_rendimiento(self):
        """Actualiza los gauges circulares de CPU/RAM/Disco del panel hero
        del Dashboard (cada 2s, sin PowerShell — ctypes + shutil)."""
        try:
            cpu_pct, self._cpu_prev_times = get_cpu_percent(self._cpu_prev_times)
            self._gauge_cpu.set_value(cpu_pct)

            ram_pct, usado_gb, total_gb = get_memory_status()
            self._gauge_ram.set_value(ram_pct)

            total, used, free = shutil.disk_usage("C:\\")
            disk_pct = (used / total) * 100 if total else 0
            self._gauge_disk.set_value(disk_pct)
        except Exception as e:
            logger.error(f"Error actualizando métricas de rendimiento: {e}")

    def refresh_network_adapters(self):
        """Actualiza información de adaptadores de red."""
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.set_status("🔄 Escaneando adaptadores de red...")
        self.btn_refresh_network.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.worker = NetworkAdapterCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_network_data)
        self.worker.finished_error.connect(self._on_network_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_network_data(self, rows: list):
        """Procesa datos de adaptadores de red."""
        self.network_model.setRows(rows)
        self.btn_refresh_network.setEnabled(True)
        self.progress_bar.setVisible(False)
        self._update_network_counter()

    def _on_network_error(self, msg: str):
        """Maneja errores de adaptadores de red."""
        self.btn_refresh_network.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al escanear adaptadores de red")
        self._avisar("Error", f"No se pudo escanear los adaptadores:\n\n{msg}", QMessageBox.Warning)

    def _adaptador_seleccionado(self) -> Optional['NetworkAdapterRow']:
        sel = self.network_table.selectionModel()
        if not sel or not sel.hasSelection():
            return None
        idx = sel.selectedRows()[0]
        fuente_idx = self.network_proxy.mapToSource(idx)
        return self.network_model.rows()[fuente_idx.row()]

    def _ejecutar_accion_adaptador(self, cmdlet: str, verbo_gerundio: str, verbo_pasado: str,
                                    aviso_extra: str = ""):
        adaptador = self._adaptador_seleccionado()
        if not adaptador:
            self._avisar("Adaptadores de Red", "Selecciona un adaptador primero.")
            return
        if not is_admin():
            self._avisar(
                "Adaptadores de Red",
                "Se requieren permisos de administrador para gestionar adaptadores "
                "de red.\n\nReinicia MASys como administrador.", QMessageBox.Warning)
            return

        texto = f"Se va a {verbo_gerundio} el adaptador «{adaptador.nombre}»."
        if aviso_extra:
            texto += f"\n\n⚠️ {aviso_extra}"
        texto += "\n\n¿Continuar?"
        if not self._confirmar_accion("Confirmar acción", texto, QMessageBox.Warning):
            return

        nombre_escapado = escape_powershell_string(adaptador.nombre)
        self.set_status(f"🔄 {verbo_gerundio.capitalize()} adaptador «{adaptador.nombre}»...")
        ok, error = run_powershell_command(
            f"{cmdlet} -Name '{nombre_escapado}' -Confirm:$false", timeout=30)
        self._registrar_historial("🌐 Redes", f"{verbo_gerundio.capitalize()} adaptador", adaptador.nombre, ok, error)
        if ok:
            self.set_status(f"✅ Adaptador «{adaptador.nombre}» {verbo_pasado}")
            self.refresh_network_adapters()
        else:
            self.set_status(f"❌ Error al {verbo_gerundio} el adaptador")
            self._avisar("Error", f"No se pudo {verbo_gerundio} el adaptador:\n\n{error}",
                         QMessageBox.Critical)

    def _habilitar_adaptador_seleccionado(self):
        self._ejecutar_accion_adaptador("Enable-NetAdapter", "habilitar", "habilitado")

    def _deshabilitar_adaptador_seleccionado(self):
        self._ejecutar_accion_adaptador(
            "Disable-NetAdapter", "deshabilitar", "deshabilitado",
            aviso_extra="Si es el adaptador que usas para conectarte a este equipo "
                        "en remoto, perderás la conexión.")

    def _reiniciar_adaptador_seleccionado(self):
        self._ejecutar_accion_adaptador(
            "Restart-NetAdapter", "reiniciar", "reiniciado",
            aviso_extra="Si es el adaptador que usas para conectarte a este equipo "
                        "en remoto, la conexión se cortará unos segundos.")

    def _liberar_renovar_ip_seleccionado(self):
        adaptador = self._adaptador_seleccionado()
        if not adaptador:
            self._avisar("Liberar y renovar IP", "Selecciona un adaptador primero.")
            return
        self._liberar_renovar_ip(adaptador)

    def _ctx_redes(self, pos):
        adaptador = self._adaptador_seleccionado()
        if not adaptador:
            return

        # Solo se ofrecen las acciones que tienen sentido según el estado
        # actual: un adaptador Deshabilitado solo puede Habilitarse (no
        # tiene sentido "Reiniciar" algo que no está encendido); cualquier
        # otro estado (Activo, Inactivo, Desconectado...) ya está
        # habilitado, así que solo puede Deshabilitarse o Reiniciarse.
        menu = QMenu(self)
        if adaptador.estado == "Deshabilitado":
            act_habilitar = menu.addAction("▶️ Habilitar")
            act_deshabilitar = None
            act_reiniciar = None
        else:
            act_habilitar = None
            act_deshabilitar = menu.addAction("⏸️ Deshabilitar")
            act_reiniciar = menu.addAction("🔄 Reiniciar Adaptador")
        menu.addSeparator()
        act_renovar = None
        if adaptador.configuracion_ip == "DHCP":
            act_renovar = menu.addAction("🔁 Liberar y renovar IP")
        menu.addSeparator()
        act_copiar = menu.addAction("📋 Copiar fila")
        act_detalle = menu.addAction("🔍 Ver detalle completo")

        action = menu.exec_(self.network_table.viewport().mapToGlobal(pos))
        if act_habilitar is not None and action == act_habilitar:
            self._habilitar_adaptador_seleccionado()
        elif act_deshabilitar is not None and action == act_deshabilitar:
            self._deshabilitar_adaptador_seleccionado()
        elif act_reiniciar is not None and action == act_reiniciar:
            self._reiniciar_adaptador_seleccionado()
        elif act_renovar is not None and action == act_renovar:
            self._liberar_renovar_ip(adaptador)
        elif action == act_copiar:
            self._copiar_seleccion_generica(self.network_table, self.network_proxy, NetworkAdapterTableModel.HEADERS)
        elif action == act_detalle:
            self._ver_detalle_adaptador(adaptador)

    def _ver_detalle_adaptador(self, adaptador: NetworkAdapterRow):
        """Ventana informativa con TODOS los campos del adaptador, no solo
        los que caben en la tabla — mismo estándar visual que el resto de
        ventanas de información (_mostrar_informe)."""
        items = [
            f"Nombre: {adaptador.nombre}",
            f"Descripción: {adaptador.descripcion}",
            f"Estado: {adaptador.estado}",
            f"Tipo: {adaptador.tipo}",
            f"IP: {adaptador.ip}",
            f"MAC: {adaptador.mac}",
            f"Configuración IP: {adaptador.configuracion_ip}",
            f"Puerta de Enlace: {adaptador.gateway}",
            f"DNS: {adaptador.dns}",
            f"IPv6: {adaptador.ipv6}",
            f"Velocidad: {adaptador.velocidad}",
        ]
        self._mostrar_informe(f"Adaptador: {adaptador.nombre}", [], items)

    def _liberar_renovar_ip(self, adaptador: NetworkAdapterRow):
        """Libera y renueva la concesión DHCP del adaptador — la primera
        acción clásica de cualquier técnico ante un problema de red, y que
        antes no existía en ningún sitio de la app. Solo tiene sentido con
        IP por DHCP: con IP fija no hay ninguna concesión que renovar."""
        if adaptador.configuracion_ip != "DHCP":
            self._avisar("Liberar y renovar IP",
                         "Este adaptador tiene IP fija — liberar/renovar solo aplica a "
                         "adaptadores con IP por DHCP.", QMessageBox.Warning)
            return
        if not is_admin():
            self._avisar(
                "Liberar y renovar IP",
                "Se requieren permisos de administrador para gestionar adaptadores "
                "de red.\n\nReinicia MASys como administrador.", QMessageBox.Warning)
            return

        if not self._confirmar_reparacion(
            f"Liberar y renovar IP — {adaptador.nombre}",
            cambios=[f"Liberar la concesión DHCP actual de «{adaptador.nombre}»",
                     "Solicitar una nueva concesión DHCP inmediatamente después"],
            reversible=True,
            objetivo=adaptador.nombre,
            explicacion=(
                "El adaptador se quedará sin IP durante unos segundos entre la liberación "
                "y la nueva concesión. Si es el adaptador que usas para conectarte a este "
                "equipo en remoto, perderás la conexión brevemente."
            ),
        ):
            return

        self.set_status(f"🔄 Liberando y renovando IP de «{adaptador.nombre}»...")
        ok_release, err_release = run_system_command(["ipconfig", "/release", adaptador.nombre], timeout=30)
        ok_renew, err_renew = run_system_command(["ipconfig", "/renew", adaptador.nombre], timeout=30)
        ok = ok_release and ok_renew
        detalle = f"Release: {'OK' if ok_release else err_release}; Renew: {'OK' if ok_renew else err_renew}"
        if hasattr(self, 'repair_audit_db'):
            self.repair_audit_db.registrar(
                seccion="🌐 Redes", accion="Liberar y renovar IP",
                objetivo=adaptador.nombre, exito=ok, detalle=detalle,
            )
        if ok:
            self.set_status(f"✅ IP de «{adaptador.nombre}» liberada y renovada")
            self._avisar("Liberar y renovar IP",
                         f"✅ IP de «{adaptador.nombre}» liberada y renovada correctamente.")
            self.refresh_network_adapters()
        else:
            self._avisar("Error", f"No se pudo completar la operación:\n\n{detalle}", QMessageBox.Critical)

    def _on_network_filter_changed(self):
        """Actualiza filtros de estado y tipo de adaptadores de red."""
        enabled_states = set()
        if self.chk_network_activo.isChecked():
            enabled_states.add("Activo")
        if self.chk_network_inactivo.isChecked():
            enabled_states.add("Inactivo")
        if self.chk_network_deshabilitado.isChecked():
            enabled_states.add("Deshabilitado")
        if self.chk_network_otros_estados.isChecked():
            # Cualquier otro valor que Windows pueda reportar en Get-NetAdapter
            # (Disconnected, NotPresent, LowerLayerDown, Testing, Dormant...).
            # Antes de este fix, estos adaptadores no tenían ningún checkbox
            # que los cubriera y desaparecían de la tabla sin forma de
            # volver a mostrarlos (bug real: menos adaptadores en Redes de
            # los que realmente existen).
            enabled_states.update({"Desconectado", "No presente", "Capa inferior inactiva",
                                    "En pruebas", "Latente", "Desconocido"})

        enabled_types = set()
        if self.chk_network_ethernet.isChecked():
            enabled_types.add("Ethernet")
        if self.chk_network_wifi.isChecked():
            enabled_types.add("WiFi")
        if self.chk_network_vpn.isChecked():
            enabled_types.add("VPN")
            enabled_types.add("VPN (TAP)")
            enabled_types.add("Túnel/VPN")
        if self.chk_network_virtual.isChecked():
            enabled_types.add("Virtual")
        if self.chk_network_otros.isChecked():
            enabled_types.add("Otro")
            enabled_types.add("Bluetooth")

        self.network_proxy.setEnabledStates(enabled_states)
        self.network_proxy.setEnabledTypes(enabled_types)

        if hasattr(self, 'lbl_status'):
            self._update_network_counter()

    def _update_network_counter(self):
        """Actualiza el contador de adaptadores de red mostrados."""
        total = self.network_model.rowCount()
        visible = self.network_proxy.rowCount()
        self.lbl_network_count.setText(f"{visible}/{total} adaptadores")
        if total == visible:
            self.set_status(f"✅ {total} adaptadores")
        else:
            self.set_status(f"✅ Mostrando {visible} de {total} adaptadores")

    def apply_network_filter(self):
        """Aplica filtro de búsqueda en adaptadores de red."""
        pattern = self.search_network.text()
        rx = QRegExp(pattern, Qt.CaseInsensitive, QRegExp.RegExp)
        self.network_proxy.setFilterRegExp(rx)
        self._update_network_counter()

