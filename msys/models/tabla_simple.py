# -*- coding: utf-8 -*-
"""Modelo de tabla genérico para los apartados nuevos: cabeceras + filas de
texto, con un objeto asociado a cada fila (para recuperar el dato original al
seleccionar), color de texto opcional por celda e icono opcional en la
primera columna. Ordenable con _FechaAwareSortProxy (fechas DD/MM/YYYY).

Lo usan Iconos, Extensiones, Características, Herramientas de red,
Migración y Móvil — evita un modelo distinto por cada tabla nueva."""

from typing import Any, Callable, List, Optional

from PyQt5.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt5.QtGui import QColor

from msys.models.table_models import _FechaAwareSortProxy

COLOR_OK = "#16a34a"
COLOR_AVISO = "#ca8a04"
COLOR_ERROR = "#dc2626"
COLOR_INFO = "#2563eb"
COLOR_APAGADO = "#8a94a6"


class TablaSimple(QAbstractTableModel):
    def __init__(self, cabeceras: List[str], color: Optional[Callable[[Any, int], Optional[str]]] = None,
                 icono: Optional[Callable[[Any], Any]] = None, tooltip: Optional[Callable[[Any, int], Optional[str]]] = None):
        """color(objeto, columna) -> '#rrggbb' o None; icono(objeto) -> QIcon para la columna 0;
        tooltip(objeto, columna) -> texto o None."""
        super().__init__()
        self.HEADERS = list(cabeceras)
        self._filas: List[List[str]] = []
        self._objetos: List[Any] = []
        self._color, self._icono, self._tooltip = color, icono, tooltip

    def set_datos(self, filas: List[List[Any]], objetos: Optional[List[Any]] = None):
        self.beginResetModel()
        self._filas = [["" if v is None else str(v) for v in f] for f in filas]
        self._objetos = list(objetos) if objetos is not None else list(filas)
        self.endResetModel()

    def objeto(self, fila: int):
        return self._objetos[fila] if 0 <= fila < len(self._objetos) else None

    def objetos(self) -> List[Any]:
        return list(self._objetos)

    def filas(self) -> List[List[str]]:
        return [list(f) for f in self._filas]

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._filas)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.HEADERS)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        f, c = index.row(), index.column()
        if role in (Qt.DisplayRole, Qt.EditRole):
            fila = self._filas[f]
            return fila[c] if c < len(fila) else ""
        if role == Qt.ForegroundRole and self._color:
            col = self._color(self._objetos[f], c)
            return QColor(col) if col else None
        if role == Qt.DecorationRole and c == 0 and self._icono:
            return self._icono(self._objetos[f])
        if role == Qt.ToolTipRole:
            if self._tooltip:
                t = self._tooltip(self._objetos[f], c)
                if t:
                    return t
            fila = self._filas[f]
            return fila[c] if c < len(fila) and len(fila[c]) > 40 else None
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal and 0 <= section < len(self.HEADERS):
            return self.HEADERS[section]
        return None


def proxy_para(modelo: TablaSimple, padre) -> _FechaAwareSortProxy:
    p = _FechaAwareSortProxy(padre)
    p.setSourceModel(modelo)
    p.setFilterCaseSensitivity(Qt.CaseInsensitive)
    p.setFilterKeyColumn(-1)
    return p


def objeto_seleccionado(tabla, proxy, modelo: TablaSimple):
    sel = tabla.selectionModel()
    if not sel or not sel.hasSelection():
        return None
    filas = sel.selectedRows()
    if not filas:
        return None
    return modelo.objeto(proxy.mapToSource(filas[0]).row())


def objetos_seleccionados(tabla, proxy, modelo: TablaSimple) -> list:
    sel = tabla.selectionModel()
    if not sel or not sel.hasSelection():
        return []
    return [modelo.objeto(proxy.mapToSource(i).row()) for i in sel.selectedRows()]
