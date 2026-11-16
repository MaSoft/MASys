# -*- coding: utf-8 -*-
"""Logo de MASys (círculo navy + «M»): un solo dibujo para la ventana, la
bandeja y el .ico del instalador (INSTALL/create_icon.py)."""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QFont, QPainter, QPixmap


def dibujar_icono_app(size: int = 32) -> QPixmap:
    """Pixmap del logo DIBUJADO al tamaño pedido — nunca escalando un pixmap ya
    generado a otro tamaño: QIcon.pixmap(tamaño_mayor) sobre un QIcon de un único
    pixmap pequeño NO lo amplía de verdad (se queda con el tamaño original y el
    hueco sobrante en blanco alrededor)."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor("#2c3e50"))
    painter.setPen(Qt.NoPen)
    margen = max(1, round(size / 32))
    painter.drawEllipse(margen, margen, size - 2 * margen, size - 2 * margen)
    painter.setPen(QColor("white"))
    painter.setFont(QFont("Segoe UI", max(6, round(size * 13 / 32)), QFont.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "M")
    painter.end()
    return pixmap
