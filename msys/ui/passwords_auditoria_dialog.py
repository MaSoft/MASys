# -*- coding: utf-8 -*-
"""Diálogo de Auditoría de contraseñas (sección 🌐 Navegadores). AUDITA las
contraseñas que Chrome/Edge ya tienen guardadas: fortaleza (local),
reutilización (local) y exposición en filtraciones conocidas (Have I Been
Pwned por k-anonimato — solo salen del equipo 5 caracteres del hash).

No es un gestor: no escribe ni cambia ninguna credencial. Motor en
msys/domain/passwords_auditoria.py. Las contraseñas se muestran ocultas
salvo que se active «Mostrar en claro», y la exportación respeta ese estado
(igual que el visor de contraseñas guardadas)."""

import csv
import time

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QTableWidget, QTableWidgetItem, QMessageBox, QProgressBar, QCheckBox,
    QAbstractItemView,
)

from msys.domain import passwords_auditoria as pa
from msys.core import tareas, informe_html
from msys.core.utils import ruta_guardado_predeterminada, dialogo_guardar_archivo, _SafeCsvWriter

_COLUMNAS = ["Sitio", "Usuario", "Contraseña", "Fortaleza", "Reutilizada", "Expuesta", "Motivos"]
_COLOR_FORT = {pa.MUY_DEBIL: "#dc2626", pa.DEBIL: "#ca8a04", pa.ACEPTABLE: "#2563eb", pa.FUERTE: "#16a34a"}


def _texto_expuesta(n):
    if n == pa.NO_COMPROBADA:
        return "—"
    if n == 0:
        return "Limpia"
    return f"⚠️ {n:,}".replace(",", ".") + " veces"


class AuditoriaPasswordsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Auditoría de contraseñas")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(900, 620)
        self._filas = []
        self._tarea = None

        c = (parent._dash_colors() if parent and hasattr(parent, "_dash_colors") else
             {"title": "#1a2634", "muted": "#8a94a6"})

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(20, 18, 20, 16)

        cab = QHBoxLayout()
        cab.setSpacing(12)
        icono = QLabel("🛡️")
        icono.setStyleSheet("font-size: 24pt; background: transparent; border: none;")
        cab.addWidget(icono)
        col = QVBoxLayout()
        col.setSpacing(2)
        t = QLabel("Auditoría de contraseñas")
        t.setStyleSheet(f"font-size: 13pt; font-weight: 600; color: {c['title']}; background: transparent; border: none;")
        col.addWidget(t)
        s = QLabel("Revisa fortaleza, reutilización y si están en filtraciones conocidas — solo audita, no cambia nada")
        s.setWordWrap(True)
        s.setStyleSheet(f"color: {c['muted']}; font-size: 9.5pt; background: transparent; border: none;")
        col.addWidget(s)
        cab.addLayout(col, 1)
        layout.addLayout(cab)

        aviso = QLabel("La comprobación de filtraciones usa Have I Been Pwned por k-anonimato: solo se envían 5 "
                       "caracteres del hash, nunca la contraseña. Requiere internet. Solo Chrome y Edge.")
        aviso.setWordWrap(True)
        aviso.setObjectName("hintLabel")
        layout.addWidget(aviso)

        fila = QHBoxLayout()
        fila.addWidget(QLabel("Perfil:"))
        self.combo = QComboBox()
        self._perfiles = pa.perfiles_passwords()
        for p in self._perfiles:
            self.combo.addItem(f"{p.navegador} — {p.nombre_perfil}", p)
        fila.addWidget(self.combo, 1)
        self.chk_mostrar = QCheckBox("👁️ Mostrar en claro")
        self.chk_mostrar.toggled.connect(self._poblar)
        fila.addWidget(self.chk_mostrar)
        self.btn_analizar = QPushButton("🔄 Analizar")
        self.btn_analizar.setObjectName("primary")
        self.btn_analizar.clicked.connect(self.analizar)
        fila.addWidget(self.btn_analizar)
        layout.addLayout(fila)

        if not self._perfiles:
            sin = QLabel("No se encontró ningún perfil de Chrome o Edge en este equipo.")
            sin.setObjectName("hintLabel")
            layout.addWidget(sin)

        self.lbl_resumen = QLabel("Elige un perfil y pulsa «Analizar».")
        self.lbl_resumen.setObjectName("fieldLabel")
        self.lbl_resumen.setWordWrap(True)
        layout.addWidget(self.lbl_resumen)

        self.tabla = QTableWidget(0, len(_COLUMNAS))
        self.tabla.setHorizontalHeaderLabels(_COLUMNAS)
        self.tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tabla.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.setSortingEnabled(True)
        self.tabla.setAlternatingRowColors(True)
        self.tabla.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.tabla, 1)

        self.barra = QProgressBar()
        self.barra.setVisible(False)
        layout.addWidget(self.barra)

        bot = QHBoxLayout()
        self.btn_expo = QPushButton("🌐 Comprobar exposición (Have I Been Pwned)")
        self.btn_expo.clicked.connect(self.comprobar_exposicion)
        self.btn_expo.setEnabled(False)
        bot.addWidget(self.btn_expo)
        self.btn_csv = QPushButton("📤 CSV")
        self.btn_csv.clicked.connect(self._exportar_csv)
        self.btn_csv.setEnabled(False)
        bot.addWidget(self.btn_csv)
        self.btn_html = QPushButton("📤 HTML")
        self.btn_html.clicked.connect(self._exportar_html)
        self.btn_html.setEnabled(False)
        bot.addWidget(self.btn_html)
        bot.addStretch()
        self.btn_cerrar = QPushButton("Cerrar")
        self.btn_cerrar.clicked.connect(self.accept)
        bot.addWidget(self.btn_cerrar)
        layout.addLayout(bot)

    # ── analizar ─────────────────────────────────────────────────────
    def analizar(self):
        perfil = self.combo.currentData()
        if not perfil:
            return
        if not self._confirmar_descifrar(perfil):
            return
        self.lbl_resumen.setText("🔄 Analizando…")
        try:
            self._filas = pa.analizar(perfil)
        except Exception as e:
            self._avisar("Error", f"No se pudieron analizar las contraseñas:\n\n{e}", QMessageBox.Warning)
            return
        self._poblar()
        self._resumen()
        hay = bool(self._filas)
        self.btn_expo.setEnabled(hay)
        self.btn_csv.setEnabled(hay)
        self.btn_html.setEnabled(hay)

    def _poblar(self):
        self.tabla.setSortingEnabled(False)
        self.tabla.setRowCount(len(self._filas))
        for i, f in enumerate(self._filas):
            self._celda(i, 0, f.sitio)
            self._celda(i, 1, f.usuario)
            self._celda(i, 2, self._texto_pwd(f))
            it_fort = self._celda(i, 3, f.fortaleza)
            if f.fortaleza in _COLOR_FORT:
                it_fort.setForeground(QColor(_COLOR_FORT[f.fortaleza]))
            self._celda(i, 4, f"{f.reutilizada_en} sitios" if f.reutilizada_en else "")
            it_exp = self._celda(i, 5, _texto_expuesta(f.expuesta))
            if f.expuesta > 0:
                it_exp.setForeground(QColor("#dc2626"))
            self._celda(i, 6, f.motivos)
        self.tabla.setSortingEnabled(True)
        self.tabla.resizeColumnsToContents()

    def _celda(self, fila, col, texto):
        it = QTableWidgetItem(texto)
        self.tabla.setItem(fila, col, it)
        return it

    def _texto_pwd(self, f):
        if f.password is None:
            return "(no se pudo descifrar)"
        if f.password == "":
            return "(vacía)"
        return f.password if self.chk_mostrar.isChecked() else "•" * 10

    def _resumen(self):
        r = pa.resumen(self._filas)
        partes = [f"{r['total']} contraseñas", f"{r['debiles']} débiles", f"{r['reutilizadas']} reutilizadas"]
        if any(f.expuesta != pa.NO_COMPROBADA for f in self._filas):
            partes.append(f"{r['expuestas']} expuestas")
        if r["sin_descifrar"]:
            partes.append(f"{r['sin_descifrar']} sin descifrar")
        self.lbl_resumen.setText("🛡️ " + " · ".join(partes))

    # ── exposición HIBP ──────────────────────────────────────────────
    def comprobar_exposicion(self):
        if not self._filas:
            return
        self._ocupado(True)
        self.barra.setVisible(True)
        self.barra.setRange(0, 0)  # indeterminada: la comprobación ocurre en el hilo trabajador
        self.lbl_resumen.setText("🌐 Comprobando en Have I Been Pwned…")
        self._tarea = tareas.lanzar(
            self, "_tarea", pa.comprobar_expuestas, self._filas,
            al_terminar=self._expo_listo, al_fallar=self._expo_error)

    def _expo_listo(self, _filas):
        self.barra.setVisible(False)
        self._ocupado(False)
        self._poblar()
        self._resumen()

    def _expo_error(self, msg):
        self.barra.setVisible(False)
        self._ocupado(False)
        self._avisar("Comprobar exposición", f"No se pudo consultar:\n\n{msg}", QMessageBox.Warning)

    # ── exportar (respeta «Mostrar en claro») ────────────────────────
    def _filas_export(self):
        return [[f.sitio, f.usuario, self._texto_pwd(f), f.fortaleza,
                 f"{f.reutilizada_en} sitios" if f.reutilizada_en else "",
                 _texto_expuesta(f.expuesta), f.motivos] for f in self._filas]

    def _exportar_csv(self):
        if not self._filas or not self._confirmar_exportar():
            return
        nombre = f"AuditoriaContrasenas_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        ruta = dialogo_guardar_archivo(self, "Guardar CSV", ruta_guardado_predeterminada(nombre), "CSV (*.csv)")
        if not ruta:
            return
        try:
            with open(ruta, "w", newline="", encoding="utf-8-sig") as fh:
                w = _SafeCsvWriter(csv.writer(fh, delimiter=";"))
                w.writerow(_COLUMNAS)
                for fila in self._filas_export():
                    w.writerow(fila)
            self._avisar("Éxito", f"Exportado a:\n\n{ruta}")
        except OSError as e:
            self._avisar("Error", f"No se pudo exportar:\n\n{e}", QMessageBox.Critical)

    def _exportar_html(self):
        if not self._filas or not self._confirmar_exportar():
            return
        nombre = f"AuditoriaContrasenas_{time.strftime('%Y%m%d_%H%M%S')}.html"
        ruta = dialogo_guardar_archivo(self, "Guardar HTML", ruta_guardado_predeterminada(nombre), "HTML (*.html)")
        if not ruta:
            return
        try:
            doc = informe_html.documento("Auditoría de contraseñas", [
                informe_html.Seccion("Contraseñas", _COLUMNAS, self._filas_export(), columnas_nivel=[3, 5])], indice=False)
            informe_html.guardar(ruta, doc)
            self._avisar("Éxito", f"Exportado a:\n\n{ruta}")
        except OSError as e:
            self._avisar("Error", f"No se pudo exportar:\n\n{e}", QMessageBox.Critical)

    # ── confirmaciones ───────────────────────────────────────────────
    def _confirmar_descifrar(self, perfil):
        box = QMessageBox(self)
        box.setWindowFlags(box.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        box.setWindowTitle("Descifrar contraseñas")
        box.setIcon(QMessageBox.Warning)
        box.setText(f"Para auditarlas hay que descifrar las contraseñas guardadas en {perfil.navegador} "
                    f"({perfil.nombre_perfil}).\n\nSe muestran ocultas; actívalas con «Mostrar en claro» solo si "
                    "es necesario.\n\n¿Continuar?")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        return box.exec_() == QMessageBox.Yes

    def _confirmar_exportar(self):
        box = QMessageBox(self)
        box.setWindowFlags(box.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        box.setWindowTitle("Exportar auditoría")
        box.setIcon(QMessageBox.Warning)
        oculto = not self.chk_mostrar.isChecked()
        box.setText("Vas a exportar el resultado a un archivo. " + (
            "Las contraseñas se exportarán OCULTAS (activa «Mostrar en claro» antes si necesitas el valor)." if oculto
            else "Las contraseñas se exportarán EN CLARO tal y como se ven ahora.") + "\n\n¿Continuar?")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        return box.exec_() == QMessageBox.Yes

    def _ocupado(self, on):
        for b in (self.btn_analizar, self.btn_expo, self.btn_csv, self.btn_html):
            b.setEnabled(not on)

    def _avisar(self, titulo, texto, icono=QMessageBox.Information):
        box = QMessageBox(self)
        box.setWindowFlags(box.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        box.setWindowTitle(titulo)
        box.setText(texto)
        box.setIcon(icono)
        box.exec_()


def abrir_auditoria_passwords(parent):
    AuditoriaPasswordsDialog(parent).exec_()
