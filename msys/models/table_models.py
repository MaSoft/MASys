# -*- coding: utf-8 -*-
"""Modelos de tabla (QAbstractTableModel) y sus proxies de filtro/orden
(QSortFilterProxyModel) para todas las pestanas de MASys. Se mantienen
juntos porque en el original cada modelo vive pegado a su proxy (p. ej.
WiFiTableModel + WiFiFilterProxy), entrelazados unos con otros, no en dos
bloques separados.
Extraido de multisys.py (Fase 1 de modularizacion)."""

import os
import re
import time
import ipaddress
import subprocess
from datetime import datetime
from typing import Optional, Any, Dict, List

from PyQt5.QtCore import Qt, QAbstractTableModel, QModelIndex, QVariant, QSortFilterProxyModel
from PyQt5.QtGui import QColor

from msys.config import logger
from msys.core.utils import (
    _software_estado, _get_software_blacklist, _es_reparse_point,
    _is_eol_software, _is_blacklisted_software,
)
from msys.models.rows import (
    SharePermissionRow, NetworkDriveRow, CredentialRow, WiFiRow, CertificateRow,
    ProcessRow, PROCESS_STATUS_LABELS, PROCESS_PRIORITY_LABELS,
    _PROCESS_PRIORITY_MAP, _PROCESS_PRIORITY_CONST, _CRITICAL_PROCESSES,
    _PROCESS_WINDIR, _is_windows_process, STATUS_LABELS, STARTUP_LABELS,
    ServiceRow, NetworkConnectionRow, InstalledSoftwareRow, ScheduledTaskRow,
    PrinterRow, FirewallRuleRow, EmailAccountRow, BrowserCloudAccountRow, ADInfoRow, GroupPolicyRow,
    StartupProgramRow, USBDeviceRow,
    MonitorRow, InstalledUpdateRow, PendingUpdateRow, DriverRow,
    RegistryResultRow, UserRow, NetworkAdapterRow,
    ActiveSessionRow, BluetoothRow, PSHistoryRow, SystemEventRow,
    RemoteConnectionRow, PartitionRow, DiscoFisicoRow, GPURow,
    PackageUpgradeRow, NetworkDeviceRow,
    _CRITICAL_SERVICES, _is_windows_service, _controlador_generico,
    SqlInstanceRow,
)
from msys.domain.env_vars import EnvVarRow

class _FechaAwareSortProxy(QSortFilterProxyModel):
    """Base de TODOS los proxies de MASys: ordena cronológicamente las
    columnas de fecha en formato dd/MM/yyyy (con hora opcional HH:mm[:ss])
    en vez de alfabéticamente por el texto tal cual.

    El bug: QSortFilterProxyModel por defecto compara el texto mostrado
    carácter a carácter. En 'dd/MM/yyyy' eso hace que el DÍA (los dos
    primeros caracteres) mande sobre el mes y el año — '01/01/2026'
    ordenaba antes que '02/01/2020', exactamente al revés de lo
    cronológico. Los proxies con su propio lessThan (orden numérico real en
    PID/CPU%/memoria/antigüedad...) heredan esto en vez de QSortFilterProxyModel
    directamente, así su super().lessThan() de reserva para el resto de
    columnas también ordena fechas bien; los que no tenían lessThan propio
    lo ganan sin cambiar nada más.

    Mismo bug, mismo motivo, en las columnas 'Antigüedad' ('N días': KB
    instaladas, controladores...): también es texto que no es una fecha
    dd/MM/yyyy, así que caía en la comparación alfabética por defecto —
    '100 días' ordenaba antes que '30 días' antes que '800 días'. Se
    reconoce ese patrón aparte y se ordena por el número real."""

    _RE_FECHA = re.compile(r'^(\d{2})/(\d{2})/(\d{4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$')
    _RE_DIAS = re.compile(r'^(-?\d+(?:[.,]\d+)?)\s*d[ií]as?$', re.IGNORECASE)
    # Celdas que son solo un número («18», «-3», «4,5»): por su valor, no como texto («18» < «2»).
    # Sin punto: «1.234» puede ser separador de miles y se deja como texto.
    _RE_NUMERO = re.compile(r'^\s*(-?\d+(?:,\d+)?)\s*$')

    @classmethod
    def _parsear_fecha(cls, texto):
        if not texto:
            return None
        m = cls._RE_FECHA.match(texto.strip())
        if not m:
            return None
        dia, mes, anio, hora, minuto, segundo = m.groups()
        try:
            return datetime(int(anio), int(mes), int(dia),
                             int(hora or 0), int(minuto or 0), int(segundo or 0))
        except ValueError:
            return None

    @classmethod
    def _parsear_dias(cls, texto):
        if not texto:
            return None
        m = cls._RE_DIAS.match(texto.strip())
        if not m:
            return None
        try:
            return float(m.group(1).replace(',', '.'))
        except ValueError:
            return None

    def lessThan(self, left, right):
        modelo = self.sourceModel()
        if modelo is not None:
            left_txt = str(modelo.data(left, Qt.DisplayRole) or '')
            right_txt = str(modelo.data(right, Qt.DisplayRole) or '')

            left_fecha = self._parsear_fecha(left_txt)
            right_fecha = self._parsear_fecha(right_txt)
            if left_fecha is not None and right_fecha is not None:
                return left_fecha < right_fecha

            left_dias = self._parsear_dias(left_txt)
            right_dias = self._parsear_dias(right_txt)
            if left_dias is not None and right_dias is not None:
                return left_dias < right_dias

            ln, rn = self._RE_NUMERO.match(left_txt), self._RE_NUMERO.match(right_txt)
            if ln and rn:
                return float(ln.group(1).replace(',', '.')) < float(rn.group(1).replace(',', '.'))
        return super().lessThan(left, right)


class CredentialFilterProxy(_FechaAwareSortProxy):
    """Proxy model personalizado para filtrar credenciales por categoría."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.enabled_categories = set()

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        if not model or source_row >= model.rowCount():
            return True

        # Filtro por categoría (columna 2)
        # Si no hay categorías habilitadas, no mostrar nada
        if not self.enabled_categories:
            return False

        category_index = model.index(source_row, 2, source_parent)
        category = str(model.data(category_index, Qt.DisplayRole) or "").strip()
        if category not in self.enabled_categories:
            return False

        # Filtro de búsqueda de texto
        if not super().filterAcceptsRow(source_row, source_parent):
            return False

        return True

    def setEnabledCategories(self, categories: set):
        self.enabled_categories = categories
        self.invalidateFilter()

class UserFilterProxy(_FechaAwareSortProxy):
    """Proxy model personalizado para filtrar usuarios por tipo y estado."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.show_local = True
        self.show_domain = True
        self.show_entra = True
        self.show_system = True
        self.only_active = False

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        if not model or source_row >= model.rowCount():
            return True

        # Obtener tipo (columna 2) y estado (columna 4)
        tipo_index = model.index(source_row, 2, source_parent)
        estado_index = model.index(source_row, 4, source_parent)

        tipo = str(model.data(tipo_index, Qt.DisplayRole) or "").strip().lower()
        estado = str(model.data(estado_index, Qt.DisplayRole) or "").strip().lower()

        # Filtro por tipo
        tipo_match = False
        if self.show_local and "local" in tipo and "sistema" not in tipo:
            tipo_match = True
        if self.show_domain and "dominio" in tipo:
            tipo_match = True
        if self.show_entra and "entra" in tipo:
            tipo_match = True
        if self.show_system and "sistema" in tipo:
            tipo_match = True

        if not tipo_match:
            return False

        # Filtro por estado
        if self.only_active and "inactivo" in estado:
            return False

        # Filtro de búsqueda de texto
        if not super().filterAcceptsRow(source_row, source_parent):
            return False

        return True

    def setFilters(self, show_local: bool, show_domain: bool, show_entra: bool, show_system: bool, only_active: bool):
        self.show_local = show_local
        self.show_domain = show_domain
        self.show_entra = show_entra
        self.show_system = show_system
        self.only_active = only_active
        self.invalidateFilter()

class SystemEventFilterProxy(_FechaAwareSortProxy):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.enabled_levels = set()   # vacío = todos
        self.enabled_logs   = set()   # vacío = todos

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        if not model or source_row >= model.rowCount():
            return True
        nivel = str(model.data(model.index(source_row, 1, source_parent), Qt.DisplayRole) or "").lower()
        log   = str(model.data(model.index(source_row, 2, source_parent), Qt.DisplayRole) or "").lower()
        if self.enabled_levels and not any(n.lower() in nivel for n in self.enabled_levels):
            return False
        if self.enabled_logs and not any(l.lower() in log for l in self.enabled_logs):
            return False
        return super().filterAcceptsRow(source_row, source_parent)

    def setFilters(self, enabled_levels: set, enabled_logs: set):
        self.enabled_levels = enabled_levels
        self.enabled_logs   = enabled_logs
        self.invalidateFilter()

class ServiceFilterProxy(_FechaAwareSortProxy):
    """Proxy de la pestaña Servicios: combina el filtro de texto ya incluido
    en QSortFilterProxyModel (setFilterRegExp/setFilterKeyColumn) con estado
    (Ejecutándose/Detenidos/Deshabilitados/Con nota), 'Solo notas' y
    'Ocultar Windows' — igual que el filtro combinado de MA_SERV, adaptado
    al patrón de proxy que ya usa el resto de MASys."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.status_key = "all"
        self.solo_notas = False
        self.ocultar_win = False
        self.notes: Dict[str, str] = {}

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        if not model or source_row >= model.rowCount():
            return True
        row = model.rows()[source_row]

        if self.ocultar_win and _is_windows_service(row):
            return False

        if self.solo_notas or self.status_key == "noted":
            if row.name not in self.notes:
                return False
        elif self.status_key == "running":
            if row.status != "running":
                return False
        elif self.status_key == "stopped":
            if row.status != "stopped":
                return False
        elif self.status_key == "disabled":
            if row.startup_type != "disabled":
                return False

        return super().filterAcceptsRow(source_row, source_parent)

    def setStatusFilters(self, status_key: str, solo_notas: bool, ocultar_win: bool, notes: Dict[str, str]):
        self.status_key = status_key
        self.solo_notas = solo_notas
        self.ocultar_win = ocultar_win
        self.notes = notes
        self.invalidateFilter()


class ProcessFilterProxy(_FechaAwareSortProxy):
    """Proxy de la pestaña Procesos: filtro de texto + estado (Ejecutándose/
    Suspendidos/Con nota) + 'Solo notas' + 'Ocultar Windows' (por ruta, ver
    _is_windows_process) + orden numérico real en PID/CPU %/Memoria (en vez
    del orden alfabético por texto que usaría QSortFilterProxyModel, donde
    '10' < '9')."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.status_key = "all"
        self.solo_notas = False
        self.ocultar_win = False
        self.notes: Dict[str, str] = {}

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        if not model or source_row >= model.rowCount():
            return True
        row = model.rows()[source_row]
        notes_lower = {n.lower() for n in self.notes}

        if self.ocultar_win and _is_windows_process(row):
            return False

        if self.solo_notas or self.status_key == "noted":
            if row.name.lower() not in notes_lower:
                return False
        elif self.status_key == "running":
            if row.status in ("stopped", "suspended", "dead", "zombie"):
                return False
        elif self.status_key == "suspended":
            if row.status not in ("stopped", "suspended"):
                return False

        return super().filterAcceptsRow(source_row, source_parent)

    def setStatusFilters(self, status_key: str, solo_notas: bool, ocultar_win: bool, notes: Dict[str, str]):
        self.status_key = status_key
        self.solo_notas = solo_notas
        self.ocultar_win = ocultar_win
        self.notes = notes
        self.invalidateFilter()

    def lessThan(self, left, right):
        model = self.sourceModel()
        col = left.column()
        if model and col in (ProcessTableModel.COL_PID, ProcessTableModel.COL_CPU, ProcessTableModel.COL_MEM):
            left_row = model.rows()[left.row()]
            right_row = model.rows()[right.row()]
            if col == ProcessTableModel.COL_PID:
                return left_row.pid < right_row.pid
            if col == ProcessTableModel.COL_CPU:
                return left_row.cpu_percent < right_row.cpu_percent
            if col == ProcessTableModel.COL_MEM:
                return left_row.memory_mb < right_row.memory_mb
        return super().lessThan(left, right)


class EnvVarFilterProxy(_FechaAwareSortProxy):
    """Proxy de la pestaña Variables de entorno: filtro de texto + ámbito
    (Todos/Usuario/Sistema) + 'Solo con problemas' + 'Solo notas'."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.scope_key = "all"
        self.solo_problemas = False
        self.solo_notas = False
        self.notes: Dict[str, str] = {}

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        if not model or source_row >= model.rowCount():
            return True
        row = model.rows()[source_row]
        notes_lower = {n.lower() for n in self.notes}

        if self.scope_key in ("user", "system") and row.scope != self.scope_key:
            return False

        if self.solo_notas and row.name.lower() not in notes_lower:
            return False

        if self.solo_problemas and row.severity == "ok":
            return False

        return super().filterAcceptsRow(source_row, source_parent)

    def setFilters(self, scope_key: str, solo_problemas: bool, solo_notas: bool, notes: Dict[str, str]):
        self.scope_key = scope_key
        self.solo_problemas = solo_problemas
        self.solo_notas = solo_notas
        self.notes = notes
        self.invalidateFilter()


class PendingUpdateFilterProxy(_FechaAwareSortProxy):
    """Proxy de Actualizaciones Pendientes: filtro de texto + 'Solo graves'
    (Crítica/Importante) + orden real por antigüedad (días) en la columna
    'Antigüedad' en vez del orden alfabético por texto ('N días') que
    usaría QSortFilterProxyModel por defecto (donde '10' < '9')."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.solo_graves = False

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        if not model or source_row >= model.rowCount():
            return True
        row = model.rows()[source_row]

        if self.solo_graves:
            criticidad = row.criticidad.lower()
            if not ('crítica' in criticidad or 'critical' in criticidad
                    or 'importante' in criticidad or 'important' in criticidad):
                return False

        return super().filterAcceptsRow(source_row, source_parent)

    def setSoloGraves(self, solo_graves: bool):
        self.solo_graves = solo_graves
        self.invalidateFilter()

    def lessThan(self, left, right):
        model = self.sourceModel()
        if model and left.column() == 6:  # Antigüedad
            left_row = model.rows()[left.row()]
            right_row = model.rows()[right.row()]
            left_dias = left_row.dias_pendiente
            right_dias = right_row.dias_pendiente
            return (left_dias if left_dias is not None else -1) < (right_dias if right_dias is not None else -1)
        return super().lessThan(left, right)


