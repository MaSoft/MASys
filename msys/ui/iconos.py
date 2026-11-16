# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: sección 🎨 Iconos (categoría Sistema).

Cuatro pestañas:
- 📚 Bibliotecas: ver los iconos de cualquier .exe/.dll/.icl/.ico (las de
  Windows a un clic), con su índice y su referencia, y extraerlos en .ico
  (todos los tamaños) o .png (el mayor), uno o todos.
- 🖼️ Iconos de aplicaciones: los programas del menú Inicio y del escritorio
  con su icono, para extraerlo o abrir su biblioteca.
- 🩺 Reparación y ⚙️ Configuración: en msys/ui/iconos_config.py.

El motor (sin Qt) está en msys/domain/iconos.py."""

import os

from PyQt5.QtCore import QBuffer, QByteArray, QFileInfo, QIODevice, QSize, Qt
from PyQt5.QtGui import QIcon, QImage, QImageReader, QPixmap
from PyQt5.QtWidgets import (QApplication, QComboBox, QDialog, QFileIconProvider, QHBoxLayout, QLabel,
                             QLineEdit, QListView, QListWidget, QListWidgetItem, QMessageBox, QSplitter, QTabWidget,
                             QVBoxLayout, QWidget)

from msys.core import tareas
from msys.core.utils import dialogo_abrir_archivo, dialogo_elegir_carpeta, dialogo_guardar_archivo, get_informes_folder
from msys.domain import iconos as dom
from msys.models.tabla_simple import COLOR_APAGADO, TablaSimple, objeto_seleccionado
from msys.ui import piezas

SECCION = "🎨 Iconos"


def imagen_de_ico(ico: bytes, tamano: int) -> QImage:
    """La imagen del .ico más cercana por arriba a 'tamano' (o la mayor)."""
    buf = QBuffer()
    buf.setData(QByteArray(ico))
    buf.open(QIODevice.ReadOnly)
    lector = QImageReader(buf, b"ico")
    mejor, mejor_w = QImage(), 0
    for i in range(max(1, lector.imageCount())):
        lector.jumpToImage(i)
        img = lector.read()
        if img.isNull():
            continue
        w = img.width()
        if mejor.isNull() or (mejor_w < tamano and w > mejor_w) or (w >= tamano and w < mejor_w):
            mejor, mejor_w = img, w
    return mejor


def _cargar(ruta, tamano, parar):
    grupos = dom.leer_biblioteca(ruta, parar)
    return [(g, imagen_de_ico(g.ico, tamano)) for g in grupos]


def _guardar_ico(ruta: str, grupo: dom.IconoGrupo):
    with open(ruta, "wb") as f:
        f.write(grupo.ico)


def _guardar_png(ruta: str, grupo: dom.IconoGrupo) -> bool:
    return imagen_de_ico(grupo.ico, 1024).save(ruta, "PNG")


def _extraer_todos(grupos, carpeta: str, prefijo: str, parar) -> int:
    os.makedirs(carpeta, exist_ok=True)
    n = 0
    for g in grupos:
        if parar():
            break
        base = os.path.join(carpeta, f"{prefijo}_{g.indice:03d}")
        _guardar_ico(base + ".ico", g)
        _guardar_png(base + ".png", g)
        n += 1
    return n


class BibliotecasWidget(QWidget):
    """Explorador de bibliotecas de iconos. En modo selector (para elegir el
    icono de una unidad) emite la elección por 'elegido'."""

    def __init__(self, host, modo_selector: bool = False):
        super().__init__()
        self.host = host
        self.modo_selector = modo_selector
        self.ruta = ""
        self.grupos = []
        self._tarea = None
        self._indice_pendiente = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(5, 5, 5, 5)
        lay.setSpacing(8)

        self.combo = QComboBox()
        for nombre, desc in dom.BIBLIOTECAS_SISTEMA:
            ruta = dom.ruta_biblioteca(nombre)
            if os.path.exists(ruta):
                self.combo.addItem(f"{nombre} · {desc}", ruta)
        self.combo.currentIndexChanged.connect(lambda _i: self.abrir(self.combo.currentData()))
        self.busqueda = QLineEdit()
        self.busqueda.setPlaceholderText("🔍 Índice o ID...")
        self.busqueda.setClearButtonEnabled(True)
        self.busqueda.textChanged.connect(self._filtrar)
        self.tam = piezas.combo([f"{t} px" for t in (32, 48, 64, 96)], [32, 48, 64, 96])
        self.tam.setCurrentIndex(1)
        self.tam.currentIndexChanged.connect(lambda _i: self.abrir(self.ruta))
        fila = QHBoxLayout()
        fila.setSpacing(8)
        fila.addWidget(self.combo, 3)
        fila.addWidget(piezas.boton("📂 Abrir archivo…", "ghost", self._abrir_archivo,
                                    "Cualquier .exe, .dll, .icl, .cpl o .ico"))
        fila.addWidget(self.busqueda, 1)
        fila.addWidget(self.tam)
        self.lbl_total = QLabel("")
        self.lbl_total.setObjectName("countLabel")
        fila.addWidget(self.lbl_total)
        lay.addLayout(fila)

        split = QSplitter(Qt.Horizontal)
        self.lista = QListWidget()
        self.lista.setViewMode(QListView.IconMode)
        self.lista.setResizeMode(QListView.Adjust)
        self.lista.setMovement(QListView.Static)
        self.lista.setUniformItemSizes(True)
        self.lista.setSelectionMode(QListWidget.ExtendedSelection)
        self.lista.currentItemChanged.connect(lambda *_: self._detalle())
        if modo_selector:
            self.lista.itemDoubleClicked.connect(lambda _it: self.window().accept())
        split.addWidget(self.lista)

        panel = QWidget()
        pl = QVBoxLayout(panel)
        pl.setContentsMargins(10, 0, 0, 0)
        self.vista = QLabel()
        self.vista.setFixedSize(260, 260)
        self.vista.setAlignment(Qt.AlignCenter)
        pl.addWidget(self.vista, 0, Qt.AlignHCenter)
        self.lbl_indice = QLabel("")
        self.lbl_indice.setStyleSheet("font-weight: 600;")
        pl.addWidget(self.lbl_indice)
        self.lbl_ref = QLabel("")
        self.lbl_ref.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_ref.setWordWrap(True)
        pl.addWidget(self.lbl_ref)
        self.lbl_tamanos = QLabel("")
        self.lbl_tamanos.setObjectName("hintLabel")
        self.lbl_tamanos.setWordWrap(True)
        pl.addWidget(self.lbl_tamanos)
        self.btn_copiar = piezas.boton("📋 Copiar referencia", "ghost", self._copiar_ref,
                                       "Lo que hay que poner en un acceso directo o en el registro")
        pl.addWidget(self.btn_copiar)
        if not modo_selector:
            self.btn_ico = piezas.boton("💾 Guardar ICO…", "ghost", self._guardar_ico, "Todos los tamaños del icono")
            self.btn_png = piezas.boton("🖼️ Guardar PNG…", "ghost", self._guardar_png, "El tamaño mayor, con transparencia")
            self.btn_todos = piezas.boton("📦 Extraer todos…", "primary", self._extraer_todos,
                                          "Todos los iconos de esta biblioteca (o los marcados) en ICO y PNG")
            for b in (self.btn_ico, self.btn_png, self.btn_todos):
                pl.addWidget(b)
        pl.addStretch()
        panel.setFixedWidth(300)
        split.addWidget(panel)
        split.setStretchFactor(0, 1)
        lay.addWidget(split, 1)
        self._detalle()

    # ── carga ──────────────────────────────────────────────────
    def abrir(self, ruta: str):
        if not ruta:
            return
        self.ruta = ruta
        self.lista.clear()
        self.grupos = []
        self.lbl_total.setText("⏳ Leyendo…")
        self.host.set_status(f"🔄 Leyendo los iconos de {os.path.basename(ruta)}...")
        tam = self.tam.currentData()
        self._tarea = tareas.Tarea(_cargar, ruta, tam, lambda: False)
        self._tarea.ok.connect(lambda res, r=ruta, t=tam: self._mostrar(res, r, t))
        self._tarea.error.connect(self._error)
        self._tarea.start()

    def _mostrar(self, resultado, ruta, tam):
        if ruta != self.ruta:
            return
        self.lista.setIconSize(QSize(tam, tam))
        self.lista.setGridSize(QSize(tam + 34, tam + 30))
        for grupo, img in resultado:
            it = QListWidgetItem(QIcon(QPixmap.fromImage(img)), str(grupo.indice))
            it.setData(Qt.UserRole, len(self.grupos))
            it.setToolTip(f"Índice {grupo.indice} · ID {grupo.res_id} · hasta {grupo.tamano_max} px")
            self.grupos.append(grupo)
            self.lista.addItem(it)
        self.lbl_total.setText(f"{len(self.grupos)} iconos")
        piezas.refrescar_export(self.host)
        self.host.set_status(f"✅ {os.path.basename(ruta)}: {len(self.grupos)} iconos")
        fila = 0
        if self._indice_pendiente is not None:
            g = grupo_por_indice(self.grupos, self._indice_pendiente)
            fila = self.grupos.index(g) if g in self.grupos else 0
            self._indice_pendiente = None
        if self.lista.count():
            self.lista.setCurrentRow(fila)
        self._filtrar(self.busqueda.text())

    def _error(self, msg):
        self.lbl_total.setText("")
        self.host.set_status("❌ No se pudieron leer los iconos")
        self.host._avisar("Iconos", f"No se pudieron leer los iconos:\n\n{msg}", QMessageBox.Warning)

    def _abrir_archivo(self):
        ruta = dialogo_abrir_archivo(self, "Elegir archivo con iconos", self.ruta or os.environ.get("SystemRoot", "C:\\"),
                                     "Archivos con iconos (*.exe *.dll *.icl *.cpl *.ico *.ocx *.scr *.mun);;Todos (*.*)")
        if ruta:
            self.abrir_ruta(ruta)

    def abrir_ruta(self, ruta: str, indice: int = None):
        self._indice_pendiente = indice
        i = self.combo.findData(ruta)
        if i < 0:
            self.combo.blockSignals(True)
            self.combo.addItem(f"{os.path.basename(ruta)} · {os.path.dirname(ruta)}", ruta)
            self.combo.setCurrentIndex(self.combo.count() - 1)
            self.combo.blockSignals(False)
            self.abrir(ruta)
        elif i != self.combo.currentIndex():
            self.combo.setCurrentIndex(i)
        else:
            self.abrir(ruta)

    def _filtrar(self, texto):
        texto = texto.strip().lstrip("-")
        for i in range(self.lista.count()):
            it = self.lista.item(i)
            g = self.grupos[it.data(Qt.UserRole)]
            it.setHidden(bool(texto) and not (str(g.indice).startswith(texto) or g.res_id.startswith(texto)))

    # ── detalle y acciones ─────────────────────────────────────
    def seleccionado(self):
        it = self.lista.currentItem()
        return self.grupos[it.data(Qt.UserRole)] if it is not None else None

    def marcados(self):
        return [self.grupos[it.data(Qt.UserRole)] for it in self.lista.selectedItems()]

    def referencia(self, g) -> str:
        return f"{self.ruta},{g.referencia}" if g.res_id.isdigit() else f"{self.ruta},{g.indice}"

    def _detalle(self):
        g = self.seleccionado()
        hay = g is not None
        for b in [self.btn_copiar] + ([] if self.modo_selector else [self.btn_ico, self.btn_png]):
            b.setEnabled(hay)
        if not hay:
            self.vista.clear()
            self.lbl_indice.setText("Elige un icono")
            self.lbl_ref.setText("")
            self.lbl_tamanos.setText("")
            return
        img = imagen_de_ico(g.ico, 256)
        pm = QPixmap.fromImage(img)
        if pm.width() > 256:
            pm = pm.scaled(256, 256, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.vista.setPixmap(pm)
        self.lbl_indice.setText(f"Índice {g.indice} · ID {g.res_id}")
        self.lbl_ref.setText(self.referencia(g))
        tams = sorted({w for w, _h, _b in g.tamanos}, reverse=True)
        self.lbl_tamanos.setText("Tamaños: " + ", ".join(f"{t} px" for t in tams))

    def _copiar_ref(self):
        g = self.seleccionado()
        if g:
            QApplication.clipboard().setText(self.referencia(g))
            self.host.set_status("📋 Referencia copiada")

    def _nombre_base(self, g) -> str:
        return f"{os.path.splitext(os.path.basename(self.ruta))[0]}_{g.indice:03d}"

    def _guardar_ico(self):
        g = self.seleccionado()
        if not g:
            return
        ruta = dialogo_guardar_archivo(self, "Guardar icono", os.path.join(get_informes_folder(), self._nombre_base(g) + ".ico"),
                                       "Icono (*.ico)")
        if ruta:
            try:
                _guardar_ico(ruta, g)
                self.host.set_status(f"✅ Guardado {os.path.basename(ruta)}")
            except OSError as e:
                self.host._avisar("Guardar icono", f"No se pudo guardar:\n\n{e}", QMessageBox.Warning)

    def _guardar_png(self):
        g = self.seleccionado()
        if not g:
            return
        ruta = dialogo_guardar_archivo(self, "Guardar PNG", os.path.join(get_informes_folder(), self._nombre_base(g) + ".png"),
                                       "Imagen PNG (*.png)")
        if ruta:
            if _guardar_png(ruta, g):
                self.host.set_status(f"✅ Guardado {os.path.basename(ruta)}")
            else:
                self.host._avisar("Guardar PNG", "No se pudo guardar la imagen", QMessageBox.Warning)

    def _extraer_todos(self):
        if not self.grupos:
            return
        grupos = self.marcados() if len(self.marcados()) > 1 else list(self.grupos)
        base = os.path.splitext(os.path.basename(self.ruta))[0]
        carpeta = dialogo_elegir_carpeta(self, "Carpeta donde dejar los iconos", get_informes_folder())
        if not carpeta:
            return
        destino = os.path.join(carpeta, f"Iconos_{base}")
        self.host.set_status(f"🔄 Extrayendo {len(grupos)} iconos...")
        self.btn_todos.setEnabled(False)
        t = tareas.Tarea(_extraer_todos, grupos, destino, base, lambda: False)
        t.ok.connect(lambda n: self._extraidos(n, destino))
        t.error.connect(lambda m: (self.btn_todos.setEnabled(True),
                                   self.host._avisar("Extraer iconos", f"No se pudieron extraer:\n\n{m}", QMessageBox.Warning)))
        self._tarea_extraer = t
        t.start()

    def _extraidos(self, n, destino):
        self.btn_todos.setEnabled(True)
        self.host.set_status(f"✅ {n} iconos extraídos en {destino}")
        if self.host._confirmar_accion("Extraer iconos", f"{n} iconos guardados (ICO y PNG) en:\n\n{destino}\n\n¿Abrir la carpeta?"):
            os.startfile(destino)

    def filas_export(self):
        filas = [[str(g.indice), g.res_id, self.referencia(g), ", ".join(str(w) for w in sorted({w for w, _h, _b in g.tamanos}, reverse=True))]
                 for g in self.grupos]
        return (f"Iconos de {os.path.basename(self.ruta)}", ["Índice", "ID", "Referencia", "Tamaños (px)"], filas)


class SelectorIconoDialog(QDialog):
    """Elegir un icono de cualquier biblioteca: devuelve la referencia 'ruta,-ID'."""

    def __init__(self, host, parent=None, ruta_inicial: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Elegir icono")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.resize(980, 620)
        lay = QVBoxLayout(self)
        self.bib = BibliotecasWidget(host, modo_selector=True)
        lay.addWidget(self.bib, 1)
        botones = piezas.fila(None, piezas.boton("Cancelar", "ghost", self.reject),
                              piezas.boton("Usar este icono", "primary", self.accept), estirar=False)
        lay.addLayout(botones)
        if ruta_inicial and os.path.exists(ruta_inicial):
            self.bib.abrir_ruta(ruta_inicial)
        else:
            self.bib.abrir(self.bib.combo.currentData())

    def referencia(self) -> str:
        g = self.bib.seleccionado()
        return self.bib.referencia(g) if g else ""


def _apps(parar):
    """[(acceso, QImage de 24 px)] sin repetidos, ordenados por nombre. El icono
    se saca del propio archivo (no de QFileIconProvider, que a veces solo da el
    genérico de acceso directo)."""
    vistos, out = set(), []
    for a in dom.escanear_accesos(parar):
        if a.estado not in (dom.ESTADO_OK, dom.ESTADO_ICONO) or not a.destino.lower().endswith((".exe", ".lnk", ".ico", ".dll", ".cpl", ".msc")):
            continue
        clave = (a.nombre.lower(), a.destino.lower())
        if clave in vistos:
            continue
        vistos.add(clave)
        img = QImage()
        archivo, indice = origen_icono(a)
        try:
            g = grupo_por_indice(dom.leer_biblioteca(archivo), indice)
            if g is not None:
                img = imagen_de_ico(g.ico, 24)
        except OSError:
            pass
        out.append((a, img))
    return sorted(out, key=lambda x: x[0].nombre.lower())


def origen_icono(a: dom.AccesoDirecto):
    """(archivo, índice) del icono de un acceso directo: el de IconLocation si
    lo tiene, si no el primero del programa."""
    archivo, indice = "", 0
    if a.icono:
        partes = a.icono.rsplit(",", 1)
        archivo = os.path.expandvars(partes[0].strip().strip('"'))
        try:
            indice = int(partes[1]) if len(partes) > 1 else 0
        except ValueError:
            indice = 0
    if not archivo:
        archivo = os.path.expandvars(a.destino)
    return archivo, indice


def grupo_por_indice(grupos, indice: int):
    if indice < 0:
        return next((g for g in grupos if g.res_id == str(-indice)), None)
    return next((g for g in grupos if g.indice == indice), grupos[0] if grupos and indice == 0 else None)


class AplicacionesWidget(QWidget):
    def __init__(self, host, al_ver_biblioteca):
        super().__init__()
        self.host = host
        self.al_ver_biblioteca = al_ver_biblioteca
        self._iconos = {}
        self._proveedor = QFileIconProvider()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(5, 5, 5, 5)
        self.btn_ico = piezas.boton("💾 Guardar ICO…", "ghost", lambda: self._guardar("ico"))
        self.btn_png = piezas.boton("🖼️ Guardar PNG…", "ghost", lambda: self._guardar("png"))
        self.btn_bib = piezas.boton("📚 Ver su biblioteca", "ghost", self._ver_biblioteca,
                                    "Abre el archivo del icono en Bibliotecas para ver todos los que tiene")
        fila, self.busqueda, _act, self.recuento = piezas.barra_superior(
            "🔍 Buscar aplicación...", [self.btn_ico, self.btn_png, self.btn_bib], None, con_equipo=False)
        lay.addLayout(fila)
        self.modelo = TablaSimple(["Aplicación", "Dónde", "Programa", "Icono"], icono=self._icono_de)
        self.tabla, self.proxy = piezas.tabla(self.modelo, self, columna_ancha=2, busqueda=self.busqueda)
        self.tabla.setIconSize(QSize(24, 24))
        self.tabla.doubleClicked.connect(lambda _i: self._ver_biblioteca())
        lay.addWidget(self.tabla, 1)
        self.cargado = False

    def _icono_de(self, a):
        if a.ruta not in self._iconos:
            self._iconos[a.ruta] = self._proveedor.icon(QFileInfo(a.ruta))
        return self._iconos[a.ruta]

    @staticmethod
    def _texto_icono(a) -> str:
        return "(el del programa)" if not a.icono or a.icono.startswith(",") else a.icono

    def actualizar(self):
        self.host.set_status("🔄 Buscando aplicaciones del menú Inicio y del escritorio...")
        tareas.lanzar(self, "_tarea", _apps, lambda: False, al_terminar=self._datos, com=True,
                      al_fallar=lambda m: self.host._avisar("Aplicaciones", f"No se pudieron leer:\n\n{m}", QMessageBox.Warning))

    def _datos(self, resultado):
        self.cargado = True
        apps = [a for a, _img in resultado]
        for a, img in resultado:
            if not img.isNull():
                self._iconos[a.ruta] = QIcon(QPixmap.fromImage(img))
        self.modelo.set_datos([[a.nombre, a.ubicacion, a.destino, self._texto_icono(a)] for a in apps], apps)
        self.recuento.setText(f"{len(apps)} aplicaciones")
        piezas.refrescar_export(self.host)
        self.host.set_status(f"✅ {len(apps)} aplicaciones")

    def _sel(self):
        return objeto_seleccionado(self.tabla, self.proxy, self.modelo)

    def _grupo(self):
        a = self._sel()
        if a is None:
            self.host._avisar("Iconos de aplicaciones", "Elige una aplicación de la lista")
            return None, None
        archivo, indice = origen_icono(a)
        try:
            grupos = dom.leer_biblioteca(archivo)
        except OSError as e:
            self.host._avisar("Iconos de aplicaciones", f"No se pudo leer el icono de «{a.nombre}»:\n\n{e}", QMessageBox.Warning)
            return a, None
        g = grupo_por_indice(grupos, indice)
        if g is None:
            self.host._avisar("Iconos de aplicaciones", f"«{os.path.basename(archivo)}» no tiene el icono {indice}.", QMessageBox.Warning)
        return a, g

    def _guardar(self, formato):
        a, g = self._grupo()
        if g is None:
            return
        nombre = dom.nombre_seguro(a.nombre)
        filtro = "Icono (*.ico)" if formato == "ico" else "Imagen PNG (*.png)"
        ruta = dialogo_guardar_archivo(self, "Guardar icono", os.path.join(get_informes_folder(), f"{nombre}.{formato}"), filtro)
        if not ruta:
            return
        try:
            if formato == "ico":
                _guardar_ico(ruta, g)
            elif not _guardar_png(ruta, g):
                raise OSError("No se pudo escribir la imagen")
            self.host.set_status(f"✅ Guardado {os.path.basename(ruta)}")
        except OSError as e:
            self.host._avisar("Guardar icono", f"No se pudo guardar:\n\n{e}", QMessageBox.Warning)

    def _ver_biblioteca(self):
        a = self._sel()
        if a is None:
            return
        archivo, indice = origen_icono(a)
        if not os.path.exists(archivo):
            self.host._avisar("Iconos de aplicaciones", f"No se encuentra el archivo del icono:\n\n{archivo}", QMessageBox.Warning)
            return
        self.al_ver_biblioteca(archivo)

    def filas_export(self):
        return ("Iconos de aplicaciones", self.modelo.HEADERS, self.modelo.filas())


class IconosMixin:
    def crear_tab_iconos(self):
        from msys.ui.iconos_config import ConfiguracionIconosWidget, ReparacionIconosWidget
        tab = piezas.PaginaPerezosa(lambda: self._iconos_cargar_pestana(forzar=False))
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)
        fila, _b, self.btn_refresh_iconos, _r = piezas.barra_superior(None, [], lambda: self._iconos_cargar_pestana(True))
        layout.addLayout(fila)

        self.iconos_tabs = piezas.pestanas()
        self.iconos_bib = BibliotecasWidget(self)
        self.iconos_apps = AplicacionesWidget(self, self._iconos_ver_biblioteca)
        self.iconos_rep = ReparacionIconosWidget(self)
        self.iconos_conf = ConfiguracionIconosWidget(self)
        self.iconos_tabs.addTab(self.iconos_bib, "📚 Bibliotecas")
        self.iconos_tabs.addTab(self.iconos_apps, "🖼️ Iconos de aplicaciones")
        self.iconos_tabs.addTab(self.iconos_rep, "🩺 Reparación")
        self.iconos_tabs.addTab(self.iconos_conf, "⚙️ Configuración")
        self.iconos_tabs.currentChanged.connect(lambda _i: (self._iconos_cargar_pestana(False), self._actualizar_barra_exportacion()))
        layout.addWidget(self.iconos_tabs, 1)
        self.registrar_export(SECCION, self._iconos_export)
        return tab

    def _iconos_cargar_pestana(self, forzar: bool):
        w = self.iconos_tabs.currentWidget()
        if w is self.iconos_bib:
            if forzar or not self.iconos_bib.grupos:
                self.iconos_bib.abrir(self.iconos_bib.ruta or self.iconos_bib.combo.currentData())
        elif forzar or not getattr(w, "cargado", False):
            w.actualizar()

    def _iconos_ver_biblioteca(self, ruta, indice=None):
        self.iconos_tabs.setCurrentWidget(self.iconos_bib)
        self.iconos_bib.abrir_ruta(ruta, indice)

    def _iconos_export(self):
        w = self.iconos_tabs.currentWidget() if hasattr(self, "iconos_tabs") else None
        if w is None or not hasattr(w, "filas_export"):
            return []
        datos = w.filas_export()
        return datos if isinstance(datos, list) else [datos]
