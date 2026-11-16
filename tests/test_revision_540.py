# -*- coding: utf-8 -*-
"""5.4.0 — revisión completa: pruebas de los arreglos que se pueden comprobar sin cambiar el sistema.
python -m unittest discover -s tests -p "test_*.py" """
import os
import struct
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QSortFilterProxyModel, Qt  # noqa: E402
from PyQt5.QtGui import QStandardItem, QStandardItemModel  # noqa: E402
from PyQt5.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication(sys.argv)

from msys import ps_scripts  # noqa: E402
from msys import collectors  # noqa: E402
from msys.ui.reporting import ReportingMixin, _SECCION_DE_TITULO  # noqa: E402
from msys.ui.startup_programs import _ejecutable_de_comando  # noqa: E402
from msys.ui.credentials import _host_de_destino  # noqa: E402


class ExportacionTest(unittest.TestCase):
    """Antes las exportaciones usaban encabezados escritos a mano que ya no coincidían con la tabla."""

    def _proxy(self, cabeceras, filas, ocultar_desde=None):
        m = QStandardItemModel(0, len(cabeceras))
        m.setHorizontalHeaderLabels(cabeceras)
        for f in filas:
            m.appendRow([QStandardItem(x) for x in f])
        p = QSortFilterProxyModel()
        p.setSourceModel(m)
        if ocultar_desde:
            p.setFilterKeyColumn(0)
            p.setFilterFixedString(ocultar_desde)
        self._vivos = (m, p)
        return p

    def test_encabezados_y_columnas_salen_de_la_tabla(self):
        p = self._proxy(["Nombre", "Fabricante", "Estado", "Instance ID"], [["Ratón", "Logi", "Conectado", "USB\\1"]])
        headers, filas = ReportingMixin._filas_desde_proxy(p, ["Nombre", "DeviceID", "Fabricante", "Estado", "Clase"])
        self.assertEqual(headers, ["Nombre", "Fabricante", "Estado", "Instance ID"])
        self.assertEqual(filas, [["Ratón", "Logi", "Conectado", "USB\\1"]])

    def test_informe_completo_incluye_lo_filtrado(self):
        p = self._proxy(["Nombre"], [["Uno"], ["Dos"], ["Tres"]], ocultar_desde="Uno")
        self.assertEqual(len(ReportingMixin._filas_desde_proxy(p, ["Nombre"])[1]), 1)          # la vista
        self.assertEqual(len(ReportingMixin._filas_desde_proxy(p, ["Nombre"], todas=True)[1]), 3)

    def test_discos_tienen_categoria(self):
        self.assertEqual(_SECCION_DE_TITULO["💿 Discos"], "💽 Discos Duros")


class GuionesPowerShellTest(unittest.TestCase):
    def test_usuarios_cruza_grupos_por_sid(self):
        # Antes: ObjectClass -eq 'User' (en Windows en español es «Usuario»: nadie salía en ningún grupo)
        self.assertNotIn("$m.ObjectClass -eq 'User'", ps_scripts.PS_GET_USERS)
        self.assertIn("$m.SID.Value", ps_scripts.PS_GET_USERS)

    def test_licencias_una_consulta_filtrada(self):
        s = ps_scripts.PS_GET_LICENSE_INFO
        self.assertEqual(s.count("Get-CimInstance -ClassName SoftwareLicensingProduct"), 1)
        self.assertIn('-Filter "PartialProductKey IS NOT NULL"', s)

    def test_firewall_sin_tope_de_500(self):
        self.assertNotRegex(ps_scripts.PS_GET_FIREWALL_RULES, r"Select-Object -First 500(?!\d)")

    def test_tipo_de_adaptador_con_break(self):
        bloque = ps_scripts.PS_GET_NETWORK_ADAPTERS.split("switch -Wildcard ($desc)")[1].split("}\n")[0]
        self.assertIn("'VPN'; break", ps_scripts.PS_GET_NETWORK_ADAPTERS)
        self.assertIn("'Not Present' { 'No presente' }", ps_scripts.PS_GET_NETWORK_ADAPTERS)
        self.assertTrue(bloque)

    def test_usb_no_solo_concentradores(self):
        self.assertNotIn("$_.Class -eq 'USB' -and $_.Status -eq 'OK'", ps_scripts.PS_GET_USB_DEVICES)
        self.assertIn("USBSTOR", ps_scripts.PS_GET_USB_DEVICES)


