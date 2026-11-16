# -*- coding: utf-8 -*-
"""Capturas + auditoría de textos recortados del correo a los clientes (5.1.0): «✉️ Correo» (cuentas de los tres
tipos, plantillas y enviados) y «✉️ Enviar correo». Datos de ejemplo inventados en una base temporal; nunca envía.

Uso (desde la carpeta de MASYS):  python tests/capturas_correo_envio.py [carpeta] [--dark]
Debe terminar con «SIN TEXTOS RECORTADOS»."""
import os
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
sys.path.insert(0, os.path.join(RAIZ, "tests"))
args = [a for a in sys.argv[1:] if not a.startswith("--")]
SALIDA = os.path.abspath(args[0]) if args else os.path.join(tempfile.gettempdir(), "msys_capturas_correo_envio")
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
from msys.domain import clientes as cl  # noqa: E402
from msys.domain import envio_correo as ec  # noqa: E402
from msys.ui import correo_envio_dialog as cd  # noqa: E402

problemas = []


def captura(w, nombre):
    for _ in range(8):
        app.processEvents()
    w.grab().save(os.path.join(SALIDA, f"{nombre}{'_oscuro' if OSCURO else ''}.png"))
    auditar(w, nombre, problemas)


os.makedirs(SALIDA, exist_ok=True)
ruta = os.path.join(tempfile.mkdtemp(), "multisys.db")
cdb, cat = ec.CorreoDB(ruta), cl.ClientesDB(ruta)
cdb.guardar_cuenta(ec.Perfil(nombre="Soporte Masoft", tipo=ec.SMTP, remitente="soporte@masoft.example",
                             nombre_remitente="Carlos · Masoft", servidor="smtp.masoft.example", puerto=587), "x")
cdb.guardar_cuenta(ec.Perfil(nombre="Gmail personal", tipo=ec.GMAIL, remitente="tecnico.ejemplo@gmail.com"), "x")
cdb.guardar_cuenta(ec.Perfil(nombre="Microsoft 365", tipo=ec.M365, remitente="soporte@contoso.example",
                             tenant="contoso.onmicrosoft.com", cliente_id="11111111-2222-3333-4444-555555555555"), "x")
for para, asunto, res in (("luis@clinica.example", "Acceso remoto para Clínica Dental Mediterráneo", "Enviado"),
                          ("marta@asesoria.example", "Revisión de tu equipo · Asesoría del Norte",
                           "Error: Usuario o contraseña rechazados (Gmail: usa una contraseña de aplicación)")):
    cdb.registrar("Soporte Masoft", [para], asunto, res, "Clínica Dental Mediterráneo", "Luis Herrera Cabrera")
cat.asegurar("Clínica Dental Mediterráneo", "Luis Herrera Cabrera", "recepcion@clinicamediterraneo.example")
cat.asegurar("Asesoría del Norte", "Marta Gil", "marta.gil@asesorianorte.example")

v = cd.CorreoDialog(None, cdb)
for ancho, alto in ((900, 520), (1040, 640)):
    v.resize(ancho, alto)
    v.show()
    for fila in range(v.lista_cuentas.count()):
        v.lista_cuentas.setCurrentRow(fila)
        v.pestanas.setCurrentIndex(0)
        captura(v, f"CO_cuentas_{ancho}_{fila + 1}")
    v._resultado("✖ Usuario o contraseña rechazados (Gmail: usa una contraseña de aplicación, no la de tu cuenta): "
                 "5.7.8 Username and Password not accepted", False)
    captura(v, f"CO_cuentas_{ancho}_error")
    v.pestanas.setCurrentIndex(1)
    for fila in range(v.lista_plantillas.count()):
        v.lista_plantillas.setCurrentRow(fila)
        captura(v, f"CO_plantillas_{ancho}_{fila + 1}")
    v.pestanas.setCurrentIndex(2)
    captura(v, f"CO_enviados_{ancho}")
v.close()

for plantilla in ("Programa de RustDesk", "Agente de análisis"):
    r = cd.RedactarDialog(None, cdb, cat, {"empresa": "Clínica Dental Mediterráneo", "usuario": "Luis Herrera Cabrera",
                                           "correo": "recepcion@clinicamediterraneo.example", "equipo": "Mostrador"},
                          plantilla, codigo=lambda: "MSA1-AB12C-DE34F-GH56J-KL78M")
    r.enlace.setText("https://1drv.ms/u/s!ejemplo/rustdesk")
    r._aplicar_plantilla()
    for ancho, alto in ((720, 560), (820, 640)):
        r.resize(ancho, alto)
        r.show()
        captura(r, f"CO_redactar_{plantilla.split()[0].lower()}_{ancho}")
    r.close()

print("\n".join(problemas) if problemas else "SIN TEXTOS RECORTADOS")
print("capturas en", SALIDA)
