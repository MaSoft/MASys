# -*- coding: utf-8 -*-
"""Ventanas del envío de correos a los clientes (5.1.0). Motor en msys/domain/envio_correo.py.

- CorreoDialog «✉️ Correo»: pestañas Cuentas (SMTP / Gmail / Microsoft 365, probar, predeterminada), Plantillas
  (con variables) y Enviados (historial).
- RedactarDialog: cuenta, plantilla, destinatario (del catálogo de Empresas o escrito), enlace y código; envía en
  segundo plano y lo deja en el historial."""

from typing import Callable, Dict, List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication, QComboBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
)

from msys.core import tareas
from msys.domain import clientes as cl
from msys.domain import envio_correo as ec
from msys.models.tabla_simple import COLOR_ERROR, COLOR_OK, TablaSimple
from msys.ui import piezas

_TITULO = "✉️ Correo"
AYUDA = {
    ec.SMTP: "Cualquier servidor SMTP: el de tu dominio, un Exchange local u otro proveedor. «Detectar» busca el "
             "servidor a partir del correo del remitente. «Sin cifrar» solo para un relé interno.",
    ec.GMAIL: "Gmail necesita una CONTRASEÑA DE APLICACIÓN (Cuenta de Google › Seguridad › Verificación en dos pasos "
              "› Contraseñas de aplicaciones), no la contraseña de la cuenta. Se envía por smtp.gmail.com:587.",
    ec.M365: "Por Microsoft Graph con OAuth, sin contraseña de buzón: aplicación registrada en Entra ID con el permiso "
             "de APLICACIÓN Mail.Send (consentimiento de administrador) y un secreto de cliente. Conviene limitar a "
             "qué buzones puede enviar.",
}


def _caja(padre, texto: str, icono=QMessageBox.Information) -> QMessageBox:
    caja = QMessageBox(padre)
    caja.setWindowFlags(caja.windowFlags() & ~Qt.WindowContextHelpButtonHint)
    caja.setWindowTitle(_TITULO)
    caja.setText(texto)
    caja.setIcon(icono)
    return caja


def _tamano(dlg: QDialog, padre, ancho: int, alto: int):
    pantalla = padre.screen() if padre is not None and hasattr(padre, "screen") else QApplication.primaryScreen()
    zona = pantalla.availableGeometry() if pantalla else None
    dlg.resize(min(ancho, zona.width() - 40) if zona else ancho, min(alto, zona.height() - 60) if zona else alto)


class _Base(QDialog):
    def _aviso(self, texto: str, icono=QMessageBox.Information):
        _caja(self, texto, icono).exec_()

    def _confirmar(self, texto: str) -> bool:
        caja = _caja(self, texto, QMessageBox.Question)
        si = caja.addButton("Sí", QMessageBox.YesRole)
        caja.addButton("No", QMessageBox.NoRole)
        caja.exec_()
        return caja.clickedButton() is si


