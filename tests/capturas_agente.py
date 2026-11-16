# -*- coding: utf-8 -*-
"""Capturas + auditoría de textos recortados del agente de análisis (4.7.0):
ventana del cliente (código, progreso y final), «📥 Informes del agente»,
«🔑 Código del técnico» y «⇄ Cambios». Sin recolectores reales ni navegador.

Uso (desde la carpeta de MASYS):  python tests/capturas_agente.py [carpeta] [--dark]
Debe terminar con «SIN TEXTOS RECORTADOS»."""
import os
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "tests"))
args = [a for a in sys.argv[1:] if not a.startswith("--")]
SALIDA = os.path.abspath(args[0]) if args else os.path.join(tempfile.gettempdir(), "msys_capturas_agente")
OSCURO = "--dark" in sys.argv
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

from PyQt5.QtGui import QFont  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setFont(QFont("Segoe UI", 9))
from msys.widgets.delegates import DARK_STYLESHEET, LIGHT_STYLESHEET  # noqa: E402
app.setStyleSheet(DARK_STYLESHEET if OSCURO else LIGHT_STYLESHEET)

from auditoria_ui import auditar  # noqa: E402
from msys.domain import agente as ag  # noqa: E402
from msys.domain import visitas  # noqa: E402
from msys.models import rows as R  # noqa: E402
from msys.ui import informes_agente_dialog as idl  # noqa: E402
from msys.agente.ventana import VentanaAgente  # noqa: E402

problemas = []
idl.QDesktopServices.openUrl = staticmethod(lambda url: None)


def captura(w, nombre):
    for _ in range(8):
        app.processEvents()
    w.grab().save(os.path.join(SALIDA, f"{nombre}{'_oscuro' if OSCURO else ''}.png"))
    auditar(w, nombre, problemas)


def soft(nombre, version):
    return R.InstalledSoftwareRow(nombre, version, "Editor", "01/10/2026", "C:\\Program Files\\x")


def informe(equipo, fin, programas):
    inf = ag.nuevo_informe([c for c, *_r in ag.LECTORES])
    inf["equipo"], inf["fin"], inf["usuario"], inf["administrador"] = equipo, fin, "recepcion", True
    ag.anadir_seccion(inf, "software", programas)
    ag.anadir_seccion(inf, "sistema", {"Equipo": equipo, "Sistema operativo": "Windows 11 Pro 24H2", "Memoria": "16 GB"})
    ag.anadir_seccion(inf, "red_local", None, error="Tiempo agotado")
    return inf


os.makedirs(SALIDA, exist_ok=True)
db = ag.AgenteDB(os.path.join(tempfile.mkdtemp(), "multisys.db"))
codigo = ag.codigo_tecnico(db.publica_activa())

# ── Ventana del agente (la del cliente) ─────────────────────────────────
v = VentanaAgente(es_admin=False)
for ancho, alto in ((640, 560), (800, 640)):
    v.resize(ancho, alto)
    v.show()
    v.arbol.collapseAll()
    captura(v, f"AG_{ancho}_inicio")
    v.txt_codigo.setText(codigo)
    v.arbol.expandAll()
    captura(v, f"AG_{ancho}_codigo")
    v.paginas.setCurrentIndex(1)
    v._progreso(12, 33, "Programas instalados")
    captura(v, f"AG_{ancho}_progreso")


def falso(clase, args):
    if clase == "InstalledSoftwareCollector":
        return [soft("7-Zip", "23.01")], "", 0.1
    return {"Dato": "valor"}, ("Sin acceso" if clase == "NetworkDeviceCollector" else ""), 0.1


carpeta = tempfile.mkdtemp()
v.analizar(ejecutar=falso, carpeta=carpeta)
for ancho, alto in ((640, 560), (800, 640)):
    v.resize(ancho, alto)
    captura(v, f"AG_{ancho}_final")
v.hide()

