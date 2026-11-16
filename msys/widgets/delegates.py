# -*- coding: utf-8 -*-
"""Delegado de celda legible + helpers compartidos de cabecera/columnas
(ajuste de ancho al redimensionar ventana o al arrastrar una columna a
mano) usados por TODAS las tablas de MASys, junto con las hojas de
estilo QSS (claro/oscuro, general y del arbol de navegacion) que vivian
pegadas a estos helpers en el original.
Extraido de multisys.py (Fase 1 de modularizacion)."""

from PyQt5.QtCore import Qt, QEvent, QObject
from PyQt5.QtGui import QPalette, QColor
from PyQt5.QtWidgets import QHeaderView, QStyledItemDelegate

MIN_COL_WIDTH = 80

_readable_cell_delegate_instance = None

def _get_readable_cell_delegate():
    """Instancia única y compartida de ReadableCellTextDelegate (stateless,
    no necesita una por tabla)."""
    global _readable_cell_delegate_instance
    if _readable_cell_delegate_instance is None:
        _readable_cell_delegate_instance = ReadableCellTextDelegate()
    return _readable_cell_delegate_instance

def _redistribuir_ancho_columnas(header, col_count, logical_index, old_size, new_size):
    """Slot de QHeaderView.sectionResized — SOLO reacciona a arrastres
    manuales del usuario (las resoluciones programáticas se hacen con
    header.blockSignals(True), así que nunca reentran aquí).

    Al ampliar una columna a mano, el ancho ganado se resta de las
    columnas SIGUIENTES (a la derecha; a la izquierda si se amplía la
    última), en orden, hasta un mínimo (MIN_COL_WIDTH) por columna. Si
    todas las columnas disponibles ya están en su mínimo y aún sobra
    ancho por compensar, se recorta la propia columna que se estaba
    ampliando — así la suma total de anchos nunca cambia: ninguna columna
    queda empujada fuera de la vista (scroll horizontal) por la derecha.

    Al estrechar una columna a mano, el hueco liberado se lo lleva la
    columna inmediatamente siguiente (o la anterior, si es la última),
    para que la tabla nunca deje un hueco vacío al final."""
    delta = new_size - old_size
    if delta == 0 or logical_index >= col_count:
        return

    try:
        header.blockSignals(True)
        try:
            if logical_index < col_count - 1:
                objetivo = list(range(logical_index + 1, col_count))
            else:
                objetivo = list(range(logical_index - 1, -1, -1))

            if delta > 0:
                restante = delta
                for col in objetivo:
                    if restante <= 0:
                        break
                    actual = header.sectionSize(col)
                    disponible = actual - MIN_COL_WIDTH
                    if disponible <= 0:
                        continue
                    quitar = min(disponible, restante)
                    header.resizeSection(col, actual - quitar)
                    restante -= quitar
                if restante > 0:
                    header.resizeSection(logical_index, new_size - restante)
            else:
                liberar = -delta
                if objetivo:
                    col = objetivo[0]
                    header.resizeSection(col, header.sectionSize(col) + liberar)
        finally:
            header.blockSignals(False)
    except RuntimeError:
        # El QHeaderView (objeto C++ de Qt) ya fue destruido — puede pasar
        # si esta señal llega durante el cierre/destrucción de un QDialog
        # ad-hoc (p. ej. los diálogos de Soporte con QTableWidget propio),
        # justo cuando el header ya no existe pero la señal todavía estaba
        # en la cola de eventos. No hay nada que actualizar en una ventana
        # que ya se está cerrando.
        pass


