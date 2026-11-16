# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: «Cambios desde la última visita» (panel del Dashboard).

Al terminar «🔄 Sincronizar Todo» se guarda la foto del equipo (msys/domain/visitas.py)
y se compara sola con la visita anterior del mismo equipo. El panel resume lo
que cambió; «Ver cambios» abre la lista completa y «Otra visita…» compara con
cualquier visita guardada."""

import logging

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
                             QVBoxLayout)

from msys.core.utils import get_informes_folder
from msys.domain import visitas as dom
from msys.models.tabla_simple import COLOR_AVISO, COLOR_ERROR, COLOR_OK, TablaSimple
from msys.ui import piezas

logger = logging.getLogger(__name__)


def _color(c, col):
    if col == 1:
        return {dom.ANADIDO: COLOR_OK, dom.QUITADO: COLOR_ERROR}.get(c.tipo, COLOR_AVISO)
    return None


class VisitasMixin:
    def _visita_crear_panel(self) -> QFrame:
        c = self._dash_colors()
        panel = QFrame()
        panel.setObjectName("visitaPanel")
        panel.setStyleSheet(f"QFrame#visitaPanel {{ background: {c['alerts_bg']}; border: 1px solid {c['alerts_border']}; "
                            f"border-left: 4px solid #2979FF; border-radius: 8px; }} QFrame#visitaPanel QLabel {{ background: transparent; border: none; }}")
        lay = QHBoxLayout(panel)
        lay.setContentsMargins(14, 8, 10, 8)
        textos = QVBoxLayout()
        textos.setSpacing(2)
        kicker = QLabel("🕘 CAMBIOS DESDE LA ÚLTIMA VISITA")
        kicker.setStyleSheet("color:#8a94a6; font-size:8pt; font-weight:700; letter-spacing: 1px;")
        textos.addWidget(kicker)
        self._lbl_visita = QLabel("Al terminar 🔄 Sincronizar Todo se guarda la visita y se compara con la anterior de este equipo.")
        self._lbl_visita.setWordWrap(True)
        textos.addWidget(self._lbl_visita)
        lay.addLayout(textos, 1)
        self._btn_visita_ver = piezas.boton("🔍 Ver cambios", "ghost", self._visita_ver)
        self._btn_visita_otra = piezas.boton("🕘 Otra visita…", "ghost", self._visita_elegir,
                                             "Comparar lo actual con cualquier visita guardada de este equipo")
        for b in (self._btn_visita_ver, self._btn_visita_otra):
            lay.addWidget(b, 0, Qt.AlignVCenter)
        self._visita_cambios, self._visita_ref = [], None
        self._visita_pintar(inicial=True)
        return panel

    def _visita_registrar(self):
        """Tras «Sincronizar Todo»: guardar la foto y comparar con la anterior."""
        try:
            foto = dom.recoger(self)
            if not foto["data"]:
                return
            ruta, anterior = dom.guardar(get_informes_folder(), foto)
        except Exception as e:  # no poder guardar la visita no debe estropear la sincronización
            logger.error("Visita: %s", e)
            self.set_status(f"⚠️ No se pudo guardar la visita: {e}")
            return
        self._visita_actual = foto
        if anterior is None:
            self._visita_cambios, self._visita_ref = [], None
            self._lbl_visita.setText("Primera visita guardada en este equipo. La próxima vez verás aquí lo que ha cambiado.")
            self._btn_visita_ver.setVisible(False)
            return
        # ruta None = todo igual que en la última visita (no se guarda otra igual): «Sin cambios desde…»
        self._visita_cambios = dom.comparar(anterior, foto)
        self._visita_ref = anterior
        self._visita_pintar()
        if self._visita_cambios:
            self.set_status(f"🕘 {len(self._visita_cambios)} cambios desde la visita del {dom.fecha_legible(anterior)}")

    def _visita_pintar(self, inicial: bool = False):
        if inicial:
            rutas = dom.listar(get_informes_folder())
            self._btn_visita_ver.setVisible(False)
            self._btn_visita_otra.setVisible(len(rutas) >= 1)
            if rutas:
                ultima = dom.cargar(rutas[-1])
                self._lbl_visita.setText(f"Última visita: {dom.fecha_legible(ultima)} ({len(rutas)} guardadas). "
                                         "Usa 🔄 Sincronizar Todo para ver qué ha cambiado desde entonces.")
            return
        ref = dom.fecha_legible(self._visita_ref)
        self._btn_visita_otra.setVisible(True)
        if not self._visita_cambios:
            self._lbl_visita.setText(f"✅ Sin cambios desde la visita del {ref}.")
            self._btn_visita_ver.setVisible(False)
        else:
            self._lbl_visita.setText(f"Desde el {ref}: {dom.resumen(self._visita_cambios)}")
            self._btn_visita_ver.setVisible(True)

    def _visita_ver(self, cambios=None, ref=None):
        cambios = self._visita_cambios if cambios is None else cambios
        ref = self._visita_ref if ref is None else ref
        dlg = QDialog(self)
        dlg.setWindowTitle(f"🕘 Cambios desde la visita del {dom.fecha_legible(ref)}")
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dlg.resize(1000, 560)
        lay = QVBoxLayout(dlg)
        modulos = ["Todos los módulos"] + sorted({c.modulo for c in cambios})
        filtro = piezas.combo(modulos)
        fila, busqueda, _a, recuento = piezas.barra_superior("🔍 Buscar elemento...", [filtro], None, con_equipo=True)
        lay.addLayout(fila)
        modelo = TablaSimple(["Módulo", "Cambio", "Elemento", "Antes", "Ahora"], color=_color)
        vista, proxy = piezas.tabla(modelo, dlg, columna_ancha=2, busqueda=busqueda)

        def _pintar():
            m = filtro.currentText()
            lista = [c for c in cambios if m == modulos[0] or c.modulo == m]
            modelo.set_datos([[c.modulo, c.tipo, c.elemento, c.antes, c.despues] for c in lista], lista)
            recuento.setText(f"{len(lista)} cambios")
        filtro.currentIndexChanged.connect(lambda _i: _pintar())
        _pintar()
        lay.addWidget(vista, 1)
        lay.addWidget(piezas.nota("Solo se comparan las secciones cargadas en las dos visitas. Procesos, conexiones y si un servicio "
                                  "está en marcha o parado no cuentan: cambian solos a cada momento."))
        botones = QDialogButtonBox(QDialogButtonBox.Close)
        botones.rejected.connect(dlg.reject)
        lay.addWidget(botones)
        dlg.exec_()

    def _visita_elegir(self):
        """Elegir una visita guardada y compararla con la más reciente (o con lo cargado ahora)."""
        rutas = dom.listar(get_informes_folder())
        if not rutas:
            self._avisar("Visitas", "Aún no hay visitas guardadas de este equipo: usa 🔄 Sincronizar Todo.")
            return
        actual = getattr(self, "_visita_actual", None) or dom.cargar(rutas[-1])
        dlg = QDialog(self)
        dlg.setWindowTitle("🕘 Comparar con otra visita")
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dlg.resize(520, 420)
        lay = QVBoxLayout(dlg)
        lay.addWidget(piezas.nota(f"Se compara la visita del {dom.fecha_legible(actual)} con la que elijas."))
        lista = QListWidget()
        for r in reversed(rutas):
            v = dom.cargar(r)
            if v is None or v.get("ts") == (actual or {}).get("ts"):
                continue
            n = sum(len(x) for x in v.get("data", {}).values())
            it = QListWidgetItem(f"{dom.fecha_legible(v)}  ·  {n} elementos  ·  {len(v.get('data', {}))} secciones")
            it.setData(Qt.UserRole, r)
            lista.addItem(it)
        if lista.count() == 0:
            self._avisar("Visitas", "Solo hay una visita guardada: no hay otra con la que comparar.")
            return
        lista.setCurrentRow(0)
        lay.addWidget(lista, 1)
        botones = QDialogButtonBox(QDialogButtonBox.Cancel)
        comparar = QPushButton("🔍 Comparar")
        comparar.setObjectName("primary")
        botones.addButton(comparar, QDialogButtonBox.AcceptRole)
        botones.accepted.connect(dlg.accept)
        botones.rejected.connect(dlg.reject)
        lista.itemDoubleClicked.connect(lambda _i: dlg.accept())
        lay.addWidget(botones)
        if dlg.exec_() != QDialog.Accepted or lista.currentItem() is None:
            return
        ref = dom.cargar(lista.currentItem().data(Qt.UserRole))
        if ref is None or actual is None:
            self._avisar("Visitas", "No se pudo leer la visita elegida.")
            return
        cambios = dom.comparar(ref, actual)
        if not cambios:
            self._avisar("Visitas", f"Sin cambios entre el {dom.fecha_legible(ref)} y el {dom.fecha_legible(actual)}.")
            return
        self._visita_ver(cambios, ref)
