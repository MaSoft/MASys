# -*- coding: utf-8 -*-
"""Modelo de datos de MASys: una @dataclass ...Row por tipo de fila que
alimenta las tablas de la aplicacion, junto con las constantes/diccionarios
de etiquetas y helpers que estan directamente ligados a un tipo de fila
concreto (traduccion de estado a espanol, clasificacion de riesgo, etc.).
Extraido de multisys.py (Fase 1 de modularizacion)."""

import os
import re
import ipaddress
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Optional, Dict

import psutil

from msys.core.utils import clasificar_riesgo_registro

@dataclass
class SharePermissionRow:
    recurso: str
    ruta: str
    identidad: str
    permiso: str
    tipo: str
    origen: str

    @property
    def es_riesgo_everyone_escritura(self) -> bool:
        """Marca como riesgo un permiso PERMITIR de escritura/modificación/
        control total concedido a 'Todos'/'Everyone' (acceso no restringido
        a cualquier usuario que alcance el recurso)."""
        identidad_u = (self.identidad or "").upper()
        es_everyone = "TODOS" in identidad_u or "EVERYONE" in identidad_u
        es_permitir = "permitir" in (self.tipo or "").lower() or "allow" in (self.tipo or "").lower()
        permiso_l = (self.permiso or "").lower()
        es_escritura = any(p in permiso_l for p in ("control total", "escritura", "modificar"))
        return es_everyone and es_permitir and es_escritura

@dataclass
class NetworkDriveRow:
    letra: str
    ruta_remota: str
    descripcion: str
    estado: str
    # Nuevos campos para v3.0
    total_gb: str = "N/A"
    usado_gb: str = "N/A"
    libre_gb: str = "N/A"
    porcentaje_uso: str = "N/A"
    credencial: str = "N/A"
    persistente: str = "N/A"
    version_smb: str = "N/A"
    velocidad: str = "N/A"
    ping_ms: str = "N/A"
    accesible: str = "Sí"

@dataclass
class CredentialRow:
    destino: str
    usuario: str
    tipo: str
    categoria: str = "Genérico"
    contraseña: str = "(no disponible)"
    # Nuevos campos para v3.0
    ultima_modificacion: str = "N/A"
    persistencia: str = "N/A"
    estado: str = "No verificada"
    fuente: str = "Credential Manager"  # Credential Manager, SSH, Git, RDP, etc.

@dataclass
class WiFiRow:
    nombre: str
    contraseña: str
    autenticacion: str
    cifrado: str
    tipo_conexion: str = "N/A"
    conectado: str = "No"

    @property
    def es_seguridad_debil(self) -> bool:
        """Red sin cifrado real (abierta) o con WEP — protocolo roto desde
        hace más de una década (ataques triviales en minutos), el patrón
        de riesgo a auditar en redes WiFi guardadas."""
        auth = self.autenticacion.strip().lower()
        return (auth in ("open", "abierta", "ninguna", "none", "")
                or "wep" in auth)

@dataclass
class CertificateRow:
    asunto: str
    emisor: str
    valido_desde: str
    valido_hasta: str
    huella_digital: str
    uso: str
    ubicacion: str  # CurrentUser\My, LocalMachine\My, etc.
    estado: str = "Válido"
    exportable: str = "No"  # Sí/No - indica si tiene clave privada exportable
    algoritmo_firma: str = ""  # p.ej. sha256RSA, sha1RSA, md5RSA...
    autofirmado: str = "No"  # Sí/No - Subject == Issuer

    @property
    def sospechoso(self) -> bool:
        """Certificado personal (\\My, no \\Root) con algoritmo de firma
        débil (MD5/SHA1/MD2 — criptográficamente rotos u obsoletos) o
        autofirmado. Restringido a \\My a propósito: TODOS los certificados
        con algoritmo débil detectados en este equipo viven en \\Root (CAs
        antiguas incluidas de fábrica por Windows, no un problema real), y
        Root es intrínsecamente autofirmado por definición — incluirlo
        aquí sería puro ruido, igual que ya se decidió para el detector de
        caducados."""
        if "\\My" not in self.ubicacion:
            return False
        algoritmo = (self.algoritmo_firma or "").lower()
        if algoritmo.startswith(("md5", "md2", "sha1")):
            return True
        if (self.autofirmado or "").strip().lower() in ("si", "sí"):
            return True
        return False

    @property
    def motivo_sospecha(self) -> str:
        if not self.sospechoso:
            return ""
        motivos = []
        algoritmo = (self.algoritmo_firma or "").lower()
        if algoritmo.startswith(("md5", "md2", "sha1")):
            motivos.append(f"algoritmo de firma débil ({self.algoritmo_firma})")
        if (self.autofirmado or "").strip().lower() in ("si", "sí"):
            motivos.append("autofirmado")
        return " y ".join(motivos)

@dataclass
class ProcessRow:
    """Proceso en ejecución (vía psutil, ver ProcessManager). A diferencia de
    ServiceRow, 'name' NO es único — varias instancias pueden compartir el
    mismo nombre de ejecutable (p.ej. varios 'chrome.exe'). El identificador
    real de la fila para acciones (Finalizar/Suspender/...) es 'pid', no
    'name'. Las notas se guardan por 'name' (ver ProcessNotesDB) porque el
    PID no sobrevive a un reinicio del proceso."""
    pid: int
    name: str
    status: str
    priority: str
    username: str
    exe_path: str = ""
    ppid: int = 0
    num_threads: int = 0
    memory_mb: float = 0.0
    cpu_percent: float = 0.0
    children: List[str] = field(default_factory=list)

# Estados de proceso (psutil.STATUS_*). En Windows, un proceso suspendido
# (vía ProcessManager.suspend_process, o pausado por un depurador) se
# informa con status "stopped", NO "suspended" (comprobado empíricamente:
# psutil.STATUS_SUSPENDED existe como constante pero no es lo que este SO
# devuelve realmente) — por eso 'stopped' se traduce como "Suspendido" y es
# la clave que usa toda la lógica de botones/filtros; 'suspended' se deja
# solo como fallback defensivo.
PROCESS_STATUS_LABELS = {
    "running":      "Ejecutándose",
    "sleeping":     "En espera",
    "disk-sleep":   "E/S en curso",
    "stopped":      "Suspendido",
    "tracing-stop": "En depuración",
    "zombie":       "Zombi",
    "dead":         "Finalizado",
    "wake-kill":    "Reanudando",
    "waking":       "Reanudando",
    "idle":         "Inactivo",
    "locked":       "Bloqueado",
    "waiting":      "Esperando",
    "suspended":    "Suspendido",
    "unknown":      "Desconocido",
}

# Prioridad de proceso — equivalente al "Tipo de inicio" de servicios, pero
# con las clases de prioridad reales de Windows (las mismas 6 que ofrece el
# Administrador de tareas).
PROCESS_PRIORITY_LABELS = {
    "realtime":     "Tiempo real",
    "high":         "Alta",
    "above_normal": "Por encima de lo normal",
    "normal":       "Normal",
    "below_normal": "Por debajo de lo normal",
    "idle":         "Baja",
    "unknown":      "Desconocido",
}

_PROCESS_PRIORITY_MAP: Dict[int, str] = {
    psutil.REALTIME_PRIORITY_CLASS:     "realtime",
    psutil.HIGH_PRIORITY_CLASS:         "high",
    psutil.ABOVE_NORMAL_PRIORITY_CLASS: "above_normal",
    psutil.NORMAL_PRIORITY_CLASS:       "normal",
    psutil.BELOW_NORMAL_PRIORITY_CLASS: "below_normal",
    psutil.IDLE_PRIORITY_CLASS:         "idle",
}
_PROCESS_PRIORITY_CONST: Dict[str, int] = {v: k for k, v in _PROCESS_PRIORITY_MAP.items()}

