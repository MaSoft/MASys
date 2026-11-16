# -*- coding: utf-8 -*-
"""Pruebas de MASys 5.0.0: empresas y usuarios (catálogo), agenda por empresa con usuario/correo/teléfono y aviso de
equipo nuevo en el servidor propio. Base de datos temporal; nunca se lanza ningún programa.
python -m unittest discover -s tests -p "test_*.py" """
import os
import sqlite3
import sys
import tempfile
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

from msys.domain import acceso_remoto as ar  # noqa: E402
from msys.domain import clientes as cl  # noqa: E402
from msys.domain import servidor_rustdesk as sr  # noqa: E402

CLAVE = "5Qbwsde3unUcJBtrx9ZkvUmwFNoExHzpryHuPUdqlWM="


def _ruta():
    return os.path.join(tempfile.mkdtemp(), "multisys.db")


class CatalogoTest(unittest.TestCase):
    def setUp(self):
        self.db = cl.ClientesDB(_ruta())

    def test_empresas_y_usuarios(self):
        e = self.db.guardar_empresa("  Asesoría   del Norte ", "Calle Mayor 1")
        self.assertEqual(e.nombre, "Asesoría del Norte")
        with self.assertRaises(ValueError):
            self.db.guardar_empresa("asesoría DEL norte")              # sin distinguir mayúsculas
        with self.assertRaises(ValueError):
            self.db.guardar_empresa("  ")
        self.assertEqual(self.db.empresa("ASESORÍA DEL NORTE").id, e.id)
        u = self.db.guardar_usuario(cl.Usuario(0, e.id, "Marta Gil", "marta@asesoria.example", "+34 922 111 222"))
        self.assertEqual((u.empresa, u.telefono), ("Asesoría del Norte", "+34 922 111 222"))
        with self.assertRaises(ValueError):
            self.db.guardar_usuario(cl.Usuario(0, e.id, "marta gil"))  # repetido en la misma empresa
        otra = self.db.guardar_empresa("Talleres Ruiz")
        self.db.guardar_usuario(cl.Usuario(0, otra.id, "Marta Gil"))    # mismo nombre en otra empresa: sí
        for correo, telefono in (("marta@", ""), ("", "abc"), ("a b@c.es", "")):
            with self.assertRaises(ValueError):
                self.db.guardar_usuario(cl.Usuario(0, e.id, "Otro", correo, telefono))
        u.correo = "nuevo@asesoria.example"
        self.db.guardar_usuario(u)
        self.assertEqual(self.db.usuario("asesoría del norte", "MARTA GIL").correo, "nuevo@asesoria.example")
        self.assertEqual([x.nombre for x in self.db.usuarios(e.id)], ["Marta Gil"])
        self.assertEqual(len(self.db.usuarios()), 2)
        # Renombrar y quitar (la empresa se lleva sus usuarios)
        self.db.guardar_empresa("Asesoría Norte", "", e.id)
        self.assertEqual(self.db.usuario("Asesoría Norte", "Marta Gil").empresa, "Asesoría Norte")
        self.db.eliminar_empresa(e.id)
        self.assertIsNone(self.db.empresa("Asesoría Norte"))
        self.assertEqual([x.empresa for x in self.db.usuarios()], ["Talleres Ruiz"])

    def test_asegurar_y_errores(self):
        u = self.db.asegurar("Clínica Sur", "Luis", "luis@clinica.example")
        self.assertEqual((u.empresa, u.correo), ("Clínica Sur", "luis@clinica.example"))
        u2 = self.db.asegurar("clínica sur", "LUIS", "", "600 111 222")    # completa sin borrar el correo
        self.assertEqual((u2.id, u2.correo, u2.telefono), (u.id, "luis@clinica.example", "600 111 222"))
        self.assertEqual(cl.errores_usuario("", " "), ["Falta la empresa", "Falta el usuario"])
        self.assertEqual(cl.errores_usuario("A", "B", "x@y.es", "+34 600 000 000"), [])


