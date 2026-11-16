# -*- coding: utf-8 -*-
"""Capturas + auditoría de textos recortados de la Base de conocimiento (4.5.0)
y de los botones de abajo a la izquierda. Sin arrancar MASys entero: un host
simulado con las secciones reales y una multisys.db temporal.

Recorre TODAS las fichas a 1024x600 y 1280x680 (ventana ajustada a pantalla),
la búsqueda, el editor nuevo y el de una ficha duplicada.

Uso (desde la carpeta de MASYS):  python tests/capturas_conocimiento.py [carpeta] [--dark]
Debe terminar con «SIN TEXTOS RECORTADOS»."""
import os
import re
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
args = [a for a in sys.argv[1:] if not a.startswith("--")]
SALIDA = os.path.abspath(args[0]) if args else os.path.join(tempfile.gettempdir(), "msys_capturas_conocimiento")
OSCURO = "--dark" in sys.argv
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

from PyQt5.QtCore import QRect, Qt  # noqa: E402
from PyQt5.QtGui import QFont, QFontMetrics  # noqa: E402
from PyQt5.QtWidgets import (QAbstractButton, QApplication, QCheckBox, QComboBox, QHeaderView, QLabel,  # noqa: E402
                             QPushButton, QScrollArea, QTabWidget, QVBoxLayout, QWidget)

app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setFont(QFont("Segoe UI", 9))
from msys.widgets.delegates import DARK_STYLESHEET, LIGHT_STYLESHEET  # noqa: E402
app.setStyleSheet(DARK_STYLESHEET if OSCURO else LIGHT_STYLESHEET)

from msys.domain import comprobar as cp  # noqa: E402
from msys.domain import conocimiento as kb  # noqa: E402
from msys.ui import comprobar_ficha  # noqa: E402
from msys.ui import conocimiento_dialog as cd  # noqa: E402
from tests.test_comprobar import _Trabajo, _registro_real  # noqa: E402

with open(os.path.join(RAIZ, "multisys.py"), encoding="utf-8") as f:
    SECCIONES = re.findall(r'_add_section\(self\.\w+, "([^"]+)"\)', f.read())

problemas = []


class Host(QWidget):
    def __init__(self):
        super().__init__()
        self.current_theme = "dark" if OSCURO else "light"
        self.tab_widgets, self._section_order = {}, []
        for nombre in SECCIONES:
            w = QWidget()
            if nombre == "🧪 Herramientas de red":
                t = QTabWidget(w)
                t.addTab(QWidget(), "🔬 Diagnóstico por capas")
                t.addTab(QWidget(), "🔎 Consultas")
                QVBoxLayout(w).addWidget(t)
            self.tab_widgets[nombre] = w
            self._section_order.append(nombre)

        # «🔎 Comprobar ahora» (4.8.0): las secciones con detector del Soporte de verdad y avisos de ejemplo
        self.worker = None
        self._con_detector = [x for x, (_a, d) in _registro_real().items() if d is not None]
        self._ejemplos = {
            "🖨️ Impresoras": [("impresoras_problema", "⚠️ Impresoras con problema: 2 encontrada(s)", lambda: None)],
            "💽 Discos Duros": [("particion_limpiar::C:", "🧹 Limpiar partición del sistema C: (8.5% libre)", lambda: None)],
            "⚠️ Eventos Sistema": [("eventos", "⚠️ Eventos críticos conocidos: 3 (Kernel-Power 41, disco 7, servicio 7031)",
                                   lambda: None)],
        }

    def _registro_soporte_por_seccion(self):
        return {x: ([], lambda x=x: list(self._ejemplos.get(x, []))) for x in self._con_detector}

    def _leer(self, seccion):
        self.worker = _Trabajo(ms=5, error="Acceso denegado al leer la sección" if seccion == "🔍 Red Local" else "")

    def seleccionar_seccion(self, nombre):
        pass

    def abrir_copias_seguridad(self):
        pass

    def _dash_colors(self):
        if OSCURO:
            return {"title": "#e8ecf1", "muted": "#8a94a6", "tile_subtitle": "#aab4c4", "alerts_bg": "#262837",
                    "alerts_border": "#3a3d4a", "alerts_text": "#d3d9e3"}
        return {"title": "#1a2634", "muted": "#8a94a6", "tile_subtitle": "#5d6b7a", "alerts_bg": "#f7f9fc",
                "alerts_border": "#e3e7ee", "alerts_text": "#2c3e50"}


