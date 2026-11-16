# -*- coding: utf-8 -*-
"""Motor (sin Qt) de la sección 🧩 Características de Windows.

Windows reparte esto en dos sitios que el usuario ve como uno solo:
- Características opcionales («Activar o desactivar las características de
  Windows»): Win32_OptionalFeature (rápido: ~4 s; Get-WindowsOptionalFeature
  tarda ~18 s y da lo mismo salvo estados «pendiente», que se cubren con la
  marca de reinicio pendiente).
- Capacidades («Características opcionales» de Configuración: RSAT, OpenSSH,
  idiomas, controladores…): Get-WindowsCapability (necesita administrador).

Aquí se juntan, con nombre en español, categoría, aviso de seguridad y
perfiles (varias de golpe). Activar/quitar usa los cmdlets oficiales con
-NoRestart y devuelve si Windows pide reiniciar."""

import os
import re
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from msys.core.utils import escape_powershell_string, is_admin, run_powershell_json

TIPO_CARACTERISTICA = "Característica"
TIPO_CAPACIDAD = "Capacidad"

ACTIVADA = "Activada"
DESACTIVADA = "Desactivada"
NO_DISPONIBLE = "No disponible"
PENDIENTE = "Pendiente de reinicio"

CAT_VIRTUAL = "Virtualización"
CAT_RED = "Red y protocolos"
CAT_NET = ".NET"
CAT_ADMIN = "Herramientas de administración"
CAT_IMPRESION = "Impresión y documentos"
CAT_MULTIMEDIA = "Multimedia"
CAT_HEREDADO = "Componentes antiguos"
CAT_IDIOMAS = "Idiomas"
CAT_CONTROLADORES = "Controladores"
CAT_SCRIPT = "PowerShell y scripts"
CAT_OTROS = "Otros"


@dataclass
class Caracteristica:
    tipo: str
    tecnico: str        # nombre que entiende DISM/PowerShell
    nombre: str         # en español
    estado: str
    categoria: str
    aviso: str = ""     # aviso de seguridad (solo si está activada)


