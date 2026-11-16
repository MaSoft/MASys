# -*- coding: utf-8 -*-
"""
MASys — El cerebro detrás de tu sistema
Autor: Masoft.es (PyQt5)
Características:
  - Análisis de carpetas compartidas locales y permisos
  - Análisis de ruta específica con control de profundidad
  - Filtrado de carpetas ocultas
  - Sistema de checkpoints para recuperación de análisis
  - Visualización de unidades remotas configuradas
  - Gestión de credenciales de Windows
  - Gestión de usuarios del sistema (locales, dominio y sistema)
  - Interfaz con pestañas profesional y compacta
"""

import json
import os
import csv
import re
import sys
import sqlite3
import winreg
import queue
import threading
# Importado con alias porque muchos métodos de export usan una variable local
# llamada "html" para acumular el string del informe (colisionaría con el módulo).
from html import escape as html_escape
import time
import logging
import ctypes
import tempfile
import pickle
import shutil
from datetime import datetime
from dataclasses import dataclass, asdict, field
from typing import List, Optional, Dict, Any, Tuple

import win32service
import win32serviceutil
import pywintypes
import psutil

from PyQt5.QtCore import (
    Qt, QAbstractTableModel, QModelIndex, QVariant, QSortFilterProxyModel,
    QRegExp, QThread, pyqtSignal, QSettings, QTimer, QRectF, QEvent, QObject
)
from PyQt5.QtGui import QFont, QColor, QPainter, QPen, QIcon, QKeySequence, QPalette, QPixmap
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton,
    QTableView, QFileDialog, QMessageBox, QLabel, QCheckBox, QHeaderView,
    QStatusBar, QProgressBar, QFrame, QTabWidget, QTreeWidget, QTreeWidgetItem,
    QStackedWidget, QDialog, QFormLayout, QDialogButtonBox, QMenu, QAction,
    QTextEdit, QSpinBox, QComboBox, QSizePolicy, QSplitter, QShortcut,
    QSystemTrayIcon, QListWidget, QListWidgetItem, QStyledItemDelegate, QInputDialog,
    QScrollArea
)

# ---------------------------
# Configuración
# ---------------------------

from msys import config as msys_config
from msys.version import NOMBRE_VERSION
from msys.config import (
    logger, CHECKPOINT_FILE, CHECKPOINT_INTERVAL, DEFAULT_TIMEOUT,
    CONFIG_FILE, DEFAULT_TAB_CONFIG, CATEGORY_ORDER, CATEGORIES,
    SECTION_CATEGORY, load_tab_config, save_tab_config,
)
from msys.core.utils import (
    PERMISOS_ES, TIPOS_ES, traducir_permiso, traducir_tipo,
    limpiar_destino_credential, clasificar_tipo_destino,
    is_admin, get_downloads_folder, get_informes_folder,
    ruta_guardado_predeterminada, render_html_a_pdf, dialogo_elegir_carpeta,
    REG_HIVES, REG_TYPES, clasificar_riesgo_registro, format_reg_data,
    _existe_ejecutable,
    wrap_with_credentials, run_powershell_json, run_powershell_command,
    is_smbshare_available, escape_powershell_string,
    _csv_safe_cell, _csv_safe_row, _SafeCsvWriter,
    _is_eol_software, _get_software_blacklist, _is_blacklisted_software,
    _software_estado,
    checkpoint_exists, get_checkpoint_info, save_checkpoint,
    load_checkpoint, delete_checkpoint, startupinfo_oculto,
)

# Comandos PowerShell
from msys.ps_scripts import (
    PS_CHECK_SMBSHARE, PS_GET_SMB_SHARES, PS_GET_SMB_ACCESS, PS_GET_WMI_SHARES,
    PS_GET_ACL_UNC, PS_GET_NETWORK_DRIVES, PS_GET_NETWORK_DRIVES_WMI,
    PS_GET_NETWORK_DRIVES_ENHANCED, PS_GET_CREDENTIALS, PS_GET_WIFI_PASSWORDS,
    PS_GET_CERTIFICATES, PS_GET_ENVIRONMENT_VARS,
    PS_GET_NET_CONNECTIONS, PS_GET_NETWORK_ADAPTERS, PS_GET_INSTALLED_SOFTWARE,
    PS_GET_SCHEDULED_TASKS, PS_GET_PRINTERS, PS_GET_FIREWALL_RULES,
    PS_GET_EMAIL_ACCOUNTS, PS_GET_USB_DEVICES, PS_GET_MONITORS, PS_GET_SYSTEM_INFO,
    PS_GET_INSTALLED_UPDATES, PS_GET_REBOOT_PENDING, PS_GET_PENDING_UPDATES,
    PS_GET_DRIVERS, PS_GET_AD_INFO, PS_GET_USERS, PS_GET_ACTIVE_SESSIONS,
    PS_GET_BLUETOOTH, PS_GET_SYSTEM_EVENTS, PS_GET_SECURITY_INFO,
    PS_GET_DISK_HEALTH, PS_GET_BACKUP_STATUS, PS_GET_LICENSE_INFO,
    PS_GET_WINGET_UPGRADES, PS_GET_LOCAL_NETWORK_DEVICES, PS_GET_PS_HISTORY,
    PS_GET_REMOTE_CONNECTIONS,
)


# ---------------------------
# Modelo de datos
# ---------------------------

from msys.models.rows import (
    SharePermissionRow, NetworkDriveRow, CredentialRow, WiFiRow, CertificateRow,
    ProcessRow, PROCESS_STATUS_LABELS, PROCESS_PRIORITY_LABELS,
    _PROCESS_PRIORITY_MAP, _PROCESS_PRIORITY_CONST, _CRITICAL_PROCESSES,
    _PROCESS_WINDIR, _is_windows_process, STATUS_LABELS, STARTUP_LABELS,
    ServiceRow, NetworkConnectionRow, InstalledSoftwareRow, ScheduledTaskRow,
    PrinterRow, FirewallRuleRow, EmailAccountRow, ADInfoRow, USBDeviceRow,
    MonitorRow, InstalledUpdateRow, PendingUpdateRow, DriverRow,
    RegistryResultRow, UserRow, NetworkAdapterRow,
    ActiveSessionRow, BluetoothRow, PSHistoryRow, SystemEventRow,
    RemoteConnectionRow, PartitionRow, DiscoFisicoRow, GPURow,
    PackageUpgradeRow, NetworkDeviceRow,
)
from msys.domain.env_vars import (
    EnvVarRow, EnvironmentVarRow, _peor_severidad, _buscar_ci,
    _expandir_valor_env, _detectar_problemas_env_vars,
    _broadcast_environment_change, VarManager, EnvVarNotesDB, EnvVarLogDB,
    _PATH_LIKE_VARS,
)
from msys.domain.repair_audit import RepairAuditDB
from msys.models.table_models import (
    _FechaAwareSortProxy, CredentialFilterProxy, UserFilterProxy,
    SystemEventFilterProxy, ServiceFilterProxy, ProcessFilterProxy,
    EnvVarFilterProxy, PendingUpdateFilterProxy, DriverFilterProxy,
    RemoteConnectionFilterProxy, ContentFilterProxy, NetworkAdapterFilterProxy,
    NetworkDriveFilterProxy, CustomFilterProxy, ShareTableModel,
    NetworkDriveTableModel, CredentialTableModel, WiFiTableModel,
    WiFiFilterProxy, CertificateTableModel, EnvVarTableModel,
    ProcessTableModel, ServiceTableModel, NetworkConnectionTableModel,
    NetworkConnectionFilterProxy, InstalledSoftwareTableModel,
    ScheduledTaskTableModel, PrinterTableModel, FirewallRuleTableModel,
    FirewallFilterProxy, EmailAccountTableModel, ActiveSessionTableModel,
    BluetoothTableModel, PSHistoryTableModel, SystemEventTableModel,
    RemoteConnectionTableModel, ADInfoTableModel, USBDeviceTableModel,
    MonitorTableModel, InstalledUpdateTableModel, PendingUpdateTableModel,
    DriverTableModel, RegistryResultTableModel, RegistryResultProxy,
)

from msys.collectors import (
    RegistrySearchThread, RegistryOrphanScanner,
    ShareCollector, NetworkDriveCollector, CredentialCollector, WiFiCollector,
    CertificateCollector, EnvironmentVarCollector, ProcessManager, ProcessNotesDB,
    ProcessWhitelistDB, ProcessCollector, ServiceManager, ServiceNotesDB,
    ServiceWhitelistDB, ServiceCollector, FirewallRuleWhitelistDB, PortWhitelistDB,
    SuspiciousDeviceDB, BlankPasswordWhitelistDB, SuspiciousCertificateWhitelistDB,
    _HostnameResolverThread, NetworkConnectionCollector, InstalledSoftwareCollector,
    ScheduledTaskCollector, PrinterCollector, PrinterManager, FirewallRuleCollector,
    EmailAccountCollector, ADInfoCollector, USBDeviceCollector, MonitorCollector,
    SystemInfoCollector, WindowsUpdatesCollector, DriverCollector, DriverManager, UsersCollector,
    ScheduledTaskManager,
    RemoteConnectionCollector, ActiveSessionCollector, BluetoothCollector,
    PSHistoryCollector, SystemEventCollector,
    NetworkAdapterCollector, SecurityInfoCollector, DiskHealthCollector,
    LicenseInfoCollector, PackageUpgradeCollector,
    NetworkDeviceCollector, PackageBlockNotesDB, NavProfilesDB,
)
from msys.models.table_models import (
    UserTableModel, ContentTableModel, NetworkAdapterTableModel,
    DiscoFisicoTableModel, PartitionTableModel, GPUTableModel,
    PackageUpgradeTableModel, NetworkDeviceTableModel, NetworkDeviceFilterProxy,
    _ENV_SCOPE_LABELS,
)
from msys.win.structs import (
    _FILETIME, _filetime_to_int, get_cpu_times, get_cpu_percent,
    _MEMORYSTATUSEX, get_memory_status,
)
from msys.widgets.sparkline import SparklineWidget
from msys.widgets.gauge import CircularGaugeWidget
from msys.widgets.delegates import (
    MIN_COL_WIDTH, _AnchoVentanaFilter, _setup_header,
    _redistribuir_ancho_columnas, _get_readable_cell_delegate,
    LIGHT_STYLESHEET, DARK_STYLESHEET,
    NAV_TREE_STYLESHEET_LIGHT, NAV_TREE_STYLESHEET_DARK,
    ReadableCellTextDelegate,
)
from msys.ui.registro_windows import RegistroWindowsMixin
from msys.ui.environment_vars import VariablesEntornoMixin
from msys.ui.processes import ProcesosMixin
from msys.ui.services import ServiciosMixin
from msys.ui.credentials import CredencialesMixin
from msys.ui.wifi import WiFiMixin
from msys.ui.certificates import CertificadosMixin
from msys.ui.network_drives import UnidadesRemotasMixin
from msys.ui.drivers import ControladoresMixin
from msys.ui.network_connections import ConexionesRedMixin
from msys.ui.installed_software import SoftwareMixin
from msys.ui.scheduled_tasks import TareasProgramadasMixin
from msys.ui.printers import ImpresorasMixin
from msys.ui.firewall import FirewallMixin
from msys.ui.email_accounts import EmailMixin
from msys.ui.correo import CorreoMixin
from msys.ui.navegadores_nube import NavegadoresNubeMixin
from msys.ui.users import UsuariosMixin
from msys.ui.remote_connections import ConexionesRemotasMixin
from msys.ui.active_sessions import SesionesActivasMixin
from msys.ui.bluetooth import BluetoothMixin
from msys.ui.ps_history import HistorialPSMixin
from msys.ui.system_events import EventosSistemaMixin
from msys.ui.ad_info import ActiveDirectoryMixin
from msys.ui.startup_programs import ProgramasInicioMixin
from msys.ui.group_policy import GroupPolicyMixin
from msys.ui.usb_devices import USBMixin
from msys.ui.monitors import MonitoresMixin
from msys.ui.network_adapters import RedesMixin
from msys.ui.content import ContenidoMixin
from msys.ui.security import SeguridadTabMixin
from msys.ui.disk_health import SaludDiscoMixin
from msys.ui.backups import BackupsMixin
from msys.ui.licenses import LicenciasMixin
from msys.ui.package_manager import GestorPaquetesMixin
from msys.ui.local_network import RedLocalMixin
from msys.ui.sql_server import SqlServerMixin
from msys.ui.windows_updates import ActualizacionesMixin
from msys.ui.shares import CompartidasMixin
from msys.ui.permisos import PermisosMixin
from msys.ui.dashboard import DashboardMixin
from msys.ui.system_info import SistemaMixin
from msys.ui.reporting import ReportingMixin
from msys.ui.config_dialog import ConfiguracionMixin
from msys.ui.soporte import SoporteMixin
from msys.ui.iconos import IconosMixin
from msys.ui.asociaciones import AsociacionesMixin
from msys.ui.caracteristicas import CaracteristicasMixin
from msys.ui.herramientas_red import HerramientasRedMixin
from msys.ui.migracion import MigracionMixin
from msys.ui.movil import MovilMixin
from msys.ui.estabilidad import EstabilidadMixin
from msys.ui.visitas import VisitasMixin
from msys.ui.conocimiento_dialog import ConocimientoMixin
from msys.ui.acceso_remoto_dialog import AccesoRemotoMixin
from msys.ui.equipo_remoto import EquipoRemotoMixin
from msys.ui.herramientas_win import HerramientasWinMixin
from msys.ui.m365_dialog import M365Mixin


