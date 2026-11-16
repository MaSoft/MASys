# -*- coding: utf-8 -*-
"""Ventana del agente de análisis (4.7.0), la que ve el cliente.

1. Pega el «código del técnico» (MSA1-…, solo archivo) o el «código de envío»
   (MSE1-…, 4.7.1: además se envía directamente al MASys del técnico).
2. Ve qué se va a leer (árbol por categorías, con casillas) y pulsa
   «Analizar y crear el archivo».
3. Al terminar, el archivo cifrado queda en su Escritorio para enviarlo.

Solo lee: no hay ningún botón que cambie el equipo. Las contraseñas y claves
se quitan antes de cifrar (msys/domain/agente.py · sanear)."""

import os
import subprocess
import threading
from typing import List, Optional

from PyQt5.QtCore import QEventLoop, QStandardPaths, Qt, QTimer
from PyQt5.QtGui import QGuiApplication, QIcon
from PyQt5.QtWidgets import (QDialog, QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPushButton,
                             QStackedWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from msys import config as msys_config
from msys.agente import recolector
from msys.domain import agente as ag
from msys.domain import agente_envio as envio
from msys.ui.piezas import CampoCodigo

_ROL = Qt.UserRole


def carpeta_escritorio() -> str:
    for ubicacion in (QStandardPaths.DesktopLocation, QStandardPaths.DocumentsLocation, QStandardPaths.HomeLocation):
        ruta = QStandardPaths.writableLocation(ubicacion)
        if ruta and os.path.isdir(ruta):
            return ruta
    return os.path.expanduser("~")


def guardar_archivo(contenido: bytes, nombre: str, carpeta: Optional[str] = None) -> str:
    carpeta = carpeta or carpeta_escritorio()
    ruta = os.path.join(carpeta, nombre)
    base, ext = os.path.splitext(ruta)
    n = 2
    while os.path.exists(ruta):
        ruta = f"{base} ({n}){ext}"
        n += 1
    with open(ruta, "wb") as f:
        f.write(contenido)
    return ruta


class VentanaAgente(QDialog):
    def __init__(self, es_admin: bool = True):
        super().__init__()
        from msys.version import APP_VERSION
        self.setWindowTitle(f"MASys · Agente de análisis {APP_VERSION}")
        self.setWindowFlags((self.windowFlags() | Qt.WindowMinimizeButtonHint) & ~Qt.WindowContextHelpButtonHint)
        try:
            from msys.ui.icono_app import dibujar_icono_app
            self.setWindowIcon(QIcon(dibujar_icono_app(64)))
        except Exception:
            pass
        self.setMinimumSize(640, 560)
        self._cancelar = False
        self._publica: Optional[bytes] = None
        self._destino = None        # (dirección, puerto, ticket) con un código de envío MSE1
        self.ruta_creada = ""

        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 16)
        lay.setSpacing(10)
        titulo = QLabel("Análisis del equipo para tu técnico")
        titulo.setStyleSheet("font-size: 14pt; font-weight: 600;")
        lay.addWidget(titulo)
        sub = QLabel("Solo lee la configuración de este equipo. No cambia nada ni incluye contraseñas.")
        sub.setObjectName("hintLabel")
        sub.setWordWrap(True)
        lay.addWidget(sub)

        self.paginas = QStackedWidget()
        lay.addWidget(self.paginas, 1)
        self.paginas.addWidget(self._pagina_inicio(es_admin))
        self.paginas.addWidget(self._pagina_progreso())
        self.paginas.addWidget(self._pagina_final())

    # ── Página 1: código y qué se lee ───────────────────────────────────
    def _pagina_inicio(self, es_admin: bool) -> QWidget:
        p = QWidget()
        lay = QVBoxLayout(p)
        lay.setContentsMargins(0, 6, 0, 0)
        lay.setSpacing(8)
        # Etiqueta y «Pegar» en una fila y el código debajo a todo el ancho: son ~67 caracteres y deben verse enteros
        fila = QHBoxLayout()
        fila.addWidget(QLabel("Código del técnico"))
        fila.addStretch(1)
        self.btn_pegar = QPushButton("📋 Pegar")
        self.btn_pegar.setObjectName("ghost")
        self.btn_pegar.clicked.connect(lambda _=False: self.txt_codigo.setText(QGuiApplication.clipboard().text().strip()))
        fila.addWidget(self.btn_pegar)
        lay.addLayout(fila)
        self.txt_codigo = CampoCodigo(lineas=3, placeholder="MSA1-… o MSE1-…")
        self.txt_codigo.textChanged.connect(self._validar_codigo)
        lay.addWidget(self.txt_codigo)
        self.lbl_codigo = QLabel("Pega el código que te ha enviado tu técnico.")
        self.lbl_codigo.setObjectName("hintLabel")
        lay.addWidget(self.lbl_codigo)

        lay.addWidget(QLabel("Qué se va a leer"))
        self.arbol = QTreeWidget()
        self.arbol.setHeaderHidden(True)
        self.arbol.setUniformRowHeights(True)
        self._llenar_arbol()
        lay.addWidget(self.arbol, 1)

        if not es_admin:
            aviso = QLabel("⚠️ Sin permiso de administrador faltarán datos. Ciérralo y ábrelo con «Ejecutar como administrador».")
            aviso.setWordWrap(True)
            aviso.setStyleSheet("color: #ca8a04;")
            lay.addWidget(aviso)

        botones = QHBoxLayout()
        botones.addStretch(1)
        self.btn_cerrar1 = QPushButton("Cerrar")
        self.btn_cerrar1.setObjectName("ghost")
        self.btn_cerrar1.clicked.connect(lambda _=False: self.reject())
        self.btn_analizar = QPushButton("Analizar y crear el archivo")
        self.btn_analizar.setObjectName("primary")
        self.btn_analizar.setEnabled(False)
        self.btn_analizar.clicked.connect(lambda _=False: self.analizar())
        botones.addWidget(self.btn_cerrar1)
        botones.addWidget(self.btn_analizar)
        lay.addLayout(botones)
        return p

    def _llenar_arbol(self):
        nodos = {}
        for clave_cat in msys_config.CATEGORY_ORDER:
            nodo = QTreeWidgetItem(self.arbol, [msys_config.CATEGORIES[clave_cat]["label"]])
            nodo.setFlags(nodo.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            nodo.setCheckState(0, Qt.Checked)
            nodos[clave_cat] = nodo
        for clave, titulo, seccion, _k, _a in ag.LECTORES:
            cat = msys_config.SECTION_CATEGORY.get(seccion, "administracion")
            hijo = QTreeWidgetItem(nodos.get(cat, nodos["administracion"]), [titulo])
            hijo.setData(0, _ROL, clave)
            hijo.setFlags(hijo.flags() | Qt.ItemIsUserCheckable)
            hijo.setCheckState(0, Qt.Checked)
        for nodo in list(nodos.values()):
            if nodo.childCount() == 0:
                self.arbol.takeTopLevelItem(self.arbol.indexOfTopLevelItem(nodo))
        self.arbol.itemClicked.connect(lambda it, _c: it.setExpanded(not it.isExpanded()) if it.childCount() else None)
        self.arbol.itemChanged.connect(lambda *_: self._validar_codigo())

    def elegidos(self) -> List[str]:
        res = []
        for i in range(self.arbol.topLevelItemCount()):
            nodo = self.arbol.topLevelItem(i)
            for j in range(nodo.childCount()):
                h = nodo.child(j)
                if h.checkState(0) == Qt.Checked:
                    res.append(h.data(0, _ROL))
        return res

    def _validar_codigo(self):
        texto = self.txt_codigo.text().strip()
        self._publica, self._destino = None, None
        if not texto:
            self.lbl_codigo.setText("Pega el código que te ha enviado tu técnico.")
            self.lbl_codigo.setStyleSheet("")
        else:
            try:
                # MSA1 = solo archivo; MSE1 (4.7.1) = además se envía directamente al técnico
                self._publica, self._destino = envio.leer_cualquier_codigo(texto)
                self.lbl_codigo.setText("Código correcto: se enviará directamente a tu técnico" if self._destino
                                        else "Código correcto")
                self.lbl_codigo.setStyleSheet("color: #16a34a; font-weight: 600; font-style: normal;")
            except ag.ErrorAgente as e:
                self.lbl_codigo.setText(str(e))
                self.lbl_codigo.setStyleSheet("color: #dc2626; font-weight: 600; font-style: normal;")
        self.btn_analizar.setText("Analizar y enviar" if self._destino else "Analizar y crear el archivo")
        self.btn_analizar.setEnabled(self._publica is not None and bool(self.elegidos()))

    # ── Página 2: progreso ──────────────────────────────────────────────
    def _pagina_progreso(self) -> QWidget:
        p = QWidget()
        lay = QVBoxLayout(p)
        lay.addStretch(1)
        self.lbl_paso = QLabel("Preparando…")
        self.lbl_paso.setAlignment(Qt.AlignCenter)
        self.lbl_paso.setStyleSheet("font-size: 11pt;")
        lay.addWidget(self.lbl_paso)
        self.barra = QProgressBar()
        lay.addWidget(self.barra)
        nota = QLabel("Puede tardar varios minutos. Puedes seguir usando el equipo.")
        nota.setObjectName("hintLabel")
        nota.setAlignment(Qt.AlignCenter)
        lay.addWidget(nota)
        lay.addStretch(1)
        fila = QHBoxLayout()
        fila.addStretch(1)
        self.btn_cancelar = QPushButton("Cancelar")
        self.btn_cancelar.setObjectName("ghost")
        self.btn_cancelar.clicked.connect(lambda _=False: self._pedir_cancelar())
        fila.addWidget(self.btn_cancelar)
        lay.addLayout(fila)
        return p

    def _pedir_cancelar(self):
        self._cancelar = True
        self.btn_cancelar.setEnabled(False)
        self.lbl_paso.setText("Cancelando…")

    def _progreso(self, hecho: int, total: int, titulo: str):
        self.barra.setMaximum(max(total, 1))
        self.barra.setValue(hecho)
        if titulo and not self._cancelar:
            self.lbl_paso.setText(f"Leyendo: {titulo}  ({hecho + 1} de {total})")

    # ── Página 3: resultado ─────────────────────────────────────────────
    def _pagina_final(self) -> QWidget:
        p = QWidget()
        lay = QVBoxLayout(p)
        lay.addStretch(1)
        self.lbl_final = QLabel("")
        self.lbl_final.setAlignment(Qt.AlignCenter)
        self.lbl_final.setWordWrap(True)
        self.lbl_final.setStyleSheet("font-size: 11pt;")
        lay.addWidget(self.lbl_final)
        self.lbl_ruta = QLabel("")
        self.lbl_ruta.setAlignment(Qt.AlignCenter)
        self.lbl_ruta.setWordWrap(True)
        self.lbl_ruta.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.lbl_ruta)
        self.lbl_fallos = QLabel("")
        self.lbl_fallos.setObjectName("hintLabel")
        self.lbl_fallos.setAlignment(Qt.AlignCenter)
        self.lbl_fallos.setWordWrap(True)
        lay.addWidget(self.lbl_fallos)
        lay.addStretch(1)
        fila = QHBoxLayout()
        fila.addStretch(1)
        self.btn_carpeta = QPushButton("📂 Abrir carpeta")
        self.btn_carpeta.setObjectName("ghost")
        self.btn_carpeta.clicked.connect(lambda _=False: self.abrir_carpeta())
        self.btn_cerrar2 = QPushButton("Cerrar")
        self.btn_cerrar2.setObjectName("primary")
        self.btn_cerrar2.clicked.connect(lambda _=False: self.accept())
        fila.addWidget(self.btn_carpeta)
        fila.addWidget(self.btn_cerrar2)
        lay.addLayout(fila)
        return p

    def abrir_carpeta(self):
        if self.ruta_creada and os.path.exists(self.ruta_creada):
            subprocess.Popen(["explorer", f"/select,{self.ruta_creada}"])

    # ── Analizar ────────────────────────────────────────────────────────
    def _enviar_en_segundo_plano(self, enviar, contenido: bytes) -> str:
        """Envía sin congelar la ventana. Devuelve "" si llegó o el motivo si no."""
        resultado = {"error": None}

        def trabajo():
            try:
                enviar(self._destino, contenido)
                resultado["error"] = ""
            except Exception as e:      # ag.ErrorAgente con motivo legible, o cualquier fallo de red
                resultado["error"] = str(e) or "Error desconocido"

        hilo = threading.Thread(target=trabajo, daemon=True)
        hilo.start()
        bucle = QEventLoop()
        reloj = QTimer()
        reloj.timeout.connect(lambda: bucle.quit() if not hilo.is_alive() else None)
        reloj.start(100)
        if hilo.is_alive():
            bucle.exec_()
        reloj.stop()
        hilo.join()
        return resultado["error"]

    def analizar(self, ejecutar=recolector.ejecutar_uno, carpeta: Optional[str] = None, enviar=None):
        if self._publica is None:
            return
        claves = self.elegidos()
        self._cancelar = False
        self.btn_cancelar.setEnabled(True)
        self.paginas.setCurrentIndex(1)
        informe = recolector.recolectar(claves, self._progreso, lambda: self._cancelar, ejecutar=ejecutar)
        if informe.get("cancelado"):
            self.paginas.setCurrentIndex(0)
            return
        fallos = [s["titulo"] for s in informe["secciones"].values() if s.get("error")]
        self.ruta_creada = ""
        try:
            contenido = ag.cifrar(informe, self._publica)
        except Exception as e:
            self._aviso(f"No se pudo cifrar el informe: {e}")
            self.paginas.setCurrentIndex(0)
            return
        motivo_envio = ""
        if self._destino:
            self.btn_cancelar.setEnabled(False)
            self.lbl_paso.setText("Enviando a tu técnico…")
            motivo_envio = self._enviar_en_segundo_plano(enviar or envio.enviar, contenido)
            if not motivo_envio:
                self.lbl_final.setText("✔ Enviado a tu técnico.\nYa puedes cerrar esta ventana.")
                self.lbl_ruta.setText("")
                self.btn_carpeta.setVisible(False)
                self._pintar_fallos(fallos)
                self.paginas.setCurrentIndex(2)
                return
        try:
            self.ruta_creada = guardar_archivo(contenido, ag.nombre_archivo(informe), carpeta)
        except Exception as e:
            self._aviso(f"No se pudo crear el archivo: {e}")
            self.paginas.setCurrentIndex(0)
            return
        donde = "en tu Escritorio" if os.path.dirname(self.ruta_creada) == carpeta_escritorio() else "en esta carpeta"
        if motivo_envio:
            self.lbl_final.setText(f"No se pudo enviar directamente: {motivo_envio}\n\nEl archivo se ha creado {donde}: "
                                   "envíaselo a tu técnico por correo o como te haya indicado.")
        else:
            self.lbl_final.setText(f"✔ Archivo creado {donde}.\nEnvíaselo a tu técnico por correo o como te haya indicado.")
        self.lbl_ruta.setText(self.ruta_creada)
        self.btn_carpeta.setVisible(True)
        self._pintar_fallos(fallos)
        self.paginas.setCurrentIndex(2)

    def _aviso(self, texto: str):
        caja = QMessageBox(self)
        caja.setWindowFlags(caja.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        caja.setIcon(QMessageBox.Warning)
        caja.setWindowTitle("Agente de análisis")
        caja.setText(texto)
        caja.exec_()

    def _pintar_fallos(self, fallos: List[str]):
        if not fallos:
            self.lbl_fallos.setText("")
        elif len(fallos) == 1:
            self.lbl_fallos.setText(f"No se pudo leer «{fallos[0]}»; el resto sí va incluido.")
        else:
            self.lbl_fallos.setText(f"No se pudieron leer {len(fallos)} apartados; el resto sí va incluido.")
