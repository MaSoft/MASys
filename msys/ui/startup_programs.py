# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña 🚀 Programas de Inicio — registro
(Run de HKLM/HKCU, 32/64 bits) + carpetas de inicio (usuario actual y
todos los usuarios), con activar/desactivar por fila o en lote, filtros,
detección de sospechosos vía Soporte y verificación de firma digital.

El activar/desactivar usa la MISMA clave binaria (StartupApproved) que el
Administrador de tareas de Windows — el valor Run / acceso directo
original NUNCA se borra ni se mueve, solo se marca como "no ejecutar en
el próximo inicio de sesión". Ver msys/domain/startup_programs.py.

Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).
Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

import os
import subprocess

from PyQt5.QtCore import Qt, QRegExp
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QCheckBox, QMessageBox, QTableView, QMenu, QApplication,
)

from msys.core.utils import escape_powershell_string
from msys.models.rows import StartupProgramRow
from msys.models.table_models import StartupProgramTableModel, StartupProgramFilterProxy
from msys.widgets.delegates import _setup_header
from msys.collectors import StartupProgramsCollector
from msys.domain.startup_programs import set_startup_enabled

_SECCION_PROGRAMAS_INICIO = "🚀 Programas de Inicio"



def _ejecutable_de_comando(comando: str) -> str:
    """5.4.0: ruta del ejecutable de una línea de órdenes de Windows. Antes se cortaba por «" »: un
    comando sin comillas con argumentos («C:\\Windows\\System32\\notepad.exe /min») se tomaba
    entero como ruta y la firma salía «No se pudo comprobar» (probado). Se usa el mismo troceado que
    Windows (CommandLineToArgvW) y, si la ruta tiene espacios sin comillas, se busca el primer
    prefijo que existe en disco (como hace CreateProcess)."""
    comando = os.path.expandvars((comando or "").strip())
    if not comando:
        return ""
    if comando.startswith('"'):
        return comando[1:].split('"', 1)[0]
    if os.path.isfile(comando):
        return comando
    partes = comando.split(" ")
    for n in range(1, len(partes) + 1):
        candidato = " ".join(partes[:n])
        if os.path.isfile(candidato):
            return candidato
        if not candidato.lower().endswith(".exe") and os.path.isfile(candidato + ".exe"):
            return candidato + ".exe"
    return partes[0]

