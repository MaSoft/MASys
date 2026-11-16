# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña Red Local: tabla ARP, filtros, refresco.
Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).

Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

import socket as _socket
import ipaddress

from PyQt5.QtCore import Qt, QRegExp
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QCheckBox,
    QPushButton, QMessageBox, QTableView, QMenu, QApplication,
)

from msys.models.table_models import NetworkDeviceTableModel, NetworkDeviceFilterProxy
from msys.widgets.delegates import _setup_header
from msys.collectors import (
    NetworkDeviceCollector, DeviceEnrichmentThread,
    _resolver_nombre_dns_o_netbios, _comprobar_acceso_web_ip,
)
from msys.core.utils import run_powershell_json
from msys.ps_scripts import PS_PING_HOST


class RedLocalMixin:
    def crear_tab_red_local(self):
        """Crea la pestaña de Red Local (dispositivos conocidos en la LAN)."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        # Barra de búsqueda y acciones — esta sección no tenía buscador
        # (inconsistente con el resto de la app, que siempre lo tiene junto
        # al botón principal); se añade aquí.
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)

        import socket
        lbl_hostname = QLabel(f"🖥️ {socket.gethostname()}")
        lbl_hostname.setObjectName("hostnameLabel")
        actions_layout.addWidget(lbl_hostname)

        self.search_red_local_rango = QLineEdit()
        self.search_red_local_rango.setPlaceholderText("Rango IP: 192.168.1.10-50")
        self.search_red_local_rango.setToolTip(
            "Filtra por rango de IP. Admite dos formatos:\n"
            "  192.168.1.10-50  (mismo prefijo, solo cambia el último octeto)\n"
            "  192.168.1.10-192.168.1.150  (dos IPs completas)")
        self.search_red_local_rango.setMaximumWidth(220)
        self.search_red_local_rango.textChanged.connect(self._apply_red_local_rango_ip)
        actions_layout.addWidget(self.search_red_local_rango)

        self.search_red_local = QLineEdit()
        self.search_red_local.setPlaceholderText("🔎 Buscar por IP, MAC o interfaz...")
        self.search_red_local.textChanged.connect(lambda: (
            self.red_local_proxy.setFilterRegExp(
                QRegExp(self.search_red_local.text(), Qt.CaseInsensitive, QRegExp.FixedString)),
            self._update_red_local_counter()
        ))
        actions_layout.addWidget(self.search_red_local, 3)

        actions_layout.addStretch()

        self.btn_refresh_red_local = QPushButton("🔄 Buscar Dispositivos")
        self.btn_refresh_red_local.setObjectName("primary")
        self.btn_refresh_red_local.setToolTip(
            "Dispositivos conocidos en la red local (tabla de vecinos ARP/IPv4, no intrusivo)")
        self.btn_refresh_red_local.clicked.connect(self.refresh_red_local)
        actions_layout.addWidget(self.btn_refresh_red_local)

        layout.addLayout(actions_layout)

        # Filtros por estado (Reachable/Stale/Permanent/Otros)
        filters_layout = QHBoxLayout()
        filters_layout.setSpacing(15)

        lbl_estado = QLabel("Estado:")
        lbl_estado.setObjectName("fieldLabel")
        filters_layout.addWidget(lbl_estado)

        self.chk_red_local_reachable = QCheckBox("✅ Alcanzable")
        self.chk_red_local_reachable.setToolTip("Comunicación confirmada recientemente")
        self.chk_red_local_reachable.setChecked(True)
        self.chk_red_local_reachable.stateChanged.connect(self._apply_red_local_filters)
        filters_layout.addWidget(self.chk_red_local_reachable)

        self.chk_red_local_stale = QCheckBox("🕒 Obsoleto")
        self.chk_red_local_stale.setToolTip("Entrada antigua en la caché ARP, sin confirmar recientemente")
        self.chk_red_local_stale.setChecked(True)
        self.chk_red_local_stale.stateChanged.connect(self._apply_red_local_filters)
        filters_layout.addWidget(self.chk_red_local_stale)

        self.chk_red_local_permanent = QCheckBox("📌 Permanente")
        self.chk_red_local_permanent.setToolTip("Entrada estática, configurada manualmente")
        self.chk_red_local_permanent.setChecked(True)
        self.chk_red_local_permanent.stateChanged.connect(self._apply_red_local_filters)
        filters_layout.addWidget(self.chk_red_local_permanent)

        self.chk_red_local_otros = QCheckBox("❔ Otros")
        self.chk_red_local_otros.setChecked(True)
        self.chk_red_local_otros.stateChanged.connect(self._apply_red_local_filters)
        filters_layout.addWidget(self.chk_red_local_otros)

        filters_layout.addStretch()

        self.lbl_red_local_count = QLabel("0/0 dispositivos")
        self.lbl_red_local_count.setObjectName("countLabel")
        filters_layout.addWidget(self.lbl_red_local_count)

        layout.addLayout(filters_layout)

        self.red_local_model = NetworkDeviceTableModel([])
        self.red_local_proxy = NetworkDeviceFilterProxy(self)
        self.red_local_proxy.setSourceModel(self.red_local_model)
        self.red_local_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.red_local_proxy.setFilterKeyColumn(-1)

        self.red_local_table = QTableView()
        self.red_local_table.setModel(self.red_local_proxy)
        self.red_local_table.setAlternatingRowColors(True)
        self.red_local_table.setSelectionBehavior(QTableView.SelectRows)
        self.red_local_table.verticalHeader().setVisible(False)
        self.red_local_table.setSortingEnabled(True)
        self.red_local_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.red_local_table.customContextMenuRequested.connect(self._ctx_red_local)
        _setup_header(self.red_local_table, len(NetworkDeviceTableModel.HEADERS), stretch_col=6)
        layout.addWidget(self.red_local_table)

        # Aviso explícito cuando el escaneo no encuentra NADA — antes la
        # tabla se quedaba simplemente vacía, sin ninguna pista de si es
        # normal (red muy pequeña/aislada) o si algo ha fallado (firewall
        # bloqueando el barrido de ping, sin permisos...).
        self.lbl_red_local_vacio = QLabel(
            "⚠️ No se ha encontrado ningún dispositivo. Esto puede deberse a que el Firewall "
            "de Windows bloquea el barrido de ping saliente, a que la red no permite tráfico "
            "entre equipos (redes de invitados/corporativas), o a que MASys no se ejecuta "
            "como administrador. Prueba a reiniciar como administrador y a pulsar Actualizar de nuevo."
        )
        self.lbl_red_local_vacio.setWordWrap(True)
        self.lbl_red_local_vacio.setStyleSheet("color:#ca8a04; font-size:9.5pt; padding:10px;")
        self.lbl_red_local_vacio.setVisible(False)
        layout.addWidget(self.lbl_red_local_vacio)

        return tab

    def refresh_red_local(self):
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return
        if getattr(self, '_enriquecer_thread', None) is not None and self._enriquecer_thread.isRunning():
            self.set_status("⏳ Todavía se está identificando nombre/acceso web del escaneo anterior, espera a que termine...")
            return

        self.set_status("🔄 Consultando dispositivos de la red local...")
        self.btn_refresh_red_local.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.worker = NetworkDeviceCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_red_local_data)
        self.worker.finished_error.connect(self._on_red_local_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_red_local_data(self, rows):
        if hasattr(self, 'dispositivo_sospechoso_db'):
            for row in rows:
                row.sospechoso = self.dispositivo_sospechoso_db.is_marked(row.mac)
        self.red_local_model.setRows(rows)
        self.btn_refresh_red_local.setEnabled(True)
        self.set_status(f"✅ {len(rows)} dispositivo(s) encontrados en la red local")
        self.lbl_red_local_vacio.setVisible(len(rows) == 0)
        self._update_red_local_counter()

        # Nombre de host y acceso web se rellenan solos tras cada escaneo
        # (antes había que comprobarlos uno a uno a mano desde el menú
        # contextual) — en paralelo, pero el peor caso real (NetBIOS
        # consultando todos los adaptadores) puede tardar ~10-16s, así que
        # se mantiene la barra de progreso visible en vez de ocultarla y
        # dar la sensación de que la app se ha quedado parada.
        # '_red_local_generacion' evita que un hilo de enriquecimiento de
        # un escaneo ANTERIOR (si se pulsa Actualizar de nuevo a medias,
        # aunque el guard de arriba ya lo impide) llegue a pisar filas de
        # un escaneo más reciente.
        self._red_local_generacion = getattr(self, '_red_local_generacion', 0) + 1
        generacion = self._red_local_generacion
        ips = [r.ip for r in rows]
        if ips:
            self.set_status(f"🔄 {len(rows)} dispositivo(s) — identificando nombre/acceso web...")
            self.progress_bar.setValue(0)
            self._enriquecer_thread = DeviceEnrichmentThread(ips)
            self._enriquecer_thread.dispositivo_enriquecido.connect(
                lambda ip, hostname, acceso_web, gen=generacion:
                    self._on_dispositivo_enriquecido(ip, hostname, acceso_web, gen))
            self._enriquecer_thread.progress.connect(
                lambda pct, texto, gen=generacion: self._on_enriquecimiento_progreso(pct, texto, gen))
            self._enriquecer_thread.start()
        else:
            self.progress_bar.setVisible(False)

    def _on_dispositivo_enriquecido(self, ip: str, hostname: str, acceso_web: str, gen: int):
        if gen != getattr(self, '_red_local_generacion', None):
            return  # resultado de un escaneo anterior ya descartado
        cambios = {}
        if hostname:
            cambios['hostname'] = hostname
        if acceso_web:
            cambios['acceso_web'] = acceso_web
        if cambios:
            self._actualizar_fila_red_local(ip, **cambios)

    def _on_enriquecimiento_progreso(self, pct: int, texto: str, gen: int):
        if gen != getattr(self, '_red_local_generacion', None):
            return
        self.progress_bar.setValue(pct)
        if pct >= 100:
            self.progress_bar.setVisible(False)
            self.set_status(f"✅ {self.red_local_model.rowCount()} dispositivo(s) — información completa")
        else:
            self.set_status(f"🔄 {texto}")

    def _on_red_local_error(self, msg):
        self.btn_refresh_red_local.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al consultar la red local")
        self.lbl_red_local_vacio.setVisible(False)
        self._avisar("Error", f"No se pudo consultar la red local:\n\n{msg}", QMessageBox.Warning)

    def _parsear_rango_ip(self, texto: str):
        """'X.X.X.A-B' (mismo prefijo, solo cambia el último octeto) o
        'IP1-IP2' (dos IPs completas) -> (entero_inicio, entero_fin), o
        None si no es un rango reconocible (incluido: campo vacío)."""
        texto = texto.strip()
        if not texto or "-" not in texto:
            return None
        izq, _, der = texto.partition("-")
        izq, der = izq.strip(), der.strip()
        try:
            ip_ini = ipaddress.IPv4Address(izq)
            if "." in der:
                ip_fin = ipaddress.IPv4Address(der)
            else:
                base = izq.rsplit(".", 1)[0]
                ip_fin = ipaddress.IPv4Address(f"{base}.{int(der)}")
        except (ValueError, ipaddress.AddressValueError):
            return None
        ini, fin = int(ip_ini), int(ip_fin)
        return (ini, fin) if ini <= fin else (fin, ini)

    def _apply_red_local_rango_ip(self):
        rango = self._parsear_rango_ip(self.search_red_local_rango.text())
        self.red_local_proxy.set_rango_ip(rango)
        self._update_red_local_counter()

    def _apply_red_local_filters(self):
        self.red_local_proxy.set_filtros(
            self.chk_red_local_reachable.isChecked(),
            self.chk_red_local_stale.isChecked(),
            self.chk_red_local_permanent.isChecked(),
            self.chk_red_local_otros.isChecked(),
        )
        self._update_red_local_counter()

    def _update_red_local_counter(self):
        total = self.red_local_model.rowCount()
        visible = self.red_local_proxy.rowCount()
        self.lbl_red_local_count.setText(f"{visible}/{total} dispositivos")

    def _dispositivo_red_local_seleccionado(self):
        sel = self.red_local_table.selectionModel()
        if not sel or not sel.hasSelection():
            return None
        idx = sel.selectedRows()[0]
        fuente_idx = self.red_local_proxy.mapToSource(idx)
        return self.red_local_model.rows()[fuente_idx.row()]

    def _ctx_red_local(self, pos):
        dispositivo = self._dispositivo_red_local_seleccionado()
        if not dispositivo:
            return

        menu = QMenu(self)
        act_resolver = menu.addAction("🔤 Resolver nombre de host")
        act_web = menu.addAction("🌐 Comprobar acceso web (80/443)")
        act_abrir_web = None
        if dispositivo.acceso_web and dispositivo.acceso_web != "No detectado":
            act_abrir_web = menu.addAction(f"🔗 Abrir en el navegador ({dispositivo.acceso_web})")
        act_ping = menu.addAction("📶 Hacer ping")
        act_wol = None
        mac_valida = dispositivo.mac and dispositivo.mac.strip().lower() not in (
            "", "00-00-00-00-00-00", "ff-ff-ff-ff-ff-ff")
        if mac_valida:
            menu.addSeparator()
            act_wol = menu.addAction("⚡ Despertar (Wake-on-LAN)")
        menu.addSeparator()
        if dispositivo.sospechoso:
            act_sospechoso = menu.addAction("✅ Quitar marca de sospechoso")
        else:
            act_sospechoso = menu.addAction("🚩 Marcar como sospechoso")
        menu.addSeparator()
        act_copiar = menu.addAction("📋 Copiar fila")
        act_detalle = menu.addAction("🔍 Ver detalle completo")

        action = menu.exec_(self.red_local_table.viewport().mapToGlobal(pos))
        if action == act_resolver:
            self._resolver_nombre_red_local(dispositivo)
        elif action == act_sospechoso:
            self._toggle_sospechoso_red_local(dispositivo)
        elif action == act_web:
            self._comprobar_acceso_web(dispositivo)
        elif act_abrir_web is not None and action == act_abrir_web:
            self._abrir_acceso_web_red_local(dispositivo)
        elif action == act_ping:
            self._hacer_ping_red_local(dispositivo)
        elif act_wol is not None and action == act_wol:
            self._wake_on_lan(dispositivo)
        elif action == act_copiar:
            self._copiar_seleccion_generica(self.red_local_table, self.red_local_proxy, NetworkDeviceTableModel.HEADERS)
        elif action == act_detalle:
            self._ver_detalle_dispositivo_red_local(dispositivo)

    def _ver_detalle_dispositivo_red_local(self, dispositivo):
        items = [
            f"IP: {dispositivo.ip}",
            f"MAC: {dispositivo.mac}",
            f"Fabricante: {dispositivo.fabricante}",
            f"Tipo estimado: {dispositivo.tipo_estimado}",
            f"Estado: {dispositivo.estado}",
            f"Interfaz: {dispositivo.interfaz}",
            f"Nombre: {dispositivo.hostname or '(sin resolver)'}",
            f"Acceso web: {dispositivo.acceso_web or '(sin comprobar)'}",
            f"Marcado como sospechoso: {'Sí' if dispositivo.sospechoso else 'No'}",
        ]
        self._mostrar_informe(f"Dispositivo: {dispositivo.ip}", [], items)

    def _actualizar_fila_red_local(self, ip: str, **cambios):
        """Actualiza los campos indicados (hostname, acceso_web...) de la
        fila con esa IP y refresca la tabla — evita tener que recargar
        todo el escaneo ARP solo para reflejar un dato obtenido bajo
        demanda (resolver nombre, comprobar acceso web...)."""
        rows = self.red_local_model.rows()
        for row in rows:
            if row.ip == ip:
                for campo, valor in cambios.items():
                    setattr(row, campo, valor)
                break
        self.red_local_model.setRows(rows)

    def _toggle_sospechoso_red_local(self, dispositivo):
        """Marca/desmarca un dispositivo como sospechoso — clave MAC (no
        IP), para que la marca sobreviva a un cambio de IP por DHCP.
        Reversible: quitar la marca es tan sencillo como aplicarla."""
        if not dispositivo.mac or not dispositivo.mac.strip():
            self._avisar("Marcar como sospechoso",
                         "Este dispositivo no tiene una MAC válida — no se puede marcar.",
                         QMessageBox.Warning)
            return

        if dispositivo.sospechoso:
            if not self._confirmar_reparacion(
                "Quitar marca de sospechoso",
                cambios=[f"«{dispositivo.ip}» ({dispositivo.mac}) dejará de resaltarse como sospechoso"],
                reversible=True, objetivo=dispositivo.ip,
                explicacion="Puedes volver a marcarlo en cualquier momento desde este mismo menú.",
            ):
                return
            if self.dispositivo_sospechoso_db.remove(dispositivo.mac):
                dispositivo.sospechoso = False
                self.red_local_model.setRows(self.red_local_model.rows())
                self.set_status(f"✅ «{dispositivo.ip}» ya no está marcado como sospechoso")
            else:
                self._avisar("Error", "No se pudo quitar la marca (fallo de base de datos local).",
                             QMessageBox.Warning)
        else:
            if not self._confirmar_reparacion(
                "Marcar como sospechoso",
                cambios=[f"«{dispositivo.ip}» ({dispositivo.mac}) se resaltará en rojo en cada escaneo futuro"],
                reversible=True, objetivo=dispositivo.ip,
                explicacion="La marca se guarda por MAC, así que sigue vigente aunque el dispositivo "
                            "cambie de IP. Puedes quitarla después desde este mismo menú, o desde "
                            "Soporte > Ver equipos marcados como sospechosos.",
            ):
                return
            if self.dispositivo_sospechoso_db.add(dispositivo.mac, dispositivo.ip, "Marcado manualmente desde Red Local"):
                dispositivo.sospechoso = True
                self.red_local_model.setRows(self.red_local_model.rows())
                self.set_status(f"🚩 «{dispositivo.ip}» marcado como sospechoso")
            else:
                self._avisar("Error", "No se pudo guardar la marca (fallo de base de datos local).",
                             QMessageBox.Warning)

    def _resolver_nombre_red_local(self, dispositivo):
        """Re-resuelve un único dispositivo bajo demanda (p. ej. si el
        enriquecimiento automático tras el escaneo no encontró nombre la
        primera vez) — reusa el mismo hilo DNS+NetBIOS que el
        enriquecimiento automático, no una versión más simple aparte."""
        ip = dispositivo.ip
        self.set_status(f"🔄 Resolviendo nombre de {ip}...")
        self._resolver_thread_red_local = DeviceEnrichmentThread([ip])
        self._resolver_thread_red_local.dispositivo_enriquecido.connect(self._on_hostname_resuelto_red_local)
        self._resolver_thread_red_local.start()

    def _on_hostname_resuelto_red_local(self, ip: str, hostname: str, acceso_web: str):
        if hostname:
            self._actualizar_fila_red_local(ip, hostname=hostname, acceso_web=acceso_web)
            self.set_status(f"✅ {ip} → {hostname}")
        else:
            self._actualizar_fila_red_local(ip, acceso_web=acceso_web)
            self.set_status(f"⚠️ No se pudo resolver {ip} (ni por DNS ni por NetBIOS)")

    def _comprobar_acceso_web(self, dispositivo):
        """Comprueba con una conexión TCP directa (rápida, sin PowerShell)
        si el dispositivo responde en el puerto 80 (HTTP) y/o 443 (HTTPS)
        — típico de routers, impresoras, NAS y cámaras IP con interfaz de
        gestión web. Puramente informativo: no envía ninguna petición
        HTTP real, solo comprueba si el puerto acepta conexión."""
        ip = dispositivo.ip
        self.set_status(f"🔄 Comprobando acceso web en {ip}...")
        QApplication.processEvents()

        resultado = _comprobar_acceso_web_ip(ip)
        abiertos = [] if resultado == "No detectado" else resultado.split(" + ")

        if abiertos:
            self._actualizar_fila_red_local(ip, acceso_web=resultado)
            self.set_status(f"✅ {ip}: acceso web detectado ({resultado})")
            if self._confirmar_accion(
                "Acceso web detectado",
                f"{ip} responde en: {resultado}.\n\n¿Abrir en el navegador?",
            ):
                self._abrir_navegador_dispositivo(ip, resultado)
        else:
            self._actualizar_fila_red_local(ip, acceso_web="No detectado")
            self.set_status(f"ℹ️ {ip}: sin acceso web en 80/443")

    def _abrir_navegador_dispositivo(self, ip: str, acceso_web: str):
        """Abre directamente la interfaz web ya detectada de un
        dispositivo — sin volver a comprobar el puerto, para eso ya está
        'Comprobar acceso web'. Prefiere https si el dispositivo ofrece
        los dos."""
        protocolo = "https" if "https" in acceso_web else "http"
        import webbrowser
        webbrowser.open(f"{protocolo}://{ip}")
        self.set_status(f"🌐 Abriendo {protocolo}://{ip} en el navegador")

    def _abrir_acceso_web_red_local(self, dispositivo):
        if not dispositivo.acceso_web or dispositivo.acceso_web == "No detectado":
            self._avisar("Abrir en el navegador",
                         "Este dispositivo no tiene acceso web detectado. Usa antes "
                         "'Comprobar acceso web (80/443)'.", QMessageBox.Warning)
            return
        self._abrir_navegador_dispositivo(dispositivo.ip, dispositivo.acceso_web)

    def _hacer_ping_red_local(self, dispositivo):
        ip = dispositivo.ip
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            self._avisar("Hacer ping", "IP inválida.", QMessageBox.Warning)
            return

        self.set_status(f"🔄 Haciendo ping a {ip}...")
        QApplication.processEvents()
        data = run_powershell_json(f'$ip = "{ip}"\n' + PS_PING_HOST, timeout=15)
        self.set_status("")

        if data is None:
            self._avisar("Hacer ping", "No se pudo completar el ping.", QMessageBox.Warning)
            return

        if data.get("Alcanzable"):
            resumen = [
                f"Enviados: {data.get('Enviados', '?')}, Recibidos: {data.get('Recibidos', '?')}",
                f"Tiempo: mín {data.get('TiempoMinimo', '?')} ms / medio {data.get('TiempoMedio', '?')} ms "
                f"/ máx {data.get('TiempoMaximo', '?')} ms",
            ]
            self._mostrar_informe(f"Ping a {ip}", resumen, [])
        else:
            self._mostrar_informe(f"Ping a {ip}", [f"Sin respuesta: {data.get('Error', 'host inalcanzable')}"], [],
                                   es_alerta=True)

    def _wake_on_lan(self, dispositivo):
        """Envía el 'magic packet' estándar de Wake-on-LAN (broadcast UDP,
        puerto 9) — no requiere privilegios de administrador ni PowerShell,
        es un simple datagrama UDP. Solo despierta el equipo si su propia
        placa/BIOS tiene WoL habilitado; MASys no puede saber de
        antemano si es el caso."""
        mac_limpia = dispositivo.mac.replace("-", "").replace(":", "").upper()
        if len(mac_limpia) != 12:
            self._avisar("Wake-on-LAN", "MAC inválida.", QMessageBox.Warning)
            return

        if not self._confirmar_accion(
            "Wake-on-LAN",
            f"Se enviará un paquete de 'despertar' (Wake-on-LAN) a la MAC {dispositivo.mac}.\n\n"
            "Solo funciona si el equipo de destino tiene esta función habilitada en su "
            "placa base/BIOS y sigue conectado a la alimentación.\n\n¿Continuar?",
        ):
            return

        try:
            mac_bytes = bytes.fromhex(mac_limpia)
            paquete = b"\xff" * 6 + mac_bytes * 16
            with _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM) as s:
                s.setsockopt(_socket.SOL_SOCKET, _socket.SO_BROADCAST, 1)
                s.sendto(paquete, ("255.255.255.255", 9))
            self.set_status(f"✅ Paquete Wake-on-LAN enviado a {dispositivo.mac}")
        except OSError as e:
            self._avisar("Wake-on-LAN", f"No se pudo enviar el paquete:\n\n{e}", QMessageBox.Critical)

