# -*- coding: utf-8 -*-
"""Capturas + auditoría de textos recortados de las pestañas de permisos de
📂 Carpetas Compartidas (4.4.0) y de sus diálogos.

Monta la sección SOLA (sin arrancar MASys), sobre una carpeta temporal que se
crea aquí con permisos de ejemplo (Todos con Modificar, herencia cortada, una
cuenta eliminada, un denegar…). Las copias van a una carpeta temporal, no a
INFORMES. Las confirmaciones responden «No»: la interfaz no cambia nada.

Uso (desde la carpeta de MASYS):  python tests/capturas_permisos.py [carpeta] [--dark]
Debe terminar con «SIN TEXTOS RECORTADOS»."""
import os
import shutil
import sys
import tempfile
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
args = [a for a in sys.argv[1:] if not a.startswith("--")]
SALIDA = os.path.abspath(args[0]) if args else os.path.join(tempfile.gettempdir(), "msys_capturas_permisos")
OSCURO = "--dark" in sys.argv
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

from PyQt5.QtCore import QThread, Qt  # noqa: E402
from PyQt5.QtGui import QFont, QFontMetrics  # noqa: E402
from PyQt5.QtWidgets import (QAbstractButton, QApplication, QCheckBox, QComboBox, QHeaderView, QLabel,  # noqa: E402
                             QLineEdit, QMessageBox, QProgressBar, QPushButton, QTabBar, QVBoxLayout, QWidget)

app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setFont(QFont("Segoe UI", 9))
from msys.widgets.delegates import DARK_STYLESHEET, LIGHT_STYLESHEET  # noqa: E402
app.setStyleSheet(DARK_STYLESHEET if OSCURO else LIGHT_STYLESHEET)

from msys.domain import permisos as dom  # noqa: E402
from msys.ui import permisos as ui  # noqa: E402
from msys.ui.permisos import PermisosMixin  # noqa: E402
from msys.ui.shares import CompartidasMixin  # noqa: E402

BASE = tempfile.mkdtemp(prefix="msys_cap_perm_")
DATOS = os.path.join(BASE, "Datos")
COPIAS = os.path.join(BASE, "copias")
os.makedirs(COPIAS)
dom.carpeta_copias = lambda: COPIAS          # nada a INFORMES del proyecto
HUERFANA = "S-1-5-21-1004336348-1177238915-682003330-1105"
problemas, avisos = [], []


def preparar():
    for c in ("Contabilidad\\2025", "Contabilidad\\2024", "RRHH\\Nóminas", "Público", "Antigua", "Proyectos\\Cliente con nombre largo de ejemplo"):
        os.makedirs(os.path.join(DATOS, c))
    for f in ("Contabilidad\\2025\\balance.xlsx", "RRHH\\Nóminas\\octubre.pdf", "Público\\leeme.txt"):
        open(os.path.join(DATOS, f), "w").close()
    r = lambda x: os.path.join(DATOS, x)  # noqa: E731
    dom.escribir(r("Público"), dom.con_entrada_nueva(dom.leer_descriptor(r("Público")), dom.TODOS, True, dom.MODIFY, "OICI"))
    d = dom.con_herencia(dom.leer_descriptor(r("RRHH")), False, True)
    dom.escribir(r("RRHH"), dom.con_entrada_nueva(d, dom.USUARIOS, True, dom.READ_EXEC, "OICI"))
    d = dom.con_entrada_nueva(dom.leer_descriptor(r("Antigua")), HUERFANA, True, dom.FULL, "OICI")
    dom.escribir(r("Antigua"), dom.con_entrada_nueva(d, dom.USUARIOS, False, 0x116, "OICI"))
    f = r("Contabilidad\\2025\\balance.xlsx")
    dom.escribir(f, dom.con_entrada_nueva(dom.leer_descriptor(f), dom.USUARIOS, True, dom.READ, ""))


