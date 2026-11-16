# -*- coding: utf-8 -*-
"""Pruebas del servidor propio de RustDesk (4.11.0) y del servidor en este PC (4.12.0). Sin Internet ni programas reales: los .exe son
archivos falsos en carpetas temporales y la comprobación de puertos se hace contra 127.0.0.1.
python -m unittest discover -s tests -p "test_*.py" """
import os
import random
import socket
import sqlite3
import string
import sys
import tempfile
import time
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

from msys.domain import acceso_remoto as ar  # noqa: E402
from msys.domain import servidor_rustdesk as sr  # noqa: E402

# Caso de prueba de src/custom_server.rs (RustDesk 1.5.0)
CLAVE_RD = "5Qbwsde3unUcJBtrx9ZkvUmwFNoExHzpryHuPUdqlWM="
CADENA_RD = "0nI900VsFHZVBVdIlncwpHS4V0bOZ0dtVldrpVO4JHdCp0YV5WdzUGZzdnYRVjI6ISeltmIsISMuEjLx4SMiojI0N3boJye"


class LocalFalso:
    """ServidorLocal simulado: nunca arranca nada ni toca tareas ni firewall."""

    def __init__(self, faltan=()):
        self.e = sr.Estado(False, False, False, False)
        self.clave, self.lista, self.llamadas, self._faltan = "", [], [], list(faltan)

    def faltan(self):
        return self._faltan

    def estado(self, con_sistema=True):
        return sr.Estado(self.e.hbbs, self.e.hbbr, self.e.tareas, self.e.firewall)

    def arrancar(self):
        self.llamadas.append("arrancar")
        self.e.hbbs = self.e.hbbr = True
        self.clave = CLAVE_RD

    def parar(self):
        self.llamadas.append("parar")
        self.e.hbbs = self.e.hbbr = False

    def clave_publica(self):
        return self.clave

    def equipos(self):
        return self.lista

    def crear_tareas(self):
        self.e.tareas = True
        return True, ""

    def quitar_tareas(self):
        self.e.tareas = False
        return True, ""

    def crear_reglas(self):
        self.e.firewall = True
        return True, ""

    def quitar_reglas(self):
        self.e.firewall = False
        return False, "acceso denegado"


def _exe_falso(carpeta, nombre="rustdesk-1.5.0-x86_64.exe"):
    ruta = os.path.join(carpeta, nombre)
    with open(ruta, "wb") as f:
        f.write(b"MZ" + b"\0" * 64)
    return ruta


class NombreClienteTest(unittest.TestCase):
    def test_igual_que_el_caso_de_prueba_de_rustdesk(self):
        s = ar.Servidor("1.1.1.1", CLAVE_RD)
        self.assertEqual(ar.cadena_config(s), CADENA_RD)
        self.assertEqual(ar.nombre_cliente(s), f"rustdesk--{CADENA_RD}.exe")

    def test_se_lee_como_lo_lee_rustdesk(self):
        s = ar.Servidor("1.1.1.1", CLAVE_RD)
        for nombre in (f"rustdesk-licensed-{CADENA_RD}.exe", f"rustdesk--{CADENA_RD}(1).exe",
                       f"rustdesk-licensed-{CADENA_RD} (1) (2).exe", f"rustdesk-licensed-{CADENA_RD}--abc.exe",
                       rf"C:\Users\x\Desktop\rustdesk--{CADENA_RD}.exe"):
            self.assertEqual(ar.leer_nombre_cliente(nombre), s, nombre)
        self.assertIsNone(ar.leer_nombre_cliente("rustdesk.exe"))

    def test_nunca_lleva_guiones_dobles_ni_en_los_extremos(self):
        azar = random.Random(7)
        for _ in range(400):
            host = "".join(azar.choice(string.ascii_lowercase + "-.") for _ in range(azar.randint(3, 30))).strip("-.") or "x"
            clave = ar.base64.b64encode(bytes(azar.getrandbits(8) for _ in range(32))).decode()
            s = ar.Servidor(host, clave, azar.choice(["", "relay.ejemplo.es:21117"]))
            cadena = ar.cadena_config(s)
            self.assertNotIn("--", cadena)
            self.assertFalse(cadena.startswith("-") or cadena.endswith("-"))
            self.assertEqual(ar.leer_nombre_cliente(ar.nombre_cliente(s)), s)


