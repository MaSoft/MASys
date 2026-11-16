# -*- coding: utf-8 -*-
"""Capturas + auditoría de textos recortados de los diálogos nuevos de la
sección Navegadores (Gestor de favoritos y Auditoría de contraseñas),
4.3.1. Sin navegador real ni red: se inyectan datos de ejemplo.

Uso (desde la carpeta de MASYS):  python tests/capturas_navegadores.py [carpeta] [--dark]
Debe terminar con «SIN TEXTOS RECORTADOS»."""
import os
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
args = [a for a in sys.argv[1:] if not a.startswith("--")]
SALIDA = os.path.abspath(args[0]) if args else os.path.join(tempfile.gettempdir(), "msys_capturas_navegadores")
OSCURO = "--dark" in sys.argv
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtGui import QFont, QFontMetrics  # noqa: E402
from PyQt5.QtWidgets import (QAbstractButton, QApplication, QCheckBox, QComboBox,  # noqa: E402
                             QHeaderView, QLabel, QLineEdit, QPushButton)

app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setFont(QFont("Segoe UI", 9))
from msys.widgets.delegates import DARK_STYLESHEET, LIGHT_STYLESHEET  # noqa: E402
app.setStyleSheet(DARK_STYLESHEET if OSCURO else LIGHT_STYLESHEET)

from msys.domain import favoritos as fav  # noqa: E402
from msys.domain import passwords_auditoria as pa  # noqa: E402


class _P:
    def __init__(self, nav): self.navegador, self.nombre_perfil, self.ruta, self.carpeta_user_data = nav, "Default", "", ""


FAVS = [
    fav.Favorito("bookmark_bar", "", "Masoft — soluciones de software para empresas", "https://masoft.es", fav.OK, "200 OK"),
    fav.Favorito("bookmark_bar", "Trabajo", "Panel de administración de Microsoft 365", "https://admin.microsoft.com", fav.REDIRIGE, "→ login"),
    fav.Favorito("bookmark_bar", "Trabajo/Documentación", "Python docs", "https://docs.python.org", fav.OK, "200 OK"),
    fav.Favorito("other", "", "Página que ya no existe", "https://ejemplo.com/rota", fav.ROTO, "404 Not Found"),
    fav.Favorito("other", "Personal", "Servidor interno sin salida", "https://10.0.0.5/panel", fav.SINRED, "No responde"),
]
PWDS = [
    pa.FilaPassword("Chrome", "Default", "https://correo.ejemplo.com", "juan@ejemplo.com", "123456", pa.MUY_DEBIL, 0, 3, 3500000, "está entre las más usadas, solo números"),
    pa.FilaPassword("Chrome", "Default", "https://banco.ejemplo.com", "juan@ejemplo.com", "Verano2021!", pa.ACEPTABLE, 60, 0, 0, "contiene un año"),
    pa.FilaPassword("Edge", "Default", "https://tienda.ejemplo.com", "juan", "gH7$kLm2!qZ", pa.FUERTE, 88, 0, 0, ""),
    pa.FilaPassword("Chrome", "Default", "https://foro.ejemplo.com", "juanito", None, "", 0, 0, pa.NO_COMPROBADA, ""),
]

problemas = []


def auditar(raiz, nombre):
    for w in raiz.findChildren((QLabel, QAbstractButton)):
        if not w.isVisible() or w.width() <= 0:
            continue
        texto = w.text().replace("&", "")
        if not texto or (isinstance(w, QLabel) and (w.wordWrap() or "\n" in texto or texto.startswith("<"))):
            continue
        fm = QFontMetrics(w.font())
        necesita = fm.horizontalAdvance(texto)
        hay = w.contentsRect().width()
        if isinstance(w, QPushButton):
            hay -= (w.iconSize().width() + 16 if not w.icon().isNull() else 0) + 12
        if isinstance(w, QCheckBox):
            hay -= 24
        if necesita > hay + 1:
            problemas.append(f"{nombre}: '{texto[:60]}' necesita {necesita}px, tiene {hay}px ({type(w).__name__})")
    for cab in raiz.findChildren(QHeaderView):
        if not cab.isVisible() or cab.orientation() != Qt.Horizontal or not cab.model():
            continue
        f = QFont(cab.font())
        f.setBold(True)
        fm = QFontMetrics(f)
        for i in range(cab.count()):
            if cab.isSectionHidden(i):
                continue
            texto = str(cab.model().headerData(i, Qt.Horizontal) or "")
            if texto and fm.horizontalAdvance(texto) + 10 > cab.sectionSize(i):
                problemas.append(f"{nombre}: cabecera '{texto}' cortada ({cab.sectionSize(i)}px)")


def captura(w, nombre):
    for _ in range(8):
        app.processEvents()
    w.grab().save(os.path.join(SALIDA, f"{nombre}{'_oscuro' if OSCURO else ''}.png"))
    auditar(w, nombre)


os.makedirs(SALIDA, exist_ok=True)

# ── Gestor de favoritos ───────────────────────────────────────────────
fav.perfiles_favoritos = lambda: [_P("Chrome"), _P("Edge"), _P("Firefox")]
from msys.ui.favoritos_dialog import GestorFavoritosDialog  # noqa: E402
dlg = GestorFavoritosDialog()
dlg._poblar(_P("Chrome"), FAVS)
dlg._actualizar_estado(FAVS)
for ancho, alto in ((900, 640), (1200, 820)):
    dlg.resize(ancho, alto)
    dlg.show()
    captura(dlg, f"Favoritos_{ancho}")
dlg.close()

# ── Auditoría de contraseñas ──────────────────────────────────────────
pa.perfiles_passwords = lambda: [_P("Chrome"), _P("Edge")]
from msys.ui.passwords_auditoria_dialog import AuditoriaPasswordsDialog  # noqa: E402
dlg = AuditoriaPasswordsDialog()
dlg._filas = PWDS
dlg._poblar()
dlg._resumen()
for b in (dlg.btn_expo, dlg.btn_csv, dlg.btn_html):
    b.setEnabled(True)
for ancho, alto in ((900, 640), (1200, 820)):
    dlg.resize(ancho, alto)
    dlg.show()
    captura(dlg, f"Contrasenas_{ancho}")
dlg.close()

print("\n".join(problemas) if problemas else "SIN TEXTOS RECORTADOS")
print("capturas en", SALIDA)
