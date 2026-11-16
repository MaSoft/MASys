# -*- coding: utf-8 -*-
"""Pruebas de 4.1.9: Estabilidad, puntuación de salud y visitas (solo lectura: nada cambia el sistema).
python -m unittest discover -s tests -p "test_*.py" """
import os
import sys
import tempfile
import unittest
from datetime import datetime
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from msys.domain import estabilidad as est  # noqa: E402
from msys.domain import salud  # noqa: E402
from msys.domain import visitas  # noqa: E402


def _ev(log, eid, fecha, p, prov="", msg=""):
    return {"Log": log, "Id": eid, "Prov": prov, "Fecha": fecha, "P": p, "Msg": msg}


class EstabilidadTest(unittest.TestCase):
    def test_codigos_traducidos(self):
        self.assertEqual(est.codigo_de_texto("0x0000009f (0xffffd30d, 0x1)"), 0x9F)
        self.assertEqual(est.codigo_de_texto("159"), 0x9F)
        self.assertIsNone(est.codigo_de_texto(""))
        self.assertEqual(est.explicar_codigo(0x116)[0], "VIDEO_TDR_FAILURE")
        self.assertIn("poco habitual", est.explicar_codigo(0x1234)[1])
        self.assertEqual(est.texto_codigo(0xD1), "0xD1")
        self.assertIn(".NET", est.explicar_excepcion("0xe0434352"))
        self.assertEqual(est.explicar_excepcion("c0000005"), "Usó memoria que no debía")
        self.assertEqual(est.explicar_controlador("NVLDDMKM.SYS"), "Tarjeta gráfica NVIDIA")
        self.assertEqual(est.explicar_controlador("raro.sys"), "Controlador «raro.sys»")

    def test_eventos_y_duplicados(self):
        datos = {
            "Eventos": [
                # Pantallazo: 41 con código y 1001 con el volcado al mismo arranque -> uno solo
                _ev("System", 41, "2026-10-01T10:00:05", ["159", "3", "0", "0", "0"]),
                _ev("System", 1001, "2026-10-01T10:00:40", ["0x0000009f (0x3, ...)", r"C:\WINDOWS\Minidump\100126-1-01.dmp", "id"],
                    prov="Microsoft-Windows-WER-SystemErrorReporting"),
                # Apagado: 41 sin código y 6008 del mismo arranque -> uno solo
                _ev("System", 41, "2026-10-02T08:00:00", ["0", "0", "0", "0", "0"]),
                _ev("System", 6008, "2026-10-02T08:00:10", ["7:50", "02/10/2026"]),
                # 1001 de otro origen no cuenta
                _ev("System", 1001, "2026-10-02T09:00:00", ["x"], prov="Otra cosa"),
                _ev("Application", 1000, "2026-10-03T11:00:00",
                    ["app.exe", "1.0", "t", "Qt5Core.dll", "5.15", "t", "c0000409", "o", "p", "t", r"C:\App\app.exe", "m"]),
                _ev("Application", 1000, "2026-10-03T12:00:00",
                    ["app.exe", "1.0", "t", "unknown", "", "t", "c0000005", "o", "p", "t", r"C:\App\app.exe", "m"]),
                _ev("Application", 1002, "2026-10-03T13:00:00", ["otra.exe", "2.0", "1", "t", "t", r"C:\Otra\otra.exe"]),
                {"Log": "System", "Id": 41, "Fecha": "no es fecha", "P": []},
            ],
            "Volcados": {"Ruta": r"C:\WINDOWS\Minidump\100126-1-01.dmp", "Fecha": "2026-10-01T10:00:30", "Tam": 1000},
            "Indice": 6.5,
        }
        e = est.interpretar(datos, 30)
        r = e.resumen()
        self.assertEqual((r["pantallazos"], r["apagados"], r["fallos_apps"], r["apps"]), (1, 1, 3, 2))
        self.assertEqual(e.indice, 6.5)
        self.assertEqual(e.eventos[0].fecha, datetime(2026, 10, 3, 13, 0))  # lo más reciente primero
        p = e.pantallazos[0]
        self.assertEqual((p.codigo, p.nombre), (0x9F, "DRIVER_POWER_STATE_FAILURE"))
        self.assertTrue(p.volcado.endswith("100126-1-01.dmp"))
        app = e.apps[0]
        self.assertEqual((app.app, app.cierres, app.bloqueos, app.modulo), ("app.exe", 2, 0, "Qt5Core.dll"))
        self.assertEqual(est.interpretar(None).eventos, [])

    def test_volcado_se_asocia_por_fecha(self):
        datos = {"Eventos": [_ev("System", 41, "2026-10-01T10:00:00", ["278"])],
                 "Volcados": [{"Ruta": "a.dmp", "Fecha": "2026-09-01T10:00:00", "Tam": 1},
                              {"Ruta": "b.dmp", "Fecha": "2026-10-01T10:03:00", "Tam": 1}]}
        p = est.interpretar(datos).pantallazos[0]
        self.assertEqual((p.codigo, p.nombre, p.volcado), (0x116, "VIDEO_TDR_FAILURE", "b.dmp"))  # 278 = 0x116

    def test_analisis_kd(self):
        salida = """
BUGCHECK_CODE:  116
PROCESS_NAME:  System
MODULE_NAME: nvlddmkm
IMAGE_NAME:  nvlddmkm.sys
FAILURE_BUCKET_ID:  0x116_IMAGE_nvlddmkm.sys
Probably caused by : nvlddmkm.sys ( nvlddmkm+12345 )
"""
        c = est.interpretar_analisis(salida)
        self.assertEqual(c["CONTROLADOR"], "nvlddmkm.sys")
        self.assertEqual(c["QUE_ES"], "Tarjeta gráfica NVIDIA")
        self.assertEqual(c["BUGCHECK_CODE"], "116")
        self.assertEqual(est.interpretar_analisis("Probably caused by : ntoskrnl.exe ( nt!x )")["CONTROLADOR"], "ntoskrnl.exe")
        self.assertNotIn("CONTROLADOR", est.interpretar_analisis("nada"))

    def test_lectura_real_del_equipo(self):
        e = est.leer(7)  # solo lectura del registro de eventos
        self.assertEqual(e.dias, 7)
        self.assertTrue(all(isinstance(x.fecha, datetime) for x in e.eventos))