class ShareInspector(
    QWidget, RegistroWindowsMixin, VariablesEntornoMixin, ProcesosMixin,
    ServiciosMixin, CredencialesMixin, WiFiMixin, CertificadosMixin,
    UnidadesRemotasMixin, ControladoresMixin, ConexionesRedMixin,
    SoftwareMixin, TareasProgramadasMixin, ImpresorasMixin, FirewallMixin,
    EmailMixin, NavegadoresNubeMixin, UsuariosMixin, ConexionesRemotasMixin, SesionesActivasMixin,
    BluetoothMixin, HistorialPSMixin, EventosSistemaMixin, ActiveDirectoryMixin, GroupPolicyMixin,
    ProgramasInicioMixin,
    RedesMixin, ContenidoMixin, SeguridadTabMixin, SaludDiscoMixin,
    BackupsMixin, LicenciasMixin, GestorPaquetesMixin, RedLocalMixin, SqlServerMixin,
    ActualizacionesMixin,
    USBMixin, MonitoresMixin, CompartidasMixin, PermisosMixin, DashboardMixin, SistemaMixin,
    ReportingMixin, ConfiguracionMixin, SoporteMixin,
    IconosMixin, AsociacionesMixin, CaracteristicasMixin, HerramientasRedMixin, MigracionMixin, MovilMixin,
    EstabilidadMixin, VisitasMixin, ConocimientoMixin, AccesoRemotoMixin, CorreoMixin, EquipoRemotoMixin,
    HerramientasWinMixin, M365Mixin,
):
    def __init__(self):
        super().__init__()
        # Sin texto: la barra de título nativa solo deja minimizar/maximizar/
        # cerrar — el título grande y la frase viven en la cabecera propia
        # de la app (crear_header), junto al logo.
        self.setWindowTitle("")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        # Establecer tamaño mínimo amplio
        self.setMinimumSize(1600, 900)
        # Maximizar ventana al iniciar
        self.showMaximized()

        self.settings = QSettings("Masoft", "MASys")
        self.worker: Optional[QThread] = None
        self.service_manager = ServiceManager()
        self.service_notes_db = ServiceNotesDB()
        self.service_notes: Dict[str, str] = {}
        self.service_whitelist_db = ServiceWhitelistDB()
        self.driver_manager = DriverManager()
        self.printer_manager = PrinterManager()
        self.task_manager = ScheduledTaskManager()
        self.process_manager = ProcessManager()
        self.process_notes_db = ProcessNotesDB()
        self.process_notes: Dict[str, str] = {}
        self.process_whitelist_db = ProcessWhitelistDB()
        self.firewall_rule_whitelist_db = FirewallRuleWhitelistDB()
        self.port_whitelist_db = PortWhitelistDB()
        self.dispositivo_sospechoso_db = SuspiciousDeviceDB()
        self.blank_password_whitelist_db = BlankPasswordWhitelistDB()
        self.cert_sospechoso_whitelist_db = SuspiciousCertificateWhitelistDB()
        self.var_manager = VarManager()
        self.env_var_notes_db = EnvVarNotesDB()
        self.env_var_notes: Dict[str, str] = {}
        self.env_var_log_db = EnvVarLogDB()
        self._env_var_backup_hecho = False
        # Historial único de todas las acciones de reparación (cualquier
        # pestaña), independiente de las notas manuales por sección de
        # arriba — ver msys/domain/repair_audit.py.
        self.repair_audit_db = RepairAuditDB()
        self.package_block_notes_db = PackageBlockNotesDB()
        self.nav_profiles_db = NavProfilesDB()
        self.current_theme = self.settings.value("theme", "light")
        if self.current_theme not in ("light", "dark"):
            self.current_theme = "light"

        QApplication.setStyle("Fusion")
        self.aplicar_estilo_profesional()

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(15, 15, 15, 15)

        # Header compacto
        header = self.crear_header()
        layout.addWidget(header)

        # Menú lateral de navegación (sustituye al antiguo QTabWidget) +
        # panel de contenido apilado
        self.content_stack = QStackedWidget()
        self.nav_tree = QTreeWidget()
        self.nav_tree.itemClicked.connect(self._on_nav_item_clicked)
        self.nav_tree.itemExpanded.connect(self._on_nav_item_expanded)
        self.nav_tree.itemCollapsed.connect(self._on_nav_item_collapsed)

        # Diccionario para almacenar widgets de sección (para reconfiguración)
        self.tab_widgets = {}
        # Nombre de sección -> QTreeWidgetItem hoja correspondiente
        self.tree_items = {}
        # Orden de creación de las secciones (para construir el árbol)
        self._section_order = []
        # Nombre de la sección actualmente mostrada en content_stack
        self._current_section = None
        # True mientras se aplica un filtro de búsqueda (desactiva el
        # acordeón estricto para poder mostrar varias categorías a la vez)
        self._nav_filtering = False

        # Pestaña 1: Sistema
        # Pestaña 0: Dashboard
        self.tab_dashboard = self.crear_tab_dashboard()
        self._add_section(self.tab_dashboard, "📊 Dashboard")

        self.tab_system = self.crear_tab_system_info()
        self._add_section(self.tab_system, "💻 Sistema")

        self.tab_registro_windows = self.crear_tab_registro_windows()
        self._add_section(self.tab_registro_windows, "🗃️ Registro de Windows")

        # Iconos: bibliotecas, iconos de aplicaciones, reparación y configuración
        self.tab_iconos = self.crear_tab_iconos()
        self._add_section(self.tab_iconos, "🎨 Iconos")

        # Extensiones y programas predeterminados
        self.tab_asociaciones = self.crear_tab_asociaciones()
        self._add_section(self.tab_asociaciones, "🔗 Extensiones y programas")

        # Características opcionales y capacidades de Windows juntas
        self.tab_caracteristicas = self.crear_tab_caracteristicas()
        self._add_section(self.tab_caracteristicas, "🧩 Características de Windows")

        # Pantallazos azules, apagados inesperados y apps que fallan
        self.tab_estabilidad = self.crear_tab_estabilidad()
        self._add_section(self.tab_estabilidad, "🩺 Estabilidad")

        # Pestaña 2: Compartidas
        self.tab_shares = self.crear_tab_compartidas()
        self._add_section(self.tab_shares, "📂 Carpetas Compartidas")

        # Pestaña 2: Unidades Remotas
        self.tab_drives = self.crear_tab_unidades_remotas()
        self._add_section(self.tab_drives, "💾 Unidades Remotas")

        # Pestaña 3: Credenciales
        self.tab_creds = self.crear_tab_credenciales()
        self._add_section(self.tab_creds, "🔑 Credenciales")

        # Pestaña 4: WiFi
        self.tab_wifi = self.crear_tab_wifi()
        self._add_section(self.tab_wifi, "📶 WiFi")

        # Pestaña 5: Certificados
        self.tab_certs = self.crear_tab_certificados()
        self._add_section(self.tab_certs, "🔐 Certificados")

        # Pestaña 6: Variables de Entorno
        self.tab_env_vars = self.crear_tab_environment_vars()
        self._add_section(self.tab_env_vars, "🌐 Variables Entorno")

        # Pestaña 7: Procesos
        self.tab_processes = self.crear_tab_processes()
        self._add_section(self.tab_processes, "⚙️ Procesos")

        # Pestaña 7b: Programas de Inicio
        self.tab_programas_inicio = self.crear_tab_programas_inicio()
        self._add_section(self.tab_programas_inicio, "🚀 Programas de Inicio")

        # Pestaña 8: Servicios
        self.tab_services = self.crear_tab_services()
        self._add_section(self.tab_services, "🛠️ Servicios")

        # Pestaña 9: Conexiones de Red
        self.tab_connections = self.crear_tab_network_connections()
        self._add_section(self.tab_connections, "🌐 Conexiones Red")

        # Pestaña 10: Software Instalado
        self.tab_software = self.crear_tab_installed_software()
        self._add_section(self.tab_software, "📦 Software Instalado")

        # Pestaña 11: Tareas Programadas
        self.tab_tasks = self.crear_tab_scheduled_tasks()
        self._add_section(self.tab_tasks, "⏰ Tareas Programadas")

        # Pestaña 11b: Adm. Dispositivos (antes "Controladores" —
        # reposicionado junto al resto de hardware, en vez de Sistema).
        self.tab_drivers = self.crear_tab_drivers()
        self._add_section(self.tab_drivers, "🖥️ Adm. Dispositivos")

        # Pestaña 12: Impresoras
        self.tab_printers = self.crear_tab_printers()
        self._add_section(self.tab_printers, "🖨️ Impresoras")

        # Pestaña 13: Reglas Firewall
        self.tab_firewall = self.crear_tab_firewall_rules()
        self._add_section(self.tab_firewall, "🔥 Firewall")

        # Pestaña 14: Contraseñas Navegadores - REMOVED (can't show actual passwords)

        # Pestaña 15: Cuentas Correo
        self.tab_email = self.crear_tab_email_accounts()
        self._add_section(self.tab_email, "📧 Cuentas Correo")

        # Pestaña 15b: Navegadores y Nube (plantilla base, sin operatividad todavía)
        self.tab_navegadores_nube = self.crear_tab_navegadores_nube()
        self._add_section(self.tab_navegadores_nube, "☁️ Navegadores y Nube")

        # Pestaña 16: Usuarios
        self.tab_users = self.crear_tab_users()
        self._add_section(self.tab_users, "👥 Usuarios")

        # Pestaña 16b: Conexiones Remotas
        self.tab_remote_connections = self.crear_tab_conexiones_remotas()
        self._add_section(self.tab_remote_connections, "🔒 Conexiones Remotas")

        # Pestaña 17a: Sesiones Activas
        self.tab_sesiones_activas = self.crear_tab_sesiones_activas()
        self._add_section(self.tab_sesiones_activas, "👤 Sesiones Activas")

        # Pestaña 17b: Bluetooth
        self.tab_bluetooth = self.crear_tab_bluetooth()
        self._add_section(self.tab_bluetooth, "🔵 Bluetooth")

        # Pestaña 17c: Historial PowerShell
        self.tab_ps_history = self.crear_tab_ps_history()
        self._add_section(self.tab_ps_history, "📜 Historial PS")

        # Pestaña 17d: Eventos del Sistema
        self.tab_eventos_sistema = self.crear_tab_eventos_sistema()
        self._add_section(self.tab_eventos_sistema, "⚠️ Eventos Sistema")

        # Pestaña 18: Active Directory — SIEMPRE visible (antes solo si RSAT
        # estaba instalado; RSAT es una herramienta de administración aparte,
        # completamente independiente de estar unido a dominio/Entra ID, así
        # que ese candado ocultaba la pestaña incluso en equipos de dominio
        # reales sin RSAT instalado). Sin RSAT, PS_GET_AD_INFO ya devuelve un
        # aviso explicando cómo instalarlo — ver botón 📥 Instalar RSAT.
        self.tab_ad = self.crear_tab_ad_info()
        self._add_section(self.tab_ad, "🏢 Active Directory")

        # Pestaña 18b: Políticas de Grupo — igual que AD, siempre visible.
        # NO depende de RSAT (dsregcmd/gpresult son nativos de Windows); el
        # botón 📥 Instalar RSAT aquí solo mejora el detalle del lado de
        # dominio, nunca es un requisito para ver esta pestaña.
        self.tab_politicas_grupo = self.crear_tab_politicas_grupo()
        self._add_section(self.tab_politicas_grupo, "🏛️ Políticas de Grupo")

        # Pestaña 18: Dispositivos USB
        self.tab_usb = self.crear_tab_usb_devices()
        self._add_section(self.tab_usb, "🔌 Dispositivos USB")

        # Pestaña 19: Gráficas y Monitores
        self.tab_monitors = self.crear_tab_monitors()
        self._add_section(self.tab_monitors, "🖥️ Gráficas y Monitores")

        # Móviles Android (5.3.0): ya no es una sección del árbol sino la ventana del botón «📱 Móviles Android» de
        # abajo. Aquí solo se prepara su base y el servidor de «Recibir del móvil», que funciona con la ventana cerrada.
        self.iniciar_moviles()

        # Pestaña 20: Actualizaciones
        self.tab_updates = self.crear_tab_windows_updates()
        self._add_section(self.tab_updates, "🔄 Actualizaciones")

        # Pestaña 23: Contenido
        self.tab_content = self.crear_tab_contenido()
        self._add_section(self.tab_content, "📁 Contenido")

        # Pestaña 24: Redes
        self.tab_redes = self.crear_tab_redes()
        self._add_section(self.tab_redes, "🌐 Redes")

        # Seguridad (nuevo)
        self.tab_seguridad = self.crear_tab_seguridad()
        self._add_section(self.tab_seguridad, "🛡️ Seguridad")

        # Discos Duros (nuevo)
        self.tab_salud_disco = self.crear_tab_salud_disco()
        self._add_section(self.tab_salud_disco, "💽 Discos Duros")

        # Copias de Seguridad NO es una sección del árbol: es una operativa
        # de acción independiente (generar copias reales), sin datos que
        # consultar/exportar como el resto de secciones — se abre con el
        # botón dedicado bajo el árbol (ver más abajo, abrir_copias_seguridad).

        # Licencias (nuevo)
        self.tab_licencias = self.crear_tab_licencias()
        self._add_section(self.tab_licencias, "🔑 Licencias")

        # Gestor de Paquetes: winget/Chocolatey (nuevo)
        self.tab_paquetes = self.crear_tab_gestor_paquetes()
        self._add_section(self.tab_paquetes, "📦 Gestor de Paquetes")

        # Red Local: dispositivos conocidos vía tabla ARP (nuevo)
        self.tab_red_local = self.crear_tab_red_local()
        self._add_section(self.tab_red_local, "🔍 Red Local")

        # Herramientas de red: diagnóstico por capas (motor de MAOSI) y consultas
        self.tab_herramientas_red = self.crear_tab_herramientas_red()
        self._add_section(self.tab_herramientas_red, "🧪 Herramientas de red")

        # SQL Server: detección de instancias (motor + LocalDB) y control de
        # sus servicios — SIEMPRE visible, igual criterio que AD/Políticas de
        # Grupo (no gatear la pestaña por si el software está o no
        # instalado; sin instancias detectadas, la tabla simplemente
        # aparece vacía con la explicación correspondiente).
        self.tab_sql_server = self.crear_tab_sql_server()
        self._add_section(self.tab_sql_server, "🗄️ SQL Server")

        # Inventario de migración (equipo viejo -> nuevo), sucesor de MIGRACION.PS1
        self.tab_migracion = self.crear_tab_migracion()
        self._add_section(self.tab_migracion, "🧳 Inventario de migración")
        # 5.6.0: botones de las herramientas de diagnóstico de Windows en sus secciones (msys/ui/herramientas_win.py)
        self._hw_instalar_botones()

        # Construir el árbol de navegación (categorías + secciones) y montarlo
        # junto al panel de contenido en un splitter redimensionable
        self._build_nav_tree()

        nav_panel = QWidget()
        nav_panel_layout = QVBoxLayout(nav_panel)
        nav_panel_layout.setContentsMargins(0, 0, 0, 0)
        nav_panel_layout.setSpacing(6)

        self.nav_search = QLineEdit()
        self.nav_search.setPlaceholderText("🔍 Buscar sección...")
        self.nav_search.setClearButtonEnabled(True)
        self.nav_search.textChanged.connect(self._filter_nav)
        nav_panel_layout.addWidget(self.nav_search)

        # Nombre del perfil de navegación activo (⚙️ Configuración y
        # perfiles) — justo debajo de la búsqueda, se actualiza al
        # cargar/guardar/restablecer un perfil (ver ConfiguracionMixin).
        self.lbl_perfil_activo = QLabel(f"👤  {self._perfil_nav_activo()}")
        self.lbl_perfil_activo.setObjectName("perfilActivoLabel")
        self.lbl_perfil_activo.setAlignment(Qt.AlignCenter)
        nav_panel_layout.addWidget(self.lbl_perfil_activo)

        nav_panel_layout.addWidget(self.nav_tree)

        # "📚 Base de conocimiento" (4.5.0): problemas habituales y qué
        # herramienta de MASys usar — ventana propia NO modal, como las de
        # abajo, justo encima de Configuración (msys/ui/conocimiento_dialog.py).
        self.btn_conocimiento = QPushButton("📚 Base de conocimiento")
        self.btn_conocimiento.setObjectName("navBackupsButton")
        self.btn_conocimiento.setToolTip("Problemas habituales y qué herramientas de MASys usar")
        self.btn_conocimiento.clicked.connect(lambda _=False: self.abrir_base_conocimiento())
        nav_panel_layout.addWidget(self.btn_conocimiento)

        # "🖥️ Acceso remoto" (4.6.0): agenda de equipos de clientes y conexión
        # (RustDesk, Asistencia rápida, Escritorio remoto...) — ventana propia
        # NO modal (msys/ui/acceso_remoto_dialog.py).
        self.btn_acceso_remoto = QPushButton("🖥️ Acceso remoto")
        self.btn_acceso_remoto.setObjectName("navBackupsButton")
        self.btn_acceso_remoto.setToolTip("Conectar con los equipos de los clientes")
        self.btn_acceso_remoto.clicked.connect(lambda _=False: self.abrir_acceso_remoto())
        nav_panel_layout.addWidget(self.btn_acceso_remoto)

        # "📱 Móviles Android" (5.3.0): informes de MASysMov por Empresa › Usuario › Terminal y «🚪 Puertos desde
        # fuera» de todos los móviles — ventana propia NO modal (msys/ui/movil.py). Antes era una sección del árbol.
        self.btn_moviles = QPushButton("📱 Móviles Android")
        self.btn_moviles.setObjectName("navBackupsButton")
        self.btn_moviles.setToolTip("Móviles de los clientes con MASysMov: informes, copias y puertos desde fuera")
        self.btn_moviles.clicked.connect(lambda _=False: self.abrir_moviles())
        nav_panel_layout.addWidget(self.btn_moviles)

        # "☁️ Microsoft 365" ya no va aquí (5.7.2): está en Administración › 📜 Historial PS › 🚀 Lanzar Scripts
        # (msys/ui/script_launcher_dialog.py).

        # "⚙️ Configuración y perfiles" tampoco es una categoría del árbol:
        # es una operativa aparte (antes vivía como botón suelto en la
        # cabecera superior) — mismo sistema de botón que Copias de
        # Seguridad, justo encima de ella. Por ahora actúa igual que el
        # botón que sustituye (abrir_configuracion).
        self.btn_configuracion = QPushButton("⚙️ Configuración y perfiles")
        self.btn_configuracion.setObjectName("navBackupsButton")
        self.btn_configuracion.setToolTip("Configuración de MASys")
        self.btn_configuracion.clicked.connect(self.abrir_configuracion)
        nav_panel_layout.addWidget(self.btn_configuracion)

        # "💿 Copias de Seguridad" NO es una categoría del árbol: es una
        # operativa de acción independiente (generar copias reales), sin
        # nada que consultar/exportar como el resto de secciones — vive
        # como botón propio debajo del árbol, no como nodo seleccionable.
        self.btn_copias_seguridad = QPushButton("💿 Copias de Seguridad")
        self.btn_copias_seguridad.setObjectName("navBackupsButton")
        self.btn_copias_seguridad.setToolTip(
            "Generar copias de seguridad reales del equipo — ventana independiente, "
            "no es una sección de consulta")
        self.btn_copias_seguridad.clicked.connect(self.abrir_copias_seguridad)
        nav_panel_layout.addWidget(self.btn_copias_seguridad)

        # Suelo real (no orientativo): con stretchFactor(0, 0) este panel
        # nunca vuelve a crecer tras el primer layout, así que sin este
        # mínimo explícito Qt puede dejarlo anclado a un ancho ínfimo si
        # splitter.setSizes() se resuelve antes de que el splitter tenga
        # geometría real. setMinimumWidth es una garantía dura del propio
        # QSplitter, no depende de en qué momento se llame nada.
        nav_panel.setMinimumWidth(240)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(nav_panel)
        splitter.addWidget(self.content_stack)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setCollapsible(1, False)
        layout.addWidget(splitter, 1)
        splitter.setSizes([270, 1200])

        # Load and apply tab configuration from file
        self._load_and_apply_tab_config()

        # Seleccionar la primera sección visible por defecto
        if self._current_section is None and self.tree_items:
            primera = next(iter(self.tree_items))
            self.seleccionar_seccion(primera)

        # Barra de estado y progreso
        status_layout = QVBoxLayout()
        status_layout.setSpacing(5)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(True)
        status_layout.addWidget(self.progress_bar)

        # Botones de acción global y barra de estado
        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(10)

        self.status = QStatusBar()
        self.lbl_status = QLabel("💡 Listo. Selecciona una pestaña para empezar.")
        self.lbl_status.setFont(QFont("Segoe UI", 9))
        self.status.addWidget(self.lbl_status)
        bottom_layout.addWidget(self.status, 1)

        # Auto-refresco
        # Sólo visible en el Dashboard: dispara 🔄 Sincronizar Todo entera
        # (no una pestaña suelta), así que no tiene sentido dejarlo
        # disponible en el resto de secciones.
        self.lbl_auto = QLabel("🔁 Auto:")
        self.lbl_auto.setObjectName("mutedInfoLabel")
        bottom_layout.addWidget(self.lbl_auto)
        self.combo_autorefresh = QComboBox()
        self.combo_autorefresh.addItems(["Off", "30 s", "1 min", "2 min", "5 min"])
        self.combo_autorefresh.setFixedWidth(80)
        self.combo_autorefresh.setToolTip("Vuelve a ejecutar 🔄 Sincronizar Todo automáticamente a este intervalo")
        self.combo_autorefresh.currentIndexChanged.connect(self._on_autorefresh_changed)
        bottom_layout.addWidget(self.combo_autorefresh)
        self._autorefresh_timer = QTimer(self)
        self._autorefresh_timer.timeout.connect(self._autorefresh_tick)

        # Botón "Análisis de Seguridad" (sólo visible en el Dashboard)
        self.btn_seguridad = QPushButton("🛡️ Seguridad")
        self.btn_seguridad.setObjectName("ghost")
        self.btn_seguridad.setToolTip("Análisis de seguridad básico del sistema")
        self.btn_seguridad.clicked.connect(self.abrir_analisis_seguridad)
        self.btn_seguridad.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        bottom_layout.addWidget(self.btn_seguridad)

        # Botón "Soporte": punto de entrada único a las reparaciones de la
        # sección activa (clásicas de la categoría + detectadas sobre los
        # datos ya cargados). Solo visible en secciones con algo registrado
        # en _registro_soporte_por_seccion() — ver msys/ui/soporte.py.
        self.btn_soporte = QPushButton("🛠️ Soporte")
        self.btn_soporte.setObjectName("ghost")
        self.btn_soporte.setToolTip("Reparaciones disponibles para esta sección")
        self.btn_soporte.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        bottom_layout.addWidget(self.btn_soporte)

        # Barra adaptativa: en el Dashboard estos 4 botones exportan TODA la
        # app (como hasta ahora); en el resto de secciones, sólo la sección
        # activa. Ver _actualizar_barra_exportacion() / los 4 métodos
        # _xxx_adaptativo(), llamados desde seleccionar_seccion().
        self.btn_export_csv_actual = QPushButton("📄 CSV")
        self.btn_export_csv_actual.setObjectName("ghost")
        self.btn_export_csv_actual.clicked.connect(self._exportar_csv_adaptativo)
        self.btn_export_csv_actual.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        bottom_layout.addWidget(self.btn_export_csv_actual)

        self.btn_export_html_full = QPushButton("🌐 Informe HTML")
        self.btn_export_html_full.setObjectName("ghost")
        self.btn_export_html_full.setToolTip("Exportar todos los datos cargados a un informe HTML completo")
        self.btn_export_html_full.clicked.connect(self._exportar_html_adaptativo)
        self.btn_export_html_full.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        bottom_layout.addWidget(self.btn_export_html_full)

        self.btn_export_pdf_full = QPushButton("📕 PDF")
        self.btn_export_pdf_full.setObjectName("ghost")
        self.btn_export_pdf_full.setToolTip("Exportar todos los datos cargados a un informe PDF")
        self.btn_export_pdf_full.clicked.connect(self._exportar_pdf_adaptativo)
        self.btn_export_pdf_full.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        bottom_layout.addWidget(self.btn_export_pdf_full)

        # Botón "Snapshots" (sólo visible en el Dashboard)
        self.btn_snapshots = QPushButton("📸 Snapshots")
        self.btn_snapshots.setObjectName("ghost")
        self.btn_snapshots.setToolTip("Guardar y comparar estado del sistema")
        self.btn_snapshots.clicked.connect(self.abrir_snapshots)
        self.btn_snapshots.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        bottom_layout.addWidget(self.btn_snapshots)

        # Botón "Guardar Resultados"
        self.btn_guardar_resultados = QPushButton("💾 Guardar Resultados")
        self.btn_guardar_resultados.setObjectName("success")
        self.btn_guardar_resultados.setFixedWidth(200)
        self.btn_guardar_resultados.clicked.connect(self._guardar_resultados_adaptativo)
        bottom_layout.addWidget(self.btn_guardar_resultados)

        status_layout.addLayout(bottom_layout)

        layout.addLayout(status_layout)

        if not is_admin():
            self.set_status("⚠️ Ejecutando sin permisos de administrador. Algunos recursos pueden no ser visibles.")

        # Command palette (Ctrl+K): salta a cualquier sección sin usar el ratón
        self._shortcut_palette = QShortcut(QKeySequence("Ctrl+K"), self)
        self._shortcut_palette.activated.connect(self.abrir_command_palette)

        # Icono en bandeja del sistema (alertas + acceso rápido)
        self._setup_tray_icon()

        # Aplicar tema oscuro/claro a la barra de título nativa de Windows desde el arranque
        self._apply_titlebar_theme()
        # El icono pequeño de la barra de título se vacía en showEvent, NO
        # aquí: en __init__ la ventana nativa aún no está realmente creada/
        # mostrada, y Qt vuelve a poner el icono (el de setWindowIcon) en
        # cuanto show()/showMaximized() se ejecuta — vaciarlo antes de eso
        # no sobrevivía al show() real (comprobado).

        # Filtro global: aplica el tema de la barra de título a cualquier diálogo
        # o ventana secundaria del script en cuanto se muestra (ver eventFilter)
        QApplication.instance().installEventFilter(self)

        # Verificar checkpoint al iniciar (con delay para que la ventana se muestre primero)
        QTimer.singleShot(100, self.check_for_checkpoint)

        # Informes del agente por Internet (4.7.1): si la recepción estaba activa, se reactiva sola
        def _recepcion_agente():
            from msys.ui.recepcion_agente import arrancar_si_toca
            arrancar_si_toca(self)
        QTimer.singleShot(3000, _recepcion_agente)
        # Equipos nuevos en el servidor de RustDesk de este PC (5.0.0): aviso en la bandeja y ventanita
        QTimer.singleShot(5000, self.iniciar_aviso_equipos_nuevos)

    def showEvent(self, event):
        super().showEvent(event)
        # Debe ir en showEvent, no en __init__: la ventana nativa no existe
        # de verdad hasta el primer show() real, y Qt reaplica el icono de
        # setWindowIcon() en ese momento — vaciarlo antes no sobrevive.
        self._quitar_icono_barra_titulo()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._actualizar_visibilidad_alertas_dashboard()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            self._actualizar_visibilidad_alertas_dashboard()

    def aplicar_estilo_profesional(self):
        """Aplica el estilo profesional moderno en el tema (claro/oscuro)
        actualmente seleccionado en self.current_theme.

        Se aplica sobre QApplication (no sobre self): un stylesheet puesto
        sólo en la ventana principal NO se hereda en diálogos de nivel
        superior (QMessageBox, QFileDialog, QDialog...), porque en Qt el
        cascadeo de stylesheets no cruza fronteras de ventana. Al ponerlo en
        la QApplication, todos los diálogos —incluido el aviso de
        sobrescritura del selector de archivos— también respetan el tema."""
        hoja = DARK_STYLESHEET if self.current_theme == "dark" else LIGHT_STYLESHEET
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(hoja)
        else:
            self.setStyleSheet(hoja)

    def _dash_colors(self):
        """Paleta de colores del Dashboard según el tema activo. Todo lo que
        pinta texto/fondo específico del Dashboard (título, tiles de
        'Resumen por módulo', caja de alertas) debe leer de aquí en vez de
        usar colores fijos, para no quedar invisible al cambiar de tema."""
        if self.current_theme == "dark":
            return {
                'title': '#e8ecf1', 'muted': '#8a94a6',
                'tile_title': '#e8ecf1', 'tile_subtitle': '#aab4c4',
                'neutral_accent': '#93a5c2',
                'gauge_track': '#3a4258', 'gauge_text': '#e8ecf1',
                'alerts_bg': '#262837', 'alerts_border': '#3a3d4a',
                'alerts_item_border': '#333a4a', 'alerts_text': '#d3d9e3',
            }
        return {
            'title': '#1a2634', 'muted': '#8a94a6',
            'tile_title': '#1a2634', 'tile_subtitle': '#5d6b7a',
            'neutral_accent': '#455a64',
            'gauge_track': '#e9edf3', 'gauge_text': '#1a2634',
            'alerts_bg': '#f7f9fc', 'alerts_border': '#e3e7ee',
            'alerts_item_border': '#edf0f4', 'alerts_text': '#2c3e50',
        }

    def _apply_titlebar_theme_to(self, widget):
        """Aplica modo oscuro/claro a la barra de título nativa de Windows (DWM)
        de CUALQUIER ventana top-level (ventana principal o diálogo), no solo
        al contenido interno."""
        try:
            hwnd = ctypes.c_void_p(int(widget.winId()))
            dark = ctypes.c_int(1 if self.current_theme == "dark" else 0)
            # 20 = DWMWA_USE_IMMERSIVE_DARK_MODE (Windows 10 20H1+/11);
            # 19 = mismo atributo en builds anteriores de Windows 10.
            for attribute in (20, 19):
                res = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, ctypes.c_int(attribute), ctypes.byref(dark), ctypes.sizeof(dark))
                if res == 0:
                    break
        except Exception as e:
            logger.error(f"No se pudo aplicar el tema a la barra de título de una ventana: {e}")

    def _apply_titlebar_theme(self):
        """Aplica el tema a la barra de título de la ventana principal."""
        self._apply_titlebar_theme_to(self)

    def _quitar_icono_barra_titulo(self):
        """Vacía el icono pequeño (ICON_SMALL) que usa la barra de título
        nativa de Windows, dejándola solo con minimizar/maximizar/cerrar —
        sin afectar al icono de la barra de tareas ni al de la bandeja del
        sistema, que usan el icono grande/el de la clase de ventana, no
        este. Solo se aplica a la ventana principal."""
        try:
            hwnd = ctypes.c_void_p(int(self.winId()))
            WM_SETICON = 0x0080
            ICON_SMALL = 0
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, 0)
        except Exception as e:
            logger.error(f"No se pudo quitar el icono de la barra de título: {e}")

    def eventFilter(self, obj, event):
        """Aplica automáticamente el tema oscuro/claro a la barra de título
        nativa de CUALQUIER diálogo o ventana secundaria del script en cuanto
        se muestra, sin tener que instrumentar cada punto donde se crea un
        QDialog."""
        if event.type() == QEvent.Show and isinstance(obj, QWidget) and obj.isWindow() and obj is not self:
            self._apply_titlebar_theme_to(obj)
        return super().eventFilter(obj, event)

    def _restyle_dashboard_static_widgets(self):
        """Reaplica color a las etiquetas 'estáticas' del Dashboard que solo
        se crean una vez (no se reconstruyen en cada _refresh_dashboard), y
        que por tanto no cambian de color al alternar tema si no se hace
        explícitamente aquí."""
        c = self._dash_colors()
        if hasattr(self, '_lbl_dash_title'):
            self._lbl_dash_title.setStyleSheet(f"font-size: 19pt; font-weight: 700; color: {c['title']};")
        if hasattr(self, '_lbl_dash_updated'):
            self._lbl_dash_updated.setStyleSheet(f"color:{c['muted']}; font-size:9pt;")
        if hasattr(self, '_dashboard_alerts_list'):
            self._dashboard_alerts_list.setStyleSheet(f"""
                QListWidget {{
                    background:{c['alerts_bg']}; border:1px solid {c['alerts_border']}; border-radius:8px;
                    padding:2px; color:{c['alerts_text']};
                }}
                QListWidget::item {{
                    padding:7px 10px; border-bottom:1px solid {c['alerts_item_border']};
                }}
            """)
        if hasattr(self, 'lbl_auto'):
            self.lbl_auto.setStyleSheet(f"color: {c['muted']}; font-size: 9pt;")

    def _toggle_theme(self):
        """Alterna entre tema claro y oscuro, lo persiste y lo reaplica en
        caliente (sin reiniciar la aplicación)."""
        self.current_theme = "light" if self.current_theme == "dark" else "dark"
        self.settings.setValue("theme", self.current_theme)
        self.aplicar_estilo_profesional()
        self._apply_titlebar_theme()
        if hasattr(self, "nav_tree"):
            self._style_nav_tree()
        if hasattr(self, "btn_theme"):
            self.btn_theme.setText("☀️ Claro" if self.current_theme == "dark" else "🌙 Oscuro")
        self._restyle_dashboard_static_widgets()
        self._tema_base_conocimiento()
        self._tema_acceso_remoto()
        self._tema_moviles()
        # Las fichas de Sistema (y la evaluación de actualidad) se
        # construyen una vez por refresco de datos, con los colores del
        # tema activo EN ESE MOMENTO grabados en su stylesheet — sin esto,
        # tras cambiar de tema seguirían mostrando los colores del tema
        # anterior hasta el próximo "Actualizar" en esa pestaña.
        if hasattr(self, "_last_system_info") and self._last_system_info:
            self._construir_fichas_sistema(self._last_system_info)
            self._construir_evaluacion_actualidad(self._last_system_info)
        # Mismo motivo que las fichas de Sistema justo arriba: las fichas
        # de Seguridad (_construir_fichas_seguridad -> _crear_ficha_sistema)
        # graban el color de fondo del tema activo en su stylesheet al
        # crearse — sin reconstruirlas aquí, tras cambiar de tema seguirían
        # mostrando el fondo del tema anterior hasta el próximo "Actualizar".
        if hasattr(self, "_last_seguridad_info") and self._last_seguridad_info:
            self._construir_fichas_seguridad(self._last_seguridad_info)
        if hasattr(self, "_dash_modulos"):
            self._refresh_dashboard()

    # ──────────────────────────────────────────────────────────
    # MENÚ LATERAL DE NAVEGACIÓN (categorías colapsables)
    # ──────────────────────────────────────────────────────────
    def _add_section(self, widget, name):
        """Registra una sección de contenido (sustituye a QTabWidget.addTab)."""
        self.tab_widgets[name] = widget
        self.content_stack.addWidget(widget)
        self._section_order.append(name)





    def _style_nav_tree(self):
        """Aplica al árbol lateral la paleta del tema actual (claro: navy
        tradicional; oscuro: casi negro para no chocar con un content-pane
        oscuro), manteniendo en ambos casos el acento azul de selección."""
        self.nav_tree.setStyleSheet(
            NAV_TREE_STYLESHEET_DARK if self.current_theme == "dark" else NAV_TREE_STYLESHEET_LIGHT
        )
        self.nav_tree.setFont(QFont("Segoe UI", 10))

    def _build_nav_tree(self, name_list=None):
        """Reconstruye el árbol de navegación a partir de una lista ordenada
        de nombres de sección (por defecto, todas en orden de creación).
        Las categorías sin ninguna sección visible no se muestran."""
        if name_list is None:
            name_list = list(self._section_order)

        self.nav_tree.blockSignals(True)
        self.nav_tree.clear()
        self.tree_items = {}

        self.nav_tree.setHeaderHidden(True)
        self.nav_tree.setIndentation(14)
        self.nav_tree.setAnimated(True)
        self.nav_tree.setExpandsOnDoubleClick(False)
        self._style_nav_tree()

        # "Dashboard" es un elemento fijo, sin categoría, siempre arriba
        if "📊 Dashboard" in name_list and "📊 Dashboard" in self.tab_widgets:
            item = QTreeWidgetItem(self.nav_tree, ["📊 Dashboard"])
            item.setData(0, Qt.UserRole, "📊 Dashboard")
            self.tree_items["📊 Dashboard"] = item

        for cat_key in CATEGORY_ORDER:
            miembros = [n for n in name_list
                        if SECTION_CATEGORY.get(n) == cat_key and n in self.tab_widgets]
            if not miembros:
                continue
            label = CATEGORIES[cat_key]["label"]
            cat_item = QTreeWidgetItem(self.nav_tree, ["▸ " + label])
            font = cat_item.font(0)
            font.setBold(True)
            cat_item.setFont(0, font)
            cat_item.setData(0, Qt.UserRole, None)
            cat_item.setData(0, Qt.UserRole + 1, cat_key)
            for name in miembros:
                child = QTreeWidgetItem(cat_item, [name])
                child.setData(0, Qt.UserRole, name)
                self.tree_items[name] = child

        self.nav_tree.blockSignals(False)

    def _on_nav_item_clicked(self, item, column):
        name = item.data(0, Qt.UserRole)
        if name is None:
            # Nodo de categoría: alternar expandir/colapsar al pulsar en
            # cualquier parte de la fila, no solo en la flecha
            item.setExpanded(not item.isExpanded())
        else:
            self.seleccionar_seccion(name)

    def _on_nav_item_expanded(self, item):
        cat_key = item.data(0, Qt.UserRole + 1)
        if cat_key is None:
            return
        item.setText(0, "▾ " + CATEGORIES[cat_key]["label"])
        # Acordeón estricto: colapsar el resto de categorías (salvo mientras
        # se está filtrando por búsqueda, donde pueden convivir varias)
        if not self._nav_filtering:
            for i in range(self.nav_tree.topLevelItemCount()):
                sibling = self.nav_tree.topLevelItem(i)
                if (sibling is not item
                        and sibling.data(0, Qt.UserRole + 1) is not None
                        and sibling.isExpanded()):
                    sibling.setExpanded(False)

    def _on_nav_item_collapsed(self, item):
        cat_key = item.data(0, Qt.UserRole + 1)
        if cat_key is None:
            return
        item.setText(0, "▸ " + CATEGORIES[cat_key]["label"])

    def seleccionar_seccion(self, name):
        """Punto único para cambiar la sección visible del content_stack.
        Usado tanto por clics en el árbol como por los buscadores internos
        de la app (antes navegaban con self.tabs.setCurrentIndex)."""
        widget = self.tab_widgets.get(name)
        if widget is None:
            return
        # Modo equipo remoto (5.2.0): solo las secciones con datos del agente (las demás enseñarían ESTE PC)
        if self.en_modo_remoto() and name not in self.tree_items:
            self.set_status(f"🔒 Modo equipo remoto: «{name}» no viene en el informe del agente. "
                            "Vuelve a este equipo para verla.")
            return
        self.content_stack.setCurrentWidget(widget)
        self._current_section = name
        self._actualizar_barra_exportacion()

        item = self.tree_items.get(name)
        if item is not None:
            parent = item.parent()
            if parent is not None and not parent.isExpanded():
                parent.setExpanded(True)
            self.nav_tree.setCurrentItem(item)
            self.nav_tree.scrollToItem(item)

    def _filter_nav(self, texto):
        """Filtra en vivo las secciones del árbol según el texto del buscador."""
        texto = texto.strip().lower()
        filtrando = bool(texto)
        self._nav_filtering = filtrando

        for i in range(self.nav_tree.topLevelItemCount()):
            top = self.nav_tree.topLevelItem(i)
            cat_key = top.data(0, Qt.UserRole + 1)

            if cat_key is None:
                # Elemento fijo sin categoría (Dashboard)
                nombre = top.data(0, Qt.UserRole) or ""
                top.setHidden(filtrando and texto not in nombre.lower())
                continue

            algun_match = False
            for j in range(top.childCount()):
                child = top.child(j)
                match = (not filtrando) or (texto in child.data(0, Qt.UserRole).lower())
                child.setHidden(not match)
                algun_match = algun_match or match

            top.setHidden(not algun_match)
            if filtrando and algun_match:
                top.setExpanded(True)
            elif not filtrando:
                activa = (self._current_section is not None
                          and SECTION_CATEGORY.get(self._current_section) == cat_key)
                top.setExpanded(activa)

        self._nav_filtering = False

    def abrir_command_palette(self):
        """Command palette (Ctrl+K): busca y salta a cualquier sección sin
        tener que navegar el árbol lateral manualmente."""
        dlg = QDialog(self)
        dlg.setWindowTitle("Ir a sección")
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dlg.setMinimumWidth(420)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        buscador = QLineEdit()
        buscador.setPlaceholderText("🔍 Escribe el nombre de una sección...")
        layout.addWidget(buscador)

        lista = QListWidget()
        layout.addWidget(lista)

        nombres_disponibles = [n for n in self._section_order if n in self.tab_widgets]

        def _poblar(filtro=""):
            lista.clear()
            filtro = filtro.strip().lower()
            for nombre in nombres_disponibles:
                if not filtro or filtro in nombre.lower():
                    lista.addItem(QListWidgetItem(nombre))
            if lista.count() > 0:
                lista.setCurrentRow(0)

        def _ir_a_seleccion():
            item = lista.currentItem()
            if item:
                self.seleccionar_seccion(item.text())
                dlg.accept()

        buscador.textChanged.connect(_poblar)
        buscador.returnPressed.connect(_ir_a_seleccion)
        lista.itemDoubleClicked.connect(lambda _item: _ir_a_seleccion())

        _poblar()
        buscador.setFocus()
        dlg.exec_()

    def _make_app_icon_pixmap(self, size: int = 32) -> "QPixmap":
        """Genera el pixmap del logo (círculo navy + "M") DIBUJADO al
        tamaño pedido — nunca escalando un pixmap ya generado a otro
        tamaño: QIcon.pixmap(tamaño_mayor) sobre un QIcon de un único
        pixmap pequeño NO lo amplía de verdad (se queda con el tamaño
        original y el hueco sobrante en blanco alrededor, comprobado —
        así se veía "el mismo icono con más separación" al pedir 56px
        sobre un QIcon construido a partir de un pixmap de 32px).
        El dibujo vive en msys/ui/icono_app.py: el .ico del instalador sale
        del mismo sitio."""
        from msys.ui.icono_app import dibujar_icono_app
        return dibujar_icono_app(size)

    def _make_app_icon(self) -> QIcon:
        """Icono de 32px para bandeja del sistema/barra de tareas."""
        return QIcon(self._make_app_icon_pixmap(32))

    def _setup_tray_icon(self):
        """Crea el icono de bandeja del sistema con menú rápido y alertas."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self._tray_icon = None
            return

        icon = self._make_app_icon()
        self.setWindowIcon(icon)

        self._tray_icon = QSystemTrayIcon(icon, self)
        self._tray_icon.setToolTip(NOMBRE_VERSION)

        tray_menu = QMenu()
        act_mostrar = tray_menu.addAction("🔎 Mostrar MASys")
        act_mostrar.triggered.connect(self._restaurar_desde_bandeja)
        tray_menu.addSeparator()
        act_salir = tray_menu.addAction("❌ Salir")
        act_salir.triggered.connect(self.close)
        self._tray_icon.setContextMenu(tray_menu)

        def _on_activated(reason):
            if reason == QSystemTrayIcon.DoubleClick:
                self._restaurar_desde_bandeja()
        self._tray_icon.activated.connect(_on_activated)

        self._tray_icon.show()

    def _restaurar_desde_bandeja(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def tray_notify(self, titulo: str, mensaje: str, critico: bool = False):
        """Muestra una notificación de bandeja (usada tras detectar hallazgos
        críticos, p. ej. desde el Dashboard)."""
        if getattr(self, '_tray_icon', None) and self._tray_icon.isVisible():
            icon = QSystemTrayIcon.Critical if critico else QSystemTrayIcon.Information
            self._tray_icon.showMessage(titulo, mensaje, icon, 8000)

    def crear_header(self):
        """Crea el header compacto de la aplicación: título a la izquierda y
        acciones globales (Sincronizar, Búsqueda, Configuración, Tema)
        alineadas a la derecha, en la misma fila."""
        header_widget = QWidget()
        header_layout = QVBoxLayout(header_widget)
        header_layout.setContentsMargins(0, 0, 0, 5)
        header_layout.setSpacing(6)

        top_row = QHBoxLayout()
        top_row.setSpacing(2)

        title_box = QVBoxLayout()
        title_box.setSpacing(0)
        title = QLabel(NOMBRE_VERSION)
        title.setObjectName("headerTitle")
        title_box.addWidget(title)
        subtitle = QLabel("El cerebro detrás de tu sistema")
        subtitle.setObjectName("headerSubtitle")
        title_box.addWidget(subtitle)

        # Logo a la izquierda del título+frase, del alto EXACTO de las dos
        # líneas juntas, pegado al máximo (spacing mínimo de top_row). Se
        # suma el sizeHint() de cada QLabel por separado, no el del
        # QVBoxLayout contenedor: un QLayout aún no instalado en un widget
        # devuelve sizeHint() a (0, 0) — comprobado — mientras que sus
        # widgets hijos, aunque tampoco estén mostrados todavía, sí
        # calculan bien su tamaño en cuanto tienen el stylesheet real
        # aplicado. El pixmap se DIBUJA directamente a ese tamaño (ver
        # _make_app_icon_pixmap) — nunca escalar un QIcon/pixmap ya
        # generado a otro tamaño con .pixmap(w, h): no amplía de verdad.
        alto_logo = title.sizeHint().height() + subtitle.sizeHint().height() + title_box.spacing()
        lbl_logo = QLabel()
        lbl_logo.setPixmap(self._make_app_icon_pixmap(alto_logo))
        lbl_logo.setFixedSize(alto_logo, alto_logo)
        top_row.addWidget(lbl_logo)

        top_row.addLayout(title_box)

        top_row.addStretch()

        self.btn_buscar_todo = QPushButton("🔄 Sincronizar Todo")
        self.btn_buscar_todo.setObjectName("primary")
        self.btn_buscar_todo.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.btn_buscar_todo.clicked.connect(self.buscar_todo)
        top_row.addWidget(self.btn_buscar_todo)

        self.btn_busqueda_global = QPushButton("🔎 Búsqueda Global")
        self.btn_busqueda_global.setObjectName("ghost")
        self.btn_busqueda_global.setToolTip("Buscar un término en todos los datos cargados")
        self.btn_busqueda_global.clicked.connect(self.abrir_busqueda_global)
        self.btn_busqueda_global.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        top_row.addWidget(self.btn_busqueda_global)

        self.btn_theme = QPushButton("☀️ Claro" if self.current_theme == "dark" else "🌙 Oscuro")
        self.btn_theme.setObjectName("ghost")
        self.btn_theme.setFixedWidth(110)
        self.btn_theme.clicked.connect(self._toggle_theme)
        top_row.addWidget(self.btn_theme)

        header_layout.addLayout(top_row)

        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setObjectName("hSeparator")
        header_layout.addWidget(separator)

        return header_widget




























    # ═══════════════════════════════════════════════════════
    #  Registro de Windows (sección independiente, categoría Sistema)
    # ═══════════════════════════════════════════════════════














    # ──────────────────────────────────────────────────────────
    # SEGURIDAD (Defender, BitLocker, Secure Boot, TPM, puertos)
    # ──────────────────────────────────────────────────────────




    # ──────────────────────────────────────────────────────────
    # SALUD DE DISCO (S.M.A.R.T. / Get-PhysicalDisk)
    # ──────────────────────────────────────────────────────────
    def _crear_subtab_tabla(self, subtabs, titulo, placeholder_busqueda, model, headers, filename_prefix,
                             stretch_col=0, con_html=False):
        """Crea una sub-pestaña estándar (búsqueda + copiar + CSV[/HTML] +
        tabla) dentro de un QTabWidget, y devuelve (proxy, table_view)."""
        sub_tab = QWidget()
        sub_layout = QVBoxLayout(sub_tab)
        sub_layout.setSpacing(8)
        sub_layout.setContentsMargins(5, 5, 5, 5)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        search = QLineEdit()
        search.setPlaceholderText(placeholder_busqueda)
        actions.addWidget(search, 3)

        proxy = _FechaAwareSortProxy(self)
        proxy.setSourceModel(model)
        proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        proxy.setFilterKeyColumn(-1)
        search.textChanged.connect(lambda: proxy.setFilterRegExp(QRegExp(search.text(), Qt.CaseInsensitive, QRegExp.FixedString)))

        table = QTableView()
        table.setModel(proxy)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QTableView.SelectRows)
        table.verticalHeader().setVisible(False)
        table.setSortingEnabled(True)

        btn_copy = QPushButton("📋 Copiar")
        btn_copy.setObjectName("ghost")
        btn_copy.setFixedWidth(100)
        btn_copy.clicked.connect(lambda: self._copiar_seleccion_generica(table, proxy, headers))
        actions.addWidget(btn_copy)

        btn_csv = QPushButton("📄 CSV")
        btn_csv.setObjectName("ghost")
        btn_csv.setFixedWidth(80)
        btn_csv.clicked.connect(lambda: self._export_generic_csv(proxy, headers, filename_prefix))
        actions.addWidget(btn_csv)

        if con_html:
            btn_html = QPushButton("🌐 HTML")
            btn_html.setObjectName("ghost")
            btn_html.setFixedWidth(110)
            btn_html.clicked.connect(lambda: self._export_generic_html(proxy, headers, titulo, filename_prefix))
            actions.addWidget(btn_html)

        sub_layout.addLayout(actions)
        _setup_header(table, len(headers), stretch_col=stretch_col)
        sub_layout.addWidget(table)

        subtabs.addTab(sub_tab, titulo)
        return proxy, table






    # ──────────────────────────────────────────────────────────
    # COPIAS DE SEGURIDAD (puntos de restauración / VSS)
    # ──────────────────────────────────────────────────────────




    # ──────────────────────────────────────────────────────────
    # LICENCIAS (activación de Windows)
    # ──────────────────────────────────────────────────────────




    # ──────────────────────────────────────────────────────────
    # GESTOR DE PAQUETES (winget)
    # ──────────────────────────────────────────────────────────





    # ──────────────────────────────────────────────────────────
    # RED LOCAL (dispositivos conocidos vía tabla ARP/vecinos IPv4)
    # ──────────────────────────────────────────────────────────






    def set_status(self, text: str):
        """Actualiza el mensaje de estado."""
        self.lbl_status.setText(text)

    def check_for_checkpoint(self):
        """Verifica si existe checkpoint al iniciar la aplicación."""
        if not checkpoint_exists():
            return

        info = get_checkpoint_info()
        if not info:
            return

        # Formatear información del checkpoint
        timestamp = info.get('timestamp', 'desconocida')
        try:
            dt = datetime.fromisoformat(timestamp)
            timestamp_str = dt.strftime("%d/%m/%Y %H:%M:%S")
        except:
            timestamp_str = timestamp

        target = info.get('target', 'desconocida')
        processed = info.get('processed_count', 0)
        total = info.get('total_folders', 0)
        results = info.get('results_count', 0)

        # Crear mensaje para el diálogo
        msg = f"""Se detectó un análisis anterior sin finalizar:

📅 Fecha: {timestamp_str}
📁 Ruta: {target}
📊 Progreso: {processed} de {total} carpetas procesadas
📋 Resultados: {results} registros guardados

¿Desea continuar desde donde se quedó?

• SÍ: Reanudará el análisis automáticamente
• NO: Eliminará el checkpoint (podrá iniciar nuevo análisis)"""

        msgBox = QMessageBox(self)
        msgBox.setWindowFlags(msgBox.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        msgBox.setWindowTitle('🔄 Recuperar Análisis Anterior')
        msgBox.setText(msg)
        msgBox.setIcon(QMessageBox.Question)
        btn_si = msgBox.addButton('Sí', QMessageBox.YesRole)
        btn_no = msgBox.addButton('No', QMessageBox.NoRole)
        msgBox.setDefaultButton(btn_si)
        msgBox.exec_()

        if msgBox.clickedButton() == btn_si:
            # Cargar datos del checkpoint en la UI
            self.txt_path.setText(target)
            logger.info("Usuario eligió reanudar desde checkpoint")
            # Iniciar análisis automáticamente
            self.refresh('path', target, resume_checkpoint=True)
        else:
            delete_checkpoint()
            logger.info("Usuario eligió eliminar checkpoint")

    def analizar(self):
        """Analiza: si hay ruta específica la analiza, si está vacío hace análisis completo."""
        path = self.txt_path.text().strip()
        mode = 'path' if path else 'local'
        self.refresh(mode, path, resume_checkpoint=False)

    def browse_path(self):
        """Abre diálogo para seleccionar carpeta."""
        path = dialogo_elegir_carpeta(self, "Seleccionar carpeta", self.txt_path.text() or "C:\\")
        if path:
            self.txt_path.setText(path)





























    # ---------------------------
    # Métodos para WiFi
    # ---------------------------


    # ---------------------------
    # Métodos para Certificados
    # ---------------------------


    # Variables de Entorno

    # ── Diálogos sin botón de ayuda "?" ──────────────────────────────────
    # Convención ya establecida en el resto de MASys (17+ sitios usan
    # setWindowFlags(... & ~Qt.WindowContextHelpButtonHint) sobre un
    # QMessageBox/QDialog construido a mano en vez de los atajos estáticos
    # QMessageBox.question()/QInputDialog.getMultiLineText(), que en Windows
    # añaden un botón "?" a la izquierda de la X y no exponen forma de
    # quitarlo antes de mostrarse). Las acciones de Servicios y Procesos
    # usan estos tres helpers en vez de repetir el patrón en cada sitio.
    def _registrar_historial(self, seccion: str, accion: str, objetivo: str, exito: bool, detalle: str = "",
                             backup_ruta: str = "") -> None:
        """5.4.0: punto único para apuntar en el historial de reparaciones las acciones que cambian
        el sistema y que antes no se apuntaban (procesos, servicios, reglas de firewall, certificados,
        adaptadores, registro, unidades de red, cuentas). Un fallo del historial nunca corta la acción."""
        db = getattr(self, "repair_audit_db", None)
        if db is None:
            return
        try:
            db.registrar(seccion=seccion, accion=accion, objetivo=objetivo, exito=bool(exito),
                         detalle=(detalle or ("OK" if exito else "Sin detalles")),
                         backup_realizado=bool(backup_ruta), backup_ruta=backup_ruta or "")
        except Exception as e:
            logger.warning("Historial de reparaciones: %s", e)

    def _copia_previa(self, subcarpeta: str, nombre_archivo: str, plantilla_ps: str, **valores):
        """5.5.0: copia restaurable ANTES de una acción irreversible (eliminar tarea, regla de firewall,
        certificado). Ejecuta plantilla_ps con {ruta} = BACKUPS\\<subcarpeta>\\<fecha>_<nombre_archivo>.
        Devuelve (ok, ruta_o_error). Si la copia falla, quien llama pregunta si seguir."""
        import re as _re
        from msys import config as _cfg
        from msys.core.utils import run_powershell_command_params
        carpeta = os.path.join(_cfg.app_base_dir(), "BACKUPS", subcarpeta)
        try:
            os.makedirs(carpeta, exist_ok=True)
        except OSError as e:
            return False, str(e)
        limpio = _re.sub(r'[\\/:*?"<>|]+', "_", nombre_archivo).strip(" ._")[:90] or "copia"
        ruta = os.path.join(carpeta, f"{time.strftime('%Y%m%d_%H%M%S')}_{limpio}")
        ok, error = run_powershell_command_params(plantilla_ps, timeout=60, ruta=ruta, **valores)
        if ok and not os.path.exists(ruta):
            ok, error = False, "No se creó el archivo de copia"
        return (True, ruta) if ok else (False, error)

    def _seguir_sin_copia(self, que: str, error: str) -> bool:
        return self._confirmar_accion("Sin copia previa",
                                      f"No se pudo guardar la copia previa de {que}:\n\n{error}\n\n¿Eliminar igualmente?",
                                      QMessageBox.Warning)

    def _confirmar_accion(self, titulo: str, texto: str, icono=QMessageBox.Question) -> bool:
        msg_box = QMessageBox(self)
        msg_box.setWindowFlags(msg_box.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        msg_box.setWindowTitle(titulo)
        msg_box.setText(texto)
        msg_box.setIcon(icono)
        btn_si = msg_box.addButton("Sí", QMessageBox.YesRole)
        msg_box.addButton("No", QMessageBox.NoRole)
        msg_box.exec_()
        return msg_box.clickedButton() is btn_si

    def _avisar(self, titulo: str, texto: str, icono=QMessageBox.Information) -> None:
        msg_box = QMessageBox(self)
        msg_box.setWindowFlags(msg_box.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        msg_box.setWindowTitle(titulo)
        msg_box.setText(texto)
        msg_box.setIcon(icono)

        # Si este aviso viene de un detector de Soporte (self._aviso_soporte_actual,
        # fijado por _ejecutar_aviso_soporte mientras dura la llamada), el
        # botón "✅ Validar" va DENTRO de esta misma ventana — nunca como
        # opción aparte en el menú de Soporte. Cubre de un plumazo todos los
        # avisos que usan _avisar() sin tener que tocar cada uno.
        aviso_soporte = getattr(self, '_aviso_soporte_actual', None)
        btn_validar = None
        if aviso_soporte:
            btn_validar = msg_box.addButton("✅ Validar (dejar de mostrar)", QMessageBox.ActionRole)
            msg_box.addButton("Cerrar", QMessageBox.AcceptRole)

        msg_box.exec_()

        if aviso_soporte and msg_box.clickedButton() is btn_validar:
            clave, etiqueta, seccion = aviso_soporte
            self._validar_aviso_soporte(clave, etiqueta, seccion)

    def _mostrar_informe(self, titulo: str, resumen: List[str], items: List[str],
                          nota: str = "", es_alerta: bool = False) -> None:
        """Ventana informativa estandarizada (cabecera + tarjeta), misma
        familia visual que _confirmar_reparacion, para CUALQUIER resultado
        de análisis/comprobación con una lista de elementos (reglas,
        puertos, hallazgos...) — un QMessageBox de texto plano deja de ser
        legible en cuanto la lista pasa de unas pocas líneas. 'resumen' son
        líneas cortas bajo el título (estado global); 'items' es la lista
        con viñetas dentro de la tarjeta (con scroll si hay muchas); 'nota'
        es un pie de página opcional en gris. Solo botón "Cerrar": no pide
        confirmación de nada, solo muestra información."""
        c = self._dash_colors()
        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle(titulo)
        dialog.setMinimumWidth(640)
        dialog.setMaximumWidth(760)

        v = QVBoxLayout(dialog)
        v.setSpacing(14)
        v.setContentsMargins(22, 20, 22, 18)

        # ── Cabecera: icono + título + resumen (subtítulo) ──
        cabecera = QHBoxLayout()
        cabecera.setSpacing(12)
        lbl_icono = QLabel("🚨" if es_alerta else "ℹ️")
        lbl_icono.setStyleSheet("font-size: 26pt; background: transparent; border: none;")
        cabecera.addWidget(lbl_icono)

        col_titulo = QVBoxLayout()
        col_titulo.setSpacing(2)
        lbl_titulo = QLabel(titulo)
        lbl_titulo.setWordWrap(True)
        lbl_titulo.setStyleSheet(
            f"font-size: 13pt; font-weight: 600; color: {c['title']}; background: transparent; border: none;")
        col_titulo.addWidget(lbl_titulo)
        for linea in resumen:
            lbl_resumen = QLabel(linea)
            lbl_resumen.setWordWrap(True)
            lbl_resumen.setStyleSheet(
                f"color: {c['muted']}; font-size: 9.5pt; background: transparent; border: none;")
            col_titulo.addWidget(lbl_resumen)
        cabecera.addLayout(col_titulo, 1)
        v.addLayout(cabecera)

        # ── Tarjeta con lista, una viñeta real por fila (con scroll) ──
        if items:
            marco = QFrame()
            marco.setStyleSheet(
                f"QFrame {{ background: {c['alerts_bg']}; border: 1px solid {c['alerts_border']}; border-radius: 8px; }}")
            v_items = QVBoxLayout(marco)
            v_items.setContentsMargins(14, 10, 14, 10)
            v_items.setSpacing(6)
            for item in items:
                fila = QHBoxLayout()
                fila.setSpacing(8)
                lbl_bullet = QLabel("•")
                lbl_bullet.setAlignment(Qt.AlignTop)
                lbl_bullet.setStyleSheet(
                    f"color: {c['alerts_text']}; font-weight: 700; background: transparent; border: none;")
                fila.addWidget(lbl_bullet)
                lbl_texto = QLabel(item)
                lbl_texto.setWordWrap(True)
                lbl_texto.setStyleSheet(f"color: {c['alerts_text']}; background: transparent; border: none;")
                fila.addWidget(lbl_texto, 1)
                v_items.addLayout(fila)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setMaximumHeight(320)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
            scroll.setWidget(marco)
            v.addWidget(scroll)

        if nota:
            lbl_nota = QLabel(nota)
            lbl_nota.setWordWrap(True)
            lbl_nota.setStyleSheet(
                f"color: {c['muted']}; font-size: 9pt; background: transparent; border: none;")
            v.addWidget(lbl_nota)

        # ── Botones ── (el de "Validar" solo si este informe viene de un
        # aviso de Soporte — ver comentario de _avisar())
        fila_botones = QHBoxLayout()
        fila_botones.addStretch()
        aviso_soporte = getattr(self, '_aviso_soporte_actual', None)
        if aviso_soporte:
            clave, etiqueta, seccion = aviso_soporte
            btn_validar = QPushButton("✅ Validar (dejar de mostrar este aviso)")
            fila_botones.addWidget(btn_validar)

            def _click_validar():
                self._validar_aviso_soporte(clave, etiqueta, seccion)
                dialog.accept()
            btn_validar.clicked.connect(_click_validar)
        btn_cerrar = QPushButton("Cerrar")
        btn_cerrar.setObjectName("primary")
        fila_botones.addWidget(btn_cerrar)
        v.addLayout(fila_botones)
        btn_cerrar.clicked.connect(dialog.accept)

        dialog.exec_()

    def _confirmar_reparacion(self, titulo: str, cambios: List[str],
                               reversible: bool, hace_backup: bool = False,
                               objetivo: str = "", explicacion: str = "",
                               es_alerta: bool = False) -> bool:
        """Confirmación estandarizada para CUALQUIER acción sensible de
        mantenimiento (reparación, pero también preventiva como crear un
        punto de restauración) — a diferencia de _confirmar_accion, genérica
        para cualquier Sí/No, obliga a listar explícitamente qué va a pasar
        en vez de un "¿continuar?" vago, y deja claro si hay backup
        automático y si se puede deshacer. No asume ningún verbo ("reparar",
        "crear"...) — 'titulo' y 'cambios' son quienes lo dicen. Todas las
        acciones de este tipo deben pasar por aquí — no por _confirmar_accion
        — y registrar el resultado en RepairAuditDB (self.repair_audit_db)
        tanto si el usuario acepta como si la acción falla al ejecutarse.

        Ventana propia (no QMessageBox) porque un solo bloque de texto con
        objetivo+cambios+backup+reversibilidad resultaba difícil de leer de
        un vistazo: aquí cada cosa vive en su propia sección visual (tarjeta
        de cambios con una viñeta por fila, insignias de color para backup/
        reversibilidad) y la ventana es más ancha para no forzar el texto a
        cortarse en líneas estrechas.

        'explicacion' es un bloque de texto opcional APARTE de 'cambios',
        para contexto previo más largo (p. ej. un informe de errores de
        hardware, o la explicación de un riesgo de seguridad) — se muestra
        en su propia tarjeta, en rojo si 'es_alerta' es True."""
        c = self._dash_colors()
        dialog = QDialog(self)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dialog.setWindowTitle(titulo)
        dialog.setMinimumWidth(640)
        dialog.setMaximumWidth(760)

        v = QVBoxLayout(dialog)
        v.setSpacing(14)
        v.setContentsMargins(22, 20, 22, 18)

        # ── Cabecera: icono + título + objetivo (subtítulo) ──
        cabecera = QHBoxLayout()
        cabecera.setSpacing(12)
        lbl_icono = QLabel("🚨" if es_alerta else "🔧")
        lbl_icono.setStyleSheet("font-size: 26pt; background: transparent; border: none;")
        cabecera.addWidget(lbl_icono)

        col_titulo = QVBoxLayout()
        col_titulo.setSpacing(2)
        lbl_titulo = QLabel(titulo)
        lbl_titulo.setWordWrap(True)
        lbl_titulo.setStyleSheet(
            f"font-size: 13pt; font-weight: 600; color: {c['title']}; background: transparent; border: none;")
        col_titulo.addWidget(lbl_titulo)
        if objetivo:
            lbl_objetivo = QLabel(objetivo)
            lbl_objetivo.setWordWrap(True)
            lbl_objetivo.setStyleSheet(
                f"color: {c['muted']}; font-size: 9.5pt; background: transparent; border: none;")
            col_titulo.addWidget(lbl_objetivo)
        cabecera.addLayout(col_titulo, 1)
        v.addLayout(cabecera)

        # ── Explicación / alerta previa (opcional) ──
        if explicacion:
            if es_alerta:
                bg, borde, texto_color = ("#3a2427", "#6b3438", "#f3b4b4") if self.current_theme == "dark" \
                    else ("#fdecea", "#f1b0b7", "#7a1f1f")
            else:
                bg, borde, texto_color = c['alerts_bg'], c['alerts_border'], c['alerts_text']
            marco_explicacion = QFrame()
            marco_explicacion.setStyleSheet(
                f"QFrame {{ background: {bg}; border: 1px solid {borde}; border-radius: 8px; }}")
            v_exp = QVBoxLayout(marco_explicacion)
            v_exp.setContentsMargins(14, 10, 14, 10)
            lbl_exp = QLabel(explicacion)
            lbl_exp.setWordWrap(True)
            lbl_exp.setStyleSheet(f"color: {texto_color}; background: transparent; border: none;")
            v_exp.addWidget(lbl_exp)
            v.addWidget(marco_explicacion)

        # ── Cambios: una viñeta real por fila, no un bloque de texto ──
        lbl_cambios_hdr = QLabel("Esto es lo que va a pasar:")
        lbl_cambios_hdr.setStyleSheet(
            f"color: {c['muted']}; font-size: 9pt; font-weight: 600; background: transparent; border: none;")
        v.addWidget(lbl_cambios_hdr)

        marco_cambios = QFrame()
        marco_cambios.setStyleSheet(
            f"QFrame {{ background: {c['alerts_bg']}; border: 1px solid {c['alerts_border']}; border-radius: 8px; }}")
        v_cambios = QVBoxLayout(marco_cambios)
        v_cambios.setContentsMargins(14, 10, 14, 10)
        v_cambios.setSpacing(6)
        for cambio in cambios:
            fila = QHBoxLayout()
            fila.setSpacing(8)
            lbl_bullet = QLabel("•")
            lbl_bullet.setAlignment(Qt.AlignTop)
            lbl_bullet.setStyleSheet(
                f"color: {c['alerts_text']}; font-weight: 700; background: transparent; border: none;")
            fila.addWidget(lbl_bullet)
            lbl_texto = QLabel(cambio)
            lbl_texto.setWordWrap(True)
            lbl_texto.setStyleSheet(f"color: {c['alerts_text']}; background: transparent; border: none;")
            fila.addWidget(lbl_texto, 1)
            v_cambios.addLayout(fila)
        v.addWidget(marco_cambios)

        # ── Insignias de backup / reversibilidad (icono + color, no frases) ──
        color_ok, color_warn, color_danger = "#16a34a", "#ca8a04", "#dc2626"
        fila_insignias = QHBoxLayout()
        fila_insignias.setSpacing(20)
        lbl_backup = QLabel(
            "💾  Copia de seguridad automática" if hace_backup else "⚠️  Sin copia de seguridad automática")
        lbl_backup.setStyleSheet(
            f"color: {color_ok if hace_backup else color_warn}; font-weight: 600; font-size: 9.5pt; "
            "background: transparent; border: none;")
        fila_insignias.addWidget(lbl_backup)
        lbl_reversible = QLabel("↩️  Se puede deshacer" if reversible else "❌  NO se puede deshacer")
        lbl_reversible.setStyleSheet(
            f"color: {color_ok if reversible else color_danger}; font-weight: 600; font-size: 9.5pt; "
            "background: transparent; border: none;")
        fila_insignias.addWidget(lbl_reversible)
        fila_insignias.addStretch()
        v.addLayout(fila_insignias)

        # ── Botones ── (el de "Validar" solo si esta confirmación viene de
        # un aviso de Soporte — ver comentario de _avisar(); validar NO
        # aplica el cambio, equivale a Cancelar pero además silencia el aviso)
        fila_botones = QHBoxLayout()
        aviso_soporte = getattr(self, '_aviso_soporte_actual', None)
        validado_en_vez_de_cancelar = False
        if aviso_soporte:
            clave, etiqueta, seccion = aviso_soporte
            btn_validar = QPushButton("✅ Validar (dejar de mostrar, sin aplicar)")
            fila_botones.addWidget(btn_validar)

            def _click_validar():
                nonlocal validado_en_vez_de_cancelar
                validado_en_vez_de_cancelar = True
                dialog.reject()
            btn_validar.clicked.connect(_click_validar)
        fila_botones.addStretch()
        btn_cancelar = QPushButton("Cancelar")
        btn_continuar = QPushButton("Sí, continuar")
        btn_continuar.setObjectName("primary")
        fila_botones.addWidget(btn_cancelar)
        fila_botones.addWidget(btn_continuar)
        v.addLayout(fila_botones)
        btn_cancelar.clicked.connect(dialog.reject)
        btn_continuar.clicked.connect(dialog.accept)

        proceder = dialog.exec_() == QDialog.Accepted
        if validado_en_vez_de_cancelar:
            self._validar_aviso_soporte(clave, etiqueta, seccion)
        return proceder

    def _editar_texto_multilinea(self, titulo: str, etiqueta: str, texto_actual: str) -> Tuple[str, bool]:
        dlg = QInputDialog(self)
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        dlg.setWindowTitle(titulo)
        dlg.setLabelText(etiqueta)
        dlg.setOption(QInputDialog.UsePlainTextEditForTextInput, True)
        dlg.setTextValue(texto_actual)
        ok = dlg.exec_() == QDialog.Accepted
        return dlg.textValue(), ok

    # Procesos


    # Servicios

    # Conexiones de Red





    # Software Instalado




    # Tareas Programadas



    # Impresoras



    # Reglas de Firewall








    # REMOVED - Browser Passwords
    # def refresh_browser_passwords(self):
    #     """Actualiza lista de contraseñas de navegadores."""
    #     self.set_status("🔄 Buscando contraseñas de navegadores...")
    #     self.btn_refresh_browser_pwd.setEnabled(False)
    #     self.progress_bar.setVisible(True)
    #     self.progress_bar.setValue(0)
    #
    #     self.worker = BrowserPasswordCollector()
    #     self.worker.progress.connect(self._on_progress)
    #     self.worker.finished_ok.connect(self._on_browser_pwd_data)
    #     self.worker.finished_error.connect(self._on_browser_pwd_error)
    #     self.worker.start()
    #
    # def _on_browser_pwd_data(self, rows: List[BrowserPasswordRow]):
    #     """Procesa datos de contraseñas de navegadores."""
    #     self.browser_pwd_model.setRows(rows)
    #     self.btn_refresh_browser_pwd.setEnabled(True)
    #     self.progress_bar.setVisible(False)
    #     total = len(rows)
    #     self.set_status(f"✅ {total} entradas encontradas")
    #
    # def _on_browser_pwd_error(self, msg: str):
    #     """Maneja errores de contraseñas de navegadores."""
    #     self.btn_refresh_browser_pwd.setEnabled(True)
    #     self.progress_bar.setVisible(False)
    #     self.set_status("❌ Error al buscar contraseñas de navegadores")
    #     QMessageBox.warning(self, "Error", f"No se pudieron obtener las contraseñas:\n\n{msg}")

    # Cuentas de Correo



    # Usuarios




    # ──────────────────────────────────────────────────────────
    # AUTO-REFRESCO
    # ──────────────────────────────────────────────────────────
    _AUTOREFRESH_INTERVALS = {1: 30000, 2: 60000, 3: 120000, 4: 300000}

    # Mapa pestaña → método refresh
    _TAB_REFRESH_MAP = {
        "⚙️ Procesos":           "refresh_processes",
        "🛠️ Servicios":          "refresh_services",
        "🌐 Conexiones Red":     "refresh_network_connections",
        "🔒 Conexiones Remotas": "refresh_remote_connections",
        "👤 Sesiones Activas":   "refresh_sesiones_activas",
        "🔵 Bluetooth":          "refresh_bluetooth",
        "📜 Historial PS":       "refresh_ps_history",
        "⚠️ Eventos Sistema":    "refresh_eventos_sistema",
        "💾 Unidades Remotas":   "refresh_network_drives",
        "📂 Carpetas Compartidas":"buscar_compartidas",
        "🔑 Credenciales":       "refresh_credentials",
        "📶 WiFi":               "refresh_wifi",
        "🔐 Certificados":       "refresh_certificates",
        "🌐 Variables Entorno":  "refresh_environment_vars",
        "📦 Software Instalado": "refresh_installed_software",
        "⏰ Tareas Programadas": "refresh_scheduled_tasks",
        "🖨️ Impresoras":         "refresh_printers",
        "🔥 Firewall":           "refresh_firewall_rules",
        "👥 Usuarios":           "refresh_users",
        "🔌 Dispositivos USB":   "refresh_usb_devices",
        "🖥️ Gráficas y Monitores": "refresh_monitors",
        "🔄 Actualizaciones":    "refresh_windows_updates",
        "🖥️ Adm. Dispositivos":  "refresh_drivers",
        "📁 Contenido":          "refresh_content",
        "🌐 Redes":              "refresh_network_adapters",
    }

    def _on_autorefresh_changed(self, index):
        self._autorefresh_timer.stop()
        if index in self._AUTOREFRESH_INTERVALS:
            self._autorefresh_timer.start(self._AUTOREFRESH_INTERVALS[index])
            self.set_status(f"🔁 Auto-sincronizar todo: {self.combo_autorefresh.currentText()}")
        else:
            self.set_status("🔁 Auto-sincronizar todo: desactivado")

    def _autorefresh_tick(self):
        """Sólo se puede activar desde el Dashboard (ver
        _actualizar_barra_exportacion), así que dispara una sincronización
        completa (🔄 Sincronizar Todo), no la de una pestaña suelta."""
        if self.worker is not None and self.worker.isRunning():
            return
        self.buscar_todo()

    # ──────────────────────────────────────────────────────────
    # CONTEXT MENU — PROCESOS
    # ──────────────────────────────────────────────────────────

    # ──────────────────────────────────────────────────────────
    # CONTEXT MENU — SERVICIOS
    # ──────────────────────────────────────────────────────────

    # ──────────────────────────────────────────────────────────
    # CONTEXT MENU — CONEXIONES DE RED
    # ──────────────────────────────────────────────────────────


    # ──────────────────────────────────────────────────────────
    # CONTEXT MENU — USUARIOS
    # ──────────────────────────────────────────────────────────


    # ──────────────────────────────────────────────────────────
    # CONTEXT MENU — SOFTWARE INSTALADO
    # ──────────────────────────────────────────────────────────


    # ── Instalar actualización pendiente ───────────────────────

    # ──────────────────────────────────────────────────────────
    # HISTORIAL DE UNIDADES REMOTAS (Registro de Windows)
    # ──────────────────────────────────────────────────────────

    def _get_startupinfo(self):
        """Devuelve STARTUPINFO para ocultar ventanas de consola."""
        return startupinfo_oculto()

    # Conexiones Remotas





    # Sesiones Activas




    # Bluetooth




    # Historial PowerShell




    # Eventos del Sistema







    # Active Directory






















    # Contenido







    # Redes / Adaptadores de Red


















    # REMOVED - Browser Passwords
    # def copy_browser_pwd_selection(self):
    #     """Copia contraseñas de navegadores seleccionadas al portapapeles."""
    #     selected_indexes = self.browser_pwd_table.selectionModel().selectedRows()
    #     if not selected_indexes:
    #         QMessageBox.information(self, "Copiar", "No hay entradas seleccionadas.")
    #         return
    #
    #     lines = []
    #     lines.append("=" * 80)
    #     lines.append("CONTRASEÑAS DE NAVEGADORES")
    #     lines.append("=" * 80)
    #     lines.append("")
    #
    #     for index in selected_indexes:
    #         row = index.row()
    #         navegador = str(self.browser_pwd_proxy.data(self.browser_pwd_proxy.index(row, 0), Qt.DisplayRole) or "")
    #         url = str(self.browser_pwd_proxy.data(self.browser_pwd_proxy.index(row, 1), Qt.DisplayRole) or "")
    #         usuario = str(self.browser_pwd_proxy.data(self.browser_pwd_proxy.index(row, 2), Qt.DisplayRole) or "")
    #         nota = str(self.browser_pwd_proxy.data(self.browser_pwd_proxy.index(row, 4), Qt.DisplayRole) or "")
    #
    #         lines.append(f"{navegador}: {url}")
    #         lines.append(f"  Usuario: {usuario}")
    #         lines.append(f"  Nota: {nota}")
    #         lines.append("")
    #
    #     lines.append("=" * 80)
    #
    #     text = "\n".join(lines)
    #     QApplication.clipboard().setText(text)
    #     self.set_status(f"✅ Copiadas {len(selected_indexes)} entradas al portapapeles")






    # ── Copy/Export: Sesiones Activas ──────────────────────────────────────────


    # ── Copy/Export: Bluetooth ──────────────────────────────────────────────────


    # ── Copy/Export: Historial PowerShell ─────────────────────────────────────





    # ========== EXPORT CSV METHODS ==========





    # REMOVED - Browser Passwords
    # def export_browser_pwd_csv(self):
    #     """Exporta contraseñas de navegadores a CSV."""
    #     if self.browser_pwd_proxy.rowCount() == 0:
    #         QMessageBox.information(self, "Exportar CSV", "No hay entradas para exportar.")
    #         return
    #
    #     default_name = f"ContrasenasNavegadores_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    #     path, _ = QFileDialog.getSaveFileName(self, "Guardar CSV", ruta_guardado_predeterminada(default_name), "CSV (*.csv)", options=QFileDialog.DontUseNativeDialog)
    #     if not path:
    #         return
    #
    #     try:
    #         headers = BrowserPasswordTableModel.HEADERS
    #         with open(path, "w", newline="", encoding="utf-8-sig") as f:
    #             writer = _SafeCsvWriter(csv.writer(f, delimiter=";"))
    #             writer.writerow(headers)
    #             for r in range(self.browser_pwd_proxy.rowCount()):
    #                 row_data = []
    #                 for c in range(len(headers)):
    #                     idx = self.browser_pwd_proxy.index(r, c)
    #                     val = self.browser_pwd_proxy.data(idx, Qt.DisplayRole)
    #                     row_data.append(str(val) if val else "")
    #                 writer.writerow(row_data)
    #
    #         total_exported = self.browser_pwd_proxy.rowCount()
    #         self.set_status(f"✅ Exportado: {os.path.basename(path)} ({total_exported} entradas)")
    #         QMessageBox.information(self, "Éxito", f"Exportadas {total_exported} entradas a:\n\n{path}")
    #     except Exception as e:
    #         QMessageBox.critical(self, "Error", f"No se pudo exportar:\n\n{e}")



    # ========== EXPORT HTML METHODS ==========






    # REMOVED - Browser Passwords
    # def export_browser_pwd_html(self):
    #     """Exporta contraseñas de navegadores a HTML."""
    #     if self.browser_pwd_proxy.rowCount() == 0:
    #         QMessageBox.information(self, "Exportar HTML", "No hay entradas para exportar.")
    #         return
    #
    #     default_name = f"ContrasenasNavegadores_{time.strftime('%Y%m%d_%H%M%S')}.html"
    #     path, _ = QFileDialog.getSaveFileName(self, "Guardar HTML", ruta_guardado_predeterminada(default_name), "HTML (*.html)", options=QFileDialog.DontUseNativeDialog)
    #     if not path:
    #         return
    #
    #     try:
    #         headers = BrowserPasswordTableModel.HEADERS
    #         total_rows = self.browser_pwd_proxy.rowCount()
    #
    #         rows_data = []
    #         for r in range(total_rows):
    #             row_data = []
    #             for c in range(len(headers)):
    #                 idx = self.browser_pwd_proxy.index(r, c)
    #                 val = self.browser_pwd_proxy.data(idx, Qt.DisplayRole)
    #                 row_data.append(str(val) if val else "")
    #             rows_data.append(row_data)
    #
    #         html = f"""<!DOCTYPE html>
    # <html lang="es">
    # <head>
    #     <meta charset="UTF-8">
    #     <title>Contraseñas de Navegadores - {time.strftime('%Y-%m-%d %H:%M:%S')}</title>
    #     <style>
    #         * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    #         body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 20px; }}
    #         .container {{ max-width: 100%; background: white; border-radius: 10px; box-shadow: 0 10px 40px rgba(0,0,0,0.3); overflow: hidden; }}
    #         header {{ background: linear-gradient(135deg, #2c3e50 0%, #34495e 100%); color: white; padding: 30px; }}
    #         h1 {{ font-size: 28px; margin-bottom: 10px; }}
    #         .subtitle {{ font-size: 14px; opacity: 0.9; }}
    #         table {{ width: 100%; border-collapse: collapse; }}
    #         thead {{ background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; }}
    #         th {{ padding: 15px 10px; text-align: left; font-weight: 600; font-size: 13px; }}
    #         tbody tr {{ border-bottom: 1px solid #e9ecef; transition: background 0.2s; }}
    #         tbody tr:hover {{ background: #f8f9fa; }}
    #         td {{ padding: 12px 10px; font-size: 13px; color: #2c3e50; }}
    #     </style>
    # </head>
    # <body>
    #     <div class="container">
    #         <header>
    #             <h1>Contraseñas de Navegadores</h1>
    #             <div class="subtitle">Generado el {time.strftime('%d/%m/%Y a las %H:%M:%S')}</div>
    #         </header>
    #         <table>
    #             <thead>
    #                 <tr>
    # """
    #         for h in headers:
    #             html += f"                    <th>{h}</th>\n"
    #         html += """                </tr>
    #             </thead>
    #             <tbody>
    # """
    #         for row in rows_data:
    #             html += "                <tr>\n"
    #             for cell in row:
    #                 html += f"                    <td>{cell}</td>\n"
    #             html += "                </tr>\n"
    #
    #         html += """            </tbody>
    #         </table>
    #     </div>
    # </body>
    # </html>
    # """
    #
    #         with open(path, "w", encoding="utf-8") as f:
    #             f.write(html)
    #
    #         self.set_status(f"✅ Exportado: {os.path.basename(path)} ({total_rows} entradas)")
    #         QMessageBox.information(self, "Éxito", f"Exportadas {total_rows} entradas a:\n\n{path}")
    #     except Exception as e:
    #         QMessageBox.critical(self, "Error", f"No se pudo exportar:\n\n{e}")



    def _on_error(self, msg: str):
        """Maneja errores generales."""
        # Rehabilitar controles específicos
        self.btn_analyze.setEnabled(True)
        self.btn_runas_shares.setEnabled(True)
        self.btn_browse.setEnabled(True)
        self.txt_path.setEnabled(True)
        self.chk_hide_admin.setEnabled(True)
        self.chk_include_hidden.setEnabled(True)
        self.chk_only_users.setEnabled(True)
        self.spin_depth.setEnabled(True)
        self.btn_stop.setEnabled(False)  # Deshabilitar botón Parar
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error en la recopilación")
        self._avisar("Error", f"No se pudo obtener la información:\n\n{msg}", QMessageBox.Critical)

    def parar_analisis(self):
        """Detiene el análisis en curso y pregunta si mostrar resultados parciales."""
        if not self.worker or not self.worker.isRunning():
            return

        # Solicitar parada inmediatamente
        self.worker.request_stop()
        self.set_status("⏹️ Deteniendo análisis...")

        # Crear diálogo personalizado
        msgBox = QMessageBox(self)
        msgBox.setWindowFlags(msgBox.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        msgBox.setWindowTitle('⏹️ Detener Análisis')
        msgBox.setText("El análisis se está deteniendo.\n\n¿Desea ver los resultados parciales?")
        msgBox.setIcon(QMessageBox.Question)

        btn_si = msgBox.addButton('Sí', QMessageBox.YesRole)
        btn_no = msgBox.addButton('No', QMessageBox.NoRole)
        msgBox.setDefaultButton(btn_si)

        # Esperar a que el worker termine (máximo 10 segundos) SIN bloquear la
        # interfaz — self.worker.wait() a secas congela toda la app (sin
        # repintado ni respuesta a clics) hasta 10s, ya que se ejecuta en el
        # hilo de la UI. Se sondea en pequeños intervalos procesando eventos
        # de Qt entre medias para mantener la app responsiva mientras espera.
        deadline = time.time() + 10
        while self.worker.isRunning() and time.time() < deadline:
            QApplication.processEvents()
            self.worker.wait(50)

        msgBox.exec_()

        if msgBox.clickedButton() == btn_si:
            # Los resultados parciales ya están disponibles, solo actualizamos el estado
            self.set_status("⚠️ Análisis detenido - Mostrando resultados parciales")
            # La señal finished_ok ya habrá emitido los datos
        else:
            # Limpiar sin mostrar resultados
            self.btn_analyze.setEnabled(True)
            self.btn_runas_shares.setEnabled(True)
            self.btn_browse.setEnabled(True)
            self.txt_path.setEnabled(True)
            self.chk_hide_admin.setEnabled(True)
            self.chk_include_hidden.setEnabled(True)
            self.chk_only_users.setEnabled(True)
            self.spin_depth.setEnabled(True)
            self.btn_stop.setEnabled(False)
            self.progress_bar.setVisible(False)
            self.set_status("❌ Análisis cancelado")

    def ejecutar_como_otro_usuario(self):
        """Solicita credenciales alternativas sin cerrar la aplicación."""
        # ALTERNATE_CREDENTIALS vive en msys.config; se lee/escribe vía
        # msys_config (no "global") porque msys.core.utils.wrap_with_credentials
        # lo consulta desde ese mismo módulo compartido.

        # Crear diálogo personalizado para solicitar credenciales
        dialog = QDialog(self)
        dialog.setWindowTitle("Credenciales Alternativas")
        dialog.setMinimumWidth(450)
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)

        layout = QVBoxLayout(dialog)

        # Información
        info_label = QLabel(
            "Introduce las credenciales con las que deseas ejecutar los análisis.\n"
            "Los comandos se ejecutarán con estas credenciales sin cerrar la aplicación.\n\n"
            "Formato de usuario: DOMINIO\\Usuario o Usuario@dominio.com"
        )
        info_label.setWordWrap(True)
        info_label.setObjectName("sectionInfoLabel")
        layout.addWidget(info_label)

        # Formulario
        form_layout = QFormLayout()

        username_input = QLineEdit()
        username_input.setPlaceholderText("DOMINIO\\Usuario o usuario@dominio.com")
        if msys_config.ALTERNATE_CREDENTIALS:
            username_input.setText(msys_config.ALTERNATE_CREDENTIALS[0])
        form_layout.addRow("👤 Usuario:", username_input)

        password_input = QLineEdit()
        password_input.setEchoMode(QLineEdit.Password)
        password_input.setPlaceholderText("Contraseña")
        form_layout.addRow("🔑 Contraseña:", password_input)

        layout.addLayout(form_layout)

        # Estado actual
        status_label = QLabel()
        if msys_config.ALTERNATE_CREDENTIALS:
            status_label.setText(f"✅ Credenciales activas: {msys_config.ALTERNATE_CREDENTIALS[0]}")
            status_label.setStyleSheet("color: #27ae60; font-weight: bold; margin-top: 10px;")
        else:
            status_label.setText("ℹ️ Usando credenciales de la sesión actual")
            status_label.setStyleSheet(
                f"color: {'#9aa0ac' if self.current_theme == 'dark' else '#7f8c8d'}; "
                "font-style: italic; margin-top: 10px;"
            )
        layout.addWidget(status_label)

        # Botones
        button_box = QDialogButtonBox()

        btn_apply = button_box.addButton("✅ Aplicar Credenciales", QDialogButtonBox.AcceptRole)
        btn_clear = button_box.addButton("🔄 Restaurar Sesión Actual", QDialogButtonBox.ResetRole)
        btn_cancel = button_box.addButton("❌ Cancelar", QDialogButtonBox.RejectRole)

        layout.addWidget(button_box)

        # Conectar señales
        def apply_credentials():
            username = username_input.text().strip()
            password = password_input.text()

            if not username or not password:
                QMessageBox.warning(dialog, "Datos incompletos",
                                  "Debes introducir tanto usuario como contraseña.")
                return

            msys_config.ALTERNATE_CREDENTIALS = (username, password)

            self._avisar("Credenciales Aplicadas", f"Los próximos análisis se ejecutarán con las credenciales de:\n\n{username}\n\n"
                "Las credenciales anteriores se restaurarán al cerrar la aplicación.")

            dialog.accept()

        def clear_credentials():
            msys_config.ALTERNATE_CREDENTIALS = None

            self._avisar("Credenciales Restauradas", "Se volverán a usar las credenciales de tu sesión actual.")

            dialog.accept()

        btn_apply.clicked.connect(apply_credentials)
        btn_clear.clicked.connect(clear_credentials)
        btn_cancel.clicked.connect(dialog.reject)

        dialog.exec_()




    def closeEvent(self, event):
        """Limpia recursos al cerrar."""
        if hasattr(self, "_autorefresh_timer"):
            self._autorefresh_timer.stop()

        if hasattr(self, "_rendimiento_timer"):
            self._rendimiento_timer.stop()

        if getattr(self, "_tray_icon", None):
            self._tray_icon.hide()

        if self.worker and self.worker.isRunning():
            self.worker.terminate()
            self.worker.wait(3000)

        content_bulk_thread = getattr(self, "_content_bulk_thread", None)
        if content_bulk_thread and content_bulk_thread.isRunning():
            content_bulk_thread.terminate()
            content_bulk_thread.wait(3000)

        update_installer = getattr(self, "_update_installer", None)
        if update_installer and update_installer.isRunning():
            update_installer.terminate()
            update_installer.wait(3000)

        self.settings.setValue("hide_admin_shares", self.chk_hide_admin.isChecked())
        self.settings.setValue("hide_system", self.chk_hide_system.isChecked())
        self.settings.setValue("hide_everyone", self.chk_hide_everyone.isChecked())
        self.settings.setValue("hide_users", self.chk_hide_users.isChecked())
        self.settings.setValue("show_only_users", self.chk_show_only_users.isChecked())
        self.settings.setValue("scan_depth", self.spin_depth.value())
        self.settings.setValue("include_hidden", self.chk_include_hidden.isChecked())
        self.settings.setValue("only_users", self.chk_only_users.isChecked())

        event.accept()