class ProgramasDeInicioTest(unittest.TestCase):
    def test_ejecutable_de_comando(self):
        win = os.environ.get("SystemRoot", r"C:\Windows")
        self.assertEqual(_ejecutable_de_comando(win + r"\System32\notepad.exe /min").lower(),
                         (win + r"\System32\notepad.exe").lower())
        self.assertEqual(_ejecutable_de_comando('"C:\\Program Files\\X\\a.exe" -b'), "C:\\Program Files\\X\\a.exe")
        self.assertTrue(_ejecutable_de_comando(r"%SystemRoot%\System32\notepad.exe").lower().endswith("notepad.exe"))
        self.assertEqual(_ejecutable_de_comando(""), "")


class CredencialesTest(unittest.TestCase):
    def test_host_de_destino(self):
        self.assertEqual(_host_de_destino("\\\\SERVIDOR\\Datos"), "SERVIDOR")
        self.assertEqual(_host_de_destino("TERMSRV/192.168.1.10"), "192.168.1.10")
        self.assertEqual(_host_de_destino("git:https://github.com"), "")
        self.assertEqual(_host_de_destino("MicrosoftAccount user=x"), "")


class NetbiosTest(unittest.TestCase):
    """Antes solo valía el nombre <20>; un equipo que solo anuncia <00> se quedaba sin nombre."""

    def _respuesta(self, tid, nombres):
        cuerpo = bytes([len(nombres)])
        for nb, sufijo, grupo in nombres:
            cuerpo += nb.ljust(15).encode("latin-1") + bytes([sufijo]) + struct.pack(">H", 0x8000 if grupo else 0x0400)
        nombre = b"\x20" + b"CK" + b"AA" * 15 + b"\x00"
        return (tid + b"\x84\x00\x00\x00\x00\x01\x00\x00\x00\x00" + nombre + b"\x00\x21\x00\x01"
                + b"\x00\x00\x00\x00" + struct.pack(">H", len(cuerpo)) + cuerpo)

    def _consultar(self, nombres):
        prueba = self

        class Sock:
            def __init__(self, *a):
                self.tid = None

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def settimeout(self, t):
                pass

            def sendto(self, datos, destino):
                self.tid = datos[:2]

            def recvfrom(self, n):
                return prueba._respuesta(self.tid, nombres), ("1.2.3.4", 137)

        with mock.patch.object(collectors.socket, "socket", Sock):
            return collectors._nombre_netbios("1.2.3.4")

    def test_nombre_unico_00(self):
        self.assertEqual(self._consultar([("GRUPO", 0x00, True), ("DESKTOP-ABC", 0x00, False)]), "DESKTOP-ABC")

    def test_sin_nombre_unico(self):
        self.assertEqual(self._consultar([("GRUPO", 0x00, True)]), "")


