# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña 📦 Gestor de Paquetes: tabla winget,
filtros, selección múltiple + "Instalar todas/seleccionadas" en segundo
plano, y bloqueo/desbloqueo de actualización por paquete (winget pin
--blocking) desde el menú contextual — una fila a la vez, a diferencia de
instalar actualizaciones (eso sí admite lote, decisión explícita del
usuario). Ver PS_GET_WINGET_UPGRADES (msys/ps_scripts.py) para el porqué
de --include-pinned y el parseo de dos tablas.

Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).
Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

from PyQt5.QtCore import Qt, QRegExp
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QCheckBox, QMenu, QMessageBox, QTableView, QApplication, QInputDialog, QDialog,
)

from msys.core.utils import run_powershell_command_params, is_admin
from msys.models.rows import PackageUpgradeRow
from msys.models.table_models import PackageUpgradeTableModel, PackageFilterProxy
from msys.widgets.delegates import _setup_header
from msys.collectors import PackageUpgradeCollector, PackageBatchUpgradeCollector
from msys.ps_scripts import PS_WINGET_PIN_ADD, PS_WINGET_PIN_REMOVE

_SECCION_AUDITORIA_PAQUETES = "📦 Gestor de Paquetes"


class GestorPaquetesMixin:
    def crear_tab_gestor_paquetes(self):
        """Crea la pestaña de Gestor de Paquetes (winget)."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        # Fila 1: hostname + búsqueda + acciones + Actualizar — mismo
        # lenguaje visual que el resto de pestañas de la app.
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)

        import socket
        hostname = socket.gethostname()
        lbl_hostname = QLabel(f"🖥️ {hostname}")
        lbl_hostname.setObjectName("hostnameLabel")
        actions_layout.addWidget(lbl_hostname)

        self.search_paquetes = QLineEdit()
        self.search_paquetes.setPlaceholderText("🔎 Buscar paquetes...")
        self.search_paquetes.textChanged.connect(self._filtrar_paquetes_texto)
        actions_layout.addWidget(self.search_paquetes, 3)

        actions_layout.addStretch()

        self.btn_instalar_seleccionadas = QPushButton("⬆️ Instalar seleccionadas")
        self.btn_instalar_seleccionadas.setToolTip(
            "Instala la actualización de los paquetes seleccionados en la tabla "
            "(Ctrl+clic / Mayús+clic para elegir varios)")
        self.btn_instalar_seleccionadas.clicked.connect(self._instalar_seleccionadas)
        actions_layout.addWidget(self.btn_instalar_seleccionadas)

        self.btn_instalar_todas = QPushButton("⬆️ Instalar todas")
        self.btn_instalar_todas.setToolTip(
            "Instala todas las actualizaciones disponibles, excepto las bloqueadas 🔒")
        self.btn_instalar_todas.clicked.connect(self._instalar_todas)
        actions_layout.addWidget(self.btn_instalar_todas)

        self.btn_refresh_paquetes = QPushButton("🔄 Actualizar")
        self.btn_refresh_paquetes.setObjectName("primary")
        self.btn_refresh_paquetes.clicked.connect(self.refresh_gestor_paquetes)
        actions_layout.addWidget(self.btn_refresh_paquetes)

        layout.addLayout(actions_layout)

        # Fila 2: filtros (checkbox) + contador a la derecha.
        filtros_layout = QHBoxLayout()
        filtros_layout.setSpacing(8)

        self.chk_ocultar_bloqueadas = QCheckBox("Ocultar bloqueadas")
        self.chk_ocultar_bloqueadas.setToolTip(
            "Las bloqueadas 🔒 se muestran por defecto para poder desbloquearlas desde aquí; "
            "marca esto para ver solo lo que realmente se puede actualizar ahora mismo.")
        self.chk_ocultar_bloqueadas.stateChanged.connect(
            lambda estado: self._on_filtro_bloqueo_cambiado(self.chk_ocultar_bloqueadas, self.chk_solo_bloqueadas, estado))
        filtros_layout.addWidget(self.chk_ocultar_bloqueadas)

        self.chk_solo_bloqueadas = QCheckBox("Solo bloqueadas")
        self.chk_solo_bloqueadas.setToolTip("Ver únicamente los paquetes bloqueados 🔒, para revisarlos o desbloquearlos.")
        self.chk_solo_bloqueadas.stateChanged.connect(
            lambda estado: self._on_filtro_bloqueo_cambiado(self.chk_solo_bloqueadas, self.chk_ocultar_bloqueadas, estado))
        filtros_layout.addWidget(self.chk_solo_bloqueadas)

        filtros_layout.addStretch()

        if not is_admin():
            lbl_sin_admin = QLabel(
                "ℹ️ MASys no se ejecuta como administrador — las actualizaciones de "
                "ámbito de máquina pueden fallar")
            lbl_sin_admin.setObjectName("hintLabel")
            filtros_layout.addWidget(lbl_sin_admin)

        self.lbl_paquetes_count = QLabel("0 / 0")
        self.lbl_paquetes_count.setObjectName("countLabel")
        filtros_layout.addWidget(self.lbl_paquetes_count)

        layout.addLayout(filtros_layout)

        self.paquetes_model = PackageUpgradeTableModel([])
        self.paquetes_proxy = PackageFilterProxy(self)
        self.paquetes_proxy.setSourceModel(self.paquetes_model)
        self.paquetes_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.paquetes_proxy.setFilterKeyColumn(-1)

        self.paquetes_table = QTableView()
        self.paquetes_table.setModel(self.paquetes_proxy)
        self.paquetes_table.setAlternatingRowColors(True)
        self.paquetes_table.setSelectionBehavior(QTableView.SelectRows)
        self.paquetes_table.setSelectionMode(QTableView.ExtendedSelection)
        self.paquetes_table.verticalHeader().setVisible(False)
        self.paquetes_table.setSortingEnabled(True)
        self.paquetes_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.paquetes_table.customContextMenuRequested.connect(self._ctx_paquetes)
        _setup_header(self.paquetes_table, len(PackageUpgradeTableModel.HEADERS), stretch_col=1)
        layout.addWidget(self.paquetes_table)

        return tab

    # ── Búsqueda / filtros / contador ────────────────────────────────────

    def _filtrar_paquetes_texto(self):
        self.paquetes_proxy.setFilterRegExp(
            QRegExp(self.search_paquetes.text(), Qt.CaseInsensitive, QRegExp.FixedString))
        self._update_paquetes_count()

    def _on_filtro_bloqueo_cambiado(self, chk_activado, chk_opuesto, estado):
        """'Ocultar bloqueadas' y 'Solo bloqueadas' son mutuamente
        excluyentes (marcar una a la fuerza sin filas visibles es un
        estado inútil) — al marcar una se desmarca la otra, en vez de un
        combo/radio (checkboxes, mismo lenguaje visual que el resto de
        filtros de la app)."""
        if estado and chk_opuesto.isChecked():
            chk_opuesto.blockSignals(True)
            chk_opuesto.setChecked(False)
            chk_opuesto.blockSignals(False)
        self._filtrar_paquetes()

    def _filtrar_paquetes(self):
        self.paquetes_proxy.setFiltros(
            self.chk_ocultar_bloqueadas.isChecked(), self.chk_solo_bloqueadas.isChecked())
        self._update_paquetes_count()

    def _update_paquetes_count(self):
        self.lbl_paquetes_count.setText(f"{self.paquetes_proxy.rowCount()} / {self.paquetes_model.rowCount()}")

    # ── Refresco (consulta winget) ───────────────────────────────────────

    def refresh_gestor_paquetes(self):
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.set_status("🔄 Buscando actualizaciones con winget...")
        self.btn_refresh_paquetes.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.worker = PackageUpgradeCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_paquetes_data)
        self.worker.finished_error.connect(self._on_paquetes_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_paquetes_data(self, data: dict):
        self.btn_refresh_paquetes.setEnabled(True)
        self.progress_bar.setVisible(False)
        if not data.get('Disponible', False):
            self.paquetes_model.setRows([])
            self._update_paquetes_count()
            self.set_status("⚠️ winget no está disponible en este equipo")
            self._avisar("winget no disponible", "No se encontró 'winget' en este equipo. Instálalo desde la Microsoft Store\n"
                "(App Installer) para poder gestionar actualizaciones desde aquí.")
            return
        paquetes = data.get('Paquetes', []) or []
        motivos = self.package_block_notes_db.get_all()
        rows = [PackageUpgradeRow(
            nombre=str(p.get('Nombre', '')),
            id_paquete=str(p.get('Id', '')),
            version_actual=str(p.get('VersionActual', '')),
            version_disponible=str(p.get('VersionDisponible', '')),
            origen=str(p.get('Origen', '')),
            bloqueado=bool(p.get('Bloqueado', False)),
            motivo_bloqueo=motivos.get(str(p.get('Id', '')), ''),
        ) for p in paquetes]
        self.paquetes_model.setRows(rows)
        self._update_paquetes_count()
        bloqueadas = sum(1 for r in rows if r.bloqueado)
        extra = f" ({bloqueadas} bloqueada(s))" if bloqueadas else ""
        self.set_status(f"✅ {len(rows)} paquete(s) con actualización disponible{extra}")

    def _on_paquetes_error(self, msg):
        self.btn_refresh_paquetes.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al buscar actualizaciones")
        self._avisar("Error", f"No se pudo consultar winget:\n\n{msg}", QMessageBox.Warning)

    # ── Selección ─────────────────────────────────────────────────────────

    def _filas_seleccionadas(self):
        """Filas PackageUpgradeRow reales (no de texto) de la selección
        actual, mapeando proxy->modelo — no confía en el orden/columnas
        visibles, a diferencia del código anterior que leía .data() por
        columna."""
        sel = self.paquetes_table.selectionModel()
        if not sel or not sel.hasSelection():
            return []
        filas_modelo = self.paquetes_model.rows()
        resultado = []
        vistos = set()
        for idx in sel.selectedRows():
            source_index = self.paquetes_proxy.mapToSource(idx)
            r = source_index.row()
            if 0 <= r < len(filas_modelo) and r not in vistos:
                vistos.add(r)
                resultado.append(filas_modelo[r])
        return resultado

    def _fila_bajo_cursor(self, pos):
        idx = self.paquetes_table.indexAt(pos)
        if not idx.isValid():
            return None
        source_index = self.paquetes_proxy.mapToSource(idx)
        filas = self.paquetes_model.rows()
        r = source_index.row()
        return filas[r] if 0 <= r < len(filas) else None

    # ── Instalar actualizaciones (seleccionadas / todas) — en lote ──────

    def _instalar_seleccionadas(self):
        filas = self._filas_seleccionadas()
        if not filas:
            self._avisar("Instalar actualizaciones", "Selecciona primero uno o varios paquetes de la lista "
                "(Ctrl+clic / Mayús+clic para elegir varios).")
            return
        # 5.4.0: las bloqueadas 🔒 se quedan fuera (winget las rechaza y salían como error)
        bloqueadas = [r for r in filas if getattr(r, "bloqueado", False)]
        filas = [r for r in filas if not getattr(r, "bloqueado", False)]
        if not filas:
            self._avisar("Instalar actualizaciones", "Todo lo seleccionado está bloqueado 🔒. Desbloquéalo antes "
                         "desde el menú contextual si quieres instalarlo.")
            return
        self._instalar_lote(filas, "seleccionada(s)", excluidas_bloqueadas=len(bloqueadas))

    def _instalar_todas(self):
        filas = [r for r in self.paquetes_model.rows() if not r.bloqueado]
        total_bloqueadas = sum(1 for r in self.paquetes_model.rows() if r.bloqueado)
        if not filas:
            if total_bloqueadas:
                self._avisar("Instalar todas", "No hay ninguna actualización disponible: todo lo listado está bloqueado 🔒.")
            else:
                self._avisar("Instalar todas", "No hay actualizaciones pendientes.")
            return
        self._instalar_lote(filas, "disponible(s)", excluidas_bloqueadas=total_bloqueadas)

    def _instalar_lote(self, filas, sufijo_objetivo: str, excluidas_bloqueadas: int = 0):
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        nombres = [r.nombre for r in filas]
        cambios = [f"Instalar la actualización de «{n}»" for n in nombres[:15]]
        if len(nombres) > 15:
            cambios.append(f"... y {len(nombres) - 15} más")
        explicacion = (
            f"{excluidas_bloqueadas} paquete(s) bloqueado(s) quedan fuera de este lote automáticamente."
            if excluidas_bloqueadas else None
        )
        if not self._confirmar_reparacion(
            f"Instalar {len(filas)} actualización(es)",
            cambios=cambios,
            reversible=False,
            objetivo=f"{len(filas)} paquete(s) {sufijo_objetivo}",
            explicacion=explicacion,
        ):
            return

        self.set_status(f"🔄 Instalando {len(filas)} actualización(es)...")
        self.btn_instalar_seleccionadas.setEnabled(False)
        self.btn_instalar_todas.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.worker = PackageBatchUpgradeCollector([(r.nombre, r.id_paquete) for r in filas])
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_lote_finalizado)
        self.worker.finished_error.connect(self._on_lote_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_lote_finalizado(self, resultados: list):
        """resultados: [(nombre, id, ok, mensaje), ...]"""
        self.btn_instalar_seleccionadas.setEnabled(True)
        self.btn_instalar_todas.setEnabled(True)
        self.progress_bar.setVisible(False)

        ok_count = sum(1 for _, _, ok, _ in resultados if ok)
        fallos = [(n, m) for n, _, ok, m in resultados if not ok]

        for nombre, id_paquete, ok, mensaje in resultados:
            self.repair_audit_db.registrar(
                seccion=_SECCION_AUDITORIA_PAQUETES, accion="Instalar actualización",
                objetivo=f"{nombre} ({id_paquete})", exito=ok, detalle=mensaje or "OK",
            )

        if fallos:
            detalle_fallos = "\n".join(f"• {n}: {m or 'sin detalles'}" for n, m in fallos[:10])
            if len(fallos) > 10:
                detalle_fallos += f"\n... y {len(fallos) - 10} más"
            self.set_status(f"⚠️ {ok_count} actualizada(s), {len(fallos)} con error(es)")
            self._avisar(
                "Instalación completada con avisos",
                f"✅ {ok_count} paquete(s) actualizado(s) correctamente.\n"
                f"❌ {len(fallos)} paquete(s) con error:\n\n{detalle_fallos}",
                QMessageBox.Warning,
            )
        else:
            self.set_status(f"✅ {ok_count} paquete(s) actualizado(s) correctamente")
            self._avisar("Instalación completada", f"{ok_count} paquete(s) actualizado(s) correctamente.")

        self.refresh_gestor_paquetes()

    def _on_lote_error(self, msg):
        self.btn_instalar_seleccionadas.setEnabled(True)
        self.btn_instalar_todas.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al instalar actualizaciones")
        self._avisar("Error", f"No se pudo completar la instalación:\n\n{msg}", QMessageBox.Warning)

    # ── Bloquear / Desbloquear (contextual, un paquete a la vez) ────────

    def _ctx_paquetes(self, pos):
        row = self._fila_bajo_cursor(pos)
        if not row:
            return
        idx = self.paquetes_table.indexAt(pos)
        self.paquetes_table.selectRow(idx.row())

        menu = QMenu(self)
        if row.bloqueado:
            act_toggle = menu.addAction("🔓 Desbloquear actualización")
        else:
            act_toggle = menu.addAction("🔒 Bloquear actualización")
        menu.addSeparator()
        act_instalar = menu.addAction("⬆️ Instalar esta actualización")
        act_instalar.setEnabled(not row.bloqueado)
        menu.addSeparator()
        act_copiar_id = menu.addAction("📋 Copiar Id")

        action = menu.exec_(self.paquetes_table.viewport().mapToGlobal(pos))
        if action == act_toggle:
            self._alternar_bloqueo_paquete(row)
        elif action == act_instalar:
            self._instalar_lote([row], "seleccionada(s)")
        elif action == act_copiar_id:
            QApplication.clipboard().setText(row.id_paquete)
            self.set_status(f"✅ Id copiado: {row.id_paquete}")

    def _alternar_bloqueo_paquete(self, row: PackageUpgradeRow):
        if row.bloqueado:
            titulo = "Desbloquear actualización"
            cambios = [f"«{row.nombre}» volverá a poder actualizarse mediante winget"]
            plantilla = PS_WINGET_PIN_REMOVE
        else:
            titulo = f"Bloquear actualización — {row.nombre}"
            cambios = [
                f"«{row.nombre}» quedará bloqueada para winget (ni de forma individual ni con 'Instalar todas') "
                "hasta que se desbloquee de nuevo",
            ]
            plantilla = PS_WINGET_PIN_ADD

        if not self._confirmar_reparacion(
            titulo, cambios=cambios, reversible=True, objetivo=row.nombre,
            explicacion=None if row.bloqueado else (
                "El bloqueo solo afecta a actualizaciones gestionadas por winget: si la propia "
                "aplicación tiene su actualizador interno (p. ej. navegadores), puede seguir "
                "actualizándose por sí sola pese al bloqueo."),
        ):
            return

        motivo = ""
        if not row.bloqueado:
            # Instancia + quitar '?', NUNCA QInputDialog.getText(...) estático
            # (convención fija del proyecto). Opcional: cancelar aquí no
            # aborta el bloqueo, solo lo deja sin motivo guardado.
            dlg_motivo = QInputDialog(self)
            dlg_motivo.setWindowFlags(dlg_motivo.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            dlg_motivo.setWindowTitle("Motivo del bloqueo (opcional)")
            dlg_motivo.setLabelText(
                f"¿Por qué se bloquea «{row.nombre}»? (opcional — para que quien lo vea después sepa por qué, sin preguntar)")
            dlg_motivo.setInputMode(QInputDialog.TextInput)
            if dlg_motivo.exec_() == QDialog.Accepted:
                motivo = dlg_motivo.textValue().strip()

        ok, msg = run_powershell_command_params(plantilla, id=row.id_paquete)
        if ok:
            if row.bloqueado:
                self.package_block_notes_db.delete_motivo(row.id_paquete)
            elif motivo:
                self.package_block_notes_db.save_motivo(row.id_paquete, motivo)
        self.repair_audit_db.registrar(
            seccion=_SECCION_AUDITORIA_PAQUETES,
            accion="Desbloquear actualización" if row.bloqueado else "Bloquear actualización",
            objetivo=f"{row.nombre} ({row.id_paquete})", exito=ok,
            detalle=(motivo if motivo else "OK") if ok else msg,
        )
        if ok:
            self.set_status(f"✅ {'Desbloqueada' if row.bloqueado else 'Bloqueada'}: {row.nombre}")
            self.refresh_gestor_paquetes()
        else:
            self._avisar("Error", f"No se pudo {'desbloquear' if row.bloqueado else 'bloquear'} «{row.nombre}»:\n\n{msg}",
                          QMessageBox.Warning)