# Procesos críticos del sistema que no deben finalizarse a la ligera. Su
# nombre aparece en rojo como advertencia visual (igual que _CRITICAL_SERVICES)
# y la confirmación de "Finalizar" añade un aviso reforzado.
_CRITICAL_PROCESSES = {
    "system", "system idle process", "registry", "secure system", "memory compression",
    "smss.exe", "csrss.exe", "wininit.exe", "winlogon.exe",
    "services.exe", "lsass.exe", "svchost.exe", "dwm.exe",
    "explorer.exe", "spoolsv.exe", "msmpeng.exe", "nissrv.exe",
}

# Pseudo-procesos del kernel (sin nombre de archivo real, ver Administrador
# de tareas) — NUNCA tienen ruta de ejecutable ni PID de usuario normal. Si
# alguno apareciera con exe_path, sería un proceso real haciéndose pasar por
# uno de estos nombres especiales — señal más fuerte que el resto de checks,
# porque los legítimos no pueden tenerla por definición.
_PROCESOS_SIN_RUTA_LEGITIMOS = {"system", "system idle process", "registry", "secure system", "memory compression"}


def _pseudo_proceso_con_ruta_falsa(row: "ProcessRow") -> bool:
    return row.name.lower() in _PROCESOS_SIN_RUTA_LEGITIMOS and bool(row.exe_path)

# Servicios críticos de Windows que no deben detenerse manualmente (portado
# de MA_SERV, _CRITICAL_SERVICES en services_tab.py). Su nombre para mostrar
# aparece en rojo en la tabla como advertencia visual (mismo criterio que
# _CRITICAL_PROCESSES para Procesos).
_CRITICAL_SERVICES = {
    "RpcSs", "RpcEptMapper", "DcomLaunch",   # RPC / COM
    "LSM", "SamSs", "KeyIso", "VaultSvc",    # Sesión / autenticación
    "lsass",                                  # Autoridad de seguridad local
    "BFE", "MpsSvc", "WinDefend",            # Firewall / Defender
    "SecurityHealthService", "wscsvc",        # Centro de seguridad
    "Dhcp", "Dnscache", "nsi",               # Red básica
    "NlaSvc", "netprofm", "iphlpsvc",        # Red — ubicación y helper
    "LanmanWorkstation", "LanmanServer",     # SMB cliente / servidor
    "EventLog", "EventSystem",               # Registro de eventos
    "Winmgmt",                               # WMI
    "SENS", "SystemEventsBroker",            # Eventos de sistema
    "PlugPlay", "Power",                     # Hardware y energía
    "CryptSvc", "Sppsvc",                    # Criptografía / licencias
    "ProfSvc", "UserManager",               # Perfiles y sesiones de usuario
    "Schedule", "BITS",                      # Programador de tareas / transferencias
    "wuauserv",                              # Windows Update
    "gpsvc",                                 # Directiva de grupo
    "W32Time",                               # Hora de Windows
    "NETLOGON",                              # Net Logon (dominio)
    "CoreMessagingRegistrar", "StateRepository", "TimeBrokerSvc",
    "CDPSvc",                                # Plataforma de dispositivos conectados
}

# Servicios propios de Windows, usados por el filtro "Ocultar Windows" de la
# pestaña Servicios (portado de MA_SERV). No es una lista exhaustiva de todo
# lo que trae Windows, sino de los nombres de servicio más comunes.
_WINDOWS_SERVICES = _CRITICAL_SERVICES | {
    "AudioSrv", "AudioEndpointBuilder", "UsoSvc", "WaaSMedicSvc", "TrustedInstaller",
    "DoSvc", "wlidsvc", "TokenBroker", "WSearch", "Themes", "ShellHWDetection", "SysMain",
    "WerSvc", "DiagTrack", "dmwappushservice", "DPS", "WdiServiceHost", "WdiSystemHost",
    "PcaSvc", "Spooler", "VDS", "VSS", "swprv", "TBS", "BDESVC", "StorSvc",
    "COMSysApp", "MSDTC", "AppXSvc", "ClipSVC", "InstallService", "wsappx",
    "bthserv", "BTAGService", "DeviceAssociationService", "DevQueryBroker", "NgcSvc",
    "NgcCtnrSvc", "DsmSvc", "WbioSrvc", "NaturalAuthentication", "FontCache",
    "fdPHost", "FDResPub", "SSDPSRV", "upnphost", "lltdsvc", "SCardSvr", "ScDeviceEnum",
    "FrameServer", "StiSvc", "stisvc", "Sense", "WdNisSvc",
    "WlanSvc", "dot3svc", "Wcmsvc", "WwanSvc", "NcdAutoSetup", "Netman", "NetSetupSvc",
    "RmSvc", "lmhosts", "WinHttpAutoProxySvc", "TapiSrv", "TrkWks",
    "TermService", "SessionEnv", "UmRdpService", "RasMan", "RasAuto", "RasSstp",
    "seclogon", "CertPropSvc", "cbdhsvc", "OneSyncSvc", "PimIndexMaintenanceSvc",
    "MapsBroker", "lfsvc", "DusmSvc", "wbengine", "SDRSVC",
    "WpnService", "WpnUserService", "WalletService", "PhoneSvc",
    "XblAuthManager", "XblGameSave", "XboxNetApiSvc", "XboxGipSvc",
    "HvHost", "vmicvss", "vmicshutdown", "vmicrdv", "vmictimesync",
    "vmicheartbeat", "vmickvpexchange", "vmicguestinterface", "wecsvc", "hidserv",
}

_CRITICAL_SERVICES_LOWER = {s.lower() for s in _CRITICAL_SERVICES}
_WINDOWS_SERVICES_LOWER = {s.lower() for s in _WINDOWS_SERVICES}


def _is_windows_service(row: "ServiceRow") -> bool:
    """Con bin_path disponible, comprobar la ruta real es más robusto que la
    lista estática _WINDOWS_SERVICES (nunca exhaustiva) — mismo criterio que
    _is_windows_process. Si por lo que sea no se pudo leer bin_path (fallo de
    QueryServiceConfig), se cae a la lista de nombres como red de seguridad."""
    if row.bin_path:
        return row.bin_path.lower().startswith(_PROCESS_WINDIR)
    return row.name.lower() in _WINDOWS_SERVICES_LOWER


def _servicio_disfrazado(row: "ServiceRow") -> bool:
    """Los servicios con un nombre real de Windows (_CRITICAL_SERVICES) casi
    siempre corren alojados en svchost.exe o en un ejecutable propio del
    sistema, SIEMPRE desde System32/SysWOW64 — si el BinPath de uno de esos
    nombres apunta a otro sitio, alguien reescribió la configuración de ese
    servicio (registro) para ejecutar otra cosa bajo un nombre de confianza."""
    if row.name.lower() not in _CRITICAL_SERVICES_LOWER:
        return False
    if not row.bin_path:
        return False
    ruta = row.bin_path.lower()
    return not (f"{_PROCESS_WINDIR}\\system32\\" in ruta or f"{_PROCESS_WINDIR}\\syswow64\\" in ruta)


def _ruta_servicio_sin_comillas(row: "ServiceRow") -> bool:
    """'Unquoted service path': si la parte del ejecutable de BinPath (hasta
    el primer '.exe') tiene un espacio y no está entre comillas, Windows
    prueba a ejecutar cada sub-ruta separada por espacios en orden hasta dar
    con un .exe — si un atacante coloca un ejecutable en una de esas
    sub-rutas intermedias, se ejecuta con los privilegios del servicio (a
    menudo SYSTEM). Vulnerabilidad clásica y ampliamente buscada por
    herramientas de auditoría (winPEAS, PowerUp...)."""
    ruta = row.bin_path or ""
    if not ruta or ruta.startswith('"'):
        return False
    idx = ruta.lower().find(".exe")
    if idx == -1:
        return False
    return " " in ruta[:idx + 4]