# ── nombres en español de las capacidades más habituales ────────────────────
_RSAT = {
    "ActiveDirectory.DS-LDS.Tools": "Active Directory (usuarios, equipos, LDS)", "BitLocker.Recovery.Tools": "Recuperación de BitLocker",
    "CertificateServices.Tools": "Servicios de certificados", "DHCP.Tools": "DHCP", "Dns.Tools": "DNS",
    "FailoverCluster.Management.Tools": "Clúster de conmutación por error", "FileServices.Tools": "Servicios de archivos",
    "GroupPolicy.Management.Tools": "Directivas de grupo (GPMC)", "IPAM.Client.Tools": "IPAM", "LLDP.Tools": "LLDP",
    "NetworkController.Tools": "Controladora de red", "NetworkLoadBalancing.Tools": "Equilibrio de carga de red",
    "RemoteAccess.Management.Tools": "Acceso remoto", "RemoteDesktop.Services.Tools": "Servicios de Escritorio remoto",
    "ServerManager.Tools": "Administrador del servidor", "StorageMigrationService.Management.Tools": "Migración de almacenamiento",
    "StorageReplica.Tools": "Réplica de almacenamiento", "SystemInsights.Management.Tools": "System Insights",
    "VolumeActivation.Tools": "Activación por volumen", "WSUS.Tools": "WSUS", "AzureStack.HCI.Management.Tools": "Azure Stack HCI",
}
_CAPACIDADES = {
    "Accessibility.Braille": ("Braille (accesibilidad)", CAT_OTROS),
    "App.StepsRecorder": ("Grabación de acciones de usuario", CAT_ADMIN),
    "App.WirelessDisplay.Connect": ("Pantalla inalámbrica", CAT_MULTIMEDIA),
    "AzureArcSetup": ("Configuración de Azure Arc", CAT_ADMIN),
    "Browser.InternetExplorer": ("Internet Explorer (modo heredado)", CAT_HEREDADO),
    "DirectX.Configuration.Database": ("Base de datos de configuración de DirectX", CAT_MULTIMEDIA),
    "MathRecognizer": ("Reconocimiento de expresiones matemáticas", CAT_OTROS),
    "Media.WindowsMediaPlayer": ("Windows Media Player heredado", CAT_MULTIMEDIA),
    "Microsoft.Onecore.StorageManagement": ("Administración de almacenamiento (núcleo)", CAT_ADMIN),
    "Microsoft.Windows.StorageManagement": ("Administración de almacenamiento", CAT_ADMIN),
    "Microsoft.Wallpapers.Extended": ("Fondos de pantalla adicionales", CAT_OTROS),
    "Microsoft.WebDriver": ("WebDriver de Microsoft Edge", CAT_ADMIN),
    "Microsoft.Windows.Console.Legacy": ("Consola heredada", CAT_HEREDADO),
    "Microsoft.Windows.Notepad.System": ("Bloc de notas (sistema)", CAT_OTROS),
    "Microsoft.Windows.Notepad": ("Bloc de notas", CAT_OTROS),
    "Microsoft.Windows.MSPaint": ("Paint", CAT_OTROS),
    "Microsoft.Windows.WordPad": ("WordPad", CAT_HEREDADO),
    "Microsoft.Windows.PowerShell.ISE": ("PowerShell ISE", CAT_SCRIPT),
    "Microsoft.Windows.Sense.Client": ("Microsoft Defender para Endpoint (cliente)", CAT_ADMIN),
    "Msix.PackagingTool.Driver": ("Controlador de MSIX Packaging Tool", CAT_ADMIN),
    "NetFX3": (".NET Framework 3.5", CAT_NET),
    "Network.Irda": ("Infrarrojos (IrDA)", CAT_HEREDADO),
    "OneCoreUAP.OneSync": ("Sincronización de correo, calendario y contactos", CAT_OTROS),
    "OpenSSH.Client": ("Cliente OpenSSH", CAT_RED),
    "OpenSSH.Server": ("Servidor OpenSSH", CAT_RED),
    "Print.Fax.Scan": ("Fax y Escáner de Windows", CAT_IMPRESION),
    "Print.Management.Console": ("Administración de impresión", CAT_IMPRESION),
    "RasCMAK.Client": ("Kit de Connection Manager (RAS)", CAT_RED),
    "RIP.Listener": ("Escucha RIP", CAT_RED),
    "SNMP.Client": ("Protocolo SNMP", CAT_RED),
    "Tools.DeveloperMode.Core": ("Modo de programador", CAT_ADMIN),
    "Tools.Graphics.DirectX": ("Herramientas gráficas de DirectX", CAT_MULTIMEDIA),
    "Tpm.TpmDiagnostics": ("Diagnóstico del TPM", CAT_ADMIN),
    "VBSCRIPT": ("VBScript (Microsoft lo está retirando)", CAT_SCRIPT),
    "WMIC": ("WMIC (Microsoft lo está retirando)", CAT_SCRIPT),
    "Windows.Desktop.EMS-SAC.Tools": ("Consola de administración especial (EMS)", CAT_ADMIN),
    "Windows.Kernel.LA57": ("Núcleo con paginación de 5 niveles", CAT_OTROS),
    "WMI-SNMP-Provider.Client": ("Proveedor SNMP de WMI", CAT_RED),
    "XPS.Viewer": ("Visor de XPS", CAT_IMPRESION),
}
_IDIOMA = {"Basic": "escritura y ortografía", "Handwriting": "escritura a mano", "OCR": "reconocimiento de texto (OCR)",
           "Speech": "reconocimiento de voz", "TextToSpeech": "lectura en voz alta"}


def _base_capacidad(tecnico: str) -> Tuple[str, str]:
    """'Language.Speech~~~es-ES~0.0.1.0' -> ('Language.Speech', 'es-ES')."""
    partes = tecnico.split("~")
    base = partes[0]
    idioma = partes[3] if len(partes) > 3 else ""
    return base, idioma