# ── Informes del agente (MASys del técnico) ─────────────────────────────
pub = db.publica_activa()
for equipo, cliente, fin, programas in (
        ("RECEPCION-01", "Asesoría del Norte", "2026-10-01T09:30:00", [soft("7-Zip", "22.01"), soft("Java 8", "8.0")]),
        ("RECEPCION-01", "Asesoría del Norte", "2026-10-27T10:15:00", [soft("7-Zip", "23.01"), soft("Chrome", "130")]),
        ("MOSTRADOR", "Clínica Dental Mediterráneo", "2026-10-20T12:00:00", [soft("Office", "16")])):
    db.guardar_informe(ag.descifrar(ag.cifrar(informe(equipo, fin, programas), pub), db.privadas()), cliente)

from msys.domain import agente_envio as envio  # noqa: E402
from msys.ui import recepcion_agente as rec  # noqa: E402
envio.regla_firewall_existe = lambda puerto=0: True
r = rec.RecepcionAgente(db, puerto=0, direccion="127.0.0.1")
d = idl.InformesAgenteDialog(None, db, recepcion_=r)
for ancho, alto in ((984, 520), (1100, 600)):
    d.resize(ancho, alto)
    d.show()
    d.arbol.setCurrentItem(d.arbol.topLevelItem(0).child(0).child(0))
    captura(d, f"AG_informes_{ancho}")

r.activar()
d._pintar_recepcion()
captura(d, "AG_informes_recibiendo")
ce = rec.CodigoEnvioDialog(d, r, ["Asesoría del Norte", "Clínica Dental Mediterráneo"], "Asesoría del Norte")
ce.direccion.setText("oficina-ejemplo.ddns.net")
ce.crear()
ce.show()
captura(ce, "AG_codigo_envio")
ce.close()
# «⚙️ Conexión…» (4.8.2), con códigos sin usar y el resultado de comprobar la dirección
vp = rec.ConexionDialog(d, r)
vp._comprobar = lambda d: (False, "⚠️ oficina-ejemplo.ddns.net apunta a 203.0.113.7, pero tu IP pública es 203.0.113.20: "
                                   "el DNS dinámico no se ha actualizado (revisa el router o el proveedor).")
vp.comprobar()
vp.show()
captura(vp, "AG_puerto")
vp.close()

# Agente con código de envío: «enviado» y «no se pudo enviar»
v2 = VentanaAgente(es_admin=True)
v2.resize(640, 560)
v2.show()
v2.txt_codigo.setText(ce.txt.text())
captura(v2, "AG_640_codigo_envio")
v2.analizar(ejecutar=falso, carpeta=tempfile.mkdtemp(), enviar=lambda *a: None)
captura(v2, "AG_640_enviado")


def _falla(*a):
    raise ag.ErrorAgente("No se pudo conectar con el equipo del técnico (¿está encendido y con MASys recibiendo?): "
                         "timed out")


v2.analizar(ejecutar=falso, carpeta=tempfile.mkdtemp(), enviar=_falla)
captura(v2, "AG_640_no_enviado")
v2.hide()
r.desactivar(recordar=False)

c = idl.CodigoTecnicoDialog(d, db)
c.show()
captura(c, "AG_codigo_tecnico")
c.close()

iid = d.arbol.topLevelItem(0).child(0).child(0).data(0, idl._ROL_ID)
lista, a, b = d.cambios(iid)
cd = idl.CambiosDialog(d, lista, a, b)
for ancho, alto in ((984, 520),):
    cd.resize(ancho, alto)
    cd.show()
    captura(cd, f"AG_cambios_{ancho}")
cd.close()

# HTML del informe (para mirarlo en Edge)
with open(os.path.join(SALIDA, "AG_informe.html"), "w", encoding="utf-8") as f:
    f.write(idl.html_informe(db.informe(iid), "Asesoría del Norte"))
# 5.2.0: aviso de informe recibido por Internet («🖥️ Ver en MASys»)
from PyQt5.QtWidgets import QWidget  # noqa: E402
from msys.ui.equipo_remoto import InformeRecibidoDialog  # noqa: E402
aviso = InformeRecibidoDialog(QWidget(), "Clínica Dental Mediterráneo", "PC-RECEPCION-02", "x", lambda i: {})
aviso.show()
captura(aviso, "AG_informe_recibido")
aviso.close()
print(visitas.resumen(lista))
print("\n".join(problemas) if problemas else "SIN TEXTOS RECORTADOS")
print("capturas en", SALIDA)
