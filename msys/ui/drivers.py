# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña Adm. Dispositivos (antes "Controladores"):
tabla, filtro, refresco. Extraido de multisys.py (Fase 2 de modularizacion —
mixins de ShareInspector).

Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

import subprocess
import webbrowser
from urllib.parse import quote

from PyQt5.QtCore import Qt, QRegExp
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QCheckBox,
    QComboBox, QPushButton, QMenu, QMessageBox, QTableView,
)

from msys.core.utils import is_admin
from msys.models.rows import _dispositivo_reinicio_permitido
from msys.models.table_models import DriverTableModel, DriverFilterProxy
from msys.widgets.delegates import _setup_header
from msys.collectors import DriverCollector


class ControladoresMixin:
    def crear_tab_drivers(self):
        """Crea la pestaña de Controladores."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        # Barra de búsqueda y acciones — mismo patrón que Sistema/Actualizaciones:
        # hostname, búsqueda, hueco elástico, botón principal a la derecha.
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)

        import socket
        hostname = socket.gethostname()
        lbl_hostname = QLabel(f"🖥️ {hostname}")
        lbl_hostname.setObjectName("hostnameLabel")
        actions_layout.addWidget(lbl_hostname)

        self.search_drivers = QLineEdit()
        self.search_drivers.setPlaceholderText("🔎 Buscar controladores...")
        self.search_drivers.textChanged.connect(lambda: self.drivers_proxy.setFilterRegExp(QRegExp(self.search_drivers.text(), Qt.CaseInsensitive, QRegExp.FixedString)))
        actions_layout.addWidget(self.search_drivers, 3)

        actions_layout.addStretch()

        # "Abrir Administrador de dispositivos" es independiente de la fila
        # seleccionada — por eso es botón de barra y no entra en el menú
        # contextual, a diferencia de "Ver propiedades" (junto a "Buscar
        # controlador oficial" en el menú contextual: ambas actúan sobre el
        # dispositivo seleccionado en la tabla).
        self.btn_abrir_devmgmt = QPushButton("🔧 Abrir Administrador de dispositivos")
        self.btn_abrir_devmgmt.setObjectName("ghost")
        self.btn_abrir_devmgmt.clicked.connect(lambda: subprocess.Popen(
            ["powershell", "-Command", "Start-Process devmgmt.msc"],
            startupinfo=self._get_startupinfo()
        ))
        actions_layout.addWidget(self.btn_abrir_devmgmt)

        self.btn_refresh_drivers = QPushButton("🔄 Actualizar")
        self.btn_refresh_drivers.setObjectName("primary")
        self.btn_refresh_drivers.clicked.connect(self.refresh_drivers)
        actions_layout.addWidget(self.btn_refresh_drivers)

        layout.addLayout(actions_layout)

        # Fila de filtros, separada de la barra principal para no
        # amontonar controles: checkboxes + clase, sin más.
        filtros_layout = QHBoxLayout()
        filtros_layout.setSpacing(8)

        self.chk_sin_firmar_drivers = QCheckBox("Solo sin firmar")
        self.chk_sin_firmar_drivers.stateChanged.connect(self._filtrar_drivers)
        filtros_layout.addWidget(self.chk_sin_firmar_drivers)

        self.chk_con_problemas_drivers = QCheckBox("Solo con problemas")
        self.chk_con_problemas_drivers.stateChanged.connect(self._filtrar_drivers)
        filtros_layout.addWidget(self.chk_con_problemas_drivers)

        self.chk_antiguos_drivers = QCheckBox("Antiguos (+2 años)")
        self.chk_antiguos_drivers.setToolTip(
            "Informativo: un controlador antiguo no implica un problema real "
            "(hay controladores, p. ej. de chipset, que llevan años sin cambiar "
            "sin que eso sea un fallo).")
        self.chk_antiguos_drivers.stateChanged.connect(self._filtrar_drivers)
        filtros_layout.addWidget(self.chk_antiguos_drivers)

        self.chk_genericos_drivers = QCheckBox("Genéricos (no del fabricante)")
        self.chk_genericos_drivers.setToolTip(
            "El proveedor del controlador es Microsoft mientras que el hardware es de otro "
            "fabricante — Windows está usando su controlador genérico de caja en vez del "
            "específico. Informativo: muchos dispositivos funcionan bien así.")
        self.chk_genericos_drivers.stateChanged.connect(self._filtrar_drivers)
        filtros_layout.addWidget(self.chk_genericos_drivers)

        self.combo_clase_drivers = QComboBox()
        self.combo_clase_drivers.addItem("Todas las clases")
        self.combo_clase_drivers.currentIndexChanged.connect(self._filtrar_drivers)
        filtros_layout.addWidget(self.combo_clase_drivers)

        filtros_layout.addStretch()

        layout.addLayout(filtros_layout)

        # Tabla
        self.drivers_model = DriverTableModel([])
        self.drivers_proxy = DriverFilterProxy(self)
        self.drivers_proxy.setSourceModel(self.drivers_model)
        self.drivers_proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.drivers_proxy.setFilterKeyColumn(-1)

        self.drivers_table = QTableView()
        self.drivers_table.setModel(self.drivers_proxy)
        self.drivers_table.setAlternatingRowColors(True)
        self.drivers_table.setSelectionBehavior(QTableView.SelectRows)
        self.drivers_table.setSelectionMode(QTableView.SingleSelection)
        self.drivers_table.verticalHeader().setVisible(False)
        self.drivers_table.setSortingEnabled(True)
        self.drivers_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.drivers_table.customContextMenuRequested.connect(self._ctx_drivers)

        _setup_header(self.drivers_table, len(DriverTableModel.HEADERS), stretch_col=0)

        layout.addWidget(self.drivers_table)

        return tab

    def _filtrar_drivers(self):
        """Aplica los 5 filtros de Controladores (sin firmar / con
        problemas / antiguos / genéricos / clase) sobre el proxy a la vez."""
        self.drivers_proxy.setFiltros(
            self.chk_sin_firmar_drivers.isChecked(),
            self.chk_con_problemas_drivers.isChecked(),
            self.chk_antiguos_drivers.isChecked(),
            self.chk_genericos_drivers.isChecked(),
            self.combo_clase_drivers.currentText(),
        )

    def refresh_drivers(self):
        """Actualiza información de controladores del sistema."""
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.set_status("🔄 Escaneando controladores del sistema...")
        self.btn_refresh_drivers.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.worker = DriverCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_drivers_data)
        self.worker.finished_error.connect(self._on_drivers_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_drivers_data(self, rows: list):
        """Procesa datos de controladores."""
        self.drivers_model.setRows(rows)
        self.btn_refresh_drivers.setEnabled(True)
        self.progress_bar.setVisible(False)

        # Repuebla el desplegable de Clase con lo que haya de verdad en
        # esta pasada (antes no existía filtro por clase).
        clases = sorted({(row.clase or "Desconocido").strip() for row in rows})
        actual = self.combo_clase_drivers.currentText()
        self.combo_clase_drivers.blockSignals(True)
        self.combo_clase_drivers.clear()
        self.combo_clase_drivers.addItem("Todas las clases")
        self.combo_clase_drivers.addItems(clases)
        idx = self.combo_clase_drivers.findText(actual)
        self.combo_clase_drivers.setCurrentIndex(idx if idx >= 0 else 0)
        self.combo_clase_drivers.blockSignals(False)

        problema_count = sum(1 for row in rows if (row.problema or "").strip())
        sin_controlador_count = sum(1 for row in rows if (row.estado or "").strip().lower() == "sin controlador")

        if problema_count > 0:
            self.set_status(f"✅ {len(rows)} controladores encontrados (⚠️ {problema_count} con problemas, {sin_controlador_count} sin controlador)")
        else:
            self.set_status(f"✅ {len(rows)} controladores encontrados")

    def _on_drivers_error(self, msg: str):
        """Maneja errores de controladores."""
        self.btn_refresh_drivers.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al obtener controladores")
        self._avisar("Error", f"No se pudo obtener información de controladores:\n\n{msg}", QMessageBox.Warning)

    def _dispositivo_seleccionado(self):
        sel = self.drivers_table.selectionModel()
        if not sel or not sel.hasSelection():
            return None
        idx = sel.selectedRows()[0]
        source_index = self.drivers_proxy.mapToSource(idx)
        rows = self.drivers_model.rows()
        if 0 <= source_index.row() < len(rows):
            return rows[source_index.row()]
        return None

    def _ejecutar_reiniciar_dispositivo(self, row, seccion_origen: str) -> bool:
        """Deshabilita y vuelve a habilitar un dispositivo por su Device
        Instance ID — único punto de verdad usado tanto desde el menú
        contextual de esta pestaña como desde el detector de Soporte, para
        no duplicar la lógica de ejecución+auditoría+refresco."""
        ok_disable, msg_disable = self.driver_manager.disable_device(row.device_id)
        ok_enable, msg_enable = self.driver_manager.enable_device(row.device_id)
        ok = ok_disable and ok_enable
        detalle = "OK" if ok else f"deshabilitar: {msg_disable} | habilitar: {msg_enable}"
        self.repair_audit_db.registrar(
            seccion=seccion_origen, accion="Reiniciar dispositivo",
            objetivo=f"{row.dispositivo} ({row.device_id})", exito=ok, detalle=detalle,
        )
        if ok:
            self.set_status(f"✅ Dispositivo «{row.dispositivo}» reiniciado")
            if hasattr(self, 'refresh_drivers'):
                self.refresh_drivers()
        else:
            self._avisar("Con avisos", f"No se pudo reiniciar completamente el dispositivo:\n\n{detalle}", QMessageBox.Warning)
        return ok

    def _reiniciar_dispositivo_seleccionado(self):
        """Desde el menú contextual: el clásico 'apágalo y enciéndelo'
        aplicado a un dispositivo concreto, sin salir al Administrador de
        dispositivos. Bloqueado (no solo advertido) para clases de riesgo
        (disco, sistema, red...) — ver _dispositivo_reinicio_permitido."""
        row = self._dispositivo_seleccionado()
        if not row:
            return
        if not _dispositivo_reinicio_permitido(row):
            self._avisar(
                "Reiniciar dispositivo",
                "Por seguridad, esta clase de dispositivo no se puede reiniciar desde aquí "
                "— podría interrumpir el arranque o el acceso remoto. Hazlo desde el "
                "Administrador de dispositivos si estás seguro.",
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
        if not self._confirmar_accion(
            "Reiniciar dispositivo",
            f"¿Deshabilitar y volver a habilitar «{row.dispositivo}»?\n\n"
            "Puede provocar una breve interrupción del dispositivo mientras se reinicia "
            "(p. ej. pantalla en negro un instante si es la tarjeta gráfica, o sonido "
            "cortado si es de audio).",
        ):
            return
        self._ejecutar_reiniciar_dispositivo(row, seccion_origen="🖥️ Adm. Dispositivos")

    def _ejecutar_desinstalar_dispositivo(self, row, seccion_origen: str) -> bool:
        """Desinstala un dispositivo presente por su Device Instance ID
        (menú contextual de esta pestaña). Sin /force: el dispositivo sigue
        conectado, a diferencia del caso de HiddenDevicesDialog (dispositivo
        ya desconectado, ese sí necesita /force — ver
        DriverManager.remove_device). Más agresivo que reiniciar: el
        dispositivo deja de funcionar hasta que Windows (si acaso) lo vuelva
        a detectar y reinstale su controlador."""
        ok, msg = self.driver_manager.remove_device(row.device_id)
        self.repair_audit_db.registrar(
            seccion=seccion_origen, accion="Desinstalar dispositivo",
            objetivo=f"{row.dispositivo} ({row.device_id})", exito=ok, detalle=msg,
        )
        if ok:
            self.set_status(f"✅ Dispositivo «{row.dispositivo}» desinstalado")
            if hasattr(self, 'refresh_drivers'):
                self.refresh_drivers()
        else:
            self._avisar("Con avisos", f"No se pudo desinstalar el dispositivo:\n\n{msg}", QMessageBox.Warning)
        return ok

    def _desinstalar_dispositivo_seleccionado(self):
        """Desde el menú contextual: desinstala el dispositivo seleccionado.
        Mismo bloqueo por clase de riesgo que 'Reiniciar', pero con un aviso
        de confirmación más explícito al ser una acción menos reversible."""
        row = self._dispositivo_seleccionado()
        if not row:
            return
        if not _dispositivo_reinicio_permitido(row):
            self._avisar(
                "Desinstalar dispositivo",
                "Por seguridad, esta clase de dispositivo no se puede desinstalar desde "
                "aquí — podría interrumpir el arranque o el acceso remoto. Hazlo desde el "
                "Administrador de dispositivos si estás seguro.",
                QMessageBox.Warning,
            )
            return
        if not is_admin():
            self._avisar(
                "Se requieren privilegios de administrador",
                "Desinstalar un dispositivo requiere ejecutar MASys como administrador.",
                QMessageBox.Warning,
            )
            return
        if not self._confirmar_accion(
            "Desinstalar dispositivo",
            f"¿Desinstalar «{row.dispositivo}»?\n\n"
            "El dispositivo dejará de funcionar hasta que Windows lo vuelva a detectar y "
            "reinstale su controlador (a veces requiere desconectar/reconectar el hardware "
            "o reiniciar el equipo). Es más agresivo que 'Reiniciar dispositivo' — usa esto "
            "solo si reiniciar no ha funcionado.",
            icono=QMessageBox.Warning,
        ):
            return
        self._ejecutar_desinstalar_dispositivo(row, seccion_origen="🖥️ Adm. Dispositivos")

    def _ctx_drivers(self, pos):
        idx = self.drivers_table.indexAt(pos)
        if not idx.isValid():
            return
        self.drivers_table.selectRow(idx.row())
        row = self._dispositivo_seleccionado()
        if not row:
            return

        menu = QMenu(self)
        act_reiniciar = menu.addAction("🔁 Reiniciar dispositivo (deshabilitar y habilitar)")
        act_reiniciar.setEnabled(_dispositivo_reinicio_permitido(row))
        act_desinstalar = menu.addAction("✕ Desinstalar dispositivo")
        act_desinstalar.setEnabled(_dispositivo_reinicio_permitido(row))
        menu.addSeparator()
        fabricante = (row.fabricante or "").strip()
        act_buscar = menu.addAction("🔍 Buscar controlador oficial")
        act_buscar.setEnabled(bool(fabricante) and fabricante.lower() not in ("desconocido", "unknown"))
        act_propiedades = menu.addAction("📄 Ver propiedades")
        menu.addSeparator()
        act_copy = menu.addAction("📋 Copiar fila")

        action = menu.exec_(self.drivers_table.viewport().mapToGlobal(pos))
        if action == act_reiniciar:
            self._reiniciar_dispositivo_seleccionado()
        elif action == act_desinstalar:
            self._desinstalar_dispositivo_seleccionado()
        elif action == act_buscar:
            self._buscar_controlador_oficial(row)
        elif action == act_propiedades:
            self._ver_propiedades_dispositivo()
        elif action == act_copy:
            cols = [self.drivers_proxy.data(self.drivers_proxy.index(idx.row(), c), Qt.DisplayRole)
                    for c in range(len(DriverTableModel.HEADERS))]
            QApplication.clipboard().setText("\t".join(str(v or "") for v in cols))

    def _buscar_controlador_oficial(self, row):
        """Abre una búsqueda web (navegador por defecto) con el fabricante y
        el dispositivo — NUNCA descarga ni ejecuta nada: no existe una API
        universal para localizar e instalar el controlador correcto de cada
        fabricante (a diferencia de Windows Update, cada uno tiene su propia
        web sin estandarizar), así que aquí solo se acerca al técnico al
        sitio de búsqueda, la descarga y verificación las hace él."""
        consulta = f"{row.fabricante} {row.dispositivo} driver".strip()
        url = f"https://www.bing.com/search?q={quote(consulta)}"
        webbrowser.open(url)
        self.set_status(f"🔍 Búsqueda abierta en el navegador: {consulta}")

    def _ver_propiedades_dispositivo(self):
        """Menú contextual 'Ver propiedades': detalle crudo de pnputil (IDs
        de hardware, ubicación, controladores candidatos) del dispositivo
        seleccionado en la tabla, en un diálogo de solo lectura."""
        row = self._dispositivo_seleccionado()
        if not row:
            self._avisar("Ver propiedades", "Selecciona antes un dispositivo en la tabla.", QMessageBox.Warning)
            return
        texto = self.driver_manager.device_properties(row.device_id)
        from msys.ui.driver_properties_dialog import DriverPropertiesDialog
        dlg = DriverPropertiesDialog(self, row.dispositivo, texto)
        dlg.exec_()

