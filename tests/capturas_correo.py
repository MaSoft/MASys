# -*- coding: utf-8 -*-
"""Capturas + auditoría de textos recortados de 📧 Cuentas Correo (4.9.0): las 6 pestañas con datos de ejemplo
(example.com), los diálogos (configuración encontrada, añadir a Outlook, ajustes, detalle, cabeceras) y el informe HTML.
Sin red ni Outlook: los resultados se pintan con datos inventados; multisys.db temporal. El visor abre un PST público
si está la variable MASYS_PST_TESTDATA (ver tests/test_correo_pst.py).

Uso (desde la carpeta de MASYS):  python tests/capturas_correo.py [carpeta] [--dark]
Debe terminar con «SIN TEXTOS RECORTADOS»."""
import os
import sys
import tempfile
import time

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "tests"))
args = [a for a in sys.argv[1:] if not a.startswith("--")]
SALIDA = os.path.abspath(args[0]) if args else os.path.join(tempfile.gettempdir(), "msys_capturas_correo")
OSCURO = "--dark" in sys.argv
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

from PyQt5.QtCore import QThread  # noqa: E402
from PyQt5.QtGui import QFont, QFontMetrics  # noqa: E402
from PyQt5.QtWidgets import (QApplication, QMessageBox, QProgressBar, QTabBar, QTreeWidgetItem,  # noqa: E402
                             QVBoxLayout, QWidget)

app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setFont(QFont("Segoe UI", 9))
from msys.widgets.delegates import DARK_STYLESHEET, LIGHT_STYLESHEET  # noqa: E402
app.setStyleSheet(DARK_STYLESHEET if OSCURO else LIGHT_STYLESHEET)

from auditoria_ui import auditar  # noqa: E402
from msys.domain.correo import direcciones as dirs  # noqa: E402
from msys.domain.correo import dominio as dom  # noqa: E402
from msys.domain.correo import informe as inf  # noqa: E402
from msys.domain.correo import outlook_cfg as olk  # noqa: E402
from msys.domain.correo import proveedores as prov  # noqa: E402
from msys.domain.correo import servidores as srv  # noqa: E402
from msys.domain.correo.ajustes import CorreoDB  # noqa: E402
from msys.ui import correo as uc  # noqa: E402
from msys.ui import correo_outlook as uo  # noqa: E402
from msys.ui.email_accounts import EmailMixin  # noqa: E402

problemas, avisos = [], []
DB = CorreoDB(os.path.join(tempfile.mkdtemp(), "multisys.db"))   # nunca la multisys.db real
uc.CorreoDB = lambda: DB
# Outlook de ejemplo (no se lee el registro de este equipo)
olk.profiles = lambda: [olk.Profile("Outlook", "16.0"), olk.Profile("Recepción", "16.0")]
olk.versions = lambda: ["16.0"]
olk.default_profile = lambda: "Outlook"
olk.rules_keys = lambda v: [rf"HKEY_CURRENT_USER\Software\Microsoft\Office\{v}\Outlook\Rules\Facturas a carpeta"]
olk.signatures = lambda: ["Firma corta", "Firma completa con logotipo"]
uo.olk = olk


class Host(QWidget, EmailMixin, uc.CorreoMixin):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.progress_bar = QProgressBar()
        self._proveedores_export = {}

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


def esperar(segundos=30):
    fin = time.time() + segundos
    while time.time() < fin:
        app.processEvents()
        if not any(isinstance(o, QThread) and o.isRunning() for o in app.allWidgets() + list(_hilos())):
            break
        time.sleep(0.02)
    for _ in range(10):
        app.processEvents()


def _hilos():
    for w in (host.correo_probar, host.correo_direcciones, host.correo_dominio, host.correo_visor):
        for v in vars(w).values():
            if isinstance(v, QThread):
                yield v


def auditar_pestanas(raiz, nombre):
    for b in raiz.findChildren(QTabBar):
        if not b.isVisible():
            continue
        fm = QFontMetrics(b.font())
        for i in range(b.count()):
            if fm.horizontalAdvance(b.tabText(i)) + 2 * 16 + 8 > b.tabRect(i).width():
                problemas.append(f"{nombre}: pestaña '{b.tabText(i)}' cortada")