class HallazgosTest(unittest.TestCase):
    """Hallazgos de seguridad: reglas deshabilitadas, servicios que se paran solos y estado del sistema."""

    def _v(self, **kw):
        v = SimpleNamespace(**kw)
        v._calcular = ReportingMixin._calcular_hallazgos_seguridad.__get__(v)
        return v

    def _modelo(self, filas):
        return SimpleNamespace(rows=lambda: filas)

    def test_puerto_sensible_solo_en_reglas_habilitadas(self):
        regla = SimpleNamespace(nombre="RDP", direccion="Entrada", accion="Permitir", puerto="3389", perfil="Any",
                                habilitada="No", es_regla_amplia=False)
        v = self._v(firewall_model=self._modelo([regla]))
        self.assertEqual([h for h in v._calcular() if h[1] == "Firewall"], [])
        regla.habilitada = "Sí"
        self.assertTrue([h for h in v._calcular() if h[1] == "Firewall"])

    def test_servicio_update_parado_no_es_hallazgo(self):
        filas = [SimpleNamespace(name="edgeupdate", display_name="Edge Update", startup_type="auto_delayed",
                                 status="stopped"),
                 SimpleNamespace(name="WinDefend", display_name="Defender", startup_type="auto", status="stopped")]
        v = self._v(services_model=self._modelo(filas))
        textos = [h[3] for h in v._calcular()]
        self.assertEqual(len(textos), 1)
        self.assertIn("WinDefend", textos[0])

    def test_defender_apagado_con_otro_antivirus_no_es_critico(self):
        sec = {"Defender": {"Habilitado": "No"}, "Antivirus": [{"Nombre": "ESET Security"}]}
        v = self._v(_last_seguridad_info=sec)
        av = [h for h in v._calcular() if h[1] == "Antivirus"]
        self.assertEqual([h[0] for h in av], ["ℹ️"])
        v = self._v(_last_seguridad_info={"Defender": {"Habilitado": "No"}, "Antivirus": []})
        self.assertEqual([h[0] for h in v._calcular() if h[1] == "Antivirus"], ["🔴"])

    def test_estado_del_sistema(self):
        sec = {"Firewall": {"Public": "INACTIVO", "Private": "Activo"},
               "Endurecimiento": {"UAC": "INACTIVO (riesgo)", "SMBv1": "HABILITADO (riesgo — vector de WannaCry)"},
               "ArchivoHosts": {"Sospechoso": True, "EntradasAnadidas": ["1.2.3.4 banco.es"]}}
        cats = {h[2] for h in self._v(_last_seguridad_info=sec)._calcular()}
        self.assertIn("Firewall de Windows desactivado en algún perfil", cats)
        self.assertIn("Control de cuentas de usuario (UAC) desactivado", cats)
        self.assertIn("SMBv1 habilitado (vector de WannaCry)", cats)

    def test_historial_ps_un_solo_hallazgo_y_sin_ruido(self):
        filas = [SimpleNamespace(comando="iex (New-Object Net.WebClient).DownloadString('x')"),
                 SimpleNamespace(comando="Invoke-WebRequest https://a"),
                 SimpleNamespace(comando="$__cmdletization_values = @(${Description}) bypass")]
        h = [x for x in self._v(ps_history_model=self._modelo(filas))._calcular() if x[1] == "Historial PS"]
        self.assertEqual(len(h), 1)
        self.assertTrue(h[0][2].startswith("2 comando"))

    def test_controlador_desconocido_no_cuenta_como_sin_firma(self):
        filas = [SimpleNamespace(firmado="Desconocido", estado="OK", dispositivo="X"),
                 SimpleNamespace(firmado="No", estado="OK", dispositivo="Y")]
        h = [x for x in self._v(drivers_model=self._modelo(filas))._calcular() if x[1] == "Controladores"]
        self.assertEqual(h[0][2], "1 controladores sin firma digital")


class ServiciosTest(unittest.TestCase):
    def test_retrasado_admitido(self):
        llamadas = []
        ws = collectors.win32service
        with mock.patch.object(ws, "OpenSCManager", return_value=1), \
                mock.patch.object(ws, "OpenService", return_value=2), \
                mock.patch.object(ws, "CloseServiceHandle"), \
                mock.patch.object(ws, "ChangeServiceConfig", side_effect=lambda *a: llamadas.append(("cfg", a[2]))), \
                mock.patch.object(ws, "ChangeServiceConfig2", side_effect=lambda h, n, v: llamadas.append(("retraso", v))):
            m = collectors.ServiceManager.__new__(collectors.ServiceManager)
            self.assertEqual(m.set_startup_type("X", "auto_delayed"), (True, "OK"))
            self.assertEqual(m.set_startup_type("X", "auto"), (True, "OK"))
        self.assertEqual(llamadas, [("cfg", ws.SERVICE_AUTO_START), ("retraso", True),
                                    ("cfg", ws.SERVICE_AUTO_START), ("retraso", False)])


