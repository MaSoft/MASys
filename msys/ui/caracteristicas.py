# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: sección 🧩 Características de Windows (categoría Sistema).

Una sola tabla con las características opcionales y las capacidades de
Windows juntas (nombre en español, categoría, estado y aviso de seguridad),
activar / quitar, perfiles (varias de golpe) e instalar .NET 3.5 desde el
medio de instalación cuando Windows Update falla.

Motor sin Qt: msys/domain/caracteristicas.py."""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QDialog, QLabel, QListWidget, QMessageBox, QSplitter, QVBoxLayout, QWidget

from msys.core import tareas
from msys.core.utils import dialogo_elegir_carpeta, is_admin
from msys.domain import caracteristicas as dom
from msys.models.tabla_simple import COLOR_APAGADO, COLOR_AVISO, COLOR_ERROR, COLOR_OK, TablaSimple, objetos_seleccionados
from msys.ui import piezas

SECCION = "🧩 Características de Windows"
FILTROS = ["Sin idiomas ni controladores", "Activadas", "Desactivadas", "Con aviso de seguridad",
           "Solo características", "Solo capacidades", "Idiomas", "Controladores", "Todas"]


def _color(c, col):
    if col == 2:
        return {dom.ACTIVADA: COLOR_OK, dom.PENDIENTE: COLOR_AVISO, dom.NO_DISPONIBLE: COLOR_APAGADO}.get(c.estado)
    if col == 5 and c.aviso:
        return COLOR_ERROR
    return None


def _pasa(c, f: int) -> bool:
    if f == 0:
        return c.categoria not in (dom.CAT_IDIOMAS, dom.CAT_CONTROLADORES)
    return [None, c.estado == dom.ACTIVADA, c.estado == dom.DESACTIVADA, bool(c.aviso), c.tipo == dom.TIPO_CARACTERISTICA,
            c.tipo == dom.TIPO_CAPACIDAD, c.categoria == dom.CAT_IDIOMAS, c.categoria == dom.CAT_CONTROLADORES, True][f]


class PerfilesDialog(QDialog):
    """Elegir un perfil, ver exactamente qué cambiaría y aplicarlo."""

    def __init__(self, host, lista, parent=None):
        super().__init__(parent)
        self.host, self.lista = host, lista
        self.setWindowTitle("Perfiles de características")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.resize(900, 520)
        lay = QVBoxLayout(self)
        split = QSplitter(Qt.Horizontal)
        self.perfiles = QListWidget()
        for p in dom.PERFILES:
            self.perfiles.addItem(p.nombre)
        self.perfiles.currentRowChanged.connect(self._ver)
        split.addWidget(self.perfiles)
        self.detalle = QLabel()
        self.detalle.setWordWrap(True)
        self.detalle.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.detalle.setTextFormat(Qt.RichText)
        split.addWidget(self.detalle)
        split.setSizes([280, 620])
        lay.addWidget(split, 1)
        self.btn = piezas.boton("✔️ Aplicar este perfil", "primary", self.accept)
        lay.addLayout(piezas.fila(None, piezas.boton("Cancelar", "ghost", self.reject), self.btn, estirar=False))
        self.cambios = []
        self.perfiles.setCurrentRow(0)

    def _ver(self, fila):
        p = dom.PERFILES[fila]
        self.cambios, ya, faltan = dom.plan_perfil(p, self.lista)
        activar = [c.nombre for c, a in self.cambios if a]
        quitar = [c.nombre for c, a in self.cambios if not a]

        def bloque(titulo, elementos, color):
            if not elementos:
                return ""
            return (f"<p style='margin-top:10px'><b style='color:{color}'>{titulo}</b><br>"
                    + "<br>".join(f"• {e}" for e in elementos) + "</p>")
        self.detalle.setText(f"<p style='font-size:11pt'><b>{p.nombre}</b><br>{p.descripcion}</p>"
                             + bloque("Se activarán", activar, COLOR_OK) + bloque("Se quitarán", quitar, COLOR_ERROR)
                             + bloque("Ya están como pide el perfil", ya, COLOR_APAGADO)
                             + bloque("No existen en esta versión de Windows", faltan, COLOR_APAGADO)
                             + ("" if self.cambios else "<p><b>Nada que cambiar.</b></p>"))
        self.btn.setEnabled(bool(self.cambios))


class CaracteristicasMixin:
    def crear_tab_caracteristicas(self):
        tab = piezas.PaginaPerezosa(lambda: self.refresh_caracteristicas())
        lay = QVBoxLayout(tab)
        lay.setSpacing(8)
        lay.setContentsMargins(10, 10, 10, 10)
        self.carac_filtro = piezas.combo(FILTROS)
        self.carac_filtro.currentIndexChanged.connect(lambda _i: self._carac_pintar())
        self.carac_botones = [
            piezas.boton("✔️ Activar", "ghost", lambda: self._carac_cambiar(True)),
            piezas.boton("✕ Quitar", "danger", lambda: self._carac_cambiar(False)),
            piezas.boton("📋 Perfiles…", "ghost", self._carac_perfiles, "Varias de golpe: soporte IT, desarrollo, endurecer…"),
            piezas.boton("💿 .NET 3.5 sin internet…", "ghost", self._carac_netfx3,
                         "Desde la carpeta \\sources\\sxs de un USB o ISO de Windows, cuando Windows Update falla"),
        ]
        fila, self.carac_busqueda, self.btn_refresh_carac, self.carac_recuento = piezas.barra_superior(
            "🔍 Buscar característica...", [self.carac_filtro] + self.carac_botones, self.refresh_caracteristicas)
        lay.addLayout(fila)
        self.carac_aviso = piezas.nota("")
        self.carac_aviso.setVisible(False)
        lay.addWidget(self.carac_aviso)
        self.carac_modelo = TablaSimple(["Nombre", "Tipo", "Estado", "Categoría", "Nombre técnico", "Aviso"], color=_color)
        self.carac_tabla, self.carac_proxy = piezas.tabla(self.carac_modelo, self, columna_ancha=0, seleccion_multiple=True,
                                                         busqueda=self.carac_busqueda, orden=(0, True))
        lay.addWidget(self.carac_tabla, 1)
        self._carac_lista, self._carac_info = [], {}
        self.registrar_export(SECCION, lambda: [(SECCION, self.carac_modelo.HEADERS, self.carac_modelo.filas())])
        return tab

    def refresh_caracteristicas(self):
        self.set_status("🔄 Leyendo las características de Windows...")
        self.btn_refresh_carac.setEnabled(False)
        tareas.lanzar(self, "_carac_tarea", dom.leer_todo, al_terminar=self._carac_datos,
                      al_fallar=lambda m: (self.btn_refresh_carac.setEnabled(True),
                                           self._avisar(SECCION, f"No se pudieron leer:\n\n{m}", QMessageBox.Warning)))

    def _carac_datos(self, info):
        self.btn_refresh_carac.setEnabled(True)
        self._carac_info = info
        self._carac_lista = info["lista"]
        avisos = []
        if info.get("reinicio"):
            avisos.append("Windows tiene un reinicio pendiente: algunos cambios no se verán hasta reiniciar.")
        if not info.get("capacidades_leidas"):
            avisos.append("Sin permisos de administrador: solo se ven las características (no las capacidades como RSAT "
                          "u OpenSSH) y no se puede cambiar nada.")
        self.carac_aviso.setText("  ".join(avisos))
        self.carac_aviso.setVisible(bool(avisos))
        self._carac_pintar()
        activas = sum(1 for c in self._carac_lista if c.estado == dom.ACTIVADA)
        con_aviso = sum(1 for c in self._carac_lista if c.aviso)
        self.set_status(f"✅ {len(self._carac_lista)} características y capacidades: {activas} activadas"
                        + (f", ⚠️ {con_aviso} con aviso de seguridad" if con_aviso else ""))
        piezas.refrescar_export(self)

    def _carac_pintar(self):
        f = self.carac_filtro.currentIndex()
        lista = [c for c in self._carac_lista if _pasa(c, f)]
        self.carac_modelo.set_datos([[c.nombre, c.tipo, c.estado, c.categoria, c.tecnico, c.aviso] for c in lista], lista)
        self.carac_recuento.setText(f"{len(lista)} de {len(self._carac_lista)}")

    def _carac_ocupado(self, ocupado: bool):
        for b in self.carac_botones + [self.btn_refresh_carac]:
            b.setEnabled(not ocupado)

    def _carac_cambiar(self, activar: bool):
        sel = [c for c in objetos_seleccionados(self.carac_tabla, self.carac_proxy, self.carac_modelo)
               if c.estado != dom.NO_DISPONIBLE and (c.estado == dom.ACTIVADA) != activar]
        if not sel:
            self._avisar(SECCION, "Marca una o varias de la lista que estén " + ("desactivadas." if activar else "activadas."))
            return
        if not is_admin():
            self._avisar(SECCION, "Hace falta ejecutar MASys como administrador.", QMessageBox.Warning)
            return
        nombres = "\n".join(f"• {c.nombre}" for c in sel[:15]) + ("\n…" if len(sel) > 15 else "")
        texto = (f"Se van a {'activar' if activar else 'quitar'} {len(sel)}:\n\n{nombres}\n\n"
                 "Puede tardar varios minutos (algunas se descargan de Windows Update) y quizá pida reiniciar.")
        if not activar:
            texto += "\n\nLo que use alguna de ellas dejará de funcionar."
        if not self._confirmar_accion(SECCION, texto + "\n\n¿Continuar?"):
            return
        self._carac_aplicar([(c, activar) for c in sel])

    def _carac_aplicar(self, cambios, origen=""):
        self._carac_ocupado(True)
        self.set_status(f"⏳ Aplicando {len(cambios)} cambio(s)... no cierres MASys")

        def trabajo():
            if origen:
                return [(c, a, *dom.cambiar(c, a, origen)) for c, a in cambios]
            return dom.aplicar_plan(cambios, progreso=lambda p, m: None)
        tareas.lanzar(self, "_carac_tarea_cambio", trabajo, al_terminar=self._carac_hecho,
                      al_fallar=lambda m: (self._carac_ocupado(False), self._avisar(SECCION, m, QMessageBox.Warning)))

    def _carac_hecho(self, resultados):
        self._carac_ocupado(False)
        bien, mal, reinicio = [], [], False
        for c, activar, ok, pide, msg in resultados:
            self.repair_audit_db.registrar(seccion=SECCION, accion="Activar característica" if activar else "Quitar característica",
                                           objetivo=c.tecnico, exito=ok, detalle=msg or ("reinicio pendiente" if pide else "OK"))
            (bien if ok else mal).append(f"• {c.nombre}" + ("" if ok else f": {msg}"))
            reinicio = reinicio or pide
        texto = ""
        if bien:
            texto += f"Hecho ({len(bien)}):\n" + "\n".join(bien)
        if mal:
            texto += ("\n\n" if texto else "") + f"No se pudo ({len(mal)}):\n" + "\n".join(mal)
        if reinicio:
            texto += "\n\n🔁 Windows pide reiniciar para terminar."
        self._avisar(SECCION, texto, QMessageBox.Warning if mal else QMessageBox.Information)
        self.refresh_caracteristicas()

    def _carac_perfiles(self):
        if not self._carac_lista:
            self._avisar(SECCION, "Pulsa 🔄 Actualizar primero.")
            return
        if not is_admin():
            self._avisar(SECCION, "Hace falta ejecutar MASys como administrador.", QMessageBox.Warning)
            return
        dlg = PerfilesDialog(self, self._carac_lista, self)
        if dlg.exec_() == QDialog.Accepted and dlg.cambios:
            if self._confirmar_accion(SECCION, f"Se aplican {len(dlg.cambios)} cambio(s). Puede tardar varios minutos. ¿Continuar?"):
                self._carac_aplicar(dlg.cambios)

    def _carac_netfx3(self):
        if not is_admin():
            self._avisar(SECCION, "Hace falta ejecutar MASys como administrador.", QMessageBox.Warning)
            return
        netfx = next((c for c in self._carac_lista if c.tipo == dom.TIPO_CARACTERISTICA and c.tecnico.lower() == "netfx3"), None)
        if netfx is not None and netfx.estado == dom.ACTIVADA:
            self._avisar(SECCION, ".NET Framework 3.5 ya está activado en este equipo.")
            return
        carpeta = dialogo_elegir_carpeta(self, "Carpeta \\sources\\sxs del USB o ISO de Windows", "")
        if not carpeta:
            return
        if not dom.origen_netfx3_valido(carpeta):
            self._avisar(SECCION, "En esa carpeta no está el paquete de .NET 3.5 (microsoft-windows-netfx3-ondemand-package…cab).\n\n"
                         "Es la carpeta sources\\sxs de un USB o ISO de la MISMA versión de Windows.", QMessageBox.Warning)
            return
        if netfx is None:
            netfx = dom.Caracteristica(dom.TIPO_CARACTERISTICA, "NetFx3", ".NET Framework 3.5", dom.DESACTIVADA, dom.CAT_NET)
        if self._confirmar_accion(SECCION, f"Instalar .NET Framework 3.5 desde:\n\n{carpeta}\n\nsin usar Windows Update. ¿Continuar?"):
            self._carac_aplicar([(netfx, True)], origen=carpeta)