def _ruta_servicio_corregida(bin_path: str) -> str:
    """Ruta propuesta con el ejecutable entre comillas, dejando cualquier
    argumento posterior (' -k netsvcs'...) fuera de las comillas."""
    idx = bin_path.lower().find(".exe")
    if idx == -1:
        return bin_path
    return f'"{bin_path[:idx + 4]}"{bin_path[idx + 4:]}'


def _ruta_ejecutable_de_bin_path(bin_path: str) -> str:
    """Extrae solo la ruta del ejecutable de un BinPath de servicio,
    descartando argumentos (' -k netsvcs', ' -p'...) y comillas envolventes
    — necesario para poder calcular hash/verificar firma digital del
    ejecutable real, no de la línea de comandos completa del servicio."""
    ruta = (bin_path or "").strip()
    if ruta.startswith('"'):
        fin = ruta.find('"', 1)
        return ruta[1:fin] if fin != -1 else ruta[1:]
    idx = ruta.lower().find(".exe")
    return ruta[:idx + 4] if idx != -1 else ruta

# "Ocultar Windows" para procesos: a diferencia de los servicios (lista
# estática de nombres, enumerable de antemano), un proceso puede ser
# lanzado por cualquier ejecutable — la forma robusta de saber si "es de
# Windows" es comprobar si corre desde %WINDIR%, no mantener una lista de
# nombres que nunca sería exhaustiva.
_PROCESS_WINDIR = os.environ.get("WINDIR", r"C:\Windows").lower()


def _is_windows_process(row: "ProcessRow") -> bool:
    if row.exe_path:
        return row.exe_path.lower().startswith(_PROCESS_WINDIR)
    return row.name.lower() in _CRITICAL_PROCESSES

# Técnica clásica de malware: nombrar el ejecutable igual que un proceso
# crítico del sistema (svchost.exe, csrss.exe...) para pasar desapercibido
# en el Administrador de tareas — pero colocado FUERA de su carpeta real.
# Cada entrada es la lista de fragmentos de ruta (en minúsculas) donde ESE
# nombre es legítimo; si el ejecutable no vive en ninguno, es una señal
# fuerte de proceso disfrazado, no solo "no es de Windows".
_RUTAS_ESPERADAS_PROCESOS_CRITICOS = {
    "smss.exe":     [f"{_PROCESS_WINDIR}\\system32\\"],
    "csrss.exe":    [f"{_PROCESS_WINDIR}\\system32\\"],
    "wininit.exe":  [f"{_PROCESS_WINDIR}\\system32\\"],
    "winlogon.exe": [f"{_PROCESS_WINDIR}\\system32\\"],
    "services.exe": [f"{_PROCESS_WINDIR}\\system32\\"],
    "lsass.exe":    [f"{_PROCESS_WINDIR}\\system32\\"],
    "svchost.exe":  [f"{_PROCESS_WINDIR}\\system32\\", f"{_PROCESS_WINDIR}\\syswow64\\"],
    "dwm.exe":      [f"{_PROCESS_WINDIR}\\system32\\"],
    "spoolsv.exe":  [f"{_PROCESS_WINDIR}\\system32\\"],
    # explorer.exe vive DIRECTAMENTE en %WINDIR%, no en System32 — se
    # comprueba aparte (coincidencia exacta) en _proceso_disfrazado.
    "msmpeng.exe":  ["\\windows defender\\"],
    "nissrv.exe":   ["\\windows defender\\"],
}


def _proceso_disfrazado(row: "ProcessRow") -> bool:
    """True si el proceso se llama igual que uno crítico del sistema pero
    su ejecutable NO vive en la carpeta donde ese proceso vive de verdad."""
    nombre = row.name.lower()
    if not row.exe_path:
        return False
    ruta = row.exe_path.lower()
    if nombre == "explorer.exe":
        return ruta != f"{_PROCESS_WINDIR}\\explorer.exe"
    fragmentos = _RUTAS_ESPERADAS_PROCESOS_CRITICOS.get(nombre)
    if fragmentos is None:
        return False
    return not any(frag in ruta for frag in fragmentos)

# Etiquetas en español para las claves internas de ServiceManager/ServiceRow.
STATUS_LABELS = {
    "running":          "Ejecutándose",
    "stopped":          "Detenido",
    "paused":           "Pausado",
    "start_pending":    "Iniciando...",
    "stop_pending":     "Deteniendo...",
    "continue_pending": "Reanudando...",
    "pause_pending":    "Pausando...",
    "unknown":          "Desconocido",
    "n/a":              "No aplica",
}

STARTUP_LABELS = {
    "auto":         "Automático",
    "auto_delayed": "Automático (retrasado)",
    "manual":       "Manual",
    "disabled":     "Deshabilitado",
    "boot":         "Arranque",
    "system":       "Sistema",
    "unknown":      "Desconocido",
}

@dataclass
class ServiceRow:
    """Servicio de Windows con detalle completo (vía pywin32, ver ServiceManager).
    'status'/'startup_type' guardan la clave interna en inglés (running/stopped/...,
    auto/manual/...); las etiquetas en español se resuelven con STATUS_LABELS/
    STARTUP_LABELS al mostrar, para no repetir comparaciones de texto frágiles."""
    name: str
    display_name: str
    status: str
    startup_type: str
    account: str = ""
    description: str = ""
    pid: int = 0
    dependencies: List[str] = field(default_factory=list)
    bin_path: str = ""

@dataclass
class SqlInstanceRow:
    """Instancia de SQL Server (motor de base de datos) o versión de
    LocalDB detectada por registro (ver detectar_sql_server en
    collectors.py) -- sin depender de sqlcmd/pyodbc. 'estado_servicio'/
    'estado_agente' guardan la clave interna de STATUS_LABELS (running/
    stopped/..., o 'n/a' para LocalDB, que no tiene servicio de Windows:
    arranca bajo demanda por usuario vía SqlLocalDB.exe)."""
    instancia: str
    tipo: str  # "Motor de base de datos" | "LocalDB"
    version: str
    edicion: str
    service_name: str = ""
    estado_servicio: str = "unknown"
    agent_service_name: str = ""
    estado_agente: str = "unknown"
    ruta_datos: str = "N/A"

@dataclass
class NetworkConnectionRow:
    protocolo: str
    direccion_local: str
    direccion_remota: str
    estado: str
    proceso: str
    pid: str
    exe_path: str = ""

    _PROCESOS_SISTEMA = {
        "svchost", "system", "idle", "lsass", "wininit", "services", "csrss",
        "smss", "registry", "memcompression", "spoolsv", "dwm", "winlogon",
        "fontdrvhost", "msmpeng", "securityhealthservice", "system idle process",
    }

    @property
    def name(self) -> str:
        """Alias de 'proceso' CON extensión .exe normalizada — permite
        reusar _proceso_disfrazado/_is_windows_process (pensadas para
        ProcessRow.name, que psutil siempre da con extensión) por duck
        typing. Get-Process.ProcessName (la fuente real de 'proceso'
        aquí) NUNCA incluye la extensión — sin este ajuste la comparación
        contra _RUTAS_ESPERADAS_PROCESOS_CRITICOS (claves con '.exe')
        jamás habría encontrado ninguna coincidencia, dejando
        es_disfrazado permanentemente en False."""
        nombre = self.proceso.strip()
        if nombre and not nombre.lower().endswith(".exe"):
            return nombre + ".exe"
        return nombre

    @property
    def es_sistema(self) -> bool:
        return self.proceso.strip().lower() in self._PROCESOS_SISTEMA

    @property
    def es_sospechosa(self) -> bool:
        """No se pudo identificar el proceso dueño del socket (ya había
        terminado cuando se consultó, o está deliberadamente oculto) —
        indicio típico a revisar, no una certeza de compromiso."""
        return self.proceso.strip().lower() in ("", "n/a")

    @property
    def es_disfrazado(self) -> bool:
        """Mismo criterio que en Procesos: nombre de proceso crítico del
        sistema (svchost.exe, csrss.exe...) corriendo FUERA de su carpeta
        real — aquí es más grave que en Procesos porque implica una
        conexión de red (o puerto en escucha) activa ahora mismo, no solo
        presencia en la lista."""
        return _proceso_disfrazado(self)

    @property
    def es_ip_publica(self) -> bool:
        """True si la dirección remota es una IP pública de Internet — ni
        privada (RFC1918), ni loopback, ni link-local, ni 'N/A'/'0.0.0.0'
        de un endpoint UDP o un TCP en escucha sin nada conectado aún.
        Útil para separar tráfico a Internet del tráfico a la LAN."""
        ip_texto = self.direccion_remota.rsplit(":", 1)[0].strip("[]")
        if not ip_texto or ip_texto.lower() in ("n/a", "-", "0.0.0.0", "::"):
            return False
        try:
            ip = ipaddress.ip_address(ip_texto)
        except ValueError:
            return False
        return not (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_multicast or ip.is_reserved or ip.is_unspecified)