class ContenidoTest(unittest.TestCase):
    """5.4.1: la búsqueda se rompía en cuanto había una subcarpeta (constante sin importar) y copiar dos
    archivos con el mismo nombre dejaba solo uno."""

    def test_busqueda_con_subcarpetas(self):
        import tempfile
        raiz = tempfile.mkdtemp()
        os.makedirs(os.path.join(raiz, "sub"))
        open(os.path.join(raiz, "sub", "x.txt"), "w").close()
        motor = collectors.ContentSearchEngine.__new__(collectors.ContentSearchEngine)
        motor._stop_requested = False
        motor._avisos = 0
        recorrido = list(motor._listar_seguro(raiz))
        self.assertTrue(any("sub" in r[1] for r in recorrido))     # antes: NameError al ver «sub»

    def test_copiar_dos_con_el_mismo_nombre(self):
        import tempfile
        base = tempfile.mkdtemp()
        filas = []
        for sub in ("a", "b"):
            os.makedirs(os.path.join(base, sub))
            ruta = os.path.join(base, sub, "repetido.txt")
            with open(ruta, "w") as f:
                f.write(sub)
            filas.append({"nombre": "repetido.txt", "ruta": ruta, "directorio": os.path.join(base, sub)})
        destino = os.path.join(base, "destino")
        os.makedirs(destino)
        hilo = collectors.ContentCopyMoveThread(filas, destino, "copiar", {})
        hilo.run()
        contenido = sorted(open(os.path.join(destino, x)).read() for x in os.listdir(destino))
        self.assertEqual(contenido, ["a", "b"])


class ImpresorasTest(unittest.TestCase):
    def test_no_usa_cmdlets_que_no_existen(self):
        import inspect
        src = inspect.getsource(collectors.PrinterManager)
        self.assertNotIn("Suspend-Printer -Name", src)
        self.assertNotIn("Resume-Printer -Name", src)
        self.assertIn("Win32_Printer", src)


class MensajesDeErrorTest(unittest.TestCase):
    def test_recorte(self):
        from msys.core.utils import _mensaje_error_powershell as m
        self.assertEqual(m("No se encuentra\r\nEn línea: 1 Carácter: 5\r\n+ x\r\n    + CategoryInfo : y"), "No se encuentra")
        self.assertEqual(m("Acceso denegado"), "Acceso denegado")


class Mejoras550Test(unittest.TestCase):
    def test_fabricantes_ieee_y_mac_privada(self):
        from msys.models.rows import identificar_fabricante_mac as f
        self.assertIn("REALTEK", f("00-E0-4C-00-00-01").upper())        # antes «Desconocido»
        self.assertIn("Hewlett", f("38:EA:A7:00:00:01"))
        self.assertEqual(f("EE-00-00-00-00-01"), "MAC privada (aleatoria)")
        self.assertEqual(f(""), "Desconocido")

    def test_metadatos_credenciales_sin_secreto(self):
        lista = [{"TargetName": "LegacyGeneric:target=Prueba", "LastWritten": __import__("datetime").datetime(2026, 1, 2, 3, 4),
                  "Persist": 2, "CredentialBlob": "SECRETO"}]
        import types
        falso = types.SimpleNamespace(CredEnumerate=lambda *a: lista)
        with mock.patch.dict(sys.modules, {"win32cred": falso}):
            d = collectors._metadatos_credenciales()
        self.assertEqual(d.get("prueba"), ("02/01/2026 03:04", "Este equipo"))
        self.assertNotIn("CredentialBlob", lista[0])                     # el secreto se descarta en el acto

    def test_copias_previas_enganchadas(self):
        import inspect
        from msys.ui import scheduled_tasks, firewall, certificates
        self.assertIn("Export-ScheduledTask", inspect.getsource(scheduled_tasks))
        self.assertIn("netsh advfirewall export", inspect.getsource(firewall))
        self.assertIn('"CERTIFICADOS"', inspect.getsource(certificates))


