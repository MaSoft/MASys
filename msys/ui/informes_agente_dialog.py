# -*- coding: utf-8 -*-
"""«📥 Informes del agente» (4.7.0): los archivos .msysinf que genera el agente
de análisis en el equipo del cliente, organizados Cliente › Equipo › fecha.

- «📥 Abrir archivo…» descifra con la clave privada del técnico (DPAPI) y
  guarda el informe en multisys.db asociado a un cliente.
- «🌐 Ver informe»: informe HTML con la plantilla común (msys/core/informe_html),
  mismas columnas que las tablas de MASys.
- «⇄ Cambios»: comparación con el informe anterior del mismo equipo (misma
  lógica que «Cambios desde la última visita», msys/domain/visitas.py).
- «🔑 Código del técnico»: el código MSA1-… que el cliente pega en el agente.

Se abre desde la ventana 🖥️ Acceso remoto. Motor en msys/domain/agente.py."""

import os
import re
from typing import List, Optional

from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QDesktopServices, QFont, QGuiApplication
from PyQt5.QtWidgets import (QComboBox, QDialog, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
                             QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from msys import config as msys_config
from msys.core import informe_html
from msys.core.utils import dialogo_abrir_archivo, get_informes_folder
from msys.domain import agente as ag
from msys.domain import visitas
from msys.models.tabla_simple import TablaSimple
from msys.ui import piezas
from msys.ui import recepcion_agente as rec
from msys.ui.conocimiento_dialog import _IconosCabecera, _colores, _etiqueta

_ROL_ID = Qt.UserRole
COLUMNAS = ["Informe", "Hora", "Usuario", "Apartados"]


def _caja(padre, texto: str, icono=QMessageBox.Information) -> QMessageBox:
    caja = QMessageBox(padre)
    caja.setWindowFlags(caja.windowFlags() & ~Qt.WindowContextHelpButtonHint)
    caja.setWindowTitle("Informes del agente")
    caja.setText(texto)
    caja.setIcon(icono)
    return caja


def _limpio(t: str) -> str:
    return re.sub(r"[^\w.\- ]", "_", t or "").strip() or "sin_nombre"


def html_informe(informe: dict, cliente: str) -> str:
    secciones = []
    errores = 0
    for clave, _t, seccion_masys, _k, _a in ag.LECTORES:
        s = informe.get("secciones", {}).get(clave)
        if s is None:
            continue
        cat = msys_config.CATEGORIES.get(msys_config.SECTION_CATEGORY.get(seccion_masys, ""), {}).get("label", "")
        if s.get("error"):
            errores += 1
            secciones.append(informe_html.Seccion(s.get("titulo", clave), ["Dato", "Valor"], [],
                                                  aviso=("warning", f"No se pudo leer: {s['error']}"), categoria=cat))
            continue
        tablas = ag.tablas_de_seccion(clave, s)
        if not tablas:
            secciones.append(informe_html.Seccion(s.get("titulo", clave), ["Dato", "Valor"], [],
                                                  nota="Sin elementos en este equipo.", categoria=cat))
        for titulo, cab, filas in tablas:
            nombre = s.get("titulo", clave) if titulo in (s.get("titulo"), "Datos") else f"{s.get('titulo', clave)} › {titulo}"
            secciones.append(informe_html.Seccion(nombre, cab, filas, categoria=cat))
    kpis = [informe_html.Kpi(len(informe.get("secciones", {})), "Apartados"),
            informe_html.Kpi(errores, "Sin leer", "warn" if errores else "ok"),
            informe_html.Kpi("Sí" if informe.get("administrador") else "No", "Administrador",
                             "ok" if informe.get("administrador") else "warn")]
    fecha, hora = _fecha_hora(informe.get("fin") or informe.get("inicio", ""))
    sub = f"{cliente} · usuario {informe.get('usuario', '')} · {fecha} {hora} · agente {informe.get('app', '')}"
    return informe_html.documento(f"Informe del agente: {informe.get('equipo', '')}", secciones, subtitulo=sub,
                                  kpis=kpis, equipo=informe.get("equipo", ""), fecha=f"{fecha} {hora}")


def _fecha_hora(iso: str):
    from msys.domain.acceso_remoto import fecha_hora
    return fecha_hora(iso)


class CodigoTecnicoDialog(QDialog):
    def __init__(self, padre, db: ag.AgenteDB):
        super().__init__(padre)
        self.db = db
        self.setWindowTitle("🔑 Código del técnico")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.addWidget(piezas.nota("El cliente lo pega en el agente. No es secreto: solo sirve para que el archivo "
                                  "lo pueda abrir este MASys."))
        self.txt = QLineEdit()
        self.txt.setReadOnly(True)
        self.txt.setFont(QFont("Consolas", 10))
        self.txt.setMinimumWidth(560)
        lay.addWidget(self.txt)
        self.btn_copiar = piezas.boton("📋 Copiar código", "primary", self.copiar)
        self.btn_instr = piezas.boton("📨 Copiar instrucciones", "ghost", self.copiar_instrucciones,
                                      "Texto para el cliente con el código")
        self.btn_nueva = piezas.boton("↻ Cambiar clave", "ghost", self.cambiar,
                                      "Crea una clave nueva; los informes ya generados se siguen pudiendo abrir")
        cerrar = piezas.boton("Cerrar", "ghost", self.accept)
        lay.addLayout(piezas.fila(self.btn_copiar, self.btn_instr, self.btn_nueva, None, cerrar, estirar=False))
        self.lbl = QLabel("")
        self.lbl.setObjectName("hintLabel")
        lay.addWidget(self.lbl)
        self._pintar()

    def _pintar(self):
        self.codigo = ag.codigo_tecnico(self.db.publica_activa())
        self.txt.setText(self.codigo)

    def copiar(self):
        QGuiApplication.clipboard().setText(self.codigo)
        self.lbl.setText("Código copiado")

    def copiar_instrucciones(self):
        QGuiApplication.clipboard().setText(ag.texto_para_cliente(self.codigo))
        self.lbl.setText("Instrucciones copiadas: pégalas en el mensaje al cliente")

    def cambiar(self):
        caja = _caja(self, "¿Crear una clave nueva? Los agentes con el código anterior seguirán generando archivos que "
                           "este MASys puede abrir, pero conviene enviar el código nuevo.", QMessageBox.Question)
        si = caja.addButton("Sí", QMessageBox.YesRole)
        caja.addButton("No", QMessageBox.NoRole)
        caja.exec_()
        if caja.clickedButton() is si:
            self.db.nueva_clave()
            self._pintar()
            self.lbl.setText("Clave nueva creada")


class InformesAgenteDialog(QDialog):
    def __init__(self, host=None, db: Optional[ag.AgenteDB] = None, clientes_agenda=None, recepcion_=None):
        super().__init__(host)
        self.host = host
        self.db = db if db is not None else ag.AgenteDB()
        # clientes_agenda(): [(cliente, equipo)] de la agenda de Acceso remoto, para proponer el cliente
        self._clientes_agenda = clientes_agenda or (lambda: [])
        self.setWindowTitle("📥 Informes del agente")
        self.setWindowFlags((self.windowFlags() | Qt.WindowMinMaxButtonsHint) & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(760, 480)
        self._filas: List[dict] = []
        c = _colores(host)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)
        cab = QHBoxLayout()
        cab.addWidget(_etiqueta("📥", "font-size: 22pt;", ajustar=False))
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(_etiqueta("Informes del agente", f"font-size: 13pt; font-weight: 600; color: {c['title']};", ajustar=False))
        col.addWidget(_etiqueta("Archivos .msysinf de los equipos de tus clientes", f"color: {c['muted']}; font-size: 9.5pt;",
                                ajustar=False))
        cab.addLayout(col, 1)
        lay.addLayout(cab)

        self.busqueda = QLineEdit()
        self.busqueda.setPlaceholderText("Buscar informe")
        self.busqueda.setClearButtonEnabled(True)
        self.busqueda.textChanged.connect(lambda _t: self._filtrar())
        self.btn_abrir = piezas.boton("📥 Abrir archivo…", "primary", self.abrir_archivo, "Abrir un .msysinf que te ha enviado un cliente")
        self.btn_codigo = piezas.boton("🔑 Código del técnico", "ghost", self.ver_codigo, "El código que el cliente pega en el agente")
        self.lbl_recuento = QLabel("")
        self.lbl_recuento.setObjectName("countLabel")
        barra = QHBoxLayout()
        barra.addWidget(self.busqueda, 2)
        barra.addWidget(self.btn_abrir)
        barra.addWidget(self.btn_codigo)
        barra.addStretch(1)
        barra.addWidget(self.lbl_recuento)
        lay.addLayout(barra)

        # Recepción por Internet (4.7.1): sigue activa aunque se cierre esta ventana, mientras MASys esté abierto
        self.recepcion = recepcion_ if recepcion_ is not None else rec.recepcion()
        self.btn_recibir = QPushButton("📡 Recibir por Internet")
        self.btn_recibir.setObjectName("ghost")
        self.btn_recibir.setCheckable(True)
        self.btn_recibir.setToolTip("Escuchar en el puerto que reenvía tu router para que los informes lleguen solos")
        self.btn_recibir.clicked.connect(lambda marcado: self.cambiar_recepcion(bool(marcado)))
        self.lbl_recepcion = QLabel("")
        self.lbl_recepcion.setObjectName("hintLabel")
        self.btn_envio = piezas.boton("📨 Código de envío…", "ghost", self.codigo_envio,
                                      "Código de un solo uso para que el informe de un cliente te llegue directamente")
        self.btn_conexion = piezas.boton("⚙️ Conexión…", "ghost", self.conexion,
                                         "Puerto en el que se reciben los informes y tu nombre de DNS dinámico")
        lay.addLayout(piezas.fila(self.btn_recibir, self.lbl_recepcion, None, self.btn_conexion, self.btn_envio,
                                  estirar=False))
        self.recepcion.recibido.connect(self._recibido)
        self.recepcion.cambio.connect(self._pintar_recepcion)
        self._pintar_recepcion()

        self.arbol = QTreeWidget()
        self.arbol.setHeaderLabels(COLUMNAS)
        self.arbol.setUniformRowHeights(True)
        self.arbol.setExpandsOnDoubleClick(False)
        self.arbol.setAlternatingRowColors(True)
        h = self.arbol.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(QHeaderView.Interactive)
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        self.arbol.itemClicked.connect(self._clic)
        self.arbol.itemDoubleClicked.connect(lambda it, _c: self.ver_informe() if it.data(0, _ROL_ID) else None)
        self.arbol.currentItemChanged.connect(lambda *_: self._botones())
        self._iconos = _IconosCabecera(self.arbol)
        lay.addWidget(self.arbol, 1)

        self.btn_masys = piezas.boton("🖥️ Ver en MASys", "primary", self.ver_en_masys,
                                      "Sus datos en las secciones de MASys, como si estuvieras en ese equipo (5.2.0)")
        self.btn_ver = piezas.boton("🌐 Ver informe", "ghost", self.ver_informe)
        self.btn_cambios = piezas.boton("⇄ Cambios con el anterior", "ghost", self.ver_cambios,
                                        "Qué ha cambiado desde el informe anterior de este equipo")
        self.btn_quitar = piezas.boton("✕ Quitar", "ghost", self.quitar)
        cerrar = piezas.boton("Cerrar", "ghost", self.accept)
        lay.addLayout(piezas.fila(self.btn_masys, self.btn_ver, self.btn_cambios, None, self.btn_quitar, cerrar,
                                  estirar=False))

        pantalla = host.screen() if host is not None and hasattr(host, "screen") else None
        zona = pantalla.availableGeometry() if pantalla else None
        self.resize(min(980, zona.width() - 40) if zona else 980, min(620, zona.height() - 60) if zona else 620)
        self.recargar()

    def showEvent(self, e):
        super().showEvent(e)
        self._iconos.recolocar()

    # ── Lista ───────────────────────────────────────────────────────────
    def recargar(self, seleccionar: Optional[str] = None):
        try:
            self._filas = self.db.listar_informes()
        except Exception as e:
            self._filas = []
            _caja(self, f"No se pudieron leer los informes: {e}", QMessageBox.Warning).exec_()
        self.arbol.clear()
        clientes, equipos = {}, {}
        sel = None
        for f in self._filas:
            nc = clientes.get(f["cliente"])
            if nc is None:
                nc = clientes[f["cliente"]] = QTreeWidgetItem(self.arbol, [f["cliente"]])
                fuente = nc.font(0)
                fuente.setBold(True)
                nc.setFont(0, fuente)
                nc.setFirstColumnSpanned(True)
            ne = equipos.get((f["cliente"], f["equipo"]))
            if ne is None:
                ne = equipos[(f["cliente"], f["equipo"])] = QTreeWidgetItem(nc, [f"🖥️ {f['equipo']}"])
                ne.setFirstColumnSpanned(True)
            fecha, hora = ag_fecha(f["fecha"])
            it = QTreeWidgetItem(ne, [fecha, hora, f.get("usuario", ""), f.get("apartados", "")])
            it.setData(0, _ROL_ID, f["id"])
            if f["id"] == seleccionar:
                sel = it
        for nc in clientes.values():
            nc.setText(0, f"{nc.text(0)}  ({sum(nc.child(i).childCount() for i in range(nc.childCount()))})")
        self.arbol.expandAll()
        for c in range(1, len(COLUMNAS)):
            self.arbol.resizeColumnToContents(c)
            self.arbol.setColumnWidth(c, max(self.arbol.columnWidth(c) + 14, 90))
        if sel is not None:
            self.arbol.setCurrentItem(sel)
        self._filtrar()

    def _filtrar(self):
        texto = self.busqueda.text().strip().lower()
        visibles = 0
        for i in range(self.arbol.topLevelItemCount()):
            nc = self.arbol.topLevelItem(i)
            vis_c = 0
            for j in range(nc.childCount()):
                ne = nc.child(j)
                vis_e = 0
                for k in range(ne.childCount()):
                    it = ne.child(k)
                    ver = not texto or any(texto in x.lower() for x in (nc.text(0), ne.text(0), it.text(0), it.text(2)))
                    it.setHidden(not ver)
                    vis_e += ver
                ne.setHidden(vis_e == 0)
                vis_c += vis_e
            nc.setHidden(vis_c == 0)
            visibles += vis_c
        total = len(self._filas)
        self.lbl_recuento.setText(f"{visibles} de {total}" if visibles != total else ("1 informe" if total == 1 else f"{total} informes"))
        self._botones()

    def _clic(self, item, _col):
        if not item.data(0, _ROL_ID):
            item.setExpanded(not item.isExpanded())

    def _actual(self) -> Optional[str]:
        it = self.arbol.currentItem()
        return it.data(0, _ROL_ID) if it is not None else None

    def _botones(self):
        hay = self._actual() is not None
        for b in (self.btn_masys, self.btn_ver, self.btn_cambios, self.btn_quitar):
            b.setEnabled(hay)

    # ── Recepción por Internet ──────────────────────────────────────────
    def _pintar_recepcion(self):
        try:
            activa = self.recepcion.activa
            self.btn_recibir.setChecked(activa)
            self.lbl_recepcion.setText(self.recepcion.estado())
            self.lbl_recepcion.setStyleSheet("color: #16a34a; font-weight: 600; font-style: normal;" if activa else "")
        except RuntimeError:
            pass   # la ventana ya se cerró

    def cambiar_recepcion(self, activar: bool):
        if activar:
            def confirmar(texto):
                caja = _caja(self, texto, QMessageBox.Question)
                si = caja.addButton("Sí", QMessageBox.YesRole)
                caja.addButton("No", QMessageBox.NoRole)
                caja.exec_()
                return caja.clickedButton() is si
            error = self.recepcion.activar(confirmar=confirmar, auditoria=self._auditoria())
            if error:
                _caja(self, error, QMessageBox.Warning).exec_()
        else:
            self.recepcion.desactivar()
        self._pintar_recepcion()

    def _auditoria(self):
        return getattr(self.host, "repair_audit_db", None) or getattr(getattr(self.host, "host", None),
                                                                      "repair_audit_db", None)

    def conexion(self):
        rec.ConexionDialog(self, self.recepcion, self._auditoria()).exec_()
        self._pintar_recepcion()

    def codigo_envio(self):
        clientes = [c for c, _e in self._clientes_agenda()] + [f["cliente"] for f in self._filas]
        it = self.arbol.currentItem()
        actual = ""
        while it is not None:
            if it.parent() is None:
                actual = re.sub(r"\s+\(\d+\)$", "", it.text(0))
            it = it.parent()
        rec.CodigoEnvioDialog(self, self.recepcion, clientes, actual).exec_()
        self._pintar_recepcion()

    def _recibido(self, cliente: str, equipo: str, iid: str):
        try:
            self.recargar(seleccionar=iid)
            self._pintar_recepcion()
        except RuntimeError:
            pass

    def done(self, r):
        # La recepción sigue; esta ventana deja de escuchar sus avisos
        for senal, slot in ((self.recepcion.recibido, self._recibido), (self.recepcion.cambio, self._pintar_recepcion)):
            try:
                senal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        super().done(r)

    # ── Acciones ────────────────────────────────────────────────────────
    def ver_codigo(self):
        CodigoTecnicoDialog(self, self.db).exec_()

    def abrir_archivo(self, ruta: str = ""):
        ruta = ruta or dialogo_abrir_archivo(self, "Abrir informe del agente", os.path.expanduser("~\\Downloads"),
                                             "Informes del agente (*.msysinf)")
        if not ruta:
            return
        try:
            with open(ruta, "rb") as f:
                contenido = f.read()
            informe = ag.descifrar(contenido, self.db.privadas())
        except (OSError, ag.ErrorAgente, ValueError) as e:
            _caja(self, f"No se pudo abrir el archivo: {e}", QMessageBox.Warning).exec_()
            return
        cliente = self._pedir_cliente(informe.get("equipo", ""))
        if not cliente:
            return
        try:
            iid = self.db.guardar_informe(informe, cliente)
        except Exception as e:
            _caja(self, f"No se pudo guardar el informe: {e}", QMessageBox.Warning).exec_()
            return
        self.recargar(seleccionar=iid)

    def _pedir_cliente(self, equipo: str) -> str:
        agenda = self._clientes_agenda()
        propuesto = next((c for c, e in agenda if e.lower() == equipo.lower()), "")
        nombres = sorted({c for c, _e in agenda} | {f["cliente"] for f in self._filas}, key=str.lower)
        if not propuesto:
            propuesto = next((f["cliente"] for f in self._filas if f["equipo"].lower() == equipo.lower()), "")
        dlg = QInputDialog(self)
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dlg.setWindowTitle("Informe del agente")
        dlg.setLabelText(f"¿De qué cliente es «{equipo}»?")
        dlg.setComboBoxItems(nombres or [""])
        dlg.setComboBoxEditable(True)
        dlg.setTextValue(propuesto)
        dlg.setOkButtonText("Guardar")
        dlg.setCancelButtonText("Cancelar")
        combo = dlg.findChild(QComboBox)
        if combo is not None:
            combo.setEditText(propuesto)
        if dlg.exec_() != QDialog.Accepted:
            return ""
        return dlg.textValue().strip()

    def _carpeta(self, cliente: str) -> str:
        carpeta = os.path.join(get_informes_folder(), "Agente", _limpio(cliente))
        os.makedirs(carpeta, exist_ok=True)
        return carpeta

    def _ventana_principal(self):
        """La ventana de MASys (la que tiene el modo equipo remoto), subiendo por host/padre."""
        w, vistos = self.host, 0
        while w is not None and vistos < 6 and not hasattr(w, "ver_equipo_remoto"):
            w = getattr(w, "host", None) or (w.parent() if hasattr(w, "parent") else None)
            vistos += 1
        return w if w is not None and hasattr(w, "ver_equipo_remoto") else None

    def ver_en_masys(self):
        iid = self._actual()
        if not iid:
            _caja(self, "Elige un informe de la lista.").exec_()
            return
        ventana = self._ventana_principal()
        if ventana is None:
            _caja(self, "Abre esta ventana desde MASys (Acceso remoto) para ver el informe en sus secciones.").exec_()
            return
        fila = next(f for f in self._filas if f["id"] == iid)
        try:
            informe = self.db.informe(iid)
        except ag.ErrorAgente as e:
            _caja(self, str(e), QMessageBox.Warning).exec_()
            return
        if ventana.ver_equipo_remoto(informe, fila["cliente"]):
            self.accept()
            ventana.showNormal()
            ventana.raise_()
            ventana.activateWindow()

    def ver_informe(self):
        iid = self._actual()
        if not iid:
            return
        fila = next(f for f in self._filas if f["id"] == iid)
        try:
            informe = self.db.informe(iid)
            html = html_informe(informe, fila["cliente"])
            fecha = (fila["fecha"] or "").replace("-", "").replace(":", "").replace("T", "_")[:13]
            ruta = os.path.join(self._carpeta(fila["cliente"]), f"Informe_{_limpio(fila['equipo'])}_{fecha}.html")
            informe_html.guardar(ruta, html)
        except (ag.ErrorAgente, OSError) as e:
            _caja(self, str(e), QMessageBox.Warning).exec_()
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(ruta))
        self.ultima_ruta = ruta

    def cambios(self, iid: str):
        """(cambios, informe anterior, informe actual) o None si no hay anterior."""
        anterior = self.db.anterior(iid)
        if not anterior:
            return None
        a, d = self.db.informe(anterior), self.db.informe(iid)
        return visitas.comparar(ag.foto_para_visitas(a), ag.foto_para_visitas(d)), a, d

    def ver_cambios(self):
        iid = self._actual()
        if not iid:
            return
        try:
            res = self.cambios(iid)
        except ag.ErrorAgente as e:
            _caja(self, str(e), QMessageBox.Warning).exec_()
            return
        if res is None:
            _caja(self, "No hay un informe anterior de este equipo para comparar.").exec_()
            return
        lista, a, d = res
        CambiosDialog(self, lista, a, d).exec_()

    def quitar(self):
        iid = self._actual()
        if not iid:
            return
        fila = next(f for f in self._filas if f["id"] == iid)
        f, h = ag_fecha(fila["fecha"])
        caja = _caja(self, f"¿Quitar el informe de «{fila['equipo']}» del {f} {h}?", QMessageBox.Question)
        si = caja.addButton("Sí", QMessageBox.YesRole)
        caja.addButton("No", QMessageBox.NoRole)
        caja.exec_()
        if caja.clickedButton() is not si:
            return
        self.db.eliminar_informe(iid)
        self.recargar()