def captura(w, nombre):
    for _ in range(10):
        app.processEvents()
    w.grab().save(os.path.join(SALIDA, f"{nombre}{'_oscuro' if OSCURO else ''}.png"))
    auditar(w, nombre, problemas)
    auditar_pestanas(w, nombre)


os.makedirs(SALIDA, exist_ok=True)
host = Host()
pagina = host.crear_tab_email_accounts()
lay = QVBoxLayout(host)
lay.setContentsMargins(0, 0, 0, 0)
lay.addWidget(pagina)

# ── Datos de ejemplo ────────────────────────────────────────────────────
pc = host.correo_probar
pc.correo.setText("ana@example.com")
pc.clave.setText("no-es-una-clave")
pc.lbl_encontrado.setText("🔎 Gmail · por su servidor de correo (MX)")
pc.rellenar(["Gmail"] + [p.name for p in prov.PROVIDERS if p.name != "Gmail"])
pc._poner("Gmail", 1, "OK", "* OK Gimap ready")
pc._poner("Gmail", 2, "Credenciales", "-ERR [AUTH] Username and password not accepted")
pc._poner("Gmail", 3, "OK · certificado no válido", "certificate verify failed")
pc.servidor.setText("mail.example.com")
pc.lbl_servidor.setText("IMAP mail.example.com:993  →  Sin conexión   ·   [WinError 10061] No se puede establecer una conexión")

dw = host.correo_direcciones
r1 = dirs.EmailResult("ana@example.com", dirs.EXISTS, "El servidor acepta la dirección (250)",
                      [dirs.Step("Forma", "ok", "Bien escrita"), dirs.Step("Dominio", "ok", "Recibe correo en mx.example.com"),
                       dirs.Step("Buzón", "ok", "mx.example.com acepta la dirección: 250 2.1.5 OK")], ["mx.example.com"])
r2 = dirs.EmailResult("info@example.net", dirs.UNKNOWN, "El servidor acepta cualquier dirección del dominio (también una inventada)",
                      [dirs.Step("Forma", "ok", "Bien escrita"), dirs.Step("Aviso", "info", "Es una dirección de grupo o de departamento"),
                       dirs.Step("Buzón", "warn", "El servidor acepta cualquier dirección del dominio")], ["mx.example.net"],
                      ["de grupo", "acepta todo"])
r3 = dirs.EmailResult("nadie@example.org", dirs.MISSING, "El servidor dice que no existe (550)",
                      [dirs.Step("Buzón", "bad", "mx.example.org: 550 5.1.1 user unknown")], ["mx.example.org"])
r4 = dirs.EmailResult("ana@@example", dirs.INVALID, "Tiene que llevar una sola @", [dirs.Step("Forma", "bad", "Tiene que llevar una sola @")])
for r in (r1, r2, r3):
    DB.anadir_historial("direccion", r.address, r.verdict, r.to_dict())
dw._cargar_historial()
dw.detalle.setHtml(uc.pasos_html(r2))
dw.pegadas.setPlainText("ana@example.com\ninfo@example.net\nnadie@example.org\nana@@example")
dw.resultados = [r1, r2, r3, r4]
dw._pintar_lista()

