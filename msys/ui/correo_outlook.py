# -*- coding: utf-8 -*-
"""Pestañas de 📧 Cuentas Correo (4.9.0) que trabajan con archivos de Outlook (motor en msys/domain/correo):

- 🗂️ Configuración de Outlook: elegir qué guardar (cada perfil con sus cuentas y opciones, sus reglas del registro y cada
  firma) en .reg y .zip. La copia completa de siempre sigue en «📧 Cuentas › 💾 Backup configuración».
- 📬 Visor PST/OST: abre archivos de Outlook SIN Outlook y sin modificarlos (libpff): carpetas, correos, cuerpo sin
  imágenes de Internet ni scripts, cabeceras, adjuntos (abrir/guardar), exportar a .eml y buscar en todo el archivo."""

import os
import tempfile

from PyQt5.QtCore import Qt, QTimer, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (QCheckBox, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QMessageBox,
                             QPlainTextEdit, QSplitter, QTextBrowser, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from msys.core import tareas
from msys.core.utils import dialogo_abrir_archivo, dialogo_elegir_carpeta, dialogo_guardar_archivo
from msys.domain.correo import outlook_cfg as olk
from msys.domain.correo import pst as pv
from msys.domain.correo.ajustes import CorreoDB
from msys.models.tabla_simple import TablaSimple, objeto_seleccionado
from msys.ui import piezas
from msys.ui.correo import T_OUTLOOK, T_VISOR, _Dialogo

_ROL = Qt.UserRole


def _documentos() -> str:
    return os.path.join(os.path.expanduser("~"), "Documents")


# ══ 🗂️ Configuración de Outlook ═════════════════════════════════════════════
class ConfigOutlookWidget(QWidget):
    def __init__(self, host, db: CorreoDB):
        super().__init__()
        self.host, self.db = host, db
        lay = QVBoxLayout(self)
        lay.setContentsMargins(5, 5, 5, 5)
        lay.addWidget(piezas.nota("Guarda perfiles, cuentas, reglas y firmas de Outlook clásico para llevarlos a otro equipo o "
                                  "tener una copia (sin contraseñas). Marca lo que quieras guardar."))
        self.destino = QLineEdit(db.ajuste("carpeta_config") or os.path.join(_documentos(), "OutlookExport"))
        self.destino.editingFinished.connect(lambda: db.guardar_ajuste("carpeta_config", self.destino.text().strip()))
        f = piezas.fila(QLabel("Carpeta de destino"), self.destino, piezas.boton("📁 Elegir...", "ghost", self._elegir),
                        piezas.boton("📂 Abrir", "ghost", self._abrir_carpeta), estirar=False)
        f.setStretch(1, 1)
        lay.addLayout(f)
        sp = QSplitter(Qt.Horizontal)
        sp.setChildrenCollapsible(False)
        self.arbol = QTreeWidget()
        self.arbol.setHeaderLabels(["Qué guardar"])
        self.arbol.setMinimumWidth(320)
        self.arbol.currentItemChanged.connect(lambda actual, _antes: self._detalle(actual))
        sp.addWidget(self.arbol)
        self.lbl_detalle = QLabel("Elige un elemento para ver qué se guarda.")
        self.lbl_detalle.setWordWrap(True)
        self.lbl_detalle.setAlignment(Qt.AlignTop)
        self.lbl_detalle.setTextInteractionFlags(Qt.TextSelectableByMouse)
        caja = QWidget()
        cv = QVBoxLayout(caja)
        cv.addWidget(self.lbl_detalle, 1)
        sp.addWidget(caja)
        sp.setSizes([420, 520])
        lay.addWidget(sp, 1)
        lay.addLayout(piezas.fila(piezas.boton("☑️ Marcar todo", "ghost", lambda: self._marcar(True)),
                                  piezas.boton("☐ Desmarcar todo", "ghost", lambda: self._marcar(False)),
                                  piezas.boton("🔄 Volver a leer", "ghost", self.cargar), None,
                                  piezas.boton("💾 Guardar lo marcado", "primary", self.guardar), estirar=False))
        self.cargar()

    def _nodo(self, padre, texto, tipo, dato=None, marcable=True):
        it = QTreeWidgetItem([texto])
        it.setData(0, _ROL, (tipo, dato))
        if marcable:
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            it.setCheckState(0, Qt.Checked)
        (padre.addChild(it) if isinstance(padre, QTreeWidgetItem) else padre.addTopLevelItem(it))
        return it

    def cargar(self):
        self.arbol.clear()
        try:
            perfiles = olk.profiles()
        except OSError as e:
            perfiles = []
            self.host.set_status(f"⚠️ No se pudieron leer los perfiles de Outlook: {e}")
        for p in perfiles:
            pn = self._nodo(self.arbol, f"Perfil: {p.label()}", "perfil", p)
            # Las cuentas van dentro del perfil (mismo .reg): sin casillas propias por cuenta
            self._nodo(pn, "Cuentas y opciones del perfil", "cuentas", p)
            reglas = olk.rules_keys(p.version)
            if reglas:   # el Outlook actual guarda las reglas en el buzón: casi nunca hay
                rn = self._nodo(pn, "Reglas", "reglas", p)
                for k in reglas:
                    self._nodo(rn, k.rsplit("\\", 1)[-1], "regla", (p, k))
            pn.setExpanded(True)
        firmas = olk.signatures()
        fn = self._nodo(self.arbol, "Firmas" + ("" if firmas else " (no hay)"), "firmas", None, marcable=bool(firmas))
        for s in firmas:
            self._nodo(fn, s, "firma", s)
        fn.setExpanded(True)
        if not perfiles:
            self.lbl_detalle.setText("No hay perfiles de Outlook clásico en este usuario de Windows.")

    def _marcar(self, si: bool):
        for i in range(self.arbol.topLevelItemCount()):
            it = self.arbol.topLevelItem(i)
            if it.flags() & Qt.ItemIsUserCheckable:
                it.setCheckState(0, Qt.Checked if si else Qt.Unchecked)

    def _detalle(self, it):
        if it is None:
            return
        tipo, dato = it.data(0, _ROL)
        textos = {
            "perfil": lambda: f"Perfil «{dato.name}» de Outlook {dato.version}.\n\nSi está marcado «Cuentas y opciones del "
                              f"perfil», se guarda la rama del registro del perfil (cuentas, almacenes y opciones; sin "
                              f"contraseñas):\n{dato.key}",
            "cuentas": lambda: f"Cuentas y opciones del perfil «{dato.name}»: la rama del registro del perfil, sin "
                               "contraseñas. Las cuentas de este equipo se ven en la pestaña «📧 Cuentas».",
            "reglas": lambda: "Reglas guardadas en el registro (las versiones actuales de Outlook las guardan en el buzón; para "
                              "copiarlas usa Outlook › Reglas › Opciones › Exportar reglas).",
            "regla": lambda: f"Regla del registro:\n{dato[1]}",
            "firmas": lambda: f"Todas las firmas en un .zip (archivos .htm, .rtf, .txt e imágenes) de:\n{olk.signatures_dir()}",
            "firma": lambda: f"Firma «{dato}»: sus archivos y su carpeta de imágenes en un .zip.",
        }
        if tipo in textos:
            self.lbl_detalle.setText(textos[tipo]())

    def _elegir(self):
        ruta = dialogo_elegir_carpeta(self, "Carpeta para la configuración", self.destino.text().strip() or _documentos())
        if ruta:
            self.destino.setText(ruta)
            self.db.guardar_ajuste("carpeta_config", ruta)

    def _abrir_carpeta(self):
        d = self.destino.text().strip()
        if os.path.isdir(d):
            os.startfile(d)
        else:
            self.host._avisar(T_OUTLOOK, "La carpeta todavía no existe: se crea al guardar.")

    def _carpeta(self):
        d = self.destino.text().strip()
        if not d:
            self.host._avisar(T_OUTLOOK, "Elige la carpeta de destino.", QMessageBox.Warning)
            return None
        if not os.path.isdir(d):
            if not self.host._confirmar_accion(T_OUTLOOK, f"La carpeta no existe:\n\n{d}\n\n¿Crearla?"):
                return None
            try:
                os.makedirs(d, exist_ok=True)
            except OSError as e:
                self.host._avisar(T_OUTLOOK, f"No se pudo crear la carpeta:\n\n{e}", QMessageBox.Warning)
                return None
        self.db.guardar_ajuste("carpeta_config", d)
        return d

    def plan(self) -> list:
        """Lo marcado, sin repetir: perfil marcado = un .reg; si no, sus reglas sueltas. Firmas: todas o las elegidas."""
        out = []
        versiones = olk.versions()
        for i in range(self.arbol.topLevelItemCount()):
            arriba = self.arbol.topLevelItem(i)
            tipo, dato = arriba.data(0, _ROL)
            if tipo == "perfil":
                p = dato
                suf = olk.version_suffix(p.version, versiones)
                for j in range(arriba.childCount()):
                    hijo = arriba.child(j)
                    tipo_hijo = hijo.data(0, _ROL)[0]
                    if tipo_hijo == "cuentas" and hijo.checkState(0) == Qt.Checked:
                        out.append(("reg", p.key, f"Perfil_{olk.safe_name(p.name)}{suf}.reg"))
                    elif tipo_hijo == "reglas":
                        if hijo.checkState(0) == Qt.Checked:
                            out.append(("reg", rf"HKEY_CURRENT_USER\Software\Microsoft\Office\{p.version}\Outlook\Rules",
                                        f"Reglas_{olk.safe_name(p.name)}{suf}.reg"))
                        else:
                            for k in range(hijo.childCount()):
                                r = hijo.child(k)
                                if r.checkState(0) == Qt.Checked:
                                    out.append(("reg", r.data(0, _ROL)[1][1],
                                                f"Regla_{olk.safe_name(r.text(0))}_{olk.safe_name(p.name)}{suf}.reg"))
            elif tipo == "firmas" and arriba.flags() & Qt.ItemIsUserCheckable:
                if arriba.checkState(0) == Qt.Checked:
                    out.append(("zip", None, "Firmas.zip"))
                elif arriba.checkState(0) == Qt.PartiallyChecked:
                    nombres = [arriba.child(j).text(0) for j in range(arriba.childCount())
                               if arriba.child(j).checkState(0) == Qt.Checked]
                    out.append(("zip", nombres, "Firmas_elegidas.zip"))
        return out

    def guardar(self):
        plan = self.plan()
        if not plan:
            self.host._avisar(T_OUTLOOK, "Marca al menos un elemento.", QMessageBox.Warning)
            return
        d = self._carpeta()
        if not d:
            return
        hechos, errores = [], []
        for tipo, que, nombre in plan:
            self.host.set_status(f"🗂️ Guardando {nombre}…")
            ruta = os.path.join(d, nombre)
            try:
                if tipo == "reg":
                    ok, err = olk.reg_export(que, ruta)
                    if not ok:
                        raise olk.OutlookError(err or "reg export falló")
                else:
                    olk.zip_signatures(que, ruta)
                hechos.append(ruta)
            except (OSError, olk.OutlookError) as e:   # se sigue con lo demás
                errores.append(f"{nombre}: {e}")
        self.host.set_status(f"✅ Configuración guardada: {len(hechos)} archivo(s)")
        lista = "\n".join(os.path.basename(p) for p in hechos)
        if errores:
            self.host._avisar(T_OUTLOOK, f"Guardados {len(hechos)}; {len(errores)} con error:\n\n" + "\n".join(errores),
                              QMessageBox.Warning)
        elif self.host._confirmar_accion(T_OUTLOOK, f"Guardados {len(hechos)} archivo(s) en {d}:\n\n{lista}\n\n"
                                         "¿Abrir la carpeta?"):
            os.startfile(d)


# ══ 📬 Visor PST/OST ════════════════════════════════════════════════════════
class CabecerasDialog(_Dialogo):
    def __init__(self, padre, texto: str):
        super().__init__(padre, "Cabeceras del correo", 760)
        t = QPlainTextEdit(texto or "Este correo no guarda cabeceras de Internet (lo normal en los enviados).")
        t.setReadOnly(True)
        t.setMinimumHeight(420)
        self.cuerpo.addWidget(t)
        self.boton("Cerrar", self.accept, "primary")
        self.terminar()


class VisorPstWidget(QWidget):
    COLS = ["Fecha", "De", "Asunto", "Adj."]

    def __init__(self, host, db: CorreoDB):
        super().__init__()
        self.host, self.db = host, db
        self.pst = None
        self.carpeta = None     # ruta de la carpeta abierta (tupla de índices)
        self.actual = None      # (ruta_carpeta, índice, Message)
        self.filas = []         # (ruta_carpeta, MsgRow) de la lista actual
        self._peticion = 0
        self._tarea = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(5, 5, 5, 5)
        self.btn_abrir = piezas.boton("📂 Abrir PST u OST…", "primary", self.abrir)
        self.lbl_archivo = QLabel("Abre un archivo PST u OST. Si es el OST que está usando Outlook, cierra Outlook antes o abre "
                                  "una copia. El archivo nunca se modifica.")
        self.lbl_archivo.setObjectName("countLabel")
        self.lbl_archivo.setWordWrap(True)
        f1 = piezas.fila(self.btn_abrir, self.lbl_archivo, estirar=False)
        f1.setStretch(1, 1)
        lay.addLayout(f1)
        self.buscar = QLineEdit()
        self.buscar.setPlaceholderText("🔎 Buscar asunto o remitente en todo el archivo")
        self.buscar.returnPressed.connect(self.buscar_en_archivo)
        self.en_texto = QCheckBox("También en el texto")
        self.btn_buscar = piezas.boton("🔍 Buscar", "ghost", self.buscar_en_archivo)
        self.btn_parar = piezas.boton("⏹️ Detener", "ghost", lambda: self._tarea.parar() if self._tarea else None)
        self.btn_parar.hide()
        self.btn_exp_carpeta = piezas.boton("📤 Carpeta a .eml…", "ghost", self.exportar_carpeta,
                                            "Exporta todos los correos de la carpeta elegida, uno por archivo .eml")
        f2 = piezas.fila(self.buscar, self.en_texto, self.btn_buscar, self.btn_parar, self.btn_exp_carpeta, estirar=False)
        f2.setStretch(0, 1)
        lay.addLayout(f2)

        sp = QSplitter(Qt.Horizontal)
        sp.setChildrenCollapsible(False)
        self.arbol = QTreeWidget()
        self.arbol.setHeaderLabels(["Carpeta", "Correos"])
        self.arbol.setMinimumWidth(230)
        cab = self.arbol.header()
        cab.setStretchLastSection(False)
        cab.setSectionResizeMode(0, QHeaderView.Stretch)
        cab.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.arbol.itemClicked.connect(lambda it, _c: self.abrir_carpeta(it.data(0, _ROL)))
        sp.addWidget(self.arbol)

        derecha = QSplitter(Qt.Vertical)
        derecha.setChildrenCollapsible(False)
        arriba = QWidget()
        av = QVBoxLayout(arriba)
        av.setContentsMargins(0, 0, 0, 0)
        self.lbl_lista = QLabel("Correos")
        self.lbl_lista.setObjectName("countLabel")
        av.addWidget(self.lbl_lista)
        self.modelo = TablaSimple(self.COLS)
        self.tabla, self.proxy = piezas.tabla(self.modelo, self, columna_ancha=2)
        self.tabla.selectionModel().selectionChanged.connect(lambda *_a: self._elegido())
        self.tabla.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabla.customContextMenuRequested.connect(self._menu_lista)
        av.addWidget(self.tabla, 1)
        derecha.addWidget(arriba)

        abajo = QWidget()
        bv = QVBoxLayout(abajo)
        bv.setContentsMargins(0, 0, 0, 0)
        self.lbl_asunto = QLabel("")
        self.lbl_asunto.setWordWrap(True)
        self.lbl_asunto.setStyleSheet("font-size: 11pt; font-weight: 600;")
        self.lbl_asunto.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.btn_cabeceras = piezas.boton("📄 Cabeceras", "ghost", self.ver_cabeceras)
        self.btn_eml = piezas.boton("📤 Exportar .eml…", "ghost", self.exportar_eml)
        fa = piezas.fila(self.lbl_asunto, self.btn_cabeceras, self.btn_eml, estirar=False)
        fa.setStretch(0, 1)
        bv.addLayout(fa)
        self.lbl_meta = QLabel("")
        self.lbl_meta.setWordWrap(True)
        self.lbl_meta.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bv.addWidget(self.lbl_meta)
        cuerpo = QSplitter(Qt.Horizontal)
        cuerpo.setChildrenCollapsible(False)
        self.cuerpo = QTextBrowser()
        self.cuerpo.setOpenLinks(False)
        self.cuerpo.anchorClicked.connect(self._enlace)
        cuerpo.addWidget(self.cuerpo)
        self.caja_adj = QWidget()
        adv = QVBoxLayout(self.caja_adj)
        adv.setContentsMargins(0, 0, 0, 0)
        self.lbl_adj = QLabel("Adjuntos")
        adv.addWidget(self.lbl_adj)
        self.adjuntos = QListWidget()
        self.adjuntos.setMinimumWidth(220)
        self.adjuntos.itemDoubleClicked.connect(lambda it: self._abrir_adjunto(it.data(_ROL)))
        self.adjuntos.setContextMenuPolicy(Qt.CustomContextMenu)
        self.adjuntos.customContextMenuRequested.connect(self._menu_adjunto)
        adv.addWidget(self.adjuntos, 1)
        adv.addWidget(piezas.boton("💾 Guardar todos…", "ghost", self.guardar_todos))
        cuerpo.addWidget(self.caja_adj)
        cuerpo.setSizes([620, 240])
        bv.addWidget(cuerpo, 1)
        derecha.addWidget(abajo)
        derecha.setSizes([300, 400])
        sp.addWidget(derecha)
        sp.setSizes([250, 900])
        lay.addWidget(sp, 1)
        self._vista(None)
        self._habilitar()
        if not pv.available():
            self.btn_abrir.setEnabled(False)
            self.lbl_archivo.setText("Falta la librería libpff-python: el visor no está disponible.")

    # ── abrir ───────────────────────────────────────────────────────────────
    def abrir(self):
        ruta = dialogo_abrir_archivo(self, "Abrir archivo de Outlook", self.db.ajuste("carpeta_visor") or _documentos(),
                                     "Archivos de Outlook (*.pst *.ost);;Todos (*.*)")
        if ruta:
            self.db.guardar_ajuste("carpeta_visor", os.path.dirname(ruta))
            self.abrir_ruta(ruta)

    def abrir_ruta(self, ruta: str):
        self._ocupado(True, f"📬 Abriendo {os.path.basename(ruta)}…")

        def trabajo():
            f = pv.PstFile(ruta)
            return f, f.tree()
        tareas.lanzar(self, "_tarea_abrir", trabajo, al_terminar=self._abierto,
                      al_fallar=lambda m: (self._ocupado(False), self.host._avisar(
                          T_VISOR, f"No se pudo abrir el archivo:\n\n{m}", QMessageBox.Warning)))

    def _abierto(self, res):
        f, arbol = res
        self.cerrar_archivo()
        self.pst = f
        self._ocupado(False, f"✅ {os.path.basename(f.path)}: {arbol.total()} correos")
        try:
            tam = piezas.tamano_legible(os.path.getsize(f.path))
        except OSError:
            tam = ""
        self.lbl_archivo.setText(f"{f.kind} · {os.path.basename(f.path)} · {tam}")
        self.lbl_archivo.setToolTip(f.path)
        self.arbol.clear()

        def anadir(nodo, padre):
            it = QTreeWidgetItem([nodo.name, str(nodo.count) if nodo.count else ""])
            it.setData(0, _ROL, nodo.path)
            it.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
            (padre.addChild(it) if padre is not None else self.arbol.addTopLevelItem(it))
            for c in nodo.children:
                anadir(c, it)
            return it
        raiz = anadir(arbol, None)
        raiz.setExpanded(True)
        for i in range(raiz.childCount()):
            raiz.child(i).setExpanded(True)
        self._ajustar_arbol()
        primera = self._primera_con_correo(raiz)
        if primera is not None:
            self.arbol.setCurrentItem(primera)
            self.abrir_carpeta(primera.data(0, _ROL))
        self._habilitar()

    def _ajustar_arbol(self):
        """El árbol, tan ancho como su carpeta más larga (hasta 460 px): los nombres no se cortan. Se repite al
        enseñar la pestaña: con la pestaña oculta la hoja de estilos aún no se ha aplicado y mide de menos."""
        a = self.arbol
        necesita = (a.sizeHintForColumn(0) + max(a.sizeHintForColumn(1), a.header().sectionSizeHint(1))
                    + a.verticalScrollBar().sizeHint().width() + 2 * a.frameWidth() + 12)
        a.setMinimumWidth(max(230, min(460, necesita)))

    def showEvent(self, e):
        super().showEvent(e)
        if self.pst is not None:
            QTimer.singleShot(0, self._ajustar_arbol)

    def _primera_con_correo(self, it):
        if it.text(1):
            return it
        for i in range(it.childCount()):
            r = self._primera_con_correo(it.child(i))
            if r is not None:
                return r
        return None

    def abrir_carpeta(self, ruta):
        if self.pst is None or ruta is None:
            return
        self.carpeta = ruta
        it = self.arbol.currentItem()
        self.lbl_lista.setText(it.text(0) if it else "Correos")
        # Si se pulsa otra carpeta antes de que termine esta, su resultado llega tarde y se descarta
        self._peticion += 1
        peticion = self._peticion
        t = tareas.Tarea(self.pst.messages, ruta)
        t.ok.connect(lambda filas: self._mostrar([(ruta, r) for r in filas]) if peticion == self._peticion else None)
        t.error.connect(lambda m: self.host._avisar(T_VISOR, f"No se pudo leer la carpeta:\n\n{m}", QMessageBox.Warning)
                        if peticion == self._peticion else None)
        self._t_carpeta = t
        self.host.set_status("📬 Leyendo la carpeta…")
        t.start()

    def _mostrar(self, filas, busqueda=False):
        self.filas = filas
        datos = [[r.date.strftime("%d/%m/%Y %H:%M") if r.date else "", r.sender, r.subject or "(sin asunto)",
                  str(r.attachments) if r.attachments else ""] for _p, r in filas]
        self.modelo.set_datos(datos, filas)
        self.lbl_lista.setText(f"{self.lbl_lista.text().split('  ·  ')[0]}  ·  {len(filas)} correo(s)")
        self.host.set_status(("🔍 Encontrados" if busqueda else "📬 Correos") + f": {len(filas)}")
        self._vista(None)
        piezas.refrescar_export(self.host)

    def _habilitar(self):
        hay = self.pst is not None
        for w in (self.buscar, self.btn_buscar, self.en_texto, self.btn_exp_carpeta):
            w.setEnabled(hay)

    # ── correo ──────────────────────────────────────────────────────────────
    def _elegido(self):
        sel = objeto_seleccionado(self.tabla, self.proxy, self.modelo)
        if sel is None or self.pst is None:
            return
        ruta, r = sel
        try:
            m = self.pst.message(ruta, r.index)
        except Exception as e:  # noqa: BLE001 - correo dañado dentro del archivo
            self._vista(None)
            self.lbl_asunto.setText(f"No se puede leer este correo: {e}")
            return
        self.actual = (ruta, r.index, m)
        self._vista(m)

    def _vista(self, m):
        hay = m is not None
        for b in (self.btn_eml, self.btn_cabeceras):
            b.setEnabled(hay)
        if not hay:
            self.actual = None
            self.lbl_asunto.setText("")
            self.lbl_meta.setText("Elige un correo de la lista." if self.pst else "")
            self.cuerpo.clear()
            self.adjuntos.clear()
            self.caja_adj.hide()
            return
        self.lbl_asunto.setText(m.subject or "(sin asunto)")
        partes = [f"De: {m.sender}"]
        if m.to:
            partes.append("Para: " + ", ".join(m.to[:8]) + (f" y {len(m.to) - 8} más" if len(m.to) > 8 else ""))
        if m.cc:
            partes.append("CC: " + ", ".join(m.cc[:8]))
        if m.date:
            partes.append(m.date.strftime("Fecha: %d/%m/%Y %H:%M"))
        self.lbl_meta.setText("\n".join(partes))
        self.cuerpo.setHtml(pv.html_for_view(m))
        self.adjuntos.clear()
        for a in m.attachments:
            it = QListWidgetItem(f"{a.name}  ·  {piezas.tamano_legible(a.size)}" + ("  ⚠️" if a.risky else ""))
            it.setData(_ROL, a)
            it.setToolTip("Doble clic: abrir · botón derecho: guardar"
                          + ("\nOjo: tipo de archivo que puede ejecutar programas" if a.risky else ""))
            self.adjuntos.addItem(it)
        self.lbl_adj.setText(f"Adjuntos ({len(m.attachments)})")
        self.caja_adj.setVisible(bool(m.attachments))

    def _enlace(self, url: QUrl):
        if url.scheme() in ("http", "https", "mailto") and self.host._confirmar_accion(
                "Abrir enlace", f"¿Abrir este enlace del correo?\n\n{url.toString()[:300]}"):
            QDesktopServices.openUrl(url)

    def ver_cabeceras(self):
        if self.actual:
            CabecerasDialog(self, self.actual[2].headers).exec_()

    # ── adjuntos ────────────────────────────────────────────────────────────
    def _menu_adjunto(self, pos):
        it = self.adjuntos.itemAt(pos)
        if not it:
            return
        a = it.data(_ROL)
        m = QMenu(self)
        m.addAction("📂 Abrir").triggered.connect(lambda: self._abrir_adjunto(a))
        m.addAction("💾 Guardar como...").triggered.connect(lambda: self._guardar_adjunto(a))
        m.exec_(self.adjuntos.mapToGlobal(pos))

    def _guardar_adjunto(self, a):
        ruta_c, idx, _m = self.actual
        ruta = dialogo_guardar_archivo(self, "Guardar adjunto", os.path.join(self.db.ajuste("carpeta_adjuntos") or _documentos(),
                                                                              pv.safe_file(a.name)), "Todos (*.*)")
        if not ruta:
            return
        try:
            datos = self.pst.attachment_bytes(ruta_c, idx, a.index)
            with open(ruta, "wb") as f:
                f.write(datos)
        except Exception as e:  # noqa: BLE001 - adjunto ilegible o sin permiso de escritura
            self.host._avisar(T_VISOR, f"No se pudo guardar:\n\n{e}", QMessageBox.Warning)
            return
        self.db.guardar_ajuste("carpeta_adjuntos", os.path.dirname(ruta))
        self.host.set_status(f"💾 Guardado: {ruta}")

    def _abrir_adjunto(self, a):
        if a.risky and not self.host._confirmar_accion(
                "Abrir adjunto", f"«{a.name}» es un tipo de archivo que puede ejecutar programas.\n\nÁbrelo solo si sabes de "
                "dónde viene; mejor guárdalo y pásale el antivirus. ¿Abrirlo igualmente?", QMessageBox.Warning):
            return
        ruta_c, idx, _m = self.actual
        try:
            carpeta = os.path.join(tempfile.gettempdir(), "MASys_correo")
            os.makedirs(carpeta, exist_ok=True)
            os.startfile(self.pst.save_attachment(ruta_c, idx, a, carpeta))
        except Exception as e:  # noqa: BLE001
            self.host._avisar(T_VISOR, f"No se pudo abrir:\n\n{e}", QMessageBox.Warning)

    def guardar_todos(self):
        if not self.actual:
            return
        carpeta = dialogo_elegir_carpeta(self, "Carpeta para los adjuntos", self.db.ajuste("carpeta_adjuntos") or _documentos())
        if not carpeta:
            return
        ruta_c, idx, m = self.actual
        hechos, errores = 0, []
        for a in m.attachments:
            try:
                self.pst.save_attachment(ruta_c, idx, a, carpeta)
                hechos += 1
            except Exception as e:  # noqa: BLE001 - se sigue con los demás
                errores.append(f"{a.name}: {e}")
        self.db.guardar_ajuste("carpeta_adjuntos", carpeta)
        if errores:
            self.host._avisar(T_VISOR, f"Guardados {hechos}; {len(errores)} con error:\n\n" + "\n".join(errores),
                              QMessageBox.Warning)
        else:
            self.host.set_status(f"💾 {hechos} adjunto(s) guardados en {carpeta}")

    # ── exportar y buscar ───────────────────────────────────────────────────
    def exportar_eml(self):
        if not self.actual:
            return
        ruta_c, idx, m = self.actual
        nombre = (m.date.strftime("%Y%m%d_%H%M_") if m.date else "") + (pv.safe_file(m.subject)[:80] or "correo") + ".eml"
        ruta = dialogo_guardar_archivo(self, "Exportar correo", os.path.join(self.db.ajuste("carpeta_eml") or _documentos(),
                                                                              nombre), "Correo (*.eml)")
        if not ruta:
            return
        try:
            with open(ruta, "wb") as f:
                f.write(self.pst.to_eml(ruta_c, idx))
        except Exception as e:  # noqa: BLE001
            self.host._avisar(T_VISOR, f"No se pudo exportar:\n\n{e}", QMessageBox.Warning)
            return
        self.db.guardar_ajuste("carpeta_eml", os.path.dirname(ruta))
        self.host.set_status(f"📤 Exportado: {ruta}")

    def _menu_lista(self, pos):
        if not self.tabla.indexAt(pos).isValid():
            return
        m = QMenu(self)
        m.addAction("📤 Exportar .eml…").triggered.connect(self.exportar_eml)
        m.addAction("📄 Ver cabeceras").triggered.connect(self.ver_cabeceras)
        m.exec_(self.tabla.viewport().mapToGlobal(pos))

    def exportar_carpeta(self):
        if self.pst is None or self.carpeta is None:
            self.host._avisar(T_VISOR, "Elige antes una carpeta del árbol.")
            return
        it = self.arbol.currentItem()
        destino = dialogo_elegir_carpeta(self, "Carpeta donde dejar los .eml", self.db.ajuste("carpeta_eml") or _documentos())
        if not destino:
            return
        sub = os.path.join(destino, pv.safe_file(it.text(0) if it else "correos"))
        ruta = self.carpeta
        self.db.guardar_ajuste("carpeta_eml", destino)

        def hecho(res):
            ok, errores = res
            self._ocupado(False, f"📤 Exportados {ok} correo(s) a {sub}")
            if errores:
                self.host._avisar(T_VISOR, f"Exportados {ok}; {len(errores)} con error:\n\n" + "\n".join(errores[:20]),
                                  QMessageBox.Warning)
            elif self.host._confirmar_accion(T_VISOR, f"Exportados {ok} correo(s) a:\n\n{sub}\n\n¿Abrir la carpeta?"):
                os.startfile(sub)
        t = tareas.Tarea(lambda: self.pst.export_folder(ruta, sub, lambda i, n, texto: t.progreso.emit(
            i, f"📤 Exportando {i + 1} de {n}: {texto}"), t.debe_parar))
        t.progreso.connect(lambda _i, texto: self.host.set_status(texto))
        t.ok.connect(hecho)
        t.error.connect(lambda m: (self._ocupado(False), self.host._avisar(T_VISOR, f"No se pudo exportar:\n\n{m}",
                                                                          QMessageBox.Warning)))
        self._tarea = t
        self._ocupado(True, "📤 Exportando…")
        t.start()

    def buscar_en_archivo(self):
        q = self.buscar.text().strip()
        if self.pst is None or len(q) < 2:
            return
        self._peticion += 1     # una búsqueda deja sin efecto la carpeta que se estuviera leyendo
        en_texto = self.en_texto.isChecked()

        def hecho(encontrados):
            self._ocupado(False)
            self.arbol.clearSelection()
            self.lbl_lista.setText(f"Búsqueda: «{q}»")
            self._mostrar([(p, r) for p, _n, r in encontrados], busqueda=True)
        t = tareas.Tarea(lambda: self.pst.search(q, en_texto, lambda i, n, texto: t.progreso.emit(i, texto), t.debe_parar))
        t.progreso.connect(lambda _i, texto: self.host.set_status(f"🔍 {texto}"))
        t.ok.connect(hecho)
        t.error.connect(lambda m: (self._ocupado(False), self.host._avisar(T_VISOR, f"No se pudo buscar:\n\n{m}",
                                                                          QMessageBox.Warning)))
        self._tarea = t
        self._ocupado(True, f"🔍 Buscando «{q}»…")
        t.start()

    def _ocupado(self, si: bool, texto: str = ""):
        for w in (self.btn_abrir, self.btn_buscar, self.btn_exp_carpeta):
            w.setEnabled(not si)
        self.btn_parar.setVisible(si)
        if texto:
            self.host.set_status(texto)
        if not si:
            self._habilitar()

    def cerrar_archivo(self):
        if self.pst is not None:
            self.pst.close()
            self.pst = None

    def filas_export(self):
        return (self.lbl_lista.text() or T_VISOR, self.COLS, self.modelo.filas())
