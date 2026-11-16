# -*- coding: utf-8 -*-
"""Ventana «📚 Base de conocimiento» (4.5.0): problemas habituales y qué
herramientas de MASys usar, en orden, con «➜ Ir a» la sección.

NO es una sección del árbol: se abre con el botón de abajo a la izquierda
(encima de «⚙️ Configuración y perfiles», ver multisys.py), como Copias de
Seguridad. Es una ventana NO modal y única: se puede dejar abierta mientras
se siguen los pasos en MASys.

- Izquierda: árbol plegable Tema › Problema + buscador; clic simple en un
  tema lo despliega; desplegar/plegar todo con los dos iconos de la cabecera.
- Derecha (panel lateral): la ficha, o su editor. Las fichas fijas no se
  editan: se duplican. Las propias se guardan en multisys.db.

Motor y datos en msys/domain/conocimiento.py."""

from typing import Dict, List, Optional

from PyQt5.QtCore import QPointF, Qt, QTimer
from PyQt5.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PyQt5.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QScrollArea, QSplitter, QStackedWidget, QTabWidget, QToolButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QApplication,
)

from msys.domain import comprobar as cp
from msys.domain import conocimiento as kb
from msys.ui import piezas
from msys.ui.comprobar_ficha import Comprobador

_ROL_FICHA = Qt.UserRole          # id de la ficha (hojas)
_ROL_TEMA = Qt.UserRole + 1       # clave del tema (nodos de tema)
_SIN_SECCION = "— Sin sección —"
_ACENTO = "#2563eb"


def _colores(host) -> Dict[str, str]:
    base = {"title": "#1a2634", "muted": "#8a94a6", "alerts_bg": "#f7f9fc", "alerts_border": "#e3e7ee",
            "alerts_text": "#2c3e50"}
    if host is not None and hasattr(host, "_dash_colors"):
        try:
            base.update(host._dash_colors())
        except Exception:
            pass
    return base