class DriverFilterProxy(_FechaAwareSortProxy):
    """Proxy de Controladores: filtro de texto + 'Solo sin firmar' + 'Solo
    con problemas' + 'Antiguos (+2 años)' (informativo, no implica fallo) +
    'Solo genéricos' (Microsoft en vez del fabricante) + clase de
    dispositivo."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.solo_sin_firmar = False
        self.solo_con_problemas = False
        self.solo_antiguos = False
        self.solo_genericos = False
        self.clase = "Todas las clases"

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        if not model or source_row >= model.rowCount():
            return True
        row = model.rows()[source_row]

        if self.solo_sin_firmar and (row.firmado or "").strip().lower() not in ("no",):
            return False

        if self.solo_con_problemas and not (row.problema or "").strip():
            return False

        if self.solo_antiguos:
            dias = row.dias_antiguedad
            if dias is None or dias <= 730:
                return False

        if self.solo_genericos and not _controlador_generico(row):
            return False

        if self.clase and self.clase != "Todas las clases":
            if (row.clase or "Desconocido").strip() != self.clase:
                return False

        return super().filterAcceptsRow(source_row, source_parent)

    def setFiltros(self, solo_sin_firmar: bool, solo_con_problemas: bool, solo_antiguos: bool,
                   solo_genericos: bool, clase: str):
        self.solo_sin_firmar = solo_sin_firmar
        self.solo_con_problemas = solo_con_problemas
        self.solo_antiguos = solo_antiguos
        self.solo_genericos = solo_genericos
        self.clase = clase
        self.invalidateFilter()


class RemoteConnectionFilterProxy(_FechaAwareSortProxy):
    """Proxy model personalizado para filtrar conexiones remotas."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.show_exito = True
        self.show_fallo = True
        self.enabled_types = set()  # Vacío = todos

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        if not model or source_row >= model.rowCount():
            return True

        # col 3 = TipoAcceso, col 4 = Resultado
        tipo_idx = model.index(source_row, 3, source_parent)
        res_idx  = model.index(source_row, 4, source_parent)
        tipo     = str(model.data(tipo_idx, Qt.DisplayRole) or "")
        resultado= str(model.data(res_idx,  Qt.DisplayRole) or "").lower()

        # Filtro por resultado
        if not self.show_exito and "exito" in resultado:
            return False
        if not self.show_fallo and "fallo" in resultado:
            return False

        # Filtro por tipo de acceso
        if self.enabled_types:
            tipo_lower = tipo.lower()
            matched = any(t.lower() in tipo_lower for t in self.enabled_types)
            if not matched:
                return False

        return super().filterAcceptsRow(source_row, source_parent)

    def setFilters(self, show_exito: bool, show_fallo: bool, enabled_types: set):
        self.show_exito = show_exito
        self.show_fallo = show_fallo
        self.enabled_types = enabled_types
        self.invalidateFilter()

class ContentFilterProxy(QSortFilterProxyModel):
    """Ordena Tamaño (col. 3) y Modificado (col. 4) por su valor numérico
    real (bytes / timestamp, vía ContentTableModel.filaEn) en vez del texto
    ya formateado ("12.34 MB", "24/07/2026 10:15") — comparar el texto
    pondría "9 MB" después de "10 MB"."""

    def lessThan(self, left, right):
        modelo = self.sourceModel()
        col = left.column()
        if col in (3, 4):
            clave = "tamano" if col == 3 else "modificado"
            return modelo.filaEn(left.row())[clave] < modelo.filaEn(right.row())[clave]
        return super().lessThan(left, right)

class NetworkAdapterFilterProxy(_FechaAwareSortProxy):
    """Proxy model personalizado para filtrar adaptadores de red por estado y tipo."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.enabled_states = set()
        self.enabled_types = set()

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        if not model or source_row >= model.rowCount():
            return True

        # Filtro por estado (columna 2)
        if self.enabled_states:
            state_index = model.index(source_row, 2, source_parent)
            state = str(model.data(state_index, Qt.DisplayRole) or "").strip()
            if state not in self.enabled_states:
                return False

        # Filtro por tipo (columna 3)
        if self.enabled_types:
            type_index = model.index(source_row, 3, source_parent)
            tipo = str(model.data(type_index, Qt.DisplayRole) or "").strip()
            if tipo not in self.enabled_types:
                return False

        # Filtro de búsqueda de texto
        if not super().filterAcceptsRow(source_row, source_parent):
            return False

        return True

    def setEnabledStates(self, states: set):
        self.enabled_states = states
        self.invalidateFilter()

    def setEnabledTypes(self, types: set):
        self.enabled_types = types
        self.invalidateFilter()

# ---------------------------
# Funciones helper para recolección de credenciales
# ---------------------------

def collect_ssh_keys() -> List[CredentialRow]:
    """Recolecta claves SSH del directorio .ssh del usuario."""
    ssh_creds = []
    try:
        ssh_dir = os.path.join(os.path.expanduser("~"), ".ssh")
        if not os.path.exists(ssh_dir):
            return ssh_creds

        # Buscar archivos de clave privada comunes
        key_files = []
        for filename in os.listdir(ssh_dir):
            filepath = os.path.join(ssh_dir, filename)
            if os.path.isfile(filepath):
                # Claves privadas comunes (sin extensión .pub)
                if filename in ["id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"] or \
                   (filename.startswith("id_") and not filename.endswith(".pub")):
                    key_files.append((filename, filepath))

        # Intentar leer archivo config para obtener hosts
        config_path = os.path.join(ssh_dir, "config")
        ssh_hosts = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8", errors="ignore") as f:
                    current_host = None
                    for line in f:
                        line = line.strip()
                        if line.startswith("Host ") and not line.startswith("Host *"):
                            current_host = line.split(None, 1)[1]
                        elif current_host and line.startswith("IdentityFile"):
                            identity_file = line.split(None, 1)[1].strip().strip('"')
                            # Extraer solo el nombre del archivo
                            key_name = os.path.basename(identity_file)
                            ssh_hosts[current_host] = key_name
            except Exception as e:
                logger.warning(f"Error leyendo SSH config: {e}")

        # Crear credenciales para cada clave encontrada
        for key_name, key_path in key_files:
            # Buscar host asociado
            host_found = None
            for host, identity in ssh_hosts.items():
                if identity == key_name or identity.endswith(key_name):
                    host_found = host
                    break

            # Determinar última modificación
            try:
                mtime = os.path.getmtime(key_path)
                ultima_mod = time.strftime("%Y-%m-%d", time.localtime(mtime))
            except:
                ultima_mod = "N/A"

            destino = host_found if host_found else f"SSH Key: {key_name}"

            ssh_creds.append(CredentialRow(
                destino=destino,
                usuario="(clave privada)",
                tipo="SSH Private Key",
                categoria="SSH",
                contraseña="(archivo de clave)",
                ultima_modificacion=ultima_mod,
                persistencia="Archivo local",
                estado="No verificada",
                fuente=f"SSH (~/.ssh/{key_name})"
            ))

    except Exception as e:
        logger.warning(f"Error recolectando claves SSH: {e}")

    return ssh_creds

def collect_git_credentials() -> List[CredentialRow]:
    """Recolecta credenciales de Git del usuario."""
    git_creds = []
    try:
        # 1. Leer archivo .git-credentials
        git_creds_file = os.path.join(os.path.expanduser("~"), ".git-credentials")
        if os.path.exists(git_creds_file):
            try:
                with open(git_creds_file, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue

                        # Formato: https://username:password@github.com
                        try:
                            from urllib.parse import urlparse
                            parsed = urlparse(line)
                            if parsed.username:
                                destino = f"{parsed.scheme}://{parsed.hostname}"
                                usuario = parsed.username
                                password_visible = "(guardada en .git-credentials)" if parsed.password else "(sin contraseña)"

                                git_creds.append(CredentialRow(
                                    destino=destino,
                                    usuario=usuario,
                                    tipo="Git Credential",
                                    categoria="Control de Versiones",
                                    contraseña=password_visible,
                                    ultima_modificacion="N/A",
                                    persistencia="Archivo local",
                                    estado="No verificada",
                                    fuente="Git (~/.git-credentials)"
                                ))
                        except Exception as e:
                            logger.warning(f"Error parseando línea de git-credentials: {line[:50]}... - {e}")
            except Exception as e:
                logger.warning(f"Error leyendo .git-credentials: {e}")

        # 2. Leer configuración de Git (git config)
        try:
            # Intentar obtener usuario y email de git config
            result_user = subprocess.run(
                ["git", "config", "--global", "user.name"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            result_email = subprocess.run(
                ["git", "config", "--global", "user.email"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW
            )

            if result_user.returncode == 0 and result_user.stdout.strip():
                git_user = result_user.stdout.strip()
                git_email = result_email.stdout.strip() if result_email.returncode == 0 else "N/A"

                git_creds.append(CredentialRow(
                    destino="Git Global Config",
                    usuario=f"{git_user} <{git_email}>",
                    tipo="Git Config",
                    categoria="Control de Versiones",
                    contraseña="(configuración de identidad)",
                    ultima_modificacion="N/A",
                    persistencia="Global",
                    estado="No verificada",
                    fuente="Git Config"
                ))
        except FileNotFoundError:
            # Git no instalado
            pass
        except Exception as e:
            logger.warning(f"Error obteniendo git config: {e}")

    except Exception as e:
        logger.warning(f"Error recolectando credenciales de Git: {e}")

    return git_creds

def collect_rdp_files() -> List[CredentialRow]:
    """Recolecta archivos RDP del usuario."""
    rdp_creds = []
    try:
        # Buscar en Documents
        documents_dir = os.path.join(os.path.expanduser("~"), "Documents")
        if not os.path.exists(documents_dir):
            return rdp_creds

        # Buscar archivos .rdp. Poda las junctions de NTFS (dirs[:] = ...)
        # antes de que os.walk descienda a ellas — followlinks=False (su
        # valor por defecto) NO las detiene, y una junction que apunte
        # hacia arriba en el árbol (o a una unidad de red enorme) puede
        # colgar el escaneo de credenciales indefinidamente.
        for root, dirs, files in os.walk(documents_dir):
            dirs[:] = [d for d in dirs if not _es_reparse_point(os.path.join(root, d))]
            for filename in files:
                if filename.lower().endswith(".rdp"):
                    filepath = os.path.join(root, filename)
                    try:
                        # Parsear archivo RDP
                        full_address = None
                        username = None

                        with open(filepath, "r", encoding="utf-16-le", errors="ignore") as f:
                            for line in f:
                                line = line.strip()
                                if line.startswith("full address:s:"):
                                    parts = line.split(":", 2)
                                    full_address = parts[2] if len(parts) == 3 else None
                                elif line.startswith("username:s:"):
                                    parts = line.split(":", 2)
                                    username = parts[2] if len(parts) == 3 else None

                        if not full_address:
                            # Intentar con encoding utf-8
                            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                                for line in f:
                                    line = line.strip()
                                    if line.startswith("full address:s:"):
                                        parts = line.split(":", 2)
                                        full_address = parts[2] if len(parts) == 3 else None
                                    elif line.startswith("username:s:"):
                                        parts = line.split(":", 2)
                                        username = parts[2] if len(parts) == 3 else None

                        destino = full_address if full_address else f"RDP: {filename}"
                        usuario_final = username if username else "(no especificado)"

                        # Determinar última modificación
                        try:
                            mtime = os.path.getmtime(filepath)
                            ultima_mod = time.strftime("%Y-%m-%d", time.localtime(mtime))
                        except:
                            ultima_mod = "N/A"

                        rdp_creds.append(CredentialRow(
                            destino=destino,
                            usuario=usuario_final,
                            tipo="RDP Connection File",
                            categoria="Escritorio Remoto",
                            contraseña="(guardada en Credential Manager)",
                            ultima_modificacion=ultima_mod,
                            persistencia="Archivo local",
                            estado="No verificada",
                            fuente=f"RDP File ({os.path.basename(filepath)})"
                        ))
                    except Exception as e:
                        logger.warning(f"Error parseando RDP file {filepath}: {e}")

    except Exception as e:
        logger.warning(f"Error recolectando archivos RDP: {e}")

    return rdp_creds

class NetworkDriveFilterProxy(_FechaAwareSortProxy):
    """Proxy model personalizado para filtrar unidades remotas por estado, letra, servidor y persistencia."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.enabled_states = set()
        self.letra_filter = "Todas"  # "Todas" o una letra específica (ej. "Z:")
        self.servidor_filter = "Todos"  # "Todos" o un servidor específico (ej. "SERVER01")
        self.persistente_enabled = True
        self.temporal_enabled = True

    def _extract_server_from_unc(self, unc_path: str) -> str:
        r"""Extrae el nombre del servidor de una ruta UNC (ej. \\SERVER\share -> SERVER)."""
        try:
            if unc_path.startswith("\\\\"):
                parts = unc_path[2:].split("\\")
                if parts:
                    return parts[0].upper()
        except:
            pass
        return ""

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        if not model or source_row >= model.rowCount():
            return True

        # Filtro por estado (columna 3)
        # Si no hay estados habilitados, no mostrar nada
        if not self.enabled_states:
            return False

        state_index = model.index(source_row, 3, source_parent)
        state = str(model.data(state_index, Qt.DisplayRole) or "").strip()
        if state not in self.enabled_states:
            return False

        # Filtro por Letra (columna 0)
        if self.letra_filter != "Todas":
            letra_index = model.index(source_row, 0, source_parent)
            letra = str(model.data(letra_index, Qt.DisplayRole) or "").strip()
            if letra != self.letra_filter:
                return False

        # Filtro por Servidor (extraído de columna 1 - Ruta Remota)
        if self.servidor_filter != "Todos":
            ruta_index = model.index(source_row, 1, source_parent)
            ruta = str(model.data(ruta_index, Qt.DisplayRole) or "").strip()
            servidor = self._extract_server_from_unc(ruta)
            if servidor != self.servidor_filter.upper():
                return False

        # Filtro por Persistencia (columna 9)
        persistencia_index = model.index(source_row, 9, source_parent)
        persistencia = str(model.data(persistencia_index, Qt.DisplayRole) or "").strip().upper()

        # Si ninguna opción de persistencia está habilitada, no mostrar nada
        if not self.persistente_enabled and not self.temporal_enabled:
            return False

        # Si solo Persistente está habilitado
        if self.persistente_enabled and not self.temporal_enabled:
            if "PERSISTENTE" not in persistencia and persistencia != "SÍ":
                return False

        # Si solo Temporal está habilitado
        if self.temporal_enabled and not self.persistente_enabled:
            if "TEMPORAL" not in persistencia and persistencia != "NO":
                return False

        # Filtro de búsqueda de texto
        if not super().filterAcceptsRow(source_row, source_parent):
            return False

        return True

    def setEnabledStates(self, states: set):
        self.enabled_states = states
        self.invalidateFilter()

    def setLetraFilter(self, letra: str):
        """Establece el filtro por letra de unidad."""
        self.letra_filter = letra
        self.invalidateFilter()

    def setServidorFilter(self, servidor: str):
        """Establece el filtro por servidor."""
        self.servidor_filter = servidor
        self.invalidateFilter()

    def setPersistenciaFilter(self, persistente: bool, temporal: bool):
        """Establece el filtro por tipo de persistencia."""
        self.persistente_enabled = persistente
        self.temporal_enabled = temporal
        self.invalidateFilter()

