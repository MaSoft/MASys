# -*- coding: utf-8 -*-
"""Diálogo genérico "♻️ Restaurar" — reverso de BackupGeneratorDialog, para
TODAS las categorías de 📀 Copias de Seguridad que ofrecen un botón
Restaurar (msys/ui/backups.py). Dos modos, decididos por si se pasa
`restaurador` o no:

  - AUTO (restaurador != None): categorías donde la restauración es una
    reversión directa y sin ambigüedad del propio backup (reg import /
    copia de ficheros de vuelta a su carpeta de origen) — Registro de
    Windows, Configuración de MASys, Perfil de usuario. El técnico elige
    QUÉ backup anterior y QUÉ partes de él restaurar (checklist, igual que
    al generar), y `restaurador(carpeta_backup_elegida, claves) -> QThread`
    hace el trabajo real con el mismo contrato de señales que el resto de
    hilos del proyecto.

  - GUIADO (restaurador is None): categorías de mayor riesgo (Credenciales
    DPAPI, Navegadores con perfiles en uso, Configuración del sistema,
    Correo/Outlook, Nube) donde MASys NO escribe nada por sí solo —
    se elige el backup anterior y se muestra `guia_manual` (texto ya con
    las rutas reales de destino) + un botón para abrir esa carpeta, y el
    propio técnico copia lo que corresponda. No se registra en el
    Historial (la app no hizo ningún cambio real)."""

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QProgressBar,
    QCheckBox, QMessageBox, QTextEdit, QScrollArea, QWidget, QFrame, QComboBox,
)

from msys.ui.backup_generator_dialog import BackupComponente, carpeta_destino_por_defecto


# Categorías que cambiaron de nombre: sus copias antiguas siguen apareciendo
_NOMBRES_ANTERIORES = {"ConfiguracionMASys": ["ConfiguracionMultiSYS"]}  # 4.1.7: MultiSYS -> MASys


def _backups_disponibles(nombre_subcarpeta: str) -> List[str]:
    """Subcarpetas con marca de tiempo (YYYYMMDD_HHMMSS) dentro de
    BACKUPS\\<categoría>, más recientes primero — mismas que genera
    BackupGeneratorDialog._iniciar()."""
    subcarpetas = []
    for categoria in [nombre_subcarpeta] + _NOMBRES_ANTERIORES.get(nombre_subcarpeta, []):
        base = carpeta_destino_por_defecto(categoria)
        if os.path.isdir(base):
            subcarpetas += [os.path.join(base, nombre) for nombre in os.listdir(base)
                            if os.path.isdir(os.path.join(base, nombre))]
    subcarpetas.sort(key=os.path.getmtime, reverse=True)
    return subcarpetas


def _formatear_marca_tiempo(ruta_carpeta: str) -> str:
    nombre = os.path.basename(ruta_carpeta)
    try:
        dt = time.strptime(nombre, "%Y%m%d_%H%M%S")
        return time.strftime("%d/%m/%Y %H:%M:%S", dt)
    except ValueError:
        return nombre


