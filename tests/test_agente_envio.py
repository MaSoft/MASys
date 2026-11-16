# -*- coding: utf-8 -*-
"""Pruebas del envío directo del agente (4.7.1). El servidor escucha SOLO en
127.0.0.1 y en un puerto libre que da Windows (no se abre nada hacia fuera ni
se toca el firewall). Bases de datos temporales.
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

from msys.domain import agente as ag  # noqa: E402
from msys.domain import agente_envio as envio  # noqa: E402
from msys.models import rows as R  # noqa: E402


def _informe(equipo="PC-01"):
    inf = ag.nuevo_informe(["software"])
    inf["equipo"], inf["fin"] = equipo, "2026-10-27T10:00:00"
    ag.anadir_seccion(inf, "software", [R.InstalledSoftwareRow("7-Zip", "23.01", "Igor", "01/10/2026", "C:\\7z")])
    return inf


def _esperar(condicion, segundos=5):
    from PyQt5.QtWidgets import QApplication
    fin = time.time() + segundos
    while not condicion() and time.time() < fin:
        QApplication.processEvents()
        time.sleep(0.02)
    return condicion()


class CodigoEnvioTest(unittest.TestCase):
    def test_ida_y_vuelta_y_cualquier_letra_cambiada(self):
        _priv, pub = ag.generar_par()
        ticket = os.urandom(16)
        for host in ("198.51.100.7", "ejemplo.ddns.net", "mi-oficina.duckdns.org"):
            codigo = envio.codigo_envio(pub, host, 47885, ticket)
            self.assertTrue(codigo.startswith("MSE1-"))
            self.assertEqual(envio.leer_codigo_envio(codigo), (pub, host, 47885, ticket))
            self.assertEqual(envio.leer_codigo_envio(codigo.lower().replace("-", " ")), (pub, host, 47885, ticket))
        codigo = envio.codigo_envio(pub, "ejemplo.ddns.net", 47885, ticket)
        for i in range(5, len(codigo)):
            if codigo[i] == "-":
                continue
            otra = "A" if codigo[i] != "A" else "B"
            with self.assertRaises(ag.ErrorAgente, msg=i):
                envio.leer_codigo_envio(codigo[:i] + otra + codigo[i + 1:])
        for malo in ("-x", "a b", "", "x" * 120, "srv..local"):
            with self.assertRaises(ValueError, msg=malo):
                envio.codigo_envio(pub, malo, 47885, ticket)
        with self.assertRaises(ValueError):
            envio.codigo_envio(pub, "srv", 0, ticket)

    def test_el_agente_acepta_los_dos_codigos(self):
        _priv, pub = ag.generar_par()
        self.assertEqual(envio.leer_cualquier_codigo(ag.codigo_tecnico(pub)), (pub, None))
        t = os.urandom(16)
        self.assertEqual(envio.leer_cualquier_codigo(envio.codigo_envio(pub, "1.2.3.4", 5000, t)),
                         (pub, ("1.2.3.4", 5000, t)))
        with self.assertRaises(ag.ErrorAgente):
            envio.leer_cualquier_codigo("MSE1-AAAAA-BBBBB")


class TicketsYServidorTest(unittest.TestCase):
    def setUp(self):
        self.db = ag.AgenteDB(os.path.join(tempfile.mkdtemp(), "multisys.db"))
        self.tickets = envio.TicketsDB(self.db)
        self.recibidos = []
        self.srv = envio.ServidorAgente(self.tickets, al_recibir=lambda c, e, i: self.recibidos.append((c, e, i)),
                                        puerto=0, direccion="127.0.0.1")
        self.srv.arrancar()
        self.addCleanup(self.srv.parar)
        self.pub = self.db.publica_activa()

    def _destino(self, cliente="Asesoría del Norte", dias=3):
        ticket, _caduca = self.tickets.nuevo(cliente, dias)
        return ("127.0.0.1", self.srv.puerto, ticket)

    def test_envio_correcto_y_un_solo_uso(self):
        destino = self._destino()
        contenido = ag.cifrar(_informe(), self.pub)
        self.assertEqual(len(self.tickets.pendientes()), 1)
        envio.enviar(destino, contenido, timeout=10)
        self.assertEqual(len(self.recibidos), 1)
        cliente, equipo, iid = self.recibidos[0]
        self.assertEqual((cliente, equipo), ("Asesoría del Norte", "PC-01"))
        self.assertEqual(self.db.informe(iid)["secciones"]["software"]["datos"][0]["nombre"], "7-Zip")
        self.assertEqual(self.tickets.pendientes(), [])
        with self.assertRaises(ag.ErrorAgente) as e:
            envio.enviar(destino, contenido, timeout=10)                 # el mismo código otra vez
        self.assertIn("ya se usó", str(e.exception))
        self.assertEqual(len(self.db.listar_informes()), 1)

    def test_rechazos(self):
        contenido = ag.cifrar(_informe(), self.pub)
        # Firma de otro contenido
        destino = self._destino()
        host, puerto, ticket = destino
        import urllib.request
        peticion = urllib.request.Request(f"http://{host}:{puerto}{envio.RUTA}", data=contenido + b"x", method="POST",
                                          headers={"X-MASys-Ticket": envio.huella_ticket(ticket),
                                                   "X-MASys-Firma": envio.firmar(ticket, contenido)})
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(peticion, timeout=10)
        self.assertEqual(e.exception.code, 401)
        # Ticket inventado
        with self.assertRaises(ag.ErrorAgente) as e:
            envio.enviar((host, puerto, os.urandom(16)), contenido, timeout=10)
        self.assertIn("no es válido", str(e.exception))
        # Archivo cifrado para OTRO técnico con un ticket bueno: no se guarda y el ticket sigue valiendo
        _p, otra = ag.generar_par()
        with self.assertRaises(ag.ErrorAgente) as e:
            envio.enviar(destino, ag.cifrar(_informe(), otra), timeout=10)
        self.assertIn("no es para ese técnico", str(e.exception))
        self.assertEqual(len(self.tickets.pendientes()), 1)
        # Caducado
        caducado = self._destino()
        with sqlite3.connect(self.db.path) as c:
            c.execute("UPDATE agente_envios SET caduca = '2000-01-01T00:00:00' WHERE huella = ?",
                      (envio.huella_ticket(caducado[2]),))
        with self.assertRaises(ag.ErrorAgente) as e:
            envio.enviar(caducado, contenido, timeout=10)
        self.assertIn("caducado", str(e.exception))
        # Anulado
        anulado = self._destino()
        self.tickets.anular(envio.huella_ticket(anulado[2]))
        with self.assertRaises(ag.ErrorAgente):
            envio.enviar(anulado, contenido, timeout=10)
        # Demasiado grande y ruta desconocida
        original = envio.MAX_CUERPO
        envio.MAX_CUERPO = 10
        try:
            with self.assertRaises(ag.ErrorAgente) as e:
                envio.enviar(destino, contenido, timeout=10)
            self.assertIn("demasiado grande", str(e.exception))
        finally:
            envio.MAX_CUERPO = original
        peticion = urllib.request.Request(f"http://{host}:{puerto}/otra", data=b"x", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(peticion, timeout=10)
        self.assertEqual(e.exception.code, 404)
        self.assertEqual(self.recibidos, [])
        self.assertEqual(self.db.listar_informes(), [])

    def test_sin_conexion(self):
        self.srv.parar()
        with self.assertRaises(ag.ErrorAgente) as e:
            envio.enviar(("127.0.0.1", self.srv.puerto, os.urandom(16)), b"x", timeout=3)
        self.assertIn("No se pudo conectar", str(e.exception))


class VentanasTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        from msys.ui import recepcion_agente as rec
        self.rec_mod = rec
        self.errores = []
        self._hook = sys.excepthook
        sys.excepthook = lambda t, v, tb: self.errores.append(f"{t.__name__}: {v}")
        self.db = ag.AgenteDB(os.path.join(tempfile.mkdtemp(), "multisys.db"))
        self.r = rec.RecepcionAgente(self.db, puerto=0, direccion="127.0.0.1")
        self._orig = (envio.regla_firewall_existe, envio.crear_regla_firewall)
        envio.regla_firewall_existe = lambda puerto=0: True    # nunca se toca el firewall en las pruebas
        envio.crear_regla_firewall = lambda puerto=0: self.fail("no debe crear reglas")

    def tearDown(self):
        envio.regla_firewall_existe, envio.crear_regla_firewall = self._orig
        self.r.desactivar(recordar=False)
        sys.excepthook = self._hook

    def test_recepcion_y_ventana_de_informes(self):
        from msys.ui import informes_agente_dialog as idl
        d = idl.InformesAgenteDialog(None, self.db, recepcion_=self.r)
        self.assertEqual(d.lbl_recepcion.text(), "Apagado")
        d.btn_recibir.click()
        self.assertTrue(self.r.activa)
        self.assertTrue(d.btn_recibir.isChecked())
        self.assertIn(f"Recibiendo en el puerto {self.r.servidor.puerto}", d.lbl_recepcion.text())
        self.assertEqual(self.r.tickets.ajuste("recibir"), "1")
        # Código de envío desde su ventana
        c = self.rec_mod.CodigoEnvioDialog(d, self.r, ["Asesoría del Norte"], "Asesoría del Norte")
        c.direccion.setText("ejemplo.ddns.net")
        c.crear()
        pub, host, puerto, ticket = envio.leer_codigo_envio(c.txt.text())
        self.assertEqual((pub, host, puerto), (self.db.publica_activa(), "ejemplo.ddns.net", self.r.servidor.puerto))
        self.assertEqual(c.modelo.rowCount(), 1)
        c.copiar_instrucciones()
        from PyQt5.QtGui import QGuiApplication
        texto = QGuiApplication.clipboard().text()
        self.assertIn(c.txt.text(), texto)
        self.assertIn(c.caducidad, texto)
        self.assertEqual(self.r.tickets.ajuste("direccion"), "ejemplo.ddns.net")
        c.direccion.setText("-mal")
        avisos = []
        self.addCleanup(setattr, self.rec_mod, "_caja", self.rec_mod._caja)
        self.rec_mod._caja = lambda padre, texto, icono=None: type("C", (), {"exec_": lambda s: avisos.append(texto)})()
        c.crear()
        self.assertTrue(avisos)
        self.assertEqual(c.modelo.rowCount(), 1)                   # con dirección mala no se crea ticket
        d._pintar_recepcion()
        self.assertIn("1 código sin usar", d.lbl_recepcion.text())
        # Llega un informe: la lista se refresca sola
        envio.enviar(("127.0.0.1", self.r.servidor.puerto, ticket), ag.cifrar(_informe("MOSTRADOR"), pub), timeout=10)
        self.assertTrue(_esperar(lambda: d.lbl_recuento.text() == "1 informe"))
        self.assertEqual(d.arbol.topLevelItem(0).text(0), "Asesoría del Norte  (1)")
        # Anular el que queda
        c2 = self.rec_mod.CodigoEnvioDialog(d, self.r, [], "Otro")
        c2.direccion.setText("1.2.3.4")
        c2.crear()
        c2.tabla.selectRow(0)
        c2.anular()
        self.assertEqual(self.r.tickets.pendientes(), [])
        d.btn_recibir.click()
        self.assertFalse(self.r.activa)
        self.assertEqual(self.r.tickets.ajuste("recibir"), "0")
        d.reject()
        self.assertEqual(self.errores, [])

    def test_arranque_de_masys_reactiva_y_avisa(self):
        avisos = []

        class Host:
            def tray_notify(self, titulo, texto, critico=False):
                avisos.append(texto)

        self.r.tickets.fijar_ajuste("recibir", "1")
        self.addCleanup(setattr, self.rec_mod, "_INSTANCIA", self.rec_mod._INSTANCIA)
        self.rec_mod._INSTANCIA = self.r
        self.rec_mod.arrancar_si_toca(Host())
        self.assertTrue(self.r.activa)
        ticket, _c = self.r.tickets.nuevo("Clínica", 1)
        envio.enviar(("127.0.0.1", self.r.servidor.puerto, ticket), ag.cifrar(_informe("GABINETE"), self.db.publica_activa()),
                     timeout=10)
        self.assertTrue(_esperar(lambda: avisos == ["GABINETE · Clínica"]))

    def test_agente_envia_o_deja_el_archivo(self):
        from msys.agente.ventana import VentanaAgente
        pub = self.db.publica_activa()
        v = VentanaAgente()
        v.txt_codigo.setText(envio.codigo_envio(pub, "ejemplo.ddns.net", 47885, os.urandom(16)))
        self.assertIn("se enviará directamente", v.lbl_codigo.text())
        self.assertEqual(v.btn_analizar.text(), "Analizar y enviar")
        falso = lambda clase, args: ([R.InstalledSoftwareRow("A", "1", "e", "", "")], "", 0.1) \
            if clase == "InstalledSoftwareCollector" else (None, "Sin datos", 0.0)  # noqa: E731
        enviados = []
        carpeta = tempfile.mkdtemp()
        v.analizar(ejecutar=falso, carpeta=carpeta, enviar=lambda destino, contenido: enviados.append((destino, contenido)))
        self.assertEqual(len(enviados), 1)
        self.assertEqual(enviados[0][0][:2], ("ejemplo.ddns.net", 47885))
        self.assertEqual(ag.descifrar(enviados[0][1], self.db.privadas())["secciones"]["software"]["datos"][0]["nombre"], "A")
        self.assertEqual(os.listdir(carpeta), [])                   # enviado: no deja archivo
        self.assertIn("Enviado a tu técnico", v.lbl_final.text())
        self.assertFalse(v.btn_carpeta.isVisibleTo(v))

        def falla(destino, contenido):
            raise ag.ErrorAgente("No se pudo conectar con el equipo del técnico")
        v.analizar(ejecutar=falso, carpeta=carpeta, enviar=falla)
        self.assertEqual(len(os.listdir(carpeta)), 1)              # sin conexión: queda el archivo
        self.assertIn("No se pudo enviar directamente", v.lbl_final.text())
        self.assertIn("No se pudo conectar", v.lbl_final.text())
        # Con el código MSA1 de siempre: solo archivo, nunca se intenta enviar
        v.txt_codigo.setText(ag.codigo_tecnico(pub))
        self.assertEqual(v.btn_analizar.text(), "Analizar y crear el archivo")
        v.analizar(ejecutar=falso, carpeta=carpeta, enviar=lambda *a: self.fail("no debe enviar"))
        self.assertEqual(len(os.listdir(carpeta)), 2)
        self.assertEqual(self.errores, [])
        v.deleteLater()


    # ── Puerto configurable (4.8.1) ─────────────────────────────────────
    @staticmethod
    def _libre() -> int:
        import socket
        with socket.socket() as so:
            so.bind(("127.0.0.1", 0))
            return so.getsockname()[1]

    def test_validar_puerto_y_nombre_de_regla(self):
        self.assertEqual(envio.validar_puerto(" 50000 "), 50000)
        for malo in ("", "abc", "80", "1023", "65536", "47880"):
            with self.assertRaises(ValueError, msg=malo):
                envio.validar_puerto(malo)
        self.assertEqual(envio.nombre_regla(envio.PUERTO), envio.REGLA_FIREWALL)   # la de 4.7.1 sigue valiendo
        self.assertEqual(envio.nombre_regla(50000), envio.REGLA_FIREWALL + " (TCP 50000)")

    def test_cambiar_puerto_escuchando_y_se_recuerda(self):
        import urllib.request
        self.assertEqual(self.r.activar(recordar=False), "")
        nuevo = self._libre()
        self.assertEqual(self.r.cambiar_puerto(str(nuevo)), "")
        self.assertTrue(self.r.activa)
        self.assertEqual(self.r.servidor.puerto, nuevo)
        self.assertIn(str(nuevo), self.r.estado())
        # Responde en el puerto nuevo (ruta sin ticket: 404/401, pero contesta)
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{nuevo}/", timeout=5)
        except urllib.error.HTTPError as e:
            e.close()
        self.assertEqual(self.db_ajuste("puerto"), str(nuevo))
        otra = self.rec_mod.RecepcionAgente(self.db, direccion="127.0.0.1")
        self.assertEqual(otra.servidor.puerto, nuevo)       # la siguiente vez que se abra MASys
        # Puerto no válido u ocupado: se queda el de antes y sigue escuchando
        self.assertIn("47880", self.r.cambiar_puerto(47880))
        import socket
        with socket.socket() as ocupado:
            ocupado.bind(("127.0.0.1", 0))
            ocupado.listen(1)
            error = self.r.cambiar_puerto(ocupado.getsockname()[1])
        self.assertIn("No se pudo escuchar", error)
        self.assertTrue(self.r.activa)
        self.assertEqual(self.r.servidor.puerto, nuevo)
        self.assertEqual(self.db_ajuste("puerto"), str(nuevo))
        # Apagado: se cambia sin arrancar nada
        self.r.desactivar(recordar=False)
        otro = self._libre()
        self.assertEqual(self.r.cambiar_puerto(otro), "")
        self.assertFalse(self.r.activa)
        self.assertEqual(self.r.servidor.puerto, otro)

    def db_ajuste(self, clave):
        return self.r.tickets.ajuste(clave)

    def test_ventana_puerto_y_codigo_con_el_puerto_nuevo(self):
        from msys.ui import informes_agente_dialog as idl
        d = idl.InformesAgenteDialog(None, self.db, recepcion_=self.r)
        self.r.tickets.nuevo("Cliente A", 3)
        v = self.rec_mod.ConexionDialog(d, self.r)
        self.assertTrue(v.lbl_codigos.isVisibleTo(v))
        self.assertTrue(v.lbl_codigos.text().startswith("El código de envío sin usar lleva el puerto"))
        nuevo = self._libre()
        v.puerto.setValue(nuevo)
        v.guardar()
        self.assertEqual(v.result(), v.Accepted)
        self.assertEqual(self.r.servidor.puerto, nuevo)
        v.btn_defecto.click()
        self.assertEqual(v.puerto.value(), envio.PUERTO)
        c = self.rec_mod.CodigoEnvioDialog(d, self.r, ["Cliente A"], "Cliente A")
        c.direccion.setText("mi-casa.ddns.net")
        c.crear()
        _pub, host, puerto, _t = envio.leer_codigo_envio(c.codigo)
        self.assertEqual((host, puerto), ("mi-casa.ddns.net", nuevo))
        self.assertEqual(self.errores, [])
        for w in (c, v, d):
            w.deleteLater()

    # ── Conexión: dirección (4.8.2) ─────────────────────────────────────
    def test_comprobar_direccion_sin_internet(self):
        mia = lambda: "203.0.113.20"
        bien, t = envio.comprobar_direccion("oficina.ddns.net", resolver=lambda n: ["203.0.113.20"], publica=mia)
        self.assertTrue(bien)
        self.assertIn("apunta a tu IP pública", t)
        bien, t = envio.comprobar_direccion("oficina.ddns.net", resolver=lambda n: ["203.0.113.7"], publica=mia)
        self.assertFalse(bien)
        self.assertIn("no se ha actualizado", t)

        def no_existe(_n):
            raise OSError("no existe")
        self.assertIn("no existe en el DNS", envio.comprobar_direccion("x.ddns.net", resolver=no_existe, publica=mia)[1])
        self.assertTrue(envio.comprobar_direccion("203.0.113.20", publica=mia)[0])
        self.assertIn("no es tu IP pública", envio.comprobar_direccion("1.2.3.4", publica=mia)[1])
        self.assertFalse(envio.comprobar_direccion("-malo", publica=mia)[0])

        def sin_red():
            raise OSError("sin red")
        self.assertIn("No se pudo consultar", envio.comprobar_direccion("oficina.ddns.net", publica=sin_red)[1])

    def test_ventana_conexion_guarda_direccion(self):
        from msys.ui import informes_agente_dialog as idl
        d = idl.InformesAgenteDialog(None, self.db, recepcion_=self.r)
        v = self.rec_mod.ConexionDialog(d, self.r)
        self.assertFalse(v.lbl_codigos.isVisibleTo(v))
        v._ip_publica = lambda: "203.0.113.20"
        v.btn_ip.click()
        self.assertEqual(v.direccion.text(), "203.0.113.20")
        v.direccion.setText("https://Oficina.DDNS.net/")
        v._comprobar = lambda dir_: (True, f"✔ {dir_} apunta a tu IP pública (203.0.113.20).")
        v.btn_comprobar.click()
        self.assertTrue(v.lbl_comprobacion.isVisibleTo(v))
        self.assertIn("Oficina.DDNS.net apunta", v.lbl_comprobacion.text())
        v.direccion.setText("oficina.ddns.net")
        self.assertFalse(v.lbl_comprobacion.isVisibleTo(v))
        v.guardar()
        self.assertEqual(v.result(), v.Accepted)
        self.assertEqual(self.r.tickets.ajuste("direccion"), "oficina.ddns.net")
        c = self.rec_mod.CodigoEnvioDialog(d, self.r, ["Cliente A"], "Cliente A")
        self.assertEqual(c.direccion.text(), "oficina.ddns.net")     # el código ya sale con ella
        self.assertEqual(self.errores, [])
        for w in (c, v, d):
            w.deleteLater()

if __name__ == "__main__":
    unittest.main()
