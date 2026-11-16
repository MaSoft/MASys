# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña Dashboard: tarjetas resumen, alertas, refresco global.
Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).

Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

import logging
import os
import time

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QListWidget, QListWidgetItem,
)

from msys.core.utils import _get_avisos_soporte_validados, _get_software_blacklist, _is_blacklisted_software, _is_eol_software
from msys.widgets.gauge import CircularGaugeWidget

logger = logging.getLogger(__name__)


class DashboardMixin:
    def crear_tab_dashboard(self):
        """Pestaña Dashboard: panel de control con puntuación de seguridad,
        métricas de rendimiento en vivo (gauges circulares), KPIs agregados
        de todas las secciones y un feed de alertas."""
        tab = QWidget()
        outer = QVBoxLayout(tab)
        outer.setSpacing(16)
        outer.setContentsMargins(20, 18, 20, 18)

        # ── Cabecera ──────────────────────────────────────────────
        dash_c = self._dash_colors()
        hdr_row = QHBoxLayout()
        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        self._lbl_dash_title = QLabel("📊 Dashboard")
        self._lbl_dash_title.setStyleSheet(f"font-size: 19pt; font-weight: 700; color: {dash_c['title']};")
        title_box.addWidget(self._lbl_dash_title)
        self._lbl_dash_updated = QLabel("Carga datos con 🔄 Sincronizar Todo y pulsa Actualizar")
        self._lbl_dash_updated.setStyleSheet(f"color:{dash_c['muted']}; font-size:9pt;")
        title_box.addWidget(self._lbl_dash_updated)
        hdr_row.addLayout(title_box)
        hdr_row.addStretch()
        btn_refresh_dash = QPushButton("🔄  Actualizar")
        btn_refresh_dash.setObjectName("primary")
        btn_refresh_dash.setFixedWidth(140)
        btn_refresh_dash.clicked.connect(self._refresh_dashboard)
        hdr_row.addWidget(btn_refresh_dash)
        outer.addLayout(hdr_row)

        # ── Panel "hero": puntuación de seguridad + métricas en vivo ──
        hero = QFrame()
        hero.setObjectName("dashHero")
        hero.setStyleSheet("""
            QFrame#dashHero {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #1b2838, stop:1 #26374f);
                border-radius: 14px;
            }
        """)
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(28, 22, 28, 22)
        hero_layout.setSpacing(30)

        # Bloque puntuación de seguridad
        score_block = QHBoxLayout()
        score_block.setSpacing(18)
        self._score_gauge = CircularGaugeWidget(color="#2ecc71", track_color="#33445c", text_color="white")
        self._score_gauge.setFixedSize(104, 104)
        score_block.addWidget(self._score_gauge)
        score_text_box = QVBoxLayout()
        score_text_box.setSpacing(4)
        score_text_box.addStretch()
        # «SEGURIDAD» a secas (antes «PUNTUACIÓN DE SEGURIDAD»): con el bloque de salud al lado no cabía a 1280 px
        lbl_score_kicker = QLabel("SEGURIDAD")
        lbl_score_kicker.setStyleSheet("color:#8fa3bf; font-size:8pt; font-weight:700; letter-spacing: 1px; background: transparent;")
        score_text_box.addWidget(lbl_score_kicker)
        self._lbl_score = QLabel("Sin calcular")
        self._lbl_score.setStyleSheet("color:white; font-size:14pt; font-weight:700; background: transparent;")
        score_text_box.addWidget(self._lbl_score)
        self._lbl_score_detalle = QLabel("Actualiza el dashboard para analizar hallazgos")
        self._lbl_score_detalle.setStyleSheet("color:#8fa3bf; font-size:9pt; background: transparent;")
        self._lbl_score_detalle.setWordWrap(True)
        score_text_box.addWidget(self._lbl_score_detalle)
        score_text_box.addStretch()
        score_block.addLayout(score_text_box, 1)
        hero_layout.addLayout(score_block, 3)

        vsep_salud = QFrame()
        vsep_salud.setFrameShape(QFrame.VLine)
        vsep_salud.setStyleSheet("background-color: #3a4a63; max-width: 1px;")
        hero_layout.addWidget(vsep_salud)

        # Bloque puntuación de SALUD (estabilidad, discos, dispositivos...: msys/domain/salud.py)
        salud_block = QHBoxLayout()
        salud_block.setSpacing(18)
        self._salud_gauge = CircularGaugeWidget(color="#2ecc71", track_color="#33445c", text_color="white")
        self._salud_gauge.setFixedSize(104, 104)
        salud_block.addWidget(self._salud_gauge)
        salud_text = QVBoxLayout()
        salud_text.setSpacing(4)
        salud_text.addStretch()
        lbl_salud_kicker = QLabel("SALUD DEL EQUIPO")
        lbl_salud_kicker.setStyleSheet("color:#8fa3bf; font-size:8pt; font-weight:700; letter-spacing: 1px; background: transparent;")
        salud_text.addWidget(lbl_salud_kicker)
        self._lbl_salud = QLabel("Calculando…")
        self._lbl_salud.setStyleSheet("color:white; font-size:14pt; font-weight:700; background: transparent;")
        salud_text.addWidget(self._lbl_salud)
        self._lbl_salud_detalle = QLabel("Estabilidad, discos, dispositivos y avisos de Soporte")
        self._lbl_salud_detalle.setStyleSheet("color:#8fa3bf; font-size:9pt; background: transparent;")
        self._lbl_salud_detalle.setWordWrap(True)
        salud_text.addWidget(self._lbl_salud_detalle)
        self._btn_salud_motivos = QPushButton("Ver motivos")
        self._btn_salud_motivos.setCursor(Qt.PointingHandCursor)
        self._btn_salud_motivos.setStyleSheet("QPushButton { color:#9cc4ee; background: transparent; border: none; padding: 0; "
                                              "text-align: left; font-size: 9pt; text-decoration: underline; }")
        self._btn_salud_motivos.clicked.connect(self._salud_ver_motivos)
        self._btn_salud_motivos.setVisible(False)
        salud_text.addWidget(self._btn_salud_motivos, 0, Qt.AlignLeft)
        salud_text.addStretch()
        salud_block.addLayout(salud_text, 1)
        hero_layout.addLayout(salud_block, 3)

        vsep = QFrame()
        vsep.setFrameShape(QFrame.VLine)
        vsep.setStyleSheet("background-color: #3a4a63; max-width: 1px;")
        hero_layout.addWidget(vsep)

        # Bloque de métricas en vivo (CPU / RAM / Disco) — integradas aquí,
        # sin ser una categoría/sección independiente del menú
        metrics_block = QHBoxLayout()
        metrics_block.setSpacing(26)

        def _make_metric_gauge(nombre, attr_gauge, color):
            box = QVBoxLayout()
            box.setSpacing(6)
            gauge = CircularGaugeWidget(color=color, track_color="#33445c", text_color="white")
            gauge.setFixedSize(68, 68)
            setattr(self, attr_gauge, gauge)
            box.addWidget(gauge, 0, Qt.AlignHCenter)
            lbl = QLabel(nombre)
            lbl.setStyleSheet("color:#8fa3bf; font-size:8pt; font-weight:700; letter-spacing: 0.5px; background: transparent;")
            lbl.setAlignment(Qt.AlignHCenter)
            box.addWidget(lbl)
            return box

        metrics_block.addLayout(_make_metric_gauge("CPU", "_gauge_cpu", "#3aa6ff"))
        metrics_block.addLayout(_make_metric_gauge("RAM", "_gauge_ram", "#b276f0"))
        metrics_block.addLayout(_make_metric_gauge("DISCO C:", "_gauge_disk", "#2ee6c5"))
        hero_layout.addLayout(metrics_block, 2)

        outer.addWidget(hero)

        # Cambios desde la última visita (msys/ui/visitas.py)
        outer.addWidget(self._visita_crear_panel())

        # ── Resumen por módulo: 5 a la vista y ▲ / ▼ para los 5 anteriores o siguientes (msys/ui/dash_modulos.py) ──
        self._lbl_kpi_kicker = QLabel("RESUMEN POR MÓDULO")
        self._lbl_kpi_kicker.setStyleSheet("color:#8a94a6; font-size:8pt; font-weight:700; letter-spacing: 1px;")
        outer.addWidget(self._lbl_kpi_kicker)
        from msys.ui.dash_modulos import ListaModulos
        # 5 fichas redondas y grandes a la vista (dash_modulos.ficha_modulo); ▲ / ▼ de 5 en 5, sin scroll
        from msys.ui.dash_modulos import ficha_modulo
        self._dash_modulos = ListaModulos(
            lambda m: ficha_modulo(m, self._dash_colors(), self.current_theme == "dark"), self._dash_colors)
        # La fila ocupa solo su alto: ▲ arriba y ▼ abajo, pegados a las tarjetas
        outer.addWidget(self._dash_modulos)
        outer.addStretch(1)

        # ── Alertas ───────────────────────────────────────────────
        # Con la ventana no maximizada puede no haber sitio para las
        # tarjetas de módulos y la lista de alertas a la vez. En vez de
        # comprimir ambas (lo que solapaba el texto de las tarjetas), se
        # oculta "Alertas y hallazgos" cuando la ventana no está maximizada
        # y se le cede ese espacio a las tarjetas de módulos.
        self._lbl_alerts_kicker = QLabel("ALERTAS Y HALLAZGOS")
        self._lbl_alerts_kicker.setStyleSheet("color:#8a94a6; font-size:8pt; font-weight:700; letter-spacing: 1px;")
        outer.addWidget(self._lbl_alerts_kicker)

        self._dashboard_alerts_list = QListWidget()
        self._dashboard_alerts_list.setMaximumHeight(110)
        self._dashboard_alerts_list.setFocusPolicy(Qt.NoFocus)
        self._dashboard_alerts_list.setSelectionMode(QListWidget.NoSelection)
        outer.addWidget(self._dashboard_alerts_list)

        self._cpu_prev_times = None
        self._rendimiento_timer = QTimer(self)
        self._rendimiento_timer.setInterval(2000)
        self._rendimiento_timer.timeout.connect(self._tick_rendimiento)
        self._rendimiento_timer.start()
        self._tick_rendimiento()

        self._restyle_dashboard_static_widgets()
        self._actualizar_visibilidad_alertas_dashboard()
        # La salud no necesita «Sincronizar Todo»: con la estabilidad y el espacio ya sale una primera cifra
        self._salud_motivos = []
        QTimer.singleShot(1500, self._salud_actualizar)

        return tab

    def _actualizar_visibilidad_alertas_dashboard(self):
        """Con la ventana maximizada se muestran las tarjetas de módulos y
        'Alertas y hallazgos'. Sin maximizar, se oculta 'Alertas y
        hallazgos' y ese espacio se le cede a las tarjetas de módulos (sin
        scroll: se decide qué mostrar según el espacio disponible, no se
        comprime todo para que quepa)."""
        if not hasattr(self, '_lbl_alerts_kicker'):
            return
        mostrar_alertas = self.isMaximized()
        self._lbl_alerts_kicker.setVisible(mostrar_alertas)
        self._dashboard_alerts_list.setVisible(mostrar_alertas)

    def _make_dash_card(self, emoji, titulo, valor, subtitulo="", color="#2c3e50", bg="#ffffff", progreso=None):
        """Tile de 'Resumen por módulo' con el mismo lenguaje gráfico que el
        panel de Rendimiento en vivo: un gauge circular real cuando el dato
        es una proporción (progreso 0-100), o una insignia circular de
        conteo cuando es un número absoluto sin porcentaje que lo justifique.
        (El nombre del método no ha cambiado para no tocar los ~15 puntos
        donde ya se llama con esta firma.)"""
        c = self._dash_colors()
        tile = QWidget()
        tile.setStyleSheet("background: transparent; border: none;")
        tl = QVBoxLayout(tile)
        tl.setSpacing(6)
        tl.setContentsMargins(6, 6, 6, 6)
        tl.setAlignment(Qt.AlignHCenter | Qt.AlignTop)

        if progreso is not None:
            visual = CircularGaugeWidget(color=color, track_color=c['gauge_track'], text_color=c['gauge_text'], suffix="%")
            visual.setFixedSize(66, 66)
            visual.set_value(progreso)
        else:
            visual = QLabel(str(valor))
            visual.setFixedSize(66, 66)
            visual.setAlignment(Qt.AlignCenter)
            size_pt = 15 if len(str(valor)) <= 3 else 11
            visual.setStyleSheet(f"""
                QLabel {{
                    background: {color};
                    color: white;
                    border-radius: 33px;
                    font-size: {size_pt}pt;
                    font-weight: 800;
                    border: none;
                }}
            """)
        tl.addWidget(visual, 0, Qt.AlignHCenter)

        if progreso is not None:
            lbl_val = QLabel(str(valor))
            lbl_val.setStyleSheet(f"font-size:10pt; font-weight:800; color:{color}; background:transparent; border:none;")
            lbl_val.setAlignment(Qt.AlignHCenter)
            tl.addWidget(lbl_val)

        lbl_titulo = QLabel(titulo)
        lbl_titulo.setStyleSheet(f"font-size:8.5pt; color:{c['tile_title']}; font-weight:700; background:transparent; border:none;")
        lbl_titulo.setAlignment(Qt.AlignHCenter)
        lbl_titulo.setWordWrap(True)
        tl.addWidget(lbl_titulo)

        if subtitulo:
            lbl_sub = QLabel(subtitulo)
            lbl_sub.setStyleSheet(f"font-size:7.5pt; color:{c['tile_subtitle']}; background:transparent; border:none;")
            lbl_sub.setAlignment(Qt.AlignHCenter)
            lbl_sub.setWordWrap(False)   # ya viene en líneas; partirla hacía que el nombre se montara sobre el círculo
            tl.addWidget(lbl_sub)

        return tile

    def _refresh_dashboard(self):
        """Rellena el dashboard con los datos cargados en cada modelo y en los apartados nuevos."""
        from msys.ui.dash_modulos import AVISO, ERROR, INFO, OK, Modulo
        mods = []
        alerts = []
        c = self._dash_colors()

        def _rows(model_name):
            m = getattr(self, model_name, None)
            return m.rows() if m and hasattr(m, 'rows') else []

        def _flag(r, nombre):
            v = getattr(r, nombre, False)
            return bool(v() if callable(v) else v)

        def _pl(n, uno, varios):
            return f"{n} {uno if n == 1 else varios}"

        def _pct(a, b):
            return (a / b * 100) if b else None

        # ── Sistema ───────────────────────────────────────────────
        est = getattr(self, "_estado_estabilidad", None)
        if est is not None:
            r = est.resumen()
            malos = r["pantallazos"] + r["apagados"]
            nivel = ERROR if r["pantallazos"] else (AVISO if malos or r["fallos_apps"] > 10 else OK)
            ind = f"índice {est.indice:.1f}/10" if est.indice is not None else "sin índice de Windows"
            mods.append(Modulo("🩺", "Estabilidad", str(r["pantallazos"]),
                               [_pl(r["pantallazos"], "pantallazo azul", "pantallazos azules"),
                                f"{_pl(r['apagados'], 'apagado inesperado', 'apagados inesperados')}",
                                f"{_pl(r['fallos_apps'], 'cierre de apps', 'cierres de apps')}", f"{ind} · últimos {est.dias} días"],
                               "#c0392b" if r["pantallazos"] else "#2e7d32", seccion="🩺 Estabilidad", nivel=nivel,
                               estado="Revisar" if nivel != OK else "Estable"))
            if r["pantallazos"]:
                alerts.append(f"🔴 {_pl(r['pantallazos'], 'pantallazo azul', 'pantallazos azules')} en los últimos {est.dias} días.")

        asoc = []
        for nombre in ("asoc_ext", "asoc_proto"):
            w = getattr(self, nombre, None)
            asoc += list(getattr(w, "_todas", None) or [])
        if asoc:
            from msys.domain import asociaciones as dom_asoc
            ra = dom_asoc.resumen(asoc)
            mods.append(Modulo("🔗", "Extensiones y programas", f"{ra['con_programa']}/{ra['total']}",
                               [f"{ra['usuario']} elegidas por el usuario", _pl(ra['problemas'], "con problemas", "con problemas")],
                               "#d68910" if ra['problemas'] else "#1565c0", progreso=_pct(ra['con_programa'], ra['total']),
                               seccion="🔗 Extensiones y programas", nivel=AVISO if ra['problemas'] else OK,
                               estado=_pl(ra['problemas'], "problema", "problemas") if ra['problemas'] else "Correctas"))

        carac = getattr(self, "_carac_lista", None) or []
        if carac:
            from msys.domain import caracteristicas as dom_car
            act = [x for x in carac if x.estado == dom_car.ACTIVADA]
            riesgo = [x for x in act if x.aviso]
            mods.append(Modulo("🧩", "Características de Windows", f"{len(act)}/{len(carac)}",
                               ["activadas / total"] + [x.nombre for x in riesgo[:3]],
                               "#c0392b" if riesgo else "#00838f", progreso=_pct(len(act), len(carac)),
                               seccion="🧩 Características de Windows", nivel=AVISO if riesgo else OK,
                               estado=_pl(len(riesgo), "de riesgo", "de riesgo") if riesgo else "Sin riesgos"))
            if riesgo:
                alerts.append(f"⚠️ {_pl(len(riesgo), 'característica de Windows de riesgo activada', 'características de Windows de riesgo activadas')} "
                              f"({', '.join(x.nombre for x in riesgo[:3])}).")

        # ── Rendimiento y procesos ─────────────────────────────────
        procs = _rows('processes_model')
        if procs:
            mem_total = sum(r.memory_mb for r in procs)
            temp_procs = [r for r in procs if '\\temp\\' in (r.exe_path or '').lower()]
            por_nombre = {}
            for r in procs:
                por_nombre[r.name] = por_nombre.get(r.name, 0) + (r.memory_mb or 0)
            top = sorted(por_nombre.items(), key=lambda x: -x[1])[:2]
            usuarios = len({(r.username or "").strip() for r in procs if (r.username or "").strip()})
            det_p = [f"Memoria total {mem_total / 1024:.1f} GB" if mem_total >= 1024 else f"Memoria total {mem_total:.0f} MB"]
            det_p += [f"{n}: {mb / 1024:.1f} GB" if mb >= 1024 else f"{n}: {mb:.0f} MB" for n, mb in top]
            det_p.append(_pl(usuarios, "usuario", "usuarios") + f" · {len(por_nombre)} programas distintos")
            mods.append(Modulo("⚙️", "Procesos activos", str(len(procs)), det_p,
                               "#1976d2", seccion="⚙️ Procesos", nivel=ERROR if temp_procs else OK,
                               estado=f"{len(temp_procs)} desde Temp" if temp_procs else ""))

        svcs = _rows('services_model')
        if svcs:
            running = sum(1 for r in svcs if r.status == "running")
            def _arr(r):
                return (r.startup_type or "").lower()
            auto_paradas = sum(1 for r in svcs if _arr(r).startswith("auto") and r.status != "running")
            deshab = sum(1 for r in svcs if _arr(r).startswith("disab") or _arr(r).startswith("deshab"))
            manual = sum(1 for r in svcs if _arr(r).startswith("manual"))
            mods.append(Modulo("🛠️", "Servicios", f"{running}/{len(svcs)}",
                               ["en ejecución / total", f"{auto_paradas} automáticos detenidos", f"{manual} manuales · {deshab} deshabilitados"],
                               "#388e3c",
                               progreso=_pct(running, len(svcs)), seccion="🛠️ Servicios"))

        inicio = _rows('startup_model')
        if inicio:
            hab = sum(1 for r in inicio if (r.estado or '').lower().startswith('habilitad'))
            susp = sum(1 for r in inicio if _flag(r, 'es_sospechosa'))
            mods.append(Modulo("🚀", "Programas de inicio", f"{hab}/{len(inicio)}", ["habilitados / total"], "#6a1b9a",
                               progreso=_pct(hab, len(inicio)), seccion="🚀 Programas de Inicio",
                               nivel=AVISO if susp else OK, estado=_pl(susp, "sospechoso", "sospechosos") if susp else ""))

        tareas_rows = _rows('tasks_model')
        riesgo_tasks = sum(1 for r in tareas_rows if _flag(r, 'es_sospechosa'))
        if tareas_rows:
            mods.append(Modulo("⏰", "Tareas programadas", str(len(tareas_rows)), ["tareas registradas"], "#5d4037",
                               seccion="⏰ Tareas Programadas", nivel=AVISO if riesgo_tasks else OK,
                               estado=_pl(riesgo_tasks, "sospechosa", "sospechosas") if riesgo_tasks else ""))

        # ── Software y actualizaciones ─────────────────────────────
        sw = _rows('software_model')
        blacklist = _get_software_blacklist()
        riesgo_sw_bl = sum(1 for r in sw if _is_blacklisted_software(r.nombre, blacklist))
        riesgo_sw_eol = sum(1 for r in sw if _is_eol_software(r.nombre))
        if sw:
            det = []
            if riesgo_sw_bl:
                det.append(f"{riesgo_sw_bl} no autorizado(s)")
            if riesgo_sw_eol:
                det.append(f"{riesgo_sw_eol} sin soporte (EOL)")
            editores = {}
            for r in sw:
                e = (r.editor or "").strip()
                if e and e.upper() != "N/A":
                    editores[e] = editores.get(e, 0) + 1
            top_ed = sorted(editores.items(), key=lambda x: -x[1])[:2]
            det = ["aplicaciones instaladas"] + det + [f"{e[:28]}: {n}" for e, n in top_ed]
            mods.append(Modulo("📦", "Software instalado", str(len(sw)), det, "#e65100",
                               seccion="📦 Software Instalado", nivel=ERROR if riesgo_sw_bl else (AVISO if riesgo_sw_eol else OK),
                               estado="Revisar" if (riesgo_sw_bl or riesgo_sw_eol) else ""))

        paquetes = _rows('paquetes_model')
        if paquetes:
            bloq = sum(1 for r in paquetes if getattr(r, 'bloqueado', False))
            mods.append(Modulo("📦", "Gestor de paquetes", str(len(paquetes)),
                               ["actualizaciones de programas disponibles"] + ([f"{bloq} bloqueadas"] if bloq else []),
                               "#ef6c00", seccion="📦 Gestor de Paquetes", nivel=AVISO, estado="Por actualizar"))

        pending = _rows('pending_updates_model')
        criticas = 0
        if pending:
            criticas = sum(1 for r in pending if 'crít' in (r.criticidad or '').lower())
            mods.append(Modulo("🔄", "Actualizaciones pendientes", str(len(pending)), [_pl(criticas, "crítica", "críticas")],
                               "#c62828" if criticas else "#e65100", seccion="🔄 Actualizaciones",
                               nivel=ERROR if criticas else AVISO, estado="Críticas" if criticas else "Pendientes"))

        drivers = _rows('drivers_model')
        if drivers:
            unsigned = sum(1 for r in drivers if (r.firmado or '').strip().lower() == 'no')
            firmados = len(drivers) - unsigned
            mods.append(Modulo("🔧", "Controladores", f"{firmados}/{len(drivers)}", ["con firma digital", f"{unsigned} sin firmar"],
                               c['neutral_accent'], progreso=_pct(firmados, len(drivers)), seccion="🖥️ Adm. Dispositivos",
                               nivel=AVISO if unsigned else OK, estado=f"{unsigned} sin firma" if unsigned else ""))

        # ── 5.4.0: módulos que faltaban con datos que «Sincronizar Todo» ya carga ──
        sec = getattr(self, '_last_seguridad_info', None) or {}
        if sec:
            defender = sec.get('Defender') or {}
            otros_av = [a.get('Nombre', '') for a in (sec.get('Antivirus') or [])
                        if isinstance(a, dict) and a.get('Nombre') and 'defender' not in a.get('Nombre', '').lower()]
            if otros_av:
                av_txt, av_ok = f"Antivirus: {otros_av[0][:30]}", True
            elif defender.get('Habilitado') == 'Sí' and 'INACTIVA' not in str(defender.get('ProteccionTiempoReal', '')):
                av_txt, av_ok = "Defender activo", True
            else:
                av_txt, av_ok = "SIN antivirus activo", False
            fw_off = [n for n, e in (sec.get('Firewall') or {}).items() if 'INACTIVO' in str(e).upper()]
            bl = sec.get('BitLocker') or []
            cifradas = sum(1 for v in bl if isinstance(v, dict) and 'On' in str(v.get('Estado', '')))
            malo = (not av_ok) or bool(fw_off)
            mods.append(Modulo("🛡️", "Seguridad del sistema", "OK" if not malo else "!",
                               [av_txt, f"Firewall: {'desactivado en ' + ', '.join(fw_off) if fw_off else 'activo'}",
                                f"BitLocker: {cifradas}/{len(bl)} unidades cifradas" if bl else "BitLocker: sin datos",
                                f"Secure Boot: {sec.get('SecureBoot', 'N/A')}"],
                               "#c0392b" if malo else "#2e7d32", seccion="🛡️ Seguridad",
                               nivel=ERROR if malo else OK, estado="Revisar" if malo else "Protegido"))

        lic = getattr(self, '_last_licencias_info', None) or {}
        if lic:
            estado_win = str(lic.get('Estado', 'N/A'))
            office = lic.get('Office') or {}
            det_l = [f"Windows: {estado_win}"]
            if office.get('Detectado'):
                det_l.append(f"Office: {office.get('Estado', 'N/A')}")
            win_ok = estado_win == "Activado"
            off_ok = (not office.get('Detectado')) or office.get('Estado') == "Activado"
            mods.append(Modulo("🔑", "Licencias", "OK" if (win_ok and off_ok) else "!", det_l,
                               "#2e7d32" if (win_ok and off_ok) else ("#c0392b" if not win_ok else "#d68910"),
                               seccion="🔑 Licencias", nivel=OK if (win_ok and off_ok) else (ERROR if not win_ok else AVISO),
                               estado="Activadas" if (win_ok and off_ok) else "Revisar"))
            if not win_ok:
                alerts.append(f"🔴 Windows sin activar ({estado_win}).")

        if getattr(self, '_updates_cargadas', False) and getattr(self, '_reboot_pendiente', False):
            mods.append(Modulo("🔁", "Reinicio pendiente", "Sí", ["Windows necesita reiniciar para terminar",
                                                                 "de instalar actualizaciones o cambios"],
                               "#d68910", seccion="🔄 Actualizaciones", nivel=AVISO, estado="Reiniciar"))
            alerts.append("⚠️ Windows tiene un reinicio pendiente.")

        adaptadores = _rows('network_model')
        if adaptadores:
            activos = [r for r in adaptadores if (r.estado or '') == 'Activo']
            vpn = [r for r in activos if 'vpn' in (r.tipo or '').lower() or 'túnel' in (r.tipo or '').lower()]
            fisicos = [r for r in activos if (r.tipo or '') in ('Ethernet', 'WiFi')]
            mods.append(Modulo("🌐", "Redes", f"{len(activos)}/{len(adaptadores)}",
                               ["adaptadores activos / total",
                                ", ".join(f"{r.nombre} {r.ip}" for r in fisicos[:2]) or "sin adaptador físico activo",
                                _pl(len(vpn), "VPN activa", "VPN activas")],
                               "#0277bd", progreso=_pct(len(activos), len(adaptadores)), seccion="🌐 Redes",
                               nivel=AVISO if not fisicos else OK, estado="Sin red física" if not fisicos else ""))

        # ── Discos ────────────────────────────────────────────────
        parts = _rows('partitions_model')
        if parts:
            def _uso(p):
                try:
                    return float(str(p.porcentaje).replace('%', '').replace(',', '.'))
                except ValueError:
                    return None
            usos = [(p, _uso(p)) for p in parts if _uso(p) is not None]
            peor = max(usos, key=lambda x: x[1]) if usos else None
            llenas = [p for p, u in usos if u >= 90]
            mods.append(Modulo("💽", "Particiones", f"{peor[1]:.0f}%" if peor else str(len(parts)),
                               ([f"la más llena: {peor[0].letra or peor[0].etiqueta} ({peor[0].libre} libres)"] if peor else [])
                               + [f"{len(parts)} particiones"], "#c0392b" if llenas else "#00897b",
                               progreso=peor[1] if peor else None, seccion="💽 Discos Duros",
                               nivel=ERROR if llenas else OK, estado=_pl(len(llenas), "casi llena", "casi llenas") if llenas else ""))
        discos = _rows('disk_inventory_model')
        if discos:
            malos = [d for d in discos if (d.estado_salud or '').lower() not in ('healthy', 'correcto', 'saludable', '')]
            mods.append(Modulo("🖴", "Discos físicos", str(len(discos)), [f"{d.modelo} · {d.estado_salud}" for d in discos[:3]],
                               "#c0392b" if malos else "#455a64", seccion="💽 Discos Duros",
                               nivel=ERROR if malos else OK, estado=_pl(len(malos), "con fallos", "con fallos") if malos else "Sanos"))
            if malos:
                alerts.append(f"🔴 {_pl(len(malos), 'disco con estado de salud malo', 'discos con estado de salud malo')}.")

        # ── Red ───────────────────────────────────────────────────
        conns = _rows('connections_model')
        if conns:
            established = sum(1 for r in conns if (r.estado or '').strip().lower() == 'establecida')
            puertos_escucha = getattr(self, '_last_seguridad_info', {}).get('Puertos', [])
            mods.append(Modulo("🌐", "Conexiones de red", str(established),
                               ["establecidas"] + ([f"{len(puertos_escucha)} puertos en escucha"] if puertos_escucha else []),
                               "#7b1fa2", seccion="🌐 Conexiones Red"))

        diag = getattr(getattr(self, "hred_diag", None), "resultados", None) or []
        if diag:
            from msys.osi.models import AVISO as D_AVISO, ERROR as D_ERROR, OK as D_OK
            n = {k: sum(1 for r in diag if r.resultado == k) for k in (D_OK, D_AVISO, D_ERROR)}
            mods.append(Modulo("🧪", "Herramientas de red", f"{n[D_OK]}/{len(diag)}",
                               [f"diagnóstico de {self.hred_diag.destino}", _pl(n[D_AVISO], "aviso", "avisos"),
                                _pl(n[D_ERROR], "error", "errores")],
                               "#c0392b" if n[D_ERROR] else ("#d68910" if n[D_AVISO] else "#2e7d32"),
                               progreso=_pct(n[D_OK], len(diag)), seccion="🧪 Herramientas de red",
                               nivel=ERROR if n[D_ERROR] else (AVISO if n[D_AVISO] else OK),
                               estado="Con errores" if n[D_ERROR] else ("Con avisos" if n[D_AVISO] else "Todo bien")))

        wifi = _rows('wifi_model')
        if wifi:
            debiles = sum(1 for r in wifi if _flag(r, 'es_seguridad_debil'))
            conectada = next((r.nombre for r in wifi if (getattr(r, "conectado", "") or "").lower() in ("sí", "si", "yes")), "")
            abiertas = sum(1 for r in wifi if "abierta" in (r.autenticacion or "").lower() or "open" in (r.autenticacion or "").lower())
            det_w = ["perfiles guardados", f"Conectada: {conectada}" if conectada else "Sin conexión WiFi ahora"]
            if abiertas:
                det_w.append(_pl(abiertas, "red abierta", "redes abiertas"))
            mods.append(Modulo("📶", "Redes WiFi", str(len(wifi)), det_w, "#00838f", seccion="📶 WiFi",
                               nivel=AVISO if debiles else OK, estado=_pl(debiles, "insegura", "inseguras") if debiles else ""))

        red_local = _rows('red_local_model')
        if red_local:
            sosp = sum(1 for r in red_local if getattr(r, 'sospechoso', False))
            mods.append(Modulo("🔍", "Red local", str(len(red_local)), ["dispositivos en la red"], "#0277bd",
                               seccion="🔍 Red Local", nivel=AVISO if sosp else OK, estado=_pl(sosp, "a revisar", "a revisar") if sosp else ""))

        fw_rows = _rows('firewall_model')
        riesgo_fw = sum(1 for r in fw_rows if _flag(r, 'es_regla_amplia'))
        if fw_rows:
            hab = sum(1 for r in fw_rows if (r.habilitada or '').lower() in ('sí', 'si', 'true', 'yes'))
            mods.append(Modulo("🔥", "Firewall", str(len(fw_rows)), [f"{hab} reglas habilitadas"], "#d84315",
                               seccion="🔥 Firewall", nivel=ERROR if riesgo_fw else OK,
                               estado=_pl(riesgo_fw, "regla amplia", "reglas amplias") if riesgo_fw else ""))

        shares_rows = _rows('model')
        riesgo_shares = sum(1 for r in shares_rows if _flag(r, 'es_riesgo_everyone_escritura'))
        if shares_rows:
            mods.append(Modulo("📂", "Carpetas compartidas", str(len(shares_rows)), ["permisos revisados"], "#8d6e63",
                               seccion="📂 Carpetas Compartidas", nivel=ERROR if riesgo_shares else OK,
                               estado=f"{riesgo_shares} con Todos+escritura" if riesgo_shares else ""))

        # ── Usuarios y accesos ────────────────────────────────────
        users = _rows('users_model')
        if users:
            activos = sum(1 for r in users if (r.estado or '').strip().lower() == 'activo')
            admins = sum(1 for r in users if any(g in (r.grupos or '').lower() for g in ['administrador', 'administrators']))
            mods.append(Modulo("👥", "Usuarios", f"{activos}/{len(users)}", ["activos / total", _pl(admins, "administrador", "administradores")],
                               "#c62828", progreso=_pct(activos, len(users)), seccion="👥 Usuarios",
                               nivel=AVISO if admins > 2 else OK, estado=f"{admins} admins" if admins > 2 else ""))

        creds = _rows('creds_model')
        if creds:
            mods.append(Modulo("🔑", "Credenciales", str(len(creds)), ["almacenadas en el Administrador de credenciales"], "#f57f17",
                               seccion="🔑 Credenciales"))

        certs = _rows('certs_model')
        if certs:
            cad = sum(1 for r in certs if (r.estado or '').lower().startswith('caducad'))
            prox = sum(1 for r in certs if (r.estado or '').lower().startswith('proxima') or (r.estado or '').lower().startswith('próxima'))
            mods.append(Modulo("🔐", "Certificados", str(len(certs)), [_pl(cad, "caducado", "caducados"), _pl(prox, "a punto de caducar", "a punto de caducar")],
                               "#6d4c41", seccion="🔐 Certificados", nivel=AVISO if (cad or prox) else OK,
                               estado=_pl(cad + prox, "por renovar", "por renovar") if (cad or prox) else ""))

        remote = _rows('remote_model')
        if remote:
            fallos = sum(1 for r in remote if (r.resultado or '').strip().lower() == 'fallo')
            ok_ = sum(1 for r in remote if (r.resultado or '').strip().lower() in ('exito', 'éxito'))
            mods.append(Modulo("🔒", "Conexiones remotas", f"{ok_}/{len(remote)}", ["éxito / total", f"{fallos} fallos (30 días)"],
                               "#c62828" if fallos >= 10 else c['neutral_accent'], progreso=_pct(ok_, len(remote)),
                               seccion="🔒 Conexiones Remotas", nivel=ERROR if fallos >= 10 else OK,
                               estado=f"{fallos} fallos" if fallos >= 10 else ""))

        sesiones = _rows('sesiones_model')
        if sesiones:
            mods.append(Modulo("👤", "Sesiones activas", str(len(sesiones)), ["sesiones abiertas en el equipo"], "#546e7a",
                               seccion="👤 Sesiones Activas"))

        events = _rows('eventos_model')
        if events:
            criticos = sum(1 for r in events if 'critico' in (r.nivel or '').lower())
            errores = sum(1 for r in events if 'error' in (r.nivel or '').lower())
            advert = sum(1 for r in events if 'advertencia' in (r.nivel or '').lower())
            dias_ev = self.spin_eventos_dias.value() if hasattr(self, "spin_eventos_dias") else 7
            mods.append(Modulo("⚠️", f"Eventos ({dias_ev} días)", str(len(events)),
                               [_pl(criticos, "crítico", "críticos"), _pl(errores, "error", "errores"), f"{advert} advertencias"],
                               "#c62828" if criticos else ("#e65100" if errores > 10 else c['neutral_accent']),
                               seccion="⚠️ Eventos Sistema", nivel=ERROR if criticos else (AVISO if errores > 10 else OK),
                               estado=f"{criticos} críticos" if criticos else ""))
            if criticos > 0:
                alerts.append(f"🔴 {criticos} evento(s) crítico(s) en los últimos {dias_ev} días.")

        ps_hist = _rows('ps_history_model')
        if ps_hist:
            mods.append(Modulo("📜", "Historial PowerShell", str(len(ps_hist)), ["comandos registrados"], c['neutral_accent'],
                               seccion="📜 Historial PS", nivel=INFO))

        # ── Hardware y periféricos ────────────────────────────────
        usb = _rows('usb_model')
        if usb:
            mods.append(Modulo("🔌", "Dispositivos USB", str(len(usb)), ["detectados"], "#4527a0", seccion="🔌 Dispositivos USB", nivel=INFO))
        impresoras = _rows('printers_model')
        if impresoras:
            pred = next((r.nombre for r in impresoras if (r.predeterminada or '').lower() in ('sí', 'si', 'true', 'yes')), "")
            mods.append(Modulo("🖨️", "Impresoras", str(len(impresoras)), [f"predeterminada: {pred}" if pred else "sin predeterminada"],
                               "#37474f", seccion="🖨️ Impresoras", nivel=INFO))
        bt = _rows('bluetooth_model')
        if bt:
            mods.append(Modulo("🔵", "Bluetooth", str(len(bt)), ["dispositivos emparejados"], "#1565c0", seccion="🔵 Bluetooth", nivel=INFO))
        correo = _rows('email_model')
        if correo:
            mods.append(Modulo("📧", "Cuentas de correo", str(len(correo)), ["configuradas en el equipo"], "#00695c",
                               seccion="📧 Cuentas Correo", nivel=INFO))

        alm = getattr(self, "movil_almacen", None)
        if alm is not None:
            try:
                disps = alm.dispositivos()
            except Exception as e:  # movil.db dañado o bloqueado: el Dashboard sigue
                logger.warning("Dashboard, móviles: %s", e)
                disps = []
            if disps:
                d = disps[0]
                det = [f"último: {d.get('nombre') or d.get('modelo') or d['id']}", _pl(d.get('informes', 0), "informe", "informes")]
                malos = 0
                try:
                    regs = alm.informes(d["id"], limite=1)
                    if regs:
                        from msys.movil import analisis
                        hall = analisis.hallazgos(regs[0]["informe"])
                        malos = sum(1 for h in hall if h[0] == analisis.ERROR)
                        det.append(_pl(len(hall), "hallazgo", "hallazgos"))
                except Exception as e:
                    logger.warning("Dashboard, informe del móvil: %s", e)
                mods.append(Modulo("📱", "Móviles Android", str(len(disps)), det, "#c0392b" if malos else "#2e7d32",
                                   seccion="📱 Móviles Android", nivel=ERROR if malos else OK,
                                   estado=_pl(malos, "riesgo", "riesgos") if malos else _pl(len(disps), "móvil", "móviles")))

        # ── Administración ────────────────────────────────────────
        mig = getattr(self, "_mig_filas", None) or []
        if mig:
            from msys.domain import migracion as dom_mig
            imp = sum(1 for e, _s, _d in mig if e.importante)
            faltan = sum(1 for _e, s, _d in mig if s == dom_mig.FALTA)
            comparando = bool(getattr(self, "_mig_origen", None))
            inv = getattr(self, "_mig_inv", None) or {}
            mods.append(Modulo("🧳", "Inventario de migración", str(len(mig)),
                               ([f"comparado con {self._mig_origen.get('equipo', '')}", _pl(faltan, "falta", "faltan")] if comparando
                                else [f"inventario de {inv.get('equipo', '')}", _pl(imp, "importante", "importantes")]),
                               "#c0392b" if faltan else "#6a1b9a", seccion="🧳 Inventario de migración",
                               nivel=AVISO if faltan else INFO, estado=_pl(faltan, "falta", "faltan") if faltan else f"{imp} importantes"))

        # ── Señales de seguridad (para la puntuación) ──
        # 5.4.0: misma fuente que el botón «🛡️ Seguridad» y los informes (_calcular_hallazgos_seguridad).
        # Antes el Dashboard tenía su propia lista corta y no miraba la sección Seguridad: en el equipo de
        # pruebas daba 85/100 «1 hallazgo» con Defender apagado). 5.4.2: resta por CATEGORÍA con tope de 20
        # (🔴 10 puntos, ⚠️ 3 por hallazgo distinto): restando cada detalle por separado cualquier equipo
        # con varias reglas o unidades sin cifrar caía a 0/100 y la cifra dejaba de distinguir nada.
        score_issues = []
        try:
            hallazgos = self._calcular_hallazgos_seguridad()
        except Exception as e:  # un detector roto no debe dejar el Dashboard sin pintar
            logger.warning("Dashboard, hallazgos de seguridad: %s", e)
            hallazgos = []
        grupos = {}
        for nivel_h, cat_h, desc_h, _det in hallazgos:
            if nivel_h in ("🔴", "⚠️"):
                grupos[(nivel_h, cat_h, desc_h)] = grupos.get((nivel_h, cat_h, desc_h), 0) + 1
        resta_cat = {}
        for (nivel_h, cat_h, desc_h), n in sorted(grupos.items(), key=lambda x: x[0][0] != "🔴"):
            texto = f"{nivel_h} {cat_h}: {desc_h}" + (f" (×{n})" if n > 1 else "")
            score_issues.append((texto, 0))
            resta_cat[cat_h] = min(20, resta_cat.get(cat_h, 0) + (10 if nivel_h == "🔴" else 3))
        for texto, _peso in score_issues:
            if texto not in alerts:
                alerts.append(texto)

        puntuacion = max(0, 100 - sum(resta_cat.values()))
        if puntuacion >= 90:
            score_color, score_icon = "#2ecc71", "🟢"
        elif puntuacion >= 70:
            score_color, score_icon = "#f1c40f", "🟡"
        else:
            score_color, score_icon = "#e74c3c", "🔴"

        if mods:
            self._score_gauge.set_value(puntuacion)
            self._score_gauge.set_color(score_color)
            self._lbl_score.setText(f"{score_icon} {puntuacion}/100")
            plural = "hallazgo" if len(score_issues) == 1 else "hallazgos"
            self._lbl_score_detalle.setText(f"{len(score_issues)} {plural} detectados" if score_issues
                                            else "Sin hallazgos de riesgo detectados")
            self._lbl_dash_updated.setText(f"Última actualización: {time.strftime('%H:%M:%S')}")
            if puntuacion < 70 and puntuacion != getattr(self, '_ultima_puntuacion_notificada', None):
                self.tray_notify("⚠️ MASys — Puntuación de seguridad baja",
                                 f"Puntuación actual: {puntuacion}/100 ({len(score_issues)} hallazgo(s)). Revisa el Dashboard.",
                                 critico=True)
                self._ultima_puntuacion_notificada = puntuacion
        else:
            self._score_gauge.set_value(0)
            self._score_gauge.set_color("#33445c")
            self._lbl_score.setText("Sin calcular")
            self._lbl_score_detalle.setText("Actualiza el dashboard para analizar hallazgos")

        self._dash_modulos.set_modulos(mods)
        self._lbl_kpi_kicker.setText(f"RESUMEN POR MÓDULO · {len(mods)} con datos")

        self._dashboard_alerts_list.clear()
        if alerts:
            for texto in alerts:
                item = QListWidgetItem(texto)
                if texto.startswith("🔴"):
                    item.setForeground(QColor("#c0392b"))
                elif texto.startswith("⚠️"):
                    item.setForeground(QColor("#b7950b"))
                self._dashboard_alerts_list.addItem(item)
        elif mods:
            item = QListWidgetItem("✅ Sin alertas detectadas en los datos cargados.")
            item.setForeground(QColor("#1e8449"))
            self._dashboard_alerts_list.addItem(item)

        self._salud_actualizar()
        self.set_status(f"📊 Dashboard actualizado — {len(mods)} módulos con datos")

    # ── Salud del equipo ──────────────────────────────────────────────────────────────

    def _salud_actualizar(self):
        """Recalcula la puntuación de salud con lo ya cargado. La estabilidad se lee
        en segundo plano la primera vez (y al pulsar Actualizar en su sección)."""
        from msys.domain import salud
        est = getattr(self, "_estado_estabilidad", None)
        if est is None:
            self._salud_leer_estabilidad()
        motivos = salud.motivos_estabilidad(est.resumen(), est.dias) if est is not None else []
        validados = _get_avisos_soporte_validados()
        avisos = {}
        for seccion, nombre in salud.DETECTORES_SALUD:
            detector = getattr(self, nombre, None)
            if detector is None:
                continue
            try:
                encontrados = detector() or []
            except Exception as e:  # un detector roto no debe tumbar el Dashboard
                logger.warning("Detector %s: %s", nombre, e)
                continue
            textos = [t[1] for t in encontrados if len(t) >= 2 and t[0] not in validados]
            if textos:
                avisos.setdefault(seccion, []).extend(textos)
        motivos += salud.motivos_soporte(avisos)
        particiones = getattr(self, "partitions_model", None)
        if not (particiones and particiones.rows()):
            motivos += salud.motivo_espacio(self._salud_libre_sistema(), os.environ.get("SystemDrive", "C:"))
        puntuacion, orden = salud.puntuar(motivos)
        self._salud_motivos = orden
        color, icono = salud.nivel(puntuacion)
        self._salud_gauge.set_value(puntuacion)
        self._salud_gauge.set_color(color)
        self._lbl_salud.setText(f"{icono} {puntuacion}/100" if est is not None or orden else "Calculando…")
        if orden:
            self._lbl_salud_detalle.setText(f"{len(orden)} motivo{'s' if len(orden) != 1 else ''}: {orden[0].texto.lstrip('🔴⚠️ℹ️ ')}")
        elif est is not None:
            self._lbl_salud_detalle.setText("Sin problemas de estabilidad, discos ni dispositivos en lo revisado")
        self._btn_salud_motivos.setVisible(bool(orden))

    def _salud_leer_estabilidad(self):
        from msys.core import tareas
        from msys.domain import estabilidad

        def _ok(est):
            self._estado_estabilidad = est
            self._salud_actualizar()
        tareas.lanzar(self, "_salud_tarea", estabilidad.leer, 30, al_terminar=_ok,
                      al_fallar=lambda m: logger.warning("Estabilidad para la salud: %s", m))

    @staticmethod
    def _salud_libre_sistema():
        try:
            import psutil
            u = psutil.disk_usage(os.environ.get("SystemDrive", "C:") + "\\")
            return 100.0 - u.percent
        except Exception:
            return None

    def _salud_ver_motivos(self):
        """Lista de motivos; doble clic (o «Ir a la sección») lleva a la sección de cada uno."""
        from PyQt5.QtWidgets import QDialog, QDialogButtonBox
        from msys.models.tabla_simple import COLOR_AVISO, COLOR_ERROR, TablaSimple, objeto_seleccionado
        from msys.ui import piezas
        dlg = QDialog(self)
        dlg.setWindowTitle("🩺 Salud del equipo: motivos")
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dlg.resize(860, 440)
        lay = QVBoxLayout(dlg)
        lay.addWidget(piezas.nota("Cada motivo resta puntos (con tope por tipo). Doble clic para ir a su sección. "
                                  "Los avisos de Soporte que valides dejan de contar."))
        modelo = TablaSimple(["Motivo", "Sección", "Puntos"],
                             color=lambda m, c: (COLOR_ERROR if m.texto.startswith("🔴") else COLOR_AVISO) if c == 0 else None)
        modelo.set_datos([[m.texto, m.seccion, f"-{m.puntos}"] for m in self._salud_motivos], self._salud_motivos)
        vista, proxy = piezas.tabla(modelo, dlg, columna_ancha=0)

        def _ir():
            m = objeto_seleccionado(vista, proxy, modelo)
            if m is not None:
                dlg.accept()
                self.seleccionar_seccion(m.seccion)
        vista.doubleClicked.connect(lambda _i: _ir())
        lay.addWidget(vista, 1)
        botones = QDialogButtonBox(QDialogButtonBox.Close)
        ir = botones.addButton("➡️ Ir a la sección", QDialogButtonBox.ActionRole)
        ir.clicked.connect(_ir)
        botones.rejected.connect(dlg.reject)
        lay.addWidget(botones)
        dlg.exec_()

