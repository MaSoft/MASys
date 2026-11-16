# -*- coding: utf-8 -*-
"""Pruebas de «🔎 Comprobar ahora» (4.8.0).

- RECARGA solo cita secciones y métodos que existen en MASys.
- Con el registro REAL de «🛠️ Soporte»: qué fichas fijas se pueden comprobar.
- Comprobador con un MASys simulado (sin recolectores reales ni QSettings):
  secuencia completa, avisos validados, fallos, MASys ocupado y detener.
- Ventana: el bloque de la ficha, comprobar, «Revisar…» y «➜ Ir a».
python -m unittest discover -s tests -p "test_*.py" """
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

from msys.domain import comprobar as cp  # noqa: E402
from msys.domain import conocimiento as kb  # noqa: E402

with open(os.path.join(RAIZ, "multisys.py"), encoding="utf-8") as _f:
    SECCIONES = re.findall(r'_add_section\(self\.\w+, "([^"]+)"\)', _f.read())


def _registro_real():
    """El _registro_soporte_por_seccion de verdad sobre un objeto vacío: solo interesa qué secciones tienen detector."""
    from msys.ui.soporte import SoporteMixin

    class Vacio:
        def __getattr__(self, _n):
            return lambda *a, **k: []
    return SoporteMixin._registro_soporte_por_seccion(Vacio())


_APP = []   # la QApplication tiene que seguir viva entre pruebas


def _app():
    from PyQt5.QtWidgets import QApplication
    if not _APP:
        _APP.append(QApplication.instance() or QApplication(sys.argv))
    return _APP[0]


def _esperar(cond, segundos=10):
    app = _app()
    fin = time.time() + segundos
    while not cond() and time.time() < fin:
        app.processEvents()
        time.sleep(0.005)
    return cond()


class _Senal:
    def __init__(self):
        self.funciones = []

    def connect(self, f):
        self.funciones.append(f)


class _Trabajo:
    """Imita un recolector: «corre» unos milisegundos y puede acabar en error."""

    def __init__(self, ms=30, error=""):
        self.fin = time.time() + ms / 1000
        self.error = error
        self.finished_error = _Senal()
        self._avisado = False

    def isRunning(self):
        if time.time() < self.fin:
            return True
        if self.error and not self._avisado:
            self._avisado = True
            for f in self.finished_error.funciones:
                f(self.error)
        return False


class _Host:
    """MASys simulado: refresh_* crea un trabajo nuevo; detectores fijos por sección."""

    def __init__(self):
        self.worker = None
        self.leidas, self.ejecutados = [], []
        self.detectores = {
            "🖨️ Impresoras": lambda: [("impresoras_problema", "⚠️ Impresoras con problema: 1", lambda: None)],
            "🔍 Red Local": lambda: [],
            "⚠️ Eventos Sistema": lambda: [("ev1", "Evento A", lambda: None), ("ev2", "Evento B", lambda: None)],
            "🌐 Redes": lambda: [],
            "💽 Discos Duros": lambda: [],
        }
        self.errores_lectura = {}
        self.rompe = set()
        self.no_arranca = set()

    def _registro_soporte_por_seccion(self):
        return {s: ([], d) for s, d in self.detectores.items()}

    def _leer(self, seccion):
        if seccion in self.rompe:
            raise RuntimeError("roto")
        self.leidas.append(seccion)
        if seccion in self.no_arranca:
            return               # como un refresh_* que sale sin arrancar nada
        self.worker = _Trabajo(error=self.errores_lectura.get(seccion, ""))

    def refresh_printers(self):
        self._leer("🖨️ Impresoras")

    def refresh_red_local(self):
        self._leer("🔍 Red Local")

    def refresh_eventos_sistema(self):
        self._leer("⚠️ Eventos Sistema")

    def refresh_network_adapters(self):
        self._leer("🌐 Redes")

    def refresh_salud_disco(self):
        self._leer("💽 Discos Duros")

    def _ejecutar_aviso_soporte(self, clave, etiqueta, seccion, callback):
        self.ejecutados.append((clave, seccion))
        callback()