class HerramientasWinTest(unittest.TestCase):
    def test_todas_las_herramientas_y_guias_estan(self):
        from msys.domain import herramientas_win as hw
        for clave in hw.HERRAMIENTAS:
            self.assertTrue(os.path.exists(hw.ruta_script(clave)), clave)
            self.assertTrue(os.path.exists(hw.ruta_guia(clave)), clave)

    def test_valores_entre_comillas_sin_interpretar(self):
        from msys.domain import herramientas_win as hw
        orden = hw.orden_powershell(r"C:\x\a.ps1", [("-Url", r"https://a'; Remove-Item C:\ -Recurse #"),
                                                      ("-Dias", 3), ("-Idiomas", ["es-es", "en-us"]), "-SinConfirmar"])
        self.assertIn(r"-Url 'https://a''; Remove-Item C:\ -Recurse #'", orden)
        self.assertIn("-Dias 3", orden)
        self.assertIn("-Idiomas 'es-es','en-us'", orden)
        with self.assertRaises(ValueError):
            hw.orden_powershell("a.ps1", ["-Malo; calc"])

    def test_vigilancias_paran_con_la_senal(self):
        from msys.domain import herramientas_win as hw
        for clave in ("microcortes", "impresoras", "rendimiento"):
            with open(hw.ruta_script(clave), encoding="utf-8-sig") as f:
                self.assertIn("$env:MASYS_PARAR", f.read(), clave)

    def test_microparadas_veredicto(self):
        from msys.domain.microparadas_disco import Parada, Prueba
        p = Prueba(0, "aleatoria", 5.0, segundos=60, operaciones=1000, maxima_ms=300,
                   paradas=[Parada(1.0, 300.0, 10.0)])
        self.assertTrue(p.veredicto.startswith("🔴"))
        p.maxima_ms, p.paradas = 8, [Parada(1.0, 8.0, 1.0)]
        self.assertTrue(p.veredicto.startswith("✅"))


class M365Test(unittest.TestCase):
    def test_catalogo_completo(self):
        from msys.domain import m365
        c = m365.catalogo()
        self.assertEqual(len(c), 16)
        for h in c:
            self.assertTrue(h.guia and os.path.exists(h.guia), h.carpeta)
            self.assertNotEqual(h.resumen, h.carpeta, h.carpeta)              # todas tienen descripción
        self.assertTrue(any(h.cambia for h in c) and any(not h.cambia for h in c))

    def test_consola_y_ejemplo(self):
        from msys.domain import m365
        h = next(x for x in m365.catalogo() if x.carpeta == "M365-VerificarCuentas")
        self.assertTrue(m365.orden_ejemplo(h).startswith(f"& '{h.script}'"))
        lanzado = {}
        with mock.patch.object(m365.subprocess, "Popen", lambda args, **k: lanzado.update(a=args, k=k)),                 mock.patch.object(m365, "carpeta_trabajo", lambda _h: os.getcwd()):
            m365.abrir_consola(h)
        self.assertIn("-NoExit", lanzado["a"])
        self.assertIn("Get-Help -Name", lanzado["a"][-1])
        self.assertTrue(lanzado["k"]["creationflags"] & m365.subprocess.CREATE_NEW_CONSOLE)

    def test_sqlite_para_thunderbird(self):
        from msys import config as cfg
        self.assertTrue(os.path.exists(cfg.ruta_recurso("msys", "tools", "win", "sqlite3.exe")))


if __name__ == "__main__":
    unittest.main()
