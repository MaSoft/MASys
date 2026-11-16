# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña Conexiones Remotas: tabla, filtro, refresco.
Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).

Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

import ipaddress
from typing import List

from PyQt5.QtCore import Qt, QRegExp
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QCheckBox,
    QPushButton, QMessageBox, QTableView, QMenu,
)

from msys.core.utils import (
    run_powershell_command, run_powershell_json, run_powershell_command_params, is_admin,
)
from msys.models.rows import RemoteConnectionRow
from msys.models.table_models import RemoteConnectionTableModel, RemoteConnectionFilterProxy
from msys.widgets.delegates import _setup_header
from msys.collectors import RemoteConnectionCollector
from msys.ps_scripts import PS_CHECK_REMOTE_AUDIT_STATUS

# GUID de la subcategoria de auditoria "Inicio de sesion" (Logon) — estable
# e independiente del idioma de Windows, a diferencia del nombre localizado
# ("Inicio de sesión" en español, "Logon" en inglés, etc.).
_AUDITPOL_LOGON_SUBCATEGORY_GUID = "{0CCE9215-69AE-11D9-BED3-505054503030}"
_RDP_OPERATIONAL_LOG_CHANNEL = "Microsoft-Windows-TerminalServices-RemoteConnectionManager/Operational"


class ConexionesRemotasMixin:
    def crear_tab_conexiones_remotas(self):
        """Crea la pestaña de conexiones remotas al equipo."""
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

        self.search_remote = QLineEdit()
        self.search_remote.setPlaceholderText("🔎 Buscar por usuario, IP, tipo...")
        self.search_remote.textChanged.connect(lambda txt: (
            self.remote_proxy.setFilterRegExp(QRegExp(txt, Qt.CaseInsensitive, QRegExp.FixedString)),
            self._update_remote_count()
        ))
        actions_layout.addWidget(self.search_remote, 3)

        actions_layout.addStretch()

        self.btn_activar_auditoria_remota = QPushButton("🔐 Activar Auditoría")
        self.btn_activar_auditoria_remota.setObjectName("ghost")
        self.btn_activar_auditoria_remota.clicked.connect(self._toggle_auditoria_conexiones_remotas)
        actions_layout.addWidget(self.btn_activar_auditoria_remota)
        self._auditoria_remota_activa = False
        self._actualizar_boton_auditoria_remota(consultar_estado_real=True)

        self.btn_refresh_remote = QPushButton("🔄 Actualizar")
        self.btn_refresh_remote.setObjectName("primary")
        self.btn_refresh_remote.setToolTip(
            "Accesos remotos al equipo (últimos 30 días) — requiere privilegios de administrador")
        self.btn_refresh_remote.clicked.connect(self.refresh_remote_connections)
        actions_layout.addWidget(self.btn_refresh_remote)

        layout.addLayout(actions_layout)

        # Filtros: Resultado / Tipo, debajo de la búsqueda
        filters_row1 = QHBoxLayout()
        filters_row1.setSpacing(20)

        lbl_resultado = QLabel("Resultado:")
        lbl_resultado.setObjectName("fieldLabel")
        filters_row1.addWidget(lbl_resultado)

        self.chk_remote_exito = QCheckBox("✅ Éxito")
        self.chk_remote_exito.setChecked(True)
        self.chk_remote_exito.stateChanged.connect(self._apply_remote_filter)
        filters_row1.addWidget(self.chk_remote_exito)

        self.chk_remote_fallo = QCheckBox("❌ Fallo")
        self.chk_remote_fallo.setChecked(True)
        self.chk_remote_fallo.stateChanged.connect(self._apply_remote_filter)
        filters_row1.addWidget(self.chk_remote_fallo)

        filters_row1.addSpacing(30)

        lbl_tipo = QLabel("Tipo:")
        lbl_tipo.setObjectName("fieldLabel")
        filters_row1.addWidget(lbl_tipo)

        self.chk_remote_rdp = QCheckBox("🖥️ RDP")
        self.chk_remote_rdp.setChecked(True)
        self.chk_remote_rdp.stateChanged.connect(self._apply_remote_filter)
        filters_row1.addWidget(self.chk_remote_rdp)

        self.chk_remote_smb = QCheckBox("📁 Red/SMB")
        self.chk_remote_smb.setChecked(True)
        self.chk_remote_smb.stateChanged.connect(self._apply_remote_filter)
        filters_row1.addWidget(self.chk_remote_smb)

        filters_row1.addStretch()

        self.lbl_remote_count = QLabel("0/0 eventos")
        self.lbl_remote_count.setObjectName("countLabel")
        filters_row1.addWidget(self.lbl_remote_count)

        layout.addLayout(filters_row1)

        # Tabla
        self.remote_model = RemoteConnectionTableModel([])
        self.remote_proxy = RemoteConnectionFilterProxy(self)
        self.remote_proxy.setSourceModel(self.remote_model)
        self.remote_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.remote_proxy.setFilterKeyColumn(-1)

        self.remote_table = QTableView()
        self.remote_table.setModel(self.remote_proxy)
        self.remote_table.setAlternatingRowColors(True)
        self.remote_table.setSelectionBehavior(QTableView.SelectRows)
        self.remote_table.verticalHeader().setVisible(False)
        self.remote_table.setSortingEnabled(True)
        self.remote_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.remote_table.customContextMenuRequested.connect(self._ctx_remoto)

        _setup_header(self.remote_table, 8, stretch_col=6)

        layout.addWidget(self.remote_table)

        return tab

    def refresh_remote_connections(self):
        """Actualiza lista de conexiones remotas al equipo."""
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.set_status("🔄 Consultando registro de seguridad...")
        self.btn_refresh_remote.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.worker = RemoteConnectionCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_remote_data)
        self.worker.finished_error.connect(self._on_remote_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_remote_data(self, rows: List[RemoteConnectionRow]):
        self.remote_model.setRows(rows)
        self.btn_refresh_remote.setEnabled(True)
        self.progress_bar.setVisible(False)
        self._apply_remote_filter()
        self.set_status(f"✅ {len(rows)} eventos de conexión remota encontrados")

    def _on_remote_error(self, msg: str):
        self.btn_refresh_remote.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al consultar conexiones remotas")
        self._avisar("Error",
            f"No se pudieron obtener las conexiones remotas.\n"
            f"Asegúrate de ejecutar como administrador.\n\n{msg}", QMessageBox.Warning)

    def _evento_remoto_seleccionado(self):
        sel = self.remote_table.selectionModel()
        if not sel or not sel.hasSelection():
            return None
        idx = sel.selectedRows()[0]
        fuente_idx = self.remote_proxy.mapToSource(idx)
        return self.remote_model.rows()[fuente_idx.row()]

    def _ctx_remoto(self, pos):
        evento = self._evento_remoto_seleccionado()
        if not evento:
            return

        menu = QMenu(self)
        act_copiar = menu.addAction("📋 Copiar fila")
        act_detalle = menu.addAction("🔍 Ver detalle completo")
        act_bloquear = None
        ip = (evento.ip_origen or "").strip()
        if ip and ip not in ("Local", "N/A"):
            menu.addSeparator()
            act_bloquear = menu.addAction(f"🚫 Bloquear IP {ip} en el Firewall")

        action = menu.exec_(self.remote_table.viewport().mapToGlobal(pos))
        if action == act_copiar:
            self._copiar_seleccion_generica(self.remote_table, self.remote_proxy, RemoteConnectionTableModel.HEADERS)
        elif action == act_detalle:
            self._ver_detalle_evento_remoto(evento)
        elif act_bloquear is not None and action == act_bloquear:
            self._bloquear_ip_origen_remota(ip)

    def _ver_detalle_evento_remoto(self, evento: RemoteConnectionRow):
        """Ventana informativa con todos los campos del evento — mismo
        estándar visual que el resto de ventanas de información
        (_mostrar_informe), útil cuando Usuario/Workstation/Proceso se
        cortan en la tabla."""
        items = [
            f"Fecha: {evento.fecha}",
            f"Usuario: {evento.usuario}",
            f"IP Origen: {evento.ip_origen}",
            f"Tipo Acceso: {evento.tipo_acceso}",
            f"Resultado: {evento.resultado}",
            f"Workstation: {evento.workstation}",
            f"Proceso: {evento.proceso}",
            f"ID Evento: {evento.id_evento}",
        ]
        self._mostrar_informe(f"Evento de conexión remota — {evento.tipo_acceso}", [], items)

    def _bloquear_ip_origen_remota(self, ip: str):
        """Bloquea una IP de origen sospechosa (p. ej. varios intentos
        fallidos) directamente desde el evento — misma lógica y misma
        convención de nombre de regla ('MASys - Bloqueo IP ...') que
        'Bloquear IP en el Firewall' de Conexiones Red."""
        if not is_admin():
            self._avisar(
                "Bloquear IP",
                "Se requieren privilegios de administrador para crear reglas de firewall.\n\n"
                "Reinicia MASys como administrador.", QMessageBox.Warning)
            return

        # Mismo aviso reforzado que Conexiones Red: una IP privada podría ser
        # otro equipo interno legítimo, no un atacante externo real.
        es_privada = False
        try:
            es_privada = ipaddress.ip_address(ip).is_private
        except ValueError:
            pass

        explicacion = (
            f"Las reglas se crean como 'MASys - Bloqueo IP {ip} (Entrada/Salida)' "
            "y se pueden gestionar (desactivar/eliminar) después desde la pestaña 🔥 Firewall."
        )
        if es_privada:
            explicacion = (
                f"⚠️ {ip} es una IP PRIVADA de tu propia red local — podrías estar bloqueando "
                "un equipo interno legítimo (otro PC, un servidor...) o incluso el tuyo propio "
                "si accedes en remoto. Confirma que sabes qué es antes de continuar.\n\n" + explicacion
            )

        if not self._confirmar_reparacion(
            "Bloquear IP en el Firewall",
            cambios=[
                f"Crear una regla de firewall de ENTRADA bloqueando {ip}",
                f"Crear una regla de firewall de SALIDA bloqueando {ip}",
            ],
            reversible=True,
            objetivo=ip,
            explicacion=explicacion,
            es_alerta=es_privada,
        ):
            return

        ok_in, msg_in = run_powershell_command_params(
            "New-NetFirewallRule -DisplayName 'MASys - Bloqueo IP {ip} (Entrada)' "
            "-Direction Inbound -RemoteAddress '{ip}' -Action Block -Profile Any | Out-Null",
            ip=ip,
        )
        ok_out, msg_out = run_powershell_command_params(
            "New-NetFirewallRule -DisplayName 'MASys - Bloqueo IP {ip} (Salida)' "
            "-Direction Outbound -RemoteAddress '{ip}' -Action Block -Profile Any | Out-Null",
            ip=ip,
        )
        ok = ok_in and ok_out
        detalle = f"Entrada: {'OK' if ok_in else msg_in}; Salida: {'OK' if ok_out else msg_out}"

        if hasattr(self, 'repair_audit_db'):
            self.repair_audit_db.registrar(
                seccion="🔒 Conexiones Remotas", accion="Bloquear IP en el Firewall",
                objetivo=ip, exito=ok, detalle=detalle,
            )
        if ok:
            self.set_status(f"✅ IP {ip} bloqueada en el Firewall (entrada y salida)")
            self._avisar("Bloquear IP", f"✅ IP {ip} bloqueada en el Firewall (entrada y salida).")
        else:
            self._avisar("Bloquear IP", f"Hubo un problema al crear alguna regla:\n\n{detalle}",
                         QMessageBox.Warning)

    def _apply_remote_filter(self):
        show_exito = self.chk_remote_exito.isChecked()
        show_fallo = self.chk_remote_fallo.isChecked()
        types = set()
        if self.chk_remote_rdp.isChecked():
            types.add("rdp")
        if self.chk_remote_smb.isChecked():
            types.add("smb")
            types.add("red")
        # Si ambos están activos = sin filtro de tipo
        if self.chk_remote_rdp.isChecked() and self.chk_remote_smb.isChecked():
            types = set()
        self.remote_proxy.setFilters(show_exito, show_fallo, types)
        self._update_remote_count()

    def _update_remote_count(self):
        total = self.remote_model.rowCount()
        visible = self.remote_proxy.rowCount()
        self.lbl_remote_count.setText(f"{visible}/{total} eventos")

    def _actualizar_boton_auditoria_remota(self, consultar_estado_real: bool = False):
        """Sincroniza texto/tooltip del botón con self._auditoria_remota_activa.
        Si consultar_estado_real=True, primero consulta el estado real en
        Windows (auditpol + wevtutil) en vez de fiarse del último toggle
        hecho desde la propia app — best-effort: la detección de auditpol
        solo reconoce es/en, así que en otros idiomas puede dar un falso
        'inactiva' (nunca falso 'activa'), y el botón por defecto ofrece
        activarla igualmente."""
        if consultar_estado_real:
            estado = run_powershell_json(PS_CHECK_REMOTE_AUDIT_STATUS, timeout=15)
            self._auditoria_remota_activa = bool(
                estado and estado.get("LogonOk") and estado.get("RdpOk")
            )

        if self._auditoria_remota_activa:
            self.btn_activar_auditoria_remota.setText("🔓 Desactivar Auditoría")
            self.btn_activar_auditoria_remota.setToolTip(
                "Deshabilita el registro de eventos de inicio de sesión y el canal "
                "RDP que usa esta pestaña para mostrar accesos remotos.\n"
                "Requiere privilegios de administrador.")
        else:
            self.btn_activar_auditoria_remota.setText("🔐 Activar Auditoría")
            self.btn_activar_auditoria_remota.setToolTip(
                "Habilita el registro de eventos de inicio de sesión y del canal RDP "
                "necesarios para que esta pestaña pueda mostrar datos.\n"
                "Requiere privilegios de administrador — no borra ni cambia nada más.")

    def _toggle_auditoria_conexiones_remotas(self):
        """Activa o desactiva, según el estado actual del botón, las dos
        fuentes de eventos que usa esta pestaña: auditoría de "Inicio de
        sesión" (Security, Event ID 4624/4625) y el canal operativo de RDP
        (TerminalServices-RemoteConnectionManager, Event ID 1149) — ninguno
        de los dos está activo por defecto en Windows. Es un cambio de
        configuración del sistema, así que se pide confirmación explícita
        en ambos sentidos."""
        if not is_admin():
            self._avisar(
                "Auditoría de conexiones remotas",
                "Se requieren permisos de administrador para cambiar esta configuración.\n\n"
                "Reinicia MASys como administrador.", QMessageBox.Warning)
            return

        activando = not self._auditoria_remota_activa

        if activando:
            titulo = "Activar auditoría de conexiones remotas"
            texto = (
                "Esto habilita en Windows el registro de eventos de inicio de sesión "
                "y del canal de eventos RDP, necesarios para que esta pestaña pueda "
                "mostrar accesos remotos.\n\n"
                "No borra ni modifica nada más, y puedes desactivarlo después con "
                "este mismo botón.\n\n"
                "¿Continuar? (requiere privilegios de administrador)"
            )
            accion_ps = (
                f'auditpol /set /subcategory:"{_AUDITPOL_LOGON_SUBCATEGORY_GUID}" '
                f'/success:enable /failure:enable\n'
                f'if ($LASTEXITCODE -ne 0) {{ throw "auditpol devolvió el código $LASTEXITCODE" }}\n'
                f'wevtutil sl "{_RDP_OPERATIONAL_LOG_CHANNEL}" /e:true\n'
                f'if ($LASTEXITCODE -ne 0) {{ throw "wevtutil devolvió el código $LASTEXITCODE" }}\n'
            )
        else:
            titulo = "Desactivar auditoría de conexiones remotas"
            texto = (
                "Esto deshabilita en Windows el registro de eventos de inicio de "
                "sesión y del canal de eventos RDP que usa esta pestaña.\n\n"
                "Los eventos ya registrados no se borran, pero a partir de ahora no "
                "se registrarán accesos nuevos.\n\n"
                "⚠️ La auditoría de «Inicio de sesión» suele venir ACTIVADA de serie en Windows y la usan "
                "también el Visor de eventos y otras herramientas de seguridad: desactivarla deja el equipo "
                "sin registro de accesos (ni locales ni remotos). Normalmente es mejor dejarla activa.\n\n"
                "¿Continuar? (requiere privilegios de administrador)"
            )
            accion_ps = (
                f'auditpol /set /subcategory:"{_AUDITPOL_LOGON_SUBCATEGORY_GUID}" '
                f'/success:disable /failure:disable\n'
                f'if ($LASTEXITCODE -ne 0) {{ throw "auditpol devolvió el código $LASTEXITCODE" }}\n'
                f'wevtutil sl "{_RDP_OPERATIONAL_LOG_CHANNEL}" /e:false\n'
                f'if ($LASTEXITCODE -ne 0) {{ throw "wevtutil devolvió el código $LASTEXITCODE" }}\n'
            )

        if not self._confirmar_accion(titulo, texto):
            return

        ok, error_msg = run_powershell_command(accion_ps, timeout=20)
        self._registrar_historial("🔒 Conexiones Remotas", "Activar auditoría" if activando else "Desactivar auditoría",
                                  "Inicio de sesión + canal RDP", ok, error_msg)
        if ok:
            self._auditoria_remota_activa = activando
            self._actualizar_boton_auditoria_remota()
            if activando:
                self._avisar(
                    "Auditoría activada",
                    "Auditoría de inicio de sesión y canal RDP activados correctamente.\n\n"
                    "Los nuevos accesos remotos empezarán a registrarse a partir de ahora "
                    "— los accesos anteriores a este cambio no se pueden recuperar "
                    "retroactivamente."
                )
                self.refresh_remote_connections()
            else:
                self._avisar("Auditoría desactivada",
                              "Auditoría de inicio de sesión y canal RDP desactivados correctamente.")
        else:
            accion_txt = "activar" if activando else "desactivar"
            self._avisar(
                f"No se pudo {accion_txt} la auditoría",
                "No se pudo aplicar el cambio. Asegúrate de ejecutar MASys "
                f"como administrador.\n\n{error_msg}", QMessageBox.Warning,
            )

