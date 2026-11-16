# -*- coding: utf-8 -*-
"""Diálogo "🔍 Captura forense" — tarjeta "Forense / auditoría" de 📀 Copias
de Seguridad (msys/ui/backups.py). A diferencia del resto de generadores
(BackupGeneratorDialog + QThread silencioso), este SÍ tiene una ventana
propia: integra las dos herramientas MAPERIT (copiadas tal cual desde el
proyecto MAPERIT, cada una con su propia consola en vivo, informe HTML y
cadena de custodia):

  - MaPeriT.SW (msys/tools/MaPeriT.SW.ps1): captura EN CALIENTE, sobre
    este mismo equipo mientras está en marcha (incluye RAM/procesos
    volátiles — Fase 1).
  - MaPeriT.OF (msys/tools/MaPeriT.OF.ps1): captura OFFLINE, de un disco
    ya conectado a este equipo pero SIN arrancar (p.ej. un disco extraído
    de otro equipo y montado como unidad adicional) — sin Fase 1, exige
    elegir la letra de unidad del disco objetivo (-TargetDisk) y admite
    limitar a un usuario concreto (-TargetUser).

MASys NO adivina el modo automáticamente: el usuario lo elige de forma
explícita, porque es una decisión pericial (qué se está adquiriendo y
cómo) que no debe inferirse solo por qué unidades hay montadas.

No se envuelve en el QThread genérico a propósito: MaPeriT ya tiene su
propia UI de consola rica (colores, progreso por fase, prerrequisitos) y
la captura en caliente requiere privilegios de administrador para el
snapshot VSS — intentar capturar esa salida y reconstruir una barra de
progreso genérica sería peor que dejarle su ventana. Se lanza en una
consola PowerShell elevada VISIBLE y aparte (win32api.ShellExecute, mismo
mecanismo que 🚀 Lanzar Scripts) — MASys no espera a que termine ni
conoce el resultado; el propio informe HTML de MaPeriT es la fuente de
verdad de esa ejecución."""

import os
import re
import string
import time

import win32api

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QCheckBox,
    QPushButton, QMessageBox, QScrollArea, QWidget, QFrame, QRadioButton,
    QButtonGroup, QComboBox,
)

from msys import config as msys_config
from msys.core.utils import dialogo_elegir_carpeta
from msys.ui.backup_generator_dialog import carpeta_destino_por_defecto

RUTA_SCRIPT_MAPERIT_SW = msys_config.ruta_recurso("msys", "tools", "MaPeriT.SW.ps1")
RUTA_SCRIPT_MAPERIT_OF = msys_config.ruta_recurso("msys", "tools", "MaPeriT.OF.ps1")

# número de fase MaPeriT -> (clave, etiqueta, marcado por defecto). Las
# fases ya cubiertas por otras categorías propias de MASys (Registro,
# Navegadores, Nube) se ofrecen pero SIN marcar, para no duplicar por
# defecto lo que ya se puede pedir por su cuenta con el resultado más
# ligero e integrado en la app. EventLogs/USB/Persistencia son las que
# de verdad aportan algo NUEVO frente al resto de MASys. La Fase 1
# (Volátil: RAM y procesos en vivo) solo existe en modo EN CALIENTE — un
# disco offline, sin arrancar, no tiene memoria volátil que capturar.
_FASE1_VOLATIL = (1, "volatil", "Memoria RAM y procesos en vivo (lento, requiere winpmem)", False)
_FASES_COMUNES = [
    (2, "systeminfo", "Información del sistema", False),
    (3, "filesystem", "Sistema de archivos (puede ser muy pesado)", False),
    (4, "registro", "Registro de Windows (forense — ya existe 'Registro de Windows' en esta pestaña)", False),
    (5, "eventlogs", "Registro de eventos completo (.evtx)", True),
    (6, "navegadores", "Navegadores (forense — ya existe 'Navegadores' en esta pestaña)", False),
    (7, "comunicaciones", "Clientes de comunicaciones", False),
    (8, "nube", "Almacenamiento en nube (forense — ya existe 'Nube' en esta pestaña)", False),
    (9, "usb", "Historial de dispositivos USB", True),
    (10, "persistencia", "Persistencia (autoruns, tareas, servicios, WMI)", True),
]