def nombre_capacidad(tecnico: str) -> Tuple[str, str]:
    """(nombre en español, categoría) de una capacidad."""
    base, idioma = _base_capacidad(tecnico)
    if base in _CAPACIDADES:
        return _CAPACIDADES[base]
    if base.startswith("Rsat."):
        resto = base[5:]
        return f"RSAT: {_RSAT.get(resto, resto)}", CAT_ADMIN
    if base.startswith("Language.Fonts.") or base.startswith("Microsoft.LanguageFeatures"):
        return f"Fuentes de idioma: {base.split('.')[-1]}", CAT_IDIOMAS
    if base.startswith("Language."):
        tipo = base.split(".", 1)[1]
        return f"Idioma {idioma}: {_IDIOMA.get(tipo, tipo)}", CAT_IDIOMAS
    m = re.match(r"Microsoft\.Windows\.(Wifi|Ethernet)\.Client\.(\w+)\.(\w+)", base)
    if m:
        return f"Controlador {'Wi-Fi' if m.group(1) == 'Wifi' else 'Ethernet'} {m.group(2)} {m.group(3)}", CAT_CONTROLADORES
    if base.startswith("Hello.Face"):
        return "Windows Hello: reconocimiento facial", CAT_OTROS
    return base, CAT_OTROS


def categoria_caracteristica(nombre: str) -> str:
    n = nombre.lower()
    reglas = [
        (("hyper-v", "virtualmachineplatform", "hypervisorplatform", "containers", "subsystem-linux", "guardedhost"), CAT_VIRTUAL),
        (("netfx", "wcf-"), CAT_NET),
        (("smb1", "directplay", "legacycomponents", "internet-explorer", "mediaplayback", "windowsmediaplayer"), CAT_HEREDADO),
        (("iis-", "was-", "smb", "telnet", "tftp", "simpletcp", "msmq", "nfs", "workfolders", "rasrip", "datacenterbridging"), CAT_RED),
        (("printing", "xps", "fax"), CAT_IMPRESION),
        (("powershell",), CAT_SCRIPT),
        (("media", "windowsmedia"), CAT_MULTIMEDIA),
        (("rsat", "management", "remoteserver"), CAT_ADMIN),
    ]
    for claves, cat in reglas:
        if any(c in n for c in claves):
            return cat
    return CAT_OTROS


# ── avisos de seguridad (solo si está activada / instalada) ─────────────────
AVISOS = [
    ("smb1protocol", "SMB1 es inseguro (lo usaron WannaCry y NotPetya): quítalo salvo que lo exija un aparato antiguo"),
    ("microsoftwindowspowershellv2", "PowerShell 2.0 permite saltarse el registro y AMSI: quítalo"),
    ("telnetclient", "Telnet envía todo sin cifrar, contraseñas incluidas"),
    ("tftp", "TFTP no cifra ni pide contraseña"),
    ("simpletcp", "Servicios TCP simples (eco, chargen): no hacen falta y se pueden usar para ataques"),
    ("iis-webserverrole", "Hay un servidor web activo en este equipo"),
    ("openssh.server", "Servidor SSH activo: revisa quién puede entrar"),
    ("browser.internetexplorer", "Internet Explorer ya no recibe parches de seguridad"),
]


def aviso_de(tecnico: str, activa: bool) -> str:
    if not activa:
        return ""
    t = tecnico.lower()
    return next((texto for clave, texto in AVISOS if t.startswith(clave)), "")


# ── lectura ─────────────────────────────────────────────────────────────────
_ESTADO_CIM = {1: ACTIVADA, 2: DESACTIVADA, 3: NO_DISPONIBLE}
_ESTADO_CAP = {"Installed": ACTIVADA, "NotPresent": DESACTIVADA, "Staged": DESACTIVADA,
               "InstallPending": PENDIENTE, "UninstallPending": PENDIENTE, "PartiallyInstalled": PENDIENTE}


