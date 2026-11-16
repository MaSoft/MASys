# -*- coding: utf-8 -*-
"""Diálogo "Cambiar resolución de pantalla" — cada monitor físicamente
conectado se puede cambiar de forma INDEPENDIENTE (ChangeDisplaySettingsEx
con un DeviceName concreto, '\\\\.\\DISPLAYn', solo afecta a esa pantalla).
Incluye el mismo mecanismo de seguridad que usa la propia Configuración de
pantalla de Windows: tras aplicar, cuenta atrás de 15s pidiendo confirmar
— si no se confirma a tiempo, revierte sola al valor anterior, para no
dejar un monitor en negro/inservible sin darle tiempo al técnico a decidir.

Se instancia bajo demanda desde MonitoresMixin._abrir_cambiar_resolucion
en monitors.py."""

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QMessageBox, QFrame,
)

from msys.collectors import enumerar_pantallas, cambiar_resolucion_pantalla


class DisplayResolutionDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Cambiar resolución de pantalla")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(560)

        c = (parent._dash_colors() if parent and hasattr(parent, "_dash_colors") else {
            "title": "#1a2634", "muted": "#8a94a6",
        })
        self._colores = c

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(22, 20, 22, 18)

        cabecera = QHBoxLayout()
        cabecera.setSpacing(12)
        lbl_icono = QLabel("🖥️")
        lbl_icono.setStyleSheet("font-size: 26pt; background: transparent; border: none;")
        cabecera.addWidget(lbl_icono)
        col_titulo = QVBoxLayout()
        col_titulo.setSpacing(2)
        lbl_titulo = QLabel("Cambiar resolución de pantalla")
        lbl_titulo.setStyleSheet(
            f"font-size: 13pt; font-weight: 600; color: {c['title']}; background: transparent; border: none;")
        col_titulo.addWidget(lbl_titulo)
        lbl_subtitulo = QLabel(
            "Cada pantalla se cambia de forma independiente, sin afectar a las demás. Si no "
            "confirmas en 15 segundos, se revierte sola."
        )
        lbl_subtitulo.setWordWrap(True)
        lbl_subtitulo.setStyleSheet(f"color: {c['muted']}; font-size: 9.5pt; background: transparent; border: none;")
        col_titulo.addWidget(lbl_subtitulo)
        cabecera.addLayout(col_titulo, 1)
        layout.addLayout(cabecera)

        self._filas_layout = QVBoxLayout()
        self._filas_layout.setSpacing(10)
        layout.addLayout(self._filas_layout)

        self._pantallas = enumerar_pantallas()
        if not self._pantallas:
            layout.addWidget(QLabel("No se ha podido detectar ninguna pantalla conectada."))
        for pantalla in self._pantallas:
            self._crear_fila_pantalla(pantalla)

        layout.addStretch()

        botones = QHBoxLayout()
        botones.addStretch()
        btn_cerrar = QPushButton("Cerrar")
        btn_cerrar.clicked.connect(self.reject)
        botones.addWidget(btn_cerrar)
        layout.addLayout(botones)

    def _crear_fila_pantalla(self, pantalla):
        c = self._colores
        marco = QFrame()
        marco.setFrameShape(QFrame.StyledPanel)
        v = QVBoxLayout(marco)
        v.setSpacing(6)

        lbl_nombre = QLabel(f"{pantalla['DeviceString']}  ({pantalla['DeviceName']})")
        lbl_nombre.setStyleSheet(f"font-weight: 600; color: {c['title']};")
        v.addWidget(lbl_nombre)

        actual = f"{pantalla['AnchoActual']} x {pantalla['AltoActual']} @ {pantalla['FrecuenciaActual']} Hz"
        lbl_actual = QLabel(f"Actual: {actual}")
        lbl_actual.setStyleSheet(f"color: {c['muted']}; font-size: 9.5pt;")
        v.addWidget(lbl_actual)

        fila = QHBoxLayout()
        combo = QComboBox()
        for ancho, alto, freq in pantalla["Modos"]:
            combo.addItem(f"{ancho} x {alto} @ {freq} Hz", (ancho, alto, freq))
        idx_actual = combo.findText(actual)
        if idx_actual >= 0:
            combo.setCurrentIndex(idx_actual)
        fila.addWidget(combo, 1)

        btn_aplicar = QPushButton("Aplicar")
        btn_aplicar.setObjectName("primary")
        btn_aplicar.clicked.connect(
            lambda: self._aplicar_resolucion(pantalla, combo, lbl_actual))
        fila.addWidget(btn_aplicar)
        v.addLayout(fila)

        self._filas_layout.addWidget(marco)

    def _aplicar_resolucion(self, pantalla, combo, lbl_actual):
        ancho, alto, freq = combo.currentData()
        ancho_anterior = pantalla["AnchoActual"]
        alto_anterior = pantalla["AltoActual"]
        freq_anterior = pantalla["FrecuenciaActual"]

        ok, msg = cambiar_resolucion_pantalla(pantalla["DeviceName"], ancho, alto, freq)
        if not ok:
            QMessageBox.warning(self, "No se pudo cambiar", f"No se pudo aplicar la resolución:\n\n{msg}")
            return

        parent = self.parent()
        if parent and hasattr(parent, "repair_audit_db"):
            parent.repair_audit_db.registrar(
                seccion="🖥️ Gráficas y Monitores", accion="Cambiar resolución",
                objetivo=f"{pantalla['DeviceString']} ({pantalla['DeviceName']})",
                exito=True, detalle=f"{ancho_anterior}x{alto_anterior}@{freq_anterior} -> {ancho}x{alto}@{freq}",
            )

        self._confirmar_o_revertir(pantalla, ancho, alto, freq, ancho_anterior, alto_anterior, freq_anterior, lbl_actual)

    def _confirmar_o_revertir(self, pantalla, ancho, alto, freq, ancho_prev, alto_prev, freq_prev, lbl_actual):
        segundos_restantes = [15]

        aviso = QMessageBox(self)
        aviso.setWindowFlags(aviso.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        aviso.setIcon(QMessageBox.Question)
        aviso.setWindowTitle("¿Mantener este cambio?")
        btn_mantener = aviso.addButton("Mantener cambios", QMessageBox.YesRole)
        aviso.addButton("Revertir ahora", QMessageBox.NoRole)

        def _actualizar_texto():
            aviso.setText(
                f"¿Mantener la nueva resolución ({ancho} x {alto} @ {freq} Hz) en «{pantalla['DeviceString']}»?\n\n"
                f"Se revertirá sola a {ancho_prev} x {alto_prev} @ {freq_prev} Hz en {segundos_restantes[0]}s "
                "si no respondes."
            )

        _actualizar_texto()

        temporizador = QTimer(aviso)
        temporizador.setInterval(1000)

        def _tick():
            segundos_restantes[0] -= 1
            if segundos_restantes[0] <= 0:
                temporizador.stop()
                aviso.reject()
                return
            _actualizar_texto()

        temporizador.timeout.connect(_tick)
        temporizador.start()

        aviso.exec_()
        temporizador.stop()

        if aviso.clickedButton() is btn_mantener:
            lbl_actual.setText(f"Actual: {ancho} x {alto} @ {freq} Hz")
            pantalla["AnchoActual"], pantalla["AltoActual"], pantalla["FrecuenciaActual"] = ancho, alto, freq
        else:
            ok, msg = cambiar_resolucion_pantalla(pantalla["DeviceName"], ancho_prev, alto_prev, freq_prev)
            if not ok:
                QMessageBox.warning(
                    self, "No se pudo revertir",
                    f"No se pudo volver a la resolución anterior automáticamente:\n\n{msg}\n\n"
                    "Cámbiala manualmente desde Configuración de pantalla de Windows.",
                )
