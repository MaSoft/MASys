# -*- coding: utf-8 -*-
"""Diálogo "Calendario de ejecución de tareas" — acción de Soporte de la
sección Tareas Programadas (ver msys/ui/soporte.py,
_registro_soporte_por_seccion). Diálogo autónomo (no es un mixin), mismo
patrón que RecycleBinDialog: se instancia bajo demanda con las filas ya
cargadas en self.tasks_model — no lanza ningún hilo propio ni llama a
PowerShell, solo interpreta datos ya recolectados.

Solo las tareas con un disparador de HORARIO FIJO (Una vez/Diario/Semanal/
Mensual/Recurrente — ver _CLASES_CON_HORARIO_FIJO en msys/models/rows.py) se
pueden situar en la cuadrícula; el resto (inicio de sesión, arranque,
inactividad, evento del sistema...) se listan aparte porque no tienen una
hora real a la que ocurran — colocarlas en la rejilla sería inventar un dato.

tarea_en_horario_laboral() se reutiliza desde soporte.py para el aviso
proactivo de _detectar_soporte_tareas: no repetir aquí la franja L-V
08:00-19:00, es la misma constante en los dos sitios."""

import math
from typing import List, Tuple

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTableWidget, QTableWidgetItem,
    QDialogButtonBox, QListWidget, QListWidgetItem, QHeaderView, QMenu, QApplication,
)

from msys.models.rows import ScheduledTaskRow, ScheduledTaskTrigger

# Franja "operativa" fija por defecto (L-V 08:00-19:00) — confirmada con el
# usuario. Si algún día hace falta que sea configurable por instalación,
# este es el único punto a tocar (aquí y en soporte.py, que la reutiliza).
HORA_INICIO_LABORAL = 8
HORA_FIN_LABORAL = 19          # exclusiva: la última hora cubierta es la 18
DIAS_LABORALES = {0, 1, 2, 3, 4}  # 0=lunes..4=viernes

_NOMBRES_DIA_CORTO = ["Lu", "Ma", "Mi", "Ju", "Vi", "Sá", "Do"]


def _horas_cubiertas(trigger: ScheduledTaskTrigger) -> List[int]:
    """Horas del día (0-23) que cubre un disparador de horario fijo, contando
    su ventana de repetición si la tiene. Envuelve la medianoche con módulo
    24 (un disparador a las 23:00 que repite 3h cubre 23, 0 y 1)."""
    if not trigger.tiene_horario_fijo:
        return []
    hora_inicio = int(trigger.hora.split(":")[0])
    duracion_horas = 1
    if trigger.repite_cada_min and trigger.repite_durante_min:
        duracion_horas = max(1, math.ceil(trigger.repite_durante_min / 60))
    duracion_horas = min(duracion_horas, 24)
    return [(hora_inicio + i) % 24 for i in range(duracion_horas)]


def _disparador_en_horario_laboral(trigger: ScheduledTaskTrigger) -> bool:
    """True si el disparador (hora + días, ya deshabilitados excluidos)
    solapa con la franja operativa."""
    if not trigger.habilitado or not trigger.tiene_horario_fijo:
        return False
    dias = trigger.dias_semana
    if dias is not None and not any(d in DIAS_LABORALES for d in dias):
        return False
    return any(HORA_INICIO_LABORAL <= h < HORA_FIN_LABORAL for h in _horas_cubiertas(trigger))


def tarea_en_horario_laboral(row: ScheduledTaskRow) -> bool:
    """True si alguno de los disparadores de horario fijo de la tarea cae,
    total o parcialmente, en la franja operativa (L-V 08:00-19:00)."""
    return any(_disparador_en_horario_laboral(t) for t in row.disparadores)


def _texto_dias(dias) -> str:
    if dias is None or len(dias) == 7:
        return "Todos"
    return ", ".join(_NOMBRES_DIA_CORTO[d] for d in dias)


def _paleta_calendario(tema_oscuro: bool) -> dict:
    if tema_oscuro:
        return {
            "normal_bg": QColor("#1e3a5f"), "normal_fg": QColor("#dbeafe"),
            "laboral_bg": QColor("#4c1d1d"), "laboral_fg": QColor("#fecaca"),
            "deshabilitada_bg": QColor("#374151"), "deshabilitada_fg": QColor("#9ca3af"),
            "zona_laboral_vacia": QColor("#2a2418"),
        }
    return {
        "normal_bg": QColor("#dbeafe"), "normal_fg": QColor("#1e3a5f"),
        "laboral_bg": QColor("#fecaca"), "laboral_fg": QColor("#7f1d1d"),
        "deshabilitada_bg": QColor("#e5e7eb"), "deshabilitada_fg": QColor("#6b7280"),
        "zona_laboral_vacia": QColor("#fff7ed"),
    }


