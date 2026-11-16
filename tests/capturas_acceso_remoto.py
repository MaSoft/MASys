# -*- coding: utf-8 -*-
"""Capturas + auditoría de textos recortados del Acceso remoto (4.6.0).
Datos de ejemplo inventados en una multisys.db temporal; no se lanza nada.

Uso (desde la carpeta de MASYS):  python tests/capturas_acceso_remoto.py [carpeta] [--dark]
Debe terminar con «SIN TEXTOS RECORTADOS»."""
import os
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
args = [a for a in sys.argv[1:] if not a.startswith("--")]
SALIDA = os.path.abspath(args[0]) if args else os.path.join(tempfile.gettempdir(), "msys_capturas_acceso")
OSCURO = "--dark" in sys.argv
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtGui import QFont, QFontMetrics  # noqa: E402
from PyQt5.QtWidgets import (QAbstractButton, QAbstractItemView, QApplication, QCheckBox, QComboBox, QHeaderView, QLabel,  # noqa: E402
                             QPushButton, QScrollArea, QTreeWidget, QWidget)

app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setFont(QFont("Segoe UI", 9))
from msys.widgets.delegates import DARK_STYLESHEET, LIGHT_STYLESHEET  # noqa: E402
app.setStyleSheet(DARK_STYLESHEET if OSCURO else LIGHT_STYLESHEET)

from msys.domain import acceso_remoto as ar  # noqa: E402
from msys.ui import acceso_remoto_dialog as ad  # noqa: E402

ar.lanzar = lambda argv: None            # nunca se lanza nada
problemas = []


class Host(QWidget):
    def _dash_colors(self):
        if OSCURO:
            return {"title": "#e8ecf1", "muted": "#8a94a6", "alerts_bg": "#262837", "alerts_border": "#3a3d4a",
                    "alerts_text": "#d3d9e3"}
        return {"title": "#1a2634", "muted": "#8a94a6", "alerts_bg": "#f7f9fc", "alerts_border": "#e3e7ee",
                "alerts_text": "#2c3e50"}


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
            hay -= (w.iconSize().width() + 16 if not w.icon().isNull() else 0) + 12 + (16 if w.menu() else 0)
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
            if cab.isSectionHidden(i):
                continue
            texto = str(cab.model().headerData(i, Qt.Horizontal) or "")
            if texto and fm.horizontalAdvance(texto) + 10 > cab.sectionSize(i):
                problemas.append(f"{nombre}: cabecera '{texto}' cortada ({cab.sectionSize(i)}px)")
    for area in raiz.findChildren(QScrollArea):
        if area.isVisible() and area.widget() is not None:
            minimo = area.widget().minimumSizeHint().width()
            if minimo > area.viewport().width() + 1:
                problemas.append(f"{nombre}: contenido de {minimo}px en una zona de {area.viewport().width()}px")
    # Nada que se salga de la ventana (un mínimo mayor que la ventana la desborda sin barra)
    for w in raiz.findChildren(QWidget):
        if w.isVisible() and w.isWindow() is False and w.window() is raiz:
            derecha = w.mapTo(raiz, w.rect().topRight()).x()
            if derecha > raiz.width() + 1 and isinstance(w, (QAbstractItemView, QPushButton, QLabel)):
                problemas.append(f"{nombre}: {type(w).__name__} '{getattr(w, 'text', lambda: '')()[:30]}' se sale "
                                 f"de la ventana ({derecha}px de {raiz.width()}px)")
    # Tablas y árboles: sin barra horizontal (si sale, algo no cabe a lo ancho)
    for vista in raiz.findChildren(QAbstractItemView):
        if vista.isVisible() and vista.horizontalScrollBar().isVisible():
            problemas.append(f"{nombre}: {type(vista).__name__} con barra horizontal (no cabe a lo ancho)")
    for arbol in raiz.findChildren(QTreeWidget):
        if not arbol.isVisible():
            continue
        ancho = arbol.viewport().width()
        for i in range(arbol.topLevelItemCount()):
            tope = arbol.topLevelItem(i)
            for item in [tope] + [tope.child(j) for j in range(tope.childCount())]:
                for col in range(arbol.columnCount()):
                    rect = arbol.visualRect(arbol.indexFromItem(item, col))
                    if not rect.isValid() or not item.text(col):
                        continue
                    necesita = QFontMetrics(item.font(col)).horizontalAdvance(item.text(col)) + 10
                    if necesita > rect.width() and rect.x() + rect.width() <= ancho:
                        problemas.append(f"{nombre}: celda cortada '{item.text(col)[:40]}' "
                                         f"({necesita}px de {rect.width()}px)")