class _AnchoVentanaFilter(QObject):
    """Al redimensionar la ventana/panel que contiene la tabla (no un
    arrastre manual de columna), el sobrante o déficit de ancho lo absorbe
    únicamente 'stretch_col' — así la tabla también llena su ancho
    completo cuando cambia el tamaño de la ventana, sin tocar el resto de
    columnas que el usuario ya haya ajustado a mano."""

    def __init__(self, header, col_count, stretch_col, parent=None):
        super().__init__(parent)
        self.header = header
        self.col_count = col_count
        self.stretch_col = stretch_col

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Resize and 0 <= self.stretch_col < self.col_count:
            try:
                self.header.blockSignals(True)
                try:
                    otros = sum(
                        self.header.sectionSize(i)
                        for i in range(self.col_count) if i != self.stretch_col
                    )
                    disponible = obj.width() - otros
                    self.header.resizeSection(self.stretch_col, max(disponible, MIN_COL_WIDTH))
                finally:
                    self.header.blockSignals(False)
            except RuntimeError:
                # Mismo caso que _redistribuir_ancho_columnas: el header ya
                # no existe (ventana en proceso de cierre).
                pass
        return False


def _setup_header(header_or_widget, col_count, stretch_col):
    """Cabecera compartida por TODAS las tablas de MASys.

    Reparto de ancho:
    - Las columnas 'de contenido acotado' (todas menos stretch_col: fechas,
      estados, PID, %, enumerados...) se dimensionan una única vez a su
      contenido real (no solo al texto de la cabecera) en cuanto el
      modelo tiene filas por primera vez.
    - 'stretch_col' (contenido libre: nombre, ruta, descripción, valor...)
      se lleva el resto del ancho disponible del viewport.
    - A partir de ahí, TODAS las columnas son redimensionables a mano
      (Interactive), pero cualquier arrastre se compensa en las columnas
      vecinas (ver _redistribuir_ancho_columnas) y cualquier resize de la
      ventana lo absorbe solo stretch_col (ver _AnchoVentanaFilter): la
      tabla llena siempre su ancho completo, nunca hay scroll horizontal
      ni columnas empujadas fuera de la vista por la derecha."""
    if hasattr(header_or_widget, 'setItemDelegate'):
        header_or_widget.setItemDelegate(_get_readable_cell_delegate())
    if hasattr(header_or_widget, 'header'):
        header = header_or_widget.header()
    elif hasattr(header_or_widget, 'horizontalHeader'):
        header = header_or_widget.horizontalHeader()
    else:
        header = header_or_widget

    header.setMinimumSectionSize(MIN_COL_WIDTH)
    header.setStretchLastSection(False)
    for i in range(col_count):
        header.setSectionResizeMode(i, QHeaderView.Interactive)

    def _tamano_inicial_a_contenido():
        # Conectado a modelReset/layoutChanged: puede seguir llegando
        # mientras un QDialog ad-hoc (tablas de Soporte) está en pleno
        # cierre/destrucción, ya con el QHeaderView (objeto C++ de Qt) o el
        # viewport ya destruidos pero la señal aún en la cola de eventos.
        # Bug real confirmado con traceback: 'RuntimeError: wrapped C/C++
        # object of type QHeaderView has been deleted' — solo se daba con
        # la tabla vacía (0 filas), porque con filas cargadas esta función
        # ya había puesto _ancho_inicial_calculado=True antes del cierre y
        # dejaba de acceder al header en cualquier llamada posterior; con 0
        # filas el guard de abajo (modelo.rowCount()==0) hacía que SIEMPRE
        # se re-intentara leer 'header' de nuevo en cada señal, incluida la
        # que llega durante el cierre. No es un fallo de esta ventana en
        # particular: cualquier QTableWidget de un diálogo que se cierre
        # vacío puede dispararlo.
        try:
            if getattr(header, '_ancho_inicial_calculado', False):
                return
            modelo = header_or_widget.model()
            if not modelo or modelo.rowCount() == 0:
                return
            header._ancho_inicial_calculado = True

            header.blockSignals(True)
            try:
                for i in range(col_count):
                    if i != stretch_col:
                        header.setSectionResizeMode(i, QHeaderView.ResizeToContents)

                # Techo dinámico por columna, no uno fijo: con muchas columnas
                # (10 en Controladores, por ejemplo) un tope fijo generoso
                # (p. ej. 280px) seguía sumando más que el ancho de la ventana
                # si varias columnas a la vez querían ser anchas ("Problema",
                # "Clase" traducida, "Estado"...) — y como la columna elástica
                # nunca baja de MIN_COL_WIDTH, el sobrante empujaba a scroll
                # horizontal. Repartiendo el hueco disponible entre las
                # columnas no elásticas, la suma nunca puede superar el ancho
                # del viewport; las columnas con contenido corto no se ven
                # afectadas (el tope solo recorta a las que querían ser más
                # anchas que su parte proporcional), y el usuario siempre puede
                # ensanchar una a mano después (_redistribuir_ancho_columnas).
                columnas_fijas = max(col_count - 1, 1)
                presupuesto_col = max(MIN_COL_WIDTH, (header_or_widget.viewport().width() - MIN_COL_WIDTH) // columnas_fijas)

                anchos_fijos = 0
                for i in range(col_count):
                    if i != stretch_col:
                        ancho = min(header.sectionSize(i), presupuesto_col)
                        header.setSectionResizeMode(i, QHeaderView.Interactive)
                        header.resizeSection(i, ancho)
                        anchos_fijos += ancho
                if 0 <= stretch_col < col_count:
                    disponible = header_or_widget.viewport().width() - anchos_fijos
                    header.resizeSection(stretch_col, max(disponible, MIN_COL_WIDTH))
            finally:
                header.blockSignals(False)
        except RuntimeError:
            pass

    modelo_inicial = header_or_widget.model()
    if modelo_inicial is not None:
        modelo_inicial.modelReset.connect(_tamano_inicial_a_contenido)
        modelo_inicial.layoutChanged.connect(_tamano_inicial_a_contenido)
        _tamano_inicial_a_contenido()

    header.sectionResized.connect(
        lambda logical, old, new: _redistribuir_ancho_columnas(header, col_count, logical, old, new)
    )

    _filtro_ventana = _AnchoVentanaFilter(header, col_count, stretch_col, parent=header_or_widget)
    header_or_widget.viewport().installEventFilter(_filtro_ventana)

# ---------------------------
# Temas visuales (claro / oscuro)
# ---------------------------
LIGHT_STYLESHEET = """
            QWidget {
                font-family: 'Segoe UI', Arial;
                font-size: 9pt;
                background-color: #f5f6fa;
                color: #2c3e50;
            }

            QLineEdit {
                border: 1px solid #dcdde1;
                border-radius: 4px;
                padding: 6px 10px;
                background: white;
                color: #2c3e50;
            }
            QLineEdit:focus {
                border: 1px solid #3498db;
            }

            QPushButton {
                border-radius: 4px;
                padding: 6px 14px;
                border: none;
                font-weight: 600;
                min-height: 28px;
                background: #ecf0f1;
                color: #2c3e50;
            }
            QPushButton#primary {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #3498db, stop:1 #2980b9);
                color: white;
            }
            QPushButton#primary:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #5dade2, stop:1 #3498db);
            }

            QPushButton#success {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #27ae60, stop:1 #229954);
                color: white;
                font-size: 10pt;
                min-height: 35px;
            }
            QPushButton#success:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #2ecc71, stop:1 #27ae60);
            }

            QPushButton#ghost {
                background: #ecf0f1;
                color: #2c3e50;
            }
            QPushButton#ghost:hover {
                background: #d5dbdb;
            }

            QPushButton#navBackupsButton {
                background: #2c3e50;
                color: #ecf0f1;
            }
            QPushButton#navBackupsButton:hover {
                background: #34495e;
            }

            QPushButton#runas {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #e67e22, stop:1 #d35400);
                color: white;
                font-size: 9pt;
            }
            QPushButton#runas:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #f39c12, stop:1 #e67e22);
            }

            QPushButton#danger {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #e74c3c, stop:1 #c0392b);
                color: white;
                font-size: 9pt;
                font-weight: bold;
            }
            QPushButton#danger:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #ec7063, stop:1 #e74c3c);
            }
            QPushButton#danger:disabled {
                background: #bdc3c7;
                color: #7f8c8d;
            }

            QTableView {
                border: 1px solid #dcdde1;
                border-radius: 6px;
                background-color: white;
                gridline-color: #ecf0f1;
                selection-background-color: #3498db;
                selection-color: white;
                color: #2c3e50;
            }
            QTableView::item {
                padding: 4px 6px;
            }

            QHeaderView::section {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #34495e, stop:1 #2c3e50);
                color: white;
                padding: 8px;
                border: none;
                border-right: 1px solid #95a5a6;
                font-weight: 600;
            }

            QProgressBar {
                border: 1px solid #dcdde1;
                border-radius: 4px;
                text-align: center;
                height: 22px;
                background-color: white;
                color: #2c3e50;
                font-weight: 600;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #3498db, stop:1 #2ecc71);
                border-radius: 3px;
            }

            QCheckBox {
                spacing: 6px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 3px;
                border: 1px solid #bdc3c7;
            }
            QCheckBox::indicator:checked {
                background-color: #3498db;
                border-color: #2980b9;
            }

            QRadioButton {
                spacing: 6px;
            }
            QRadioButton::indicator {
                width: 16px;
                height: 16px;
                border-radius: 8px;
                border: 1px solid #bdc3c7;
            }
            QRadioButton::indicator:checked {
                background-color: #3498db;
                border-color: #2980b9;
            }

            QTabWidget::pane {
                border: 1px solid #dcdde1;
                border-radius: 6px;
                background: white;
            }
            QTabBar::tab {
                background: #ecf0f1;
                color: #2c3e50;
                padding: 8px 16px;
                margin-right: 4px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                font-weight: 600;
            }
            QTabBar::tab:selected {
                background: white;
                color: #3498db;
            }
            QTabBar::tab:hover:!selected {
                background: #d5dbdb;
            }

            QMenu {
                background: white;
                border: 1px solid #dcdde1;
                padding: 4px;
            }
            QMenu::item {
                padding: 6px 30px 6px 30px;
                color: #2c3e50;
            }
            QMenu::item:selected {
                background: #3498db;
                color: white;
            }
            QMenu::item:hover {
                background: #3498db;
                color: white;
            }

            QStatusBar {
                background: #f5f6fa;
                border-top: 1px solid #ecf0f1;
                color: #2c3e50;
            }

            QLabel#headerTitle {
                font-size: 16pt;
                font-weight: bold;
                color: #2c3e50;
            }
            QLabel#headerSubtitle {
                font-size: 9pt;
                color: #7f8c8d;
            }

            QDialog {
                background-color: #f5f6fa;
            }
            QTextEdit, QSpinBox, QComboBox {
                background: white;
                color: #2c3e50;
                border: 1px solid #dcdde1;
                border-radius: 4px;
            }

            QGroupBox {
                font-weight: bold;
                margin-top: 10px;
                border: 1px solid #dcdde1;
                border-radius: 6px;
                padding-top: 14px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
                color: #2c3e50;
            }

            QLabel#hostnameLabel {
                color: #2c3e50;
                font-weight: bold;
                font-size: 10pt;
            }
            QLabel#hintLabel {
                color: #7f8c8d;
                font-style: italic;
            }
            QLabel#perfilActivoLabel {
                color: white;
                background: #2979FF;
                font-weight: 700;
                font-size: 9.5pt;
                padding: 5px 12px;
                border-radius: 9px;
            }
            QLabel#fieldLabel {
                font-weight: bold;
                color: #2c3e50;
            }
            QLabel#countLabel {
                color: #34495e;
                font-weight: bold;
            }
            QLabel#sectionInfoLabel {
                color: #34495e;
                margin-bottom: 10px;
            }
            QLabel#mutedInfoLabel {
                color: #495057;
                font-size: 9pt;
            }
            QLabel#dialogSummaryLabel {
                font-weight: bold;
                font-size: 10pt;
                color: #343a40;
                padding: 4px 0;
            }
            QLabel#successNoticeLabel {
                font-size: 11pt;
                color: #155724;
                padding: 20px;
            }
            QFrame#hSeparator {
                background-color: #bdc3c7;
                max-height: 1px;
            }
        """

DARK_STYLESHEET = """
            QWidget {
                font-family: 'Segoe UI', Arial;
                font-size: 9pt;
                background-color: #1e1f29;
                color: #e6e6e6;
            }

            QLineEdit {
                border: 1px solid #3a3d4a;
                border-radius: 4px;
                padding: 6px 10px;
                background: #262837;
                color: #e6e6e6;
            }
            QLineEdit:focus {
                border: 1px solid #3498db;
            }

            QPushButton {
                border-radius: 4px;
                padding: 6px 14px;
                border: none;
                font-weight: 600;
                min-height: 28px;
                background: #323548;
                color: #e6e6e6;
            }
            QPushButton#primary {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #3498db, stop:1 #2980b9);
                color: white;
            }
            QPushButton#primary:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #5dade2, stop:1 #3498db);
            }

            QPushButton#success {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #27ae60, stop:1 #229954);
                color: white;
                font-size: 10pt;
                min-height: 35px;
            }
            QPushButton#success:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #2ecc71, stop:1 #27ae60);
            }

            QPushButton#ghost {
                background: #323548;
                color: #e6e6e6;
            }
            QPushButton#ghost:hover {
                background: #3f4358;
            }

            QPushButton#navBackupsButton {
                background: #2c3e50;
                color: #ecf0f1;
            }
            QPushButton#navBackupsButton:hover {
                background: #34495e;
            }

            QPushButton#runas {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #e67e22, stop:1 #d35400);
                color: white;
                font-size: 9pt;
            }
            QPushButton#runas:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #f39c12, stop:1 #e67e22);
            }

            QPushButton#danger {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #e74c3c, stop:1 #c0392b);
                color: white;
                font-size: 9pt;
                font-weight: bold;
            }
            QPushButton#danger:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #ec7063, stop:1 #e74c3c);
            }
            QPushButton#danger:disabled {
                background: #45485a;
                color: #7f8c9a;
            }

            QTableView {
                border: 1px solid #3a3d4a;
                border-radius: 6px;
                background-color: #262837;
                alternate-background-color: #2c2e40;
                gridline-color: #3a3d4a;
                selection-background-color: #3498db;
                selection-color: white;
                color: #e6e6e6;
            }
            QTableView::item {
                padding: 4px 6px;
            }

            QTreeView {
                alternate-background-color: #2c2e40;
            }

            QHeaderView::section {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #262837, stop:1 #1a1b24);
                color: #e6e6e6;
                padding: 8px;
                border: none;
                border-right: 1px solid #3a3d4a;
                font-weight: 600;
            }

            QProgressBar {
                border: 1px solid #3a3d4a;
                border-radius: 4px;
                text-align: center;
                height: 22px;
                background-color: #262837;
                color: #e6e6e6;
                font-weight: 600;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #3498db, stop:1 #2ecc71);
                border-radius: 3px;
            }

            QCheckBox {
                spacing: 6px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
                border-radius: 3px;
                border: 1px solid #4a4d5a;
                background: #262837;
            }
            QCheckBox::indicator:checked {
                background-color: #3498db;
                border-color: #2980b9;
            }

            QRadioButton {
                spacing: 6px;
            }
            QRadioButton::indicator {
                width: 16px;
                height: 16px;
                border-radius: 8px;
                border: 1px solid #4a4d5a;
                background: #262837;
            }
            QRadioButton::indicator:checked {
                background-color: #3498db;
                border-color: #2980b9;
            }

            QTabWidget::pane {
                border: 1px solid #3a3d4a;
                border-radius: 6px;
                background: #262837;
            }
            QTabBar::tab {
                background: #262837;
                color: #e6e6e6;
                padding: 8px 16px;
                margin-right: 4px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                font-weight: 600;
            }
            QTabBar::tab:selected {
                background: #323548;
                color: #5dade2;
            }
            QTabBar::tab:hover:!selected {
                background: #2c2e40;
            }

            QMenu {
                background: #262837;
                border: 1px solid #3a3d4a;
                padding: 4px;
                color: #e6e6e6;
            }
            QMenu::item {
                padding: 6px 30px 6px 30px;
                color: #e6e6e6;
            }
            QMenu::item:selected {
                background: #3498db;
                color: white;
            }
            QMenu::item:hover {
                background: #3498db;
                color: white;
            }

            QStatusBar {
                background: #1e1f29;
                border-top: 1px solid #3a3d4a;
                color: #e6e6e6;
            }

            QLabel#headerTitle {
                font-size: 16pt;
                font-weight: bold;
                color: #e6e6e6;
            }
            QLabel#headerSubtitle {
                font-size: 9pt;
                color: #9aa0ac;
            }

            QDialog {
                background-color: #1e1f29;
            }
            QTextEdit, QSpinBox, QComboBox {
                background: #262837;
                color: #e6e6e6;
                border: 1px solid #3a3d4a;
                border-radius: 4px;
            }

            QGroupBox {
                font-weight: bold;
                margin-top: 10px;
                border: 1px solid #3a3d4a;
                border-radius: 6px;
                padding-top: 14px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 4px;
                color: #e6e6e6;
            }

            QLabel#hostnameLabel {
                color: #5dade2;
                font-weight: bold;
                font-size: 10pt;
            }
            QLabel#hintLabel {
                color: #9aa0ac;
                font-style: italic;
            }
            QLabel#perfilActivoLabel {
                color: white;
                background: #3d8bff;
                font-weight: 700;
                font-size: 9.5pt;
                padding: 5px 12px;
                border-radius: 9px;
            }
            QLabel#fieldLabel {
                font-weight: bold;
                color: #e6e6e6;
            }
            QLabel#countLabel {
                color: #e6e6e6;
                font-weight: bold;
            }
            QLabel#sectionInfoLabel {
                color: #e6e6e6;
                margin-bottom: 10px;
            }
            QLabel#mutedInfoLabel {
                color: #9aa0ac;
                font-size: 9pt;
            }
            QLabel#dialogSummaryLabel {
                font-weight: bold;
                font-size: 10pt;
                color: #e6e6e6;
                padding: 4px 0;
            }
            QLabel#successNoticeLabel {
                font-size: 11pt;
                color: #2ecc71;
                padding: 20px;
            }
            QFrame#hSeparator {
                background-color: #3a3d4a;
                max-height: 1px;
            }
        """

NAV_TREE_STYLESHEET_LIGHT = """
            QTreeWidget {
                background-color: #2c3e50;
                color: #ecf0f1;
                border: none;
                outline: 0;
                font-size: 9pt;
            }
            QTreeWidget::item {
                padding: 7px 6px;
                border: none;
            }
            QTreeWidget::item:hover {
                background: #34495e;
            }
            QTreeWidget::item:selected {
                background: #3498db;
                color: white;
            }
            QTreeView::branch {
                border-image: none;
                image: none;
            }
        """

NAV_TREE_STYLESHEET_DARK = """
            QTreeWidget {
                background-color: #14151c;
                color: #d8dadf;
                border: none;
                outline: 0;
                font-size: 9pt;
            }
            QTreeWidget::item {
                padding: 7px 6px;
                border: none;
            }
            QTreeWidget::item:hover {
                background: #1c1e28;
            }
            QTreeWidget::item:selected {
                background: #3498db;
                color: white;
            }
            QTreeView::branch {
                border-image: none;
                image: none;
            }
        """

class ReadableCellTextDelegate(QStyledItemDelegate):
    """Delegate compartido por todas las QTableView del script: cuando una
    celda trae un color de fondo semántico (Qt.BackgroundRole — verde/
    amarillo/rojo de estado, riesgo, etc.) y el propio modelo NO fija ya un
    Qt.ForegroundRole explícito, calcula un color de texto oscuro o claro
    según el contraste del fondo, para que ese texto siga siendo legible
    tanto en tema claro como oscuro sin tener que tocar el color de fondo
    semántico de cada modelo ni duplicar esa lógica en ~35 métodos data()."""

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        try:
            if index.data(Qt.ForegroundRole) is not None:
                return
            bg = index.data(Qt.BackgroundRole)
            if bg is None:
                return
            color = bg if isinstance(bg, QColor) else QColor(bg)
            if not color.isValid():
                return
            luminance = 0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()
            text_color = QColor("#212529") if luminance > 140 else QColor("#f1f3f5")
            option.palette.setColor(QPalette.Text, text_color)
        except Exception:
            pass

