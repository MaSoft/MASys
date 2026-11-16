# -*- coding: utf-8 -*-
"""Diálogo "Detectar configuración de cuenta de correo" — dado un email y
una contraseña, prueba conexiones reales (IMAP/POP3/SMTP) contra ~35
proveedores conocidos hasta encontrar uno que funcione. Diálogo autónomo,
se instancia bajo demanda desde EmailMixin.abrir_detector_cuenta_correo.

Portado de GESTION DE CORREOS\\GestCorreov4.ps1 (pestaña "Probar Cuenta"),
corrigiendo la validación de certificado TLS deshabilitada del original
— ver msys/email_providers.py para el detalle de la corrección."""

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, QPushButton,
    QLineEdit, QCheckBox, QTableWidget, QTableWidgetItem, QMessageBox,
    QAbstractItemView, QProgressBar, QApplication,
)

from msys.collectors import EmailProviderTestThread
from msys.widgets.delegates import _setup_header

_COLOR_OK = QColor("#d4edda")
_COLOR_CREDENCIALES = QColor("#f8d7da")
_COLOR_NEUTRO = QColor("#fff3cd")


class EmailProviderDetectDialog(QDialog):
    def __init__(self, parent=None, email_inicial: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Detectar configuración de cuenta de correo")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(680, 480)
        self._thread = None
        self._encontrado = None

        c = (parent._dash_colors() if parent and hasattr(parent, "_dash_colors") else {
            "title": "#1a2634", "muted": "#8a94a6",
        })

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(22, 20, 22, 18)

        cabecera = QHBoxLayout()
        cabecera.setSpacing(12)
        lbl_icono = QLabel("🔍")
        lbl_icono.setStyleSheet("font-size: 26pt; background: transparent; border: none;")
        cabecera.addWidget(lbl_icono)
        col_titulo = QVBoxLayout()
        col_titulo.setSpacing(2)
        lbl_titulo = QLabel("Detectar configuración de cuenta de correo")
        lbl_titulo.setWordWrap(True)
        lbl_titulo.setStyleSheet(
            f"font-size: 13pt; font-weight: 600; color: {c['title']}; background: transparent; border: none;")
        col_titulo.addWidget(lbl_titulo)
        lbl_subtitulo = QLabel("Prueba credenciales reales contra ~35 proveedores conocidos (IMAP/POP3/SMTP)")
        lbl_subtitulo.setWordWrap(True)
        lbl_subtitulo.setStyleSheet(
            f"color: {c['muted']}; font-size: 9.5pt; background: transparent; border: none;")
        col_titulo.addWidget(lbl_subtitulo)
        cabecera.addLayout(col_titulo, 1)
        layout.addLayout(cabecera)

        aviso = QLabel(
            "⚠️ Esto envía la contraseña real a los servidores de correo probados, igual que "
            "haría el propio cliente de correo. Si la cuenta tiene verificación en dos pasos, "
            "usa una contraseña de aplicación en vez de la contraseña normal.")
        aviso.setWordWrap(True)
        aviso.setObjectName("hintLabel")
        layout.addWidget(aviso)

        form = QFormLayout()
        self.txt_email = QLineEdit()
        self.txt_email.setPlaceholderText("usuario@dominio.com")
        form.addRow("Correo:", self.txt_email)

        fila_pass = QHBoxLayout()
        self.txt_password = QLineEdit()
        self.txt_password.setEchoMode(QLineEdit.Password)
        fila_pass.addWidget(self.txt_password)
        self.chk_mostrar = QCheckBox("👁️ Mostrar")
        self.chk_mostrar.toggled.connect(
            lambda on: self.txt_password.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        fila_pass.addWidget(self.chk_mostrar)
        form.addRow("Contraseña:", fila_pass)
        layout.addLayout(form)

        self.lbl_estado = QLabel("Introduce el correo y la contraseña, y pulsa «Detectar».")
        self.lbl_estado.setObjectName("fieldLabel")
        layout.addWidget(self.lbl_estado)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.tabla = QTableWidget(0, 4)
        self.tabla.setHorizontalHeaderLabels(["Proveedor", "IMAP", "POP3", "SMTP"])
        self.tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tabla.setSelectionBehavior(QTableWidget.SelectRows)
        self.tabla.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.setSortingEnabled(False)
        _setup_header(self.tabla, 4, stretch_col=0)
        layout.addWidget(self.tabla, 1)

        self.lbl_resultado = QLabel("")
        self.lbl_resultado.setWordWrap(True)
        self.lbl_resultado.setObjectName("hintLabel")
        self.lbl_resultado.setVisible(False)
        layout.addWidget(self.lbl_resultado)

        botones = QHBoxLayout()
        self.btn_detectar = QPushButton("🔍 Detectar")
        self.btn_detectar.setObjectName("primary")
        self.btn_detectar.clicked.connect(self._iniciar)
        botones.addWidget(self.btn_detectar)
        self.btn_detener = QPushButton("⏹️ Detener")
        self.btn_detener.setEnabled(False)
        self.btn_detener.clicked.connect(self._detener)
        botones.addWidget(self.btn_detener)
        botones.addStretch()
        self.btn_copiar = QPushButton("📋 Copiar configuración")
        self.btn_copiar.setEnabled(False)
        self.btn_copiar.clicked.connect(self._copiar_configuracion)
        botones.addWidget(self.btn_copiar)
        self.btn_cerrar = QPushButton("Cerrar")
        self.btn_cerrar.clicked.connect(self.reject)
        botones.addWidget(self.btn_cerrar)
        layout.addLayout(botones)

        if email_inicial:
            self.txt_email.setText(email_inicial)
            self.txt_password.setFocus()

    def _iniciar(self):
        email = self.txt_email.text().strip()
        password = self.txt_password.text()
        if not email or "@" not in email:
            self._avisar_local("Detectar", "Introduce un correo válido.", QMessageBox.Warning)
            return
        if not password:
            self._avisar_local("Detectar", "Introduce la contraseña.", QMessageBox.Warning)
            return

        self._encontrado = None
        self.tabla.setRowCount(0)
        self.lbl_resultado.setVisible(False)
        self.btn_copiar.setEnabled(False)
        self.btn_detectar.setEnabled(False)
        self.btn_detener.setEnabled(True)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.lbl_estado.setText("🔄 Probando proveedores...")

        self._thread = EmailProviderTestThread(email, password)
        self._thread.progress.connect(self._on_progress)
        self._thread.provider_result.connect(self._on_provider_result)
        self._thread.finished_ok.connect(self._on_finished)
        self._thread.finished_error.connect(self._on_error)
        self._thread.start()

    def _detener(self):
        if self._thread is not None and self._thread.isRunning():
            self._thread.request_stop()
            self.btn_detener.setEnabled(False)

    def _on_progress(self, valor, texto):
        self.progress.setValue(valor)
        self.lbl_estado.setText(f"🔄 {texto}")

    def _on_provider_result(self, nombre, resultado):
        fila = self.tabla.rowCount()
        self.tabla.insertRow(fila)
        self.tabla.setItem(fila, 0, QTableWidgetItem(nombre))
        for col, clave in ((1, "imap"), (2, "pop3"), (3, "smtp")):
            valor = resultado.get(clave, "N/D")
            item = QTableWidgetItem(valor)
            if valor == "OK":
                item.setBackground(_COLOR_OK)
            elif valor == "Credenciales":
                item.setBackground(_COLOR_CREDENCIALES)
            elif valor not in ("N/D",):
                item.setBackground(_COLOR_NEUTRO)
            self.tabla.setItem(fila, col, item)
        self.tabla.scrollToBottom()

    def _on_finished(self, resultados, encontrado):
        self.progress.setVisible(False)
        self.btn_detectar.setEnabled(True)
        self.btn_detener.setEnabled(False)
        self._encontrado = encontrado

        if encontrado is None:
            self.lbl_estado.setText(
                f"❌ No se encontró un proveedor que funcione con esas credenciales ({len(resultados)} probados).")
            return

        proveedor, resultado = encontrado
        self.lbl_estado.setText(f"✅ Proveedor identificado: {proveedor.nombre}")
        self.lbl_resultado.setText(
            f"<b>{proveedor.nombre}</b> — credenciales verificadas correctamente<br>"
            f"IMAP: {proveedor.imap_host}:{proveedor.imap_port} "
            f"({'SSL' if proveedor.imap_ssl else 'sin cifrar'})<br>"
            f"POP3: {proveedor.pop_host or 'no disponible'}"
            + (f":{proveedor.pop_port} ({'SSL' if proveedor.pop_ssl else 'sin cifrar'})" if proveedor.pop_host else "")
            + f"<br>SMTP: {proveedor.smtp_host}:{proveedor.smtp_port} "
            f"({'SSL' if proveedor.smtp_ssl else 'STARTTLS'})")
        self.lbl_resultado.setVisible(True)
        self.btn_copiar.setEnabled(True)

    def _on_error(self, mensaje):
        self.progress.setVisible(False)
        self.btn_detectar.setEnabled(True)
        self.btn_detener.setEnabled(False)
        self.lbl_estado.setText(f"❌ {mensaje}")

    def _copiar_configuracion(self):
        if self._encontrado is None:
            return
        proveedor, _ = self._encontrado
        texto = (
            f"Proveedor: {proveedor.nombre}\n"
            f"IMAP: {proveedor.imap_host}:{proveedor.imap_port} ({'SSL' if proveedor.imap_ssl else 'sin cifrar'})\n"
            f"POP3: {proveedor.pop_host or 'no disponible'}"
            + (f":{proveedor.pop_port} ({'SSL' if proveedor.pop_ssl else 'sin cifrar'})\n" if proveedor.pop_host else "\n")
            + f"SMTP: {proveedor.smtp_host}:{proveedor.smtp_port} ({'SSL' if proveedor.smtp_ssl else 'STARTTLS'})\n"
        )
        QApplication.clipboard().setText(texto)
        self.lbl_estado.setText("✅ Configuración copiada al portapapeles")

    def _avisar_local(self, titulo, texto, icono=QMessageBox.Information):
        box = QMessageBox(self)
        box.setWindowFlags(box.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        box.setWindowTitle(titulo)
        box.setText(texto)
        box.setIcon(icono)
        box.exec_()

    def closeEvent(self, event):
        # request_stop() no interrumpe una prueba de socket ya en curso
        # (hasta ~6s de timeout cada una) — 3000ms podía no ser suficiente
        # y se cerraba igual, destruyendo un QThread todavía vivo (riesgo
        # real de "QThread: Destroyed while thread is still running").
        # Si tras esperar más sigue vivo, se rechaza el cierre en vez de
        # arriesgar, y se reintenta solo cuando el hilo ya ha terminado.
        if self._thread is not None and self._thread.isRunning():
            self._thread.request_stop()
            self._thread.wait(8000)
            if self._thread.isRunning():
                self.lbl_estado.setText("⏳ Deteniendo la detección, vuelve a cerrar en un momento...")
                event.ignore()
                return
        event.accept()
