# -*- coding: utf-8 -*-
"""Recepción por Internet de los informes del agente (4.7.1) en el MASys del técnico.

- RecepcionAgente: una sola por MASys (recepcion()). Arranca/para el servidor
  (msys/domain/agente_envio.py), recuerda si estaba activa (se reactiva al
  abrir MASys: arrancar_si_toca) y avisa con la señal «recibido» (desde el hilo
  del servidor al de la ventana) para refrescar la lista y avisar en la bandeja.
- CodigoEnvioDialog: crea un código de envío MSE1 para un cliente (dirección,
  caducidad) y lista los que siguen sin usar, con «✕ Anular».

Se usa desde «📥 Informes del agente» (msys/ui/informes_agente_dialog.py)."""

import re
from typing import Callable, List, Optional

from PyQt5.QtCore import QObject, Qt, pyqtSignal
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtWidgets import (QApplication, QComboBox, QDialog, QLabel, QLineEdit, QMessageBox, QSpinBox,
                             QVBoxLayout)

from msys.domain import agente as ag
from msys.domain import agente_envio as envio
from msys.models.tabla_simple import TablaSimple
from msys.ui import piezas

SECCION = "📥 Informes del agente"


class RecepcionAgente(QObject):
    recibido = pyqtSignal(str, str, str)     # cliente, equipo, id del informe
    cambio = pyqtSignal()                    # se activó o se paró

    def __init__(self, db: Optional[ag.AgenteDB] = None, puerto: Optional[int] = None, direccion: str = "0.0.0.0"):
        super().__init__()
        self.db = db if db is not None else ag.AgenteDB()
        self.tickets = envio.TicketsDB(self.db)
        if puerto is None:     # el elegido en «⚙️ Puerto…» (4.8.1) o el de siempre
            try:
                puerto = envio.validar_puerto(self.tickets.ajuste("puerto", str(envio.PUERTO)))
            except ValueError:
                puerto = envio.PUERTO
        self.servidor = envio.ServidorAgente(self.tickets, al_recibir=lambda c, e, i: self.recibido.emit(c, e, i),
                                             puerto=puerto, direccion=direccion)

    @property
    def activa(self) -> bool:
        return self.servidor.activo

    def activar(self, confirmar: Callable[[str], bool] = lambda _t: True, recordar: bool = True,
                auditoria=None) -> str:
        """"" si quedó escuchando; si no, el motivo. confirmar(texto) decide si se abre el firewall."""
        if self.servidor.activo:
            return ""
        try:
            from msys.core.utils import is_admin
            admin = bool(is_admin())
        except Exception:
            admin = False
        if admin and not envio.regla_firewall_existe(self.servidor.puerto):
            if confirmar(f"Para recibir informes por Internet hay que abrir el puerto {self.servidor.puerto} en el firewall de "
                         "Windows, solo para redes privadas y de dominio (el router lo reenvía a este PC). ¿Abrirlo?"):
                ok, msg = envio.crear_regla_firewall(self.servidor.puerto)
                if auditoria is not None:
                    try:
                        auditoria.registrar(seccion=SECCION, accion="Regla de firewall",
                                            objetivo=f"TCP {self.servidor.puerto} privado", exito=ok, detalle=msg or "OK")
                    except Exception:
                        pass
        try:
            self.servidor.arrancar()
        except OSError as e:
            return f"No se pudo escuchar en el puerto {self.servidor.puerto}: {e}"
        if recordar:
            self.tickets.fijar_ajuste("recibir", "1")
        self.cambio.emit()
        return ""

    def cambiar_puerto(self, valor, confirmar: Callable[[str], bool] = lambda _t: True, auditoria=None) -> str:
        """"" si quedó cambiado (y escuchando, si lo estaba); si no, el motivo y se queda el puerto anterior."""
        try:
            nuevo = envio.validar_puerto(valor)
        except ValueError as e:
            return str(e)
        anterior = self.servidor.puerto
        if nuevo == anterior:
            return ""
        estaba = self.servidor.activo
        if estaba:
            self.servidor.parar()
        self.servidor.puerto = nuevo
        if estaba:
            error = self.activar(confirmar=confirmar, recordar=False, auditoria=auditoria)
            if error:
                self.servidor.puerto = anterior
                try:
                    self.servidor.arrancar()
                except OSError:
                    pass
                self.cambio.emit()
                return error
        self.tickets.fijar_ajuste("puerto", str(nuevo))
        self.cambio.emit()
        return ""

    def desactivar(self, recordar: bool = True):
        self.servidor.parar()
        if recordar:
            self.tickets.fijar_ajuste("recibir", "0")
        self.cambio.emit()

    def estado(self) -> str:
        pendientes = len(self.tickets.pendientes())
        codigos = f" · {pendientes} código{'s' if pendientes != 1 else ''} sin usar" if pendientes else ""
        if self.activa:
            return f"Recibiendo en el puerto {self.servidor.puerto}{codigos}"
        return f"Apagado{codigos}"


