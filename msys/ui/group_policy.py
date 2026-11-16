# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña 🏛️ Políticas de Grupo — estado de unión
a dominio/Entra ID (dsregcmd) + tabla de GPOs aplicadas (gpresult /x) +
inscripción Intune/MDM. A diferencia de 🏢 Active Directory, NO depende de
RSAT en ningún punto: siempre visible, sea cual sea el equipo (workgroup,
dominio, Entra ID o híbrido) — ver PS_GET_GROUP_POLICY_INFO
(msys/ps_scripts.py) para el porqué (dsregcmd/gpresult son binarios
nativos de Windows).

Mismo lenguaje visual que el resto de la app: cabecera de UNA fila
(hostname + búsqueda + acciones + Actualizar a la derecha, igual que
Seguridad/AD/Sistema), un par de fichas informativas de contexto arriba
(Estado de unión / Intune) y la tabla real de GPOs aplicadas como
contenido principal — no una pantalla entera de fichas.

El botón "📥 Instalar RSAT" (_instalar_rsat) vive en ActiveDirectoryMixin
(msys/ui/ad_info.py) y se reutiliza aquí tal cual — mismo acción, dos
sitios donde tiene sentido ofrecerla (aquí solo mejora el detalle de GPOs
de dominio vía Get-GPOReport; no aporta nada al lado de Entra ID/Intune).