class _Ajustes(dict):
    def value(self, k, defecto=None, **_kw):
        return self.get(k, defecto)

    def setValue(self, k, v):
        self[k] = v


class _Auditoria:
    def registrar(self, **kw):
        print("   (historial)", kw.get("accion"), kw.get("objetivo"))


class Host(QWidget, CompartidasMixin, PermisosMixin):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.progress_bar = QProgressBar()
        self.repair_audit_db = _Auditoria()
        self._proveedores_export = {}
        self.settings = _Ajustes()

    def set_status(self, t):
        self.ultimo_estado = t

    def _avisar(self, titulo, texto, icono=QMessageBox.Information):
        avisos.append(f"{titulo}: {texto[:160]}")

    def _confirmar_accion(self, titulo, texto, icono=QMessageBox.Question):
        return False

    def registrar_export(self, nombre, proveedor):
        self._proveedores_export[nombre] = proveedor

    def _actualizar_barra_exportacion(self):
        pass

    # Lo que la pestaña antigua conecta y vive en multisys.py
    def analizar(self):
        pass

    def browse_path(self):
        pass

    def parar_analisis(self):
        pass

    def ejecutar_como_otro_usuario(self):
        pass


def esperar(segundos=120):
    import gc
    fin = time.time() + segundos
    while time.time() < fin:
        for _ in range(20):
            app.processEvents()
        if not any(o.isRunning() for o in gc.get_objects() if isinstance(o, QThread)):
            for _ in range(10):
                app.processEvents()
            return
        time.sleep(0.05)


def auditar(raiz, nombre):
    for w in raiz.findChildren((QLabel, QAbstractButton)):
        if not w.isVisible() or w.width() <= 0:
            continue
        texto = w.text().replace("&", "")
        if not texto or (isinstance(w, QLabel) and (w.wordWrap() or "\n" in texto or texto.startswith("<"))):
            continue
        fm = QFontMetrics(w.font())
        hay = w.contentsRect().width() - (12 if isinstance(w, QPushButton) else 0) - (24 if isinstance(w, QCheckBox) else 0)
        if fm.horizontalAdvance(texto) > hay + 1:
            problemas.append(f"{nombre}: '{texto[:60]}' necesita {fm.horizontalAdvance(texto)}px, tiene {hay}px")
    for c in raiz.findChildren(QComboBox):
        if c.isVisible() and c.currentText() and QFontMetrics(c.font()).horizontalAdvance(c.currentText()) > c.width() - 26:
            problemas.append(f"{nombre}: desplegable '{c.currentText()[:50]}' cortado ({c.width()}px)")
    for cab in raiz.findChildren(QHeaderView):
        if not cab.isVisible() or cab.orientation() != Qt.Horizontal or not cab.model():
            continue
        f = QFont(cab.font())
        f.setBold(True)
        fm = QFontMetrics(f)
        for i in range(cab.count()):
            texto = str(cab.model().headerData(i, Qt.Horizontal) or "")
            if texto and not cab.isSectionHidden(i) and fm.horizontalAdvance(texto) + 10 > cab.sectionSize(i):
                problemas.append(f"{nombre}: cabecera '{texto}' cortada ({cab.sectionSize(i)}px)")
    for e in raiz.findChildren(QLineEdit):
        if e.isVisible() and e.placeholderText() and e.width() < 180 and not isinstance(e.parent(), QComboBox):
            problemas.append(f"{nombre}: campo '{e.placeholderText()[:40]}' demasiado estrecho ({e.width()}px)")
    for b in raiz.findChildren(QTabBar):
        if b.isVisible():
            for i in range(b.count()):
                f = QFont(b.font())
                f.setWeight(QFont.DemiBold)
                if QFontMetrics(f).horizontalAdvance(b.tabText(i)) + 2 * 16 + 8 > b.tabRect(i).width():
                    problemas.append(f"{nombre}: pestaña '{b.tabText(i)}' cortada")


