# -*- coding: utf-8 -*-
"""Modo «equipo remoto» de la ventana principal (5.2.0). Motor en msys/domain/equipo_remoto.py.

Al entrar:
  1. En cada módulo de la interfaz, la clase del recolector se sustituye por una que entrega los datos del agente
     (y Red local no sondea IP desde este PC). Los informes y exportaciones llevan el nombre del equipo del
     cliente (informe_html.EQUIPO_FORZADO y _nombre_equipo(), que usa reporting).
  2. El árbol enseña solo las secciones que trajo el agente; encima del contenido, una franja ámbar dice de qué
     equipo son los datos.
  3. Las acciones quedan BLOQUEADAS (se ejecutarían en ESTE PC): solo se permiten navegar, filtrar, ordenar,
     actualizar (que vuelve a enseñar los datos del agente) y exportar. «Sincronizar Todo» tampoco (leería este
     equipo y guardaría una visita con datos mezclados).
  4. Se pulsan los «Actualizar» de esas secciones, en el orden de Sincronizar Todo.
Al salir se deshace todo y se vuelve a leer ESTE equipo con «Sincronizar Todo» (las secciones tenían datos del
cliente)."""

import importlib
import logging
import re
from typing import List, Optional

from PyQt5.QtCore import QEvent, QObject, Qt, QTimer
from PyQt5.QtWidgets import (
    QAbstractButton, QAbstractItemView, QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton,
    QRadioButton, QVBoxLayout, QWidget,
)

from msys.domain import equipo_remoto as er
from msys.ui import piezas

logger = logging.getLogger(__name__)

# DENTRO de las secciones solo se pueden pulsar (por su texto exacto) «🔄 Actualizar» (vuelve a enseñar los datos
# del agente), exportar, buscar/filtrar y copiar al portapapeles. Todo lo demás queda bloqueado: OJO, muchos botones
# que empiezan por 🔄 son acciones («🔄 Reiniciar Adaptador», «🔄 Reiniciar servicio», «🔄 Restablecer todo»…), y
# «Actualizar …» puede ser instalar (paquetes). Comprobado contra la ventana real (5.2.0).
_PERMITIDO = re.compile(r"^🔄 Actualizar$|^(📄|🌐|📕|📊)\s*(Exportar\s+)?(CSV|Excel|HTML|PDF)$|^🌐 Informe HTML$|"
                        r"^🔍?\s*(Buscar|Filtrar|Limpiar filtros?)$|^📋 Copiar$")
# FUERA de las secciones (cabecera, barra de abajo, panel lateral) se permite todo salvo estos (leen, reparan o
# guardan ESTE equipo, o guardarían los datos del cliente como si fueran de este PC):
PROHIBIDOS_FUERA = ("btn_buscar_todo", "btn_copias_seguridad", "btn_seguridad", "btn_soporte", "btn_snapshots",
                    "btn_guardar_resultados")
AVISO_BLOQUEO = ("🔒 Modo equipo remoto: las acciones están desactivadas (se harían en ESTE PC, no en el del "
                 "cliente). Puedes ver, filtrar y exportar.")


def _repeticion(original, datos):
    """Subclase del recolector cuyo hilo entrega los datos del agente (hereda todas sus señales)."""
    class Repeticion(original):
        def run(self):
            self.finished_ok.emit(datos)
    Repeticion.__name__ = Repeticion.__qualname__ = original.__name__
    Repeticion.masys_remoto = True
    return Repeticion


def _hilo_mudo(original):
    """Hilo que no hace nada: lo que consultaría desde este PC sobre datos de otro equipo."""
    class Mudo(original):
        def run(self):
            return
    Mudo.__name__ = Mudo.__qualname__ = original.__name__
    Mudo.masys_remoto = True
    return Mudo