class ValidacionServidorTest(unittest.TestCase):
    def test_validar(self):
        s = ar.validar_servidor(" http://midominio.ddns.net/ ", f" {CLAVE_RD}\n")
        self.assertEqual((s.host, s.clave, s.relay), ("midominio.ddns.net", CLAVE_RD, ""))
        self.assertTrue(s.configurado)
        self.assertEqual(ar.validar_servidor("1.2.3.4:30000", CLAVE_RD, "otro.es").relay, "otro.es")
        self.assertEqual(ar.validar_servidor("fe80::1", CLAVE_RD).host, "[fe80::1]")
        for host, clave in (("", CLAVE_RD), ("-x", CLAVE_RD), ("a b", CLAVE_RD), ("srv", "corta"),
                            ("srv", "no es base64!"), ("srv", ar.base64.b64encode(b"x" * 31).decode())):
            with self.assertRaises(ValueError, msg=(host, clave)):
                ar.validar_servidor(host, clave)
        self.assertFalse(ar.Servidor("srv", "").configurado)

    def test_puertos(self):
        s = ar.validar_servidor("midominio.ddns.net", CLAVE_RD)
        self.assertEqual([(h, p) for h, p, _pr, _u in ar.puertos_servidor(s)],
                         [("midominio.ddns.net", 21115), ("midominio.ddns.net", 21116), ("midominio.ddns.net", 21117)])
        s = ar.validar_servidor("1.2.3.4:30000", CLAVE_RD, "relay.es")
        self.assertEqual([(h, p) for h, p, _pr, _u in ar.puertos_servidor(s)],
                         [("1.2.3.4", 29999), ("1.2.3.4", 30000), ("relay.es", 21117)])

    def test_comprobar_contra_este_equipo(self):
        escucha = socket.socket()
        escucha.bind(("127.0.0.1", 0))
        escucha.listen(4)
        self.addCleanup(escucha.close)
        puerto = escucha.getsockname()[1]
        filas = ar.comprobar_servidor(ar.Servidor(f"127.0.0.1:{puerto}", CLAVE_RD), timeout=2)
        self.assertEqual([f[1] for f in filas], [puerto - 1, puerto, puerto + 1])
        self.assertTrue(filas[1][4])
        self.assertEqual(filas[1][5], "Responde")
        filas = ar.comprobar_servidor(ar.Servidor("no-existe.invalid", CLAVE_RD), timeout=2)
        self.assertFalse(any(f[4] for f in filas))

    def test_orden_por_el_servidor_propio(self):
        s = ar.Servidor("midominio.ddns.net", CLAVE_RD)
        e = ar.Equipo("e1", "Cliente", "PC", ar.RUSTDESK, "123 456 789", servidor_propio=True)
        self.assertEqual(ar.orden_rustdesk("r.exe", e, "x", s),
                         ["r.exe", "--connect", f"123456789@midominio.ddns.net?key={CLAVE_RD}", "--password", "x"])
        # Sin servidor propio en el equipo, o por IP directa: como siempre
        e2 = ar.Equipo("e2", "Cliente", "PC", ar.RUSTDESK, "123456789")
        self.assertEqual(ar.orden_rustdesk("r.exe", e2, "", s), ["r.exe", "--connect", "123456789"])
        e3 = ar.Equipo("e3", "Cliente", "PC", ar.RUSTDESK, "", "10.0.0.7", servidor_propio=True)
        self.assertEqual(ar.orden_rustdesk("r.exe", e3, "", s), ["r.exe", "--connect", "10.0.0.7"])
        with self.assertRaises(ValueError):
            ar.orden_rustdesk("r.exe", e, "", ar.Servidor())
        self.assertIn("No le cambies el nombre", ar.texto_para_cliente(ar.RUSTDESK, propio=True))
        self.assertIn(ar.URL_RUSTDESK, ar.texto_para_cliente(ar.RUSTDESK))


class GenerarClienteTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.s = ar.Servidor("midominio.ddns.net", CLAVE_RD)

    def test_origen(self):
        bueno = _exe_falso(self.d)
        self.assertEqual(ar.problema_origen_cliente(bueno), "")
        self.assertTrue(ar.problema_origen_cliente(os.path.join(self.d, "nada.exe")))
        self.assertTrue(ar.problema_origen_cliente(_exe_falso(self.d, "calc.exe")))
        texto = os.path.join(self.d, "rustdesk-texto.exe")
        with open(texto, "w") as f:
            f.write("hola")
        self.assertIn("No es un programa", ar.problema_origen_cliente(texto))
        instalado = os.path.join(self.d, "instalado")
        os.makedirs(instalado)
        open(os.path.join(instalado, "librustdesk.dll"), "wb").close()
        self.assertIn("instalado", ar.problema_origen_cliente(_exe_falso(instalado, "rustdesk.exe")))

    def test_generar(self):
        origen = _exe_falso(self.d)
        destino = os.path.join(self.d, "salida")
        os.makedirs(destino)
        ruta = ar.generar_cliente(origen, destino, self.s)
        self.assertEqual(os.path.basename(ruta), ar.nombre_cliente(self.s))
        self.assertEqual(ar.leer_nombre_cliente(ruta), self.s)
        with open(ruta, "rb") as f:
            self.assertTrue(f.read().startswith(b"MZ"))
        with self.assertRaises(FileExistsError):
            ar.generar_cliente(origen, destino, self.s)
        self.assertEqual(ar.generar_cliente(origen, destino, self.s, sobrescribir=True), ruta)
        with self.assertRaises(ValueError):
            ar.generar_cliente(origen, destino, ar.Servidor())
        with self.assertRaises(ValueError):
            ar.generar_cliente(origen, os.path.join(self.d, "no-existe"), self.s)
        rara = os.path.join(self.d, "host=x")
        os.makedirs(rara)
        with self.assertRaises(ValueError):
            ar.generar_cliente(origen, rara, self.s)

    def test_leer_clave(self):
        pub = os.path.join(self.d, "id_ed25519.pub")
        with open(pub, "w") as f:
            f.write(CLAVE_RD + "\n")
        self.assertEqual(ar.leer_clave_archivo(pub), CLAVE_RD)
        privada = os.path.join(self.d, "id_ed25519")
        with open(privada, "w") as f:
            f.write("secreta")
        with self.assertRaises(ValueError):
            ar.leer_clave_archivo(privada)
        with self.assertRaises(ValueError):
            ar.leer_clave_archivo(os.path.join(self.d, "no.pub"))


class BaseDatosServidorTest(unittest.TestCase):
    def test_migracion_desde_4_6_0_y_ajustes(self):
        ruta = os.path.join(tempfile.mkdtemp(), "multisys.db")
        with sqlite3.connect(ruta) as c:                       # tabla tal como la creaba la 4.6.0
            c.execute("""CREATE TABLE acceso_equipos (
                id TEXT PRIMARY KEY, cliente TEXT NOT NULL, nombre TEXT NOT NULL, metodo TEXT NOT NULL,
                rustdesk_id TEXT NOT NULL DEFAULT '', direccion TEXT NOT NULL DEFAULT '',
                usuario TEXT NOT NULL DEFAULT '', clave BLOB, notas TEXT NOT NULL DEFAULT '',
                ultima TEXT NOT NULL DEFAULT '', creado TEXT NOT NULL, modificado TEXT NOT NULL)""")
            c.execute("INSERT INTO acceso_equipos (id, cliente, nombre, metodo, rustdesk_id, creado, modificado) "
                      "VALUES ('v1', 'Viejo', 'PC', 'rustdesk', '111222333', 'x', 'x')")
        db = ar.AccesoDB(ruta)
        (viejo,) = db.listar()
        self.assertFalse(viejo.servidor_propio)                # lo de antes sigue por los servidores públicos
        viejo.servidor_propio = True
        viejo.contacto = "Ana Pérez"
        db.guardar(viejo)
        self.assertTrue(db.listar()[0].servidor_propio)
        self.assertFalse(db.servidor().configurado)
        self.assertEqual(db.direccion_ddns(), "")              # sin tablas del agente: no falla ni las crea
        with sqlite3.connect(ruta) as c:
            self.assertIsNone(c.execute("SELECT name FROM sqlite_master WHERE name = 'agente_ajustes'").fetchone())
            c.execute("CREATE TABLE agente_ajustes (clave TEXT PRIMARY KEY, valor TEXT NOT NULL)")
            c.execute("INSERT INTO agente_ajustes VALUES ('direccion', 'midominio.ddns.net')")
        self.assertEqual(db.direccion_ddns(), "midominio.ddns.net")
        db.fijar_servidor(ar.Servidor("midominio.ddns.net", CLAVE_RD))
        self.assertEqual(db.servidor(), ar.Servidor("midominio.ddns.net", CLAVE_RD, ""))


class VentanaServidorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        from PyQt5.QtWidgets import QWidget
        from msys.ui import acceso_remoto_dialog as ad
        self.ad = ad
        self.d = tempfile.mkdtemp()
        self.lanzados, self.avisos, self.urls = [], [], []
        self._orig = {n: getattr(ar, n) for n in ("lanzar", "buscar_rustdesk")}
        self._orig_ad = {n: getattr(ad, n) for n in ("dialogo_abrir_archivo", "dialogo_elegir_carpeta")}
        ar.lanzar = lambda argv: self.lanzados.append(list(argv))
        ar.buscar_rustdesk = lambda ruta="": r"C:\Program Files\RustDesk\rustdesk.exe"
        self._hook = sys.excepthook
        sys.excepthook = lambda t, v, tb: self.avisos.append(f"EXCEPCIÓN {t.__name__}: {v}")
        self.db = ar.AccesoDB(os.path.join(self.d, "multisys.db"))
        self.host = QWidget()
        self.dlg = ad.AccesoRemotoDialog(self.host, self.db)
        self.dlg._aviso = lambda texto, *_a: self.avisos.append(texto)
        self.dlg._confirmar = lambda *_a: True                  # nunca una ventana real (sin pantalla en las pruebas)

    def tearDown(self):
        for n, f in self._orig.items():
            setattr(ar, n, f)
        for n, f in self._orig_ad.items():
            setattr(self.ad, n, f)
        sys.excepthook = self._hook
        self.dlg.deleteLater()
        self.host.deleteLater()

    def _ventana_servidor(self):
        self.local = LocalFalso()
        v = self.ad.ServidorPropioDialog(self.dlg, self.db, local=self.local)
        v._aviso = lambda texto, *_a: self.avisos.append(texto)
        v._confirmar = lambda *_a: True
        v._abrir_url = lambda url: self.urls.append(url.toString())
        v._comprobar = lambda s: [("midominio.ddns.net", 21115, "TCP", "Prueba del tipo de red", True, "Responde"),
                                  ("midominio.ddns.net", 21116, "TCP y UDP", "Identificación (ID)", True, "Responde"),
                                  ("midominio.ddns.net", 21117, "TCP", "Retransmisión", False, "No contesta")]
        return v

    def _esperar(self, v):
        fin = time.time() + 10
        for _ in range(3):              # acción → refresco del estado (dos tareas encadenadas)
            for nombre in ("_tarea", "_tarea_local", "_tarea_estado"):
                t = getattr(v, nombre, None)
                while t is not None and t.isRunning() and time.time() < fin:
                    self.app.processEvents()
            for _ in range(20):
                self.app.processEvents()

    def test_barrido_de_la_ventana_del_servidor(self):
        from PyQt5.QtGui import QGuiApplication
        v = self._ventana_servidor()
        # Vacía: Guardar con todo vacío = sin servidor; Comprobar y Generar avisan sin hacer nada
        v.btn_comprobar.click()
        self.assertIn("Falta la dirección", self.avisos[-1])
        v.btn_generar.click()
        self.assertIn("Falta la dirección", self.avisos[-1])
        # Leer la clave de id_ed25519.pub
        pub = os.path.join(self.d, "id_ed25519.pub")
        with open(pub, "w") as f:
            f.write(CLAVE_RD)
        self.ad.dialogo_abrir_archivo = lambda *a: pub
        v.btn_leer.click()
        self.assertEqual(v.clave.text(), CLAVE_RD)
        v.host.setText("midominio.ddns.net")
        v.btn_comprobar.click()
        self._esperar(v)
        self.assertFalse(v.lbl_comprobacion.isHidden())
        self.assertIn("✖ 21117 TCP", v.lbl_comprobacion.text())
        self.assertIn("21116 UDP no se puede comprobar", v.lbl_comprobacion.text())
        # Descargas: solo se piden las páginas oficiales
        v.btn_descargar.click()
        v.btn_servidor.click()
        self.assertEqual(self.urls, [ar.URL_RUSTDESK, ar.URL_SERVIDOR])
        # Elegir un RustDesk que no sirve y luego el bueno
        self.ad.dialogo_abrir_archivo = lambda *a: _exe_falso(self.d, "calc.exe")
        v.btn_origen.click()
        self.assertIn("RustDesk descargado", self.avisos[-1])
        origen = _exe_falso(self.d)
        self.ad.dialogo_abrir_archivo = lambda *a: origen
        v.btn_origen.click()
        self.assertEqual(v.origen.text(), os.path.normpath(origen))
        # Generar (dos veces: la segunda pregunta y sustituye)
        salida = os.path.join(self.d, "salida")
        os.makedirs(salida)
        self.ad.dialogo_elegir_carpeta = lambda *a: salida
        self.assertFalse(v.btn_instr.isEnabled())
        v.btn_generar.click()
        v.btn_generar.click()
        nombre = ar.nombre_cliente(ar.Servidor("midominio.ddns.net", CLAVE_RD))
        self.assertEqual(os.listdir(salida), [nombre])
        self.assertTrue(self.db.servidor().configurado)          # generar guarda el servidor del programa
        v.btn_carpeta.click()
        self.assertTrue(self.urls[-1].startswith("file:"))
        v.btn_instr.click()
        self.assertIn("No le cambies el nombre", QGuiApplication.clipboard().text())
        # Guardar con una clave mala no guarda; con todo vacío lo quita
        v.clave.setText("mala")
        v.btn_guardar.click()
        self.assertEqual(self.db.servidor().clave, CLAVE_RD)
        for campo in (v.host, v.clave, v.relay):
            campo.clear()
        v.btn_guardar.click()
        self.assertFalse(self.db.servidor().configurado)
        self.assertEqual([a for a in self.avisos if a.startswith("EXCEPCIÓN")], [])
        v.deleteLater()

    def test_propone_el_dns_dinamico_de_conexion(self):
        with sqlite3.connect(self.db.path) as c:
            c.execute("CREATE TABLE agente_ajustes (clave TEXT PRIMARY KEY, valor TEXT NOT NULL)")
            c.execute("INSERT INTO agente_ajustes VALUES ('direccion', 'midominio.ddns.net')")
        v = self._ventana_servidor()
        self.assertEqual(v.host.text(), "midominio.ddns.net")
        v.deleteLater()

    def test_equipo_por_servidor_propio_y_conectar(self):
        from PyQt5.QtGui import QGuiApplication
        # Sin servidor: un equipo nuevo no lo marca y «RustDesk con mi servidor» avisa
        self.dlg.btn_nuevo.click()
        self.assertFalse(self.dlg.chk_servidor.isChecked())
        self.dlg.cancelar_edicion()
        self.dlg.copiar_para_cliente_propio()
        self.assertIn("Configura primero", self.avisos[-1])
        self.db.fijar_servidor(ar.Servidor("midominio.ddns.net", CLAVE_RD))
        self.dlg.btn_nuevo.click()
        self.assertTrue(self.dlg.chk_servidor.isChecked())        # con servidor, lo normal es usarlo
        self.dlg.ed_cliente.setCurrentText("Cliente A")
        self.dlg.ed_contacto.setCurrentText("Ana Pérez")
        self.dlg.ed_nombre.setText("Recepción")
        self.dlg.ed_rustdesk.setText("123 456 789")
        self.dlg.btn_guardar.click()
        (e,) = self.db.listar()
        self.assertTrue(e.servidor_propio)
        self.dlg.btn_conectar.click()
        self.assertEqual(self.lanzados[-1], [r"C:\Program Files\RustDesk\rustdesk.exe", "--connect",
                                             f"123456789@midominio.ddns.net?key={CLAVE_RD}"])
        self.dlg.copiar_para_cliente_propio()
        self.assertIn("No le cambies el nombre", QGuiApplication.clipboard().text())
        # Desmarcar en el editor = cambio pendiente; guardado, conecta por los servidores públicos
        self.dlg.editar(e.id)
        self.dlg.chk_servidor.setChecked(False)
        self.assertTrue(self.dlg._hay_cambios())
        self.dlg.btn_guardar.click()
        self.dlg.btn_conectar.click()
        self.assertEqual(self.lanzados[-1][-1], "123456789")
        self.assertEqual([a for a in self.avisos if a.startswith("EXCEPCIÓN")], [])


    def test_pestana_en_este_pc(self):
        from PyQt5.QtCore import QItemSelectionModel
        v = self._ventana_servidor()
        self._esperar(v)
        self.assertIn("Parado", v.lbl_estado.text())
        self.assertTrue(v.btn_parar.isHidden() and not v.btn_arrancar.isHidden())
        self.assertFalse(v.btn_usar.isEnabled())                 # aún sin clave
        # Arrancar: la primera vez pone la clave del PC en Conexión (y la dirección de ⚙️ Conexión si está vacía)
        v.btn_arrancar.click()
        self._esperar(v)
        self.assertEqual(self.local.llamadas, ["arrancar"])
        self.assertIn("En marcha", v.lbl_estado.text())
        self.assertEqual(v.clave.text(), CLAVE_RD)
        self.assertTrue(v.btn_arrancar.isHidden() and not v.btn_parar.isHidden())
        # Arrancar con Windows: pregunta; «No» deja la casilla como estaba
        v._confirmar = lambda *_a: False
        v.chk_windows.click()
        self.assertFalse(v.chk_windows.isChecked())
        v._confirmar = lambda *_a: True
        v.chk_windows.click()
        self._esperar(v)
        self.assertTrue(self.local.e.tareas and v.chk_windows.isChecked())
        # Firewall: crear y, al quitar, un error se avisa
        v.btn_firewall.click()
        self._esperar(v)
        self.assertTrue(self.local.e.firewall)
        self.assertIn("Quitar", v.btn_firewall.text())
        v.btn_firewall.click()
        self._esperar(v)
        self.assertIn("acceso denegado", self.avisos[-1])
        # Equipos dados de alta: el que ya está en la agenda se marca; «＋ A la agenda» sin elegir avisa
        self.db.guardar(ar.Equipo("x1", "Cliente A", "Recepción", ar.RUSTDESK, "111 222 333", contacto="Ana Pérez"))
        self.local.lista = [sr.EquipoServidor("111222333", "31/10/2026 00:12", "192.168.1.75"),
                            sr.EquipoServidor("12345678", "31/10/2026 00:20", "10.0.0.8")]
        v.btn_refrescar.click()
        self._esperar(v)
        self.assertEqual(v.lbl_equipos.text(), "2 equipos")
        filas = {f[0]: f[3] for f in v.modelo_equipos.filas()}
        self.assertEqual(filas, {"111222333": "Sí", "12345678": ""})
        v.btn_agenda.click()
        self.assertIn("Elige el equipo", self.avisos[-1])
        # Parar: pregunta
        v.btn_parar.click()
        self._esperar(v)
        self.assertEqual(self.local.llamadas[-1], "parar")
        self.assertIn("Parado", v.lbl_estado.text())
        # «🔑 Usar este servidor» lleva a Conexión con su clave
        v.clave.clear()
        v.btn_usar.click()
        self.assertEqual((v.clave.text(), v.pestanas.currentIndex()), (CLAVE_RD, 1))
        # Elegir un equipo de la lista y añadirlo: cierra y la agenda abre el editor con su ID y el servidor propio
        fila = next(r for r in range(v.tabla_equipos.model().rowCount())
                    if v.tabla_equipos.model().index(r, 0).data() == "12345678")
        v.tabla_equipos.selectionModel().select(v.tabla_equipos.model().index(fila, 0),
                                                QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows)
        v.btn_agenda.click()
        self.assertEqual(v.anadir_id, "12345678")
        self.assertEqual([a for a in self.avisos if a.startswith("EXCEPCIÓN")], [])
        v.deleteLater()

    def test_anadir_a_la_agenda_abre_el_editor(self):
        original = self.ad.ServidorPropioDialog

        class Falso:
            def __init__(s, *_a, **_k):
                s.anadir_id = "12345678"

            def exec_(s):
                return 1

        self.ad.ServidorPropioDialog = Falso
        self.addCleanup(setattr, self.ad, "ServidorPropioDialog", original)
        self.dlg.abrir_servidor()
        self.assertIs(self.dlg.panel.currentWidget(), self.dlg.pag_editor)
        self.assertEqual(self.dlg.ed_rustdesk.text(), "12345678")
        self.assertTrue(self.dlg.chk_servidor.isChecked())

    def test_sin_programas_del_servidor(self):
        self.local = LocalFalso(faltan=["hbbs.exe"])
        v = self.ad.ServidorPropioDialog(self.dlg, self.db, local=self.local)
        self.assertIn("Falta hbbs.exe", v.lbl_estado.text())
        self.assertFalse(v.btn_arrancar.isEnabled() or v.chk_windows.isEnabled() or v.btn_firewall.isEnabled())
        v.deleteLater()


