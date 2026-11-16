# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: pestaña Variables de Entorno (tabla, panel de
detalle/notas, alta/edicion/borrado via VarManager, desglose y busqueda en
PATH, backup de registro antes de escribir).
Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).

Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

import os
import re
import time
import subprocess
from typing import List, Optional, Tuple

from PyQt5.QtCore import Qt, QRegExp
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QCheckBox, QPushButton, QTableView, QSplitter, QFrame, QTextEdit,
    QDialog, QDialogButtonBox, QFormLayout, QLineEdit, QMenu, QMessageBox,
)

from msys.config import logger
from msys.core.utils import is_admin, ruta_guardado_predeterminada, dialogo_guardar_archivo
from msys.domain.env_vars import EnvVarRow, _PATH_LIKE_VARS, _expandir_valor_env
from msys.models.table_models import EnvVarTableModel, EnvVarFilterProxy, _ENV_SCOPE_LABELS
from msys.widgets.delegates import _setup_header
from msys.collectors import EnvironmentVarCollector


class VariablesEntornoMixin:
    def crear_tab_environment_vars(self):
        """Crea la pestaña de variables de entorno — mismo tratamiento que
        Servicios/Procesos: lectura real (registro vía VarManager, no el
        entorno fusionado del proceso que heredó PowerShell), detección de
        problemas (rutas rotas, solapes, posible secuestro de PATH...),
        notas, panel de detalle con caja de nota al lado."""
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

        self.search_env_vars = QLineEdit()
        self.search_env_vars.setPlaceholderText("🔎 Buscar por nombre o valor...")
        self.search_env_vars.textChanged.connect(self._filtrar_env_vars)
        toolbar.addWidget(self.search_env_vars, 3)

        self.combo_ambito_env_vars = QComboBox()
        for label, key in [("Todos", "all"), ("Usuario", "user"), ("Sistema", "system")]:
            self.combo_ambito_env_vars.addItem(label, key)
        self.combo_ambito_env_vars.currentIndexChanged.connect(self._filtrar_env_vars)
        toolbar.addWidget(self.combo_ambito_env_vars)

        self.chk_solo_problemas_env_vars = QCheckBox("Solo con problemas")
        self.chk_solo_problemas_env_vars.setToolTip(
            "Muestra solo variables con rutas rotas, solapes, PATH excesivo o posible secuestro")
        self.chk_solo_problemas_env_vars.stateChanged.connect(self._filtrar_env_vars)
        toolbar.addWidget(self.chk_solo_problemas_env_vars)

        self.chk_solo_notas_env_vars = QCheckBox("Solo notas")
        self.chk_solo_notas_env_vars.stateChanged.connect(self._filtrar_env_vars)
        toolbar.addWidget(self.chk_solo_notas_env_vars)

        self.lbl_count_env_vars = QLabel("Cargando...")
        self.lbl_count_env_vars.setObjectName("mutedInfoLabel")
        toolbar.addWidget(self.lbl_count_env_vars)

        toolbar.addStretch()

        self.btn_refresh_env_vars = QPushButton("🔄 Actualizar")
        self.btn_refresh_env_vars.setObjectName("primary")
        self.btn_refresh_env_vars.clicked.connect(self.refresh_environment_vars)
        toolbar.addWidget(self.btn_refresh_env_vars)

        layout.addLayout(toolbar)

        # ── Segunda fila: acciones sobre la variable seleccionada ────────
        row2 = QHBoxLayout()
        row2.setSpacing(8)

        self.btn_env_var_nueva = QPushButton("➕ Nueva variable")
        self.btn_env_var_nueva.setObjectName("ghost")
        self.btn_env_var_nueva.clicked.connect(self._nueva_env_var)
        row2.addWidget(self.btn_env_var_nueva)

        self.btn_env_var_editar = QPushButton("✏️ Editar valor")
        self.btn_env_var_editar.setObjectName("ghost")
        self.btn_env_var_editar.setEnabled(False)
        self.btn_env_var_editar.clicked.connect(self._editar_env_var)
        row2.addWidget(self.btn_env_var_editar)

        self.btn_env_var_eliminar = QPushButton("✖ Eliminar")
        self.btn_env_var_eliminar.setObjectName("ghost")
        self.btn_env_var_eliminar.setEnabled(False)
        self.btn_env_var_eliminar.clicked.connect(self._eliminar_env_var)
        row2.addWidget(self.btn_env_var_eliminar)

        row2.addSpacing(16)

        self.btn_nota_env_var = QPushButton("📝 Nota")
        self.btn_nota_env_var.setObjectName("ghost")
        self.btn_nota_env_var.setEnabled(False)
        self.btn_nota_env_var.clicked.connect(self._editar_nota_env_var)
        row2.addWidget(self.btn_nota_env_var)

        self.btn_borrar_nota_env_var = QPushButton("✕ Borrar nota")
        self.btn_borrar_nota_env_var.setObjectName("ghost")
        self.btn_borrar_nota_env_var.setVisible(False)
        self.btn_borrar_nota_env_var.clicked.connect(self._borrar_nota_env_var)
        row2.addWidget(self.btn_borrar_nota_env_var)

        row2.addSpacing(16)

        self.btn_env_var_desglose_path = QPushButton("📋 Ver PATH desglosado")
        self.btn_env_var_desglose_path.setObjectName("ghost")
        self.btn_env_var_desglose_path.clicked.connect(self._ver_path_desglosado)
        row2.addWidget(self.btn_env_var_desglose_path)

        self.btn_env_var_buscar_reemplazar = QPushButton("🔁 Buscar y reemplazar en PATH")
        self.btn_env_var_buscar_reemplazar.setObjectName("ghost")
        self.btn_env_var_buscar_reemplazar.clicked.connect(self._buscar_reemplazar_path)
        row2.addWidget(self.btn_env_var_buscar_reemplazar)

        row2.addStretch()
        layout.addLayout(row2)

        # ── Tabla + panel de detalle (splitter vertical) ─────────────────
        self.env_vars_model = EnvVarTableModel([])
        self.env_vars_proxy = EnvVarFilterProxy(self)
        self.env_vars_proxy.setSourceModel(self.env_vars_model)
        self.env_vars_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.env_vars_proxy.setFilterKeyColumn(-1)

        self.env_vars_table = QTableView()
        self.env_vars_table.setModel(self.env_vars_proxy)
        self.env_vars_table.setAlternatingRowColors(True)
        self.env_vars_table.setSelectionBehavior(QTableView.SelectRows)
        self.env_vars_table.setSelectionMode(QTableView.SingleSelection)
        self.env_vars_table.verticalHeader().setVisible(False)
        self.env_vars_table.setSortingEnabled(True)
        self.env_vars_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.env_vars_table.customContextMenuRequested.connect(self._ctx_env_vars)

        _setup_header(self.env_vars_table, len(EnvVarTableModel.HEADERS), stretch_col=EnvVarTableModel.COL_VALUE)
        self.env_vars_table.selectionModel().selectionChanged.connect(self._on_env_var_seleccionada)

        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self.env_vars_table)
        splitter.addWidget(self._crear_panel_detalle_env_var())
        splitter.setSizes([600, 170])

        layout.addWidget(splitter, 1)

        return tab

    def _crear_panel_detalle_env_var(self):
        """Panel de detalle bajo la tabla de Variables de entorno (valor
        completo, problemas detectados, nota) — mismo diseño que Servicios/
        Procesos: la nota vive en una tercera caja al lado, solo visible si
        existe, y las otras dos cajas se dimensionan a su contenido real."""
        panel = QFrame()
        panel.setFrameShape(QFrame.StyledPanel)
        v = QVBoxLayout(panel)
        v.setContentsMargins(8, 6, 8, 6)
        v.setSpacing(3)

        cabecera = QHBoxLayout()
        cabecera.setSpacing(8)
        self.lbl_detalle_env_var_nombre = QLabel("Selecciona una variable para ver el detalle")
        fuente = self.lbl_detalle_env_var_nombre.font()
        fuente.setBold(True)
        fuente.setPointSize(fuente.pointSize() + 1)
        self.lbl_detalle_env_var_nombre.setFont(fuente)
        self.lbl_detalle_env_var_nombre.setWordWrap(True)
        cabecera.addWidget(self.lbl_detalle_env_var_nombre, 1)
        lbl_valor_hdr = QLabel("Valor completo:")
        lbl_valor_hdr.setObjectName("mutedInfoLabel")
        cabecera.addWidget(lbl_valor_hdr)
        v.addLayout(cabecera)

        fila = QHBoxLayout()
        fila.setSpacing(10)

        self.txt_detalle_env_var_valor = QTextEdit()
        self.txt_detalle_env_var_valor.setReadOnly(True)
        fila.addWidget(self.txt_detalle_env_var_valor, 1)

        col_der = QVBoxLayout()
        col_der.setSpacing(1)
        self.lbl_detalle_env_var_ambito = QLabel("Ámbito: —")
        col_der.addWidget(self.lbl_detalle_env_var_ambito)
        lbl_problemas_hdr = QLabel("Problemas detectados:")
        lbl_problemas_hdr.setObjectName("mutedInfoLabel")
        col_der.addWidget(lbl_problemas_hdr)
        self.lbl_detalle_env_var_problemas = QLabel("ninguno")
        self.lbl_detalle_env_var_problemas.setWordWrap(True)
        self.lbl_detalle_env_var_problemas.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        col_der.addWidget(self.lbl_detalle_env_var_problemas, 1)
        fila.addLayout(col_der)

        # ── Caja de nota (tercera columna, al lado — solo visible si hay nota) ──
        self.panel_nota_env_var = QWidget()
        col_nota = QVBoxLayout(self.panel_nota_env_var)
        col_nota.setContentsMargins(0, 0, 0, 0)
        col_nota.setSpacing(2)
        lbl_nota_hdr = QLabel("📝 Nota:")
        lbl_nota_hdr.setObjectName("mutedInfoLabel")
        col_nota.addWidget(lbl_nota_hdr)
        self.txt_detalle_env_var_nota = QTextEdit()
        self.txt_detalle_env_var_nota.setReadOnly(True)
        self.txt_detalle_env_var_nota.setStyleSheet(
            "QTextEdit { background-color: #FFF8E1; color: #6d4c00; "
            "border: none; border-left: 3px solid #FF9800; padding: 4px 8px; }"
        )
        col_nota.addWidget(self.txt_detalle_env_var_nota)
        self.panel_nota_env_var.setVisible(False)
        fila.addWidget(self.panel_nota_env_var, 1)

        v.addLayout(fila, 1)

        return panel

    def refresh_environment_vars(self):
        """Actualiza lista de variables de entorno."""
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.env_var_notes = self.env_var_notes_db.get_all_notes()
        self.set_status("🔄 Leyendo variables de entorno...")
        self.btn_refresh_env_vars.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.worker = EnvironmentVarCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_env_vars_data)
        self.worker.finished_error.connect(self._on_env_vars_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_env_vars_data(self, rows: List[EnvVarRow]):
        """Procesa datos de variables de entorno."""
        self.env_vars_model.setRows(rows)
        self.env_vars_model.setNotes(self.env_var_notes)
        self.btn_refresh_env_vars.setEnabled(True)
        self.progress_bar.setVisible(False)
        total = len(rows)
        problemas = sum(1 for r in rows if r.severity != "ok")
        self.set_status(f"✅ {total} variables de entorno leídas ({problemas} con algo que revisar)")
        self._filtrar_env_vars()
        self._on_env_var_seleccionada()

    def _on_env_vars_error(self, msg: str):
        """Maneja errores de variables de entorno."""
        self.btn_refresh_env_vars.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al leer variables de entorno")
        self._avisar("Error", f"No se pudieron obtener las variables de entorno:\n\n{msg}", QMessageBox.Warning)

    def _filtrar_env_vars(self):
        """Reaplica búsqueda + combo de ámbito + checkboxes sobre el proxy
        de Variables de entorno."""
        if not hasattr(self, 'env_vars_proxy'):
            return
        self.env_vars_proxy.setFilterRegExp(
            QRegExp(self.search_env_vars.text(), Qt.CaseInsensitive, QRegExp.FixedString))
        scope_key = self.combo_ambito_env_vars.currentData()
        self.env_vars_proxy.setFilters(
            scope_key, self.chk_solo_problemas_env_vars.isChecked(),
            self.chk_solo_notas_env_vars.isChecked(), self.env_var_notes,
        )
        self._actualizar_contador_env_vars()

    def _actualizar_contador_env_vars(self):
        if not hasattr(self, 'env_vars_model'):
            return
        total = self.env_vars_model.rowCount()
        visibles = self.env_vars_proxy.rowCount()
        problemas = sum(1 for r in self.env_vars_model.rows() if r.severity != "ok")
        self.lbl_count_env_vars.setText(f"Mostrando {visibles} / {total}  |  {problemas} con algo que revisar")

    def _env_var_seleccionada(self) -> Optional[EnvVarRow]:
        sel = self.env_vars_table.selectionModel()
        if not sel or not sel.hasSelection():
            return None
        idx = sel.selectedRows()[0]
        name, scope = self.env_vars_proxy.data(idx, Qt.UserRole)
        return self.env_vars_model.row_by_key(name, scope)

    def _nota_de_env_var(self, name: str) -> Optional[str]:
        """Las notas se guardan por nombre (independiente del ámbito): una
        variable definida en Usuario y Sistema comparte la misma nota."""
        for note_name, text in self.env_var_notes.items():
            if note_name.lower() == name.lower():
                return text
        return None

    def _on_env_var_seleccionada(self, *_args):
        row = self._env_var_seleccionada()
        habilitado = row is not None
        self.btn_env_var_editar.setEnabled(habilitado)
        self.btn_env_var_eliminar.setEnabled(habilitado)
        self.btn_nota_env_var.setEnabled(habilitado)
        self.btn_borrar_nota_env_var.setVisible(habilitado and self._nota_de_env_var(row.name) is not None)

        if row is None:
            self.lbl_detalle_env_var_nombre.setText("Selecciona una variable para ver el detalle")
            self.txt_detalle_env_var_valor.clear()
            self.lbl_detalle_env_var_ambito.setText("Ámbito: —")
            self.lbl_detalle_env_var_problemas.setText("ninguno")
            self.panel_nota_env_var.setVisible(False)
        else:
            self.lbl_detalle_env_var_nombre.setText(row.name)
            self.txt_detalle_env_var_valor.setPlainText(row.value or "(vacío)")
            self.lbl_detalle_env_var_ambito.setText(f"Ámbito: {_ENV_SCOPE_LABELS.get(row.scope, row.scope)}")
            self.lbl_detalle_env_var_problemas.setText(
                "\n".join(f"• {i}" for i in row.issues) if row.issues else "ninguno")
            nota = self._nota_de_env_var(row.name)
            if nota:
                self.txt_detalle_env_var_nota.setPlainText(nota)
                self.panel_nota_env_var.setVisible(True)
            else:
                self.panel_nota_env_var.setVisible(False)

    # ── Notas ─────────────────────────────────────────────────────────────────

    def _editar_nota_env_var(self):
        row = self._env_var_seleccionada()
        if not row:
            return
        actual = self._nota_de_env_var(row.name) or ""
        texto, ok = self._editar_texto_multilinea(
            f"Nota — {row.name}", "Nota de esta variable (aplica a ambos ámbitos si existe en los dos):", actual)
        if not ok:
            return
        texto = texto.strip()
        if texto:
            self.env_var_notes_db.save_note(row.name, texto)
            self.env_var_notes[row.name] = texto
        else:
            self.env_var_notes_db.delete_note(row.name)
            self.env_var_notes.pop(row.name, None)
        self.env_vars_model.setNotes(self.env_var_notes)
        self._on_env_var_seleccionada()
        self._filtrar_env_vars()

    def _borrar_nota_env_var(self):
        row = self._env_var_seleccionada()
        if not row:
            return
        if not self._confirmar_accion("Borrar nota", f"¿Borrar la nota de «{row.name}»?"):
            return
        self.env_var_notes_db.delete_note(row.name)
        self.env_var_notes.pop(row.name, None)
        self.env_vars_model.setNotes(self.env_var_notes)
        self._on_env_var_seleccionada()
        self._filtrar_env_vars()
        self.set_status(f"✅ Nota eliminada de «{row.name}»")

    # ── Backup .reg ───────────────────────────────────────────────────────────

    def _ofrecer_backup_si_hace_falta(self) -> bool:
        """Antes de la primera edición/borrado de la sesión, ofrece un
        backup .reg restaurable con doble clic — tocar el registro es más
        delicado que reiniciar un servicio, así que aquí sí merece la pena
        el paso extra. Si el usuario ya lo hizo o rechaza expresamente
        seguir sin backup, no vuelve a insistir en la misma sesión.
        Devuelve False solo si el usuario cancela la operación por completo."""
        if self._env_var_backup_hecho:
            return True
        resp = QMessageBox(self)
        resp.setWindowFlags(resp.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        resp.setWindowTitle("Copia de seguridad recomendada")
        resp.setIcon(QMessageBox.Question)
        resp.setText(
            "Vas a modificar variables de entorno del registro.<br><br>"
            "¿Quieres generar antes una copia de seguridad (.reg) restaurable "
            "con doble clic?"
        )
        btn_si = resp.addButton("Sí, generar backup", QMessageBox.YesRole)
        btn_no = resp.addButton("Continuar sin backup", QMessageBox.NoRole)
        btn_cancelar = resp.addButton("Cancelar", QMessageBox.RejectRole)
        resp.exec_()
        clicked = resp.clickedButton()
        if clicked is btn_cancelar:
            return False
        if clicked is btn_si:
            self._exportar_backup_reg(manual=False)
            # 5.4.0: si se cancela el guardado, NO hay copia: no seguir como si la hubiera
            if not self._env_var_backup_hecho:
                return False
        self._env_var_backup_hecho = True
        return True

    def _exportar_backup_reg(self, manual: bool) -> None:
        default_name = f"variables_entorno_backup_{time.strftime('%Y%m%d_%H%M%S')}.reg"
        path = dialogo_guardar_archivo(
            self, "Guardar copia de seguridad", ruta_guardado_predeterminada(default_name),
            "Archivo de registro (*.reg)")
        if not path:
            return
        ok, msg = self.var_manager.export_reg_backup(path)
        if ok:
            self._env_var_backup_hecho = True
            self.set_status(f"✅ Backup guardado: {os.path.basename(path)}")
            if manual:
                self._avisar("Backup generado",
                    f"Copia de seguridad guardada en:\n\n{path}\n\n"
                    "Para restaurarla, haz doble clic sobre el archivo .reg.")
        else:
            self._avisar("Error al generar backup", f"No se pudo exportar:\n{msg}", QMessageBox.Warning)

    # ── Crear / editar / eliminar ─────────────────────────────────────────────

    def _dialogo_valor_env_var(self, titulo: str, nombre_inicial: str, valor_inicial: str,
                                 scope_inicial: str, nombre_editable: bool) -> Optional[Tuple[str, str, str]]:
        """Diálogo compartido para 'Nueva variable' y 'Editar valor'. Sin
        botón de ayuda '?' (mismo criterio que el resto de la app)."""
        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle(titulo)
        dialog.setMinimumWidth(520)
        form = QFormLayout(dialog)

        txt_nombre = QLineEdit(nombre_inicial)
        txt_nombre.setEnabled(nombre_editable)
        form.addRow("Nombre:", txt_nombre)

        txt_valor = QTextEdit()
        txt_valor.setPlainText(valor_inicial)
        txt_valor.setFixedHeight(90)
        form.addRow("Valor:", txt_valor)

        combo_scope = QComboBox()
        combo_scope.addItem("Usuario", "user")
        combo_scope.addItem("Sistema (requiere administrador)", "system")
        idx = combo_scope.findData(scope_inicial)
        if idx >= 0:
            combo_scope.setCurrentIndex(idx)
        combo_scope.setEnabled(nombre_editable)  # al editar una existente, el ámbito no cambia
        form.addRow("Ámbito:", combo_scope)

        botones = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        botones.accepted.connect(dialog.accept)
        botones.rejected.connect(dialog.reject)
        form.addRow(botones)

        if dialog.exec_() != QDialog.Accepted:
            return None
        nombre = txt_nombre.text().strip()
        valor = txt_valor.toPlainText()
        scope = combo_scope.currentData()
        if not nombre:
            self._avisar("Nombre vacío", "El nombre de la variable no puede estar vacío.", QMessageBox.Warning)
            return None
        return nombre, valor, scope

    def _nueva_env_var(self):
        datos = self._dialogo_valor_env_var("Nueva variable de entorno", "", "", "user", nombre_editable=True)
        if not datos:
            return
        nombre, valor, scope = datos
        if scope == "system" and not is_admin():
            self._avisar("Se requieren privilegios de administrador",
                "Crear una variable de Sistema requiere ejecutar MASys como administrador.",
                QMessageBox.Warning)
            return
        if not self._ofrecer_backup_si_hace_falta():
            return
        ok, msg = self.var_manager.set_value(nombre, valor, scope)
        if ok:
            self.env_var_log_db.add_entry(nombre, scope, "crear", "", valor)
            self.set_status(f"✅ Variable «{nombre}» creada en {_ENV_SCOPE_LABELS[scope]}")
            self.refresh_environment_vars()
        else:
            self._avisar("Error", f"No se pudo crear la variable:\n{msg}", QMessageBox.Warning)

    def _editar_env_var(self):
        row = self._env_var_seleccionada()
        if not row:
            return
        if row.scope == "system" and not is_admin():
            self._avisar("Se requieren privilegios de administrador",
                "Editar una variable de Sistema requiere ejecutar MASys como administrador.",
                QMessageBox.Warning)
            return
        datos = self._dialogo_valor_env_var(
            f"Editar «{row.name}» ({_ENV_SCOPE_LABELS.get(row.scope, row.scope)})",
            row.name, row.value, row.scope, nombre_editable=False)
        if not datos:
            return
        _nombre, valor_nuevo, _scope = datos
        if valor_nuevo == row.value:
            return
        if not self._ofrecer_backup_si_hace_falta():
            return
        ok, msg = self.var_manager.set_value(row.name, valor_nuevo, row.scope)
        if ok:
            self.env_var_log_db.add_entry(row.name, row.scope, "editar", row.value, valor_nuevo)
            self.set_status(f"✅ «{row.name}» actualizada")
            self.refresh_environment_vars()
        else:
            self._avisar("Error", f"No se pudo actualizar la variable:\n{msg}", QMessageBox.Warning)

    def _eliminar_env_var(self):
        row = self._env_var_seleccionada()
        if not row:
            return
        if row.scope == "system" and not is_admin():
            self._avisar("Se requieren privilegios de administrador",
                "Eliminar una variable de Sistema requiere ejecutar MASys como administrador.",
                QMessageBox.Warning)
            return
        if not self._confirmar_accion("Eliminar variable",
                f"¿Eliminar «{row.name}» del ámbito {_ENV_SCOPE_LABELS.get(row.scope, row.scope)}?",
                QMessageBox.Warning):
            return
        if not self._ofrecer_backup_si_hace_falta():
            return
        ok, msg = self.var_manager.delete_value(row.name, row.scope)
        if ok:
            self.env_var_log_db.add_entry(row.name, row.scope, "eliminar", row.value, "")
            self.set_status(f"✅ «{row.name}» eliminada de {_ENV_SCOPE_LABELS.get(row.scope, row.scope)}")
            self.refresh_environment_vars()
        else:
            self._avisar("Error", f"No se pudo eliminar la variable:\n{msg}", QMessageBox.Warning)

    # ── PATH: desglose y buscar/reemplazar ─────────────────────────────────────

    def _ver_path_desglosado(self):
        row = self._env_var_seleccionada()
        nombre_upper = row.name.upper() if row else ""
        if not row or nombre_upper not in _PATH_LIKE_VARS:
            self._avisar("Ver PATH desglosado",
                "Selecciona primero una variable tipo PATH/PATHEXT/PSModulePath.")
            return
        entradas = [p for p in row.value.split(";") if p.strip()]
        vars_conocidas = {**os.environ}
        lineas = []
        for entrada in entradas:
            expandida = _expandir_valor_env(entrada.strip().strip('"'), vars_conocidas)
            existe = "✓" if os.path.exists(expandida) else "✗"
            lineas.append(f"{existe}  {entrada}")
        texto = "\n".join(lineas) if lineas else "(vacío)"

        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle(f"Desglose de {row.name} — {_ENV_SCOPE_LABELS.get(row.scope, row.scope)}")
        dialog.setMinimumSize(600, 420)
        v = QVBoxLayout(dialog)
        txt = QTextEdit()
        txt.setReadOnly(True)
        txt.setPlainText(texto)
        txt.setFontFamily("Consolas")
        v.addWidget(txt)
        botones = QDialogButtonBox(QDialogButtonBox.Close)
        botones.rejected.connect(dialog.reject)
        botones.accepted.connect(dialog.accept)
        v.addWidget(botones)
        dialog.exec_()

    def _buscar_reemplazar_path(self):
        row = self._env_var_seleccionada()
        nombre_upper = row.name.upper() if row else ""
        if not row or nombre_upper not in _PATH_LIKE_VARS:
            self._avisar("Buscar y reemplazar en PATH",
                "Selecciona primero una variable tipo PATH/PATHEXT/PSModulePath.")
            return
        if row.scope == "system" and not is_admin():
            self._avisar("Se requieren privilegios de administrador",
                "Modificar el PATH de Sistema requiere ejecutar MASys como administrador.",
                QMessageBox.Warning)
            return

        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle(f"Buscar y reemplazar en {row.name} ({_ENV_SCOPE_LABELS.get(row.scope, row.scope)})")
        dialog.setMinimumWidth(480)
        form = QFormLayout(dialog)
        txt_buscar = QLineEdit()
        txt_buscar.setPlaceholderText(r"Ej: D:\Herramientas")
        form.addRow("Buscar:", txt_buscar)
        txt_reemplazar = QLineEdit()
        txt_reemplazar.setPlaceholderText(r"Ej: E:\Herramientas")
        form.addRow("Reemplazar por:", txt_reemplazar)
        botones = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        botones.accepted.connect(dialog.accept)
        botones.rejected.connect(dialog.reject)
        form.addRow(botones)
        if dialog.exec_() != QDialog.Accepted:
            return
        buscar = txt_buscar.text().strip()
        reemplazar = txt_reemplazar.text().strip()
        if not buscar:
            return

        entradas = [p for p in row.value.split(";")]
        afectadas = [p for p in entradas if buscar.lower() in p.lower()]
        if not afectadas:
            self._avisar("Sin coincidencias", f"Ningún segmento de {row.name} contiene «{buscar}».")
            return

        # 5.4.0: la búsqueda ya era sin distinguir mayúsculas, pero el reemplazo sí las distinguía:
        # con «d:\herramientas» frente a «D:\Herramientas» decía «1 entrada», no cambiaba nada y lo
        # apuntaba en el historial como cambio. Ahora reemplaza igual que busca.
        patron = re.compile(re.escape(buscar), re.IGNORECASE)
        nuevas = [patron.sub(lambda _m: reemplazar, p) for p in entradas]
        resumen = "\n".join(f"  {old}  →  {new}" for old, new in zip(entradas, nuevas) if old != new)
        if not self._confirmar_accion("Confirmar reemplazo",
                f"Se van a modificar {len(afectadas)} entrada(s) de «{row.name}»:\n\n{resumen}\n\n¿Continuar?"):
            return
        if not self._ofrecer_backup_si_hace_falta():
            return

        valor_nuevo = ";".join(nuevas)
        ok, msg = self.var_manager.set_value(row.name, valor_nuevo, row.scope)
        if ok:
            self.env_var_log_db.add_entry(row.name, row.scope, "buscar_reemplazar", row.value, valor_nuevo)
            self.set_status(f"✅ {len(afectadas)} entrada(s) actualizadas en «{row.name}»")
            self.refresh_environment_vars()
        else:
            self._avisar("Error", f"No se pudo aplicar el reemplazo:\n{msg}", QMessageBox.Warning)

    # ── Menú contextual ────────────────────────────────────────────────────────

    def _ctx_env_vars(self, pos):
        idx = self.env_vars_table.indexAt(pos)
        if not idx.isValid():
            return
        self.env_vars_table.selectRow(idx.row())
        row = self._env_var_seleccionada()
        if not row:
            return

        menu = QMenu(self)
        act_editar = menu.addAction("✏️ Editar valor")
        act_eliminar = menu.addAction("✖ Eliminar")
        menu.addSeparator()
        act_nota = menu.addAction("📝 Editar nota")
        act_borrar_nota = menu.addAction("✕ Borrar nota") if self._nota_de_env_var(row.name) is not None else None
        menu.addSeparator()
        act_copiar_valor = menu.addAction("📋 Copiar valor")
        act_copiar_par = menu.addAction("📋 Copiar NOMBRE=valor")
        act_abrir = None
        valor_unico = row.value.strip().strip('"') if row.value else ""
        if valor_unico and os.path.isdir(_expandir_valor_env(valor_unico, dict(os.environ))):
            act_abrir = menu.addAction("📂 Abrir ubicación")
        act_desglose = None
        if row.name.upper() in _PATH_LIKE_VARS:
            menu.addSeparator()
            act_desglose = menu.addAction("📋 Ver PATH desglosado")

        action = menu.exec_(self.env_vars_table.viewport().mapToGlobal(pos))

        if action == act_editar:
            self._editar_env_var()
        elif action == act_eliminar:
            self._eliminar_env_var()
        elif action == act_nota:
            self._editar_nota_env_var()
        elif act_borrar_nota is not None and action == act_borrar_nota:
            self._borrar_nota_env_var()
        elif action == act_copiar_valor:
            QApplication.clipboard().setText(row.value)
            self.set_status("✅ Valor copiado al portapapeles")
        elif action == act_copiar_par:
            QApplication.clipboard().setText(f"{row.name}={row.value}")
            self.set_status("✅ Copiado al portapapeles")
        elif act_abrir is not None and action == act_abrir:
            subprocess.Popen(["explorer", _expandir_valor_env(valor_unico, dict(os.environ))])
        elif act_desglose is not None and action == act_desglose:
            self._ver_path_desglosado()
