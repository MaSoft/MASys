# -*- coding: utf-8 -*-
"""Pestañas 🩺 Reparación y ⚙️ Configuración de la sección 🎨 Iconos.

Reparación: cachés de iconos y de miniaturas, accesos directos rotos (con su
icono). Configuración: iconos del escritorio, espaciado, distribuciones
guardadas, iconos superpuestos (overlays) e iconos personalizados de unidades.

Todo cambio pide confirmación, se apunta en el historial de reparaciones
(repair_audit_db) y, si toca el registro de la máquina, deja antes una copia
.reg en BACKUPS\\ICONOS."""

import glob
import os
import subprocess
import time

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QInputDialog, QLabel, QMessageBox, QScrollArea, QSpinBox,
                             QVBoxLayout, QWidget)

from msys import config as msys_config
from msys.core import tareas
from msys.core.utils import is_admin
from msys.domain import iconos as dom
from msys.models.tabla_simple import (COLOR_APAGADO, COLOR_AVISO, COLOR_ERROR, COLOR_OK, TablaSimple,
                                      objeto_seleccionado, objetos_seleccionados)
from msys.ui import piezas

SECCION = "🎨 Iconos"


def carpeta_copias() -> str:
    return os.path.join(msys_config.app_base_dir(), "BACKUPS", "ICONOS")


def _registrar(host, accion, objetivo, ok, detalle=""):
    if hasattr(host, "repair_audit_db"):
        host.repair_audit_db.registrar(seccion=SECCION, accion=accion, objetivo=objetivo, exito=ok,
                                       detalle=detalle if not ok else (detalle or "OK"))


_AVISO_EXPLORADOR = ("La barra de tareas y el escritorio desaparecen un momento mientras el Explorador se reinicia. "
                     "Las ventanas de carpetas abiertas se cierran.")


