# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña 🔑 Licencias — Windows y Office/365,
las DOS únicas categorías con un registro central fiable en Windows
(Software Protection Platform, SoftwareLicensingProduct). El software de
terceros (Adobe, antivirus...) no tiene un equivalente accesible de forma
fiable — cada fabricante guarda su licencia a su manera, y adivinarla
buscando texto en el registro es justo lo que se quitó de aquí (ver
PS_GET_LICENSE_INFO en msys/ps_scripts.py): antes se escaneaba el registro
de Office en busca de cualquier email y se etiquetaba como "licencia
Office 365" — daba falsos positivos reales (cuentas de Gmail añadidas a
Outlook por IMAP saliendo como licencias).

La cuenta real de Office SÍ se resuelve de forma fiable (a diferencia del
escaneo anterior): Office mantiene su propio mapeo dedicado Id de
licencia→email (LicensingNext\\LicenseIdToEmailMapping), no hace falta
adivinar nada — ver PS_GET_LICENSE_INFO.

Paneles grandes a propósito (no las fichas compactas de _crear_ficha_
sistema): con solo 2 elementos, un grid de fichas pequeñas dejaba media
pantalla vacía y el texto ilegible — aquí cada panel ocupa la mitad del
ancho disponible y crece con la ventana.

Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).
Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

from datetime import datetime

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QMessageBox,
    QFrame, QTreeWidget, QTreeWidgetItem,
)

from msys.collectors import LicenseInfoCollector

_ESTADOS_SALUDABLES = {"Activado"}


