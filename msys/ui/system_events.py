# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña Eventos Sistema: tabla, reconstruccion de vista, detalle de evento, filtro.
Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).

Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

import re
import subprocess
from html import escape as html_escape

from PyQt5.QtCore import Qt, QRegExp
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QCheckBox, QSpinBox, QPushButton, QMessageBox, QDialog, QTextEdit, QTableView,
)

from msys.core.utils import is_admin
from msys.models.rows import SystemEventRow
from msys.models.table_models import SystemEventTableModel, SystemEventFilterProxy
from msys.widgets.delegates import _setup_header
from msys.collectors import SystemEventCollector


class EventosSistemaMixin:
    def crear_tab_eventos_sistema(self):
        """Crea la pestaña de eventos del sistema (últimos 7 días)."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        # Barra de búsqueda y acciones — mismo patrón que Sistema/
        # Actualizaciones/Controladores: búsqueda a la izquierda, hueco
        # elástico, botón principal a la derecha.
        actions = QHBoxLayout()
        self.search_eventos = QLineEdit()
        self.search_eventos.setPlaceholderText("🔎 Buscar por fuente, mensaje, ID...")
        self.search_eventos.textChanged.connect(lambda txt: (
            self.eventos_proxy.setFilterRegExp(QRegExp(txt, Qt.CaseInsensitive, QRegExp.FixedString)),
            self._update_eventos_count()
        ))
        actions.addWidget(self.search_eventos, 3)

        lbl_excluir = QLabel("Excluir fuentes:")
        lbl_excluir.setObjectName("fieldLabel")
        actions.addWidget(lbl_excluir)
        self.txt_ev_excluir_fuentes = QLineEdit()
        self.txt_ev_excluir_fuentes.setPlaceholderText("Nombres separados por coma, p. ej.: MouseWithoutBordersHelper, VSS")
        self.txt_ev_excluir_fuentes.textChanged.connect(self._reconstruir_vista_eventos)
        actions.addWidget(self.txt_ev_excluir_fuentes, 2)

        actions.addStretch()

        self.btn_abrir_eventvwr = QPushButton("🗂️ Abrir Visor de eventos")
        self.btn_abrir_eventvwr.setObjectName("ghost")
        self.btn_abrir_eventvwr.clicked.connect(lambda: subprocess.Popen(
            ["powershell", "-Command", "Start-Process eventvwr.msc"],
            startupinfo=self._get_startupinfo()
        ))
        actions.addWidget(self.btn_abrir_eventvwr)

        self.btn_refresh_eventos = QPushButton("🔄 Actualizar")
        self.btn_refresh_eventos.setObjectName("primary")
        self.btn_refresh_eventos.clicked.connect(self.refresh_eventos_sistema)
        actions.addWidget(self.btn_refresh_eventos)

        layout.addLayout(actions)

        # Filtros de nivel — debajo de la búsqueda
        filters_row = QHBoxLayout()
        filters_row.setSpacing(20)
        lbl_nivel = QLabel("Nivel:")
        lbl_nivel.setObjectName("fieldLabel")
        filters_row.addWidget(lbl_nivel)
        self.chk_ev_critico = QCheckBox("🔴 Crítico")
        self.chk_ev_critico.setChecked(True)
        self.chk_ev_critico.stateChanged.connect(self._apply_eventos_filter)
        filters_row.addWidget(self.chk_ev_critico)
        self.chk_ev_error = QCheckBox("❌ Error")
        self.chk_ev_error.setChecked(True)
        self.chk_ev_error.stateChanged.connect(self._apply_eventos_filter)
        filters_row.addWidget(self.chk_ev_error)
        self.chk_ev_advertencia = QCheckBox("⚠️ Advertencia")
        self.chk_ev_advertencia.setChecked(True)
        self.chk_ev_advertencia.stateChanged.connect(self._apply_eventos_filter)
        filters_row.addWidget(self.chk_ev_advertencia)

        filters_row.addSpacing(30)
        lbl_log = QLabel("Log:")
        lbl_log.setObjectName("fieldLabel")
        filters_row.addWidget(lbl_log)
        self.chk_ev_application = QCheckBox("Application")
        self.chk_ev_application.setChecked(True)
        self.chk_ev_application.stateChanged.connect(self._apply_eventos_filter)
        filters_row.addWidget(self.chk_ev_application)
        self.chk_ev_system = QCheckBox("System")
        self.chk_ev_system.setChecked(True)
        self.chk_ev_system.stateChanged.connect(self._apply_eventos_filter)
        filters_row.addWidget(self.chk_ev_system)

        self.chk_ev_security = QCheckBox("Security")
        es_admin = is_admin()
        self.chk_ev_security.setEnabled(es_admin)
        self.chk_ev_security.setChecked(False)
        if es_admin:
            self.chk_ev_security.setToolTip(
                "Solo fallos de auditoría (inicios de sesión denegados, accesos "
                "rechazados...) — el log de Seguridad no usa Crítico/Error/"
                "Advertencia, así que se filtra distinto y siempre se trata como "
                "Advertencia. Aplica al pulsar Actualizar.")
        else:
            self.chk_ev_security.setToolTip(
                "Requiere ejecutar MASys como administrador para leer el "
                "registro de Seguridad de Windows.")
        self.chk_ev_security.stateChanged.connect(self._apply_eventos_filter)
        filters_row.addWidget(self.chk_ev_security)

        filters_row.addSpacing(30)
        lbl_dias = QLabel("Días:")
        lbl_dias.setObjectName("fieldLabel")
        filters_row.addWidget(lbl_dias)
        self.spin_eventos_dias = QSpinBox()
        self.spin_eventos_dias.setRange(1, 90)
        self.spin_eventos_dias.setValue(7)
        self.spin_eventos_dias.setFixedWidth(60)
        self.spin_eventos_dias.setToolTip("Rango de días a consultar (aplica al pulsar Actualizar)")
        filters_row.addWidget(self.spin_eventos_dias)

        filters_row.addSpacing(30)
        self.chk_ev_agrupar = QCheckBox("Agrupar repetidos")
        self.chk_ev_agrupar.setToolTip(
            "Colapsa eventos idénticos repetidos (misma fuente + ID) en una "
            "sola fila con un contador (×N), mostrando el más reciente. No "
            "cambia el total recogido, solo cómo se muestra.")
        self.chk_ev_agrupar.stateChanged.connect(self._reconstruir_vista_eventos)
        filters_row.addWidget(self.chk_ev_agrupar)

        filters_row.addStretch()
        layout.addLayout(filters_row)

        self.lbl_eventos_count = QLabel("Eventos: 0")
        self.lbl_eventos_count.setObjectName("countLabel")
        layout.addWidget(self.lbl_eventos_count)

        self.eventos_model = SystemEventTableModel([])
        self.eventos_proxy = SystemEventFilterProxy(self)
        self.eventos_proxy.setSourceModel(self.eventos_model)
        self.eventos_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.eventos_proxy.setFilterKeyColumn(-1)

        self.eventos_table = QTableView()
        self.eventos_table.setModel(self.eventos_proxy)
        self.eventos_table.setAlternatingRowColors(True)
        self.eventos_table.setSelectionBehavior(QTableView.SelectRows)
        self.eventos_table.verticalHeader().setVisible(False)
        self.eventos_table.setSortingEnabled(True)
        self.eventos_table.doubleClicked.connect(self._mostrar_evento_completo)

        _setup_header(self.eventos_table, 6, stretch_col=5)

        layout.addWidget(self.eventos_table)

        self._eventos_todos = []  # filas crudas sin agrupar/excluir, para poder reconstruir la vista

        return tab

    def refresh_eventos_sistema(self):
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        dias = self.spin_eventos_dias.value()
        incluir_security = self.chk_ev_security.isEnabled() and self.chk_ev_security.isChecked()
        self.set_status(f"🔄 Consultando eventos de los últimos {dias} días...")
        self.btn_refresh_eventos.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.worker = SystemEventCollector(dias=dias, incluir_security=incluir_security)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_eventos_data)
        self.worker.finished_error.connect(self._on_eventos_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_eventos_data(self, rows):
        self._eventos_todos = rows
        self.btn_refresh_eventos.setEnabled(True)
        self.progress_bar.setVisible(False)
        self._reconstruir_vista_eventos()
        self.set_status(f"✅ {len(rows)} eventos del sistema cargados")

    def _on_eventos_error(self, msg):
        self.btn_refresh_eventos.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al consultar eventos del sistema")
        self._avisar("Error", f"No se pudieron obtener los eventos del sistema:\n\n{msg}", QMessageBox.Warning)

    def _reconstruir_vista_eventos(self):
        """Reconstruye las filas mostradas a partir de self._eventos_todos
        (los eventos crudos tal cual llegaron): quita las fuentes excluidas
        y, si 'Agrupar repetidos' está activo, colapsa las filas que
        comparten fuente+ID+nivel en una sola con un contador (×N),
        mostrando la más reciente. Se llama al cargar datos nuevos y cada
        vez que cambian la exclusión o el agrupado — los checkboxes de
        Nivel/Log y el buscador siguen aplicándose después, como filtro del
        proxy, sin verse afectados por esto."""
        excluidas = {
            n.strip().lower() for n in self.txt_ev_excluir_fuentes.text().split(',') if n.strip()
        }
        base = [r for r in self._eventos_todos if r.fuente.strip().lower() not in excluidas]

        if not self.chk_ev_agrupar.isChecked():
            self.eventos_model.setRows(base)
        else:
            grupos = {}
            orden = []
            for row in base:
                clave = (row.fuente, row.id_evento, row.nivel)
                if clave not in grupos:
                    grupos[clave] = []
                    orden.append(clave)
                grupos[clave].append(row)

            agrupadas = []
            for clave in orden:
                filas_grupo = grupos[clave]
                representativo = filas_grupo[0]  # PowerShell ya las entrega de más reciente a más antigua
                n = len(filas_grupo)
                fuente_mostrada = representativo.fuente if n == 1 else f"{representativo.fuente} (×{n})"
                agrupadas.append(SystemEventRow(
                    fecha=representativo.fecha,
                    nivel=representativo.nivel,
                    log=representativo.log,
                    fuente=fuente_mostrada,
                    id_evento=representativo.id_evento,
                    mensaje=representativo.mensaje,
                ))
            self.eventos_model.setRows(agrupadas)

        self._apply_eventos_filter()

    def _mostrar_evento_completo(self, index):
        """Doble clic sobre una fila: diálogo con el mensaje completo (el
        de la tabla se corta visualmente y, agrupado, solo se ve el más
        reciente de los ×N)."""
        source_index = self.eventos_proxy.mapToSource(index)
        row = self.eventos_model.rows()[source_index.row()]

        m = re.match(r'^(.*) \(×(\d+)\)$', row.fuente)
        fuente = m.group(1) if m else row.fuente
        nota_grupo = f"\n\nMostrando el más reciente de {m.group(2)} apariciones agrupadas." if m else ""

        dlg = QDialog(self)
        dlg.setWindowTitle(f"Evento — {fuente} (ID {row.id_evento})")
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dlg.setMinimumSize(640, 420)
        dlg_layout = QVBoxLayout(dlg)

        info = QLabel(
            f"<b>Fecha:</b> {html_escape(row.fecha)} &nbsp;|&nbsp; "
            f"<b>Nivel:</b> {html_escape(row.nivel)} &nbsp;|&nbsp; "
            f"<b>Log:</b> {html_escape(row.log)} &nbsp;|&nbsp; "
            f"<b>Fuente:</b> {html_escape(fuente)} &nbsp;|&nbsp; "
            f"<b>ID:</b> {html_escape(row.id_evento)}")
        info.setTextFormat(Qt.RichText)
        info.setWordWrap(True)
        dlg_layout.addWidget(info)

        txt = QTextEdit()
        txt.setReadOnly(True)
        txt.setPlainText(row.mensaje + nota_grupo)
        dlg_layout.addWidget(txt)

        btn_row = QHBoxLayout()
        btn_copiar = QPushButton("📋 Copiar mensaje")
        btn_copiar.clicked.connect(lambda: QApplication.clipboard().setText(row.mensaje))
        btn_row.addWidget(btn_copiar)
        btn_row.addStretch()
        btn_cerrar = QPushButton("Cerrar")
        btn_cerrar.setObjectName("primary")
        btn_cerrar.clicked.connect(dlg.accept)
        btn_row.addWidget(btn_cerrar)
        dlg_layout.addLayout(btn_row)

        dlg.exec_()

    def _apply_eventos_filter(self):
        levels = set()
        if self.chk_ev_critico.isChecked(): levels.add("critico")
        if self.chk_ev_error.isChecked(): levels.add("error")
        if self.chk_ev_advertencia.isChecked(): levels.add("advertencia")
        logs = set()
        if self.chk_ev_application.isChecked(): logs.add("application")
        if self.chk_ev_system.isChecked(): logs.add("system")
        if self.chk_ev_security.isChecked(): logs.add("security")

        # El proxy interpreta un set VACÍO como "sin filtro, mostrar todo"
        # (útil como valor inicial sin inicializar) — pero si el usuario
        # desmarca las tres casillas de Nivel (o las tres de Log) a
        # propósito, la intención es "no quiero ver nada de esto", no "muéstralo
        # todo". Un valor que nunca coincide con ningún nivel/log real fuerza
        # el resultado vacío que el usuario espera en ese caso.
        if not levels:
            levels = {"__ninguno_seleccionado__"}
        if not logs:
            logs = {"__ninguno_seleccionado__"}

        self.eventos_proxy.setFilters(levels, logs)
        self._update_eventos_count()

    def _update_eventos_count(self):
        self.lbl_eventos_count.setText(f"Eventos: {self.eventos_proxy.rowCount()}")

