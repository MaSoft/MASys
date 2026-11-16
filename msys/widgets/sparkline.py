# -*- coding: utf-8 -*-
"""Widget de mini-grafico de linea (sparkline) para metricas en vivo.
Extraido de multisys.py (Fase 1 de modularizacion)."""

from PyQt5.QtCore import Qt, QRectF
from PyQt5.QtGui import QPainter, QPen, QColor, QFont
from PyQt5.QtWidgets import QWidget

class SparklineWidget(QWidget):
    """Mini gráfica de área (sin dependencias externas) para mostrar el
    histórico reciente de una métrica (CPU%, RAM%, etc.), con relleno
    degradado bajo la línea al estilo de un panel de monitorización."""
    def __init__(self, color="#3498db", max_points=60, parent=None):
        super().__init__(parent)
        self._values = []
        self._max_points = max_points
        self._color = QColor(color)
        self.setMinimumHeight(46)

    def add_value(self, value: float):
        self._values.append(max(0.0, min(100.0, value)))
        if len(self._values) > self._max_points:
            self._values.pop(0)
        self.update()

    def paintEvent(self, event):
        from PyQt5.QtGui import QLinearGradient, QPainterPath
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        # Líneas de referencia sutiles (25/50/75%)
        grid_pen = QPen(QColor(0, 0, 0, 18))
        grid_pen.setWidth(1)
        painter.setPen(grid_pen)
        for frac in (0.25, 0.5, 0.75):
            y = int(h * frac)
            painter.drawLine(0, y, w, y)

        if len(self._values) < 2:
            painter.end()
            return

        n = len(self._values)
        step_x = w / max(1, self._max_points - 1)
        start_idx = self._max_points - n
        points = []
        for i, v in enumerate(self._values):
            x = (start_idx + i) * step_x
            y = h - (v / 100.0) * (h - 6) - 3
            points.append((x, y))

        # Área rellena con degradado bajo la línea
        area = QPainterPath()
        area.moveTo(points[0][0], h)
        for x, y in points:
            area.lineTo(x, y)
        area.lineTo(points[-1][0], h)
        area.closeSubpath()

        gradient = QLinearGradient(0, 0, 0, h)
        fill_color = QColor(self._color)
        fill_color.setAlpha(90)
        gradient.setColorAt(0, fill_color)
        fill_color_end = QColor(self._color)
        fill_color_end.setAlpha(6)
        gradient.setColorAt(1, fill_color_end)
        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawPath(area)

        # Línea principal
        pen = QPen(self._color)
        pen.setWidth(2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        line_path = QPainterPath()
        line_path.moveTo(points[0][0], points[0][1])
        for x, y in points[1:]:
            line_path.lineTo(x, y)
        painter.drawPath(line_path)

        painter.end()