@dataclass
class InstalledSoftwareRow:
    nombre: str
    version: str
    editor: str
    fecha_instalacion: str
    carpeta_instalacion: str
    uninstall_string: str = ""
    system_component: bool = False

    @property
    def dias_desde_instalacion(self) -> Optional[int]:
        """Días transcurridos desde la instalación (fecha_instalacion,
        formato dd/MM/yyyy), o None si no se puede calcular — muchos
        instaladores no registran InstallDate en el Registro."""
        try:
            fecha_dt = datetime.strptime(self.fecha_instalacion.strip(), "%d/%m/%Y")
            return (datetime.now() - fecha_dt).days
        except Exception:
            return None

# NO incluye ProgramData: es la carpeta "datos compartidos de todos los
# usuarios" oficial de Windows — muchísimo software 100% legítimo vive ahí
# (comprobado con datos reales: Windows Defender ejecuta MpCmdRun.exe
# directamente desde C:\ProgramData\Microsoft\Windows Defender\Platform\...),
# así que marcarla como sospechosa por sí sola es puro ruido.
_RUTAS_TAREA_SOSPECHOSA = (
    "\\appdata\\local\\temp\\", "\\temp\\", "\\downloads\\",
    "\\users\\public\\", "\\windows\\temp\\",
)
_PATRONES_BYPASS_TAREA = (
    '-enc ', '-encodedcommand', '-executionpolicy bypass', '-ep bypass',
    '-windowstyle hidden', '-w hidden', 'invoke-expression', ' iex ', ' iex(',
    'downloadstring', 'downloadfile', 'mshta ', 'regsvr32 /u /s /i:',
    'certutil -decode', 'certutil -urlcache', 'frombase64string',
)

_ETIQUETAS_DISPARADOR = {
    "MSFT_TaskTimeTrigger": "Una vez",
    "MSFT_TaskDailyTrigger": "Diario",
    "MSFT_TaskWeeklyTrigger": "Semanal",
    "MSFT_TaskMonthlyTrigger": "Mensual",
    "MSFT_TaskMonthlyDOWTrigger": "Mensual",
    "MSFT_TaskLogonTrigger": "Al iniciar sesión",
    "MSFT_TaskBootTrigger": "Al arrancar el equipo",
    "MSFT_TaskIdleTrigger": "En inactividad",
    "MSFT_TaskEventTrigger": "Al ocurrir un evento del sistema",
    "MSFT_TaskRegistrationTrigger": "Al registrar/actualizar la tarea",
    "MSFT_TaskSessionStateChangeTrigger": "Cambio de estado de sesión",
}

# Solo estas clases tienen un HORARIO DE EJECUCIÓN real en StartBoundary. En
# el resto (inicio de sesión, arranque, inactividad, evento...) esa misma
# propiedad solo marca "a partir de cuándo está activo el desencadenador",
# NO la hora a la que se dispara — comprobado con datos reales: una tarea
# con LogonTrigger trae Hora='12:05' aunque en realidad se ejecute cada vez
# que alguien inicia sesión, a cualquier hora del día. Usar esa hora en el
# calendario de ejecución sería directamente falso.
_CLASES_CON_HORARIO_FIJO = {
    "MSFT_TaskTimeTrigger", "MSFT_TaskDailyTrigger", "MSFT_TaskWeeklyTrigger",
    "MSFT_TaskMonthlyTrigger", "MSFT_TaskMonthlyDOWTrigger",
}

_NOMBRES_DIA_CORTO = ["Lu", "Ma", "Mi", "Ju", "Vi", "Sá", "Do"]

# (bit del bitmask DaysOfWeek del Programador de tareas, día ISO 0=lunes..6=domingo)
_BITS_DIA_SEMANA = ((1, 6), (2, 0), (4, 1), (8, 2), (16, 3), (32, 4), (64, 5))


def _bitmask_a_dias_semana(bitmask) -> Optional[List[int]]:
    """Convierte el bitmask DaysOfWeek de un WeeklyTrigger a una lista de
    días ISO (0=lunes..6=domingo), ordenada; None si no aplica (bitmask
    vacío o no es un trigger semanal)."""
    if bitmask in (None, ""):
        return None
    try:
        bitmask = int(bitmask)
    except (TypeError, ValueError):
        return None
    dias = sorted(dia for bit, dia in _BITS_DIA_SEMANA if bitmask & bit)
    return dias or None


@dataclass
class ScheduledTaskTrigger:
    """Un desencadenador de una tarea programada, ya interpretado desde el
    dato crudo de PowerShell (ver PS_GET_SCHEDULED_TASKS en ps_scripts.py).
    Instanciar siempre vía scheduled_task_trigger_desde_ps(), nunca a mano
    con datos a medio traducir."""
    tipo: str
    hora: Optional[str] = None                # "HH:MM", solo si tiene horario real
    dias_semana: Optional[List[int]] = None    # 0=lunes..6=domingo; None = todos los días
    repite_cada_min: Optional[int] = None
    repite_durante_min: Optional[int] = None
    habilitado: bool = True

    @property
    def tiene_horario_fijo(self) -> bool:
        return self.hora is not None

    @property
    def texto(self) -> str:
        """Descripción legible completa, para tooltip/detalle."""
        if not self.tiene_horario_fijo:
            return self.tipo + ("" if self.habilitado else " (deshabilitado)")
        partes = [self.tipo]
        if self.dias_semana is not None and len(self.dias_semana) < 7:
            partes.append("(" + ", ".join(_NOMBRES_DIA_CORTO[d] for d in self.dias_semana) + ")")
        partes.append(f"a las {self.hora}")
        if self.repite_cada_min:
            extra = f"— repite cada {self.repite_cada_min} min"
            if self.repite_durante_min:
                extra += f" durante {self.repite_durante_min} min"
            partes.append(extra)
        if not self.habilitado:
            partes.append("(deshabilitado)")
        return " ".join(partes)


