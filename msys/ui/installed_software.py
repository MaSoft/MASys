# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña Software Instalado: tabla, lista negra, desinstalar, menu contextual, exportar.
Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).

Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

import os
import re
import csv
import time
import subprocess
import webbrowser
from urllib.parse import quote
from typing import List, Optional
from html import escape as html_escape

from PyQt5.QtCore import Qt, QRegExp
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QMenu, QMessageBox, QTableView, QCheckBox, QInputDialog, QDialog,
    QTableWidget, QTableWidgetItem, QDialogButtonBox,
)

from msys.core.utils import (
    _get_software_blacklist, ruta_guardado_predeterminada, dialogo_guardar_archivo,
    _SafeCsvWriter, run_powershell_json_params, run_powershell_command_params,
    escape_powershell_string, winget_esta_bloqueado,
    _software_winget_id, _set_software_winget_link, _quitar_software_winget_link,
)
from msys.models.rows import InstalledSoftwareRow
from msys.models.table_models import InstalledSoftwareTableModel, InstalledSoftwareFilterProxy
from msys.widgets.delegates import _setup_header
from msys.collectors import InstalledSoftwareCollector
from msys.ps_scripts import PS_WINGET_SEARCH, PS_WINGET_PIN_ADD, PS_WINGET_PIN_REMOVE


def _extraer_guid_msi(uninstall_str: str) -> Optional[str]:
    """Si el UninstallString es de msiexec, devuelve el GUID del producto
    (p. ej. '{AC76BA86-...}') — necesario tanto para desinstalar como para
    reparar (msiexec /X{GUID} / /f{GUID}). None si no es un paquete MSI o
    no se pudo extraer el GUID."""
    if not uninstall_str or not re.search(r'msiexec', uninstall_str, re.IGNORECASE):
        return None
    m = re.search(r'\{[A-F0-9\-]+\}', uninstall_str, re.IGNORECASE)
    return m.group() if m else None