def captura(w, nombre):
    for _ in range(8):
        app.processEvents()
    w.grab().save(os.path.join(SALIDA, f"{nombre}{'_oscuro' if OSCURO else ''}.png"))
    auditar(w, nombre)


os.makedirs(SALIDA, exist_ok=True)
db = ar.AccesoDB(os.path.join(tempfile.mkdtemp(), "multisys.db"))
host = Host()
dlg = ad.AccesoRemotoDialog(host, db)
dlg._confirmar = lambda *_a: True

for ancho, alto in ((984, 520), (1240, 620)):
    dlg.resize(ancho, alto)
    dlg.show()
    captura(dlg, f"AR_{ancho}_vacia")

# Datos de ejemplo (inventados)
CONTACTOS = {   # inventados
    "Asesoría del Norte": ("Marta Gil", "marta.gil@asesorianorte.example", "922 111 222"),
    "Clínica Dental Mediterráneo": ("Luis Herrera Cabrera", "recepcion@clinicamediterraneo.example", "+34 600 123 456"),
    "Talleres Hermanos Ruiz": ("Pedro Ruiz", "", "928 333 444"),
}
EJEMPLO = [
    ("Asesoría del Norte", "Recepción", ar.RUSTDESK, "123 456 789", "", "", "Pide aceptar la conexión"),
    ("Asesoría del Norte", "Servidor de archivos", ar.RDP, "", "srv-archivos.asesoria.local", "administrador", ""),
    ("Asesoría del Norte", "Contabilidad 2", ar.SOMBRA, "", "10.0.0.24", "", ""),
    ("Clínica Dental Mediterráneo", "Mostrador", ar.RUSTDESK, "987 654 321", "192.168.10.15", "", "Horario de 9 a 14"),
    ("Clínica Dental Mediterráneo", "Gabinete 1", ar.ASISTENCIA, "", "", "", ""),
    ("Talleres Hermanos Ruiz", "Oficina", ar.OFRECER, "", "oficina-taller", "", ""),
]
for cliente, nombre, metodo, rid, direccion, usuario, notas in EJEMPLO:
    contacto, correo, telefono = CONTACTOS[cliente]
    e = ar.Equipo(db.nuevo_id(), cliente, nombre, metodo, rid, direccion, usuario, notas, contacto=contacto,
                  correo=correo, telefono=telefono)
    db.guardar(e, clave="ejemplo" if rid else None)
    db.registrar(e, metodo, "Iniciada")
dlg.recargar()
primero = next(e for e in dlg._equipos if e.nombre == "Mostrador")

for ancho, alto in ((984, 520), (1240, 620)):
    dlg.resize(ancho, alto)
    dlg.recargar(seleccionar=primero.id)
    dlg._actual = primero.id
    dlg._mostrar_ficha()
    captura(dlg, f"AR_{ancho}_ficha")
    dlg.editar(primero.id)
    captura(dlg, f"AR_{ancho}_editor")
    dlg.cancelar_edicion()
    dlg.nuevo_equipo()
    captura(dlg, f"AR_{ancho}_nuevo")
    dlg.cancelar_edicion()
    # Todas las fichas
    for e in list(dlg._equipos):
        dlg._actual = e.id
        dlg._mostrar_ficha()
        for _ in range(4):
            app.processEvents()
        auditar(dlg, f"AR_{ancho}_ficha_{e.nombre}")

# Servidor propio (4.11.0-4.12.0): las tres pestañas, vacías y con datos, a 720 y 900 de ancho
from msys.domain import servidor_rustdesk as sr  # noqa: E402
import time  # noqa: E402
CLAVE = "5Qbwsde3unUcJBtrx9ZkvUmwFNoExHzpryHuPUdqlWM="


class LocalEjemplo:
    """Servidor en este PC simulado (no arranca nada)."""

    def __init__(self, en_marcha):
        self.e = sr.Estado(en_marcha, en_marcha, en_marcha, en_marcha)

    def faltan(self):
        return []

    def estado(self, con_sistema=True):
        return self.e

    def clave_publica(self):
        return CLAVE if self.e.en_marcha else ""

    def equipos(self):
        if not self.e.en_marcha:
            return []
        return [sr.EquipoServidor("12345678", "31/10/2026 00:12", "192.168.1.75"),
                sr.EquipoServidor("123456789", "30/10/2026 18:40", "198.51.100.17"),
                sr.EquipoServidor("987654321", "29/10/2026 09:05", "2a0c:5a80:1234:5600::1")]


