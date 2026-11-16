# -*- coding: utf-8 -*-
"""Pruebas de MASys 5.2.0: modo «equipo remoto» (datos del agente en las secciones de MASys). Usa los módulos y
recolectores reales de MASys sobre una ventana de prueba; nunca lee este equipo ni lanza nada.
python -m unittest discover -s tests -p "test_*.py" """
import os
import sys
import time
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

from msys.domain import agente as ag  # noqa: E402
from msys.domain import equipo_remoto as er  # noqa: E402


def _informe():
    inf = ag.nuevo_informe(["procesos", "red_local", "servicios"])
    inf.update(equipo="PC-RECEPCION", fin="2026-10-31T09:40:00")
    ag.anadir_seccion(inf, "procesos", [{"nombre": "cliente.exe", "pid": 4242}])
    ag.anadir_seccion(inf, "red_local", [{"ip": "10.9.9.9"}])
    ag.anadir_seccion(inf, "servicios", None, "Acceso denegado")
    return inf


class MotorTest(unittest.TestCase):
    def test_apartados_y_rotulo(self):
        inf = _informe()
        aps = er.apartados(inf)
        self.assertEqual([a.clave for a in aps], ["procesos", "red_local"])      # sin el que falló
        self.assertEqual((aps[0].seccion, aps[0].clase, aps[0].refresco),
                         ("⚙️ Procesos", "ProcessCollector", "refresh_processes"))
        self.assertEqual(aps[0].datos, [{"nombre": "cliente.exe", "pid": 4242}])
        self.assertEqual(er.sin_datos(inf), ["Servicios"])
        self.assertEqual(er.rotulo(inf, "Clínica Sur"), "PC-RECEPCION · Clínica Sur · datos del agente del 31/10/2026 09:40")

    def test_todos_los_lectores_tienen_seccion_y_modulo(self):
        import importlib
        for clave, _t, _s, clase, _a in ag.LECTORES:
            self.assertIn(clave, er.SECCIONES, clave)
            modulo, _r = er.SECCIONES[clave]
            self.assertTrue(hasattr(importlib.import_module(modulo), clase), f"{modulo}.{clase}")
        for modulo, nombre in er.HILOS_LOCALES:
            self.assertTrue(hasattr(importlib.import_module(modulo), nombre))
        from msys.core import informe_html
        self.assertEqual(informe_html.EQUIPO_FORZADO, "")

    def test_los_refresh_existen_en_la_ventana(self):
        """Cada refresh_* del mapa es un método de la ventana real (lo que pulsa Sincronizar Todo)."""
        import ast
        fuente = ""
        for raiz, _d, archivos in os.walk(os.path.join(RAIZ, "msys", "ui")):
            for a in archivos:
                if a.endswith(".py"):
                    fuente += open(os.path.join(raiz, a), encoding="utf-8").read()
        metodos = {n.name for n in ast.walk(ast.parse(fuente)) if isinstance(n, ast.FunctionDef)}
        for clave, (_m, refresco) in er.SECCIONES.items():
            self.assertIn(refresco, metodos, clave)


class VentanaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        from PyQt5.QtWidgets import QPushButton, QSplitter, QStackedWidget, QVBoxLayout, QWidget
        from msys.ui import equipo_remoto as eq
        from msys.ui import local_network, processes
        self.eq, self.processes, self.local_network = eq, processes, local_network
        self.originales = (processes.ProcessCollector, local_network.DeviceEnrichmentThread)
        prueba = self

        class Ventana(eq.EquipoRemotoMixin, QWidget):
            """Lo mínimo de multisys.ShareInspector, con dos secciones que usan los recolectores reales."""

            def __init__(s):
                super().__init__()
                s.worker, s.estados, s.recibido, s.enriquecidos, s.sincronizado = None, [], [], [], 0
                lay = QVBoxLayout(s)
                s.content_stack = QStackedWidget()
                divisor = QSplitter()
                divisor.addWidget(s.content_stack)
                lay.addWidget(divisor)
                s.tab_widgets, s.tree_items = {}, {}
                import socket
                from PyQt5.QtWidgets import QLabel
                for nombre in ("📊 Dashboard", "⚙️ Procesos", "🔍 Red Local", "🛠️ Servicios", "🎨 Iconos"):
                    w = QWidget()
                    s.lbl_equipo = QLabel(f"🖥️ {socket.gethostname()}", w)     # como las barras de las secciones
                    s.tab_widgets[nombre] = w
                    s.content_stack.addWidget(w)
                s.nav_tree = QWidget()
                s.btn_buscar_todo = QPushButton("🔄 Sincronizar Todo", s)
                s._build_nav_tree()
                s.actual = None

            def _build_nav_tree(s, name_list=None):
                s.tree_items = {n: True for n in (name_list or list(s.tab_widgets))}

            def seleccionar_seccion(s, nombre):
                if s.en_modo_remoto() and nombre not in s.tree_items:
                    return
                s.actual = nombre

            def set_status(s, texto):
                s.estados.append(texto)

            def buscar_todo(s):
                s.sincronizado += 1

            # Como ui/processes.refresh_processes y ui/local_network: crean el recolector por su nombre en el módulo
            def refresh_processes(s):
                s.worker = prueba.processes.ProcessCollector()
                s.worker.finished_ok.connect(lambda filas: s.recibido.append(("procesos", filas)))
                s.worker.start()

            def refresh_red_local(s):
                s.worker = prueba.local_network.NetworkDeviceCollector()
                s.worker.finished_ok.connect(s._on_red)
                s.worker.start()

            def _on_red(s, filas):
                s.recibido.append(("red_local", filas))
                hilo = prueba.local_network.DeviceEnrichmentThread(["10.9.9.9"])
                hilo.started.connect(lambda: s.enriquecidos.append("arrancado"))
                s._hilo = hilo
                hilo.start()

        self.v = Ventana()
        self.v._aviso_remoto = lambda texto: self.v.estados.append(f"AVISO {texto}")

    def tearDown(self):
        if self.v.en_modo_remoto():
            self.v.salir_equipo_remoto(releer=False)
        self.v.deleteLater()

    def _esperar(self, hasta, segundos=10):
        fin = time.time() + segundos
        while not hasta() and time.time() < fin:
            self.app.processEvents()
            time.sleep(0.01)
        for _ in range(10):
            self.app.processEvents()

    def test_entrar_ver_y_salir(self):
        import socket
        from msys.core import informe_html
        self.assertTrue(self.v.ver_equipo_remoto(_informe(), "Clínica Sur"))
        self.assertTrue(self.v.en_modo_remoto())
        # Árbol: solo lo que trajo el agente (sin Servicios, que falló, ni secciones que no lee el agente)
        self.assertEqual(list(self.v.tree_items), ["⚙️ Procesos", "🔍 Red Local"])
        self._esperar(lambda: len(self.v.recibido) == 2 and self.v.actual is not None)
        self.assertEqual(self.v.recibido, [("procesos", [{"nombre": "cliente.exe", "pid": 4242}]),
                                           ("red_local", [{"ip": "10.9.9.9"}])])
        self.assertEqual(self.v.actual, "⚙️ Procesos")
        self._esperar(lambda: not self.v._hilo.isRunning(), 3)
        self.assertEqual(self.v.enriquecidos, ["arrancado"])           # el hilo arranca…
        self.assertTrue(getattr(self.local_network.DeviceEnrichmentThread, "masys_remoto", False))  # …pero mudo
        # Informes y exportaciones: el nombre del equipo del cliente, no el de este PC
        self.assertEqual(self.v._nombre_equipo(), "PC-RECEPCION")
        self.assertIn("PC-RECEPCION", informe_html.documento("Prueba", []))
        # Secciones que no vienen en el informe: no se abren
        self.v.seleccionar_seccion("🎨 Iconos")
        self.assertEqual(self.v.actual, "⚙️ Procesos")
        self.assertTrue(self.v._franja_remota.isVisibleTo(self.v))
        self.assertIn("PC-RECEPCION", self.v._lbl_franja.text())
        self.assertIn("sin datos: Servicios", self.v.estados[0])
        self.assertEqual(self.v.lbl_equipo.text(), "🛰️ PC-RECEPCION")              # la barra de la sección
        # Volver: todo como estaba y vuelve a leer este equipo
        self.v.btn_volver_equipo.click()
        self.assertFalse(self.v.en_modo_remoto())
        self.assertIs(self.processes.ProcessCollector, self.originales[0])
        self.assertIs(self.local_network.DeviceEnrichmentThread, self.originales[1])
        self.assertEqual(informe_html.EQUIPO_FORZADO, "")
        self.assertEqual(self.v._nombre_equipo(), socket.gethostname())
        self.assertIn("🎨 Iconos", self.v.tree_items)
        self.assertEqual(self.v.lbl_equipo.text(), f"🖥️ {socket.gethostname()}")
        self.assertFalse(self.v._franja_remota.isVisibleTo(self.v))
        self._esperar(lambda: self.v.sincronizado == 1, 3)
        self.assertEqual(self.v.sincronizado, 1)

    def test_cambiar_de_equipo_y_sin_apartados(self):
        self.v.ver_equipo_remoto(_informe(), "A")
        otro = _informe()
        otro["equipo"] = "PC-OFICINA"
        self.v.ver_equipo_remoto(otro, "B")
        from msys.core import informe_html
        self.assertEqual(informe_html.EQUIPO_FORZADO, "PC-OFICINA")
        self.v.salir_equipo_remoto(releer=False)
        self.assertIs(self.processes.ProcessCollector, self.originales[0])      # sin sustituciones apiladas
        vacio = ag.nuevo_informe([])
        self.assertFalse(self.v.ver_equipo_remoto(vacio))
        self.assertIn("no trae ningún apartado", self.v.estados[-1])
        self.assertFalse(self.v.en_modo_remoto())

    def test_bloqueo_de_acciones(self):
        from PyQt5.QtCore import QEvent, QPoint, Qt
        from PyQt5.QtGui import QContextMenuEvent, QKeyEvent, QMouseEvent
        from PyQt5.QtWidgets import QCheckBox, QPushButton, QTableView
        seccion = self.v.tab_widgets["⚙️ Procesos"]          # dentro del contenido de las secciones
        textos = ("🔄 Actualizar", "📄 CSV", "🗑️ Desinstalar", "🔧 Reparar", "🌐 Informe HTML",
                  "🔄 Reiniciar Adaptador", "🔄 Restablecer todo", "⬆️ Actualizar seleccionados", "📄 Exportar CSV")
        botones = {t: QPushButton(t, seccion) for t in textos}
        filtro = QCheckBox("Solo críticos", seccion)
        tabla = QTableView(seccion)
        fuera = QPushButton("📕 PDF", self.v)                 # barra de abajo: fuera de las secciones
        self.v.btn_soporte = QPushButton("🛠️ Soporte", self.v)
        self.v.ver_equipo_remoto(_informe())
        b = self.v._bloqueo_remoto
        clic = QMouseEvent(QEvent.MouseButtonRelease, QPoint(1, 1), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        bloqueados = {t for t, w in botones.items() if b.bloquea(w, clic)}
        self.assertEqual(bloqueados, {"🗑️ Desinstalar", "🔧 Reparar", "🔄 Reiniciar Adaptador", "🔄 Restablecer todo",
                                      "⬆️ Actualizar seleccionados"})
        self.assertTrue(b.bloquea(self.v.btn_buscar_todo, clic))
        self.assertTrue(b.bloquea(self.v.btn_soporte, clic))
        self.assertFalse(b.bloquea(fuera, clic))
        self.assertFalse(b.bloquea(filtro, clic))
        self.assertFalse(b.bloquea(self.v.btn_volver_equipo, clic))
        intro = QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier)
        self.assertTrue(b.bloquea(botones["🔧 Reparar"], intro))
        self.assertFalse(b.bloquea(botones["🔧 Reparar"], QKeyEvent(QEvent.KeyPress, Qt.Key_A, Qt.NoModifier)))
        self.assertTrue(b.bloquea(tabla.viewport(), QContextMenuEvent(QContextMenuEvent.Mouse, QPoint(1, 1))))
        doble = QMouseEvent(QEvent.MouseButtonDblClick, QPoint(1, 1), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier)
        self.assertTrue(b.bloquea(tabla.viewport(), doble))
        # Aviso al pulsar algo bloqueado; y al salir, nada bloqueado
        b.eventFilter(botones["🔧 Reparar"], clic)
        self.assertTrue(self.v.estados[-1].startswith("🔒 Modo equipo remoto"))
        self.v.salir_equipo_remoto(releer=False)
        self.assertIsNone(self.v._bloqueo_remoto)

    def test_aviso_de_informe_recibido(self):
        inf = _informe()
        dlg = self.v.aviso_informe_recibido("Clínica Sur", "PC-RECEPCION", "abc", cargar=lambda iid: inf)
        dlg.btn_ver.click()
        self.assertTrue(self.v.en_modo_remoto())
        self.assertIn("Clínica Sur", self.v._remoto["rotulo"])


class InformesAgenteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def test_boton_ver_en_masys(self):
        import tempfile
        from PyQt5.QtWidgets import QWidget
        from msys.ui import informes_agente_dialog as idl
        db = ag.AgenteDB(os.path.join(tempfile.mkdtemp(), "multisys.db"))
        iid = db.guardar_informe(_informe(), "Clínica Sur")
        vistos = []

        class Principal(QWidget):
            def ver_equipo_remoto(s, informe, cliente):
                vistos.append((informe["equipo"], cliente))
                return True

        class Acceso(QWidget):
            pass

        principal = Principal()
        acceso = Acceso()
        acceso.host = principal
        dlg = idl.InformesAgenteDialog(acceso, db=db)
        from PyQt5.QtWidgets import QTreeWidgetItemIterator
        it = QTreeWidgetItemIterator(dlg.arbol)               # cliente › equipo › informe
        while it.value() is not None:
            if it.value().data(0, idl._ROL_ID) == iid:
                dlg.arbol.setCurrentItem(it.value())
            it += 1
        dlg.btn_masys.click()
        self.assertEqual(vistos, [("PC-RECEPCION", "Clínica Sur")])
        dlg.deleteLater()
        principal.deleteLater()


if __name__ == "__main__":
    unittest.main()