class ServidorLocalTest(unittest.TestCase):
    """Motor del servidor en este PC. Las órdenes de tareas y firewall se anotan, NUNCA se ejecutan."""

    def setUp(self):
        self.base = tempfile.mkdtemp()
        self.s = sr.ServidorLocal(self.base)
        self.ordenes = []
        self._orig = sr._ejecutar
        sr._ejecutar = lambda argv, timeout=30: (self.ordenes.append(list(argv)) or (0, ""))
        self.addCleanup(setattr, sr, "_ejecutar", self._orig)

    def test_rutas_faltan_y_fechas(self):
        self.assertEqual(self.s.faltan(), ["hbbs.exe", "hbbr.exe"])
        self.assertEqual(self.s.carpeta_datos, os.path.join(self.base, "datos"))
        with self.assertRaises(RuntimeError):
            self.s.arrancar()
        self.assertEqual(self.s.equipos(), [])
        self.assertEqual(self.s.clave_publica(), "")
        local = sr.fecha_local("2026-10-31 00:12:54")
        self.assertRegex(local, r"^\d\d/\d\d/2026 \d\d:\d\d$")
        self.assertEqual(sr.fecha_local(""), "")
        self.assertEqual(sr.Estado(True, False).texto, "Solo hbbs en marcha")
        self.assertEqual(sr.Estado(True, True).texto, "En marcha")

    def test_equipos_de_la_base_del_servidor(self):
        os.makedirs(self.s.carpeta_datos)
        with sqlite3.connect(os.path.join(self.s.carpeta_datos, "db_v2.sqlite3")) as c:   # esquema de src/database.rs
            c.execute("""create table peer (guid blob primary key not null, id varchar(100) not null,
                         uuid blob not null, pk blob not null, created_at datetime not null default(current_timestamp),
                         user blob, status tinyint, note varchar(300), info text not null) without rowid""")
            c.execute("insert into peer (guid, id, uuid, pk, created_at, info) values (x'01', '12345678', x'02', "
                      "x'03', '2026-10-31 00:12:54', '{\"ip\":\"::ffff:192.168.1.75\"}')")
            c.execute("insert into peer (guid, id, uuid, pk, info) values (x'04', '111222333', x'05', x'06', 'mal')")
        equipos = self.s.equipos()
        self.assertEqual([(e.id, e.ip) for e in equipos], [("111222333", ""), ("12345678", "192.168.1.75")])
        with open(os.path.join(self.s.carpeta_datos, "id_ed25519.pub"), "w") as f:
            f.write(CLAVE_RD)
        self.assertEqual(self.s.clave_publica(), CLAVE_RD)

    def test_ordenes_de_tareas_y_firewall(self):
        os.makedirs(self.s.carpeta_bin)
        for p in sr.PROGRAMAS:
            _exe_falso(self.s.carpeta_bin, f"{p}.exe")
        self.assertEqual(self.s.crear_tareas(), (True, ""))
        script = self.ordenes[0][-1]
        self.assertIn("-AtStartup", script)
        self.assertIn("-UserId 'SYSTEM'", script)
        self.assertIn(f"-WorkingDirectory '{self.s.carpeta_datos}'", script)
        self.assertEqual(script.count("Register-ScheduledTask"), 2)    # una tarea por programa
        self.ordenes.clear()
        self.assertEqual(self.s.crear_reglas(), (True, ""))
        altas = [o for o in self.ordenes if "add" in o]
        self.assertEqual(len(altas), 3)
        for o in altas:
            self.assertIn("profile=private,domain", o)
            self.assertTrue(any(x.startswith("program=") and self.s.carpeta_bin in x for x in o))
        self.assertIn("localport=21115,21116", altas[0])
        self.assertIn("protocol=UDP", altas[1])
        self.assertIn("localport=21117", altas[2])
        with self.assertRaises(ValueError):
            sr._ps_texto("a\nb")
        self.assertEqual(sr._ps_texto("C:\\Bob's"), "'C:\\Bob''s'")

    @unittest.skipUnless(os.path.isfile(os.path.join(RAIZ, "SERVIDOR_RUSTDESK", "bin", "hbbs.exe")),
                         "Sin los programas compilados del servidor")
    def test_programas_reales_sin_tocar_la_configuracion_de_rustdesk(self):
        sr._ejecutar = self._orig                               # schtasks /Query y netsh show: solo lectura
        if sr.ServidorLocal(self.base).puertos_ocupados():
            self.skipTest("Los puertos 21115-21117 ya están en uso en este equipo")
        import shutil
        os.makedirs(self.s.carpeta_bin)
        for p in sr.PROGRAMAS:
            shutil.copy2(os.path.join(RAIZ, "SERVIDOR_RUSTDESK", "bin", f"{p}.exe"), self.s.carpeta_bin)
        config = os.path.join(os.environ.get("APPDATA", ""), "RustDesk", "config")

        def huella():
            if not os.path.isdir(config):
                return {}
            return {n: (os.path.getsize(os.path.join(config, n)), os.path.getmtime(os.path.join(config, n)))
                    for n in os.listdir(config)}

        antes = huella()
        self.addCleanup(lambda: self.s.parar() if any(self.s.procesos().values()) else None)
        self.s.arrancar(espera=10)
        e = self.s.estado(con_sistema=False)
        self.assertTrue(e.en_marcha)
        self.assertTrue(self.s.clave_publica())
        self.assertEqual(sorted(x for x in os.listdir(self.s.carpeta_datos) if not x.endswith(".log"))[:2],
                         ["db_v2.sqlite3", "db_v2.sqlite3-shm"])
        filas = ar.comprobar_servidor(ar.Servidor("127.0.0.1", self.s.clave_publica()), timeout=3)
        self.assertTrue(all(f[4] for f in filas))
        self.s.parar()
        self.assertFalse(any(self.s.procesos().values()))
        self.assertEqual(huella(), antes)                       # el RustDesk del usuario, intacto


if __name__ == "__main__":
    unittest.main()
