# -*- coding: utf-8 -*-
"""Diálogos de datos de navegador: marcadores, historial+descargas,
extensiones y contraseñas guardadas. Un único diálogo genérico
parametrizado por columnas + función de carga (msys/browser_data.py) —
las 4 vistas comparten exactamente la misma estructura (selector de
perfil, tabla, exportar CSV/HTML), solo cambian los datos.

Se instancian bajo demanda desde el menú/botones de la pestaña
Navegadores (NavegadoresNubeMixin) — diálogos autónomos, no dependen de
"self" de ShareInspector salvo por convención (parent._dash_colors(),
parent._avisar si existe)."""

import os
import csv
import time
from html import escape as html_escape

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QTableWidget, QTableWidgetItem, QMessageBox, QAbstractItemView,
    QCheckBox, QFileDialog,
)

from msys.core import informe_html
from msys.browser_data import enumerar_perfiles
from msys.core.utils import ruta_guardado_predeterminada, dialogo_guardar_archivo, _SafeCsvWriter


class BrowserDataDialog(QDialog):
    def __init__(self, parent, titulo: str, icono: str, subtitulo: str,
                 columnas, funcion_cargar, nombre_exportacion: str,
                 es_sensible: bool = False, aviso_sensible: str = ""):
        """columnas: lista de (encabezado, atributo_del_dataclass).
        funcion_cargar: PerfilNavegador -> list[dataclass]."""
        super().__init__(parent)
        self.setWindowTitle(titulo)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(760, 520)
        self.columnas = columnas
        self.funcion_cargar = funcion_cargar
        self.nombre_exportacion = nombre_exportacion
        self.es_sensible = es_sensible
        self._filas = []

        c = (parent._dash_colors() if parent and hasattr(parent, "_dash_colors") else {
            "title": "#1a2634", "muted": "#8a94a6",
        })

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(22, 20, 22, 18)

        cabecera = QHBoxLayout()
        cabecera.setSpacing(12)
        lbl_icono = QLabel(icono)
        lbl_icono.setStyleSheet("font-size: 26pt; background: transparent; border: none;")
        cabecera.addWidget(lbl_icono)
        col_titulo = QVBoxLayout()
        col_titulo.setSpacing(2)
        lbl_titulo = QLabel(titulo)
        lbl_titulo.setWordWrap(True)
        lbl_titulo.setStyleSheet(
            f"font-size: 13pt; font-weight: 600; color: {c['title']}; background: transparent; border: none;")
        col_titulo.addWidget(lbl_titulo)
        lbl_subtitulo = QLabel(subtitulo)
        lbl_subtitulo.setWordWrap(True)
        lbl_subtitulo.setStyleSheet(
            f"color: {c['muted']}; font-size: 9.5pt; background: transparent; border: none;")
        col_titulo.addWidget(lbl_subtitulo)
        cabecera.addLayout(col_titulo, 1)
        layout.addLayout(cabecera)

        if es_sensible and aviso_sensible:
            aviso = QLabel(aviso_sensible)
            aviso.setWordWrap(True)
            aviso.setObjectName("hintLabel")
            layout.addWidget(aviso)

        fila_perfil = QHBoxLayout()
        fila_perfil.addWidget(QLabel("Perfil:"))
        self.combo_perfil = QComboBox()
        self._perfiles = [p for p in enumerar_perfiles() if not (es_sensible and p.navegador == "Firefox")]
        for p in self._perfiles:
            self.combo_perfil.addItem(f"{p.navegador} — {p.nombre_perfil}", p)
        fila_perfil.addWidget(self.combo_perfil, 1)

        if es_sensible:
            self.chk_mostrar = QCheckBox("👁️ Mostrar en claro")
            self.chk_mostrar.toggled.connect(self._poblar_tabla)
            fila_perfil.addWidget(self.chk_mostrar)

        self.btn_cargar = QPushButton("🔄 Cargar")
        self.btn_cargar.setObjectName("primary")
        self.btn_cargar.clicked.connect(self._cargar)
        fila_perfil.addWidget(self.btn_cargar)
        layout.addLayout(fila_perfil)

        if not self._perfiles:
            aviso_sin_perfiles = QLabel("No se encontró ningún perfil de navegador compatible en este equipo.")
            aviso_sin_perfiles.setObjectName("hintLabel")
            layout.addWidget(aviso_sin_perfiles)

        self.lbl_estado = QLabel("Elige un perfil y pulsa «Cargar».")
        self.lbl_estado.setObjectName("fieldLabel")
        layout.addWidget(self.lbl_estado)

        self.tabla = QTableWidget(0, len(columnas))
        self.tabla.setHorizontalHeaderLabels([h for h, _ in columnas])
        self.tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tabla.setSelectionBehavior(QTableWidget.SelectRows)
        self.tabla.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.setSortingEnabled(True)
        self.tabla.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.tabla, 1)

        botones = QHBoxLayout()
        self.btn_csv = QPushButton("📤 Exportar CSV")
        self.btn_csv.setEnabled(False)
        self.btn_csv.clicked.connect(self._exportar_csv)
        botones.addWidget(self.btn_csv)
        self.btn_html = QPushButton("📤 Exportar HTML")
        self.btn_html.setEnabled(False)
        self.btn_html.clicked.connect(self._exportar_html)
        botones.addWidget(self.btn_html)
        botones.addStretch()
        self.btn_cerrar = QPushButton("Cerrar")
        self.btn_cerrar.clicked.connect(self.accept)
        botones.addWidget(self.btn_cerrar)
        layout.addLayout(botones)

    def _cargar(self):
        if not self._perfiles:
            return
        perfil = self.combo_perfil.currentData()

        if self.es_sensible:
            if not self._confirmar_carga_sensible(perfil):
                return

        self.lbl_estado.setText("🔄 Cargando...")
        self.btn_cargar.setEnabled(False)
        try:
            self._filas = self.funcion_cargar(perfil)
        except Exception as e:
            self._filas = []
            self._avisar_local("Error", f"No se pudo cargar:\n\n{e}", QMessageBox.Warning)
        finally:
            self.btn_cargar.setEnabled(True)

        self._poblar_tabla()
        total = len(self._filas)
        self.btn_csv.setEnabled(total > 0)
        self.btn_html.setEnabled(total > 0)
        self.lbl_estado.setText(f"✅ {total} elemento(s) cargado(s)" if total else "✅ No se encontró nada para este perfil.")

    def _confirmar_carga_sensible(self, perfil) -> bool:
        box = QMessageBox(self)
        box.setWindowFlags(box.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        box.setWindowTitle("Descifrar contraseñas guardadas")
        box.setIcon(QMessageBox.Warning)
        box.setText(
            f"Vas a descifrar las contraseñas reales guardadas en {perfil.navegador} ({perfil.nombre_perfil}).\n\n"
            "Se muestran ocultas por defecto — actívalas con «Mostrar en claro» solo si es necesario, "
            "y ten cuidado si compartes la pantalla o exportas el resultado.\n\n¿Continuar?")
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        return box.exec_() == QMessageBox.Yes

    def _valor_celda(self, fila, atributo):
        valor = getattr(fila, atributo, None)
        if self.es_sensible and atributo == "password":
            if valor is None:
                return "(no se pudo descifrar)"
            if valor == "":
                return "(vacía)"
            return valor if self.chk_mostrar.isChecked() else "•" * 10
        return "" if valor is None else str(valor)

    def _poblar_tabla(self):
        self.tabla.setSortingEnabled(False)
        self.tabla.setRowCount(len(self._filas))
        for i, fila in enumerate(self._filas):
            for j, (_header, atributo) in enumerate(self.columnas):
                self.tabla.setItem(i, j, QTableWidgetItem(self._valor_celda(fila, atributo)))
        self.tabla.setSortingEnabled(True)

    def _filas_para_exportar(self):
        """Para datos sensibles, exportar SIEMPRE respeta el estado actual
        de «Mostrar en claro» — si está oculto en pantalla, se exporta
        oculto (para no filtrar credenciales en un CSV/HTML sin que el
        usuario lo pidiera explícitamente marcando la casilla primero)."""
        return [[self._valor_celda(fila, atributo) for _h, atributo in self.columnas] for fila in self._filas]

    def _exportar_csv(self):
        if not self._filas:
            return
        if self.es_sensible and not self._confirmar_exportar_sensible():
            return
        default_name = f"{self.nombre_exportacion}_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        path = dialogo_guardar_archivo(self, "Guardar CSV", ruta_guardado_predeterminada(default_name), "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = _SafeCsvWriter(csv.writer(f, delimiter=";"))
                writer.writerow([h for h, _ in self.columnas])
                for fila in self._filas_para_exportar():
                    writer.writerow(fila)
            self._avisar_local("Éxito", f"Exportado a:\n\n{path}")
        except OSError as e:
            self._avisar_local("Error", f"No se pudo exportar:\n\n{e}", QMessageBox.Critical)

    def _exportar_html(self):
        if not self._filas:
            return
        if self.es_sensible and not self._confirmar_exportar_sensible():
            return
        default_name = f"{self.nombre_exportacion}_{time.strftime('%Y%m%d_%H%M%S')}.html"
        path = dialogo_guardar_archivo(self, "Guardar HTML", ruta_guardado_predeterminada(default_name), "HTML (*.html)")
        if not path:
            return
        try:
            headers = [h for h, _ in self.columnas]
            filas = self._filas_para_exportar()
            # Plantilla común de informes (búsqueda, filtros por columna, orden, CSV e imprimir)
            informe_html.guardar(path, informe_html.documento(
                self.nombre_exportacion, [informe_html.Seccion(self.nombre_exportacion, headers, filas)], indice=False))
            self._avisar_local("Éxito", f"Exportado a:\n\n{path}")
        except OSError as e:
            self._avisar_local("Error", f"No se pudo exportar:\n\n{e}", QMessageBox.Critical)

    def _confirmar_exportar_sensible(self) -> bool:
        box = QMessageBox(self)
        box.setWindowFlags(box.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        box.setWindowTitle("Exportar contraseñas")
        box.setIcon(QMessageBox.Warning)
        oculto = not (hasattr(self, "chk_mostrar") and self.chk_mostrar.isChecked())
        texto = ("Vas a exportar credenciales reales a un archivo en disco. " +
                 ("Se exportarán OCULTAS (activa «Mostrar en claro» antes de exportar si necesitas "
                  "el valor real)." if oculto else
                  "Se exportarán las contraseñas EN CLARO tal y como se ven ahora en pantalla.") +
                 "\n\n¿Continuar?")
        box.setText(texto)
        box.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        box.setDefaultButton(QMessageBox.No)
        return box.exec_() == QMessageBox.Yes

    def _avisar_local(self, titulo, texto, icono=QMessageBox.Information):
        box = QMessageBox(self)
        box.setWindowFlags(box.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        box.setWindowTitle(titulo)
        box.setText(texto)
        box.setIcon(icono)
        box.exec_()


def abrir_dialogo_marcadores(parent):
    from msys.browser_data import leer_marcadores
    dlg = BrowserDataDialog(
        parent, "Marcadores / Favoritos", "🔖",
        "Marcadores guardados en el navegador, por perfil",
        [("Carpeta", "carpeta"), ("Título", "titulo"), ("URL", "url")],
        leer_marcadores, "Marcadores",
    )
    dlg.exec_()


def abrir_dialogo_historial(parent):
    from msys.browser_data import leer_historial
    dlg = BrowserDataDialog(
        parent, "Historial de navegación", "📜",
        "Últimas 500 páginas visitadas, por perfil",
        [("Título", "titulo"), ("URL", "url"), ("Visitas", "visitas"), ("Última visita", "ultima_visita")],
        leer_historial, "Historial",
    )
    dlg.exec_()


def abrir_dialogo_descargas(parent):
    from msys.browser_data import leer_descargas
    dlg = BrowserDataDialog(
        parent, "Descargas recientes", "⬇️",
        "Últimas 200 descargas registradas, por perfil",
        [("Archivo", "nombre_archivo"), ("Ruta", "ruta"), ("Fecha", "fecha")],
        leer_descargas, "Descargas",
    )
    dlg.exec_()


def abrir_dialogo_extensiones(parent):
    from msys.browser_data import leer_extensiones
    dlg = BrowserDataDialog(
        parent, "Extensiones instaladas", "🧩",
        "Extensiones/complementos instalados, por perfil — revisa las que no reconozcas",
        [("Nombre", "nombre"), ("Versión", "version"), ("Habilitada", "habilitada"), ("ID", "id_extension")],
        leer_extensiones, "Extensiones",
    )
    dlg.exec_()


def abrir_dialogo_passwords(parent):
    from msys.browser_data import leer_passwords
    dlg = BrowserDataDialog(
        parent, "Contraseñas guardadas", "🔑",
        "Credenciales guardadas en Chrome/Edge, descifradas con la clave real del usuario actual",
        [("Sitio", "sitio"), ("Usuario", "usuario"), ("Contraseña", "password")],
        leer_passwords, "ContrasenasGuardadas",
        es_sensible=True,
        aviso_sensible="⚠️ Firefox no está soportado (requeriría reimplementar el cifrado NSS sin poder "
                       "probarlo en este equipo). Solo Chrome y Edge.",
    )
    dlg.exec_()
