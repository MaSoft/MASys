# -*- coding: utf-8 -*-
"""Diálogo "Ver propiedades completas" de un dispositivo — texto crudo de
pnputil /enum-devices (IDs de hardware, ubicación, controladores
candidatos y su clasificación), en monoespaciado y de solo lectura.

Se muestra tal cual, sin parsear: las etiquetas de pnputil están
localizadas al idioma del sistema — parsear por texto de etiqueta sería
tan frágil como lo fue el parseo de 'quser' (bug real corregido en
Sesiones Activas). Aquí no hace falta: es solo para que el técnico LEA el
detalle, no para clasificar nada por código.

Se instancia bajo demanda desde ControladoresMixin._ver_propiedades_dispositivo."""

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit,
    QApplication,
)


class DriverPropertiesDialog(QDialog):
    def __init__(self, parent, nombre_dispositivo: str, texto: str):
        super().__init__(parent)
        self.setWindowTitle(f"Propiedades — {nombre_dispositivo}")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(700, 560)
        self._texto = texto

        c = (parent._dash_colors() if parent and hasattr(parent, "_dash_colors") else {
            "title": "#1a2634", "muted": "#8a94a6",
        })

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(22, 20, 22, 18)

        cabecera = QHBoxLayout()
        cabecera.setSpacing(12)
        lbl_icono = QLabel("📄")
        lbl_icono.setStyleSheet("font-size: 26pt; background: transparent; border: none;")
        cabecera.addWidget(lbl_icono)
        col_titulo = QVBoxLayout()
        col_titulo.setSpacing(2)
        lbl_titulo = QLabel(nombre_dispositivo)
        lbl_titulo.setWordWrap(True)
        lbl_titulo.setStyleSheet(
            f"font-size: 13pt; font-weight: 600; color: {c['title']}; background: transparent; border: none;")
        col_titulo.addWidget(lbl_titulo)
        lbl_subtitulo = QLabel("IDs de hardware, ubicación y controladores candidatos (tal cual los da pnputil)")
        lbl_subtitulo.setWordWrap(True)
        lbl_subtitulo.setStyleSheet(
            f"color: {c['muted']}; font-size: 9.5pt; background: transparent; border: none;")
        col_titulo.addWidget(lbl_subtitulo)
        cabecera.addLayout(col_titulo, 1)
        layout.addLayout(cabecera)

        self.texto_widget = QTextEdit()
        self.texto_widget.setReadOnly(True)
        self.texto_widget.setPlainText(texto)
        self.texto_widget.setFont(QFont("Consolas", 9))
        self.texto_widget.setLineWrapMode(QTextEdit.NoWrap)
        layout.addWidget(self.texto_widget, 1)

        botones = QHBoxLayout()
        self.btn_copiar = QPushButton("📋 Copiar todo")
        self.btn_copiar.clicked.connect(self._copiar)
        botones.addWidget(self.btn_copiar)
        botones.addStretch()
        self.btn_cerrar = QPushButton("Cerrar")
        self.btn_cerrar.clicked.connect(self.accept)
        botones.addWidget(self.btn_cerrar)
        layout.addLayout(botones)

    def _copiar(self):
        QApplication.clipboard().setText(self._texto)
