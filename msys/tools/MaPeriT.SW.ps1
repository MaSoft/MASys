#Requires -RunAsAdministrator
#Requires -Version 5.1
<#
.NOTES
    MaPeriT.SW - Herramienta de Peritacion Informatica Forense Profesional v1.7.5
    Analisis forense digital completo para sistemas Windows
    Cumple RFC 3227 (IETF) y NIST SP 800-86
    Compilado: 2026-11-16 12:00:00
    NO EDITAR DIRECTAMENTE - Modifique los ficheros fuente en src\
#>

#region ===== Main-Params.ps1 =====
<#
.SYNOPSIS
    MaPeriT.SW - Herramienta de Peritacion Informatica Forense Profesional

.DESCRIPTION
    Realiza una adquisicion forense completa de un equipo Windows siguiendo
    los estandares RFC 3227 y NIST SP 800-86. Genera un informe HTML
    profesional en espanol apto para presentacion en juzgado.

.PARAMETER CaseNumber
    Numero de caso o expediente (obligatorio, excepto en modo -VerifyPath)

.PARAMETER ExaminerName
    Nombre del perito informatico (obligatorio, excepto en modo -VerifyPath)

.PARAMETER OutputPath
    Directorio de salida para las evidencias. Por defecto: .\MaPeriT_<CaseNumber>_<timestamp>

.PARAMETER EvidenceDescription
    Descripcion del encargo pericial

.PARAMETER SkipPhases
    Numeros de fase a omitir (1-10), como cadena separada por comas o espacios
    (p.ej. "3,7" o "3 7"). NO usar sintaxis de array @() aqui: al invocarse este
    script con -File (como hace MASys), PowerShell NO evalua @() como
    expresion -- lo recibe como texto literal y falla la conversion a numero.
    Si se invoca el script de forma interactiva con -Command o dot-sourcing, un
    array real tambien funciona (se convierte a texto y se vuelve a parsear).

.PARAMETER SkipRAM
    Omitir el volcado de memoria RAM

.PARAMETER CopyLargeFiles
    Copiar ficheros grandes (>500MB) como PST/OST

.PARAMETER HMACKey
    Clave para firmar el manifiesto de evidencias con HMAC-SHA256

.PARAMETER VerifyPath
    Modo verificacion: comprueba la integridad de una adquisicion previa contra su manifiesto.
    No requiere -CaseNumber ni -ExaminerName.

.PARAMETER DryRun
    Modo simulacion de preflight: comprueba prerrequisitos (privilegios, PowerShell, SO),
    localiza winpmem, enumera perfiles de usuario y verifica la presencia de las rutas de
    artefactos clave de cada fase, SIN VSS, SIN montar hives y SIN copiar ni un solo byte.
    Util para validar el entorno antes de una adquisicion real. No requiere -CaseNumber ni
    -ExaminerName. No es una simulacion fase a fase: es un chequeo de entorno de solo lectura.

.PARAMETER SelfTest
    Ejecuta una prueba de humo de la cadena de integridad (hash + copia + registro de
    custodia) contra un fichero temporal sintetico propio de la herramienta, para
    verificar que el mecanismo de hashing/copia/cadena de custodia funciona correctamente
    ANTES de iniciar una adquisicion real sobre el equipo objetivo. No toca el equipo
    objetivo. No requiere -CaseNumber ni -ExaminerName.

.PARAMETER ExcludePaths
    Array de fragmentos de ruta a excluir de la copia de evidencias (comparacion por
    subcadena, sin distinguir mayusculas/minusculas). Util para minimizacion de datos
    cuando la autorizacion/orden judicial delimita el alcance de la adquisicion
    (p.ej. -ExcludePaths @("\Fotos Familiares\","\Backups Personales\")).
    Se aplica en los dos puntos centrales de copia (Copy-ForensicFile y
    Copy-ForensicDirectory), que cubren la practica totalidad de las Fases 3, 4, 6, 7 y 8.
    Las rutas excluidas quedan registradas como SKIP en el log, nunca se omiten en
    silencio.

.PARAMETER NetworkIsolationCheck
    Comprueba y documenta en la Fase 1 si el equipo analizado tiene conexiones de red
    activas en el momento de la adquisicion (adaptadores activos, conexiones TCP
    establecidas). Es solo un chequeo de LECTURA e informativo para el acta pericial; no
    aisla ni desconecta nada.

.PARAMETER ChainOfCustodyStrictMode
    Si alguna evidencia queda con un hash SHA-256 invalido (ver INVALIDO: en
    ChainOfCustody.csv), el chequeo final de integridad se marca explicitamente como
    FALLIDO y se destaca en rojo como hallazgo critico en el informe pericial HTML.
    IMPORTANTE: este modo NO aborta la adquisicion en curso -- interrumpirla a mitad de
    proceso perderia de forma irrecuperable los datos volatiles de las fases pendientes,
    lo cual es peor que el problema de integridad que se quiere señalar. Es un modo de
    senalizacion agresiva al final, no de aborto.

.PARAMETER MaxEventsPerQuery
    Sobrescribe el limite por defecto de eventos por consulta a los logs de Windows
    (Config.ps1: $Script:Defaults.MaxEventsPerQuery = 50000) sin tener que editar el
    fuente. Util en servidores con actividad muy alta donde el limite por defecto se
    queda corto (ver WARNING "EVENTOS_POSIBLEMENTE_TRUNCADOS" en el log de ejecucion).

.EXAMPLE
    .\MaPeriT.SW.ps1 -CaseNumber "2026-001" -ExaminerName "Juan Perez" -OutputPath "E:\Evidencias"

.EXAMPLE
    .\MaPeriT.SW.ps1 -CaseNumber "CASO-123" -ExaminerName "Ana Garcia" -SkipPhases "8" -SkipRAM

.EXAMPLE
    .\MaPeriT.SW.ps1 -VerifyPath "E:\Evidencias\MaPeriT_CASO-001_20260602"

.EXAMPLE
    .\MaPeriT.SW.ps1 -DryRun

.EXAMPLE
    .\MaPeriT.SW.ps1 -SelfTest

.EXAMPLE
    .\MaPeriT.SW.ps1 -CaseNumber "CASO-123" -ExaminerName "Ana Garcia" -ChainOfCustodyStrictMode -NetworkIsolationCheck -MaxEventsPerQuery 200000
#>

param(
    [string]$CaseNumber,

    [string]$ExaminerName,

    [string]$OutputPath,

    [string]$EvidenceDescription = "",

    [string]$SkipPhases = "",

    [switch]$SkipRAM,

    [switch]$CopyLargeFiles,

    [string]$HMACKey = "",

    [string]$VerifyPath = "",

    [switch]$DryRun,

    [switch]$SelfTest,

    [string[]]$ExcludePaths = @(),

    [switch]$NetworkIsolationCheck,

    [switch]$ChainOfCustodyStrictMode,

    [int]$MaxEventsPerQuery = 0
)

# $SkipPhases llega como texto (ver .PARAMETER SkipPhases) -- se admite
# separado por comas Y/O espacios porque un array real pasado en modo
# interactivo (-Command) se convierte a texto separado por espacios via
# ToString(), y ambos formatos deben funcionar igual.
[int[]]$PhasesToSkip = @()
if ($SkipPhases) {
    $PhasesToSkip = @(
        $SkipPhases -split '[,\s]+' | Where-Object { $_ -match '^\d+$' } | ForEach-Object { [int]$_ }
    )
}

#endregion ===== Main-Params.ps1 =====

#region ===== Config.ps1 =====
#region ============================================================
# MaPeriT.SW - CONFIGURACIÓN GLOBAL
# Constantes, textos en español, tablas de referencia
# ============================================================

$Script:TOOL_NAME    = "MaPeriT.SW"
$Script:TOOL_VERSION = "1.7.5"
$Script:TOOL_FULL    = "MaPeriT.SW - Herramienta de Peritacion Informatica Forense Profesional"
$Script:TOOL_AUTHOR  = "MaPeriT.SW Project"
$Script:MIN_PS_VER   = [version]"5.1"

# --- Nombres de fases ---
$Script:PhaseNames = @{
    1  = "Datos Volatiles"
    2  = "Informacion del Sistema"
    3  = "Artefactos del Sistema de Ficheros"
    4  = "Registro de Windows"
    5  = "Logs de Eventos"
    6  = "Navegadores Web"
    7  = "Comunicaciones"
    8  = "Almacenamiento en la Nube"
    9  = "Dispositivos USB/Externos"
    10 = "Mecanismos de Persistencia"
}

# --- Estructura de directorios de salida ---
$Script:OutputDirs = @{
    Volatile       = "01_Volatile"
    SystemInfo     = "02_SystemInfo"
    Filesystem     = "03_Filesystem"
    Registry       = "04_Registry"
    EventLogs      = "05_EventLogs"
    Browsers       = "06_Browsers"
    Communications = "07_Communications"
    CloudStorage   = "08_CloudStorage"
    USB            = "09_USB"
    Persistence    = "10_Persistence"
    Analysis       = "Analysis"
    Report         = "Report"
    Logs           = "Logs"
}

# --- Event IDs de seguridad relevantes ---
$Script:SecurityEventIDs = @{
    LogonSuccess        = @(4624)
    LogonFailed         = @(4625)
    Logoff              = @(4634, 4647)
    ExplicitCredentials = @(4648)
    SpecialPrivileges   = @(4672)
    PrivilegeUse        = @(4673)
    AccountCreated      = @(4720)
    AccountEnabled      = @(4722)
    PasswordChange      = @(4723, 4724)
    AccountDisabled     = @(4725)
    AccountDeleted      = @(4726)
    AccountChanged      = @(4738)
    AccountLocked       = @(4740)
    AuditPolicyChange   = @(4719)
    ObjectAccess        = @(4663)
}

$Script:SystemEventIDs = @{
    ServiceCrash       = @(7034)
    ServiceControl     = @(7035, 7036)
    ServiceStartChange = @(7040)
    ServiceInstalled   = @(7045)
    ShutdownInitiated  = @(1074)
    EventLogStarted    = @(6005)
    EventLogStopped    = @(6006)
    UnexpectedShutdown = @(6008)
    SystemBoot         = @(6009)
}

$Script:AppEventIDs = @{
    AppCrash  = @(1000)
    WER       = @(1001)
    AppHang   = @(1002)
}

$Script:PowerShellEventIDs = @{
    ScriptBlock = @(4104)
    ModuleLog   = @(4103)
}

$Script:TaskSchedulerEventIDs = @{
    TaskRegistered = @(106)
    TaskUpdated    = @(140)
    TaskDeleted    = @(141)
    TaskStarted    = @(200)
    TaskCompleted  = @(201)
}

$Script:RDPEventIDs = @{
    SessionLogon      = @(21)
    ShellStart        = @(22)
    SessionLogoff     = @(23)
    SessionDisconnect = @(24)
    SessionReconnect  = @(25)
}

# --- Tipos de Logon (Event 4624) ---
$Script:LogonTypes = @{
    2  = "Interactivo (local)"
    3  = "Red"
    4  = "Batch"
    5  = "Servicio"
    7  = "Desbloqueo"
    8  = "Red (texto claro)"
    9  = "Credenciales nuevas"
    10 = "Escritorio Remoto (RDP)"
    11 = "Cached interactivo"
    12 = "Cached remoto"
    13 = "Cached desbloqueo"
}

# --- Rutas de registro clave ---
$Script:RegistryPaths = @{
    USBSTOR          = "HKLM:\SYSTEM\CurrentControlSet\Enum\USBSTOR"
    USB              = "HKLM:\SYSTEM\CurrentControlSet\Enum\USB"
    MountedDevices   = "HKLM:\SYSTEM\MountedDevices"
    NetworkList      = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\NetworkList\Profiles"
    NetworkSigs      = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\NetworkList\Signatures"
    NetworkInterfaces= "HKLM:\SYSTEM\CurrentControlSet\Services\Tcpip\Parameters\Interfaces"
    RunHKLM          = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
    RunOnceHKLM      = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce"
    RunHKCU          = "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
    RunOnceHKCU      = "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce"
    PoliciesRun      = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer\Run"
    Winlogon         = "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon"
    UninstallHKLM    = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    UninstallWow64   = "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"
    UninstallHKCU    = "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    AppCompatCache   = "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\AppCompatCache"
    BAM              = "HKLM:\SYSTEM\CurrentControlSet\Services\bam\State\UserSettings"
    DAM              = "HKLM:\SYSTEM\CurrentControlSet\Services\dam\State\UserSettings"
    ShutdownTime     = "HKLM:\SYSTEM\CurrentControlSet\Control\Windows"
    TimeZone         = "HKLM:\SYSTEM\CurrentControlSet\Control\TimeZoneInformation"
    RDPClientDefault = "HKCU:\Software\Microsoft\Terminal Server Client\Default"
    RDPClientServers = "HKCU:\Software\Microsoft\Terminal Server Client\Servers"
    LastLoggedOn     = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Authentication\LogonUI"
    UserAssist       = "Software\Microsoft\Windows\CurrentVersion\Explorer\UserAssist"
    RecentDocs       = "Software\Microsoft\Windows\CurrentVersion\Explorer\RecentDocs"
    RunMRU           = "Software\Microsoft\Windows\CurrentVersion\Explorer\RunMRU"
    TypedPaths       = "Software\Microsoft\Windows\CurrentVersion\Explorer\TypedPaths"
    TypedURLs        = "Software\Microsoft\Internet Explorer\TypedURLs"
    MountPoints2     = "Software\Microsoft\Windows\CurrentVersion\Explorer\MountPoints2"
    ComDlg32MRU      = "Software\Microsoft\Windows\CurrentVersion\Explorer\ComDlg32\OpenSavePidlMRU"
    Shellbags        = "Software\Classes\Local Settings\Software\Microsoft\Windows\Shell\BagMRU"
}

# --- Rutas de perfiles de navegadores ---
$Script:BrowserPaths = @{
    Chrome  = "AppData\Local\Google\Chrome\User Data"
    Edge    = "AppData\Local\Microsoft\Edge\User Data"
    Brave   = "AppData\Local\BraveSoftware\Brave-Browser\User Data"
    Firefox = "AppData\Roaming\Mozilla\Firefox\Profiles"
    IE      = "AppData\Local\Microsoft\Windows\WebCache"
}

# --- Ficheros de navegadores Chromium a recopilar ---
$Script:ChromiumFiles = @(
    "History", "Downloads", "Cookies", "Login Data", "Preferences",
    "Bookmarks", "Web Data", "Top Sites", "Shortcuts", "Favicons",
    "Network Action Predictor",
    "Network\Cookies"   # ubicacion de las cookies desde Chrome/Edge 96
)

# --- Ficheros de Firefox a recopilar ---
$Script:FirefoxFiles = @(
    "places.sqlite", "cookies.sqlite", "formhistory.sqlite",
    "logins.json", "key4.db", "cert9.db", "extensions.json",
    "webappsstore.sqlite", "favicons.sqlite", "permissions.sqlite"
)

# --- Rutas de comunicaciones ---
$Script:CommunicationPaths = @{
    OutlookOST     = "AppData\Local\Microsoft\Outlook"
    OutlookPST     = "Documents\Outlook Files"
    TeamsNew        = "AppData\Local\Packages\MSTeams_8wekyb3d8bbwe\LocalCache"
    TeamsClassic    = "AppData\Roaming\Microsoft\Teams"
    Skype           = "AppData\Roaming\Microsoft\Skype for Desktop"
    Discord         = "AppData\Roaming\discord"
}

# --- Rutas de almacenamiento en la nube ---
$Script:CloudPaths = @{
    OneDriveLogs    = "AppData\Local\Microsoft\OneDrive\logs"
    OneDriveSettings= "AppData\Local\Microsoft\OneDrive\settings"
    GoogleDrive     = "AppData\Local\Google\DriveFS"
    Dropbox         = "AppData\Local\Dropbox"
    DropboxInfo     = "AppData\Roaming\Dropbox"
}

# --- Severidad de anomalias ---
$Script:AnomalySeverity = @{
    Critical = "CRITICA"
    High     = "ALTA"
    Medium   = "MEDIA"
    Low      = "BAJA"
    Info     = "INFORMATIVA"
}

# --- Textos del informe en espanol ---
$Script:ReportStrings = @{
    Title              = "INFORME PERICIAL INFORMATICO FORENSE"
    Subtitle           = "Analisis Forense Digital"
    Confidential       = "CONFIDENCIAL - DOCUMENTO PERICIAL"
    TOC                = "INDICE"
    ExecutiveSummary   = "RESUMEN EJECUTIVO"
    Methodology        = "METODOLOGIA"
    MethodologyText    = "El presente analisis forense se ha realizado siguiendo las directrices establecidas en la RFC 3227 (Guidelines for Evidence Collection and Archiving) del IETF y la guia NIST SP 800-86 (Guide to Integrating Forensic Techniques into Incident Response). La adquisicion de evidencias se ha efectuado respetando el orden de volatilidad establecido en la RFC 3227, priorizando la captura de datos volatiles antes de proceder con los datos persistentes. Todas las evidencias han sido verificadas mediante hash SHA-256 para garantizar su integridad."
    ChainOfCustody     = "CADENA DE CUSTODIA"
    SystemID           = "IDENTIFICACION DEL SISTEMA"
    CollectedEvidence  = "EVIDENCIAS RECOPILADAS"
    Findings           = "HALLAZGOS"
    Timeline           = "LINEA TEMPORAL"
    Anomalies          = "ANOMALIAS DETECTADAS"
    Conclusions        = "CONCLUSIONES"
    Appendices         = "ANEXOS"
    AppendixHashes     = "Anexo A: Manifiesto de Hashes"
    AppendixProcesses  = "Anexo B: Lista Completa de Procesos"
    AppendixServices   = "Anexo C: Lista Completa de Servicios"
    AppendixEvents     = "Anexo D: Analisis de Eventos"
    AppendixLog        = "Anexo E: Log de Ejecucion de la Herramienta"
    CaseNumber         = "Numero de Caso"
    ExaminerName       = "Perito Informatico"
    ExamDate           = "Fecha del Analisis"
    SystemName         = "Nombre del Equipo"
    ToolUsed           = "Herramienta Utilizada"
    ToolHash           = "Hash de la Herramienta (SHA-256)"
    EvidenceID         = "ID Evidencia"
    SourcePath         = "Ruta Origen"
    DestPath           = "Ruta Destino"
    SHA256             = "Hash SHA-256"
    AcquisitionMethod  = "Metodo de Adquisicion"
    Timestamp          = "Marca de Tiempo"
    Phase              = "Fase"
    Action             = "Accion"
    Result             = "Resultado"
    NoDataAvailable    = "No se encontraron datos en esta categoria."
    CollectionFailed   = "La recopilacion de este artefacto fallo. Consulte el log de ejecucion para mas detalles."
    TotalFiles         = "Total de ficheros recopilados"
    TotalSize          = "Tamano total de evidencias"
    ExecutionTime      = "Tiempo total de ejecucion"
    UserActivity       = "Actividad de Usuarios"
    NetworkActivity    = "Actividad de Red"
    InstalledSoftware  = "Software Instalado"
    USBDevices         = "Dispositivos USB"
    WebBrowsing        = "Navegacion Web"
    Communications     = "Comunicaciones"
    CloudStorage       = "Almacenamiento en la Nube"
    PersistenceMechanisms = "Mecanismos de Persistencia"
    Severity           = "Severidad"
    Description        = "Descripcion"
    Evidence           = "Evidencia"
    Recommendation     = "Recomendacion"
}

# --- Banner de inicio ---
$Script:Banner = @"

  ╔═══════════════════════════════════════════════════════════════════╗
  ║                                                                   ║
  ║      ███╗   ███╗ █████╗ ██████╗ ███████╗██████╗ ██╗████████╗     ║
  ║      ████╗ ████║██╔══██╗██╔══██╗██╔════╝██╔══██╗██║╚══██╔══╝     ║
  ║      ██╔████╔██║███████║██████╔╝█████╗  ██████╔╝██║   ██║        ║
  ║      ██║╚██╔╝██║██╔══██║██╔═══╝ ██╔══╝  ██╔══██╗██║   ██║        ║
  ║      ██║ ╚═╝ ██║██║  ██║██║     ███████╗██║  ██║██║   ██║        ║
  ║      ╚═╝     ╚═╝╚═╝  ╚═╝╚═╝     ╚══════╝╚═╝  ╚═╝╚═╝   ╚═╝  .SW  ║
  ║                                                                   ║
  ║        Herramienta de Peritacion Informatica Forense              ║
  ║                    Profesional v{VERSION}                         ║
  ║                                                                   ║
  ╚═══════════════════════════════════════════════════════════════════╝

"@

# --- Aviso legal ---
$Script:LegalDisclaimer = @"
═══════════════════════════════════════════════════════════════════════
                          AVISO LEGAL
═══════════════════════════════════════════════════════════════════════

  MaPeriT.SW realiza una adquisicion forense de evidencias
  digitales. Su uso debe estar autorizado por el propietario del
  equipo o por orden judicial.

  - Todas las operaciones son de SOLO LECTURA sobre el sistema.
  - La unica escritura se realiza en el directorio de salida.
  - Se crea un Volume Shadow Copy temporal que se elimina al finalizar.
  - No se realizan conexiones de red por parte de la herramienta.
  - Todas las evidencias se verifican mediante hash SHA-256.

  El uso no autorizado de esta herramienta puede constituir un
  delito conforme a la legislacion vigente.

═══════════════════════════════════════════════════════════════════════
"@

# --- Parametros por defecto ---
$Script:Defaults = @{
    LargeFileThresholdMB = 500
    PhaseTimeoutMinutes  = 10
    ADSScanTimeoutSec    = 120
    SignatureScanTimeout = 300
    MaxEventsPerQuery    = 50000
    HashAlgorithm        = "SHA256"
    CSVDelimiter         = ","
    DateFormat           = "yyyy-MM-dd HH:mm:ss.fff"
    DateFormatShort      = "yyyy-MM-dd HH:mm:ss"
    TimeZoneFormat       = "yyyy-MM-dd HH:mm:ss zzz"
}

#endregion ===== Config.ps1 =====

#region ===== Logging.ps1 =====
#region ============================================================
# MaPeriT.SW - MOTOR DE LOGGING
# ActionLog, CadenaCustodia, salida por consola
# ============================================================

function Initialize-Logging {
    param(
        [string]$LogsPath
    )
    $Script:ActionLogPath = Join-Path $LogsPath "ActionLog.csv"
    $Script:CustodyLogPath = Join-Path $LogsPath "ChainOfCustody.csv"
    $Script:EvidenceCounter = 0
    $Script:StrictCustodyFailures = @()

    "Timestamp,Phase,Action,Target,Result,Duration_ms,ErrorMessage" | Out-File -FilePath $Script:ActionLogPath -Encoding UTF8
    "Timestamp,EvidenceID,SourcePath,DestinationPath,SHA256Hash,AcquiredBy,Method,FileSize" | Out-File -FilePath $Script:CustodyLogPath -Encoding UTF8

    Write-ForensicLog -Phase "INIT" -Action "LOGGING_INICIADO" -Target $LogsPath -Result "OK"
}

function Write-ForensicLog {
    param(
        [string]$Phase,
        [string]$Action,
        [string]$Target,
        [string]$Result = "OK",
        [long]$DurationMs = 0,
        [string]$ErrorMessage = ""
    )
    $ts = Get-Date -Format $Script:Defaults.DateFormat
    $sanitizedTarget = $Target -replace '"', '""'
    $sanitizedError = $ErrorMessage -replace '"', '""'
    $line = "`"$ts`",`"$Phase`",`"$Action`",`"$sanitizedTarget`",`"$Result`",$DurationMs,`"$sanitizedError`""

    try {
        $line | Out-File -FilePath $Script:ActionLogPath -Append -Encoding UTF8
    } catch {
        # Si falla el log a fichero, al menos mostramos en consola
    }

    $color = switch ($Result) {
        "OK"      { "Green" }
        "WARNING" { "Yellow" }
        "ERROR"   { "Red" }
        "SKIP"    { "DarkYellow" }
        default   { "White" }
    }

    $prefix = switch ($Result) {
        "OK"      { "[+]" }
        "WARNING" { "[!]" }
        "ERROR"   { "[-]" }
        "SKIP"    { "[~]" }
        default   { "[*]" }
    }

    Write-Host "  $prefix " -ForegroundColor $color -NoNewline
    Write-Host "$Action" -ForegroundColor White -NoNewline
    if ($Target) {
        Write-Host " -> " -ForegroundColor DarkGray -NoNewline
        Write-Host "$Target" -ForegroundColor Gray
    } else {
        Write-Host ""
    }
}

function Add-ChainOfCustodyEntry {
    param(
        [string]$SourcePath,
        [string]$DestPath,
        [string]$Hash,
        [string]$Method,
        [long]$FileSize = 0
    )
    $Script:EvidenceCounter++
    $ts = Get-Date -Format $Script:Defaults.DateFormat
    $evidenceId = "EVD-{0:D5}" -f $Script:EvidenceCounter
    $examiner = $Script:SessionInfo.ExaminerName

    # --- Validacion de formato del hash antes de dejarlo constar en el acta de custodia ---
    # Get-ForensicHash nunca lanza excepcion: si el calculo falla devuelve cadenas de
    # diagnostico como "FILE_NOT_FOUND" o "HASH_ERROR: ...". Sin esta validacion, ese texto
    # quedaria escrito en la columna SHA256Hash del acta como si fuese un hash real,
    # comprometiendo la validez pericial de esa evidencia sin que nadie lo note.
    $isValidHash = $Hash -match '^[A-Fa-f0-9]{64}$'
    if (-not $isValidHash) {
        Write-ForensicLog -Phase "CUSTODY" -Action "HASH_INVALIDO_EN_CUSTODIA" -Target "$DestPath -> '$Hash'" -Result "ERROR" -ErrorMessage "El valor de hash no tiene formato SHA-256 valido (64 hex). La evidencia se registra igualmente marcada como INVALIDO, pero debe revisarse manualmente: puede que el fichero se haya eliminado o modificado tras la copia."
        if ($Script:SessionInfo) { $Script:SessionInfo.TotalErrors++ }
        # -ChainOfCustodyStrictMode: acumular para que el chequeo final de integridad se
        # marque explicitamente como FALLIDO y se destaque en el informe pericial. No aborta
        # la adquisicion en curso (ver documentacion del parametro).
        if ($Script:ChainOfCustodyStrictMode) {
            if (-not $Script:StrictCustodyFailures) { $Script:StrictCustodyFailures = @() }
            $Script:StrictCustodyFailures += "EVD-{0:D5} ({1})" -f $Script:EvidenceCounter, $DestPath
        }
    }
    $hashField = if ($isValidHash) { $Hash } else { "INVALIDO:$Hash" }

    $sanitizedSource = $SourcePath -replace '"', '""'
    $sanitizedDest = $DestPath -replace '"', '""'
    $line = "`"$ts`",`"$evidenceId`",`"$sanitizedSource`",`"$sanitizedDest`",`"$hashField`",`"$examiner`",`"$Method`",$FileSize"

    try {
        $line | Out-File -FilePath $Script:CustodyLogPath -Append -Encoding UTF8
    } catch {
        Write-ForensicLog -Phase "CUSTODY" -Action "ERROR_ESCRITURA_CUSTODIA" -Target $DestPath -Result "ERROR" -ErrorMessage $_.Exception.Message
    }
}

function Write-PhaseHeader {
    param(
        [int]$PhaseNumber,
        [string]$PhaseName
    )
    $Script:CurrentPhaseStart = Get-Date
    $header = @"

  ╔═════════════════════════════════════════════════════════════════╗
  ║  FASE $PhaseNumber - $($PhaseName.ToUpper().PadRight(52))║
  ╚═════════════════════════════════════════════════════════════════╝
"@
    Write-Host $header -ForegroundColor Cyan
    Write-ForensicLog -Phase "FASE_$PhaseNumber" -Action "INICIO_FASE" -Target $PhaseName -Result "OK"
}

function Write-PhaseComplete {
    param(
        [int]$PhaseNumber,
        [string]$PhaseName,
        [int]$ItemsCollected = 0
    )
    $duration = ((Get-Date) - $Script:CurrentPhaseStart).TotalSeconds
    Write-Host ""
    Write-Host "  [✓] Fase $PhaseNumber completada: $ItemsCollected elementos recopilados ($([math]::Round($duration,1))s)" -ForegroundColor Green
    Write-Host ""
    Write-ForensicLog -Phase "FASE_$PhaseNumber" -Action "FIN_FASE" -Target "$PhaseName - $ItemsCollected elementos" -Result "OK" -DurationMs ([long]($duration * 1000))
}

function Write-StepHeader {
    param(
        [string]$StepName
    )
    Write-Host ""
    Write-Host "  --- $StepName ---" -ForegroundColor DarkCyan
}

function Write-ProgressBanner {
    param(
        [int]$CurrentPhase,
        [int]$TotalPhases = 10,
        [string]$PhaseName
    )
    $pct = [math]::Round(($CurrentPhase / $TotalPhases) * 100)
    $filled = [math]::Round($pct / 5)
    $empty = 20 - $filled
    $bar = ("█" * $filled) + ("░" * $empty)
    Write-Host ""
    Write-Host "  Progreso: [$bar] $pct% - Fase $CurrentPhase/$TotalPhases" -ForegroundColor Yellow
}

function Export-CollectionToCSV {
    param(
        [array]$Data,
        [string]$OutputPath,
        [string]$Phase,
        [string]$Description
    )
    if ($null -eq $Data -or $Data.Count -eq 0) {
        Write-ForensicLog -Phase $Phase -Action "SIN_DATOS" -Target $Description -Result "WARNING"
        return
    }
    try {
        $Data | Export-Csv -Path $OutputPath -NoTypeInformation -Encoding UTF8
        $hash = Get-ForensicHash -FilePath $OutputPath
        $size = (Get-Item $OutputPath).Length
        Add-ChainOfCustodyEntry -SourcePath "GENERATED" -DestPath $OutputPath -Hash $hash -Method "Export-CSV" -FileSize $size
        Write-ForensicLog -Phase $Phase -Action "EXPORTADO" -Target $OutputPath -Result "OK"
    } catch {
        Write-ForensicLog -Phase $Phase -Action "ERROR_EXPORT" -Target $OutputPath -Result "ERROR" -ErrorMessage $_.Exception.Message
    }
}

function Export-CollectionToJSON {
    param(
        [object]$Data,
        [string]$OutputPath,
        [string]$Phase,
        [string]$Description
    )
    if ($null -eq $Data) {
        Write-ForensicLog -Phase $Phase -Action "SIN_DATOS" -Target $Description -Result "WARNING"
        return
    }
    try {
        # -InputObject (no tuberia): con tuberia, una coleccion de UN elemento se
        # escribia como objeto suelto en vez de array, y quien leia el JSON
        # (p.ej. el informe con Anomalies.json) contaba 0 elementos.
        ConvertTo-Json -InputObject $Data -Depth 10 | Out-File -FilePath $OutputPath -Encoding UTF8
        $hash = Get-ForensicHash -FilePath $OutputPath
        $size = (Get-Item $OutputPath).Length
        Add-ChainOfCustodyEntry -SourcePath "GENERATED" -DestPath $OutputPath -Hash $hash -Method "Export-JSON" -FileSize $size
        Write-ForensicLog -Phase $Phase -Action "EXPORTADO" -Target $OutputPath -Result "OK"
    } catch {
        Write-ForensicLog -Phase $Phase -Action "ERROR_EXPORT" -Target $OutputPath -Result "ERROR" -ErrorMessage $_.Exception.Message
    }
}

function Export-CollectionToText {
    param(
        [string]$Content,
        [string]$OutputPath,
        [string]$Phase,
        [string]$Description
    )
    try {
        $Content | Out-File -FilePath $OutputPath -Encoding UTF8
        $hash = Get-ForensicHash -FilePath $OutputPath
        $size = (Get-Item $OutputPath).Length
        Add-ChainOfCustodyEntry -SourcePath "GENERATED" -DestPath $OutputPath -Hash $hash -Method "Export-TXT" -FileSize $size
        Write-ForensicLog -Phase $Phase -Action "EXPORTADO" -Target $OutputPath -Result "OK"
    } catch {
        Write-ForensicLog -Phase $Phase -Action "ERROR_EXPORT" -Target $OutputPath -Result "ERROR" -ErrorMessage $_.Exception.Message
    }
}

#endregion ===== Logging.ps1 =====

#region ===== PathHelpers.ps1 =====
#region ============================================================
# Ecosistema MaPeriT - HELPERS DE RUTAS COMPARTIDOS
# Fichero comun a varias herramientas (ver cada Build.ps1 para la lista
# exacta de que herramientas lo incluyen). NO EDITAR una copia local
# dentro de MAPERIT_SW/MAPERIT_OF/etc. - este es el unico punto de verdad.
# ============================================================

function Get-RelativeEvidencePath {
    <#
        Calcula la ruta relativa de un fichero de evidencia respecto a la raiz
        de salida, anclada estrictamente al PREFIJO de la ruta y sin distinguir
        mayusculas/minusculas (frecuente que la capitalizacion real en disco
        difiera de la de $RootPath en Windows).

        Antes cada herramienta (MaPeriT.SW, MaPeriT.OF) tenia su propia copia
        de esta misma funcion, escrita por separado. La version original de
        SW usaba $FullPath.Replace($RootPath, ""), que:
          - no es un anclaje de prefijo: sustituye CUALQUIER aparicion de
            $RootPath en toda la ruta, no solo al principio - podia corromper
            la ruta relativa si el nombre de la carpeta raiz se repetia en una
            subruta anidada.
          - es sensible a mayusculas/minusculas: si la capitalizacion real en
            disco difiere de la de $RootPath, no encuentra coincidencia y
            devuelve la ruta completa sin recortar, rompiendo la verificacion
            de integridad posterior (Test-HashManifest / -VerifyPath).
        Corregido una vez aqui en 2026-07-12 en vez de mantener dos copias
        que podian divergir o repetir el mismo bug por separado.
    #>
    param(
        [string]$FullPath,
        [string]$RootPath
    )
    if ([string]::IsNullOrEmpty($FullPath) -or [string]::IsNullOrEmpty($RootPath)) { return "" }

    if ($FullPath.StartsWith($RootPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $FullPath.Substring($RootPath.Length).TrimStart('\')
    }

    # Fallback defensivo (no deberia ocurrir en uso normal): devolver cadena
    # vacia en vez de una ruta relativa erronea generada con .Replace() (que
    # sustituye CUALQUIER aparicion de $RootPath en toda la ruta, no solo al
    # principio). Una ruta vacia hace fallar la verificacion posterior de
    # forma visible (FILE_MISSING); una ruta relativa "plausible pero
    # incorrecta" fallaria de forma silenciosa y mas dificil de diagnosticar.
    # Write-ForensicLog es una funcion propia de cada herramienta (no de este
    # fichero comun), asi que se comprueba que exista antes de usarla.
    if (Get-Command Write-ForensicLog -ErrorAction SilentlyContinue) {
        Write-ForensicLog -Phase "INTEGRIDAD" -Action "RUTA_RELATIVA_NO_ANCLADA" -Target "$FullPath (raiz esperada: $RootPath)" -Result "WARNING"
    }
    return ""
}

#endregion

#endregion ===== PathHelpers.ps1 =====

#region ===== HtmlEncoding.ps1 =====
#region ============================================================
# Ecosistema MaPeriT - COMPATIBILIDAD DE HtmlEncode COMPARTIDA
#
# BUG CONFIRMADO EMPIRICAMENTE (12/07/2026, verificacion end-to-end real de
# MaPeriT.OF contra un disco objetivo sintetico): "Add-Type -AssemblyName
# System.Web -ErrorAction SilentlyContinue" puede fallar en silencio incluso
# bajo Windows PowerShell 5.1 (no es un problema exclusivo de PowerShell 7 /
# .NET Core, como se habia asumido hasta ahora). Cuando falla, TODAS las
# llamadas a [System.Web.HttpUtility]::HtmlEncode(...) en cualquier
# generador de informes (SW, OF, AN usan esta clase extensivamente) lanzan
# "No se encuentra el tipo [System.Web.HttpUtility]" y el informe pericial
# se genera roto - en el caso de OF, el bug era aun peor: nunca llegaba
# siquiera a INTENTAR cargar el ensamblado.
#
# Este fichero garantiza que el tipo System.Web.HttpUtility SIEMPRE quede
# disponible: intenta cargar el ensamblado real y, si el tipo sigue sin
# resolver por cualquier motivo, define un reemplazo minimo bajo el MISMO
# nombre completamente cualificado. Todas las llamadas existentes
# [System.Web.HttpUtility]::HtmlEncode(...) en cualquier fichero de informe
# siguen funcionando sin cambiar ni un solo punto de llamada.
# ============================================================

Add-Type -AssemblyName System.Web -ErrorAction SilentlyContinue

if (-not ([System.Management.Automation.PSTypeName]'System.Web.HttpUtility').Type) {
    try {
        Add-Type -TypeDefinition @"
using System;
namespace System.Web {
    public static class HttpUtility {
        public static string HtmlEncode(string s) {
            if (string.IsNullOrEmpty(s)) return s;
            return s.Replace("&","&amp;").Replace("<","&lt;").Replace(">","&gt;").Replace("\"","&quot;").Replace("'","&#39;");
        }
    }
}
"@ -Language CSharp -ErrorAction Stop
    } catch {
        # Si ni siquiera esto carga (extremadamente improbable), los generadores
        # de informe seguiran fallando igual que antes de este fichero - no hay
        # nada mas que se pueda hacer sin depender de una libreria externa.
    }
}

#endregion

#endregion ===== HtmlEncoding.ps1 =====

#region ===== Hashing.ps1 =====
#region ============================================================
# MaPeriT.SW - MOTOR DE HASHING E INTEGRIDAD
# SHA-256, manifiestos, HMAC, verificacion
# ============================================================

function Get-ForensicHash {
    param(
        [string]$FilePath
    )
    try {
        if (-not (Test-Path $FilePath)) { return "FILE_NOT_FOUND" }
        $hash = Get-FileHash -Path $FilePath -Algorithm SHA256 -ErrorAction Stop
        return $hash.Hash
    } catch {
        return "HASH_ERROR: $($_.Exception.Message)"
    }
}

# Get-RelativeEvidencePath ya no se define aqui: se comparte con MaPeriT.OF
# via ..\Common\PathHelpers.ps1 (incluido por Build.ps1 antes de este
# fichero) para no mantener dos copias que puedan divergir o repetir el
# mismo bug de anclaje de ruta por separado. Ver ese fichero para el
# historial completo del bug que motivo la correccion original.

function Get-ScriptSelfHash {
    param(
        [string]$ScriptPath
    )
    if ([string]::IsNullOrEmpty($ScriptPath) -or -not (Test-Path $ScriptPath)) {
        return "SELF_HASH_UNAVAILABLE"
    }
    return Get-ForensicHash -FilePath $ScriptPath
}

function New-HashManifest {
    param(
        [string]$EvidenceRootPath,
        [string]$ManifestPath
    )
    Write-ForensicLog -Phase "INTEGRIDAD" -Action "GENERANDO_MANIFIESTO" -Target $EvidenceRootPath
    $sw = [System.Diagnostics.Stopwatch]::StartNew()

    $manifestData = [System.Collections.ArrayList]::new()

    # Logs\ActionLog.csv es el registro vivo de la ejecucion: se sigue escribiendo
    # despues de generar el manifiesto (como minimo, la propia linea
    # MANIFIESTO_GENERADO de esta funcion). Incluido en el manifiesto, -VerifyPath
    # daba SIEMPRE "INTEGRIDAD COMPROMETIDA" sobre una adquisicion intacta. Se
    # excluye; cada evidencia sigue con su hash en Logs\ChainOfCustody.csv.
    $files = @(Get-ChildItem -Path $EvidenceRootPath -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object { -not $Script:ActionLogPath -or $_.FullName -ne $Script:ActionLogPath })
    $total = $files.Count
    $current = 0

    foreach ($file in $files) {
        $current++
        if ($current % 50 -eq 0) {
            Write-Host "`r  [*] Calculando hashes: $current/$total" -ForegroundColor Yellow -NoNewline
        }
        try {
            $hash = Get-ForensicHash -FilePath $file.FullName
            $relativePath = Get-RelativeEvidencePath -FullPath $file.FullName -RootPath $EvidenceRootPath
            $lastMod = $file.LastWriteTimeUtc.ToString($Script:Defaults.DateFormat)
            [void]$manifestData.Add([PSCustomObject]@{
                FilePath = $relativePath
                SHA256 = $hash
                FileSize = $file.Length
                LastModified = $lastMod
            })
        } catch {
            $relativePath = Get-RelativeEvidencePath -FullPath $file.FullName -RootPath $EvidenceRootPath
            [void]$manifestData.Add([PSCustomObject]@{
                FilePath = $relativePath
                SHA256 = "HASH_ERROR"
                FileSize = 0
                LastModified = "ERROR"
            })
        }
    }

    Write-Host ""
    $manifestData | Export-Csv -Path $ManifestPath -NoTypeInformation -Encoding UTF8
    $sw.Stop()

    Write-ForensicLog -Phase "INTEGRIDAD" -Action "MANIFIESTO_GENERADO" -Target "$total ficheros hasheados" -Result "OK" -DurationMs $sw.ElapsedMilliseconds
    return $total
}

function Test-HashManifest {
    <#
        Unica implementacion de verificacion de integridad contra un manifiesto de
        hashes. Antes existia esta funcion (sin usar en ningun sitio) Y una segunda
        copia casi identica de la misma logica duplicada directamente en el bloque
        -VerifyPath de Main.ps1 -- dos sitios que mantener sincronizados para el mismo
        calculo de seguridad. Ahora -VerifyPath llama a esta funcion con -ShowProgress.
    #>
    param(
        [string]$ManifestPath,
        [string]$EvidenceRootPath,
        [switch]$ShowProgress
    )
    if (-not (Test-Path $ManifestPath)) {
        Write-Host "  [-] Manifiesto no encontrado: $ManifestPath" -ForegroundColor Red
        return [PSCustomObject]@{ Success = $false; Total = 0; Verified = 0; Missing = 0; Mismatches = 0 }
    }

    # @() obligatorio: Import-Csv con un manifiesto de UN solo fichero de evidencia
    # devuelve un objeto escalar (no un array) en PowerShell 5.1, y ese escalar no
    # tiene propiedad .Count -- $manifestData.Count quedaria en $null y "Total en
    # manifiesto" saldria vacio en el resumen, aunque la verificacion en si (el
    # foreach de mas abajo) funcione bien igualmente sobre un unico objeto.
    $manifestData = @(Import-Csv $ManifestPath -ErrorAction Stop)
    $mismatchList = @()
    $verified = 0
    $missing = 0
    $total = $manifestData.Count
    $current = 0

    foreach ($entry in $manifestData) {
        $current++
        if ($ShowProgress -and ($current % 25 -eq 0)) {
            Write-Host "`r  [*] Verificando: $current/$total" -ForegroundColor Yellow -NoNewline
        }

        $filePath = Join-Path $EvidenceRootPath $entry.FilePath
        $expectedHash = $entry.SHA256

        if ($expectedHash -like "HASH_ERROR*") { continue }

        if (-not (Test-Path $filePath)) {
            if ($ShowProgress) { Write-Host "`n  [-] FALTA: $($entry.FilePath)" -ForegroundColor Red }
            $mismatchList += [PSCustomObject]@{
                File = $entry.FilePath
                Expected = $expectedHash
                Actual = "FILE_MISSING"
                Status = "FALLO"
            }
            $missing++
            continue
        }

        $actualHash = Get-ForensicHash -FilePath $filePath
        if ($actualHash -ne $expectedHash) {
            if ($ShowProgress) {
                Write-Host "`n  [-] MODIFICADO: $($entry.FilePath)" -ForegroundColor Red
                Write-Host "      Esperado: $expectedHash" -ForegroundColor DarkGray
                Write-Host "      Actual:   $actualHash" -ForegroundColor DarkGray
            }
            $mismatchList += [PSCustomObject]@{
                File = $entry.FilePath
                Expected = $expectedHash
                Actual = $actualHash
                Status = "FALLO"
            }
        } else {
            $verified++
        }
    }
    if ($ShowProgress) { Write-Host "" }

    $success = ($mismatchList.Count -eq 0)
    if (-not $ShowProgress) {
        if ($success) {
            Write-Host "  [+] Verificacion de integridad OK: $verified ficheros verificados" -ForegroundColor Green
        } else {
            Write-Host "  [-] FALLOS DE INTEGRIDAD DETECTADOS: $($mismatchList.Count) ficheros" -ForegroundColor Red
            $mismatchList | ForEach-Object {
                Write-Host "      $($_.File): $($_.Status)" -ForegroundColor Red
            }
        }
    }

    return [PSCustomObject]@{
        Success    = $success
        Total      = $total
        Verified   = $verified
        Missing    = $missing
        Mismatches = ($mismatchList.Count - $missing)
    }
}

function New-EvidenceHMAC {
    param(
        [string]$ManifestPath,
        [string]$Key,
        [string]$OutputPath
    )
    if ([string]::IsNullOrEmpty($Key)) {
        Write-ForensicLog -Phase "INTEGRIDAD" -Action "HMAC_OMITIDO" -Target "No se proporciono clave HMAC" -Result "SKIP"
        return
    }

    try {
        $keyBytes = [System.Text.Encoding]::UTF8.GetBytes($Key)
        $hmac = New-Object System.Security.Cryptography.HMACSHA256
        $hmac.Key = $keyBytes

        $fileBytes = [System.IO.File]::ReadAllBytes($ManifestPath)
        $hashBytes = $hmac.ComputeHash($fileBytes)
        $hmacHex = [BitConverter]::ToString($hashBytes) -replace '-', ''

        $content = @"
HMAC-SHA256 del Manifiesto de Evidencias
==========================================
Fichero:  $(Split-Path $ManifestPath -Leaf)
Fecha:    $(Get-Date -Format $Script:Defaults.DateFormat)
HMAC:     $hmacHex

Para verificar la integridad del manifiesto, recalcule el HMAC-SHA256
del fichero HashManifest.csv usando la misma clave y compare con el
valor anterior.
"@
        $content | Out-File -FilePath $OutputPath -Encoding UTF8
        Write-ForensicLog -Phase "INTEGRIDAD" -Action "HMAC_GENERADO" -Target $OutputPath -Result "OK"
    } catch {
        Write-ForensicLog -Phase "INTEGRIDAD" -Action "HMAC_ERROR" -Target $OutputPath -Result "ERROR" -ErrorMessage $_.Exception.Message
    } finally {
        if ($hmac) { $hmac.Dispose() }
    }
}

#endregion ===== Hashing.ps1 =====

#region ===== CSharpHelpers.ps1 =====
#region ============================================================
# MaPeriT.SW - HELPERS C# EMBEBIDOS
# Acceso raw NTFS: MFT, USN Journal via P/Invoke
# ============================================================

function Initialize-CSharpHelpers {
    $csharpCode = @'
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.ComponentModel;

namespace MaPeriT
{
    public class NativeMethods
    {
        [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Auto)]
        public static extern IntPtr CreateFile(
            string lpFileName,
            uint dwDesiredAccess,
            uint dwShareMode,
            IntPtr lpSecurityAttributes,
            uint dwCreationDisposition,
            uint dwFlagsAndAttributes,
            IntPtr hTemplateFile);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool ReadFile(
            IntPtr hFile,
            byte[] lpBuffer,
            uint nNumberOfBytesToRead,
            out uint lpNumberOfBytesRead,
            IntPtr lpOverlapped);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool SetFilePointerEx(
            IntPtr hFile,
            long liDistanceToMove,
            out long lpNewFilePointer,
            uint dwMoveMethod);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool DeviceIoControl(
            IntPtr hDevice,
            uint dwIoControlCode,
            IntPtr lpInBuffer,
            uint nInBufferSize,
            IntPtr lpOutBuffer,
            uint nOutBufferSize,
            out uint lpBytesReturned,
            IntPtr lpOverlapped);

        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool CloseHandle(IntPtr hObject);

        public const uint GENERIC_READ = 0x80000000;
        public const uint FILE_SHARE_READ = 0x00000001;
        public const uint FILE_SHARE_WRITE = 0x00000002;
        public const uint OPEN_EXISTING = 3;
        public const uint FILE_FLAG_BACKUP_SEMANTICS = 0x02000000;
        public const uint FSCTL_GET_NTFS_VOLUME_DATA = 0x00090064;
        public const uint FILE_BEGIN = 0;
        public static readonly IntPtr INVALID_HANDLE_VALUE = new IntPtr(-1);
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct NTFS_VOLUME_DATA_BUFFER
    {
        public long VolumeSerialNumber;
        public long NumberSectors;
        public long TotalClusters;
        public long FreeClusters;
        public long TotalReserved;
        public uint BytesPerSector;
        public uint BytesPerCluster;
        public uint BytesPerFileRecordSegment;
        public uint ClustersPerFileRecordSegment;
        public long MftValidDataLength;
        public long MftStartLcn;
        public long Mft2StartLcn;
        public long MftZoneStart;
        public long MftZoneEnd;
    }

    public class RawDiskReader : IDisposable
    {
        private IntPtr _volumeHandle = NativeMethods.INVALID_HANDLE_VALUE;
        private string _volumePath;
        private bool _disposed = false;

        public NTFS_VOLUME_DATA_BUFFER VolumeData { get; private set; }
        public bool IsOpen { get; private set; }
        public string LastError { get; private set; }

        public bool OpenVolume(string driveLetter)
        {
            _volumePath = @"\\.\" + driveLetter.TrimEnd('\\');
            _volumeHandle = NativeMethods.CreateFile(
                _volumePath,
                NativeMethods.GENERIC_READ,
                NativeMethods.FILE_SHARE_READ | NativeMethods.FILE_SHARE_WRITE,
                IntPtr.Zero,
                NativeMethods.OPEN_EXISTING,
                NativeMethods.FILE_FLAG_BACKUP_SEMANTICS,
                IntPtr.Zero);

            if (_volumeHandle == NativeMethods.INVALID_HANDLE_VALUE)
            {
                LastError = "CreateFile failed: " + new Win32Exception(Marshal.GetLastWin32Error()).Message;
                IsOpen = false;
                return false;
            }

            IsOpen = true;
            return ReadVolumeData();
        }

        private bool ReadVolumeData()
        {
            int size = Marshal.SizeOf(typeof(NTFS_VOLUME_DATA_BUFFER));
            IntPtr buffer = Marshal.AllocHGlobal(size);

            try
            {
                uint bytesReturned;
                bool result = NativeMethods.DeviceIoControl(
                    _volumeHandle,
                    NativeMethods.FSCTL_GET_NTFS_VOLUME_DATA,
                    IntPtr.Zero, 0,
                    buffer, (uint)size,
                    out bytesReturned,
                    IntPtr.Zero);

                if (!result)
                {
                    LastError = "FSCTL_GET_NTFS_VOLUME_DATA failed: " + new Win32Exception(Marshal.GetLastWin32Error()).Message;
                    return false;
                }

                VolumeData = (NTFS_VOLUME_DATA_BUFFER)Marshal.PtrToStructure(buffer, typeof(NTFS_VOLUME_DATA_BUFFER));
                return true;
            }
            finally
            {
                Marshal.FreeHGlobal(buffer);
            }
        }

        public bool ExtractMFT(string outputPath, Action<long, long> progressCallback = null)
        {
            if (!IsOpen)
            {
                LastError = "Volume not open";
                return false;
            }

            long mftOffset = VolumeData.MftStartLcn * VolumeData.BytesPerCluster;
            long mftSize = VolumeData.MftValidDataLength;

            if (mftSize <= 0)
            {
                LastError = "MFT size is zero or invalid";
                return false;
            }

            long newPos;
            if (!NativeMethods.SetFilePointerEx(_volumeHandle, mftOffset, out newPos, NativeMethods.FILE_BEGIN))
            {
                LastError = "SetFilePointerEx failed: " + new Win32Exception(Marshal.GetLastWin32Error()).Message;
                return false;
            }

            int bufferSize = (int)VolumeData.BytesPerCluster * 64;
            if (bufferSize <= 0 || bufferSize > 4 * 1024 * 1024)
                bufferSize = 1024 * 1024;

            byte[] buffer = new byte[bufferSize];
            long totalRead = 0;

            try
            {
                using (FileStream outStream = new FileStream(outputPath, FileMode.Create, FileAccess.Write, FileShare.None, bufferSize))
                {
                    while (totalRead < mftSize)
                    {
                        uint toRead = (uint)Math.Min(bufferSize, mftSize - totalRead);
                        uint bytesRead;

                        if (!NativeMethods.ReadFile(_volumeHandle, buffer, toRead, out bytesRead, IntPtr.Zero))
                        {
                            LastError = "ReadFile failed at offset " + (mftOffset + totalRead) + ": " + new Win32Exception(Marshal.GetLastWin32Error()).Message;
                            return false;
                        }

                        if (bytesRead == 0) break;

                        outStream.Write(buffer, 0, (int)bytesRead);
                        totalRead += bytesRead;

                        if (progressCallback != null && totalRead % (10 * 1024 * 1024) == 0)
                            progressCallback(totalRead, mftSize);
                    }
                }

                return true;
            }
            catch (Exception ex)
            {
                LastError = "Write error: " + ex.Message;
                return false;
            }
        }

        public byte[] ReadSectors(long offset, int count)
        {
            if (!IsOpen) return null;

            long newPos;
            NativeMethods.SetFilePointerEx(_volumeHandle, offset, out newPos, NativeMethods.FILE_BEGIN);

            byte[] buffer = new byte[count];
            uint bytesRead;
            NativeMethods.ReadFile(_volumeHandle, buffer, (uint)count, out bytesRead, IntPtr.Zero);

            if (bytesRead < count)
            {
                byte[] result = new byte[bytesRead];
                Array.Copy(buffer, result, bytesRead);
                return result;
            }

            return buffer;
        }

        public void Dispose()
        {
            if (!_disposed)
            {
                if (_volumeHandle != NativeMethods.INVALID_HANDLE_VALUE)
                {
                    NativeMethods.CloseHandle(_volumeHandle);
                    _volumeHandle = NativeMethods.INVALID_HANDLE_VALUE;
                }
                _disposed = true;
            }
        }
    }

    public class MFTInfo
    {
        public long MftStartLcn { get; set; }
        public long MftValidDataLength { get; set; }
        public uint BytesPerCluster { get; set; }
        public uint BytesPerSector { get; set; }
        public uint BytesPerFileRecord { get; set; }
        public long TotalClusters { get; set; }
        public long VolumeSerialNumber { get; set; }
    }
}
'@

    try {
        Add-Type -TypeDefinition $csharpCode -Language CSharp -ErrorAction Stop
        Write-ForensicLog -Phase "INIT" -Action "CSHARP_HELPERS_LOADED" -Target "MaPeriT.RawDiskReader" -Result "OK"
        return $true
    } catch {
        if ($_.Exception.Message -like "*already exists*" -or $_.Exception.Message -like "*ya existe*" -or $_.Exception.Message -like "*MaPeriT*") {
            Write-ForensicLog -Phase "INIT" -Action "CSHARP_HELPERS_YA_CARGADOS" -Target "MaPeriT.RawDiskReader" -Result "OK"
            return $true
        }
        Write-ForensicLog -Phase "INIT" -Action "CSHARP_HELPERS_ERROR" -Target $_.Exception.Message -Result "ERROR"
        return $false
    }
}

function Extract-MFTRaw {
    param(
        [string]$DriveLetter = "C:",
        [string]$OutputPath
    )

    Write-ForensicLog -Phase "FILESYSTEM" -Action "MFT_EXTRACCION_RAW" -Target $DriveLetter

    $reader = $null
    try {
        $reader = New-Object MaPeriT.RawDiskReader

        if (-not $reader.OpenVolume($DriveLetter)) {
            Write-ForensicLog -Phase "FILESYSTEM" -Action "MFT_OPEN_VOLUME_FAILED" -Target $reader.LastError -Result "ERROR"
            return $false
        }

        $volData = $reader.VolumeData
        $mftSizeMB = [math]::Round($volData.MftValidDataLength / 1MB, 2)
        Write-ForensicLog -Phase "FILESYSTEM" -Action "MFT_INFO" -Target "Tamano: ${mftSizeMB}MB, LCN: $($volData.MftStartLcn), Cluster: $($volData.BytesPerCluster)B"

        Write-Host "  [*] Extrayendo MFT ($mftSizeMB MB)..." -ForegroundColor Yellow

        $progressAction = [Action[long,long]]{
            param($current, $total)
            $pct = [math]::Round(($current / $total) * 100)
            Write-Host "`r  [*] MFT: $pct% ($([math]::Round($current/1MB))/$([math]::Round($total/1MB)) MB)" -NoNewline -ForegroundColor Yellow
        }

        $success = $reader.ExtractMFT($OutputPath, $progressAction)
        Write-Host ""

        if ($success) {
            Write-ForensicLog -Phase "FILESYSTEM" -Action "MFT_EXTRAIDA" -Target $OutputPath -Result "OK"
            return $true
        } else {
            Write-ForensicLog -Phase "FILESYSTEM" -Action "MFT_EXTRACCION_FALLIDA" -Target $reader.LastError -Result "ERROR"
            return $false
        }
    } catch {
        Write-ForensicLog -Phase "FILESYSTEM" -Action "MFT_EXCEPCION" -Target $_.Exception.Message -Result "ERROR"
        return $false
    } finally {
        if ($reader) { $reader.Dispose() }
    }
}

function Get-NTFSVolumeInfo {
    param(
        [string]$DriveLetter = "C:"
    )

    $reader = $null
    try {
        $reader = New-Object MaPeriT.RawDiskReader
        if (-not $reader.OpenVolume($DriveLetter)) {
            return $null
        }

        $volData = $reader.VolumeData
        return [PSCustomObject]@{
            VolumeSerialNumber       = "0x{0:X16}" -f $volData.VolumeSerialNumber
            MftStartLcn              = $volData.MftStartLcn
            MftValidDataLengthMB     = [math]::Round($volData.MftValidDataLength / 1MB, 2)
            BytesPerSector           = $volData.BytesPerSector
            BytesPerCluster          = $volData.BytesPerCluster
            BytesPerFileRecord       = $volData.BytesPerFileRecordSegment
            TotalClusters            = $volData.TotalClusters
            FreeClusters             = $volData.FreeClusters
            TotalSizeMB              = [math]::Round(($volData.TotalClusters * $volData.BytesPerCluster) / 1MB, 2)
            FreeSizeMB               = [math]::Round(($volData.FreeClusters * $volData.BytesPerCluster) / 1MB, 2)
        }
    } catch {
        return $null
    } finally {
        if ($reader) { $reader.Dispose() }
    }
}

#endregion ===== CSharpHelpers.ps1 =====

#region ===== FileAcquisition.ps1 =====
#region ============================================================
# MaPeriT.SW - ADQUISICION DE FICHEROS
# VSS snapshots, copia de ficheros bloqueados, raw copy
# ============================================================

function New-ForensicVSSSnapshot {
    param(
        [string]$Volume = "C:\"
    )
    Write-ForensicLog -Phase "VSS" -Action "CREANDO_SNAPSHOT" -Target $Volume

    try {
        $shadowResult = Invoke-CimMethod -Namespace root/cimv2 -ClassName Win32_ShadowCopy -MethodName Create -Arguments @{Volume=$Volume; Context="ClientAccessible"} -ErrorAction Stop

        if ($shadowResult.ReturnValue -eq 0) {
            $shadowId = $shadowResult.ShadowID
            $shadow = Get-CimInstance -ClassName Win32_ShadowCopy | Where-Object { $_.ID -eq $shadowId }

            if ($shadow) {
                $Script:VSSSnapshot = @{
                    ID         = $shadow.ID
                    CimObject  = $shadow
                    DevicePath = $shadow.DeviceObject
                    VolumeName = $Volume
                    Created    = Get-Date
                }

                $Script:VSSShadowPath = $shadow.DeviceObject + "\"
                Write-ForensicLog -Phase "VSS" -Action "SNAPSHOT_CREADO" -Target $shadow.DeviceObject -Result "OK"
                return $true
            }
        }

        Write-ForensicLog -Phase "VSS" -Action "SNAPSHOT_FALLIDO" -Target "ReturnValue: $($shadowResult.ReturnValue)" -Result "ERROR"
        return $false
    } catch {
        Write-ForensicLog -Phase "VSS" -Action "SNAPSHOT_ERROR" -Target $Volume -Result "ERROR" -ErrorMessage $_.Exception.Message

        try {
            Write-ForensicLog -Phase "VSS" -Action "INTENTANDO_VSSADMIN" -Target $Volume
            $output = & vssadmin create shadow /for=$Volume 2>&1
            $matchPath = $output | Select-String "Shadow Copy Volume Name"
            $matchId   = $output | Select-String "Shadow Copy ID:\s*(\{[^}]+\})"
            if ($matchPath) {
                $devicePath = ($matchPath -split ":\s+")[1].Trim()
                $shadowGuid = if ($matchId -and $matchId.Matches.Count -gt 0) { $matchId.Matches[0].Groups[1].Value } else { $null }
                $Script:VSSSnapshot = @{
                    ID         = if ($shadowGuid) { $shadowGuid } else { "vssadmin" }
                    CimObject  = $null
                    DevicePath = $devicePath
                    VolumeName = $Volume
                    Created    = Get-Date
                }
                $Script:VSSShadowPath = $devicePath + "\"
                Write-ForensicLog -Phase "VSS" -Action "SNAPSHOT_CREADO_VSSADMIN" -Target $devicePath -Result "OK"
                return $true
            }
        } catch {
            Write-ForensicLog -Phase "VSS" -Action "VSSADMIN_ERROR" -Target $Volume -Result "ERROR" -ErrorMessage $_.Exception.Message
        }

        return $false
    }
}

function Remove-ForensicVSSSnapshot {
    if (-not $Script:VSSSnapshot) {
        return
    }

    Write-ForensicLog -Phase "VSS" -Action "ELIMINANDO_SNAPSHOT" -Target $Script:VSSSnapshot.DevicePath

    try {
        if ($Script:VSSSnapshot.CimObject) {
            Invoke-CimMethod -InputObject $Script:VSSSnapshot.CimObject -MethodName Delete -ErrorAction SilentlyContinue | Out-Null
        } elseif ($Script:VSSSnapshot.ID -and $Script:VSSSnapshot.ID -ne "vssadmin") {
            & vssadmin delete shadows /shadow="$($Script:VSSSnapshot.ID)" /quiet 2>&1 | Out-Null
        } else {
            & vssadmin delete shadows /for=$($Script:VSSSnapshot.VolumeName) /oldest /quiet 2>&1 | Out-Null
        }
        Write-ForensicLog -Phase "VSS" -Action "SNAPSHOT_ELIMINADO" -Target $Script:VSSSnapshot.DevicePath -Result "OK"
    } catch {
        Write-ForensicLog -Phase "VSS" -Action "SNAPSHOT_ELIMINAR_ERROR" -Target $Script:VSSSnapshot.DevicePath -Result "WARNING" -ErrorMessage $_.Exception.Message
    }
}

function Copy-ForensicFile {
    param(
        [string]$SourcePath,
        [string]$DestPath,
        [string]$Phase = "COPY",
        [switch]$UseVSSFirst
    )

    if (Test-PathExcluded -Path $SourcePath) {
        Write-ForensicLog -Phase $Phase -Action "EXCLUIDO_POR_PARAMETRO" -Target $SourcePath -Result "SKIP"
        return $false
    }

    if (-not (Test-Path $SourcePath)) {
        Write-ForensicLog -Phase $Phase -Action "FICHERO_NO_ENCONTRADO" -Target $SourcePath -Result "WARNING"
        return $false
    }

    $destDir = Split-Path $DestPath -Parent
    if (-not (Test-Path $destDir)) {
        New-Item -ItemType Directory -Path $destDir -Force | Out-Null
    }

    $method = "DirectCopy"

    if ($UseVSSFirst -and $Script:VSSShadowPath) {
        $success = Copy-FromVSS -SourcePath $SourcePath -DestPath $DestPath -Phase $Phase
        if ($success) { return $true }
    }

    try {
        Copy-Item -Path $SourcePath -Destination $DestPath -Force -ErrorAction Stop
        $method = "DirectCopy"
    } catch {
        if ($Script:VSSShadowPath) {
            $success = Copy-FromVSS -SourcePath $SourcePath -DestPath $DestPath -Phase $Phase
            if ($success) { return $true }
        }

        try {
            Copy-WithRawStream -SourcePath $SourcePath -DestPath $DestPath
            $method = "RawStream"
        } catch {
            Write-ForensicLog -Phase $Phase -Action "COPIA_FALLIDA" -Target $SourcePath -Result "ERROR" -ErrorMessage $_.Exception.Message
            return $false
        }
    }

    try {
        $hash = Get-ForensicHash -FilePath $DestPath
        $size = (Get-Item $DestPath).Length
        Add-ChainOfCustodyEntry -SourcePath $SourcePath -DestPath $DestPath -Hash $hash -Method $method -FileSize $size

        if ($hash -notmatch '^[A-Fa-f0-9]{64}$') {
            # El fichero se copio pero su hash no se pudo verificar (p.ej. un antivirus lo
            # elimino/puso en cuarentena justo tras la copia). Ya queda constancia en la
            # cadena de custodia (marcado INVALIDO), pero esta copia NO se considera fiable
            # y no debe contarse como evidencia correctamente adquirida.
            Write-ForensicLog -Phase $Phase -Action "HASH_POST_COPIA_NO_VALIDO" -Target "$DestPath ('$hash')" -Result "ERROR"
            return $false
        }

        Write-ForensicLog -Phase $Phase -Action "COPIADO" -Target (Split-Path $SourcePath -Leaf) -Result "OK"
        return $true
    } catch {
        Write-ForensicLog -Phase $Phase -Action "HASH_POST_COPIA_ERROR" -Target $DestPath -Result "ERROR" -ErrorMessage $_.Exception.Message
        return $false
    }
}

function Copy-FromVSS {
    param(
        [string]$SourcePath,
        [string]$DestPath,
        [string]$Phase = "VSS"
    )

    if (-not $Script:VSSShadowPath) { return $false }

    # Esta funcion solo sabe traducir rutas locales con letra de unidad (p.ej. "C:\...").
    # Si en el futuro se le pasa una ruta UNC u otro formato, abortar de forma explicita
    # en vez de construir una ruta VSS incorrecta a partir de un Substring mal alineado.
    if ($SourcePath.Length -lt 3 -or $SourcePath[1] -ne ':') {
        Write-ForensicLog -Phase $Phase -Action "VSS_RUTA_NO_SOPORTADA" -Target $SourcePath -Result "WARNING" -ErrorMessage "Copy-FromVSS solo admite rutas locales con letra de unidad (X:\...)."
        return $false
    }

    try {
        $relativePath = $SourcePath.Substring(2)
        $vssPath = $Script:VSSShadowPath + $relativePath.TrimStart("\")

        if (Test-Path $vssPath) {
            Copy-Item -Path $vssPath -Destination $DestPath -Force -ErrorAction SilentlyContinue
            if (Test-Path $DestPath) {
                $hash = Get-ForensicHash -FilePath $DestPath
                $size = (Get-Item $DestPath).Length
                Add-ChainOfCustodyEntry -SourcePath $SourcePath -DestPath $DestPath -Hash $hash -Method "VSS" -FileSize $size
                if ($hash -notmatch '^[A-Fa-f0-9]{64}$') {
                    Write-ForensicLog -Phase $Phase -Action "VSS_HASH_NO_VALIDO" -Target "$DestPath ('$hash')" -Result "ERROR"
                    return $false
                }
                Write-ForensicLog -Phase $Phase -Action "COPIADO_VSS" -Target (Split-Path $SourcePath -Leaf) -Result "OK"
                return $true
            }
        }
    } catch {
        Write-ForensicLog -Phase $Phase -Action "VSS_COPIA_ERROR" -Target $SourcePath -Result "WARNING" -ErrorMessage $_.Exception.Message
    }

    return $false
}

function Copy-WithRawStream {
    param(
        [string]$SourcePath,
        [string]$DestPath,
        [int]$BufferSize = 1048576
    )

    $sourceStream = $null
    $destStream = $null

    try {
        $sourceStream = [System.IO.File]::Open(
            $SourcePath,
            [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read,
            [System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete
        )
        $destStream = [System.IO.File]::Create($DestPath)

        $buffer = New-Object byte[] $BufferSize
        while (($read = $sourceStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $destStream.Write($buffer, 0, $read)
        }
    } finally {
        if ($sourceStream) { $sourceStream.Close(); $sourceStream.Dispose() }
        if ($destStream) { $destStream.Close(); $destStream.Dispose() }
    }
}

function Copy-ForensicDirectory {
    param(
        [string]$SourceDir,
        [string]$DestDir,
        [string]$Phase,
        [string]$Filter = "*",
        [switch]$Recurse
    )

    if (-not (Test-Path $SourceDir)) {
        Write-ForensicLog -Phase $Phase -Action "DIRECTORIO_NO_ENCONTRADO" -Target $SourceDir -Result "WARNING"
        return 0
    }

    if (-not (Test-Path $DestDir)) {
        New-Item -ItemType Directory -Path $DestDir -Force | Out-Null
    }

    $copied = 0
    $params = @{
        Path = $SourceDir
        Filter = $Filter
        File = $true
        ErrorAction = "SilentlyContinue"
    }
    if ($Recurse) { $params.Recurse = $true }

    $files = Get-ChildItem @params
    foreach ($file in $files) {
        # Comprobar exclusion ANTES de crear la carpeta destino: antes se creaba la
        # estructura de directorios completa para cada fichero (incluidos los excluidos
        # por -ExcludePaths) y solo Copy-ForensicFile comprobaba la exclusion despues,
        # dejando carpetas vacias residuales en la salida para rutas que el propio
        # parametro pedia minimizar.
        if (Test-PathExcluded -Path $file.FullName) {
            Write-ForensicLog -Phase $Phase -Action "EXCLUIDO_POR_PARAMETRO" -Target $file.FullName -Result "SKIP"
            continue
        }

        $relativePath = $file.FullName.Substring($SourceDir.Length).TrimStart("\")
        $destPath = Join-Path $DestDir $relativePath
        $destFileDir = Split-Path $destPath -Parent
        if (-not (Test-Path $destFileDir)) {
            New-Item -ItemType Directory -Path $destFileDir -Force | Out-Null
        }

        $success = Copy-ForensicFile -SourcePath $file.FullName -DestPath $destPath -Phase $Phase
        if ($success) { $copied++ }
    }

    return $copied
}

function Get-UserProfiles {
    $profiles = @()
    $usersDir = "$env:SystemDrive\Users"

    if (-not (Test-Path $usersDir)) { return $profiles }

    $excludeDirs = @("Public", "Default", "Default User", "All Users")
    $dirs = Get-ChildItem -Path $usersDir -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -notin $excludeDirs }

    foreach ($dir in $dirs) {
        $ntuser = Join-Path $dir.FullName "NTUSER.DAT"
        $sid = ""
        try {
            $userObj = New-Object System.Security.Principal.NTAccount($dir.Name)
            $sid = $userObj.Translate([System.Security.Principal.SecurityIdentifier]).Value
        } catch { }

        $profiles += [PSCustomObject]@{
            Username    = $dir.Name
            ProfilePath = $dir.FullName
            SID         = $sid
            HasNTUSER   = Test-Path $ntuser
        }
    }

    return $profiles
}

$Script:LoadedHives = @()

function Mount-OfflineUserHives {
    $profiles = Get-UserProfiles
    foreach ($profile in $profiles) {
        $sid = $profile.SID
        if (-not $sid) { continue }
        if (Test-Path "Registry::HKU\$sid" -ErrorAction SilentlyContinue) { continue }

        $ntuser = Join-Path $profile.ProfilePath "NTUSER.DAT"
        if (-not (Test-Path $ntuser)) { continue }

        $tempKey = "HKU\MAPE_$($profile.Username)"

        # Si una ejecucion anterior interrumpida dejo esta clave montada, intentar liberarla
        # primero. Si no se puede, no seguir: cargar sobre una clave ya existente falla y
        # antes simplemente se perdia el usuario en silencio sin ningun aviso.
        if (Test-Path "Registry::$tempKey" -ErrorAction SilentlyContinue) {
            Write-ForensicLog -Phase "REGISTRY" -Action "HIVE_YA_MONTADO_PREVIO" -Target "$tempKey (residuo de una ejecucion anterior)" -Result "WARNING"
            & reg.exe unload $tempKey 2>&1 | Out-Null
            if (Test-Path "Registry::$tempKey" -ErrorAction SilentlyContinue) {
                Write-ForensicLog -Phase "REGISTRY" -Action "HIVE_MONTAR_OMITIDO" -Target $profile.Username -Result "ERROR" -ErrorMessage "No se pudo liberar el montaje residual $tempKey; se omite este usuario en Fase 4/10 (UserAssist, MRU, RDP History, COM Hijacking)."
                continue
            }
        }

        # NUNCA se monta el NTUSER.DAT original del equipo analizado: "reg load" abre el
        # fichero en lectura-escritura y Windows puede reescribirlo al cargarlo o al
        # desmontarlo (aplicar sus .LOG1/.LOG2, actualizar cabeceras). Se monta una copia
        # de trabajo en la carpeta de salida, que se borra al desmontar.
        $workDir = Join-Path $Script:SessionInfo.OutputPath "Logs\_hives_trabajo\$($profile.Username)"
        try {
            New-Item -ItemType Directory -Path $workDir -Force | Out-Null
            $workHive = Join-Path $workDir "NTUSER.DAT"
            foreach ($ext in @("", ".LOG1", ".LOG2")) {
                if (Test-Path -LiteralPath "$ntuser$ext") {
                    Copy-Item -LiteralPath "$ntuser$ext" -Destination "$workHive$ext" -Force -ErrorAction Stop
                    (Get-Item -LiteralPath "$workHive$ext" -Force).Attributes = 'Normal'
                }
            }
        } catch {
            Write-ForensicLog -Phase "REGISTRY" -Action "HIVE_COPIA_TRABAJO_FALLIDA" -Target $profile.Username -Result "ERROR" -ErrorMessage $_.Exception.Message
            Remove-Item -LiteralPath $workDir -Recurse -Force -ErrorAction SilentlyContinue
            continue
        }

        try {
            $result = & reg.exe load $tempKey $workHive 2>&1
            if ($LASTEXITCODE -eq 0) {
                $Script:LoadedHives += @{ Username = $profile.Username; Key = $tempKey; SID = $sid; MountedSID = "MAPE_$($profile.Username)"; WorkDir = $workDir }
                Write-ForensicLog -Phase "REGISTRY" -Action "HIVE_MONTADO" -Target "$($profile.Username) -> $tempKey (copia de trabajo)" -Result "OK"
            } else {
                Remove-Item -LiteralPath $workDir -Recurse -Force -ErrorAction SilentlyContinue
                # reg.exe fallando con codigo de salida no nulo NO lanza excepcion en PowerShell,
                # asi que sin este else el fallo pasaba totalmente desapercibido: el usuario
                # quedaba excluido de UserAssist/MRU/RDP History/COM Hijacking sin ningun aviso.
                Write-ForensicLog -Phase "REGISTRY" -Action "HIVE_MONTAR_FALLIDO" -Target $profile.Username -Result "ERROR" -ErrorMessage "reg.exe load devolvio codigo $LASTEXITCODE : $($result -join ' ')"
            }
        } catch {
            Write-ForensicLog -Phase "REGISTRY" -Action "HIVE_MONTAR_ERROR" -Target $profile.Username -Result "ERROR" -ErrorMessage $_.Exception.Message
        }
    }
}

$Script:HivesNoDesmontadas = @()

function Dismount-OfflineUserHives {
    foreach ($hive in $Script:LoadedHives) {
        $unloaded = $false
        try {
            [gc]::Collect()
            Start-Sleep -Milliseconds 500
            & reg.exe unload $hive.Key 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) {
                $unloaded = $true
            } else {
                Write-ForensicLog -Phase "REGISTRY" -Action "HIVE_DESMONTAR_REINTENTO" -Target $hive.Key -Result "WARNING"
                [gc]::Collect()
                Start-Sleep -Seconds 2
                & reg.exe unload $hive.Key 2>&1 | Out-Null
                if ($LASTEXITCODE -eq 0) {
                    $unloaded = $true
                } else {
                    # Tercer y ultimo intento con una espera mayor antes de declarar el fallo.
                    [gc]::Collect()
                    Start-Sleep -Seconds 3
                    & reg.exe unload $hive.Key 2>&1 | Out-Null
                    $unloaded = ($LASTEXITCODE -eq 0)
                }
            }
        } catch {
            Write-ForensicLog -Phase "REGISTRY" -Action "HIVE_DESMONTAR_ERROR" -Target $hive.Key -Result "WARNING" -ErrorMessage $_.Exception.Message
        }

        if ($unloaded) {
            Write-ForensicLog -Phase "REGISTRY" -Action "HIVE_DESMONTADO" -Target $hive.Key -Result "OK"
            if ($hive.WorkDir) { Remove-Item -LiteralPath $hive.WorkDir -Recurse -Force -ErrorAction SilentlyContinue }
        } else {
            # CRITICO: sin esta comprobacion, un fallo en el reintento se registraba solo como
            # WARNING y la clave HKEY_USERS\MAPE_<usuario> podia quedar montada de forma
            # PERMANENTE en el registro vivo del equipo analizado -- una escritura/modificacion
            # real del sistema objetivo, en contra de la promesa de "solo lectura" de la
            # herramienta, y sin ningun ERROR que lo delatase. Ademas puede impedir que el
            # perfil de ese usuario cargue correctamente tras la adquisicion.
            $Script:HivesNoDesmontadas += $hive.Key
            if ($Script:SessionInfo) { $Script:SessionInfo.TotalErrors++ }
            Write-ForensicLog -Phase "REGISTRY" -Action "HIVE_DESMONTAR_FALLO_PERMANENTE" -Target $hive.Key -Result "ERROR" -ErrorMessage "La clave $($hive.Key) sigue montada en HKEY_USERS del equipo analizado tras 3 intentos. Requiere desmontaje manual con 'reg unload $($hive.Key)' antes de devolver el equipo. Es una copia de trabajo (el NTUSER.DAT original no se ha montado), pero la clave sigue presente en el registro del equipo."
        }
    }
    $Script:LoadedHives = @()
    # Carpeta de copias de trabajo: se elimina si ha quedado vacia
    $wb = Join-Path $Script:SessionInfo.OutputPath "Logs\_hives_trabajo"
    if ((Test-Path -LiteralPath $wb) -and -not (Get-ChildItem -LiteralPath $wb -Force -ErrorAction SilentlyContinue)) {
        Remove-Item -LiteralPath $wb -Force -ErrorAction SilentlyContinue
    }
}

function Get-EffectiveHKUSID {
    param([string]$SID, [string]$Username)
    if (Test-Path "Registry::HKU\$SID" -ErrorAction SilentlyContinue) { return $SID }
    $mounted = $Script:LoadedHives | Where-Object { $_.Username -eq $Username }
    if ($mounted) { return $mounted.MountedSID }
    return $null
}

function Save-RegistryHive {
    param(
        [string]$HiveKey,
        [string]$OutputPath,
        [string]$Phase = "REGISTRY"
    )

    try {
        $destDir = Split-Path $OutputPath -Parent
        if (-not (Test-Path $destDir)) {
            New-Item -ItemType Directory -Path $destDir -Force | Out-Null
        }

        $result = & reg.exe save $HiveKey $OutputPath /y 2>&1
        if ($LASTEXITCODE -eq 0) {
            $hash = Get-ForensicHash -FilePath $OutputPath
            $size = (Get-Item $OutputPath).Length
            Add-ChainOfCustodyEntry -SourcePath $HiveKey -DestPath $OutputPath -Hash $hash -Method "reg.exe save" -FileSize $size
            Write-ForensicLog -Phase $Phase -Action "HIVE_GUARDADO" -Target $HiveKey -Result "OK"
            return $true
        } else {
            Write-ForensicLog -Phase $Phase -Action "HIVE_REG_FALLIDO" -Target $HiveKey -Result "WARNING" -ErrorMessage ($result -join " ")
            return $false
        }
    } catch {
        Write-ForensicLog -Phase $Phase -Action "HIVE_ERROR" -Target $HiveKey -Result "ERROR" -ErrorMessage $_.Exception.Message
        return $false
    }
}

#endregion ===== FileAcquisition.ps1 =====

#region ===== Initialize.ps1 =====
#region ============================================================
# MaPeriT.SW - INICIALIZACION
# Verificacion de privilegios, estructura de directorios, sesion
# ============================================================

function Test-AdminPrivileges {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-Prerequisites {
    $issues = @()

    if (-not (Test-AdminPrivileges)) {
        $issues += "Se requieren privilegios de Administrador. Ejecute el script como Administrador."
    }

    if ($PSVersionTable.PSVersion -lt $Script:MIN_PS_VER) {
        $issues += "Se requiere PowerShell $($Script:MIN_PS_VER) o superior. Version actual: $($PSVersionTable.PSVersion)"
    }

    $osInfo = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue
    if ($osInfo -and $osInfo.Caption -notlike "*Windows*") {
        $issues += "Esta herramienta solo es compatible con sistemas Windows."
    }

    return $issues
}

function Initialize-OutputStructure {
    param(
        [string]$OutputPath
    )

    if (-not (Test-Path $OutputPath)) {
        New-Item -ItemType Directory -Path $OutputPath -Force | Out-Null
    }

    foreach ($dirName in $Script:OutputDirs.Values) {
        $dirPath = Join-Path $OutputPath $dirName
        if (-not (Test-Path $dirPath)) {
            New-Item -ItemType Directory -Path $dirPath -Force | Out-Null
        }
    }

    # Subdirectorios especificos
    $subDirs = @(
        (Join-Path $OutputPath "$($Script:OutputDirs.Registry)\Hives"),
        (Join-Path $OutputPath "$($Script:OutputDirs.Registry)\Hives\Users"),
        (Join-Path $OutputPath "$($Script:OutputDirs.Registry)\ExtractedValues"),
        (Join-Path $OutputPath "$($Script:OutputDirs.EventLogs)\RawLogs"),
        (Join-Path $OutputPath "$($Script:OutputDirs.EventLogs)\Analysis"),
        (Join-Path $OutputPath "$($Script:OutputDirs.Filesystem)\Prefetch"),
        (Join-Path $OutputPath "$($Script:OutputDirs.Filesystem)\RecentFiles"),
        (Join-Path $OutputPath "$($Script:OutputDirs.Filesystem)\JumpLists"),
        (Join-Path $OutputPath "$($Script:OutputDirs.Filesystem)\RecycleBin"),
        (Join-Path $OutputPath "$($Script:OutputDirs.Filesystem)\Thumbcache"),
        (Join-Path $OutputPath "$($Script:OutputDirs.Filesystem)\SRUM"),
        (Join-Path $OutputPath "$($Script:OutputDirs.Filesystem)\ActivitiesCache"),
        (Join-Path $OutputPath "$($Script:OutputDirs.Filesystem)\PowerShellHistory"),
        (Join-Path $OutputPath "$($Script:OutputDirs.Filesystem)\StartupItems"),
        (Join-Path $OutputPath "$($Script:OutputDirs.Volatile)\MemoryDump")
    )

    foreach ($subDir in $subDirs) {
        if (-not (Test-Path $subDir)) {
            New-Item -ItemType Directory -Path $subDir -Force | Out-Null
        }
    }

    return $OutputPath
}

function Initialize-ForensicSession {
    param(
        [string]$CaseNumber,
        [string]$ExaminerName,
        [string]$OutputPath,
        [string]$ScriptPath,
        [string]$EvidenceDescription = "",
        [string]$HMACKey = ""
    )

    $computerInfo = Get-CimInstance Win32_ComputerSystem -ErrorAction SilentlyContinue
    $osInfo = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue

    $Script:SessionInfo = @{
        CaseNumber          = $CaseNumber
        ExaminerName        = $ExaminerName
        EvidenceDescription = $EvidenceDescription
        OutputPath          = $OutputPath
        HMACKey             = $HMACKey
        StartTime           = Get-Date
        StartTimeUTC        = (Get-Date).ToUniversalTime()
        TimeZone            = [System.TimeZoneInfo]::Local.DisplayName
        TimeZoneId          = [System.TimeZoneInfo]::Local.Id
        UTCOffset           = [System.TimeZoneInfo]::Local.BaseUtcOffset.ToString()
        ComputerName        = $env:COMPUTERNAME
        Domain              = if ($computerInfo) { $computerInfo.Domain } else { "N/A" }
        OSVersion           = if ($osInfo) { $osInfo.Caption } else { "N/A" }
        OSBuild             = if ($osInfo) { $osInfo.BuildNumber } else { "N/A" }
        PSVersion           = $PSVersionTable.PSVersion.ToString()
        ToolName            = $Script:TOOL_FULL
        ToolVersion         = $Script:TOOL_VERSION
        ScriptHash          = Get-ScriptSelfHash -ScriptPath $ScriptPath
        ScriptPath          = $ScriptPath
        PhasesCompleted     = @()
        PhasesSkipped       = @()
        PhasesFailed        = @()
        TotalErrors         = 0
        TotalWarnings       = 0
        TotalEvidences      = 0
        EndTime             = $null
    }

    return $Script:SessionInfo
}

function Show-SessionBanner {
    $banner = $Script:Banner -replace '\{VERSION\}', $Script:TOOL_VERSION
    Write-Host $banner -ForegroundColor DarkCyan
    Write-Host $Script:LegalDisclaimer -ForegroundColor Yellow

    Write-Host ""
    Write-Host "  ═══════════════════════════════════════════════════════════" -ForegroundColor White
    Write-Host "  DATOS DE LA SESION PERICIAL" -ForegroundColor White
    Write-Host "  ═══════════════════════════════════════════════════════════" -ForegroundColor White
    Write-Host "  Caso:           $($Script:SessionInfo.CaseNumber)" -ForegroundColor White
    Write-Host "  Perito:         $($Script:SessionInfo.ExaminerName)" -ForegroundColor White
    Write-Host "  Equipo:         $($Script:SessionInfo.ComputerName)" -ForegroundColor White
    Write-Host "  Dominio:        $($Script:SessionInfo.Domain)" -ForegroundColor White
    Write-Host "  SO:             $($Script:SessionInfo.OSVersion) (Build $($Script:SessionInfo.OSBuild))" -ForegroundColor White
    Write-Host "  Zona Horaria:   $($Script:SessionInfo.TimeZone)" -ForegroundColor White
    Write-Host "  Hora Inicio:    $($Script:SessionInfo.StartTime.ToString($Script:Defaults.TimeZoneFormat))" -ForegroundColor White
    Write-Host "  Hora UTC:       $($Script:SessionInfo.StartTimeUTC.ToString($Script:Defaults.DateFormat))" -ForegroundColor White
    Write-Host "  Hash Script:    $($Script:SessionInfo.ScriptHash)" -ForegroundColor White
    Write-Host "  PowerShell:     $($Script:SessionInfo.PSVersion)" -ForegroundColor White
    Write-Host "  Salida:         $($Script:SessionInfo.OutputPath)" -ForegroundColor White
    Write-Host "  ═══════════════════════════════════════════════════════════" -ForegroundColor White
    Write-Host ""
}

function Complete-ForensicSession {
    $Script:SessionInfo.EndTime = Get-Date
    $Script:SessionInfo.TotalEvidences = $Script:EvidenceCounter
    $duration = $Script:SessionInfo.EndTime - $Script:SessionInfo.StartTime

    Write-Host ""
    Write-Host "  ═══════════════════════════════════════════════════════════" -ForegroundColor Green
    Write-Host "  SESION PERICIAL COMPLETADA" -ForegroundColor Green
    Write-Host "  ═══════════════════════════════════════════════════════════" -ForegroundColor Green
    Write-Host "  Hora Fin:       $($Script:SessionInfo.EndTime.ToString($Script:Defaults.TimeZoneFormat))" -ForegroundColor White
    Write-Host "  Duracion:       $($duration.ToString('hh\:mm\:ss'))" -ForegroundColor White
    Write-Host "  Evidencias:     $($Script:SessionInfo.TotalEvidences)" -ForegroundColor White
    Write-Host "  Fases OK:       $($Script:SessionInfo.PhasesCompleted.Count)" -ForegroundColor Green
    if ($Script:SessionInfo.PhasesFailed.Count -gt 0) {
        Write-Host "  Fases FALLO:    $($Script:SessionInfo.PhasesFailed.Count)" -ForegroundColor Red
    }
    Write-Host "  Informe:        $(Join-Path $Script:SessionInfo.OutputPath "$($Script:OutputDirs.Report)\InformePericial.html")" -ForegroundColor Cyan
    Write-Host "  ═══════════════════════════════════════════════════════════" -ForegroundColor Green
    Write-Host ""
}

function Resolve-ServiceExecutablePath {
    <#
        Resuelve el ejecutable real a partir del PathName de un servicio de Windows
        (Win32_Service), incluyendo rutas SIN COMILLAS que contienen espacios y
        argumentos (el clasico patron de vulnerabilidad "unquoted service path").

        El enfoque anterior, usado en Fase 2 (verificacion de firma) y Fase 10
        (deteccion de servicios sin firma/sospechosos):
            $PathName -replace '^"([^"]+)".*','$1' -replace '\s+-.*$','' -replace '\s+/.*$',''
        solo funcionaba si el ejecutable iba entre comillas, o si los argumentos
        empezaban por "-"/"/". Con argumentos posicionales sin prefijo (p.ej.
        'C:\Program Files\App\svc.exe C:\config.xml') el resultado no era una ruta
        valida, Test-Path fallaba, y el servicio quedaba marcado "PathNotFound":
        EXCLUIDO EN SILENCIO de la verificacion de firma y del analisis de
        persistencia, justo el tipo de configuracion (ruta sin comillas con
        espacios) que un analisis forense deberia poder señalar, no ignorar.
    #>
    param([string]$PathName)

    if ([string]::IsNullOrWhiteSpace($PathName)) { return $null }

    # Caso con comillas: "C:\ruta con espacios\svc.exe" [argumentos]
    if ($PathName -match '^"([^"]+)"') {
        return $Matches[1]
    }

    # Sin espacios: toda la cadena es el ejecutable.
    if ($PathName -notmatch '\s') {
        return $PathName
    }

    # Sin comillas y CON espacios: probar limite por limite (algoritmo estandar de
    # resolucion de rutas de servicio sin comillas) hasta encontrar un fichero real.
    $tokens = $PathName -split ' '
    $candidate = $tokens[0]
    for ($i = 1; $i -lt $tokens.Count; $i++) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf -ErrorAction SilentlyContinue) {
            return $candidate
        }
        $candidate = "$candidate $($tokens[$i])"
    }
    if (Test-Path -LiteralPath $candidate -PathType Leaf -ErrorAction SilentlyContinue) {
        return $candidate
    }

    # Ultimo recurso: recorte por argumentos con prefijo -/ (comportamiento previo),
    # para no perder del todo el dato si ningun candidato intermedio existe en disco.
    return ($PathName -replace '\s+-.*$', '' -replace '\s+/.*$', '')
}

function Test-EventQueryTruncated {
    <#
        Comprueba si una consulta Get-WinEvent probablemente se trunco por haber alcanzado
        el limite -MaxEvents configurado. Antes, los CSV/el informe pericial nunca indicaban
        en ningun sitio que una fuente de eventos (Security, Sysmon, PowerShell, etc.) podia
        estar incompleta porque el sistema tenia mas eventos que el limite consultado -- un
        perito podia presentar sin saberlo un log parcial como si fuese completo.
    #>
    param(
        [int]$Count,
        [int]$MaxEvents,
        [string]$Phase,
        [string]$LogDescription
    )
    if ($Count -ge $MaxEvents) {
        Write-ForensicLog -Phase $Phase -Action "EVENTOS_POSIBLEMENTE_TRUNCADOS" -Target "${LogDescription}: se alcanzo el limite de $MaxEvents eventos consultados; puede haber eventos adicionales no capturados en esta adquisicion" -Result "WARNING"
        if ($Script:SessionInfo) { $Script:SessionInfo.TotalWarnings++ }
    }
}

function Test-NetworkIsolationState {
    <#
        Comprobacion de LECTURA UNICAMENTE: documenta el estado de red del equipo en el
        momento de la adquisicion (-NetworkIsolationCheck). No aisla ni desconecta nada;
        solo deja constancia en el acta pericial de si el equipo tenia conectividad activa,
        dato relevante para la cadena de custodia (p.ej. para descartar exfiltracion
        posterior a la incautacion).
    #>
    try {
        $adapters = Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'Up' }
        $established = @(Get-NetTCPConnection -State Established -ErrorAction SilentlyContinue)

        $result = [PSCustomObject]@{
            AdaptadoresActivos     = ($adapters | Select-Object -ExpandProperty Name) -join ", "
            NumAdaptadoresActivos  = @($adapters).Count
            ConexionesEstablecidas = $established.Count
            IPsRemotasUnicas       = ($established | Select-Object -ExpandProperty RemoteAddress -Unique | Where-Object { $_ -and $_ -ne '0.0.0.0' -and $_ -ne '::' }) -join ", "
            Timestamp              = (Get-Date).ToString($Script:Defaults.DateFormat)
        }

        if ($result.NumAdaptadoresActivos -gt 0 -or $result.ConexionesEstablecidas -gt 0) {
            Write-ForensicLog -Phase "FASE_1" -Action "RED_ACTIVA_EN_ADQUISICION" -Target "$($result.NumAdaptadoresActivos) adaptador(es) activo(s), $($result.ConexionesEstablecidas) conexion(es) TCP establecida(s)" -Result "WARNING"
        } else {
            Write-ForensicLog -Phase "FASE_1" -Action "RED_AISLADA_EN_ADQUISICION" -Target "Sin adaptadores activos ni conexiones TCP establecidas" -Result "OK"
        }
        return $result
    } catch {
        Write-ForensicLog -Phase "FASE_1" -Action "NETWORK_ISOLATION_CHECK_ERROR" -Target $_.Exception.Message -Result "WARNING"
        return $null
    }
}

function Test-PathExcluded {
    <#
        Comprueba si una ruta coincide con alguno de los fragmentos indicados en
        -ExcludePaths (comparacion por subcadena, sin distinguir mayusculas/minusculas).
        Se usa en los dos puntos centrales de copia (Copy-ForensicFile,
        Copy-ForensicDirectory) para minimizacion de datos cuando la autorizacion delimita
        el alcance de la adquisicion. Las exclusiones quedan siempre registradas como SKIP,
        nunca se omiten en silencio.
    #>
    param([string]$Path)

    if (-not $Script:ExcludePathPatterns -or $Script:ExcludePathPatterns.Count -eq 0) { return $false }
    if ([string]::IsNullOrEmpty($Path)) { return $false }

    foreach ($pattern in $Script:ExcludePathPatterns) {
        if ([string]::IsNullOrWhiteSpace($pattern)) { continue }
        if ($Path.IndexOf($pattern, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
            return $true
        }
    }
    return $false
}

function Invoke-DryRunPreflight {
    <#
        Modo -DryRun: chequeo de entorno de SOLO LECTURA. No crea VSS, no monta hives, no
        copia ni un solo byte. Comprueba prerrequisitos, localiza winpmem, enumera
        perfiles de usuario y verifica la presencia de las rutas de artefactos clave de
        cada fase, para detectar problemas de entorno ANTES de una adquisicion real.
    #>
    param([string]$ScriptPath)

    Write-Host ""
    Write-Host "  MaPeriT.SW - MODO DRY-RUN (simulacion de preflight, solo lectura)" -ForegroundColor Cyan
    Write-Host "  ===================================================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  Este modo NO crea VSS, NO monta hives de registro y NO copia ningun" -ForegroundColor Yellow
    Write-Host "  fichero. Solo comprueba que el entorno esta listo para una adquisicion" -ForegroundColor Yellow
    Write-Host "  real." -ForegroundColor Yellow
    Write-Host ""

    $issues = Test-Prerequisites
    if ($issues.Count -eq 0) {
        Write-Host "  [+] Prerrequisitos: OK (Administrador, PowerShell, SO Windows)" -ForegroundColor Green
    } else {
        Write-Host "  [-] Prerrequisitos con problemas:" -ForegroundColor Red
        $issues | ForEach-Object { Write-Host "      - $_" -ForegroundColor Red }
    }

    $winpmemPaths = @(
        (Join-Path (Split-Path $ScriptPath -Parent) "tools\winpmem_mini_x64.exe"),
        (Join-Path (Split-Path $ScriptPath -Parent) "winpmem_mini_x64.exe"),
        "C:\Tools\winpmem_mini_x64.exe"
    )
    $winpmem = $winpmemPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $winpmem) { $winpmem = Get-Command winpmem_mini_x64.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source }
    if ($winpmem) {
        Write-Host "  [+] WinPmem localizado: $winpmem" -ForegroundColor Green
    } else {
        Write-Host "  [!] WinPmem NO localizado - el volcado de RAM (Fase 1.2) se omitira con WARNING" -ForegroundColor Yellow
    }

    $profiles = Get-UserProfiles
    Write-Host "  [*] Perfiles de usuario detectados: $($profiles.Count)" -ForegroundColor White
    foreach ($p in $profiles) {
        $sidTag = if ($p.SID) { $p.SID } else { "SID no resuelto" }
        Write-Host "      - $($p.Username) ($sidTag) - NTUSER.DAT: $(if($p.HasNTUSER){'Si'}else{'No'})" -ForegroundColor Gray
    }

    $keyPaths = @(
        "C:\Windows\Prefetch",
        "C:\Windows\AppCompat\Programs\Amcache.hve",
        "C:\Windows\System32\sru\SRUDB.dat",
        "C:\Windows\System32\winevt\Logs",
        "C:\Windows\INF\setupapi.dev.log",
        "C:\`$Recycle.Bin"
    )
    Write-Host "  [*] Rutas de artefactos clave:" -ForegroundColor White
    foreach ($kp in $keyPaths) {
        $exists = Test-Path $kp -ErrorAction SilentlyContinue
        $color = if ($exists) { "Green" } else { "Yellow" }
        $mark = if ($exists) { "[+]" } else { "[!]" }
        Write-Host "      $mark $kp" -ForegroundColor $color
    }

    try {
        $sysDrive = Get-PSDrive -Name ($env:SystemDrive.TrimEnd(':')) -ErrorAction SilentlyContinue
        if ($sysDrive) {
            Write-Host "  [*] Espacio libre en ${env:SystemDrive}: $([math]::Round($sysDrive.Free/1GB,1)) GB" -ForegroundColor White
        }
    } catch { }

    Write-Host ""
    Write-Host "  ===================================================================" -ForegroundColor Cyan
    Write-Host "  DRY-RUN COMPLETADO - ningun fichero del equipo ha sido modificado" -ForegroundColor Cyan
    Write-Host "  ===================================================================" -ForegroundColor Cyan
    Write-Host ""
}

function Invoke-SelfTest {
    <#
        Modo -SelfTest: prueba de humo de la cadena de integridad (hash + copia +
        registro de custodia) contra un fichero temporal SINTETICO PROPIO de la
        herramienta (nunca del equipo objetivo), para verificar que el mecanismo
        funciona correctamente antes de iniciar una adquisicion real.
    #>
    Write-Host ""
    Write-Host "  MaPeriT.SW - MODO SELF-TEST (prueba de la cadena de integridad)" -ForegroundColor Cyan
    Write-Host "  ===================================================================" -ForegroundColor Cyan
    Write-Host ""

    $allOk = $true
    $testDir = Join-Path $env:TEMP "MaPeriT_SelfTest_$(Get-Date -Format 'yyyyMMddHHmmss')"
    New-Item -ItemType Directory -Path $testDir -Force | Out-Null
    $srcFile = Join-Path $testDir "source.txt"
    $dstFile = Join-Path $testDir "dest.txt"
    $syntheticContent = "MaPeriT.SW SelfTest - $(Get-Date -Format o) - $([guid]::NewGuid())"

    try {
        $syntheticContent | Out-File -FilePath $srcFile -Encoding UTF8 -NoNewline
        $expectedHash = (Get-FileHash -Path $srcFile -Algorithm SHA256).Hash
        Write-Host "  [*] Fichero de prueba creado y hasheado: $expectedHash" -ForegroundColor White

        $Script:CustodyLogPath = Join-Path $testDir "TestCustody.csv"
        "Timestamp,EvidenceID,SourcePath,DestinationPath,SHA256Hash,AcquiredBy,Method,FileSize" | Out-File -FilePath $Script:CustodyLogPath -Encoding UTF8
        $Script:ActionLogPath = Join-Path $testDir "TestAction.csv"
        "Timestamp,Phase,Action,Target,Result,Duration_ms,ErrorMessage" | Out-File -FilePath $Script:ActionLogPath -Encoding UTF8
        $Script:EvidenceCounter = 0
        $Script:SessionInfo = @{ ExaminerName = "SelfTest"; TotalErrors = 0; TotalWarnings = 0 }

        $copySuccess = Copy-ForensicFile -SourcePath $srcFile -DestPath $dstFile -Phase "SELFTEST"
        if ($copySuccess -and (Test-Path $dstFile)) {
            Write-Host "  [+] Copy-ForensicFile: OK" -ForegroundColor Green
        } else {
            Write-Host "  [-] Copy-ForensicFile: FALLO" -ForegroundColor Red
            $allOk = $false
        }

        $actualHash = Get-ForensicHash -FilePath $dstFile
        if ($actualHash -eq $expectedHash) {
            Write-Host "  [+] Verificacion de hash SHA-256: OK ($actualHash)" -ForegroundColor Green
        } else {
            Write-Host "  [-] Verificacion de hash SHA-256: FALLO (esperado $expectedHash, obtenido $actualHash)" -ForegroundColor Red
            $allOk = $false
        }

        # @() fuerza contexto de array: si el CSV tiene EXACTAMENTE 1 fila de datos,
        # Import-Csv devuelve un unico [PSCustomObject] suelto (no envuelto en array),
        # que no expone propiedad .Count -> $custodyEntries.Count es $null y
        # "$null -ge 1" es $false, dando un falso "FALLO" aunque el hash SI coincida.
        $custodyEntries = @(Import-Csv $Script:CustodyLogPath -ErrorAction SilentlyContinue)
        if ($custodyEntries -and $custodyEntries.Count -ge 1 -and $custodyEntries[-1].SHA256Hash -eq $expectedHash) {
            Write-Host "  [+] Registro en cadena de custodia: OK ($($custodyEntries.Count) entrada(s))" -ForegroundColor Green
        } else {
            Write-Host "  [-] Registro en cadena de custodia: FALLO o no coincide" -ForegroundColor Red
            $allOk = $false
        }

        if ($Script:EvidenceCounter -ge 1) {
            Write-Host "  [+] Contador de evidencias: OK ($($Script:EvidenceCounter))" -ForegroundColor Green
        } else {
            Write-Host "  [-] Contador de evidencias: FALLO (no incremento)" -ForegroundColor Red
            $allOk = $false
        }
    } catch {
        Write-Host "  [-] EXCEPCION durante el self-test: $($_.Exception.Message)" -ForegroundColor Red
        $allOk = $false
    } finally {
        Remove-Item -Path $testDir -Recurse -Force -ErrorAction SilentlyContinue
    }

    Write-Host ""
    Write-Host "  ===================================================================" -ForegroundColor Cyan
    if ($allOk) {
        Write-Host "  SELF-TEST: TODOS LOS CHEQUEOS SUPERADOS" -ForegroundColor Green
    } else {
        Write-Host "  SELF-TEST: HAY FALLOS - revise los mensajes anteriores antes de" -ForegroundColor Red
        Write-Host "  iniciar una adquisicion real" -ForegroundColor Red
    }
    Write-Host "  ===================================================================" -ForegroundColor Cyan
    Write-Host ""

    return $allOk
}

#endregion ===== Initialize.ps1 =====

#region ===== Phase1-Volatile.ps1 =====
#region ============================================================
# MaPeriT.SW - FASE 1: DATOS VOLATILES
# RFC 3227 - Orden de volatilidad (mas volatil primero)
# ============================================================

function Invoke-Phase1-VolatileCollection {
    param([string]$OutputPath)

    $phasePath = Join-Path $OutputPath $Script:OutputDirs.Volatile
    $phaseNum = 1
    $collected = 0

    Write-PhaseHeader -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum]

    # --- 1.1 Fecha/Hora del sistema ---
    Write-StepHeader "1.1 Fecha, Hora y Zona Horaria del Sistema"
    try {
        $timeInfo = [PSCustomObject]@{
            FechaHoraLocal   = (Get-Date).ToString($Script:Defaults.TimeZoneFormat)
            FechaHoraUTC     = (Get-Date).ToUniversalTime().ToString($Script:Defaults.DateFormat)
            ZonaHoraria      = [System.TimeZoneInfo]::Local.DisplayName
            ZonaHorariaId    = [System.TimeZoneInfo]::Local.Id
            OffsetUTC        = [System.TimeZoneInfo]::Local.BaseUtcOffset.ToString()
            HorarioVerano    = [System.TimeZoneInfo]::Local.IsDaylightSavingTime((Get-Date))
            Uptime           = ((Get-Date) - (Get-CimInstance Win32_OperatingSystem).LastBootUpTime).ToString()
            UltimoArranque   = (Get-CimInstance Win32_OperatingSystem).LastBootUpTime.ToString($Script:Defaults.DateFormat)
            ServidorNTP      = (& w32tm /query /source 2>$null) -join ""
        }
        Export-CollectionToJSON -Data $timeInfo -OutputPath (Join-Path $phasePath "SystemTime.json") -Phase "FASE_1" -Description "Fecha/Hora del sistema"
        $collected++
    } catch {
        Write-ForensicLog -Phase "FASE_1" -Action "SYSTEM_TIME_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 1.2 Volcado de RAM ---
    Write-StepHeader "1.2 Volcado de Memoria RAM"
    if ($SkipRAM) {
        Write-ForensicLog -Phase "FASE_1" -Action "RAM_DUMP_OMITIDO" -Target "Parametro -SkipRAM activado" -Result "SKIP"
    } else {
    try {
        $ramDumpPath = Join-Path $phasePath "MemoryDump"
        $winpmemPaths = @(
            (Join-Path (Split-Path $Script:SessionInfo.ScriptPath -Parent) "tools\winpmem_mini_x64.exe"),
            (Join-Path (Split-Path $Script:SessionInfo.ScriptPath -Parent) "winpmem_mini_x64.exe"),
            "C:\Tools\winpmem_mini_x64.exe",
            (Join-Path $env:TEMP "winpmem_mini_x64.exe")
        )
        $winpmem = $winpmemPaths | Where-Object { Test-Path $_ } | Select-Object -First 1
        if (-not $winpmem) {
            $winpmem = Get-Command winpmem_mini_x64.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source
        }

        if ($winpmem) {
            $ramOutput = Join-Path $ramDumpPath "memory.raw"
            Write-ForensicLog -Phase "FASE_1" -Action "RAM_DUMP_INICIO" -Target $winpmem
            $sw = [System.Diagnostics.Stopwatch]::StartNew()
            & $winpmem $ramOutput 2>&1 | Out-Null
            $sw.Stop()

            if (Test-Path $ramOutput) {
                $hash = Get-ForensicHash -FilePath $ramOutput
                $size = (Get-Item $ramOutput).Length
                Add-ChainOfCustodyEntry -SourcePath "MEMORY_DUMP" -DestPath $ramOutput -Hash $hash -Method "WinPmem" -FileSize $size
                Write-ForensicLog -Phase "FASE_1" -Action "RAM_DUMP_OK" -Target "$([math]::Round($size/1GB, 2)) GB" -Result "OK" -DurationMs $sw.ElapsedMilliseconds
                $collected++
            } else {
                Write-ForensicLog -Phase "FASE_1" -Action "RAM_DUMP_SIN_SALIDA" -Target $ramOutput -Result "WARNING"
            }
        } else {
            Write-ForensicLog -Phase "FASE_1" -Action "RAM_DUMP_WINPMEM_NO_ENCONTRADO" -Target "Coloque winpmem_mini_x64.exe en tools\" -Result "WARNING"
        }
    } catch {
        Write-ForensicLog -Phase "FASE_1" -Action "RAM_DUMP_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }
    } # end if -not $SkipRAM

    # --- 1.3 Procesos en ejecucion ---
    Write-StepHeader "1.3 Procesos en Ejecucion"
    try {
        $processes = Get-CimInstance Win32_Process -ErrorAction Stop | Select-Object `
            ProcessId, Name, ParentProcessId, CommandLine, ExecutablePath,
            CreationDate, ThreadCount, HandleCount, WorkingSetSize, VirtualSize,
            @{N='SessionId';E={$_.SessionId}},
            @{N='Owner';E={
                try { $o = Invoke-CimMethod -InputObject $_ -MethodName GetOwner -ErrorAction SilentlyContinue; if($o.User){"$($o.Domain)\$($o.User)"}else{"N/A"} } catch {"N/A"}
            }}

        Export-CollectionToCSV -Data $processes -OutputPath (Join-Path $phasePath "Processes.csv") -Phase "FASE_1" -Description "Procesos en ejecucion"
        $collected++

        $processHashes = @()
        foreach ($proc in $processes) {
            if ($proc.ExecutablePath -and (Test-Path $proc.ExecutablePath -ErrorAction SilentlyContinue)) {
                try {
                    $hash = Get-ForensicHash -FilePath $proc.ExecutablePath
                    $processHashes += [PSCustomObject]@{
                        ProcessId = $proc.ProcessId
                        Name = $proc.Name
                        Path = $proc.ExecutablePath
                        SHA256 = $hash
                    }
                } catch { }
            }
        }
        if ($processHashes.Count -gt 0) {
            Export-CollectionToCSV -Data $processHashes -OutputPath (Join-Path $phasePath "ProcessHashes.csv") -Phase "FASE_1" -Description "Hashes de ejecutables"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_1" -Action "PROCESOS_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 1.4 Modulos cargados por proceso ---
    Write-StepHeader "1.4 Modulos Cargados (DLLs)"
    try {
        $modules = @()
        Get-Process -ErrorAction SilentlyContinue | ForEach-Object {
            $procName = $_.Name
            $procId = $_.Id
            try {
                $_.Modules | ForEach-Object {
                    $modules += [PSCustomObject]@{
                        ProcessId   = $procId
                        ProcessName = $procName
                        ModuleName  = $_.ModuleName
                        FileName    = $_.FileName
                        ModuleMemorySize = $_.ModuleMemorySize
                    }
                }
            } catch { }
        }
        if ($modules.Count -gt 0) {
            Export-CollectionToCSV -Data $modules -OutputPath (Join-Path $phasePath "ProcessModules.csv") -Phase "FASE_1" -Description "Modulos cargados"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_1" -Action "MODULOS_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 1.5 Conexiones de red TCP ---
    Write-StepHeader "1.5 Conexiones de Red"
    try {
        $tcpConnections = Get-NetTCPConnection -ErrorAction Stop | Select-Object `
            LocalAddress, LocalPort, RemoteAddress, RemotePort, State, OwningProcess,
            CreationTime,
            @{N='ProcessName';E={
                try { (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).Name } catch { "N/A" }
            }}
        Export-CollectionToCSV -Data $tcpConnections -OutputPath (Join-Path $phasePath "NetworkConnections_TCP.csv") -Phase "FASE_1" -Description "Conexiones TCP"
        $collected++
    } catch {
        Write-ForensicLog -Phase "FASE_1" -Action "TCP_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 1.6 Endpoints UDP ---
    Write-StepHeader "1.6 Endpoints UDP"
    try {
        $udpEndpoints = Get-NetUDPEndpoint -ErrorAction Stop | Select-Object `
            LocalAddress, LocalPort, OwningProcess,
            @{N='ProcessName';E={
                try { (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).Name } catch { "N/A" }
            }}
        Export-CollectionToCSV -Data $udpEndpoints -OutputPath (Join-Path $phasePath "NetworkConnections_UDP.csv") -Phase "FASE_1" -Description "Endpoints UDP"
        $collected++
    } catch {
        Write-ForensicLog -Phase "FASE_1" -Action "UDP_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 1.7 Usuarios conectados ---
    Write-StepHeader "1.7 Usuarios Conectados"
    try {
        $logonSessions = Get-CimInstance Win32_LogonSession -ErrorAction SilentlyContinue
        $loggedOnUsers = @()
        foreach ($session in $logonSessions) {
            try {
                $user = Get-CimAssociatedInstance -InputObject $session -ResultClassName Win32_Account -ErrorAction SilentlyContinue
                if ($user) {
                    $loggedOnUsers += [PSCustomObject]@{
                        Usuario      = $user.Name
                        Dominio      = $user.Domain
                        SID          = $user.SID
                        TipoLogon    = $session.LogonType
                        TipoLogonDesc= $Script:LogonTypes[[int]$session.LogonType]
                        InicioSesion = $session.StartTime
                        LogonId      = $session.LogonId
                    }
                }
            } catch { }
        }
        Export-CollectionToCSV -Data $loggedOnUsers -OutputPath (Join-Path $phasePath "LoggedOnUsers.csv") -Phase "FASE_1" -Description "Usuarios conectados"
        $collected++

        $queryUser = & query user 2>$null
        if ($queryUser) {
            Export-CollectionToText -Content ($queryUser | Out-String) -OutputPath (Join-Path $phasePath "QueryUser.txt") -Phase "FASE_1" -Description "query user"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_1" -Action "USUARIOS_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 1.8 Ficheros abiertos ---
    Write-StepHeader "1.8 Ficheros Abiertos y Recursos Compartidos"
    try {
        $openFiles = Get-SmbOpenFile -ErrorAction SilentlyContinue | Select-Object FileId, SessionId, Path, ShareRelativePath, ClientComputerName, ClientUserName
        if ($openFiles) {
            Export-CollectionToCSV -Data $openFiles -OutputPath (Join-Path $phasePath "OpenFiles.csv") -Phase "FASE_1" -Description "Ficheros abiertos SMB"
            $collected++
        }

        $netFile = & net file 2>$null
        if ($netFile) {
            Export-CollectionToText -Content ($netFile | Out-String) -OutputPath (Join-Path $phasePath "NetFile.txt") -Phase "FASE_1" -Description "net file"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_1" -Action "OPEN_FILES_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 1.9 Portapapeles ---
    Write-StepHeader "1.9 Contenido del Portapapeles"
    try {
        $clipboardContent = ""
        $runspace = [runspacefactory]::CreateRunspace()
        $runspace.ApartmentState = "STA"
        $runspace.Open()
        $ps = [powershell]::Create()
        $ps.Runspace = $runspace
        [void]$ps.AddScript({
            Add-Type -AssemblyName System.Windows.Forms
            [System.Windows.Forms.Clipboard]::GetText()
        })
        $clipboardContent = $ps.Invoke()
        $ps.Dispose()
        $runspace.Close()

        if ($clipboardContent) {
            Export-CollectionToText -Content ($clipboardContent | Out-String) -OutputPath (Join-Path $phasePath "Clipboard.txt") -Phase "FASE_1" -Description "Portapapeles"
            $collected++
        } else {
            Write-ForensicLog -Phase "FASE_1" -Action "CLIPBOARD_VACIO" -Target "Portapapeles vacio o sin texto" -Result "WARNING"
        }
    } catch {
        Write-ForensicLog -Phase "FASE_1" -Action "CLIPBOARD_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    # --- 1.10 Cache DNS ---
    Write-StepHeader "1.10 Cache DNS"
    try {
        $dnsCache = Get-DnsClientCache -ErrorAction Stop | Select-Object Entry, Name, Type, Status, Section, TimeToLive, DataLength, Data
        Export-CollectionToCSV -Data $dnsCache -OutputPath (Join-Path $phasePath "DnsCache.csv") -Phase "FASE_1" -Description "Cache DNS"
        $collected++
    } catch {
        Write-ForensicLog -Phase "FASE_1" -Action "DNS_CACHE_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 1.11 Cache ARP ---
    Write-StepHeader "1.11 Cache ARP"
    try {
        $arpCache = Get-NetNeighbor -ErrorAction Stop | Select-Object IPAddress, LinkLayerAddress, State, InterfaceAlias, InterfaceIndex, AddressFamily
        Export-CollectionToCSV -Data $arpCache -OutputPath (Join-Path $phasePath "ArpCache.csv") -Phase "FASE_1" -Description "Cache ARP"
        $collected++
    } catch {
        Write-ForensicLog -Phase "FASE_1" -Action "ARP_CACHE_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 1.12 Tabla de rutas ---
    Write-StepHeader "1.12 Tabla de Rutas"
    try {
        $routes = Get-NetRoute -ErrorAction Stop | Select-Object DestinationPrefix, NextHop, InterfaceAlias, InterfaceIndex, RouteMetric, Protocol, AddressFamily
        Export-CollectionToCSV -Data $routes -OutputPath (Join-Path $phasePath "RoutingTable.csv") -Phase "FASE_1" -Description "Tabla de rutas"
        $collected++
    } catch {
        Write-ForensicLog -Phase "FASE_1" -Action "ROUTES_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 1.13 Shares y unidades mapeadas ---
    Write-StepHeader "1.13 Recursos Compartidos y Unidades Mapeadas"
    try {
        $shares = Get-SmbShare -ErrorAction SilentlyContinue | Select-Object Name, Path, Description, ShareType, CurrentUsers, EncryptData
        if ($shares) {
            Export-CollectionToCSV -Data $shares -OutputPath (Join-Path $phasePath "NetworkShares.csv") -Phase "FASE_1" -Description "Recursos compartidos"
            $collected++
        }

        $netUse = & net use 2>$null
        if ($netUse) {
            Export-CollectionToText -Content ($netUse | Out-String) -OutputPath (Join-Path $phasePath "MappedDrives.txt") -Phase "FASE_1" -Description "Unidades mapeadas"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_1" -Action "SHARES_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 1.14 Variables de entorno ---
    Write-StepHeader "1.14 Variables de Entorno"
    try {
        $envVars = @()
        foreach ($scope in @("Machine", "User", "Process")) {
            [System.Environment]::GetEnvironmentVariables($scope).GetEnumerator() | ForEach-Object {
                $envVars += [PSCustomObject]@{
                    Scope = $scope
                    Name  = $_.Key
                    Value = $_.Value
                }
            }
        }
        Export-CollectionToCSV -Data $envVars -OutputPath (Join-Path $phasePath "EnvironmentVariables.csv") -Phase "FASE_1" -Description "Variables de entorno"
        $collected++
    } catch {
        Write-ForensicLog -Phase "FASE_1" -Action "ENV_VARS_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 1.15 Tareas programadas activas ---
    Write-StepHeader "1.15 Tareas Programadas Activas"
    try {
        $tasks = Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.State -ne 'Disabled' } | Select-Object `
            TaskName, TaskPath, State, Description,
            @{N='Author';E={$_.Principal.UserId}},
            @{N='Actions';E={($_.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -join "; "}}
        Export-CollectionToCSV -Data $tasks -OutputPath (Join-Path $phasePath "ActiveScheduledTasks.csv") -Phase "FASE_1" -Description "Tareas programadas activas"
        $collected++
    } catch {
        Write-ForensicLog -Phase "FASE_1" -Action "SCHED_TASKS_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 1.16 Named Pipes ---
    Write-StepHeader "1.16 Named Pipes"
    try {
        $pipes = @()
        Get-ChildItem "\\.\pipe\" -ErrorAction SilentlyContinue | ForEach-Object {
            $pipes += [PSCustomObject]@{
                Nombre = $_.Name
                Tamano = $_.Length
            }
        }
        if ($pipes.Count -gt 0) {
            Export-CollectionToCSV -Data $pipes -OutputPath (Join-Path $phasePath "NamedPipes.csv") -Phase "FASE_1" -Description "Named Pipes"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_1" -Action "NAMED_PIPES_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    Write-PhaseComplete -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum] -ItemsCollected $collected
    return $collected
}

#endregion ===== Phase1-Volatile.ps1 =====

#region ===== Phase2-SystemInfo.ps1 =====
#region ============================================================
# MaPeriT.SW - FASE 2: INFORMACION DEL SISTEMA
# ============================================================

function Invoke-Phase2-SystemInfoCollection {
    param([string]$OutputPath)

    $phasePath = Join-Path $OutputPath $Script:OutputDirs.SystemInfo
    $phaseNum = 2
    $collected = 0

    Write-PhaseHeader -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum]

    # --- 2.1 Inventario de hardware ---
    Write-StepHeader "2.1 Inventario de Hardware"
    try {
        $cpu = Get-CimInstance Win32_Processor -ErrorAction Stop | Select-Object Name, Manufacturer, NumberOfCores, NumberOfLogicalProcessors, MaxClockSpeed, SocketDesignation, ProcessorId
        $ram = Get-CimInstance Win32_PhysicalMemory -ErrorAction Stop | Select-Object Manufacturer, PartNumber, SerialNumber, Capacity, Speed, ConfiguredClockSpeed, DeviceLocator
        $disks = Get-CimInstance Win32_DiskDrive -ErrorAction Stop | Select-Object Model, SerialNumber, InterfaceType, MediaType, Size, Partitions, DeviceID, FirmwareRevision
        $bios = Get-CimInstance Win32_BIOS -ErrorAction Stop | Select-Object Manufacturer, Name, SerialNumber, SMBIOSBIOSVersion, ReleaseDate, Version
        $board = Get-CimInstance Win32_BaseBoard -ErrorAction Stop | Select-Object Manufacturer, Product, SerialNumber, Version
        $gpu = Get-CimInstance Win32_VideoController -ErrorAction SilentlyContinue | Select-Object Name, DriverVersion, AdapterRAM, VideoProcessor
        $nics = Get-NetAdapter -ErrorAction SilentlyContinue | Select-Object Name, InterfaceDescription, MacAddress, Status, LinkSpeed, MediaType, DriverVersion, DriverProvider

        $hardware = [PSCustomObject]@{
            CPU         = $cpu
            RAM         = $ram
            RAMTotalGB  = [math]::Round(($ram | Measure-Object -Property Capacity -Sum).Sum / 1GB, 2)
            Discos      = $disks
            BIOS        = $bios
            PlacaBase   = $board
            GPU         = $gpu
            Adaptadores = $nics
        }
        Export-CollectionToJSON -Data $hardware -OutputPath (Join-Path $phasePath "HardwareInventory.json") -Phase "FASE_2" -Description "Inventario de hardware"
        $collected++
    } catch {
        Write-ForensicLog -Phase "FASE_2" -Action "HARDWARE_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 2.2 Informacion del SO ---
    Write-StepHeader "2.2 Informacion del Sistema Operativo"
    try {
        $os = Get-CimInstance Win32_OperatingSystem -ErrorAction Stop
        $cs = Get-CimInstance Win32_ComputerSystem -ErrorAction Stop

        $osInfo = [PSCustomObject]@{
            NombreEquipo        = $cs.Name
            Dominio             = $cs.Domain
            TipoSistema         = $cs.SystemType
            Fabricante          = $cs.Manufacturer
            Modelo              = $cs.Model
            SO                  = $os.Caption
            Version             = $os.Version
            Build               = $os.BuildNumber
            ServicePack         = $os.ServicePackMajorVersion
            Arquitectura        = $os.OSArchitecture
            FechaInstalacion    = $os.InstallDate
            UltimoArranque      = $os.LastBootUpTime
            DirectorioWindows   = $os.WindowsDirectory
            DirectorioSistema   = $os.SystemDirectory
            NumeroSerie         = $os.SerialNumber
            ProductoID          = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion" -ErrorAction SilentlyContinue).ProductId
            EdicionID           = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion" -ErrorAction SilentlyContinue).EditionID
            DisplayVersion      = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion" -ErrorAction SilentlyContinue).DisplayVersion
            UBR                 = (Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion" -ErrorAction SilentlyContinue).UBR
        }
        Export-CollectionToJSON -Data $osInfo -OutputPath (Join-Path $phasePath "OSInfo.json") -Phase "FASE_2" -Description "Informacion del SO"
        $collected++
    } catch {
        Write-ForensicLog -Phase "FASE_2" -Action "OS_INFO_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 2.3 Informacion de discos y particiones ---
    Write-StepHeader "2.3 Discos y Particiones"
    try {
        $volumes = Get-Volume -ErrorAction SilentlyContinue | Select-Object DriveLetter, FileSystemLabel, FileSystem, DriveType, HealthStatus, SizeRemaining, Size, OperationalStatus
        $partitions = Get-Partition -ErrorAction SilentlyContinue | Select-Object DiskNumber, PartitionNumber, DriveLetter, Size, Type, IsActive, IsBoot, IsSystem, GptType
        Export-CollectionToCSV -Data $volumes -OutputPath (Join-Path $phasePath "Volumes.csv") -Phase "FASE_2" -Description "Volumenes"
        Export-CollectionToCSV -Data $partitions -OutputPath (Join-Path $phasePath "Partitions.csv") -Phase "FASE_2" -Description "Particiones"
        $collected += 2
    } catch {
        Write-ForensicLog -Phase "FASE_2" -Action "DISKS_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 2.4 Usuarios y grupos locales ---
    Write-StepHeader "2.4 Usuarios y Grupos Locales"
    try {
        $users = Get-LocalUser -ErrorAction Stop | Select-Object Name, Enabled, SID, LastLogon, PasswordLastSet, PasswordRequired, PasswordChangeableDate, PasswordExpires, AccountExpires, Description, FullName
        Export-CollectionToCSV -Data $users -OutputPath (Join-Path $phasePath "LocalUsers.csv") -Phase "FASE_2" -Description "Usuarios locales"
        $collected++

        $groupData = @()
        Get-LocalGroup -ErrorAction SilentlyContinue | ForEach-Object {
            $groupName = $_.Name
            $members = @()
            try {
                $members = Get-LocalGroupMember -Group $groupName -ErrorAction SilentlyContinue | ForEach-Object { $_.Name }
            } catch { }
            $groupData += [PSCustomObject]@{
                Grupo    = $groupName
                SID      = $_.SID
                Miembros = ($members -join "; ")
            }
        }
        Export-CollectionToCSV -Data $groupData -OutputPath (Join-Path $phasePath "LocalGroups.csv") -Phase "FASE_2" -Description "Grupos locales"
        $collected++
    } catch {
        Write-ForensicLog -Phase "FASE_2" -Action "USERS_GROUPS_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 2.5 Software instalado ---
    Write-StepHeader "2.5 Software Instalado"
    try {
        $regPaths = @($Script:RegistryPaths.UninstallHKLM, $Script:RegistryPaths.UninstallWow64, $Script:RegistryPaths.UninstallHKCU)
        $software = @()
        foreach ($regPath in $regPaths) {
            if (Test-Path $regPath) {
                Get-ChildItem $regPath -ErrorAction SilentlyContinue | ForEach-Object {
                    $props = Get-ItemProperty $_.PsPath -ErrorAction SilentlyContinue
                    if ($props.DisplayName) {
                        $software += [PSCustomObject]@{
                            Nombre     = $props.DisplayName
                            Version    = $props.DisplayVersion
                            Editor     = $props.Publisher
                            FechaInst  = $props.InstallDate
                            Ubicacion  = $props.InstallLocation
                            Origen     = $regPath -replace 'HKLM:\\|HKCU:\\', ''
                        }
                    }
                }
            }
        }
        Export-CollectionToCSV -Data ($software | Sort-Object Nombre -Unique) -OutputPath (Join-Path $phasePath "InstalledSoftware.csv") -Phase "FASE_2" -Description "Software instalado"
        $collected++
    } catch {
        Write-ForensicLog -Phase "FASE_2" -Action "SOFTWARE_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 2.6 Servicios de Windows ---
    Write-StepHeader "2.6 Servicios de Windows"
    try {
        $services = Get-CimInstance Win32_Service -ErrorAction Stop | Select-Object `
            Name, DisplayName, State, StartMode, PathName, StartName, ProcessId, ServiceType, Description,
            @{N='Firmado';E={
                if ($_.PathName) {
                    $exePath = Resolve-ServiceExecutablePath -PathName $_.PathName
                    if ($exePath -and (Test-Path $exePath -ErrorAction SilentlyContinue)) {
                        try { (Get-AuthenticodeSignature $exePath -ErrorAction SilentlyContinue).Status.ToString() } catch { "N/A" }
                    } else { "PathNotFound" }
                } else { "N/A" }
            }}
        Export-CollectionToCSV -Data $services -OutputPath (Join-Path $phasePath "Services.csv") -Phase "FASE_2" -Description "Servicios de Windows"
        $collected++
    } catch {
        Write-ForensicLog -Phase "FASE_2" -Action "SERVICES_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 2.7 Tareas programadas (completo) ---
    Write-StepHeader "2.7 Tareas Programadas (Completo)"
    try {
        $allTasks = Get-ScheduledTask -ErrorAction SilentlyContinue | Select-Object `
            TaskName, TaskPath, State, Description, Author, Date,
            @{N='Trigger';E={($_.Triggers | ForEach-Object { $_.CimClass.CimClassName }) -join "; "}},
            @{N='Actions';E={($_.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)" }) -join "; "}},
            @{N='RunAs';E={$_.Principal.UserId}},
            @{N='RunLevel';E={$_.Principal.RunLevel}}
        Export-CollectionToCSV -Data $allTasks -OutputPath (Join-Path $phasePath "ScheduledTasks.csv") -Phase "FASE_2" -Description "Tareas programadas"
        $collected++

        $xmlDir = Join-Path $phasePath "ScheduledTasksXML"
        if (-not (Test-Path $xmlDir)) { New-Item -ItemType Directory -Path $xmlDir -Force | Out-Null }
        $xmlCount = 0
        Get-ScheduledTask -ErrorAction SilentlyContinue | ForEach-Object {
            try {
                $xmlContent = Export-ScheduledTask -TaskName $_.TaskName -TaskPath $_.TaskPath -ErrorAction SilentlyContinue
                if ($xmlContent) {
                    $safeName = ($_.TaskPath + $_.TaskName) -replace '[\\/:*?"<>|]', '_'
                    $xmlContent | Out-File -FilePath (Join-Path $xmlDir "$safeName.xml") -Encoding UTF8
                    $xmlCount++
                }
            } catch { }
        }
        if ($xmlCount -gt 0) {
            Write-ForensicLog -Phase "FASE_2" -Action "SCHED_TASKS_XML" -Target "$xmlCount tareas exportadas como XML" -Result "OK"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_2" -Action "SCHED_TASKS_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 2.8 Drivers cargados ---
    Write-StepHeader "2.8 Drivers Cargados"
    try {
        $drivers = Get-CimInstance Win32_SystemDriver -ErrorAction Stop | Select-Object Name, DisplayName, State, StartMode, PathName, ServiceType, Description
        Export-CollectionToCSV -Data $drivers -OutputPath (Join-Path $phasePath "Drivers.csv") -Phase "FASE_2" -Description "Drivers cargados"
        $collected++
    } catch {
        Write-ForensicLog -Phase "FASE_2" -Action "DRIVERS_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 2.9 Historial de Windows Update ---
    Write-StepHeader "2.9 Historial de Windows Update"
    try {
        $hotfixes = Get-HotFix -ErrorAction SilentlyContinue | Select-Object HotFixID, Description, InstalledBy, InstalledOn
        Export-CollectionToCSV -Data $hotfixes -OutputPath (Join-Path $phasePath "WindowsUpdates.csv") -Phase "FASE_2" -Description "Actualizaciones instaladas"
        $collected++
    } catch {
        Write-ForensicLog -Phase "FASE_2" -Action "UPDATES_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 2.10 Configuracion de red ---
    Write-StepHeader "2.10 Configuracion de Red"
    try {
        $ipConfig = Get-NetIPConfiguration -Detailed -ErrorAction SilentlyContinue
        $ipAddresses = Get-NetIPAddress -ErrorAction SilentlyContinue | Select-Object InterfaceAlias, InterfaceIndex, IPAddress, PrefixLength, AddressFamily, Type, PrefixOrigin, SuffixOrigin
        Export-CollectionToCSV -Data $ipAddresses -OutputPath (Join-Path $phasePath "IPAddresses.csv") -Phase "FASE_2" -Description "Direcciones IP"
        $collected++

        $wifiProfiles = @()
        try {
            # Limitado a 2 partes: un SSID puede contener ':' (p.ej. "Office:Guest"); sin el
            # limite, -split parte por TODAS las ocurrencias de ':' en la linea y [1] solo
            # recoge el fragmento hasta el segundo ':', truncando/corrompiendo el nombre real
            # de la red y haciendo que la consulta posterior de detalle del perfil falle o,
            # peor, coincida con un perfil distinto de igual prefijo.
            # netsh traduce la etiqueta segun el idioma del sistema: "All User Profile"
            # (ingles) y "Perfil de todos los usuarios" (espanol). Antes solo se buscaba
            # la inglesa y en un Windows en espanol no se recogia ningun perfil.
            $profileNames = (& netsh wlan show profiles 2>$null) | Select-String -Pattern "All User Profile|Perfil de todos los usuarios" | ForEach-Object { ($_ -split ":\s*", 2)[1].Trim() }
            foreach ($profile in $profileNames) {
                $detail = & netsh wlan show profile name="$profile" key=clear 2>$null
                $wifiProfiles += [PSCustomObject]@{
                    Nombre = $profile
                    Detalle = ($detail | Out-String)
                }
            }
            if ($wifiProfiles.Count -gt 0) {
                Export-CollectionToCSV -Data $wifiProfiles -OutputPath (Join-Path $phasePath "WiFiProfiles.csv") -Phase "FASE_2" -Description "Perfiles WiFi"
                $collected++
            }
        } catch {
            Write-ForensicLog -Phase "FASE_2" -Action "WIFI_WARNING" -Target "Sin adaptador WiFi o sin perfiles" -Result "WARNING"
        }
    } catch {
        Write-ForensicLog -Phase "FASE_2" -Action "NETWORK_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 2.11 Reglas de Firewall ---
    Write-StepHeader "2.11 Reglas de Firewall"
    try {
        $fwRules = Get-NetFirewallRule -ErrorAction SilentlyContinue | Where-Object { $_.Enabled -eq 'True' } | Select-Object `
            Name, DisplayName, Direction, Action, Profile, Enabled,
            @{N='LocalPort';E={
                try { ($_ | Get-NetFirewallPortFilter -ErrorAction SilentlyContinue).LocalPort } catch { "N/A" }
            }},
            @{N='RemotePort';E={
                try { ($_ | Get-NetFirewallPortFilter -ErrorAction SilentlyContinue).RemotePort } catch { "N/A" }
            }},
            @{N='Protocol';E={
                try { ($_ | Get-NetFirewallPortFilter -ErrorAction SilentlyContinue).Protocol } catch { "N/A" }
            }},
            @{N='Program';E={
                try { ($_ | Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue).Program } catch { "N/A" }
            }}
        Export-CollectionToCSV -Data $fwRules -OutputPath (Join-Path $phasePath "FirewallRules.csv") -Phase "FASE_2" -Description "Reglas de firewall activas"
        $collected++
    } catch {
        Write-ForensicLog -Phase "FASE_2" -Action "FIREWALL_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 2.12 BitLocker ---
    Write-StepHeader "2.12 Estado de BitLocker"
    try {
        $bitlocker = Get-BitLockerVolume -ErrorAction SilentlyContinue | Select-Object MountPoint, VolumeStatus, ProtectionStatus, EncryptionMethod, EncryptionPercentage, LockStatus, AutoUnlockEnabled, VolumeType
        if ($bitlocker) {
            Export-CollectionToJSON -Data $bitlocker -OutputPath (Join-Path $phasePath "BitLocker.json") -Phase "FASE_2" -Description "Estado BitLocker"
            $collected++
        } else {
            Write-ForensicLog -Phase "FASE_2" -Action "BITLOCKER_NO_ENCONTRADO" -Target "BitLocker no activo" -Result "WARNING"
        }
    } catch {
        Write-ForensicLog -Phase "FASE_2" -Action "BITLOCKER_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    # --- 2.13 Antivirus ---
    Write-StepHeader "2.13 Estado del Antivirus"
    try {
        $avProducts = Get-CimInstance -Namespace "root/SecurityCenter2" -ClassName AntiVirusProduct -ErrorAction SilentlyContinue | Select-Object displayName, instanceGuid, pathToSignedProductExe, pathToSignedReportingExe, productState, timestamp

        $defenderStatus = $null
        try {
            $defenderStatus = Get-MpComputerStatus -ErrorAction SilentlyContinue | Select-Object `
                AMEngineVersion, AMProductVersion, AMServiceEnabled, AMServiceVersion,
                AntispywareEnabled, AntispywareSignatureLastUpdated, AntivirusEnabled,
                AntivirusSignatureLastUpdated, BehaviorMonitorEnabled, FullScanAge,
                IoavProtectionEnabled, NISEnabled, OnAccessProtectionEnabled,
                QuickScanAge, RealTimeProtectionEnabled, TamperProtectionSource
        } catch { }

        $defenderThreats = @()
        try {
            $defenderThreats = Get-MpThreatDetection -ErrorAction SilentlyContinue | Select-Object `
                ThreatID, @{N='ThreatName';E={
                    try { (Get-MpThreat -ThreatID $_.ThreatID -ErrorAction SilentlyContinue).ThreatName } catch { "N/A" }
                }},
                InitialDetectionTime, LastThreatStatusChangeTime, ProcessName, DomainUser, ActionSuccess
        } catch { }

        $avInfo = [PSCustomObject]@{
            ProductosAV  = $avProducts
            DefenderStatus = $defenderStatus
            AmenazasDetectadas = $defenderThreats
        }
        Export-CollectionToJSON -Data $avInfo -OutputPath (Join-Path $phasePath "AntivirusStatus.json") -Phase "FASE_2" -Description "Estado del antivirus"
        $collected++
    } catch {
        Write-ForensicLog -Phase "FASE_2" -Action "ANTIVIRUS_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    # --- 2.14 Politica de seguridad ---
    Write-StepHeader "2.14 Politicas de Seguridad"
    try {
        $secPolPath = Join-Path $phasePath "SecurityPolicy.inf"
        & secedit /export /cfg $secPolPath /quiet 2>$null
        if (Test-Path $secPolPath) {
            $hash = Get-ForensicHash -FilePath $secPolPath
            Add-ChainOfCustodyEntry -SourcePath "secedit" -DestPath $secPolPath -Hash $hash -Method "secedit export" -FileSize (Get-Item $secPolPath).Length
            Write-ForensicLog -Phase "FASE_2" -Action "SECPOL_EXPORTADA" -Target $secPolPath -Result "OK"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_2" -Action "SECPOL_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    # --- 2.15 Resultado de Politicas de Grupo ---
    Write-StepHeader "2.15 Resultado de Politicas de Grupo"
    try {
        $gpHtml = Join-Path $phasePath "GPResult.html"
        & gpresult /H $gpHtml /F 2>$null
        if (Test-Path $gpHtml) {
            $hash = Get-ForensicHash -FilePath $gpHtml
            Add-ChainOfCustodyEntry -SourcePath "gpresult" -DestPath $gpHtml -Hash $hash -Method "gpresult /H" -FileSize (Get-Item $gpHtml).Length
            $collected++
        }

        $gpText = & gpresult /R 2>$null
        if ($gpText) {
            Export-CollectionToText -Content ($gpText | Out-String) -OutputPath (Join-Path $phasePath "GPResult.txt") -Phase "FASE_2" -Description "gpresult /R"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_2" -Action "GPRESULT_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    Write-PhaseComplete -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum] -ItemsCollected $collected
    return $collected
}

#endregion ===== Phase2-SystemInfo.ps1 =====

#region ===== Phase3-Filesystem.ps1 =====
#region ============================================================
# MaPeriT.SW - FASE 3: ARTEFACTOS DEL SISTEMA DE FICHEROS
# ============================================================

function Invoke-Phase3-FilesystemCollection {
    param([string]$OutputPath)

    $phasePath = Join-Path $OutputPath $Script:OutputDirs.Filesystem
    $phaseNum = 3
    $collected = 0

    Write-PhaseHeader -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum]

    # --- 3.1 Extraccion de la MFT ---
    Write-StepHeader "3.1 Extraccion de la MFT (Master File Table)"
    try {
        $mftOutput = Join-Path $phasePath '$MFT'
        $mftSuccess = $false

        $mftSuccess = Extract-MFTRaw -DriveLetter "C:" -OutputPath $mftOutput
        if (-not $mftSuccess -and $Script:VSSShadowPath) {
            Write-ForensicLog -Phase "FASE_3" -Action "MFT_FALLBACK_VSS" -Target "Intentando copia via VSS"
            $vssMft = $Script:VSSShadowPath + '$MFT'
            if (Test-Path $vssMft -ErrorAction SilentlyContinue) {
                Copy-Item -Path $vssMft -Destination $mftOutput -Force -ErrorAction Stop
                $mftSuccess = $true
                Write-ForensicLog -Phase "FASE_3" -Action "MFT_VSS_OK" -Target $mftOutput -Result "OK"
            }
        }

        if ($mftSuccess -and (Test-Path $mftOutput)) {
            $hash = Get-ForensicHash -FilePath $mftOutput
            $size = (Get-Item $mftOutput).Length
            Add-ChainOfCustodyEntry -SourcePath 'C:\$MFT' -DestPath $mftOutput -Hash $hash -Method "RawDisk/VSS" -FileSize $size
            $collected++
        } else {
            Write-ForensicLog -Phase "FASE_3" -Action "MFT_NO_EXTRAIDA" -Target "Todos los metodos fallaron" -Result "WARNING"
        }
    } catch {
        Write-ForensicLog -Phase "FASE_3" -Action "MFT_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 3.2 USN Journal ---
    Write-StepHeader "3.2 USN Journal"
    try {
        $usnOutput = Join-Path $phasePath "UsnJournal.csv"
        $usnData = & fsutil usn readjournal C: csv 2>$null
        if ($usnData) {
            $usnData | Out-File -FilePath $usnOutput -Encoding UTF8
            $hash = Get-ForensicHash -FilePath $usnOutput
            $size = (Get-Item $usnOutput).Length
            Add-ChainOfCustodyEntry -SourcePath 'C:\$Extend\$UsnJrnl' -DestPath $usnOutput -Hash $hash -Method "fsutil" -FileSize $size
            Write-ForensicLog -Phase "FASE_3" -Action "USN_JOURNAL_OK" -Target "$([math]::Round($size/1MB, 2)) MB" -Result "OK"
            $collected++
        } else {
            Write-ForensicLog -Phase "FASE_3" -Action "USN_JOURNAL_VACIO" -Target "fsutil no devolvio datos" -Result "WARNING"
        }
    } catch {
        Write-ForensicLog -Phase "FASE_3" -Action "USN_JOURNAL_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 3.3 $LogFile ---
    Write-StepHeader "3.3 NTFS LogFile"
    try {
        if ($Script:VSSShadowPath) {
            $vssLogFile = $Script:VSSShadowPath + '$LogFile'
            $logFileOutput = Join-Path $phasePath '$LogFile'
            if (Test-Path $vssLogFile -ErrorAction SilentlyContinue) {
                Copy-Item -Path $vssLogFile -Destination $logFileOutput -Force -ErrorAction Stop
                $hash = Get-ForensicHash -FilePath $logFileOutput
                $size = (Get-Item $logFileOutput).Length
                Add-ChainOfCustodyEntry -SourcePath 'C:\$LogFile' -DestPath $logFileOutput -Hash $hash -Method "VSS" -FileSize $size
                Write-ForensicLog -Phase "FASE_3" -Action "LOGFILE_COPIADO" -Target $logFileOutput -Result "OK"
                $collected++
            }
        } else {
            Write-ForensicLog -Phase "FASE_3" -Action "LOGFILE_SIN_VSS" -Target "Requiere VSS snapshot" -Result "WARNING"
        }
    } catch {
        Write-ForensicLog -Phase "FASE_3" -Action "LOGFILE_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 3.4 Prefetch ---
    Write-StepHeader "3.4 Ficheros Prefetch"
    try {
        $prefetchSrc = "C:\Windows\Prefetch"
        $prefetchDst = Join-Path $phasePath "Prefetch"
        if (Test-Path $prefetchSrc) {
            $pfCount = Copy-ForensicDirectory -SourceDir $prefetchSrc -DestDir $prefetchDst -Phase "FASE_3" -Filter "*.pf"
            Write-ForensicLog -Phase "FASE_3" -Action "PREFETCH_COPIADOS" -Target "$pfCount ficheros" -Result "OK"
            $collected += $pfCount

            $pfSummary = @()
            Get-ChildItem (Join-Path $phasePath "Prefetch") -Filter "*.pf" -ErrorAction SilentlyContinue | ForEach-Object {
                $pfSummary += [PSCustomObject]@{
                    Nombre      = $_.Name
                    Tamano      = $_.Length
                    Creacion    = $_.CreationTime.ToString($Script:Defaults.DateFormat)
                    Modificado  = $_.LastWriteTime.ToString($Script:Defaults.DateFormat)
                    Acceso      = $_.LastAccessTime.ToString($Script:Defaults.DateFormat)
                }
            }
            if ($pfSummary.Count -gt 0) {
                Export-CollectionToCSV -Data $pfSummary -OutputPath (Join-Path $phasePath "PrefetchSummary.csv") -Phase "FASE_3" -Description "Resumen Prefetch"
                $collected++
            }
        } else {
            Write-ForensicLog -Phase "FASE_3" -Action "PREFETCH_NO_ENCONTRADO" -Target $prefetchSrc -Result "WARNING"
        }
    } catch {
        Write-ForensicLog -Phase "FASE_3" -Action "PREFETCH_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 3.5 Ficheros recientes / LNK ---
    Write-StepHeader "3.5 Ficheros Recientes (LNK)"
    try {
        $profiles = Get-UserProfiles
        $lnkParsed = @()

        foreach ($profile in $profiles) {
            $recentDir = Join-Path $profile.ProfilePath "AppData\Roaming\Microsoft\Windows\Recent"
            if (Test-Path $recentDir) {
                $userDest = Join-Path $phasePath "RecentFiles\$($profile.Username)"
                $lnkCount = Copy-ForensicDirectory -SourceDir $recentDir -DestDir $userDest -Phase "FASE_3" -Filter "*.lnk"
                $collected += $lnkCount

                $shell = New-Object -ComObject WScript.Shell -ErrorAction SilentlyContinue
                if ($shell) {
                    Get-ChildItem $recentDir -Filter "*.lnk" -ErrorAction SilentlyContinue | ForEach-Object {
                        try {
                            $shortcut = $shell.CreateShortcut($_.FullName)
                            $lnkParsed += [PSCustomObject]@{
                                Usuario        = $profile.Username
                                NombreLNK      = $_.Name
                                RutaDestino    = $shortcut.TargetPath
                                Argumentos     = $shortcut.Arguments
                                DirectorioTrabajo = $shortcut.WorkingDirectory
                                Descripcion    = $shortcut.Description
                                Creacion       = $_.CreationTime.ToString($Script:Defaults.DateFormat)
                                Modificacion   = $_.LastWriteTime.ToString($Script:Defaults.DateFormat)
                                Acceso         = $_.LastAccessTime.ToString($Script:Defaults.DateFormat)
                            }
                        } catch { }
                    }
                    [System.Runtime.Interopservices.Marshal]::ReleaseComObject($shell) | Out-Null
                }
            }
        }

        if ($lnkParsed.Count -gt 0) {
            Export-CollectionToCSV -Data $lnkParsed -OutputPath (Join-Path $phasePath "LNKParsed.csv") -Phase "FASE_3" -Description "LNK parseados"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_3" -Action "LNK_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 3.6 Jump Lists ---
    Write-StepHeader "3.6 Jump Lists"
    try {
        $profiles = Get-UserProfiles
        foreach ($profile in $profiles) {
            $autoDestDir = Join-Path $profile.ProfilePath "AppData\Roaming\Microsoft\Windows\Recent\AutomaticDestinations"
            $customDestDir = Join-Path $profile.ProfilePath "AppData\Roaming\Microsoft\Windows\Recent\CustomDestinations"
            $userJumpDest = Join-Path $phasePath "JumpLists\$($profile.Username)"

            if (Test-Path $autoDestDir) {
                $c = Copy-ForensicDirectory -SourceDir $autoDestDir -DestDir (Join-Path $userJumpDest "AutomaticDestinations") -Phase "FASE_3"
                $collected += $c
            }
            if (Test-Path $customDestDir) {
                $c = Copy-ForensicDirectory -SourceDir $customDestDir -DestDir (Join-Path $userJumpDest "CustomDestinations") -Phase "FASE_3"
                $collected += $c
            }
        }
    } catch {
        Write-ForensicLog -Phase "FASE_3" -Action "JUMPLISTS_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 3.7 Papelera de reciclaje ---
    Write-StepHeader "3.7 Papelera de Reciclaje"
    try {
        $recycleBinPath = "C:\`$Recycle.Bin"
        $recycleOutput = Join-Path $phasePath "RecycleBin"

        if (Test-Path $recycleBinPath) {
            $sidDirs = Get-ChildItem $recycleBinPath -Directory -Force -ErrorAction SilentlyContinue
            $recycleSummary = @()

            foreach ($sidDir in $sidDirs) {
                $destSidDir = Join-Path $recycleOutput $sidDir.Name
                $iFiles = Get-ChildItem $sidDir.FullName -Filter '$I*' -Force -ErrorAction SilentlyContinue
                foreach ($iFile in $iFiles) {
                    try {
                        $iFileContent = [System.IO.File]::ReadAllBytes($iFile.FullName)
                        $originalSize = 0
                        $originalName = ""
                        $deletionTime = $null

                        if ($iFileContent.Length -ge 28) {
                            $originalSize = [BitConverter]::ToInt64($iFileContent, 8)
                            $deletionTimeTicks = [BitConverter]::ToInt64($iFileContent, 16)
                            $deletionTime = [DateTime]::FromFileTime($deletionTimeTicks)
                            $nameLength = [BitConverter]::ToInt32($iFileContent, 24)
                            if ($iFileContent.Length -ge 28 + $nameLength * 2) {
                                $originalName = [System.Text.Encoding]::Unicode.GetString($iFileContent, 28, $nameLength * 2).TrimEnd("`0")
                            }
                        }

                        $recycleSummary += [PSCustomObject]@{
                            SID             = $sidDir.Name
                            ArchivoI        = $iFile.Name
                            NombreOriginal  = $originalName
                            TamanoOriginal  = $originalSize
                            FechaEliminacion= if($deletionTime){$deletionTime.ToString($Script:Defaults.DateFormat)}else{"N/A"}
                        }
                    } catch { }
                }
                $c = Copy-ForensicDirectory -SourceDir $sidDir.FullName -DestDir $destSidDir -Phase "FASE_3"
                $collected += $c
            }

            if ($recycleSummary.Count -gt 0) {
                Export-CollectionToCSV -Data $recycleSummary -OutputPath (Join-Path $phasePath "RecycleBinSummary.csv") -Phase "FASE_3" -Description "Papelera de reciclaje"
                $collected++
            }
        }
    } catch {
        Write-ForensicLog -Phase "FASE_3" -Action "RECYCLE_BIN_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 3.8 Thumbcache ---
    Write-StepHeader "3.8 Thumbcache"
    try {
        $profiles = Get-UserProfiles
        foreach ($profile in $profiles) {
            $thumbDir = Join-Path $profile.ProfilePath "AppData\Local\Microsoft\Windows\Explorer"
            if (Test-Path $thumbDir) {
                $userThumbDest = Join-Path $phasePath "Thumbcache\$($profile.Username)"
                $c = Copy-ForensicDirectory -SourceDir $thumbDir -DestDir $userThumbDest -Phase "FASE_3" -Filter "thumbcache_*.db"
                $collected += $c
            }
        }
    } catch {
        Write-ForensicLog -Phase "FASE_3" -Action "THUMBCACHE_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 3.9 SRUM ---
    Write-StepHeader "3.9 SRUM (System Resource Usage Monitor)"
    try {
        $srumSrc = "C:\Windows\System32\sru\SRUDB.dat"
        $srumDst = Join-Path $phasePath "SRUM\SRUDB.dat"
        if (Test-Path $srumSrc) {
            if (Copy-ForensicFile -SourcePath $srumSrc -DestPath $srumDst -Phase "FASE_3" -UseVSSFirst) { $collected++ }
        } else {
            Write-ForensicLog -Phase "FASE_3" -Action "SRUM_NO_ENCONTRADO" -Target $srumSrc -Result "WARNING"
        }
    } catch {
        Write-ForensicLog -Phase "FASE_3" -Action "SRUM_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 3.10 Amcache ---
    Write-StepHeader "3.10 Amcache.hve"
    try {
        $amcacheSrc = "C:\Windows\AppCompat\Programs\Amcache.hve"
        $amcacheDst = Join-Path $phasePath "Amcache.hve"
        if (Test-Path $amcacheSrc) {
            if (Copy-ForensicFile -SourcePath $amcacheSrc -DestPath $amcacheDst -Phase "FASE_3" -UseVSSFirst) { $collected++ }
        }
    } catch {
        Write-ForensicLog -Phase "FASE_3" -Action "AMCACHE_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 3.11 ActivitiesCache (Windows Timeline) ---
    Write-StepHeader "3.11 ActivitiesCache (Windows Timeline)"
    try {
        $profiles = Get-UserProfiles
        foreach ($profile in $profiles) {
            $cdpBase = Join-Path $profile.ProfilePath "AppData\Local\ConnectedDevicesPlatform"
            if (Test-Path $cdpBase) {
                $dbFiles = Get-ChildItem $cdpBase -Filter "ActivitiesCache.db" -Recurse -ErrorAction SilentlyContinue
                foreach ($db in $dbFiles) {
                    $destPath = Join-Path $phasePath "ActivitiesCache\$($profile.Username)\$(Split-Path $db.Directory -Leaf)"
                    if (Copy-ForensicFile -SourcePath $db.FullName -DestPath (Join-Path $destPath "ActivitiesCache.db") -Phase "FASE_3" -UseVSSFirst) { $collected++ }
                }
            }
        }
    } catch {
        Write-ForensicLog -Phase "FASE_3" -Action "ACTIVITIES_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 3.12 BITS Transfer Jobs ---
    Write-StepHeader "3.12 BITS Transfer Jobs"
    try {
        $bitsOutput = & bitsadmin /list /allusers /verbose 2>$null
        if ($bitsOutput) {
            Export-CollectionToText -Content ($bitsOutput | Out-String) -OutputPath (Join-Path $phasePath "BITSJobs.txt") -Phase "FASE_3" -Description "BITS Jobs"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_3" -Action "BITS_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    # --- 3.13 PowerShell History ---
    Write-StepHeader "3.13 Historial de PowerShell"
    try {
        $profiles = Get-UserProfiles
        foreach ($profile in $profiles) {
            $psHistoryPath = Join-Path $profile.ProfilePath "AppData\Roaming\Microsoft\Windows\PowerShell\PSReadLine\ConsoleHost_history.txt"
            if (Test-Path $psHistoryPath) {
                $destPath = Join-Path $phasePath "PowerShellHistory\$($profile.Username)\ConsoleHost_history.txt"
                if (Copy-ForensicFile -SourcePath $psHistoryPath -DestPath $destPath -Phase "FASE_3") { $collected++ }
            }
        }
    } catch {
        Write-ForensicLog -Phase "FASE_3" -Action "PS_HISTORY_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 3.14 Fichero hosts ---
    Write-StepHeader "3.14 Fichero hosts"
    try {
        $hostsSrc = "C:\Windows\System32\drivers\etc\hosts"
        if (Test-Path $hostsSrc) {
            if (Copy-ForensicFile -SourcePath $hostsSrc -DestPath (Join-Path $phasePath "hosts") -Phase "FASE_3") { $collected++ }
        }
    } catch {
        Write-ForensicLog -Phase "FASE_3" -Action "HOSTS_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 3.15 Elementos de inicio ---
    Write-StepHeader "3.15 Elementos de Inicio"
    try {
        $startupPaths = @(
            "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\StartUp",
            "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\StartUp"
        )
        $profiles = Get-UserProfiles
        foreach ($profile in $profiles) {
            $startupPaths += Join-Path $profile.ProfilePath "AppData\Roaming\Microsoft\Windows\Start Menu\Programs\StartUp"
        }

        $startupItems = @()
        foreach ($path in ($startupPaths | Select-Object -Unique)) {
            if (Test-Path $path) {
                $destDir = Join-Path $phasePath "StartupItems\$(Split-Path $path -Leaf)"
                Get-ChildItem $path -ErrorAction SilentlyContinue | ForEach-Object {
                    $startupItems += [PSCustomObject]@{
                        Ruta     = $_.FullName
                        Nombre   = $_.Name
                        Tamano   = $_.Length
                        Creacion = $_.CreationTime.ToString($Script:Defaults.DateFormat)
                    }
                }
                $c = Copy-ForensicDirectory -SourceDir $path -DestDir $destDir -Phase "FASE_3"
                $collected += $c
            }
        }

        if ($startupItems.Count -gt 0) {
            Export-CollectionToCSV -Data $startupItems -OutputPath (Join-Path $phasePath "StartupItemsSummary.csv") -Phase "FASE_3" -Description "Elementos de inicio"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_3" -Action "STARTUP_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 3.16 Informacion de volumen NTFS ---
    Write-StepHeader "3.16 Informacion del Volumen NTFS"
    try {
        $ntfsInfo = Get-NTFSVolumeInfo -DriveLetter "C:"
        if ($ntfsInfo) {
            Export-CollectionToJSON -Data $ntfsInfo -OutputPath (Join-Path $phasePath "NTFSVolumeInfo.json") -Phase "FASE_3" -Description "Info volumen NTFS"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_3" -Action "NTFS_INFO_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    Write-PhaseComplete -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum] -ItemsCollected $collected
    return $collected
}

#endregion ===== Phase3-Filesystem.ps1 =====

#region ===== Phase4-Registry.ps1 =====
#region ============================================================
# MaPeriT.SW - FASE 4: REGISTRO DE WINDOWS
# ============================================================

function Invoke-Phase4-RegistryCollection {
    param([string]$OutputPath)

    $phasePath = Join-Path $OutputPath $Script:OutputDirs.Registry
    $hivesPath = Join-Path $phasePath "Hives"
    $extractedPath = Join-Path $phasePath "ExtractedValues"
    $phaseNum = 4
    $collected = 0

    Write-PhaseHeader -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum]

    # --- Montar hives de usuarios offline ---
    Write-StepHeader "4.0 Montaje de Hives de Usuarios Offline"
    Mount-OfflineUserHives

    # --- 4.1 Copia de Hives del sistema ---
    Write-StepHeader "4.1 Copia de Hives del Sistema"
    $systemHives = @{
        "HKLM\SAM"      = Join-Path $hivesPath "SAM"
        "HKLM\SYSTEM"   = Join-Path $hivesPath "SYSTEM"
        "HKLM\SOFTWARE" = Join-Path $hivesPath "SOFTWARE"
        "HKLM\SECURITY" = Join-Path $hivesPath "SECURITY"
        "HKU\.DEFAULT"  = Join-Path $hivesPath "DEFAULT"   # el hive DEFAULT se publica en HKU\.DEFAULT (HKLM\DEFAULT no existe)
    }

    foreach ($entry in $systemHives.GetEnumerator()) {
        $success = Save-RegistryHive -HiveKey $entry.Key -OutputPath $entry.Value -Phase "FASE_4"
        if ($success) { $collected++ }
    }

    # --- 4.2 NTUSER.DAT y UsrClass.dat por usuario ---
    Write-StepHeader "4.2 Hives de Usuario (NTUSER.DAT, UsrClass.dat)"
    $profiles = Get-UserProfiles
    foreach ($profile in $profiles) {
        $userHivesDir = Join-Path $hivesPath "Users\$($profile.Username)"
        if (-not (Test-Path $userHivesDir)) { New-Item -ItemType Directory -Path $userHivesDir -Force | Out-Null }

        $ntuser = Join-Path $profile.ProfilePath "NTUSER.DAT"
        if (Test-Path $ntuser) {
            if (Copy-ForensicFile -SourcePath $ntuser -DestPath (Join-Path $userHivesDir "NTUSER.DAT") -Phase "FASE_4" -UseVSSFirst) { $collected++ }
        }

        $usrClass = Join-Path $profile.ProfilePath "AppData\Local\Microsoft\Windows\UsrClass.dat"
        if (Test-Path $usrClass) {
            if (Copy-ForensicFile -SourcePath $usrClass -DestPath (Join-Path $userHivesDir "UsrClass.dat") -Phase "FASE_4" -UseVSSFirst) { $collected++ }
        }
    }

    # --- 4.3 Hora del ultimo apagado ---
    Write-StepHeader "4.3 Ultimo Apagado"
    try {
        $shutdownReg = Get-ItemProperty $Script:RegistryPaths.ShutdownTime -ErrorAction SilentlyContinue
        if ($shutdownReg -and $shutdownReg.ShutdownTime) {
            $bytes = $shutdownReg.ShutdownTime
            if ($bytes.Length -ge 8) {
                $ticks = [BitConverter]::ToInt64($bytes, 0)
                $shutdownTime = [DateTime]::FromFileTime($ticks)
                $result = [PSCustomObject]@{
                    UltimoApagado     = $shutdownTime.ToString($Script:Defaults.DateFormat)
                    UltimoApagadoUTC  = $shutdownTime.ToUniversalTime().ToString($Script:Defaults.DateFormat)
                    RawBytes          = [BitConverter]::ToString($bytes)
                }
                Export-CollectionToJSON -Data $result -OutputPath (Join-Path $extractedPath "LastShutdown.json") -Phase "FASE_4" -Description "Ultimo apagado"
                $collected++
            }
        }
    } catch {
        Write-ForensicLog -Phase "FASE_4" -Action "SHUTDOWN_TIME_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    # --- 4.4 Zona horaria ---
    Write-StepHeader "4.4 Zona Horaria del Registro"
    try {
        $tzReg = Get-ItemProperty $Script:RegistryPaths.TimeZone -ErrorAction SilentlyContinue
        if ($tzReg) {
            Export-CollectionToJSON -Data ($tzReg | Select-Object TimeZoneKeyName, StandardName, DaylightName, Bias, StandardBias, DaylightBias, ActiveTimeBias) -OutputPath (Join-Path $extractedPath "TimeZone.json") -Phase "FASE_4" -Description "Zona horaria"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_4" -Action "TIMEZONE_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    # --- 4.5 Historial de interfaces de red ---
    Write-StepHeader "4.5 Historial de Interfaces de Red"
    try {
        $networkProfiles = @()
        if (Test-Path $Script:RegistryPaths.NetworkList) {
            Get-ChildItem $Script:RegistryPaths.NetworkList -ErrorAction SilentlyContinue | ForEach-Object {
                $props = Get-ItemProperty $_.PsPath -ErrorAction SilentlyContinue
                $firstConnected = $null
                $lastConnected = $null
                try {
                    if ($props.DateCreated) {
                        $bytes = $props.DateCreated
                        $year = [BitConverter]::ToUInt16($bytes, 0)
                        $month = [BitConverter]::ToUInt16($bytes, 2)
                        $day = [BitConverter]::ToUInt16($bytes, 6)
                        $hour = [BitConverter]::ToUInt16($bytes, 8)
                        $min = [BitConverter]::ToUInt16($bytes, 10)
                        $firstConnected = "{0:D4}-{1:D2}-{2:D2} {3:D2}:{4:D2}" -f $year,$month,$day,$hour,$min
                    }
                    if ($props.DateLastConnected) {
                        $bytes = $props.DateLastConnected
                        $year = [BitConverter]::ToUInt16($bytes, 0)
                        $month = [BitConverter]::ToUInt16($bytes, 2)
                        $day = [BitConverter]::ToUInt16($bytes, 6)
                        $hour = [BitConverter]::ToUInt16($bytes, 8)
                        $min = [BitConverter]::ToUInt16($bytes, 10)
                        $lastConnected = "{0:D4}-{1:D2}-{2:D2} {3:D2}:{4:D2}" -f $year,$month,$day,$hour,$min
                    }
                } catch { }

                $networkProfiles += [PSCustomObject]@{
                    NombrePerfil     = $props.ProfileName
                    Descripcion      = $props.Description
                    Gestionado       = if($props.NameType -eq 6){"Si"}else{"No"}
                    Categoria        = switch($props.Category){ 0{"Publica"} 1{"Privada"} 2{"Dominio"} default{"Desconocida"} }
                    PrimeraConexion  = $firstConnected
                    UltimaConexion   = $lastConnected
                }
            }
        }
        if ($networkProfiles.Count -gt 0) {
            Export-CollectionToCSV -Data $networkProfiles -OutputPath (Join-Path $extractedPath "NetworkProfiles.csv") -Phase "FASE_4" -Description "Perfiles de red"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_4" -Action "NETWORK_PROFILES_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 4.6 Historial USB (USBSTOR) ---
    Write-StepHeader "4.6 Historial de Dispositivos USB"
    try {
        $usbDevices = @()
        if (Test-Path $Script:RegistryPaths.USBSTOR) {
            Get-ChildItem $Script:RegistryPaths.USBSTOR -ErrorAction SilentlyContinue | ForEach-Object {
                $deviceClass = $_.PSChildName
                Get-ChildItem $_.PsPath -ErrorAction SilentlyContinue | ForEach-Object {
                    $props = Get-ItemProperty $_.PsPath -ErrorAction SilentlyContinue
                    $serial = $_.PSChildName

                    $firstInstall = $null
                    $lastConnected = $null
                    $lastRemoved = $null
                    try {
                        $devProps = Get-ItemProperty "$($_.PsPath)\Properties\{83da6326-97a6-4088-9453-a1923f573b29}" -ErrorAction SilentlyContinue
                        if (Test-Path "$($_.PsPath)\Properties\{83da6326-97a6-4088-9453-a1923f573b29}\0064") {
                            $firstInstall = (Get-ItemPropertyValue "$($_.PsPath)\Properties\{83da6326-97a6-4088-9453-a1923f573b29}\0064" -Name "(default)" -ErrorAction SilentlyContinue)
                        }
                        if (Test-Path "$($_.PsPath)\Properties\{83da6326-97a6-4088-9453-a1923f573b29}\0066") {
                            $lastConnected = (Get-ItemPropertyValue "$($_.PsPath)\Properties\{83da6326-97a6-4088-9453-a1923f573b29}\0066" -Name "(default)" -ErrorAction SilentlyContinue)
                        }
                        if (Test-Path "$($_.PsPath)\Properties\{83da6326-97a6-4088-9453-a1923f573b29}\0067") {
                            $lastRemoved = (Get-ItemPropertyValue "$($_.PsPath)\Properties\{83da6326-97a6-4088-9453-a1923f573b29}\0067" -Name "(default)" -ErrorAction SilentlyContinue)
                        }
                    } catch { }

                    $usbDevices += [PSCustomObject]@{
                        ClaseDispositivo  = $deviceClass
                        NumeroSerie       = $serial
                        NombreAmigable    = $props.FriendlyName
                        Fabricante        = $props.Mfg
                        Servicio          = $props.Service
                        PrimeraInstalacion= $firstInstall
                        UltimaConexion    = $lastConnected
                        UltimaExtraccion  = $lastRemoved
                    }
                }
            }
        }
        if ($usbDevices.Count -gt 0) {
            Export-CollectionToCSV -Data $usbDevices -OutputPath (Join-Path $extractedPath "USBSTORDevices.csv") -Phase "FASE_4" -Description "Dispositivos USBSTOR"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_4" -Action "USBSTOR_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 4.7 MountedDevices ---
    Write-StepHeader "4.7 MountedDevices"
    try {
        $mountedDevices = @()
        $regKey = Get-Item $Script:RegistryPaths.MountedDevices -ErrorAction SilentlyContinue
        if ($regKey) {
            $regKey.GetValueNames() | ForEach-Object {
                $valueName = $_
                $data = $regKey.GetValue($valueName)
                $dataHex = if ($data -is [byte[]]) { [BitConverter]::ToString($data[0..([Math]::Min(63, $data.Length-1))]) } else { $data.ToString() }
                $mountedDevices += [PSCustomObject]@{
                    Nombre = $valueName
                    Datos  = $dataHex
                }
            }
        }
        if ($mountedDevices.Count -gt 0) {
            Export-CollectionToCSV -Data $mountedDevices -OutputPath (Join-Path $extractedPath "MountedDevices.csv") -Phase "FASE_4" -Description "MountedDevices"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_4" -Action "MOUNTED_DEVICES_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 4.8 UserAssist (ROT13) ---
    Write-StepHeader "4.8 UserAssist (ROT13)"
    try {
        $userAssistData = @()
        $profiles = Get-UserProfiles

        foreach ($profile in $profiles) {
            $sid = $profile.SID
            if (-not $sid) { continue }

            $effectiveSID = Get-EffectiveHKUSID -SID $sid -Username $profile.Username
            if (-not $effectiveSID) { continue }

            $uaBasePath = "Registry::HKU\$effectiveSID\$($Script:RegistryPaths.UserAssist)"

            Get-ChildItem $uaBasePath -ErrorAction SilentlyContinue | ForEach-Object {
                $guidPath = Join-Path $_.PsPath "Count"
                if (Test-Path $guidPath) {
                    $countKey = Get-Item $guidPath -ErrorAction SilentlyContinue
                    if ($countKey) {
                        $countKey.GetValueNames() | ForEach-Object {
                            $rot13Name = $_
                            $decodedName = Convert-ROT13 -Text $rot13Name
                            $valueData = $countKey.GetValue($rot13Name)

                            $runCount = 0
                            $lastRun = $null
                            if ($valueData -is [byte[]] -and $valueData.Length -ge 72) {
                                $runCount = [BitConverter]::ToUInt32($valueData, 4)
                                $lastRunTicks = [BitConverter]::ToInt64($valueData, 60)
                                if ($lastRunTicks -gt 0) {
                                    try { $lastRun = [DateTime]::FromFileTime($lastRunTicks).ToString($Script:Defaults.DateFormat) } catch { }
                                }
                            }

                            $userAssistData += [PSCustomObject]@{
                                Usuario      = $profile.Username
                                ProgramaROT13= $rot13Name
                                Programa     = $decodedName
                                Ejecuciones  = $runCount
                                UltimaEjecucion = $lastRun
                            }
                        }
                    }
                }
            }
        }

        if ($userAssistData.Count -gt 0) {
            Export-CollectionToCSV -Data $userAssistData -OutputPath (Join-Path $extractedPath "UserAssist.csv") -Phase "FASE_4" -Description "UserAssist"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_4" -Action "USERASSIST_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 4.9 ShimCache (AppCompatCache) ---
    Write-StepHeader "4.9 ShimCache (AppCompatCache)"
    try {
        $shimData = Get-ItemProperty $Script:RegistryPaths.AppCompatCache -ErrorAction SilentlyContinue
        if ($shimData -and $shimData.AppCompatCache) {
            $rawBytes = $shimData.AppCompatCache
            $shimOutputRaw = Join-Path $extractedPath "AppCompatCache.bin"
            [System.IO.File]::WriteAllBytes($shimOutputRaw, $rawBytes)
            $hash = Get-ForensicHash -FilePath $shimOutputRaw
            Add-ChainOfCustodyEntry -SourcePath $Script:RegistryPaths.AppCompatCache -DestPath $shimOutputRaw -Hash $hash -Method "Registry" -FileSize $rawBytes.Length
            $collected++

            $shimEntries = @(Parse-ShimCache -Data $rawBytes)
            if ($shimEntries.Count -gt 0) {
                Export-CollectionToCSV -Data $shimEntries -OutputPath (Join-Path $extractedPath "ShimCache.csv") -Phase "FASE_4" -Description "ShimCache"
                $collected++
            }
        }
    } catch {
        Write-ForensicLog -Phase "FASE_4" -Action "SHIMCACHE_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 4.10 BAM/DAM ---
    Write-StepHeader "4.10 BAM/DAM (Background Activity Monitor)"
    try {
        $bamData = @()
        foreach ($regPath in @($Script:RegistryPaths.BAM, $Script:RegistryPaths.DAM)) {
            if (Test-Path $regPath) {
                Get-ChildItem $regPath -ErrorAction SilentlyContinue | ForEach-Object {
                    $sid = $_.PSChildName
                    $props = Get-ItemProperty $_.PsPath -ErrorAction SilentlyContinue
                    if ($props) {
                        $props.PSObject.Properties | Where-Object { $_.Name -notlike "PS*" -and $_.Name -ne "(default)" -and $_.Name -ne "Version" -and $_.Name -ne "SequenceNumber" } | ForEach-Object {
                            $execPath = $_.Name
                            $valueBytes = $_.Value
                            $execTime = $null
                            if ($valueBytes -is [byte[]] -and $valueBytes.Length -ge 8) {
                                $ticks = [BitConverter]::ToInt64($valueBytes, 0)
                                if ($ticks -gt 0) {
                                    try { $execTime = [DateTime]::FromFileTime($ticks).ToString($Script:Defaults.DateFormat) } catch { }
                                }
                            }
                            $bamData += [PSCustomObject]@{
                                Origen    = if($regPath -like "*bam*"){"BAM"}else{"DAM"}
                                SID       = $sid
                                Ejecutable = $execPath
                                FechaEjecucion = $execTime
                            }
                        }
                    }
                }
            }
        }
        if ($bamData.Count -gt 0) {
            Export-CollectionToCSV -Data $bamData -OutputPath (Join-Path $extractedPath "BAM_DAM.csv") -Phase "FASE_4" -Description "BAM/DAM"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_4" -Action "BAM_DAM_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 4.11 Autoruns del Registro ---
    Write-StepHeader "4.11 Autoruns del Registro"
    try {
        $autoruns = @()
        $autorunPaths = @(
            @{Path=$Script:RegistryPaths.RunHKLM; Scope="HKLM"},
            @{Path=$Script:RegistryPaths.RunOnceHKLM; Scope="HKLM"},
            @{Path=$Script:RegistryPaths.RunHKCU; Scope="HKCU"},
            @{Path=$Script:RegistryPaths.RunOnceHKCU; Scope="HKCU"},
            @{Path=$Script:RegistryPaths.PoliciesRun; Scope="HKLM\Policies"}
        )

        foreach ($entry in $autorunPaths) {
            if (Test-Path $entry.Path) {
                $props = Get-ItemProperty $entry.Path -ErrorAction SilentlyContinue
                $props.PSObject.Properties | Where-Object { $_.Name -notlike "PS*" -and $_.Name -ne "(default)" } | ForEach-Object {
                    $autoruns += [PSCustomObject]@{
                        Scope    = $entry.Scope
                        Clave    = $entry.Path
                        Nombre   = $_.Name
                        Valor    = $_.Value
                    }
                }
            }
        }

        $winlogon = Get-ItemProperty $Script:RegistryPaths.Winlogon -ErrorAction SilentlyContinue
        if ($winlogon) {
            foreach ($prop in @("Shell", "Userinit", "Taskman")) {
                if ($winlogon.$prop) {
                    $autoruns += [PSCustomObject]@{
                        Scope  = "HKLM\Winlogon"
                        Clave  = $Script:RegistryPaths.Winlogon
                        Nombre = $prop
                        Valor  = $winlogon.$prop
                    }
                }
            }
        }

        if ($autoruns.Count -gt 0) {
            Export-CollectionToCSV -Data $autoruns -OutputPath (Join-Path $extractedPath "Autoruns.csv") -Phase "FASE_4" -Description "Autoruns del Registro"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_4" -Action "AUTORUNS_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 4.12 MRU Lists ---
    Write-StepHeader "4.12 Listas MRU (Elementos Recientes)"
    try {
        $mruData = @()
        $profiles = Get-UserProfiles
        foreach ($profile in $profiles) {
            $sid = $profile.SID
            if (-not $sid) { continue }
            $effectiveSID = Get-EffectiveHKUSID -SID $sid -Username $profile.Username
            if (-not $effectiveSID) { continue }
            $hkuBase = "Registry::HKU\$effectiveSID"

            $mruPaths = @(
                @{Name="RunMRU"; Path="$hkuBase\$($Script:RegistryPaths.RunMRU)"},
                @{Name="TypedPaths"; Path="$hkuBase\$($Script:RegistryPaths.TypedPaths)"},
                @{Name="TypedURLs"; Path="$hkuBase\$($Script:RegistryPaths.TypedURLs)"}
            )

            foreach ($mru in $mruPaths) {
                if (Test-Path $mru.Path -ErrorAction SilentlyContinue) {
                    $props = Get-ItemProperty $mru.Path -ErrorAction SilentlyContinue
                    $props.PSObject.Properties | Where-Object { $_.Name -notlike "PS*" -and $_.Name -ne "(default)" -and $_.Name -ne "MRUList" } | ForEach-Object {
                        $mruData += [PSCustomObject]@{
                            Usuario = $profile.Username
                            Tipo    = $mru.Name
                            Entrada = $_.Name
                            Valor   = $_.Value
                        }
                    }
                }
            }
        }
        if ($mruData.Count -gt 0) {
            Export-CollectionToCSV -Data $mruData -OutputPath (Join-Path $extractedPath "MRULists.csv") -Phase "FASE_4" -Description "Listas MRU"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_4" -Action "MRU_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 4.13 RDP History ---
    Write-StepHeader "4.13 Historial RDP"
    try {
        $rdpHistory = @()
        $profiles = Get-UserProfiles
        foreach ($profile in $profiles) {
            $sid = $profile.SID
            if (-not $sid) { continue }
            $effectiveSID = Get-EffectiveHKUSID -SID $sid -Username $profile.Username
            if (-not $effectiveSID) { continue }
            $hkuBase = "Registry::HKU\$effectiveSID"

            $rdpDefaultPath = "$hkuBase\Software\Microsoft\Terminal Server Client\Default"
            if (Test-Path $rdpDefaultPath -ErrorAction SilentlyContinue) {
                $props = Get-ItemProperty $rdpDefaultPath -ErrorAction SilentlyContinue
                $props.PSObject.Properties | Where-Object { $_.Name -like "MRU*" } | ForEach-Object {
                    $rdpHistory += [PSCustomObject]@{
                        Usuario  = $profile.Username
                        Tipo     = "MRU"
                        Servidor = $_.Value
                        UsuarioRDP = ""
                    }
                }
            }

            $rdpServersPath = "$hkuBase\Software\Microsoft\Terminal Server Client\Servers"
            if (Test-Path $rdpServersPath -ErrorAction SilentlyContinue) {
                Get-ChildItem $rdpServersPath -ErrorAction SilentlyContinue | ForEach-Object {
                    $serverName = $_.PSChildName
                    $serverProps = Get-ItemProperty $_.PsPath -ErrorAction SilentlyContinue
                    $rdpHistory += [PSCustomObject]@{
                        Usuario  = $profile.Username
                        Tipo     = "Servidor"
                        Servidor = $serverName
                        UsuarioRDP = $serverProps.UsernameHint
                    }
                }
            }
        }
        if ($rdpHistory.Count -gt 0) {
            Export-CollectionToCSV -Data $rdpHistory -OutputPath (Join-Path $extractedPath "RDPHistory.csv") -Phase "FASE_4" -Description "Historial RDP"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_4" -Action "RDP_HISTORY_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 4.14 Ultimo usuario logueado ---
    Write-StepHeader "4.14 Ultimo Usuario Logueado"
    try {
        $lastLogon = Get-ItemProperty $Script:RegistryPaths.LastLoggedOn -ErrorAction SilentlyContinue
        if ($lastLogon) {
            $result = [PSCustomObject]@{
                UltimoUsuario = $lastLogon.LastLoggedOnUser
                UltimoSAM     = $lastLogon.LastLoggedOnSAMUser
                UltimoDisplayName = $lastLogon.LastLoggedOnDisplayName
            }
            Export-CollectionToJSON -Data $result -OutputPath (Join-Path $extractedPath "LastLoggedOnUser.json") -Phase "FASE_4" -Description "Ultimo usuario logueado"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_4" -Action "LAST_LOGON_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    # --- Desmontar hives offline ---
    Write-StepHeader "4.15 Desmontaje de Hives Offline"
    Dismount-OfflineUserHives

    Write-PhaseComplete -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum] -ItemsCollected $collected
    return $collected
}

# --- Funciones auxiliares ---

function Convert-ROT13 {
    param([string]$Text)
    $result = [System.Text.StringBuilder]::new()
    foreach ($char in $Text.ToCharArray()) {
        if ($char -ge 'A' -and $char -le 'Z') {
            [void]$result.Append([char](((([int]$char - 65 + 13) % 26) + 65)))
        } elseif ($char -ge 'a' -and $char -le 'z') {
            [void]$result.Append([char](((([int]$char - 97 + 13) % 26) + 97)))
        } else {
            [void]$result.Append($char)
        }
    }
    return $result.ToString()
}

function Parse-ShimCache {
    <#
        Formato de AppCompatCache de Windows 10/11 (comprobado byte a byte contra
        datos reales): cabecera de longitud variable, cuyo tamano indica el primer
        DWORD (0x30 o 0x34), seguida de entradas consecutivas:
          +0   firma "10ts" (4 bytes)
          +4   valor desconocido (4)
          +8   tamano de la entrada contado desde +12 (4)
          +12  longitud de la ruta en bytes (2)
          +14  ruta en UTF-16LE
          a continuacion FILETIME de ultima modificacion (8), tamano de datos y datos
        Corregido en v1.7.5: antes la longitud se leia en +8 y la ruta desde +12
        (rutas corruptas) y el contador de entradas avanzaba cada 4 bytes con un
        tope de 1024, asi que solo se recorrian los primeros ~4 KB de la cache.
        Otros formatos (Windows 7/8) no se interpretan: queda constancia en el log
        y el binario completo se conserva en AppCompatCache.bin.
    #>
    param([byte[]]$Data)

    $entries = [System.Collections.Generic.List[object]]::new()
    if ($null -eq $Data -or $Data.Length -lt 52) { return $entries }

    try {
        $offset = [int][BitConverter]::ToUInt32($Data, 0)
        if (($offset -ne 0x30 -and $offset -ne 0x34) -or [BitConverter]::ToUInt32($Data, $offset) -ne 0x73743031) {
            Write-ForensicLog -Phase "FASE_4" -Action "SHIMCACHE_FORMATO_NO_SOPORTADO" -Target ("Cabecera 0x{0:X}: solo se interpreta el formato de Windows 10/11; el binario se conserva en AppCompatCache.bin" -f $offset) -Result "WARNING"
            return $entries
        }
        $indice = 0
        while ($offset + 14 -le $Data.Length) {
            if ([BitConverter]::ToUInt32($Data, $offset) -ne 0x73743031) { break }   # "10ts"
            $entrySize  = [int][BitConverter]::ToUInt32($Data, $offset + 8)
            $pathLength = [int][BitConverter]::ToUInt16($Data, $offset + 12)
            $pathOffset = $offset + 14
            if ($entrySize -le 0 -or $pathOffset + $pathLength + 8 -gt $Data.Length) { break }

            $path    = [System.Text.Encoding]::Unicode.GetString($Data, $pathOffset, $pathLength)
            $lastMod = $null
            $ticks   = [BitConverter]::ToInt64($Data, $pathOffset + $pathLength)
            if ($ticks -gt 0) {
                try { $lastMod = [DateTime]::FromFileTime($ticks).ToString($Script:Defaults.DateFormat) } catch { }
            }

            [void]$entries.Add([PSCustomObject]@{
                Indice              = $indice
                RutaEjecutable      = $path.TrimEnd("`0")
                UltimaModificacion  = $lastMod
            })
            $indice++
            $offset = $offset + 12 + $entrySize
        }
    } catch {
        Write-ForensicLog -Phase "FASE_4" -Action "SHIMCACHE_PARSE_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    return $entries
}

#endregion ===== Phase4-Registry.ps1 =====

#region ===== Phase5-EventLogs.ps1 =====
#region ============================================================
# MaPeriT.SW - FASE 5: LOGS DE EVENTOS
# ============================================================

function Invoke-Phase5-EventLogCollection {
    param([string]$OutputPath)

    $phasePath = Join-Path $OutputPath $Script:OutputDirs.EventLogs
    $rawPath = Join-Path $phasePath "RawLogs"
    $analysisPath = Join-Path $phasePath "Analysis"
    $phaseNum = 5
    $collected = 0

    Write-PhaseHeader -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum]

    # --- 5.1 Copia de todos los .evtx ---
    Write-StepHeader "5.1 Copia de Logs de Eventos (.evtx)"
    try {
        $evtxSource = "C:\Windows\System32\winevt\Logs"
        if (Test-Path $evtxSource) {
            $evtxFiles = Get-ChildItem $evtxSource -Filter "*.evtx" -ErrorAction SilentlyContinue
            $total = $evtxFiles.Count
            $current = 0
            foreach ($file in $evtxFiles) {
                $current++
                if ($current % 25 -eq 0) {
                    Write-Host "`r  [*] Copiando logs: $current/$total" -NoNewline -ForegroundColor Yellow
                }
                $destFile = Join-Path $rawPath $file.Name
                $success = Copy-ForensicFile -SourcePath $file.FullName -DestPath $destFile -Phase "FASE_5"
                if ($success) { $collected++ }
            }
            Write-Host "`r  [+] $collected logs copiados de $total                    " -ForegroundColor Green
        }
    } catch {
        Write-ForensicLog -Phase "FASE_5" -Action "EVTX_COPY_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 5.2 Analisis de eventos de seguridad: Logon ---
    Write-StepHeader "5.2 Eventos de Inicio de Sesion"
    try {
        $logonEvents = [System.Collections.ArrayList]::new()
        $events = Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4624} -MaxEvents $Script:Defaults.MaxEventsPerQuery -ErrorAction SilentlyContinue
        Test-EventQueryTruncated -Count @($events).Count -MaxEvents $Script:Defaults.MaxEventsPerQuery -Phase "FASE_5" -LogDescription "Security 4624 (Logon exitoso)"
        foreach ($evt in $events) {
            $p = $evt.Properties
            $logonType = if ($p.Count -gt 8) { $p[8].Value } else { 0 }
            [void]$logonEvents.Add([PSCustomObject]@{
                Timestamp       = $evt.TimeCreated.ToString($Script:Defaults.DateFormat)
                EventID         = $evt.Id
                UsuarioDestino  = if ($p.Count -gt 5) { $p[5].Value } else { "" }
                DominioDestino  = if ($p.Count -gt 6) { $p[6].Value } else { "" }
                TipoLogon       = $logonType
                TipoLogonDesc   = $Script:LogonTypes[[int]$logonType]
                IPOrigen        = if ($p.Count -gt 18) { $p[18].Value } else { "" }
                ProcesoLogon    = if ($p.Count -gt 9) { $p[9].Value } else { "" }
                PaqueteAuth     = if ($p.Count -gt 10) { $p[10].Value } else { "" }
                LogonID         = if ($p.Count -gt 7) { $p[7].Value } else { "" }
            })
        }
        if ($logonEvents.Count -gt 0) {
            Export-CollectionToCSV -Data $logonEvents -OutputPath (Join-Path $analysisPath "SecurityLogon.csv") -Phase "FASE_5" -Description "Eventos logon exitoso (4624)"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_5" -Action "LOGON_EVENTS_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 5.3 Logons fallidos ---
    Write-StepHeader "5.3 Intentos de Sesion Fallidos"
    try {
        $failedLogons = [System.Collections.ArrayList]::new()
        $events = Get-WinEvent -FilterHashtable @{LogName='Security'; Id=4625} -MaxEvents $Script:Defaults.MaxEventsPerQuery -ErrorAction SilentlyContinue
        Test-EventQueryTruncated -Count @($events).Count -MaxEvents $Script:Defaults.MaxEventsPerQuery -Phase "FASE_5" -LogDescription "Security 4625 (Logon fallido)"
        foreach ($evt in $events) {
            $p = $evt.Properties
            [void]$failedLogons.Add([PSCustomObject]@{
                Timestamp       = $evt.TimeCreated.ToString($Script:Defaults.DateFormat)
                UsuarioDestino  = if ($p.Count -gt 5) { $p[5].Value } else { "" }
                DominioDestino  = if ($p.Count -gt 6) { $p[6].Value } else { "" }
                TipoLogon       = if ($p.Count -gt 10) { $p[10].Value } else { "" }
                RazonFallo      = if ($p.Count -gt 8) { $p[8].Value } else { "" }
                Status          = if ($p.Count -gt 7) { "0x{0:X}" -f $p[7].Value } else { "" }
                SubStatus       = if ($p.Count -gt 9) { "0x{0:X}" -f $p[9].Value } else { "" }
                IPOrigen        = if ($p.Count -gt 19) { $p[19].Value } else { "" }
                ProcesoLogon    = if ($p.Count -gt 11) { $p[11].Value } else { "" }
            })
        }
        if ($failedLogons.Count -gt 0) {
            Export-CollectionToCSV -Data $failedLogons -OutputPath (Join-Path $analysisPath "SecurityFailedLogon.csv") -Phase "FASE_5" -Description "Logons fallidos (4625)"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_5" -Action "FAILED_LOGON_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    # --- 5.4 Cambios en cuentas ---
    Write-StepHeader "5.4 Gestion de Cuentas"
    try {
        $accountIds = $Script:SecurityEventIDs.AccountCreated + $Script:SecurityEventIDs.AccountEnabled +
                      $Script:SecurityEventIDs.PasswordChange + $Script:SecurityEventIDs.AccountDisabled +
                      $Script:SecurityEventIDs.AccountDeleted + $Script:SecurityEventIDs.AccountChanged +
                      $Script:SecurityEventIDs.AccountLocked
        $accountEvents = [System.Collections.ArrayList]::new()
        $events = Get-WinEvent -FilterHashtable @{LogName='Security'; Id=$accountIds} -MaxEvents $Script:Defaults.MaxEventsPerQuery -ErrorAction SilentlyContinue
        Test-EventQueryTruncated -Count @($events).Count -MaxEvents $Script:Defaults.MaxEventsPerQuery -Phase "FASE_5" -LogDescription "Security (Gestion de cuentas)"
        foreach ($evt in $events) {
            $p = $evt.Properties
            [void]$accountEvents.Add([PSCustomObject]@{
                Timestamp      = $evt.TimeCreated.ToString($Script:Defaults.DateFormat)
                EventID        = $evt.Id
                Descripcion    = $evt.Message -split "`n" | Select-Object -First 1
                CuentaObjetivo = if ($p.Count -gt 0) { $p[0].Value } else { "" }
                CuentaOrigen   = if ($p.Count -gt 4) { $p[4].Value } else { "" }
            })
        }
        if ($accountEvents.Count -gt 0) {
            Export-CollectionToCSV -Data $accountEvents -OutputPath (Join-Path $analysisPath "SecurityAccountChanges.csv") -Phase "FASE_5" -Description "Gestion de cuentas"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_5" -Action "ACCOUNT_EVENTS_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    # --- 5.5 Eventos de sistema: Servicios ---
    Write-StepHeader "5.5 Eventos de Servicios del Sistema"
    try {
        $serviceIds = $Script:SystemEventIDs.ServiceInstalled + $Script:SystemEventIDs.ServiceCrash +
                      $Script:SystemEventIDs.ServiceStartChange
        $serviceEvents = @()
        $events = Get-WinEvent -FilterHashtable @{LogName='System'; Id=$serviceIds} -MaxEvents $Script:Defaults.MaxEventsPerQuery -ErrorAction SilentlyContinue
        Test-EventQueryTruncated -Count @($events).Count -MaxEvents $Script:Defaults.MaxEventsPerQuery -Phase "FASE_5" -LogDescription "System (Eventos de servicios)"
        foreach ($evt in $events) {
            $serviceEvents += [PSCustomObject]@{
                Timestamp   = $evt.TimeCreated.ToString($Script:Defaults.DateFormat)
                EventID     = $evt.Id
                Proveedor   = $evt.ProviderName
                Mensaje     = ($evt.Message -split "`n" | Select-Object -First 2) -join " "
            }
        }
        if ($serviceEvents.Count -gt 0) {
            Export-CollectionToCSV -Data $serviceEvents -OutputPath (Join-Path $analysisPath "SystemServices.csv") -Phase "FASE_5" -Description "Eventos de servicios"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_5" -Action "SERVICE_EVENTS_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    # --- 5.6 Shutdowns/Startups ---
    Write-StepHeader "5.6 Arranques y Apagados"
    try {
        $shutdownIds = $Script:SystemEventIDs.ShutdownInitiated + $Script:SystemEventIDs.EventLogStarted +
                       $Script:SystemEventIDs.EventLogStopped + $Script:SystemEventIDs.UnexpectedShutdown +
                       $Script:SystemEventIDs.SystemBoot
        $shutdownEvents = @()
        $events = Get-WinEvent -FilterHashtable @{LogName='System'; Id=$shutdownIds} -MaxEvents 5000 -ErrorAction SilentlyContinue
        Test-EventQueryTruncated -Count @($events).Count -MaxEvents 5000 -Phase "FASE_5" -LogDescription "System (Arranques/Apagados)"
        foreach ($evt in $events) {
            $shutdownEvents += [PSCustomObject]@{
                Timestamp   = $evt.TimeCreated.ToString($Script:Defaults.DateFormat)
                EventID     = $evt.Id
                Tipo        = switch ($evt.Id) {
                    1074 { "Apagado/Reinicio iniciado" }
                    6005 { "Servicio de log de eventos iniciado" }
                    6006 { "Servicio de log de eventos detenido" }
                    6008 { "APAGADO INESPERADO" }
                    6009 { "Arranque del sistema" }
                    default { "Otro" }
                }
                Mensaje     = ($evt.Message -split "`n" | Select-Object -First 1)
            }
        }
        if ($shutdownEvents.Count -gt 0) {
            Export-CollectionToCSV -Data $shutdownEvents -OutputPath (Join-Path $analysisPath "SystemShutdowns.csv") -Phase "FASE_5" -Description "Arranques y apagados"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_5" -Action "SHUTDOWN_EVENTS_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    # --- 5.7 PowerShell Script Block Logging ---
    Write-StepHeader "5.7 PowerShell Script Block Logging"
    try {
        $psEvents = [System.Collections.ArrayList]::new()
        $events = Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-PowerShell/Operational'; Id=4104} -MaxEvents 10000 -ErrorAction SilentlyContinue
        Test-EventQueryTruncated -Count @($events).Count -MaxEvents 10000 -Phase "FASE_5" -LogDescription "PowerShell Operational 4104 (Script Block Logging)"
        foreach ($evt in $events) {
            $p = $evt.Properties
            [void]$psEvents.Add([PSCustomObject]@{
                Timestamp   = $evt.TimeCreated.ToString($Script:Defaults.DateFormat)
                ScriptBlock = if ($p.Count -gt 2) { $p[2].Value } else { "" }
                ScriptPath  = if ($p.Count -gt 4) { $p[4].Value } else { "" }
                Level       = $evt.Level
            })
        }
        if ($psEvents.Count -gt 0) {
            Export-CollectionToCSV -Data $psEvents -OutputPath (Join-Path $analysisPath "PowerShellScripts.csv") -Phase "FASE_5" -Description "PowerShell scripts ejecutados"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_5" -Action "POWERSHELL_EVENTS_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    # --- 5.7b PowerShell Module Logging (4103) ---
    Write-StepHeader "5.7b PowerShell Module Logging"
    try {
        $psModEvents = [System.Collections.ArrayList]::new()
        $events = Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-PowerShell/Operational'; Id=4103} -MaxEvents 10000 -ErrorAction SilentlyContinue
        Test-EventQueryTruncated -Count @($events).Count -MaxEvents 10000 -Phase "FASE_5" -LogDescription "PowerShell Operational 4103 (Module Logging)"
        foreach ($evt in $events) {
            $p = $evt.Properties
            [void]$psModEvents.Add([PSCustomObject]@{
                Timestamp  = $evt.TimeCreated.ToString($Script:Defaults.DateFormat)
                Payload    = if ($p.Count -gt 0) { $p[0].Value } else { "" }
                ContextInfo = if ($p.Count -gt 2) {
                    $ctx = $p[2].Value.ToString()
                    if ($ctx.Length -gt 300) { $ctx.Substring(0, 300) + "..." } else { $ctx }
                } else { "" }
                Level      = $evt.Level
            })
        }
        if ($psModEvents.Count -gt 0) {
            Export-CollectionToCSV -Data $psModEvents -OutputPath (Join-Path $analysisPath "PowerShellModuleLog.csv") -Phase "FASE_5" -Description "PowerShell Module Logging (4103)"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_5" -Action "PS_MODULE_LOG_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    # --- 5.8 Task Scheduler ---
    Write-StepHeader "5.8 Eventos del Programador de Tareas"
    try {
        $taskIds = $Script:TaskSchedulerEventIDs.TaskRegistered + $Script:TaskSchedulerEventIDs.TaskUpdated + $Script:TaskSchedulerEventIDs.TaskDeleted +
                   $Script:TaskSchedulerEventIDs.TaskStarted + $Script:TaskSchedulerEventIDs.TaskCompleted
        $taskEvents = @()
        $events = Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-TaskScheduler/Operational'; Id=$taskIds} -MaxEvents 10000 -ErrorAction SilentlyContinue
        Test-EventQueryTruncated -Count @($events).Count -MaxEvents 10000 -Phase "FASE_5" -LogDescription "TaskScheduler Operational"
        foreach ($evt in $events) {
            $taskEvents += [PSCustomObject]@{
                Timestamp = $evt.TimeCreated.ToString($Script:Defaults.DateFormat)
                EventID   = $evt.Id
                Accion    = switch ($evt.Id) {
                    106 { "Tarea registrada" }
                    140 { "Tarea actualizada" }
                    141 { "Tarea eliminada" }
                    200 { "Tarea iniciada" }
                    201 { "Tarea completada" }
                    default { "Otro" }
                }
                Mensaje   = ($evt.Message -split "`n" | Select-Object -First 1)
            }
        }
        if ($taskEvents.Count -gt 0) {
            Export-CollectionToCSV -Data $taskEvents -OutputPath (Join-Path $analysisPath "TaskScheduler.csv") -Phase "FASE_5" -Description "Eventos del programador"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_5" -Action "TASK_SCHEDULER_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    # --- 5.9 Windows Defender ---
    Write-StepHeader "5.9 Eventos de Windows Defender"
    try {
        $defenderEvents = @()
        $events = Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-Windows Defender/Operational'} -MaxEvents 5000 -ErrorAction SilentlyContinue
        Test-EventQueryTruncated -Count @($events).Count -MaxEvents 5000 -Phase "FASE_5" -LogDescription "Windows Defender Operational"
        foreach ($evt in $events) {
            $defenderEvents += [PSCustomObject]@{
                Timestamp = $evt.TimeCreated.ToString($Script:Defaults.DateFormat)
                EventID   = $evt.Id
                Level     = $evt.LevelDisplayName
                Mensaje   = ($evt.Message -split "`n" | Select-Object -First 2) -join " "
            }
        }
        if ($defenderEvents.Count -gt 0) {
            Export-CollectionToCSV -Data $defenderEvents -OutputPath (Join-Path $analysisPath "DefenderEvents.csv") -Phase "FASE_5" -Description "Eventos Defender"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_5" -Action "DEFENDER_EVENTS_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    # --- 5.10 RDP/Terminal Services ---
    Write-StepHeader "5.10 Eventos RDP/Terminal Services"
    try {
        $rdpEvents = @()
        $rdpLogs = @(
            "Microsoft-Windows-TerminalServices-LocalSessionManager/Operational",
            "Microsoft-Windows-TerminalServices-RemoteConnectionManager/Operational"
        )
        foreach ($logName in $rdpLogs) {
            $events = Get-WinEvent -FilterHashtable @{LogName=$logName} -MaxEvents 5000 -ErrorAction SilentlyContinue
            Test-EventQueryTruncated -Count @($events).Count -MaxEvents 5000 -Phase "FASE_5" -LogDescription $logName
            foreach ($evt in $events) {
                $rdpEvents += [PSCustomObject]@{
                    Timestamp = $evt.TimeCreated.ToString($Script:Defaults.DateFormat)
                    Log       = $logName -replace 'Microsoft-Windows-', ''
                    EventID   = $evt.Id
                    Accion    = switch ($evt.Id) {
                        21 { "Sesion RDP iniciada" }
                        22 { "Shell RDP iniciado" }
                        23 { "Sesion RDP cerrada" }
                        24 { "Sesion RDP desconectada" }
                        25 { "Sesion RDP reconectada" }
                        default { "Evento RDP $($evt.Id)" }
                    }
                    Mensaje   = ($evt.Message -split "`n" | Select-Object -First 1)
                }
            }
        }
        if ($rdpEvents.Count -gt 0) {
            Export-CollectionToCSV -Data $rdpEvents -OutputPath (Join-Path $analysisPath "RDPSessions.csv") -Phase "FASE_5" -Description "Eventos RDP"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_5" -Action "RDP_EVENTS_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    # --- 5.11 Sysmon (si disponible) ---
    Write-StepHeader "5.11 Eventos Sysmon (si disponible)"
    try {
        $sysmonEvents = @()
        $events = Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-Sysmon/Operational'} -MaxEvents 20000 -ErrorAction SilentlyContinue
        Test-EventQueryTruncated -Count @($events).Count -MaxEvents 20000 -Phase "FASE_5" -LogDescription "Sysmon Operational"
        if ($events) {
            foreach ($evt in $events) {
                $sysmonEvents += [PSCustomObject]@{
                    Timestamp = $evt.TimeCreated.ToString($Script:Defaults.DateFormat)
                    EventID   = $evt.Id
                    Tipo      = switch ($evt.Id) {
                        1  { "Proceso creado" }
                        3  { "Conexion de red" }
                        5  { "Proceso terminado" }
                        7  { "Imagen cargada" }
                        8  { "CreateRemoteThread" }
                        11 { "Fichero creado" }
                        12 { "Registro modificado" }
                        13 { "Valor registro cambiado" }
                        22 { "Consulta DNS" }
                        default { "Evento $($evt.Id)" }
                    }
                    Mensaje   = ($evt.Message -split "`n" | Select-Object -First 2) -join " "
                }
            }
            Export-CollectionToCSV -Data $sysmonEvents -OutputPath (Join-Path $analysisPath "Sysmon.csv") -Phase "FASE_5" -Description "Eventos Sysmon"
            $collected++
        } else {
            Write-ForensicLog -Phase "FASE_5" -Action "SYSMON_NO_INSTALADO" -Target "Sysmon no detectado" -Result "WARNING"
        }
    } catch {
        Write-ForensicLog -Phase "FASE_5" -Action "SYSMON_NO_DISPONIBLE" -Target "Log de Sysmon no existe" -Result "WARNING"
    }

    # --- 5.12 Application crashes ---
    Write-StepHeader "5.12 Errores de Aplicacion"
    try {
        $crashIds = $Script:AppEventIDs.AppCrash + $Script:AppEventIDs.WER + $Script:AppEventIDs.AppHang
        $crashEvents = @()
        $events = Get-WinEvent -FilterHashtable @{LogName='Application'; Id=$crashIds} -MaxEvents 5000 -ErrorAction SilentlyContinue
        Test-EventQueryTruncated -Count @($events).Count -MaxEvents 5000 -Phase "FASE_5" -LogDescription "Application (Crashes)"
        foreach ($evt in $events) {
            $crashEvents += [PSCustomObject]@{
                Timestamp = $evt.TimeCreated.ToString($Script:Defaults.DateFormat)
                EventID   = $evt.Id
                Tipo      = switch ($evt.Id) { 1000 {"Crash"} 1001 {"WER"} 1002 {"Hang"} default {"Otro"} }
                Mensaje   = ($evt.Message -split "`n" | Select-Object -First 3) -join " "
            }
        }
        if ($crashEvents.Count -gt 0) {
            Export-CollectionToCSV -Data $crashEvents -OutputPath (Join-Path $analysisPath "AppCrashes.csv") -Phase "FASE_5" -Description "Crashes de aplicacion"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_5" -Action "APP_CRASH_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    Write-PhaseComplete -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum] -ItemsCollected $collected
    return $collected
}

#endregion ===== Phase5-EventLogs.ps1 =====

#region ===== Phase6-Browsers.ps1 =====
#region ============================================================
# MaPeriT.SW - FASE 6: NAVEGADORES WEB
# ============================================================

function Invoke-Phase6-BrowserCollection {
    param([string]$OutputPath)

    $phasePath = Join-Path $OutputPath $Script:OutputDirs.Browsers
    $phaseNum = 6
    $collected = 0

    Write-PhaseHeader -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum]

    $profiles = Get-UserProfiles

    # --- 6.1 Google Chrome ---
    Write-StepHeader "6.1 Google Chrome"
    try {
        foreach ($profile in $profiles) {
            $chromePath = Join-Path $profile.ProfilePath $Script:BrowserPaths.Chrome
            if (Test-Path $chromePath) {
                $chromeProfiles = @("Default") + (Get-ChildItem $chromePath -Directory -Filter "Profile *" -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })
                foreach ($chromeProfile in $chromeProfiles) {
                    $profileDir = Join-Path $chromePath $chromeProfile
                    if (-not (Test-Path $profileDir)) { continue }
                    $destDir = Join-Path $phasePath "Chrome\$($profile.Username)\$chromeProfile"

                    foreach ($file in $Script:ChromiumFiles) {
                        $srcFile = Join-Path $profileDir $file
                        if (Test-Path $srcFile) {
                            $success = Copy-ForensicFile -SourcePath $srcFile -DestPath (Join-Path $destDir $file) -Phase "FASE_6" -UseVSSFirst
                            if ($success) { $collected++ }
                        }
                    }

                    $extDir = Join-Path $profileDir "Extensions"
                    if (Test-Path $extDir) {
                        $extData = @()
                        Get-ChildItem $extDir -Directory -ErrorAction SilentlyContinue | ForEach-Object {
                            $extId = $_.Name
                            $manifestFiles = Get-ChildItem $_.FullName -Recurse -Filter "manifest.json" -ErrorAction SilentlyContinue | Select-Object -First 1
                            if ($manifestFiles) {
                                try {
                                    $manifest = Get-Content $manifestFiles.FullName -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json -ErrorAction SilentlyContinue
                                    $extData += [PSCustomObject]@{
                                        Usuario  = $profile.Username
                                        Perfil   = $chromeProfile
                                        ExtID    = $extId
                                        Nombre   = $manifest.name
                                        Version  = $manifest.version
                                        Desc     = if($manifest.description.Length -gt 100){$manifest.description.Substring(0,100)}else{$manifest.description}
                                    }
                                } catch { }
                            }
                        }
                        if ($extData.Count -gt 0) {
                            Export-CollectionToCSV -Data $extData -OutputPath (Join-Path $destDir "Extensions.csv") -Phase "FASE_6" -Description "Extensiones Chrome"
                            $collected++
                        }
                    }
                }
            }
        }
    } catch {
        Write-ForensicLog -Phase "FASE_6" -Action "CHROME_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 6.2 Microsoft Edge ---
    Write-StepHeader "6.2 Microsoft Edge"
    try {
        foreach ($profile in $profiles) {
            $edgePath = Join-Path $profile.ProfilePath $Script:BrowserPaths.Edge
            if (Test-Path $edgePath) {
                $edgeProfiles = @("Default") + (Get-ChildItem $edgePath -Directory -Filter "Profile *" -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })
                foreach ($edgeProfile in $edgeProfiles) {
                    $profileDir = Join-Path $edgePath $edgeProfile
                    if (-not (Test-Path $profileDir)) { continue }
                    $destDir = Join-Path $phasePath "Edge\$($profile.Username)\$edgeProfile"

                    foreach ($file in $Script:ChromiumFiles) {
                        $srcFile = Join-Path $profileDir $file
                        if (Test-Path $srcFile) {
                            $success = Copy-ForensicFile -SourcePath $srcFile -DestPath (Join-Path $destDir $file) -Phase "FASE_6" -UseVSSFirst
                            if ($success) { $collected++ }
                        }
                    }
                }
            }
        }
    } catch {
        Write-ForensicLog -Phase "FASE_6" -Action "EDGE_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 6.3 Brave ---
    Write-StepHeader "6.3 Brave"
    try {
        foreach ($profile in $profiles) {
            $bravePath = Join-Path $profile.ProfilePath $Script:BrowserPaths.Brave
            if (Test-Path $bravePath) {
                $braveProfiles = @("Default") + (Get-ChildItem $bravePath -Directory -Filter "Profile *" -ErrorAction SilentlyContinue | ForEach-Object { $_.Name })
                foreach ($braveProfile in $braveProfiles) {
                    $profileDir = Join-Path $bravePath $braveProfile
                    if (-not (Test-Path $profileDir)) { continue }
                    $destDir = Join-Path $phasePath "Brave\$($profile.Username)\$braveProfile"
                    foreach ($file in $Script:ChromiumFiles) {
                        $srcFile = Join-Path $profileDir $file
                        if (Test-Path $srcFile) {
                            $success = Copy-ForensicFile -SourcePath $srcFile -DestPath (Join-Path $destDir $file) -Phase "FASE_6" -UseVSSFirst
                            if ($success) { $collected++ }
                        }
                    }
                }
            }
        }
    } catch {
        Write-ForensicLog -Phase "FASE_6" -Action "BRAVE_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 6.4 Mozilla Firefox ---
    Write-StepHeader "6.4 Mozilla Firefox"
    try {
        foreach ($profile in $profiles) {
            $ffPath = Join-Path $profile.ProfilePath $Script:BrowserPaths.Firefox
            if (Test-Path $ffPath) {
                $ffProfiles = Get-ChildItem $ffPath -Directory -ErrorAction SilentlyContinue
                foreach ($ffProfile in $ffProfiles) {
                    $destDir = Join-Path $phasePath "Firefox\$($profile.Username)\$($ffProfile.Name)"
                    foreach ($file in $Script:FirefoxFiles) {
                        $srcFile = Join-Path $ffProfile.FullName $file
                        if (Test-Path $srcFile) {
                            $success = Copy-ForensicFile -SourcePath $srcFile -DestPath (Join-Path $destDir $file) -Phase "FASE_6" -UseVSSFirst
                            if ($success) { $collected++ }
                        }
                    }
                }
            }
        }
    } catch {
        Write-ForensicLog -Phase "FASE_6" -Action "FIREFOX_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 6.5 Internet Explorer ---
    Write-StepHeader "6.5 Internet Explorer / Legacy"
    try {
        foreach ($profile in $profiles) {
            $iePath = Join-Path $profile.ProfilePath $Script:BrowserPaths.IE
            if (Test-Path $iePath) {
                $webCacheFile = Join-Path $iePath "WebCacheV01.dat"
                if (Test-Path $webCacheFile) {
                    $destDir = Join-Path $phasePath "InternetExplorer\$($profile.Username)"
                    $success = Copy-ForensicFile -SourcePath $webCacheFile -DestPath (Join-Path $destDir "WebCacheV01.dat") -Phase "FASE_6" -UseVSSFirst
                    if ($success) { $collected++ }
                }
            }
        }
    } catch {
        Write-ForensicLog -Phase "FASE_6" -Action "IE_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 6.6 Resumen de navegadores detectados ---
    Write-StepHeader "6.6 Resumen de Navegadores Detectados"
    try {
        $browserSummary = @()
        foreach ($profile in $profiles) {
            foreach ($browser in @("Chrome","Edge","Brave","Firefox","InternetExplorer")) {
                $browserDir = Join-Path $phasePath "$browser\$($profile.Username)"
                if (Test-Path $browserDir) {
                    $fileCount = (Get-ChildItem $browserDir -Recurse -File -ErrorAction SilentlyContinue).Count
                    $totalSize = (Get-ChildItem $browserDir -Recurse -File -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
                    $browserSummary += [PSCustomObject]@{
                        Usuario       = $profile.Username
                        Navegador     = $browser
                        FicherosCopiados = $fileCount
                        TamanoTotalMB = [math]::Round($totalSize / 1MB, 2)
                    }
                }
            }
        }
        if ($browserSummary.Count -gt 0) {
            Export-CollectionToCSV -Data $browserSummary -OutputPath (Join-Path $phasePath "BrowserSummary.csv") -Phase "FASE_6" -Description "Resumen navegadores"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_6" -Action "BROWSER_SUMMARY_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    Write-PhaseComplete -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum] -ItemsCollected $collected
    return $collected
}

#endregion ===== Phase6-Browsers.ps1 =====

#region ===== Phase7-Communications.ps1 =====
#region ============================================================
# MaPeriT.SW - FASE 7: COMUNICACIONES
# ============================================================

function Invoke-Phase7-CommunicationsCollection {
    param([string]$OutputPath)

    $phasePath = Join-Path $OutputPath $Script:OutputDirs.Communications
    $phaseNum = 7
    $collected = 0

    Write-PhaseHeader -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum]
    $profiles = Get-UserProfiles

    # --- 7.1 Outlook PST/OST ---
    Write-StepHeader "7.1 Microsoft Outlook (PST/OST)"
    try {
        $outlookFiles = @()
        foreach ($profile in $profiles) {
            $ostDir = Join-Path $profile.ProfilePath $Script:CommunicationPaths.OutlookOST
            $pstDir = Join-Path $profile.ProfilePath $Script:CommunicationPaths.OutlookPST

            foreach ($dir in @($ostDir, $pstDir)) {
                if (Test-Path $dir) {
                    Get-ChildItem $dir -Include "*.ost","*.pst" -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
                        $sizeMB = [math]::Round($_.Length / 1MB, 2)
                        $outlookFiles += [PSCustomObject]@{
                            Usuario    = $profile.Username
                            Fichero    = $_.Name
                            RutaCompleta = $_.FullName
                            TamanoMB   = $sizeMB
                            Tipo       = $_.Extension.ToUpper().TrimStart(".")
                            Modificado = $_.LastWriteTime.ToString($Script:Defaults.DateFormat)
                            Creacion   = $_.CreationTime.ToString($Script:Defaults.DateFormat)
                        }

                        if ($_.Length -le ($Script:Defaults.LargeFileThresholdMB * 1MB) -or $CopyLargeFiles) {
                            $destPath = Join-Path $phasePath "Outlook\$($profile.Username)\$($_.Name)"
                            $success = Copy-ForensicFile -SourcePath $_.FullName -DestPath $destPath -Phase "FASE_7" -UseVSSFirst
                            if ($success) { $collected++ }
                        } else {
                            Write-ForensicLog -Phase "FASE_7" -Action "OUTLOOK_GRANDE" -Target "$($_.Name) ($sizeMB MB) - solo metadatos (use -CopyLargeFiles para copiar)" -Result "WARNING"
                        }
                    }
                }
            }
        }
        if ($outlookFiles.Count -gt 0) {
            Export-CollectionToCSV -Data $outlookFiles -OutputPath (Join-Path $phasePath "OutlookFiles.csv") -Phase "FASE_7" -Description "Ficheros Outlook"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_7" -Action "OUTLOOK_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 7.2 Microsoft Teams ---
    Write-StepHeader "7.2 Microsoft Teams"
    try {
        foreach ($profile in $profiles) {
            $teamsNew = Join-Path $profile.ProfilePath $Script:CommunicationPaths.TeamsNew
            $teamsClassic = Join-Path $profile.ProfilePath $Script:CommunicationPaths.TeamsClassic

            if (Test-Path $teamsNew) {
                $destDir = Join-Path $phasePath "Teams\$($profile.Username)\NewTeams"
                $dbFiles = Get-ChildItem $teamsNew -Include "*.db","*.json","*.log" -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.Length -lt 100MB }
                foreach ($file in $dbFiles) {
                    $relPath = $file.FullName.Substring($teamsNew.Length).TrimStart("\")
                    $success = Copy-ForensicFile -SourcePath $file.FullName -DestPath (Join-Path $destDir $relPath) -Phase "FASE_7"
                    if ($success) { $collected++ }
                }
            }

            if (Test-Path $teamsClassic) {
                $destDir = Join-Path $phasePath "Teams\$($profile.Username)\ClassicTeams"
                $keyFiles = @("storage.json", "Cookies", "Local Storage", "IndexedDB")
                foreach ($keyFile in $keyFiles) {
                    $srcPath = Join-Path $teamsClassic $keyFile
                    if (Test-Path $srcPath) {
                        if ((Get-Item $srcPath).PSIsContainer) {
                            $c = Copy-ForensicDirectory -SourceDir $srcPath -DestDir (Join-Path $destDir $keyFile) -Phase "FASE_7" -Recurse
                            $collected += $c
                        } else {
                            $success = Copy-ForensicFile -SourcePath $srcPath -DestPath (Join-Path $destDir $keyFile) -Phase "FASE_7"
                            if ($success) { $collected++ }
                        }
                    }
                }
            }
        }
    } catch {
        Write-ForensicLog -Phase "FASE_7" -Action "TEAMS_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 7.3 Skype ---
    Write-StepHeader "7.3 Skype"
    try {
        foreach ($profile in $profiles) {
            $skypePath = Join-Path $profile.ProfilePath $Script:CommunicationPaths.Skype
            if (Test-Path $skypePath) {
                $destDir = Join-Path $phasePath "Skype\$($profile.Username)"
                $dbFiles = Get-ChildItem $skypePath -Include "*.db","*.sqlite","*.json" -Recurse -ErrorAction SilentlyContinue
                foreach ($file in $dbFiles) {
                    $success = Copy-ForensicFile -SourcePath $file.FullName -DestPath (Join-Path $destDir $file.Name) -Phase "FASE_7"
                    if ($success) { $collected++ }
                }
            }
        }
    } catch {
        Write-ForensicLog -Phase "FASE_7" -Action "SKYPE_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    # --- 7.4 Discord ---
    Write-StepHeader "7.4 Discord"
    try {
        foreach ($profile in $profiles) {
            $discordPath = Join-Path $profile.ProfilePath $Script:CommunicationPaths.Discord
            if (Test-Path $discordPath) {
                $destDir = Join-Path $phasePath "Discord\$($profile.Username)"
                $ldbDir = Join-Path $discordPath "Local Storage\leveldb"
                if (Test-Path $ldbDir) {
                    $c = Copy-ForensicDirectory -SourceDir $ldbDir -DestDir (Join-Path $destDir "leveldb") -Phase "FASE_7"
                    $collected += $c
                }
                $cacheDir = Join-Path $discordPath "Cache\Cache_Data"
                if (Test-Path $cacheDir) {
                    Write-ForensicLog -Phase "FASE_7" -Action "DISCORD_CACHE_DETECTADO" -Target "$cacheDir (solo metadatos)" -Result "OK"
                }
            }
        }
    } catch {
        Write-ForensicLog -Phase "FASE_7" -Action "DISCORD_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    Write-PhaseComplete -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum] -ItemsCollected $collected
    return $collected
}

#endregion ===== Phase7-Communications.ps1 =====

#region ===== Phase8-CloudStorage.ps1 =====
#region ============================================================
# MaPeriT.SW - FASE 8: ALMACENAMIENTO EN LA NUBE
# ============================================================

function Invoke-Phase8-CloudStorageCollection {
    param([string]$OutputPath)

    $phasePath = Join-Path $OutputPath $Script:OutputDirs.CloudStorage
    $phaseNum = 8
    $collected = 0

    Write-PhaseHeader -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum]
    $profiles = Get-UserProfiles

    # --- 8.1 OneDrive ---
    Write-StepHeader "8.1 Microsoft OneDrive"
    try {
        foreach ($profile in $profiles) {
            $oneDriveLogs = Join-Path $profile.ProfilePath $Script:CloudPaths.OneDriveLogs
            $oneDriveSettings = Join-Path $profile.ProfilePath $Script:CloudPaths.OneDriveSettings

            if (Test-Path $oneDriveLogs) {
                $destDir = Join-Path $phasePath "OneDrive\$($profile.Username)\logs"
                $c = Copy-ForensicDirectory -SourceDir $oneDriveLogs -DestDir $destDir -Phase "FASE_8" -Filter "*.log" -Recurse
                $collected += $c
            }

            if (Test-Path $oneDriveSettings) {
                $destDir = Join-Path $phasePath "OneDrive\$($profile.Username)\settings"
                $c = Copy-ForensicDirectory -SourceDir $oneDriveSettings -DestDir $destDir -Phase "FASE_8" -Filter "*.ini" -Recurse
                $collected += $c
                $c2 = Copy-ForensicDirectory -SourceDir $oneDriveSettings -DestDir $destDir -Phase "FASE_8" -Filter "*.dat" -Recurse
                $collected += $c2
            }

            # Configuracion de OneDrive del registro DE ESE PERFIL (HKU\<SID>), no del
            # HKCU del perito que ejecuta la herramienta: antes se leia HKCU y se
            # atribuia la cuenta del perito a cada perfil del equipo. Solo esta
            # disponible para perfiles con sesion iniciada (los hives offline se
            # montan unicamente durante la Fase 4); el NTUSER.DAT completo de todos
            # los perfiles ya se conserva en 04_Registry\Hives\Users.
            $hkuSid = if ($profile.SID -and (Test-Path "Registry::HKU\$($profile.SID)" -ErrorAction SilentlyContinue)) { $profile.SID } else { $null }
            if ($hkuSid) {
                $oneDriveReg = Get-ItemProperty "Registry::HKU\$hkuSid\Software\Microsoft\OneDrive\Accounts\*" -ErrorAction SilentlyContinue
                if ($oneDriveReg) {
                    $odInfo = @($oneDriveReg | Select-Object UserEmail, UserFolder, ServiceEndpointUri, Business, LastSignInTime)
                    $odDir = Join-Path $phasePath "OneDrive\$($profile.Username)"
                    if (-not (Test-Path $odDir)) { New-Item -ItemType Directory -Path $odDir -Force | Out-Null }
                    Export-CollectionToJSON -Data $odInfo -OutputPath (Join-Path $odDir "OneDriveConfig.json") -Phase "FASE_8" -Description "Config OneDrive"
                    $collected++
                }
            }
        }
    } catch {
        Write-ForensicLog -Phase "FASE_8" -Action "ONEDRIVE_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 8.2 Google Drive ---
    Write-StepHeader "8.2 Google Drive"
    try {
        foreach ($profile in $profiles) {
            $gdrivePath = Join-Path $profile.ProfilePath $Script:CloudPaths.GoogleDrive
            if (Test-Path $gdrivePath) {
                $destDir = Join-Path $phasePath "GoogleDrive\$($profile.Username)"
                $metadataFiles = Get-ChildItem $gdrivePath -Include "*.db","*.sqlite","*.journal","metadata_sqlite_db*" -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.Length -lt 100MB }
                foreach ($file in $metadataFiles) {
                    $relPath = $file.FullName.Substring($gdrivePath.Length).TrimStart("\")
                    $success = Copy-ForensicFile -SourcePath $file.FullName -DestPath (Join-Path $destDir $relPath) -Phase "FASE_8" -UseVSSFirst
                    if ($success) { $collected++ }
                }

                $logFiles = Get-ChildItem $gdrivePath -Filter "*.log" -Recurse -ErrorAction SilentlyContinue
                foreach ($file in $logFiles) {
                    $relPath = $file.FullName.Substring($gdrivePath.Length).TrimStart("\")
                    $success = Copy-ForensicFile -SourcePath $file.FullName -DestPath (Join-Path $destDir $relPath) -Phase "FASE_8"
                    if ($success) { $collected++ }
                }
            }
        }
    } catch {
        Write-ForensicLog -Phase "FASE_8" -Action "GDRIVE_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 8.3 Dropbox ---
    Write-StepHeader "8.3 Dropbox"
    try {
        foreach ($profile in $profiles) {
            $dropboxLocal = Join-Path $profile.ProfilePath $Script:CloudPaths.Dropbox
            $dropboxRoaming = Join-Path $profile.ProfilePath $Script:CloudPaths.DropboxInfo

            foreach ($dbxPath in @($dropboxLocal, $dropboxRoaming)) {
                if (Test-Path $dbxPath) {
                    $destDir = Join-Path $phasePath "Dropbox\$($profile.Username)"
                    $dbFiles = Get-ChildItem $dbxPath -Include "*.db","*.dbx","*.json","*.log" -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.Length -lt 50MB }
                    foreach ($file in $dbFiles) {
                        $relPath = $file.FullName.Substring($dbxPath.Length).TrimStart("\")
                        $success = Copy-ForensicFile -SourcePath $file.FullName -DestPath (Join-Path $destDir $relPath) -Phase "FASE_8"
                        if ($success) { $collected++ }
                    }
                }
            }

            $dropboxInfoJson = Join-Path $profile.ProfilePath "AppData\Roaming\Dropbox\info.json"
            if (Test-Path $dropboxInfoJson) {
                $success = Copy-ForensicFile -SourcePath $dropboxInfoJson -DestPath (Join-Path $phasePath "Dropbox\$($profile.Username)\info.json") -Phase "FASE_8"
                if ($success) { $collected++ }
            }
        }
    } catch {
        Write-ForensicLog -Phase "FASE_8" -Action "DROPBOX_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 8.4 Resumen ---
    Write-StepHeader "8.4 Resumen de Almacenamiento en la Nube"
    try {
        $cloudSummary = @()
        foreach ($service in @("OneDrive","GoogleDrive","Dropbox")) {
            $serviceDir = Join-Path $phasePath $service
            if (Test-Path $serviceDir) {
                $files = Get-ChildItem $serviceDir -Recurse -File -ErrorAction SilentlyContinue
                $cloudSummary += [PSCustomObject]@{
                    Servicio    = $service
                    Ficheros    = $files.Count
                    TamanoTotalMB = [math]::Round(($files | Measure-Object -Property Length -Sum).Sum / 1MB, 2)
                    Detectado   = "Si"
                }
            } else {
                $cloudSummary += [PSCustomObject]@{
                    Servicio    = $service
                    Ficheros    = 0
                    TamanoTotalMB = 0
                    Detectado   = "No"
                }
            }
        }
        Export-CollectionToCSV -Data $cloudSummary -OutputPath (Join-Path $phasePath "CloudStorageSummary.csv") -Phase "FASE_8" -Description "Resumen cloud"
        $collected++
    } catch {
        Write-ForensicLog -Phase "FASE_8" -Action "CLOUD_SUMMARY_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    Write-PhaseComplete -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum] -ItemsCollected $collected
    return $collected
}

#endregion ===== Phase8-CloudStorage.ps1 =====

#region ===== Phase9-USB.ps1 =====
#region ============================================================
# MaPeriT.SW - FASE 9: DISPOSITIVOS USB/EXTERNOS
# ============================================================

function Invoke-Phase9-USBCollection {
    param([string]$OutputPath)

    $phasePath = Join-Path $OutputPath $Script:OutputDirs.USB
    $phaseNum = 9
    $collected = 0

    Write-PhaseHeader -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum]

    # --- 9.1 USBSTOR del registro (consolidado) ---
    Write-StepHeader "9.1 Dispositivos USBSTOR"
    try {
        $usbDevices = @()
        if (Test-Path $Script:RegistryPaths.USBSTOR) {
            Get-ChildItem $Script:RegistryPaths.USBSTOR -ErrorAction SilentlyContinue | ForEach-Object {
                $deviceClass = $_.PSChildName
                $parts = $deviceClass -split '&'
                $vendor = ""
                $product = ""
                $revision = ""
                if ($parts.Count -ge 2) { $vendor = $parts[1] -replace 'Ven_', '' }
                if ($parts.Count -ge 3) { $product = $parts[2] -replace 'Prod_', '' }
                if ($parts.Count -ge 4) { $revision = $parts[3] -replace 'Rev_', '' }

                Get-ChildItem $_.PsPath -ErrorAction SilentlyContinue | ForEach-Object {
                    $props = Get-ItemProperty $_.PsPath -ErrorAction SilentlyContinue
                    $usbDevices += [PSCustomObject]@{
                        Clase           = $deviceClass
                        Fabricante      = $vendor
                        Producto        = $product
                        Revision        = $revision
                        NumeroSerie     = $_.PSChildName
                        NombreAmigable  = $props.FriendlyName
                        ContainerID     = $props.ContainerID
                        HardwareID      = ($props.HardwareID | Select-Object -First 1)
                    }
                }
            }
        }

        if (Test-Path $Script:RegistryPaths.USB) {
            Get-ChildItem $Script:RegistryPaths.USB -ErrorAction SilentlyContinue | ForEach-Object {
                $vidPid = $_.PSChildName
                if ($vidPid -match 'VID_(\w+)&PID_(\w+)') {
                    Get-ChildItem $_.PsPath -ErrorAction SilentlyContinue | ForEach-Object {
                        $props = Get-ItemProperty $_.PsPath -ErrorAction SilentlyContinue
                        $usbDevices += [PSCustomObject]@{
                            Clase           = "USB"
                            Fabricante      = "VID_$($Matches[1])"
                            Producto        = "PID_$($Matches[2])"
                            Revision        = ""
                            NumeroSerie     = $_.PSChildName
                            NombreAmigable  = $props.FriendlyName
                            ContainerID     = $props.ContainerID
                            HardwareID      = ($props.HardwareID | Select-Object -First 1)
                        }
                    }
                }
            }
        }

        if ($usbDevices.Count -gt 0) {
            Export-CollectionToCSV -Data $usbDevices -OutputPath (Join-Path $phasePath "USBDevices.csv") -Phase "FASE_9" -Description "Dispositivos USB"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_9" -Action "USBSTOR_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 9.2 SetupAPI logs ---
    Write-StepHeader "9.2 SetupAPI Logs"
    try {
        $setupApiLog = "C:\Windows\INF\setupapi.dev.log"
        if (Test-Path $setupApiLog) {
            $destPath = Join-Path $phasePath "setupapi.dev.log"
            if (Copy-ForensicFile -SourcePath $setupApiLog -DestPath $destPath -Phase "FASE_9") { $collected++ }

            # Lectura en streaming linea a linea en vez de Get-Content (que carga el
            # fichero COMPLETO en memoria de una vez): setupapi.dev.log puede acumular
            # cientos de MB en equipos con muchos años de uso, y solo necesitamos
            # inspeccionar linea a linea, nunca el contenido completo a la vez.
            $usbInstallEvents = @()
            $currentSection = ""
            $currentTimestamp = ""
            $fs = [System.IO.File]::Open($setupApiLog, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete)
            $streamReader = [System.IO.StreamReader]::new($fs)
            try {
                while (($line = $streamReader.ReadLine()) -ne $null) {
                    if ($line -match '>>>  \[Device Install') {
                        $currentSection = $line
                    }
                    elseif ($line -match '>>>  Section start (\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})') {
                        $currentTimestamp = $Matches[1]
                    }
                    elseif ($line -match 'USBSTOR|USB\\VID_' -and $currentSection) {
                        $usbInstallEvents += [PSCustomObject]@{
                            Timestamp  = $currentTimestamp
                            Seccion    = $currentSection.Trim('> []')
                            Dispositivo = $line.Trim()
                        }
                    }
                }
            } finally {
                $streamReader.Dispose()
                $fs.Dispose()
            }

            if ($usbInstallEvents.Count -gt 0) {
                Export-CollectionToCSV -Data $usbInstallEvents -OutputPath (Join-Path $phasePath "SetupAPIUSBEvents.csv") -Phase "FASE_9" -Description "USB install events"
                $collected++
            }
        }
    } catch {
        Write-ForensicLog -Phase "FASE_9" -Action "SETUPAPI_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 9.3 MountPoints2 ---
    Write-StepHeader "9.3 MountPoints2 por Usuario"
    try {
        $mountPoints = @()
        $profiles = Get-UserProfiles

        foreach ($profile in $profiles) {
            $sid = $profile.SID
            if (-not $sid) { continue }
            $effectiveSID = Get-EffectiveHKUSID -SID $sid -Username $profile.Username
            if (-not $effectiveSID) { continue }
            $mpPath = "Registry::HKU\$effectiveSID\$($Script:RegistryPaths.MountPoints2)"
            if (Test-Path $mpPath -ErrorAction SilentlyContinue) {
                Get-ChildItem $mpPath -ErrorAction SilentlyContinue | ForEach-Object {
                    $mountPoints += [PSCustomObject]@{
                        Usuario = $profile.Username
                        SID     = $sid
                        Volume  = $_.PSChildName
                    }
                }
            }
        }

        if ($mountPoints.Count -gt 0) {
            Export-CollectionToCSV -Data $mountPoints -OutputPath (Join-Path $phasePath "MountPoints2.csv") -Phase "FASE_9" -Description "MountPoints2"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_9" -Action "MOUNTPOINTS_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 9.4 Dispositivos de disco actuales ---
    Write-StepHeader "9.4 Discos Conectados Actualmente"
    try {
        $currentDisks = Get-CimInstance Win32_DiskDrive -ErrorAction SilentlyContinue | Select-Object `
            DeviceID, Model, InterfaceType, MediaType, SerialNumber, Size,
            @{N='TamanoGB';E={[math]::Round($_.Size/1GB, 2)}},
            Partitions, FirmwareRevision, PNPDeviceID

        $usbDisks = $currentDisks | Where-Object { $_.InterfaceType -eq 'USB' -or $_.PNPDeviceID -like '*USBSTOR*' }

        if ($currentDisks) {
            Export-CollectionToCSV -Data $currentDisks -OutputPath (Join-Path $phasePath "CurrentDisks.csv") -Phase "FASE_9" -Description "Discos actuales"
            $collected++
        }

        if ($usbDisks) {
            Export-CollectionToCSV -Data $usbDisks -OutputPath (Join-Path $phasePath "CurrentUSBDisks.csv") -Phase "FASE_9" -Description "Discos USB actuales"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_9" -Action "CURRENT_DISKS_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    # --- 9.5 Timeline de conexiones USB ---
    Write-StepHeader "9.5 Timeline de Conexiones USB"
    try {
        $usbTimeline = @()

        $usbEvents = Get-WinEvent -FilterHashtable @{
            LogName = 'Microsoft-Windows-DriverFrameworks-UserMode/Operational'
        } -MaxEvents 5000 -ErrorAction SilentlyContinue
        Test-EventQueryTruncated -Count @($usbEvents).Count -MaxEvents 5000 -Phase "FASE_9" -LogDescription "DriverFrameworks-UserMode Operational"

        if ($usbEvents) {
            foreach ($evt in $usbEvents) {
                if ($evt.Message -match 'USB|USBSTOR|mass storage') {
                    $usbTimeline += [PSCustomObject]@{
                        Timestamp = $evt.TimeCreated.ToString($Script:Defaults.DateFormat)
                        EventID   = $evt.Id
                        Origen    = "DriverFrameworks"
                        Mensaje   = ($evt.Message -split "`n" | Select-Object -First 1)
                    }
                }
            }
        }

        $pnpEvents = Get-WinEvent -FilterHashtable @{
            LogName = 'Microsoft-Windows-Kernel-PnP/Configuration'
        } -MaxEvents 5000 -ErrorAction SilentlyContinue
        Test-EventQueryTruncated -Count @($pnpEvents).Count -MaxEvents 5000 -Phase "FASE_9" -LogDescription "Kernel-PnP Configuration"

        if ($pnpEvents) {
            foreach ($evt in $pnpEvents) {
                if ($evt.Message -match 'USB|USBSTOR') {
                    $usbTimeline += [PSCustomObject]@{
                        Timestamp = $evt.TimeCreated.ToString($Script:Defaults.DateFormat)
                        EventID   = $evt.Id
                        Origen    = "Kernel-PnP"
                        Mensaje   = ($evt.Message -split "`n" | Select-Object -First 1)
                    }
                }
            }
        }

        if ($usbTimeline.Count -gt 0) {
            $usbTimeline = $usbTimeline | Sort-Object Timestamp
            Export-CollectionToCSV -Data $usbTimeline -OutputPath (Join-Path $phasePath "USBTimeline.csv") -Phase "FASE_9" -Description "Timeline USB"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_9" -Action "USB_TIMELINE_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    Write-PhaseComplete -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum] -ItemsCollected $collected
    return $collected
}

#endregion ===== Phase9-USB.ps1 =====

#region ===== Phase10-Persistence.ps1 =====
#region ============================================================
# MaPeriT.SW - FASE 10: MECANISMOS DE PERSISTENCIA
# ============================================================

function Invoke-Phase10-PersistenceCollection {
    param([string]$OutputPath)

    $phasePath = Join-Path $OutputPath $Script:OutputDirs.Persistence
    $phaseNum = 10
    $collected = 0

    Write-PhaseHeader -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum]

    # --- 10.1 Resumen consolidado de autoruns ---
    Write-StepHeader "10.1 Resumen Consolidado de Autoruns"
    try {
        $autoruns = @()

        $runKeys = @(
            "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
            "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce",
            "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunServices",
            "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunServicesOnce",
            "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run",
            "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\RunOnce",
            "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer\Run",
            "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer\Run",
            "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run",
            "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\RunOnce"
        )

        foreach ($key in $runKeys) {
            if (Test-Path $key) {
                $props = Get-ItemProperty $key -ErrorAction SilentlyContinue
                $props.PSObject.Properties | Where-Object { $_.Name -notlike "PS*" -and $_.Name -ne "(default)" } | ForEach-Object {
                    $autoruns += [PSCustomObject]@{
                        Tipo    = "Registry Run"
                        Clave   = $key
                        Nombre  = $_.Name
                        Valor   = $_.Value
                        Riesgo  = "Media"
                    }
                }
            }
        }

        $winlogon = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" -ErrorAction SilentlyContinue
        if ($winlogon) {
            if ($winlogon.Shell -and $winlogon.Shell -ne "explorer.exe") {
                $autoruns += [PSCustomObject]@{ Tipo="Winlogon Shell"; Clave="Winlogon"; Nombre="Shell"; Valor=$winlogon.Shell; Riesgo="Alta" }
            }
            if ($winlogon.Userinit -and $winlogon.Userinit -ne "C:\Windows\system32\userinit.exe,") {
                $autoruns += [PSCustomObject]@{ Tipo="Winlogon Userinit"; Clave="Winlogon"; Nombre="Userinit"; Valor=$winlogon.Userinit; Riesgo="Alta" }
            }
        }

        $startupFolders = @(
            "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\StartUp",
            "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\StartUp"
        )
        foreach ($folder in $startupFolders) {
            if (Test-Path $folder) {
                Get-ChildItem $folder -ErrorAction SilentlyContinue | ForEach-Object {
                    $autoruns += [PSCustomObject]@{
                        Tipo    = "Startup Folder"
                        Clave   = $folder
                        Nombre  = $_.Name
                        Valor   = $_.FullName
                        Riesgo  = "Media"
                    }
                }
            }
        }

        $suspiciousTasks = Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {
            $_.Principal.UserId -ne 'SYSTEM' -and
            $_.Principal.UserId -ne 'LOCAL SERVICE' -and
            $_.Principal.UserId -ne 'NETWORK SERVICE' -and
            $_.State -ne 'Disabled'
        }
        foreach ($task in $suspiciousTasks) {
            foreach ($action in $task.Actions) {
                if ($action.Execute) {
                    $autoruns += [PSCustomObject]@{
                        Tipo    = "Scheduled Task"
                        Clave   = $task.TaskPath
                        Nombre  = $task.TaskName
                        Valor   = "$($action.Execute) $($action.Arguments)"
                        Riesgo  = if($action.Execute -match 'Users|Temp|AppData|Downloads'){"Alta"}else{"Baja"}
                    }
                }
            }
        }

        if ($autoruns.Count -gt 0) {
            Export-CollectionToCSV -Data $autoruns -OutputPath (Join-Path $phasePath "AutorunsConsolidated.csv") -Phase "FASE_10" -Description "Autoruns consolidados"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_10" -Action "AUTORUNS_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 10.2 WMI Event Subscriptions ---
    Write-StepHeader "10.2 Suscripciones WMI (Persistencia)"
    try {
        $wmiPersistence = @()

        $filters = Get-CimInstance -Namespace root/subscription -ClassName __EventFilter -ErrorAction SilentlyContinue
        $consumers = Get-CimInstance -Namespace root/subscription -ClassName __EventConsumer -ErrorAction SilentlyContinue
        $bindings = Get-CimInstance -Namespace root/subscription -ClassName __FilterToConsumerBinding -ErrorAction SilentlyContinue

        if ($filters) {
            foreach ($filter in $filters) {
                $wmiPersistence += [PSCustomObject]@{
                    Tipo     = "EventFilter"
                    Nombre   = $filter.Name
                    Detalle  = $filter.QueryLanguage + ": " + $filter.Query
                    Creador  = $filter.CreatorSID
                }
            }
        }

        if ($consumers) {
            foreach ($consumer in $consumers) {
                $detail = ""
                if ($consumer.CommandLineTemplate) { $detail = "CMD: " + $consumer.CommandLineTemplate }
                elseif ($consumer.ScriptText) { $detail = "Script: " + $consumer.ScriptText.Substring(0, [Math]::Min(200, $consumer.ScriptText.Length)) }
                elseif ($consumer.ScriptFileName) { $detail = "ScriptFile: " + $consumer.ScriptFileName }

                $wmiPersistence += [PSCustomObject]@{
                    Tipo     = $consumer.CimClass.CimClassName
                    Nombre   = $consumer.Name
                    Detalle  = $detail
                    Creador  = ""
                }
            }
        }

        if ($bindings) {
            foreach ($binding in $bindings) {
                $wmiPersistence += [PSCustomObject]@{
                    Tipo     = "FilterToConsumerBinding"
                    Nombre   = "Binding"
                    Detalle  = "Filter: $($binding.Filter) -> Consumer: $($binding.Consumer)"
                    Creador  = $binding.CreatorSID
                }
            }
        }

        if ($wmiPersistence.Count -gt 0) {
            Export-CollectionToCSV -Data $wmiPersistence -OutputPath (Join-Path $phasePath "WMIPersistence.csv") -Phase "FASE_10" -Description "Persistencia WMI"
            $collected++
            Write-ForensicLog -Phase "FASE_10" -Action "WMI_PERSISTENCE_DETECTADA" -Target "$($wmiPersistence.Count) elementos" -Result "WARNING"
        } else {
            Write-ForensicLog -Phase "FASE_10" -Action "WMI_SIN_SUSCRIPCIONES" -Target "Limpio" -Result "OK"
        }
    } catch {
        Write-ForensicLog -Phase "FASE_10" -Action "WMI_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 10.3 Alternate Data Streams ---
    Write-StepHeader "10.3 Alternate Data Streams (ADS)"
    try {
        $adsResults = @()
        $scanDirs = @(
            "C:\Windows\System32",
            "C:\Windows\SysWOW64",
            "$env:SystemDrive\Users",
            "$env:TEMP"
        )

        $adsTimeout = $Script:Defaults.ADSScanTimeoutSec
        $sw = [System.Diagnostics.Stopwatch]::StartNew()

        foreach ($dir in $scanDirs) {
            if ($sw.Elapsed.TotalSeconds -gt $adsTimeout) {
                Write-ForensicLog -Phase "FASE_10" -Action "ADS_TIMEOUT" -Target "Limite de tiempo alcanzado ($adsTimeout s)" -Result "WARNING"
                break
            }

            if (-not (Test-Path $dir)) { continue }

            try {
                Get-ChildItem $dir -File -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
                    if ($sw.Elapsed.TotalSeconds -gt $adsTimeout) { return }
                    try {
                        $streams = Get-Item $_.FullName -Stream * -ErrorAction SilentlyContinue | Where-Object { $_.Stream -ne ':$DATA' -and $_.Stream -ne 'Zone.Identifier' }
                        foreach ($stream in $streams) {
                            $adsResults += [PSCustomObject]@{
                                Fichero = $_.FullName
                                Stream  = $stream.Stream
                                Tamano  = $stream.Length
                            }
                        }
                    } catch { }
                }
            } catch { }
        }

        if ($adsResults.Count -gt 0) {
            Export-CollectionToCSV -Data $adsResults -OutputPath (Join-Path $phasePath "AlternateDataStreams.csv") -Phase "FASE_10" -Description "ADS detectados"
            $collected++
        } else {
            Write-ForensicLog -Phase "FASE_10" -Action "ADS_LIMPIO" -Target "No se encontraron ADS sospechosos" -Result "OK"
        }
    } catch {
        Write-ForensicLog -Phase "FASE_10" -Action "ADS_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 10.4 Servicios con ejecutables sin firma ---
    Write-StepHeader "10.4 Servicios con Ejecutables Sin Firma"
    try {
        $unsignedServices = @()
        $services = Get-CimInstance Win32_Service -ErrorAction SilentlyContinue | Where-Object { $_.PathName -and $_.State -eq 'Running' }

        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        foreach ($svc in $services) {
            if ($sw.Elapsed.TotalSeconds -gt $Script:Defaults.SignatureScanTimeout) {
                Write-ForensicLog -Phase "FASE_10" -Action "SIGNATURE_TIMEOUT" -Target "Limite de tiempo para verificacion de firmas" -Result "WARNING"
                break
            }

            $exePath = Resolve-ServiceExecutablePath -PathName $svc.PathName
            if ($exePath -and (Test-Path $exePath -ErrorAction SilentlyContinue)) {
                try {
                    $sig = Get-AuthenticodeSignature $exePath -ErrorAction SilentlyContinue
                    if ($sig -and $sig.Status -ne 'Valid') {
                        $unsignedServices += [PSCustomObject]@{
                            Servicio = $svc.Name
                            NombreMostrado = $svc.DisplayName
                            Ejecutable = $exePath
                            EstadoFirma = $sig.Status.ToString()
                            CuentaServicio = $svc.StartName
                            Estado = $svc.State
                        }
                    }
                } catch { }
            } else {
                # Un servicio EN EJECUCION cuyo PathName no se puede resolver a un fichero
                # real (p.ej. ruta sin comillas con espacios que no encaja en ningun
                # candidato existente) es en si mismo un dato de interes forense y no debe
                # descartarse en silencio: antes simplemente se saltaba este servicio.
                $unsignedServices += [PSCustomObject]@{
                    Servicio = $svc.Name
                    NombreMostrado = $svc.DisplayName
                    Ejecutable = $svc.PathName
                    EstadoFirma = "RutaNoResuelta"
                    CuentaServicio = $svc.StartName
                    Estado = $svc.State
                }
            }
        }

        if ($unsignedServices.Count -gt 0) {
            Export-CollectionToCSV -Data $unsignedServices -OutputPath (Join-Path $phasePath "UnsignedServices.csv") -Phase "FASE_10" -Description "Servicios sin firma"
            $collected++
            Write-ForensicLog -Phase "FASE_10" -Action "SERVICIOS_SIN_FIRMA" -Target "$($unsignedServices.Count) servicios" -Result "WARNING"
        }
    } catch {
        Write-ForensicLog -Phase "FASE_10" -Action "UNSIGNED_SERVICES_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 10.5 Procesos desde ubicaciones inusuales ---
    Write-StepHeader "10.5 Procesos desde Ubicaciones Inusuales"
    try {
        $suspiciousProcesses = @()
        $processes = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.ExecutablePath }

        $suspiciousPaths = @('\\Users\\', '\\Temp\\', '\\tmp\\', '\\Downloads\\', '\\AppData\\Local\\Temp\\', '\\ProgramData\\', '\\Public\\')

        foreach ($proc in $processes) {
            $isSuspicious = $false
            foreach ($pattern in $suspiciousPaths) {
                if ($proc.ExecutablePath -match [regex]::Escape($pattern)) {
                    $isSuspicious = $true
                    break
                }
            }

            if ($isSuspicious) {
                $owner = "N/A"
                try {
                    $o = Invoke-CimMethod -InputObject $proc -MethodName GetOwner -ErrorAction SilentlyContinue
                    if ($o.User) { $owner = "$($o.Domain)\$($o.User)" }
                } catch { }

                $suspiciousProcesses += [PSCustomObject]@{
                    PID         = $proc.ProcessId
                    Nombre      = $proc.Name
                    Ejecutable  = $proc.ExecutablePath
                    LineaComandos = $proc.CommandLine
                    Propietario = $owner
                    ParentPID   = $proc.ParentProcessId
                    FechaCreacion = $proc.CreationDate
                }
            }
        }

        if ($suspiciousProcesses.Count -gt 0) {
            Export-CollectionToCSV -Data $suspiciousProcesses -OutputPath (Join-Path $phasePath "SuspiciousProcesses.csv") -Phase "FASE_10" -Description "Procesos sospechosos"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_10" -Action "SUSPICIOUS_PROC_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 10.6 Nuevos servicios instalados (ultimos 30 dias) ---
    Write-StepHeader "10.6 Servicios Instalados Recientemente"
    try {
        $recentServices = @()
        $thirtyDaysAgo = (Get-Date).AddDays(-30)
        $events = Get-WinEvent -FilterHashtable @{LogName='System'; Id=7045; StartTime=$thirtyDaysAgo} -ErrorAction SilentlyContinue
        foreach ($evt in $events) {
            $xml = [xml]$evt.ToXml()
            $data = $xml.Event.EventData.Data
            $recentServices += [PSCustomObject]@{
                Timestamp   = $evt.TimeCreated.ToString($Script:Defaults.DateFormat)
                NombreServicio = ($data | Where-Object { $_.Name -eq 'ServiceName' }).'#text'
                ImagePath   = ($data | Where-Object { $_.Name -eq 'ImagePath' }).'#text'
                TipoServicio= ($data | Where-Object { $_.Name -eq 'ServiceType' }).'#text'
                TipoInicio  = ($data | Where-Object { $_.Name -eq 'StartType' }).'#text'
                CuentaServicio = ($data | Where-Object { $_.Name -eq 'AccountName' }).'#text'
            }
        }
        if ($recentServices.Count -gt 0) {
            Export-CollectionToCSV -Data $recentServices -OutputPath (Join-Path $phasePath "RecentServiceInstalls.csv") -Phase "FASE_10" -Description "Servicios recientes"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_10" -Action "RECENT_SERVICES_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    # --- 10.7 Certificados raiz sospechosos ---
    Write-StepHeader "10.7 Certificados Raiz Sospechosos"
    try {
        $knownIssuers = @(
            'CN=Microsoft Root Certificate Authority*',
            'CN=Microsoft Root Authority*',
            'CN=Microsoft ECC Root*',
            'CN=DigiCert*', 'CN=GlobalSign*', 'CN=VeriSign*', 'CN=GeoTrust*',
            'CN=Comodo*', 'CN=COMODO*', 'CN=Entrust*', 'CN=Go Daddy*',
            'CN=GoDaddy*', 'CN=Thawte*', 'CN=Symantec*', 'CN=Baltimore*',
            'CN=Starfield*', 'CN=AddTrust*', 'CN=USERTrust*', 'CN=AAA Certificate*',
            'CN=D-TRUST*', 'CN=T-TeleSec*', 'CN=QuoVadis*', 'CN=IdenTrust*',
            'CN=Certum*', 'CN=Buypass*', 'CN=ISRG Root*', 'CN=Amazon Root*',
            'CN=Actalis*', 'CN=Hongkong Post*', 'CN=SecureTrust*', 'CN=XRamp*',
            'CN=AffirmTrust*', 'CN=Certigna*', 'CN=emSign*', 'CN=E-Tugra*',
            'CN=OISTE*', 'CN=SwissSign*', 'CN=Trustwave*', 'CN=TWCA*',
            'CN=SSL.com*', 'CN=Telia*', 'CN=Autoridad de Certificacion*'
        )
        $suspectCerts = @()
        Get-ChildItem Cert:\LocalMachine\Root -ErrorAction SilentlyContinue | ForEach-Object {
            $isKnown = $false
            foreach ($pattern in $knownIssuers) {
                if ($_.Issuer -like $pattern -or $_.Subject -like $pattern) { $isKnown = $true; break }
            }
            if (-not $isKnown) {
                $suspectCerts += [PSCustomObject]@{
                    Thumbprint  = $_.Thumbprint
                    Subject     = $_.Subject
                    Issuer      = $_.Issuer
                    NotBefore   = $_.NotBefore.ToString($Script:Defaults.DateFormat)
                    NotAfter    = $_.NotAfter.ToString($Script:Defaults.DateFormat)
                    SerialNumber = $_.SerialNumber
                }
            }
        }
        if ($suspectCerts.Count -gt 0) {
            Export-CollectionToCSV -Data $suspectCerts -OutputPath (Join-Path $phasePath "SuspiciousRootCerts.csv") -Phase "FASE_10" -Description "Certificados raiz sospechosos"
            $collected++
            Write-ForensicLog -Phase "FASE_10" -Action "CERTS_SOSPECHOSOS" -Target "$($suspectCerts.Count) certificados no estandar" -Result "WARNING"
        } else {
            Write-ForensicLog -Phase "FASE_10" -Action "CERTS_LIMPIOS" -Target "Solo certificados conocidos" -Result "OK"
        }
    } catch {
        Write-ForensicLog -Phase "FASE_10" -Action "CERTS_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    # --- 10.8 COM Hijacking ---
    Write-StepHeader "10.8 COM Hijacking"
    try {
        $comHijacks = @()
        $profiles = Get-UserProfiles
        foreach ($profile in $profiles) {
            $sid = $profile.SID
            if (-not $sid) { continue }
            $effectiveSID = Get-EffectiveHKUSID -SID $sid -Username $profile.Username
            if (-not $effectiveSID) { continue }
            $clsidPath = "Registry::HKU\$effectiveSID\Software\Classes\CLSID"
            if (Test-Path $clsidPath -ErrorAction SilentlyContinue) {
                Get-ChildItem $clsidPath -ErrorAction SilentlyContinue | ForEach-Object {
                    $clsid = $_.PSChildName
                    foreach ($subKey in @("InprocServer32", "LocalServer32")) {
                        $serverPath = Join-Path $_.PsPath $subKey
                        if (Test-Path $serverPath -ErrorAction SilentlyContinue) {
                            $val = (Get-ItemProperty $serverPath -ErrorAction SilentlyContinue).'(default)'
                            if ($val) {
                                $comHijacks += [PSCustomObject]@{
                                    Usuario   = $profile.Username
                                    CLSID     = $clsid
                                    Tipo      = $subKey
                                    Valor     = $val
                                    Riesgo    = if ($val -match 'Users|Temp|AppData|Downloads') { "Alta" } else { "Media" }
                                }
                            }
                        }
                    }
                }
            }
        }
        if ($comHijacks.Count -gt 0) {
            Export-CollectionToCSV -Data $comHijacks -OutputPath (Join-Path $phasePath "COMHijacking.csv") -Phase "FASE_10" -Description "COM Hijacking"
            $collected++
            Write-ForensicLog -Phase "FASE_10" -Action "COM_HIJACK_DETECTADO" -Target "$($comHijacks.Count) entradas CLSID en HKCU" -Result "WARNING"
        } else {
            Write-ForensicLog -Phase "FASE_10" -Action "COM_HIJACK_LIMPIO" -Target "Sin COM hijacking en HKCU" -Result "OK"
        }
    } catch {
        Write-ForensicLog -Phase "FASE_10" -Action "COM_HIJACK_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    Write-PhaseComplete -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum] -ItemsCollected $collected
    return $collected
}

#endregion ===== Phase10-Persistence.ps1 =====

#region ===== Timeline.ps1 =====
#region ============================================================
# MaPeriT.SW - LINEA TEMPORAL UNIFICADA
# Correlacion de eventos de todas las fuentes
# ============================================================

function Build-ForensicTimeline {
    param([string]$OutputPath)

    $analysisPath = Join-Path $OutputPath $Script:OutputDirs.Analysis
    $timeline = [System.Collections.ArrayList]::new()

    Write-StepHeader "Construyendo Linea Temporal Unificada"
    $sw = [System.Diagnostics.Stopwatch]::StartNew()

    # --- Eventos de logon/logoff ---
    try {
        $logonFile = Join-Path $OutputPath "$($Script:OutputDirs.EventLogs)\Analysis\SecurityLogon.csv"
        if (Test-Path $logonFile) {
            Import-Csv $logonFile -ErrorAction SilentlyContinue | ForEach-Object {
                [void]$timeline.Add([PSCustomObject]@{
                    Timestamp   = $_.Timestamp
                    Origen      = "EventLog-Security"
                    Categoria   = "Sesion"
                    Evento      = "Logon $($_.TipoLogonDesc)"
                    Detalle     = "Usuario: $($_.UsuarioDestino)\$($_.DominioDestino) desde $($_.IPOrigen)"
                    Usuario     = $_.UsuarioDestino
                    Severidad   = "Info"
                })
            }
        }
    } catch { }

    # --- Logons fallidos ---
    try {
        $failedFile = Join-Path $OutputPath "$($Script:OutputDirs.EventLogs)\Analysis\SecurityFailedLogon.csv"
        if (Test-Path $failedFile) {
            Import-Csv $failedFile -ErrorAction SilentlyContinue | ForEach-Object {
                [void]$timeline.Add([PSCustomObject]@{
                    Timestamp   = $_.Timestamp
                    Origen      = "EventLog-Security"
                    Categoria   = "Sesion"
                    Evento      = "Logon FALLIDO"
                    Detalle     = "Usuario: $($_.UsuarioDestino) desde $($_.IPOrigen) - $($_.RazonFallo)"
                    Usuario     = $_.UsuarioDestino
                    Severidad   = "Warning"
                })
            }
        }
    } catch { }

    # --- Arranques/Apagados ---
    try {
        $shutdownFile = Join-Path $OutputPath "$($Script:OutputDirs.EventLogs)\Analysis\SystemShutdowns.csv"
        if (Test-Path $shutdownFile) {
            Import-Csv $shutdownFile -ErrorAction SilentlyContinue | ForEach-Object {
                [void]$timeline.Add([PSCustomObject]@{
                    Timestamp   = $_.Timestamp
                    Origen      = "EventLog-System"
                    Categoria   = "Sistema"
                    Evento      = $_.Tipo
                    Detalle     = $_.Mensaje
                    Usuario     = "SYSTEM"
                    Severidad   = if($_.EventID -eq '6008'){"Warning"}else{"Info"}
                })
            }
        }
    } catch { }

    # --- Servicios instalados ---
    try {
        $svcFile = Join-Path $OutputPath "$($Script:OutputDirs.EventLogs)\Analysis\SystemServices.csv"
        if (Test-Path $svcFile) {
            Import-Csv $svcFile -ErrorAction SilentlyContinue | Where-Object { $_.EventID -eq '7045' } | ForEach-Object {
                [void]$timeline.Add([PSCustomObject]@{
                    Timestamp   = $_.Timestamp
                    Origen      = "EventLog-System"
                    Categoria   = "Servicio"
                    Evento      = "Nuevo servicio instalado"
                    Detalle     = $_.Mensaje
                    Usuario     = "SYSTEM"
                    Severidad   = "Warning"
                })
            }
        }
    } catch { }

    # --- PowerShell scripts ---
    try {
        $psFile = Join-Path $OutputPath "$($Script:OutputDirs.EventLogs)\Analysis\PowerShellScripts.csv"
        if (Test-Path $psFile) {
            Import-Csv $psFile -ErrorAction SilentlyContinue | ForEach-Object {
                $scriptPreview = if ($_.ScriptBlock.Length -gt 100) { $_.ScriptBlock.Substring(0, 100) + "..." } else { $_.ScriptBlock }
                [void]$timeline.Add([PSCustomObject]@{
                    Timestamp   = $_.Timestamp
                    Origen      = "EventLog-PowerShell"
                    Categoria   = "Ejecucion"
                    Evento      = "Script PowerShell ejecutado"
                    Detalle     = "Path: $($_.ScriptPath) | $scriptPreview"
                    Usuario     = ""
                    Severidad   = "Info"
                })
            }
        }
    } catch { }

    # --- RDP ---
    try {
        $rdpFile = Join-Path $OutputPath "$($Script:OutputDirs.EventLogs)\Analysis\RDPSessions.csv"
        if (Test-Path $rdpFile) {
            Import-Csv $rdpFile -ErrorAction SilentlyContinue | ForEach-Object {
                [void]$timeline.Add([PSCustomObject]@{
                    Timestamp   = $_.Timestamp
                    Origen      = "EventLog-RDP"
                    Categoria   = "Sesion"
                    Evento      = $_.Accion
                    Detalle     = $_.Mensaje
                    Usuario     = ""
                    Severidad   = "Info"
                })
            }
        }
    } catch { }

    # --- Tareas programadas ---
    try {
        $taskFile = Join-Path $OutputPath "$($Script:OutputDirs.EventLogs)\Analysis\TaskScheduler.csv"
        if (Test-Path $taskFile) {
            Import-Csv $taskFile -ErrorAction SilentlyContinue | Where-Object { $_.EventID -in @('106','141') } | ForEach-Object {
                [void]$timeline.Add([PSCustomObject]@{
                    Timestamp   = $_.Timestamp
                    Origen      = "EventLog-TaskScheduler"
                    Categoria   = "Tarea"
                    Evento      = $_.Accion
                    Detalle     = $_.Mensaje
                    Usuario     = ""
                    Severidad   = if($_.EventID -eq '106'){"Warning"}else{"Info"}
                })
            }
        }
    } catch { }

    # --- USB ---
    try {
        $usbFile = Join-Path $OutputPath "$($Script:OutputDirs.USB)\USBTimeline.csv"
        if (Test-Path $usbFile) {
            Import-Csv $usbFile -ErrorAction SilentlyContinue | ForEach-Object {
                [void]$timeline.Add([PSCustomObject]@{
                    Timestamp   = $_.Timestamp
                    Origen      = "USB-$($_.Origen)"
                    Categoria   = "Dispositivo"
                    Evento      = "Actividad USB"
                    Detalle     = $_.Mensaje
                    Usuario     = ""
                    Severidad   = "Info"
                })
            }
        }
    } catch { }

    # --- BAM/DAM (ejecucion de programas) ---
    try {
        $bamFile = Join-Path $OutputPath "$($Script:OutputDirs.Registry)\ExtractedValues\BAM_DAM.csv"
        if (Test-Path $bamFile) {
            Import-Csv $bamFile -ErrorAction SilentlyContinue | Where-Object { $_.FechaEjecucion } | ForEach-Object {
                [void]$timeline.Add([PSCustomObject]@{
                    Timestamp   = $_.FechaEjecucion
                    Origen      = "Registry-$($_.Origen)"
                    Categoria   = "Ejecucion"
                    Evento      = "Programa ejecutado (BAM/DAM)"
                    Detalle     = $_.Ejecutable
                    Usuario     = $_.SID
                    Severidad   = "Info"
                })
            }
        }
    } catch { }

    # --- UserAssist ---
    try {
        $uaFile = Join-Path $OutputPath "$($Script:OutputDirs.Registry)\ExtractedValues\UserAssist.csv"
        if (Test-Path $uaFile) {
            Import-Csv $uaFile -ErrorAction SilentlyContinue | Where-Object { $_.UltimaEjecucion } | ForEach-Object {
                [void]$timeline.Add([PSCustomObject]@{
                    Timestamp   = $_.UltimaEjecucion
                    Origen      = "Registry-UserAssist"
                    Categoria   = "Ejecucion"
                    Evento      = "Programa ejecutado ($($_.Ejecuciones)x)"
                    Detalle     = $_.Programa
                    Usuario     = $_.Usuario
                    Severidad   = "Info"
                })
            }
        }
    } catch { }

    # --- Prefetch ---
    try {
        $pfFile = Join-Path $OutputPath "$($Script:OutputDirs.Filesystem)\PrefetchSummary.csv"
        if (Test-Path $pfFile) {
            Import-Csv $pfFile -ErrorAction SilentlyContinue | ForEach-Object {
                [void]$timeline.Add([PSCustomObject]@{
                    Timestamp   = $_.Modificado
                    Origen      = "Prefetch"
                    Categoria   = "Ejecucion"
                    Evento      = "Programa en Prefetch"
                    Detalle     = $_.Nombre
                    Usuario     = ""
                    Severidad   = "Info"
                })
            }
        }
    } catch { }

    # --- Papelera de reciclaje ---
    try {
        $rbFile = Join-Path $OutputPath "$($Script:OutputDirs.Filesystem)\RecycleBinSummary.csv"
        if (Test-Path $rbFile) {
            Import-Csv $rbFile -ErrorAction SilentlyContinue | Where-Object { $_.FechaEliminacion -ne "N/A" } | ForEach-Object {
                [void]$timeline.Add([PSCustomObject]@{
                    Timestamp   = $_.FechaEliminacion
                    Origen      = "RecycleBin"
                    Categoria   = "Fichero"
                    Evento      = "Fichero eliminado"
                    Detalle     = "$($_.NombreOriginal) ($($_.TamanoOriginal) bytes)"
                    Usuario     = $_.SID
                    Severidad   = "Info"
                })
            }
        }
    } catch { }

    # --- Defender ---
    try {
        $defFile = Join-Path $OutputPath "$($Script:OutputDirs.EventLogs)\Analysis\DefenderEvents.csv"
        if (Test-Path $defFile) {
            Import-Csv $defFile -ErrorAction SilentlyContinue | Where-Object { $_.Level -in @('Warning','Error','Critical') } | ForEach-Object {
                [void]$timeline.Add([PSCustomObject]@{
                    Timestamp   = $_.Timestamp
                    Origen      = "Defender"
                    Categoria   = "Seguridad"
                    Evento      = "Evento Defender ($($_.Level))"
                    Detalle     = $_.Mensaje
                    Usuario     = "SYSTEM"
                    Severidad   = "Warning"
                })
            }
        }
    } catch { }

    # --- Ordenar y exportar ---
    $sortedTimeline = @($timeline | Sort-Object { try { [DateTime]::Parse($_.Timestamp) } catch { [DateTime]::MinValue } })

    if ($sortedTimeline.Count -gt 0) {
        Export-CollectionToCSV -Data $sortedTimeline -OutputPath (Join-Path $analysisPath "Timeline.csv") -Phase "ANALISIS" -Description "Timeline unificada"

        $recentDays = 7
        $cutoff = (Get-Date).AddDays(-$recentDays).ToString($Script:Defaults.DateFormat)
        $recentTimeline = @($sortedTimeline | Where-Object {
            try { [DateTime]::Parse($_.Timestamp) -ge [DateTime]::Parse($cutoff) } catch { $false }
        })
        if ($recentTimeline.Count -gt 0) {
            Export-CollectionToCSV -Data $recentTimeline -OutputPath (Join-Path $analysisPath "Timeline_Last${recentDays}Days.csv") -Phase "ANALISIS" -Description "Timeline ultimos $recentDays dias"
        }
    }

    $sw.Stop()
    Write-ForensicLog -Phase "ANALISIS" -Action "TIMELINE_COMPLETADA" -Target "$($timeline.Count) eventos" -Result "OK" -DurationMs $sw.ElapsedMilliseconds

    return $timeline.Count
}

#endregion ===== Timeline.ps1 =====

#region ===== IOCExtractor.ps1 =====
#region ============================================================
# MaPeriT.SW - EXTRACCION DE IOCs (Indicators of Compromise)
# Consolida IPs, dominios y hashes de todas las fuentes
# ============================================================

function Export-ForensicIOCs {
    param([string]$OutputPath)

    $analysisPath = Join-Path $OutputPath $Script:OutputDirs.Analysis
    $iocs = [System.Collections.ArrayList]::new()

    Write-StepHeader "Extrayendo Indicadores de Compromiso (IOCs)"
    $sw = [System.Diagnostics.Stopwatch]::StartNew()

    # --- IPs remotas de conexiones TCP ---
    try {
        $tcpFile = Join-Path $OutputPath "$($Script:OutputDirs.Volatile)\NetworkConnections_TCP.csv"
        if (Test-Path $tcpFile) {
            Import-Csv $tcpFile -ErrorAction SilentlyContinue | Where-Object {
                $_.RemoteAddress -and $_.RemoteAddress -ne '0.0.0.0' -and $_.RemoteAddress -ne '::' -and
                $_.RemoteAddress -ne '127.0.0.1' -and $_.RemoteAddress -ne '::1' -and
                $_.RemoteAddress -notlike '192.168.*' -and $_.RemoteAddress -notlike '10.*' -and
                $_.RemoteAddress -notlike '172.1[6-9].*' -and $_.RemoteAddress -notlike '172.2[0-9].*' -and
                $_.RemoteAddress -notlike '172.3[0-1].*'
            } | ForEach-Object {
                [void]$iocs.Add([PSCustomObject]@{
                    Tipo    = "IP"
                    Valor   = $_.RemoteAddress
                    Fuente  = "TCP Connections"
                    Contexto = "Puerto: $($_.RemotePort), Proceso: $($_.ProcessName), Estado: $($_.State)"
                })
            }
        }
    } catch { }

    # --- Dominios de cache DNS ---
    try {
        $dnsFile = Join-Path $OutputPath "$($Script:OutputDirs.Volatile)\DnsCache.csv"
        if (Test-Path $dnsFile) {
            Import-Csv $dnsFile -ErrorAction SilentlyContinue | Where-Object { $_.Entry -and $_.Entry -ne '' } | ForEach-Object {
                [void]$iocs.Add([PSCustomObject]@{
                    Tipo    = "Dominio"
                    Valor   = $_.Entry
                    Fuente  = "DNS Cache"
                    Contexto = "Data: $($_.Data)"
                })
            }
        }
    } catch { }

    # --- Hashes de procesos ---
    try {
        $hashFile = Join-Path $OutputPath "$($Script:OutputDirs.Volatile)\ProcessHashes.csv"
        if (Test-Path $hashFile) {
            Import-Csv $hashFile -ErrorAction SilentlyContinue | ForEach-Object {
                [void]$iocs.Add([PSCustomObject]@{
                    Tipo    = "SHA256"
                    Valor   = $_.SHA256
                    Fuente  = "Procesos Activos"
                    Contexto = "PID: $($_.ProcessId), Nombre: $($_.Name), Ruta: $($_.Path)"
                })
            }
        }
    } catch { }

    # --- IPs de logons fallidos ---
    try {
        $failedFile = Join-Path $OutputPath "$($Script:OutputDirs.EventLogs)\Analysis\SecurityFailedLogon.csv"
        if (Test-Path $failedFile) {
            Import-Csv $failedFile -ErrorAction SilentlyContinue | Where-Object {
                $_.IPOrigen -and $_.IPOrigen -ne '-' -and $_.IPOrigen -ne '' -and $_.IPOrigen -ne '127.0.0.1'
            } | Group-Object IPOrigen | ForEach-Object {
                [void]$iocs.Add([PSCustomObject]@{
                    Tipo    = "IP"
                    Valor   = $_.Name
                    Fuente  = "Failed Logons"
                    Contexto = "$($_.Count) intentos fallidos"
                })
            }
        }
    } catch { }

    # --- Servidores RDP ---
    try {
        $rdpFile = Join-Path $OutputPath "$($Script:OutputDirs.Registry)\ExtractedValues\RDPHistory.csv"
        if (Test-Path $rdpFile) {
            Import-Csv $rdpFile -ErrorAction SilentlyContinue | ForEach-Object {
                [void]$iocs.Add([PSCustomObject]@{
                    Tipo    = "Host"
                    Valor   = $_.Servidor
                    Fuente  = "RDP History"
                    Contexto = "Usuario: $($_.Usuario), UsuarioRDP: $($_.UsuarioRDP)"
                })
            }
        }
    } catch { }

    # --- URLs de MRU ---
    try {
        $mruFile = Join-Path $OutputPath "$($Script:OutputDirs.Registry)\ExtractedValues\MRULists.csv"
        if (Test-Path $mruFile) {
            Import-Csv $mruFile -ErrorAction SilentlyContinue | Where-Object { $_.Tipo -eq 'TypedURLs' } | ForEach-Object {
                [void]$iocs.Add([PSCustomObject]@{
                    Tipo    = "URL"
                    Valor   = $_.Valor
                    Fuente  = "TypedURLs"
                    Contexto = "Usuario: $($_.Usuario)"
                })
            }
        }
    } catch { }

    # --- Deduplicar y exportar ---
    if ($iocs.Count -gt 0) {
        $uniqueIOCs = $iocs | Sort-Object Tipo, Valor -Unique
        Export-CollectionToCSV -Data $uniqueIOCs -OutputPath (Join-Path $analysisPath "IOCs.csv") -Phase "ANALISIS" -Description "IOCs consolidados"
        Export-CollectionToJSON -Data $uniqueIOCs -OutputPath (Join-Path $analysisPath "IOCs.json") -Phase "ANALISIS" -Description "IOCs (JSON)"
    }

    $sw.Stop()
    Write-ForensicLog -Phase "ANALISIS" -Action "IOC_EXTRACCION" -Target "$($iocs.Count) indicadores extraidos" -Result "OK" -DurationMs $sw.ElapsedMilliseconds

    return $iocs.Count
}

#endregion ===== IOCExtractor.ps1 =====

#region ===== Anomalies.ps1 =====
#region ============================================================
# MaPeriT.SW - DETECCION DE ANOMALIAS
# Motor de analisis automatizado
# ============================================================

function Find-ForensicAnomalies {
    param([string]$OutputPath)

    $analysisPath = Join-Path $OutputPath $Script:OutputDirs.Analysis
    $anomalies = [System.Collections.ArrayList]::new()
    $anomalyId = 0

    Write-StepHeader "Ejecutando Deteccion de Anomalias"
    $sw = [System.Diagnostics.Stopwatch]::StartNew()

    # --- A1: Procesos desde ubicaciones sospechosas ---
    try {
        $suspFile = Join-Path $OutputPath "$($Script:OutputDirs.Persistence)\SuspiciousProcesses.csv"
        if (Test-Path $suspFile) {
            $suspProcs = Import-Csv $suspFile -ErrorAction SilentlyContinue
            foreach ($proc in $suspProcs) {
                $anomalyId++
                [void]$anomalies.Add([PSCustomObject]@{
                    ID          = "ANO-{0:D4}" -f $anomalyId
                    Severidad   = $Script:AnomalySeverity.Medium
                    Categoria   = "Proceso sospechoso"
                    Titulo      = "Proceso ejecutandose desde ubicacion inusual"
                    Descripcion = "El proceso '$($proc.Nombre)' (PID: $($proc.PID)) se esta ejecutando desde '$($proc.Ejecutable)', que es una ubicacion atipica para ejecutables legitimos."
                    Evidencia   = $suspFile
                    Recomendacion = "Verificar la legitimidad del ejecutable. Comprobar su hash contra bases de datos de malware."
                })
            }
        }
    } catch { }

    # --- A2: Servicios sin firma digital ---
    try {
        $unsignedFile = Join-Path $OutputPath "$($Script:OutputDirs.Persistence)\UnsignedServices.csv"
        if (Test-Path $unsignedFile) {
            $unsignedSvcs = Import-Csv $unsignedFile -ErrorAction SilentlyContinue
            foreach ($svc in $unsignedSvcs) {
                $anomalyId++
                [void]$anomalies.Add([PSCustomObject]@{
                    ID          = "ANO-{0:D4}" -f $anomalyId
                    Severidad   = $Script:AnomalySeverity.High
                    Categoria   = "Servicio sin firma"
                    Titulo      = "Servicio con ejecutable sin firma valida: $($svc.Servicio)"
                    Descripcion = "El servicio '$($svc.NombreMostrado)' ejecuta '$($svc.Ejecutable)' con estado de firma '$($svc.EstadoFirma)'. Los servicios legitimos de Windows deben tener firma digital valida."
                    Evidencia   = $unsignedFile
                    Recomendacion = "Investigar el origen del ejecutable. Un servicio sin firma puede indicar manipulacion o software no autorizado."
                })
            }
        }
    } catch { }

    # --- A3: Persistencia WMI ---
    try {
        $wmiFile = Join-Path $OutputPath "$($Script:OutputDirs.Persistence)\WMIPersistence.csv"
        if (Test-Path $wmiFile) {
            $wmiItems = @(Import-Csv $wmiFile -ErrorAction SilentlyContinue)
            if ($wmiItems.Count -gt 0) {
                $anomalyId++
                [void]$anomalies.Add([PSCustomObject]@{
                    ID          = "ANO-{0:D4}" -f $anomalyId
                    Severidad   = $Script:AnomalySeverity.Critical
                    Categoria   = "Persistencia WMI"
                    Titulo      = "Suscripciones WMI de persistencia detectadas"
                    Descripcion = "Se detectaron $($wmiItems.Count) elementos de persistencia WMI (EventFilters, EventConsumers, Bindings). Las suscripciones WMI son un mecanismo avanzado de persistencia frecuentemente utilizado por malware y APTs."
                    Evidencia   = $wmiFile
                    Recomendacion = "Analizar detalladamente cada suscripcion WMI. Verificar si corresponden a software legitimo o a persistencia maliciosa."
                })
            }
        }
    } catch { }

    # --- A4: Fuerza bruta (logons fallidos excesivos) ---
    try {
        $failedFile = Join-Path $OutputPath "$($Script:OutputDirs.EventLogs)\Analysis\SecurityFailedLogon.csv"
        if (Test-Path $failedFile) {
            $failedLogons = Import-Csv $failedFile -ErrorAction SilentlyContinue
            $grouped = $failedLogons | Group-Object UsuarioDestino | Where-Object { $_.Count -gt 10 }
            foreach ($group in $grouped) {
                $anomalyId++
                [void]$anomalies.Add([PSCustomObject]@{
                    ID          = "ANO-{0:D4}" -f $anomalyId
                    Severidad   = if($group.Count -gt 50){$Script:AnomalySeverity.Critical}else{$Script:AnomalySeverity.High}
                    Categoria   = "Fuerza bruta"
                    Titulo      = "Multiples intentos de sesion fallidos para '$($group.Name)'"
                    Descripcion = "Se detectaron $($group.Count) intentos de inicio de sesion fallidos para el usuario '$($group.Name)'. Esto puede indicar un ataque de fuerza bruta o intentos de acceso no autorizado."
                    Evidencia   = $failedFile
                    Recomendacion = "Verificar las IPs de origen. Comprobar si los intentos coinciden con horarios laborales normales. Revisar si la cuenta fue finalmente comprometida."
                })
            }

            $groupedByIP = $failedLogons | Where-Object { $_.IPOrigen -and $_.IPOrigen -ne '-' } | Group-Object IPOrigen | Where-Object { $_.Count -gt 20 }
            foreach ($group in $groupedByIP) {
                $anomalyId++
                [void]$anomalies.Add([PSCustomObject]@{
                    ID          = "ANO-{0:D4}" -f $anomalyId
                    Severidad   = $Script:AnomalySeverity.High
                    Categoria   = "Fuerza bruta por IP"
                    Titulo      = "Multiples intentos fallidos desde IP $($group.Name)"
                    Descripcion = "$($group.Count) intentos de sesion fallidos originados desde la direccion IP '$($group.Name)'."
                    Evidencia   = $failedFile
                    Recomendacion = "Identificar la IP de origen. Verificar si es interna o externa. Considerar bloqueo si es maliciosa."
                })
            }
        }
    } catch { }

    # --- A5: Servicios instalados recientemente ---
    try {
        $recentSvcFile = Join-Path $OutputPath "$($Script:OutputDirs.Persistence)\RecentServiceInstalls.csv"
        if (Test-Path $recentSvcFile) {
            $recentSvcs = Import-Csv $recentSvcFile -ErrorAction SilentlyContinue
            foreach ($svc in $recentSvcs) {
                $riesgo = "Baja"
                if ($svc.ImagePath -match 'Users|Temp|AppData|cmd\.exe|powershell|mshta|wscript|cscript|rundll32') {
                    $riesgo = "Alta"
                }
                if ($riesgo -eq "Alta") {
                    $anomalyId++
                    [void]$anomalies.Add([PSCustomObject]@{
                        ID          = "ANO-{0:D4}" -f $anomalyId
                        Severidad   = $Script:AnomalySeverity.High
                        Categoria   = "Servicio sospechoso"
                        Titulo      = "Servicio instalado con ruta sospechosa: $($svc.NombreServicio)"
                        Descripcion = "El servicio '$($svc.NombreServicio)' fue instalado el $($svc.Timestamp) con ImagePath='$($svc.ImagePath)'. La ruta contiene indicadores de posible actividad maliciosa."
                        Evidencia   = $recentSvcFile
                        Recomendacion = "Investigar quien y por que instalo este servicio. Analizar el ejecutable."
                    })
                }
            }
        }
    } catch { }

    # --- A6: Alternate Data Streams ---
    try {
        $adsFile = Join-Path $OutputPath "$($Script:OutputDirs.Persistence)\AlternateDataStreams.csv"
        if (Test-Path $adsFile) {
            $ads = @(Import-Csv $adsFile -ErrorAction SilentlyContinue)
            if ($ads.Count -gt 0) {
                $anomalyId++
                [void]$anomalies.Add([PSCustomObject]@{
                    ID          = "ANO-{0:D4}" -f $anomalyId
                    Severidad   = $Script:AnomalySeverity.Medium
                    Categoria   = "Alternate Data Streams"
                    Titulo      = "Alternate Data Streams sospechosos detectados"
                    Descripcion = "Se encontraron $($ads.Count) Alternate Data Streams no estandar en ficheros del sistema. Los ADS pueden ser utilizados para ocultar datos o codigo malicioso."
                    Evidencia   = $adsFile
                    Recomendacion = "Examinar el contenido de cada ADS. Los ADS de Zone.Identifier son normales y han sido excluidos."
                })
            }
        }
    } catch { }

    # --- A7: Apagados inesperados ---
    try {
        $shutdownFile = Join-Path $OutputPath "$($Script:OutputDirs.EventLogs)\Analysis\SystemShutdowns.csv"
        if (Test-Path $shutdownFile) {
            $unexpected = @(Import-Csv $shutdownFile -ErrorAction SilentlyContinue | Where-Object { $_.EventID -eq '6008' })
            if ($unexpected.Count -gt 0) {
                $anomalyId++
                [void]$anomalies.Add([PSCustomObject]@{
                    ID          = "ANO-{0:D4}" -f $anomalyId
                    Severidad   = $Script:AnomalySeverity.Medium
                    Categoria   = "Apagado inesperado"
                    Titulo      = "$($unexpected.Count) apagados inesperados del sistema"
                    Descripcion = "El sistema registro $($unexpected.Count) eventos de apagado inesperado (Event ID 6008). Esto puede indicar cortes de energia, fallos del sistema, o desconexiones forzadas para evitar registro de actividad."
                    Evidencia   = $shutdownFile
                    Recomendacion = "Correlacionar las fechas de apagados inesperados con otros eventos del sistema."
                })
            }
        }
    } catch { }

    # --- A8: Amenazas detectadas por Defender ---
    try {
        $avFile = Join-Path $OutputPath "$($Script:OutputDirs.SystemInfo)\AntivirusStatus.json"
        if (Test-Path $avFile) {
            $avData = Get-Content $avFile -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json -ErrorAction SilentlyContinue
            # Una sola amenaza se serializa como objeto suelto, no como array: sin @()
            # su .Count es $null en PowerShell 5.1 y la anomalia CRITICA se perdia.
            $amenazas = @($avData.AmenazasDetectadas | Where-Object { $_ })
            if ($amenazas.Count -gt 0) {
                $anomalyId++
                [void]$anomalies.Add([PSCustomObject]@{
                    ID          = "ANO-{0:D4}" -f $anomalyId
                    Severidad   = $Script:AnomalySeverity.Critical
                    Categoria   = "Malware detectado"
                    Titulo      = "Windows Defender detecto $($amenazas.Count) amenazas"
                    Descripcion = "Windows Defender tiene registro de $($amenazas.Count) detecciones de amenazas en este sistema. Esto confirma la presencia de software malicioso o potencialmente no deseado."
                    Evidencia   = $avFile
                    Recomendacion = "Analizar cada amenaza detectada, verificar si fue eliminada o puesta en cuarentena. Determinar vector de entrada."
                })
            }
        }
    } catch { }

    # --- A9: Autoruns sospechosos ---
    try {
        $autoFile = Join-Path $OutputPath "$($Script:OutputDirs.Persistence)\AutorunsConsolidated.csv"
        if (Test-Path $autoFile) {
            $highRiskAutoruns = Import-Csv $autoFile -ErrorAction SilentlyContinue | Where-Object { $_.Riesgo -eq 'Alta' }
            foreach ($ar in $highRiskAutoruns) {
                $anomalyId++
                [void]$anomalies.Add([PSCustomObject]@{
                    ID          = "ANO-{0:D4}" -f $anomalyId
                    Severidad   = $Script:AnomalySeverity.High
                    Categoria   = "Autorun sospechoso"
                    Titulo      = "Autorun de alto riesgo: $($ar.Nombre)"
                    Descripcion = "Se detecto un autorun de alto riesgo: Tipo='$($ar.Tipo)', Nombre='$($ar.Nombre)', Valor='$($ar.Valor)'. Las entradas en ubicaciones de usuario o con rutas a interpretes de comandos son indicadores de posible compromiso."
                    Evidencia   = $autoFile
                    Recomendacion = "Verificar la legitimidad de este autorun. Comprobar si coincide con software conocido."
                })
            }
        }
    } catch { }

    # --- A10: Certificados raiz sospechosos ---
    try {
        $certFile = Join-Path $OutputPath "$($Script:OutputDirs.Persistence)\SuspiciousRootCerts.csv"
        if (Test-Path $certFile) {
            $certs = @(Import-Csv $certFile -ErrorAction SilentlyContinue)
            if ($certs.Count -gt 0) {
                $anomalyId++
                [void]$anomalies.Add([PSCustomObject]@{
                    ID          = "ANO-{0:D4}" -f $anomalyId
                    Severidad   = $Script:AnomalySeverity.High
                    Categoria   = "Certificado raiz sospechoso"
                    Titulo      = "$($certs.Count) certificados raiz no estandar en el almacen de confianza"
                    Descripcion = "Se detectaron certificados raiz en Cert:\LocalMachine\Root que no corresponden a autoridades de certificacion reconocidas. Esto puede indicar interceptacion de trafico HTTPS (MITM), adware o herramientas de monitorizacion de red."
                    Evidencia   = $certFile
                    Recomendacion = "Examinar cada certificado. Verificar si corresponde a software corporativo legitimo (proxies, DLP) o a software no autorizado."
                })
            }
        }
    } catch { }

    # --- A11: COM Hijacking ---
    try {
        $comFile = Join-Path $OutputPath "$($Script:OutputDirs.Persistence)\COMHijacking.csv"
        if (Test-Path $comFile) {
            $comEntries = Import-Csv $comFile -ErrorAction SilentlyContinue
            $highRisk = @($comEntries | Where-Object { $_.Riesgo -eq 'Alta' })
            if ($highRisk.Count -gt 0) {
                $anomalyId++
                [void]$anomalies.Add([PSCustomObject]@{
                    ID          = "ANO-{0:D4}" -f $anomalyId
                    Severidad   = $Script:AnomalySeverity.High
                    Categoria   = "COM Hijacking"
                    Titulo      = "$($highRisk.Count) entradas CLSID en HKCU apuntan a ubicaciones de usuario"
                    Descripcion = "Se detectaron objetos COM registrados en HKCU\Software\Classes\CLSID con servidores (InprocServer32/LocalServer32) que apuntan a directorios de usuario. Esta tecnica es utilizada para persistencia y evasion."
                    Evidencia   = $comFile
                    Recomendacion = "Verificar cada CLSID. Comprobar si los ejecutables referenciados son legitimos."
                })
            }
        }
    } catch { }

    # --- Exportar resultados ---
    if ($anomalies.Count -gt 0) {
        Export-CollectionToCSV -Data $anomalies -OutputPath (Join-Path $analysisPath "Anomalies.csv") -Phase "ANALISIS" -Description "Anomalias detectadas"
        Export-CollectionToJSON -Data $anomalies -OutputPath (Join-Path $analysisPath "Anomalies.json") -Phase "ANALISIS" -Description "Anomalias (JSON)"

        $summary = [PSCustomObject]@{
            TotalAnomalias = $anomalies.Count
            Criticas = @($anomalies | Where-Object { $_.Severidad -eq $Script:AnomalySeverity.Critical }).Count
            Altas    = @($anomalies | Where-Object { $_.Severidad -eq $Script:AnomalySeverity.High }).Count
            Medias   = @($anomalies | Where-Object { $_.Severidad -eq $Script:AnomalySeverity.Medium }).Count
            Bajas    = @($anomalies | Where-Object { $_.Severidad -eq $Script:AnomalySeverity.Low }).Count
        }

        Write-Host ""
        Write-Host "  ┌──────────────────────────────────────────┐" -ForegroundColor White
        Write-Host "  │      RESUMEN DE ANOMALIAS DETECTADAS     │" -ForegroundColor White
        Write-Host "  ├──────────────────────────────────────────┤" -ForegroundColor White
        if ($summary.Criticas -gt 0) {
            Write-Host "  │  CRITICAS:  $($summary.Criticas.ToString().PadRight(28))│" -ForegroundColor Red
        }
        if ($summary.Altas -gt 0) {
            Write-Host "  │  ALTAS:     $($summary.Altas.ToString().PadRight(28))│" -ForegroundColor DarkYellow
        }
        if ($summary.Medias -gt 0) {
            Write-Host "  │  MEDIAS:    $($summary.Medias.ToString().PadRight(28))│" -ForegroundColor Yellow
        }
        if ($summary.Bajas -gt 0) {
            Write-Host "  │  BAJAS:     $($summary.Bajas.ToString().PadRight(28))│" -ForegroundColor Cyan
        }
        Write-Host "  │  TOTAL:     $($summary.TotalAnomalias.ToString().PadRight(28))│" -ForegroundColor White
        Write-Host "  └──────────────────────────────────────────┘" -ForegroundColor White
    } else {
        Write-Host "  [+] No se detectaron anomalias significativas." -ForegroundColor Green
    }

    $sw.Stop()
    Write-ForensicLog -Phase "ANALISIS" -Action "ANOMALIAS_COMPLETADAS" -Target "$($anomalies.Count) anomalias" -Result "OK" -DurationMs $sw.ElapsedMilliseconds

    return $anomalies.Count
}

#endregion ===== Anomalies.ps1 =====

#region ===== ChainOfCustody.ps1 =====
#region ============================================================
# MaPeriT.SW - CADENA DE CUSTODIA
# ============================================================

function New-ChainOfCustodyReport {
    param([string]$OutputPath)

    $reportPath = Join-Path $OutputPath $Script:OutputDirs.Report
    $custodyFile = Join-Path $OutputPath "$($Script:OutputDirs.Logs)\ChainOfCustody.csv"

    if (-not (Test-Path $custodyFile)) {
        Write-ForensicLog -Phase "INFORME" -Action "CUSTODIA_SIN_DATOS" -Target $custodyFile -Result "WARNING"
        return
    }

    $custodyData = @(Import-Csv $custodyFile -ErrorAction SilentlyContinue)
    $totalEvidences = $custodyData.Count

    # Campos de entrada del perito (CaseNumber, ExaminerName, EvidenceDescription) o del
    # equipo analizado (ComputerName, OSVersion/Build) interpolados en HTML: deben ir
    # codificados igual que ya se hace en la tabla de evidencias mas abajo.
    $caseNumberEnc   = [System.Web.HttpUtility]::HtmlEncode($Script:SessionInfo.CaseNumber)
    $examinerNameEnc = [System.Web.HttpUtility]::HtmlEncode($Script:SessionInfo.ExaminerName)
    $evidenceDescEnc = [System.Web.HttpUtility]::HtmlEncode($Script:SessionInfo.EvidenceDescription)
    $computerNameEnc = [System.Web.HttpUtility]::HtmlEncode($Script:SessionInfo.ComputerName)
    $osVersionEnc    = [System.Web.HttpUtility]::HtmlEncode($Script:SessionInfo.OSVersion)
    $osBuildEnc      = [System.Web.HttpUtility]::HtmlEncode($Script:SessionInfo.OSBuild)

    $html = @"
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Cadena de Custodia - Caso $caseNumberEnc</title>
<style>
    @page { size: A4; margin: 2cm; }
    @media print { .no-print { display: none; } body { font-size: 10pt; } }
    body { font-family: 'Georgia', 'Times New Roman', serif; color: #1a1a1a; margin: 0; padding: 20px; background: #fff; line-height: 1.4; }
    .header { text-align: center; border: 3px double #333; padding: 20px; margin-bottom: 30px; }
    .header h1 { font-size: 22px; margin: 0; letter-spacing: 2px; }
    .header h2 { font-size: 16px; margin: 5px 0; color: #555; font-weight: normal; }
    .confidential { background: #fee; border: 1px solid #c00; padding: 8px; text-align: center; font-weight: bold; color: #c00; margin: 15px 0; font-size: 12px; }
    .info-table { width: 100%; border-collapse: collapse; margin: 20px 0; }
    .info-table td { padding: 8px 12px; border: 1px solid #ccc; }
    .info-table td:first-child { background: #f5f5f5; font-weight: bold; width: 200px; }
    .evidence-table { width: 100%; border-collapse: collapse; margin: 20px 0; font-size: 9pt; }
    .evidence-table th { background: #2c3e50; color: white; padding: 8px 6px; text-align: left; font-size: 9pt; }
    .evidence-table td { padding: 6px; border: 1px solid #ddd; word-break: break-all; }
    .evidence-table tr:nth-child(even) { background: #f9f9f9; }
    .evidence-table tr:hover { background: #eef; }
    .section-title { background: #2c3e50; color: white; padding: 10px 15px; margin: 25px 0 10px 0; font-size: 14px; }
    .signatures { margin-top: 50px; page-break-inside: avoid; }
    .sig-row { display: flex; justify-content: space-between; margin-top: 40px; }
    .sig-block { width: 45%; text-align: center; }
    .sig-line { border-top: 1px solid #333; margin-top: 50px; padding-top: 5px; }
    .footer { text-align: center; font-size: 9px; color: #888; margin-top: 30px; border-top: 1px solid #ccc; padding-top: 10px; }
    .hash-note { font-family: 'Courier New', monospace; font-size: 8pt; color: #555; word-break: break-all; }
</style>
</head>
<body>

<div class="header">
    <h1>ACTA DE CADENA DE CUSTODIA</h1>
    <h2>Evidencias Digitales - Analisis Forense Informatico</h2>
</div>

<div class="confidential">DOCUMENTO CONFIDENCIAL - CADENA DE CUSTODIA DE EVIDENCIAS DIGITALES</div>

<div class="section-title">1. DATOS DEL CASO</div>
<table class="info-table">
    <tr><td>Numero de Caso</td><td>$caseNumberEnc</td></tr>
    <tr><td>Perito Informatico</td><td>$examinerNameEnc</td></tr>
    <tr><td>Descripcion</td><td>$evidenceDescEnc</td></tr>
    <tr><td>Fecha de Inicio</td><td>$($Script:SessionInfo.StartTime.ToString($Script:Defaults.TimeZoneFormat))</td></tr>
    <tr><td>Fecha de Fin</td><td>$($Script:SessionInfo.EndTime.ToString($Script:Defaults.TimeZoneFormat))</td></tr>
    <tr><td>Equipo Analizado</td><td>$computerNameEnc</td></tr>
    <tr><td>Sistema Operativo</td><td>$osVersionEnc (Build $osBuildEnc)</td></tr>
</table>

<div class="section-title">2. HERRAMIENTA UTILIZADA</div>
<table class="info-table">
    <tr><td>Herramienta</td><td>$($Script:SessionInfo.ToolName) v$($Script:SessionInfo.ToolVersion)</td></tr>
    <tr><td>Hash SHA-256</td><td class="hash-note">$($Script:SessionInfo.ScriptHash)</td></tr>
    <tr><td>PowerShell</td><td>$($Script:SessionInfo.PSVersion)</td></tr>
</table>

<div class="section-title">3. INVENTARIO DE EVIDENCIAS ($totalEvidences elementos)</div>
<table class="evidence-table">
<thead>
    <tr><th>ID</th><th>Timestamp</th><th>Origen</th><th>Destino</th><th>SHA-256</th><th>Metodo</th><th>Tamano</th></tr>
</thead>
<tbody>
"@

    # StringBuilder en vez de concatenacion de string en bucle (+=): con miles de evidencias
    # la concatenacion repetida es O(n^2) y podia hacer muy lenta la generacion del acta.
    $rowsSb = [System.Text.StringBuilder]::new()
    $rowErrors = 0

    foreach ($entry in $custodyData) {
        try {
            $sourcePath = if ($null -eq $entry.SourcePath) { "" } else { $entry.SourcePath }
            $destPath   = if ($null -eq $entry.DestinationPath) { "" } else { $entry.DestinationPath }
            $hashValue  = if ($null -eq $entry.SHA256Hash) { "" } else { $entry.SHA256Hash }

            $shortSource = if ($sourcePath.Length -gt 50) { "..." + $sourcePath.Substring($sourcePath.Length - 47) } else { $sourcePath }
            $shortDest   = if ($destPath.Length -gt 50) { "..." + $destPath.Substring($destPath.Length - 47) } else { $destPath }
            $shortHash   = if ($hashValue.Length -gt 16) { $hashValue.Substring(0, 16) + "..." } else { $hashValue }
            $sizeParsed  = 0L
            [void][long]::TryParse($entry.FileSize, [ref]$sizeParsed)
            $sizeFormatted = Format-FileSize -Bytes $sizeParsed

            [void]$rowsSb.AppendLine(@"
    <tr>
        <td>$($entry.EvidenceID)</td>
        <td style="white-space:nowrap">$($entry.Timestamp)</td>
        <td title="$([System.Web.HttpUtility]::HtmlEncode($sourcePath))">$([System.Web.HttpUtility]::HtmlEncode($shortSource))</td>
        <td title="$([System.Web.HttpUtility]::HtmlEncode($destPath))">$([System.Web.HttpUtility]::HtmlEncode($shortDest))</td>
        <td class="hash-note" title="$([System.Web.HttpUtility]::HtmlEncode($hashValue))">$([System.Web.HttpUtility]::HtmlEncode($shortHash))</td>
        <td>$([System.Web.HttpUtility]::HtmlEncode($entry.Method))</td>
        <td style="white-space:nowrap">$sizeFormatted</td>
    </tr>
"@)
        } catch {
            # Antes, una unica fila con datos inesperados (p.ej. FileSize no numerico) lanzaba
            # una excepcion no capturada que abortaba la generacion de TODO el acta de cadena
            # de custodia (documento legal), sin generar ningun HTML. Ahora esa fila se marca
            # como error y se continua con el resto de evidencias.
            $rowErrors++
            [void]$rowsSb.AppendLine("<tr><td colspan='7' style='color:#c00'>[Error al procesar esta entrada de evidencia: $([System.Web.HttpUtility]::HtmlEncode($_.Exception.Message))]</td></tr>")
            Write-ForensicLog -Phase "INFORME" -Action "CUSTODIA_FILA_ERROR" -Target "$($entry.EvidenceID)" -Result "ERROR" -ErrorMessage $_.Exception.Message
        }
    }
    $html += $rowsSb.ToString()
    if ($rowErrors -gt 0) {
        Write-ForensicLog -Phase "INFORME" -Action "CUSTODIA_FILAS_CON_ERROR" -Target "$rowErrors de $totalEvidences filas no se pudieron formatear correctamente" -Result "WARNING"
    }

    $html += @"
</tbody>
</table>

<div class="section-title">4. VERIFICACION DE INTEGRIDAD</div>
<table class="info-table">
    <tr><td>Total de Evidencias</td><td>$totalEvidences</td></tr>
    <tr><td>Manifiesto de Hashes</td><td>Logs\HashManifest.csv</td></tr>
    <tr><td>Algoritmo</td><td>SHA-256 (FIPS 180-4)</td></tr>
    <tr><td>Nota</td><td>Todos los hashes han sido calculados sobre los ficheros de evidencia en el momento de su adquisicion. Para verificar la integridad, recalcule los hashes y compare con el manifiesto.</td></tr>
</table>

<div class="signatures">
<div class="section-title">5. FIRMAS</div>
<p>Con la presente acta se certifica que las evidencias digitales listadas han sido adquiridas siguiendo los procedimientos establecidos en la RFC 3227 y NIST SP 800-86, manteniendo la cadena de custodia en todo momento.</p>

<div class="sig-row">
    <div class="sig-block">
        <div class="sig-line">Perito Informatico<br><strong>$examinerNameEnc</strong><br>Fecha: $($Script:SessionInfo.EndTime.ToString('dd/MM/yyyy'))</div>
    </div>
    <div class="sig-block">
        <div class="sig-line">Testigo / Custodio<br><br>Fecha: ___/___/______</div>
    </div>
</div>
</div>

<div class="footer">
    Documento generado por $($Script:TOOL_FULL) v$($Script:TOOL_VERSION)<br>
    Caso: $caseNumberEnc | Fecha: $($Script:SessionInfo.EndTime.ToString('dd/MM/yyyy HH:mm'))
</div>

</body>
</html>
"@

    $custodyHtmlPath = Join-Path $reportPath "CadenaCustodia.html"
    $html | Out-File -FilePath $custodyHtmlPath -Encoding UTF8
    Write-ForensicLog -Phase "INFORME" -Action "CADENA_CUSTODIA_GENERADA" -Target $custodyHtmlPath -Result "OK"
}

function Format-FileSize {
    param([long]$Bytes)
    if ($Bytes -ge 1GB) { return "{0:N2} GB" -f ($Bytes / 1GB) }
    if ($Bytes -ge 1MB) { return "{0:N2} MB" -f ($Bytes / 1MB) }
    if ($Bytes -ge 1KB) { return "{0:N2} KB" -f ($Bytes / 1KB) }
    return "$Bytes B"
}

#endregion ===== ChainOfCustody.ps1 =====

#region ===== ReportTemplates.ps1 =====
#region ============================================================
# MaPeriT.SW - PLANTILLAS HTML/CSS DEL INFORME
# ============================================================

function Get-ReportCSS {
    return @'
:root {
  --sw: 230px;
  --c-bg: #f1f5f9;
  --c-surface: #ffffff;
  --c-border: #e2e8f0;
  --c-text: #1e293b;
  --c-muted: #64748b;
  --c-accent: #1d4ed8;
  --c-accent-bg: #dbeafe;
  --c-nav: #0f172a;
  --c-nav-t: #94a3b8;
  --c-critical: #dc2626;
  --c-critical-bg: #fef2f2;
  --c-high: #ea580c;
  --c-high-bg: #fff7ed;
  --c-medium: #ca8a04;
  --c-medium-bg: #fefce8;
  --c-low: #2563eb;
  --c-low-bg: #eff6ff;
  --c-ok: #16a34a;
  --c-ok-bg: #f0fdf4;
  --r: 6px;
  --sh: 0 1px 3px rgba(0,0,0,.1),0 1px 2px rgba(0,0,0,.06);
}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{
  font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif;
  font-size:14px;line-height:1.6;color:var(--c-text);
  background:var(--c-bg);display:flex;min-height:100vh;
}

/* ── SIDEBAR ── */
.sidebar{
  width:var(--sw);min-height:100vh;background:var(--c-nav);
  position:fixed;top:0;left:0;overflow-y:auto;z-index:100;
  display:flex;flex-direction:column;
  border-right:1px solid rgba(255,255,255,.05);
}
.sb-brand{
  padding:20px 18px 16px;border-bottom:1px solid rgba(255,255,255,.08);
}
.sb-brand-name{font-size:17px;font-weight:700;color:#fff;letter-spacing:.3px}
.sb-brand-sub{font-size:11px;color:#64748b;margin-top:3px}
.sb-case{
  padding:10px 18px;border-bottom:1px solid rgba(255,255,255,.06);
  font-size:11px;color:#64748b;
}
.sb-case strong{color:#94a3b8;display:block;font-size:10px;text-transform:uppercase;letter-spacing:.8px;margin-bottom:2px}
nav{padding:10px 0}
.sb-label{
  padding:8px 18px 3px;font-size:10px;font-weight:600;
  text-transform:uppercase;letter-spacing:1px;color:#334155;
}
nav a{
  display:block;padding:6px 18px;color:var(--c-nav-t);
  text-decoration:none;font-size:12.5px;
  border-left:3px solid transparent;transition:all .12s;
}
nav a:hover,nav a.active{color:#fff;border-left-color:#3b82f6;background:rgba(255,255,255,.04)}
.sb-footer{
  margin-top:auto;padding:14px 18px;border-top:1px solid rgba(255,255,255,.06);
  font-size:10px;color:#334155;line-height:1.6;
}

/* ── MAIN ── */
.main{margin-left:var(--sw);flex:1;min-width:0}

/* ── COVER ── */
.cover{
  background:linear-gradient(135deg,#0f172a 0%,#1e3a5f 50%,#0f172a 100%);
  color:#fff;padding:72px 64px;min-height:620px;
  display:flex;flex-direction:column;justify-content:center;
}
.cover-badge{
  display:inline-block;background:rgba(59,130,246,.2);
  border:1px solid rgba(59,130,246,.4);color:#93c5fd;
  padding:4px 14px;border-radius:20px;font-size:11px;
  font-weight:600;letter-spacing:1.2px;text-transform:uppercase;margin-bottom:28px;
}
.cover h1{font-size:44px;font-weight:800;letter-spacing:-1px;line-height:1.1}
.cover h2{font-size:17px;font-weight:400;color:#94a3b8;margin-top:10px}
.cover-grid{
  display:grid;grid-template-columns:1fr 1fr;gap:10px 48px;
  margin-top:52px;padding-top:36px;
  border-top:1px solid rgba(255,255,255,.12);
}
.cover-item label{font-size:10px;text-transform:uppercase;letter-spacing:1px;color:#64748b}
.cover-item span{display:block;font-size:15px;font-weight:600;color:#e2e8f0;margin-top:2px}
.cover-hash{
  margin-top:36px;font-family:'Cascadia Code','Fira Code','Courier New',monospace;
  font-size:10px;color:#334155;word-break:break-all;line-height:1.8;
}
.confidential{
  display:inline-block;background:rgba(220,38,38,.2);
  border:1px solid rgba(220,38,38,.4);color:#fca5a5;
  padding:4px 14px;border-radius:4px;font-size:11px;
  font-weight:700;letter-spacing:1px;text-transform:uppercase;margin-top:24px;
}

/* ── CONTENT ── */
.content{padding:48px 56px;max-width:1060px}

/* ── SECTION HEADERS ── */
.section-hdr{
  display:flex;align-items:center;gap:14px;
  padding-bottom:14px;border-bottom:2px solid var(--c-border);
  margin:48px 0 28px;
}
.sec-num{
  background:var(--c-accent);color:#fff;
  width:34px;height:34px;border-radius:50%;
  display:flex;align-items:center;justify-content:center;
  font-size:14px;font-weight:700;flex-shrink:0;
}
.section-hdr h2{font-size:22px;font-weight:700}
.sub-hdr{
  font-size:13px;font-weight:600;color:var(--c-text);
  padding:9px 14px;margin:24px 0 10px;
  background:var(--c-surface);border-left:3px solid var(--c-accent);
  border-radius:0 var(--r) var(--r) 0;box-shadow:var(--sh);
}

/* ── STAT GRID ── */
.stat-grid{
  display:grid;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));
  gap:14px;margin:24px 0;
}
.stat-card{
  background:var(--c-surface);border:1px solid var(--c-border);
  border-radius:var(--r);padding:20px 18px;box-shadow:var(--sh);
  border-top:3px solid var(--c-accent);
}
.stat-card.crit{border-top-color:var(--c-critical)}
.stat-card.ok{border-top-color:var(--c-ok)}
.stat-card.warn{border-top-color:var(--c-medium)}
.sv{font-size:34px;font-weight:800;line-height:1;color:var(--c-accent)}
.stat-card.crit .sv{color:var(--c-critical)}
.stat-card.ok .sv{color:var(--c-ok)}
.stat-card.warn .sv{color:var(--c-medium)}
.sl{font-size:12px;color:var(--c-muted);margin-top:6px}

/* ── INFO TABLE ── */
.info-table{width:100%;border-collapse:collapse;background:var(--c-surface);border-radius:var(--r);box-shadow:var(--sh);overflow:hidden}
.info-table tr{border-bottom:1px solid var(--c-border)}
.info-table tr:last-child{border-bottom:none}
.info-table td:first-child{
  background:#f8fafc;font-weight:600;font-size:11.5px;
  color:var(--c-muted);width:210px;padding:10px 14px;
  text-transform:uppercase;letter-spacing:.3px;white-space:nowrap;
}
.info-table td{padding:10px 14px;font-size:13px;word-break:break-word}

/* ── DATA TABLES ── */
.tcard{
  background:var(--c-surface);border-radius:var(--r);
  box-shadow:var(--sh);border:1px solid var(--c-border);
  overflow:hidden;margin:10px 0 20px;
}
.ttoolbar{
  padding:10px 14px;background:#f8fafc;
  border-bottom:1px solid var(--c-border);
  display:flex;align-items:center;justify-content:space-between;
  flex-wrap:wrap;gap:8px;
}
.ttoolbar-label{font-size:11px;font-weight:600;color:var(--c-muted);text-transform:uppercase;letter-spacing:.5px}
.tsearch{
  padding:5px 10px;border:1px solid var(--c-border);border-radius:4px;
  font-size:12px;width:190px;background:#fff;color:var(--c-text);
}
.tsearch:focus{outline:none;border-color:var(--c-accent);box-shadow:0 0 0 2px rgba(29,78,216,.15)}
.twrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
.dtable{width:100%;border-collapse:collapse;font-size:12.5px;min-width:400px}
.dtable thead{background:#1e293b;position:sticky;top:0;z-index:2}
.dtable th{
  padding:9px 12px;text-align:left;font-weight:600;
  color:#e2e8f0;font-size:11px;letter-spacing:.3px;
  white-space:nowrap;border-right:1px solid rgba(255,255,255,.06);
}
.dtable th:last-child{border-right:none}
.dtable td{
  padding:7px 12px;border-bottom:1px solid #f1f5f9;
  vertical-align:top;max-width:280px;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
}
.dtable tbody tr:hover{background:#f0f7ff}
.dtable tbody tr:last-child td{border-bottom:none}
.dtable td.w{white-space:normal;word-break:break-word;max-width:none}
.dtable td.mono{font-family:'Cascadia Code','Fira Code','Courier New',monospace;font-size:11px;color:#475569}
.tnote{
  padding:7px 14px;background:#fffbeb;border-top:1px solid #fef3c7;
  font-size:11px;color:#92400e;
}

/* ── PHASES TABLE ── */
.phases-table{width:100%;border-collapse:collapse;background:var(--c-surface);border-radius:var(--r);box-shadow:var(--sh);overflow:hidden}
.phases-table th{background:#1e293b;color:#e2e8f0;padding:10px 14px;font-size:12px;font-weight:600;text-align:left}
.phases-table td{padding:9px 14px;border-bottom:1px solid var(--c-border);font-size:13px}
.phases-table tr:last-child td{border-bottom:none}
.phases-table tr:hover td{background:#f8fafc}

/* ── BADGES ── */
.badge{display:inline-flex;align-items:center;padding:2px 9px;border-radius:4px;font-size:11px;font-weight:600;white-space:nowrap}
.b-ok{background:#dcfce7;color:#15803d}
.b-warn{background:#fef9c3;color:#a16207}
.b-err{background:#fee2e2;color:#b91c1c}
.b-info{background:#dbeafe;color:#1d4ed8}
.b-skip{background:#f3f4f6;color:#6b7280}

/* ── ANOMALIES ── */
.anom-card{
  background:var(--c-surface);border:1px solid var(--c-border);
  border-radius:var(--r);margin:10px 0;box-shadow:var(--sh);overflow:hidden;
}
.anom-hdr{
  display:flex;align-items:flex-start;gap:12px;
  padding:12px 16px;border-bottom:1px solid var(--c-border);
}
.anom-card.sc .anom-hdr{background:var(--c-critical-bg);border-bottom-color:#fecaca}
.anom-card.sh .anom-hdr{background:var(--c-high-bg);border-bottom-color:#fed7aa}
.anom-card.sm .anom-hdr{background:var(--c-medium-bg);border-bottom-color:#fef08a}
.anom-card.sl .anom-hdr{background:var(--c-low-bg);border-bottom-color:#bfdbfe}
.anom-body{padding:14px 16px}
.anom-body p{margin:5px 0;font-size:13px}
.anom-title{font-size:13.5px;font-weight:600;flex:1;line-height:1.4}
.anom-id{font-family:monospace;font-size:11px;color:var(--c-muted);white-space:nowrap}
.sev{
  display:inline-flex;align-items:center;padding:3px 10px;
  border-radius:20px;font-size:11px;font-weight:700;white-space:nowrap;flex-shrink:0;
}
.sev.CRITICA{background:var(--c-critical);color:#fff}
.sev.ALTA{background:var(--c-high);color:#fff}
.sev.MEDIA{background:var(--c-medium);color:#fff}
.sev.BAJA{background:var(--c-low);color:#fff}
.sev.INFORMATIVA{background:#94a3b8;color:#fff}

/* ── COLLAPSIBLE ── */
details{margin:6px 0}
summary{
  display:flex;align-items:center;gap:8px;
  padding:10px 14px;background:#f8fafc;border:1px solid var(--c-border);
  border-radius:var(--r);cursor:pointer;font-weight:600;font-size:13px;
  list-style:none;user-select:none;transition:background .12s;
}
summary::before{content:'▶';font-size:10px;transition:transform .15s;flex-shrink:0}
details[open]>summary::before{transform:rotate(90deg)}
details[open]>summary{border-radius:var(--r) var(--r) 0 0;border-bottom-color:transparent}
details[open]>.det-body{
  border:1px solid var(--c-border);border-top:none;
  border-radius:0 0 var(--r) var(--r);padding:14px;background:var(--c-surface);
}

/* ── SIGNATURE ── */
.sig-grid{display:grid;grid-template-columns:1fr 1fr;gap:48px;margin-top:60px;padding-top:36px;border-top:2px solid var(--c-border)}
.sig-item{text-align:center}
.sig-line{border-top:1px solid #94a3b8;margin-top:70px;padding-top:8px;font-size:13px}

/* ── TEXT ── */
p{margin:8px 0;text-align:justify}
.alert{padding:10px 14px;border-radius:var(--r);margin:10px 0;font-size:13px}
.alert-warn{background:#fffbeb;border:1px solid #fef3c7;color:#92400e}
.alert-info{background:#eff6ff;border:1px solid #dbeafe;color:#1d4ed8}
.alert-ok{background:#f0fdf4;border:1px solid #dcfce7;color:#15803d}
.alert-crit{background:var(--c-critical-bg);border:1px solid #fecaca;color:var(--c-critical);font-weight:600}

/* ── FOOTER ── */
.report-footer{
  text-align:center;font-size:11px;color:var(--c-muted);
  border-top:1px solid var(--c-border);padding:20px 56px;margin-top:0;
}

/* ── PRINT ── */
@media print{
  .sidebar{display:none}
  .main{margin-left:0}
  .content{padding:20px}
  .cover{background:#0f172a!important;-webkit-print-color-adjust:exact;print-color-adjust:exact;padding:40px}
  .cover h1{font-size:32px}
  .tcard,.info-table,.phases-table{box-shadow:none}
  .page-break{page-break-before:always}
  .dtable thead{background:#1e293b!important;-webkit-print-color-adjust:exact;print-color-adjust:exact}
  nav a,.sb-brand,.sb-footer,.ttoolbar .tsearch{display:none}
  details>summary::before{display:none}
  details>.det-body{display:block!important}
}
@page{size:A4;margin:1.5cm}
'@
}

function Get-ReportJS {
    return @'
(function(){
  // Search filter
  window.filterTable = function(inputId, tableId) {
    var f = document.getElementById(inputId).value.toUpperCase();
    var trs = document.getElementById(tableId).querySelectorAll('tbody tr');
    trs.forEach(function(tr){
      var txt = tr.textContent.toUpperCase();
      tr.style.display = txt.indexOf(f) > -1 ? '' : 'none';
    });
  };

  // Sidebar active link on scroll
  document.addEventListener('DOMContentLoaded', function(){
    var sections = document.querySelectorAll('[data-sec]');
    var links = document.querySelectorAll('nav a[href^="#"]');
    if(!sections.length || !links.length) return;
    var io = new IntersectionObserver(function(entries){
      entries.forEach(function(e){
        if(e.isIntersecting){
          var id = e.target.id;
          links.forEach(function(a){
            a.classList.toggle('active', a.getAttribute('href') === '#'+id);
          });
        }
      });
    },{rootMargin:'-20% 0px -70% 0px'});
    sections.forEach(function(s){ io.observe(s); });
  });
})();
'@
}

#endregion ===== ReportTemplates.ps1 =====

#region ===== ReportGenerator.ps1 =====
#region ============================================================
# MaPeriT.SW - GENERADOR DEL INFORME PERICIAL HTML
# ============================================================

function New-ForensicReport {
    param([string]$OutputPath)

    $reportPath = Join-Path $OutputPath $Script:OutputDirs.Report
    $reportFile = Join-Path $reportPath "InformePericial.html"
    $duration   = if ($Script:SessionInfo.EndTime) {
                      ($Script:SessionInfo.EndTime - $Script:SessionInfo.StartTime).ToString('hh\:mm\:ss')
                  } else { "En curso" }

    Write-ForensicLog -Phase "INFORME" -Action "GENERANDO_INFORME" -Target $reportFile
    $css = Get-ReportCSS
    $js  = Get-ReportJS
    $S   = $Script:ReportStrings

    # Campos que provienen de entrada de linea de comandos del perito (CaseNumber,
    # ExaminerName) o de datos del equipo analizado (ComputerName, Domain, OSVersion,
    # ScriptPath) y se interpolan en HTML: deben ir siempre codificados. El resto del
    # informe (tablas CSV/JSON, anomalias) ya usaba HtmlEncode de forma consistente;
    # estos campos de cabecera/portada/pie eran la unica excepcion.
    $caseNumberEnc   = [System.Web.HttpUtility]::HtmlEncode($Script:SessionInfo.CaseNumber)
    $examinerNameEnc = [System.Web.HttpUtility]::HtmlEncode($Script:SessionInfo.ExaminerName)
    $computerNameEnc = [System.Web.HttpUtility]::HtmlEncode($Script:SessionInfo.ComputerName)
    $domainEnc       = [System.Web.HttpUtility]::HtmlEncode($Script:SessionInfo.Domain)
    $osVersionEnc    = [System.Web.HttpUtility]::HtmlEncode($Script:SessionInfo.OSVersion)
    $osBuildEnc      = [System.Web.HttpUtility]::HtmlEncode($Script:SessionInfo.OSBuild)
    $scriptPathEnc   = [System.Web.HttpUtility]::HtmlEncode($Script:SessionInfo.ScriptPath)
    $outputPathEnc   = [System.Web.HttpUtility]::HtmlEncode($OutputPath)

    $html = [System.Text.StringBuilder]::new(600000)

    # ── Contar evidencias y anomalías ──────────────────────────────────────
    $totalFiles   = (Get-ChildItem (Join-Path $OutputPath "*") -Recurse -File -ErrorAction SilentlyContinue |
                      Where-Object { $_.DirectoryName -notlike "*Report*" }).Count
    $totalSizeMB  = [math]::Round(
        (Get-ChildItem (Join-Path $OutputPath "*") -Recurse -File -ErrorAction SilentlyContinue |
         Measure-Object -Property Length -Sum).Sum / 1MB, 2)
    $anomalyFile  = Join-Path $OutputPath "$($Script:OutputDirs.Analysis)\Anomalies.json"
    $anomalyCount = 0; $criticalCount = 0; $highCount = 0
    if (Test-Path $anomalyFile) {
        # Dos pasos (no @(... | ConvertFrom-Json)): en PowerShell 5.1 ConvertFrom-Json
        # emite un array JSON como UN objeto y @() lo anidaria. @($x) sobre la
        # variable ya asignada si da el recuento correcto, tambien con un unico
        # elemento (antes una sola anomalia contaba como 0).
        $aData = Get-Content $anomalyFile -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json -ErrorAction SilentlyContinue
        $aData = @($aData | Where-Object { $_ })
        if ($aData.Count -gt 0) {
            $anomalyCount  = $aData.Count
            $criticalCount = @($aData | Where-Object { $_.Severidad -eq $Script:AnomalySeverity.Critical }).Count
            $highCount     = @($aData | Where-Object { $_.Severidad -eq $Script:AnomalySeverity.High }).Count
        }
    }

    # ── HTML head ──────────────────────────────────────────────────────────
    [void]$html.AppendLine(@"
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Informe Pericial - Caso $caseNumberEnc</title>
<style>$css</style>
</head>
<body>
"@)

    # ── SIDEBAR ────────────────────────────────────────────────────────────
    [void]$html.AppendLine(@"
<nav class="sidebar">
  <div class="sb-brand">
    <div class="sb-brand-name">$($Script:TOOL_NAME)</div>
    <div class="sb-brand-sub">Peritacion Forense v$($Script:TOOL_VERSION)</div>
  </div>
  <div class="sb-case">
    <strong>Caso</strong>$caseNumberEnc
    <strong style="margin-top:6px">Perito</strong>$examinerNameEnc
    <strong style="margin-top:6px">Equipo</strong>$computerNameEnc
  </div>
  <nav>
    <div class="sb-label">Informe</div>
    <a href="#cover">Portada</a>
    <a href="#sec1">1. Resumen Ejecutivo</a>
    <a href="#sec2">2. Metodologia</a>
    <a href="#sec3">3. Cadena de Custodia</a>
    <a href="#sec4">4. Identificacion del Sistema</a>
    <div class="sb-label">Hallazgos</div>
    <a href="#sec5">5. Evidencias Recopiladas</a>
    <a href="#sec6">6. Hallazgos</a>
    <a href="#sec7">7. Linea Temporal</a>
    <a href="#sec8">8. Anomalias $(if($criticalCount -gt 0){"($criticalCount criticas)"})</a>
    <div class="sb-label">Cierre</div>
    <a href="#sec9">9. Conclusiones</a>
    <a href="#sec10">10. Anexos</a>
  </nav>
  <div class="sb-footer">
    $($Script:SessionInfo.StartTime.ToString('dd/MM/yyyy HH:mm'))<br>
    Duracion: $duration<br>
    $totalFiles ficheros &middot; $totalSizeMB MB
  </div>
</nav>
<div class="main">
"@)

    # ── PORTADA ────────────────────────────────────────────────────────────
    [void]$html.AppendLine(@"
<div id="cover" class="cover" data-sec>
  <div class="cover-badge">Informe Pericial Informatico</div>
  <h1>$($S.Title)</h1>
  <h2>$($S.Subtitle)</h2>
  <div class="cover-grid">
    <div class="cover-item"><label>Numero de Caso</label><span>$caseNumberEnc</span></div>
    <div class="cover-item"><label>Perito</label><span>$examinerNameEnc</span></div>
    <div class="cover-item"><label>Fecha de Analisis</label><span>$($Script:SessionInfo.StartTime.ToString('dd/MM/yyyy'))</span></div>
    <div class="cover-item"><label>Equipo Analizado</label><span>$computerNameEnc</span></div>
    <div class="cover-item"><label>Sistema Operativo</label><span>$osVersionEnc</span></div>
    <div class="cover-item"><label>Duracion</label><span>$duration</span></div>
    <div class="cover-item"><label>Evidencias</label><span>$($Script:SessionInfo.TotalEvidences)</span></div>
    <div class="cover-item"><label>Anomalias</label><span>$anomalyCount$(if($criticalCount -gt 0){" ($criticalCount criticas)"})</span></div>
  </div>
  <div class="cover-hash">
    SHA-256 herramienta: $($Script:SessionInfo.ScriptHash)
  </div>
  <div class="confidential">Documento Confidencial &mdash; Cadena de Custodia de Evidencias Digitales</div>
</div>

<div class="content">
"@)

    # ── SEC 1: RESUMEN EJECUTIVO ───────────────────────────────────────────
    [void]$html.AppendLine(@"
<div id="sec1" data-sec>
<div class="section-hdr"><div class="sec-num">1</div><h2>$($S.ExecutiveSummary)</h2></div>

<p>Se ha realizado un analisis forense informatico completo del equipo <strong>$computerNameEnc</strong>
(SO: $osVersionEnc, Build $osBuildEnc, Dominio: <strong>$domainEnc</strong>).</p>
<p>Analisis iniciado el <strong>$($Script:SessionInfo.StartTime.ToString('dd/MM/yyyy a las HH:mm:ss'))</strong>
y finalizado el <strong>$(if($Script:SessionInfo.EndTime){$Script:SessionInfo.EndTime.ToString('dd/MM/yyyy a las HH:mm:ss')}else{'En curso'})</strong>
&mdash; duracion total: <strong>$duration</strong>.</p>

<div class="stat-grid">
  <div class="stat-card ok">
    <div class="sv">$($Script:SessionInfo.TotalEvidences)</div>
    <div class="sl">Evidencias Recopiladas</div>
  </div>
  <div class="stat-card">
    <div class="sv">$totalFiles</div>
    <div class="sl">Ficheros Generados</div>
  </div>
  <div class="stat-card">
    <div class="sv">$totalSizeMB MB</div>
    <div class="sl">Volumen Total</div>
  </div>
  <div class="stat-card $(if($criticalCount -gt 0){'crit'}elseif($anomalyCount -gt 0){'warn'}else{'ok'})">
    <div class="sv">$anomalyCount</div>
    <div class="sl">Anomalias$(if($criticalCount -gt 0){" &bull; $criticalCount CRITICAS"}elseif($highCount -gt 0){" &bull; $highCount ALTAS"})</div>
  </div>
</div>

$(if ($Script:ChainOfCustodyStrictMode -and $Script:StrictCustodyFailures -and $Script:StrictCustodyFailures.Count -gt 0) {
@"
<div class="alert alert-crit">
  <strong>CHEQUEO FINAL DE INTEGRIDAD: FALLIDO</strong> (-ChainOfCustodyStrictMode) &mdash;
  $($Script:StrictCustodyFailures.Count) evidencia(s) quedaron con un hash SHA-256 no valido
  tras la copia. Revise las entradas marcadas <code>INVALIDO:</code> en
  <strong>Logs\ChainOfCustody.csv</strong> antes de dar la adquisicion por concluida.
</div>
"@
})
$(if ($Script:HivesNoDesmontadas -and $Script:HivesNoDesmontadas.Count -gt 0) {
@"
<div class="alert alert-crit">
  <strong>AVISO: EL EQUIPO ANALIZADO HA QUEDADO MODIFICADO</strong> &mdash;
  $($Script:HivesNoDesmontadas.Count) clave(s) de registro de usuario montadas temporalmente
  para la Fase 4 no se pudieron desmontar y siguen cargadas en <code>HKEY_USERS</code> del
  equipo analizado. Debe desmontarlas manualmente (<code>reg unload &lt;clave&gt;</code>)
  antes de devolver o reiniciar el equipo. Vea el log de ejecucion (Accion
  HIVE_DESMONTAR_FALLO_PERMANENTE) para el detalle de las claves afectadas.
</div>
"@
})

<p>Se ejecutaron las 10 fases de adquisicion siguiendo el orden de volatilidad RFC 3227.
<strong>$($Script:SessionInfo.PhasesCompleted.Count)</strong> fases se completaron satisfactoriamente$(
if($Script:SessionInfo.PhasesFailed.Count -gt 0){
    ", <strong>$($Script:SessionInfo.PhasesFailed.Count)</strong> fases con errores parciales (los datos se recopilaron igualmente)"
}).</p>
</div>
"@)

    # ── SEC 2: METODOLOGIA ─────────────────────────────────────────────────
    [void]$html.AppendLine(@"
<div id="sec2" data-sec>
<div class="section-hdr"><div class="sec-num">2</div><h2>$($S.Methodology)</h2></div>
<p>$($S.MethodologyText)</p>

<div class="sub-hdr">2.1 Herramienta Utilizada</div>
<table class="info-table">
  <tr><td>Herramienta</td><td><strong>$($Script:SessionInfo.ToolName) v$($Script:SessionInfo.ToolVersion)</strong></td></tr>
  <tr><td>Hash SHA-256</td><td style="font-family:monospace;font-size:12px;word-break:break-all">$($Script:SessionInfo.ScriptHash)</td></tr>
  <tr><td>PowerShell</td><td>$($Script:SessionInfo.PSVersion)</td></tr>
  <tr><td>Ruta del script</td><td style="word-break:break-all">$scriptPathEnc</td></tr>
  <tr><td>Normativa aplicada</td><td>RFC 3227 (IETF) &bull; NIST SP 800-86</td></tr>
</table>

<div class="sub-hdr">2.2 Fases de Adquisicion</div>
<table class="phases-table">
<thead><tr><th style="width:60px">Fase</th><th>Descripcion</th><th style="width:140px">Estado</th></tr></thead>
<tbody>
"@)

    for ($i = 1; $i -le 10; $i++) {
        $status = if ($i -in $Script:SessionInfo.PhasesCompleted) { '<span class="badge b-ok">Completada</span>' }
                  elseif ($i -in $Script:SessionInfo.PhasesFailed) { '<span class="badge b-err">Error parcial</span>' }
                  elseif ($i -in $Script:SessionInfo.PhasesSkipped) { '<span class="badge b-skip">Omitida</span>' }
                  else { '<span class="badge b-info">N/A</span>' }
        [void]$html.AppendLine("<tr><td>Fase $i</td><td>$($Script:PhaseNames[$i])</td><td>$status</td></tr>")
    }
    [void]$html.AppendLine("</tbody></table></div>")

    # ── SEC 3: CADENA DE CUSTODIA ──────────────────────────────────────────
    [void]$html.AppendLine(@"
<div id="sec3" data-sec>
<div class="section-hdr"><div class="sec-num">3</div><h2>$($S.ChainOfCustody)</h2></div>
<p>La cadena de custodia completa esta documentada en <strong>CadenaCustodia.html</strong>.
Se verificaron con SHA-256 un total de <strong>$($Script:SessionInfo.TotalEvidences)</strong> evidencias en el momento de su adquisicion.</p>
<div class="alert alert-info">El manifiesto completo de hashes se encuentra en <strong>Logs\HashManifest.csv</strong>.
Para verificar la integridad en cualquier momento: <code>.\MaPeriT.SW.ps1 -VerifyPath "$outputPathEnc"</code></div>
</div>
"@)

    # ── SEC 4: IDENTIFICACION DEL SISTEMA ─────────────────────────────────
    [void]$html.AppendLine(@"
<div id="sec4" data-sec class="page-break">
<div class="section-hdr"><div class="sec-num">4</div><h2>$($S.SystemID)</h2></div>
"@)
    Add-ReportSection -Html $html -OutputPath $OutputPath -DataFile "$($Script:OutputDirs.SystemInfo)\OSInfo.json"       -Title "4.1 Sistema Operativo"        -Type "json-table"
    Add-ReportSection -Html $html -OutputPath $OutputPath -DataFile "$($Script:OutputDirs.SystemInfo)\HardwareInventory.json" -Title "4.2 Hardware"            -Type "json-summary"
    Add-ReportSection -Html $html -OutputPath $OutputPath -DataFile "$($Script:OutputDirs.SystemInfo)\LocalUsers.csv"    -Title "4.3 Usuarios Locales"         -Type "csv" -MaxRows 30
    Add-ReportSection -Html $html -OutputPath $OutputPath -DataFile "$($Script:OutputDirs.SystemInfo)\IPAddresses.csv"   -Title "4.4 Configuracion de Red"     -Type "csv" -MaxRows 20
    Add-ReportSection -Html $html -OutputPath $OutputPath -DataFile "$($Script:OutputDirs.SystemInfo)\BitLocker.json"    -Title "4.5 Estado BitLocker"         -Type "json-table"
    [void]$html.AppendLine("</div>")

    # ── SEC 5: EVIDENCIAS RECOPILADAS ──────────────────────────────────────
    [void]$html.AppendLine(@"
<div id="sec5" data-sec class="page-break">
<div class="section-hdr"><div class="sec-num">5</div><h2>$($S.CollectedEvidence)</h2></div>
<table class="phases-table">
<thead><tr><th>Directorio</th><th style="width:100px;text-align:right">Ficheros</th><th style="width:100px;text-align:right">Tamano</th></tr></thead>
<tbody>
"@)
    foreach ($d in ($Script:OutputDirs.GetEnumerator() | Sort-Object Value)) {
        $dp    = Join-Path $OutputPath $d.Value
        if (-not (Test-Path $dp)) { continue }
        $files = Get-ChildItem $dp -Recurse -File -ErrorAction SilentlyContinue
        $mb    = [math]::Round(($files | Measure-Object -Property Length -Sum).Sum / 1MB, 2)
        [void]$html.AppendLine("<tr><td><code>$($d.Value)/</code></td><td style='text-align:right'>$($files.Count)</td><td style='text-align:right'>$mb MB</td></tr>")
    }
    [void]$html.AppendLine("</tbody></table></div>")

    # ── SEC 6: HALLAZGOS ───────────────────────────────────────────────────
    [void]$html.AppendLine(@"
<div id="sec6" data-sec class="page-break">
<div class="section-hdr"><div class="sec-num">6</div><h2>$($S.Findings)</h2></div>
"@)
    Add-ReportSection -Html $html -OutputPath $OutputPath -DataFile "$($Script:OutputDirs.EventLogs)\Analysis\SecurityLogon.csv"       -Title "6.1 Inicios de Sesion"              -Type "csv" -MaxRows 50
    Add-ReportSection -Html $html -OutputPath $OutputPath -DataFile "$($Script:OutputDirs.EventLogs)\Analysis\SecurityFailedLogon.csv" -Title "6.2 Intentos Fallidos de Sesion"    -Type "csv" -MaxRows 50
    Add-ReportSection -Html $html -OutputPath $OutputPath -DataFile "$($Script:OutputDirs.Volatile)\NetworkConnections_TCP.csv"        -Title "6.3 Conexiones de Red (TCP)"        -Type "csv" -MaxRows 50
    Add-ReportSection -Html $html -OutputPath $OutputPath -DataFile "$($Script:OutputDirs.SystemInfo)\InstalledSoftware.csv"           -Title "6.4 Software Instalado"             -Type "csv" -MaxRows 100
    Add-ReportSection -Html $html -OutputPath $OutputPath -DataFile "$($Script:OutputDirs.USB)\USBDevices.csv"                         -Title "6.5 Dispositivos USB"               -Type "csv"
    Add-ReportSection -Html $html -OutputPath $OutputPath -DataFile "$($Script:OutputDirs.Browsers)\BrowserSummary.csv"               -Title "6.6 Navegadores Web"                -Type "csv"
    Add-ReportSection -Html $html -OutputPath $OutputPath -DataFile "$($Script:OutputDirs.Communications)\OutlookFiles.csv"            -Title "6.7 Ficheros de Correo"             -Type "csv"
    Add-ReportSection -Html $html -OutputPath $OutputPath -DataFile "$($Script:OutputDirs.CloudStorage)\CloudStorageSummary.csv"       -Title "6.8 Almacenamiento en la Nube"      -Type "csv"
    Add-ReportSection -Html $html -OutputPath $OutputPath -DataFile "$($Script:OutputDirs.Persistence)\AutorunsConsolidated.csv"       -Title "6.9 Mecanismos de Persistencia"     -Type "csv" -MaxRows 50
    Add-ReportSection -Html $html -OutputPath $OutputPath -DataFile "$($Script:OutputDirs.Registry)\ExtractedValues\UserAssist.csv"    -Title "6.10 Programas Ejecutados (UserAssist)" -Type "csv" -MaxRows 50
    Add-ReportSection -Html $html -OutputPath $OutputPath -DataFile "$($Script:OutputDirs.Registry)\ExtractedValues\BAM_DAM.csv"       -Title "6.11 Actividad BAM/DAM"             -Type "csv" -MaxRows 50
    Add-ReportSection -Html $html -OutputPath $OutputPath -DataFile "$($Script:OutputDirs.Registry)\ExtractedValues\RDPHistory.csv"    -Title "6.12 Historial RDP"                 -Type "csv"
    Add-ReportSection -Html $html -OutputPath $OutputPath -DataFile "$($Script:OutputDirs.Persistence)\WMIPersistence.csv"            -Title "6.13 Persistencia WMI"              -Type "csv"
    [void]$html.AppendLine("</div>")

    # ── SEC 7: LINEA TEMPORAL ──────────────────────────────────────────────
    [void]$html.AppendLine(@"
<div id="sec7" data-sec class="page-break">
<div class="section-hdr"><div class="sec-num">7</div><h2>$($S.Timeline)</h2></div>
<p>Timeline unificada de multiples fuentes (logs de eventos, registro, prefetch, papelera, USB, etc.) en orden cronologico.</p>
"@)
    Add-ReportSection -Html $html -OutputPath $OutputPath -DataFile "$($Script:OutputDirs.Analysis)\Timeline_Last7Days.csv" -Title "7.1 Ultimos 7 Dias" -Type "csv" -MaxRows 200
    [void]$html.AppendLine("<p class='alert alert-info'>Timeline completa: <strong>Analysis\Timeline.csv</strong></p></div>")

    # ── SEC 8: ANOMALIAS ───────────────────────────────────────────────────
    [void]$html.AppendLine(@"
<div id="sec8" data-sec class="page-break">
<div class="section-hdr"><div class="sec-num">8</div><h2>$($S.Anomalies)</h2></div>
"@)
    if (Test-Path $anomalyFile) {
        $anomalies = Get-Content $anomalyFile -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json -ErrorAction SilentlyContinue
        $anomalies = @($anomalies | Where-Object { $_ })
        if ($anomalies.Count -gt 0) {
            $sorted = $anomalies | Sort-Object { switch($_.Severidad){'CRITICA'{0}'ALTA'{1}'MEDIA'{2}'BAJA'{3}default{4}} }
            foreach ($a in $sorted) {
                $sevClass = switch ($a.Severidad) {'CRITICA'{'sc'}'ALTA'{'sh'}'MEDIA'{'sm'}'BAJA'{'sl'}default{''}}
                $titleEnc = [System.Web.HttpUtility]::HtmlEncode($a.Titulo)
                $descEnc  = [System.Web.HttpUtility]::HtmlEncode($a.Descripcion)
                $recEnc   = [System.Web.HttpUtility]::HtmlEncode($a.Recomendacion)
                $catEnc   = [System.Web.HttpUtility]::HtmlEncode($a.Categoria)
                [void]$html.AppendLine(@"
<div class="anom-card $sevClass">
  <div class="anom-hdr">
    <span class="sev $($a.Severidad)">$($a.Severidad)</span>
    <span class="anom-id">$($a.ID)</span>
    <span class="anom-title">$titleEnc</span>
  </div>
  <div class="anom-body">
    <p><strong>Categoria:</strong> $catEnc</p>
    <p>$descEnc</p>
    <p><strong>Recomendacion:</strong> $recEnc</p>
  </div>
</div>
"@)
            }
        } else {
            [void]$html.AppendLine("<div class='alert alert-ok'>No se detectaron anomalias significativas.</div>")
        }
    } else {
        [void]$html.AppendLine("<p>Fichero de anomalias no encontrado.</p>")
    }
    [void]$html.AppendLine("</div>")

    # ── SEC 9: CONCLUSIONES ────────────────────────────────────────────────
    $dateStr = "$($Script:SessionInfo.StartTime.ToString('dd')) de $($Script:SessionInfo.StartTime.ToString('MMMM')) de $($Script:SessionInfo.StartTime.ToString('yyyy'))"
    [void]$html.AppendLine(@"
<div id="sec9" data-sec class="page-break">
<div class="section-hdr"><div class="sec-num">9</div><h2>$($S.Conclusions)</h2></div>
<p>Tras la realizacion del analisis forense informatico del equipo <strong>$computerNameEnc</strong>,
se han recopilado y verificado un total de <strong>$($Script:SessionInfo.TotalEvidences)</strong> evidencias digitales,
generando <strong>$totalFiles</strong> ficheros con un volumen total de <strong>$totalSizeMB MB</strong>.</p>

<p>El motor de deteccion automatica ha identificado <strong>$anomalyCount anomalias</strong>$(
if($criticalCount -gt 0){
    ", de las cuales <strong>$criticalCount son de severidad CRITICA</strong> y requieren atencion inmediata"
}elseif($highCount -gt 0){
    ", de las cuales <strong>$highCount son de severidad ALTA</strong>"
}).</p>

<p>Todas las evidencias han sido adquiridas siguiendo las directrices de la RFC 3227 y NIST SP 800-86,
manteniendo la cadena de custodia documentada y verificada mediante hashes SHA-256.
La integridad puede ser verificada en cualquier momento mediante el manifiesto incluido.</p>

<p>El presente informe se emite a los efectos oportunos, quedando a disposicion del solicitante
para cuantas aclaraciones sean necesarias.</p>

<div class="sig-grid">
  <div class="sig-item">
    <div class="sig-line">En _______________________, a $dateStr<br><br>$examinerNameEnc<br><em>Perito Informatico</em></div>
  </div>
  <div class="sig-item">
    <div class="sig-line">Recibido: ___________________<br><br>&nbsp;<br><em>Firma y sello</em></div>
  </div>
</div>
</div>
"@)

    # ── SEC 10: ANEXOS ─────────────────────────────────────────────────────
    [void]$html.AppendLine(@"
<div id="sec10" data-sec class="page-break">
<div class="section-hdr"><div class="sec-num">10</div><h2>$($S.Appendices)</h2></div>
"@)
    Add-ReportSection -Html $html -OutputPath $OutputPath -DataFile "$($Script:OutputDirs.Volatile)\Processes.csv"                       -Title "Anexo A: Procesos en Ejecucion"          -Type "csv" -MaxRows 200 -Collapsible
    Add-ReportSection -Html $html -OutputPath $OutputPath -DataFile "$($Script:OutputDirs.SystemInfo)\Services.csv"                       -Title "Anexo B: Servicios de Windows"           -Type "csv" -MaxRows 200 -Collapsible
    Add-ReportSection -Html $html -OutputPath $OutputPath -DataFile "$($Script:OutputDirs.EventLogs)\Analysis\SystemShutdowns.csv"        -Title "Anexo C: Arranques y Apagados"           -Type "csv" -MaxRows 100 -Collapsible
    Add-ReportSection -Html $html -OutputPath $OutputPath -DataFile "$($Script:OutputDirs.EventLogs)\Analysis\PowerShellScripts.csv"      -Title "Anexo D: Scripts PowerShell Ejecutados"  -Type "csv" -MaxRows 100 -Collapsible
    Add-ReportSection -Html $html -OutputPath $OutputPath -DataFile "$($Script:OutputDirs.EventLogs)\Analysis\DefenderEvents.csv"         -Title "Anexo E: Eventos Windows Defender"       -Type "csv" -MaxRows 100 -Collapsible
    Add-ReportSection -Html $html -OutputPath $OutputPath -DataFile "$($Script:OutputDirs.Registry)\ExtractedValues\USBSTORDevices.csv"   -Title "Anexo F: Historial USB (USBSTOR)"        -Type "csv" -MaxRows 100 -Collapsible
    Add-ReportSection -Html $html -OutputPath $OutputPath -DataFile "$($Script:OutputDirs.Persistence)\COMHijacking.csv"                  -Title "Anexo G: Entradas COM en HKCU"          -Type "csv" -MaxRows 100 -Collapsible
    [void]$html.AppendLine("</div>")

    # ── FOOTER ─────────────────────────────────────────────────────────────
    [void]$html.AppendLine(@"
</div><!-- /content -->

<div class="report-footer">
  Informe generado por <strong>$($Script:TOOL_FULL) v$($Script:TOOL_VERSION)</strong><br>
  Caso: $caseNumberEnc &bull; Perito: $examinerNameEnc &bull; Fecha: $(Get-Date -Format 'dd/MM/yyyy HH:mm')<br>
  Hash herramienta: <code style="font-size:10px">$($Script:SessionInfo.ScriptHash)</code>
</div>

</div><!-- /main -->
<script>$js</script>
</body>
</html>
"@)

    $html.ToString() | Out-File -FilePath $reportFile -Encoding UTF8
    Write-ForensicLog -Phase "INFORME" -Action "INFORME_GENERADO" -Target $reportFile -Result "OK"
}

# ── Helper: sección de datos ───────────────────────────────────────────────
function Add-ReportSection {
    param(
        [System.Text.StringBuilder]$Html,
        [string]$OutputPath,
        [string]$DataFile,
        [string]$Title,
        [string]$Type    = "csv",
        [int]$MaxRows    = 100,
        [switch]$Collapsible
    )

    $filePath = Join-Path $OutputPath $DataFile
    $safeId   = "t_" + ($Title -replace '[^a-zA-Z0-9]','_')

    if ($Collapsible) {
        [void]$Html.AppendLine("<details><summary>$([System.Web.HttpUtility]::HtmlEncode($Title))</summary><div class='det-body'>")
    } else {
        [void]$Html.AppendLine("<div class='sub-hdr'>$([System.Web.HttpUtility]::HtmlEncode($Title))</div>")
    }

    if (-not (Test-Path $filePath)) {
        [void]$Html.AppendLine("<p><em style='color:#94a3b8'>Sin datos disponibles.</em></p>")
        if ($Collapsible) { [void]$Html.AppendLine("</div></details>") }
        return
    }

    try {
        switch ($Type) {

            "csv" {
                $data = @(Import-Csv $filePath -ErrorAction Stop | Select-Object -First $MaxRows)
                if ($data.Count -eq 0) {
                    [void]$Html.AppendLine("<p><em style='color:#94a3b8'>Sin registros.</em></p>")
                } else {
                    $cols    = $data[0].PSObject.Properties.Name
                    $totalInFile = @(Import-Csv $filePath -ErrorAction SilentlyContinue).Count
                    $showSearch  = $totalInFile -gt 15
                    $searchId    = $safeId + "_s"
                    $tableId     = $safeId + "_tbl"

                    [void]$Html.AppendLine("<div class='tcard'>")
                    [void]$Html.AppendLine("<div class='ttoolbar'>")
                    [void]$Html.AppendLine("  <span class='ttoolbar-label'>$($data.Count) / $totalInFile registros</span>")
                    if ($showSearch) {
                        [void]$Html.AppendLine("  <input class='tsearch' id='$searchId' type='text' placeholder='Filtrar...' oninput=""filterTable('$searchId','$tableId')"">")
                    }
                    [void]$Html.AppendLine("</div>")
                    [void]$Html.AppendLine("<div class='twrap'>")
                    [void]$Html.AppendLine("<table class='dtable' id='$tableId'><thead><tr>")
                    foreach ($col in $cols) {
                        [void]$Html.AppendLine("<th>$([System.Web.HttpUtility]::HtmlEncode($col))</th>")
                    }
                    [void]$Html.AppendLine("</tr></thead><tbody>")

                    # Detectar columnas que merecen clase especial
                    $monoCols = @('Hash','SHA256','SHA-256','MD5','HMAC','Hash_SHA256')
                    $wrapCols = @('Path','Ruta','Descripcion','Description','CommandLine','Actions','Command')

                    foreach ($row in $data) {
                        [void]$Html.Append("<tr>")
                        foreach ($col in $cols) {
                            $raw = if ($null -eq $row.$col) { "" } else { $row.$col.ToString() }
                            $enc = [System.Web.HttpUtility]::HtmlEncode($raw)
                            $cls = ""
                            if ($monoCols -contains $col) { $cls = " class='mono'" }
                            elseif ($wrapCols -contains $col) { $cls = " class='w'" }
                            $titleAttr = if ($raw.Length -gt 60 -and $cls -notlike "*w*") { " title='$([System.Web.HttpUtility]::HtmlEncode($raw))'" } else { "" }
                            if ($enc.Length -gt 80 -and $cls -notlike "*w*") { $enc = $enc.Substring(0,77) + "&#8230;" }
                            [void]$Html.Append("<td$cls$titleAttr>$enc</td>")
                        }
                        [void]$Html.AppendLine("</tr>")
                    }
                    [void]$Html.AppendLine("</tbody></table>")
                    [void]$Html.AppendLine("</div>")
                    if ($totalInFile -gt $MaxRows) {
                        [void]$Html.AppendLine("<div class='tnote'>Mostrando $MaxRows de $totalInFile registros. Fichero completo: $DataFile</div>")
                    }
                    [void]$Html.AppendLine("</div>")
                }
            }

            "json-table" {
                $data = Get-Content $filePath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
                [void]$Html.AppendLine("<table class='info-table'>")
                $data.PSObject.Properties | ForEach-Object {
                    $val = if ($_.Value -is [System.Array] -or $_.Value -is [PSCustomObject]) {
                        ($_.Value | ConvertTo-Json -Depth 2 -Compress)
                    } else { $_.Value }
                    [void]$Html.AppendLine("<tr><td>$([System.Web.HttpUtility]::HtmlEncode($_.Name))</td><td>$([System.Web.HttpUtility]::HtmlEncode($val.ToString()))</td></tr>")
                }
                [void]$Html.AppendLine("</table>")
            }

            "json-summary" {
                $data = Get-Content $filePath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
                [void]$Html.AppendLine("<table class='info-table'>")
                $data.PSObject.Properties |
                    Where-Object { $_.Value -isnot [System.Array] -and $_.Value -isnot [PSCustomObject] } |
                    ForEach-Object {
                        [void]$Html.AppendLine("<tr><td>$([System.Web.HttpUtility]::HtmlEncode($_.Name))</td><td>$([System.Web.HttpUtility]::HtmlEncode($_.Value.ToString()))</td></tr>")
                    }
                [void]$Html.AppendLine("</table>")
            }
        }
    } catch {
        [void]$Html.AppendLine("<div class='alert alert-warn'>Error al leer ${DataFile}: $([System.Web.HttpUtility]::HtmlEncode($_.Exception.Message))</div>")
    }

    if ($Collapsible) { [void]$Html.AppendLine("</div></details>") }
}

# Alias de compatibilidad (por si algo llama Add-ReportDataSection)
function Add-ReportDataSection {
    param(
        [System.Text.StringBuilder]$Html,
        [string]$OutputPath,
        [string]$DataFile,
        [string]$Title,
        [string]$Type    = "csv",
        [int]$MaxRows    = 100,
        [string]$TableClass = "",
        [switch]$Collapsible
    )
    Add-ReportSection -Html $Html -OutputPath $OutputPath -DataFile $DataFile -Title $Title -Type $Type -MaxRows $MaxRows -Collapsible:$Collapsible
}

#endregion ===== ReportGenerator.ps1 =====

#region ===== Main.ps1 =====
#region ============================================================
# MaPeriT.SW - PUNTO DE ENTRADA (EJECUCION)
# Este fichero se ejecuta despues de cargar todos los modulos
# ============================================================

# --- Punto de entrada ---
$ErrorActionPreference = "Continue"
$ProgressPreference = "SilentlyContinue"

# La carga de System.Web.HttpUtility (usado por los generadores de informe)
# ahora la garantiza ..\Common\HtmlEncoding.ps1, incluido antes en la
# compilacion - ver ese fichero para el bug confirmado que motivo el cambio
# (la carga podia fallar en silencio, dejando todo HtmlEncode roto).

# --- Modo Dry-Run (preflight de solo lectura, sin CaseNumber/ExaminerName) ---
if ($DryRun) {
    Invoke-DryRunPreflight -ScriptPath $PSCommandPath
    exit 0
}

# --- Modo Self-Test (prueba de la cadena de integridad, sin CaseNumber/ExaminerName) ---
if ($SelfTest) {
    $selfTestOk = Invoke-SelfTest
    if ($selfTestOk) { exit 0 } else { exit 1 }
}

# --- Modo verificacion ---
if (-not [string]::IsNullOrEmpty($VerifyPath)) {
    Write-Host ""
    Write-Host "  MaPeriT.SW - MODO VERIFICACION DE INTEGRIDAD" -ForegroundColor Cyan
    Write-Host "  ============================================" -ForegroundColor Cyan
    Write-Host ""

    if (-not (Test-Path $VerifyPath)) {
        Write-Host "  [-] Directorio no encontrado: $VerifyPath" -ForegroundColor Red
        exit 1
    }

    $manifestPath = Get-ChildItem -Path $VerifyPath -Recurse -Filter "HashManifest.csv" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $manifestPath) {
        Write-Host "  [-] HashManifest.csv no encontrado en $VerifyPath" -ForegroundColor Red
        exit 1
    }

    Write-Host "  [*] Directorio:  $VerifyPath" -ForegroundColor White
    Write-Host "  [*] Manifiesto:  $($manifestPath.FullName)" -ForegroundColor White
    Write-Host ""

    # Verificacion delegada a Test-HashManifest (Core\Hashing.ps1) - unica
    # implementacion de esta logica en todo el proyecto, ver comentario en esa funcion.
    $verifyResult = Test-HashManifest -ManifestPath $manifestPath.FullName -EvidenceRootPath $VerifyPath -ShowProgress

    Write-Host ""
    Write-Host "  =============================================" -ForegroundColor Cyan
    Write-Host "  RESULTADO DE VERIFICACION" -ForegroundColor Cyan
    Write-Host "  =============================================" -ForegroundColor Cyan
    Write-Host "  Total en manifiesto:  $($verifyResult.Total)" -ForegroundColor White
    Write-Host "  Verificados OK:       $($verifyResult.Verified)" -ForegroundColor Green
    if ($verifyResult.Missing -gt 0) {
        Write-Host "  Ficheros faltantes:   $($verifyResult.Missing)" -ForegroundColor Red
    }
    if ($verifyResult.Mismatches -gt 0) {
        Write-Host "  Hashes NO coinciden:  $($verifyResult.Mismatches)" -ForegroundColor Red
    }
    if ($verifyResult.Success) {
        Write-Host "  INTEGRIDAD VERIFICADA: Todas las evidencias intactas" -ForegroundColor Green
    } else {
        Write-Host "  INTEGRIDAD COMPROMETIDA: Se detectaron discrepancias" -ForegroundColor Red
    }
    Write-Host "  =============================================" -ForegroundColor Cyan
    Write-Host ""
    # Codigo de salida 0 = integridad verificada, 1 = discrepancias (antes era
    # siempre 0, y un script de automatizacion no podia detectar el fallo).
    if ($verifyResult.Success) { exit 0 } else { exit 1 }
}

# --- Validar parametros obligatorios para modo adquisicion ---
if ([string]::IsNullOrEmpty($CaseNumber) -or [string]::IsNullOrEmpty($ExaminerName)) {
    Write-Host "  [-] ERROR: -CaseNumber y -ExaminerName son obligatorios para adquisicion." -ForegroundColor Red
    Write-Host "  Use -VerifyPath para verificar una adquisicion previa." -ForegroundColor Yellow
    exit 1
}

# Resolver OutputPath por defecto
if ([string]::IsNullOrEmpty($OutputPath)) {
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $OutputPath = Join-Path (Get-Location) "MaPeriT_${CaseNumber}_${timestamp}"
}

# Ruta de salida siempre absoluta y en forma larga. Con una ruta relativa
# (".\Caso1") o en formato corto 8.3 ("C:\Users\NOMBRE~1\...") los ficheros
# se enumeran con su ruta larga, que no comparte prefijo con OutputPath, y el
# manifiesto de hashes se generaba con TODAS las rutas relativas vacias:
# la verificacion posterior con -VerifyPath era imposible.
$OutputPath = [System.IO.Path]::GetFullPath([System.IO.Path]::Combine((Get-Location -PSProvider FileSystem).ProviderPath, $OutputPath))
if (-not (Test-Path -LiteralPath $OutputPath)) { New-Item -ItemType Directory -Path $OutputPath -Force | Out-Null }
$OutputPath = (Get-Item -LiteralPath $OutputPath).FullName.TrimEnd('\')

# --- Verificacion de prerequisitos ---
$issues = Test-Prerequisites
if ($issues.Count -gt 0) {
    Write-Host ""
    Write-Host "  [!] ERRORES DE PREREQUISITOS:" -ForegroundColor Red
    foreach ($issue in $issues) {
        Write-Host "      - $issue" -ForegroundColor Red
    }
    Write-Host ""
    exit 1
}

# --- Aplicar opciones de configuracion en tiempo de ejecucion ---
if ($MaxEventsPerQuery -gt 0) {
    Write-Host "  [*] Limite de eventos por consulta sobrescrito a $MaxEventsPerQuery (por defecto $($Script:Defaults.MaxEventsPerQuery))" -ForegroundColor Yellow
    $Script:Defaults.MaxEventsPerQuery = $MaxEventsPerQuery
}

if ($ExcludePaths -and $ExcludePaths.Count -gt 0) {
    $Script:ExcludePathPatterns = $ExcludePaths
    Write-Host "  [*] Exclusion de rutas activa ($($ExcludePaths.Count) patron(es)):" -ForegroundColor Yellow
    $ExcludePaths | ForEach-Object { Write-Host "      - $_" -ForegroundColor Yellow }
} else {
    $Script:ExcludePathPatterns = @()
}

if ($ChainOfCustodyStrictMode) {
    $Script:ChainOfCustodyStrictMode = $true
    Write-Host "  [*] Modo estricto de cadena de custodia activo (-ChainOfCustodyStrictMode)" -ForegroundColor Yellow
} else {
    $Script:ChainOfCustodyStrictMode = $false
}

# --- Inicializar helpers C# ---
$csharpReady = Initialize-CSharpHelpers

# --- Crear estructura de directorios ---
Initialize-OutputStructure -OutputPath $OutputPath | Out-Null

# --- Inicializar logging ---
Initialize-Logging -LogsPath (Join-Path $OutputPath $Script:OutputDirs.Logs)

# --- Inicializar sesion ---
$session = Initialize-ForensicSession `
    -CaseNumber $CaseNumber `
    -ExaminerName $ExaminerName `
    -OutputPath $OutputPath `
    -ScriptPath $PSCommandPath `
    -EvidenceDescription $EvidenceDescription `
    -HMACKey $HMACKey

# --- Mostrar banner ---
Show-SessionBanner

# --- Crear VSS Snapshot ---
Write-Host ""
Write-Host "  === PREPARACION ===" -ForegroundColor Cyan

# -NetworkIsolationCheck: se documenta lo antes posible (dato altamente volatil, RFC 3227)
# y antes de crear el VSS para no retrasar la captura de este estado.
if ($NetworkIsolationCheck) {
    Write-Host "  [*] Comprobando estado de red del equipo (-NetworkIsolationCheck)..." -ForegroundColor Yellow
    $netIsolationState = Test-NetworkIsolationState
    if ($netIsolationState) {
        $netCheckPath = Join-Path $OutputPath "$($Script:OutputDirs.Volatile)\NetworkIsolationCheck.json"
        Export-CollectionToJSON -Data $netIsolationState -OutputPath $netCheckPath -Phase "FASE_1" -Description "Estado de aislamiento de red al inicio de la adquisicion"
    }
}

$vssReady = New-ForensicVSSSnapshot -Volume "C:\"
if (-not $vssReady) {
    Write-Host "  [!] No se pudo crear VSS Snapshot. Algunos ficheros bloqueados no podran copiarse." -ForegroundColor Yellow
}

# --- Ejecutar fases ---
$totalCollected = 0
$phaseResults = @{}

for ($phase = 1; $phase -le 10; $phase++) {
    Write-ProgressBanner -CurrentPhase $phase -TotalPhases 10 -PhaseName $Script:PhaseNames[$phase]

    if ($phase -in $PhasesToSkip) {
        Write-Host "`n  [~] Fase $phase ($($Script:PhaseNames[$phase])) OMITIDA por configuracion" -ForegroundColor Yellow
        $Script:SessionInfo.PhasesSkipped += $phase
        Write-ForensicLog -Phase "FASE_$phase" -Action "FASE_OMITIDA" -Target $Script:PhaseNames[$phase] -Result "SKIP"
        continue
    }

    try {
        $phaseCollected = 0

        $phaseRaw = switch ($phase) {
            1  { Invoke-Phase1-VolatileCollection -OutputPath $OutputPath }
            2  { Invoke-Phase2-SystemInfoCollection -OutputPath $OutputPath }
            3  { Invoke-Phase3-FilesystemCollection -OutputPath $OutputPath }
            4  { Invoke-Phase4-RegistryCollection -OutputPath $OutputPath }
            5  { Invoke-Phase5-EventLogCollection -OutputPath $OutputPath }
            6  { Invoke-Phase6-BrowserCollection -OutputPath $OutputPath }
            7  { Invoke-Phase7-CommunicationsCollection -OutputPath $OutputPath }
            8  { Invoke-Phase8-CloudStorageCollection -OutputPath $OutputPath }
            9  { Invoke-Phase9-USBCollection -OutputPath $OutputPath }
            10 { Invoke-Phase10-PersistenceCollection -OutputPath $OutputPath }
        }
        $phaseCollected = if ($phaseRaw -is [array]) { [int]($phaseRaw[-1]) } else { [int]$phaseRaw }

        $totalCollected += $phaseCollected
        $phaseResults[$phase] = $phaseCollected
        $Script:SessionInfo.PhasesCompleted += $phase

        if ($Script:CurrentPhaseStart) {
            $phaseDuration = ((Get-Date) - $Script:CurrentPhaseStart).TotalMinutes
            if ($phaseDuration -gt $Script:Defaults.PhaseTimeoutMinutes) {
                Write-ForensicLog -Phase "FASE_$phase" -Action "FASE_TIMEOUT_SUPERADO" -Target "Duracion: $([math]::Round($phaseDuration,1)) min (limite: $($Script:Defaults.PhaseTimeoutMinutes) min)" -Result "WARNING"
                $Script:SessionInfo.TotalWarnings++
            }
        }

    } catch {
        Write-Host "`n  [-] ERROR EN FASE ${phase}: $($_.Exception.Message)" -ForegroundColor Red
        Write-ForensicLog -Phase "FASE_$phase" -Action "FASE_ERROR_GENERAL" -Target $_.Exception.Message -Result "ERROR"
        $Script:SessionInfo.PhasesFailed += $phase
        $Script:SessionInfo.TotalErrors++
    }
}

# --- Analisis ---
Write-Host ""
Write-Host "  === ANALISIS ===" -ForegroundColor Cyan

Write-PhaseHeader -PhaseNumber 11 -PhaseName "Analisis y Deteccion"

try {
    $timelineCount = Build-ForensicTimeline -OutputPath $OutputPath
    Write-ForensicLog -Phase "ANALISIS" -Action "TIMELINE" -Target "$timelineCount eventos en timeline" -Result "OK"
} catch {
    Write-ForensicLog -Phase "ANALISIS" -Action "TIMELINE_ERROR" -Target $_.Exception.Message -Result "ERROR"
}

try {
    $iocCount = Export-ForensicIOCs -OutputPath $OutputPath
    Write-ForensicLog -Phase "ANALISIS" -Action "IOCS" -Target "$iocCount indicadores extraidos" -Result "OK"
} catch {
    Write-ForensicLog -Phase "ANALISIS" -Action "IOCS_ERROR" -Target $_.Exception.Message -Result "ERROR"
}

$anomalyCount = 0
try {
    $anomalyCount = Find-ForensicAnomalies -OutputPath $OutputPath
    Write-ForensicLog -Phase "ANALISIS" -Action "ANOMALIAS" -Target "$anomalyCount anomalias detectadas" -Result "OK"
} catch {
    Write-ForensicLog -Phase "ANALISIS" -Action "ANOMALIAS_ERROR" -Target $_.Exception.Message -Result "ERROR"
}

# --- Integridad ---
Write-Host ""
Write-Host "  === VERIFICACION DE INTEGRIDAD ===" -ForegroundColor Cyan

$manifestPath = Join-Path $OutputPath "$($Script:OutputDirs.Logs)\HashManifest.csv"
$hashedCount = New-HashManifest -EvidenceRootPath $OutputPath -ManifestPath $manifestPath

if (-not [string]::IsNullOrEmpty($HMACKey)) {
    $hmacPath = Join-Path $OutputPath "$($Script:OutputDirs.Logs)\ManifestHMAC.txt"
    New-EvidenceHMAC -ManifestPath $manifestPath -Key $HMACKey -OutputPath $hmacPath
}

# --- Finalizar sesion ---
Complete-ForensicSession

# --- Generar informes ---
Write-Host ""
Write-Host "  === GENERACION DE INFORMES ===" -ForegroundColor Cyan

try {
    New-ChainOfCustodyReport -OutputPath $OutputPath
    Write-Host "  [+] Cadena de Custodia generada" -ForegroundColor Green
} catch {
    Write-Host "  [-] Error al generar Cadena de Custodia: $($_.Exception.Message)" -ForegroundColor Red
}

try {
    New-ForensicReport -OutputPath $OutputPath
    Write-Host "  [+] Informe Pericial generado" -ForegroundColor Green
} catch {
    Write-Host "  [-] Error al generar Informe: $($_.Exception.Message)" -ForegroundColor Red
}

# --- Limpieza ---
Write-Host ""
Write-Host "  === LIMPIEZA ===" -ForegroundColor Cyan
Remove-ForensicVSSSnapshot

if ($Script:HivesNoDesmontadas -and $Script:HivesNoDesmontadas.Count -gt 0) {
    Write-Host ""
    Write-Host "  [!!!] AVISO CRITICO: EL EQUIPO ANALIZADO HA QUEDADO MODIFICADO" -ForegroundColor Red
    Write-Host "  Las siguientes claves de registro NO se pudieron desmontar y siguen" -ForegroundColor Red
    Write-Host "  cargadas en HKEY_USERS del equipo objetivo. Desmontelas manualmente" -ForegroundColor Red
    Write-Host "  con 'reg unload <clave>' antes de devolver o reiniciar el equipo:" -ForegroundColor Red
    foreach ($k in $Script:HivesNoDesmontadas) {
        Write-Host "      - $k" -ForegroundColor Red
    }
    Write-Host ""
}

# --- Resumen final ---
Write-Host ""
Write-Host "  =================================================================" -ForegroundColor Green
Write-Host "              PERITACION FORENSE COMPLETADA                        " -ForegroundColor Green
Write-Host "  =================================================================" -ForegroundColor Green
Write-Host "  Artefactos:  $totalCollected (elementos recopilados en fases)" -ForegroundColor White
Write-Host "  Evidencias:  $($Script:SessionInfo.TotalEvidences) (piezas en cadena de custodia)" -ForegroundColor White
Write-Host "  Hashes:      $hashedCount" -ForegroundColor White
Write-Host "  Anomalias:   $anomalyCount" -ForegroundColor White
Write-Host "  Directorio:  $OutputPath" -ForegroundColor White
if ($Script:HivesNoDesmontadas -and $Script:HivesNoDesmontadas.Count -gt 0) {
    Write-Host "  AVISO:       $($Script:HivesNoDesmontadas.Count) hive(s) de registro sin desmontar - ver arriba" -ForegroundColor Red
}
if ($Script:ChainOfCustodyStrictMode) {
    if ($Script:StrictCustodyFailures -and $Script:StrictCustodyFailures.Count -gt 0) {
        Write-Host "  Custodia:    FALLIDA - $($Script:StrictCustodyFailures.Count) evidencia(s) con hash invalido (-ChainOfCustodyStrictMode)" -ForegroundColor Red
    } else {
        Write-Host "  Custodia:    OK - todos los hashes verificados (-ChainOfCustodyStrictMode)" -ForegroundColor Green
    }
}
Write-Host "  =================================================================" -ForegroundColor Green
Write-Host ""

$exitCode = 0

# -ChainOfCustodyStrictMode: senalizacion agresiva al finalizar (no aborta la adquisicion
# en curso). Si hubo evidencias con hash invalido, el chequeo final de integridad se
# marca explicitamente como FALLIDO, de forma muy visible, y el codigo de salida del
# proceso permite detectarlo desde scripts de automatizacion que envuelvan la herramienta.
if ($Script:ChainOfCustodyStrictMode -and $Script:StrictCustodyFailures -and $Script:StrictCustodyFailures.Count -gt 0) {
    Write-Host ""
    Write-Host "  #################################################################" -ForegroundColor Red
    Write-Host "  # CHEQUEO FINAL DE INTEGRIDAD: FALLIDO (-ChainOfCustodyStrictMode) #" -ForegroundColor Red
    Write-Host "  #################################################################" -ForegroundColor Red
    Write-Host "  Las siguientes evidencias quedaron con un hash SHA-256 no valido" -ForegroundColor Red
    Write-Host "  (revise Logs\ChainOfCustody.csv, entradas con prefijo INVALIDO:):" -ForegroundColor Red
    foreach ($f in $Script:StrictCustodyFailures) {
        Write-Host "      - $f" -ForegroundColor Red
    }
    Write-Host ""
    Write-ForensicLog -Phase "FIN" -Action "CHEQUEO_INTEGRIDAD_FINAL_FALLIDO" -Target "$($Script:StrictCustodyFailures.Count) evidencia(s) con hash invalido" -Result "ERROR"
    $exitCode = 2
} else {
    Write-ForensicLog -Phase "FIN" -Action "PERITACION_COMPLETADA" -Target $OutputPath -Result "OK"
}

exit $exitCode

#endregion ===== Main.ps1 =====

