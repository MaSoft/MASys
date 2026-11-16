# -*- coding: utf-8 -*-
"""Hilos (QThread) de los generadores de la pestaña 📀 Copias de Seguridad
(msys/ui/backups.py + msys/ui/backup_generator_dialog.py). Mismo contrato
de señales que el resto de hilos de reparación del proyecto — progress
(int, str) / finished_ok(str destino) / finished_error(str) — ver
DriverBackupThread en collectors.py, de donde se copió el patrón.

Uno por categoría: cada hilo recibe (destino, claves_seleccionadas) y hace
SOLO lo marcado por el usuario en el checklist del diálogo, nunca todo
incondicionalmente."""

import os
import json
import shutil
import subprocess
from typing import List, Optional, Tuple

import win32api

from PyQt5.QtCore import QThread, pyqtSignal

try:
    import psutil
except ImportError:
    psutil = None

from msys.browser_data import enumerar_perfiles, PerfilNavegador
from msys.core.utils import run_powershell_json_params, get_user_content_folders
from msys.ps_scripts import (
    PS_BACKUP_TAREAS_PROGRAMADAS, PS_BACKUP_SERVICIOS, PS_BACKUP_IMPRESORAS,
    PS_BACKUP_CERTIFICADOS,
)
from msys.collectors import OutlookConfigBackupThread, NavProfilesDB
from msys import config as msys_config


def _startupinfo_oculto():
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return startupinfo


# ── Registro de Windows ──────────────────────────────────────────────────

COMPONENTES_REGISTRO = [
    ("hklm_software", "HKLM\\SOFTWARE — configuración de programas instalados"),
    ("hklm_system", "HKLM\\SYSTEM — configuración del sistema, servicios, controladores"),
    ("hkcu", "HKCU — configuración del usuario actual"),
]


