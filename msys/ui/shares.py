# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña Carpetas Compartidas: analisis local/ruta, vista jerarquica y de carpetas, menus contextuales, historial de conexiones.
Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).

Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

import os
import subprocess

from PyQt5.QtCore import Qt, QRegExp
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QCheckBox,
    QSpinBox, QPushButton, QMenu, QAction, QMessageBox, QStackedWidget,
    QTreeWidget, QTreeWidgetItem,
)

from msys.config import logger
from msys.core.utils import run_powershell_json
from msys.models.table_models import ShareTableModel, CustomFilterProxy
from msys.widgets.delegates import _setup_header
from msys.collectors import ShareCollector
from msys.ui import piezas


class CompartidasMixin:
    def crear_tab_compartidas(self):
        """Crea la pestaña de carpetas compartidas."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        # Panel de configuración unificado
        config_layout = QHBoxLayout()
        config_layout.setSpacing(8)

        # Campo de ruta (vacío = análisis completo, con ruta = análisis de ruta)
        self.txt_path = QLineEdit()
        self.txt_path.setPlaceholderText("Ruta específica (vacío = Análisis Completo): C:\\Carpeta, H:\\ o \\\\Servidor\\Recurso")
        config_layout.addWidget(self.txt_path, 4)

        self.btn_browse = QPushButton("📂")
        self.btn_browse.setObjectName("ghost")
        self.btn_browse.setFixedWidth(40)
        self.btn_browse.clicked.connect(self.browse_path)
        config_layout.addWidget(self.btn_browse)

        self.btn_analyze = QPushButton("🔍 Analizar")
        self.btn_analyze.setObjectName("success")
        self.btn_analyze.setFixedWidth(140)
        self.btn_analyze.clicked.connect(self.analizar)
        config_layout.addWidget(self.btn_analyze)

        self.btn_runas_shares = QPushButton("👤 Otro Usuario")
        self.btn_runas_shares.setObjectName("runas")
        self.btn_runas_shares.setFixedWidth(140)
        self.btn_runas_shares.clicked.connect(self.ejecutar_como_otro_usuario)
        config_layout.addWidget(self.btn_runas_shares)

        self.btn_stop = QPushButton("⏹️ Parar")
        self.btn_stop.setObjectName("danger")
        self.btn_stop.setFixedWidth(110)
        self.btn_stop.clicked.connect(self.parar_analisis)
        self.btn_stop.setEnabled(False)  # Deshabilitado hasta que inicie análisis
        config_layout.addWidget(self.btn_stop)

        layout.addLayout(config_layout)

        # Filtros en una sola línea compacta
        options_layout = QHBoxLayout()
        options_layout.setSpacing(20)

        self.chk_hide_admin = QCheckBox("🔒 Ocultar Admin (C$)")
        self.chk_hide_admin.setChecked(self.settings.value("hide_admin_shares", True, type=bool))
        self.chk_hide_admin.setToolTip(
            "Excluye los recursos administrativos (C$, ADMIN$...) desde el propio análisis, "
            "no solo de la vista: si lo desmarcas después de analizar, vuelve a pulsar "
            "«Analizar» para que aparezcan."
        )
        self.chk_hide_admin.stateChanged.connect(self._on_filter_changed)
        options_layout.addWidget(self.chk_hide_admin)

        self.chk_hide_system = QCheckBox("🖥️ Ocultar Sistema")
        self.chk_hide_system.setChecked(self.settings.value("hide_system", False, type=bool))
        self.chk_hide_system.stateChanged.connect(self._on_filter_changed)
        options_layout.addWidget(self.chk_hide_system)

        self.chk_hide_everyone = QCheckBox("👥 Ocultar Todos")
        self.chk_hide_everyone.setChecked(self.settings.value("hide_everyone", False, type=bool))
        self.chk_hide_everyone.stateChanged.connect(self._on_filter_changed)
        options_layout.addWidget(self.chk_hide_everyone)

        self.chk_hide_users = QCheckBox("🚫 Ocultar Usuarios")
        self.chk_hide_users.setChecked(self.settings.value("hide_users", False, type=bool))
        self.chk_hide_users.stateChanged.connect(self._on_filter_changed)
        options_layout.addWidget(self.chk_hide_users)

        self.chk_show_only_users = QCheckBox("👤 Mostrar Solo Usuarios")
        self.chk_show_only_users.setChecked(self.settings.value("show_only_users", False, type=bool))
        self.chk_show_only_users.setToolTip(
            "Filtra la VISTA de los datos ya analizados (no repite el análisis). "
            "Para excluir cuentas de sistema/genéricas desde el propio escaneo, usa "
            "«Solo usuarios reales» más abajo, antes de pulsar «Analizar»."
        )
        self.chk_show_only_users.stateChanged.connect(self._on_show_only_users_changed)
        options_layout.addWidget(self.chk_show_only_users)

        options_layout.addStretch()
        layout.addLayout(options_layout)

        # Opciones de escaneo de ruta específica
        scan_options_layout = QHBoxLayout()
        scan_options_layout.setSpacing(20)

        # Control de profundidad
        depth_label = QLabel("📊 Profundidad máxima:")
        scan_options_layout.addWidget(depth_label)

        self.spin_depth = QSpinBox()
        self.spin_depth.setMinimum(1)
        self.spin_depth.setMaximum(99)
        self.spin_depth.setValue(self.settings.value("scan_depth", 99, type=int))
        self.spin_depth.setToolTip("Nivel máximo de subcarpetas a escanear (1=solo raíz)")
        self.spin_depth.setFixedWidth(60)
        scan_options_layout.addWidget(self.spin_depth)

        # Checkbox para carpetas ocultas
        self.chk_include_hidden = QCheckBox("👁️ Incluir carpetas ocultas")
        self.chk_include_hidden.setChecked(self.settings.value("include_hidden", True, type=bool))
        self.chk_include_hidden.setToolTip("Incluir carpetas con atributo oculto en el escaneo")
        scan_options_layout.addWidget(self.chk_include_hidden)

        # Checkbox para solo usuarios
        self.chk_only_users = QCheckBox("👤 Solo usuarios reales")
        self.chk_only_users.setChecked(self.settings.value("only_users", False, type=bool))
        self.chk_only_users.setToolTip(
            "Excluye cuentas del sistema y grupos genéricos DESDE EL PROPIO ANÁLISIS "
            "(hay que marcarlo antes de pulsar «Analizar»; no afecta a datos ya cargados)."
        )
        scan_options_layout.addWidget(self.chk_only_users)

        scan_options_layout.addStretch()
        layout.addLayout(scan_options_layout)

        # Barra de búsqueda y acciones
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)

        # Nombre del equipo
        import socket
        hostname = socket.gethostname()
        lbl_hostname = QLabel(f"🖥️ {hostname}")
        lbl_hostname.setObjectName("hostnameLabel")
        actions_layout.addWidget(lbl_hostname)

        self.search = QLineEdit()
        self.search.setPlaceholderText("🔎 Buscar en resultados...")
        self.search.textChanged.connect(self.apply_filter)
        actions_layout.addWidget(self.search, 3)

        self.btn_expand_collapse = QPushButton("➕ Expandir Todo")
        self.btn_expand_collapse.setObjectName("ghost")
        self.btn_expand_collapse.setFixedWidth(160)
        self.btn_expand_collapse.clicked.connect(self.toggle_expand_collapse)
        actions_layout.addWidget(self.btn_expand_collapse)

        self.btn_tree_view = QPushButton("📁 Carpetas")
        self.btn_tree_view.setObjectName("ghost")
        self.btn_tree_view.setCheckable(True)
        self.btn_tree_view.setFixedWidth(120)
        self.btn_tree_view.clicked.connect(self.toggle_view_mode)
        actions_layout.addWidget(self.btn_tree_view)

        # Copiar/CSV/HTML se quitaron de aquí: ya los da la barra inferior
        # adaptativa (ReportingMixin._actualizar_barra_exportacion), que
        # exporta desde este mismo self.proxy (ver reporting.py, tabla de
        # secciones) con HTML/PDF/CSV/Excel — evita duplicar botones.
        layout.addLayout(actions_layout)

        # Contenedor para alternar entre vistas
        self.view_stack = QStackedWidget()

        # Modelo de datos (se usa internamente)
        self.model = ShareTableModel([])
        self.proxy = CustomFilterProxy(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.proxy.setFilterKeyColumn(-1)

        # VISTA 1: ÁRBOL JERÁRQUICO (Recurso → Usuario → Permisos) - POR DEFECTO
        self.tree_hierarchical = QTreeWidget()
        self.tree_hierarchical.setHeaderLabels(["Recurso / Usuario", "Permiso", "Tipo", "Origen"])
        self.tree_hierarchical.setAlternatingRowColors(True)
        self.tree_hierarchical.setFont(QFont("Segoe UI", 9))
        _setup_header(self.tree_hierarchical, 4, stretch_col=0)
        self.tree_hierarchical.setSelectionMode(QTreeWidget.ExtendedSelection)
        # Configurar eventos
        self.tree_hierarchical.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree_hierarchical.customContextMenuRequested.connect(self.mostrar_menu_contextual_hierarchical)
        self.view_stack.addWidget(self.tree_hierarchical)  # Índice 0

        # VISTA 2: ÁRBOL DE ESTRUCTURA DE CARPETAS (H: → COMPARTIR → 2025 → Usuarios)
        self.tree_folders = QTreeWidget()
        self.tree_folders.setHeaderLabels(["Estructura de Carpetas", "Usuarios/Permisos", "Tipo", "Origen"])
        self.tree_folders.setAlternatingRowColors(True)
        self.tree_folders.setFont(QFont("Segoe UI", 9))
        _setup_header(self.tree_folders, 4, stretch_col=0)
        self.tree_folders.setSelectionMode(QTreeWidget.ExtendedSelection)
        # Configurar eventos
        self.tree_folders.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree_folders.customContextMenuRequested.connect(self.mostrar_menu_contextual_folders)
        self.view_stack.addWidget(self.tree_folders)  # Índice 1

        layout.addWidget(self.view_stack)

        # 4.4.0: la pestaña de siempre pasa a ser la primera de un grupo de
        # pestañas, junto a las de permisos de carpetas (msys/ui/permisos.py).
        self.compartidas_tabs = piezas.pestanas()
        self.compartidas_tabs.addTab(tab, "📂 Recursos compartidos")
        self.crear_pestanas_permisos(self.compartidas_tabs)
        self.compartidas_tabs.currentChanged.connect(lambda _i: self._actualizar_barra_exportacion())
        return self.compartidas_tabs

    def apply_filter(self):
        """Aplica filtro de búsqueda en compartidas."""
        pattern = self.search.text()
        rx = QRegExp(pattern, Qt.CaseInsensitive, QRegExp.RegExp)
        self.proxy.setFilterRegExp(rx)
        self._update_shares_counter()
        # Refrescar vista activa automáticamente
        if self.view_stack.currentIndex() == 0:
            self._populate_hierarchical_tree()
        else:
            self._populate_folder_tree()

    def _on_show_only_users_changed(self):
        """Cuando se marca 'Solo Usuarios', desactiva los demás filtros pero aplica su lógica."""
        if self.chk_show_only_users.isChecked():
            # Recordar el estado ACTUAL de la sesión (no el guardado en disco,
            # que solo se sincroniza al cerrar la app) para poder restaurarlo
            # tal cual al desmarcar "Solo Usuarios".
            self._filtros_previos_solo_usuarios = (
                self.chk_hide_admin.isChecked(),
                self.chk_hide_system.isChecked(),
                self.chk_hide_everyone.isChecked(),
                self.chk_hide_users.isChecked(),
            )

            # Desmarcar los otros checkboxes visualmente
            self.chk_hide_admin.setChecked(False)
            self.chk_hide_system.setChecked(False)
            self.chk_hide_everyone.setChecked(False)
            self.chk_hide_users.setChecked(False)

            # Deshabilitar los otros checkboxes
            self.chk_hide_admin.setEnabled(False)
            self.chk_hide_system.setEnabled(False)
            self.chk_hide_everyone.setEnabled(False)
            self.chk_hide_users.setEnabled(False)

            # Aplicar el filtro de solo usuarios (que internamente aplica hide_admin + hide_system + hide_everyone)
            self.proxy.setHideAdminShares(True)
            self.proxy.setHideSystem(True)
            self.proxy.setHideEveryone(True)
            self.proxy.setHideUsers(False)
            self.proxy.setShowOnlyUsers(True)
        else:
            # Rehabilitar los otros checkboxes
            self.chk_hide_admin.setEnabled(True)
            self.chk_hide_system.setEnabled(True)
            self.chk_hide_everyone.setEnabled(True)
            self.chk_hide_users.setEnabled(True)

            # Restaurar el estado que tenían justo antes de marcar "Solo Usuarios"
            # en esta misma sesión. Si no existe (p.ej. venía ya marcado al abrir
            # la app), usar los valores guardados en disco como respaldo.
            previos = getattr(self, "_filtros_previos_solo_usuarios", None)
            if previos is not None:
                hide_admin, hide_system, hide_everyone, hide_users = previos
            else:
                hide_admin = self.settings.value("hide_admin_shares", True, type=bool)
                hide_system = self.settings.value("hide_system", False, type=bool)
                hide_everyone = self.settings.value("hide_everyone", False, type=bool)
                hide_users = self.settings.value("hide_users", False, type=bool)

            self.chk_hide_admin.setChecked(hide_admin)
            self.chk_hide_system.setChecked(hide_system)
            self.chk_hide_everyone.setChecked(hide_everyone)
            self.chk_hide_users.setChecked(hide_users)

            self.proxy.setShowOnlyUsers(False)
            # Aplicar filtros según checkboxes
            self._on_filter_changed()

        # Refrescar vista activa automáticamente
        self._update_shares_counter()
        if self.view_stack.currentIndex() == 0:
            self._populate_hierarchical_tree()
        else:
            self._populate_folder_tree()

    def _on_filter_changed(self):
        """Actualiza filtros."""
        # Si "Solo Usuarios" está activo, no cambiar filtros
        if not self.chk_show_only_users.isChecked():
            self.proxy.setHideSystem(self.chk_hide_system.isChecked())
            self.proxy.setHideEveryone(self.chk_hide_everyone.isChecked())
            self.proxy.setHideAdminShares(self.chk_hide_admin.isChecked())
            self.proxy.setHideUsers(self.chk_hide_users.isChecked())
            self.proxy.setShowOnlyUsers(False)
        self._update_shares_counter()
        # Refrescar vista activa automáticamente
        if self.view_stack.currentIndex() == 0:
            self._populate_hierarchical_tree()
        else:
            self._populate_folder_tree()

    def toggle_view_mode(self):
        r"""Alterna entre árbol jerárquico (Recurso→Usuario) y árbol de carpetas (H:\COMPARTIR\2025\Usuario)."""
        if self.btn_tree_view.isChecked():
            # Cambiar a vista de árbol de carpetas
            self.btn_tree_view.setText("📊 Recursos")
            self._populate_folder_tree()
            self.view_stack.setCurrentIndex(1)
        else:
            # Cambiar a vista de árbol jerárquico
            self.btn_tree_view.setText("📁 Carpetas")
            self._populate_hierarchical_tree()
            self.view_stack.setCurrentIndex(0)

    def toggle_expand_collapse(self):
        """Expande o colapsa todos los nodos del árbol actual."""
        current_widget = self.view_stack.currentWidget()

        if self.btn_expand_collapse.text().startswith("➕"):
            # Expandir todo
            current_widget.expandAll()
            self.btn_expand_collapse.setText("➖ Colapsar Todo")
        else:
            # Colapsar todo
            current_widget.collapseAll()
            self.btn_expand_collapse.setText("➕ Expandir Todo")

    def _populate_hierarchical_tree(self):
        """Puebla el árbol jerárquico: Recurso → Usuario → Permisos."""
        self.tree_hierarchical.clear()

        # Organizar datos por recurso
        resources_dict = {}
        for row_idx in range(self.proxy.rowCount()):
            recurso_idx = self.proxy.index(row_idx, 0)
            ruta_idx = self.proxy.index(row_idx, 1)
            identidad_idx = self.proxy.index(row_idx, 2)
            permiso_idx = self.proxy.index(row_idx, 3)
            tipo_idx = self.proxy.index(row_idx, 4)
            origen_idx = self.proxy.index(row_idx, 5)

            recurso = self.proxy.data(recurso_idx, Qt.DisplayRole) or ""
            ruta = self.proxy.data(ruta_idx, Qt.DisplayRole) or ""
            identidad = self.proxy.data(identidad_idx, Qt.DisplayRole) or ""
            permiso = self.proxy.data(permiso_idx, Qt.DisplayRole) or ""
            tipo = self.proxy.data(tipo_idx, Qt.DisplayRole) or ""
            origen = self.proxy.data(origen_idx, Qt.DisplayRole) or ""

            if recurso not in resources_dict:
                resources_dict[recurso] = {"ruta": ruta, "permisos": []}

            resources_dict[recurso]["permisos"].append({
                "identidad": identidad,
                "permiso": permiso,
                "tipo": tipo,
                "origen": origen
            })

        # Crear árbol jerárquico
        for recurso, data in sorted(resources_dict.items()):
            # Nodo raíz: Recurso compartido
            resource_item = QTreeWidgetItem(self.tree_hierarchical)
            resource_item.setText(0, f"📂 {recurso}")
            resource_item.setText(1, f"({len(data['permisos'])} permisos)")
            resource_item.setText(2, "")
            resource_item.setText(3, data["ruta"])
            resource_item.setExpanded(False)

            # Hijos: Permisos de cada usuario/grupo
            for perm in data["permisos"]:
                perm_item = QTreeWidgetItem(resource_item)

                # Icono según tipo de usuario
                identidad = perm["identidad"]
                if "BUILTIN" in identidad.upper() or "NT AUTHORITY" in identidad.upper():
                    icon = "🖥️"
                elif "TODOS" in identidad.upper() or "EVERYONE" in identidad.upper():
                    icon = "👥"
                else:
                    icon = "👤"

                perm_item.setText(0, f"{icon} {identidad}")
                perm_item.setText(1, perm["permiso"])
                perm_item.setText(2, perm["tipo"])
                perm_item.setText(3, perm["origen"])

    def _populate_folder_tree(self):
        """Puebla el árbol de estructura de carpetas: H: → COMPARTIR → 2025 → Usuarios."""
        self.tree_folders.clear()

        # Estructura: {ruta_completa: {usuarios: [{identidad, permiso, tipo, origen}]}}
        folder_structure = {}

        for row_idx in range(self.proxy.rowCount()):
            recurso_idx = self.proxy.index(row_idx, 0)
            ruta_idx = self.proxy.index(row_idx, 1)
            identidad_idx = self.proxy.index(row_idx, 2)
            permiso_idx = self.proxy.index(row_idx, 3)
            tipo_idx = self.proxy.index(row_idx, 4)
            origen_idx = self.proxy.index(row_idx, 5)

            recurso = self.proxy.data(recurso_idx, Qt.DisplayRole) or ""
            ruta = self.proxy.data(ruta_idx, Qt.DisplayRole) or ""
            identidad = self.proxy.data(identidad_idx, Qt.DisplayRole) or ""
            permiso = self.proxy.data(permiso_idx, Qt.DisplayRole) or ""
            tipo = self.proxy.data(tipo_idx, Qt.DisplayRole) or ""
            origen = self.proxy.data(origen_idx, Qt.DisplayRole) or ""

            # Normalizar ruta
            ruta_normalized = ruta.replace('/', '\\').strip()
            if not ruta_normalized:
                continue

            if ruta_normalized not in folder_structure:
                folder_structure[ruta_normalized] = {"recurso": recurso, "usuarios": []}

            folder_structure[ruta_normalized]["usuarios"].append({
                "identidad": identidad,
                "permiso": permiso,
                "tipo": tipo,
                "origen": origen
            })

        # Crear árbol de carpetas jerárquico
        root_nodes = {}  # {parte_raíz: QTreeWidgetItem}

        for ruta, data in sorted(folder_structure.items()):
            # Dividir ruta en partes: H:\COMPARTIR\2025 → ['H:', 'COMPARTIR', '2025']
            es_unc = ruta.startswith('\\\\')
            parts = [p for p in ruta.split('\\') if p]

            if not parts:
                continue

            # Obtener o crear nodo raíz (unidad o primer nivel)
            root = parts[0]
            if root not in root_nodes:
                root_item = QTreeWidgetItem(self.tree_folders)
                # Si es unidad (H:), mostrar icono de disco
                if len(root) == 2 and root[1] == ':':
                    root_item.setText(0, f"💾 {root}")
                    root_path = root
                else:
                    root_item.setText(0, f"📁 {root}")
                    # Si la ruta original era UNC (\\Servidor\Recurso), conservar
                    # el prefijo — reconstruir solo por texto visible lo pierde
                    # y rompe "Abrir Ubicación"/"Mostrar permisos" para análisis
                    # de rutas de red.
                    root_path = ('\\\\' + root) if es_unc else root
                # Ruta real asociada al nodo — nunca se deriva parseando el
                # texto/icono del árbol (frágil), se guarda tal cual.
                root_item.setData(0, Qt.UserRole, root_path)
                root_item.setExpanded(False)
                root_nodes[root] = root_item
            else:
                root_item = root_nodes[root]

            # Navegar/crear estructura de subcarpetas
            current_item = root_item
            current_path = current_item.data(0, Qt.UserRole)
            for part in parts[1:]:
                target_path = current_path + '\\' + part

                # Buscar si ya existe este hijo (por ruta real, no por texto)
                found = None
                for i in range(current_item.childCount()):
                    child = current_item.child(i)
                    if child.data(0, Qt.UserRole) == target_path:
                        found = child
                        break

                if found:
                    current_item = found
                else:
                    # Crear nuevo nodo de carpeta
                    folder_item = QTreeWidgetItem(current_item)
                    folder_item.setText(0, f"📁 {part}")
                    folder_item.setData(0, Qt.UserRole, target_path)
                    folder_item.setExpanded(False)
                    current_item = folder_item

                current_path = target_path

            # Añadir usuarios como hijos del último nivel
            for user in data["usuarios"]:
                user_item = QTreeWidgetItem(current_item)

                # Icono según tipo de usuario
                identidad = user["identidad"]
                if "BUILTIN" in identidad.upper() or "NT AUTHORITY" in identidad.upper():
                    icon = "🖥️"
                elif "TODOS" in identidad.upper() or "EVERYONE" in identidad.upper():
                    icon = "👥"
                else:
                    icon = "👤"

                user_item.setText(0, f"{icon} {identidad}")
                user_item.setText(1, user["permiso"])
                user_item.setText(2, user["tipo"])
                user_item.setText(3, user["origen"])

    def _update_shares_counter(self):
        """Actualiza el contador de registros mostrados vs totales."""
        total = self.model.rowCount()
        visible = self.proxy.rowCount()
        if total == visible:
            self.set_status(f"✅ {total} registros")
        else:
            self.set_status(f"✅ Mostrando {visible} de {total} registros")

    def refresh(self, mode: str, target: str, resume_checkpoint: bool = False):
        """Inicia recopilación de datos con soporte de checkpoint."""
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        exclude = self.chk_hide_admin.isChecked()
        max_depth = self.spin_depth.value()
        include_hidden = self.chk_include_hidden.isChecked()
        only_users = self.chk_only_users.isChecked()

        # Deshabilitar controles específicos (NO todo el widget)
        self.btn_analyze.setEnabled(False)
        self.btn_runas_shares.setEnabled(False)
        self.btn_browse.setEnabled(False)
        self.txt_path.setEnabled(False)
        self.chk_hide_admin.setEnabled(False)
        self.chk_include_hidden.setEnabled(False)
        self.chk_only_users.setEnabled(False)
        self.spin_depth.setEnabled(False)
        self.btn_stop.setEnabled(True)  # Habilitar botón Parar
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        if resume_checkpoint:
            self.set_status("🔄 Reanudando análisis desde checkpoint...")
        else:
            self.set_status("🔄 Recopilando datos...")

        self.worker = ShareCollector(mode, target, exclude_admin_shares=exclude,
                                     max_depth=max_depth, include_hidden=include_hidden,
                                     only_real_users=only_users,
                                     resume_checkpoint=resume_checkpoint)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_shares_data)
        self.worker.finished_error.connect(self._on_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def abrir_ubicacion(self, ruta):
        """Abre el explorador de Windows en la ubicación especificada."""
        try:
            if os.path.exists(ruta):
                subprocess.Popen(['explorer', ruta])
                self.set_status(f"📂 Abriendo: {ruta}")
            else:
                self._avisar("Ruta no encontrada", f"La ruta no existe o no es accesible:\n\n{ruta}", QMessageBox.Warning)
        except Exception as e:
            self._avisar("Error", f"No se pudo abrir la ubicación:\n\n{str(e)}", QMessageBox.Critical)

    def abrir_propiedades_windows(self, ruta):
        """Abre la ventana de 'Acceso a la red' (Explorador → Propiedades → Compartir → Compartir...)."""
        try:
            if not os.path.exists(ruta):
                self._avisar("Ruta no encontrada", f"La ruta no existe o no es accesible:\n\n{ruta}", QMessageBox.Warning)
                return

            # Usar PowerShell para invocar el diálogo de compartir
            # Simula: Clic derecho → Propiedades → Compartir → Compartir...
            # $path se interpola como cadena de comillas SIMPLES a propósito:
            # dentro de comillas simples de PowerShell el único caracter
            # especial es la propia comilla simple (doblada para escaparla) —
            # backtick y $ son literales ahí. Con comillas dobles (como
            # estaba antes) un nombre de carpeta/recurso compartido con
            # "$(comando)" (válido en NTFS) ejecutaría ese comando —
            # justamente el escenario de una máquina ya comprometida.
            ruta_escaped = ruta.replace("'", "''")

            ps_command = f'''
Add-Type @"
using System;
using System.Runtime.InteropServices;

public class ShareDialog {{
    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    public static extern int SHShowShareFolderUI(IntPtr hwndParent, string pszPath);
}}
"@

$path = '{ruta_escaped}'
[ShareDialog]::SHShowShareFolderUI([IntPtr]::Zero, $path)
'''

            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0  # SW_HIDE

            subprocess.Popen(
                ['powershell', '-WindowStyle', 'Hidden', '-Command', ps_command],
                startupinfo=startupinfo,
                creationflags=subprocess.CREATE_NO_WINDOW
            )

            self.set_status(f"🔑 Abriendo ventana 'Acceso a la red' para: {ruta}")

        except Exception as e:
            logger.exception(f"Error abriendo ventana de compartir: {e}")
            self._avisar("Error", f"Error al abrir la ventana de compartir:\n\n{str(e)}", QMessageBox.Critical)

    def mostrar_menu_contextual_hierarchical(self, position):
        """Muestra el menú contextual al hacer clic derecho en el árbol jerárquico."""
        item = self.tree_hierarchical.itemAt(position)
        if not item:
            return

        # Menú para carpeta compartida (nodo raíz)
        if item.parent() is None:
            # Es un recurso compartido (carpeta)
            ruta = item.text(3)

            menu = QMenu(self)

            action_abrir = QAction("📂 Abrir Ubicación", self)
            action_abrir.triggered.connect(lambda: self.abrir_ubicacion(ruta))
            menu.addAction(action_abrir)

            menu.exec_(self.tree_hierarchical.viewport().mapToGlobal(position))
        else:
            # Es un usuario/grupo
            identidad = item.text(0)

            # Quitar icono para obtener el nombre limpio
            for icon in ["👤 ", "👥 ", "🖥️ "]:
                if identidad.startswith(icon):
                    identidad = identidad[len(icon):]
                    break

            # Verificar que no sea Admin, Sistema o Todos
            identidad_upper = identidad.upper()
            if any(x in identidad_upper for x in ["BUILTIN", "NT AUTHORITY", "TODOS", "EVERYONE", "SYSTEM"]):
                return

            # Obtener información del recurso (padre)
            recurso_item = item.parent()
            recurso = recurso_item.text(0).replace("📂 ", "")
            ruta = recurso_item.text(3)

            # Crear menú contextual
            menu = QMenu(self)

            action_permisos = QAction("🔑 Mostrar permisos", self)
            action_permisos.triggered.connect(lambda: self.abrir_propiedades_windows(ruta))
            menu.addAction(action_permisos)

            action_historial = QAction("📊 Historial de conexiones", self)
            action_historial.triggered.connect(lambda: self.mostrar_historial_usuario(identidad, recurso, ruta))
            menu.addAction(action_historial)

            menu.exec_(self.tree_hierarchical.viewport().mapToGlobal(position))

    def mostrar_menu_contextual_folders(self, position):
        """Muestra el menú contextual al hacer clic derecho en el árbol de carpetas."""
        item = self.tree_folders.itemAt(position)
        if not item:
            return

        identidad = item.text(0)

        # Verificar si es un usuario o una carpeta
        is_usuario = identidad.startswith("👤") or identidad.startswith("👥") or identidad.startswith("🖥️")

        if is_usuario:
            # Quitar icono
            for icon in ["👤 ", "👥 ", "🖥️ "]:
                if identidad.startswith(icon):
                    identidad = identidad[len(icon):]
                    break

            # Verificar que no sea Admin, Sistema o Todos
            identidad_upper = identidad.upper()
            if any(x in identidad_upper for x in ["BUILTIN", "NT AUTHORITY", "TODOS", "EVERYONE", "SYSTEM"]):
                return

            # Ruta real de la carpeta padre, guardada como dato del nodo al
            # construir el árbol (evita perder el prefijo \\ de rutas UNC).
            carpeta_item = item.parent()
            ruta = carpeta_item.data(0, Qt.UserRole) or ""
            recurso = ruta.rsplit("\\", 1)[-1] if ruta else ""

            # Crear menú contextual para usuario
            menu = QMenu(self)

            action_permisos = QAction("🔑 Mostrar permisos", self)
            action_permisos.triggered.connect(lambda: self.abrir_propiedades_windows(ruta))
            menu.addAction(action_permisos)

            action_historial = QAction("📊 Historial de conexiones", self)
            action_historial.triggered.connect(lambda: self.mostrar_historial_usuario(identidad, recurso, ruta))
            menu.addAction(action_historial)

            menu.exec_(self.tree_folders.viewport().mapToGlobal(position))
        else:
            # Es una carpeta, mostrar menú de carpeta.
            # Ruta real guardada como dato del nodo (ver _populate_folder_tree).
            ruta = item.data(0, Qt.UserRole) or ""

            menu = QMenu(self)

            action_abrir = QAction("📂 Abrir Ubicación", self)
            action_abrir.triggered.connect(lambda: self.abrir_ubicacion(ruta))
            menu.addAction(action_abrir)

            menu.exec_(self.tree_folders.viewport().mapToGlobal(position))

    def obtener_historial_conexiones(self, identidad, ruta):
        """
        Obtiene el historial de conexiones desde los registros de eventos de Windows.
        Busca en el registro de seguridad eventos de acceso a recursos compartidos.
        """
        historial = []

        try:
            # Extraer el nombre de usuario sin el dominio si lo tiene
            usuario_limpio = identidad.split('\\')[-1].replace("'", "''")

            # Comando PowerShell para obtener eventos de acceso a recursos compartidos
            # Event ID 5140: Se accedió a un recurso compartido de red
            ps_command = f"""
            $eventos = Get-WinEvent -FilterHashtable @{{
                LogName='Security'
                ID=5140
            }} -MaxEvents 1000 -ErrorAction SilentlyContinue

            $eventos | Where-Object {{
                $_.Properties[1].Value -like '*{usuario_limpio}*'
            }} | ForEach-Object {{
                [PSCustomObject]@{{
                    Fecha = $_.TimeCreated.ToString('dd/MM/yyyy HH:mm:ss')
                    Usuario = $_.Properties[1].Value
                    IP = $_.Properties[18].Value
                    Recurso = $_.Properties[5].Value
                    Evento = 'Acceso a recurso compartido'
                }}
            }} | Select-Object -First 50 | ConvertTo-Json
            """

            resultado = run_powershell_json(ps_command, timeout=15)

            if resultado:
                import json
                eventos = json.loads(resultado) if isinstance(resultado, str) else resultado

                # Asegurarse de que sea una lista
                if isinstance(eventos, dict):
                    eventos = [eventos]

                for evento in eventos:
                    historial.append({
                        'fecha': evento.get('Fecha', 'N/A'),
                        'usuario': evento.get('Usuario', 'N/A'),
                        'ip': evento.get('IP', 'N/A'),
                        'recurso': evento.get('Recurso', 'N/A'),
                        'evento': evento.get('Evento', 'N/A')
                    })

        except Exception as e:
            logger.error(f"Error obteniendo historial: {e}")

        return historial