class SaludTest(unittest.TestCase):
    def test_pesos_y_topes(self):
        m = salud.motivos_estabilidad({"pantallazos": 5, "apagados": 2, "fallos_apps": 25, "apps": 3}, 30)
        self.assertEqual([x.puntos for x in m], [30, 6, 6])
        self.assertIn("5 pantallazos azules", m[0].texto)
        self.assertEqual(salud.motivos_estabilidad({"pantallazos": 1, "apagados": 0, "fallos_apps": 4}, 7)[0].texto,
                         "🔴 1 pantallazo azul en 7 días")
        s = salud.motivos_soporte({"💽 Discos Duros": ["⚠️ a", "⚠️ b", "⚠️ c", "⚠️ d"]})
        self.assertEqual([x.puntos for x in s], [5, 5, 5, 0])  # tope de 15 por sección
        self.assertEqual(salud.motivo_espacio(8, "C:")[0].puntos, 15)
        self.assertEqual(salud.motivo_espacio(15, "C:")[0].puntos, 5)
        self.assertEqual(salud.motivo_espacio(40, "C:"), [])
        self.assertEqual(salud.motivo_espacio(None, "C:"), [])
        punt, orden = salud.puntuar(m + s)
        self.assertEqual(punt, 100 - 42 - 15)
        self.assertEqual(orden[0].puntos, 30)
        self.assertEqual(salud.puntuar([salud.Motivo("x", "s", 80), salud.Motivo("y", "s", 80)])[0], 0)
        self.assertEqual(salud.nivel(95)[1], "🟢")
        self.assertEqual(salud.nivel(50)[1], "🔴")


class _Modelo:
    def __init__(self, filas):
        self._f = filas

    def rows(self):
        return self._f


