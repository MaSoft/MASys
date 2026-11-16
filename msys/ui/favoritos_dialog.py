# -*- coding: utf-8 -*-
"""Diálogo del Gestor de favoritos (sección 🌐 Navegadores). Amplía el
visor de Marcadores (solo lectura) a un gestor completo: cargar, editar
(doble clic en título/URL), reorganizar (arrastrar y soltar), comprobar
enlaces rotos, exportar al HTML estándar que importa cualquier navegador,
importar desde ese HTML y volver a escribir los marcadores en el propio
Chrome/Edge sustituyendo los actuales (con copia de seguridad previa y el
navegador cerrado).

Motor en msys/domain/favoritos.py. Diálogo autónomo (no depende del "self"
de ShareInspector salvo parent._dash_colors() si existe)."""

import time

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QTreeWidget, QTreeWidgetItem, QMessageBox, QProgressBar, QInputDialog,
    QAbstractItemView,
)

from msys.domain import favoritos as fav
from msys.core import tareas
from msys.core.backup import respaldar_archivo
from msys.core.utils import ruta_guardado_predeterminada, dialogo_guardar_archivo, dialogo_abrir_archivo

# Rol donde guardamos (tipo, raiz, estado, detalle) en cada item del árbol
_ROL = Qt.UserRole
# Colores de estado de enlace
_COLOR = {fav.ROTO: "#dc2626", fav.SINRED: "#ca8a04", fav.REDIRIGE: "#2563eb", fav.OK: "#16a34a"}


class GestorFavoritosDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Gestor de favoritos")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(860, 620)
        self._tarea = None

        c = (parent._dash_colors() if parent and hasattr(parent, "_dash_colors") else
             {"title": "#1a2634", "muted": "#8a94a6"})

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 18, 20, 16)

        # Cabecera
        cab = QHBoxLayout()
        cab.setSpacing(12)
        icono = QLabel("🔖")
        icono.setStyleSheet("font-size: 24pt; background: transparent; border: none;")
        cab.addWidget(icono)
        col = QVBoxLayout()
        col.setSpacing(2)
        t = QLabel("Gestor de favoritos")
        t.setStyleSheet(f"font-size: 13pt; font-weight: 600; color: {c['title']}; background: transparent; border: none;")
        col.addWidget(t)
        s = QLabel("Edita, comprueba enlaces, reorganiza y vuelve a escribirlos en el navegador o migra a otro")
        s.setWordWrap(True)
        s.setStyleSheet(f"color: {c['muted']}; font-size: 9.5pt; background: transparent; border: none;")
        col.addWidget(s)
        cab.addLayout(col, 1)
        layout.addLayout(cab)

        # Fila: perfil + cargar
        fila = QHBoxLayout()
        fila.addWidget(QLabel("Perfil:"))
        self.combo = QComboBox()
        self._perfiles = fav.perfiles_favoritos()
        for p in self._perfiles:
            self.combo.addItem(f"{p.navegador} — {p.nombre_perfil}", p)
        fila.addWidget(self.combo, 1)
        self.btn_cargar = QPushButton("🔄 Cargar")
        self.btn_cargar.setObjectName("primary")
        self.btn_cargar.clicked.connect(self.cargar)
        fila.addWidget(self.btn_cargar)
        layout.addLayout(fila)

        if not self._perfiles:
            aviso = QLabel("No se encontró ningún perfil de navegador en este equipo.")
            aviso.setObjectName("hintLabel")
            layout.addWidget(aviso)

        # Acciones de edición
        acc = QHBoxLayout()
        acc.setSpacing(6)
        self.btn_carpeta = QPushButton("➕ Carpeta")
        self.btn_carpeta.clicked.connect(self.nueva_carpeta)
        acc.addWidget(self.btn_carpeta)
        self.btn_quitar = QPushButton("✕ Quitar")
        self.btn_quitar.clicked.connect(self.quitar)
        acc.addWidget(self.btn_quitar)
        self.btn_enlaces = QPushButton("🔗 Comprobar enlaces")
        self.btn_enlaces.clicked.connect(self.comprobar_enlaces)
        acc.addWidget(self.btn_enlaces)
        acc.addStretch()
        self.lbl_estado = QLabel("Elige un perfil y pulsa «Cargar».")
        self.lbl_estado.setObjectName("fieldLabel")
        acc.addWidget(self.lbl_estado)
        layout.addLayout(acc)

        # Árbol editable
        self.arbol = QTreeWidget()
        self.arbol.setColumnCount(3)
        self.arbol.setHeaderLabels(["Título / carpeta", "URL", "Enlace"])
        self.arbol.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.arbol.setDragDropMode(QAbstractItemView.InternalMove)
        self.arbol.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.SelectedClicked)
        self.arbol.setUniformRowHeights(True)
        self.arbol.header().setStretchLastSection(False)
        self.arbol.setColumnWidth(0, 340)
        self.arbol.setColumnWidth(1, 360)
        layout.addWidget(self.arbol, 1)

        self.barra = QProgressBar()
        self.barra.setVisible(False)
        layout.addWidget(self.barra)

        # Botones inferiores
        bot = QHBoxLayout()
        self.btn_exportar = QPushButton("📤 Exportar (HTML estándar)")
        self.btn_exportar.clicked.connect(self.exportar)
        bot.addWidget(self.btn_exportar)
        self.btn_importar = QPushButton("📥 Importar HTML…")
        self.btn_importar.clicked.connect(self.importar)
        bot.addWidget(self.btn_importar)
        self.btn_escribir = QPushButton("💾 Sustituir en el navegador")
        self.btn_escribir.clicked.connect(self.escribir)
        bot.addWidget(self.btn_escribir)
        bot.addStretch()
        self.btn_cerrar = QPushButton("Cerrar")
        self.btn_cerrar.clicked.connect(self.accept)
        bot.addWidget(self.btn_cerrar)
        layout.addLayout(bot)

        self._habilitar(False)

    # ── helpers de item ──────────────────────────────────────────────
    def _nuevo_item(self, tipo, titulo, url="", raiz=""):
        it = QTreeWidgetItem([titulo, url if tipo == "url" else "", ""])
        it.setData(0, _ROL, {"tipo": tipo, "raiz": raiz, "estado": "", "detalle": ""})
        flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsDragEnabled
        if tipo in ("folder", "raiz"):
            flags |= Qt.ItemIsDropEnabled
        if tipo == "folder":
            flags |= Qt.ItemIsEditable
            it.setForeground(0, Qt.darkGray)
        elif tipo == "url":
            flags |= Qt.ItemIsEditable
        if tipo == "raiz":
            flags &= ~Qt.ItemIsDragEnabled  # las tres raíces de Chromium no se mueven
        it.setFlags(flags)
        return it

    def _meta(self, it):
        return it.data(0, _ROL) or {}

    # ── cargar ───────────────────────────────────────────────────────
    def cargar(self):
        perfil = self.combo.currentData()
        if not perfil:
            return
        self.lbl_estado.setText("🔄 Cargando…")
        try:
            favoritos = fav.cargar(perfil)
        except Exception as e:
            self._avisar("Error", f"No se pudieron cargar los marcadores:\n\n{e}", QMessageBox.Warning)
            return
        self._poblar(perfil, favoritos)
        self._actualizar_estado(favoritos)

    def _poblar(self, perfil, favoritos):
        self.arbol.clear()
        self._raices = {}
        if fav.es_chromium(perfil):
            for clave in ("bookmark_bar", "other", "synced"):
                raiz = self._nuevo_item("raiz", f"📁 {fav.RAICES[clave]}", raiz=clave)
                self.arbol.addTopLevelItem(raiz)
                raiz.setExpanded(True)
                self._raices[clave] = raiz
            for f in favoritos:
                self._colocar(self._raices.get(f.raiz) or self._raices["other"], f)
        else:
            raiz = self._nuevo_item("raiz", f"📁 {perfil.navegador} — {perfil.nombre_perfil}", raiz="")
            self.arbol.addTopLevelItem(raiz)
            raiz.setExpanded(True)
            self._raices[""] = raiz
            for f in favoritos:
                self._colocar(raiz, f)
        self._habilitar(True)
        # Firefox no se puede reescribir: solo exportar
        self.btn_escribir.setEnabled(fav.es_chromium(perfil))

    def _colocar(self, raiz_item, f):
        """Crea las carpetas que falten bajo la raíz y cuelga el marcador."""
        nodo = raiz_item
        for parte in [p for p in f.carpeta.split("/") if p]:
            hijo = None
            for i in range(nodo.childCount()):
                h = nodo.child(i)
                if self._meta(h).get("tipo") == "folder" and h.text(0) == parte:
                    hijo = h
                    break
            if hijo is None:
                hijo = self._nuevo_item("folder", parte)
                nodo.addChild(hijo)
                hijo.setExpanded(True)
            nodo = hijo
        it = self._nuevo_item("url", f.titulo, f.url)
        self._pintar_estado(it, f.estado, f.detalle)
        nodo.addChild(it)

    def _pintar_estado(self, it, estado, detalle):
        m = self._meta(it)
        m["estado"], m["detalle"] = estado, detalle
        it.setData(0, _ROL, m)
        it.setText(2, estado)
        it.setToolTip(2, detalle)
        if estado in _COLOR:
            from PyQt5.QtGui import QColor
            it.setForeground(2, QColor(_COLOR[estado]))

    # ── recoger favoritos del árbol ──────────────────────────────────
    def _recoger(self):
        favoritos = []

        def _rec(nodo, raiz, carpeta):
            for i in range(nodo.childCount()):
                h = nodo.child(i)
                m = self._meta(h)
                if m.get("tipo") == "url":
                    favoritos.append(fav.Favorito(raiz, carpeta, h.text(0), h.text(1), m.get("estado", ""), m.get("detalle", "")))
                elif m.get("tipo") == "folder":
                    sub = f"{carpeta}/{h.text(0)}" if carpeta else h.text(0)
                    _rec(h, raiz, sub)

        for i in range(self.arbol.topLevelItemCount()):
            raiz_item = self.arbol.topLevelItem(i)
            _rec(raiz_item, self._meta(raiz_item).get("raiz", ""), "")
        return favoritos

    # ── acciones de edición ──────────────────────────────────────────
    def nueva_carpeta(self):
        sel = self.arbol.currentItem()
        destino = sel if sel and self._meta(sel).get("tipo") in ("folder", "raiz") else \
            (sel.parent() if sel else None) or (self.arbol.topLevelItem(0) if self.arbol.topLevelItemCount() else None)
        if destino is None:
            return
        nombre, ok = QInputDialog.getText(self, "Nueva carpeta", "Nombre de la carpeta:")
        if not ok or not nombre.strip():
            return
        it = self._nuevo_item("folder", nombre.strip())
        destino.addChild(it)
        destino.setExpanded(True)

    def quitar(self):
        items = [i for i in self.arbol.selectedItems() if self._meta(i).get("tipo") != "raiz"]
        if not items:
            self._avisar("Quitar", "Selecciona uno o varios marcadores o carpetas (las raíces no se pueden quitar).")
            return
        con_hijos = sum(1 for i in items if i.childCount())
        aviso = "" if not con_hijos else f" (y su contenido en {con_hijos} carpeta[s])"
        if not self._confirmar("Quitar", f"¿Quitar {len(items)} elemento(s){aviso} de la lista?\n\n"
                               "No cambia el navegador hasta que pulses «Sustituir»."):
            return
        for it in items:
            (it.parent() or self.arbol.invisibleRootItem()).removeChild(it)

    def comprobar_enlaces(self):
        favoritos = [f for f in self._recoger() if f.url]
        if not favoritos:
            self._avisar("Comprobar enlaces", "No hay marcadores cargados.")
            return
        self._ocupado(True)
        self.barra.setVisible(True)
        self.barra.setRange(0, 0)  # indeterminada: la actualización por ítem ocurre en el hilo trabajador
        self.lbl_estado.setText(f"🔗 Comprobando {len(favoritos)} enlaces…")
        self._tarea = tareas.lanzar(
            self, "_tarea", fav.comprobar_enlaces, favoritos,
            al_terminar=self._enlaces_listos, al_fallar=self._enlaces_error)

    def _enlaces_listos(self, favoritos):
        # vuelca estados sobre los items por URL
        por_url = {}
        for f in favoritos:
            por_url.setdefault(f.url, f)

        def _rec(nodo):
            for i in range(nodo.childCount()):
                h = nodo.child(i)
                m = self._meta(h)
                if m.get("tipo") == "url" and h.text(1) in por_url:
                    f = por_url[h.text(1)]
                    self._pintar_estado(h, f.estado, f.detalle)
                else:
                    _rec(h)
        for i in range(self.arbol.topLevelItemCount()):
            _rec(self.arbol.topLevelItem(i))
        self.barra.setVisible(False)
        self._ocupado(False)
        r = fav.resumen(favoritos)
        self.lbl_estado.setText(f"✅ {r['total']} comprobados · {r['rotos']} rotos · {r['redirigen']} redirigen · {r['sin_conexion']} sin conexión")

    def _enlaces_error(self, msg):
        self.barra.setVisible(False)
        self._ocupado(False)
        self._avisar("Comprobar enlaces", f"No se pudo completar:\n\n{msg}", QMessageBox.Warning)

    # ── exportar / importar ──────────────────────────────────────────
    def exportar(self):
        favoritos = self._recoger()
        if not favoritos:
            self._avisar("Exportar", "No hay marcadores que exportar.")
            return
        nombre = f"Favoritos_{time.strftime('%Y%m%d_%H%M%S')}.html"
        ruta = dialogo_guardar_archivo(self, "Exportar favoritos (HTML)", ruta_guardado_predeterminada(nombre), "HTML (*.html)")
        if not ruta:
            return
        try:
            fav.exportar_netscape(favoritos, ruta)
            self._avisar("Exportado", f"Guardados {len(favoritos)} marcadores en:\n\n{ruta}\n\n"
                         "Impórtalo desde cualquier navegador (Favoritos → Importar).")
        except OSError as e:
            self._avisar("Error", f"No se pudo exportar:\n\n{e}", QMessageBox.Critical)

    def importar(self):
        ruta = dialogo_abrir_archivo(self, "Importar favoritos (HTML)", ruta_guardado_predeterminada(""), "HTML (*.html *.htm)")
        if not ruta:
            return
        try:
            nuevos = fav.importar_netscape(ruta)
        except OSError as e:
            self._avisar("Error", f"No se pudo leer el archivo:\n\n{e}", QMessageBox.Critical)
            return
        if not nuevos:
            self._avisar("Importar", "No se encontraron marcadores en ese archivo.")
            return
        destino = self.arbol.topLevelItem(0) if self.arbol.topLevelItemCount() else None
        if destino is None:
            self._avisar("Importar", "Carga antes un perfil para saber dónde añadirlos.")
            return
        carpeta = self._nuevo_item("folder", f"Importado {time.strftime('%d/%m/%Y')}")
        destino.addChild(carpeta)
        for f in nuevos:
            self._colocar(carpeta, fav.Favorito("", f.carpeta, f.titulo, f.url))
        destino.setExpanded(True)
        self.lbl_estado.setText(f"📥 Importados {len(nuevos)} marcadores (pulsa «Sustituir» para escribirlos)")

    # ── escribir en el navegador ─────────────────────────────────────
    def escribir(self):
        perfil = self.combo.currentData()
        if not perfil or not fav.es_chromium(perfil):
            self._avisar("Sustituir", "Solo se puede escribir en Chrome o Edge. Firefox: usa «Exportar» e importa desde Firefox.")
            return
        favoritos = self._recoger()
        if fav.navegador_en_ejecucion(perfil.navegador):
            self._avisar("Cierra el navegador", f"{perfil.navegador} está abierto. Ciérralo del todo antes de sustituir "
                         "los marcadores (si no, al cerrarlo descartaría los cambios).", QMessageBox.Warning)
            return
        if not self._confirmar("Sustituir marcadores",
                               f"Se SUSTITUIRÁN todos los marcadores de {perfil.navegador} ({perfil.nombre_perfil}) "
                               f"por los {len(favoritos)} de esta lista.\n\nSe hará una copia de seguridad del archivo "
                               "original primero. ¿Continuar?"):
            return
        try:
            mensaje = fav.escribir_chromium(perfil, favoritos, respaldar=respaldar_archivo)
        except Exception as e:
            self._avisar("Error", f"No se pudieron escribir los marcadores:\n\n{e}", QMessageBox.Critical)
            return
        self._auditar(perfil, len(favoritos))
        self._avisar("Hecho", mensaje + "\n\nAbre el navegador para verlos.")
        self.lbl_estado.setText(f"💾 {len(favoritos)} marcadores escritos en {perfil.navegador}")

    def _auditar(self, perfil, n):
        padre = self.parent()
        db = getattr(padre, "repair_audit_db", None)
        if db and hasattr(db, "registrar"):
            try:
                db.registrar(accion="Sustituir favoritos", objetivo=f"{perfil.navegador}/{perfil.nombre_perfil}",
                             detalle=f"{n} marcadores", resultado="OK")
            except Exception:
                pass

    # ── estado de la UI ──────────────────────────────────────────────
    def _habilitar(self, on):
        for b in (self.btn_carpeta, self.btn_quitar, self.btn_enlaces, self.btn_exportar, self.btn_escribir):
            b.setEnabled(on)
        self.btn_importar.setEnabled(on)

    def _ocupado(self, on):
        for b in (self.btn_cargar, self.btn_carpeta, self.btn_quitar, self.btn_enlaces,
                  self.btn_exportar, self.btn_importar, self.btn_escribir):
            b.setEnabled(not on)

    def _actualizar_estado(self, favoritos):
        r = fav.resumen(favoritos)
        dup = f" · {r['duplicados']} duplicados" if r["duplicados"] else ""
        self.lbl_estado.setText(f"✅ {r['total']} marcadores{dup}")

    def _avisar(self, titulo, texto, icono=QMessageBox.Information):
        box = QMessageBox(self)
        box.setWindowFlags(box.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        box.setWindowTitle(titulo)
        box.setText(texto)
        box.setIcon(icono)
        box.exec_()

    def _confirmar(self, titulo, texto):
        box = QMessageBox(self)
        box.setWindowFlags(box.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        box.setWindowTitle(titulo)
        box.setIcon(QMessageBox.Question)
        box.setText(texto)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        return box.exec_() == QMessageBox.Yes


def abrir_gestor_favoritos(parent):
    GestorFavoritosDialog(parent).exec_()
