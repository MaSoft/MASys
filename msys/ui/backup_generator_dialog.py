# -*- coding: utf-8 -*-
"""Diálogo genérico "Generar copia de seguridad" — base reutilizable para
TODOS los generadores de la pestaña 📀 Copias de Seguridad (msys/ui/
backups.py). Un generador concreto (Registro, Credenciales, Navegadores...)
solo aporta:
  - la lista de BackupComponente seleccionables (checklist, personalizable
    por el usuario — no todo o nada),
  - una `thread_factory(destino, claves_seleccionadas) -> QThread` que
    hace el trabajo real, con el mismo contrato de señales que el resto de
    hilos de reparación del proyecto (progress/finished_ok/finished_error,
    ver DriverBackupThread en collectors.py).

Destino por defecto: BACKUPS\\<nombre_subcarpeta>\\<fecha_hora> dentro de
msys_config.app_base_dir() — el usuario puede elegir otra carpeta con
'📂 Cambiar carpeta destino'. Se crea una subcarpeta con fecha/hora en
CUALQUIER destino (incluido uno elegido a mano) para no pisar un backup
anterior sin avisar."""

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable, List

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QProgressBar,
    QCheckBox, QMessageBox, QTextEdit, QScrollArea, QWidget, QFrame,
)

from msys import config as msys_config
from msys.core.utils import dialogo_elegir_carpeta


@dataclass
class BackupComponente:
    """Un elemento seleccionable dentro de un generador — p. ej.
    'HKLM\\Software' dentro de Registro de Windows, o un perfil concreto
    de navegador dentro de Navegadores."""
    clave: str
    etiqueta: str
    marcado_por_defecto: bool = True
    tooltip: str = ""


def carpeta_backups_base() -> str:
    """BACKUPS\\ junto al ejecutable/proyecto — mismo mecanismo que ya usa
    SCRIPTS\\ (msys_config.app_base_dir())."""
    carpeta = os.path.join(msys_config.app_base_dir(), "BACKUPS")
    os.makedirs(carpeta, exist_ok=True)
    return carpeta


def carpeta_destino_por_defecto(nombre_subcarpeta: str) -> str:
    return os.path.join(carpeta_backups_base(), nombre_subcarpeta)


