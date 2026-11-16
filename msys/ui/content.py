# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña Contenido — búsqueda de archivos y
carpetas individuales (nombre/regex, extensión, tamaño, fecha) acotada a
un ámbito exclusivo (Perfil Usuario / Escritorio / Sin filtros). El botón
"Analizar" original pasa a ser "Buscar" + "Detener": ya no hay un
inventario agregado aparte, la búsqueda ES la pestaña.

"Sin filtros" activa el campo "Ubicación" (deshabilitado en los otros dos
ámbitos): en vez de una carpeta fija, se busca en la ruta que el usuario
elija ahí — sustituye a la antigua categoría "C:\\ No Común".

Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).

Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

import os
import subprocess
import time

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QCheckBox,
    QPushButton, QMessageBox, QTableView, QRadioButton, QButtonGroup,
    QComboBox, QAbstractItemView, QApplication, QStackedWidget,
    QTreeWidget, QTreeWidgetItem, QProgressBar,
)

from msys.models.table_models import ContentTableModel, ContentFilterProxy
from msys.widgets.delegates import _setup_header
from msys.collectors import (
    ContentSearchEngine, ContentTrashThread, ContentCopyMoveThread, ContentZipThread,
)
from msys.core.utils import (
    get_user_content_folders, dialogo_guardar_archivo,
    ruta_guardado_predeterminada, dialogo_elegir_carpeta, preguntar_colision,
)

EXTENSION_PRESETS = {
    "Imágenes": ".jpg,.jpeg,.png,.gif,.bmp,.tiff,.webp,.svg,.ico",
    "Documentos": ".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.txt,.odt,.rtf,.csv",
    "Vídeo": ".mp4,.avi,.mkv,.mov,.wmv,.flv,.webm,.m4v",
    "Audio": ".mp3,.wav,.flac,.aac,.ogg,.wma,.m4a",
    "Comprimidos": ".zip,.rar,.7z,.tar,.gz,.bz2,.xz",
    "Código": ".py,.js,.html,.css,.java,.cpp,.c,.h,.cs,.php,.ts,.json,.xml",
}


