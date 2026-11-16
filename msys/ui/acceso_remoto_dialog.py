# -*- coding: utf-8 -*-
"""Ventana «🖥️ Acceso remoto» (4.6.0): agenda de equipos de clientes y
conexión en un clic (RustDesk, Asistencia rápida, Escritorio remoto, ver la
sesión de un usuario, ofrecer asistencia), con historial. 4.11.0: «🛰️ Servidor
propio» (servidor de RustDesk del técnico y cliente para enviar con él dentro).

NO es una sección del árbol: botón bajo el árbol, entre Base de conocimiento
y Configuración (multisys.py). Ventana única y NO modal, como la Base de
conocimiento.

- Izquierda: árbol Cliente › Equipo (clic en el cliente = plegar/desplegar,
  doble clic en el equipo = conectar con su método; columnas ajustables y
  ordenables; desplegar/plegar todo en la cabecera).
- Derecha (panel lateral): ficha del equipo con sus métodos, o su editor.

Motor en msys/domain/acceso_remoto.py."""

import csv
import json
import os
import re
from typing import List, Optional

from PyQt5.QtCore import Qt, QTimer, QUrl
from PyQt5.QtGui import QDesktopServices, QGuiApplication
from PyQt5.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QHBoxLayout, QHeaderView, QInputDialog,
    QLabel, QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QSplitter, QStackedWidget,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from msys.core import informe_html
from msys.core import tareas
from msys.core.utils import (dialogo_abrir_archivo, dialogo_elegir_carpeta, dialogo_guardar_archivo,
                              ruta_guardado_predeterminada)
from msys.domain import acceso_remoto as ar
from msys.domain import clientes as cl
from msys.domain import servidor_rustdesk as sr
from msys.models.tabla_simple import COLOR_ERROR, COLOR_OK, TablaSimple
from msys.ui import piezas
from msys.ui.conocimiento_dialog import _colores, _etiqueta, _IconosCabecera, _scroll

_ROL_EQUIPO = Qt.UserRole        # id del equipo (hojas)
_ROL_CLIENTE = Qt.UserRole + 1   # nombre del cliente (nodos)
_ROL_BUSCAR = Qt.UserRole + 2    # texto en el que busca el buscador (incluye ID y dirección)
COLUMNAS = ["Equipo", "Usuario", "Correo", "Teléfono", "Método", "Última conexión"]


def _caja(padre, titulo: str, texto: str, icono=QMessageBox.Information) -> QMessageBox:
    caja = QMessageBox(padre)
    caja.setWindowFlags(caja.windowFlags() & ~Qt.WindowContextHelpButtonHint)
    caja.setWindowTitle(titulo)
    caja.setText(texto)
    caja.setIcon(icono)
    return caja