class CustomFilterProxy(_FechaAwareSortProxy):
    """Proxy model personalizado con filtros adicionales."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hide_system = False
        self.hide_everyone = False
        self.hide_admin_shares = False
        self.hide_users = False
        self.show_only_users = False

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        if not model or source_row >= model.rowCount():
            return True

        # Obtener Recurso (columna 0) para verificar si es administrativo
        resource_index = model.index(source_row, 0, source_parent)
        resource = str(model.data(resource_index, Qt.DisplayRole) or "").strip()
        is_admin_resource = resource.endswith("$")

        # FILTRO 1: Recursos administrativos (basado en columna Recurso - columna 0)
        if self.hide_admin_shares:
            if is_admin_resource:
                return False

        # FILTRO 2: Permisos de sistema (basado en columna Usuario/Grupo - columna 2)
        # PERO NO aplica a recursos administrativos (esos solo se filtran con filtro 1)
        if self.hide_system and not is_admin_resource:
            identity_index = model.index(source_row, 2, source_parent)
            identity = str(model.data(identity_index, Qt.DisplayRole) or "").strip()
            identity_upper = identity.upper()
            if "NT AUTHORITY" in identity_upper or "BUILTIN" in identity_upper or "NT SERVICE" in identity_upper:
                return False

        # FILTRO 3: Grupo "Todos" (basado en columna Usuario/Grupo - columna 2)
        # PERO NO aplica a recursos administrativos (esos solo se filtran con filtro 1)
        if self.hide_everyone and not is_admin_resource:
            identity_index = model.index(source_row, 2, source_parent)
            identity = str(model.data(identity_index, Qt.DisplayRole) or "").strip()
            identity_upper = identity.upper()
            if "TODOS" in identity_upper or "EVERYONE" in identity_upper:
                return False

        # FILTRO 4: Ocultar Usuarios (local/dominio)
        # PERO NO aplica a recursos administrativos (esos solo se filtran con filtro 1)
        if self.hide_users and not is_admin_resource:
            identity_index = model.index(source_row, 2, source_parent)
            identity = str(model.data(identity_index, Qt.DisplayRole) or "").strip()
            identity_upper = identity.upper()
            # Es usuario si NO es sistema, NO es "Todos" y NO está vacío
            is_system = "NT AUTHORITY" in identity_upper or "BUILTIN" in identity_upper or "NT SERVICE" in identity_upper
            is_everyone = "TODOS" in identity_upper or "EVERYONE" in identity_upper
            if not is_system and not is_everyone and identity:
                return False

        # FILTRO 5: Ver SOLO Usuarios (local/dominio)
        # PERO NO aplica a recursos administrativos (esos solo se filtran con filtro 1)
        if self.show_only_users and not is_admin_resource:
            identity_index = model.index(source_row, 2, source_parent)
            identity = str(model.data(identity_index, Qt.DisplayRole) or "").strip()
            identity_upper = identity.upper()
            # Es usuario si NO es sistema y NO es "Todos"
            is_system = "NT AUTHORITY" in identity_upper or "BUILTIN" in identity_upper or "NT SERVICE" in identity_upper
            is_everyone = "TODOS" in identity_upper or "EVERYONE" in identity_upper
            if is_system or is_everyone or not identity:
                return False

        # FILTRO 6: Búsqueda por texto (aplica a todas las columnas)
        # Se aplica AL FINAL para que no interfiera con los otros filtros
        if not super().filterAcceptsRow(source_row, source_parent):
            return False

        return True

    def setHideSystem(self, hide: bool):
        self.hide_system = hide
        self.invalidateFilter()

    def setHideEveryone(self, hide: bool):
        self.hide_everyone = hide
        self.invalidateFilter()

    def setHideAdminShares(self, hide: bool):
        self.hide_admin_shares = hide
        self.invalidateFilter()

    def setHideUsers(self, hide: bool):
        self.hide_users = hide
        self.invalidateFilter()

    def setShowOnlyUsers(self, show: bool):
        self.show_only_users = show
        self.invalidateFilter()

class ShareTableModel(QAbstractTableModel):
    HEADERS = ["Recurso", "Ruta", "Usuario/Grupo", "Permiso", "Tipo", "Origen"]

    def __init__(self, rows: List[SharePermissionRow] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0: return row.recurso.strip()
            if col == 1: return row.ruta.strip()
            if col == 2: return row.identidad.strip()
            if col == 3: return row.permiso.strip()
            if col == 4: return row.tipo.strip()
            if col == 5: return row.origen.strip()

        if role == Qt.BackgroundRole and col in (2, 3, 4) and row.es_riesgo_everyone_escritura:
            return QColor("#f8d7da")
        if role == Qt.ToolTipRole and row.es_riesgo_everyone_escritura:
            return "⚠️ Riesgo: 'Todos/Everyone' tiene permiso de escritura/control total"

        if role == Qt.TextAlignmentRole:
            if col in (4, 5):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        else:
            return section + 1

    def setRows(self, rows: List[SharePermissionRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rows(self) -> List[SharePermissionRow]:
        return self._rows

class NetworkDriveTableModel(QAbstractTableModel):
    HEADERS = ["Letra", "Ruta Remota", "Descripción", "Estado", "Total", "Usado", "Libre",
               "% Uso", "Credencial", "Persistencia", "SMB", "Velocidad", "Ping", "Accesible"]

    def __init__(self, rows: List[NetworkDriveRow] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0: return row.letra.strip()
            if col == 1: return row.ruta_remota.strip()
            if col == 2: return row.descripcion.strip()
            if col == 3: return row.estado.strip()
            if col == 4: return row.total_gb.strip()
            if col == 5: return row.usado_gb.strip()
            if col == 6: return row.libre_gb.strip()
            if col == 7: return row.porcentaje_uso.strip()
            if col == 8: return row.credencial.strip()
            if col == 9: return row.persistente.strip()
            if col == 10: return row.version_smb.strip()
            if col == 11: return row.velocidad.strip()
            if col == 12: return row.ping_ms.strip()
            if col == 13: return row.accesible.strip()

        # Color coding for Estado column (col 3)
        if role == Qt.BackgroundRole and col == 3:
            estado = row.estado.upper()
            if "CONECTADA" in estado:
                return QColor("#d4edda")  # Verde
            elif "DESCONECTADA" in estado:
                return QColor("#fff3cd")  # Amarillo
            elif "ERROR" in estado:
                return QColor("#f8d7da")  # Rojo

        # Color coding for % Uso column (col 7)
        if role == Qt.BackgroundRole and col == 7:
            try:
                percent = float(row.porcentaje_uso.replace('%', ''))
                if percent >= 90:
                    return QColor("#f8d7da")  # Rojo
                elif percent >= 75:
                    return QColor("#fff3cd")  # Amarillo
            except:
                pass

        # Color coding for Accesible column (col 13)
        if role == Qt.BackgroundRole and col == 13:
            if row.accesible.strip() == "No":
                return QColor("#f8d7da")  # Rojo

        if role == Qt.TextAlignmentRole:
            if col in (0, 3, 7, 9, 10, 11, 12, 13):  # Centered columns
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        else:
            return section + 1

    def setRows(self, rows: List[NetworkDriveRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rows(self) -> List[NetworkDriveRow]:
        return self._rows

class CredentialTableModel(QAbstractTableModel):
    HEADERS = ["Destino", "Usuario", "Categoría", "Tipo", "Contraseña", "Última Modificación", "Persistencia", "Estado", "Fuente"]

    def __init__(self, rows: List[CredentialRow] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0: return row.destino.strip()
            if col == 1: return row.usuario.strip()
            if col == 2: return row.categoria.strip()
            if col == 3: return row.tipo.strip()
            if col == 4: return row.contraseña.strip()
            if col == 5: return row.ultima_modificacion.strip()
            if col == 6: return row.persistencia.strip()
            if col == 7: return row.estado.strip()
            if col == 8: return row.fuente.strip()

        # Color coding for Estado column
        if role == Qt.BackgroundRole and col == 7:
            estado = row.estado.strip().lower()
            if "válida" in estado or "verificada" in estado:
                return QColor("#d4edda")  # Verde
            elif "inválida" in estado or "error" in estado:
                return QColor("#f8d7da")  # Rojo
            elif "no verificada" in estado:
                return QColor("#fff3cd")  # Amarillo

        if role == Qt.TextAlignmentRole:
            if col in (2, 3, 6, 7, 8):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        else:
            return section + 1

    def setRows(self, rows: List[CredentialRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rows(self) -> List[CredentialRow]:
        return self._rows

class WiFiTableModel(QAbstractTableModel):
    HEADERS = ["Nombre", "Contraseña", "Autenticación", "Cifrado", "Tipo Conexión", "Conectado"]

    def __init__(self, rows: List[WiFiRow] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0: return row.nombre.strip()
            if col == 1: return row.contraseña.strip()
            if col == 2: return row.autenticacion.strip()
            if col == 3: return row.cifrado.strip()
            if col == 4: return row.tipo_conexion.strip()
            if col == 5: return row.conectado.strip()

        # Color for connected WiFi
        if role == Qt.BackgroundRole and col == 5:
            if row.conectado.strip().lower() == "si":
                return QColor("#d4edda")  # Verde

        # Seguridad de la autenticación: roja si es abierta/WEP (riesgo real)
        if role == Qt.BackgroundRole and col == 2 and row.es_seguridad_debil:
            return QColor("#f8d7da")
        if role == Qt.ForegroundRole and col == 2 and row.es_seguridad_debil:
            return QColor("#c0392b")

        if role == Qt.TextAlignmentRole:
            if col in (2, 3, 4, 5):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        else:
            return section + 1

    def setRows(self, rows: List[WiFiRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rows(self) -> List[WiFiRow]:
        return self._rows

class WiFiFilterProxy(_FechaAwareSortProxy):
    """Filtra la tabla de WiFi por seguridad (segura/insegura) y por si
    está conectada actualmente, además del texto libre ya heredado."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._show_seguras = True
        self._show_inseguras = True
        self._solo_conectada = False

    def set_filtros(self, show_seguras: bool, show_inseguras: bool, solo_conectada: bool):
        self._show_seguras = show_seguras
        self._show_inseguras = show_inseguras
        self._solo_conectada = solo_conectada
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        if not super().filterAcceptsRow(source_row, source_parent):
            return False
        model = self.sourceModel()
        row = model.rows()[source_row]
        if row.es_seguridad_debil and not self._show_inseguras:
            return False
        if not row.es_seguridad_debil and not self._show_seguras:
            return False
        if self._solo_conectada and row.conectado.strip().lower() != "si":
            return False
        return True