class MotorTest(unittest.TestCase):
    def test_recarga_cita_secciones_y_metodos_reales(self):
        codigo = ""
        for raiz, _d, archivos in os.walk(os.path.join(RAIZ, "msys")):
            for a in archivos:
                if a.endswith(".py"):
                    with open(os.path.join(raiz, a), encoding="utf-8", errors="replace") as f:
                        codigo += f.read()
        for seccion, metodo in cp.RECARGA.items():
            self.assertIn(seccion, SECCIONES, seccion)
            self.assertIn(f"def {metodo}(self", codigo, metodo)
            # El mismo método que usa «🔄 Sincronizar Todo»
            self.assertIn(f'"{metodo}"', codigo, metodo)

    def test_registro_real_y_fichas_comprobables(self):
        registro = _registro_real()
        con_detector = [s for s, (_a, d) in registro.items() if d is not None]
        for s in con_detector:
            self.assertIn(s, SECCIONES + ["📁 Contenido", "☁️ Navegadores y Nube"], s)
        comprobables = [f.id for f in kb.FICHAS_FIJAS if cp.secciones_de(f, con_detector)]
        # Casi todas las fichas tienen algo que comprobar
        self.assertGreaterEqual(len(comprobables), 30, comprobables)
        imp = cp.secciones_de(next(f for f in kb.FICHAS_FIJAS if f.id == "imp-no-imprime"), con_detector)
        self.assertEqual(imp[0], "🖨️ Impresoras")
        self.assertEqual(len(imp), len(set(imp)))
        # Copias de Seguridad no es una sección: nunca se comprueba
        for f in kb.FICHAS_FIJAS:
            self.assertNotIn(kb.COPIAS, cp.secciones_de(f, con_detector + [kb.COPIAS]))

    def test_avisos_de_y_resumen(self):
        avisos, ocultos = cp.avisos_de([("a", "A", print), ("b", "B"), ("c", "C", None), "basura"], ["b"])
        self.assertEqual([a.clave for a in avisos], ["a", "c"])
        self.assertEqual(ocultos, 1)
        self.assertIsNotNone(avisos[0].resolver)
        self.assertIsNone(avisos[1].resolver)
        r = [cp.Resultado("x", cp.BIEN), cp.Resultado("y", cp.BIEN)]
        self.assertTrue(cp.resumen(r).startswith("✔"))
        r[1].estado, r[1].avisos = cp.AVISOS, avisos
        self.assertEqual(cp.resumen(r), "⚠️ 2 avisos")
        r.append(cp.Resultado("z", cp.ERROR, error="e"))
        self.assertEqual(cp.resumen(r), "⚠️ 2 avisos · 1 sección sin poder leer")
        r.append(cp.Resultado("w"))
        self.assertEqual(cp.resumen(r), "Comprobando… 3 de 4")
        self.assertIn("no tiene nada", cp.resumen([]))


class ComprobadorTest(unittest.TestCase):
    def setUp(self):
        _app()
        from msys.ui.comprobar_ficha import Comprobador
        self.C = Comprobador
        self.host = _Host()

    def _correr(self, secciones, validados=()):
        c = self.C(self.host, "f", secciones, validados=lambda: validados, espera_ms=5)
        fin = []
        c.terminado.connect(lambda: fin.append(1))
        c.iniciar()
        self.assertTrue(_esperar(lambda: fin), "no terminó")
        return c

    def test_secuencia_completa(self):
        c = self._correr(["🖨️ Impresoras", "🔍 Red Local", "⚠️ Eventos Sistema"], validados=["ev2"])
        self.assertEqual(self.host.leidas, ["🖨️ Impresoras", "🔍 Red Local", "⚠️ Eventos Sistema"])
        estados = [r.estado for r in c.resultados]
        self.assertEqual(estados, [cp.AVISOS, cp.BIEN, cp.AVISOS])
        self.assertEqual([a.clave for a in c.resultados[2].avisos], ["ev1"])
        self.assertEqual(c.resultados[2].validados, 1)
        self.assertFalse(c.activo)
        self.assertEqual(c.resumen(), "⚠️ 2 avisos")

    def test_fallos_no_paran_el_resto(self):
        self.host.rompe.add("🖨️ Impresoras")
        self.host.errores_lectura["🔍 Red Local"] = "sin permiso"
        self.host.no_arranca.add("🌐 Redes")
        c = self._correr(["🖨️ Impresoras", "🔍 Red Local", "🌐 Redes", "💽 Discos Duros", "📶 WiFi"])
        self.assertEqual([r.estado for r in c.resultados], [cp.ERROR, cp.ERROR, cp.ERROR, cp.BIEN, cp.ERROR])
        self.assertIn("roto", c.resultados[0].error)
        self.assertIn("sin permiso", c.resultados[1].error)
        self.assertIn("ocupado", c.resultados[2].error)
        self.assertIn("no se puede volver a leer", c.resultados[4].error)

    def test_espera_a_que_masys_termine_otra_lectura(self):
        self.host.worker = _Trabajo(ms=150)
        inicio = time.time()
        c = self._correr(["💽 Discos Duros"])
        self.assertGreaterEqual(time.time() - inicio, 0.14)
        self.assertEqual(c.resultados[0].estado, cp.BIEN)

    def test_detener(self):
        c = self.C(self.host, "f", ["🖨️ Impresoras", "🔍 Red Local"], validados=lambda: (), espera_ms=5)
        c.iniciar()
        _esperar(lambda: c.resultados[0].estado == cp.LEYENDO)
        c.cancelar()
        self.assertTrue(all(r.estado == cp.CANCELADA for r in c.resultados))
        self.assertEqual(cp.resumen(c.resultados), "Comprobación detenida")
        _esperar(lambda: False, 0.2)
        self.assertEqual(self.host.leidas, ["🖨️ Impresoras"])   # no siguió con la siguiente


