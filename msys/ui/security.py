# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña Seguridad: fichas de antivirus/firewall/
cifrado/endurecimiento, refresco, exportar.
Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).

Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos.

La vista de fichas reutiliza self._crear_ficha_sistema() (definida en
SistemaMixin) — mismo componente visual en las dos pestañas, sin duplicar
~100 líneas de estilo de tarjeta; es un helper genérico (emoji, título,
color, filas), no algo específico de Sistema."""

import csv

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QMessageBox, QFrame, QScrollArea, QGridLayout, QSizePolicy,
    QTreeWidget, QTreeWidgetItem, QTableWidget, QTableWidgetItem, QPlainTextEdit,
)

from msys.config import logger
from msys.core.utils import ruta_guardado_predeterminada, dialogo_guardar_archivo, _SafeCsvWriter
from msys.widgets.delegates import _setup_header
from msys.collectors import SecurityInfoCollector


class SeguridadTabMixin:
    def crear_tab_seguridad(self):
        """Crea la pestaña de Seguridad del sistema."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)

        import socket
        hostname = socket.gethostname()
        lbl_hostname = QLabel(f"🖥️ {hostname}")
        lbl_hostname.setObjectName("hostnameLabel")
        actions_layout.addWidget(lbl_hostname)

        self.search_seguridad = QLineEdit()
        self.search_seguridad.setPlaceholderText("🔎 Buscar en seguridad...")
        self.search_seguridad.textChanged.connect(self._aplicar_filtro_fichas_seguridad)
        actions_layout.addWidget(self.search_seguridad, 3)

        actions_layout.addStretch()

        self.btn_refresh_seguridad = QPushButton("🔄 Actualizar")
        self.btn_refresh_seguridad.setObjectName("primary")
        self.btn_refresh_seguridad.clicked.connect(self.refresh_seguridad)
        actions_layout.addWidget(self.btn_refresh_seguridad)

        layout.addLayout(actions_layout)

        # Árbol interno (no se muestra): fuente de datos de export_seguridad_csv,
        # igual que el patrón ya establecido en Sistema.
        self.seguridad_tree = QTreeWidget(self)
        self.seguridad_tree.setHeaderLabels(["Componente", "Valor"])

        self.seguridad_cards_scroll = QScrollArea()
        self.seguridad_cards_scroll.setWidgetResizable(True)
        self.seguridad_cards_scroll.setFrameShape(QFrame.NoFrame)

        seguridad_cards_container = QWidget()
        container_layout = QVBoxLayout(seguridad_cards_container)
        container_layout.setContentsMargins(2, 2, 2, 2)
        container_layout.setSpacing(14)

        grid_widget = QWidget()
        self.seguridad_cards_grid = QGridLayout(grid_widget)
        self.seguridad_cards_grid.setSpacing(14)
        self.seguridad_cards_grid.setContentsMargins(0, 0, 0, 0)
        container_layout.addWidget(grid_widget)

        self.seguridad_cards_scroll.setWidget(seguridad_cards_container)
        layout.addWidget(self.seguridad_cards_scroll)

        return tab

    def _crear_tabla_puertos_escucha(self, puertos):
        """Tabla compacta de puertos TCP en escucha — datos repetitivos,
        mismo criterio que la tabla de módulos de RAM en Sistema."""
        headers = ["Puerto", "Dirección", "Proceso"]
        tabla = QTableWidget(len(puertos), len(headers))
        tabla.setHorizontalHeaderLabels(headers)
        tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        tabla.setSelectionMode(QTableWidget.NoSelection)
        tabla.verticalHeader().setVisible(False)
        tabla.setAlternatingRowColors(True)
        tabla.setShowGrid(False)
        _setup_header(tabla, len(headers), stretch_col=2)
        for i, p in enumerate(puertos):
            valores = [str(p.get('Puerto', 'N/A')), p.get('Direccion', 'N/A'), p.get('Proceso', 'N/A')]
            for col, val in enumerate(valores):
                tabla.setItem(i, col, QTableWidgetItem(val))

        tabla.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        tabla.resizeRowsToContents()
        alto_filas = sum(tabla.rowHeight(i) for i in range(tabla.rowCount()))
        alto_total = tabla.horizontalHeader().height() + alto_filas + 2 * tabla.frameWidth()
        tabla.setFixedHeight(min(alto_total, 260))
        if alto_total > 260:
            tabla.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        return tabla

    def _crear_texto_archivo_hosts(self, contenido: str):
        """Caja de texto compacta, de solo lectura, con el contenido
        completo del archivo hosts — con su propio scroll interno para no
        disparar el alto de la ficha si el archivo es largo."""
        texto = QPlainTextEdit()
        texto.setPlainText(contenido or "(vacío)")
        texto.setReadOnly(True)
        texto.setLineWrapMode(QPlainTextEdit.NoWrap)
        texto.setStyleSheet("font-family: Consolas, 'Courier New', monospace; font-size: 8.5pt;")
        texto.setFixedHeight(160)
        return texto

    def _crear_lista_indicios(self, indicios, color_texto: str):
        """Lista de indicios como texto con viñetas — no hace falta un
        QListWidget completo para una lista corta de una sola columna de
        texto, y así hereda el 'background: transparent' del resto de la
        ficha sin tener que reestilarlo aparte."""
        lbl = QLabel("\n".join(f"•  {indicio}" for indicio in indicios))
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"font-size:9pt; color:{color_texto};")
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        return lbl

    def _construir_fichas_seguridad(self, data: dict):
        """Reconstruye la rejilla de fichas de Seguridad — rejilla simple
        que rellena de izquierda a derecha y salta de fila (sin el
        reparto asimétrico de columnas de Sistema, aquí todas las fichas
        pesan parecido)."""
        while self.seguridad_cards_grid.count():
            item = self.seguridad_cards_grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        fichas = []

        antivirus = data.get('Antivirus') or []
        if antivirus:
            filas_av = [(a.get('Nombre', 'N/A'), a.get('Estado', 'N/A')) for a in antivirus]
        else:
            defender = data.get('Defender', {}) or {}
            filas_av = [
                ("Windows Defender", defender.get('Habilitado', 'N/A')),
                ("Protección en tiempo real", defender.get('ProteccionTiempoReal', 'N/A')),
                ("Últ. actualización firmas", defender.get('UltimaActualizacion', 'N/A')),
            ]
        fichas.append(self._crear_ficha_sistema("🦠", "Antivirus", "#dc2626", filas_av))

        firewall = data.get('Firewall', {}) or {}
        etiquetas_perfil = {"Domain": "Dominio", "Private": "Privado", "Public": "Público"}
        filas_fw = [(etiquetas_perfil.get(k, k), v) for k, v in firewall.items()] or [("Estado", "N/A")]
        fichas.append(self._crear_ficha_sistema("🔥", "Firewall", "#ea580c", filas_fw))

        bitlocker = data.get('BitLocker', []) or []
        filas_bl = [(vol.get('Unidad', 'N/A'), vol.get('Estado', 'N/A')) for vol in bitlocker] or [("Estado", "Sin unidades")]
        fichas.append(self._crear_ficha_sistema("🔐", "BitLocker", "#7c3aed", filas_bl))

        fichas.append(self._crear_ficha_sistema("🔒", "Arranque seguro / TPM", "#0891b2", [
            ("Secure Boot", data.get('SecureBoot', 'N/A')),
            ("TPM", data.get('TPM', 'N/A')),
        ]))

        rdp = data.get('EscritorioRemoto', {}) or {}
        fichas.append(self._crear_ficha_sistema("🖧", "Escritorio Remoto (RDP)", "#2563eb", [
            ("Habilitado", rdp.get('Habilitado', 'N/A')),
            ("NLA", rdp.get('NLA', 'N/A')),
            ("Puerto", rdp.get('Puerto', 'N/A')),
        ]))

        endurecimiento = data.get('Endurecimiento', {}) or {}
        fichas.append(self._crear_ficha_sistema("🛡️", "Endurecimiento", "#16a34a", [
            ("UAC", endurecimiento.get('UAC', 'N/A')),
            ("SMBv1", endurecimiento.get('SMBv1', 'N/A')),
            ("PowerShell Remoting", endurecimiento.get('PSRemoting', 'N/A')),
            ("AutoRun/AutoPlay", endurecimiento.get('AutoRun', 'N/A')),
            ("Cuenta Administrador", endurecimiento.get('CuentaAdmin', 'N/A')),
            ("Cuenta Invitado", endurecimiento.get('CuentaInvitado', 'N/A')),
            ("Protección LSA", endurecimiento.get('LSAProtection', 'N/A')),
            ("Credential Guard", endurecimiento.get('CredentialGuard', 'N/A')),
            ("Protector de pantalla", endurecimiento.get('ProtectorPantalla', 'N/A')),
        ]))

        politica = data.get('PoliticaContrasenas', {}) or {}
        fichas.append(self._crear_ficha_sistema("🔑", "Política de contraseñas", "#9333ea", [
            ("Longitud mínima", politica.get('LongitudMinima', 'N/A')),
            ("Complejidad", politica.get('Complejidad', 'N/A')),
            ("Bloqueo tras intentos", politica.get('BloqueoTrasIntentos', 'N/A')),
            ("Caducidad", politica.get('CaducidadDias', 'N/A')),
        ]))

        puertos = data.get('Puertos', []) or []
        tabla_puertos = self._crear_tabla_puertos_escucha(puertos) if puertos else None
        fichas.append(self._crear_ficha_sistema(
            "🌐", "Puertos en escucha (TCP)", "#0f766e",
            [("Total", len(puertos))], widget_extra=tabla_puertos))

        # Indicios de software/licencias pirata: lista de señales a revisar
        # manualmente (KMS local, procesos/tareas/programas con nombre de
        # activador conocido, canal de activación de Windows) — NO es un
        # veredicto: puede haber falsos positivos, ver comentario del script.
        pirata_info = data.get('SoftwarePirata', {}) or {}
        indicios_pirata = pirata_info.get('Indicios', []) or []
        widget_pirata = self._crear_lista_indicios(indicios_pirata, "#be123c") if indicios_pirata else None
        fichas.append(self._crear_ficha_sistema("🚩", "Indicios de software / licencias pirata", "#be123c", [
            ("Estado de licencia Windows", pirata_info.get('EstadoLicenciaWindows', 'N/A')),
            ("Canal de activación", pirata_info.get('CanalActivacion', 'N/A')),
            ("Indicios encontrados", len(indicios_pirata)),
            ("Estado", "🚩 Hay indicios que revisar" if indicios_pirata else "✅ Sin indicios detectados"),
        ], widget_extra=widget_pirata))

        # Archivo hosts: contenido completo + qué entradas hay más allá del
        # localhost estándar de Windows (revisión manual — no es un
        # veredicto automático, ver comentario de PS_GET_SECURITY_INFO).
        # Va la última a propósito: con la rejilla de 3 columnas en
        # zigzag (ver más abajo), este es el orden que la deja justo debajo
        # de "Política de contraseñas" en su misma columna.
        hosts_info = data.get('ArchivoHosts', {}) or {}
        entradas_anadidas = hosts_info.get('EntradasAnadidas', []) or []
        widget_hosts = QWidget()
        lay_hosts = QVBoxLayout(widget_hosts)
        lay_hosts.setContentsMargins(0, 0, 0, 0)
        lay_hosts.setSpacing(6)
        if entradas_anadidas:
            lbl_anadidas = QLabel("Entradas añadidas (más allá del localhost estándar):")
            lbl_anadidas.setWordWrap(True)
            lbl_anadidas.setStyleSheet("font-size:9pt; font-weight:600; color:#b45309;")
            lay_hosts.addWidget(lbl_anadidas)
            lay_hosts.addWidget(self._crear_lista_indicios(entradas_anadidas, "#b45309"))
        lay_hosts.addWidget(self._crear_texto_archivo_hosts(hosts_info.get('ContenidoCompleto', '')))
        fichas.append(self._crear_ficha_sistema("📄", "Archivo Hosts", "#b45309", [
            ("Ruta", hosts_info.get('Ruta', 'N/A')),
            ("Última modificación", hosts_info.get('UltimaModificacion', 'N/A')),
            ("Entradas añadidas", len(entradas_anadidas)),
            ("Estado", "⚠️ Revisar — hay entradas añadidas" if hosts_info.get('Sospechoso')
                       else "✅ Sin entradas añadidas"),
        ], widget_extra=widget_hosts))

        if not fichas:
            vacio = QLabel("No hay datos. Pulsa 🔄 Actualizar.")
            vacio.setAlignment(Qt.AlignCenter)
            vacio.setStyleSheet("color:#6c757d; font-size:11pt; padding:40px;")
            self.seguridad_cards_grid.addWidget(vacio, 0, 0)
            return

        # Las fichas se apilan en columnas propias (QVBoxLayout, una por
        # columna), NO directamente celda a celda en la rejilla: puestas
        # directamente en el grid con Qt.AlignTop, una fila con fichas de
        # alturas distintas deja un hueco en blanco bajo la más baja antes
        # de la siguiente fila (la altura de fila la marca la ficha más
        # alta). Apilando en un QVBoxLayout propio, cada ficha se pega a la
        # siguiente con solo el spacing fijado, sin depender de lo que
        # midan las demás columnas — mismo criterio que ya se usa en
        # Sistema (_construir_fichas_sistema).
        max_cols = 3
        columnas = [QVBoxLayout() for _ in range(max_cols)]
        for col_layout in columnas:
            col_layout.setContentsMargins(0, 0, 0, 0)
            col_layout.setSpacing(14)

        for idx, card in enumerate(fichas):
            columnas[idx % max_cols].addWidget(card)

        for col, col_layout in enumerate(columnas):
            col_layout.addStretch()
            contenedor = QWidget()
            contenedor.setLayout(col_layout)
            contenedor.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
            self.seguridad_cards_grid.addWidget(contenedor, 0, col, Qt.AlignTop)
            self.seguridad_cards_grid.setColumnStretch(col, 1)
            # Ni Qt.AlignTop ni SizePolicy.Maximum bastaban por sí solos:
            # con 3 columnas de contenido desigual dentro del QScrollArea,
            # algunas columnas seguían estirándose por encima de su
            # sizeHint real (comprobado con medidas directas), separando
            # las fichas de más abajo del resto. Un tope MÁXIMO explícito
            # en píxeles, releído tras poblar el layout, es la única forma
            # que da un resultado consistente.
            contenedor.setMaximumHeight(contenedor.sizeHint().height())

        self.seguridad_cards_grid.setRowStretch(1, 1)

        self._aplicar_filtro_fichas_seguridad()

    def _fichas_seguridad(self):
        if not hasattr(self, 'seguridad_cards_scroll'):
            return []
        return self.seguridad_cards_scroll.findChildren(QFrame, "sysInfoCard")

    def _aplicar_filtro_fichas_seguridad(self):
        texto = self.search_seguridad.text().strip().lower()
        for card in self._fichas_seguridad():
            coincide = not texto or texto in (card.property("texto_busqueda") or "")
            card.setVisible(coincide)
            card.setProperty("oculto_por_filtro", not coincide)

    def refresh_seguridad(self):
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.set_status("🔄 Consultando estado de seguridad...")
        self.btn_refresh_seguridad.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)
        self.worker = SecurityInfoCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_seguridad_data)
        self.worker.finished_error.connect(self._on_seguridad_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_seguridad_data(self, data: dict):
        self._last_seguridad_info = data or {}
        self.seguridad_tree.clear()
        self.btn_refresh_seguridad.setEnabled(True)
        self.progress_bar.setVisible(False)

        if not data:
            self.seguridad_tree.addTopLevelItem(QTreeWidgetItem(['Error', 'No se pudo obtener información']))
            while self.seguridad_cards_grid.count():
                item = self.seguridad_cards_grid.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            err = QLabel("❌ No se pudo obtener información")
            err.setStyleSheet("color:#c0392b; font-size:11pt; padding:40px;")
            err.setAlignment(Qt.AlignCenter)
            self.seguridad_cards_grid.addWidget(err, 0, 0)
            self.set_status("❌ Error al obtener estado de seguridad")
            return

        antivirus = data.get('Antivirus') or []
        defender = data.get('Defender', {}) or {}
        av_item = QTreeWidgetItem(['🦠 Antivirus', f"{len(antivirus)} producto(s)" if antivirus else defender.get('Habilitado', 'N/A')])
        if antivirus:
            for a in antivirus:
                av_item.addChild(QTreeWidgetItem([a.get('Nombre', 'N/A'), a.get('Estado', 'N/A')]))
        else:
            av_item.addChild(QTreeWidgetItem(['Protección en tiempo real', defender.get('ProteccionTiempoReal', 'N/A')]))
            av_item.addChild(QTreeWidgetItem(['Última actualización de firmas', defender.get('UltimaActualizacion', 'N/A')]))
        if 'INACTIVA' in str(defender.get('ProteccionTiempoReal', '')):
            av_item.setForeground(1, QColor('#c0392b'))
        self.seguridad_tree.addTopLevelItem(av_item)
        av_item.setExpanded(True)

        firewall = data.get('Firewall', {}) or {}
        fw_item = QTreeWidgetItem(['🔥 Firewall', ''])
        for k, v in firewall.items():
            child = QTreeWidgetItem([k, v])
            if 'INACTIVO' in (v or ''):
                child.setForeground(1, QColor('#c0392b'))
            fw_item.addChild(child)
        self.seguridad_tree.addTopLevelItem(fw_item)
        fw_item.setExpanded(True)

        bitlocker = data.get('BitLocker', []) or []
        bl_item = QTreeWidgetItem(['🔐 BitLocker', f"{len(bitlocker)} unidad(es)"])
        for vol in bitlocker:
            estado = vol.get('Estado', 'N/A')
            v_item = QTreeWidgetItem([vol.get('Unidad', 'N/A'), estado])
            if 'Off' in (estado or '') or 'Unprotected' in (estado or ''):
                v_item.setForeground(1, QColor('#c0392b'))
            bl_item.addChild(v_item)
        self.seguridad_tree.addTopLevelItem(bl_item)
        bl_item.setExpanded(True)

        sb_item = QTreeWidgetItem(['🔒 Secure Boot', data.get('SecureBoot', 'N/A')])
        if 'Deshabilitado' in (data.get('SecureBoot') or ''):
            sb_item.setForeground(1, QColor('#c0392b'))
        self.seguridad_tree.addTopLevelItem(sb_item)

        self.seguridad_tree.addTopLevelItem(QTreeWidgetItem(['🔑 TPM', data.get('TPM', 'N/A')]))

        endurecimiento = data.get('Endurecimiento', {}) or {}
        end_item = QTreeWidgetItem(['🛡️ Endurecimiento', ''])
        for etiqueta, clave in [("UAC", "UAC"), ("SMBv1", "SMBv1"), ("PowerShell Remoting", "PSRemoting"),
                                 ("AutoRun/AutoPlay", "AutoRun"), ("Cuenta Administrador", "CuentaAdmin"),
                                 ("Cuenta Invitado", "CuentaInvitado"), ("Protección LSA", "LSAProtection"),
                                 ("Credential Guard", "CredentialGuard"), ("Protector de pantalla", "ProtectorPantalla")]:
            valor = endurecimiento.get(clave, 'N/A')
            child = QTreeWidgetItem([etiqueta, valor])
            if 'riesgo' in (valor or '').lower():
                child.setForeground(1, QColor('#c0392b'))
            end_item.addChild(child)
        self.seguridad_tree.addTopLevelItem(end_item)
        end_item.setExpanded(True)

        politica = data.get('PoliticaContrasenas', {}) or {}
        pol_item = QTreeWidgetItem(['🔑 Política de contraseñas', ''])
        for etiqueta, clave in [("Longitud mínima", "LongitudMinima"), ("Complejidad", "Complejidad"),
                                 ("Bloqueo tras intentos", "BloqueoTrasIntentos"), ("Caducidad", "CaducidadDias")]:
            valor = politica.get(clave, 'N/A')
            child = QTreeWidgetItem([etiqueta, valor])
            if 'riesgo' in (valor or '').lower():
                child.setForeground(1, QColor('#c0392b'))
            pol_item.addChild(child)
        self.seguridad_tree.addTopLevelItem(pol_item)
        pol_item.setExpanded(True)

        puertos = data.get('Puertos', []) or []
        p_item = QTreeWidgetItem(['🌐 Puertos en escucha (TCP)', f"{len(puertos)} puerto(s)"])
        for p in puertos:
            p_item.addChild(QTreeWidgetItem([str(p.get('Puerto', 'N/A')),
                                              f"{p.get('Direccion', 'N/A')} — {p.get('Proceso', 'N/A')}"]))
        self.seguridad_tree.addTopLevelItem(p_item)

        rdp = data.get('EscritorioRemoto', {}) or {}
        rdp_item = QTreeWidgetItem(['🖧 Escritorio Remoto (RDP)', rdp.get('Habilitado', 'N/A')])
        rdp_item.addChild(QTreeWidgetItem(['Habilitado', rdp.get('Habilitado', 'N/A')]))
        nla_item = QTreeWidgetItem(['NLA (autenticación a nivel de red)', rdp.get('NLA', 'N/A')])
        if 'INACTIVO' in (rdp.get('NLA', '') or ''):
            nla_item.setForeground(1, QColor('#c0392b'))
        rdp_item.addChild(nla_item)
        rdp_item.addChild(QTreeWidgetItem(['Puerto', rdp.get('Puerto', 'N/A')]))
        self.seguridad_tree.addTopLevelItem(rdp_item)

        self._construir_fichas_seguridad(data)

        self.set_status("✅ Estado de seguridad obtenido")

    def _on_seguridad_error(self, msg: str):
        self.btn_refresh_seguridad.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al obtener estado de seguridad")
        self._avisar("Error", f"No se pudo obtener el estado de seguridad:\n\n{msg}", QMessageBox.Warning)

    def copy_seguridad_selection(self):
        """Copia al portapapeles las fichas de Seguridad actualmente
        visibles (respeta el filtro de búsqueda) — mismo criterio que
        copy_system_selection en Sistema."""
        bloques = []
        for card in self._fichas_seguridad():
            if card.property("oculto_por_filtro"):
                continue
            texto = card.property("texto_copiar")
            if texto:
                bloques.append(texto)

        if not bloques:
            self._avisar("Copiar", "No hay información para copiar")
            return

        QApplication.clipboard().setText("\n\n".join(bloques))
        self.set_status(f"📋 {len(bloques)} ficha(s) copiada(s) al portapapeles")

    def export_seguridad_csv(self):
        """Exporta el estado de seguridad a CSV."""
        if self.seguridad_tree.topLevelItemCount() == 0:
            self._avisar("Exportar", "No hay datos para exportar", QMessageBox.Warning)
            return

        filename = dialogo_guardar_archivo(
            self, "Guardar CSV", ruta_guardado_predeterminada("seguridad.csv"), "CSV (*.csv)")
        if not filename:
            return

        try:
            with open(filename, 'w', newline='', encoding='utf-8-sig') as f:
                writer = _SafeCsvWriter(csv.writer(f, delimiter=';'))
                writer.writerow(['Categoría', 'Componente', 'Valor'])

                for i in range(self.seguridad_tree.topLevelItemCount()):
                    top_item = self.seguridad_tree.topLevelItem(i)
                    category = top_item.text(0)
                    writer.writerow([category, '', top_item.text(1)])
                    for j in range(top_item.childCount()):
                        child = top_item.child(j)
                        writer.writerow([category, child.text(0), child.text(1)])

            self.set_status(f"✅ Seguridad exportada a {filename}")
            self._avisar("Exportar", f"Datos exportados correctamente a:\n{filename}")
        except Exception as e:
            logger.exception(f"Error al exportar seguridad a CSV: {e}")
            self._avisar("Error", f"Error al exportar:\n{str(e)}", QMessageBox.Critical)
