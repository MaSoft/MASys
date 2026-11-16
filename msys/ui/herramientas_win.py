# -*- coding: utf-8 -*-
"""5.6.x — Botones y ventanas de las herramientas de diagnóstico de Windows (motor: msys/domain/herramientas_win.py).

Cada herramienta tiene un botón en la sección donde encaja (Conexiones Remotas, Seguridad, Sistema, Software,
Impresoras, Migración, Herramientas de red) y comparte la ventana `EjecucionDialog`: salida en vivo, «⏹ Parar», y al
terminar el informe HTML, la carpeta, la guía y la tabla del CSV. No es modal: MASys se puede seguir usando."""

import os
import webbrowser

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
                             QLineEdit, QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from msys.domain import herramientas_win as hw
from msys.models.tabla_simple import TablaSimple
from msys.ui import piezas


class _Hilo(QThread):
    linea = pyqtSignal(str)
    hecho = pyqtSignal(object)

    def __init__(self, clave, argumentos, carpeta, timeout=None):
        super().__init__()
        self._a = (clave, argumentos, carpeta, timeout)
        self._parar = False

    def parar(self):
        self._parar = True

    def run(self):
        clave, argumentos, carpeta, timeout = self._a
        try:
            r = hw.ejecutar(clave, argumentos, carpeta, timeout=timeout, al_linea=self.linea.emit,
                            debe_parar=lambda: self._parar)
        except Exception as e:  # el error se enseña en la ventana
            r = hw.Resultado(-1, f"No se pudo ejecutar: {e}", carpeta)
        self.hecho.emit(r)


class EjecucionDialog(QDialog):
    """Progreso y resultado de una herramienta. al_terminar(resultado) para el historial de reparaciones."""

    def __init__(self, host, clave, argumentos, carpeta, timeout=None, al_terminar=None, explicacion=""):
        super().__init__(host)
        self.host, self.clave, self.carpeta, self._al_terminar = host, clave, carpeta, al_terminar
        self.resultado = None
        self.setWindowTitle(f"🧰 {hw.titulo(clave)}")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.resize(940, 620)
        lay = QVBoxLayout(self)
        self.lbl = QLabel(f"🔄 En marcha… {explicacion}".strip())
        self.lbl.setWordWrap(True)
        lay.addWidget(self.lbl)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(5000)
        self.log.setStyleSheet("font-family: Consolas; font-size: 9pt;")
        lay.addWidget(self.log, 2)
        self.modelo = TablaSimple(["Resultado"])
        self.vista, self.proxy = piezas.tabla(self.modelo, self, columna_ancha=0)
        self.vista.setVisible(False)
        lay.addWidget(self.vista, 3)
        self.btn_parar = piezas.boton("⏹ Parar", "ghost", self._parar)
        self.btn_html = piezas.boton("🌐 Abrir informe", "primary", self._abrir_html)
        self.btn_carpeta = piezas.boton("📂 Abrir carpeta", "ghost", lambda: os.startfile(self.carpeta))
        self.btn_guia = piezas.boton("📖 Guía", "ghost", self._abrir_guia, "Guía detallada de la herramienta")
        self.btn_cerrar = piezas.boton("Cerrar", "ghost", self.close)
        for b in (self.btn_html, self.btn_carpeta):
            b.setEnabled(False)
        lay.addLayout(piezas.fila(self.btn_parar, None, self.btn_guia, self.btn_carpeta, self.btn_html, self.btn_cerrar))
        self._inicio = QTimer(self)
        self.hilo = _Hilo(clave, argumentos, carpeta, timeout)
        self.hilo.linea.connect(self.log.appendPlainText)
        self.hilo.hecho.connect(self._hecho)
        self.hilo.start()

    def _parar(self):
        self.hilo.parar()
        self.btn_parar.setEnabled(False)
        self.lbl.setText("⏹ Parando… (se guarda lo recogido hasta ahora si la herramienta lo permite)")

    def _hecho(self, r):
        self.resultado = r
        self.btn_parar.setEnabled(False)
        htmls, csvs = r.de_tipo(".html") or r.de_tipo(".txt") or r.de_tipo(".log"), r.de_tipo(".csv")
        self.btn_html.setEnabled(bool(htmls))
        self.btn_carpeta.setEnabled(True)
        estado = "⏹ Parada" if r.parado else ("✅ Terminada" if r.codigo == 0 else f"⚠️ Terminó con avisos (código {r.codigo})")
        self.lbl.setText(f"{estado} · {len(r.archivos)} archivo(s) en {r.carpeta}")
        if csvs:
            cab, filas = hw.leer_csv(csvs[0])
            if cab:
                self.modelo = TablaSimple(cab)
                self.modelo.set_datos(filas)
                self.proxy.setSourceModel(self.modelo)
                self.vista.setVisible(True)
        if self._al_terminar:
            try:
                self._al_terminar(r)
            except Exception:
                pass

    def _abrir_html(self):
        r = self.resultado
        informes = (r.de_tipo(".html") or r.de_tipo(".txt") or r.de_tipo(".log")) if r else []
        if informes:
            os.startfile(informes[0])

    def _abrir_guia(self):
        ruta = hw.ruta_guia(self.clave)
        if os.path.exists(ruta):
            webbrowser.open(ruta)

    def closeEvent(self, e):
        if self.hilo.isRunning():
            if not self.host._confirmar_accion("Herramienta en marcha", "La herramienta sigue en marcha. ¿Pararla y cerrar?"):
                e.ignore()
                return
            self.hilo.parar()
            self.hilo.wait(20000)
        super().closeEvent(e)


