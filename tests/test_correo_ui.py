# -*- coding: utf-8 -*-
"""📧 Cuentas Correo (4.9.0): las pestañas nuevas con datos falsos, sin red, sin Outlook y con una multisys.db temporal.

- Barrido: se pulsan TODOS los botones de cada pestaña; falla si se abre el explorador nativo de Windows
  (QFileDialog estático) o si algo escribe en la multisys.db real.
- Flujos: detectar y probar (con proveedor encontrado abre la configuración), «Probar toda la lista» sin confirmar no manda
  nada, comprobar una dirección (historial) y una lista, dominio + informe HTML, guardar lo marcado de Outlook, exportación
  de la barra inferior por pestaña, y el visor con un PST público si está MASYS_PST_TESTDATA."""
import os
import sys
import tempfile
import time
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

from PyQt5.QtCore import QThread  # noqa: E402
from PyQt5.QtWidgets import (QApplication, QDialog, QFileDialog, QMenu, QMessageBox, QProgressBar, QPushButton,
                             QWidget)  # noqa: E402

APP = QApplication.instance() or QApplication(sys.argv)

from msys.domain.correo import direcciones as dirs  # noqa: E402
from msys.domain.correo import dominio as dom  # noqa: E402
from msys.domain.correo import outlook_cfg as olk  # noqa: E402
from msys.domain.correo import servidores as srv  # noqa: E402
from msys.domain.correo.ajustes import CorreoDB  # noqa: E402
from msys.ui import correo as uc  # noqa: E402
from msys.ui import correo_outlook as uo  # noqa: E402
from msys.ui.email_accounts import EmailMixin  # noqa: E402

PST_DIR = os.environ.get("MASYS_PST_TESTDATA") or os.environ.get("MACORREO_PST_TESTDATA", "")
REAL_DB = os.path.join(RAIZ, "multisys.db")


def _tablas_reales():
    import sqlite3
    if not os.path.exists(REAL_DB):
        return set()
    with sqlite3.connect(REAL_DB) as c:
        return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


class Host(QWidget, EmailMixin, uc.CorreoMixin):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.progress_bar = QProgressBar()
        self._proveedores_export = {}
        self.avisos, self.confirmaciones, self.respuesta = [], [], False

    def set_status(self, t):
        self.estado = t

    def _avisar(self, titulo, texto, icono=QMessageBox.Information):
        self.avisos.append(f"{titulo}: {texto}")

    def _confirmar_accion(self, titulo, texto, icono=QMessageBox.Question):
        self.confirmaciones.append(titulo)
        return self.respuesta

    def registrar_export(self, nombre, proveedor):
        self._proveedores_export[nombre] = proveedor

    def _actualizar_barra_exportacion(self):
        pass


def esperar(host, segundos=20):
    fin = time.time() + segundos
    while time.time() < fin:
        APP.processEvents()
        vivos = [v for w in (host.correo_probar, host.correo_direcciones, host.correo_dominio, host.correo_visor)
                 for v in vars(w).values() if isinstance(v, QThread) and v.isRunning()]
        if not vivos:
            break
        time.sleep(0.02)
    for _ in range(5):
        APP.processEvents()