# ═════════════════════════════════════════════════════════════════════════ Configuración
class CorreoDialog(_Base):
    def __init__(self, padre, cdb: Optional[ec.CorreoDB] = None):
        super().__init__(padre)
        self.cdb = cdb if cdb is not None else ec.CorreoDB()
        self._enviar = ec.enviar                      # las pruebas lo sustituyen: sin red
        self._detectar = ec.detectar_smtp
        self._tarea = None
        self._cuenta: Optional[ec.Perfil] = None
        self._plantilla: Optional[ec.Plantilla] = None
        self.setWindowTitle("✉️ Correo para los clientes")
        self.setWindowFlags((self.windowFlags() | Qt.WindowMinMaxButtonsHint) & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(900, 520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)
        self.pestanas = piezas.pestanas()
        self.pestanas.addTab(self._crear_cuentas(), "📮 Cuentas")
        self.pestanas.addTab(self._crear_plantillas(), "📝 Plantillas")
        self.pestanas.addTab(self._crear_enviados(), "📋 Enviados")
        lay.addWidget(self.pestanas, 1)
        cerrar = piezas.boton("Cerrar", "ghost", self.accept)
        lay.addLayout(piezas.fila(None, cerrar, estirar=False))
        _tamano(self, padre, 1040, 640)
        self.recargar_cuentas()
        self.recargar_plantillas()
        self.recargar_enviados()

    # ── Cuentas ─────────────────────────────────────────────────────────
    def _crear_cuentas(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setSpacing(12)
        izq = QVBoxLayout()
        self.lista_cuentas = QListWidget()
        self.lista_cuentas.setMinimumWidth(240)
        self.lista_cuentas.currentRowChanged.connect(lambda _r: self._mostrar_cuenta())
        izq.addWidget(self.lista_cuentas, 1)
        self.btn_nueva_cuenta = piezas.boton("＋ Cuenta", "primary", self.nueva_cuenta)
        self.btn_predeterminada = piezas.boton("★ Predeterminada", "ghost", self.hacer_predeterminada,
                                               "La que se propone al enviar")
        self.btn_quitar_cuenta = piezas.boton("✕ Quitar", "ghost", self.quitar_cuenta)
        izq.addLayout(piezas.fila(self.btn_nueva_cuenta, self.btn_predeterminada, estirar=False))
        izq.addLayout(piezas.fila(self.btn_quitar_cuenta))
        lay.addLayout(izq)

        # El formulario va en una zona con desplazamiento: en ventanas bajas no se aplasta ni se corta la ayuda
        contenedor = QWidget()
        der = QVBoxLayout(contenedor)
        der.setContentsMargins(0, 0, 6, 0)
        der.setSpacing(6)

        def campo(titulo, widget, fila=None):
            etiqueta = QLabel(titulo)
            der.addWidget(etiqueta)
            der.addWidget(widget) if fila is None else der.addLayout(fila)
            return etiqueta

        self.ed_nombre = QLineEdit()
        self.ed_nombre.setPlaceholderText("Por ejemplo: Soporte Masoft")
        campo("Nombre de la cuenta", self.ed_nombre)
        self.ed_tipo = piezas.combo([n for _k, n in ec.TIPOS], [k for k, _n in ec.TIPOS])
        self.ed_tipo.currentIndexChanged.connect(lambda _i: self._tipo_cambiado())
        campo("Tipo", self.ed_tipo)
        self.lbl_ayuda = piezas.nota("")
        der.addWidget(self.lbl_ayuda)
        self.ed_remitente = QLineEdit()
        self.ed_remitente.setPlaceholderText("soporte@tudominio.es")
        self.ed_nombre_rem = QLineEdit()
        self.ed_nombre_rem.setPlaceholderText("Tu nombre o el de la empresa ({tecnico} en las plantillas)")
        der.addLayout(piezas.fila(self._columna("Remitente", self.ed_remitente),
                                  self._columna("Nombre del remitente", self.ed_nombre_rem), estirar=False))
        # SMTP
        self.caja_smtp = QWidget()
        ls = QVBoxLayout(self.caja_smtp)
        ls.setContentsMargins(0, 0, 0, 0)
        ls.setSpacing(6)
        self.ed_servidor = QLineEdit()
        self.ed_servidor.setPlaceholderText("smtp.tudominio.es")
        self.ed_puerto = QSpinBox()
        self.ed_puerto.setRange(1, 65535)
        self.ed_puerto.setValue(587)
        self.ed_puerto.setMinimumWidth(90)
        self.ed_seguridad = piezas.combo([n for _k, n in ec.SEGURIDADES], [k for k, _n in ec.SEGURIDADES])
        self.btn_detectar = piezas.boton("🔎 Detectar", "ghost", self.detectar,
                                         "Busca el servidor a partir del correo del remitente")
        ls.addLayout(piezas.fila(self._columna("Servidor SMTP", self.ed_servidor), self._columna("Puerto", self.ed_puerto),
                                 self._columna("Seguridad", self.ed_seguridad), estirar=False))
        self.ed_usuario = QLineEdit()
        self.ed_usuario.setPlaceholderText("Vacío = el remitente")
        ls.addLayout(piezas.fila(self._columna("Usuario", self.ed_usuario), self.btn_detectar, estirar=False))
        der.addWidget(self.caja_smtp)
        # Microsoft 365
        self.caja_m365 = QWidget()
        lm = QVBoxLayout(self.caja_m365)
        lm.setContentsMargins(0, 0, 0, 0)
        self.ed_tenant = QLineEdit()
        self.ed_tenant.setPlaceholderText("GUID o empresa.onmicrosoft.com")
        self.ed_cliente_id = QLineEdit()
        self.ed_cliente_id.setPlaceholderText("Id de la aplicación (GUID)")
        lm.addLayout(piezas.fila(self._columna("Inquilino", self.ed_tenant),
                                 self._columna("Aplicación", self.ed_cliente_id), estirar=False))
        der.addWidget(self.caja_m365)
        self.lbl_secreto = QLabel("Contraseña")
        der.addWidget(self.lbl_secreto)
        self.ed_secreto = QLineEdit()
        self.ed_secreto.setEchoMode(QLineEdit.Password)
        der.addWidget(self.ed_secreto)
        der.addStretch(1)
        self.lbl_resultado = piezas.nota("")      # fuera de la zona con desplazamiento: siempre a la vista
        self.lbl_resultado.setVisible(False)
        self.btn_probar = piezas.boton("✉️ Probar", "ghost", self.probar, "Envía un correo de prueba al propio remitente")
        self.btn_guardar_cuenta = piezas.boton("💾 Guardar", "primary", self.guardar_cuenta)
        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setFrameShape(QScrollArea.NoFrame)
        area.setWidget(contenedor)
        columna = QVBoxLayout()           # los botones, siempre a la vista (fuera de la zona con desplazamiento)
        columna.addWidget(area, 1)
        columna.addWidget(self.lbl_resultado)
        columna.addLayout(piezas.fila(None, self.btn_probar, self.btn_guardar_cuenta, estirar=False))
        lay.addLayout(columna, 1)
        return w

    @staticmethod
    def _columna(titulo: str, widget) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(QLabel(titulo))
        col.addWidget(widget)
        return col

    def recargar_cuentas(self, seleccionar: Optional[int] = None):
        try:
            self._cuentas = self.cdb.cuentas()
        except Exception as e:
            self._cuentas = []
            self._aviso(f"No se pudieron leer las cuentas: {e}", QMessageBox.Warning)
        self.lista_cuentas.blockSignals(True)
        self.lista_cuentas.clear()
        fila = 0
        for i, p in enumerate(self._cuentas):
            item = QListWidgetItem(("★ " if p.predeterminado else "") + f"{p.nombre} · {ec.NOMBRE_TIPO[p.tipo].split(' (')[0]}")
            item.setToolTip(p.remitente)
            self.lista_cuentas.addItem(item)
            if p.id == seleccionar:
                fila = i
        self.lista_cuentas.blockSignals(False)
        if self._cuentas:
            self.lista_cuentas.setCurrentRow(fila)
            self._mostrar_cuenta()
        else:
            self.nueva_cuenta()

    def _mostrar_cuenta(self):
        fila = self.lista_cuentas.currentRow()
        if not 0 <= fila < len(self._cuentas):
            return
        self._pintar_cuenta(self._cuentas[fila])

    def _pintar_cuenta(self, p: ec.Perfil):
        self._cuenta = p
        self.ed_nombre.setText(p.nombre)
        self.ed_tipo.setCurrentIndex(max(self.ed_tipo.findData(p.tipo), 0))
        self.ed_remitente.setText(p.remitente)
        self.ed_nombre_rem.setText(p.nombre_remitente)
        self.ed_servidor.setText(p.servidor)
        self.ed_puerto.setValue(int(p.puerto or 587))
        self.ed_seguridad.setCurrentIndex(max(self.ed_seguridad.findData(p.seguridad), 0))
        self.ed_usuario.setText(p.usuario)
        self.ed_tenant.setText(p.tenant)
        self.ed_cliente_id.setText(p.cliente_id)
        self.ed_secreto.clear()
        self.lbl_resultado.setVisible(False)
        self._tipo_cambiado()

    def _tipo_cambiado(self):
        tipo = self.ed_tipo.currentData() or ec.SMTP
        self.caja_smtp.setVisible(tipo == ec.SMTP)
        self.caja_m365.setVisible(tipo == ec.M365)
        self.lbl_ayuda.setText(AYUDA[tipo])
        self.lbl_secreto.setText({ec.SMTP: "Contraseña", ec.GMAIL: "Contraseña de aplicación",
                                  ec.M365: "Secreto de la aplicación"}[tipo])
        guardado = bool(self._cuenta and self._cuenta.con_secreto)
        self.ed_secreto.setPlaceholderText("Guardado (cifrado): vacío = sin cambios" if guardado else
                                           ("Opcional (relé sin usuario)" if tipo == ec.SMTP else "Obligatorio"))

    def _leer_cuenta(self) -> ec.Perfil:
        base = self._cuenta or ec.Perfil()
        return ec.Perfil(base.id, self.ed_nombre.text().strip(), self.ed_tipo.currentData() or ec.SMTP,
                         self.ed_remitente.text().strip(), self.ed_nombre_rem.text().strip(), self.ed_servidor.text().strip(),
                         self.ed_puerto.value(), self.ed_seguridad.currentData() or ec.STARTTLS, self.ed_usuario.text().strip(),
                         self.ed_tenant.text().strip(), self.ed_cliente_id.text().strip(), base.predeterminado,
                         base.con_secreto)

    def nueva_cuenta(self):
        self.lista_cuentas.clearSelection()
        self._pintar_cuenta(ec.Perfil())
        self.ed_nombre.setFocus()

    def guardar_cuenta(self) -> Optional[ec.Perfil]:
        try:
            p = self.cdb.guardar_cuenta(self._leer_cuenta(), self.ed_secreto.text() or None)
        except ValueError as e:
            self._aviso(str(e), QMessageBox.Warning)
            return None
        self.recargar_cuentas(p.id)
        return p

    def hacer_predeterminada(self):
        if self._cuenta is None or not self._cuenta.id:
            self._aviso("Elige una cuenta guardada.")
            return
        self.cdb.fijar_predeterminada(self._cuenta.id)
        self.recargar_cuentas(self._cuenta.id)

    def quitar_cuenta(self):
        if self._cuenta is None or not self._cuenta.id:
            self._aviso("Elige una cuenta guardada.")
            return
        if not self._confirmar(f"¿Quitar la cuenta «{self._cuenta.nombre}»? Su contraseña o secreto se borran."):
            return
        self.cdb.eliminar_cuenta(self._cuenta.id)
        self._cuenta = None
        self.recargar_cuentas()

    def detectar(self):
        correo = self.ed_remitente.text().strip()
        try:
            if not cl.validar_correo(correo):
                raise ValueError("Escribe primero el correo del remitente")
        except ValueError as e:
            self._aviso(str(e))
            return
        self._resultado("Buscando el servidor…", None)

        def listo(p):
            if p is None:
                self._resultado("No se encontró: escribe el servidor a mano (te lo da tu proveedor).", False)
                return
            self.ed_servidor.setText(p.servidor)
            self.ed_puerto.setValue(p.puerto)
            self.ed_seguridad.setCurrentIndex(max(self.ed_seguridad.findData(p.seguridad), 0))
            self._resultado(f"Encontrado: {p.servidor}:{p.puerto}", True)

        tareas.lanzar(self, "_tarea", self._detectar, correo, al_terminar=listo,
                      al_fallar=lambda msg: self._resultado(f"No se pudo buscar: {msg}", False))

    def probar(self):
        p = self.guardar_cuenta()
        if p is None:
            return
        secreto = self.cdb.secreto(p.id)
        mensaje = ec.Mensaje([p.remitente], "Prueba de MASys",
                             f"Correo de prueba de la cuenta «{p.nombre}» de MASys.\nSi lo recibes, la cuenta funciona.")
        self.btn_probar.setEnabled(False)
        self._resultado("Enviando la prueba…", None)

        def enviar():
            self._enviar(p, secreto, mensaje)

        def listo(_r):
            self.btn_probar.setEnabled(True)
            self.cdb.registrar(p.nombre, [p.remitente], mensaje.asunto, "Enviado")
            self._resultado(f"✔ Enviado a {p.remitente}: mira la bandeja de entrada.", True)
            self.recargar_enviados()

        def fallo(msg):
            self.btn_probar.setEnabled(True)
            self.cdb.registrar(p.nombre, [p.remitente], mensaje.asunto, f"Error: {msg}")
            self._resultado(f"✖ {msg}", False)
            self.recargar_enviados()

        tareas.lanzar(self, "_tarea", enviar, al_terminar=listo, al_fallar=fallo)

    def _resultado(self, texto: str, bien: Optional[bool]):
        self.lbl_resultado.setText(texto)
        color = "" if bien is None else ("color: #16a34a;" if bien else "color: #ca8a04;")
        self.lbl_resultado.setStyleSheet(color + " font-style: normal;")
        self.lbl_resultado.setVisible(True)

    # ── Plantillas ──────────────────────────────────────────────────────
    def _crear_plantillas(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setSpacing(12)
        izq = QVBoxLayout()
        self.lista_plantillas = QListWidget()
        self.lista_plantillas.setMinimumWidth(240)
        self.lista_plantillas.currentRowChanged.connect(lambda _r: self._mostrar_plantilla())
        izq.addWidget(self.lista_plantillas, 1)
        self.btn_nueva_plantilla = piezas.boton("＋ Plantilla", "primary", self.nueva_plantilla)
        self.btn_quitar_plantilla = piezas.boton("✕ Quitar", "ghost", self.quitar_plantilla)
        izq.addLayout(piezas.fila(self.btn_nueva_plantilla, self.btn_quitar_plantilla, estirar=False))
        lay.addLayout(izq)
        der = QVBoxLayout()
        der.setSpacing(6)
        self.pl_nombre = QLineEdit()
        self.pl_asunto = QLineEdit()
        self.pl_cuerpo = QPlainTextEdit()
        self.pl_enlace = QLineEdit()
        self.pl_enlace.setPlaceholderText("Enlace de descarga que se propone ({enlace})")
        for titulo, widget in (("Nombre", self.pl_nombre), ("Asunto", self.pl_asunto), ("Texto", self.pl_cuerpo),
                               ("Enlace", self.pl_enlace)):
            der.addWidget(QLabel(titulo))
            der.addWidget(widget, 1 if widget is self.pl_cuerpo else 0)
        der.addWidget(piezas.nota("Variables: " + " · ".join(f"{{{k}}} {d.lower()}" for k, d in ec.VARIABLES)
                                  + ". Los programas no se adjuntan (Gmail y Exchange los bloquean): pon el enlace de "
                                  "descarga, por ejemplo uno de OneDrive."))
        self.btn_guardar_plantilla = piezas.boton("💾 Guardar", "primary", self.guardar_plantilla)
        der.addLayout(piezas.fila(None, self.btn_guardar_plantilla, estirar=False))
        lay.addLayout(der, 1)
        return w

    def recargar_plantillas(self, seleccionar: Optional[int] = None):
        try:
            self._plantillas = self.cdb.plantillas()
        except Exception as e:
            self._plantillas = []
            self._aviso(f"No se pudieron leer las plantillas: {e}", QMessageBox.Warning)
        self.lista_plantillas.blockSignals(True)
        self.lista_plantillas.clear()
        fila = 0
        for i, t in enumerate(self._plantillas):
            self.lista_plantillas.addItem(t.nombre)
            if t.id == seleccionar:
                fila = i
        self.lista_plantillas.blockSignals(False)
        if self._plantillas:
            self.lista_plantillas.setCurrentRow(fila)
            self._mostrar_plantilla()
        else:
            self.nueva_plantilla()

    def _mostrar_plantilla(self):
        fila = self.lista_plantillas.currentRow()
        if 0 <= fila < len(self._plantillas):
            self._pintar_plantilla(self._plantillas[fila])

    def _pintar_plantilla(self, t: ec.Plantilla):
        self._plantilla = t
        self.pl_nombre.setText(t.nombre)
        self.pl_asunto.setText(t.asunto)
        self.pl_cuerpo.setPlainText(t.cuerpo)
        self.pl_enlace.setText(t.enlace)

    def nueva_plantilla(self):
        self.lista_plantillas.clearSelection()
        self._pintar_plantilla(ec.Plantilla())
        self.pl_nombre.setFocus()

    def guardar_plantilla(self):
        t = ec.Plantilla(self._plantilla.id if self._plantilla else 0, self.pl_nombre.text(), self.pl_asunto.text(),
                         self.pl_cuerpo.toPlainText(), self.pl_enlace.text().strip())
        try:
            t = self.cdb.guardar_plantilla(t)
        except ValueError as e:
            self._aviso(str(e), QMessageBox.Warning)
            return
        self.recargar_plantillas(t.id)

    def quitar_plantilla(self):
        if self._plantilla is None or not self._plantilla.id:
            self._aviso("Elige una plantilla guardada.")
            return
        if not self._confirmar(f"¿Quitar la plantilla «{self._plantilla.nombre}»?"):
            return
        self.cdb.eliminar_plantilla(self._plantilla.id)
        self._plantilla = None
        self.recargar_plantillas()

    # ── Enviados ────────────────────────────────────────────────────────
    def _crear_enviados(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.busqueda_enviados = QLineEdit()
        self.busqueda_enviados.setPlaceholderText("Buscar")
        self.busqueda_enviados.setClearButtonEnabled(True)
        self.lbl_enviados = QLabel("")
        self.lbl_enviados.setObjectName("countLabel")
        lay.addLayout(piezas.fila(self.busqueda_enviados, self.lbl_enviados, estirar=False))
        self.modelo_enviados = TablaSimple(["Fecha", "Cuenta", "Para", "Asunto", "Empresa", "Resultado"],
                                           color=lambda x, c: (COLOR_OK if x.resultado == "Enviado" else COLOR_ERROR)
                                           if c == 5 else None,
                                           tooltip=lambda x, c: {2: x.para, 3: x.asunto, 5: x.resultado}.get(c))
        self.tabla_enviados, self.proxy_enviados = piezas.tabla(self.modelo_enviados, self, columna_ancha=3,
                                                                busqueda=self.busqueda_enviados)
        lay.addWidget(self.tabla_enviados, 1)
        self.lbl_detalle = piezas.nota("Elige un correo para ver a quién se envió y, si falló, el motivo completo.")
        lay.addWidget(self.lbl_detalle)
        self.tabla_enviados.selectionModel().currentRowChanged.connect(lambda act, _a: self._detalle_enviado(act))
        self.busqueda_enviados.textChanged.connect(lambda _t: self._recuento_enviados())
        return w

    def _detalle_enviado(self, indice):
        if not indice.isValid():
            return
        x = self.modelo_enviados.objeto(self.proxy_enviados.mapToSource(indice).row())
        if x is None:
            return
        quien = " · ".join(v for v in (x.usuario, x.empresa) if v)
        self.lbl_detalle.setText(f"{x.para}" + (f" ({quien})" if quien else "") + f" — {x.resultado}")

    def recargar_enviados(self):
        try:
            filas = self.cdb.enviados()
        except Exception:
            filas = []
        datos = []
        for x in filas:
            fecha, hora = (x.fecha[:10], x.fecha[11:16]) if len(x.fecha) >= 16 else (x.fecha, "")
            if len(fecha) == 10 and fecha[4] == "-":
                fecha = f"{fecha[8:10]}/{fecha[5:7]}/{fecha[:4]}"
            datos.append([f"{fecha} {hora}".strip(), x.cuenta, x.para, x.asunto, x.empresa,
                          "Enviado" if x.resultado == "Enviado" else "Error"])
        self.modelo_enviados.set_datos(datos, filas)
        self._recuento_enviados()

    def _recuento_enviados(self):
        n, t = self.proxy_enviados.rowCount(), self.modelo_enviados.rowCount()
        self.lbl_enviados.setText(f"{n} de {t}" if n != t else ("1 correo" if t == 1 else f"{t} correos"))


# ═════════════════════════════════════════════════════════════════════════ Redactar
class RedactarDialog(_Base):
    """Correo a un cliente. datos: variables ya conocidas (empresa, usuario, correo, equipo…). codigo: función que da
    el código del técnico para la plantilla del agente (se pide solo si la plantilla lo usa)."""

    def __init__(self, padre, cdb: ec.CorreoDB, catalogo: Optional[cl.ClientesDB] = None,
                 datos: Optional[Dict[str, str]] = None, plantilla: str = "", codigo: Optional[Callable[[], str]] = None,
                 abrir_configuracion: Optional[Callable[[], None]] = None):
        super().__init__(padre)
        self.cdb, self.catalogo = cdb, catalogo
        self.datos = dict(datos or {})
        self._codigo_fn = codigo
        self._abrir_configuracion = abrir_configuracion
        self._enviar = ec.enviar                     # las pruebas lo sustituyen: sin red
        self._tarea = None
        self._editado = False
        self.enviado = False
        self.setWindowTitle("✉️ Enviar correo")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(720, 560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(6)

        self.cuenta = QComboBox()
        self.plantilla = QComboBox()
        lay.addLayout(piezas.fila(self._col("Cuenta", self.cuenta), self._col("Plantilla", self.plantilla), estirar=False))
        self.para = QComboBox()
        self.para.setEditable(True)
        self.para.setInsertPolicy(QComboBox.NoInsert)
        self.para.lineEdit().setPlaceholderText("correo@cliente.es (varios separados por «;»)")
        self.para.activated.connect(lambda i: self._elegido(i))
        lay.addWidget(QLabel("Para"))
        lay.addWidget(self.para)
        self.enlace = QLineEdit()
        self.enlace.setPlaceholderText("Enlace de descarga (OneDrive, tu web…)")
        self.codigo = QLineEdit()
        self.codigo.setPlaceholderText("Código para el agente")
        self.fila_codigo = QWidget()
        lc = QVBoxLayout(self.fila_codigo)
        lc.setContentsMargins(0, 0, 0, 0)
        lc.addWidget(QLabel("Código"))
        lc.addWidget(self.codigo)
        lay.addWidget(QLabel("Enlace"))
        lay.addWidget(self.enlace)
        lay.addWidget(self.fila_codigo)
        self.asunto = QLineEdit()
        self.cuerpo = QPlainTextEdit()
        lay.addWidget(QLabel("Asunto"))
        lay.addWidget(self.asunto)
        lay.addWidget(QLabel("Texto"))
        lay.addWidget(self.cuerpo, 1)
        self.lbl_estado = piezas.nota("")
        self.lbl_estado.setVisible(False)
        lay.addWidget(self.lbl_estado)
        self.btn_cuentas = piezas.boton("⚙️ Cuentas y plantillas", "ghost", self.configurar)
        self.btn_cancelar = piezas.boton("Cancelar", "ghost", self.reject)
        self.btn_enviar = piezas.boton("✉️ Enviar", "primary", self.enviar)
        lay.addLayout(piezas.fila(self.btn_cuentas, None, self.btn_cancelar, self.btn_enviar, estirar=False))
        _tamano(self, padre, 820, 640)

        self._cargar(plantilla)
        self.plantilla.currentIndexChanged.connect(lambda _i: self._aplicar_plantilla(nueva=True))
        self.cuenta.currentIndexChanged.connect(lambda _i: self._aplicar_plantilla())
        self.enlace.textEdited.connect(lambda _t: self._aplicar_plantilla())
        self.codigo.textEdited.connect(lambda _t: self._aplicar_plantilla())
        self.cuerpo.textChanged.connect(self._texto_cambiado)
        self.asunto.textEdited.connect(lambda _t: setattr(self, "_editado", True))

    @staticmethod
    def _col(titulo, widget) -> QVBoxLayout:
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(QLabel(titulo))
        col.addWidget(widget)
        return col

    def _cargar(self, plantilla: str):
        self._cuentas = self.cdb.cuentas()
        self._plantillas = self.cdb.plantillas()
        self.cuenta.clear()
        for p in self._cuentas:
            self.cuenta.addItem(f"{p.nombre} <{p.remitente}>", p.id)
        pred = self.cdb.predeterminada()
        if pred is not None:
            self.cuenta.setCurrentIndex(max(self.cuenta.findData(pred.id), 0))
        self.plantilla.clear()
        for t in self._plantillas:
            self.plantilla.addItem(t.nombre, t.id)
        if plantilla:
            self.plantilla.setCurrentIndex(max(self.plantilla.findText(plantilla), 0))
        self.para.clear()
        self._destinos: List[cl.Usuario] = []
        try:
            for u in (self.catalogo.usuarios() if self.catalogo is not None else []):
                if u.correo:
                    self._destinos.append(u)
                    self.para.addItem(f"{u.correo} — {u.nombre} ({u.empresa})", u.correo)
        except Exception:
            pass
        self.para.setCurrentText(self.datos.get("correo", ""))
        self._aplicar_plantilla(nueva=True)
        if not self._cuentas:
            self._estado("Aún no hay ninguna cuenta para enviar: configúrala en «⚙️ Cuentas y plantillas».", False)

    def _elegido(self, indice: int):
        correo = self.para.itemData(indice)
        if not correo:
            return
        u = next((x for x in self._destinos if x.correo == correo), None)
        self.para.setEditText(correo)
        if u is not None:
            self.datos.update(empresa=u.empresa, usuario=u.nombre, correo=u.correo, telefono=u.telefono)
            self._aplicar_plantilla()

    def _plantilla_actual(self) -> Optional[ec.Plantilla]:
        return next((t for t in self._plantillas if t.id == self.plantilla.currentData()), None)

    def _cuenta_actual(self) -> Optional[ec.Perfil]:
        return next((p for p in self._cuentas if p.id == self.cuenta.currentData()), None)

    def _aplicar_plantilla(self, nueva: bool = False):
        t = self._plantilla_actual()
        if t is None:
            return
        if nueva:
            self.enlace.setText(t.enlace or self.enlace.text())
            usa_codigo = "{codigo}" in (t.asunto + t.cuerpo)
            self.fila_codigo.setVisible(usa_codigo)
            if usa_codigo and not self.codigo.text() and self._codigo_fn is not None:
                try:
                    self.codigo.setText(self._codigo_fn() or "")
                except Exception:
                    pass
            if self._editado and not self._confirmar("¿Cambiar de plantilla? Se pierde lo que has escrito."):
                return
            self._editado = False
        if self._editado:
            return     # el texto escrito a mano no se pisa
        datos = ec.datos_basicos(self._cuenta_actual(), **self.datos)
        # Vacíos, {enlace} y {codigo} se quedan a la vista: así no se envía nunca un correo sin enlace o sin código
        for clave, campo in (("enlace", self.enlace), ("codigo", self.codigo)):
            if campo.text().strip():
                datos[clave] = campo.text().strip()
            else:
                datos.pop(clave, None)
        self.cuerpo.blockSignals(True)
        self.asunto.setText(ec.rellenar(t.asunto, datos))
        self.cuerpo.setPlainText(ec.rellenar(t.cuerpo, datos))
        self.cuerpo.blockSignals(False)

    def _texto_cambiado(self):
        self._editado = True

    def _estado(self, texto: str, bien: Optional[bool]):
        self.lbl_estado.setText(texto)
        color = "" if bien is None else ("color: #16a34a;" if bien else "color: #ca8a04;")
        self.lbl_estado.setStyleSheet(color + " font-style: normal;")
        self.lbl_estado.setVisible(True)

    def configurar(self):
        if self._abrir_configuracion is not None:
            self._abrir_configuracion()
        else:
            CorreoDialog(self, self.cdb).exec_()
        para, asunto, cuerpo, editado = self.para.currentText(), self.asunto.text(), self.cuerpo.toPlainText(), self._editado
        self._cargar(self.plantilla.currentText())
        self.para.setCurrentText(para)
        if editado:
            self.asunto.setText(asunto)
            self.cuerpo.setPlainText(cuerpo)
            self._editado = True

    def enviar(self):
        p = self._cuenta_actual()
        if p is None:
            self._aviso("Configura primero una cuenta en «⚙️ Cuentas y plantillas».")
            return
        try:
            para = ec.destinatarios(self.para.currentText())
        except ValueError as e:
            self._aviso(str(e), QMessageBox.Warning)
            return
        texto = self.asunto.text() + self.cuerpo.toPlainText()
        if "{enlace}" in texto or "{codigo}" in texto:
            self._aviso("Falta el enlace o el código: el texto todavía tiene {enlace} o {codigo}.", QMessageBox.Warning)
            return
        if not self.asunto.text().strip() and not self._confirmar("El correo no tiene asunto. ¿Enviarlo igual?"):
            return
        mensaje = ec.Mensaje(para, self.asunto.text().strip(), self.cuerpo.toPlainText())
        t = self._plantilla_actual()
        empresa, usuario = self.datos.get("empresa", ""), self.datos.get("usuario", "")
        self.btn_enviar.setEnabled(False)
        self._estado("Enviando…", None)

        def trabajo():
            ec.aplicar(self.cdb, p, mensaje, empresa, usuario, enviar_fn=self._enviar)

        def listo(_r):
            if t is not None and self.enlace.text().strip():
                self.cdb.recordar_enlace(t.id, self.enlace.text())
            self.enviado = True
            self._estado(f"✔ Enviado a {', '.join(para)}", True)
            self.accept()

        def fallo(msg):
            self.btn_enviar.setEnabled(True)
            self._estado(f"✖ {msg}", False)

        tareas.lanzar(self, "_tarea", trabajo, al_terminar=listo, al_fallar=fallo)