def _formatear_fecha_iso(valor: str) -> str:
    """'2025-06-04T15:27:53Z' -> '04/06/2025' — si no se puede parsear
    (formato inesperado, 'N/A'...), se devuelve tal cual en vez de ocultarlo."""
    if not valor or valor == "N/A":
        return "N/A"
    try:
        return datetime.strptime(valor.split("T")[0], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return valor


class LicenciasMixin:
    def crear_tab_licencias(self):
        """Crea la pestaña de Licencias."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(10)
        layout.setContentsMargins(14, 14, 14, 14)

        # Cabecera: UNA fila, mismo lenguaje visual que el resto de la app
        # (hostname + resto de acciones + Actualizar a la derecha) — sin
        # búsqueda: solo dos paneles de estado, nada que filtrar.
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)

        import socket
        hostname = socket.gethostname()
        lbl_hostname = QLabel(f"🖥️ {hostname}")
        lbl_hostname.setObjectName("hostnameLabel")
        actions_layout.addWidget(lbl_hostname)

        actions_layout.addStretch()

        self.btn_refresh_licencias = QPushButton("🔄 Actualizar")
        self.btn_refresh_licencias.setObjectName("primary")
        self.btn_refresh_licencias.clicked.connect(self.refresh_licencias)
        actions_layout.addWidget(self.btn_refresh_licencias)

        layout.addLayout(actions_layout)

        lbl_alcance = QLabel(
            "ℹ️ Windows solo mantiene un registro central fiable de licencias para sus propios "
            "productos (Windows y Office/365) — el software de terceros no tiene un equivalente "
            "accesible con garantías, así que no se muestra aquí para no arriesgar un dato falso.")
        lbl_alcance.setObjectName("hintLabel")
        lbl_alcance.setWordWrap(True)
        layout.addWidget(lbl_alcance)

        self._licencias_paneles_layout = QHBoxLayout()
        self._licencias_paneles_layout.setSpacing(18)
        layout.addLayout(self._licencias_paneles_layout, 1)

        # Árbol OCULTO (nunca se añade al layout visible): esta pestaña
        # muestra paneles, no una tabla, pero _collect_all_sections_data /
        # _obtener_datos_seccion_actual (reporting.py) esperan un
        # QTreeWidget 'licencias_tree' de 2 columnas para poder incluir
        # Licencias en los informes HTML/PDF/Excel consolidados y en
        # CSV/HTML por sección — sin este árbol paralelo, Licencias
        # desaparecería en silencio de esos exportadores.
        self.licencias_tree = QTreeWidget(tab)
        self.licencias_tree.setHeaderLabels(["Componente", "Valor"])
        self.licencias_tree.setVisible(False)

        return tab

    def refresh_licencias(self):
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.set_status("🔄 Consultando estado de licencia (Windows y Office)...")
        self.btn_refresh_licencias.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.worker = LicenseInfoCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_licencias_data)
        self.worker.finished_error.connect(self._on_licencias_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _crear_panel_licencia(self, icono: str, titulo: str, estado_texto: str,
                               saludable: bool, filas: list) -> QFrame:
        """Panel grande (no ficha compacta): ocupa la mitad del ancho
        disponible y crece con la ventana — pensado para 1-2 paneles a la
        vez, no para un grid de muchos elementos pequeños."""
        c = self._dash_colors() if hasattr(self, "_dash_colors") else {
            "title": "#1a2634", "muted": "#8a94a6",
            "alerts_bg": "#f7f9fc", "alerts_border": "#e3e7ee",
        }
        color_estado = "#16a34a" if saludable else "#dc2626"

        panel = QFrame()
        panel.setObjectName("licenciaPanel")
        panel.setStyleSheet(f"""
            QFrame#licenciaPanel {{
                background: {c['alerts_bg']};
                border: 1px solid {c['alerts_border']};
                border-top: 5px solid {color_estado};
                border-radius: 12px;
            }}
            QFrame#licenciaPanel QLabel {{ background: transparent; border: none; }}
            QFrame#licenciaPanel QLabel#licBadge {{
                background: {color_estado}; color: white; border-radius: 11px;
                padding: 5px 16px; font-size: 11pt; font-weight: 700;
            }}
        """)
        v = QVBoxLayout(panel)
        v.setContentsMargins(30, 26, 30, 26)
        v.setSpacing(16)

        cabecera = QHBoxLayout()
        cabecera.setSpacing(16)
        lbl_icono = QLabel(icono)
        lbl_icono.setStyleSheet("font-size: 34pt;")
        cabecera.addWidget(lbl_icono)

        col_titulo = QVBoxLayout()
        col_titulo.setSpacing(8)
        lbl_titulo = QLabel(titulo)
        lbl_titulo.setStyleSheet(f"font-size: 17pt; font-weight: 700; color: {c['title']};")
        col_titulo.addWidget(lbl_titulo)
        badge = QLabel(("✅  " if saludable else "⚠️  ") + estado_texto)
        badge.setObjectName("licBadge")
        col_titulo.addWidget(badge, 0, Qt.AlignLeft)
        cabecera.addLayout(col_titulo, 1)
        v.addLayout(cabecera)

        separador = QFrame()
        separador.setFixedHeight(1)
        separador.setStyleSheet(f"background: {c['alerts_border']}; border: none;")
        v.addWidget(separador)

        for etiqueta, valor in filas:
            bloque = QVBoxLayout()
            bloque.setSpacing(3)
            lbl_e = QLabel(etiqueta.upper())
            lbl_e.setStyleSheet(f"font-size: 9pt; font-weight: 700; color: {c['muted']}; letter-spacing: 0.5px;")
            bloque.addWidget(lbl_e)
            lbl_v = QLabel(str(valor) if valor not in (None, "") else "N/A")
            lbl_v.setWordWrap(True)
            lbl_v.setStyleSheet(f"font-size: 13pt; color: {c['title']};")
            lbl_v.setTextInteractionFlags(Qt.TextSelectableByMouse)
            bloque.addWidget(lbl_v)
            v.addLayout(bloque)

        v.addStretch()
        return panel

    def _on_licencias_data(self, data: dict):
        self._last_licencias_info = data or {}   # 5.4.0: para el Dashboard
        self.btn_refresh_licencias.setEnabled(True)
        self.progress_bar.setVisible(False)

        while self._licencias_paneles_layout.count():
            item = self._licencias_paneles_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.licencias_tree.clear()

        # --- Windows ---
        estado_win = str(data.get('Estado', 'N/A'))
        saludable_win = estado_win in _ESTADOS_SALUDABLES
        filas_windows = [
            ("Producto", data.get('Producto', 'N/A')),
            ("Clave de producto", data.get('Clave', 'N/A')),
            ("Descripción", data.get('Descripcion', 'N/A')),
        ]
        panel_windows = self._crear_panel_licencia("🪟", "Windows", estado_win, saludable_win, filas_windows)
        self._licencias_paneles_layout.addWidget(panel_windows, 1)

        item_windows = QTreeWidgetItem(["Windows", ""])
        for etiqueta, valor in [("Estado", estado_win)] + filas_windows:
            item_windows.addChild(QTreeWidgetItem([etiqueta, str(valor)]))
        self.licencias_tree.addTopLevelItem(item_windows)

        # --- Office / Microsoft 365 ---
        office = data.get('Office') or {}
        detectado = bool(office.get('Detectado'))
        estado_office = str(office.get('Estado', 'N/A'))
        saludable_office = detectado and estado_office in _ESTADOS_SALUDABLES
        if detectado:
            fecha_legible = _formatear_fecha_iso(str(office.get('DesdeFecha', 'N/A')))
            filas_office = [
                ("Producto", office.get('Producto', 'N/A')),
                ("Cuenta vinculada", office.get('Cuenta', 'N/A')),
                ("SKU", office.get('Sku', 'N/A')),
                ("Clave parcial", office.get('ClaveParcial', 'N/A')),
                ("En este estado desde", fecha_legible),
            ]
            estado_mostrado = estado_office
        else:
            filas_office = [
                ("Detalle", "No se detectó ninguna licencia de Office/365 activada por Windows en este equipo"),
            ]
            estado_mostrado = "No detectado"
        panel_office = self._crear_panel_licencia(
            "📗", "Office / Microsoft 365", estado_mostrado, saludable_office, filas_office)
        self._licencias_paneles_layout.addWidget(panel_office, 1)

        item_office = QTreeWidgetItem(["Office / Microsoft 365", ""])
        for etiqueta, valor in [("Estado", estado_mostrado)] + filas_office:
            item_office.addChild(QTreeWidgetItem([etiqueta, str(valor)]))
        self.licencias_tree.addTopLevelItem(item_office)

        if saludable_win and (not detectado or saludable_office):
            self.set_status("✅ Estado de licencia obtenido")
        else:
            self.set_status("⚠️ Estado de licencia obtenido — revisa los avisos")

    def _on_licencias_error(self, msg):
        self.btn_refresh_licencias.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al obtener estado de licencia")
        self._avisar("Error", f"No se pudo obtener el estado de licencia:\n\n{msg}", QMessageBox.Warning)
