# -*- coding: utf-8 -*-
"""📱 Móviles Android (5.3.0): ventana propia, NO modal y única, con el botón bajo el árbol entre «🖥️ Acceso remoto»
y «⚙️ Configuración y perfiles» (antes, 4.1.5–5.2.0, era la sección «📱 Móvil Android» del árbol, en Hardware).

Recibe los informes de MASysMov por las dos vías acordadas:
- Wi-Fi: «📲 Emparejar móvil…» pide antes la empresa y el usuario y enseña un QR; con «Recibir del móvil» marcado,
  MASys escucha en el puerto 47880 (solo red local, todo cifrado) AUNQUE la ventana esté cerrada.
- Archivo: «📥 Importar archivo…» abre un .msysmov exportado en el móvil (cifrado con contraseña).

Izquierda: árbol Empresa › Usuario › Terminal (acordeón: al abrir una empresa o un usuario se pliega el que estaba
abierto); los móviles sin empresa van a «Sin asignar» (clic derecho › «👥 Asignar a empresa y usuario…»).
Debajo del árbol, la lista «Informes» del móvil elegido (fecha · vía de cada envío, como en 5.2.0).
Derecha: las pestañas (Ficha, Seguridad, Apps, Conexiones, Evolución, Cambios,
Espacio, Batería, Rastreadores, Procesos, Hardware, Red, Consumo de datos, Pruebas, Datos personales y Copias).
«🚪 Puertos desde fuera»: ventana aparte con los pedidos de TODOS los móviles (el primer móvil emparejado que envíe
su informe con datos móviles hace la prueba). «Datos personales»: el técnico elige si el móvil manda contactos,
llamadas y SMS (nada, solo recuentos o todo). Motor sin Qt: msys/movil/."""

import csv
import os
import time
from typing import Dict, List, Optional, Tuple

from PyQt5.QtCore import QObject, QSettings, Qt, QTimer, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices, QPixmap
from PyQt5.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
                             QListWidget, QMenu, QMessageBox, QSplitter, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from msys.core.utils import (dialogo_abrir_archivo, dialogo_elegir_carpeta, dialogo_guardar_archivo, get_informes_folder,
                              is_admin, ruta_guardado_predeterminada)
from msys.domain import clientes as cl
from msys.movil import analisis as an
from msys.movil import copias as cp
from msys.movil import cripto, servidor
from msys.movil.almacen import (ENVIADO, HECHO, NIVEL_COMPLETO, NIVEL_NO, NIVEL_RECUENTOS, PENDIENTE, SIN_MOVIL, Almacen)
from msys.models.tabla_simple import COLOR_AVISO, COLOR_ERROR, COLOR_INFO, COLOR_OK, TablaSimple, objeto_seleccionado
from msys.ui import piezas

TITULO = "📱 Móviles Android"
PUERTOS = "🚪 Puertos desde fuera"
SIN_ASIGNAR = "Sin asignar"
_COLORES = {an.OK: COLOR_OK, an.AVISO: COLOR_AVISO, an.ERROR: COLOR_ERROR, an.INFO: COLOR_INFO}
CLAVE_ENLACE = "movil/recibir"
NIVELES = [(NIVEL_NO, "No recibir"), (NIVEL_RECUENTOS, "Solo recuentos"), (NIVEL_COMPLETO, "Completo (contactos, llamadas y SMS)")]

_ROL_TIPO = Qt.UserRole          # "empresa" | "usuario" | "movil"
_ROL_ID = Qt.UserRole + 1        # id del móvil, o (empresa, usuario) del grupo


class _Puente(QObject):
    """Lleva los avisos del hilo del servidor al hilo de la interfaz."""
    recibido = pyqtSignal(str, int)
    emparejado = pyqtSignal(str, str)
    resultado = pyqtSignal(str, str)
    copia = pyqtSignal(str)


def qr_pixmap(texto: str) -> QPixmap:
    from io import BytesIO
    import qrcode
    img = qrcode.make(texto, box_size=8, border=2)
    buf = BytesIO()
    img.save(buf, format="PNG")
    pm = QPixmap()
    pm.loadFromData(buf.getvalue())
    return pm


def _caja(padre, titulo: str, texto: str, icono=QMessageBox.Information) -> QMessageBox:
    caja = QMessageBox(padre)
    caja.setWindowFlags(caja.windowFlags() & ~Qt.WindowContextHelpButtonHint)
    caja.setWindowTitle(titulo)
    caja.setText(texto)
    caja.setIcon(icono)
    return caja


def _colores(host) -> Dict[str, str]:
    base = {"title": "#1a2634", "muted": "#8a94a6"}
    if host is not None and hasattr(host, "_dash_colors"):
        try:
            base.update(host._dash_colors())
        except Exception:
            pass
    return base


def _tamano(b: int) -> str:
    v = float(b)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if v < 1024 or u == "TB":
            return f"{v:.0f} {u}" if u == "B" else f"{v:.1f} {u}".replace(".", ",")
        v /= 1024
    return str(b)


def _html(texto) -> str:
    import html
    return html.escape(str(texto or ""))


def _estado_puerto(estado) -> str:
    """«Sin respuesta (filtrado)» → «filtrado»: el resultado de varios puertos cabe en una celda."""
    t = str(estado or "")
    if t.startswith("Sin respuesta"):
        return "filtrado"
    if t.startswith("Error"):
        return "error"
    return t.lower()


def nombre_movil(d: dict) -> str:
    return d.get("nombre") or d.get("modelo") or d.get("id", "")