class AccesoRemotoDialog(QDialog):
    def __init__(self, host=None, db: Optional[ar.AccesoDB] = None):
        super().__init__(host)
        self.host = host
        self.db = db if db is not None else ar.AccesoDB()
        self.clientes = cl.ClientesDB(self.db.path)       # catálogo de empresas y usuarios, misma multisys.db
        self.setWindowTitle("🖥️ Acceso remoto")
        self.setWindowFlags((self.windowFlags() | Qt.WindowMinMaxButtonsHint) & ~Qt.WindowContextHelpButtonHint)
        self.setModal(False)
        self.setMinimumSize(860, 520)
        self._equipos: List[ar.Equipo] = []
        self._actual: Optional[str] = None
        self._editando: Optional[ar.Equipo] = None
        self._tarea = None

        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.setContentsMargins(18, 16, 18, 14)

        cab = QHBoxLayout()
        cab.setSpacing(12)
        cab.addWidget(_etiqueta("🖥️", "font-size: 22pt;", ajustar=False))
        col = QVBoxLayout()
        col.setSpacing(2)
        self.lbl_titulo = _etiqueta("Acceso remoto", ajustar=False)
        self.lbl_sub = _etiqueta("Equipos de clientes y conexión en un clic", ajustar=False)
        col.addWidget(self.lbl_titulo)
        col.addWidget(self.lbl_sub)
        cab.addLayout(col, 1)
        # En la cabecera: la barra de abajo no tiene sitio a 984 px para un botón más
        self.btn_servidor = piezas.boton("🛰️ Servidor propio", "ghost", self.abrir_servidor,
                                         "Tu servidor de RustDesk y el programa para enviar al cliente")
        self.btn_empresas = piezas.boton("👥 Empresas", "ghost", self.abrir_empresas,
                                         "Empresas y usuarios de tus clientes (5.0.0)")
        cab.addWidget(self.btn_empresas, 0, Qt.AlignVCenter)
        self.btn_correo = piezas.boton("✉️ Correo", "ghost", self.abrir_correo,
                                       "Cuentas para enviar correos a los clientes, plantillas y enviados (5.1.0)")
        cab.addWidget(self.btn_correo, 0, Qt.AlignVCenter)
        cab.addWidget(self.btn_servidor, 0, Qt.AlignVCenter)
        lay.addLayout(cab)

        # Barra: búsqueda · nuevo · conectar · enviar al cliente · historial · hueco · recuento
        self.busqueda = QLineEdit()
        self.busqueda.setPlaceholderText("Buscar equipo")
        self.busqueda.setClearButtonEnabled(True)
        self.busqueda.setMinimumWidth(180)
        self.busqueda.textChanged.connect(lambda _t: self._filtrar())
        self.btn_nuevo = piezas.boton("＋ Equipo", "primary", self.nuevo_equipo, "Añadir un equipo a la agenda")
        self.btn_conectar = piezas.boton("🔌 Conectar", "primary", lambda: self.conectar(self._equipo_actual()),
                                         "Conectar con el método preferido del equipo")
        self.btn_enviar = QPushButton("📨 Enviar al cliente")
        self.btn_enviar.setObjectName("ghost")
        self.btn_enviar.setToolTip("Copiar las instrucciones para el cliente")
        menu = QMenu(self.btn_enviar)
        menu.addAction("RustDesk", lambda: self.copiar_para_cliente(ar.RUSTDESK))
        menu.addAction("RustDesk con mi servidor", self.copiar_para_cliente_propio)
        menu.addAction("Asistencia rápida", lambda: self.copiar_para_cliente(ar.ASISTENCIA))
        menu.addAction("Agente de análisis", self.copiar_para_agente)
        menu.addSeparator()
        menu.addAction("✉️ Por correo…", lambda: self.redactar(self._equipo_actual()))
        self.btn_enviar.setMenu(menu)
        self.btn_historial = piezas.boton("📋 Historial", "ghost", self.ver_historial, "Conexiones lanzadas")
        self.btn_informes = piezas.boton("📥 Informes del agente", "ghost", self.ver_informes_agente,
                                         "Informes .msysinf de los equipos de tus clientes")
        self.lbl_recuento = QLabel("")
        self.lbl_recuento.setObjectName("countLabel")
        barra = QHBoxLayout()
        barra.setSpacing(8)
        for w in (self.busqueda, self.btn_nuevo, self.btn_conectar, self.btn_enviar, self.btn_historial, self.btn_informes):
            barra.addWidget(w, 2 if w is self.busqueda else 0)
        barra.addStretch(1)
        barra.addWidget(self.lbl_recuento)
        lay.addLayout(barra)

        # Árbol Cliente › Equipo | panel
        self.arbol = QTreeWidget()
        self.arbol.setColumnCount(len(COLUMNAS))
        self.arbol.setHeaderLabels(COLUMNAS)
        self.arbol.setUniformRowHeights(True)
        self.arbol.setExpandsOnDoubleClick(False)
        self.arbol.setIndentation(16)
        self.arbol.setAlternatingRowColors(True)
        self.arbol.setSortingEnabled(True)
        self.arbol.sortByColumn(0, Qt.AscendingOrder)
        cab = self.arbol.header()
        cab.setStretchLastSection(False)
        cab.setSectionResizeMode(QHeaderView.Interactive)
        cab.setSectionResizeMode(0, QHeaderView.Stretch)
        self.arbol.itemClicked.connect(self._clic_arbol)
        self.arbol.itemDoubleClicked.connect(self._doble_clic)
        self.arbol.currentItemChanged.connect(lambda act, _a: self._item_actual(act))
        self.arbol.setContextMenuPolicy(Qt.CustomContextMenu)
        self.arbol.customContextMenuRequested.connect(self._menu_arbol)
        self._iconos_cab = _IconosCabecera(self.arbol)

        self.panel = QStackedWidget()
        self.panel.setMinimumWidth(300)
        self.pag_vacia = QWidget()
        lv = QVBoxLayout(self.pag_vacia)
        self.lbl_vacio = _etiqueta("", ajustar=True)
        self.lbl_vacio.setAlignment(Qt.AlignCenter)
        lv.addWidget(self.lbl_vacio)
        self.panel.addWidget(self.pag_vacia)
        self.contenido_ficha = QWidget()
        self.lay_ficha = QVBoxLayout(self.contenido_ficha)
        self.lay_ficha.setContentsMargins(4, 0, 8, 8)
        self.lay_ficha.setSpacing(8)
        self.pag_ficha = _scroll(self.contenido_ficha, "arFicha")
        self.panel.addWidget(self.pag_ficha)
        self.pag_editor = self._crear_editor()
        self.panel.addWidget(self.pag_editor)

        div = QSplitter(Qt.Horizontal)
        div.splitterMoved.connect(lambda *_a: self._ajustar_columnas())
        div.addWidget(self.arbol)
        div.addWidget(self.panel)
        div.setStretchFactor(0, 1)
        div.setStretchFactor(1, 0)
        div.setCollapsible(0, False)
        div.setCollapsible(1, False)
        div.setSizes([760, 330])
        lay.addWidget(div, 1)

        self.aplicar_tema()
        self.recargar()
        self._ajustar_a_pantalla()

    # ── Tema y tamaño ───────────────────────────────────────────────────
    def aplicar_tema(self):
        c = self._c = _colores(self.host)
        self.lbl_titulo.setStyleSheet(f"font-size: 13pt; font-weight: 600; color: {c['title']}; background: transparent; border: none;")
        self.lbl_sub.setStyleSheet(f"color: {c['muted']}; font-size: 9.5pt; background: transparent; border: none;")
        self.lbl_vacio.setStyleSheet(f"color: {c['muted']}; font-size: 10.5pt; background: transparent;")
        if self.host is not None and hasattr(self.host, "_apply_titlebar_theme_to") and self.isVisible():
            self.host._apply_titlebar_theme_to(self)
        if self.panel.currentWidget() is self.pag_ficha and self._actual:
            self._mostrar_ficha()

    def showEvent(self, e):
        super().showEvent(e)
        if self.host is not None and hasattr(self.host, "_apply_titlebar_theme_to"):
            self.host._apply_titlebar_theme_to(self)
        QTimer.singleShot(0, self._iconos_cab.recolocar)
        QTimer.singleShot(0, self._ajustar_columnas)

    def _ajustar_a_pantalla(self):
        pantalla = (self.host.screen() if self.host is not None and hasattr(self.host, "screen") else None) \
            or QApplication.primaryScreen()
        zona = pantalla.availableGeometry() if pantalla else None
        ancho, alto = 1180, 700
        if zona is not None:
            ancho, alto = min(ancho, zona.width() - 40), min(alto, zona.height() - 60)
        self.resize(max(ancho, self.minimumWidth()), max(alto, self.minimumHeight()))

    # ── Datos ───────────────────────────────────────────────────────────
    def recargar(self, seleccionar: Optional[str] = None):
        try:
            self._equipos = self.db.listar()
        except Exception as e:
            self._equipos = []
            self._aviso(f"No se pudo leer la agenda: {e}", QMessageBox.Warning)
        self._pintar(seleccionar or self._actual)

    def _pintar(self, seleccionar: Optional[str]):
        abiertos = {self.arbol.topLevelItem(i).data(0, _ROL_CLIENTE) for i in range(self.arbol.topLevelItemCount())
                    if self.arbol.topLevelItem(i).isExpanded()}
        primera_vez = self.arbol.topLevelItemCount() == 0
        self.arbol.blockSignals(True)
        self.arbol.setSortingEnabled(False)
        self.arbol.clear()
        nodos = {}
        item_sel = None
        for e in self._equipos:
            nodo = nodos.get(e.cliente)
            if nodo is None:
                nodo = nodos[e.cliente] = QTreeWidgetItem(self.arbol, [e.cliente])
                nodo.setData(0, _ROL_CLIENTE, e.cliente)
                nodo.setToolTip(0, e.cliente)
                f = nodo.font(0)
                f.setBold(True)
                nodo.setFont(0, f)
            fecha, hora = ar.fecha_hora(e.ultima)
            hijo = QTreeWidgetItem(nodo, [e.nombre, e.contacto, e.correo, e.telefono,
                                          ar.NOMBRE_METODO.get(e.metodo, e.metodo), f"{fecha} {hora}".strip()])
            hijo.setData(0, _ROL_EQUIPO, e.id)
            hijo.setData(0, _ROL_BUSCAR, " ".join([e.nombre, e.cliente, ar.NOMBRE_METODO.get(e.metodo, ""),
                                                   e.rustdesk_id, e.direccion, e.usuario, e.notas, e.contacto,
                                                   e.correo, e.telefono]).lower())
            dato = " · ".join(x for x in (e.rustdesk_id, e.direccion) if x)
            hijo.setToolTip(0, f"{e.nombre} · {e.cliente} · {e.contacto}" + (f" · {dato}" if dato else ""))
            for col in range(1, len(COLUMNAS)):
                hijo.setToolTip(col, hijo.text(col))
            if e.id == seleccionar:
                item_sel = hijo
        for cliente, nodo in nodos.items():
            nodo.setText(0, f"{cliente}  ({nodo.childCount()})")
            nodo.setFirstColumnSpanned(True)       # el nombre del cliente ocupa todo el ancho
            nodo.setExpanded(primera_vez or cliente in abiertos)
        self.arbol.setSortingEnabled(True)
        self.arbol.blockSignals(False)
        self._ajustar_columnas()
        self._filtrar()
        if item_sel is not None:
            item_sel.parent().setExpanded(True)
            self.arbol.setCurrentItem(item_sel)
            self.arbol.scrollToItem(item_sel)
            self._actual = seleccionar
            if self.panel.currentWidget() is not self.pag_editor:
                self._mostrar_ficha()
        elif self.panel.currentWidget() is not self.pag_editor:
            self._actual = None
            self._mostrar_vacio()
        self._recuento()

    # Si la tabla no cabe a lo ancho se ocultan, en este orden, Método y Correo (siguen en la ficha, en el texto
    # al pasar el ratón y en el buscador): nunca textos recortados ni barra horizontal (5.0.0).
    _COLUMNAS_OPCIONALES = (4, 2)

    def _ajustar_columnas(self):
        """Cada columna (menos la de Equipo, que se estira) al ancho de su contenido y nunca menos que su título."""
        from PyQt5.QtGui import QFont, QFontMetrics
        f = QFont(self.arbol.header().font())
        f.setBold(True)
        fm = QFontMetrics(f)
        anchos = {}
        for c in range(1, len(COLUMNAS)):
            self.arbol.setColumnHidden(c, False)
            self.arbol.resizeColumnToContents(c)
            minimo = fm.horizontalAdvance(COLUMNAS[c]) + 28
            # resizeColumnToContents se queda unos píxeles corto con la hoja de estilos de MASys
            anchos[c] = min(max(self.arbol.columnWidth(c) + 14, minimo), 320)
            self.arbol.setColumnWidth(c, anchos[c])
        # Lo que necesita la columna Equipo: los nombres de los equipos (las filas de empresa ocupan todo el ancho)
        necesita = fm.horizontalAdvance(COLUMNAS[0]) + 60
        for i in range(self.arbol.topLevelItemCount()):
            nodo = self.arbol.topLevelItem(i)
            for j in range(nodo.childCount()):
                hijo = nodo.child(j)
                necesita = max(necesita, QFontMetrics(hijo.font(0)).horizontalAdvance(hijo.text(0))
                               + 2 * self.arbol.indentation() + 24)
        total = necesita + sum(anchos.values())
        for c in self._COLUMNAS_OPCIONALES:
            if total <= self.arbol.viewport().width():
                break
            self.arbol.setColumnHidden(c, True)
            total -= anchos[c]

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, self._ajustar_columnas)

    def _filtrar(self):
        texto = self.busqueda.text().strip().lower()
        for i in range(self.arbol.topLevelItemCount()):
            nodo = self.arbol.topLevelItem(i)
            del_cliente = texto in nodo.data(0, _ROL_CLIENTE).lower() if texto else True
            visibles = 0
            for j in range(nodo.childCount()):
                hijo = nodo.child(j)
                ver = (del_cliente or texto in (hijo.data(0, _ROL_BUSCAR) or "")
                       or any(texto in hijo.text(c).lower() for c in range(len(COLUMNAS))))
                hijo.setHidden(not ver)
                visibles += ver
            nodo.setHidden(visibles == 0)
            if texto and visibles:
                nodo.setExpanded(True)
        self._recuento()

    def _recuento(self):
        total = len(self._equipos)
        vis = sum(1 for i in range(self.arbol.topLevelItemCount()) for j in range(self.arbol.topLevelItem(i).childCount())
                  if not self.arbol.topLevelItem(i).child(j).isHidden())
        if vis != total:
            self.lbl_recuento.setText(f"{vis} de {total}")
        else:
            self.lbl_recuento.setText("1 equipo" if total == 1 else f"{total} equipos")
        self.btn_conectar.setEnabled(self._equipo_actual() is not None and self.panel.currentWidget() is not self.pag_editor)

    def _mostrar_vacio(self):
        self.lbl_vacio.setText("Añade el primer equipo con «＋ Equipo»" if not self._equipos else "Elige un equipo de la lista")
        self.panel.setCurrentWidget(self.pag_vacia)
        self.btn_conectar.setEnabled(False)

    def _equipo_de_item(self, item) -> Optional[ar.Equipo]:
        fid = item.data(0, _ROL_EQUIPO) if item is not None else None
        return next((e for e in self._equipos if e.id == fid), None) if fid else None

    def _equipo_actual(self) -> Optional[ar.Equipo]:
        return next((e for e in self._equipos if e.id == self._actual), None)

    def _cliente_actual(self) -> str:
        item = self.arbol.currentItem()
        if item is None:
            return ""
        if item.data(0, _ROL_CLIENTE):
            return item.data(0, _ROL_CLIENTE)
        e = self._equipo_de_item(item)
        return e.cliente if e else ""

    def _clic_arbol(self, item, _col):
        if item.data(0, _ROL_CLIENTE):
            item.setExpanded(not item.isExpanded())
        else:
            self._item_actual(item)

    def _doble_clic(self, item, _col):
        e = self._equipo_de_item(item)
        if e is not None:
            self.conectar(e)

    def _item_actual(self, item):
        e = self._equipo_de_item(item)
        if e is None or (e.id == self._actual and self.panel.currentWidget() is self.pag_ficha):
            return
        if self.panel.currentWidget() is self.pag_editor and not self._descartar_edicion():
            return
        self._actual = e.id
        self._mostrar_ficha()
        self._recuento()

    def _menu_arbol(self, pos):
        e = self._equipo_de_item(self.arbol.itemAt(pos))
        if e is None:
            return
        menu = QMenu(self)
        menu.addAction("🔌 Conectar", lambda: self.conectar(e))
        menu.addAction("✏️ Editar", lambda: self.editar(e.id))
        menu.addSeparator()
        menu.addAction("✕ Eliminar", lambda: self.eliminar(e.id))
        menu.exec_(self.arbol.viewport().mapToGlobal(pos))

    # ── Ficha ───────────────────────────────────────────────────────────
    def _limpiar_ficha(self):
        while self.lay_ficha.count():
            w = self.lay_ficha.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.deleteLater()

    def _mostrar_ficha(self):
        e = self._equipo_actual()
        if e is None:
            self._mostrar_vacio()
            return
        c = self._c
        self._limpiar_ficha()
        self.lay_ficha.addWidget(_etiqueta(e.nombre, f"font-size: 14pt; font-weight: 600; color: {c['title']};"))
        self.lay_ficha.addWidget(_etiqueta(f"{e.cliente} · {ar.NOMBRE_METODO.get(e.metodo, e.metodo)}", f"color: {c['muted']};"))

        conectar = piezas.boton(f"🔌 Conectar ({ar.NOMBRE_METODO.get(e.metodo, e.metodo)})", "primary",
                                lambda: self.conectar(e))
        self.lay_ficha.addWidget(conectar)

        datos = [("Empresa", e.cliente), ("Usuario", e.contacto), ("Correo", e.correo), ("Teléfono", e.telefono),
                 ("ID de RustDesk", e.rustdesk_id),
                 ("Servidor de RustDesk", "Propio" if e.servidor_propio and e.rustdesk_id else ""),
                 ("Dirección", e.direccion), ("Cuenta de Windows", e.usuario),
                 ("Contraseña", "Guardada (cifrada)" if e.con_clave else "")]
        for etiqueta, valor in datos:
            if valor:
                self.lay_ficha.addWidget(_etiqueta(etiqueta, f"color: {c['muted']}; font-size: 8.5pt;", ajustar=False))
                self.lay_ficha.addWidget(_etiqueta(valor, f"color: {c['alerts_text']};"))
        if e.con_clave:
            self.lay_ficha.addWidget(piezas.boton("📋 Copiar contraseña", "ghost", lambda: self.copiar_clave(e)))
        if e.notas:
            self.lay_ficha.addWidget(_etiqueta("Notas", f"color: {c['muted']}; font-size: 8.5pt;", ajustar=False))
            self.lay_ficha.addWidget(_etiqueta(e.notas, f"color: {c['alerts_text']};"))

        otros = [k for k, _n, d in ar.METODOS if k != e.metodo and self._metodo_posible(e, k)]
        if otros:
            self.lay_ficha.addWidget(_etiqueta("Conectar con", f"font-weight: 600; color: {c['title']};", ajustar=False))
            for k in otros:
                self.lay_ficha.addWidget(piezas.boton(ar.NOMBRE_METODO[k], "ghost",
                                                      lambda k=k: self.conectar(e, k)))
        acciones = QWidget()
        fila = QHBoxLayout(acciones)
        fila.setContentsMargins(0, 6, 0, 0)
        fila.addWidget(piezas.boton("✏️ Editar", "ghost", lambda: self.editar(e.id)))
        fila.addWidget(piezas.boton("✉️ Correo…", "ghost", lambda: self.redactar(e),
                                    "Enviar un correo a este usuario con una plantilla"))
        fila.addStretch(1)
        fila.addWidget(piezas.boton("✕ Eliminar", "ghost", lambda: self.eliminar(e.id)))
        self.lay_ficha.addWidget(acciones)
        self.lay_ficha.addStretch(1)
        self.panel.setCurrentWidget(self.pag_ficha)
        self.pag_ficha.verticalScrollBar().setValue(0)

    @staticmethod
    def _metodo_posible(e: ar.Equipo, metodo: str) -> bool:
        dato = ar.DATO_METODO.get(metodo, "")
        if dato == "id":
            return bool(e.rustdesk_id or e.direccion)
        if dato == "direccion":
            return bool(e.direccion)
        return True

    # ── Conectar ────────────────────────────────────────────────────────
    def conectar(self, e: Optional[ar.Equipo], metodo: Optional[str] = None):
        if e is None:
            return
        metodo = metodo or e.metodo
        if not self._metodo_posible(e, metodo):
            self._aviso(f"Falta el dato que necesita {ar.NOMBRE_METODO.get(metodo, metodo)}: edita el equipo.")
            return
        try:
            if metodo == ar.RUSTDESK:
                exe = ar.buscar_rustdesk(self.db.ajuste("ruta_rustdesk"))
                if not exe:
                    exe = self._pedir_rustdesk()
                    if not exe:
                        return
                ar.lanzar(ar.orden_rustdesk(exe, e, self.db.clave(e.id), self.db.servidor()))
            elif metodo == ar.ASISTENCIA:
                QApplication.setOverrideCursor(Qt.WaitCursor)
                try:
                    appid = ar.buscar_asistencia_rapida()
                finally:
                    QApplication.restoreOverrideCursor()
                if not appid:
                    if self._confirmar("Asistencia rápida no está instalada en este equipo. ¿Abrir la Tienda de Microsoft para instalarla?"):
                        QDesktopServices.openUrl(QUrl(ar.URI_TIENDA_ASISTENCIA))
                    return
                ar.lanzar_asistencia_rapida(appid)
            elif metodo == ar.RDP:
                ar.lanzar(ar.orden_rdp(e))
            elif metodo == ar.SOMBRA:
                self._conectar_sombra(e)
                return
            elif metodo == ar.OFRECER:
                ar.lanzar(ar.orden_ofrecer(e))
            else:
                return
        except (ValueError, OSError, RuntimeError) as ex:
            self._registrar(e, metodo, f"Error: {ex}")
            self._aviso(f"No se pudo conectar con {e.nombre}: {ex}", QMessageBox.Warning)
            return
        self._registrar(e, metodo, "Iniciada")

    def _registrar(self, e: ar.Equipo, metodo: str, resultado: str):
        try:
            self.db.registrar(e, metodo, resultado)
        except Exception:
            pass  # el historial no debe impedir conectar
        if resultado == "Iniciada":
            self.recargar(seleccionar=e.id)

    def _conectar_sombra(self, e: ar.Equipo):
        """Lee las sesiones del equipo en segundo plano y deja elegir cuál ver."""
        self.setCursor(Qt.BusyCursor)

        def listo(sesiones):
            self.unsetCursor()
            textos = [f"{s.id} · {s.usuario} · {s.estado}" + (f" · {s.nombre}" if s.nombre else "") for s in sesiones]
            sid = self._elegir_sesion(e, sesiones, textos)
            if sid is None:
                return
            try:
                ar.lanzar(ar.orden_sombra(e, sid))
            except (ValueError, OSError) as ex:
                self._registrar(e, ar.SOMBRA, f"Error: {ex}")
                self._aviso(f"No se pudo abrir la sesión: {ex}", QMessageBox.Warning)
                return
            self._registrar(e, ar.SOMBRA, "Iniciada")

        def fallo(msg):
            self.unsetCursor()
            self._registrar(e, ar.SOMBRA, f"Error: {msg}")
            self._aviso(f"No se pudieron leer las sesiones de {e.nombre}: {msg}", QMessageBox.Warning)

        tareas.lanzar(self, "_tarea", ar.sesiones_de, e, al_terminar=listo, al_fallar=fallo)

    def _elegir_sesion(self, e, sesiones, textos) -> Optional[str]:
        dlg = QInputDialog(self)
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dlg.setWindowTitle("Ver la sesión de un usuario")
        dlg.setLabelText(f"Sesión de {e.nombre}")
        dlg.setComboBoxItems(textos)
        dlg.setOkButtonText("Conectar")
        dlg.setCancelButtonText("Cancelar")
        if dlg.exec_() != QDialog.Accepted:
            return None
        return sesiones[textos.index(dlg.textValue())].id

    def _pedir_rustdesk(self) -> str:
        caja = _caja(self, "RustDesk", "RustDesk no está instalado en este equipo.")
        descargar = caja.addButton("Descargar", QMessageBox.AcceptRole)
        buscar = caja.addButton("Buscar rustdesk.exe…", QMessageBox.ActionRole)
        caja.addButton("Cancelar", QMessageBox.RejectRole)
        caja.exec_()
        if caja.clickedButton() is descargar:
            QDesktopServices.openUrl(QUrl(ar.URL_RUSTDESK))
            return ""
        if caja.clickedButton() is buscar:
            ruta = dialogo_abrir_archivo(self, "Buscar rustdesk.exe", os.path.expanduser("~\\Downloads"),
                                         "Programas (*.exe)")
            exe = ar.buscar_rustdesk(ruta) if ruta else ""
            if ruta and not exe:
                self._aviso("Ese archivo no es RustDesk (debe ser un rustdesk….exe).", QMessageBox.Warning)
            if exe:
                self.db.fijar_ajuste("ruta_rustdesk", exe)
            return exe
        return ""

    def copiar_clave(self, e: ar.Equipo):
        clave = self.db.clave(e.id)
        if not clave:
            self._aviso("No se pudo leer la contraseña guardada (¿se guardó con otro usuario de Windows?).", QMessageBox.Warning)
            return
        QGuiApplication.clipboard().setText(clave)
        self._estado("Contraseña copiada")

    def copiar_para_cliente(self, metodo: str):
        QGuiApplication.clipboard().setText(ar.texto_para_cliente(metodo))
        self._estado("Instrucciones copiadas: pégalas en el correo o el mensaje al cliente")

    def copiar_para_cliente_propio(self):
        if not self.db.servidor().configurado:
            self._aviso("Configura primero tu servidor en «🛰️ Servidor propio» y genera el programa para el cliente.")
            return
        QGuiApplication.clipboard().setText(ar.texto_para_cliente(ar.RUSTDESK, propio=True))
        self._estado("Instrucciones copiadas: envíalas junto con el programa generado")

    def abrir_servidor(self):
        dlg = ServidorPropioDialog(self, self.db)
        dlg.exec_()
        if dlg.anadir_id:
            self.nuevo_equipo(rustdesk_id=dlg.anadir_id)
            return
        if self.panel.currentWidget() is self.pag_ficha and self._actual:
            self._mostrar_ficha()

    def abrir_empresas(self):
        from msys.ui.clientes_dialog import ClientesDialog
        ClientesDialog(self, self.clientes).exec_()

    def _estado(self, texto: str):
        self.lbl_recuento.setText(texto)
        QTimer.singleShot(4000, self._recuento)

    # ── Editor ──────────────────────────────────────────────────────────
    def _crear_editor(self):
        cont = QWidget()
        lay = QVBoxLayout(cont)
        lay.setContentsMargins(4, 0, 8, 8)
        lay.setSpacing(6)
        self.lbl_editor = _etiqueta("Nuevo equipo", "font-size: 13pt; font-weight: 600;", ajustar=False)
        lay.addWidget(self.lbl_editor)

        def campo(titulo, w):
            lay.addWidget(QLabel(titulo))
            lay.addWidget(w)
            return w

        self.ed_cliente = campo("Empresa", QComboBox())
        self.ed_cliente.setEditable(True)
        self.ed_cliente.setInsertPolicy(QComboBox.NoInsert)
        self.ed_cliente.lineEdit().setPlaceholderText("Elige de la lista o escríbela")
        self.ed_cliente.currentTextChanged.connect(lambda _t: self._cargar_usuarios())
        self.ed_contacto = campo("Usuario", QComboBox())
        self.ed_contacto.setEditable(True)
        self.ed_contacto.setInsertPolicy(QComboBox.NoInsert)
        self.ed_contacto.lineEdit().setPlaceholderText("Elige de la lista o escríbelo")
        self.ed_contacto.activated.connect(lambda _i: self._datos_de_usuario())
        self.ed_correo = campo("Correo", QLineEdit())
        self.ed_correo.setPlaceholderText("Opcional")
        self.ed_telefono = campo("Teléfono", QLineEdit())
        self.ed_telefono.setPlaceholderText("Opcional")
        self.ed_nombre = campo("Equipo", QLineEdit())
        self.ed_nombre.setPlaceholderText("Nombre o puesto")
        self.ed_metodo = campo("Método preferido", piezas.combo([n for _k, n, _d in ar.METODOS], [k for k, _n, _d in ar.METODOS]))
        self.ed_rustdesk = campo("ID de RustDesk", QLineEdit())
        self.ed_rustdesk.setPlaceholderText("123 456 789")
        self.chk_servidor = QCheckBox("Por mi servidor propio")
        self.chk_servidor.setToolTip("El cliente usa el programa generado en «🛰️ Servidor propio»")
        lay.addWidget(self.chk_servidor)
        self.ed_direccion = campo("Dirección", QLineEdit())
        self.ed_direccion.setPlaceholderText("Nombre o IP[:puerto]")
        self.ed_usuario = campo("Cuenta de Windows", QLineEdit())
        self.ed_usuario.setPlaceholderText("Opcional (Escritorio remoto)")
        self.ed_clave = campo("Contraseña", QLineEdit())
        self.ed_clave.setEchoMode(QLineEdit.Password)
        self.chk_quitar_clave = QCheckBox("Quitar la contraseña guardada")
        lay.addWidget(self.chk_quitar_clave)
        self.ed_notas = campo("Notas", QPlainTextEdit())
        self.ed_notas.setFixedHeight(70)
        lay.addStretch(1)
        self.btn_guardar = piezas.boton("💾 Guardar", "primary", self.guardar)
        self.btn_cancelar = piezas.boton("Cancelar", "ghost", self.cancelar_edicion)
        lay.addLayout(piezas.fila(None, self.btn_cancelar, self.btn_guardar, estirar=False))
        return _scroll(cont, "arEditor")

    def nuevo_equipo(self, rustdesk_id: str = ""):
        if self.panel.currentWidget() is self.pag_editor and not self._descartar_edicion():
            return
        e = ar.Equipo(self.db.nuevo_id(), self._cliente_actual(), "", rustdesk_id=rustdesk_id)
        try:
            e.servidor_propio = bool(rustdesk_id) or self.db.servidor().configurado    # lo normal en los nuevos
        except Exception:
            pass
        self._abrir_editor(e, "Nuevo equipo")
        if rustdesk_id:
            self.ed_cliente.setFocus()

    # Empresas y usuarios: el catálogo (cl.ClientesDB) más lo que ya hay en la agenda
    def _cargar_empresas(self, actual: str):
        try:
            catalogo = [x.nombre for x in self.clientes.empresas()]
        except Exception:
            catalogo = []
        nombres = {n.lower(): n for n in [x.cliente for x in self._equipos] + catalogo if n}
        self.ed_cliente.blockSignals(True)
        self.ed_cliente.clear()
        self.ed_cliente.addItems(sorted(nombres.values(), key=str.lower))
        self.ed_cliente.setCurrentText(actual)
        self.ed_cliente.blockSignals(False)

    def _cargar_usuarios(self, actual: Optional[str] = None):
        empresa = cl.limpiar(self.ed_cliente.currentText())
        texto = self.ed_contacto.currentText() if actual is None else actual
        nombres = {}
        try:
            e = self.clientes.empresa(empresa) if empresa else None
            for u in (self.clientes.usuarios(e.id) if e else []):
                nombres[u.nombre.lower()] = u.nombre
        except Exception:
            pass
        for x in self._equipos:
            if x.contacto and x.cliente.lower() == empresa.lower():
                nombres.setdefault(x.contacto.lower(), x.contacto)
        self.ed_contacto.blockSignals(True)
        self.ed_contacto.clear()
        self.ed_contacto.addItems(sorted(nombres.values(), key=str.lower))
        self.ed_contacto.setCurrentText(texto)
        self.ed_contacto.blockSignals(False)

    def _datos_de_usuario(self):
        """Elegido de la lista: correo y teléfono del catálogo (o del último equipo con ese usuario)."""
        empresa, nombre = self.ed_cliente.currentText(), self.ed_contacto.currentText()
        correo = telefono = ""
        try:
            u = self.clientes.usuario(empresa, nombre)
        except Exception:
            u = None
        if u is not None:
            correo, telefono = u.correo, u.telefono
        else:
            x = next((x for x in self._equipos if x.cliente.lower() == cl.limpiar(empresa).lower()
                      and x.contacto.lower() == cl.limpiar(nombre).lower()), None)
            if x is not None:
                correo, telefono = x.correo, x.telefono
        self.ed_correo.setText(correo)
        self.ed_telefono.setText(telefono)

    def _sincronizar_catalogo(self, e: ar.Equipo) -> None:
        """Escrito a mano: pregunta si se añade (o actualiza) en la lista de empresas y usuarios. Nunca impide
        guardar el equipo: si se dice que no, el equipo guarda igual sus datos."""
        try:
            empresa = self.clientes.empresa(e.cliente)
            if empresa is None and self._confirmar(f"«{e.cliente}» no está en la lista de empresas. ¿Añadirla?"):
                empresa = self.clientes.guardar_empresa(e.cliente)
            if empresa is None:
                return
            e.cliente = empresa.nombre
            u = self.clientes.usuario(empresa.nombre, e.contacto)
            if u is None:
                if self._confirmar(f"«{e.contacto}» no está entre los usuarios de {empresa.nombre}. ¿Añadirlo con su "
                                   "correo y teléfono?"):
                    self.clientes.guardar_usuario(cl.Usuario(0, empresa.id, e.contacto, e.correo, e.telefono))
                return
            e.contacto = u.nombre
            cambios = (e.correo and e.correo != u.correo) or (e.telefono and e.telefono != u.telefono)
            if cambios and self._confirmar(f"El correo o el teléfono de {u.nombre} son distintos de los de la lista. "
                                           "¿Actualizar la lista?"):
                u.correo, u.telefono = e.correo or u.correo, e.telefono or u.telefono
                self.clientes.guardar_usuario(u)
        except Exception as ex:
            self._aviso(f"No se pudo actualizar la lista de empresas: {ex}", QMessageBox.Warning)

    def editar(self, equipo_id: str):
        e = next((x for x in self._equipos if x.id == equipo_id), None)
        if e is not None:
            self._actual = e.id
            self._abrir_editor(ar.Equipo(**vars(e)), "Editar equipo")

    def _abrir_editor(self, e: ar.Equipo, titulo: str):
        self._editando = e
        self.lbl_editor.setText(titulo)
        self.lbl_editor.setStyleSheet(f"font-size: 13pt; font-weight: 600; color: {self._c['title']}; background: transparent; border: none;")
        self._cargar_empresas(e.cliente)
        self._cargar_usuarios(e.contacto)
        self.ed_correo.setText(e.correo)
        self.ed_telefono.setText(e.telefono)
        self.ed_nombre.setText(e.nombre)
        self.ed_metodo.setCurrentIndex(max(self.ed_metodo.findData(e.metodo), 0))
        self.ed_rustdesk.setText(e.rustdesk_id)
        self.chk_servidor.setChecked(e.servidor_propio)
        self.ed_direccion.setText(e.direccion)
        self.ed_usuario.setText(e.usuario)
        self.ed_clave.clear()
        self.ed_clave.setPlaceholderText("Guardada: vacía = sin cambios" if e.con_clave else "Opcional")
        self.chk_quitar_clave.setChecked(False)
        self.chk_quitar_clave.setVisible(e.con_clave)
        self.ed_notas.setPlainText(e.notas)
        self.panel.setCurrentWidget(self.pag_editor)
        self.pag_editor.verticalScrollBar().setValue(0)
        (self.ed_nombre if e.cliente else self.ed_cliente).setFocus()
        self.btn_conectar.setEnabled(False)

    def _leer_editor(self) -> ar.Equipo:
        e = self._editando
        return ar.Equipo(e.id, cl.limpiar(self.ed_cliente.currentText()), self.ed_nombre.text().strip(),
                         self.ed_metodo.currentData() or ar.RUSTDESK, self.ed_rustdesk.text().strip(),
                         self.ed_direccion.text().strip(), self.ed_usuario.text().strip(),
                         self.ed_notas.toPlainText().strip(), e.con_clave, e.ultima, self.chk_servidor.isChecked(),
                         contacto=cl.limpiar(self.ed_contacto.currentText()), correo=self.ed_correo.text().strip(),
                         telefono=cl.limpiar(self.ed_telefono.text()))

    def guardar(self):
        if self._editando is None:
            return
        e = self._leer_editor()
        errores = ar.errores_equipo(e)
        if errores:
            self._aviso("\n".join(errores), QMessageBox.Warning)
            return
        self._sincronizar_catalogo(e)
        try:
            self.db.guardar(e, clave=self.ed_clave.text() or None,
                            quitar_clave=self._editando.con_clave and self.chk_quitar_clave.isChecked())
        except Exception as ex:
            self._aviso(f"No se pudo guardar: {ex}", QMessageBox.Warning)
            return
        self.ed_clave.clear()
        self._editando = None
        self._actual = e.id
        self.panel.setCurrentWidget(self.pag_ficha)
        self.recargar(seleccionar=e.id)
        self._mostrar_ficha()

    def _hay_cambios(self) -> bool:
        if self._editando is None:
            return False
        a, b = self._editando, self._leer_editor()
        return (a.cliente, a.nombre, a.metodo, a.rustdesk_id, a.direccion, a.usuario, a.notas, a.servidor_propio,
                a.contacto, a.correo, a.telefono) != \
               (b.cliente, b.nombre, b.metodo, b.rustdesk_id, b.direccion, b.usuario, b.notas, b.servidor_propio,
                b.contacto, b.correo, b.telefono) \
            or bool(self.ed_clave.text())

    def _descartar_edicion(self) -> bool:
        if self._hay_cambios() and not self._confirmar("¿Descartar los cambios del equipo?"):
            return False
        self._editando = None
        self.ed_clave.clear()
        return True

    def cancelar_edicion(self):
        if not self._descartar_edicion():
            return
        if self._equipo_actual() is not None:
            self._mostrar_ficha()
        else:
            self._mostrar_vacio()
        self._recuento()

    def eliminar(self, equipo_id: str):
        e = next((x for x in self._equipos if x.id == equipo_id), None)
        if e is None or not self._confirmar(f"¿Quitar «{e.nombre}» ({e.cliente}) de la agenda? El historial se conserva."):
            return
        try:
            self.db.eliminar(equipo_id)
        except Exception as ex:
            self._aviso(f"No se pudo quitar: {ex}", QMessageBox.Warning)
            return
        if self._editando is not None and self._editando.id == equipo_id:
            self._editando = None
        self._actual = None
        self.recargar()

    # ── Historial e informes del agente ─────────────────────────────────
    def ver_historial(self):
        HistorialAccesoDialog(self, self.db).exec_()

    def ver_informes_agente(self):
        from msys.ui.informes_agente_dialog import InformesAgenteDialog
        InformesAgenteDialog(self, clientes_agenda=lambda: [(e.cliente, e.nombre) for e in self._equipos]).exec_()

    # ── Correo (5.1.0) ──────────────────────────────────────────────────
    def _correo_db(self):
        from msys.domain import envio_correo as ec
        return ec.CorreoDB(self.db.path)

    def abrir_correo(self):
        from msys.ui.correo_envio_dialog import CorreoDialog
        CorreoDialog(self, self._correo_db()).exec_()

    def _codigo_agente(self) -> str:
        from msys.domain import agente as ag
        return ag.codigo_tecnico(ag.AgenteDB(self.db.path).publica_activa())

    def redactar(self, e: Optional[ar.Equipo] = None, plantilla: str = ""):
        """Correo al usuario del equipo (o a quien se elija) con una plantilla."""
        from msys.ui.correo_envio_dialog import RedactarDialog
        datos = {}
        if e is not None:
            datos = dict(empresa=e.cliente, usuario=e.contacto, correo=e.correo, telefono=e.telefono, equipo=e.nombre)
        if not plantilla:
            plantilla = "Programa de RustDesk" if e is not None and e.servidor_propio else "En blanco"
        try:
            dlg = RedactarDialog(self, self._correo_db(), self.clientes, datos, plantilla, codigo=self._codigo_agente)
        except Exception as ex:
            self._aviso(f"No se pudo preparar el correo: {ex}", QMessageBox.Warning)
            return
        dlg.exec_()
        if dlg.enviado:
            self._estado("Correo enviado")

    def copiar_para_agente(self):
        from msys.domain import agente as ag
        try:
            codigo = ag.codigo_tecnico(ag.AgenteDB(self.db.path).publica_activa())
        except Exception as e:
            self._aviso(f"No se pudo preparar el código del técnico: {e}", QMessageBox.Warning)
            return
        QGuiApplication.clipboard().setText(ag.texto_para_cliente(codigo))
        self._estado("Instrucciones del agente copiadas (con tu código)")

    # ── Mensajes y cierre ───────────────────────────────────────────────
    def _confirmar(self, texto: str) -> bool:
        caja = _caja(self, "Acceso remoto", texto, QMessageBox.Question)
        si = caja.addButton("Sí", QMessageBox.YesRole)
        caja.addButton("No", QMessageBox.NoRole)
        caja.exec_()
        return caja.clickedButton() is si

    def _aviso(self, texto: str, icono=QMessageBox.Information):
        _caja(self, "Acceso remoto", texto, icono).exec_()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and self.panel.currentWidget() is self.pag_editor:
            self.cancelar_edicion()
            return
        super().keyPressEvent(e)

    def reject(self):
        if self.panel.currentWidget() is self.pag_editor:
            if not self._descartar_edicion():
                return
            self.cancelar_edicion()
        super().reject()