class BloqueoAcciones(QObject):
    """Filtro de eventos de la aplicación mientras dura el modo remoto."""

    _TIPOS = (QEvent.MouseButtonPress, QEvent.MouseButtonRelease, QEvent.MouseButtonDblClick, QEvent.KeyPress,
              QEvent.ContextMenu)

    def __init__(self, ventana, permitidos: List[QWidget], prohibidos: List[QWidget], avisar, zona=None):
        super().__init__(ventana)
        self.ventana, self.permitidos, self.prohibidos, self.avisar = ventana, permitidos, prohibidos, avisar
        self.zona = zona                                 # el contenido de las secciones (content_stack)

    def bloquea(self, obj, ev) -> bool:
        if ev.type() not in self._TIPOS or not isinstance(obj, QWidget) or obj.window() is not self.ventana:
            return False
        if any(p is obj or p.isAncestorOf(obj) for p in self.permitidos):
            return False
        tipo = ev.type()
        if obj in self.prohibidos:
            return tipo != QEvent.KeyPress or ev.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space)
        if self.zona is not None and not (self.zona is obj or self.zona.isAncestorOf(obj)):
            return False                                 # fuera de las secciones (salvo los prohibidos)
        if tipo == QEvent.ContextMenu:
            return True                                  # los menús de clic derecho son acciones
        if tipo == QEvent.MouseButtonDblClick and isinstance(obj.parent(), QAbstractItemView):
            return True                                  # doble clic en una fila: abre propiedades o acciones
        if not isinstance(obj, QAbstractButton):
            return False
        if tipo == QEvent.KeyPress and ev.key() not in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            return False
        if isinstance(obj, (QCheckBox, QRadioButton)):
            return False                                 # filtros de las tablas
        return not _PERMITIDO.search((obj.text() or "").strip())

    def eventFilter(self, obj, ev):
        if self.bloquea(obj, ev):
            if ev.type() in (QEvent.MouseButtonRelease, QEvent.ContextMenu, QEvent.KeyPress):
                self.avisar()
            return True
        return False


class InformeRecibidoDialog(QDialog):
    """«📥 Informe recibido» (5.2.0): el agente acaba de enviar el informe por Internet."""

    def __init__(self, host, cliente: str, equipo: str, iid: str, cargar):
        super().__init__(host)
        self.host, self.cliente, self.iid, self._cargar = host, cliente, iid, cargar
        self.setWindowTitle("📥 Informe del agente recibido")
        self.setWindowFlags((self.windowFlags() | Qt.WindowStaysOnTopHint) & ~Qt.WindowContextHelpButtonHint)
        self.setModal(False)
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(8)
        lbl = QLabel(f"<b>{equipo}</b> · {cliente}")
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(lbl)
        lay.addWidget(piezas.nota("Míralo en MASys como si estuvieras en ese equipo, con sus secciones de siempre. "
                                  "Las acciones quedan desactivadas mientras lo ves."))
        self.btn_ver = piezas.boton("🖥️ Ver en MASys", "primary", self.ver)
        self.btn_luego = piezas.boton("Más tarde", "ghost", self.close)
        lay.addLayout(piezas.fila(self.btn_ver, None, self.btn_luego, estirar=False))

    def ver(self):
        try:
            informe = self._cargar(self.iid)
        except Exception as e:
            QMessageBox.warning(self, "Informe del agente", f"No se pudo abrir el informe: {e}")
            return
        self.close()
        if self.host.ver_equipo_remoto(informe, self.cliente):
            self.host.showNormal()
            self.host.raise_()
            self.host.activateWindow()