class ContenidoMixin:
    def crear_tab_contenido(self):
        """Crea la pestaña de Contenido del sistema."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        # Fila 1: hostname, término de búsqueda, hueco elástico, Buscar/Detener.
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)

        import socket
        hostname = socket.gethostname()
        lbl_hostname = QLabel(f"🖥️ {hostname}")
        lbl_hostname.setObjectName("hostnameLabel")
        actions_layout.addWidget(lbl_hostname)

        self.search_content = QLineEdit()
        self.search_content.setPlaceholderText("🔎 Buscar contenido...")
        self.search_content.returnPressed.connect(self.refresh_content)
        actions_layout.addWidget(self.search_content, 3)

        self.txt_content_ubicacion = QLineEdit()
        self.txt_content_ubicacion.setPlaceholderText("Ubicación...")
        self.txt_content_ubicacion.setReadOnly(True)  # se elige con el botón, no se escribe a mano
        self.txt_content_ubicacion.setEnabled(False)
        actions_layout.addWidget(self.txt_content_ubicacion, 2)
        self.btn_content_ubicacion = QPushButton("📂")
        self.btn_content_ubicacion.setFixedWidth(56)
        self.btn_content_ubicacion.setToolTip("Elegir ubicación de búsqueda")
        self.btn_content_ubicacion.setEnabled(False)
        self.btn_content_ubicacion.clicked.connect(self._elegir_ubicacion_content)
        actions_layout.addWidget(self.btn_content_ubicacion)

        actions_layout.addStretch()

        self.btn_refresh_content = QPushButton("🔍 Buscar")
        self.btn_refresh_content.setObjectName("primary")
        self.btn_refresh_content.setToolTip(
            "Busca archivos y carpetas en el ámbito marcado abajo")
        self.btn_refresh_content.clicked.connect(self.refresh_content)
        actions_layout.addWidget(self.btn_refresh_content)

        self.btn_detener_content = QPushButton("⏹ Detener")
        self.btn_detener_content.setEnabled(False)
        self.btn_detener_content.clicked.connect(self.detener_content)
        actions_layout.addWidget(self.btn_detener_content)

        layout.addLayout(actions_layout)

        # Fila 2: ámbito de búsqueda (exactamente uno de los 3 a la vez) +
        # tipo + subcarpetas + regex, todo junto en la misma línea.
        filters_layout = QHBoxLayout()
        filters_layout.setSpacing(12)

        self.chk_content_perfil = QCheckBox("👤 Perfil Usuario")
        self.chk_content_escritorio = QCheckBox("🖥️ Escritorio")
        self.chk_content_sin_filtros = QCheckBox("🌐 Sin filtros")
        self.chk_content_sin_filtros.setToolTip(
            "Busca en la carpeta que elijas en 'Ubicación', no en una categoría fija")
        self._content_scope_checks = [
            self.chk_content_perfil, self.chk_content_escritorio,
            self.chk_content_sin_filtros,
        ]
        for chk in self._content_scope_checks:
            chk.toggled.connect(self._on_content_scope_toggled)
            filters_layout.addWidget(chk)

        filters_layout.addSpacing(10)
        self.grupo_tipo_content = QButtonGroup(tab)
        self.rb_content_archivos = QRadioButton("Archivos")
        self.rb_content_carpetas = QRadioButton("Carpetas")
        self.rb_content_todo = QRadioButton("Todo")
        self.rb_content_todo.setChecked(True)
        for rb in (self.rb_content_archivos, self.rb_content_carpetas, self.rb_content_todo):
            self.grupo_tipo_content.addButton(rb)
            filters_layout.addWidget(rb)
        self.chk_content_subcarpetas = QCheckBox("Incluir subcarpetas")
        self.chk_content_subcarpetas.setChecked(True)
        filters_layout.addWidget(self.chk_content_subcarpetas)
        self.chk_content_regex = QCheckBox("Regex")
        filters_layout.addWidget(self.chk_content_regex)

        filters_layout.addStretch()
        layout.addLayout(filters_layout)

        self.chk_content_sin_filtros.setChecked(True)  # ámbito por defecto

        # Fila 3: extensión + presets + tamaño + fecha.
        criterios_layout = QHBoxLayout()
        criterios_layout.setSpacing(8)
        criterios_layout.addWidget(QLabel("Ext:"))
        self.txt_content_ext = QLineEdit()
        self.txt_content_ext.setPlaceholderText(".jpg,.png,.pdf")
        self.txt_content_ext.setFixedWidth(170)
        criterios_layout.addWidget(self.txt_content_ext)
        self.combo_content_preset = QComboBox()
        self.combo_content_preset.addItem("Presets")
        self.combo_content_preset.addItems(list(EXTENSION_PRESETS.keys()))
        self.combo_content_preset.currentTextChanged.connect(self._aplicar_preset_content)
        criterios_layout.addWidget(self.combo_content_preset)
        criterios_layout.addWidget(QLabel("Min (MB):"))
        self.txt_content_min = QLineEdit()
        self.txt_content_min.setFixedWidth(70)
        criterios_layout.addWidget(self.txt_content_min)
        criterios_layout.addWidget(QLabel("Max (MB):"))
        self.txt_content_max = QLineEdit()
        self.txt_content_max.setFixedWidth(70)
        criterios_layout.addWidget(self.txt_content_max)
        criterios_layout.addWidget(QLabel("Modificado últimos (días):"))
        self.txt_content_dias = QLineEdit()
        self.txt_content_dias.setFixedWidth(60)
        criterios_layout.addWidget(self.txt_content_dias)
        criterios_layout.addStretch()
        layout.addLayout(criterios_layout)

        # Resultados: lista (tabla) o árbol de carpetas — mismo patrón de
        # alternancia que Carpetas Compartidas (shares.py: btn_tree_view +
        # view_stack), aplicado aquí a los resultados de la búsqueda.
        self.content_model = ContentTableModel(self)
        self.content_proxy = ContentFilterProxy(self)
        self.content_proxy.setSourceModel(self.content_model)

        self.content_table = QTableView()
        self.content_table.setModel(self.content_proxy)
        self.content_table.setAlternatingRowColors(True)
        self.content_table.setSelectionBehavior(QTableView.SelectRows)
        self.content_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.content_table.verticalHeader().setVisible(False)
        self.content_table.setSortingEnabled(True)
        _setup_header(self.content_table, len(ContentTableModel.HEADERS), stretch_col=2)

        self.content_tree = QTreeWidget()
        self.content_tree.setHeaderLabels(["Nombre", "Tipo", "Tamaño", "Modificado"])
        self.content_tree.setAlternatingRowColors(True)
        self.content_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        _setup_header(self.content_tree, 4, stretch_col=0)

        self.content_view_stack = QStackedWidget()
        self.content_view_stack.addWidget(self.content_table)  # índice 0: lista
        self.content_view_stack.addWidget(self.content_tree)   # índice 1: árbol
        layout.addWidget(self.content_view_stack, 1)

        # Acciones sobre resultados
        acciones_layout = QHBoxLayout()
        self.btn_content_abrir = QPushButton("Abrir en Explorer")
        self.btn_content_abrir.clicked.connect(self._content_abrir_explorer)
        acciones_layout.addWidget(self.btn_content_abrir)
        self.btn_content_copiar = QPushButton("Copiar ruta")
        self.btn_content_copiar.clicked.connect(self._content_copiar_ruta)
        acciones_layout.addWidget(self.btn_content_copiar)
        self.btn_content_exportar = QPushButton("Exportar")
        self.btn_content_exportar.clicked.connect(self._content_exportar)
        acciones_layout.addWidget(self.btn_content_exportar)
        self.btn_content_copiar_a = QPushButton("📂 Copiar a...")
        self.btn_content_copiar_a.clicked.connect(lambda: self._content_copiar_o_mover("copiar"))
        acciones_layout.addWidget(self.btn_content_copiar_a)
        self.btn_content_mover_a = QPushButton("✂️ Mover a...")
        self.btn_content_mover_a.clicked.connect(lambda: self._content_copiar_o_mover("mover"))
        acciones_layout.addWidget(self.btn_content_mover_a)
        self.btn_content_zip = QPushButton("📦 Comprimir a ZIP")
        self.btn_content_zip.clicked.connect(self._content_comprimir_zip)
        acciones_layout.addWidget(self.btn_content_zip)

        self.btn_content_view_toggle = QPushButton("🌳 Árbol")
        self.btn_content_view_toggle.setObjectName("ghost")
        self.btn_content_view_toggle.setCheckable(True)
        self.btn_content_view_toggle.clicked.connect(self._toggle_content_view_mode)
        acciones_layout.addWidget(self.btn_content_view_toggle)
        self.btn_content_expand_collapse = QPushButton("➕ Expandir Todo")
        self.btn_content_expand_collapse.setObjectName("ghost")
        self.btn_content_expand_collapse.setVisible(False)
        self.btn_content_expand_collapse.clicked.connect(self._toggle_content_expand_collapse)
        acciones_layout.addWidget(self.btn_content_expand_collapse)

        acciones_layout.addStretch()
        self.btn_content_eliminar = QPushButton("✕ Enviar a la papelera")
        self.btn_content_eliminar.setStyleSheet(
            "QPushButton { background-color: #dc2626; color: white; padding: 5px 12px; }"
            "QPushButton:hover { background-color: #b91c1c; }")
        self.btn_content_eliminar.clicked.connect(self._content_eliminar_seleccionados)
        acciones_layout.addWidget(self.btn_content_eliminar)
        layout.addLayout(acciones_layout)

        fila_progreso_bulk = QHBoxLayout()
        self.content_bulk_progreso = QProgressBar()
        self.content_bulk_progreso.setVisible(False)
        fila_progreso_bulk.addWidget(self.content_bulk_progreso, 1)
        self.btn_content_bulk_detener = QPushButton("⏹ Detener")
        self.btn_content_bulk_detener.setVisible(False)
        self.btn_content_bulk_detener.clicked.connect(self._detener_content_bulk)
        fila_progreso_bulk.addWidget(self.btn_content_bulk_detener)
        layout.addLayout(fila_progreso_bulk)

        self._content_bulk_thread = None

        return tab

    # ── Vista de resultados: lista o árbol ──────────────────────────────

    def _toggle_content_view_mode(self):
        if self.btn_content_view_toggle.isChecked():
            self.btn_content_view_toggle.setText("📋 Lista")
            self.btn_content_expand_collapse.setVisible(True)
            self._poblar_arbol_content()
            self.content_view_stack.setCurrentIndex(1)
        else:
            self.btn_content_view_toggle.setText("🌳 Árbol")
            self.btn_content_expand_collapse.setVisible(False)
            self.content_view_stack.setCurrentIndex(0)

    def _toggle_content_expand_collapse(self):
        if self.btn_content_expand_collapse.text().startswith("➕"):
            self.content_tree.expandAll()
            self.btn_content_expand_collapse.setText("➖ Colapsar Todo")
        else:
            self.content_tree.collapseAll()
            self.btn_content_expand_collapse.setText("➕ Expandir Todo")

    def _refrescar_vista_arbol_si_activa(self):
        if self.content_view_stack.currentIndex() == 1:
            self._poblar_arbol_content()

    def _poblar_arbol_content(self):
        """Reconstruye el árbol a partir de la ruta completa de cada
        resultado (self.content_model.todas()) — mismo criterio que
        shares.py::_populate_folder_tree (ruta real en Qt.UserRole, nunca
        derivada del texto/icono del nodo), pero con un diccionario
        ruta->nodo en vez de recorrer los hijos uno a uno: con miles de
        resultados, escanear childCount() en cada nivel sería demasiado
        lento."""
        self.content_tree.clear()
        nodos_por_ruta = {}

        for fila in self.content_model.todas():
            ruta = fila["ruta"].replace("/", "\\").strip()
            partes = [p for p in ruta.split("\\") if p]
            if not partes:
                continue

            raiz_nombre = partes[0]
            raiz_path = raiz_nombre
            nodo_actual = nodos_por_ruta.get(raiz_path)
            if nodo_actual is None:
                nodo_actual = QTreeWidgetItem(self.content_tree)
                icono_raiz = "💾" if len(raiz_nombre) == 2 and raiz_nombre[1] == ":" else "📁"
                nodo_actual.setText(0, f"{icono_raiz} {raiz_nombre}")
                nodo_actual.setData(0, Qt.UserRole, raiz_path)
                nodos_por_ruta[raiz_path] = nodo_actual

            ruta_actual = raiz_path
            for parte in partes[1:-1]:
                ruta_actual = f"{ruta_actual}\\{parte}"
                hijo = nodos_por_ruta.get(ruta_actual)
                if hijo is None:
                    hijo = QTreeWidgetItem(nodo_actual)
                    hijo.setText(0, f"📁 {parte}")
                    hijo.setData(0, Qt.UserRole, ruta_actual)
                    nodos_por_ruta[ruta_actual] = hijo
                nodo_actual = hijo

            icono = "📁" if fila["tipo"] == "folder" else "📄"
            item = QTreeWidgetItem(nodo_actual)
            item.setText(0, f"{icono} {partes[-1]}")
            item.setText(1, "Carpeta" if fila["tipo"] == "folder" else "Archivo")
            item.setText(2, fila["tamano_fmt"])
            item.setText(3, fila["modificado_fmt"])
            item.setData(0, Qt.UserRole, ruta)
            item.setData(0, Qt.UserRole + 1, fila)  # marca de "esto es un resultado real"

    # ── Ámbito exclusivo (Perfil / Escritorio / Sin filtros) ────────────

    def _on_content_scope_toggled(self, checked):
        """Los 3 checkboxes de ámbito se comportan como un grupo exclusivo
        (como radio buttons) pero se mantienen como QCheckBox porque así se
        pidió: al marcar uno se desmarcan los demás, y no se permite dejar
        los 3 sin marcar (si el usuario desmarca el único activo, se vuelve
        a marcar solo). Además, 'Sin filtros' es el único que habilita el
        campo 'Ubicación' — en Perfil Usuario/Escritorio el ámbito ya está
        fijado por la categoría, no hay ruta que elegir."""
        origen = self.sender()
        if checked:
            for chk in self._content_scope_checks:
                if chk is not origen:
                    chk.blockSignals(True)
                    chk.setChecked(False)
                    chk.blockSignals(False)
        elif not any(c.isChecked() for c in self._content_scope_checks):
            origen.blockSignals(True)
            origen.setChecked(True)
            origen.blockSignals(False)

        usa_ubicacion = self.chk_content_sin_filtros.isChecked()
        self.txt_content_ubicacion.setEnabled(usa_ubicacion)
        self.btn_content_ubicacion.setEnabled(usa_ubicacion)

    def _elegir_ubicacion_content(self):
        carpeta = dialogo_elegir_carpeta(
            self, "Elegir ubicación de búsqueda", self.txt_content_ubicacion.text().strip() or "C:\\")
        if carpeta:
            self.txt_content_ubicacion.setText(carpeta)

    _CARPETAS_PERFIL_BASICAS = ("Documentos", "Imágenes", "Videos", "Música", "Descargas")

    def _resolver_raices_contenido(self):
        if self.chk_content_perfil.isChecked():
            carpetas = get_user_content_folders()
            return [ruta for nombre, ruta in carpetas.items()
                    if nombre in self._CARPETAS_PERFIL_BASICAS]
        if self.chk_content_escritorio.isChecked():
            carpetas = get_user_content_folders()
            return [carpetas["Escritorio"]] if "Escritorio" in carpetas else []
        if self.chk_content_sin_filtros.isChecked():
            ubicacion = self.txt_content_ubicacion.text().strip()
            return [ubicacion] if ubicacion else []
        return []

    def _aplicar_preset_content(self, nombre):
        if nombre in EXTENSION_PRESETS:
            self.txt_content_ext.setText(EXTENSION_PRESETS[nombre])

    # ── Búsqueda (reemplaza al antiguo inventario de un solo disparo) ───

    def refresh_content(self):
        """Lanza la búsqueda de contenido. Sustituye al antiguo análisis
        PowerShell de un solo disparo: usa el slot compartido self.worker,
        igual que el resto de secciones, para que 'Sincronizar Todo' y el
        chequeo de 'ya hay una actualización en curso' sigan funcionando
        sin tocar nada más."""
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        raices = self._resolver_raices_contenido()
        if not raices:
            if self.chk_content_sin_filtros.isChecked():
                self._avisar("Contenido", "Elige una ubicación donde buscar.", QMessageBox.Warning)
            else:
                self._avisar("Contenido", "Marca al menos un ámbito de búsqueda.", QMessageBox.Warning)
            return

        try:
            min_mb = float(self.txt_content_min.text().strip() or 0)
        except ValueError:
            self._avisar("Contenido", "Tamaño mínimo inválido.", QMessageBox.Warning)
            return
        try:
            max_mb = (float(self.txt_content_max.text().strip())
                      if self.txt_content_max.text().strip() else float("inf"))
        except ValueError:
            self._avisar("Contenido", "Tamaño máximo inválido.", QMessageBox.Warning)
            return
        try:
            dias = int(self.txt_content_dias.text().strip()) if self.txt_content_dias.text().strip() else 0
        except ValueError:
            self._avisar("Contenido", "Valor de días inválido.", QMessageBox.Warning)
            return

        extensiones = []
        ext_raw = self.txt_content_ext.text().strip()
        if ext_raw:
            for e in ext_raw.split(","):
                e = e.strip().lower()
                if e:
                    extensiones.append(e if e.startswith(".") else f".{e}")

        tipo = ("file" if self.rb_content_archivos.isChecked()
                else "folder" if self.rb_content_carpetas.isChecked() else "all")

        self.content_model.limpiar()
        self.content_tree.clear()
        self.btn_refresh_content.setEnabled(False)
        self.btn_detener_content.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)  # indeterminado: sin total fiable sin recorrer el árbol dos veces
        self.set_status("🔄 Iniciando búsqueda...")

        self.worker = ContentSearchEngine(
            raices, tipo, self.search_content.text().strip(), self.chk_content_regex.isChecked(),
            extensiones, min_mb * 1024 * 1024, max_mb * 1024 * 1024, dias,
            self.chk_content_subcarpetas.isChecked())
        self.worker.progreso.connect(lambda msg: self.set_status(f"🔄 {msg}"))
        self.worker.resultado.connect(self._on_content_resultado)
        self.worker.error_fatal.connect(self._on_content_error_fatal)
        self.worker.finalizado.connect(self._on_content_finalizado)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def detener_content(self):
        if self.worker is not None and self.worker.isRunning() and isinstance(self.worker, ContentSearchEngine):
            self.worker.request_stop()
            self.btn_detener_content.setEnabled(False)

    def _on_content_resultado(self, filas: list):
        self.content_model.agregarFilas(filas)
        self.set_status(f"🔄 {self.content_model.rowCount()} elementos encontrados...")

    def _on_content_error_fatal(self, msg: str):
        self.btn_refresh_content.setEnabled(True)
        self.btn_detener_content.setEnabled(False)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error en la búsqueda de contenido")
        self._avisar("Error", f"No se pudo completar la búsqueda:\n\n{msg}", QMessageBox.Warning)

    def _on_content_finalizado(self, archivos: int, carpetas: int, avisos: int):
        self.btn_refresh_content.setEnabled(True)
        self.btn_detener_content.setEnabled(False)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        aviso_txt = f"  ({avisos} avisos de acceso)" if avisos else ""
        self.set_status(f"✅ Completado: {archivos} archivos, {carpetas} carpetas{aviso_txt}")
        self._refrescar_vista_arbol_si_activa()

    # ── Acciones sobre resultados ────────────────────────────────────

    def _content_filas_seleccionadas(self):
        if self.content_view_stack.currentIndex() == 1:
            filas = []
            for item in self.content_tree.selectedItems():
                fila = item.data(0, Qt.UserRole + 1)
                if fila:  # los nodos estructurales (carpetas intermedias) no llevan fila
                    filas.append(fila)
            return filas
        filas = []
        for idx in self.content_table.selectionModel().selectedRows():
            fuente = self.content_proxy.mapToSource(idx)
            filas.append(self.content_model.filaEn(fuente.row()))
        return filas

    def _content_abrir_explorer(self):
        seleccion = self._content_filas_seleccionadas()
        if not seleccion:
            self._avisar("Contenido", "Selecciona un elemento.")
            return
        ruta = seleccion[0]["ruta"]
        if not os.path.exists(ruta):
            self._avisar("Contenido", "El elemento ya no existe en disco.", QMessageBox.Warning)
            return
        if os.path.isdir(ruta):
            subprocess.Popen(["explorer", ruta])
        else:
            subprocess.Popen(["explorer", "/select,", ruta])

    def _content_copiar_ruta(self):
        seleccion = self._content_filas_seleccionadas()
        if not seleccion:
            return
        rutas = "\n".join(f["ruta"] for f in seleccion)
        QApplication.clipboard().setText(rutas)
        self.set_status("✅ Ruta(s) copiada(s) al portapapeles")

    # ── Operaciones masivas (papelera / copiar-mover / zip): hilo propio ─

    def _set_content_bulk_botones_habilitados(self, habilitados: bool):
        """Deshabilita Buscar y las acciones sobre resultados mientras hay
        una operación masiva en curso (papelera/copiar/mover/zip) — evita
        lanzar una búsqueda nueva o una segunda operación sobre el mismo
        conjunto de filas a medias de procesarlo."""
        self.btn_refresh_content.setEnabled(habilitados)
        self.btn_content_eliminar.setEnabled(habilitados)
        self.btn_content_copiar_a.setEnabled(habilitados)
        self.btn_content_mover_a.setEnabled(habilitados)
        self.btn_content_zip.setEnabled(habilitados)

    def _detener_content_bulk(self):
        """El hilo solo comprueba la señal de parada ENTRE elementos, no a
        mitad de procesar uno solo — un archivo muy grande en curso (mover
        entre unidades, comprimir) no se corta hasta que termina ese."""
        if self._content_bulk_thread is not None:
            self._content_bulk_thread.request_stop()
            self.btn_content_bulk_detener.setEnabled(False)
            self.set_status("⏹ Deteniendo tras el elemento en curso...")

    def _content_eliminar_seleccionados(self):
        seleccion = self._content_filas_seleccionadas()
        if not seleccion:
            self._avisar("Contenido", "No hay elementos seleccionados.")
            return

        nombres = [f"Enviar «{f['nombre']}» a la papelera de reciclaje" for f in seleccion[:8]]
        if len(seleccion) > 8:
            nombres.append(f"… y {len(seleccion) - 8} más")

        if not self._confirmar_reparacion(
            "Enviar a la papelera de reciclaje",
            cambios=nombres,
            reversible=True,
            objetivo=f"{len(seleccion)} elemento(s)",
        ):
            return

        self._set_content_bulk_botones_habilitados(False)
        self.content_bulk_progreso.setRange(0, len(seleccion))
        self.content_bulk_progreso.setValue(0)
        self.content_bulk_progreso.setVisible(True)
        self.btn_content_bulk_detener.setVisible(True)
        self.btn_content_bulk_detener.setEnabled(True)

        rutas = [f["ruta"] for f in seleccion]
        self._content_bulk_thread = ContentTrashThread(rutas)
        self._content_bulk_thread.progreso.connect(self._on_progreso_content_bulk)
        self._content_bulk_thread.finished_ok.connect(self._on_content_papelera_terminado)
        self._content_bulk_thread.start()

    def _on_progreso_content_bulk(self, actual: int, total: int, nombre: str):
        self.content_bulk_progreso.setValue(actual)
        self.set_status(f"🔄 Procesando {actual}/{total}: {nombre}")

    def _on_content_papelera_terminado(self, enviadas: list, errores: list):
        self.content_bulk_progreso.setVisible(False)
        self.btn_content_bulk_detener.setVisible(False)
        self._set_content_bulk_botones_habilitados(True)
        self._content_bulk_thread = None

        if enviadas:
            self.content_model.quitarPorRuta(set(enviadas))
            self._refrescar_vista_arbol_si_activa()

        self.repair_audit_db.registrar(
            seccion="📁 Contenido", accion="Enviar a la papelera de reciclaje",
            objetivo=f"{len(enviadas)} elemento(s)",
            exito=(bool(enviadas) and not errores),
            detalle="; ".join(errores) if errores else "OK",
        )

        if errores:
            self._avisar("Contenido", "No se pudieron enviar a la papelera:\n\n" + "\n".join(errores), QMessageBox.Warning)
        self.set_status(f"✅ {len(enviadas)} elemento(s) enviado(s) a la papelera")

    # ── Copiar / Mover seleccionados a otra ubicación ────────────────

    def _content_validar_destino(self, seleccion, destino: str) -> str:
        """Mismo criterio que RecycleBinDialog._validar_destino: si el
        destino elegido es la misma carpeta donde ya está algún elemento,
        'sobrescribir' en la resolución de colisión borraría el propio
        origen antes de copiarlo/moverlo."""
        destino_norm = os.path.normcase(os.path.abspath(destino))
        coincidencias = [
            f["nombre"] for f in seleccion
            if f.get("directorio")
            and os.path.normcase(os.path.abspath(f["directorio"])) == destino_norm
        ]
        if not coincidencias:
            return ""
        lista = ", ".join(coincidencias[:5]) + (f" y {len(coincidencias) - 5} más" if len(coincidencias) > 5 else "")
        return f"El destino elegido ya es la carpeta actual de: {lista}."

    def _content_copiar_o_mover(self, modo: str):
        seleccion = self._content_filas_seleccionadas()
        if not seleccion:
            self._avisar("Contenido", "No hay elementos seleccionados.")
            return

        destino = dialogo_elegir_carpeta(self, "Elegir carpeta de destino", "C:\\")
        if not destino:
            return

        error_destino = self._content_validar_destino(seleccion, destino)
        if error_destino:
            self._avisar("Contenido", error_destino, QMessageBox.Warning)
            return

        verbo = "Copiar" if modo == "copiar" else "Mover"
        cambios = [f"{verbo} «{f['nombre']}» → {destino}" for f in seleccion[:8]]
        if len(seleccion) > 8:
            cambios.append(f"… y {len(seleccion) - 8} más")

        if not self._confirmar_reparacion(
            f"{verbo} elementos de Contenido",
            cambios=cambios,
            reversible=(modo == "copiar"),  # copiar no toca el origen; mover sí lo desplaza
            objetivo=f"{len(seleccion)} elemento(s) → {destino}",
        ):
            return

        decisiones = {}
        accion_para_todos = None
        for fila in seleccion:
            destino_final = os.path.join(destino, fila["nombre"])
            if not os.path.exists(destino_final):
                continue
            accion = accion_para_todos
            if accion is None:
                accion, aplicar_a_todos = preguntar_colision(self, fila["nombre"])
                if accion is None:
                    accion = "omitir"
                    accion_para_todos = "omitir"
                elif aplicar_a_todos:
                    accion_para_todos = accion
            decisiones[fila["nombre"]] = accion

        self._set_content_bulk_botones_habilitados(False)
        self.content_bulk_progreso.setRange(0, len(seleccion))
        self.content_bulk_progreso.setValue(0)
        self.content_bulk_progreso.setVisible(True)
        self.btn_content_bulk_detener.setVisible(True)
        self.btn_content_bulk_detener.setEnabled(True)

        self._content_bulk_thread = ContentCopyMoveThread(seleccion, destino, modo, decisiones)
        self._content_bulk_thread.progreso.connect(self._on_progreso_content_bulk)
        self._content_bulk_thread.finished_ok.connect(
            lambda procesados, omitidos, errores:
                self._on_content_copiar_mover_terminado(procesados, omitidos, errores, modo, destino))
        self._content_bulk_thread.start()

    def _on_content_copiar_mover_terminado(self, procesados: int, omitidos: int, errores: list,
                                            modo: str, destino: str):
        self.content_bulk_progreso.setVisible(False)
        self.btn_content_bulk_detener.setVisible(False)
        self._set_content_bulk_botones_habilitados(True)
        self._content_bulk_thread = None

        verbo = "Copiar" if modo == "copiar" else "Mover"
        self.repair_audit_db.registrar(
            seccion="📁 Contenido", accion=f"{verbo} elementos de Contenido",
            objetivo=f"{procesados} elemento(s) → {destino}" + (f" ({omitidos} omitido(s))" if omitidos else ""),
            exito=(procesados > 0 and not errores),
            detalle="; ".join(errores) if errores else "OK",
        )

        if errores:
            self._avisar("Contenido", f"Algunos elementos no se pudieron {modo}:\n\n" + "\n".join(errores), QMessageBox.Warning)
        sufijo_omitidos = f", {omitidos} omitido(s)" if omitidos else ""
        self.set_status(f"✅ {procesados} elemento(s) {'copiado(s)' if modo == 'copiar' else 'movido(s)'} "
                         f"a {destino}{sufijo_omitidos}")
        # Los resultados pueden haber cambiado de ubicación (mover) o dejar
        # de existir tal cual — más simple y fiable relanzar la búsqueda
        # que intentar reconciliar fila a fila qué se movió de verdad.
        if modo == "mover" and procesados:
            self.refresh_content()

    # ── Comprimir seleccionados a ZIP ─────────────────────────────────

    def _content_comprimir_zip(self):
        seleccion = self._content_filas_seleccionadas()
        if not seleccion:
            self._avisar("Contenido", "No hay elementos seleccionados.")
            return

        ruta_zip = dialogo_guardar_archivo(
            self, "Comprimir seleccionados a ZIP",
            ruta_guardado_predeterminada(f"contenido_{time.strftime('%Y%m%d_%H%M%S')}.zip"),
            "Archivo ZIP (*.zip)")
        if not ruta_zip:
            return

        self._set_content_bulk_botones_habilitados(False)
        self.content_bulk_progreso.setRange(0, len(seleccion))
        self.content_bulk_progreso.setValue(0)
        self.content_bulk_progreso.setVisible(True)
        self.btn_content_bulk_detener.setVisible(True)
        self.btn_content_bulk_detener.setEnabled(True)

        self._content_bulk_thread = ContentZipThread(seleccion, ruta_zip)
        self._content_bulk_thread.progreso.connect(self._on_progreso_content_bulk)
        self._content_bulk_thread.finished_ok.connect(
            lambda agregados, errores: self._on_content_zip_terminado(agregados, errores, ruta_zip))
        self._content_bulk_thread.start()

    def _on_content_zip_terminado(self, agregados: int, errores: list, ruta_zip: str):
        self.content_bulk_progreso.setVisible(False)
        self.btn_content_bulk_detener.setVisible(False)
        self._set_content_bulk_botones_habilitados(True)
        self._content_bulk_thread = None

        if errores:
            self._avisar("Contenido", "Algunos elementos no se pudieron comprimir:\n\n" + "\n".join(errores), QMessageBox.Warning)
        self.set_status(f"✅ {agregados} elemento(s) comprimido(s) en {ruta_zip}")

    def _content_exportar(self):
        filas = self.content_model.todas()
        if not filas:
            self._avisar("Contenido", "No hay resultados para exportar.")
            return
        ruta = dialogo_guardar_archivo(
            self, "Exportar resultados de búsqueda",
            ruta_guardado_predeterminada(f"busqueda_contenido_{time.strftime('%Y%m%d_%H%M%S')}.txt"),
            "Texto (*.txt);;CSV (*.csv)")
        if not ruta:
            return

        es_csv = ruta.lower().endswith(".csv")
        try:
            # 5.4.0: CSV con el módulo csv (un «;» en un nombre ya no rompe las columnas), BOM para que
            # Excel lea bien las tildes y _SafeCsvWriter contra fórmulas inyectadas en nombres.
            with open(ruta, "w", encoding="utf-8-sig" if es_csv else "utf-8", newline="" if es_csv else None) as f:
                if es_csv:
                    import csv as _csv
                    from msys.core.utils import _SafeCsvWriter
                    escritor = _SafeCsvWriter(_csv.writer(f, delimiter=";"))
                    escritor.writerow(["Tipo", "Nombre", "Directorio", "Tamaño", "Modificado", "Ruta completa"])
                    for fila in filas:
                        tipo_txt = "Carpeta" if fila["tipo"] == "folder" else "Archivo"
                        escritor.writerow([tipo_txt, fila['nombre'], fila['directorio'], fila['tamano_fmt'],
                                           fila['modificado_fmt'], fila['ruta']])
                else:
                    for fila in sorted(filas, key=lambda x: x["ruta"]):
                        tipo_txt = "Carpeta" if fila["tipo"] == "folder" else "Archivo"
                        f.write(f"[{tipo_txt}] {fila['ruta']} ({fila['tamano_fmt']}) "
                                f"[{fila['modificado_fmt']}]\n")
                    f.write(f"\nTotal: {len(filas)} elementos\n")
        except OSError as e:
            self._avisar("Contenido", f"No se pudo exportar:\n{e}", QMessageBox.Warning)
            return
        self.set_status(f"✅ Exportado a {ruta}")
