# -*- coding: utf-8 -*-
"""Recolectores de datos en segundo plano (QThread) de MASys: uno por
pestana, ejecutan PowerShell/WMI/winreg/psutil/pywin32 sin bloquear la UI y
emiten los resultados via señales Qt. Incluye tambien los dos hilos de
busqueda/escaneo del Registro de Windows y los managers ProcessManager/
ServiceManager (con sus NotesDB de notas de texto libre), que en el
original viven pegados a sus collectors respectivos.
Extraido de multisys.py (Fase 1 de modularizacion)."""

import os
import re
import sys
import time
import ctypes
import shutil
import socket
import hashlib
import json
import sqlite3
import subprocess
import tempfile
import winreg
import zipfile
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Tuple

from PyQt5.QtCore import QThread, pyqtSignal

import win32service
import win32serviceutil
import pywintypes
import psutil
import pythoncom
import win32com.client
import win32api
import win32con
from send2trash import send2trash

from msys import config as msys_config
from msys.config import logger, CHECKPOINT_INTERVAL
from msys.core.utils import (
    run_powershell_json, run_powershell_command, escape_powershell_string,
    run_powershell_json_params, run_powershell_command_params, run_system_command,
    is_smbshare_available, wrap_with_credentials,
    checkpoint_exists, get_checkpoint_info, save_checkpoint, load_checkpoint,
    delete_checkpoint,
    REG_HIVES, REG_TYPES, clasificar_riesgo_registro, format_reg_data,
    _existe_ejecutable,
    traducir_permiso, traducir_tipo, limpiar_destino_credential,
    clasificar_tipo_destino, _sqlite_connect, ruta_destino_sin_colision,
    wifi_escanear_redes_cercanas, _es_reparse_point, _FILE_ATTRIBUTE_REPARSE_POINT,
    startupinfo_oculto, detectar_versiones_outlook_instaladas,
)
from msys.ps_scripts import (
    PS_CHECK_SMBSHARE, PS_GET_SMB_SHARES, PS_GET_SMB_ACCESS, PS_GET_WMI_SHARES,
    PS_GET_ACL_UNC, PS_GET_NETWORK_DRIVES, PS_GET_NETWORK_DRIVES_WMI,
    PS_GET_NETWORK_DRIVES_ENHANCED, PS_GET_CREDENTIALS, PS_GET_WIFI_PASSWORDS,
    PS_GET_CERTIFICATES, PS_GET_ENVIRONMENT_VARS,
    PS_GET_NET_CONNECTIONS, PS_GET_NETWORK_ADAPTERS, PS_GET_INSTALLED_SOFTWARE,
    PS_GET_SCHEDULED_TASKS, PS_GET_PRINTERS, PS_GET_FIREWALL_RULES,
    PS_GET_EMAIL_ACCOUNTS, PS_GET_BROWSER_CLOUD_ACCOUNTS,
    PS_GET_USB_DEVICES, PS_GET_MONITORS, PS_GET_SYSTEM_INFO,
    PS_GET_INSTALLED_UPDATES, PS_GET_REBOOT_PENDING, PS_GET_PENDING_UPDATES,
    PS_GET_DRIVERS, PS_GET_AD_INFO, PS_GET_GROUP_POLICY_INFO, PS_GET_STARTUP_PROGRAMS, PS_GET_USERS, PS_GET_ACTIVE_SESSIONS,
    PS_GET_BLUETOOTH, PS_GET_SYSTEM_EVENTS, PS_GET_SECURITY_INFO,
    PS_GET_DISK_HEALTH, PS_LIMPIEZA_SISTEMA, PS_GET_LICENSE_INFO,
    PS_GET_WINGET_UPGRADES, PS_GET_LOCAL_NETWORK_DEVICES, PS_GET_PS_HISTORY,
    PS_GET_PS_HISTORY_EVENTOS,
    PS_GET_REMOTE_CONNECTIONS, PS_GET_RDP_CONNECTION_HISTORY,
    PS_GET_OUTLOOK_ACCOUNTS, PS_EXPORT_OUTLOOK_ACCOUNT_PST, PS_GET_OUTLOOK_ACCOUNT_DATAFILE,
)
from msys.models.rows import (
    SharePermissionRow, NetworkDriveRow, CredentialRow, WiFiRow, CertificateRow,
    ProcessRow, ServiceRow, NetworkConnectionRow, InstalledSoftwareRow,
    ScheduledTaskRow, PrinterRow, FirewallRuleRow, FirewallProfileRow, EmailAccountRow,
    BrowserCloudAccountRow, ADInfoRow, StartupProgramRow,
    USBDeviceRow, MonitorRow, InstalledUpdateRow, PendingUpdateRow, DriverRow,
    RegistryResultRow, UserRow, NetworkAdapterRow, ActiveSessionRow, RDPConnectionRow,
    BluetoothRow, PSHistoryRow, SystemEventRow, RemoteConnectionRow,
    PartitionRow, DiscoFisicoRow, GPURow, PackageUpgradeRow, NetworkDeviceRow,
    identificar_fabricante_mac, scheduled_task_trigger_desde_ps,
    _PROCESS_PRIORITY_MAP, _PROCESS_PRIORITY_CONST, SqlInstanceRow,
)
from msys.domain.env_vars import EnvVarRow, VarManager
from msys.models.table_models import (
    collect_ssh_keys, collect_git_credentials, collect_rdp_files,
)
from msys.email_providers import test_mail_connection

class RegistrySearchThread(QThread):
    """Motor de búsqueda en el Registro de Windows. winreg puro (no
    PowerShell) en un QThread — recorre los hives seleccionados de forma
    recursiva y emite los resultados por lotes para no saturar la cola de
    señales de Qt con miles de emisiones individuales."""

    resultado_lote = pyqtSignal(list)      # List[RegistryResultRow]
    progreso = pyqtSignal(int, str)        # porcentaje, texto de estado
    finalizado = pyqtSignal(int, int)      # nº claves, nº valores encontrados
    error = pyqtSignal(str)

    _LOTE_SIZE = 40

    def __init__(self, termino: str, usar_regex: bool, hives: List[str],
                 buscar_claves: bool, buscar_valores: bool, buscar_datos: bool,
                 max_profundidad: int = 20):
        super().__init__()
        self._termino = termino
        self._usar_regex = usar_regex
        self._hives = hives
        self._buscar_claves = buscar_claves
        self._buscar_valores = buscar_valores
        self._buscar_datos = buscar_datos
        self._max_profundidad = max_profundidad
        self._stop_requested = False
        self._buffer: List[RegistryResultRow] = []
        self._n_claves = 0
        self._n_valores = 0
        self._pattern = None

    def request_stop(self):
        self._stop_requested = True

    def run(self):
        try:
            raw = self._termino if self._usar_regex else re.escape(self._termino)
            self._pattern = re.compile(raw, re.IGNORECASE)
        except re.error as e:
            self.error.emit(f"Expresión regular inválida: {e}")
            return

        # Si HKCR está seleccionado junto con HKLM/HKCU, se salta
        # "software\classes" en esos dos hives al recorrerlos: HKCR ya es
        # la vista fusionada de ambos y repetir el recorrido solo duplica
        # resultados y tiempo de búsqueda.
        saltar_classes = "HKCR" in self._hives and any(h in self._hives for h in ("HKLM", "HKCU"))

        total = len(self._hives)
        for idx, abbr in enumerate(self._hives):
            if self._stop_requested:
                break
            handle, full_name = REG_HIVES[abbr]
            self.progreso.emit(int((idx / max(total, 1)) * 100), f"Buscando en {abbr}...")
            prefijo_a_saltar = "software\\classes" if (saltar_classes and abbr in ("HKLM", "HKCU")) else None
            self._buscar_clave(handle, "", abbr, full_name, 0, prefijo_a_saltar)

        self._flush()
        self.progreso.emit(100, "Búsqueda detenida" if self._stop_requested else "Completado")
        self.finalizado.emit(self._n_claves, self._n_valores)

    def _flush(self):
        if self._buffer:
            self.resultado_lote.emit(self._buffer)
            self._buffer = []

    def _emitir(self, row: 'RegistryResultRow'):
        self._buffer.append(row)
        if row.match_type == "key":
            self._n_claves += 1
        else:
            self._n_valores += 1
        if len(self._buffer) >= self._LOTE_SIZE:
            self._flush()

    def _buscar_clave(self, hive, subkey, hive_abbr, hive_full, profundidad, prefijo_a_saltar):
        if profundidad > self._max_profundidad or self._stop_requested:
            return
        if prefijo_a_saltar and subkey.lower() == prefijo_a_saltar:
            return

        try:
            key_handle = winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
        except (PermissionError, OSError):
            return

        try:
            if self._buscar_valores or self._buscar_datos:
                self._escanear_valores(key_handle, subkey, hive_abbr, hive_full, hive)

            idx = 0
            while not self._stop_requested:
                try:
                    nombre_hijo = winreg.EnumKey(key_handle, idx)
                    idx += 1
                except OSError:
                    break
                ruta_hija = f"{subkey}\\{nombre_hijo}" if subkey else nombre_hijo

                if self._buscar_claves and self._pattern.search(nombre_hijo):
                    self._emitir(RegistryResultRow(
                        hive_abbr=hive_abbr, hive_full=hive_full, hive_key=hive,
                        subkey=ruta_hija, match_type="key",
                    ))

                self._buscar_clave(hive, ruta_hija, hive_abbr, hive_full,
                                    profundidad + 1, prefijo_a_saltar)
        except (PermissionError, OSError):
            pass
        finally:
            try:
                winreg.CloseKey(key_handle)
            except OSError:
                pass

    def _escanear_valores(self, key_handle, subkey, hive_abbr, hive_full, hive):
        idx = 0
        while not self._stop_requested:
            try:
                vnombre, vdato, vtipo = winreg.EnumValue(key_handle, idx)
                idx += 1
            except OSError:
                break

            coincide = False
            dato_str = ""
            if self._buscar_valores and self._pattern.search(vnombre):
                coincide = True
            if not coincide and self._buscar_datos:
                dato_str = format_reg_data(vdato, vtipo)
                if self._pattern.search(dato_str):
                    coincide = True
            if coincide:
                if not dato_str:
                    dato_str = format_reg_data(vdato, vtipo)
                self._emitir(RegistryResultRow(
                    hive_abbr=hive_abbr, hive_full=hive_full, hive_key=hive,
                    subkey=subkey, match_type="value",
                    value_name=vnombre, value_data=dato_str,
                    reg_type=REG_TYPES.get(vtipo, f"0x{vtipo:X}"), reg_type_int=vtipo,
                ))