class _Opciones(QDialog):
    """Formulario sencillo: campos = [(clave, etiqueta, widget)]."""

    def __init__(self, padre, titulo, explicacion, campos, texto_aceptar="▶️ Empezar"):
        super().__init__(padre)
        self.setWindowTitle(titulo)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        if explicacion:
            lay.addWidget(piezas.nota(explicacion))
        form = QFormLayout()
        self.campos = {}
        for clave, etiqueta, w in campos:
            form.addRow(etiqueta, w) if etiqueta else form.addRow(w)
            self.campos[clave] = w
        lay.addLayout(form)
        botones = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        botones.button(QDialogButtonBox.Ok).setText(texto_aceptar)
        botones.accepted.connect(self.accept)
        botones.rejected.connect(self.reject)
        lay.addWidget(botones)


def _spin(minimo, maximo, valor, sufijo=""):
    s = QSpinBox()
    s.setRange(minimo, maximo)
    s.setValue(valor)
    if sufijo:
        s.setSuffix(sufijo)
    return s


def _combo(opciones):
    c = QComboBox()
    for texto, dato in opciones:
        c.addItem(texto, dato)
    return c


class HerramientasWinMixin:
    """Se instala desde multisys.py tras crear las secciones (`_hw_instalar_botones`)."""

    # ── enganche de botones ──
    def _hw_boton_antes_de(self, referencia, texto, ayuda, al_pulsar):
        if referencia is None or referencia.parentWidget() is None:
            return None
        lay = referencia.parentWidget().layout()
        boton = QPushButton(texto)
        boton.setToolTip(ayuda)
        boton.clicked.connect(lambda _=False: al_pulsar())
        # se busca el layout que contiene de verdad al botón de referencia
        destino = _layout_que_contiene(lay, referencia)
        if destino is None:
            return None
        destino.insertWidget(destino.indexOf(referencia), boton)
        return boton

    def _hw_instalar_botones(self):
        g = lambda n: getattr(self, n, None)  # noqa: E731
        self.btn_hw_accesos = self._hw_boton_antes_de(
            g("btn_activar_auditoria_remota"), "🔎 Auditoría de accesos…",
            "Informe de TODOS los accesos al equipo: inicios y cierres de sesión, fallos, Escritorio remoto, bloqueos "
            "y cambios en cuentas de usuario (registro de Seguridad). Solo lee.", self.hw_auditoria_accesos)
        self.btn_hw_aviso_rdp = self._hw_boton_antes_de(
            g("btn_activar_auditoria_remota"), "🖧 Aviso de RDP…",
            "Ver, quitar o restaurar el aviso que muestra Escritorio remoto al abrir archivos .rdp", self.hw_aviso_rdp)
        self.btn_hw_bitlocker = self._hw_boton_antes_de(
            g("btn_refresh_seguridad"), "🔑 Claves de BitLocker…",
            "Estado de BitLocker de cada unidad y sus contraseñas de recuperación. Solo lee.", self.hw_bitlocker)
        self.btn_hw_win11 = self._hw_boton_antes_de(
            g("btn_refresh_system"), "🪟 Windows 11…",
            "Requisitos completos de Windows 11 y cómo funcionará según el uso. Solo lee.", self.hw_windows11)
        self.btn_hw_rendimiento = self._hw_boton_antes_de(
            g("btn_refresh_system"), "📈 Rendimiento…",
            "Captura CPU, RAM, disco, red y procesos durante minutos u horas y dice cuándo se satura y quién lo provoca",
            self.hw_rendimiento)
        self.btn_hw_office = self._hw_boton_antes_de(
            g("btn_refresh_software"), "📥 Instalar Office…",
            "Instalación desatendida de Microsoft 365 Apps / Office con la herramienta oficial de Microsoft (ODT)",
            self.hw_instalar_office)
        self.btn_hw_impresoras = self._hw_boton_antes_de(
            g("btn_refresh_printers"), "📡 Vigilar impresoras…",
            "Vigila las impresoras de red: caídas, puerto de impresión, cambios de MAC (conflictos de IP) y cola atascada",
            self.hw_vigilar_impresoras)
        self.btn_hw_thunderbird = self._hw_boton_antes_de(
            g("btn_refresh_email"), "✉️ Thunderbird…",
            "Diagnóstico y exportación de Thunderbird antes de pasar a Outlook: contactos, calendario, correo local y firmas",
            self.hw_thunderbird)
        self.btn_hw_microparadas = self._hw_boton_antes_de(
            g("btn_refresh_disk_health"), "⏱️ Microparadas…",
            "Mide la latencia de cada lectura del disco físico sin caché y cuenta las paradas (portado de MADisk). Solo lee.",
            self.hw_microparadas)
        self._hw_pestana_red()

    def _hw_pestana_red(self):
        tabs = getattr(self, "hred_tabs", None)
        if tabs is None:
            return
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 14, 16, 14)
        for texto, explicacion, funcion in (
                ("🔐 Diagnóstico HTTPS / TLS…", "Por qué el equipo no conecta por HTTPS con un servidor: TLS, certificado, proxy y "
                 ".NET. Con «Arreglar» activa TLS 1.2 en Windows y .NET guardando copia para deshacer.", self.hw_tls),
                ("📶 Vigilar microcortes…", "Vigila la conexión un tiempo (pings cada segundo a la puerta de enlace, al DNS y a "
                 "Internet) y dice cada corte y dónde está el problema: red local, Internet o DNS.", self.hw_microcortes),
                ("📡 Vigilar impresoras de red…", "Ping, puerto de impresión, cambios de MAC y cola de impresión durante un tiempo.",
                 self.hw_vigilar_impresoras)):
            fila = QHBoxLayout()
            b = piezas.boton(texto, "primary", funcion)
            b.setMinimumWidth(260)
            fila.addWidget(b)
            fila.addWidget(piezas.nota(explicacion), 1)
            lay.addLayout(fila)
        lay.addStretch(1)
        tabs.addTab(w, "🧰 Vigilancia y TLS")

    # ── común ──
    def _hw_abrir(self, clave, argumentos, carpeta, timeout=None, al_terminar=None, explicacion=""):
        dlg = EjecucionDialog(self, clave, argumentos, carpeta, timeout, al_terminar, explicacion)
        dlg.show()
        if not hasattr(self, "_hw_ventanas"):
            self._hw_ventanas = []
        self._hw_ventanas = [d for d in self._hw_ventanas if _vivo(d)] + [dlg]
        return dlg

    def _hw_historial(self, seccion, accion, objetivo):
        def _f(r):
            self._registrar_historial(seccion, accion, objetivo, (r.codigo == 0 and not r.parado),
                                      (r.salida or "")[-500:])
        return _f

    # ── 1. Auditoría de accesos ──
    def hw_auditoria_accesos(self):
        dlg = _Opciones(self, "🔎 Auditoría de accesos", "Lee el registro de Seguridad de este equipo (y sus copias "
                        "archivadas) y genera un informe HTML y un CSV. Solo lee.",
                        [("dias", "Días hacia atrás:", _spin(0, 3650, 30, " días (0 = todo)")),
                         ("sistema", "", QCheckBox("Incluir las cuentas del sistema (SYSTEM, servicios)")),
                         ("cierres", "", QCheckBox("Incluir los cierres de sesión"))])
        dlg.campos["cierres"].setChecked(True)
        if dlg.exec_() != QDialog.Accepted:
            return
        c = hw.carpeta_resultados("accesos")
        args = [("-Dias", dlg.campos["dias"].value()), ("-CarpetaResultados", c)]
        if dlg.campos["sistema"].isChecked():
            args.append("-IncluirSistema")
        if not dlg.campos["cierres"].isChecked():
            args.append("-SinCierres")
        self._hw_abrir("accesos", args, c)

    # ── 2. Aviso de RDP ──
    def hw_aviso_rdp(self):
        dlg = _Opciones(self, "🖧 Aviso de Escritorio remoto", "Al abrir un archivo .rdp, Escritorio remoto avisa de la "
                        "redirección de unidades y pide consentimiento. Aquí se ve su estado y se puede quitar o volver "
                        "a poner (para todos los usuarios con sesión; se guarda copia para restaurar).",
                        [("accion", "Acción:", _combo([("Ver el estado (no cambia nada)", "Estado"),
                                                       ("Quitar el aviso", "Desactivar"),
                                                       ("Restaurar el aviso de Windows", "Restaurar")]))])
        if dlg.exec_() != QDialog.Accepted:
            return
        accion = dlg.campos["accion"].currentData()
        if accion != "Estado" and not self._confirmar_accion(
                "Aviso de Escritorio remoto", f"¿{'Quitar' if accion == 'Desactivar' else 'Restaurar'} el aviso de "
                "Escritorio remoto para todos los usuarios con sesión en este equipo?"):
            return
        c = hw.carpeta_resultados("aviso_rdp")
        args = [("-Accion", accion), ("-CarpetaResultados", c)] + (["-SinConfirmar"] if accion != "Estado" else [])
        self._hw_abrir("aviso_rdp", args, c, al_terminar=None if accion == "Estado" else
                       self._hw_historial("🔒 Conexiones Remotas", f"Aviso de RDP: {accion}", "Escritorio remoto (.rdp)"))

    # ── 3. Claves de BitLocker ──
    def hw_bitlocker(self):
        dlg = _Opciones(self, "🔑 Claves de BitLocker", "Estado de cada unidad y, si se marca, sus contraseñas de "
                        "recuperación (48 dígitos). El CSV con claves es información MUY sensible: guárdalo en lugar "
                        "seguro. Solo lee: nunca activa ni cambia BitLocker.",
                        [("claves", "", QCheckBox("Mostrar también las contraseñas de recuperación"))])
        if dlg.exec_() != QDialog.Accepted:
            return
        c = hw.carpeta_resultados("bitlocker")
        args = [("-Carpeta", c)] + ([] if dlg.campos["claves"].isChecked() else ["-SoloEstado"])
        self._hw_abrir("bitlocker", args, c)

    # ── 4. Diagnóstico TLS ──
    def hw_tls(self):
        url = QLineEdit("https://")
        dlg = _Opciones(self, "🔐 Diagnóstico HTTPS / TLS", "Comprueba protocolos TLS del equipo y de .NET, el certificado "
                        "del servidor, el proxy y la conexión. «Arreglar» activa TLS 1.2 (cliente y .NET) con copia para "
                        "deshacer y pide reiniciar.",
                        [("url", "Dirección:", url), ("tiempo", "Tiempo máximo:", _spin(1, 120, 10, " s")),
                         ("arreglar", "", QCheckBox("Arreglar si falta TLS 1.2 (cambia el registro; con copia)"))])
        if dlg.exec_() != QDialog.Accepted:
            return
        destino = url.text().strip()
        if not destino or destino in ("https://", "http://"):
            self._avisar("Diagnóstico TLS", "Escribe la dirección del servidor (por ejemplo https://www.microsoft.com).")
            return
        arreglar = dlg.campos["arreglar"].isChecked()
        if arreglar and not self._confirmar_accion("Arreglar TLS", "Se activará TLS 1.2 en Windows y en .NET Framework "
                                                   "(registro, con copia para deshacer). Puede pedir reiniciar. ¿Seguir?"):
            return
        c = hw.carpeta_resultados("tls")
        args = [("-Url", destino), ("-TiempoSegundos", dlg.campos["tiempo"].value()), ("-CarpetaResultados", c)]
        if arreglar:
            args += ["-Arreglar", "-SinConfirmar"]
        self._hw_abrir("tls", args, c, al_terminar=self._hw_historial("🧪 Herramientas de red", "Arreglar TLS 1.2", destino)
                       if arreglar else None)

    # ── 5. Windows 11 ──
    def hw_windows11(self):
        c = hw.carpeta_resultados("win11")
        self._hw_abrir("win11", [("-Carpeta", c)], c, explicacion="Comprueba los requisitos de Windows 11. Solo lee.")

    # ── 6. Thunderbird ──
    def hw_thunderbird(self):
        dlg = _Opciones(self, "✉️ Exportar Thunderbird", "Se ejecuta con la sesión de este usuario. Con «Solo diagnóstico» "
                        "no copia nada. Thunderbird debe estar CERRADO para exportar.",
                        [("diag", "", QCheckBox("Solo diagnóstico (no exportar)")),
                         ("calendario", "Calendario:", _combo([("Exportar aproximado (CSV)", "Aproximado"),
                                                               ("Explicar cómo hacerlo a mano", "Manual"),
                                                               ("No exportarlo", "Saltar")])),
                         ("correo", "Correo local:", _combo([("Convertir a .eml para Outlook", "Convertir"),
                                                             ("Copiar sin convertir", "NoConvertir")]))])
        dlg.campos["diag"].setChecked(True)
        if dlg.exec_() != QDialog.Accepted:
            return
        c = hw.carpeta_resultados("thunderbird")
        args = [("-CarpetaSalida", c), "-SinConfirmar"]
        if dlg.campos["diag"].isChecked():
            args.append("-SoloDiagnostico")
        else:
            args += [("-Calendario", dlg.campos["calendario"].currentData()),
                     ("-CorreoLocal", dlg.campos["correo"].currentData())]
        self._hw_abrir("thunderbird", args, c)

    # ── 7. Instalar Office ──
    def hw_instalar_office(self):
        dlg = _Opciones(self, "📥 Instalar Microsoft 365 / Office", "Usa la herramienta oficial de Microsoft (ODT): descarga "
                        "setup.exe de Microsoft si falta (comprobando su firma) e instala sin preguntas. Tarda bastante y "
                        "necesita Internet. «Solo simular» enseña lo que haría sin instalar.",
                        [("producto", "Producto:", _combo([("Microsoft 365 Empresa (O365BusinessRetail)", "O365BusinessRetail"),
                                                           ("Microsoft 365 Apps para empresas grandes (O365ProPlusRetail)",
                                                            "O365ProPlusRetail"),
                                                           ("Office LTSC 2024 Standard (volumen)", "Standard2024Volume"),
                                                           ("Office LTSC 2021 Standard (volumen)", "Standard2021Volume")])),
                         ("arq", "Arquitectura:", _combo([("Automática", "Auto"), ("64 bits", "64"), ("32 bits", "32")])),
                         ("compartido", "", QCheckBox("Equipo compartido (activación por usuario, RDS)")),
                         ("simular", "", QCheckBox("Solo simular (no instala)"))])
        dlg.campos["simular"].setChecked(True)
        if dlg.exec_() != QDialog.Accepted:
            return
        simular = dlg.campos["simular"].isChecked()
        producto = dlg.campos["producto"].currentData()
        if not simular and not self._confirmar_accion("Instalar Office", f"¿Instalar {producto} en este equipo ahora?"):
            return
        c = hw.carpeta_resultados("office")
        canal = "PerpetualVL2024" if "2024" in producto else ("PerpetualVL2021" if "2021" in producto else "Current")
        args = [("-Producto", producto), ("-Arquitectura", dlg.campos["arq"].currentData()), ("-Canal", canal),
                ("-CarpetaTrabajo", c), "-SinConfirmar"]
        if dlg.campos["compartido"].isChecked():
            args.append("-EquipoCompartido")
        if simular:
            args.append("-Simular")
        self._hw_abrir("office", args, c, al_terminar=None if simular else
                       self._hw_historial("📦 Software Instalado", "Instalar Office (ODT)", producto))

    # ── 8. Microcortes ──
    def hw_microcortes(self):
        dlg = _Opciones(self, "📶 Vigilar microcortes de red", "Pings cada segundo a la puerta de enlace, al DNS y a "
                        "Internet, y consultas DNS. Al terminar (o al pulsar «Parar»), informe con cada corte y su causa.",
                        [("min", "Duración:", _spin(1, 10080, 10, " min")),
                         ("fallos", "Fallos seguidos para contar corte:", _spin(1, 30, 2))])
        if dlg.exec_() != QDialog.Accepted:
            return
        c = hw.carpeta_resultados("microcortes")
        self._hw_abrir("microcortes", [("-Minutos", dlg.campos["min"].value()),
                                       ("-FallosParaCorte", dlg.campos["fallos"].value()), ("-CarpetaResultados", c)], c)

    # ── 9. Impresoras ──
    def hw_vigilar_impresoras(self):
        dlg = _Opciones(self, "📡 Vigilar impresoras de red", "Toma las impresoras de red instaladas en este equipo (o las "
                        "IP que escribas) y vigila ping, puerto, MAC y cola.",
                        [("ips", "IP o nombres (opcional):", QLineEdit()),
                         ("modo", "Modo:", _combo([("Foto rápida (una pasada)", "una"), ("Vigilar durante un tiempo", "tiempo")])),
                         ("min", "Duración:", _spin(1, 10080, 30, " min"))])
        dlg.campos["ips"].setPlaceholderText("192.168.1.20, impresora-oficina  (vacío = las instaladas en Windows)")
        if dlg.exec_() != QDialog.Accepted:
            return
        c = hw.carpeta_resultados("impresoras")
        ips = dlg.campos["ips"].text().strip()
        args = [("-Impresoras", ips)] if ips else ["-DesdeWindows"]
        if dlg.campos["modo"].currentData() == "una":
            args.append("-UnaVez")
        else:
            args.append(("-Minutos", dlg.campos["min"].value()))
        self._hw_abrir("impresoras", args + [("-CarpetaResultados", c)], c)

    # ── 11. Microparadas de disco (portado de MADisk) ──
    def hw_microparadas(self):
        from msys.domain import microparadas_disco as md
        try:
            discos = md.listar_discos()
        except Exception as e:
            self._avisar("Microparadas", f"No se pudieron leer los discos:\n\n{e}")
            return
        if not discos:
            self._avisar("Microparadas", "No se encontró ningún disco físico.")
            return
        dlg = _Opciones(self, "⏱️ Microparadas del disco", "Para el equipo que se «congela» un instante sin errores en el "
                        "disco: lee el disco físico sin la caché de Windows y mide cada lectura. Solo lee. Conviene cerrar "
                        "programas pesados durante la prueba.",
                        [("disco", "Disco:", _combo([(f"Disco {d['indice']}: {d['modelo']} ({d['tamano_gb']} GB)", d)
                                                     for d in discos])),
                         ("patron", "Lectura:", _combo([("Aleatoria, bloques de 4 KB (recomendada)", "aleatoria"),
                                                        ("Secuencial, bloques de 1 MB", "secuencial")])),
                         ("seg", "Duración:", _combo([("10 s", 10), ("30 s", 30), ("1 min", 60), ("2 min", 120), ("5 min", 300)])),
                         ("umbral", "Parada a partir de:", _spin(1, 5000, 5, " ms"))])
        dlg.campos["seg"].setCurrentIndex(1)
        if dlg.exec_() != QDialog.Accepted:
            return
        d = dlg.campos["disco"].currentData()
        v = MicroparadasDialog(self, d["indice"], d["modelo"], dlg.campos["seg"].currentData(),
                               dlg.campos["patron"].currentData(), float(dlg.campos["umbral"].value()))
        v.show()
        self._hw_ventanas = [x for x in getattr(self, "_hw_ventanas", []) if _vivo(x)] + [v]

    # ── 10. Rendimiento ──
    def hw_rendimiento(self):
        dlg = _Opciones(self, "📈 Monitor de rendimiento", "«Capturar ahora» mide durante los minutos que digas y analiza al "
                        "terminar. «Instalar» deja una tarea programada que captura en segundo plano días seguidos (luego "
                        "«Analizar» saca el informe y «Desinstalar» la quita).",
                        [("modo", "Modo:", _combo([("Capturar ahora y analizar", "ahora"),
                                                   ("Analizar lo capturado por la tarea", "analizar"),
                                                   ("Ver el estado de la tarea", "estado"),
                                                   ("Instalar la captura en segundo plano (tarea programada)", "instalar"),
                                                   ("Desinstalar la tarea (conserva los datos)", "desinstalar")])),
                         ("min", "Duración (capturar ahora):", _spin(2, 1440, 15, " min")),
                         ("int", "Intervalo entre muestras:", _spin(2, 3600, 15, " s"))])
        if dlg.exec_() != QDialog.Accepted:
            return
        modo = dlg.campos["modo"].currentData()
        datos = hw.carpeta_resultados("rendimiento", nueva=False)
        datos = os.path.join(datos, "DATOS")
        os.makedirs(datos, exist_ok=True)
        c = hw.carpeta_resultados("rendimiento")
        if modo in ("instalar", "desinstalar") and not self._confirmar_accion(
                "Monitor de rendimiento", f"¿{'Crear' if modo == 'instalar' else 'Quitar'} la tarea programada de captura?"):
            return
        if modo == "ahora":
            args = ["-Capturar", ("-DuracionMinutos", dlg.campos["min"].value()),
                    ("-IntervaloSegundos", dlg.campos["int"].value()), ("-CarpetaDatos", datos)]
            # Al terminar (o al pulsar «Parar») se analiza lo capturado
            hecho = lambda r: self._hw_abrir("rendimiento", ["-Analizar", ("-CarpetaDatos", datos),  # noqa: E731
                                                             ("-CarpetaResultados", c)], c)
            self._hw_abrir("rendimiento", args, c, al_terminar=hecho,
                           explicacion="Al terminar la captura se abre el análisis.")
            return
        args = {"analizar": ["-Analizar", ("-CarpetaResultados", c)], "estado": ["-Estado"],
                "instalar": ["-Instalar", ("-IntervaloSegundos", dlg.campos["int"].value()), "-SinConfirmar"],
                "desinstalar": ["-Desinstalar", "-SinConfirmar"]}[modo] + [("-CarpetaDatos", datos)]
        self._hw_abrir("rendimiento", args, c, al_terminar=self._hw_historial(
            "💻 Sistema", f"Monitor de rendimiento: {modo}", "Tarea programada de captura")
            if modo in ("instalar", "desinstalar") else None)