class RegistryBackupThread(QThread):
    """reg export por rama, un fichero .reg independiente por cada una —
    nunca todo el registro de golpe (exportar HKLM completo incluye ramas
    binarias enormes tipo BCD/hardware que no aportan nada a un backup de
    configuración y multiplican el tamaño y el tiempo sin necesidad)."""
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(str)
    finished_error = pyqtSignal(str)

    _RAMAS = {
        "hklm_software": (r"HKLM\SOFTWARE", "HKLM_SOFTWARE.reg"),
        "hklm_system": (r"HKLM\SYSTEM", "HKLM_SYSTEM.reg"),
        "hkcu": (r"HKCU", "HKCU.reg"),
    }

    def __init__(self, destino: str, claves: list):
        super().__init__()
        self.destino = destino
        self.claves = claves

    def run(self):
        try:
            total = len(self.claves) or 1
            avisos = []
            for idx, clave in enumerate(self.claves):
                rama, nombre_archivo = self._RAMAS.get(clave, (None, None))
                if rama is None:
                    continue
                self.progress.emit(int((idx / total) * 90), f"Exportando {rama}...")
                ruta_salida = os.path.join(self.destino, nombre_archivo)
                resultado = subprocess.run(
                    ["reg", "export", rama, ruta_salida, "/y"],
                    capture_output=True, text=True, timeout=180,
                    startupinfo=_startupinfo_oculto(), creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if resultado.returncode != 0:
                    avisos.append(f"{rama}: {(resultado.stderr or resultado.stdout or '').strip()}")

            if avisos and len(avisos) == total:
                self.finished_error.emit("; ".join(avisos))
                return
            if avisos:
                self._escribir_avisos(avisos)
            self.progress.emit(100, "Completado")
            self.finished_ok.emit(self.destino)
        except subprocess.TimeoutExpired:
            self.finished_error.emit("Tiempo agotado exportando el registro.")
        except Exception as e:
            self.finished_error.emit(str(e))

    def _escribir_avisos(self, avisos):
        try:
            with open(os.path.join(self.destino, "AVISOS.txt"), "w", encoding="utf-8") as f:
                f.write("Algunos elementos no se pudieron copiar:\n\n" + "\n".join(avisos))
        except OSError:
            pass


# ── Credenciales y accesos ───────────────────────────────────────────────

COMPONENTES_CREDENCIALES = [
    ("credential_manager", "Windows Credential Manager (bóveda + claves DPAPI para poder descifrarla)"),
    ("wifi", "Perfiles WiFi guardados, con la contraseña en claro"),
    ("ssh", "Claves SSH del usuario (carpeta .ssh)"),
    ("git", "Credenciales de Git guardadas en texto plano (.git-credentials)"),
]


class CredentialsBackupThread(QThread):
    """El Credential Manager de Windows NO se puede 'exportar' con sus
    secretos en claro por API — los ficheros de la bóveda (%APPDATA%/
    %LOCALAPPDATA%\\Microsoft\\Credentials) están cifrados con DPAPI y solo
    se descifran de vuelta con las claves maestras de ESE MISMO usuario en
    %APPDATA%\\Microsoft\\Protect — por eso se copian los tres juntos, sin
    los tres el backup de credenciales no sirve para restaurar nada."""
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(str)
    finished_error = pyqtSignal(str)

    def __init__(self, destino: str, claves: list):
        super().__init__()
        self.destino = destino
        self.claves = claves

    def run(self):
        try:
            total = len(self.claves) or 1
            avisos = []
            for idx, clave in enumerate(self.claves):
                pct = int((idx / total) * 90)
                if clave == "credential_manager":
                    self.progress.emit(pct, "Copiando Credential Manager (bóveda + claves DPAPI)...")
                    self._copiar_credential_manager(avisos)
                elif clave == "wifi":
                    self.progress.emit(pct, "Exportando perfiles WiFi (con clave en claro)...")
                    self._exportar_wifi(avisos)
                elif clave == "ssh":
                    self.progress.emit(pct, "Copiando claves SSH...")
                    self._copiar_carpeta_usuario(".ssh", "SSH", avisos)
                elif clave == "git":
                    self.progress.emit(pct, "Copiando credenciales de Git...")
                    self._copiar_archivo_usuario(".git-credentials", avisos)

            if avisos:
                self._escribir_avisos(avisos)
            self.progress.emit(100, "Completado")
            self.finished_ok.emit(self.destino)
        except Exception as e:
            self.finished_error.emit(str(e))

    def _copiar_credential_manager(self, avisos):
        origenes = [
            (os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Credentials"), "Credentials_Roaming"),
            (os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Credentials"), "Credentials_Local"),
            (os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Protect"), "Protect_DPAPI_Keys"),
        ]
        destino_cm = os.path.join(self.destino, "CredentialManager")
        alguno_copiado = False
        for origen, nombre in origenes:
            if os.path.isdir(origen):
                try:
                    shutil.copytree(origen, os.path.join(destino_cm, nombre), dirs_exist_ok=True)
                    alguno_copiado = True
                except OSError as e:
                    avisos.append(f"Credential Manager ({nombre}): {e}")
            else:
                avisos.append(f"Credential Manager: no se encontró {origen}")
        if not alguno_copiado:
            avisos.append("Credential Manager: no se pudo copiar ninguna de las 3 carpetas necesarias")

    def _exportar_wifi(self, avisos):
        destino_wifi = os.path.join(self.destino, "WiFi")
        os.makedirs(destino_wifi, exist_ok=True)
        resultado = subprocess.run(
            ["netsh", "wlan", "export", "profile", f"folder={destino_wifi}", "key=clear"],
            capture_output=True, text=True, timeout=60,
            startupinfo=_startupinfo_oculto(), creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if resultado.returncode != 0:
            avisos.append(f"WiFi: {(resultado.stderr or resultado.stdout or '').strip()}")

    def _copiar_carpeta_usuario(self, subcarpeta, nombre_destino, avisos):
        origen = os.path.join(os.environ.get("USERPROFILE", ""), subcarpeta)
        if os.path.isdir(origen):
            try:
                shutil.copytree(origen, os.path.join(self.destino, nombre_destino), dirs_exist_ok=True)
            except OSError as e:
                avisos.append(f"{nombre_destino}: {e}")
        else:
            avisos.append(f"{nombre_destino}: no encontrado ({origen})")

    def _copiar_archivo_usuario(self, nombre_archivo, avisos):
        origen = os.path.join(os.environ.get("USERPROFILE", ""), nombre_archivo)
        if os.path.isfile(origen):
            try:
                shutil.copy2(origen, os.path.join(self.destino, nombre_archivo))
            except OSError as e:
                avisos.append(f"{nombre_archivo}: {e}")
        else:
            avisos.append(f"{nombre_archivo}: no encontrado")

    def _escribir_avisos(self, avisos):
        try:
            with open(os.path.join(self.destino, "AVISOS.txt"), "w", encoding="utf-8") as f:
                f.write("Algunos elementos no se pudieron copiar:\n\n" + "\n".join(avisos))
        except OSError:
            pass


# ── Navegadores ───────────────────────────────────────────────────────────

_ARCHIVOS_CHROMIUM = [
    "Bookmarks", "Login Data", "History", "Cookies", os.path.join("Network", "Cookies"),
    "Preferences", "Secure Preferences", "Current Session", "Current Tabs",
    "Last Session", "Last Tabs",
]
_ARCHIVOS_FIREFOX = [
    "places.sqlite", "logins.json", "key4.db", "cookies.sqlite",
    "sessionstore.jsonlz4", "extensions.json",
]
_PROCESOS_NAVEGADOR = {"Chrome": "chrome.exe", "Edge": "msedge.exe", "Firefox": "firefox.exe"}


def navegador_en_ejecucion(navegador: str) -> bool:
    proceso = _PROCESOS_NAVEGADOR.get(navegador)
    if not proceso or psutil is None:
        return False
    try:
        return any((p.info.get('name') or '').lower() == proceso.lower()
                   for p in psutil.process_iter(['name']))
    except Exception:
        return False


def componentes_navegadores():
    """Un componente por PERFIL de navegador realmente detectado en este
    equipo (no una lista fija) — reutiliza enumerar_perfiles(), la misma
    función que ya usa el detector de Soporte de Navegadores."""
    perfiles = enumerar_perfiles()
    return perfiles, [
        (p.ruta, f"{p.navegador} — {p.nombre_perfil}" +
         (" (en ejecución ahora mismo)" if navegador_en_ejecucion(p.navegador) else ""))
        for p in perfiles
    ]


class BrowserBackupThread(QThread):
    """Copia, por cada perfil de navegador SELECCIONADO, los ficheros
    conocidos de marcadores/contraseñas/historial/cookies/sesiones —
    Chromium (Chrome/Edge) y Firefox tienen nombres de fichero distintos,
    de ahí las dos listas separadas. Si el navegador está en ejecución
    ahora mismo, se avisa en el informe (copia best-effort: en Windows
    normalmente se puede copiar un fichero abierto para lectura
    compartida, pero puede quedar inconsistente a media escritura)."""
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(str)
    finished_error = pyqtSignal(str)

    def __init__(self, destino: str, perfiles: list):
        super().__init__()
        self.destino = destino
        self.perfiles = perfiles  # List[PerfilNavegador]

    def run(self):
        try:
            avisos = []
            total = len(self.perfiles) or 1
            for idx, perfil in enumerate(self.perfiles):
                pct = int((idx / total) * 90)
                self.progress.emit(pct, f"Copiando {perfil.navegador} — {perfil.nombre_perfil}...")
                if navegador_en_ejecucion(perfil.navegador):
                    avisos.append(
                        f"{perfil.navegador} {perfil.nombre_perfil}: el navegador estaba en ejecución "
                        "durante la copia — si algún archivo aparece incompleto, ciérralo y repite.")
                self._copiar_perfil(perfil, avisos)

            if avisos:
                self._escribir_avisos(avisos)
            self.progress.emit(100, "Completado")
            self.finished_ok.emit(self.destino)
        except Exception as e:
            self.finished_error.emit(str(e))

    def _copiar_perfil(self, perfil: PerfilNavegador, avisos):
        archivos = _ARCHIVOS_FIREFOX if perfil.navegador == "Firefox" else _ARCHIVOS_CHROMIUM
        nombre_carpeta = f"{perfil.navegador}_{perfil.nombre_perfil}".replace(" ", "_").replace(os.sep, "_")
        destino_perfil = os.path.join(self.destino, nombre_carpeta)
        os.makedirs(destino_perfil, exist_ok=True)
        copiados = 0
        for nombre_rel in archivos:
            origen = os.path.join(perfil.ruta, nombre_rel)
            if not os.path.isfile(origen):
                continue
            try:
                nombre_plano = nombre_rel.replace(os.sep, "_")
                shutil.copy2(origen, os.path.join(destino_perfil, nombre_plano))
                copiados += 1
            except OSError as e:
                avisos.append(f"{perfil.navegador} {perfil.nombre_perfil} - {nombre_rel}: {e}")
        if copiados == 0:
            avisos.append(
                f"{perfil.navegador} {perfil.nombre_perfil}: no se encontró ningún archivo conocido para copiar")

    def _escribir_avisos(self, avisos):
        try:
            with open(os.path.join(self.destino, "AVISOS.txt"), "w", encoding="utf-8") as f:
                f.write("Avisos de esta copia:\n\n" + "\n".join(avisos))
        except OSError:
            pass


# ── Configuración del sistema ────────────────────────────────────────────

COMPONENTES_SISTEMA = [
    ("firewall", "Reglas y perfiles de Firewall (.wfw)"),
    ("tareas", "Tareas programadas (una .xml por tarea)"),
    ("servicios", "Servicios — nombre, inicio, cuenta, ruta (documental, no un backup restaurable con un clic)"),
    ("variables_entorno", "Variables de entorno (Usuario y Sistema)"),
    ("red", "Configuración de red — adaptadores, IP, DNS"),
    ("impresoras", "Impresoras — nombre, controlador, puerto (documental)"),
    ("directiva_seguridad", "Directiva de seguridad local"),
    ("certificados", "Certificados personales (solo la parte pública, sin clave privada)"),
]


class SystemConfigBackupThread(QThread):
    """Un componente = una llamada nativa (reg/netsh/secedit) o una
    consulta PowerShell puntual — 'Servicios' e 'Impresoras' son
    fotografías documentales (nombre/config), no recrean nada por sí
    solas; el resto sí se puede reimportar/reaplicar directamente."""
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(str)
    finished_error = pyqtSignal(str)

    def __init__(self, destino: str, claves: list):
        super().__init__()
        self.destino = destino
        self.claves = claves

    def run(self):
        try:
            total = len(self.claves) or 1
            avisos = []
            for idx, clave in enumerate(self.claves):
                pct = int((idx / total) * 90)
                if clave == "firewall":
                    self.progress.emit(pct, "Exportando reglas de Firewall...")
                    self._exportar_firewall(avisos)
                elif clave == "tareas":
                    self.progress.emit(pct, "Exportando tareas programadas (puede tardar)...")
                    self._exportar_tareas(avisos)
                elif clave == "servicios":
                    self.progress.emit(pct, "Exportando configuración de servicios...")
                    self._ejecutar_ps_backup(PS_BACKUP_SERVICIOS, avisos, "Servicios")
                elif clave == "variables_entorno":
                    self.progress.emit(pct, "Exportando variables de entorno...")
                    self._exportar_variables_entorno(avisos)
                elif clave == "red":
                    self.progress.emit(pct, "Exportando configuración de red...")
                    self._exportar_red(avisos)
                elif clave == "impresoras":
                    self.progress.emit(pct, "Exportando impresoras...")
                    self._ejecutar_ps_backup(PS_BACKUP_IMPRESORAS, avisos, "Impresoras")
                elif clave == "directiva_seguridad":
                    self.progress.emit(pct, "Exportando directiva de seguridad local...")
                    self._exportar_directiva_seguridad(avisos)
                elif clave == "certificados":
                    self.progress.emit(pct, "Exportando certificados personales...")
                    self._ejecutar_ps_backup(PS_BACKUP_CERTIFICADOS, avisos, "Certificados")

            if avisos:
                self._escribir_avisos(avisos)
            self.progress.emit(100, "Completado")
            self.finished_ok.emit(self.destino)
        except Exception as e:
            self.finished_error.emit(str(e))

    def _ejecutar_ps_backup(self, plantilla, avisos, nombre):
        resultado = run_powershell_json_params(plantilla, timeout=180, destino=self.destino)
        if not isinstance(resultado, dict):
            avisos.append(f"{nombre}: no se pudo completar la exportación")
        elif "Ok" in resultado and not resultado["Ok"]:
            avisos.append(f"{nombre}: {resultado.get('Error', 'sin detalles')}")

    def _exportar_firewall(self, avisos):
        ruta = os.path.join(self.destino, "Firewall.wfw")
        resultado = subprocess.run(
            ["netsh", "advfirewall", "export", ruta],
            capture_output=True, text=True, timeout=60,
            startupinfo=_startupinfo_oculto(), creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if resultado.returncode != 0:
            avisos.append(f"Firewall: {(resultado.stderr or resultado.stdout or '').strip()}")

    def _exportar_tareas(self, avisos):
        resultado = run_powershell_json_params(PS_BACKUP_TAREAS_PROGRAMADAS, timeout=300, destino=self.destino)
        if not isinstance(resultado, dict):
            avisos.append("Tareas programadas: no se pudo completar la exportación")
        elif resultado.get("Fallidas"):
            avisos.append(f"Tareas programadas: {resultado['Fallidas']} de ellas no se pudieron exportar")

    def _exportar_variables_entorno(self, avisos):
        ramas = (
            (r"HKCU\Environment", "Variables_Usuario.reg"),
            (r"HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment", "Variables_Sistema.reg"),
        )
        for rama, archivo in ramas:
            ruta_salida = os.path.join(self.destino, archivo)
            resultado = subprocess.run(
                ["reg", "export", rama, ruta_salida, "/y"],
                capture_output=True, text=True, timeout=30,
                startupinfo=_startupinfo_oculto(), creationflags=subprocess.CREATE_NO_WINDOW,
            )
            if resultado.returncode != 0:
                avisos.append(f"Variables de entorno ({rama}): {(resultado.stderr or resultado.stdout or '').strip()}")

    def _exportar_red(self, avisos):
        ruta = os.path.join(self.destino, "Red.txt")
        try:
            resultado = subprocess.run(
                ["netsh", "dump"], capture_output=True, text=True, timeout=60,
                startupinfo=_startupinfo_oculto(), creationflags=subprocess.CREATE_NO_WINDOW,
            )
            with open(ruta, "w", encoding="utf-8") as f:
                f.write(resultado.stdout or "")
            if resultado.returncode != 0:
                avisos.append(f"Red: {(resultado.stderr or '').strip()}")
        except OSError as e:
            avisos.append(f"Red: {e}")

    def _exportar_directiva_seguridad(self, avisos):
        ruta = os.path.join(self.destino, "DirectivaSeguridadLocal.inf")
        resultado = subprocess.run(
            ["secedit", "/export", "/cfg", ruta],
            capture_output=True, text=True, timeout=60,
            startupinfo=_startupinfo_oculto(), creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if resultado.returncode != 0:
            avisos.append(f"Directiva de seguridad local: {(resultado.stdout or resultado.stderr or '').strip()}")

    def _escribir_avisos(self, avisos):
        try:
            with open(os.path.join(self.destino, "AVISOS.txt"), "w", encoding="utf-8") as f:
                f.write("Algunos elementos no se pudieron copiar:\n\n" + "\n".join(avisos))
        except OSError:
            pass


# ── Correo ────────────────────────────────────────────────────────────────

COMPONENTES_CORREO = [
    ("config_outlook", "Configuración de cuentas Outlook (perfiles, cuentas, reglas) y firmas"),
]


class CorreoBackupThread(QThread):
    """Reutiliza OutlookConfigBackupThread (msys/collectors.py) tal cual —
    llamada directa a su run() dentro de ESTE mismo hilo (no un QThread
    nuevo: sus señales, conectadas con AutoConnection entre objetos del
    mismo hilo, se resuelven en DirectConnection y se entregan al
    instante), para no duplicar la lógica ya probada de exportar la rama
    de registro de Outlook + comprimir firmas.

    PST/OST masivo se deja FUERA a propósito en esta primera versión:
    exportar un buzón completo puede tardar hasta 30 min POR CUENTA
    (OutlookPSTExportThread) y automatizarlo sin supervisión dentro de un
    backup por lotes es arriesgado — usa '📤 Exportar esta cuenta a PST'
    en Cuentas de Correo mientras tanto. Las reglas de Outlook (no
    confundir con las de firewall) tampoco se automatizan: Outlook no
    expone una exportación de reglas fiable entre versiones por script,
    solo 'Administrar reglas y alertas > Exportar reglas' (.rwz) manual."""
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(str)
    finished_error = pyqtSignal(str)

    def __init__(self, destino: str, claves: list):
        super().__init__()
        self.destino = destino
        self.claves = claves

    def run(self):
        try:
            avisos = []
            if "config_outlook" in self.claves:
                self.progress.emit(20, "Exportando configuración y firmas de Outlook...")
                self._exportar_config_outlook(avisos)

            if avisos:
                self._escribir_avisos(avisos)
            self.progress.emit(100, "Completado")
            self.finished_ok.emit(self.destino)
        except Exception as e:
            self.finished_error.emit(str(e))

    def _exportar_config_outlook(self, avisos):
        destino_outlook = os.path.join(self.destino, "Outlook")
        os.makedirs(destino_outlook, exist_ok=True)
        sub = OutlookConfigBackupThread(destino_outlook)
        resultado = {}
        sub.finished_ok.connect(lambda archivos: resultado.setdefault("ok", archivos))
        sub.finished_error.connect(lambda msg: resultado.setdefault("error", msg))
        sub.run()
        if "error" in resultado:
            avisos.append(f"Configuración de Outlook: {resultado['error']}")

    def _escribir_avisos(self, avisos):
        try:
            with open(os.path.join(self.destino, "AVISOS.txt"), "w", encoding="utf-8") as f:
                f.write("Avisos de esta copia:\n\n" + "\n".join(avisos))
        except OSError:
            pass


# ── Configuración de MASys ────────────────────────────────────────────

COMPONENTES_MASYS = [
    ("config_app", "Configuración de la aplicación (buscacompartidos_config.json + ajustes)"),
    ("base_datos", "Base de datos local (multisys.db — auditoría, listas aceptadas...)"),
    ("catalogo_scripts", "Catálogo de scripts de 🚀 Lanzar Scripts (metadatos, no los .ps1 en sí)"),
    ("perfiles_navegacion", "Perfiles de navegación (⚙️ Configuración y perfiles — visibilidad y orden de secciones)"),
]

# Los ajustes de la app se guardan con QSettings("Masoft", "MASys")
# — en Windows eso es NativeFormat = Registro, no un fichero — por eso
# "config_app" exporta esta rama con reg export en vez de leer claves una a
# una: captura TODO lo que la app haya guardado ahí (lista negra de
# software, vínculos winget, avisos validados...) de una sola vez, sin
# tener que mantener aquí una lista de qué claves existen.
_RAMA_REGISTRO_MASYS = r"HKCU\Software\Masoft\MASys"


class MASysConfigBackupThread(QThread):
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(str)
    finished_error = pyqtSignal(str)

    def __init__(self, destino: str, claves: list):
        super().__init__()
        self.destino = destino
        self.claves = claves

    def run(self):
        try:
            total = len(self.claves) or 1
            avisos = []
            for idx, clave in enumerate(self.claves):
                pct = int((idx / total) * 90)
                if clave == "config_app":
                    self.progress.emit(pct, "Exportando configuración y ajustes de la aplicación...")
                    self._exportar_config_app(avisos)
                elif clave == "base_datos":
                    self.progress.emit(pct, "Copiando base de datos local...")
                    ruta_db = os.path.join(msys_config.app_base_dir(), "multisys.db")
                    self._copiar_archivo_base(ruta_db, "multisys.db", avisos)
                elif clave == "catalogo_scripts":
                    self.progress.emit(pct, "Copiando catálogo de scripts...")
                    self._copiar_catalogo_scripts(avisos)
                elif clave == "perfiles_navegacion":
                    self.progress.emit(pct, "Exportando perfiles de navegación...")
                    self._exportar_perfiles_navegacion(avisos)

            if avisos:
                self._escribir_avisos(avisos)
            self.progress.emit(100, "Completado")
            self.finished_ok.emit(self.destino)
        except Exception as e:
            self.finished_error.emit(str(e))

    def _exportar_perfiles_navegacion(self, avisos):
        """Exporta TODOS los perfiles guardados (incluido 'MASys') a un
        JSON portable — independiente de multisys.db, para poder
        restaurar solo los perfiles sin tocar la base de datos completa
        (esa restauración queda excluida por el riesgo de sobrescribir la
        BD que el propio proceso tiene abierta, ver
        MASysConfigRestoreThread)."""
        try:
            db = NavProfilesDB()
            perfiles = {nombre: db.cargar(nombre) for nombre in db.listar()}
            with open(os.path.join(self.destino, "PerfilesNavegacion.json"), "w", encoding="utf-8") as f:
                json.dump(perfiles, f, indent=2, ensure_ascii=False)
        except Exception as e:
            avisos.append(f"Perfiles de navegación: {e}")

    def _exportar_config_app(self, avisos):
        ruta_json = os.path.join(msys_config.app_base_dir(), "buscacompartidos_config.json")
        self._copiar_archivo_base(ruta_json, "buscacompartidos_config.json", avisos)

        ruta_reg = os.path.join(self.destino, "Ajustes_MASys.reg")
        resultado = subprocess.run(
            ["reg", "export", _RAMA_REGISTRO_MASYS, ruta_reg, "/y"],
            capture_output=True, text=True, timeout=30,
            startupinfo=_startupinfo_oculto(), creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if resultado.returncode != 0:
            avisos.append(f"Ajustes (registro): {(resultado.stderr or resultado.stdout or '').strip()}")

    def _copiar_catalogo_scripts(self, avisos):
        ruta = os.path.join(msys_config.app_base_dir(), "SCRIPTS", "catalogo.json")
        self._copiar_archivo_base(ruta, "catalogo_scripts.json", avisos)

    def _copiar_archivo_base(self, ruta_origen, nombre_destino, avisos):
        ruta_origen = os.path.normpath(ruta_origen)
        if not os.path.isfile(ruta_origen):
            avisos.append(f"{nombre_destino}: no encontrado ({ruta_origen})")
            return
        try:
            shutil.copy2(ruta_origen, os.path.join(self.destino, nombre_destino))
        except OSError as e:
            avisos.append(f"{nombre_destino}: {e}")

    def _escribir_avisos(self, avisos):
        try:
            with open(os.path.join(self.destino, "AVISOS.txt"), "w", encoding="utf-8") as f:
                f.write("Algunos elementos no se pudieron copiar:\n\n" + "\n".join(avisos))
        except OSError:
            pass


# ── Perfil de usuario ─────────────────────────────────────────────────────

# AppData\Local se excluye a propósito de la lista entera: es
# fundamentalmente caché de aplicaciones (Chrome/Edge/Teams pueden ocupar
# varios GB ahí sin que sea "mis archivos" en ningún sentido útil).
_CARPETA_PERFIL_EXCLUIDA = "AppData\\Local"
# AppData\Roaming SÍ se ofrece pero sin marcar por defecto — es donde vive
# configuración de muchas apps, útil pero menos evidente que "mis fotos"
# como para incluirla sin que el usuario lo decida a propósito.
_CARPETAS_PERFIL_SIN_MARCAR_DEFECTO = {"AppData\\Roaming"}


def componentes_perfil_usuario():
    """Un componente por carpeta de perfil REALMENTE resuelta en este
    equipo (get_user_content_folders(), mismo mecanismo que ya usa
    Contenido — respeta reubicaciones a otra unidad/OneDrive). Devuelve
    (carpetas {nombre: ruta}, [(clave, etiqueta, marcado_por_defecto), ...])
    — sin depender de BackupComponente (msys/ui/...) para no acoplar este
    módulo de backend a la capa de UI; quien construye el diálogo (backups.py)
    ya sabe envolver estas tuplas."""
    carpetas = get_user_content_folders()
    carpetas.pop(_CARPETA_PERFIL_EXCLUIDA, None)
    return carpetas, [
        (nombre, f"{nombre} — {ruta}", nombre not in _CARPETAS_PERFIL_SIN_MARCAR_DEFECTO)
        for nombre, ruta in carpetas.items()
    ]


class UserProfileBackupThread(QThread):
    """Copia completa (shutil.copytree) de cada carpeta de perfil
    seleccionada — puede tardar bastante y ocupar mucho espacio según el
    contenido real; no hay forma de estimarlo de antemano sin recorrer
    primero toda la carpeta, así que el progreso es por CARPETA completada,
    no por bytes."""
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(str)
    finished_error = pyqtSignal(str)

    def __init__(self, destino: str, carpetas_seleccionadas: dict):
        super().__init__()
        self.destino = destino
        self.carpetas_seleccionadas = carpetas_seleccionadas  # {nombre: ruta_origen}

    def run(self):
        try:
            avisos = []
            total = len(self.carpetas_seleccionadas) or 1
            for idx, (nombre, ruta_origen) in enumerate(self.carpetas_seleccionadas.items()):
                pct = int((idx / total) * 90)
                self.progress.emit(pct, f"Copiando {nombre} (puede tardar)...")
                nombre_carpeta_destino = nombre.replace("\\", "_")
                destino_carpeta = os.path.join(self.destino, nombre_carpeta_destino)
                try:
                    shutil.copytree(ruta_origen, destino_carpeta, dirs_exist_ok=True)
                except OSError as e:
                    avisos.append(f"{nombre}: {e}")

            if avisos:
                self._escribir_avisos(avisos)
            self.progress.emit(100, "Completado")
            self.finished_ok.emit(self.destino)
        except Exception as e:
            self.finished_error.emit(str(e))

    def _escribir_avisos(self, avisos):
        try:
            with open(os.path.join(self.destino, "AVISOS.txt"), "w", encoding="utf-8") as f:
                f.write("Algunos elementos no se pudieron copiar completamente:\n\n" + "\n".join(avisos))
        except OSError:
            pass


# ── Nube ──────────────────────────────────────────────────────────────────

COMPONENTES_NUBE = [
    ("onedrive_config", "Cuentas y carpetas vinculadas de OneDrive (resumen)"),
]


def _cuentas_onedrive():
    """Mismo mecanismo de lectura del registro que
    SoporteMixin._detectar_soporte_onedrive (soporte.py) — cuentas
    Personal/Business bajo HKCU\\...\\OneDrive\\Accounts."""
    import re
    import winreg
    cuentas = []
    try:
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
                        datos = {"Tipo": nombre_subclave}
                        for valor in ("UserFolder", "UserEmail", "DisplayName", "SPOSiteId"):
                            try:
                                datos[valor], _ = winreg.QueryValueEx(subclave, valor)
                            except OSError:
                                pass
                        cuentas.append(datos)
                except OSError:
                    continue
    except OSError:
        pass
    return cuentas


class NubeBackupThread(QThread):
    """No copia los archivos sincronizados (eso ya lo hace OneDrive por
    definición, esa es la razón de ser de OneDrive) — vuelca a texto qué
    cuentas están vinculadas y a qué carpeta local corresponde cada una,
    para poder reconfigurar la sincronización tras una reinstalación sin
    tener que recordar de memoria qué cuenta iba con qué carpeta."""
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(str)
    finished_error = pyqtSignal(str)

    def __init__(self, destino: str, claves: list):
        super().__init__()
        self.destino = destino
        self.claves = claves

    def run(self):
        try:
            avisos = []
            if "onedrive_config" in self.claves:
                self.progress.emit(30, "Leyendo cuentas de OneDrive vinculadas...")
                self._exportar_onedrive(avisos)

            if avisos:
                self._escribir_avisos(avisos)
            self.progress.emit(100, "Completado")
            self.finished_ok.emit(self.destino)
        except Exception as e:
            self.finished_error.emit(str(e))

    def _exportar_onedrive(self, avisos):
        cuentas = _cuentas_onedrive()
        if not cuentas:
            avisos.append("OneDrive: no se encontró ninguna cuenta vinculada en este equipo")
            return
        lineas = [f"Cuentas de OneDrive vinculadas en este equipo ({len(cuentas)}):", ""]
        for cuenta in cuentas:
            lineas.append(f"- Tipo: {cuenta.get('Tipo', 'N/A')}")
            for etiqueta, clave in (("Email", "UserEmail"), ("Nombre", "DisplayName"), ("Carpeta local", "UserFolder")):
                if clave in cuenta:
                    lineas.append(f"    {etiqueta}: {cuenta[clave]}")
            lineas.append("")
        try:
            with open(os.path.join(self.destino, "OneDrive_Cuentas.txt"), "w", encoding="utf-8") as f:
                f.write("\n".join(lineas))
        except OSError as e:
            avisos.append(f"OneDrive: no se pudo escribir el resumen ({e})")

    def _escribir_avisos(self, avisos):
        try:
            with open(os.path.join(self.destino, "AVISOS.txt"), "w", encoding="utf-8") as f:
                f.write("Avisos de esta copia:\n\n" + "\n".join(avisos))
        except OSError:
            pass


# ── Bases de datos SQL Server ────────────────────────────────────────────
# Vía sqlcmd (nunca pyodbc/motor propio) -- si no está instalado, esta
# categoría queda deshabilitada en tiempo de uso (ver _generar_backup_sql
# en msys/ui/backups.py), no se empaqueta ninguna dependencia nueva.
# Autenticación de Windows integrada (-E) siempre -- nunca se pide ni se
# guarda una contraseña SQL en MASys. Solo instancias LOCALES: mismo
# alcance que el resto de la aplicación (gestiona ESTE equipo).

def sqlcmd_disponible() -> Optional[str]:
    """Ruta a sqlcmd.exe si está en el PATH, o None. sqlcmd viene con SQL
    Server, SSMS o las 'herramientas de línea de comandos de SQL Server'
    instaladas por separado -- no se asume presente solo por detectar el
    motor (son paquetes distintos)."""
    return shutil.which("sqlcmd")


def servidor_sql_local(nombre_instancia: str) -> str:
    """Cadena de conexión -S para una instancia LOCAL: '.' para la
    predeterminada (MSSQLSERVER), '.\\NOMBRE' para una con nombre."""
    if nombre_instancia.upper() == "MSSQLSERVER":
        return "."
    return f".\\{nombre_instancia}"


def listar_bases_datos_sql(sqlcmd_path: str, servidor: str) -> Tuple[bool, List[str], str]:
    """(ok, nombres_bd_usuario, mensaje_error). Excluye las 4 bases de
    sistema (master/model/msdb/tempdb, database_id 1-4) -- no tiene sentido
    ofrecer 'copia de seguridad' de las bases internas del motor aquí."""
    query = ("SET NOCOUNT ON; SELECT name FROM sys.databases WHERE database_id > 4 "
             "AND state_desc = 'ONLINE' ORDER BY name;")
    try:
        resultado = subprocess.run(
            [sqlcmd_path, "-S", servidor, "-E", "-b", "-h", "-1", "-W", "-Q", query],
            capture_output=True, text=True, timeout=30,
            startupinfo=_startupinfo_oculto(), creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if resultado.returncode != 0:
            return False, [], (resultado.stderr or resultado.stdout or "sqlcmd devolvió un error").strip()
        nombres = [linea.strip() for linea in resultado.stdout.splitlines() if linea.strip()]
        return True, nombres, ""
    except subprocess.TimeoutExpired:
        return False, [], "Tiempo agotado conectando con la instancia."
    except Exception as e:
        return False, [], str(e)


class SqlBackupThread(QThread):
    """BACKUP DATABASE ... TO DISK, una base a la vez, vía sqlcmd -E
    (autenticación de Windows integrada). El propio motor SQL Server
    escribe el .bak en disco -- necesita que la CUENTA DEL SERVICIO tenga
    permiso de escritura en 'destino' (no la cuenta con la que se ejecuta
    MASys), así que el destino por defecto en BACKUPS\\ del proyecto
    puede fallar en una instancia con cuenta de servicio restringida; el
    mensaje de error de sqlcmd se traslada tal cual para que quede claro
    que es un problema de permisos del servicio, no de MASys."""
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(str)
    finished_error = pyqtSignal(str)

    def __init__(self, destino: str, claves: list, sqlcmd_path: str, servidor: str):
        super().__init__()
        self.destino = destino
        self.claves = claves
        self.sqlcmd_path = sqlcmd_path
        self.servidor = servidor

    def run(self):
        try:
            total = len(self.claves) or 1
            avisos = []
            for idx, nombre_bd in enumerate(self.claves):
                self.progress.emit(int((idx / total) * 90), f"Copiando base de datos {nombre_bd}...")
                ruta_bak = os.path.join(self.destino, f"{nombre_bd}.bak")
                # Nombre entre corchetes con «]» doblado y textos con «'» doblado: una carpeta como «O'Brien»
                # rompía la orden de SQL.
                nombre_id = nombre_bd.replace("]", "]]")
                nombre_txt = nombre_bd.replace("'", "''")
                query = (
                    f"BACKUP DATABASE [{nombre_id}] TO DISK = N'{ruta_bak.replace(chr(39), chr(39) * 2)}' "
                    f"WITH INIT, STATS = 25, NAME = N'{nombre_txt}-Full-MASys';"
                )
                resultado = subprocess.run(
                    [self.sqlcmd_path, "-S", self.servidor, "-E", "-b", "-Q", query],
                    capture_output=True, text=True, timeout=3600,
                    startupinfo=_startupinfo_oculto(), creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if resultado.returncode != 0:
                    avisos.append(f"{nombre_bd}: {(resultado.stderr or resultado.stdout or '').strip()}")

            if avisos and len(avisos) == total:
                self.finished_error.emit("; ".join(avisos))
                return
            if avisos:
                self._escribir_avisos(avisos)
            self.progress.emit(100, "Completado")
            self.finished_ok.emit(self.destino)
        except subprocess.TimeoutExpired:
            self.finished_error.emit("Tiempo agotado haciendo la copia de la base de datos.")
        except Exception as e:
            self.finished_error.emit(str(e))

    def _escribir_avisos(self, avisos):
        try:
            with open(os.path.join(self.destino, "AVISOS.txt"), "w", encoding="utf-8") as f:
                f.write("Algunas bases de datos no se pudieron copiar:\n\n" + "\n".join(avisos))
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════════════
# RESTAURACIÓN
# ═══════════════════════════════════════════════════════════════════════
# Solo 3 categorías tienen restauración AUTOMÁTICA real: Registro de
# Windows, Configuración de MASys y Perfil de usuario -- las tres son
# reversiones directas y sin ambigüedad de su propio backup (reg import /
# copia de ficheros de vuelta a su carpeta de origen), reutilizando el
# MISMO mapeo clave->ruta/rama que ya usa cada generador de más arriba
# (fuente única de verdad, no se duplica el mapeo). El resto de categorías
# (Credenciales, Navegadores, Configuración del sistema, Correo, Nube)
# ofrecen el mismo selector "restaurar por partes" en modo GUIADO desde
# BackupRestoreDialog (msys/ui/backup_restore_dialog.py): se abre la
# carpeta del backup y se explica qué copiar y dónde, sin que MASys
# sobrescriba nada por sí solo -- son operaciones de mayor riesgo
# (credenciales DPAPI, perfiles de navegador en uso, perfiles de Outlook)
# que merecen supervisión directa del técnico antes de automatizarse.
# Bases de datos SQL Server: TAMPOCO tiene restauración automática ni
# guiada aquí a propósito -- RESTORE DATABASE sobre una instancia en
# producción exige poner la BD en modo de un solo usuario, decidir el
# WITH REPLACE/MOVE correctos y validar la cadena de backups (full/dif/log)
# antes de sobrescribir; automatizarlo mal es la forma más rápida de perder
# datos reales de un cliente. Restaurar un .bak es tarea de un DBA con
# SSMS/sqlcmd en el momento, no un botón de MASys.


def restaurar_registro_elevado(rutas_reg: list) -> tuple:
    """Reimporta los .reg seleccionados en una consola PowerShell elevada
    aparte -- mismo patrón que ActiveDirectoryMixin._instalar_rsat
    (msys/ui/ad_info.py): reg import sobre ramas HKLM necesita privilegios
    de administrador y MASys mismo no siempre se ejecuta elevado.
    Fire-and-forget con consola visible: el técnico ve el resultado real de
    cada 'reg import' (incluidos fallos), no un resumen post-hoc que podría
    no reflejar un fallo silencioso de una consola oculta."""
    rutas_validas = [r for r in rutas_reg if os.path.isfile(r)]
    if not rutas_validas:
        return False, "No hay ficheros .reg seleccionados, o no se encuentran en el backup elegido."
    pasos = []
    for ruta in rutas_validas:
        ruta_escapada = ruta.replace("'", "''")
        nombre = os.path.basename(ruta)
        pasos.append(f"Write-Host 'Importando {nombre}...' -ForegroundColor Cyan; reg import '{ruta_escapada}'")
    comando = "; Write-Host ''; ".join(pasos)
    comando += "; Write-Host ''; Write-Host 'Restauracion completada. Puedes cerrar esta ventana.' -ForegroundColor Green"
    argumentos = f'-NoExit -ExecutionPolicy Bypass -Command "{comando}"'
    try:
        win32api.ShellExecute(0, "runas", "powershell.exe", argumentos, None, 1)
        return True, ""
    except Exception as e:
        return False, str(e)


class RegistryRestoreThread(QThread):
    """Adaptador QThread para restaurar_registro_elevado() -- mismo
    contrato de señales (progress/finished_ok/finished_error) que el resto
    de hilos de restauración para que BackupRestoreDialog los trate todos
    igual, aunque el 'reg import' real ocurra en una consola elevada aparte
    y no se pueda seguir su progreso real desde aquí: finished_ok se emite
    en cuanto la consola se LANZA correctamente, no cuando termina."""
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(str)
    finished_error = pyqtSignal(str)

    def __init__(self, carpeta_backup: str, claves: list):
        super().__init__()
        self.carpeta_backup = carpeta_backup
        self.claves = claves

    def run(self):
        self.progress.emit(20, "Abriendo consola elevada para reg import...")
        rutas = [
            os.path.join(self.carpeta_backup, RegistryBackupThread._RAMAS[clave][1])
            for clave in self.claves if clave in RegistryBackupThread._RAMAS
        ]
        ok, mensaje = restaurar_registro_elevado(rutas)
        if ok:
            self.progress.emit(
                100, "Consola elevada lanzada — revisa esa ventana para el resultado real de cada import")
            self.finished_ok.emit(self.carpeta_backup)
        else:
            self.finished_error.emit(mensaje)


class UserProfileRestoreThread(QThread):
    """Reverso de UserProfileBackupThread: copia de vuelta desde la carpeta
    de backup a la ruta de perfil real (misma tabla carpetas {nombre: ruta}
    que devuelve componentes_perfil_usuario()). copytree con
    dirs_exist_ok=True: FUSIONA con lo que ya haya en destino (sobrescribe
    ficheros con el mismo nombre, no borra los que solo existan ya en
    destino) -- mismo criterio que el propio backup al copiar."""
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(str)
    finished_error = pyqtSignal(str)

    def __init__(self, carpeta_backup: str, carpetas_reales: dict, claves: list):
        super().__init__()
        self.carpeta_backup = carpeta_backup
        self.carpetas_reales = carpetas_reales  # {nombre: ruta_real}, de componentes_perfil_usuario()
        self.claves = claves

    def run(self):
        try:
            avisos = []
            total = len(self.claves) or 1
            for idx, nombre in enumerate(self.claves):
                pct = int((idx / total) * 90)
                ruta_real = self.carpetas_reales.get(nombre)
                if not ruta_real:
                    avisos.append(f"{nombre}: no se reconoce esta carpeta en el equipo actual")
                    continue
                nombre_carpeta_backup = nombre.replace("\\", "_")
                origen = os.path.join(self.carpeta_backup, nombre_carpeta_backup)
                if not os.path.isdir(origen):
                    avisos.append(f"{nombre}: no está presente en el backup elegido")
                    continue
                self.progress.emit(pct, f"Restaurando {nombre} (puede tardar)...")
                try:
                    os.makedirs(ruta_real, exist_ok=True)
                    shutil.copytree(origen, ruta_real, dirs_exist_ok=True)
                except OSError as e:
                    avisos.append(f"{nombre}: {e}")

            if avisos and len(avisos) == total:
                self.finished_error.emit("; ".join(avisos))
                return
            if avisos:
                self._escribir_avisos(avisos)
            self.progress.emit(100, "Completado")
            self.finished_ok.emit(self.carpeta_backup)
        except Exception as e:
            self.finished_error.emit(str(e))

    def _escribir_avisos(self, avisos):
        try:
            with open(os.path.join(self.carpeta_backup, "AVISOS_RESTAURACION.txt"), "w", encoding="utf-8") as f:
                f.write("Algunos elementos no se pudieron restaurar:\n\n" + "\n".join(avisos))
        except OSError:
            pass


class MASysConfigRestoreThread(QThread):
    """Reverso de MASysConfigBackupThread. 'base_datos' (multisys.db)
    queda EXCLUIDA a propósito: es la base de datos que el propio proceso
    de MASys tiene abierta en este mismo instante -- sobrescribirla en
    caliente puede corromper el fichero o fallar por bloqueo de Windows.
    Se restaura a mano, con MASys cerrado (ver guía en
    BackupRestoreDialog)."""
    progress = pyqtSignal(int, str)
    finished_ok = pyqtSignal(str)
    finished_error = pyqtSignal(str)

    def __init__(self, carpeta_backup: str, claves: list):
        super().__init__()
        self.carpeta_backup = carpeta_backup
        self._pedia_base_datos = "base_datos" in claves
        self.claves = [c for c in claves if c != "base_datos"]

    def run(self):
        if self._pedia_base_datos and not self.claves:
            self.finished_error.emit(
                "La base de datos local (multisys.db) no se restaura automáticamente: "
                "cierra MASys y copia el fichero a mano desde la carpeta del backup.")
            return
        try:
            # 'notas' son informativas (no cuentan como fallo real de lo que
            # SÍ se intentó); 'avisos' son fallos reales de self.claves,
            # usados para decidir si TODO lo intentado falló.
            notas = []
            if self._pedia_base_datos:
                # No se calla: si se pidió junto con otras claves, el
                # técnico debe saber que ESTA parte en concreto no se tocó.
                notas.append(
                    "multisys.db: no se restaura automáticamente (base de datos en uso por "
                    "el propio proceso) — cierra MASys y cópiala a mano desde el backup")
            avisos = []
            total = len(self.claves) or 1
            for idx, clave in enumerate(self.claves):
                pct = int((idx / total) * 90)
                if clave == "config_app":
                    self.progress.emit(pct, "Restaurando configuración de la aplicación...")
                    self._restaurar_archivo("buscacompartidos_config.json", msys_config.app_base_dir(), avisos)
                    # Copias anteriores a 4.1.7 llevan el nombre antiguo
                    nombre_reg = "Ajustes_MASys.reg"
                    if not os.path.isfile(os.path.join(self.carpeta_backup, nombre_reg)) and                             os.path.isfile(os.path.join(self.carpeta_backup, "Ajustes_MultiSYS.reg")):
                        nombre_reg = "Ajustes_MultiSYS.reg"
                    self._restaurar_reg_hkcu(nombre_reg, avisos)
                elif clave == "catalogo_scripts":
                    self.progress.emit(pct, "Restaurando catálogo de scripts...")
                    destino_carpeta = os.path.join(msys_config.app_base_dir(), "SCRIPTS")
                    os.makedirs(destino_carpeta, exist_ok=True)
                    self._restaurar_archivo("catalogo_scripts.json", destino_carpeta, avisos, nombre_destino="catalogo.json")
                elif clave == "perfiles_navegacion":
                    self.progress.emit(pct, "Restaurando perfiles de navegación...")
                    self._restaurar_perfiles_navegacion(avisos)

            if avisos and len(avisos) >= total:
                self.finished_error.emit("; ".join(notas + avisos))
                return
            if notas or avisos:
                self._escribir_avisos(notas + avisos)
            self.progress.emit(100, "Completado")
            self.finished_ok.emit(self.carpeta_backup)
        except Exception as e:
            self.finished_error.emit(str(e))

    def _restaurar_archivo(self, nombre_en_backup, carpeta_destino, avisos, nombre_destino=None):
        origen = os.path.join(self.carpeta_backup, nombre_en_backup)
        if not os.path.isfile(origen):
            avisos.append(f"{nombre_en_backup}: no está presente en el backup elegido")
            return
        try:
            shutil.copy2(origen, os.path.join(carpeta_destino, nombre_destino or nombre_en_backup))
        except OSError as e:
            avisos.append(f"{nombre_en_backup}: {e}")

    def _restaurar_perfiles_navegacion(self, avisos):
        """Reverso de _exportar_perfiles_navegacion. A diferencia de
        'base_datos', esto SÍ se restaura en caliente: escribe solo la
        tabla nav_profiles (vía NavProfilesDB, su propia conexión SQLite
        de corta duración), no reemplaza el fichero multisys.db completo
        que el proceso ya tiene abierto -- incluye 'MASys' a propósito,
        aunque esté protegido contra sobrescritura manual desde el propio
        diálogo de perfiles: una restauración completa de un backup SÍ
        debe poder recuperar también el perfil predeterminado tal como
        estaba en ese momento."""
        origen = os.path.join(self.carpeta_backup, "PerfilesNavegacion.json")
        if not os.path.isfile(origen):
            avisos.append("Perfiles de navegación: no está presente en el backup elegido")
            return
        try:
            with open(origen, "r", encoding="utf-8") as f:
                perfiles = json.load(f)
            db = NavProfilesDB()
            for nombre, config in perfiles.items():
                if config:
                    db.guardar(nombre, config)
        except Exception as e:
            avisos.append(f"Perfiles de navegación: {e}")

    def _restaurar_reg_hkcu(self, nombre_en_backup, avisos):
        """HKCU no necesita elevación (siempre escribible por el propio
        usuario) -- a diferencia de restaurar_registro_elevado() para HKLM,
        esto puede ir en el mismo hilo sin pedir UAC."""
        origen = os.path.join(self.carpeta_backup, nombre_en_backup)
        if not os.path.isfile(origen):
            avisos.append(f"{nombre_en_backup}: no está presente en el backup elegido")
            return
        resultado = subprocess.run(
            ["reg", "import", origen],
            capture_output=True, text=True, timeout=30,
            startupinfo=_startupinfo_oculto(), creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if resultado.returncode != 0:
            avisos.append(f"{nombre_en_backup}: {(resultado.stderr or resultado.stdout or '').strip()}")

    def _escribir_avisos(self, avisos):
        try:
            with open(os.path.join(self.carpeta_backup, "AVISOS_RESTAURACION.txt"), "w", encoding="utf-8") as f:
                f.write("Algunos elementos no se pudieron restaurar:\n\n" + "\n".join(avisos))
        except OSError:
            pass
