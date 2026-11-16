# -*- coding: utf-8 -*-
"""Auditoría de textos recortados para los scripts de capturas (4.7.0 en adelante).
Comprueba: etiquetas y botones más estrechos que su texto, desplegables
estrechados, cabeceras cortadas, zonas con desplazamiento que cortan el
contenido, tablas/árboles con barra horizontal, celdas de árbol cortadas y
controles que se salen de la ventana. Uso: auditar(ventana, "nombre", problemas)."""
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont, QFontMetrics
from PyQt5.QtWidgets import (QAbstractButton, QAbstractItemView, QCheckBox, QComboBox, QHeaderView, QLabel,
                             QLineEdit, QPushButton, QScrollArea, QTreeWidget, QWidget)


def auditar(raiz, nombre, problemas):
    for w in raiz.findChildren((QLabel, QAbstractButton)):
        if not w.isVisible() or w.width() <= 0:
            continue
        texto = w.text().replace("&", "")
        if not texto or (isinstance(w, QLabel) and (w.wordWrap() or "\n" in texto or texto.startswith("<"))):
            continue
        fm = QFontMetrics(w.font())
        necesita = fm.horizontalAdvance(texto)
        hay = w.contentsRect().width()
        if isinstance(w, QPushButton):
            hay -= (w.iconSize().width() + 16 if not w.icon().isNull() else 0) + 12 + (16 if w.menu() else 0)
        if isinstance(w, QCheckBox):
            hay -= 24
        if necesita > hay + 1:
            problemas.append(f"{nombre}: '{texto[:60]}' necesita {necesita}px, tiene {hay}px ({type(w).__name__})")
    # Cuadros de texto con contenido que no se ve entero (códigos, rutas…)
    for e in raiz.findChildren(QLineEdit):
        if e.isVisible() and e.text():
            necesita = QFontMetrics(e.font()).horizontalAdvance(e.text()) + 16
            if necesita > e.width():
                problemas.append(f"{nombre}: cuadro de texto '{e.text()[:30]}…' no se ve entero ({necesita}px de {e.width()}px)")
    for c in raiz.findChildren(QComboBox):
        if c.isVisible() and c.width() < c.minimumSizeHint().width():
            problemas.append(f"{nombre}: desplegable '{c.currentText()[:40]}' estrechado ({c.width()}px)")
    for cab in raiz.findChildren(QHeaderView):
        if not cab.isVisible() or cab.orientation() != Qt.Horizontal or not cab.model():
            continue
        f = QFont(cab.font())
        f.setBold(True)
        fm = QFontMetrics(f)
        for i in range(cab.count()):
            if cab.isSectionHidden(i):
                continue
            texto = str(cab.model().headerData(i, Qt.Horizontal) or "")
            if texto and fm.horizontalAdvance(texto) + 10 > cab.sectionSize(i):
                problemas.append(f"{nombre}: cabecera '{texto}' cortada ({cab.sectionSize(i)}px)")
    for area in raiz.findChildren(QScrollArea):
        if area.isVisible() and area.widget() is not None:
            minimo = area.widget().minimumSizeHint().width()
            if minimo > area.viewport().width() + 1:
                problemas.append(f"{nombre}: contenido de {minimo}px en una zona de {area.viewport().width()}px")
    for w in raiz.findChildren(QWidget):
        if w.isVisible() and w.window() is raiz and isinstance(w, (QAbstractItemView, QPushButton, QLabel)):
            derecha = w.mapTo(raiz, w.rect().topRight()).x()
            if derecha > raiz.width() + 1:
                problemas.append(f"{nombre}: {type(w).__name__} se sale de la ventana ({derecha}px de {raiz.width()}px)")
    for vista in raiz.findChildren(QAbstractItemView):
        if vista.isVisible() and vista.horizontalScrollBar().isVisible():
            problemas.append(f"{nombre}: {type(vista).__name__} con barra horizontal (no cabe a lo ancho)")
    for arbol in raiz.findChildren(QTreeWidget):
        if not arbol.isVisible():
            continue
        ancho = arbol.viewport().width()
        pila = [arbol.topLevelItem(i) for i in range(arbol.topLevelItemCount())]
        while pila:
            item = pila.pop()
            pila += [item.child(j) for j in range(item.childCount())]
            if item.isHidden():
                continue
            for col in range(arbol.columnCount()):
                rect = arbol.visualRect(arbol.indexFromItem(item, col))
                if not rect.isValid() or not item.text(col):
                    continue
                extra = 30 if item.flags() & Qt.ItemIsUserCheckable and col == 0 else 10
                necesita = QFontMetrics(item.font(col)).horizontalAdvance(item.text(col)) + extra
                if necesita > rect.width() and rect.x() + rect.width() <= ancho:
                    problemas.append(f"{nombre}: celda cortada '{item.text(col)[:40]}' ({necesita}px de {rect.width()}px)")