def scheduled_task_trigger_desde_ps(d: dict) -> ScheduledTaskTrigger:
    """Traduce un disparador crudo de PS_GET_SCHEDULED_TASKS (clase CIM,
    hora, bitmask de días, repetición) a un ScheduledTaskTrigger listo para
    mostrar en español."""
    clase = d.get("Clase") or ""
    tipo = _ETIQUETAS_DISPARADOR.get(clase, "Desconocido")
    repite_cada = d.get("RepiteCadaMin")
    repite_durante = d.get("RepiteDuranteMin")
    habilitado = bool(d.get("Habilitado", True))

    if clase not in _CLASES_CON_HORARIO_FIJO:
        return ScheduledTaskTrigger(
            tipo=tipo, hora=None, dias_semana=None,
            repite_cada_min=repite_cada, repite_durante_min=repite_durante,
            habilitado=habilitado,
        )

    hora = d.get("Hora") or None
    dias_semana = _bitmask_a_dias_semana(d.get("DiasSemanaBitmask"))

    if clase == "MSFT_TaskTimeTrigger":
        if repite_cada:
            # "Una vez" con repetición es en la práctica recurrente todos
            # los días desde su fecha de anclaje (a menudo antigua e
            # irrelevante, p. ej. la fecha de instalación del software) —
            # no un evento real de un solo día.
            tipo = "Recurrente"
            dias_semana = None
        elif d.get("FechaUnica"):
            try:
                dias_semana = [datetime.strptime(d["FechaUnica"], "%d/%m/%Y").weekday()]
            except Exception:
                dias_semana = None

    return ScheduledTaskTrigger(
        tipo=tipo, hora=hora, dias_semana=dias_semana,
        repite_cada_min=repite_cada, repite_durante_min=repite_durante,
        habilitado=habilitado,
    )


@dataclass
class ScheduledTaskRow:
    nombre: str
    estado: str
    proxima_ejecucion: str
    ultima_ejecucion: str
    ultimo_resultado: str
    ejecuta_como: str = ""
    oculta: bool = False
    autor: str = ""
    accion: str = ""
    ruta: str = ""
    ejecutable_principal: str = ""
    fecha_creacion: str = "N/A"
    disparadores: List[ScheduledTaskTrigger] = field(default_factory=list)

    @property
    def dias_desde_creacion(self) -> Optional[int]:
        """Días transcurridos desde el registro de la tarea (fecha_creacion,
        formato dd/MM/yyyy), o None si no se puede calcular — muchas tareas
        de Microsoft no traen esta fecha."""
        try:
            fecha_dt = datetime.strptime(self.fecha_creacion.strip(), "%d/%m/%Y")
            return (datetime.now() - fecha_dt).days
        except Exception:
            return None

    @property
    def motivo_sospecha(self) -> list:
        """Lista de motivos concretos, o vacía si no hay indicios. Basado en
        la ACCIÓN real de la tarea (ruta de ejecución, parámetros), no en
        'oculta' a secas — comprobado con datos reales: ~22% de las tareas
        de un equipo limpio están ocultas (.NET NGEN, licencias, tareas de
        mantenimiento de Microsoft...), así que ese único criterio era puro
        ruido, no una señal real (mismo error ya corregido con
        'genéricos/antiguos' de controladores). La ruta sospechosa se
        comprueba SOLO contra el ejecutable principal, no contra cualquier
        argumento — un argumento (p. ej. una ruta de log) que mencione
        'temp' no convierte la tarea en sospechosa por sí solo."""
        motivos = []
        ejecutable = (self.ejecutable_principal or "").lower()
        if any(p in ejecutable for p in _RUTAS_TAREA_SOSPECHOSA):
            motivos.append("El ejecutable de la tarea está en una carpeta temporal, de descargas o de usuario público")
        accion = (self.accion or "").lower()
        if any(p in accion for p in _PATRONES_BYPASS_TAREA):
            motivos.append("La acción contiene parámetros típicos de bypass/ofuscación de PowerShell u otro binario")
        # Corroboración, no disparador único: una tarea creada hace pocos
        # días es de lo más normal por sí sola (instalar software, Windows
        # Update...), así que solo cuenta como señal cuando YA hay otro
        # indicio real que reforzar — mismo criterio que 'oculta' más
        # arriba, para no meter ruido de un solo dato.
        if motivos:
            dias = self.dias_desde_creacion
            if dias is not None and dias <= 3:
                motivos.append(f"Creada hace {dias} día(s) — posible persistencia reciente")
        return motivos

    @property
    def es_sospechosa(self) -> bool:
        return bool(self.motivo_sospecha)

@dataclass
class PrinterRow:
    nombre: str
    tipo: str
    puerto: str
    estado: str
    compartida: str
    predeterminada: str

# Get-NetFirewallRule/Get-NetFirewallProfile devuelven los nombres de
# perfil en inglés SIEMPRE (son valores .NET estables, no texto localizado
# — ver el comentario de PS_GET_FIREWALL_RULES en ps_scripts.py), así que
# la traducción a español para mostrar en la UI se hace aquí, en Python,
# sin tocar el valor original (que sigue haciendo falta en inglés para
# los propios comandos Set-NetFirewallProfile/-Rule).
_TRADUCCION_PERFILES_FIREWALL = {
    "Domain": "Dominio",
    "Private": "Privado",
    "Public": "Público",
    "Any": "Cualquiera",
    "NotConfigured": "No configurado",
}


def traducir_perfil_firewall(valor: str) -> str:
    """Traduce al español un valor de perfil de firewall — puede venir
    combinado con comas (p. ej. 'Domain, Private, Public'), así que se
    traduce token a token."""
    if not valor:
        return valor
    partes = [p.strip() for p in valor.split(",")]
    return ", ".join(_TRADUCCION_PERFILES_FIREWALL.get(p, p) for p in partes)


@dataclass
class FirewallRuleRow:
    nombre: str
    direccion: str
    accion: str
    protocolo: str
    puerto: str
    perfil: str
    habilitada: str = "N/A"
    nombre_interno: str = ""  # Name (no DisplayName): único, para Set-NetFirewallRule
    es_sistema: bool = False  # Group con prefijo "@..." (cadena de recurso de
                               # una DLL del propio Windows) — heurística
                               # verificada empíricamente: 513/640 reglas reales.
    programa: str = "N/A"     # Get-NetFirewallApplicationFilter.Program, si la regla
                               # está atada a un ejecutable concreto (no todas lo están).

    @property
    def es_regla_amplia(self) -> bool:
        """Marca como 'amplia' una regla de entrada ACTIVA que permite
        Any/Any: sin restricción de puerto ni protocolo (Any/*/N/A todos
        cuentan como sin restricción real), el patrón de riesgo típico a
        auditar. Una regla deshabilitada no representa un riesgo activo,
        aunque esté configurada como amplia."""
        if self.habilitada.strip().lower() in ("no", "false"):
            return False
        if self.direccion != "Entrada" or self.accion != "Permitir":
            return False
        puerto = (self.puerto or "").strip().lower()
        protocolo = (self.protocolo or "").strip().lower()
        puerto_abierto = puerto in ("any", "*", "n/a", "")
        protocolo_abierto = protocolo in ("any", "*", "n/a", "")
        return puerto_abierto and protocolo_abierto

    @property
    def perfil_es(self) -> str:
        """Valor de 'perfil' traducido al español para mostrar en la UI —
        usar SIEMPRE 'perfil' (no esta propiedad) al construir comandos
        PowerShell."""
        return traducir_perfil_firewall(self.perfil)

@dataclass
class FirewallProfileRow:
    """Estado del Firewall de Windows por perfil (Dominio/Privado/Público)
    — el control más importante de todos: si un perfil está desactivado,
    ninguna regla de ese perfil importa."""
    nombre: str
    habilitado: bool

    @property
    def nombre_es(self) -> str:
        """Nombre del perfil traducido al español para mostrar en la UI —
        usar SIEMPRE 'nombre' (no esta propiedad) en comandos PowerShell
        como Set-NetFirewallProfile -Name."""
        return traducir_perfil_firewall(self.nombre)

# REMOVED - Browser Passwords
# @dataclass
# class BrowserPasswordRow:
#     navegador: str
#     url: str
#     usuario: str
#     contraseña: str
#     nota: str

