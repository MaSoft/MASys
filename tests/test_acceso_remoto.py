# -*- coding: utf-8 -*-
"""Pruebas del Acceso remoto (4.6.0). NUNCA se lanza ningún programa ni se
conecta a nada: ar.lanzar y compañía se sustituyen por funciones que anotan
la orden. Base de datos temporal (cifrado DPAPI real del usuario).
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

from msys.domain import acceso_remoto as ar  # noqa: E402

QUSER_ES = (" NOMBRE USUARIO        NOMBRE SESIÓN      ID. ESTADO  TIEMPO IN. TIEMPO SESIÓN\r\n"
            ">tecnico               console             1  Activo      ninguno   18/10/2026 15:36\r\n"
            " contable                                  3  Desc            1:02  27/10/2026 8:10\r\n")
QUSER_EN = (" USERNAME              SESSIONNAME        ID  STATE   IDLE TIME  LOGON TIME\r\n"
            " admin                 rdp-tcp#5           2  Active          .  10/27/2026 9:00 AM\r\n")


def _equipo(**kw):
    base = dict(id="e1", cliente="Cliente A", nombre="PC Recepción", metodo=ar.RUSTDESK, rustdesk_id="123 456 789",
                contacto="Ana Pérez")
    base.update(kw)
    return ar.Equipo(**base)


class ValidacionTest(unittest.TestCase):
    def test_direcciones(self):
        self.assertEqual(ar.separar_direccion("servidor"), ("servidor", None))
        self.assertEqual(ar.separar_direccion(" 192.168.1.5:3390 "), ("192.168.1.5", 3390))
        self.assertEqual(ar.separar_direccion("pc.empresa.local"), ("pc.empresa.local", None))
        self.assertEqual(ar.separar_direccion("[fe80::1]:3389"), ("fe80::1", 3389))
        self.assertEqual(ar.separar_direccion("fe80::1"), ("fe80::1", None))
        for malo in ("", "-servidor", "a b", "srv:0", "srv:70000", "srv:abc", "[fe80::1", "x;calc", "srv/../x"):
            with self.assertRaises(ValueError, msg=malo):
                ar.separar_direccion(malo)

    def test_id_rustdesk(self):
        self.assertEqual(ar.validar_id_rustdesk("123 456 789"), "123456789")
        self.assertEqual(ar.validar_id_rustdesk("192.168.1.20"), "192.168.1.20")
        self.assertEqual(ar.validar_id_rustdesk("mi-alias"), "mi-alias")
        for malo in ("", "-p", "12", "id;rm"):
            with self.assertRaises(ValueError, msg=malo):
                ar.validar_id_rustdesk(malo)

    def test_errores_equipo(self):
        self.assertEqual(ar.errores_equipo(_equipo()), [])
        self.assertIn("Falta la empresa", ar.errores_equipo(_equipo(cliente=" ")))
        self.assertTrue(ar.errores_equipo(_equipo(rustdesk_id="")))
        self.assertTrue(ar.errores_equipo(_equipo(metodo=ar.RDP, rustdesk_id="")))
        self.assertEqual(ar.errores_equipo(_equipo(metodo=ar.RDP, direccion="srv:3390")), [])
        self.assertEqual(ar.errores_equipo(_equipo(metodo=ar.ASISTENCIA, rustdesk_id="")), [])
        self.assertTrue(ar.errores_equipo(_equipo(direccion="-x")))


class OrdenesTest(unittest.TestCase):
    def test_ordenes(self):
        e = _equipo(direccion="srv.local:3390")
        self.assertEqual(ar.orden_rustdesk("C:/r/rustdesk.exe", e), ["C:/r/rustdesk.exe", "--connect", "123456789"])
        self.assertEqual(ar.orden_rustdesk("r.exe", e, "clave"), ["r.exe", "--connect", "123456789", "--password", "clave"])
        self.assertEqual(ar.orden_rustdesk("r.exe", _equipo(rustdesk_id="", direccion="10.0.0.7")),
                         ["r.exe", "--connect", "10.0.0.7"])
        self.assertEqual(ar.orden_rdp(e), ["mstsc", "/v:srv.local:3390"])
        self.assertEqual(ar.orden_rdp(_equipo(direccion="[fe80::1]:3389")), ["mstsc", "/v:[fe80::1]:3389"])
        self.assertEqual(ar.orden_sombra(e, "3"), ["mstsc", "/shadow:3", "/v:srv.local", "/control"])
        with self.assertRaises(ValueError):
            ar.orden_sombra(e, "3 /noconsentprompt")
        self.assertEqual(ar.orden_ofrecer(e), ["msra", "/offerRA", "srv.local"])
        with self.assertRaises(ValueError):
            ar.orden_rdp(_equipo(direccion="-x"))

    def test_quser_en_cualquier_idioma(self):
        s = ar.leer_quser(QUSER_ES)
        self.assertEqual([(x.id, x.usuario, x.nombre, x.estado) for x in s],
                         [("1", "tecnico", "console", "Activo"), ("3", "contable", "", "Desc")])
        s = ar.leer_quser(QUSER_EN)
        self.assertEqual((s[0].id, s[0].usuario, s[0].nombre, s[0].estado), ("2", "admin", "rdp-tcp#5", "Active"))
        self.assertEqual(ar.leer_quser(""), [])

    def test_texto_para_cliente_y_rustdesk(self):
        self.assertIn(ar.URL_RUSTDESK, ar.texto_para_cliente(ar.RUSTDESK))
        self.assertIn("Ctrl + Windows + Q", ar.texto_para_cliente(ar.ASISTENCIA))
        d = tempfile.mkdtemp()
        falso = os.path.join(d, "rustdesk-1.5.0-x86_64.exe")
        open(falso, "wb").close()
        self.assertEqual(ar.buscar_rustdesk(falso), falso)
        otro = os.path.join(d, "calc.exe")
        open(otro, "wb").close()
        self.assertNotEqual(ar.buscar_rustdesk(otro), otro)
        self.assertEqual(ar.fecha_hora("2026-10-27T09:05:00"), ("27/10/2026", "09:05"))
        self.assertEqual(ar.fecha_hora(""), ("", ""))


class BaseDatosTest(unittest.TestCase):
    def setUp(self):
        self.db = ar.AccesoDB(os.path.join(tempfile.mkdtemp(), "multisys.db"))

    def test_equipos_y_clave_cifrada(self):
        e = _equipo()
        self.db.guardar(e, clave="S3creta!")
        (x,) = self.db.listar()
        self.assertTrue(x.con_clave)
        self.assertEqual(self.db.clave(e.id), "S3creta!")
        with sqlite3.connect(self.db.path) as c:
            crudo = bytes(c.execute("SELECT clave FROM acceso_equipos").fetchone()[0])
        self.assertNotIn(b"S3creta", crudo)                     # nunca en claro
        e.nombre = "PC Recepción 2"
        self.db.guardar(e)                                      # clave=None: se queda
        self.assertEqual(self.db.clave(e.id), "S3creta!")
        self.db.guardar(e, quitar_clave=True)
        self.assertEqual(self.db.clave(e.id), "")
        self.assertFalse(self.db.listar()[0].con_clave)
        with self.assertRaises(ValueError):
            self.db.guardar(_equipo(id="e2", nombre=""))
        self.assertEqual(self.db.clientes(), ["Cliente A"])

    def test_historial_y_ultima(self):
        e = _equipo()
        self.db.guardar(e)
        self.db.registrar(e, ar.RUSTDESK, "Error: no responde")
        self.assertEqual(self.db.listar()[0].ultima, "")
        self.db.registrar(e, ar.RDP, "Iniciada")
        self.assertTrue(self.db.listar()[0].ultima)
        h = self.db.historial()
        self.assertEqual([(c.metodo, c.resultado) for c in h],
                         [("Escritorio remoto", "Iniciada"), ("RustDesk", "Error: no responde")])
        self.db.eliminar(e.id)
        self.assertEqual(self.db.listar(), [])
        self.assertEqual(len(self.db.historial()), 2)           # el historial se conserva

    def test_ajustes(self):
        self.assertEqual(self.db.ajuste("ruta_rustdesk"), "")
        self.db.fijar_ajuste("ruta_rustdesk", r"C:\x\rustdesk.exe")
        self.db.fijar_ajuste("ruta_rustdesk", r"C:\y\rustdesk.exe")
        self.assertEqual(self.db.ajuste("ruta_rustdesk"), r"C:\y\rustdesk.exe")


class VentanaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        from PyQt5.QtWidgets import QWidget
        from msys.ui import acceso_remoto_dialog as ad
        self.ad = ad
        self.lanzados, self.avisos = [], []
        self._orig = {n: getattr(ar, n) for n in ("lanzar", "buscar_rustdesk", "buscar_asistencia_rapida",
                                                    "lanzar_asistencia_rapida", "sesiones_de")}
        ar.lanzar = lambda argv: self.lanzados.append(list(argv))
        ar.buscar_rustdesk = lambda ruta="": r"C:\Program Files\RustDesk\rustdesk.exe"
        ar.buscar_asistencia_rapida = lambda: "MicrosoftCorporationII.QuickAssist_8wekyb3d8bbwe!App"
        ar.lanzar_asistencia_rapida = lambda appid: self.lanzados.append(["asistencia", appid])
        ar.sesiones_de = lambda e: ar.leer_quser(QUSER_ES)
        self._hook = sys.excepthook
        sys.excepthook = lambda t, v, tb: self.avisos.append(f"EXCEPCIÓN {t.__name__}: {v}")
        self.db = ar.AccesoDB(os.path.join(tempfile.mkdtemp(), "multisys.db"))
        self.host = QWidget()
        self.dlg = ad.AccesoRemotoDialog(self.host, self.db)
        self.dlg._confirmar = lambda *_a: True
        self.dlg._aviso = lambda texto, *_a: self.avisos.append(texto)
        self.dlg._elegir_sesion = lambda e, sesiones, textos: sesiones[-1].id
        self.dlg.ver_historial = lambda: self.avisos.append("historial")
        self.dlg.redactar = lambda *a, **k: self.avisos.append("correo")   # 5.1.0: nunca una ventana modal real

    def tearDown(self):
        for n, f in self._orig.items():
            setattr(ar, n, f)
        sys.excepthook = self._hook
        self.dlg.deleteLater()
        self.host.deleteLater()

    def _alta(self, cliente, nombre, metodo, rustdesk="", direccion="", clave=""):
        self.dlg.btn_nuevo.click()
        self.dlg.ed_cliente.setCurrentText(cliente)
        self.dlg.ed_contacto.setCurrentText("Ana Pérez")
        self.dlg.ed_nombre.setText(nombre)
        self.dlg.ed_metodo.setCurrentIndex(self.dlg.ed_metodo.findData(metodo))
        self.dlg.ed_rustdesk.setText(rustdesk)
        self.dlg.ed_direccion.setText(direccion)
        self.dlg.ed_clave.setText(clave)
        self.dlg.btn_guardar.click()

    def _esperar_tarea(self):
        fin = time.time() + 10
        while self.dlg._tarea is not None and self.dlg._tarea.isRunning() and time.time() < fin:
            self.app.processEvents()
        for _ in range(20):
            self.app.processEvents()

    def test_alta_validacion_y_filtro_por_cliente(self):
        self.assertEqual(self.dlg.lbl_recuento.text(), "0 equipos")
        self.dlg.btn_nuevo.click()
        self.dlg.btn_guardar.click()                       # vacío: avisa y no guarda
        self.assertEqual(self.db.listar(), [])
        self.assertIn("Falta la empresa", self.avisos[-1])
        self.dlg.cancelar_edicion()
        self._alta("Cliente A", "Recepción", ar.RUSTDESK, rustdesk="123 456 789", clave="x1")
        self._alta("Cliente B", "Servidor", ar.RDP, direccion="srv.b.local")
        self._alta("Cliente A", "Contabilidad", ar.SOMBRA, direccion="10.0.0.9")
        self.assertEqual(len(self.db.listar()), 3)
        a = self.dlg.arbol
        self.assertEqual(a.topLevelItemCount(), 2)                       # Cliente › Equipo
        nodos = {a.topLevelItem(i).data(0, 0x0101): a.topLevelItem(i) for i in range(2)}
        self.assertEqual(nodos["Cliente A"].childCount(), 2)
        self.assertEqual(nodos["Cliente A"].text(0), "Cliente A  (2)")
        # Clic en el cliente pliega/despliega; «＋ Equipo» propone el cliente elegido
        abierto = nodos["Cliente B"].isExpanded()
        self.dlg._clic_arbol(nodos["Cliente B"], 0)
        self.assertNotEqual(nodos["Cliente B"].isExpanded(), abierto)
        a.setCurrentItem(nodos["Cliente B"])
        self.dlg.btn_nuevo.click()
        self.assertEqual(self.dlg.ed_cliente.currentText(), "Cliente B")
        self.dlg.cancelar_edicion()
        # Búsqueda por equipo y por cliente
        self.dlg.busqueda.setText("conta")
        self.assertEqual(self.dlg.lbl_recuento.text(), "1 de 3")
        self.assertTrue(nodos["Cliente B"].isHidden())
        self.dlg.busqueda.setText("cliente b")
        self.assertEqual(self.dlg.lbl_recuento.text(), "1 de 3")
        self.dlg.busqueda.clear()
        self.assertEqual(self.dlg.lbl_recuento.text(), "3 equipos")
        # Desplegar/plegar todo desde la cabecera
        self.dlg._iconos_cab.btn_plegar.click()
        self.assertFalse(any(a.topLevelItem(i).isExpanded() for i in range(2)))
        self.dlg._iconos_cab.btn_desplegar.click()
        self.assertTrue(all(a.topLevelItem(i).isExpanded() for i in range(2)))

    def test_conectar_cada_metodo_y_barrido_de_la_ficha(self):
        from PyQt5.QtWidgets import QPushButton
        self._alta("Cliente A", "Recepción", ar.RUSTDESK, rustdesk="123 456 789", direccion="10.0.0.5", clave="x1")
        e = self.dlg._equipo_actual()
        self.assertIsNotNone(e)
        self.dlg.btn_conectar.click()
        self.assertEqual(self.lanzados[-1], [r"C:\Program Files\RustDesk\rustdesk.exe", "--connect", "123456789",
                                             "--password", "x1"])
        # Todos los botones de la ficha (cada método posible, copiar contraseña, editar…)
        botones = [b for b in self.dlg.contenido_ficha.findChildren(QPushButton)
                   if b.isVisibleTo(self.dlg.contenido_ficha) and b.text() not in ("✕ Eliminar", "✏️ Editar")]
        textos = [b.text() for b in botones]
        for t in ("Asistencia rápida", "Escritorio remoto", "Ver la sesión de un usuario", "Ofrecer asistencia",
                  "📋 Copiar contraseña"):
            self.assertIn(t, textos)
        for b in botones:
            b.click()
            self._esperar_tarea()
        self.assertIn(["mstsc", "/v:10.0.0.5"], self.lanzados)
        self.assertIn(["mstsc", "/shadow:3", "/v:10.0.0.5", "/control"], self.lanzados)
        self.assertIn(["msra", "/offerRA", "10.0.0.5"], self.lanzados)
        self.assertIn(["asistencia", "MicrosoftCorporationII.QuickAssist_8wekyb3d8bbwe!App"], self.lanzados)
        from PyQt5.QtGui import QGuiApplication
        self.assertEqual(QGuiApplication.clipboard().text(), "x1")
        historial = self.db.historial()
        self.assertGreaterEqual(len(historial), 6)
        self.assertTrue(all(c.resultado == "Iniciada" for c in historial))
        self.assertEqual([a for a in self.avisos if a.startswith("EXCEPCIÓN")], [])

    def test_errores_al_conectar_quedan_en_el_historial(self):
        self._alta("Cliente A", "Recepción", ar.RDP, direccion="srv")
        ar.lanzar = lambda argv: (_ for _ in ()).throw(OSError("no se encuentra mstsc"))
        self.dlg.btn_conectar.click()
        self.assertEqual(self.db.historial()[0].resultado, "Error: no se encuentra mstsc")
        self.assertIn("no se encuentra mstsc", self.avisos[-1])
        # Sin RustDesk: se pregunta (aquí, Cancelar) y no se registra nada
        ar.buscar_rustdesk = lambda ruta="": ""
        self.dlg._pedir_rustdesk = lambda: ""
        e = self.dlg._equipo_actual()
        e.rustdesk_id = "123456789"
        self.dlg.conectar(e, ar.RUSTDESK)
        self.assertEqual(len(self.db.historial()), 1)
        # Sin Asistencia rápida: no se lanza nada
        ar.buscar_asistencia_rapida = lambda: ""
        self.dlg._confirmar = lambda *_a: False
        n = len(self.lanzados)
        self.dlg.conectar(e, ar.ASISTENCIA)
        self.assertEqual(len(self.lanzados), n)
        # Sesiones que no se pueden leer
        ar.sesiones_de = lambda e: (_ for _ in ()).throw(RuntimeError("srv no responde"))
        e.direccion = "srv"
        self.dlg.conectar(e, ar.SOMBRA)
        self._esperar_tarea()
        self.assertTrue(self.db.historial()[0].resultado.startswith("Error: srv no responde"))

    def test_editar_quitar_clave_y_eliminar(self):
        self._alta("Cliente A", "Recepción", ar.RUSTDESK, rustdesk="123456789", clave="x1")
        e = self.dlg._equipo_actual()
        self.dlg.editar(e.id)
        self.assertTrue(self.dlg.chk_quitar_clave.isVisibleTo(self.dlg))
        self.dlg.chk_quitar_clave.setChecked(True)
        self.dlg.ed_nombre.setText("Recepción nueva")
        self.dlg.btn_guardar.click()
        x = self.db.listar()[0]
        self.assertEqual((x.nombre, x.con_clave), ("Recepción nueva", False))
        # Editor con cambios + Esc/cerrar: pregunta; «No» lo deja abierto
        self.dlg.show()
        self.dlg.editar(e.id)
        self.dlg.ed_nombre.setText("otro")
        self.dlg._confirmar = lambda *_a: False
        self.dlg.reject()
        self.assertTrue(self.dlg.isVisible())
        self.dlg._confirmar = lambda *_a: True
        self.dlg.reject()
        self.assertFalse(self.dlg.isVisible())
        self.assertEqual(self.db.listar()[0].nombre, "Recepción nueva")
        self.dlg.eliminar(e.id)
        self.assertEqual(self.db.listar(), [])

    def test_historial_y_exportacion(self):
        e = _equipo()
        self.db.guardar(e)
        self.db.registrar(e, ar.RUSTDESK, "Iniciada")
        self.db.registrar(e, ar.RDP, "Error: x")
        h = self.ad.HistorialAccesoDialog(self.dlg, self.db)
        self.assertEqual(h.modelo.rowCount(), 2)
        self.assertEqual(h.recuento.text(), "2 conexiones")
        h.busqueda.setText("Error")
        self.assertEqual(h.recuento.text(), "1 de 2")
        destino = os.path.join(tempfile.mkdtemp(), "h.csv")
        self.addCleanup(setattr, self.ad, "dialogo_guardar_archivo", self.ad.dialogo_guardar_archivo)
        self.ad.dialogo_guardar_archivo = lambda *a: destino
        h.exportar_csv()
        with open(destino, encoding="utf-8-sig") as f:
            contenido = f.read()
        self.assertIn("Escritorio remoto", contenido)
        self.assertNotIn("RustDesk", contenido)                 # solo lo visible
        h.deleteLater()

    def test_ventana_unica(self):
        class H(self.ad.AccesoRemotoMixin, type(self.host)):
            pass
        original = self.ad.ar.AccesoDB
        self.ad.ar.AccesoDB = lambda: self.db                   # nunca la multisys.db real
        self.addCleanup(setattr, self.ad.ar, "AccesoDB", original)
        h = H()
        h.abrir_acceso_remoto()
        primera = h._dlg_acceso
        h.abrir_acceso_remoto()
        self.assertIs(h._dlg_acceso, primera)
        h._tema_acceso_remoto()
        primera.hide()
        h.deleteLater()


if __name__ == "__main__":
    unittest.main()