def _icono_doble(abajo: bool, color: str) -> QIcon:
    """Chevron doble (desplegar todo / plegar todo) dibujado a mano."""
    pm = QPixmap(16, 16)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(QPen(QColor(color), 1.6, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    for y in (4.0, 9.0):
        camino = QPainterPath()
        if abajo:
            camino.moveTo(QPointF(3.5, y))
            camino.lineTo(QPointF(8, y + 3.5))
            camino.lineTo(QPointF(12.5, y))
        else:
            camino.moveTo(QPointF(3.5, y + 3.5))
            camino.lineTo(QPointF(8, y))
            camino.lineTo(QPointF(12.5, y + 3.5))
        p.drawPath(camino)
    p.end()
    return QIcon(pm)


class _IconosCabecera:
    """Desplegar todo / plegar todo: dos iconos a la derecha del título de la
    columna, dentro de la misma cabecera (convención del usuario)."""

    def __init__(self, arbol: QTreeWidget):
        self.arbol = arbol
        cab = arbol.header()
        self.btn_desplegar = QToolButton(cab)
        self.btn_plegar = QToolButton(cab)
        for b, ayuda, f in ((self.btn_desplegar, "Desplegar todo", arbol.expandAll),
                            (self.btn_plegar, "Plegar todo", arbol.collapseAll)):
            b.setToolTip(ayuda)
            b.setAutoRaise(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedSize(22, 20)
            # La cabecera es oscura en los dos temas (delegates.py): iconos claros y sin fondo
            b.setStyleSheet("QToolButton { background: transparent; border: none; border-radius: 4px; }"
                            " QToolButton:hover { background: rgba(255, 255, 255, 40); }")
            b.clicked.connect(lambda _=False, f=f: f())
        cab.sectionResized.connect(lambda *_: self.recolocar())
        cab.geometriesChanged.connect(self.recolocar)
        self.pintar("#dfe6ee")

    def pintar(self, color: str):
        self.btn_desplegar.setIcon(_icono_doble(True, color))
        self.btn_plegar.setIcon(_icono_doble(False, color))

    def recolocar(self):
        cab = self.arbol.header()
        x = cab.sectionViewportPosition(0) + cab.sectionSize(0)
        y = max(0, (cab.height() - 20) // 2)
        self.btn_plegar.move(x - 26, y)
        self.btn_desplegar.move(x - 50, y)
        self.btn_desplegar.raise_()
        self.btn_plegar.raise_()


def _scroll(contenido: QWidget, nombre: str) -> QScrollArea:
    """Zona con desplazamiento sin el fondo claro que Qt pone en el tema oscuro."""
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setFrameShape(QFrame.NoFrame)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.viewport().setObjectName(f"{nombre}Vista")
    contenido.setObjectName(nombre)
    area.setStyleSheet(f"QScrollArea {{ background: transparent; border: none; }}"
                       f" QWidget#{nombre}Vista, QWidget#{nombre} {{ background: transparent; }}")
    area.setWidget(contenido)
    return area


def _etiqueta(texto: str, estilo: str = "", ajustar: bool = True) -> QLabel:
    lbl = QLabel(texto)
    lbl.setWordWrap(ajustar)
    lbl.setTextFormat(Qt.PlainText)
    if estilo:
        lbl.setStyleSheet(estilo + " background: transparent; border: none;")
    return lbl


def _tabs_de(widget: QWidget) -> List[QTabWidget]:
    """Las pestañas de una sección. Ojo: hay secciones que SON la propia
    QTabWidget (📂 Carpetas Compartidas) y findChildren no incluye al widget."""
    propias = [widget] if isinstance(widget, QTabWidget) else []
    return propias + widget.findChildren(QTabWidget)


def pestanas_de_seccion(widget: Optional[QWidget]) -> List[str]:
    """Títulos de las pestañas que tiene una sección (también las anidadas)."""
    if widget is None:
        return []
    vistos: List[str] = []
    for tabs in _tabs_de(widget):
        for i in range(tabs.count()):
            t = tabs.tabText(i)
            if t and t not in vistos:
                vistos.append(t)
    return vistos


def seleccionar_pestana(widget: QWidget, titulo: str) -> bool:
    """Deja visible la pestaña «titulo» de la sección (y sus pestañas padre)."""
    for tabs in _tabs_de(widget):
        for i in range(tabs.count()):
            if tabs.tabText(i) == titulo:
                tabs.setCurrentIndex(i)
                hijo, padre = tabs, tabs.parentWidget()
                while padre is not None and padre is not widget:
                    if isinstance(padre, QStackedWidget) and isinstance(padre.parentWidget(), QTabWidget):
                        externo = padre.parentWidget()
                        externo.setCurrentIndex(padre.indexOf(hijo))
                    hijo, padre = padre, padre.parentWidget()
                return True
    return False


class _FilaPaso(QFrame):
    """Fila del editor: texto del paso + a qué sección (y pestaña) lleva."""

    def __init__(self, dialogo: "BaseConocimientoDialog", paso: kb.Paso):
        super().__init__()
        self.dialogo, self.paso_id = dialogo, paso.id
        self.setObjectName("kbFilaPaso")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 4)
        lay.setSpacing(4)
        arriba = QHBoxLayout()
        arriba.setSpacing(6)
        self.numero = QLabel("1.")
        self.numero.setFixedWidth(24)
        arriba.addWidget(self.numero)
        self.texto = QLineEdit(paso.texto)
        self.texto.setPlaceholderText("Qué hacer")
        arriba.addWidget(self.texto, 1)
        self.btn_subir = self._mini("▲", "Subir", lambda: dialogo._mover_paso(self, -1))
        self.btn_bajar = self._mini("▼", "Bajar", lambda: dialogo._mover_paso(self, 1))
        self.btn_quitar = self._mini("✕", "Quitar paso", lambda: dialogo._quitar_paso(self))
        for b in (self.btn_subir, self.btn_bajar, self.btn_quitar):
            arriba.addWidget(b)
        lay.addLayout(arriba)
        abajo = QHBoxLayout()
        abajo.setSpacing(6)
        abajo.addSpacing(30)
        abajo.addWidget(QLabel("Ir a"))
        self.seccion = piezas.combo([_SIN_SECCION] + dialogo._secciones())
        self.pestana = piezas.combo([])
        abajo.addWidget(self.seccion)
        abajo.addWidget(self.pestana)
        abajo.addStretch()
        lay.addLayout(abajo)
        i = self.seccion.findText(paso.seccion) if paso.seccion else 0
        self.seccion.setCurrentIndex(max(i, 0))
        self._cargar_pestanas(paso.pestana)
        self.seccion.currentIndexChanged.connect(lambda _i: self._cargar_pestanas(""))

    @staticmethod
    def _mini(texto, ayuda, al_pulsar) -> QToolButton:
        b = QToolButton()
        b.setText(texto)
        b.setToolTip(ayuda)
        b.setAutoRaise(True)
        b.setFixedSize(26, 24)
        b.clicked.connect(lambda _=False: al_pulsar())
        return b

    def _cargar_pestanas(self, elegida: str):
        nombres = self.dialogo._pestanas(self.seccion.currentText())
        self.pestana.blockSignals(True)
        self.pestana.clear()
        self.pestana.addItem("— Pestaña —", "")
        for n in nombres:
            self.pestana.addItem(n, n)
        piezas.ajustar_combo(self.pestana)
        i = self.pestana.findData(elegida) if elegida else 0
        self.pestana.setCurrentIndex(max(i, 0))
        self.pestana.blockSignals(False)
        self._con_pestanas = bool(nombres)
        self.pestana.setVisible(self._con_pestanas)

    def leer(self) -> kb.Paso:
        seccion = self.seccion.currentText()
        return kb.Paso(self.paso_id, self.texto.text().strip(),
                       "" if seccion == _SIN_SECCION else seccion,
                       (self.pestana.currentData() or "") if self._con_pestanas else "")


class BaseConocimientoDialog(QDialog):
    def __init__(self, host=None, db: Optional[kb.ConocimientoDB] = None):
        super().__init__(host)
        self.host = host
        self.db = db if db is not None else kb.ConocimientoDB()
        self.setWindowTitle("📚 Base de conocimiento")
        self.setWindowFlags((self.windowFlags() | Qt.WindowMinMaxButtonsHint) & ~Qt.WindowContextHelpButtonHint)
        self.setModal(False)
        self.setMinimumSize(860, 520)
        self._fichas: Dict[str, kb.Ficha] = {}
        self._actual: Optional[str] = None
        self._editando: Optional[kb.Ficha] = None
        self._filas: List[_FilaPaso] = []
        self._comprobador: Optional[Comprobador] = None    # «🔎 Comprobar ahora»: una a la vez
        self._caja_comprobar: Optional[QFrame] = None

        lay = QVBoxLayout(self)
        lay.setSpacing(10)
        lay.setContentsMargins(18, 16, 18, 14)

        # Cabecera
        cab = QHBoxLayout()
        cab.setSpacing(12)
        icono = _etiqueta("📚", "font-size: 22pt;", ajustar=False)
        cab.addWidget(icono)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.lbl_titulo = _etiqueta("Base de conocimiento", ajustar=False)
        col.addWidget(self.lbl_titulo)
        self.lbl_sub = _etiqueta("Problemas habituales y qué herramienta de MASys usar", ajustar=False)
        col.addWidget(self.lbl_sub)
        cab.addLayout(col, 1)
        lay.addLayout(cab)

        # Barra: búsqueda · nueva ficha · hueco · recuento
        self.busqueda = QLineEdit()
        self.busqueda.setPlaceholderText("Buscar problema")
        self.busqueda.setClearButtonEnabled(True)
        self.busqueda.setMinimumWidth(240)
        self.busqueda.textChanged.connect(lambda _t: self._filtrar())
        self.btn_nueva = piezas.boton("＋ Nueva ficha", "primary", self.nueva_ficha,
                                      "Crear una ficha propia")
        self.lbl_recuento = QLabel("")
        self.lbl_recuento.setObjectName("countLabel")
        barra = QHBoxLayout()
        barra.setSpacing(8)
        barra.addWidget(self.busqueda, 2)
        barra.addWidget(self.btn_nueva)
        barra.addStretch(1)
        barra.addWidget(self.lbl_recuento)
        lay.addLayout(barra)

        # Árbol | panel
        self.arbol = QTreeWidget()
        self.arbol.setColumnCount(1)
        self.arbol.setHeaderLabels(["Problema"])
        self.arbol.setExpandsOnDoubleClick(False)
        self.arbol.setUniformRowHeights(True)
        self.arbol.setIndentation(16)
        self.arbol.itemClicked.connect(self._clic_arbol)
        self.arbol.currentItemChanged.connect(lambda act, _ant: self._item_actual(act))
        self._iconos_cab = _IconosCabecera(self.arbol)

        self.panel = QStackedWidget()
        self.pag_vacia = QWidget()
        lv = QVBoxLayout(self.pag_vacia)
        self.lbl_vacio = _etiqueta("Elige un problema de la lista", ajustar=False)
        self.lbl_vacio.setAlignment(Qt.AlignCenter)
        lv.addWidget(self.lbl_vacio)
        self.panel.addWidget(self.pag_vacia)

        self.contenido_ficha = QWidget()
        self.lay_ficha = QVBoxLayout(self.contenido_ficha)
        self.lay_ficha.setContentsMargins(4, 0, 10, 10)
        self.lay_ficha.setSpacing(10)
        self.pag_ficha = _scroll(self.contenido_ficha, "kbFicha")
        self.panel.addWidget(self.pag_ficha)

        self.pag_editor = self._crear_editor()
        self.panel.addWidget(self.pag_editor)

        div = QSplitter(Qt.Horizontal)
        div.addWidget(self.arbol)
        div.addWidget(self.panel)
        div.setStretchFactor(0, 0)
        div.setStretchFactor(1, 1)
        div.setCollapsible(0, False)
        div.setCollapsible(1, False)
        div.setSizes([330, 640])
        self.arbol.setMinimumWidth(280)
        lay.addWidget(div, 1)

        self.aplicar_tema()
        self.recargar()
        self._ajustar_a_pantalla()

    # ── Tema ────────────────────────────────────────────────────────────
    def aplicar_tema(self):
        c = _colores(self.host)
        self._c = c
        self.lbl_titulo.setStyleSheet(f"font-size: 13pt; font-weight: 600; color: {c['title']};"
                                      " background: transparent; border: none;")
        self.lbl_sub.setStyleSheet(f"color: {c['muted']}; font-size: 9.5pt; background: transparent; border: none;")
        self.lbl_vacio.setStyleSheet(f"color: {c['muted']}; font-size: 10.5pt; background: transparent;")
        if self.host is not None and hasattr(self.host, "_apply_titlebar_theme_to") and self.isVisible():
            self.host._apply_titlebar_theme_to(self)
        if self.panel.currentWidget() is self.pag_ficha and self._actual:
            self._mostrar_ficha(self._actual)

    def showEvent(self, e):
        super().showEvent(e)
        if self.host is not None and hasattr(self.host, "_apply_titlebar_theme_to"):
            self.host._apply_titlebar_theme_to(self)
        QTimer.singleShot(0, self._iconos_cab.recolocar)

    def _ajustar_a_pantalla(self):
        pantalla = (self.host.screen() if self.host is not None and hasattr(self.host, "screen") else None) \
            or QApplication.primaryScreen()
        zona = pantalla.availableGeometry() if pantalla else None
        ancho, alto = 1060, 720
        if zona is not None:
            ancho, alto = min(ancho, zona.width() - 40), min(alto, zona.height() - 60)
        self.resize(max(ancho, self.minimumWidth()), max(alto, self.minimumHeight()))

    # ── Datos y árbol ───────────────────────────────────────────────────
    def recargar(self, seleccionar: Optional[str] = None):
        try:
            propias = self.db.listar()
        except Exception as e:  # base de datos bloqueada o dañada: siguen las fijas
            propias = []
            self._aviso("Base de conocimiento", f"No se pudieron leer tus fichas: {e}", QMessageBox.Warning)
        self._fichas = {f.id: f for f in kb.todas_las_fichas(propias)}
        self._filtrar(seleccionar or self._actual)

    def _filtrar(self, seleccionar: Optional[str] = None):
        texto = self.busqueda.text().strip()
        self.arbol.blockSignals(True)
        self.arbol.clear()
        visibles = 0
        item_sel = None
        for clave, titulo in kb.TEMAS:
            fichas = [f for f in self._fichas.values() if f.tema == clave and kb.coincide(f, texto)]
            fichas.sort(key=lambda f: (f.propia, f.titulo.lower()))
            if not fichas:
                continue
            nodo = QTreeWidgetItem(self.arbol, [f"{titulo}  ({len(fichas)})"])
            nodo.setData(0, _ROL_TEMA, clave)
            f_nodo = nodo.font(0)
            f_nodo.setBold(True)
            nodo.setFont(0, f_nodo)
            for f in fichas:
                hijo = QTreeWidgetItem(nodo, [("✎ " if f.propia else "") + f.titulo])
                hijo.setData(0, _ROL_FICHA, f.id)
                hijo.setToolTip(0, f.titulo + (" · Ficha propia" if f.propia else ""))
                if f.id == seleccionar:
                    item_sel = hijo
            visibles += len(fichas)
            if texto:
                nodo.setExpanded(True)
        self.arbol.blockSignals(False)
        total = len(self._fichas)
        if texto:
            self.lbl_recuento.setText(f"{visibles} de {total}" if visibles else "Sin resultados")
        else:
            self.lbl_recuento.setText(f"{total} fichas")
        if item_sel is not None:
            item_sel.parent().setExpanded(True)
            self.arbol.setCurrentItem(item_sel)
            self.arbol.scrollToItem(item_sel)
            if self.panel.currentWidget() is not self.pag_editor:
                self._mostrar_ficha(item_sel.data(0, _ROL_FICHA))
        elif self.panel.currentWidget() is not self.pag_editor:
            self._actual = None
            self.lbl_vacio.setText("Ningún problema coincide" if texto and not visibles else "Elige un problema de la lista")
            self.panel.setCurrentWidget(self.pag_vacia)

    def _clic_arbol(self, item: QTreeWidgetItem, _col: int):
        if item.data(0, _ROL_FICHA) is None:
            item.setExpanded(not item.isExpanded())
        else:
            self._item_actual(item)

    def _item_actual(self, item: Optional[QTreeWidgetItem]):
        if item is None:
            return
        fid = item.data(0, _ROL_FICHA)
        if fid is None or fid == self._actual and self.panel.currentWidget() is self.pag_ficha:
            return
        if self.panel.currentWidget() is self.pag_editor and not self._descartar_edicion():
            return
        self._mostrar_ficha(fid)

    # ── Vista de una ficha ──────────────────────────────────────────────
    def _limpiar_ficha(self):
        self._caja_comprobar = None
        while self.lay_ficha.count():
            w = self.lay_ficha.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.deleteLater()

    def _mostrar_ficha(self, fid: str):
        f = self._fichas.get(fid)
        if f is None:
            self.panel.setCurrentWidget(self.pag_vacia)
            return
        self._actual = fid
        c = self._c
        self._limpiar_ficha()

        cab_w = QWidget()
        cab = QHBoxLayout(cab_w)
        cab.setContentsMargins(0, 0, 0, 0)
        cab.setSpacing(8)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(_etiqueta(f.titulo, f"font-size: 14pt; font-weight: 600; color: {c['title']};"))
        origen = "Ficha propia" if f.propia else "Ficha de MASys"
        col.addWidget(_etiqueta(f"{kb.TITULO_TEMA.get(f.tema, '')} · {origen}", f"color: {c['muted']};"))
        cab.addLayout(col, 1)
        if f.propia:
            cab.addWidget(piezas.boton("✏️ Editar", "ghost", lambda: self.editar(f.id)), 0, Qt.AlignTop)
        cab.addWidget(piezas.boton("⧉ Duplicar", "ghost", lambda: self.duplicar(f.id),
                                   "Crear una ficha propia a partir de esta"), 0, Qt.AlignTop)
        if f.propia:
            cab.addWidget(piezas.boton("✕ Eliminar", "ghost", lambda: self.eliminar(f.id)), 0, Qt.AlignTop)
        self.lay_ficha.addWidget(cab_w)
        if self._secciones_comprobables(f):
            self._caja_comprobar = QFrame()
            self._caja_comprobar.setObjectName("kbComprobar")
            self._caja_comprobar.setStyleSheet(
                f"QFrame#kbComprobar {{ background: {c['alerts_bg']}; border: 1px solid {_ACENTO};"
                " border-radius: 8px; }")
            QVBoxLayout(self._caja_comprobar).setContentsMargins(12, 10, 12, 10)
            self.lay_ficha.addWidget(self._caja_comprobar)
            self._pintar_comprobacion()

        for titulo, lista in (("Síntomas", f.sintomas), ("Causas habituales", f.causas)):
            if lista:
                self.lay_ficha.addWidget(_etiqueta(titulo, f"font-weight: 600; color: {c['title']};", ajustar=False))
                self.lay_ficha.addWidget(_etiqueta("\n".join(f"•  {x}" for x in lista), f"color: {c['alerts_text']};"))

        if f.pasos:
            self.lay_ficha.addWidget(_etiqueta("Pasos", f"font-weight: 600; color: {c['title']};", ajustar=False))
        for n, paso in enumerate(f.pasos, 1):
            self.lay_ficha.addWidget(self._tarjeta_paso(n, paso))
        self.lay_ficha.addStretch(1)
        self.panel.setCurrentWidget(self.pag_ficha)
        self.pag_ficha.verticalScrollBar().setValue(0)

    def _tarjeta_paso(self, n: int, paso: kb.Paso) -> QFrame:
        c = self._c
        marco = QFrame()
        marco.setObjectName("kbPaso")
        marco.setStyleSheet(f"QFrame#kbPaso {{ background: {c['alerts_bg']}; border: 1px solid {c['alerts_border']};"
                            " border-radius: 8px; }")
        fila = QHBoxLayout(marco)
        fila.setContentsMargins(10, 8, 10, 8)
        fila.setSpacing(10)
        num = QLabel(str(n))
        num.setAlignment(Qt.AlignCenter)
        num.setFixedSize(26, 26)
        num.setStyleSheet(f"background: {_ACENTO}; color: white; border-radius: 13px; font-weight: 600; border: none;")
        fila.addWidget(num, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(_etiqueta(paso.texto, f"color: {c['alerts_text']};"))
        if paso.seccion:
            destino = paso.seccion + (f" › {paso.pestana}" if paso.pestana else "")
            col.addWidget(_etiqueta(destino, f"color: {c['muted']}; font-size: 8.5pt;"))
        fila.addLayout(col, 1)
        if paso.seccion:
            ir = piezas.boton("➜ Ir a", "ghost", lambda: self.ir_a(paso), f"Abrir {paso.seccion}")
            fila.addWidget(ir, 0, Qt.AlignVCenter)
        return marco

    # ── Comprobar ahora (4.8.0) ─────────────────────────────────────────
    def _secciones_comprobables(self, f: kb.Ficha) -> List[str]:
        registro = getattr(self.host, "_registro_soporte_por_seccion", None)
        if registro is None:
            return []
        try:
            con_detector = [s for s, (_acciones, detector) in registro().items() if detector is not None]
        except Exception:
            return []
        return cp.secciones_de(f, con_detector)

    def comprobar(self, fid: str):
        f = self._fichas.get(fid)
        if f is None or (self._comprobador is not None and self._comprobador.activo):
            return
        secciones = self._secciones_comprobables(f)
        if not secciones:
            return
        self._comprobador = Comprobador(self.host, fid, secciones)
        self._comprobador.cambio.connect(self._pintar_comprobacion)
        self._comprobador.iniciar()
        self._pintar_comprobacion()

    def detener_comprobacion(self):
        if self._comprobador is not None:
            self._comprobador.cancelar()

    def _resolver(self, seccion: str, aviso: cp.Aviso):
        """La acción del aviso, la misma que en el menú «🛠️ Soporte» (con su «✅ Validar»)."""
        if aviso.resolver is None or self.host is None:
            return
        try:
            if hasattr(self.host, "_ejecutar_aviso_soporte"):
                self.host._ejecutar_aviso_soporte(aviso.clave, aviso.texto, seccion, aviso.resolver)
            else:
                aviso.resolver()
        except Exception as e:
            self._aviso("Comprobar ahora", f"No se pudo abrir el aviso: {e}", QMessageBox.Warning)

    def _pintar_comprobacion(self):
        caja = self._caja_comprobar
        f = self._fichas.get(self._actual or "")
        if caja is None or f is None:
            return
        try:
            lay = caja.layout()
        except RuntimeError:          # la ficha ya se repintó
            self._caja_comprobar = None
            return
        while lay.count():
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
            elif item.layout() is not None:
                sub_lay = item.layout()
                while sub_lay.count():
                    hijo = sub_lay.takeAt(0).widget()
                    if hijo is not None:
                        hijo.hide()
                        hijo.deleteLater()
        lay.setSpacing(6)
        c = self._c
        comp = self._comprobador
        propia = comp is not None and comp.ficha_id == f.id
        en_marcha = comp is not None and comp.activo

        cab = QHBoxLayout()
        cab.setSpacing(8)
        cab.addWidget(_etiqueta("🔎 Comprobar en este equipo", f"font-weight: 600; color: {c['title']};", ajustar=False))
        cab.addStretch(1)
        if propia and en_marcha:
            boton = piezas.boton("■ Detener", "ghost", self.detener_comprobacion)
        else:
            boton = piezas.boton("🔎 Comprobar de nuevo" if propia else "🔎 Comprobar ahora", "primary",
                                 lambda: self.comprobar(f.id),
                                 "Vuelve a leer esas secciones en este equipo y pasa los avisos de «🛠️ Soporte»")
            if en_marcha:
                boton.setEnabled(False)
                boton.setToolTip("Ya hay una comprobación en marcha en otra ficha")
        cab.addWidget(boton)
        lay.addLayout(cab)

        if not propia:
            nombres = " · ".join(self._secciones_comprobables(f))
            lay.addWidget(_etiqueta(f"Vuelve a leer y revisa: {nombres}. Solo lee; no cambia nada.",
                                    f"color: {c['muted']};"))
            return

        lay.addWidget(_etiqueta(comp.resumen(), f"color: {c['alerts_text']}; font-weight: 600;"))
        iconos = {cp.PENDIENTE: ("○", c["muted"]), cp.LEYENDO: ("⏳", _ACENTO), cp.BIEN: ("✔", "#16a34a"),
                  cp.AVISOS: ("⚠️", "#ca8a04"), cp.ERROR: ("✖", "#dc2626"), cp.CANCELADA: ("–", c["muted"])}
        textos = {cp.PENDIENTE: "En espera", cp.LEYENDO: "Leyendo…", cp.BIEN: "Sin avisos",
                  cp.CANCELADA: "Sin comprobar"}
        for r in comp.resultados:
            icono, color = iconos.get(r.estado, ("○", c["muted"]))
            if r.estado == cp.AVISOS:
                estado = f"{len(r.avisos)} aviso{'s' if len(r.avisos) != 1 else ''}"
            elif r.estado == cp.ERROR:
                estado = r.error
            else:
                estado = textos.get(r.estado, "")
            if r.validados:
                estado += f" ({r.validados} validado{'s' if r.validados != 1 else ''})"
            fila = QHBoxLayout()
            fila.setSpacing(8)
            ico = _etiqueta(icono, f"color: {color}; font-weight: 600;", ajustar=False)
            ico.setFixedWidth(22)
            fila.addWidget(ico)
            fila.addWidget(_etiqueta(r.seccion, f"color: {c['title']};", ajustar=False))
            fila.addWidget(_etiqueta(estado, f"color: {color if r.estado != cp.BIEN else c['muted']};"), 1)
            fila.addWidget(piezas.boton("➜ Ir a", "ghost", lambda _=False, s=r.seccion: self.ir_a(kb.Paso("", "", s)),
                                        f"Abrir {r.seccion}"))
            lay.addLayout(fila)
            for aviso in r.avisos:
                sub = QHBoxLayout()
                sub.setSpacing(8)
                sub.addSpacing(30)
                sub.addWidget(_etiqueta(aviso.texto, f"color: {c['alerts_text']};"), 1)
                if aviso.resolver is not None:
                    sub.addWidget(piezas.boton("Revisar…", "ghost",
                                               lambda _=False, s=r.seccion, a=aviso: self._resolver(s, a),
                                               "La misma opción del menú «🛠️ Soporte»: enseña el detalle y pide confirmación"))
                lay.addLayout(sub)

    # ── Ir a ────────────────────────────────────────────────────────────
    def ir_a(self, paso: kb.Paso):
        host = self.host
        if host is None:
            return
        try:
            if paso.seccion == kb.COPIAS:
                host.abrir_copias_seguridad()
                return
            if paso.seccion == kb.PUERTOS_MOVIL:
                host.abrir_puertos_movil()
                return
            if paso.seccion == kb.MOVILES:
                dlg = host.abrir_moviles()
                if paso.pestana and dlg is not None:
                    seleccionar_pestana(dlg.tabs, paso.pestana)
                return
            widget = getattr(host, "tab_widgets", {}).get(paso.seccion)
            if widget is None:
                self._aviso("Base de conocimiento", f"«{paso.seccion}» no está en esta versión de MASys.")
                return
            host.seleccionar_seccion(paso.seccion)
            if paso.pestana:
                seleccionar_pestana(widget, paso.pestana)
            if host.isMinimized():
                host.showNormal()
            host.raise_()
            host.activateWindow()
        except Exception as e:
            self._aviso("Base de conocimiento", f"No se pudo abrir «{paso.seccion}»: {e}", QMessageBox.Warning)

    def _secciones(self) -> List[str]:
        orden = list(getattr(self.host, "_section_order", []) or [])
        return orden + list(kb.VENTANAS)

    def _pestanas(self, seccion: str) -> List[str]:
        if seccion == kb.MOVILES:
            dlg = getattr(self.host, "_dlg_moviles", None)
            return pestanas_de_seccion(dlg.tabs) if dlg is not None else ["💾 Copias"]
        return pestanas_de_seccion(getattr(self.host, "tab_widgets", {}).get(seccion))

    # ── Editor ──────────────────────────────────────────────────────────
    def _crear_editor(self) -> QScrollArea:
        cont = QWidget()
        lay = QVBoxLayout(cont)
        lay.setContentsMargins(4, 0, 10, 10)
        lay.setSpacing(8)
        self.lbl_editor = _etiqueta("Nueva ficha", "font-size: 13pt; font-weight: 600;", ajustar=False)
        lay.addWidget(self.lbl_editor)

        self.ed_tema = piezas.combo([t for _k, t in kb.TEMAS], [k for k, _t in kb.TEMAS])
        lay.addLayout(piezas.fila(QLabel("Tema"), self.ed_tema))
        self.ed_titulo = QLineEdit()
        self.ed_titulo.setPlaceholderText("Problema")
        lay.addWidget(QLabel("Título"))
        lay.addWidget(self.ed_titulo)

        self.ed_sintomas = QPlainTextEdit()
        self.ed_sintomas.setPlaceholderText("Uno por línea")
        self.ed_sintomas.setFixedHeight(76)
        lay.addWidget(QLabel("Síntomas"))
        lay.addWidget(self.ed_sintomas)
        self.ed_causas = QPlainTextEdit()
        self.ed_causas.setPlaceholderText("Una por línea")
        self.ed_causas.setFixedHeight(76)
        lay.addWidget(QLabel("Causas habituales"))
        lay.addWidget(self.ed_causas)

        lay.addWidget(QLabel("Pasos"))
        self.lay_pasos = QVBoxLayout()
        self.lay_pasos.setSpacing(2)
        lay.addLayout(self.lay_pasos)
        self.btn_anadir_paso = piezas.boton("＋ Añadir paso", "ghost", self._anadir_paso)
        lay.addLayout(piezas.fila(self.btn_anadir_paso))
        lay.addStretch(1)

        self.btn_guardar = piezas.boton("💾 Guardar", "primary", self.guardar)
        self.btn_cancelar = piezas.boton("Cancelar", "ghost", self.cancelar_edicion)
        lay.addLayout(piezas.fila(None, self.btn_cancelar, self.btn_guardar, estirar=False))
        return _scroll(cont, "kbEditor")

    def nueva_ficha(self):
        if self.panel.currentWidget() is self.pag_editor and not self._descartar_edicion():
            return
        actual = self._fichas.get(self._actual) if self._actual else None
        self._abrir_editor(kb.nueva_ficha(actual.tema if actual else "red"), "Nueva ficha")

    def duplicar(self, fid: str):
        f = self._fichas.get(fid)
        if f is not None:
            copia = kb.duplicar(f)
            copia.titulo = f"{f.titulo} (copia)" if f.propia else f.titulo
            self._abrir_editor(copia, "Nueva ficha (copia)")

    def editar(self, fid: str):
        f = self._fichas.get(fid)
        if f is not None and f.propia:
            self._abrir_editor(kb.Ficha(f.id, f.tema, f.titulo, list(f.sintomas), list(f.causas),
                                        [kb.Paso(p.id, p.texto, p.seccion, p.pestana) for p in f.pasos],
                                        propia=True, origen=f.origen), "Editar ficha")

    def _abrir_editor(self, ficha: kb.Ficha, titulo: str):
        self._editando = ficha
        self.lbl_editor.setText(titulo)
        self.lbl_editor.setStyleSheet(f"font-size: 13pt; font-weight: 600; color: {self._c['title']};"
                                      " background: transparent; border: none;")
        i = self.ed_tema.findData(ficha.tema)
        self.ed_tema.setCurrentIndex(max(i, 0))
        self.ed_titulo.setText(ficha.titulo)
        self.ed_sintomas.setPlainText("\n".join(ficha.sintomas))
        self.ed_causas.setPlainText("\n".join(ficha.causas))
        for fila in self._filas:
            fila.hide()
            fila.deleteLater()
        self._filas = []
        for p in ficha.pasos:
            self._anadir_paso(p)
        if not ficha.pasos:
            self._anadir_paso()
        self.panel.setCurrentWidget(self.pag_editor)
        self.pag_editor.verticalScrollBar().setValue(0)
        self.ed_titulo.setFocus()

    def _anadir_paso(self, paso: Optional[kb.Paso] = None):
        if self._editando is None:
            return
        if paso is None:
            paso = kb.nuevo_paso(self._ficha_editada_parcial())
        fila = _FilaPaso(self, paso)
        self._filas.append(fila)
        self.lay_pasos.addWidget(fila)
        self._renumerar()
        if not paso.texto:
            fila.texto.setFocus()

    def _ficha_editada_parcial(self) -> kb.Ficha:
        f = self._editando
        return kb.Ficha(f.id, f.tema, f.titulo, pasos=[kb.Paso(fila.paso_id, "") for fila in self._filas])

    def _quitar_paso(self, fila: _FilaPaso):
        if fila in self._filas:
            self._filas.remove(fila)
            self.lay_pasos.removeWidget(fila)
            fila.hide()
            fila.deleteLater()
            self._renumerar()

    def _mover_paso(self, fila: _FilaPaso, delta: int):
        i = self._filas.index(fila)
        j = i + delta
        if 0 <= j < len(self._filas):
            self._filas[i], self._filas[j] = self._filas[j], self._filas[i]
            self.lay_pasos.removeWidget(fila)
            self.lay_pasos.insertWidget(j, fila)
            self._renumerar()

    def _renumerar(self):
        for n, fila in enumerate(self._filas, 1):
            fila.numero.setText(f"{n}.")
            fila.btn_subir.setEnabled(n > 1)
            fila.btn_bajar.setEnabled(n < len(self._filas))

    def _leer_editor(self) -> kb.Ficha:
        f = self._editando
        lineas = lambda t: [x.strip() for x in t.splitlines() if x.strip()]  # noqa: E731
        return kb.Ficha(f.id, self.ed_tema.currentData() or "red", self.ed_titulo.text().strip(),
                        lineas(self.ed_sintomas.toPlainText()), lineas(self.ed_causas.toPlainText()),
                        [p for p in (fila.leer() for fila in self._filas) if p.texto],
                        propia=True, origen=f.origen)

    def guardar(self):
        if self._editando is None:
            return
        ficha = self._leer_editor()
        if not ficha.titulo:
            self._aviso("Base de conocimiento", "Escribe el título del problema.")
            self.ed_titulo.setFocus()
            return
        try:
            self.db.guardar(ficha)
        except Exception as e:
            self._aviso("Base de conocimiento", f"No se pudo guardar: {e}", QMessageBox.Warning)
            return
        self._editando = None
        self.panel.setCurrentWidget(self.pag_ficha)
        self._actual = ficha.id
        self.recargar(seleccionar=ficha.id)
        self._mostrar_ficha(ficha.id)

    def _hay_cambios(self) -> bool:
        if self._editando is None:
            return False
        antes, ahora = self._editando, self._leer_editor()
        return (antes.titulo, antes.tema, antes.sintomas, antes.causas,
                [(p.texto, p.seccion, p.pestana) for p in antes.pasos if p.texto]) != \
               (ahora.titulo, ahora.tema, ahora.sintomas, ahora.causas,
                [(p.texto, p.seccion, p.pestana) for p in ahora.pasos])

    def _descartar_edicion(self) -> bool:
        if self._hay_cambios() and not self._confirmar("Base de conocimiento", "¿Descartar los cambios de la ficha?"):
            return False
        self._editando = None
        return True

    def cancelar_edicion(self):
        if not self._descartar_edicion():
            return
        if self._actual and self._actual in self._fichas:
            self._mostrar_ficha(self._actual)
        else:
            self.panel.setCurrentWidget(self.pag_vacia)

    def eliminar(self, fid: str):
        f = self._fichas.get(fid)
        if f is None or not f.propia:
            return
        if not self._confirmar("Eliminar ficha", f"¿Eliminar la ficha «{f.titulo}»?"):
            return
        try:
            self.db.eliminar(fid)
        except Exception as e:
            self._aviso("Base de conocimiento", f"No se pudo eliminar: {e}", QMessageBox.Warning)
            return
        self._actual = None
        self.recargar()

    # ── Mensajes y teclado ──────────────────────────────────────────────
    def _confirmar(self, titulo: str, texto: str) -> bool:
        caja = QMessageBox(self)
        caja.setWindowFlags(caja.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        caja.setWindowTitle(titulo)
        caja.setText(texto)
        caja.setIcon(QMessageBox.Question)
        si = caja.addButton("Sí", QMessageBox.YesRole)
        caja.addButton("No", QMessageBox.NoRole)
        caja.exec_()
        return caja.clickedButton() is si

    def _aviso(self, titulo: str, texto: str, icono=QMessageBox.Information):
        caja = QMessageBox(self)
        caja.setWindowFlags(caja.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        caja.setWindowTitle(titulo)
        caja.setText(texto)
        caja.setIcon(icono)
        caja.exec_()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape and self.panel.currentWidget() is self.pag_editor:
            self.cancelar_edicion()
            return
        super().keyPressEvent(e)

    def reject(self):
        # Cerrar la ventana (también la X, que en QDialog pasa por aquí): no perder una ficha a medias
        if self.panel.currentWidget() is self.pag_editor:
            if not self._descartar_edicion():
                return
            self.cancelar_edicion()
        super().reject()


class ConocimientoMixin:
    def abrir_base_conocimiento(self):
        """Botón «📚 Base de conocimiento» bajo el árbol (multisys.py). Ventana
        única y NO modal: se puede seguir usando MASys con ella abierta."""
        dlg = getattr(self, "_dlg_conocimiento", None)
        try:
            if dlg is not None:
                dlg.show()
                if dlg.isMinimized():
                    dlg.showNormal()
                dlg.raise_()
                dlg.activateWindow()
                return
        except RuntimeError:
            pass  # la ventana anterior ya se destruyó
        self._dlg_conocimiento = BaseConocimientoDialog(self)
        self._dlg_conocimiento.show()

    def _tema_base_conocimiento(self):
        """Tras cambiar claro/oscuro con la ventana abierta."""
        dlg = getattr(self, "_dlg_conocimiento", None)
        try:
            if dlg is not None:
                dlg.aplicar_tema()
        except RuntimeError:
            self._dlg_conocimiento = None
