# -*- coding: utf-8 -*-
"""Pruebas de 📱 Móviles Android (5.3.0) y de lo nuevo de MASysMov 1.3.2.

- Puerto desde fuera de TODOS los móviles: el primer móvil que envía se lo queda; sin datos móviles vuelve a pendiente
  (y ese móvil no lo coge otra vez enseguida); sin respuesta en 10 min vuelve a pendiente; quitar un móvil no lo pierde.
- Empresa y usuario de cada móvil; nivel de datos personales; encargo «datos» de extremo a extremo con el servidor.
- Análisis de los bloques nuevos (tests/datos/informe_movil_132.json = mismos datos que Version132Test.kt de MASysMov).
- Ventana: árbol Empresa › Usuario › Terminal con acordeón, «Sin asignar», asignar, importar, CSV y la de puertos.
Todo en carpetas temporales: nunca la movil.db ni la multisys.db reales; ningún QMessageBox real.
python -m unittest tests.test_moviles"""
import base64
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.request
from unittest import mock

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

from msys.movil import almacen as am  # noqa: E402
from msys.movil import analisis as an  # noqa: E402
from msys.movil import copias as cp  # noqa: E402

DATOS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "datos")


def _informe(nombre="informe_movil_132.json"):
    with open(os.path.join(DATOS, nombre), encoding="utf-8") as f:
        return json.load(f)


def _almacen(carpeta):
    return am.Almacen(os.path.join(carpeta, "m.db"), proteger=lambda b: b, desproteger=lambda b: b)


class PuertosDeTodosTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.alm = _almacen(self.dir)
        for i in ("m1", "m2"):
            self.alm.guardar_dispositivo(i, i.upper(), "wifi", clave=b"k" * 32)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_el_primero_se_lo_queda(self):
        id_ = self.alm.nuevo_encargo(am.SIN_MOVIL, "puerto", {"host": "", "puertos": [3389]})
        e1 = self.alm.encargos_para("m1")
        self.assertEqual([e["id"] for e in e1], [id_])
        self.assertEqual(self.alm.encargos_para("m2"), [])                 # ya es de m1
        self.assertEqual([e["id"] for e in self.alm.encargos_para("m1")], [id_])   # se le reenvía hasta que conteste
        self.assertTrue(self.alm.resultado_encargo("m1", id_, {"red": "datos móviles", "puertos": [{"puerto": 3389, "estado": "Cerrado"}]}))
        e = self.alm.encargos_puerto()[0]
        self.assertEqual((e["estado"], e["dispositivo"]), (am.HECHO, "m1"))

    def test_sin_datos_moviles_pasa_al_siguiente(self):
        id_ = self.alm.nuevo_encargo(am.SIN_MOVIL, "puerto", {"host": "", "puertos": [443]})
        self.alm.encargos_para("m1")
        self.assertTrue(self.alm.resultado_encargo("m1", id_, {"error": "Sin datos móviles: hace falta", "red": "ninguna"}))
        e = self.alm.encargos_puerto()[0]
        self.assertEqual((e["estado"], e["dispositivo"]), (am.PENDIENTE, am.SIN_MOVIL))
        self.assertEqual([i["dispositivo"] for i in e["intentos"]], ["m1"])
        self.assertEqual(self.alm.encargos_para("m1"), [])                 # m1 no lo vuelve a coger enseguida
        self.assertEqual([x["id"] for x in self.alm.encargos_para("m2")], [id_])
        # Pasado el rato, m1 podría volver a intentarlo
        self.alm.resultado_encargo("m2", id_, {"error": "Sin datos", "red": "ninguna"})
        with mock.patch.object(am, "ahora_ms", return_value=am.ahora_ms() + am.REINTENTO_MS + 1000):
            self.assertEqual([x["id"] for x in self.alm.encargos_para("m1")], [id_])

    def test_otro_error_si_cierra_el_pedido(self):
        id_ = self.alm.nuevo_encargo(am.SIN_MOVIL, "puerto", {"host": "x.invalid", "puertos": [80]})
        self.alm.encargos_para("m1")
        self.alm.resultado_encargo("m1", id_, {"error": "No se pudo resolver x.invalid", "red": "datos móviles"})
        self.assertEqual(self.alm.encargos_puerto()[0]["estado"], am.HECHO)

    def test_sin_respuesta_en_10_minutos_vuelve(self):
        id_ = self.alm.nuevo_encargo(am.SIN_MOVIL, "puerto", {"host": "", "puertos": [80]})
        self.alm.encargos_para("m1")
        self.assertEqual(self.alm.liberar_caducados(), 0)
        self.assertEqual(self.alm.liberar_caducados(am.ahora_ms() + am.CADUCA_MS + 1000), 1)
        self.assertEqual([x["id"] for x in self.alm.encargos_para("m2")], [id_])

    def test_quitar_movil_no_pierde_pedidos(self):
        hecho = self.alm.nuevo_encargo(am.SIN_MOVIL, "puerto", {"host": "", "puertos": [80]})
        self.alm.encargos_para("m1")
        self.alm.resultado_encargo("m1", hecho, {"red": "datos móviles", "puertos": []})
        medio = self.alm.nuevo_encargo(am.SIN_MOVIL, "puerto", {"host": "", "puertos": [22]})
        self.alm.encargos_para("m1")
        self.alm.nuevo_encargo("m1", "copias", {})
        self.alm.quitar_dispositivo("m1")
        por_id = {e["id"]: e for e in self.alm.encargos_puerto()}
        self.assertEqual(por_id[hecho]["estado"], am.HECHO)                # el resultado se queda en la lista
        self.assertEqual((por_id[medio]["estado"], por_id[medio]["dispositivo"]), (am.PENDIENTE, am.SIN_MOVIL))
        self.assertEqual([e for e in self.alm.encargos() if e["tipo"] == "copias"], [])

    def test_los_de_un_movil_concreto_siguen_igual(self):
        self.alm.nuevo_encargo("m2", "puerto", {"host": "", "puertos": [80]})  # pedidos de antes de 5.3.0
        self.assertEqual(self.alm.encargos_para("m1"), [])
        self.assertEqual(len(self.alm.encargos_para("m2")), 1)


class AsignacionYDatosTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.alm = _almacen(self.dir)
        self.alm.guardar_dispositivo("m1", "Redmi", "wifi")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_asignar(self):
        self.assertEqual((self.alm.dispositivos()[0]["empresa"], self.alm.dispositivos()[0]["usuario"]), ("", ""))
        self.alm.asignar("m1", "  Aceros   SL ", "Ana")
        d = self.alm.dispositivos()[0]
        self.assertEqual((d["empresa"], d["usuario"]), ("Aceros SL", "Ana"))
        with self.assertRaises(ValueError):
            self.alm.asignar("m1", "Aceros", "")
        self.alm.asignar("m1", "", "")                                     # vuelve a «Sin asignar»
        self.assertEqual(self.alm.dispositivos()[0]["empresa"], "")

    def test_migracion_aditiva(self):
        # Una movil.db de 5.2.0 (sin las columnas nuevas) se abre sin perder nada
        import sqlite3
        ruta = os.path.join(self.dir, "vieja.db")
        c = sqlite3.connect(ruta)
        c.executescript(am.ESQUEMA.split("CREATE TABLE IF NOT EXISTS datos_personales")[0])
        c.execute("INSERT INTO dispositivos (id, nombre, origen, alta) VALUES ('x', 'Viejo', 'wifi', 1)")
        c.execute("INSERT INTO encargos (id, dispositivo, creado, tipo, datos, estado) VALUES ('e', 'x', 1, 'puerto', '{}', 'Hecho')")
        c.commit()
        c.close()
        alm = am.Almacen(ruta, proteger=lambda b: b, desproteger=lambda b: b)
        self.assertEqual(alm.dispositivos()[0]["nombre"], "Viejo")
        self.assertEqual(alm.encargos_puerto()[0]["intentos"], [])

    def test_nivel_y_datos_personales(self):
        self.assertEqual(self.alm.nivel_datos(), am.NIVEL_NO)               # por defecto no se piden
        self.alm.fijar_nivel_datos(am.NIVEL_COMPLETO)
        self.assertEqual(self.alm.nivel_datos(), am.NIVEL_COMPLETO)
        with self.assertRaises(ValueError):
            self.alm.fijar_nivel_datos("todo")
        self.assertIsNone(self.alm.datos_personales("m1"))
        self.alm.guardar_datos_personales("m1", {"nivel": "recuentos", "contactos": {"permiso": True, "numero": 3}})
        self.assertEqual(self.alm.datos_personales("m1")["contactos"]["numero"], 3)
        self.alm.quitar_dispositivo("m1")
        self.assertIsNone(self.alm.datos_personales("m1"))

    def test_datos_protegidos_en_disco(self):
        # Con la protección de verdad (DPAPI), el texto no está en claro en la base
        alm = am.Almacen(os.path.join(self.dir, "dpapi.db"))
        alm.guardar_dispositivo("m1", "Redmi", "wifi")
        alm.guardar_datos_personales("m1", {"nivel": "completo", "sms": {"lista": [{"texto": "CODIGOSECRETO"}]}})
        with open(os.path.join(self.dir, "dpapi.db"), "rb") as f:
            self.assertNotIn(b"CODIGOSECRETO", f.read())
        self.assertEqual(alm.datos_personales("m1")["sms"]["lista"][0]["texto"], "CODIGOSECRETO")