class TaskCalendarDialog(QDialog):
    COLUMNAS_FIJAS = ["Tarea", "Días", "Hora"]
    N_HORAS = 24

    def __init__(self, parent, tareas: List[ScheduledTaskRow]):
        super().__init__(parent)
        self.setWindowTitle("📅 Calendario de ejecución de tareas")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(1060, 640)

        tema_oscuro = getattr(parent, "current_theme", "light") == "dark"
        self._colores = _paleta_calendario(tema_oscuro)

        v = QVBoxLayout(self)

        con_horario, sin_horario = self._separar_disparadores(tareas)
        con_horario.sort(key=lambda par: (par[1].hora, par[0].nombre.lower()))
        en_laboral = sum(1 for _, tr in con_horario if _disparador_en_horario_laboral(tr))

        if con_horario:
            resumen = QLabel(
                f"🕐 {en_laboral} de {len(con_horario)} disparadores con horario fijo caen en "
                f"horario laboral (L-V {HORA_INICIO_LABORAL:02d}:00-{HORA_FIN_LABORAL:02d}:00) "
                "— columnas de esa franja en negrita y sombreadas abajo."
            )
        else:
            resumen = QLabel("No hay disparadores con horario fijo que mostrar en la cuadrícula.")
        resumen.setWordWrap(True)
        v.addWidget(resumen)

        leyenda = QHBoxLayout()
        leyenda.addWidget(self._chip("En horario laboral", self._colores["laboral_bg"], self._colores["laboral_fg"]))
        leyenda.addWidget(self._chip("Fuera de horario laboral", self._colores["normal_bg"], self._colores["normal_fg"]))
        leyenda.addWidget(self._chip("Deshabilitada", self._colores["deshabilitada_bg"], self._colores["deshabilitada_fg"]))
        leyenda.addStretch()
        v.addLayout(leyenda)

        self.tabla = self._crear_tabla(con_horario)
        self.tabla.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabla.customContextMenuRequested.connect(self._ctx_tabla)
        v.addWidget(self.tabla, 3)

        if sin_horario:
            v.addWidget(QLabel(
                f"⏱️ Sin horario fijo ({len(sin_horario)}) — se disparan por evento, no por hora, "
                "y no aparecen en la cuadrícula:"))
            self.lista = QListWidget()
            self.lista.setMaximumHeight(140)
            for tarea, trigger in sin_horario:
                item = QListWidgetItem(f"{tarea.nombre} — {trigger.texto}")
                item.setData(Qt.UserRole, tarea)
                self.lista.addItem(item)
            self.lista.setContextMenuPolicy(Qt.CustomContextMenu)
            self.lista.customContextMenuRequested.connect(self._ctx_lista)
            v.addWidget(self.lista)

        botones = QDialogButtonBox(QDialogButtonBox.Close)
        botones.rejected.connect(self.reject)
        botones.accepted.connect(self.accept)
        v.addWidget(botones)

    @staticmethod
    def _separar_disparadores(tareas: List[ScheduledTaskRow]):
        con_horario: List[Tuple[ScheduledTaskRow, ScheduledTaskTrigger]] = []
        sin_horario: List[Tuple[ScheduledTaskRow, ScheduledTaskTrigger]] = []
        for tarea in tareas:
            for trigger in tarea.disparadores:
                (con_horario if trigger.tiene_horario_fijo else sin_horario).append((tarea, trigger))
        return con_horario, sin_horario

    def _chip(self, texto, bg, fg):
        lbl = QLabel("  " + texto + "  ")
        lbl.setStyleSheet(f"background:{bg.name()}; color:{fg.name()}; border-radius:4px; padding:2px 6px;")
        return lbl

    def _crear_tabla(self, filas):
        n_fijas = len(self.COLUMNAS_FIJAS)
        tabla = QTableWidget(len(filas), n_fijas + self.N_HORAS)
        # Sin icono/emoji delante del número de hora: probado en la práctica
        # que "hace que no se distinga bien el número" en una columna tan
        # estrecha — la franja laboral ya se marca con negrita + tooltip en
        # la cabecera y el sombreado de fondo en las celdas, es suficiente.
        headers = self.COLUMNAS_FIJAS + [f"{h:02d}" for h in range(self.N_HORAS)]
        tabla.setHorizontalHeaderLabels(headers)
        tabla.verticalHeader().setVisible(False)
        tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        tabla.setSelectionMode(QTableWidget.NoSelection)
        tabla.setAlternatingRowColors(False)
        tabla.setWordWrap(False)

        header = tabla.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, n_fijas):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        for col in range(n_fijas, tabla.columnCount()):
            header.setSectionResizeMode(col, QHeaderView.Fixed)
            tabla.setColumnWidth(col, 30)
            hora = col - n_fijas
            if HORA_INICIO_LABORAL <= hora < HORA_FIN_LABORAL:
                item_header = tabla.horizontalHeaderItem(col)
                item_header.setToolTip("Dentro de horario laboral (L-V)")
                fuente = item_header.font()
                fuente.setBold(True)
                item_header.setFont(fuente)

        for fila_idx, (tarea, trigger) in enumerate(filas):
            item_tarea = QTableWidgetItem(tarea.nombre)
            item_tarea.setData(Qt.UserRole, tarea)
            tabla.setItem(fila_idx, 0, item_tarea)
            tabla.setItem(fila_idx, 1, QTableWidgetItem(_texto_dias(trigger.dias_semana)))
            hora_txt = trigger.hora + (" 🔁" if trigger.repite_cada_min else "")
            tabla.setItem(fila_idx, 2, QTableWidgetItem(hora_txt))
            for col in range(n_fijas):
                tabla.item(fila_idx, col).setToolTip(trigger.texto)

            if not trigger.habilitado:
                bg, fg = self._colores["deshabilitada_bg"], self._colores["deshabilitada_fg"]
            elif _disparador_en_horario_laboral(trigger):
                bg, fg = self._colores["laboral_bg"], self._colores["laboral_fg"]
            else:
                bg, fg = self._colores["normal_bg"], self._colores["normal_fg"]

            for hora in _horas_cubiertas(trigger):
                celda = QTableWidgetItem("")
                celda.setBackground(bg)
                celda.setForeground(fg)
                celda.setToolTip(f"{tarea.nombre}\n{trigger.texto}")
                tabla.setItem(fila_idx, n_fijas + hora, celda)

        # Zona laboral sombreada de fondo en las columnas 08-18, incluso en
        # las celdas sin ninguna tarea, para que la franja se vea de un
        # vistazo aunque esa hora concreta esté vacía en esa fila.
        for fila_idx in range(len(filas)):
            for hora in range(HORA_INICIO_LABORAL, HORA_FIN_LABORAL):
                col = n_fijas + hora
                if tabla.item(fila_idx, col) is None:
                    vacio = QTableWidgetItem("")
                    vacio.setBackground(self._colores["zona_laboral_vacia"])
                    tabla.setItem(fila_idx, col, vacio)

        tabla.resizeRowsToContents()
        return tabla

    def _ctx_tabla(self, pos):
        idx = self.tabla.indexAt(pos)
        if not idx.isValid():
            return
        item = self.tabla.item(idx.row(), 0)
        tarea = item.data(Qt.UserRole) if item else None
        if tarea is None:
            return
        self._menu_contextual_tarea(tarea, self.tabla, pos)

    def _ctx_lista(self, pos):
        item = self.lista.itemAt(pos)
        tarea = item.data(Qt.UserRole) if item else None
        if tarea is None:
            return
        self._menu_contextual_tarea(tarea, self.lista, pos)

    def _menu_contextual_tarea(self, tarea, widget, pos):
        """Mismas opciones que el menú contextual de la tabla principal de
        Tareas Programadas (ver _ctx_tasks en scheduled_tasks.py) — se
        reutilizan directamente los métodos del padre pasándoles la tarea
        (todos aceptan row=None -> selección de la tabla principal, o una
        fila explícita como aquí), en vez de duplicar la lógica de
        habilitar/ejecutar/eliminar/ver acción. Compartido entre la
        cuadrícula y la lista de 'sin horario fijo' — 'widget' es el
        QTableWidget o QListWidget desde el que se pidió el menú (ambos son
        QAbstractItemView, ambos tienen .viewport())."""
        parent = self.parent()
        if parent is None:
            return

        deshabilitada = "deshabilitada" in (tarea.estado or "").strip().lower()

        menu = QMenu(self)
        act_toggle = menu.addAction("▶️ Habilitar" if deshabilitada else "⏸️ Deshabilitar")
        act_run = menu.addAction("🚀 Ejecutar ahora")
        menu.addSeparator()
        act_ver_accion = menu.addAction("📄 Ver acción completa")
        act_ver_accion.setEnabled(bool(tarea.accion))
        menu.addSeparator()
        act_eliminar = menu.addAction("✕ Eliminar tarea")
        menu.addSeparator()
        act_copy = menu.addAction("📋 Copiar nombre de la tarea")

        action = menu.exec_(widget.viewport().mapToGlobal(pos))
        if action == act_toggle:
            parent._habilitar_deshabilitar_tarea_seleccionada(tarea)
            self.accept()  # el estado mostrado en el calendario quedaría desactualizado
        elif action == act_run:
            parent._ejecutar_tarea_ahora_seleccionada(tarea)
        elif action == act_ver_accion:
            parent._ver_accion_tarea_seleccionada(tarea)
        elif action == act_eliminar:
            parent._eliminar_tarea_seleccionada(tarea)
            self.accept()  # la tarea eliminada seguiría apareciendo en el calendario
        elif action == act_copy:
            QApplication.clipboard().setText(tarea.nombre)
