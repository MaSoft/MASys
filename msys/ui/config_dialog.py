# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Dialogo de configuracion: mostrar/ocultar y
reordenar pestañas, + perfiles de navegación (guardar/cargar/borrar una
configuración completa con nombre). "MASys" es el perfil predeterminado
y protegido (no se sobrescribe ni se borra) — ver NavProfilesDB en
msys/collectors.py.

Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).
Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

import os

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QMessageBox, QTreeWidget, QTreeWidgetItem, QInputDialog, QDialog,
)

from msys.config import (
    CATEGORIES, CATEGORY_ORDER, CONFIG_FILE, SECTION_CATEGORY,
    load_tab_config, save_tab_config,
)


class ConfiguracionMixin:
    def _synced_tab_config(self):
        """Devuelve la configuración guardada sincronizada con las secciones
        que existen realmente en self._section_order: conserva las entradas
        existentes (orden/visibilidad, incluida cualquier reordenación
        manual del usuario dentro de su categoría) e inserta cada sección
        NUEVA justo después de la sección que la precede en
        self._section_order — su posición natural de creación — en vez de
        siempre al final de toda la lista. Añadirla al final hacía que
        cualquier pestaña nueva apareciera bajo la ÚLTIMA categoría entera
        del fichero guardado (p. ej. debajo de Seguridad), sin ninguna
        relación con dónde se creó realmente ni con lo que pidiera el
        usuario al añadirla."""
        saved_config = load_tab_config()
        conocidas = {tab_cfg['name']: tab_cfg for tab_cfg in saved_config.get('tabs', [])
                     if tab_cfg['name'] in self._section_order}

        # Conserva el orden ya guardado tal cual para las secciones conocidas.
        synced_tabs = [tab_cfg for tab_cfg in saved_config.get('tabs', [])
                       if tab_cfg['name'] in conocidas]

        for idx, name in enumerate(self._section_order):
            if name in conocidas:
                continue

            nueva_entrada = {"name": name, "visible": True}
            if idx == 0:
                synced_tabs.insert(0, nueva_entrada)
            else:
                predecesora = self._section_order[idx - 1]
                pos = next((i for i, t in enumerate(synced_tabs) if t['name'] == predecesora), None)
                if pos is None:
                    # No debería ocurrir (predecesora ya procesada en esta
                    # misma iteración), pero no perder la sección si pasa.
                    synced_tabs.append(nueva_entrada)
                else:
                    synced_tabs.insert(pos + 1, nueva_entrada)

            conocidas[name] = nueva_entrada

        return {"tabs": synced_tabs}

    # ── Perfil activo (QSettings) ────────────────────────────────────────

    def _perfil_nav_activo(self) -> str:
        from msys.collectors import NavProfilesDB
        nombre = self.settings.value("nav_profile_activo", NavProfilesDB.NOMBRE_PREDETERMINADO)
        return NavProfilesDB.NOMBRE_PREDETERMINADO if nombre == NavProfilesDB.NOMBRE_ANTERIOR else nombre

    def _set_perfil_nav_activo(self, nombre: str):
        self.settings.setValue("nav_profile_activo", nombre)
        if hasattr(self, "lbl_perfil_activo"):
            self.lbl_perfil_activo.setText(f"👤  {nombre}")

    # ── Diálogo principal ─────────────────────────────────────────────────

    def abrir_configuracion(self):
        """Abre el diálogo de configuración de visibilidad, orden y
        perfiles del menú."""
        from PyQt5.QtWidgets import (
            QDialog, QVBoxLayout, QHBoxLayout, QDialogButtonBox, QLabel,
            QPushButton, QComboBox,
        )
        from PyQt5.QtCore import Qt

        # 'MASys' se crea la PRIMERA vez a partir de lo que ya está
        # activo ahora mismo — no del hardcodeado DEFAULT_TAB_CONFIG, ver
        # NavProfilesDB.asegurar_perfil_predeterminado.
        self.nav_profiles_db.asegurar_perfil_predeterminado(self._synced_tab_config())

        dialog = QDialog(self)
        dialog.setWindowTitle("Configuración del Menú")
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setMinimumWidth(560)
        dialog.setMinimumHeight(700)

        layout = QVBoxLayout()

        title = QLabel("Configura la visibilidad, el orden y los perfiles de las secciones:")
        title.setStyleSheet("font-weight: bold; font-size: 14px; margin-bottom: 10px;")
        layout.addWidget(title)

        instructions = QLabel(
            "✓ Marca/desmarca para mostrar/ocultar\n"
            "▲▼ Reordena una sección dentro de su propia categoría\n"
            "(las categorías no son reordenables)"
        )
        instructions.setStyleSheet(
            f"color: {'#9aa0ac' if self.current_theme == 'dark' else '#7f8c8d'}; "
            "font-style: italic; margin-bottom: 10px;"
        )
        layout.addWidget(instructions)

        # ── Fila de perfiles ──
        fila_perfil = QHBoxLayout()
        fila_perfil.addWidget(QLabel("Perfil:"))
        self.combo_perfiles = QComboBox()
        self.combo_perfiles.setMinimumWidth(160)
        self.combo_perfiles.setToolTip(
            "Elegir un perfil lo carga aquí para revisarlo — sigue haciendo falta pulsar "
            "Aceptar para aplicarlo de verdad")
        fila_perfil.addWidget(self.combo_perfiles, 1)

        btn_guardar_perfil = QPushButton("💾 Guardar como...")
        btn_guardar_perfil.clicked.connect(self._perfil_guardar_como)
        fila_perfil.addWidget(btn_guardar_perfil)

        btn_borrar_perfil = QPushButton("✕ Borrar")
        btn_borrar_perfil.clicked.connect(self._perfil_borrar_seleccionado)
        fila_perfil.addWidget(btn_borrar_perfil)

        layout.addLayout(fila_perfil)
        self._refrescar_combo_perfiles()
        # Elegir en el desplegable YA carga el perfil — no hace falta un
        # botón "Cargar" aparte (el combo es justo eso, un selector).
        # Conectado DESPUÉS de _refrescar_combo_perfiles() para no disparar
        # una carga redundante al construir el diálogo.
        self.combo_perfiles.currentIndexChanged.connect(self._perfil_cargar_seleccionado)

        # Main area with tree and buttons
        main_layout = QHBoxLayout()

        self.config_tree = QTreeWidget()
        self.config_tree.setHeaderHidden(True)
        self.config_tree.setIndentation(16)
        main_layout.addWidget(self.config_tree)

        # Se va a editar partiendo de lo que ya está activo — Cargar/
        # Restablecer todo sustituyen esto explícitamente más abajo.
        self._perfil_en_edicion = self._perfil_nav_activo()
        self._poblar_config_tree(self._synced_tab_config())

        # Reorder buttons
        button_layout = QVBoxLayout()
        button_layout.addStretch()

        btn_move_up = QPushButton("▲ Subir")
        btn_move_up.setFixedWidth(100)
        btn_move_up.clicked.connect(self._move_tab_up)
        button_layout.addWidget(btn_move_up)

        btn_move_down = QPushButton("▼ Bajar")
        btn_move_down.setFixedWidth(100)
        btn_move_down.clicked.connect(self._move_tab_down)
        button_layout.addWidget(btn_move_down)

        button_layout.addStretch()
        main_layout.addLayout(button_layout)

        layout.addLayout(main_layout)

        # ── Fila de marcado rápido + restablecer ──
        fila_marcado = QHBoxLayout()
        btn_marcar_todo = QPushButton("☑ Seleccionar todo")
        btn_marcar_todo.clicked.connect(lambda: self._marcar_todo_config_tree(True))
        fila_marcado.addWidget(btn_marcar_todo)

        btn_desmarcar_todo = QPushButton("☐ Deseleccionar todo")
        btn_desmarcar_todo.clicked.connect(lambda: self._marcar_todo_config_tree(False))
        fila_marcado.addWidget(btn_desmarcar_todo)

        fila_marcado.addStretch()

        btn_restablecer = QPushButton("🔄 Restablecer todo")
        btn_restablecer.setToolTip("Vuelve a cargar el perfil predeterminado 'MASys' en esta ventana")
        btn_restablecer.clicked.connect(self._perfil_restablecer_masys)
        fila_marcado.addWidget(btn_restablecer)

        layout.addLayout(fila_marcado)

        # OK/Cancel buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(lambda: self.aplicar_configuracion(dialog))
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        dialog.setLayout(layout)
        dialog.exec_()

    # ── Construcción/lectura del árbol ───────────────────────────────────

    def _poblar_config_tree(self, config: dict):
        """(Re)construye self.config_tree a partir de un {"tabs": [...]}
        — reutilizado al abrir el diálogo, al cargar un perfil y al
        restablecer el predeterminado, para no repetir la misma
        construcción tres veces."""
        self.config_tree.clear()
        visible_names = {c['name'] for c in config.get('tabs', []) if c.get('visible', True)}
        ordered_names = [c['name'] for c in config.get('tabs', [])]

        if "📊 Dashboard" in ordered_names:
            dash_item = QTreeWidgetItem(self.config_tree, ["📊 Dashboard"])
            dash_item.setFlags(dash_item.flags() | Qt.ItemIsUserCheckable)
            dash_item.setCheckState(0, Qt.Checked if "📊 Dashboard" in visible_names else Qt.Unchecked)

        for cat_key in CATEGORY_ORDER:
            miembros = [n for n in ordered_names if SECTION_CATEGORY.get(n) == cat_key]
            if not miembros:
                continue
            cat_item = QTreeWidgetItem(self.config_tree, [CATEGORIES[cat_key]["label"]])
            font = cat_item.font(0)
            font.setBold(True)
            cat_item.setFont(0, font)
            cat_item.setFlags(cat_item.flags() & ~Qt.ItemIsUserCheckable)
            for name in miembros:
                child = QTreeWidgetItem(cat_item, [name])
                child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                child.setCheckState(0, Qt.Checked if name in visible_names else Qt.Unchecked)

        self.config_tree.expandAll()

    def _leer_config_desde_tree(self) -> dict:
        """Lee el estado actual (marcado + orden) de self.config_tree y lo
        devuelve como {"tabs": [...]} — mismo formato que CONFIG_FILE y que
        un perfil guardado."""
        new_config = {"tabs": []}
        for i in range(self.config_tree.topLevelItemCount()):
            top = self.config_tree.topLevelItem(i)
            if top.childCount() == 0:
                new_config["tabs"].append({
                    "name": top.text(0),
                    "visible": top.checkState(0) == Qt.Checked
                })
                continue
            for j in range(top.childCount()):
                child = top.child(j)
                new_config["tabs"].append({
                    "name": child.text(0),
                    "visible": child.checkState(0) == Qt.Checked
                })
        return new_config

    def _marcar_todo_config_tree(self, marcado: bool):
        estado = Qt.Checked if marcado else Qt.Unchecked
        for i in range(self.config_tree.topLevelItemCount()):
            top = self.config_tree.topLevelItem(i)
            if top.flags() & Qt.ItemIsUserCheckable:
                top.setCheckState(0, estado)
            for j in range(top.childCount()):
                top.child(j).setCheckState(0, estado)

    def _move_tab_up(self):
        """Sube la sección seleccionada un puesto, solo entre sus hermanas
        de la misma categoría (Dashboard y las categorías no se mueven)."""
        item = self.config_tree.currentItem()
        if item is None or item.parent() is None:
            return
        parent = item.parent()
        idx = parent.indexOfChild(item)
        if idx > 0:
            parent.takeChild(idx)
            parent.insertChild(idx - 1, item)
            self.config_tree.setCurrentItem(item)

    def _move_tab_down(self):
        """Baja la sección seleccionada un puesto, solo entre sus hermanas
        de la misma categoría."""
        item = self.config_tree.currentItem()
        if item is None or item.parent() is None:
            return
        parent = item.parent()
        idx = parent.indexOfChild(item)
        if idx < parent.childCount() - 1:
            parent.takeChild(idx)
            parent.insertChild(idx + 1, item)
            self.config_tree.setCurrentItem(item)

    # ── Perfiles: guardar / cargar / borrar / restablecer ────────────────

    def _refrescar_combo_perfiles(self):
        self.combo_perfiles.blockSignals(True)
        self.combo_perfiles.clear()
        self.combo_perfiles.addItems(self.nav_profiles_db.listar())
        idx = self.combo_perfiles.findText(self._perfil_en_edicion if hasattr(self, "_perfil_en_edicion") else "")
        if idx >= 0:
            self.combo_perfiles.setCurrentIndex(idx)
        self.combo_perfiles.blockSignals(False)

    def _perfil_guardar_como(self):
        """Guarda el estado ACTUAL del árbol (tal como está editado ahora
        mismo, aplicado o no todavía) como un perfil nuevo o existente —
        salvo 'MASys', protegido."""
        from msys.collectors import NavProfilesDB
        dlg = QInputDialog(self)
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dlg.setWindowTitle("Guardar perfil")
        dlg.setLabelText("Nombre del perfil:")
        dlg.setInputMode(QInputDialog.TextInput)
        if dlg.exec_() != QDialog.Accepted:
            return
        nombre = dlg.textValue().strip()
        if not nombre:
            return
        if nombre == NavProfilesDB.NOMBRE_PREDETERMINADO:
            self._avisar(
                "Guardar perfil",
                f"«{NavProfilesDB.NOMBRE_PREDETERMINADO}» es el perfil predeterminado y no se puede "
                "sobrescribir. Elige otro nombre.",
                QMessageBox.Warning)
            return
        if self.nav_profiles_db.existe(nombre):
            if QMessageBox.question(
                self, "Perfil existente",
                f"Ya existe un perfil llamado «{nombre}». ¿Sobrescribirlo con la configuración actual?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            ) != QMessageBox.Yes:
                return

        self.nav_profiles_db.guardar(nombre, self._leer_config_desde_tree())
        self._perfil_en_edicion = nombre
        self._refrescar_combo_perfiles()
        self.set_status(f"✅ Perfil «{nombre}» guardado")

    def _perfil_cargar_seleccionado(self):
        nombre = self.combo_perfiles.currentText()
        if not nombre:
            self._avisar("Cargar perfil", "No hay ningún perfil que cargar.")
            return
        config = self.nav_profiles_db.cargar(nombre)
        if config is None:
            self._avisar("Cargar perfil", f"No se encontró el perfil «{nombre}».", QMessageBox.Warning)
            return
        self._poblar_config_tree(config)
        self._perfil_en_edicion = nombre
        self.set_status(f"📂 Perfil «{nombre}» cargado en el editor — pulsa Aceptar para aplicarlo")

    def _perfil_borrar_seleccionado(self):
        from msys.collectors import NavProfilesDB
        nombre = self.combo_perfiles.currentText()
        if not nombre:
            return
        if nombre == NavProfilesDB.NOMBRE_PREDETERMINADO:
            self._avisar(
                "Borrar perfil",
                f"«{NavProfilesDB.NOMBRE_PREDETERMINADO}» es el perfil predeterminado y no se puede borrar.",
                QMessageBox.Warning)
            return
        if QMessageBox.question(
            self, "Borrar perfil", f"¿Borrar el perfil «{nombre}»? Esta acción no se puede deshacer.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return
        self.nav_profiles_db.borrar(nombre)
        if getattr(self, "_perfil_en_edicion", None) == nombre:
            self._perfil_en_edicion = NavProfilesDB.NOMBRE_PREDETERMINADO
        self._refrescar_combo_perfiles()
        self.set_status(f"✕ Perfil «{nombre}» borrado")

    def _perfil_restablecer_masys(self):
        """'Restablecer todo': recarga el perfil predeterminado 'MASys'
        en el editor (igual que Cargar, con el perfil fijo) — sigue
        haciendo falta pulsar Aceptar para que se aplique de verdad, mismo
        criterio que el resto de ediciones de este diálogo."""
        from msys.collectors import NavProfilesDB
        config = self.nav_profiles_db.cargar(NavProfilesDB.NOMBRE_PREDETERMINADO)
        if config is None:
            # No debería pasar (se asegura al abrir el diálogo), pero por
            # si acaso: usar el hardcodeado como último recurso.
            from msys.config import DEFAULT_TAB_CONFIG
            config = DEFAULT_TAB_CONFIG
        self._poblar_config_tree(config)
        self._perfil_en_edicion = NavProfilesDB.NOMBRE_PREDETERMINADO
        self._refrescar_combo_perfiles()
        self.set_status("🔄 Perfil «MASys» cargado en el editor — pulsa Aceptar para aplicarlo")

    # ── Aplicar / cargar al arrancar ──────────────────────────────────────

    def aplicar_configuracion(self, dialog):
        """Aplica la configuración de visibilidad/orden editada en el
        diálogo, y fija el perfil en edición como perfil activo."""
        new_config = self._leer_config_desde_tree()

        if save_tab_config(new_config):
            visible_ordered = [c['name'] for c in new_config['tabs'] if c.get('visible', True)]
            self._build_nav_tree(name_list=visible_ordered)
            if self._current_section in self.tree_items:
                self.seleccionar_seccion(self._current_section)
            elif self.tree_items:
                self.seleccionar_seccion(next(iter(self.tree_items)))
            self._set_perfil_nav_activo(getattr(self, "_perfil_en_edicion", self._perfil_nav_activo()))
            dialog.accept()
            self._avisar("Configuración", "Configuración guardada y aplicada correctamente")
        else:
            self._avisar("Error", "No se pudo guardar la configuración", QMessageBox.Critical)

    def _load_and_apply_tab_config(self):
        """Carga el fichero de configuración y aplica orden/visibilidad al
        arrancar la aplicación."""
        if not os.path.exists(CONFIG_FILE):
            return

        synced_config = self._synced_tab_config()
        save_tab_config(synced_config)
        self.nav_profiles_db.asegurar_perfil_predeterminado(synced_config)

        visible_ordered = [c['name'] for c in synced_config['tabs'] if c.get('visible', True)]
        self._build_nav_tree(name_list=visible_ordered)
