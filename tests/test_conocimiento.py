# -*- coding: utf-8 -*-
"""Pruebas de la Base de conocimiento (4.5.0).

- Las fichas fijas solo citan cosas que EXISTEN en MASys: cada texto entre «»
  aparece tal cual en el código, cada sección existe en multisys.py y cada
  pestaña existe como addTab(...).
- Búsqueda, duplicar, fichas propias en una multisys.db temporal.
- Ventana: barrido de todos los botones de todas las fichas con un MASys
  simulado (sin arrancar recolectores), editor completo y «➜ Ir a» a pestaña.
python -m unittest discover -s tests -p "test_*.py" """
import glob
import os
import re
import sqlite3
import sys
import tempfile
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

from msys.domain import conocimiento as kb  # noqa: E402


def _codigo_masys() -> str:
    """Todo el código de MASys menos las propias fichas y las copias .bak."""
    rutas = glob.glob(os.path.join(RAIZ, "msys", "**", "*.py"), recursive=True) + [os.path.join(RAIZ, "multisys.py")]
    propio = os.path.normcase(os.path.join(RAIZ, "msys", "domain", "conocimiento.py"))
    partes = []
    for r in rutas:
        if os.path.normcase(r) == propio:
            continue
        with open(r, encoding="utf-8", errors="replace") as f:
            partes.append(f.read())
    return "\n".join(partes)


CODIGO = _codigo_masys()
with open(os.path.join(RAIZ, "multisys.py"), encoding="utf-8") as _f:
    SECCIONES = re.findall(r'_add_section\(self\.\w+, "([^"]+)"\)', _f.read())
# addTab(página, "título") aunque la página ocupe varias líneas
PESTANAS = set(re.findall(r'addTab\(.{0,600}?,\s*"([^"]+)"\)', CODIGO, re.S))


class FichasFijasTest(unittest.TestCase):
    def test_estructura(self):
        ids = [f.id for f in kb.FICHAS_FIJAS]
        self.assertEqual(len(ids), len(set(ids)), "ids de ficha repetidos")
        self.assertGreaterEqual(len(ids), 30)
        temas = {k for k, _t in kb.TEMAS}
        pasos = []
        for f in kb.FICHAS_FIJAS:
            self.assertIn(f.tema, temas, f.id)
            self.assertTrue(f.titulo and f.sintomas and f.causas and f.pasos, f.id)
            self.assertFalse(f.propia)
            pasos += [p.id for p in f.pasos]
        self.assertEqual(len(pasos), len(set(pasos)), "ids de paso repetidos")
        # Todos los temas tienen al menos una ficha
        self.assertEqual(temas, {f.tema for f in kb.FICHAS_FIJAS})

    def test_botones_citados_existen_en_masys(self):
        faltan = []
        for f in kb.FICHAS_FIJAS:
            for p in f.pasos:
                for citado in re.findall(r"«([^»]+)»", p.texto):
                    if f'"{citado}"' not in CODIGO:
                        faltan.append(f"{p.id}: «{citado}»")
        self.assertEqual(faltan, [], "Textos que no existen en MASys")

    def test_secciones_y_pestanas_existen(self):
        self.assertGreater(len(SECCIONES), 40)
        self.assertGreater(len(PESTANAS), 10)
        malos = []
        for f in kb.FICHAS_FIJAS:
            for p in f.pasos:
                if p.seccion and p.seccion not in kb.VENTANAS and p.seccion not in SECCIONES:
                    malos.append(f"{p.id}: sección {p.seccion}")
                if p.pestana and p.pestana not in PESTANAS:
                    malos.append(f"{p.id}: pestaña {p.pestana}")
                if p.pestana and not p.seccion:
                    malos.append(f"{p.id}: pestaña sin sección")
        self.assertEqual(malos, [])
        for ventana in kb.VENTANAS:
            self.assertIn(f'"{ventana}"', CODIGO)


class BusquedaTest(unittest.TestCase):
    def _ids(self, texto):
        return {f.id for f in kb.FICHAS_FIJAS if kb.coincide(f, texto)}

    def test_busqueda_sin_acentos_y_varias_palabras(self):
        self.assertIn("imp-no-imprime", self._ids("impresora"))
        self.assertIn("red-sin-internet", self._ids("INTERNET conecta"))
        self.assertIn("est-pantallazo", self._ids("pantallazo azul"))
        self.assertIn("red-sin-internet", self._ids("diagnostico capas"))  # sin tilde
        self.assertEqual(self._ids("palabraquenoexiste"), set())
        self.assertEqual(len(self._ids("   ")), len(kb.FICHAS_FIJAS))

    def test_duplicar_y_pasos_nuevos(self):
        base = kb.FICHAS_FIJAS[0]
        copia = kb.duplicar(base)
        self.assertTrue(copia.propia)
        self.assertNotEqual(copia.id, base.id)
        self.assertEqual(copia.origen, base.id)
        self.assertEqual([p.texto for p in copia.pasos], [p.texto for p in base.pasos])
        self.assertTrue(all(p.id.startswith(copia.id + ".") for p in copia.pasos))
        nuevo = kb.nuevo_paso(copia)
        self.assertNotIn(nuevo.id, {p.id for p in copia.pasos})
        # Duplicar una propia conserva el origen fijo
        self.assertEqual(kb.duplicar(copia).origen, base.id)


class BaseDatosTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.db = kb.ConocimientoDB(os.path.join(self.dir, "multisys.db"))

    def test_guardar_listar_editar_eliminar(self):
        f = kb.nueva_ficha("impresion")
        f.titulo = "Plotter de la oficina"
        f.sintomas = ["Sale en blanco"]
        f.pasos = [kb.Paso(f.id + ".1", "Revisar la cola", "🖨️ Impresoras")]
        self.db.guardar(f)
        lista = self.db.listar()
        self.assertEqual(len(lista), 1)
        self.assertEqual(lista[0].titulo, "Plotter de la oficina")
        self.assertEqual(lista[0].pasos[0].seccion, "🖨️ Impresoras")
        self.assertTrue(lista[0].propia)
        f.titulo = "Plotter (A0)"
        self.db.guardar(f)
        self.assertEqual([x.titulo for x in self.db.listar()], ["Plotter (A0)"])
        self.db.eliminar(f.id)
        self.assertEqual(self.db.listar(), [])

    def test_no_guarda_fijas_ni_sin_titulo(self):
        with self.assertRaises(ValueError):
            self.db.guardar(kb.FICHAS_FIJAS[0])
        with self.assertRaises(ValueError):
            self.db.guardar(kb.nueva_ficha())

    def test_fila_danada_no_rompe(self):
        with sqlite3.connect(self.db.path) as c:
            c.execute("INSERT INTO conocimiento_fichas VALUES ('u-x', '{no es json', 'a', 'a')")
        f = kb.nueva_ficha()
        f.titulo = "Buena"
        self.db.guardar(f)
        self.assertEqual([x.titulo for x in self.db.listar()], ["Buena"])


class VentanaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt5.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        from PyQt5.QtWidgets import QLabel, QTabWidget, QVBoxLayout, QWidget
        from msys.ui import conocimiento_dialog as cd

        errores = self.errores = []
        self._hook = sys.excepthook
        sys.excepthook = lambda t, v, tb: errores.append(f"{t.__name__}: {v}")

        class Host(QWidget):
            def __init__(self):
                super().__init__()
                self.tab_widgets, self._section_order = {}, []
                self.vistas, self.copias, self.ventanas = [], 0, []
                for nombre in SECCIONES:
                    w = QWidget()
                    if nombre == "🧪 Herramientas de red":
                        t = QTabWidget(w)
                        t.addTab(QLabel("d"), "🔬 Diagnóstico por capas")
                        t.addTab(QLabel("c"), "🔎 Consultas")
                        QVBoxLayout(w).addWidget(t)
                        self.hred = t
                    if nombre == "📂 Carpetas Compartidas":
                        # Como en MASys (shares.py): la sección ES la QTabWidget, con las pestañas planas
                        w = QTabWidget()
                        for t in ("📂 Recursos compartidos", "📁 Permisos de carpetas", "⚠️ Análisis de permisos",
                                  "🧭 Acceso de una cuenta", "💾 Copias de permisos"):
                            w.addTab(QLabel(t), t)
                        self.comp = w
                    if nombre == "🎨 Iconos":   # pestañas anidadas (otra QTabWidget dentro de una página)
                        externo = QTabWidget(w)
                        externo.addTab(QLabel("r"), "📂 Recursos compartidos")
                        interno_w = QWidget()
                        interno = QTabWidget(interno_w)
                        interno.addTab(QLabel("p"), "📁 Permisos de carpetas")
                        interno.addTab(QLabel("a"), "🧭 Acceso de una cuenta")
                        QVBoxLayout(interno_w).addWidget(interno)
                        externo.addTab(interno_w, "Permisos")
                        QVBoxLayout(w).addWidget(externo)
                        self.comp_ext, self.comp_int = externo, interno
                    self.tab_widgets[nombre] = w
                    self._section_order.append(nombre)

            def seleccionar_seccion(self, nombre):
                self.vistas.append(nombre)

            def abrir_copias_seguridad(self):
                self.copias += 1

            # 5.3.0: Móviles Android y Puertos desde fuera son ventanas (como Copias de Seguridad)
            def abrir_moviles(self):
                self.ventanas.append("moviles")
                return None

            def abrir_puertos_movil(self):
                self.ventanas.append("puertos")

            def _dash_colors(self):
                return {"title": "#1a2634", "muted": "#8a94a6", "alerts_bg": "#f7f9fc",
                        "alerts_border": "#e3e7ee", "alerts_text": "#2c3e50"}

        self.cd = cd
        self.host = Host()
        self.db = kb.ConocimientoDB(os.path.join(tempfile.mkdtemp(), "multisys.db"))
        self.dlg = cd.BaseConocimientoDialog(self.host, self.db)
        # Sin ventanas modales que se queden esperando
        self.dlg._confirmar = lambda *_a: True
        self.dlg._aviso = lambda *a: errores.append(f"aviso: {a[1]}")

    def tearDown(self):
        sys.excepthook = self._hook
        self.dlg.deleteLater()
        self.host.deleteLater()

    def _hojas(self):
        a = self.dlg.arbol
        return [a.topLevelItem(i).child(j) for i in range(a.topLevelItemCount())
                for j in range(a.topLevelItem(i).childCount())]

    def test_arbol_con_todas_las_fichas_y_busqueda(self):
        self.assertEqual(len(self._hojas()), len(kb.FICHAS_FIJAS))
        self.assertEqual(self.dlg.lbl_recuento.text(), f"{len(kb.FICHAS_FIJAS)} fichas")
        self.dlg.busqueda.setText("impresora")
        ids = {h.data(0, self.cd._ROL_FICHA) for h in self._hojas()}
        self.assertTrue({"imp-no-imprime", "imp-no-aparece"} <= ids)
        self.assertTrue(all(h.parent().isExpanded() for h in self._hojas()))
        self.assertLess(len(self._hojas()), len(kb.FICHAS_FIJAS))
        self.dlg.busqueda.setText("zzzz")
        self.assertEqual(self.dlg.lbl_recuento.text(), "Sin resultados")
        self.assertIs(self.dlg.panel.currentWidget(), self.dlg.pag_vacia)

    def test_barrido_todas_las_fichas_y_sus_botones(self):
        from PyQt5.QtWidgets import QPushButton
        for hoja in self._hojas():
            self.dlg._clic_arbol(hoja, 0)
            fid = hoja.data(0, self.cd._ROL_FICHA)
            self.assertEqual(self.dlg._actual, fid)
            ficha = kb.FICHAS_FIJAS[[f.id for f in kb.FICHAS_FIJAS].index(fid)]
            botones = [b for b in self.dlg.contenido_ficha.findChildren(QPushButton) if b.text() == "➜ Ir a" and b.isVisibleTo(self.dlg.contenido_ficha)]
            self.assertEqual(len(botones), sum(1 for p in ficha.pasos if p.seccion), fid)
            for b in botones:
                b.click()
        self.assertEqual(self.errores, [])
        self.assertTrue(self.host.vistas)
        self.assertGreater(self.host.copias, 0)
        self.assertIn("moviles", self.host.ventanas)
        self.assertIn("puertos", self.host.ventanas)

    def test_ir_a_pestana(self):
        # Sección que ES la QTabWidget
        self.dlg.ir_a(kb.Paso("x.0", "", "📂 Carpetas Compartidas", "🧭 Acceso de una cuenta"))
        self.assertEqual(self.host.vistas[-1], "📂 Carpetas Compartidas")
        self.assertEqual(self.host.comp.currentIndex(), 3)
        self.assertIn("💾 Copias de permisos", self.dlg._pestanas("📂 Carpetas Compartidas"))
        # Pestaña anidada: también se elige la pestaña de fuera
        p = kb.Paso("x.1", "", "🎨 Iconos", "🧭 Acceso de una cuenta")
        self.dlg.ir_a(p)
        self.assertEqual(self.host.vistas[-1], "🎨 Iconos")
        self.assertEqual(self.host.comp_ext.currentIndex(), 1)
        self.assertEqual(self.host.comp_int.tabText(self.host.comp_int.currentIndex()), "🧭 Acceso de una cuenta")
        self.dlg.ir_a(kb.Paso("x.2", "", "🧪 Herramientas de red", "🔎 Consultas"))
        self.assertEqual(self.host.hred.currentIndex(), 1)
        self.dlg.ir_a(kb.Paso("x.3", "", "No existe"))
        self.assertEqual(len(self.errores), 1)

    def test_editor_nueva_duplicar_editar_eliminar(self):
        # Nueva ficha con dos pasos, uno con sección y pestaña
        self.dlg.btn_nueva.click()
        self.assertIs(self.dlg.panel.currentWidget(), self.dlg.pag_editor)
        self.dlg.btn_guardar.click()                       # sin título: avisa y no guarda
        self.assertEqual(self.db.listar(), [])
        self.dlg.ed_titulo.setText("Escáner de red no escanea")
        self.dlg.ed_sintomas.setPlainText("No llega el correo\n\n  Sale error  ")
        fila = self.dlg._filas[0]
        fila.texto.setText("Probar la consulta")
        fila.seccion.setCurrentText("🧪 Herramientas de red")
        self.assertTrue(fila._con_pestanas)
        fila.pestana.setCurrentIndex(fila.pestana.findData("🔎 Consultas"))
        self.dlg.btn_anadir_paso.click()
        self.dlg._filas[1].texto.setText("Segundo")
        self.dlg._filas[1].btn_subir.click()               # reordena
        self.assertEqual(self.dlg._filas[0].texto.text(), "Segundo")
        self.dlg.btn_anadir_paso.click()                   # paso vacío: no se guarda
        self.dlg.btn_guardar.click()
        guardadas = self.db.listar()
        self.assertEqual(len(guardadas), 1)
        g = guardadas[0]
        self.assertEqual(g.sintomas, ["No llega el correo", "Sale error"])
        self.assertEqual([p.texto for p in g.pasos], ["Segundo", "Probar la consulta"])
        self.assertEqual((g.pasos[1].seccion, g.pasos[1].pestana), ("🧪 Herramientas de red", "🔎 Consultas"))
        self.assertIs(self.dlg.panel.currentWidget(), self.dlg.pag_ficha)
        self.assertEqual(self.dlg._actual, g.id)
        self.assertEqual(len(self._hojas()), len(kb.FICHAS_FIJAS) + 1)

        # Editar la propia y quitar un paso
        self.dlg.editar(g.id)
        self.dlg._filas[0].btn_quitar.click()
        self.dlg.ed_titulo.setText("Escáner de red")
        self.dlg.btn_guardar.click()
        g = self.db.listar()[0]
        self.assertEqual((g.titulo, len(g.pasos)), ("Escáner de red", 1))

        # Duplicar una fija → ficha propia nueva; la fija sigue intacta
        self.dlg.duplicar("imp-no-imprime")
        self.dlg.btn_guardar.click()
        self.assertEqual(len(self.db.listar()), 2)
        self.assertEqual(sum(1 for f in kb.FICHAS_FIJAS if f.id == "imp-no-imprime"), 1)

        # Cancelar un editor con cambios (confirmado) vuelve a la ficha
        self.dlg.editar(g.id)
        self.dlg.ed_titulo.setText("cambio que se descarta")
        self.dlg.btn_cancelar.click()
        self.assertIs(self.dlg.panel.currentWidget(), self.dlg.pag_ficha)
        self.assertEqual(self.db.listar()[0].titulo, "Escáner de red")

        # Eliminar
        self.dlg.eliminar(g.id)
        self.assertEqual(len(self.db.listar()), 1)
        self.assertEqual(self.errores, ["aviso: Escribe el título del problema."])

    def test_cerrar_con_editor_abierto_y_reabrir(self):
        self.dlg.show()
        self.dlg.btn_nueva.click()
        self.dlg.ed_titulo.setText("a medias")
        self.dlg._confirmar = lambda *_a: False            # «No» descartar: no se cierra
        self.dlg.reject()
        self.assertTrue(self.dlg.isVisible())
        self.dlg._confirmar = lambda *_a: True
        self.dlg.reject()
        self.assertFalse(self.dlg.isVisible())
        self.assertEqual(self.db.listar(), [])

    def test_mixin_ventana_unica(self):
        class H(self.cd.ConocimientoMixin, type(self.host)):
            pass
        # Nunca la multisys.db real del proyecto
        original = self.cd.kb.ConocimientoDB
        self.cd.kb.ConocimientoDB = lambda: self.db
        self.addCleanup(setattr, self.cd.kb, "ConocimientoDB", original)
        h = H()
        h.abrir_base_conocimiento()
        primera = h._dlg_conocimiento
        self.assertIs(primera.db, self.db)
        h.abrir_base_conocimiento()
        self.assertIs(h._dlg_conocimiento, primera)
        h._tema_base_conocimiento()
        primera.close()
        h.abrir_base_conocimiento()
        self.assertIs(h._dlg_conocimiento, primera)
        self.assertTrue(primera.isVisible())
        primera.hide()
        h.deleteLater()


if __name__ == "__main__":
    unittest.main()
