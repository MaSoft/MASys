# -*- coding: utf-8 -*-
"""Diálogo "Detectar redes cercanas" — escanea (netsh wlan show networks,
solo lectura) y permite conectar a cualquier red visible, tenga o no
perfil ya guardado. Diálogo autónomo, se instancia bajo demanda desde
WiFiMixin._wifi_detectar_redes_cercanas."""

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QMessageBox, QAbstractItemView, QInputDialog, QLineEdit,
    QProgressBar,
)

from msys.collectors import WiFiScanThread
from msys.core.utils import (
    wifi_obtener_interfaz, wifi_conectar_a_perfil_existente,
    wifi_crear_perfil_y_conectar, wifi_mapear_seguridad_a_perfil,
)
from msys.widgets.delegates import _setup_header


class WiFiScanDialog(QDialog):
    def __init__(self, nombres_con_perfil: set, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Detectar redes cercanas")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(640, 440)
        self._nombres_con_perfil = nombres_con_perfil
        self._thread = None

        c = (parent._dash_colors() if parent and hasattr(parent, "_dash_colors") else {
            "title": "#1a2634", "muted": "#8a94a6",
        })

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(22, 20, 22, 18)

        # ── Cabecera: icono + título + subtítulo, mismo patrón que
        # _confirmar_reparacion — no un bloque de texto plano.
        cabecera = QHBoxLayout()
        cabecera.setSpacing(12)
        lbl_icono = QLabel("🔍")
        lbl_icono.setStyleSheet("font-size: 26pt; background: transparent; border: none;")
        cabecera.addWidget(lbl_icono)
        col_titulo = QVBoxLayout()
        col_titulo.setSpacing(2)
        lbl_titulo = QLabel("Detectar redes cercanas")
        lbl_titulo.setWordWrap(True)
        lbl_titulo.setStyleSheet(
            f"font-size: 13pt; font-weight: 600; color: {c['title']}; background: transparent; border: none;")
        col_titulo.addWidget(lbl_titulo)
        lbl_subtitulo = QLabel("Redes WiFi visibles ahora mismo — elige una para conectarte")
        lbl_subtitulo.setWordWrap(True)
        lbl_subtitulo.setStyleSheet(
            f"color: {c['muted']}; font-size: 9.5pt; background: transparent; border: none;")
        col_titulo.addWidget(lbl_subtitulo)
        cabecera.addLayout(col_titulo, 1)
        layout.addLayout(cabecera)

        self.lbl_estado = QLabel("🔄 Escaneando...")
        self.lbl_estado.setObjectName("fieldLabel")
        layout.addWidget(self.lbl_estado)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        layout.addWidget(self.progress)

        self.tabla = QTableWidget(0, 4)
        self.tabla.setHorizontalHeaderLabels(["Red", "Seguridad", "Señal", "Estado"])
        self.tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tabla.setSelectionBehavior(QTableWidget.SelectRows)
        self.tabla.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.setSortingEnabled(True)
        _setup_header(self.tabla, 4, stretch_col=0)
        layout.addWidget(self.tabla, 1)

        botones = QHBoxLayout()
        self.btn_actualizar = QPushButton("🔄 Volver a escanear")
        self.btn_actualizar.clicked.connect(self._iniciar)
        botones.addWidget(self.btn_actualizar)
        botones.addStretch()
        self.btn_conectar = QPushButton("📶 Conectar")
        self.btn_conectar.setEnabled(False)
        self.btn_conectar.clicked.connect(self._conectar_seleccionada)
        botones.addWidget(self.btn_conectar)
        self.btn_cerrar = QPushButton("Cerrar")
        self.btn_cerrar.clicked.connect(self.accept)
        botones.addWidget(self.btn_cerrar)
        layout.addLayout(botones)

        self.tabla.itemSelectionChanged.connect(
            lambda: self.btn_conectar.setEnabled(bool(self.tabla.selectionModel().selectedRows())))

        QTimer.singleShot(0, self._iniciar)

    def _iniciar(self):
        if self._thread is not None and self._thread.isRunning():
            return
        self.lbl_estado.setText("🔄 Escaneando...")
        self.progress.setVisible(True)
        self.btn_actualizar.setEnabled(False)
        self.btn_conectar.setEnabled(False)
        self.tabla.setRowCount(0)

        self._thread = WiFiScanThread()
        self._thread.finished_ok.connect(self._on_escaneado)
        self._thread.finished_error.connect(self._on_error)
        self._thread.start()

    def _on_escaneado(self, redes: list):
        self.progress.setVisible(False)
        self.btn_actualizar.setEnabled(True)

        self.tabla.setSortingEnabled(False)
        self.tabla.setRowCount(len(redes))
        for fila_idx, red in enumerate(redes):
            item_nombre = QTableWidgetItem(red["ssid"])
            item_nombre.setData(Qt.UserRole, red)
            self.tabla.setItem(fila_idx, 0, item_nombre)
            self.tabla.setItem(fila_idx, 1, QTableWidgetItem(red["autenticacion"]))
            item_senal = QTableWidgetItem()
            item_senal.setData(Qt.DisplayRole, red["senal"])
            self.tabla.setItem(fila_idx, 2, item_senal)
            estado = "💾 Perfil guardado" if red["ssid"] in self._nombres_con_perfil else "🆕 Nueva"
            self.tabla.setItem(fila_idx, 3, QTableWidgetItem(estado))
        self.tabla.setSortingEnabled(True)

        if redes:
            self.lbl_estado.setText(f"✅ {len(redes)} red(es) detectada(s)")
        else:
            self.lbl_estado.setText("✅ No se detectó ninguna red (¿WiFi desactivado?)")

    def _on_error(self, mensaje):
        self.progress.setVisible(False)
        self.btn_actualizar.setEnabled(True)
        self.lbl_estado.setText(f"❌ {mensaje}")

    def _conectar_seleccionada(self):
        filas_sel = self.tabla.selectionModel().selectedRows()
        if not filas_sel:
            return
        red = self.tabla.item(filas_sel[0].row(), 0).data(Qt.UserRole)

        interfaz = wifi_obtener_interfaz()
        if not interfaz:
            self._avisar("Conectar", "No se encontró ninguna interfaz WiFi.", QMessageBox.Warning)
            return

        parent = self.parent()
        tiene_perfil = red["ssid"] in self._nombres_con_perfil
        auth_lower = red["autenticacion"].strip().lower()
        es_abierta = auth_lower in ("open", "abierta", "ninguna", "none", "")

        if not tiene_perfil:
            authentication, _ = wifi_mapear_seguridad_a_perfil(red["autenticacion"])
            if authentication is None:
                self._avisar("Conectar", "Esta red usa seguridad Enterprise/802.1X (certificado o credenciales "
                    "de dominio) — no se puede crear el perfil solo con una clave. "
                    "Conéctate desde la configuración de Windows.")
                return

        password = ""
        if not tiene_perfil and not es_abierta:
            dlg_pwd = QInputDialog(self)
            dlg_pwd.setWindowFlags(dlg_pwd.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            dlg_pwd.setWindowTitle("Contraseña de la red")
            dlg_pwd.setLabelText(f"Contraseña de «{red['ssid']}»:")
            dlg_pwd.setTextEchoMode(QLineEdit.Password)
            aceptado = dlg_pwd.exec_() == QDialog.Accepted
            password = dlg_pwd.textValue()
            if not aceptado:
                return
            if not password:
                self._avisar("Conectar", "Introduce una contraseña.")
                return

        if parent and hasattr(parent, "_confirmar_reparacion"):
            cambios = ([f"Conectar a «{red['ssid']}» usando el perfil ya guardado"] if tiene_perfil
                       else [f"Crear un perfil nuevo para «{red['ssid']}» y conectar"])
            if not parent._confirmar_reparacion(
                "Conectar a red WiFi", cambios=cambios, reversible=True, objetivo=red["ssid"],
            ):
                return

        if tiene_perfil:
            ok, mensaje = wifi_conectar_a_perfil_existente(red["ssid"], interfaz)
        else:
            ok, mensaje = wifi_crear_perfil_y_conectar(red["ssid"], password, red["autenticacion"], interfaz)

        if parent and hasattr(parent, "repair_audit_db"):
            parent.repair_audit_db.registrar(
                seccion="📶 WiFi", accion="Conectar a red WiFi",
                objetivo=red["ssid"], exito=ok, detalle=mensaje,
            )

        if ok:
            self._avisar("Conectar", f"Conectado a «{red['ssid']}».")
            if parent and hasattr(parent, "refresh_wifi"):
                parent.refresh_wifi()
            self.accept()
        else:
            self._avisar("Conectar", f"No se pudo conectar:\n\n{mensaje}", QMessageBox.Warning)

    def closeEvent(self, event):
        if self._thread is not None and self._thread.isRunning():
            self._thread.wait(3000)
        event.accept()
