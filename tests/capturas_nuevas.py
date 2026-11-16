# -*- coding: utf-8 -*-
"""Capturas reales de los apartados nuevos de MASys (Iconos, Extensiones,
Características, Herramientas de red, Migración, Móvil) + auditoría de textos
recortados.

Monta cada sección SOLA en una ventana mínima (sin arrancar MASys entero ni
sus recolectores), con datos reales de este equipo y SIN ejecutar ninguna
acción que cambie nada: las confirmaciones responden «No».

Uso (desde la carpeta de MASYS):  python tests/capturas_nuevas.py [carpeta_salida] [--dark] [--solo=Iconos]"""
import os
import sys
import tempfile
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
args = [a for a in sys.argv[1:] if not a.startswith("--")]
SALIDA = os.path.abspath(args[0]) if args else os.path.join(tempfile.gettempdir(), "msys_capturas_nuevas")
OSCURO = "--dark" in sys.argv
SOLO = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--solo=")), "")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtGui import QFont, QFontMetrics  # noqa: E402
from PyQt5.QtWidgets import (QAbstractButton, QApplication, QCheckBox, QComboBox, QLabel, QLineEdit, QTabBar, QMessageBox, QProgressBar,  # noqa: E402
                             QPushButton, QTabWidget, QVBoxLayout, QWidget)

app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setFont(QFont("Segoe UI", 9))

from msys.widgets.delegates import DARK_STYLESHEET, LIGHT_STYLESHEET  # noqa: E402

app.setStyleSheet(DARK_STYLESHEET if OSCURO else LIGHT_STYLESHEET)

from msys.ui.iconos import IconosMixin  # noqa: E402

MIXINS = [("Iconos", IconosMixin, "crear_tab_iconos", "iconos_tabs")]
try:
    from msys.ui.asociaciones import AsociacionesMixin  # noqa: E402
    MIXINS.append(("Extensiones", AsociacionesMixin, "crear_tab_asociaciones", "asoc_tabs"))
except ImportError:
    pass
try:
    from msys.ui.caracteristicas import CaracteristicasMixin  # noqa: E402
    MIXINS.append(("Caracteristicas", CaracteristicasMixin, "crear_tab_caracteristicas", None))
except ImportError:
    pass
try:
    from msys.ui.herramientas_red import HerramientasRedMixin  # noqa: E402
    MIXINS.append(("HerramientasRed", HerramientasRedMixin, "crear_tab_herramientas_red", "hred_tabs"))
except ImportError:
    pass
try:
    from msys.ui.migracion import MigracionMixin  # noqa: E402
    MIXINS.append(("Migracion", MigracionMixin, "crear_tab_migracion", None))
except ImportError:
    pass
# Móviles Android (5.3.0) ya no es una sección: sus capturas están en tests/capturas_moviles.py
try:
    from msys.ui.estabilidad import EstabilidadMixin  # noqa: E402
    MIXINS.append(("Estabilidad", EstabilidadMixin, "crear_tab_estabilidad", "estab_tabs"))
except ImportError:
    pass


class _Auditoria:
    def registrar(self, **kw):
        print("   (historial)", kw.get("accion"), kw.get("objetivo"))


avisos = []
problemas = []


class _AjustesFalsos(dict):
    def value(self, k, defecto=None):
        return self.get(k, defecto)

    def setValue(self, k, v):
        self[k] = v


def crear_host(mixin):
    class Host(QWidget, mixin):
        def __init__(self):
            super().__init__()
            self.worker = None
            self.progress_bar = QProgressBar()
            self.repair_audit_db = _Auditoria()
            self._proveedores_export = {}
            self.current_theme = "dark" if OSCURO else "light"
            self.settings = _AjustesFalsos()

        def set_status(self, t):
            self.ultimo_estado = t

        def _avisar(self, titulo, texto, icono=QMessageBox.Information):
            avisos.append(f"{titulo}: {texto[:160]}")

        def _confirmar_accion(self, titulo, texto, icono=QMessageBox.Question):
            return False

        def registrar_export(self, nombre, proveedor):
            self._proveedores_export[nombre] = proveedor

        def registrar_html(self, nombre, generador):
            pass

        def _actualizar_barra_exportacion(self):
            pass

        def _on_progress(self, p, m):
            pass

        def _dash_colors(self):
            # Misma paleta que ShareInspector._dash_colors (multisys.py)
            if OSCURO:
                return {'alerts_bg': '#262837', 'alerts_border': '#3a3d4a', 'muted': '#8a94a6'}
            return {'alerts_bg': '#f7f9fc', 'alerts_border': '#e3e7ee', 'muted': '#8a94a6'}

    return Host()