def ag_fecha(iso: str):
    return _fecha_hora(iso)


class CambiosDialog(QDialog):
    """Diferencias entre dos informes del mismo equipo (como «Cambios desde la última visita»)."""

    def __init__(self, padre, cambios, antes: dict, despues: dict):
        super().__init__(padre)
        fa, ha = ag_fecha(antes.get("fin") or antes.get("inicio", ""))
        fd, hd = ag_fecha(despues.get("fin") or despues.get("inicio", ""))
        self.setWindowTitle(f"⇄ {despues.get('equipo', '')}: cambios del {fa} {ha} al {fd} {hd}")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.resize(980, 540)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 12)
        self.busqueda = QLineEdit()
        self.busqueda.setPlaceholderText("Buscar elemento")
        self.busqueda.setClearButtonEnabled(True)
        self.recuento = QLabel(f"{len(cambios)} cambios")
        self.recuento.setObjectName("countLabel")
        resumen = visitas.resumen(cambios) or "Sin cambios en los apartados que se comparan"
        lay.addLayout(piezas.fila(self.busqueda, self.recuento, estirar=False))
        lay.addWidget(piezas.nota(resumen))
        self.modelo = TablaSimple(["Módulo", "Cambio", "Elemento", "Antes", "Ahora"])
        self.tabla, _p = piezas.tabla(self.modelo, self, columna_ancha=2, busqueda=self.busqueda)
        self.modelo.set_datos([[c.modulo, c.tipo, c.elemento, c.antes, c.despues] for c in cambios], cambios)
        lay.addWidget(self.tabla, 1)
        lay.addWidget(piezas.nota("Solo se comparan los apartados leídos en los dos informes."))
        lay.addLayout(piezas.fila(None, piezas.boton("Cerrar", "ghost", self.accept), estirar=False))