class BackupGeneratorDialog(QDialog):
    def __init__(self, parent, titulo: str, icono: str, descripcion: str,
                 nombre_subcarpeta: str, componentes: List[BackupComponente],
                 thread_factory: Callable[[str, List[str]], "object"],
                 seccion_auditoria: str):
        super().__init__(parent)
        self.setWindowTitle(titulo)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(640, 560)
        self._thread = None
        self._titulo = titulo
        self._destino_base = carpeta_destino_por_defecto(nombre_subcarpeta)
        self._destino_final = ""
        self._componentes = componentes
        self._thread_factory = thread_factory
        self._seccion_auditoria = seccion_auditoria
        self._checks = {}

        c = (parent._dash_colors() if parent and hasattr(parent, "_dash_colors") else {
            "title": "#1a2634", "muted": "#8a94a6",
        })

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(22, 20, 22, 18)

        cabecera = QHBoxLayout()
        cabecera.setSpacing(12)
        lbl_icono = QLabel(icono)
        lbl_icono.setStyleSheet("font-size: 26pt; background: transparent; border: none;")
        cabecera.addWidget(lbl_icono)
        col_titulo = QVBoxLayout()
        col_titulo.setSpacing(2)
        lbl_titulo = QLabel(titulo)
        lbl_titulo.setStyleSheet(
            f"font-size: 13pt; font-weight: 600; color: {c['title']}; background: transparent; border: none;")
        col_titulo.addWidget(lbl_titulo)
        lbl_subtitulo = QLabel(descripcion)
        lbl_subtitulo.setWordWrap(True)
        lbl_subtitulo.setStyleSheet(f"color: {c['muted']}; font-size: 9.5pt; background: transparent; border: none;")
        col_titulo.addWidget(lbl_subtitulo)
        cabecera.addLayout(col_titulo, 1)
        layout.addLayout(cabecera)

        # ── Checklist de componentes (personalizable, no todo-o-nada) ──
        lbl_incluir = QLabel("Incluir en esta copia:")
        lbl_incluir.setStyleSheet(f"color: {c['muted']}; font-size: 9pt; font-weight: 600; background: transparent; border: none;")
        layout.addWidget(lbl_incluir)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setMaximumHeight(220)
        contenedor = QWidget()
        v_check = QVBoxLayout(contenedor)
        v_check.setSpacing(4)
        for comp in componentes:
            chk = QCheckBox(comp.etiqueta)
            chk.setChecked(comp.marcado_por_defecto)
            if comp.tooltip:
                chk.setToolTip(comp.tooltip)
            self._checks[comp.clave] = chk
            v_check.addWidget(chk)
        v_check.addStretch()
        scroll.setWidget(contenedor)
        layout.addWidget(scroll)

        fila_todo = QHBoxLayout()
        btn_marcar_todo = QPushButton("☑ Marcar todo")
        btn_marcar_todo.clicked.connect(lambda: self._marcar_todo(True))
        fila_todo.addWidget(btn_marcar_todo)
        btn_desmarcar_todo = QPushButton("☐ Desmarcar todo")
        btn_desmarcar_todo.clicked.connect(lambda: self._marcar_todo(False))
        fila_todo.addWidget(btn_desmarcar_todo)
        fila_todo.addStretch()
        layout.addLayout(fila_todo)

        # ── Carpeta destino ──
        fila_destino = QHBoxLayout()
        self.lbl_destino = QLabel(f"📁 Destino: {self._destino_base}")
        self.lbl_destino.setWordWrap(True)
        self.lbl_destino.setStyleSheet(f"color: {c['muted']}; font-size: 9pt; background: transparent; border: none;")
        fila_destino.addWidget(self.lbl_destino, 1)
        btn_cambiar_destino = QPushButton("📂 Cambiar carpeta destino")
        btn_cambiar_destino.clicked.connect(self._cambiar_destino)
        fila_destino.addWidget(btn_cambiar_destino)
        layout.addLayout(fila_destino)

        # ── Estado / progreso / log ──
        self.lbl_estado = QLabel("Listo para empezar.")
        self.lbl_estado.setObjectName("fieldLabel")
        layout.addWidget(self.lbl_estado)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(120)
        self.log.setVisible(False)
        layout.addWidget(self.log)

        # ── Botones ──
        botones = QHBoxLayout()
        botones.addStretch()
        self.btn_iniciar = QPushButton("💾 Iniciar backup")
        self.btn_iniciar.setObjectName("primary")
        self.btn_iniciar.clicked.connect(self._iniciar)
        botones.addWidget(self.btn_iniciar)
        self.btn_cerrar = QPushButton("Cerrar")
        self.btn_cerrar.clicked.connect(self._on_cerrar_clicked)
        botones.addWidget(self.btn_cerrar)
        layout.addLayout(botones)

    def _marcar_todo(self, marcado: bool):
        for chk in self._checks.values():
            chk.setChecked(marcado)

    def _cambiar_destino(self):
        elegido = dialogo_elegir_carpeta(self, "Elegir carpeta base del backup", self._destino_base)
        if elegido:
            self._destino_base = elegido
            self.lbl_destino.setText(f"📁 Destino: {self._destino_base}")

    def _claves_seleccionadas(self) -> List[str]:
        return [clave for clave, chk in self._checks.items() if chk.isChecked()]

    def _iniciar(self):
        claves = self._claves_seleccionadas()
        if not claves:
            QMessageBox.warning(self, "Nada seleccionado", "Marca al menos un elemento para incluir en la copia.")
            return

        # Subcarpeta con fecha/hora SIEMPRE, también si el destino es uno
        # elegido a mano — evita pisar un backup anterior sin avisar.
        marca_tiempo = time.strftime("%Y%m%d_%H%M%S")
        self._destino_final = os.path.join(self._destino_base, marca_tiempo)
        try:
            os.makedirs(self._destino_final, exist_ok=True)
        except OSError as e:
            QMessageBox.critical(self, "Error", f"No se pudo crear la carpeta destino:\n\n{e}")
            return

        self.btn_iniciar.setEnabled(False)
        for chk in self._checks.values():
            chk.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.log.setVisible(True)
        self.log.clear()
        self.lbl_estado.setText("🔄 Preparando...")

        self._thread = self._thread_factory(self._destino_final, claves)
        self._thread.progress.connect(self._on_progreso)
        self._thread.finished_ok.connect(self._on_finalizado_ok)
        self._thread.finished_error.connect(self._on_finalizado_error)
        self._thread.start()

    def _on_progreso(self, porcentaje, texto):
        self.progress.setValue(porcentaje)
        self.lbl_estado.setText(f"🔄 {texto}")
        self.log.append(texto)

    def _registrar_auditoria(self, exito: bool, detalle: str):
        parent = self.parent()
        if parent and hasattr(parent, "repair_audit_db"):
            parent.repair_audit_db.registrar(
                seccion=self._seccion_auditoria, accion=self._titulo,
                objetivo=self._destino_final, exito=exito, detalle=detalle,
                tipo_operacion="BACKUP",
            )

    def _on_finalizado_ok(self, destino):
        self.progress.setValue(100)
        self.lbl_estado.setText(f"✅ Backup completado en: {destino}")
        self._registrar_auditoria(True, "OK")

        parent = self.parent()
        if parent and hasattr(parent, "set_status"):
            parent.set_status(f"✅ Copia de seguridad generada en: {destino}")

        if QMessageBox.question(
            self, "Backup completado",
            f"Copia de seguridad completada en:\n\n{destino}\n\n¿Abrir la carpeta ahora?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes,
        ) == QMessageBox.Yes:
            try:
                if sys.platform == "win32":
                    os.startfile(destino)
                else:
                    subprocess.Popen(["xdg-open", destino])
            except OSError as e:
                QMessageBox.warning(self, "Aviso", f"No se pudo abrir la carpeta:\n\n{e}")

    def _on_finalizado_error(self, mensaje):
        self.progress.setVisible(False)
        self.lbl_estado.setText("❌ El backup no se pudo completar")
        self.log.append(f"ERROR: {mensaje}")
        self.btn_iniciar.setEnabled(True)
        for chk in self._checks.values():
            chk.setEnabled(True)
        self._registrar_auditoria(False, mensaje)
        QMessageBox.warning(self, "Con avisos", f"No se pudo completar el backup:\n\n{mensaje}")

    def _hilo_en_marcha(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def _confirmar_y_detener_si_hace_falta(self) -> bool:
        if not self._hilo_en_marcha():
            return True
        if QMessageBox.question(
            self, "Backup en curso",
            "El backup sigue en curso. Cerrar ahora no lo detiene, pero perderás la "
            "vista del progreso. ¿Cerrar de todas formas?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return False
        return True

    def _on_cerrar_clicked(self):
        if self._confirmar_y_detener_si_hace_falta():
            self.accept()

    def closeEvent(self, event):
        if self._confirmar_y_detener_si_hace_falta():
            event.accept()
        else:
            event.ignore()