class AgendaTest(unittest.TestCase):
    def test_equipo_con_usuario_correo_y_telefono(self):
        db = ar.AccesoDB(_ruta())
        e = ar.Equipo("e1", "Asesoría", "Recepción", ar.RUSTDESK, "123456789", contacto="Marta",
                      correo="marta@a.example", telefono="922 111 222")
        db.guardar(e)
        (x,) = db.listar()
        self.assertEqual((x.contacto, x.correo, x.telefono), ("Marta", "marta@a.example", "922 111 222"))
        self.assertIn("Falta el usuario", ar.errores_equipo(ar.Equipo("e2", "A", "PC", ar.RUSTDESK, "123456789")))
        self.assertTrue(ar.errores_equipo(ar.Equipo("e2", "A", "PC", ar.RUSTDESK, "123456789", contacto="M",
                                                    correo="mal")))

    def test_migracion_desde_4_12(self):
        ruta = _ruta()
        with sqlite3.connect(ruta) as c:      # tabla de la 4.12 (con servidor_propio y sin los datos del usuario)
            c.execute("""CREATE TABLE acceso_equipos (id TEXT PRIMARY KEY, cliente TEXT NOT NULL, nombre TEXT NOT NULL,
                metodo TEXT NOT NULL, rustdesk_id TEXT NOT NULL DEFAULT '', direccion TEXT NOT NULL DEFAULT '',
                usuario TEXT NOT NULL DEFAULT '', clave BLOB, notas TEXT NOT NULL DEFAULT '',
                ultima TEXT NOT NULL DEFAULT '', creado TEXT NOT NULL, modificado TEXT NOT NULL,
                servidor_propio INTEGER NOT NULL DEFAULT 0)""")
            c.execute("INSERT INTO acceso_equipos (id, cliente, nombre, metodo, creado, modificado) "
                      "VALUES ('v', 'Viejo', 'PC', 'rdp', 'x', 'x')")
        (v,) = ar.AccesoDB(ruta).listar()
        self.assertEqual((v.cliente, v.contacto, v.correo), ("Viejo", "", ""))


class LocalFalso:
    def __init__(self, equipos):
        self.lista = equipos

    def faltan(self):
        return []

    def equipos(self):
        return self.lista


class VentanasTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        from PyQt5.QtWidgets import QWidget
        from msys.ui import acceso_remoto_dialog as ad
        from msys.ui import clientes_dialog as cd
        self.ad, self.cd = ad, cd
        self.avisos, self.preguntas, self.lanzados = [], [], []
        self._orig = {n: getattr(ar, n) for n in ("lanzar", "buscar_rustdesk", "AccesoDB")}
        ar.lanzar = lambda argv: self.lanzados.append(list(argv))
        ar.buscar_rustdesk = lambda ruta="": r"C:\Program Files\RustDesk\rustdesk.exe"
        self.db = ar.AccesoDB(_ruta())
        ar.AccesoDB = lambda *a, **k: self.db                       # nunca la multisys.db real
        self._hook = sys.excepthook
        sys.excepthook = lambda t, v, tb: self.avisos.append(f"EXCEPCIÓN {t.__name__}: {v}")
        self.host = QWidget()
        self.dlg = ad.AccesoRemotoDialog(self.host, self.db)
        self.dlg._aviso = lambda texto, *_a: self.avisos.append(texto)
        self.respuesta = True
        self.dlg._confirmar = lambda texto: (self.preguntas.append(texto), self.respuesta)[1]

    def tearDown(self):
        for n, f in self._orig.items():
            setattr(ar, n, f)
        sys.excepthook = self._hook
        self.dlg.deleteLater()
        self.host.deleteLater()

    def _alta(self, empresa, usuario, correo="", telefono="", nombre="PC", rid="123 456 789"):
        self.dlg.btn_nuevo.click()
        self.dlg.ed_cliente.setCurrentText(empresa)
        self.dlg.ed_contacto.setCurrentText(usuario)
        self.dlg.ed_correo.setText(correo)
        self.dlg.ed_telefono.setText(telefono)
        self.dlg.ed_nombre.setText(nombre)
        self.dlg.ed_rustdesk.setText(rid)
        self.dlg.btn_guardar.click()

    def test_editor_con_catalogo(self):
        # Empresa y usuario a mano: pregunta si los añade; con «Sí» van al catálogo
        self._alta("Asesoría del Norte", "Marta Gil", "marta@a.example", "922 111 222", "Recepción")
        self.assertEqual(len(self.preguntas), 2)
        self.assertIn("no está en la lista de empresas", self.preguntas[0])
        u = self.dlg.clientes.usuario("Asesoría del Norte", "Marta Gil")
        self.assertEqual((u.correo, u.telefono), ("marta@a.example", "922 111 222"))
        # Con «No» el equipo se guarda igual y el catálogo no cambia
        self.respuesta = False
        self._alta("Talleres Ruiz", "Pedro", nombre="Oficina", rid="987654321")
        self.assertIsNone(self.dlg.clientes.empresa("Talleres Ruiz"))
        self.assertEqual(len(self.db.listar()), 2)
        # Elegir de la lista: la empresa propone sus usuarios y el usuario rellena correo y teléfono
        self.dlg.btn_nuevo.click()
        self.dlg.ed_cliente.setCurrentText("asesoría del norte")
        self.assertEqual([self.dlg.ed_contacto.itemText(i) for i in range(self.dlg.ed_contacto.count())], ["Marta Gil"])
        self.dlg.ed_contacto.setCurrentIndex(0)
        self.dlg.ed_contacto.activated.emit(0)
        self.assertEqual((self.dlg.ed_correo.text(), self.dlg.ed_telefono.text()), ("marta@a.example", "922 111 222"))
        # Correo distinto del de la lista: pregunta si la actualiza
        self.respuesta, self.preguntas = True, []
        self.dlg.ed_correo.setText("marta.gil@a.example")
        self.dlg.ed_nombre.setText("Contabilidad")
        self.dlg.ed_rustdesk.setText("111222333")
        self.dlg.btn_guardar.click()
        self.assertEqual(len(self.preguntas), 1)
        self.assertEqual(self.dlg.clientes.usuario("Asesoría del Norte", "Marta Gil").correo, "marta.gil@a.example")
        # La empresa se guarda con el nombre de la lista (mayúsculas de la lista)
        self.assertEqual({e.cliente for e in self.db.listar()}, {"Asesoría del Norte", "Talleres Ruiz"})
        # Árbol: Empresa › equipos con usuario, correo y teléfono
        a = self.dlg.arbol
        nodos = {a.topLevelItem(i).data(0, 0x0101): a.topLevelItem(i) for i in range(a.topLevelItemCount())}
        self.assertEqual(nodos["Asesoría del Norte"].childCount(), 2)
        fila = [nodos["Talleres Ruiz"].child(0).text(c) for c in range(a.columnCount())]
        self.assertEqual(fila[:4], ["Oficina", "Pedro", "", ""])
        self.dlg.busqueda.setText("marta.gil@")
        self.assertEqual(self.dlg.lbl_recuento.text(), "1 de 3")
        # Faltan empresa o usuario: no guarda («＋ Equipo» propone la empresa elegida: se vacía)
        self.dlg.btn_nuevo.click()
        self.dlg.ed_cliente.setCurrentText("")
        self.dlg.btn_guardar.click()
        self.assertIn("Falta la empresa", self.avisos[-1])
        self.assertIn("Falta el usuario", self.avisos[-1])
        self.assertEqual([x for x in self.avisos if x.startswith("EXCEPCIÓN")], [])

    def test_ventana_de_empresas(self):
        from PyQt5.QtWidgets import QDialog
        cdb = self.dlg.clientes
        v = self.cd.ClientesDialog(self.dlg, cdb)
        v._confirmar = lambda *_a: True
        v._aviso = lambda texto, *_a: self.avisos.append(texto)
        self.assertEqual(v.lbl_recuento.text(), "0 empresas · 0 usuarios")
        v.btn_usuario.click()                                        # sin empresas: avisa
        self.assertIn("Crea antes la empresa", self.avisos[-1])
        # Fichas: alta de empresa y usuario por la propia ficha (sin abrirla en pantalla)
        f = self.cd.FichaDialog(v, cdb)
        self.assertEqual(f.windowTitle(), "Nueva empresa")
        f.ed_nombre.setText("Clínica Sur")
        f.guardar()
        self.assertEqual(f.result(), QDialog.Accepted)
        f = self.cd.FichaDialog(v, cdb, es_usuario=True, empresa_inicial="Clínica Sur")
        self.assertEqual(f.windowTitle(), "Nuevo usuario")
        f.ed_nombre.setText("Luis")
        f.ed_correo.setText("mal")
        f._caja = None
        original = self.cd._caja
        self.cd._caja = lambda padre, texto, *a, **k: type("C", (), {"exec_": lambda s: self.avisos.append(texto)})()
        try:
            f.guardar()                                              # correo mal: avisa y no cierra
            self.assertIn("no es un correo válido", self.avisos[-1])
            f.ed_correo.setText("luis@clinica.example")
            f.guardar()
        finally:
            self.cd._caja = original
        v.recargar()
        self.assertEqual(v.lbl_recuento.text(), "1 empresa · 1 usuario")
        # Búsqueda, editar (ficha simulada) y quitar
        v.busqueda.setText("luis@")
        self.assertFalse(v.arbol.topLevelItem(0).isHidden())
        v.busqueda.setText("nadie")
        self.assertTrue(v.arbol.topLevelItem(0).isHidden())
        v.busqueda.clear()
        v.arbol.setCurrentItem(v.arbol.topLevelItem(0))
        v.arbol.setCurrentItem(None)
        v.btn_editar.click()
        self.assertIn("Elige en la lista", self.avisos[-1])
        v.arbol.setCurrentItem(v.arbol.topLevelItem(0))
        v.btn_eliminar.click()
        self.assertEqual(v.lbl_recuento.text(), "0 empresas · 0 usuarios")
        self.assertEqual([x for x in self.avisos if x.startswith("EXCEPCIÓN")], [])
        v.deleteLater()

    def test_aviso_de_equipo_nuevo(self):
        from PyQt5.QtWidgets import QWidget

        class Host(self.ad.AccesoRemotoMixin, QWidget):
            pass

        host = Host()
        host.tray_notify = lambda titulo, texto, *a: self.avisos.append(f"BANDEJA {texto}")
        self.db.fijar_servidor(ar.Servidor("midominio.ddns.net", CLAVE))
        self.db.guardar(ar.Equipo("x", "A", "PC", ar.RUSTDESK, "111 222 333", contacto="M"))
        local = LocalFalso([sr.EquipoServidor("111222333", "31/10/2026 09:00", "10.0.0.2"),
                            sr.EquipoServidor("12345678", "31/10/2026 09:23", "192.168.1.75")])
        nuevos = host.revisar_equipos_nuevos(local, self.db)
        self.assertEqual([q.id for q in nuevos], ["12345678"])           # el de la agenda no se avisa
        self.assertIn("BANDEJA ID 12345678 · 192.168.1.75", self.avisos)
        self.assertEqual(host.revisar_equipos_nuevos(local, self.db), [])  # una sola vez
        self.assertEqual(host.revisar_equipos_nuevos(LocalFalso([]), self.db), [])
        # La ventanita: Conectar ahora (por el servidor, sin contraseña) y Añadir a la agenda
        dlg = self.ad.EquipoNuevoDialog(host, nuevos[0], self.db)
        dlg.btn_conectar.click()
        self.assertEqual(self.lanzados[-1], [r"C:\Program Files\RustDesk\rustdesk.exe", "--connect",
                                             f"12345678@midominio.ddns.net?key={CLAVE}"])
        dlg.btn_agenda.click()
        editor = host._dlg_acceso
        self.assertIs(editor.panel.currentWidget(), editor.pag_editor)
        self.assertEqual(editor.ed_rustdesk.text(), "12345678")
        self.assertTrue(editor.chk_servidor.isChecked())
        editor.hide()
        host.deleteLater()

    def test_sin_servidor_no_abre_la_base(self):
        from PyQt5.QtWidgets import QWidget

        class Host(self.ad.AccesoRemotoMixin, QWidget):
            pass

        class SinProgramas(LocalFalso):
            def faltan(self):
                return ["hbbs.exe"]

        host = Host()
        abierta = []
        self.assertEqual(host.revisar_equipos_nuevos(SinProgramas([]), type("D", (), {
            "ajuste": lambda *a: abierta.append(1)})()), [])
        self.assertEqual(host.revisar_equipos_nuevos(LocalFalso([]), type("D", (), {
            "ajuste": lambda *a: abierta.append(1)})()), [])
        self.assertEqual(abierta, [])
        self.assertEqual(sr.equipos_sin_avisar([sr.EquipoServidor("1234567", "", "")], ["123 4567"], []), [])
        host.deleteLater()


if __name__ == "__main__":
    unittest.main()
