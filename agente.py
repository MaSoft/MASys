# -*- coding: utf-8 -*-
"""MASys · Agente de análisis (4.7.0): punto de entrada de MASys_Agente_vX.Y.Z.exe.

Se ejecuta en el equipo del CLIENTE. Solo lee (los mismos recolectores que
«🔄 Sincronizar Todo») y deja un único archivo cifrado en el Escritorio.
No deja nada más: los temporales van a una carpeta propia que se borra al
salir, y no se toca el registro ni se crean bases de datos (no se arranca la
ventana de MASys)."""

import os
import shutil
import sys
import tempfile
import uuid


def preparar_temporales() -> str:
    """Carpeta temporal propia (se borra al salir). Se llama ANTES de importar nada que pueda crear temporales."""
    carpeta = os.path.join(tempfile.gettempdir(), f"MASys_Agente_{uuid.uuid4().hex[:8]}")
    os.makedirs(carpeta, exist_ok=True)
    os.environ["TEMP"] = os.environ["TMP"] = carpeta
    tempfile.tempdir = carpeta
    return carpeta


def main() -> int:
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QFont
    from PyQt5.QtWidgets import QApplication

    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setFont(QFont("Segoe UI", 9))
    from msys.widgets.delegates import LIGHT_STYLESHEET
    app.setStyleSheet(LIGHT_STYLESHEET)

    from msys.agente.ventana import VentanaAgente
    from msys.core.utils import is_admin
    ventana = VentanaAgente(es_admin=bool(is_admin()))
    ventana.show()
    return app.exec_()


if __name__ == "__main__":
    _temp = preparar_temporales()
    codigo = 1
    try:
        codigo = main()
    finally:
        tempfile.tempdir = None
        shutil.rmtree(_temp, ignore_errors=True)
    sys.exit(codigo)
