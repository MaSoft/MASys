# -*- coding: utf-8 -*-
"""Pruebas de MASys 5.1.0: envío de correos a los clientes. NUNCA se conecta a nada: SMTP y Microsoft 365 simulados.
python -m unittest discover -s tests -p "test_*.py" """
import os
import sqlite3
import sys
import tempfile
import time
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

from msys.domain import clientes as cl  # noqa: E402
from msys.domain import envio_correo as ec  # noqa: E402

GUID = "11111111-2222-3333-4444-555555555555"


def _ruta():
    return os.path.join(tempfile.mkdtemp(), "multisys.db")


class SmtpFalso:
    """Imita smtplib.SMTP / SMTP_SSL y anota lo que se hace."""
    registro = []
    starttls_ofrecido = True
    fallo_login = None

    def __init__(self, host, port, timeout=None, context=None):
        self.registro.append(("conectar", host, port, "ssl" if context is not None else "plano"))

    def ehlo(self):
        pass

    def has_extn(self, nombre):
        return self.starttls_ofrecido

    def starttls(self, context=None):
        self.registro.append(("starttls",))

    def login(self, usuario, clave):
        if self.fallo_login:
            raise self.fallo_login
        self.registro.append(("login", usuario))

    def send_message(self, msg):
        self.registro.append(("enviar", msg["To"], msg["Subject"], msg["From"]))
        return {}

    def quit(self):
        pass


class Respuesta:
    def __init__(self, codigo, datos=None):
        self.status_code, self._datos = codigo, datos or {}

    def json(self):
        return self._datos


class HttpFalso:
    def __init__(self, respuestas):
        self.respuestas, self.llamadas = list(respuestas), []

    def post(self, url, **kw):
        self.llamadas.append((url, kw))
        return self.respuestas.pop(0)


