# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña Procesos: tabla, panel de detalle, notas, cambio de prioridad, acciones (finalizar/suspender/reanudar), menu contextual.
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
from msys.models.rows import ProcessRow, PROCESS_PRIORITY_LABELS, _CRITICAL_PROCESSES
from msys.models.table_models import ProcessTableModel, ProcessFilterProxy
from msys.widgets.delegates import _setup_header
from msys.collectors import ProcessCollector


# 5.4.0: antes «{verbo}ando» daba «Finalizarando», «Detenerando», «Activarando»...
_GERUNDIO = {"Finalizar": "Finalizando", "Suspender": "Suspendiendo", "Reanudar": "Reanudando", "Iniciar": "Iniciando", "Detener": "Deteniendo", "Reiniciar": "Reiniciando", "Activar": "Activando", "Desactivar": "Desactivando"}

class ProcesosMixin:
    def crear_tab_processes(self):
        """Crea la pestaña de procesos — operativa portada de MAProcess
        (_TEMP\\MAProcess\\core\\process_manager.py + ui\\processes_tab.py),
        mismo tratamiento que recibió Servicios desde MA_SERV: filtros
        avanzados, panel de detalle, notas y control real de procesos vía
        psutil en vez de PowerShell (que limitaba el listado a 50 procesos
        arbitrarios y no exponía estado, prioridad ni usuario). Finalizar/
        Suspender/Reanudar viven en la fila 2 (row2), junto a Nota — mismo
        patrón de agrupación que Servicios."""
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

        self.search_processes = QLineEdit()
        self.search_processes.setPlaceholderText("🔎 Buscar por nombre, usuario o ruta...")
        self.search_processes.textChanged.connect(self._filtrar_procesos)
        toolbar.addWidget(self.search_processes, 3)

        self.combo_filtro_procesos = QComboBox()
        for label, key in [("Todos", "all"), ("Ejecutándose", "running"),
                            ("Suspendidos", "suspended"), ("Con nota", "noted")]:
            self.combo_filtro_procesos.addItem(label, key)
        self.combo_filtro_procesos.currentIndexChanged.connect(self._filtrar_procesos)
        toolbar.addWidget(self.combo_filtro_procesos)

        self.chk_solo_notas_procesos = QCheckBox("Solo notas")
        self.chk_solo_notas_procesos.stateChanged.connect(self._filtrar_procesos)
        toolbar.addWidget(self.chk_solo_notas_procesos)

        self.chk_ocultar_win_procesos = QCheckBox("Ocultar Windows")
        self.chk_ocultar_win_procesos.setToolTip("Oculta los procesos que corren desde la carpeta de Windows")
        self.chk_ocultar_win_procesos.stateChanged.connect(self._filtrar_procesos)
        toolbar.addWidget(self.chk_ocultar_win_procesos)

        self.lbl_count_procesos = QLabel("Cargando...")
        self.lbl_count_procesos.setObjectName("mutedInfoLabel")
        toolbar.addWidget(self.lbl_count_procesos)

        toolbar.addStretch()

        self.btn_refresh_processes = QPushButton("🔄 Actualizar")
        self.btn_refresh_processes.setObjectName("primary")
        self.btn_refresh_processes.clicked.connect(self.refresh_processes)
        toolbar.addWidget(self.btn_refresh_processes)

        layout.addLayout(toolbar)

        # ── Segunda fila: prioridad + acciones + nota ────────────────────
        row2 = QHBoxLayout()
        row2.setSpacing(8)

        row2.addWidget(QLabel("Prioridad:"))

        self.combo_prioridad_procesos = QComboBox()
        for key in ("realtime", "high", "above_normal", "normal", "below_normal", "idle"):
            self.combo_prioridad_procesos.addItem(PROCESS_PRIORITY_LABELS[key], key)
        self.combo_prioridad_procesos.setEnabled(False)
        self.combo_prioridad_procesos.setFixedWidth(200)
        self.combo_prioridad_procesos.setToolTip("Cambiar la prioridad del proceso seleccionado")
        row2.addWidget(self.combo_prioridad_procesos)

        self.btn_aplicar_prioridad_proceso = QPushButton("Aplicar prioridad")
        self.btn_aplicar_prioridad_proceso.setObjectName("ghost")
        self.btn_aplicar_prioridad_proceso.setEnabled(False)
        self.btn_aplicar_prioridad_proceso.clicked.connect(self._aplicar_prioridad_proceso)
        row2.addWidget(self.btn_aplicar_prioridad_proceso)

        row2.addSpacing(16)

        self.btn_proceso_finalizar = QPushButton("✖ Finalizar")
        self.btn_proceso_finalizar.setObjectName("ghost")
        self.btn_proceso_finalizar.setToolTip("Finalizar el proceso seleccionado")
        self.btn_proceso_finalizar.setEnabled(False)
        self.btn_proceso_finalizar.clicked.connect(lambda: self._accion_proceso_toolbar("kill"))
        row2.addWidget(self.btn_proceso_finalizar)

        self.btn_proceso_suspender = QPushButton("⏸ Suspender")
        self.btn_proceso_suspender.setObjectName("ghost")
        self.btn_proceso_suspender.setToolTip("Suspender el proceso seleccionado")
        self.btn_proceso_suspender.setEnabled(False)
        self.btn_proceso_suspender.clicked.connect(lambda: self._accion_proceso_toolbar("suspend"))
        row2.addWidget(self.btn_proceso_suspender)

        self.btn_proceso_reanudar = QPushButton("▶ Reanudar")
        self.btn_proceso_reanudar.setObjectName("ghost")
        self.btn_proceso_reanudar.setToolTip("Reanudar el proceso seleccionado")
        self.btn_proceso_reanudar.setEnabled(False)
        self.btn_proceso_reanudar.clicked.connect(lambda: self._accion_proceso_toolbar("resume"))
        row2.addWidget(self.btn_proceso_reanudar)

        row2.addSpacing(16)

        self.btn_nota_proceso = QPushButton("📝 Nota")
        self.btn_nota_proceso.setObjectName("ghost")
        self.btn_nota_proceso.setEnabled(False)
        self.btn_nota_proceso.clicked.connect(self._editar_nota_proceso)
        row2.addWidget(self.btn_nota_proceso)

        self.btn_borrar_nota_proceso = QPushButton("✕ Borrar nota")
        self.btn_borrar_nota_proceso.setObjectName("ghost")
        self.btn_borrar_nota_proceso.setVisible(False)
        self.btn_borrar_nota_proceso.clicked.connect(self._borrar_nota_proceso)
        row2.addWidget(self.btn_borrar_nota_proceso)

        row2.addStretch()
        layout.addLayout(row2)

        # ── Tabla + panel de detalle (splitter vertical) ─────────────────
        self.processes_model = ProcessTableModel([])
        self.processes_proxy = ProcessFilterProxy(self)
        self.processes_proxy.setSourceModel(self.processes_model)
        self.processes_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.processes_proxy.setFilterKeyColumn(-1)

        self.processes_table = QTableView()
        self.processes_table.setModel(self.processes_proxy)
        self.processes_table.setAlternatingRowColors(True)
        self.processes_table.setSelectionBehavior(QTableView.SelectRows)
        self.processes_table.setSelectionMode(QTableView.SingleSelection)
        self.processes_table.verticalHeader().setVisible(False)
        self.processes_table.setSortingEnabled(True)
        self.processes_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.processes_table.customContextMenuRequested.connect(self._ctx_procesos)

        _setup_header(self.processes_table, len(ProcessTableModel.HEADERS), stretch_col=ProcessTableModel.COL_DISPLAY)
        self.processes_table.selectionModel().selectionChanged.connect(self._on_proceso_seleccionado)

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.processes_table)
        splitter.addWidget(self._crear_panel_detalle_proceso())
        splitter.setSizes([600, 170])

        layout.addWidget(splitter, 1)

        return tab

    def _crear_panel_detalle_proceso(self):
        """Panel de detalle bajo la tabla de Procesos (ruta del ejecutable,
        procesos hijos, usuario, PID/PID padre, nota). La nota vive en una
        TERCERA caja al lado de la ruta (no debajo de todo, como antes) y
        solo aparece si el proceso tiene nota guardada; las otras dos cajas
        no llevan un stretch artificialmente grande — se dimensionan a su
        contenido real (col_der sin stretch) para que añadir/quitar la caja
        de nota no las deforme."""
        panel = QFrame()
        panel.setFrameShape(QFrame.StyledPanel)
        v = QVBoxLayout(panel)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(3)

        cabecera = QHBoxLayout()
        cabecera.setSpacing(8)
        self.lbl_detalle_proceso_nombre = QLabel("Selecciona un proceso para ver el detalle")
        fuente = self.lbl_detalle_proceso_nombre.font()
        fuente.setBold(True)
        fuente.setPointSize(fuente.pointSize() + 1)
        self.lbl_detalle_proceso_nombre.setFont(fuente)
        self.lbl_detalle_proceso_nombre.setWordWrap(True)
        cabecera.addWidget(self.lbl_detalle_proceso_nombre, 1)
        lbl_ruta_hdr = QLabel("Ruta del ejecutable:")
        lbl_ruta_hdr.setObjectName("mutedInfoLabel")
        cabecera.addWidget(lbl_ruta_hdr)
        v.addLayout(cabecera)

        fila = QHBoxLayout()
        fila.setSpacing(10)

        self.txt_detalle_proceso_ruta = QTextEdit()
        self.txt_detalle_proceso_ruta.setReadOnly(True)
        fila.addWidget(self.txt_detalle_proceso_ruta, 1)

        col_der = QVBoxLayout()
        col_der.setSpacing(1)
        self.lbl_detalle_proceso_usuario = QLabel("Usuario: —")
        col_der.addWidget(self.lbl_detalle_proceso_usuario)
        self.lbl_detalle_proceso_pid = QLabel("PID: —")
        col_der.addWidget(self.lbl_detalle_proceso_pid)
        lbl_hijos_hdr = QLabel("Procesos hijos:")
        lbl_hijos_hdr.setObjectName("mutedInfoLabel")
        col_der.addWidget(lbl_hijos_hdr)
        self.lbl_detalle_proceso_hijos = QLabel("ninguno")
        self.lbl_detalle_proceso_hijos.setWordWrap(True)
        self.lbl_detalle_proceso_hijos.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        col_der.addWidget(self.lbl_detalle_proceso_hijos, 1)
        # Sin stretch (peso 0): ocupa solo el ancho que necesita su
        # contenido real, no una fracción fija de la fila.
        fila.addLayout(col_der)

        # ── Caja de nota (tercera columna, al lado — solo visible si hay nota) ──
        self.panel_nota_proceso = QWidget()
        col_nota = QVBoxLayout(self.panel_nota_proceso)
        col_nota.setContentsMargins(0, 0, 0, 0)
        col_nota.setSpacing(2)
        lbl_nota_hdr = QLabel("📝 Nota:")
        lbl_nota_hdr.setObjectName("mutedInfoLabel")
        col_nota.addWidget(lbl_nota_hdr)
        self.txt_detalle_proceso_nota = QTextEdit()
        self.txt_detalle_proceso_nota.setReadOnly(True)
        self.txt_detalle_proceso_nota.setStyleSheet(
            "QTextEdit { background-color: #FFF8E1; color: #6d4c00; "
            "border: none; border-left: 3px solid #FF9800; padding: 4px 8px; }"
        )
        col_nota.addWidget(self.txt_detalle_proceso_nota)
        self.panel_nota_proceso.setVisible(False)
        fila.addWidget(self.panel_nota_proceso, 1)

        # fila con stretch=1: las cajas rellenan todo el alto restante del
        # panel hasta el final, en vez de quedar con un hueco muerto debajo.
        v.addLayout(fila, 1)

        return panel

    def refresh_processes(self):
        """Actualiza lista de procesos."""
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.process_notes = self.process_notes_db.get_all_notes()
        self.set_status("🔄 Escaneando procesos...")
        self.btn_refresh_processes.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.worker = ProcessCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_processes_data)
        self.worker.finished_error.connect(self._on_processes_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_processes_data(self, rows: List[ProcessRow]):
        """Procesa datos de procesos."""
        self.processes_model.setRows(rows)
        self.processes_model.setNotes(self.process_notes)
        self.btn_refresh_processes.setEnabled(True)
        self.progress_bar.setVisible(False)
        total = len(rows)
        running = sum(1 for r in rows if r.status == "running")
        self.set_status(f"✅ {total} procesos encontrados ({running} en ejecución)")
        self._filtrar_procesos()
        self._on_proceso_seleccionado()

    def _on_processes_error(self, msg: str):
        """Maneja errores de procesos."""
        self.btn_refresh_processes.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al escanear procesos")
        self._avisar("Error", f"No se pudieron obtener los procesos:\n\n{msg}", QMessageBox.Warning)

    def _filtrar_procesos(self):
        """Reaplica búsqueda + combo de estado + checkboxes sobre el proxy
        de Procesos. Conectado a todos los controles de filtrado del
        toolbar (ver crear_tab_processes)."""
        if not hasattr(self, 'processes_proxy'):
            return
        self.processes_proxy.setFilterRegExp(
            QRegExp(self.search_processes.text(), Qt.CaseInsensitive, QRegExp.FixedString))
        status_key = self.combo_filtro_procesos.currentData()
        self.processes_proxy.setStatusFilters(
            status_key, self.chk_solo_notas_procesos.isChecked(),
            self.chk_ocultar_win_procesos.isChecked(), self.process_notes,
        )
        self._actualizar_contador_procesos()

    def _actualizar_contador_procesos(self):
        if not hasattr(self, 'processes_model'):
            return
        total = self.processes_model.rowCount()
        visibles = self.processes_proxy.rowCount()
        running = sum(1 for r in self.processes_model.rows() if r.status == "running")
        self.lbl_count_procesos.setText(f"Mostrando {visibles} / {total}  |  {running} en ejecución")

    def _proceso_seleccionado(self) -> Optional[ProcessRow]:
        sel = self.processes_table.selectionModel()
        if not sel or not sel.hasSelection():
            return None
        idx = sel.selectedRows()[0]
        pid = self.processes_proxy.data(idx, Qt.UserRole)
        return self.processes_model.row_by_pid(pid)

    def _nota_de_proceso(self, name: str) -> Optional[str]:
        """Las notas se guardan por nombre de ejecutable (ver ProcessRow.__doc__),
        no por PID — todas las instancias de 'chrome.exe' comparten la misma nota."""
        for note_name, text in self.process_notes.items():
            if note_name.lower() == name.lower():
                return text
        return None

    def _on_proceso_seleccionado(self, *_args):
        """Refresca panel de detalle + habilita/deshabilita controles según
        haya o no un proceso seleccionado (mismo patrón que
        _on_servicio_seleccionado)."""
        row = self._proceso_seleccionado()
        habilitado = row is not None
        self.btn_nota_proceso.setEnabled(habilitado)
        self.combo_prioridad_procesos.setEnabled(habilitado)
        self.btn_aplicar_prioridad_proceso.setEnabled(habilitado)
        self.btn_borrar_nota_proceso.setVisible(habilitado and self._nota_de_proceso(row.name) is not None)

        if row is None:
            self.lbl_detalle_proceso_nombre.setText("Selecciona un proceso para ver el detalle")
            self.txt_detalle_proceso_ruta.clear()
            self.lbl_detalle_proceso_usuario.setText("Usuario: —")
            self.lbl_detalle_proceso_pid.setText("PID: —")
            self.lbl_detalle_proceso_hijos.setText("ninguno")
            self.panel_nota_proceso.setVisible(False)
        else:
            self.lbl_detalle_proceso_nombre.setText(f"{row.name}  [PID {row.pid}]")
            self.txt_detalle_proceso_ruta.setPlainText(row.exe_path or "(sin acceso a la ruta)")
            self.lbl_detalle_proceso_usuario.setText(f"Usuario: {row.username or '-'}")
            ppid_text = f" | PID padre: {row.ppid}" if row.ppid else ""
            self.lbl_detalle_proceso_pid.setText(f"PID: {row.pid}{ppid_text}")
            self.lbl_detalle_proceso_hijos.setText(", ".join(row.children) if row.children else "ninguno")
            idx_prioridad = self.combo_prioridad_procesos.findData(row.priority)
            if idx_prioridad >= 0:
                self.combo_prioridad_procesos.setCurrentIndex(idx_prioridad)
            nota = self._nota_de_proceso(row.name)
            if nota:
                self.txt_detalle_proceso_nota.setPlainText(nota)
                self.panel_nota_proceso.setVisible(True)
            else:
                self.panel_nota_proceso.setVisible(False)

        self._actualizar_botones_accion_proceso(row)

    def _aplicar_prioridad_proceso(self):
        row = self._proceso_seleccionado()
        if not row:
            return
        nueva_prioridad = self.combo_prioridad_procesos.currentData()
        if nueva_prioridad == "realtime":
            if not self._confirmar_accion("Confirmar prioridad Tiempo real",
                "La prioridad <b>Tiempo real</b> puede dejar sin respuesta el teclado, "
                f"el ratón u otros procesos del sistema.<br><br>¿Aplicar de todas formas a «{row.name}»?",
                QMessageBox.Warning):
                return
        ok, msg = self.process_manager.set_priority(row.pid, nueva_prioridad)
        self._registrar_historial("⚙️ Procesos", "Cambiar prioridad", f"{row.name} (PID {row.pid}) → {nueva_prioridad}", ok, msg)
        if ok:
            etiqueta = PROCESS_PRIORITY_LABELS.get(nueva_prioridad, nueva_prioridad)
            self.set_status(f"✅ Prioridad de «{row.name}» (PID {row.pid}) cambiada a '{etiqueta}'")
            self.refresh_processes()
        else:
            self._avisar("Error", f"No se pudo cambiar la prioridad:\n{msg}", QMessageBox.Warning)

    def _editar_nota_proceso(self):
        row = self._proceso_seleccionado()
        if not row:
            return
        actual = self._nota_de_proceso(row.name) or ""
        texto, ok = self._editar_texto_multilinea(
            f"Nota — {row.name}", "Nota de este proceso (se aplica a todas sus instancias):", actual)
        if not ok:
            return
        texto = texto.strip()
        if texto:
            self.process_notes_db.save_note(row.name, texto)
            self.process_notes[row.name] = texto
        else:
            self.process_notes_db.delete_note(row.name)
            self.process_notes.pop(row.name, None)
        self.processes_model.setNotes(self.process_notes)
        self._on_proceso_seleccionado()
        self._filtrar_procesos()

    def _borrar_nota_proceso(self):
        row = self._proceso_seleccionado()
        if not row:
            return
        if not self._confirmar_accion("Borrar nota",
                f"¿Borrar la nota del proceso «{row.name}»?"):
            return
        self.process_notes_db.delete_note(row.name)
        self.process_notes.pop(row.name, None)
        self.processes_model.setNotes(self.process_notes)
        self._on_proceso_seleccionado()
        self._filtrar_procesos()
        self.set_status(f"✅ Nota eliminada de «{row.name}»")

    def _accion_proceso(self, row: ProcessRow, accion: str):
        """accion: 'kill'|'suspend'|'resume'. Único punto de verdad, usado
        tanto desde el menú contextual como desde los botones de la fila 2
        (ver _accion_proceso_toolbar). Llamada síncrona: psutil es rápido y
        local, no justifica un QThread propio (mismo criterio que
        _accion_servicio)."""
        etiquetas = {"kill": "Finalizar", "suspend": "Suspender", "resume": "Reanudar"}
        etiqueta = etiquetas[accion]
        texto = f"¿{etiqueta} el proceso «{row.name}» (PID {row.pid})?"
        if accion == "kill" and row.name.lower() in _CRITICAL_PROCESSES:
            texto += ("\n\n⚠ Este es un proceso crítico del sistema. Finalizarlo puede "
                      "provocar un bloqueo, cierre de sesión o reinicio inesperado.")
        if not self._confirmar_accion(f"{etiqueta} proceso", texto):
            return
        self.set_status(f"🔄 {_GERUNDIO.get(etiqueta, etiqueta)} proceso {row.name} (PID {row.pid})...")
        QApplication.processEvents()
        metodo = {
            "kill": self.process_manager.kill_process,
            "suspend": self.process_manager.suspend_process,
            "resume": self.process_manager.resume_process,
        }[accion]
        ok, msg = metodo(row.pid)
        self._registrar_historial("⚙️ Procesos", f"{etiqueta} proceso", f"{row.name} (PID {row.pid})", ok, msg)
        if ok:
            self.set_status(f"✅ Proceso {row.name} (PID {row.pid}) — {etiqueta} completado")
            self.refresh_processes()
        else:
            self._avisar("Error", f"No se pudo {etiqueta.lower()} el proceso.\n\n{msg}", QMessageBox.Warning)

    def _accion_proceso_toolbar(self, accion: str):
        """Finalizar/Suspender/Reanudar desde los botones pequeños de la fila
        de herramientas de la pestaña Procesos (junto a Nota). Actúa sobre
        la fila seleccionada en self.processes_table."""
        row = self._proceso_seleccionado()
        if not row:
            self._avisar("Procesos", "Selecciona antes un proceso en la tabla.")
            return
        self._accion_proceso(row, accion)

    def _actualizar_botones_accion_proceso(self, row: Optional[ProcessRow]):
        """Habilita/deshabilita Finalizar/Suspender/Reanudar según el estado
        real del proceso seleccionado. 'stopped' es el estado real que
        informa psutil para un proceso suspendido en Windows (ver
        PROCESS_STATUS_LABELS)."""
        if not hasattr(self, 'btn_proceso_finalizar'):
            return
        habilitado = row is not None
        if not habilitado:
            self.btn_proceso_finalizar.setEnabled(False)
            self.btn_proceso_suspender.setEnabled(False)
            self.btn_proceso_reanudar.setEnabled(False)
            return
        alive = row.status not in ("dead", "zombie")
        suspendido = row.status in ("stopped", "suspended")
        self.btn_proceso_finalizar.setEnabled(alive)
        self.btn_proceso_suspender.setEnabled(alive and not suspendido)
        self.btn_proceso_reanudar.setEnabled(alive and suspendido)

    def _ctx_procesos(self, pos):
        idx = self.processes_table.indexAt(pos)
        if not idx.isValid():
            return
        self.processes_table.selectRow(idx.row())
        row = self._proceso_seleccionado()
        if not row:
            return
        ruta = row.exe_path

        menu = QMenu(self)
        act_kill    = menu.addAction("✖ Finalizar")
        act_suspend = menu.addAction("⏸ Suspender")
        act_resume  = menu.addAction("▶ Reanudar")
        alive = row.status not in ("dead", "zombie")
        suspendido = row.status in ("stopped", "suspended")
        act_kill.setEnabled(alive)
        act_suspend.setEnabled(alive and not suspendido)
        act_resume.setEnabled(alive and suspendido)
        menu.addSeparator()
        act_nota = menu.addAction("📝 Editar nota")
        act_borrar_nota = menu.addAction("✕ Borrar nota") if self._nota_de_proceso(row.name) is not None else None
        menu.addSeparator()
        act_loc  = menu.addAction("📂 Abrir ubicación del ejecutable")
        act_hash = menu.addAction("🔑 Ver hash SHA-256 del ejecutable")
        act_sig  = menu.addAction("🔏 Verificar firma digital")
        menu.addSeparator()
        act_copy = menu.addAction("📋 Copiar fila")

        action = menu.exec_(self.processes_table.viewport().mapToGlobal(pos))

        if action == act_kill:
            self._accion_proceso(row, "kill")
        elif action == act_suspend:
            self._accion_proceso(row, "suspend")
        elif action == act_resume:
            self._accion_proceso(row, "resume")
        elif action == act_nota:
            self._editar_nota_proceso()
        elif act_borrar_nota is not None and action == act_borrar_nota:
            self._borrar_nota_proceso()

        elif action == act_loc:
            if ruta:
                import os as _os
                folder = _os.path.dirname(ruta)
                subprocess.Popen(["explorer", folder])
            else:
                self._avisar("Ubicación", "Ruta no disponible para este proceso.")

        elif action == act_hash:
            if not ruta:
                self._avisar("Hash", "Ruta no disponible para calcular hash.")
                return
            self.set_status("🔄 Calculando hash...")
            ruta_escaped = escape_powershell_string(ruta)
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-FileHash -Path '{ruta_escaped}' -Algorithm SHA256 -ErrorAction SilentlyContinue).Hash"],
                capture_output=True, text=True, timeout=15,
                startupinfo=self._get_startupinfo()
            )
            hash_val = out.stdout.strip() or "No se pudo calcular"
            self._avisar(f"SHA-256 — {row.name}", f"Archivo: {ruta}\n\nSHA-256:\n{hash_val}")
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
            self._avisar(f"Firma digital — {row.name}", f"Archivo: {ruta}\n\nEstado: {estado}\nFirmante: {firmante}")
            self.set_status(f"✅ Firma digital: {estado}")

        elif action == act_copy:
            cols = [self.processes_proxy.data(self.processes_proxy.index(idx.row(), c), Qt.DisplayRole)
                    for c in range(len(ProcessTableModel.HEADERS))]
            QApplication.clipboard().setText("\t".join(str(v or "") for v in cols))

    def copy_processes_selection(self):
        """Copia procesos seleccionados al portapapeles."""
        selected_indexes = self.processes_table.selectionModel().selectedRows()
        if not selected_indexes:
            self._avisar("Copiar", "No hay procesos seleccionados.")
            return

        lines = []
        lines.append("=" * 80)
        lines.append("PROCESOS")
        lines.append("=" * 80)
        lines.append("")

        for index in selected_indexes:
            row = index.row()
            nombre = str(self.processes_proxy.data(self.processes_proxy.index(row, 0), Qt.DisplayRole) or "")
            pid = str(self.processes_proxy.data(self.processes_proxy.index(row, 1), Qt.DisplayRole) or "")
            memoria = str(self.processes_proxy.data(self.processes_proxy.index(row, 2), Qt.DisplayRole) or "")
            cpu = str(self.processes_proxy.data(self.processes_proxy.index(row, 3), Qt.DisplayRole) or "")

            lines.append(f"{nombre} (PID: {pid})")
            lines.append(f"  Memoria: {memoria} MB | CPU: {cpu}")
            lines.append("")

        lines.append("=" * 80)

        text = "\n".join(lines)
        QApplication.clipboard().setText(text)
        self.set_status(f"✅ Copiados {len(selected_indexes)} procesos al portapapeles")

