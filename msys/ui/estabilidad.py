# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: sección 🩺 Estabilidad (categoría Sistema).

Arriba: periodo, Visor de eventos, Monitor de confiabilidad y Actualizar; debajo
cuatro cifras (pantallazos, apagados inesperados, fallos de apps e índice de
estabilidad de Windows).

Pestañas:
- 🕒 Línea de tiempo: todo junto, lo más reciente primero.
- 💥 Pantallazos azules: código traducido, causa habitual, volcado y, con
  «🔬 Analizar volcado» (kd.exe), el controlador implicado.
- 📉 Apps que fallan: cierres y bloqueos por app con el módulo que falla.

Motor sin Qt: msys/domain/estabilidad.py. Solo lectura: no cambia nada del equipo."""

import os
import subprocess

from PyQt5.QtWidgets import QFrame, QHBoxLayout, QLabel, QMessageBox, QVBoxLayout, QWidget

from msys.core import tareas
from msys.domain import estabilidad as dom
from msys.models.tabla_simple import COLOR_AVISO, COLOR_ERROR, COLOR_INFO, COLOR_OK, TablaSimple, objeto_seleccionado
from msys.ui import piezas

SECCION = "🩺 Estabilidad"
PERIODOS = [("Últimos 7 días", 7), ("Últimos 30 días", 30), ("Últimos 90 días", 90), ("Último año", 365)]


def _fecha(d) -> str:
    return d.strftime("%d/%m/%Y")


def _hora(d) -> str:
    return d.strftime("%H:%M")


def _color_tipo(e, col):
    if col == 2:
        return {dom.TIPO_PANTALLAZO: COLOR_ERROR, dom.TIPO_APAGADO: COLOR_AVISO}.get(e.tipo, COLOR_INFO)
    return None


class _Cifra(QFrame):
    """Tarjeta pequeña: título, número grande y explicación (mismo estilo que las fichas de Sistema)."""

    def __init__(self, titulo: str, colores: dict, acento: str):
        super().__init__()
        self.setObjectName("sysInfoCard")
        self.setStyleSheet(f"QFrame#sysInfoCard {{ background: {colores['alerts_bg']}; border: 1px solid {colores['alerts_border']}; "
                           f"border-top: 3px solid {acento}; border-radius: 8px; }} QFrame#sysInfoCard QLabel {{ background: transparent; border: none; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(2)
        t = QLabel(titulo)
        t.setObjectName("hintLabel")
        self.valor = QLabel("—")
        self.valor.setStyleSheet("font-size: 18pt; font-weight: 700;")
        self.nota = QLabel("")
        self.nota.setObjectName("hintLabel")
        for w in (t, self.valor, self.nota):
            lay.addWidget(w)

    def poner(self, valor: str, nota: str, color: str):
        self.valor.setText(valor)
        self.valor.setStyleSheet(f"font-size: 18pt; font-weight: 700; color: {color};")
        self.nota.setText(nota)


class EstabilidadMixin:
    def crear_tab_estabilidad(self):
        tab = piezas.PaginaPerezosa(lambda: self._estab_cargar())
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)
        self.estab_periodo = piezas.combo([p[0] for p in PERIODOS], [p[1] for p in PERIODOS])
        self.estab_periodo.setCurrentIndex(1)
        self.estab_periodo.currentIndexChanged.connect(lambda _i: self._estab_cargar())
        fila, self.estab_busqueda, _a, self.estab_recuento = piezas.barra_superior(
            "🔍 Buscar app, código o texto...",
            [self.estab_periodo,
             piezas.boton("📋 Visor de eventos", "ghost", lambda: self._estab_abrir("eventvwr.msc"),
                          "Abre el Visor de eventos de Windows"),
             piezas.boton("📈 Monitor de confiabilidad", "ghost", lambda: self._estab_abrir("perfmon.exe", "/rel"),
                          "El historial de estabilidad de Windows, día a día")],
            self._estab_cargar)
        layout.addLayout(fila)

        cifras = QHBoxLayout()
        cifras.setSpacing(8)
        c = self._dash_colors()
        self.estab_c_bsod = _Cifra("Pantallazos azules", c, COLOR_ERROR)
        self.estab_c_apagados = _Cifra("Apagados inesperados", c, COLOR_AVISO)
        self.estab_c_apps = _Cifra("Fallos de apps", c, COLOR_INFO)
        self.estab_c_indice = _Cifra("Índice de estabilidad de Windows", c, COLOR_OK)
        for c in (self.estab_c_bsod, self.estab_c_apagados, self.estab_c_apps, self.estab_c_indice):
            cifras.addWidget(c, 1)
        layout.addLayout(cifras)

        self.estab_tabs = piezas.pestanas()
        # Línea de tiempo
        self.estab_m_tiempo = TablaSimple(["Fecha", "Hora", "Tipo", "Qué pasó", "Detalle"], color=_color_tipo)
        t_tiempo, self.estab_p_tiempo = piezas.tabla(self.estab_m_tiempo, self, columna_ancha=4, busqueda=self.estab_busqueda)
        self.estab_tabs.addTab(self._estab_pagina(t_tiempo), "🕒 Línea de tiempo")
        # Pantallazos
        self.estab_m_bsod = TablaSimple(["Fecha", "Hora", "Código", "Nombre", "Qué significa", "Causa habitual", "Controlador", "Volcado"],
                                        color=lambda p, c: COLOR_ERROR if c == 2 else (COLOR_INFO if c == 6 and p.controlador else None),
                                        tooltip=lambda p, c: p.volcado if c == 7 and p.volcado else None)
        self.estab_t_bsod, self.estab_p_bsod = piezas.tabla(self.estab_m_bsod, self, columna_ancha=4, busqueda=self.estab_busqueda)
        pag = QWidget()
        lay = QVBoxLayout(pag)
        lay.setContentsMargins(5, 5, 5, 5)
        lay.addLayout(piezas.fila(
            piezas.boton("🔬 Analizar volcado", "ghost", self._estab_analizar,
                         "Busca el controlador que causó el pantallazo (necesita las Herramientas de depuración de Windows)"),
            piezas.boton("📂 Carpeta de volcados", "ghost", self._estab_carpeta, "Abre C:\\Windows\\Minidump")))
        lay.addWidget(self.estab_t_bsod, 1)
        lay.addWidget(piezas.nota("El código lo da Windows. El controlador concreto solo se sabe analizando el volcado: elige una fila "
                                  "con volcado y pulsa «🔬 Analizar volcado» (MASys debe estar abierto como administrador)."))
        self.estab_tabs.addTab(pag, "💥 Pantallazos azules")
        # Apps
        self.estab_m_apps = TablaSimple(["App", "Fallos", "Se cerró", "Dejó de responder", "Último", "Módulo que falla", "Qué significa", "Ruta"],
                                        color=lambda a, c: COLOR_ERROR if c == 1 and a.total >= 5 else None,
                                        tooltip=lambda a, c: a.ruta if c == 7 and a.ruta else None)
        t_apps, self.estab_p_apps = piezas.tabla(self.estab_m_apps, self, columna_ancha=7, busqueda=self.estab_busqueda, orden=(1, False))
        self.estab_tabs.addTab(self._estab_pagina(t_apps, "Si el módulo que falla no es de la propia app (por ejemplo una DLL de un "
                                                         "antivirus o de un complemento), la causa suele ser ese otro programa."),
                               "📉 Apps que fallan")
        self.estab_tabs.currentChanged.connect(lambda _i: self._actualizar_barra_exportacion())
        layout.addWidget(self.estab_tabs, 1)
        self._estab_datos = None
        self.registrar_export(SECCION, self._estab_export)
        return tab

    def _estab_pagina(self, tabla, texto_nota: str = ""):
        pag = QWidget()
        lay = QVBoxLayout(pag)
        lay.setContentsMargins(5, 5, 5, 5)
        lay.addWidget(tabla, 1)
        if texto_nota:
            lay.addWidget(piezas.nota(texto_nota))
        return pag

    def _estab_cargar(self):
        dias = self.estab_periodo.currentData() or 30
        self.set_status(f"🔄 Leyendo el registro de eventos de los últimos {dias} días...")
        tareas.lanzar(self, "_estab_tarea", dom.leer, dias, al_terminar=self._estab_pintar,
                      al_fallar=lambda m: self._avisar(SECCION, f"No se pudo leer la estabilidad:\n\n{m}", QMessageBox.Warning))

    def _estab_pintar(self, est):
        self._estab_datos = est
        self._estado_estabilidad = est  # lo usa la puntuación de salud del Dashboard
        r = est.resumen()
        self.estab_c_bsod.poner(str(r["pantallazos"]), f"en {est.dias} días", COLOR_ERROR if r["pantallazos"] else COLOR_OK)
        self.estab_c_apagados.poner(str(r["apagados"]), "sin apagarse bien", COLOR_AVISO if r["apagados"] else COLOR_OK)
        self.estab_c_apps.poner(str(r["fallos_apps"]), f"{r['apps']} apps distintas", COLOR_AVISO if r["fallos_apps"] else COLOR_OK)
        if est.indice is None:
            self.estab_c_indice.poner("—", "Windows no lo calcula aquí", COLOR_INFO)
        else:
            col = COLOR_OK if est.indice >= 7 else (COLOR_AVISO if est.indice >= 4 else COLOR_ERROR)
            self.estab_c_indice.poner(f"{est.indice:.1f}".replace(".", ","), "de 10 (Monitor de confiabilidad)", col)

        self.estab_m_tiempo.set_datos([[_fecha(e.fecha), _hora(e.fecha), e.tipo, e.titulo, e.detalle] for e in est.eventos], est.eventos)
        bsod = est.pantallazos
        self.estab_m_bsod.set_datos([[_fecha(p.fecha), _hora(p.fecha), dom.texto_codigo(p.codigo), p.nombre, p.significado, p.causa,
                                      p.controlador or "—", os.path.basename(p.volcado) if p.volcado else "—"] for p in bsod], bsod)
        apps = est.apps
        self.estab_m_apps.set_datos([[a.app, str(a.total), str(a.cierres), str(a.bloqueos), f"{_fecha(a.ultimo)} {_hora(a.ultimo)}", a.modulo or "—",
                                      a.excepcion or "—", a.ruta] for a in apps], apps)
        self.estab_recuento.setText(f"{len(est.eventos)} sucesos")
        if r["pantallazos"] or r["apagados"]:
            self.set_status(f"⚠️ {r['pantallazos']} pantallazos y {r['apagados']} apagados inesperados en {est.dias} días")
        else:
            self.set_status(f"✅ Sin pantallazos ni apagados inesperados en {est.dias} días · {r['fallos_apps']} fallos de apps")
        piezas.refrescar_export(self)
        if hasattr(self, "_salud_actualizar"):
            self._salud_actualizar()

    def _estab_abrir(self, programa: str, *args):
        try:
            subprocess.Popen([programa, *args], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError:
            try:
                os.startfile(programa)
            except OSError as e:
                self._avisar(SECCION, f"No se pudo abrir {programa}:\n\n{e}", QMessageBox.Warning)

    def _estab_carpeta(self):
        ok, msg = dom.abrir_carpeta_volcados()
        if not ok:
            self._avisar(SECCION, msg)

    def _estab_analizar(self):
        p = objeto_seleccionado(self.estab_t_bsod, self.estab_p_bsod, self.estab_m_bsod)
        if p is None:
            self._avisar(SECCION, "Elige un pantallazo de la lista")
            return
        if not p.volcado:
            self._avisar(SECCION, "Ese pantallazo no tiene volcado guardado: Windows no lo escribió o ya se borró.")
            return
        if dom.ruta_kd() is None:
            ok, campos, msg = dom.analizar_volcado(p.volcado)
            self._avisar(SECCION, msg)
            return
        self.set_status("🔬 Analizando el volcado (la primera vez descarga símbolos de Microsoft y puede tardar unos minutos)...")
        tareas.lanzar(self, "_estab_tarea_kd", dom.analizar_volcado, p.volcado,
                      al_terminar=lambda res: self._estab_analizado(p, res),
                      al_fallar=lambda m: self._avisar(SECCION, f"No se pudo analizar:\n\n{m}", QMessageBox.Warning))

    def _estab_analizado(self, p, res):
        ok, campos, msg = res
        if not ok:
            self.set_status("⚠️ Análisis sin resultado")
            self._avisar(SECCION, msg, QMessageBox.Warning)
            return
        p.controlador = f"{campos['CONTROLADOR']} · {campos.get('QUE_ES', '')}".strip(" ·")
        self._estab_pintar(self._estab_datos)
        self.set_status(f"✅ Controlador implicado: {p.controlador}")
        detalle = "\n".join(f"{k}: {v}" for k, v in campos.items() if k in ("BUGCHECK_CODE", "MODULE_NAME", "PROCESS_NAME", "FAILURE_BUCKET_ID"))
        self._avisar(SECCION, f"Controlador implicado: {campos['CONTROLADOR']}\n{campos.get('QUE_ES', '')}\n\n{detalle}\n\n"
                              "Siguiente paso: actualizar o volver a instalar ese controlador desde la web del fabricante.")

    def _estab_export(self):
        if not hasattr(self, "estab_tabs"):
            return []
        i = self.estab_tabs.currentIndex()
        m, titulo = [(self.estab_m_tiempo, "Estabilidad · línea de tiempo"), (self.estab_m_bsod, "Pantallazos azules"),
                     (self.estab_m_apps, "Apps que fallan")][i]
        return [(titulo, m.HEADERS, m.filas())]
