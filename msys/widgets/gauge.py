# -*- coding: utf-8 -*-
"""Widget de gauge circular (arco de progreso tipo velocimetro) para
metricas en vivo (CPU/RAM). Extraido de multisys.py (Fase 1 de
modularizacion)."""

from PyQt5.QtCore import Qt, QRectF
from PyQt5.QtGui import QPainter, QPen, QColor, QFont
from PyQt5.QtWidgets import QWidget

class CircularGaugeWidget(QWidget):
    """Gauge circular (arco de progreso, estilo velocímetro) para mostrar un
    porcentaje con aspecto de panel de monitorización profesional."""
    def __init__(self, color="#3498db", track_color="#e9ecef", text_color="#2c3e50",
                 suffix="%", parent=None):
        super().__init__(parent)
        self._value = 0.0
        self._color = QColor(color)
        self._track_color = QColor(track_color)
        self._text_color = QColor(text_color)
        self._suffix = suffix
        self.setMinimumSize(96, 96)

    def set_value(self, value: float):
        self._value = max(0.0, min(100.0, value))
        self.update()

    def set_color(self, color):
        self._color = QColor(color)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        side = min(self.width(), self.height())
        pen_width = max(6, int(side * 0.11))
        margin = pen_width / 2 + 2
        rect = QRectF(margin, margin, side - 2 * margin, side - 2 * margin)
        rect.moveCenter(QRectF(0, 0, self.width(), self.height()).center())

        pen_track = QPen(self._track_color)
        pen_track.setWidth(pen_width)
        pen_track.setCapStyle(Qt.RoundCap)
        painter.setPen(pen_track)
        painter.drawArc(rect, 90 * 16, -360 * 16)

        if self._value > 0:
            pen_val = QPen(self._color)
            pen_val.setWidth(pen_width)
            pen_val.setCapStyle(Qt.RoundCap)
            painter.setPen(pen_val)
            span = -int(360 * 16 * (self._value / 100.0))
            painter.drawArc(rect, 90 * 16, span)

        painter.setPen(self._text_color)
        font = QFont("Segoe UI", max(9, int(side * 0.155)), QFont.Bold)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignCenter, f"{int(round(self._value))}{self._suffix}")

        painter.end()
