# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: sección 🔗 Extensiones y programas (categoría Sistema).

Pestañas:
- 📄 Extensiones: qué programa abre cada extensión, de dónde sale la elección y
  si está rota. «Abrir con…» (diálogo de Windows), «Restablecer» (quitar la
  elección del usuario) y abrir la Configuración de Windows.
- 🌐 Protocolos: lo mismo para http, mailto, tel, teams...
- 📦 Por programa: cuántas extensiones abre cada programa.
Arriba: exportar / importar el XML de asociaciones de DISM.

Motor sin Qt: msys/domain/asociaciones.py (ahí se explica por qué no se fija
el predeterminado escribiendo en el registro)."""

import os
import time

from PyQt5.QtWidgets import QMessageBox, QVBoxLayout, QWidget

from msys.core import tareas
from msys.core.utils import dialogo_abrir_archivo, dialogo_guardar_archivo, get_informes_folder, is_admin
from msys.domain import asociaciones as dom
from msys.models.tabla_simple import (COLOR_APAGADO, COLOR_AVISO, COLOR_ERROR, COLOR_INFO, COLOR_OK, TablaSimple,
                                      objeto_seleccionado)
from msys.ui import piezas

SECCION = "🔗 Extensiones y programas"
FILTROS = ["Con programa", "Con problemas", "Elegidas por el usuario", "Sin programa", "Todas"]


def _color(a, col):
    if col == 4:
        return {dom.ESTADO_OK: COLOR_OK, dom.ESTADO_SIN: COLOR_APAGADO}.get(a.estado, COLOR_ERROR)
    if col == 3 and a.origen == dom.ORIGEN_USUARIO:
        return COLOR_INFO
    return None


def _pasa(a, filtro: int) -> bool:
    if filtro == 0:
        return a.estado != dom.ESTADO_SIN
    if filtro == 1:
        return a.estado in (dom.ESTADO_ROTA, dom.ESTADO_NO_ENCONTRADO)
    if filtro == 2:
        return a.origen == dom.ORIGEN_USUARIO
    if filtro == 3:
        return a.estado in (dom.ESTADO_SIN, dom.ESTADO_ROTA)
    return True


class _ListaAsociaciones(QWidget):
    """Tabla de extensiones o de protocolos con su barra de una fila."""

    def __init__(self, host, protocolos: bool):
        super().__init__()
        self.host, self.protocolos = host, protocolos
        self.cargado = False
        self._todas = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(5, 5, 5, 5)
        self.filtro = piezas.combo(FILTROS)
        self.filtro.currentIndexChanged.connect(lambda _i: self._pintar())
        botones = [self.filtro]
        if not protocolos:
            botones.append(piezas.boton("🪟 Abrir con…", "ghost", self._abrir_con,
                                        "Diálogo de Windows para elegir el programa (marca «Usar siempre»)"))
        botones += [piezas.boton("↩️ Restablecer", "ghost", self._restablecer,
                                 "Quita la elección del usuario: Windows vuelve a la suya o pregunta"),
                    piezas.boton("⚙️ Aplicaciones predeterminadas", "ghost", dom.abrir_configuracion,
                                 "Abre Configuración › Aplicaciones › Aplicaciones predeterminadas")]
        fila, self.busqueda, _a, self.recuento = piezas.barra_superior(
            "🔍 Buscar " + ("protocolo o programa..." if protocolos else "extensión, tipo o programa..."),
            botones, None, con_equipo=False)
        lay.addLayout(fila)
        cab = ["Protocolo" if protocolos else "Extensión", "Tipo de archivo" if not protocolos else "Identificador",
               "Abre con", "Origen", "Estado", "Programa"]
        self.modelo = TablaSimple(cab, color=_color,
                                  tooltip=lambda a, c: a.ejecutable if c in (2, 5) and a.ejecutable else None)
        self.tabla, self.proxy = piezas.tabla(self.modelo, self, columna_ancha=5, busqueda=self.busqueda)
        self.tabla.doubleClicked.connect(lambda _i: self._abrir_con() if not protocolos else None)
        lay.addWidget(self.tabla, 1)

    def actualizar(self):
        self.host.set_status("🔄 Leyendo " + ("protocolos..." if self.protocolos else "extensiones de archivo..."))
        funcion = dom.listar_protocolos if self.protocolos else dom.listar_extensiones
        tareas.lanzar(self, "_tarea", funcion, al_terminar=self._datos,
                      al_fallar=lambda m: self.host._avisar(SECCION, f"No se pudieron leer:\n\n{m}", QMessageBox.Warning))

    def _datos(self, lista):
        self.cargado = True
        self._todas = lista
        self._pintar()
        r = dom.resumen(lista)
        self.host.set_status(f"✅ {r['total']} {'protocolos' if self.protocolos else 'extensiones'}: {r['con_programa']} con "
                             f"programa, {r['usuario']} elegidas por el usuario, {r['problemas']} con problemas")
        piezas.refrescar_export(self.host)
        if hasattr(self.host, "_asoc_actualizar_por_programa"):
            self.host._asoc_actualizar_por_programa()

    def _pintar(self):
        f = self.filtro.currentIndex()
        lista = [a for a in self._todas if _pasa(a, f)]
        self.modelo.set_datos([[a.clave, a.progid if self.protocolos else a.tipo, a.programa, a.origen, a.estado,
                                a.ejecutable] for a in lista], lista)
        self.recuento.setText(f"{len(lista)} de {len(self._todas)}")

    def _sel(self):
        a = objeto_seleccionado(self.tabla, self.proxy, self.modelo)
        if a is None:
            self.host._avisar(SECCION, "Elige una fila de la lista")
        return a

    def _abrir_con(self):
        a = self._sel()
        if a is None:
            return
        ok, msg = dom.abrir_con(a.clave)
        if ok:
            self.host.set_status(f"🪟 Elige el programa para {a.clave} y marca «Usar siempre»; después pulsa 🔄 Actualizar")
        else:
            self.host._avisar("Abrir con", f"No se pudo abrir el diálogo de Windows:\n\n{msg}", QMessageBox.Warning)

    def _restablecer(self):
        a = self._sel()
        if a is None:
            return
        if a.origen != dom.ORIGEN_USUARIO and a.estado != dom.ESTADO_ROTA:
            self.host._avisar("Restablecer", f"{a.clave} no tiene una elección del usuario: ya usa la del sistema o del programa.")
            return
        if not self.host._confirmar_accion("Restablecer", f"{a.clave} deja de abrirse con «{a.programa or 'el programa elegido'}» "
                                           "y vuelve a lo que tenga Windows (o pregunta la próxima vez). ¿Continuar?"):
            return
        ok, msg = dom.restablecer(a.clave, self.protocolos)
        if hasattr(self.host, "repair_audit_db"):
            self.host.repair_audit_db.registrar(seccion=SECCION, accion="Restablecer asociación", objetivo=a.clave,
                                                exito=ok, detalle=msg or "OK")
        if ok:
            self.host.set_status(f"✅ {a.clave} restablecida")
            nueva = dom.consultar(a.clave, self.protocolos)
            self._todas = [nueva if x.clave == a.clave else x for x in self._todas]
            self._pintar()
        else:
            self.host._avisar("Restablecer", msg, QMessageBox.Warning)

    def filas_export(self):
        return ("Protocolos" if self.protocolos else "Extensiones de archivo", self.modelo.HEADERS, self.modelo.filas())


class _PorPrograma(QWidget):
    def __init__(self, host):
        super().__init__()
        self.host = host
        self.cargado = True  # se rellena con lo que ya tengan las otras pestañas
        lay = QVBoxLayout(self)
        lay.setContentsMargins(5, 5, 5, 5)
        fila, self.busqueda, _a, self.recuento = piezas.barra_superior("🔍 Buscar programa o extensión...", [], None,
                                                                       con_equipo=False)
        lay.addLayout(fila)
        self.modelo = TablaSimple(["Programa", "Nº", "Extensiones y protocolos", "Programa (ruta)"])
        self.tabla, self.proxy = piezas.tabla(self.modelo, self, columna_ancha=2, busqueda=self.busqueda, orden=(1, False))
        lay.addWidget(self.tabla, 1)
        lay.addWidget(piezas.nota("Se calcula con lo leído en Extensiones y Protocolos. Para cambiar el programa de una "
                                  "extensión: pestaña Extensiones › Abrir con…"))

    def actualizar(self, asociaciones=None):
        if asociaciones is None:
            return
        grupos = dom.por_programa(asociaciones)
        self.modelo.set_datos([[p, f"{len(l):>4}", ", ".join(l), e] for p, e, l in grupos], grupos)
        self.recuento.setText(f"{len(grupos)} programas")

    def filas_export(self):
        return ("Programas predeterminados", self.modelo.HEADERS, self.modelo.filas())


class AsociacionesMixin:
    def crear_tab_asociaciones(self):
        tab = piezas.PaginaPerezosa(lambda: self._asoc_cargar(False))
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)
        fila, _b, _act, _r = piezas.barra_superior(None, [
            piezas.boton("📤 Exportar XML…", "ghost", self._asoc_exportar_xml,
                         "Asociaciones de este equipo en el formato de DISM (para otro equipo o una GPO)"),
            piezas.boton("📥 Importar XML…", "ghost", self._asoc_importar_xml,
                         "Se aplica a los usuarios nuevos o al iniciar sesión (necesita administrador)"),
        ], lambda: self._asoc_cargar(True))
        layout.addLayout(fila)
        self.asoc_tabs = piezas.pestanas()
        self.asoc_ext = _ListaAsociaciones(self, protocolos=False)
        self.asoc_proto = _ListaAsociaciones(self, protocolos=True)
        self.asoc_prog = _PorPrograma(self)
        self.asoc_tabs.addTab(self.asoc_ext, "📄 Extensiones")
        self.asoc_tabs.addTab(self.asoc_proto, "🌐 Protocolos")
        self.asoc_tabs.addTab(self.asoc_prog, "📦 Por programa")
        self.asoc_tabs.currentChanged.connect(lambda _i: (self._asoc_cargar(False), self._actualizar_barra_exportacion()))
        layout.addWidget(self.asoc_tabs, 1)
        self.registrar_export(SECCION, self._asoc_export)
        return tab

    def _asoc_cargar(self, forzar: bool):
        w = self.asoc_tabs.currentWidget()
        if w is self.asoc_prog:
            for lista in (self.asoc_ext, self.asoc_proto):
                if forzar or not lista.cargado:
                    lista.actualizar()
            self._asoc_actualizar_por_programa()
        elif forzar or not w.cargado:
            w.actualizar()

    def _asoc_actualizar_por_programa(self):
        self.asoc_prog.actualizar(self.asoc_ext._todas + self.asoc_proto._todas)

    def _asoc_export(self):
        w = self.asoc_tabs.currentWidget() if hasattr(self, "asoc_tabs") else None
        return [w.filas_export()] if w is not None else []

    def _asoc_exportar_xml(self):
        ruta = dialogo_guardar_archivo(self, "Exportar asociaciones",
                                       os.path.join(get_informes_folder(), f"Asociaciones_{time.strftime('%Y%m%d_%H%M%S')}.xml"),
                                       "XML (*.xml)")
        if not ruta:
            return
        ok, msg = dom.exportar_xml(ruta)
        self.repair_audit_db.registrar(seccion=SECCION, accion="Exportar XML de asociaciones", objetivo=ruta, exito=ok,
                                       detalle=msg if not ok else "OK")
        if ok:
            self._avisar("Exportar XML", f"Asociaciones guardadas en:\n\n{ruta}")
        else:
            self._avisar("Exportar XML", f"No se pudo exportar:\n\n{msg}", QMessageBox.Warning)

    def _asoc_importar_xml(self):
        if not is_admin():
            self._avisar("Importar XML", "Hace falta ejecutar MASys como administrador.", QMessageBox.Warning)
            return
        ruta = dialogo_abrir_archivo(self, "Importar asociaciones", get_informes_folder(), "XML (*.xml)")
        if not ruta:
            return
        if not self._confirmar_accion("Importar XML", "Windows aplica este XML a los usuarios NUEVOS y a cada usuario la "
                                      "próxima vez que inicie sesión (no cambia al instante las elecciones ya hechas).\n\n"
                                      "Se deshace con DISM /Remove-DefaultAppAssociations. ¿Importar?"):
            return
        ok, msg = dom.importar_xml(ruta)
        self.repair_audit_db.registrar(seccion=SECCION, accion="Importar XML de asociaciones", objetivo=ruta, exito=ok,
                                       detalle=msg if not ok else "OK")
        self._avisar("Importar XML", "Importado. Se aplica al iniciar sesión." if ok else f"No se pudo importar:\n\n{msg}",
                     QMessageBox.Information if ok else QMessageBox.Warning)