for _seccion, _metodo in cp.RECARGA.items():
    setattr(Host, _metodo, lambda self, s=_seccion: self._leer(s))


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
    for c in raiz.findChildren(QComboBox):
        if c.isVisible() and c.width() < c.minimumSizeHint().width():
            problemas.append(f"{nombre}: desplegable '{c.currentText()[:40]}' estrechado ({c.width()}px)")
    for cab in raiz.findChildren(QHeaderView):
        if not cab.isVisible() or cab.orientation() != Qt.Horizontal or not cab.model():
            continue
        f = QFont(cab.font())
        f.setBold(True)
        fm = QFontMetrics(f)
        for i in range(cab.count()):
            texto = str(cab.model().headerData(i, Qt.Horizontal) or "")
            # Los dos iconos de desplegar/plegar ocupan 52 px a la derecha del título
            if texto and fm.horizontalAdvance(texto) + 10 + 52 > cab.sectionSize(i):
                problemas.append(f"{nombre}: cabecera '{texto}' tapada por los iconos ({cab.sectionSize(i)}px)")
    # Una zona sin barra horizontal corta el contenido sin avisar
    for area in raiz.findChildren(QScrollArea):
        if area.isVisible() and area.widget() is not None:
            minimo = area.widget().minimumSizeHint().width()
            if minimo > area.viewport().width() + 1:
                problemas.append(f"{nombre}: contenido de {minimo}px en una zona de {area.viewport().width()}px")
    # Ninguna fila del árbol cortada con «…» (la auditoría de etiquetas no ve los árboles)
    arbol = getattr(raiz, "arbol", None)
    if arbol is not None and arbol.isVisible():
        ancho = arbol.viewport().width()
        for i in range(arbol.topLevelItemCount()):
            tema = arbol.topLevelItem(i)
            for item in [tema] + [tema.child(j) for j in range(tema.childCount())]:
                rect = arbol.visualItemRect(item)
                if not rect.isValid():
                    continue
                necesita = QFontMetrics(item.font(0)).horizontalAdvance(item.text(0)) + 8
                if rect.x() + necesita > ancho:
                    problemas.append(f"{nombre}: fila del árbol cortada '{item.text(0)[:50]}' "
                                     f"({rect.x() + necesita}px de {ancho}px)")
    # Los iconos de la cabecera no tapan el título
    if arbol is not None and arbol.isVisible():
        cab = arbol.header()
        fin_titulo = 8 + QFontMetrics(cab.font()).horizontalAdvance("Problema")
        if raiz._iconos_cab.btn_desplegar.x() < fin_titulo:
            problemas.append(f"{nombre}: iconos de la cabecera encima del título")


def captura(w, nombre, guardar=True):
    for _ in range(8):
        app.processEvents()
    if guardar:
        w.grab().save(os.path.join(SALIDA, f"{nombre}{'_oscuro' if OSCURO else ''}.png"))
    auditar(w, nombre)


os.makedirs(SALIDA, exist_ok=True)
host = Host()
db = kb.ConocimientoDB(os.path.join(tempfile.mkdtemp(), "multisys.db"))
dlg = cd.BaseConocimientoDialog(host, db)
dlg._confirmar = lambda *_a: True

# Tamaños: el que toma la ventana en una pantalla de 1024x600 y de 1280x680
for ancho, alto in ((984, 520), (1240, 620)):
    dlg.resize(ancho, alto)
    dlg.show()
    dlg.busqueda.clear()
    dlg.arbol.collapseAll()
    captura(dlg, f"KB_{ancho}_vacia")
    dlg.arbol.expandAll()
    # TODAS las fichas (solo se guardan imágenes de algunas)
    for ficha in kb.FICHAS_FIJAS:
        dlg._mostrar_ficha(ficha.id)
        captura(dlg, f"KB_{ancho}_{ficha.id}", guardar=ficha.id in ("red-sin-internet", "est-pantallazo", "comp-permisos"))
    # Comprobar ahora: con avisos, sin avisos y con una sección que no se pudo leer
    comprobar_ficha._validados_reales = lambda: ()
    for fid in ("imp-no-imprime", "red-sin-internet", "est-pantallazo"):
        dlg._mostrar_ficha(fid)
        dlg.comprobar(fid)
        while dlg._comprobador.activo:
            app.processEvents()
        captura(dlg, f"KB_{ancho}_comprobada_{fid}")
    dlg._comprobador = None
    dlg.busqueda.setText("impresora")
    captura(dlg, f"KB_{ancho}_busqueda")
    dlg.busqueda.clear()
    dlg.nueva_ficha()
    dlg.ed_titulo.setText("Escáner de red no escanea")
    dlg._filas[0].texto.setText("Probar la consulta del servidor de correo")
    dlg._filas[0].seccion.setCurrentText("🧪 Herramientas de red")
    dlg._filas[0].pestana.setCurrentIndex(2)
    dlg._anadir_paso()
    captura(dlg, f"KB_{ancho}_editor_nueva")
    dlg.cancelar_edicion()
    dlg.duplicar("red-sin-internet")
    captura(dlg, f"KB_{ancho}_editor_duplicada")
    dlg.cancelar_edicion()
dlg.hide()

# Botones de abajo a la izquierda, al ancho mínimo del panel del árbol (240 px)
panel = QWidget()
lay = QVBoxLayout(panel)
for texto in ("📚 Base de conocimiento", "🖥️ Acceso remoto", "⚙️ Configuración y perfiles", "💿 Copias de Seguridad"):
    b = QPushButton(texto)
    b.setObjectName("navBackupsButton")
    lay.addWidget(b)
panel.setFixedWidth(240)
panel.show()
captura(panel, "Botones_abajo_izquierda")
panel.setGeometry(QRect(0, 0, 240, panel.sizeHint().height()))

print("\n".join(problemas) if problemas else "SIN TEXTOS RECORTADOS")
print("capturas en", SALIDA)