class ServidorDatosTest(unittest.TestCase):
    def test_encargo_datos_extremo_a_extremo(self):
        from msys.movil import cripto, servidor
        d = tempfile.mkdtemp()
        alm = _almacen(d)
        resultados = []
        srv = servidor.ServidorMovil(alm, puerto=0, al_resultado=lambda a, b: resultados.append(b))
        srv.puerto = 0
        srv.arrancar()
        puerto = srv._httpd.server_address[1]
        try:
            qr = srv.nueva_sesion()
            clave = base64.b64decode(qr["key"])

            def post(ruta, obj, extra=None):
                req = urllib.request.Request(f"http://127.0.0.1:{puerto}{ruta}", method="POST",
                                             data=cripto.json_sellado(clave, obj, cripto.aad("m1", ruta)),
                                             headers=dict({"X-MASys-Device": "m1"}, **(extra or {})))
                with urllib.request.urlopen(req, timeout=10) as r:
                    return cripto.json_abierto(clave, r.read(), cripto.aad("m1", ruta + "|resp"))
            post("/api/pair", {"deviceId": "m1", "deviceName": "Redmi"}, {"X-MASys-Pair": qr["pair"]})
            alm.fijar_copias_auto("m1", False)
            self.assertEqual(post("/api/informe", _informe())["encargos"], [])         # «No recibir»: no se piden
            alm.fijar_nivel_datos(am.NIVEL_RECUENTOS)
            pid = alm.nuevo_encargo(am.SIN_MOVIL, "puerto", {"host": "", "puertos": [3389]})
            r = post("/api/informe", dict(_informe(), generado=1))
            tipos = {e["tipo"]: e for e in r["encargos"]}
            self.assertEqual(set(tipos), {"puerto", "datos"})
            self.assertEqual(tipos["puerto"]["id"], pid)
            self.assertEqual(tipos["datos"]["nivel"], "recuentos")
            res = {"nivel": "recuentos", "fecha": 5, "contactos": {"permiso": True, "numero": 1},
                   "llamadas": {"permiso": True, "numero": 2}, "sms": {"permiso": False, "numero": 0}}
            self.assertTrue(post("/api/encargo", {"id": tipos["datos"]["id"], "resultado": res})["ok"])
            self.assertEqual(alm.datos_personales("m1")["llamadas"]["numero"], 2)
            self.assertIn(tipos["datos"]["id"], resultados)
            # Si el técnico pasa a «No recibir» antes de que llegue, no se guarda
            alm.fijar_nivel_datos(am.NIVEL_NO)
            post("/api/encargo", {"id": "datos-9", "resultado": dict(res, contactos={"permiso": True, "numero": 99})})
            self.assertEqual(alm.datos_personales("m1")["contactos"]["numero"], 1)
            # El de puertos sin datos móviles vuelve a pendiente
            post("/api/encargo", {"id": pid, "resultado": {"error": "Sin datos móviles", "red": "ninguna"}})
            self.assertEqual(alm.encargos_puerto()[0]["estado"], am.PENDIENTE)
        finally:
            srv.parar()
            shutil.rmtree(d, ignore_errors=True)


