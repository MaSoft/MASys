# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: sección 🧳 Inventario de migración (categoría Administración).

En el equipo VIEJO: «🔄 Actualizar» hace el inventario (equipo, red, unidades,
usuarios, credenciales y certificados, correo, navegadores, nube, Office,
programas, impresoras, tareas, dispositivos, datos y personalización), cada
elemento con «qué hacer en el equipo nuevo», y «💾 Guardar…» lo deja en un
archivo .msysinv.
En el equipo NUEVO: «📂 Comparar…» abre ese archivo, hace el inventario de
aquí y marca cada elemento como presente / falta / distinto; «🔁 Reinstalar
con winget…» instala los programas que faltan. «🌐 Informe de migración…»
saca el HTML con el mismo diseño que MIGRACION.PS1 (buscador, apartados plegables).

Motor sin Qt: msys/domain/migracion.py (+ migracion_ps.py)."""

import os
import time

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QDialog, QLabel, QListWidget, QListWidgetItem, QMessageBox, QVBoxLayout

from msys.core import tareas
from msys.core.utils import dialogo_abrir_archivo, dialogo_guardar_archivo, get_informes_folder
from msys.domain import migracion as dom
from msys.models.tabla_simple import COLOR_AVISO, COLOR_ERROR, COLOR_OK, TablaSimple
from msys.ui import piezas

SECCION = "🧳 Inventario de migración"
FILTROS = ["Todo", "Solo lo importante", "Faltan en este equipo", "Distintos", "Presentes"]


class ElegirProgramasDialog(QDialog):
    def __init__(self, ids, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Reinstalar con winget")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.resize(520, 560)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(f"{len(ids)} programas del equipo viejo que faltan aquí y winget sabe instalar:"))
        self.lista = QListWidget()
        for i in ids:
            it = QListWidgetItem(i)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked)
            self.lista.addItem(it)
        lay.addWidget(self.lista, 1)
        lay.addWidget(piezas.nota("Se abre una ventana de winget para ver el avance; los programas de pago necesitarán su licencia."))
        lay.addLayout(piezas.fila(piezas.boton("Marcar todos", "ghost", lambda: self._todos(True)),
                                  piezas.boton("Ninguno", "ghost", lambda: self._todos(False)), None,
                                  piezas.boton("Cancelar", "ghost", self.reject),
                                  piezas.boton("🔁 Instalar los marcados", "primary", self.accept), estirar=False))

    def _todos(self, marcar):
        for i in range(self.lista.count()):
            self.lista.item(i).setCheckState(Qt.Checked if marcar else Qt.Unchecked)

    def elegidos(self):
        return [self.lista.item(i).text() for i in range(self.lista.count()) if self.lista.item(i).checkState() == Qt.Checked]


def _color(fila, col):
    e, estado, _d = fila
    if col == 1 and e.importante:
        return COLOR_AVISO
    if col == 4:
        return {dom.PRESENTE: COLOR_OK, dom.FALTA: COLOR_ERROR, dom.DISTINTO: COLOR_AVISO}.get(estado)
    return None


class MigracionMixin:
    def crear_tab_migracion(self):
        tab = piezas.PaginaPerezosa(lambda: None)
        lay = QVBoxLayout(tab)
        lay.setSpacing(8)
        lay.setContentsMargins(10, 10, 10, 10)
        self.mig_categoria = piezas.combo(["Todas las categorías"] + dom.CATEGORIAS)
        self.mig_categoria.currentIndexChanged.connect(lambda _i: self._mig_pintar())
        self.mig_filtro = piezas.combo(FILTROS)
        self.mig_filtro.currentIndexChanged.connect(lambda _i: self._mig_pintar())
        self.mig_botones = {
            "guardar": piezas.boton("💾 Guardar…", "ghost", self._mig_guardar, "Guardar el inventario de este equipo para el equipo nuevo"),
            "comparar": piezas.boton("📂 Comparar…", "ghost", self._mig_comparar,
                                     "En el equipo NUEVO: abrir el inventario del viejo y ver qué falta"),
            "winget": piezas.boton("🔁 Reinstalar con winget…", "ghost", self._mig_winget,
                                   "Instalar aquí los programas del equipo viejo que faltan"),
            "html": piezas.boton("🌐 Lista de comprobación…", "ghost", self._mig_html, "HTML imprimible con una casilla por elemento"),
        }
        fila, self.mig_busqueda, self.btn_refresh_mig, self.mig_recuento = piezas.barra_superior(
            "🔍 Buscar...", list(self.mig_botones.values()), self._mig_inventario)
        self.btn_refresh_mig.setToolTip("Hacer el inventario de este equipo")
        lay.addLayout(fila)
        # Segunda fila: filtros de detalle (convención de MASys: la barra de arriba es búsqueda + acciones + Actualizar)
        self.mig_estado = piezas.nota("Equipo viejo: Actualizar hace el inventario y Guardar lo deja en un archivo. Equipo nuevo: Comparar. "
                                      "No se copia ningún dato ni se guarda ninguna contraseña.")
        fila2 = piezas.fila(self.mig_categoria, self.mig_filtro, self.mig_estado, estirar=False)
        fila2.setStretch(2, 1)
        lay.addLayout(fila2)
        self.mig_modelo = TablaSimple(["Categoría", "Elemento", "Detalle", "Qué hacer en el equipo nuevo", "En este equipo"], color=_color)
        self.mig_tabla, self.mig_proxy = piezas.tabla(self.mig_modelo, self, columna_ancha=3, busqueda=self.mig_busqueda)
        lay.addWidget(self.mig_tabla, 1)
        self._mig_inv = None       # inventario de ESTE equipo
        self._mig_origen = None    # inventario del equipo viejo (al comparar)
        self._mig_filas = []
        self._mig_botones_estado()
        self.registrar_export(SECCION, lambda: [(SECCION, self.mig_modelo.HEADERS, self.mig_modelo.filas())])
        # El HTML de la barra inferior es el informe de MIGRACION.PS1 (no la tabla genérica)
        self.registrar_html(SECCION, self._mig_informe)
        return tab

    # ── estado ─────────────────────────────────────────────────
    def _mig_botones_estado(self, ocupado: bool = False):
        self.btn_refresh_mig.setEnabled(not ocupado)
        self.mig_botones["comparar"].setEnabled(not ocupado)
        self.mig_botones["guardar"].setEnabled(not ocupado and self._mig_inv is not None)
        self.mig_botones["html"].setEnabled(not ocupado and bool(self._mig_filas))
        faltan = any(e.categoria == dom.C_WINGET and est == dom.FALTA for e, est, _d in self._mig_filas)
        self.mig_botones["winget"].setEnabled(not ocupado and self._mig_origen is not None and faltan)
        self.mig_tabla.setColumnHidden(4, self._mig_origen is None)
        if self._mig_origen is not None and self.mig_tabla.columnWidth(4) < 90:
            self.mig_tabla.setColumnWidth(4, 130)  # al volver a mostrarse, Qt la deja con ancho 0

    def _mig_inventario(self, despues=None):
        self._mig_botones_estado(ocupado=True)
        self.set_status("🧳 Haciendo el inventario de este equipo (1-3 minutos)...")
        t = tareas.Tarea(lambda: dom.hacer_inventario(progreso=lambda v, tx: t.progreso.emit(v, tx)), com=True)
        t.progreso.connect(lambda v, tx: self.mig_estado.setText(f"Haciendo el inventario: {v}% · {tx}"))
        t.ok.connect(lambda inv: self._mig_hecho(inv, despues))
        t.error.connect(lambda m: (self._mig_botones_estado(), self._avisar(SECCION, f"El inventario falló:\n\n{m}", QMessageBox.Warning)))
        self._mig_tarea = t
        t.start()

    def _mig_hecho(self, inv, despues=None):
        self._mig_inv = inv
        if despues:
            despues()
            return
        self._mig_origen = None
        self._mig_filas = [(e, dom.INFORMATIVO, "") for e in dom.elementos_de(inv)]
        imp = sum(1 for e, _s, _d in self._mig_filas if e.importante)
        self.mig_estado.setText(f"Inventario de {inv['equipo']} · {inv['fecha']} · {len(self._mig_filas)} elementos, {imp} importantes "
                                "(en amarillo). Guárdalo para compararlo en el equipo nuevo.")
        self._mig_pintar()
        self._mig_botones_estado()
        self.set_status(f"✅ Inventario hecho: {len(self._mig_filas)} elementos")
        piezas.refrescar_export(self)

    def _mig_pintar(self):
        cat = self.mig_categoria.currentIndex()
        f = self.mig_filtro.currentIndex()
        filas = []
        for fila in self._mig_filas:
            e, est, _d = fila
            if cat > 0 and e.categoria != dom.CATEGORIAS[cat - 1]:
                continue
            if (f == 1 and not e.importante) or (f == 2 and est != dom.FALTA) or (f == 3 and est != dom.DISTINTO) or \
                    (f == 4 and est != dom.PRESENTE):
                continue
            filas.append(fila)
        self.mig_modelo.set_datos([[e.categoria, e.elemento, e.detalle, e.accion, est if self._mig_origen else ""]
                                   for e, est, _d in filas], filas)
        self.mig_recuento.setText(f"{len(filas)} de {len(self._mig_filas)}")

    # ── acciones ───────────────────────────────────────────────
    def _mig_guardar(self):
        if not self._mig_inv:
            return
        nombre = f"Inventario_{self._mig_inv['equipo']}_{time.strftime('%Y%m%d_%H%M')}{dom.EXTENSION}"
        ruta = dialogo_guardar_archivo(self, "Guardar inventario", os.path.join(get_informes_folder(), nombre),
                                       f"Inventario de MASys (*{dom.EXTENSION})")
        if not ruta:
            return
        try:
            dom.guardar(self._mig_inv, ruta)
        except OSError as e:
            self._avisar(SECCION, f"No se pudo guardar:\n\n{e}", QMessageBox.Warning)
            return
        self._avisar(SECCION, f"Inventario guardado en:\n\n{ruta}\n\nNo lleva contraseñas, pero sí nombres de cuentas, redes y "
                     "programas: guárdalo en un sitio seguro. En el equipo nuevo: 📂 Comparar…")

    def _mig_comparar(self):
        ruta = dialogo_abrir_archivo(self, "Inventario del equipo viejo", get_informes_folder(),
                                     f"Inventario de MASys (*{dom.EXTENSION});;Todos (*.*)")
        if not ruta:
            return
        try:
            origen = dom.cargar(ruta)
        except (OSError, ValueError) as e:
            self._avisar(SECCION, f"No se pudo abrir:\n\n{e}", QMessageBox.Warning)
            return
        if origen.get("equipo") == os.environ.get("COMPUTERNAME") and not self._confirmar_accion(
                SECCION, "Ese inventario es de ESTE mismo equipo. ¿Compararlo igualmente?"):
            return
        self._mig_origen = origen
        self._mig_inventario(despues=self._mig_mostrar_comparacion)

    def _mig_mostrar_comparacion(self):
        comp = dom.comparar(self._mig_origen, self._mig_inv)
        self._mig_filas = comp
        n = {k: sum(1 for _e, s, _d in comp if s == k) for k in (dom.PRESENTE, dom.FALTA, dom.DISTINTO)}
        self.mig_estado.setText(f"Comparando {self._mig_origen['equipo']} ({self._mig_origen['fecha']}) con este equipo: "
                                f"{n[dom.PRESENTE]} presentes · {n[dom.FALTA]} faltan · {n[dom.DISTINTO]} distintos")
        self.mig_filtro.setCurrentIndex(2)
        self._mig_pintar()
        self._mig_botones_estado()
        self.set_status(f"✅ Comparación hecha: faltan {n[dom.FALTA]}")
        piezas.refrescar_export(self)

    def _mig_winget(self):
        ids = sorted(e.elemento for e, est, _d in self._mig_filas if e.categoria == dom.C_WINGET and est == dom.FALTA and e.clave)
        if not ids:
            return
        dlg = ElegirProgramasDialog(ids, self)
        if dlg.exec_() != QDialog.Accepted or not dlg.elegidos():
            return
        ruta = os.path.join(get_informes_folder(), f"winget_import_{time.strftime('%Y%m%d_%H%M%S')}.json")
        try:
            n = dom.import_winget(self._mig_origen, dlg.elegidos(), ruta)
        except OSError as e:
            self._avisar(SECCION, f"No se pudo preparar la lista:\n\n{e}", QMessageBox.Warning)
            return
        ok, msg = dom.lanzar_import_winget(ruta)
        self.repair_audit_db.registrar(seccion=SECCION, accion="Reinstalar con winget", objetivo=f"{n} programas ({ruta})",
                                       exito=ok, detalle=msg or "OK")
        if ok:
            self._avisar(SECCION, f"Se ha abierto winget con {n} programas. Cuando termine, vuelve a comparar para ver qué falta.")
        else:
            self._avisar(SECCION, f"No se pudo abrir winget:\n\n{msg}", QMessageBox.Warning)

    def _mig_html(self):
        """Botón de arriba: lista de comprobación con casillas (como antes de 4.2.0)."""
        base = self._mig_origen or self._mig_inv
        if not base:
            return
        comp = self._mig_filas if self._mig_origen else None
        nombre = f"Migracion_{base['equipo']}_{time.strftime('%Y%m%d_%H%M')}.html"
        ruta = dialogo_guardar_archivo(self, "Lista de comprobación", os.path.join(get_informes_folder(), nombre), "HTML (*.html)")
        if not ruta:
            return
        self._mig_escribir(ruta, dom.lista_html(base, comp, (self._mig_inv or {}).get("equipo", "")), "Lista guardada", "¿Abrirla?")

    def _mig_informe(self):
        """Botón HTML de la barra inferior (junto a CSV): informe con el diseño de MIGRACION.PS1."""
        base = self._mig_origen or self._mig_inv
        if not base:
            self._avisar(SECCION, "Haz antes el inventario con 🔄 Actualizar.")
            return
        comp = self._mig_filas if self._mig_origen else None
        nombre = f"Informe_{base['equipo']}_{time.strftime('%Y%m%d_%H%M%S')}.html"   # mismo nombre que MIGRACION.PS1
        ruta = dialogo_guardar_archivo(self, "Informe de migración", os.path.join(get_informes_folder(), nombre), "HTML (*.html)")
        if not ruta:
            return
        self._mig_escribir(ruta, dom.informe_html(base, comp, (self._mig_inv or {}).get("equipo", "")), "Informe guardado", "¿Abrirlo?")

    def _mig_escribir(self, ruta, contenido, hecho, pregunta):
        try:
            with open(ruta, "w", encoding="utf-8") as f:
                f.write(contenido)
        except OSError as e:
            self._avisar(SECCION, f"No se pudo guardar:\n\n{e}", QMessageBox.Warning)
            return
        if self._confirmar_accion(SECCION, f"{hecho} en:\n\n{ruta}\n\n{pregunta}"):
            os.startfile(ruta)