class VentanaComprobarTest(unittest.TestCase):
    def setUp(self):
        _app()
        from PyQt5.QtWidgets import QWidget
        from msys.ui import conocimiento_dialog as cd

        errores = self.errores = []
        self._hook = sys.excepthook
        sys.excepthook = lambda t, v, tb: errores.append(f"{t.__name__}: {v}")
        base = _Host

        class Host(QWidget, base):
            def __init__(self):
                QWidget.__init__(self)
                base.__init__(self)
                self.tab_widgets = {s: QWidget() for s in SECCIONES}
                self._section_order = list(SECCIONES)
                self.vistas = []

            def seleccionar_seccion(self, nombre):
                self.vistas.append(nombre)

        self.cd = cd
        self.host = Host()
        self.dlg = cd.BaseConocimientoDialog(self.host, kb.ConocimientoDB(os.path.join(tempfile.mkdtemp(), "multisys.db")))
        self.dlg._aviso = lambda *a: errores.append(f"aviso: {a[1]}")

    def tearDown(self):
        sys.excepthook = self._hook
        if self.dlg._comprobador is not None:
            self.dlg._comprobador.cancelar()
        self.dlg.deleteLater()
        self.host.deleteLater()

    def _botones(self, texto):
        from PyQt5.QtWidgets import QPushButton
        return [b for b in self.dlg.contenido_ficha.findChildren(QPushButton)
                if b.text() == texto and b.isVisibleTo(self.dlg.contenido_ficha)]

    def test_comprobar_ficha_y_revisar(self):
        from msys.ui import comprobar_ficha
        self.dlg._mostrar_ficha("imp-no-imprime")
        self.assertIsNotNone(self.dlg._caja_comprobar)
        boton, = self._botones("🔎 Comprobar ahora")
        # Sin la validación real (QSettings del usuario): se sustituye
        original = comprobar_ficha._validados_reales
        comprobar_ficha._validados_reales = lambda: ()
        try:
            boton.click()
            self.assertEqual(len(self._botones("■ Detener")), 1)
            self.assertTrue(_esperar(lambda: not self.dlg._comprobador.activo))
        finally:
            comprobar_ficha._validados_reales = original
        self.assertEqual(self.host.leidas, ["🖨️ Impresoras", "🔍 Red Local", "⚠️ Eventos Sistema"])
        self.assertEqual(len(self._botones("🔎 Comprobar de nuevo")), 1)
        revisar = self._botones("Revisar…")
        self.assertEqual(len(revisar), 3)
        for b in revisar:
            b.click()
        self.assertEqual([s for _c, s in self.host.ejecutados],
                         ["🖨️ Impresoras", "⚠️ Eventos Sistema", "⚠️ Eventos Sistema"])
        # «➜ Ir a» de los resultados (3) + los de los pasos
        ficha = next(f for f in kb.FICHAS_FIJAS if f.id == "imp-no-imprime")
        ir = self._botones("➜ Ir a")
        self.assertEqual(len(ir), 3 + sum(1 for p in ficha.pasos if p.seccion))
        for b in ir[:3]:
            b.click()
        self.assertEqual(self.host.vistas, ["🖨️ Impresoras", "🔍 Red Local", "⚠️ Eventos Sistema"])
        # Otra ficha y vuelta: el resultado sigue
        self.dlg._mostrar_ficha("red-sin-internet")
        self.assertEqual(len(self._botones("🔎 Comprobar ahora")), 1)
        self.dlg._mostrar_ficha("imp-no-imprime")
        self.assertEqual(len(self._botones("Revisar…")), 3)
        self.assertEqual(self.errores, [])

    def test_solo_una_a_la_vez_y_ficha_sin_nada(self):
        self.host.worker = _Trabajo(ms=10000)    # MASys ocupado: la comprobación se queda esperando
        self.dlg._mostrar_ficha("imp-no-imprime")
        self._botones("🔎 Comprobar ahora")[0].click()
        self.dlg._mostrar_ficha("red-sin-internet")
        b, = self._botones("🔎 Comprobar ahora")
        self.assertFalse(b.isEnabled())
        self.dlg._mostrar_ficha("imp-no-imprime")
        self._botones("■ Detener")[0].click()
        self.assertEqual(len(self._botones("🔎 Comprobar de nuevo")), 1)
        # Ficha propia sin secciones con detector: sin bloque
        f = kb.nueva_ficha()
        f.titulo = "Sin nada"
        f.pasos = [kb.Paso(f"{f.id}.1", "Algo", "💻 Sistema")]
        self.dlg.db.guardar(f)
        self.dlg.recargar(f.id)
        self.dlg._mostrar_ficha(f.id)
        self.assertIsNone(self.dlg._caja_comprobar)
        self.assertEqual(self.errores, [])


if __name__ == "__main__":
    unittest.main()