# ── Diálogos pequeños ──────────────────────────────────────────────────
class EmparejarDialog(QDialog):
    def __init__(self, sesion: dict, puente: _Puente, parent=None, quien: str = ""):
        super().__init__(parent)
        import json
        self.setWindowTitle("Emparejar móvil")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        lay = QVBoxLayout(self)
        lay.setSpacing(12)
        titulo = QLabel("En MASysMov: Ajustes › Enlace con MASys › Emparejar con el PC, y apunta a este código")
        titulo.setWordWrap(True)
        titulo.setStyleSheet("font-size: 11pt; font-weight: 600;")
        lay.addWidget(titulo)
        if quien:
            lay.addWidget(piezas.nota(f"El móvil quedará en {quien}."))
        qr = QLabel()
        qr.setAlignment(Qt.AlignCenter)
        qr.setPixmap(qr_pixmap(json.dumps(sesion, separators=(",", ":"))).scaled(340, 340, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        qr.setStyleSheet("background: white; border-radius: 8px; padding: 10px;")
        lay.addWidget(qr)
        self.estado = piezas.nota(f"Vale 10 minutos y para un solo móvil. El móvil tiene que estar en la misma Wi-Fi que este PC "
                                  f"({', '.join(sesion['ips'])}).")
        lay.addWidget(self.estado)
        lay.addLayout(piezas.fila(None, piezas.boton("Cerrar", "ghost", self.reject), estirar=False))
        puente.emparejado.connect(self._hecho)

    def _hecho(self, _id, nombre):
        self.estado.setText(f"✅ Emparejado con «{nombre}». Ya puede enviar informes.")
        self.estado.setStyleSheet("color: #16a34a; font-weight: 600;")


class AsignarDialog(QDialog):
    """Empresa y usuario de un móvil, con el catálogo 👥 Empresas y usuarios (5.0.0): se elige de la lista o se escribe.
    Si no está en el catálogo, pregunta si se añade; con «No» se guarda igual (como la agenda de Acceso remoto)."""

    def __init__(self, padre, clientes: cl.ClientesDB, titulo: str, empresa: str = "", usuario: str = ""):
        super().__init__(padre)
        self.clientes = clientes
        self.resultado: Optional[Tuple[str, str]] = None
        self.setWindowTitle(titulo)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.setSpacing(6)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.addWidget(piezas.nota("Elige de la lista o escribe. Los dos son obligatorios."))
        lay.addWidget(QLabel("Empresa"))
        self.ed_empresa = QComboBox()
        self.ed_empresa.setEditable(True)
        self.ed_empresa.setInsertPolicy(QComboBox.NoInsert)
        self.ed_empresa.lineEdit().setPlaceholderText("Elige de la lista o escríbela")
        lay.addWidget(self.ed_empresa)
        lay.addWidget(QLabel("Usuario"))
        self.ed_usuario = QComboBox()
        self.ed_usuario.setEditable(True)
        self.ed_usuario.setInsertPolicy(QComboBox.NoInsert)
        self.ed_usuario.lineEdit().setPlaceholderText("Elige de la lista o escríbelo")
        lay.addWidget(self.ed_usuario)
        lay.addSpacing(6)
        self.btn_guardar = piezas.boton("💾 Guardar", "primary", self.guardar)
        lay.addLayout(piezas.fila(None, piezas.boton("Cancelar", "ghost", self.reject), self.btn_guardar, estirar=False))
        try:
            empresas = [e.nombre for e in self.clientes.empresas()]
        except Exception:
            empresas = []
        self.ed_empresa.addItems(empresas)
        self.ed_empresa.setCurrentText(empresa)
        self.ed_empresa.currentTextChanged.connect(lambda _t: self._cargar_usuarios())
        self._cargar_usuarios(usuario)

    def _cargar_usuarios(self, actual: Optional[str] = None):
        texto = self.ed_usuario.currentText() if actual is None else actual
        nombres = []
        try:
            e = self.clientes.empresa(cl.limpiar(self.ed_empresa.currentText()))
            nombres = [u.nombre for u in self.clientes.usuarios(e.id)] if e else []
        except Exception:
            pass
        self.ed_usuario.blockSignals(True)
        self.ed_usuario.clear()
        self.ed_usuario.addItems(nombres)
        self.ed_usuario.setCurrentText(texto)
        self.ed_usuario.blockSignals(False)

    def _confirmar(self, texto: str) -> bool:
        caja = _caja(self, self.windowTitle(), texto, QMessageBox.Question)
        caja.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        return caja.exec_() == QMessageBox.Yes

    def _aviso(self, texto: str):
        _caja(self, self.windowTitle(), texto, QMessageBox.Warning).exec_()

    def guardar(self):
        empresa, usuario = cl.limpiar(self.ed_empresa.currentText()), cl.limpiar(self.ed_usuario.currentText())
        if not empresa or not usuario:
            self._aviso("Faltan la empresa o el usuario.")
            return
        try:
            existe = self.clientes.usuario(empresa, usuario) is not None
        except Exception:
            existe = True       # sin catálogo legible no se pregunta
        if not existe and self._confirmar(f"«{usuario}» de «{empresa}» no está en 👥 Empresas y usuarios. ¿Añadirlo?"):
            try:
                u = self.clientes.asegurar(empresa, usuario)
                empresa, usuario = u.empresa or empresa, u.nombre
            except ValueError as e:
                self._aviso(str(e))
                return
        self.resultado = (empresa, usuario)
        self.accept()


# ── Ventana de Móviles ────────────────────────────────────────────────
class MovilesDialog(QDialog):
    def __init__(self, host, clientes: Optional[cl.ClientesDB] = None):
        super().__init__(host)
        self.host = host
        self.alm: Almacen = host.movil_almacen
        self.srv: servidor.ServidorMovil = host.movil_servidor
        self.puente: _Puente = host.movil_puente
        self.clientes = clientes if clientes is not None else cl.ClientesDB()
        self.setWindowTitle(TITULO)
        self.setWindowFlags((self.windowFlags() | Qt.WindowMinMaxButtonsHint) & ~Qt.WindowContextHelpButtonHint)
        self.setModal(False)
        self.setMinimumSize(900, 560)
        self._disps: List[dict] = []
        self._regs: List[dict] = []
        self._actual: Optional[str] = None
        self._pendiente_asignar: Optional[Tuple[str, str]] = None

        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        lay.setContentsMargins(18, 16, 18, 14)

        cab = QHBoxLayout()
        cab.setSpacing(12)
        icono = QLabel("📱")
        icono.setStyleSheet("font-size: 22pt; background: transparent; border: none;")
        cab.addWidget(icono)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.lbl_titulo = QLabel("Móviles Android")
        self.lbl_sub = QLabel("Informes de MASysMov por empresa y usuario")
        col.addWidget(self.lbl_titulo)
        col.addWidget(self.lbl_sub)
        cab.addLayout(col, 1)
        self.btn_empresas = piezas.boton("👥 Empresas", "ghost", self.abrir_empresas, "Empresas y usuarios de tus clientes")
        self.btn_puertos = piezas.boton(PUERTOS, "ghost", self.abrir_puertos,
                                        "Comprobar si un puerto se ve desde Internet: lo hace el primer móvil con datos móviles")
        cab.addWidget(self.btn_empresas, 0, Qt.AlignVCenter)
        cab.addWidget(self.btn_puertos, 0, Qt.AlignVCenter)
        lay.addLayout(cab)

        self.chk_recibir = QCheckBox("Recibir del móvil")
        self.chk_recibir.setToolTip(f"MASys escucha en el puerto {servidor.PUERTO} de la red local mientras está abierto "
                                    "(aunque esta ventana esté cerrada)")
        self.chk_recibir.setChecked(self.srv.activo)
        self.chk_recibir.toggled.connect(self._enlace)
        self.btn_emparejar = piezas.boton("📲 Emparejar móvil…", "ghost", self.emparejar)
        self.btn_importar = piezas.boton("📥 Importar archivo…", "ghost", self.importar, "Un informe .msysmov exportado en MASysMov")
        self.btn_html = piezas.boton("🌐 Informe HTML…", "ghost", self.informe_html)
        self.btn_csv = piezas.boton("📄 CSV…", "ghost", self.exportar_csv, "La tabla de la pestaña que se ve")
        self.btn_quitar = piezas.boton("✕ Quitar móvil", "danger", self.quitar, "Borra el móvil y todos sus informes de este PC")
        fila, _b, self.btn_actualizar, self.recuento = piezas.barra_superior(
            None, [self.chk_recibir, self.btn_emparejar, self.btn_importar, self.btn_html, self.btn_csv, self.btn_quitar],
            lambda: self.recargar(), con_equipo=False)
        lay.addLayout(fila)

        self.aviso = piezas.nota("")
        self.cmb_datos = piezas.combo([t for _k, t in NIVELES], [k for k, _t in NIVELES])
        self.cmb_datos.setToolTip("Lo que manda el móvil de contactos, llamadas y SMS en cada envío por Wi-Fi")
        try:
            nivel = self.alm.nivel_datos()
        except Exception:
            nivel = NIVEL_NO
        self.cmb_datos.setCurrentIndex(max(0, self.cmb_datos.findData(nivel)))
        self.cmb_datos.currentIndexChanged.connect(lambda _i: self._nivel_datos())
        lay.addLayout(piezas.fila(self.aviso, QLabel("Datos personales:"), self.cmb_datos, estirar=False))
        lay.itemAt(lay.count() - 1).layout().setStretch(0, 1)

        split = QSplitter(Qt.Horizontal)
        izq = QWidget()
        il = QVBoxLayout(izq)
        il.setContentsMargins(0, 0, 0, 0)
        self.arbol = QTreeWidget()
        self.arbol.setHeaderHidden(True)
        self.arbol.setIndentation(16)
        self.arbol.setExpandsOnDoubleClick(False)
        self.arbol.setContextMenuPolicy(Qt.CustomContextMenu)
        self.arbol.customContextMenuRequested.connect(self._menu_arbol)
        self.arbol.itemClicked.connect(self._clic_arbol)
        self.arbol.itemExpanded.connect(self._al_expandir)
        self.arbol.currentItemChanged.connect(lambda it, _p: self._al_elegir(it))
        il.addWidget(self.arbol, 1)
        il.addWidget(QLabel("Informes"))
        self.lst_informes = QListWidget()
        self.lst_informes.setToolTip("Cada envío del móvil: fecha y vía. Elige uno para verlo a la derecha")
        self.lst_informes.currentRowChanged.connect(lambda _r: self.pintar())
        il.addWidget(self.lst_informes, 1)
        izq.setMinimumWidth(250)
        split.addWidget(izq)
        self._izq, self._split = izq, split

        der = QWidget()
        dl = QVBoxLayout(der)
        dl.setContentsMargins(0, 0, 0, 0)
        self.lbl_movil = QLabel("")
        self.lbl_movil.setWordWrap(True)
        dl.addWidget(self.lbl_movil)
        self.tabs = piezas.pestanas()
        self.m_ficha = TablaSimple(["Dato", "Valor"], color=lambda f, c: _COLORES.get(f[2]) if c == 1 and f[2] != an.INFO else None)
        self.m_seg = TablaSimple(["Estado", "Hallazgo", "Detalle"], color=lambda f, c: _COLORES.get(f[0]) if c == 0 else None)
        self.m_apps = TablaSimple(["App", "Notas", "Permisos sensibles", "Origen", "Versión", "Paquete"],
                                  color=lambda a, c: COLOR_AVISO if c == 1 and a["estado"] == an.AVISO else None)
        self.m_con = TablaSimple(["App", "Dominio", "Consultas", "Bloqueadas", "Publicidad"],
                                 color=lambda f, c: COLOR_AVISO if c == 4 and f["rastreador"] else None)
        self.m_evo = TablaSimple(["Fecha", "Batería", "Salud", "Temperatura", "Ciclos", "Almacenamiento", "Apps", "Velocidad", "Vía"])
        self.m_cam = TablaSimple(["Cambio", "App", "Detalle"])
        self._vistas: Dict[str, tuple] = {}
        for titulo, modelo, ancha in (("📋 Ficha", self.m_ficha, 1), ("🛡️ Seguridad", self.m_seg, 2),
                                      ("📦 Apps", self.m_apps, 1), ("🌐 Conexiones", self.m_con, 1),
                                      ("📈 Evolución", self.m_evo, 0), ("🔄 Cambios", self.m_cam, 2)):
            w = QWidget()
            wl = QVBoxLayout(w)
            wl.setContentsMargins(5, 5, 5, 5)
            busq = None
            if modelo in (self.m_apps, self.m_con):
                busq = QLineEdit()
                busq.setPlaceholderText("🔍 Buscar...")
                busq.setClearButtonEnabled(True)
                wl.addWidget(busq)
            vista, proxy = piezas.tabla(modelo, self, columna_ancha=ancha, busqueda=busq,
                                        orden=(2, False) if modelo is self.m_con else (-1, True))
            wl.addWidget(vista, 1)
            self._vistas[titulo] = (modelo, vista, proxy)
            self.tabs.addTab(w, titulo)
        # Tablas de analisis.EXTRA: una pestaña por tema, con subpestañas si tiene varias
        self._extra: Dict[str, tuple] = {}
        temas: Dict[str, list] = {}
        for clave, pest, sub, cols in an.EXTRA:
            temas.setdefault(pest, []).append((clave, sub, cols))
        for pest, subs in temas.items():
            self.tabs.addTab(self._pestana_tablas(subs, self._extra, pest), pest)
        # 👥 Datos personales (encargo «datos», MASysMov 1.3.2)
        self._dp: Dict[str, tuple] = {}
        subs_dp = [("resumen", "Resumen", ["Dato", "Valor"]),
                   ("contactos", "Contactos", ["Nombre", "Teléfonos", "Correos", "Cuentas", "Favorito"]),
                   ("llamadas", "Llamadas", ["Fecha", "Hora", "Número", "Nombre", "Tipo", "Duración"]),
                   ("sms", "SMS", ["Fecha", "Hora", "Número", "Tipo", "Leído", "Texto"])]
        self.tabs.addTab(self._pestana_tablas(subs_dp, self._dp, "👥 Datos personales",
                                              "Lo manda MASysMov 1.3.2 o posterior por Wi-Fi según «Datos personales» (arriba a la "
                                              "derecha). Con «Solo recuentos» solo llega el Resumen."), "👥 Datos personales")
        self.tabs.addTab(self._crear_copias(), "💾 Copias")
        split.addWidget(der)
        dl.addWidget(self.tabs, 1)
        split.setStretchFactor(1, 1)
        split.setSizes([280, 1000])
        lay.addWidget(split, 1)

        for b in (self.btn_empresas, self.btn_puertos, self.chk_recibir, self.btn_emparejar, self.btn_importar, self.btn_html,
                  self.btn_csv, self.btn_quitar, self.btn_actualizar):
            b.setMinimumWidth(b.sizeHint().width())
        self.setMinimumWidth(max(self.minimumWidth(), self.layout().minimumSize().width()))
        self.puente.emparejado.connect(self._emparejado)
        self._ajustar_a_pantalla()
        self.aplicar_tema()
        self._pintar_aviso()
        self.recargar()

    # ── piezas ──────────────────────────────────────────────────────────
    def _pestana_tablas(self, subs, destino: dict, pest: str, texto_nota: str = "") -> QWidget:
        cont = QWidget()
        cl_ = QVBoxLayout(cont)
        cl_.setContentsMargins(5, 5, 5, 5)
        if texto_nota:
            cl_.addWidget(piezas.nota(texto_nota))
        interior = piezas.pestanas() if len(subs) > 1 else None
        for clave, sub, cols in subs:
            modelo = TablaSimple(cols)
            vista, _proxy = piezas.tabla(modelo, self, columna_ancha=len(cols) - 1 if clave in ("sms", "resumen") else 0)
            destino[clave] = (pest, sub, modelo)
            if interior is None:
                cl_.addWidget(vista, 1)
            else:
                w = QWidget()
                wl = QVBoxLayout(w)
                wl.setContentsMargins(0, 5, 0, 0)
                wl.addWidget(vista, 1)
                interior.addTab(w, sub)
        if interior is not None:
            cl_.addWidget(interior, 1)
        return cont

    def _crear_copias(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(5, 5, 5, 5)
        self.lbl_carpeta = QLabel()
        self.lbl_carpeta.setWordWrap(True)
        self.lbl_carpeta.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.chk_copias_auto = QCheckBox("Traer las copias nuevas en cada envío")
        self.chk_copias_auto.setToolTip("Cada vez que el móvil manda su informe, envía también las copias que este PC no tenga")
        self.chk_copias_auto.toggled.connect(self._copias_auto)
        lay.addLayout(piezas.fila(QLabel("📁"), self.lbl_carpeta,
                                  piezas.boton("📁 Carpeta…", "ghost", self._copias_elegir, "Dónde se guardan las copias de todos los móviles"),
                                  piezas.boton("📂 Abrir carpeta", "ghost", self._copias_abrir),
                                  self.chk_copias_auto,
                                  piezas.boton("📨 Pedir al móvil", "primary", self._copias_pedir,
                                               "El móvil las manda la próxima vez que envíe su informe"), estirar=False))
        self.m_copias = TablaSimple(["App", "Versión", "Fecha", "Hora", "Instalable", "Tamaño", "Contenido", "Estado"],
                                    color=lambda f, c: {"Recibida": COLOR_OK, "Borrada en el PC": COLOR_AVISO}.get(f["estado"], COLOR_INFO) if c == 7 else None)
        self.v_copias, self.p_copias = piezas.tabla(self.m_copias, self, columna_ancha=0)
        self.v_copias.doubleClicked.connect(lambda _i: self._copias_abrir(seleccion=True))
        lay.addWidget(self.v_copias, 1)
        lay.addWidget(piezas.nota("Las copias se hacen en MASysMov y llegan cifradas por la Wi-Fi: copias de apps (Sistema › Espacio › "
                                  "Copias de apps) y, desde MASysMov 1.3.2, las copias de Datos (contactos, llamadas y SMS, con su "
                                  "contraseña) guardadas en la carpeta de copias. Para restaurar una en otro móvil, cópiala a su carpeta."))
        return w

    def _ajustar_a_pantalla(self):
        pantalla = (self.host.screen() if self.host is not None and hasattr(self.host, "screen") else None) \
            or QApplication.primaryScreen()
        zona = pantalla.availableGeometry() if pantalla else None
        ancho, alto = 1320, 760
        if zona is not None:
            ancho, alto = min(ancho, zona.width() - 40), min(alto, zona.height() - 60)
        self.resize(max(ancho, self.minimumWidth()), max(alto, self.minimumHeight()))

    def aplicar_tema(self):
        c = _colores(self.host)
        self.lbl_titulo.setStyleSheet(f"font-size: 13pt; font-weight: 600; color: {c['title']}; background: transparent; border: none;")
        self.lbl_sub.setStyleSheet(f"color: {c['muted']}; font-size: 9.5pt; background: transparent; border: none;")
        self.lbl_movil.setStyleSheet(f"font-size: 11pt; font-weight: 600; color: {c['title']}; background: transparent; border: none;")
        if self.host is not None and hasattr(self.host, "_apply_titlebar_theme_to") and self.isVisible():
            self.host._apply_titlebar_theme_to(self)

    def showEvent(self, e):
        super().showEvent(e)
        if self.host is not None and hasattr(self.host, "_apply_titlebar_theme_to"):
            self.host._apply_titlebar_theme_to(self)

    def _confirmar(self, texto: str) -> bool:
        caja = _caja(self, TITULO, texto, QMessageBox.Question)
        caja.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        return caja.exec_() == QMessageBox.Yes

    def _aviso_caja(self, texto: str, icono=QMessageBox.Information):
        _caja(self, TITULO, texto, icono).exec_()

    def _estado(self, texto: str):
        f = getattr(self.host, "set_status", None)
        if f:
            f(texto)

    def _pedir_asignacion(self, titulo: str, empresa: str = "", usuario: str = "") -> Optional[Tuple[str, str]]:
        dlg = AsignarDialog(self, self.clientes, titulo, empresa, usuario)
        return dlg.resultado if dlg.exec_() == QDialog.Accepted else None

    # ── árbol Empresa › Usuario › Terminal ─────────────────────────────
    def recargar(self, seleccionar: Optional[str] = None):
        actual = seleccionar or self._actual
        try:
            self._disps = self.alm.dispositivos()
        except Exception as e:
            self._disps = []
            self._aviso_caja(f"No se pudo leer la lista de móviles: {e}", QMessageBox.Warning)
        n = len(self._disps)
        self.recuento.setText(f"{n} móvil" if n == 1 else f"{n} móviles")
        self.arbol.blockSignals(True)
        self.arbol.clear()
        grupos: Dict[str, Dict[str, List[dict]]] = {}
        sueltos: List[dict] = []
        for d in self._disps:
            if d.get("empresa") and d.get("usuario"):
                grupos.setdefault(d["empresa"], {}).setdefault(d["usuario"], []).append(d)
            else:
                sueltos.append(d)
        item_sel = None
        for empresa in sorted(grupos, key=str.lower):
            usuarios = grupos[empresa]
            total = sum(len(v) for v in usuarios.values())
            it_e = self._grupo(None, f"🏢 {empresa}  ({total})", "empresa", (empresa, ""))
            for usuario in sorted(usuarios, key=str.lower):
                it_u = self._grupo(it_e, f"👤 {usuario}  ({len(usuarios[usuario])})", "usuario", (empresa, usuario))
                for d in usuarios[usuario]:
                    it = self._terminal(it_u, d)
                    if d["id"] == actual:
                        item_sel = it
        if sueltos:
            it_s = self._grupo(None, f"❔ {SIN_ASIGNAR}  ({len(sueltos)})", "empresa", ("", ""))
            for d in sueltos:
                it = self._terminal(it_s, d)
                if d["id"] == actual:
                    item_sel = it
        self.arbol.blockSignals(False)
        self._ajustar_ancho_arbol()
        if not self._disps:
            vacio = QTreeWidgetItem(self.arbol, ["Sin móviles: «📲 Emparejar móvil…» o «📥 Importar archivo…»"])
            vacio.setFlags(Qt.NoItemFlags)
        if item_sel is not None:
            padre = item_sel.parent()
            while padre is not None:
                padre.setExpanded(True)
                padre = padre.parent()
            self.arbol.setCurrentItem(item_sel)
        else:
            self._actual = None
            self.mostrar()

    def _grupo(self, padre, texto: str, tipo: str, clave) -> QTreeWidgetItem:
        it = QTreeWidgetItem(self.arbol if padre is None else padre, [texto])
        it.setData(0, _ROL_TIPO, tipo)
        it.setData(0, _ROL_ID, clave)
        f = it.font(0)
        f.setBold(tipo == "empresa")
        it.setFont(0, f)
        it.setToolTip(0, texto)
        return it

    def _terminal(self, padre, d: dict) -> QTreeWidgetItem:
        via = "Wi-Fi" if d["enlazado"] else "archivo"
        it = QTreeWidgetItem(padre, [f"📱 {nombre_movil(d)}"])
        it.setData(0, _ROL_TIPO, "movil")
        it.setData(0, _ROL_ID, d["id"])
        it.setToolTip(0, f"{nombre_movil(d)} · {d.get('modelo', '')} · {d['informes']} informes · por {via} · "
                         f"{d.get('version_app', '')} · último informe {an._fecha_hora(d.get('ultimo'))}")
        return it

    def _ajustar_ancho_arbol(self):
        """Que ningún nombre del árbol salga cortado: el panel izquierdo se ensancha a lo que haga falta (con tope)."""
        fm = self.arbol.fontMetrics()
        necesita = 0

        def medir(it, nivel):
            nonlocal necesita
            f = it.font(0)
            from PyQt5.QtGui import QFontMetrics
            ancho = QFontMetrics(f).horizontalAdvance(it.text(0)) if f.bold() else fm.horizontalAdvance(it.text(0))
            necesita = max(necesita, ancho + (nivel + 1) * self.arbol.indentation() + 36)
            for i in range(it.childCount()):
                medir(it.child(i), nivel + 1)
        for i in range(self.arbol.topLevelItemCount()):
            medir(self.arbol.topLevelItem(i), 0)
        ancho = min(max(250, necesita), 520)
        self._izq.setMinimumWidth(ancho)
        tam = self._split.sizes()
        if tam and tam[0] < ancho:
            self._split.setSizes([ancho, max(400, sum(tam) - ancho)])

    def _clic_arbol(self, item, _col):
        if item.data(0, _ROL_TIPO) in ("empresa", "usuario"):
            item.setExpanded(not item.isExpanded())

    def _al_expandir(self, item):
        """Acordeón: solo una empresa abierta y, dentro, un solo usuario."""
        self.arbol.blockSignals(True)
        try:
            if item.data(0, _ROL_TIPO) == "empresa":
                for i in range(self.arbol.topLevelItemCount()):
                    otro = self.arbol.topLevelItem(i)
                    if otro is not item:
                        otro.setExpanded(False)
                        for j in range(otro.childCount()):
                            otro.child(j).setExpanded(False)
            elif item.data(0, _ROL_TIPO) == "usuario" and item.parent() is not None:
                padre = item.parent()
                for j in range(padre.childCount()):
                    if padre.child(j) is not item:
                        padre.child(j).setExpanded(False)
        finally:
            self.arbol.blockSignals(False)

    def _al_elegir(self, item):
        if item is not None and item.data(0, _ROL_TIPO) == "movil":
            if item.data(0, _ROL_ID) != self._actual:
                self._actual = item.data(0, _ROL_ID)
                self.mostrar()

    def _disp(self, id_: Optional[str] = None) -> Optional[dict]:
        id_ = id_ or self._actual
        return next((d for d in self._disps if d["id"] == id_), None)

    def _menu_arbol(self, pos):
        item = self.arbol.itemAt(pos)
        if item is None or item.data(0, _ROL_TIPO) != "movil":
            return
        self.arbol.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction("👥 Asignar a empresa y usuario…", self.asignar)
        menu.addSeparator()
        menu.addAction("✕ Quitar móvil", self.quitar)
        menu.exec_(self.arbol.viewport().mapToGlobal(pos))

    def asignar(self):
        d = self._disp()
        if d is None:
            return
        r = self._pedir_asignacion(f"¿De quién es «{nombre_movil(d)}»?", d.get("empresa", ""), d.get("usuario", ""))
        if r is None:
            return
        try:
            self.alm.asignar(d["id"], *r)
        except ValueError as e:
            self._aviso_caja(str(e), QMessageBox.Warning)
            return
        self.recargar(seleccionar=d["id"])
        self._estado(f"📱 «{nombre_movil(d)}» queda en {r[0]} › {r[1]}")

    # ── informe del móvil elegido ──────────────────────────────────────
    def mostrar(self):
        d = self._disp()
        try:
            self._regs = self.alm.informes(d["id"]) if d else []
        except Exception:
            self._regs = []
        self.lbl_movil.setText(f"📱 {nombre_movil(d)}" + (f"  ·  {d['empresa']} › {d['usuario']}" if d and d.get("empresa") else "")
                               if d else "Elige un móvil a la izquierda")
        self.lst_informes.blockSignals(True)
        self.lst_informes.clear()
        for reg in self._regs:
            self.lst_informes.addItem(f"{an._fecha_hora(reg['generado'])} · {reg['origen']}")
        if self._regs:
            self.lst_informes.setCurrentRow(0)
        self.lst_informes.blockSignals(False)
        for b in (self.btn_html, self.btn_quitar):
            b.setEnabled(d is not None)
        self.pintar()

    def _informe_actual(self):
        r = self.lst_informes.currentRow()
        regs = self._regs
        return (regs[r]["informe"], regs[r + 1]["informe"] if r + 1 < len(regs) else None) if 0 <= r < len(regs) else (None, None)

    def pintar(self):
        inf, anterior = self._informe_actual()
        if inf is None:
            for m in (self.m_ficha, self.m_seg, self.m_apps, self.m_con, self.m_evo, self.m_cam):
                m.set_datos([], [])
        else:
            fic = an.ficha(inf)
            self.m_ficha.set_datos([[c, v] for c, v, _s in fic], fic)
            hal = an.hallazgos(inf)
            self.m_seg.set_datos([[{an.OK: "✅", an.AVISO: "⚠️", an.ERROR: "❌", an.INFO: "ℹ️"}.get(s, s) + " " +
                                   {an.OK: "Bien", an.AVISO: "Revisar", an.ERROR: "Importante", an.INFO: "Info"}.get(s, s), t, d]
                                  for s, t, d in hal], hal)
            apps = an.filas_apps(inf)
            self.m_apps.set_datos([[a.get("nombre", ""), a["notas"], ", ".join(a.get("permisos") or []), a["origen"],
                                    a.get("version", ""), a.get("pkg", "")] for a in apps], apps)
            con = an.filas_conexiones(inf)
            self.m_con.set_datos([[c["app"], c["dominio"], f"{c['consultas']:>6}", str(c["bloqueadas"] or ""), c["rastreador"]]
                                  for c in con], con)
            self.m_evo.set_datos(an.evolucion(self._regs))
            cam = an.cambios(inf, anterior) + an.alertas(inf)
            self.m_cam.set_datos([list(c) for c in cam], cam)
        ex = an.extra(inf) if inf is not None else {}
        for clave, (_p, _s, m) in self._extra.items():
            m.set_datos(ex.get(clave, []))
        d = self._disp()
        try:
            dp = an.datos_personales(self.alm.datos_personales(d["id"]) if d else None)
        except Exception:
            dp = an.datos_personales(None)
        for clave, (_p, _s, m) in self._dp.items():
            m.set_datos(dp.get(clave, []))
        self.pintar_copias()

    def pintar_copias(self):
        d = self._disp()
        filas = self.alm.copias(d["id"]) if d else []
        self.lbl_carpeta.setText(self.srv.copias.carpeta_de(d["id"]) if d else self.srv.copias.carpeta())
        self.chk_copias_auto.blockSignals(True)
        self.chk_copias_auto.setChecked(bool(d) and self.alm.copias_auto(d["id"]))
        self.chk_copias_auto.setEnabled(bool(d))
        self.chk_copias_auto.blockSignals(False)
        grupos = cp.agrupar(filas)
        datos = []
        for f in grupos:
            t = time.localtime(f["creada"] / 1000) if f["creada"] else None
            datos.append([f["app"], f["version"], time.strftime("%d/%m/%Y", t) if t else "", time.strftime("%H:%M", t) if t else "",
                          f["instalable"], _tamano(f["tamano"]), f["contenido"], f["estado"]])
        self.m_copias.set_datos(datos, grupos)

    # ── enlace (servidor) y datos personales ───────────────────────────
    def _enlace(self, activar: bool):
        ok = self.host.movil_activar_enlace(activar, padre=self)
        if ok != activar:
            self.chk_recibir.blockSignals(True)
            self.chk_recibir.setChecked(ok)
            self.chk_recibir.blockSignals(False)
        self._pintar_aviso()

    def _pintar_aviso(self):
        if self.srv.activo:
            self.aviso.setText(f"Recibiendo en {', '.join(servidor.ips_locales())} (puerto {servidor.PUERTO}). Mientras MASys esté "
                               "abierto, el móvil puede enviar su informe (aunque cierres esta ventana).")
        else:
            self.aviso.setText("Sin recibir por Wi-Fi: marca «Recibir del móvil» o importa un archivo exportado en MASysMov.")

    def _nivel_datos(self):
        nivel = self.cmb_datos.currentData()
        try:
            self.alm.fijar_nivel_datos(nivel)
        except (ValueError, OSError) as e:
            self._aviso_caja(f"No se pudo guardar: {e}", QMessageBox.Warning)
            return
        self._estado({NIVEL_NO: "👥 Los móviles no mandarán contactos, llamadas ni SMS",
                      NIVEL_RECUENTOS: "👥 Los móviles mandarán solo cuántos contactos, llamadas y SMS tienen",
                      NIVEL_COMPLETO: "👥 Los móviles mandarán sus contactos, llamadas y SMS en el próximo envío"}.get(nivel, ""))

    def _emparejado(self, id_: str, _nombre: str):
        if self._pendiente_asignar:
            try:
                self.alm.asignar(id_, *self._pendiente_asignar)
            except ValueError:
                pass
            self._pendiente_asignar = None
        self.recargar(seleccionar=id_)

    # ── acciones ────────────────────────────────────────────────────────
    def emparejar(self):
        if not self.srv.activo:
            self.chk_recibir.setChecked(True)
            if not self.srv.activo:
                return
        r = self._pedir_asignacion("¿De quién es el móvil que vas a emparejar?")
        if r is None:
            return
        self._pendiente_asignar = r
        EmparejarDialog(self.srv.nueva_sesion(), self.puente, self, quien=f"{r[0]} › {r[1]}").exec_()

    def importar(self):
        ruta = dialogo_abrir_archivo(self, "Informe exportado en MASysMov", get_informes_folder(),
                                     f"Informe de MASysMov (*{cripto.EXTENSION});;Todos (*.*)")
        if not ruta:
            return
        contrasena, ok = QInputDialog.getText(self, "Contraseña", "Contraseña que pusiste al exportar en el móvil:", QLineEdit.Password)
        if not ok:
            return
        try:
            inf = cripto.leer_archivo(ruta, contrasena)
        except cripto.ErrorCifrado:
            self._aviso_caja("Contraseña incorrecta (o archivo dañado).", QMessageBox.Warning)
            return
        except (OSError, ValueError) as e:
            self._aviso_caja(f"No se pudo abrir:\n\n{e}", QMessageBox.Warning)
            return
        self.importar_informe(inf)

    def importar_informe(self, inf: dict) -> bool:
        if not isinstance(inf, dict) or inf.get("formato") != "masysmov_informe":
            self._aviso_caja("Ese archivo no es un informe de MASysMov.", QMessageBox.Warning)
            return False
        d = inf.get("dispositivo") or {}
        disp = str(d.get("id") or "archivo-" + str(d.get("modelo", "movil")))[:100]
        conocido = self._disp(disp)
        asignacion = None
        if not (conocido and conocido.get("empresa")):
            asignacion = self._pedir_asignacion(f"¿De quién es «{d.get('nombre') or d.get('modelo') or 'el móvil'}»?")
            if asignacion is None:
                return False
        self.alm.guardar_dispositivo(disp, d.get("nombre") or d.get("modelo") or "Móvil", "archivo", modelo=d.get("modelo", ""))
        if asignacion:
            self.alm.asignar(disp, *asignacion)
        nuevo = self.alm.guardar_informe(disp, inf, "archivo")
        self.recargar(seleccionar=disp)
        self._estado("✅ Informe importado" if nuevo else "ℹ️ Ese informe ya estaba importado")
        return True

    def quitar(self):
        d = self._disp()
        if d is None:
            return
        if self._confirmar(f"Se borran de este PC «{nombre_movil(d)}», todos sus informes y lo recibido de contactos, llamadas y "
                           "SMS; deja de aceptar sus envíos. Las copias ya guardadas en la carpeta NO se borran. ¿Continuar?"):
            self.alm.quitar_dispositivo(d["id"])
            self._actual = None
            self.recargar()

    def informe_html(self):
        inf, anterior = self._informe_actual()
        d = self._disp()
        if inf is None or d is None:
            self._aviso_caja("Elige un móvil con algún informe.")
            return
        nombre = nombre_movil(d)
        ruta = dialogo_guardar_archivo(self, "Informe del móvil",
                                       os.path.join(get_informes_folder(), f"Movil_{''.join(c for c in nombre if c.isalnum())}_{time.strftime('%Y%m%d_%H%M')}.html"),
                                       "HTML (*.html)")
        if not ruta:
            return
        try:
            with open(ruta, "w", encoding="utf-8") as f:
                f.write(an.informe_html(nombre, inf, anterior))
        except OSError as e:
            self._aviso_caja(f"No se pudo guardar:\n\n{e}", QMessageBox.Warning)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(ruta))

    def tablas_visibles(self) -> List[Tuple[str, List[str], List[List[str]]]]:
        """[(título, cabeceras, filas)] de la pestaña que se ve (todas sus subpestañas)."""
        titulo = self.tabs.tabText(self.tabs.currentIndex())
        if titulo in self._vistas:
            modelo = self._vistas[titulo][0]
            return [(titulo, modelo.HEADERS, modelo.filas())]
        for grupo in (self._extra, self._dp):
            filas = [(f"{p} · {s}", m.HEADERS, m.filas()) for p, s, m in grupo.values() if p == titulo]
            if filas:
                return filas
        return [("Copias", self.m_copias.HEADERS, self.m_copias.filas())]

    def exportar_csv(self):
        tablas = [t for t in self.tablas_visibles() if t[2]]
        if not tablas:
            self._aviso_caja("La pestaña que se ve no tiene datos.")
            return
        d = self._disp()
        base = "".join(c for c in (nombre_movil(d) if d else "Movil") if c.isalnum()) or "Movil"
        ruta = dialogo_guardar_archivo(self, "Guardar CSV", ruta_guardado_predeterminada(f"Movil_{base}_{time.strftime('%Y%m%d_%H%M')}.csv"),
                                       "CSV (*.csv)")
        if not ruta:
            return
        try:
            with open(ruta, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f, delimiter=";")
                for i, (titulo, cab, filas) in enumerate(tablas):
                    if len(tablas) > 1:
                        if i:
                            w.writerow([])
                        w.writerow([titulo])
                    w.writerow(cab)
                    w.writerows(filas)
        except OSError as e:
            self._aviso_caja(f"No se pudo guardar: {e}", QMessageBox.Warning)
            return
        self._estado(f"📄 Guardado en {ruta}")

    def abrir_empresas(self):
        from msys.ui.clientes_dialog import ClientesDialog
        ClientesDialog(self, self.clientes).exec_()

    def abrir_puertos(self):
        abrir = getattr(self.host, "abrir_puertos_movil", None)
        if abrir:
            abrir()

    # ── copias ─────────────────────────────────────────────────────────
    def _copias_auto(self, si: bool):
        d = self._disp()
        if d:
            self.alm.fijar_copias_auto(d["id"], si)

    def _copias_elegir(self):
        # 4.10.1: el diálogo propio de MASys (sigue el tema), nunca el explorador nativo de Windows
        ruta = dialogo_elegir_carpeta(self, "Carpeta para las copias del móvil", self.srv.copias.carpeta())
        if ruta:
            self.srv.copias.fijar_carpeta(os.path.normpath(ruta))
            self.pintar_copias()
            self._estado("📁 Las copias nuevas del móvil irán a " + ruta)

    def _copias_abrir(self, seleccion: bool = False):
        d = self._disp()
        if seleccion:
            f = objeto_seleccionado(self.v_copias, self.p_copias, self.m_copias)
            if f and f.get("ruta") and os.path.isdir(f["ruta"]):
                os.startfile(f["ruta"])
                return
            if f and f.get("ruta") and os.path.isfile(f["ruta"]):
                import subprocess
                subprocess.Popen(["explorer", "/select,", os.path.normpath(f["ruta"])])
                return
        ruta = self.srv.copias.carpeta_de(d["id"]) if d else self.srv.copias.carpeta()
        os.makedirs(ruta, exist_ok=True)
        os.startfile(ruta)

    def _copias_pedir(self):
        d = self._disp()
        if not d or not d["enlazado"]:
            self._aviso_caja("Elige un móvil emparejado por Wi-Fi (los importados por archivo no envían copias).")
            return
        if not any(e["tipo"] == "copias" and e["estado"] != HECHO for e in self.alm.encargos(d["id"])):
            self.alm.nuevo_encargo(d["id"], "copias", {})
        self._estado("📨 Pedido: el móvil enviará sus copias la próxima vez que mande su informe (con Wi-Fi)")


# ── 🚪 Puertos desde fuera (todos los móviles) ─────────────────────────
class PuertosDialog(QDialog):
    """Pedidos de «Puerto desde fuera» de TODOS los móviles. Se piden sin móvil: el primer móvil emparejado por Wi-Fi que
    envíe su informe hace la prueba con datos móviles (si no tiene, pasa al siguiente)."""

    def __init__(self, host):
        super().__init__(host)
        self.host = host
        self.alm: Almacen = host.movil_almacen
        self._detalles: Dict[str, str] = {}
        self.setWindowTitle(PUERTOS)
        self.setWindowFlags((self.windowFlags() | Qt.WindowMinMaxButtonsHint) & ~Qt.WindowContextHelpButtonHint)
        self.setModal(False)
        self.setMinimumSize(1160, 440)
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.addWidget(piezas.nota("Comprueba si un puerto de una red se ve DESDE INTERNET. Lo hace el primer móvil emparejado que "
                                  "envíe su informe: lo prueba con sus datos móviles aunque esté en la Wi-Fi. Si no tiene datos "
                                  "móviles, el pedido espera al siguiente; si un móvil no contesta en 10 minutos, también."))
        self.ed_host = QLineEdit()
        self.ed_host.setPlaceholderText("Vacío = IP pública de la Wi-Fi, o un dominio")
        self.ed_puertos = QLineEdit("3389, 443, 80")
        self.btn_pedir = piezas.boton("📨 Pedir", "primary", self.pedir)
        self.btn_quitar = piezas.boton("✕ Quitar", "danger", self.quitar)
        fila = piezas.fila(QLabel("Dirección"), self.ed_host, QLabel("Puertos"), self.ed_puertos, self.btn_pedir, self.btn_quitar,
                           piezas.boton("🔄 Actualizar", "ghost", self.recargar), estirar=False)
        fila.setStretch(1, 2)
        fila.setStretch(3, 2)
        lay.addLayout(fila)
        # Tabla corta (cabe a lo ancho) y, debajo, el detalle completo de la fila elegida
        self.modelo = TablaSimple(["Pedido", "Dirección", "Puertos", "Estado", "Resultado", "Móvil"],
                                  color=lambda e, c: (COLOR_OK if e["estado"] == HECHO else COLOR_INFO) if c == 3 else None)
        self.vista, self.proxy = piezas.tabla(self.modelo, self, columna_ancha=4)
        self.vista.selectionModel().selectionChanged.connect(lambda *_a: self._detalle())
        lay.addWidget(self.vista, 1)
        self.lbl_detalle = QLabel("")
        self.lbl_detalle.setWordWrap(True)
        self.lbl_detalle.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_detalle.setObjectName("hintLabel")
        lay.addWidget(self.lbl_detalle)
        self.recuento = QLabel("")
        self.recuento.setObjectName("countLabel")
        lay.addWidget(self.recuento)
        self.resize(1260, 600)
        self.recargar()

    def showEvent(self, e):
        super().showEvent(e)
        if self.host is not None and hasattr(self.host, "_apply_titlebar_theme_to"):
            self.host._apply_titlebar_theme_to(self)

    def _confirmar(self, texto: str) -> bool:
        caja = _caja(self, PUERTOS, texto, QMessageBox.Question)
        caja.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        return caja.exec_() == QMessageBox.Yes

    def _aviso(self, texto: str, icono=QMessageBox.Information):
        _caja(self, PUERTOS, texto, icono).exec_()

    def recargar(self):
        try:
            encs = self.alm.encargos_puerto()
            disps = {d["id"]: d for d in self.alm.dispositivos()}
        except Exception as e:
            self._aviso(f"No se pudieron leer los pedidos: {e}", QMessageBox.Warning)
            return
        filas = []
        for e in encs:
            res = e.get("resultado") or {}
            texto = " · ".join(f"{p.get('puerto')} {_estado_puerto(p.get('estado'))}" for p in res.get("puertos", [])) if res else ""
            texto = texto or res.get("error", "")
            direccion = e["datos"].get("host") or (f"IP pública ({res['host']})" if res.get("host") else "IP pública")
            d = disps.get(e["dispositivo"]) if e["dispositivo"] != SIN_MOVIL else None
            estado = e["estado"]
            if estado == PENDIENTE and e["dispositivo"] != SIN_MOVIL:
                estado = "Asignado a un móvil"
            elif estado == PENDIENTE:
                estado = "Esperando a un móvil"
            # Móviles que lo cogieron sin datos móviles
            intentos = [f"{nombre_movil(disps[i['dispositivo']]) if i.get('dispositivo') in disps else 'móvil quitado'} "
                        f"({an._fecha_hora(i.get('fecha'))})" for i in e.get("intentos") or []]
            if intentos and e["estado"] != HECHO:
                estado += f" ({len(intentos)} sin datos)"
            movil = nombre_movil(d) if d else ("" if e["dispositivo"] == SIN_MOVIL else "móvil quitado")
            filas.append([an._fecha_hora(e["creado"]), direccion, ", ".join(str(p) for p in e["datos"].get("puertos", [])),
                          estado, texto, movil])
            quien = " › ".join(x for x in ((d or {}).get("empresa", ""), (d or {}).get("usuario", ""), movil) if x) or "ninguno todavía"
            completo = "; ".join(f"{p.get('puerto')}: {p.get('estado')}" for p in res.get("puertos", [])) or res.get("error", "") or "—"
            self._detalles[e["id"]] = (f"<b>Móvil:</b> {_html(quien)} &nbsp;·&nbsp; <b>Dirección:</b> {_html(direccion)}"
                                       + (f" &nbsp;·&nbsp; <b>Red:</b> {_html(res['red'])}" if res.get("red") else "")
                                       + f"<br><b>Resultado:</b> {_html(completo)}"
                                       + (f"<br><b>Sin datos móviles:</b> {_html(', '.join(intentos))}" if intentos else ""))
        self.modelo.set_datos(filas, encs)
        n = len(encs)
        self.recuento.setText(f"{n} pedido" if n == 1 else f"{n} pedidos")
        self._detalle()

    def _detalle(self):
        e = objeto_seleccionado(self.vista, self.proxy, self.modelo)
        self.lbl_detalle.setText(self._detalles.get(e["id"], "") if e else "Elige un pedido para ver su resultado completo.")

    def pedir(self):
        from msys.ui.herramientas_red import leer_puertos
        from msys.domain.consultas_red import limpiar_destino
        try:
            puertos = [p for p, _n in leer_puertos(self.ed_puertos.text())]
            host = limpiar_destino(self.ed_host.text()) if self.ed_host.text().strip() else ""
        except ValueError as e:
            self._aviso(str(e), QMessageBox.Warning)
            return
        if not puertos:
            self._aviso("Escribe al menos un puerto.", QMessageBox.Warning)
            return
        self.alm.nuevo_encargo(SIN_MOVIL, "puerto", {"host": host, "puertos": puertos[:20]})
        self.recargar()
        hay_wifi = any(d["enlazado"] for d in self.alm.dispositivos())
        f = getattr(self.host, "set_status", None)
        if f:
            f("📨 Pedido guardado: lo hará el primer móvil que envíe su informe" if hay_wifi else
              "📨 Pedido guardado, pero no hay ningún móvil emparejado por Wi-Fi: empareja uno en Móviles Android")

    def quitar(self):
        e = objeto_seleccionado(self.vista, self.proxy, self.modelo)
        if e and self._confirmar("¿Quitar este pedido de la lista?"):
            self.alm.quitar_encargo(e["id"])
            self.recargar()


# ── En la ventana principal ───────────────────────────────────────────
class MovilMixin:
    def iniciar_moviles(self):
        """Lo llama multisys.py al arrancar: base de móviles y servidor (que sigue aunque la ventana esté cerrada)."""
        self.movil_almacen = Almacen()
        self.movil_puente = _Puente()
        self.movil_puente.recibido.connect(self._movil_recibido)
        self.movil_puente.resultado.connect(lambda _d, _i: self._movil_refrescar_ventanas())
        self.movil_puente.copia.connect(lambda _d: self._movil_refrescar_ventanas(solo_copias=True))
        self.movil_servidor = servidor.ServidorMovil(
            self.movil_almacen, al_recibir=self.movil_puente.recibido.emit, al_emparejar=self.movil_puente.emparejado.emit,
            al_resultado=self.movil_puente.resultado.emit, al_copia=self.movil_puente.copia.emit)
        ajustes = getattr(self, "settings", None) or QSettings("Masoft", "MASys")
        self._movil_ajustes = ajustes
        if str(ajustes.value(CLAVE_ENLACE, "false")).lower() == "true":
            # Sin preguntas al arrancar (la regla de firewall ya se creó al marcarlo la primera vez)
            try:
                self.movil_servidor.arrancar()
            except OSError as e:
                if hasattr(self, "set_status"):
                    self.set_status(f"📱 No se pudo recibir del móvil (puerto {servidor.PUERTO}): {e}")

    def movil_activar_enlace(self, activar: bool, padre=None) -> bool:
        """Encender o apagar «Recibir del móvil». Devuelve cómo queda."""
        padre = padre or self
        if activar:
            if is_admin() and not servidor.regla_firewall_existe():
                caja = _caja(padre, TITULO, f"Para que el móvil llegue a este PC hay que abrir el puerto {servidor.PUERTO} en el "
                                            "firewall de Windows, solo para redes privadas y de dominio (nunca en redes públicas). "
                                            "¿Abrirlo?", QMessageBox.Question)
                caja.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
                if caja.exec_() == QMessageBox.Yes:
                    ok, msg = servidor.crear_regla_firewall()
                    audit = getattr(self, "repair_audit_db", None)
                    if audit is not None:
                        audit.registrar(seccion=TITULO, accion="Regla de firewall", objetivo=f"TCP {servidor.PUERTO} privado",
                                        exito=ok, detalle=msg or "OK")
            try:
                self.movil_servidor.arrancar()
            except OSError as e:
                _caja(padre, TITULO, f"No se pudo escuchar en el puerto {servidor.PUERTO}:\n\n{e}", QMessageBox.Warning).exec_()
                return False
        else:
            self.movil_servidor.parar()
        self._movil_ajustes.setValue(CLAVE_ENLACE, "true" if activar else "false")
        return activar

    def _movil_recibido(self, dispositivo, _id):
        if hasattr(self, "set_status"):
            self.set_status("📱 Informe nuevo del móvil recibido")
        if hasattr(self, "tray_notify"):
            self.tray_notify("MASys", "Informe nuevo del móvil recibido")
        self._movil_refrescar_ventanas(seleccionar=dispositivo)

    def _movil_refrescar_ventanas(self, seleccionar: Optional[str] = None, solo_copias: bool = False):
        dlg = getattr(self, "_dlg_moviles", None)
        try:
            if dlg is not None and dlg.isVisible():
                if solo_copias:
                    dlg.pintar_copias()
                else:
                    dlg.recargar(seleccionar=seleccionar)
        except RuntimeError:
            self._dlg_moviles = None
        pto = getattr(self, "_dlg_puertos", None)
        try:
            if pto is not None and pto.isVisible():
                pto.recargar()
        except RuntimeError:
            self._dlg_puertos = None

    def _movil_mostrar_unica(self, atributo: str, crear):
        dlg = getattr(self, atributo, None)
        try:
            if dlg is not None:
                dlg.show()
                if dlg.isMinimized():
                    dlg.showNormal()
                dlg.raise_()
                dlg.activateWindow()
                return dlg
        except RuntimeError:
            pass
        dlg = crear()
        setattr(self, atributo, dlg)
        dlg.show()
        return dlg

    def abrir_moviles(self):
        """Botón «📱 Móviles Android» bajo el árbol (multisys.py). Ventana única y NO modal."""
        dlg = self._movil_mostrar_unica("_dlg_moviles", lambda: MovilesDialog(self))
        QTimer.singleShot(0, lambda: dlg.recargar())
        return dlg

    def abrir_puertos_movil(self):
        """«🚪 Puertos desde fuera»: pedidos de todos los móviles. Ventana única y NO modal."""
        dlg = self._movil_mostrar_unica("_dlg_puertos", lambda: PuertosDialog(self))
        dlg.recargar()
        return dlg

    def _tema_moviles(self):
        dlg = getattr(self, "_dlg_moviles", None)
        try:
            if dlg is not None:
                dlg.aplicar_tema()
        except RuntimeError:
            self._dlg_moviles = None