class ReparacionIconosWidget(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self.cargado = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(5, 5, 5, 5)
        lay.setSpacing(10)

        g, gl = piezas.grupo("🧹 Cachés")
        self.lbl_iconos = QLabel("")
        self.lbl_mini = QLabel("")
        gl.addLayout(piezas.fila(self.lbl_iconos, None, piezas.boton(
            "🧹 Reconstruir caché de iconos", "ghost", lambda: self._reconstruir(False),
            "Para iconos en blanco, equivocados o que no se actualizan"), estirar=False))
        gl.addLayout(piezas.fila(self.lbl_mini, None, piezas.boton(
            "🧹 Reconstruir caché de miniaturas", "ghost", lambda: self._reconstruir(True),
            "Para vistas previas de fotos y documentos que salen mal"), estirar=False))
        lay.addWidget(g)

        self.filtro = piezas.combo(["Con problemas", "Todos"])
        self.filtro.currentIndexChanged.connect(lambda _i: self._pintar())
        self.btn_abrir = piezas.boton("📂 Abrir ubicación", "ghost", self._abrir_ubicacion)
        self.btn_quitar = piezas.boton("✕ Quitar", "danger", self._quitar, "Envía los accesos marcados a la Papelera")
        fila, self.busqueda, _a, self.recuento = piezas.barra_superior(
            "🔍 Buscar acceso directo...", [self.filtro, self.btn_abrir, self.btn_quitar], None, con_equipo=False)
        g2, gl2 = piezas.grupo("🔗 Accesos directos (escritorio, menú Inicio y barra de tareas)")
        gl2.addLayout(fila)
        self.modelo = TablaSimple(["Acceso directo", "Dónde", "Estado", "Destino", "Icono"], color=self._color)
        self.tabla, self.proxy = piezas.tabla(self.modelo, self, columna_ancha=3, seleccion_multiple=True,
                                              busqueda=self.busqueda)
        gl2.addWidget(self.tabla, 1)
        lay.addWidget(g2, 1)
        self._todos = []

    @staticmethod
    def _color(a, col):
        if col != 2:
            return None
        return {dom.ESTADO_ROTO: COLOR_ERROR, dom.ESTADO_ICONO: COLOR_AVISO, dom.ESTADO_OK: COLOR_OK}.get(a.estado, COLOR_APAGADO)

    def actualizar(self):
        for etiqueta, miniaturas in ((self.lbl_iconos, False), (self.lbl_mini, True)):
            n, b = dom.tamano_cache(miniaturas)
            que = "miniaturas" if miniaturas else "iconos"
            etiqueta.setText(f"Caché de {que}: {n} archivo(s) · {piezas.tamano_legible(b)}")
        self.host.set_status("🔄 Revisando accesos directos...")
        tareas.lanzar(self, "_tarea", dom.escanear_accesos, None, al_terminar=self._datos, com=True,
                      al_fallar=lambda m: self.host._avisar("Accesos directos", f"No se pudieron revisar:\n\n{m}", QMessageBox.Warning))

    def _datos(self, accesos):
        self.cargado = True
        self._todos = accesos
        self._pintar()
        malos = sum(1 for a in accesos if a.estado in (dom.ESTADO_ROTO, dom.ESTADO_ICONO))
        self.host.set_status(f"✅ {len(accesos)} accesos directos revisados, {malos} con problemas")

    def _pintar(self):
        solo_malos = self.filtro.currentIndex() == 0
        lista = [a for a in self._todos if not solo_malos or a.estado in (dom.ESTADO_ROTO, dom.ESTADO_ICONO)]
        self.modelo.set_datos([[a.nombre, a.ubicacion, a.estado, a.destino,
                                "(el del programa)" if not a.icono or a.icono.startswith(",") else a.icono] for a in lista], lista)
        self.recuento.setText(f"{len(lista)} de {len(self._todos)}")
        piezas.refrescar_export(self.host)

    def _reconstruir(self, miniaturas):
        que = "miniaturas" if miniaturas else "iconos"
        if not self.host._confirmar_accion(f"Caché de {que}", f"Se borra la caché de {que} y Windows la vuelve a crear.\n\n"
                                           f"{_AVISO_EXPLORADOR}\n\n¿Continuar?"):
            return
        self.host.set_status(f"🔄 Reconstruyendo la caché de {que}...")
        tareas.lanzar(self, "_tarea_cache", dom.reconstruir_cache, miniaturas,
                      al_terminar=lambda r: self._cache_hecha(que, r),
                      al_fallar=lambda m: self.host._avisar("Caché", m, QMessageBox.Warning))

    def _cache_hecha(self, que, resultado):
        ok, msg = resultado
        _registrar(self.host, f"Reconstruir caché de {que}", que, ok, msg)
        self.host.set_status(("✅ " if ok else "⚠️ ") + msg)
        self.host._avisar(f"Caché de {que}", msg, QMessageBox.Information if ok else QMessageBox.Warning)
        self.actualizar()

    def _sel(self):
        return objetos_seleccionados(self.tabla, self.proxy, self.modelo)

    def _abrir_ubicacion(self):
        sel = self._sel()
        if sel:
            subprocess.Popen(["explorer", "/select,", sel[0].ruta])

    def _quitar(self):
        sel = self._sel()
        if not sel:
            self.host._avisar("Quitar accesos directos", "Marca uno o varios accesos directos de la lista")
            return
        nombres = "\n".join(f"• {a.nombre} ({a.ubicacion})" for a in sel[:12]) + ("\n…" if len(sel) > 12 else "")
        if not self.host._confirmar_accion("Quitar accesos directos",
                                           f"Se envían a la Papelera {len(sel)} acceso(s) directo(s):\n\n{nombres}\n\n"
                                           "Se pueden recuperar desde la Papelera. ¿Continuar?"):
            return
        fallos = []
        for a in sel:
            ok, msg = dom.enviar_a_papelera(a.ruta)
            _registrar(self.host, "Quitar acceso directo", a.ruta, ok, msg)
            if not ok:
                fallos.append(f"{a.nombre}: {msg}")
        if fallos:
            self.host._avisar("Quitar accesos directos", "Algunos no se pudieron quitar (los del menú Inicio de todos los "
                              "usuarios necesitan administrador):\n\n" + "\n".join(fallos), QMessageBox.Warning)
        self.actualizar()

    def filas_export(self):
        return ("Accesos directos", self.modelo.HEADERS, self.modelo.filas())


class ConfiguracionIconosWidget(QScrollArea):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self.cargado = False
        self.setWidgetResizable(True)
        cuerpo = QWidget()
        self.setWidget(cuerpo)
        lay = QVBoxLayout(cuerpo)
        lay.setContentsMargins(5, 5, 5, 5)
        lay.setSpacing(10)
        if not is_admin():
            lay.addWidget(piezas.nota("Sin permisos de administrador: los iconos superpuestos y los de las unidades "
                                      "se pueden ver, pero no cambiar."))

        # Iconos del escritorio
        g, gl = piezas.grupo("🖥️ Iconos del escritorio")
        self.checks = {}
        widgets = []
        for clsid, nombre, _oc in dom.ICONOS_ESCRITORIO:
            c = QCheckBox(nombre)
            c.toggled.connect(lambda v, k=clsid, n=nombre: self._icono_escritorio(k, n, v))
            self.checks[clsid] = c
            widgets.append(c)
        gl.addLayout(piezas.fila(*widgets))
        lay.addWidget(g)

        # Espaciado
        g, gl = piezas.grupo("↔️ Espaciado de los iconos del escritorio")
        self.sp_h, self.sp_v = QSpinBox(), QSpinBox()
        for sp in (self.sp_h, self.sp_v):
            sp.setRange(dom.ESPACIADO_MIN, dom.ESPACIADO_MAX)
            sp.setSuffix(" px")
        gl.addLayout(piezas.fila(QLabel("Horizontal"), self.sp_h, QLabel("Vertical"), self.sp_v,
                                 piezas.boton("✔️ Aplicar", "primary", self._aplicar_espaciado),
                                 piezas.boton("↩️ Valores de Windows", "ghost", self._espaciado_defecto)))
        lay.addWidget(g)

        # Distribución guardada
        g, gl = piezas.grupo("📐 Posición de los iconos del escritorio")
        self.mod_dist = TablaSimple(["Nombre", "Fecha", "Equipo", "Archivo"])
        self.tab_dist, self.prx_dist = piezas.tabla(self.mod_dist, self, columna_ancha=3)
        self.tab_dist.setMinimumHeight(110)
        self.tab_dist.setMaximumHeight(170)
        gl.addLayout(piezas.fila(piezas.boton("💾 Guardar la actual…", "primary", self._guardar_dist),
                                 piezas.boton("↩️ Restaurar", "ghost", self._restaurar_dist),
                                 piezas.boton("✕ Quitar", "danger", self._quitar_dist)))
        gl.addWidget(self.tab_dist)
        lay.addWidget(g)

        # Overlays
        g, gl = piezas.grupo("🧩 Iconos superpuestos (OneDrive, Dropbox, Git...)")
        gl.addWidget(piezas.nota(f"Windows solo tiene {dom.PLAZAS_OVERLAY} plazas para estos iconos y reserva "
                                 f"{dom.PLAZAS_RESERVADAS}; las da por orden alfabético del nombre. Si a un programa no "
                                 "le salen sus iconos de sincronizado, dale prioridad."))
        self.mod_ov = TablaSimple(["Posición", "Nombre", "Programa", "Estado", "Archivo"], color=self._color_ov)
        self.tab_ov, self.prx_ov = piezas.tabla(self.mod_ov, self, columna_ancha=4)
        self.tab_ov.setMinimumHeight(170)
        gl.addLayout(piezas.fila(piezas.boton("⬆️ Dar prioridad", "primary", self._priorizar,
                                              "Lo pone el primero de la lista (copia del registro antes)"),
                                 piezas.boton("📂 Copias del registro", "ghost",
                                              lambda: (os.makedirs(carpeta_copias(), exist_ok=True), os.startfile(carpeta_copias())))))
        gl.addWidget(self.tab_ov)
        lay.addWidget(g)

        # Unidades
        g, gl = piezas.grupo("💽 Iconos personalizados de unidades")
        self.mod_un = TablaSimple(["Unidad", "Icono", "Nombre"])
        self.tab_un, self.prx_un = piezas.tabla(self.mod_un, self, columna_ancha=1)
        self.tab_un.setMinimumHeight(100)
        gl.addLayout(piezas.fila(piezas.boton("🎨 Poner icono a una unidad…", "primary", self._poner_icono_unidad),
                                 piezas.boton("✕ Quitar personalización", "danger", self._quitar_icono_unidad)))
        gl.addWidget(self.tab_un)
        lay.addWidget(g)
        lay.addStretch()

    # ── carga ──────────────────────────────────────────────────
    def actualizar(self):
        self.cargado = True
        vis = dom.iconos_escritorio_visibles()
        for clsid, c in self.checks.items():
            c.blockSignals(True)
            c.setChecked(vis.get(clsid, False))
            c.blockSignals(False)
        h, v = dom.espaciado_iconos()
        self.sp_h.setValue(h)
        self.sp_v.setValue(v)
        self._cargar_dist()
        self.overlays = dom.listar_overlays()
        self.mod_ov.set_datos([[o.posicion, "·" * (len(o.nombre) - len(o.nombre.lstrip(" "))) + o.nombre.lstrip(" "), o.programa,
                                o.estado, o.dll] for o in self.overlays], self.overlays)
        un = dom.iconos_unidades()
        self.mod_un.set_datos([[f"{l}:", i, e] for l, i, e in un], un)
        self.host.set_status("✅ Configuración de iconos leída")
        piezas.refrescar_export(self.host)

    @staticmethod
    def _color_ov(o, col):
        if col != 3:
            return None
        return {dom.OVERLAY_ACTIVO: COLOR_OK, dom.OVERLAY_RIESGO: COLOR_AVISO}.get(o.estado, COLOR_ERROR)

    def _cargar_dist(self):
        carpeta = os.path.join(carpeta_copias(), "ESCRITORIO")
        filas, objs = [], []
        for ruta in sorted(glob.glob(os.path.join(carpeta, "*.json")), key=os.path.getmtime, reverse=True):
            try:
                d = dom.leer_archivo_distribucion(ruta)
            except (OSError, ValueError):
                continue
            filas.append([d.get("nombre", ""), d.get("creado", ""), d.get("equipo", ""), os.path.basename(ruta)])
            objs.append(ruta)
        self.mod_dist.set_datos(filas, objs)

    # ── acciones ───────────────────────────────────────────────
    def _icono_escritorio(self, clsid, nombre, visible):
        ok, msg = dom.fijar_icono_escritorio(clsid, visible)
        _registrar(self.host, "Mostrar icono del escritorio" if visible else "Ocultar icono del escritorio", nombre, ok, msg)
        if ok:
            self.host.set_status(f"✅ «{nombre}» {'visible' if visible else 'oculto'} en el escritorio")
        else:
            self.host._avisar("Iconos del escritorio", f"No se pudo cambiar:\n\n{msg}", QMessageBox.Warning)

    def _espaciado_defecto(self):
        self.sp_h.setValue(dom.ESPACIADO_DEFECTO)
        self.sp_v.setValue(dom.ESPACIADO_DEFECTO)
        self._aplicar_espaciado()

    def _aplicar_espaciado(self):
        h, v = self.sp_h.value(), self.sp_v.value()
        if not self.host._confirmar_accion("Espaciado de iconos", f"Horizontal {h} px y vertical {v} px.\n\n"
                                           f"Para verlo hay que reiniciar el Explorador. {_AVISO_EXPLORADOR}\n\n¿Aplicar?"):
            return
        ok, msg = dom.fijar_espaciado(h, v)
        _registrar(self.host, "Espaciado de iconos", f"{h}×{v} px", ok, msg)
        if not ok:
            self.host._avisar("Espaciado de iconos", f"No se pudo cambiar:\n\n{msg}", QMessageBox.Warning)
            return
        dom.reiniciar_explorador()
        self.host.set_status(f"✅ Espaciado de iconos {h}×{v} px")

    def _guardar_dist(self):
        nombre, ok = QInputDialog.getText(self, "Guardar posición de los iconos", "Nombre (p. ej. «Trabajo», «Dos monitores»):")
        nombre = (nombre or "").strip()
        if not ok or not nombre:
            return
        ruta = os.path.join(carpeta_copias(), "ESCRITORIO", f"{dom.nombre_seguro(nombre)}_{time.strftime('%Y%m%d_%H%M%S')}.json")
        ok, msg = dom.guardar_distribucion(ruta, nombre)
        _registrar(self.host, "Guardar posición de iconos", nombre, ok, msg)
        if ok:
            self._cargar_dist()
            self.host.set_status(f"✅ Posición de los iconos guardada como «{nombre}»")
        else:
            self.host._avisar("Guardar posición", f"No se pudo guardar:\n\n{msg}", QMessageBox.Warning)

    def _dist_sel(self):
        return objeto_seleccionado(self.tab_dist, self.prx_dist, self.mod_dist)

    def _restaurar_dist(self):
        ruta = self._dist_sel()
        if not ruta:
            self.host._avisar("Restaurar posición", "Elige una posición guardada de la lista")
            return
        if not self.host._confirmar_accion("Restaurar posición de los iconos",
                                           f"Los iconos del escritorio vuelven a como estaban al guardar «{os.path.basename(ruta)}».\n\n"
                                           f"{_AVISO_EXPLORADOR}\n\n¿Continuar?"):
            return
        ok, msg = dom.restaurar_distribucion(ruta)
        _registrar(self.host, "Restaurar posición de iconos", ruta, ok, msg)
        if ok:
            self.host.set_status("✅ Posición de los iconos restaurada")
        else:
            self.host._avisar("Restaurar posición", f"No se pudo restaurar:\n\n{msg}", QMessageBox.Warning)

    def _quitar_dist(self):
        ruta = self._dist_sel()
        if ruta and self.host._confirmar_accion("Quitar posición guardada", f"¿Quitar «{os.path.basename(ruta)}»?"):
            ok, msg = dom.enviar_a_papelera(ruta)
            if not ok:
                self.host._avisar("Quitar", f"No se pudo quitar:\n\n{msg}", QMessageBox.Warning)
            self._cargar_dist()

    def _priorizar(self):
        o = objeto_seleccionado(self.tab_ov, self.prx_ov, self.mod_ov)
        if o is None:
            self.host._avisar("Iconos superpuestos", "Elige el icono superpuesto que debe ir primero")
            return
        if o.posicion == 1:
            self.host._avisar("Iconos superpuestos", "Ya es el primero de la lista")
            return
        if not self.host._confirmar_accion("Dar prioridad", f"«{o.nombre.strip()}» ({o.programa}) pasa a ser el primero.\n\n"
                                           "Antes se guarda una copia de esta parte del registro en BACKUPS\\ICONOS.\n"
                                           f"{_AVISO_EXPLORADOR}\n\n¿Continuar?"):
            return
        copia = os.path.join(carpeta_copias(), f"iconos_superpuestos_{time.strftime('%Y%m%d_%H%M%S')}.reg")
        ok, msg = dom.exportar_clave_reg(rf"HKLM\{dom.K_OVERLAYS}", copia)
        if not ok:
            self.host._avisar("Dar prioridad", f"No se pudo hacer la copia del registro, así que no se cambia nada:\n\n{msg}",
                              QMessageBox.Warning)
            return
        nuevo = dom.nombre_priorizado(o.nombre, self.overlays)
        ok, msg = dom.renombrar_overlay(o.nombre, nuevo)
        _registrar(self.host, "Dar prioridad a icono superpuesto", o.nombre.strip(), ok, msg or f"copia: {copia}")
        if not ok:
            self.host._avisar("Dar prioridad", f"No se pudo cambiar:\n\n{msg}", QMessageBox.Warning)
            return
        dom.reiniciar_explorador()
        self.actualizar()
        self.host.set_status(f"✅ «{o.nombre.strip()}» tiene prioridad. Copia del registro: {os.path.basename(copia)}")

    def _poner_icono_unidad(self):
        from msys.ui.iconos import SelectorIconoDialog
        letras = [f"{chr(c)}:" for c in range(ord("A"), ord("Z") + 1) if os.path.exists(f"{chr(c)}:\\")]
        letra, ok = QInputDialog.getItem(self, "Icono de unidad", "Unidad:", letras, 0, False)
        if not ok or not letra:
            return
        dlg = SelectorIconoDialog(self.host, self, dom.ruta_biblioteca("imageres.dll"))
        if dlg.exec_() != dlg.Accepted or not dlg.referencia():
            return
        ref = dlg.referencia()
        ok, msg = dom.fijar_icono_unidad(letra, ref)
        _registrar(self.host, "Icono de unidad", f"{letra} → {ref}", ok, msg)
        if ok:
            self.actualizar()
            self.host.set_status(f"✅ Icono de {letra} cambiado (se ve al abrir de nuevo «Este equipo»)")
        else:
            self.host._avisar("Icono de unidad", f"No se pudo cambiar:\n\n{msg}", QMessageBox.Warning)

    def _quitar_icono_unidad(self):
        u = objeto_seleccionado(self.tab_un, self.prx_un, self.mod_un)
        if u is None:
            self.host._avisar("Icono de unidad", "Elige una unidad de la lista")
            return
        letra = u[0]
        if not self.host._confirmar_accion("Quitar personalización", f"La unidad {letra}: vuelve al icono y nombre de Windows. ¿Continuar?"):
            return
        ok, msg = dom.quitar_icono_unidad(letra)
        _registrar(self.host, "Quitar icono de unidad", f"{letra}:", ok, msg)
        if ok:
            self.actualizar()
        else:
            self.host._avisar("Quitar personalización", f"No se pudo quitar:\n\n{msg}", QMessageBox.Warning)

    def filas_export(self):
        return [("Iconos superpuestos", self.mod_ov.HEADERS, self.mod_ov.filas()),
                ("Iconos personalizados de unidades", self.mod_un.HEADERS, self.mod_un.filas()),
                ("Posiciones guardadas del escritorio", self.mod_dist.HEADERS, self.mod_dist.filas())]
