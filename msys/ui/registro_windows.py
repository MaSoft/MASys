# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: pestaña Registro de Windows (busqueda, escaneo
de entradas huerfanas, backup de ramas, eliminacion segura).
Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).

Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

import os
import re
import time
import subprocess
import winreg
from typing import List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QCheckBox, QPushButton, QProgressBar, QTableView, QMessageBox,
)

from msys import config as msys_config
from msys.config import logger


def _carpeta_backups_registro() -> str:
    """5.4.0: norma del proyecto — las copias .reg previas van a BACKUPS\\REGISTRO (antes a INFORMES)."""
    carpeta = os.path.join(msys_config.app_base_dir(), "BACKUPS", "REGISTRO")
    try:
        os.makedirs(carpeta, exist_ok=True)
    except OSError:
        return get_informes_folder()
    return carpeta
from msys.core.utils import (
    REG_HIVES, is_admin, get_informes_folder, ruta_guardado_predeterminada,
    dialogo_guardar_archivo, startupinfo_oculto,
)
from msys.models.rows import RegistryResultRow
from msys.models.table_models import RegistryResultTableModel, RegistryResultProxy
from msys.widgets.delegates import _setup_header
from msys.collectors import RegistrySearchThread, RegistryOrphanScanner


class RegistroWindowsMixin:
    def crear_tab_registro_windows(self):
        """Sección de edición/búsqueda del Registro de Windows, basada
        en la operativa de maregedit.py (búsqueda por hives/claves/valores/
        datos con regex, backup de rama, eliminación con confirmación)
        más el escáner de cadenas rotas/huérfanas añadido para MASys."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        self.reg_search_thread = None
        self.reg_orphan_thread = None
        self._reg_modo_huerfanas = False

        if not is_admin():
            aviso_admin = QLabel(
                "⚠️ MASys no se está ejecutando como administrador: la "
                "búsqueda funciona igual, pero eliminar claves/valores del "
                "registro estará deshabilitado hasta reiniciar la "
                "aplicación como administrador.")
            aviso_admin.setObjectName("hintLabel")
            aviso_admin.setWordWrap(True)
            layout.addWidget(aviso_admin)

        # Fila 1: término de búsqueda + regex + acciones principales
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)

        self.reg_txt_busqueda = QLineEdit()
        self.reg_txt_busqueda.setPlaceholderText(
            "🔎 Buscar en el registro (nombre de clave, de valor o dato)...")
        self.reg_txt_busqueda.returnPressed.connect(self._reg_iniciar_busqueda)
        actions_layout.addWidget(self.reg_txt_busqueda, 3)

        self.reg_chk_regex = QCheckBox("Regex")
        actions_layout.addWidget(self.reg_chk_regex)

        actions_layout.addStretch()

        self.reg_btn_buscar = QPushButton("🔍 Buscar")
        self.reg_btn_buscar.setObjectName("primary")
        self.reg_btn_buscar.clicked.connect(self._reg_iniciar_busqueda)
        actions_layout.addWidget(self.reg_btn_buscar)

        self.reg_btn_detener = QPushButton("⏹ Detener")
        self.reg_btn_detener.setObjectName("danger")
        self.reg_btn_detener.setEnabled(False)
        self.reg_btn_detener.clicked.connect(self._reg_detener_busqueda)
        actions_layout.addWidget(self.reg_btn_detener)

        self.reg_btn_backup = QPushButton("💾 Backup Rama")
        self.reg_btn_backup.setObjectName("ghost")
        self.reg_btn_backup.setToolTip(
            "Exporta a un .reg la rama del elemento seleccionado, antes de "
            "modificarla. Opcional pero muy recomendable antes de eliminar.")
        self.reg_btn_backup.clicked.connect(self._reg_backup_rama)
        actions_layout.addWidget(self.reg_btn_backup)

        self.reg_btn_huerfanas = QPushButton("⚠️ Comprobar Cadenas Rotas")
        self.reg_btn_huerfanas.setObjectName("ghost")
        self.reg_btn_huerfanas.setToolTip(
            "Busca referencias a ficheros que ya no existen en disco: "
            "desinstaladores, autoarranque (Run/RunOnce), App Paths, "
            "asociaciones de extensión, CLSID/COM, servicios y Shared "
            "DLLs. Comprobación heurística — revisa antes de eliminar.")
        self.reg_btn_huerfanas.clicked.connect(self._reg_iniciar_escaneo_huerfanas)
        actions_layout.addWidget(self.reg_btn_huerfanas)

        layout.addLayout(actions_layout)

        # Fila 2: hives + ámbito de búsqueda
        filters_row = QHBoxLayout()
        filters_row.setSpacing(8)

        filters_row.addWidget(QLabel("Hives:"))
        self.reg_hive_checks = {}
        hives_por_defecto = {"HKCU": True, "HKLM": True, "HKCR": False, "HKU": False, "HKCC": False}
        for abbr in REG_HIVES:
            chk = QCheckBox(abbr)
            chk.setChecked(hives_por_defecto.get(abbr, False))
            self.reg_hive_checks[abbr] = chk
            filters_row.addWidget(chk)

        sep = QLabel("|")
        sep.setObjectName("hintLabel")
        filters_row.addWidget(sep)

        filters_row.addWidget(QLabel("Buscar en:"))
        self.reg_chk_claves = QCheckBox("Claves")
        self.reg_chk_claves.setChecked(True)
        self.reg_chk_valores = QCheckBox("Valores")
        self.reg_chk_valores.setChecked(True)
        self.reg_chk_datos = QCheckBox("Datos")
        self.reg_chk_datos.setChecked(True)
        filters_row.addWidget(self.reg_chk_claves)
        filters_row.addWidget(self.reg_chk_valores)
        filters_row.addWidget(self.reg_chk_datos)

        filters_row.addStretch()
        layout.addLayout(filters_row)

        # Fila 3: progreso + estado
        progreso_row = QHBoxLayout()
        progreso_row.setSpacing(8)
        self.reg_progress = QProgressBar()
        self.reg_progress.setValue(0)
        self.reg_progress.setTextVisible(False)
        self.reg_progress.setFixedHeight(6)
        progreso_row.addWidget(self.reg_progress, 3)
        self.reg_lbl_count = QLabel("")
        self.reg_lbl_count.setObjectName("hintLabel")
        progreso_row.addWidget(self.reg_lbl_count)
        layout.addLayout(progreso_row)

        # Tabla de resultados
        self.reg_model = RegistryResultTableModel([])
        self.reg_proxy = RegistryResultProxy(self)
        self.reg_proxy.setSourceModel(self.reg_model)

        self.reg_table = QTableView()
        self.reg_table.setModel(self.reg_proxy)
        self.reg_table.setSelectionBehavior(QTableView.SelectRows)
        self.reg_table.setSelectionMode(QTableView.ExtendedSelection)
        self.reg_table.setEditTriggers(QTableView.NoEditTriggers)
        self.reg_table.setAlternatingRowColors(True)
        self.reg_table.setSortingEnabled(True)
        self.reg_table.verticalHeader().setVisible(False)
        self.reg_table.doubleClicked.connect(self._reg_abrir_regedit)
        _setup_header(self.reg_table, len(RegistryResultTableModel.HEADERS),
                      stretch_col=RegistryResultTableModel.COL_RUTA)
        layout.addWidget(self.reg_table, 1)

        # Fila 4: selección / navegación / eliminación. Copiar/CSV/Informe
        # HTML/PDF/Guardar Resultados NO van aquí — los cubre la barra
        # inferior adaptativa (_tabla_secciones_exportables), igual que el
        # resto de secciones de la app (Controladores, Eventos Sistema...).
        bottom_row = QHBoxLayout()
        bottom_row.setSpacing(8)

        btn_sel_todo = QPushButton("Seleccionar Todo")
        btn_sel_todo.setObjectName("ghost")
        btn_sel_todo.clicked.connect(lambda: self.reg_table.selectAll())
        bottom_row.addWidget(btn_sel_todo)

        btn_desel = QPushButton("Deseleccionar")
        btn_desel.setObjectName("ghost")
        btn_desel.clicked.connect(lambda: self.reg_table.clearSelection())
        bottom_row.addWidget(btn_desel)

        btn_copiar_ruta = QPushButton("Copiar Ruta")
        btn_copiar_ruta.setObjectName("ghost")
        btn_copiar_ruta.clicked.connect(self._reg_copiar_ruta)
        bottom_row.addWidget(btn_copiar_ruta)

        btn_regedit = QPushButton("Abrir en Regedit")
        btn_regedit.setObjectName("ghost")
        btn_regedit.clicked.connect(self._reg_abrir_regedit)
        bottom_row.addWidget(btn_regedit)

        bottom_row.addStretch()

        self.reg_btn_eliminar_todas_huerfanas = QPushButton("✕ Eliminar Todas las Huérfanas")
        self.reg_btn_eliminar_todas_huerfanas.setObjectName("danger")
        self.reg_btn_eliminar_todas_huerfanas.setVisible(False)
        self.reg_btn_eliminar_todas_huerfanas.clicked.connect(self._reg_eliminar_todas_huerfanas)
        bottom_row.addWidget(self.reg_btn_eliminar_todas_huerfanas)

        self.reg_btn_eliminar = QPushButton("✕ Eliminar Seleccionados")
        self.reg_btn_eliminar.setObjectName("danger")
        self.reg_btn_eliminar.clicked.connect(self._reg_eliminar_seleccionados)
        bottom_row.addWidget(self.reg_btn_eliminar)

        layout.addLayout(bottom_row)

        self.reg_lbl_status = QLabel("Listo")
        self.reg_lbl_status.setObjectName("hintLabel")
        layout.addWidget(self.reg_lbl_status)

        return tab

    def _reg_hives_seleccionados(self) -> List[str]:
        return [abbr for abbr, chk in self.reg_hive_checks.items() if chk.isChecked()]

    def _reg_fila_seleccionada(self) -> Optional[RegistryResultRow]:
        sel = self.reg_table.selectionModel()
        if not sel or not sel.hasSelection():
            return None
        idx = sel.selectedRows()[0]
        fuente_idx = self.reg_proxy.mapToSource(idx)
        return self.reg_model.rows()[fuente_idx.row()]

    def _reg_filas_seleccionadas(self) -> List[RegistryResultRow]:
        sel = self.reg_table.selectionModel()
        if not sel or not sel.hasSelection():
            return []
        filas = []
        for idx in sel.selectedRows():
            fuente_idx = self.reg_proxy.mapToSource(idx)
            filas.append(self.reg_model.rows()[fuente_idx.row()])
        return filas

    def _reg_iniciar_busqueda(self):
        if self.reg_search_thread and self.reg_search_thread.isRunning():
            return
        # 5.4.0: no mezclar en la misma tabla una búsqueda con el escáner de huérfanas en marcha
        if self.reg_orphan_thread and self.reg_orphan_thread.isRunning():
            self._avisar("Registro de Windows", "Espera a que termine el escaneo de entradas huérfanas.",
                         QMessageBox.Warning)
            return
        termino = self.reg_txt_busqueda.text().strip()
        if not termino:
            self._avisar("Registro de Windows", "Introduce un término de búsqueda.", QMessageBox.Warning)
            return
        hives = self._reg_hives_seleccionados()
        if not hives:
            self._avisar("Registro de Windows", "Selecciona al menos un hive.", QMessageBox.Warning)
            return
        if not (self.reg_chk_claves.isChecked() or self.reg_chk_valores.isChecked()
                or self.reg_chk_datos.isChecked()):
            self._avisar("Registro de Windows",
                         "Selecciona al menos un ámbito de búsqueda (Claves, Valores o Datos).",
                         QMessageBox.Warning)
            return

        self._reg_modo_huerfanas = False
        self.reg_btn_eliminar_todas_huerfanas.setVisible(False)
        self.reg_model.setRows([])
        self.reg_progress.setValue(0)
        self.reg_lbl_count.setText("")
        self.reg_lbl_status.setText("Iniciando búsqueda...")

        self.reg_search_thread = RegistrySearchThread(
            termino, self.reg_chk_regex.isChecked(), hives,
            self.reg_chk_claves.isChecked(), self.reg_chk_valores.isChecked(),
            self.reg_chk_datos.isChecked())
        self.reg_search_thread.resultado_lote.connect(self._reg_on_lote)
        self.reg_search_thread.progreso.connect(self._reg_on_progreso)
        self.reg_search_thread.finalizado.connect(self._reg_on_busqueda_finalizada)
        self.reg_search_thread.error.connect(self._reg_on_error)
        self.reg_btn_buscar.setEnabled(False)
        self.reg_btn_detener.setEnabled(True)
        self.reg_search_thread.start()

    def _reg_detener_busqueda(self):
        if self.reg_search_thread and self.reg_search_thread.isRunning():
            self.reg_search_thread.request_stop()
        if self.reg_orphan_thread and self.reg_orphan_thread.isRunning():
            self.reg_orphan_thread.request_stop()

    def _reg_on_lote(self, lote):
        self.reg_model.addRows(lote)
        filas = self.reg_model.rows()
        n_claves = sum(1 for f in filas if f.match_type == "key")
        n_valores = sum(1 for f in filas if f.match_type == "value")
        self.reg_lbl_count.setText(f"{n_claves} clv | {n_valores} val")

    def _reg_on_progreso(self, pct, texto):
        self.reg_progress.setValue(pct)
        self.reg_lbl_status.setText(texto)

    def _reg_on_busqueda_finalizada(self, n_claves, n_valores):
        self.reg_btn_buscar.setEnabled(True)
        self.reg_btn_detener.setEnabled(False)
        self.reg_lbl_status.setText(f"Completado: {n_claves} claves, {n_valores} valores encontrados")

    def _reg_on_error(self, mensaje):
        self.reg_btn_buscar.setEnabled(True)
        self.reg_btn_detener.setEnabled(False)
        self.reg_lbl_status.setText(f"Error: {mensaje}")
        self._avisar("Registro de Windows", mensaje, QMessageBox.Warning)

    # ── Escáner de cadenas rotas / huérfanas ────────────

    def _reg_iniciar_escaneo_huerfanas(self):
        if self.reg_orphan_thread and self.reg_orphan_thread.isRunning():
            return
        if self.reg_search_thread and self.reg_search_thread.isRunning():
            self._avisar("Registro de Windows", "Espera a que termine la búsqueda en curso.", QMessageBox.Warning)
            return

        self._reg_modo_huerfanas = True
        self.reg_model.setRows([])
        self.reg_progress.setValue(0)
        self.reg_lbl_count.setText("")
        self.reg_btn_eliminar_todas_huerfanas.setVisible(False)

        self.reg_orphan_thread = RegistryOrphanScanner()
        self.reg_orphan_thread.progreso.connect(self._reg_on_progreso)
        self.reg_orphan_thread.finalizado.connect(self._reg_on_huerfanas_finalizado)
        self.reg_orphan_thread.error.connect(self._reg_on_error)
        self.reg_btn_huerfanas.setEnabled(False)
        self.reg_btn_detener.setEnabled(True)
        self.reg_orphan_thread.start()

    def _reg_on_huerfanas_finalizado(self, filas):
        self.reg_btn_huerfanas.setEnabled(True)
        self.reg_btn_detener.setEnabled(False)
        self.reg_model.setRows(filas)
        self.reg_lbl_count.setText(f"{len(filas)} huérfana(s)")
        self.reg_btn_eliminar_todas_huerfanas.setVisible(bool(filas))
        self.reg_lbl_status.setText(
            f"Completado: {len(filas)} entrada(s) rota(s)/huérfana(s) encontrada(s)"
            if filas else "Completado: no se ha encontrado ninguna entrada rota")

    # ── Acciones sobre la selección ─────────────────────

    def _reg_copiar_ruta(self):
        filas = self._reg_filas_seleccionadas()
        if not filas:
            self._avisar("Registro de Windows", "No hay elementos seleccionados.")
            return
        rutas = []
        for f in filas:
            ruta = f.full_path
            if f.value_name:
                ruta += f"\\{f.value_name}"
            rutas.append(ruta)
        QApplication.clipboard().setText("\n".join(rutas))
        self.reg_lbl_status.setText("Ruta(s) copiada(s) al portapapeles")

    def _reg_abrir_regedit(self):
        fila = self._reg_fila_seleccionada()
        if not fila:
            self._avisar("Registro de Windows", "Selecciona un elemento.")
            return
        reg_path = f"Computer\\{fila.hive_full}\\{fila.subkey}" if fila.subkey else f"Computer\\{fila.hive_full}"
        try:
            with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Applets\Regedit",
                    0, winreg.KEY_SET_VALUE) as k:
                winreg.SetValueEx(k, "LastKey", 0, winreg.REG_SZ, reg_path)
        except (PermissionError, OSError):
            pass
        subprocess.Popen(["regedit"])
        self.reg_lbl_status.setText(f"Regedit abierto en {fila.full_path}")

    def _reg_backup_rama(self):
        fila = self._reg_fila_seleccionada()
        if not fila:
            self._avisar("Registro de Windows",
                         "Selecciona un elemento para hacer backup de su rama.")
            return
        rama = fila.full_path
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        nombre_seguro = re.sub(r'[\\/:*?"<>|]', '_', fila.subkey or fila.hive_abbr)[:60]
        nombre_defecto = f"backup_registro_{nombre_seguro}_{timestamp}.reg"

        path = dialogo_guardar_archivo(
            self, "Guardar backup de rama",
            ruta_guardado_predeterminada(nombre_defecto), "Archivo de registro (*.reg)")
        if not path:
            return

        self.reg_lbl_status.setText(f"Exportando {rama}...")
        try:
            resultado = subprocess.run(
                ["reg", "export", rama, path, "/y"],
                capture_output=True, text=True,
                startupinfo=startupinfo_oculto(), creationflags=subprocess.CREATE_NO_WINDOW)
            if resultado.returncode == 0:
                self.reg_lbl_status.setText(f"Backup guardado: {path}")
                self._avisar("Registro de Windows", f"Backup creado:\n{path}")
            else:
                raise RuntimeError(resultado.stderr.strip() or "Error desconocido")
        except Exception as e:
            logger.exception(f"Error creando backup de rama de registro: {e}")
            self._avisar("Error", f"No se pudo crear el backup:\n{e}", QMessageBox.Critical)
            self.reg_lbl_status.setText("Error al crear backup")

    def _reg_eliminar_seleccionados(self):
        self._reg_eliminar_filas(self._reg_filas_seleccionadas())

    def _reg_eliminar_todas_huerfanas(self):
        self._reg_eliminar_filas(list(self.reg_model.rows()))

    def _reg_eliminar_filas(self, filas: List[RegistryResultRow]):
        if not filas:
            self._avisar("Registro de Windows", "No hay elementos que eliminar.")
            return

        if not is_admin():
            self._avisar(
                "Registro de Windows",
                "Se requieren permisos de administrador para eliminar entradas "
                "del registro.\n\nReinicia MASys como administrador.",
                QMessageBox.Warning)
            return

        # Las rutas "bloqueadas" (fila roja) nunca se eliminan desde la
        # app, aunque estuvieran en la selección — se filtran en vez de
        # abortar toda la operación, para no penalizar el resto de la
        # selección por un único acierto de doble clic.
        bloqueadas = [f for f in filas if f.riesgo == "bloqueada"]
        eliminables = [f for f in filas if f.riesgo != "bloqueada"]
        if not eliminables:
            self._avisar(
                "Registro de Windows",
                "Todos los elementos seleccionados están en rutas protegidas "
                "(raíces de hive o ramas del sistema) y no se pueden eliminar "
                "desde esta herramienta.", QMessageBox.Warning)
            return

        n_criticas = sum(1 for f in eliminables if f.riesgo == "critica")
        texto = (f"Se eliminarán {len(eliminables)} entrada(s) del registro.\n"
                 "Esta acción NO se puede deshacer.")
        if n_criticas:
            texto += (f"\n\n⚠️ {n_criticas} de ellas están en rutas críticas/de "
                       "sistema (marcadas en naranja).")
        if bloqueadas:
            texto += (f"\n\n{len(bloqueadas)} elemento(s) de la selección están "
                       "en rutas protegidas y se omitirán automáticamente.")
        texto += "\n\n¿Cómo quieres continuar?"

        eleccion = self._reg_confirmar_eliminacion(texto)
        if eleccion == "cancelar":
            return
        if eleccion == "backup":
            n_ok, errores_backup = self._reg_backup_ramas_multiples(eliminables)
            if errores_backup:
                detalle = "\n".join(errores_backup[:10])
                if not self._confirmar_accion(
                        "Backup incompleto",
                        f"Se crearon {n_ok} backup(s) correctamente, pero "
                        f"{len(errores_backup)} fallaron:\n\n{detalle}\n\n"
                        "¿Continuar con la eliminación de todas formas?",
                        QMessageBox.Warning):
                    return
            else:
                self.reg_lbl_status.setText(
                    f"{n_ok} backup(s) creado(s) en {_carpeta_backups_registro()} — eliminando...")

        filas_borradas_ok = []
        errores = []
        for f in eliminables:
            try:
                if f.match_type == "key" or (f.match_type == "huerfana" and not f.value_name):
                    self._reg_eliminar_clave_recursiva(f.hive_key, f.subkey)
                else:
                    with winreg.OpenKey(f.hive_key, f.subkey, 0,
                                         winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as k:
                        winreg.DeleteValue(k, f.value_name)
                filas_borradas_ok.append(f)
            except Exception as e:
                errores.append(f"{f.full_path}: {e}")

        # Identidad de objeto (id()), no igualdad de contenido: dos filas
        # distintas del registro pueden tener exactamente los mismos
        # valores mostrados (p. ej. dos huérfanas con idéntico motivo).
        ids_borrados = {id(f) for f in filas_borradas_ok}
        nuevas_filas = [f for f in self.reg_model.rows() if id(f) not in ids_borrados]
        self.reg_model.setRows(nuevas_filas)
        if self._reg_modo_huerfanas:
            self.reg_btn_eliminar_todas_huerfanas.setVisible(bool(nuevas_filas))

        for f in filas_borradas_ok:
            self._registrar_historial("🗃️ Registro de Windows", "Eliminar entrada del registro",
                                      f.full_path + (f"\\{f.value_name}" if f.value_name else ""), True,
                                      "Con copia previa .reg" if eleccion == "backup" else "Sin copia previa",
                                      backup_ruta=_carpeta_backups_registro() if eleccion == "backup" else "")
        for e in errores:
            self._registrar_historial("🗃️ Registro de Windows", "Eliminar entrada del registro", e.split(":")[0], False, e)
        msg = f"{len(filas_borradas_ok)} eliminado(s)"
        if errores:
            msg += f", {len(errores)} error(es)"
        self.reg_lbl_status.setText(msg)
        if errores:
            detalle = "\n".join(errores[:10])
            if len(errores) > 10:
                detalle += f"\n... y {len(errores) - 10} más"
            self._avisar("Errores al eliminar", f"{msg}\n\nDetalles:\n{detalle}", QMessageBox.Warning)

    def _reg_confirmar_eliminacion(self, texto: str) -> str:
        """Diálogo de confirmación de borrado con 3 opciones (no solo Sí/No):
        crear backup y eliminar, eliminar sin backup, o cancelar. Devuelve
        'backup' | 'sin_backup' | 'cancelar'."""
        msg_box = QMessageBox(self)
        msg_box.setWindowFlags(msg_box.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        msg_box.setWindowTitle("Confirmar eliminación")
        msg_box.setText(texto)
        msg_box.setIcon(QMessageBox.Warning)
        btn_backup = msg_box.addButton("💾 Backup y eliminar", QMessageBox.AcceptRole)
        btn_sin_backup = msg_box.addButton("✕ Eliminar sin backup", QMessageBox.DestructiveRole)
        msg_box.addButton("Cancelar", QMessageBox.RejectRole)
        msg_box.setDefaultButton(btn_backup)
        msg_box.exec_()
        clicked = msg_box.clickedButton()
        if clicked is btn_backup:
            return "backup"
        if clicked is btn_sin_backup:
            return "sin_backup"
        return "cancelar"

    def _reg_backup_ramas_multiples(self, filas: List[RegistryResultRow]):
        """Exporta a INFORMES\\ un .reg por cada rama distinta implicada en
        'filas' (sin diálogo por archivo — con el escáner de huérfanas
        puede haber decenas de ramas distintas). Devuelve (nº de backups
        creados, lista de errores)."""
        ramas = sorted({f.full_path for f in filas})
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        carpeta = _carpeta_backups_registro()
        creados = 0
        errores = []
        for rama in ramas:
            nombre_seguro = re.sub(r'[\\/:*?"<>|]', '_', rama)[:100]
            path = os.path.join(carpeta, f"backup_registro_{nombre_seguro}_{timestamp}.reg")
            try:
                resultado = subprocess.run(
                    ["reg", "export", rama, path, "/y"],
                    capture_output=True, text=True,
                    startupinfo=startupinfo_oculto(), creationflags=subprocess.CREATE_NO_WINDOW)
                if resultado.returncode == 0:
                    creados += 1
                else:
                    errores.append(f"{rama}: {resultado.stderr.strip() or 'error desconocido'}")
            except Exception as e:
                errores.append(f"{rama}: {e}")
        return creados, errores

    def _reg_eliminar_clave_recursiva(self, hive, subkey):
        with winreg.OpenKey(hive, subkey, 0, winreg.KEY_ALL_ACCESS | winreg.KEY_WOW64_64KEY) as k:
            while True:
                try:
                    hijo = winreg.EnumKey(k, 0)
                except OSError:
                    break
                self._reg_eliminar_clave_recursiva(hive, f"{subkey}\\{hijo}")
        winreg.DeleteKeyEx(hive, subkey, winreg.KEY_WOW64_64KEY, 0)