class ServidorPropioDialog(QDialog):
    """«🛰️ Servidor propio» (4.11.0): dirección y clave pública del servidor de RustDesk del técnico (hbbs y
    hbbr), comprobación de sus puertos y el programa para el cliente: el RustDesk oficial descargado con el
    servidor dentro del nombre. Nada de esto cambia el RustDesk del técnico (conecta con «ID@servidor?key=»)."""

    def __init__(self, padre, db: ar.AccesoDB, local=None):
        super().__init__(padre)
        self.db = db
        self.local = local if local is not None else sr.ServidorLocal()
        self._tarea = None
        self._tarea_local = None
        self._tarea_estado = None                        # aparte: se lanza al acabar una acción
        self._comprobar = ar.comprobar_servidor          # las pruebas lo sustituyen: sin red
        self._abrir_url = QDesktopServices.openUrl       # ídem: sin abrir el navegador ni el Explorador
        self._auditoria = getattr(getattr(padre, "host", None), "repair_audit_db", None)
        self.generado = ""
        self.anadir_id = ""                              # «＋ A la agenda»: lo recoge la ventana de Acceso remoto
        self._estado: Optional[sr.Estado] = None
        self.setWindowTitle("🛰️ Servidor propio de RustDesk")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(720)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)
        s = db.servidor()
        self.pestanas = piezas.pestanas()
        lay.addWidget(self.pestanas, 1)

        # ── En este PC (4.12.0) ──
        pc = QWidget()
        lp = QVBoxLayout(pc)
        lp.setSpacing(8)
        lp.addWidget(piezas.nota("El servidor de RustDesk de MASys (hbbs y hbbr, compilados desde el código oficial) "
                                 "funcionando en este PC. Su clave y los equipos dados de alta se guardan en "
                                 "SERVIDOR_RUSTDESK\\datos."))
        self.lbl_estado = QLabel("Comprobando…")
        self.btn_arrancar = piezas.boton("▶ Arrancar", "primary", self.arrancar_local)
        self.btn_parar = piezas.boton("■ Parar", "ghost", self.parar_local)
        self.btn_refrescar = piezas.boton("🔄 Actualizar", "ghost", self.refrescar_local)
        lp.addLayout(piezas.fila(self.lbl_estado, None, self.btn_arrancar, self.btn_parar, self.btn_refrescar,
                                 estirar=False))
        self.chk_windows = QCheckBox("Arrancar con Windows, aunque nadie inicie sesión")
        self.chk_windows.setToolTip("Tareas programadas «MASys servidor RustDesk» que se ejecutan como SYSTEM")
        self.chk_windows.clicked.connect(self.cambiar_arranque_windows)
        lp.addWidget(self.chk_windows)
        self.lbl_firewall = QLabel("")
        self.btn_firewall = piezas.boton("🛡️ Permitir en el firewall", "ghost", self.cambiar_firewall,
                                         "Reglas para hbbs y hbbr, solo redes privadas y de dominio")
        self.btn_usar = piezas.boton("🔑 Usar este servidor", "ghost", self.usar_local,
                                     "Pone su clave en «Conexión» (y tu DNS dinámico si la dirección está vacía)")
        lp.addLayout(piezas.fila(self.lbl_firewall, None, self.btn_firewall, self.btn_usar, estirar=False))
        lp.addWidget(QLabel("Equipos dados de alta en el servidor"))
        self.modelo_equipos = TablaSimple(["ID", "Alta", "IP", "En la agenda"])
        self.tabla_equipos, _px = piezas.tabla(self.modelo_equipos, self, columna_ancha=2, orden=(1, False))
        self.tabla_equipos.setMinimumHeight(110)
        self.tabla_equipos.doubleClicked.connect(lambda _i: self.anadir_a_agenda())
        lp.addWidget(self.tabla_equipos, 1)
        self.btn_agenda = piezas.boton("＋ A la agenda", "ghost", self.anadir_a_agenda,
                                       "Crear en la agenda el equipo elegido, por tu servidor")
        self.lbl_equipos = QLabel("")
        self.lbl_equipos.setObjectName("countLabel")
        lp.addLayout(piezas.fila(self.btn_agenda, None, self.lbl_equipos, estirar=False))
        self.pestanas.addTab(pc, "🖥️ En este PC")

        # ── Conexión ──
        cx = QWidget()
        lx = QVBoxLayout(cx)
        lx.setSpacing(10)
        lx.addWidget(piezas.nota("La dirección con la que los clientes llegan a tu servidor (en este PC o en otro "
                                 "equipo tuyo). En el router, redirige hacia ese equipo los puertos 21115 TCP, "
                                 "21116 TCP y UDP y 21117 TCP."))
        self.host = QLineEdit(s.host or db.direccion_ddns())
        self.host.setPlaceholderText("Nombre de DNS dinámico o IP pública")
        self.host.setMinimumWidth(260)
        self.btn_comprobar = piezas.boton("🔎 Comprobar", "ghost", self.comprobar, "Mira si los puertos del servidor responden")
        lx.addLayout(piezas.fila(self._titulo("Dirección"), self.host, self.btn_comprobar, estirar=False))
        self.clave = QLineEdit(s.clave)
        self.clave.setPlaceholderText("Contenido de id_ed25519.pub")
        self.btn_leer = piezas.boton("📄 Leer id_ed25519.pub…", "ghost", self.leer_clave,
                                     "Para un servidor en otro equipo: el archivo está en la carpeta de hbbs.exe")
        lx.addLayout(piezas.fila(self._titulo("Clave"), self.clave, self.btn_leer, estirar=False))
        self.relay = QLineEdit(s.relay)
        self.relay.setPlaceholderText("Vacío: el mismo equipo (puerto 21117)")
        lx.addLayout(piezas.fila(self._titulo("Retransmisión"), self.relay, estirar=False))
        self.lbl_comprobacion = piezas.nota("")
        self.lbl_comprobacion.setVisible(False)
        lx.addWidget(self.lbl_comprobacion)
        for campo in (self.host, self.relay):
            campo.textChanged.connect(lambda _t: self.lbl_comprobacion.setVisible(False))
        lx.addStretch(1)
        self.btn_servidor = piezas.boton("🌐 Servidor oficial para otro equipo", "ghost",
                                         lambda: self._abrir_url(QUrl(ar.URL_SERVIDOR)),
                                         "rustdesk-server (página oficial), si el servidor no va en este PC")
        lx.addLayout(piezas.fila(self.btn_servidor))
        self.pestanas.addTab(cx, "🔗 Conexión")

        # ── Programa para el cliente ──
        pr = QWidget()
        lc = QVBoxLayout(pr)
        lc.setSpacing(10)
        lc.addWidget(piezas.nota("El RustDesk oficial que se descarga (un solo archivo) con tu servidor dentro del "
                                 "nombre. El cliente lo abre sin instalar nada y su ID queda en tu servidor."))
        self.origen = QLineEdit(db.ajuste("origen_cliente"))
        self.origen.setPlaceholderText("rustdesk-1.x.y-x86_64.exe descargado")
        self.btn_origen = piezas.boton("📁 Elegir…", "ghost", self.elegir_origen)
        self.btn_descargar = piezas.boton("🌐 Descargar", "ghost", lambda: self._abrir_url(QUrl(ar.URL_RUSTDESK)),
                                          "Página oficial de descarga de RustDesk")
        lc.addLayout(piezas.fila(self._titulo("RustDesk"), self.origen, self.btn_origen, self.btn_descargar,
                                 estirar=False))
        self.btn_generar = piezas.boton("📦 Generar programa…", "primary", self.generar,
                                        "Crea la copia con tu servidor en el nombre en la carpeta que elijas")
        self.btn_carpeta = piezas.boton("📂 Abrir carpeta", "ghost", self.abrir_carpeta)
        self.btn_instr = piezas.boton("📋 Copiar instrucciones", "ghost", self.copiar_instrucciones,
                                      "Texto para el cliente")
        for b in (self.btn_carpeta, self.btn_instr):
            b.setEnabled(False)
        self.btn_correo_programa = piezas.boton("✉️ Enviar por correo…", "ghost", self.enviar_por_correo,
                                                "Plantilla «Programa de RustDesk» con el enlace de descarga")
        lc.addLayout(piezas.fila(self.btn_generar, self.btn_carpeta, self.btn_instr, self.btn_correo_programa))
        self.lbl_generado = piezas.nota("")
        self.lbl_generado.setVisible(False)
        lc.addWidget(self.lbl_generado)
        lc.addStretch(1)
        self.pestanas.addTab(pr, "📦 Programa para el cliente")

        self.btn_guardar = piezas.boton("💾 Guardar", "primary", self.guardar)
        self.btn_cerrar = piezas.boton("Cerrar", "ghost", self.reject)
        lay.addLayout(piezas.fila(None, self.btn_cerrar, self.btn_guardar, estirar=False))

        faltan = self.local.faltan()
        if faltan:
            self.lbl_estado.setText(f"Falta {', '.join(faltan)} en SERVIDOR_RUSTDESK\\bin")
            for w in (self.btn_arrancar, self.btn_parar, self.btn_refrescar, self.chk_windows, self.btn_firewall):
                w.setEnabled(False)
            self.btn_usar.setEnabled(bool(self.local.clave_publica()))
            self._pintar_equipos()
        else:
            QTimer.singleShot(0, self.refrescar_local)

    # ── En este PC ──────────────────────────────────────────────────────
    def _botones_local(self, activos: bool):
        for w in (self.btn_arrancar, self.btn_parar, self.btn_refrescar, self.chk_windows, self.btn_firewall):
            w.setEnabled(activos)

    def _en_segundo_plano(self, texto: str, funcion, *args, al_terminar=None):
        """Las órdenes del servidor (schtasks, netsh, esperar a que arranque) no congelan la ventana."""
        self._botones_local(False)
        self.lbl_estado.setText(texto)

        def listo(resultado):
            if al_terminar is not None:
                al_terminar(resultado)
            self.refrescar_local()

        def fallo(msg):
            self._aviso(msg, QMessageBox.Warning)
            self.refrescar_local()

        tareas.lanzar(self, "_tarea_local", funcion, *args, al_terminar=listo, al_fallar=fallo)

    def refrescar_local(self):
        if self.local.faltan():
            return
        self._botones_local(False)
        tareas.lanzar(self, "_tarea_estado", self.local.estado, al_terminar=self._pintar_local,
                      al_fallar=lambda msg: (self._botones_local(True), self.lbl_estado.setText(f"No se pudo leer: {msg}")))

    def _pintar_local(self, e: "sr.Estado"):
        self._estado = e
        self._botones_local(True)
        color = "#16a34a" if e.en_marcha else ("#ca8a04" if (e.hbbs or e.hbbr) else self._color_apagado())
        self.lbl_estado.setText(f"● {e.texto}")
        self.lbl_estado.setStyleSheet(f"color: {color}; font-weight: 600;")
        # Solo el que sirve: el estilo de MASys no distingue los botones desactivados
        self.btn_arrancar.setVisible(not e.en_marcha)
        self.btn_parar.setVisible(e.hbbs or e.hbbr)
        self.chk_windows.setChecked(e.tareas)
        self.lbl_firewall.setText("Firewall: permitido (redes privadas)" if e.firewall else "Firewall: sin reglas")
        self.btn_firewall.setText("🛡️ Quitar del firewall" if e.firewall else "🛡️ Permitir en el firewall")
        self.btn_usar.setEnabled(bool(self.local.clave_publica()))
        self._pintar_equipos()

    def _color_apagado(self) -> str:
        return "#8a94a6"

    def _pintar_equipos(self):
        try:
            equipos = self.local.equipos()
        except Exception as ex:
            equipos = []
            self.lbl_equipos.setText(f"No se pudieron leer: {ex}")
        en_agenda = set()
        try:
            en_agenda = {re.sub(r"\s+", "", e.rustdesk_id) for e in self.db.listar() if e.rustdesk_id}
        except Exception:
            pass
        self.modelo_equipos.set_datos([[q.id, q.alta, q.ip, "Sí" if q.id in en_agenda else ""] for q in equipos],
                                      equipos)
        if equipos or not self.lbl_equipos.text().startswith("No se pudieron"):
            self.lbl_equipos.setText("1 equipo" if len(equipos) == 1 else f"{len(equipos)} equipos")
        self.btn_agenda.setEnabled(bool(equipos))

    def arrancar_local(self):
        self._en_segundo_plano("Arrancando…", self.local.arrancar, al_terminar=lambda _r: self._clave_si_vacia())

    def parar_local(self):
        if not self._confirmar("¿Parar el servidor? Los clientes no podrán conectarse hasta que vuelva a arrancar."):
            return
        self._en_segundo_plano("Parando…", self.local.parar)

    def _clave_si_vacia(self):
        """Primera vez: hbbs acaba de crear la clave; si Conexión no tiene ninguna, se pone la de este PC."""
        if not self.clave.text().strip():
            self.usar_local()

    def usar_local(self):
        clave = self.local.clave_publica()
        if not clave:
            self._aviso("Este servidor aún no tiene clave: arráncalo una vez y la crea.")
            return
        self.clave.setText(clave)
        self.relay.clear()
        if not self.host.text().strip():
            self.host.setText(self.db.direccion_ddns())
        self.pestanas.setCurrentIndex(1)

    def _registrar(self, accion: str, objetivo: str, ok: bool, detalle: str):
        if self._auditoria is not None:
            try:
                self._auditoria.registrar(seccion="🖥️ Acceso remoto", accion=accion, objetivo=objetivo, exito=ok,
                                          detalle=detalle or "OK")
            except Exception:
                pass

    def cambiar_arranque_windows(self, marcado: bool):
        if marcado:
            pregunta = ("Se crearán dos tareas programadas («MASys servidor RustDesk») que arrancan hbbs y hbbr con "
                        "Windows como SYSTEM, sin ventana y aunque nadie inicie sesión. ¿Crearlas?")
        else:
            pregunta = "¿Quitar las tareas programadas? El servidor dejará de arrancar con Windows."
        if not self._confirmar(pregunta):
            self.chk_windows.setChecked(not marcado)
            return
        funcion = self.local.crear_tareas if marcado else self.local.quitar_tareas

        def hecho(resultado):
            ok, msg = resultado
            self._registrar("Arrancar con Windows" if marcado else "No arrancar con Windows",
                            "Tareas MASys servidor RustDesk", ok, msg)
            if not ok:
                self._aviso(f"No se pudo: {msg}", QMessageBox.Warning)

        self._en_segundo_plano("Aplicando…", funcion, al_terminar=hecho)

    def cambiar_firewall(self):
        quitar = bool(self._estado and self._estado.firewall)
        pregunta = ("¿Quitar las reglas del firewall del servidor? Desde otros equipos no se podrá llegar a él."
                    if quitar else
                    "Se permitirá la entrada a hbbs (21115 y 21116 TCP, 21116 UDP) y hbbr (21117 TCP) solo en redes "
                    "privadas y de dominio: el router reenvía a este PC. ¿Crear las reglas?")
        if not self._confirmar(pregunta):
            return
        funcion = self.local.quitar_reglas if quitar else self.local.crear_reglas

        def hecho(resultado):
            ok, msg = resultado
            self._registrar("Quitar reglas de firewall" if quitar else "Reglas de firewall",
                            "hbbs/hbbr 21115-21117 privado", ok, msg)
            if not ok:
                self._aviso(f"No se pudo: {msg}", QMessageBox.Warning)

        self._en_segundo_plano("Aplicando…", funcion, al_terminar=hecho)

    def anadir_a_agenda(self):
        indices = self.tabla_equipos.selectionModel().selectedRows() if self.tabla_equipos.selectionModel() else []
        if not indices:
            self._aviso("Elige el equipo en la lista.")
            return
        fila = self.tabla_equipos.model().mapToSource(indices[0]).row()
        equipo = self.modelo_equipos.objeto(fila)
        if equipo is None:
            return
        self.anadir_id = equipo.id
        self.accept()

    @staticmethod
    def _titulo(texto: str) -> QLabel:
        lbl = QLabel(texto)
        lbl.setMinimumWidth(QLabel("Retransmisión").sizeHint().width() + 6)   # columna de títulos alineada
        return lbl

    def _servidor(self) -> Optional[ar.Servidor]:
        """Lo escrito, validado; None (y aviso) si algo está mal."""
        try:
            return ar.validar_servidor(self.host.text(), self.clave.text(), self.relay.text())
        except ValueError as e:
            self._aviso(str(e), QMessageBox.Warning)
            return None

    def leer_clave(self):
        inicio = os.path.dirname(self.db.ajuste("carpeta_servidor")) if self.db.ajuste("carpeta_servidor") \
            else os.path.expanduser("~")
        ruta = dialogo_abrir_archivo(self, "Clave pública del servidor", inicio,
                                     "Clave pública (id_ed25519.pub);;Todos los archivos (*)")
        if not ruta:
            return
        try:
            self.clave.setText(ar.leer_clave_archivo(ruta))
        except ValueError as e:
            self._aviso(str(e), QMessageBox.Warning)
            return
        self.db.fijar_ajuste("carpeta_servidor", ruta)

    def comprobar(self):
        s = self._servidor()
        if s is None:
            return
        self.btn_comprobar.setEnabled(False)
        self._resultado_texto("Comprobando…", None)

        def listo(filas):
            self.btn_comprobar.setEnabled(True)
            lineas = [f"{'✔' if bien else '✖'} {puerto} TCP · {uso}: {detalle}"
                      for _h, puerto, _proto, uso, bien, detalle in filas]
            lineas.append(f"El {ar.PUERTO_ID} UDP no se puede comprobar desde aquí. Desde tu propia red, algunos "
                          "routers no dejan llegar a tu IP pública: si falla, pruébalo desde fuera.")
            self._resultado_texto("\n".join(lineas), all(f[4] for f in filas))

        def fallo(msg):
            self.btn_comprobar.setEnabled(True)
            self._resultado_texto(f"No se pudo comprobar: {msg}", False)

        tareas.lanzar(self, "_tarea", self._comprobar, s, al_terminar=listo, al_fallar=fallo)

    def _resultado_texto(self, texto: str, bien: Optional[bool]):
        self.lbl_comprobacion.setText(texto)
        color = "" if bien is None else ("color: #16a34a;" if bien else "color: #ca8a04;")
        self.lbl_comprobacion.setStyleSheet(color + " font-style: normal;")
        self.lbl_comprobacion.setVisible(True)

    def elegir_origen(self):
        actual = self.origen.text().strip()
        inicio = os.path.dirname(actual) if actual else os.path.expanduser("~\\Downloads")
        ruta = dialogo_abrir_archivo(self, "RustDesk descargado", inicio, "Programas (*.exe)")
        if not ruta:
            return
        problema = ar.problema_origen_cliente(ruta)
        if problema:
            self._aviso(problema, QMessageBox.Warning)
            return
        self.origen.setText(os.path.normpath(ruta))
        self.db.fijar_ajuste("origen_cliente", os.path.normpath(ruta))

    def generar(self):
        s = self._servidor()
        if s is None:
            return
        origen = self.origen.text().strip()
        problema = ar.problema_origen_cliente(origen)
        if problema:
            self._aviso(f"RustDesk: {problema}", QMessageBox.Warning)
            return
        inicio = self.db.ajuste("carpeta_cliente") or os.path.join(os.path.expanduser("~"), "Desktop")
        carpeta = dialogo_elegir_carpeta(self, "Dónde guardar el programa para el cliente", inicio)
        if not carpeta:
            return
        try:
            try:
                ruta = ar.generar_cliente(origen, carpeta, s)
            except FileExistsError:
                if not self._confirmar("Ya hay un programa generado con este servidor en esa carpeta. ¿Sustituirlo?"):
                    return
                ruta = ar.generar_cliente(origen, carpeta, s, sobrescribir=True)
        except (ValueError, OSError) as e:
            self._aviso(f"No se pudo generar: {e}", QMessageBox.Warning)
            return
        self.db.fijar_servidor(s)                    # el programa lleva este servidor: queda guardado
        self.db.fijar_ajuste("origen_cliente", os.path.normpath(origen))
        self.db.fijar_ajuste("carpeta_cliente", os.path.normpath(carpeta))
        self.generado = ruta
        self.lbl_generado.setToolTip(ruta)
        self._generado_texto("✔ Programa creado. Envíaselo al cliente sin cambiarle el nombre.")
        for b in (self.btn_carpeta, self.btn_instr):
            b.setEnabled(True)

    def _generado_texto(self, texto: str):
        self.lbl_generado.setText(texto)
        self.lbl_generado.setStyleSheet("color: #16a34a; font-style: normal;")
        self.lbl_generado.setVisible(True)

    def abrir_carpeta(self):
        if self.generado:
            self._abrir_url(QUrl.fromLocalFile(os.path.dirname(self.generado)))

    def enviar_por_correo(self):
        redactar = getattr(self.parent(), "redactar", None)
        if redactar is None:
            self._aviso("Abre esta ventana desde Acceso remoto para enviar correos.")
            return
        redactar(None, "Programa de RustDesk")

    def copiar_instrucciones(self):
        QGuiApplication.clipboard().setText(ar.texto_para_cliente(ar.RUSTDESK, propio=True))
        self._generado_texto("✔ Instrucciones copiadas. Envíalas junto con el programa.")

    def guardar(self):
        if not (self.host.text().strip() or self.clave.text().strip() or self.relay.text().strip()):
            s = ar.Servidor()                         # todo vacío = sin servidor propio
        else:
            s = self._servidor()
            if s is None:
                return
        try:
            self.db.fijar_servidor(s)
        except Exception as e:
            self._aviso(f"No se pudo guardar: {e}", QMessageBox.Warning)
            return
        self.accept()

    def _confirmar(self, texto: str) -> bool:
        caja = _caja(self, "Servidor propio", texto, QMessageBox.Question)
        si = caja.addButton("Sí", QMessageBox.YesRole)
        caja.addButton("No", QMessageBox.NoRole)
        caja.exec_()
        return caja.clickedButton() is si

    def _aviso(self, texto: str, icono=QMessageBox.Information):
        _caja(self, "Servidor propio", texto, icono).exec_()