def _lista(datos) -> list:
    if datos is None:
        return []
    return datos if isinstance(datos, list) else [datos]


def leer_caracteristicas() -> List[Caracteristica]:
    datos = run_powershell_json(
        "Get-CimInstance Win32_OptionalFeature | Select-Object Name,Caption,InstallState | ConvertTo-Json -Compress", timeout=120)
    out = []
    for d in _lista(datos):
        nombre = d.get("Name") or ""
        if not nombre:
            continue
        estado = _ESTADO_CIM.get(d.get("InstallState"), DESACTIVADA)
        out.append(Caracteristica(TIPO_CARACTERISTICA, nombre, d.get("Caption") or nombre, estado,
                                  categoria_caracteristica(nombre), aviso_de(nombre, estado == ACTIVADA)))
    return out


def leer_capacidades() -> List[Caracteristica]:
    """Necesita administrador; sin él devuelve [] (la interfaz lo explica)."""
    if not is_admin():
        return []
    datos = run_powershell_json(
        "Get-WindowsCapability -Online | Select-Object Name,@{n='State';e={[string]$_.State}} | ConvertTo-Json -Compress",
        timeout=300)
    out = []
    for d in _lista(datos):
        tecnico = d.get("Name") or ""
        if not tecnico:
            continue
        estado = _ESTADO_CAP.get(d.get("State"), DESACTIVADA)
        nombre, cat = nombre_capacidad(tecnico)
        out.append(Caracteristica(TIPO_CAPACIDAD, tecnico, nombre, estado, cat, aviso_de(tecnico, estado == ACTIVADA)))
    return out


def reinicio_pendiente() -> bool:
    import winreg
    for clave in (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending",
                  r"SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired"):
        try:
            winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, clave, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY).Close()
            return True
        except OSError:
            continue
    return False


def leer_todo() -> Dict[str, object]:
    caracteristicas = leer_caracteristicas()
    capacidades = leer_capacidades()
    return {"lista": caracteristicas + capacidades, "capacidades_leidas": is_admin(), "reinicio": reinicio_pendiente()}


# ── cambios ─────────────────────────────────────────────────────────────────
_PS_CAMBIO = """
try {{
    $r = {orden} -ErrorAction Stop
    @{{ ok = $true; reinicio = [bool]$r.RestartNeeded }} | ConvertTo-Json -Compress
}} catch {{
    @{{ ok = $false; error = $_.Exception.Message }} | ConvertTo-Json -Compress
}}
"""


def cambiar(c: Caracteristica, activar: bool, origen: str = "") -> Tuple[bool, bool, str]:
    """(ok, pide_reinicio, mensaje). origen: carpeta \\sources\\sxs para .NET 3.5 sin internet."""
    if not is_admin():
        return False, False, "Hace falta ejecutar MASys como administrador"
    n = escape_powershell_string(c.tecnico)
    if c.tipo == TIPO_CARACTERISTICA:
        if activar:
            extra = f" -Source '{escape_powershell_string(origen)}' -LimitAccess" if origen else ""
            orden = f"Enable-WindowsOptionalFeature -Online -FeatureName '{n}' -All -NoRestart{extra}"
        else:
            orden = f"Disable-WindowsOptionalFeature -Online -FeatureName '{n}' -NoRestart"
    else:
        if activar:
            extra = f" -Source '{escape_powershell_string(origen)}' -LimitAccess" if origen else ""
            orden = f"Add-WindowsCapability -Online -Name '{n}'{extra}"
        else:
            orden = f"Remove-WindowsCapability -Online -Name '{n}'"
    r = run_powershell_json(_PS_CAMBIO.format(orden=orden), timeout=3600)
    if not isinstance(r, dict):
        return False, False, "PowerShell no devolvió respuesta (¿se agotó el tiempo?)"
    if not r.get("ok"):
        return False, False, str(r.get("error") or "Error desconocido")
    return True, bool(r.get("reinicio")), ""