class BackupRestoreDialog(QDialog):
    def __init__(self, parent, titulo: str, icono: str,
                 nombre_subcarpeta: str, componentes: List[BackupComponente],
                 seccion_auditoria: str,
                 restaurador: Optional[Callable[[str, List[str]], "object"]] = None,
                 guia_manual: str = ""):
        super().__init__(parent)
        self.setWindowTitle(f"♻️ Restaurar — {titulo}")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(640, 560)
        self._thread = None
        self._titulo = titulo
        self._componentes = componentes
        self._seccion_auditoria = seccion_auditoria
        self._restaurador = restaurador
        self._guia_manual = guia_manual
        self._checks = {}
        self._backups = _backups_disponibles(nombre_subcarpeta)

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
        lbl_titulo = QLabel(f"Restaurar — {titulo}")
        lbl_titulo.setStyleSheet(
            f"font-size: 13pt; font-weight: 600; color: {c['title']}; background: transparent; border: none;")
        col_titulo.addWidget(lbl_titulo)
        subt = ("Elige una copia anterior y qué partes de ella restaurar." if restaurador
                else "Elige una copia anterior. MASys no sobrescribe nada por su cuenta en "
                     "esta categoría — te guía para hacerlo tú mismo con seguridad.")
        lbl_subtitulo = QLabel(subt)
        lbl_subtitulo.setWordWrap(True)
        lbl_subtitulo.setStyleSheet(f"color: {c['muted']}; font-size: 9.5pt; background: transparent; border: none;")
        col_titulo.addWidget(lbl_subtitulo)
        cabecera.addLayout(col_titulo, 1)
        layout.addLayout(cabecera)

        # ── Selector de backup anterior ──
        lbl_elegir = QLabel("Copia de seguridad a restaurar:")
        lbl_elegir.setStyleSheet(f"color: {c['muted']}; font-size: 9pt; font-weight: 600; background: transparent; border: none;")
        layout.addWidget(lbl_elegir)

        self.combo_backups = QComboBox()
        if self._backups:
            for ruta in self._backups:
                self.combo_backups.addItem(_formatear_marca_tiempo(ruta), ruta)
        else:
            self.combo_backups.addItem("(no hay copias de seguridad previas de esta categoría)", "")
            self.combo_backups.setEnabled(False)
        layout.addWidget(self.combo_backups)

        if restaurador:
            # ── Checklist de componentes (igual que al generar) ──
            lbl_incluir = QLabel("Restaurar:")
            lbl_incluir.setStyleSheet(f"color: {c['muted']}; font-size: 9pt; font-weight: 600; background: transparent; border: none;")
            layout.addWidget(lbl_incluir)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setMaximumHeight(180)
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

            aviso = QLabel(
                "⚠️ Esto SOBRESCRIBE la configuración actual del equipo con la del backup elegido. "
                "Si algún elemento no está presente en esa copia, se avisa al terminar sin tocar nada de él.")
            aviso.setWordWrap(True)
            aviso.setStyleSheet("color: #ca8a04; font-size: 9pt; background: transparent; border: none;")
            layout.addWidget(aviso)
        else:
            # ── Modo guiado: solo texto + abrir carpeta ──
            self.txt_guia = QTextEdit()
            self.txt_guia.setReadOnly(True)
            self.txt_guia.setPlainText(guia_manual)
            self.txt_guia.setMinimumHeight(180)
            layout.addWidget(self.txt_guia, 1)

        # ── Estado / progreso / log (solo modo AUTO) ──
        self.lbl_estado = QLabel("Listo para empezar." if restaurador else "")
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
        if restaurador:
            self.btn_iniciar = QPushButton("♻️ Restaurar seleccionados")
            self.btn_iniciar.setObjectName("primary")
            self.btn_iniciar.setEnabled(bool(self._backups))
            self.btn_iniciar.clicked.connect(self._iniciar)
            botones.addWidget(self.btn_iniciar)
        else:
            self.btn_abrir = QPushButton("📂 Abrir carpeta de este backup")
            self.btn_abrir.setObjectName("primary")
            self.btn_abrir.setEnabled(bool(self._backups))
            self.btn_abrir.clicked.connect(self._abrir_carpeta_elegida)
            botones.addWidget(self.btn_abrir)
        self.btn_cerrar = QPushButton("Cerrar")
        self.btn_cerrar.clicked.connect(self._on_cerrar_clicked)
        botones.addWidget(self.btn_cerrar)
        layout.addLayout(botones)

    def _carpeta_elegida(self) -> str:
        return self.combo_backups.currentData() or ""

    def _abrir_carpeta_elegida(self):
        carpeta = self._carpeta_elegida()
        if not carpeta:
            return
        try:
            if sys.platform == "win32":
                os.startfile(carpeta)
            else:
                subprocess.Popen(["xdg-open", carpeta])
        except OSError as e:
            QMessageBox.warning(self, "Aviso", f"No se pudo abrir la carpeta:\n\n{e}")

    def _claves_seleccionadas(self) -> List[str]:
        return [clave for clave, chk in self._checks.items() if chk.isChecked()]

    def _iniciar(self):
        carpeta = self._carpeta_elegida()
        if not carpeta:
            return
        claves = self._claves_seleccionadas()
        if not claves:
            QMessageBox.warning(self, "Nada seleccionado", "Marca al menos un elemento para restaurar.")
            return

        if QMessageBox.question(
            self, "Confirmar restauración",
            f"Vas a restaurar {len(claves)} elemento(s) desde la copia del "
            f"{_formatear_marca_tiempo(carpeta)}, sobrescribiendo la configuración actual "
            "del equipo donde coincida. ¿Continuar?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        ) != QMessageBox.Yes:
            return

        self.btn_iniciar.setEnabled(False)
        self.combo_backups.setEnabled(False)
        for chk in self._checks.values():
            chk.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.log.setVisible(True)
        self.log.clear()
        self.lbl_estado.setText("🔄 Preparando...")
        self._carpeta_restaurada = carpeta

        self._thread = self._restaurador(carpeta, claves)
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
                objetivo=self._carpeta_restaurada, exito=exito, detalle=detalle,
                tipo_operacion="RESTORE",
            )

    def _on_finalizado_ok(self, carpeta):
        self.progress.setValue(100)
        self.lbl_estado.setText("✅ Restauración completada")
        self._registrar_auditoria(True, "OK")

        parent = self.parent()
        if parent and hasattr(parent, "set_status"):
            parent.set_status(f"✅ {self._titulo}: restauración completada desde {carpeta}")
        QMessageBox.information(self, "Restauración completada", f"Restauración de «{self._titulo}» completada.")
        self.btn_cerrar.setText("Cerrar")

    def _on_finalizado_error(self, mensaje):
        self.progress.setVisible(False)
        self.lbl_estado.setText("❌ La restauración no se pudo completar")
        self.log.append(f"ERROR: {mensaje}")
        self.log.setVisible(True)
        self.btn_iniciar.setEnabled(True)
        self.combo_backups.setEnabled(True)
        for chk in self._checks.values():
            chk.setEnabled(True)
        self._registrar_auditoria(False, mensaje)
        QMessageBox.warning(self, "Con avisos", f"No se pudo completar la restauración:\n\n{mensaje}")

    def _hilo_en_marcha(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def _confirmar_y_detener_si_hace_falta(self) -> bool:
        if not self._hilo_en_marcha():
            return True
        if QMessageBox.question(
            self, "Restauración en curso",
            "La restauración sigue en curso. Cerrar ahora no la detiene, pero perderás la "
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
