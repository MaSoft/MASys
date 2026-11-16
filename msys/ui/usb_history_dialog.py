# -*- coding: utf-8 -*-
"""Diálogo "Historial de almacenamiento USB" — pendrives y discos externos
que se han conectado ALGUNA VEZ a este equipo, no solo los presentes ahora
mismo. Lee HKLM\\SYSTEM\\CurrentControlSet\\Enum\\USBSTOR (ver
leer_historial_usb_almacenamiento en collectors.py) — útil para investigar
fugas de datos o dispositivos desconocidos, algo que la tabla principal de
Dispositivos USB (solo lo conectado ahora) no puede responder.

Se instancia bajo demanda desde SoporteMixin._soporte_historial_usb en
soporte.py, acción clásica de "🔌 Dispositivos USB"."""

import csv
import time

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QAbstractItemView, QApplication,
)

from msys.core.utils import ruta_guardado_predeterminada, dialogo_guardar_archivo, _SafeCsvWriter
from msys.collectors import leer_historial_usb_almacenamiento


class USBHistoryDialog(QDialog):
    HEADERS = ["Nombre", "Clase de dispositivo", "Número de serie"]

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Historial de almacenamiento USB")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(720, 460)

        c = (parent._dash_colors() if parent and hasattr(parent, "_dash_colors") else {
            "title": "#1a2634", "muted": "#8a94a6",
        })

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(22, 20, 22, 18)

        cabecera = QHBoxLayout()
        cabecera.setSpacing(12)
        lbl_icono = QLabel("🕘")
        lbl_icono.setStyleSheet("font-size: 26pt; background: transparent; border: none;")
        cabecera.addWidget(lbl_icono)
        col_titulo = QVBoxLayout()
        col_titulo.setSpacing(2)
        lbl_titulo = QLabel("Historial de almacenamiento USB")
        lbl_titulo.setStyleSheet(
            f"font-size: 13pt; font-weight: 600; color: {c['title']}; background: transparent; border: none;")
        col_titulo.addWidget(lbl_titulo)
        lbl_subtitulo = QLabel(
            "Pendrives y discos externos que se han conectado alguna vez a este equipo, "
            "aunque no estén conectados ahora — útil para investigar fugas de datos o "
            "dispositivos desconocidos"
        )
        lbl_subtitulo.setWordWrap(True)
        lbl_subtitulo.setStyleSheet(f"color: {c['muted']}; font-size: 9.5pt; background: transparent; border: none;")
        col_titulo.addWidget(lbl_subtitulo)
        cabecera.addLayout(col_titulo, 1)
        layout.addLayout(cabecera)

        self.lbl_estado = QLabel("🔄 Cargando...")
        self.lbl_estado.setObjectName("fieldLabel")
        layout.addWidget(self.lbl_estado)

        self.tabla = QTableWidget(0, len(self.HEADERS))
        self.tabla.setHorizontalHeaderLabels(self.HEADERS)
        self.tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tabla.setSelectionBehavior(QTableWidget.SelectRows)
        self.tabla.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.setSortingEnabled(True)
        self.tabla.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.tabla, 1)

        botones = QHBoxLayout()
        self.btn_actualizar = QPushButton("🔄 Actualizar")
        self.btn_actualizar.clicked.connect(self._cargar)
        botones.addWidget(self.btn_actualizar)
        botones.addStretch()
        self.btn_csv = QPushButton("📤 Exportar CSV")
        self.btn_csv.clicked.connect(self._exportar_csv)
        botones.addWidget(self.btn_csv)
        self.btn_cerrar = QPushButton("Cerrar")
        self.btn_cerrar.clicked.connect(self.accept)
        botones.addWidget(self.btn_cerrar)
        layout.addLayout(botones)

        self._cargar()

    def _cargar(self):
        self.lbl_estado.setText("🔄 Cargando...")
        QApplication.processEvents()

        dispositivos = leer_historial_usb_almacenamiento()

        self.tabla.setSortingEnabled(False)
        self.tabla.setRowCount(len(dispositivos))
        for i, d in enumerate(dispositivos):
            self.tabla.setItem(i, 0, QTableWidgetItem(d["Nombre"]))
            self.tabla.setItem(i, 1, QTableWidgetItem(d["Clase"]))
            self.tabla.setItem(i, 2, QTableWidgetItem(d["NumeroSerie"]))
        self.tabla.setSortingEnabled(True)

        if dispositivos:
            self.lbl_estado.setText(f"✅ {len(dispositivos)} dispositivo(s) en el historial")
        else:
            self.lbl_estado.setText("ℹ️ No se ha encontrado historial (o el registro no es accesible)")

    def _exportar_csv(self):
        if self.tabla.rowCount() == 0:
            self.lbl_estado.setText("ℹ️ No hay datos para exportar")
            return
        default_name = f"HistorialUSB_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        path = dialogo_guardar_archivo(self, "Guardar CSV", ruta_guardado_predeterminada(default_name), "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = _SafeCsvWriter(csv.writer(f, delimiter=";"))
                writer.writerow(self.HEADERS)
                for r in range(self.tabla.rowCount()):
                    writer.writerow([self.tabla.item(r, c).text() for c in range(len(self.HEADERS))])
            self.lbl_estado.setText(f"✅ Exportado a {path}")
        except OSError as e:
            self.lbl_estado.setText(f"❌ No se pudo exportar: {e}")