class HistorialAccesoDialog(QDialog):
    """Conexiones lanzadas desde Acceso remoto, con búsqueda y exportación."""

    def __init__(self, padre, db: ar.AccesoDB):
        super().__init__(padre)
        self.setWindowTitle("📋 Historial de acceso remoto")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        pantalla = padre.screen() if padre is not None and hasattr(padre, "screen") else QApplication.primaryScreen()
        zona = pantalla.availableGeometry() if pantalla else None
        self.resize(min(1100, zona.width() - 40) if zona else 1100, min(560, zona.height() - 60) if zona else 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        self.busqueda = QLineEdit()
        self.busqueda.setPlaceholderText("Buscar conexión")
        self.busqueda.setClearButtonEnabled(True)
        self.btn_csv = piezas.boton("📄 CSV", "ghost", self.exportar_csv)
        self.btn_html = piezas.boton("🌐 Informe HTML", "ghost", self.exportar_html)
        self.recuento = QLabel("")
        self.recuento.setObjectName("countLabel")
        barra = QHBoxLayout()
        barra.addWidget(self.busqueda, 1)
        barra.addWidget(self.btn_csv)
        barra.addWidget(self.btn_html)
        barra.addWidget(self.recuento)
        lay.addLayout(barra)
        self.modelo = TablaSimple(["Fecha", "Hora", "Cliente", "Equipo", "Método", "Usuario", "Resultado"],
                                  color=lambda f, c: (COLOR_OK if f[6] == "Iniciada" else COLOR_ERROR) if c == 6 else None)
        self.tabla, self.proxy = piezas.tabla(self.modelo, self, columna_ancha=6, busqueda=self.busqueda)
        lay.addWidget(self.tabla, 1)
        cerrar = piezas.boton("Cerrar", "ghost", self.accept)
        lay.addLayout(piezas.fila(None, cerrar, estirar=False))
        try:
            conexiones = db.historial()
        except Exception as e:
            conexiones = []
            _caja(self, "Historial", f"No se pudo leer el historial: {e}", QMessageBox.Warning).exec_()
        filas = [[*ar.fecha_hora(c.fecha), c.cliente, c.equipo, c.metodo, c.usuario, c.resultado] for c in conexiones]
        self.modelo.set_datos(filas)
        self.busqueda.textChanged.connect(lambda _t: self._recuento())
        self._recuento()

    def _recuento(self):
        n, t = self.proxy.rowCount(), self.modelo.rowCount()
        self.recuento.setText(f"{n} de {t}" if n != t else f"{t} conexiones" if t != 1 else "1 conexión")
        for b in (self.btn_csv, self.btn_html):
            b.setEnabled(n > 0)

    def _visibles(self) -> List[List[str]]:
        return [[self.proxy.index(r, c).data() or "" for c in range(self.proxy.columnCount())]
                for r in range(self.proxy.rowCount())]

    def exportar_csv(self):
        ruta = dialogo_guardar_archivo(self, "Guardar CSV", ruta_guardado_predeterminada("Acceso_remoto.csv"),
                                       "CSV (*.csv)")
        if not ruta:
            return
        try:
            with open(ruta, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f, delimiter=";")
                w.writerow(self.modelo.HEADERS)
                w.writerows(self._visibles())
        except OSError as e:
            _caja(self, "Historial", f"No se pudo guardar: {e}", QMessageBox.Warning).exec_()

    def exportar_html(self):
        ruta = dialogo_guardar_archivo(self, "Guardar informe", ruta_guardado_predeterminada("Acceso_remoto.html"),
                                       "HTML (*.html)")
        if not ruta:
            return
        try:
            doc = informe_html.documento("Historial de acceso remoto",
                                         [informe_html.Seccion("Conexiones", self.modelo.HEADERS, self._visibles(),
                                                               columnas_nivel=[6])])
            informe_html.guardar(ruta, doc)
            QDesktopServices.openUrl(QUrl.fromLocalFile(ruta))
        except OSError as e:
            _caja(self, "Historial", f"No se pudo guardar: {e}", QMessageBox.Warning).exec_()


class EquipoNuevoDialog(QDialog):
    """«🆕 Equipo nuevo en tu servidor» (5.0.0): un cliente acaba de abrir el programa generado y su ID se ha dado
    de alta en el servidor de este PC. Ventana NO modal: añadirlo a la agenda, conectar ya o dejarlo para luego."""

    def __init__(self, host, equipo: "sr.EquipoServidor", db: ar.AccesoDB):
        super().__init__(host)
        self.host, self.equipo, self.db = host, equipo, db
        self.setWindowTitle("🆕 Equipo nuevo en tu servidor")
        self.setWindowFlags((self.windowFlags() | Qt.WindowStaysOnTopHint) & ~Qt.WindowContextHelpButtonHint)
        self.setModal(False)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(8)
        lay.addWidget(piezas.nota("Un cliente ha abierto el programa que generaste y su equipo se ha dado de alta en "
                                  "tu servidor. Añádelo a la agenda con su empresa y usuario, o conéctate ya: él solo "
                                  "tendrá que pulsar «Aceptar»."))
        for titulo, valor in (("ID de RustDesk", equipo.id), ("Alta", equipo.alta), ("IP", equipo.ip or "—")):
            lbl = QLabel(f"<b>{titulo}:</b> {valor}")
            lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
            lay.addWidget(lbl)
        self.btn_agenda = piezas.boton("＋ Añadir a la agenda", "primary", self.anadir)
        self.btn_conectar = piezas.boton("🔌 Conectar ahora", "ghost", self.conectar,
                                         "RustDesk por tu servidor, sin contraseña: el cliente pulsa «Aceptar»")
        self.btn_luego = piezas.boton("Más tarde", "ghost", self.close)
        lay.addLayout(piezas.fila(self.btn_agenda, self.btn_conectar, None, self.btn_luego, estirar=False))

    def anadir(self):
        abrir = getattr(self.host, "abrir_acceso_remoto", None)
        if abrir is not None:
            abrir()
            dlg = getattr(self.host, "_dlg_acceso", None)
            if dlg is not None:
                dlg.nuevo_equipo(rustdesk_id=self.equipo.id)
        self.close()

    def conectar(self):
        try:
            exe = ar.buscar_rustdesk(self.db.ajuste("ruta_rustdesk"))
            if not exe:
                raise OSError("RustDesk no está instalado en este equipo")
            temporal = ar.Equipo("", "", "", rustdesk_id=self.equipo.id, servidor_propio=True)
            ar.lanzar(ar.orden_rustdesk(exe, temporal, "", self.db.servidor()))
        except (ValueError, OSError) as e:
            _caja(self, "Acceso remoto", f"No se pudo conectar: {e}", QMessageBox.Warning).exec_()


class AccesoRemotoMixin:
    # ── Aviso de equipo nuevo en el servidor de este PC (5.0.0) ──────────
    def iniciar_aviso_equipos_nuevos(self, intervalo_ms: int = 15000):
        """Lo arranca multisys.py al abrir MASys. Revisa la base del servidor (solo lectura) cada 15 s."""
        if getattr(self, "_timer_equipos_nuevos", None) is not None:
            return
        self._timer_equipos_nuevos = QTimer(self)
        self._timer_equipos_nuevos.timeout.connect(lambda: self.revisar_equipos_nuevos())
        self._timer_equipos_nuevos.start(intervalo_ms)
        self.revisar_equipos_nuevos()

    def revisar_equipos_nuevos(self, local=None, db=None) -> list:
        """Avisa (bandeja + ventanita) de los IDs del servidor que no están en la agenda ni se avisaron ya.
        Sin servidor o sin equipos no abre multisys.db. Nunca lanza excepciones (es un temporizador)."""
        try:
            local = local if local is not None else sr.ServidorLocal()
            if local.faltan():
                return []
            equipos = local.equipos()
            if not equipos:
                return []
            db = db if db is not None else ar.AccesoDB()
            avisados = set(json.loads(db.ajuste("servidor_avisados", "[]") or "[]"))
            nuevos = sr.equipos_sin_avisar(equipos, [e.rustdesk_id for e in db.listar()], avisados)
            if not nuevos:
                return []
            db.fijar_ajuste("servidor_avisados", json.dumps(sorted(avisados | {q.id for q in nuevos})))
        except Exception:
            return []
        for q in nuevos:
            getattr(self, "tray_notify", lambda *_a: None)("🆕 Equipo nuevo en tu servidor",
                                                           f"ID {q.id} · {q.ip or 'sin IP'}")
            dlg = EquipoNuevoDialog(self, q, db)
            dlg.show()
        return nuevos

    def abrir_acceso_remoto(self):
        """Botón «🖥️ Acceso remoto» bajo el árbol (multisys.py). Ventana única y NO modal."""
        dlg = getattr(self, "_dlg_acceso", None)
        try:
            if dlg is not None:
                dlg.show()
                if dlg.isMinimized():
                    dlg.showNormal()
                dlg.raise_()
                dlg.activateWindow()
                return
        except RuntimeError:
            pass
        self._dlg_acceso = AccesoRemotoDialog(self)
        self._dlg_acceso.show()

    def _tema_acceso_remoto(self):
        dlg = getattr(self, "_dlg_acceso", None)
        try:
            if dlg is not None:
                dlg.aplicar_tema()
        except RuntimeError:
            self._dlg_acceso = None
