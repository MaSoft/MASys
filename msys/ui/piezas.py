# -*- coding: utf-8 -*-
"""Piezas de interfaz compartidas por los apartados nuevos, con la misma
convención que el resto de MASys:

- Barra superior en UNA fila: «🖥️ equipo», búsqueda, botones de acción,
  hueco y «🔄 Actualizar» a la derecha (feedback del usuario, 22/07/2026).
- Tablas ordenables y con columnas ajustables (_setup_header).
- Sin papelera: quitar/eliminar va con «✕»."""

import socket
from typing import Callable, List, Optional, Tuple

from PyQt5.QtCore import QEvent, QObject, QRegExp, Qt, QTimer
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (QComboBox, QGroupBox, QTabBar, QTabWidget, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                             QPushButton, QTableView, QVBoxLayout, QWidget)

from msys.models.tabla_simple import TablaSimple, proxy_para
from msys.widgets.delegates import _setup_header


def boton(texto: str, rol: str = "ghost", al_pulsar: Optional[Callable] = None, ayuda: str = "") -> QPushButton:
    b = QPushButton(texto)
    if rol:
        b.setObjectName(rol)
    if al_pulsar:
        b.clicked.connect(lambda _=False: al_pulsar())
    if ayuda:
        b.setToolTip(ayuda)
    return b


def barra_superior(placeholder: Optional[str], botones: List[QPushButton], al_actualizar: Optional[Callable],
                   con_equipo: bool = True) -> Tuple[QHBoxLayout, Optional[QLineEdit], Optional[QPushButton], QLabel]:
    """Devuelve (fila, búsqueda, botón Actualizar, etiqueta de recuento)."""
    fila = QHBoxLayout()
    fila.setSpacing(8)
    if con_equipo:
        lbl = QLabel(f"🖥️ {socket.gethostname()}")
        lbl.setObjectName("hostnameLabel")
        fila.addWidget(lbl)
    busqueda = None
    if placeholder:
        busqueda = QLineEdit()
        busqueda.setPlaceholderText(placeholder)
        busqueda.setClearButtonEnabled(True)
        busqueda.setMinimumWidth(200)
        fila.addWidget(busqueda, 3)
    for b in botones:
        fila.addWidget(b)
    fila.addStretch()
    recuento = QLabel("")
    recuento.setObjectName("countLabel")
    fila.addWidget(recuento)
    actualizar = None
    if al_actualizar:
        actualizar = boton("🔄 Actualizar", "primary", al_actualizar)
        fila.addWidget(actualizar)
    return fila, busqueda, actualizar, recuento


def tabla(modelo: TablaSimple, padre, columna_ancha: int = 0, seleccion_multiple: bool = False,
          busqueda: Optional[QLineEdit] = None, orden: Tuple[int, bool] = (-1, True)):
    """QTableView + proxy ordenable; si se pasa la búsqueda, filtra en vivo.
    orden=(columna, ascendente); columna -1 = el orden en que llegan los datos."""
    proxy = proxy_para(modelo, padre)
    vista = QTableView()
    vista.setModel(proxy)
    vista.setAlternatingRowColors(True)
    vista.setSelectionBehavior(QTableView.SelectRows)
    vista.setSelectionMode(QTableView.ExtendedSelection if seleccion_multiple else QTableView.SingleSelection)
    vista.verticalHeader().setVisible(False)
    vista.setSortingEnabled(True)
    # Sin esto Qt arranca ordenando por la columna 0 de la Z a la A
    columna, ascendente = orden
    vista.sortByColumn(columna, Qt.AscendingOrder if ascendente else Qt.DescendingOrder)
    vista.setWordWrap(False)
    _setup_header(vista, len(modelo.HEADERS), stretch_col=columna_ancha)
    # _setup_header ajusta cada columna a su contenido: si el título es más largo que los datos
    # («Temperatura» sobre «31 ºC») quedaría cortado. Después de cada carga, ninguna más estrecha que su título.
    ajustar = lambda: _ancho_minimo_cabeceras(vista, modelo, columna_ancha)  # noqa: E731
    modelo.modelReset.connect(lambda: QTimer.singleShot(0, ajustar))
    # Una tabla en una pestaña oculta se mide con un ancho ficticio: se repite al enseñarla
    vista.installEventFilter(_AlMostrar(ajustar, vista))
    if busqueda is not None:
        busqueda.textChanged.connect(
            lambda t: proxy.setFilterRegExp(QRegExp(t, Qt.CaseInsensitive, QRegExp.FixedString)))
    return vista, proxy


class _BarraPestanas(QTabBar):
    """La hoja de estilos pone las pestañas en negrita (600) con 16 px de relleno
    y los títulos llevan un emoji (que Qt mide con otra fuente): el ancho que
    calcula Qt se queda corto y corta la última letra. Aquí se mide el texto
    con la fuente en negrita de verdad y se reserva el relleno completo."""

    def tabSizeHint(self, i):
        from PyQt5.QtGui import QFontMetrics
        tam = super().tabSizeHint(i)
        f = QFont(self.font())
        f.setWeight(QFont.DemiBold)
        necesario = QFontMetrics(f).horizontalAdvance(self.tabText(i)) + 2 * 16 + 18
        if tam.width() < necesario:
            tam.setWidth(necesario)
        return tam