dmw = host.correo_dominio
rep = dom.Report("example.com", "29/10/2026 10:30", [
    dom.Check("dns", "Dominio y DNS", "ok", "2 servidores DNS", ["NS a.iana-servers.net", "NS b.iana-servers.net"]),
    dom.Check("mx", "Servidores de correo (MX)", "ok", "1 servidor(es): mx.example.com", ["MX 10 mx.example.com  →  192.0.2.10"],
              items=[("info", "Un solo MX: si cae, el correo se reintenta desde el que lo envía")]),
    dom.Check("spf", "SPF (quién puede enviar)", "warn", "Termina en «?all» (neutro): no protege", ["v=spf1 include:_spf.example.net ?all"],
              "Termínalo en «-all» o «~all».", [("ok", "Gasta 2 consultas DNS de 10 permitidas")]),
    dom.Check("dmarc", "DMARC (qué hacer con el correo falso)", "bad", "No tiene DMARC", [],
              "Crea un TXT en _dmarc.example.com empezando por «v=DMARC1; p=none; rua=mailto:<un buzón tuyo>»."),
    dom.Check("dkim", "DKIM (firma de los correos)", "info", "No se encontró con los selectores habituales"),
    dom.Check("smtp", "Servidor de correo (SMTP)", "info", "No se puede comprobar desde tu red (puerto 25 cerrado o sin respuesta)"),
    dom.Check("bl", "Listas negras", "ok", "No aparece (1 IP y el dominio)", ["IP comprobadas: 192.0.2.10"]),
])
dmw.dominio.setText("example.com")
dmw.mostrar(rep)
DB.anadir_historial("dominio", rep.domain, "1/1/3", rep.to_dict())

ow = host.correo_outlook
ow.destino.setText(os.path.join(tempfile.gettempdir(), "OutlookExport"))
ow.cargar()
ow.arbol.expandAll()
ow.arbol.setCurrentItem(ow.arbol.topLevelItem(0))

vw = host.correo_visor
PST = os.path.join(os.environ.get("MASYS_PST_TESTDATA", ""), "enron.pst")
if os.path.isfile(PST):
    vw.abrir_ruta(PST)
    esperar(60)
    esperar(30)
    if vw.modelo.rowCount():
        vw.tabla.selectRow(0)
        esperar(10)

# ── Capturas de cada pestaña ────────────────────────────────────────────
for ancho, alto in ((1300, 720), (1650, 900)):
    host.resize(ancho, alto)
    host.show()
    esperar(5)
    for i in range(host.correo_tabs.count()):
        host.correo_tabs.setCurrentIndex(i)
        esperar(5)
        captura(host, f"Correo_{i}_{ancho}")
    host.correo_tabs.setCurrentIndex(2)
    dw.modo.setCurrentIndex(1)
    esperar(3)
    captura(host, f"Correo_2_lista_{ancho}")
    dw.modo.setCurrentIndex(0)

print("Exportación por pestaña:")
for i in range(host.correo_tabs.count()):
    host.correo_tabs.setCurrentIndex(i)
    s = host._correo_export()
    print(f"  {host.correo_tabs.tabText(i)}: {[(t, len(f)) for t, _c, f in s]}")

# ── Diálogos ────────────────────────────────────────────────────────────
res = {"IMAP": srv.Result("OK", "", True), "POP3": srv.Result("Credenciales", "", True), "SMTP": srv.Result("OK", "", False)}
d = uc.ConfiguracionDialog(host, host, prov.BY_NAME["Gmail"], "ana@example.com", res)
d.show()
captura(d, "Correo_dlg_configuracion")
d.close()
d = uc.AnadirOutlookDialog(host, host, prov.BY_NAME["Gmail"], "ana@example.com")
d.show()
captura(d, "Correo_dlg_anadir_outlook")
d.close()
d = uc.AjustesCorreoDialog(host, DB)
d.show()
captura(d, "Correo_dlg_ajustes")
d.close()
d = uc.PasosDialog(host, r2)
d.show()
captura(d, "Correo_dlg_detalle")
d.close()
d = uo.CabecerasDialog(host, "Received: from mx.example.com\nFrom: Ana <ana@example.com>\nSubject: =?utf-8?q?Prueba?=")
d.show()
captura(d, "Correo_dlg_cabeceras")
d.close()

with open(os.path.join(SALIDA, "Correo_informe.html"), "w", encoding="utf-8") as f:
    f.write(inf.domain_html(rep))
print("\n".join(problemas) if problemas else "SIN TEXTOS RECORTADOS")
if avisos:
    print("AVISOS MOSTRADOS:\n  " + "\n  ".join(avisos))
print("capturas en", SALIDA)