@dataclass
class EmailAccountRow:
    cliente: str
    cuenta: str
    servidor: str
    tipo: str
    # Detalle completo por protocolo — no se muestra como columna (la
    # tabla sigue con las 4 de siempre), pero lo usan el diálogo "Ver
    # configuración" y los detectores de Soporte (autenticación básica a
    # punto de desaparecer, cuenta sin SSL/TLS...).
    imap_host: str = ""
    imap_port: int = 0
    imap_ssl: bool = False
    pop_host: str = ""
    pop_port: int = 0
    pop_ssl: bool = False
    smtp_host: str = ""
    smtp_port: int = 0
    smtp_ssl: bool = False

@dataclass
class BrowserCloudAccountRow:
    """Cuenta detectada en un navegador (Firefox/Chrome/Edge) o de
    almacenamiento en la nube (OneDrive) — separado de EmailAccountRow
    porque no son cuentas de correo, aunque compartan la misma forma de
    fila (mismo criterio que llevó a extraerlas de Cuentas de Correo a su
    propia pestaña Navegadores y Nube)."""
    cliente: str
    cuenta: str
    servidor: str
    tipo: str

_RUTAS_INICIO_SOSPECHOSA = (
    "\\appdata\\local\\temp\\", "\\temp\\", "\\downloads\\",
    "\\users\\public\\", "\\windows\\temp\\",
)

@dataclass
class StartupProgramRow:
    nombre: str
    comando: str
    ubicacion: str
    estado: str            # "Habilitado" / "Deshabilitado" — texto a mostrar
    hive: str               # "HKLM" / "HKCU" — para el toggle
    clave_origen: str       # "Run" / "Run32" / "StartupFolder" — para el toggle
    clave_valor: str        # nombre del valor de registro o del fichero — para el toggle
    existe_fichero: bool = True   # False = el ejecutable/acceso directo ya no existe en disco

    @property
    def motivo_sospecha(self) -> list:
        """Mismo criterio que ScheduledTaskRow: señales concretas, no
        'está deshabilitado' a secas (eso es normal y no indica nada malo
        por sí solo)."""
        motivos = []
        comando = (self.comando or "").lower()
        if any(p in comando for p in _RUTAS_INICIO_SOSPECHOSA):
            motivos.append("Se lanza desde una carpeta temporal, de descargas o de usuario público")
        if not self.existe_fichero:
            motivos.append("El fichero al que apunta esta entrada ya no existe en el disco (huérfana)")
        return motivos

    @property
    def es_sospechosa(self) -> bool:
        return bool(self.motivo_sospecha)

@dataclass
class GroupPolicyRow:
    ambito: str                  # "Equipo" o "Usuario"
    nombre: str                  # nombre de la GPO
    estado: str                  # "Aplicada" / "Deshabilitada" / "Acceso denegado"
    ou: str                      # OU / Sitio
    ultima_actualizacion: str

@dataclass
class ADInfoRow:
    tipo: str
    nombre: str
    sam_account_name: str
    habilitado: str
    ultima_conexion: str
    distinguished_name: str

@dataclass
class USBDeviceRow:
    nombre: str
    fabricante: str
    estado: str
    instance_id: str

@dataclass
class MonitorRow:
    nombre: str
    fabricante: str
    serial: str
    tipo_conexion: str  # HDMI, DisplayPort, VGA, DVI, USB Type-C, Thunderbolt

@dataclass
class InstalledUpdateRow:
    kb: str
    descripcion: str
    instalacion: str
    usuario: str

    @property
    def dias_instalado(self) -> Optional[int]:
        """Días transcurridos desde que se instaló (self.instalacion,
        formato dd/MM/yyyy), o None si no se puede calcular."""
        try:
            fecha_dt = datetime.strptime(self.instalacion.strip(), "%d/%m/%Y")
            return (datetime.now() - fecha_dt).days
        except Exception:
            return None

@dataclass
class PendingUpdateRow:
    kb: str
    titulo: str
    tamano: str
    criticidad: str
    fecha: str
    tipo: str = "Software"      # 'Software' o 'Controlador'
    update_id: str = ""         # Identity.UpdateID de Windows Update — para
                                 # instalar la actualización exacta sin tener
                                 # que volver a emparejar por título truncado.

    @property
    def dias_pendiente(self) -> Optional[int]:
        """Días transcurridos desde que la actualización está disponible
        (self.fecha, formato dd/MM/yyyy), o None si no se puede calcular."""
        try:
            fecha_dt = datetime.strptime(self.fecha.strip(), "%d/%m/%Y")
            return (datetime.now() - fecha_dt).days
        except Exception:
            return None

@dataclass
class DriverRow:
    dispositivo: str
    fabricante: str
    version: str
    fecha: str
    clase: str
    firmado: str
    estado: str
    proveedor: str
    problema: str = ""
    device_id: str = ""  # Device Instance ID (Win32_PnPEntity.DeviceID) —
                          # necesario para poder deshabilitar/habilitar el
                          # dispositivo vía pnputil; vacío si WMI no lo dio.

    @property
    def dias_antiguedad(self) -> Optional[int]:
        """Días transcurridos desde la fecha del controlador (self.fecha,
        formato dd/MM/yyyy), o None si no se puede calcular. Puramente
        informativo — un controlador antiguo no implica un problema real
        (ver 'problema', que sí lo es); antes se usaba esta antigüedad para
        marcar 'Obsoleto' en Estado, dando falsos positivos con
        controladores de chipset u otros que llevan años sin cambiar sin
        que eso sea un fallo."""
        try:
            fecha_dt = datetime.strptime(self.fecha.strip(), "%d/%m/%Y")
            return (datetime.now() - fecha_dt).days
        except Exception:
            return None


def _controlador_generico(row: "DriverRow") -> bool:
    """True si el PROVEEDOR del controlador (quién lo escribió,
    Win32_PnPSignedDriver.DriverProviderName) es Microsoft pero el
    FABRICANTE del hardware (Win32_PnPEntity.Manufacturer) es otro —
    señal fiable de que Windows está usando su controlador genérico de
    caja en vez del específico del fabricante. Si el propio fabricante ES
    Microsoft (p. ej. hardware Surface), no cuenta como 'genérico
    sustituyendo al específico' — es lo esperado. Sin datos suficientes
    (fabricante 'Desconocido' o vacío) no se afirma nada, por prudencia."""
    proveedor = (row.proveedor or "").strip().lower()
    fabricante = (row.fabricante or "").strip().lower()
    if not proveedor.startswith("microsoft"):
        return False
    if not fabricante or fabricante in ("desconocido", "unknown", "n/a"):
        return False
    return not fabricante.startswith("microsoft")

# Clases de dispositivo donde deshabilitar/reiniciar vía Soporte está
# BLOQUEADO por seguridad, no solo advertido: un error aquí puede dejar el
# equipo sin arrancar (disco, sistema) o cortar el acceso remoto con el que
# un técnico podría estar trabajando (red) — a diferencia de un servicio o
# proceso colgado, esto puede no tener vuelta atrás sin acceso físico.
_CLASES_DISPOSITIVO_SIN_REINICIO = {
    "sistema", "procesador", "equipo", "unidades de disco", "volumen",
    "red", "red (transporte)", "red (cliente)", "red (servicio)",
}


def _dispositivo_reinicio_permitido(row: "DriverRow") -> bool:
    return bool(row.device_id) and (row.clase or "").strip().lower() not in _CLASES_DISPOSITIVO_SIN_REINICIO

@dataclass
class RegistryResultRow:
    """Una fila de resultado del Registro de Windows: puede ser una
    coincidencia de búsqueda (match_type 'key'/'value') o una entrada
    detectada como rota/huérfana por el escáner (match_type 'huerfana'),
    en cuyo caso 'motivo' explica por qué se considera rota."""
    hive_abbr: str
    hive_full: str
    hive_key: int          # constante winreg.HKEY_* (para reabrir la clave)
    subkey: str
    match_type: str        # 'key' | 'value' | 'huerfana'
    value_name: str = ""
    value_data: str = ""
    reg_type: str = ""
    reg_type_int: Optional[int] = None
    motivo: str = ""        # solo para 'huerfana': qué referencia está rota

    @property
    def full_path(self) -> str:
        return f"{self.hive_abbr}\\{self.subkey}" if self.subkey else self.hive_abbr

    @property
    def riesgo(self) -> Optional[str]:
        return clasificar_riesgo_registro(self.hive_abbr, self.subkey)

    @property
    def value_data_mostrado(self) -> str:
        if self.match_type == "huerfana" and self.motivo:
            return f"{self.value_data}  ⚠ {self.motivo}"
        return self.value_data