_INSTANCIA: Optional[RecepcionAgente] = None


def recepcion() -> RecepcionAgente:
    global _INSTANCIA
    if _INSTANCIA is None:
        _INSTANCIA = RecepcionAgente()
    return _INSTANCIA


def arrancar_si_toca(host) -> None:
    """Al abrir MASys: si la recepción estaba activa, se vuelve a activar (sin preguntar nada) y los
    informes que lleguen se avisan en la bandeja."""
    try:
        r = recepcion()
        r.recibido.connect(lambda cliente, equipo, _i: getattr(host, "tray_notify", lambda *a: None)(
            "📥 Informe del agente recibido", f"{equipo} · {cliente}"))
        # 5.2.0: ofrecer verlo en MASys como si se estuviera en ese equipo
        if hasattr(host, "aviso_informe_recibido"):
            r.recibido.connect(lambda cliente, equipo, iid: host.aviso_informe_recibido(cliente, equipo, iid))
        if r.tickets.ajuste("recibir") == "1":
            r.activar(confirmar=lambda _t: False, recordar=False)
    except Exception:
        pass   # la recepción nunca debe impedir que MASys arranque


def _caja(padre, texto: str, icono=QMessageBox.Information) -> QMessageBox:
    caja = QMessageBox(padre)
    caja.setWindowFlags(caja.windowFlags() & ~Qt.WindowContextHelpButtonHint)
    caja.setWindowTitle("Código de envío")
    caja.setText(texto)
    caja.setIcon(icono)
    return caja


def _confirmar(padre, texto: str) -> bool:
    caja = _caja(padre, texto, QMessageBox.Question)
    si = caja.addButton("Sí", QMessageBox.YesRole)
    caja.addButton("No", QMessageBox.NoRole)
    caja.exec_()
    return caja.clickedButton() is si


