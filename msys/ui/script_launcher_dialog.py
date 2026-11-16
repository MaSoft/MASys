# -*- coding: utf-8 -*-
"""Diálogo "🚀 Lanzar Scripts" — gestor de scripts .ps1 propios del técnico,
accesible desde Historial PS (msys/ui/ps_history.py). Diálogo autónomo (no
es un mixin): se instancia bajo demanda, como RecycleBinDialog/
TaskCalendarDialog.

Los scripts viven en la carpeta SCRIPTS\\ junto al ejecutable/proyecto
(msys_config.app_base_dir(), no __file__ de este módulo — ver su docstring)
con un catálogo propio en SCRIPTS\\catalogo.json (nombre, descripción,
parámetros y si necesita privilegios de administrador, por script).

Se ejecutan SIEMPRE con -ExecutionPolicy Bypass (evita que la política de
ejecución del sistema bloquee un script propio y de confianza) en una
consola de PowerShell VISIBLE nueva vía win32api.ShellExecute (verbo
'runas' si el script pide administrador) — el técnico ve la salida en vivo
y puede responder a cualquier Read-Host/confirmación del propio script; no
se captura la salida dentro de MASys a propósito."""

import json
import os
import shutil

import win32api

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QTextEdit,
    QCheckBox, QPushButton, QTableWidget, QTableWidgetItem, QDialogButtonBox,
    QMessageBox, QFormLayout,
)

from msys import config as msys_config
from msys.core.utils import dialogo_abrir_archivo, ruta_destino_sin_colision
from msys.widgets.delegates import _setup_header


def _carpeta_scripts() -> str:
    carpeta = os.path.join(msys_config.app_base_dir(), "SCRIPTS")
    os.makedirs(carpeta, exist_ok=True)
    return carpeta


def _ruta_catalogo() -> str:
    return os.path.join(_carpeta_scripts(), "catalogo.json")


def _cargar_catalogo() -> list:
    """Lista de dicts: {'archivo', 'nombre', 'descripcion', 'parametros', 'admin'}."""
    ruta = _ruta_catalogo()
    if not os.path.isfile(ruta):
        return []
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            datos = json.load(f)
        return datos if isinstance(datos, list) else []
    except (OSError, ValueError):
        return []


def _guardar_catalogo(entradas: list) -> None:
    with open(_ruta_catalogo(), "w", encoding="utf-8") as f:
        json.dump(entradas, f, ensure_ascii=False, indent=2)


class ScriptLauncherDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🚀 Lanzar Scripts")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(760, 460)
        self._entradas = _cargar_catalogo()

        v = QVBoxLayout(self)
        v.addWidget(QLabel(
            f"Scripts .ps1 propios, guardados en:\n{_carpeta_scripts()}\n\n"
            "Se ejecutan siempre con -ExecutionPolicy Bypass, en una consola nueva."))

        self.tabla = QTableWidget(0, 3)
        self.tabla.setHorizontalHeaderLabels(["Nombre", "Descripción", "Admin"])
        self.tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tabla.setSelectionBehavior(QTableWidget.SelectRows)
        self.tabla.setSelectionMode(QTableWidget.SingleSelection)
        self.tabla.verticalHeader().setVisible(False)
        _setup_header(self.tabla, 3, stretch_col=1)
        v.addWidget(self.tabla)

        fila_botones = QHBoxLayout()
        self.btn_ejecutar = QPushButton("▶️ Ejecutar seleccionado")
        self.btn_ejecutar.setObjectName("primary")
        self.btn_ejecutar.setEnabled(False)
        fila_botones.addWidget(self.btn_ejecutar)
        self.btn_modificar = QPushButton("✏️ Modificar")
        self.btn_modificar.setEnabled(False)
        fila_botones.addWidget(self.btn_modificar)
        self.btn_eliminar = QPushButton("✕ Eliminar")
        self.btn_eliminar.setEnabled(False)
        fila_botones.addWidget(self.btn_eliminar)
        fila_botones.addStretch()
        self.btn_anadir = QPushButton("➕ Añadir script...")
        fila_botones.addWidget(self.btn_anadir)
        # 5.7.2: «☁️ Microsoft 365» (antes en el menú lateral, bajo Móviles Android).
        self.btn_m365 = QPushButton("☁️ Microsoft 365")
        self.btn_m365.setToolTip("Herramientas para el inquilino de Microsoft 365 del cliente: cuentas, Exchange y SharePoint")
        self.btn_m365.clicked.connect(self._abrir_m365)
        fila_botones.addWidget(self.btn_m365)
        v.addLayout(fila_botones)

        self.tabla.itemSelectionChanged.connect(self._actualizar_botones)
        self.btn_anadir.clicked.connect(self._anadir_script)
        self.btn_modificar.clicked.connect(self._modificar_script)
        self.btn_eliminar.clicked.connect(self._eliminar_script)
        self.btn_ejecutar.clicked.connect(self._ejecutar_script)
        self.tabla.itemDoubleClicked.connect(lambda _: self._ejecutar_script())

        self._recargar_tabla()

        botones = QDialogButtonBox(QDialogButtonBox.Close)
        botones.rejected.connect(self.reject)
        botones.accepted.connect(self.accept)
        v.addWidget(botones)

    def _abrir_m365(self):
        # Modal sobre esta ventana (también modal); al cerrarla se vuelve a Lanzar Scripts.
        from msys.ui.m365_dialog import M365Dialog
        host = self.parent()
        try:
            M365Dialog(host if host is not None else self, parent=self).exec_()
        except Exception as e:
            QMessageBox.warning(self, "Microsoft 365", f"No se pudo abrir la ventana de Microsoft 365:\n\n{e}")

    def _actualizar_botones(self):
        hay_sel = bool(self.tabla.selectionModel().selectedRows())
        self.btn_ejecutar.setEnabled(hay_sel)
        self.btn_modificar.setEnabled(hay_sel)
        self.btn_eliminar.setEnabled(hay_sel)

    def _recargar_tabla(self):
        self.tabla.setSortingEnabled(False)
        self.tabla.setRowCount(len(self._entradas))
        for fila, entrada in enumerate(self._entradas):
            item_nombre = QTableWidgetItem(entrada.get("nombre", ""))
            item_nombre.setData(Qt.UserRole, entrada)
            self.tabla.setItem(fila, 0, item_nombre)
            self.tabla.setItem(fila, 1, QTableWidgetItem(entrada.get("descripcion", "")))
            self.tabla.setItem(fila, 2, QTableWidgetItem("Sí" if entrada.get("admin") else "No"))
        self.tabla.setSortingEnabled(True)
        self.tabla.model().layoutChanged.emit()
        self._actualizar_botones()

    def _entrada_seleccionada(self):
        filas = self.tabla.selectionModel().selectedRows()
        if not filas:
            return None
        return self.tabla.item(filas[0].row(), 0).data(Qt.UserRole)

    def _set_status_padre(self, msg: str):
        parent = self.parent()
        if parent is not None and hasattr(parent, "set_status"):
            parent.set_status(msg)

    # ── Añadir / Modificar / Eliminar ────────────────────────────────────

    def _anadir_script(self):
        carpeta = _carpeta_scripts()
        ruta_origen = dialogo_abrir_archivo(self, "Elegir script PowerShell", carpeta, "Scripts PowerShell (*.ps1)")
        if not ruta_origen:
            return

        # Si el .ps1 elegido ya vive dentro de SCRIPTS\, se cataloga tal
        # cual (sin copiarlo ni renombrarlo) — evita que ruta_destino_sin_colision
        # lo trate como una colisión consigo mismo y genere un "(2)" absurdo.
        ya_en_carpeta = os.path.normcase(os.path.abspath(os.path.dirname(ruta_origen))) == \
            os.path.normcase(os.path.abspath(carpeta))
        if ya_en_carpeta:
            ruta_destino = ruta_origen
        else:
            ruta_destino = ruta_destino_sin_colision(carpeta, os.path.basename(ruta_origen))
            try:
                shutil.copy2(ruta_origen, ruta_destino)
            except OSError as e:
                QMessageBox.warning(self, "Añadir script", f"No se pudo copiar el script a la carpeta SCRIPTS:\n\n{e}")
                return

        datos = self._dialogo_metadatos("Añadir script", {
            "nombre": os.path.splitext(os.path.basename(ruta_destino))[0],
            "descripcion": "", "parametros": "", "admin": False,
        })
        if datos is None:
            # El archivo ya se copió (o ya estaba ahí) pero sin entrada en
            # el catálogo — mejor eso que perder la copia si se reintenta.
            self._set_status_padre("Añadir script cancelado (el archivo queda en SCRIPTS\\ sin catalogar)")
            return

        datos["archivo"] = os.path.basename(ruta_destino)
        self._entradas.append(datos)
        _guardar_catalogo(self._entradas)
        self._recargar_tabla()
        self._set_status_padre(f"✅ Script «{datos['nombre']}» añadido")

    def _modificar_script(self):
        entrada = self._entrada_seleccionada()
        if entrada is None:
            return
        # Qt.UserRole no preserva identidad de objeto para un dict Python
        # (data() puede devolver una copia) — se localiza por VALOR el
        # índice real en self._entradas ANTES de tocar nada, y se actualiza
        # ese dict, no la copia que acabamos de leer.
        try:
            idx = self._entradas.index(entrada)
        except ValueError:
            return
        datos = self._dialogo_metadatos("Modificar script", entrada)
        if datos is None:
            return
        self._entradas[idx].update(datos)
        _guardar_catalogo(self._entradas)
        self._recargar_tabla()
        self._set_status_padre(f"✅ Script «{self._entradas[idx]['nombre']}» actualizado")

    def _eliminar_script(self):
        entrada = self._entrada_seleccionada()
        if entrada is None:
            return
        ruta = os.path.join(_carpeta_scripts(), entrada.get("archivo", ""))
        resp = QMessageBox.warning(
            self, "Eliminar script",
            f"¿Eliminar «{entrada.get('nombre')}»?\n\nTambién se borrará el archivo:\n{ruta}\n\n"
            "Esta acción no es reversible.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if resp != QMessageBox.Yes:
            return
        try:
            if os.path.isfile(ruta):
                os.remove(ruta)
        except OSError as e:
            QMessageBox.warning(self, "Eliminar script", f"No se pudo borrar el archivo:\n\n{e}")
        self._entradas.remove(entrada)
        _guardar_catalogo(self._entradas)
        self._recargar_tabla()
        self._set_status_padre(f"✅ Script «{entrada.get('nombre')}» eliminado")

    # ── Ejecutar ──────────────────────────────────────────────────────────

    def _ejecutar_script(self):
        entrada = self._entrada_seleccionada()
        if entrada is None:
            return
        ruta = os.path.join(_carpeta_scripts(), entrada.get("archivo", ""))
        if not os.path.isfile(ruta):
            QMessageBox.warning(self, "Ejecutar script", f"El archivo ya no existe:\n\n{ruta}")
            return

        parametros = (entrada.get("parametros") or "").strip()
        args = f'-NoExit -ExecutionPolicy Bypass -File "{ruta}"'
        if parametros:
            args += f" {parametros}"

        try:
            win32api.ShellExecute(
                0, "runas" if entrada.get("admin") else "open",
                "powershell.exe", args, None, 1)
            self._set_status_padre(f"🚀 Ejecutando «{entrada.get('nombre')}»...")
        except Exception as e:
            QMessageBox.warning(self, "Ejecutar script", f"No se pudo lanzar el script:\n\n{e}")

    # ── Formulario de metadatos (compartido añadir/modificar) ──────────────

    def _dialogo_metadatos(self, titulo: str, valores: dict):
        """Nombre/Descripción/Parámetros/Admin. Devuelve un dict con los
        nuevos valores, o None si se cancela."""
        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle(titulo)
        dialog.setMinimumWidth(460)
        v = QVBoxLayout(dialog)

        form = QFormLayout()
        txt_nombre = QLineEdit(valores.get("nombre", ""))
        form.addRow("Nombre:", txt_nombre)
        txt_descripcion = QTextEdit(valores.get("descripcion", ""))
        txt_descripcion.setMaximumHeight(70)
        form.addRow("Descripción:", txt_descripcion)
        txt_parametros = QLineEdit(valores.get("parametros", ""))
        txt_parametros.setPlaceholderText("p. ej.: -Nombre 'Equipo1' -Force")
        form.addRow("Parámetros:", txt_parametros)
        chk_admin = QCheckBox("Ejecutar como administrador")
        chk_admin.setChecked(bool(valores.get("admin", False)))
        form.addRow("", chk_admin)
        v.addLayout(form)

        botones = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        botones.accepted.connect(dialog.accept)
        botones.rejected.connect(dialog.reject)
        v.addWidget(botones)

        if dialog.exec_() != QDialog.Accepted:
            return None

        nombre = txt_nombre.text().strip() or valores.get("nombre") or "Script sin nombre"
        return {
            "nombre": nombre,
            "descripcion": txt_descripcion.toPlainText().strip(),
            "parametros": txt_parametros.text().strip(),
            "admin": chk_admin.isChecked(),
        }