def captura(w, nombre):
    for _ in range(10):
        app.processEvents()
    w.grab().save(os.path.join(SALIDA, f"{nombre}{'_oscuro' if OSCURO else ''}.png"))
    auditar(w, nombre)


try:
    os.makedirs(SALIDA, exist_ok=True)
    preparar()
    host = Host()
    pagina = host.crear_tab_compartidas()
    lay = QVBoxLayout(host)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(pagina)
    host.resize(1300, 720)
    host.show()
    host.perm_ruta.setText(DATOS)
    host._perm_leer()
    esperar()
    assert host._perm_lec is not None, avisos
    host._perm_guardar()
    # Un cambio después de la copia, para que restaurar y comparar tengan algo que enseñar
    pub = os.path.join(DATOS, "Público")
    dom.escribir(pub, dom.con_entrada_nueva(dom.leer_descriptor(pub), dom.AUTENTICADOS, True, dom.FULL, "OICI"))
    time.sleep(1.1)   # otro nombre de archivo (lleva la hora)
    host._perm_leer()
    esperar()
    host._perm_guardar()
    host.perm_ac_cuenta.setText("Usuarios")
    host._perm_consultar()
    esperar()
    host._perm_listar_copias()
    esperar()
    host.perm_co_tabla.selectAll()
    host._perm_comparar_copias()
    esperar()
    host._perm_ir_a("Antigua")
    for ancho, alto in ((1300, 720), (1650, 900)):
        host.resize(ancho, alto)
        esperar(5)
        for i in range(host.compartidas_tabs.count()):
            host.compartidas_tabs.setCurrentIndex(i)
            esperar(5)
            captura(host, f"Permisos_{i}_{ancho}")
    # Diálogos
    nombre_de = host._perm_lec.nombre
    dlg = ui.EditarPermisosDialog(os.path.join(DATOS, "Antigua"), True, nombre_de, host)
    dlg.show()
    captura(dlg, "Permisos_editar")
    dlg.close()
    dlg = ui.AnadirPermisoDialog(DATOS, True, host)
    dlg.txt.setText("Usuarios")
    dlg._comprobar()
    dlg.show()
    captura(dlg, "Permisos_anadir")
    dlg.close()
    dlg = ui.CuentaDialog("Cambiar el propietario", "Nuevo propietario de:\n" + DATOS, DATOS, host, con_rama=True,
                          inicial="Administradores")
    dlg._comprobar()
    dlg.show()
    captura(dlg, "Permisos_propietario")
    dlg.close()
    copias = sorted(os.listdir(COPIAS))
    copia = dom.cargar(os.path.join(COPIAS, copias[0]))
    # Una copia «de otro dominio»: las cuentas se buscan en el destino
    copia.cuentas[HUERFANA] = ("VIEJODOM\\jgarcia", 1)
    copia.equipo = "SRV-VIEJO"
    eq = ui.EquivalenciasDialog(copia, DATOS, None, host)
    eq.show()
    esperar()
    captura(eq, "Permisos_equivalencias")
    eq.close()
    copia = dom.cargar(os.path.join(COPIAS, copias[0]))
    rd = ui.RestaurarDialog(host, copia, os.path.join(COPIAS, copias[0]), host)
    rd.show()
    rd._comparar()
    esperar()
    captura(rd, "Permisos_restaurar")
    print("Diferencias al restaurar:", [(d.estado, d.rel) for d in rd.difs])
    rd.close()
    exportado = sum(len(s[2]) for p in host._proveedores_export.values() for s in (p() or []))
    print(f"Exportación con {exportado} filas; análisis: {len(host._perm_hallazgos)} hallazgos")
    host.close()
finally:
    shutil.rmtree(BASE, ignore_errors=True)

print("\n".join(problemas) if problemas else "SIN TEXTOS RECORTADOS")
if avisos:
    print("AVISOS MOSTRADOS:\n  " + "\n  ".join(avisos))
print("capturas en", SALIDA)