@dataclass
class UserRow:
    nombre: str
    nombre_completo: str
    tipo: str  # Local, Dominio, Sistema, Sistema (Local)
    dominio: str
    estado: str  # Activo, Inactivo
    ultima_conexion: str
    fecha_creacion: str
    descripcion: str
    grupos: str
    sid: str
    password_expires: str
    password_required: str
    cuenta_vinculada: str = ""  # "Local", "Microsoft (email)", "Entra ID (Azure AD)", "Dominio"...

@dataclass
class NetworkAdapterRow:
    nombre: str
    descripcion: str
    estado: str  # Activo, Inactivo, Deshabilitado
    tipo: str  # WiFi, Ethernet, VPN, Virtual, Bluetooth, Otro
    ip: str
    mac: str
    configuracion_ip: str  # DHCP o Fija
    gateway: str
    dns: str
    ipv6: str
    velocidad: str

@dataclass
class ActiveSessionRow:
    usuario: str
    sesion: str
    id: str
    estado: str
    tiempo_inactivo: str
    inicio: str
    tipo: str
    origen_ip: str = ""  # IP de origen (solo RDP) correlacionada con el
                          # histórico de conexiones (evento 1149) — vacío
                          # si no es RDP o no se encontró un evento reciente
                          # de ese usuario.

@dataclass
class RDPConnectionRow:
    """Histórico de conexiones RDP autenticadas (evento 1149 de
    Microsoft-Windows-TerminalServices-RemoteConnectionManager/Operational)
    — a diferencia de ActiveSessionRow, esto es un HISTÓRICO, no un
    snapshot: incluye conexiones ya finalizadas."""
    usuario: str
    dominio: str
    ip_origen: str
    fecha: str

@dataclass
class BluetoothRow:
    nombre: str
    fabricante: str
    estado: str
    clase: str
    instance_id: str

_PATRONES_BYPASS_PS = (
    '-enc ', '-encodedcommand', '-executionpolicy bypass', '-ep bypass',
    '-windowstyle hidden', '-w hidden', 'invoke-expression', ' iex ', ' iex(',
    'downloadstring', 'downloadfile', 'mshta ', 'regsvr32 /u /s /i:',
    'certutil -decode', 'certutil -urlcache', 'frombase64string',
    'net.webclient', 'disablerealtimemonitoring', 'add-mppreference -exclusionpath',
    '-noprofile -noninteractive -windowstyle',
)

# Clasificación por TIPO de comando (independiente de si es sospechoso o
# no) — cada categoría es una pista sobre qué está haciendo el comando, no
# un juicio de riesgo. Varias pueden solaparse (p. ej. un cmdlet nativo que
# además descarga algo), no son mutuamente excluyentes a propósito.
_PATRON_SCRIPT_LOCAL = re.compile(
    r'(?:^|[\s&|;])\.[\\/][^\s"\']*\.(ps1|bat|cmd|exe)\b', re.IGNORECASE)
_PATRONES_GESTOR_PAQUETES = (
    'pip ', 'pip.exe', 'pip3 ', 'npm ', 'npm.cmd', 'yarn ', 'choco ', 'cinst ',
    'winget ', 'install-module', 'install-package', 'install-script',
)
_PATRONES_DESCARGA_RED = (
    'invoke-webrequest', 'invoke-restmethod', 'iwr ', 'iwr(', 'irm ', 'irm(',
    'curl ', 'curl.exe', 'wget ', 'start-bitstransfer',
)
_PATRON_CMDLET_ADMIN = re.compile(
    r'(?:^|[\s|;&(])(get|set|new|remove|enable|disable|start|stop|restart)-[a-z]+\b',
    re.IGNORECASE)


@dataclass
class PSHistoryRow:
    numero: str
    comando: str
    fecha_hora: str = "Desconocida"
    origen: str = "Historial local (PSReadLine)"

    @property
    def motivo_sospecha(self) -> list:
        """Lista de motivos concretos (parámetros de bypass/ofuscación de
        PowerShell u otro binario), o vacía si no hay indicios. No marca
        '.\\script.ps1' por sí solo: invocar un script local así es
        completamente normal y mayoritario en cualquier historial — el
        indicio real es forzar la política de ejecución (-ExecutionPolicy
        Bypass), no la sintaxis de invocación en sí."""
        cmd = (self.comando or "").lower()
        return [p for p in _PATRONES_BYPASS_PS if p in cmd]

    @property
    def es_sospechoso(self) -> bool:
        return bool(self.motivo_sospecha)

    @property
    def es_script_local(self) -> bool:
        """Invoca un script/ejecutable local por ruta relativa (.\\algo.ps1,
        ./algo.sh...), no un cmdlet ni un binario del PATH."""
        return bool(_PATRON_SCRIPT_LOCAL.search(self.comando or ""))

    @property
    def es_gestor_paquetes(self) -> bool:
        """pip, npm, choco, winget, Install-Module/-Package/-Script..."""
        cmd = (self.comando or "").lower()
        return any(p in cmd for p in _PATRONES_GESTOR_PAQUETES)

    @property
    def es_descarga_red(self) -> bool:
        """Invoke-WebRequest/-RestMethod (y sus alias iwr/irm), curl, wget,
        Start-BitsTransfer — el comando trae algo de la red."""
        cmd = (self.comando or "").lower()
        return any(p in cmd for p in _PATRONES_DESCARGA_RED)

    @property
    def es_cmdlet_admin(self) -> bool:
        """Cmdlet nativo Verbo-Nombre de administración (Get-/Set-/New-/
        Remove-/Enable-/Disable-/Start-/Stop-/Restart-) — deliberadamente
        SIN 'Install-' (esa cae en gestor de paquetes, no aquí)."""
        return bool(_PATRON_CMDLET_ADMIN.search(self.comando or ""))

@dataclass
class SystemEventRow:
    fecha: str
    nivel: str
    log: str
    fuente: str
    id_evento: str
    mensaje: str

# (fuente, id_evento) en minúsculas -> motivo, para las combinaciones más
# conocidas y estables de "apagado inesperado / pantalla azul" — no una
# lista exhaustiva de eventos críticos, solo las señales más fiables y
# documentadas que merece la pena destacar entre el ruido de la tabla.
_PATRONES_EVENTOS_CRITICOS = {
    ("microsoft-windows-kernel-power", "41"):
        "Apagado inesperado del sistema — posible corte de luz, cuelgue total o botón de "
        "encendido mantenido pulsado",
    ("eventlog", "6008"):
        "El sistema no se apagó correctamente la vez anterior",
    ("microsoft-windows-wer-systemerrorreporting", "1001"):
        "Pantalla azul (BSOD) registrada por Windows",
}


def _evento_critico_conocido(row: "SystemEventRow") -> str:
    """Devuelve el motivo si (fuente, id_evento) coincide con uno de los
    patrones conocidos, o '' si no aplica."""
    clave = ((row.fuente or "").strip().lower(), (row.id_evento or "").strip())
    return _PATRONES_EVENTOS_CRITICOS.get(clave, "")

@dataclass
class RemoteConnectionRow:
    fecha: str
    usuario: str
    ip_origen: str
    tipo_acceso: str   # RDP, Red/SMB, RDP (Auth)
    resultado: str     # Exito, Fallo
    workstation: str
    proceso: str
    id_evento: str

