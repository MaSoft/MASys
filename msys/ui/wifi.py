# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña WiFi: tabla, filtros, refresco.
Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).

Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

from typing import List

from PyQt5.QtCore import Qt, QRegExp
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QCheckBox,
    QPushButton, QMessageBox, QTableView, QMenu, QApplication,
)

from msys.models.rows import WiFiRow
from msys.models.table_models import WiFiTableModel, WiFiFilterProxy
from msys.widgets.delegates import _setup_header
from msys.collectors import WiFiCollector
from msys.core.utils import (
    wifi_olvidar_perfil, wifi_bloquear_red, wifi_desbloquear_red,
    wifi_red_esta_bloqueada, wifi_exportar_perfil, dialogo_elegir_carpeta,
    wifi_estado_conexion_actual, wifi_desconectar,
)
from msys.ui.wifi_qr_dialog import WiFiQRDialog
from msys.ui.wifi_scan_dialog import WiFiScanDialog


class WiFiMixin:
    def crear_tab_wifi(self):
        """Crea la pestaña de WiFi."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        # Barra de búsqueda y acciones
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)

        import socket
        hostname = socket.gethostname()
        lbl_hostname_wifi = QLabel(f"🖥️ {hostname}")
        lbl_hostname_wifi.setObjectName("hostnameLabel")
        actions_layout.addWidget(lbl_hostname_wifi)

        self.search_wifi = QLineEdit()
        self.search_wifi.setPlaceholderText("🔎 Buscar redes WiFi...")
        self.search_wifi.textChanged.connect(self.apply_wifi_filter)
        actions_layout.addWidget(self.search_wifi, 3)

        actions_layout.addStretch()

        self.btn_wifi_detectar = QPushButton("🔍 Detectar redes cercanas")
        self.btn_wifi_detectar.setToolTip("Escanea redes visibles ahora mismo, tengan o no perfil guardado")
        self.btn_wifi_detectar.clicked.connect(self._wifi_detectar_redes_cercanas)
        actions_layout.addWidget(self.btn_wifi_detectar)

        self.btn_wifi_desconectar = QPushButton("🔌 Desconectar")
        self.btn_wifi_desconectar.setToolTip("Desconecta la red WiFi activa ahora mismo")
        self.btn_wifi_desconectar.clicked.connect(self._wifi_desconectar_actual)
        actions_layout.addWidget(self.btn_wifi_desconectar)

        self.btn_refresh_wifi = QPushButton("🔄 Actualizar")
        self.btn_refresh_wifi.setObjectName("primary")
        self.btn_refresh_wifi.setToolTip(
            "Contraseñas WiFi guardadas (requiere privilegios de administrador)")
        self.btn_refresh_wifi.clicked.connect(self.refresh_wifi)
        actions_layout.addWidget(self.btn_refresh_wifi)

        layout.addLayout(actions_layout)

        # Filtros: seguridad y estado de conexión, debajo de la búsqueda
        filters_layout = QHBoxLayout()
        filters_layout.setSpacing(15)

        lbl_seguridad = QLabel("Seguridad:")
        lbl_seguridad.setObjectName("fieldLabel")
        filters_layout.addWidget(lbl_seguridad)

        self.chk_wifi_seguras = QCheckBox("🔒 Seguras (WPA2/WPA3)")
        self.chk_wifi_seguras.setChecked(True)
        self.chk_wifi_seguras.stateChanged.connect(self._apply_wifi_filters)
        filters_layout.addWidget(self.chk_wifi_seguras)

        self.chk_wifi_inseguras = QCheckBox("⚠️ Inseguras (Abierta/WEP)")
        self.chk_wifi_inseguras.setChecked(True)
        self.chk_wifi_inseguras.stateChanged.connect(self._apply_wifi_filters)
        filters_layout.addWidget(self.chk_wifi_inseguras)

        filters_layout.addSpacing(30)

        self.chk_wifi_solo_conectada = QCheckBox("✅ Solo conectada actualmente")
        self.chk_wifi_solo_conectada.setChecked(False)
        self.chk_wifi_solo_conectada.stateChanged.connect(self._apply_wifi_filters)
        filters_layout.addWidget(self.chk_wifi_solo_conectada)

        filters_layout.addStretch()

        self.lbl_wifi_count = QLabel("0/0 redes")
        self.lbl_wifi_count.setObjectName("countLabel")
        filters_layout.addWidget(self.lbl_wifi_count)

        layout.addLayout(filters_layout)

        # Tabla de redes WiFi
        self.wifi_model = WiFiTableModel([])
        self.wifi_proxy = WiFiFilterProxy(self)
        self.wifi_proxy.setSourceModel(self.wifi_model)
        self.wifi_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.wifi_proxy.setFilterKeyColumn(-1)

        self.wifi_table = QTableView()
        self.wifi_table.setModel(self.wifi_proxy)
        self.wifi_table.setAlternatingRowColors(True)
        self.wifi_table.setSelectionBehavior(QTableView.SelectRows)
        self.wifi_table.verticalHeader().setVisible(False)
        self.wifi_table.setSortingEnabled(True)
        self.wifi_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.wifi_table.customContextMenuRequested.connect(self._mostrar_menu_contextual_wifi)

        _setup_header(self.wifi_table, 6, stretch_col=0)

        layout.addWidget(self.wifi_table)

        return tab

    # ── Menú contextual: gestión de la red seleccionada ─────────────────

    def _wifi_fila_bajo_cursor(self, pos):
        """Selecciona (si hace falta) y devuelve la WiFiRow bajo el
        cursor, o None si el clic fue fuera de cualquier fila."""
        indice = self.wifi_table.indexAt(pos)
        if not indice.isValid():
            return None
        self.wifi_table.selectRow(indice.row())
        fuente = self.wifi_proxy.mapToSource(indice)
        return self.wifi_model.rows()[fuente.row()]

    def _mostrar_menu_contextual_wifi(self, pos):
        row = self._wifi_fila_bajo_cursor(pos)
        if row is None:
            return

        menu = QMenu(self)
        menu.addAction("📋 Copiar contraseña", lambda: self._wifi_copiar_password(row))
        menu.addAction("📱 Generar código QR", lambda: self._wifi_generar_qr(row))
        menu.addAction("💾 Exportar perfil (.xml)", lambda: self._wifi_exportar_perfil(row))
        menu.addSeparator()

        if wifi_red_esta_bloqueada(row.nombre):
            menu.addAction("✅ Desbloquear red", lambda: self._wifi_desbloquear(row))
        else:
            menu.addAction("🚫 Bloquear red", lambda: self._wifi_bloquear(row))
        menu.addAction("✕ Olvidar red", lambda: self._wifi_olvidar(row))

        menu.exec_(self.wifi_table.viewport().mapToGlobal(pos))

    def _wifi_copiar_password(self, row: WiFiRow):
        QApplication.clipboard().setText(row.contraseña)
        self.set_status(f"✅ Contraseña de «{row.nombre}» copiada al portapapeles")

    def _wifi_generar_qr(self, row: WiFiRow):
        # Distinguir "red abierta" (sin contraseña, legítimo) de "no se
        # pudo leer la contraseña" (WEP/WPA sin clave real, el QR saldría
        # incompleto e inútil) — es_seguridad_debil no sirve aquí porque
        # también es True para WEP, que SÍ necesita una clave real.
        auth_lower = row.autenticacion.strip().lower()
        es_red_abierta = auth_lower in ("open", "abierta", "ninguna", "none", "")
        password_vacia = not row.contraseña or row.contraseña.strip().upper() == "N/A"
        if password_vacia and not es_red_abierta:
            self._avisar("Generar código QR", "No se pudo leer la contraseña de esta red (revisa que MASys "
                "se esté ejecutando como administrador).")
            return
        dlg = WiFiQRDialog(row.nombre, row.contraseña, row.autenticacion, self)
        dlg.exec_()

    def _wifi_exportar_perfil(self, row: WiFiRow):
        carpeta = dialogo_elegir_carpeta(self, "Elegir carpeta de destino", "C:\\")
        if not carpeta:
            return
        ok, mensaje = wifi_exportar_perfil(row.nombre, carpeta)
        if ok:
            self.set_status(f"✅ Perfil de «{row.nombre}» exportado a {carpeta}")
        else:
            self._avisar("Exportar perfil", f"No se pudo exportar el perfil:\n\n{mensaje}", QMessageBox.Warning)

    def _wifi_olvidar(self, row: WiFiRow):
        if not self._confirmar_reparacion(
            "Olvidar red WiFi",
            cambios=[f"Eliminar el perfil guardado de «{row.nombre}» (incluida la contraseña) de este equipo"],
            reversible=False,
            objetivo=row.nombre,
            explicacion="No afecta al router ni a otros equipos — solo a lo que este PC recuerda.",
        ):
            return

        ok, mensaje = wifi_olvidar_perfil(row.nombre)
        self.repair_audit_db.registrar(
            seccion="📶 WiFi", accion="Olvidar red WiFi",
            objetivo=row.nombre, exito=ok, detalle=mensaje,
        )
        if ok:
            self.set_status(f"✅ Red «{row.nombre}» olvidada")
            self.refresh_wifi()
        else:
            self._avisar("Olvidar red", f"No se pudo olvidar la red:\n\n{mensaje}", QMessageBox.Warning)

    def _wifi_bloquear(self, row: WiFiRow):
        if not self._confirmar_reparacion(
            "Bloquear red WiFi",
            cambios=[f"Impedir que Windows se conecte a «{row.nombre}», a mano o automáticamente"],
            reversible=True,
            objetivo=row.nombre,
            explicacion="El perfil guardado (si existe) no se toca — solo se añade un filtro de bloqueo.",
        ):
            return

        ok, mensaje = wifi_bloquear_red(row.nombre)
        self.repair_audit_db.registrar(
            seccion="📶 WiFi", accion="Bloquear red WiFi",
            objetivo=row.nombre, exito=ok, detalle=mensaje,
        )
        if ok:
            self.set_status(f"✅ Red «{row.nombre}» bloqueada")
        else:
            self._avisar("Bloquear red", f"No se pudo bloquear la red:\n\n{mensaje}", QMessageBox.Warning)

    def _wifi_desbloquear(self, row: WiFiRow):
        if not self._confirmar_reparacion(
            "Desbloquear red WiFi",
            cambios=[f"Quitar el filtro de bloqueo de «{row.nombre}»"],
            reversible=True,
            objetivo=row.nombre,
        ):
            return

        ok, mensaje = wifi_desbloquear_red(row.nombre)
        self.repair_audit_db.registrar(
            seccion="📶 WiFi", accion="Desbloquear red WiFi",
            objetivo=row.nombre, exito=ok, detalle=mensaje,
        )
        if ok:
            self.set_status(f"✅ Red «{row.nombre}» desbloqueada")
        else:
            self._avisar("Desbloquear red", f"No se pudo desbloquear la red:\n\n{mensaje}", QMessageBox.Warning)

    def refresh_wifi(self):
        """Actualiza lista de redes WiFi."""
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.set_status("🔄 Escaneando redes WiFi...")
        self.btn_refresh_wifi.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.worker = WiFiCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_wifi_data)
        self.worker.finished_error.connect(self._on_wifi_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_wifi_data(self, rows: List[WiFiRow]):
        """Procesa datos de WiFi."""
        self.wifi_model.setRows(rows)
        self.btn_refresh_wifi.setEnabled(True)
        self.progress_bar.setVisible(False)
        total = len(rows)
        self.set_status(f"✅ {total} redes WiFi encontradas")
        self._update_wifi_counter()

    def _on_wifi_error(self, msg: str):
        """Maneja errores de WiFi."""
        self.btn_refresh_wifi.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al escanear WiFi")
        self._avisar("Error", f"No se pudieron obtener las redes WiFi:\n\n{msg}\n\nNota: Se requieren privilegios de administrador", QMessageBox.Warning)

    def apply_wifi_filter(self):
        """Aplica filtro de búsqueda en WiFi."""
        pattern = self.search_wifi.text()
        rx = QRegExp(pattern, Qt.CaseInsensitive, QRegExp.FixedString)  # literal, como el resto de MASys: «C:\Program Files» u «Office (64» no encontraban nada
        self.wifi_proxy.setFilterRegExp(rx)
        self._update_wifi_counter()

    def _apply_wifi_filters(self):
        """Aplica los checkboxes de seguridad/conectada (además del texto
        de búsqueda, que ya gestiona apply_wifi_filter)."""
        self.wifi_proxy.set_filtros(
            self.chk_wifi_seguras.isChecked(),
            self.chk_wifi_inseguras.isChecked(),
            self.chk_wifi_solo_conectada.isChecked(),
        )
        self._update_wifi_counter()

    def _update_wifi_counter(self):
        total = self.wifi_model.rowCount()
        visible = self.wifi_proxy.rowCount()
        self.lbl_wifi_count.setText(f"{visible}/{total} redes")

    # ── Detectar redes cercanas / Desconectar ───────────────────────────

    def _wifi_detectar_redes_cercanas(self):
        nombres_con_perfil = {row.nombre for row in self.wifi_model.rows()}
        dlg = WiFiScanDialog(nombres_con_perfil, self)
        dlg.exec_()

    def _wifi_desconectar_actual(self):
        estado = wifi_estado_conexion_actual()
        if estado is None:
            self._avisar("Desconectar", "No hay ninguna red WiFi conectada ahora mismo.")
            return

        if not self._confirmar_reparacion(
            "Desconectar red WiFi",
            cambios=[f"Desconectar la red WiFi activa: «{estado['ssid']}»"],
            reversible=True,
            objetivo=estado["ssid"],
            explicacion=(
                "⚠️ Si este equipo se está administrando en remoto A TRAVÉS de esta "
                "misma red WiFi (p. ej. escritorio remoto por WiFi, sin cable de red), "
                "desconectar cortará también esa sesión remota al instante."
            ),
            es_alerta=True,
        ):
            return

        ok, mensaje = wifi_desconectar(estado["interfaz"])
        self.repair_audit_db.registrar(
            seccion="📶 WiFi", accion="Desconectar red WiFi",
            objetivo=estado["ssid"], exito=ok, detalle=mensaje,
        )
        if ok:
            self.set_status(f"✅ Desconectado de «{estado['ssid']}»")
        else:
            self._avisar("Desconectar", f"No se pudo desconectar:\n\n{mensaje}", QMessageBox.Warning)

