# -*- coding: utf-8 -*-
"""Diálogo "Generar QR" para compartir una red WiFi guardada con un móvil
— diálogo autónomo (no es un mixin), se instancia bajo demanda desde
WiFiMixin._wifi_generar_qr_seleccionada. Usa el formato estándar
'WIFI:T:...;S:...;P:...;;' que reconoce la cámara de Android/iOS al
escanear, sin necesidad de ninguna app aparte."""

from io import BytesIO

import qrcode

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QMessageBox,
)

from msys.core.utils import dialogo_guardar_archivo, ruta_guardado_predeterminada


def _escapar_valor_qr_wifi(valor: str) -> str:
    """Los caracteres especiales del formato WIFI: (\\, ;, ,, ", :) deben
    escaparse con una barra invertida — si no, un SSID o contraseña que
    los contenga rompería el formato y el móvil no podría interpretarlo."""
    for ch in ("\\", ";", ",", '"', ":"):
        valor = valor.replace(ch, "\\" + ch)
    return valor


def generar_qr_wifi_pixmap(ssid: str, password: str, autenticacion: str) -> QPixmap:
    auth_lower = (autenticacion or "").strip().lower()
    if auth_lower in ("open", "abierta", "ninguna", "none", ""):
        texto = f"WIFI:T:nopass;S:{_escapar_valor_qr_wifi(ssid)};;"
    else:
        tipo = "WEP" if "wep" in auth_lower else "WPA"
        texto = (f"WIFI:T:{tipo};S:{_escapar_valor_qr_wifi(ssid)};"
                 f"P:{_escapar_valor_qr_wifi(password)};;")

    imagen = qrcode.make(texto)
    buffer = BytesIO()
    imagen.save(buffer, format="PNG")
    pixmap = QPixmap()
    pixmap.loadFromData(buffer.getvalue())
    return pixmap


class WiFiQRDialog(QDialog):
    def __init__(self, ssid: str, password: str, autenticacion: str, parent=None):
        super().__init__(parent)
        self._ssid = ssid
        self.setWindowTitle(f"Código QR — {ssid}")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        c = (parent._dash_colors() if parent and hasattr(parent, "_dash_colors") else {
            "title": "#1a2634", "muted": "#8a94a6",
            "alerts_bg": "#f7f9fc", "alerts_border": "#e3e7ee", "alerts_text": "#2c3e50",
        })

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(22, 20, 22, 18)

        # ── Cabecera: icono + título + subtítulo, mismo patrón que
        # _confirmar_reparacion — no un bloque de texto plano.
        cabecera = QHBoxLayout()
        cabecera.setSpacing(12)
        lbl_icono = QLabel("📶")
        lbl_icono.setStyleSheet("font-size: 26pt; background: transparent; border: none;")
        cabecera.addWidget(lbl_icono)
        col_titulo = QVBoxLayout()
        col_titulo.setSpacing(2)
        lbl_titulo = QLabel(f"Código QR para «{ssid}»")
        lbl_titulo.setWordWrap(True)
        lbl_titulo.setStyleSheet(
            f"font-size: 13pt; font-weight: 600; color: {c['title']}; background: transparent; border: none;")
        col_titulo.addWidget(lbl_titulo)
        lbl_subtitulo = QLabel("Escanéalo con la cámara del móvil para conectarse sin teclear la contraseña")
        lbl_subtitulo.setWordWrap(True)
        lbl_subtitulo.setStyleSheet(
            f"color: {c['muted']}; font-size: 9.5pt; background: transparent; border: none;")
        col_titulo.addWidget(lbl_subtitulo)
        cabecera.addLayout(col_titulo, 1)
        layout.addLayout(cabecera)

        self._pixmap = generar_qr_wifi_pixmap(ssid, password, autenticacion)
        lbl_qr = QLabel()
        lbl_qr.setAlignment(Qt.AlignCenter)
        lbl_qr.setPixmap(self._pixmap.scaled(320, 320, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        lbl_qr.setStyleSheet("background: white; border-radius: 8px; padding: 12px;")
        layout.addWidget(lbl_qr)

        # ── Tarjeta de aviso: el QR lleva la contraseña en claro dentro.
        marco_aviso = QFrame()
        marco_aviso.setStyleSheet(
            f"QFrame {{ background: {c['alerts_bg']}; border: 1px solid {c['alerts_border']}; border-radius: 8px; }}")
        v_aviso = QVBoxLayout(marco_aviso)
        v_aviso.setContentsMargins(14, 10, 14, 10)
        fila = QHBoxLayout()
        fila.setSpacing(8)
        lbl_bullet = QLabel("•")
        lbl_bullet.setAlignment(Qt.AlignTop)
        lbl_bullet.setStyleSheet(f"color: {c['alerts_text']}; font-weight: 700; background: transparent; border: none;")
        fila.addWidget(lbl_bullet)
        lbl_texto = QLabel("El código contiene la contraseña de la red en texto claro: "
                            "cualquiera que lo escanee podrá conectarse. Compártelo solo con "
                            "quien deba tener acceso.")
        lbl_texto.setWordWrap(True)
        lbl_texto.setStyleSheet(f"color: {c['alerts_text']}; background: transparent; border: none;")
        fila.addWidget(lbl_texto, 1)
        v_aviso.addLayout(fila)
        layout.addWidget(marco_aviso)

        botones = QHBoxLayout()
        botones.addStretch()
        btn_guardar = QPushButton("💾 Guardar imagen...")
        btn_guardar.clicked.connect(self._guardar_imagen)
        botones.addWidget(btn_guardar)
        btn_cerrar = QPushButton("Cerrar")
        btn_cerrar.clicked.connect(self.accept)
        botones.addWidget(btn_cerrar)
        layout.addLayout(botones)

    def _guardar_imagen(self):
        ruta = dialogo_guardar_archivo(
            self, "Guardar código QR",
            ruta_guardado_predeterminada(f"wifi_qr_{self._ssid}.png"),
            "Imagen PNG (*.png)")
        if not ruta:
            return
        if self._pixmap.save(ruta, "PNG"):
            self._avisar("Código QR", f"Guardado en:\n{ruta}")
        else:
            self._avisar("Código QR", "No se pudo guardar la imagen.", QMessageBox.Warning)
