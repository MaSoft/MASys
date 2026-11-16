# -*- coding: utf-8 -*-
"""Captura del Dashboard (resumen por módulo, 5 a la vista con ▲ / ▼) con datos de ejemplo, sin arrancar MASys
entero ni sus recolectores. Uso: python tests/captura_dashboard.py [carpeta] [--dark]"""
import os
import sys
import tempfile
from types import SimpleNamespace as N

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RAIZ)
args = [a for a in sys.argv[1:] if not a.startswith("--")]
SALIDA = os.path.abspath(args[0]) if args else os.path.join(tempfile.gettempdir(), "msys_dashboard")
OSCURO = "--dark" in sys.argv
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

from PyQt5.QtGui import QFont  # noqa: E402
from PyQt5.QtWidgets import QApplication, QMainWindow  # noqa: E402

app = QApplication(sys.argv)
app.setStyle("Fusion")
app.setFont(QFont("Segoe UI", 9))
from msys.widgets.delegates import DARK_STYLESHEET, LIGHT_STYLESHEET  # noqa: E402

app.setStyleSheet(DARK_STYLESHEET if OSCURO else LIGHT_STYLESHEET)

from msys.ui.dashboard import DashboardMixin  # noqa: E402
from msys.ui.visitas import VisitasMixin  # noqa: E402


class _M:
    def __init__(self, filas):
        self._f = filas

    def rows(self):
        return self._f


class Host(DashboardMixin, VisitasMixin, QMainWindow):
    current_theme = "dark" if OSCURO else "light"

    def __init__(self):
        super().__init__()
        self.abiertas = []
        self.processes_model = _M([N(memory_mb=120.0, exe_path="C:\\Windows\\x.exe", name="chrome.exe", username="tecnico")] * 150 +
                                  [N(memory_mb=45.0, exe_path="C:\\Windows\\y.exe", name="svchost.exe", username="SYSTEM")] * 30)
        self.services_model = _M([N(status="running", startup_type="automatic")] * 140 + [N(status="stopped", startup_type="manual")] * 90)
        self.software_model = _M([N(nombre=f"Programa {i}", editor="Microsoft Corporation" if i % 3 else "Adobe") for i in range(96)])
        self.users_model = _M([N(estado="Activo", grupos="Administradores")] * 3 + [N(estado="Inactivo", grupos="Usuarios")])
        self.pending_updates_model = _M([N(criticidad="Crítica"), N(criticidad="Moderada")])
        self.drivers_model = _M([N(firmado="Sí")] * 200 + [N(firmado="No")] * 2)
        self.partitions_model = _M([N(letra="C:", etiqueta="", libre="22 GB", porcentaje="91,5%"), N(letra="D:", etiqueta="Datos", libre="400 GB", porcentaje="40%")])
        self.disk_inventory_model = _M([N(modelo="Samsung SSD 980", estado_salud="Healthy")])
        self.certs_model = _M([N(estado="Valido")] * 30 + [N(estado="Caducado")] * 4)
        self.wifi_model = _M([N(es_seguridad_debil=False, nombre="Casa", conectado="Sí", autenticacion="WPA2")] + [N(es_seguridad_debil=False, nombre="Bar", conectado="No", autenticacion="Abierta")] * 5)
        self.eventos_model = _M([N(nivel="Error")] * 14 + [N(nivel="Advertencia")] * 30)
        self.printers_model = _M([N(nombre="HP LaserJet", predeterminada="Sí")])
        self._carac_lista = [N(estado="Activada", aviso="", nombre="Hyper-V")] * 20 + [N(estado="Activada", aviso="x", nombre="SMB 1.0")] + \
            [N(estado="Desactivada", aviso="", nombre="z")] * 120
        from msys.domain.estabilidad import Estabilidad
        self._estado_estabilidad = Estabilidad(dias=30)
        self._estado_estabilidad.indice = 6.4

    def _dash_colors(self):
        if self.current_theme == "dark":
            return {'title': '#e8ecf1', 'muted': '#8a94a6', 'tile_title': '#e8ecf1', 'tile_subtitle': '#aab4c4',
                    'neutral_accent': '#93a5c2', 'gauge_track': '#3a4258', 'gauge_text': '#e8ecf1',
                    'alerts_bg': '#262837', 'alerts_border': '#3a3d4a', 'alerts_item_border': '#333a4a', 'alerts_text': '#d3d9e3'}
        return {'title': '#1a2634', 'muted': '#8a94a6', 'tile_title': '#1a2634', 'tile_subtitle': '#5d6b7a',
                'neutral_accent': '#455a64', 'gauge_track': '#e9edf3', 'gauge_text': '#1a2634',
                'alerts_bg': '#f7f9fc', 'alerts_border': '#e3e7ee', 'alerts_item_border': '#edf0f4', 'alerts_text': '#2c3e50'}

    def seleccionar_seccion(self, s):
        self.abiertas.append(s)

    def tray_notify(self, *a, **k):
        pass

    def set_status(self, t):
        pass

    def _restyle_dashboard_static_widgets(self):
        pass

    def _tick_rendimiento(self):
        pass


os.makedirs(SALIDA, exist_ok=True)
w = Host()
w.setCentralWidget(w.crear_tab_dashboard())
w.show()
w._refresh_dashboard()
# Área real de la sección: la ventana mínima es 1600x900 (sin el árbol ni la barra inferior, ~1300x750)
for ancho, alto in ((1300, 750), (1640, 940)):
    w.resize(ancho, alto)
    app.processEvents()
    if w.height() > alto + 2:
        print(f"NO CABE a {ancho}x{alto}: necesita {w.height()} px")
    w.grab().save(os.path.join(SALIDA, f"dashboard_{ancho}x{alto}.png"))
w.resize(1300, 750)
w._dash_modulos.btn_bajar.click()
app.processEvents()
w.grab().save(os.path.join(SALIDA, "dashboard_pagina2.png"))
print("módulos:", [m.titulo for m in w._dash_modulos.modulos()])
print("capturas en", SALIDA)