class EquipoRemotoMixin:
    """Para la ventana principal (multisys.py)."""

    def en_modo_remoto(self) -> bool:
        return getattr(self, "_remoto", None) is not None

    def _nombre_equipo(self) -> str:
        """El equipo del que son los datos que se ven: el del cliente en modo remoto (informes y exportaciones)."""
        if self.en_modo_remoto():
            return self._remoto["informe"].get("equipo") or "equipo remoto"
        import socket
        return socket.gethostname()

    # ── Entrar ──────────────────────────────────────────────────────────
    def ver_equipo_remoto(self, informe: dict, cliente: str = "") -> bool:
        apartados = er.apartados(informe)
        if not apartados:
            self._aviso_remoto("Este informe no trae ningún apartado que se pueda enseñar.")
            return False
        trabajando = getattr(self, "worker", None)
        # Una lectura REAL de este PC en marcha mezclaría datos; la entrega de los datos de otro informe, no
        if trabajando is not None and trabajando.isRunning() and not getattr(type(trabajando), "masys_remoto", False):
            self._aviso_remoto("Hay una lectura en curso en MASys: espera a que termine y vuelve a intentarlo.")
            return False
        if self.en_modo_remoto():
            self._quitar_sustituciones()                  # cambio de un equipo remoto a otro
        else:
            self._arbol_antes = list(getattr(self, "tree_items", {}).keys())
        self._remoto = {"informe": informe, "cliente": cliente, "rotulo": er.rotulo(informe, cliente),
                        "turno": object()}
        self._sustituir(apartados, informe.get("equipo") or "equipo-remoto")
        self._franja(True)
        self._etiquetas_equipo(True)
        self._instalar_bloqueo()
        secciones = [a.seccion for a in apartados if a.seccion in self.tab_widgets]
        self._build_nav_tree(name_list=secciones)
        faltan = er.sin_datos(informe)
        texto = f"🛰️ Viendo {self._remoto['rotulo']}"
        if faltan:
            texto += f" · sin datos: {', '.join(faltan[:4])}" + ("…" if len(faltan) > 4 else "")
        getattr(self, "set_status", lambda *_a: None)(texto)
        self._cargar_apartados([a.refresco for a in apartados], self._remoto["turno"], secciones)
        return True

    def _sustituir(self, apartados, nombre_equipo: str):
        self._sustituciones = []

        def poner(modulo: str, nombre: str, fabrica):
            try:
                m = importlib.import_module(modulo)
                original = getattr(m, nombre)
            except (ImportError, AttributeError) as e:
                logger.warning("Modo remoto: no se pudo sustituir %s.%s: %s", modulo, nombre, e)
                return
            self._sustituciones.append((m, nombre, original))
            setattr(m, nombre, fabrica(original))

        for a in apartados:
            poner(a.modulo, a.clase, lambda original, datos=a.datos: _repeticion(original, datos))
        for modulo, nombre in er.HILOS_LOCALES:
            poner(modulo, nombre, _hilo_mudo)
        poner("msys.core.informe_html", "EQUIPO_FORZADO", lambda _antes: nombre_equipo)

    def _quitar_sustituciones(self):
        for m, nombre, original in reversed(getattr(self, "_sustituciones", [])):
            setattr(m, nombre, original)
        self._sustituciones = []

    def _cargar_apartados(self, pendientes: List[str], turno, secciones: List[str]):
        """Pulsa los «Actualizar» uno tras otro (cada refresh_* espera a que no haya otra lectura en marcha)."""
        if not self.en_modo_remoto() or self._remoto.get("turno") is not turno:
            return                                         # se salió o se cambió de equipo mientras tanto
        trabajando = getattr(self, "worker", None)
        if trabajando is not None and trabajando.isRunning():
            QTimer.singleShot(80, lambda: self._cargar_apartados(pendientes, turno, secciones))
            return
        if not pendientes:
            if secciones:
                self.seleccionar_seccion(secciones[0])
            return
        try:
            getattr(self, pendientes[0])()
        except Exception as e:
            logger.error("Modo remoto: %s falló: %s", pendientes[0], e)
        QTimer.singleShot(40, lambda: self._cargar_apartados(pendientes[1:], turno, secciones))

    # ── Franja y bloqueo ────────────────────────────────────────────────
    def _franja(self, visible: bool):
        franja = getattr(self, "_franja_remota", None)
        if franja is None:
            franja = self._franja_remota = QFrame()
            franja.setObjectName("franjaRemota")
            franja.setStyleSheet("#franjaRemota { background: #f59e0b; border-radius: 6px; } "
                                 "#franjaRemota QLabel { color: #1a1a2e; font-weight: 600; background: transparent; }")
            fila = QHBoxLayout(franja)
            fila.setContentsMargins(12, 6, 8, 6)
            self._lbl_franja = QLabel("")
            self._lbl_franja.setWordWrap(True)
            fila.addWidget(self._lbl_franja, 1)
            self.btn_volver_equipo = QPushButton("↩ Volver a este equipo")
            self.btn_volver_equipo.setToolTip("Deja de ver el equipo del cliente y vuelve a leer ESTE equipo")
            self.btn_volver_equipo.clicked.connect(lambda _=False: self.salir_equipo_remoto())
            fila.addWidget(self.btn_volver_equipo, 0)
            divisor = self.content_stack.parentWidget()
            contenedor = divisor.parentWidget().layout() if divisor is not None and divisor.parentWidget() else None
            if contenedor is not None:
                contenedor.insertWidget(contenedor.indexOf(divisor), franja)
        if visible:
            self._lbl_franja.setText(f"🛰️ Estás viendo {self._remoto['rotulo']}. No es este PC: las acciones "
                                     "están desactivadas.")
        franja.setVisible(visible)

    def _etiquetas_equipo(self, remoto: bool):
        """Las secciones crean al abrirse una etiqueta fija «🖥️ <este PC>» en su barra: en modo remoto dice el equipo
        del cliente (y al salir vuelve a decir este PC)."""
        import socket
        from PyQt5.QtWidgets import QLabel
        if remoto:
            local = socket.gethostname()
            nuevo = f"🛰️ {self._remoto['informe'].get('equipo') or 'equipo remoto'}"
            cambiadas = getattr(self, "_etiquetas_cambiadas", [])
            vistas = {id(lbl) for lbl, _t in cambiadas}
            for widget in getattr(self, "tab_widgets", {}).values():
                for lbl in widget.findChildren(QLabel):
                    texto = lbl.text().strip()
                    if id(lbl) not in vistas and texto in (local, f"🖥️ {local}", f"🖥 {local}"):
                        cambiadas.append((lbl, lbl.text()))
            for lbl, _t in cambiadas:
                lbl.setText(nuevo)
            self._etiquetas_cambiadas = cambiadas
        else:
            for lbl, original in getattr(self, "_etiquetas_cambiadas", []):
                try:
                    lbl.setText(original)
                except RuntimeError:
                    pass
            self._etiquetas_cambiadas = []

    def _instalar_bloqueo(self):
        from PyQt5.QtWidgets import QApplication
        if getattr(self, "_bloqueo_remoto", None) is not None:
            return
        permitidos = [w for w in (getattr(self, "_franja_remota", None), getattr(self, "nav_tree", None)) if w]
        prohibidos = [getattr(self, n) for n in PROHIBIDOS_FUERA if getattr(self, n, None) is not None]
        self._bloqueo_remoto = BloqueoAcciones(self, permitidos, prohibidos,
                                               lambda: getattr(self, "set_status", lambda *_a: None)(AVISO_BLOQUEO),
                                               zona=getattr(self, "content_stack", None))
        QApplication.instance().installEventFilter(self._bloqueo_remoto)

    def _quitar_bloqueo(self):
        from PyQt5.QtWidgets import QApplication
        b = getattr(self, "_bloqueo_remoto", None)
        if b is not None:
            QApplication.instance().removeEventFilter(b)
            b.deleteLater()
        self._bloqueo_remoto = None

    # ── Salir ───────────────────────────────────────────────────────────
    def salir_equipo_remoto(self, releer: bool = True):
        if not self.en_modo_remoto():
            return
        self._quitar_sustituciones()
        self._quitar_bloqueo()
        self._franja(False)
        self._etiquetas_equipo(False)
        self._remoto = None
        antes = getattr(self, "_arbol_antes", None)
        self._build_nav_tree(name_list=antes or None)
        if self.tree_items:
            self.seleccionar_seccion(next(iter(self.tree_items)))
        getattr(self, "set_status", lambda *_a: None)("↩ De vuelta en este equipo: volviendo a leer sus datos…")
        if releer and hasattr(self, "buscar_todo"):
            QTimer.singleShot(100, self.buscar_todo)      # las secciones tenían datos del cliente

    # ── Avisos ──────────────────────────────────────────────────────────
    def _aviso_remoto(self, texto: str):
        caja = QMessageBox(self)
        caja.setWindowFlags(caja.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        caja.setWindowTitle("Equipo remoto")
        caja.setText(texto)
        caja.exec_()

    def aviso_informe_recibido(self, cliente: str, equipo: str, iid: str, cargar=None) -> Optional[QDialog]:
        """Lo llama la recepción por Internet (recepcion_agente.arrancar_si_toca) al llegar un informe."""
        if cargar is None:
            from msys.domain import agente as ag
            cargar = lambda i: ag.AgenteDB().informe(i)  # noqa: E731
        dlg = InformeRecibidoDialog(self, cliente, equipo, iid, cargar)
        dlg.show()
        return dlg
