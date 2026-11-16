# -*- coding: utf-8 -*-
"""Pruebas del agente de análisis remoto (4.7.0). No se ejecuta ningún
recolector real ni se abre el navegador: los recolectores se sustituyen por
funciones de prueba. Bases de datos y carpetas temporales.
python -m unittest discover -s tests -p "test_*.py" """
import json
import os
import re
import sys
import tempfile
import time
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

from msys.domain import agente as ag  # noqa: E402
from msys.models import rows as R  # noqa: E402


def _software(nombre, version="1.0"):
    return R.InstalledSoftwareRow(nombre=nombre, version=version, editor="Editor", fecha_instalacion="01/01/2026",
                                  carpeta_instalacion="C:\\x")


def _informe(software, equipo="PC-01", fin="2026-10-27T10:00:00"):
    inf = ag.nuevo_informe(["software", "actualizaciones", "sistema", "wifi"])
    inf["equipo"], inf["fin"] = equipo, fin
    ag.anadir_seccion(inf, "software", software)
    ag.anadir_seccion(inf, "actualizaciones", {"Installed": [{"KB": "KB500", "Descripcion": "Update", "Instalacion": "01/10/2026",
                                                              "Usuario": "SYSTEM"}],
                                               "Pending": [{"KB": "KB600", "Titulo": "Seguridad", "Tamano": "10 MB",
                                                            "Criticidad": "Crítica", "Fecha": "20/10/2026"}],
                                               "RebootPending": True})
    ag.anadir_seccion(inf, "sistema", {"Equipo": equipo, "Clave de Windows": "ABCDE-FGHIJ-KLMNO-PQRST-UVWXY",
                                       "Memoria": {"Total": "16 GB"}})
    ag.anadir_seccion(inf, "wifi", [R.WiFiRow(nombre="Oficina", contraseña="SuperSecreta1", autenticacion="WPA2", cifrado="AES")])
    ag.anadir_seccion(inf, "red_local", None, error="Tiempo agotado")
    return inf


class CodigoYCifradoTest(unittest.TestCase):
    def test_codigo_del_tecnico(self):
        _priv, pub = ag.generar_par()
        codigo = ag.codigo_tecnico(pub)
        self.assertTrue(codigo.startswith("MSA1-"))
        self.assertEqual(ag.leer_codigo(codigo), pub)
        self.assertEqual(ag.leer_codigo("  " + codigo.lower().replace("-", " ") + "\n"), pub)   # tolerante
        # Cualquier letra cambiada, también la última (que lleva bits de relleno)
        rotos = [codigo[:-1] + c for c in "ABCDEFGH234567" if c != codigo[-1]]
        medio = codigo.index("-", 10) + 2
        rotos.append(codigo[:medio] + ("A" if codigo[medio] != "A" else "B") + codigo[medio + 1:])
        for malo in ["", "XYZ1-AAAAA", codigo[:30]] + rotos:
            with self.assertRaises(ag.ErrorAgente, msg=malo):
                ag.leer_codigo(malo)

    def test_cifrar_y_descifrar(self):
        priv, pub = ag.generar_par()
        otra_priv, _otra_pub = ag.generar_par()
        inf = _informe([_software("7-Zip")])
        datos = ag.cifrar(inf, pub)
        self.assertTrue(datos.startswith(ag.MAGIA))
        self.assertNotIn(b"7-Zip", datos)
        self.assertEqual(ag.descifrar(datos, [otra_priv, priv]), inf)
        with self.assertRaises(ag.ErrorAgente):
            ag.descifrar(datos, [otra_priv])                      # de otro técnico
        alterado = bytearray(datos)
        alterado[-5] ^= 1
        with self.assertRaises(ag.ErrorAgente):
            ag.descifrar(bytes(alterado), [priv])                 # modificado
        with self.assertRaises(ag.ErrorAgente):
            ag.descifrar(b"PK\x03\x04 no es", [priv])


class SanearTest(unittest.TestCase):
    def test_sin_secretos(self):
        inf = _informe([_software("Office")])
        texto = json.dumps(inf, ensure_ascii=False)
        self.assertNotIn("SuperSecreta1", texto)
        self.assertNotIn("ABCDE-FGHIJ-KLMNO-PQRST", texto)
        self.assertIn("*****-*****-*****-*****-UVWXY", texto)          # como lo enseña Windows
        self.assertEqual(inf["secciones"]["wifi"]["datos"][0]["contraseña"], ag.OCULTO)

    def test_lineas_de_ordenes_y_campos(self):
        casos = {
            "New-LocalUser bob -Password 'Abc123!' -Name x": "Abc123!",
            "$p = ConvertTo-SecureString \"Pa55w0rd\" -AsPlainText -Force": "Pa55w0rd",
            "net user contable Secr3t /add": "Secr3t",
            "cmdkey /add:srv /user:x /pass:Qwerty9": "Qwerty9",
            "conn = 'Server=x;password=Zx9!;'": "Zx9!",
            "manage-bde -protectors 123456-123456-123456-123456-123456-123456-123456-123456": "123456-123456",
        }
        for orden, secreto in casos.items():
            self.assertNotIn(secreto, ag.sanear_texto(orden), orden)
        self.assertEqual(ag.sanear_texto("Get-Service | Where Status -eq Running"), "Get-Service | Where Status -eq Running")
        d = ag.sanear({"password_expires": "Nunca", "password_required": "Sí", "token": "abc", "Contraseña": ""})
        self.assertEqual(d, {"password_expires": "Nunca", "password_required": "Sí", "token": ag.OCULTO, "Contraseña": ""})


class TablasYVisitasTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_reconstruir_y_mismas_columnas_que_masys(self):
        from msys.models.table_models import InstalledSoftwareTableModel, InstalledUpdateTableModel, PendingUpdateTableModel
        inf = _informe([_software("7-Zip", "23.01")])
        s = inf["secciones"]["software"]
        fila = ag.reconstruir(s["datos"])[0]
        self.assertIsInstance(fila, R.InstalledSoftwareRow)
        self.assertEqual(fila.version, "23.01")
        (titulo, cab, filas), = ag.tablas_de_seccion("software", s)
        self.assertEqual(cab, InstalledSoftwareTableModel.HEADERS)
        self.assertEqual(filas[0][0], "7-Zip")
        tablas = ag.tablas_de_seccion("actualizaciones", inf["secciones"]["actualizaciones"])
        cabeceras = [t[1] for t in tablas]
        self.assertIn(InstalledUpdateTableModel.HEADERS, cabeceras)
        self.assertIn(PendingUpdateTableModel.HEADERS, cabeceras)
        datos = ag.tablas_de_seccion("sistema", inf["secciones"]["sistema"])[0]
        self.assertEqual(datos[1], ["Dato", "Valor"])
        self.assertIn(["Memoria › Total", "16 GB"], datos[2])
        # Campos que ya no existen se ignoran; los que falten toman su valor por defecto
        raro = ag.reconstruir({"__clase": "PendingUpdateRow", "kb": "1", "titulo": "t", "tamano": "", "criticidad": "",
                               "fecha": "", "campo_nuevo": 5})
        self.assertIsInstance(raro, R.PendingUpdateRow)
        self.assertEqual(raro.tipo, "Software")

    def test_cambios_entre_dos_informes(self):
        from msys.domain import visitas
        antes = _informe([_software("7-Zip", "22.0"), _software("Java 8")], fin="2026-10-01T10:00:00")
        despues = _informe([_software("7-Zip", "23.01"), _software("Chrome")], fin="2026-10-27T10:00:00")
        cambios = visitas.comparar(ag.foto_para_visitas(antes), ag.foto_para_visitas(despues))
        tipos = {(c.elemento, c.tipo) for c in cambios}
        self.assertIn(("Chrome", visitas.ANADIDO), tipos)
        self.assertIn(("Java 8", visitas.QUITADO), tipos)
        self.assertIn(("7-Zip", visitas.CAMBIADO), tipos)
        self.assertIn("parches", ag.foto_para_visitas(despues)["data"])

    def test_lectores_son_los_de_sincronizar_todo(self):
        from msys import collectors
        with open(os.path.join(RAIZ, "msys", "ui", "reporting.py"), encoding="utf-8") as f:
            metodos = re.findall(r'\("[^"]+",\s+"(refresh_\w+)"\)', f.read())
        self.assertEqual(len(metodos), len(ag.LECTORES))
        with open(os.path.join(RAIZ, "multisys.py"), encoding="utf-8") as f:
            secciones = set(re.findall(r'_add_section\(self\.\w+, "([^"]+)"\)', f.read()))
        for clave, _t, seccion, clase, _a in ag.LECTORES:
            cls = getattr(collectors, clase)
            self.assertTrue(hasattr(cls, "finished_ok") and hasattr(cls, "finished_error"), clase)
            self.assertIn(seccion, secciones, clave)


class RecolectorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def _clase(self, modo):
        from PyQt5.QtCore import QThread, pyqtSignal

        class Hilo(QThread):
            finished_ok = pyqtSignal(list)
            finished_error = pyqtSignal(str)

            def __init__(self, **kw):
                super().__init__()
                self.kw = kw
                self._stop = False

            def request_stop(self):
                self._stop = True

            def run(self):
                if modo == "ok":
                    self.finished_ok.emit([_software("A")])
                elif modo == "error":
                    self.finished_error.emit("sin permiso")
                else:
                    while not self._stop:
                        time.sleep(0.05)
        return Hilo

    def test_ejecutar_uno(self):
        from msys.agente import recolector as rc
        for modo in ("ok", "error", "lento"):
            rc._CLASES["Falso"] = self._clase(modo)
            datos, error, _s = rc.ejecutar_uno("Falso", {}, tope=1)
            if modo == "ok":
                self.assertEqual((len(datos), error), (1, ""))
            elif modo == "error":
                self.assertEqual((datos, error), (None, "sin permiso"))
            else:
                self.assertIn("No terminó", error)
        rc._CLASES.pop("Falso", None)

    def test_recolectar_con_fallos_y_cancelar(self):
        from msys.agente import recolector as rc
        llamadas = []

        def falso(clase, args):
            llamadas.append((clase, args))
            if clase == "WiFiCollector":
                return None, "No hay WiFi", 0.1
            return [_software("X")], "", 0.1

        inf = rc.recolectar(["software", "wifi", "eventos"], ejecutar=falso)
        self.assertEqual(set(inf["secciones"]), {"software", "wifi", "eventos"})
        self.assertEqual(inf["secciones"]["wifi"]["error"], "No hay WiFi")
        self.assertIn(("SystemEventCollector", {"dias": 7, "incluir_security": False}), llamadas)
        self.assertTrue(inf["fin"])
        inf = rc.recolectar(["software", "wifi"], cancelado=lambda: True, ejecutar=falso)
        self.assertTrue(inf.get("cancelado"))


class BaseDatosTest(unittest.TestCase):
    def setUp(self):
        self.db = ag.AgenteDB(os.path.join(tempfile.mkdtemp(), "multisys.db"))

    def test_claves_rotan_y_los_archivos_viejos_se_abren(self):
        pub1 = self.db.publica_activa()
        self.assertEqual(self.db.publica_activa(), pub1)          # la crea solo una vez
        viejo = ag.cifrar(_informe([_software("A")]), pub1)
        pub2 = self.db.nueva_clave()
        self.assertNotEqual(pub1, pub2)
        self.assertEqual(self.db.publica_activa(), pub2)
        self.assertEqual(len(self.db.privadas()), 2)
        self.assertEqual(ag.descifrar(viejo, self.db.privadas())["equipo"], "PC-01")

    def test_informes(self):
        a = _informe([_software("A")], fin="2026-10-01T10:00:00")
        b = _informe([_software("B")], fin="2026-10-27T10:00:00")
        with self.assertRaises(ValueError):
            self.db.guardar_informe(a, " ")
        ia, ib = self.db.guardar_informe(a, "Cliente A"), self.db.guardar_informe(b, "Cliente A")
        lista = self.db.listar_informes()
        self.assertEqual([x["id"] for x in lista], [ib, ia])         # el más reciente primero
        self.assertEqual(lista[0]["apartados"], "4 de 5")
        self.assertEqual(self.db.anterior(ib), ia)
        self.assertIsNone(self.db.anterior(ia))
        self.assertEqual(self.db.informe(ia)["secciones"]["software"]["datos"][0]["nombre"], "A")
        self.db.eliminar_informe(ia)
        self.assertIsNone(self.db.anterior(ib))


class VentanasTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.errores = []
        self._hook = sys.excepthook
        sys.excepthook = lambda t, v, tb: self.errores.append(f"{t.__name__}: {v}")
        self.db = ag.AgenteDB(os.path.join(tempfile.mkdtemp(), "multisys.db"))

    def tearDown(self):
        sys.excepthook = self._hook

    def _falso(self, clase, args):
        if clase == "InstalledSoftwareCollector":
            return [_software("7-Zip")], "", 0.1
        if clase == "WiFiCollector":
            return [R.WiFiRow(nombre="Casa", contraseña="NoDebeSalir", autenticacion="WPA2", cifrado="AES")], "", 0.1
        return None, "Sin datos de prueba", 0.0

    def test_agente_del_cliente(self):
        from msys.agente.ventana import VentanaAgente
        v = VentanaAgente(es_admin=False)
        self.assertFalse(v.btn_analizar.isEnabled())
        v.txt_codigo.setText("MSA1-ESTO-NO-VALE")
        self.assertFalse(v.btn_analizar.isEnabled())
        self.assertIn("no es válido", v.lbl_codigo.text())
        v.txt_codigo.setText(ag.codigo_tecnico(self.db.publica_activa()))
        self.assertTrue(v.btn_analizar.isEnabled())
        self.assertEqual(len(v.elegidos()), len(ag.LECTORES))
        from PyQt5.QtCore import Qt
        for i in range(v.arbol.topLevelItemCount()):                 # desmarcar todo: no se puede analizar
            v.arbol.topLevelItem(i).setCheckState(0, Qt.Unchecked)
        self.assertEqual(v.elegidos(), [])
        self.assertFalse(v.btn_analizar.isEnabled())
        for i in range(v.arbol.topLevelItemCount()):
            v.arbol.topLevelItem(i).setCheckState(0, Qt.Checked)
        carpeta = tempfile.mkdtemp()
        v.analizar(ejecutar=self._falso, carpeta=carpeta)
        self.assertEqual(v.paginas.currentIndex(), 2)
        archivos = os.listdir(carpeta)
        self.assertEqual(len(archivos), 1)
        self.assertTrue(archivos[0].endswith(".msysinf"))
        with open(os.path.join(carpeta, archivos[0]), "rb") as f:
            contenido = f.read()
        self.assertNotIn(b"NoDebeSalir", contenido)
        inf = ag.descifrar(contenido, self.db.privadas())
        self.assertEqual(inf["secciones"]["software"]["datos"][0]["nombre"], "7-Zip")
        self.assertIn("No se pudieron leer", v.lbl_fallos.text())
        # Un segundo archivo no pisa el primero
        v.analizar(ejecutar=self._falso, carpeta=carpeta)
        self.assertEqual(len(os.listdir(carpeta)), 2)
        self.assertEqual(self.errores, [])
        v.deleteLater()

    def test_informes_recibidos(self):
        from msys.ui import informes_agente_dialog as idl
        abiertos = []
        original = idl.QDesktopServices.openUrl
        idl.QDesktopServices.openUrl = staticmethod(lambda url: abiertos.append(url.toLocalFile()))
        self.addCleanup(setattr, idl.QDesktopServices, "openUrl", original)
        pub = self.db.publica_activa()
        carpeta = tempfile.mkdtemp()
        rutas = []
        for i, (soft, fin) in enumerate((([_software("A")], "2026-10-01T10:00:00"), ([_software("B")], "2026-10-27T10:00:00"))):
            ruta = os.path.join(carpeta, f"i{i}.msysinf")
            with open(ruta, "wb") as f:
                f.write(ag.cifrar(_informe(soft, fin=fin), pub))
            rutas.append(ruta)
        from msys.ui import recepcion_agente as rec
        # Recepción con la base de datos de la prueba: la de por defecto abriría la multisys.db real
        r = rec.RecepcionAgente(self.db, puerto=0, direccion="127.0.0.1")
        d = idl.InformesAgenteDialog(None, self.db, clientes_agenda=lambda: [("Asesoría del Norte", "pc-01")],
                                     recepcion_=r)
        propuestos = []
        d._pedir_cliente = lambda equipo: (propuestos.append(equipo), "Asesoría del Norte")[1]
        d._carpeta = lambda cliente: carpeta
        for r in rutas:
            d.abrir_archivo(r)
        self.assertEqual(propuestos, ["PC-01", "PC-01"])
        self.assertEqual(d.lbl_recuento.text(), "2 informes")
        cliente = d.arbol.topLevelItem(0)
        self.assertEqual(cliente.text(0), "Asesoría del Norte  (2)")
        reciente = cliente.child(0).child(0)
        d.arbol.setCurrentItem(reciente)
        self.assertTrue(d.btn_ver.isEnabled())
        d.ver_informe()
        self.assertTrue(abiertos and abiertos[-1].endswith(".html"))
        with open(abiertos[-1], encoding="utf-8") as f:
            html = f.read()
        self.assertIn("PC-01", html)
        self.assertNotIn("SuperSecreta1", html)
        cambios, _a, _d = d.cambios(reciente.data(0, idl._ROL_ID))
        self.assertTrue(any(c.elemento == "B" for c in cambios))
        d.busqueda.setText("zzz")
        self.assertEqual(d.lbl_recuento.text(), "0 de 2")
        # Un archivo que no es del agente: avisa y no guarda
        malo = os.path.join(carpeta, "malo.msysinf")
        with open(malo, "wb") as f:
            f.write(b"no es un informe")
        avisos = []
        self.addCleanup(setattr, idl, "_caja", idl._caja)
        idl._caja = lambda padre, texto, icono=None: type("C", (), {"exec_": lambda s: avisos.append(texto)})()
        d.abrir_archivo(malo)
        self.assertEqual(len(self.db.listar_informes()), 2)
        self.assertTrue(avisos and "No se pudo abrir" in avisos[0])
        c = idl.CodigoTecnicoDialog(d, self.db)
        self.assertEqual(ag.leer_codigo(c.txt.text()), pub)
        c.copiar_instrucciones()
        from PyQt5.QtGui import QGuiApplication
        self.assertIn(c.txt.text(), QGuiApplication.clipboard().text())
        self.assertEqual(self.errores, [])
        d.deleteLater()


if __name__ == "__main__":
    unittest.main()