class CorreoUITest(unittest.TestCase):
    def setUp(self):
        self.tablas_antes = _tablas_reales()
        self.dir = tempfile.mkdtemp()
        self.db = CorreoDB(os.path.join(self.dir, "multisys.db"))
        self.parches = []

        def parche(obj, nombre, valor):
            self.parches.append((obj, nombre, getattr(obj, nombre)))
            setattr(obj, nombre, valor)
        self.parche = parche
        parche(uc, "CorreoDB", lambda: self.db)
        # Explorador nativo = fallo; los diálogos propios responden lo que diga self.ruta_dialogo
        nativo = staticmethod(lambda *a, **k: self.fail("Se abrió el explorador nativo de Windows"))
        for f in ("getOpenFileName", "getOpenFileNames", "getSaveFileName", "getExistingDirectory"):
            parche(QFileDialog, f, nativo)
        self.ruta_dialogo = ""
        for mod in (uc, uo):
            for f in ("dialogo_abrir_archivo", "dialogo_guardar_archivo", "dialogo_elegir_carpeta"):
                if hasattr(mod, f):
                    parche(mod, f, lambda *a, **k: self.ruta_dialogo)
        self.dialogos = []
        parche(QDialog, "exec_", lambda d: (self.dialogos.append(type(d).__name__), 0)[1])
        # Los menús (🕓 Historial) se cierran solos: un QMenu.exec_ dejaría la prueba esperando
        self.menus = []
        parche(QMenu, "exec_", lambda m, *a: (self.menus.append([x.text() for x in m.actions()]), None)[1])
        # Sin red ni Outlook
        self.pruebas_servidor = []

        def test_server(proto, host, port, ssl_directo, user="", password="", timeout=8):
            self.pruebas_servidor.append((proto, host))
            return srv.Result("OK", "falso", True)
        parche(srv, "test_server", test_server)
        parche(srv, "detect", lambda correo, isp=True: srv.Detection(correo.split("@")[1], ["Gmail"], {"Gmail": "por el dominio"}))
        parche(dirs, "domain_mail", lambda d, lifetime=6: dirs.DomainMail(True, True, ["mx.example.com"], ""))
        parche(dom, "run", lambda d, sel=None, progress=None, cancelled=lambda: False: dom.Report(d, "29/10/2026 10:00", [
            dom.Check("spf", "SPF (quién puede enviar)", "bad", "No tiene SPF", [], "Añade un TXT"),
            dom.Check("mx", "Servidores de correo (MX)", "ok", "1 servidor", ["MX 10 mx.example.com"])]))
        parche(olk, "profiles", lambda: [olk.Profile("Outlook", "16.0")])
        parche(olk, "versions", lambda: ["16.0"])
        parche(olk, "default_profile", lambda: "Outlook")
        parche(olk, "rules_keys", lambda v: [r"HKEY_CURRENT_USER\Software\Microsoft\Office\16.0\Outlook\Rules\Una"])
        parche(olk, "signatures", lambda: ["Corta", "Larga"])
        self.exportados = []

        def reg_export(clave, ruta):
            self.exportados.append(os.path.basename(ruta))
            open(ruta, "w").close()
            return True, ""
        parche(olk, "reg_export", reg_export)
        parche(olk, "zip_signatures", lambda nombres, ruta: (self.exportados.append(os.path.basename(ruta)), 1)[1])
        parche(olk, "running", lambda: False)
        parche(olk, "classic_available", lambda: False)
        parche(os, "startfile", lambda *a: None)
        self.host = Host()
        self.host.crear_tab_email_accounts()
        self.host.resize(1300, 720)
        self.host.correo_tabs.show()

    def tearDown(self):
        esperar(self.host)
        self.host.correo_visor.cerrar_archivo()
        self.host.deleteLater()
        for obj, nombre, valor in reversed(self.parches):
            setattr(obj, nombre, valor)
        self.assertEqual(self.tablas_antes, _tablas_reales(), "Se escribió en la multisys.db real")

    # ── barrido ─────────────────────────────────────────────────────────────
    def test_barrido_todos_los_botones(self):
        h = self.host
        h.correo_probar.correo.setText("ana@example.com")
        h.correo_direcciones.direccion.setText("ana@example.com")
        h.correo_direcciones.pegadas.setPlainText("ana@example.com, otra@example.net")
        h.correo_dominio.dominio.setText("example.com")
        h.correo_probar.servidor.setText("mail.example.com")
        pulsados = 0
        for i in range(1, h.correo_tabs.count()):
            h.correo_tabs.setCurrentIndex(i)
            pagina = h.correo_tabs.widget(i)
            if pagina is h.correo_direcciones:
                modos = (0, 1)
            else:
                modos = (None,)
            for modo in modos:
                if modo is not None:
                    pagina.modo.setCurrentIndex(modo)
                APP.processEvents()
                for b in pagina.findChildren(QPushButton):
                    if b.isVisible() and b.isEnabled():
                        b.click()
                        pulsados += 1
                        esperar(h)
        self.assertGreaterEqual(pulsados, 20)          # los botones visibles de las 5 pestañas nuevas
        # Con el proveedor encontrado se abre la configuración (y desde ella nada se manda a Outlook sin confirmar)
        self.assertIn("ConfiguracionDialog", self.dialogos)
        self.assertIn("AjustesCorreoDialog", self.dialogos)

    # ── flujos ──────────────────────────────────────────────────────────────
    def test_detectar_solo_su_proveedor_y_lista_sin_confirmar(self):
        p = self.host.correo_probar
        p.correo.setText("ana@gmail.com")
        p.clave.setText("x")
        p.detectar_y_probar()
        esperar(self.host)
        self.assertEqual({h for _p, h in self.pruebas_servidor}, {"imap.gmail.com", "pop.gmail.com", "smtp.gmail.com"})
        self.assertEqual(p._filas["Gmail"][1], "OK")
        self.pruebas_servidor.clear()
        self.host.respuesta = False
        p.probar_todos()
        esperar(self.host)
        self.assertEqual(self.pruebas_servidor, [], "Sin confirmar no se manda la contraseña a nadie")
        self.assertIn("Probar toda la lista", self.host.confirmaciones)
        self.assertEqual(p.filas_export()[0], uc.T_PROBAR)

    def test_direcciones_una_y_lista(self):
        d = self.host.correo_direcciones
        d.buzon.setChecked(False)
        d.direccion.setText("ana@example.com")
        d.comprobar_una()
        esperar(self.host)
        self.assertEqual(["ana@example.com"], [h["objetivo"] for h in self.db.historial("direccion")])
        self.assertIn("No se puede saber", d.detalle.toPlainText())
        d.modo.setCurrentIndex(1)
        d.pegadas.setPlainText("ana@example.com\nmal@-ex.com\nbea@example.org")    # dominio mal escrito: no válida
        d.comprobar_lista()
        esperar(self.host)
        self.assertEqual(3, len(d.resultados))
        self.assertEqual(dirs.INVALID, d.resultados[1].verdict)
        titulo, cab, filas = d.filas_export()
        self.assertEqual(3, len(filas))
        self.assertEqual(cab, d.COLS_LISTA)

    def test_dominio_e_informe(self):
        w = self.host.correo_dominio
        w.dominio.setText("https://Example.com/contacto")
        w.comprobar()
        esperar(self.host)
        self.assertEqual(2, w.modelo.rowCount())
        self.assertEqual("❌ Problema", w.modelo.filas()[0][1])        # lo que falla, primero
        self.assertIn("Qué hacer", w.detalle.toPlainText())
        self.assertEqual("example.com", self.db.historial("dominio")[0]["objetivo"])
        self.ruta_dialogo = os.path.join(self.dir, "informe.html")
        w.guardar_informe()
        with open(self.ruta_dialogo, encoding="utf-8") as f:
            self.assertIn("MASys · informe de correo", f.read())
        self.host.correo_tabs.setCurrentWidget(w)
        self.assertEqual("Correo del dominio example.com", self.host._correo_export()[0][0])

    def test_configuracion_outlook_lo_marcado(self):
        w = self.host.correo_outlook
        w.destino.setText(os.path.join(self.dir, "OutlookExport"))
        self.host.respuesta = True                      # «¿Crear la carpeta?» → Sí
        w.guardar()
        self.assertEqual(["Perfil_Outlook.reg", "Reglas_Outlook.reg", "Firmas.zip"], self.exportados)
        # Solo una firma: va a «Firmas_elegidas.zip» y sin el perfil
        self.exportados.clear()
        w._marcar(False)
        firmas = w.arbol.topLevelItem(1)
        from PyQt5.QtCore import Qt
        firmas.child(0).setCheckState(0, Qt.Checked)
        self.assertEqual([("zip", ["Corta"], "Firmas_elegidas.zip")], w.plan())

    def test_cuentas_sigue_con_su_tabla_de_siempre(self):
        self.host.correo_tabs.setCurrentIndex(0)
        self.assertEqual([], self.host._correo_export())

    @unittest.skipUnless(PST_DIR and os.path.isfile(os.path.join(PST_DIR, "enron.pst")), "sin PST de prueba")
    def test_visor_con_pst_publico(self):
        v = self.host.correo_visor
        v.abrir_ruta(os.path.join(PST_DIR, "enron.pst"))
        esperar(self.host, 60)
        esperar(self.host, 30)
        self.assertIsNotNone(v.pst)
        self.assertGreater(v.modelo.rowCount(), 0)
        v.tabla.selectRow(0)
        APP.processEvents()
        self.assertTrue(v.lbl_asunto.text())
        self.ruta_dialogo = os.path.join(self.dir, "c.eml")
        v.exportar_eml()
        self.assertTrue(os.path.getsize(self.ruta_dialogo) > 100)
        v.buscar.setText("power")
        v.buscar_en_archivo()
        esperar(self.host, 60)
        self.assertGreater(v.modelo.rowCount(), 0)


if __name__ == "__main__":
    unittest.main()
