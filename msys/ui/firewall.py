# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña Firewall: tabla, filtros, menu contextual, cambiar accion/estado de regla.
Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).

Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

from typing import List

from PyQt5.QtCore import Qt, QRegExp
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QCheckBox,
    QPushButton, QMenu, QMessageBox, QTableView,
)

from msys.core.utils import (
    escape_powershell_string, is_admin, run_powershell_command,
    run_powershell_command_params,
)
from msys.models.rows import FirewallRuleRow, FirewallProfileRow
from msys.models.table_models import FirewallRuleTableModel, FirewallFilterProxy
from msys.widgets.delegates import _setup_header
from msys.collectors import FirewallRuleCollector


class FirewallMixin:
    def crear_tab_firewall_rules(self):
        """Crea la pestaña de reglas de firewall."""
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

        self.search_firewall = QLineEdit()
        self.search_firewall.setPlaceholderText("🔎 Buscar reglas...")
        self.search_firewall.textChanged.connect(lambda: (
            self.firewall_proxy.setFilterRegExp(QRegExp(self.search_firewall.text(), Qt.CaseInsensitive, QRegExp.FixedString)),
            self._update_firewall_counter()
        ))
        actions_layout.addWidget(self.search_firewall, 3)

        # Estado del Firewall por perfil (Dominio/Privado/Público) — el
        # control más importante de todos: si un perfil está desactivado,
        # ninguna regla de ese perfil importa. Antes esta pestaña no lo
        # mostraba en absoluto. Va en la misma fila que el buscador y
        # "Actualizar" (no en una fila propia solo para esto).
        self.perfiles_layout = QHBoxLayout()
        self.perfiles_layout.setSpacing(6)
        self._lbl_perfiles = {}  # nombre -> QLabel, para refrescar sin reconstruir
        self.firewall_perfiles = []  # último estado de perfiles cargado, reusado por Soporte
        actions_layout.addLayout(self.perfiles_layout)

        actions_layout.addStretch()

        self.btn_refresh_firewall = QPushButton("🔄 Actualizar")
        self.btn_refresh_firewall.setObjectName("primary")
        self.btn_refresh_firewall.setToolTip("Todas las reglas de firewall (activas y desactivadas) y el estado de cada perfil")
        self.btn_refresh_firewall.clicked.connect(self.refresh_firewall_rules)
        actions_layout.addWidget(self.btn_refresh_firewall)

        layout.addLayout(actions_layout)

        # Filtros: dirección, acción, habilitada
        filters_layout = QHBoxLayout()
        filters_layout.setSpacing(15)

        lbl_direccion = QLabel("Dirección:")
        lbl_direccion.setObjectName("fieldLabel")
        filters_layout.addWidget(lbl_direccion)
        self.chk_fw_entrada = QCheckBox("⬇️ Entrada")
        self.chk_fw_entrada.setChecked(True)
        self.chk_fw_entrada.stateChanged.connect(self._apply_firewall_filters)
        filters_layout.addWidget(self.chk_fw_entrada)
        self.chk_fw_salida = QCheckBox("⬆️ Salida")
        self.chk_fw_salida.setChecked(True)
        self.chk_fw_salida.stateChanged.connect(self._apply_firewall_filters)
        filters_layout.addWidget(self.chk_fw_salida)

        filters_layout.addSpacing(20)

        lbl_accion = QLabel("Acción:")
        lbl_accion.setObjectName("fieldLabel")
        filters_layout.addWidget(lbl_accion)
        self.chk_fw_permitir = QCheckBox("✅ Permitir")
        self.chk_fw_permitir.setChecked(True)
        self.chk_fw_permitir.stateChanged.connect(self._apply_firewall_filters)
        filters_layout.addWidget(self.chk_fw_permitir)
        self.chk_fw_bloquear = QCheckBox("⛔ Bloquear")
        self.chk_fw_bloquear.setChecked(True)
        self.chk_fw_bloquear.stateChanged.connect(self._apply_firewall_filters)
        filters_layout.addWidget(self.chk_fw_bloquear)

        filters_layout.addSpacing(20)

        lbl_habilitada = QLabel("Estado:")
        lbl_habilitada.setObjectName("fieldLabel")
        filters_layout.addWidget(lbl_habilitada)
        self.chk_fw_habilitadas = QCheckBox("🟢 Habilitadas")
        self.chk_fw_habilitadas.setChecked(True)
        self.chk_fw_habilitadas.stateChanged.connect(self._apply_firewall_filters)
        filters_layout.addWidget(self.chk_fw_habilitadas)
        self.chk_fw_deshabilitadas = QCheckBox("⚪ Deshabilitadas")
        self.chk_fw_deshabilitadas.setChecked(True)
        self.chk_fw_deshabilitadas.stateChanged.connect(self._apply_firewall_filters)
        filters_layout.addWidget(self.chk_fw_deshabilitadas)

        filters_layout.addStretch()
        layout.addLayout(filters_layout)

        # Segunda línea: Origen (Sistema/Terceros) + contador de reglas
        # (totalmente a la derecha) — separada de Dirección/Acción/Estado
        # porque las cuatro juntas en una sola fila iban muy apretadas.
        filters_layout2 = QHBoxLayout()
        filters_layout2.setSpacing(15)

        lbl_origen = QLabel("Origen:")
        lbl_origen.setObjectName("fieldLabel")
        filters_layout2.addWidget(lbl_origen)
        self.chk_fw_sistema = QCheckBox("🪟 Sistema (Windows)")
        self.chk_fw_sistema.setToolTip(
            "Reglas propias de Windows (grupo con prefijo '@', p. ej. "
            "@FirewallAPI.dll). Desmarca para ver solo aplicaciones de terceros.")
        self.chk_fw_sistema.setChecked(True)
        self.chk_fw_sistema.stateChanged.connect(self._apply_firewall_filters)
        filters_layout2.addWidget(self.chk_fw_sistema)
        self.chk_fw_terceros = QCheckBox("📦 Terceros/Aplicaciones")
        self.chk_fw_terceros.setChecked(True)
        self.chk_fw_terceros.stateChanged.connect(self._apply_firewall_filters)
        filters_layout2.addWidget(self.chk_fw_terceros)

        filters_layout2.addStretch()

        self.lbl_firewall_count = QLabel("0/0 reglas")
        self.lbl_firewall_count.setObjectName("countLabel")
        filters_layout2.addWidget(self.lbl_firewall_count)

        layout.addLayout(filters_layout2)

        # Tabla
        self.firewall_model = FirewallRuleTableModel([])
        self.firewall_proxy = FirewallFilterProxy(self)
        self.firewall_proxy.setSourceModel(self.firewall_model)
        self.firewall_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.firewall_proxy.setFilterKeyColumn(-1)

        self.firewall_table = QTableView()
        self.firewall_table.setModel(self.firewall_proxy)
        self.firewall_table.setAlternatingRowColors(True)
        self.firewall_table.setSelectionBehavior(QTableView.SelectRows)
        self.firewall_table.verticalHeader().setVisible(False)
        self.firewall_table.setSortingEnabled(True)
        self.firewall_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.firewall_table.customContextMenuRequested.connect(self._ctx_firewall)

        _setup_header(self.firewall_table, len(FirewallRuleTableModel.HEADERS), stretch_col=0)

        layout.addWidget(self.firewall_table)

        return tab

    # REMOVED - Browser Passwords tab
    # def crear_tab_browser_passwords(self):
    #     """Crea la pestaña de contraseñas de navegadores."""
    #     tab = QWidget()
    #     layout = QVBoxLayout(tab)
    #     layout.setSpacing(8)
    #     layout.setContentsMargins(10, 10, 10, 10)
    #
    #     # Botones de acción
    #     btn_layout = QHBoxLayout()
    #     self.btn_refresh_browser_pwd = QPushButton("🔄 Buscar Contraseñas")
    #     self.btn_refresh_browser_pwd.setObjectName("primary")
    #     self.btn_refresh_browser_pwd.clicked.connect(self.refresh_browser_passwords)
    #     btn_layout.addWidget(self.btn_refresh_browser_pwd)
    #
    #     info_label = QLabel("ℹ️ Detección de contraseñas guardadas en navegadores")
    #     info_label.setObjectName("hintLabel")
    #     btn_layout.addWidget(info_label)
    #
    #     btn_layout.addStretch()
    #     layout.addLayout(btn_layout)
    #
    #     # Barra de búsqueda y acciones
    #     actions_layout = QHBoxLayout()
    #     actions_layout.setSpacing(8)
    #
    #     import socket
    #     hostname = socket.gethostname()
    #     lbl_hostname = QLabel(f"🖥️ {hostname}")
    #     lbl_hostname.setObjectName("hostnameLabel")
    #     actions_layout.addWidget(lbl_hostname)
    #
    #     self.search_browser_pwd = QLineEdit()
    #     self.search_browser_pwd.setPlaceholderText("🔎 Buscar...")
    #     self.search_browser_pwd.textChanged.connect(lambda: self.browser_pwd_proxy.setFilterRegExp(QRegExp(self.search_browser_pwd.text(), Qt.CaseInsensitive, QRegExp.FixedString)))
    #     actions_layout.addWidget(self.search_browser_pwd, 3)
    #
    #     self.btn_copy_browser_pwd = QPushButton("📋 Copiar")
    #     self.btn_copy_browser_pwd.setObjectName("ghost")
    #     self.btn_copy_browser_pwd.setFixedWidth(100)
    #     self.btn_copy_browser_pwd.clicked.connect(self.copy_browser_pwd_selection)
    #     actions_layout.addWidget(self.btn_copy_browser_pwd)
    #
    #     self.btn_export_csv_browser_pwd = QPushButton("📄 CSV")
    #     self.btn_export_csv_browser_pwd.setObjectName("ghost")
    #     self.btn_export_csv_browser_pwd.setFixedWidth(80)
    #     self.btn_export_csv_browser_pwd.clicked.connect(self.export_browser_pwd_csv)
    #     actions_layout.addWidget(self.btn_export_csv_browser_pwd)
    #
    #     self.btn_export_html_browser_pwd = QPushButton("🌐 HTML")
    #     self.btn_export_html_browser_pwd.setObjectName("ghost")
    #     self.btn_export_html_browser_pwd.setFixedWidth(110)
    #     self.btn_export_html_browser_pwd.clicked.connect(self.export_browser_pwd_html)
    #     actions_layout.addWidget(self.btn_export_html_browser_pwd)
    #
    #     layout.addLayout(actions_layout)
    #
    #     # Tabla
    #     self.browser_pwd_model = BrowserPasswordTableModel([])
    #     self.browser_pwd_proxy = _FechaAwareSortProxy(self)
    #     self.browser_pwd_proxy.setSourceModel(self.browser_pwd_model)
    #     self.browser_pwd_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
    #     self.browser_pwd_proxy.setFilterKeyColumn(-1)
    #
    #     self.browser_pwd_table = QTableView()
    #     self.browser_pwd_table.setModel(self.browser_pwd_proxy)
    #     self.browser_pwd_table.setAlternatingRowColors(True)
    #     self.browser_pwd_table.setSelectionBehavior(QTableView.SelectRows)
    #     self.browser_pwd_table.verticalHeader().setVisible(False)
    #     self.browser_pwd_table.setSortingEnabled(True)
    #
    #     header = self.browser_pwd_table.horizontalHeader()
    #     header.setStretchLastSection(False)
    #     # Configurar todas las columnas como Interactive para permitir redimensionamiento manual
    #     for i in range(5):
    #         header.setSectionResizeMode(i, QHeaderView.Interactive)
    #
    #     layout.addWidget(self.browser_pwd_table)
    #
    #     return tab


    def refresh_firewall_rules(self):
        """Actualiza lista de reglas de firewall."""
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.set_status("🔄 Escaneando reglas de firewall...")
        self.btn_refresh_firewall.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.worker = FirewallRuleCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_firewall_data)
        self.worker.finished_error.connect(self._on_firewall_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_firewall_data(self, data: dict):
        """Procesa datos de reglas + perfiles de firewall."""
        rows = data.get("reglas", [])
        perfiles = data.get("perfiles", [])
        self.firewall_model.setRows(rows)
        self.firewall_perfiles = perfiles
        self._actualizar_badges_perfiles(perfiles)
        self.btn_refresh_firewall.setEnabled(True)
        self.progress_bar.setVisible(False)
        total = len(rows)
        self.set_status(f"✅ {total} reglas de firewall encontradas")
        self._update_firewall_counter()

    def _actualizar_badges_perfiles(self, perfiles: List[FirewallProfileRow]):
        """Badges de color por perfil (Dominio/Privado/Público) — clic para
        activar/desactivar. Se reconstruyen en cada actualización porque el
        número de perfiles reales podría variar (aunque en la práctica
        Windows siempre da los 3)."""
        while self.perfiles_layout.count() > 0:
            item = self.perfiles_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        for perfil in perfiles:
            btn = QPushButton(f"{'🟢' if perfil.habilitado else '🔴'} {perfil.nombre_es}: "
                               f"{'Activo' if perfil.habilitado else 'Inactivo'}")
            btn.setStyleSheet(
                "QPushButton { background-color: %s; color: white; padding: 3px 8px; "
                "border-radius: 4px; font-size: 8.5pt; }" % ("#16a34a" if perfil.habilitado else "#dc2626"))
            btn.setToolTip(
                f"Clic para {'desactivar' if perfil.habilitado else 'activar'} "
                f"el firewall en el perfil {perfil.nombre_es}")
            btn.clicked.connect(lambda checked=False, p=perfil: self._toggle_perfil_firewall(p))
            self.perfiles_layout.addWidget(btn)

    def _toggle_perfil_firewall(self, perfil: FirewallProfileRow):
        if not is_admin():
            self._avisar("Firewall",
                         "Se requieren permisos de administrador para cambiar el estado "
                         "del firewall.\n\nReinicia MASys como administrador.",
                         QMessageBox.Warning)
            return

        accion_texto = "Desactivar" if perfil.habilitado else "Activar"
        if not self._confirmar_reparacion(
            f"{accion_texto} Firewall — perfil {perfil.nombre_es}",
            cambios=[f"{accion_texto} el Firewall de Windows para el perfil {perfil.nombre_es}"],
            reversible=True,
            objetivo=perfil.nombre_es,
            explicacion=(
                "Este perfil de red quedará sin ninguna protección de entrada/salida "
                "hasta que se reactive." if perfil.habilitado else None
            ),
            es_alerta=perfil.habilitado,  # desactivar es lo arriesgado, no activar
        ):
            return

        nuevo_valor = "False" if perfil.habilitado else "True"
        ok, msg = run_powershell_command_params(
            "Set-NetFirewallProfile -Name '{nombre}' -Enabled " + nuevo_valor,
            nombre=perfil.nombre,
        )
        self.repair_audit_db.registrar(
            seccion="🔥 Firewall", accion=f"{accion_texto} Firewall",
            objetivo=perfil.nombre_es, exito=ok, detalle=msg,
        )
        if ok:
            self.set_status(f"✅ Firewall {'activado' if accion_texto == 'Activar' else 'desactivado'} "
                             f"para el perfil {perfil.nombre_es}")
            self.refresh_firewall_rules()
        else:
            self._avisar("Error", f"No se pudo cambiar el estado del firewall:\n\n{msg}", QMessageBox.Warning)

    def _on_firewall_error(self, msg: str):
        """Maneja errores de reglas de firewall."""
        self.btn_refresh_firewall.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al escanear reglas de firewall")
        self._avisar("Error", f"No se pudieron obtener las reglas de firewall:\n\n{msg}", QMessageBox.Warning)

    def _apply_firewall_filters(self):
        self.firewall_proxy.set_filtros(
            self.chk_fw_entrada.isChecked(), self.chk_fw_salida.isChecked(),
            self.chk_fw_permitir.isChecked(), self.chk_fw_bloquear.isChecked(),
            self.chk_fw_habilitadas.isChecked(), self.chk_fw_deshabilitadas.isChecked(),
            self.chk_fw_sistema.isChecked(), self.chk_fw_terceros.isChecked(),
        )
        self._update_firewall_counter()

    def _update_firewall_counter(self):
        total = self.firewall_model.rowCount()
        visible = self.firewall_proxy.rowCount()
        self.lbl_firewall_count.setText(f"{visible}/{total} reglas")

    def _ctx_firewall(self, pos):
        idx = self.firewall_table.indexAt(pos)
        if not idx.isValid():
            return
        fuente_idx = self.firewall_proxy.mapToSource(self.firewall_proxy.index(idx.row(), 0))
        regla = self.firewall_model.rows()[fuente_idx.row()]

        menu = QMenu(self)
        if regla.accion == "Permitir":
            act_toggle_accion = menu.addAction("⛔ Cambiar a Bloquear")
        else:
            act_toggle_accion = menu.addAction("✅ Cambiar a Permitir")
        if regla.habilitada.strip().lower() in ("sí", "si", "true"):
            act_toggle_estado = menu.addAction("⚪ Deshabilitar regla")
        else:
            act_toggle_estado = menu.addAction("🟢 Habilitar regla")
        menu.addSeparator()
        act_eliminar = menu.addAction("✕ Eliminar regla")

        action = menu.exec_(self.firewall_table.viewport().mapToGlobal(pos))
        if action == act_toggle_accion:
            self._firewall_cambiar_accion(regla)
        elif action == act_toggle_estado:
            self._firewall_cambiar_estado(regla)
        elif action == act_eliminar:
            self._firewall_eliminar_regla(regla)

    def _firewall_cambiar_accion(self, regla: FirewallRuleRow):
        if not is_admin():
            self._avisar("Firewall",
                         "Se requieren permisos de administrador para modificar reglas "
                         "de firewall.\n\nReinicia MASys como administrador.",
                         QMessageBox.Warning)
            return
        if not regla.nombre_interno:
            self._avisar("Firewall", "No se pudo identificar la regla de forma única.",
                         QMessageBox.Warning)
            return
        nueva_accion = "Block" if regla.accion == "Permitir" else "Allow"
        texto_nuevo = "Bloquear" if nueva_accion == "Block" else "Permitir"
        if not self._confirmar_accion(
                "Confirmar cambio",
                f"Regla: {regla.nombre}\n\n"
                f"Se cambiará la acción de «{regla.accion}» a «{texto_nuevo}».\n\n"
                "¿Continuar?", QMessageBox.Warning):
            return
        nombre_escapado = escape_powershell_string(regla.nombre_interno)
        ok, error = run_powershell_command(
            f"$r = @(Get-NetFirewallRule | Where-Object {{ $_.Name -eq '{nombre_escapado}' }}); if ($r.Count -ne 1) {{ throw 'No se encuentra la regla' }}; $r[0] | Set-NetFirewallRule -Action {nueva_accion}")
        self._registrar_historial("🔥 Firewall", f"Cambiar regla a {texto_nuevo}", regla.nombre, ok, error)
        if ok:
            regla.accion = texto_nuevo
            self.firewall_model.setRows(self.firewall_model.rows())
            self._update_firewall_counter()
            self.set_status(f"✅ Regla «{regla.nombre}» cambiada a {texto_nuevo}")
        else:
            self._avisar("Error", f"No se pudo cambiar la regla:\n\n{error}", QMessageBox.Critical)

    def _firewall_cambiar_estado(self, regla: FirewallRuleRow):
        if not is_admin():
            self._avisar("Firewall",
                         "Se requieren permisos de administrador para modificar reglas "
                         "de firewall.\n\nReinicia MASys como administrador.",
                         QMessageBox.Warning)
            return
        if not regla.nombre_interno:
            self._avisar("Firewall", "No se pudo identificar la regla de forma única.",
                         QMessageBox.Warning)
            return
        habilitada_ahora = regla.habilitada.strip().lower() in ("sí", "si", "true")
        nuevo_valor = "False" if habilitada_ahora else "True"
        texto_nuevo = "deshabilitada" if habilitada_ahora else "habilitada"
        if not self._confirmar_accion(
                "Confirmar cambio",
                f"Regla: {regla.nombre}\n\nQuedará {texto_nuevo}.\n\n¿Continuar?",
                QMessageBox.Warning):
            return
        nombre_escapado = escape_powershell_string(regla.nombre_interno)
        ok, error = run_powershell_command(
            f"$r = @(Get-NetFirewallRule | Where-Object {{ $_.Name -eq '{nombre_escapado}' }}); if ($r.Count -ne 1) {{ throw 'No se encuentra la regla' }}; $r[0] | Set-NetFirewallRule -Enabled {nuevo_valor}")
        self._registrar_historial("🔥 Firewall", "Habilitar regla" if nuevo_valor == "True" else "Deshabilitar regla",
                                  regla.nombre, ok, error)
        if ok:
            regla.habilitada = "No" if habilitada_ahora else "Sí"
            self.firewall_model.setRows(self.firewall_model.rows())
            self._update_firewall_counter()
            self.set_status(f"✅ Regla «{regla.nombre}» {texto_nuevo}")
        else:
            self._avisar("Error", f"No se pudo cambiar la regla:\n\n{error}", QMessageBox.Critical)

    def _firewall_eliminar_regla(self, regla: FirewallRuleRow):
        """A diferencia de cambiar acción/estado (reversible con otro
        clic), eliminar una regla del sistema es definitivo: si es una
        regla de Windows habría que recrearla a mano o reparar con
        'netsh advfirewall reset'. Complementa a "Bloquear IP en el
        Firewall" de Conexiones Red — ahora sí hay una forma de quitar
        esas reglas 'MASys - Bloqueo IP ...' sin salir de la app."""
        if not is_admin():
            self._avisar("Firewall",
                         "Se requieren permisos de administrador para eliminar reglas "
                         "de firewall.\n\nReinicia MASys como administrador.",
                         QMessageBox.Warning)
            return
        if not regla.nombre_interno:
            self._avisar("Firewall", "No se pudo identificar la regla de forma única.",
                         QMessageBox.Warning)
            return

        cambios = [f"Eliminar la regla «{regla.nombre}» ({regla.direccion}, {regla.accion})"]
        explicacion = None
        if regla.es_sistema:
            explicacion = ("⚠️ Esta es una regla propia de Windows. Eliminarla puede afectar "
                            "a una función del sistema (Compartir archivos, Escritorio remoto, "
                            "Detección de redes...) — solo continúa si sabes qué es.")
        if not self._confirmar_reparacion(
            "Eliminar regla de firewall",
            cambios=cambios,
            reversible=False,
            objetivo=regla.nombre,
            explicacion=explicacion,
            es_alerta=regla.es_sistema,
        ):
            return

        nombre_escapado = escape_powershell_string(regla.nombre_interno)
        # 5.5.0: copia de TODA la configuración del firewall antes de borrar (netsh advfirewall import la restaura)
        ok_copia, copia = self._copia_previa(
            "FIREWALL", "firewall_antes_de_eliminar.wfw",
            "netsh advfirewall export '{ruta}' | Out-Null; if ($LASTEXITCODE -ne 0) {{ throw 'netsh devolvió error' }}")
        if not ok_copia and not self._seguir_sin_copia("la configuración del firewall", copia):
            return
        # Por nombre exacto: -Name admite comodines y con corchetes podía borrar otra regla.
        ok, error = run_powershell_command(f"$r = @(Get-NetFirewallRule | Where-Object {{ $_.Name -eq '{nombre_escapado}' }}); if ($r.Count -ne 1) {{ throw 'No se encuentra la regla' }}; $r[0] | Remove-NetFirewallRule")
        if ok and ok_copia:
            error = f"Copia previa: {copia} (restaurar: netsh advfirewall import \"{copia}\")"
        self.repair_audit_db.registrar(
            seccion="🔥 Firewall", accion="Eliminar regla de firewall",
            objetivo=regla.nombre, exito=ok, detalle=error or "OK",
        )
        if ok:
            nuevas_filas = [r for r in self.firewall_model.rows() if r is not regla]
            self.firewall_model.setRows(nuevas_filas)
            self._update_firewall_counter()
            self.set_status(f"✅ Regla «{regla.nombre}» eliminada")
        else:
            self._avisar("Error", f"No se pudo eliminar la regla:\n\n{error}", QMessageBox.Critical)