def _fases_para_modo(offline: bool):
    return list(_FASES_COMUNES) if offline else [_FASE1_VOLATIL] + list(_FASES_COMUNES)


def _discos_offline_disponibles():
    """Letras de unidad montadas ahora mismo, EXCLUYENDO la unidad de
    sistema de este equipo (esa es "en caliente", no un disco objetivo
    offline — nunca tiene sentido ofrecerla aquí)."""
    sistema = os.environ.get("SystemDrive", "C:").upper().rstrip("\\")
    discos = []
    for letra in string.ascii_uppercase:
        unidad = f"{letra}:"
        if unidad == sistema:
            continue
        if os.path.exists(f"{unidad}\\"):
            discos.append(unidad)
    return discos


class ForensicCaptureDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🔍 Captura forense (MAPERIT)")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(660, 640)
        self._destino_base = carpeta_destino_por_defecto("Forense")
        self._checks = {}

        c = (parent._dash_colors() if parent and hasattr(parent, "_dash_colors") else {
            "title": "#1a2634", "muted": "#8a94a6",
        })
        self._colores = c

        v = QVBoxLayout(self)
        v.setSpacing(14)
        v.setContentsMargins(22, 20, 22, 18)

        cabecera = QHBoxLayout()
        lbl_icono = QLabel("🔍")
        lbl_icono.setStyleSheet("font-size: 26pt; background: transparent; border: none;")
        cabecera.addWidget(lbl_icono)
        col_titulo = QVBoxLayout()
        col_titulo.setSpacing(2)
        lbl_titulo = QLabel("Captura forense (MAPERIT)")
        lbl_titulo.setStyleSheet(f"font-size: 13pt; font-weight: 600; color: {c['title']}; background: transparent; border: none;")
        col_titulo.addWidget(lbl_titulo)
        self.lbl_sub = QLabel()
        self.lbl_sub.setWordWrap(True)
        self.lbl_sub.setStyleSheet(f"color: {c['muted']}; font-size: 9.5pt; background: transparent; border: none;")
        col_titulo.addWidget(self.lbl_sub)
        cabecera.addLayout(col_titulo, 1)
        v.addLayout(cabecera)

        # ── Modo: en caliente vs. offline ──
        fila_modo = QHBoxLayout()
        lbl_modo = QLabel("Modo de captura:")
        lbl_modo.setStyleSheet(f"color: {c['muted']}; font-size: 9pt; font-weight: 600; background: transparent; border: none;")
        fila_modo.addWidget(lbl_modo)
        self.radio_en_caliente = QRadioButton("🔴 En caliente (este equipo, en marcha)")
        self.radio_offline = QRadioButton("💾 Offline (disco conectado, sin arrancar)")
        self.radio_en_caliente.setChecked(True)
        self._grupo_modo = QButtonGroup(self)
        self._grupo_modo.addButton(self.radio_en_caliente)
        self._grupo_modo.addButton(self.radio_offline)
        self.radio_en_caliente.toggled.connect(self._cambiar_modo)
        fila_modo.addWidget(self.radio_en_caliente)
        fila_modo.addWidget(self.radio_offline)
        fila_modo.addStretch()
        v.addLayout(fila_modo)

        self.lbl_aviso_script = QLabel()
        self.lbl_aviso_script.setWordWrap(True)
        self.lbl_aviso_script.setStyleSheet("color: #c0392b; font-size: 9pt; background: transparent; border: none;")
        v.addWidget(self.lbl_aviso_script)

        # ── Campos exclusivos de offline: disco objetivo + usuario ──
        self.widget_offline = QWidget()
        v_offline = QVBoxLayout(self.widget_offline)
        v_offline.setContentsMargins(0, 0, 0, 0)
        v_offline.setSpacing(6)

        fila_disco = QHBoxLayout()
        fila_disco.addWidget(QLabel("Disco objetivo:"))
        self.combo_disco = QComboBox()
        fila_disco.addWidget(self.combo_disco, 1)
        btn_refrescar_discos = QPushButton("🔄")
        btn_refrescar_discos.setToolTip("Actualizar lista de discos/unidades conectadas")
        btn_refrescar_discos.setFixedWidth(36)
        btn_refrescar_discos.clicked.connect(self._actualizar_lista_discos)
        fila_disco.addWidget(btn_refrescar_discos)
        v_offline.addLayout(fila_disco)

        fila_usuario = QHBoxLayout()
        fila_usuario.addWidget(QLabel("Usuario específico:"))
        self.txt_usuario_objetivo = QLineEdit()
        self.txt_usuario_objetivo.setPlaceholderText("Vacío = todos los perfiles detectados en el disco")
        fila_usuario.addWidget(self.txt_usuario_objetivo, 1)
        v_offline.addLayout(fila_usuario)

        v.addWidget(self.widget_offline)
        self._actualizar_lista_discos()

        # ── Caso / Perito ──
        fila_caso = QHBoxLayout()
        fila_caso.addWidget(QLabel("Nº de caso:"))
        self.txt_caso = QLineEdit(f"MASys_{os.environ.get('COMPUTERNAME', 'Equipo')}_{time.strftime('%Y%m%d_%H%M%S')}")
        fila_caso.addWidget(self.txt_caso, 1)
        v.addLayout(fila_caso)

        fila_perito = QHBoxLayout()
        fila_perito.addWidget(QLabel("Técnico/perito:"))
        self.txt_perito = QLineEdit(os.environ.get("USERNAME", "Técnico"))
        fila_perito.addWidget(self.txt_perito, 1)
        v.addLayout(fila_perito)

        # ── Checklist de fases (depende del modo) ──
        lbl_fases = QLabel("Fases a incluir:")
        lbl_fases.setStyleSheet(f"color: {c['muted']}; font-size: 9pt; font-weight: 600; background: transparent; border: none;")
        v.addWidget(lbl_fases)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setMaximumHeight(220)
        self._contenedor_fases = QWidget()
        self._v_check = QVBoxLayout(self._contenedor_fases)
        self._v_check.setSpacing(4)
        scroll.setWidget(self._contenedor_fases)
        v.addWidget(scroll)
        self._crear_checklist_fases()

        # ── Carpeta destino ──
        fila_destino = QHBoxLayout()
        self.lbl_destino = QLabel(f"📁 Destino: {self._destino_base}")
        self.lbl_destino.setWordWrap(True)
        self.lbl_destino.setStyleSheet(f"color: {c['muted']}; font-size: 9pt; background: transparent; border: none;")
        fila_destino.addWidget(self.lbl_destino, 1)
        btn_cambiar_destino = QPushButton("📂 Cambiar carpeta destino")
        btn_cambiar_destino.clicked.connect(self._cambiar_destino)
        fila_destino.addWidget(btn_cambiar_destino)
        v.addLayout(fila_destino)

        # ── Botones ──
        botones = QHBoxLayout()
        botones.addStretch()
        self.btn_iniciar = QPushButton("🚀 Iniciar captura forense (ventana nueva, elevada)")
        self.btn_iniciar.setObjectName("primary")
        self.btn_iniciar.clicked.connect(self._iniciar)
        botones.addWidget(self.btn_iniciar)
        btn_cerrar = QPushButton("Cerrar")
        btn_cerrar.clicked.connect(self.reject)
        botones.addWidget(btn_cerrar)
        v.addLayout(botones)

        self._cambiar_modo()

    # ── Modo en caliente / offline ────────────────────────────────────────

    def _modo_offline(self) -> bool:
        return self.radio_offline.isChecked()

    def _cambiar_modo(self):
        offline = self._modo_offline()
        self.widget_offline.setVisible(offline)
        if offline:
            self.lbl_sub.setText(
                "Analiza un disco YA CONECTADO a este equipo pero SIN ARRANCAR (p.ej. extraído de "
                "otro equipo). Solo lectura sobre el disco objetivo. Se abre en una CONSOLA PROPIA "
                "aparte — MASys no espera a que termine ni conoce el resultado.")
        else:
            self.lbl_sub.setText(
                "Captura EN CALIENTE sobre este mismo equipo, en marcha. Se abre en una CONSOLA "
                "PROPIA elevada (UAC) — MASys no espera a que termine ni conoce el resultado. "
                "Requiere administrador. Puede tardar varios minutos según las fases elegidas.")
        # La Fase 1 (Volátil) solo se muestra/oculta, nunca se destruye ni
        # se vuelve a crear: reconstruir el checklist entero en cada cambio
        # de modo (deleteLater() + addWidget() de golpe) dejaba el
        # QScrollArea con widgets a medio destruir mientras aún se podía
        # hacer scroll sobre él, distorsionando visualmente la lista al
        # subir/bajar. Con todas las casillas creadas UNA sola vez, cambiar
        # de modo es solo un setVisible().
        if hasattr(self, "_checks"):
            self._checks[1].setVisible(not offline)
        self._actualizar_aviso_script()

    def _actualizar_aviso_script(self):
        ruta = RUTA_SCRIPT_MAPERIT_OF if self._modo_offline() else RUTA_SCRIPT_MAPERIT_SW
        existe = os.path.isfile(ruta)
        self.lbl_aviso_script.setVisible(not existe)
        if not existe:
            self.lbl_aviso_script.setText(f"⚠️ No se encuentra el script en:\n{ruta}")
        self.btn_iniciar.setEnabled(existe)

    def _actualizar_lista_discos(self):
        actual = self.combo_disco.currentData() if self.combo_disco.count() else None
        self.combo_disco.clear()
        discos = _discos_offline_disponibles()
        if not discos:
            self.combo_disco.addItem("(ningún disco adicional detectado)", None)
            self.combo_disco.setEnabled(False)
        else:
            self.combo_disco.setEnabled(True)
            for unidad in discos:
                self.combo_disco.addItem(unidad, unidad)
            if actual:
                idx = self.combo_disco.findData(actual)
                if idx >= 0:
                    self.combo_disco.setCurrentIndex(idx)

    # ── Checklist de fases ──────────────────────────────────────────────

    def _crear_checklist_fases(self):
        """Crea TODAS las casillas (Fase 1 incluida) una única vez. Cambiar
        de modo solo oculta/muestra la Fase 1 (ver _cambiar_modo) — nunca
        se destruyen ni se vuelven a crear casillas."""
        self._checks = {}
        for numero, clave, etiqueta, marcado in [_FASE1_VOLATIL] + _FASES_COMUNES:
            chk = QCheckBox(f"Fase {numero}: {etiqueta}")
            chk.setChecked(marcado)
            self._checks[numero] = chk
            self._v_check.addWidget(chk)

    def _fases_a_omitir(self):
        # La Fase 1 sigue existiendo como casilla (solo oculta en modo
        # offline) — no contarla aquí cuando no aplica a este modo.
        aplicables = {numero for numero, *_ in _fases_para_modo(self._modo_offline())}
        return [numero for numero, chk in self._checks.items()
                if numero in aplicables and not chk.isChecked()]

    def _cambiar_destino(self):
        elegido = dialogo_elegir_carpeta(self, "Elegir carpeta base de la captura forense", self._destino_base)
        if elegido:
            self._destino_base = elegido
            self.lbl_destino.setText(f"📁 Destino: {self._destino_base}")

    def _iniciar(self):
        offline = self._modo_offline()
        ruta_script = RUTA_SCRIPT_MAPERIT_OF if offline else RUTA_SCRIPT_MAPERIT_SW
        if not os.path.isfile(ruta_script):
            QMessageBox.critical(self, "Error", f"No se encuentra el script en:\n{ruta_script}")
            return

        disco = None
        if offline:
            disco = self.combo_disco.currentData()
            if not disco:
                QMessageBox.warning(
                    self, "Sin disco objetivo",
                    "Selecciona el disco conectado que quieres analizar antes de iniciar la "
                    "captura offline (pulsa 🔄 si acabas de conectarlo).")
                return

        caso = re.sub(r'[\\/:*?"<>|]', "_", self.txt_caso.text().strip()) or "MASys_Backup"
        # perito/usuario_objetivo se interpolan sin comillado propio dentro
        # de argumentos ya envueltos en comillas dobles (línea de comandos
        # cruda vía ShellExecute) -- una comilla doble en cualquiera de los
        # dos rompería esa comilla y podría inyectar más flags en una
        # consola PowerShell elevada (runas). Mismo saneo que ya se aplica
        # a 'caso' en la línea de arriba.
        perito = re.sub(r'[\\/:*?"<>|]', "_", self.txt_perito.text().strip()) or "Tecnico"
        omitir = self._fases_a_omitir()
        total_fases = len(_fases_para_modo(offline))

        if len(omitir) == total_fases:
            QMessageBox.warning(self, "Nada seleccionado", "Marca al menos una fase para incluir en la captura.")
            return

        destino_final = os.path.join(self._destino_base, f"{caso}_{time.strftime('%Y%m%d_%H%M%S')}")
        try:
            os.makedirs(self._destino_base, exist_ok=True)
        except OSError as e:
            QMessageBox.critical(self, "Error", f"No se pudo crear la carpeta destino:\n\n{e}")
            return

        # SkipPhases se pasa como texto simple ("3,7"), NUNCA como sintaxis
        # de array @(...): invocado con -File (como aquí, vía ShellExecute
        # con una línea de comandos cruda), PowerShell no evalúa @() como
        # expresión -- lo recibe como texto literal y la conversión a
        # System.Int32[] falla (error reproducido y confirmado: "No se
        # puede convertir el valor "@()" al tipo "System.Int32""). El script
        # (MaPeriT.SW.ps1 / MaPeriT.OF.ps1) ahora parsea esta cadena él
        # mismo. Si no hay fases que omitir, se omite el flag entero
        # (el valor por defecto del script ya es "").
        skip_phases_arg = ""
        if omitir:
            skip_phases_arg = f' -SkipPhases "{",".join(str(n) for n in omitir)}"'

        if offline:
            usuario_objetivo = re.sub(r'[\\/:*?"<>|]', "_", self.txt_usuario_objetivo.text().strip())
            argumentos = (
                f'-NoExit -ExecutionPolicy Bypass -File "{ruta_script}" '
                f'-TargetDisk "{disco}" -CaseNumber "{caso}" -ExaminerName "{perito}" '
                f'-OutputPath "{destino_final}"{skip_phases_arg}'
            )
            if usuario_objetivo:
                argumentos += f' -TargetUser "{usuario_objetivo}"'
        else:
            argumentos = (
                f'-NoExit -ExecutionPolicy Bypass -File "{ruta_script}" '
                f'-CaseNumber "{caso}" -ExaminerName "{perito}" -OutputPath "{destino_final}"'
                f'{skip_phases_arg}'
            )

        try:
            win32api.ShellExecute(0, "runas", "powershell.exe", argumentos, None, 1)
            parent = self.parent()
            modo_txt = f"Offline (disco {disco})" if offline else "En caliente"
            if parent and hasattr(parent, "set_status"):
                parent.set_status(f"🔍 Captura forense ({modo_txt}) iniciada en consola aparte — destino: {destino_final}")
            if parent and hasattr(parent, "repair_audit_db"):
                parent.repair_audit_db.registrar(
                    seccion="📀 Copias de Seguridad", accion="Forense / auditoría",
                    objetivo=destino_final, exito=True,
                    detalle=f"Modo={modo_txt}; Caso={caso}; Perito={perito}; Fases omitidas={omitir or 'ninguna'}",
                    tipo_operacion="BACKUP",
                )
            QMessageBox.information(
                self, "Captura iniciada",
                f"Se ha abierto una consola de PowerShell aparte con la captura forense ({modo_txt}) en curso.\n\n"
                "Puedes cerrar esta ventana — MASys no necesita esperar a que termine. Sigue el "
                "progreso en la propia consola; al terminar generará su informe HTML en:\n\n"
                f"{destino_final}")
            self.accept()
        except Exception as e:
            QMessageBox.warning(self, "Error", f"No se pudo iniciar la captura forense:\n\n{e}")