class CopiasDeDatosTest(unittest.TestCase):
    def test_nombre_msysdatos(self):
        self.assertTrue(cp.nombre_valido("MASysMov_datos_20261031_153000.msysdatos"))
        self.assertFalse(cp.nombre_valido("MASysMov datos (1).msysdatos"))
        self.assertFalse(cp.nombre_valido("../x.msysdatos"))
        fila = dict(nombre="MASysMov_datos_20261031_153000.msysdatos", app="Copia de Datos", pkg="", version="", creada=1,
                    tamano=10, contenido="Contactos, llamadas y SMS (cifrada)", recibidos=10, completa=1, ruta="", anunciada=1, sha="")
        with mock.patch.object(cp, "estado", return_value="Recibida"):
            self.assertEqual(cp.agrupar([fila])[0]["instalable"], "No (copia de Datos)")


class AnalisisTest(unittest.TestCase):
    def setUp(self):
        self.inf = _informe()
        self.ex = an.extra(self.inf)

    def test_tablas_nuevas(self):
        self.assertIn(["Procesador", "Núcleos", "8"], self.ex["hardware"])
        self.assertEqual(self.ex["red_sim"][0][:4], ["SIM 1", "Movistar", "4G LTE", "-95 dBm"])
        self.assertIn(["Salud real", "76 %"], self.ex["bat_salud"])
        self.assertIn(["Banda de 6 GHz", "No lo dice"], self.ex["red_wifi"])
        self.assertTrue(any("Cambiar del canal 6 al 11" in v for _d, v in self.ex["red_wifi"]))
        self.assertEqual(self.ex["red_cercanas"][1][0], "(oculta)")
        self.assertEqual(self.ex["red_guardadas"][0][0], "Solo con el modo avanzado o root en MASysMov")
        self.assertEqual(self.ex["red_bt"][0], ["Auriculares", "Audio", "Sí", "80 %"])
        self.assertEqual(len(self.ex["red_velocidad"]), len(self.inf["velocidad"]))
        self.assertEqual(len(self.ex["trafico"]), len(self.inf["trafico"]["apps"]))
        self.assertEqual([p[2] for p in self.ex["pruebas"]], ["Bien", "Fallo", "Sin hacer"])

    def test_hallazgos_y_alertas(self):
        titulos = [t for _s, t, _d in an.hallazgos(self.inf)]
        self.assertIn("Batería gastada", titulos)
        self.assertIn("1 prueba(s) del móvil con fallo", titulos)
        self.assertTrue(any(t.startswith("Wi-Fi de 2,4 GHz") for t in titulos))
        self.assertEqual(an.alertas(self.inf)[0][0], "🔔 App nueva")

    def test_informe_antiguo_sin_bloques_nuevos(self):
        ex = an.extra(_informe("informe_movil_ejemplo.json"))
        for clave in ("hardware", "red_sim", "bat_salud", "pruebas", "red_wifi"):
            self.assertEqual(ex[clave], [], clave)

    def test_datos_personales(self):
        self.assertEqual(an.datos_personales(None)["resumen"], [])
        dp = {"nivel": "completo", "fecha": 1700000000000,
              "contactos": {"permiso": True, "numero": 1, "lista": [{"nombre": "Ana", "telefonos": ["600"], "correos": [], "cuentas": ["Google"], "favorito": True}]},
              "llamadas": {"permiso": True, "numero": 1, "lista": [{"numero": "600", "nombre": "Ana", "tipo": "Entrante", "fecha": 1700000000000, "duracion": 65}]},
              "sms": {"permiso": False, "numero": 0}}
        f = an.datos_personales(dp)
        self.assertIn(["SMS", "Sin permiso en el móvil"], f["resumen"])
        self.assertIn(["Nivel", "Completo"], f["resumen"])
        self.assertEqual(f["contactos"][0][:2], ["Ana", "600"])
        self.assertEqual(f["llamadas"][0][-1], "1:05")

    def test_html_con_las_secciones_nuevas(self):
        html = an.informe_html("Redmi", self.inf)
        for texto in ("🔧 Hardware · Información", "📶 Red · Líneas SIM", "✅ Pruebas · Pruebas", "📊 Consumo de datos"):
            self.assertIn(texto, html)