Reutiliza self._crear_ficha_sistema() (SistemaMixin) para las 2 fichas de
contexto — mismo componente visual que el resto de la app."""

import os
import subprocess
import time

from PyQt5.QtCore import Qt, QRegExp
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QMessageBox, QTableView,
)

from msys.core.utils import ruta_guardado_predeterminada, dialogo_guardar_archivo
from msys.models.rows import GroupPolicyRow
from msys.models.table_models import GroupPolicyTableModel, _FechaAwareSortProxy
from msys.widgets.delegates import _setup_header
from msys.collectors import GroupPolicyInfoCollector, GroupPolicyReportCollector


class GroupPolicyMixin:
    def crear_tab_politicas_grupo(self):
        """Crea la pestaña de Políticas de Grupo."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        # Cabecera: TODO en una fila, mismo orden que el resto de la app
        # (hostname, búsqueda, resto de acciones, "Actualizar" pegado a la
        # derecha del todo).
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)

        import socket
        hostname = socket.gethostname()
        lbl_hostname = QLabel(f"🖥️ {hostname}")
        lbl_hostname.setObjectName("hostnameLabel")
        actions_layout.addWidget(lbl_hostname)

        self.search_politicas_grupo = QLineEdit()
        self.search_politicas_grupo.setPlaceholderText("🔎 Buscar en políticas de grupo...")
        self.search_politicas_grupo.textChanged.connect(
            lambda: self.gp_proxy.setFilterRegExp(
                QRegExp(self.search_politicas_grupo.text(), Qt.CaseInsensitive, QRegExp.FixedString)))
        actions_layout.addWidget(self.search_politicas_grupo, 3)

        actions_layout.addStretch()

        btn_instalar_rsat = QPushButton("📥 Instalar RSAT")
        btn_instalar_rsat.setToolTip(
            "El módulo de Directivas de Grupo de RSAT permite un informe más detallado "
            "de las GPO de dominio (requiere administrador)")
        btn_instalar_rsat.clicked.connect(self._instalar_rsat)
        actions_layout.addWidget(btn_instalar_rsat)

        # La accion que de verdad se pidio "gestionar" politicas: abrir el
        # Editor de Directivas de Grupo NATIVO (gpedit.msc), no un informe
        # de solo lectura. Se decidio explicitamente no reimplementar un
        # editor de politicas dentro de MASys (reinventaria un
        # componente enorme de Windows, con mucho riesgo de bugs frente al
        # nativo) -- ver conversacion de diseño 20260729.
        self.btn_abrir_gpedit = QPushButton("🛠️ Abrir Editor de Directivas de Grupo")
        self.btn_abrir_gpedit.setObjectName("ghost")
        self.btn_abrir_gpedit.setToolTip(
            "Abre el Editor de Directivas de Grupo nativo de Windows (gpedit.msc): "
            "permite ver Y MODIFICAR cada política, no solo consultar cuáles están "
            "aplicadas. No disponible en ediciones Windows Home.")
        self.btn_abrir_gpedit.clicked.connect(self._abrir_editor_gpedit)
        actions_layout.addWidget(self.btn_abrir_gpedit)

        self.btn_informe_gpo = QPushButton("📄 Ver informe detallado")
        self.btn_informe_gpo.setToolTip(
            "Genera el informe completo de gpresult (mismo detalle que el Editor de "
            "Directivas de Grupo: cada configuración aplicada, no solo el nombre de la GPO)")
        self.btn_informe_gpo.clicked.connect(self.exportar_informe_detallado_gpo)
        actions_layout.addWidget(self.btn_informe_gpo)

        self.btn_refresh_politicas_grupo = QPushButton("🔄 Actualizar")
        self.btn_refresh_politicas_grupo.setObjectName("primary")
        self.btn_refresh_politicas_grupo.clicked.connect(self.refresh_politicas_grupo)
        actions_layout.addWidget(self.btn_refresh_politicas_grupo)

        layout.addLayout(actions_layout)

        # Un par de fichas de contexto, NO una pantalla entera de fichas —
        # el contenido real es la tabla de abajo.
        self._politicas_grupo_fichas_layout = QHBoxLayout()
        self._politicas_grupo_fichas_layout.setSpacing(14)
        layout.addLayout(self._politicas_grupo_fichas_layout)

        # Tabla: una fila por GPO aplicada (Equipo y Usuario mezclados,
        # distinguidos por la columna Ámbito) — esto es lo que se pidió
        # mostrar, no un resumen en tarjetas.
        self.gp_model = GroupPolicyTableModel([])
        self.gp_proxy = _FechaAwareSortProxy(self)
        self.gp_proxy.setSourceModel(self.gp_model)
        self.gp_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.gp_proxy.setFilterKeyColumn(-1)

        self.gp_table = QTableView()
        self.gp_table.setModel(self.gp_proxy)
        self.gp_table.setAlternatingRowColors(True)
        self.gp_table.setSelectionBehavior(QTableView.SelectRows)
        self.gp_table.verticalHeader().setVisible(False)
        self.gp_table.setSortingEnabled(True)
        _setup_header(self.gp_table, len(GroupPolicyTableModel.HEADERS), stretch_col=1)

        layout.addWidget(self.gp_table, 1)

        return tab

    def _abrir_editor_gpedit(self):
        """Lanza gpedit.msc. Comprobación previa de existencia (a diferencia
        de devmgmt.msc/taskschd.msc, que están en TODAS las ediciones de
        Windows) porque gpedit.msc NO viene en Windows Home — sin este
        chequeo, el botón fallaría en silencio ahí (Start-Process dentro de
        PowerShell, lanzado con Popen sin esperar, no propaga el error)."""
        gpedit_path = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'System32', 'gpedit.msc')
        if not os.path.exists(gpedit_path):
            self._avisar(
                "Editor de Directivas de Grupo no disponible",
                "gpedit.msc no está presente en este equipo — normalmente porque es "
                "una edición Windows Home, que no incluye el Editor de Directivas de "
                "Grupo Local.",
                QMessageBox.Warning)
            return
        try:
            subprocess.Popen(["powershell", "-Command", "Start-Process gpedit.msc"], startupinfo=self._get_startupinfo())
            self.set_status("🛠️ Abriendo Editor de Directivas de Grupo...")
        except Exception as e:
            self._avisar("Error", f"No se pudo abrir el Editor de Directivas de Grupo:\n\n{e}", QMessageBox.Critical)

    def refresh_politicas_grupo(self):
        if not hasattr(self, 'btn_refresh_politicas_grupo'):
            return
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.set_status("🔄 Consultando estado de unión y directivas de grupo...")
        self.btn_refresh_politicas_grupo.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.worker = GroupPolicyInfoCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_politicas_grupo_data)
        self.worker.finished_error.connect(self._on_politicas_grupo_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_politicas_grupo_error(self, msg):
        self.btn_refresh_politicas_grupo.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al consultar políticas de grupo")
        self._avisar("Error", f"No se pudo obtener información de políticas de grupo:\n\n{msg}", QMessageBox.Warning)

    def _on_politicas_grupo_data(self, data: dict):
        self._last_politicas_grupo_info = data or {}
        self.btn_refresh_politicas_grupo.setEnabled(True)
        self.progress_bar.setVisible(False)

        self._construir_fichas_contexto_politicas_grupo(data)

        rows = []
        for ambito, clave_datos in [("Equipo", "GPOEquipo"), ("Usuario", "GPOUsuario")]:
            gpo_info = data.get(clave_datos, {}) or {}
            ou = gpo_info.get('OU', 'N/A')
            ultima = gpo_info.get('UltimaActualizacion', 'N/A')
            for gpo in (gpo_info.get('GPOs') or []):
                if gpo.get('AccesoDenegado'):
                    estado = "Acceso denegado"
                elif not gpo.get('Habilitada'):
                    estado = "Deshabilitada"
                else:
                    estado = "Aplicada"
                rows.append(GroupPolicyRow(
                    ambito=ambito, nombre=gpo.get('Nombre', 'N/A'),
                    estado=estado, ou=ou, ultima_actualizacion=ultima,
                ))
        self.gp_model.setRows(rows)

        if data.get('GPOError'):
            self.set_status(f"⚠️ {data['GPOError']}")
        else:
            self.set_status(f"✅ {len(rows)} directiva(s) de grupo encontrada(s)")

    def exportar_informe_detallado_gpo(self):
        """Genera el informe HTML de 'gpresult /h' — el mismo nivel de
        detalle que se ve en el Editor de Directivas de Grupo (rsop.msc):
        cada configuración de plantilla administrativa con su nombre
        amigable y su valor, no solo qué GPO está aplicada. Ver
        GroupPolicyReportCollector (msys/collectors.py) para el porqué de
        usar gpresult /h en vez de reimplementar la resolución de ADMX."""
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        default_name = f"PoliticasGrupo_detallado_{time.strftime('%Y%m%d_%H%M%S')}.html"
        path = dialogo_guardar_archivo(self, "Guardar informe detallado", ruta_guardado_predeterminada(default_name), "HTML (*.html)")
        if not path:
            return

        self.set_status("🔄 Generando informe detallado de políticas de grupo...")
        self.btn_informe_gpo.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.worker = GroupPolicyReportCollector(path)
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_informe_gpo_generado)
        self.worker.finished_error.connect(self._on_informe_gpo_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_informe_gpo_generado(self, path: str):
        self.btn_informe_gpo.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status(f"✅ Informe generado: {os.path.basename(path)}")
        resp = QMessageBox.information(
            self, "Informe generado",
            f"Informe detallado de políticas de grupo generado en:\n\n{path}",
            QMessageBox.Ok | QMessageBox.Open, QMessageBox.Ok,
        )
        if resp == QMessageBox.Open:
            os.startfile(path)

    def _on_informe_gpo_error(self, msg: str):
        self.btn_informe_gpo.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al generar el informe detallado")
        self._avisar("Error", f"No se pudo generar el informe detallado:\n\n{msg}", QMessageBox.Warning)

    def _construir_fichas_contexto_politicas_grupo(self, data: dict):
        while self._politicas_grupo_fichas_layout.count():
            item = self._politicas_grupo_fichas_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        estado = data.get('EstadoUnion', {}) or {}
        filas_estado = [
            ("Dominio/Workgroup", estado.get('Dominio', 'N/A')),
            (("Dominio AD", "Sí" if estado.get('DomainJoined') == 'YES' else "No"),
             ("Entra ID", "Sí" if estado.get('AzureAdJoined') == 'YES' else "No")),
        ]
        if estado.get('EnterpriseJoined') == 'YES':
            filas_estado.append(("Workplace Join", "Sí"))
        if estado.get('TenantName') and estado.get('TenantName') != 'N/A':
            filas_estado.append(("Tenant", estado.get('TenantName')))
        ficha_estado = self._crear_ficha_sistema("🪪", "Estado de unión", "#2563eb", filas_estado)
        self._politicas_grupo_fichas_layout.addWidget(ficha_estado)

        intune = data.get('Intune', {}) or {}
        inscrito = bool(intune.get('Inscrito'))
        ficha_intune = self._crear_ficha_sistema(
            "☁️", "Políticas Intune / MDM", "#16a34a" if inscrito else "#6b7280", [
                ("Inscrito", "✅ Sí" if inscrito else "No detectado"),
                ("Cuenta", intune.get('Cuenta', 'N/A')),
                ("Servidor de administración", intune.get('ServidorAdministracion', 'N/A')),
            ])
        self._politicas_grupo_fichas_layout.addWidget(ficha_intune)

        self._politicas_grupo_fichas_layout.addStretch()