def esperar(segundos=60):
    """Procesa eventos hasta que no quede ningún hilo QThread vivo (o tiempo)."""
    from PyQt5.QtCore import QThread
    fin = time.time() + segundos
    while time.time() < fin:
        for _ in range(20):
            app.processEvents()
        vivos = [t for t in app.findChildren(QThread)] + [t for t in _hilos() if t.isRunning()]
        if not any(t.isRunning() for t in vivos):
            for _ in range(10):
                app.processEvents()
            return
        time.sleep(0.05)


def _hilos():
    from PyQt5.QtCore import QThread
    import gc
    return [o for o in gc.get_objects() if isinstance(o, QThread)]


def auditar(raiz, nombre):
    for w in raiz.findChildren((QLabel, QAbstractButton)):
        if not w.isVisible() or w.width() <= 0:
            continue
        texto = w.text().replace("&", "")
        if not texto:
            continue
        if isinstance(w, QLabel) and (w.wordWrap() or "\n" in texto or texto.startswith("<")):
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
        if c.isVisible() and c.currentText():
            fm = QFontMetrics(c.font())
            if fm.horizontalAdvance(c.currentText()) > c.width() - 26:
                problemas.append(f"{nombre}: desplegable '{c.currentText()[:50]}' cortado ({c.width()}px)")
    from PyQt5.QtWidgets import QHeaderView
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
    for e in raiz.findChildren(QLineEdit):
        if e.isVisible() and e.placeholderText() and e.width() < 180 and not isinstance(e.parent(), (QComboBox,)):
            problemas.append(f"{nombre}: campo '{e.placeholderText()[:40]}' demasiado estrecho ({e.width()}px)")
    for b in raiz.findChildren(QTabBar):
        if not b.isVisible():
            continue
        for i in range(b.count()):
            f = QFont(b.font())
            f.setWeight(QFont.DemiBold)
            fm = QFontMetrics(f)
            if fm.horizontalAdvance(b.tabText(i)) + 2 * 16 + 8 > b.tabRect(i).width():
                problemas.append(f"{nombre}: pestaña '{b.tabText(i)}' cortada")


def captura(w, nombre):
    for _ in range(10):
        app.processEvents()
    w.grab().save(os.path.join(SALIDA, f"{nombre}{'_oscuro' if OSCURO else ''}.png"))
    auditar(w, nombre)


os.makedirs(SALIDA, exist_ok=True)
for titulo, mixin, crear, attr_tabs in MIXINS:
    if SOLO and SOLO != titulo:
        continue
    host = crear_host(mixin)
    pagina = getattr(host, crear)()
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(pagina)
    if titulo == "HerramientasRed":
        host.hred_diag.txt_destino.setText("masoft.es")
        host.hred_diag.diagnosticar()
        host.hred_consultas.txt.setText("masoft.es")
        host.hred_consultas.herr.setCurrentIndex(3)
        host.hred_consultas.consultar()
        host.show()
        esperar(180)
    if titulo == "Migracion":
        host.show()
        host._mig_inventario()
        esperar(400)
        host.resize(1300, 720)
        captura(host, "Migracion_inventario_1300")
        from msys.domain import migracion as _m
        _inv = _m.cargar(os.path.join(SALIDA, "..", "inv.msysinv")) if os.path.exists(os.path.join(SALIDA, "..", "inv.msysinv")) else host._mig_inv
        _inv = dict(_inv, equipo="EQUIPO-VIEJO", elementos=_inv["elementos"] + [dict(_inv["elementos"][-1], elemento="Programa que no está aquí",
                                                                                   clave="prog:no esta aqui", categoria=_m.C_PROGRAMAS)])
        host._mig_origen = _inv
        host._mig_mostrar_comparacion()
    for ancho, alto in ((1300, 720), (1650, 900)):
        host.resize(ancho, alto)
        host.show()
        esperar()
        tabs = getattr(host, attr_tabs) if attr_tabs else None
        if isinstance(tabs, QTabWidget):
            for i in range(tabs.count()):
                tabs.setCurrentIndex(i)
                esperar(90)
                captura(host, f"{titulo}_{i}_{ancho}")
        else:
            captura(host, f"{titulo}_{ancho}")
    if titulo == "Caracteristicas" and getattr(host, "_carac_lista", None):
        from msys.ui.caracteristicas import PerfilesDialog
        for fila in range(4):
            dlg = PerfilesDialog(host, host._carac_lista, host)
            dlg.perfiles.setCurrentRow(fila)
            dlg.show()
            captura(dlg, f"{titulo}_perfil_{fila}")
            dlg.close()
    exportado = sum(len(s[2]) for p in host._proveedores_export.values() for s in (p() or []))
    print(f"{titulo}: exportación con {exportado} filas")
    host.close()

print("\n".join(problemas) if problemas else "SIN TEXTOS RECORTADOS")
if avisos:
    print("AVISOS MOSTRADOS:\n  " + "\n  ".join(avisos))
print("capturas en", SALIDA)