class MotorTest(unittest.TestCase):
    def setUp(self):
        self.db = ec.CorreoDB(_ruta())
        SmtpFalso.registro, SmtpFalso.starttls_ofrecido, SmtpFalso.fallo_login = [], True, None

    def test_plantillas_iniciales_solo_una_vez(self):
        nombres = [t.nombre for t in self.db.plantillas()]
        self.assertEqual(nombres, ["Programa de RustDesk", "Agente de análisis", "En blanco"])
        self.db.eliminar_plantilla(self.db.plantillas()[2].id)
        self.assertEqual(len(ec.CorreoDB(self.db.path).plantillas()), 2)       # no vuelven al abrir otra vez
        t = self.db.guardar_plantilla(ec.Plantilla(0, "Aviso", "Hola {empresa}", "Texto"))
        self.db.recordar_enlace(t.id, " https://x.example/a ")
        self.assertEqual(self.db.plantillas()[-1].enlace, "https://x.example/a")
        with self.assertRaises(ValueError):
            self.db.guardar_plantilla(ec.Plantilla(0, " "))

    def test_cuentas_validacion_secreto_y_predeterminada(self):
        with self.assertRaises(ValueError):
            self.db.guardar_cuenta(ec.Perfil(nombre="G", tipo=ec.GMAIL, remitente="a@gmail.com"))      # sin contraseña
        g = self.db.guardar_cuenta(ec.Perfil(nombre="G", tipo=ec.GMAIL, remitente="a@gmail.com", servidor="x"),
                                   "abcd efgh ijkl mnop")
        self.assertEqual((g.servidor, g.puerto, g.seguridad), (ec.GMAIL_SERVIDOR, 587, ec.STARTTLS))   # preajuste
        self.assertTrue(self.db.cuentas()[0].predeterminado)                 # la primera, predeterminada
        with sqlite3.connect(self.db.path) as c:
            self.assertNotIn(b"abcd efgh", bytes(c.execute("SELECT secreto FROM correo_cuentas").fetchone()[0]))
        self.assertEqual(self.db.secreto(g.id), "abcd efgh ijkl mnop")
        errores = ec.errores_perfil(ec.Perfil(nombre="M", tipo=ec.M365, remitente="s@e.es", tenant="x", cliente_id="y"),
                                    False)
        self.assertEqual(len(errores), 3)
        m = self.db.guardar_cuenta(ec.Perfil(nombre="M", tipo=ec.M365, remitente="s@e.es", tenant="contoso.onmicrosoft.com",
                                             cliente_id=GUID), "secreto")
        s = self.db.guardar_cuenta(ec.Perfil(nombre="Relé", tipo=ec.SMTP, remitente="s@e.es", servidor="relay.e.local",
                                             puerto=25, seguridad=ec.SIN_CIFRAR))           # SMTP sin contraseña: vale
        for malo in (ec.Perfil(nombre="X", tipo=ec.SMTP, remitente="s@e.es", servidor="-x"),
                     ec.Perfil(nombre="X", tipo=ec.SMTP, remitente="s@e.es", servidor="h", puerto=0),
                     ec.Perfil(nombre="", tipo=ec.SMTP, remitente="nocorreo", servidor="h")):
            with self.assertRaises(ValueError):
                self.db.guardar_cuenta(malo)
        self.db.fijar_predeterminada(m.id)
        self.assertEqual(self.db.predeterminada().id, m.id)
        self.db.eliminar_cuenta(m.id)
        self.assertIn(self.db.predeterminada().id, (g.id, s.id))              # siempre queda una predeterminada
        # Guardar sin secreto conserva el guardado
        g.nombre = "Gmail"
        self.db.guardar_cuenta(g)
        self.assertEqual(self.db.secreto(g.id), "abcd efgh ijkl mnop")

    def test_rellenar_y_destinatarios(self):
        datos = ec.datos_basicos(ec.Perfil(nombre_remitente="Carlos"), empresa="Clínica", usuario="Luis")
        self.assertEqual(ec.rellenar("{usuario} de {empresa}: {tecnico} {raro}", datos), "Luis de Clínica: Carlos {raro}")
        self.assertRegex(datos["fecha"], r"^\d\d/\d\d/\d{4}$")
        self.assertEqual(ec.destinatarios("a@b.es; c@d.es,e@f.es"), ["a@b.es", "c@d.es", "e@f.es"])
        for malo in ("", "a@b.es; mal"):
            with self.assertRaises(ValueError):
                ec.destinatarios(malo)

    def test_envio_smtp(self):
        p = ec.Perfil(1, "Dominio", ec.SMTP, "soporte@masoft.example", "Carlos", "smtp.masoft.example", 587, ec.STARTTLS)
        m = ec.Mensaje(["cliente@e.example"], "Asunto", "Texto")
        ec.enviar(p, "clave", m, smtp=SmtpFalso, smtp_ssl=SmtpFalso)
        self.assertEqual([r[0] for r in SmtpFalso.registro], ["conectar", "starttls", "login", "enviar"])
        self.assertEqual(SmtpFalso.registro[2], ("login", "soporte@masoft.example"))   # usuario vacío = remitente
        self.assertIn("Carlos", SmtpFalso.registro[3][3])
        # STARTTLS pedido y no ofrecido: NO se envía la contraseña
        SmtpFalso.registro, SmtpFalso.starttls_ofrecido = [], False
        with self.assertRaises(ec.ErrorEnvio):
            ec.enviar(p, "clave", m, smtp=SmtpFalso, smtp_ssl=SmtpFalso)
        self.assertNotIn("login", [r[0] for r in SmtpFalso.registro])
        # SSL directo
        SmtpFalso.registro, SmtpFalso.starttls_ofrecido = [], True
        p.seguridad, p.puerto = ec.SSL_DIRECTO, 465
        ec.enviar(p, "clave", m, smtp=SmtpFalso, smtp_ssl=SmtpFalso)
        self.assertEqual(SmtpFalso.registro[0][3], "ssl")
        # Gmail con contraseña rechazada: pista de la contraseña de aplicación
        import smtplib
        SmtpFalso.fallo_login = smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")
        g = ec.Perfil(2, "G", ec.GMAIL, "a@gmail.com")
        with self.assertRaises(ec.ErrorEnvio) as ctx:
            ec.enviar(g, "x", m, smtp=SmtpFalso, smtp_ssl=SmtpFalso)
        self.assertIn("contraseña de aplicación", str(ctx.exception))

    def test_envio_microsoft_365(self):
        p = ec.Perfil(3, "M365", ec.M365, "soporte@contoso.example", tenant="contoso.onmicrosoft.com", cliente_id=GUID)
        m = ec.Mensaje(["a@b.example", "c@d.example"], "Hola", "Texto")
        http = HttpFalso([Respuesta(200, {"access_token": "TOKEN"}), Respuesta(202)])
        ec.enviar(p, "secreto", m, http=http)
        (url_token, kw_token), (url_envio, kw_envio) = http.llamadas
        self.assertIn("contoso.onmicrosoft.com/oauth2/v2.0/token", url_token)
        self.assertEqual(kw_token["data"]["grant_type"], "client_credentials")
        self.assertEqual(url_envio, "https://graph.microsoft.com/v1.0/users/soporte@contoso.example/sendMail")
        self.assertEqual(kw_envio["headers"]["Authorization"], "Bearer TOKEN")
        self.assertEqual([x["emailAddress"]["address"] for x in kw_envio["json"]["message"]["toRecipients"]],
                         ["a@b.example", "c@d.example"])
        http = HttpFalso([Respuesta(200, {"access_token": "T"}),
                          Respuesta(403, {"error": {"message": "Access is denied."}})])
        with self.assertRaises(ec.ErrorEnvio) as ctx:
            ec.enviar(p, "s", m, http=http)
        self.assertIn("Mail.Send", str(ctx.exception))
        http = HttpFalso([Respuesta(401, {"error_description": "AADSTS7000215: Invalid client secret"})])
        with self.assertRaises(ec.ErrorEnvio) as ctx:
            ec.enviar(p, "s", m, http=http)
        self.assertIn("AADSTS7000215", str(ctx.exception))

    def test_historial_y_deteccion(self):
        p = self.db.guardar_cuenta(ec.Perfil(nombre="Relé", tipo=ec.SMTP, remitente="s@e.es", servidor="relay.e.local",
                                             puerto=25, seguridad=ec.SIN_CIFRAR))
        m = ec.Mensaje(["a@b.es"], "Uno", "x")
        ec.aplicar(self.db, p, m, "Clínica", "Luis", enviar_fn=lambda *a: None)
        with self.assertRaises(ec.ErrorEnvio):
            ec.aplicar(self.db, p, m, enviar_fn=lambda *a: (_ for _ in ()).throw(ec.ErrorEnvio("caído")))
        h = self.db.enviados()
        self.assertEqual([x.resultado for x in h], ["Error: caído", "Enviado"])
        self.assertEqual((h[1].empresa, h[1].usuario), ("Clínica", "Luis"))
        from msys.domain.correo import servidores
        original = servidores.detect
        self.addCleanup(setattr, servidores, "detect", original)
        servidores.detect = lambda correo: servidores.Detection("gmail.com", names=["Gmail"])
        d = ec.detectar_smtp("x@gmail.com")
        self.assertEqual((d.servidor, d.puerto, d.seguridad), ("smtp.gmail.com", 587, ec.STARTTLS))
        servidores.detect = lambda correo: servidores.Detection("raro.example")
        self.assertIsNone(ec.detectar_smtp("x@raro.example"))


class VentanasTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        from msys.ui import correo_envio_dialog as cd
        self.cd = cd
        self.ruta = _ruta()
        self.cdb = ec.CorreoDB(self.ruta)
        self.catalogo = cl.ClientesDB(self.ruta)
        self.avisos, self.enviados = [], []
        self._hook = sys.excepthook
        sys.excepthook = lambda t, v, tb: self.avisos.append(f"EXCEPCIÓN {t.__name__}: {v}")

    def tearDown(self):
        sys.excepthook = self._hook

    def _esperar(self, v):
        fin = time.time() + 10
        while v._tarea is not None and v._tarea.isRunning() and time.time() < fin:
            self.app.processEvents()
        for _ in range(20):
            self.app.processEvents()

    def _silenciar(self, v):
        v._aviso = lambda texto, *_a: self.avisos.append(texto)
        v._confirmar = lambda *_a: True
        v._enviar = lambda p, s, m: self.enviados.append((p.nombre, s, m.para, m.asunto, m.cuerpo))
        return v

    def test_configuracion(self):
        v = self._silenciar(self.cd.CorreoDialog(None, self.cdb))
        v._detectar = lambda correo: ec.Perfil(servidor="smtp.masoft.example", puerto=465, seguridad=ec.SSL_DIRECTO)
        # Cambiar de tipo enseña los campos que tocan
        v.ed_tipo.setCurrentIndex(v.ed_tipo.findData(ec.M365))
        self.assertTrue(v.caja_m365.isVisibleTo(v) is False or not v.caja_smtp.isVisibleTo(v))
        self.assertIn("Mail.Send", v.lbl_ayuda.text())
        v.ed_tipo.setCurrentIndex(v.ed_tipo.findData(ec.SMTP))
        # Guardar con datos que faltan: avisa
        v.btn_guardar_cuenta.click()
        self.assertIn("Falta el nombre de la cuenta", self.avisos[-1])
        v.ed_nombre.setText("Soporte")
        v.ed_remitente.setText("soporte@masoft.example")
        v.ed_nombre_rem.setText("Carlos")
        v.btn_detectar.click()
        self._esperar(v)
        self.assertEqual((v.ed_servidor.text(), v.ed_puerto.value()), ("smtp.masoft.example", 465))
        v.ed_secreto.setText("clave")
        v.btn_probar.click()                        # guarda y envía una prueba al propio remitente
        self._esperar(v)
        self.assertEqual(self.enviados[-1][:3], ("Soporte", "clave", ["soporte@masoft.example"]))
        self.assertIn("✔ Enviado", v.lbl_resultado.text())
        self.assertEqual(v.modelo_enviados.rowCount(), 1)
        self.assertTrue(v.lista_cuentas.item(0).text().startswith("★ Soporte"))
        # Plantillas: nueva, guardar, quitar
        v.btn_nueva_plantilla.click()
        v.pl_nombre.setText("Recordatorio")
        v.pl_asunto.setText("Hola {usuario}")
        v.btn_guardar_plantilla.click()
        self.assertEqual(v.lista_plantillas.count(), 4)
        v.btn_quitar_plantilla.click()
        self.assertEqual(v.lista_plantillas.count(), 3)
        v.btn_quitar_cuenta.click()
        self.assertEqual(self.cdb.cuentas(), [])
        self.assertEqual([x for x in self.avisos if x.startswith("EXCEPCIÓN")], [])
        v.deleteLater()

    def test_redactar_y_enviar(self):
        self.cdb.guardar_cuenta(ec.Perfil(nombre="Soporte", tipo=ec.SMTP, remitente="soporte@masoft.example",
                                          nombre_remitente="Carlos", servidor="smtp.masoft.example"), "clave")
        self.catalogo.asegurar("Clínica Sur", "Luis Herrera", "luis@clinica.example", "600 111 222")
        v = self._silenciar(self.cd.RedactarDialog(None, self.cdb, self.catalogo, {"equipo": "Mostrador"},
                                                   "Programa de RustDesk", codigo=lambda: "MSA1-CODIGO"))
        self.assertTrue(v.fila_codigo.isHidden())                   # esta plantilla no usa {codigo}
        # Sin enlace: no se envía con {enlace} sin rellenar
        v.para.setEditText("luis@clinica.example")
        v.btn_enviar.click()
        self.assertIn("Falta el enlace", self.avisos[-1])
        # Elegir el destinatario del catálogo rellena empresa y usuario
        v.para.activated.emit(0)
        self.assertEqual(v.para.currentText(), "luis@clinica.example")
        self.assertTrue(v.asunto.text().endswith("Clínica Sur"))
        v.enlace.setText("https://onedrive.example/rustdesk")
        v.enlace.textEdited.emit(v.enlace.text())
        self.assertIn("https://onedrive.example/rustdesk", v.cuerpo.toPlainText())
        self.assertIn("Hola Luis Herrera", v.cuerpo.toPlainText())
        self.assertIn("Carlos", v.cuerpo.toPlainText())
        v.btn_enviar.click()
        self._esperar(v)
        self.assertTrue(v.enviado)
        self.assertEqual(self.enviados[-1][2], ["luis@clinica.example"])
        self.assertEqual(self.cdb.enviados()[0].empresa, "Clínica Sur")
        self.assertEqual(self.cdb.plantillas()[0].enlace, "https://onedrive.example/rustdesk")   # se recuerda
        # Plantilla del agente: pide el código y lo rellena solo
        v2 = self._silenciar(self.cd.RedactarDialog(None, self.cdb, self.catalogo, {}, "Agente de análisis",
                                                    codigo=lambda: "MSA1-CODIGO"))
        self.assertFalse(v2.fila_codigo.isHidden())
        self.assertIn("MSA1-CODIGO", v2.cuerpo.toPlainText())
        # Texto escrito a mano: cambiar el enlace no lo pisa
        v2.cuerpo.setPlainText("Texto mío")
        v2.enlace.setText("https://otro.example")
        v2.enlace.textEdited.emit(v2.enlace.text())
        self.assertEqual(v2.cuerpo.toPlainText(), "Texto mío")
        self.assertEqual([x for x in self.avisos if x.startswith("EXCEPCIÓN")], [])
        v.deleteLater()
        v2.deleteLater()

    def test_sin_cuentas(self):
        v = self._silenciar(self.cd.RedactarDialog(None, self.cdb, self.catalogo, {}, "En blanco"))
        self.assertIn("Aún no hay ninguna cuenta", v.lbl_estado.text())
        v.para.setEditText("a@b.es")
        v.btn_enviar.click()
        self.assertIn("Configura primero una cuenta", self.avisos[-1])
        v.deleteLater()

    def test_ganchos_en_acceso_remoto(self):
        from PyQt5.QtWidgets import QPushButton, QWidget
        from msys.domain import acceso_remoto as ar
        from msys.ui import acceso_remoto_dialog as ad
        abiertos = []
        originales = (self.cd.RedactarDialog.exec_, self.cd.CorreoDialog.exec_)
        self.cd.RedactarDialog.exec_ = lambda s: abiertos.append(("redactar", s.plantilla.currentText(),
                                                                   s.para.currentText())) or 0
        self.cd.CorreoDialog.exec_ = lambda s: abiertos.append(("correo",)) or 0
        self.addCleanup(setattr, self.cd.RedactarDialog, "exec_", originales[0])
        self.addCleanup(setattr, self.cd.CorreoDialog, "exec_", originales[1])
        db = ar.AccesoDB(self.ruta)
        db.guardar(ar.Equipo("e1", "Clínica Sur", "Mostrador", ar.RUSTDESK, "123456789", contacto="Luis Herrera",
                             correo="luis@clinica.example", servidor_propio=True))
        host = QWidget()
        dlg = ad.AccesoRemotoDialog(host, db)
        dlg._aviso = lambda texto, *_a: self.avisos.append(texto)
        dlg.btn_correo.click()
        dlg.recargar(seleccionar="e1")
        dlg._actual = "e1"
        dlg._mostrar_ficha()
        boton = next(b for b in dlg.contenido_ficha.findChildren(QPushButton) if b.text() == "✉️ Correo…")
        boton.click()
        self.assertEqual(abiertos, [("correo",), ("redactar", "Programa de RustDesk", "luis@clinica.example")])
        self.assertEqual([x for x in self.avisos if x.startswith("EXCEPCIÓN")], [])
        dlg.deleteLater()
        host.deleteLater()


if __name__ == "__main__":
    unittest.main()