def origen_netfx3_valido(carpeta: str) -> bool:
    """La carpeta \\sources\\sxs del medio de instalación de Windows tiene el paquete de .NET 3.5."""
    try:
        return any(f.lower().startswith("microsoft-windows-netfx3-ondemand-package") and f.lower().endswith(".cab")
                   for f in os.listdir(carpeta))
    except OSError:
        return False


# ── perfiles ────────────────────────────────────────────────────────────────
@dataclass
class Perfil:
    nombre: str
    descripcion: str
    activar: List[str] = field(default_factory=list)    # técnicos (prefijo para capacidades)
    quitar: List[str] = field(default_factory=list)


PERFILES = [
    Perfil("🛠️ Puesto de soporte IT", "Herramientas para administrar dominio, DNS, DHCP y equipos remotos",
           activar=["Rsat.ActiveDirectory.DS-LDS.Tools", "Rsat.GroupPolicy.Management.Tools", "Rsat.Dns.Tools",
                    "Rsat.DHCP.Tools", "Rsat.ServerManager.Tools", "Rsat.BitLocker.Recovery.Tools", "OpenSSH.Client",
                    "Print.Management.Console"]),
    Perfil("💻 Desarrollo y máquinas virtuales", "WSL, plataforma de máquinas virtuales, Hyper-V y Espacio aislado",
           activar=["Microsoft-Windows-Subsystem-Linux", "VirtualMachinePlatform", "Microsoft-Hyper-V-All",
                    "Containers-DisposableClientVM", "Tools.DeveloperMode.Core"]),
    Perfil("🛡️ Endurecer el equipo", "Quita protocolos y componentes antiguos que facilitan ataques",
           quitar=["SMB1Protocol", "SMB1Protocol-Client", "SMB1Protocol-Server", "MicrosoftWindowsPowerShellV2Root",
                   "MicrosoftWindowsPowerShellV2", "TelnetClient", "TFTP", "SimpleTCP", "Browser.InternetExplorer"]),
    Perfil("🗂️ Programas antiguos", ".NET 3.5 y DirectPlay, que piden muchos programas y juegos antiguos",
           activar=["NetFx3", "LegacyComponents", "DirectPlay"]),
]


def _buscar(lista: List[Caracteristica], tecnico: str) -> Optional[Caracteristica]:
    t = tecnico.lower()
    exacta = next((c for c in lista if c.tecnico.lower() == t), None)
    if exacta:
        return exacta
    return next((c for c in lista if c.tipo == TIPO_CAPACIDAD and _base_capacidad(c.tecnico)[0].lower() == t), None)


def plan_perfil(perfil: Perfil, lista: List[Caracteristica]) -> Tuple[List[Tuple[Caracteristica, bool]], List[str], List[str]]:
    """(cambios [(característica, activar)], ya_estan [nombres], no_existen [técnicos])."""
    cambios, ya, faltan = [], [], []
    for tecnico, activar in [(t, True) for t in perfil.activar] + [(t, False) for t in perfil.quitar]:
        c = _buscar(lista, tecnico)
        if c is None or c.estado == NO_DISPONIBLE:
            faltan.append(tecnico)
        elif (c.estado == ACTIVADA) == activar:
            ya.append(c.nombre)
        else:
            cambios.append((c, activar))
    return cambios, ya, faltan


def aplicar_plan(cambios: List[Tuple[Caracteristica, bool]], progreso: Optional[Callable[[int, str], None]] = None,
                 parar: Optional[Callable[[], bool]] = None) -> List[Tuple[Caracteristica, bool, bool, bool, str]]:
    """Aplica de una en una. Devuelve [(característica, activar, ok, reinicio, mensaje)]."""
    out = []
    for i, (c, activar) in enumerate(cambios):
        if parar and parar():
            break
        if progreso:
            progreso(int(100 * i / max(1, len(cambios))), f"{'Activando' if activar else 'Quitando'} {c.nombre}...")
        ok, reinicio, msg = cambiar(c, activar)
        out.append((c, activar, ok, reinicio, msg))
    return out
