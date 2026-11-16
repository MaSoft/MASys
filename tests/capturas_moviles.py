# -*- coding: utf-8 -*-
"""Capturas + auditoría de textos recortados de 📱 Móviles Android y 🚪 Puertos desde fuera (5.3.0).
Datos de ejemplo en una movil.db y una multisys.db TEMPORALES (nunca las reales); no se abre ningún puerto.

Uso (desde la carpeta de MASYS):  python tests/capturas_moviles.py [carpeta] [--dark]
Debe terminar con «SIN TEXTOS RECORTADOS»."""
import json
import os
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "tests"))
args = [a for a in sys.argv[1:] if not a.startswith("--")]
SALIDA = os.path.abspath(args[0]) if args else os.path.join(tempfile.gettempdir(), "msys_capturas_moviles")
OSCURO = "--dark" in sys.argv
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

from PyQt5.QtGui import QFont  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMessageBox, QWidget  # noqa: E402

app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setFont(QFont("Segoe UI", 9))
from msys.widgets.delegates import DARK_STYLESHEET, LIGHT_STYLESHEET  # noqa: E402
app.setStyleSheet(DARK_STYLESHEET if OSCURO else LIGHT_STYLESHEET)

from auditoria_ui import auditar  # noqa: E402
from msys.domain import clientes as cl  # noqa: E402
from msys.movil import almacen as am  # noqa: E402
from msys.movil import servidor  # noqa: E402
from msys.ui import movil as mv  # noqa: E402

problemas = []
mv.MovilesDialog._aviso_caja = lambda s, t, *a: problemas.append(f"aviso inesperado: {t[:100]}")
mv.MovilesDialog._confirmar = lambda s, t: False
TMP = tempfile.mkdtemp()


class Host(QWidget):
    def __init__(self):
        super().__init__()
        self.movil_almacen = am.Almacen(os.path.join(TMP, "movil.db"), proteger=lambda b: b, desproteger=lambda b: b)
        self.movil_almacen.fijar_ajuste("copias_carpeta", os.path.join(TMP, "COPIAS"))
        self.movil_puente = mv._Puente()
        self.movil_servidor = servidor.ServidorMovil(self.movil_almacen, puerto=0)

    def _dash_colors(self):
        return {"title": "#e8ecf1", "muted": "#8a94a6"} if OSCURO else {"title": "#1a2634", "muted": "#8a94a6"}

    def set_status(self, t):
        pass

    def movil_activar_enlace(self, activar, padre=None):
        return False

    def abrir_puertos_movil(self):
        pass


def preparar(host):
    alm = host.movil_almacen
    with open(os.path.join(RAIZ, "tests", "datos", "informe_movil_132.json"), encoding="utf-8") as f:
        inf = json.load(f)
    for id_, nombre, emp, usu in (("m1", "Redmi Note 13 Pro de Recepción", "Empresa Demo SL", "Ana García"),
                                  ("m2", "Pixel 7", "Empresa Demo SL", "Luis Pérez"),
                                  ("m3", "Galaxy A54", "Bodega Los Llanos", "Eva"), ("m4", "Moto g84", "", "")):
        alm.guardar_dispositivo(id_, nombre, "wifi", clave=b"k" * 32, modelo=nombre)
        if emp:
            alm.asignar(id_, emp, usu)
    viejo = dict(inf, generado=inf["generado"] - 7 * 86_400_000, apps=inf["apps"][1:])
    alm.guardar_informe("m1", viejo, "wifi")
    alm.guardar_informe("m1", inf, "wifi")
    alm.guardar_datos_personales("m1", {
        "nivel": "completo", "fecha": inf["generado"],
        "contactos": {"permiso": True, "numero": 2, "lista": [
            {"nombre": "Ana García", "telefonos": ["600 112 233"], "correos": ["ana@ejemplo.es"], "cuentas": ["Google"], "favorito": True},
            {"nombre": "Taller", "telefonos": ["922 000 000"], "correos": [], "cuentas": ["Teléfono"], "favorito": False}]},
        "llamadas": {"permiso": True, "numero": 1, "lista": [
            {"numero": "600 112 233", "nombre": "Ana García", "tipo": "Entrante", "fecha": inf["generado"], "duracion": 125}]},
        "sms": {"permiso": True, "numero": 1, "conversaciones": 1, "lista": [
            {"direccion": "BANCO", "texto": "Su código de verificación es 123456", "fecha": inf["generado"], "tipo": "Recibido", "leido": True}]}})
    alm.fijar_nivel_datos(am.NIVEL_COMPLETO)
    # Puertos: uno hecho, uno a la espera tras un intento sin datos móviles, uno en un móvil
    a = alm.nuevo_encargo(am.SIN_MOVIL, "puerto", {"host": "", "puertos": [3389, 443, 80]})
    alm.encargos_para("m3")
    alm.resultado_encargo("m3", a, {"host": "198.51.100.200", "red": "datos móviles",
                                     "puertos": [{"puerto": 3389, "estado": "Cerrado"}, {"puerto": 443, "estado": "Abierto"},
                                                 {"puerto": 80, "estado": "Sin respuesta (filtrado)"}]})
    b = alm.nuevo_encargo(am.SIN_MOVIL, "puerto", {"host": "oficina.ejemplo.es", "puertos": [21115, 21116]})
    alm.encargos_para("m2")
    alm.resultado_encargo("m2", b, {"error": "Sin datos móviles: hace falta para probar desde fuera", "red": "ninguna"})
    alm.nuevo_encargo(am.SIN_MOVIL, "puerto", {"host": "", "puertos": [22]})
    alm.encargos_para("m1")