def _ancho_minimo_cabeceras(vista, modelo, columna_ancha):
    """Ninguna columna más estrecha que su título. Con las señales bloqueadas: si no,
    _redistribuir_ancho_columnas (delegates.py) estrecharía la vecina para compensar.
    El ancho que falte o sobre lo absorbe la columna elástica, como en el resto de MASys."""
    from PyQt5.QtGui import QFontMetrics
    try:
        cab = vista.horizontalHeader()
        f = QFont(cab.font())
        f.setBold(True)
        fm = QFontMetrics(f)
        cab.blockSignals(True)
        try:
            for i, texto in enumerate(modelo.HEADERS):
                if i == columna_ancha or cab.isSectionHidden(i):
                    continue
                # Título entero y contenido entero (hasta 280 px; lo más largo se ve con el ratón encima)
                minimo = max(fm.horizontalAdvance(texto) + 28, min(vista.sizeHintForColumn(i) + 14, 280))
                if cab.sectionSize(i) < minimo:
                    cab.resizeSection(i, minimo)
            if 0 <= columna_ancha < len(modelo.HEADERS):
                otros = sum(cab.sectionSize(i) for i in range(len(modelo.HEADERS)) if i != columna_ancha and not cab.isSectionHidden(i))
                cab.resizeSection(columna_ancha, max(vista.viewport().width() - otros, 120))
        finally:
            cab.blockSignals(False)
    except RuntimeError:
        pass  # la tabla ya se destruyó


class _AlMostrar(QObject):
    def __init__(self, funcion, padre):
        super().__init__(padre)
        self._f = funcion

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Show:
            QTimer.singleShot(0, self._f)
        return False


def pestanas() -> QTabWidget:
    """QTabWidget cuyos títulos nunca se cortan (ver _BarraPestanas)."""
    t = QTabWidget()
    barra = _BarraPestanas()
    barra.setElideMode(Qt.ElideNone)
    barra.setExpanding(False)
    t.setTabBar(barra)
    return t


def combo(opciones=(), datos=None) -> QComboBox:
    """Desplegable que se ensancha a su texto más largo (nunca corta la opción)."""
    c = QComboBox()
    c.setSizeAdjustPolicy(QComboBox.AdjustToContents)
    for i, texto in enumerate(opciones):
        c.addItem(texto, datos[i] if datos else None)
    ajustar_combo(c)
    return c


def ajustar_combo(c: QComboBox):
    """Ancho mínimo = texto más largo + flecha + relleno de la hoja de estilos."""
    from PyQt5.QtGui import QFontMetrics
    fm = QFontMetrics(c.font())
    ancho = max((fm.horizontalAdvance(c.itemText(i)) for i in range(c.count())), default=40)
    c.setMinimumWidth(ancho + 56)


def nota(texto: str) -> QLabel:
    lbl = QLabel(texto)
    lbl.setObjectName("hintLabel")
    lbl.setWordWrap(True)
    return lbl


def grupo(titulo: str) -> Tuple[QGroupBox, QVBoxLayout]:
    g = QGroupBox(titulo)
    lay = QVBoxLayout(g)
    lay.setSpacing(8)
    return g, lay


def fila(*widgets, estirar: bool = True) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(8)
    for w in widgets:
        if w is None:
            lay.addStretch()
        elif isinstance(w, QWidget):
            lay.addWidget(w)
        else:
            lay.addLayout(w)
    if estirar and widgets and widgets[-1] is not None:
        lay.addStretch()
    return lay


def tamano_legible(n: int) -> str:
    for unidad in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unidad == "GB":
            return f"{n:,.0f} {unidad}".replace(",", ".") if unidad == "bytes" else f"{n:,.1f} {unidad}".replace(",", "X").replace(".", ",").replace("X", ".")
        n /= 1024
    return str(n)


def refrescar_export(host):
    """Tras cargar datos: que la barra inferior (CSV/HTML/PDF/Excel) se habilite."""
    f = getattr(host, "_actualizar_barra_exportacion", None)
    if f:
        f()


class CampoCodigo(QPlainTextEdit):
    """Código largo para copiar o pegar (MSA1/MSE1 del agente): se parte en varias líneas para verse
    entero, sin saltos propios. Tiene text()/setText() como un QLineEdit."""

    def __init__(self, lineas: int = 2, solo_lectura: bool = False, placeholder: str = ""):
        super().__init__()
        self.setFont(QFont("Consolas", 9))
        self.setReadOnly(solo_lectura)
        self.setPlaceholderText(placeholder)
        self.setTabChangesFocus(True)
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        alto = self.fontMetrics().lineSpacing() * lineas + 2 * self.frameWidth() + 14
        self.setFixedHeight(alto)

    def text(self) -> str:
        return " ".join(self.toPlainText().split())

    def setText(self, texto: str):
        self.setPlainText(texto or "")

    def insertFromMimeData(self, origen):
        # Al pegar, sin saltos de línea (el código se copia a veces partido de un correo)
        self.insertPlainText(" ".join((origen.text() or "").split()))


class PaginaPerezosa(QWidget):
    """Página de sección que carga sus datos la primera vez que se enseña (MASys
    crea todas las secciones al arrancar: así el arranque no se alarga)."""

    def __init__(self, al_mostrar):
        super().__init__()
        self._al_mostrar, self._mostrada = al_mostrar, False

    def showEvent(self, e):
        super().showEvent(e)
        if not self._mostrada:
            self._mostrada = True
            self._al_mostrar()
