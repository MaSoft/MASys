# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: botón "🛠️ Soporte" de la barra inferior — punto
de entrada único a las reparaciones de la sección activa. Se actualiza
desde _actualizar_barra_exportacion() (ReportingMixin), así que aparece/
desaparece y refresca su menú exactamente cuando cambian el CSV/HTML/PDF.

Cómo añadir una sección nueva: añadir su entrada en
_registro_soporte_por_seccion() con (acciones_clasicas, detector).

- acciones_clasicas: lista de (etiqueta, callback) — siempre visibles,
  independientes de que haya nada que detectar.
- detector(): lista de (clave, etiqueta, callback) — clave es un
  identificador ESTABLE y único (no debe cambiar si solo cambia un
  contador o una fecha dentro de la etiqueta; para detectores que iteran
  entidades concretas, incluye el identificador real de la entidad, p. ej.
  f"disco_riesgo::{row.disco}") — se usa para poder "✅ Validar" el aviso
  (self._validar_aviso_soporte) y que deje de mostrarse hasta que se
  desvalide desde «📋 Avisos validados» (self._soporte_ver_avisos_validados).

El callback decide su propio flujo: confirmar+ejecutar+avisar in-line (como
los menús contextuales existentes) o abrir su propia ventana (necesario
para reparaciones largas con progreso, p. ej. SFC/DISM). Toda acción de
reparación real debe pasar por self._confirmar_reparacion(...) y terminar
registrando el resultado en self.repair_audit_db.registrar(...), tanto si
tuvo éxito como si falló."""

import os
import re
import csv
import json
import time
import subprocess
from datetime import datetime
from html import escape as html_escape
from typing import List

from PyQt5.QtWidgets import (
    QMenu, QMessageBox, QDialog, QVBoxLayout, QHBoxLayout, QDialogButtonBox,
    QTableWidget, QTableWidgetItem, QLabel, QPushButton, QFileDialog,
    QTextEdit, QApplication, QComboBox, QCheckBox, QFrame, QInputDialog,
)
from PyQt5.QtCore import Qt

from msys.core import informe_html
from msys.core.utils import (
    run_powershell_command_params, run_powershell_json, is_admin, get_informes_folder,
    startupinfo_oculto, dialogo_elegir_carpeta, ruta_guardado_predeterminada,
    dialogo_guardar_archivo, _SafeCsvWriter, run_powershell_command, escape_powershell_string,
    _get_software_blacklist, _is_blacklisted_software, _is_eol_software,
    _get_avisos_soporte_validados, _marcar_aviso_soporte_validado, _quitar_aviso_soporte_validado,
)
from msys.ps_scripts import PS_CHECK_MEMORY_DIAGNOSTICS, PS_COMPROBAR_PUERTO, PS_GET_NET_CONNECTIONS
from msys.ui.sfc_dism_dialog import SFCDISMDialog
from msys.ui.recycle_bin_dialog import RecycleBinDialog
from msys.ui.chkdsk_dialog import CHKDSKDialog
from msys.ui.system_cleanup_dialog import SystemCleanupDialog
from msys.ui.data_drive_cleanup_dialog import DataDriveCleanupDialog
from msys.ui.duplicate_files_dialog import DuplicateFilesDialog
from msys.ui.task_calendar_dialog import TaskCalendarDialog
from msys.widgets.delegates import _setup_header
from msys.domain.env_vars import (
    _PATH_LIKE_VARS, _expandir_valor_env, _broadcast_environment_change,
    _SUSPICIOUS_PATH_FRAGMENTS, _CRITICAL_SYSTEM_BINARIES,
)
from msys.models.table_models import _ENV_SCOPE_LABELS
from msys.models.rows import (
    _CRITICAL_PROCESSES, _is_windows_process, _proceso_disfrazado, _pseudo_proceso_con_ruta_falsa,
    _is_windows_service, _servicio_disfrazado, _ruta_servicio_sin_comillas, _ruta_servicio_corregida,
    STATUS_LABELS, _dispositivo_reinicio_permitido, _evento_critico_conocido,
)


class SoporteMixin:
    def _registro_soporte_por_seccion(self):
        """Único punto de verdad: nombre_sección -> (acciones_clasicas, detector).
        acciones_clasicas: lista [(etiqueta, callback), ...] fija para esa
        categoría. detector: callable sin argumentos que devuelve una lista
        [(etiqueta, callback), ...] calculada sobre los datos YA CARGADOS de
        la sección (problemas detectados ahora mismo), o None si esa sección
        todavía no tiene detector propio."""
        return {
            "💻 Sistema": (
                [
                    ("🔧 Reparar archivos de sistema (SFC/DISM)", self._soporte_sfc_dism),
                    ("💾 Crear punto de restauración", self._soporte_crear_punto_restauracion),
                    ("🧠 Analizar memoria", self._soporte_analizar_memoria),
                ],
                None,
            ),
            "🌐 Variables Entorno": (
                [
                    ("📜 Ver historial de cambios", self._soporte_ver_historial_env_vars),
                    ("💾 Backup completo del registro (.reg)", lambda: self._exportar_backup_reg(manual=True)),
                    ("📂 Restaurar backup .reg", self._soporte_restaurar_backup_reg),
                    ("🔄 Notificar cambio de entorno a procesos abiertos", self._soporte_notificar_cambio_entorno),
                ],
                lambda: (
                    self._detectar_soporte_env_vars()
                    + self._detectar_soporte_seguridad_path()
                    + self._detectar_soporte_solape_scopes()
                    + self._detectar_soporte_path_largo()
                ),
            ),
            "⚙️ Procesos": (
                [
                    ("🔁 Reiniciar Windows Explorer", self._soporte_reiniciar_explorer),
                    ("📋 Ver procesos marcados como legítimos", self._soporte_ver_procesos_whitelist),
                ],
                self._detectar_soporte_procesos_sospechosos,
            ),
            "🚀 Programas de Inicio": (
                [],
                self._detectar_soporte_programas_inicio,
            ),
            "🛠️ Servicios": (
                [
                    ("🖨️ Reiniciar Cola de impresión (Spooler)", self._soporte_reiniciar_spooler),
                    ("🔄 Reiniciar servicios de Windows Update", self._soporte_reiniciar_windows_update),
                    ("📋 Ver servicios marcados como legítimos", self._soporte_ver_servicios_whitelist),
                ],
                lambda: (
                    self._detectar_soporte_servicios_sospechosos()
                    + self._detectar_soporte_servicios_rutas_sin_comillas()
                    + self._detectar_soporte_servicios_atascados()
                ),
            ),
            "🔄 Actualizaciones": (
                [
                    ("🧹 Restablecer Windows Update", self._soporte_restablecer_windows_update),
                ],
                self._detectar_soporte_actualizaciones,
            ),
            "🖥️ Adm. Dispositivos": (
                [
                    ("💾 Backup completo de controladores", self._soporte_backup_controladores),
                    ("👻 Ver controladores ocultos", self._soporte_controladores_ocultos),
                ],
                # Genéricos/Antiguos NO entran aquí a propósito: son casi
                # universales en cualquier equipo real (informativo, no un
                # problema — ver sus propios docstrings) y ensuciaban el
                # botón Soporte con "avisos" que no lo son. Siguen
                # disponibles vía los checkboxes de la propia tabla.
                self._detectar_soporte_controladores,
            ),
            "🖨️ Impresoras": (
                [
                    ("🖨️ Reiniciar Cola de impresión (Spooler)", self._soporte_reiniciar_spooler),
                ],
                self._detectar_soporte_impresoras,
            ),
            "🔵 Bluetooth": (
                [],
                self._detectar_soporte_bluetooth,
            ),
            "🔌 Dispositivos USB": (
                [
                    ("🕘 Historial de almacenamiento USB", self._soporte_historial_usb),
                    ("🕵️ Detectar dispositivos USB sospechosos", self._soporte_usb_sospechosos),
                ],
                None,
            ),
            "📦 Software Instalado": (
                [],
                self._detectar_soporte_software,
            ),
            "⏰ Tareas Programadas": (
                [
                    ("📅 Calendario de ejecución", self._abrir_calendario_tareas),
                ],
                self._detectar_soporte_tareas,
            ),
            "📜 Historial PS": (
                [],
                self._detectar_soporte_ps_history,
            ),
            "⚠️ Eventos Sistema": (
                [],
                self._detectar_soporte_eventos_criticos,
            ),
            "💾 Unidades Remotas": (
                [],
                self._detectar_soporte_unidades_remotas,
            ),
            "📁 Contenido": (
                [
                    ("🗑️ Gestionar Papelera de Reciclaje", self._soporte_papelera_reciclaje),
                ],
                None,
            ),
            "💽 Discos Duros": (
                [
                    ("🔧 Verificar disco (CHKDSK)", self._soporte_verificar_disco),
                    ("🔍 Buscar archivos duplicados grandes (≥500 MB)", self._soporte_buscar_duplicados),
                ],
                self._detectar_soporte_discos,
            ),
            "📶 WiFi": (
                [],
                self._detectar_soporte_wifi,
            ),
            "🌐 Conexiones Red": (
                [],
                self._detectar_soporte_conexiones_red,
            ),
            "🔥 Firewall": (
                [
                    ("🔌 Comprobar puerto abierto/cerrado", self._soporte_comprobar_puerto),
                    ("🔍 Analizar puertos abiertos no comunes", self._soporte_analizar_puertos_no_comunes),
                    ("📋 Ver reglas de firewall aceptadas", self._soporte_ver_firewall_whitelist),
                    ("📋 Ver puertos comprobados", self._soporte_ver_puertos_whitelist),
                ],
                self._detectar_soporte_firewall,
            ),
            "🌐 Redes": (
                [
                    ("🧹 Vaciar caché DNS", self._soporte_vaciar_cache_dns),
                ],
                self._detectar_soporte_redes,
            ),
            "🔒 Conexiones Remotas": (
                [],
                self._detectar_soporte_conexiones_remotas,
            ),
            "🛡️ Seguridad": (
                [],
                self._detectar_soporte_seguridad,
            ),
            "🔐 Certificados": (
                [
                    ("📋 Ver certificados validados", self._soporte_ver_certs_sospechosos_whitelist),
                ],
                self._detectar_soporte_certificados,
            ),
            "🔑 Credenciales": (
                [],
                self._detectar_soporte_credenciales,
            ),
            "📧 Cuentas Correo": (
                [],
                self._detectar_soporte_email,
            ),
            "☁️ Navegadores y Nube": (
                [],
                self._detectar_soporte_navegadores_nube,
            ),
            "👥 Usuarios": (
                [
                    ("📋 Ver cuentas con contraseña en blanco confirmadas", self._soporte_ver_password_blanco_whitelist),
                ],
                self._detectar_soporte_usuarios,
            ),
            "🔍 Red Local": (
                [
                    ("📋 Ver equipos marcados como sospechosos", self._soporte_ver_sospechosos_red_local),
                ],
                self._detectar_soporte_red_local,
            ),
            "👤 Sesiones Activas": (
                [],
                self._detectar_soporte_sesiones_activas,
            ),
            # 📦 Gestor de Paquetes NO tiene entrada aquí a propósito: sus
            # únicas acciones ("Instalar todas", "ver bloqueadas") ya son un
            # botón y un checkbox visibles directamente en la pestaña — el
            # botón Soporte existe para sacar a la luz acciones que NO son
            # obvias desde la propia sección (ver SFC/DISM en 💻 Sistema),
            # no para duplicar controles que ya están a la vista. Sin
            # entrada, el botón Soporte se oculta solo en esta pestaña
            # (comportamiento correcto, no un hueco).
        }

    def _actualizar_boton_soporte(self):
        if not hasattr(self, 'btn_soporte'):
            return

        entrada = self._registro_soporte_por_seccion().get(getattr(self, '_current_section', None))
        if entrada is None:
            self.btn_soporte.setVisible(False)
            return

        acciones_clasicas, detector = entrada
        seccion_actual = getattr(self, '_current_section', '') or ''
        todas_detectadas = detector() if detector else []
        avisos_validados = _get_avisos_soporte_validados()
        # Cada aviso detectado trae su propia clave estable (ver comentario
        # de _registro_soporte_por_seccion y cada _detectar_soporte_*) — los
        # ya validados se descartan aquí, no vuelven a molestar hasta que
        # se desvaliden desde «📋 Avisos validados».
        acciones_detectadas = [t for t in todas_detectadas if t[0] not in avisos_validados]

        # Secciones sin acciones clásicas (p. ej. Eventos Sistema) dependen
        # por completo del detector — si no encuentra nada, no hay nada que
        # mostrar y el botón debe ocultarse, no aparecer con un menú vacío
        # (que es lo que pasaba antes de este fix).
        if not acciones_clasicas and not acciones_detectadas:
            self.btn_soporte.setVisible(False)
            return

        menu_anterior = self.btn_soporte.menu()
        menu = QMenu(self)
        for etiqueta, callback in acciones_clasicas:
            menu.addAction(etiqueta, callback)
        if acciones_detectadas:
            menu.addSeparator()
            for clave, etiqueta, callback in acciones_detectadas:
                menu.addAction(etiqueta, lambda checked=False, c=clave, e=etiqueta, s=seccion_actual, cb=callback:
                               self._ejecutar_aviso_soporte(c, e, s, cb))
        if avisos_validados:
            menu.addSeparator()
            menu.addAction("📋 Avisos validados...", self._soporte_ver_avisos_validados)

        self.btn_soporte.setMenu(menu)
        self.btn_soporte.setVisible(True)
        if menu_anterior is not None:
            menu_anterior.deleteLater()

    def _ejecutar_aviso_soporte(self, clave: str, etiqueta: str, seccion: str, callback):
        """Punto único de entrada para CUALQUIER aviso detectado de Soporte
        al pulsarlo desde el menú — deja el contexto (clave/etiqueta/
        sección) accesible en self._aviso_soporte_actual mientras dura la
        llamada al callback, para que _avisar()/_mostrar_informe()/
        _confirmar_reparacion() (multisys.py) y los diálogos de detalle que
        llaman a _agregar_boton_validar_aviso() puedan ofrecer el botón
        "✅ Validar" DENTRO de la propia ventana del aviso — nunca como una
        entrada aparte en el menú de Soporte (así se pedía)."""
        self._aviso_soporte_actual = (clave, etiqueta, seccion)
        try:
            callback()
        finally:
            self._aviso_soporte_actual = None

    def _validar_aviso_soporte(self, clave: str, etiqueta: str, seccion: str):
        """Marca un aviso detectado como validado (QSettings, ver
        core/utils.py) — deja de mostrarse en el menú de Soporte de
        cualquier sección hasta que se desvalide desde «📋 Avisos
        validados». No cambia nada en el sistema, solo silencia el aviso;
        el problema real (si lo hay) sigue existiendo."""
        _marcar_aviso_soporte_validado(clave, etiqueta, seccion)
        self.set_status("✅ Aviso validado — no volverá a aparecer hasta que lo desvalides en «📋 Avisos validados»")
        self._actualizar_boton_soporte()

    def _agregar_boton_validar_aviso(self, contenedor_botones, dialog, es_button_box: bool = False):
        """Añade el botón "✅ Validar" a la fila de botones de un diálogo de
        DETALLE de un aviso (tabla con problema, etc.) si el aviso actual
        viene de un detector (self._aviso_soporte_actual) — mismo criterio
        en todos los diálogos de detalle, para no repetir la lectura de
        contexto en cada uno. Al pulsarlo, valida y cierra el diálogo."""
        aviso = getattr(self, '_aviso_soporte_actual', None)
        if not aviso:
            return
        clave, etiqueta, seccion = aviso
        btn = QPushButton("✅ Validar (dejar de mostrar este aviso)")

        def _click():
            self._validar_aviso_soporte(clave, etiqueta, seccion)
            dialog.accept()
        btn.clicked.connect(_click)

        if es_button_box:
            contenedor_botones.addButton(btn, QDialogButtonBox.ActionRole)
        else:
            contenedor_botones.addWidget(btn)

    def _soporte_ver_avisos_validados(self):
        """Lista los avisos validados (snapshot de etiqueta/sección/fecha
        tomado al validar, no se vuelve a ejecutar ningún detector) con
        acción para desvalidar — el aviso desvalidado vuelve a evaluarse
        con datos frescos la próxima vez que se abra Soporte en su
        sección, no con el texto guardado aquí."""
        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle("Avisos validados")
        dialog.setMinimumSize(760, 420)
        v = QVBoxLayout(dialog)

        v.addWidget(QLabel(
            "Avisos que has marcado como revisados — ya no aparecen en el menú de Soporte. "
            "Desvalida el que quieras que vuelva a mostrarse (con datos frescos, no con el texto de aquí)."))

        tabla = QTableWidget(0, 3)
        tabla.setHorizontalHeaderLabels(["Sección", "Aviso", "Validado el"])
        tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        tabla.setSelectionBehavior(QTableWidget.SelectRows)
        tabla.setSelectionMode(QTableWidget.SingleSelection)
        tabla.verticalHeader().setVisible(False)
        _setup_header(tabla, 3, stretch_col=1)
        v.addWidget(tabla)

        lbl_vacio = QLabel("No hay avisos validados.")
        lbl_vacio.setVisible(False)
        v.addWidget(lbl_vacio)

        def _recargar():
            datos = _get_avisos_soporte_validados()
            tabla.setSortingEnabled(False)
            tabla.setRowCount(len(datos))
            for fila, (clave, info) in enumerate(datos.items()):
                item_seccion = QTableWidgetItem(info.get("seccion", ""))
                item_seccion.setData(Qt.UserRole, clave)
                tabla.setItem(fila, 0, item_seccion)
                tabla.setItem(fila, 1, QTableWidgetItem(info.get("etiqueta", "")))
                tabla.setItem(fila, 2, QTableWidgetItem(info.get("fecha", "")))
            tabla.setSortingEnabled(True)
            tabla.model().layoutChanged.emit()
            lbl_vacio.setVisible(not datos)
            btn_desvalidar.setEnabled(False)

        fila_botones = QHBoxLayout()
        btn_desvalidar = QPushButton("↩️ Desvalidar seleccionado")
        btn_desvalidar.setEnabled(False)
        fila_botones.addWidget(btn_desvalidar)
        fila_botones.addStretch()
        v.addLayout(fila_botones)

        def _on_seleccion():
            btn_desvalidar.setEnabled(bool(tabla.selectionModel().selectedRows()))

        def _on_desvalidar():
            filas_sel = tabla.selectionModel().selectedRows()
            if not filas_sel:
                return
            clave = tabla.item(filas_sel[0].row(), 0).data(Qt.UserRole)
            _quitar_aviso_soporte_validado(clave)
            _recargar()
            self._actualizar_boton_soporte()

        tabla.itemSelectionChanged.connect(_on_seleccion)
        btn_desvalidar.clicked.connect(_on_desvalidar)

        _recargar()

        botones = QDialogButtonBox(QDialogButtonBox.Close)
        botones.rejected.connect(dialog.reject)
        botones.accepted.connect(dialog.accept)
        v.addWidget(botones)
        dialog.exec_()

    def _soporte_crear_punto_restauracion(self):
        """Crea un punto de restauración de Windows. Acción de baja
        complejidad, usada para validar de extremo a extremo el flujo
        confirmar → ejecutar → auditar antes de abordar reparaciones más
        largas (SFC/DISM)."""
        if not self._confirmar_reparacion(
            "Crear punto de restauración",
            cambios=["Crear un punto de restauración de Windows con la fecha y hora actuales"],
            reversible=True,
        ):
            return

        descripcion = f"MASys {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        ok, salida_o_error = run_powershell_command_params(
            "Checkpoint-Computer -Description '{descripcion}' -RestorePointType MODIFY_SETTINGS",
            timeout=120,
            descripcion=descripcion,
        )

        self.repair_audit_db.registrar(
            seccion="Sistema", accion="Crear punto de restauración",
            objetivo="Sistema completo", exito=ok,
            detalle="OK" if ok else salida_o_error,
        )

        if ok:
            self._avisar(
                "Punto de restauración",
                "Punto de restauración creado correctamente.\n\n"
                "Nota: Windows limita la creación de puntos de restauración a "
                "uno cada 24 horas — si ya se creó uno hoy, esta operación no "
                "habrá creado uno nuevo aunque no dé ningún error."
            )
        else:
            self._avisar("No se pudo crear el punto de restauración", f"{salida_o_error}\n\nComprueba que la Protección del sistema "
                "esté activada para la unidad C: (Propiedades del sistema → "
                "Protección del sistema).", QMessageBox.Warning)

    def _soporte_sfc_dism(self):
        """Verifica y repara archivos de sistema con DISM /RestoreHealth +
        SFC /scannow. Acción larga (10-30 min) con su propia ventana de
        progreso (SFCDISMDialog) — a diferencia de las demás acciones de
        soporte, que son de confirmar+ejecutar+avisar in-line."""
        if not is_admin():
            self._avisar(
                "Reparar archivos de sistema",
                "Se requieren privilegios de administrador para ejecutar "
                "DISM/SFC.\n\nReinicia MASys como administrador.",
                QMessageBox.Warning,
            )
            return

        if not self._confirmar_reparacion(
            "Reparar archivos de sistema (SFC/DISM)",
            cambios=[
                "Ejecutar DISM /Online /Cleanup-Image /RestoreHealth (repara la imagen de Windows)",
                "Ejecutar SFC /scannow (verifica y repara archivos de sistema protegidos)",
                "Duración estimada: 10-30 minutos, con una ventana de progreso en vivo",
            ],
            reversible=False,
        ):
            return

        dialog = SFCDISMDialog(self)
        dialog.exec_()

        # resultado_exito queda en None si se cerró sin llegar a terminar
        # (p. ej. detenido a medias) — se registra igual, como fallo, para
        # que quede constancia de que se intentó.
        exito = bool(dialog.resultado_exito)
        self.repair_audit_db.registrar(
            seccion="Sistema", accion="Reparar archivos de sistema (SFC/DISM)",
            objetivo="Sistema completo", exito=exito,
            detalle=dialog.resultado_log or "Detenido antes de finalizar",
        )

    def _soporte_analizar_memoria(self):
        """Comprueba, SIN reiniciar, lo que ya se sabe sobre la memoria:
        errores de hardware ya detectados (WHEA) y el resultado del último
        Diagnóstico de memoria de Windows si se ejecutó alguna vez. Una
        prueba nueva completa solo puede hacerse reiniciando — eso lo
        ofrece mdsched.exe, la herramienta nativa de Windows, con su propio
        diálogo; aquí no se intenta automatizar ni silenciar ese paso."""
        data = run_powershell_json(PS_CHECK_MEMORY_DIAGNOSTICS, timeout=20)
        errores = (data or {}).get("ErroresHardware") or []
        ultimo = (data or {}).get("UltimoDiagnostico")

        if errores:
            informe = f"⚠️ {len(errores)} error(es) de hardware de memoria en los últimos 90 días (registro WHEA):\n"
            informe += "\n".join(f"  • {e.get('Fecha', 'N/A')}" for e in errores[:10])
        else:
            informe = "✅ No hay errores de hardware de memoria registrados en los últimos 90 días."

        if ultimo:
            informe += f"\n\nÚltimo Diagnóstico de memoria de Windows: {ultimo.get('Fecha', 'N/A')}\n{ultimo.get('Mensaje', '')}"
        else:
            informe += "\n\nNunca se ha ejecutado el Diagnóstico de memoria de Windows en este equipo."

        if not self._confirmar_reparacion(
            "Analizar memoria", explicacion=informe, es_alerta=bool(errores),
            cambios=["Programar el Diagnóstico de memoria de Windows (mdsched.exe) "
                     "para la próxima vez que se reinicie el equipo"],
            reversible=True,
        ):
            return

        try:
            subprocess.Popen(["mdsched.exe"])
            self.repair_audit_db.registrar(
                seccion="Sistema", accion="Analizar memoria",
                objetivo="Sistema completo", exito=True,
                detalle="Diagnóstico de memoria de Windows lanzado (mdsched.exe); "
                        "el resultado real solo se sabrá tras el reinicio.",
            )
        except Exception as e:
            self._avisar("Error", f"No se pudo lanzar el Diagnóstico de memoria de Windows:\n\n{e}", QMessageBox.Warning)

    # ── Variables de Entorno ────────────────────────────────────────────

    def _soporte_ver_historial_env_vars(self):
        """Muestra el historial de crear/editar/eliminar/buscar-reemplazar
        ya registrado por EnvVarLogDB (incluye el valor anterior) con un
        botón para restaurar la entrada seleccionada — antes se escribía
        pero no había forma ni de consultarlo ni de aplicarlo de vuelta."""
        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle("Historial de cambios — Variables de entorno")
        dialog.setMinimumSize(780, 440)
        v = QVBoxLayout(dialog)

        tabla = QTableWidget(0, 6)
        tabla.setHorizontalHeaderLabels(
            ["Variable", "Ámbito", "Acción", "Valor anterior", "Valor nuevo", "Fecha"])
        tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        tabla.setSelectionBehavior(QTableWidget.SelectRows)
        tabla.setSelectionMode(QTableWidget.SingleSelection)
        tabla.verticalHeader().setVisible(False)
        _setup_header(tabla, 6, stretch_col=4)
        v.addWidget(tabla)

        lbl_vacio = QLabel("Todavía no se ha registrado ningún cambio desde MASys.")
        lbl_vacio.setVisible(False)
        v.addWidget(lbl_vacio)

        def _recargar():
            entradas = self.env_var_log_db.get_entries(limit=500)
            # Ordenar por columna reordena filas — se guarda cada entrada
            # completa en Qt.UserRole de su propia fila (columna 0) en vez
            # de indexar por posición de fila, para no restaurar la entrada
            # equivocada después de que el usuario haya ordenado la tabla.
            tabla.setSortingEnabled(False)
            tabla.setRowCount(len(entradas))
            for fila, entrada in enumerate(entradas):
                nombre, scope, accion, val_anterior, val_nuevo, timestamp = entrada
                valores = [nombre, _ENV_SCOPE_LABELS.get(scope, scope), accion, val_anterior, val_nuevo, timestamp]
                for col, valor in enumerate(valores):
                    item = QTableWidgetItem(valor)
                    if col == 0:
                        item.setData(Qt.UserRole, entrada)
                    tabla.setItem(fila, col, item)
            tabla.setSortingEnabled(True)
            tabla.model().layoutChanged.emit()
            lbl_vacio.setVisible(not entradas)
            btn_restaurar.setEnabled(False)

        fila_botones = QHBoxLayout()
        btn_restaurar = QPushButton("↩️ Restaurar valor anterior de la selección")
        btn_restaurar.setEnabled(False)
        fila_botones.addWidget(btn_restaurar)
        fila_botones.addStretch()
        v.addLayout(fila_botones)

        def _on_seleccion():
            btn_restaurar.setEnabled(bool(tabla.selectionModel().selectedRows()))

        def _on_restaurar():
            filas_sel = tabla.selectionModel().selectedRows()
            if not filas_sel:
                return
            nombre, scope, accion, val_anterior, _val_nuevo, _timestamp = (
                tabla.item(filas_sel[0].row(), 0).data(Qt.UserRole))
            if self._restaurar_entrada_historial(nombre, scope, accion, val_anterior):
                _recargar()

        tabla.itemSelectionChanged.connect(_on_seleccion)
        btn_restaurar.clicked.connect(_on_restaurar)

        _recargar()

        botones = QDialogButtonBox(QDialogButtonBox.Close)
        botones.rejected.connect(dialog.reject)
        botones.accepted.connect(dialog.accept)
        v.addWidget(botones)
        dialog.exec_()

    def _restaurar_entrada_historial(self, nombre: str, scope: str, accion: str, valor_anterior: str) -> bool:
        """Aplica de vuelta el 'valor_anterior' de una entrada del historial.
        Caso especial: si la entrada original fue una CREACIÓN, "restaurar"
        significa eliminar la variable (antes de esa acción no existía),
        no ponerle un valor vacío."""
        if scope == "system" and not is_admin():
            self._avisar("Se requieren privilegios de administrador",
                f"Restaurar «{nombre}» en el ámbito Sistema requiere ejecutar MASys como administrador.",
                QMessageBox.Warning)
            return False

        ambito = _ENV_SCOPE_LABELS.get(scope, scope)
        es_deshacer_creacion = (accion == "crear")

        if es_deshacer_creacion:
            cambios = [f"Eliminar «{nombre}» ({ambito}) — antes de esa acción la variable no existía"]
        else:
            cambios = [f"Restaurar «{nombre}» ({ambito}) a su valor anterior:", valor_anterior or "(vacío)"]

        if not self._confirmar_reparacion(
            f"Restaurar {nombre}", cambios=cambios, reversible=True,
            objetivo=f"{nombre} ({ambito})",
        ):
            return False
        if not self._ofrecer_backup_si_hace_falta():
            return False

        if es_deshacer_creacion:
            ok, msg = self.var_manager.delete_value(nombre, scope)
            accion_log = "restaurar_eliminar"
        else:
            ok, msg = self.var_manager.set_value(nombre, valor_anterior, scope)
            accion_log = "restaurar"

        if ok:
            self.env_var_log_db.add_entry(nombre, scope, accion_log, "", valor_anterior)
            self.repair_audit_db.registrar(
                seccion="🌐 Variables Entorno", accion=f"Restaurar {nombre}",
                objetivo=f"{nombre} ({ambito})", exito=True,
                detalle="Variable eliminada (deshacer creación)" if es_deshacer_creacion
                        else "Valor anterior restaurado",
                backup_realizado=self._env_var_backup_hecho,
            )
            self.set_status(f"✅ «{nombre}» restaurada")
            if hasattr(self, 'refresh_environment_vars'):
                self.refresh_environment_vars()
            return True
        else:
            self.repair_audit_db.registrar(
                seccion="🌐 Variables Entorno", accion=f"Restaurar {nombre}",
                objetivo=f"{nombre} ({ambito})", exito=False, detalle=msg,
            )
            self._avisar("Error", f"No se pudo restaurar la variable:\n{msg}", QMessageBox.Warning)
            return False

    def _soporte_restaurar_backup_reg(self):
        """Importa un backup .reg generado previamente (por MASys o por
        el propio Windows) sin salir de la app — hasta ahora la única forma
        era buscar el archivo en el explorador y hacer doble clic.

        Se construye el QFileDialog como instancia (en vez de usar el
        método estático getOpenFileName) para poder quitarle el botón "?"
        de ayuda antes de mostrarlo, igual que en cualquier otro QDialog
        de la app — el método estático no da esa oportunidad porque crea
        y muestra la ventana de una sola vez."""
        dialogo = QFileDialog(self, "Seleccionar backup de registro",
                               get_informes_folder(), "Archivo de registro (*.reg)")
        dialogo.setWindowFlags(dialogo.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialogo.setOption(QFileDialog.DontUseNativeDialog, True)
        dialogo.setFileMode(QFileDialog.ExistingFile)
        dialogo.setAcceptMode(QFileDialog.AcceptOpen)
        if dialogo.exec_() != QDialog.Accepted:
            return
        archivos = dialogo.selectedFiles()
        path = archivos[0] if archivos else ""
        if not path:
            return

        if not self._confirmar_reparacion(
            "Restaurar backup de registro",
            cambios=[
                f"Importar «{os.path.basename(path)}» en el registro (reg import)",
                "Esto SOBRESCRIBE cualquier variable de entorno cuyo nombre coincida con las del backup",
            ],
            reversible=False,
        ):
            return

        try:
            resultado = subprocess.run(
                ["reg", "import", path], capture_output=True, timeout=30,
                startupinfo=startupinfo_oculto(), creationflags=subprocess.CREATE_NO_WINDOW)
            ok = resultado.returncode == 0
            if ok:
                detalle = "OK"
            else:
                stderr = resultado.stderr.decode("oem", errors="replace") if resultado.stderr else ""
                detalle = stderr or f"reg import devolvió el código {resultado.returncode}"
        except Exception as e:
            ok, detalle = False, str(e)

        self.repair_audit_db.registrar(
            seccion="🌐 Variables Entorno", accion="Restaurar backup de registro",
            objetivo=os.path.basename(path), exito=ok, detalle=detalle,
        )

        if ok:
            _broadcast_environment_change()
            self.set_status(f"✅ Backup importado: {os.path.basename(path)}")
            if hasattr(self, 'refresh_environment_vars'):
                self.refresh_environment_vars()
        else:
            self._avisar("Error al restaurar", f"No se pudo importar el backup:\n\n{detalle}\n\n"
                "Si el backup incluye variables de Sistema, comprueba que MASys "
                "se está ejecutando como administrador.", QMessageBox.Warning)

    def _soporte_notificar_cambio_entorno(self):
        """Reemite manualmente WM_SETTINGCHANGE — útil cuando alguien tocó
        el registro por fuera de MASys (regedit, un script) y no quiere
        cerrar sesión para que Explorer/nuevas consolas vean el cambio."""
        if not self._confirmar_reparacion(
            "Notificar cambio de entorno",
            cambios=[
                "Enviar WM_SETTINGCHANGE a todos los procesos con ventanas (Explorer, "
                "nuevas consolas...) para que relean las variables de entorno del registro",
                "No modifica ningún dato, solo avisa a los procesos ya abiertos",
            ],
            reversible=True,
        ):
            return

        try:
            _broadcast_environment_change()
            self.repair_audit_db.registrar(
                seccion="🌐 Variables Entorno", accion="Notificar cambio de entorno",
                objetivo="Sistema completo", exito=True, detalle="WM_SETTINGCHANGE emitido",
            )
            self._avisar(
                "Notificar cambio de entorno",
                "Aviso enviado. Los procesos ya abiertos que escuchen este mensaje "
                "(como Explorer) verán las variables actualizadas; los que ya tenían "
                "su propio entorno cargado en memoria (como una consola ya abierta) "
                "seguirán sin verlo hasta que se reinicien."
            )
        except Exception as e:
            self.repair_audit_db.registrar(
                seccion="🌐 Variables Entorno", accion="Notificar cambio de entorno",
                objetivo="Sistema completo", exito=False, detalle=str(e),
            )
            self._avisar("Error", f"No se pudo notificar el cambio:\n\n{e}", QMessageBox.Warning)

    def _entradas_path_problema(self, valor: str, es_pathext: bool, vars_conocidas: dict):
        """Recalcula, sobre el valor ACTUAL de una variable tipo PATH, qué
        entradas son duplicadas o apuntan a una ruta inexistente — misma
        lógica que _detectar_problemas_env_vars, pero aquí devuelve además
        la lista de entradas limpias para poder reescribir la variable."""
        partes = valor.split(";")
        vistas = set()
        limpias = []
        eliminadas = []
        for p in partes:
            p_stripped = p.strip()
            if not p_stripped:
                continue
            expandida = _expandir_valor_env(p_stripped.strip('"'), vars_conocidas)
            norm = expandida.rstrip("\\").lower()
            if norm in vistas:
                eliminadas.append((p_stripped, "entrada duplicada"))
                continue
            if not es_pathext and not os.path.exists(expandida):
                eliminadas.append((p_stripped, "ruta inexistente"))
                continue
            vistas.add(norm)
            limpias.append(p)
        return limpias, eliminadas

    def _detectar_soporte_env_vars(self):
        """Detector de Variables de Entorno: por cada variable tipo PATH/
        PATHEXT/PSModulePath (en cualquiera de los dos ámbitos) que tenga
        ahora mismo entradas duplicadas o rutas inexistentes, ofrece una
        acción de limpieza de un clic — opera sobre los datos YA CARGADOS
        en la tabla, no vuelve a consultar el registro."""
        if not hasattr(self, 'env_vars_model'):
            return []
        vars_conocidas = {**os.environ, **{r.name: r.value for r in self.env_vars_model.rows()}}

        acciones = []
        for row in self.env_vars_model.rows():
            if row.name.upper() not in _PATH_LIKE_VARS:
                continue
            _limpias, eliminadas = self._entradas_path_problema(
                row.value, row.name.upper() == "PATHEXT", vars_conocidas)
            if not eliminadas:
                continue
            ambito = _ENV_SCOPE_LABELS.get(row.scope, row.scope)
            etiqueta = f"🧹 Limpiar {row.name} ({ambito}): quitar {len(eliminadas)} entrada(s)"
            clave = f"env_vars_limpiar::{row.name}::{row.scope}"
            acciones.append((clave, etiqueta, lambda nombre=row.name, scope=row.scope: self._soporte_limpiar_path_var(nombre, scope)))
        return acciones

    def _soporte_limpiar_path_var(self, nombre: str, scope: str):
        """Reescribe una variable tipo PATH quitando duplicados/rutas rotas.
        Vuelve a leer el valor actual desde el modelo (no el capturado al
        construir el menú) por si cambió entre medias."""
        row = self.env_vars_model.row_by_key(nombre, scope)
        if row is None:
            self._avisar("Limpiar PATH", "Esa variable ya no existe; actualiza la lista e inténtalo de nuevo.")
            return
        if scope == "system" and not is_admin():
            self._avisar("Se requieren privilegios de administrador",
                f"Modificar el {nombre} de Sistema requiere ejecutar MASys como administrador.",
                QMessageBox.Warning)
            return

        vars_conocidas = {**os.environ, **{r.name: r.value for r in self.env_vars_model.rows()}}
        limpias, eliminadas = self._entradas_path_problema(row.value, row.name.upper() == "PATHEXT", vars_conocidas)
        if not eliminadas:
            self._avisar("Limpiar PATH", f"«{nombre}» ya no tiene entradas duplicadas ni rutas rotas.")
            return

        ambito = _ENV_SCOPE_LABELS.get(scope, scope)
        cambios = [f"Eliminar {len(eliminadas)} entrada(s) de «{nombre}» ({ambito}):"]
        cambios += [f"{entrada}  —  {motivo}" for entrada, motivo in eliminadas]
        if not self._confirmar_reparacion(
            f"Limpiar {nombre}", cambios=cambios, reversible=True,
            objetivo=f"{nombre} ({ambito})",
        ):
            return
        if not self._ofrecer_backup_si_hace_falta():
            return

        valor_nuevo = ";".join(limpias)
        ok, msg = self.var_manager.set_value(nombre, valor_nuevo, scope)
        if ok:
            self.env_var_log_db.add_entry(nombre, scope, "limpiar_path", row.value, valor_nuevo)
            self.repair_audit_db.registrar(
                seccion="🌐 Variables Entorno", accion=f"Limpiar {nombre}",
                objetivo=f"{nombre} ({ambito})", exito=True,
                detalle=f"{len(eliminadas)} entrada(s) eliminadas: " +
                        ", ".join(e for e, _m in eliminadas),
                backup_realizado=self._env_var_backup_hecho,
            )
            self.set_status(f"✅ {len(eliminadas)} entrada(s) eliminadas de «{nombre}»")
            self.refresh_environment_vars()
        else:
            self.repair_audit_db.registrar(
                seccion="🌐 Variables Entorno", accion=f"Limpiar {nombre}",
                objetivo=f"{nombre} ({ambito})", exito=False, detalle=msg,
            )
            self._avisar("Error", f"No se pudo aplicar la limpieza:\n{msg}", QMessageBox.Warning)

    def _entradas_riesgo_seguridad_path(self, row, vars_conocidas: dict):
        """Recalcula, sobre el valor ACTUAL de una variable tipo PATH, qué
        entradas representan un riesgo de seguridad real: ruta en carpeta
        temporal, o carpeta que aparece ANTES que System32 y contiene un
        binario crítico del sistema (posible secuestro de comandos). Es
        deliberadamente una comprobación aparte de _entradas_path_problema:
        aquí no se trata de rutas rotas/duplicadas sino de una señal de
        seguridad, y merece su propio texto de aviso más explícito."""
        nombre_upper = row.name.upper()
        es_lista_rutas = nombre_upper in _PATH_LIKE_VARS or (";" in row.value and "\\" in row.value)
        if not es_lista_rutas or nombre_upper == "PATHEXT":
            return []

        partes = [p for p in row.value.split(";") if p.strip()]
        system32 = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32")
        idx_system32 = next(
            (i for i, p in enumerate(partes) if p.strip().rstrip("\\").lower() == system32.lower()),
            None,
        )

        riesgos = []
        for i, p in enumerate(partes):
            p_stripped = p.strip()
            expandida = _expandir_valor_env(p_stripped.strip('"'), vars_conocidas)
            if not os.path.isdir(expandida):
                continue

            # Las dos señales son independientes: una carpeta puede ser a la
            # vez temporal Y de secuestro (p. ej. malware que se instala en
            # %TEMP% con nombre cmd.exe) — no se descartan entre sí.
            motivos = []
            if any(frag in expandida.lower() + "\\" for frag in _SUSPICIOUS_PATH_FRAGMENTS):
                motivos.append(f"ruta en carpeta temporal ({expandida})")
            if idx_system32 is not None and i < idx_system32:
                try:
                    archivos = {f.lower() for f in os.listdir(expandida)}
                except OSError:
                    archivos = set()
                interceptados = _CRITICAL_SYSTEM_BINARIES & archivos
                if interceptados:
                    motivos.append(
                        f"contiene {', '.join(sorted(interceptados))} y aparece antes que "
                        "System32 — posible secuestro de comandos"
                    )
            if motivos:
                riesgos.append((p_stripped, "; ".join(motivos)))
        return riesgos

    def _detectar_soporte_seguridad_path(self):
        """Detector separado del de limpieza normal: variables tipo PATH con
        entradas que son una señal real de riesgo de seguridad (carpeta
        temporal o posible secuestro de comandos), no simples rutas rotas."""
        if not hasattr(self, 'env_vars_model'):
            return []
        vars_conocidas = {**os.environ, **{r.name: r.value for r in self.env_vars_model.rows()}}

        acciones = []
        for row in self.env_vars_model.rows():
            riesgos = self._entradas_riesgo_seguridad_path(row, vars_conocidas)
            if not riesgos:
                continue
            ambito = _ENV_SCOPE_LABELS.get(row.scope, row.scope)
            etiqueta = f"⚠️ Riesgo de seguridad en {row.name} ({ambito}): {len(riesgos)} entrada(s)"
            clave = f"env_vars_riesgo::{row.name}::{row.scope}"
            acciones.append((clave, etiqueta, lambda nombre=row.name, scope=row.scope: self._soporte_riesgo_seguridad_path(nombre, scope)))
        return acciones

    def _soporte_riesgo_seguridad_path(self, nombre: str, scope: str):
        """Acción de seguridad, separada de 'Limpiar PATH': antes de ofrecer
        quitar nada, explica el riesgo real (persistencia de malware /
        secuestro de comandos) con un texto propio y más explícito, dejando
        claro que es una alerta a revisar, no una confirmación de infección."""
        row = self.env_vars_model.row_by_key(nombre, scope)
        if row is None:
            self._avisar("Riesgo de seguridad en PATH",
                "Esa variable ya no existe; actualiza la lista e inténtalo de nuevo.")
            return
        if scope == "system" and not is_admin():
            self._avisar("Se requieren privilegios de administrador",
                f"Modificar el {nombre} de Sistema requiere ejecutar MASys como administrador.",
                QMessageBox.Warning)
            return

        vars_conocidas = {**os.environ, **{r.name: r.value for r in self.env_vars_model.rows()}}
        riesgos = self._entradas_riesgo_seguridad_path(row, vars_conocidas)
        if not riesgos:
            self._avisar("Riesgo de seguridad en PATH",
                f"«{nombre}» ya no tiene entradas de riesgo detectadas.")
            return

        ambito = _ENV_SCOPE_LABELS.get(scope, scope)
        explicacion = (
            "POSIBLE PERSISTENCIA DE MALWARE O SECUESTRO DE COMANDOS.\n\n"
            "Se han detectado entradas que podrían interceptar comandos del sistema "
            "(cmd.exe, powershell.exe, explorer.exe...) o que viven en una carpeta "
            "temporal — un patrón típico de malware persistente en Windows.\n\n"
            "Esto es una ALERTA a revisar, NO una confirmación de infección: antes "
            "de eliminar nada, identifica qué instaló cada entrada y, si tienes "
            "dudas, analízala con tu antivirus/EDR antes de continuar."
        )
        cambios = [f"{entrada}  —  {motivo}" for entrada, motivo in riesgos]

        if not self._confirmar_reparacion(
            f"⚠️ Riesgo de seguridad en {nombre}", cambios=cambios, reversible=True,
            objetivo=f"{nombre} ({ambito})", explicacion=explicacion, es_alerta=True,
        ):
            return
        if not self._ofrecer_backup_si_hace_falta():
            return

        entradas_a_quitar = {e for e, _m in riesgos}
        partes = row.value.split(";")
        nuevas = [p for p in partes if p.strip() not in entradas_a_quitar]
        valor_nuevo = ";".join(nuevas)

        ok, msg = self.var_manager.set_value(nombre, valor_nuevo, scope)
        if ok:
            self.env_var_log_db.add_entry(nombre, scope, "quitar_riesgo_seguridad", row.value, valor_nuevo)
            self.repair_audit_db.registrar(
                seccion="🌐 Variables Entorno", accion=f"Quitar riesgo de seguridad en {nombre}",
                objetivo=f"{nombre} ({ambito})", exito=True,
                detalle=f"{len(riesgos)} entrada(s) eliminadas: " + ", ".join(entradas_a_quitar),
                backup_realizado=self._env_var_backup_hecho,
            )
            self.set_status(f"✅ {len(riesgos)} entrada(s) de riesgo eliminadas de «{nombre}»")
            self.refresh_environment_vars()
        else:
            self.repair_audit_db.registrar(
                seccion="🌐 Variables Entorno", accion=f"Quitar riesgo de seguridad en {nombre}",
                objetivo=f"{nombre} ({ambito})", exito=False, detalle=msg,
            )
            self._avisar("Error", f"No se pudo aplicar el cambio:\n{msg}", QMessageBox.Warning)

    def _detectar_soporte_solape_scopes(self):
        """Detector: variables (que no sean PATH/PATHEXT/PSModulePath/TEMP/TMP,
        diseñadas para solaparse) definidas a la vez en Usuario y en Sistema
        con valores distintos ahora mismo — mismo criterio de exclusión que
        _detectar_problemas_env_vars. No se puede resolver solo (hay que
        elegir qué valor prevalece), así que la acción abre un diálogo de
        elección en vez de aplicar nada automáticamente."""
        if not hasattr(self, 'env_vars_model'):
            return []
        solape_esperado = _PATH_LIKE_VARS | {"TEMP", "TMP"}
        por_nombre = {}
        for row in self.env_vars_model.rows():
            por_nombre.setdefault(row.name.lower(), {})[row.scope] = row

        acciones = []
        for name_lower, filas in por_nombre.items():
            if name_lower.upper() in solape_esperado:
                continue
            if "user" not in filas or "system" not in filas:
                continue
            if filas["user"].value == filas["system"].value:
                continue
            nombre_real = filas["user"].name
            etiqueta = f"⚖️ Resolver solape Usuario/Sistema en {nombre_real}"
            clave = f"env_vars_solape::{nombre_real}"
            acciones.append((clave, etiqueta, lambda nombre=nombre_real: self._soporte_resolver_solape(nombre)))
        return acciones

    def _soporte_resolver_solape(self, nombre: str):
        """Muestra los dos valores en conflicto y deja elegir cuál debe
        prevalecer en AMBOS ámbitos — a diferencia de 'Limpiar PATH', aquí no
        hay una respuesta mecánica correcta: los dos valores pueden ser
        intencionales, así que la decisión es siempre del usuario."""
        row_user = self.env_vars_model.row_by_key(nombre, "user")
        row_system = self.env_vars_model.row_by_key(nombre, "system")
        if row_user is None or row_system is None:
            self._avisar("Resolver solape",
                f"«{nombre}» ya no está definida en ambos ámbitos; actualiza la lista e inténtalo de nuevo.")
            return
        if row_user.value == row_system.value:
            self._avisar("Resolver solape", f"«{nombre}» ya tiene el mismo valor en ambos ámbitos.")
            return

        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle(f"Resolver solape — {nombre}")
        dialog.setMinimumWidth(580)
        v = QVBoxLayout(dialog)
        v.addWidget(QLabel(
            f"«{nombre}» tiene valores distintos en Usuario y en Sistema.\n"
            "Elige cuál quieres que quede en AMBOS ámbitos:"))

        lbl_user_hdr = QLabel("Valor en Usuario:")
        lbl_user_hdr.setStyleSheet("font-weight: 600;")
        v.addWidget(lbl_user_hdr)
        txt_user = QTextEdit()
        txt_user.setReadOnly(True)
        txt_user.setPlainText(row_user.value)
        txt_user.setMaximumHeight(70)
        v.addWidget(txt_user)

        lbl_sys_hdr = QLabel("Valor en Sistema:")
        lbl_sys_hdr.setStyleSheet("font-weight: 600;")
        v.addWidget(lbl_sys_hdr)
        txt_sys = QTextEdit()
        txt_sys.setReadOnly(True)
        txt_sys.setPlainText(row_system.value)
        txt_sys.setMaximumHeight(70)
        v.addWidget(txt_sys)

        botones = QHBoxLayout()
        btn_usar_user = QPushButton("Usar el de Usuario en ambos")
        btn_usar_sys = QPushButton("Usar el de Sistema en ambos")
        btn_cancelar = QPushButton("Cancelar")
        botones.addWidget(btn_usar_user)
        botones.addWidget(btn_usar_sys)
        botones.addWidget(btn_cancelar)
        self._agregar_boton_validar_aviso(botones, dialog)
        v.addLayout(botones)

        eleccion = {}

        def _elegir(valor):
            eleccion["scope_prevalece"] = valor
            dialog.accept()

        btn_usar_user.clicked.connect(lambda: _elegir("user"))
        btn_usar_sys.clicked.connect(lambda: _elegir("system"))
        btn_cancelar.clicked.connect(dialog.reject)

        if dialog.exec_() != QDialog.Accepted or "scope_prevalece" not in eleccion:
            return

        if eleccion["scope_prevalece"] == "user":
            valor_final, scope_a_escribir, fila_destino = row_user.value, "system", row_system
        else:
            valor_final, scope_a_escribir, fila_destino = row_system.value, "user", row_user

        if scope_a_escribir == "system" and not is_admin():
            self._avisar("Se requieren privilegios de administrador",
                f"Igualar el valor de «{nombre}» en Sistema requiere ejecutar MASys como administrador.",
                QMessageBox.Warning)
            return

        ambito_destino = _ENV_SCOPE_LABELS.get(scope_a_escribir, scope_a_escribir)
        if not self._confirmar_reparacion(
            f"Resolver solape en {nombre}",
            cambios=[f"Ámbito {ambito_destino}: cambiar de «{fila_destino.value}» a «{valor_final}»"],
            reversible=True, objetivo=f"{nombre} ({ambito_destino})",
        ):
            return
        if not self._ofrecer_backup_si_hace_falta():
            return

        ok, msg = self.var_manager.set_value(nombre, valor_final, scope_a_escribir)
        if ok:
            self.env_var_log_db.add_entry(nombre, scope_a_escribir, "resolver_solape", fila_destino.value, valor_final)
            self.repair_audit_db.registrar(
                seccion="🌐 Variables Entorno", accion=f"Resolver solape en {nombre}",
                objetivo=f"{nombre} ({ambito_destino})", exito=True,
                detalle=f"Igualado al valor de {'Usuario' if eleccion['scope_prevalece'] == 'user' else 'Sistema'}",
                backup_realizado=self._env_var_backup_hecho,
            )
            self.set_status(f"✅ «{nombre}» igualada en ambos ámbitos")
            self.refresh_environment_vars()
        else:
            self.repair_audit_db.registrar(
                seccion="🌐 Variables Entorno", accion=f"Resolver solape en {nombre}",
                objetivo=f"{nombre} ({ambito_destino})", exito=False, detalle=msg,
            )
            self._avisar("Error", f"No se pudo aplicar el cambio:\n{msg}", QMessageBox.Warning)

    def _detectar_soporte_path_largo(self):
        """Detector: PATH combinado (Sistema + Usuario, en ese orden — el
        mismo que arma Windows para un proceso nuevo) por encima del límite
        práctico clásico de ~2047 caracteres."""
        if not hasattr(self, 'env_vars_model'):
            return []
        row_user = self.env_vars_model.row_by_key("PATH", "user")
        row_system = self.env_vars_model.row_by_key("PATH", "system")
        valor_user = row_user.value if row_user else ""
        valor_system = row_system.value if row_system else ""
        combinado = ";".join(p for p in (valor_system, valor_user) if p)
        if len(combinado) <= 2047:
            return []
        return [("path_largo", f"📏 Revisar PATH combinado extenso ({len(combinado)} caracteres)",
                 self._soporte_revisar_path_largo)]

    def _soporte_revisar_path_largo(self):
        """Diagnóstico de solo lectura (no modifica nada, como 'Ver historial
        de cambios'): explica de dónde viene la longitud del PATH combinado
        y qué se ahorraría limpiando cada ámbito, y lista las entradas más
        largas para que se revisen a mano — no se borra nada automáticamente
        aquí porque decidir qué entrada larga es prescindible requiere saber
        qué se usa realmente en este equipo."""
        row_user = self.env_vars_model.row_by_key("PATH", "user")
        row_system = self.env_vars_model.row_by_key("PATH", "system")
        valor_user = row_user.value if row_user else ""
        valor_system = row_system.value if row_system else ""
        combinado = ";".join(p for p in (valor_system, valor_user) if p)

        vars_conocidas = {**os.environ, **{r.name: r.value for r in self.env_vars_model.rows()}}
        n_entradas_user = len([p for p in valor_user.split(";") if p.strip()])
        n_entradas_system = len([p for p in valor_system.split(";") if p.strip()])

        lineas = [
            f"PATH combinado (Sistema + Usuario): {len(combinado)} caracteres "
            "(límite práctico clásico ~2047).",
            "",
            f"Aportación de Sistema: {len(valor_system)} caracteres, {n_entradas_system} entrada(s).",
            f"Aportación de Usuario: {len(valor_user)} caracteres, {n_entradas_user} entrada(s).",
            "",
        ]

        for etiqueta_scope, row in (("Sistema", row_system), ("Usuario", row_user)):
            if row is None:
                continue
            _limpias, eliminadas = self._entradas_path_problema(row.value, False, vars_conocidas)
            if eliminadas:
                caracteres_ahorrados = sum(len(e) + 1 for e, _m in eliminadas)
                lineas.append(
                    f"En PATH ({etiqueta_scope}), \"🧹 Limpiar PATH\" quitaría {len(eliminadas)} "
                    f"entrada(s) y ahorraría ~{caracteres_ahorrados} caracteres."
                )
        lineas.append("")

        entradas_todas = []
        if valor_system:
            entradas_todas += [(p.strip(), "Sistema") for p in valor_system.split(";") if p.strip()]
        if valor_user:
            entradas_todas += [(p.strip(), "Usuario") for p in valor_user.split(";") if p.strip()]
        entradas_todas.sort(key=lambda x: len(x[0]), reverse=True)

        lineas.append("Entradas más largas (candidatas a revisar a mano):")
        for entrada, scope_nombre in entradas_todas[:15]:
            lineas.append(f"  {len(entrada):>4} car.  [{scope_nombre}]  {entrada}")

        texto = "\n".join(lineas)

        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle("PATH combinado extenso")
        dialog.setMinimumSize(700, 480)
        v = QVBoxLayout(dialog)
        txt = QTextEdit()
        txt.setReadOnly(True)
        txt.setPlainText(texto)
        txt.setFontFamily("Consolas")
        v.addWidget(txt)
        botones = QDialogButtonBox(QDialogButtonBox.Close)
        botones.rejected.connect(dialog.reject)
        botones.accepted.connect(dialog.accept)
        self._agregar_boton_validar_aviso(botones, dialog, es_button_box=True)
        v.addWidget(botones)
        dialog.exec_()

    # ── Procesos ─────────────────────────────────────────────────────────

    def _soporte_reiniciar_explorer(self):
        """Finaliza y relanza explorer.exe — la reparación de 'técnico en
        situ' más habitual de esta sección (barra de tareas colgada, iconos
        desaparecidos, ventanas del Explorador de archivos sin responder).
        Síncrona, como el resto de acciones de Procesos: psutil es rápido y
        local, no justifica un QThread propio."""
        procesos = [p for p in self.process_manager.list_all_processes() if p.name.lower() == "explorer.exe"]
        if not procesos:
            self._avisar("Reiniciar Windows Explorer", "No se encontró ningún proceso explorer.exe en ejecución.")
            return

        if not self._confirmar_reparacion(
            "Reiniciar Windows Explorer",
            cambios=[
                f"Finalizar {len(procesos)} instancia(s) de explorer.exe (se cerrarán las "
                "ventanas del Explorador de archivos abiertas; el escritorio y la barra de "
                "tareas parpadearán al reaparecer)",
                "Relanzar explorer.exe automáticamente",
            ],
            reversible=True,
        ):
            return

        errores = []
        for p in procesos:
            ok, msg = self.process_manager.kill_process(p.pid)
            if not ok:
                errores.append(f"PID {p.pid}: {msg}")

        try:
            subprocess.Popen(["explorer.exe"])
            exito = True
            detalle = "OK" if not errores else "Con avisos: " + "; ".join(errores)
        except Exception as e:
            exito = False
            detalle = f"No se pudo relanzar explorer.exe: {e}"
            if errores:
                detalle += " | " + "; ".join(errores)

        self.repair_audit_db.registrar(
            seccion="⚙️ Procesos", accion="Reiniciar Windows Explorer",
            objetivo=f"{len(procesos)} proceso(s) explorer.exe", exito=exito, detalle=detalle,
        )

        if exito:
            self.set_status("✅ Windows Explorer reiniciado")
            if hasattr(self, 'refresh_processes'):
                self.refresh_processes()
        else:
            self._avisar("Error", detalle, QMessageBox.Warning)

    def _procesos_sospechosos(self):
        """Procesos (ya cargados en la tabla) con alguna señal de riesgo,
        de más a menos grave:
        1) pseudo-proceso del kernel (System, Registry, Memory Compression...)
           con una ruta de ejecutable — NUNCA deberían tenerla, así que si
           aparece es un proceso real haciéndose pasar por uno especial.
        2) nombre de un proceso crítico del sistema (svchost.exe, csrss.exe...)
           corriendo FUERA de su carpeta real — técnica clásica de malware
           para camuflarse en el Administrador de tareas.
        3) ejecutable en carpeta temporal (débil pero real, mismo criterio
           que _SUSPICIOUS_PATH_FRAGMENTS en Variables de Entorno).
        Devuelve [(row, motivo), ...] — cada proceso aparece una sola vez,
        con el motivo de mayor prioridad que le aplique. No incluye los que
        el usuario ya marcó como legítimos (ProcessWhitelistDB, clave
        nombre+ruta) desde este mismo diálogo."""
        if not hasattr(self, 'processes_model'):
            return []
        resultado = []
        for row in self.processes_model.rows():
            if self.process_whitelist_db.is_whitelisted(row.name, row.exe_path or ""):
                continue
            if _pseudo_proceso_con_ruta_falsa(row):
                resultado.append((row, "proceso especial del sistema con ruta de ejecutable "
                                        "(nunca debería tenerla)"))
                continue
            if not row.exe_path:
                continue
            if _proceso_disfrazado(row):
                resultado.append((row, "nombre de proceso crítico del sistema fuera de su carpeta real"))
            elif (row.name.lower() not in _CRITICAL_PROCESSES
                    and not _is_windows_process(row)
                    and any(frag in row.exe_path.lower() + "\\" for frag in _SUSPICIOUS_PATH_FRAGMENTS)):
                resultado.append((row, "ejecutable en carpeta temporal"))
        return resultado

    def _detectar_soporte_procesos_sospechosos(self):
        sospechosos = self._procesos_sospechosos()
        if not sospechosos:
            return []
        return [("procesos_sospechosos", f"⚠️ Procesos sospechosos: {len(sospechosos)} encontrado(s)",
                 self._soporte_procesos_sospechosos)]

    def _soporte_procesos_sospechosos(self):
        """Lista los procesos detectados con un aviso explícito (mismo tono
        que 'Riesgo de seguridad en PATH') y permite finalizar cada uno de
        forma individual — sin finalización masiva sin revisar, porque tanto
        instaladores legítimos en carpetas temporales como falsos positivos
        de la detección por nombre son posibles."""
        sospechosos = self._procesos_sospechosos()
        if not sospechosos:
            self._avisar("Procesos sospechosos", "Ya no hay procesos sospechosos detectados.")
            return

        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle("Procesos sospechosos")
        dialog.setMinimumWidth(760)
        v = QVBoxLayout(dialog)

        aviso = QLabel(
            "⚠️ Se han detectado procesos con alguna señal de riesgo: ejecutarse desde una "
            "carpeta temporal, o llamarse igual que un proceso crítico del sistema "
            "(svchost.exe, csrss.exe...) sin vivir en su carpeta real — ambas son técnicas "
            "típicas de malware persistente, aunque también dan falsos positivos "
            "(instaladores legítimos, por ejemplo).\n\n"
            "Esto es una ALERTA a revisar, NO una confirmación de infección: antes de "
            "finalizar nada, identifica qué proceso es y, si tienes dudas, analízalo con "
            "tu antivirus/EDR."
        )
        aviso.setWordWrap(True)
        v.addWidget(aviso)

        tabla = QTableWidget(len(sospechosos), 4)
        tabla.setHorizontalHeaderLabels(["Proceso", "PID", "Ruta", "Motivo"])
        tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        tabla.setSelectionBehavior(QTableWidget.SelectRows)
        tabla.setSelectionMode(QTableWidget.SingleSelection)
        tabla.verticalHeader().setVisible(False)
        tabla.setSortingEnabled(False)
        for fila, (row, motivo) in enumerate(sospechosos):
            item_proceso = QTableWidgetItem(row.name)
            # Se guarda (row, motivo) en Qt.UserRole de la propia celda en
            # vez de indexar sospechosos[fila] por posición: al activar el
            # orden por columnas las filas se reordenan, y sin esto se
            # acabaría actuando sobre el proceso equivocado tras ordenar.
            item_proceso.setData(Qt.UserRole, (row, motivo))
            tabla.setItem(fila, 0, item_proceso)
            item_pid = QTableWidgetItem()
            item_pid.setData(Qt.DisplayRole, row.pid)
            tabla.setItem(fila, 1, item_pid)
            tabla.setItem(fila, 2, QTableWidgetItem(row.exe_path))
            tabla.setItem(fila, 3, QTableWidgetItem(motivo))
        tabla.setSortingEnabled(True)
        _setup_header(tabla, 4, stretch_col=2)
        tabla.model().layoutChanged.emit()
        v.addWidget(tabla)

        fila_botones = QHBoxLayout()
        btn_finalizar = QPushButton("✖ Finalizar seleccionado")
        btn_finalizar.setEnabled(False)
        fila_botones.addWidget(btn_finalizar)
        btn_marcar_legitimo = QPushButton("✅ Marcar como legítimo (no volver a avisar)")
        btn_marcar_legitimo.setEnabled(False)
        fila_botones.addWidget(btn_marcar_legitimo)
        fila_botones.addStretch()
        v.addLayout(fila_botones)

        def _actualizar_botones_seleccion():
            hay_seleccion = bool(tabla.selectionModel().selectedRows())
            btn_finalizar.setEnabled(hay_seleccion)
            btn_marcar_legitimo.setEnabled(hay_seleccion)

        tabla.itemSelectionChanged.connect(_actualizar_botones_seleccion)

        def _finalizar_seleccionado():
            filas_sel = tabla.selectionModel().selectedRows()
            if not filas_sel:
                return
            fila_idx = filas_sel[0].row()
            row, motivo = tabla.item(fila_idx, 0).data(Qt.UserRole)
            if not self._confirmar_reparacion(
                f"Finalizar proceso sospechoso: {row.name}",
                cambios=[f"Finalizar «{row.name}» (PID {row.pid})", f"Ruta: {row.exe_path}"],
                reversible=False, objetivo=f"{row.name} (PID {row.pid})",
                explicacion=(
                    f"Motivo de la alerta: {motivo}. Confirma que sabes qué es este proceso "
                    "antes de finalizarlo — si no lo reconoces, considera analizarlo con tu "
                    "antivirus/EDR en vez de solo finalizarlo."
                ),
                es_alerta=True,
            ):
                return
            ok, msg = self.process_manager.kill_process(row.pid)
            self.repair_audit_db.registrar(
                seccion="⚙️ Procesos", accion="Finalizar proceso sospechoso",
                objetivo=f"{row.name} (PID {row.pid}) — {row.exe_path} — {motivo}", exito=ok,
                detalle="OK" if ok else msg,
            )
            if ok:
                self.set_status(f"✅ Proceso «{row.name}» (PID {row.pid}) finalizado")
                tabla.removeRow(fila_idx)
                if hasattr(self, 'refresh_processes'):
                    self.refresh_processes()
                if tabla.rowCount() == 0:
                    dialog.accept()
            else:
                QMessageBox.warning(dialog, "Error", f"No se pudo finalizar el proceso:\n{msg}")

        def _marcar_legitimo():
            filas_sel = tabla.selectionModel().selectedRows()
            if not filas_sel:
                return
            fila_idx = filas_sel[0].row()
            row, motivo = tabla.item(fila_idx, 0).data(Qt.UserRole)
            if not self._confirmar_reparacion(
                "Marcar como legítimo",
                cambios=[f"«{row.name}» (PID {row.pid}) dejará de generar alertas en:", row.exe_path],
                reversible=True, objetivo=f"{row.name} (PID {row.pid})",
                explicacion=(
                    "No se volverá a avisar de este proceso EN ESA RUTA EXACTA — si en el "
                    "futuro aparece con el mismo nombre en otra ruta, sí se detectará de nuevo. "
                    "Puedes deshacerlo después desde 'Ver procesos marcados como legítimos'."
                ),
            ):
                return
            self.process_whitelist_db.add(row.name, row.exe_path or "", motivo)
            self.set_status(f"✅ «{row.name}» marcado como legítimo — no volverá a avisar en esta ruta")
            tabla.removeRow(fila_idx)
            if tabla.rowCount() == 0:
                dialog.accept()

        btn_finalizar.clicked.connect(_finalizar_seleccionado)
        btn_marcar_legitimo.clicked.connect(_marcar_legitimo)

        botones = QDialogButtonBox(QDialogButtonBox.Close)
        botones.rejected.connect(dialog.reject)
        botones.accepted.connect(dialog.accept)
        self._agregar_boton_validar_aviso(botones, dialog, es_button_box=True)
        v.addWidget(botones)
        dialog.exec_()

    def _soporte_ver_procesos_whitelist(self):
        """Gestión de la lista de procesos marcados como legítimos: verla y
        quitar entradas (para que el detector vuelva a avisar de esa ruta) —
        cierra el mismo círculo que 'Ver historial' en Variables de Entorno:
        si se puede excluir, tiene que poderse deshacer la exclusión."""
        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle("Procesos marcados como legítimos")
        dialog.setMinimumSize(720, 400)
        v = QVBoxLayout(dialog)

        tabla = QTableWidget(0, 4)
        tabla.setHorizontalHeaderLabels(["Proceso", "Ruta", "Motivo original", "Marcado el"])
        tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        tabla.setSelectionBehavior(QTableWidget.SelectRows)
        tabla.setSelectionMode(QTableWidget.SingleSelection)
        tabla.verticalHeader().setVisible(False)
        _setup_header(tabla, 4, stretch_col=1)
        v.addWidget(tabla)

        lbl_vacio = QLabel("No hay ningún proceso marcado como legítimo todavía.")
        lbl_vacio.setVisible(False)
        v.addWidget(lbl_vacio)

        def _recargar():
            entradas = self.process_whitelist_db.get_all()
            tabla.setSortingEnabled(False)
            tabla.setRowCount(len(entradas))
            for fila, entrada in enumerate(entradas):
                nombre, ruta, motivo, marcado_el = entrada
                for col, valor in enumerate([nombre, ruta, motivo, marcado_el]):
                    item = QTableWidgetItem(valor)
                    if col == 0:
                        item.setData(Qt.UserRole, entrada)
                    tabla.setItem(fila, col, item)
            tabla.setSortingEnabled(True)
            tabla.model().layoutChanged.emit()
            lbl_vacio.setVisible(not entradas)
            btn_quitar.setEnabled(False)

        fila_botones = QHBoxLayout()
        btn_quitar = QPushButton("✕ Quitar de la lista (volver a detectar)")
        btn_quitar.setEnabled(False)
        fila_botones.addWidget(btn_quitar)
        fila_botones.addStretch()
        v.addLayout(fila_botones)

        tabla.itemSelectionChanged.connect(
            lambda: btn_quitar.setEnabled(bool(tabla.selectionModel().selectedRows())))

        def _quitar_seleccionado():
            filas_sel = tabla.selectionModel().selectedRows()
            if not filas_sel:
                return
            nombre, ruta, _motivo, _marcado_el = tabla.item(filas_sel[0].row(), 0).data(Qt.UserRole)
            self.process_whitelist_db.remove(nombre, ruta)
            self.set_status(f"✅ «{nombre}» quitado de la lista de legítimos")
            _recargar()

        btn_quitar.clicked.connect(_quitar_seleccionado)

        _recargar()

        botones = QDialogButtonBox(QDialogButtonBox.Close)
        botones.rejected.connect(dialog.reject)
        botones.accepted.connect(dialog.accept)
        v.addWidget(botones)
        dialog.exec_()

    # ── Servicios ────────────────────────────────────────────────────────

    def _soporte_reiniciar_servicios(self, titulo: str, nombres: list, descripcion_accion: str):
        """Detiene e inicia de nuevo una lista de servicios, en ese orden —
        compartido por 'Reiniciar Spooler' y 'Reiniciar servicios de Windows
        Update'. Si alguno ya estaba detenido, stop_service simplemente
        devuelve su propio error (no es fatal, se anota como aviso y se
        sigue con el start)."""
        if not self._confirmar_reparacion(
            titulo,
            cambios=[descripcion_accion, f"Servicios afectados: {', '.join(nombres)}"],
            reversible=True,
        ):
            return

        avisos = []
        for nombre in nombres:
            ok, msg = self.service_manager.stop_service(nombre)
            if not ok:
                avisos.append(f"detener {nombre}: {msg}")
        for nombre in nombres:
            ok, msg = self.service_manager.start_service(nombre)
            if not ok:
                avisos.append(f"iniciar {nombre}: {msg}")

        exito = not avisos
        self.repair_audit_db.registrar(
            seccion="🛠️ Servicios", accion=titulo,
            objetivo=", ".join(nombres), exito=exito,
            detalle="OK" if exito else "; ".join(avisos),
        )
        if exito:
            self.set_status(f"✅ {titulo} completado")
            if hasattr(self, 'refresh_services'):
                self.refresh_services()
        else:
            self._avisar("Con avisos", f"{titulo} terminó con avisos:\n\n" + "\n".join(avisos), QMessageBox.Warning)

    def _soporte_reiniciar_spooler(self):
        """El clásico 'la impresora no imprime, cola atascada' — reiniciar
        el servicio Spooler."""
        self._soporte_reiniciar_servicios(
            "Reiniciar Cola de impresión (Spooler)", ["Spooler"],
            "Detener e iniciar de nuevo el servicio de Cola de impresión (Spooler) — "
            "vacía trabajos de impresión atascados en la cola.",
        )

    def _soporte_reiniciar_windows_update(self):
        """El clásico 'Windows Update no funciona/se queda colgado' —
        reiniciar los tres servicios implicados, en el orden habitual."""
        self._soporte_reiniciar_servicios(
            "Reiniciar servicios de Windows Update", ["wuauserv", "BITS", "CryptSvc"],
            "Detener e iniciar de nuevo los servicios de Windows Update (wuauserv), "
            "Transferencia inteligente en segundo plano (BITS) y Servicios de cifrado (CryptSvc).",
        )

    def _servicios_sospechosos(self):
        """Servicios (ya cargados en la tabla) con alguna señal de riesgo:
        1) nombre de servicio conocido de Windows con BinPath fuera de
           System32/SysWOW64 (servicio 'disfrazado' — configuración de
           registro reescrita para ejecutar otra cosa bajo un nombre de
           confianza).
        2) BinPath en carpeta temporal (para servicios que no sean de
           Windows) — mismo criterio que en Procesos.
        Devuelve [(row, motivo), ...], excluyendo los ya marcados como
        legítimos (ServiceWhitelistDB, clave nombre+ruta)."""
        if not hasattr(self, 'services_model'):
            return []
        resultado = []
        for row in self.services_model.rows():
            if self.service_whitelist_db.is_whitelisted(row.name, row.bin_path or ""):
                continue
            if _servicio_disfrazado(row):
                resultado.append((row, "nombre de servicio conocido de Windows con ruta fuera de "
                                        "System32/SysWOW64"))
                continue
            if not row.bin_path:
                continue
            if (not _is_windows_service(row)
                    and any(frag in row.bin_path.lower() + "\\" for frag in _SUSPICIOUS_PATH_FRAGMENTS)):
                resultado.append((row, "ejecutable en carpeta temporal"))
        return resultado

    def _detectar_soporte_servicios_sospechosos(self):
        sospechosos = self._servicios_sospechosos()
        if not sospechosos:
            return []
        return [("servicios_sospechosos", f"⚠️ Servicios sospechosos: {len(sospechosos)} encontrado(s)",
                 self._soporte_servicios_sospechosos)]

    def _soporte_servicios_sospechosos(self):
        """Lista los servicios detectados con un aviso explícito (mismo tono
        que 'Procesos sospechosos') y permite, por cada uno: detenerlo y
        deshabilitar su tipo de inicio (reversible: se puede volver a activar
        a mano), o marcarlo como legítimo para no volver a avisar en esa
        ruta exacta."""
        sospechosos = self._servicios_sospechosos()
        if not sospechosos:
            self._avisar("Servicios sospechosos", "Ya no hay servicios sospechosos detectados.")
            return

        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle("Servicios sospechosos")
        dialog.setMinimumWidth(780)
        v = QVBoxLayout(dialog)

        aviso = QLabel(
            "⚠️ Se han detectado servicios con alguna señal de riesgo: un nombre de servicio "
            "conocido de Windows cuyo ejecutable vive fuera de System32/SysWOW64, o un "
            "ejecutable en carpeta temporal — ambas son técnicas típicas de persistencia de "
            "malware (los servicios sobreviven a un reinicio), aunque también dan falsos "
            "positivos con software de terceros legítimo.\n\n"
            "Esto es una ALERTA a revisar, NO una confirmación de infección: antes de tocar "
            "nada, identifica qué servicio es y, si tienes dudas, analízalo con tu "
            "antivirus/EDR."
        )
        aviso.setWordWrap(True)
        v.addWidget(aviso)

        tabla = QTableWidget(len(sospechosos), 4)
        tabla.setHorizontalHeaderLabels(["Servicio", "Nombre visible", "BinPath", "Motivo"])
        tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        tabla.setSelectionBehavior(QTableWidget.SelectRows)
        tabla.setSelectionMode(QTableWidget.SingleSelection)
        tabla.verticalHeader().setVisible(False)
        tabla.setSortingEnabled(False)
        for fila, (row, motivo) in enumerate(sospechosos):
            item_nombre = QTableWidgetItem(row.name)
            item_nombre.setData(Qt.UserRole, (row, motivo))
            tabla.setItem(fila, 0, item_nombre)
            tabla.setItem(fila, 1, QTableWidgetItem(row.display_name))
            tabla.setItem(fila, 2, QTableWidgetItem(row.bin_path))
            tabla.setItem(fila, 3, QTableWidgetItem(motivo))
        tabla.setSortingEnabled(True)
        _setup_header(tabla, 4, stretch_col=2)
        tabla.model().layoutChanged.emit()
        v.addWidget(tabla)

        fila_botones = QHBoxLayout()
        btn_detener = QPushButton("⏸ Detener y deshabilitar seleccionado")
        btn_detener.setEnabled(False)
        fila_botones.addWidget(btn_detener)
        btn_marcar_legitimo = QPushButton("✅ Marcar como legítimo (no volver a avisar)")
        btn_marcar_legitimo.setEnabled(False)
        fila_botones.addWidget(btn_marcar_legitimo)
        fila_botones.addStretch()
        v.addLayout(fila_botones)

        def _actualizar_botones_seleccion():
            hay_seleccion = bool(tabla.selectionModel().selectedRows())
            btn_detener.setEnabled(hay_seleccion)
            btn_marcar_legitimo.setEnabled(hay_seleccion)

        tabla.itemSelectionChanged.connect(_actualizar_botones_seleccion)

        def _detener_y_deshabilitar():
            filas_sel = tabla.selectionModel().selectedRows()
            if not filas_sel:
                return
            fila_idx = filas_sel[0].row()
            row, motivo = tabla.item(fila_idx, 0).data(Qt.UserRole)
            if not self._confirmar_reparacion(
                f"Detener y deshabilitar servicio sospechoso: {row.display_name}",
                cambios=[
                    f"Detener el servicio «{row.display_name}» ({row.name}) si está en ejecución",
                    "Cambiar su tipo de inicio a Deshabilitado (para que no vuelva a arrancar solo)",
                    f"Ruta: {row.bin_path}",
                ],
                reversible=True, objetivo=f"{row.name} ({row.display_name})",
                explicacion=(
                    f"Motivo de la alerta: {motivo}. Confirma que sabes qué es este servicio "
                    "antes de tocarlo — si no lo reconoces, considera analizarlo con tu "
                    "antivirus/EDR. Es reversible: puedes volver a iniciarlo y restaurar su "
                    "tipo de inicio manualmente después."
                ),
                es_alerta=True,
            ):
                return
            row_actual = self.services_model.row_by_name(row.name) or row
            avisos = []
            if row_actual.status == "running":
                ok_stop, msg_stop = self.service_manager.stop_service(row.name)
                if not ok_stop:
                    avisos.append(f"detener: {msg_stop}")
            ok_disable, msg_disable = self.service_manager.set_startup_type(row.name, "disabled")
            if not ok_disable:
                avisos.append(f"deshabilitar: {msg_disable}")
            ok = not avisos
            self.repair_audit_db.registrar(
                seccion="🛠️ Servicios", accion="Detener y deshabilitar servicio sospechoso",
                objetivo=f"{row.name} ({row.display_name}) — {row.bin_path} — {motivo}", exito=ok,
                detalle="OK" if ok else "; ".join(avisos),
            )
            if ok:
                self.set_status(f"✅ Servicio «{row.display_name}» detenido y deshabilitado")
                tabla.removeRow(fila_idx)
                if hasattr(self, 'refresh_services'):
                    self.refresh_services()
                if tabla.rowCount() == 0:
                    dialog.accept()
            else:
                QMessageBox.warning(dialog, "Con avisos", "; ".join(avisos))

        def _marcar_legitimo():
            filas_sel = tabla.selectionModel().selectedRows()
            if not filas_sel:
                return
            fila_idx = filas_sel[0].row()
            row, motivo = tabla.item(fila_idx, 0).data(Qt.UserRole)
            if not self._confirmar_reparacion(
                "Marcar como legítimo",
                cambios=[f"«{row.display_name}» ({row.name}) dejará de generar alertas en:", row.bin_path],
                reversible=True, objetivo=f"{row.name} ({row.display_name})",
                explicacion=(
                    "No se volverá a avisar de este servicio EN ESA RUTA EXACTA — si en el "
                    "futuro aparece con el mismo nombre en otra ruta, sí se detectará de nuevo. "
                    "Puedes deshacerlo después desde 'Ver servicios marcados como legítimos'."
                ),
            ):
                return
            self.service_whitelist_db.add(row.name, row.bin_path or "", motivo)
            self.set_status(f"✅ «{row.display_name}» marcado como legítimo — no volverá a avisar en esta ruta")
            tabla.removeRow(fila_idx)
            if tabla.rowCount() == 0:
                dialog.accept()

        btn_detener.clicked.connect(_detener_y_deshabilitar)
        btn_marcar_legitimo.clicked.connect(_marcar_legitimo)

        botones = QDialogButtonBox(QDialogButtonBox.Close)
        botones.rejected.connect(dialog.reject)
        botones.accepted.connect(dialog.accept)
        self._agregar_boton_validar_aviso(botones, dialog, es_button_box=True)
        v.addWidget(botones)
        dialog.exec_()

    def _servicios_rutas_sin_comillas(self):
        if not hasattr(self, 'services_model'):
            return []
        return [row for row in self.services_model.rows() if _ruta_servicio_sin_comillas(row)]

    def _detectar_soporte_servicios_rutas_sin_comillas(self):
        afectados = self._servicios_rutas_sin_comillas()
        if not afectados:
            return []
        return [("servicios_rutas_sin_comillas", f"⚠️ Rutas de servicio sin comillas: {len(afectados)} encontrado(s)",
                 self._soporte_servicios_rutas_sin_comillas)]

    def _soporte_servicios_rutas_sin_comillas(self):
        """A diferencia de 'Servicios sospechosos', esto no es una alerta de
        posible malware — es una vulnerabilidad real en un servicio
        legítimo ('unquoted service path'). La reparación (añadir comillas
        al BinPath) no cambia el comportamiento del servicio, solo elimina
        la ambigüedad que un atacante podría explotar."""
        afectados = self._servicios_rutas_sin_comillas()
        if not afectados:
            self._avisar("Rutas de servicio sin comillas", "Ya no hay rutas de servicio sin comillas detectadas.")
            return

        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle("Rutas de servicio sin comillas")
        dialog.setMinimumWidth(780)
        v = QVBoxLayout(dialog)

        aviso = QLabel(
            "⚠️ Estos servicios tienen una ruta de ejecutable con espacios sin encerrar entre "
            "comillas. Windows prueba a ejecutar cada sub-ruta separada por espacios en orden "
            "hasta dar con un '.exe' — si alguien coloca un ejecutable en una de esas "
            "sub-rutas intermedias, se ejecutaría con los privilegios de este servicio (a "
            "menudo SYSTEM). Es una vulnerabilidad real en software por lo demás legítimo, no "
            "una señal de infección — añadir las comillas la elimina sin cambiar el "
            "comportamiento del servicio."
        )
        aviso.setWordWrap(True)
        v.addWidget(aviso)

        tabla = QTableWidget(len(afectados), 3)
        tabla.setHorizontalHeaderLabels(["Servicio", "Nombre visible", "BinPath actual"])
        tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        tabla.setSelectionBehavior(QTableWidget.SelectRows)
        tabla.setSelectionMode(QTableWidget.SingleSelection)
        tabla.verticalHeader().setVisible(False)
        tabla.setSortingEnabled(False)
        for fila, row in enumerate(afectados):
            item_nombre = QTableWidgetItem(row.name)
            item_nombre.setData(Qt.UserRole, row)
            tabla.setItem(fila, 0, item_nombre)
            tabla.setItem(fila, 1, QTableWidgetItem(row.display_name))
            tabla.setItem(fila, 2, QTableWidgetItem(row.bin_path))
        tabla.setSortingEnabled(True)
        _setup_header(tabla, 3, stretch_col=2)
        tabla.model().layoutChanged.emit()
        v.addWidget(tabla)

        fila_botones = QHBoxLayout()
        btn_corregir = QPushButton("🔧 Corregir ruta (añadir comillas)")
        btn_corregir.setEnabled(False)
        fila_botones.addWidget(btn_corregir)
        fila_botones.addStretch()
        v.addLayout(fila_botones)

        tabla.itemSelectionChanged.connect(
            lambda: btn_corregir.setEnabled(bool(tabla.selectionModel().selectedRows())))

        def _corregir_seleccionado():
            filas_sel = tabla.selectionModel().selectedRows()
            if not filas_sel:
                return
            fila_idx = filas_sel[0].row()
            row = tabla.item(fila_idx, 0).data(Qt.UserRole)
            row_actual = self.services_model.row_by_name(row.name) or row
            ruta_corregida = _ruta_servicio_corregida(row_actual.bin_path)
            if not self._confirmar_reparacion(
                f"Corregir ruta sin comillas: {row_actual.display_name}",
                cambios=[
                    f"Cambiar BinPath de:\n{row_actual.bin_path}",
                    f"a:\n{ruta_corregida}",
                ],
                reversible=True, objetivo=f"{row_actual.name} ({row_actual.display_name})",
            ):
                return
            ok, msg = self.service_manager.set_bin_path(row_actual.name, ruta_corregida)
            self.repair_audit_db.registrar(
                seccion="🛠️ Servicios", accion="Corregir ruta de servicio sin comillas",
                objetivo=f"{row_actual.name} ({row_actual.display_name})", exito=ok,
                detalle="OK" if ok else msg,
            )
            if ok:
                self.set_status(f"✅ Ruta de «{row_actual.display_name}» corregida")
                tabla.removeRow(fila_idx)
                if hasattr(self, 'refresh_services'):
                    self.refresh_services()
                if tabla.rowCount() == 0:
                    dialog.accept()
            else:
                QMessageBox.warning(dialog, "Error", f"No se pudo corregir la ruta:\n{msg}")

        btn_corregir.clicked.connect(_corregir_seleccionado)

        botones = QDialogButtonBox(QDialogButtonBox.Close)
        botones.rejected.connect(dialog.reject)
        botones.accepted.connect(dialog.accept)
        self._agregar_boton_validar_aviso(botones, dialog, es_button_box=True)
        v.addWidget(botones)
        dialog.exec_()

    def _soporte_ver_servicios_whitelist(self):
        """Gestión de la lista de servicios marcados como legítimos: verla y
        quitar entradas (para que el detector vuelva a avisar de esa ruta) —
        mismo mecanismo que 'Ver procesos marcados como legítimos'."""
        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle("Servicios marcados como legítimos")
        dialog.setMinimumSize(720, 400)
        v = QVBoxLayout(dialog)

        tabla = QTableWidget(0, 4)
        tabla.setHorizontalHeaderLabels(["Servicio", "BinPath", "Motivo original", "Marcado el"])
        tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        tabla.setSelectionBehavior(QTableWidget.SelectRows)
        tabla.setSelectionMode(QTableWidget.SingleSelection)
        tabla.verticalHeader().setVisible(False)
        _setup_header(tabla, 4, stretch_col=1)
        v.addWidget(tabla)

        lbl_vacio = QLabel("No hay ningún servicio marcado como legítimo todavía.")
        lbl_vacio.setVisible(False)
        v.addWidget(lbl_vacio)

        def _recargar():
            entradas = self.service_whitelist_db.get_all()
            tabla.setSortingEnabled(False)
            tabla.setRowCount(len(entradas))
            for fila, entrada in enumerate(entradas):
                nombre, ruta, motivo, marcado_el = entrada
                for col, valor in enumerate([nombre, ruta, motivo, marcado_el]):
                    item = QTableWidgetItem(valor)
                    if col == 0:
                        item.setData(Qt.UserRole, entrada)
                    tabla.setItem(fila, col, item)
            tabla.setSortingEnabled(True)
            tabla.model().layoutChanged.emit()
            lbl_vacio.setVisible(not entradas)
            btn_quitar.setEnabled(False)

        fila_botones = QHBoxLayout()
        btn_quitar = QPushButton("✕ Quitar de la lista (volver a detectar)")
        btn_quitar.setEnabled(False)
        fila_botones.addWidget(btn_quitar)
        fila_botones.addStretch()
        v.addLayout(fila_botones)

        tabla.itemSelectionChanged.connect(
            lambda: btn_quitar.setEnabled(bool(tabla.selectionModel().selectedRows())))

        def _quitar_seleccionado():
            filas_sel = tabla.selectionModel().selectedRows()
            if not filas_sel:
                return
            nombre, ruta, _motivo, _marcado_el = tabla.item(filas_sel[0].row(), 0).data(Qt.UserRole)
            self.service_whitelist_db.remove(nombre, ruta)
            self.set_status(f"✅ «{nombre}» quitado de la lista de legítimos")
            _recargar()

        btn_quitar.clicked.connect(_quitar_seleccionado)

        _recargar()

        botones = QDialogButtonBox(QDialogButtonBox.Close)
        botones.rejected.connect(dialog.reject)
        botones.accepted.connect(dialog.accept)
        v.addWidget(botones)
        dialog.exec_()

    def _servicios_atascados(self):
        """Servicios (ya cargados en la tabla) atascados en un estado
        'pending' (iniciando/deteniendo/pausando/reanudando sin resolverse)
        y con un PID real — StopService normal en ese estado suele ya haber
        fallado o quedarse esperando indefinidamente."""
        if not hasattr(self, 'services_model'):
            return []
        estados_pending = {"start_pending", "stop_pending", "pause_pending", "continue_pending"}
        return [row for row in self.services_model.rows() if row.status in estados_pending and row.pid]

    def _detectar_soporte_servicios_atascados(self):
        atascados = self._servicios_atascados()
        if not atascados:
            return []
        return [("servicios_atascados", f"🔧 Servicios atascados: {len(atascados)} encontrado(s)",
                 self._soporte_servicios_atascados)]

    def _soporte_servicios_atascados(self):
        """Lista los servicios atascados en un estado pending y permite
        forzar la finalización de su proceso (PID) directamente — algo que
        hoy solo se podía hacer saliendo al Administrador de tareas a buscar
        el PID a mano, ya que el propio Administrador de servicios de
        Windows no ofrece esta opción."""
        atascados = self._servicios_atascados()
        if not atascados:
            self._avisar("Servicios atascados", "Ya no hay servicios atascados detectados.")
            return

        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle("Servicios atascados")
        dialog.setMinimumWidth(680)
        v = QVBoxLayout(dialog)

        aviso = QLabel(
            "🔧 Estos servicios llevan un tiempo en un estado de transición "
            "(iniciando/deteniendo/pausando/reanudando) sin completarlo. Forzar la "
            "finalización mata directamente su proceso — no es un detenido ordenado: "
            "Windows podría relanzarlo solo si su tipo de inicio es Automático, o "
            "marcarlo como fallido. Úsalo solo si el servicio lleva ya un buen rato "
            "atascado y no responde."
        )
        aviso.setWordWrap(True)
        v.addWidget(aviso)

        tabla = QTableWidget(len(atascados), 4)
        tabla.setHorizontalHeaderLabels(["Servicio", "Nombre visible", "Estado", "PID"])
        tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        tabla.setSelectionBehavior(QTableWidget.SelectRows)
        tabla.setSelectionMode(QTableWidget.SingleSelection)
        tabla.verticalHeader().setVisible(False)
        tabla.setSortingEnabled(False)
        for fila, row in enumerate(atascados):
            item_nombre = QTableWidgetItem(row.name)
            item_nombre.setData(Qt.UserRole, row)
            tabla.setItem(fila, 0, item_nombre)
            tabla.setItem(fila, 1, QTableWidgetItem(row.display_name))
            tabla.setItem(fila, 2, QTableWidgetItem(STATUS_LABELS.get(row.status, row.status)))
            item_pid = QTableWidgetItem()
            item_pid.setData(Qt.DisplayRole, row.pid)
            tabla.setItem(fila, 3, item_pid)
        tabla.setSortingEnabled(True)
        _setup_header(tabla, 4, stretch_col=1)
        tabla.model().layoutChanged.emit()
        v.addWidget(tabla)

        fila_botones = QHBoxLayout()
        btn_forzar = QPushButton("⛔ Forzar finalización del proceso")
        btn_forzar.setEnabled(False)
        fila_botones.addWidget(btn_forzar)
        fila_botones.addStretch()
        v.addLayout(fila_botones)

        tabla.itemSelectionChanged.connect(
            lambda: btn_forzar.setEnabled(bool(tabla.selectionModel().selectedRows())))

        def _forzar_seleccionado():
            filas_sel = tabla.selectionModel().selectedRows()
            if not filas_sel:
                return
            fila_idx = filas_sel[0].row()
            row = tabla.item(fila_idx, 0).data(Qt.UserRole)
            row_actual = self.services_model.row_by_name(row.name) or row
            estado_label = STATUS_LABELS.get(row_actual.status, row_actual.status)
            if not self._confirmar_reparacion(
                f"Forzar finalización: {row_actual.display_name}",
                cambios=[f"Finalizar directamente el proceso (PID {row_actual.pid}) de "
                         f"«{row_actual.display_name}», atascado en estado «{estado_label}»"],
                reversible=False, objetivo=f"{row_actual.name} (PID {row_actual.pid})",
                explicacion=(
                    "Esto NO detiene el servicio de forma ordenada — mata su proceso "
                    "directamente. Windows podría reiniciarlo automáticamente si su tipo de "
                    "inicio es Automático, o marcarlo como fallido. Úsalo solo si el servicio "
                    "lleva un buen rato atascado y no responde."
                ),
                es_alerta=True,
            ):
                return
            ok, msg = self.service_manager.force_kill_process(row_actual.pid)
            self.repair_audit_db.registrar(
                seccion="🛠️ Servicios", accion="Forzar finalización de servicio atascado",
                objetivo=f"{row_actual.name} (PID {row_actual.pid}) — {estado_label}", exito=ok,
                detalle="OK" if ok else msg,
            )
            if ok:
                self.set_status(f"✅ Proceso de «{row_actual.display_name}» finalizado")
                tabla.removeRow(fila_idx)
                if hasattr(self, 'refresh_services'):
                    self.refresh_services()
                if tabla.rowCount() == 0:
                    dialog.accept()
            else:
                QMessageBox.warning(dialog, "Error", f"No se pudo finalizar el proceso:\n{msg}")

        btn_forzar.clicked.connect(_forzar_seleccionado)

        botones = QDialogButtonBox(QDialogButtonBox.Close)
        botones.rejected.connect(dialog.reject)
        botones.accepted.connect(dialog.accept)
        self._agregar_boton_validar_aviso(botones, dialog, es_button_box=True)
        v.addWidget(botones)
        dialog.exec_()

    # ── Actualizaciones ──────────────────────────────────────────────────

    def _soporte_restablecer_windows_update(self):
        """El 'reset de caché' clásico de técnico: más profundo que solo
        reiniciar los servicios (Servicios → Reiniciar servicios de Windows
        Update, que únicamente destraba un servicio colgado) — esto limpia
        la caché de descargas y el almacén de certificados de firma que
        suele quedar corrupto cuando Windows Update lleva tiempo fallando o
        atascado buscando actualizaciones."""
        if not is_admin():
            self._avisar(
                "Se requieren privilegios de administrador",
                "Restablecer Windows Update requiere ejecutar MASys como administrador.",
                QMessageBox.Warning,
            )
            return

        if not self._confirmar_reparacion(
            "Restablecer Windows Update",
            cambios=[
                "Detener los servicios wuauserv, BITS y CryptSvc",
                "Renombrar %WINDIR%\\SoftwareDistribution y %WINDIR%\\System32\\catroot2 "
                "(Windows los reconstruye solos la próxima vez que los necesite)",
                "Volver a iniciar los tres servicios",
            ],
            reversible=True,
            explicacion=(
                "Soluciona el caso típico de 'Windows Update lleva tiempo fallando o "
                "atascado buscando actualizaciones' por caché corrupta. Las carpetas NO "
                "se borran, se renombran con un sufijo — quedan ahí por si hiciera falta "
                "rescatar algo."
            ),
        ):
            return

        windir = os.environ.get("WINDIR", r"C:\Windows")
        carpetas = [
            os.path.join(windir, "SoftwareDistribution"),
            os.path.join(windir, "System32", "catroot2"),
        ]
        servicios = ["wuauserv", "BITS", "CryptSvc"]

        avisos = []
        for nombre in servicios:
            ok, msg = self.service_manager.stop_service(nombre)
            if not ok:
                avisos.append(f"detener {nombre}: {msg}")

        for carpeta in carpetas:
            if not os.path.isdir(carpeta):
                continue
            destino = carpeta + ".old"
            sufijo = 1
            while os.path.exists(destino):
                sufijo += 1
                destino = f"{carpeta}.old{sufijo}"
            try:
                os.rename(carpeta, destino)
            except Exception as e:
                avisos.append(f"renombrar {carpeta}: {e}")

        for nombre in servicios:
            ok, msg = self.service_manager.start_service(nombre)
            if not ok:
                avisos.append(f"iniciar {nombre}: {msg}")

        exito = not avisos
        self.repair_audit_db.registrar(
            seccion="🔄 Actualizaciones", accion="Restablecer Windows Update",
            objetivo=", ".join(servicios), exito=exito,
            detalle="OK" if exito else "; ".join(avisos),
        )
        if exito:
            self.set_status("✅ Windows Update restablecido")
            self._avisar(
                "Restablecer Windows Update",
                "Restablecido correctamente. Vuelve a comprobar actualizaciones — la "
                "primera búsqueda tras esto puede tardar más de lo habitual.",
            )
            if hasattr(self, 'refresh_windows_updates'):
                self.refresh_windows_updates()
        else:
            self._avisar("Con avisos", "Restablecer Windows Update terminó con avisos:\n\n" + "\n".join(avisos), QMessageBox.Warning)

    def _detectar_soporte_actualizaciones(self):
        if getattr(self, '_reboot_pendiente', False):
            return [("actualizaciones_reinicio", "🔁 Reiniciar el equipo ahora", self._soporte_reiniciar_equipo)]
        return []

    def _soporte_reiniciar_equipo(self):
        """Solo aparece en el menú cuando hay un reinicio pendiente. Usa el
        mecanismo nativo de Windows (shutdown /r /t 60) en vez de un
        reinicio instantáneo — da 60 segundos con el aviso propio de
        Windows, cancelable con 'shutdown /a', antes de la acción más
        disruptiva que ofrece toda la aplicación."""
        if not self._confirmar_reparacion(
            "Reiniciar el equipo",
            cambios=[
                "Programar el reinicio del equipo en 60 segundos (aviso nativo de Windows)",
                "Cierra el trabajo sin guardar en cualquier aplicación abierta antes de continuar",
            ],
            reversible=False,
            explicacion=(
                "Hay un reinicio pendiente para completar la instalación de "
                "actualizaciones de Windows. El aviso de Windows con cuenta atrás se "
                "puede cancelar ejecutando 'shutdown /a' antes de que termine."
            ),
            es_alerta=True,
        ):
            return

        try:
            subprocess.run(
                ["shutdown", "/r", "/t", "60", "/c",
                 "MASys: reinicio para completar actualizaciones de Windows"],
                timeout=10,
            )
            self.repair_audit_db.registrar(
                seccion="🔄 Actualizaciones", accion="Reiniciar el equipo",
                objetivo="Sistema completo", exito=True,
                detalle="shutdown /r /t 60 programado",
            )
            self.set_status("🔄 Reinicio programado en 60 segundos (shutdown /a para cancelar)")
        except Exception as e:
            self.repair_audit_db.registrar(
                seccion="🔄 Actualizaciones", accion="Reiniciar el equipo",
                objetivo="Sistema completo", exito=False, detalle=str(e),
            )
            self._avisar("Error", f"No se pudo programar el reinicio:\n\n{e}", QMessageBox.Warning)

    # ── Controladores ────────────────────────────────────────────────────

    def _soporte_backup_controladores(self):
        """Acción clásica de 'Adm. Dispositivos': copia de seguridad
        completa de los paquetes de controladores de terceros. Elegir
        carpeta y progreso viven dentro del propio diálogo
        (DriverBackupDialog / DriverBackupThread), igual que CHKDSK."""
        from msys.ui.driver_backup_dialog import DriverBackupDialog
        dlg = DriverBackupDialog(self, self.driver_manager)
        dlg.exec_()

    def _soporte_controladores_ocultos(self):
        """Acción clásica de 'Adm. Dispositivos': dispositivos 'fantasma'
        (ya no presentes, pero con entrada conservada por Windows). La
        tabla principal nunca los incluye — ver HiddenDevicesDialog, que ya
        traía su propia carga/refresco/exportar CSV/quitar por menú
        contextual, montado sobre DriverManager.enum_disconnected_devices_csv
        y remove_device(force=True)."""
        from msys.ui.hidden_devices_dialog import HiddenDevicesDialog
        dlg = HiddenDevicesDialog(self, self.driver_manager)
        dlg.exec_()

    def _dispositivos_con_problema(self):
        if not hasattr(self, 'drivers_model'):
            return []
        return [row for row in self.drivers_model.rows() if (row.problema or "").strip()]

    def _detectar_soporte_controladores(self):
        problemas = self._dispositivos_con_problema()
        if not problemas:
            return []
        return [("controladores_problema", f"⚠️ Dispositivos con problema: {len(problemas)} encontrado(s)",
                 self._soporte_dispositivos_con_problema)]

    def _soporte_dispositivos_con_problema(self):
        """Lista los dispositivos que Windows ya marca con problema
        (Win32_PnPEntity.ConfigManagerErrorCode) y permite reiniciarlos
        (deshabilitar + habilitar) de uno en uno — el mismo 'apágalo y
        enciéndelo' del menú contextual de Controladores, pero partiendo de
        lo ya detectado. Bloqueado para clases de riesgo (disco, sistema,
        red...), igual que en el menú contextual."""
        problemas = self._dispositivos_con_problema()
        if not problemas:
            self._avisar("Dispositivos con problema", "Ya no hay dispositivos con problema detectados.")
            return

        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle("Dispositivos con problema")
        dialog.setMinimumWidth(760)
        v = QVBoxLayout(dialog)

        aviso = QLabel(
            "⚠️ Windows ha marcado estos dispositivos con un problema (controlador dañado, "
            "deshabilitado, en conflicto...). Reiniciar un dispositivo (deshabilitarlo y "
            "volver a habilitarlo) fuerza a Windows a recargar su controlador, y resuelve "
            "muchos de estos casos sin tener que reinstalar nada."
        )
        aviso.setWordWrap(True)
        v.addWidget(aviso)

        tabla = QTableWidget(len(problemas), 4)
        tabla.setHorizontalHeaderLabels(["Dispositivo", "Clase", "Estado", "Problema"])
        tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        tabla.setSelectionBehavior(QTableWidget.SelectRows)
        tabla.setSelectionMode(QTableWidget.SingleSelection)
        tabla.verticalHeader().setVisible(False)
        tabla.setSortingEnabled(False)
        for fila, row in enumerate(problemas):
            item_nombre = QTableWidgetItem(row.dispositivo)
            item_nombre.setData(Qt.UserRole, row)
            tabla.setItem(fila, 0, item_nombre)
            tabla.setItem(fila, 1, QTableWidgetItem(row.clase))
            tabla.setItem(fila, 2, QTableWidgetItem(row.estado))
            tabla.setItem(fila, 3, QTableWidgetItem(row.problema))
        tabla.setSortingEnabled(True)
        _setup_header(tabla, 4, stretch_col=3)
        tabla.model().layoutChanged.emit()
        v.addWidget(tabla)

        fila_botones = QHBoxLayout()
        btn_reiniciar = QPushButton("🔁 Reiniciar dispositivo seleccionado")
        btn_reiniciar.setEnabled(False)
        fila_botones.addWidget(btn_reiniciar)
        fila_botones.addStretch()
        v.addLayout(fila_botones)

        tabla.itemSelectionChanged.connect(
            lambda: btn_reiniciar.setEnabled(bool(tabla.selectionModel().selectedRows())))

        def _reiniciar_seleccionado():
            filas_sel = tabla.selectionModel().selectedRows()
            if not filas_sel:
                return
            fila_idx = filas_sel[0].row()
            row = tabla.item(fila_idx, 0).data(Qt.UserRole)

            if not _dispositivo_reinicio_permitido(row):
                self._avisar(
                    "Reiniciar dispositivo",
                    "Por seguridad, esta clase de dispositivo no se puede reiniciar desde "
                    "aquí — podría interrumpir el arranque o el acceso remoto. Hazlo desde "
                    "el Administrador de dispositivos si estás seguro.",
                    QMessageBox.Warning,
                )
                return
            if not is_admin():
                self._avisar(
                    "Se requieren privilegios de administrador",
                    "Reiniciar un dispositivo requiere ejecutar MASys como administrador.",
                    QMessageBox.Warning,
                )
                return
            if not self._confirmar_reparacion(
                f"Reiniciar dispositivo: {row.dispositivo}",
                cambios=[f"Deshabilitar y volver a habilitar «{row.dispositivo}»"],
                reversible=True, objetivo=row.dispositivo,
                explicacion=(
                    f"Problema detectado: {row.problema}. Puede provocar una breve "
                    "interrupción del dispositivo mientras se reinicia."
                ),
            ):
                return

            ok = self._ejecutar_reiniciar_dispositivo(row, seccion_origen="🖥️ Adm. Dispositivos")
            if ok:
                tabla.removeRow(fila_idx)
                if tabla.rowCount() == 0:
                    dialog.accept()

        btn_reiniciar.clicked.connect(_reiniciar_seleccionado)

        botones = QDialogButtonBox(QDialogButtonBox.Close)
        botones.rejected.connect(dialog.reject)
        botones.accepted.connect(dialog.accept)
        self._agregar_boton_validar_aviso(botones, dialog, es_button_box=True)
        v.addWidget(botones)
        dialog.exec_()

    # ── Impresoras ───────────────────────────────────────────────────────

    def _impresoras_con_problema(self):
        if not hasattr(self, 'printers_model'):
            return []
        return [row for row in self.printers_model.rows()
                if any(p in (row.estado or "").strip().lower() for p in ("error", "desconectada"))]

    def _detectar_soporte_impresoras(self):
        problemas = self._impresoras_con_problema()
        if not problemas:
            return []
        return [("impresoras_problema", f"⚠️ Impresoras con problema: {len(problemas)} encontrada(s)",
                 self._soporte_impresoras_con_problema)]

    def _soporte_impresoras_con_problema(self):
        """Lista impresoras en Error/Desconectada y permite quitar la
        seleccionada sin salir del diálogo — mismo patrón que
        'Dispositivos con problema' de Adm. Dispositivos."""
        problemas = self._impresoras_con_problema()
        if not problemas:
            self._avisar("Impresoras con problema", "Ya no hay impresoras con problema detectadas.")
            return

        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle("Impresoras con problema")
        dialog.setMinimumWidth(640)
        v = QVBoxLayout(dialog)

        aviso = QLabel(
            "⚠️ Estas impresoras están en estado de error o desconectadas. Puedes quitarlas "
            "del sistema si ya no se usan, o comprobar el cable/red y volver a actualizar."
        )
        aviso.setWordWrap(True)
        v.addWidget(aviso)

        tabla = QTableWidget(len(problemas), 3)
        tabla.setHorizontalHeaderLabels(["Nombre", "Puerto", "Estado"])
        tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        tabla.setSelectionBehavior(QTableWidget.SelectRows)
        tabla.setSelectionMode(QTableWidget.SingleSelection)
        tabla.verticalHeader().setVisible(False)
        tabla.setSortingEnabled(False)
        for fila, row in enumerate(problemas):
            item_nombre = QTableWidgetItem(row.nombre)
            item_nombre.setData(Qt.UserRole, row)
            tabla.setItem(fila, 0, item_nombre)
            tabla.setItem(fila, 1, QTableWidgetItem(row.puerto))
            tabla.setItem(fila, 2, QTableWidgetItem(row.estado))
        tabla.setSortingEnabled(True)
        _setup_header(tabla, 3, stretch_col=0)
        tabla.model().layoutChanged.emit()
        v.addWidget(tabla)

        fila_botones = QHBoxLayout()
        btn_quitar = QPushButton("✕ Quitar impresora seleccionada")
        btn_quitar.setEnabled(False)
        fila_botones.addWidget(btn_quitar)
        fila_botones.addStretch()
        v.addLayout(fila_botones)

        tabla.itemSelectionChanged.connect(
            lambda: btn_quitar.setEnabled(bool(tabla.selectionModel().selectedRows())))

        def _quitar_seleccionada():
            filas_sel = tabla.selectionModel().selectedRows()
            if not filas_sel:
                return
            fila_idx = filas_sel[0].row()
            row = tabla.item(fila_idx, 0).data(Qt.UserRole)

            if not is_admin():
                self._avisar(
                    "Se requieren privilegios de administrador",
                    "Quitar una impresora requiere ejecutar MASys como administrador.",
                    QMessageBox.Warning,
                )
                return
            if not self._confirmar_reparacion(
                f"Quitar impresora: {row.nombre}",
                cambios=[f"Quitar «{row.nombre}» del sistema"],
                reversible=False, objetivo=row.nombre,
                explicacion=(
                    "Si es una impresora de red o compartida, habrá que volver a añadirla "
                    "manualmente si hace falta en el futuro."
                ),
            ):
                return

            ok = self._ejecutar_accion_impresora(
                "Quitar impresora", row.nombre, self.printer_manager.remove,
                seccion_origen="🖨️ Impresoras")
            if ok:
                tabla.removeRow(fila_idx)
                if tabla.rowCount() == 0:
                    dialog.accept()

        btn_quitar.clicked.connect(_quitar_seleccionada)

        botones = QDialogButtonBox(QDialogButtonBox.Close)
        botones.rejected.connect(dialog.reject)
        botones.accepted.connect(dialog.accept)
        self._agregar_boton_validar_aviso(botones, dialog, es_button_box=True)
        v.addWidget(botones)
        dialog.exec_()

    # ── Bluetooth ────────────────────────────────────────────────────────

    def _bluetooth_con_problema(self):
        if not hasattr(self, 'bluetooth_model'):
            return []
        return [row for row in self.bluetooth_model.rows()
                if (row.estado or "").strip().lower() not in ("correcto", "")]

    def _detectar_soporte_bluetooth(self):
        problemas = self._bluetooth_con_problema()
        if not problemas:
            return []
        return [("bluetooth_problema", f"⚠️ Dispositivos Bluetooth con problema: {len(problemas)} encontrado(s)",
                 self._soporte_bluetooth_con_problema)]

    def _soporte_bluetooth_con_problema(self):
        """Lista dispositivos Bluetooth con Estado distinto de 'Correcto' y
        permite reiniciar (deshabilitar+habilitar) el seleccionado — mismo
        patrón que 'Dispositivos con problema' de Adm. Dispositivos."""
        problemas = self._bluetooth_con_problema()
        if not problemas:
            self._avisar("Bluetooth con problema", "Ya no hay dispositivos Bluetooth con problema detectados.")
            return

        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle("Dispositivos Bluetooth con problema")
        dialog.setMinimumWidth(680)
        v = QVBoxLayout(dialog)

        aviso = QLabel(
            "⚠️ Windows reporta un estado distinto de 'Correcto' para estos dispositivos "
            "Bluetooth. Reiniciar (deshabilitar y volver a habilitar) resuelve muchos casos."
        )
        aviso.setWordWrap(True)
        v.addWidget(aviso)

        tabla = QTableWidget(len(problemas), 3)
        tabla.setHorizontalHeaderLabels(["Dispositivo", "Clase", "Estado"])
        tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        tabla.setSelectionBehavior(QTableWidget.SelectRows)
        tabla.setSelectionMode(QTableWidget.SingleSelection)
        tabla.verticalHeader().setVisible(False)
        tabla.setSortingEnabled(False)
        for fila, row in enumerate(problemas):
            item_nombre = QTableWidgetItem(row.nombre)
            item_nombre.setData(Qt.UserRole, row)
            tabla.setItem(fila, 0, item_nombre)
            tabla.setItem(fila, 1, QTableWidgetItem(row.clase))
            tabla.setItem(fila, 2, QTableWidgetItem(row.estado))
        tabla.setSortingEnabled(True)
        _setup_header(tabla, 3, stretch_col=0)
        tabla.model().layoutChanged.emit()
        v.addWidget(tabla)

        fila_botones = QHBoxLayout()
        btn_reiniciar = QPushButton("🔁 Reiniciar dispositivo seleccionado")
        btn_reiniciar.setEnabled(False)
        fila_botones.addWidget(btn_reiniciar)
        fila_botones.addStretch()
        v.addLayout(fila_botones)

        tabla.itemSelectionChanged.connect(
            lambda: btn_reiniciar.setEnabled(bool(tabla.selectionModel().selectedRows())))

        def _reiniciar_seleccionado():
            filas_sel = tabla.selectionModel().selectedRows()
            if not filas_sel:
                return
            fila_idx = filas_sel[0].row()
            row = tabla.item(fila_idx, 0).data(Qt.UserRole)

            if not row.instance_id:
                self._avisar("Reiniciar dispositivo", "Este dispositivo no tiene un Instance ID válido.", QMessageBox.Warning)
                return
            if not is_admin():
                self._avisar(
                    "Se requieren privilegios de administrador",
                    "Reiniciar un dispositivo requiere ejecutar MASys como administrador.",
                    QMessageBox.Warning,
                )
                return
            if not self._confirmar_reparacion(
                f"Reiniciar dispositivo Bluetooth: {row.nombre}",
                cambios=[f"Deshabilitar y volver a habilitar «{row.nombre}»"],
                reversible=True, objetivo=row.nombre,
                explicacion=f"Estado detectado: {row.estado}. Puede provocar una breve desconexión.",
            ):
                return

            ok_disable, msg_disable = self.driver_manager.disable_device(row.instance_id)
            ok_enable, msg_enable = self.driver_manager.enable_device(row.instance_id)
            ok = ok_disable and ok_enable
            detalle = "OK" if ok else f"deshabilitar: {msg_disable} | habilitar: {msg_enable}"
            self.repair_audit_db.registrar(
                seccion="🔵 Bluetooth", accion="Reiniciar dispositivo",
                objetivo=f"{row.nombre} ({row.instance_id})", exito=ok, detalle=detalle,
            )
            if ok:
                self.set_status(f"✅ Dispositivo «{row.nombre}» reiniciado")
                if hasattr(self, 'refresh_bluetooth'):
                    self.refresh_bluetooth()
                tabla.removeRow(fila_idx)
                if tabla.rowCount() == 0:
                    dialog.accept()
            else:
                self._avisar("Con avisos", f"No se pudo reiniciar completamente el dispositivo:\n\n{detalle}", QMessageBox.Warning)

        btn_reiniciar.clicked.connect(_reiniciar_seleccionado)

        botones = QDialogButtonBox(QDialogButtonBox.Close)
        botones.rejected.connect(dialog.reject)
        botones.accepted.connect(dialog.accept)
        self._agregar_boton_validar_aviso(botones, dialog, es_button_box=True)
        v.addWidget(botones)
        dialog.exec_()

    # ── Dispositivos USB ─────────────────────────────────────────────────

    def _soporte_historial_usb(self):
        """Acción clásica de 'Dispositivos USB': historial completo de
        almacenamiento USB conectado alguna vez (no solo lo presente
        ahora) — ver USBHistoryDialog / leer_historial_usb_almacenamiento."""
        from msys.ui.usb_history_dialog import USBHistoryDialog
        dlg = USBHistoryDialog(self)
        dlg.exec_()

    def _soporte_usb_sospechosos(self):
        """Acción clásica de 'Dispositivos USB': indicios tipo BadUSB
        (mismo VID/PID con varias clases de PnP a la vez) — ver
        USBSuspiciousDialog / detectar_usb_sospechosos."""
        from msys.ui.usb_suspicious_dialog import USBSuspiciousDialog
        dlg = USBSuspiciousDialog(self)
        dlg.exec_()

    # ── Software Instalado ───────────────────────────────────────────────

    def _software_con_riesgo(self):
        """Software en la lista negra configurada por el usuario o
        identificado como sin soporte (EOL) — mismos criterios que la
        columna Estado de la tabla (_software_estado), NO se inventa un
        criterio nuevo aquí. A diferencia de los descartados 'genéricos/
        antiguos' de Adm. Dispositivos, esto no es ruido: la lista negra la
        define el propio técnico, y el listado EOL es una lista curada de
        software concreto sin soporte conocido, no 'todo lo antiguo'."""
        if not hasattr(self, 'software_model'):
            return []
        blacklist = _get_software_blacklist()
        resultado = []
        for row in self.software_model.rows():
            if _is_blacklisted_software(row.nombre, blacklist):
                resultado.append((row, "🚫 No autorizado"))
            elif _is_eol_software(row.nombre):
                resultado.append((row, "⚠️ Sin soporte (EOL)"))
        return resultado

    def _detectar_soporte_software(self):
        con_riesgo = self._software_con_riesgo()
        if not con_riesgo:
            return []
        return [("software_riesgo", f"⚠️ Software no autorizado o sin soporte: {len(con_riesgo)} encontrado(s)",
                 self._soporte_software_con_riesgo)]

    def _soporte_software_con_riesgo(self):
        """Lista software no autorizado (lista negra) o sin soporte (EOL) y
        permite desinstalar el seleccionado — mismo patrón que 'Dispositivos
        con problema' de Adm. Dispositivos."""
        con_riesgo = self._software_con_riesgo()
        if not con_riesgo:
            self._avisar("Software con riesgo", "Ya no hay software no autorizado ni sin soporte detectado.")
            return

        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle("Software no autorizado o sin soporte")
        dialog.setMinimumWidth(720)
        v = QVBoxLayout(dialog)

        aviso = QLabel(
            "⚠️ Estas aplicaciones están en tu lista negra configurada o identificadas como sin "
            "soporte del fabricante (EOL) — ambos motivos concretos, no una suposición genérica."
        )
        aviso.setWordWrap(True)
        v.addWidget(aviso)

        tabla = QTableWidget(len(con_riesgo), 3)
        tabla.setHorizontalHeaderLabels(["Software", "Editor", "Motivo"])
        tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        tabla.setSelectionBehavior(QTableWidget.SelectRows)
        tabla.setSelectionMode(QTableWidget.SingleSelection)
        tabla.verticalHeader().setVisible(False)
        tabla.setSortingEnabled(False)
        for fila, (row, motivo) in enumerate(con_riesgo):
            item_nombre = QTableWidgetItem(row.nombre)
            item_nombre.setData(Qt.UserRole, row)
            tabla.setItem(fila, 0, item_nombre)
            tabla.setItem(fila, 1, QTableWidgetItem(row.editor))
            tabla.setItem(fila, 2, QTableWidgetItem(motivo))
        tabla.setSortingEnabled(True)
        _setup_header(tabla, 3, stretch_col=0)
        tabla.model().layoutChanged.emit()
        v.addWidget(tabla)

        fila_botones = QHBoxLayout()
        btn_desinstalar = QPushButton("✕ Desinstalar seleccionado")
        btn_desinstalar.setEnabled(False)
        fila_botones.addWidget(btn_desinstalar)
        fila_botones.addStretch()
        v.addLayout(fila_botones)

        tabla.itemSelectionChanged.connect(
            lambda: btn_desinstalar.setEnabled(bool(tabla.selectionModel().selectedRows())))

        def _desinstalar_seleccionado():
            filas_sel = tabla.selectionModel().selectedRows()
            if not filas_sel:
                return
            fila_idx = filas_sel[0].row()
            row = tabla.item(fila_idx, 0).data(Qt.UserRole)

            if not row.uninstall_string:
                self._avisar("Desinstalar software", "Este software no tiene un desinstalador registrado.", QMessageBox.Warning)
                return
            if not self._confirmar_reparacion(
                f"Desinstalar software: {row.nombre}",
                cambios=[f"Ejecutar el desinstalador de «{row.nombre}»"],
                reversible=False, objetivo=row.nombre,
                explicacion="Se abrirá el desinstalador del fabricante; sigue sus instrucciones en pantalla.",
            ):
                return

            self._desinstalar_software(row.nombre, row.uninstall_string)
            tabla.removeRow(fila_idx)
            if tabla.rowCount() == 0:
                dialog.accept()

        btn_desinstalar.clicked.connect(_desinstalar_seleccionado)

        botones = QDialogButtonBox(QDialogButtonBox.Close)
        botones.rejected.connect(dialog.reject)
        botones.accepted.connect(dialog.accept)
        self._agregar_boton_validar_aviso(botones, dialog, es_button_box=True)
        v.addWidget(botones)
        dialog.exec_()

    # ── Tareas Programadas ───────────────────────────────────────────────

    def _tareas_sospechosas(self):
        if not hasattr(self, 'tasks_model'):
            return []
        return [row for row in self.tasks_model.rows() if row.es_sospechosa]

    def _detectar_soporte_tareas(self):
        # "Tareas con ejecución en horario laboral" NO tiene entrada propia
        # aquí a propósito: abriría el mismo TaskCalendarDialog que ya es
        # accesible en todo momento vía la acción clásica "📅 Calendario de
        # ejecución" — tenerlo dos veces en el mismo menú (uno fijo, otro
        # condicional) era literalmente el mismo calendario duplicado, y el
        # propio calendario ya muestra el resumen "N de M en horario
        # laboral" al abrirse. Un único punto de entrada.
        resultados = []
        sospechosas = self._tareas_sospechosas()
        if sospechosas:
            resultados.append(("tareas_sospechosas", f"⚠️ Tareas programadas con indicios sospechosos: {len(sospechosas)} encontrada(s)",
                                self._soporte_tareas_sospechosas))
        return resultados

    def _abrir_calendario_tareas(self):
        """Abre el calendario de ejecución con las tareas ya cargadas en la
        pestaña — no relanza ninguna consulta a PowerShell, es solo lectura
        de self.tasks_model.rows() (ver msys/ui/task_calendar_dialog.py)."""
        if not hasattr(self, 'tasks_model') or not self.tasks_model.rows():
            self._avisar("Calendario de ejecución",
                          "No hay tareas cargadas todavía. Pulsa «🔄 Actualizar» en la pestaña "
                          "de Tareas Programadas primero.")
            return
        dialog = TaskCalendarDialog(self, self.tasks_model.rows())
        dialog.exec_()

    def _soporte_tareas_sospechosas(self):
        """Lista tareas cuya acción apunta a una carpeta temporal/de
        descargas o contiene parámetros de bypass, y permite deshabilitar
        o eliminar la seleccionada — mismo patrón que el resto de
        'X con problema' de Soporte."""
        sospechosas = self._tareas_sospechosas()
        if not sospechosas:
            self._avisar("Tareas sospechosas", "Ya no hay tareas programadas con indicios sospechosos.")
            return

        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle("Tareas programadas sospechosas")
        dialog.setMinimumWidth(760)
        v = QVBoxLayout(dialog)

        aviso = QLabel(
            "⚠️ Estas tareas tienen indicios concretos (no solo estar ocultas, que es normal en "
            "cualquier equipo): la acción se ejecuta desde una carpeta temporal/de descargas, o "
            "contiene parámetros típicos de bypass/ofuscación."
        )
        aviso.setWordWrap(True)
        v.addWidget(aviso)

        tabla = QTableWidget(len(sospechosas), 3)
        tabla.setHorizontalHeaderLabels(["Tarea", "Motivo", "Acción"])
        tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        tabla.setSelectionBehavior(QTableWidget.SelectRows)
        tabla.setSelectionMode(QTableWidget.SingleSelection)
        tabla.verticalHeader().setVisible(False)
        tabla.setSortingEnabled(False)
        for fila, row in enumerate(sospechosas):
            item_nombre = QTableWidgetItem(row.nombre)
            item_nombre.setData(Qt.UserRole, row)
            tabla.setItem(fila, 0, item_nombre)
            tabla.setItem(fila, 1, QTableWidgetItem("; ".join(row.motivo_sospecha)))
            tabla.setItem(fila, 2, QTableWidgetItem(row.accion))
        tabla.setSortingEnabled(True)
        _setup_header(tabla, 3, stretch_col=2)
        tabla.model().layoutChanged.emit()
        v.addWidget(tabla)

        fila_botones = QHBoxLayout()
        btn_deshabilitar = QPushButton("⏸️ Deshabilitar seleccionada")
        btn_deshabilitar.setEnabled(False)
        fila_botones.addWidget(btn_deshabilitar)
        btn_eliminar = QPushButton("✕ Eliminar seleccionada")
        btn_eliminar.setEnabled(False)
        fila_botones.addWidget(btn_eliminar)
        fila_botones.addStretch()
        v.addLayout(fila_botones)

        def _actualizar_botones():
            hay_sel = bool(tabla.selectionModel().selectedRows())
            btn_deshabilitar.setEnabled(hay_sel)
            btn_eliminar.setEnabled(hay_sel)

        tabla.itemSelectionChanged.connect(_actualizar_botones)

        def _fila_actual():
            filas_sel = tabla.selectionModel().selectedRows()
            if not filas_sel:
                return None, None
            fila_idx = filas_sel[0].row()
            return fila_idx, tabla.item(fila_idx, 0).data(Qt.UserRole)

        def _deshabilitar_seleccionada():
            fila_idx, row = _fila_actual()
            if row is None:
                return
            if not is_admin():
                self._avisar(
                    "Se requieren privilegios de administrador",
                    "Deshabilitar una tarea requiere ejecutar MASys como administrador.",
                    QMessageBox.Warning,
                )
                return
            if not self._confirmar_reparacion(
                f"Deshabilitar tarea: {row.nombre}",
                cambios=[f"Deshabilitar «{row.nombre}»"],
                reversible=True, objetivo=row.nombre,
                explicacion=f"Motivo: {'; '.join(row.motivo_sospecha)}",
            ):
                return
            ok = self._ejecutar_accion_tarea("Deshabilitar tarea", row, self.task_manager.disable)
            if ok:
                tabla.removeRow(fila_idx)
                if tabla.rowCount() == 0:
                    dialog.accept()

        def _eliminar_seleccionada():
            fila_idx, row = _fila_actual()
            if row is None:
                return
            if not is_admin():
                self._avisar(
                    "Se requieren privilegios de administrador",
                    "Eliminar una tarea requiere ejecutar MASys como administrador.",
                    QMessageBox.Warning,
                )
                return
            if not self._confirmar_reparacion(
                f"Eliminar tarea: {row.nombre}",
                cambios=[f"Eliminar definitivamente «{row.nombre}»"],
                reversible=False, objetivo=row.nombre,
                explicacion=f"Motivo: {'; '.join(row.motivo_sospecha)}",
            ):
                return
            ok = self._ejecutar_accion_tarea("Eliminar tarea", row, self.task_manager.delete)
            if ok:
                tabla.removeRow(fila_idx)
                if tabla.rowCount() == 0:
                    dialog.accept()

        btn_deshabilitar.clicked.connect(_deshabilitar_seleccionada)
        btn_eliminar.clicked.connect(_eliminar_seleccionada)

        botones = QDialogButtonBox(QDialogButtonBox.Close)
        botones.rejected.connect(dialog.reject)
        botones.accepted.connect(dialog.accept)
        self._agregar_boton_validar_aviso(botones, dialog, es_button_box=True)
        v.addWidget(botones)
        dialog.exec_()

    # ── Programas de Inicio ─────────────────────────────────────────────

    def _programas_inicio_sospechosos(self):
        if not hasattr(self, 'startup_model'):
            return []
        return [row for row in self.startup_model.rows() if row.es_sospechosa]

    def _detectar_soporte_programas_inicio(self):
        sospechosos = self._programas_inicio_sospechosos()
        if not sospechosos:
            return []
        return [("programas_inicio_sospechosos",
                 f"⚠️ Programas de inicio con indicios sospechosos: {len(sospechosos)} encontrado(s)",
                 self._soporte_programas_inicio_sospechosos)]

    def _soporte_programas_inicio_sospechosos(self):
        """Lista programas de inicio con indicios concretos (ruta temporal/
        de descargas, o fichero al que apuntan ya no existe) y permite
        deshabilitarlos — mismo patrón que _soporte_tareas_sospechosas,
        adaptado (aquí solo tiene sentido deshabilitar, no 'eliminar':
        el valor de registro/acceso directo original no es cosa de
        Soporte tocarlo, StartupApproved es el mecanismo reversible)."""
        sospechosos = self._programas_inicio_sospechosos()
        if not sospechosos:
            self._avisar("Programas de inicio sospechosos", "Ya no hay programas de inicio con indicios sospechosos.")
            return

        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle("Programas de inicio sospechosos")
        dialog.setMinimumWidth(760)
        v = QVBoxLayout(dialog)

        aviso = QLabel(
            "⚠️ Estas entradas tienen indicios concretos: se lanzan desde una carpeta temporal/"
            "de descargas, o el fichero al que apuntan ya no existe en el disco (huérfana)."
        )
        aviso.setWordWrap(True)
        v.addWidget(aviso)

        tabla = QTableWidget(len(sospechosos), 3)
        tabla.setHorizontalHeaderLabels(["Programa", "Motivo", "Ubicación"])
        tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        tabla.setSelectionBehavior(QTableWidget.SelectRows)
        tabla.setSelectionMode(QTableWidget.SingleSelection)
        tabla.verticalHeader().setVisible(False)
        tabla.setSortingEnabled(False)
        for fila, row in enumerate(sospechosos):
            item_nombre = QTableWidgetItem(row.nombre)
            item_nombre.setData(Qt.UserRole, row)
            tabla.setItem(fila, 0, item_nombre)
            tabla.setItem(fila, 1, QTableWidgetItem("; ".join(row.motivo_sospecha)))
            tabla.setItem(fila, 2, QTableWidgetItem(row.ubicacion))
        tabla.setSortingEnabled(True)
        _setup_header(tabla, 3, stretch_col=1)
        tabla.model().layoutChanged.emit()
        v.addWidget(tabla)

        fila_botones = QHBoxLayout()
        btn_deshabilitar = QPushButton("🚫 Deshabilitar seleccionada")
        btn_deshabilitar.setEnabled(False)
        fila_botones.addWidget(btn_deshabilitar)
        fila_botones.addStretch()
        v.addLayout(fila_botones)

        def _actualizar_botones():
            btn_deshabilitar.setEnabled(bool(tabla.selectionModel().selectedRows()))
        tabla.itemSelectionChanged.connect(_actualizar_botones)

        def _fila_actual():
            filas_sel = tabla.selectionModel().selectedRows()
            if not filas_sel:
                return None, None
            fila_idx = filas_sel[0].row()
            return fila_idx, tabla.item(fila_idx, 0).data(Qt.UserRole)

        def _deshabilitar_seleccionada():
            fila_idx, row = _fila_actual()
            if row is None:
                return
            if row.hive == "HKLM" and not is_admin():
                self._avisar(
                    "Se requieren privilegios de administrador",
                    "Deshabilitar esta entrada (Todos los Usuarios) requiere ejecutar MASys "
                    "como administrador.",
                    QMessageBox.Warning,
                )
                return
            if not self._confirmar_reparacion(
                f"Deshabilitar programa de inicio: {row.nombre}",
                cambios=[f"Deshabilitar «{row.nombre}» en el inicio de Windows"],
                reversible=True, objetivo=row.nombre,
                explicacion=f"Motivo: {'; '.join(row.motivo_sospecha)}",
            ):
                return
            ok = self._ejecutar_toggle_programa_inicio(row, habilitar=False)
            if ok:
                tabla.removeRow(fila_idx)
                if hasattr(self, 'refresh_programas_inicio'):
                    self.refresh_programas_inicio()
                if tabla.rowCount() == 0:
                    dialog.accept()
            else:
                self._avisar("Con avisos", f"No se pudo deshabilitar «{row.nombre}».", QMessageBox.Warning)

        btn_deshabilitar.clicked.connect(_deshabilitar_seleccionada)

        botones = QDialogButtonBox(QDialogButtonBox.Close)
        botones.rejected.connect(dialog.reject)
        botones.accepted.connect(dialog.accept)
        self._agregar_boton_validar_aviso(botones, dialog, es_button_box=True)
        v.addWidget(botones)
        dialog.exec_()

    # ── Historial PS ─────────────────────────────────────────────────────

    def _ps_history_sospechosos(self):
        if not hasattr(self, 'ps_history_model'):
            return []
        return [row for row in self.ps_history_model.rows() if row.es_sospechoso]

    def _detectar_soporte_ps_history(self):
        sospechosos = self._ps_history_sospechosos()
        if not sospechosos:
            return []
        return [("ps_history_sospechosos", f"⚠️ Comandos con indicios de bypass/ofuscación: {len(sospechosos)} encontrado(s)",
                 self._soporte_ps_history_sospechosos)]

    def _soporte_ps_history_sospechosos(self):
        """Lista comandos con parámetros típicos de bypass/ofuscación
        (-EncodedCommand, -ExecutionPolicy Bypass, IEX + descarga...) —
        solo lectura: aquí no hay nada que 'reparar', solo informar. Ver
        también el checkbox 'Solo sospechosos' de la propia pestaña."""
        sospechosos = self._ps_history_sospechosos()
        if not sospechosos:
            self._avisar("Historial PS sospechoso", "Ya no hay comandos con indicios sospechosos detectados.")
            return

        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle("Comandos de PowerShell sospechosos")
        dialog.setMinimumWidth(820)
        v = QVBoxLayout(dialog)

        aviso = QLabel(
            "⚠️ Estos comandos contienen parámetros típicos de bypass/ofuscación de "
            "PowerShell (codificación, forzar política de ejecución, descarga+ejecución "
            "en memoria...). Revisa el contexto: muchos usos son legítimos (administración "
            "remota, scripts de despliegue), pero merece la pena confirmarlo."
        )
        aviso.setWordWrap(True)
        v.addWidget(aviso)

        tabla = QTableWidget(len(sospechosos), 4)
        tabla.setHorizontalHeaderLabels(["Fecha/Hora", "Origen", "Comando", "Motivo"])
        tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        tabla.setSelectionBehavior(QTableWidget.SelectRows)
        tabla.setSelectionMode(QTableWidget.SingleSelection)
        tabla.verticalHeader().setVisible(False)
        tabla.setSortingEnabled(False)
        for fila, row in enumerate(sospechosos):
            tabla.setItem(fila, 0, QTableWidgetItem(row.fecha_hora))
            tabla.setItem(fila, 1, QTableWidgetItem(row.origen))
            tabla.setItem(fila, 2, QTableWidgetItem(row.comando))
            tabla.setItem(fila, 3, QTableWidgetItem("; ".join(row.motivo_sospecha)))
        tabla.setSortingEnabled(True)
        _setup_header(tabla, 4, stretch_col=2)
        tabla.model().layoutChanged.emit()
        v.addWidget(tabla)

        botones = QDialogButtonBox(QDialogButtonBox.Close)
        botones.rejected.connect(dialog.reject)
        botones.accepted.connect(dialog.accept)
        self._agregar_boton_validar_aviso(botones, dialog, es_button_box=True)
        v.addWidget(botones)
        dialog.exec_()

    # ── Eventos Sistema ──────────────────────────────────────────────────

    def _eventos_criticos_conocidos(self):
        """Escanea self._eventos_todos (los eventos crudos, sin agrupar ni
        excluir — no self.eventos_model.rows(), que si 'Agrupar repetidos'
        está activo tendría la fuente con un sufijo '(×N)' que rompería la
        comparación exacta) en busca de las combinaciones (fuente, ID)
        conocidas y estables de apagado inesperado/BSOD."""
        eventos = getattr(self, '_eventos_todos', None) or []
        resultado = []
        for row in eventos:
            motivo = _evento_critico_conocido(row)
            if motivo:
                resultado.append((row, motivo))
        return resultado

    def _detectar_soporte_eventos_criticos(self):
        criticos = self._eventos_criticos_conocidos()
        if not criticos:
            return []
        return [("eventos_criticos", f"⚠️ Eventos críticos conocidos: {len(criticos)} encontrado(s)",
                 self._soporte_eventos_criticos)]

    def _soporte_eventos_criticos(self):
        """Puramente informativo — no hay nada que 'reparar' en un evento ya
        ocurrido. Destaca entre el ruido de la tabla las combinaciones
        (fuente, ID) más fiables y documentadas de apagado inesperado o
        pantalla azul, con la fecha de cada aparición."""
        criticos = self._eventos_criticos_conocidos()
        if not criticos:
            self._avisar("Eventos críticos conocidos", "No se han detectado eventos críticos conocidos.")
            return

        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle("Eventos críticos conocidos")
        dialog.setMinimumWidth(760)
        v = QVBoxLayout(dialog)

        aviso = QLabel(
            "⚠️ Se han detectado eventos que suelen indicar un apagado inesperado o una "
            "pantalla azul (BSOD) — señales fiables y bien documentadas, no una lista "
            "exhaustiva de todo lo que podría ir mal. Revisa las fechas: si coinciden con "
            "cortes de luz o reinicios forzados conocidos, probablemente no haga falta "
            "investigar más; si son repetidos sin motivo aparente, puede valer la pena "
            "revisar hardware (memoria, alimentación) o controladores."
        )
        aviso.setWordWrap(True)
        v.addWidget(aviso)

        tabla = QTableWidget(len(criticos), 3)
        tabla.setHorizontalHeaderLabels(["Fecha", "Motivo", "Mensaje"])
        tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        tabla.setSelectionBehavior(QTableWidget.SelectRows)
        tabla.setSelectionMode(QTableWidget.SingleSelection)
        tabla.verticalHeader().setVisible(False)
        tabla.setSortingEnabled(False)
        for fila, (row, motivo) in enumerate(criticos):
            item_fecha = QTableWidgetItem(row.fecha)
            item_fecha.setData(Qt.UserRole, row)
            tabla.setItem(fila, 0, item_fecha)
            tabla.setItem(fila, 1, QTableWidgetItem(motivo))
            tabla.setItem(fila, 2, QTableWidgetItem(row.mensaje))
        tabla.setSortingEnabled(True)
        _setup_header(tabla, 3, stretch_col=2)
        tabla.model().layoutChanged.emit()
        v.addWidget(tabla)

        fila_botones = QHBoxLayout()
        btn_copiar = QPushButton("📋 Copiar mensaje completo")
        btn_copiar.setEnabled(False)
        fila_botones.addWidget(btn_copiar)
        fila_botones.addStretch()
        v.addLayout(fila_botones)

        tabla.itemSelectionChanged.connect(
            lambda: btn_copiar.setEnabled(bool(tabla.selectionModel().selectedRows())))

        def _copiar_seleccionado():
            filas_sel = tabla.selectionModel().selectedRows()
            if not filas_sel:
                return
            row = tabla.item(filas_sel[0].row(), 0).data(Qt.UserRole)
            QApplication.clipboard().setText(row.mensaje)
            self.set_status("✅ Mensaje copiado al portapapeles")

        btn_copiar.clicked.connect(_copiar_seleccionado)

        botones = QDialogButtonBox(QDialogButtonBox.Close)
        botones.rejected.connect(dialog.reject)
        botones.accepted.connect(dialog.accept)
        self._agregar_boton_validar_aviso(botones, dialog, es_button_box=True)
        v.addWidget(botones)
        dialog.exec_()

    # ── Unidades Remotas ─────────────────────────────────────────────────

    def _unidades_con_error(self):
        """Unidades que el propio Estado ya marca como 'Error' — antes de
        corregir el chequeo de accesibilidad (Test-Path) en el colector,
        este estado nunca llegaba a producirse de verdad."""
        if not hasattr(self, 'drives_model'):
            return []
        return [row for row in self.drives_model.rows() if (row.estado or "").strip().upper() == "ERROR"]

    def _detectar_soporte_unidades_remotas(self):
        con_error = self._unidades_con_error()
        if not con_error:
            return []
        return [("unidades_remotas_error", f"🔌 Unidades de red con error: {len(con_error)} encontrada(s)",
                 self._soporte_unidades_con_error)]

    def _soporte_unidades_con_error(self):
        """Lista las unidades de red mapeadas que no responden ahora mismo
        y permite reconectarlas de una en una con 'net use', respetando si
        eran persistentes o temporales — mismo mecanismo que ya usa
        Desconectar, en sentido inverso."""
        con_error = self._unidades_con_error()
        if not con_error:
            self._avisar("Unidades de red con error", "Ya no hay unidades de red en estado de error.")
            return

        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle("Unidades de red con error")
        dialog.setMinimumWidth(680)
        v = QVBoxLayout(dialog)

        aviso = QLabel(
            "🔌 Estas unidades de red están mapeadas pero no responden ahora mismo "
            "(servidor apagado, sin conexión de red, o el recurso ya no está compartido). "
            "Reconectar vuelve a intentar el mapeo con 'net use'; puede pedir credenciales "
            "si el recurso las requiere y no están guardadas."
        )
        aviso.setWordWrap(True)
        v.addWidget(aviso)

        tabla = QTableWidget(len(con_error), 3)
        tabla.setHorizontalHeaderLabels(["Letra", "Ruta Remota", "Persistencia"])
        tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        tabla.setSelectionBehavior(QTableWidget.SelectRows)
        tabla.setSelectionMode(QTableWidget.SingleSelection)
        tabla.verticalHeader().setVisible(False)
        tabla.setSortingEnabled(False)
        for fila, row in enumerate(con_error):
            item_letra = QTableWidgetItem(row.letra)
            item_letra.setData(Qt.UserRole, row)
            tabla.setItem(fila, 0, item_letra)
            tabla.setItem(fila, 1, QTableWidgetItem(row.ruta_remota))
            tabla.setItem(fila, 2, QTableWidgetItem(row.persistente))
        tabla.setSortingEnabled(True)
        _setup_header(tabla, 3, stretch_col=1)
        tabla.model().layoutChanged.emit()
        v.addWidget(tabla)

        fila_botones = QHBoxLayout()
        btn_reconectar = QPushButton("🔌 Reconectar unidad seleccionada")
        btn_reconectar.setEnabled(False)
        fila_botones.addWidget(btn_reconectar)
        fila_botones.addStretch()
        v.addLayout(fila_botones)

        tabla.itemSelectionChanged.connect(
            lambda: btn_reconectar.setEnabled(bool(tabla.selectionModel().selectedRows())))

        def _reconectar_seleccionada():
            filas_sel = tabla.selectionModel().selectedRows()
            if not filas_sel:
                return
            fila_idx = filas_sel[0].row()
            row = tabla.item(fila_idx, 0).data(Qt.UserRole)

            if not self._confirmar_reparacion(
                f"Reconectar unidad: {row.letra}",
                cambios=[f"Volver a mapear «{row.letra}» a «{row.ruta_remota}»"],
                reversible=True, objetivo=row.letra,
                explicacion="Puede pedir credenciales si el recurso las requiere y no están guardadas.",
            ):
                return

            ok = self._ejecutar_reconectar_unidad(row, seccion_origen="💾 Unidades Remotas")
            if ok:
                tabla.removeRow(fila_idx)
                if tabla.rowCount() == 0:
                    dialog.accept()

        btn_reconectar.clicked.connect(_reconectar_seleccionada)

        botones = QDialogButtonBox(QDialogButtonBox.Close)
        botones.rejected.connect(dialog.reject)
        botones.accepted.connect(dialog.accept)
        self._agregar_boton_validar_aviso(botones, dialog, es_button_box=True)
        v.addWidget(botones)
        dialog.exec_()

    # ── Contenido ──────────────────────────────────────────────────────

    def _soporte_papelera_reciclaje(self):
        """Abre el gestor de la Papelera de Reciclaje de Windows: ver
        contenido, restaurar elementos seleccionados a su ubicación
        original, o vaciarla por completo."""
        dialog = RecycleBinDialog(self)
        dialog.exec_()

    # ── Discos Duros ─────────────────────────────────────────────────

    def _detectar_soporte_discos(self):
        """Detector con dos criterios independientes sobre los datos YA
        CARGADOS: discos físicos con Estado de Salud no óptimo (mismo
        criterio de texto que usa DiscoFisicoTableModel para colorear la
        celda), y particiones con menos de un 20% libre (mismo umbral que
        el semáforo de la columna % Uso, en rojo a partir de 90 y en
        amarillo desde 75 — aquí se dispara desde 80, que es donde ya deja
        de estar en verde)."""
        if not hasattr(self, 'disk_inventory_model'):
            return []
        acciones = []
        for row in self.disk_inventory_model.rows():
            estado = row.estado_salud.strip().lower()
            if not estado or estado == "n/a" or "healthy" in estado or "correcto" in estado:
                continue
            etiqueta = f"⚠️ {row.disco} en estado «{row.estado_salud}» — revisar"
            clave = f"disco_riesgo::{row.disco}"
            acciones.append((clave, etiqueta, lambda r=row: self._soporte_disco_en_riesgo(r)))

        if hasattr(self, 'partitions_model'):
            unidad_sistema = os.environ.get("SystemDrive", "C:").upper()
            for row in self.partitions_model.rows():
                try:
                    usado_pct = float(row.porcentaje.strip().rstrip("%").replace(",", "."))
                except ValueError:
                    continue
                if usado_pct < 80:
                    continue
                letra = row.letra.strip().upper()
                libre_pct = round(100 - usado_pct, 1)
                if letra == unidad_sistema:
                    etiqueta = f"🧹 Limpiar partición del sistema {row.letra} ({libre_pct}% libre)"
                    acciones.append((f"particion_limpiar::{row.letra}", etiqueta, self._soporte_limpiar_particion_sistema))
                else:
                    etiqueta = f"🧹 Limpiar partición de datos {row.letra} ({libre_pct}% libre)"
                    acciones.append((f"particion_limpiar::{row.letra}", etiqueta, lambda l=row.letra: self._soporte_limpiar_particion_datos(l)))
        return acciones

    def _soporte_disco_en_riesgo(self, row):
        """Puramente informativo (como 'Ver historial de cambios'): no
        modifica nada, solo reúne en un aviso los datos ya cargados
        (estado + telemetría S.M.A.R.T. si el proveedor la expone) y
        sugiere el siguiente paso — CHKDSK para el sistema de archivos,
        que es un diagnóstico distinto del estado de salud del hardware."""
        detalle = (
            f"{row.disco} — {row.modelo}\n\n"
            f"Estado de salud: {row.estado_salud}\n"
            f"Estado operativo: {row.estado_operativo}\n"
            f"Temperatura: {row.temperatura}\n"
            f"Desgaste (SSD): {row.desgaste}\n"
            f"Horas de encendido: {row.horas_encendido}\n"
            f"Errores no corregidos: {row.errores}\n\n"
            "Esto es una ALERTA a revisar, NO una confirmación de fallo inminente: "
            "considera hacer copia de seguridad de los datos de este disco. "
            "«🔧 Verificar disco (CHKDSK)» comprueba el sistema de archivos, que es "
            "un diagnóstico distinto del estado de salud del hardware que se "
            "muestra aquí."
        )
        self._avisar(f"Disco en riesgo: {row.disco}", detalle, QMessageBox.Warning)

    def _soporte_verificar_disco(self):
        """Ejecuta CHKDSK sobre el volumen elegido. Solo lectura por
        defecto (sin /f): no bloquea la unidad, puede ejecutarse con ella
        en uso. Con 'Reparar automáticamente' (/f) o 'Buscar sectores
        defectuosos' (/r, que implica /f) CHKDSK necesita bloqueo
        exclusivo — si el volumen está en uso (típicamente el del
        sistema), Windows programa la comprobación para el próximo
        reinicio en vez de ejecutarla ahora (ver CHKDSKRepairThread)."""
        if not is_admin():
            self._avisar(
                "Verificar disco (CHKDSK)",
                "Se requieren privilegios de administrador para ejecutar CHKDSK.\n\n"
                "Reinicia MASys como administrador.",
                QMessageBox.Warning,
            )
            return

        letras = ([row.letra.rstrip(":").strip() for row in self.partitions_model.rows()]
                  if hasattr(self, 'partitions_model') else [])
        letras = [l for l in letras if l]
        if not letras:
            self._avisar("Verificar disco (CHKDSK)",
                          "No hay particiones cargadas. Actualiza la sección "
                          "«💽 Discos Duros» primero.")
            return

        c = self._dash_colors()
        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle("Verificar disco (CHKDSK)")
        dialog.setMinimumWidth(480)
        v = QVBoxLayout(dialog)
        v.setSpacing(14)
        v.setContentsMargins(22, 20, 22, 18)

        # ── Cabecera: icono + título + subtítulo, mismo patrón que
        # _confirmar_reparacion — no un bloque de texto plano.
        cabecera = QHBoxLayout()
        cabecera.setSpacing(12)
        lbl_icono = QLabel("🔎")
        lbl_icono.setStyleSheet("font-size: 26pt; background: transparent; border: none;")
        cabecera.addWidget(lbl_icono)
        col_titulo = QVBoxLayout()
        col_titulo.setSpacing(2)
        lbl_titulo = QLabel("Verificar disco (CHKDSK)")
        lbl_titulo.setWordWrap(True)
        lbl_titulo.setStyleSheet(
            f"font-size: 13pt; font-weight: 600; color: {c['title']}; background: transparent; border: none;")
        col_titulo.addWidget(lbl_titulo)
        lbl_subtitulo = QLabel("Elige la unidad y el modo de verificación")
        lbl_subtitulo.setWordWrap(True)
        lbl_subtitulo.setStyleSheet(
            f"color: {c['muted']}; font-size: 9.5pt; background: transparent; border: none;")
        col_titulo.addWidget(lbl_subtitulo)
        cabecera.addLayout(col_titulo, 1)
        v.addLayout(cabecera)

        v.addWidget(QLabel("Unidad a verificar:"))
        combo = QComboBox()
        combo.addItems(letras)
        v.addWidget(combo)

        chk_reparar = QCheckBox("Reparar errores automáticamente (/f)")
        v.addWidget(chk_reparar)
        chk_sectores = QCheckBox("Buscar e intentar recuperar sectores defectuosos "
                                  "(/r, más lento, implica /f)")
        v.addWidget(chk_sectores)

        # ── Tarjeta con una viñeta por dato, mismo patrón que la tarjeta
        # de "cambios" de _confirmar_reparacion — no un QLabel de aviso suelto.
        marco_aviso = QFrame()
        marco_aviso.setStyleSheet(
            f"QFrame {{ background: {c['alerts_bg']}; border: 1px solid {c['alerts_border']}; border-radius: 8px; }}")
        v_aviso = QVBoxLayout(marco_aviso)
        v_aviso.setContentsMargins(14, 10, 14, 10)
        v_aviso.setSpacing(6)
        for punto in (
            "Sin marcar nada: solo lectura, sin bloquear la unidad",
            "Con /f o /r: si la unidad está en uso (p. ej. la del sistema), Windows "
            "programará la comprobación para el próximo reinicio en vez de ejecutarla ahora",
        ):
            fila = QHBoxLayout()
            fila.setSpacing(8)
            lbl_bullet = QLabel("•")
            lbl_bullet.setAlignment(Qt.AlignTop)
            lbl_bullet.setStyleSheet(f"color: {c['alerts_text']}; font-weight: 700; background: transparent; border: none;")
            fila.addWidget(lbl_bullet)
            lbl_texto = QLabel(punto)
            lbl_texto.setWordWrap(True)
            lbl_texto.setStyleSheet(f"color: {c['alerts_text']}; background: transparent; border: none;")
            fila.addWidget(lbl_texto, 1)
            v_aviso.addLayout(fila)
        v.addWidget(marco_aviso)

        botones = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        botones.accepted.connect(dialog.accept)
        botones.rejected.connect(dialog.reject)
        v.addWidget(botones)

        if dialog.exec_() != QDialog.Accepted:
            return

        letra = combo.currentText()
        reparar = chk_reparar.isChecked()
        sectores = chk_sectores.isChecked()

        if sectores:
            cambios = ["Ejecutar CHKDSK con /r: busca sectores defectuosos e intenta "
                       "recuperar la información legible (implica /f)"]
        elif reparar:
            cambios = ["Ejecutar CHKDSK con /f: repara errores del sistema de archivos "
                       "automáticamente"]
        else:
            cambios = ["Ejecutar CHKDSK en modo solo lectura: no modifica nada, no "
                       "bloquea la unidad"]

        if not self._confirmar_reparacion(
            "Verificar disco (CHKDSK)",
            cambios=cambios,
            reversible=not (reparar or sectores),
            objetivo=f"{letra}:",
            explicacion=(
                "Si la unidad está en uso, Windows programará la comprobación para "
                "el próximo reinicio en vez de ejecutarla ahora."
                if (reparar or sectores) else None
            ),
        ):
            return

        dlg = CHKDSKDialog(letra, reparar, sectores, self)
        dlg.exec_()

        exito = bool(dlg.resultado_exito)
        detalle = dlg.resultado_log or "Detenido antes de finalizar"
        if dlg.resultado_programado:
            detalle += "\n\n[Programado para el próximo reinicio: la unidad estaba en uso]"

        self.repair_audit_db.registrar(
            seccion="💽 Discos Duros", accion=f"Verificar disco (CHKDSK) {letra}:",
            objetivo=f"{letra}:", exito=exito, detalle=detalle,
        )

    def _soporte_limpiar_particion_sistema(self):
        """Limpieza de la partición de sistema — puerto de Start-DiskCleanup
        (Mantenimientov2.ps1) simplificado, ver SystemCleanupThread para el
        detalle exacto. Sin privilegios de administrador, powercfg/pnputil/
        DriverStore fallan en silencio y la limpieza queda incompleta, así
        que se exige igual que en CHKDSK/SFC-DISM."""
        if not is_admin():
            self._avisar(
                "Limpieza de partición de sistema",
                "Se requieren privilegios de administrador para esta limpieza "
                "(hibernación, DriverStore, Delivery Optimization).\n\n"
                "Reinicia MASys como administrador.",
                QMessageBox.Warning,
            )
            return

        unidad_sistema = os.environ.get("SystemDrive", "C:")
        if not self._confirmar_reparacion(
            "Limpieza de partición de sistema",
            cambios=[
                "Temporales y cachés de navegador/apps de TODOS los perfiles de usuario",
                "Hibernación (si estaba activa), Prefetch (solo en discos SSD)",
                "Windows.old — ya no se podrá revertir a la versión anterior de Windows",
                "Versiones antiguas duplicadas del DriverStore (sin /force: nunca una en uso)",
                "Caché de Delivery Optimization, logs CBS archivados (+180 días)",
                "Papelera de reciclaje (todas las unidades fijas)",
            ],
            reversible=False,
            objetivo=f"{unidad_sistema}\\",
            explicacion=(
                "No incluye DeepClean/ResetBase de WinSxS (irreversible, fuera de "
                "alcance) ni compresión de sistema. Es una operación atómica: no se "
                "puede detener a mitad una vez iniciada."
            ),
        ):
            return

        dlg = SystemCleanupDialog(self)
        dlg.exec_()

        if dlg.resultado is not None:
            categorias = dlg.resultado.get("categorias", {})
            liberado = dlg.resultado.get("espacio_liberado", 0)
            detalle = f"Espacio recuperado: {liberado / (1024**3):.2f} GB — " + \
                      "; ".join(f"{k}: {v}" for k, v in categorias.items())
            exito = True
        else:
            detalle = dlg.resultado_error or "Error desconocido"
            exito = False

        self.repair_audit_db.registrar(
            seccion="💽 Discos Duros", accion="Limpieza de partición de sistema",
            objetivo=f"{unidad_sistema}\\", exito=exito, detalle=detalle,
        )
        if exito:
            self.set_status(f"✅ Limpieza de {unidad_sistema}\\ completada")

    def _soporte_limpiar_particion_datos(self, letra: str):
        """Limpieza 'adaptada' para una partición de datos: papelera de
        esa unidad + archivos y carpetas basura seguros — nada de
        temporales/cachés de sistema, que no existen fuera de la unidad
        de Windows. Ver DataDriveCleanupThread."""
        if not self._confirmar_reparacion(
            f"Limpieza de partición de datos {letra}",
            cambios=[
                f"Vaciar la papelera de reciclaje de {letra}",
                "Archivos basura: *.tmp, Thumbs.db/.DS_Store, bloqueos de Office "
                "huérfanos (~$*.doc/xls/ppt), restos de macOS (._*)",
                "Cachés regenerables de macOS/Synology completas: .Spotlight-V100, "
                ".fseventsd, .TemporaryItems, @eaDir",
                "Papeleras residuales de otros sistemas: .Trashes, #recycle, RECYCLER",
                "Historial de GoodSync (_gsdata_\\_saved_ y _history_) — el estado de "
                "sincronización activo NUNCA se toca",
            ],
            reversible=False,
            objetivo=f"{letra}\\",
            explicacion=(
                "No se toca ningún archivo propio del usuario, la carpeta 'System "
                "Volume Information' (Protección del sistema de esa unidad), ni el "
                "resto de _gsdata_ (necesario para que GoodSync siga sincronizando "
                "sin tener que re-escanear todo desde cero)."
            ),
        ):
            return

        dlg = DataDriveCleanupDialog(letra, self)
        dlg.exec_()

        if dlg.resultado is not None:
            categorias = dlg.resultado.get("categorias", {})
            liberado = dlg.resultado.get("espacio_liberado", 0)
            detalle = f"Espacio recuperado: {liberado / (1024**3):.2f} GB — " + \
                      ("; ".join(f"{k}: {v}" for k, v in categorias.items()) or "nada que limpiar")
            exito = True
        else:
            detalle = dlg.resultado_error or "Detenido antes de finalizar"
            exito = False

        self.repair_audit_db.registrar(
            seccion="💽 Discos Duros", accion=f"Limpieza de partición de datos {letra}",
            objetivo=f"{letra}\\", exito=exito, detalle=detalle,
        )
        if exito:
            self.set_status(f"✅ Limpieza de {letra}\\ completada")

    def _soporte_buscar_duplicados(self):
        """Busca archivos duplicados (idénticos byte a byte, confirmado
        por hash — no solo por tamaño) de 500 MB o más, en la unidad o
        carpeta que se elija. No borra nada automáticamente: el propio
        diálogo de resultados (DuplicateFilesDialog) es donde el técnico
        marca qué copias concretas enviar a la papelera."""
        carpeta = dialogo_elegir_carpeta(self, "Elegir unidad o carpeta a analizar", "C:\\")
        if not carpeta:
            return

        dlg = DuplicateFilesDialog(carpeta, 500 * 1024 * 1024, self)
        dlg.exec_()

    # ── WiFi ─────────────────────────────────────────────────────────

    def _detectar_soporte_wifi(self):
        """Detector: redes WiFi guardadas (ya cargadas en la tabla) con
        seguridad débil (abierta/WEP) — mismo criterio que WiFiTableModel
        usa para colorear en rojo la celda de Autenticación. Reutiliza
        directamente _wifi_bloquear/_wifi_olvidar de WiFiMixin (mismo
        'self': SoporteMixin y WiFiMixin son dos mixins de la misma
        instancia de ShareInspector) en vez de duplicar la lógica aquí."""
        if not hasattr(self, 'wifi_model'):
            return []
        acciones = []
        for row in self.wifi_model.rows():
            if not row.es_seguridad_debil:
                continue
            acciones.append((f"wifi_bloquear::{row.nombre}", f"🚫 Bloquear red insegura «{row.nombre}»",
                              lambda r=row: self._wifi_bloquear(r)))
            acciones.append((f"wifi_olvidar::{row.nombre}", f"✕ Olvidar red insegura «{row.nombre}»",
                              lambda r=row: self._wifi_olvidar(r)))
        return acciones

    # ── Conexiones Red ───────────────────────────────────────────────

    def _detectar_soporte_conexiones_red(self):
        """Detector: conexiones/puertos (ya cargados en la tabla) cuyo
        proceso está disfrazado — nombre de proceso crítico del sistema
        corriendo fuera de su carpeta real, con una conexión de red o
        puerto en escucha ACTIVO ahora mismo. Más grave que el mismo aviso
        en Procesos: aquí implica tráfico/escucha en curso, no solo
        presencia en la lista de procesos."""
        if not hasattr(self, 'connections_model'):
            return []
        acciones = []
        vistos = set()
        for row in self.connections_model.rows():
            if not row.es_disfrazado:
                continue
            clave = (row.proceso, row.pid)
            if clave in vistos:
                continue
            vistos.add(clave)
            etiqueta = f"🚨 Proceso disfrazado con red activa: «{row.proceso}» (PID {row.pid})"
            clave_conexion = f"conexion_disfrazada::{row.proceso}::{row.pid}"
            acciones.append((clave_conexion, etiqueta, lambda r=row: self._soporte_conexion_disfrazada(r)))
        return acciones

    def _soporte_conexion_disfrazada(self, row):
        """Informativo, como el resto de detectores de riesgo de
        seguridad: reúne los datos y sugiere el siguiente paso, sin actuar
        automáticamente — terminar el proceso o bloquear la IP son
        decisiones del técnico, disponibles en el menú contextual de
        Conexiones Red."""
        detalle = (
            f"Proceso: {row.proceso} (PID {row.pid})\n"
            f"Ruta real: {row.exe_path}\n"
            f"Conexión: {row.protocolo} {row.direccion_local} → {row.direccion_remota} ({row.estado})\n\n"
            "POSIBLE PERSISTENCIA DE MALWARE: nombre de proceso crítico del sistema "
            "(svchost.exe, csrss.exe...) ejecutándose fuera de su carpeta real, con una "
            "conexión de red o puerto en escucha activo ahora mismo.\n\n"
            "Esto es una ALERTA a revisar, NO una confirmación de infección: identifica "
            "qué es antes de actuar. Puedes finalizar el proceso o bloquear la IP remota "
            "desde el menú contextual de la pestaña 🌐 Conexiones Red."
        )
        self._avisar(f"Conexión sospechosa: {row.proceso}", detalle, QMessageBox.Warning)

    # ── Exportación de tablas ad-hoc de Soporte (CSV/HTML) ────────────
    # Mismo formato (CSV ';' + utf-8-sig, HTML con la plantilla degradado
    # morado) que ya usan export_*_csv/html en el resto de pestañas —
    # aquí exporta el contenido ACTUAL de un QTableWidget de un diálogo
    # de Soporte (p. ej. tras marcar filas como aceptadas/comprobadas),
    # no una tabla-modelo persistente.

    def _exportar_tabla_csv(self, tabla: QTableWidget, headers: List[str], nombre_base: str, col_offset: int = 0):
        """col_offset: nº de columnas iniciales del QTableWidget que no
        forman parte de 'headers' (p. ej. una columna de casilla de
        marcado en la posición 0) y que se saltan al leer."""
        if tabla.rowCount() == 0:
            self._avisar("Exportar CSV", "No hay filas para exportar.")
            return
        default_name = f"{nombre_base}_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        path = dialogo_guardar_archivo(self, "Guardar CSV", ruta_guardado_predeterminada(default_name), "CSV (*.csv)")
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = _SafeCsvWriter(csv.writer(f, delimiter=";"))
                writer.writerow(headers)
                for r in range(tabla.rowCount()):
                    writer.writerow([tabla.item(r, c + col_offset).text() if tabla.item(r, c + col_offset) else ""
                                      for c in range(len(headers))])
            self.set_status(f"✅ Exportado: {os.path.basename(path)} ({tabla.rowCount()} filas)")
            self._avisar("Éxito", f"Exportadas {tabla.rowCount()} filas a:\n\n{path}")
        except Exception as e:
            self._avisar("Error", f"No se pudo exportar:\n\n{e}", QMessageBox.Critical)

    def _exportar_tabla_html(self, tabla: QTableWidget, headers: List[str], nombre_base: str, titulo: str,
                              col_offset: int = 0):
        """Tabla de un diálogo de Soporte con la plantilla común de informes (msys/core/informe_html.py)."""
        if tabla.rowCount() == 0:
            self._avisar("Exportar HTML", "No hay filas para exportar.")
            return
        default_name = f"{nombre_base}_{time.strftime('%Y%m%d_%H%M%S')}.html"
        path = dialogo_guardar_archivo(self, "Guardar HTML", ruta_guardado_predeterminada(default_name), "HTML (*.html)")
        if not path:
            return
        rows_data = [
            [tabla.item(r, c + col_offset).text() if tabla.item(r, c + col_offset) else "" for c in range(len(headers))]
            for r in range(tabla.rowCount())
        ]
        html = informe_html.documento(titulo, [informe_html.Seccion(titulo, headers, rows_data)], indice=False)
        self._guardar_informe_html(path, html, f"{len(rows_data)} filas.", f"✅ Exportado: {os.path.basename(path)} ({len(rows_data)} filas)")

    # ── Firewall ─────────────────────────────────────────────────────

    def _detectar_soporte_firewall(self):
        """Detector con dos criterios independientes, ninguno de los dos
        disparaba ninguna alerta antes (solo se veían mirando los badges/
        la tabla a mano): perfiles con el Firewall DESACTIVADO (el control
        más importante de todos — si un perfil está desactivado, ninguna
        regla de ese perfil importa) y reglas de entrada Any/Any activas
        (es_regla_amplia, ya calculado en el modelo)."""
        acciones = []
        for perfil in getattr(self, 'firewall_perfiles', []):
            if not perfil.habilitado:
                etiqueta = f"🚨 Firewall DESACTIVADO en el perfil «{perfil.nombre_es}»"
                acciones.append((f"firewall_perfil_desactivado::{perfil.nombre_es}", etiqueta,
                                  lambda p=perfil: self._toggle_perfil_firewall(p)))

        if hasattr(self, 'firewall_model'):
            amplias = [r for r in self.firewall_model.rows() if r.es_regla_amplia
                       and not self.firewall_rule_whitelist_db.is_whitelisted(r.nombre_interno)]
            if amplias:
                etiqueta = f"⚠️ {len(amplias)} regla(s) de entrada Any/Any activa(s) — revisar"
                acciones.append(("firewall_reglas_amplias", etiqueta, self._soporte_revisar_reglas_amplias))
        return acciones

    def _soporte_revisar_reglas_amplias(self):
        """Lista interactiva de reglas amplias (Any/Any), con acción por
        fila: cambiar a Bloquear (reusa _firewall_cambiar_accion de
        FirewallMixin — mismo confirm+PowerShell+actualización de tabla que
        el menú contextual de la pestaña Firewall) o aceptar la regla como
        legítima para dejar de avisar de ella (FirewallRuleWhitelistDB,
        reversible desde 'Ver reglas de firewall aceptadas'). No incluye
        las ya aceptadas."""
        amplias = [r for r in self.firewall_model.rows() if r.es_regla_amplia
                   and not self.firewall_rule_whitelist_db.is_whitelisted(r.nombre_interno)]
        if not amplias:
            self._avisar("Reglas amplias", "Ya no hay reglas Any/Any activas por revisar.")
            return

        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle("Reglas de firewall amplias (Any/Any)")
        dialog.setMinimumWidth(780)
        v = QVBoxLayout(dialog)

        aviso = QLabel(
            "⚠️ Reglas de ENTRADA activas que permiten cualquier puerto y protocolo "
            "(Any/Any) — el patrón de riesgo más habitual a auditar en un firewall.\n\n"
            "Selecciona una o varias filas (Ctrl/Shift + clic, o 'Seleccionar todo') y elige "
            "una acción."
        )
        aviso.setWordWrap(True)
        v.addWidget(aviso)

        tabla = QTableWidget(len(amplias), 4)
        tabla.setHorizontalHeaderLabels(["Nombre", "Perfil", "Programa", "Origen"])
        tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        tabla.setSelectionBehavior(QTableWidget.SelectRows)
        tabla.setSelectionMode(QTableWidget.ExtendedSelection)
        tabla.verticalHeader().setVisible(False)
        tabla.setSortingEnabled(False)
        for fila, r in enumerate(amplias):
            item_nombre = QTableWidgetItem(r.nombre)
            item_nombre.setData(Qt.UserRole, r)
            tabla.setItem(fila, 0, item_nombre)
            tabla.setItem(fila, 1, QTableWidgetItem(r.perfil_es))
            tabla.setItem(fila, 2, QTableWidgetItem(r.programa))
            tabla.setItem(fila, 3, QTableWidgetItem("Sistema (Windows)" if r.es_sistema else "Terceros"))
        tabla.setSortingEnabled(True)
        _setup_header(tabla, 4, stretch_col=0)
        tabla.model().layoutChanged.emit()
        v.addWidget(tabla)

        def _filas_marcadas():
            return [(idx.row(), tabla.item(idx.row(), 0).data(Qt.UserRole))
                    for idx in tabla.selectionModel().selectedRows()]

        fila_botones = QHBoxLayout()
        btn_bloquear = QPushButton("⛔ Cambiar a Bloquear")
        btn_bloquear.setEnabled(False)
        fila_botones.addWidget(btn_bloquear)
        btn_aceptar = QPushButton("✅ Aceptar (no volver a avisar)")
        btn_aceptar.setEnabled(False)
        fila_botones.addWidget(btn_aceptar)
        fila_botones.addStretch()
        btn_csv = QPushButton("📄 CSV")
        btn_csv.clicked.connect(lambda: self._exportar_tabla_csv(
            tabla, ["Nombre", "Perfil", "Programa", "Origen"], "Reglas_Firewall_Amplias"))
        fila_botones.addWidget(btn_csv)
        btn_html = QPushButton("🌐 HTML")
        btn_html.clicked.connect(lambda: self._exportar_tabla_html(
            tabla, ["Nombre", "Perfil", "Programa", "Origen"], "Reglas_Firewall_Amplias",
            "Reglas de Firewall Amplias (Any/Any)"))
        fila_botones.addWidget(btn_html)
        v.addLayout(fila_botones)

        def _actualizar_botones():
            hay_marcadas = bool(_filas_marcadas())
            btn_bloquear.setEnabled(hay_marcadas)
            btn_aceptar.setEnabled(hay_marcadas)

        tabla.itemSelectionChanged.connect(_actualizar_botones)

        def _bloquear_marcadas():
            marcadas = _filas_marcadas()
            if not marcadas:
                return
            if not is_admin():
                self._avisar("Firewall",
                             "Se requieren permisos de administrador para modificar reglas "
                             "de firewall.\n\nReinicia MASys como administrador.",
                             QMessageBox.Warning)
                return
            if not self._confirmar_reparacion(
                f"Bloquear {len(marcadas)} regla(s) de firewall",
                cambios=[f"«{regla.nombre}»" for _fila, regla in marcadas],
                reversible=True, objetivo=f"{len(marcadas)} regla(s)",
                explicacion="Cada regla pasará de Permitir a Bloquear.",
            ):
                return
            ok_total = 0
            for fila, regla in sorted(marcadas, key=lambda t: -t[0]):
                if not regla.nombre_interno:
                    continue
                nombre_escapado = escape_powershell_string(regla.nombre_interno)
                ok, error = run_powershell_command(
                    f"$r = @(Get-NetFirewallRule | Where-Object {{ $_.Name -eq '{nombre_escapado}' }}); if ($r.Count -ne 1) {{ throw 'No se encuentra la regla' }}; $r[0] | Set-NetFirewallRule -Action Block")
                self.repair_audit_db.registrar(
                    seccion="🔥 Firewall", accion="Cambiar a Bloquear (revisión de reglas amplias)",
                    objetivo=regla.nombre, exito=ok, detalle=error if not ok else "OK",
                )
                if ok:
                    regla.accion = "Bloquear"
                    ok_total += 1
                    tabla.removeRow(fila)
            if hasattr(self, 'firewall_model'):
                self.firewall_model.setRows(self.firewall_model.rows())
                if hasattr(self, '_update_firewall_counter'):
                    self._update_firewall_counter()
            self.set_status(f"✅ {ok_total}/{len(marcadas)} regla(s) cambiadas a Bloquear")
            if tabla.rowCount() == 0:
                dialog.accept()

        def _aceptar_marcadas():
            marcadas = _filas_marcadas()
            if not marcadas:
                return
            if not self._confirmar_reparacion(
                f"Aceptar {len(marcadas)} regla(s) de firewall",
                cambios=[f"«{regla.nombre}»" for _fila, regla in marcadas],
                reversible=True, objetivo=f"{len(marcadas)} regla(s)",
                explicacion=(
                    "Las reglas siguen activas tal cual están (Any/Any Permitir) — solo se "
                    "deja de avisar de ellas aquí. Puedes deshacerlo desde 'Ver reglas de "
                    "firewall aceptadas'."
                ),
            ):
                return
            aceptadas = 0
            fallidas = 0
            for fila, regla in sorted(marcadas, key=lambda t: -t[0]):
                if not regla.nombre_interno:
                    continue
                if self.firewall_rule_whitelist_db.add(
                        regla.nombre_interno, regla.nombre, "Aceptada desde revisión de reglas amplias"):
                    aceptadas += 1
                    tabla.removeRow(fila)
                else:
                    fallidas += 1
            self.set_status(f"✅ {aceptadas} regla(s) aceptadas — no volverán a avisar")
            if fallidas:
                self._avisar("Aceptar reglas",
                             f"{fallidas} regla(s) no se pudieron guardar (fallo de base de datos local). "
                             "Vuelve a intentarlo.", QMessageBox.Warning)
            if tabla.rowCount() == 0:
                dialog.accept()

        btn_bloquear.clicked.connect(_bloquear_marcadas)
        btn_aceptar.clicked.connect(_aceptar_marcadas)

        fila_inferior = QHBoxLayout()
        btn_seleccionar_todo = QPushButton("☑ Seleccionar todo")
        btn_seleccionar_todo.clicked.connect(tabla.selectAll)
        fila_inferior.addWidget(btn_seleccionar_todo)
        btn_deseleccionar_todo = QPushButton("☐ Deseleccionar todo")
        btn_deseleccionar_todo.clicked.connect(tabla.clearSelection)
        fila_inferior.addWidget(btn_deseleccionar_todo)
        fila_inferior.addStretch()
        self._agregar_boton_validar_aviso(fila_inferior, dialog)
        btn_cerrar = QPushButton("Cerrar")
        btn_cerrar.clicked.connect(dialog.accept)
        fila_inferior.addWidget(btn_cerrar)
        v.addLayout(fila_inferior)

        dialog.exec_()

    def _soporte_ver_firewall_whitelist(self):
        """Gestión de reglas de firewall aceptadas: verlas y quitarlas (para
        que el detector vuelva a avisar) — mismo patrón que 'Ver procesos
        marcados como legítimos'."""
        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle("Reglas de firewall aceptadas")
        dialog.setMinimumSize(720, 400)
        v = QVBoxLayout(dialog)

        tabla = QTableWidget(0, 3)
        tabla.setHorizontalHeaderLabels(["Regla", "Motivo", "Aceptada el"])
        tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        tabla.setSelectionBehavior(QTableWidget.SelectRows)
        tabla.setSelectionMode(QTableWidget.SingleSelection)
        tabla.verticalHeader().setVisible(False)
        _setup_header(tabla, 3, stretch_col=0)
        v.addWidget(tabla)

        lbl_vacio = QLabel("No hay ninguna regla de firewall aceptada todavía.")
        lbl_vacio.setVisible(False)
        v.addWidget(lbl_vacio)

        def _recargar():
            entradas = self.firewall_rule_whitelist_db.get_all()
            tabla.setSortingEnabled(False)
            tabla.setRowCount(len(entradas))
            for fila, entrada in enumerate(entradas):
                nombre_interno, nombre_visible, motivo, aceptada_el = entrada
                for col, valor in enumerate([nombre_visible or nombre_interno, motivo, aceptada_el]):
                    item = QTableWidgetItem(valor)
                    if col == 0:
                        item.setData(Qt.UserRole, entrada)
                    tabla.setItem(fila, col, item)
            tabla.setSortingEnabled(True)
            tabla.model().layoutChanged.emit()
            lbl_vacio.setVisible(not entradas)
            btn_quitar.setEnabled(False)

        fila_botones = QHBoxLayout()
        btn_quitar = QPushButton("✕ Quitar de la lista (volver a avisar)")
        btn_quitar.setEnabled(False)
        fila_botones.addWidget(btn_quitar)
        fila_botones.addStretch()
        v.addLayout(fila_botones)

        tabla.itemSelectionChanged.connect(
            lambda: btn_quitar.setEnabled(bool(tabla.selectionModel().selectedRows())))

        def _quitar_seleccionado():
            filas_sel = tabla.selectionModel().selectedRows()
            if not filas_sel:
                return
            nombre_interno, _nombre_visible, _motivo, _aceptada_el = tabla.item(filas_sel[0].row(), 0).data(Qt.UserRole)
            if self.firewall_rule_whitelist_db.remove(nombre_interno):
                self.set_status("✅ Regla quitada de la lista de aceptadas")
            else:
                self._avisar("Quitar de la lista",
                             "No se pudo quitar la regla (fallo de base de datos local). Vuelve a intentarlo.",
                             QMessageBox.Warning)
            _recargar()

        btn_quitar.clicked.connect(_quitar_seleccionado)

        _recargar()

        fila_cerrar = QHBoxLayout()
        fila_cerrar.addStretch()
        btn_cerrar = QPushButton("Cerrar")
        btn_cerrar.clicked.connect(dialog.accept)
        fila_cerrar.addWidget(btn_cerrar)
        v.addLayout(fila_cerrar)
        dialog.exec_()

    # Puertos habitualmente usados por Windows/servicios comunes — lista
    # HEURÍSTICA, no oficial ni exhaustiva. Un puerto ausente de aquí no es
    # en sí mismo sospechoso, solo menos habitual y merece una mirada.
    _PUERTOS_COMUNES = {
        20, 21, 22, 23, 25, 53, 67, 68, 80, 88, 110, 111, 123, 135, 137, 138, 139,
        143, 161, 162, 389, 443, 445, 464, 465, 500, 515, 587, 593, 636, 989, 990,
        993, 995, 1433, 1434, 1723, 3268, 3269, 3306, 3389, 4500, 5060, 5061, 5353,
        5432, 5985, 5986, 8080, 8443,
    }
    # Puertos efímeros/dinámicos de Windows (rango IANA 49152-65535) abiertos
    # por un proceso propio del sistema: RPC/DCOM interno completamente
    # normal (lsass/wininit/svchost/spoolsv...), no algo que investigar —
    # sin este filtro, esos puertos enterraban entre 5 y 10 coincidencias
    # de ruido por cada hallazgo real (AnyDesk, GoodSync, Tailscale...).
    _RANGO_DINAMICO_MIN = 49152
    _PROCESOS_SISTEMA_PUERTOS = {
        "svchost", "system", "lsass", "wininit", "services", "csrss",
        "smss", "spoolsv", "dwm", "winlogon", "fontdrvhost", "memcompression",
    }

    def _soporte_comprobar_puerto(self):
        """Comprueba si algo escucha localmente (TCP y UDP) en el puerto
        indicado y qué reglas de firewall lo mencionan explícitamente. Es
        el estado LOCAL de este equipo — no comprueba si el puerto es
        alcanzable de verdad desde Internet, eso depende también del
        router/NAT y queda fuera de lo que se puede saber desde aquí."""
        dlg = QInputDialog(self)
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dlg.setWindowTitle("Comprobar puerto")
        dlg.setLabelText("Número de puerto a comprobar (1-65535):")
        dlg.setInputMode(QInputDialog.TextInput)
        aceptado = dlg.exec_() == QDialog.Accepted
        texto = dlg.textValue()
        if not aceptado or not texto.strip():
            return
        try:
            puerto = int(texto.strip())
            if not (1 <= puerto <= 65535):
                raise ValueError
        except ValueError:
            self._avisar("Comprobar puerto", "Introduce un número de puerto válido (1-65535).", QMessageBox.Warning)
            return

        self.set_status(f"🔄 Comprobando puerto {puerto}...")
        QApplication.processEvents()
        data = run_powershell_json(f"$puerto = {puerto}\n" + PS_COMPROBAR_PUERTO, timeout=30)
        self.set_status("")

        if data is None:
            self._avisar("Comprobar puerto", "No se pudo completar la comprobación.", QMessageBox.Warning)
            return
        if not isinstance(data, dict):
            data = {}

        tcp = data.get("TCP") or {}
        udp = data.get("UDP") or {}
        reglas = data.get("ReglasFirewall") or []
        if isinstance(reglas, dict):
            reglas = [reglas]

        resumen = []
        if tcp.get("Escuchando"):
            resumen.append(f"TCP: ESCUCHANDO — proceso «{tcp.get('Proceso', 'N/A')}» (PID {tcp.get('PID', '?')})")
        else:
            resumen.append("TCP: cerrado (nada escuchando localmente)")
        if udp.get("Escuchando"):
            resumen.append(f"UDP: ESCUCHANDO — proceso «{udp.get('Proceso', 'N/A')}» (PID {udp.get('PID', '?')})")
        else:
            resumen.append("UDP: cerrado (nada escuchando localmente)")

        items = []
        if reglas:
            for r in reglas[:15]:
                estado = "habilitada" if r.get("Habilitada") in ("Sí", "Si") else "deshabilitada"
                items.append(f"{r.get('Nombre')} — {r.get('Direccion')}, {r.get('Accion')}, {estado}")
            if len(reglas) > 15:
                items.append(f"… y {len(reglas) - 15} más")
        else:
            items.append("Ninguna regla de firewall menciona este puerto explícitamente.")

        nota = (
            "Nota: esto es el estado LOCAL de este equipo, no si el puerto es alcanzable "
            "de verdad desde Internet (depende también del router/NAT)."
        )
        self._mostrar_informe(f"Puerto {puerto}", resumen, items, nota)

    def _soporte_analizar_puertos_no_comunes(self):
        """Escanea qué puertos TCP están en escucha ahora mismo y filtra
        los que NO están en la lista heurística de puertos habituales —
        útil para detectar servicios olvidados, herramientas de acceso
        remoto no autorizadas, o malware con su propio puerto de escucha.
        Reusa PS_GET_NET_CONNECTIONS (mismo dato que ya carga la pestaña
        Conexiones Red) con una consulta propia, para no depender de que
        esa otra pestaña ya se haya actualizado antes."""
        self.set_status("🔄 Analizando puertos en escucha...")
        QApplication.processEvents()
        data = run_powershell_json(PS_GET_NET_CONNECTIONS, timeout=30)
        self.set_status("")

        if data is None:
            self._avisar("Analizar puertos", "No se pudo completar el análisis.", QMessageBox.Warning)
            return
        if isinstance(data, dict):
            data = [data]

        no_comunes = []
        vistos = set()
        for c in (data or []):
            if c.get("Estado") != "Escuchando" or c.get("Protocolo") != "TCP":
                continue
            try:
                puerto = int(str(c.get("DireccionLocal", "")).rsplit(":", 1)[-1])
            except ValueError:
                continue
            if puerto in self._PUERTOS_COMUNES:
                continue
            proceso_lower = str(c.get("Proceso", "")).strip().lower()
            if puerto >= self._RANGO_DINAMICO_MIN and proceso_lower in self._PROCESOS_SISTEMA_PUERTOS:
                continue
            clave = (puerto, c.get("PID"))
            if clave in vistos:
                continue
            vistos.add(clave)
            ruta = c.get("RutaEjecutable") or "N/A"
            if self.port_whitelist_db.is_whitelisted(puerto, ruta):
                continue
            no_comunes.append((puerto, c.get("Proceso", "N/A"), ruta, c.get("PID")))

        if not no_comunes:
            self._avisar("Puertos no comunes",
                         "No se detectó ningún puerto en escucha fuera de la lista de puertos habituales "
                         "(sin contar los ya marcados como comprobados).")
            return

        no_comunes.sort(key=lambda x: x[0])
        self._mostrar_puertos_no_comunes(no_comunes)

    def _mostrar_puertos_no_comunes(self, no_comunes):
        """Lista interactiva de puertos no comunes, con acción por fila para
        marcarlo como comprobado (PortWhitelistDB, clave puerto+ruta) y que
        deje de aparecer en futuros análisis — reversible desde 'Ver
        puertos comprobados'."""
        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle("Puertos abiertos no comunes")
        dialog.setMinimumWidth(780)
        v = QVBoxLayout(dialog)

        aviso = QLabel(
            f"{len(no_comunes)} puerto(s) TCP en escucha fuera de la lista heurística de puertos "
            "habituales — útil para detectar servicios olvidados, herramientas de acceso remoto no "
            "autorizadas o malware con su propio puerto de escucha.\n\n"
            "Selecciona uno o varios (Ctrl/Shift + clic, o 'Seleccionar todo') y elige una acción."
        )
        aviso.setWordWrap(True)
        v.addWidget(aviso)

        tabla = QTableWidget(len(no_comunes), 4)
        tabla.setHorizontalHeaderLabels(["Puerto", "Proceso", "PID", "Ruta"])
        tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        tabla.setSelectionBehavior(QTableWidget.SelectRows)
        tabla.setSelectionMode(QTableWidget.ExtendedSelection)
        tabla.verticalHeader().setVisible(False)
        tabla.setSortingEnabled(False)
        for fila, (puerto, proceso, ruta, pid) in enumerate(no_comunes):
            item_puerto = QTableWidgetItem(str(puerto))
            item_puerto.setData(Qt.UserRole, (puerto, proceso, ruta, pid))
            tabla.setItem(fila, 0, item_puerto)
            tabla.setItem(fila, 1, QTableWidgetItem(proceso))
            tabla.setItem(fila, 2, QTableWidgetItem(str(pid)))
            tabla.setItem(fila, 3, QTableWidgetItem(ruta))
        tabla.setSortingEnabled(True)
        _setup_header(tabla, 4, stretch_col=3)
        tabla.model().layoutChanged.emit()
        v.addWidget(tabla)

        def _filas_marcadas():
            return [(idx.row(), tabla.item(idx.row(), 0).data(Qt.UserRole))
                    for idx in tabla.selectionModel().selectedRows()]

        fila_botones = QHBoxLayout()
        btn_bloquear = QPushButton("⛔ Bloquear puerto(s)")
        btn_bloquear.setEnabled(False)
        fila_botones.addWidget(btn_bloquear)
        btn_comprobado = QPushButton("✅ Marcar como comprobado(s)")
        btn_comprobado.setEnabled(False)
        fila_botones.addWidget(btn_comprobado)
        fila_botones.addStretch()
        btn_csv = QPushButton("📄 CSV")
        btn_csv.clicked.connect(lambda: self._exportar_tabla_csv(
            tabla, ["Puerto", "Proceso", "PID", "Ruta"], "Puertos_No_Comunes"))
        fila_botones.addWidget(btn_csv)
        btn_html = QPushButton("🌐 HTML")
        btn_html.clicked.connect(lambda: self._exportar_tabla_html(
            tabla, ["Puerto", "Proceso", "PID", "Ruta"], "Puertos_No_Comunes",
            "Puertos Abiertos No Comunes"))
        fila_botones.addWidget(btn_html)
        v.addLayout(fila_botones)

        def _actualizar_botones():
            hay_marcadas = bool(_filas_marcadas())
            btn_bloquear.setEnabled(hay_marcadas)
            btn_comprobado.setEnabled(hay_marcadas)

        tabla.itemSelectionChanged.connect(_actualizar_botones)

        def _bloquear_marcados():
            marcados = _filas_marcadas()
            if not marcados:
                return
            if not is_admin():
                self._avisar("Bloquear puerto",
                             "Se requieren permisos de administrador para crear reglas de "
                             "firewall.\n\nReinicia MASys como administrador.",
                             QMessageBox.Warning)
                return
            if not self._confirmar_reparacion(
                f"Bloquear {len(marcados)} puerto(s) en el Firewall",
                cambios=[f"Puerto {puerto} ({proceso})" for _fila, (puerto, proceso, _ruta, _pid) in marcados],
                reversible=True, objetivo=f"{len(marcados)} puerto(s)",
                explicacion=(
                    "Cada proceso seguirá escuchando localmente (esto no lo detiene), pero dejará "
                    "de ser alcanzable desde la red. Las reglas se crean como 'MASys - Bloqueo "
                    "Puerto {N} (Entrada)' y se pueden gestionar después desde la pestaña 🔥 Firewall."
                ),
            ):
                return
            ok_total = 0
            for fila, (puerto, proceso, ruta, pid) in sorted(marcados, key=lambda t: -t[0]):
                ok, msg = run_powershell_command_params(
                    "New-NetFirewallRule -DisplayName 'MASys - Bloqueo Puerto {puerto} (Entrada)' "
                    "-Direction Inbound -LocalPort {puerto} -Protocol TCP -Action Block -Profile Any | Out-Null",
                    puerto=str(puerto),
                )
                self.repair_audit_db.registrar(
                    seccion="🔥 Firewall", accion="Bloquear puerto",
                    objetivo=f"Puerto {puerto} — {proceso} ({ruta})", exito=ok, detalle=msg if not ok else "OK",
                )
                if ok:
                    ok_total += 1
                    tabla.removeRow(fila)
            self.set_status(f"✅ {ok_total}/{len(marcados)} puerto(s) bloqueados en el Firewall")
            if tabla.rowCount() == 0:
                dialog.accept()

        def _marcar_comprobados():
            marcados = _filas_marcadas()
            if not marcados:
                return
            if not self._confirmar_reparacion(
                f"Marcar {len(marcados)} puerto(s) como comprobado(s)",
                cambios=[f"Puerto {puerto} ({proceso})" for _fila, (puerto, proceso, _ruta, _pid) in marcados],
                reversible=True, objetivo=f"{len(marcados)} puerto(s)",
                explicacion=(
                    "No se volverá a avisar de ESTOS puertos con ESTA ruta de ejecutable exacta — "
                    "si en el futuro otro programa distinto ocupa el mismo puerto, sí se detectará "
                    "de nuevo. Puedes deshacerlo después desde 'Ver puertos comprobados'."
                ),
            ):
                return
            marcados_ok = 0
            fallidos = 0
            for fila, (puerto, proceso, ruta, pid) in sorted(marcados, key=lambda t: -t[0]):
                if self.port_whitelist_db.add(puerto, ruta, proceso, "Comprobado desde análisis de puertos no comunes"):
                    marcados_ok += 1
                    tabla.removeRow(fila)
                else:
                    fallidos += 1
            self.set_status(f"✅ {marcados_ok} puerto(s) marcados como comprobados")
            if fallidos:
                self._avisar("Marcar como comprobado",
                             f"{fallidos} puerto(s) no se pudieron guardar (fallo de base de datos local). "
                             "Vuelve a intentarlo.", QMessageBox.Warning)
            if tabla.rowCount() == 0:
                dialog.accept()

        btn_bloquear.clicked.connect(_bloquear_marcados)
        btn_comprobado.clicked.connect(_marcar_comprobados)

        fila_inferior = QHBoxLayout()
        btn_seleccionar_todo = QPushButton("☑ Seleccionar todo")
        btn_seleccionar_todo.clicked.connect(tabla.selectAll)
        fila_inferior.addWidget(btn_seleccionar_todo)
        btn_deseleccionar_todo = QPushButton("☐ Deseleccionar todo")
        btn_deseleccionar_todo.clicked.connect(tabla.clearSelection)
        fila_inferior.addWidget(btn_deseleccionar_todo)
        fila_inferior.addStretch()
        btn_cerrar = QPushButton("Cerrar")
        btn_cerrar.clicked.connect(dialog.accept)
        fila_inferior.addWidget(btn_cerrar)
        v.addLayout(fila_inferior)

        dialog.exec_()

    def _soporte_ver_puertos_whitelist(self):
        """Gestión de puertos marcados como comprobados: verlos y quitarlos
        (para que el análisis vuelva a avisar) — mismo patrón que 'Ver
        procesos marcados como legítimos'."""
        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle("Puertos comprobados")
        dialog.setMinimumSize(720, 400)
        v = QVBoxLayout(dialog)

        tabla = QTableWidget(0, 4)
        tabla.setHorizontalHeaderLabels(["Puerto", "Proceso", "Ruta", "Comprobado el"])
        tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        tabla.setSelectionBehavior(QTableWidget.SelectRows)
        tabla.setSelectionMode(QTableWidget.SingleSelection)
        tabla.verticalHeader().setVisible(False)
        _setup_header(tabla, 4, stretch_col=2)
        v.addWidget(tabla)

        lbl_vacio = QLabel("No hay ningún puerto marcado como comprobado todavía.")
        lbl_vacio.setVisible(False)
        v.addWidget(lbl_vacio)

        def _recargar():
            entradas = self.port_whitelist_db.get_all()
            tabla.setSortingEnabled(False)
            tabla.setRowCount(len(entradas))
            for fila, entrada in enumerate(entradas):
                puerto, ruta, proceso, _motivo, comprobado_el = entrada
                item_puerto = QTableWidgetItem(str(puerto))
                item_puerto.setData(Qt.UserRole, entrada)
                tabla.setItem(fila, 0, item_puerto)
                tabla.setItem(fila, 1, QTableWidgetItem(proceso))
                tabla.setItem(fila, 2, QTableWidgetItem(ruta))
                tabla.setItem(fila, 3, QTableWidgetItem(comprobado_el))
            tabla.setSortingEnabled(True)
            tabla.model().layoutChanged.emit()
            lbl_vacio.setVisible(not entradas)
            btn_quitar.setEnabled(False)

        fila_botones = QHBoxLayout()
        btn_quitar = QPushButton("✕ Quitar de la lista (volver a avisar)")
        btn_quitar.setEnabled(False)
        fila_botones.addWidget(btn_quitar)
        fila_botones.addStretch()
        v.addLayout(fila_botones)

        tabla.itemSelectionChanged.connect(
            lambda: btn_quitar.setEnabled(bool(tabla.selectionModel().selectedRows())))

        def _quitar_seleccionado():
            filas_sel = tabla.selectionModel().selectedRows()
            if not filas_sel:
                return
            puerto, ruta, _proceso, _motivo, _comprobado_el = tabla.item(filas_sel[0].row(), 0).data(Qt.UserRole)
            if self.port_whitelist_db.remove(puerto, ruta):
                self.set_status(f"✅ Puerto {puerto} quitado de la lista de comprobados")
            else:
                self._avisar("Quitar de la lista",
                             "No se pudo quitar el puerto (fallo de base de datos local). Vuelve a intentarlo.",
                             QMessageBox.Warning)
            _recargar()

        btn_quitar.clicked.connect(_quitar_seleccionado)

        _recargar()

        fila_cerrar = QHBoxLayout()
        fila_cerrar.addStretch()
        btn_cerrar = QPushButton("Cerrar")
        btn_cerrar.clicked.connect(dialog.accept)
        fila_cerrar.addWidget(btn_cerrar)
        v.addLayout(fila_cerrar)
        dialog.exec_()

    # ── Redes (adaptadores) ───────────────────────────────────────────

    def _detectar_soporte_redes(self):
        """Detector con tres criterios independientes, ninguno visible
        antes salvo mirando la tabla a mano:
        1) Adaptador con IP fija activo pero sin Gateway o sin DNS
           configurados — mala configuración manual habitual.
        2) Más de un adaptador ACTIVO con Gateway al mismo tiempo — causa
           clásica de red intermitente/lenta (dos puertas de enlace
           compitiendo, p. ej. WiFi y Ethernet a la vez).
        3) Adaptador activo con IP APIPA (169.254.x.x) — el DHCP falló y
           el adaptador se autoasignó una IP no funcional."""
        if not hasattr(self, 'network_model'):
            return []
        acciones = []
        rows = self.network_model.rows()

        fijas_mal_config = [
            r for r in rows
            if r.estado == "Activo" and r.configuracion_ip == "Fija"
            and (r.gateway == "N/A" or r.dns == "N/A")
        ]
        if fijas_mal_config:
            etiqueta = f"⚠️ {len(fijas_mal_config)} adaptador(es) con IP fija incompleta (sin gateway/DNS)"
            acciones.append(("redes_ip_fija_incompleta", etiqueta, lambda: self._soporte_mostrar_adaptadores_red(
                "Adaptadores con IP fija incompleta", fijas_mal_config,
                "Tienen IP fija configurada pero falta el Gateway y/o los servidores DNS — "
                "sin esto no hay salida a Internet aunque la IP local funcione.")))

        activos_con_gateway = [r for r in rows if r.estado == "Activo" and r.gateway != "N/A"]
        if len(activos_con_gateway) > 1:
            etiqueta = f"⚠️ {len(activos_con_gateway)} adaptadores activos con puerta de enlace a la vez"
            acciones.append(("redes_multiples_gateway", etiqueta, lambda: self._soporte_mostrar_adaptadores_red(
                "Varias puertas de enlace activas", activos_con_gateway,
                "Tener más de un adaptador activo con Gateway a la vez (p. ej. WiFi y Ethernet "
                "conectados a la vez) es una causa habitual de red lenta o intermitente — "
                "Windows puede alternar entre rutas. Normalmente basta con desactivar el que no "
                "se esté usando.")))

        apipa = [r for r in rows if r.estado == "Activo" and r.ip.startswith("169.254.")]
        if apipa:
            etiqueta = f"🚨 {len(apipa)} adaptador(es) activo(s) sin DHCP (IP APIPA)"
            acciones.append(("redes_apipa", etiqueta, lambda: self._soporte_mostrar_adaptadores_red(
                "Adaptadores sin respuesta de DHCP (APIPA)", apipa,
                "IP 169.254.x.x autoasignada: el adaptador está activo pero no recibió "
                "respuesta de ningún servidor DHCP — revisa el cable/punto de acceso/router, "
                "o prueba 'Liberar y renovar IP' desde el menú contextual del adaptador.",
                es_alerta=True)))

        return acciones

    def _soporte_mostrar_adaptadores_red(self, titulo, adaptadores, nota, es_alerta=False):
        """Informativo: lista los adaptadores detectados por
        _detectar_soporte_redes — la acción (liberar/renovar IP, deshabilitar
        el que sobra...) se hace desde el menú contextual de la pestaña
        🌐 Redes, no aquí."""
        items = [f"{r.nombre} — {r.tipo}, IP {r.ip}, Puerta de enlace {r.gateway}, DNS {r.dns}"
                  for r in adaptadores]
        self._mostrar_informe(titulo, [], items, nota, es_alerta=es_alerta)

    def _soporte_vaciar_cache_dns(self):
        """Vacía la caché de resolución DNS del sistema (Clear-DnsClientCache)
        — clásica primera acción de cualquier técnico ante problemas de
        resolución de nombres, y que antes no existía en ningún sitio de
        la app. Se reconstruye sola a medida que se resuelven nombres, así
        que no hay nada que 'deshacer' de verdad."""
        if not is_admin():
            self._avisar("Vaciar caché DNS",
                         "Se requieren permisos de administrador para vaciar la caché DNS.\n\n"
                         "Reinicia MASys como administrador.", QMessageBox.Warning)
            return
        if not self._confirmar_reparacion(
            "Vaciar caché DNS",
            cambios=["Vaciar toda la caché de resolución de nombres DNS de este equipo"],
            reversible=True, objetivo="Caché DNS del sistema",
            explicacion="Se reconstruye automáticamente a medida que se resuelven nombres de "
                        "nuevo — no hay ningún dato que se pierda de forma permanente.",
        ):
            return
        ok, msg = run_powershell_command("Clear-DnsClientCache")
        if hasattr(self, 'repair_audit_db'):
            self.repair_audit_db.registrar(
                seccion="🌐 Redes", accion="Vaciar caché DNS",
                objetivo="Caché DNS del sistema", exito=ok, detalle=msg if not ok else "OK",
            )
        if ok:
            self.set_status("✅ Caché DNS vaciada")
            self._avisar("Vaciar caché DNS", "✅ Caché DNS vaciada correctamente.")
        else:
            self._avisar("Error", f"No se pudo vaciar la caché DNS:\n\n{msg}", QMessageBox.Critical)

    # ── Conexiones Remotas ────────────────────────────────────────────

    _UMBRAL_FALLOS_FUERZA_BRUTA = 3

    def _detectar_soporte_conexiones_remotas(self):
        """Detector de posible fuerza bruta RDP/red: agrupa los eventos
        'Fallo' ya cargados en la tabla por IP de origen — 3 o más intentos
        fallidos de la misma IP en la ventana de 30 días es un patrón mucho
        más propio de un ataque automatizado que de un usuario despistado.
        Ignora 'Local'/'N/A' (no es una IP real que se pueda bloquear)."""
        if not hasattr(self, 'remote_model'):
            return []
        fallos_por_ip = {}
        for r in self.remote_model.rows():
            if "fallo" not in (r.resultado or "").lower():
                continue
            ip = (r.ip_origen or "").strip()
            if not ip or ip in ("Local", "N/A"):
                continue
            fallos_por_ip.setdefault(ip, []).append(r)

        acciones = []
        for ip, eventos in fallos_por_ip.items():
            if len(eventos) < self._UMBRAL_FALLOS_FUERZA_BRUTA:
                continue
            etiqueta = f"🚨 {len(eventos)} intentos fallidos desde {ip} — posible fuerza bruta"
            acciones.append((f"fuerza_bruta_ip::{ip}", etiqueta, lambda ip=ip, ev=eventos: self._soporte_ip_sospechosa_remota(ip, ev)))
        return acciones

    def _soporte_ip_sospechosa_remota(self, ip: str, eventos: list):
        """Informativo: lista los intentos fallidos de esa IP — bloquearla
        se hace desde el menú contextual de 🔒 Conexiones Remotas (misma
        regla que 'Bloquear IP en el Firewall' de Conexiones Red), no aquí."""
        items = [f"{e.fecha} — usuario «{e.usuario}», tipo {e.tipo_acceso}" for e in eventos[:20]]
        if len(eventos) > 20:
            items.append(f"… y {len(eventos) - 20} más")
        nota = (
            "Esto es informativo, no una confirmación de ataque: revisa los intentos y, si "
            "no reconoces la IP, bloquéala desde el menú contextual (clic derecho sobre "
            "cualquier fila de esa IP) en la pestaña 🔒 Conexiones Remotas."
        )
        self._mostrar_informe(f"IP sospechosa: {ip}", [f"{len(eventos)} intento(s) fallido(s)"],
                               items, nota, es_alerta=True)

    # ── Seguridad ──────────────────────────────────────────────────────

    def _detectar_soporte_seguridad(self):
        """Detector con cuatro criterios independientes, sobre datos YA
        recogidos por la pestaña 🛡️ Seguridad (self._last_seguridad_info):
        Secure Boot deshabilitado, Windows Defender con protección en
        tiempo real inactiva, BitLocker sin cifrar, y Escritorio Remoto
        activo sin NLA. Ninguno disparaba ninguna alerta antes de esto —
        solo se veían en rojo mirando el árbol de la pestaña a mano."""
        data = getattr(self, '_last_seguridad_info', None)
        if not data:
            return []
        acciones = []

        secure_boot = data.get('SecureBoot') or ''
        if 'Deshabilitado' in secure_boot:
            acciones.append(("seguridad_secure_boot", "🚨 Secure Boot deshabilitado", lambda: self._avisar(
                "Secure Boot",
                "Secure Boot está deshabilitado — protege contra malware que se ejecuta "
                "antes que el propio Windows (bootkits/rootkits de arranque).\n\n"
                "Actívalo desde la configuración de firmware (BIOS/UEFI).",
                QMessageBox.Warning)))

        defender = data.get('Defender') or {}
        if 'INACTIVA' in (defender.get('ProteccionTiempoReal') or ''):
            acciones.append(("seguridad_defender_inactivo", "🚨 Windows Defender: protección en tiempo real inactiva", lambda: self._avisar(
                "Windows Defender",
                "La protección en tiempo real está desactivada — el equipo no está protegido "
                "frente a malware activo.\n\n"
                "Actívala desde Seguridad de Windows, o confirma que hay otro antivirus "
                "gestionándolo (algunos terceros desactivan Defender a propósito).",
                QMessageBox.Warning)))

        bitlocker = data.get('BitLocker') or []
        sin_cifrar = [v for v in bitlocker
                      if 'Off' in (v.get('Estado') or '') or 'Unprotected' in (v.get('Estado') or '')]
        if sin_cifrar:
            unidades = ", ".join(v.get('Unidad', 'N/A') for v in sin_cifrar)
            acciones.append((f"seguridad_bitlocker::{unidades}", f"⚠️ {len(sin_cifrar)} unidad(es) sin cifrar con BitLocker ({unidades})",
                             lambda u=unidades: self._avisar(
                "BitLocker",
                f"Unidad(es) sin cifrar: {u}\n\n"
                "Si el equipo se pierde o lo roban, los datos de esas unidades son legibles "
                "sin ninguna protección adicional.",
                QMessageBox.Warning)))

        rdp = data.get('EscritorioRemoto') or {}
        if rdp.get('Habilitado') == 'Sí' and 'INACTIVO' in (rdp.get('NLA') or ''):
            acciones.append(("seguridad_rdp_sin_nla", "🚨 Escritorio Remoto activo SIN NLA", lambda: self._avisar(
                "Escritorio Remoto (RDP)",
                "RDP está habilitado pero sin NLA (autenticación a nivel de red) — permite "
                "iniciar el protocolo de escritorio remoto sin autenticarse antes, una "
                "superficie de ataque real y evitable.\n\n"
                "Actívalo en Propiedades del sistema > Acceso remoto, o vía política de grupo.",
                QMessageBox.Warning)))

        firewall = data.get('Firewall') or {}
        perfiles_inactivos = [k for k, v in firewall.items() if 'INACTIVO' in (v or '')]
        if perfiles_inactivos:
            etiquetas = {"Domain": "Dominio", "Private": "Privado", "Public": "Público"}
            nombres = ", ".join(etiquetas.get(p, p) for p in perfiles_inactivos)
            acciones.append((f"seguridad_firewall_perfil::{nombres}", f"⚠️ Firewall inactivo en el perfil: {nombres}", lambda n=nombres: self._avisar(
                "Firewall de Windows",
                f"El perfil de firewall «{n}» está inactivo — el equipo no filtra tráfico entrante "
                "en ese contexto de red.\n\nActívalo desde Seguridad de Windows > Firewall y "
                "protección de red, o revisa la pestaña 🔥 Firewall para el detalle de reglas.",
                QMessageBox.Warning)))

        endurecimiento = data.get('Endurecimiento') or {}
        politica = data.get('PoliticaContrasenas') or {}
        if 'riesgo' in (endurecimiento.get('UAC') or '').lower():
            acciones.append(("seguridad_uac", "🚨 Control de cuentas de usuario (UAC) desactivado", lambda: self._avisar(
                "UAC desactivado",
                "El Control de cuentas de usuario está desactivado — cualquier programa puede "
                "realizar cambios con privilegios de administrador sin pedir confirmación.\n\n"
                "Actívalo en Panel de control > Cuentas de usuario > Cambiar la configuración "
                "de Control de cuentas de usuario.",
                QMessageBox.Warning)))

        if 'riesgo' in (endurecimiento.get('SMBv1') or '').lower():
            acciones.append(("seguridad_smbv1", "🚨 Protocolo SMBv1 habilitado", lambda: self._avisar(
                "SMBv1 habilitado",
                "SMBv1 está habilitado — protocolo obsoleto y vector conocido de ransomware "
                "(WannaCry/NotPetya explotaban vulnerabilidades específicas de SMBv1).\n\n"
                "Deshabilítalo salvo que algún dispositivo antiguo lo necesite expresamente: "
                "Disable-WindowsOptionalFeature -Online -FeatureName SMB1Protocol.",
                QMessageBox.Warning)))

        if 'riesgo' in (endurecimiento.get('CuentaAdmin') or '').lower():
            acciones.append(("seguridad_cuenta_admin", "⚠️ Cuenta Administrador integrada habilitada", lambda: self._avisar(
                "Cuenta Administrador habilitada",
                "La cuenta 'Administrador' integrada (SID terminado en -500) está habilitada. "
                "Es un objetivo predecible para ataques de fuerza bruta/adivinación, precisamente "
                "por tener un SID conocido y no requerir adivinar el nombre de usuario.\n\n"
                "Considera deshabilitarla si usas cuentas de administrador nominales en su lugar.",
                QMessageBox.Warning)))

        if 'riesgo' in (endurecimiento.get('CuentaInvitado') or '').lower():
            acciones.append(("seguridad_cuenta_invitado", "⚠️ Cuenta Invitado integrada habilitada", lambda: self._avisar(
                "Cuenta Invitado habilitada",
                "La cuenta 'Invitado' integrada (SID terminado en -501) está habilitada — permite "
                "acceso sin contraseña propia a quien la use.\n\n"
                "Desactívala salvo que tengas una razón concreta para mantenerla activa.",
                QMessageBox.Warning)))

        if 'riesgo' in (endurecimiento.get('LSAProtection') or '').lower():
            acciones.append(("seguridad_lsa_protection", "⚠️ Protección LSA (RunAsPPL) inactiva", lambda: self._avisar(
                "Protección LSA inactiva",
                "LSASS no está protegido como proceso protegido (RunAsPPL) — herramientas de robo "
                "de credenciales en memoria (tipo Mimikatz) pueden volcar credenciales con más "
                "facilidad si un atacante consigue privilegios elevados.\n\n"
                "Actívala vía directiva de grupo o el registro (HKLM\\SYSTEM\\CurrentControlSet\\"
                "Control\\Lsa, RunAsPPL=1) tras comprobar compatibilidad de drivers/EDR.",
                QMessageBox.Warning)))

        if 'riesgo' in (endurecimiento.get('ProtectorPantalla') or '').lower():
            acciones.append(("seguridad_protector_pantalla", "⚠️ Protector de pantalla sin contraseña o inactivo", lambda: self._avisar(
                "Protector de pantalla",
                f"Estado actual: {endurecimiento.get('ProtectorPantalla')}\n\n"
                "Sin un protector de pantalla que exija contraseña, cualquiera que se acerque al "
                "equipo desatendido puede usarlo con la sesión abierta.\n\n"
                "Configúralo en Configuración > Personalización > Pantalla de bloqueo > "
                "Protector de pantalla.",
                QMessageBox.Warning)))

        if 'riesgo' in (politica.get('Complejidad') or '').lower() or 'riesgo' in (politica.get('LongitudMinima') or '').lower():
            acciones.append(("seguridad_politica_contrasenas", "⚠️ Política de contraseñas local débil", lambda: self._avisar(
                "Política de contraseñas débil",
                f"Longitud mínima: {politica.get('LongitudMinima', 'N/A')}\n"
                f"Complejidad: {politica.get('Complejidad', 'N/A')}\n\n"
                "Sin longitud mínima ni complejidad exigidas, las cuentas locales pueden usar "
                "contraseñas triviales.\n\nRevisa la directiva de seguridad local "
                "(secpol.msc > Directivas de cuenta > Directiva de contraseñas).",
                QMessageBox.Warning)))

        return acciones

    # ── Cuentas de Correo ────────────────────────────────────────────────

    def _detectar_soporte_email(self):
        """Detector sobre las cuentas de correo ya cargadas (EmailMixin) +
        una comprobación ligera del disco (tamaño de .ost/.pst y
        antigüedad del último backup de configuración) — todo local, sin
        red ni COM, así que es rápido de ejecutar cada vez que se abre
        Soporte."""
        if not hasattr(self, 'email_model'):
            return []
        acciones = []

        for row in self.email_model.rows():
            tipo = (row.tipo or "").strip()

            # 1. Autenticación básica (IMAP/POP3 con usuario+contraseña
            # directos) contra Office 365/Outlook.com — Microsoft la está
            # retirando; estas cuentas pueden dejar de autenticar en
            # cualquier momento si siguen configuradas así en vez de como
            # cuenta Exchange/Moderna.
            hosts_ms = (row.imap_host, row.pop_host, row.smtp_host)
            usa_office365 = any(h and ("office365.com" in h or "outlook.com" in h) for h in hosts_ms)
            if tipo in ("IMAP", "POP3") and usa_office365:
                acciones.append((
                    f"email_auth_basica::{row.cuenta}",
                    f"⚠️ «{row.cuenta}» usa autenticación básica (Microsoft la está retirando)",
                    lambda r=row: self._soporte_info_email_auth_basica(r),
                ))

            # 2. Puerto clásico sin cifrar (25/110/143 con el SSL
            # correspondiente en False) — credenciales y correo en claro.
            sin_cifrar = (
                (row.imap_host and not row.imap_ssl and row.imap_port in (0, 143)) or
                (row.pop_host and not row.pop_ssl and row.pop_port in (0, 110)) or
                (row.smtp_host and not row.smtp_ssl and row.smtp_port in (0, 25))
            )
            if sin_cifrar:
                acciones.append((
                    f"email_sin_cifrar::{row.cuenta}",
                    f"🔓 «{row.cuenta}» tiene un protocolo configurado sin cifrar",
                    lambda r=row: self._soporte_info_email_sin_cifrar(r),
                ))

            # 3. Red de seguridad para la propia heurística de
            # PS_GET_EMAIL_ACCOUNTS: si no supo clasificar el tipo, avisar
            # en vez de dejarlo pasar en silencio (ver fix del bug real de
            # Gmail detectado como Exchange por esta misma razón).
            if tipo in ("No determinado", "Desconocido", ""):
                acciones.append((
                    f"email_tipo_desconocido::{row.cuenta}",
                    f"❓ No se pudo determinar el tipo de cuenta de «{row.cuenta}»",
                    lambda r=row: self._avisar(
                        "Tipo de cuenta no determinado",
                        f"No se pudo clasificar automáticamente «{r.cuenta}» (cliente: {r.cliente}).\n\n"
                        "Revisa su configuración manualmente con «Ver configuración» en el menú "
                        "contextual de la fila."),
                ))

        acciones.extend(self._detectar_soporte_email_disco())
        return acciones

    def _detectar_soporte_email_disco(self):
        """Tamaño de los .ost/.pst locales y antigüedad del último backup
        de configuración generado con «Backup configuración» — comprobación
        de filesystem, no depende de ningún modelo cargado."""
        acciones = []
        try:
            carpeta_outlook = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'Microsoft', 'Outlook')
            if os.path.isdir(carpeta_outlook):
                for nombre in os.listdir(carpeta_outlook):
                    if not nombre.lower().endswith(('.ost', '.pst')):
                        continue
                    ruta = os.path.join(carpeta_outlook, nombre)
                    try:
                        tamano_gb = os.path.getsize(ruta) / (1024 ** 3)
                    except OSError:
                        continue
                    if tamano_gb >= 20:
                        acciones.append((
                            f"email_disco_grande::{nombre}",
                            f"💽 «{nombre}» pesa {tamano_gb:.1f} GB — considera archivar correo antiguo",
                            lambda n=nombre, t=tamano_gb: self._avisar(
                                "Archivo de datos muy grande",
                                f"«{n}» ocupa {t:.1f} GB. Un archivo de datos tan grande puede "
                                "ralentizar Outlook y sus búsquedas. Considera mover correo antiguo "
                                "a un .pst de archivo con el botón «Exportar a PST»."),
                        ))
        except OSError:
            pass

        try:
            carpeta_informes = get_informes_folder()
            backups = [f for f in os.listdir(carpeta_informes)
                       if f.startswith("OutlookConfig_") and f.endswith(".reg")]
            if not backups:
                acciones.append((
                    "email_sin_backup_config",
                    "💾 Nunca se ha hecho un backup de la configuración de Outlook",
                    lambda: self._avisar(
                        "Sin backup de configuración",
                        "No se ha encontrado ningún backup previo generado con «Backup "
                        "configuración». Se recomienda hacer uno, sobre todo antes de reinstalar "
                        "o migrar el equipo."),
                ))
            else:
                mtimes = [os.path.getmtime(os.path.join(carpeta_informes, f)) for f in backups]
                dias = (time.time() - max(mtimes)) / 86400
                if dias > 90:
                    acciones.append((
                        "email_backup_config_antiguo",
                        f"💾 El último backup de configuración de Outlook tiene {int(dias)} días",
                        lambda d=dias: self._avisar(
                            "Backup de configuración antiguo",
                            f"El backup más reciente tiene {int(d)} días. Considera generar uno "
                            "nuevo con el botón «Backup configuración» de Cuentas de Correo."),
                    ))
        except OSError:
            pass
        return acciones

    def _soporte_info_email_auth_basica(self, row):
        self._avisar(
            "Autenticación básica en retirada",
            f"«{row.cuenta}» se conecta por {row.tipo} directamente contra un servidor de "
            f"Office 365/Outlook.com ({row.imap_host or row.smtp_host}).\n\n"
            "Microsoft está retirando la autenticación básica (usuario+contraseña directos) para "
            "estos servicios en favor de la autenticación moderna (OAuth2). Esta cuenta puede "
            "dejar de poder conectarse en cualquier momento. Reconfigúrala como cuenta Exchange/"
            "Office 365 moderna en vez de IMAP/POP3 manual.")

    def _soporte_info_email_sin_cifrar(self, row):
        detalle = []
        if row.imap_host and not row.imap_ssl and row.imap_port in (0, 143):
            detalle.append(f"IMAP: {row.imap_host}:{row.imap_port or 143} sin SSL/TLS")
        if row.pop_host and not row.pop_ssl and row.pop_port in (0, 110):
            detalle.append(f"POP3: {row.pop_host}:{row.pop_port or 110} sin SSL/TLS")
        if row.smtp_host and not row.smtp_ssl and row.smtp_port in (0, 25):
            detalle.append(f"SMTP: {row.smtp_host}:{row.smtp_port or 25} sin SSL/TLS")
        self._avisar(
            "Protocolo sin cifrar",
            f"«{row.cuenta}» tiene al menos un protocolo configurado sin cifrado:\n\n"
            + "\n".join(detalle) +
            "\n\nEsto significa que la contraseña y el contenido del correo viajan en claro por "
            "la red. Cambia el puerto/cifrado en la configuración de la cuenta si el proveedor lo "
            "permite (usa «Detectar cuenta» para encontrar la configuración segura recomendada).")

    # ── Navegadores y Nube ───────────────────────────────────────────────

    _MOTORES_BUSQUEDA_CONOCIDOS = (
        "google.com", "bing.com", "yahoo.com", "duckduckgo.com", "ecosia.org",
        "startpage.com", "qwant.com", "search.brave.com", "you.com", "presearch.com",
        "yandex.", "baidu.com", "ask.com", "aol.com",
    )
    _HOMEPAGE_SEGURAS = ("about:blank", "chrome://newtab", "chrome://new-tab-page", "edge://newtab")
    _UMBRAL_PERFIL_NAVEGADOR_MB = 5000

    def _detectar_soporte_navegadores_nube(self):
        """Detector combinado de la sección "☁️ Navegadores y Nube":
          1. OneDrive configurado pero sin sincronizar (proceso caído, o
             la carpeta configurada ya no existe/no es accesible).
          2. Página de inicio / motor de búsqueda no reconocido en
             Chrome/Edge — señal clásica de adware/PUP (informativo, no
             acusatorio: puede ser una elección legítima del usuario).
          3. Perfil de navegador con un tamaño en disco excesivo.
        """
        acciones = []
        acciones.extend(self._detectar_soporte_onedrive())
        acciones.extend(self._detectar_soporte_navegador_config())
        acciones.extend(self._detectar_soporte_navegador_tamano())
        return acciones

    def _detectar_soporte_onedrive(self):
        if not hasattr(self, 'nube_model'):
            return []
        tiene_onedrive = any("onedrive" in (row.cliente or "").lower()
                              for row in self.nube_model.rows())
        if not tiene_onedrive:
            return []

        try:
            import psutil
            corriendo = any((p.info.get('name') or '').lower() == 'onedrive.exe'
                             for p in psutil.process_iter(['name']))
        except Exception:
            corriendo = True  # si psutil falla, no avisar por un falso negativo

        acciones = []
        if not corriendo:
            acciones.append((
                "onedrive_no_ejecutando",
                "☁️ OneDrive está configurado pero no se está ejecutando ahora mismo",
                self._soporte_info_onedrive_no_ejecutando,
            ))

        # Carpeta configurada (UserFolder) inaccesible — p. ej. unidad
        # externa desconectada, carpeta movida/renombrada manualmente.
        # FIX: esto se ejecuta EN EL HILO DE UI cada vez que se entra en la
        # pestaña (_actualizar_boton_soporte llama al detector de forma
        # síncrona) — antes lanzaba un proceso powershell.exe completo por
        # cada comprobación; leer el registro directamente con winreg es
        # prácticamente instantáneo y evita ese coste en cada visita.
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\OneDrive\Accounts") as clave_cuentas:
                i = 0
                while True:
                    try:
                        nombre_subclave = winreg.EnumKey(clave_cuentas, i)
                    except OSError:
                        break
                    i += 1
                    if not re.match(r'^(Personal|Business\d*)$', nombre_subclave):
                        continue
                    try:
                        with winreg.OpenKey(clave_cuentas, nombre_subclave) as subclave:
                            carpeta, _tipo = winreg.QueryValueEx(subclave, "UserFolder")
                    except OSError:
                        continue
                    if carpeta and not os.path.isdir(carpeta):
                        acciones.append((
                            f"onedrive_carpeta_inaccesible::{nombre_subclave}",
                            f"☁️ La carpeta de OneDrive «{nombre_subclave}» no existe o no es accesible: {carpeta}",
                            lambda c=carpeta: self._avisar(
                                "Carpeta de OneDrive inaccesible",
                                f"OneDrive tiene configurada esta carpeta:\n\n{c}\n\nPero no existe o no "
                                "es accesible ahora mismo (¿unidad externa desconectada? ¿carpeta movida "
                                "o renombrada manualmente?). Esto detiene la sincronización de esa cuenta."),
                        ))
        except OSError:
            pass

        return acciones

    def _soporte_info_onedrive_no_ejecutando(self):
        self._avisar(
            "OneDrive no está sincronizando",
            "Hay una cuenta de OneDrive configurada en este equipo, pero el proceso OneDrive.exe "
            "no se está ejecutando ahora mismo — la sincronización está detenida o pausada.\n\n"
            "Abre OneDrive manualmente (menú Inicio → OneDrive) para reanudar la sincronización.")

    def _detectar_soporte_navegador_config(self):
        """Página de inicio / motor de búsqueda no reconocido — solo
        Chrome/Edge (Preferences es JSON legible); Firefox guarda esto
        comprimido (search.json.mozlz4), fuera de alcance por ahora."""
        try:
            from msys.browser_data import enumerar_perfiles
        except ImportError:
            return []

        acciones = []
        try:
            perfiles = [p for p in enumerar_perfiles() if p.navegador in ("Chrome", "Edge")]
        except Exception:
            return []

        for perfil in perfiles:
            ruta_prefs = os.path.join(perfil.ruta, "Preferences")
            if not os.path.isfile(ruta_prefs):
                continue
            try:
                with open(ruta_prefs, "r", encoding="utf-8") as f:
                    prefs = json.loads(f.read())
            except (OSError, ValueError):
                continue

            homepage = prefs.get("homepage")
            homepage_es_newtab = prefs.get("homepage_is_newtabpage")
            if homepage and homepage_es_newtab is False and \
                    homepage.lower().rstrip("/") not in self._HOMEPAGE_SEGURAS:
                acciones.append((
                    f"navegador_homepage::{perfil.navegador}::{perfil.nombre_perfil}",
                    f"🏠 {perfil.navegador} ({perfil.nombre_perfil}): página de inicio personalizada — {homepage}",
                    lambda h=homepage, p=perfil: self._avisar(
                        "Página de inicio personalizada",
                        f"{p.navegador} ({p.nombre_perfil}) tiene esta página de inicio:\n\n{h}\n\n"
                        "Si no la reconoces o no la configuraste tú, puede ser obra de una extensión "
                        "o instalador de terceros (adware/PUP). Revísala en Extensiones instaladas y "
                        "en la configuración del navegador."),
                ))

            tud = (prefs.get("default_search_provider_data") or {}).get("template_url_data") or {}
            url_motor = (tud.get("url") or "").lower()
            if url_motor and not any(d in url_motor for d in self._MOTORES_BUSQUEDA_CONOCIDOS):
                acciones.append((
                    f"navegador_motor_busqueda::{perfil.navegador}::{perfil.nombre_perfil}",
                    f"🔍 {perfil.navegador} ({perfil.nombre_perfil}): motor de búsqueda no reconocido "
                    f"— {tud.get('short_name', url_motor)}",
                    lambda t=tud, p=perfil: self._avisar(
                        "Motor de búsqueda no reconocido",
                        f"{p.navegador} ({p.nombre_perfil}) tiene configurado por defecto:\n\n"
                        f"{t.get('short_name', '(sin nombre)')} — {t.get('url', '')}\n\n"
                        "No está en la lista de motores conocidos. Puede ser una elección legítima "
                        "tuya, pero si no lo configuraste, revisa las extensiones instaladas — un "
                        "motor de búsqueda desconocido es una señal clásica de adware."),
                ))
        return acciones

    def _detectar_soporte_navegador_tamano(self):
        """Lee el tamaño de perfil de un CACHÉ ya calculado en segundo
        plano (BrowserProfileSizeThread, disparado al pulsar «Actualizar»
        en Navegadores) — nunca hace os.walk() aquí. FIX de un bug real:
        antes calculaba el tamaño con os.walk() directamente EN ESTE
        detector, que _actualizar_boton_soporte() ejecuta de forma
        síncrona en el hilo de UI cada vez que se entra en la pestaña —
        con un perfil de ~18 GB esto colapsaba la interfaz varios segundos
        en cada visita, incluso sin haber pulsado «Actualizar»."""
        tamanos = getattr(self, "_tamanos_perfiles_navegador", None)
        if not tamanos:
            return []

        acciones = []
        for (navegador, nombre_perfil), (tamano, ruta) in tamanos.items():
            if tamano < self._UMBRAL_PERFIL_NAVEGADOR_MB:
                continue
            acciones.append((
                f"navegador_perfil_grande::{navegador}::{nombre_perfil}",
                f"💽 Perfil {navegador} ({nombre_perfil}) ocupa {tamano / 1024:.1f} GB en disco",
                lambda t=tamano, n=navegador, np=nombre_perfil, r=ruta: self._avisar(
                    "Perfil de navegador muy grande",
                    f"El perfil {n} ({np}) ocupa {t / 1024:.1f} GB en disco ({r}).\n\n"
                    "Un perfil tan grande (normalmente caché/historial acumulado) puede ralentizar "
                    "el navegador. Considera limpiar datos de navegación antiguos desde la "
                    "configuración del propio navegador."),
            ))
        return acciones

    # ── Sesiones Activas ─────────────────────────────────────────────────

    @staticmethod
    def _es_ip_privada(ip: str) -> bool:
        """True si la IP cae en un rango privado/CGNAT/enlace local
        habitual (10.x, 172.16-31.x, 192.168.x, 100.64-127.x — este último
        es el rango CGNAT que usan VPNs como Tailscale, no solo NAT de
        operador —, 169.254.x). Sin lista blanca que mantener: cualquier
        IP fuera de estos rangos se considera "posiblemente externa"."""
        try:
            partes = [int(p) for p in ip.strip().split(".")]
            if len(partes) != 4 or any(p < 0 or p > 255 for p in partes):
                return True  # formato irreconocible: no avisar por si acaso
        except ValueError:
            return True

        a, b = partes[0], partes[1]
        if a == 10:
            return True
        if a == 172 and 16 <= b <= 31:
            return True
        if a == 192 and b == 168:
            return True
        if a == 100 and 64 <= b <= 127:
            return True
        if a == 169 and b == 254:
            return True
        return False

    @staticmethod
    def _es_sesion_desconectada(estado: str) -> bool:
        """True si el Estado indica que el cliente se desconectó SIN
        cerrar sesión (ES: "Desconectado"/"Desconec", EN: "Disc") — la
        sesión sigue viva en el servidor, ocupando recursos, sin nadie
        delante de la pantalla. Antes esto se capturaba en el dato
        (row.estado) pero ningún detector lo miraba — cualquier fila RDP
        se anunciaba como "activa" sin comprobar si en realidad estaba
        desconectada."""
        estado_lower = (estado or "").lower()
        return "desc" in estado_lower or "disc" in estado_lower

    def _detectar_soporte_sesiones_activas(self):
        """Detector sobre las sesiones ya cargadas (sesiones_model):
          1. Sesión desconectada sin cerrar (cualquier tipo) — sigue
             ocupando recursos sin nadie delante; se puede cerrar desde
             el menú contextual de la fila.
          2. Sesión RDP realmente activa ahora mismo — informativo, no
             un fallo en sí (puede ser tú mismo conectando en remoto).
          3. Esa sesión RDP viene de una IP de origen NO privada/CGNAT —
             señal de acceso desde fuera de la red local/VPN habitual
             (se comprueba independientemente de si sigue conectada,
             porque el origen importa igual aunque ya se haya desconectado).
        La IP de origen (row.origen_ip) ya viene correlacionada por
        ActiveSessionCollector contra el histórico de conexiones RDP — no
        se vuelve a consultar el registro de eventos aquí."""
        if not hasattr(self, 'sesiones_model'):
            return []

        acciones = []
        for row in self.sesiones_model.rows():
            desconectada = self._es_sesion_desconectada(row.estado)

            if desconectada:
                acciones.append((
                    f"sesion_desconectada::{row.usuario}::{row.inicio}",
                    f"🔌 Sesión desconectada sin cerrar: «{row.usuario}» ({row.tipo}, desde {row.inicio})",
                    lambda r=row: self._avisar(
                        "Sesión desconectada sin cerrar",
                        f"Usuario: {r.usuario}\nTipo: {r.tipo}\nEstado: {r.estado}\nInicio: {r.inicio}\n\n"
                        "El cliente se desconectó pero la sesión sigue abierta en este equipo, "
                        "ocupando recursos (memoria, posibles archivos bloqueados) sin nadie delante. "
                        "Considera cerrarla desde el menú contextual de la fila si no va a reanudarse."),
                ))

            if "rdp" not in (row.tipo or "").lower():
                continue

            if not desconectada:
                acciones.append((
                    f"sesion_rdp_activa::{row.usuario}::{row.inicio}",
                    f"🖧 Sesión RDP activa: «{row.usuario}» (desde {row.inicio})",
                    lambda r=row: self._avisar(
                        "Sesión RDP activa",
                        f"Usuario: {r.usuario}\nEstado: {r.estado}\nInicio: {r.inicio}\n"
                        f"Origen: {r.origen_ip or 'no identificado'}\n\n"
                        "Hay una sesión de Escritorio Remoto activa ahora mismo en este equipo. "
                        "Si no la reconoces, ciérrala desde el menú contextual de la fila y revisa "
                        "quién tiene acceso remoto autorizado a esta máquina."),
                ))

            if row.origen_ip and not self._es_ip_privada(row.origen_ip):
                acciones.append((
                    f"sesion_rdp_ip_externa::{row.usuario}::{row.origen_ip}",
                    f"🌍 Sesión RDP de «{row.usuario}» viene de una IP externa: {row.origen_ip}",
                    lambda r=row: self._avisar(
                        "Conexión RDP desde IP externa",
                        f"La sesión RDP de «{r.usuario}» procede de {r.origen_ip}, que no está en un "
                        "rango privado/VPN habitual (10.x, 172.16-31.x, 192.168.x, 100.64-127.x).\n\n"
                        "Si no reconoces este acceso, ciérralo y revisa el historial de conexiones RDP "
                        "para ver si se repite."),
                ))
        return acciones

    # ── Certificados ─────────────────────────────────────────────────────

    def _detectar_soporte_certificados(self):
        """Detector sobre los certificados PERSONALES (ubicación \\My, no
        \\Root) ya cargados: caducados/próximos a caducar (≤30 días, mismo
        umbral que ya usa PS_GET_CERTIFICATES para el Estado) y sospechosos
        (algoritmo de firma débil o autofirmado — ver CertificateRow.sospechoso).

        Los certificados raíz (\\Root) se excluyen a propósito de AMBOS
        criterios: Windows trae de fábrica decenas de raíces ya caducadas
        y con algoritmos antiguos (MD5/SHA1) sin que suponga ningún
        problema real — comprobado en vivo en este equipo: los 54
        certificados con algoritmo débil detectados viven TODOS en \\Root,
        ninguno en \\My. Avisar de esos sería puro ruido. Lo que sí importa
        es un certificado PERSONAL caducado o con una señal de riesgo real."""
        if not hasattr(self, 'certs_model'):
            return []
        acciones = []
        for row in self.certs_model.rows():
            if "\\My" not in (row.ubicacion or ""):
                continue
            estado = (row.estado or "").strip().lower()
            if "caducado" in estado:
                acciones.append((
                    f"cert_caducado::{row.huella_digital}",
                    f"❌ Certificado caducado: «{row.asunto}»",
                    lambda r=row: self._soporte_info_certificado(r, caducado=True),
                ))
            elif "proxima" in estado or "próxima" in estado:
                acciones.append((
                    f"cert_proximo_caducar::{row.huella_digital}",
                    f"⚠️ Certificado próximo a caducar: «{row.asunto}» (hasta {row.valido_hasta})",
                    lambda r=row: self._soporte_info_certificado(r, caducado=False),
                ))

            if row.sospechoso and not self.cert_sospechoso_whitelist_db.is_whitelisted(row.huella_digital):
                acciones.append((
                    f"cert_sospechoso::{row.huella_digital}",
                    f"🚨 Certificado sospechoso: «{row.asunto}» ({row.motivo_sospecha})",
                    lambda r=row: self._soporte_info_certificado_sospechoso(r),
                ))
        return acciones

    def _soporte_info_certificado(self, row, caducado: bool):
        """Info de caducidad, con accesos directos a Ver/Exportar (las
        mismas acciones del menú contextual de Certificados — CertificadosMixin
        vive en el mismo 'self') para no obligar a ir a buscar la fila."""
        titulo = "Certificado caducado" if caducado else "Certificado próximo a caducar"
        cuerpo_extra = (
            "Este certificado ya no es válido — cualquier operación de firma o autenticación "
            "que dependa de él fallará. Solicita uno nuevo o renuévalo desde su origen (entidad "
            "emisora / departamento de IT)."
            if caducado else
            "Renuévalo antes de la fecha indicada para evitar interrupciones en firma o "
            "autenticación."
        )
        self._dialogo_info_certificado(
            titulo,
            f"Asunto: {row.asunto}\n"
            f"Emisor: {row.emisor}\n"
            f"Válido hasta: {row.valido_hasta}\n"
            f"Ubicación: {row.ubicacion}\n"
            f"Uso: {row.uso}\n\n"
            f"{cuerpo_extra}",
            row,
        )

    def _soporte_info_certificado_sospechoso(self, row):
        self._dialogo_info_certificado(
            "Certificado sospechoso",
            f"Asunto: {row.asunto}\n"
            f"Emisor: {row.emisor}\n"
            f"Ubicación: {row.ubicacion}\n"
            f"Algoritmo de firma: {row.algoritmo_firma or 'Desconocido'}\n"
            f"Autofirmado: {row.autofirmado}\n\n"
            f"Motivo: {row.motivo_sospecha}.\n\n"
            "Revísalo: si no reconoces este certificado o no sabes por qué está aquí, "
            "puedes eliminarlo desde esta misma ventana tras confirmar que ninguna aplicación "
            "lo necesita. Si ya lo revisaste y es correcto, confirma para dejar de ver este aviso.",
            row,
            mostrar_confirmar=True,
        )

    def _dialogo_info_certificado(self, titulo: str, cuerpo: str, row, mostrar_confirmar: bool = False):
        """Ventana compartida por los avisos de Certificados de Soporte:
        info + accesos directos a Ver/Exportar/Eliminar/Confirmar (las
        mismas acciones que el menú contextual de la pestaña Certificados,
        más Confirmar solo para el aviso de sospechoso) — todo en la MISMA
        ventana, no repartido entre esta y una línea suelta en el
        desplegable de Soporte."""
        dialog = QDialog(self)
        dialog.setWindowTitle(titulo)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setMinimumWidth(480)
        v = QVBoxLayout(dialog)

        lbl = QLabel(cuerpo)
        lbl.setWordWrap(True)
        v.addWidget(lbl)

        fila_botones = QHBoxLayout()
        btn_ver = QPushButton("👁️ Ver certificado")
        btn_ver.clicked.connect(lambda: self._ver_certificado_nativo(row.asunto, row.huella_digital, row.ubicacion))
        fila_botones.addWidget(btn_ver)

        btn_exportar = QPushButton("📤 Exportar")
        btn_exportar.clicked.connect(lambda: self._exportar_certificado_individual(row.asunto, row.huella_digital, row.ubicacion))
        fila_botones.addWidget(btn_exportar)

        if (row.ubicacion or "").lower().startswith("currentuser"):
            btn_eliminar = QPushButton("✕ Eliminar")
            def _eliminar_y_cerrar():
                self._eliminar_certificado(row.asunto, row.huella_digital, row.ubicacion)
                dialog.accept()
            btn_eliminar.clicked.connect(_eliminar_y_cerrar)
            fila_botones.addWidget(btn_eliminar)

        if mostrar_confirmar:
            # Certificado sospechoso: ya tiene su propio mecanismo de "no
            # volver a avisar" por huella digital (cert_sospechoso_whitelist_db,
            # ver 'Ver certificados validados') — es la MISMA granularidad
            # que el botón genérico de abajo, así que no se duplica aquí
            # (dos botones "✅ Validar" casi idénticos habría sido confuso).
            btn_validar = QPushButton("✅ Validar")
            def _validar_y_cerrar():
                self._soporte_confirmar_certificado_sospechoso(row)
                dialog.accept()
            btn_validar.clicked.connect(_validar_y_cerrar)
            fila_botones.addWidget(btn_validar)
        else:
            # Caducado / próximo a caducar: sin mecanismo de silenciar
            # propio, aquí sí aplica el genérico.
            self._agregar_boton_validar_aviso(fila_botones, dialog)

        fila_botones.addStretch()
        v.addLayout(fila_botones)

        botones = QHBoxLayout()
        botones.addStretch()
        btn_cerrar = QPushButton("Cerrar")
        btn_cerrar.clicked.connect(dialog.accept)
        botones.addWidget(btn_cerrar)
        v.addLayout(botones)

        dialog.exec_()

    def _soporte_confirmar_certificado_sospechoso(self, row):
        """Valida que el certificado sospechoso ya se ha revisado — deja
        de avisar sobre ESTE certificado (clave huella digital). Reversible
        desde 'Ver certificados validados'."""
        if not row.huella_digital:
            self._avisar("Validar", "Este certificado no tiene una huella digital válida — no se puede validar.",
                         QMessageBox.Warning)
            return
        if not self._confirmar_reparacion(
            "Validar certificado revisado",
            cambios=[f"«{row.asunto}» dejará de generar este aviso"],
            reversible=True, objetivo=row.asunto,
            explicacion=(
                f"Motivo detectado: {row.motivo_sospecha}. Si ya revisaste que este certificado es "
                "legítimo y esperado, valídalo para dejar de ver este aviso. Puedes deshacerlo "
                "después desde 'Ver certificados validados'."
            ),
        ):
            return
        if self.cert_sospechoso_whitelist_db.add(row.huella_digital, row.asunto, "Validado manualmente desde Soporte"):
            self.set_status(f"✅ «{row.asunto}» validado — no se volverá a avisar")
        else:
            self._avisar("Error", "No se pudo guardar la validación (fallo de base de datos local).",
                         QMessageBox.Warning)

    def _soporte_ver_certs_sospechosos_whitelist(self):
        """Gestión de certificados validados: verlos e invalidarlos (para
        que el detector vuelva a avisar) — mismo patrón que 'Ver cuentas
        con contraseña en blanco confirmadas'."""
        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle("Certificados validados")
        dialog.setMinimumSize(640, 380)
        v = QVBoxLayout(dialog)

        tabla = QTableWidget(0, 3)
        tabla.setHorizontalHeaderLabels(["Certificado", "Motivo", "Validado el"])
        tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        tabla.setSelectionBehavior(QTableWidget.SelectRows)
        tabla.setSelectionMode(QTableWidget.SingleSelection)
        tabla.verticalHeader().setVisible(False)
        _setup_header(tabla, 3, stretch_col=1)
        v.addWidget(tabla)

        lbl_vacio = QLabel("No hay ningún certificado validado todavía.")
        lbl_vacio.setVisible(False)
        v.addWidget(lbl_vacio)

        def _recargar():
            entradas = self.cert_sospechoso_whitelist_db.get_all()
            tabla.setSortingEnabled(False)
            tabla.setRowCount(len(entradas))
            for fila, entrada in enumerate(entradas):
                huella, asunto, motivo, validado_el = entrada
                item_asunto = QTableWidgetItem(asunto)
                item_asunto.setData(Qt.UserRole, entrada)
                tabla.setItem(fila, 0, item_asunto)
                tabla.setItem(fila, 1, QTableWidgetItem(motivo))
                tabla.setItem(fila, 2, QTableWidgetItem(validado_el))
            tabla.setSortingEnabled(True)
            tabla.model().layoutChanged.emit()
            lbl_vacio.setVisible(not entradas)
            btn_quitar.setEnabled(False)

        fila_botones = QHBoxLayout()
        btn_quitar = QPushButton("✕ Invalidar (volver a avisar)")
        btn_quitar.setEnabled(False)
        fila_botones.addWidget(btn_quitar)
        fila_botones.addStretch()
        v.addLayout(fila_botones)

        tabla.itemSelectionChanged.connect(
            lambda: btn_quitar.setEnabled(bool(tabla.selectionModel().selectedRows())))

        def _quitar_seleccionado():
            filas_sel = tabla.selectionModel().selectedRows()
            if not filas_sel:
                return
            huella, asunto, _motivo, _validado_el = tabla.item(filas_sel[0].row(), 0).data(Qt.UserRole)
            if self.cert_sospechoso_whitelist_db.remove(huella):
                self.set_status(f"✅ «{asunto}» invalidado — volverá a avisar")
            else:
                self._avisar("Invalidar",
                             "No se pudo invalidar el certificado (fallo de base de datos local). "
                             "Vuelve a intentarlo.",
                             QMessageBox.Warning)
            _recargar()

        btn_quitar.clicked.connect(_quitar_seleccionado)

        _recargar()

        fila_cerrar = QHBoxLayout()
        fila_cerrar.addStretch()
        btn_cerrar = QPushButton("Cerrar")
        btn_cerrar.clicked.connect(dialog.accept)
        fila_cerrar.addWidget(btn_cerrar)
        v.addLayout(fila_cerrar)
        dialog.exec_()

    # ── Credenciales ─────────────────────────────────────────────────────

    def _detectar_soporte_credenciales(self):
        """Detector sobre las credenciales ya cargadas: el fichero
        ~/.git-credentials (helper 'store' de Git) guarda usuario y token/
        contraseña en TEXTO PLANO sin ninguna protección — a diferencia del
        resto de credenciales de esta pestaña (Credential Manager, SSH,
        RDP), que si están protegidas por Windows. Es un hallazgo 100%
        verificable (el fichero existe o no existe, sin ambigüedad) y de
        solución conocida, así que solo se informa con instrucciones —
        migrar el helper toca configuración de Git del usuario y no es
        automatizable con seguridad desde aquí sin arriesgar los tokens
        guardados."""
        if not hasattr(self, 'creds_model'):
            return []
        if not any(".git-credentials" in (r.fuente or "") for r in self.creds_model.rows()):
            return []
        return [(
            "credenciales_git_texto_plano",
            "🚨 Credenciales de Git guardadas en texto plano (~/.git-credentials)",
            self._soporte_explicar_git_credentials_plano,
        )]

    def _soporte_explicar_git_credentials_plano(self):
        self._avisar(
            "Credenciales de Git en texto plano",
            "El fichero ~/.git-credentials (helper 'store') guarda usuario y contraseña/token de "
            "Git sin ningún cifrado ni protección — cualquiera con acceso al perfil puede leerlo "
            "directamente con un editor de texto.\n\n"
            "Recomendación: migrar al gestor de credenciales de Windows, que sí protege el "
            "secreto:\n\n"
            "  git config --global credential.helper manager-core\n\n"
            "Después, la próxima vez que Git pida autenticación se guardará en Credential Manager "
            "en vez de en el fichero de texto — puedes entonces eliminar ~/.git-credentials "
            "manualmente.",
            QMessageBox.Warning,
        )

    # ── Usuarios ─────────────────────────────────────────────────────────

    def _detectar_soporte_usuarios(self):
        """Detector sobre las cuentas ya cargadas en la pestaña 👥 Usuarios:
        cuenta de Invitado/Guest habilitada, cuentas locales activas cuya
        DIRECTIVA permite contraseña en blanco (PasswordRequired=No, y no
        marcadas como revisadas en BlankPasswordWhitelistDB), e identidades
        de dominio que no se pudieron resolver (SID de dominio no
        traducible en el último escaneo — posible controlador de dominio
        inalcanzable en ese momento).

        Nota sobre la contraseña en blanco: PasswordRequired=No NO
        significa que la cuenta tenga actualmente una contraseña en blanco
        (ni tiene en cuenta un PIN/Windows Hello, que es una credencial
        aparte) — significa que la directiva PERMITE dejarla en blanco, lo
        cual ya es en sí mismo un hueco de seguridad real (si alguna vez se
        borra la contraseña, no hay nada que lo impida). Por eso se avisa,
        pero con lenguaje que no da por hecho que la contraseña esté
        realmente vacía ahora mismo."""
        if not hasattr(self, 'users_model'):
            return []
        acciones = []
        ya_ofrecidas = set()

        for row in self.users_model.rows():
            nombre = (row.nombre or "").strip()
            nombre_lower = nombre.lower()
            if not nombre or nombre_lower in ya_ofrecidas:
                continue
            estado_activo = (row.estado or "").strip().lower() == "activo"
            tipo_local = (row.tipo or "").strip().lower() == "local"

            if nombre_lower in ("invitado", "guest") and estado_activo:
                ya_ofrecidas.add(nombre_lower)
                acciones.append((
                    f"usuario_invitado_habilitado::{nombre_lower}",
                    f"🚨 Cuenta de invitado «{nombre}» habilitada — desactivar",
                    lambda n=nombre: self._soporte_desactivar_usuario(n),
                ))
                continue

            if (estado_activo and tipo_local
                    and (row.password_required or "").strip().lower() == "no"
                    and row.sid and not self.blank_password_whitelist_db.is_whitelisted(row.sid)):
                ya_ofrecidas.add(nombre_lower)
                acciones.append((
                    f"usuario_password_blanco_exigir::{row.sid}",
                    f"⚠️ «{nombre}» permite contraseña en blanco — 🔒 Exigir contraseña (activar directiva)",
                    lambda r=row: self._soporte_exigir_password(r),
                ))
                acciones.append((
                    f"usuario_password_blanco_confirmar::{row.sid}",
                    f"⚠️ «{nombre}» permite contraseña en blanco — ✅ Ya lo sé, confirmar (dejar de avisar)",
                    lambda r=row: self._soporte_confirmar_password_blanco(r),
                ))

        if any((r.tipo or "").strip().lower() == "dominio (no resuelto)" for r in self.users_model.rows()):
            acciones.append((
                "usuarios_dominio_no_resuelto",
                "🔄 Reintentar resolución de identidades de dominio",
                self.refresh_users,
            ))

        return acciones

    def _soporte_exigir_password(self, row):
        """Activa la directiva 'contraseña obligatoria' para esta cuenta
        vía 'net user /passwordreq:yes' — el mismo mecanismo que la casilla
        'El usuario no necesita contraseña' de Usuarios y contraseñas.
        NO establece ni cambia la contraseña actual, solo impide que sea
        blanco a partir de ahora.

        RIESGO real y explícito en la confirmación: si la cuenta tiene
        AHORA MISMO una contraseña realmente en blanco (no solo permitida
        por directiva, sino vacía de verdad), esto puede impedir iniciar
        sesión con ella hasta asignarle una contraseña real — no hay forma
        segura de comprobar el valor actual de una contraseña desde aquí,
        así que la decisión de que es seguro aplicarlo queda en manos del
        técnico, no de MASys."""
        if not is_admin():
            self._avisar("Permisos insuficientes",
                         "Esta acción requiere ejecutar MASys como administrador.",
                         QMessageBox.Warning)
            return

        if not self._confirmar_reparacion(
            "Exigir contraseña (activar directiva)",
            cambios=[f"«{row.nombre}» ya no podrá tener una contraseña en blanco a partir de ahora"],
            reversible=True, objetivo=row.nombre, es_alerta=True,
            explicacion=(
                "Esto NO establece ni cambia la contraseña actual de la cuenta — solo activa la "
                "directiva que impide dejarla en blanco a partir de ahora.\n\n"
                "⚠️ RIESGO: si esta cuenta tiene AHORA MISMO una contraseña realmente en blanco "
                "(no solo permitida, sino vacía de verdad), esta acción puede impedir iniciar "
                "sesión con ella hasta asignarle una contraseña real. Aplícalo solo si sabes que "
                "ya usa una contraseña o PIN configurado."
            ),
        ):
            return

        result = subprocess.run(
            ["net", "user", row.nombre, "/passwordreq:yes"],
            capture_output=True, timeout=15,
            startupinfo=self._get_startupinfo(),
        )
        exito = (result.returncode == 0)
        detalle = "OK" if exito else (
            result.stdout.decode(errors="replace").strip()
            or result.stderr.decode(errors="replace").strip()
            or "Sin detalles")

        self.repair_audit_db.registrar(
            seccion="👥 Usuarios", accion="Exigir contraseña (activar directiva)",
            objetivo=row.nombre, exito=exito, detalle=detalle,
        )
        if exito:
            self.set_status(f"✅ Directiva de contraseña obligatoria activada para «{row.nombre}»")
            if hasattr(self, 'refresh_users'):
                self.refresh_users()
        else:
            self._avisar("Error", f"No se pudo activar la directiva.\n\n{detalle}", QMessageBox.Warning)

    def _soporte_confirmar_password_blanco(self, row):
        """Confirma que la cuenta con directiva de contraseña en blanco ya
        se ha revisado (p. ej. usa PIN/Windows Hello, o es un caso
        aceptado) — deja de avisar sobre ESTA cuenta (clave SID).
        Reversible desde 'Ver cuentas con contraseña en blanco
        confirmadas'."""
        if not row.sid:
            self._avisar("Confirmar", "Esta cuenta no tiene SID válido — no se puede confirmar.",
                         QMessageBox.Warning)
            return
        if not self._confirmar_reparacion(
            "Confirmar cuenta revisada",
            cambios=[f"«{row.nombre}» dejará de generar este aviso"],
            reversible=True, objetivo=row.nombre,
            explicacion=(
                "La directiva de esta cuenta permite contraseña en blanco. Si ya revisaste que "
                "esto es intencionado (p. ej. inicia sesión con PIN/Windows Hello, o es un caso "
                "aceptado), confirma para dejar de ver este aviso. Puedes deshacerlo después desde "
                "'Ver cuentas con contraseña en blanco confirmadas'."
            ),
        ):
            return
        if self.blank_password_whitelist_db.add(row.sid, row.nombre, "Confirmado manualmente desde Soporte"):
            self.set_status(f"✅ «{row.nombre}» confirmado — no se volverá a avisar")
        else:
            self._avisar("Error", "No se pudo guardar la confirmación (fallo de base de datos local).",
                         QMessageBox.Warning)

    def _soporte_ver_password_blanco_whitelist(self):
        """Gestión de cuentas confirmadas: verlas y desconfirmarlas (para
        que el detector vuelva a avisar) — mismo patrón que 'Ver puertos
        comprobados'."""
        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle("Cuentas con contraseña en blanco confirmadas")
        dialog.setMinimumSize(640, 380)
        v = QVBoxLayout(dialog)

        tabla = QTableWidget(0, 3)
        tabla.setHorizontalHeaderLabels(["Cuenta", "Motivo", "Confirmado el"])
        tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        tabla.setSelectionBehavior(QTableWidget.SelectRows)
        tabla.setSelectionMode(QTableWidget.SingleSelection)
        tabla.verticalHeader().setVisible(False)
        _setup_header(tabla, 3, stretch_col=1)
        v.addWidget(tabla)

        lbl_vacio = QLabel("No hay ninguna cuenta confirmada todavía.")
        lbl_vacio.setVisible(False)
        v.addWidget(lbl_vacio)

        def _recargar():
            entradas = self.blank_password_whitelist_db.get_all()
            tabla.setSortingEnabled(False)
            tabla.setRowCount(len(entradas))
            for fila, entrada in enumerate(entradas):
                sid, nombre, motivo, confirmado_el = entrada
                item_nombre = QTableWidgetItem(nombre)
                item_nombre.setData(Qt.UserRole, entrada)
                tabla.setItem(fila, 0, item_nombre)
                tabla.setItem(fila, 1, QTableWidgetItem(motivo))
                tabla.setItem(fila, 2, QTableWidgetItem(confirmado_el))
            tabla.setSortingEnabled(True)
            tabla.model().layoutChanged.emit()
            lbl_vacio.setVisible(not entradas)
            btn_quitar.setEnabled(False)

        fila_botones = QHBoxLayout()
        btn_quitar = QPushButton("✕ Desconfirmar (volver a avisar)")
        btn_quitar.setEnabled(False)
        fila_botones.addWidget(btn_quitar)
        fila_botones.addStretch()
        v.addLayout(fila_botones)

        tabla.itemSelectionChanged.connect(
            lambda: btn_quitar.setEnabled(bool(tabla.selectionModel().selectedRows())))

        def _quitar_seleccionado():
            filas_sel = tabla.selectionModel().selectedRows()
            if not filas_sel:
                return
            sid, nombre, _motivo, _confirmado_el = tabla.item(filas_sel[0].row(), 0).data(Qt.UserRole)
            if self.blank_password_whitelist_db.remove(sid):
                self.set_status(f"✅ «{nombre}» desconfirmado — volverá a avisar")
            else:
                self._avisar("Desconfirmar",
                             "No se pudo desconfirmar la cuenta (fallo de base de datos local). Vuelve a intentarlo.",
                             QMessageBox.Warning)
            _recargar()

        btn_quitar.clicked.connect(_quitar_seleccionado)

        _recargar()

        fila_cerrar = QHBoxLayout()
        fila_cerrar.addStretch()
        btn_cerrar = QPushButton("Cerrar")
        btn_cerrar.clicked.connect(dialog.accept)
        fila_cerrar.addWidget(btn_cerrar)
        v.addLayout(fila_cerrar)
        dialog.exec_()

    def _soporte_desactivar_usuario(self, nombre: str):
        """Desactiva una cuenta local marcada como riesgo por el detector
        de Usuarios. Mismo mecanismo que UsuariosMixin._run_user_action,
        pero registrado en RepairAuditDB como corresponde a toda acción
        lanzada desde el botón de Soporte."""
        if not self._confirmar_reparacion(
            "Desactivar cuenta de riesgo",
            cambios=[f"Desactivar la cuenta «{nombre}»",
                     "El usuario no podrá iniciar sesión hasta que se reactive manualmente."],
            reversible=True,
            es_alerta=True,
        ):
            return

        nombre_escaped = escape_powershell_string(nombre)
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Disable-LocalUser -Name '{nombre_escaped}' -ErrorAction Stop"],
            capture_output=True, timeout=15,
            startupinfo=self._get_startupinfo()
        )
        exito = (result.returncode == 0)
        detalle = "OK" if exito else (result.stderr.decode(errors="replace").strip() or "Sin detalles")

        self.repair_audit_db.registrar(
            seccion="👥 Usuarios", accion="Desactivar cuenta de riesgo",
            objetivo=nombre, exito=exito, detalle=detalle,
        )
        if exito:
            self.set_status(f"✅ Cuenta {nombre} desactivada")
            if hasattr(self, 'refresh_users'):
                self.refresh_users()
        else:
            self._avisar("Error", f"No se pudo desactivar la cuenta.\n\n{detalle}", QMessageBox.Warning)

    # ── Red Local ──────────────────────────────────────────────────────

    def _detectar_soporte_red_local(self):
        """Detector de conflicto de IP: la misma IP con MACs distintas entre
        entradas 'Alcanzable'/'Permanente' (NO obsoletas) es un conflicto
        real en la LAN — dos dispositivos respondiendo a la misma
        dirección a la vez causa conectividad intermitente para ambos. Una
        entrada 'Obsoleto' no cuenta: es solo una IP que cambió de dueño
        recientemente (normal con DHCP), no un conflicto activo."""
        if not hasattr(self, 'red_local_model'):
            return []
        ips_activas = {}
        for r in self.red_local_model.rows():
            if r.estado.strip().lower() not in ("alcanzable", "permanente"):
                continue
            ips_activas.setdefault(r.ip, set()).add(r.mac.strip().lower())

        acciones = []
        for ip, macs in ips_activas.items():
            if len(macs) > 1:
                etiqueta = f"🚨 Conflicto de IP: {ip} respondida por {len(macs)} MACs distintas"
                acciones.append((f"red_local_conflicto_ip::{ip}", etiqueta, lambda ip=ip, macs=macs: self._soporte_mostrar_conflicto_ip(ip, macs)))

        # Recordatorio activo: dispositivos ya marcados como sospechosos en
        # un escaneo anterior que SIGUEN apareciendo en el escaneo actual —
        # el resaltado en rojo de la tabla ya lo muestra, pero solo si el
        # técnico mira; esto lo saca también al primer plano en Soporte.
        sospechosos_visibles = [r for r in self.red_local_model.rows() if r.sospechoso]
        if sospechosos_visibles:
            etiqueta = f"🚩 {len(sospechosos_visibles)} dispositivo(s) marcado(s) como sospechoso(s) en este escaneo"
            acciones.append(("red_local_sospechosos", etiqueta, lambda: self._soporte_mostrar_sospechosos_visibles(sospechosos_visibles)))

        return acciones

    def _soporte_mostrar_conflicto_ip(self, ip, macs):
        items = [f"MAC: {m}" for m in sorted(macs)]
        nota = (
            "Dos o más dispositivos están respondiendo a la misma IP en la red local — "
            "causa típica de conectividad intermitente para ambos. Revisa la configuración "
            "DHCP/IP estática de esos equipos."
        )
        self._mostrar_informe(f"Conflicto de IP: {ip}", [], items, nota, es_alerta=True)

    def _soporte_mostrar_sospechosos_visibles(self, dispositivos):
        items = [f"{d.ip} — {d.mac} ({d.tipo_estimado if d.tipo_estimado != 'N/A' else d.fabricante})"
                  for d in dispositivos]
        self._mostrar_informe(
            "Dispositivos sospechosos en este escaneo", [], items,
            "Marcados manualmente en un escaneo anterior — gestiona la lista completa desde "
            "'Ver equipos marcados como sospechosos'.",
            es_alerta=True)

    def _soporte_ver_sospechosos_red_local(self):
        """Gestión de dispositivos marcados como sospechosos: verlos y
        quitarlos — mismo patrón que 'Ver procesos marcados como
        legítimos', pero es la lista de 'a vigilar', no la de 'ignorar'."""
        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle("Equipos marcados como sospechosos")
        dialog.setMinimumSize(720, 400)
        v = QVBoxLayout(dialog)

        tabla = QTableWidget(0, 4)
        tabla.setHorizontalHeaderLabels(["MAC", "Última IP vista", "Motivo", "Marcado el"])
        tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        tabla.setSelectionBehavior(QTableWidget.SelectRows)
        tabla.setSelectionMode(QTableWidget.SingleSelection)
        tabla.verticalHeader().setVisible(False)
        _setup_header(tabla, 4, stretch_col=1)
        v.addWidget(tabla)

        lbl_vacio = QLabel("No hay ningún equipo marcado como sospechoso todavía.")
        lbl_vacio.setVisible(False)
        v.addWidget(lbl_vacio)

        def _recargar():
            entradas = self.dispositivo_sospechoso_db.get_all()
            tabla.setSortingEnabled(False)
            tabla.setRowCount(len(entradas))
            for fila, entrada in enumerate(entradas):
                mac, ip_visto, motivo, marcado_el = entrada
                for col, valor in enumerate([mac, ip_visto, motivo, marcado_el]):
                    item = QTableWidgetItem(valor)
                    if col == 0:
                        item.setData(Qt.UserRole, entrada)
                    tabla.setItem(fila, col, item)
            tabla.setSortingEnabled(True)
            tabla.model().layoutChanged.emit()
            lbl_vacio.setVisible(not entradas)
            btn_quitar.setEnabled(False)

        fila_botones = QHBoxLayout()
        btn_quitar = QPushButton("✕ Quitar marca de sospechoso")
        btn_quitar.setEnabled(False)
        fila_botones.addWidget(btn_quitar)
        fila_botones.addStretch()
        v.addLayout(fila_botones)

        tabla.itemSelectionChanged.connect(
            lambda: btn_quitar.setEnabled(bool(tabla.selectionModel().selectedRows())))

        def _quitar_seleccionado():
            filas_sel = tabla.selectionModel().selectedRows()
            if not filas_sel:
                return
            mac, _ip_visto, _motivo, _marcado_el = tabla.item(filas_sel[0].row(), 0).data(Qt.UserRole)
            if self.dispositivo_sospechoso_db.remove(mac):
                self.set_status(f"✅ {mac} quitado de la lista de sospechosos")
                if hasattr(self, 'red_local_model'):
                    for row in self.red_local_model.rows():
                        if row.mac.strip().lower() == mac:
                            row.sospechoso = False
                    self.red_local_model.setRows(self.red_local_model.rows())
            else:
                self._avisar("Quitar marca de sospechoso",
                             "No se pudo quitar la marca (fallo de base de datos local).",
                             QMessageBox.Warning)
            _recargar()

        btn_quitar.clicked.connect(_quitar_seleccionado)

        _recargar()

        fila_cerrar = QHBoxLayout()
        fila_cerrar.addStretch()
        btn_cerrar = QPushButton("Cerrar")
        btn_cerrar.clicked.connect(dialog.accept)
        fila_cerrar.addWidget(btn_cerrar)
        v.addLayout(fila_cerrar)
        dialog.exec_()