# ── Ventanas ───────────────────────────────────────────────────────────
from PyQt5.QtWidgets import QWidget  # noqa: E402


class _Host(QWidget):
    """Lo que la ventana necesita de MASys, sin arrancar MASys."""

    def __init__(self, carpeta):
        super().__init__()
        from msys.movil import servidor
        from msys.ui.movil import _Puente
        self.movil_almacen = _almacen(carpeta)
        self.movil_almacen.fijar_ajuste("copias_carpeta", os.path.join(carpeta, "COPIAS"))
        self.movil_puente = _Puente()
        self.movil_servidor = servidor.ServidorMovil(self.movil_almacen, puerto=0)
        self.estados = []

    def set_status(self, t):
        self.estados.append(t)

    def movil_activar_enlace(self, activar, padre=None):
        return False



class VentanasTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from msys.domain import clientes as cl
        from msys.ui import movil as mv
        self.mv = mv
        self.dir = tempfile.mkdtemp()
        self.host = _Host(self.dir)
        self.clientes = cl.ClientesDB(os.path.join(self.dir, "multisys.db"))
        self.clientes.asegurar("Aceros", "Ana")
        alm = self.host.movil_almacen
        for id_, nombre, emp, usu in (("m1", "Redmi", "Aceros", "Ana"), ("m2", "Pixel", "Aceros", "Luis"),
                                      ("m3", "Galaxy", "Bodega", "Eva"), ("m4", "Moto", "", "")):
            alm.guardar_dispositivo(id_, nombre, "wifi", clave=b"k" * 32)
            if emp:
                alm.asignar(id_, emp, usu)
        alm.guardar_informe("m1", _informe(), "wifi")
        self.avisos = []
        self.p = [mock.patch.object(mv.MovilesDialog, "_aviso_caja", lambda s, t, *a: self.avisos.append(t)),
                  mock.patch.object(mv.MovilesDialog, "_confirmar", lambda s, t: True),
                  mock.patch.object(mv.PuertosDialog, "_aviso", lambda s, t, *a: self.avisos.append(t)),
                  mock.patch.object(mv.PuertosDialog, "_confirmar", lambda s, t: True)]
        for x in self.p:
            x.start()
        self.dlg = mv.MovilesDialog(self.host, clientes=self.clientes)

    def tearDown(self):
        for x in self.p:
            x.stop()
        self.dlg.close()
        self.dlg.deleteLater()
        shutil.rmtree(self.dir, ignore_errors=True)

    def _arriba(self):
        a = self.dlg.arbol
        return {a.topLevelItem(i).text(0): a.topLevelItem(i) for i in range(a.topLevelItemCount())}

    def test_arbol_y_acordeon(self):
        arriba = self._arriba()
        self.assertEqual(list(arriba), ["🏢 Aceros  (2)", "🏢 Bodega  (1)", "❔ Sin asignar  (1)"])
        self.assertFalse(any(it.isExpanded() for it in arriba.values()))      # empresas plegadas
        aceros, bodega = arriba["🏢 Aceros  (2)"], arriba["🏢 Bodega  (1)"]
        self.dlg._clic_arbol(aceros, 0)
        self.assertTrue(aceros.isExpanded())
        ana, luis = aceros.child(0), aceros.child(1)
        self.assertEqual((ana.text(0), luis.text(0)), ("👤 Ana  (1)", "👤 Luis  (1)"))
        ana.setExpanded(True)
        luis.setExpanded(True)
        self.assertFalse(ana.isExpanded())                                    # un solo usuario abierto
        bodega.setExpanded(True)
        self.assertFalse(aceros.isExpanded())                                # una sola empresa abierta
        self.assertFalse(luis.isExpanded())
        self.dlg._clic_arbol(aceros, 0)
        self.assertFalse(bodega.isExpanded())

    def test_elegir_terminal_pinta_pestanas(self):
        aceros = self._arriba()["🏢 Aceros  (2)"]
        self.dlg.arbol.setCurrentItem(aceros.child(0).child(0))
        self.assertEqual(self.dlg._actual, "m1")
        self.assertEqual(self.dlg.lst_informes.count(), 1)
        self.assertGreater(self.dlg.m_ficha.rowCount(), 3)
        self.assertGreater(self.dlg._extra["hardware"][2].rowCount(), 0)
        self.assertIn("Aceros › Ana", self.dlg.lbl_movil.text())
        titulos = [self.dlg.tabs.tabText(i) for i in range(self.dlg.tabs.count())]
        for t in ("🔧 Hardware", "📶 Red", "📊 Consumo de datos", "✅ Pruebas", "👥 Datos personales", "💾 Copias"):
            self.assertIn(t, titulos)
        self.assertNotIn("🚪 Puerto desde fuera", titulos)                   # pasó a la ventana de todos

    def test_asignar_sin_asignar(self):
        self.dlg.recargar(seleccionar="m4")
        with mock.patch.object(self.mv.MovilesDialog, "_pedir_asignacion", lambda s, *a: ("Bodega", "Eva")):
            self.dlg.asignar()
        self.assertNotIn("❔ Sin asignar  (1)", self._arriba())
        self.assertIn("🏢 Bodega  (2)", self._arriba())
        self.assertEqual(self.dlg._actual, "m4")                              # sigue elegido y a la vista
        self.assertTrue(self._arriba()["🏢 Bodega  (2)"].isExpanded())

    def test_importar_pide_empresa_y_cancelar_no_importa(self):
        inf = dict(_informe(), dispositivo=dict(_informe()["dispositivo"], id="nuevo-1", nombre="Nuevo"))
        with mock.patch.object(self.mv.MovilesDialog, "_pedir_asignacion", lambda s, *a: None):
            self.assertFalse(self.dlg.importar_informe(inf))
        self.assertNotIn("nuevo-1", [d["id"] for d in self.host.movil_almacen.dispositivos()])
        with mock.patch.object(self.mv.MovilesDialog, "_pedir_asignacion", lambda s, *a: ("Aceros", "Ana")):
            self.assertTrue(self.dlg.importar_informe(inf))
        d = next(x for x in self.host.movil_almacen.dispositivos() if x["id"] == "nuevo-1")
        self.assertEqual((d["empresa"], d["usuario"]), ("Aceros", "Ana"))

    def test_asignar_dialogo(self):
        dlg = self.mv.AsignarDialog(None, self.clientes, "x")
        self.assertEqual([dlg.ed_empresa.itemText(i) for i in range(dlg.ed_empresa.count())], ["Aceros"])
        dlg.ed_empresa.setCurrentText("Aceros")
        self.assertEqual([dlg.ed_usuario.itemText(i) for i in range(dlg.ed_usuario.count())], ["Ana"])
        with mock.patch.object(self.mv.AsignarDialog, "_aviso", lambda s, t: self.avisos.append(t)):
            dlg.ed_usuario.setCurrentText("")
            dlg.guardar()
            self.assertIsNone(dlg.resultado)                                    # los dos son obligatorios
        with mock.patch.object(self.mv.AsignarDialog, "_confirmar", lambda s, t: True):
            dlg.ed_empresa.setCurrentText("Nueva SL")
            dlg.ed_usuario.setCurrentText("Pepe")
            dlg.guardar()
        self.assertEqual(dlg.resultado, ("Nueva SL", "Pepe"))
        self.assertIsNotNone(self.clientes.usuario("Nueva SL", "Pepe"))       # añadido al catálogo
        dlg2 = self.mv.AsignarDialog(None, self.clientes, "x")
        with mock.patch.object(self.mv.AsignarDialog, "_confirmar", lambda s, t: False):
            dlg2.ed_empresa.setCurrentText("Otra")
            dlg2.ed_usuario.setCurrentText("Sin catálogo")
            dlg2.guardar()
        self.assertEqual(dlg2.resultado, ("Otra", "Sin catálogo"))           # con «No» se guarda igual
        self.assertIsNone(self.clientes.empresa("Otra"))

    def test_nivel_de_datos_y_csv(self):
        i = self.dlg.cmb_datos.findData(am.NIVEL_RECUENTOS)
        self.dlg.cmb_datos.setCurrentIndex(i)
        self.assertEqual(self.host.movil_almacen.nivel_datos(), am.NIVEL_RECUENTOS)
        self.dlg.recargar(seleccionar="m1")
        self.dlg.tabs.setCurrentIndex([self.dlg.tabs.tabText(i) for i in range(self.dlg.tabs.count())].index("📶 Red"))
        tablas = self.dlg.tablas_visibles()
        self.assertEqual([t[0] for t in tablas][:2], ["📶 Red · Líneas SIM", "📶 Red · Wi-Fi"])
        ruta = os.path.join(self.dir, "x.csv")
        with mock.patch.object(self.mv, "dialogo_guardar_archivo", lambda *a: ruta):
            self.dlg.exportar_csv()
        with open(ruta, encoding="utf-8-sig") as f:
            texto = f.read()
        self.assertIn("Movistar", texto)
        self.assertIn("📶 Red · Wi-Fi", texto)

    def test_quitar_movil(self):
        self.dlg.recargar(seleccionar="m2")
        self.dlg.quitar()
        self.assertNotIn("m2", [d["id"] for d in self.host.movil_almacen.dispositivos()])
        self.assertEqual(self._arriba()["🏢 Aceros  (1)"].childCount(), 1)

    def test_barrido_de_botones(self):
        """Todos los botones de la ventana con un móvil elegido: ninguno revienta ni abre el explorador nativo."""
        from PyQt5.QtWidgets import QPushButton
        self.dlg.recargar(seleccionar="m1")
        saltar = {"📲 Emparejar móvil…", "📥 Importar archivo…", "👥 Empresas", "🚪 Puertos desde fuera", "📁 Carpeta…",
                  "📂 Abrir carpeta", "🌐 Informe HTML…", "📄 CSV…"}
        pulsados = []
        for b in self.dlg.findChildren(QPushButton):
            if b.text() in saltar or not b.isEnabled():
                continue
            b.click()
            pulsados.append(b.text())
        self.assertIn("📨 Pedir al móvil", pulsados)
        self.assertIn("✕ Quitar móvil", pulsados)

    def test_ventana_de_puertos(self):
        alm = self.host.movil_almacen
        pto = self.mv.PuertosDialog(self.host)
        pto.ed_host.setText("")
        pto.ed_puertos.setText("3389, 443")
        pto.pedir()
        self.assertEqual(pto.modelo.rowCount(), 1)
        self.assertEqual(pto.modelo.filas()[0][3], "Esperando a un móvil")
        id_ = alm.encargos_puerto()[0]["id"]
        alm.encargos_para("m3")
        alm.resultado_encargo("m3", id_, {"red": "datos móviles", "puertos": [{"puerto": 3389, "estado": "Cerrado"}]})
        pto.recargar()
        fila = pto.modelo.filas()[0]
        self.assertEqual(fila[3], am.HECHO)
        self.assertEqual(fila[4:6], ["3389 cerrado", "Galaxy"])
        pto.vista.selectRow(0)
        self.assertIn("Bodega › Eva › Galaxy", pto.lbl_detalle.text())
        self.assertIn("3389: Cerrado", pto.lbl_detalle.text())
        pto.ed_puertos.setText("puerto")
        pto.pedir()
        self.assertTrue(self.avisos)                                          # puertos no válidos: avisa
        pto.vista.selectRow(0)
        pto.quitar()
        self.assertEqual(pto.modelo.rowCount(), 0)
        pto.close()
        pto.deleteLater()


if __name__ == "__main__":
    unittest.main()
