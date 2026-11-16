# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: pestañas nuevas de 📧 Cuentas Correo (4.9.0), la operativa de MACorreo adaptada a MASys
(motor en msys/domain/correo, copia propia). La pestaña de siempre («📧 Cuentas») no cambia.

- 🔑 Probar cuenta: reconoce el proveedor de un correo (dominio, MX, registros SRV, base de Thunderbird), prueba
  IMAP/POP3/SMTP con la contraseña SOLO en sus servidores, enseña la configuración y la añade a Outlook (PRF).
  Abajo, prueba de un servidor suelto. «Probar toda la lista…» manda la contraseña a todos: pide confirmarlo.
- ✉️ Comprobar direcciones: una (paso a paso, con historial) o una lista pegada o de un CSV/TXT, con pausas.
- 🌐 Dominio de correo: lo que mira MXToolbox, con semáforo, «qué hacer» e informe HTML.
- 🗂️ Configuración de Outlook y 📬 Visor PST/OST: msys/ui/correo_outlook.py.
La contraseña solo vive en el cuadro de texto: no se guarda en ningún sitio."""

import html
import os
import time
from datetime import datetime

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QApplication, QCheckBox, QDialog, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                             QMenu, QMessageBox, QPlainTextEdit, QSpinBox, QSplitter, QStackedWidget, QTextBrowser,
                             QVBoxLayout, QWidget)

from msys.core import tareas
from msys.core.utils import dialogo_abrir_archivo, dialogo_guardar_archivo, get_informes_folder
from msys.domain.correo import direcciones as dirs
from msys.domain.correo import dominio as dom
from msys.domain.correo import informe as inf
from msys.domain.correo import outlook_cfg as olk
from msys.domain.correo import proveedores as prov
from msys.domain.correo import servidores as srv
from msys.domain.correo.ajustes import CorreoDB
from msys.models.tabla_simple import (COLOR_APAGADO, COLOR_AVISO, COLOR_ERROR, COLOR_INFO, COLOR_OK, TablaSimple,
                                      objeto_seleccionado, objetos_seleccionados)
from msys.ui import piezas

SECCION = "📧 Cuentas Correo"
T_CUENTAS = "📧 Cuentas"
T_PROBAR = "🔑 Probar cuenta"
T_DIRECCIONES = "✉️ Comprobar direcciones"
T_DOMINIO = "🌐 Dominio de correo"
T_OUTLOOK = "🗂️ Configuración de Outlook"
T_VISOR = "📬 Visor PST/OST"

COLOR = {"ok": COLOR_OK, "warn": COLOR_AVISO, "bad": COLOR_ERROR, "info": COLOR_INFO, "none": COLOR_APAGADO}
NIVEL_TEXTO = {"ok": "✅ Correcto", "warn": "⚠️ Mejorable", "bad": "❌ Problema", "info": "ℹ️ Información"}
PASO_ICONO = {"ok": "✅", "warn": "⚠️", "bad": "❌", "info": "ℹ️", "none": "➖"}
VEREDICTO = {dirs.EXISTS: "✅ Existe", dirs.MISSING: "❌ No existe", dirs.UNKNOWN: "⚠️ No se puede saber",
             dirs.INVALID: "❌ No válida"}
AVISO_CONTRASENA = ("Gmail, Yahoo e iCloud piden una contraseña de aplicación si tienen la verificación en dos pasos. "
                    "Outlook.com y Microsoft 365 ya no admiten la contraseña normal en IMAP y POP (piden inicio de sesión "
                    "moderno): ahí «Credenciales» no quiere decir que la contraseña esté mal.")


def nivel_resultado(texto: str) -> str:
    """Nivel de una prueba de servidor (mismos colores que GestCorreo v4)."""
    if not texto or texto in ("-", "N/D"):
        return "none"
    if texto.startswith("OK"):
        return "warn" if "certificado" in texto else "ok"
    if texto.startswith("Probando"):
        return "info"
    if texto.startswith("Credenciales"):
        return "warn"
    return "bad"


def _sin_ayuda(d: QDialog):
    d.setWindowFlags(d.windowFlags() & ~Qt.WindowContextHelpButtonHint)


def _fecha_hora(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000).strftime("%d/%m/%Y %H:%M")


def pasos_html(r: dirs.EmailResult) -> str:
    """Resultado de una dirección: veredicto, avisos y cada paso con su icono (para un QTextBrowser)."""
    e = html.escape
    marcas = "".join(f" &nbsp;<span style='color:{COLOR_AVISO if f != 'de grupo' else COLOR_INFO}'>[{e(f)}]</span>"
                     for f in r.flags)
    nivel = dirs.VERDICT_LEVEL[r.verdict]
    filas = "".join(f"<tr><td style='padding:3px 8px 3px 0'>{PASO_ICONO.get(s.level, 'ℹ️')}</td>"
                    f"<td style='padding:3px 12px 3px 0'><b>{e(s.name)}</b></td><td style='padding:3px 0'>{e(s.text)}</td></tr>"
                    for s in r.steps)
    return (f"<p style='font-size:12pt'><b>{e(r.address)}</b> &nbsp;<span style='color:{COLOR[nivel]}'>"
            f"<b>{e(VEREDICTO[r.verdict])}</b></span>{marcas}</p>"
            + (f"<p>{e(r.reason)}</p>" if r.reason else "") + f"<table>{filas}</table>")


def comprobacion_html(c: dom.Check) -> str:
    e = html.escape
    partes = [f"<p style='font-size:11pt'><b>{e(c.name)}</b> &nbsp;<span style='color:{COLOR[c.level]}'>"
              f"<b>{e(NIVEL_TEXTO[c.level])}</b></span></p><p>{e(c.summary)}</p>"]
    if c.items:
        partes.append("<table>" + "".join(f"<tr><td style='color:{COLOR.get(n, COLOR_INFO)}; padding-right:6px'>●</td>"
                                          f"<td>{e(t)}</td></tr>" for n, t in c.items) + "</table>")
    if c.records:
        partes.append("<p><b>Registros</b></p><pre style='font-family:Consolas; font-size:9pt'>"
                      + e("\n".join(c.records)) + "</pre>")
    if c.advice:
        partes.append(f"<p><b>Qué hacer:</b> {e(c.advice)}</p>")
    return "".join(partes)


class _Dialogo(QDialog):
    """Ventana sencilla de MASys (sin el botón «?») con un cuerpo y botones abajo a la derecha."""

    def __init__(self, padre, titulo: str, ancho: int = 560):
        super().__init__(padre)
        _sin_ayuda(self)
        self.setWindowTitle(titulo)
        self.setMinimumWidth(ancho)
        self.cuerpo = QVBoxLayout(self)
        self.cuerpo.setSpacing(10)
        self.botones = QHBoxLayout()
        self.botones.addStretch()

    def boton(self, texto: str, al_pulsar, rol: str = "ghost"):
        b = piezas.boton(texto, rol, al_pulsar)
        self.botones.addWidget(b)
        return b

    def terminar(self):
        self.cuerpo.addLayout(self.botones)


# ══ Ajustes ═════════════════════════════════════════════════════════════════
class AjustesCorreoDialog(_Dialogo):
    """Cómo se pregunta a los servidores de correo (Comprobar direcciones) y búsqueda en la base de Thunderbird."""

    def __init__(self, padre, db: CorreoDB):
        super().__init__(padre, "⚙️ Ajustes de correo", 620)
        self.db = db
        self.cuerpo.addWidget(piezas.nota("Comprobar direcciones pregunta al servidor de correo del dominio si aceptaría un "
                                          "correo para la dirección y cuelga antes de enviar nada."))
        form = QFormLayout()
        form.setSpacing(8)
        self.pausa = self._numero("pausa", 0, 120)
        self.espera = self._numero("espera", 3, 120)
        form.addRow("Pausa entre preguntas al mismo servidor", self.pausa)
        form.addRow("Esperar la respuesta como mucho", self.espera)
        import socket
        self.helo = QLineEdit(db.ajuste("helo"))
        self.helo.setPlaceholderText(socket.getfqdn())
        form.addRow("Nombre con el que se presenta (EHLO)", self.helo)
        self.remitente = QLineEdit(db.ajuste("remitente"))
        self.remitente.setPlaceholderText("vacío: remitente nulo <>")
        form.addRow("Remitente (MAIL FROM)", self.remitente)
        self.cuerpo.addLayout(form)
        self.cuerpo.addWidget(piezas.nota("Algunos servidores rechazan el remitente nulo: si pasa, pon una dirección que exista "
                                          "de un dominio tuyo."))
        self.cuerpo.addWidget(QLabel("Más dominios de direcciones temporales (uno por línea)"))
        self.extra = QPlainTextEdit(db.ajuste("temporales_extra"))
        self.extra.setFixedHeight(80)
        self.cuerpo.addWidget(self.extra)
        self.thunder = QCheckBox("Buscar en la base de proveedores de Thunderbird los dominios que MASys no conoce")
        self.thunder.setChecked(db.si("thunderbird"))
        self.cuerpo.addWidget(self.thunder)
        self.cuerpo.addWidget(piezas.nota("Solo se envía el dominio (lo que va detrás de la @), nunca el correo ni la contraseña."))
        self.boton("Cancelar", self.reject)
        self.boton("💾 Guardar", self._guardar, "primary")
        self.terminar()

    def _numero(self, clave, minimo, maximo):
        s = QDoubleSpinBox()
        s.setRange(minimo, maximo)
        s.setDecimals(0)
        s.setSuffix(" s")
        s.setValue(self.db.numero(clave))
        return s

    def _guardar(self):
        self.db.guardar_ajuste("pausa", int(self.pausa.value()))
        self.db.guardar_ajuste("espera", int(self.espera.value()))
        self.db.guardar_ajuste("helo", self.helo.text().strip())
        self.db.guardar_ajuste("remitente", self.remitente.text().strip())
        self.db.guardar_ajuste("temporales_extra", self.extra.toPlainText().strip())
        self.db.guardar_ajuste("thunderbird", "1" if self.thunder.isChecked() else "0")
        self.accept()


# ══ 🔑 Probar cuenta ════════════════════════════════════════════════════════
class ConfiguracionDialog(_Dialogo):
    """Proveedor encontrado: servidores, puertos y cifrado para copiar, y «➕ Añadir a Outlook…»."""

    def __init__(self, padre, host, p: prov.Provider, correo: str, resultados: dict):
        super().__init__(padre, f"Configuración: {p.name}", 760)
        self.host, self.p, self.correo = host, p, correo
        ok = all(resultados.get(k, srv.Result("-")).ok for k in ("IMAP", "SMTP"))
        estado = QLabel("✅ Credenciales comprobadas" if ok else "⚠️ Revisa los resultados")
        estado.setStyleSheet(f"color: {COLOR_OK if ok else COLOR_AVISO}; font-weight: 600;")
        self.cuerpo.addWidget(estado)
        form = QFormLayout()
        form.setSpacing(6)
        form.addRow("Usuario", self._copiable(correo))
        filas = [("IMAP (entrada)", p.imap, p.imap_port, p.imap_ssl, resultados.get("IMAP"))]
        if p.pop_port:
            filas.append(("POP3 (entrada, alternativa)", p.pop, p.pop_port, p.pop_ssl, resultados.get("POP3")))
        filas.append(("SMTP (salida)", p.smtp, p.smtp_port, p.smtp_ssl, resultados.get("SMTP")))
        for titulo, servidor, puerto, ssl_directo, res in filas:
            txt = f"{servidor}   ·   puerto {puerto}   ·   {prov.security_name(ssl_directo)}"
            if res is not None:
                txt += f"   ·   {res.text()}"
            form.addRow(titulo, self._copiable(txt, f"{servidor}:{puerto} {prov.security_name(ssl_directo)}"))
        self.cuerpo.addLayout(form)
        self.boton("➕ Añadir a Outlook…", self._anadir)
        self.boton("Cerrar", self.accept, "primary")
        self.terminar()

    @staticmethod
    def _copiable(texto: str, para_copiar: str = "") -> QHBoxLayout:
        lay = QHBoxLayout()
        lay.setSpacing(6)
        e = QLineEdit(texto)
        e.setReadOnly(True)
        e.setCursorPosition(0)
        e.setMinimumWidth(QLabel().fontMetrics().horizontalAdvance(texto) + 24)
        lay.addWidget(e, 1)
        lay.addWidget(piezas.boton("📋 Copiar", "ghost", lambda: QApplication.clipboard().setText(para_copiar or texto)))
        return lay

    def _anadir(self):
        AnadirOutlookDialog(self, self.host, self.p, self.correo).exec_()


class AnadirOutlookDialog(_Dialogo):
    """Alta de la cuenta en Outlook clásico con un archivo PRF (sin contraseña: Outlook la pide al sincronizar)."""

    def __init__(self, padre, host, p: prov.Provider, correo: str):
        super().__init__(padre, "➕ Añadir a Outlook", 520)
        self.host, self.p = host, p
        form = QFormLayout()
        form.setSpacing(8)
        self.perfil = piezas.combo()
        self.perfil.setEditable(True)
        perfiles = olk.profiles()
        for x in perfiles:
            self.perfil.addItem(x.name)
        self.perfil.setCurrentText(olk.default_profile() or (perfiles[0].name if perfiles else "Outlook"))
        piezas.ajustar_combo(self.perfil)
        form.addRow("Perfil", self.perfil)
        self.tipo = piezas.combo(["IMAP", "POP3"] if p.pop_port else ["IMAP"])
        form.addRow("Tipo", self.tipo)
        self.nombre = QLineEdit(correo.split("@")[0])
        form.addRow("Nombre que se ve", self.nombre)
        self.correo = QLineEdit(correo)
        form.addRow("Correo", self.correo)
        self.usuario = QLineEdit(correo)
        form.addRow("Usuario", self.usuario)
        self.cuerpo.addLayout(form)
        self.cuerpo.addWidget(piezas.nota("Outlook pide la contraseña la primera vez que sincroniza (no se guarda en ningún "
                                          "archivo)."))
        self.boton("Cancelar", self.reject)
        self.boton("➕ Añadir", self._aceptar, "primary")
        self.terminar()

    def _aceptar(self):
        if not olk.classic_available():
            self.host._avisar("Añadir a Outlook", "No se encuentra Outlook clásico (OUTLOOK.EXE). El Outlook nuevo de la "
                              "Tienda no admite cuentas por archivo PRF.", QMessageBox.Warning)
            return
        correo = self.correo.text().strip()
        if not srv.valid_email(correo):
            self.host._avisar("Añadir a Outlook", "El correo no es válido.", QMessageBox.Warning)
            return
        if olk.running():
            if not self.host._confirmar_accion("Outlook está abierto", "Para añadir la cuenta hay que cerrar Outlook (solo lee "
                                               "la cuenta al arrancar).\n\nSe le pide que se cierre como al pulsar Salir: "
                                               "guarda lo que tenga pendiente. ¿Cerrarlo?"):
                return
            if not olk.close():
                if not self.host._confirmar_accion("Outlook no se cierra", "Outlook sigue abierto.\n\nSi fuerzas el cierre, se "
                                                   "pierde lo que no tuviera guardado. ¿Forzarlo?", QMessageBox.Warning) \
                        or not olk.force_close():
                    return
        p, pop = self.p, self.tipo.currentText() == "POP3"
        prf = olk.build_prf(self.perfil.currentText().strip() or "Outlook", "POP3" if pop else "IMAP",
                            self.nombre.text().strip(), correo, self.usuario.text().strip(),
                            p.pop if pop else p.imap, p.pop_port if pop else p.imap_port, p.pop_ssl if pop else p.imap_ssl,
                            p.smtp, p.smtp_port, p.smtp_ssl)
        try:
            olk.launch_with_prf(prf)
        except Exception as e:  # noqa: BLE001 - se enseña el motivo
            self.host._avisar("Añadir a Outlook", f"No se pudo arrancar Outlook:\n\n{e}", QMessageBox.Warning)
            return
        self.host._avisar("Añadir a Outlook", f"Outlook se abre con la cuenta {correo}.\n\nDebería aparecer en el panel de "
                          "carpetas; Outlook pedirá la contraseña la primera vez.")
        self.accept()


class ProbarCuentaWidget(QWidget):
    COLS = ["Proveedor", "IMAP", "POP3", "SMTP", "Servidor de entrada", "Servidor de salida"]

    def __init__(self, host, db: CorreoDB):
        super().__init__()
        self.host, self.db = host, db
        self.extra = []          # configuraciones encontradas fuera de la lista (SRV, Thunderbird)
        self._tarea = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(5, 5, 5, 5)
        self.correo = QLineEdit()
        self.correo.setPlaceholderText("usuario@example.com")
        self.correo.returnPressed.connect(self.detectar_y_probar)
        self.clave = QLineEdit()
        self.clave.setEchoMode(QLineEdit.Password)
        self.clave.setPlaceholderText("Contraseña (no se guarda)")
        self.clave.returnPressed.connect(self.detectar_y_probar)
        self.ver = QCheckBox("👁️ Mostrar")
        self.ver.toggled.connect(lambda on: self.clave.setEchoMode(QLineEdit.Normal if on else QLineEdit.Password))
        self.btn_detectar = piezas.boton("🔍 Detectar y probar", "primary", self.detectar_y_probar,
                                         "Reconoce el proveedor y prueba la contraseña solo en sus servidores")
        self.btn_todos = piezas.boton("📋 Probar toda la lista…", "ghost", self.probar_todos,
                                      "Como GestCorreo v4: prueba la contraseña en todos los proveedores")
        self.btn_parar = piezas.boton("⏹️ Detener", "ghost", self._parar)
        self.btn_parar.hide()
        self.btn_ajustes = piezas.boton("⚙️ Ajustes…", "ghost", lambda: AjustesCorreoDialog(self, self.db).exec_())
        f = piezas.fila(QLabel("Correo"), self.correo, QLabel("Contraseña"), self.clave, self.ver, self.btn_detectar,
                        self.btn_todos, self.btn_parar, self.btn_ajustes, estirar=False)
        f.setStretch(1, 3)
        f.setStretch(3, 2)
        lay.addLayout(f)
        self.lbl_encontrado = QLabel("")
        self.lbl_encontrado.setObjectName("countLabel")
        lay.addLayout(piezas.fila(piezas.nota(AVISO_CONTRASENA), estirar=False))
        lay.addLayout(piezas.fila(self.lbl_encontrado, None, QLabel("Doble clic: probar solo ese proveedor"), estirar=False))
        self.modelo = TablaSimple(self.COLS, color=lambda o, c: COLOR.get(nivel_resultado(o[c])) if 1 <= c <= 3 else None,
                                  tooltip=lambda o, c: o[6].get(c) if 1 <= c <= 3 else None)
        self.tabla, self.proxy = piezas.tabla(self.modelo, self, columna_ancha=4)
        self.tabla.doubleClicked.connect(lambda _i: self.probar_fila())
        lay.addWidget(self.tabla, 1)
        # Servidor suelto
        g, gl = piezas.grupo("Probar un servidor (con el correo y la contraseña de arriba)")
        self.proto = piezas.combo(["IMAP", "POP3", "SMTP"])
        self.proto.currentIndexChanged.connect(self._puerto_por_defecto)
        self.servidor = QLineEdit()
        self.servidor.setPlaceholderText("mail.example.com")
        self.servidor.returnPressed.connect(self.probar_servidor)
        self.puerto = QSpinBox()
        self.puerto.setRange(1, 65535)
        self.puerto.setValue(993)
        self.ssl = QCheckBox("SSL/TLS directo")
        self.ssl.setChecked(True)
        self.ssl.setToolTip("Sin marcar: STARTTLS si el servidor lo ofrece")
        self.btn_servidor = piezas.boton("▶️ Probar", "primary", self.probar_servidor)
        fs = piezas.fila(self.proto, self.servidor, QLabel("Puerto"), self.puerto, self.ssl, self.btn_servidor, estirar=False)
        fs.setStretch(1, 1)
        gl.addLayout(fs)
        self.lbl_servidor = QLabel("Escribe un servidor y pulsa Probar.")
        self.lbl_servidor.setWordWrap(True)
        self.lbl_servidor.setTextInteractionFlags(Qt.TextSelectableByMouse)
        gl.addWidget(self.lbl_servidor)
        lay.addWidget(g)
        self._filas = {}
        self.rellenar([p.name for p in prov.PROVIDERS])

    # ── tabla ───────────────────────────────────────────────────────────────
    def _proveedor(self, nombre: str):
        return prov.BY_NAME.get(nombre) or next((p for p in self.extra if p.name == nombre), None)

    def rellenar(self, nombres):
        self._orden = list(nombres)
        self._filas = {}
        for n in nombres:
            p = self._proveedor(n)
            self._filas[n] = [n, "-", "-" if p.pop_port else "N/D", "-", f"{p.imap}:{p.imap_port}" if p.imap else "-",
                              f"{p.smtp}:{p.smtp_port}" if p.smtp else "-", {}]
        self._pintar()

    def _pintar(self):
        filas = [self._filas[n] for n in self._orden]
        self.modelo.set_datos([f[:6] for f in filas], filas)

    def _poner(self, nombre: str, col: int, texto: str, detalle: str = ""):
        f = self._filas.get(nombre)
        if f is None:
            return
        f[col] = texto
        if detalle:
            f[6][col] = detalle
        self._pintar()

    # ── pruebas ─────────────────────────────────────────────────────────────
    def _datos(self):
        c = self.correo.text().strip()
        if not srv.valid_email(c):
            self.host._avisar(T_PROBAR, "Escribe un correo válido.", QMessageBox.Warning)
            return None
        return c, self.clave.text()

    def _ocupado(self, si: bool, texto: str = ""):
        for b in (self.btn_detectar, self.btn_todos, self.btn_servidor):
            b.setEnabled(not si)
        self.btn_parar.setVisible(si)
        if texto:
            self.host.set_status(texto)

    def _probar(self, orden, correo, clave, parar_al_encontrar: bool):
        """Prueba los proveedores en ese orden en segundo plano; la tabla se va rellenando."""
        def trabajo(t):
            encontrado = None
            for k, nombre in enumerate(orden):
                if t.debe_parar():
                    break
                p = self._proveedor(nombre)
                res = {}
                t.progreso.emit(k, f"@{nombre}|*|Probando…|")
                for proto, servidor, puerto, ssl_directo in (("IMAP", p.imap, p.imap_port, p.imap_ssl),
                                                            ("POP3", p.pop, p.pop_port, p.pop_ssl),
                                                            ("SMTP", p.smtp, p.smtp_port, p.smtp_ssl)):
                    if t.debe_parar():
                        break
                    r = srv.test_server(proto, servidor, puerto, ssl_directo, correo, clave)
                    res[proto] = r
                    t.progreso.emit(k, f"@{nombre}|{proto}|{r.text()}|{r.detail}")
                if res.get("IMAP", srv.Result("-")).ok and res.get("SMTP", srv.Result("-")).ok:
                    encontrado = (nombre, res)
                    if parar_al_encontrar:
                        break
            return encontrado
        t = tareas.Tarea(lambda: trabajo(t))
        t.progreso.connect(self._progreso)
        t.ok.connect(self._terminado)
        t.error.connect(lambda m: (self._ocupado(False), self.host._avisar(T_PROBAR, f"No se pudo completar:\n\n{m}",
                                                                          QMessageBox.Warning)))
        self._tarea = t
        self._ocupado(True, "🔑 Probando la cuenta…")
        t.start()

    def _progreso(self, _k, texto: str):
        if not texto.startswith("@"):
            return
        nombre, proto, valor, detalle = (texto[1:].split("|", 3) + [""])[:4]
        if proto == "*":
            for col in (1, 2, 3):
                if self._filas.get(nombre, ["", "", ""])[col] != "N/D":
                    self._poner(nombre, col, valor)
            self.host.set_status(f"🔑 Probando {nombre}…")
        else:
            self._poner(nombre, {"IMAP": 1, "POP3": 2, "SMTP": 3}[proto], valor, detalle)

    def _terminado(self, encontrado):
        self._ocupado(False)
        piezas.refrescar_export(self.host)
        if encontrado:
            nombre, res = encontrado
            self.lbl_encontrado.setText(f"✅ Encontrado: {nombre}")
            self.host.set_status(f"✅ Proveedor encontrado: {nombre}")
            ConfiguracionDialog(self, self.host, self._proveedor(nombre), self.correo.text().strip(), res).exec_()
        elif self._tarea is not None and self._tarea.debe_parar():
            self.host.set_status("⏹️ Prueba detenida")
        else:
            self.host.set_status("⚠️ Sin coincidencia completa: pasa el ratón por los resultados para ver la respuesta del servidor")

    def detectar_y_probar(self):
        v = self._datos()
        if not v:
            return
        correo, clave = v
        self._ocupado(True, "🔍 Buscando el proveedor…")
        thunder = self.db.si("thunderbird")

        def despues(det: srv.Detection):
            self._ocupado(False)
            self.extra = det.extra
            nombres = det.names + [p.name for p in det.extra]
            if not nombres:
                self.lbl_encontrado.setText("⚠️ Proveedor no reconocido")
                self.rellenar([p.name for p in prov.PROVIDERS])
                self.host._avisar(T_PROBAR, f"No se reconoce el proveedor de {det.domain}.\n\n"
                                  + ("Servidor de correo (MX): " + ", ".join(det.mx[:3]) + "\n\n" if det.mx else "")
                                  + "Puedes probar un servidor concreto abajo o «📋 Probar toda la lista…».")
                return
            como = det.how.get(nombres[0]) or ("por sus registros SRV" if "SRV" in nombres[0]
                                                else "por la base de datos de Thunderbird")
            self.lbl_encontrado.setText(f"🔎 {nombres[0]} · {como}")
            self.rellenar(nombres + [p.name for p in prov.PROVIDERS if p.name not in nombres])
            self._probar(nombres, correo, clave, parar_al_encontrar=True)
        tareas.lanzar(self, "_tarea_detectar", srv.detect, correo, thunder, al_terminar=despues,
                      al_fallar=lambda m: (self._ocupado(False), self.host._avisar(
                          T_PROBAR, f"No se pudo buscar el proveedor:\n\n{m}", QMessageBox.Warning)))

    def probar_todos(self):
        v = self._datos()
        if not v:
            return
        n = len(prov.PROVIDERS)
        if not self.host._confirmar_accion("Probar toda la lista", f"La contraseña se enviará a los servidores de {n} "
                                           "proveedores distintos.\n\nLo normal es «🔍 Detectar y probar», que solo la manda "
                                           "al proveedor del correo. Úsalo solo con una cuenta de prueba o si sabes lo que "
                                           "haces. ¿Probar todos?", QMessageBox.Warning):
            return
        correo, clave = v
        primero = prov.by_domain(correo.rsplit("@", 1)[-1])
        orden = primero + [p.name for p in prov.PROVIDERS if p.name not in primero]
        self.extra = []
        self.rellenar(orden)
        self._probar(orden, correo, clave, parar_al_encontrar=True)

    def probar_fila(self):
        fila = objeto_seleccionado(self.tabla, self.proxy, self.modelo)
        v = self._datos() if fila else None
        if v:
            self._probar([fila[0]], *v, parar_al_encontrar=False)

    def _parar(self):
        if self._tarea is not None:
            self._tarea.parar()
        self.host.set_status("⏹️ Deteniendo…")

    def _puerto_por_defecto(self, i):
        self.puerto.setValue([993, 995, 587][i])
        self.ssl.setChecked(i != 2)

    def probar_servidor(self):
        servidor = self.servidor.text().strip()
        if not servidor:
            self.lbl_servidor.setText("Escribe un servidor.")
            self.lbl_servidor.setStyleSheet(f"color: {COLOR_ERROR};")
            return
        proto, puerto, ssl_directo = self.proto.currentText(), self.puerto.value(), self.ssl.isChecked()
        self.lbl_servidor.setText(f"Probando {proto} en {servidor}:{puerto}…")
        self.lbl_servidor.setStyleSheet("")
        self.btn_servidor.setEnabled(False)

        def visto(r: srv.Result):
            self.btn_servidor.setEnabled(True)
            self.lbl_servidor.setText(f"{proto} {servidor}:{puerto}  →  {r.text()}" + (f"   ·   {r.detail[:160]}" if r.detail else ""))
            self.lbl_servidor.setStyleSheet(f"color: {COLOR[nivel_resultado(r.text())]};")
            self.host.set_status(f"🔑 Servidor {servidor}: {r.text()}")
        tareas.lanzar(self, "_tarea_servidor", srv.test_server, proto, servidor, puerto, ssl_directo,
                      self.correo.text().strip(), self.clave.text(), al_terminar=visto,
                      al_fallar=lambda m: (self.btn_servidor.setEnabled(True), self.lbl_servidor.setText(f"Error: {m}")))

    def filas_export(self):
        return (T_PROBAR, self.COLS, [f[:6] for f in (self._filas[n] for n in self._orden)])


# ══ ✉️ Comprobar direcciones ════════════════════════════════════════════════
class PasosDialog(_Dialogo):
    def __init__(self, padre, r: dirs.EmailResult):
        super().__init__(padre, "Detalle de la comprobación", 620)
        t = QTextBrowser()
        t.setHtml(pasos_html(r))
        t.setMinimumHeight(260)
        self.cuerpo.addWidget(t)
        self.boton("Cerrar", self.accept, "primary")
        self.terminar()


class DireccionesWidget(QWidget):
    COLS_LISTA = ["Correo", "Resultado", "Avisos", "Motivo", "Servidor de correo"]
    COLS_HIST = ["Correo", "Resultado", "Fecha"]

    def __init__(self, host, db: CorreoDB):
        super().__init__()
        self.host, self.db = host, db
        self.resultados = []
        self._tarea = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(5, 5, 5, 5)
        self.modo = piezas.combo(["Una dirección", "Lista de direcciones"])
        self.modo.currentIndexChanged.connect(lambda i: self.pila.setCurrentIndex(i))
        self.buzon = QCheckBox("Preguntar al servidor si el buzón existe (puerto 25)")
        self.buzon.setChecked(db.si("buzon"))
        self.buzon.toggled.connect(lambda v: db.guardar_ajuste("buzon", "1" if v else "0"))
        self.buzon.setToolTip("Sin marcar solo se mira la forma y el dominio")
        lay.addLayout(piezas.fila(self.modo, self.buzon, None,
                                  piezas.boton("⚙️ Ajustes…", "ghost", lambda: AjustesCorreoDialog(self, self.db).exec_()),
                                  estirar=False))
        lay.addWidget(piezas.nota("Se mira si está bien escrita, si el dominio recibe correo y se pregunta a su servidor si "
                                  "aceptaría un correo para esa dirección, sin enviar nada."))
        self.pila = QStackedWidget()
        self.pila.addWidget(self._una())
        self.pila.addWidget(self._lista())
        lay.addWidget(self.pila, 1)

    # ── una ─────────────────────────────────────────────────────────────────
    def _una(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        self.direccion = QLineEdit()
        self.direccion.setPlaceholderText("usuario@example.com")
        self.direccion.returnPressed.connect(self.comprobar_una)
        self.btn_una = piezas.boton("▶️ Comprobar", "primary", self.comprobar_una)
        f = piezas.fila(self.direccion, self.btn_una, estirar=False)
        f.setStretch(0, 1)
        v.addLayout(f)
        sp = QSplitter(Qt.Vertical)
        sp.setChildrenCollapsible(False)
        self.detalle = QTextBrowser()
        self.detalle.setHtml("<p>Escribe una dirección y pulsa Comprobar.</p>")
        sp.addWidget(self.detalle)
        caja = QWidget()
        cv = QVBoxLayout(caja)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.addLayout(piezas.fila(QLabel("<b>Últimas comprobaciones</b>"), None,
                                 piezas.boton("✕ Quitar del historial", "ghost", self._quitar_historial), estirar=False))
        self.m_hist = TablaSimple(self.COLS_HIST, color=lambda h, c: COLOR.get(dirs.VERDICT_LEVEL.get(h["resumen"]))
                                  if c == 1 else None)
        self.t_hist, self.p_hist = piezas.tabla(self.m_hist, self, columna_ancha=0, seleccion_multiple=True)
        self.t_hist.clicked.connect(lambda _i: self._abrir_historial())
        cv.addWidget(self.t_hist, 1)
        sp.addWidget(caja)
        sp.setSizes([260, 220])
        v.addWidget(sp, 1)
        self._cargar_historial()
        return w

    def comprobar_una(self):
        a = self.direccion.text().strip()
        if not a:
            return
        self.btn_una.setEnabled(False)
        self.host.set_status(f"✉️ Comprobando {a}…")
        chk = self.db.comprobador(self.buzon.isChecked())

        def visto(r: dirs.EmailResult):
            self.btn_una.setEnabled(True)
            self.detalle.setHtml(pasos_html(r))
            self.host.set_status(f"✉️ {r.address}: {r.verdict}")
            self.db.anadir_historial("direccion", r.address, r.verdict, r.to_dict())
            self._cargar_historial()
        tareas.lanzar(self, "_tarea_una", chk.check, a, al_terminar=visto,
                      al_fallar=lambda m: (self.btn_una.setEnabled(True), self.host._avisar(
                          T_DIRECCIONES, f"No se pudo comprobar:\n\n{m}", QMessageBox.Warning)))

    def _cargar_historial(self):
        hist = self.db.historial("direccion")
        self.m_hist.set_datos([[h["objetivo"], VEREDICTO.get(h["resumen"], h["resumen"]), _fecha_hora(h["ts"])]
                               for h in hist], hist)

    def _abrir_historial(self):
        h = objeto_seleccionado(self.t_hist, self.p_hist, self.m_hist)
        if h and h.get("datos"):
            self.direccion.setText(h["objetivo"])
            self.detalle.setHtml(pasos_html(dirs.EmailResult.from_dict(h["datos"])))

    def _quitar_historial(self):
        ids = [h["id"] for h in objetos_seleccionados(self.t_hist, self.p_hist, self.m_hist) if h]
        if not ids:
            self.host._avisar(T_DIRECCIONES, "Elige en la lista las comprobaciones que quieres quitar.")
            return
        if self.host._confirmar_accion("Historial", f"¿Quitar {len(ids)} comprobación(es) del historial?"):
            self.db.quitar_historial(ids)
            self._cargar_historial()

    # ── lista ───────────────────────────────────────────────────────────────
    def _lista(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(piezas.nota("Pega las direcciones (una por línea, o separadas por comas o espacios) o abre un CSV o un "
                                "TXT: se sacan solas, sin repetir."))
        self.pegadas = QPlainTextEdit()
        self.pegadas.setPlaceholderText("ana@example.com\nsoporte@example.net")
        self.pegadas.setFixedHeight(90)
        self.pegadas.textChanged.connect(self._contar)
        v.addWidget(self.pegadas)
        self.lbl_n = QLabel("")
        self.lbl_n.setObjectName("countLabel")
        self.btn_lista = piezas.boton("▶️ Comprobar lista", "primary", self.comprobar_lista)
        self.btn_parar = piezas.boton("⏹️ Detener", "ghost", self._parar)
        self.btn_parar.hide()
        self.lbl_resumen = QLabel("")
        self.lbl_resumen.setObjectName("countLabel")
        v.addLayout(piezas.fila(piezas.boton("📂 Abrir CSV o TXT…", "ghost", self._abrir_archivo), self.lbl_n, None,
                                self.lbl_resumen, self.btn_parar, self.btn_lista, estirar=False))
        self.m_lista = TablaSimple(self.COLS_LISTA, color=lambda r, c: COLOR[dirs.VERDICT_LEVEL[r.verdict]] if c == 1 else None)
        self.t_lista, self.p_lista = piezas.tabla(self.m_lista, self, columna_ancha=3)
        self.t_lista.doubleClicked.connect(lambda _i: self._detalle_lista())
        v.addWidget(self.t_lista, 1)
        v.addWidget(piezas.nota("Doble clic en una fila para ver el detalle. Entre dos preguntas al mismo servidor se espera "
                                "unos segundos (se cambia en Ajustes) para que no bloqueen tu IP. Para guardar los resultados, usa "
                                "CSV o Excel de la barra de abajo."))
        self._contar()
        return w

    def _contar(self):
        n = len(dirs.extract(self.pegadas.toPlainText()))
        self.lbl_n.setText(f"{n} dirección(es)" if n else "")

    def _abrir_archivo(self):
        ruta = dialogo_abrir_archivo(self, "Lista de direcciones", self.db.ajuste("carpeta_lista") or os.path.expanduser("~"),
                                     "Listas (*.csv *.txt);;Todos (*.*)")
        if not ruta:
            return
        self.db.guardar_ajuste("carpeta_lista", os.path.dirname(ruta))
        encontradas = dirs.read_list_file(ruta)
        if not encontradas:
            self.host._avisar(T_DIRECCIONES, "No se ha encontrado ninguna dirección en el archivo.", QMessageBox.Warning)
            return
        self.pegadas.setPlainText("\n".join(encontradas))

    def _pintar_lista(self):
        self.m_lista.set_datos([[r.address, VEREDICTO[r.verdict], ", ".join(r.flags), r.reason, ", ".join(r.mx[:2])]
                                for r in self.resultados], self.resultados)
        cuenta = {k: sum(1 for r in self.resultados if r.verdict == k) for k in VEREDICTO}
        self.lbl_resumen.setText("  ·  ".join(f"{VEREDICTO[k]}: {n}" for k, n in cuenta.items() if n))

    def comprobar_lista(self):
        direcciones = dirs.extract(self.pegadas.toPlainText())
        if not direcciones:
            self.host._avisar(T_DIRECCIONES, "No hay direcciones en la lista.", QMessageBox.Warning)
            return
        preguntar = self.buzon.isChecked()
        pausa = self.db.numero("pausa")
        if preguntar and len(direcciones) > 200 and not self.host._confirmar_accion(
                T_DIRECCIONES, f"Son {len(direcciones)} direcciones y se pregunta a sus servidores una por una.\n\nMuchas "
                "preguntas seguidas desde la misma IP pueden hacer que algunos servidores la bloqueen un tiempo. ¿Seguir?"):
            return
        self.resultados = []
        self._pintar_lista()
        chk = self.db.comprobador(preguntar)
        hechas = []

        def trabajo(t):
            ultimo = {}
            for i, a in enumerate(direcciones):
                if t.debe_parar():
                    break
                t.progreso.emit(i, f"✉️ Comprobando {a} ({i + 1} de {len(direcciones)})")
                if preguntar:
                    dirs.pause_between(ultimo, a.rsplit("@", 1)[-1].lower(), pausa, t.debe_parar)
                hechas.append(chk.check(a))
                t.progreso.emit(-1, "")
            return hechas
        t = tareas.Tarea(lambda: trabajo(t))
        t.progreso.connect(lambda i, texto: self._nueva_fila(hechas) if i == -1 else self.host.set_status(texto))
        t.ok.connect(self._lista_hecha)
        t.error.connect(lambda m: (self._ocupado(False), self.host._avisar(T_DIRECCIONES, f"No se pudo completar:\n\n{m}",
                                                                          QMessageBox.Warning)))
        self._tarea = t
        self._ocupado(True)
        t.start()

    def _nueva_fila(self, hechas):
        if len(hechas) != len(self.resultados):
            self.resultados = list(hechas)
            self._pintar_lista()

    def _lista_hecha(self, hechas):
        self.resultados = list(hechas)
        self._pintar_lista()
        self._ocupado(False)
        parada = self._tarea is not None and self._tarea.debe_parar()
        self.host.set_status(f"✅ Lista comprobada: {len(hechas)} dirección(es)" + (" (detenida)" if parada else ""))
        piezas.refrescar_export(self.host)

    def _ocupado(self, si: bool):
        self.btn_lista.setEnabled(not si)
        self.btn_parar.setVisible(si)

    def _parar(self):
        if self._tarea is not None:
            self._tarea.parar()

    def _detalle_lista(self):
        r = objeto_seleccionado(self.t_lista, self.p_lista, self.m_lista)
        if r is not None:
            PasosDialog(self, r).exec_()

    def filas_export(self):
        if self.pila.currentIndex() == 1:
            return ("Comprobación de direcciones", self.COLS_LISTA, self.m_lista.filas())
        return ("Últimas comprobaciones de direcciones", self.COLS_HIST, self.m_hist.filas())


# ══ 🌐 Dominio de correo ════════════════════════════════════════════════════
class DominioWidget(QWidget):
    COLS = ["Comprobación", "Estado", "Resumen"]

    def __init__(self, host, db: CorreoDB):
        super().__init__()
        self.host, self.db = host, db
        self.informe = None
        self._tarea = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(5, 5, 5, 5)
        self.dominio = QLineEdit()
        self.dominio.setPlaceholderText("example.com (o una dirección de correo)")
        self.dominio.returnPressed.connect(self.comprobar)
        self.selector = QLineEdit()
        self.selector.setPlaceholderText("Selector DKIM (opcional)")
        self.selector.setToolTip("El s= de la cabecera DKIM-Signature de un correo del dominio. Se prueban también los habituales.")
        self.btn = piezas.boton("▶️ Comprobar", "primary", self.comprobar)
        self.btn_parar = piezas.boton("⏹️ Detener", "ghost", lambda: self._tarea.parar() if self._tarea else None)
        self.btn_parar.hide()
        self.btn_hist = piezas.boton("🕓 Historial", "ghost", self._menu_historial)
        self.btn_informe = piezas.boton("🌐 Informe HTML…", "ghost", self.guardar_informe)
        self.btn_informe.setEnabled(False)
        f = piezas.fila(self.dominio, self.selector, self.btn, self.btn_parar, self.btn_hist, self.btn_informe, estirar=False)
        f.setStretch(0, 3)
        f.setStretch(1, 1)
        lay.addLayout(f)
        self.lbl = QLabel("Escribe un dominio y pulsa Comprobar. Se leen sus registros públicos y se saluda a su servidor de "
                          "correo, sin enviar nada.")
        self.lbl.setObjectName("countLabel")
        self.lbl.setWordWrap(True)
        lay.addWidget(self.lbl)
        sp = QSplitter(Qt.Vertical)
        sp.setChildrenCollapsible(False)
        self.modelo = TablaSimple(self.COLS, color=lambda c, col: COLOR[c.level] if col == 1 else None)
        self.tabla, self.proxy = piezas.tabla(self.modelo, self, columna_ancha=2)
        self.tabla.selectionModel().selectionChanged.connect(lambda *_a: self._detalle())
        sp.addWidget(self.tabla)
        self.detalle = QTextBrowser()
        sp.addWidget(self.detalle)
        sp.setSizes([280, 240])
        lay.addWidget(sp, 1)

    def comprobar(self):
        d = dom.normalize_domain(self.dominio.text())
        if not d or "." not in d:
            self.host._avisar(T_DOMINIO, "Escribe un dominio, por ejemplo example.com.", QMessageBox.Warning)
            return
        selectores = [x.strip() for x in self.selector.text().replace(",", " ").split() if x.strip()]
        t = tareas.Tarea(lambda: dom.run(d, selectores, progress=lambda i, n, texto: t.progreso.emit(i, texto),
                                         cancelled=t.debe_parar))
        t.progreso.connect(lambda _i, texto: self.host.set_status(f"🌐 {texto}"))
        t.ok.connect(self._hecho)
        t.error.connect(lambda m: (self._ocupado(False), self.host._avisar(T_DOMINIO, f"No se pudo comprobar:\n\n{m}",
                                                                          QMessageBox.Warning)))
        self._tarea = t
        self._ocupado(True)
        t.start()

    def _ocupado(self, si):
        self.btn.setEnabled(not si)
        self.btn_parar.setVisible(si)

    def _hecho(self, rep: dom.Report):
        self._ocupado(False)
        parado = self._tarea is not None and self._tarea.debe_parar()
        self.mostrar(rep)
        n = rep.counts()
        self.host.set_status(f"🌐 {rep.domain}: {n['bad']} problema(s), {n['warn']} mejorable(s)" + (" · detenido" if parado else ""))
        if not parado:
            self.db.anadir_historial("dominio", rep.domain, f"{n['bad']}/{n['warn']}/{n['ok']}", rep.to_dict())
        piezas.refrescar_export(self.host)

    def mostrar(self, rep: dom.Report):
        self.informe = rep
        orden = {"bad": 0, "warn": 1, "ok": 2, "info": 3}
        lista = sorted(rep.checks, key=lambda c: orden.get(c.level, 4))
        self.modelo.set_datos([[c.name, NIVEL_TEXTO[c.level], c.summary] for c in lista], lista)
        n = rep.counts()
        self.lbl.setText(f"{rep.domain} · {rep.when}   —   " + "  ·  ".join(
            f"{NIVEL_TEXTO[k]}: {n[k]}" for k in ("bad", "warn", "ok", "info") if n[k]))
        self.btn_informe.setEnabled(True)
        if lista:
            self.tabla.selectRow(0)
        self._detalle()

    def _detalle(self):
        c = objeto_seleccionado(self.tabla, self.proxy, self.modelo)
        self.detalle.setHtml(comprobacion_html(c) if c else "")

    def _menu_historial(self):
        hist = self.db.historial("dominio", 60)
        m = QMenu(self)
        if not hist:
            a = m.addAction("Todavía no hay dominios comprobados")
            a.setEnabled(False)
        vistos = set()
        for h in hist:
            if h["objetivo"] in vistos:
                continue
            vistos.add(h["objetivo"])
            m.addAction(f"{h['objetivo']}   ·   {_fecha_hora(h['ts'])}").triggered.connect(
                lambda _=False, h=h: self._abrir(h))
        m.exec_(self.btn_hist.mapToGlobal(self.btn_hist.rect().bottomLeft()))

    def _abrir(self, h):
        if h.get("datos"):
            self.dominio.setText(h["objetivo"])
            self.mostrar(dom.Report.from_dict(h["datos"]))

    def guardar_informe(self):
        if not self.informe:
            return
        nombre = f"Correo_{self.informe.domain}_{time.strftime('%Y%m%d_%H%M%S')}.html"
        ruta = dialogo_guardar_archivo(self, "Guardar informe", os.path.join(get_informes_folder(), nombre), "HTML (*.html)")
        if not ruta:
            return
        try:
            with open(ruta, "w", encoding="utf-8") as f:
                f.write(inf.domain_html(self.informe))
        except OSError as e:
            self.host._avisar(T_DOMINIO, f"No se pudo guardar:\n\n{e}", QMessageBox.Warning)
            return
        if self.host._confirmar_accion("Informe", f"Informe guardado en:\n\n{ruta}\n\n¿Abrirlo?"):
            os.startfile(ruta)

    def filas_export(self):
        titulo = f"Correo del dominio {self.informe.domain}" if self.informe else T_DOMINIO
        return (titulo, self.COLS, self.modelo.filas())


# ══ La sección ═════════════════════════════════════════════════════════════
class CorreoMixin:
    def crear_pestanas_correo(self, pestanas):
        """Añade las pestañas nuevas detrás de «📧 Cuentas» (que ya está en `pestanas`)."""
        from msys.ui.correo_outlook import ConfigOutlookWidget, VisorPstWidget
        self.correo_db = CorreoDB()
        self.correo_probar = ProbarCuentaWidget(self, self.correo_db)
        self.correo_direcciones = DireccionesWidget(self, self.correo_db)
        self.correo_dominio = DominioWidget(self, self.correo_db)
        self.correo_outlook = ConfigOutlookWidget(self, self.correo_db)
        self.correo_visor = VisorPstWidget(self, self.correo_db)
        # Títulos escritos tal cual (= T_*): tests/test_conocimiento.py busca así las pestañas que citan las fichas
        pestanas.addTab(self.correo_probar, "🔑 Probar cuenta")
        pestanas.addTab(self.correo_direcciones, "✉️ Comprobar direcciones")
        pestanas.addTab(self.correo_dominio, "🌐 Dominio de correo")
        pestanas.addTab(self.correo_outlook, "🗂️ Configuración de Outlook")
        pestanas.addTab(self.correo_visor, "📬 Visor PST/OST")
        pestanas.currentChanged.connect(lambda _i: self._actualizar_barra_exportacion())
        self.registrar_export(SECCION, self._correo_export)

    def _correo_export(self):
        """La pestaña visible. «📧 Cuentas» devuelve [] y la barra usa su tabla de siempre (email_proxy)."""
        w = self.correo_tabs.currentWidget() if hasattr(self, "correo_tabs") else None
        return [w.filas_export()] if w is not None and hasattr(w, "filas_export") else []