class _HiloDisco(QThread):
    progreso = pyqtSignal(float, float, int)
    hecho = pyqtSignal(object, str)

    def __init__(self, disco, segundos, patron, umbral):
        super().__init__()
        self._a = (disco, segundos, patron, umbral)
        self._parar = False

    def run(self):
        from msys.domain import microparadas_disco as md
        disco, segundos, patron, umbral = self._a
        try:
            r = md.medir(disco, segundos, patron, umbral, al_progreso=self.progreso.emit, debe_parar=lambda: self._parar)
            self.hecho.emit(r, "")
        except Exception as e:
            self.hecho.emit(None, str(e))


class MicroparadasDialog(QDialog):
    def __init__(self, host, disco, nombre, segundos, patron, umbral):
        super().__init__(host)
        self.host, self.nombre, self.prueba = host, nombre, None
        self.setWindowTitle(f"⏱️ Microparadas · {nombre}")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.resize(820, 560)
        lay = QVBoxLayout(self)
        self.lbl = QLabel(f"🔄 Leyendo el disco ({'4 KB repartidos' if patron == 'aleatoria' else '1 MB seguidos'}, "
                          f"{segundos} s, parada ≥ {umbral:g} ms)… Solo lee.")
        self.lbl.setWordWrap(True)
        lay.addWidget(self.lbl)
        self.lbl_vivo = QLabel("")
        lay.addWidget(self.lbl_vivo)
        self.modelo = TablaSimple(["Segundo", "Duración (ms)", "Posición en el disco (MB)"])
        self.vista, self.proxy = piezas.tabla(self.modelo, self, columna_ancha=2)
        lay.addWidget(self.vista, 1)
        self.btn_parar = piezas.boton("⏹ Parar", "ghost", self._parar)
        self.btn_html = piezas.boton("🌐 Guardar informe…", "primary", self._informe)
        self.btn_html.setEnabled(False)
        lay.addLayout(piezas.fila(self.btn_parar, None, self.btn_html, piezas.boton("Cerrar", "ghost", self.close)))
        self.hilo = _HiloDisco(disco, segundos, patron, umbral)
        self.hilo.progreso.connect(lambda f, ms, n: self.lbl_vivo.setText(
            f"{f * 100:.0f} % · última lectura {ms:.2f} ms · paradas: {n}"))
        self.hilo.hecho.connect(self._hecho)
        self.hilo.start()

    def _parar(self):
        self.hilo._parar = True
        self.btn_parar.setEnabled(False)

    def _hecho(self, r, error):
        self.btn_parar.setEnabled(False)
        if r is None:
            self.lbl.setText(f"❌ {error}")
            return
        self.prueba = r
        self.lbl.setText(r.veredicto)
        self.lbl_vivo.setText(f"{r.operaciones} lecturas en {r.segundos:.0f} s · media {r.media_ms:.2f} ms · 99 % por "
                              f"debajo de {r.p99_ms:.2f} ms · máxima {r.maxima_ms:.2f} ms")
        self.modelo.set_datos([[f"{p.segundo:.2f}", f"{p.ms:.2f}", f"{p.posicion_mb:.1f}"] for p in r.paradas[:5000]])
        self.btn_html.setEnabled(True)

    def _informe(self):
        import time as _t
        from msys.core import informe_html
        from msys.core.utils import dialogo_guardar_archivo, ruta_guardado_predeterminada
        r = self.prueba
        ruta = dialogo_guardar_archivo(self, "Guardar informe", ruta_guardado_predeterminada(
            f"Microparadas_disco{r.disco}_{_t.strftime('%Y%m%d_%H%M%S')}.html"), "HTML (*.html)")
        if not ruta:
            return
        kpis = [informe_html.Kpi(r.operaciones, "Lecturas"), informe_html.Kpi(f"{r.media_ms:.2f} ms", "Media"),
                informe_html.Kpi(f"{r.maxima_ms:.1f} ms", "Máxima", "crit" if r.maxima_ms >= 200 else ""),
                informe_html.Kpi(len(r.paradas), "Paradas", "warn" if r.paradas else "ok")]
        sec = informe_html.Seccion(f"Paradas (≥ {r.umbral_ms:g} ms)", ["Segundo", "Duración (ms)", "Posición (MB)"],
                                   [[f"{p.segundo:.2f}", f"{p.ms:.2f}", f"{p.posicion_mb:.1f}"] for p in r.paradas],
                                   aviso=("info", r.veredicto))
        informe_html.guardar(ruta, informe_html.documento(f"⏱️ Microparadas del disco {r.disco}", [sec],
                                                          subtitulo=f"{self.nombre} · lectura {r.patron}", kpis=kpis))
        webbrowser.open(ruta)

    def closeEvent(self, e):
        if self.hilo.isRunning():
            self.hilo._parar = True
            self.hilo.wait(10000)
        super().closeEvent(e)


def _layout_que_contiene(lay, widget):
    if lay is None:
        return None
    if lay.indexOf(widget) >= 0:
        return lay
    for i in range(lay.count()):
        hijo = lay.itemAt(i).layout()
        if hijo is not None:
            r = _layout_que_contiene(hijo, widget)
            if r is not None:
                return r
    return None


def _vivo(w):
    try:
        from PyQt5 import sip
        return not sip.isdeleted(w)
    except Exception:
        return False
