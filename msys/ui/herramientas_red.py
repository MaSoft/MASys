# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: sección 🧪 Herramientas de red (categoría Red).

Pestañas:
- 🔬 Diagnóstico por capas: el motor de MAOSI (msys/osi, copia propia):
  contexto VPN/proxy, interfaces, puerta de enlace y ARP, ping y traceroute,
  puertos, TLS, DNS en varios servidores, HTTP y Microsoft 365. Investigar un
  puerto que falla e informe HTML técnico o para el cliente.
- 🔎 Consultas: DNS, WHOIS (RDAP), cabeceras web, certificado y mi IP
  (idea de yougetsignal.com/tools; motor msys/domain/consultas_red.py).
El «🚪 Puerto desde fuera» (comprobado por el móvil con datos móviles) está en la
sección 📱 Móvil Android, que reutiliza leer_puertos() de aquí."""

import os
import re
import time
from datetime import datetime

from PyQt5.QtWidgets import QCheckBox, QLabel, QLineEdit, QMessageBox, QVBoxLayout, QWidget

from msys.core import tareas
from msys.core.utils import dialogo_guardar_archivo, get_informes_folder
from msys.domain import consultas_red as cr
from msys.models.tabla_simple import COLOR_AVISO, COLOR_ERROR, COLOR_INFO, COLOR_OK, TablaSimple, objeto_seleccionado
from msys.osi import investigacion_puerto, motor
from msys.osi.informe import generar_html
from msys.osi.models import AVISO, ERROR, INFO, OK
from msys.ui import piezas

SECCION = "🧪 Herramientas de red"
_COLORES = {OK: COLOR_OK, AVISO: COLOR_AVISO, ERROR: COLOR_ERROR, INFO: COLOR_INFO}
PUERTOS_DEFECTO = "80 HTTP, 443 HTTPS, 22 SSH, 3389 RDP, 445 SMB, 25 SMTP"


def leer_puertos(texto: str):
    """'80 HTTP, 443, 8080 Panel' -> [(80, 'HTTP'), (443, ''), (8080, 'Panel')]."""
    out = []
    for parte in texto.split(","):
        parte = parte.strip()
        if not parte:
            continue
        m = re.match(r"^(\d{1,5})\s*(.*)$", parte)
        if not m or not 0 < int(m.group(1)) < 65536:
            raise ValueError(f"«{parte}» no es un puerto válido (número de 1 a 65535 y, si quieres, un nombre)")
        out.append((int(m.group(1)), m.group(2).strip()[:30]))
    if not out:
        raise ValueError("Escribe al menos un puerto")
    return out


class DiagnosticoWidget(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self.cargado = True
        self.resultados = []
        self.destino = ""
        self.fecha = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(5, 5, 5, 5)
        self.txt_destino = QLineEdit()
        self.txt_destino.setPlaceholderText("Dominio o IP: masoft.es, 192.168.1.10…")
        self.txt_destino.returnPressed.connect(self.diagnosticar)
        self.chk_m365 = QCheckBox("Microsoft 365")
        self.chk_m365.setChecked(True)
        self.txt_puertos = QLineEdit(PUERTOS_DEFECTO)
        self.txt_puertos.setToolTip("Puertos separados por comas, con nombre opcional: 80 HTTP, 8443 Panel")
        self.btn_diag = piezas.boton("▶️ Diagnosticar", "primary", self.diagnosticar)
        self.lbl_estado = QLabel("")
        self.lbl_estado.setObjectName("countLabel")
        fila1 = piezas.fila(QLabel("Destino"), self.txt_destino, self.chk_m365, QLabel("Puertos"), self.txt_puertos,
                            self.btn_diag, estirar=False)
        fila1.setStretch(1, 2)
        fila1.setStretch(4, 3)
        lay.addLayout(fila1)
        self.filtro = piezas.combo(["Todas", "Con problemas", "Correctas"])
        self.filtro.currentIndexChanged.connect(lambda _i: self._pintar())
        self.btn_inv = piezas.boton("🔍 Investigar puerto", "ghost", self._investigar,
                                    "Repite pruebas alrededor de un puerto que falla para decir por qué")
        self.btn_tec = piezas.boton("🌐 Informe técnico…", "ghost", lambda: self._informe("tecnico"))
        self.btn_cli = piezas.boton("👤 Informe para el cliente…", "ghost", lambda: self._informe("cliente"),
                                    "Veredicto y hallazgos en lenguaje llano, sin la parte técnica que esté bien")
        lay.addLayout(piezas.fila(self.filtro, self.btn_inv, self.btn_tec, self.btn_cli, None, self.lbl_estado, estirar=False))
        self.modelo = TablaSimple(["Capa", "Prueba", "Estado", "Tiempo", "Detalle"],
                                  color=lambda r, c: _COLORES.get(r.resultado) if c == 2 else None)
        self.tabla, self.proxy = piezas.tabla(self.modelo, self, columna_ancha=4)
        self.tabla.doubleClicked.connect(lambda _i: self._investigar())
        lay.addWidget(self.tabla, 1)
        self._botones(False)

    def _botones(self, hay_datos: bool, ocupado: bool = False):
        self.btn_diag.setEnabled(not ocupado)
        for b in (self.btn_inv, self.btn_tec, self.btn_cli):
            b.setEnabled(hay_datos and not ocupado)

    def actualizar(self):
        if self.destino:
            self.diagnosticar()

    def diagnosticar(self):
        try:
            destino = cr.limpiar_destino(self.txt_destino.text())
            puertos = leer_puertos(self.txt_puertos.text())
        except ValueError as e:
            self.host._avisar(SECCION, str(e), QMessageBox.Warning)
            return
        self.destino, self.fecha = destino, datetime.now().isoformat(timespec="seconds")
        self._botones(False, ocupado=True)
        self.lbl_estado.setText("⏳ Empezando…")
        self.host.set_status(f"🔬 Diagnosticando {destino}...")
        t = tareas.Tarea(lambda: motor.ejecutar_diagnostico(
            destino, incluir_m365=self.chk_m365.isChecked(), puertos=puertos,
            on_progreso=lambda texto: t.progreso.emit(0, texto)))
        t.progreso.connect(lambda _p, texto: self.lbl_estado.setText("⏳ " + texto))
        t.ok.connect(self._datos)
        t.error.connect(lambda m: (self._botones(bool(self.resultados)),
                                   self.host._avisar(SECCION, f"El diagnóstico falló:\n\n{m}", QMessageBox.Warning)))
        self._tarea = t
        t.start()

    def _datos(self, resultados):
        self.resultados = resultados
        self._pintar()
        self._botones(True)
        n = {k: sum(1 for r in resultados if r.resultado == k) for k in (OK, AVISO, ERROR)}
        def pl(k, uno, varios):
            return f"{n[k]} {uno if n[k] == 1 else varios}"
        self.lbl_estado.setText(f"✅ {pl(OK, 'correcta', 'correctas')} · ⚠️ {pl(AVISO, 'aviso', 'avisos')} · "
                                f"❌ {pl(ERROR, 'error', 'errores')}")
        self.host.set_status(f"✅ Diagnóstico de {self.destino} terminado")
        piezas.refrescar_export(self.host)

    def _pintar(self):
        f = self.filtro.currentIndex()
        lista = [r for r in self.resultados if f == 0 or (f == 1) == (r.resultado in (AVISO, ERROR))]
        filas = []
        for r in lista:
            detalle = r.detalle
            inv = (r.extra or {}).get("investigacion")
            if inv:
                detalle += f"  🔍 {inv.get('conclusion', '')}"
            filas.append([r.capa_nombre, r.prueba, r.resultado, f"{r.valor_ms:.0f} ms" if r.valor_ms is not None else "", detalle])
        self.modelo.set_datos(filas, lista)

    def _investigar(self):
        r = objeto_seleccionado(self.tabla, self.proxy, self.modelo)
        puerto, nombre = investigacion_puerto.extraer_puerto(r.prueba) if r else (None, "")
        if r is None or puerto is None or r.resultado == OK:
            self.host._avisar(SECCION, "Elige en la tabla un puerto que haya fallado (fila «Puerto …» con aviso o error).")
            return
        self._botones(True, ocupado=True)
        t = tareas.Tarea(lambda: investigacion_puerto.investigar(self.destino, puerto, nombre,
                                                                  on_progreso=lambda texto: t.progreso.emit(0, texto)))
        t.progreso.connect(lambda _p, texto: self.lbl_estado.setText("🔍 " + texto))
        t.ok.connect(lambda inv: self._investigado(r, inv))
        t.error.connect(lambda m: (self._botones(True), self.host._avisar(SECCION, m, QMessageBox.Warning)))
        self._tarea_inv = t
        t.start()

    def _investigado(self, r, inv):
        r.extra = dict(r.extra or {}, investigacion=inv)
        self._botones(True)
        self._pintar()
        self.lbl_estado.setText("🔍 Investigación terminada")
        pruebas = "\n".join(f"• {p['prueba']}: {p['resultado']} — {p['detalle']}" for p in inv.get("pruebas", []))
        self.host._avisar(f"Puerto {inv.get('puerto')}", f"{inv.get('conclusion', '')}\n\nPruebas hechas:\n{pruebas}")

    def _informe(self, modo):
        if not self.resultados:
            return
        nombre = f"Diagnostico_{self.destino}_{'cliente' if modo == 'cliente' else 'tecnico'}_{time.strftime('%Y%m%d_%H%M%S')}.html"
        ruta = dialogo_guardar_archivo(self, "Guardar informe", os.path.join(get_informes_folder(), nombre), "HTML (*.html)")
        if not ruta:
            return
        try:
            with open(ruta, "w", encoding="utf-8") as f:
                f.write(generar_html(self.destino, self.fecha, self.resultados, modo=modo))
        except OSError as e:
            self.host._avisar(SECCION, f"No se pudo guardar:\n\n{e}", QMessageBox.Warning)
            return
        if self.host._confirmar_accion("Informe", f"Informe guardado en:\n\n{ruta}\n\n¿Abrirlo?"):
            os.startfile(ruta)

    def filas_export(self):
        return (f"Diagnóstico de {self.destino}", self.modelo.HEADERS, self.modelo.filas())


class ConsultasWidget(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self.cargado = True
        lay = QVBoxLayout(self)
        lay.setContentsMargins(5, 5, 5, 5)
        self.herr = piezas.combo([h for h, _d in cr.HERRAMIENTAS])
        self.herr.currentIndexChanged.connect(self._cambio_herramienta)
        self.txt = QLineEdit()
        self.txt.returnPressed.connect(self.consultar)
        self.tipo = piezas.combo(cr.TIPOS_DNS)
        self.btn = piezas.boton("🔎 Consultar", "primary", self.consultar)
        fila = piezas.fila(self.herr, self.txt, self.tipo, self.btn, estirar=False)
        fila.setStretch(1, 1)
        lay.addLayout(fila)
        self.desc = piezas.nota("")
        lay.addWidget(self.desc)
        self.modelo = TablaSimple(["Dato", "Valor"], color=lambda f, c: _COLORES.get(f[2]) if c == 1 and f[2] != INFO else None)
        self.tabla, self.proxy = piezas.tabla(self.modelo, self, columna_ancha=1)
        lay.addWidget(self.tabla, 1)
        self.titulo = ""
        self._cambio_herramienta(0)

    def _cambio_herramienta(self, i):
        nombre, desc = cr.HERRAMIENTAS[i]
        self.desc.setText(desc)
        es_dns = nombre.startswith("DNS")
        self.tipo.setVisible(es_dns)
        self.txt.setEnabled(nombre != "Mi IP pública")
        self.txt.setPlaceholderText({"Certificado TLS": "Dominio o dominio:puerto (masoft.es, correo.ejemplo.com:993)",
                                     "Web: cabeceras y redirecciones": "Dirección web (masoft.es o https://…)",
                                     "WHOIS (RDAP)": "Dominio o IP"}.get(nombre, "Dominio o IP"))

    def actualizar(self):
        pass

    def consultar(self):
        herramienta = self.herr.currentText()
        texto = self.txt.text().strip()
        if herramienta != "Mi IP pública" and not texto:
            self.host._avisar(SECCION, "Escribe un dominio o una IP")
            return
        self.btn.setEnabled(False)
        self.host.set_status(f"🔎 {herramienta}…")
        self.titulo = f"{herramienta}: {texto}" if texto else herramienta
        tareas.lanzar(self, "_tarea", cr.consultar, herramienta, texto, self.tipo.currentText(),
                      al_terminar=self._datos,
                      al_fallar=lambda m: (self.btn.setEnabled(True), self.host._avisar(SECCION, m, QMessageBox.Warning)))

    def _datos(self, filas):
        self.btn.setEnabled(True)
        self.modelo.set_datos([[c, v] for c, v, _e in filas], filas)
        self.host.set_status(f"✅ {self.titulo}")
        piezas.refrescar_export(self.host)

    def filas_export(self):
        return (self.titulo or "Consulta", self.modelo.HEADERS, self.modelo.filas())


class HerramientasRedMixin:
    def crear_tab_herramientas_red(self):
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setSpacing(8)
        lay.setContentsMargins(10, 10, 10, 10)
        self.hred_tabs = piezas.pestanas()
        self.hred_diag = DiagnosticoWidget(self)
        self.hred_consultas = ConsultasWidget(self)
        self.hred_tabs.addTab(self.hred_diag, "🔬 Diagnóstico por capas")
        self.hred_tabs.addTab(self.hred_consultas, "🔎 Consultas")
        self.hred_tabs.currentChanged.connect(lambda _i: self._actualizar_barra_exportacion())
        lay.addWidget(self.hred_tabs, 1)
        self.registrar_export(SECCION, self._hred_export)
        return tab

    def _hred_export(self):
        w = self.hred_tabs.currentWidget() if hasattr(self, "hred_tabs") else None
        return [w.filas_export()] if w is not None and hasattr(w, "filas_export") else []