class CertificateTableModel(QAbstractTableModel):
    HEADERS = ["Nombre", "Emisor", "Válido Desde", "Válido Hasta", "Huella Digital", "Uso", "Ubicación", "Estado", "Exportable", "Algoritmo Firma"]

    def __init__(self, rows: List[CertificateRow] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0: return row.asunto.strip()
            if col == 1: return row.emisor.strip()
            if col == 2: return row.valido_desde.strip()
            if col == 3: return row.valido_hasta.strip()
            if col == 4: return row.huella_digital.strip()
            if col == 5: return row.uso.strip()
            if col == 6: return row.ubicacion.strip()
            if col == 7: return row.estado.strip()
            if col == 8: return row.exportable.strip()
            if col == 9: return row.algoritmo_firma.strip()

        # Sospechoso (algoritmo débil o autofirmado en \My) tiene prioridad
        # sobre el color de Estado — mismo criterio que Red Local con sus
        # dispositivos marcados.
        if role == Qt.BackgroundRole and row.sospechoso:
            return QColor("#f8d7da")  # Rojo, toda la fila

        # Color coding for Estado column
        if role == Qt.BackgroundRole and col == 7:
            estado = row.estado.strip().lower()
            if "valido" in estado or "válido" in estado:
                return QColor("#d4edda")  # Verde
            elif "caducado" in estado:
                return QColor("#f8d7da")  # Rojo
            elif "proxima" in estado or "próxima" in estado:
                return QColor("#fff3cd")  # Amarillo

        # Color coding for Exportable column
        if role == Qt.BackgroundRole and col == 8:
            exportable = row.exportable.strip().lower()
            if "si" in exportable or "sí" in exportable:
                return QColor("#d4edda")  # Verde
            elif "bloqueada" in exportable:
                return QColor("#f8d7da")  # Rojo

        if role == Qt.TextAlignmentRole:
            if col in (2, 3, 6, 7, 8, 9):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        else:
            return section + 1

    def setRows(self, rows: List[CertificateRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rows(self) -> List[CertificateRow]:
        return self._rows


class CertificateFilterProxy(_FechaAwareSortProxy):
    """Proxy persistente de Certificados — antes cada cambio de filtro
    recreaba una clase de proxy entera y hacía setModel() en la QTableView,
    lo que reiniciaba selección y orden en cada pulsación de tecla o
    checkbox (único caso en todo MASys que no seguía el patrón estándar
    de *FilterProxy + setFilters()+invalidateFilter() del resto de
    pestañas). El texto de búsqueda se delega en el mecanismo estándar de
    QSortFilterProxyModel (setFilterRegExp externo + super().filterAcceptsRow
    al final), igual que UserFilterProxy."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.show_validos = True
        self.show_proximos = True
        self.show_caducados = True
        self.show_personales = False
        self.show_exportables = False
        self.show_sistema = True
        self.show_usuario = True
        self.show_sospechosos_only = False

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        if not model or source_row >= model.rowCount():
            return True

        estado = str(model.data(model.index(source_row, 7, source_parent), Qt.DisplayRole) or "").lower()

        estado_ok = False
        if self.show_validos and ("valido" in estado or "válido" in estado):
            estado_ok = True
        if self.show_proximos and ("proxima" in estado or "próxima" in estado):
            estado_ok = True
        if self.show_caducados and "caducado" in estado:
            estado_ok = True
        if not estado_ok:
            return False

        if self.show_personales:
            uso = str(model.data(model.index(source_row, 5, source_parent), Qt.DisplayRole) or "").lower()
            if "autenticaci" not in uso and "client authentication" not in uso:
                return False

        if self.show_exportables:
            exportable = str(model.data(model.index(source_row, 8, source_parent), Qt.DisplayRole) or "").lower()
            if "si" not in exportable and "sí" not in exportable:
                return False

        row = model.rows()[source_row]
        ubicacion_lower = (row.ubicacion or "").lower()
        es_sistema = ubicacion_lower.startswith("localmachine")
        es_usuario = ubicacion_lower.startswith("currentuser")
        if es_sistema and not self.show_sistema:
            return False
        if es_usuario and not self.show_usuario:
            return False

        if self.show_sospechosos_only and not row.sospechoso:
            return False

        return super().filterAcceptsRow(source_row, source_parent)

    def setFilters(self, show_validos: bool, show_proximos: bool, show_caducados: bool,
                    show_personales: bool, show_exportables: bool,
                    show_sistema: bool = True, show_usuario: bool = True,
                    show_sospechosos_only: bool = False):
        self.show_validos = show_validos
        self.show_proximos = show_proximos
        self.show_caducados = show_caducados
        self.show_personales = show_personales
        self.show_exportables = show_exportables
        self.show_sistema = show_sistema
        self.show_usuario = show_usuario
        self.show_sospechosos_only = show_sospechosos_only
        self.invalidateFilter()


_ENV_SCOPE_LABELS = {"user": "Usuario", "system": "Sistema"}
_ENV_SEVERITY_LABELS = {
    "ok": "OK", "info": "Info", "warning": "Revisar", "critical": "Crítico",
}


class EnvVarTableModel(QAbstractTableModel):
    """Modelo de variables de entorno (leídas del registro, ver VarManager).
    Identidad de fila = (nombre, ámbito) — no solo el nombre, porque una
    misma variable puede tener una fila en Usuario y otra en Sistema.

    Las celdas con BackgroundRole en pastel (mismo esquema que Servicios/
    Procesos) SIEMPRE fijan también un ForegroundRole oscuro explícito: en
    tema oscuro, QTableView pinta el texto en gris claro (~#e6e6e6) por la
    hoja de estilos global, y ese gris sobre un fondo pastel claro sería
    prácticamente ilegible si no se fuerza aquí un color de texto oscuro."""

    HEADERS = ["Nombre", "Valor", "Ámbito", "Estado"]
    COL_NAME, COL_VALUE, COL_SCOPE, COL_STATE = range(4)

    _DARK_TEXT = QColor("#212121")

    def __init__(self, rows: List[EnvVarRow] = None, notes: Dict[str, str] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []
        self._notes = notes or {}

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == self.COL_NAME:
                nota = " 📝" if row.name.lower() in {n.lower() for n in self._notes} else ""
                return row.name.strip() + nota
            if col == self.COL_VALUE:
                # Una sola línea en la tabla — el valor completo vive en el
                # panel de detalle, igual que Ruta/Descripción en
                # Procesos/Servicios.
                return row.value.replace("\n", " ").strip()
            if col == self.COL_SCOPE: return _ENV_SCOPE_LABELS.get(row.scope, row.scope)
            if col == self.COL_STATE:
                if row.severity == "ok":
                    return "OK"
                return f"{_ENV_SEVERITY_LABELS.get(row.severity, row.severity)} ({len(row.issues)})"

        if role == Qt.ForegroundRole and col == self.COL_NAME and row.severity == "critical":
            return QColor("#C62828")

        if role == Qt.BackgroundRole and col == self.COL_STATE:
            if row.severity == "critical":
                return QColor("#f8d7da")
            elif row.severity == "warning":
                return QColor("#fff3cd")
            elif row.severity == "info":
                return QColor("#d1ecf1")
            else:
                return QColor("#d4edda")

        if role == Qt.ForegroundRole and col == self.COL_STATE:
            # Ver __doc__ de la clase: sin esto, el texto sería ilegible en
            # tema oscuro sobre estos fondos pastel claros.
            return self._DARK_TEXT

        if role == Qt.BackgroundRole and col == self.COL_SCOPE:
            if row.scope == "user":
                return QColor("#d4edda")
            elif row.scope == "system":
                return QColor("#fff3cd")

        if role == Qt.ForegroundRole and col == self.COL_SCOPE:
            return self._DARK_TEXT

        if role == Qt.ToolTipRole and col in (self.COL_NAME, self.COL_STATE) and row.issues:
            return "\n".join(row.issues)

        if role == Qt.TextAlignmentRole:
            if col in (self.COL_SCOPE, self.COL_STATE):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        if role == Qt.UserRole:
            return (row.name, row.scope)

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        else:
            return section + 1

    def setRows(self, rows: List[EnvVarRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def setNotes(self, notes: Dict[str, str]):
        self._notes = notes
        if self._rows:
            top_left = self.index(0, self.COL_NAME)
            bottom_right = self.index(len(self._rows) - 1, self.COL_NAME)
            self.dataChanged.emit(top_left, bottom_right, [Qt.DisplayRole])

    def row_by_key(self, name: str, scope: str) -> Optional[EnvVarRow]:
        for r in self._rows:
            if r.name == name and r.scope == scope:
                return r
        return None

    def rows(self) -> List[EnvVarRow]:
        return self._rows

class ProcessTableModel(QAbstractTableModel):
    """Modelo de procesos (operativa portada de MAProcess). 'name' NO es
    único (varias instancias pueden compartir nombre) — Qt.UserRole devuelve
    el PID, que es el identificador real de la fila para acciones, a
    diferencia de ServiceTableModel donde UserRole devuelve el nombre."""

    HEADERS = ["Nombre", "PID", "Estado", "Prioridad", "Usuario", "CPU %", "Memoria (MB)"]
    COL_DISPLAY, COL_PID, COL_STATE, COL_PRIORITY, COL_USER, COL_CPU, COL_MEM = range(7)

    def __init__(self, rows: List[ProcessRow] = None, notes: Dict[str, str] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []
        self._notes = notes or {}

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == self.COL_DISPLAY:
                nota = " 📝" if row.name.lower() in {n.lower() for n in self._notes} else ""
                return row.name.strip() + nota
            if col == self.COL_PID: return str(row.pid)
            if col == self.COL_STATE: return PROCESS_STATUS_LABELS.get(row.status, row.status)
            if col == self.COL_PRIORITY: return PROCESS_PRIORITY_LABELS.get(row.priority, row.priority)
            if col == self.COL_USER: return row.username.strip()
            if col == self.COL_CPU: return f"{row.cpu_percent:.1f}"
            if col == self.COL_MEM: return f"{row.memory_mb:.1f}"

        if role == Qt.ForegroundRole and col == self.COL_DISPLAY:
            if row.name.lower() in _CRITICAL_PROCESSES:
                return QColor("#C62828")

        # Mismo esquema de "semáforo" pastel (BackgroundRole) que usa
        # ServiceTableModel.
        if role == Qt.BackgroundRole and col == self.COL_STATE:
            if row.status == "running":
                return QColor("#d4edda")   # Verde
            elif row.status in ("stopped", "suspended"):
                return QColor("#fff3cd")   # Ámbar — suspendido es reversible, no es un error
            elif row.status in ("zombie", "dead"):
                return QColor("#f8d7da")   # Rojo

        if role == Qt.TextAlignmentRole:
            if col in (self.COL_PID, self.COL_STATE, self.COL_PRIORITY, self.COL_CPU, self.COL_MEM):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        if role == Qt.UserRole:
            return row.pid

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        else:
            return section + 1

    def setRows(self, rows: List[ProcessRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def setNotes(self, notes: Dict[str, str]):
        self._notes = notes
        if self._rows:
            top_left = self.index(0, self.COL_DISPLAY)
            bottom_right = self.index(len(self._rows) - 1, self.COL_DISPLAY)
            self.dataChanged.emit(top_left, bottom_right, [Qt.DisplayRole])

    def row_by_pid(self, pid: int) -> Optional[ProcessRow]:
        for r in self._rows:
            if r.pid == pid:
                return r
        return None

    def rows(self) -> List[ProcessRow]:
        return self._rows

class ServiceTableModel(QAbstractTableModel):
    """Modelo de servicios de Windows (operativa portada de MA_SERV). Se
    mantiene sobre el patrón QAbstractTableModel/QSortFilterProxyModel que
    ya usa el resto de MASys (en vez de la QTableWidget plana de
    MA_SERV) para no romper la barra de exportación adaptativa (CSV/HTML/
    PDF/Excel), que lee genéricamente de cualquier proxy registrado en
    _tabla_secciones_exportables()."""

    HEADERS = ["Nombre para mostrar", "Servicio", "Estado", "Inicio", "Cuenta"]
    COL_DISPLAY, COL_NAME, COL_STATE, COL_STARTUP, COL_ACCOUNT = range(5)

    def __init__(self, rows: List[ServiceRow] = None, notes: Dict[str, str] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []
        self._notes = notes or {}

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == self.COL_DISPLAY:
                nota = " 📝" if row.name in self._notes else ""
                return row.display_name.strip() + nota
            if col == self.COL_NAME: return row.name.strip()
            if col == self.COL_STATE: return STATUS_LABELS.get(row.status, row.status)
            if col == self.COL_STARTUP: return STARTUP_LABELS.get(row.startup_type, row.startup_type)
            if col == self.COL_ACCOUNT: return row.account.strip()

        if role == Qt.ForegroundRole and col == self.COL_DISPLAY:
            if row.name in _CRITICAL_SERVICES:
                return QColor("#C62828")

        # Mismo esquema de "semáforo" pastel (BackgroundRole) que ya usan el
        # resto de tablas de MASys (Estado/Tipo de Inicio, Usuarios,
        # Firewall...): no depende del tema claro/oscuro porque es una
        # convención ya establecida en toda la app, no algo nuevo de esta
        # pestaña.
        if role == Qt.BackgroundRole and col == self.COL_STATE:
            if row.status == "running":
                return QColor("#d4edda")   # Verde
            elif row.status == "stopped":
                return QColor("#f8d7da")   # Rojo
            elif row.status in ("start_pending", "stop_pending", "continue_pending", "pause_pending"):
                return QColor("#fff3cd")   # Amarillo

        if role == Qt.BackgroundRole and col == self.COL_STARTUP:
            if row.startup_type in ("auto", "auto_delayed", "boot", "system"):
                return QColor("#d4edda")   # Verde
            elif row.startup_type == "disabled":
                return QColor("#f8d7da")   # Rojo

        if role == Qt.TextAlignmentRole:
            if col in (self.COL_STATE, self.COL_STARTUP):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        if role == Qt.UserRole:
            return row.name

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        else:
            return section + 1

    def setRows(self, rows: List[ServiceRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def setNotes(self, notes: Dict[str, str]):
        self._notes = notes
        if self._rows:
            top_left = self.index(0, self.COL_DISPLAY)
            bottom_right = self.index(len(self._rows) - 1, self.COL_DISPLAY)
            self.dataChanged.emit(top_left, bottom_right, [Qt.DisplayRole])

    def rows(self) -> List[ServiceRow]:
        return self._rows

    def row_by_name(self, name: str) -> Optional[ServiceRow]:
        for r in self._rows:
            if r.name == name:
                return r
        return None


class SqlInstanceTableModel(QAbstractTableModel):
    """Modelo de instancias de SQL Server/LocalDB detectadas (ver
    detectar_sql_server en collectors.py). Mismo esquema de semáforo
    pastel que ServiceTableModel para las columnas de estado."""

    HEADERS = ["Instancia", "Tipo", "Versión", "Edición", "Estado motor",
               "Estado agente", "Ruta de datos"]
    (COL_INSTANCIA, COL_TIPO, COL_VERSION, COL_EDICION,
     COL_ESTADO_MOTOR, COL_ESTADO_AGENTE, COL_RUTA) = range(7)

    def __init__(self, rows: List[SqlInstanceRow] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == self.COL_INSTANCIA: return row.instancia
            if col == self.COL_TIPO: return row.tipo
            if col == self.COL_VERSION: return row.version
            if col == self.COL_EDICION: return row.edicion
            if col == self.COL_ESTADO_MOTOR: return STATUS_LABELS.get(row.estado_servicio, row.estado_servicio)
            if col == self.COL_ESTADO_AGENTE: return STATUS_LABELS.get(row.estado_agente, row.estado_agente)
            if col == self.COL_RUTA: return row.ruta_datos

        if role == Qt.BackgroundRole and col in (self.COL_ESTADO_MOTOR, self.COL_ESTADO_AGENTE):
            estado = row.estado_servicio if col == self.COL_ESTADO_MOTOR else row.estado_agente
            if estado == "running":
                return QColor("#d4edda")   # Verde
            elif estado == "stopped":
                return QColor("#f8d7da")   # Rojo
            elif estado in ("start_pending", "stop_pending", "continue_pending", "pause_pending"):
                return QColor("#fff3cd")   # Amarillo

        if role == Qt.TextAlignmentRole:
            if col in (self.COL_TIPO, self.COL_VERSION, self.COL_EDICION,
                       self.COL_ESTADO_MOTOR, self.COL_ESTADO_AGENTE):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        return section + 1

    def setRows(self, rows: List[SqlInstanceRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rows(self) -> List[SqlInstanceRow]:
        return self._rows


class NetworkConnectionTableModel(QAbstractTableModel):
    HEADERS = ["Protocolo", "Dirección Local", "Dirección Remota", "Estado", "Proceso", "PID"]

    def __init__(self, rows: List[NetworkConnectionRow] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0: return row.protocolo.strip()
            if col == 1: return row.direccion_local.strip()
            if col == 2: return row.direccion_remota.strip()
            if col == 3: return row.estado.strip()
            if col == 4: return row.proceso.strip()
            if col == 5: return row.pid.strip()

        # Color coding for Estado column
        if role == Qt.BackgroundRole and col == 3:
            estado = row.estado.strip().lower()
            if "establecida" in estado or "established" in estado:
                return QColor("#d4edda")  # Verde

        # Proceso no identificado o disfrazado (nombre de proceso crítico
        # del sistema corriendo fuera de su carpeta real): posible indicio
        # a revisar, mismo color para las dos señales.
        if role == Qt.BackgroundRole and col == 4 and (row.es_sospechosa or row.es_disfrazado):
            return QColor("#f8d7da")
        if role == Qt.ForegroundRole and col == 4 and (row.es_sospechosa or row.es_disfrazado):
            return QColor("#c0392b")

        if role == Qt.TextAlignmentRole:
            if col in (0, 3, 5):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        else:
            return section + 1

    def setRows(self, rows: List[NetworkConnectionRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rows(self) -> List[NetworkConnectionRow]:
        return self._rows

class NetworkConnectionFilterProxy(_FechaAwareSortProxy):
    """Filtra conexiones de red por protocolo (TCP/UDP), procesos de
    sistema, conexiones sospechosas (proceso no identificado o disfrazado)
    y solo-Internet (IP remota pública), además del texto libre heredado."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._show_tcp = True
        self._show_udp = True
        self._show_sistema = True
        self._solo_sospechosas = False
        self._solo_publicas = False

    def set_filtros(self, show_tcp: bool, show_udp: bool, show_sistema: bool,
                     solo_sospechosas: bool, solo_publicas: bool = False):
        self._show_tcp = show_tcp
        self._show_udp = show_udp
        self._show_sistema = show_sistema
        self._solo_sospechosas = solo_sospechosas
        self._solo_publicas = solo_publicas
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        if not super().filterAcceptsRow(source_row, source_parent):
            return False
        model = self.sourceModel()
        row = model.rows()[source_row]
        protocolo = row.protocolo.strip().upper()
        if protocolo == "TCP" and not self._show_tcp:
            return False
        if protocolo == "UDP" and not self._show_udp:
            return False
        if row.es_sistema and not self._show_sistema:
            return False
        if self._solo_sospechosas and not (row.es_sospechosa or row.es_disfrazado):
            return False
        if self._solo_publicas and not row.es_ip_publica:
            return False
        return True

class InstalledSoftwareTableModel(QAbstractTableModel):
    HEADERS = ["Nombre", "Versión", "Editor", "Fecha de Instalación", "Carpeta de Instalación", "Estado"]

    def __init__(self, rows: List[InstalledSoftwareRow] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []
        self._blacklist = _get_software_blacklist()

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0: return (row.nombre or "").strip()
            if col == 1: return (row.version or "").strip()
            if col == 2: return (row.editor or "").strip()
            if col == 3: return (row.fecha_instalacion or "").strip()
            if col == 4: return (row.carpeta_instalacion or "").strip()
            if col == 5: return _software_estado(row.nombre, self._blacklist, row.system_component)

        if role == Qt.TextAlignmentRole:
            if col in (3, 5):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        if role == Qt.ForegroundRole and col == 5:
            estado = _software_estado(row.nombre, self._blacklist, row.system_component)
            if "No autorizado" in estado:
                return QColor("#c0392b")
            if "EOL" in estado:
                return QColor("#d68910")
            return QColor("#1e8449")

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        else:
            return section + 1

    def setRows(self, rows: List[InstalledSoftwareRow]):
        self.beginResetModel()
        self._rows = rows
        self._blacklist = _get_software_blacklist()
        self.endResetModel()

    def refresh_blacklist(self):
        """Vuelve a leer la lista negra (tras editarla) sin recargar los datos."""
        self.beginResetModel()
        self._blacklist = _get_software_blacklist()
        self.endResetModel()

    def rows(self) -> List[InstalledSoftwareRow]:
        return self._rows


class InstalledSoftwareFilterProxy(_FechaAwareSortProxy):
    """Proxy de Software Instalado: filtro de texto (heredado) + 5
    checkboxes independientes combinables en AND (Microsoft / Otros
    fabricantes / Antiguos 2+ años / Sin soporte (EOL) / No autorizado) —
    igual que 'Solo sospechosas'+'Mostrar deshabilitadas' en Tareas
    Programadas: cada casilla marcada añade una condición más, ninguna
    excluye a las demás. Marcar Microsoft Y Otros a la vez es
    contradictorio a propósito (no hay validación especial): el resultado
    es simplemente 0 filas, visible de inmediato en el contador."""

    ANTIGUEDAD_DIAS = 730  # 2 años

    def __init__(self, parent=None):
        super().__init__(parent)
        self.solo_microsoft = False
        self.solo_otros = False
        self.solo_antiguos = False
        self.solo_eol = False
        self.solo_no_autorizado = False
        # Ocultos por defecto: SystemComponent=1 son runtimes/subcomponentes
        # (VC++ redistributables, piezas de drivers...) que ni siquiera
        # Programas y características muestra por defecto — mismo criterio
        # aquí, con opción explícita de mostrarlos.
        self.mostrar_componentes = False
        self._blacklist = _get_software_blacklist()

    def refresh_blacklist(self):
        """Vuelve a leer la lista negra (tras editarla) sin recargar los
        datos ni perder los filtros activos."""
        self._blacklist = _get_software_blacklist()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        if not model or source_row >= model.rowCount():
            return True
        row = model.rows()[source_row]
        editor = (row.editor or "").strip().lower()

        if row.system_component and not self.mostrar_componentes:
            return False
        if self.solo_microsoft and "microsoft" not in editor:
            return False
        if self.solo_otros and "microsoft" in editor:
            return False
        if self.solo_antiguos:
            dias = row.dias_desde_instalacion
            if dias is None or dias < self.ANTIGUEDAD_DIAS:
                return False
        if self.solo_eol and not _is_eol_software(row.nombre):
            return False
        if self.solo_no_autorizado and not _is_blacklisted_software(row.nombre, self._blacklist):
            return False

        return super().filterAcceptsRow(source_row, source_parent)

    def setFiltros(self, microsoft: bool, otros: bool, antiguos: bool, eol: bool, no_autorizado: bool,
                   mostrar_componentes: bool = False):
        self.solo_microsoft = microsoft
        self.solo_otros = otros
        self.solo_antiguos = antiguos
        self.solo_eol = eol
        self.solo_no_autorizado = no_autorizado
        self.mostrar_componentes = mostrar_componentes
        self.invalidateFilter()


class ScheduledTaskTableModel(QAbstractTableModel):
    HEADERS = ["Nombre", "Estado", "Próxima Ejecución", "Última Ejecución", "Último Resultado", "Riesgo"]

    def __init__(self, rows: List[ScheduledTaskRow] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0: return row.nombre.strip()
            if col == 1: return row.estado.strip()
            if col == 2: return row.proxima_ejecucion.strip()
            if col == 3: return row.ultima_ejecucion.strip()
            if col == 4: return row.ultimo_resultado.strip()
            if col == 5: return "⚠️ Sospechosa" if row.es_sospechosa else "✅ Normal"

        # Color coding for Estado column
        if role == Qt.BackgroundRole and col == 1:
            estado = row.estado.strip().lower()
            if "lista" in estado or "ready" in estado:
                return QColor("#d4edda")  # Verde
            elif "deshabilitada" in estado or "disabled" in estado:
                return QColor("#f8d7da")  # Rojo
            elif "ejecutando" in estado or "running" in estado:
                return QColor("#fff3cd")  # Amarillo

        # Color coding for Último Resultado column — texto traducido
        # ("Exitoso"/"Error: N"/"Ejecutándose"/...), no un código numérico
        # crudo (antes comparaba contra "0", dejó de colorear nada en
        # silencio en cuanto se tradujo el campo a texto).
        if role == Qt.BackgroundRole and col == 4:
            resultado = row.ultimo_resultado.strip().lower()
            if resultado == "exitoso":
                return QColor("#d4edda")  # Verde (éxito)
            elif resultado.startswith("error"):
                return QColor("#f8d7da")  # Rojo (error real)
            elif resultado in ("ejecutándose", "terminada por el usuario"):
                return QColor("#fff3cd")  # Amarillo (informativo, no error)

        # Color coding para la columna Riesgo (tareas ocultas / SYSTEM sin autor)
        if role == Qt.BackgroundRole and col == 5 and row.es_sospechosa:
            return QColor("#f8d7da")
        if role == Qt.ForegroundRole and col == 5:
            return QColor("#c0392b") if row.es_sospechosa else QColor("#1e8449")

        if role == Qt.TextAlignmentRole:
            if col in (1, 2, 3, 4, 5):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        else:
            return section + 1

    def setRows(self, rows: List[ScheduledTaskRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rows(self) -> List[ScheduledTaskRow]:
        return self._rows


class ScheduledTaskFilterProxy(_FechaAwareSortProxy):
    """Proxy de Tareas Programadas: filtro de texto + 'Solo sospechosas'
    (row.es_sospechosa, basado en la acción real) + 'Mostrar deshabilitadas'
    (ocultas por defecto: son mayoría en cualquier equipo real y rara vez
    interesan, pero siguen disponibles — no se descartan en el origen de
    datos, solo en la vista)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.solo_sospechosas = False
        self.mostrar_deshabilitadas = False

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        if not model or source_row >= model.rowCount():
            return True
        row = model.rows()[source_row]

        if self.solo_sospechosas and not row.es_sospechosa:
            return False

        if not self.mostrar_deshabilitadas and "deshabilitada" in (row.estado or "").strip().lower():
            return False

        return super().filterAcceptsRow(source_row, source_parent)

    def setFiltros(self, solo_sospechosas: bool, mostrar_deshabilitadas: bool):
        self.solo_sospechosas = solo_sospechosas
        self.mostrar_deshabilitadas = mostrar_deshabilitadas
        self.invalidateFilter()


class PrinterTableModel(QAbstractTableModel):
    HEADERS = ["Nombre", "Tipo", "Puerto", "Estado", "Compartida", "Predeterminada"]

    def __init__(self, rows: List[PrinterRow] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0: return row.nombre.strip()
            if col == 1: return row.tipo.strip()
            if col == 2: return row.puerto.strip()
            if col == 3: return row.estado.strip()
            if col == 4: return row.compartida.strip()
            if col == 5: return row.predeterminada.strip()

        # Color coding for Estado column
        if role == Qt.BackgroundRole and col == 3:
            estado = row.estado.strip().lower()
            if "lista" in estado or "ready" in estado or "normal" in estado:
                return QColor("#d4edda")  # Verde
            elif "error" in estado or "offline" in estado:
                return QColor("#f8d7da")  # Rojo

        # Color coding for Compartida and Predeterminada columns
        if role == Qt.BackgroundRole and col in (4, 5):
            valor = row.compartida.strip().lower() if col == 4 else row.predeterminada.strip().lower()
            if "si" in valor or "sí" in valor or "yes" in valor or "true" in valor:
                return QColor("#d4edda")  # Verde

        if role == Qt.TextAlignmentRole:
            if col in (3, 4, 5):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        else:
            return section + 1

    def setRows(self, rows: List[PrinterRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rows(self) -> List[PrinterRow]:
        return self._rows

class FirewallRuleTableModel(QAbstractTableModel):
    HEADERS = ["Nombre", "Dirección", "Acción", "Protocolo", "Puerto", "Programa", "Perfil", "Habilitada", "Riesgo"]
    COL_HABILITADA, COL_RIESGO = 7, 8

    def __init__(self, rows: List[FirewallRuleRow] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0: return row.nombre.strip()
            if col == 1: return row.direccion.strip()
            if col == 2: return row.accion.strip()
            if col == 3: return row.protocolo.strip()
            if col == 4: return row.puerto.strip()
            if col == 5: return row.programa.strip()
            if col == 6: return row.perfil_es.strip()
            if col == self.COL_HABILITADA: return row.habilitada.strip()
            if col == self.COL_RIESGO: return "⚠️ Amplia (Any/Any)" if row.es_regla_amplia else ""

        # Color coding for Dirección column
        if role == Qt.BackgroundRole and col == 1:
            direccion = row.direccion.strip().lower()
            if "entrada" in direccion or "inbound" in direccion:
                return QColor("#fff3cd")  # Amarillo
            elif "salida" in direccion or "outbound" in direccion:
                return QColor("#d1ecf1")  # Azul claro

        # Color coding for Acción column
        if role == Qt.BackgroundRole and col == 2:
            accion = row.accion.strip().lower()
            if "permitir" in accion or "allow" in accion:
                return QColor("#d4edda")  # Verde
            elif "bloquear" in accion or "block" in accion or "denegar" in accion:
                return QColor("#f8d7da")  # Rojo

        # Habilitada: una regla deshabilitada no representa ningún riesgo
        # activo ni permiso real — se atenúa en vez de resaltarla.
        if role == Qt.BackgroundRole and col == self.COL_HABILITADA:
            habilitada = row.habilitada.strip().lower()
            if habilitada in ("no", "false"):
                return QColor("#e9ecef")

        # Regla amplia (entrada + permitir + sin restricción de puerto/protocolo)
        if role == Qt.BackgroundRole and col == self.COL_RIESGO and row.es_regla_amplia:
            return QColor("#f8d7da")
        if role == Qt.ForegroundRole and col == self.COL_RIESGO and row.es_regla_amplia:
            return QColor("#c0392b")

        if role == Qt.TextAlignmentRole:
            # Programa (col 5) es una ruta de archivo — se deja a la
            # izquierda como Nombre, no centrada como el resto de columnas
            # cortas de valor fijo.
            if col in (1, 2, 3, 4, 6, self.COL_HABILITADA, self.COL_RIESGO):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        else:
            return section + 1

    def setRows(self, rows: List[FirewallRuleRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rows(self) -> List[FirewallRuleRow]:
        return self._rows

class FirewallFilterProxy(_FechaAwareSortProxy):
    """Filtra reglas de firewall por dirección, acción, estado habilitada
    y origen (sistema/Windows vs terceros), además del texto libre ya
    heredado."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._show_entrada = True
        self._show_salida = True
        self._show_permitir = True
        self._show_bloquear = True
        self._show_habilitadas = True
        self._show_deshabilitadas = True
        self._show_sistema = True
        self._show_terceros = True

    def set_filtros(self, entrada, salida, permitir, bloquear, habilitadas, deshabilitadas,
                     sistema=True, terceros=True):
        self._show_entrada = entrada
        self._show_salida = salida
        self._show_permitir = permitir
        self._show_bloquear = bloquear
        self._show_habilitadas = habilitadas
        self._show_deshabilitadas = deshabilitadas
        self._show_sistema = sistema
        self._show_terceros = terceros
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        if not super().filterAcceptsRow(source_row, source_parent):
            return False
        row = self.sourceModel().rows()[source_row]
        if row.direccion == "Entrada" and not self._show_entrada:
            return False
        if row.direccion == "Salida" and not self._show_salida:
            return False
        if row.accion == "Permitir" and not self._show_permitir:
            return False
        if row.accion == "Bloquear" and not self._show_bloquear:
            return False
        habilitada = row.habilitada.strip().lower() in ("sí", "si", "true")
        if habilitada and not self._show_habilitadas:
            return False
        if not habilitada and not self._show_deshabilitadas:
            return False
        if row.es_sistema and not self._show_sistema:
            return False
        if not row.es_sistema and not self._show_terceros:
            return False
        return True

# REMOVED - Browser Passwords
# class BrowserPasswordTableModel(QAbstractTableModel):
#     HEADERS = ["Navegador", "URL", "Usuario", "Contraseña", "Nota"]
#
#     def __init__(self, rows: List[BrowserPasswordRow] = None, parent=None):
#         super().__init__(parent)
#         self._rows = rows or []
#
#     def rowCount(self, parent=QModelIndex()):
#         return len(self._rows)
#
#     def columnCount(self, parent=QModelIndex()):
#         return len(self.HEADERS)
#
#     def data(self, index: QModelIndex, role=Qt.DisplayRole):
#         if not index.isValid():
#             return QVariant()
#         row = self._rows[index.row()]
#         col = index.column()
#
#         if role in (Qt.DisplayRole, Qt.EditRole):
#             if col == 0: return row.navegador.strip()
#             if col == 1: return row.url.strip()
#             if col == 2: return row.usuario.strip()
#             if col == 3: return row.contraseña.strip()
#             if col == 4: return row.nota.strip()
#
#         # Color coding for Contraseña column
#         if role == Qt.BackgroundRole and col == 3:
#             password = row.contraseña.strip()
#             if password and password != "N/A" and password != "":
#                 return QColor("#fff3cd")  # Amarillo (tiene contraseña)
#
#         if role == Qt.TextAlignmentRole:
#             if col == 0:
#                 return Qt.AlignCenter
#             return Qt.AlignLeft | Qt.AlignVCenter
#
#         return QVariant()
#
#     def headerData(self, section, orientation, role=Qt.DisplayRole):
#         if role != Qt.DisplayRole:
#             return QVariant()
#         if orientation == Qt.Horizontal:
#             return self.HEADERS[section]
#         else:
#             return section + 1
#
#     def setRows(self, rows: List[BrowserPasswordRow]):
#         self.beginResetModel()
#         self._rows = rows
#         self.endResetModel()
#
#     def rows(self) -> List[BrowserPasswordRow]:
#         return self._rows

class EmailAccountTableModel(QAbstractTableModel):
    HEADERS = ["Cliente", "Cuenta", "Servidor", "Tipo"]

    def __init__(self, rows: List[EmailAccountRow] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0: return row.cliente.strip()
            if col == 1: return row.cuenta.strip()
            if col == 2: return row.servidor.strip()
            if col == 3: return row.tipo.strip()

        # Color coding for Tipo column
        if role == Qt.BackgroundRole and col == 3:
            tipo = row.tipo.strip().lower()
            if "imap" in tipo:
                return QColor("#d4edda")  # Verde
            elif "pop" in tipo:
                return QColor("#fff3cd")  # Amarillo
            elif "exchange" in tipo:
                return QColor("#d1ecf1")  # Azul

        if role == Qt.TextAlignmentRole:
            if col in (0, 3):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        else:
            return section + 1

    def setRows(self, rows: List[EmailAccountRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rows(self) -> List[EmailAccountRow]:
        return self._rows


class EmailAccountFilterProxy(_FechaAwareSortProxy):
    """Proxy persistente de Cuentas de Correo — filtro por tipo de cuenta
    (Exchange / IMAP / POP3 / Otros), mismo patrón que CertificateFilterProxy:
    setFilters(...) + invalidateFilter(), nunca recrear el proxy."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.show_exchange = True
        self.show_imap = True
        self.show_pop3 = True
        self.show_otros = True

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        if not model or source_row >= model.rowCount():
            return True

        row = model.rows()[source_row]
        tipo_lower = (row.tipo or "").lower()

        if "exchange" in tipo_lower:
            if not self.show_exchange:
                return False
        elif "pop3" in tipo_lower or "pop" in tipo_lower:
            if not self.show_pop3:
                return False
        elif "imap" in tipo_lower:
            if not self.show_imap:
                return False
        else:
            if not self.show_otros:
                return False

        return super().filterAcceptsRow(source_row, source_parent)

    def setFilters(self, show_exchange: bool, show_imap: bool, show_pop3: bool, show_otros: bool):
        self.show_exchange = show_exchange
        self.show_imap = show_imap
        self.show_pop3 = show_pop3
        self.show_otros = show_otros
        self.invalidateFilter()


class BrowserCloudAccountTableModel(QAbstractTableModel):
    """Mismas 4 columnas que EmailAccountTableModel (Cliente/Cuenta/
    Servidor/Tipo) — misma forma de fila, pero para cuentas de navegador
    (Firefox/Chrome/Edge) y de nube (OneDrive), no de correo."""
    HEADERS = ["Cliente", "Cuenta", "Servidor", "Tipo"]

    def __init__(self, rows: List[BrowserCloudAccountRow] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0: return row.cliente.strip()
            if col == 1: return row.cuenta.strip()
            if col == 2: return row.servidor.strip()
            if col == 3: return row.tipo.strip()

        # Color coding for Tipo column
        if role == Qt.BackgroundRole and col == 3:
            tipo = row.tipo.strip().lower()
            if "onedrive" in tipo:
                return QColor("#cfe2ff")  # Azul
            elif "sincronizado" in tipo or "google account" in tipo:
                return QColor("#d4edda")  # Verde
            elif "autofill" in tipo or "autocompletado" in row.servidor.strip().lower():
                return QColor("#fff3cd")  # Amarillo

        if role == Qt.TextAlignmentRole:
            if col in (0, 3):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        else:
            return section + 1

    def setRows(self, rows: List[BrowserCloudAccountRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rows(self) -> List[BrowserCloudAccountRow]:
        return self._rows


class NavegadoresFilterProxy(_FechaAwareSortProxy):
    """Proxy persistente de la tabla "Navegadores" (Firefox/Chrome/Edge —
    la nube vive en su propia tabla/modelo separado dentro de
    NavegadoresNubeMixin). Filtro por cliente, mismo patrón que
    CertificateFilterProxy: setFilters(...) + invalidateFilter(), nunca
    recrear el proxy."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.show_firefox = True
        self.show_chromium = True

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        if not model or source_row >= model.rowCount():
            return True

        row = model.rows()[source_row]
        cliente_lower = (row.cliente or "").lower()

        if "firefox" in cliente_lower:
            if not self.show_firefox:
                return False
        elif "chrome" in cliente_lower or "edge" in cliente_lower:
            if not self.show_chromium:
                return False

        return super().filterAcceptsRow(source_row, source_parent)

    def setFilters(self, show_firefox: bool, show_chromium: bool):
        self.show_firefox = show_firefox
        self.show_chromium = show_chromium
        self.invalidateFilter()


class NubeFilterProxy(_FechaAwareSortProxy):
    """Proxy persistente de la tabla "Nube" — filtro por proveedor
    (OneDrive / Dropbox / Google Drive / Otros), mismo patrón que
    NavegadoresFilterProxy."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.show_onedrive = True
        self.show_dropbox = True
        self.show_google_drive = True
        self.show_otros = True

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        if not model or source_row >= model.rowCount():
            return True

        row = model.rows()[source_row]
        cliente_lower = (row.cliente or "").lower()

        if "onedrive" in cliente_lower:
            if not self.show_onedrive:
                return False
        elif "dropbox" in cliente_lower:
            if not self.show_dropbox:
                return False
        elif "google drive" in cliente_lower or "drive" in cliente_lower:
            if not self.show_google_drive:
                return False
        else:
            if not self.show_otros:
                return False

        return super().filterAcceptsRow(source_row, source_parent)

    def setFilters(self, show_onedrive: bool, show_dropbox: bool, show_google_drive: bool, show_otros: bool):
        self.show_onedrive = show_onedrive
        self.show_dropbox = show_dropbox
        self.show_google_drive = show_google_drive
        self.show_otros = show_otros
        self.invalidateFilter()


class ActiveSessionTableModel(QAbstractTableModel):
    HEADERS = ["Usuario", "Sesión", "ID", "Estado", "Inactividad", "Inicio Sesión", "Tipo", "Origen (IP)"]
    def __init__(self, rows=None, parent=None):
        super().__init__(parent)
        self._rows = rows or []
    def rowCount(self, parent=QModelIndex()): return len(self._rows)
    def columnCount(self, parent=QModelIndex()): return len(self.HEADERS)
    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid(): return QVariant()
        row, col = self._rows[index.row()], index.column()
        if role in (Qt.DisplayRole, Qt.EditRole):
            return [row.usuario, row.sesion, row.id, row.estado,
                    row.tiempo_inactivo, row.inicio, row.tipo, row.origen_ip or ""][col]
        if role == Qt.BackgroundRole and col == 6:
            t = row.tipo.lower()
            if "rdp" in t: return QColor("#cfe2ff")
            if "red" in t: return QColor("#fff3cd")
            if "inter" in t: return QColor("#d4edda")
        if role == Qt.BackgroundRole and col == 3:
            e = row.estado.lower()
            if "desc" in e or "disc" in e: return QColor("#f8d7da")  # desconectada sin cerrar
        if role == Qt.ForegroundRole and col == 3:
            e = row.estado.lower()
            if "desc" in e or "disc" in e: return QColor("#58151c")
        if role == Qt.TextAlignmentRole:
            return Qt.AlignCenter if col in (2,3,6) else Qt.AlignLeft | Qt.AlignVCenter
        return QVariant()
    def headerData(self, s, o, role=Qt.DisplayRole):
        return self.HEADERS[s] if role == Qt.DisplayRole and o == Qt.Horizontal else (s+1 if o == Qt.Vertical and role == Qt.DisplayRole else QVariant())
    def setRows(self, rows): self.beginResetModel(); self._rows = rows; self.endResetModel()
    def rows(self): return self._rows


class RDPConnectionHistoryTableModel(QAbstractTableModel):
    HEADERS = ["Usuario", "Dominio", "IP de origen", "Fecha"]
    def __init__(self, rows=None, parent=None):
        super().__init__(parent)
        self._rows = rows or []
    def rowCount(self, parent=QModelIndex()): return len(self._rows)
    def columnCount(self, parent=QModelIndex()): return len(self.HEADERS)
    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid(): return QVariant()
        row, col = self._rows[index.row()], index.column()
        if role in (Qt.DisplayRole, Qt.EditRole):
            return [row.usuario, row.dominio, row.ip_origen, row.fecha][col]
        if role == Qt.TextAlignmentRole:
            return Qt.AlignCenter if col == 2 else Qt.AlignLeft | Qt.AlignVCenter
        return QVariant()
    def headerData(self, s, o, role=Qt.DisplayRole):
        return self.HEADERS[s] if role == Qt.DisplayRole and o == Qt.Horizontal else (s+1 if o == Qt.Vertical and role == Qt.DisplayRole else QVariant())
    def setRows(self, rows): self.beginResetModel(); self._rows = rows; self.endResetModel()
    def rows(self): return self._rows


class ActiveSessionFilterProxy(_FechaAwareSortProxy):
    """Proxy persistente de Sesiones Activas — filtro por tipo (Interactiva/
    RDP/Red), mismo patrón que CertificateFilterProxy: setFilters(...) +
    invalidateFilter(), nunca recrear el proxy."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.show_interactiva = True
        self.show_rdp = True
        self.show_red = True

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        if not model or source_row >= model.rowCount():
            return True

        row = model.rows()[source_row]
        tipo_lower = (row.tipo or "").lower()

        if "rdp" in tipo_lower:
            if not self.show_rdp:
                return False
        elif "red" in tipo_lower:
            if not self.show_red:
                return False
        elif "inter" in tipo_lower:
            if not self.show_interactiva:
                return False

        return super().filterAcceptsRow(source_row, source_parent)

    def setFilters(self, show_interactiva: bool, show_rdp: bool, show_red: bool):
        self.show_interactiva = show_interactiva
        self.show_rdp = show_rdp
        self.show_red = show_red
        self.invalidateFilter()


class BluetoothTableModel(QAbstractTableModel):
    HEADERS = ["Nombre", "Fabricante", "Estado", "Clase", "Instance ID"]
    def __init__(self, rows=None, parent=None):
        super().__init__(parent)
        self._rows = rows or []
    def rowCount(self, parent=QModelIndex()): return len(self._rows)
    def columnCount(self, parent=QModelIndex()): return len(self.HEADERS)
    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid(): return QVariant()
        row, col = self._rows[index.row()], index.column()
        if role in (Qt.DisplayRole, Qt.EditRole):
            return [row.nombre, row.fabricante, row.estado, row.clase, row.instance_id][col]
        if role == Qt.BackgroundRole and col == 2:
            s = row.estado.lower()
            if "ok" in s or "connected" in s: return QColor("#d4edda")
            if "error" in s or "unknown" in s: return QColor("#f8d7da")
        if role == Qt.TextAlignmentRole:
            return Qt.AlignCenter if col == 2 else Qt.AlignLeft | Qt.AlignVCenter
        return QVariant()
    def headerData(self, s, o, role=Qt.DisplayRole):
        return self.HEADERS[s] if role == Qt.DisplayRole and o == Qt.Horizontal else (s+1 if o == Qt.Vertical and role == Qt.DisplayRole else QVariant())
    def setRows(self, rows): self.beginResetModel(); self._rows = rows; self.endResetModel()
    def rows(self): return self._rows

class PSHistoryTableModel(QAbstractTableModel):
    HEADERS = ["#", "Fecha/Hora", "Origen", "Comando", "Riesgo"]

    def __init__(self, rows=None, parent=None):
        super().__init__(parent)
        self._rows = rows or []

    def rowCount(self, parent=QModelIndex()): return len(self._rows)
    def columnCount(self, parent=QModelIndex()): return len(self.HEADERS)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid(): return QVariant()
        row, col = self._rows[index.row()], index.column()
        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0: return row.numero
            if col == 1: return row.fecha_hora
            if col == 2: return row.origen
            if col == 3: return row.comando
            if col == 4: return "⚠️ Sospechoso" if row.es_sospechoso else "✅ Normal"
        if role == Qt.BackgroundRole and col == 4 and row.es_sospechoso:
            return QColor("#f8d7da")
        if role == Qt.ForegroundRole and col == 4:
            return QColor("#c0392b") if row.es_sospechoso else QColor("#1e8449")
        if role == Qt.TextAlignmentRole:
            if col in (0, 1, 2, 4):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter
        return QVariant()

    def headerData(self, s, o, role=Qt.DisplayRole):
        return self.HEADERS[s] if role == Qt.DisplayRole and o == Qt.Horizontal else (s+1 if o == Qt.Vertical and role == Qt.DisplayRole else QVariant())

    def setRows(self, rows): self.beginResetModel(); self._rows = rows; self.endResetModel()
    def rows(self): return self._rows


class PSHistoryFilterProxy(_FechaAwareSortProxy):
    """Proxy de Historial PS: filtro de texto + 'Solo sospechosos'
    (parámetros de bypass/ofuscación) + Origen (Todos/Registro de
    eventos/Historial local) + categorías de TIPO de comando.

    Las categorías de tipo (script local / gestor de paquetes / descarga de
    red / cmdlet nativo) van en OR entre sí — son un 'qué es este comando',
    marcar dos a la vez sirve para ver AMBOS tipos juntos, no para exigir
    que un comando sea las dos cosas a la vez (que casi nunca pasa y dejaría
    la tabla vacía sin motivo). 'Solo sospechosos' y 'Origen' siguen en AND
    con lo anterior: son condiciones aparte, no un tipo más."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.solo_sospechosos = False
        self.origen = "Todos"
        self.tipo_script_local = False
        self.tipo_gestor_paquetes = False
        self.tipo_descarga_red = False
        self.tipo_cmdlet_admin = False

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        if not model or source_row >= model.rowCount():
            return True
        row = model.rows()[source_row]

        if self.solo_sospechosos and not row.es_sospechoso:
            return False

        if self.origen and self.origen != "Todos" and row.origen != self.origen:
            return False

        tipos_activos = (
            self.tipo_script_local, self.tipo_gestor_paquetes,
            self.tipo_descarga_red, self.tipo_cmdlet_admin,
        )
        if any(tipos_activos):
            coincide_tipo = (
                (self.tipo_script_local and row.es_script_local)
                or (self.tipo_gestor_paquetes and row.es_gestor_paquetes)
                or (self.tipo_descarga_red and row.es_descarga_red)
                or (self.tipo_cmdlet_admin and row.es_cmdlet_admin)
            )
            if not coincide_tipo:
                return False

        return super().filterAcceptsRow(source_row, source_parent)

    def setFiltros(self, solo_sospechosos: bool, origen: str,
                    tipo_script_local: bool = False, tipo_gestor_paquetes: bool = False,
                    tipo_descarga_red: bool = False, tipo_cmdlet_admin: bool = False):
        self.solo_sospechosos = solo_sospechosos
        self.origen = origen
        self.tipo_script_local = tipo_script_local
        self.tipo_gestor_paquetes = tipo_gestor_paquetes
        self.tipo_descarga_red = tipo_descarga_red
        self.tipo_cmdlet_admin = tipo_cmdlet_admin
        self.invalidateFilter()

class SystemEventTableModel(QAbstractTableModel):
    HEADERS = ["Fecha", "Nivel", "Log", "Fuente", "ID", "Mensaje"]
    def __init__(self, rows=None, parent=None):
        super().__init__(parent)
        self._rows = rows or []
    def rowCount(self, parent=QModelIndex()): return len(self._rows)
    def columnCount(self, parent=QModelIndex()): return len(self.HEADERS)
    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid(): return QVariant()
        row, col = self._rows[index.row()], index.column()
        if role in (Qt.DisplayRole, Qt.EditRole):
            return [row.fecha, row.nivel, row.log, row.fuente, row.id_evento, row.mensaje][col]
        if role == Qt.BackgroundRole and col == 1:
            n = row.nivel.lower()
            if "critico" in n or "crítico" in n: return QColor("#721c24")
            if "error" in n: return QColor("#f8d7da")
            if "advertencia" in n: return QColor("#fff3cd")
        if role == Qt.ForegroundRole and col == 1:
            n = row.nivel.lower()
            if "critico" in n or "crítico" in n:
                return QColor("white")
            # Error/Advertencia: fondo pastel claro, necesita texto oscuro
            # explícito o en tema oscuro queda casi ilegible (mismo bug ya
            # corregido en Actualizaciones/Controladores).
            if "error" in n or "advertencia" in n:
                return QColor("#212121")
        if role == Qt.TextAlignmentRole:
            return Qt.AlignCenter if col in (0,1,2,4) else Qt.AlignLeft | Qt.AlignVCenter
        return QVariant()
    def headerData(self, s, o, role=Qt.DisplayRole):
        return self.HEADERS[s] if role == Qt.DisplayRole and o == Qt.Horizontal else (s+1 if o == Qt.Vertical and role == Qt.DisplayRole else QVariant())
    def setRows(self, rows): self.beginResetModel(); self._rows = rows; self.endResetModel()
    def rows(self): return self._rows

class RemoteConnectionTableModel(QAbstractTableModel):
    HEADERS = ["Fecha", "Usuario", "IP Origen", "Tipo Acceso", "Resultado", "Workstation", "Proceso", "ID Evento"]

    def __init__(self, rows: List[RemoteConnectionRow] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            vals = [row.fecha, row.usuario, row.ip_origen, row.tipo_acceso,
                    row.resultado, row.workstation, row.proceso, row.id_evento]
            return vals[col].strip() if col < len(vals) else QVariant()

        if role == Qt.BackgroundRole:
            if col == 4:  # Resultado
                r = row.resultado.lower()
                if "exito" in r or "éxito" in r:
                    return QColor("#d4edda")
                elif "fallo" in r:
                    return QColor("#f8d7da")
            if col == 3:  # Tipo Acceso
                t = row.tipo_acceso.lower()
                if "rdp" in t:
                    return QColor("#cfe2ff")
                elif "smb" in t or "red" in t:
                    return QColor("#fff3cd")

        if role == Qt.TextAlignmentRole:
            if col in (0, 4, 7):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        return section + 1

    def setRows(self, rows: List[RemoteConnectionRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rows(self) -> List[RemoteConnectionRow]:
        return self._rows

class StartupProgramTableModel(QAbstractTableModel):
    HEADERS = ["Nombre", "Comando", "Ubicación", "Estado"]

    def __init__(self, rows: List[StartupProgramRow] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0: return row.nombre
            if col == 1: return row.comando
            if col == 2: return row.ubicacion
            if col == 3: return row.estado

        if role == Qt.BackgroundRole and col == 3:
            if row.estado == "Habilitado":
                return QColor("#d4edda")  # Verde
            elif row.estado == "Deshabilitado":
                return QColor("#f8d7da")  # Rojo

        if role == Qt.TextAlignmentRole and col in (2, 3):
            return Qt.AlignCenter

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        return section + 1

    def setRows(self, rows: List[StartupProgramRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rows(self) -> List[StartupProgramRow]:
        return self._rows

class StartupProgramFilterProxy(_FechaAwareSortProxy):
    """Filtro de texto + 'Solo sospechosos' (row.es_sospechosa) + 'Solo
    habilitados' + 'Solo deshabilitados' — todos combinables en AND (los
    dos últimos marcados a la vez dan 0 resultados, es una combinación
    contradictoria y no un caso especial a evitar). Ninguno oculta nada
    por defecto (a diferencia de Tareas Programadas, aquí ver qué está
    deshabilitado es precisamente el propósito de la pestaña)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.solo_sospechosos = False
        self.solo_habilitados = False
        self.solo_deshabilitados = False

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        if not model or source_row >= model.rowCount():
            return True
        row = model.rows()[source_row]

        if self.solo_sospechosos and not row.es_sospechosa:
            return False

        if self.solo_habilitados and row.estado != "Habilitado":
            return False

        if self.solo_deshabilitados and row.estado != "Deshabilitado":
            return False

        return super().filterAcceptsRow(source_row, source_parent)

    def setFiltros(self, solo_sospechosos: bool, solo_habilitados: bool, solo_deshabilitados: bool):
        self.solo_sospechosos = solo_sospechosos
        self.solo_habilitados = solo_habilitados
        self.solo_deshabilitados = solo_deshabilitados
        self.invalidateFilter()

class GroupPolicyTableModel(QAbstractTableModel):
    HEADERS = ["Ámbito", "Nombre", "Estado", "OU / Sitio", "Última actualización"]

    def __init__(self, rows: List[GroupPolicyRow] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0: return row.ambito
            if col == 1: return row.nombre
            if col == 2: return row.estado
            if col == 3: return row.ou
            if col == 4: return row.ultima_actualizacion

        if role == Qt.BackgroundRole and col == 2:
            estado = row.estado.lower()
            if estado == "aplicada":
                return QColor("#d4edda")  # Verde
            elif "denegado" in estado or "deshabilitada" in estado:
                return QColor("#f8d7da")  # Rojo

        if role == Qt.TextAlignmentRole and col in (0, 2, 4):
            return Qt.AlignCenter

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        return section + 1

    def setRows(self, rows: List[GroupPolicyRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rows(self) -> List[GroupPolicyRow]:
        return self._rows

class ADInfoTableModel(QAbstractTableModel):
    HEADERS = ["Tipo", "Nombre", "SAM Account Name", "Habilitado", "Última Conexión", "Distinguished Name"]

    def __init__(self, rows: List[ADInfoRow] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0: return row.tipo.strip()
            if col == 1: return row.nombre.strip()
            if col == 2: return row.sam_account_name.strip()
            if col == 3: return row.habilitado.strip()
            if col == 4: return row.ultima_conexion.strip()
            if col == 5: return row.distinguished_name.strip()

        # Color coding for Tipo column
        if role == Qt.BackgroundRole and col == 0:
            tipo = row.tipo.strip().lower()
            if "usuario" in tipo or "user" in tipo:
                return QColor("#d4edda")  # Verde
            elif "grupo" in tipo or "group" in tipo:
                return QColor("#d1ecf1")  # Azul
            elif "computadora" in tipo or "computer" in tipo:
                return QColor("#fff3cd")  # Amarillo

        # Color coding for Habilitado column
        if role == Qt.BackgroundRole and col == 3:
            habilitado = row.habilitado.strip().lower()
            if "si" in habilitado or "sí" in habilitado or "yes" in habilitado or "true" in habilitado:
                return QColor("#d4edda")  # Verde
            elif "no" in habilitado or "false" in habilitado:
                return QColor("#f8d7da")  # Rojo

        if role == Qt.TextAlignmentRole:
            if col in (0, 3, 4):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        else:
            return section + 1

    def setRows(self, rows: List[ADInfoRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rows(self) -> List[ADInfoRow]:
        return self._rows

class USBDeviceTableModel(QAbstractTableModel):
    HEADERS = ["Nombre", "Fabricante", "Estado", "Instance ID"]

    def __init__(self, rows: List[USBDeviceRow] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0: return row.nombre.strip()
            if col == 1: return row.fabricante.strip()
            if col == 2: return row.estado.strip()
            if col == 3: return row.instance_id.strip()

        if role == Qt.BackgroundRole and col == 2:
            estado = row.estado.strip().lower()
            if "conectado" in estado or "ok" in estado:
                return QColor("#d4edda")  # Verde
            elif "error" in estado:
                return QColor("#f8d7da")  # Rojo

        if role == Qt.TextAlignmentRole:
            if col == 2:
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        else:
            return section + 1

    def setRows(self, rows: List[USBDeviceRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rows(self) -> List[USBDeviceRow]:
        return self._rows

class MonitorTableModel(QAbstractTableModel):
    HEADERS = ["Nombre", "Fabricante", "Serial", "Tipo Conexión"]

    def __init__(self, rows: List[MonitorRow] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0: return row.nombre.strip()
            if col == 1: return row.fabricante.strip()
            if col == 2: return row.serial.strip()
            if col == 3: return row.tipo_conexion.strip()

        if role == Qt.TextAlignmentRole:
            if col == 3:
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        else:
            return section + 1

    def setRows(self, rows: List[MonitorRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rows(self) -> List[MonitorRow]:
        return self._rows

class InstalledUpdateTableModel(QAbstractTableModel):
    HEADERS = ["KB", "Descripción", "Instalación", "Usuario", "Antigüedad"]

    def __init__(self, rows: List[InstalledUpdateRow] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0: return row.kb.strip()
            if col == 1: return row.descripcion.strip()
            if col == 2: return row.instalacion.strip()
            if col == 3: return row.usuario.strip()
            if col == 4:
                dias = row.dias_instalado
                return f"{dias} días" if dias is not None else "N/A"

        if role == Qt.TextAlignmentRole:
            if col in (0, 2, 4):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        else:
            return section + 1

    def setRows(self, rows: List[InstalledUpdateRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rows(self) -> List[InstalledUpdateRow]:
        return self._rows

class PendingUpdateTableModel(QAbstractTableModel):
    HEADERS = ["KB", "Título", "Tamaño", "Criticidad", "Fecha", "Tipo", "Antigüedad"]

    # Igual que EnvVarTableModel: las celdas con BackgroundRole pastel deben
    # fijar también un ForegroundRole oscuro explícito, o en tema oscuro el
    # texto gris claro de la hoja de estilos global es casi ilegible sobre
    # esos fondos claros.
    _DARK_TEXT = QColor("#212121")

    def __init__(self, rows: List[PendingUpdateRow] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0: return row.kb.strip()
            if col == 1: return row.titulo.strip()
            if col == 2: return row.tamano.strip()
            if col == 3: return row.criticidad.strip()
            if col == 4: return row.fecha.strip()
            if col == 5: return row.tipo.strip()
            if col == 6:
                dias = row.dias_pendiente
                return f"{dias} días" if dias is not None else "N/A"

        if role == Qt.TextAlignmentRole:
            if col in (0, 2, 3, 4, 5, 6):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        # Color coding for criticality
        if role == Qt.BackgroundRole and col == 3:
            criticidad = row.criticidad.lower()
            if 'crítica' in criticidad or 'critical' in criticidad:
                return QColor(255, 200, 200)  # Light red
            elif 'importante' in criticidad or 'important' in criticidad:
                return QColor(255, 230, 200)  # Light orange
            elif 'recomendada' in criticidad:
                return QColor(255, 255, 200)  # Light yellow

        if role == Qt.ForegroundRole and col == 3:
            criticidad = row.criticidad.lower()
            if ('crítica' in criticidad or 'critical' in criticidad or 'importante' in criticidad
                    or 'important' in criticidad or 'recomendada' in criticidad):
                return self._DARK_TEXT

        # Resalta antigüedad: pendiente hace mucho + crítica/importante = riesgo alto
        if role == Qt.BackgroundRole and col == 6:
            dias = row.dias_pendiente
            criticidad = row.criticidad.lower()
            es_grave = 'crítica' in criticidad or 'critical' in criticidad or 'importante' in criticidad
            if dias is not None and dias > 30 and es_grave:
                return QColor("#f8d7da")
            elif dias is not None and dias > 30:
                return QColor("#fff3cd")

        if role == Qt.ForegroundRole and col == 6:
            dias = row.dias_pendiente
            if dias is not None and dias > 30:
                return self._DARK_TEXT

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        else:
            return section + 1

    def setRows(self, rows: List[PendingUpdateRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rows(self) -> List[PendingUpdateRow]:
        return self._rows

class DriverTableModel(QAbstractTableModel):
    HEADERS = ["Dispositivo", "Fabricante", "Versión", "Fecha", "Antigüedad", "Clase", "Firmado", "Estado", "Problema", "Proveedor"]
    COL_ANTIGUEDAD, COL_FIRMADO, COL_ESTADO, COL_PROBLEMA = 4, 6, 7, 8

    # Igual que EnvVarTableModel/PendingUpdateTableModel: las celdas con
    # BackgroundRole pastel necesitan también un ForegroundRole oscuro
    # explícito, o en tema oscuro el texto queda casi ilegible.
    _DARK_TEXT = QColor("#212121")

    def __init__(self, rows: List[DriverRow] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0: return row.dispositivo.strip()
            if col == 1: return row.fabricante.strip()
            if col == 2: return row.version.strip()
            if col == 3: return row.fecha.strip()
            if col == self.COL_ANTIGUEDAD:
                dias = row.dias_antiguedad
                return f"{dias} días" if dias is not None else "N/A"
            if col == 5: return row.clase.strip()
            if col == self.COL_FIRMADO: return row.firmado.strip()
            if col == self.COL_ESTADO: return row.estado.strip()
            if col == self.COL_PROBLEMA: return row.problema.strip()
            if col == 9: return row.proveedor.strip()

        if role == Qt.TextAlignmentRole:
            if col in (2, 3, self.COL_ANTIGUEDAD, self.COL_FIRMADO, self.COL_ESTADO):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        # Estado: 'Sin controlador' es lo grave (rojo); 'Instalado' es lo normal.
        if role == Qt.BackgroundRole and col == self.COL_ESTADO:
            estado = row.estado.lower()
            if 'sin controlador' in estado:
                return QColor(255, 200, 200)  # Light red
            elif 'instalado' in estado:
                return QColor(220, 255, 220)  # Light green

        # Problema: cualquier texto no vacío es un aviso real (Windows
        # reporta un ConfigManagerErrorCode != 0 para ese dispositivo).
        if role == Qt.BackgroundRole and col == self.COL_PROBLEMA:
            if row.problema.strip():
                return QColor(255, 200, 200)  # Light red

        # Color coding for Firmado
        if role == Qt.BackgroundRole and col == self.COL_FIRMADO:
            firmado = row.firmado.lower()
            if 'no' in firmado:
                return QColor(255, 230, 200)  # Light orange
            elif 'sí' in firmado or 'si' in firmado:
                return QColor(220, 255, 220)  # Light green

        if role == Qt.ForegroundRole and col in (self.COL_FIRMADO, self.COL_ESTADO, self.COL_PROBLEMA):
            return self._DARK_TEXT

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        else:
            return section + 1

    def setRows(self, rows: List[DriverRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rows(self) -> List[DriverRow]:
        return self._rows

class RegistryResultTableModel(QAbstractTableModel):
    """Tabla de resultados del Registro de Windows: tanto de la búsqueda en
    vivo como del escáner de cadenas rotas/huérfanas (mismo modelo para las
    dos, distinguibles por la columna Tipo). Filas coloreadas en rojo
    (ruta bloqueada, no eliminable desde la app) o naranja (crítica/de
    sistema, eliminable con aviso reforzado) — mismo esquema pastel que
    Controladores/Variables Entorno."""
    HEADERS = ["Tipo", "Hive", "Ruta", "Valor", "Dato", "Tipo Dato"]
    COL_TIPO, COL_HIVE, COL_RUTA, COL_VALOR, COL_DATO, COL_TIPODATO = range(6)

    _DARK_TEXT = QColor("#212121")
    _COLOR_BLOQUEADA = QColor("#f8d7da")   # rojo pastel
    _COLOR_CRITICA = QColor("#ffe5cc")     # naranja pastel
    _COLOR_HUERFANA = QColor("#fff3cd")    # amarillo pastel (aviso, no riesgo de ruta)

    _TIPO_LABELS = {"key": "Clave", "value": "Valor", "huerfana": "Huérfana"}

    def __init__(self, rows: List[RegistryResultRow] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == self.COL_TIPO: return self._TIPO_LABELS.get(row.match_type, row.match_type)
            if col == self.COL_HIVE: return row.hive_abbr
            if col == self.COL_RUTA: return row.subkey or "(raíz)"
            if col == self.COL_VALOR: return row.value_name or "—"
            if col == self.COL_DATO: return row.value_data_mostrado or "—"
            if col == self.COL_TIPODATO: return row.reg_type or "—"

        if role == Qt.TextAlignmentRole:
            if col in (self.COL_TIPO, self.COL_HIVE, self.COL_TIPODATO):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        if role == Qt.ToolTipRole:
            if row.match_type == "huerfana" and row.motivo:
                return row.motivo
            return row.full_path

        if role == Qt.BackgroundRole:
            if row.match_type == "huerfana":
                return self._COLOR_HUERFANA
            riesgo = row.riesgo
            if riesgo == "bloqueada":
                return self._COLOR_BLOQUEADA
            if riesgo == "critica":
                return self._COLOR_CRITICA

        if role == Qt.ForegroundRole:
            if row.match_type == "huerfana" or row.riesgo:
                return self._DARK_TEXT

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        return section + 1

    def setRows(self, rows: List[RegistryResultRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def addRows(self, nuevas: List[RegistryResultRow]):
        """Añade filas sin resetear el modelo entero — usado durante el
        streaming de resultados en vivo del motor de búsqueda."""
        if not nuevas:
            return
        primero = len(self._rows)
        self.beginInsertRows(QModelIndex(), primero, primero + len(nuevas) - 1)
        self._rows.extend(nuevas)
        self.endInsertRows()

    def rows(self) -> List[RegistryResultRow]:
        return self._rows


class RegistryResultProxy(_FechaAwareSortProxy):
    """Proxy de ordenación para la tabla de resultados del Registro. Sin
    filtro propio (la 'búsqueda' real ya la hace el motor sobre el
    registro, no sobre filas ya mostradas) — solo ordenación por columnas,
    heredada de _FechaAwareSortProxy."""
    pass


class UserTableModel(QAbstractTableModel):
    HEADERS = ["Nombre", "Nombre Completo", "Tipo", "Dominio", "Estado", "Última Conexión",
               "Fecha Creación", "Descripción", "Grupos", "SID", "Password Expira", "Password Requerida",
               "Cuenta Vinculada"]

    def __init__(self, rows: List[UserRow] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0: return row.nombre.strip()
            if col == 1: return row.nombre_completo.strip()
            if col == 2: return row.tipo.strip()
            if col == 3: return row.dominio.strip()
            if col == 4: return row.estado.strip()
            if col == 5: return row.ultima_conexion.strip()
            if col == 6: return row.fecha_creacion.strip()
            if col == 7: return row.descripcion.strip()
            if col == 8: return row.grupos.strip()
            if col == 9: return row.sid.strip()
            if col == 10: return row.password_expires.strip()
            if col == 11: return row.password_required.strip()
            if col == 12: return row.cuenta_vinculada.strip()

        if role == Qt.TextAlignmentRole:
            if col in (2, 4, 10, 11):  # Tipo, Estado, Password Expira, Password Requerida
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        # Color coding for Tipo column
        if role == Qt.BackgroundRole and col == 2:
            tipo = row.tipo.strip().lower()
            if 'sistema' in tipo:
                return QColor("#fff3cd")  # Amarillo
            elif 'entra' in tipo:
                return QColor("#e0d4fd")  # Violeta (Entra ID / Azure AD)
            elif 'dominio' in tipo:
                return QColor("#cfe2ff")  # Azul
            elif 'local' in tipo:
                return QColor("#d1e7dd")  # Verde

        # Color coding for Estado column
        if role == Qt.BackgroundRole and col == 4:
            estado = row.estado.strip().lower()
            if 'inactivo' in estado:
                return QColor("#f8d7da")  # Rojo
            elif 'activo' in estado:
                return QColor("#d4edda")  # Verde

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        else:
            return section + 1

    def setRows(self, rows: List[UserRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rows(self) -> List[UserRow]:
        return self._rows

class ContentTableModel(QAbstractTableModel):
    """Resultados de la Búsqueda avanzada de Contenido (archivos/carpetas
    individuales localizados por ContentSearchEngine, msys/collectors.py) —
    filas como dict {nombre, ruta, directorio, tamano, tamano_fmt,
    modificado, modificado_fmt, tipo}, no un dataclass fijo, porque se
    reciben por lotes en pyqtSignal(list) mientras la búsqueda progresa."""

    HEADERS = ["Tipo", "Nombre", "Directorio", "Tamaño", "Modificado"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._filas: List[Dict[str, Any]] = []

    def rowCount(self, parent=QModelIndex()):
        return len(self._filas)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        fila = self._filas[index.row()]
        col = index.column()

        if role == Qt.DisplayRole:
            if col == 0: return "Carpeta" if fila["tipo"] == "folder" else "Archivo"
            if col == 1: return fila["nombre"]
            if col == 2: return fila["directorio"]
            if col == 3: return fila["tamano_fmt"]
            if col == 4: return fila["modificado_fmt"]

        if role == Qt.TextAlignmentRole:
            if col == 3:
                return Qt.AlignRight | Qt.AlignVCenter
            if col == 0:
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        if role == Qt.BackgroundRole and col == 0:
            return QColor("#f0f0f0") if fila["tipo"] == "folder" else QColor("#e7f3ff")

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        return section + 1

    def filaEn(self, row: int) -> Dict[str, Any]:
        return self._filas[row]

    def agregarFilas(self, nuevas: List[Dict[str, Any]]):
        if not nuevas:
            return
        inicio = len(self._filas)
        self.beginInsertRows(QModelIndex(), inicio, inicio + len(nuevas) - 1)
        self._filas.extend(nuevas)
        self.endInsertRows()

    def limpiar(self):
        self.beginResetModel()
        self._filas = []
        self.endResetModel()

    def quitarPorRuta(self, rutas: set):
        self.beginResetModel()
        self._filas = [f for f in self._filas if f["ruta"] not in rutas]
        self.endResetModel()

    def todas(self) -> List[Dict[str, Any]]:
        return list(self._filas)

class NetworkAdapterTableModel(QAbstractTableModel):
    HEADERS = ["Nombre", "Descripción", "Estado", "Tipo", "IP", "MAC", "Configuración IP", "Puerta de Enlace", "DNS", "IPv6", "Velocidad"]

    def __init__(self, rows: List[NetworkAdapterRow] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0: return row.nombre.strip()
            if col == 1: return row.descripcion.strip()
            if col == 2: return row.estado.strip()
            if col == 3: return row.tipo.strip()
            if col == 4: return row.ip.strip()
            if col == 5: return row.mac.strip()
            if col == 6: return row.configuracion_ip.strip()
            if col == 7: return row.gateway.strip()
            if col == 8: return row.dns.strip()
            if col == 9: return row.ipv6.strip()
            if col == 10: return row.velocidad.strip()

        if role == Qt.TextAlignmentRole:
            if col in (2, 3, 6):  # Estado, Tipo, Configuración IP
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        # Color coding for Estado column
        # Ojo: comprobar 'inactivo'/'deshabilitado' ANTES que 'activo', porque
        # 'activo' in 'inactivo' da True (subcadena) y pintaría en verde los
        # adaptadores inactivos si se comprobara 'activo' primero.
        if role == Qt.BackgroundRole and col == 2:
            estado = row.estado.strip().lower()
            if 'inactivo' in estado:
                return QColor("#f8d7da")  # Rojo claro
            elif 'deshabilitado' in estado:
                return QColor("#e2e3e5")  # Gris
            elif 'activo' in estado:
                return QColor("#d4edda")  # Verde

        # Color coding for Tipo column
        if role == Qt.BackgroundRole and col == 3:
            tipo = row.tipo.strip().lower()
            if 'ethernet' in tipo:
                return QColor("#cfe2ff")  # Azul claro
            elif 'wifi' in tipo or 'wi-fi' in tipo:
                return QColor("#d1ecf1")  # Cyan claro
            elif 'vpn' in tipo or 'túnel' in tipo:
                return QColor("#fff3cd")  # Amarillo claro
            elif 'virtual' in tipo:
                return QColor("#e7e7e7")  # Gris claro

        # Color coding for Configuración IP column
        if role == Qt.BackgroundRole and col == 6:
            config = row.configuracion_ip.strip().lower()
            if 'dhcp' in config:
                return QColor("#d1f2eb")  # Verde agua
            elif 'fija' in config:
                return QColor("#ffeaa7")  # Amarillo suave

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        else:
            return section + 1

    def setRows(self, rows: List[NetworkAdapterRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rows(self) -> List[NetworkAdapterRow]:
        return self._rows

class DiscoFisicoTableModel(QAbstractTableModel):
    HEADERS = ["Disco", "Modelo", "Tipo", "Bus", "Tamaño", "Número de Serie",
               "Tipo de Medio", "Estado de Salud", "Estado Operativo",
               "Temperatura", "Desgaste (SSD)", "Horas Encendido", "Errores"]
    COL_ESTADO_SALUD = 7
    COL_ERRORES = 12

    def __init__(self, rows: List[DiscoFisicoRow] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0: return row.disco.strip()
            if col == 1: return row.modelo.strip()
            if col == 2: return row.tipo.strip()
            if col == 3: return row.bus.strip()
            if col == 4: return row.tamano.strip()
            if col == 5: return row.serial.strip()
            if col == 6: return row.tipo_medio.strip()
            if col == self.COL_ESTADO_SALUD: return row.estado_salud.strip()
            if col == 8: return row.estado_operativo.strip()
            if col == 9: return row.temperatura.strip()
            if col == 10: return row.desgaste.strip()
            if col == 11: return row.horas_encendido.strip()
            if col == self.COL_ERRORES: return row.errores.strip()

        if role == Qt.BackgroundRole and col == self.COL_ESTADO_SALUD:
            estado = row.estado_salud.strip().lower()
            if "healthy" in estado or "correcto" in estado:
                return QColor("#d4edda")
            elif "warning" in estado or "advertencia" in estado:
                return QColor("#fff3cd")
            elif estado and estado != "n/a":
                return QColor("#f8d7da")

        if role == Qt.BackgroundRole and col == self.COL_ERRORES:
            # "N/A" (proveedor sin telemetría) y "0" (sin errores) se dejan
            # sin colorear; solo se resalta si hay errores sin corregir de
            # verdad — mismo criterio de "no alarmar sin datos reales" que
            # ya se aplicó al ocultar botones de Soporte sin nada detectado.
            errores = row.errores.strip().lower()
            if errores not in ("n/a", "0", ""):
                return QColor("#f8d7da")

        if role == Qt.TextAlignmentRole:
            if col in (2, 3, 4, 6, self.COL_ESTADO_SALUD, 8, 9, 10, 11, self.COL_ERRORES):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        return section + 1

    def setRows(self, rows: List[DiscoFisicoRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rows(self) -> List[DiscoFisicoRow]:
        return self._rows

class PartitionTableModel(QAbstractTableModel):
    HEADERS = ["Disco", "Letra", "Etiqueta", "Sistema de Archivos", "Tamaño", "Usado", "Libre", "% Uso"]

    def __init__(self, rows: List[PartitionRow] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0: return row.disco.strip()
            if col == 1: return row.letra.strip()
            if col == 2: return row.etiqueta.strip()
            if col == 3: return row.sistema.strip()
            if col == 4: return row.tamano.strip()
            if col == 5: return row.usado.strip()
            if col == 6: return row.libre.strip()
            if col == 7: return row.porcentaje.strip()

        if role == Qt.BackgroundRole and col == 7:
            # Mismo criterio de semáforo que Estado de Salud en Discos: un
            # disco con estado 'Correcto' no avisa de que una PARTICIÓN
            # concreta esté casi llena, así que necesita su propio color.
            try:
                valor = float(row.porcentaje.strip().rstrip("%").replace(",", "."))
            except ValueError:
                valor = None
            if valor is not None:
                if valor >= 90:
                    return QColor("#f8d7da")
                elif valor >= 75:
                    return QColor("#fff3cd")

        if role == Qt.TextAlignmentRole:
            if col in (0, 1, 3, 4, 5, 6, 7):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        else:
            return section + 1

    def setRows(self, rows: List[PartitionRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rows(self) -> List[PartitionRow]:
        return self._rows

class DiscoColumnFilterProxy(_FechaAwareSortProxy):
    """Añade un filtro exacto por disco físico (columna 'Disco', presente
    tanto en DiscoFisicoRow como en PartitionRow) encima del texto libre ya
    heredado — usado por el combo 'Disco' de Discos Duros, que filtra a la
    vez la tabla de Discos y la de Particiones con la misma selección."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._disco_filtro = ""

    def set_disco_filtro(self, disco: str):
        self._disco_filtro = disco
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        if not super().filterAcceptsRow(source_row, source_parent):
            return False
        if not self._disco_filtro:
            return True
        return self.sourceModel().rows()[source_row].disco.strip() == self._disco_filtro

class GPUTableModel(QAbstractTableModel):
    HEADERS = ["Nombre", "VRAM", "Driver", "Resolución"]

    def __init__(self, rows: List[GPURow] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0: return row.nombre.strip()
            if col == 1: return row.ram.strip()
            if col == 2: return row.driver.strip()
            if col == 3: return row.resolucion.strip()

        if role == Qt.TextAlignmentRole:
            if col in (1, 2, 3):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        else:
            return section + 1

    def setRows(self, rows: List[GPURow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rows(self) -> List[GPURow]:
        return self._rows

class PackageUpgradeTableModel(QAbstractTableModel):
    HEADERS = ["Estado", "Nombre", "Id", "Versión Actual", "Versión Disponible", "Origen"]

    def __init__(self, rows: List[PackageUpgradeRow] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0: return "🔒 Bloqueada" if row.bloqueado else "🔓 Disponible"
            if col == 1: return row.nombre.strip()
            if col == 2: return row.id_paquete.strip()
            if col == 3: return row.version_actual.strip()
            if col == 4: return row.version_disponible.strip()
            if col == 5: return row.origen.strip()

        if role == Qt.TextAlignmentRole:
            if col in (0, 3, 4, 5):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        if role == Qt.ToolTipRole and col == 0 and row.bloqueado and row.motivo_bloqueo:
            return f"Motivo: {row.motivo_bloqueo}"

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        else:
            return section + 1

    def setRows(self, rows: List[PackageUpgradeRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rows(self) -> List[PackageUpgradeRow]:
        return self._rows


class PackageFilterProxy(_FechaAwareSortProxy):
    """Proxy de Gestor de Paquetes: filtro de texto (heredado) + 'Ocultar
    bloqueadas' — mismo patrón que ScheduledTaskFilterProxy (checkbox
    independiente, no combo). Bloqueadas se MUESTRAN por defecto (con su
    icono 🔒) porque saber qué está bloqueado es justo el punto de la
    columna Estado; el checkbox es para cuando el técnico solo quiere ver
    qué puede actualizarse ahora mismo."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.ocultar_bloqueadas = False
        self.solo_bloqueadas = False

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        if not model or source_row >= model.rowCount():
            return True
        row = model.rows()[source_row]

        if self.ocultar_bloqueadas and row.bloqueado:
            return False
        if self.solo_bloqueadas and not row.bloqueado:
            return False

        return super().filterAcceptsRow(source_row, source_parent)

    def setFiltros(self, ocultar_bloqueadas: bool, solo_bloqueadas: bool = False):
        self.ocultar_bloqueadas = ocultar_bloqueadas
        self.solo_bloqueadas = solo_bloqueadas
        self.invalidateFilter()

class NetworkDeviceTableModel(QAbstractTableModel):
    HEADERS = ["IP", "MAC", "Fabricante", "Tipo estimado", "Estado", "Interfaz", "Nombre", "Acceso Web"]
    COL_ESTADO, COL_NOMBRE, COL_ACCESO_WEB = 4, 6, 7

    def __init__(self, rows: List[NetworkDeviceRow] = None, parent=None):
        super().__init__(parent)
        self._rows = rows or []

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(self.HEADERS)

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid():
            return QVariant()
        row = self._rows[index.row()]
        col = index.column()

        if role in (Qt.DisplayRole, Qt.EditRole):
            if col == 0: return row.ip.strip()
            if col == 1: return row.mac.strip()
            if col == 2: return row.fabricante.strip()
            if col == 3: return row.tipo_estimado.strip()
            if col == self.COL_ESTADO: return row.estado.strip()
            if col == 5: return row.interfaz.strip()
            if col == self.COL_NOMBRE: return row.hostname.strip()
            if col == self.COL_ACCESO_WEB: return row.acceso_web.strip()

        # Sospechoso tiene prioridad sobre cualquier otro color de fondo —
        # marcado manual del técnico, más importante que el estado ARP.
        if role == Qt.BackgroundRole and row.sospechoso:
            return QColor("#f8d7da")  # Rojo claro, toda la fila

        if role == Qt.BackgroundRole and col == self.COL_ESTADO:
            estado = row.estado.strip().lower()
            if estado == "alcanzable":
                return QColor("#d4edda")  # Verde
            elif estado == "obsoleto":
                return QColor("#fff3cd")  # Amarillo
            elif estado == "permanente":
                return QColor("#d1ecf1")  # Azul claro

        if role == Qt.BackgroundRole and col == self.COL_ACCESO_WEB:
            if row.acceso_web and row.acceso_web not in ("No detectado", ""):
                return QColor("#d1ecf1")  # Azul claro — tiene interfaz web

        if role == Qt.TextAlignmentRole:
            if col in (2, 3, self.COL_ESTADO, 5, self.COL_ACCESO_WEB):
                return Qt.AlignCenter
            return Qt.AlignLeft | Qt.AlignVCenter

        return QVariant()

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return QVariant()
        if orientation == Qt.Horizontal:
            return self.HEADERS[section]
        else:
            return section + 1

    def setRows(self, rows: List[NetworkDeviceRow]):
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()

    def rows(self) -> List[NetworkDeviceRow]:
        return self._rows

class NetworkDeviceFilterProxy(_FechaAwareSortProxy):
    """Filtra dispositivos de red local por Estado (Alcanzable/Obsoleto/
    Permanente/Otros), además del texto libre ya heredado."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._show_reachable = True
        self._show_stale = True
        self._show_permanent = True
        self._show_otros = True
        self._rango_ip = None  # (entero_inicio, entero_fin) o None = sin filtro

    def set_filtros(self, reachable, stale, permanent, otros):
        self._show_reachable = reachable
        self._show_stale = stale
        self._show_permanent = permanent
        self._show_otros = otros
        self.invalidateFilter()

    def set_rango_ip(self, rango):
        """rango: (entero_inicio, entero_fin) ya calculado por la UI
        (ipaddress.IPv4Address convertida a int), o None para quitar el
        filtro."""
        self._rango_ip = rango
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        if not super().filterAcceptsRow(source_row, source_parent):
            return False
        row = self.sourceModel().rows()[source_row]
        estado = row.estado.strip().lower()
        if estado == "alcanzable":
            if not self._show_reachable:
                return False
        elif estado == "obsoleto":
            if not self._show_stale:
                return False
        elif estado == "permanente":
            if not self._show_permanent:
                return False
        elif not self._show_otros:
            return False

        if self._rango_ip is not None:
            try:
                ip_num = int(ipaddress.IPv4Address(row.ip.strip()))
            except (ValueError, ipaddress.AddressValueError):
                return False
            ini, fin = self._rango_ip
            if not (ini <= ip_num <= fin):
                return False

        return True

