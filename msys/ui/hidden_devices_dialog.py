# -*- coding: utf-8 -*-
"""Diálogo "Dispositivos ocultos/fantasma" — hardware que estuvo conectado
alguna vez y ya no lo está (o cuyo controlador sigue instalado sin que el
dispositivo esté presente), equivalente a "Ver > Mostrar dispositivos
ocultos" en el Administrador de dispositivos nativo. Útil para depurar
puertos USB duplicados o limpiar entradas fantasma acumuladas.

Se instancia bajo demanda desde el botón "🛠️ Soporte" de la sección
"🖥️ Adm. Dispositivos" — ver SoporteMixin._soporte_controladores_ocultos en
soporte.py, mismo sitio donde vive el resto de acciones clásicas.
Los datos vienen de pnputil /enum-devices /disconnected /format csv —
cabeceras Y VALORES en inglés fijo (verificado en vivo), no del texto
localizado, así que el parseo con csv.DictReader es fiable en cualquier
idioma de Windows."""

import csv
import io
import time

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QMessageBox, QAbstractItemView, QMenu, QApplication,
)

from msys.core.utils import ruta_guardado_predeterminada, dialogo_guardar_archivo, _SafeCsvWriter

# Traducción best-effort de la columna Status de pnputil: valores
# documentados/observados con más frecuencia. pnputil no tiene un enum
# oficial cerrado de valores para este campo, así que lo no mapeado se deja
# tal cual (mejor mostrar el original en inglés que una traducción
# inventada) — si aparece alguno nuevo, añadirlo aquí.
_PNPUTIL_STATUS_ES = {
    "started": "Iniciado",
    "stopped": "Detenido",
    "disabled": "Deshabilitado",
    "error": "Error",
    "unknown": "Desconocido",
}


def _traducir_status_pnputil(valor: str) -> str:
    if not valor:
        return "Desconocido"
    return _PNPUTIL_STATUS_ES.get(valor.strip().lower(), valor)


class HiddenDevicesDialog(QDialog):
    HEADERS = ["Descripción", "Clase", "Fabricante", "Estado", "Controlador", "ID de instancia"]

    def __init__(self, parent, driver_manager):
        super().__init__(parent)
        self.driver_manager = driver_manager
        self.setWindowTitle("Dispositivos ocultos / fantasma")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(760, 480)
        self._filas = []

        c = (parent._dash_colors() if parent and hasattr(parent, "_dash_colors") else {
            "title": "#1a2634", "muted": "#8a94a6",
        })

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(22, 20, 22, 18)

        cabecera = QHBoxLayout()
        cabecera.setSpacing(12)
        lbl_icono = QLabel("👻")
        lbl_icono.setStyleSheet("font-size: 26pt; background: transparent; border: none;")
        cabecera.addWidget(lbl_icono)
        col_titulo = QVBoxLayout()
        col_titulo.setSpacing(2)
        lbl_titulo = QLabel("Dispositivos ocultos / fantasma")
        lbl_titulo.setStyleSheet(f"font-size: 13pt; font-weight: 600; color: {c['title']}; background: transparent; border: none;")
        col_titulo.addWidget(lbl_titulo)
        lbl_subtitulo = QLabel("Hardware que estuvo conectado y ya no está presente — clic derecho para quitarlo")
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
        self.tabla.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabla.customContextMenuRequested.connect(self._ctx_menu)
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
        self.btn_actualizar.setEnabled(False)
        QApplication.processEvents()

        ok, salida = self.driver_manager.enum_disconnected_devices_csv()
        self.btn_actualizar.setEnabled(True)

        if not ok:
            self.lbl_estado.setText(f"❌ {salida}")
            return

        self._filas = []
        try:
            lector = csv.DictReader(io.StringIO(salida))
            for fila in lector:
                self._filas.append(fila)
        except csv.Error as e:
            self.lbl_estado.setText(f"❌ No se pudo interpretar la respuesta: {e}")
            return

        self.tabla.setSortingEnabled(False)
        self.tabla.setRowCount(len(self._filas))
        for i, fila in enumerate(self._filas):
            valores = [
                fila.get("DeviceDescription", ""), fila.get("ClassName", ""),
                fila.get("ManufacturerName", ""), _traducir_status_pnputil(fila.get("Status", "")),
                fila.get("DriverName", ""), fila.get("InstanceId", ""),
            ]
            for col, val in enumerate(valores):
                self.tabla.setItem(i, col, QTableWidgetItem(val))
        self.tabla.setSortingEnabled(True)

        self.lbl_estado.setText(f"✅ {len(self._filas)} dispositivo(s) oculto(s) encontrado(s)")

    def _fila_seleccionada(self):
        sel = self.tabla.selectionModel()
        if not sel or not sel.hasSelection():
            return None
        fila = sel.selectedRows()[0].row()
        if 0 <= fila < len(self._filas):
            return self._filas[fila]
        return None

    def _ctx_menu(self, pos):
        idx = self.tabla.indexAt(pos)
        if not idx.isValid():
            return
        self.tabla.selectRow(idx.row())
        fila = self._fila_seleccionada()
        if not fila:
            return

        menu = QMenu(self)
        act_quitar = menu.addAction("✕ Quitar dispositivo")
        menu.addSeparator()
        act_copiar = menu.addAction("📋 Copiar fila")
        action = menu.exec_(self.tabla.viewport().mapToGlobal(pos))

        if action == act_quitar:
            self._quitar_dispositivo(fila)
        elif action == act_copiar:
            valores = [self.tabla.item(idx.row(), c).text() for c in range(len(self.HEADERS))]
            QApplication.clipboard().setText("\t".join(valores))

    def _quitar_dispositivo(self, fila):
        instance_id = fila.get("InstanceId", "")
        descripcion = fila.get("DeviceDescription", instance_id)
        if not instance_id:
            return

        parent = self.parent()
        if parent and hasattr(parent, "_confirmar_accion"):
            if not parent._confirmar_accion(
                "Quitar dispositivo oculto",
                f"¿Quitar «{descripcion}» del sistema?\n\n"
                "Es un dispositivo que ya no está conectado — quitarlo solo limpia el registro "
                "de Windows, no afecta a ningún hardware presente.",
                icono=QMessageBox.Warning,
            ):
                return

        ok, msg = self.driver_manager.remove_device(instance_id, force=True)
        if parent and hasattr(parent, "repair_audit_db"):
            parent.repair_audit_db.registrar(
                seccion="🖥️ Adm. Dispositivos", accion="Quitar dispositivo oculto",
                objetivo=f"{descripcion} ({instance_id})", exito=ok, detalle=msg,
            )
        if ok:
            self.lbl_estado.setText(f"✅ «{descripcion}» quitado")
            self._cargar()
        else:
            QMessageBox.warning(self, "Con avisos", f"No se pudo quitar el dispositivo:\n\n{msg}")

    def _exportar_csv(self):
        if not self._filas:
            return
        default_name = f"DispositivosOcultos_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        path = dialogo_guardar_archivo(self, "Guardar CSV", ruta_guardado_predeterminada(default_name), "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = _SafeCsvWriter(csv.writer(f, delimiter=";"))
                writer.writerow(self.HEADERS)
                for r in range(self.tabla.rowCount()):
                    writer.writerow([self.tabla.item(r, c).text() for c in range(len(self.HEADERS))])
            QMessageBox.information(self, "Éxito", f"Exportado a:\n{path}")
        except OSError as e:
            QMessageBox.critical(self, "Error", f"No se pudo exportar:\n\n{e}")
