# -*- coding: utf-8 -*-
"""5.7.0 — Ventana «☁️ Microsoft 365»: las 16 herramientas de inquilino (Microsoft 365, Exchange Online, SharePoint).
No las ejecuta MASys: piden iniciar sesión en el inquilino del cliente. Abre una consola de PowerShell visible en la
carpeta de trabajo (INFORMES\\M365\\<herramienta>, con las plantillas) con la ayuda y los ejemplos; guía y orden de ejemplo
a mano. Motor: msys/domain/m365.py."""

import os
import webbrowser

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QApplication, QDialog, QLabel, QVBoxLayout

from msys.domain import m365
from msys.models.tabla_simple import COLOR_AVISO, TablaSimple, objeto_seleccionado
from msys.ui import piezas


class M365Dialog(QDialog):
    def __init__(self, host, parent=None):
        # 5.7.2: se abre desde «🚀 Lanzar Scripts», que es modal: la ventana cuelga de ella (parent) para poder usarse;
        # host sigue siendo la ventana principal (avisos).
        super().__init__(parent if parent is not None else host)
        self.host = host
        self.setWindowTitle("☁️ Microsoft 365 · herramientas de inquilino")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.resize(1180, 640)
        lay = QVBoxLayout(self)
        lay.addWidget(piezas.nota(
            "Herramientas para el INQUILINO de Microsoft 365 del cliente (no para este equipo). Cada una pide iniciar "
            "sesión con una cuenta de administración del cliente. «▶️ Abrir consola» abre PowerShell en su carpeta de "
            "trabajo, con las plantillas CSV y los ejemplos: rellena la plantilla y prueba SIEMPRE primero con -Simular."))
        self.modelo = TablaSimple(["Herramienta", "Área", "Para qué sirve", "¿Cambia algo?"],
                                  color=lambda h, c: COLOR_AVISO if c == 3 and h.cambia else None,
                                  tooltip=lambda h, c: h.resumen if c == 2 else None)
        self.herramientas = m365.catalogo()
        self.modelo.set_datos([[h.carpeta, h.categoria, h.resumen,
                                "Sí (tiene -Simular)" if h.cambia else "No, solo lee"] for h in self.herramientas],
                              self.herramientas)
        self.vista, self.proxy = piezas.tabla(self.modelo, self, columna_ancha=2)
        self.vista.doubleClicked.connect(lambda _i: self._consola())
        lay.addWidget(self.vista, 1)
        self.lbl = QLabel("")
        self.lbl.setWordWrap(True)
        lay.addWidget(self.lbl)
        lay.addLayout(piezas.fila(
            piezas.boton("📖 Guía", "ghost", self._guia, "Guía detallada: requisitos, plantilla, ejemplos y errores"),
            piezas.boton("📋 Copiar orden de ejemplo", "ghost", self._copiar),
            piezas.boton("📂 Carpeta de trabajo", "ghost", self._carpeta),
            None,
            piezas.boton("▶️ Abrir consola", "primary", self._consola,
                         "PowerShell visible en la carpeta de trabajo, con la ayuda y los ejemplos de la herramienta"),
            piezas.boton("Cerrar", "ghost", self.close)))
        self.vista.selectionModel().selectionChanged.connect(lambda *_: self._detalle())

    def _actual(self):
        h = objeto_seleccionado(self.vista, self.proxy, self.modelo)
        if h is None:
            self.lbl.setText("Elige una herramienta de la lista.")
        return h

    def _detalle(self):
        h = objeto_seleccionado(self.vista, self.proxy, self.modelo)
        if h is not None:
            self.lbl.setText(f"<b>{h.carpeta}</b> · {h.resumen}<br><code>{m365.orden_ejemplo(h)}</code>")

    def _guia(self):
        h = self._actual()
        if h is not None and h.guia and os.path.exists(h.guia):
            webbrowser.open(h.guia)

    def _copiar(self):
        h = self._actual()
        if h is not None:
            QApplication.clipboard().setText(m365.orden_ejemplo(h))
            self.lbl.setText("📋 Orden de ejemplo copiada. Cambia los datos (CSV, TenantId…) antes de ejecutarla.")

    def _carpeta(self):
        h = self._actual()
        if h is not None:
            os.startfile(m365.carpeta_trabajo(h))

    def _consola(self):
        h = self._actual()
        if h is None:
            return
        try:
            carpeta = m365.abrir_consola(h)
        except Exception as e:
            self.host._avisar("Microsoft 365", f"No se pudo abrir la consola:\n\n{e}")
            return
        self.lbl.setText(f"▶️ Consola abierta en {carpeta}")


class M365Mixin:
    def abrir_m365(self):
        dlg = getattr(self, "_m365_dlg", None)
        try:
            if dlg is not None and dlg.isVisible():
                dlg.raise_()
                dlg.activateWindow()
                return
        except RuntimeError:
            pass
        self._m365_dlg = M365Dialog(self)
        self._m365_dlg.show()