@dataclass
class PartitionRow:
    disco: str
    letra: str
    etiqueta: str
    sistema: str
    tamano: str
    usado: str
    libre: str
    porcentaje: str

@dataclass
class DiscoFisicoRow:
    """Inventario + salud S.M.A.R.T. de un disco físico en una sola fila
    (antes eran dos pestañas separadas — Discos y Salud de Disco — con la
    misma granularidad 1:1 por disco, así que fusionarlas en una tabla no
    pierde información, solo evita cambiar de pestaña para verla)."""
    disco: str
    modelo: str
    tipo: str
    bus: str
    tamano: str
    serial: str
    tipo_medio: str
    estado_salud: str
    estado_operativo: str
    temperatura: str = "N/A"
    desgaste: str = "N/A"
    horas_encendido: str = "N/A"
    errores: str = "N/A"

@dataclass
class GPURow:
    nombre: str
    ram: str
    driver: str
    resolucion: str

@dataclass
class PackageUpgradeRow:
    nombre: str
    id_paquete: str
    version_actual: str
    version_disponible: str
    origen: str
    bloqueado: bool = False
    motivo_bloqueo: str = ""

# OUI (primeros 3 octetos de la MAC) de fabricantes de MUY alta confianza —
# NO es la base de datos completa del IEEE (30.000+ entradas registradas,
# exigiría descargar/embeber y mantener actualizado un fichero externo que
# este proyecto no tiene forma de garantizar). Se limita a software de
# virtualización (OUI fijo y muy estable, cero ambigüedad) y hardware
# maker/IoT extremadamente común en redes domésticas y de oficina pequeña
# (Raspberry Pi, ESP32/ESP8266). Cualquier MAC fuera de esta lista se
# marca 'Desconocido' — nunca se adivina un fabricante sin certeza.
_OUI_FABRICANTES = {
    "000C29": "VMware (máquina virtual)",
    "000569": "VMware (máquina virtual)",
    "001C14": "VMware (máquina virtual)",
    "005056": "VMware (máquina virtual)",
    "080027": "Oracle VirtualBox (máquina virtual)",
    "00155D": "Microsoft Hyper-V (máquina virtual)",
    "001C42": "Parallels (máquina virtual)",
    "B827EB": "Raspberry Pi Foundation",
    "DCA632": "Raspberry Pi Trading",
    "E45F01": "Raspberry Pi Trading",
    "28CDC1": "Raspberry Pi Trading",
    "D83ADD": "Raspberry Pi Trading",
    "240AC4": "Espressif (ESP32/ESP8266 — IoT)",
    "30AEA4": "Espressif (ESP32/ESP8266 — IoT)",
    "3C71BF": "Espressif (ESP32/ESP8266 — IoT)",
    "CC50E3": "Espressif (ESP32/ESP8266 — IoT)",
    "A4CF12": "Espressif (ESP32/ESP8266 — IoT)",
    "84F3EB": "Espressif (ESP32/ESP8266 — IoT)",
}


def identificar_fabricante_mac(mac: str) -> str:
    """Fabricante a partir del OUI (3 primeros octetos) de la MAC — lista
    reducida y de alta confianza (ver _OUI_FABRICANTES), NO la base de
    datos completa del IEEE. 'Desconocido' es el resultado honesto para
    cualquier MAC fuera de esta lista corta, no un fallo de la consulta."""
    if not mac:
        return "Desconocido"
    oui = re.sub(r'[:\-.]', '', mac).upper()[:6]
    if oui in _OUI_FABRICANTES:
        return _OUI_FABRICANTES[oui]
    # 5.5.0: registro público completo del IEEE (msys/datos/oui_ieee.tsv.gz, 40.000 fabricantes). Antes
    # solo había una lista corta y en la red de pruebas salía «Desconocido» en 22 de 22 dispositivos.
    nombre = _oui_ieee().get(oui)
    if nombre:
        return nombre
    # Segundo dígito 2, 6, A o E = dirección «administrada localmente»: móviles y Windows la ponen al azar
    # por privacidad (no tiene fabricante en ningún registro).
    if len(oui) >= 2 and oui[1] in "26AE":
        return "MAC privada (aleatoria)"
    return "Desconocido"


_OUI_IEEE_CACHE = None


def _oui_ieee() -> dict:
    """Carga (una vez) el registro MA-L del IEEE empaquetado con MASys. Si falta, diccionario vacío."""
    global _OUI_IEEE_CACHE
    if _OUI_IEEE_CACHE is None:
        _OUI_IEEE_CACHE = {}
        try:
            import gzip
            from msys import config as _cfg
            ruta = _cfg.ruta_recurso("msys", "datos", "oui_ieee.tsv.gz")
            with gzip.open(ruta, "rt", encoding="utf-8") as f:
                for linea in f:
                    if linea and not linea.startswith("#"):
                        clave, _sep, nombre = linea.rstrip("\n").partition("\t")
                        if clave and nombre:
                            _OUI_IEEE_CACHE[clave] = nombre
        except Exception:
            pass
    return _OUI_IEEE_CACHE


# Patrones de hostname -> tipo de dispositivo. Basado en texto asignado
# por humanos (nombre de equipo/router), no en un código exacto — mucho
# más fiable que adivinar por MAC, pero solo aplica una vez resuelto el
# nombre (no automático en la carga inicial, ver _resolver_nombre_red_local).
_PATRONES_TIPO_DISPOSITIVO = [
    (r'iphone|ipad', 'Móvil/Tablet (Apple)'),
    (r'android|galaxy|xiaomi|redmi|huawei|oneplus|pixel', 'Móvil/Tablet (Android)'),
    (r'printer|impresora|\bhp\b|canon|epson|brother|kyocera|xerox', 'Impresora'),
    (r'\bnas\b|synology|qnap', 'NAS (almacenamiento en red)'),
    (r'roku|chromecast|firetv|fire-tv|appletv|apple-tv|androidtv|android-tv|\btv\b', 'TV / Media'),
    (r'echo|alexa|google-home|nest|homepod', 'Asistente / Smart Home'),
    (r'router|gateway|\bap-|access-?point|mikrotik|ubnt|ubiquiti', 'Router / Equipo de red'),
    (r'raspberrypi|raspberry-pi', 'Raspberry Pi'),
    (r'esp32|esp8266|espressif', 'Dispositivo IoT (ESP32/ESP8266)'),
    (r'desktop-|laptop-|\bpc-', 'Equipo Windows'),
]


def identificar_tipo_por_hostname(hostname: str) -> str:
    """Heurística de tipo de dispositivo a partir del hostname resuelto —
    texto asignado por una persona/fabricante, así que es un patrón más
    fiable que intentar adivinar por MAC. Vacío/sin coincidencia -> 'N/A',
    nunca se afirma un tipo sin una coincidencia real de patrón."""
    if not hostname:
        return "N/A"
    texto = hostname.lower()
    for patron, tipo in _PATRONES_TIPO_DISPOSITIVO:
        if re.search(patron, texto):
            return tipo
    return "N/A"


@dataclass
class NetworkDeviceRow:
    ip: str
    mac: str
    estado: str
    interfaz: str
    fabricante: str = "Desconocido"
    hostname: str = ""
    acceso_web: str = ""
    sospechoso: bool = False

    @property
    def tipo_estimado(self) -> str:
        """Combina fabricante (fiable: virtualización/Raspberry Pi/ESP32) y
        hostname (una vez resuelto) para una estimación de tipo de
        dispositivo — nunca sustituye a mirar el propio dispositivo, es
        solo una pista de partida para el técnico."""
        if "máquina virtual" in self.fabricante:
            return "Máquina Virtual"
        if "Raspberry Pi" in self.fabricante or "ESP32" in self.fabricante:
            return self.fabricante
        return identificar_tipo_por_hostname(self.hostname)