class VisitasTest(unittest.TestCase):
    def _host(self, programas, servicios=None):
        h = SimpleNamespace()
        h.software_model = _Modelo([SimpleNamespace(nombre=n, version=v) for n, v in programas])
        if servicios is not None:
            h.services_model = _Modelo([SimpleNamespace(name=n, startup_type=t) for n, t in servicios])
        return h

    def test_recoger_guardar_y_comparar(self):
        base = tempfile.mkdtemp()
        a = visitas.recoger(self._host([("7-Zip", "23.01"), ("Chrome", "128"), ("Viejo", "1")], [("Spooler", "Automático")]))
        a["ts"] = "2026-10-01T09:00:00"
        ruta, anterior = visitas.guardar(base, a)
        self.assertIsNotNone(ruta)
        self.assertIsNone(anterior)
        # Sin cambios: no guarda otra igual y devuelve la anterior
        ruta2, anterior2 = visitas.guardar(base, dict(a, ts="2026-10-02T09:00:00"))
        self.assertIsNone(ruta2)
        self.assertEqual(anterior2["ts"], a["ts"])
        # Servicios sin cargar en la visita nueva: no se dan por quitados
        b = visitas.recoger(self._host([("7-Zip", "24.08"), ("Chrome", "128"), ("Nuevo", "2")]))
        b["ts"] = "2026-10-03T09:00:00"
        ruta3, anterior3 = visitas.guardar(base, b)
        self.assertIsNotNone(ruta3)
        cambios = visitas.comparar(anterior3, b)
        tipos = {(c.tipo, c.elemento) for c in cambios}
        self.assertEqual(tipos, {(visitas.ANADIDO, "Nuevo"), (visitas.QUITADO, "Viejo"), (visitas.CAMBIADO, "7-Zip")})
        self.assertTrue(all(c.modulo == "Programas" for c in cambios))
        self.assertEqual(visitas.resumen(cambios), "Programas: +1 −1 1 cambiados")
        self.assertEqual(len(visitas.listar(base)), 2)
        self.assertEqual(visitas.fecha_legible(b), "03/10/2026 09:00")

    def test_compara_snapshots_antiguos(self):
        viejo = {"ts": "2026-01-01T00:00:00", "data": {"software": ["7-Zip|23.01", "Chrome|120"]}}
        nuevo = {"ts": "2026-02-01T00:00:00", "data": {"software": {"7-Zip": "23.01", "Chrome": "128"}}}
        c = visitas.comparar(viejo, nuevo)
        self.assertEqual([(x.tipo, x.elemento, x.antes, x.despues) for x in c], [(visitas.CAMBIADO, "Chrome", "120", "128")])

    def test_carpeta_por_equipo_y_tope(self):
        base = tempfile.mkdtemp()
        self.assertTrue(visitas.carpeta(base, "PC/ra:ro").endswith(os.path.join("Visitas", "PCraro")))
        for i in range(visitas.MAXIMO + 3):
            f = visitas.recoger(self._host([("App", str(i))]))
            f["ts"] = f"2026-10-01T10:{i // 60:02d}:{i % 60:02d}"
            visitas.guardar(base, f)
        self.assertEqual(len(visitas.listar(base)), visitas.MAXIMO)


class InformeHtmlTest(unittest.TestCase):
    def test_plantilla_comun(self):
        from msys.core import informe_html as ih
        secs = [ih.Seccion("Procesos", ["Nombre", "Estado"], [["<b>x</b>", "✅ OK"], ["y", "🔴 Crítico"], ["z", "Inactivo"]]),
                ih.Seccion("Notas", ["Nombre"], [[ih.Html('a <span class="nota-icon">📝</span>')]], abierta=False)]
        h = ih.documento("Informe", secs, kpis=[ih.Kpi(3, "Filas")], bloque_inicial="<p>R</p>")
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", h)              # texto escapado
        self.assertIn('<span class="nota-icon">📝</span>', h)   # HTML propio sin escapar
        self.assertIn('data-level="crit"', h)
        self.assertIn('class="chip" data-level="crit"', h)     # filtro por nivel porque hay niveles
        self.assertIn('id="s1" class="datos plegada"', h)
        self.assertIn('<nav>', h)                               # índice con varias secciones
        self.assertNotIn("<nav>", ih.documento("I", secs[:1]))
        self.assertEqual(ih.nivel_celda("Inactivo"), "")
        self.assertEqual(ih.nivel_celda("⚠️ revisar"), "warn")
        self.assertEqual(ih.nivel_celda("Activo"), "ok")
        control = {chr(c) for c in range(32)} - {"\t", "\n", "\r"}
        self.assertFalse(control & set(h))  # ningún carácter de control en el informe


if __name__ == "__main__":
    unittest.main()
