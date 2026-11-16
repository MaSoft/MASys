# -*- coding: utf-8 -*-
"""Diálogo "📋 Historial de copias de seguridad" — acción clásica de
🛠️ Soporte en 📀 Copias de Seguridad (ver msys/ui/soporte.py,
_registro_soporte_por_seccion). Sustituye a los botones CSV/Informe HTML/
PDF/Guardar Resultados de la barra inferior en esta sección concreta: esa
pestaña ya no tiene una tabla de datos que exportar (son tarjetas de
acción, ver msys/ui/backups.py), así que en vez de eso lista lo que YA se
hizo — cada BackupGeneratorDialog y ForensicCaptureDialog registra su
ejecución en self.repair_audit_db (msys/domain/repair_audit.py) al
terminar, con éxito o con avisos.

Lista Fecha/Hora, Categoría, Usuario y Destino, con filtro de búsqueda en
caliente, y genera su propio informe HTML reutilizando el exportador
genérico de secciones del padre (ReportingMixin._exportar_html_seccion) —
sobre lo que esté visible en ese momento (respeta el filtro)."""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTableWidget, QTableWidgetItem, QAbstractItemView, QMessageBox,
)

from msys.widgets.delegates import _setup_header

_SECCION_BACKUPS = "📀 Copias de Seguridad"
_HEADERS = ["Fecha y hora", "Tipo", "Categoría", "Usuario", "Destino", "Resultado"]


class BackupHistoryDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("📋 Historial de copias de seguridad")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(760, 480)
        self._filas_todas = []
        self._filas_mostradas = []

        v = QVBoxLayout(self)
        v.setSpacing(10)

        fila_filtro = QHBoxLayout()
        fila_filtro.addWidget(QLabel("🔎 Buscar:"))
        self.txt_filtro = QLineEdit()
        self.txt_filtro.setPlaceholderText("Filtra por categoría, usuario o carpeta destino...")
        self.txt_filtro.textChanged.connect(self._aplicar_filtro)
        fila_filtro.addWidget(self.txt_filtro, 1)
        btn_actualizar = QPushButton("🔄 Actualizar")
        btn_actualizar.clicked.connect(self._recargar)
        fila_filtro.addWidget(btn_actualizar)
        v.addLayout(fila_filtro)

        self.tabla = QTableWidget(0, len(_HEADERS))
        self.tabla.setHorizontalHeaderLabels(_HEADERS)
        self.tabla.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.tabla.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tabla.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.setAlternatingRowColors(True)
        _setup_header(self.tabla, len(_HEADERS), stretch_col=4)
        v.addWidget(self.tabla, 1)

        self.lbl_contador = QLabel()
        self.lbl_contador.setObjectName("hintLabel")
        v.addWidget(self.lbl_contador)

        fila_botones = QHBoxLayout()
        self.btn_html = QPushButton("🌐 Informe HTML (de lo mostrado)")
        self.btn_html.clicked.connect(self._exportar_html)
        fila_botones.addWidget(self.btn_html)
        fila_botones.addStretch()
        btn_cerrar = QPushButton("Cerrar")
        btn_cerrar.clicked.connect(self.accept)
        fila_botones.addWidget(btn_cerrar)
        v.addLayout(fila_botones)

        self._recargar()

    def _recargar(self):
        parent = self.parent()
        entradas = []
        if parent and hasattr(parent, "repair_audit_db"):
            entradas = parent.repair_audit_db.obtener_historial_seccion(_SECCION_BACKUPS)
        self._filas_todas = [
            (fecha, "♻️ RESTORE" if tipo_operacion == "RESTORE" else "💾 BACKUP",
             categoria, usuario, destino, "✅ OK" if exito else "❌ Con avisos")
            for fecha, categoria, usuario, destino, exito, tipo_operacion in entradas
        ]
        self._aplicar_filtro()

    def _aplicar_filtro(self):
        texto = self.txt_filtro.text().strip().lower()
        if texto:
            filas = [f for f in self._filas_todas if texto in " ".join(f).lower()]
        else:
            filas = self._filas_todas

        self.tabla.setSortingEnabled(False)
        self.tabla.setRowCount(len(filas))
        for r, fila in enumerate(filas):
            for c, valor in enumerate(fila):
                self.tabla.setItem(r, c, QTableWidgetItem(valor))
        self.tabla.setSortingEnabled(True)

        self._filas_mostradas = filas
        total = len(self._filas_todas)
        mostradas = len(filas)
        self.lbl_contador.setText(
            f"{mostradas} de {total} copia(s) de seguridad registradas" if mostradas != total
            else f"{total} copia(s) de seguridad registradas")
        self.btn_html.setEnabled(mostradas > 0)

    def _exportar_html(self):
        parent = self.parent()
        if not (parent and hasattr(parent, "_exportar_html_seccion")):
            QMessageBox.warning(self, "No disponible", "No se puede generar el informe HTML desde aquí.")
            return
        if not self._filas_mostradas:
            QMessageBox.information(self, "Sin datos", "No hay copias de seguridad que exportar con el filtro actual.")
            return
        parent._exportar_html_seccion("📀 Historial de Copias de Seguridad", _HEADERS, self._filas_mostradas)