class ConexionDialog(QDialog):
    """«⚙️ Conexión…» (4.8.1-4.8.2): el puerto en el que MASys recibe los informes y la dirección (nombre de DNS
    dinámico o IP pública) que llevan los códigos de envío, con una comprobación de que esa dirección lleva aquí."""

    def __init__(self, padre, r: RecepcionAgente, auditoria=None):
        super().__init__(padre)
        self.r, self.auditoria = r, auditoria
        self._comprobar = envio.comprobar_direccion     # las pruebas lo sustituyen: sin Internet
        self._ip_publica = envio.ip_publica
        self.setWindowTitle("⚙️ Conexión para recibir informes")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(600)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)

        lay.addWidget(piezas.nota(f"Puerto: en el router, redirige este mismo puerto (TCP) hacia este PC. "
                                  f"El de siempre es {envio.PUERTO}."))
        self.puerto = QSpinBox()
        self.puerto.setRange(1024, 65535)
        self.puerto.setValue(r.servidor.puerto)
        self.puerto.setMinimumWidth(110)
        self.btn_defecto = piezas.boton(f"Volver al {envio.PUERTO}", "ghost",
                                        lambda: self.puerto.setValue(envio.PUERTO))
        lay.addLayout(piezas.fila(QLabel("Puerto"), self.puerto, self.btn_defecto))
        pendientes = len(r.tickets.pendientes())
        quien = ("El código de envío sin usar lleva" if pendientes == 1
                 else f"Los {pendientes} códigos de envío sin usar llevan")
        self.lbl_codigos = piezas.nota(
            f"{quien} el puerto {r.servidor.puerto}: si lo cambias, no se podrá enviar directamente con "
            f"{'él' if pendientes == 1 else 'ellos'} (el agente dejará el archivo en el Escritorio del cliente)."
            if pendientes else "")
        self.lbl_codigos.setVisible(bool(pendientes))
        lay.addWidget(self.lbl_codigos)

        lay.addWidget(piezas.nota("Dirección: el nombre de DNS dinámico que diste de alta en el router o en el "
                                  "proveedor (No-IP, DynDNS…), o tu IP pública. Va dentro de cada código de envío."))
        self.direccion = QLineEdit(r.tickets.ajuste("direccion"))
        self.direccion.setPlaceholderText("Nombre de DNS dinámico o IP pública")
        self.direccion.setMinimumWidth(260)
        self.btn_ip = piezas.boton("🌐 Mi IP pública", "ghost", self.detectar_ip,
                                   "Poner la IP con la que este equipo sale a Internet")
        self.btn_comprobar = piezas.boton("🔎 Comprobar", "ghost", self.comprobar,
                                          "Mira si la dirección lleva a la IP pública de este equipo")
        lay.addLayout(piezas.fila(QLabel("Dirección"), self.direccion, self.btn_ip, self.btn_comprobar))
        self.lbl_comprobacion = piezas.nota("")
        self.lbl_comprobacion.setVisible(False)
        lay.addWidget(self.lbl_comprobacion)
        self.direccion.textChanged.connect(lambda _t: self.lbl_comprobacion.setVisible(False))

        guardar = piezas.boton("💾 Guardar", "primary", self.guardar)
        cancelar = piezas.boton("Cancelar", "ghost", self.reject)
        lay.addLayout(piezas.fila(None, guardar, cancelar, estirar=False))

    def _direccion(self) -> str:
        return re.sub(r"^https?://", "", self.direccion.text().strip()).strip("/")

    def detectar_ip(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            ip = self._ip_publica()
        except Exception as e:
            QApplication.restoreOverrideCursor()
            _caja(self, f"No se pudo consultar la IP pública: {e}", QMessageBox.Warning).exec_()
            return
        QApplication.restoreOverrideCursor()
        self.direccion.setText(ip)

    def comprobar(self):
        if not self._direccion():
            self._resultado(False, "Escribe primero la dirección.")
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            bien, texto = self._comprobar(self._direccion())
        finally:
            QApplication.restoreOverrideCursor()
        self._resultado(bien, texto)

    def _resultado(self, bien: bool, texto: str):
        self.lbl_comprobacion.setText(texto)
        self.lbl_comprobacion.setStyleSheet("color: #16a34a; font-style: normal;" if bien
                                            else "color: #ca8a04; font-style: normal;")
        self.lbl_comprobacion.setVisible(True)

    def guardar(self):
        direccion = self._direccion()
        if direccion:
            try:
                direccion = envio._host_valido(direccion)
            except ValueError as e:
                _caja(self, str(e), QMessageBox.Warning).exec_()
                return
        error = self.r.cambiar_puerto(self.puerto.value(), confirmar=lambda t: _confirmar(self, t),
                                      auditoria=self.auditoria)
        if error:
            _caja(self, error, QMessageBox.Warning).exec_()
            return
        self.r.tickets.fijar_ajuste("direccion", direccion)
        self.accept()


class CodigoEnvioDialog(QDialog):
    """Crea el código de envío de un cliente y enseña los que siguen sin usar."""

    def __init__(self, padre, r: RecepcionAgente, clientes: List[str], cliente: str = ""):
        super().__init__(padre)
        self.r = r
        self.codigo, self.caducidad = "", ""
        self.setWindowTitle("📨 Código de envío")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(720)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(8)
        lay.addWidget(piezas.nota("El cliente lo pega en el agente y el informe te llega directamente. Vale una sola vez."))

        self.cliente = QComboBox()
        self.cliente.setEditable(True)
        self.cliente.addItems(sorted(set(clientes), key=str.lower))
        self.cliente.setCurrentText(cliente)
        self.cliente.setMinimumWidth(260)
        self.direccion = QLineEdit(r.tickets.ajuste("direccion"))
        self.direccion.setPlaceholderText("Nombre de DNS dinámico o IP pública")
        self.direccion.setMinimumWidth(260)
        self.btn_ip = piezas.boton("🌐 Mi IP pública", "ghost", self.detectar_ip, "Poner la IP con la que este equipo sale a Internet")
        self.caduca = piezas.combo([t for _d, t in envio.CADUCIDADES], [d for d, _t in envio.CADUCIDADES])
        self.caduca.setCurrentIndex(1)
        lay.addLayout(piezas.fila(QLabel("Cliente"), self.cliente, QLabel("Caduca en"), self.caduca))
        lay.addLayout(piezas.fila(QLabel("Dirección"), self.direccion, self.btn_ip,
                                  QLabel(f"Puerto {r.servidor.puerto}")))
        self.lbl_ip = piezas.nota("Con una IP, si tu proveedor la cambia, el código deja de servir; con un nombre de DNS "
                                  "dinámico, no.")
        lay.addWidget(self.lbl_ip)
        self.btn_crear = piezas.boton("Crear código", "primary", self.crear)
        lay.addLayout(piezas.fila(self.btn_crear))

        self.txt = piezas.CampoCodigo(lineas=3, solo_lectura=True, placeholder="Aquí aparecerá el código")
        lay.addWidget(self.txt)
        self.btn_copiar = piezas.boton("📋 Copiar código", "ghost", self.copiar)
        self.btn_instr = piezas.boton("📨 Copiar instrucciones", "ghost", self.copiar_instrucciones,
                                      "Texto para el cliente con el código")
        for b in (self.btn_copiar, self.btn_instr):
            b.setEnabled(False)
        self.lbl_aviso = QLabel("")
        self.lbl_aviso.setObjectName("hintLabel")
        lay.addLayout(piezas.fila(self.btn_copiar, self.btn_instr, self.lbl_aviso))

        lay.addWidget(QLabel("Códigos sin usar"))
        self.modelo = TablaSimple(["Cliente", "Creado", "Caduca"])
        self.tabla, _p = piezas.tabla(self.modelo, self, columna_ancha=0)
        self.tabla.setMinimumHeight(120)
        lay.addWidget(self.tabla, 1)
        self.btn_anular = piezas.boton("✕ Anular", "ghost", self.anular, "El código elegido deja de valer")
        cerrar = piezas.boton("Cerrar", "ghost", self.accept)
        lay.addLayout(piezas.fila(self.btn_anular, None, cerrar, estirar=False))
        self._pintar()

    def _pintar(self):
        filas = self.r.tickets.pendientes()
        self.modelo.set_datos([[f["cliente"], envio.tiempo_legible(f["creado"]), envio.tiempo_legible(f["caduca"])]
                               for f in filas], filas)
        self.btn_anular.setEnabled(bool(filas))

    def detectar_ip(self):
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            ip = envio.ip_publica()
        except Exception as e:
            QApplication.restoreOverrideCursor()
            _caja(self, f"No se pudo consultar la IP pública: {e}", QMessageBox.Warning).exec_()
            return
        QApplication.restoreOverrideCursor()
        self.direccion.setText(ip)

    def crear(self):
        cliente = self.cliente.currentText().strip()
        direccion = re.sub(r"^https?://", "", self.direccion.text().strip()).strip("/")
        if not cliente:
            _caja(self, "Elige o escribe el cliente.").exec_()
            return
        try:
            publica = self.r.db.publica_activa()
            envio.codigo_envio(publica, direccion, self.r.servidor.puerto, b"\0" * 16)   # valida antes de crear el ticket
            ticket, caduca = self.r.tickets.nuevo(cliente, self.caduca.currentData() or 3)
            self.codigo = envio.codigo_envio(publica, direccion, self.r.servidor.puerto, ticket)
            self.caducidad = envio.tiempo_legible(caduca)
        except ValueError as e:
            _caja(self, str(e), QMessageBox.Warning).exec_()
            return
        self.r.tickets.fijar_ajuste("direccion", direccion)
        self.txt.setText(self.codigo)
        for b in (self.btn_copiar, self.btn_instr):
            b.setEnabled(True)
        self.lbl_aviso.setText("" if self.r.activa else "Activa «📡 Recibir por Internet» para que te llegue")
        self._pintar()

    def copiar(self):
        QGuiApplication.clipboard().setText(self.codigo)
        self.lbl_aviso.setText("Código copiado")

    def copiar_instrucciones(self):
        QGuiApplication.clipboard().setText(envio.texto_para_cliente(self.codigo, self.caducidad))
        self.lbl_aviso.setText("Instrucciones copiadas")

    def anular(self):
        sel = self.tabla.selectionModel().selectedRows()
        if not sel:
            _caja(self, "Elige el código que quieres anular.").exec_()
            return
        fila = self.modelo.objeto(self.tabla.model().mapToSource(sel[0]).row())
        self.r.tickets.anular(fila["huella"])
        self._pintar()