class SoftwareMixin:
    def crear_tab_installed_software(self):
        """Crea la pestaña de software instalado."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        # Barra de búsqueda y acciones — una sola fila: hostname, búsqueda,
        # hueco elástico, acciones independientes de selección, Actualizar.
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)

        import socket
        hostname = socket.gethostname()
        lbl_hostname = QLabel(f"🖥️ {hostname}")
        lbl_hostname.setObjectName("hostnameLabel")
        actions_layout.addWidget(lbl_hostname)

        self.search_software = QLineEdit()
        self.search_software.setPlaceholderText("🔎 Buscar software...")
        self.search_software.textChanged.connect(self._filtrar_software_texto)
        actions_layout.addWidget(self.search_software, 3)

        actions_layout.addStretch()

        self.btn_software_blacklist = QPushButton("🚫 Lista negra")
        self.btn_software_blacklist.setObjectName("ghost")
        self.btn_software_blacklist.clicked.connect(self._editar_lista_negra_software)
        actions_layout.addWidget(self.btn_software_blacklist)

        # Independiente de la fila seleccionada — mismo criterio que el
        # resto de "Abrir X nativo de Windows" en otras pestañas.
        self.btn_abrir_programas_caracteristicas = QPushButton("📦 Abrir Programas y características")
        self.btn_abrir_programas_caracteristicas.setObjectName("ghost")
        self.btn_abrir_programas_caracteristicas.clicked.connect(lambda: subprocess.Popen(
            ["powershell", "-Command", "Start-Process appwiz.cpl"],
            startupinfo=self._get_startupinfo()
        ))
        actions_layout.addWidget(self.btn_abrir_programas_caracteristicas)

        self.btn_refresh_software = QPushButton("🔄 Actualizar")
        self.btn_refresh_software.setObjectName("primary")
        self.btn_refresh_software.clicked.connect(self.refresh_installed_software)
        actions_layout.addWidget(self.btn_refresh_software)

        layout.addLayout(actions_layout)

        # Segunda fila: filtros (checkboxes independientes, combinables en
        # AND) + contador a la derecha.
        filtros_layout = QHBoxLayout()
        filtros_layout.setSpacing(8)

        self.chk_software_microsoft = QCheckBox("🏢 Microsoft")
        self.chk_software_otros = QCheckBox("🏭 Otros fabricantes")
        self.chk_software_antiguos = QCheckBox("🕰️ Antiguos (más de 2 años)")
        self.chk_software_eol = QCheckBox("⚠️ Sin soporte (EOL)")
        self.chk_software_no_autorizado = QCheckBox("🚫 No autorizado")
        # Ocultos por defecto (ver InstalledSoftwareFilterProxy): al
        # contrario que el resto de checkboxes, este no estrecha la lista
        # (AND) sino que añade de vuelta filas que se ocultan por defecto.
        self.chk_software_componentes = QCheckBox("🧩 Mostrar componentes del sistema")
        for chk in (self.chk_software_microsoft, self.chk_software_otros, self.chk_software_antiguos,
                    self.chk_software_eol, self.chk_software_no_autorizado, self.chk_software_componentes):
            chk.stateChanged.connect(self._filtrar_software_categoria)
            filtros_layout.addWidget(chk)

        filtros_layout.addStretch()

        self.lbl_software_count = QLabel("0 / 0")
        self.lbl_software_count.setObjectName("countLabel")
        filtros_layout.addWidget(self.lbl_software_count)

        layout.addLayout(filtros_layout)

        # Tabla
        self.software_model = InstalledSoftwareTableModel([])
        self.software_proxy = InstalledSoftwareFilterProxy(self)
        self.software_proxy.setSourceModel(self.software_model)
        self.software_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.software_proxy.setFilterKeyColumn(-1)

        self.software_table = QTableView()
        self.software_table.setModel(self.software_proxy)
        self.software_table.setAlternatingRowColors(True)
        self.software_table.setSelectionBehavior(QTableView.SelectRows)
        self.software_table.setSelectionMode(QTableView.SingleSelection)
        self.software_table.verticalHeader().setVisible(False)
        self.software_table.setSortingEnabled(True)
        self.software_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.software_table.customContextMenuRequested.connect(self._ctx_software)

        _setup_header(self.software_table, len(InstalledSoftwareTableModel.HEADERS), stretch_col=0)

        layout.addWidget(self.software_table)

        return tab

    def refresh_installed_software(self):
        """Actualiza lista de software instalado."""
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.set_status("🔄 Escaneando software instalado...")
        self.btn_refresh_software.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.worker = InstalledSoftwareCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_software_data)
        self.worker.finished_error.connect(self._on_software_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _editar_lista_negra_software(self):
        """Gestión de la lista negra de software: ver, añadir y quitar
        fragmentos de nombre que marcan un programa como 'No autorizado' en
        la columna Estado de Software Instalado — mismo patrón de tabla +
        añadir/quitar que el resto de listas gestionables de Soporte (p. ej.
        'Certificados validados', msys/ui/soporte.py), no un cuadro de texto
        libre suelto."""
        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle("Lista negra de software")
        dialog.setMinimumSize(520, 420)
        v = QVBoxLayout(dialog)

        v.addWidget(QLabel(
            "Fragmentos de nombre (sin distinguir mayúsculas) que marcan un programa como "
            "«No autorizado» en la columna Estado de Software Instalado."))

        tabla = QTableWidget(0, 1)
        tabla.setHorizontalHeaderLabels(["Fragmento"])
        tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        tabla.setSelectionBehavior(QTableWidget.SelectRows)
        tabla.setSelectionMode(QTableWidget.SingleSelection)
        tabla.verticalHeader().setVisible(False)
        _setup_header(tabla, 1, stretch_col=0)
        v.addWidget(tabla)

        lbl_vacio = QLabel("La lista negra está vacía.")
        lbl_vacio.setVisible(False)
        v.addWidget(lbl_vacio)

        fragmentos = list(_get_software_blacklist())

        def _recargar():
            tabla.setSortingEnabled(False)
            tabla.setRowCount(len(fragmentos))
            for fila, frag in enumerate(fragmentos):
                tabla.setItem(fila, 0, QTableWidgetItem(frag))
            tabla.setSortingEnabled(True)
            tabla.model().layoutChanged.emit()
            lbl_vacio.setVisible(not fragmentos)
            btn_quitar.setEnabled(False)

        fila_anadir = QHBoxLayout()
        txt_nuevo = QLineEdit()
        txt_nuevo.setPlaceholderText("p. ej.: utorrent, cracked, keygen...")
        fila_anadir.addWidget(txt_nuevo, 1)
        btn_anadir = QPushButton("➕ Añadir")
        fila_anadir.addWidget(btn_anadir)
        v.addLayout(fila_anadir)

        fila_botones = QHBoxLayout()
        btn_quitar = QPushButton("✕ Quitar seleccionado")
        btn_quitar.setEnabled(False)
        fila_botones.addWidget(btn_quitar)
        fila_botones.addStretch()
        v.addLayout(fila_botones)

        tabla.itemSelectionChanged.connect(
            lambda: btn_quitar.setEnabled(bool(tabla.selectionModel().selectedRows())))

        def _anadir():
            nuevo = txt_nuevo.text().strip().lower()
            if not nuevo:
                return
            if nuevo in fragmentos:
                self._avisar("Lista negra", f"«{nuevo}» ya está en la lista.")
                return
            fragmentos.append(nuevo)
            txt_nuevo.clear()
            _recargar()

        def _quitar():
            filas_sel = tabla.selectionModel().selectedRows()
            if not filas_sel:
                return
            fragmentos.remove(tabla.item(filas_sel[0].row(), 0).text())
            _recargar()

        txt_nuevo.returnPressed.connect(_anadir)
        btn_anadir.clicked.connect(_anadir)
        btn_quitar.clicked.connect(_quitar)

        _recargar()

        botones = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        botones.accepted.connect(dialog.accept)
        botones.rejected.connect(dialog.reject)
        v.addWidget(botones)

        if dialog.exec_() == QDialog.Accepted:
            self.settings.setValue("software_blacklist", ", ".join(fragmentos))
            self.software_model.refresh_blacklist()
            self.software_proxy.refresh_blacklist()
            self._update_software_count()
            self.set_status("✅ Lista negra de software actualizada")

    def _filtrar_software_texto(self):
        self.software_proxy.setFilterRegExp(QRegExp(self.search_software.text(), Qt.CaseInsensitive, QRegExp.FixedString))
        self._update_software_count()

    def _filtrar_software_categoria(self):
        self.software_proxy.setFiltros(
            self.chk_software_microsoft.isChecked(),
            self.chk_software_otros.isChecked(),
            self.chk_software_antiguos.isChecked(),
            self.chk_software_eol.isChecked(),
            self.chk_software_no_autorizado.isChecked(),
            self.chk_software_componentes.isChecked(),
        )
        self._update_software_count()

    def _update_software_count(self):
        self.lbl_software_count.setText(f"{self.software_proxy.rowCount()} / {self.software_model.rowCount()}")

    def _on_software_data(self, rows: List[InstalledSoftwareRow]):
        """Procesa datos de software instalado."""
        self.software_model.setRows(rows)
        self.btn_refresh_software.setEnabled(True)
        self.progress_bar.setVisible(False)
        total = len(rows)
        self._update_software_count()
        self.set_status(f"✅ {total} aplicaciones encontradas")

    def _on_software_error(self, msg: str):
        """Maneja errores de software instalado."""
        self.btn_refresh_software.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al escanear software instalado")
        self._avisar("Error", f"No se pudo obtener el software instalado:\n\n{msg}", QMessageBox.Warning)

    def _software_seleccionado(self):
        sel = self.software_table.selectionModel()
        if not sel or not sel.hasSelection():
            return None
        idx = sel.selectedRows()[0]
        source_index = self.software_proxy.mapToSource(idx)
        rows = self.software_model.rows()
        if 0 <= source_index.row() < len(rows):
            return rows[source_index.row()]
        return None

    def _ctx_software(self, pos):
        idx = self.software_table.indexAt(pos)
        if not idx.isValid():
            return
        self.software_table.selectRow(idx.row())
        sw_row = self._software_seleccionado()
        if not sw_row:
            return

        carpeta = (sw_row.carpeta_instalacion or "").strip()
        guid_msi = _extraer_guid_msi(sw_row.uninstall_string)
        winget_id = _software_winget_id(sw_row.nombre)

        menu = QMenu(self)
        act_uninstall = menu.addAction("✕ Desinstalar")
        act_uninstall.setEnabled(bool(sw_row.uninstall_string))
        act_reparar = menu.addAction("🔧 Reparar (solo paquetes MSI)")
        act_reparar.setEnabled(bool(guid_msi))
        menu.addSeparator()
        act_carpeta = menu.addAction("📂 Abrir carpeta de instalación")
        act_carpeta.setEnabled(bool(carpeta) and carpeta.upper() != "N/A")
        act_buscar = menu.addAction("🔍 Buscar actualización")
        menu.addSeparator()
        if winget_id:
            act_vincular_winget = menu.addAction(f"🔗 Cambiar vínculo winget (actual: {winget_id})")
        else:
            act_vincular_winget = menu.addAction("🔗 Vincular con winget...")
        act_actualizar_winget = menu.addAction("⬆️ Actualizar vía winget")
        act_actualizar_winget.setEnabled(bool(winget_id))
        act_bloqueo_winget = menu.addAction("🔒/🔓 Bloquear o desbloquear actualización (winget)")
        act_bloqueo_winget.setEnabled(bool(winget_id))
        act_quitar_vinculo_winget = menu.addAction("❌ Quitar vínculo winget")
        act_quitar_vinculo_winget.setEnabled(bool(winget_id))
        menu.addSeparator()
        act_copy = menu.addAction("📋 Copiar fila")

        action = menu.exec_(self.software_table.viewport().mapToGlobal(pos))
        if action == act_uninstall:
            self._desinstalar_software(sw_row.nombre, sw_row.uninstall_string, sw_row.system_component)
        elif action == act_reparar:
            self._reparar_software(sw_row.nombre, guid_msi)
        elif action == act_carpeta:
            self._abrir_carpeta_software(carpeta)
        elif action == act_buscar:
            self._buscar_actualizacion_software(sw_row.nombre, sw_row.editor)
        elif action == act_vincular_winget:
            self._vincular_winget_software(sw_row.nombre)
        elif action == act_actualizar_winget:
            self._actualizar_software_via_winget(sw_row.nombre, winget_id)
        elif action == act_bloqueo_winget:
            self._alternar_bloqueo_winget_software(sw_row.nombre, winget_id)
        elif action == act_quitar_vinculo_winget:
            self._quitar_vinculo_winget_software(sw_row.nombre)
        elif action == act_copy:
            cols = [self.software_proxy.data(self.software_proxy.index(idx.row(), c), Qt.DisplayRole)
                    for c in range(len(InstalledSoftwareTableModel.HEADERS))]
            QApplication.clipboard().setText("\t".join(str(v or "") for v in cols))

    def _desinstalar_software(self, nombre: str, uninstall_str: str, system_component: bool = False):
        if system_component:
            # Doble aviso reforzado: son runtimes/subcomponentes (VC++
            # redistributables, piezas de drivers...) de los que puede
            # depender OTRO software ya instalado — desinstalarlos puede
            # romper una app distinta sin relación aparente con el nombre.
            texto = (
                f"⚠️ «{nombre}» es un COMPONENTE DEL SISTEMA (runtime o subcomponente), no una "
                "aplicación instalada por el usuario.\n\n"
                "Es posible que otro software ya instalado dependa de él (p. ej. un Visual C++ "
                "Redistributable) — desinstalarlo puede romper esa otra aplicación sin relación "
                "aparente con el nombre mostrado aquí.\n\n"
                "Esta acción no es reversible. ¿Continuar de todas formas?"
            )
            resp = QMessageBox.warning(
                self, "⚠️ Desinstalar componente del sistema", texto,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if resp != QMessageBox.Yes:
                return
            resp2 = QMessageBox.warning(
                self, "Confirmación final",
                f"Última confirmación: se va a desinstalar el componente del sistema «{nombre}».",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if resp2 != QMessageBox.Yes:
                return
        else:
            resp = QMessageBox.warning(
                self, "Desinstalar software",
                f"¿Desinstalar «{nombre}»?\n\nEsta acción no es reversible.\n"
                f"Se ejecutará el desinstalador del fabricante.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if resp != QMessageBox.Yes:
                return

        self.set_status(f"🔄 Desinstalando {nombre}...")
        try:
            guid = _extraer_guid_msi(uninstall_str)
            if guid:
                subprocess.Popen(["msiexec.exe", f"/X{guid}", "/qb!"], startupinfo=None)
            else:
                # Sin shell=True: en Windows, CreateProcess acepta la cadena de
                # comando completa (ejecutable + argumentos citados) directamente,
                # sin pasar por cmd.exe ni interpretar metacaracteres de shell
                # (&, |, %VAR%...) presentes en el UninstallString del registro.
                subprocess.Popen(uninstall_str)
            self.set_status(f"✅ Desinstalador de «{nombre}» iniciado")
            if hasattr(self, "repair_audit_db"):
                self.repair_audit_db.registrar(
                    seccion="📦 Software Instalado", accion="Desinstalar software",
                    objetivo=nombre, exito=True, detalle="Desinstalador iniciado",
                )
            self._avisar("Desinstalar", f"El desinstalador de «{nombre}» se ha iniciado.\n\n"
                "Sigue las instrucciones en pantalla. Después actualiza la lista con 🔄.")
        except Exception as e:
            self.set_status("❌ Error al iniciar desinstalador")
            if hasattr(self, "repair_audit_db"):
                self.repair_audit_db.registrar(
                    seccion="📦 Software Instalado", accion="Desinstalar software",
                    objetivo=nombre, exito=False, detalle=str(e),
                )
            self._avisar("Error", f"No se pudo iniciar el desinstalador:\n\n{e}", QMessageBox.Critical)

    def _reparar_software(self, nombre: str, guid_msi: str):
        """Reparación estándar de Windows Installer (msiexec /f) — reinstala
        archivos/registro/atajos dañados o que faltan sin desinstalar
        primero. Solo disponible para paquetes MSI (no todo instalador usa
        Windows Installer, p. ej. NSIS/Inno Setup no tienen esta opción)."""
        if not self._confirmar_accion(
            "Reparar software",
            f"¿Reparar «{nombre}»?\n\nWindows Installer reinstalará los archivos, entradas de "
            "registro y accesos directos dañados o que falten, sin desinstalar la aplicación.",
        ):
            return
        self.set_status(f"🔄 Reparando {nombre}...")
        try:
            subprocess.Popen(["msiexec.exe", f"/f{guid_msi}", "/qb!"], startupinfo=None)
            self.set_status(f"✅ Reparación de «{nombre}» iniciada")
            if hasattr(self, "repair_audit_db"):
                self.repair_audit_db.registrar(
                    seccion="📦 Software Instalado", accion="Reparar software",
                    objetivo=nombre, exito=True, detalle="Reparación MSI iniciada",
                )
        except Exception as e:
            self.set_status("❌ Error al iniciar la reparación")
            if hasattr(self, "repair_audit_db"):
                self.repair_audit_db.registrar(
                    seccion="📦 Software Instalado", accion="Reparar software",
                    objetivo=nombre, exito=False, detalle=str(e),
                )
            self._avisar("Error", f"No se pudo iniciar la reparación:\n\n{e}", QMessageBox.Critical)

    def _abrir_carpeta_software(self, carpeta: str):
        if not carpeta or carpeta.upper() == "N/A" or not os.path.isdir(carpeta):
            self._avisar("Abrir carpeta", f"La carpeta no existe o no está accesible:\n\n{carpeta}", QMessageBox.Warning)
            return
        subprocess.Popen(["explorer.exe", carpeta])

    def _buscar_actualizacion_software(self, nombre: str, editor: str):
        """Abre una búsqueda web con el editor y el nombre — NUNCA descarga
        ni ejecuta nada, mismo criterio que 'Buscar controlador oficial' en
        Adm. Dispositivos: no existe una API universal de actualizaciones
        para software de terceros."""
        editor_limpio = "" if (editor or "").strip().upper() == "N/A" else (editor or "").strip()
        consulta = f"{editor_limpio} {nombre} descargar última versión".strip()
        url = f"https://www.bing.com/search?q={quote(consulta)}"
        webbrowser.open(url)
        self.set_status(f"🔍 Búsqueda abierta en el navegador: {consulta}")

    def _vincular_winget_software(self, nombre: str):
        """Busca en el catálogo de winget y guarda manualmente el Id exacto
        para este software (QSettings, ver core/utils.py) — necesario para
        software que 'winget upgrade' no relaciona bien por su cuenta
        (nombres genéricos, apps internas...). Una vez vinculado, 'Actualizar
        vía winget' usa este Id directamente sin depender de que winget
        vuelva a adivinarlo."""
        dlg = QInputDialog(self)
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dlg.setWindowTitle("Vincular con winget")
        dlg.setLabelText("Término de búsqueda en el catálogo de winget:")
        dlg.setTextValue(nombre)
        if dlg.exec_() != QDialog.Accepted:
            return
        texto = dlg.textValue().strip()
        if not texto:
            return

        self.set_status(f"🔎 Buscando «{texto}» en winget...")
        # 5.5.0: la búsqueda (hasta 30 s) va en segundo plano; antes congelaba la ventana
        from msys.core import tareas
        tareas.lanzar(self, "_tarea_winget", run_powershell_json_params, PS_WINGET_SEARCH, timeout=30, query=texto,
                      al_terminar=lambda data, n=nombre, t=texto: self._vincular_winget_resultado(n, t, data),
                      al_fallar=lambda m: self._avisar("Vincular con winget", f"La búsqueda falló:\n\n{m}",
                                                       QMessageBox.Warning))

    def _vincular_winget_resultado(self, nombre: str, texto: str, data):
        self.set_status("")
        if not isinstance(data, dict) or not data.get("Disponible", False):
            self._avisar("winget no disponible", "No se encontró 'winget' en este equipo. Instálalo desde la "
                "Microsoft Store (App Installer) para poder vincular software.", QMessageBox.Warning)
            return
        paquetes = data.get("Paquetes") or []
        if isinstance(paquetes, dict):
            paquetes = [paquetes]
        if not paquetes:
            self._avisar("Vincular con winget", f"No se encontraron paquetes en winget para «{texto}».", QMessageBox.Warning)
            return

        opciones = [f"{p.get('Nombre', '')}  —  {p.get('Id', '')}  (v{p.get('Version', 'N/A')})" for p in paquetes]
        dlg2 = QInputDialog(self)
        dlg2.setWindowFlags(dlg2.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dlg2.setWindowTitle("Vincular con winget")
        dlg2.setLabelText(f"{len(paquetes)} resultado(s) para «{texto}» — elige el paquete correcto:")
        dlg2.setComboBoxItems(opciones)
        dlg2.setComboBoxEditable(False)
        if dlg2.exec_() != QDialog.Accepted:
            return
        paquete = paquetes[opciones.index(dlg2.textValue())]
        winget_id = paquete.get("Id", "")
        if not winget_id:
            return

        _set_software_winget_link(nombre, winget_id)
        self.set_status(f"✅ «{nombre}» vinculado a winget: {winget_id}")
        self._avisar("Vincular con winget", f"«{nombre}» queda vinculado al paquete winget:\n\n{winget_id}\n\n"
            "Ya puedes usar «⬆️ Actualizar vía winget» desde el menú contextual.")

    def _actualizar_software_via_winget(self, nombre: str, winget_id: str):
        """Ejecuta 'winget upgrade --id' con el Id vinculado — mismo comando
        base que usa PackageBatchUpgradeCollector en msys/collectors.py
        (Gestor de Paquetes), pero disparado desde un vínculo manual en vez
        de depender de que winget relacione el software por su cuenta."""
        if not winget_id:
            return
        if not self._confirmar_accion(
            "Actualizar vía winget",
            f"¿Actualizar «{nombre}» mediante winget ({winget_id})?\n\nEsto puede tardar varios minutos.",
        ):
            return
        id_escaped = escape_powershell_string(winget_id)
        self.set_status(f"🔄 Actualizando {nombre} vía winget (puede tardar varios minutos)...")
        startupinfo = self._get_startupinfo()

        def _trabajo():
            try:
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     f"winget upgrade --id '{id_escaped}' --silent --accept-package-agreements --accept-source-agreements"],
                    capture_output=True, text=True, timeout=300, startupinfo=startupinfo)
            except subprocess.TimeoutExpired:
                return False, "La actualización tardó más de 5 minutos."
            return r.returncode == 0, "" if r.returncode == 0 else (r.stderr or r.stdout or "").strip()

        def _hecho(resultado):
            exito, detalle = resultado
            if hasattr(self, "repair_audit_db"):
                self.repair_audit_db.registrar(
                    seccion="📦 Software Instalado", accion="Actualizar vía winget",
                    objetivo=f"{nombre} ({winget_id})", exito=exito, detalle=detalle or "OK",
                )
            if exito:
                self.set_status(f"✅ {nombre} actualizado correctamente vía winget")
                self._avisar("Actualizar vía winget", f"«{nombre}» se actualizó correctamente.")
                self.refresh_installed_software()
            else:
                self.set_status("❌ Error al actualizar vía winget")
                self._avisar("Error", f"No se pudo actualizar «{nombre}»:\n\n{detalle or 'Sin detalles'}",
                             QMessageBox.Warning)

        # 5.5.0: antes subprocess.run(timeout=300) en la propia ventana: MASys congelado hasta 5 minutos
        from msys.core import tareas
        tareas.lanzar(self, "_tarea_winget", _trabajo, al_terminar=_hecho,
                      al_fallar=lambda m: self._avisar("Error", f"No se pudo actualizar «{nombre}»:\n\n{m}",
                                                       QMessageBox.Warning))

    def _alternar_bloqueo_winget_software(self, nombre: str, winget_id: str):
        """Bloquear/Desbloquear actualización vía winget pin — mismo
        mecanismo y misma UX que Gestor de Paquetes
        (_alternar_bloqueo_paquete en package_manager.py), para el software
        vinculado manualmente aquí. Sin tabla de estado propia en esta
        pestaña: se consulta bajo demanda (winget_esta_bloqueado) solo al
        elegir esta acción, no al abrir el menú contextual (evitaría una
        llamada a winget en CADA clic derecho de la tabla)."""
        if not winget_id:
            return
        # 5.5.0: la consulta a winget va en segundo plano (antes congelaba la ventana)
        from msys.core import tareas
        self.set_status(f"🔎 Consultando si «{nombre}» está bloqueado en winget...")
        tareas.lanzar(self, "_tarea_winget", winget_esta_bloqueado, winget_id,
                      al_terminar=lambda b, n=nombre, i=winget_id: self._alternar_bloqueo_winget_continuar(n, i, b),
                      al_fallar=lambda m: self._avisar("winget", f"No se pudo consultar winget:\n\n{m}",
                                                       QMessageBox.Warning))

    def _alternar_bloqueo_winget_continuar(self, nombre: str, winget_id: str, bloqueado: bool):
        self.set_status("")
        if bloqueado:
            titulo = "Desbloquear actualización"
            cambios = [f"«{nombre}» volverá a poder actualizarse mediante winget"]
            plantilla = PS_WINGET_PIN_REMOVE
        else:
            titulo = f"Bloquear actualización — {nombre}"
            cambios = [
                f"«{nombre}» quedará bloqueada para winget (ni de forma individual ni en lote) "
                "hasta que se desbloquee de nuevo",
            ]
            plantilla = PS_WINGET_PIN_ADD

        if not self._confirmar_reparacion(
            titulo, cambios=cambios, reversible=True, objetivo=nombre,
            explicacion=None if bloqueado else (
                "El bloqueo solo afecta a actualizaciones gestionadas por winget: si la propia "
                "aplicación tiene su actualizador interno, puede seguir actualizándose por sí "
                "sola pese al bloqueo."),
        ):
            return

        motivo = ""
        if not bloqueado:
            dlg_motivo = QInputDialog(self)
            dlg_motivo.setWindowFlags(dlg_motivo.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            dlg_motivo.setWindowTitle("Motivo del bloqueo (opcional)")
            dlg_motivo.setLabelText(
                f"¿Por qué se bloquea «{nombre}»? (opcional — para que quien lo vea después sepa por qué)")
            dlg_motivo.setInputMode(QInputDialog.TextInput)
            if dlg_motivo.exec_() == QDialog.Accepted:
                motivo = dlg_motivo.textValue().strip()

        ok, msg = run_powershell_command_params(plantilla, id=winget_id)
        if ok and hasattr(self, "package_block_notes_db"):
            if bloqueado:
                self.package_block_notes_db.delete_motivo(winget_id)
            elif motivo:
                self.package_block_notes_db.save_motivo(winget_id, motivo)
        if hasattr(self, "repair_audit_db"):
            self.repair_audit_db.registrar(
                seccion="📦 Software Instalado",
                accion="Desbloquear actualización" if bloqueado else "Bloquear actualización",
                objetivo=f"{nombre} ({winget_id})", exito=ok,
                detalle=(motivo if motivo else "OK") if ok else msg,
            )
        if ok:
            self.set_status(f"✅ {'Desbloqueada' if bloqueado else 'Bloqueada'}: {nombre}")
        else:
            self._avisar(
                "Error", f"No se pudo {'desbloquear' if bloqueado else 'bloquear'} «{nombre}»:\n\n{msg}",
                QMessageBox.Warning)

    def _quitar_vinculo_winget_software(self, nombre: str):
        _quitar_software_winget_link(nombre)
        self.set_status(f"✅ Vínculo winget de «{nombre}» eliminado")

    def copy_software_selection(self):
        """Copia software seleccionado al portapapeles."""
        selected_indexes = self.software_table.selectionModel().selectedRows()
        if not selected_indexes:
            self._avisar("Copiar", "No hay software seleccionado.")
            return

        lines = []
        lines.append("=" * 80)
        lines.append("SOFTWARE INSTALADO")
        lines.append("=" * 80)
        lines.append("")

        for index in selected_indexes:
            row = index.row()
            nombre = str(self.software_proxy.data(self.software_proxy.index(row, 0), Qt.DisplayRole) or "")
            version = str(self.software_proxy.data(self.software_proxy.index(row, 1), Qt.DisplayRole) or "")
            editor = str(self.software_proxy.data(self.software_proxy.index(row, 2), Qt.DisplayRole) or "")
            carpeta = str(self.software_proxy.data(self.software_proxy.index(row, 4), Qt.DisplayRole) or "")

            lines.append(f"{nombre}")
            lines.append(f"  Versión: {version} | Editor: {editor}")
            if carpeta and carpeta != "N/A":
                lines.append(f"  Carpeta: {carpeta}")
            lines.append("")

        lines.append("=" * 80)

        text = "\n".join(lines)
        QApplication.clipboard().setText(text)
        self.set_status(f"✅ Copiado {len(selected_indexes)} software al portapapeles")

    def export_software_csv(self):
        """Exporta software instalado a CSV."""
        if self.software_proxy.rowCount() == 0:
            self._avisar("Exportar CSV", "No hay software para exportar.")
            return

        default_name = f"SoftwareInstalado_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        path = dialogo_guardar_archivo(self, "Guardar CSV", ruta_guardado_predeterminada(default_name), "CSV (*.csv)")
        if not path:
            return

        try:
            headers = InstalledSoftwareTableModel.HEADERS
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = _SafeCsvWriter(csv.writer(f, delimiter=";"))
                writer.writerow(headers)
                for r in range(self.software_proxy.rowCount()):
                    row_data = []
                    for c in range(len(headers)):
                        idx = self.software_proxy.index(r, c)
                        val = self.software_proxy.data(idx, Qt.DisplayRole)
                        row_data.append(str(val) if val else "")
                    writer.writerow(row_data)

            total_exported = self.software_proxy.rowCount()
            self.set_status(f"✅ Exportado: {os.path.basename(path)} ({total_exported} aplicaciones)")
            self._avisar("Éxito", f"Exportadas {total_exported} aplicaciones a:\n\n{path}")
        except Exception as e:
            self._avisar("Error", f"No se pudo exportar:\n\n{e}", QMessageBox.Critical)

    def export_software_html(self):
        """Exporta software instalado a HTML."""
        if self.software_proxy.rowCount() == 0:
            self._avisar("Exportar HTML", "No hay software para exportar.")
            return

        default_name = f"SoftwareInstalado_{time.strftime('%Y%m%d_%H%M%S')}.html"
        path = dialogo_guardar_archivo(self, "Guardar HTML", ruta_guardado_predeterminada(default_name), "HTML (*.html)")
        if not path:
            return

        try:
            headers = InstalledSoftwareTableModel.HEADERS
            total_rows = self.software_proxy.rowCount()

            rows_data = []
            for r in range(total_rows):
                row_data = []
                for c in range(len(headers)):
                    idx = self.software_proxy.index(r, c)
                    val = self.software_proxy.data(idx, Qt.DisplayRole)
                    row_data.append(str(val) if val else "")
                rows_data.append(row_data)

            html = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Software Instalado - {time.strftime('%Y-%m-%d %H:%M:%S')}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 20px; }}
        .container {{ max-width: 100%; background: white; border-radius: 10px; box-shadow: 0 10px 40px rgba(0,0,0,0.3); overflow: hidden; }}
        header {{ background: linear-gradient(135deg, #2c3e50 0%, #34495e 100%); color: white; padding: 30px; }}
        h1 {{ font-size: 28px; margin-bottom: 10px; }}
        .subtitle {{ font-size: 14px; opacity: 0.9; }}
        table {{ width: 100%; border-collapse: collapse; }}
        thead {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; }}
        th {{ padding: 15px 10px; text-align: left; font-weight: 600; font-size: 13px; }}
        tbody tr {{ border-bottom: 1px solid #e9ecef; transition: background 0.2s; }}
        tbody tr:hover {{ background: #f8f9fa; }}
        td {{ padding: 12px 10px; font-size: 13px; color: #2c3e50; }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Software Instalado</h1>
            <div class="subtitle">Generado el {time.strftime('%d/%m/%Y a las %H:%M:%S')}</div>
        </header>
        <table>
            <thead>
                <tr>
"""
            for h in headers:
                html += f"                    <th>{h}</th>\n"
            html += """                </tr>
            </thead>
            <tbody>
"""
            for row in rows_data:
                html += "                <tr>\n"
                for cell in row:
                    html += f"                    <td>{html_escape(str(cell))}</td>\n"
                html += "                </tr>\n"

            html += """            </tbody>
        </table>
    </div>
</body>
</html>
"""

            with open(path, "w", encoding="utf-8") as f:
                f.write(html)

            self.set_status(f"✅ Exportado: {os.path.basename(path)} ({total_rows} aplicaciones)")
            self._avisar("Éxito", f"Exportadas {total_rows} aplicaciones a:\n\n{path}")
        except Exception as e:
            self._avisar("Error", f"No se pudo exportar:\n\n{e}", QMessageBox.Critical)