def guardar(w, nombre):
    os.makedirs(SALIDA, exist_ok=True)
    app.processEvents()
    w.grab().save(os.path.join(SALIDA, f"{nombre}{'_oscuro' if OSCURO else ''}.png"))
    auditar(w, nombre, problemas)


def main():
    host = Host()
    preparar(host)
    clientes = cl.ClientesDB(os.path.join(TMP, "multisys.db"))
    dlg = mv.MovilesDialog(host, clientes=clientes)
    dlg.resize(1320, 760)
    dlg.show()
    app.processEvents()
    guardar(dlg, "01_moviles_plegado")
    dlg.recargar(seleccionar="m1")
    app.processEvents()
    for i in range(dlg.tabs.count()):
        dlg.tabs.setCurrentIndex(i)
        app.processEvents()
        titulo = "".join(c for c in dlg.tabs.tabText(i) if c.isalnum())
        guardar(dlg, f"02_{i:02d}_{titulo}")
    # Subpestañas de Red y Datos personales
    from PyQt5.QtWidgets import QTabWidget
    for nombre_tab in ("📶 Red", "👥 Datos personales"):
        idx = [dlg.tabs.tabText(i) for i in range(dlg.tabs.count())].index(nombre_tab)
        dlg.tabs.setCurrentIndex(idx)
        interior = dlg.tabs.widget(idx).findChild(QTabWidget)
        for j in range(interior.count()):
            interior.setCurrentIndex(j)
            app.processEvents()
            guardar(dlg, f"03_{''.join(c for c in nombre_tab if c.isalnum())}_{j}")
    # Ancho mínimo
    dlg.resize(dlg.minimumWidth(), dlg.minimumHeight())
    dlg.tabs.setCurrentIndex(0)
    app.processEvents()
    guardar(dlg, "04_minima")
    # Asignar y puertos
    asg = mv.AsignarDialog(dlg, clientes, "¿De quién es «Moto g84»?", "Empresa Demo SL", "Ana García")
    asg.show()
    guardar(asg, "05_asignar")
    asg.close()
    emp = mv.EmparejarDialog({"t": "masys-pair", "v": 1, "key": "A" * 44, "pair": "prueba", "port": 47880,
                              "ips": ["192.168.1.50"], "name": "PC-PRUEBA"}, host.movil_puente, dlg,
                             quien="Empresa Demo SL › Ana García")
    emp.show()
    guardar(emp, "05b_emparejar")
    emp.close()
    pto = mv.PuertosDialog(host)
    pto.show()
    app.processEvents()
    guardar(pto, "06_puertos")
    pto.resize(pto.minimumWidth(), pto.minimumHeight())
    app.processEvents()
    guardar(pto, "07_puertos_minima")
    if problemas:
        print("TEXTOS RECORTADOS / PROBLEMAS:")
        for p in problemas:
            print(" -", p)
        sys.exit(1)
    print(f"SIN TEXTOS RECORTADOS · capturas en {SALIDA}")


if __name__ == "__main__":
    main()