class ProgramasInicioMixin:
    def crear_tab_programas_inicio(self):
        """Crea la pestaña de Programas de Inicio."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        # Cabecera: TODO en una fila (hostname, búsqueda, acciones en
        # lote, Actualizar a la derecha).
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)

        import socket
        hostname = socket.gethostname()
        lbl_hostname = QLabel(f"🖥️ {hostname}")
        lbl_hostname.setObjectName("hostnameLabel")
        actions_layout.addWidget(lbl_hostname)

        self.search_programas_inicio = QLineEdit()
        self.search_programas_inicio.setPlaceholderText("🔎 Buscar en programas de inicio...")
        self.search_programas_inicio.textChanged.connect(self._filtrar_programas_inicio_texto)
        actions_layout.addWidget(self.search_programas_inicio, 3)

        actions_layout.addStretch()

        self.btn_habilitar_seleccionados = QPushButton("✅ Habilitar seleccionados")
        self.btn_habilitar_seleccionados.setEnabled(False)
        self.btn_habilitar_seleccionados.clicked.connect(lambda: self._alternar_seleccionados_programas_inicio(True))
        actions_layout.addWidget(self.btn_habilitar_seleccionados)

        self.btn_deshabilitar_seleccionados = QPushButton("🚫 Deshabilitar seleccionados")
        self.btn_deshabilitar_seleccionados.setEnabled(False)
        self.btn_deshabilitar_seleccionados.clicked.connect(lambda: self._alternar_seleccionados_programas_inicio(False))
        actions_layout.addWidget(self.btn_deshabilitar_seleccionados)

        self.btn_refresh_programas_inicio = QPushButton("🔄 Actualizar")
        self.btn_refresh_programas_inicio.setObjectName("primary")
        self.btn_refresh_programas_inicio.clicked.connect(self.refresh_programas_inicio)
        actions_layout.addWidget(self.btn_refresh_programas_inicio)

        layout.addLayout(actions_layout)

        # Segunda fila: filtros (checkboxes combinables en AND) + contador
        # a la derecha — misma convención que el resto de MASys.
        filtros_layout = QHBoxLayout()
        filtros_layout.setSpacing(8)

        self.chk_startup_sospechosos = QCheckBox("Solo sospechosos")
        self.chk_startup_sospechosos.setToolTip(
            "Se lanzan desde una carpeta temporal/de descargas, o el fichero al que apuntan "
            "ya no existe en el disco (entrada huérfana).")
        self.chk_startup_sospechosos.stateChanged.connect(self._filtrar_programas_inicio)
        filtros_layout.addWidget(self.chk_startup_sospechosos)

        self.chk_startup_habilitados = QCheckBox("Solo habilitados")
        self.chk_startup_habilitados.stateChanged.connect(self._filtrar_programas_inicio)
        filtros_layout.addWidget(self.chk_startup_habilitados)

        self.chk_startup_deshabilitados = QCheckBox("Solo deshabilitados")
        self.chk_startup_deshabilitados.stateChanged.connect(self._filtrar_programas_inicio)
        filtros_layout.addWidget(self.chk_startup_deshabilitados)

        filtros_layout.addStretch()

        self.lbl_startup_count = QLabel("0 / 0")
        self.lbl_startup_count.setObjectName("countLabel")
        filtros_layout.addWidget(self.lbl_startup_count)

        layout.addLayout(filtros_layout)

        self.startup_model = StartupProgramTableModel([])
        self.startup_proxy = StartupProgramFilterProxy(self)
        self.startup_proxy.setSourceModel(self.startup_model)
        self.startup_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.startup_proxy.setFilterKeyColumn(-1)

        self.startup_table = QTableView()
        self.startup_table.setModel(self.startup_proxy)
        self.startup_table.setAlternatingRowColors(True)
        self.startup_table.setSelectionBehavior(QTableView.SelectRows)
        self.startup_table.verticalHeader().setVisible(False)
        self.startup_table.setSortingEnabled(True)
        self.startup_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.startup_table.customContextMenuRequested.connect(self._ctx_programas_inicio)
        _setup_header(self.startup_table, len(StartupProgramTableModel.HEADERS), stretch_col=1)

        layout.addWidget(self.startup_table, 1)

        # setModel() crea un selectionModel nuevo cada vez — conectar
        # DESPUÉS de asignar el modelo, no antes.
        self.startup_table.selectionModel().selectionChanged.connect(self._actualizar_botones_lote_programas_inicio)

        return tab

    # ── Carga y filtros ───────────────────────────────────────────────────

    def refresh_programas_inicio(self):
        if not hasattr(self, 'btn_refresh_programas_inicio'):
            return
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.set_status("🔄 Consultando programas de inicio...")
        self.btn_refresh_programas_inicio.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.worker = StartupProgramsCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_programas_inicio_data)
        self.worker.finished_error.connect(self._on_programas_inicio_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_programas_inicio_data(self, rows):
        self.startup_model.setRows(rows)
        self.btn_refresh_programas_inicio.setEnabled(True)
        self.progress_bar.setVisible(False)
        self._update_startup_count()
        self.set_status(f"✅ {len(rows)} programa(s) de inicio encontrado(s)")

    def _on_programas_inicio_error(self, msg):
        self.btn_refresh_programas_inicio.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al consultar programas de inicio")
        self._avisar("Error", f"No se pudo obtener la lista de programas de inicio:\n\n{msg}", QMessageBox.Warning)

    def _filtrar_programas_inicio_texto(self):
        self.startup_proxy.setFilterRegExp(
            QRegExp(self.search_programas_inicio.text(), Qt.CaseInsensitive, QRegExp.FixedString))
        self._update_startup_count()

    def _filtrar_programas_inicio(self):
        self.startup_proxy.setFiltros(
            self.chk_startup_sospechosos.isChecked(),
            self.chk_startup_habilitados.isChecked(),
            self.chk_startup_deshabilitados.isChecked(),
        )
        self._update_startup_count()

    def _update_startup_count(self):
        self.lbl_startup_count.setText(f"{self.startup_proxy.rowCount()} / {self.startup_model.rowCount()}")

    # ── Selección y acciones en lote ────────────────────────────────────

    def _programas_inicio_seleccionados(self):
        filas_origen = {self.startup_proxy.mapToSource(idx).row()
                         for idx in self.startup_table.selectionModel().selectedRows()}
        filas = self.startup_model.rows()
        return [filas[i] for i in sorted(filas_origen) if 0 <= i < len(filas)]

    def _programa_inicio_seleccionado(self):
        seleccionados = self._programas_inicio_seleccionados()
        return seleccionados[0] if seleccionados else None

    def _actualizar_botones_lote_programas_inicio(self):
        hay_sel = bool(self.startup_table.selectionModel().selectedRows())
        self.btn_habilitar_seleccionados.setEnabled(hay_sel)
        self.btn_deshabilitar_seleccionados.setEnabled(hay_sel)

    def _alternar_seleccionados_programas_inicio(self, habilitar: bool):
        filas = self._programas_inicio_seleccionados()
        if not filas:
            return
        etiqueta = "Habilitar" if habilitar else "Deshabilitar"
        nombres = "\n".join(f"• {r.nombre}" for r in filas[:15])
        if len(filas) > 15:
            nombres += f"\n… y {len(filas) - 15} más"
        if not self._confirmar_accion(
            f"{etiqueta} {len(filas)} programa(s) de inicio",
            f"¿{etiqueta} estas {len(filas)} entrada(s) en el inicio de Windows?\n\n{nombres}",
        ):
            return

        correctos, fallidos = [], []
        for row in filas:
            if self._ejecutar_toggle_programa_inicio(row, habilitar):
                correctos.append(row.nombre)
            else:
                fallidos.append(row.nombre)

        participio = "habilitada(s)" if habilitar else "deshabilitada(s)"
        if fallidos:
            self._avisar(
                "Con avisos",
                f"{len(correctos)} entrada(s) {participio} correctamente.\n\n"
                f"No se pudieron modificar {len(fallidos)}:\n" + "\n".join(f"• {n}" for n in fallidos),
                QMessageBox.Warning)
        self.set_status(f"✅ {len(correctos)} programa(s) de inicio {participio}"
                         + (f" — {len(fallidos)} con error" if fallidos else ""))
        self.refresh_programas_inicio()

    # ── Menú contextual ──────────────────────────────────────────────────

    def _ctx_programas_inicio(self, pos):
        idx = self.startup_table.indexAt(pos)
        if not idx.isValid():
            return
        if not self.startup_table.selectionModel().isRowSelected(idx.row(), idx.parent()):
            self.startup_table.selectRow(idx.row())
        row = self._programa_inicio_seleccionado()
        if not row:
            return

        habilitado = row.estado == "Habilitado"
        menu = QMenu(self)
        if habilitado:
            act_toggle = menu.addAction("🚫 Deshabilitar en el inicio")
        else:
            act_toggle = menu.addAction("✅ Habilitar en el inicio")
        menu.addSeparator()
        act_sig = menu.addAction("🔏 Verificar firma digital")
        menu.addSeparator()
        act_copy = menu.addAction("📋 Copiar fila")

        action = menu.exec_(self.startup_table.viewport().mapToGlobal(pos))
        if action == act_toggle:
            self._alternar_programa_inicio(row, habilitar=not habilitado)
        elif action == act_sig:
            self._verificar_firma_programa_inicio(row)
        elif action == act_copy:
            cols = [self.startup_proxy.data(self.startup_proxy.index(idx.row(), c), Qt.DisplayRole)
                    for c in range(len(StartupProgramTableModel.HEADERS))]
            QApplication.clipboard().setText("\t".join(str(v or "") for v in cols))

    def _verificar_firma_programa_inicio(self, row: StartupProgramRow):
        """Mismo mecanismo que '🔏 Verificar firma digital' de Servicios —
        Get-AuthenticodeSignature sobre el ejecutable resuelto. No se hace
        de forma automática para todas las filas al cargar (sería lento);
        es una comprobación bajo demanda por fila."""
        ruta = _ejecutable_de_comando(row.comando)
        if not ruta:
            self._avisar("Firma digital", "No hay ruta de ejecutable disponible para esta entrada.")
            return
        self.set_status("🔄 Comprobando firma digital...")
        QApplication.processEvents()
        ruta_escaped = escape_powershell_string(ruta)
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"$s = Get-AuthenticodeSignature -LiteralPath '{ruta_escaped}' -ErrorAction SilentlyContinue; "
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
        self._avisar(f"Firma digital — {row.nombre}", f"Archivo: {ruta}\n\nEstado: {estado}\nFirmante: {firmante}")
        self.set_status(f"✅ Firma digital: {estado}")

    # ── Toggle individual (menú contextual y Soporte) ──────────────────

    def _ejecutar_toggle_programa_inicio(self, row: StartupProgramRow, habilitar: bool) -> bool:
        """Único punto de verdad para ejecutar+auditar el activar/
        desactivar — usado desde el menú contextual, el lote y Soporte.
        NO confirma ni refresca (eso lo decide cada llamador: el lote
        confirma una vez para todas, no fila a fila)."""
        etiqueta = "Habilitar programa de inicio" if habilitar else "Deshabilitar programa de inicio"
        try:
            set_startup_enabled(row.hive, row.clave_origen, row.clave_valor, habilitar)
            ok, detalle = True, "OK"
        except PermissionError:
            ok, detalle = False, "Permisos insuficientes (requiere administrador para entradas de Todos los Usuarios)"
        except OSError as e:
            ok, detalle = False, str(e)

        if hasattr(self, "repair_audit_db"):
            self.repair_audit_db.registrar(
                seccion=_SECCION_PROGRAMAS_INICIO, accion=etiqueta,
                objetivo=row.nombre, exito=ok, detalle=detalle,
            )
        return ok

    def _alternar_programa_inicio(self, row: StartupProgramRow, habilitar: bool):
        etiqueta = "Habilitar" if habilitar else "Deshabilitar"
        texto = f"¿{etiqueta} «{row.nombre}» en el inicio de Windows?"
        if row.hive == "HKLM":
            texto += ("\n\n⚠ Esta entrada afecta a TODOS los usuarios del equipo y requiere "
                      "permisos de administrador.")
        if not self._confirmar_accion(f"{etiqueta} programa de inicio", texto):
            return

        ok = self._ejecutar_toggle_programa_inicio(row, habilitar)
        if not ok:
            self._avisar("Con avisos", f"No se pudo {etiqueta.lower()} «{row.nombre}».\n\n"
                         "Si es una entrada de Todos los Usuarios, ejecuta MASys como administrador.",
                         QMessageBox.Warning)
            return

        participio = "Habilitado" if habilitar else "Deshabilitado"
        self.set_status(f"✅ «{row.nombre}» — {participio.lower()} en el inicio")
        self.refresh_programas_inicio()