class RegistryOrphanScanner(QThread):
    """Escáner de 'cadenas rotas' / entradas huérfanas: comprobaciones
    heurísticas sobre rutas de fichero referenciadas desde el registro que
    ya no existen en disco. El registro no tiene integridad referencial —
    esto es best-effort y se prioriza NO marcar falsos positivos (más vale
    no detectar una entrada rota que dejar seleccionable el borrado de una
    válida)."""

    progreso = pyqtSignal(int, str)
    finalizado = pyqtSignal(list)   # List[RegistryResultRow], match_type='huerfana'
    error = pyqtSignal(str)

    _CATEGORIAS = [
        "Desinstaladores", "Autoarranque (Run/RunOnce)", "App Paths",
        "Asociaciones de extensión", "CLSID/COM", "Servicios", "Shared DLLs",
    ]

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def run(self):
        try:
            resultados: List[RegistryResultRow] = []
            pasos = [
                self._escanear_uninstall, self._escanear_run,
                self._escanear_app_paths, self._escanear_extensiones,
                self._escanear_clsid, self._escanear_servicios,
                self._escanear_shared_dlls,
            ]
            total = len(pasos)
            for idx, paso in enumerate(pasos):
                if self._stop_requested:
                    break
                self.progreso.emit(int((idx / total) * 100),
                                    f"Comprobando {self._CATEGORIAS[idx]}...")
                try:
                    resultados.extend(paso())
                except Exception as e:
                    logger.warning(f"Error en escaneo de huérfanos "
                                    f"({self._CATEGORIAS[idx]}): {e}")
            self.progreso.emit(100, "Búsqueda detenida" if self._stop_requested else "Completado")
            self.finalizado.emit(resultados)
        except Exception as e:
            logger.exception(f"Error en RegistryOrphanScanner: {e}")
            self.error.emit(str(e))

    # ── utilidades internas ─────────────────────────────

    def _iter_subkeys(self, hive, ruta):
        try:
            with winreg.OpenKey(hive, ruta, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                idx = 0
                while True:
                    try:
                        nombre = winreg.EnumKey(k, idx)
                    except OSError:
                        break
                    idx += 1
                    yield nombre, f"{ruta}\\{nombre}" if ruta else nombre
        except (FileNotFoundError, PermissionError, OSError):
            return

    def _leer_valor(self, hive, ruta, nombre) -> Optional[str]:
        try:
            with winreg.OpenKey(hive, ruta, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                valor, _ = winreg.QueryValueEx(k, nombre)
                return str(valor) if valor is not None else None
        except (FileNotFoundError, PermissionError, OSError):
            return None

    def _existe_clave(self, hive, ruta) -> bool:
        try:
            with winreg.OpenKey(hive, ruta, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY):
                return True
        except (FileNotFoundError, PermissionError, OSError):
            return False

    def _fila(self, hive_abbr, hive, subkey, value_name, value_data, motivo) -> RegistryResultRow:
        return RegistryResultRow(
            hive_abbr=hive_abbr, hive_full=REG_HIVES[hive_abbr][1], hive_key=hive,
            subkey=subkey, match_type="huerfana",
            value_name=value_name, value_data=value_data, motivo=motivo,
        )

    # ── comprobaciones individuales ─────────────────────

    def _escanear_uninstall(self) -> List[RegistryResultRow]:
        filas = []
        objetivos = [
            ("HKLM", winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            ("HKLM", winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            ("HKCU", winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        for abbr, hive, base in objetivos:
            if self._stop_requested:
                break
            for _, ruta in self._iter_subkeys(hive, base):
                cadena = self._leer_valor(hive, ruta, "UninstallString")
                if cadena and not _existe_ejecutable(cadena):
                    filas.append(self._fila(abbr, hive, ruta, "UninstallString", cadena,
                                             f"El desinstalador no existe en disco: {cadena}"))
        return filas

    def _escanear_run(self) -> List[RegistryResultRow]:
        filas = []
        objetivos = [
            ("HKLM", winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"),
            ("HKLM", winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce"),
            ("HKCU", winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"),
            ("HKCU", winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce"),
        ]
        for abbr, hive, ruta in objetivos:
            if self._stop_requested:
                break
            try:
                with winreg.OpenKey(hive, ruta, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                    idx = 0
                    while True:
                        try:
                            nombre, dato, _ = winreg.EnumValue(k, idx)
                        except OSError:
                            break
                        idx += 1
                        if dato and not _existe_ejecutable(str(dato)):
                            filas.append(self._fila(abbr, hive, ruta, nombre, str(dato),
                                                     f"El ejecutable de autoarranque no existe: {dato}"))
            except (FileNotFoundError, PermissionError, OSError):
                continue
        return filas

    def _escanear_app_paths(self) -> List[RegistryResultRow]:
        filas = []
        base = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
        for _, ruta in self._iter_subkeys(winreg.HKEY_LOCAL_MACHINE, base):
            if self._stop_requested:
                break
            cadena = self._leer_valor(winreg.HKEY_LOCAL_MACHINE, ruta, "")
            if cadena and not _existe_ejecutable(cadena):
                # value_name = "" (el nombre técnico real del valor por
                # defecto): winreg.DeleteValue necesita ese "", no el texto
                # de pantalla "(predeterminado)" — usar el texto ahí rompía
                # el borrado con WinError 2 (no encuentra ese "nombre").
                filas.append(self._fila("HKLM", winreg.HKEY_LOCAL_MACHINE, ruta, "", cadena,
                                         f"App Path apunta a un ejecutable inexistente: {cadena}"))
        return filas

    def _escanear_extensiones(self) -> List[RegistryResultRow]:
        filas = []
        for nombre, ruta in self._iter_subkeys(winreg.HKEY_CLASSES_ROOT, ""):
            if self._stop_requested:
                break
            if not nombre.startswith("."):
                continue
            progid = self._leer_valor(winreg.HKEY_CLASSES_ROOT, ruta, "")
            if progid and not self._existe_clave(winreg.HKEY_CLASSES_ROOT, progid):
                filas.append(self._fila("HKCR", winreg.HKEY_CLASSES_ROOT, ruta, "", progid,
                                         f"La extensión apunta a un ProgID que no existe: {progid}"))
        return filas

    def _escanear_clsid(self) -> List[RegistryResultRow]:
        filas = []
        contador = 0
        for _, ruta in self._iter_subkeys(winreg.HKEY_CLASSES_ROOT, "CLSID"):
            if self._stop_requested:
                break
            contador += 1
            if contador > 8000:
                # límite defensivo: CLSID puede tener decenas de miles de
                # entradas y la mayoría son de sistema, nunca huérfanas.
                break
            ruta_inproc = f"{ruta}\\InprocServer32"
            ruta_dll = self._leer_valor(winreg.HKEY_CLASSES_ROOT, ruta_inproc, "")
            if ruta_dll and not _existe_ejecutable(ruta_dll):
                # La fila apunta a la propia subclave InprocServer32 (no a
                # CLSID\{guid} con un value_name "InprocServer32" inventado
                # — esa combinación no existe y el borrado fallaba con
                # WinError 2). Sin value_name (huérfana + subkey), el
                # borrado elimina la subclave InprocServer32 completa.
                filas.append(self._fila("HKCR", winreg.HKEY_CLASSES_ROOT, ruta_inproc, "", ruta_dll,
                                         f"El CLSID apunta a una DLL inexistente: {ruta_dll}"))
        return filas

    def _escanear_servicios(self) -> List[RegistryResultRow]:
        filas = []
        base = r"SYSTEM\CurrentControlSet\Services"
        for _, ruta in self._iter_subkeys(winreg.HKEY_LOCAL_MACHINE, base):
            if self._stop_requested:
                break
            cadena = self._leer_valor(winreg.HKEY_LOCAL_MACHINE, ruta, "ImagePath")
            if cadena and not _existe_ejecutable(cadena):
                filas.append(self._fila("HKLM", winreg.HKEY_LOCAL_MACHINE, ruta, "ImagePath", cadena,
                                         f"El servicio apunta a un binario inexistente: {cadena}"))
        return filas

    def _escanear_shared_dlls(self) -> List[RegistryResultRow]:
        filas = []
        ruta = r"SOFTWARE\Microsoft\Windows\CurrentVersion\SharedDlls"
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, ruta, 0,
                                 winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                idx = 0
                while True:
                    try:
                        nombre_dll, refcount, _ = winreg.EnumValue(k, idx)
                    except OSError:
                        break
                    idx += 1
                    if nombre_dll and not os.path.exists(os.path.expandvars(nombre_dll)):
                        filas.append(self._fila("HKLM", winreg.HKEY_LOCAL_MACHINE, ruta, nombre_dll, str(refcount),
                                                 f"La DLL compartida no existe en disco: {nombre_dll}"))
        except (FileNotFoundError, PermissionError, OSError):
            pass
        return filas


class ShareCollector(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(list)
    finished_error = pyqtSignal(str)

    def __init__(self, mode: str, target: str, exclude_admin_shares: bool, max_depth: int = 99, include_hidden: bool = True, only_real_users: bool = False, resume_checkpoint: bool = False):
        super().__init__()
        self.mode = mode
        self.target = target
        self.exclude_admin = exclude_admin_shares
        self.max_depth = max_depth
        self.include_hidden = include_hidden
        self.only_real_users = only_real_users
        self.resume_checkpoint = resume_checkpoint
        self.checkpoint_data = None
        self._stop_requested = False

        # Cargar checkpoint si se solicita reanudar
        if self.resume_checkpoint:
            self.checkpoint_data = load_checkpoint()
            if self.checkpoint_data:
                # 'results' se guarda como lista de dicts (JSON no serializa
                # dataclasses directamente) — reconstruir SharePermissionRow
                # para que el resto del pipeline los use con normalidad.
                try:
                    self.checkpoint_data['results'] = [
                        SharePermissionRow(**r) for r in self.checkpoint_data.get('results', [])
                    ]
                except TypeError as e:
                    logger.error(f"Checkpoint con formato incompatible, se descarta: {e}")
                    self.checkpoint_data = None

    def request_stop(self):
        """Solicita detener el análisis de forma controlada."""
        self._stop_requested = True

    def run(self):
        try:
            if self.mode == 'local':
                rows = self.collect_local()
            elif self.mode == 'path':
                rows = self.collect_path()
            else:
                raise ValueError(f"Modo desconocido: {self.mode}")

            self.finished_ok.emit(rows)
        except Exception as e:
            logger.exception(f"Error en ShareCollector: {e}")
            self.finished_error.emit(str(e))

    def collect_local(self) -> List[SharePermissionRow]:
        """Recopila recursos compartidos del equipo local."""
        rows: List[SharePermissionRow] = []

        self.progress.emit(10, "Obteniendo lista de recursos compartidos...")

        # Intentar Get-SmbShare primero
        shares_data = None
        if is_smbshare_available():
            self.progress.emit(15, "Usando Get-SmbShare...")
            shares_data = run_powershell_json(PS_GET_SMB_SHARES)

        # Si no funciona, usar WMI
        if not shares_data:
            self.progress.emit(15, "Usando Win32_Share (WMI)...")
            shares_data = run_powershell_json(PS_GET_WMI_SHARES)

        if not shares_data:
            raise Exception("No se pudieron obtener los recursos compartidos del sistema")

        if isinstance(shares_data, dict):
            shares_data = [shares_data]

        # Filtrar recursos administrativos si está marcado
        if self.exclude_admin:
            shares_data = [s for s in shares_data if not s.get("Name", "").endswith("$")]

        total = len(shares_data)
        if total == 0:
            self.progress.emit(100, "No hay recursos compartidos")
            return rows

        self.progress.emit(20, f"Procesando {total} recursos compartidos...")

        # Procesar cada recurso compartido
        for idx, share in enumerate(shares_data):
            # Verificar si se solicitó detener
            if self._stop_requested:
                self.progress.emit(100, "Análisis detenido por el usuario")
                return rows

            name = share.get("Name", "")
            path = share.get("Path", "")

            progress_pct = 20 + int((idx / total) * 70)
            self.progress.emit(progress_pct, f"Analizando {name}...")

            # Obtener permisos SMB si está disponible
            if is_smbshare_available():
                name_escaped = escape_powershell_string(name)
                ps_smb = PS_GET_SMB_ACCESS.format(name=name_escaped)
                acc_smb = run_powershell_json(ps_smb, timeout=10)

                if acc_smb:
                    entries_smb = acc_smb if isinstance(acc_smb, list) else [acc_smb]
                    for entry in entries_smb:
                        identity = entry.get("AccountName", "")
                        # Filtrar si solo se quieren usuarios reales
                        if self.only_real_users and not self._is_real_user(identity):
                            continue
                        rows.append(SharePermissionRow(
                            recurso=name,
                            ruta=path,
                            identidad=identity,
                            permiso=traducir_permiso(entry.get("AccessRight", "")),
                            tipo=traducir_tipo(entry.get("AccessControlType", "")),
                            origen="SMB"
                        ))

            # Obtener permisos NTFS
            if path and path.strip():
                path_escaped = escape_powershell_string(path)
                ps_acl = PS_GET_ACL_UNC.format(path=path_escaped)
                acl = run_powershell_json(ps_acl, timeout=10)

                if acl and not (isinstance(acl, dict) and acl.get('IdentityReference') == 'ERROR'):
                    entries_acl = acl if isinstance(acl, list) else [acl]
                    for entry in entries_acl:
                        identity = entry.get("IdentityReference", "")
                        # Filtrar si solo se quieren usuarios reales
                        if self.only_real_users and not self._is_real_user(identity):
                            continue
                        rows.append(SharePermissionRow(
                            recurso=name,
                            ruta=path,
                            identidad=identity,
                            permiso=traducir_permiso(entry.get("FileSystemRights", "")),
                            tipo=traducir_tipo(entry.get("AccessControlType", "")),
                            origen="NTFS"
                        ))

        self.progress.emit(100, "Completado")
        return rows

    def collect_path(self) -> List[SharePermissionRow]:
        """Analiza la ruta especificada y todas sus subcarpetas recursivamente con soporte de checkpoint."""
        rows: List[SharePermissionRow] = []
        path = self.target.strip()
        processed_folders = set()

        # Normalizar la ruta
        path = path.replace('/', '\\')

        # Validar que la ruta no esté vacía
        if not path:
            raise Exception("La ruta especificada está vacía")

        # Si es solo una letra de unidad (H: o H:\), añadir \
        if len(path) == 2 and path[1] == ':':
            path = path + '\\'
        elif len(path) == 3 and path.endswith(':\\'):
            pass  # Ya está bien formada

        # Verificar que la ruta existe
        if not os.path.exists(path):
            raise Exception(f"La ruta especificada no existe: {path}")

        # Cargar checkpoint si existe y se solicita reanudar
        if self.checkpoint_data and self.resume_checkpoint:
            # Verificar que el checkpoint coincida con la configuración actual
            if (self.checkpoint_data.get('mode') == self.mode and
                self.checkpoint_data.get('target') == path and
                self.checkpoint_data.get('max_depth') == self.max_depth and
                self.checkpoint_data.get('include_hidden') == self.include_hidden and
                self.checkpoint_data.get('only_real_users') == self.only_real_users):

                # Recuperar resultados y carpetas procesadas
                rows = self.checkpoint_data.get('results', [])
                processed_folders = set(self.checkpoint_data.get('processed_folders', []))
                self.progress.emit(5, f"Recuperando desde checkpoint: {len(processed_folders)} carpetas ya procesadas...")
                logger.info(f"Reanudando desde checkpoint: {len(rows)} resultados, {len(processed_folders)} carpetas procesadas")
            else:
                logger.warning("Checkpoint no coincide con configuración actual, iniciando análisis nuevo")
                delete_checkpoint()

        self.progress.emit(10, "Escaneando estructura de carpetas...")

        # Recopilar todas las carpetas (la especificada + subcarpetas recursivamente)
        all_folders = [path]
        try:
            # Normalizar la ruta base para calcular profundidad correctamente
            base_path = path.rstrip('\\')
            base_depth = base_path.count('\\')

            for root, dirs, files in os.walk(path):
                # Calcular profundidad actual relativa a la ruta base
                current_depth = root.count('\\') - base_depth + 1

                # Si excedemos la profundidad máxima, no explorar más subdirectorios
                if current_depth >= self.max_depth:
                    dirs.clear()  # Evita que os.walk continúe en subdirectorios
                    continue

                # Junctions fuera, como en el resto de recorridos de este módulo: os.walk las sigue (comprobado,
                # Python 3.12) y una que apunte hacia arriba repetía las mismas carpetas hasta max_depth niveles.
                dirs[:] = [d for d in dirs if not _es_reparse_point(os.path.join(root, d))]

                # Filtrar carpetas ocultas si es necesario
                if not self.include_hidden:
                    dirs[:] = [d for d in dirs if not self._is_hidden(os.path.join(root, d))]

                for dir_name in dirs:
                    folder_path = os.path.join(root, dir_name)
                    all_folders.append(folder_path)
        except Exception as e:
            logger.warning(f"Error al escanear subcarpetas: {e}")

        total_folders = len(all_folders)
        self.progress.emit(20, f"Analizando permisos de {total_folders} carpetas...")
        # Carpetas cuyos permisos se han podido leer: «solo usuarios reales» puede dejar 0 filas sin que haya fallado
        # nada (carpetas con solo grupos del sistema) y antes eso acababa en «No se pudieron obtener permisos».
        leidas = len(processed_folders)

        # Analizar permisos de cada carpeta
        for idx, folder_path in enumerate(all_folders):
            # Verificar si se solicitó detener
            if self._stop_requested:
                self.progress.emit(100, "Análisis detenido por el usuario")
                return rows

            # Saltar carpetas ya procesadas
            if folder_path in processed_folders:
                continue

            progress_pct = 20 + int((idx / total_folders) * 75)
            folder_name = os.path.basename(folder_path.rstrip('\\')) or folder_path
            self.progress.emit(progress_pct, f"Analizando {folder_name}... ({idx+1}/{total_folders})")

            # Obtener permisos NTFS de esta carpeta
            path_escaped = escape_powershell_string(folder_path)
            ps_acl = PS_GET_ACL_UNC.format(path=path_escaped)
            acl = run_powershell_json(ps_acl, timeout=10)

            if acl and not (isinstance(acl, dict) and acl.get('IdentityReference') == 'ERROR'):
                leidas += 1
                entries_acl = acl if isinstance(acl, list) else [acl]
                for entry in entries_acl:
                    identity = entry.get("IdentityReference", "")
                    # Filtrar si solo se quieren usuarios reales
                    if self.only_real_users and not self._is_real_user(identity):
                        continue
                    rows.append(SharePermissionRow(
                        recurso=folder_name if folder_name else folder_path,
                        ruta=folder_path,
                        identidad=identity,
                        permiso=traducir_permiso(entry.get("FileSystemRights", "")),
                        tipo=traducir_tipo(entry.get("AccessControlType", "")),
                        origen="NTFS"
                    ))

            # Marcar carpeta como procesada
            processed_folders.add(folder_path)

            # Guardar checkpoint cada CHECKPOINT_INTERVAL carpetas
            if (idx + 1) % CHECKPOINT_INTERVAL == 0:
                checkpoint_data = {
                    'mode': self.mode,
                    'target': path,
                    'max_depth': self.max_depth,
                    'include_hidden': self.include_hidden,
                    'only_real_users': self.only_real_users,
                    'exclude_admin': self.exclude_admin,
                    'total_folders': total_folders,
                    'processed_count': len(processed_folders),
                    'processed_folders': list(processed_folders),
                    'results': [asdict(r) for r in rows],
                }
                save_checkpoint(checkpoint_data)

        if not rows and not leidas:
            raise Exception(f"No se pudieron obtener permisos para la ruta: {path}")

        # Eliminar checkpoint al completar exitosamente
        delete_checkpoint()

        self.progress.emit(100, "Completado")
        return rows

    def _is_hidden(self, folder_path: str) -> bool:
        """Verifica si una carpeta tiene el atributo oculto en Windows."""
        try:
            attrs = ctypes.windll.kernel32.GetFileAttributesW(folder_path)
            # FILE_ATTRIBUTE_HIDDEN = 0x2
            return attrs != -1 and bool(attrs & 0x2)
        except Exception:
            return False

    def _is_real_user(self, identity: str) -> bool:
        """Determina si una identidad es un usuario real (no cuenta del sistema/grupo genérico)."""
        if not identity:
            return False

        identity_upper = identity.upper().strip()

        # Patrones completos de cuentas del sistema (verificar primero)
        system_full_patterns = [
            'NT AUTHORITY\\SYSTEM',
            'NT AUTHORITY\\LOCAL SERVICE',
            'NT AUTHORITY\\NETWORK SERVICE',
            'NT AUTHORITY\\ANONYMOUS LOGON',
            'NT AUTHORITY\\AUTHENTICATED USERS',
            'NT AUTHORITY\\USUARIOS AUTENTICADOS',  # Versión española (e)
            'NT AUTHORITY\\USUARIOS AUTENTIFICADOS',  # Versión española (i)
            'NT AUTHORITY\\INTERACTIVE',
            'NT AUTHORITY\\INTERACTIVO',  # Versión española
            'NT AUTHORITY\\SERVICE',
            'NT AUTHORITY\\BATCH',
            'NT AUTHORITY\\NETWORK',
            'AUTORITE NT\\',
            'NT-AUTORITÄT\\'
        ]

        for pattern in system_full_patterns:
            if pattern in identity_upper:
                return False

        # Prefijos de sistema
        system_prefixes = [
            'BUILTIN\\',
            'NT SERVICE\\',
            'APPLICATION PACKAGE AUTHORITY\\',
            'AUTORITE NT\\',
            'NT-AUTORITÄT\\'
        ]

        for prefix in system_prefixes:
            if identity_upper.startswith(prefix):
                return False

        # Grupos genéricos (nombre completo o después de \)
        generic_groups = [
            'EVERYONE', 'TODOS', 'TOUT LE MONDE', 'JEDER',
            'AUTHENTICATED USERS', 'USUARIOS AUTENTICADOS', 'USUARIOS AUTENTIFICADOS', 'UTILISATEURS AUTHENTIFIÉS',
            'USERS', 'USUARIOS', 'USUÁRIOS', 'UTILISATEURS', 'BENUTZER',
            'ADMINISTRATORS', 'ADMINISTRADORES', 'ADMINISTRATEURS', 'ADMINISTRATOREN',
            'CREATOR OWNER', 'CREADOR PROPIETARIO', 'CRÉATEUR PROPRIÉTAIRE',
            'INTERACTIVE', 'INTERACTIVO', 'INTERACTIF',
            'DOMAIN USERS', 'DOMAIN ADMINS', 'ENTERPRISE ADMINS',
            'ACCOUNT OPERATORS', 'BACKUP OPERATORS', 'PRINT OPERATORS',
            'POWER USERS', 'REMOTE DESKTOP USERS'
        ]

        # Extraer última parte si hay \
        if '\\' in identity_upper:
            last_part = identity_upper.split('\\')[-1].strip()
        else:
            last_part = identity_upper

        # Verificar si coincide exactamente con algún grupo genérico
        if last_part in generic_groups:
            return False

        # Si llegamos aquí, probablemente es un usuario real
        return True

class NetworkDriveCollector(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(list)
    finished_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        """Solicita detener el análisis de forma controlada."""
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(20, "Buscando unidades remotas...")

            # Intentar primero con el comando mejorado (v3.0) con timeout extendido
            drives = run_powershell_json(PS_GET_NETWORK_DRIVES_ENHANCED, timeout=30)
            enhanced_mode = True

            # Fallback a comandos básicos si falla el mejorado
            if not drives:
                self.progress.emit(30, "Usando método básico...")
                enhanced_mode = False
                drives = run_powershell_json(PS_GET_NETWORK_DRIVES)

            if not drives:
                self.progress.emit(50, "Intentando método alternativo (WMI)...")
                drives = run_powershell_json(PS_GET_NETWORK_DRIVES_WMI)

            if not drives:
                self.progress.emit(100, "No se encontraron unidades remotas")
                self.finished_ok.emit([])
                return

            if isinstance(drives, dict):
                drives = [drives]

            rows = []
            total = len(drives)
            for idx, d in enumerate(drives):
                # Verificar si se solicitó detener
                if self._stop_requested:
                    self.progress.emit(100, "Análisis detenido por el usuario")
                    self.finished_ok.emit(rows)
                    return

                self.progress.emit(50 + int((idx / total) * 50), f"Procesando unidad {d.get('Letra', '')}...")

                # Crear fila con todos los campos (v3.0)
                if enhanced_mode:
                    # Modo mejorado: parsear todos los campos
                    rows.append(NetworkDriveRow(
                        letra=d.get("Letra", ""),
                        ruta_remota=d.get("RutaRemota", ""),
                        descripcion=d.get("Descripcion", ""),
                        estado=d.get("Estado", ""),
                        total_gb=d.get("TotalGB", "N/A"),
                        usado_gb=d.get("UsadoGB", "N/A"),
                        libre_gb=d.get("LibreGB", "N/A"),
                        porcentaje_uso=d.get("PorcentajeUso", "N/A"),
                        credencial=d.get("Credencial", "N/A"),
                        persistente=d.get("Persistente", "N/A"),
                        version_smb=d.get("VersionSMB", "N/A"),
                        velocidad=d.get("Velocidad", "N/A"),
                        ping_ms=d.get("PingMS", "N/A"),
                        accesible=d.get("Accesible", "Sí")
                    ))
                else:
                    # Modo básico: solo campos básicos, resto por defecto "N/A"
                    rows.append(NetworkDriveRow(
                        letra=d.get("Letra", ""),
                        ruta_remota=d.get("RutaRemota", ""),
                        descripcion=d.get("Descripcion", ""),
                        estado=d.get("Estado", "")
                        # Los demás campos usan valores por defecto del dataclass
                    ))

            self.progress.emit(100, "Completado")
            self.finished_ok.emit(rows)
        except Exception as e:
            logger.exception(f"Error en NetworkDriveCollector: {e}")
            self.finished_error.emit(str(e))


_PERSISTENCIA_CREDENCIAL = {1: "Solo esta sesión", 2: "Este equipo", 3: "Itinerante (empresa)"}


def _metadatos_credenciales() -> Dict[str, Tuple[str, str]]:
    """5.5.0: {destino en minúsculas: (fecha de última modificación, persistencia)} del Administrador de
    credenciales con CredEnumerate. SOLO se leen el nombre, la fecha y la persistencia: el campo con el
    secreto (CredentialBlob) se descarta en el acto, no se guarda ni se muestra. Antes la columna «Última
    modificación» salía siempre «N/A» y «Persistencia» siempre «Sistema»."""
    resultado: Dict[str, Tuple[str, str]] = {}
    try:
        import win32cred
        lista = win32cred.CredEnumerate(None, 0) or []
    except Exception:
        return resultado
    for c in lista:
        try:
            c.pop("CredentialBlob", None)
            destino = limpiar_destino_credential(str(c.get("TargetName") or "")).lower()
            fecha = c.get("LastWritten")
            fecha_txt = fecha.strftime("%d/%m/%Y %H:%M") if fecha else "N/A"
            resultado[destino] = (fecha_txt, _PERSISTENCIA_CREDENCIAL.get(int(c.get("Persist") or 0), ""))
        except Exception:
            continue
    lista = None
    return resultado

class CredentialCollector(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(list)
    finished_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False
        self.credential_manager_fallo = False

    def request_stop(self):
        """Solicita detener el análisis de forma controlada."""
        self._stop_requested = True

    def run(self):
        try:
            all_rows = []

            # 1. Recolectar credenciales del Credential Manager
            self.progress.emit(10, "Consultando Credential Manager...")
            creds = run_powershell_json(PS_GET_CREDENTIALS)
            # None = fallo real del proceso de PowerShell (timeout, no se
            # pudo lanzar...); PS_GET_CREDENTIALS ya emite "[]" tanto para
            # "0 credenciales de verdad" como para cualquier excepción DENTRO
            # del script (su propio catch), así que None solo puede venir de
            # un fallo a nivel de proceso — no se aborta la recolección
            # entera (SSH/Git/RDP son fuentes independientes y sí merece la
            # pena seguir con ellas), pero se avisa en vez de mostrar "0
            # credenciales" en silencio como si Credential Manager
            # simplemente no tuviera ninguna.
            credential_manager_fallo = (creds is None)
            self.credential_manager_fallo = credential_manager_fallo

            if creds:
                if isinstance(creds, dict):
                    creds = [creds]

                total = len(creds)
                metadatos = _metadatos_credenciales()   # 5.5.0: fecha y persistencia reales
                for idx, c in enumerate(creds):
                    if self._stop_requested:
                        self.progress.emit(100, "Análisis detenido por el usuario")
                        self.finished_ok.emit(all_rows)
                        return

                    self.progress.emit(10 + int((idx / total) * 20), f"Procesando credencial {idx+1}/{total}...")
                    destino_original = c.get("Destino", "")
                    tipo_original = c.get("Tipo", "")
                    destino_limpio = limpiar_destino_credential(destino_original)
                    categoria = clasificar_tipo_destino(destino_original, tipo_original)

                    all_rows.append(CredentialRow(
                        destino=destino_limpio,
                        usuario=c.get("Usuario", ""),
                        tipo=tipo_original,
                        categoria=categoria,
                        contraseña="(protegida por Windows)",
                        ultima_modificacion=metadatos.get(destino_limpio.lower(), ("N/A", ""))[0],
                        persistencia=metadatos.get(destino_limpio.lower(), ("", "Sistema"))[1] or "Sistema",
                        estado="Guardada",
                        fuente="Credential Manager"
                    ))

            # 2. Recolectar claves SSH
            if self._stop_requested:
                self.progress.emit(100, "Análisis detenido por el usuario")
                self.finished_ok.emit(all_rows)
                return

            self.progress.emit(40, "Buscando claves SSH...")
            ssh_creds = collect_ssh_keys()
            all_rows.extend(ssh_creds)

            # 3. Recolectar credenciales de Git
            if self._stop_requested:
                self.progress.emit(100, "Análisis detenido por el usuario")
                self.finished_ok.emit(all_rows)
                return

            self.progress.emit(60, "Buscando credenciales de Git...")
            git_creds = collect_git_credentials()
            all_rows.extend(git_creds)

            # 4. Recolectar archivos RDP
            if self._stop_requested:
                self.progress.emit(100, "Análisis detenido por el usuario")
                self.finished_ok.emit(all_rows)
                return

            self.progress.emit(80, "Buscando archivos RDP...")
            rdp_creds = collect_rdp_files()
            all_rows.extend(rdp_creds)

            # Completado
            if credential_manager_fallo:
                self.progress.emit(100, f"Completado con avisos - {len(all_rows)} credenciales encontradas "
                                         "(no se pudo consultar el Administrador de Credenciales)")
            else:
                self.progress.emit(100, f"Completado - {len(all_rows)} credenciales encontradas")
            self.finished_ok.emit(all_rows)

        except Exception as e:
            logger.exception(f"Error en CredentialCollector: {e}")
            self.finished_error.emit(str(e))

class WiFiCollector(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(list)
    finished_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        """Solicita detener el análisis de forma controlada."""
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(20, "Escaneando redes WiFi guardadas...")
            wifi_list = run_powershell_json(PS_GET_WIFI_PASSWORDS, timeout=45)

            # None (timeout/error real) y [] (equipo sin WiFi guardadas,
            # p. ej. solo Ethernet — resultado legítimo) eran indistinguibles.
            if wifi_list is None:
                self.finished_error.emit(
                    "Tiempo agotado o error al escanear las redes WiFi guardadas.")
                return
            if not wifi_list:
                self.progress.emit(100, "No se encontraron redes WiFi")
                self.finished_ok.emit([])
                return

            if isinstance(wifi_list, dict):
                wifi_list = [wifi_list]

            rows = []
            total = len(wifi_list)
            for idx, w in enumerate(wifi_list):
                if self._stop_requested:
                    self.progress.emit(100, "Análisis detenido por el usuario")
                    self.finished_ok.emit(rows)
                    return

                self.progress.emit(40 + int((idx / total) * 60), f"Procesando red {idx+1}/{total}...")

                rows.append(WiFiRow(
                    nombre=w.get("Nombre", ""),
                    contraseña=w.get("Contraseña", "N/A"),
                    autenticacion=w.get("Autenticacion", "N/A"),
                    cifrado=w.get("Cifrado", "N/A"),
                    tipo_conexion=w.get("TipoConexion", "N/A"),
                    conectado=w.get("Conectado", "No")
                ))

            self.progress.emit(100, "Completado")
            self.finished_ok.emit(rows)
        except Exception as e:
            logger.exception(f"Error en WiFiCollector: {e}")
            self.finished_error.emit(str(e))


class WiFiScanThread(QThread):
    """Escaneo de redes WiFi VISIBLES ahora mismo (netsh wlan show
    networks), distinto de WiFiCollector (perfiles YA GUARDADOS con su
    contraseña) — complementario: este no necesita admin ni expone
    ninguna clave, solo lista lo que hay en el aire."""

    finished_ok = pyqtSignal(list)  # [{ssid, autenticacion, cifrado, senal}, ...]
    finished_error = pyqtSignal(str)

    def run(self):
        try:
            redes = wifi_escanear_redes_cercanas()
            self.finished_ok.emit(redes)
        except Exception as e:
            logger.exception(f"Error en WiFiScanThread: {e}")
            self.finished_error.emit(str(e))


class CertificateCollector(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(list)
    finished_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        """Solicita detener el análisis de forma controlada."""
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(20, "Escaneando certificados del sistema...")
            certs = run_powershell_json(PS_GET_CERTIFICATES, timeout=30)

            if certs is None:
                self.finished_error.emit(
                    "Tiempo agotado o error al escanear los certificados del sistema.")
                return
            if not certs:
                self.progress.emit(100, "No se encontraron certificados")
                self.finished_ok.emit([])
                return

            if isinstance(certs, dict):
                certs = [certs]

            rows = []
            total = len(certs)
            for idx, c in enumerate(certs):
                if self._stop_requested:
                    self.progress.emit(100, "Análisis detenido por el usuario")
                    self.finished_ok.emit(rows)
                    return

                self.progress.emit(40 + int((idx / total) * 60), f"Procesando certificado {idx+1}/{total}...")

                rows.append(CertificateRow(
                    asunto=c.get("Asunto", ""),
                    emisor=c.get("Emisor", ""),
                    valido_desde=c.get("ValidoDesde", ""),
                    valido_hasta=c.get("ValidoHasta", ""),
                    huella_digital=c.get("HuellaDigital", ""),
                    uso=c.get("Uso", ""),
                    ubicacion=c.get("Ubicacion", ""),
                    estado=c.get("Estado", "Desconocido"),
                    exportable=c.get("Exportable", "No"),
                    algoritmo_firma=c.get("AlgoritmoFirma", "") or "",
                    autofirmado=c.get("Autofirmado", "No"),
                ))

            self.progress.emit(100, "Completado")
            self.finished_ok.emit(rows)
        except Exception as e:
            logger.exception(f"Error en CertificateCollector: {e}")
            self.finished_error.emit(str(e))

class EnvironmentVarCollector(QThread):
    """Operativa portada: antes leía Get-ChildItem Env: por PowerShell (el
    entorno YA fusionado del propio proceso — no distingue solapes entre
    ámbitos ni se entera de cambios hechos después de que MASys
    arrancara). Ahora usa VarManager (registro directo, ver su __doc__)."""
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(list)
    finished_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        """Solicita detener el análisis de forma controlada."""
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(20, "Leyendo variables de entorno del registro...")
            rows = VarManager().list_all_vars()
            self.progress.emit(100, "Completado")
            self.finished_ok.emit(rows)
        except Exception as e:
            logger.exception(f"Error en EnvironmentVarCollector: {e}")
            self.finished_error.emit(str(e))

# ---------------------------
# Gestión de Procesos (pestaña "Procesos") — psutil, sin PowerShell.
# Portado de la operativa validada en el proyecto MAProcess
# (_TEMP\MAProcess\core\process_manager.py): la versión anterior usaba
# PowerShell (Get-Process | Select-Object -First 50), lo que limitaba el
# listado a los primeros 50 procesos arbitrarios y solo exponía CPU
# acumulada (segundos), no un % en vivo, ni estado, prioridad o usuario.
# ---------------------------

_PROC_ATTRS = ["pid", "ppid", "name", "username", "status", "exe", "num_threads"]


class ProcessManager:
    """Enumeración y control de procesos vía psutil. Requiere privilegios de
    administrador para actuar sobre procesos de otros usuarios o del propio
    sistema (MASys ya se relanza elevado, ver uac_admin=True en
    multisys.spec)."""

    def list_all_processes(self) -> List[ProcessRow]:
        processes: List[ProcessRow] = []
        # process_iter es inherentemente inestable: cualquier proceso puede
        # morir entre la enumeración y la lectura de sus datos, así que cada
        # proceso se envuelve en su propio try/except (mismo patrón que
        # ServiceManager._load_detail con pywintypes.error).
        for proc in psutil.process_iter(_PROC_ATTRS):
            try:
                info = proc.info

                try:
                    mem_mb = proc.memory_info().rss / (1024 * 1024)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    mem_mb = 0.0

                try:
                    # Primera lectura por proceso: psutil devuelve 0.0 hasta
                    # la siguiente llamada (necesita dos muestras para
                    # calcular %). Limitación conocida de psutil, no un bug:
                    # en "Actualizar" sucesivos ya refleja el uso real.
                    cpu = proc.cpu_percent(interval=None)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    cpu = 0.0

                try:
                    priority = _PROCESS_PRIORITY_MAP.get(proc.nice(), "normal")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    priority = "unknown"

                try:
                    children = [f"{c.name()} ({c.pid})" for c in proc.children()]
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    children = []

                processes.append(ProcessRow(
                    pid=info.get("pid", 0),
                    name=info.get("name") or f"PID {info.get('pid')}",
                    status=info.get("status") or "unknown",
                    priority=priority,
                    username=info.get("username") or "",
                    exe_path=info.get("exe") or "",
                    ppid=info.get("ppid") or 0,
                    num_threads=info.get("num_threads") or 0,
                    memory_mb=round(mem_mb, 1),
                    cpu_percent=round(cpu, 1),
                    children=children,
                ))
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        return sorted(processes, key=lambda p: p.name.lower())

    def get_process(self, pid: int) -> Optional[ProcessRow]:
        for p in self.list_all_processes():
            if p.pid == pid:
                return p
        return None

    def kill_process(self, pid: int) -> Tuple[bool, str]:
        """Finalizar = terminate() y, si no responde en 3s, kill() forzado
        (mismo comportamiento que 'Finalizar tarea' del Administrador de
        tareas, que tampoco pide confirmación adicional para forzar)."""
        try:
            proc = psutil.Process(pid)
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except psutil.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
            return True, "OK"
        except psutil.NoSuchProcess:
            return True, "OK (el proceso ya no existía)"
        except psutil.AccessDenied as exc:
            return False, f"Acceso denegado (requiere administrador): {exc}"
        except Exception as exc:
            return False, str(exc)

    def suspend_process(self, pid: int) -> Tuple[bool, str]:
        try:
            psutil.Process(pid).suspend()
            return True, "OK"
        except psutil.NoSuchProcess:
            return False, "El proceso ya no existe"
        except psutil.AccessDenied as exc:
            return False, f"Acceso denegado (requiere administrador): {exc}"
        except Exception as exc:
            return False, str(exc)

    def resume_process(self, pid: int) -> Tuple[bool, str]:
        try:
            psutil.Process(pid).resume()
            return True, "OK"
        except psutil.NoSuchProcess:
            return False, "El proceso ya no existe"
        except psutil.AccessDenied as exc:
            return False, f"Acceso denegado (requiere administrador): {exc}"
        except Exception as exc:
            return False, str(exc)

    def set_priority(self, pid: int, priority_key: str) -> Tuple[bool, str]:
        const = _PROCESS_PRIORITY_CONST.get(priority_key)
        if const is None:
            return False, f"Prioridad desconocida: {priority_key}"
        try:
            psutil.Process(pid).nice(const)
            return True, "OK"
        except psutil.NoSuchProcess:
            return False, "El proceso ya no existe"
        except psutil.AccessDenied as exc:
            return False, f"Acceso denegado (requiere administrador): {exc}"
        except Exception as exc:
            return False, str(exc)


class ProcessNotesDB:
    """Notas de texto libre por nombre de proceso, persistidas en multisys.db
    (misma base que ServiceNotesDB, tabla propia). Se guardan por nombre, no
    por PID: el PID no sobrevive a un reinicio del proceso."""

    def __init__(self):
        base_dir = msys_config.app_base_dir()
        self.path = os.path.join(base_dir, "multisys.db")
        self._init_schema()

    def _conn(self):
        return _sqlite_connect(self.path)

    def _init_schema(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS process_notes (
                    process_name TEXT PRIMARY KEY,
                    note_text TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

    def get_all_notes(self) -> Dict[str, str]:
        with self._conn() as conn:
            cur = conn.execute("SELECT process_name, note_text FROM process_notes")
            return dict(cur.fetchall())

    def save_note(self, process_name: str, note_text: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO process_notes (process_name, note_text, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(process_name) DO UPDATE SET note_text=excluded.note_text, updated_at=excluded.updated_at",
                (process_name, note_text, datetime.now().isoformat(timespec='seconds')),
            )

    def delete_note(self, process_name: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM process_notes WHERE process_name = ?", (process_name,))


class ProcessWhitelistDB:
    """Procesos que el usuario ha marcado explícitamente como 'ya lo conozco,
    no me avises más' desde el detector de Procesos sospechosos (Soporte).
    Clave (nombre, ruta) y no solo nombre: un mismo nombre de proceso puede
    ser legítimo en una ruta y no en otra (ese es justo el caso que detecta
    _proceso_disfrazado), así que marcar 'legítimo' por nombre solo sería
    peligroso — silenciaría también una futura instancia maliciosa con el
    mismo nombre en otro sitio."""

    def __init__(self):
        base_dir = msys_config.app_base_dir()
        self.path = os.path.join(base_dir, "multisys.db")
        self._init_schema()

    def _conn(self):
        return _sqlite_connect(self.path)

    def _init_schema(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS process_whitelist (
                    process_name TEXT NOT NULL,
                    exe_path TEXT NOT NULL,
                    motivo TEXT NOT NULL DEFAULT '',
                    added_at TEXT NOT NULL,
                    PRIMARY KEY (process_name, exe_path)
                )
            """)

    def is_whitelisted(self, process_name: str, exe_path: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT 1 FROM process_whitelist WHERE process_name = ? AND exe_path = ?",
                (process_name.lower(), exe_path.lower()),
            )
            return cur.fetchone() is not None

    def add(self, process_name: str, exe_path: str, motivo: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO process_whitelist (process_name, exe_path, motivo, added_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(process_name, exe_path) DO UPDATE SET motivo=excluded.motivo, added_at=excluded.added_at",
                (process_name.lower(), exe_path.lower(), motivo, datetime.now().isoformat(timespec='seconds')),
            )

    def remove(self, process_name: str, exe_path: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM process_whitelist WHERE process_name = ? AND exe_path = ?",
                (process_name.lower(), exe_path.lower()),
            )

    def get_all(self) -> List[Tuple[str, str, str, str]]:
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT process_name, exe_path, motivo, added_at FROM process_whitelist ORDER BY added_at DESC")
            return cur.fetchall()


class ProcessCollector(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(list)
    finished_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        """Solicita detener el análisis de forma controlada."""
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(20, "Escaneando procesos en ejecución...")
            rows = ProcessManager().list_all_processes()
            self.progress.emit(100, "Completado")
            self.finished_ok.emit(rows)
        except Exception as e:
            logger.exception(f"Error en ProcessCollector: {e}")
            self.finished_error.emit(str(e))


# ---------------------------
# Gestión de Servicios de Windows (pestaña "Servicios") — pywin32 puro,
# sin dependencia de Qt. Portado de la operativa validada en el proyecto
# MA_SERV (LAUNCHER\SCRIPTS\MA_SERV\core\service_manager.py): consultas
# síncronas y rápidas contra el Service Control Manager, sin pasar por
# PowerShell (que en la versión anterior limitaba el listado a 100
# servicios y no exponía tipo de inicio real, cuenta, PID ni dependencias).
# ---------------------------

_SVC_STATUS_MAP: Dict[int, str] = {
    win32service.SERVICE_RUNNING:          "running",
    win32service.SERVICE_STOPPED:          "stopped",
    win32service.SERVICE_PAUSED:           "paused",
    win32service.SERVICE_START_PENDING:    "start_pending",
    win32service.SERVICE_STOP_PENDING:     "stop_pending",
    win32service.SERVICE_CONTINUE_PENDING: "continue_pending",
    win32service.SERVICE_PAUSE_PENDING:    "pause_pending",
}

_SVC_START_TYPE_MAP: Dict[int, str] = {
    win32service.SERVICE_AUTO_START:   "auto",
    win32service.SERVICE_DEMAND_START: "manual",
    win32service.SERVICE_DISABLED:     "disabled",
    win32service.SERVICE_BOOT_START:   "boot",
    win32service.SERVICE_SYSTEM_START: "system",
}

_SVC_START_TYPE_CONST: Dict[str, int] = {
    "auto":     win32service.SERVICE_AUTO_START,
    "manual":   win32service.SERVICE_DEMAND_START,
    "disabled": win32service.SERVICE_DISABLED,
}

class ServiceManager:
    """Enumeración y control de servicios de Windows vía win32service/
    win32serviceutil. Requiere privilegios de administrador para
    start/stop/restart/set_startup_type (MASys ya se relanza elevado,
    ver uac_admin=True en multisys.spec)."""

    def list_all_services(self) -> List[ServiceRow]:
        rows: List[ServiceRow] = []
        try:
            hscm = win32service.OpenSCManager(
                None, None, win32service.SC_MANAGER_ENUMERATE_SERVICE
            )
            try:
                raw_list = win32service.EnumServicesStatusEx(
                    hscm,
                    win32service.SERVICE_WIN32,
                    win32service.SERVICE_STATE_ALL,
                )
            finally:
                win32service.CloseServiceHandle(hscm)

            for raw in raw_list:
                rows.append(self._load_detail(
                    raw["ServiceName"],
                    raw["DisplayName"],
                    raw["CurrentState"],
                    raw.get("ProcessId", 0),
                ))

        except pywintypes.error as exc:
            logger.error("Error enumerando servicios: %s", exc)

        return sorted(rows, key=lambda r: r.display_name.lower())

    def _load_detail(self, name: str, display_name: str, current_state: int, pid: int = 0) -> ServiceRow:
        base_status = _SVC_STATUS_MAP.get(current_state, "unknown")
        fallback = ServiceRow(
            name=name, display_name=display_name, status=base_status,
            startup_type="unknown", account="", pid=pid,
        )
        try:
            hscm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
            try:
                access = win32service.SERVICE_QUERY_CONFIG | win32service.SERVICE_QUERY_STATUS
                hsvc = win32service.OpenService(hscm, name, access)
                try:
                    # (svcType, startType, errCtrl, binPath, loadGroup, tagId, deps, startName, displayName)
                    config = win32service.QueryServiceConfig(hsvc)
                    startup = _SVC_START_TYPE_MAP.get(config[1], "manual")
                    account = config[7] or ""
                    deps = list(config[6]) if config[6] else []
                    bin_path = config[3] or ""

                    try:
                        description = win32service.QueryServiceConfig2(
                            hsvc, win32service.SERVICE_CONFIG_DESCRIPTION
                        ) or ""
                    except Exception:
                        description = ""

                    if startup == "auto":
                        try:
                            delayed = win32service.QueryServiceConfig2(
                                hsvc, win32service.SERVICE_CONFIG_DELAYED_AUTO_START_INFO
                            )
                            if delayed:
                                startup = "auto_delayed"
                        except Exception:
                            pass
                finally:
                    win32service.CloseServiceHandle(hsvc)
            finally:
                win32service.CloseServiceHandle(hscm)

            return ServiceRow(
                name=name, display_name=display_name, status=base_status,
                startup_type=startup, account=account, description=description,
                pid=pid, dependencies=deps, bin_path=bin_path,
            )
        except pywintypes.error as exc:
            logger.debug("No se pudo obtener config de '%s': %s", name, exc)
            return fallback

    def get_status(self, service_name: str) -> str:
        try:
            status = win32serviceutil.QueryServiceStatus(service_name)
            return _SVC_STATUS_MAP.get(status[1], "unknown")
        except pywintypes.error:
            return "unknown"

    def start_service(self, service_name: str) -> Tuple[bool, str]:
        try:
            win32serviceutil.StartService(service_name)
            return True, "OK"
        except pywintypes.error as exc:
            return False, str(exc)

    def _dependientes_activos(self, service_name: str) -> List[str]:
        """Nombres CORTOS de los servicios activos que dependen de este."""
        try:
            hscm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
            try:
                hsvc = win32service.OpenService(hscm, service_name, win32service.SERVICE_ENUMERATE_DEPENDENTS)
                try:
                    return [d[0] for d in win32service.EnumDependentServices(hsvc, win32service.SERVICE_ACTIVE)]
                finally:
                    win32service.CloseServiceHandle(hsvc)
            finally:
                win32service.CloseServiceHandle(hscm)
        except pywintypes.error:
            return []

    def stop_service(self, service_name: str) -> Tuple[bool, str]:
        """5.4.0: detiene antes los servicios que dependen de este (StopServiceWithDeps), como hace
        la consola de Servicios de Windows. Antes se llamaba a StopService a secas: con dependientes en
        marcha Windows lo rechaza (error 1051) aunque MASys avisaba de que «se detendrán también»."""
        try:
            win32serviceutil.StopServiceWithDeps(service_name, waitSecs=30)
            return True, "OK"
        except pywintypes.error as exc:
            return False, str(exc)

    def restart_service(self, service_name: str) -> Tuple[bool, str]:
        """Detiene (con sus dependientes), arranca y vuelve a arrancar los dependientes que estaban en marcha."""
        dependientes = self._dependientes_activos(service_name)
        try:
            win32serviceutil.StopServiceWithDeps(service_name, waitSecs=30)
            win32serviceutil.StartService(service_name)
            win32serviceutil.WaitForServiceStatus(service_name, win32service.SERVICE_RUNNING, 30)
        except pywintypes.error as exc:
            return False, str(exc)
        fallidos = []
        for dep in dependientes:
            try:
                win32serviceutil.StartService(dep)
            except pywintypes.error as exc:
                if getattr(exc, "winerror", None) != 1056:   # 1056 = ya estaba en marcha
                    fallidos.append(f"{dep}: {exc}")
        if fallidos:
            return False, "Reiniciado, pero no se pudieron volver a arrancar: " + "; ".join(fallidos)
        return True, "OK"

    def set_startup_type(self, service_name: str, startup_type: str) -> Tuple[bool, str]:
        """5.4.0: admite «auto_delayed» (antes el desplegable lo ofrecía y fallaba con «Tipo
        desconocido») y al elegir «auto» quita la marca de retraso (ChangeServiceConfig no la toca:
        el servicio seguía «Automático (retrasado)»). Comprobado con un servicio de prueba propio."""
        retrasado = startup_type == "auto_delayed"
        const = win32service.SERVICE_AUTO_START if retrasado else _SVC_START_TYPE_CONST.get(startup_type)
        if const is None:
            return False, f"Tipo desconocido: {startup_type}"
        try:
            hscm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
            try:
                hsvc = win32service.OpenService(hscm, service_name, win32service.SERVICE_CHANGE_CONFIG)
                try:
                    win32service.ChangeServiceConfig(
                        hsvc, win32service.SERVICE_NO_CHANGE, const,
                        win32service.SERVICE_NO_CHANGE, None, None, 0, None, None, None, None,
                    )
                    if const == win32service.SERVICE_AUTO_START:
                        win32service.ChangeServiceConfig2(
                            hsvc, win32service.SERVICE_CONFIG_DELAYED_AUTO_START_INFO, retrasado)
                finally:
                    win32service.CloseServiceHandle(hsvc)
            finally:
                win32service.CloseServiceHandle(hscm)
            return True, "OK"
        except pywintypes.error as exc:
            return False, str(exc)

    def set_bin_path(self, service_name: str, bin_path: str) -> Tuple[bool, str]:
        """Cambia únicamente BinPath (el resto de parámetros van a
        SERVICE_NO_CHANGE) — usado por la reparación de 'ruta de servicio
        sin comillas' de Soporte, que solo necesita envolver el ejecutable
        entre comillas sin tocar cuenta, tipo de inicio ni dependencias."""
        try:
            hscm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
            try:
                hsvc = win32service.OpenService(hscm, service_name, win32service.SERVICE_CHANGE_CONFIG)
                try:
                    win32service.ChangeServiceConfig(
                        hsvc, win32service.SERVICE_NO_CHANGE, win32service.SERVICE_NO_CHANGE,
                        win32service.SERVICE_NO_CHANGE, bin_path, None, 0, None, None, None, None,
                    )
                finally:
                    win32service.CloseServiceHandle(hsvc)
            finally:
                win32service.CloseServiceHandle(hscm)
            return True, "OK"
        except pywintypes.error as exc:
            return False, str(exc)

    def get_dependent_services(self, service_name: str) -> List[str]:
        """Servicios que dependen de ESTE (al revés que ServiceRow.dependencies,
        que son las dependencias DE este servicio) — para poder avisar antes
        de detener/reiniciar de cuáles se detendrían en cascada. Solo cuentan
        los que están activos ahora mismo (SERVICE_ACTIVE): uno ya detenido
        no se ve afectado por detener este."""
        try:
            hscm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_CONNECT)
            try:
                access = win32service.SERVICE_ENUMERATE_DEPENDENTS | win32service.SERVICE_QUERY_STATUS
                hsvc = win32service.OpenService(hscm, service_name, access)
                try:
                    dependientes = win32service.EnumDependentServices(hsvc, win32service.SERVICE_ACTIVE)
                    return [d[1] if isinstance(d, (tuple, list)) and len(d) > 1 else str(d)
                            for d in dependientes]
                finally:
                    win32service.CloseServiceHandle(hsvc)
            finally:
                win32service.CloseServiceHandle(hscm)
        except pywintypes.error as exc:
            logger.debug("No se pudieron obtener dependientes de '%s': %s", service_name, exc)
            return []

    def force_kill_process(self, pid: int) -> Tuple[bool, str]:
        """Finaliza directamente el proceso tras un servicio atascado en un
        estado 'pending' (iniciando/deteniendo/pausando sin resolverse) —
        vía psutil, no SCM: StopService en ese estado normalmente ya falló o
        se ha quedado esperando indefinidamente sin completar la transición."""
        try:
            proc = psutil.Process(pid)
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except psutil.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=3)
            return True, "OK"
        except psutil.NoSuchProcess:
            return True, "OK (el proceso ya no existía)"
        except psutil.AccessDenied as exc:
            return False, f"Acceso denegado (requiere administrador): {exc}"
        except Exception as exc:
            return False, str(exc)


# ---------------------------
# SQL Server: deteccion de instancias (motor + LocalDB)
# ---------------------------
# Deteccion 100% por registro/servicios (winreg + ServiceManager, ya
# importados en este modulo) -- deliberadamente SIN pyodbc ni sqlcmd aqui:
# esta es solo la fase de "que hay instalado", no la de conectar/consultar.
# Cubre los dos hives donde el instalador de SQL Server se registra segun
# arquitectura (64 bits nativo, o WOW6432Node para instaladores de 32 bits
# en un SO de 64 bits -- todavia frecuente en versiones antiguas de Express).

def _sql_leer_valor(hive, ruta: str, nombre: str) -> Optional[str]:
    try:
        with winreg.OpenKey(hive, ruta, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            valor, _ = winreg.QueryValueEx(k, nombre)
            return str(valor) if valor is not None else None
    except (FileNotFoundError, PermissionError, OSError):
        return None


def _sql_enumerar_instancias(hive, ruta_base: str) -> Dict[str, str]:
    """{NombreInstancia: NombreInterno} desde <ruta_base>\\Instance Names\\SQL,
    o {} si la clave no existe (SQL Server no instalado en ese hive)."""
    resultado: Dict[str, str] = {}
    ruta = ruta_base + r"\Instance Names\SQL"
    try:
        with winreg.OpenKey(hive, ruta, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            idx = 0
            while True:
                try:
                    nombre, interno, _ = winreg.EnumValue(k, idx)
                except OSError:
                    break
                idx += 1
                resultado[str(nombre)] = str(interno)
    except (FileNotFoundError, PermissionError, OSError):
        pass
    return resultado


def detectar_sql_server() -> List[SqlInstanceRow]:
    """Motor de base de datos (SQL Server / Express, cualquier edicion) +
    LocalDB. LocalDB no tiene servicio de Windows -- arranca bajo demanda
    por usuario via SqlLocalDB.exe -- asi que sus filas llevan
    estado_servicio='n/a' en vez de consultar un servicio que no existe."""
    filas: List[SqlInstanceRow] = []
    service_mgr = ServiceManager()
    vistos = set()

    for ruta_base in (
        r"SOFTWARE\Microsoft\Microsoft SQL Server",
        r"SOFTWARE\WOW6432Node\Microsoft\Microsoft SQL Server",
    ):
        instancias = _sql_enumerar_instancias(winreg.HKEY_LOCAL_MACHINE, ruta_base)
        for nombre_instancia, interno in instancias.items():
            if interno in vistos:
                continue
            vistos.add(interno)

            ruta_setup = f"{ruta_base}\\{interno}\\Setup"
            version = _sql_leer_valor(winreg.HKEY_LOCAL_MACHINE, ruta_setup, "Version") or "N/A"
            edicion = _sql_leer_valor(winreg.HKEY_LOCAL_MACHINE, ruta_setup, "Edition") or "N/A"
            ruta_datos = (_sql_leer_valor(winreg.HKEY_LOCAL_MACHINE, ruta_setup, "SQLDataRoot")
                          or _sql_leer_valor(winreg.HKEY_LOCAL_MACHINE, ruta_setup, "SQLPath") or "N/A")

            if nombre_instancia.upper() == "MSSQLSERVER":
                svc_motor, svc_agente = "MSSQLSERVER", "SQLSERVERAGENT"
            else:
                svc_motor, svc_agente = f"MSSQL${nombre_instancia}", f"SQLAGENT${nombre_instancia}"

            filas.append(SqlInstanceRow(
                instancia=nombre_instancia,
                tipo="Motor de base de datos",
                version=version,
                edicion=edicion,
                service_name=svc_motor,
                estado_servicio=service_mgr.get_status(svc_motor),
                agent_service_name=svc_agente,
                estado_agente=service_mgr.get_status(svc_agente),
                ruta_datos=ruta_datos,
            ))

    try:
        ruta_localdb = r"SOFTWARE\Microsoft\Microsoft SQL Server Local DB\Installed Versions"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, ruta_localdb, 0,
                             winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            idx = 0
            while True:
                try:
                    version_key = winreg.EnumKey(k, idx)
                except OSError:
                    break
                idx += 1
                filas.append(SqlInstanceRow(
                    instancia=f"LocalDB {version_key}",
                    tipo="LocalDB",
                    version=version_key,
                    edicion="LocalDB",
                    service_name="",
                    estado_servicio="n/a",
                    agent_service_name="",
                    estado_agente="n/a",
                    ruta_datos="N/A (por usuario, bajo demanda)",
                ))
    except (FileNotFoundError, PermissionError, OSError):
        pass

    return filas


class SqlServerCollector(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(list)
    finished_error = pyqtSignal(str)

    def run(self):
        try:
            self.progress.emit(30, "Buscando instancias de SQL Server en el registro...")
            filas = detectar_sql_server()
            self.progress.emit(100, "Completado")
            self.finished_ok.emit(filas)
        except Exception as e:
            logger.exception(f"Error en SqlServerCollector: {e}")
            self.finished_error.emit(str(e))


class ServiceNotesDB:
    """Notas de texto libre por servicio, persistidas en una BD SQLite propia
    de MASys (junto al ejecutable, igual que buscacompartidos_config.json).
    Es la primera BD de la aplicación: no existía ninguna previamente
    (script_launcher.db en la raíz está vacío y sin uso), así que esta pasa
    a ser la BD principal de MASys de aquí en adelante."""

    def __init__(self):
        base_dir = msys_config.app_base_dir()
        self.path = os.path.join(base_dir, "multisys.db")
        self._init_schema()

    def _conn(self):
        return _sqlite_connect(self.path)

    def _init_schema(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS service_notes (
                    service_name TEXT PRIMARY KEY,
                    note_text TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)

    def get_all_notes(self) -> Dict[str, str]:
        with self._conn() as conn:
            cur = conn.execute("SELECT service_name, note_text FROM service_notes")
            return dict(cur.fetchall())

    def save_note(self, service_name: str, note_text: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO service_notes (service_name, note_text, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(service_name) DO UPDATE SET note_text=excluded.note_text, updated_at=excluded.updated_at",
                (service_name, note_text, datetime.now().isoformat(timespec='seconds')),
            )

    def delete_note(self, service_name: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM service_notes WHERE service_name = ?", (service_name,))


class ServiceWhitelistDB:
    """Servicios que el usuario ha marcado explícitamente como 'ya lo
    conozco, no me avises más' desde el detector de Servicios sospechosos
    (Soporte). Misma clave (nombre, ruta) y mismo razonamiento que
    ProcessWhitelistDB: un mismo nombre de servicio podría ser legítimo con
    un BinPath y no con otro (ese es justo el caso que detecta
    _servicio_disfrazado)."""

    def __init__(self):
        base_dir = msys_config.app_base_dir()
        self.path = os.path.join(base_dir, "multisys.db")
        self._init_schema()

    def _conn(self):
        return _sqlite_connect(self.path)

    def _init_schema(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS service_whitelist (
                    service_name TEXT NOT NULL,
                    bin_path TEXT NOT NULL,
                    motivo TEXT NOT NULL DEFAULT '',
                    added_at TEXT NOT NULL,
                    PRIMARY KEY (service_name, bin_path)
                )
            """)

    def is_whitelisted(self, service_name: str, bin_path: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT 1 FROM service_whitelist WHERE service_name = ? AND bin_path = ?",
                (service_name.lower(), bin_path.lower()),
            )
            return cur.fetchone() is not None

    def add(self, service_name: str, bin_path: str, motivo: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO service_whitelist (service_name, bin_path, motivo, added_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(service_name, bin_path) DO UPDATE SET motivo=excluded.motivo, added_at=excluded.added_at",
                (service_name.lower(), bin_path.lower(), motivo, datetime.now().isoformat(timespec='seconds')),
            )

    def remove(self, service_name: str, bin_path: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM service_whitelist WHERE service_name = ? AND bin_path = ?",
                (service_name.lower(), bin_path.lower()),
            )

    def get_all(self) -> List[Tuple[str, str, str, str]]:
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT service_name, bin_path, motivo, added_at FROM service_whitelist ORDER BY added_at DESC")
            return cur.fetchall()


class FirewallRuleWhitelistDB:
    """Reglas de firewall 'amplias' (Any/Any) que el usuario ha marcado
    explícitamente como 'ya la conozco, no me avises más' desde el
    detector de reglas amplias (Soporte). Clave nombre_interno: a
    diferencia de ProcessWhitelistDB/ServiceWhitelistDB no hace falta una
    clave compuesta con la ruta, porque Set-NetFirewallRule ya identifica
    la regla de forma única por su propio Name.

    Todos los métodos atrapan sqlite3.Error: multisys.db vive dentro de la
    carpeta del proyecto sincronizada con OneDrive (ver app_base_dir), y
    ese proveedor puede retener un lock de archivo brevemente durante su
    propio escaneo/sincronizado — sin este try/except, un choque puntual
    con OneDrive lanzaría la excepción sin capturar dentro de un slot de Qt
    (clic de un botón), abortando toda la aplicación sin ningún aviso ni
    traceback visible, en vez de solo fallar esta operación puntual."""

    def __init__(self):
        base_dir = msys_config.app_base_dir()
        self.path = os.path.join(base_dir, "multisys.db")
        self._init_schema()

    def _conn(self):
        return _sqlite_connect(self.path)

    def _init_schema(self):
        try:
            with self._conn() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS firewall_rule_whitelist (
                        nombre_interno TEXT NOT NULL PRIMARY KEY,
                        nombre_visible TEXT NOT NULL DEFAULT '',
                        motivo TEXT NOT NULL DEFAULT '',
                        added_at TEXT NOT NULL
                    )
                """)
        except sqlite3.Error as e:
            logger.error(f"No se pudo crear/verificar la tabla firewall_rule_whitelist: {e}")

    def is_whitelisted(self, nombre_interno: str) -> bool:
        if not nombre_interno:
            return False
        try:
            with self._conn() as conn:
                cur = conn.execute(
                    "SELECT 1 FROM firewall_rule_whitelist WHERE nombre_interno = ?",
                    (nombre_interno,),
                )
                return cur.fetchone() is not None
        except sqlite3.Error as e:
            logger.error(f"No se pudo consultar firewall_rule_whitelist: {e}")
            return False

    def add(self, nombre_interno: str, nombre_visible: str, motivo: str) -> bool:
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO firewall_rule_whitelist (nombre_interno, nombre_visible, motivo, added_at) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(nombre_interno) DO UPDATE SET "
                    "nombre_visible=excluded.nombre_visible, motivo=excluded.motivo, added_at=excluded.added_at",
                    (nombre_interno, nombre_visible, motivo, datetime.now().isoformat(timespec='seconds')),
                )
            return True
        except sqlite3.Error as e:
            logger.error(f"No se pudo guardar en firewall_rule_whitelist: {e}")
            return False

    def remove(self, nombre_interno: str) -> bool:
        try:
            with self._conn() as conn:
                conn.execute(
                    "DELETE FROM firewall_rule_whitelist WHERE nombre_interno = ?", (nombre_interno,))
            return True
        except sqlite3.Error as e:
            logger.error(f"No se pudo eliminar de firewall_rule_whitelist: {e}")
            return False

    def get_all(self) -> List[Tuple[str, str, str, str]]:
        try:
            with self._conn() as conn:
                cur = conn.execute(
                    "SELECT nombre_interno, nombre_visible, motivo, added_at "
                    "FROM firewall_rule_whitelist ORDER BY added_at DESC")
                return cur.fetchall()
        except sqlite3.Error as e:
            logger.error(f"No se pudo leer firewall_rule_whitelist: {e}")
            return []


class PortWhitelistDB:
    """Puertos 'no comunes' que el usuario ha marcado explícitamente como
    'ya lo comprobé, no me avises más' desde el analizador de puertos no
    comunes (Soporte). Clave (puerto, ruta_ejecutable) y no solo puerto:
    el mismo puerto podría pasar a servir a un programa distinto en el
    futuro (p. ej. tras desinstalar el original), y eso sí merece
    avisar de nuevo — mismo razonamiento que ProcessWhitelistDB.

    Mismo try/except sqlite3.Error en todos los métodos que
    FirewallRuleWhitelistDB, y por la misma razón (multisys.db dentro de
    la carpeta sincronizada con OneDrive)."""

    def __init__(self):
        base_dir = msys_config.app_base_dir()
        self.path = os.path.join(base_dir, "multisys.db")
        self._init_schema()

    def _conn(self):
        return _sqlite_connect(self.path)

    def _init_schema(self):
        try:
            with self._conn() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS port_whitelist (
                        puerto INTEGER NOT NULL,
                        ruta_ejecutable TEXT NOT NULL,
                        proceso TEXT NOT NULL DEFAULT '',
                        motivo TEXT NOT NULL DEFAULT '',
                        added_at TEXT NOT NULL,
                        PRIMARY KEY (puerto, ruta_ejecutable)
                    )
                """)
        except sqlite3.Error as e:
            logger.error(f"No se pudo crear/verificar la tabla port_whitelist: {e}")

    def is_whitelisted(self, puerto: int, ruta_ejecutable: str) -> bool:
        try:
            with self._conn() as conn:
                cur = conn.execute(
                    "SELECT 1 FROM port_whitelist WHERE puerto = ? AND ruta_ejecutable = ?",
                    (puerto, (ruta_ejecutable or "").lower()),
                )
                return cur.fetchone() is not None
        except sqlite3.Error as e:
            logger.error(f"No se pudo consultar port_whitelist: {e}")
            return False

    def add(self, puerto: int, ruta_ejecutable: str, proceso: str, motivo: str) -> bool:
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO port_whitelist (puerto, ruta_ejecutable, proceso, motivo, added_at) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT(puerto, ruta_ejecutable) DO UPDATE SET "
                    "proceso=excluded.proceso, motivo=excluded.motivo, added_at=excluded.added_at",
                    (puerto, (ruta_ejecutable or "").lower(), proceso, motivo,
                     datetime.now().isoformat(timespec='seconds')),
                )
            return True
        except sqlite3.Error as e:
            logger.error(f"No se pudo guardar en port_whitelist: {e}")
            return False

    def remove(self, puerto: int, ruta_ejecutable: str) -> bool:
        try:
            with self._conn() as conn:
                conn.execute(
                    "DELETE FROM port_whitelist WHERE puerto = ? AND ruta_ejecutable = ?",
                    (puerto, (ruta_ejecutable or "").lower()),
                )
            return True
        except sqlite3.Error as e:
            logger.error(f"No se pudo eliminar de port_whitelist: {e}")
            return False

    def get_all(self) -> List[Tuple[int, str, str, str, str]]:
        try:
            with self._conn() as conn:
                cur = conn.execute(
                    "SELECT puerto, ruta_ejecutable, proceso, motivo, added_at "
                    "FROM port_whitelist ORDER BY added_at DESC")
                return cur.fetchall()
        except sqlite3.Error as e:
            logger.error(f"No se pudo leer port_whitelist: {e}")
            return []


class BlankPasswordWhitelistDB:
    """Cuentas de usuario que el técnico ha confirmado explícitamente como
    'ya lo sé, permite contraseña en blanco a propósito, no me avises más'
    desde el detector de Usuarios (Soporte). Clave SID (no nombre): un SID
    es la identidad estable de la cuenta incluso si se renombra; el nombre
    no. Mismo try/except sqlite3.Error que el resto de *WhitelistDB
    (multisys.db vive en la carpeta del proyecto sincronizada con
    OneDrive, que puede retener un lock de archivo brevemente)."""

    def __init__(self):
        base_dir = msys_config.app_base_dir()
        self.path = os.path.join(base_dir, "multisys.db")
        self._init_schema()

    def _conn(self):
        return _sqlite_connect(self.path)

    def _init_schema(self):
        try:
            with self._conn() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS blank_password_whitelist (
                        sid TEXT NOT NULL PRIMARY KEY,
                        nombre TEXT NOT NULL DEFAULT '',
                        motivo TEXT NOT NULL DEFAULT '',
                        added_at TEXT NOT NULL
                    )
                """)
        except sqlite3.Error as e:
            logger.error(f"No se pudo crear/verificar la tabla blank_password_whitelist: {e}")

    def is_whitelisted(self, sid: str) -> bool:
        try:
            with self._conn() as conn:
                cur = conn.execute(
                    "SELECT 1 FROM blank_password_whitelist WHERE sid = ?", (sid,))
                return cur.fetchone() is not None
        except sqlite3.Error as e:
            logger.error(f"No se pudo consultar blank_password_whitelist: {e}")
            return False

    def add(self, sid: str, nombre: str, motivo: str) -> bool:
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO blank_password_whitelist (sid, nombre, motivo, added_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(sid) DO UPDATE SET nombre=excluded.nombre, motivo=excluded.motivo, added_at=excluded.added_at",
                    (sid, nombre, motivo, datetime.now().isoformat(timespec='seconds')),
                )
            return True
        except sqlite3.Error as e:
            logger.error(f"No se pudo guardar en blank_password_whitelist: {e}")
            return False

    def remove(self, sid: str) -> bool:
        try:
            with self._conn() as conn:
                conn.execute("DELETE FROM blank_password_whitelist WHERE sid = ?", (sid,))
            return True
        except sqlite3.Error as e:
            logger.error(f"No se pudo eliminar de blank_password_whitelist: {e}")
            return False

    def get_all(self) -> List[Tuple[str, str, str, str]]:
        try:
            with self._conn() as conn:
                cur = conn.execute(
                    "SELECT sid, nombre, motivo, added_at FROM blank_password_whitelist ORDER BY added_at DESC")
                return cur.fetchall()
        except sqlite3.Error as e:
            logger.error(f"No se pudo leer blank_password_whitelist: {e}")
            return []


class SuspiciousCertificateWhitelistDB:
    """Certificados que el técnico ha confirmado explícitamente como 'ya lo
    sé, no me avises más' desde el detector de certificados sospechosos
    (Soporte) — algoritmo de firma débil o autofirmado en \\My. Clave huella
    digital (thumbprint): identidad estable del certificado, a diferencia
    del asunto (que puede repetirse entre certificados distintos). Mismo
    try/except sqlite3.Error que el resto de *WhitelistDB (multisys.db vive
    en la carpeta del proyecto sincronizada con OneDrive, que puede retener
    un lock de archivo brevemente)."""

    def __init__(self):
        base_dir = msys_config.app_base_dir()
        self.path = os.path.join(base_dir, "multisys.db")
        self._init_schema()

    def _conn(self):
        return _sqlite_connect(self.path)

    def _init_schema(self):
        try:
            with self._conn() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS suspicious_cert_whitelist (
                        huella_digital TEXT NOT NULL PRIMARY KEY,
                        asunto TEXT NOT NULL DEFAULT '',
                        motivo TEXT NOT NULL DEFAULT '',
                        added_at TEXT NOT NULL
                    )
                """)
        except sqlite3.Error as e:
            logger.error(f"No se pudo crear/verificar la tabla suspicious_cert_whitelist: {e}")

    def is_whitelisted(self, huella_digital: str) -> bool:
        try:
            with self._conn() as conn:
                cur = conn.execute(
                    "SELECT 1 FROM suspicious_cert_whitelist WHERE huella_digital = ?", (huella_digital,))
                return cur.fetchone() is not None
        except sqlite3.Error as e:
            logger.error(f"No se pudo consultar suspicious_cert_whitelist: {e}")
            return False

    def add(self, huella_digital: str, asunto: str, motivo: str) -> bool:
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO suspicious_cert_whitelist (huella_digital, asunto, motivo, added_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(huella_digital) DO UPDATE SET asunto=excluded.asunto, motivo=excluded.motivo, added_at=excluded.added_at",
                    (huella_digital, asunto, motivo, datetime.now().isoformat(timespec='seconds')),
                )
            return True
        except sqlite3.Error as e:
            logger.error(f"No se pudo guardar en suspicious_cert_whitelist: {e}")
            return False

    def remove(self, huella_digital: str) -> bool:
        try:
            with self._conn() as conn:
                conn.execute("DELETE FROM suspicious_cert_whitelist WHERE huella_digital = ?", (huella_digital,))
            return True
        except sqlite3.Error as e:
            logger.error(f"No se pudo eliminar de suspicious_cert_whitelist: {e}")
            return False

    def get_all(self) -> List[Tuple[str, str, str, str]]:
        try:
            with self._conn() as conn:
                cur = conn.execute(
                    "SELECT huella_digital, asunto, motivo, added_at FROM suspicious_cert_whitelist ORDER BY added_at DESC")
                return cur.fetchall()
        except sqlite3.Error as e:
            logger.error(f"No se pudo leer suspicious_cert_whitelist: {e}")
            return []


class SuspiciousDeviceDB:
    """Dispositivos de la Red Local que el técnico ha marcado manualmente
    como 'sospechosos' (a vigilar) — lo contrario de un whitelist: aquí no
    se guarda para dejar de avisar, se guarda para SEGUIR avisando en cada
    escaneo futuro, incluso si el dispositivo cambia de IP (DHCP). Clave
    MAC (no IP) precisamente por eso — la MAC es la identidad estable del
    dispositivo, la IP no. Mismo try/except sqlite3.Error que el resto de
    *WhitelistDB (multisys.db vive en la carpeta del proyecto sincronizada
    con OneDrive, ver FirewallRuleWhitelistDB)."""

    def __init__(self):
        base_dir = msys_config.app_base_dir()
        self.path = os.path.join(base_dir, "multisys.db")
        self._init_schema()

    def _conn(self):
        return _sqlite_connect(self.path)

    def _init_schema(self):
        try:
            with self._conn() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS suspicious_devices (
                        mac TEXT NOT NULL PRIMARY KEY,
                        ip_visto TEXT NOT NULL DEFAULT '',
                        motivo TEXT NOT NULL DEFAULT '',
                        marcado_el TEXT NOT NULL
                    )
                """)
        except sqlite3.Error as e:
            logger.error(f"No se pudo crear/verificar la tabla suspicious_devices: {e}")

    def is_marked(self, mac: str) -> bool:
        if not mac:
            return False
        try:
            with self._conn() as conn:
                cur = conn.execute(
                    "SELECT 1 FROM suspicious_devices WHERE mac = ?", (mac.strip().lower(),))
                return cur.fetchone() is not None
        except sqlite3.Error as e:
            logger.error(f"No se pudo consultar suspicious_devices: {e}")
            return False

    def add(self, mac: str, ip_visto: str, motivo: str) -> bool:
        try:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO suspicious_devices (mac, ip_visto, motivo, marcado_el) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(mac) DO UPDATE SET "
                    "ip_visto=excluded.ip_visto, motivo=excluded.motivo, marcado_el=excluded.marcado_el",
                    (mac.strip().lower(), ip_visto, motivo, datetime.now().isoformat(timespec='seconds')),
                )
            return True
        except sqlite3.Error as e:
            logger.error(f"No se pudo guardar en suspicious_devices: {e}")
            return False

    def remove(self, mac: str) -> bool:
        try:
            with self._conn() as conn:
                conn.execute("DELETE FROM suspicious_devices WHERE mac = ?", (mac.strip().lower(),))
            return True
        except sqlite3.Error as e:
            logger.error(f"No se pudo eliminar de suspicious_devices: {e}")
            return False

    def get_all(self) -> List[Tuple[str, str, str, str]]:
        try:
            with self._conn() as conn:
                cur = conn.execute(
                    "SELECT mac, ip_visto, motivo, marcado_el FROM suspicious_devices ORDER BY marcado_el DESC")
                return cur.fetchall()
        except sqlite3.Error as e:
            logger.error(f"No se pudo leer suspicious_devices: {e}")
            return []


class ServiceCollector(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(list)
    finished_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        """Solicita detener el análisis de forma controlada."""
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(20, "Escaneando servicios de Windows (SCM)...")
            rows = ServiceManager().list_all_services()
            self.progress.emit(100, "Completado")
            self.finished_ok.emit(rows)
        except Exception as e:
            logger.exception(f"Error en ServiceCollector: {e}")
            self.finished_error.emit(str(e))

class _HostnameResolverThread(QThread):
    """Resuelve un hostname por DNS inverso fuera del hilo de la UI: si el
    servidor DNS es lento o la IP no tiene PTR, gethostbyaddr puede tardar
    varios segundos y congelaba la ventana al llamarse directamente desde
    el menú contextual de Conexiones Red."""
    resuelto = pyqtSignal(str, str, str)   # ip, hostname (vacío si falla), error

    def __init__(self, ip: str):
        super().__init__()
        self._ip = ip

    def run(self):
        try:
            import socket as _sock
            hostname = _sock.gethostbyaddr(self._ip)[0]
            self.resuelto.emit(self._ip, hostname, "")
        except Exception as e:
            self.resuelto.emit(self._ip, "", str(e))


import concurrent.futures as _concurrent_futures

# BUG REAL: socket.gethostbyaddr() en Windows NO respeta
# socket.setdefaulttimeout() — es una llamada bloqueante a la API de
# resolución del sistema operativo, no pasa por el socket normal de
# Python. Confirmado empíricamente: 10.4 segundos para UNA sola IP sin
# PTR inverso configurado (lo normal en cualquier red doméstica/de
# oficina pequeña), incluso con el timeout puesto a 2s. Sin acotarlo,
# enriquecer una tabla de 18-50 dispositivos podía tardar minutos en vez
# de segundos. Se envía a un pool de hilos propio y se acota la ESPERA
# (no la llamada en sí, que sigue corriendo huérfana en su hilo hasta que
# el propio SO responda) con future.result(timeout=...) — nunca dentro de
# un 'with' que haría shutdown(wait=True) y volvería a bloquear igual.
_pool_resolucion_dns = _concurrent_futures.ThreadPoolExecutor(
    max_workers=30, thread_name_prefix="red-local-dns")


def _resolver_nombre_dns_o_netbios(ip: str) -> str:
    """DNS inverso primero (acotado a 1.5s de espera real, ver
    _pool_resolucion_dns); si no hay PTR o tarda más de eso, se cae a una
    consulta NetBIOS (nbtstat -A) — funciona porque cualquier Windows en
    la LAN responde a NetBIOS aunque nadie le haya configurado DNS. El
    código de servicio NetBIOS <20> (File Server Service) identifica el
    nombre real del equipo de forma fiable en CUALQUIER idioma de Windows
    — a diferencia de la palabra 'Único'/'UNIQUE' vs 'Grupo'/'GROUP' de la
    salida (esa sí viene traducida según el idioma del Windows que
    ejecuta MASys, y no sirve para identificar la fila)."""
    try:
        future = _pool_resolucion_dns.submit(socket.gethostbyaddr, ip)
        return future.result(timeout=1.5)[0]
    except Exception:
        pass
    # 5.4.0: consulta NetBIOS propia por UDP 137 en vez de «nbtstat -A». Antes solo se aceptaba el
    # nombre con código <20> (servicio Servidor): un equipo que solo anuncia <00> se quedaba sin nombre
    # (comprobado: dos equipos de la red de pruebas) y nbtstat tardaba ~9 s por
    # recorrer todos los adaptadores. Ahora: nombre ÚNICO <00> o <20>, por el bit de grupo (no por el
    # texto «Único/UNIQUE», que va traducido), en milisegundos.
    try:
        return _nombre_netbios(ip)
    except Exception:
        return ""


def _nombre_netbios(ip: str, timeout: float = 1.0) -> str:
    """Consulta de estado de nodo NetBIOS (NBSTAT, RFC 1002 §4.2.17) por UDP 137. Devuelve el nombre
    único del equipo (<00> preferido, si no <20>) o ''."""
    import os as _os
    import struct as _struct
    tid = _os.urandom(2)
    nombre = b"*" + b"\x00" * 15
    codificado = bytes(x for c in nombre for x in (0x41 + (c >> 4), 0x41 + (c & 0x0F)))
    paquete = (tid + b"\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00" + b"\x20" + codificado + b"\x00"
               + b"\x00\x21\x00\x01")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        try:
            s.sendto(paquete, (ip, 137))
            datos, _ = s.recvfrom(2048)
        except OSError:
            return ""
    if len(datos) < 57 or datos[:2] != tid:
        return ""
    pos = 12
    while pos < len(datos) and datos[pos] != 0:
        if datos[pos] & 0xC0 == 0xC0:      # puntero de compresión (2 bytes)
            pos += 1
            break
        pos += datos[pos] + 1
    pos += 1 + 2 + 2 + 4 + 2               # fin del nombre, tipo, clase, TTL, longitud de datos
    if pos >= len(datos):
        return ""
    total = datos[pos]
    pos += 1
    candidatos = []
    for _ in range(total):
        if pos + 18 > len(datos):
            break
        nb = datos[pos:pos + 15].decode("latin-1").strip()
        sufijo = datos[pos + 15]
        flags = _struct.unpack(">H", datos[pos + 16:pos + 18])[0]
        pos += 18
        if nb and not (flags & 0x8000) and sufijo in (0x00, 0x20):
            candidatos.append((0 if sufijo == 0x00 else 1, nb))
    return sorted(candidatos)[0][1] if candidatos else ""


def _comprobar_acceso_web_ip(ip: str) -> str:
    """Prueba de conexión TCP directa (sin PowerShell) a 80/443 — típico
    de routers/impresoras/NAS/cámaras IP con interfaz de gestión web.
    Timeout corto a propósito: se llama para MUCHOS dispositivos a la vez
    en paralelo tras cada escaneo."""
    abiertos = []
    for puerto, nombre in ((80, "http"), (443, "https")):
        try:
            with socket.create_connection((ip, puerto), timeout=0.5):
                abiertos.append(nombre)
        except OSError:
            pass
    return " + ".join(abiertos) if abiertos else "No detectado"


class DeviceEnrichmentThread(QThread):
    """Enriquece EN PARALELO (ThreadPoolExecutor dentro de este único
    QThread, no un QThread por dispositivo) la lista de dispositivos de
    Red Local recién escaneada: nombre de host (DNS + NetBIOS) y acceso
    web (80/443). Se lanza automáticamente tras cada escaneo — antes había
    que comprobarlo dispositivo a dispositivo a mano desde el menú
    contextual. El peor caso por dispositivo es ~10-12s (nbtstat
    consultando todos los adaptadores locales, ver
    _resolver_nombre_dns_o_netbios), pero al ir todos en PARALELO el
    tiempo total del lote queda dominado por ese peor caso UNA sola vez,
    no multiplicado por el número de dispositivos — con 18 dispositivos
    reales medido en ~10-13s totales, no ~18×10s."""
    dispositivo_enriquecido = pyqtSignal(str, str, str)  # ip, hostname, acceso_web
    progress = pyqtSignal(int, str)

    def __init__(self, ips):
        super().__init__()
        self._ips = list(ips)

    def run(self):
        import concurrent.futures
        if not self._ips:
            return

        def _procesar(ip):
            return ip, _resolver_nombre_dns_o_netbios(ip), _comprobar_acceso_web_ip(ip)

        total = len(self._ips)
        completados = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=20) as executor:
            for ip, hostname, acceso_web in executor.map(_procesar, self._ips):
                completados += 1
                self.progress.emit(
                    int(completados / total * 100),
                    f"Identificando dispositivos... {completados}/{total}")
                self.dispositivo_enriquecido.emit(ip, hostname, acceso_web)


class NetworkConnectionCollector(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(list)
    finished_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        """Solicita detener el análisis de forma controlada."""
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(20, "Escaneando conexiones de red...")
            connections = run_powershell_json(PS_GET_NET_CONNECTIONS, timeout=30)

            if connections is None:
                self.finished_error.emit(
                    "Tiempo agotado o error al escanear las conexiones de red.")
                return
            if not connections:
                self.progress.emit(100, "No se encontraron conexiones")
                self.finished_ok.emit([])
                return

            if isinstance(connections, dict):
                connections = [connections]

            rows = []
            total = len(connections)
            for idx, c in enumerate(connections):
                if self._stop_requested:
                    self.progress.emit(100, "Análisis detenido por el usuario")
                    self.finished_ok.emit(rows)
                    return

                self.progress.emit(40 + int((idx / total) * 60), f"Procesando conexión {idx+1}/{total}...")

                rows.append(NetworkConnectionRow(
                    protocolo=c.get("Protocolo", ""),
                    direccion_local=c.get("DireccionLocal", ""),
                    direccion_remota=c.get("DireccionRemota", ""),
                    estado=c.get("Estado", ""),
                    proceso=c.get("Proceso", "N/A"),
                    pid=str(c.get("PID", "")),
                    exe_path=c.get("RutaEjecutable") or "",
                ))

            self.progress.emit(100, "Completado")
            self.finished_ok.emit(rows)
        except Exception as e:
            logger.exception(f"Error en NetworkConnectionCollector: {e}")
            self.finished_error.emit(str(e))

class InstalledSoftwareCollector(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(list)
    finished_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        """Solicita detener el análisis de forma controlada."""
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(20, "Escaneando software instalado...")
            software = run_powershell_json(PS_GET_INSTALLED_SOFTWARE, timeout=60)

            if software is None:
                self.progress.emit(100, "No se pudo completar el escaneo")
                self.finished_error.emit(
                    "Tiempo agotado o error al consultar el software instalado del sistema.")
                return

            if not software:
                self.progress.emit(100, "No se encontró software instalado")
                self.finished_ok.emit([])
                return

            if isinstance(software, dict):
                software = [software]

            rows = []
            total = len(software)
            for idx, sw in enumerate(software):
                if self._stop_requested:
                    self.progress.emit(100, "Análisis detenido por el usuario")
                    self.finished_ok.emit(rows)
                    return

                self.progress.emit(40 + int((idx / total) * 60), f"Procesando software {idx+1}/{total}...")

                rows.append(InstalledSoftwareRow(
                    nombre=sw.get("Nombre") or "",
                    version=sw.get("Version") or "N/A",
                    editor=sw.get("Editor") or "N/A",
                    fecha_instalacion=sw.get("FechaInstalacion") or "N/A",
                    carpeta_instalacion=sw.get("CarpetaInstalacion") or "N/A",
                    uninstall_string=sw.get("UninstallString") or "",
                    system_component=bool(sw.get("SystemComponent", False)),
                ))

            self.progress.emit(100, "Completado")
            self.finished_ok.emit(rows)
        except Exception as e:
            logger.exception(f"Error en InstalledSoftwareCollector: {e}")
            self.finished_error.emit(str(e))

class ScheduledTaskCollector(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(list)
    finished_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        """Solicita detener el análisis de forma controlada."""
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(20, "Escaneando tareas programadas...")
            tasks = run_powershell_json(PS_GET_SCHEDULED_TASKS, timeout=45)

            if tasks is None:
                self.finished_error.emit(
                    "Tiempo agotado o error al escanear las tareas programadas.")
                return
            if not tasks:
                self.progress.emit(100, "No se encontraron tareas programadas")
                self.finished_ok.emit([])
                return

            if isinstance(tasks, dict):
                tasks = [tasks]

            rows = []
            total = len(tasks)
            for idx, t in enumerate(tasks):
                if self._stop_requested:
                    self.progress.emit(100, "Análisis detenido por el usuario")
                    self.finished_ok.emit(rows)
                    return

                self.progress.emit(40 + int((idx / total) * 60), f"Procesando tarea {idx+1}/{total}...")

                # ConvertTo-Json colapsa un array de 1 elemento a un objeto
                # suelto — mismo patrón defensivo que ya se aplica a $tareas
                # en sí (ver más arriba) — aquí se repite a nivel de
                # Disparadores por si acaso, aunque al ser una propiedad
                # anidada (no la salida directa del pipeline) no debería
                # colapsar en la práctica.
                disparadores_raw = t.get("Disparadores") or []
                if isinstance(disparadores_raw, dict):
                    disparadores_raw = [disparadores_raw]
                disparadores = [scheduled_task_trigger_desde_ps(d) for d in disparadores_raw]

                rows.append(ScheduledTaskRow(
                    nombre=t.get("Nombre", ""),
                    estado=t.get("Estado", ""),
                    proxima_ejecucion=t.get("ProximaEjecucion", "N/A"),
                    ultima_ejecucion=t.get("UltimaEjecucion", "N/A"),
                    ultimo_resultado=str(t.get("UltimoResultado", "N/A")),
                    ejecuta_como=t.get("EjecutaComo", "") or "",
                    oculta=bool(t.get("Oculta", False)),
                    autor=t.get("Autor", "") or "",
                    accion=t.get("Accion", "") or "",
                    ruta=t.get("Ruta", "") or "\\",
                    ejecutable_principal=t.get("EjecutablePrincipal", "") or "",
                    fecha_creacion=t.get("FechaCreacion", "N/A") or "N/A",
                    disparadores=disparadores,
                ))

            self.progress.emit(100, "Completado")
            self.finished_ok.emit(rows)
        except Exception as e:
            logger.exception(f"Error en ScheduledTaskCollector: {e}")
            self.finished_error.emit(str(e))


# -TaskName y -TaskPath admiten comodines (comprobado: «A[d]obe Acrobat Update Task» encuentra «Adobe Acrobat Update
# Task»): con corchetes en el nombre se podía activar, lanzar o BORRAR otra tarea. Se busca por nombre y ruta exactos.
_PS_TAREA_EXACTA = ("$t = @(Get-ScheduledTask | Where-Object {{ $_.TaskName -eq '{nombre}' -and $_.TaskPath -eq '{ruta}' }}); "
                    "if ($t.Count -ne 1) {{ throw 'No se encuentra la tarea' }}; ")


class ScheduledTaskManager:
    """Acciones sobre una tarea programada por Nombre+Ruta (TaskPath) — el
    nombre solo no es único: dos tareas en carpetas distintas del
    Programador de tareas pueden compartir nombre, así que toda acción
    exige ambos para no arriesgarse a tocar la tarea equivocada. Siempre
    vía run_powershell_command_params (nombre y ruta escapados
    automáticamente)."""

    def enable(self, nombre: str, ruta: str) -> Tuple[bool, str]:
        return run_powershell_command_params(
            _PS_TAREA_EXACTA + "$t[0] | Enable-ScheduledTask | Out-Null", nombre=nombre, ruta=ruta)

    def disable(self, nombre: str, ruta: str) -> Tuple[bool, str]:
        return run_powershell_command_params(
            _PS_TAREA_EXACTA + "$t[0] | Disable-ScheduledTask | Out-Null", nombre=nombre, ruta=ruta)

    def run_now(self, nombre: str, ruta: str) -> Tuple[bool, str]:
        return run_powershell_command_params(
            _PS_TAREA_EXACTA + "$t[0] | Start-ScheduledTask", nombre=nombre, ruta=ruta)

    def delete(self, nombre: str, ruta: str) -> Tuple[bool, str]:
        return run_powershell_command_params(
            _PS_TAREA_EXACTA + "$t[0] | Unregister-ScheduledTask -Confirm:$false",
            nombre=nombre, ruta=ruta)


class PrinterCollector(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(list)
    finished_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        """Solicita detener el análisis de forma controlada."""
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(20, "Escaneando impresoras...")
            printers = run_powershell_json(PS_GET_PRINTERS, timeout=30)

            # None = error/timeout real de PowerShell; [] = sin impresoras
            # instaladas (resultado válido). Antes se trataban igual y un
            # fallo real de la consulta se mostraba como "0 impresoras"
            # sin avisar — mismo bug ya corregido en Controladores.
            if printers is None:
                self.progress.emit(100, "No se pudo completar el escaneo")
                self.finished_error.emit(
                    "Tiempo agotado o error al consultar las impresoras del sistema.")
                return

            if not printers:
                self.progress.emit(100, "No se encontraron impresoras")
                self.finished_ok.emit([])
                return

            if isinstance(printers, dict):
                printers = [printers]

            rows = []
            total = len(printers)
            for idx, pr in enumerate(printers):
                if self._stop_requested:
                    self.progress.emit(100, "Análisis detenido por el usuario")
                    self.finished_ok.emit(rows)
                    return

                self.progress.emit(40 + int((idx / total) * 60), f"Procesando impresora {idx+1}/{total}...")

                rows.append(PrinterRow(
                    nombre=pr.get("Nombre", ""),
                    tipo=pr.get("Tipo", ""),
                    puerto=pr.get("Puerto", ""),
                    estado=pr.get("Estado", ""),
                    compartida=pr.get("Compartida", "No"),
                    predeterminada=pr.get("Predeterminada", "No")
                ))

            self.progress.emit(100, "Completado")
            self.finished_ok.emit(rows)
        except Exception as e:
            logger.exception(f"Error en PrinterCollector: {e}")
            self.finished_error.emit(str(e))


class PrinterManager:
    """Acciones sobre una impresora por su Nombre — a diferencia de PnP
    (DriverManager), el subsistema de impresión de Windows identifica una
    impresora por Name, no por un ID aparte (Get-Printer, Remove-Printer y los
    métodos de Win32_Printer funcionan así). Siempre
    vía run_powershell_command_params (escapa el nombre automáticamente,
    nunca interpolado a mano — mismo criterio de seguridad que el resto
    del proyecto tras la revisión de Credenciales)."""

    # 5.4.0: «Suspend-Printer»/«Resume-Printer» NO existen en Windows (el módulo PrintManagement solo
    # tiene Suspend-/Resume-PrintJob): pausar y reanudar impresora fallaban SIEMPRE. Se usan los métodos
    # de Win32_Printer (Pause, Resume, SetDefaultPrinter, PrintTestPage) y se comprueba su ReturnValue.
    # La impresora se busca por nombre con Where-Object, no con -Filter "Name='…'": en WQL la barra
    # invertida hay que doblarla y las impresoras de red («\\servidor\cola») no se encontraban.
    # Probado con una impresora de prueba propia (pausar → Paused, reanudar → Normal).
    _PS_METODO_IMPRESORA = (
        "$p = Get-CimInstance Win32_Printer | Where-Object {{ $_.Name -eq '{nombre}' }} | Select-Object -First 1; "
        "if (-not $p) {{ throw 'No se encuentra la impresora' }}; "
        "$r = Invoke-CimMethod -InputObject $p -MethodName METODO; "
        "if ($r.ReturnValue -ne 0) {{ throw \"Windows devolvió el código $($r.ReturnValue)\" }}")

    def _metodo_impresora(self, nombre: str, metodo: str) -> Tuple[bool, str]:
        return run_powershell_command_params(self._PS_METODO_IMPRESORA.replace("METODO", metodo), nombre=nombre)

    def set_default(self, nombre: str) -> Tuple[bool, str]:
        return self._metodo_impresora(nombre, "SetDefaultPrinter")

    def print_test_page(self, nombre: str) -> Tuple[bool, str]:
        return self._metodo_impresora(nombre, "PrintTestPage")

    def pause(self, nombre: str) -> Tuple[bool, str]:
        return self._metodo_impresora(nombre, "Pause")

    def resume(self, nombre: str) -> Tuple[bool, str]:
        return self._metodo_impresora(nombre, "Resume")

    def remove(self, nombre: str) -> Tuple[bool, str]:
        # -Name admite comodines (comprobado: Get-Printer -Name 'Micro[s]oft Print to PDF' encuentra «Microsoft Print
        # to PDF»): con corchetes en el nombre se podía borrar OTRA impresora. Se busca por nombre exacto.
        return run_powershell_command_params(
            "$p = @(Get-Printer | Where-Object {{ $_.Name -eq '{nombre}' }}); "
            "if ($p.Count -ne 1) {{ throw 'No se encuentra la impresora' }}; "
            "Remove-Printer -InputObject $p[0]", nombre=nombre)

    def get_print_jobs(self, nombre: str) -> Tuple[bool, Any]:
        """Trabajos en la cola de una impresora (Get-PrintJob). Devuelve
        (True, lista_dicts) o (False, mensaje_error)."""
        datos = run_powershell_json_params(
            "@(Get-PrintJob -PrinterName '{nombre}' -ErrorAction Stop | "
            "Select-Object Id, DocumentName, JobStatus, TotalPages, Size, "
            "@{{N='Owner';E={{$_.UserName}}}}, "
            "@{{N='Enviado';E={{$_.SubmittedTime}}}}) | ConvertTo-Json -Compress",
            nombre=nombre, timeout=20,
        )
        if datos is None:
            return False, "Tiempo agotado o error al consultar la cola de impresión."
        if isinstance(datos, dict):
            datos = [datos]
        return True, datos

    def pause_job(self, nombre: str, job_id: int) -> Tuple[bool, str]:
        return run_powershell_command_params(
            "Suspend-PrintJob -PrinterName '{nombre}' -ID {job_id}", nombre=nombre, job_id=job_id)

    def resume_job(self, nombre: str, job_id: int) -> Tuple[bool, str]:
        return run_powershell_command_params(
            "Resume-PrintJob -PrinterName '{nombre}' -ID {job_id}", nombre=nombre, job_id=job_id)

    def remove_job(self, nombre: str, job_id: int) -> Tuple[bool, str]:
        return run_powershell_command_params(
            "Remove-PrintJob -PrinterName '{nombre}' -ID {job_id}", nombre=nombre, job_id=job_id)


class FirewallRuleCollector(QThread):
    """Reglas de firewall + estado de los 3 perfiles (Dominio/Privado/
    Público) en la misma pasada — el estado de perfil es el control más
    importante de todos (si un perfil está desactivado, sus reglas no
    importan), antes esta sección no lo comprobaba."""
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(dict)  # {"reglas": [FirewallRuleRow], "perfiles": [FirewallProfileRow]}
    finished_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        """Solicita detener el análisis de forma controlada."""
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(20, "Escaneando reglas y perfiles de firewall...")
            data = run_powershell_json(PS_GET_FIREWALL_RULES, timeout=60)

            if data is None:
                self.finished_error.emit(
                    "Tiempo agotado o error al escanear las reglas de firewall.")
                return
            if not isinstance(data, dict):
                data = {}

            reglas_json = data.get("Reglas") or []
            if isinstance(reglas_json, dict):
                reglas_json = [reglas_json]
            perfiles_json = data.get("Perfiles") or []
            if isinstance(perfiles_json, dict):
                perfiles_json = [perfiles_json]

            perfiles = [FirewallProfileRow(
                nombre=p.get("Nombre", ""),
                habilitado=bool(p.get("Habilitado", False)),
            ) for p in perfiles_json]

            rows = []
            total = len(reglas_json)
            for idx, r in enumerate(reglas_json):
                if self._stop_requested:
                    self.progress.emit(100, "Análisis detenido por el usuario")
                    self.finished_ok.emit({"reglas": rows, "perfiles": perfiles})
                    return

                self.progress.emit(40 + int((idx / max(total, 1)) * 60), f"Procesando regla {idx+1}/{total}...")

                rows.append(FirewallRuleRow(
                    nombre=r.get("Nombre", ""),
                    direccion=r.get("Direccion", ""),
                    accion=r.get("Accion", ""),
                    protocolo=r.get("Protocolo", ""),
                    puerto=r.get("Puerto", "N/A"),
                    perfil=r.get("Perfil", ""),
                    habilitada=r.get("Habilitada", "N/A"),
                    nombre_interno=r.get("NombreInterno", ""),
                    es_sistema=bool(r.get("EsSistema", False)),
                    programa=r.get("Programa") or "N/A",
                ))

            self.progress.emit(100, "Completado")
            self.finished_ok.emit({"reglas": rows, "perfiles": perfiles})
        except Exception as e:
            logger.exception(f"Error en FirewallRuleCollector: {e}")
            self.finished_error.emit(str(e))

# REMOVED - Browser Passwords
# class BrowserPasswordCollector(QThread):
#     progress = pyqtSignal(int, str)
#     finished_ok = pyqtSignal(list)
#     finished_error = pyqtSignal(str)
#
#     def __init__(self):
#         super().__init__()
#         self._stop_requested = False
#
#     def request_stop(self):
#         """Solicita detener el análisis de forma controlada."""
#         self._stop_requested = True
#
#     def run(self):
#         try:
#             self.progress.emit(20, "Escaneando contraseñas de navegadores...")
#             passwords = run_powershell_json(PS_GET_BROWSER_PASSWORDS, timeout=45)
#
#             if not passwords:
#                 self.progress.emit(100, "No se encontraron contraseñas de navegadores")
#                 self.finished_ok.emit([])
#                 return
#
#             if isinstance(passwords, dict):
#                 passwords = [passwords]
#
#             rows = []
#             total = len(passwords)
#             for idx, pw in enumerate(passwords):
#                 if self._stop_requested:
#                     self.progress.emit(100, "Análisis detenido por el usuario")
#                     self.finished_ok.emit(rows)
#                     return
#
#                 self.progress.emit(40 + int((idx / total) * 60), f"Procesando contraseña {idx+1}/{total}...")
#
#                 rows.append(BrowserPasswordRow(
#                     navegador=pw.get("Navegador", ""),
#                     url=pw.get("URL", ""),
#                     usuario=pw.get("Usuario", ""),
#                     contraseña=pw.get("Contrasena", ""),
#                     nota=pw.get("Nota", "No disponible")
#                 ))
#
#             self.progress.emit(100, "Completado")
#             self.finished_ok.emit(rows)
#         except Exception as e:
#             logger.exception(f"Error en BrowserPasswordCollector: {e}")
#             self.finished_error.emit(str(e))

class EmailAccountCollector(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(list)
    finished_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        """Solicita detener el análisis de forma controlada."""
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(20, "Escaneando cuentas de correo...")
            accounts = run_powershell_json(PS_GET_EMAIL_ACCOUNTS, timeout=45)

            if not accounts:
                self.progress.emit(100, "No se encontraron cuentas de correo")
                self.finished_ok.emit([])
                return

            if isinstance(accounts, dict):
                accounts = [accounts]

            rows = []
            total = len(accounts)
            for idx, acc in enumerate(accounts):
                if self._stop_requested:
                    self.progress.emit(100, "Análisis detenido por el usuario")
                    self.finished_ok.emit(rows)
                    return

                self.progress.emit(40 + int((idx / total) * 60), f"Procesando cuenta {idx+1}/{total}...")

                rows.append(EmailAccountRow(
                    cliente=acc.get("Cliente", ""),
                    cuenta=acc.get("Cuenta", ""),
                    servidor=acc.get("Servidor", ""),
                    tipo=acc.get("Tipo", ""),
                    imap_host=acc.get("ImapHost", "") or "",
                    imap_port=int(acc.get("ImapPort") or 0),
                    imap_ssl=bool(acc.get("ImapSSL", False)),
                    pop_host=acc.get("PopHost", "") or "",
                    pop_port=int(acc.get("PopPort") or 0),
                    pop_ssl=bool(acc.get("PopSSL", False)),
                    smtp_host=acc.get("SmtpHost", "") or "",
                    smtp_port=int(acc.get("SmtpPort") or 0),
                    smtp_ssl=bool(acc.get("SmtpSSL", False)),
                ))

            self.progress.emit(100, "Completado")
            self.finished_ok.emit(rows)
        except Exception as e:
            logger.exception(f"Error en EmailAccountCollector: {e}")
            self.finished_error.emit(str(e))

class BrowserCloudAccountCollector(QThread):
    """Cuentas de navegador (Firefox/Chrome/Edge) y de nube (OneDrive) —
    misma estructura que EmailAccountCollector, separado porque no son
    cuentas de correo (ver PS_GET_BROWSER_CLOUD_ACCOUNTS)."""
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(list)
    finished_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        """Solicita detener el análisis de forma controlada."""
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(20, "Escaneando cuentas de navegadores y nube...")
            accounts = run_powershell_json(PS_GET_BROWSER_CLOUD_ACCOUNTS, timeout=45)

            if not accounts:
                self.progress.emit(100, "No se encontraron cuentas")
                self.finished_ok.emit([])
                return

            if isinstance(accounts, dict):
                accounts = [accounts]

            rows = []
            total = len(accounts)
            for idx, acc in enumerate(accounts):
                if self._stop_requested:
                    self.progress.emit(100, "Análisis detenido por el usuario")
                    self.finished_ok.emit(rows)
                    return

                self.progress.emit(40 + int((idx / total) * 60), f"Procesando cuenta {idx+1}/{total}...")

                rows.append(BrowserCloudAccountRow(
                    cliente=acc.get("Cliente", ""),
                    cuenta=acc.get("Cuenta", ""),
                    servidor=acc.get("Servidor", ""),
                    tipo=acc.get("Tipo", "")
                ))

            self.progress.emit(100, "Completado")
            self.finished_ok.emit(rows)
        except Exception as e:
            logger.exception(f"Error en BrowserCloudAccountCollector: {e}")
            self.finished_error.emit(str(e))


class BrowserProfileSizeThread(QThread):
    """Calcula el tamaño en disco de cada perfil de navegador (os.walk
    completo — puede tardar varios segundos en un perfil de varios GB con
    decenas de miles de archivos de caché). SIEMPRE en segundo plano: esto
    NUNCA debe llamarse desde un detector de Soporte síncrono (bug real
    corregido — colapsaba la UI varios segundos cada vez que se entraba en
    Navegadores y Nube, porque _actualizar_boton_soporte() ejecuta los
    detectores en el hilo de UI en cada cambio de pestaña)."""
    finished_ok = pyqtSignal(dict)

    def run(self):
        from msys.browser_data import enumerar_perfiles, tamano_perfil_mb
        resultado = {}
        try:
            for perfil in enumerar_perfiles():
                try:
                    resultado[(perfil.navegador, perfil.nombre_perfil)] = (
                        tamano_perfil_mb(perfil), perfil.ruta)
                except OSError:
                    continue
        except Exception as e:
            logger.warning(f"Error calculando tamaño de perfiles de navegador: {e}")
        self.finished_ok.emit(resultado)


class EmailProviderTestThread(QThread):
    """Detecta la configuración IMAP/POP3/SMTP de una cuenta de correo
    probando conexiones reales contra ~35 proveedores conocidos.

    Portado de GestCorreov4.ps1 (Test-MailConnection / "Probar todos los
    proveedores"), con dos diferencias deliberadas:
      - usa msys.email_providers.test_mail_connection, que valida el
        certificado TLS de verdad (el original lo aceptaba siempre);
      - prueba los proveedores EN PARALELO (ThreadPoolExecutor) en vez de
        uno a uno; el original podía tardar hasta ~3.5 min en el peor caso
        (35 proveedores x 3 protocolos x 6s de timeout, en serie) — aquí,
        al paralelizar por proveedor y entre proveedores, el peor caso baja
        a un puñado de rondas de 6s.
    """
    progress = pyqtSignal(int, str)
    provider_result = pyqtSignal(str, dict)
    finished_ok = pyqtSignal(list, object)
    finished_error = pyqtSignal(str)

    def __init__(self, email: str, password: str, timeout: float = 6.0, resto: bool = False):
        super().__init__()
        self.email = email
        self.password = password
        self.timeout = timeout
        # La contraseña va SOLO a los proveedores del dominio de la cuenta; el resto de la lista (resto=True) solo
        # cuando el técnico lo confirma en el diálogo — misma regla que «📧 Correo › Probar cuenta» (4.9.0). Antes se
        # mandaba a los ~39 proveedores (Gmail, Yahoo...) aunque la cuenta no fuera de ellos.
        self.resto = resto
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def _probar_proveedor(self, p) -> Dict[str, str]:
        with _concurrent_futures.ThreadPoolExecutor(max_workers=3) as pool:
            f_imap = pool.submit(test_mail_connection, p.imap_host, p.imap_port, "IMAP",
                                  p.imap_ssl, self.email, self.password, self.timeout) if p.imap_host else None
            f_pop3 = pool.submit(test_mail_connection, p.pop_host, p.pop_port, "POP3",
                                  p.pop_ssl, self.email, self.password, self.timeout) if p.pop_host else None
            f_smtp = pool.submit(test_mail_connection, p.smtp_host, p.smtp_port, "SMTP",
                                  p.smtp_ssl, self.email, self.password, self.timeout) if p.smtp_host else None
            return {
                "imap": f_imap.result() if f_imap else "N/D",
                "pop3": f_pop3.result() if f_pop3 else "N/D",
                "smtp": f_smtp.result() if f_smtp else "N/D",
            }

    def _es_match_completo(self, r: Dict[str, str]) -> bool:
        return r.get("imap") == "OK" and r.get("smtp") == "OK"

    def _probar_tanda(self, proveedores, resultados, ya_probados) -> Optional[Any]:
        """Prueba una lista de proveedores en paralelo entre sí; devuelve
        el (proveedor, resultado) del primer match completo, o None."""
        if not proveedores:
            return None
        encontrado = None
        with _concurrent_futures.ThreadPoolExecutor(max_workers=6) as pool:
            futuros = {pool.submit(self._probar_proveedor, p): p for p in proveedores}
            completados = 0
            for futuro in _concurrent_futures.as_completed(futuros):
                p = futuros[futuro]
                if self._stop_requested:
                    for f in futuros:
                        f.cancel()
                    break
                try:
                    r = futuro.result()
                except Exception:
                    r = {"imap": "Error", "pop3": "Error", "smtp": "Error"}
                completados += 1
                ya_probados.add(p.nombre)
                resultados.append((p, r))
                self.provider_result.emit(p.nombre, r)
                self.progress.emit(
                    min(95, int(len(ya_probados) * 100 / max(1, self._total))),
                    f"Probado {p.nombre} ({len(ya_probados)}/{self._total})...")
                if encontrado is None and self._es_match_completo(r):
                    encontrado = (p, r)
                    for f in futuros:
                        f.cancel()
                    break
        return encontrado

    def run(self):
        try:
            from msys.email_providers import PROVEEDORES, proveedores_prioritarios
            prioritarios = proveedores_prioritarios(self.email)
            tanda = [p for p in PROVEEDORES if p not in prioritarios] if self.resto else prioritarios
            self._total = len(tanda)

            resultados: List[Any] = []
            ya_probados: set = set()

            self.progress.emit(2, "Probando el resto de proveedores conocidos..." if self.resto
                               else "Probando los proveedores del dominio de la cuenta...")
            encontrado = self._probar_tanda(tanda, resultados, ya_probados)

            if self._stop_requested:
                self.progress.emit(100, "Detección detenida por el usuario")
            else:
                self.progress.emit(100, "Completado")
            self.finished_ok.emit(resultados, encontrado)
        except Exception as e:
            logger.exception(f"Error en EmailProviderTestThread: {e}")
            self.finished_error.emit(str(e))


class OutlookConfigBackupThread(QThread):
    """Backup de la configuración de Outlook del usuario actual: exporta a
    .reg la rama completa HKCU\\...\\Office\\{ver}\\Outlook de cada versión
    detectada (perfiles, cuentas, reglas — todo vive bajo esa rama) y
    comprime la carpeta de firmas (%APPDATA%\\Microsoft\\Signatures) en un
    .zip. Portado de GestCorreov4.ps1 (Export-OutlookAllConfig + el ZIP de
    firmas de la pestaña "Exportar Config"), consolidado en una sola
    operación en vez de exports separados por perfil/reglas/firma."""
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(list)
    finished_error = pyqtSignal(str)

    def __init__(self, carpeta_destino: str):
        super().__init__()
        self.carpeta_destino = carpeta_destino

    def run(self):
        try:
            archivos_generados = []
            versiones = detectar_versiones_outlook_instaladas()

            if not versiones:
                self.finished_error.emit(
                    "No se encontró ninguna versión de Outlook con un perfil configurado en este usuario.")
                return

            total_pasos = len(versiones) + 1
            paso = 0

            for ver in versiones:
                paso += 1
                self.progress.emit(int(paso * 90 / total_pasos), f"Exportando configuración de Outlook {ver}...")
                reg_key = f"HKEY_CURRENT_USER\\Software\\Microsoft\\Office\\{ver}\\Outlook"
                destino = os.path.join(self.carpeta_destino, f"OutlookConfig_{ver}.reg")
                try:
                    resultado = subprocess.run(
                        ["reg", "export", reg_key, destino, "/y"],
                        capture_output=True, timeout=30, check=False,
                        startupinfo=startupinfo_oculto(), creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    if resultado.returncode == 0 and os.path.exists(destino):
                        archivos_generados.append(destino)
                except (subprocess.TimeoutExpired, OSError) as e:
                    logger.warning(f"No se pudo exportar Outlook {ver}: {e}")

            self.progress.emit(95, "Comprimiendo firmas de correo...")
            carpeta_firmas = os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Signatures")
            if os.path.isdir(carpeta_firmas) and os.listdir(carpeta_firmas):
                destino_zip = os.path.join(self.carpeta_destino, "Firmas.zip")
                try:
                    with zipfile.ZipFile(destino_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                        for raiz, _dirs, ficheros in os.walk(carpeta_firmas):
                            for nombre in ficheros:
                                ruta_completa = os.path.join(raiz, nombre)
                                ruta_relativa = os.path.relpath(ruta_completa, carpeta_firmas)
                                zf.write(ruta_completa, ruta_relativa)
                    archivos_generados.append(destino_zip)
                except OSError as e:
                    logger.warning(f"No se pudo comprimir la carpeta de firmas: {e}")

            self.progress.emit(100, "Completado")
            if not archivos_generados:
                self.finished_error.emit(
                    "No se pudo generar ningún archivo de backup (revisa permisos de la carpeta de destino).")
                return
            self.finished_ok.emit(archivos_generados)
        except Exception as e:
            logger.exception(f"Error en OutlookConfigBackupThread: {e}")
            self.finished_error.emit(str(e))


class OutlookAccountsListThread(QThread):
    """Enumera las cuentas del perfil de Outlook activo, para el
    exportador a PST.

    Se ejecuta vía PowerShell (proceso aparte), no con win32com.client en
    el proceso de la app — igual que el resto de collectors.py habla con
    Office/Outlook. Si Outlook.exe no está en un estado normal (p. ej. un
    diálogo de seguridad "Object Model Guard" pendiente en pantalla, no
    necesariamente visible), esta llamada puede colgarse varios segundos
    hasta el timeout: no es un fallo del hilo en sí, sino de la
    automatización COM de Outlook — comprobado que hasta un Start-Job de
    PowerShell puro (sin Python ni Qt) se cuelga igual en ese caso."""
    finished_ok = pyqtSignal(list)
    finished_error = pyqtSignal(str)

    def run(self):
        try:
            data = run_powershell_json(PS_GET_OUTLOOK_ACCOUNTS, timeout=30)
            if data is None:
                self.finished_error.emit(
                    "No se pudo conectar con Outlook (¿está instalado y con un perfil configurado?).")
                return
            if isinstance(data, dict):
                if "Error" in data:
                    self.finished_error.emit(data["Error"])
                    return
                data = [data]

            cuentas = [{
                "nombre": c.get("Nombre", ""),
                "tipo": c.get("Tipo", "Desconocido"),
                "tiene_carpeta": bool(c.get("TieneCarpeta", False)),
            } for c in data]
            self.finished_ok.emit(cuentas)
        except Exception as e:
            logger.exception(f"Error en OutlookAccountsListThread: {e}")
            self.finished_error.emit(str(e))


class OutlookPSTExportThread(QThread):
    """Exporta cuentas de Outlook a ficheros .pst independientes, una
    invocación de PowerShell por cuenta (ver PS_EXPORT_OUTLOOK_ACCOUNT_PST
    — mismo motivo que OutlookAccountsListThread para no usar win32com
    directo). Portado de GestCorreov4.ps1 (Export-OutlookToPST /
    New-EmptyPST).

    La decisión de sobrescribir/añadir/renombrar ante un .pst ya existente
    se resuelve ANTES de arrancar este hilo (en la UI, ver
    outlook_pst_dialog.py)."""
    progress = pyqtSignal(int, str)
    cuenta_completada = pyqtSignal(str, bool, str)
    finished_ok = pyqtSignal(int, int)
    finished_error = pyqtSignal(str)

    def __init__(self, plan: List[Dict[str, str]]):
        super().__init__()
        self.plan = plan
        self._stop_requested = False

    def request_stop(self):
        """Detiene el proceso ANTES de empezar la siguiente cuenta — la
        cuenta que ya esté en curso termina (no se interrumpe a mitad de
        una llamada COM, arriesgaría dejar el store PST a medio montar)."""
        self._stop_requested = True

    def run(self):
        try:
            ok_count = 0
            total = len(self.plan)
            for idx, item in enumerate(self.plan):
                if self._stop_requested:
                    self.progress.emit(100, "Exportación detenida por el usuario")
                    self.finished_ok.emit(ok_count, total)
                    return

                nombre = item["nombre"]
                pst_path = item["pst_path"]
                modo = item["modo"]
                self.progress.emit(int(idx * 100 / max(1, total)), f"Exportando cuenta: {nombre}...")

                # 30 min: una cuenta con un buzón muy grande puede tardar
                # bastante en copiarse carpeta a carpeta — 180s (el valor
                # anterior) podía cortar la exportación a mitad dejando el
                # store PST montado en el propio Outlook del usuario.
                resultado = run_powershell_json_params(
                    PS_EXPORT_OUTLOOK_ACCOUNT_PST, timeout=1800,
                    nombre_cuenta=nombre, pst_path=pst_path, modo=modo,
                )
                if isinstance(resultado, dict) and resultado.get("Ok"):
                    ok_count += 1
                    self.cuenta_completada.emit(nombre, True, resultado.get("Mensaje", pst_path))
                elif isinstance(resultado, dict):
                    self.cuenta_completada.emit(nombre, False, resultado.get("Mensaje", "Error desconocido"))
                else:
                    self.cuenta_completada.emit(nombre, False, "No se pudo ejecutar la exportación (PowerShell no devolvió resultado).")

            self.progress.emit(100, "Completado")
            self.finished_ok.emit(ok_count, total)
        except Exception as e:
            logger.exception(f"Error en OutlookPSTExportThread: {e}")
            self.finished_error.emit(str(e))


class OutlookAccountDatafileThread(QThread):
    """Localiza la ruta del archivo de datos local (.ost/.pst) de una
    cuenta de Outlook, para el menú contextual "Abrir carpeta del archivo
    de datos" de Cuentas de Correo."""
    finished_ok = pyqtSignal(str)
    finished_error = pyqtSignal(str)

    def __init__(self, nombre_cuenta: str):
        super().__init__()
        self.nombre_cuenta = nombre_cuenta

    def run(self):
        try:
            resultado = run_powershell_json_params(
                PS_GET_OUTLOOK_ACCOUNT_DATAFILE, timeout=30, nombre_cuenta=self.nombre_cuenta,
            )
            if isinstance(resultado, dict) and resultado.get("Ok"):
                self.finished_ok.emit(resultado.get("Mensaje", ""))
            elif isinstance(resultado, dict):
                self.finished_error.emit(resultado.get("Mensaje", "Error desconocido"))
            else:
                self.finished_error.emit("No se pudo consultar Outlook.")
        except Exception as e:
            logger.exception(f"Error en OutlookAccountDatafileThread: {e}")
            self.finished_error.emit(str(e))

class ADInfoCollector(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(list)
    finished_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        """Solicita detener el análisis de forma controlada."""
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(20, "Escaneando información de Active Directory...")
            ad_info = run_powershell_json(PS_GET_AD_INFO, timeout=60)

            if not ad_info:
                self.progress.emit(100, "No se encontró información de Active Directory")
                self.finished_ok.emit([])
                return

            if isinstance(ad_info, dict):
                ad_info = [ad_info]

            rows = []
            total = len(ad_info)
            for idx, ad in enumerate(ad_info):
                if self._stop_requested:
                    self.progress.emit(100, "Análisis detenido por el usuario")
                    self.finished_ok.emit(rows)
                    return

                self.progress.emit(40 + int((idx / total) * 60), f"Procesando objeto AD {idx+1}/{total}...")

                rows.append(ADInfoRow(
                    tipo=ad.get("Tipo", ""),
                    nombre=ad.get("Nombre", ""),
                    sam_account_name=ad.get("SamAccountName", ""),
                    habilitado=ad.get("Habilitado", ""),
                    ultima_conexion=ad.get("UltimaConexion", "N/A"),
                    distinguished_name=ad.get("DistinguishedName", "")
                ))

            self.progress.emit(100, "Completado")
            self.finished_ok.emit(rows)
        except Exception as e:
            logger.exception(f"Error en ADInfoCollector: {e}")
            self.finished_error.emit(str(e))

class GroupPolicyInfoCollector(QThread):
    """A diferencia de ADInfoCollector, NUNCA depende de RSAT — dsregcmd y
    gpresult son binarios nativos de Windows (ver PS_GET_GROUP_POLICY_INFO)."""
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(dict)
    finished_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(30, "Consultando estado de unión y directivas de grupo...")
            data = run_powershell_json(PS_GET_GROUP_POLICY_INFO, timeout=60)
            self.progress.emit(100, "Completado")
            self.finished_ok.emit(data or {})
        except Exception as e:
            logger.exception(f"Error en GroupPolicyInfoCollector: {e}")
            self.finished_error.emit(str(e))

class GroupPolicyReportCollector(QThread):
    """Genera el informe HTML detallado de 'gpresult /h' — mismo nivel de
    detalle que el Editor de Directivas de Grupo / RSoP (nombres amigables
    de cada configuración de plantilla administrativa, no solo la lista de
    GPOs aplicadas que da GroupPolicyInfoCollector). No se reimplementa la
    resolución de ADMX a mano: gpresult /h ya hace exactamente eso de forma
    oficial y fiable — mismo criterio que Intune/MDM en
    PS_GET_GROUP_POLICY_INFO, no fingir un dato que el propio Windows ya
    sabe dar con garantías. Usa run_system_command (argv real, sin
    intérprete de por medio) en vez de envolverlo en PowerShell."""
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(str)
    finished_error = pyqtSignal(str)

    def __init__(self, destino: str):
        super().__init__()
        self._destino = destino

    def run(self):
        tmp_path = os.path.join(tempfile.gettempdir(), f"msys_gpreport_{int(time.time() * 1000)}.html")
        try:
            self.progress.emit(30, "Generando informe detallado (gpresult /h)...")
            ok, salida = run_system_command(["gpresult", "/h", tmp_path, "/f"], timeout=90)
            if not os.path.exists(tmp_path):
                self.finished_error.emit(salida or "gpresult no generó el informe.")
                return
            shutil.move(tmp_path, self._destino)
            self.progress.emit(100, "Completado")
            self.finished_ok.emit(self._destino)
        except Exception as e:
            logger.exception(f"Error en GroupPolicyReportCollector: {e}")
            self.finished_error.emit(str(e))
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

class StartupProgramsCollector(QThread):
    """Registro (Run de HKLM/HKCU) + carpetas de inicio — ver
    PS_GET_STARTUP_PROGRAMS. El activar/desactivar en sí NO pasa por aquí:
    es una escritura de registro directa vía winreg (ver
    msys/domain/startup_programs.py), síncrona, sin QThread propio."""
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(list)
    finished_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(30, "Consultando programas de inicio...")
            items = run_powershell_json(PS_GET_STARTUP_PROGRAMS, timeout=30)
            if not items:
                self.progress.emit(100, "Completado")
                self.finished_ok.emit([])
                return
            if isinstance(items, dict):
                items = [items]
            rows = [
                StartupProgramRow(
                    nombre=it.get("Nombre", ""), comando=it.get("Comando", ""),
                    ubicacion=it.get("Ubicacion", ""), estado=it.get("Estado", "Habilitado"),
                    hive=it.get("Hive", "HKCU"), clave_origen=it.get("ClaveOrigen", "Run"),
                    clave_valor=it.get("ClaveValor", it.get("Nombre", "")),
                    existe_fichero=bool(it.get("Existe", True)),
                )
                for it in items
            ]
            self.progress.emit(100, "Completado")
            self.finished_ok.emit(rows)
        except Exception as e:
            logger.exception(f"Error en StartupProgramsCollector: {e}")
            self.finished_error.emit(str(e))

class USBDeviceCollector(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(list)
    finished_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        """Solicita detener el análisis de forma controlada."""
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(20, "Escaneando dispositivos USB...")
            devices = run_powershell_json(PS_GET_USB_DEVICES, timeout=30)

            # None = error/timeout real; [] = sin dispositivos USB (válido).
            if devices is None:
                self.progress.emit(100, "No se pudo completar el escaneo")
                self.finished_error.emit(
                    "Tiempo agotado o error al consultar los dispositivos USB del sistema.")
                return

            if not devices:
                self.progress.emit(100, "No se encontraron dispositivos USB")
                self.finished_ok.emit([])
                return

            if isinstance(devices, dict):
                devices = [devices]

            rows = []
            total = len(devices)
            for idx, d in enumerate(devices):
                if self._stop_requested:
                    self.progress.emit(100, "Análisis detenido por el usuario")
                    self.finished_ok.emit(rows)
                    return

                self.progress.emit(40 + int((idx / total) * 60), f"Procesando dispositivo {idx+1}/{total}...")

                rows.append(USBDeviceRow(
                    nombre=d.get("Nombre", ""),
                    fabricante=d.get("Fabricante", "Desconocido"),
                    estado=d.get("Estado", ""),
                    instance_id=d.get("InstanceId", "")
                ))

            self.progress.emit(100, "Completado")
            self.finished_ok.emit(rows)
        except Exception as e:
            logger.exception(f"Error en USBDeviceCollector: {e}")
            self.finished_error.emit(str(e))


def leer_historial_usb_almacenamiento() -> List[Dict[str, str]]:
    """Dispositivos de almacenamiento USB (pendrives, discos externos) que
    se han conectado ALGUNA VEZ a este equipo, no solo los presentes ahora
    — WMI/PnP (incluso Get-PnpDevice -PresentOnly:$false) solo conoce lo
    detectado recientemente, no el histórico completo. Se lee
    HKLM\\SYSTEM\\CurrentControlSet\\Enum\\USBSTOR, el mismo sitio que usa
    cualquier herramienta forense/soporte estándar (USBDeview y similares)
    para esto. Consulta local de registro, rápida — se ejecuta síncrona,
    sin QThread, igual que el resto de operaciones puntuales de
    DriverManager/PrinterManager."""
    resultados = []
    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Enum\USBSTOR",
            0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY
        ) as clave_raiz:
            idx_clase = 0
            while True:
                try:
                    nombre_clase = winreg.EnumKey(clave_raiz, idx_clase)
                except OSError:
                    break
                idx_clase += 1
                try:
                    with winreg.OpenKey(
                        clave_raiz, nombre_clase, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY
                    ) as clave_clase:
                        idx_serial = 0
                        while True:
                            try:
                                serial = winreg.EnumKey(clave_clase, idx_serial)
                            except OSError:
                                break
                            idx_serial += 1
                            try:
                                with winreg.OpenKey(
                                    clave_clase, serial, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY
                                ) as clave_serial:
                                    try:
                                        nombre_amigable, _ = winreg.QueryValueEx(clave_serial, "FriendlyName")
                                    except FileNotFoundError:
                                        nombre_amigable = nombre_clase
                                    # Dispositivos sin número de serie real de fábrica
                                    # reciben un pseudo-serial con '&' (p. ej. '6&2a3b...&0')
                                    # generado por Windows — no es un identificador útil.
                                    serial_mostrar = serial if "&" not in serial else "(sin número de serie de fábrica)"
                                    resultados.append({
                                        "Nombre": nombre_amigable,
                                        "Clase": nombre_clase,
                                        "NumeroSerie": serial_mostrar,
                                    })
                            except OSError:
                                continue
                except OSError:
                    continue
    except FileNotFoundError:
        pass
    except OSError:
        pass
    return resultados


def detectar_usb_sospechosos() -> Tuple[bool, Any]:
    """Dispositivos USB con indicios de riesgo tipo BadUSB: un mismo VID/PID
    (mismo dispositivo físico) que se presenta ante Windows con MÁS de una
    clase de PnP a la vez (p. ej. un pendrive que también se registra como
    teclado HID) — el patrón más documentado de ataque por hardware USB
    (Rubber Ducky, O.MG Cable, BadUSB clásico). Es un indicio, no una
    condena: algunos periféricos legítimos también son compuestos (p. ej.
    un teclado con hub USB integrado), por eso se explica el motivo exacto
    en cada fila en vez de solo etiquetar 'sospechoso'.
    Devuelve (True, lista_dicts) o (False, mensaje_error)."""
    datos = run_powershell_json(
        # -like (comodín) y .Split() de string, NUNCA -match/-split con el
        # backslash del InstanceId como regex: '\V' no es un escape válido
        # para el motor de regex de .NET (falla en seco con "Unrecognized
        # escape sequence"), verificado en vivo.
        #
        # $resultado = @() + `if ($resultado.Count -eq 0) { Write-Output
        # "[]" } else { ... | ConvertTo-Json }` es el patrón YA probado en
        # el resto del proyecto (ver PS_GET_NETWORK_CONNECTIONS) para este
        # problema — no ",@(...)": esa variante devolvía el wrapper .NET
        # completo ({"value":[...], "Count":N}) en vez del array plano,
        # verificado en vivo.
        "$ErrorActionPreference = 'Stop'; "
        "$dispositivos = Get-PnpDevice -PresentOnly | "
        "Where-Object { $_.InstanceId -like 'USB\\VID_*' }; "
        "$grupos = $dispositivos | Group-Object { $_.InstanceId.Split('\\')[1] }; "
        "$resultado = @(); "
        "foreach ($g in $grupos) { "
        "  $clases = @($g.Group | Select-Object -ExpandProperty Class -Unique); "
        "  if ($clases.Count -gt 1) { "
        "    foreach ($d in $g.Group) { "
        "      $resultado += [PSCustomObject]@{ "
        "        Nombre = $d.FriendlyName; Clase = $d.Class; VidPid = $g.Name; "
        "        InstanceId = $d.InstanceId; "
        "        Motivo = \"Mismo dispositivo físico (mismo VID/PID) presenta varias clases a la vez: $($clases -join ', ')\" "
        "      } "
        "    } "
        "  } "
        "}; "
        "if ($resultado.Count -eq 0) { Write-Output '[]' } else { $resultado | ConvertTo-Json -Compress }",
        timeout=30,
    )
    if datos is None:
        return False, "Tiempo agotado o error al analizar los dispositivos USB conectados."
    if isinstance(datos, dict):
        datos = [datos]
    return True, datos


def enumerar_pantallas() -> List[Dict[str, Any]]:
    """Monitores físicamente conectados vía la API nativa de Windows
    (EnumDisplayDevices/EnumDisplaySettings) — DeviceName en formato
    '\\\\.\\DISPLAYn', necesario para cambiar la resolución de UNA pantalla
    concreta sin tocar las demás. No es la misma fuente que MonitorRow
    (WmiMonitorID vía EDID): esa da fabricante/serie/tipo de conexión pero
    no expone el DeviceName que hace falta aquí — son dos consultas
    independientes a propósito."""
    pantallas = []
    i = 0
    while True:
        try:
            d = win32api.EnumDisplayDevices(None, i)
        except Exception:
            break
        i += 1
        if not (d.StateFlags & win32con.DISPLAY_DEVICE_ATTACHED_TO_DESKTOP):
            continue
        try:
            actual = win32api.EnumDisplaySettings(d.DeviceName, win32con.ENUM_CURRENT_SETTINGS)
        except Exception:
            continue
        modos = set()
        j = 0
        while True:
            try:
                m = win32api.EnumDisplaySettings(d.DeviceName, j)
            except Exception:
                break
            j += 1
            if m.PelsWidth and m.PelsHeight and m.DisplayFrequency:
                modos.add((m.PelsWidth, m.PelsHeight, m.DisplayFrequency))
        pantallas.append({
            "DeviceName": d.DeviceName,
            "DeviceString": d.DeviceString,
            "AnchoActual": actual.PelsWidth,
            "AltoActual": actual.PelsHeight,
            "FrecuenciaActual": actual.DisplayFrequency,
            "Modos": sorted(modos, reverse=True),
        })
    return pantallas


def cambiar_resolucion_pantalla(device_name: str, ancho: int, alto: int, frecuencia: int) -> Tuple[bool, str]:
    """Cambia la resolución de UNA pantalla concreta (identificada por su
    DeviceName '\\\\.\\DISPLAYn') sin afectar a las demás: ChangeDisplaySettingsEx
    con un DeviceName específico solo reconfigura ese monitor."""
    try:
        dm = win32api.EnumDisplaySettings(device_name, win32con.ENUM_CURRENT_SETTINGS)
        dm.PelsWidth = ancho
        dm.PelsHeight = alto
        dm.DisplayFrequency = frecuencia
        dm.Fields = win32con.DM_PELSWIDTH | win32con.DM_PELSHEIGHT | win32con.DM_DISPLAYFREQUENCY
        resultado = win32api.ChangeDisplaySettingsEx(device_name, dm)
        if resultado == win32con.DISP_CHANGE_SUCCESSFUL:
            return True, "OK"
        codigos = {
            win32con.DISP_CHANGE_RESTART: "Requiere reiniciar el equipo para aplicarse.",
            win32con.DISP_CHANGE_BADMODE: "Ese modo no es válido para esta pantalla.",
            win32con.DISP_CHANGE_FAILED: "El controlador gráfico rechazó el cambio.",
            win32con.DISP_CHANGE_BADPARAM: "Parámetros inválidos.",
        }
        return False, codigos.get(resultado, f"Código de error {resultado}")
    except Exception as exc:
        return False, str(exc)

class MonitorCollector(QThread):
    """Recopila las 2 categorías de la pestaña Gráficas y Monitores:
    tarjetas gráficas (Graficas) y monitores conectados (Monitores)."""
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(dict)
    finished_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        """Solicita detener el análisis de forma controlada."""
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(20, "Escaneando gráficas y monitores...")
            data = run_powershell_json(PS_GET_MONITORS, timeout=30) or {}
            if not isinstance(data, dict):
                data = {}

            graficas = [GPURow(
                nombre=str(g.get("Nombre", "")),
                ram=str(g.get("RAM", "N/A")),
                driver=str(g.get("Driver", "N/A")),
                resolucion=str(g.get("Resolucion", "N/A")),
            ) for g in (data.get("Graficas") or [])]

            monitores_raw = data.get("Monitores") or []
            if isinstance(monitores_raw, dict):
                monitores_raw = [monitores_raw]
            monitores = [MonitorRow(
                nombre=m.get("Nombre", ""),
                fabricante=m.get("Fabricante", "Desconocido"),
                serial=m.get("Serial", "N/A"),
                tipo_conexion=m.get("TipoConexion", "Desconocido")
            ) for m in monitores_raw]

            self.progress.emit(100, "Completado")
            self.finished_ok.emit({"graficas": graficas, "monitores": monitores})
        except Exception as e:
            logger.exception(f"Error en MonitorCollector: {e}")
            self.finished_error.emit(str(e))

class SystemInfoCollector(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(dict)
    finished_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        """Solicita detener el análisis de forma controlada."""
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(20, "Recopilando información del sistema...")
            # 60s en vez de 45: la consulta de la clave de licencia de
            # Windows (SoftwareLicensingProduct) puede tardar más de lo
            # habitual en algunos equipos.
            system_info = run_powershell_json(PS_GET_SYSTEM_INFO, timeout=60)

            if not system_info:
                self.progress.emit(100, "No se pudo obtener información del sistema")
                self.finished_ok.emit({})
                return

            self.progress.emit(100, "Completado")
            self.finished_ok.emit(system_info)
        except Exception as e:
            logger.exception(f"Error en SystemInfoCollector: {e}")
            self.finished_error.emit(str(e))

class WindowsUpdatesCollector(QThread):
    """Dos llamadas PS independientes con su propio timeout cada una: antes
    era una sola (instaladas rápidas + pendientes lentas) con un único
    timeout de 60s, así que si la búsqueda de pendientes se alargaba (muy
    habitual: primera ejecución, conexión lenta, WSUS) se mataba el proceso
    entero y se perdían TAMBIÉN las instaladas, que ya se habían obtenido en
    menos de un segundo. Ahora un fallo en una no se lleva por delante la
    otra, y el resultado indica explícitamente si alguna de las dos falló
    (InstalledError/PendingError) — antes 'None' (fallo/timeout) y '0
    encontradas' (resultado real) eran indistinguibles para el usuario."""
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(dict)  # {'Installed':[...], 'Pending':[...], 'InstalledError':bool, 'PendingError':bool, 'RebootPending':bool}
    finished_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        """Solicita detener el análisis de forma controlada."""
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(10, "Buscando actualizaciones instaladas...")
            installed_data = run_powershell_json(PS_GET_INSTALLED_UPDATES, timeout=180)
            installed_error = installed_data is None

            self.progress.emit(40, "Buscando actualizaciones pendientes (puede tardar varios minutos)...")
            pending_data = run_powershell_json(PS_GET_PENDING_UPDATES, timeout=180)
            pending_error = pending_data is None

            self.progress.emit(90, "Comprobando si hay reinicio pendiente...")
            reboot_pending = bool(run_powershell_json(PS_GET_REBOOT_PENDING, timeout=15))

            self.progress.emit(100, "Completado")
            self.finished_ok.emit({
                'Installed': installed_data or [],
                'Pending': pending_data or [],
                'InstalledError': installed_error,
                'PendingError': pending_error,
                'RebootPending': reboot_pending,
            })
        except Exception as e:
            logger.exception(f"Error en WindowsUpdatesCollector: {e}")
            self.finished_error.emit(str(e))

class DriverCollector(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(list)
    finished_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        """Solicita detener el análisis de forma controlada."""
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(20, "Escaneando controladores del sistema...")
            # 90s en vez de 45: Win32_PnPSignedDriver puede tardar más de lo
            # habitual en equipos con muchos dispositivos. Además, 'None'
            # (timeout/error real) se trataba igual que "sin controladores"
            # y el usuario veía un falso "✅ 0 controladores encontrados" en
            # vez de un error — mismo bug que ya se corrigió en Actualizaciones.
            drivers = run_powershell_json(PS_GET_DRIVERS, timeout=90)

            if drivers is None:
                self.progress.emit(100, "No se pudo completar el escaneo")
                self.finished_error.emit(
                    "Tiempo agotado o error al consultar los controladores del sistema.")
                return

            if isinstance(drivers, dict):
                drivers = [drivers]

            rows = []
            total = len(drivers)
            for idx, drv in enumerate(drivers):
                if self._stop_requested:
                    self.progress.emit(100, "Análisis detenido por el usuario")
                    self.finished_ok.emit(rows)
                    return

                self.progress.emit(40 + int((idx / total) * 60), f"Procesando controlador {idx+1}/{total}...")

                rows.append(DriverRow(
                    dispositivo=drv.get("Dispositivo", ""),
                    fabricante=drv.get("Fabricante", ""),
                    version=drv.get("Version", ""),
                    fecha=drv.get("Fecha", ""),
                    clase=drv.get("Clase", ""),
                    firmado=drv.get("Firmado", ""),
                    estado=drv.get("Estado", ""),
                    proveedor=drv.get("Proveedor", ""),
                    problema=drv.get("Problema", ""),
                    device_id=drv.get("DeviceID", ""),
                ))

            self.progress.emit(100, "Completado")
            self.finished_ok.emit(rows)
        except Exception as e:
            logger.exception(f"Error en DriverCollector: {e}")
            self.finished_error.emit(str(e))


class DriverBackupThread(QThread):
    """Exporta TODOS los paquetes de controladores de terceros del almacén
    de Windows a una carpeta (pnputil /export-driver * <destino>) — hilo
    aparte porque puede tardar bastante según el número de paquetes.
    Mismo patrón de señales que los demás hilos de reparación
    (CHKDSKRepairThread, etc.): progress/finished_ok/finished_error."""
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(str)
    finished_error = pyqtSignal(str)

    def __init__(self, destino: str, driver_manager: "DriverManager"):
        super().__init__()
        self.destino = destino
        self.driver_manager = driver_manager

    def run(self):
        try:
            self.progress.emit(10, "Exportando paquetes de controladores (puede tardar varios minutos)...")
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            resultado = subprocess.run(
                ["pnputil", "/export-driver", "*", self.destino],
                capture_output=True, text=True, timeout=600,
                startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            salida = (resultado.stdout or "").strip()
            if resultado.returncode != 0:
                error = (resultado.stderr or "").strip() or salida or f"pnputil devolvió el código {resultado.returncode}"
                self.finished_error.emit(error)
                return

            self.progress.emit(90, "Escribiendo instrucciones de restauración...")
            try:
                with open(os.path.join(self.destino, "COMO_RESTAURAR.txt"), "w", encoding="utf-8") as f:
                    f.write(
                        "RESTAURAR CONTROLADORES DESDE ESTE BACKUP\n"
                        "==========================================\n\n"
                        "Tras reinstalar Windows, con esta carpeta accesible (USB, red...),\n"
                        "abre un símbolo del sistema como Administrador y ejecuta:\n\n"
                        "  pnputil /add-driver \"<ruta_a_esta_carpeta>\\*.inf\" /subdirs /install\n"
                    )
            except OSError:
                pass  # el backup en si ya se completo; la nota es solo una ayuda extra

            self.progress.emit(100, "Backup completado")
            self.finished_ok.emit(self.destino)
        except subprocess.TimeoutExpired:
            self.finished_error.emit(
                "Tiempo agotado exportando los controladores (10 min) — puede que el "
                "almacén tenga muchos paquetes.")
        except Exception as exc:
            self.finished_error.emit(str(exc))


class DriverManager:
    """Deshabilitar/habilitar un dispositivo por su Device Instance ID vía
    pnputil.exe — invocación directa del ejecutable (argv list), nunca
    PowerShell con el ID interpolado en un string: el propio ID suele
    contener '&' y otros caracteres que en una llamada directa no hace
    falta escapar en absoluto."""

    def _pnputil(self, accion: str, device_id: str) -> Tuple[bool, str]:
        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            resultado = subprocess.run(
                ["pnputil", accion, device_id],
                capture_output=True, text=True, timeout=30,
                startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            salida = (resultado.stdout or "").strip() or (resultado.stderr or "").strip()
            if resultado.returncode == 0:
                return True, salida or "OK"
            return False, salida or f"pnputil devolvió el código {resultado.returncode}"
        except Exception as exc:
            return False, str(exc)

    def disable_device(self, device_id: str) -> Tuple[bool, str]:
        return self._pnputil("/disable-device", device_id)

    def enable_device(self, device_id: str) -> Tuple[bool, str]:
        return self._pnputil("/enable-device", device_id)

    def remove_device(self, device_id: str, force: bool = False) -> Tuple[bool, str]:
        """Quita un dispositivo del sistema (pnputil /remove-device) — a
        diferencia de disable/enable (reversible al instante), esto lo
        quita del todo; Windows puede volver a detectarlo y reinstalar su
        controlador solo, pero no siempre (según el bus: PCI/USB suele
        re-enumerar, otros no). force=True (/force) hace falta para
        dispositivos desconectados/ocultos — sin conexión activa, pnputil
        rechaza la operación si no se fuerza."""
        args = ["pnputil", "/remove-device", device_id]
        if force:
            args.append("/force")
        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            resultado = subprocess.run(
                args, capture_output=True, text=True, timeout=30,
                startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            salida = (resultado.stdout or "").strip() or (resultado.stderr or "").strip()
            if resultado.returncode == 0:
                return True, salida or "OK"
            return False, salida or f"pnputil devolvió el código {resultado.returncode}"
        except Exception as exc:
            return False, str(exc)

    def enum_disconnected_devices_csv(self) -> Tuple[bool, str]:
        """CSV crudo de pnputil con los dispositivos desconectados/ocultos
        (pnputil /enum-devices /disconnected /format csv) — cabeceras Y
        valores en inglés fijo (verificado en vivo), independiente del
        idioma de Windows. Fuente de datos de HiddenDevicesDialog."""
        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            resultado = subprocess.run(
                ["pnputil", "/enum-devices", "/disconnected", "/format", "csv"],
                capture_output=True, text=True, timeout=60,
                startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if resultado.returncode != 0:
                err = (resultado.stderr or "").strip()
                return False, err or f"pnputil devolvió el código {resultado.returncode}"
            return True, resultado.stdout or ""
        except subprocess.TimeoutExpired:
            return False, "Tiempo agotado buscando dispositivos ocultos."
        except Exception as exc:
            return False, str(exc)

    def enum_third_party_drivers_csv(self) -> Tuple[bool, str]:
        """Vista previa (CSV) de los paquetes de controladores de terceros
        que 'pnputil /export-driver * <destino>' copiaría. pnputil no tiene
        salida estructurada, así que se usa Get-WindowsDriver (DISM):
        Get-WindowsDriver -Online SIN /All ya devuelve solo los paquetes de
        terceros del almacén — el mismo conjunto que exporta pnputil."""
        ps_code = (
            "$ErrorActionPreference = 'Stop'; "
            "Get-WindowsDriver -Online | Select-Object ProviderName, "
            "@{N='OriginalName';E={$_.OriginalFileName}}, ClassName, "
            "@{N='DriverVersion';E={$_.Version}}, @{N='DriverName';E={$_.Driver}} | "
            "ConvertTo-Csv -NoTypeInformation"
        )
        cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_code]
        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            resultado = subprocess.run(
                cmd, capture_output=True, text=True, timeout=90,
                startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if resultado.returncode != 0:
                err = (resultado.stderr or "").strip()
                return False, err or f"Get-WindowsDriver devolvió el código {resultado.returncode}"
            return True, resultado.stdout or ""
        except subprocess.TimeoutExpired:
            return False, ("Tiempo agotado consultando el almacén de controladores "
                            "(DISM puede tardar en equipos con muchos paquetes).")
        except Exception as exc:
            return False, str(exc)

    def device_properties(self, device_id: str) -> str:
        """Detalle crudo de pnputil para un dispositivo: IDs de hardware,
        ubicación y controladores candidatos con su clasificación (incluye
        /stack para ver también los controladores no instalados). Devuelve
        texto tal cual, sin parsear — ver driver_properties_dialog.py."""
        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            resultado = subprocess.run(
                ["pnputil", "/enum-devices", "/deviceid", device_id, "/drivers", "/stack"],
                capture_output=True, text=True, timeout=30,
                startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW,
            )
            salida = (resultado.stdout or "").strip()
            if not salida:
                salida = (resultado.stderr or "").strip() or f"pnputil devolvió el código {resultado.returncode}"
            return salida
        except Exception as exc:
            return f"No se pudo consultar el dispositivo:\n\n{exc}"


def _detectar_tipo_union_equipo() -> str:
    """Devuelve una descripción legible de a qué está unido este equipo
    (Entra ID / Active Directory / ambos en Hybrid Join / grupo de
    trabajo), a partir de 'dsregcmd /status'. Sirve para explicar en la
    UI por qué no aparecen usuarios de dominio o de Entra ID cuando el
    equipo, simplemente, no está unido a ninguno de los dos."""
    try:
        resultado = subprocess.run(
            ["dsregcmd", "/status"], capture_output=True, timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        texto = resultado.stdout.decode(errors="replace")

        def _valor(clave):
            m = re.search(rf'{clave}\s*:\s*(\S+)', texto)
            return m.group(1).strip() if m else None

        azure_ad = (_valor("AzureAdJoined") or "").upper() == "YES"
        domain = (_valor("DomainJoined") or "").upper() == "YES"
        enterprise = (_valor("EnterpriseJoined") or "").upper() == "YES"
        workplace = (_valor("WorkplaceJoined") or "").upper() == "YES"

        m_tenant = re.search(r'TenantName\s*:\s*(.+)', texto)
        tenant = m_tenant.group(1).strip() if m_tenant else ""

        partes = []
        if azure_ad:
            partes.append(f"Entra ID (Azure AD){f' — {tenant}' if tenant else ''}")
        if domain:
            partes.append("Dominio local (Active Directory)")
        if enterprise:
            partes.append("Hybrid/Enterprise")
        if workplace:
            partes.append("Cuenta de trabajo/estudio registrada")

        if not partes:
            return "Grupo de trabajo (sin Entra ID ni dominio)"
        return " + ".join(partes)
    except Exception:
        return ""


class UsersCollector(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(list)
    finished_error = pyqtSignal(str)
    tipo_union = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        """Solicita detener el análisis de forma controlada."""
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(20, "Escaneando usuarios del sistema...")
            self.tipo_union.emit(_detectar_tipo_union_equipo())
            users = run_powershell_json(PS_GET_USERS, timeout=45)

            if not users:
                self.progress.emit(100, "No se encontraron usuarios")
                self.finished_ok.emit([])
                return

            if isinstance(users, dict):
                users = [users]

            rows = []
            total = len(users)
            for idx, user in enumerate(users):
                if self._stop_requested:
                    self.progress.emit(100, "Análisis detenido por el usuario")
                    self.finished_ok.emit(rows)
                    return

                self.progress.emit(40 + int((idx / total) * 60), f"Procesando usuario {idx+1}/{total}...")

                rows.append(UserRow(
                    nombre=user.get("Nombre", ""),
                    nombre_completo=user.get("NombreCompleto", ""),
                    tipo=user.get("Tipo", ""),
                    dominio=user.get("Dominio", ""),
                    estado=user.get("Estado", ""),
                    ultima_conexion=user.get("UltimaConexion", ""),
                    fecha_creacion=user.get("FechaCreacion", ""),
                    descripcion=user.get("Descripcion", ""),
                    grupos=user.get("Grupos", ""),
                    sid=user.get("SID", ""),
                    password_expires=user.get("PasswordExpires", ""),
                    password_required=user.get("PasswordRequired", ""),
                    cuenta_vinculada=user.get("CuentaVinculada", "")
                ))

            self.progress.emit(100, "Completado")
            self.finished_ok.emit(rows)
        except Exception as e:
            logger.exception(f"Error en UsersCollector: {e}")
            self.finished_error.emit(str(e))

class RemoteConnectionCollector(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(list)
    finished_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(20, "Consultando registro de eventos de seguridad...")
            data = run_powershell_json(PS_GET_REMOTE_CONNECTIONS, timeout=60)

            # None (timeout/error real, p. ej. sin permisos de administrador
            # para leer el log de Seguridad) y [] (0 accesos remotos, p. ej.
            # porque la directiva de auditoría no está habilitada — un
            # resultado real y distinto) eran indistinguibles.
            if data is None:
                self.finished_error.emit(
                    "Tiempo agotado o error al consultar el registro de eventos de "
                    "seguridad. Comprueba que MASys se ejecuta como administrador.")
                return
            if not data:
                self.progress.emit(100, "No se encontraron conexiones remotas")
                self.finished_ok.emit([])
                return

            if isinstance(data, dict):
                data = [data]

            rows = []
            total = len(data)
            for idx, item in enumerate(data):
                if self._stop_requested:
                    self.progress.emit(100, "Análisis detenido")
                    self.finished_ok.emit(rows)
                    return
                self.progress.emit(40 + int((idx / max(total, 1)) * 60),
                                   f"Procesando evento {idx+1}/{total}...")
                rows.append(RemoteConnectionRow(
                    fecha=item.get("Fecha", ""),
                    usuario=item.get("Usuario", ""),
                    ip_origen=item.get("IPOrigen", ""),
                    tipo_acceso=item.get("TipoAcceso", ""),
                    resultado=item.get("Resultado", ""),
                    workstation=item.get("Workstation", ""),
                    proceso=item.get("Proceso", ""),
                    id_evento=item.get("IdEvento", ""),
                ))

            self.progress.emit(100, "Completado")
            self.finished_ok.emit(rows)
        except Exception as e:
            logger.exception(f"Error en RemoteConnectionCollector: {e}")
            self.finished_error.emit(str(e))

class ActiveSessionCollector(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(list)
    finished_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(20, "Consultando sesiones activas...")
            data = run_powershell_json(PS_GET_ACTIVE_SESSIONS, timeout=30)
            if not data:
                self.finished_ok.emit([])
                return
            if isinstance(data, dict):
                data = [data]
            rows = []
            for item in data:
                if self._stop_requested:
                    break
                rows.append(ActiveSessionRow(
                    usuario=item.get("Usuario", ""),
                    sesion=item.get("Sesion", ""),
                    id=item.get("ID", ""),
                    estado=item.get("Estado", ""),
                    tiempo_inactivo=item.get("TiempoInactivo", ""),
                    inicio=item.get("Inicio", ""),
                    tipo=item.get("Tipo", ""),
                ))

            # Enriquecer las sesiones RDP con su IP de origen — correlada
            # con el evento 1149 más reciente del mismo usuario (no hay
            # una IP de cliente disponible por WMI en esta edición de
            # Windows, ver PS_GET_RDP_CONNECTION_HISTORY).
            if any("rdp" in r.tipo.lower() for r in rows) and not self._stop_requested:
                self.progress.emit(70, "Buscando IP de origen de sesiones RDP...")
                try:
                    historial = run_powershell_json(PS_GET_RDP_CONNECTION_HISTORY, timeout=30)
                    if isinstance(historial, dict):
                        historial = [historial]
                    for r in rows:
                        if "rdp" not in r.tipo.lower():
                            continue
                        coincidencia = next(
                            (h for h in (historial or [])
                             if (h.get("Usuario") or "").lower() == r.usuario.lower()), None)
                        if coincidencia:
                            r.origen_ip = coincidencia.get("IP", "")
                except Exception as e:
                    logger.warning(f"No se pudo correlacionar IP de origen RDP: {e}")

            self.progress.emit(100, "Completado")
            self.finished_ok.emit(rows)
        except Exception as e:
            logger.exception(f"Error en ActiveSessionCollector: {e}")
            self.finished_error.emit(str(e))


class RDPConnectionHistoryCollector(QThread):
    """Histórico de conexiones RDP autenticadas (últimas 100, evento 1149)
    — para el diálogo "Historial de conexiones RDP"."""
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(list)
    finished_error = pyqtSignal(str)

    def run(self):
        try:
            self.progress.emit(30, "Consultando historial de conexiones RDP...")
            data = run_powershell_json(PS_GET_RDP_CONNECTION_HISTORY, timeout=30)
            if not data:
                self.finished_ok.emit([])
                return
            if isinstance(data, dict):
                data = [data]
            rows = [
                RDPConnectionRow(
                    usuario=item.get("Usuario", ""),
                    dominio=item.get("Dominio", ""),
                    ip_origen=item.get("IP", ""),
                    fecha=item.get("Fecha", ""),
                )
                for item in data
            ]
            self.progress.emit(100, "Completado")
            self.finished_ok.emit(rows)
        except Exception as e:
            logger.exception(f"Error en RDPConnectionHistoryCollector: {e}")
            self.finished_error.emit(str(e))


class BluetoothCollector(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(list)
    finished_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(20, "Escaneando dispositivos Bluetooth...")
            data = run_powershell_json(PS_GET_BLUETOOTH, timeout=30)
            # None = error/timeout real; [] = sin dispositivos Bluetooth
            # (válido — PS_GET_BLUETOOTH ya devuelve '[]' explícito en ese
            # caso, nunca 'null', así que None aquí es siempre error real).
            if data is None:
                self.progress.emit(100, "No se pudo completar el escaneo")
                self.finished_error.emit(
                    "Tiempo agotado o error al consultar los dispositivos Bluetooth del sistema.")
                return
            if not data:
                self.finished_ok.emit([])
                return
            if isinstance(data, dict):
                data = [data]
            rows = []
            for item in data:
                if self._stop_requested:
                    break
                rows.append(BluetoothRow(
                    nombre=item.get("Nombre", ""),
                    fabricante=item.get("Fabricante", ""),
                    estado=item.get("Estado", ""),
                    clase=item.get("Clase", ""),
                    instance_id=item.get("InstanceId", ""),
                ))
            self.progress.emit(100, "Completado")
            self.finished_ok.emit(rows)
        except Exception as e:
            logger.exception(f"Error en BluetoothCollector: {e}")
            self.finished_error.emit(str(e))

class PSHistoryCollector(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(list)
    finished_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(20, "Leyendo registro de bloques de script...")
            eventos = run_powershell_json(PS_GET_PS_HISTORY_EVENTOS, timeout=30)

            self.progress.emit(60, "Leyendo historial local de PowerShell...")
            archivo = run_powershell_json(PS_GET_PS_HISTORY, timeout=15)

            # Ambas consultas ya capturan sus propios errores internamente
            # y devuelven '[]' (nunca None) salvo fallo real del propio
            # subprocess de PowerShell — None en las DOS a la vez sí es un
            # error real que merece avisar, no una lista vacía silenciosa.
            if eventos is None and archivo is None:
                self.finished_error.emit(
                    "Tiempo agotado o error al leer el historial de PowerShell.")
                return

            if isinstance(eventos, dict):
                eventos = [eventos]
            if isinstance(archivo, dict):
                archivo = [archivo]

            rows = []
            numero = 1

            # Fuente 1: registro de eventos (4104) — fecha/hora real, y
            # captura lo ejecutado de verdad (scripts incluidos), no solo
            # lo tecleado a mano. Se muestra primero por ser más rica.
            for item in (eventos or []):
                if self._stop_requested:
                    break
                comando = (item.get("Comando") or "").strip()
                if not comando:
                    continue
                rows.append(PSHistoryRow(
                    numero=str(numero), comando=comando,
                    fecha_hora=item.get("FechaHora", "Desconocida"),
                    origen="Registro de eventos (bloque de script)",
                ))
                numero += 1

            # Fuente 2: historial local de PSReadLine — SIN fecha/hora real
            # (el archivo no la guarda), se muestra explícito en vez de
            # dejarlo en blanco o inventar una. Complementa al registro de
            # eventos cuando ese registro está vacío o no disponible.
            for item in (archivo or []):
                if self._stop_requested:
                    break
                comando = (item.get("Comando") or "").strip()
                if not comando:
                    continue
                rows.append(PSHistoryRow(
                    numero=str(numero), comando=comando,
                    fecha_hora="Desconocida (no hay registro de eventos para este comando)",
                    origen="Historial local (PSReadLine)",
                ))
                numero += 1

            self.progress.emit(100, "Completado")
            self.finished_ok.emit(rows)
        except Exception as e:
            logger.exception(f"Error en PSHistoryCollector: {e}")
            self.finished_error.emit(str(e))

class SystemEventCollector(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(list)
    finished_error = pyqtSignal(str)

    def __init__(self, dias: int = 7, incluir_security: bool = False):
        super().__init__()
        self._stop_requested = False
        self._dias = max(1, min(90, dias))
        self._incluir_security = incluir_security

    def request_stop(self):
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(20, f"Consultando eventos de los últimos {self._dias} días...")
            ps_command = (PS_GET_SYSTEM_EVENTS
                           .replace("DIAS_PLACEHOLDER", str(self._dias))
                           .replace("INCLUIR_SECURITY_PLACEHOLDER", "true" if self._incluir_security else "false"))
            data = run_powershell_json(ps_command, timeout=90)

            # None (timeout/error real) y [] (0 eventos de verdad, un
            # resultado legítimo) eran indistinguibles — mismo bug ya
            # corregido en Actualizaciones/Controladores. El script PS ya no
            # traga los errores en un '[]' de reserva, así que aquí sí se
            # puede diferenciar de verdad.
            if data is None:
                self.finished_error.emit(
                    "Tiempo agotado o error al consultar el registro de eventos de Windows.")
                return
            if not data:
                self.finished_ok.emit([])
                return
            if isinstance(data, dict):
                data = [data]
            rows = []
            total = len(data)
            for idx, item in enumerate(data):
                if self._stop_requested:
                    break
                self.progress.emit(30 + int((idx / max(total, 1)) * 70),
                                   f"Procesando evento {idx+1}/{total}...")
                rows.append(SystemEventRow(
                    fecha=item.get("Fecha", ""),
                    nivel=item.get("Nivel", ""),
                    log=item.get("Log", ""),
                    fuente=item.get("Fuente", ""),
                    id_evento=str(item.get("IdEvento", "")),
                    mensaje=item.get("Mensaje", ""),
                ))
            self.progress.emit(100, "Completado")
            self.finished_ok.emit(rows)
        except Exception as e:
            logger.exception(f"Error en SystemEventCollector: {e}")
            self.finished_error.emit(str(e))

def _tam_legible(size_bytes: int) -> str:
    if size_bytes < 0:
        return "Desconocido"
    tam = float(size_bytes)
    for unidad in ("B", "KB", "MB", "GB", "TB"):
        if tam < 1024:
            return f"{int(tam)} B" if unidad == "B" else f"{tam:.2f} {unidad}"
        tam /= 1024
    return f"{tam:.2f} PB"

def _fecha_legible(timestamp: float) -> str:
    try:
        return datetime.fromtimestamp(timestamp).strftime("%d/%m/%Y %H:%M")
    except (OSError, ValueError, OverflowError):
        return "—"

# _es_reparse_point vive en msys.core.utils (compartido con table_models.py,
# que no puede importar de este módulo sin crear una dependencia circular).

class ContentSearchEngine(QThread):
    """Búsqueda avanzada de la pestaña Contenido: puerto de la lógica de
    MaXPlorer (I:\\...\\BUSCADOR\\maxplorer.py, clase SearchEngine, hilo
    threading.Thread + queue.Queue + polling) a QThread/pyqtSignal — no
    hace falta cola ni polling, PyQt ya entrega las señales al hilo de la
    UI. Acepta VARIAS raíces (una lista de carpetas, no una única
    ubicación) porque el ámbito de búsqueda son las categorías ya
    existentes de Contenido (Perfil Usuario / Escritorio / C:\\ No Común),
    resueltas por el diálogo con get_user_content_folders() /
    get_carpetas_no_comunes_c().

    Dos correcciones sobre el comportamiento original de MaXPlorer:
    1. Regex inválida: el original metía el error en la cola pero seguía
       buscando SIN filtro (mostraba todo, como si el término no
       existiera) — aquí aborta con error_fatal antes de arrancar.
    2. Permisos denegados en modo recursivo: el original recorría con
       os.walk(location) sin onerror, así que un PermissionError en una
       subcarpeta se tragaba en silencio — ni contaba como aviso ni se
       notificaba. Aquí se cuentan y se informan en el resumen final,
       igual que ya hacía (solo) el modo sin subcarpetas.

    Además, el tamaño de una carpeta que matchea NO se recalcula con un
    os.walk propio por carpeta (así lo hacía _folder_size en el original,
    repitiendo E/S ya hecha por el recorrido principal): se acumula en la
    misma pasada bottom-up (os.walk topdown=False visita los hijos antes
    que el padre, así que el tamaño de un directorio ya está cerrado
    cuando le toca evaluarlo)."""

    progreso = pyqtSignal(str)
    resultado = pyqtSignal(list)
    error_fatal = pyqtSignal(str)
    finalizado = pyqtSignal(int, int, int)  # archivos, carpetas, avisos

    _TAM_LOTE = 200

    def __init__(self, raices, tipo, termino, use_regex, extensiones,
                 min_size, max_size, dias, incluir_subcarpetas):
        super().__init__()
        self._raices = list(raices)
        self._tipo = tipo  # "file" | "folder" | "all"
        self._extensiones = ([e.strip().lower() for e in extensiones if e.strip()]
                              if extensiones else [])
        self._min_size = min_size
        self._max_size = max_size
        self._incluir_subcarpetas = incluir_subcarpetas
        self._stop_requested = False
        self._avisos = 0
        self._lote = []

        self._pattern = None
        self._error_regex = None
        if termino:
            try:
                crudo = termino if use_regex else re.escape(termino)
                self._pattern = re.compile(crudo, re.IGNORECASE)
            except re.error as e:
                self._error_regex = str(e)

        self._fecha_limite = None
        if dias and dias > 0:
            self._fecha_limite = (datetime.now() - timedelta(days=dias)).timestamp()

    def request_stop(self):
        self._stop_requested = True

    # ── Ejecución ──────────────────────────────────────────────

    def run(self):
        if self._error_regex:
            self.error_fatal.emit(f"Expresión regular inválida: {self._error_regex}")
            return

        archivos = 0
        carpetas = 0
        try:
            for raiz in self._raices:
                if self._stop_requested:
                    break
                if not os.path.isdir(raiz):
                    self._avisos += 1
                    continue
                self.progreso.emit(f"Buscando en {raiz}...")
                archivos, carpetas = (
                    self._buscar_recursivo(raiz, archivos, carpetas)
                    if self._incluir_subcarpetas else
                    self._buscar_plano(raiz, archivos, carpetas)
                )

            self._vaciar_lote()
        except Exception as e:
            logger.exception(f"Error en ContentSearchEngine: {e}")
            self.error_fatal.emit(f"Error inesperado: {e}")
            return

        self.finalizado.emit(archivos, carpetas, self._avisos)

    def _on_walk_error(self, _exc):
        self._avisos += 1

    def _listar_seguro(self, raiz):
        """Generador equivalente a os.walk(raiz, topdown=False) (hijos antes
        que el padre, para acumular tamaños en una sola pasada) pero que
        NUNCA desciende a un reparse point (junction de NTFS).

        BUG REAL encontrado y corregido: la versión anterior usaba
        os.walk(topdown=False) directamente. Con topdown=False no se puede
        podar 'dirs' como se hace normalmente (dirs[:] = ...), porque para
        cuando el código ve la tupla ya se recorrió esa subcarpeta — así
        que followlinks=False (su valor por defecto) no bastaba para
        evitar junctions de compatibilidad que apuntan hacia atrás en el
        propio árbol (p. ej. 'C:\\Documents and Settings' -> 'C:\\Users'):
        tras un rato buscando en C:\\ con subcarpetas, la app se colgaba y
        cerraba — el mismo bug ya corregido antes en DiskSpaceScanThread.

        Iterativo (pila propia, no recursión de Python) para no toparse
        con el límite de profundidad de recursión en árboles muy anidados.
        Yields (root, dirs_reales, dirs_reparse, files) — dirs_reales ya
        se recorrieron (yield previo), dirs_reparse NUNCA se recorren."""
        pila = [(raiz, False)]
        listados = {}

        while pila:
            if self._stop_requested:
                return
            ruta, expandida = pila.pop()

            if expandida:
                dirs_reales, dirs_reparse, files = listados.pop(ruta)
                yield ruta, dirs_reales, dirs_reparse, files
                continue

            try:
                entradas = list(os.scandir(ruta))
            except OSError:
                self._avisos += 1
                continue

            dirs_reales, dirs_reparse, files = [], [], []
            for entrada in entradas:
                try:
                    if entrada.is_dir(follow_symlinks=False):
                        st = entrada.stat(follow_symlinks=False)
                        if st.st_file_attributes & _FILE_ATTRIBUTE_REPARSE_POINT:
                            dirs_reparse.append(entrada.name)
                        else:
                            dirs_reales.append(entrada.name)
                    elif entrada.is_file(follow_symlinks=False):
                        files.append(entrada.name)
                except OSError:
                    self._avisos += 1

            listados[ruta] = (dirs_reales, dirs_reparse, files)
            pila.append((ruta, True))
            for nombre in dirs_reales:
                pila.append((os.path.join(ruta, nombre), False))

    def _buscar_recursivo(self, raiz, archivos, carpetas):
        necesita_tamano_carpeta = self._tipo in ("folder", "all")
        tamanos_acumulados = {}

        for root, dirs_reales, dirs_reparse, files in self._listar_seguro(raiz):
            if self._stop_requested:
                break

            tam_dir = 0
            if self._tipo in ("file", "all"):
                for name in files:
                    full = os.path.join(root, name)
                    try:
                        st = os.stat(full)
                    except OSError:
                        self._avisos += 1
                        continue
                    tam_dir += st.st_size
                    fila = self._evaluar(name, full, root, "file", st, st.st_size)
                    if fila:
                        archivos += 1
                        self._agregar(fila)
            else:
                for name in files:
                    try:
                        tam_dir += os.path.getsize(os.path.join(root, name))
                    except OSError:
                        self._avisos += 1

            for name in dirs_reales:
                tam_dir += tamanos_acumulados.pop(os.path.join(root, name), 0)

            # Los reparse points nunca se recorren, pero sí se muestran (con
            # tamaño 0) si matchean — igual criterio que DiskSpaceScanThread:
            # ocultar sin más una carpeta visible en el Explorer confundiría
            # más que ayudar.
            if necesita_tamano_carpeta:
                for name in dirs_reparse:
                    ruta_reparse = os.path.join(root, name)
                    try:
                        st = os.stat(ruta_reparse, follow_symlinks=False)
                    except OSError:
                        self._avisos += 1
                        continue
                    fila = self._evaluar(name, ruta_reparse, root, "folder", st, 0)
                    if fila:
                        carpetas += 1
                        self._agregar(fila)

            tamanos_acumulados[root] = tam_dir

            if necesita_tamano_carpeta:
                try:
                    st = os.stat(root)
                except OSError:
                    self._avisos += 1
                    continue
                # 'root' == raiz también es un candidato válido: en el
                # ámbito "C:\ No Común", raiz ES la carpeta de primer nivel
                # que el inventario ya trata como un elemento por derecho
                # propio, así que también debe poder matchear por nombre.
                fila = self._evaluar(os.path.basename(root.rstrip("\\/")), root,
                                      os.path.dirname(root), "folder", st, tam_dir)
                if fila:
                    carpetas += 1
                    self._agregar(fila)

        return archivos, carpetas

    def _buscar_plano(self, raiz, archivos, carpetas):
        try:
            entradas = list(os.scandir(raiz))
        except (PermissionError, OSError):
            self._avisos += 1
            return archivos, carpetas

        for entrada in entradas:
            if self._stop_requested:
                break
            try:
                st = entrada.stat(follow_symlinks=False)
            except OSError:
                self._avisos += 1
                continue

            if entrada.is_file(follow_symlinks=False) and self._tipo in ("file", "all"):
                fila = self._evaluar(entrada.name, entrada.path, raiz, "file", st, st.st_size)
                if fila:
                    archivos += 1
                    self._agregar(fila)
            elif entrada.is_dir(follow_symlinks=False) and self._tipo in ("folder", "all"):
                tam = self._tamano_carpeta(entrada.path)
                fila = self._evaluar(entrada.name, entrada.path, raiz, "folder", st, tam)
                if fila:
                    carpetas += 1
                    self._agregar(fila)

        return archivos, carpetas

    def _tamano_carpeta(self, path):
        """A diferencia de _listar_seguro (necesaria para _buscar_recursivo,
        que necesita el tamaño de CADA subcarpeta), aquí solo hace falta un
        total acumulado — con topdown=True (por defecto) sí se puede podar
        'dirs' in situ antes de que os.walk descienda, así que no hace
        falta la pila propia: mismo bug de junctions que _buscar_recursivo,
        pero con una corrección más simple al alcanzarlo a tiempo."""
        total = 0
        try:
            for r, dirs, files in os.walk(path, onerror=self._on_walk_error):
                if self._stop_requested:
                    break
                dirs[:] = [d for d in dirs if not _es_reparse_point(os.path.join(r, d))]
                for name in files:
                    try:
                        total += os.path.getsize(os.path.join(r, name))
                    except OSError:
                        self._avisos += 1
        except OSError:
            self._avisos += 1
        return total

    def _evaluar(self, nombre, ruta_completa, directorio, tipo, st, size):
        if self._pattern and not self._pattern.search(nombre):
            return None
        if self._extensiones and tipo == "file":
            ext = os.path.splitext(nombre)[1].lower()
            if ext not in self._extensiones:
                return None
        if self._fecha_limite and st.st_mtime < self._fecha_limite:
            return None
        if not (self._min_size <= size <= self._max_size):
            return None
        return {
            "nombre": nombre,
            "ruta": ruta_completa,
            "directorio": directorio,
            "tamano": size,
            "tamano_fmt": _tam_legible(size),
            "modificado": st.st_mtime,
            "modificado_fmt": _fecha_legible(st.st_mtime),
            "tipo": tipo,
        }

    def _agregar(self, fila):
        self._lote.append(fila)
        if len(self._lote) >= self._TAM_LOTE:
            self._vaciar_lote()

    def _vaciar_lote(self):
        if self._lote:
            self.resultado.emit(self._lote)
            self._lote = []

class ContentTrashThread(QThread):
    """Envía a la papelera de reciclaje cada ruta de 'rutas', una por una,
    en su propio hilo — mismo motivo que RecycleBinMoveThread: con cientos
    de resultados (sobre todo en rutas de red/SMB, donde cada llamada es
    un round-trip) el bucle síncrono en el hilo de UI se notaría como un
    parón sin ningún feedback."""

    progreso = pyqtSignal(int, int, str)  # actual, total, nombre
    finished_ok = pyqtSignal(list, list)  # enviadas (rutas), errores

    def __init__(self, rutas: list):
        super().__init__()
        self._rutas = rutas
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def run(self):
        enviadas = []
        errores = []
        total = len(self._rutas)
        for idx, ruta in enumerate(self._rutas, start=1):
            if self._stop_requested:
                errores.append(f"Detenido por el usuario: {total - idx + 1} elemento(s) sin enviar a la papelera")
                break
            self.progreso.emit(idx, total, os.path.basename(ruta.rstrip("\\/")) or ruta)
            try:
                send2trash(ruta)
                enviadas.append(ruta)
            except Exception as e:
                errores.append(f"{ruta}: {e}")
        self.finished_ok.emit(enviadas, errores)


class ContentCopyMoveThread(QThread):
    """Copia o mueve a 'destino' cada elemento de 'items' (resultados de la
    pestaña Contenido: archivos O carpetas) — en su propio hilo porque
    puede tratarse de muchos elementos o carpetas grandes (mover entre
    unidades distintas es una copia+borrado real, no un rename atómico).

    'decisiones' ({nombre: 'sobrescribir'|'renombrar'|'omitir'}) llega YA
    resuelta desde el hilo de UI (msys.core.utils.preguntar_colision no
    puede llamarse dentro de un QThread) — mismo patrón que
    RecycleBinMoveThread."""

    progreso = pyqtSignal(int, int, str)  # actual, total, nombre
    finished_ok = pyqtSignal(int, int, list)  # procesados, omitidos, errores

    def __init__(self, items: list, destino: str, modo: str, decisiones: dict):
        super().__init__()
        self._items = items
        self._destino = destino
        self._modo = modo  # "copiar" | "mover"
        self._decisiones = decisiones
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def run(self):
        errores = []
        procesados = 0
        omitidos = 0
        total = len(self._items)

        for idx, fila in enumerate(self._items, start=1):
            if self._stop_requested:
                pendientes = total - idx + 1
                errores.append(f"Detenido por el usuario: {pendientes} elemento(s) sin procesar")
                break

            self.progreso.emit(idx, total, fila["nombre"])
            origen = fila["ruta"]
            if not os.path.exists(origen):
                errores.append(f"{fila['nombre']}: ya no existe en disco")
                continue

            destino_final = os.path.join(self._destino, fila["nombre"])
            accion = self._decisiones.get(fila["nombre"])
            if accion == "omitir":
                omitidos += 1
                continue
            if accion == "renombrar":
                destino_final = ruta_destino_sin_colision(self._destino, fila["nombre"])
            elif accion is None and os.path.exists(destino_final):
                # 5.4.0: dos elementos con el MISMO nombre en la selección (p. ej. «repetido.txt» de dos
                # carpetas): el segundo machacaba al primero sin avisar (probado). Ahora se renombra.
                destino_final = ruta_destino_sin_colision(self._destino, fila["nombre"])

            try:
                if accion == "sobrescribir" and os.path.exists(destino_final):
                    if os.path.isdir(destino_final):
                        shutil.rmtree(destino_final)
                    else:
                        os.remove(destino_final)

                if self._modo == "mover":
                    shutil.move(origen, destino_final)
                elif os.path.isdir(origen):
                    shutil.copytree(origen, destino_final)
                else:
                    shutil.copy2(origen, destino_final)
                procesados += 1
            except OSError as e:
                errores.append(f"{fila['nombre']}: {e}")

        self.finished_ok.emit(procesados, omitidos, errores)


class ContentZipThread(QThread):
    """Comprime a 'destino_zip' cada elemento de 'items' (archivos y/o
    carpetas, con su estructura relativa preservada dentro del zip) — en
    su propio hilo porque puede tratarse de muchos archivos o carpetas
    grandes. Usa dirs[:] para podar reparse points igual que
    ContentSearchEngine._tamano_carpeta: comprimir una carpeta con una
    junction NTFS hacia atrás con os.walk sin protección repetiría el
    mismo bug ya corregido ahí."""

    progreso = pyqtSignal(int, int, str)  # actual, total, nombre
    finished_ok = pyqtSignal(int, list)  # agregados, errores

    def __init__(self, items: list, destino_zip: str):
        super().__init__()
        self._items = items
        self._destino_zip = destino_zip
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def run(self):
        errores = []
        agregados = 0
        total = len(self._items)

        try:
            with zipfile.ZipFile(self._destino_zip, "w", zipfile.ZIP_DEFLATED) as zf:
                for idx, fila in enumerate(self._items, start=1):
                    if self._stop_requested:
                        errores.append(f"Detenido por el usuario: {total - idx + 1} elemento(s) sin comprimir")
                        break
                    self.progreso.emit(idx, total, fila["nombre"])
                    ruta = fila["ruta"]
                    if not os.path.exists(ruta):
                        errores.append(f"{fila['nombre']}: ya no existe en disco")
                        continue
                    try:
                        if os.path.isdir(ruta):
                            base = os.path.dirname(ruta.rstrip("\\/"))
                            for root, dirs, files in os.walk(ruta):
                                if self._stop_requested:
                                    break
                                dirs[:] = [d for d in dirs if not _es_reparse_point(os.path.join(root, d))]
                                for name in files:
                                    full = os.path.join(root, name)
                                    arcname = os.path.relpath(full, base)
                                    zf.write(full, arcname)
                        else:
                            zf.write(ruta, fila["nombre"])
                        agregados += 1
                    except OSError as e:
                        errores.append(f"{fila['nombre']}: {e}")
        except OSError as e:
            errores.append(f"No se pudo crear el archivo zip: {e}")

        self.finished_ok.emit(agregados, errores)


class RecycleBinListThread(QThread):
    """Enumera el contenido de la Papelera de Reciclaje de Windows vía COM
    (Shell.Application, Namespace(10)) — no existe una API nativa de Python
    para esto. Se ejecuta en su propio hilo porque GetDetailsOf() hace una
    llamada COM por columna y por elemento; con una papelera de miles de
    archivos, hacerlo en el hilo de UI la congelaría.

    Los índices de columna (nombre/ubicación original/tamaño/fecha) NO son
    fijos: dependen del idioma de Windows. Se detectan en tiempo de
    ejecución comparando el texto de cada cabecera (GetDetailsOf(None, i))
    contra fragmentos conocidos en español e inglés, en vez de asumir un
    índice fijo que solo sería correcto en un idioma."""

    finished_ok = pyqtSignal(list)
    finished_error = pyqtSignal(str)

    def run(self):
        pythoncom.CoInitialize()
        try:
            shell = win32com.client.Dispatch("Shell.Application")
            papelera = shell.Namespace(10)
            if papelera is None:
                self.finished_error.emit("No se pudo acceder a la Papelera de Reciclaje.")
                return

            idx_ubicacion = idx_tamano = idx_fecha = None
            for i in range(40):
                cabecera = str(papelera.GetDetailsOf(None, i) or "").strip().lower()
                if not cabecera:
                    if i > 15:
                        break
                    continue
                if idx_ubicacion is None and ("ubicaci" in cabecera or "original location" in cabecera):
                    idx_ubicacion = i
                elif idx_tamano is None and cabecera in ("tamaño", "size"):
                    idx_tamano = i
                elif idx_fecha is None and ("eliminaci" in cabecera or "date deleted" in cabecera):
                    idx_fecha = i

            filas = []
            for item in papelera.Items():
                try:
                    nombre = papelera.GetDetailsOf(item, 0) or item.Name
                    ubicacion = papelera.GetDetailsOf(item, idx_ubicacion) if idx_ubicacion is not None else ""
                    tamano_txt = papelera.GetDetailsOf(item, idx_tamano) if idx_tamano is not None else ""
                    fecha_txt = papelera.GetDetailsOf(item, idx_fecha) if idx_fecha is not None else ""
                    filas.append({
                        "nombre": nombre,
                        "ubicacion_original": ubicacion,
                        "tamano": tamano_txt,
                        "fecha_eliminacion": fecha_txt,
                        "ruta_papelera": item.Path,
                        "es_carpeta": bool(item.IsFolder),
                    })
                except Exception as e:
                    logger.warning(f"Elemento de papelera ilegible, se omite: {e}")
                    continue

            self.finished_ok.emit(filas)
        except Exception as e:
            logger.exception(f"Error en RecycleBinListThread: {e}")
            self.finished_error.emit(str(e))
        finally:
            pythoncom.CoUninitialize()


class RecycleBinMoveThread(QThread):
    """Mueve a 'destino' los elementos ya restaurados a su ubicación
    original (RecycleBinDialog._restaurar_en_otra_ubicacion) — en su propio
    hilo porque mover entre unidades distintas es una copia+borrado real
    (no un rename atómico) y puede tardar con carpetas grandes, a
    diferencia del resto de operaciones de este diálogo (COM rápidas sobre
    pocos elementos, hechas en el hilo de UI).

    'decisiones' ({nombre: 'sobrescribir'|'renombrar'|'omitir'}) llega YA
    resuelta desde el hilo de UI (RecycleBinDialog._preguntar_colision) —
    este hilo no puede mostrar diálogos, así que ninguna decisión se toma
    aquí dentro, solo se aplica."""

    progreso = pyqtSignal(int, int, str)  # actual, total, nombre
    finished_ok = pyqtSignal(int, int, list)  # movidas, omitidas, errores

    def __init__(self, restauradas: list, destino: str, decisiones: dict):
        super().__init__()
        self._restauradas = restauradas
        self._destino = destino
        self._decisiones = decisiones
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def run(self):
        errores = []
        movidas = 0
        omitidas = 0
        total = len(self._restauradas)

        for idx, fila in enumerate(self._restauradas, start=1):
            if self._stop_requested:
                pendientes = total - idx + 1
                errores.append(f"Detenido por el usuario: {pendientes} elemento(s) sin mover "
                                f"(quedan en su ubicación original, no en {self._destino})")
                break

            self.progreso.emit(idx, total, fila["nombre"])
            origen = os.path.join(fila["ubicacion_original"], fila["nombre"])
            if not os.path.exists(origen):
                errores.append(f"{fila['nombre']}: no se encontró tras restaurar en {fila['ubicacion_original']}")
                continue

            destino_final = os.path.join(self._destino, fila["nombre"])
            accion = self._decisiones.get(fila["nombre"])
            if accion == "omitir":
                omitidas += 1
                continue
            if accion == "renombrar":
                destino_final = ruta_destino_sin_colision(self._destino, fila["nombre"])
            elif accion is None and os.path.exists(destino_final):
                # 5.4.0: dos elementos con el MISMO nombre en la selección (p. ej. «repetido.txt» de dos
                # carpetas): el segundo machacaba al primero sin avisar (probado). Ahora se renombra.
                destino_final = ruta_destino_sin_colision(self._destino, fila["nombre"])

            try:
                if accion == "sobrescribir" and os.path.exists(destino_final):
                    if os.path.isdir(destino_final):
                        shutil.rmtree(destino_final)
                    else:
                        os.remove(destino_final)
                shutil.move(origen, destino_final)
                movidas += 1
            except OSError as e:
                errores.append(f"{fila['nombre']}: {e}")

        self.finished_ok.emit(movidas, omitidas, errores)


class NetworkAdapterCollector(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(list)
    finished_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        """Solicita detener el análisis de forma controlada."""
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(20, "Escaneando adaptadores de red...")
            adapters = run_powershell_json(PS_GET_NETWORK_ADAPTERS, timeout=60)

            if adapters is None:
                self.finished_error.emit(
                    "Tiempo agotado o error al escanear los adaptadores de red.")
                return
            if not adapters:
                self.progress.emit(100, "No se encontraron adaptadores de red")
                self.finished_ok.emit([])
                return

            # Asegurarse de que adapters es una lista
            if isinstance(adapters, dict):
                adapters = [adapters]
            elif not isinstance(adapters, list):
                self.progress.emit(100, "Formato de datos inesperado")
                self.finished_ok.emit([])
                return

            rows = []
            total = len(adapters)
            for idx, adapter in enumerate(adapters):
                if self._stop_requested:
                    self.progress.emit(100, "Análisis detenido por el usuario")
                    self.finished_ok.emit(rows)
                    return

                self.progress.emit(40 + int((idx / total) * 60), f"Procesando adaptador {idx+1}/{total}...")

                try:
                    rows.append(NetworkAdapterRow(
                        nombre=str(adapter.get("Nombre", "Sin nombre")),
                        descripcion=str(adapter.get("Descripcion", "Sin descripción")),
                        estado=str(adapter.get("Estado", "Desconocido")),
                        tipo=str(adapter.get("Tipo", "Otro")),
                        ip=str(adapter.get("IP", "N/A")),
                        mac=str(adapter.get("MAC", "N/A")),
                        configuracion_ip=str(adapter.get("ConfiguracionIP", "N/A")),
                        gateway=str(adapter.get("Gateway", "N/A")),
                        dns=str(adapter.get("DNS", "N/A")),
                        ipv6=str(adapter.get("IPv6", "N/A")),
                        velocidad=str(adapter.get("Velocidad", "N/A"))
                    ))
                except Exception as e:
                    logger.warning(f"Error procesando adaptador {idx}: {e}")
                    continue

            self.progress.emit(100, "Completado")
            self.finished_ok.emit(rows)
        except Exception as e:
            logger.exception(f"Error en NetworkAdapterCollector: {e}")
            self.finished_error.emit(f"Error al escanear adaptadores de red: {str(e)}")

class SecurityInfoCollector(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(dict)
    finished_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(30, "Consultando estado de seguridad...")
            # 5.4.0: 38 s medidos en el equipo de pruebas con límite 45: sin margen en equipos lentos
            data = run_powershell_json(PS_GET_SECURITY_INFO, timeout=120)
            self.progress.emit(100, "Completado")
            self.finished_ok.emit(data or {})
        except Exception as e:
            logger.exception(f"Error en SecurityInfoCollector: {e}")
            self.finished_error.emit(str(e))

class DiskHealthCollector(QThread):
    """Recopila en una sola pasada las 2 categorías de la sección Discos
    Duros: inventario físico + salud S.M.A.R.T. fusionados en una tabla
    (mismo disco, 1:1) y particiones aparte (granularidad distinta)."""
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(dict)
    finished_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(30, "Consultando discos y particiones...")
            data = run_powershell_json(PS_GET_DISK_HEALTH, timeout=30)

            # None (timeout/error real) y {} (0 discos, resultado
            # legítimo en una VM sin almacenamiento reportado por WMI) eran
            # indistinguibles — mismo bug ya corregido en otras secciones
            # esta sesión.
            if data is None:
                self.finished_error.emit(
                    "Tiempo agotado o error al consultar discos y particiones.")
                return
            if not isinstance(data, dict):
                data = {}

            inventario = data.get("Discos") or []
            salud = data.get("SaludDisco") or []
            # Discos y SaludDisco se construyen en el mismo bucle sobre
            # Get-PhysicalDisk en PS_GET_DISK_HEALTH, así que llegan
            # garantizados en el mismo orden y cantidad — zip() es seguro
            # aquí sin necesitar emparejar por el campo "Disco".
            discos = [DiscoFisicoRow(
                disco=str(d.get("Disco", "")),
                modelo=str(d.get("Modelo", "")),
                tipo=str(d.get("Tipo", "")),
                bus=str(d.get("Bus", "")),
                tamano=str(d.get("Tamano", "")),
                serial=str(d.get("Serial", "")),
                tipo_medio=str(s.get("TipoMedio", "")),
                estado_salud=str(s.get("EstadoSalud", "")),
                estado_operativo=str(s.get("EstadoOperativo", "")),
                temperatura=str(s.get("Temperatura", "N/A")),
                desgaste=str(s.get("Desgaste", "N/A")),
                horas_encendido=str(s.get("HorasEncendido", "N/A")),
                errores=str(s.get("Errores", "N/A")),
            ) for d, s in zip(inventario, salud)]

            particiones = [PartitionRow(
                disco=str(p.get("Disco", "")),
                letra=str(p.get("Letra", "")),
                etiqueta=str(p.get("Etiqueta", "")),
                sistema=str(p.get("Sistema", "")),
                tamano=str(p.get("Tamano", "")),
                usado=str(p.get("Usado", "")),
                libre=str(p.get("Libre", "")),
                porcentaje=str(p.get("Porcentaje", "")),
            ) for p in (data.get("Particiones") or [])]

            self.progress.emit(100, "Completado")
            self.finished_ok.emit({"discos": discos, "particiones": particiones})
        except Exception as e:
            logger.exception(f"Error en DiskHealthCollector: {e}")
            self.finished_error.emit(str(e))


class SystemCleanupThread(QThread):
    """Limpieza de la partición de sistema: puerto de Start-DiskCleanup
    (Mantenimientov2.ps1, proyecto MANTENIMIENTO V2) simplificado para MASys — ver PS_LIMPIEZA_SISTEMA
    para el detalle exacto de qué se limpia y por qué. La papelera de
    reciclaje se vacía aparte, aquí mismo en Python, con la misma llamada
    SHEmptyRecycleBinW que ya usa RecycleBinDialog (todas las unidades
    fijas, no solo la de sistema).

    A diferencia de SFCDISMRepairThread/CHKDSKRepairThread, esto es UNA
    única llamada bloqueante a PowerShell (run_powershell_json), no un
    proceso propio leído carácter a carácter — no hay handle al que pedir
    terminate() a mitad, así que este hilo NO admite request_stop(): una
    vez lanzado, corre hasta terminar o hasta el timeout."""

    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(dict)  # {categorias, espacio_liberado, espacio_libre_antes, espacio_libre_despues}
    finished_error = pyqtSignal(str)

    SHERB_NOCONFIRMATION = 0x00000001
    SHERB_NOPROGRESSUI = 0x00000002
    SHERB_NOSOUND = 0x00000004

    def run(self):
        try:
            unidad_sistema = os.environ.get("SystemDrive", "C:") + "\\"
            try:
                libre_antes = shutil.disk_usage(unidad_sistema).free
            except OSError:
                libre_antes = 0

            self.progress.emit(20, "Limpiando temporales, cachés y archivos de sistema...")
            data = run_powershell_json(PS_LIMPIEZA_SISTEMA, timeout=600)
            if data is None:
                self.finished_error.emit("Tiempo agotado o error al limpiar el sistema.")
                return
            if not isinstance(data, dict):
                data = {}
            categorias = {str(k): int(v) for k, v in data.items()}

            self.progress.emit(80, "Vaciando papelera de reciclaje (todas las unidades)...")
            resultado_papelera = ctypes.windll.shell32.SHEmptyRecycleBinW(
                None, None,
                self.SHERB_NOCONFIRMATION | self.SHERB_NOPROGRESSUI | self.SHERB_NOSOUND)
            if resultado_papelera == 0:
                categorias["Papelera de reciclaje (todas las unidades)"] = 0

            try:
                libre_despues = shutil.disk_usage(unidad_sistema).free
            except OSError:
                libre_despues = libre_antes

            self.progress.emit(100, "Completado")
            self.finished_ok.emit({
                "categorias": categorias,
                "espacio_liberado": max(0, libre_despues - libre_antes),
                "espacio_libre_antes": libre_antes,
                "espacio_libre_despues": libre_despues,
            })
        except Exception as e:
            logger.exception(f"Error en SystemCleanupThread: {e}")
            self.finished_error.emit(str(e))


_PATRONES_EXT_BASURA = {"tmp"}
_PATRONES_OFFICE_LOCK_EXT = {"doc", "docx", "xls", "xlsx", "ppt", "pptx"}

# Carpetas de caché/metadatos que se regeneran solas (macOS/Synology) —
# mismo nivel de seguridad que Thumbs.db, se borran enteras como unidad.
_CARPETAS_BASURA_REGENERABLE = {".spotlight-v100", ".fseventsd", ".temporaryitems", "@eadir"}
# Carpetas "papelera" de otros sistemas/herramientas que aparecen sueltas
# en discos externos o antiguos — vaciarlas es como vaciar una papelera:
# pierdes la recuperación, pero no es basura de otro tipo.
_CARPETAS_PAPELERA_RESIDUAL = {".trashes", "#recycle", "recycler"}
# _gsdata_ (GoodSync) NO se borra entera: contiene el estado de
# sincronización activo (_file_state._gs) además del historial de
# versiones. Solo sus subcarpetas _saved_/_history_ son papelera/versiones
# antiguas — investigado en help.goodsync.com antes de tocarlo.
_NOMBRE_GSDATA = "_gsdata_"
_SUBCARPETAS_GSDATA_LIMPIABLES = {"_saved_", "_history_"}

def _es_archivo_basura(nombre: str) -> bool:
    """Patrones de basura seguros y reconocibles en cualquier unidad —
    nunca un archivo real del usuario: *.tmp, Thumbs.db/.DS_Store (caché
    de miniaturas de Explorer/Finder, se regeneran solas), ._* (AppleDouble
    de macOS, Windows nunca los usa), y ~$*.doc/xls/ppt (bloqueos de
    Office huérfanos, solo existen mientras el archivo está abierto; si
    sobreviven es porque Office/el equipo se cerró mal)."""
    nombre_lower = nombre.lower()
    if nombre_lower in ("thumbs.db", ".ds_store"):
        return True
    if nombre_lower.startswith("._"):
        return True
    partes = nombre_lower.rsplit(".", 1)
    if len(partes) != 2:
        return False
    base, ext = partes
    if ext in _PATRONES_EXT_BASURA:
        return True
    if base.startswith("~$") and ext in _PATRONES_OFFICE_LOCK_EXT:
        return True
    return False

def _tamano_y_borrar_carpeta(path: str, stop_check) -> int:
    """Suma el tamaño de 'path' completo y lo borra como unidad — usado
    para carpetas basura regenerables/papelera residual, a diferencia de
    _es_archivo_basura que solo mira archivos sueltos. Junction-safe
    (poda dirs[:] igual que el resto de recorridos de este módulo)."""
    total = 0
    try:
        for root, dirs, files in os.walk(path):
            if stop_check():
                break
            dirs[:] = [d for d in dirs if not _es_reparse_point(os.path.join(root, d))]
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
        shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass
    return total


class DataDriveCleanupThread(QThread):
    """Limpieza 'adaptada' para una partición de DATOS (no la de sistema):
    a diferencia de SystemCleanupThread, aquí no hay basura de Windows que
    limpiar (temporales de perfil y cachés de navegador solo existen en
    C:) — se limita a lo que sí generaliza a cualquier unidad: la papelera
    de reciclaje DE ESA UNIDAD en concreto (a diferencia de
    SHEmptyRecycleBinW, que vacía todas a la vez), patrones de archivo
    basura seguros (*.tmp, Thumbs.db/.DS_Store, ~$* de Office, ._* de
    macOS), carpetas de caché regenerables de macOS/Synology
    (.Spotlight-V100, .fseventsd, .TemporaryItems, @eaDir), papeleras
    residuales de otros sistemas (.Trashes, #recycle, RECYCLER) y el
    historial de versiones de GoodSync (_gsdata_\\_saved_/_history_ —
    NUNCA el resto de _gsdata_, que es estado de sincronización activo,
    no basura).

    Recorrido iterativo (pila propia) y junction-safe (ver
    _es_reparse_point): un bucle de junctions en una unidad de datos
    causaría el mismo cuelgue ya corregido en ContentSearchEngine. Se
    excluyen explícitamente '$Recycle.Bin' (ya se trata aparte) y 'System
    Volume Information' (datos de instantáneas VSS de ESA unidad; tocarlos
    podría romper la Protección del sistema/puntos de restauración)."""

    progreso = pyqtSignal(str)
    finished_ok = pyqtSignal(dict)  # {categorias, espacio_liberado, espacio_libre_antes, espacio_libre_despues}
    finished_error = pyqtSignal(str)

    _CARPETAS_EXCLUIDAS = {"$recycle.bin", "system volume information"}

    def __init__(self, letra: str):
        super().__init__()
        self._letra = letra.rstrip(":")
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def _vaciar_papelera_unidad(self) -> int:
        liberado = 0
        rb_root = f"{self._letra}:\\$Recycle.Bin"
        if not os.path.isdir(rb_root):
            return 0
        try:
            entradas = list(os.scandir(rb_root))
        except OSError:
            return 0
        for entrada in entradas:
            if self._stop_requested:
                break
            try:
                if not entrada.is_dir(follow_symlinks=False):
                    continue
                for root, dirs, files in os.walk(entrada.path):
                    if self._stop_requested:
                        break
                    dirs[:] = [d for d in dirs if not _es_reparse_point(os.path.join(root, d))]
                    for name in files:
                        try:
                            liberado += os.path.getsize(os.path.join(root, name))
                        except OSError:
                            pass
                shutil.rmtree(entrada.path, ignore_errors=True)
            except OSError:
                continue
        return liberado

    def _limpiar_archivos_basura(self):
        """Recorre toda la unidad UNA sola vez clasificando cada carpeta
        encontrada en una de 4 categorías (regenerable/papelera residual/
        gsdata/normal a seguir recorriendo) y cada archivo suelto con
        _es_archivo_basura. Devuelve un dict con el desglose:
        {"archivos": (bytes, n), "carpetas_regenerables": (bytes, n),
         "papeleras_residuales": (bytes, n), "gsdata_historial": (bytes, n)}."""
        raiz = f"{self._letra}:\\"
        resultado = {
            "archivos": [0, 0],
            "carpetas_regenerables": [0, 0],
            "papeleras_residuales": [0, 0],
            "gsdata_historial": [0, 0],
        }
        pendientes = [raiz]
        while pendientes:
            if self._stop_requested:
                break
            actual = pendientes.pop()
            try:
                entradas = list(os.scandir(actual))
            except OSError:
                continue
            for entrada in entradas:
                if self._stop_requested:
                    break
                try:
                    if entrada.is_dir(follow_symlinks=False):
                        nombre_lower = entrada.name.lower()
                        if nombre_lower in self._CARPETAS_EXCLUIDAS:
                            continue
                        if _es_reparse_point(entrada.path):
                            continue
                        if nombre_lower in _CARPETAS_BASURA_REGENERABLE:
                            resultado["carpetas_regenerables"][0] += _tamano_y_borrar_carpeta(
                                entrada.path, lambda: self._stop_requested)
                            resultado["carpetas_regenerables"][1] += 1
                            continue
                        if nombre_lower in _CARPETAS_PAPELERA_RESIDUAL:
                            resultado["papeleras_residuales"][0] += _tamano_y_borrar_carpeta(
                                entrada.path, lambda: self._stop_requested)
                            resultado["papeleras_residuales"][1] += 1
                            continue
                        if nombre_lower == _NOMBRE_GSDATA:
                            # NUNCA se borra _gsdata_ entera: solo sus
                            # subcarpetas de historial/papelera, dejando
                            # intacto el estado de sincronización activo.
                            for sub in _SUBCARPETAS_GSDATA_LIMPIABLES:
                                ruta_sub = os.path.join(entrada.path, sub)
                                if os.path.isdir(ruta_sub):
                                    resultado["gsdata_historial"][0] += _tamano_y_borrar_carpeta(
                                        ruta_sub, lambda: self._stop_requested)
                                    resultado["gsdata_historial"][1] += 1
                            continue
                        pendientes.append(entrada.path)
                    elif entrada.is_file(follow_symlinks=False) and _es_archivo_basura(entrada.name):
                        try:
                            tam = entrada.stat(follow_symlinks=False).st_size
                            os.remove(entrada.path)
                            resultado["archivos"][0] += tam
                            resultado["archivos"][1] += 1
                        except OSError:
                            continue
                except OSError:
                    continue
        return resultado

    def run(self):
        try:
            raiz = f"{self._letra}:\\"
            try:
                libre_antes = shutil.disk_usage(raiz).free
            except OSError:
                libre_antes = 0

            self.progreso.emit(f"Vaciando papelera de la unidad {self._letra}:...")
            bytes_papelera = self._vaciar_papelera_unidad()

            categorias = {}
            if bytes_papelera > 0:
                categorias[f"Papelera de reciclaje ({self._letra}:)"] = bytes_papelera

            if not self._stop_requested:
                self.progreso.emit(f"Buscando archivos temporales y residuales en {self._letra}:...")
                desglose = self._limpiar_archivos_basura()

                bytes_arch, n_arch = desglose["archivos"]
                if bytes_arch > 0:
                    categorias[f"Archivos basura ({n_arch}: *.tmp, Thumbs.db/.DS_Store, "
                               "bloqueos de Office, restos de macOS)"] = bytes_arch

                bytes_regen, n_regen = desglose["carpetas_regenerables"]
                if bytes_regen > 0:
                    categorias[f"Cachés regenerables de macOS/Synology ({n_regen} carpeta(s): "
                               ".Spotlight-V100, .fseventsd, @eaDir...)"] = bytes_regen

                bytes_papel_res, n_papel_res = desglose["papeleras_residuales"]
                if bytes_papel_res > 0:
                    categorias[f"Papeleras residuales ({n_papel_res}: .Trashes/#recycle/RECYCLER)"] = bytes_papel_res

                bytes_gsdata, n_gsdata = desglose["gsdata_historial"]
                if bytes_gsdata > 0:
                    categorias[f"Historial de GoodSync ({n_gsdata} carpeta(s) _saved_/_history_, "
                               "estado de sincronización intacto)"] = bytes_gsdata

            try:
                libre_despues = shutil.disk_usage(raiz).free
            except OSError:
                libre_despues = libre_antes

            self.progreso.emit("Detenido por el usuario" if self._stop_requested else "Completado")
            self.finished_ok.emit({
                "categorias": categorias,
                "espacio_liberado": max(0, libre_despues - libre_antes),
                "espacio_libre_antes": libre_antes,
                "espacio_libre_despues": libre_despues,
            })
        except Exception as e:
            logger.exception(f"Error en DataDriveCleanupThread: {e}")
            self.finished_error.emit(str(e))


class DuplicateFilesFinderThread(QThread):
    """Busca archivos duplicados de al menos 'min_size_bytes' bajo 'raiz'.
    NO borra nada — solo propone grupos para que el técnico revise y elija
    qué copias enviar a la papelera desde el diálogo de resultados.

    Tres pasadas para no hashear archivos grandes sin necesidad:
    1) Agrupar por tamaño EXACTO — descarta la inmensa mayoría sin abrir
       ningún archivo.
    2) Sobre cada grupo con >1 candidato, hash PARCIAL (primer + último MB)
       para descartar coincidencias de tamaño que no son duplicados reales
       sin leer el archivo entero.
    3) Solo sobre los que sobreviven al hash parcial, hash COMPLETO
       (sha256) para confirmar que son bit a bit idénticos antes de
       proponerlos — el hash parcial por sí solo no basta para asegurar
       nada, es solo una poda barata.

    Recorrido iterativo (pila propia) y junction-safe (ver
    _es_reparse_point), mismo criterio que el resto de hilos de este
    módulo que recorren una unidad entera."""

    progreso = pyqtSignal(str)
    finished_ok = pyqtSignal(list)  # [{"tamano": int, "rutas": [str, ...]}, ...]
    finished_error = pyqtSignal(str)

    _CHUNK_HASH_PARCIAL = 1024 * 1024        # 1 MB
    _CHUNK_HASH_COMPLETO = 4 * 1024 * 1024   # 4 MB por bloque leído

    def __init__(self, raiz: str, min_size_bytes: int):
        super().__init__()
        self._raiz = raiz
        self._min_size = min_size_bytes
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def _listar_archivos_grandes(self) -> Dict[int, List[str]]:
        pendientes = [self._raiz]
        por_tamano: Dict[int, List[str]] = {}
        while pendientes:
            if self._stop_requested:
                break
            actual = pendientes.pop()
            try:
                entradas = list(os.scandir(actual))
            except OSError:
                continue
            for entrada in entradas:
                if self._stop_requested:
                    break
                try:
                    if entrada.is_dir(follow_symlinks=False):
                        if _es_reparse_point(entrada.path):
                            continue
                        pendientes.append(entrada.path)
                    elif entrada.is_file(follow_symlinks=False):
                        tam = entrada.stat(follow_symlinks=False).st_size
                        if tam >= self._min_size:
                            por_tamano.setdefault(tam, []).append(entrada.path)
                except OSError:
                    continue
        return por_tamano

    def _hash_parcial(self, ruta: str) -> Optional[str]:
        try:
            with open(ruta, "rb") as f:
                inicio = f.read(self._CHUNK_HASH_PARCIAL)
                f.seek(0, os.SEEK_END)
                tam = f.tell()
                fin = b""
                if tam > self._CHUNK_HASH_PARCIAL:
                    f.seek(-self._CHUNK_HASH_PARCIAL, os.SEEK_END)
                    fin = f.read(self._CHUNK_HASH_PARCIAL)
            h = hashlib.blake2b()
            h.update(inicio)
            h.update(fin)
            return h.hexdigest()
        except OSError:
            return None

    def _hash_completo(self, ruta: str) -> Optional[str]:
        try:
            h = hashlib.sha256()
            with open(ruta, "rb") as f:
                while True:
                    if self._stop_requested:
                        return None
                    bloque = f.read(self._CHUNK_HASH_COMPLETO)
                    if not bloque:
                        break
                    h.update(bloque)
            return h.hexdigest()
        except OSError:
            return None

    def run(self):
        try:
            self.progreso.emit(f"Buscando archivos de {_tam_legible(self._min_size)} o más en {self._raiz}...")
            por_tamano = self._listar_archivos_grandes()
            candidatos = {tam: rutas for tam, rutas in por_tamano.items() if len(rutas) > 1}

            if self._stop_requested:
                self.progreso.emit("Detenido por el usuario")
                self.finished_ok.emit([])
                return

            total_candidatos = sum(len(rutas) for rutas in candidatos.values())
            self.progreso.emit(f"Comparando {total_candidatos} candidato(s) del mismo tamaño...")

            grupos_finales = []
            for tam, rutas in candidatos.items():
                if self._stop_requested:
                    break

                por_hash_parcial: Dict[str, List[str]] = {}
                for ruta in rutas:
                    hp = self._hash_parcial(ruta)
                    if hp:
                        por_hash_parcial.setdefault(hp, []).append(ruta)

                for rutas_parciales in por_hash_parcial.values():
                    if len(rutas_parciales) < 2 or self._stop_requested:
                        continue
                    por_hash_completo: Dict[str, List[str]] = {}
                    for ruta in rutas_parciales:
                        hc = self._hash_completo(ruta)
                        if hc:
                            por_hash_completo.setdefault(hc, []).append(ruta)
                    for rutas_finales in por_hash_completo.values():
                        if len(rutas_finales) > 1:
                            grupos_finales.append({"tamano": tam, "rutas": rutas_finales})

            grupos_finales.sort(key=lambda g: -g["tamano"])
            self.progreso.emit("Detenido por el usuario" if self._stop_requested else "Completado")
            self.finished_ok.emit(grupos_finales)
        except Exception as e:
            logger.exception(f"Error en DuplicateFilesFinderThread: {e}")
            self.finished_error.emit(str(e))


class LicenseInfoCollector(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(dict)
    finished_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(30, "Consultando estado de licencia (Windows y Office)...")
            # Get-CimInstance SoftwareLicensingProduct puede tardar >20s en
            # equipos reales (medido ~23.5s) — 20s lo hacía fallar por
            # timeout de forma consistente, no ocasional. Windows y Office
            # van en la MISMA consulta PS_GET_LICENSE_INFO (misma API,
            # SoftwareLicensingProduct) — ya no hay una segunda consulta
            # aparte escaneando el registro de Office en busca de cuentas:
            # esa vía (PS_GET_OFFICE_LICENSE_ACCOUNTS) daba falsos
            # positivos reales -- cualquier cuenta de Gmail añadida a
            # Outlook por IMAP salía como "licencia Office 365" (detectado
            # en vivo, 2026-07-30).
            data = run_powershell_json(PS_GET_LICENSE_INFO, timeout=90)
            self.progress.emit(100, "Completado")
            if data is None:
                # 5.4.0: antes un tiempo agotado se pintaba como «N/A» sin decir nada
                self.finished_error.emit("Windows no respondió a tiempo al consultar las licencias (90 s).")
                return
            self.finished_ok.emit(data)
        except Exception as e:
            logger.exception(f"Error en LicenseInfoCollector: {e}")
            self.finished_error.emit(str(e))

class PackageUpgradeCollector(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(dict)  # {'Disponible': bool, 'Paquetes': [...]}
    finished_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(30, "Consultando actualizaciones disponibles (winget)...")
            data = run_powershell_json(PS_GET_WINGET_UPGRADES, timeout=60)
            self.progress.emit(100, "Completado")
            self.finished_ok.emit(data or {"Disponible": False, "Paquetes": []})
        except Exception as e:
            logger.exception(f"Error en PackageUpgradeCollector: {e}")
            self.finished_error.emit(str(e))

class PackageBatchUpgradeCollector(QThread):
    """Aplica 'winget upgrade --id' a una lista de paquetes, uno a uno, EN
    SEGUNDO PLANO -- sustituye al subprocess.run() síncrono que había antes
    directamente en el hilo de la UI (congelaba la ventana hasta 5 minutos
    con un solo paquete; con un lote de varios habría sido mucho peor).
    Sigue con el siguiente paquete aunque uno falle o agote su timeout —
    un lote de 20 no debe abortarse entero por uno solo problemático — y
    emite el resultado detallado de cada uno al final para poder mostrar
    un resumen real (cuántos OK, cuántos no y por qué), no un mensaje
    genérico."""
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(list)  # [(nombre, id, ok, mensaje), ...]
    finished_error = pyqtSignal(str)

    def __init__(self, paquetes: list):
        """paquetes: [(nombre, id_paquete), ...]"""
        super().__init__()
        self.paquetes = paquetes
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def run(self):
        try:
            resultados = []
            total = len(self.paquetes) or 1
            for idx, (nombre, id_paquete) in enumerate(self.paquetes):
                if self._stop_requested:
                    break
                self.progress.emit(
                    int((idx / total) * 100), f"Actualizando {nombre} ({idx + 1}/{total})...")
                id_escapado = escape_powershell_string(id_paquete)
                try:
                    resultado = subprocess.run(
                        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command",
                         f"winget upgrade --id '{id_escapado}' --silent "
                         "--accept-package-agreements --accept-source-agreements"],
                        capture_output=True, text=True, timeout=420,
                        startupinfo=startupinfo_oculto(), creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    ok = resultado.returncode == 0
                    msg = "" if ok else (resultado.stderr or resultado.stdout or "").strip()
                except subprocess.TimeoutExpired:
                    ok, msg = False, "Tiempo agotado (más de 7 minutos)"
                except Exception as e:
                    ok, msg = False, str(e)
                resultados.append((nombre, id_paquete, ok, msg))

            self.progress.emit(100, "Completado")
            self.finished_ok.emit(resultados)
        except Exception as e:
            logger.exception(f"Error en PackageBatchUpgradeCollector: {e}")
            self.finished_error.emit(str(e))

class PackageBlockNotesDB:
    """Motivo opcional de bloqueo de un paquete (winget pin), persistido en
    multisys.db — winget solo guarda EL anclaje, no un 'por qué'; esto deja
    constancia para que un compañero vea la razón sin tener que preguntar.
    Mismo patrón que ProcessNotesDB/ServiceNotesDB de más arriba. El motivo
    se borra al desbloquear: no tiene sentido conservar la razón de un
    bloqueo que ya no existe."""

    def __init__(self):
        base_dir = msys_config.app_base_dir()
        self.path = os.path.join(base_dir, "multisys.db")
        self._init_schema()

    def _conn(self):
        return _sqlite_connect(self.path)

    def _init_schema(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS package_block_notes (
                    id_paquete TEXT PRIMARY KEY,
                    motivo TEXT NOT NULL DEFAULT '',
                    usuario TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                )
            """)

    def get_motivo(self, id_paquete: str) -> str:
        with self._conn() as conn:
            cur = conn.execute("SELECT motivo FROM package_block_notes WHERE id_paquete = ?", (id_paquete,))
            fila = cur.fetchone()
            return fila[0] if fila else ""

    def get_all(self) -> Dict[str, str]:
        with self._conn() as conn:
            cur = conn.execute("SELECT id_paquete, motivo FROM package_block_notes")
            return dict(cur.fetchall())

    def save_motivo(self, id_paquete: str, motivo: str, usuario: str = None) -> None:
        if usuario is None:
            usuario = os.environ.get("USERNAME", "")
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO package_block_notes (id_paquete, motivo, usuario, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(id_paquete) DO UPDATE SET motivo=excluded.motivo, usuario=excluded.usuario, updated_at=excluded.updated_at",
                (id_paquete, motivo, usuario, datetime.now().isoformat(timespec='seconds')),
            )

    def delete_motivo(self, id_paquete: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM package_block_notes WHERE id_paquete = ?", (id_paquete,))

class NavProfilesDB:
    """Perfiles de navegación (visibilidad + orden de las secciones del
    menú lateral), persistidos en multisys.db — ver ConfiguracionMixin
    (msys/ui/config_dialog.py), "⚙️ Configuración y perfiles". Cada perfil
    es solo un snapshot con nombre de la MISMA estructura {"tabs": [{name,
    visible}, ...]} que ya usa CONFIG_FILE (msys/config.py) — un perfil no
    es más que "guarda esto con un nombre para poder volver a cargarlo".

    'MASys' es el perfil predeterminado/protegido: no se puede
    sobrescribir ni borrar, es el ancla fija de '🔄 Restablecer todo'."""

    NOMBRE_PREDETERMINADO = "MASys"
    NOMBRE_ANTERIOR = "MultiSYS"  # nombre del predeterminado antes de 4.1.7

    def __init__(self):
        base_dir = msys_config.app_base_dir()
        self.path = os.path.join(base_dir, "multisys.db")
        self._init_schema()

    def _conn(self):
        return _sqlite_connect(self.path)

    def _init_schema(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS nav_profiles (
                    nombre TEXT PRIMARY KEY,
                    config_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            # El predeterminado ya guardado conserva su configuración con el nombre nuevo
            if not conn.execute("SELECT 1 FROM nav_profiles WHERE nombre = ?", (self.NOMBRE_PREDETERMINADO,)).fetchone():
                conn.execute("UPDATE nav_profiles SET nombre = ? WHERE nombre = ?",
                             (self.NOMBRE_PREDETERMINADO, self.NOMBRE_ANTERIOR))

    def guardar(self, nombre: str, config: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO nav_profiles (nombre, config_json, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(nombre) DO UPDATE SET config_json=excluded.config_json, updated_at=excluded.updated_at",
                (nombre, json.dumps(config, ensure_ascii=False), datetime.now().isoformat(timespec='seconds')),
            )

    def cargar(self, nombre: str) -> Optional[dict]:
        with self._conn() as conn:
            cur = conn.execute("SELECT config_json FROM nav_profiles WHERE nombre = ?", (nombre,))
            fila = cur.fetchone()
            return json.loads(fila[0]) if fila else None

    def listar(self) -> List[str]:
        """'MASys' siempre primero, el resto alfabético — no por fecha
        de modificación, para que la lista no salte de orden mientras el
        técnico está trabajando con ella."""
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT nombre FROM nav_profiles ORDER BY (nombre = ?) DESC, nombre COLLATE NOCASE",
                (self.NOMBRE_PREDETERMINADO,),
            )
            return [r[0] for r in cur.fetchall()]

    def borrar(self, nombre: str) -> bool:
        if nombre == self.NOMBRE_PREDETERMINADO:
            return False
        with self._conn() as conn:
            conn.execute("DELETE FROM nav_profiles WHERE nombre = ?", (nombre,))
        return True

    def existe(self, nombre: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute("SELECT 1 FROM nav_profiles WHERE nombre = ?", (nombre,))
            return cur.fetchone() is not None

    def asegurar_perfil_predeterminado(self, config_actual: dict) -> None:
        """La primera vez que se usa el sistema de perfiles en este
        equipo, 'MASys' se crea a partir de la configuración YA ACTIVA
        en ese momento -- "el actual, tal como está" -- no del
        DEFAULT_TAB_CONFIG hardcodeado de msys/config.py, que podría no
        coincidir con una personalización que el técnico ya tuviera hecha
        antes de que existiera este sistema de perfiles."""
        if not self.existe(self.NOMBRE_PREDETERMINADO):
            self.guardar(self.NOMBRE_PREDETERMINADO, config_actual)

class NetworkDeviceCollector(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(list)
    finished_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def run(self):
        try:
            self.progress.emit(30, "Consultando dispositivos conocidos en la red local (tabla ARP)...")
            data = run_powershell_json(PS_GET_LOCAL_NETWORK_DEVICES, timeout=30)
            if data is None:
                self.finished_error.emit(
                    "Tiempo agotado o error al consultar la tabla de vecinos ARP/IPv4.")
                return
            if not data:
                self.finished_ok.emit([])
                return
            if isinstance(data, dict):
                data = [data]
            # Fabricante: cálculo LOCAL puro (tabla OUI reducida en memoria,
            # sin red ni PowerShell) — se hace aquí, no en la UI, para que
            # ya llegue relleno en finished_ok y no haga falta ningún
            # cálculo adicional por fila más adelante.
            rows = [NetworkDeviceRow(
                ip=str(d.get("IP", "")),
                mac=str(d.get("MAC", "")),
                estado=str(d.get("Estado", "")),
                interfaz=str(d.get("Interfaz", "")),
                fabricante=identificar_fabricante_mac(str(d.get("MAC", ""))),
            ) for d in data]
            self.progress.emit(100, "Completado")
            self.finished_ok.emit(rows)
        except Exception as e:
            logger.exception(f"Error en NetworkDeviceCollector: {e}")
            self.finished_error.emit(str(e))


def _ejecutar_streaming_con_progreso(argv, on_texto, on_pct, stop_check, timeout_segundos,
                                      auto_confirmar_stdin=False):
    """Ejecuta argv capturando su salida carácter a carácter — necesario
    porque herramientas como SFC/DISM/CHKDSK actualizan la MISMA línea con
    retorno de carro ('\\r'), no con saltos de línea, así que leer con
    readline() perdería el progreso intermedio. Devuelve (éxito, log
    completo). on_texto(linea)/on_pct(pct) se llaman por cada línea
    completa que traiga un porcentaje reconocible (siempre numérico,
    independiente del idioma de Windows — el texto localizado de éxito/
    fallo no se intenta reconocer, eso lo decide el código de salida).

    auto_confirmar_stdin=True escribe 'Y\\n' a stdin nada más arrancar:
    CHKDSK con /f o /r sobre un volumen bloqueado (típicamente el de
    sistema) pregunta interactivamente si programar la comprobación para
    el próximo reinicio — sin esto, el proceso se queda esperando una
    entrada que nunca llega y cuelga hasta el timeout."""
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE

    proceso = subprocess.Popen(
        argv, stdin=subprocess.PIPE if auto_confirmar_stdin else None,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding='oem', errors='replace',
        creationflags=subprocess.CREATE_NO_WINDOW, startupinfo=startupinfo,
    )
    if auto_confirmar_stdin:
        try:
            proceso.stdin.write("Y\n")
            proceso.stdin.flush()
            proceso.stdin.close()
        except OSError:
            pass

    lineas = []
    parcial = ""
    inicio = time.time()
    try:
        while True:
            if stop_check():
                proceso.terminate()
                lineas.append("[Detenido por el usuario]")
                break
            if time.time() - inicio > timeout_segundos:
                proceso.terminate()
                lineas.append(f"[Tiempo límite de {timeout_segundos // 60} minutos agotado]")
                break

            ch = proceso.stdout.read(1)
            if ch == '' and proceso.poll() is not None:
                break
            if ch in ('\r', '\n'):
                if parcial.strip():
                    lineas.append(parcial)
                    on_texto(parcial)
                    m = re.search(r'(\d{1,3}(?:[.,]\d+)?)\s*%', parcial)
                    if m:
                        try:
                            pct = int(float(m.group(1).replace(',', '.')))
                            on_pct(min(100, max(0, pct)))
                        except ValueError:
                            pass
                parcial = ""
            else:
                parcial += ch
    finally:
        try:
            proceso.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proceso.kill()

    return proceso.returncode == 0, "\n".join(lineas)


class SFCDISMRepairThread(QThread):
    """Repara archivos de sistema: primero DISM /RestoreHealth (repara el
    almacén de componentes WinSxS del que depende SFC — orden recomendado
    por Microsoft) y despues sfc /scannow."""

    etapa_cambiada = pyqtSignal(str)
    progreso_porcentaje = pyqtSignal(str, int)
    progreso_texto = pyqtSignal(str)
    finished_ok = pyqtSignal(bool, str)  # (exito_general, log_completo)

    # Límite defensivo: si sfc/dism se quedaran colgados en un equipo con
    # daños graves, no dejar el hilo corriendo indefinidamente.
    TIMEOUT_POR_ETAPA_SEGUNDOS = 45 * 60

    def __init__(self):
        super().__init__()
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def _ejecutar_streaming(self, argv, etapa):
        return _ejecutar_streaming_con_progreso(
            argv,
            on_texto=self.progreso_texto.emit,
            on_pct=lambda pct: self.progreso_porcentaje.emit(etapa, pct),
            stop_check=lambda: self._stop_requested,
            timeout_segundos=self.TIMEOUT_POR_ETAPA_SEGUNDOS,
        )

    def run(self):
        try:
            self.etapa_cambiada.emit("Fase 1/2 — DISM: reparando la imagen de Windows...")
            ok_dism, log_dism = self._ejecutar_streaming(
                ["DISM", "/Online", "/Cleanup-Image", "/RestoreHealth"], "DISM"
            )
            if self._stop_requested:
                self.finished_ok.emit(False, f"=== DISM ===\n{log_dism}\n\n[Detenido antes de ejecutar SFC]")
                return

            self.etapa_cambiada.emit("Fase 2/2 — SFC: verificando archivos de sistema...")
            ok_sfc, log_sfc = self._ejecutar_streaming(["sfc", "/scannow"], "SFC")

            log_completo = f"=== DISM /RestoreHealth ===\n{log_dism}\n\n=== SFC /scannow ===\n{log_sfc}"
            self.finished_ok.emit(ok_dism and ok_sfc, log_completo)
        except Exception as e:
            logger.exception(f"Error en SFCDISMRepairThread: {e}")
            self.finished_ok.emit(False, f"Error inesperado: {e}")


class CHKDSKRepairThread(QThread):
    """Ejecuta CHKDSK sobre una unidad. Sin /f (por defecto) es de solo
    lectura y puede ejecutarse con el volumen en uso, sin bloquearlo. Con
    /f (o /r, que lo implica) CHKDSK necesita bloqueo exclusivo — si el
    volumen está en uso (típicamente el del sistema), Windows pregunta si
    programar la comprobación para el próximo reinicio en vez de
    ejecutarla ahora; auto_confirmar_stdin en el helper compartido
    responde 'Y' automáticamente a esa pregunta (ya se explicó y se
    confirmó en el diálogo previo de MASys, no hace falta preguntarlo
    dos veces)."""

    progreso_texto = pyqtSignal(str)
    progreso_porcentaje = pyqtSignal(int)
    finished_ok = pyqtSignal(bool, str, bool)  # (exito, log_completo, programado_para_reinicio)

    # CHKDSK /r en discos grandes puede tardar horas de verdad — límite
    # defensivo generoso, no un tiempo esperado.
    TIMEOUT_SEGUNDOS = 4 * 60 * 60

    def __init__(self, letra: str, reparar: bool, buscar_sectores: bool):
        super().__init__()
        self._letra = letra.rstrip(":")
        self._reparar = reparar
        self._buscar_sectores = buscar_sectores
        self._stop_requested = False

    def request_stop(self):
        self._stop_requested = True

    def run(self):
        argv = ["chkdsk", f"{self._letra}:"]
        if self._buscar_sectores:
            argv.append("/r")  # /r implica /f
        elif self._reparar:
            argv.append("/f")
        necesita_bloqueo = self._reparar or self._buscar_sectores

        try:
            exito, log = _ejecutar_streaming_con_progreso(
                argv,
                on_texto=self.progreso_texto.emit,
                on_pct=self.progreso_porcentaje.emit,
                stop_check=lambda: self._stop_requested,
                timeout_segundos=self.TIMEOUT_SEGUNDOS,
                auto_confirmar_stdin=necesita_bloqueo,
            )
            # Si se pidió /f o /r y el proceso termina sin haber impreso
            # NUNCA un % de progreso, es que no llegó a analizar nada —
            # se quedó en la pregunta de programar para el reinicio (el
            # texto de esa pregunta está localizado, así que no se busca
            # por frase, igual que el resto de esta herramienta).
            programado = necesita_bloqueo and not re.search(r'\d{1,3}\s*%', log)
            self.finished_ok.emit(exito, log, programado)
        except Exception as e:
            logger.exception(f"Error en CHKDSKRepairThread: {e}")
            self.finished_ok.emit(False, f"Error inesperado: {e}", False)
