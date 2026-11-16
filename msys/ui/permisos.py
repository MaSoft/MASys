# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: pestañas de permisos dentro de 📂 Carpetas Compartidas.

- 📁 Permisos de carpetas: leer un árbol local o de red (propietario, herencia
  y entradas), ver cada carpeta, editar permisos, cambiar el propietario y
  quitar una cuenta de toda una rama.
- ⚠️ Análisis de permisos: lo que conviene revisar (Todos con escritura,
  cuentas eliminadas, herencia cortada, denegar, sin acceso…).
- 🧭 Acceso de una cuenta: dónde entra un usuario o grupo y con qué permiso
  (por sus grupos, y por red si la carpeta está compartida).
- 💾 Copias de permisos: guardar, restaurar en el mismo servidor o en otro
  (equivalencias de cuentas), comparar copias y exportar para icacls.

Todo cambio: confirmación, copia previa automática del estado actual en
INFORMES\\PERMISOS y entrada en el historial de reparaciones.

Motor sin Qt: msys/domain/permisos.py."""

import os
import subprocess
from typing import List, Optional

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                             QMessageBox, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QTreeWidget,
                             QTreeWidgetItem, QVBoxLayout, QWidget)

from msys import config as msys_config
from msys.core import tareas
from msys.core.utils import dialogo_abrir_archivo, dialogo_elegir_carpeta, dialogo_guardar_archivo, is_admin
from msys.domain import permisos as dom
from msys.models.tabla_simple import (COLOR_APAGADO, COLOR_AVISO, COLOR_ERROR, COLOR_INFO, COLOR_OK, TablaSimple,
                                      objeto_seleccionado, objetos_seleccionados, proxy_para)
from msys.ui import piezas
from msys.widgets.delegates import _setup_header

SECCION = "📂 Carpetas Compartidas"
T_PERMISOS = "📂 Carpetas Compartidas · Permisos de carpetas"
T_ANALISIS = "📂 Carpetas Compartidas · Análisis de permisos"
T_ACCESO = "📂 Carpetas Compartidas · Acceso de una cuenta"
T_COPIAS = "📂 Carpetas Compartidas · Copias de permisos"
FILTROS = ["Todo", "Con permisos propios", "Herencia cortada", "Con avisos", "No se pudieron leer"]
NIVEL_COLOR = {dom.CRITICO: COLOR_ERROR, dom.AVISO: COLOR_AVISO, dom.INFO_: COLOR_INFO}
ESTADO_COLOR = {dom.CAMBIA: COLOR_AVISO, dom.NUEVA: COLOR_OK, dom.DESAPARECE: COLOR_ERROR, dom.NO_EXISTE: COLOR_APAGADO,
                dom.SOLO_DESTINO: COLOR_APAGADO, dom.ERROR: COLOR_ERROR}


def _sin_ayuda(dlg):
    dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)


def _texto_rel(rel: str) -> str:
    return rel or "(carpeta principal)"


def _tabla_editable(cabeceras: List[str], editable_col: int = -1) -> QTableWidget:
    t = QTableWidget(0, len(cabeceras))
    t.setHorizontalHeaderLabels(cabeceras)
    t.verticalHeader().setVisible(False)
    t.setAlternatingRowColors(True)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setWordWrap(False)
    t.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed if editable_col >= 0
                      else QAbstractItemView.NoEditTriggers)
    cab = t.horizontalHeader()
    cab.setSectionResizeMode(QHeaderView.Interactive)
    cab.setStretchLastSection(True)
    return t


def _celda(texto: str, editable: bool = False, color: Optional[str] = None) -> QTableWidgetItem:
    it = QTableWidgetItem(texto)
    if not editable:
        it.setFlags(it.flags() & ~Qt.ItemIsEditable)
    if color:
        it.setForeground(QColor(color))
    return it


# ── Diálogos ────────────────────────────────────────────────────────────────

class CuentaDialog(QDialog):
    """Pedir una cuenta o grupo y comprobar que existe en el equipo de la ruta."""

    def __init__(self, titulo: str, explicacion: str, ruta: str, parent=None, con_rama: bool = False, inicial: str = ""):
        super().__init__(parent)
        _sin_ayuda(self)
        self.setWindowTitle(titulo)
        self.resize(560, 220)
        self.servidor = dom.servidor_de(ruta)
        self.sid, self.nombre = "", ""
        lay = QVBoxLayout(self)
        lay.addWidget(piezas.nota(explicacion))
        self.txt = QLineEdit(inicial)
        self.txt.setPlaceholderText("Usuario o grupo: Administradores, DOMINIO\\usuario, DOMINIO\\Contabilidad…")
        self.txt.textChanged.connect(self._sin_comprobar)
        self.txt.returnPressed.connect(self._comprobar)
        lay.addLayout(piezas.fila(self.txt, piezas.boton("✔️ Comprobar", "ghost", self._comprobar), estirar=False))
        self.lbl = QLabel("")
        self.lbl.setWordWrap(True)
        lay.addWidget(self.lbl)
        self.chk_rama = QCheckBox("También todo lo que hay dentro (subcarpetas y todos los archivos)")
        self.chk_rama.setVisible(con_rama)
        lay.addWidget(self.chk_rama)
        lay.addStretch()
        self.btn_ok = piezas.boton("Aceptar", "primary", self._aceptar)
        lay.addLayout(piezas.fila(None, piezas.boton("Cancelar", "ghost", self.reject), self.btn_ok, estirar=False))

    def _sin_comprobar(self):
        self.sid = ""
        self.lbl.setText("")

    def _comprobar(self) -> bool:
        try:
            self.sid, self.nombre, tipo = dom.resolver_cuenta(self.txt.text(), self.servidor)
        except ValueError as e:
            self.sid = ""
            self.lbl.setText(f"<span style='color:{COLOR_ERROR}'>✕ {e}</span>")
            return False
        self.lbl.setText(f"<span style='color:{COLOR_OK}'>✔️ {self.nombre}</span> · {dom.TIPOS_CUENTA.get(tipo, '')}")
        return True

    def _aceptar(self):
        if self.sid or self._comprobar():
            self.accept()


class AnadirPermisoDialog(CuentaDialog):
    def __init__(self, ruta: str, carpeta: bool, parent=None):
        super().__init__("Añadir permiso", "Cuenta o grupo al que se da (o se deniega) el permiso.", ruta, parent)
        self.resize(620, 300)
        lay = self.layout()
        self.tipo = piezas.combo(["Permitir", "Denegar"])
        self.nivel = piezas.combo([n for n, _m in dom.NIVELES_EDITABLES])
        self.nivel.setCurrentIndex(1)
        self.aplica = piezas.combo([t for t, _f in dom.APLICA])
        self.aplica.setVisible(carpeta)
        filas = QHBoxLayout()
        filas.setSpacing(8)
        for w in (QLabel("Tipo:"), self.tipo, QLabel("Permiso:"), self.nivel):
            filas.addWidget(w)
        filas.addStretch()
        lay.insertLayout(3, filas)
        fila_aplica = QHBoxLayout()
        lbl = QLabel("Se aplica a:")
        lbl.setVisible(carpeta)
        fila_aplica.addWidget(lbl)
        fila_aplica.addWidget(self.aplica)
        fila_aplica.addStretch()
        lay.insertLayout(4, fila_aplica)
        self.nota_denegar = piezas.nota("Denegar gana a cualquier permiso, también al de los grupos: úsalo solo si no hay otra forma.")
        lay.insertWidget(5, self.nota_denegar)
        self.tipo.currentIndexChanged.connect(lambda i: self.nota_denegar.setVisible(i == 1))
        self.nota_denegar.setVisible(False)
        self._carpeta = carpeta

    def valores(self):
        nombre, m = dom.NIVELES_EDITABLES[self.nivel.currentIndex()]
        banderas = dom.APLICA[self.aplica.currentIndex()][1] if self._carpeta else ""
        if nombre.startswith("Mostrar el contenido"):
            banderas = "CI" if self._carpeta else ""
        return self.sid, self.tipo.currentIndex() == 0, m, banderas


class EditarPermisosDialog(QDialog):
    """Permisos de una carpeta o archivo: añadir, quitar, herencia. No escribe:
    deja en self.nuevo lo que hay que escribir y el host lo aplica."""

    def __init__(self, ruta: str, carpeta: bool, nombre_de, parent=None):
        super().__init__(parent)
        _sin_ayuda(self)
        self.setWindowTitle("Editar permisos")
        self.resize(1000, 560)
        self.ruta, self.carpeta, self._nombre_de = ruta, carpeta, nombre_de
        self.original = dom.leer_descriptor(ruta)
        self.nuevo = dom.Descriptor.desde_sddl(self.original.sddl())
        self.resetear_dentro = False
        lay = QVBoxLayout(self)
        self.lbl_ruta = QLabel(f"<b>{'📁' if carpeta else '📄'} {ruta}</b>")
        self.lbl_ruta.setTextFormat(Qt.RichText)
        self.lbl_ruta.setWordWrap(True)
        lay.addWidget(self.lbl_ruta)
        self.lbl_dueno = QLabel("")
        lay.addWidget(self.lbl_dueno)
        self.chk_heredar = QCheckBox("Heredar los permisos de la carpeta de arriba")
        self.chk_heredar.setChecked(not self.original.protegida)
        self.chk_heredar.clicked.connect(self._herencia)
        botones = [piezas.boton("➕ Añadir…", "primary", self._anadir), piezas.boton("✕ Quitar", "danger", self._quitar,
                                                                                   "Quita las entradas propias seleccionadas (las heredadas se cambian en la carpeta de arriba)")]
        lay.addLayout(piezas.fila(*botones, None, self.chk_heredar, estirar=False))
        self.modelo = TablaSimple(["Tipo", "Cuenta", "Permiso", "Se aplica a", "Origen"], color=self._color)
        self.tabla, self.proxy = piezas.tabla(self.modelo, self, columna_ancha=1, seleccion_multiple=True)
        lay.addWidget(self.tabla, 1)
        self.chk_dentro = QCheckBox("Que todo lo que hay dentro pierda sus permisos propios y herede de esta carpeta")
        self.chk_dentro.setToolTip("Como «Reemplazar todas las entradas de permisos de objetos secundarios» del Explorador")
        self.chk_dentro.setVisible(carpeta)
        lay.addWidget(self.chk_dentro)
        self.lbl_cambios = QLabel("")
        self.lbl_cambios.setObjectName("hintLabel")
        self.lbl_cambios.setWordWrap(True)
        lay.addWidget(self.lbl_cambios)
        lay.addLayout(piezas.fila(None, piezas.boton("Cancelar", "ghost", self.reject),
                                  piezas.boton("✔️ Aplicar", "primary", self._aplicar), estirar=False))
        self._pintar()

    @staticmethod
    def _color(e, col):
        if e.heredada:
            return COLOR_APAGADO
        if col == 0 and e.denegar:
            return COLOR_ERROR
        return None

    def _pintar(self):
        n = self._nombre_de
        filas = [["Permitir" if e.permitir else ("Denegar" if e.denegar else e.tipo), n(e.sid), dom.nombre_nivel(e.mascara),
                  dom.texto_aplica(e.banderas) if self.carpeta else "Este archivo",
                  "Heredada" if e.heredada else "Propia"] for e in self.nuevo.entradas]
        self.modelo.set_datos(filas, list(self.nuevo.entradas))
        self.lbl_dueno.setText(f"👤 Propietario: {n(self.nuevo.propietario)}   ·   "
                               + ("Herencia cortada" if self.nuevo.protegida else "Hereda de la carpeta de arriba"))
        cambios = dom.diferencias(self.original, self.nuevo, n)
        self.lbl_cambios.setText(("Cambios pendientes: " + " · ".join(cambios)) if cambios else "Sin cambios todavía.")

    def _anadir(self):
        dlg = AnadirPermisoDialog(self.ruta, self.carpeta, self)
        if dlg.exec_() != QDialog.Accepted:
            return
        sid, permitir, m, banderas = dlg.valores()
        self.nuevo = dom.con_entrada_nueva(self.nuevo, sid, permitir, m, banderas)
        self._pintar()

    def _quitar(self):
        sel = objetos_seleccionados(self.tabla, self.proxy, self.modelo)
        heredadas = [e for e in sel if e.heredada]
        propias = [e for e in sel if not e.heredada]
        if heredadas and not propias:
            QMessageBox.information(self, "Editar permisos", "Las entradas heredadas vienen de la carpeta de arriba: "
                                    "se quitan allí, o quitando la marca «Heredar…» de aquí.")
            return
        if propias:
            self.nuevo = dom.sin_entradas(self.nuevo, {e.clave() for e in propias})
            self._pintar()

    def _herencia(self, marcado: bool):
        if marcado:
            self.nuevo = dom.con_herencia(self.nuevo, True)
            self._pintar()
            return
        caja = QMessageBox(self)
        _sin_ayuda(caja)
        caja.setWindowTitle("Cortar la herencia")
        caja.setText("¿Qué hago con los permisos que ahora vienen de la carpeta de arriba?")
        copiar = caja.addButton("Convertirlos en propios", QMessageBox.YesRole)
        quitar = caja.addButton("Quitarlos", QMessageBox.NoRole)
        caja.addButton("Cancelar", QMessageBox.RejectRole)
        caja.exec_()
        if caja.clickedButton() is copiar:
            self.nuevo = dom.con_herencia(self.nuevo, False, True)
        elif caja.clickedButton() is quitar:
            self.nuevo = dom.con_herencia(self.nuevo, False, False)
        else:
            self.chk_heredar.setChecked(True)
            return
        self._pintar()

    def _aplicar(self):
        self.resetear_dentro = self.chk_dentro.isChecked()
        if self.nuevo.clave() == self.original.clave() and not self.resetear_dentro:
            self.reject()
            return
        if not self.nuevo.entradas and not self.nuevo.sin_dacl:
            caja = QMessageBox(self)
            _sin_ayuda(caja)
            caja.setIcon(QMessageBox.Warning)
            caja.setWindowTitle("Editar permisos")
            caja.setText("Así nadie tendrá acceso (solo el propietario podrá volver a cambiar los permisos). ¿Seguir?")
            si = caja.addButton("Sí", QMessageBox.YesRole)
            caja.addButton("No", QMessageBox.NoRole)
            caja.exec_()
            if caja.clickedButton() is not si:
                return
        self.accept()


class EquivalenciasDialog(QDialog):
    """Qué cuenta del destino corresponde a cada cuenta de la copia."""

    def __init__(self, copia: dom.Lectura, raiz_destino: str, actuales: Optional[List[dom.Equivalencia]], parent=None):
        super().__init__(parent)
        _sin_ayuda(self)
        self.setWindowTitle("Equivalencias de cuentas")
        self.resize(1100, 640)
        self.copia, self.raiz_destino = copia, raiz_destino
        self.equivalencias: List[dom.Equivalencia] = list(actuales or [])
        lay = QVBoxLayout(self)
        lay.addWidget(piezas.nota(
            "Las cuentas de Windows (Todos, Administradores, SYSTEM…) valen en cualquier equipo. Las de un dominio o de un "
            "equipo se buscan por nombre en el destino. Cambia aquí el dominio (o el nombre del servidor, para sus cuentas "
            "locales) y pulsa «Buscar». Doble clic en «Cuenta en el destino» para escribir otra a mano; si se deja vacía, "
            "esa entrada se quita al restaurar."))
        self.dominios = _tabla_editable(["En la copia", "En el destino (doble clic para cambiar)"], editable_col=1)
        destino_equipo = dom.equipo_de(raiz_destino)
        for d in dom.dominios_de(copia):
            fila = self.dominios.rowCount()
            self.dominios.insertRow(fila)
            self.dominios.setItem(fila, 0, _celda(d))
            self.dominios.setItem(fila, 1, _celda(destino_equipo if d.upper() == copia.equipo.upper() else d, editable=True))
        self.dominios.setMaximumHeight(110)
        self.dominios.resizeColumnsToContents()
        lay.addWidget(self.dominios)
        self.btn_buscar = piezas.boton("🔎 Buscar las cuentas en el destino", "primary", self._buscar)
        self.lbl = QLabel("")
        lay.addLayout(piezas.fila(self.btn_buscar, self.lbl))
        self.cuentas = _tabla_editable(["Cuenta en la copia", "Tipo", "Cuenta en el destino", "Estado"], editable_col=2)
        self.cuentas.itemChanged.connect(self._editada)
        lay.addWidget(self.cuentas, 1)
        lay.addLayout(piezas.fila(None, piezas.boton("Cancelar", "ghost", self.reject),
                                  piezas.boton("✔️ Usar estas equivalencias", "primary", self._aceptar), estirar=False))
        if self.equivalencias:
            self._pintar()
        else:
            QTimer.singleShot(0, self._buscar)

    def _mapa_dominios(self):
        return {self.dominios.item(f, 0).text(): self.dominios.item(f, 1).text().strip()
                for f in range(self.dominios.rowCount())}

    def _buscar(self):
        self.btn_buscar.setEnabled(False)
        self.lbl.setText("⏳ Buscando las cuentas…")
        tareas.lanzar(self, "_tarea", dom.proponer_equivalencias, self.copia, self.raiz_destino, self._mapa_dominios(),
                      al_terminar=self._buscadas, al_fallar=lambda m: (self.btn_buscar.setEnabled(True), self.lbl.setText(f"✕ {m}")))

    def _buscadas(self, eq):
        self.btn_buscar.setEnabled(True)
        self.equivalencias = eq
        self._pintar()

    def _pintar(self):
        self.cuentas.blockSignals(True)
        self.cuentas.setRowCount(0)
        colores = {dom.EQ_NO_ENCONTRADA: COLOR_ERROR, dom.EQ_TRADUCIDA: COLOR_INFO, dom.EQ_ELIMINADA: COLOR_AVISO,
                   dom.EQ_IGUAL: COLOR_OK, dom.EQ_UNIVERSAL: COLOR_APAGADO}
        for e in self.equivalencias:
            f = self.cuentas.rowCount()
            self.cuentas.insertRow(f)
            self.cuentas.setItem(f, 0, _celda(e.nombre_origen))
            self.cuentas.setItem(f, 1, _celda(dom.TIPOS_CUENTA.get(e.tipo, "")))
            self.cuentas.setItem(f, 2, _celda(e.nombre_destino, editable=e.estado != dom.EQ_UNIVERSAL))
            self.cuentas.setItem(f, 3, _celda(e.estado, color=colores.get(e.estado)))
        self.cuentas.resizeColumnsToContents()
        self.cuentas.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.cuentas.blockSignals(False)
        quitan = sum(1 for e in self.equivalencias if e.estado == dom.EQ_NO_ENCONTRADA)
        traducidas = sum(1 for e in self.equivalencias if e.estado == dom.EQ_TRADUCIDA)
        self.lbl.setText(f"<span>{len(self.equivalencias)} cuentas · {traducidas} traducidas</span>"
                         + (f" · <span style='color:{COLOR_ERROR}'>{quitan} no existen en el destino</span>" if quitan else ""))

    def _editada(self, item):
        if item.column() != 2:
            return
        e = self.equivalencias[item.row()]
        nueva = dom.buscar_equivalencia(e.sid_origen, e.nombre_origen, e.tipo, item.text(), dom.servidor_de(self.raiz_destino))
        if nueva.estado == dom.EQ_NO_ENCONTRADA and item.text().strip():
            nueva.estado = "No existe esa cuenta: se quitará"
        self.equivalencias[item.row()] = nueva
        self._pintar()

    def _aceptar(self):
        if not self.equivalencias:
            return
        self.accept()


class RestaurarDialog(QDialog):
    """Comparar una copia con lo que hay ahora y restaurar todo o una parte."""

    def __init__(self, host, copia: dom.Lectura, ruta_copia: str, parent=None):
        super().__init__(parent)
        _sin_ayuda(self)
        self.setWindowTitle("Restaurar permisos")
        self.resize(1250, 720)
        self.host, self.copia, self.ruta_copia = host, copia, ruta_copia
        self.equivalencias: Optional[List[dom.Equivalencia]] = None
        self.difs: List[dom.Diferencia] = []
        self.recursos: List[dom.CambioRecurso] = []
        lay = QVBoxLayout(self)
        tipo = f"copia previa a «{copia.origen}»" if copia.parcial else "copia completa"
        lay.addWidget(QLabel(f"<b>💾 {os.path.basename(ruta_copia)}</b> · {tipo} · {copia.equipo} · "
                             f"{_fecha(copia.fecha)} · {len(copia.elementos)} elementos de {copia.raiz}"))
        self.txt_destino = QLineEdit(copia.raiz)
        self.txt_destino.setPlaceholderText("Carpeta donde se restaura (la misma u otra, local o \\\\servidor\\recurso)")
        self.txt_rama = QLineEdit()
        self.txt_rama.setPlaceholderText("Solo esta subcarpeta (vacío = todo), p. ej. Contabilidad\\2025")
        self.txt_rama.setMinimumWidth(260)
        self.btn_eq = piezas.boton("🔀 Equivalencias de cuentas…", "ghost", self._equivalencias,
                                   "Para restaurar en otro servidor o dominio")
        self.btn_comparar = piezas.boton("🔍 Comparar con lo actual", "primary", self._comparar)
        lay.addLayout(piezas.fila(QLabel("Destino:"), self.txt_destino,
                                  piezas.boton("📂", "ghost", self._elegir_destino), estirar=False))
        lay.addLayout(piezas.fila(QLabel("Rama:"), self.txt_rama, self.btn_eq, self.btn_comparar))
        self.lbl_eq = QLabel("")
        self.lbl_eq.setObjectName("hintLabel")
        lay.addWidget(self.lbl_eq)
        self.tabs = piezas.pestanas()
        self.modelo = TablaSimple(["Estado", "Ruta", "Qué cambia"], color=lambda d, c: ESTADO_COLOR.get(d.estado) if c == 0 else None,
                                  tooltip=lambda d, c: "\n".join(d.detalle) if c == 2 else None)
        self.tabla, self.proxy = piezas.tabla(self.modelo, self, columna_ancha=2, seleccion_multiple=True)
        self.tabs.addTab(self.tabla, "📁 Carpetas y archivos")
        pag = QWidget()
        lp = QVBoxLayout(pag)
        self.modelo_rec = TablaSimple(["Estado", "Recurso compartido", "Qué cambia"],
                                      color=lambda d, c: ESTADO_COLOR.get(d.estado) if c == 0 else None)
        self.tabla_rec, _p = piezas.tabla(self.modelo_rec, self, columna_ancha=2)
        lp.addWidget(self.tabla_rec, 1)
        self.chk_crear = QCheckBox("Crear los recursos compartidos que no existan en el destino")
        lp.addWidget(self.chk_crear)
        self.tabs.addTab(pag, "🌐 Permisos de red")
        lay.addWidget(self.tabs, 1)
        self.lbl_estado = QLabel("Pulsa «Comparar con lo actual»: no se cambia nada hasta que pulses Restaurar.")
        lay.addWidget(self.lbl_estado)
        self.btn_sel = piezas.boton("♻️ Restaurar lo seleccionado", "ghost", lambda: self._restaurar(True))
        self.btn_todo = piezas.boton("♻️ Restaurar todo", "danger", lambda: self._restaurar(False))
        self.btn_sel.setEnabled(False)
        self.btn_todo.setEnabled(False)
        lay.addLayout(piezas.fila(None, piezas.boton("Cerrar", "ghost", self.reject), self.btn_sel, self.btn_todo, estirar=False))
        self.txt_destino.textChanged.connect(self._sin_comparar)
        self.txt_rama.textChanged.connect(self._sin_comparar)
        self._pintar_eq()

    def _sin_comparar(self):
        self.difs, self.recursos = [], []
        self.modelo.set_datos([])
        self.modelo_rec.set_datos([])
        self.btn_sel.setEnabled(False)
        self.btn_todo.setEnabled(False)
        self._pintar_eq()

    def _destino(self) -> str:
        return dom.normalizar_raiz(self.txt_destino.text())

    def _otro_equipo(self) -> bool:
        return dom.equipo_de(self._destino()).upper() != (self.copia.equipo or "").upper()

    def _pintar_eq(self):
        if self.equivalencias is None:
            self.lbl_eq.setText("Destino en otro equipo: se buscarán las cuentas equivalentes al comparar (revísalas con 🔀)."
                                if self._otro_equipo() else "Mismo equipo: las cuentas son las mismas.")
            return
        quitan = sum(1 for e in self.equivalencias if not e.sid_destino)
        trad = sum(1 for e in self.equivalencias if e.sid_destino and dom.sid_canonico(e.sid_destino) != dom.sid_canonico(e.sid_origen))
        self.lbl_eq.setText(f"Equivalencias: {trad} cuentas traducidas" + (f", {quitan} sin equivalente (sus entradas se quitan)" if quitan else ""))

    def _elegir_destino(self):
        c = dialogo_elegir_carpeta(self, "Carpeta donde se restaura", self.txt_destino.text())
        if c:
            self.txt_destino.setText(os.path.normpath(c))

    def _equivalencias(self) -> bool:
        dlg = EquivalenciasDialog(self.copia, self._destino(), self.equivalencias, self)
        if dlg.exec_() == QDialog.Accepted:
            self.equivalencias = dlg.equivalencias
            self._sin_comparar()
            return True
        return False

    def _ocupado(self, si: bool):
        for b in (self.btn_comparar, self.btn_eq, self.btn_sel, self.btn_todo):
            b.setEnabled(not si)

    def _comparar(self):
        destino = self._destino()
        if not destino:
            return
        if self.equivalencias is None and self._otro_equipo() and not self._equivalencias():
            return
        self._ocupado(True)
        self.lbl_estado.setText("⏳ Leyendo lo que hay ahora en el destino…")
        eq = self.equivalencias or []
        rama = self.txt_rama.text().strip().strip("\\")

        def trabajo():
            difs, _actual = dom.planificar(self.copia, destino, eq, rama,
                                           progreso=lambda n, tx: t.progreso.emit(n, tx), parar=t.debe_parar)
            rec, aviso = dom.planificar_recursos(self.copia, destino, eq) if not rama else ([], "")
            return difs, rec, aviso
        t = tareas.Tarea(trabajo)
        t.progreso.connect(lambda n, tx: self.lbl_estado.setText(f"⏳ {tx}"))
        t.ok.connect(self._comparado)
        t.error.connect(lambda m: (self._ocupado(False), self.lbl_estado.setText(f"✕ {m}")))
        self._tarea = t
        t.start()

    def _comparado(self, res):
        self._ocupado(False)
        self.difs, self.recursos, aviso = res
        self.modelo.set_datos([[d.estado, _texto_rel(d.rel), " · ".join(d.detalle)] for d in self.difs], self.difs)
        self.modelo_rec.set_datos([[c.estado, c.nombre, " · ".join(c.detalle)] for c in self.recursos], self.recursos)
        cambian = sum(1 for d in self.difs if d.aplicable)
        faltan = sum(1 for d in self.difs if d.estado == dom.NO_EXISTE)
        texto = (f"{cambian} con diferencias que se pueden restaurar" + (f" · {faltan} no existen en el destino" if faltan else "")
                 + (f" · {len(self.recursos)} recursos compartidos distintos" if self.recursos else ""))
        if not cambian and not self.recursos:
            texto = "✅ El destino ya está como en la copia."
        self.lbl_estado.setText(texto + (f"\n⚠️ {aviso}" if aviso else ""))
        self.btn_sel.setEnabled(cambian > 0)
        self.btn_todo.setEnabled(cambian > 0 or bool(self.recursos))

    def _restaurar(self, solo_seleccion: bool):
        difs = [d for d in (objetos_seleccionados(self.tabla, self.proxy, self.modelo) if solo_seleccion else self.difs) if d.aplicable]
        recursos = [] if solo_seleccion else [c for c in self.recursos if c.estado == dom.CAMBIA
                                                or (c.estado == dom.NO_EXISTE and self.chk_crear.isChecked())]
        if not difs and not recursos:
            self.host._avisar("Restaurar permisos", "Selecciona filas con diferencias («Cambia»).")
            return
        destino = self._destino()
        texto = (f"Se restauran los permisos de {len(difs)} carpeta(s) o archivo(s)"
                 + (f" y de {len(recursos)} recurso(s) compartido(s)" if recursos else "") + f" en:\n\n{destino}\n\n"
                 "Antes se guarda una copia de cómo están ahora (en INFORMES\\PERMISOS). Windows recalcula lo heredado de lo "
                 "que hay dentro: en carpetas muy grandes puede tardar.\n\n¿Continuar?")
        if not self.host._confirmar_accion("Restaurar permisos", texto):
            return
        self._ocupado(True)
        crear = self.chk_crear.isChecked()

        def trabajo():
            t.progreso.emit(0, "Guardando cómo está ahora…")
            previa = dom.copia_previa(destino, [d.rel for d in difs], f"Restaurar {os.path.basename(self.ruta_copia)}",
                                      recursos=[c.nombre for c in recursos if c.estado == dom.CAMBIA])
            res = dom.aplicar(destino, difs, progreso=lambda n, tx: t.progreso.emit(n, f"Restaurando {n} de {len(difs)}: {tx}"))
            res_rec = dom.aplicar_recursos(destino, recursos, crear) if recursos else []
            return previa, res, res_rec
        t = tareas.Tarea(trabajo)
        t.progreso.connect(lambda n, tx: self.lbl_estado.setText(f"⏳ {tx}"))
        t.ok.connect(lambda r: self._restaurado(destino, r))
        t.error.connect(lambda m: (self._ocupado(False), self.host._avisar("Restaurar permisos", f"No se pudo:\n\n{m}", QMessageBox.Warning)))
        self._tarea = t
        t.start()

    def _restaurado(self, destino, r):
        previa, res, res_rec = r
        mal = [(rel, m) for rel, ok, m in res if not ok] + [(n, m) for n, ok, m in res_rec if not ok]
        avisos = [(rel, m) for rel, ok, m in res if ok and m]
        bien = sum(1 for _r, ok, _m in res if ok) + sum(1 for _n, ok, _m in res_rec if ok)
        self.host.repair_audit_db.registrar(seccion=SECCION, accion="Restaurar permisos", objetivo=destino, exito=not mal,
                                            detalle=f"Copia {os.path.basename(self.ruta_copia)}: {bien} bien, {len(mal)} mal",
                                            backup_realizado=True, backup_ruta=previa, tipo_operacion="RESTORE")
        texto = f"Restaurados: {bien}.\n\nCopia de cómo estaba antes:\n{previa}"
        if mal:
            texto += f"\n\nNo se pudo ({len(mal)}):\n" + "\n".join(f"• {_texto_rel(a)}: {m}" for a, m in mal[:15])
        if avisos:
            texto += "\n\nOjo:\n" + "\n".join(f"• {_texto_rel(a)}: {m}" for a, m in avisos[:10])
        self.host._avisar("Restaurar permisos", texto, QMessageBox.Warning if mal else QMessageBox.Information)
        self._comparar()


def _fecha(iso: str) -> str:
    """2026-10-22T10:15:00 → 22/10/2026 10:15"""
    if not iso or len(iso) < 16:
        return iso or ""
    return f"{iso[8:10]}/{iso[5:7]}/{iso[0:4]} {iso[11:16]}"


# ── Pestañas ────────────────────────────────────────────────────────────────

class PermisosMixin:
    def crear_pestanas_permisos(self, contenedor):
        self._perm_lec: Optional[dom.Lectura] = None
        self._perm_viendo_copia = ""
        self._perm_hallazgos: List[dom.Hallazgo] = []
        self._perm_avisos_rel = {}
        contenedor.addTab(self._perm_tab_carpetas(), "📁 Permisos de carpetas")
        contenedor.addTab(self._perm_tab_analisis(), "⚠️ Análisis de permisos")
        contenedor.addTab(self._perm_tab_acceso(), "🧭 Acceso de una cuenta")
        contenedor.addTab(self._perm_tab_copias(), "💾 Copias de permisos")
        self.registrar_export(T_PERMISOS, self._perm_export)

    # ── 📁 Permisos de carpetas ──
    def _perm_tab_carpetas(self):
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setSpacing(8)
        lay.setContentsMargins(10, 10, 10, 10)
        self.perm_ruta = QLineEdit()
        self.perm_ruta.setPlaceholderText("Carpeta: D:\\Datos, H:\\ o \\\\Servidor\\Recurso")
        self.perm_ruta.setText(self.settings.value("permisos_ruta", "", type=str) or "")
        self.perm_ruta.returnPressed.connect(self._perm_leer)
        self.perm_archivos = piezas.combo(dom.TEXTO_ARCHIVOS)
        self.perm_archivos.setCurrentIndex(dom.ARCHIVOS_PROPIOS)
        self.perm_archivos.setToolTip("Los archivos que solo heredan no aportan nada; leer todos sirve para ver sus propietarios")
        self.perm_prof = QSpinBox()
        self.perm_prof.setRange(1, 99)
        self.perm_prof.setValue(99)
        self.perm_prof.setToolTip("Niveles de subcarpetas (1 = solo la carpeta indicada)")
        self.perm_prof.setFixedWidth(60)
        self.perm_ocultas = QCheckBox("Ocultas")
        self.perm_ocultas.setChecked(True)
        self.perm_btn_leer = piezas.boton("🔍 Leer permisos", "success", self._perm_leer)
        self.perm_btn_parar = piezas.boton("⏹️ Parar", "danger", self._perm_parar)
        self.perm_btn_parar.setEnabled(False)
        lay.addLayout(piezas.fila(self.perm_ruta, piezas.boton("📂", "ghost", self._perm_elegir), self.perm_archivos,
                                  QLabel("Profundidad:"), self.perm_prof, self.perm_ocultas, self.perm_btn_leer,
                                  self.perm_btn_parar, estirar=False))
        self.perm_filtro = piezas.combo(FILTROS)
        self.perm_filtro.currentIndexChanged.connect(lambda _i: self._perm_pintar_arbol())
        self.perm_btn_editar = piezas.boton("✏️ Editar permisos…", "primary", self._perm_editar)
        self.perm_btn_dueno = piezas.boton("👤 Propietario…", "ghost", self._perm_propietario)
        self.perm_btn_quitar = piezas.boton("✕ Quitar una cuenta de la rama…", "danger", self._perm_quitar_cuenta,
                                            "Quita las entradas propias de una cuenta en la carpeta y en todo lo leído de dentro")
        fila, self.perm_busqueda, _a, self.perm_recuento = piezas.barra_superior(
            "🔍 Buscar carpeta, propietario o cuenta...", [self.perm_filtro, self.perm_btn_editar, self.perm_btn_dueno,
                                                           self.perm_btn_quitar], None)
        self._perm_timer = QTimer(self)
        self._perm_timer.setSingleShot(True)
        self._perm_timer.setInterval(300)
        self._perm_timer.timeout.connect(self._perm_pintar_arbol)
        self.perm_busqueda.textChanged.connect(lambda _t: self._perm_timer.start())
        lay.addLayout(fila)
        self.perm_estado = piezas.nota("Lee una carpeta local o de red. Con MASys como administrador se leen también las "
                                       "carpetas que no dan acceso a los administradores.")
        lay.addWidget(self.perm_estado)
        split = QSplitter(Qt.Horizontal)
        self.perm_arbol = QTreeWidget()
        self.perm_arbol.setHeaderLabels(["Carpeta / archivo", "Propietario", "Herencia", "Propios", "Avisos"])
        self.perm_arbol.setAlternatingRowColors(True)
        self.perm_arbol.setFont(QFont("Segoe UI", 9))
        _setup_header(self.perm_arbol, 5, stretch_col=0)
        self.perm_arbol.currentItemChanged.connect(lambda _a, _b: self._perm_ver_detalle())
        self.perm_arbol.itemDoubleClicked.connect(lambda _i, _c: self._perm_editar())
        split.addWidget(self.perm_arbol)
        der = QWidget()
        ld = QVBoxLayout(der)
        ld.setContentsMargins(0, 0, 0, 0)
        self.perm_lbl_det = QLabel("Elige una carpeta.")
        self.perm_lbl_det.setWordWrap(True)
        self.perm_lbl_det.setTextFormat(Qt.RichText)
        ld.addWidget(self.perm_lbl_det)
        self.perm_det_modelo = TablaSimple(["Tipo", "Cuenta", "Permiso", "Se aplica a", "Origen"],
                                           color=EditarPermisosDialog._color)
        self.perm_det_tabla, _p = piezas.tabla(self.perm_det_modelo, self, columna_ancha=1)
        ld.addWidget(self.perm_det_tabla, 1)
        split.addWidget(der)
        split.setSizes([700, 600])
        lay.addWidget(split, 1)
        # Tabla plana (no visible) para la barra de exportación
        self.perm_plano = TablaSimple(["Ruta", "Tipo", "Propietario", "Herencia", "Cuenta", "Permitir/Denegar", "Permiso",
                                       "Se aplica a", "Origen"])
        self.perm_plano_proxy = proxy_para(self.perm_plano, self)
        self._perm_acciones = [self.perm_btn_editar, self.perm_btn_dueno, self.perm_btn_quitar]
        self._perm_botones = [self.perm_btn_leer] + self._perm_acciones
        self._perm_estado_botones()
        return tab

    def _perm_elegir(self):
        c = dialogo_elegir_carpeta(self, "Carpeta de la que leer los permisos", self.perm_ruta.text())
        if c:
            self.perm_ruta.setText(os.path.normpath(c))

    def _perm_estado_botones(self):
        vivo = self._perm_lec is not None and not self._perm_viendo_copia
        for b in self._perm_acciones:
            b.setEnabled(vivo)
            b.setToolTip(b.toolTip() if vivo else "Lee una carpeta primero (con una copia abierta no se cambia nada)")

    def _perm_ocupado(self, si: bool):
        for b in self._perm_botones + getattr(self, "_perm_botones_copias", []) + [getattr(self, "perm_btn_consultar", None)]:
            if b is not None:
                b.setEnabled(not si)
        self.perm_btn_parar.setEnabled(si)
        if not si:
            self._perm_estado_botones()

    def _perm_conectar(self, ruta: str) -> bool:
        """Con «👤 Otro Usuario» configurado, abre la sesión con el servidor con esas credenciales."""
        creds = msys_config.ALTERNATE_CREDENTIALS
        if not creds or not dom.servidor_de(ruta):
            return True
        motivo = dom.conectar_con_credenciales(ruta, creds[0], creds[1])
        if motivo:
            self._avisar("Permisos", motivo, QMessageBox.Warning)
            return False
        return True

    def _perm_leer(self):
        ruta = dom.normalizar_raiz(self.perm_ruta.text())
        if not ruta:
            self._avisar("Permisos", "Escribe o elige una carpeta.")
            return
        if not self._perm_conectar(ruta):
            return
        self.settings.setValue("permisos_ruta", ruta)
        self._perm_ocupado(True)
        self.perm_estado.setText("⏳ Leyendo…")
        archivos, prof, ocultas = self.perm_archivos.currentIndex(), self.perm_prof.value(), self.perm_ocultas.isChecked()
        t = tareas.Tarea(lambda: dom.leer_arbol(ruta, prof, archivos, ocultas, progreso=lambda n, tx: t.progreso.emit(n, tx),
                                                parar=t.debe_parar))
        t.progreso.connect(lambda n, tx: self.perm_estado.setText(f"⏳ {n} elementos · {tx}"))
        t.ok.connect(lambda lec: self._perm_cargar(lec, ""))
        t.error.connect(lambda m: (self._perm_ocupado(False), self.perm_estado.setText(f"✕ {m}"),
                                   self._avisar("Permisos", m, QMessageBox.Warning)))
        self._perm_tarea = t
        t.start()

    def _perm_parar(self):
        t = getattr(self, "_perm_tarea", None)
        if t is not None and t.isRunning():
            t.parar()
            self.perm_estado.setText("⏳ Parando…")

    def _perm_cargar(self, lec: dom.Lectura, copia: str):
        self._perm_ocupado(False)
        self._perm_lec, self._perm_viendo_copia = lec, copia
        self._perm_hallazgos = dom.analizar(lec)
        self._perm_avisos_rel = {}
        for h in self._perm_hallazgos:
            if h.nivel != dom.INFO_:
                clave = "" if h.rel == "(carpeta principal)" else h.rel.lower()
                self._perm_avisos_rel[clave] = self._perm_avisos_rel.get(clave, 0) + 1
        self._perm_estado_botones()
        carpetas = sum(1 for e in lec.elementos if e.carpeta)
        partes = [f"{'💾 Copia ' + os.path.basename(copia) + ' · ' if copia else ''}{lec.raiz} en {lec.equipo}",
                  f"{carpetas} carpetas y {len(lec.elementos) - carpetas} archivos con permisos propios"
                  if lec.archivos == dom.ARCHIVOS_PROPIOS else f"{len(lec.elementos)} elementos"]
        if lec.recursos:
            partes.append(f"{len(lec.recursos)} recurso(s) compartido(s): " + ", ".join(r.nombre for r in lec.recursos))
        if lec.detenida:
            partes.append("⚠️ PARADO a medias: faltan carpetas")
        if lec.aviso_recursos:
            partes.append("⚠️ " + lec.aviso_recursos)
        if lec.sin_listar:
            partes.append(f"⚠️ {len(lec.sin_listar)} carpeta(s) sin poder ver su contenido")
        self.perm_estado.setText(" · ".join(partes))
        self._perm_pintar_arbol()
        self._perm_pintar_plano()
        self._perm_pintar_analisis()
        self.set_status(f"✅ Permisos leídos: {len(lec.elementos)} elementos, {len(self._perm_hallazgos)} cosas a revisar")
        piezas.refrescar_export(self)

    def _perm_filtra(self, e: dom.Elemento, f: int, texto: str) -> bool:
        lec = self._perm_lec
        if f == 1 and not (e.sddl and e.descriptor.tiene_propios):
            return False
        if f == 2 and not (e.sddl and e.descriptor.protegida):
            return False
        if f == 3 and not self._perm_avisos_rel.get(e.rel.lower()):
            return False
        if f == 4 and not e.error and e.rel not in lec.sin_listar:
            return False
        if texto:
            if texto in e.rel.lower():
                return True
            if not e.sddl:
                return False
            d = e.descriptor
            nombres = [lec.nombre(d.propietario)] + [lec.nombre(x.sid) for x in d.propias]
            return any(texto in n.lower() for n in nombres)
        return True

    def _perm_pintar_arbol(self):
        arbol = self.perm_arbol
        arbol.setUpdatesEnabled(False)
        arbol.clear()
        lec = self._perm_lec
        if lec is None:
            arbol.setUpdatesEnabled(True)
            return
        f, texto = self.perm_filtro.currentIndex(), self.perm_busqueda.text().strip().lower()
        incluir = set()
        visibles = 0
        for e in lec.elementos:
            if self._perm_filtra(e, f, texto):
                visibles += 1
                r = e.rel.lower()
                incluir.add(r)
                while r:   # y sus carpetas de arriba, para que se vea dónde está
                    r = r.rpartition("\\")[0]
                    incluir.add(r)
        items = {}
        nombre = lec.nombre
        fuente_neg = QFont("Segoe UI", 9)
        fuente_neg.setBold(True)
        for e in sorted(lec.elementos, key=lambda x: (x.nivel, x.rel.lower())):
            clave = e.rel.lower()
            if clave not in incluir:
                continue
            padre = items.get(clave.rpartition("\\")[0]) if e.rel else None
            it = QTreeWidgetItem(padre if padre is not None else arbol)
            nom = os.path.basename(e.rel) if e.rel else (os.path.basename(lec.raiz.rstrip("\\")) or lec.raiz)
            it.setText(0, ("📁 " if e.carpeta else "📄 ") + nom)
            it.setToolTip(0, lec.ruta(e.rel))
            it.setData(0, Qt.UserRole, e.rel)
            if e.error:
                it.setText(1, f"✕ {e.error}")
                it.setForeground(1, QColor(COLOR_ERROR))
            elif e.sddl:
                d = e.descriptor
                it.setText(1, nombre(d.propietario))
                if lec.huerfana(d.propietario):
                    it.setForeground(1, QColor(COLOR_AVISO))
                it.setText(2, "Cortada" if d.protegida else "Hereda")
                if d.protegida:
                    it.setForeground(2, QColor(COLOR_INFO))
                n = len(d.propias)
                it.setText(3, str(n) if n else "—")
                if n:
                    it.setFont(0, fuente_neg)
            avisos = self._perm_avisos_rel.get(clave, 0)
            if avisos:
                it.setText(4, f"⚠️ {avisos}")
                it.setForeground(4, QColor(COLOR_AVISO))
            items[clave] = it
        raiz = items.get("")
        if raiz is not None:
            raiz.setExpanded(True)
        for col in range(1, 5):   # que propietario, herencia… se lean enteros
            arbol.resizeColumnToContents(col)
        if (texto or f) and visibles <= 2000:
            arbol.expandAll()
        arbol.setUpdatesEnabled(True)
        self.perm_recuento.setText(f"{visibles} de {len(lec.elementos)}")
        if raiz is not None and arbol.currentItem() is None:
            arbol.setCurrentItem(raiz)

    def _perm_elemento_actual(self) -> Optional[dom.Elemento]:
        it = self.perm_arbol.currentItem()
        if it is None or self._perm_lec is None:
            return None
        return self._perm_lec.por_rel().get((it.data(0, Qt.UserRole) or "").lower())

    def _perm_ver_detalle(self):
        e = self._perm_elemento_actual()
        lec = self._perm_lec
        if e is None:
            self.perm_det_modelo.set_datos([])
            return
        ruta = lec.ruta(e.rel)
        if e.error:
            self.perm_lbl_det.setText(f"<b>{ruta}</b><br><span style='color:{COLOR_ERROR}'>✕ {e.error}</span>")
            self.perm_det_modelo.set_datos([])
            return
        d = e.descriptor
        avisos = [h for h in self._perm_hallazgos if (h.rel if h.rel != "(carpeta principal)" else "").lower() == e.rel.lower()]
        texto = (f"<b>{ruta}</b><br>👤 Propietario: {lec.nombre(d.propietario)} · "
                 + ("<span style='color:%s'>Herencia cortada</span>" % COLOR_INFO if d.protegida else "Hereda de la carpeta de arriba"))
        for h in avisos[:6]:
            texto += f"<br><span style='color:{NIVEL_COLOR.get(h.nivel)}'>{h.nivel}: {h.tipo}</span> — {h.que_hacer}"
        self.perm_lbl_det.setText(texto)
        filas = [["Permitir" if x.permitir else ("Denegar" if x.denegar else x.tipo), lec.nombre(x.sid), dom.nombre_nivel(x.mascara),
                  dom.texto_aplica(x.banderas) if e.carpeta else "Este archivo", "Heredada" if x.heredada else "Propia"]
                 for x in d.entradas]
        if d.sin_dacl:
            filas = [["Permitir", "Todos", "Control total", "Sin lista de permisos", "Propia"]]
        self.perm_det_modelo.set_datos(filas, list(d.entradas) or [dom.Entrada("A", "", "FA", dom.TODOS)])

    def _perm_pintar_plano(self):
        lec = self._perm_lec
        filas = []
        for e in lec.elementos:
            ruta = lec.ruta(e.rel)
            tipo = "Carpeta" if e.carpeta else "Archivo"
            if e.error or not e.sddl:
                filas.append([ruta, tipo, "", "", "", "", e.error, "", ""])
                continue
            d = e.descriptor
            base = [ruta, tipo, lec.nombre(d.propietario), "Cortada" if d.protegida else "Hereda"]
            for x in d.entradas:
                filas.append(base + [lec.nombre(x.sid), "Permitir" if x.permitir else "Denegar", dom.nombre_nivel(x.mascara),
                                     dom.texto_aplica(x.banderas) if e.carpeta else "Este archivo",
                                     "Heredada" if x.heredada else "Propia"])
        self.perm_plano.set_datos(filas)

    def _perm_export(self):
        secciones = []
        if self.perm_plano.rowCount():
            secciones.append((T_PERMISOS, self.perm_plano.HEADERS, self.perm_plano.filas()))
        if self.perm_an_modelo.rowCount():
            secciones.append((T_ANALISIS, self.perm_an_modelo.HEADERS, self.perm_an_modelo.filas()))
        return secciones

    def _perm_aplicar(self, accion: str, rels_previa: List[str], trabajo, releer: List[str], copia_rama=None):
        """Copia previa + trabajo en segundo plano + historial + volver a leer lo tocado.
        copia_rama = (ruta relativa, archivos): se copia antes la rama ENTERA (cambios
        que llegan a todo lo de dentro); si no, solo rels_previa."""
        lec = self._perm_lec
        raiz = lec.raiz
        self._perm_ocupado(True)
        self.perm_estado.setText(f"⏳ {accion}…")

        def todo():
            t.progreso.emit(0, "Guardando cómo está ahora…")
            if copia_rama is not None:
                previa_lec = dom.leer_arbol(lec.ruta(copia_rama[0]), archivos=copia_rama[1])
                previa_lec.origen = accion
                previa = dom.guardar(previa_lec, os.path.join(dom.carpeta_copias(), dom.nombre_copia(previa_lec, "Antes")))
            else:
                previa = dom.copia_previa(raiz, rels_previa, accion)
            t.progreso.emit(0, accion + "…")
            return previa, trabajo()
        t = tareas.Tarea(todo)
        t.progreso.connect(lambda _n, tx: self.perm_estado.setText(f"⏳ {tx}"))
        t.ok.connect(lambda r: self._perm_hecho(accion, r, releer))
        t.error.connect(lambda m: (self._perm_ocupado(False), self.perm_estado.setText(f"✕ {m}"),
                                   self._avisar("Permisos", f"No se pudo:\n\n{m}", QMessageBox.Warning)))
        self._perm_tarea = t
        t.start()

    def _perm_hecho(self, accion, r, releer):
        previa, resultados = r
        mal = [(rel, m) for rel, ok, m in resultados if not ok]
        avisos = [(rel, m) for rel, ok, m in resultados if ok and m]
        lec = self._perm_lec
        objetivo = lec.ruta(resultados[0][0]) if len(resultados) == 1 else lec.raiz
        if not resultados:
            self._perm_ocupado(False)
            return
        self.repair_audit_db.registrar(seccion=SECCION, accion=accion, objetivo=objetivo, exito=not mal,
                                       detalle=f"{len(resultados) - len(mal)} bien, {len(mal)} mal" + (f": {mal[0][1]}" if mal else ""),
                                       backup_realizado=True, backup_ruta=previa)
        texto = f"{accion}: hecho ({len(resultados) - len(mal)}).\n\nCopia de cómo estaba antes:\n{previa}"
        if mal:
            texto = f"{accion}: no se pudo en {len(mal)}:\n" + "\n".join(f"• {_texto_rel(a)}: {m}" for a, m in mal[:15]) + \
                    f"\n\nCopia de cómo estaba antes:\n{previa}"
        if avisos:
            texto += "\n\nOjo:\n" + "\n".join(f"• {_texto_rel(a)}: {m[:300]}" for a, m in avisos[:8])
        self._avisar("Permisos", texto, QMessageBox.Warning if mal else QMessageBox.Information)
        self._perm_releer(releer)

    def _perm_releer(self, bases: List[str]):
        """Vuelve a leer lo tocado y lo que tiene dentro (cambia lo heredado)."""
        lec = self._perm_lec
        rels = [e.rel for e in lec.elementos if any(dom._en_rama(e.rel, b) for b in bases)]

        def trabajo():
            return dom.leer_arbol(lec.raiz, solo=rels)
        t = tareas.Tarea(trabajo)
        t.ok.connect(self._perm_releido)
        t.error.connect(lambda m: (self._perm_ocupado(False), self.perm_estado.setText(f"✕ {m}")))
        self._perm_tarea = t
        self.perm_estado.setText("⏳ Volviendo a leer lo cambiado…")
        t.start()

    def _perm_releido(self, nueva: dom.Lectura):
        lec = self._perm_lec
        nuevos = {e.rel.lower(): e for e in nueva.elementos}
        lec.elementos = [nuevos.get(e.rel.lower(), e) for e in lec.elementos]
        lec.cuentas.update(nueva.cuentas)
        actual = self._perm_elemento_actual()
        rel = actual.rel if actual else ""
        self._perm_cargar(lec, "")
        self._perm_ir_a(rel)

    def _perm_ir_a(self, rel: str):
        clave = (rel or "").lower()
        it_iter = [self.perm_arbol.topLevelItem(i) for i in range(self.perm_arbol.topLevelItemCount())]
        while it_iter:
            it = it_iter.pop()
            if (it.data(0, Qt.UserRole) or "").lower() == clave:
                self.perm_arbol.setCurrentItem(it)
                self.perm_arbol.scrollToItem(it)
                return
            it_iter.extend(it.child(i) for i in range(it.childCount()))

    def _perm_editar(self):
        e = self._perm_elemento_actual()
        if e is None or self._perm_viendo_copia:
            return
        lec = self._perm_lec
        ruta = lec.ruta(e.rel)

        def nombre_de(sid):
            n = lec.cuentas.get(dom.sid_canonico(sid), ("", 0))[0]
            return n or dom.nombre_de_sid(sid, dom.servidor_de(lec.raiz))[0] or lec.nombre(sid)
        try:
            dlg = EditarPermisosDialog(ruta, e.carpeta, nombre_de, self)
        except Exception as ex:
            self._avisar("Editar permisos", f"No se pudieron leer los permisos de:\n{ruta}\n\n{ex}", QMessageBox.Warning)
            return
        if dlg.exec_() != QDialog.Accepted:
            return
        cambios = dom.diferencias(dlg.original, dlg.nuevo, nombre_de)
        texto = f"{ruta}\n\n" + ("\n".join(f"• {c}" for c in cambios) if cambios else "")
        if dlg.resetear_dentro:
            texto += "\n• Todo lo de dentro pierde sus permisos propios y hereda de esta carpeta"
        texto += "\n\nAntes se guarda una copia de cómo está ahora. Windows aplica lo heredable a todo lo de dentro: en carpetas grandes tarda.\n\n¿Aplicar?"
        if not self._confirmar_accion("Editar permisos", texto):
            return
        nuevo, resetear = dlg.nuevo, dlg.resetear_dentro
        cambia = nuevo.clave() != dlg.original.clave()

        def trabajo():
            res = []
            if cambia:
                try:
                    res.append((e.rel, True, dom.escribir(ruta, nuevo)))
                except Exception as ex:
                    res.append((e.rel, False, str(ex)))
            if resetear:
                ok, salida = dom.restablecer_herencia_dentro(ruta)
                res.append((e.rel, ok, "" if ok else salida))
            return res
        self._perm_aplicar("Editar permisos", [e.rel], trabajo, [e.rel],
                           copia_rama=(e.rel, dom.ARCHIVOS_PROPIOS) if resetear else None)

    def _perm_propietario(self):
        e = self._perm_elemento_actual()
        if e is None or self._perm_viendo_copia:
            return
        lec = self._perm_lec
        ruta = lec.ruta(e.rel)
        if not is_admin():
            self._avisar("Propietario", "Para poner de propietario a otra cuenta hace falta ejecutar MASys como administrador.",
                         QMessageBox.Warning)
            return
        dlg = CuentaDialog("Cambiar el propietario", f"Nuevo propietario de:\n{ruta}\n\nEl propietario siempre puede cambiar "
                           "los permisos. Lo normal en un servidor es Administradores.", ruta, self, con_rama=e.carpeta,
                           inicial="Administradores")
        if dlg.exec_() != QDialog.Accepted:
            return
        rama = dlg.chk_rama.isChecked()
        texto = f"Propietario de {ruta}" + (" y de TODO lo que tiene dentro" if rama else "") + f":\n\n{dlg.nombre}\n\n" \
                "Antes se guarda una copia de los propietarios actuales. ¿Continuar?"
        if not self._confirmar_accion("Cambiar el propietario", texto):
            return
        sid = dlg.sid

        def trabajo():
            if rama:
                ok, salida = dom.cambiar_propietario_rama(ruta, sid)
                return [(e.rel, ok, "" if ok else salida)]
            return dom.cambiar_propietario(lec.raiz, [e.rel], sid)
        self._perm_aplicar("Cambiar el propietario", [e.rel], trabajo, [e.rel],
                           copia_rama=(e.rel, dom.ARCHIVOS_TODOS) if rama else None)

    def _perm_quitar_cuenta(self):
        e = self._perm_elemento_actual()
        if e is None or self._perm_viendo_copia or not e.carpeta:
            return
        lec = self._perm_lec
        ruta = lec.ruta(e.rel)
        dlg = CuentaDialog("Quitar una cuenta de la rama", f"Se quitan las entradas PROPIAS de esta cuenta en:\n{ruta}\n"
                           "y en todo lo leído de dentro (lo heredado desaparece solo).", ruta, self)
        if dlg.exec_() != QDialog.Accepted:
            return
        cambios = dom.quitar_cuenta_de_rama(lec, e.rel, dlg.sid)
        if not cambios:
            self._avisar("Quitar una cuenta", f"{dlg.nombre} no tiene permisos propios en esta rama (solo heredados o ninguno).")
            return
        lista = "\n".join(f"• {_texto_rel(r)}" for r, _d in cambios[:15]) + ("\n…" if len(cambios) > 15 else "")
        if not self._confirmar_accion("Quitar una cuenta", f"Quitar {dlg.nombre} de {len(cambios)} sitio(s):\n\n{lista}\n\n"
                                      "Antes se guarda una copia de cómo están ahora. ¿Continuar?"):
            return

        def trabajo():
            res = []
            for rel, desc in cambios:
                try:
                    res.append((rel, True, dom.escribir(lec.ruta(rel), desc, propietario=False)))
                except Exception as ex:
                    res.append((rel, False, str(ex)))
            return res
        self._perm_aplicar(f"Quitar {dlg.nombre}", [r for r, _d in cambios], trabajo, [e.rel])

    # ── ⚠️ Análisis ──
    def _perm_tab_analisis(self):
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setSpacing(8)
        lay.setContentsMargins(10, 10, 10, 10)
        self.perm_an_nivel = piezas.combo(["Todos los niveles", dom.CRITICO, dom.AVISO, dom.INFO_])
        self.perm_an_nivel.currentIndexChanged.connect(lambda _i: self._perm_pintar_analisis())
        self.perm_an_tipo = piezas.combo(["Todos los tipos"])
        self.perm_an_tipo.currentIndexChanged.connect(lambda _i: self._perm_pintar_analisis(False))
        fila, self.perm_an_busqueda, _a, self.perm_an_recuento = piezas.barra_superior(
            "🔍 Buscar ruta, cuenta o tipo...", [self.perm_an_nivel, self.perm_an_tipo,
                                                 piezas.boton("📁 Ver la carpeta", "ghost", self._perm_an_ir)], None)
        lay.addLayout(fila)
        self.perm_an_resumen = piezas.nota("Lee una carpeta en «📁 Permisos de carpetas» (o abre una copia) para analizarla.")
        lay.addWidget(self.perm_an_resumen)
        self.perm_an_modelo = TablaSimple(["Nivel", "Qué pasa", "Ruta", "Cuenta", "Detalle", "Qué hacer"],
                                          color=lambda h, c: NIVEL_COLOR.get(h.nivel) if c == 0 else None)
        self.perm_an_tabla, self.perm_an_proxy = piezas.tabla(self.perm_an_modelo, self, columna_ancha=4,
                                                              busqueda=self.perm_an_busqueda)
        self.perm_an_tabla.doubleClicked.connect(lambda _i: self._perm_an_ir())
        lay.addWidget(self.perm_an_tabla, 1)
        return tab

    def _perm_pintar_analisis(self, rehacer_tipos: bool = True):
        hs = self._perm_hallazgos
        if rehacer_tipos:
            actual = self.perm_an_tipo.currentText()
            self.perm_an_tipo.blockSignals(True)
            self.perm_an_tipo.clear()
            self.perm_an_tipo.addItem("Todos los tipos")
            for tipo in sorted({h.tipo for h in hs}):
                self.perm_an_tipo.addItem(tipo)
            i = self.perm_an_tipo.findText(actual)
            self.perm_an_tipo.setCurrentIndex(max(i, 0))
            piezas.ajustar_combo(self.perm_an_tipo)
            self.perm_an_tipo.blockSignals(False)
        nivel = self.perm_an_nivel.currentText() if self.perm_an_nivel.currentIndex() else ""
        tipo = self.perm_an_tipo.currentText() if self.perm_an_tipo.currentIndex() else ""
        lista = [h for h in hs if (not nivel or h.nivel == nivel) and (not tipo or h.tipo == tipo)]
        self.perm_an_modelo.set_datos([[h.nivel, h.tipo, h.rel, h.cuenta, h.detalle, h.que_hacer] for h in lista], lista)
        self.perm_an_recuento.setText(f"{len(lista)} de {len(hs)}")
        if self._perm_lec is not None:
            n = {k: sum(1 for h in hs if h.nivel == k) for k in (dom.CRITICO, dom.AVISO, dom.INFO_)}
            self.perm_an_resumen.setText(f"{self._perm_lec.raiz}: 🔴 {n[dom.CRITICO]} críticos · 🟠 {n[dom.AVISO]} avisos · "
                                         f"🔵 {n[dom.INFO_]} para revisar" if hs else f"{self._perm_lec.raiz}: ✅ nada que revisar")

    def _perm_an_ir(self):
        h = objeto_seleccionado(self.perm_an_tabla, self.perm_an_proxy, self.perm_an_modelo)
        if h is None:
            return
        self.compartidas_tabs.setCurrentIndex(1)
        self.perm_filtro.setCurrentIndex(0)
        self.perm_busqueda.clear()
        self._perm_pintar_arbol()
        self._perm_ir_a("" if h.rel == "(carpeta principal)" else h.rel)

    # ── 🧭 Acceso de una cuenta ──
    def _perm_tab_acceso(self):
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setSpacing(8)
        lay.setContentsMargins(10, 10, 10, 10)
        self.perm_ac_cuenta = QLineEdit()
        self.perm_ac_cuenta.setPlaceholderText("Usuario o grupo: DOMINIO\\usuario, Contabilidad, Todos…")
        self.perm_ac_cuenta.setMinimumWidth(320)
        self.perm_ac_cuenta.returnPressed.connect(self._perm_consultar)
        self.perm_ac_cambios = QCheckBox("Solo donde cambia")
        self.perm_ac_cambios.setChecked(True)
        self.perm_ac_cambios.setToolTip("Sin marcar, una fila por cada carpeta y archivo leído")
        self.perm_ac_cambios.stateChanged.connect(lambda _s: self._perm_consultar() if getattr(self, "_perm_ac_sids", None) else None)
        self.perm_btn_consultar = piezas.boton("🧭 Consultar", "primary", self._perm_consultar)
        fila, self.perm_ac_busqueda, _a, self.perm_ac_recuento = piezas.barra_superior(
            "🔍 Buscar ruta...", [self.perm_ac_cuenta, self.perm_btn_consultar, self.perm_ac_cambios], None)
        lay.addLayout(fila)
        self.perm_ac_nota = piezas.nota("Sobre la carpeta leída en «📁 Permisos de carpetas»: qué puede hacer la cuenta en cada "
                                        "sitio, sumando sus grupos. Si la carpeta está compartida se cruza con el permiso de red "
                                        "(manda el más restrictivo).")
        lay.addWidget(self.perm_ac_nota)
        self.perm_ac_modelo = TablaSimple(["Ruta", "Tipo", "Permiso en la carpeta", "Por red", "Efectivo", "Lo dan", "Lo deniegan"],
                                          color=self._perm_ac_color)
        self.perm_ac_tabla, self.perm_ac_proxy = piezas.tabla(self.perm_ac_modelo, self, columna_ancha=0, busqueda=self.perm_ac_busqueda)
        lay.addWidget(self.perm_ac_tabla, 1)
        return tab

    @staticmethod
    def _perm_ac_color(a, col):
        if col == 4:
            if not a.mascara:
                return COLOR_APAGADO
            return COLOR_ERROR if a.mascara & 0x40000 else (COLOR_AVISO if a.mascara & 0x2 else COLOR_OK)
        if col == 6 and a.denegado:
            return COLOR_ERROR
        return None

    def _perm_consultar(self):
        lec = self._perm_lec
        if lec is None:
            self._avisar("Acceso de una cuenta", "Lee primero una carpeta en «📁 Permisos de carpetas».")
            return
        cuenta = self.perm_ac_cuenta.text().strip()
        if not cuenta:
            return
        self.perm_btn_consultar.setEnabled(False)
        self.perm_ac_nota.setText("⏳ Buscando la cuenta y sus grupos…")
        solo = self.perm_ac_cambios.isChecked()

        def trabajo():
            sid, completo, sids, avisos = dom.grupos_de(cuenta, lec.raiz)
            for s in sids:
                if s not in lec.cuentas:
                    lec.cuentas[s] = dom.nombre_de_sid(s, dom.servidor_de(lec.raiz))
            return sid, completo, sids, avisos, dom.acceso_de_cuenta(lec, sid, sids, solo)
        tareas.lanzar(self, "_perm_tarea_ac", trabajo, al_terminar=self._perm_consultado, com=True,
                      al_fallar=lambda m: (self.perm_btn_consultar.setEnabled(True), self.perm_ac_nota.setText(f"✕ {m}")))

    def _perm_consultado(self, r):
        self.perm_btn_consultar.setEnabled(True)
        sid, completo, sids, avisos, filas = r
        self._perm_ac_sids = sids
        lec = self._perm_lec
        grupos = sorted(lec.nombre(s) for s in sids if s != sid)
        self.perm_ac_nota.setText(f"👤 {completo} · cuenta como: {', '.join(grupos)}" + ("".join(f"\n⚠️ {a}" for a in avisos)))
        self.perm_ac_modelo.set_datos([[_texto_rel(a.rel), "Carpeta" if a.carpeta else "Archivo", a.nivel, a.red or "—",
                                        a.efectivo, a.por, a.denegado] for a in filas], filas)
        self.perm_ac_recuento.setText(f"{len(filas)} filas")

    # ── 💾 Copias ──
    def _perm_tab_copias(self):
        tab = QWidget()
        lay = QVBoxLayout(tab)
        lay.setSpacing(8)
        lay.setContentsMargins(10, 10, 10, 10)
        self._perm_botones_copias = [
            piezas.boton("💾 Guardar copia de lo leído", "primary", self._perm_guardar,
                         "Guarda propietario, herencia y permisos de todo lo leído en «📁 Permisos de carpetas», más los permisos de red"),
            piezas.boton("♻️ Restaurar…", "ghost", self._perm_restaurar, "Compara la copia con lo actual y restaura todo o una parte, aquí u en otro servidor"),
            piezas.boton("⇄ Comparar dos copias", "ghost", self._perm_comparar_copias),
            piezas.boton("📂 Abrir en «Permisos»", "ghost", self._perm_abrir_copia, "Ver y analizar la copia sin cambiar nada"),
            piezas.boton("📤 Exportar para icacls…", "ghost", self._perm_icacls, "Formato de icacls /save (sin propietarios)"),
        ]
        fila, self.perm_co_busqueda, self.perm_co_btn_act, self.perm_co_recuento = piezas.barra_superior(
            "🔍 Buscar copia...", self._perm_botones_copias + [piezas.boton("📁 Carpeta", "ghost", self._perm_abrir_carpeta),
                                                               piezas.boton("📥 Otra copia…", "ghost", self._perm_otra_copia)],
            self._perm_listar_copias)
        lay.addLayout(fila)
        lay.addWidget(piezas.nota("Copias en INFORMES\\PERMISOS. Las «Antes de…» las hace MASys solo antes de cada cambio, "
                                  "con el estado anterior de lo que se cambió: restaurar una deshace ese cambio."))
        split = QSplitter(Qt.Vertical)
        self.perm_co_modelo = TablaSimple(["Fecha", "Equipo", "Carpeta", "Elementos", "Tipo", "Archivo"],
                                          color=lambda c, col: COLOR_INFO if col == 4 and c.get("parcial") else None)
        self.perm_co_tabla, self.perm_co_proxy = piezas.tabla(self.perm_co_modelo, self, columna_ancha=2, seleccion_multiple=True,
                                                              busqueda=self.perm_co_busqueda, orden=(0, False))
        self.perm_co_tabla.doubleClicked.connect(lambda _i: self._perm_restaurar())
        split.addWidget(self.perm_co_tabla)
        self.perm_cmp_modelo = TablaSimple(["Estado", "Ruta", "Qué cambia"],
                                           color=lambda d, c: ESTADO_COLOR.get(d.estado) if c == 0 else None,
                                           tooltip=lambda d, c: "\n".join(d.detalle) if c == 2 else None)
        self.perm_cmp_tabla, self.perm_cmp_proxy = piezas.tabla(self.perm_cmp_modelo, self, columna_ancha=2)
        split.addWidget(self.perm_cmp_tabla)
        split.setSizes([400, 250])
        lay.addWidget(split, 1)
        self._perm_resumenes = {}
        QTimer.singleShot(0, self._perm_listar_copias)
        return tab

    def _perm_listar_copias(self):
        carpeta = dom.carpeta_copias()
        archivos = [os.path.join(carpeta, f) for f in os.listdir(carpeta) if f.lower().endswith(dom.EXTENSION)]
        cache = self._perm_resumenes

        def trabajo():
            salida = []
            for a in archivos:
                clave = (a, os.path.getmtime(a))
                if clave not in cache:
                    try:
                        cache[clave] = dom.resumen_copia(a)
                    except Exception as e:
                        cache[clave] = {"ruta": a, "fecha": "", "equipo": "", "raiz": f"✕ dañada: {e}", "elementos": 0,
                                        "parcial": False, "origen": ""}
                salida.append(cache[clave])
            return salida
        tareas.lanzar(self, "_perm_tarea_lista", trabajo, al_terminar=self._perm_copias_listadas,
                      al_fallar=lambda m: self.set_status(f"✕ Copias de permisos: {m}"))

    def _perm_copias_listadas(self, lista):
        filas = [[_fecha(c["fecha"]), c["equipo"], c["raiz"], c["elementos"],
                  f"Antes de: {c['origen']}" if c.get("parcial") else "Completa" + (" (parada a medias)" if c.get("detenida") else ""),
                  os.path.basename(c["ruta"])] for c in lista]
        self.perm_co_modelo.set_datos(filas, lista)
        self.perm_co_recuento.setText(f"{len(lista)} copias")

    def _perm_copia_sel(self) -> Optional[dict]:
        c = objeto_seleccionado(self.perm_co_tabla, self.perm_co_proxy, self.perm_co_modelo)
        if c is None:
            self._avisar("Copias de permisos", "Elige una copia de la lista.")
        return c

    def _perm_guardar(self):
        lec = self._perm_lec
        if lec is None or self._perm_viendo_copia:
            self._avisar("Copias de permisos", "Lee primero la carpeta en «📁 Permisos de carpetas».")
            return
        if lec.detenida and not self._confirmar_accion("Copias de permisos", "La lectura se paró a medias: la copia no tendrá todo. ¿Guardarla igual?"):
            return
        destino = os.path.join(dom.carpeta_copias(), dom.nombre_copia(lec))
        try:
            dom.guardar(lec, destino)
        except OSError as e:
            self._avisar("Copias de permisos", f"No se pudo guardar:\n\n{e}", QMessageBox.Warning)
            return
        self.repair_audit_db.registrar(seccion=SECCION, accion="Copia de permisos", objetivo=lec.raiz, exito=True,
                                       detalle=f"{len(lec.elementos)} elementos, {len(lec.recursos)} recursos compartidos",
                                       backup_realizado=True, backup_ruta=destino)
        self.set_status(f"💾 Copia guardada: {destino}")
        self._perm_listar_copias()

    def _perm_cargar_copia(self, ruta: str, al_terminar):
        self.set_status("⏳ Abriendo la copia…")
        tareas.lanzar(self, "_perm_tarea_carga", dom.cargar, ruta, al_terminar=al_terminar,
                      al_fallar=lambda m: self._avisar("Copias de permisos", m, QMessageBox.Warning))

    def _perm_restaurar(self, ruta: str = ""):
        if not ruta:
            c = self._perm_copia_sel()
            if c is None:
                return
            ruta = c["ruta"]
        self._perm_cargar_copia(ruta, lambda copia: self._perm_dialogo_restaurar(copia, ruta))

    def _perm_dialogo_restaurar(self, copia, ruta):
        self.set_status("")
        RestaurarDialog(self, copia, ruta, self).exec_()
        self._perm_listar_copias()
        if self._perm_lec is not None and not self._perm_viendo_copia:
            self._perm_releer([""])

    def _perm_abrir_copia(self, ruta: str = ""):
        if not ruta:
            c = self._perm_copia_sel()
            if c is None:
                return
            ruta = c["ruta"]
        self._perm_cargar_copia(ruta, lambda lec: (self._perm_cargar(lec, ruta), self.compartidas_tabs.setCurrentIndex(1)))

    def _perm_otra_copia(self):
        ruta = dialogo_abrir_archivo(self, "Copia de permisos", dom.carpeta_copias(), f"Copias de permisos (*{dom.EXTENSION})")
        if not ruta:
            return
        caja = QMessageBox(self)
        _sin_ayuda(caja)
        caja.setWindowTitle("Copia de permisos")
        caja.setText(f"{os.path.basename(ruta)}\n\n¿Qué quieres hacer con ella?")
        ver = caja.addButton("📂 Verla y analizarla", QMessageBox.YesRole)
        rest = caja.addButton("♻️ Restaurar…", QMessageBox.NoRole)
        caja.addButton("Cancelar", QMessageBox.RejectRole)
        caja.exec_()
        if caja.clickedButton() is ver:
            self._perm_abrir_copia(ruta)
        elif caja.clickedButton() is rest:
            self._perm_restaurar(ruta)

    def _perm_comparar_copias(self):
        sel = objetos_seleccionados(self.perm_co_tabla, self.perm_co_proxy, self.perm_co_modelo)
        if len(sel) != 2:
            self._avisar("Comparar copias", "Selecciona DOS copias de la misma carpeta (Ctrl + clic).")
            return
        a, b = sorted(sel, key=lambda c: c["fecha"])
        if dom.normalizar_raiz(a["raiz"]).lower() != dom.normalizar_raiz(b["raiz"]).lower() and not self._confirmar_accion(
                "Comparar copias", f"Son de carpetas distintas:\n{a['raiz']}\n{b['raiz']}\n\nSe comparan por la ruta de dentro. ¿Seguir?"):
            return
        self.set_status("⏳ Comparando…")
        tareas.lanzar(self, "_perm_tarea_cmp", lambda: dom.comparar(dom.cargar(a["ruta"]), dom.cargar(b["ruta"])),
                      al_terminar=lambda difs: self._perm_comparadas(a, b, difs),
                      al_fallar=lambda m: self._avisar("Comparar copias", m, QMessageBox.Warning))

    def _perm_comparadas(self, a, b, difs):
        self.perm_cmp_modelo.set_datos([[d.estado, _texto_rel(d.rel), " · ".join(d.detalle)] for d in difs], difs)
        self.set_status(f"⇄ De {_fecha(a['fecha'])} a {_fecha(b['fecha'])}: {len(difs)} cambios" if difs else
                        f"⇄ De {_fecha(a['fecha'])} a {_fecha(b['fecha'])}: sin cambios")

    def _perm_icacls(self):
        lec = self._perm_lec
        raiz = lec.raiz if lec is not None and not self._perm_viendo_copia else dom.normalizar_raiz(self.perm_ruta.text())
        if not raiz:
            self._avisar("Exportar para icacls", "Lee o escribe primero la carpeta en «📁 Permisos de carpetas».")
            return
        nombre = os.path.splitext(dom.nombre_copia(dom.Lectura(raiz, dom.equipo_de(raiz), "", ""), "icacls"))[0] + ".txt"
        destino = dialogo_guardar_archivo(self, "Exportar para icacls", os.path.join(dom.carpeta_copias(), nombre), "Texto (*.txt)")
        if not destino:
            return
        self.set_status("⏳ icacls /save…")
        tareas.lanzar(self, "_perm_tarea_icacls", dom.exportar_icacls, raiz, destino,
                      al_terminar=lambda r: self._avisar("Exportar para icacls", (
                          f"Guardado en:\n{destino}\n\nSe restaura con:\nicacls \"{os.path.dirname(raiz.rstrip(chr(92))) or raiz}\" "
                          f"/restore \"{destino}\"\n\nNo incluye los propietarios.") if r[0] else f"No se pudo:\n\n{r[1][:800]}",
                          QMessageBox.Information if r[0] else QMessageBox.Warning),
                      al_fallar=lambda m: self._avisar("Exportar para icacls", m, QMessageBox.Warning))

    def _perm_abrir_carpeta(self):
        try:
            subprocess.Popen(["explorer", dom.carpeta_copias()])
        except OSError as e:
            self._avisar("Copias de permisos", str(e), QMessageBox.Warning)
