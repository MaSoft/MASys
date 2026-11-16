# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña Servicios: tabla, panel de detalle, notas, tipo de inicio, acciones (iniciar/detener/reiniciar), menu contextual.
Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).

Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

import subprocess
from typing import List, Optional

from PyQt5.QtCore import Qt, QRegExp
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QCheckBox, QComboBox, QPushButton, QMenu, QMessageBox, QSplitter,
    QFrame, QTextEdit, QTableView,
)

from msys.core.utils import escape_powershell_string
from msys.models.rows import ServiceRow, STARTUP_LABELS, _CRITICAL_SERVICES_LOWER, _ruta_ejecutable_de_bin_path
from msys.models.table_models import ServiceTableModel, ServiceFilterProxy
from msys.widgets.delegates import _setup_header
from msys.collectors import ServiceCollector


# 5.4.0: antes «{verbo}ando» daba «Finalizarando», «Detenerando», «Activarando»...
_GERUNDIO = {"Finalizar": "Finalizando", "Suspender": "Suspendiendo", "Reanudar": "Reanudando", "Iniciar": "Iniciando", "Detener": "Deteniendo", "Reiniciar": "Reiniciando", "Activar": "Activando", "Desactivar": "Desactivando"}

class ServiciosMixin:
    def crear_tab_services(self):
        """Crea la pestaña de servicios — operativa portada de MA_SERV
        (LAUNCHER\\SCRIPTS\\MA_SERV\\ui\\services_tab.py): filtros avanzados,
        panel de detalle, notas y cambio de tipo de inicio. Construida sólo
        con widgets y objectName ya cubiertos por LIGHT_STYLESHEET/
        DARK_STYLESHEET (a diferencia de MA_SERV, que fijaba sus propios
        colores claros con setStyleSheet por widget), para heredar el tema
        activo de MASys automáticamente. Iniciar/Detener/Reiniciar viven
        en la fila 2 (row2), junto a Nota — todas las acciones sobre el
        servicio seleccionado quedan agrupadas arriba, en vez de repartidas
        entre esta fila y la barra inferior global."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        # ── Barra de herramientas ────────────────────────────────────────
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        import socket
        hostname = socket.gethostname()
        lbl_hostname = QLabel(f"🖥️ {hostname}")
        lbl_hostname.setObjectName("hostnameLabel")
        toolbar.addWidget(lbl_hostname)

        self.search_services = QLineEdit()
        self.search_services.setPlaceholderText("🔎 Buscar por nombre, servicio o descripción...")
        self.search_services.textChanged.connect(self._filtrar_servicios)
        toolbar.addWidget(self.search_services, 3)

        self.combo_filtro_servicios = QComboBox()
        for label, key in [("Todos", "all"), ("Ejecutándose", "running"),
                            ("Detenidos", "stopped"), ("Deshabilitados", "disabled"),
                            ("Con nota", "noted")]:
            self.combo_filtro_servicios.addItem(label, key)
        self.combo_filtro_servicios.currentIndexChanged.connect(self._filtrar_servicios)
        toolbar.addWidget(self.combo_filtro_servicios)

        self.chk_solo_notas_servicios = QCheckBox("Solo notas")
        self.chk_solo_notas_servicios.stateChanged.connect(self._filtrar_servicios)
        toolbar.addWidget(self.chk_solo_notas_servicios)

        self.chk_ocultar_win_servicios = QCheckBox("Ocultar Windows")
        self.chk_ocultar_win_servicios.setToolTip("Oculta los servicios propios del sistema operativo Windows")
        self.chk_ocultar_win_servicios.stateChanged.connect(self._filtrar_servicios)
        toolbar.addWidget(self.chk_ocultar_win_servicios)

        self.lbl_count_servicios = QLabel("Cargando...")
        self.lbl_count_servicios.setObjectName("mutedInfoLabel")
        toolbar.addWidget(self.lbl_count_servicios)

        toolbar.addStretch()

        self.btn_refresh_services = QPushButton("🔄 Actualizar")
        self.btn_refresh_services.setObjectName("primary")
        self.btn_refresh_services.clicked.connect(self.refresh_services)
        toolbar.addWidget(self.btn_refresh_services)

        layout.addLayout(toolbar)

        # ── Segunda fila: tipo de inicio + nota ──────────────────────────
        row2 = QHBoxLayout()
        row2.setSpacing(8)

        row2.addWidget(QLabel("Tipo de inicio:"))

        self.combo_startup_servicios = QComboBox()
        for lbl, key in [("Automático", "auto"), ("Automático (retrasado)", "auto_delayed"),
                          ("Manual", "manual"), ("Deshabilitado", "disabled")]:
            self.combo_startup_servicios.addItem(lbl, key)
        self.combo_startup_servicios.setEnabled(False)
        self.combo_startup_servicios.setFixedWidth(200)
        row2.addWidget(self.combo_startup_servicios)

        self.btn_aplicar_startup_servicio = QPushButton("Aplicar tipo de inicio")
        self.btn_aplicar_startup_servicio.setObjectName("ghost")
        self.btn_aplicar_startup_servicio.setEnabled(False)
        self.btn_aplicar_startup_servicio.clicked.connect(self._aplicar_tipo_inicio_servicio)
        row2.addWidget(self.btn_aplicar_startup_servicio)

        row2.addSpacing(16)

        self.btn_servicio_iniciar = QPushButton("▶ Iniciar")
        self.btn_servicio_iniciar.setObjectName("ghost")
        self.btn_servicio_iniciar.setToolTip("Iniciar el servicio seleccionado")
        self.btn_servicio_iniciar.setEnabled(False)
        self.btn_servicio_iniciar.clicked.connect(lambda: self._accion_servicio_toolbar("start"))
        row2.addWidget(self.btn_servicio_iniciar)

        self.btn_servicio_detener = QPushButton("■ Detener")
        self.btn_servicio_detener.setObjectName("ghost")
        self.btn_servicio_detener.setToolTip("Detener el servicio seleccionado")
        self.btn_servicio_detener.setEnabled(False)
        self.btn_servicio_detener.clicked.connect(lambda: self._accion_servicio_toolbar("stop"))
        row2.addWidget(self.btn_servicio_detener)

        self.btn_servicio_reiniciar = QPushButton("↺ Reiniciar")
        self.btn_servicio_reiniciar.setObjectName("ghost")
        self.btn_servicio_reiniciar.setToolTip("Reiniciar el servicio seleccionado")
        self.btn_servicio_reiniciar.setEnabled(False)
        self.btn_servicio_reiniciar.clicked.connect(lambda: self._accion_servicio_toolbar("restart"))
        row2.addWidget(self.btn_servicio_reiniciar)

        row2.addSpacing(16)

        self.btn_nota_servicio = QPushButton("📝 Nota")
        self.btn_nota_servicio.setObjectName("ghost")
        self.btn_nota_servicio.setEnabled(False)
        self.btn_nota_servicio.clicked.connect(self._editar_nota_servicio)
        row2.addWidget(self.btn_nota_servicio)

        self.btn_borrar_nota_servicio = QPushButton("✕ Borrar nota")
        self.btn_borrar_nota_servicio.setObjectName("ghost")
        self.btn_borrar_nota_servicio.setVisible(False)
        self.btn_borrar_nota_servicio.clicked.connect(self._borrar_nota_servicio)
        row2.addWidget(self.btn_borrar_nota_servicio)

        row2.addStretch()
        layout.addLayout(row2)

        # ── Tabla + panel de detalle (splitter vertical) ─────────────────
        self.services_model = ServiceTableModel([])
        self.services_proxy = ServiceFilterProxy(self)
        self.services_proxy.setSourceModel(self.services_model)
        self.services_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.services_proxy.setFilterKeyColumn(-1)

        self.services_table = QTableView()
        self.services_table.setModel(self.services_proxy)
        self.services_table.setAlternatingRowColors(True)
        self.services_table.setSelectionBehavior(QTableView.SelectRows)
        self.services_table.setSelectionMode(QTableView.SingleSelection)
        self.services_table.verticalHeader().setVisible(False)
        self.services_table.setSortingEnabled(True)
        self.services_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.services_table.customContextMenuRequested.connect(self._ctx_servicios)

        _setup_header(self.services_table, len(ServiceTableModel.HEADERS), stretch_col=ServiceTableModel.COL_DISPLAY)
        self.services_table.selectionModel().selectionChanged.connect(self._on_servicio_seleccionado)

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.services_table)
        splitter.addWidget(self._crear_panel_detalle_servicio())
        splitter.setSizes([600, 170])

        layout.addWidget(splitter, 1)

        return tab

    def _crear_panel_detalle_servicio(self):
        """Panel de detalle bajo la tabla de Servicios (descripción,
        dependencias, cuenta, PID, nota). La nota vive en una TERCERA caja
        al lado de la descripción (no debajo de todo, como antes) y solo
        aparece si el servicio tiene nota guardada; las otras dos cajas no
        llevan un stretch artificialmente grande — se dimensionan a su
        contenido real (col_der sin stretch) para que añadir/quitar la caja
        de nota no las deforme."""
        panel = QFrame()
        panel.setFrameShape(QFrame.StyledPanel)
        v = QVBoxLayout(panel)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(3)

        cabecera = QHBoxLayout()
        cabecera.setSpacing(8)
        self.lbl_detalle_servicio_nombre = QLabel("Selecciona un servicio para ver el detalle")
        fuente = self.lbl_detalle_servicio_nombre.font()
        fuente.setBold(True)
        fuente.setPointSize(fuente.pointSize() + 1)
        self.lbl_detalle_servicio_nombre.setFont(fuente)
        self.lbl_detalle_servicio_nombre.setWordWrap(True)
        cabecera.addWidget(self.lbl_detalle_servicio_nombre, 1)
        lbl_desc_hdr = QLabel("Descripción:")
        lbl_desc_hdr.setObjectName("mutedInfoLabel")
        cabecera.addWidget(lbl_desc_hdr)
        v.addLayout(cabecera)

        fila = QHBoxLayout()
        fila.setSpacing(10)

        self.txt_detalle_servicio_desc = QTextEdit()
        self.txt_detalle_servicio_desc.setReadOnly(True)
        fila.addWidget(self.txt_detalle_servicio_desc, 1)

        col_der = QVBoxLayout()
        col_der.setSpacing(1)
        self.lbl_detalle_servicio_cuenta = QLabel("Cuenta: —")
        col_der.addWidget(self.lbl_detalle_servicio_cuenta)
        self.lbl_detalle_servicio_pid = QLabel("PID: —")
        col_der.addWidget(self.lbl_detalle_servicio_pid)
        lbl_deps_hdr = QLabel("Dependencias:")
        lbl_deps_hdr.setObjectName("mutedInfoLabel")
        col_der.addWidget(lbl_deps_hdr)
        self.lbl_detalle_servicio_deps = QLabel("ninguna")
        self.lbl_detalle_servicio_deps.setWordWrap(True)
        self.lbl_detalle_servicio_deps.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        col_der.addWidget(self.lbl_detalle_servicio_deps, 1)
        # Sin stretch (peso 0): ocupa solo el ancho que necesita su
        # contenido real, no una fracción fija de la fila.
        fila.addLayout(col_der)

        # ── Caja de nota (tercera columna, al lado — solo visible si hay nota) ──
        self.panel_nota_servicio = QWidget()
        col_nota = QVBoxLayout(self.panel_nota_servicio)
        col_nota.setContentsMargins(0, 0, 0, 0)
        col_nota.setSpacing(2)
        lbl_nota_hdr = QLabel("📝 Nota:")
        lbl_nota_hdr.setObjectName("mutedInfoLabel")
        col_nota.addWidget(lbl_nota_hdr)
        self.txt_detalle_servicio_nota = QTextEdit()
        self.txt_detalle_servicio_nota.setReadOnly(True)
        self.txt_detalle_servicio_nota.setStyleSheet(
            "QTextEdit { background-color: #FFF8E1; color: #6d4c00; "
            "border: none; border-left: 3px solid #FF9800; padding: 4px 8px; }"
        )
        col_nota.addWidget(self.txt_detalle_servicio_nota)
        self.panel_nota_servicio.setVisible(False)
        fila.addWidget(self.panel_nota_servicio, 1)

        # fila con stretch=1: las cajas rellenan todo el alto restante del
        # panel hasta el final, en vez de quedar con un hueco muerto debajo.
        v.addLayout(fila, 1)

        return panel

    def refresh_services(self):
        """Actualiza lista de servicios."""
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.service_notes = self.service_notes_db.get_all_notes()
        self.set_status("🔄 Escaneando servicios...")
        self.btn_refresh_services.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.worker = ServiceCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_services_data)
        self.worker.finished_error.connect(self._on_services_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_services_data(self, rows: List[ServiceRow]):
        """Procesa datos de servicios."""
        self.services_model.setRows(rows)
        self.services_model.setNotes(self.service_notes)
        self.btn_refresh_services.setEnabled(True)
        self.progress_bar.setVisible(False)
        total = len(rows)
        running = sum(1 for r in rows if r.status == "running")
        self.set_status(f"✅ {total} servicios encontrados ({running} en ejecución)")
        self._filtrar_servicios()
        self._on_servicio_seleccionado()

    def _on_services_error(self, msg: str):
        """Maneja errores de servicios."""
        self.btn_refresh_services.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al escanear servicios")
        self._avisar("Error", f"No se pudieron obtener los servicios:\n\n{msg}", QMessageBox.Warning)

    def _filtrar_servicios(self):
        """Reaplica búsqueda + combo de estado + checkboxes sobre el proxy
        de Servicios. Conectado a todos los controles de filtrado del
        toolbar (ver crear_tab_services)."""
        if not hasattr(self, 'services_proxy'):
            return
        self.services_proxy.setFilterRegExp(
            QRegExp(self.search_services.text(), Qt.CaseInsensitive, QRegExp.FixedString))
        status_key = self.combo_filtro_servicios.currentData()
        self.services_proxy.setStatusFilters(
            status_key, self.chk_solo_notas_servicios.isChecked(),
            self.chk_ocultar_win_servicios.isChecked(), self.service_notes,
        )
        self._actualizar_contador_servicios()

    def _actualizar_contador_servicios(self):
        if not hasattr(self, 'services_model'):
            return
        total = self.services_model.rowCount()
        visibles = self.services_proxy.rowCount()
        running = sum(1 for r in self.services_model.rows() if r.status == "running")
        self.lbl_count_servicios.setText(f"Mostrando {visibles} / {total}  |  {running} en ejecución")

    def _servicio_seleccionado(self) -> Optional[ServiceRow]:
        sel = self.services_table.selectionModel()
        if not sel or not sel.hasSelection():
            return None
        idx = sel.selectedRows()[0]
        name = self.services_proxy.data(idx, Qt.UserRole)
        return self.services_model.row_by_name(name)

    def _on_servicio_seleccionado(self, *_args):
        """Refresca panel de detalle + habilita/deshabilita controles según
        haya o no un servicio seleccionado (patrón _set_action_buttons_enabled
        de MA_SERV)."""
        row = self._servicio_seleccionado()
        habilitado = row is not None
        self.btn_nota_servicio.setEnabled(habilitado)
        self.combo_startup_servicios.setEnabled(habilitado)
        self.btn_aplicar_startup_servicio.setEnabled(habilitado)
        self.btn_borrar_nota_servicio.setVisible(habilitado and row.name in self.service_notes)

        if row is None:
            self.lbl_detalle_servicio_nombre.setText("Selecciona un servicio para ver el detalle")
            self.txt_detalle_servicio_desc.clear()
            self.lbl_detalle_servicio_cuenta.setText("Cuenta: —")
            self.lbl_detalle_servicio_pid.setText("PID: —")
            self.lbl_detalle_servicio_deps.setText("ninguna")
            self.panel_nota_servicio.setVisible(False)
        else:
            self.lbl_detalle_servicio_nombre.setText(f"{row.display_name}  [{row.name}]")
            self.txt_detalle_servicio_desc.setPlainText(row.description or "(sin descripción)")
            self.lbl_detalle_servicio_cuenta.setText(f"Cuenta: {row.account or '-'}")
            self.lbl_detalle_servicio_pid.setText(f"PID: {row.pid}" if row.pid else "PID: -")
            self.lbl_detalle_servicio_deps.setText(", ".join(row.dependencies) if row.dependencies else "ninguna")
            idx_startup = self.combo_startup_servicios.findData(row.startup_type)
            if idx_startup >= 0:
                self.combo_startup_servicios.setCurrentIndex(idx_startup)
            nota = self.service_notes.get(row.name)
            if nota:
                self.txt_detalle_servicio_nota.setPlainText(nota)
                self.panel_nota_servicio.setVisible(True)
            else:
                self.panel_nota_servicio.setVisible(False)

        self._actualizar_botones_accion_servicio(row)

    def _aplicar_tipo_inicio_servicio(self):
        row = self._servicio_seleccionado()
        if not row:
            return
        nuevo_tipo = self.combo_startup_servicios.currentData()
        ok, msg = self.service_manager.set_startup_type(row.name, nuevo_tipo)
        self._registrar_historial("🛠️ Servicios", "Cambiar tipo de inicio", f"{row.display_name} ({row.name}) → {nuevo_tipo}", ok, msg)
        if ok:
            etiqueta = STARTUP_LABELS.get(nuevo_tipo, nuevo_tipo)
            self.set_status(f"✅ Tipo de inicio de «{row.display_name}» cambiado a '{etiqueta}'")
            self.refresh_services()
        else:
            self._avisar("Error", f"No se pudo cambiar el tipo de inicio:\n{msg}", QMessageBox.Warning)

    def _editar_nota_servicio(self):
        row = self._servicio_seleccionado()
        if not row:
            return
        actual = self.service_notes.get(row.name, "")
        texto, ok = self._editar_texto_multilinea(
            f"Nota — {row.display_name}", "Nota de este servicio:", actual)
        if not ok:
            return
        texto = texto.strip()
        if texto:
            self.service_notes_db.save_note(row.name, texto)
            self.service_notes[row.name] = texto
        else:
            self.service_notes_db.delete_note(row.name)
            self.service_notes.pop(row.name, None)
        self.services_model.setNotes(self.service_notes)
        self._on_servicio_seleccionado()
        self._filtrar_servicios()

    def _borrar_nota_servicio(self):
        row = self._servicio_seleccionado()
        if not row:
            return
        if not self._confirmar_accion("Borrar nota",
                f"¿Borrar la nota del servicio «{row.display_name}»?"):
            return
        self.service_notes_db.delete_note(row.name)
        self.service_notes.pop(row.name, None)
        self.services_model.setNotes(self.service_notes)
        self._on_servicio_seleccionado()
        self._filtrar_servicios()
        self.set_status(f"✅ Nota eliminada de «{row.display_name}»")

    def _accion_servicio(self, row: ServiceRow, accion: str):
        """accion: 'start'|'stop'|'restart'. Único punto de verdad para
        Iniciar/Detener/Reiniciar, usado tanto desde el menú contextual como
        desde los botones de la fila 2 (ver _accion_servicio_toolbar).
        Llamada síncrona (igual patrón que _ctx_procesos): win32service es
        rápido y local, no justifica un QThread propio como hacía MA_SERV."""
        etiquetas = {"start": "Iniciar", "stop": "Detener", "restart": "Reiniciar"}
        etiqueta = etiquetas[accion]
        texto = f"¿{etiqueta} el servicio «{row.display_name}»?"
        if accion in ("stop", "restart") and row.name.lower() in _CRITICAL_SERVICES_LOWER:
            texto += ("\n\n⚠ Este es un servicio crítico del sistema. Detenerlo puede "
                      "provocar un bloqueo, cierre de sesión o inestabilidad del equipo.")
        if accion in ("stop", "restart"):
            # Windows detiene en cascada los servicios que dependen de este
            # — sin este aviso, el usuario no se entera hasta que ya se han
            # caído (el propio Administrador de servicios de Windows sí
            # muestra esta advertencia antes de detener).
            dependientes = self.service_manager.get_dependent_services(row.name)
            if dependientes:
                texto += ("\n\n⚠ Estos servicios dependen de este y se detendrán también: "
                          + ", ".join(dependientes))
        if not self._confirmar_accion(f"{etiqueta} servicio", texto):
            return
        self.set_status(f"🔄 {_GERUNDIO.get(etiqueta, etiqueta)} servicio {row.name}...")
        QApplication.processEvents()
        metodo = {
            "start": self.service_manager.start_service,
            "stop": self.service_manager.stop_service,
            "restart": self.service_manager.restart_service,
        }[accion]
        ok, msg = metodo(row.name)
        self._registrar_historial("🛠️ Servicios", f"{etiqueta} servicio", f"{row.display_name} ({row.name})", ok, msg)
        if ok:
            self.set_status(f"✅ Servicio {row.name} — {etiqueta} completado")
            self.refresh_services()
        else:
            self._avisar("Error", f"No se pudo {etiqueta.lower()} el servicio.\n\n{msg}", QMessageBox.Warning)

    def _accion_servicio_toolbar(self, accion: str):
        """Iniciar/Detener/Reiniciar desde los botones pequeños de la fila
        de herramientas de la pestaña Servicios (junto a Nota). Actúa sobre
        la fila seleccionada en self.services_table."""
        row = self._servicio_seleccionado()
        if not row:
            self._avisar("Servicios", "Selecciona antes un servicio en la tabla.")
            return
        self._accion_servicio(row, accion)

    def _actualizar_botones_accion_servicio(self, row: Optional[ServiceRow]):
        """Habilita/deshabilita Iniciar/Detener/Reiniciar según el estado
        real del servicio seleccionado (mismo criterio que el menú
        contextual). Se llama desde _on_servicio_seleccionado()."""
        if not hasattr(self, 'btn_servicio_iniciar'):
            return
        habilitado = row is not None
        self.btn_servicio_iniciar.setEnabled(habilitado and row.status in ("stopped", "paused"))
        self.btn_servicio_detener.setEnabled(habilitado and row.status in ("running", "paused"))
        self.btn_servicio_reiniciar.setEnabled(habilitado and row.status == "running")

    def _ctx_servicios(self, pos):
        idx = self.services_table.indexAt(pos)
        if not idx.isValid():
            return
        self.services_table.selectRow(idx.row())
        row = self._servicio_seleccionado()
        if not row:
            return

        ruta = _ruta_ejecutable_de_bin_path(row.bin_path) if row.bin_path else ""

        menu = QMenu(self)
        act_start   = menu.addAction("▶️ Iniciar servicio")
        act_stop    = menu.addAction("⏹️ Detener servicio")
        act_restart = menu.addAction("🔄 Reiniciar servicio")
        act_start.setEnabled(row.status in ("stopped", "paused"))
        act_stop.setEnabled(row.status in ("running", "paused"))
        act_restart.setEnabled(row.status == "running")
        menu.addSeparator()
        act_nota = menu.addAction("📝 Editar nota")
        act_borrar_nota = menu.addAction("✕ Borrar nota") if row.name in self.service_notes else None
        menu.addSeparator()
        act_hash = menu.addAction("🔑 Ver hash SHA-256 del ejecutable")
        act_sig = menu.addAction("🔏 Verificar firma digital")
        menu.addSeparator()
        act_copy = menu.addAction("📋 Copiar fila")

        action = menu.exec_(self.services_table.viewport().mapToGlobal(pos))
        if action == act_start:
            self._accion_servicio(row, "start")
        elif action == act_stop:
            self._accion_servicio(row, "stop")
        elif action == act_restart:
            self._accion_servicio(row, "restart")
        elif action == act_nota:
            self._editar_nota_servicio()
        elif act_borrar_nota is not None and action == act_borrar_nota:
            self._borrar_nota_servicio()

        elif action == act_hash:
            if not ruta:
                self._avisar("Hash", "Ruta no disponible para calcular hash.")
                return
            self.set_status("🔄 Calculando hash...")
            ruta_escaped = escape_powershell_string(ruta)
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-FileHash -LiteralPath '{ruta_escaped}' -Algorithm SHA256 -ErrorAction SilentlyContinue).Hash"],
                capture_output=True, text=True, timeout=15,
                startupinfo=self._get_startupinfo()
            )
            hash_val = out.stdout.strip() or "No se pudo calcular"
            self._avisar(f"SHA-256 — {row.display_name}", f"Archivo: {ruta}\n\nSHA-256:\n{hash_val}")
            QApplication.clipboard().setText(hash_val)
            self.set_status("✅ Hash copiado al portapapeles")

        elif action == act_sig:
            if not ruta:
                self._avisar("Firma digital", "Ruta no disponible para verificar la firma.")
                return
            self.set_status("🔄 Comprobando firma digital...")
            ruta_escaped = escape_powershell_string(ruta)
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"$s = Get-AuthenticodeSignature -LiteralPath '{ruta_escaped}'; "
                 "\"$($s.Status)|$($s.SignerCertificate.Subject)\""],
                capture_output=True, text=True, timeout=15,
                startupinfo=self._get_startupinfo()
            )
            partes = (out.stdout or "").strip().split("|", 1)
            estado_raw = partes[0] if partes else ""
            firmante = partes[1] if len(partes) > 1 and partes[1] else "Desconocido"
            iconos = {"Valid": "✅ Válida", "NotSigned": "🚫 Sin firmar",
                      "HashMismatch": "🔴 Hash no coincide (posible alteración)",
                      "NotTrusted": "⚠️ No confiable", "UnknownError": "⚠️ Error desconocido"}
            estado = iconos.get(estado_raw, estado_raw or "⚠️ No se pudo comprobar")
            self._avisar(f"Firma digital — {row.display_name}", f"Archivo: {ruta}\n\nEstado: {estado}\nFirmante: {firmante}")
            self.set_status(f"✅ Firma digital: {estado}")

        elif action == act_copy:
            cols = [self.services_proxy.data(self.services_proxy.index(idx.row(), c), Qt.DisplayRole)
                    for c in range(len(ServiceTableModel.HEADERS))]
            QApplication.clipboard().setText("\t".join(str(v or "") for v in cols))

    def copy_services_selection(self):
        """Copia servicios seleccionados al portapapeles."""
        selected_indexes = self.services_table.selectionModel().selectedRows()
        if not selected_indexes:
            self._avisar("Copiar", "No hay servicios seleccionados.")
            return

        lines = []
        lines.append("=" * 80)
        lines.append("SERVICIOS")
        lines.append("=" * 80)
        lines.append("")

        for index in selected_indexes:
            row = index.row()
            nombre_visible = str(self.services_proxy.data(self.services_proxy.index(row, ServiceTableModel.COL_DISPLAY), Qt.DisplayRole) or "")
            nombre = str(self.services_proxy.data(self.services_proxy.index(row, ServiceTableModel.COL_NAME), Qt.DisplayRole) or "")
            estado = str(self.services_proxy.data(self.services_proxy.index(row, ServiceTableModel.COL_STATE), Qt.DisplayRole) or "")
            tipo = str(self.services_proxy.data(self.services_proxy.index(row, ServiceTableModel.COL_STARTUP), Qt.DisplayRole) or "")
            cuenta = str(self.services_proxy.data(self.services_proxy.index(row, ServiceTableModel.COL_ACCOUNT), Qt.DisplayRole) or "")

            lines.append(f"{nombre_visible}")
            lines.append(f"  Nombre: {nombre} | Estado: {estado}")
            lines.append(f"  Inicio: {tipo} | Cuenta: {cuenta}")
            lines.append("")

        lines.append("=" * 80)

        text = "\n".join(lines)
        QApplication.clipboard().setText(text)
        self.set_status(f"✅ Copiados {len(selected_indexes)} servicios al portapapeles")