# ---------------------------
# Main
# ---------------------------

def _cargar_traduccion_qt(app):
    """Instala la traducción base de Qt en español para que los botones
    estándar de los diálogos (QMessageBox, QDialogButtonBox: Aceptar,
    Cancelar, Sí, No, Abrir...) se muestren en español y no en inglés."""
    from PyQt5.QtCore import QTranslator, QLibraryInfo
    candidatos = [QLibraryInfo.location(QLibraryInfo.TranslationsPath)]
    if hasattr(sys, "_MEIPASS"):
        candidatos.append(os.path.join(sys._MEIPASS, "PyQt5", "Qt5", "translations"))
        candidatos.append(os.path.join(sys._MEIPASS, "PyQt5", "Qt", "translations"))
    for carpeta in candidatos:
        for nombre in ("qtbase_es.qm", "qt_es.qm"):
            ruta = os.path.join(carpeta, nombre)
            if os.path.isfile(ruta):
                traductor = QTranslator(app)
                if traductor.load(ruta):
                    app.installTranslator(traductor)
                    return
    logger.warning("No se encontró la traducción de Qt al español; los botones "
                    "estándar de los diálogos pueden mostrarse en inglés.")

def _instalar_capturador_errores():
    """5.4.0: sin esto, en PyQt5 cualquier error no previsto dentro de un botón llama a
    qFatal y MASys se CIERRA de golpe, sin aviso y perdiendo lo cargado. Ahora el error
    va al registro y se avisa al técnico; la ventana sigue abierta."""
    import traceback

    def _capturar(tipo, valor, tb):
        if issubclass(tipo, KeyboardInterrupt):
            sys.__excepthook__(tipo, valor, tb)
            return
        detalle = "".join(traceback.format_exception(tipo, valor, tb))
        logger.error("Error no controlado:\n%s", detalle)
        try:
            caja = QMessageBox(QApplication.activeWindow())
            caja.setWindowFlags(caja.windowFlags() & ~Qt.WindowContextHelpButtonHint)
            caja.setIcon(QMessageBox.Warning)
            caja.setWindowTitle("MASys — error")
            caja.setText("La última acción no se ha podido completar por un error inesperado.\n\n"
                         f"{tipo.__name__}: {valor}\n\nMASys sigue abierto; el detalle está en el registro.")
            caja.exec_()
        except Exception:
            pass

    sys.excepthook = _capturar


def main():
    _instalar_capturador_errores()
    app = QApplication(sys.argv)
    app.setApplicationName("MASys")
    app.setOrganizationName("Masoft.es")
    _cargar_traduccion_qt(app)

    font = QFont("Segoe UI", 9)
    app.setFont(font)

    w = ShareInspector()
    w.show()

    sys.exit(app.exec_())

if __name__ == "__main__":
    main()