def esperar_estado(sv):
    fin_espera = time.time() + 5
    while sv._tarea_estado is not None and sv._tarea_estado.isRunning() and time.time() < fin_espera:
        app.processEvents()
    for _ in range(10):
        app.processEvents()


def capturar_servidor(sv, nombre):
    alto = min(sv.sizeHint().height(), 560)        # pantalla de 1024x600 menos la barra de tareas
    if sv.minimumSizeHint().height() > 560:
        problemas.append(f"{nombre}: la ventana necesita {sv.minimumSizeHint().height()} px de alto")
    for ancho in (720, 900):
        for i in range(sv.pestanas.count()):
            sv.pestanas.setCurrentIndex(i)
            sv.resize(ancho, alto)
            sv.show()
            captura(sv, f"AR_servidor_{nombre}_{ancho}_p{i + 1}")


sv = ad.ServidorPropioDialog(dlg, db, local=LocalEjemplo(False))
sv._aviso = lambda *_a: None
esperar_estado(sv)
capturar_servidor(sv, "vacia")
sv.close()
sv = ad.ServidorPropioDialog(dlg, db, local=LocalEjemplo(True))
sv._aviso = lambda *_a: None
esperar_estado(sv)
sv.host.setText("midominio.ddns.net")
sv.clave.setText(CLAVE)
sv.origen.setText(r"C:\Users\tecnico\Downloads\rustdesk-1.5.0-x86_64.exe")
sv._resultado_texto("\n".join([
    "✔ 21115 TCP · Prueba del tipo de red: Responde",
    "✔ 21116 TCP · Identificación (ID): Responde",
    "✖ 21117 TCP · Retransmisión: No contesta: ¿puerto sin redirigir en el router o firewall?",
    "El 21116 UDP no se puede comprobar desde aquí. Desde tu propia red, algunos routers no dejan llegar a tu "
    "IP pública: si falla, pruébalo desde fuera."]), False)
sv._generado_texto("✔ Programa creado. Envíaselo al cliente sin cambiarle el nombre.")
for b in (sv.btn_carpeta, sv.btn_instr):
    b.setEnabled(True)
capturar_servidor(sv, "datos")
sv.close()
db.fijar_servidor(ar.Servidor("midominio.ddns.net", CLAVE))
dlg.resize(984, 520)
dlg.nuevo_equipo()
captura(dlg, "AR_984_nuevo_con_servidor")
dlg.cancelar_edicion()

# 5.0.0: Empresas y usuarios, sus fichas y el aviso de equipo nuevo
from msys.ui import clientes_dialog as cd  # noqa: E402
from msys.domain import clientes as cl  # noqa: E402
cdb = cl.ClientesDB(db.path)
for empresa, (contacto, correo, telefono) in CONTACTOS.items():
    cdb.asegurar(empresa, contacto, correo, telefono)
cdb.asegurar("Asesoría del Norte", "Javier Martín Rodríguez", "javier.martin@asesorianorte.example", "")
cv = cd.ClientesDialog(dlg, cdb)
for ancho, alto in ((900, 440), (980, 600)):
    cv.resize(ancho, alto)
    cv.show()
    captura(cv, f"AR_empresas_{ancho}")
cv.close()
for nombre, ficha in (("empresa", cd.FichaDialog(dlg, cdb, empresa=cdb.empresa("Asesoría del Norte"))),
                      ("usuario", cd.FichaDialog(dlg, cdb, usuario=cdb.usuario("Clínica Dental Mediterráneo",
                                                                               "Luis Herrera Cabrera")))):
    ficha.show()
    captura(ficha, f"AR_ficha_{nombre}")
    ficha.close()
aviso = ad.EquipoNuevoDialog(Host(), sr.EquipoServidor("12345678", "31/10/2026 09:23", "192.168.1.75"), db)
aviso.show()
captura(aviso, "AR_equipo_nuevo")
aviso.close()

h = ad.HistorialAccesoDialog(dlg, db)
for ancho, alto in ((984, 520), (1100, 560)):
    h.resize(ancho, alto)
    h.show()
    captura(h, f"AR_historial_{ancho}")
h.close()
dlg.hide()

print("\n".join(problemas) if problemas else "SIN TEXTOS RECORTADOS")
print("capturas en", SALIDA)
