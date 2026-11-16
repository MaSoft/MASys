# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña Usuarios: tabla, filtro, menu contextual, activar/desactivar, permisos e historial, exportar.
Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).

Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

import os
import csv
import time
import subprocess
from typing import List
from html import escape as html_escape

from PyQt5.QtCore import Qt, QRegExp
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QCheckBox, QPushButton, QMenu, QMessageBox, QTableView,
    QTextEdit, QDialog,
)

from msys.core.utils import escape_powershell_string, ruta_guardado_predeterminada, dialogo_guardar_archivo, _SafeCsvWriter
from msys.models.rows import UserRow
from msys.models.table_models import UserTableModel, UserFilterProxy
from msys.widgets.delegates import _setup_header
from msys.collectors import UsersCollector


# 5.4.0: antes «{verbo}ando» daba «Finalizarando», «Detenerando», «Activarando»...
_GERUNDIO = {"Finalizar": "Finalizando", "Suspender": "Suspendiendo", "Reanudar": "Reanudando", "Iniciar": "Iniciando", "Detener": "Deteniendo", "Reiniciar": "Reiniciando", "Activar": "Activando", "Desactivar": "Desactivando"}

class UsuariosMixin:
    def crear_tab_users(self):
        """Crea la pestaña de usuarios del sistema."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        # ── Barra de herramientas ────────────────────────────────────────
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        import socket
        hostname = socket.gethostname()
        lbl_hostname = QLabel(f"🖥️ {hostname}")
        lbl_hostname.setObjectName("hostnameLabel")
        toolbar.addWidget(lbl_hostname)

        self.search_users = QLineEdit()
        self.search_users.setPlaceholderText("🔎 Buscar usuarios...")
        self.search_users.textChanged.connect(lambda: self.users_proxy.setFilterRegExp(QRegExp(self.search_users.text(), Qt.CaseInsensitive, QRegExp.FixedString)))
        toolbar.addWidget(self.search_users, 3)

        self.chk_users_local = QCheckBox("Local")
        self.chk_users_local.setChecked(True)
        self.chk_users_local.stateChanged.connect(self.apply_users_filter)
        toolbar.addWidget(self.chk_users_local)

        self.chk_users_domain = QCheckBox("Dominio")
        self.chk_users_domain.setChecked(True)
        self.chk_users_domain.stateChanged.connect(self.apply_users_filter)
        toolbar.addWidget(self.chk_users_domain)

        self.chk_users_entra = QCheckBox("Entra ID")
        self.chk_users_entra.setChecked(True)
        self.chk_users_entra.setToolTip("Cuentas de Entra ID (Azure AD) con perfil local en este equipo")
        self.chk_users_entra.stateChanged.connect(self.apply_users_filter)
        toolbar.addWidget(self.chk_users_entra)

        self.chk_users_system = QCheckBox("Sistema")
        self.chk_users_system.setChecked(True)
        self.chk_users_system.stateChanged.connect(self.apply_users_filter)
        toolbar.addWidget(self.chk_users_system)

        self.chk_users_active = QCheckBox("Solo Activos")
        self.chk_users_active.setChecked(False)
        self.chk_users_active.stateChanged.connect(self.apply_users_filter)
        toolbar.addWidget(self.chk_users_active)

        toolbar.addStretch()

        self.btn_refresh_users = QPushButton("🔄 Actualizar")
        self.btn_refresh_users.setObjectName("primary")
        self.btn_refresh_users.clicked.connect(self.refresh_users)
        toolbar.addWidget(self.btn_refresh_users)

        layout.addLayout(toolbar)

        # ── Segunda fila: tipo de unión del equipo (Entra ID/dominio) ────
        info_layout = QHBoxLayout()
        info_layout.setSpacing(8)
        self.lbl_tipo_union_equipo = QLabel("🔐 Tipo de unión: consultando...")
        self.lbl_tipo_union_equipo.setObjectName("hintLabel")
        self.lbl_tipo_union_equipo.setToolTip(
            "A qué está unido este equipo (Entra ID / Active Directory / "
            "grupo de trabajo). Si no está unido a ninguno, es normal que "
            "no aparezcan usuarios de Dominio ni de Entra ID.")
        info_layout.addWidget(self.lbl_tipo_union_equipo)
        info_layout.addStretch()

        self.lbl_users_count = QLabel("Usuarios: 0/0")
        self.lbl_users_count.setObjectName("countLabel")
        info_layout.addWidget(self.lbl_users_count)

        layout.addLayout(info_layout)

        # Tabla
        self.users_model = UserTableModel([])
        self.users_proxy = UserFilterProxy(self)
        self.users_proxy.setSourceModel(self.users_model)
        self.users_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.users_proxy.setFilterKeyColumn(-1)

        self.users_table = QTableView()
        self.users_table.setModel(self.users_proxy)
        self.users_table.setAlternatingRowColors(True)
        self.users_table.setSelectionBehavior(QTableView.SelectRows)
        self.users_table.verticalHeader().setVisible(False)
        self.users_table.setSortingEnabled(True)
        self.users_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.users_table.customContextMenuRequested.connect(self._ctx_usuarios)

        _setup_header(self.users_table, 13, stretch_col=7)

        layout.addWidget(self.users_table)

        return tab

    def refresh_users(self):
        """Actualiza lista de usuarios del sistema."""
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.set_status("🔄 Escaneando usuarios del sistema...")
        self.btn_refresh_users.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.worker = UsersCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_users_data)
        self.worker.finished_error.connect(self._on_users_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.tipo_union.connect(self._on_tipo_union_equipo)
        self.worker.start()

    def _on_tipo_union_equipo(self, texto: str):
        """Actualiza la etiqueta de tipo de unión del equipo (Entra ID/
        dominio/grupo de trabajo), o la oculta si no se pudo determinar."""
        if texto:
            self.lbl_tipo_union_equipo.setText(f"🔐 Tipo de unión: {texto}")
            self.lbl_tipo_union_equipo.setVisible(True)
        else:
            self.lbl_tipo_union_equipo.setVisible(False)

    def _on_users_data(self, rows: List[UserRow]):
        """Procesa datos de usuarios."""
        self.users_model.setRows(rows)
        self.btn_refresh_users.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.apply_users_filter()  # Aplicar filtros después de cargar los datos
        total = len(rows)
        self.set_status(f"✅ {total} usuarios encontrados")

    def _on_users_error(self, msg: str):
        """Maneja errores de usuarios."""
        self.btn_refresh_users.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al buscar usuarios")
        self._avisar("Error", f"No se pudieron obtener los usuarios:\n\n{msg}", QMessageBox.Warning)

    def apply_users_filter(self):
        """Aplica filtros a la tabla de usuarios."""
        # Obtener filtros activos
        show_local = self.chk_users_local.isChecked()
        show_domain = self.chk_users_domain.isChecked()
        show_entra = self.chk_users_entra.isChecked()
        show_system = self.chk_users_system.isChecked()
        only_active = self.chk_users_active.isChecked()

        # Aplicar filtros al proxy
        self.users_proxy.setFilters(show_local, show_domain, show_entra, show_system, only_active)

        # Actualizar contador
        self.lbl_users_count.setText(f"Usuarios: {self.users_proxy.rowCount()}/{self.users_model.rowCount()}")

    def _ctx_usuarios(self, pos):
        idx = self.users_table.indexAt(pos)
        if not idx.isValid():
            return
        row = idx.row()
        nombre = str(self.users_proxy.data(self.users_proxy.index(row, 0), Qt.DisplayRole) or "")
        estado = str(self.users_proxy.data(self.users_proxy.index(row, 4), Qt.DisplayRole) or "").lower()
        tipo   = str(self.users_proxy.data(self.users_proxy.index(row, 2), Qt.DisplayRole) or "").lower()

        # Enable-LocalUser/Disable-LocalUser sólo operan sobre cuentas
        # LOCALES reales. Dominio/Entra ID/Sistema no son gestionables así
        # (con -ErrorAction SilentlyContinue el cmdlet fallaría en
        # silencio y la UI reportaría "completado" sin haber hecho nada).
        tipo_gestionable = (tipo.strip() == "local")

        menu = QMenu(self)
        if not tipo_gestionable:
            if "sistema" in tipo:
                motivo = "Cuenta de sistema"
            else:
                motivo = "Cuenta externa (dominio/Entra ID) — gestiónala desde su origen"
            act_info = menu.addAction(f"ℹ️ {motivo} — sin acciones disponibles aquí")
            act_info.setEnabled(False)
        else:
            act_enable  = menu.addAction("✅ Activar cuenta")
            act_disable = menu.addAction("🚫 Desactivar cuenta")
            # Comparación exacta: "activo" in "inactivo" da True por
            # subcadena, lo que habilitaba "Desactivar" también en cuentas
            # ya inactivas.
            act_enable.setEnabled(estado.strip() == "inactivo")
            act_disable.setEnabled(estado.strip() == "activo")
        menu.addSeparator()
        act_copy = menu.addAction("📋 Copiar fila")

        action = menu.exec_(self.users_table.viewport().mapToGlobal(pos))
        if tipo_gestionable:
            if action == act_enable:
                if self._confirmar_accion("Activar cuenta", f"¿Activar la cuenta «{nombre}»?"):
                    self._run_user_action("Enable-LocalUser", nombre)
            elif action == act_disable:
                if self._confirmar_accion("Desactivar cuenta",
                    f"¿Desactivar la cuenta «{nombre}»?\nEl usuario no podrá iniciar sesión."):
                    self._run_user_action("Disable-LocalUser", nombre)
        if action == act_copy:
            cols = [self.users_proxy.data(self.users_proxy.index(row, c), Qt.DisplayRole)
                    for c in range(13)]
            QApplication.clipboard().setText("\t".join(str(v or "") for v in cols))

    def _run_user_action(self, cmd: str, nombre: str):
        label = "Activar" if "Enable" in cmd else "Desactivar"
        self.set_status(f"🔄 {_GERUNDIO.get(label, label)} cuenta {nombre}...")
        nombre_escaped = escape_powershell_string(nombre)
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"{cmd} -Name '{nombre_escaped}' -ErrorAction Stop"],
            capture_output=True, timeout=15,
            startupinfo=self._get_startupinfo()
        )
        # 5.4.0: -ErrorAction Stop (antes SilentlyContinue: el aviso de error salía «Sin detalles»)
        # y la acción va al historial de reparaciones
        self._registrar_historial("👥 Usuarios", f"{label} cuenta", nombre, result.returncode == 0,
                                  result.stderr.decode("cp850", errors="replace").strip()[:500])
        if result.returncode == 0:
            self.set_status(f"✅ Cuenta {nombre} — {label} completado")
            self.refresh_users()
        else:
            err = result.stderr.decode("cp850", errors="replace").strip()
            self._avisar("Error", f"No se pudo {label.lower()} la cuenta.\n\n{err or 'Sin detalles'}", QMessageBox.Warning)

    def copy_users_selection(self):
        """Copia usuarios seleccionados al portapapeles."""
        selected_indexes = self.users_table.selectionModel().selectedRows()
        if not selected_indexes:
            self._avisar("Copiar", "No hay usuarios seleccionados.")
            return

        lines = []
        lines.append("=" * 80)
        lines.append("USUARIOS DEL SISTEMA")
        lines.append("=" * 80)
        lines.append("")

        for index in selected_indexes:
            row = index.row()
            nombre = str(self.users_proxy.data(self.users_proxy.index(row, 0), Qt.DisplayRole) or "")
            nombre_completo = str(self.users_proxy.data(self.users_proxy.index(row, 1), Qt.DisplayRole) or "")
            tipo = str(self.users_proxy.data(self.users_proxy.index(row, 2), Qt.DisplayRole) or "")
            dominio = str(self.users_proxy.data(self.users_proxy.index(row, 3), Qt.DisplayRole) or "")
            estado = str(self.users_proxy.data(self.users_proxy.index(row, 4), Qt.DisplayRole) or "")
            ultima_conexion = str(self.users_proxy.data(self.users_proxy.index(row, 5), Qt.DisplayRole) or "")
            cuenta_vinculada = str(self.users_proxy.data(self.users_proxy.index(row, 12), Qt.DisplayRole) or "")

            lines.append(f"{nombre}")
            if nombre_completo:
                lines.append(f"  Nombre Completo: {nombre_completo}")
            lines.append(f"  Tipo: {tipo} | Dominio: {dominio} | Estado: {estado}")
            lines.append(f"  Cuenta Vinculada: {cuenta_vinculada}")
            lines.append(f"  Última Conexión: {ultima_conexion}")
            lines.append("")

        lines.append("=" * 80)

        text = "\n".join(lines)
        QApplication.clipboard().setText(text)
        self.set_status(f"✅ Copiados {len(selected_indexes)} usuarios al portapapeles")

    def export_users_csv(self):
        """Exporta usuarios a CSV."""
        if self.users_proxy.rowCount() == 0:
            self._avisar("Exportar CSV", "No hay usuarios para exportar.")
            return

        default_name = f"Usuarios_{time.strftime('%Y%m%d_%H%M%S')}.csv"
        path = dialogo_guardar_archivo(self, "Guardar CSV", ruta_guardado_predeterminada(default_name), "CSV (*.csv)")
        if not path:
            return

        try:
            headers = UserTableModel.HEADERS
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                writer = _SafeCsvWriter(csv.writer(f, delimiter=";"))
                writer.writerow(headers)
                for r in range(self.users_proxy.rowCount()):
                    row_data = []
                    for c in range(len(headers)):
                        idx = self.users_proxy.index(r, c)
                        val = self.users_proxy.data(idx, Qt.DisplayRole)
                        row_data.append(str(val) if val else "")
                    writer.writerow(row_data)

            total_exported = self.users_proxy.rowCount()
            self.set_status(f"✅ Exportado: {os.path.basename(path)} ({total_exported} usuarios)")
            self._avisar("Éxito", f"Exportados {total_exported} usuarios a:\n\n{path}")
        except Exception as e:
            self._avisar("Error", f"No se pudo exportar:\n\n{e}", QMessageBox.Critical)

    def export_users_html(self):
        """Exporta usuarios a HTML."""
        if self.users_proxy.rowCount() == 0:
            self._avisar("Exportar HTML", "No hay usuarios para exportar.")
            return

        default_name = f"Usuarios_{time.strftime('%Y%m%d_%H%M%S')}.html"
        path = dialogo_guardar_archivo(self, "Guardar HTML", ruta_guardado_predeterminada(default_name), "HTML (*.html)")
        if not path:
            return

        try:
            headers = UserTableModel.HEADERS
            total_rows = self.users_proxy.rowCount()

            rows_data = []
            for r in range(total_rows):
                row_data = []
                for c in range(len(headers)):
                    idx = self.users_proxy.index(r, c)
                    val = self.users_proxy.data(idx, Qt.DisplayRole)
                    row_data.append(str(val) if val else "")
                rows_data.append(row_data)

            html = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <title>Usuarios del Sistema - {time.strftime('%Y-%m-%d %H:%M:%S')}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 20px; }}
        .container {{ max-width: 100%; background: white; border-radius: 10px; box-shadow: 0 10px 40px rgba(0,0,0,0.3); overflow: hidden; }}
        header {{ background: linear-gradient(135deg, #2c3e50 0%, #34495e 100%); color: white; padding: 30px; }}
        h1 {{ font-size: 28px; margin-bottom: 10px; }}
        .subtitle {{ font-size: 14px; opacity: 0.9; }}
        table {{ width: 100%; border-collapse: collapse; }}
        thead {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; }}
        th {{ padding: 15px 10px; text-align: left; font-weight: 600; font-size: 13px; }}
        tbody tr {{ border-bottom: 1px solid #e9ecef; transition: background 0.2s; }}
        tbody tr:hover {{ background: #f8f9fa; }}
        td {{ padding: 12px 10px; font-size: 13px; color: #2c3e50; }}
        .badge {{ display: inline-block; padding: 4px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; }}
        .badge-success {{ background: #d4edda; color: #155724; }}
        .badge-warning {{ background: #fff3cd; color: #856404; }}
        .badge-danger {{ background: #f8d7da; color: #721c24; }}
        .badge-info {{ background: #cfe2ff; color: #084298; }}
        .badge-entra {{ background: #e0d4fd; color: #4c1d95; }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>👥 Usuarios del Sistema</h1>
            <div class="subtitle">Generado el {time.strftime('%d/%m/%Y a las %H:%M:%S')} | Total: {total_rows} usuarios</div>
        </header>
        <table>
            <thead>
                <tr>
"""
            for h in headers:
                html += f"                    <th>{h}</th>\n"
            html += """                </tr>
            </thead>
            <tbody>
"""
            for row_data in rows_data:
                html += "                <tr>\n"
                for idx, cell in enumerate(row_data):
                    cell = html_escape(str(cell))
                    # Aplicar badges a ciertas columnas
                    if idx == 2:  # Tipo
                        tipo_lower = cell.lower()
                        if "sistema" in tipo_lower:
                            cell = f'<span class="badge badge-warning">{cell}</span>'
                        elif "entra" in tipo_lower:
                            cell = f'<span class="badge badge-entra">{cell}</span>'
                        elif "dominio" in tipo_lower:
                            cell = f'<span class="badge badge-info">{cell}</span>'
                        elif "local" in tipo_lower:
                            cell = f'<span class="badge badge-success">{cell}</span>'
                    elif idx == 4:  # Estado
                        # 'inactivo' se comprueba antes que 'activo': por
                        # subcadena, "activo" in "inactivo" es True.
                        estado_lower = cell.lower()
                        if "inactivo" in estado_lower:
                            cell = f'<span class="badge badge-danger">{cell}</span>'
                        elif "activo" in estado_lower:
                            cell = f'<span class="badge badge-success">{cell}</span>'

                    html += f"                    <td>{cell}</td>\n"
                html += "                </tr>\n"

            html += """            </tbody>
        </table>
    </div>
</body>
</html>
"""
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)

            self.set_status(f"✅ Exportado: {os.path.basename(path)} ({total_rows} usuarios)")
            self._avisar("Éxito", f"Exportados {total_rows} usuarios a:\n\n{path}")
        except Exception as e:
            self._avisar("Error", f"No se pudo exportar:\n\n{e}", QMessageBox.Critical)

    def mostrar_permisos_usuario(self, identidad, recurso, ruta):
        """Muestra una ventana con los permisos del usuario en el recurso compartido."""
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Permisos de {identidad} en {recurso}")
        dialog.setMinimumSize(700, 500)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        layout = QVBoxLayout(dialog)

        # Información general
        info_text = f"""
        <h2>Permisos de Usuario</h2>
        <p><strong>Usuario/Grupo:</strong> {identidad}</p>
        <p><strong>Recurso Compartido:</strong> {recurso}</p>
        <p><strong>Ruta:</strong> {ruta}</p>
        <hr>
        """

        info_label = QLabel(info_text)
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        # Buscar todos los permisos de este usuario en este recurso
        permisos_text = QTextEdit()
        permisos_text.setReadOnly(True)

        permisos_info = "<h3>Permisos asignados:</h3><ul>"
        found = False

        for row_idx in range(self.proxy.rowCount()):
            recurso_idx = self.proxy.index(row_idx, 0)
            ruta_idx = self.proxy.index(row_idx, 1)
            identidad_idx = self.proxy.index(row_idx, 2)
            permiso_idx = self.proxy.index(row_idx, 3)
            tipo_idx = self.proxy.index(row_idx, 4)
            origen_idx = self.proxy.index(row_idx, 5)

            recurso_model = self.proxy.data(recurso_idx, Qt.DisplayRole) or ""
            identidad_model = self.proxy.data(identidad_idx, Qt.DisplayRole) or ""

            if recurso_model == recurso and identidad_model == identidad:
                permiso = html_escape(str(self.proxy.data(permiso_idx, Qt.DisplayRole) or ""))
                tipo = html_escape(str(self.proxy.data(tipo_idx, Qt.DisplayRole) or ""))
                origen = html_escape(str(self.proxy.data(origen_idx, Qt.DisplayRole) or ""))

                permisos_info += f"<li><strong>{permiso}</strong> - {tipo} (Origen: {origen})</li>"
                found = True

        if not found:
            permisos_info += "<li><em>No se encontraron permisos específicos</em></li>"

        permisos_info += "</ul>"
        permisos_text.setHtml(permisos_info)
        layout.addWidget(permisos_text)

        # Botón cerrar
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        btn_close = QPushButton("Cerrar")
        btn_close.clicked.connect(dialog.accept)
        btn_layout.addWidget(btn_close)
        layout.addLayout(btn_layout)

        dialog.exec_()

    def mostrar_historial_usuario(self, identidad, recurso, ruta):
        """Muestra el historial de conexiones del usuario al recurso compartido."""
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Historial de Conexiones - {identidad}")
        dialog.setMinimumSize(900, 600)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        layout = QVBoxLayout(dialog)

        # Información general
        info_text = f"""
        <h2>Historial de Conexiones</h2>
        <p><strong>Usuario/Grupo:</strong> {identidad}</p>
        <p><strong>Recurso Compartido:</strong> {recurso}</p>
        <p><strong>Ruta:</strong> {ruta}</p>
        <hr>
        """

        info_label = QLabel(info_text)
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        # Área de resultados
        resultados = QTextEdit()
        resultados.setReadOnly(True)
        resultados.setFont(QFont("Consolas", 9))
        layout.addWidget(resultados)

        # Obtener historial
        resultados.setPlainText("Consultando registros de eventos de Windows...\n\n")
        QApplication.processEvents()

        historial = self.obtener_historial_conexiones(identidad, ruta)

        if historial:
            texto_historial = f"Se encontraron {len(historial)} registro(s) de conexión:\n\n"
            texto_historial += "=" * 80 + "\n\n"

            for idx, registro in enumerate(historial, 1):
                texto_historial += f"Conexión #{idx}\n"
                texto_historial += f"  Fecha/Hora: {registro.get('fecha', 'N/A')}\n"
                texto_historial += f"  Usuario: {registro.get('usuario', 'N/A')}\n"
                texto_historial += f"  IP Origen: {registro.get('ip', 'N/A')}\n"
                texto_historial += f"  Recurso: {registro.get('recurso', 'N/A')}\n"
                texto_historial += f"  Evento: {registro.get('evento', 'N/A')}\n"
                texto_historial += "-" * 80 + "\n\n"

            resultados.setPlainText(texto_historial)
        else:
            resultados.setPlainText(
                "No se encontraron registros de conexión en los eventos de Windows.\n\n"
                "Esto puede deberse a:\n"
                "• El registro de auditoría no está habilitado\n"
                "• Los eventos han sido limpiados\n"
                "• El usuario no ha accedido recientemente\n"
                "• Se requieren permisos de administrador para ver estos eventos"
            )

        # Botones
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        btn_actualizar = QPushButton("🔄 Actualizar")
        btn_actualizar.clicked.connect(lambda: self.mostrar_historial_usuario(identidad, recurso, ruta))
        btn_layout.addWidget(btn_actualizar)

        btn_close = QPushButton("Cerrar")
        btn_close.clicked.connect(dialog.accept)
        btn_layout.addWidget(btn_close)

        layout.addLayout(btn_layout)

        dialog.exec_()

