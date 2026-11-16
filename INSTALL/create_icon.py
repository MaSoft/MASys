#!/usr/bin/env python3
"""Genera INSTALL/masys.ico (multi-resolucion real) a partir del dibujo
compartido en msys/ui/icono_app.py (mismo logo que la ventana y la bandeja).
QImage.save(..., "ICO") de Qt no genera un .ico multi-resolucion de verdad,
por eso se pasa por Pillow (esta en requirements.txt)."""

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt5.QtCore import QBuffer, QByteArray, QIODevice
from PyQt5.QtWidgets import QApplication
from PIL import Image

from msys.ui.icono_app import dibujar_icono_app

SIZES = [16, 24, 32, 48, 64, 128, 256]


def main() -> None:
    app = QApplication(sys.argv)  # noqa: F841 - QPixmap necesita QApplication
    out_path = Path(__file__).resolve().parent / "masys.ico"

    pil_images = []
    for size in SIZES:
        pix = dibujar_icono_app(size)
        qt_bytes = QByteArray()
        qt_buf = QBuffer(qt_bytes)
        qt_buf.open(QIODevice.WriteOnly)
        pix.save(qt_buf, "PNG")
        qt_buf.close()
        pil_images.append(Image.open(io.BytesIO(bytes(qt_bytes))).convert("RGBA"))

    pil_images[-1].save(out_path, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"Icono generado: {out_path} ({len(SIZES)} resoluciones: {SIZES})")


if __name__ == "__main__":
    main()
