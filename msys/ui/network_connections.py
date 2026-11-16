# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña Conexiones Red: tabla, filtros, menu contextual, resolucion de hostname.
Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).

Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

import os
import subprocess
import ipaddress
from typing import List

from PyQt5.QtCore import Qt, QRegExp
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QCheckBox, QPushButton, QMenu, QMessageBox, QTableView,
)

from msys.models.rows import NetworkConnectionRow, _CRITICAL_PROCESSES
from msys.models.table_models import NetworkConnectionTableModel, NetworkConnectionFilterProxy
from msys.widgets.delegates import _setup_header
from msys.collectors import NetworkConnectionCollector, _HostnameResolverThread
from msys.core.utils import is_admin, run_powershell_command_params


class ConexionesRedMixin:
    def crear_tab_network_connections(self):
        """Crea la pestaña de conexiones de red."""
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

        self.search_connections = QLineEdit()
        self.search_connections.setPlaceholderText("🔎 Buscar conexiones...")
        self.search_connections.textChanged.connect(lambda: (
            self.connections_proxy.setFilterRegExp(QRegExp(self.search_connections.text(), Qt.CaseInsensitive, QRegExp.FixedString)),
            self._update_connections_counter()
        ))
        actions_layout.addWidget(self.search_connections, 3)

        actions_layout.addStretch()

        self.btn_refresh_connections = QPushButton("🔄 Actualizar")
        self.btn_refresh_connections.setObjectName("primary")
        self.btn_refresh_connections.setToolTip("Conexiones de red activas en el sistema")
        self.btn_refresh_connections.clicked.connect(self.refresh_network_connections)
        actions_layout.addWidget(self.btn_refresh_connections)

        layout.addLayout(actions_layout)

        # Filtros: protocolo, procesos de sistema, sospechosas
        filters_layout = QHBoxLayout()
        filters_layout.setSpacing(15)

        lbl_protocolo = QLabel("Protocolo:")
        lbl_protocolo.setObjectName("fieldLabel")
        filters_layout.addWidget(lbl_protocolo)

        self.chk_conn_tcp = QCheckBox("TCP")
        self.chk_conn_tcp.setChecked(True)
        self.chk_conn_tcp.stateChanged.connect(self._apply_connections_filters)
        filters_layout.addWidget(self.chk_conn_tcp)

        self.chk_conn_udp = QCheckBox("UDP")
        self.chk_conn_udp.setChecked(True)
        self.chk_conn_udp.stateChanged.connect(self._apply_connections_filters)
        filters_layout.addWidget(self.chk_conn_udp)

        filters_layout.addSpacing(30)

        self.chk_conn_sistema = QCheckBox("⚙️ Conexiones de sistema")
        self.chk_conn_sistema.setChecked(True)
        self.chk_conn_sistema.setToolTip(
            "Procesos del propio Windows: svchost, System, lsass, csrss, "
            "services, spoolsv... Desmarca para ver solo aplicaciones de usuario.")
        self.chk_conn_sistema.stateChanged.connect(self._apply_connections_filters)
        filters_layout.addWidget(self.chk_conn_sistema)

        self.chk_conn_sospechosas = QCheckBox("⚠️ Solo sospechosas (proceso no identificado o disfrazado)")
        self.chk_conn_sospechosas.setToolTip(
            "Proceso no identificado (ya terminó) o con nombre de proceso crítico del "
            "sistema (svchost.exe, csrss.exe...) corriendo fuera de su carpeta real")
        self.chk_conn_sospechosas.setChecked(False)
        self.chk_conn_sospechosas.stateChanged.connect(self._apply_connections_filters)
        filters_layout.addWidget(self.chk_conn_sospechosas)

        self.chk_conn_solo_publicas = QCheckBox("🌐 Solo conexiones a Internet (IP pública)")
        self.chk_conn_solo_publicas.setToolTip(
            "Oculta conexiones a IPs privadas (LAN), loopback y de enlace local")
        self.chk_conn_solo_publicas.setChecked(False)
        self.chk_conn_solo_publicas.stateChanged.connect(self._apply_connections_filters)
        filters_layout.addWidget(self.chk_conn_solo_publicas)

        filters_layout.addStretch()

        self.lbl_connections_count = QLabel("0/0 conexiones")
        self.lbl_connections_count.setObjectName("countLabel")
        filters_layout.addWidget(self.lbl_connections_count)

        layout.addLayout(filters_layout)

        # Tabla
        self.connections_model = NetworkConnectionTableModel([])
        self.connections_proxy = NetworkConnectionFilterProxy(self)
        self.connections_proxy.setSourceModel(self.connections_model)
        self.connections_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.connections_proxy.setFilterKeyColumn(-1)

        self.connections_table = QTableView()
        self.connections_table.setModel(self.connections_proxy)
        self.connections_table.setAlternatingRowColors(True)
        self.connections_table.setSelectionBehavior(QTableView.SelectRows)
        self.connections_table.verticalHeader().setVisible(False)
        self.connections_table.setSortingEnabled(True)
        self.connections_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.connections_table.customContextMenuRequested.connect(self._ctx_conexiones)

        _setup_header(self.connections_table, 6, stretch_col=2)

        layout.addWidget(self.connections_table)

        return tab

    def refresh_network_connections(self):
        """Actualiza lista de conexiones de red."""
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.set_status("🔄 Escaneando conexiones de red...")
        self.btn_refresh_connections.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.worker = NetworkConnectionCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_connections_data)
        self.worker.finished_error.connect(self._on_connections_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_connections_data(self, rows: List[NetworkConnectionRow]):
        """Procesa datos de conexiones de red."""
        self.connections_model.setRows(rows)
        self.btn_refresh_connections.setEnabled(True)
        self.progress_bar.setVisible(False)
        total = len(rows)
        self.set_status(f"✅ {total} conexiones encontradas")
        self._update_connections_counter()

    def _on_connections_error(self, msg: str):
        """Maneja errores de conexiones de red."""
        self.btn_refresh_connections.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al escanear conexiones de red")
        self._avisar("Error", f"No se pudieron obtener las conexiones:\n\n{msg}", QMessageBox.Warning)

    def _apply_connections_filters(self):
        self.connections_proxy.set_filtros(
            self.chk_conn_tcp.isChecked(),
            self.chk_conn_udp.isChecked(),
            self.chk_conn_sistema.isChecked(),
            self.chk_conn_sospechosas.isChecked(),
            self.chk_conn_solo_publicas.isChecked(),
        )
        self._update_connections_counter()

    def _update_connections_counter(self):
        total = self.connections_model.rowCount()
        visible = self.connections_proxy.rowCount()
        self.lbl_connections_count.setText(f"{visible}/{total} conexiones")

    def _ctx_conexiones(self, pos):
        idx = self.connections_table.indexAt(pos)
        if not idx.isValid():
            return
        fuente = self.connections_proxy.mapToSource(idx)
        row = self.connections_model.rows()[fuente.row()]

        menu = QMenu(self)
        act_resolve = menu.addAction("🔍 Resolver hostname de IP remota")
        act_copy = menu.addAction("📋 Copiar fila")
        menu.addSeparator()
        act_abrir_ubicacion = menu.addAction("📂 Abrir ubicación del ejecutable")
        act_terminar = menu.addAction("🛑 Terminar proceso")
        act_bloquear_ip = menu.addAction("🚫 Bloquear IP remota en el Firewall")

        action = menu.exec_(self.connections_table.viewport().mapToGlobal(pos))
        if action == act_resolve:
            # rsplit por el ÚLTIMO ':' (no split por el primero): una IPv6
            # tiene varios ':' propios — "2001:db8::1:443".split(':')[0]
            # daría "2001", no la IP real. Bug real corregido de paso.
            ip = row.direccion_remota.rsplit(":", 1)[0].strip("[]")
            if not ip or ip.lower() in ("-", "n/a", "0.0.0.0", "::"):
                self._avisar("Resolver", "IP no disponible.")
                return
            self.set_status(f"🔄 Resolviendo {ip}...")
            # En un hilo aparte: si el DNS es lento o la IP no tiene PTR,
            # gethostbyaddr puede tardar varios segundos y congelaba la
            # ventana al llamarse directamente aquí.
            self._resolver_thread = _HostnameResolverThread(ip)
            self._resolver_thread.resuelto.connect(self._on_hostname_resuelto)
            self._resolver_thread.start()
        elif action == act_copy:
            cols = [row.protocolo, row.direccion_local, row.direccion_remota,
                    row.estado, row.proceso, row.pid]
            QApplication.clipboard().setText("\t".join(str(v or "") for v in cols))
        elif action == act_abrir_ubicacion:
            self._abrir_ubicacion_ejecutable(row)
        elif action == act_terminar:
            self._terminar_proceso_conexion(row)
        elif action == act_bloquear_ip:
            self._bloquear_ip_remota(row)

    def _abrir_ubicacion_ejecutable(self, row: NetworkConnectionRow):
        if not row.exe_path or not os.path.isfile(row.exe_path):
            self._avisar("Abrir ubicación", "No se conoce la ruta del ejecutable (o ya no existe en disco).")
            return
        subprocess.Popen(["explorer", "/select,", row.exe_path])

    def _terminar_proceso_conexion(self, row: NetworkConnectionRow):
        try:
            pid = int(row.pid)
        except (ValueError, TypeError):
            self._avisar("Terminar proceso", "PID inválido.", QMessageBox.Warning)
            return

        texto = f"¿Finalizar el proceso «{row.proceso}» (PID {pid})?"
        if row.name.lower() in _CRITICAL_PROCESSES:
            texto += ("\n\n⚠ Este es un proceso crítico del sistema. Finalizarlo puede "
                      "provocar un bloqueo, cierre de sesión o reinicio inesperado.")
        if not self._confirmar_accion("Finalizar proceso", texto):
            return

        ok, msg = self.process_manager.kill_process(pid)
        self.repair_audit_db.registrar(
            seccion="🌐 Conexiones Red", accion="Finalizar proceso",
            objetivo=f"{row.proceso} (PID {pid})", exito=ok, detalle=msg,
        )
        if ok:
            self.set_status(f"✅ Proceso {row.proceso} (PID {pid}) finalizado")
            self.refresh_network_connections()
        else:
            self._avisar("Error", f"No se pudo finalizar el proceso.\n\n{msg}", QMessageBox.Warning)

    def _bloquear_ip_remota(self, row: NetworkConnectionRow):
        ip = row.direccion_remota.rsplit(":", 1)[0].strip("[]")
        if not ip or ip.lower() in ("-", "n/a", "0.0.0.0", "::"):
            self._avisar("Bloquear IP", "No hay una IP remota real en esta fila.")
            return

        if not is_admin():
            self._avisar(
                "Bloquear IP",
                "Se requieren privilegios de administrador para crear reglas de firewall.\n\n"
                "Reinicia MASys como administrador.",
                QMessageBox.Warning,
            )
            return

        # Bloquear una IP PRIVADA (de la propia red local) es un riesgo real
        # distinto de bloquear una IP pública desconocida: podría ser otro
        # equipo interno legítimo, un servidor de la oficina, o incluso el
        # propio puesto del administrador si accede en remoto — sin este
        # aviso, un clic apresurado en la fila equivocada podía dejar sin
        # acceso interno a algo importante sin ninguna señal previa de alerta.
        es_privada = False
        try:
            es_privada = ipaddress.ip_address(ip).is_private
        except ValueError:
            pass

        explicacion = (
            f"Las reglas se crean como 'MASys - Bloqueo IP {ip} (Entrada/Salida)' "
            "y se pueden gestionar (desactivar/eliminar) después desde la pestaña 🛡️ Firewall."
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

        self.repair_audit_db.registrar(
            seccion="🌐 Conexiones Red", accion="Bloquear IP en el Firewall",
            objetivo=ip, exito=ok, detalle=detalle,
        )
        if ok:
            self.set_status(f"✅ IP {ip} bloqueada en el Firewall (entrada y salida)")
        else:
            self._avisar("Bloquear IP", f"Hubo un problema al crear alguna regla:\n\n{detalle}", QMessageBox.Warning)

    def _on_hostname_resuelto(self, ip: str, hostname: str, error: str):
        if hostname:
            self._avisar(f"Hostname — {ip}", f"IP: {ip}\nHostname: {hostname}")
            QApplication.clipboard().setText(hostname)
            self.set_status(f"✅ {ip} → {hostname}")
        else:
            self._avisar(f"Hostname — {ip}", f"IP: {ip}\nNo se pudo resolver: {error}")
            self.set_status(f"⚠️ No se pudo resolver {ip}")

