# -*- coding: utf-8 -*-
"""Diálogo "Dispositivos USB sospechosos" — indicios tipo BadUSB: un mismo
VID/PID (mismo dispositivo físico) presentándose ante Windows con más de
una clase de PnP a la vez (p. ej. un pendrive que también se registra como
teclado HID). Es un indicio, no una condena — algunos periféricos legítimos
también son compuestos, por eso cada fila explica el motivo exacto en vez
de solo etiquetar "sospechoso" a secas.

Se instancia bajo demanda desde SoporteMixin._soporte_usb_sospechosos en
soporte.py, acción clásica de "🔌 Dispositivos USB"."""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QAbstractItemView, QApplication,
)

from msys.collectors import detectar_usb_sospechosos


class USBSuspiciousDialog(QDialog):
    HEADERS = ["Dispositivo", "Clase", "VID/PID", "Motivo"]

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Dispositivos USB sospechosos")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(820, 460)

        c = (parent._dash_colors() if parent and hasattr(parent, "_dash_colors") else {
            "title": "#1a2634", "muted": "#8a94a6",
        })

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(22, 20, 22, 18)

        cabecera = QHBoxLayout()
        cabecera.setSpacing(12)
        lbl_icono = QLabel("🕵️")
        lbl_icono.setStyleSheet("font-size: 26pt; background: transparent; border: none;")
        cabecera.addWidget(lbl_icono)
        col_titulo = QVBoxLayout()
        col_titulo.setSpacing(2)
        lbl_titulo = QLabel("Dispositivos USB sospechosos")
        lbl_titulo.setStyleSheet(
            f"font-size: 13pt; font-weight: 600; color: {c['title']}; background: transparent; border: none;")
        col_titulo.addWidget(lbl_titulo)
        lbl_subtitulo = QLabel(
            "Un mismo dispositivo físico (VID/PID) que se presenta con varias clases a la vez "
            "— el patrón más documentado de ataque BadUSB (Rubber Ducky, O.MG Cable...). Es un "
            "indicio, no una condena: revisa el motivo antes de actuar."
        )
        lbl_subtitulo.setWordWrap(True)
        lbl_subtitulo.setStyleSheet(f"color: {c['muted']}; font-size: 9.5pt; background: transparent; border: none;")
        col_titulo.addWidget(lbl_subtitulo)
        cabecera.addLayout(col_titulo, 1)
        layout.addLayout(cabecera)

        self.lbl_estado = QLabel("🔄 Analizando...")
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
        self.btn_actualizar = QPushButton("🔄 Volver a analizar")
        self.btn_actualizar.clicked.connect(self._cargar)
        botones.addWidget(self.btn_actualizar)
        botones.addStretch()
        self.btn_cerrar = QPushButton("Cerrar")
        self.btn_cerrar.clicked.connect(self.accept)
        botones.addWidget(self.btn_cerrar)
        layout.addLayout(botones)

        self._cargar()

    def _cargar(self):
        self.lbl_estado.setText("🔄 Analizando...")
        self.btn_actualizar.setEnabled(False)
        QApplication.processEvents()

        ok, datos = detectar_usb_sospechosos()
        self.btn_actualizar.setEnabled(True)

        if not ok:
            self.lbl_estado.setText(f"❌ {datos}")
            self.tabla.setRowCount(0)
            return

        self.tabla.setSortingEnabled(False)
        self.tabla.setRowCount(len(datos))
        for i, d in enumerate(datos):
            self.tabla.setItem(i, 0, QTableWidgetItem(d.get("Nombre", "") or ""))
            self.tabla.setItem(i, 1, QTableWidgetItem(d.get("Clase", "") or ""))
            self.tabla.setItem(i, 2, QTableWidgetItem(d.get("VidPid", "") or ""))
            self.tabla.setItem(i, 3, QTableWidgetItem(d.get("Motivo", "") or ""))
        self.tabla.setSortingEnabled(True)

        if datos:
            self.lbl_estado.setText(f"⚠️ {len(datos)} entrada(s) con indicios encontradas")
        else:
            self.lbl_estado.setText("✅ Sin indicios de dispositivos USB compuestos sospechosos")
