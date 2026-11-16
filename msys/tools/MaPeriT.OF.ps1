#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
.NOTES
    MaPeriT.OF - Adquisicion Forense Offline v1.1.1
    Recopila evidencias desde disco montado en otro equipo o entorno Live
    Compatible con MaPeriT.AN para analisis posterior
    Compilado: 2026-07-12 18:56:36
    NO EDITAR DIRECTAMENTE - Modifique los ficheros fuente en src\
#>
#region ===== src\Main-Params.ps1 =====
<#
.SYNOPSIS
    MaPeriT.OF - Adquisicion Forense Offline

.DESCRIPTION
    Recopila evidencias forenses desde un disco duro conectado a otro equipo
    o desde un entorno Windows Live CD. El disco objetivo NO necesita estar
    arrancado. Genera la misma estructura de salida que MaPeriT.SW para
    procesado posterior con MaPeriT.AN.

    Cumple RFC 3227 (IETF) y NIST SP 800-86.
    Todas las operaciones son de SOLO LECTURA sobre el disco objetivo.

.PARAMETER TargetDisk
    OBLIGATORIO. Letra de unidad del disco objetivo montado.
    Ejemplos: D:  E:  F:  G:

.PARAMETER CaseNumber
    OBLIGATORIO. Numero de caso o expediente.

.PARAMETER ExaminerName
    OBLIGATORIO. Nombre del perito informatico.

.PARAMETER OutputPath
    Directorio de salida. Por defecto: .\MaPeriT_OF_<caso>_<timestamp>

.PARAMETER EvidenceDescription
    Descripcion del encargo pericial.

.PARAMETER SkipPhases
    Fases a omitir (rango valido: 2-10), como cadena separada por comas o
    espacios (p.ej. "8,9" o "8 9"). NO usar sintaxis de array @() aqui: al
    invocarse este script con -File (como hace MASys), PowerShell NO
    evalua @() como expresion -- lo recibe como texto literal y falla la
    conversion a numero. Ejemplo: -SkipPhases "8,9"

.PARAMETER CopyLargeFiles
    Copiar ficheros grandes (>500 MB) como PST/OST.

.PARAMETER HMACKey
    Clave secreta para firma HMAC-SHA256 del manifiesto de evidencias.
    Permite verificacion criptografica de integridad en sede judicial.

.PARAMETER TargetUser
    Nombre de perfil de usuario especifico a analizar (carpeta en Users\).
    Si se omite, se procesan todos los perfiles detectados.

.PARAMETER ExcludePaths
    Array de fragmentos de ruta a excluir de la copia de evidencias (comparacion por
    subcadena, sin distinguir mayusculas/minusculas). Util para minimizacion de datos
    cuando la autorizacion/orden judicial delimita el alcance de la adquisicion
    (p.ej. -ExcludePaths @("\Fotos Familiares\","\Backups Personales\")). Las rutas
    excluidas quedan registradas como SKIP en el log, nunca se omiten en silencio.

.PARAMETER VerifyPath
    Modo verificacion: comprueba la integridad de una adquisicion OF previa contra su
    HashManifest.csv, reconstruyendo la ubicacion de cada fichero de forma portable
    aunque la carpeta de evidencias se haya movido o copiado a otra unidad para su
    presentacion en sede judicial. No requiere -TargetDisk/-CaseNumber/-ExaminerName.

.EXAMPLE
    .\MaPeriT.OF.ps1 -TargetDisk "D:" -CaseNumber "JUD-2026-112" -ExaminerName "Juan Perez Garcia"

.EXAMPLE
    .\MaPeriT.OF.ps1 -TargetDisk "E:" -CaseNumber "CASO-001" -ExaminerName "Ana Garcia" `
        -OutputPath "F:\Peritajes\CASO-001" -TargetUser "jsmith" -HMACKey "clave-judicial-2026"

.EXAMPLE
    .\MaPeriT.OF.ps1 -TargetDisk "F:" -CaseNumber "INC-789" -ExaminerName "Carlos Martinez" `
        -SkipPhases "8,9" -CopyLargeFiles

.EXAMPLE
    .\MaPeriT.OF.ps1 -VerifyPath "F:\Peritajes\MaPeriT_OF_CASO-001_20260712"
#>

param(
    # TargetDisk/CaseNumber/ExaminerName NO se declaran [Parameter(Mandatory=$true)]:
    # eso obligaria a PowerShell a pedirlos de forma interactiva incluso cuando el
    # unico proposito de la invocacion es -VerifyPath (que no toca ningun disco
    # objetivo). Se validan manualmente en Main.ps1 solo cuando no se use -VerifyPath.
    [string]$TargetDisk          = "",
    [string]$CaseNumber          = "",
    [string]$ExaminerName        = "",
    [string]$OutputPath          = "",
    [string]$EvidenceDescription = "",
    [string]$SkipPhases          = "",
    [switch]$CopyLargeFiles,
    [string]$HMACKey             = "",
    [string]$TargetUser          = "",
    [string[]]$ExcludePaths      = @(),
    [string]$VerifyPath          = ""
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

#endregion ===== src\Main-Params.ps1 =====

#region ===== src\Config.ps1 =====
#region ============================================================
# MaPeriT.OF - CONFIGURACION GLOBAL
# ============================================================

$Script:TOOL_NAME    = "MaPeriT.OF"
$Script:TOOL_VERSION = "1.1.1"
$Script:TOOL_FULL    = "MaPeriT.OF - Adquisicion Forense Offline Profesional"

# --- Nombres de fases (Phase 1 no aplica en offline) ---
$Script:PhaseNames = @{
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

# --- Estructura de directorios de salida (IDENTICA a SW para compatibilidad con AN) ---
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

# --- Rutas relativas al disco objetivo (se combinan con $Script:TargetRoot en runtime) ---
$Script:TargetPaths = @{
    Windows       = "Windows"
    System32      = "Windows\System32"
    Config        = "Windows\System32\config"
    EventLogs     = "Windows\System32\winevt\Logs"
    Tasks         = "Windows\System32\Tasks"
    Prefetch      = "Windows\Prefetch"
    Users         = "Users"
}

# --- Hives del registro en el disco objetivo ---
$Script:HiveFiles = @{
    SYSTEM   = "Windows\System32\config\SYSTEM"
    SOFTWARE = "Windows\System32\config\SOFTWARE"
    SAM      = "Windows\System32\config\SAM"
    SECURITY = "Windows\System32\config\SECURITY"
    NTUSER   = "NTUSER.DAT"
    UsrClass = "AppData\Local\Microsoft\Windows\UsrClass.dat"
}

# --- Rutas de perfiles de navegadores (relativas al perfil de usuario) ---
$Script:BrowserPaths = @{
    Chrome  = "AppData\Local\Google\Chrome\User Data"
    Edge    = "AppData\Local\Microsoft\Edge\User Data"
    Brave   = "AppData\Local\BraveSoftware\Brave-Browser\User Data"
    Firefox = "AppData\Roaming\Mozilla\Firefox\Profiles"
    IE      = "AppData\Local\Microsoft\Windows\WebCache"
}

# --- Ficheros de navegadores Chromium ---
$Script:ChromiumFiles = @(
    "History", "Downloads", "Cookies", "Login Data", "Preferences",
    "Bookmarks", "Web Data", "Top Sites", "Shortcuts", "Favicons"
)

# --- Ficheros de Firefox ---
$Script:FirefoxFiles = @(
    "places.sqlite", "cookies.sqlite", "formhistory.sqlite",
    "logins.json", "key4.db", "cert9.db", "extensions.json",
    "webappsstore.sqlite", "favicons.sqlite", "permissions.sqlite"
)

# --- Rutas de comunicaciones (relativas al perfil de usuario) ---
$Script:CommunicationPaths = @{
    OutlookOST    = "AppData\Local\Microsoft\Outlook"
    OutlookPST    = "Documents\Outlook Files"
    TeamsNew      = "AppData\Local\Packages\MSTeams_8wekyb3d8bbwe\LocalCache"
    TeamsClassic  = "AppData\Roaming\Microsoft\Teams"
    Skype         = "AppData\Roaming\Microsoft\Skype for Desktop"
    Discord       = "AppData\Roaming\discord"
}

# --- Rutas de almacenamiento en la nube (relativas al perfil de usuario) ---
$Script:CloudPaths = @{
    OneDriveLogs     = "AppData\Local\Microsoft\OneDrive\logs"
    OneDriveSettings = "AppData\Local\Microsoft\OneDrive\settings"
    GoogleDrive      = "AppData\Local\Google\DriveFS"
    Dropbox          = "AppData\Local\Dropbox"
    DropboxInfo      = "AppData\Roaming\Dropbox"
}

# --- Perfiles de usuario del sistema a excluir ---
$Script:ExcludedProfiles = @(
    "Public", "Default", "Default User", "All Users",
    "defaultuser0", "TEMP", "Administrator"
)

# --- Defaults ---
$Script:Defaults = @{
    LargeFileThresholdMB = 500
    PhaseTimeoutMinutes  = 15
    HashAlgorithm        = "SHA256"
    CSVDelimiter         = ","
    DateFormat           = "yyyy-MM-dd HH:mm:ss.fff"
    DateFormatShort      = "yyyy-MM-dd HH:mm:ss"
    TimeZoneFormat       = "yyyy-MM-dd HH:mm:ss zzz"
    MaxEvtxFiles         = 200
}

# --- Sesion OF (rellenada en Initialize) ---
$Script:OFSession = @{
    StartTime         = $null
    EndTime           = $null
    TargetDisk        = ""
    TargetRoot        = ""
    CaseNumber        = ""
    ExaminerName      = ""
    OutputPath        = ""
    ScriptPath        = ""
    ScriptHash        = ""
    TargetOS          = "DESCONOCIDO"
    TargetComputer    = "DESCONOCIDO"
    DetectedUsers     = @()
    ProcessedUsers    = @()
    TotalFiles        = 0
    TotalSizeBytes    = 0
    Errors            = 0
    Warnings          = 0
}

# --- Variable global: ruta raiz del disco objetivo (se define en Initialize) ---
$Script:TargetRoot = ""

# --- Variables para hives cargados (se limpian al finalizar) ---
$Script:LoadedHives = [System.Collections.Generic.Dictionary[string,string]]::new()

# --- Banner ---
$Script:Banner = @"

  ╔═══════════════════════════════════════════════════════════════════╗
  ║                                                                   ║
  ║      ███╗   ███╗ █████╗ ██████╗ ███████╗██████╗ ██╗████████╗     ║
  ║      ████╗ ████║██╔══██╗██╔══██╗██╔════╝██╔══██╗██║╚══██╔══╝     ║
  ║      ██╔████╔██║███████║██████╔╝█████╗  ██████╔╝██║   ██║        ║
  ║      ██║╚██╔╝██║██╔══██║██╔═══╝ ██╔══╝  ██╔══██╗██║   ██║        ║
  ║      ██║ ╚═╝ ██║██║  ██║██║     ███████╗██║  ██║██║   ██║        ║
  ║      ╚═╝     ╚═╝╚═╝  ╚═╝╚═╝     ╚══════╝╚═╝  ╚═╝╚═╝   ╚═╝  .OF  ║
  ║                                                                   ║
  ║          Adquisicion Forense Offline Profesional                  ║
  ║                       v{VERSION}                                      ║
  ║                                                                   ║
  ╚═══════════════════════════════════════════════════════════════════╝

"@
#endregion ===== src\Config.ps1 =====

#region ===== src\Core\Logging.ps1 =====
#region ============================================================
# MaPeriT.OF - LOGGING
# Mismo formato de salida que SW para compatibilidad con AN
# ============================================================

$Script:LogPath = ""

function Initialize-OFLogging {
    param([string]$LogsPath)
    $Script:LogPath = Join-Path $LogsPath "ForensicLog.csv"
    "Timestamp,Phase,Action,Target,Result,Detail" | Out-File -FilePath $Script:LogPath -Encoding UTF8 -Force
}

function Write-ForensicLog {
    param(
        [string]$Phase   = "OF",
        [string]$Action  = "",
        [string]$Target  = "",
        [string]$Result  = "OK",
        [string]$Detail  = "",
        [long]$DurationMs = 0
    )
    $ts     = (Get-Date).ToString($Script:Defaults.DateFormat)
    $detail = if ($DurationMs -gt 0) { "${Detail}$(if($Detail){' '})Duracion=${DurationMs}ms" } else { $Detail }
    $line   = "$ts,$Phase,$Action,`"$($Target -replace '"','""')`",$Result,`"$($detail -replace '"','""')`""
    Add-Content -Path $Script:LogPath -Value $line -Encoding UTF8

    $color = switch ($Result) {
        "OK"      { "Green"  }
        "WARNING" { "Yellow" }
        "ERROR"   { "Red"    }
        "SKIP"    { "DarkGray" }
        default   { "White"  }
    }
    $symbol = switch ($Result) {
        "OK"      { "[+]" }
        "WARNING" { "[!]" }
        "ERROR"   { "[-]" }
        "SKIP"    { "[~]" }
        default   { "[*]" }
    }
    Write-Host "  $symbol $Action" -ForegroundColor $color -NoNewline
    if ($Target) { Write-Host " -> $Target" -ForegroundColor DarkGray -NoNewline }
    Write-Host ""

    if ($Result -eq "ERROR")   { $Script:OFSession.Errors++ }
    if ($Result -eq "WARNING") { $Script:OFSession.Warnings++ }
}

function Write-PhaseHeader {
    param([int]$PhaseNumber, [string]$PhaseName)
    Write-Host ""
    Write-Host "  ╔══════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
    Write-Host "  ║  FASE $PhaseNumber — $($PhaseName.PadRight(54))║" -ForegroundColor Cyan
    Write-Host "  ╚══════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
    Write-Host ""
    Write-ForensicLog -Phase "FASE_$PhaseNumber" -Action "FASE_INICIO" -Target $PhaseName
}

function Write-PhaseComplete {
    param([int]$PhaseNumber, [string]$PhaseName, [int]$ItemsCollected)
    Write-Host ""
    Write-Host "  [OK] Fase $PhaseNumber completada — $ItemsCollected artefactos recopilados" -ForegroundColor Green
    Write-ForensicLog -Phase "FASE_$PhaseNumber" -Action "FASE_COMPLETA" -Target "$ItemsCollected artefactos" -Result "OK"
}

function Write-StepHeader {
    param([string]$Step)
    Write-Host "  --- $Step" -ForegroundColor DarkCyan
}

function Export-CollectionToCSV {
    param([object]$Data, [string]$OutputPath, [string]$Phase, [string]$Description)
    try {
        $dir = Split-Path $OutputPath -Parent
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        $Data | Export-Csv -Path $OutputPath -NoTypeInformation -Encoding UTF8 -Force
        $count = if ($Data -is [array]) { $Data.Count } else { 1 }
        Write-ForensicLog -Phase $Phase -Action "EXPORTADO_CSV" -Target (Split-Path $OutputPath -Leaf) -Result "OK" -Detail "$count registros"
    } catch {
        Write-ForensicLog -Phase $Phase -Action "ERROR_CSV" -Target $Description -Result "ERROR" -Detail $_.Exception.Message
    }
}

function Export-CollectionToJSON {
    param([object]$Data, [string]$OutputPath, [string]$Phase, [string]$Description)
    try {
        $dir = Split-Path $OutputPath -Parent
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        $Data | ConvertTo-Json -Depth 5 | Out-File -FilePath $OutputPath -Encoding UTF8 -Force
        Write-ForensicLog -Phase $Phase -Action "EXPORTADO_JSON" -Target (Split-Path $OutputPath -Leaf) -Result "OK" -Detail $Description
    } catch {
        Write-ForensicLog -Phase $Phase -Action "ERROR_JSON" -Target $Description -Result "ERROR" -Detail $_.Exception.Message
    }
}

function Export-CollectionToText {
    param([string]$Content, [string]$OutputPath, [string]$Phase, [string]$Description)
    try {
        $dir = Split-Path $OutputPath -Parent
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        $Content | Out-File -FilePath $OutputPath -Encoding UTF8 -Force
        Write-ForensicLog -Phase $Phase -Action "EXPORTADO_TXT" -Target (Split-Path $OutputPath -Leaf) -Result "OK" -Detail $Description
    } catch {
        Write-ForensicLog -Phase $Phase -Action "ERROR_TXT" -Target $Description -Result "ERROR" -Detail $_.Exception.Message
    }
}
#endregion ===== src\Core\Logging.ps1 =====

#region ===== ..\Common\PathHelpers.ps1 =====
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
#endregion ===== ..\Common\PathHelpers.ps1 =====

#region ===== ..\Common\HtmlEncoding.ps1 =====
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
#endregion ===== ..\Common\HtmlEncoding.ps1 =====

#region ===== src\Core\Hashing.ps1 =====
#region ============================================================
# MaPeriT.OF - HASHING Y HMAC
# ============================================================

function Get-ForensicHash {
    param([string]$FilePath)
    try {
        return (Get-FileHash -Path $FilePath -Algorithm SHA256 -ErrorAction Stop).Hash
    } catch {
        Write-ForensicLog -Action "HASH_ERROR" -Target $FilePath -Result "ERROR" -Detail $_.Exception.Message
        return "ERROR_HASH"
    }
}

function Get-StringHash {
    param([string]$InputString)
    $bytes  = [System.Text.Encoding]::UTF8.GetBytes($InputString)
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    $hash   = $sha256.ComputeHash($bytes)
    $sha256.Dispose()
    return [System.BitConverter]::ToString($hash) -replace '-', ''
}

function Get-HMACSignature {
    param([string]$FilePath, [string]$Key)
    try {
        $content   = [System.IO.File]::ReadAllBytes($FilePath)
        $keyBytes  = [System.Text.Encoding]::UTF8.GetBytes($Key)
        $hmac      = [System.Security.Cryptography.HMACSHA256]::new($keyBytes)
        $signature = $hmac.ComputeHash($content)
        $hmac.Dispose()
        return [System.BitConverter]::ToString($signature) -replace '-', ''
    } catch {
        Write-ForensicLog -Action "HMAC_ERROR" -Target $FilePath -Result "ERROR" -Detail $_.Exception.Message
        return "ERROR_HMAC"
    }
}

# Get-RelativeEvidencePath ya no se define aqui: se comparte con MaPeriT.SW
# via ..\Common\PathHelpers.ps1 (incluido por Build.ps1 antes de este
# fichero) para no mantener dos copias que puedan divergir o repetir el
# mismo bug de anclaje de ruta por separado.

function Test-HashManifest {
    <#
        Verifica la integridad de una adquisicion OF ya completada, comparando el
        HashManifest.csv contra los ficheros reales bajo -VerifyPath. Usa RelativePath
        (calculado en Add-ChainOfCustodyEntry) para reconstruir la ubicacion actual de
        cada fichero, no DestPath -- DestPath es la ruta ABSOLUTA del momento de la
        adquisicion y deja de ser fiable si la carpeta de evidencias se movio o se
        copio a otra unidad para su presentacion en sede judicial.
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
    $missing  = 0
    $total    = $manifestData.Count
    $current  = 0

    foreach ($entry in $manifestData) {
        $current++
        if ($ShowProgress -and ($current % 25 -eq 0)) {
            Write-Host "`r  [*] Verificando: $current/$total" -ForegroundColor Yellow -NoNewline
        }

        if ($entry.SHA256 -like "INVALIDO:*" -or $entry.SHA256 -like "ERROR_HASH*") { continue }

        # RelativePath puede no existir en manifiestos generados antes de esta version;
        # en ese caso, mejor esfuerzo con DestPath (funciona si no se ha movido la carpeta).
        $relPath = if ($entry.PSObject.Properties.Name -contains 'RelativePath' -and $entry.RelativePath) {
            $entry.RelativePath
        } else {
            $null
        }
        $filePath = if ($relPath) { Join-Path $EvidenceRootPath $relPath } else { $entry.DestPath }

        if (-not (Test-Path $filePath)) {
            if ($ShowProgress) { Write-Host "`n  [-] FALTA: $($entry.EvidenceID) ($filePath)" -ForegroundColor Red }
            $mismatchList += [PSCustomObject]@{ File = $filePath; Expected = $entry.SHA256; Actual = "FILE_MISSING"; Status = "FALLO" }
            $missing++
            continue
        }

        $actualHash = (Get-FileHash -Path $filePath -Algorithm SHA256 -ErrorAction SilentlyContinue).Hash
        if ($actualHash -ne $entry.SHA256) {
            if ($ShowProgress) {
                Write-Host "`n  [-] MODIFICADO: $($entry.EvidenceID) ($filePath)" -ForegroundColor Red
                Write-Host "      Esperado: $($entry.SHA256)" -ForegroundColor DarkGray
                Write-Host "      Actual:   $actualHash" -ForegroundColor DarkGray
            }
            $mismatchList += [PSCustomObject]@{ File = $filePath; Expected = $entry.SHA256; Actual = $actualHash; Status = "FALLO" }
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
            $mismatchList | ForEach-Object { Write-Host "      $($_.File): $($_.Status)" -ForegroundColor Red }
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

function Write-HMACSignature {
    param([string]$ManifestPath, [string]$Key)
    $signature  = Get-HMACSignature -FilePath $ManifestPath -Key $Key
    $sigFile    = $ManifestPath -replace '\.csv$', '_HMAC.txt'
    $sigContent = @"
MaPeriT.OF - Firma HMAC-SHA256 del Manifiesto de Evidencias
============================================================
Fichero firmado : $ManifestPath
Algoritmo       : HMAC-SHA256
Fecha de firma  : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
Firma           : $signature

Para verificar: recalcule el HMAC-SHA256 del HashManifest.csv con la clave
secreta acordada y compare con el valor anterior.
"@
    $sigContent | Out-File -FilePath $sigFile -Encoding UTF8 -Force
    Write-ForensicLog -Action "HMAC_FIRMADO" -Target (Split-Path $sigFile -Leaf) -Result "OK"
}
#endregion ===== src\Core\Hashing.ps1 =====

#region ===== src\Core\FileAcquisition.ps1 =====
#region ============================================================
# MaPeriT.OF - ADQUISICION DE FICHEROS OFFLINE
# Copia ficheros del disco objetivo con hash y cadena de custodia
# ============================================================

function Test-PathExcluded {
    <#
        Comprueba si una ruta coincide con alguno de los fragmentos indicados en
        -ExcludePaths (comparacion por subcadena, sin distinguir mayusculas/minusculas).
        Mismo patron ya usado en MaPeriT.SW para minimizacion de datos cuando la
        autorizacion/orden judicial delimita el alcance de la adquisicion. Las
        exclusiones quedan siempre registradas como SKIP, nunca se omiten en silencio.
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

function Copy-ForensicFile {
    param(
        [string]$SourcePath,
        [string]$DestPath,
        [string]$Phase = "OF",
        [string]$Method = "CopyItem"
    )
    if (Test-PathExcluded -Path $SourcePath) {
        Write-ForensicLog -Phase $Phase -Action "EXCLUIDO_POR_PARAMETRO" -Target $SourcePath -Result "SKIP"
        return $false
    }
    if (-not (Test-Path $SourcePath)) {
        Write-ForensicLog -Phase $Phase -Action "FICHERO_NO_ENCONTRADO" -Target $SourcePath -Result "WARNING"
        return $false
    }
    try {
        $dir = Split-Path $DestPath -Parent
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        Copy-Item -Path $SourcePath -Destination $DestPath -Force -ErrorAction Stop
        $hash = Get-ForensicHash -FilePath $DestPath
        $size = (Get-Item $DestPath).Length
        Add-ChainOfCustodyEntry -SourcePath $SourcePath -DestPath $DestPath -Hash $hash -Method $Method -FileSize $size
        $Script:OFSession.TotalFiles++
        $Script:OFSession.TotalSizeBytes += $size
        return $true
    } catch {
        Write-ForensicLog -Phase $Phase -Action "COPIA_ERROR" -Target $SourcePath -Result "ERROR" -Detail $_.Exception.Message
        return $false
    }
}

function Copy-ForensicDirectory {
    param(
        [string]$SourceDir,
        [string]$DestDir,
        [string]$Phase    = "OF",
        [string]$Filter   = "*",
        [switch]$Recurse,
        [long]$MaxSizeMB  = 0
    )
    if (-not (Test-Path $SourceDir)) {
        Write-ForensicLog -Phase $Phase -Action "DIR_NO_ENCONTRADO" -Target $SourceDir -Result "WARNING"
        return 0
    }
    $copied = 0
    try {
        $getParams = @{ Path = $SourceDir; Filter = $Filter; File = $true; ErrorAction = "SilentlyContinue" }
        if ($Recurse) { $getParams.Recurse = $true }
        $files = Get-ChildItem @getParams
        if ($MaxSizeMB -gt 0 -and -not $CopyLargeFiles) {
            $threshold = $MaxSizeMB * 1MB
            $files = $files | Where-Object { $_.Length -le $threshold }
        }
        foreach ($f in $files) {
            # Comprobar exclusion antes de construir la ruta destino: evita crear
            # carpetas vacias residuales para ficheros que -ExcludePaths pide
            # minimizar (Copy-ForensicFile tambien lo comprueba, pero aqui se evita
            # incluso el Join-Path/creacion de estructura para el caso excluido).
            if (Test-PathExcluded -Path $f.FullName) { continue }
            $relative = $f.FullName.Substring($SourceDir.TrimEnd('\').Length + 1)
            $dest     = Join-Path $DestDir $relative
            if (Copy-ForensicFile -SourcePath $f.FullName -DestPath $dest -Phase $Phase -Method "CopyDir") {
                $copied++
            }
        }
    } catch {
        Write-ForensicLog -Phase $Phase -Action "COPIA_DIR_ERROR" -Target $SourceDir -Result "ERROR" -Detail $_.Exception.Message
    }
    return $copied
}

function Copy-ForensicFileBackup {
    param(
        [string]$SourcePath,
        [string]$DestPath,
        [string]$Phase = "OF"
    )
    # Copia con privilegio de backup para ficheros del sistema (como $MFT)
    # Usa robocopy /B como metodo primario
    $srcDir  = Split-Path $SourcePath -Parent
    $srcFile = Split-Path $SourcePath -Leaf
    $destDir = Split-Path $DestPath -Parent

    if (-not (Test-Path $destDir)) { New-Item -ItemType Directory -Path $destDir -Force | Out-Null }

    $roboArgs = @($srcDir, $destDir, $srcFile, '/B', '/R:0', '/W:0', '/NJH', '/NJS', '/NFL', '/NDL')
    $result   = & robocopy @roboArgs 2>&1
    $exitCode = $LASTEXITCODE

    # Robocopy: exit codes 0-7 son exito; >=8 error
    if ($exitCode -le 7 -and (Test-Path $DestPath)) {
        $hash = Get-ForensicHash -FilePath $DestPath
        $size = (Get-Item $DestPath).Length
        Add-ChainOfCustodyEntry -SourcePath $SourcePath -DestPath $DestPath -Hash $hash -Method "robocopy/B" -FileSize $size
        $Script:OFSession.TotalFiles++
        $Script:OFSession.TotalSizeBytes += $size
        Write-ForensicLog -Phase $Phase -Action "BACKUP_COPY_OK" -Target $srcFile -Result "OK" -Detail "$([math]::Round($size/1MB,1)) MB"
        return $true
    }

    # Fallback: Copy-Item directo (puede funcionar en discos no-sistema)
    try {
        Copy-Item -Path $SourcePath -Destination $DestPath -Force -ErrorAction Stop
        if (Test-Path $DestPath) {
            $hash = Get-ForensicHash -FilePath $DestPath
            $size = (Get-Item $DestPath).Length
            Add-ChainOfCustodyEntry -SourcePath $SourcePath -DestPath $DestPath -Hash $hash -Method "CopyItem/Fallback" -FileSize $size
            $Script:OFSession.TotalFiles++
            $Script:OFSession.TotalSizeBytes += $size
            Write-ForensicLog -Phase $Phase -Action "BACKUP_COPY_FALLBACK_OK" -Target $srcFile -Result "OK"
            return $true
        }
    } catch {}

    Write-ForensicLog -Phase $Phase -Action "BACKUP_COPY_FALLO" -Target $SourcePath -Result "WARNING" -Detail "RoboCopy=$exitCode"
    return $false
}

function Copy-RobocopyDir {
    param(
        [string]$SourceDir,
        [string]$DestDir,
        [string]$Filter  = "*.*",
        [string]$Phase   = "OF",
        [switch]$Recurse
    )
    if (-not (Test-Path $SourceDir)) {
        Write-ForensicLog -Phase $Phase -Action "DIR_NO_ENCONTRADO" -Target $SourceDir -Result "WARNING"
        return 0
    }
    if (-not (Test-Path $DestDir)) { New-Item -ItemType Directory -Path $DestDir -Force | Out-Null }

    $roboArgs = @($SourceDir, $DestDir, $Filter, '/B', '/R:0', '/W:0', '/NJH', '/NJS', '/COPY:DAT')
    if ($Recurse) { $roboArgs += '/S' }

    $output   = & robocopy @roboArgs 2>&1
    $exitCode = $LASTEXITCODE

    # BUG CONFIRMADO: esta funcion copiaba con robocopy pero NUNCA hasheaba
    # ni registraba los ficheros en la cadena de custodia (a diferencia de
    # Copy-ForensicDirectory/Copy-ForensicFile, que si lo hacen siempre). Se
    # usa para Prefetch (Phase3) y Tareas Programadas (Phase10): esa evidencia
    # quedaba fuera de HashManifest.csv, sin SHA-256 verificable - compromete
    # su validez probatoria. Se hashea y registra cada fichero copiado.
    $copiedFileObjs = @(Get-ChildItem $DestDir -Filter $Filter -Recurse:$Recurse -File -ErrorAction SilentlyContinue)
    foreach ($cf in $copiedFileObjs) {
        $relative = $cf.FullName.Substring($DestDir.TrimEnd('\').Length + 1)
        $srcPath  = Join-Path $SourceDir $relative
        try {
            $hash = Get-ForensicHash -FilePath $cf.FullName
            Add-ChainOfCustodyEntry -SourcePath $srcPath -DestPath $cf.FullName -Hash $hash -Method "robocopy" -FileSize $cf.Length
            $Script:OFSession.TotalFiles++
            $Script:OFSession.TotalSizeBytes += $cf.Length
        } catch {
            Write-ForensicLog -Phase $Phase -Action "HASH_ROBOCOPY_ERROR" -Target $cf.FullName -Result "WARNING" -Detail $_.Exception.Message
        }
    }
    $copiedFiles = $copiedFileObjs.Count
    if ($exitCode -le 7) {
        Write-ForensicLog -Phase $Phase -Action "ROBOCOPY_OK" -Target (Split-Path $SourceDir -Leaf) -Result "OK" -Detail "$copiedFiles ficheros"
    } else {
        Write-ForensicLog -Phase $Phase -Action "ROBOCOPY_PARCIAL" -Target (Split-Path $SourceDir -Leaf) -Result "WARNING" -Detail "ExitCode=$exitCode"
    }
    return $copiedFiles
}
#endregion ===== src\Core\FileAcquisition.ps1 =====

#region ===== src\Core\RegistryOffline.ps1 =====
#region ============================================================
# MaPeriT.OF - REGISTRO OFFLINE
# Carga, consulta y descarga hives del disco objetivo sin arrancar el SO
# Tecnica: reg load -> Registry:: provider -> reg unload
# ============================================================

function Mount-OfflineHive {
    param(
        [string]$HivePath,
        [string]$Label = "OF"
    )
    if (-not (Test-Path $HivePath)) {
        Write-ForensicLog -Action "HIVE_NO_ENCONTRADO" -Target $HivePath -Result "WARNING"
        return $null
    }
    # Nombre unico para evitar conflictos con otros procesos
    $suffix  = [System.Guid]::NewGuid().ToString("N").Substring(0,8).ToUpper()
    $keyName = "OF_${Label}_${suffix}"
    $regKey  = "HKLM\$keyName"

    $output  = & reg load $regKey "`"$HivePath`"" 2>&1
    if ($LASTEXITCODE -eq 0) {
        $Script:LoadedHives[$regKey] = $HivePath
        Write-ForensicLog -Action "HIVE_CARGADO" -Target "$Label -> $keyName" -Result "OK"
        return "Registry::$regKey"
    }
    Write-ForensicLog -Action "HIVE_ERROR_CARGA" -Target $HivePath -Result "ERROR" -Detail ($output -join " ")
    return $null
}

function Dismount-OfflineHive {
    param([string]$RegistryPath)
    if (-not $RegistryPath) { return }
    $regKey = $RegistryPath -replace '^Registry::', ''
    [System.GC]::Collect()
    [System.GC]::WaitForPendingFinalizers()
    $output = & reg unload $regKey 2>&1
    if ($LASTEXITCODE -eq 0) {
        $Script:LoadedHives.Remove($regKey)
        Write-ForensicLog -Action "HIVE_DESCARGADO" -Target $regKey -Result "OK"
    } else {
        Write-ForensicLog -Action "HIVE_ERROR_DESCARGA" -Target $regKey -Result "WARNING" -Detail ($output -join " ")
    }
}

function Dismount-AllOfflineHives {
    # BUG CONFIRMADO: la version anterior ignoraba el codigo de salida real de
    # "reg unload" y SIEMPRE vaciaba $Script:LoadedHives + logueaba "OK", aunque
    # el unload hubiera fallado de verdad (proceso con handle abierto, AV, etc.).
    # Es la ULTIMA red de seguridad de Main.ps1 para garantizar que ningun hive
    # queda montado en el registro del equipo forense tras la ejecucion - con el
    # bug, un hive podia quedar montado indefinidamente y el log lo ocultaba
    # afirmando "limpieza completa OK". Ahora se comprueba cada unload
    # individualmente y solo se retira del tracking lo que realmente se descargo.
    $keys = @($Script:LoadedHives.Keys)
    $failed = [System.Collections.Generic.List[string]]::new()
    foreach ($key in $keys) {
        [System.GC]::Collect()
        [System.GC]::WaitForPendingFinalizers()
        $output = & reg unload $key 2>&1
        if ($LASTEXITCODE -eq 0) {
            $Script:LoadedHives.Remove($key)
        } else {
            $failed.Add($key)
            Write-ForensicLog -Action "HIVE_ERROR_DESCARGA" -Target $key -Result "WARNING" -Detail ($output -join " ")
        }
    }
    if ($failed.Count -gt 0) {
        Write-ForensicLog -Action "HIVES_LIMPIEZA_INCOMPLETA" -Target ($failed -join ", ") -Result "ERROR" -Detail "Hives que siguen montados en el equipo forense - requiere intervencion manual (reg unload)"
    } else {
        Write-ForensicLog -Action "HIVES_LIMPIEZA_COMPLETA" -Result "OK"
    }
}

function Get-OfflineRegProperty {
    param([string]$HiveRoot, [string]$SubKey, [string]$Name = "")
    $fullPath = "$HiveRoot\$SubKey"
    try {
        if ($Name) {
            return (Get-ItemProperty -Path $fullPath -Name $Name -ErrorAction Stop).$Name
        } else {
            return Get-ItemProperty -Path $fullPath -ErrorAction Stop
        }
    } catch { return $null }
}

function Get-OfflineRegChildren {
    param([string]$HiveRoot, [string]$SubKey = "")
    $path = if ($SubKey) { "$HiveRoot\$SubKey" } else { $HiveRoot }
    return Get-ChildItem -Path $path -ErrorAction SilentlyContinue
}

function Get-OfflineControlSet {
    param([string]$SystemHiveRoot)
    # Lee el ControlSet activo desde Select\Current
    try {
        $current = Get-OfflineRegProperty -HiveRoot $SystemHiveRoot -SubKey "Select" -Name "Current"
        if ($current) { return "ControlSet{0:D3}" -f [int]$current }
    } catch {}
    return "ControlSet001"
}

function Export-OfflineRegSubtree {
    param(
        [string]$HiveRoot,
        [string]$SubKey,
        [string]$OutputPath,
        [string]$Phase = "OF",
        [int]$Depth = 3
    )
    $fullPath = "$HiveRoot\$SubKey"
    $results  = @()
    try {
        $keys = Get-ChildItem -Path $fullPath -Recurse -Depth $Depth -ErrorAction SilentlyContinue
        foreach ($k in $keys) {
            $props = Get-ItemProperty -Path $k.PSPath -ErrorAction SilentlyContinue
            if ($props) {
                $props.PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } | ForEach-Object {
                    $results += [PSCustomObject]@{
                        KeyPath = $k.PSPath -replace 'Microsoft\.PowerShell\.Core\\Registry::', ''
                        Name    = $_.Name
                        Value   = ($_.Value | Out-String).Trim()
                        Type    = $_.TypeNameOfValue
                    }
                }
            }
        }
        if ($results.Count -gt 0) {
            $results | Export-Csv -Path $OutputPath -NoTypeInformation -Encoding UTF8 -Force
            Write-ForensicLog -Phase $Phase -Action "REG_EXPORTADO" -Target (Split-Path $OutputPath -Leaf) -Result "OK" -Detail "$($results.Count) valores"
        }
    } catch {
        Write-ForensicLog -Phase $Phase -Action "REG_EXPORT_ERROR" -Target $SubKey -Result "ERROR" -Detail $_.Exception.Message
    }
    return $results
}
#endregion ===== src\Core\RegistryOffline.ps1 =====

#region ===== src\Core\Initialize.ps1 =====
#region ============================================================
# MaPeriT.OF - INICIALIZACION Y VALIDACION
# ============================================================

function Initialize-OFSession {
    param(
        [string]$TargetDisk,
        [string]$CaseNumber,
        [string]$ExaminerName,
        [string]$OutputPath,
        [string]$EvidenceDescription,
        [string]$TargetUser
    )

    # --- Normalizar letra de disco ---
    $disk = $TargetDisk.TrimEnd('\').TrimEnd('/')
    if ($disk -notmatch '^[A-Za-z]:$') {
        throw "Formato de disco no valido: '$TargetDisk'. Use formato D: E: F: etc."
    }
    $root = $disk + '\'

    # --- Verificar que el disco existe y tiene Windows ---
    if (-not (Test-Path $root)) {
        throw "El disco '$root' no es accesible. Verifique que esta montado correctamente."
    }
    $winPath = Join-Path $root "Windows\System32"
    if (-not (Test-Path $winPath)) {
        throw "No se encontro una instalacion de Windows en '$root'. Verifique la letra de disco."
    }

    # --- Normalizar OutputPath ---
    if ([string]::IsNullOrEmpty($OutputPath)) {
        $ts         = Get-Date -Format 'yyyyMMdd_HHmmss'
        $OutputPath = Join-Path (Split-Path $Script:OFSession.ScriptPath -Parent) "MaPeriT_OF_${CaseNumber}_${ts}"
    }

    # --- Crear estructura de directorios de salida (identica a SW) ---
    foreach ($dir in $Script:OutputDirs.Values) {
        $full = Join-Path $OutputPath $dir
        if (-not (Test-Path $full)) { New-Item -ItemType Directory -Path $full -Force | Out-Null }
    }

    # --- Crear directorio de logs e inicializar logging ---
    Initialize-OFLogging -LogsPath (Join-Path $OutputPath $Script:OutputDirs.Logs)

    # --- Detectar perfiles de usuario en el disco objetivo ---
    $usersRoot      = Join-Path $root $Script:TargetPaths.Users
    $detectedUsers  = @()
    if (Test-Path $usersRoot) {
        $detectedUsers = Get-ChildItem $usersRoot -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -notin $Script:ExcludedProfiles } |
            Select-Object -ExpandProperty Name
    }

    # Filtrar por TargetUser si se especifico.
    # BUG CONFIRMADO: si -TargetUser no coincidia con ningun perfil detectado
    # (p.ej. un simple error de escritura), la version anterior ampliaba el
    # alcance a TODOS los usuarios del disco de forma silenciosa (solo
    # Write-Warning, que no queda en ForensicLog.csv ni en el informe), y
    # ademas registraba igualmente "USUARIO_FILTRADO=OK" mas abajo como si el
    # filtro se hubiera aplicado. Si una orden judicial limita la adquisicion
    # a un custodio concreto, un typo del perito ampliaba el alcance sin fallar
    # y sin dejar rastro fiable de la desviacion en el propio log forense.
    # Postura fail-safe: restringir el alcance ante duda, nunca ampliarlo -
    # si el usuario indicado no existe, se detiene la ejecucion.
    if ($TargetUser -and ($TargetUser -notin $detectedUsers)) {
        Write-ForensicLog -Phase "OF" -Action "USUARIO_NO_ENCONTRADO" -Target $TargetUser -Result "ERROR" -Detail "Perfiles detectados: $($detectedUsers -join ', ')"
        throw "El usuario '$TargetUser' no se encontro en $usersRoot. Perfiles detectados: $($detectedUsers -join ', '). Adquisicion detenida para no exceder el alcance autorizado - revise el nombre e intente de nuevo."
    }
    $processedUsers = if ($TargetUser) { @($TargetUser) } else { $detectedUsers }

    # --- Hash del propio script ---
    $scriptHash = Get-ForensicHash -FilePath $Script:OFSession.ScriptPath

    # --- Rellenar sesion ---
    $Script:TargetRoot                = $root
    $Script:OFSession.TargetDisk      = $disk
    $Script:OFSession.TargetRoot      = $root
    $Script:OFSession.CaseNumber      = $CaseNumber
    $Script:OFSession.ExaminerName    = $ExaminerName
    $Script:OFSession.OutputPath      = $OutputPath
    $Script:OFSession.StartTime       = Get-Date
    $Script:OFSession.ScriptHash      = $scriptHash
    $Script:OFSession.DetectedUsers   = $detectedUsers
    $Script:OFSession.ProcessedUsers  = $processedUsers

    # --- Log de inicio (mismo formato que SW para que AN pueda leer el caso) ---
    Write-ForensicLog -Phase "OF" -Action "SESION_INICIADA" -Target $CaseNumber -Result "OK"
    Write-ForensicLog -Phase "OF" -Action "SCRIPT_HASH"    -Target $scriptHash  -Result "OK"
    Write-ForensicLog -Phase "OF" -Action "DISCO_OBJETIVO" -Target $root        -Result "OK"
    Write-ForensicLog -Phase "OF" -Action "PERITO"         -Target $ExaminerName -Result "OK"
    Write-ForensicLog -Phase "OF" -Action "USUARIOS_DETECTADOS" -Target ($detectedUsers -join ", ") -Result "OK"
    if ($TargetUser) {
        Write-ForensicLog -Phase "OF" -Action "USUARIO_FILTRADO" -Target $TargetUser -Result "OK"
    }
    if ($EvidenceDescription) {
        Write-ForensicLog -Phase "OF" -Action "DESCRIPCION" -Target $EvidenceDescription -Result "OK"
    }

    # --- Nota de adquisicion offline en 01_Volatile (AN espera esa carpeta) ---
    $volatilePath = Join-Path $OutputPath $Script:OutputDirs.Volatile
    $noteContent  = @"
ADQUISICION OFFLINE - MaPeriT.OF v$($Script:TOOL_VERSION)
===========================================================
Fecha            : $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
Caso             : $CaseNumber
Perito           : $ExaminerName
Disco objetivo   : $root
Descripcion      : $EvidenceDescription

NOTA: Esta adquisicion se realizo sobre un disco montado en otro equipo.
Los datos volatiles (RAM, procesos, red, portapapeles, cache DNS/ARP)
NO estan disponibles en adquisicion offline y han sido omitidos.
Los artefactos recopilados corresponden a las fases 2-10 (datos persistentes).
"@
    $noteContent | Out-File -FilePath (Join-Path $volatilePath "OFFLINE_ACQUISITION.txt") -Encoding UTF8 -Force

    return $OutputPath
}

function Show-OFBanner {
    Write-Host $Script:Banner -ForegroundColor Cyan
    Write-Host "  Disco objetivo  : $($Script:OFSession.TargetRoot)" -ForegroundColor White
    Write-Host "  Caso            : $($Script:OFSession.CaseNumber)"  -ForegroundColor White
    Write-Host "  Perito          : $($Script:OFSession.ExaminerName)" -ForegroundColor White
    Write-Host "  Salida          : $($Script:OFSession.OutputPath)"   -ForegroundColor White
    Write-Host "  Usuarios        : $($Script:OFSession.ProcessedUsers -join ', ')" -ForegroundColor White
    Write-Host ""
    Write-Host "  ================================================================" -ForegroundColor DarkGray
    Write-Host "  AVISO: Operaciones de SOLO LECTURA sobre el disco objetivo."     -ForegroundColor Yellow
    Write-Host "         La escritura se realiza unicamente en el directorio de salida." -ForegroundColor Yellow
    Write-Host "  ================================================================" -ForegroundColor DarkGray
    Write-Host ""
}

function Get-TargetUserPaths {
    # Devuelve array de hashtables {Name, Root} para cada usuario a procesar
    return $Script:OFSession.ProcessedUsers | ForEach-Object {
        @{
            Name = $_
            Root = Join-Path $Script:TargetRoot "$($Script:TargetPaths.Users)\$_"
        }
    }
}
#endregion ===== src\Core\Initialize.ps1 =====

#region ===== src\Phases\Phase2-SystemInfo.ps1 =====
#region ============================================================
# MaPeriT.OF - FASE 2: INFORMACION DEL SISTEMA
# Lee hives SYSTEM y SOFTWARE del disco objetivo via reg load
# ============================================================

function Invoke-Phase2-SystemInfo {
    param([string]$OutputPath)

    $phasePath = Join-Path $OutputPath $Script:OutputDirs.SystemInfo
    $phaseNum  = 2
    $collected = 0

    Write-PhaseHeader -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum]

    $systemHivePath   = Join-Path $Script:TargetRoot $Script:HiveFiles.SYSTEM
    $softwareHivePath = Join-Path $Script:TargetRoot $Script:HiveFiles.SOFTWARE

    $sysHive = Mount-OfflineHive -HivePath $systemHivePath   -Label "SYS"
    $sftHive = Mount-OfflineHive -HivePath $softwareHivePath -Label "SFT"

    try {
        # --- 2.1 Informacion del SO ---
        Write-StepHeader "2.1 Version del Sistema Operativo"
        if ($sftHive) {
            try {
                $cv  = Get-OfflineRegProperty -HiveRoot $sftHive -SubKey 'Microsoft\Windows NT\CurrentVersion'
                $osInfo = [PSCustomObject]@{
                    Caption          = "$($cv.ProductName)"
                    Version          = "$($cv.CurrentMajorVersionNumber).$($cv.CurrentMinorVersionNumber)"
                    BuildNumber      = "$($cv.CurrentBuildNumber)"
                    UBR              = "$($cv.UBR)"
                    DisplayVersion   = "$($cv.DisplayVersion)"
                    ReleaseId        = "$($cv.ReleaseId)"
                    RegisteredOwner  = "$($cv.RegisteredOwner)"
                    RegisteredOrg    = "$($cv.RegisteredOrganization)"
                    InstallDate      = if ($cv.InstallDate) { [System.DateTimeOffset]::FromUnixTimeSeconds($cv.InstallDate).LocalDateTime.ToString($Script:Defaults.DateFormatShort) } else { "" }
                    Architecture     = if ((Test-Path (Join-Path $Script:TargetRoot "Windows\SysWOW64"))) { "64-bit" } else { "32-bit" }
                    OSType           = "OFFLINE_ACQUISITION"
                }
                $Script:OFSession.TargetOS = $osInfo.Caption
                Export-CollectionToJSON -Data $osInfo -OutputPath (Join-Path $phasePath "OSInfo.json") -Phase "FASE_2" -Description "Version SO"
                $collected++
            } catch {
                Write-ForensicLog -Phase "FASE_2" -Action "OS_INFO_ERROR" -Target $_.Exception.Message -Result "ERROR"
            }
        }

        # --- 2.2 Nombre del equipo ---
        Write-StepHeader "2.2 Nombre del Equipo"
        if ($sysHive) {
            try {
                $cs = Get-OfflineControlSet -SystemHiveRoot $sysHive
                $cn = Get-OfflineRegProperty -HiveRoot $sysHive -SubKey "$cs\Control\ComputerName\ComputerName" -Name "ComputerName"
                if ($cn) {
                    $Script:OFSession.TargetComputer = $cn
                    [PSCustomObject]@{ ComputerName = $cn } |
                        Export-Csv -Path (Join-Path $phasePath "ComputerName.csv") -NoTypeInformation -Encoding UTF8 -Force
                    Write-ForensicLog -Phase "FASE_2" -Action "COMPUTERNAME" -Target $cn -Result "OK"
                    $collected++
                }

                # Actualizar OSInfo.json con CSName para compatibilidad con AN
                $osInfoPath = Join-Path $phasePath "OSInfo.json"
                if (Test-Path $osInfoPath) {
                    $osObj = Get-Content $osInfoPath -Raw | ConvertFrom-Json
                    $osObj | Add-Member -NotePropertyName "CSName" -NotePropertyValue $cn -Force
                    $osObj | ConvertTo-Json -Depth 5 | Out-File $osInfoPath -Encoding UTF8 -Force
                }
            } catch {
                Write-ForensicLog -Phase "FASE_2" -Action "COMPUTERNAME_ERROR" -Target $_.Exception.Message -Result "ERROR"
            }
        }

        # --- 2.3 Zona horaria ---
        Write-StepHeader "2.3 Zona Horaria"
        if ($sysHive) {
            try {
                $cs  = Get-OfflineControlSet -SystemHiveRoot $sysHive
                $tz  = Get-OfflineRegProperty -HiveRoot $sysHive -SubKey "$cs\Control\TimeZoneInformation"
                if ($tz) {
                    [PSCustomObject]@{
                        TimeZoneKeyName = $tz.TimeZoneKeyName
                        Bias            = $tz.Bias
                        ActiveTimeBias  = $tz.ActiveTimeBias
                    } | Export-Csv -Path (Join-Path $phasePath "TimeZone.csv") -NoTypeInformation -Encoding UTF8 -Force
                    Write-ForensicLog -Phase "FASE_2" -Action "TIMEZONE" -Target "$($tz.TimeZoneKeyName)" -Result "OK"
                    $collected++
                }
            } catch {
                Write-ForensicLog -Phase "FASE_2" -Action "TIMEZONE_ERROR" -Target $_.Exception.Message -Result "WARNING"
            }
        }

        # --- 2.4 Ultimo apagado ---
        Write-StepHeader "2.4 Fecha de Ultimo Apagado"
        if ($sysHive) {
            try {
                $cs       = Get-OfflineControlSet -SystemHiveRoot $sysHive
                $shutdown = Get-OfflineRegProperty -HiveRoot $sysHive -SubKey "$cs\Control\Windows" -Name "ShutdownTime"
                if ($shutdown) {
                    $ftBytes = [byte[]]$shutdown
                    $ft      = [System.BitConverter]::ToInt64($ftBytes, 0)
                    $dt      = [System.DateTime]::FromFileTimeUtc($ft)
                    [PSCustomObject]@{ UltimoApagadoUTC = $dt.ToString($Script:Defaults.DateFormatShort) } |
                        Export-Csv -Path (Join-Path $phasePath "LastShutdown.csv") -NoTypeInformation -Encoding UTF8 -Force
                    Write-ForensicLog -Phase "FASE_2" -Action "ULTIMO_APAGADO" -Target $dt.ToString($Script:Defaults.DateFormatShort) -Result "OK"
                    $collected++
                }
            } catch {
                Write-ForensicLog -Phase "FASE_2" -Action "SHUTDOWN_ERROR" -Target $_.Exception.Message -Result "WARNING"
            }
        }

        # --- 2.5 Software instalado ---
        Write-StepHeader "2.5 Software Instalado"
        if ($sftHive) {
            try {
                $installed = @()
                foreach ($hiveKey in @('Microsoft\Windows\CurrentVersion\Uninstall', 'WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall')) {
                    $keys = Get-OfflineRegChildren -HiveRoot $sftHive -SubKey $hiveKey
                    foreach ($k in $keys) {
                        $p = Get-ItemProperty -Path $k.PSPath -ErrorAction SilentlyContinue
                        if ($p -and $p.DisplayName) {
                            $installed += [PSCustomObject]@{
                                DisplayName    = $p.DisplayName
                                DisplayVersion = $p.DisplayVersion
                                Publisher      = $p.Publisher
                                InstallDate    = $p.InstallDate
                                InstallLocation= $p.InstallLocation
                                UninstallString= $p.UninstallString
                                Hive           = $hiveKey -replace '.*Uninstall', 'Uninstall'
                            }
                        }
                    }
                }
                if ($installed.Count -gt 0) {
                    Export-CollectionToCSV -Data $installed -OutputPath (Join-Path $phasePath "InstalledSoftware.csv") -Phase "FASE_2" -Description "Software instalado"
                    $collected++
                }
            } catch {
                Write-ForensicLog -Phase "FASE_2" -Action "SOFTWARE_ERROR" -Target $_.Exception.Message -Result "ERROR"
            }
        }

        # --- 2.6 Historial de redes ---
        Write-StepHeader "2.6 Historial de Redes"
        if ($sftHive) {
            try {
                $networks = @()
                $netKeys  = Get-OfflineRegChildren -HiveRoot $sftHive -SubKey 'Microsoft\Windows NT\CurrentVersion\NetworkList\Profiles'
                foreach ($k in $netKeys) {
                    $p = Get-ItemProperty -Path $k.PSPath -ErrorAction SilentlyContinue
                    if ($p) {
                        $networks += [PSCustomObject]@{
                            ProfileName = $p.ProfileName
                            Description = $p.Description
                            Category    = $p.Category
                            DateCreated = $p.DateCreated
                            DateLastConnected = $p.DateLastConnected
                        }
                    }
                }
                if ($networks.Count -gt 0) {
                    Export-CollectionToCSV -Data $networks -OutputPath (Join-Path $phasePath "NetworkHistory.csv") -Phase "FASE_2" -Description "Historial redes"
                    $collected++
                }
            } catch {
                Write-ForensicLog -Phase "FASE_2" -Action "NETWORKS_ERROR" -Target $_.Exception.Message -Result "WARNING"
            }
        }

    } finally {
        if ($sysHive) { Dismount-OfflineHive -RegistryPath $sysHive }
        if ($sftHive) { Dismount-OfflineHive -RegistryPath $sftHive }
    }

    Write-PhaseComplete -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum] -ItemsCollected $collected
    return $collected
}
#endregion ===== src\Phases\Phase2-SystemInfo.ps1 =====

#region ===== src\Phases\Phase3-Filesystem.ps1 =====
#region ============================================================
# MaPeriT.OF - FASE 3: SISTEMA DE FICHEROS
# Copia $MFT del disco objetivo (misma ruta que SW -> AN puede procesarlo)
# ============================================================

function Invoke-Phase3-Filesystem {
    param([string]$OutputPath)

    $phasePath = Join-Path $OutputPath $Script:OutputDirs.Filesystem
    $phaseNum  = 3
    $collected = 0

    Write-PhaseHeader -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum]

    # --- 3.1 Copia del $MFT ---
    Write-StepHeader "3.1 Copia del `$MFT (Master File Table)"
    try {
        $sw      = [System.Diagnostics.Stopwatch]::StartNew()
        $mftSrc  = Join-Path $Script:TargetRoot '$MFT'
        $mftDest = Join-Path $phasePath '$MFT'

        $ok = Copy-ForensicFileBackup -SourcePath $mftSrc -DestPath $mftDest -Phase "FASE_3"
        $sw.Stop()

        if ($ok) {
            $sizeMB = [math]::Round((Get-Item $mftDest).Length / 1MB, 1)
            Write-ForensicLog -Phase "FASE_3" -Action "MFT_COPIADO" -Target '$MFT' -Result "OK" `
                -Detail "$sizeMB MB en $($sw.ElapsedMilliseconds)ms" -DurationMs $sw.ElapsedMilliseconds
            $collected++
        } else {
            Write-ForensicLog -Phase "FASE_3" -Action "MFT_NO_COPIADO" -Target $mftSrc -Result "WARNING" `
                -Detail "Requiere privilegios de administrador y volumen accesible"
        }
    } catch {
        Write-ForensicLog -Phase "FASE_3" -Action "MFT_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 3.2 Listado de directorios raiz ---
    Write-StepHeader "3.2 Listado de Directorios Raiz"
    try {
        $dirs = Get-ChildItem -Path $Script:TargetRoot -ErrorAction SilentlyContinue |
            Select-Object Name, FullName, Attributes, CreationTime, LastWriteTime, LastAccessTime,
                @{N='SizeKB';E={ if ($_.PSIsContainer) { 0 } else { [math]::Round($_.Length/1KB,1) } }}
        Export-CollectionToCSV -Data $dirs -OutputPath (Join-Path $phasePath "RootDirectories.csv") -Phase "FASE_3" -Description "Directorios raiz"
        $collected++
    } catch {
        Write-ForensicLog -Phase "FASE_3" -Action "ROOTDIRS_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    # --- 3.3 Prefetch ---
    Write-StepHeader "3.3 Ficheros Prefetch"
    try {
        $prefetchSrc  = Join-Path $Script:TargetRoot $Script:TargetPaths.Prefetch
        $prefetchDest = Join-Path $phasePath "Prefetch"
        if (Test-Path $prefetchSrc) {
            $n = Copy-RobocopyDir -SourceDir $prefetchSrc -DestDir $prefetchDest -Filter "*.pf" -Phase "FASE_3"
            if ($n -gt 0) { $collected++ }
        } else {
            Write-ForensicLog -Phase "FASE_3" -Action "PREFETCH_NO_ENCONTRADO" -Target $prefetchSrc -Result "WARNING"
        }
    } catch {
        Write-ForensicLog -Phase "FASE_3" -Action "PREFETCH_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    # --- 3.4 Ficheros recientes modificados (ultimos 30 dias) ---
    Write-StepHeader "3.4 Ficheros Recientes del Sistema"
    try {
        $since  = (Get-Date).AddDays(-30)
        $sysDir = Join-Path $Script:TargetRoot "Windows\System32"
        $recent = Get-ChildItem -Path $sysDir -File -ErrorAction SilentlyContinue |
            Where-Object { $_.LastWriteTime -gt $since -and $_.Extension -in @('.exe','.dll','.sys','.ps1','.bat','.cmd') } |
            Select-Object Name, FullName, Extension, Length, CreationTime, LastWriteTime, Attributes |
            Sort-Object LastWriteTime -Descending | Select-Object -First 500
        if ($recent.Count -gt 0) {
            Export-CollectionToCSV -Data $recent -OutputPath (Join-Path $phasePath "RecentSystemFiles.csv") -Phase "FASE_3" -Description "Ficheros sistema recientes"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_3" -Action "RECENT_FILES_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    # --- 3.5 Ejecutables en ubicaciones sospechosas ---
    Write-StepHeader "3.5 Ejecutables en Ubicaciones Sospechosas"
    try {
        $suspiciousPaths = @(
            Join-Path $Script:TargetRoot "Users\*\AppData\Roaming",
            Join-Path $Script:TargetRoot "Users\*\AppData\Local\Temp",
            Join-Path $Script:TargetRoot "Windows\Temp",
            Join-Path $Script:TargetRoot "ProgramData"
        )
        $exes = @()
        foreach ($pattern in $suspiciousPaths) {
            $exes += Get-ChildItem -Path $pattern -Include @('*.exe','*.dll','*.ps1','*.bat','*.vbs') `
                -Recurse -ErrorAction SilentlyContinue -Depth 3 |
                Select-Object Name, FullName, Length, CreationTime, LastWriteTime
        }
        if ($exes.Count -gt 0) {
            Export-CollectionToCSV -Data $exes -OutputPath (Join-Path $phasePath "SuspiciousExecutables.csv") -Phase "FASE_3" -Description "Ejecutables sospechosos"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_3" -Action "SUSPICIOUS_EXE_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    # --- 3.6 Papelera de reciclaje ---
    Write-StepHeader "3.6 Papelera de Reciclaje"
    try {
        $recyclers = @()
        $recyclePaths = Get-ChildItem -Path (Join-Path $Script:TargetRoot '$Recycle.Bin') -Directory -ErrorAction SilentlyContinue
        foreach ($sid in $recyclePaths) {
            $items = Get-ChildItem $sid.FullName -File -ErrorAction SilentlyContinue
            foreach ($item in $items) {
                $recyclers += [PSCustomObject]@{
                    SID        = $sid.Name
                    FileName   = $item.Name
                    FullPath   = $item.FullName
                    Size       = $item.Length
                    DeletedTime= $item.LastWriteTime
                }
            }
        }
        if ($recyclers.Count -gt 0) {
            Export-CollectionToCSV -Data $recyclers -OutputPath (Join-Path $phasePath "RecycleBin.csv") -Phase "FASE_3" -Description "Papelera de reciclaje"
            $collected++
        }
    } catch {
        Write-ForensicLog -Phase "FASE_3" -Action "RECYCLEBIN_ERROR" -Target $_.Exception.Message -Result "WARNING"
    }

    Write-PhaseComplete -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum] -ItemsCollected $collected
    return $collected
}
#endregion ===== src\Phases\Phase3-Filesystem.ps1 =====

#region ===== src\Phases\Phase4-Registry.ps1 =====
#region ============================================================
# MaPeriT.OF - FASE 4: REGISTRO DE WINDOWS OFFLINE
# Carga hives del disco objetivo y exporta artefactos clave
# ============================================================

function Invoke-Phase4-Registry {
    param([string]$OutputPath)

    $phasePath = Join-Path $OutputPath $Script:OutputDirs.Registry
    $phaseNum  = 4
    $collected = 0

    Write-PhaseHeader -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum]

    $sysHivePath = Join-Path $Script:TargetRoot $Script:HiveFiles.SYSTEM
    $sftHivePath = Join-Path $Script:TargetRoot $Script:HiveFiles.SOFTWARE

    $sysHive = Mount-OfflineHive -HivePath $sysHivePath -Label "SYS4"
    $sftHive = Mount-OfflineHive -HivePath $sftHivePath -Label "SFT4"

    try {
        $cs = if ($sysHive) { Get-OfflineControlSet -SystemHiveRoot $sysHive } else { "ControlSet001" }

        # --- 4.1 Claves Run/RunOnce (SYSTEM-wide) ---
        Write-StepHeader "4.1 Autoarranque - Run/RunOnce (SOFTWARE)"
        if ($sftHive) {
            foreach ($runKey in @('Microsoft\Windows\CurrentVersion\Run', 'Microsoft\Windows\CurrentVersion\RunOnce',
                                  'Microsoft\Windows\CurrentVersion\Policies\Explorer\Run')) {
                $vals = Get-OfflineRegProperty -HiveRoot $sftHive -SubKey $runKey
                if ($vals) {
                    $rows = $vals.PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } |
                        ForEach-Object { [PSCustomObject]@{ Hive='SOFTWARE'; Key=$runKey; Name=$_.Name; Value=$_.Value } }
                    $outFile = Join-Path $phasePath "Run_SOFTWARE_$(($runKey -split '\\')[-1]).csv"
                    $rows | Export-Csv $outFile -NoTypeInformation -Encoding UTF8 -Force
                    Write-ForensicLog -Phase "FASE_4" -Action "RUN_KEY" -Target $runKey -Result "OK" -Detail "$($rows.Count) entradas"
                    $collected++
                }
            }
        }

        # --- 4.2 Winlogon ---
        Write-StepHeader "4.2 Winlogon"
        if ($sftHive) {
            try {
                $wl = Get-OfflineRegProperty -HiveRoot $sftHive -SubKey 'Microsoft\Windows NT\CurrentVersion\Winlogon'
                if ($wl) {
                    $wl.PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } |
                        ForEach-Object { [PSCustomObject]@{ Name=$_.Name; Value=$_.Value } } |
                        Export-Csv (Join-Path $phasePath "Winlogon.csv") -NoTypeInformation -Encoding UTF8 -Force
                    Write-ForensicLog -Phase "FASE_4" -Action "WINLOGON" -Target "Winlogon" -Result "OK"
                    $collected++
                }
            } catch {
                Write-ForensicLog -Phase "FASE_4" -Action "WINLOGON_ERROR" -Target $_.Exception.Message -Result "WARNING"
            }
        }

        # --- 4.3 Servicios instalados ---
        Write-StepHeader "4.3 Servicios"
        if ($sysHive) {
            try {
                $services = @()
                $svcKeys  = Get-OfflineRegChildren -HiveRoot $sysHive -SubKey "$cs\Services"
                foreach ($k in $svcKeys) {
                    $p = Get-ItemProperty -Path $k.PSPath -ErrorAction SilentlyContinue
                    if ($p) {
                        $services += [PSCustomObject]@{
                            ServiceName  = $k.PSChildName
                            DisplayName  = $p.DisplayName
                            ImagePath    = $p.ImagePath
                            Start        = $p.Start
                            Type         = $p.Type
                            Description  = $p.Description
                        }
                    }
                }
                if ($services.Count -gt 0) {
                    Export-CollectionToCSV -Data $services -OutputPath (Join-Path $phasePath "Services.csv") -Phase "FASE_4" -Description "Servicios"
                    $collected++
                }
            } catch {
                Write-ForensicLog -Phase "FASE_4" -Action "SERVICES_ERROR" -Target $_.Exception.Message -Result "ERROR"
            }
        }

        # --- 4.4 Ultima sesion de usuario (LastLoggedOn) ---
        Write-StepHeader "4.4 Ultimo Usuario con Sesion"
        if ($sftHive) {
            try {
                $ll = Get-OfflineRegProperty -HiveRoot $sftHive `
                    -SubKey 'Microsoft\Windows\CurrentVersion\Authentication\LogonUI'
                if ($ll) {
                    [PSCustomObject]@{
                        LastLoggedOnUser          = $ll.LastLoggedOnUser
                        LastLoggedOnDisplayName   = $ll.LastLoggedOnDisplayName
                        LastLoggedOnSAMUser       = $ll.LastLoggedOnSAMUser
                    } | Export-Csv (Join-Path $phasePath "LastLoggedOn.csv") -NoTypeInformation -Encoding UTF8 -Force
                    Write-ForensicLog -Phase "FASE_4" -Action "LAST_LOGON" -Target "$($ll.LastLoggedOnUser)" -Result "OK"
                    $collected++
                }
            } catch {
                Write-ForensicLog -Phase "FASE_4" -Action "LAST_LOGON_ERROR" -Target $_.Exception.Message -Result "WARNING"
            }
        }

        # --- 4.5 AppCompatCache (ShimCache) ---
        Write-StepHeader "4.5 AppCompatCache (ShimCache)"
        if ($sysHive) {
            try {
                $acc = Get-OfflineRegProperty -HiveRoot $sysHive `
                    -SubKey "$cs\Control\Session Manager\AppCompatCache" -Name "AppCompatCache"
                if ($acc) {
                    $binPath = Join-Path $phasePath "AppCompatCache.bin"
                    [System.IO.File]::WriteAllBytes($binPath, [byte[]]$acc)
                    $hash = Get-ForensicHash -FilePath $binPath
                    Add-ChainOfCustodyEntry -SourcePath "REGISTRY:AppCompatCache" -DestPath $binPath -Hash $hash -Method "reg load" -FileSize ([byte[]]$acc).Length
                    # BUG CONFIRMADO: los parentesis estaban mal colocados -
                    # "$([byte[]]$acc).Length bytes" interpola el ARRAY DE
                    # BYTES completo como texto (todos los bytes en decimal,
                    # potencialmente varios MB) y concatena ".Length bytes"
                    # como texto literal detras, en vez de evaluar la longitud.
                    Write-ForensicLog -Phase "FASE_4" -Action "SHIMCACHE_EXTRAIDO" -Target "AppCompatCache.bin" -Result "OK" -Detail "$(([byte[]]$acc).Length) bytes"
                    $collected++
                }
            } catch {
                Write-ForensicLog -Phase "FASE_4" -Action "SHIMCACHE_ERROR" -Target $_.Exception.Message -Result "WARNING"
            }
        }

        # --- 4.6 NTUSER.DAT por usuario ---
        Write-StepHeader "4.6 Registro de Usuarios (NTUSER.DAT)"
        foreach ($user in Get-TargetUserPaths) {
            $ntuserPath = Join-Path $user.Root $Script:HiveFiles.NTUSER
            if (-not (Test-Path $ntuserPath)) {
                Write-ForensicLog -Phase "FASE_4" -Action "NTUSER_NO_ENCONTRADO" -Target "$($user.Name)\NTUSER.DAT" -Result "WARNING"
                continue
            }
            $ntHive = Mount-OfflineHive -HivePath $ntuserPath -Label "NT_$($user.Name)"
            if (-not $ntHive) { continue }
            try {
                $userRegPath = Join-Path $phasePath "Users\$($user.Name)"
                if (-not (Test-Path $userRegPath)) { New-Item -ItemType Directory -Path $userRegPath -Force | Out-Null }

                # Run/RunOnce de usuario
                foreach ($runKey in @('Software\Microsoft\Windows\CurrentVersion\Run', 'Software\Microsoft\Windows\CurrentVersion\RunOnce')) {
                    $vals = Get-OfflineRegProperty -HiveRoot $ntHive -SubKey $runKey
                    if ($vals) {
                        $rows = $vals.PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } |
                            ForEach-Object { [PSCustomObject]@{ Usuario=$user.Name; Key=$runKey; Name=$_.Name; Value=$_.Value } }
                        $rows | Export-Csv (Join-Path $userRegPath "Run_$($user.Name).csv") -NoTypeInformation -Encoding UTF8 -Force
                        Write-ForensicLog -Phase "FASE_4" -Action "USER_RUN_KEY" -Target "$($user.Name)" -Result "OK" -Detail "$($rows.Count) entradas"
                        $collected++
                    }
                }

                # RecentDocs
                try {
                    $rd    = Get-OfflineRegChildren -HiveRoot $ntHive -SubKey 'Software\Microsoft\Windows\CurrentVersion\Explorer\RecentDocs'
                    $rdRows = @()
                    foreach ($k in $rd) {
                        $p = Get-ItemProperty $k.PSPath -ErrorAction SilentlyContinue
                        if ($p) { $rdRows += [PSCustomObject]@{ Extension=$k.PSChildName; MRUListEx=$p.MRUListEx } }
                    }
                    if ($rdRows.Count -gt 0) {
                        $rdRows | Export-Csv (Join-Path $userRegPath "RecentDocs_$($user.Name).csv") -NoTypeInformation -Encoding UTF8 -Force
                        Write-ForensicLog -Phase "FASE_4" -Action "RECENT_DOCS" -Target $user.Name -Result "OK"
                        $collected++
                    }
                } catch {}

                # TypedPaths (Explorer address bar)
                try {
                    $tp = Get-OfflineRegProperty -HiveRoot $ntHive -SubKey 'Software\Microsoft\Windows\CurrentVersion\Explorer\TypedPaths'
                    if ($tp) {
                        $tp.PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } |
                            ForEach-Object { [PSCustomObject]@{ Usuario=$user.Name; Entrada=$_.Name; Ruta=$_.Value } } |
                            Export-Csv (Join-Path $userRegPath "TypedPaths_$($user.Name).csv") -NoTypeInformation -Encoding UTF8 -Force
                        Write-ForensicLog -Phase "FASE_4" -Action "TYPED_PATHS" -Target $user.Name -Result "OK"
                        $collected++
                    }
                } catch {}

                # RDP Client - servidores a los que se ha conectado
                try {
                    $rdpServers = Get-OfflineRegChildren -HiveRoot $ntHive -SubKey 'Software\Microsoft\Terminal Server Client\Servers'
                    if ($rdpServers) {
                        $rdpServers | Select-Object @{N='Usuario';E={$user.Name}}, @{N='Servidor';E={$_.PSChildName}} |
                            Export-Csv (Join-Path $userRegPath "RDP_Servers_$($user.Name).csv") -NoTypeInformation -Encoding UTF8 -Force
                        Write-ForensicLog -Phase "FASE_4" -Action "RDP_SERVERS" -Target $user.Name -Result "OK"
                        $collected++
                    }
                } catch {}

            } finally {
                Dismount-OfflineHive -RegistryPath $ntHive
            }
        }

    } finally {
        if ($sysHive) { Dismount-OfflineHive -RegistryPath $sysHive }
        if ($sftHive) { Dismount-OfflineHive -RegistryPath $sftHive }
    }

    Write-PhaseComplete -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum] -ItemsCollected $collected
    return $collected
}
#endregion ===== src\Phases\Phase4-Registry.ps1 =====

#region ===== src\Phases\Phase5-EventLogs.ps1 =====
#region ============================================================
# MaPeriT.OF - FASE 5: LOGS DE EVENTOS
# Copia directa de .evtx desde el disco objetivo (misma ruta que SW)
# ============================================================

function Invoke-Phase5-EventLogs {
    param([string]$OutputPath)

    $phasePath = Join-Path $OutputPath $Script:OutputDirs.EventLogs
    $phaseNum  = 5
    $collected = 0

    Write-PhaseHeader -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum]

    $evtxSource = Join-Path $Script:TargetRoot $Script:TargetPaths.EventLogs

    if (-not (Test-Path $evtxSource)) {
        Write-ForensicLog -Phase "FASE_5" -Action "EVTX_DIR_NO_ENCONTRADO" -Target $evtxSource -Result "WARNING"
        Write-PhaseComplete -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum] -ItemsCollected 0
        return 0
    }

    Write-StepHeader "5.1 Copia de ficheros .evtx"

    $evtxFiles = Get-ChildItem $evtxSource -Filter "*.evtx" -ErrorAction SilentlyContinue |
        Sort-Object Length -Descending

    Write-ForensicLog -Phase "FASE_5" -Action "EVTX_ENCONTRADOS" -Target "$($evtxFiles.Count) ficheros" -Result "OK"

    # Priorizar logs de seguridad y sistema
    $priority = @("Security.evtx","System.evtx","Application.evtx",
                  "Microsoft-Windows-PowerShell%4Operational.evtx",
                  "Microsoft-Windows-TaskScheduler%4Operational.evtx",
                  "Microsoft-Windows-TerminalServices-RemoteConnectionManager%4Operational.evtx",
                  "Microsoft-Windows-Sysmon%4Operational.evtx",
                  "Microsoft-Windows-Windows Defender%4Operational.evtx")

    $ordered = @()
    foreach ($p in $priority) {
        $match = $evtxFiles | Where-Object { $_.Name -eq $p }
        if ($match) { $ordered += $match }
    }
    $ordered += $evtxFiles | Where-Object { $_.Name -notin $priority }

    $limitFiles = [Math]::Min($ordered.Count, $Script:Defaults.MaxEvtxFiles)
    $sw         = [System.Diagnostics.Stopwatch]::StartNew()

    foreach ($evtx in $ordered | Select-Object -First $limitFiles) {
        $dest = Join-Path $phasePath $evtx.Name
        if (Copy-ForensicFile -SourcePath $evtx.FullName -DestPath $dest -Phase "FASE_5" -Method "evtx_copy") {
            $collected++
        }
    }
    $sw.Stop()

    if ($ordered.Count -gt $limitFiles) {
        Write-ForensicLog -Phase "FASE_5" -Action "EVTX_LIMITE" -Target "$limitFiles de $($ordered.Count)" -Result "WARNING" `
            -Detail "Limite de $($Script:Defaults.MaxEvtxFiles) ficheros alcanzado"
    }

    Write-ForensicLog -Phase "FASE_5" -Action "EVTX_COMPLETADO" -Target "$collected ficheros copiados" -Result "OK" -DurationMs $sw.ElapsedMilliseconds

    Write-PhaseComplete -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum] -ItemsCollected $collected
    return $collected
}
#endregion ===== src\Phases\Phase5-EventLogs.ps1 =====

#region ===== src\Phases\Phase6-Browsers.ps1 =====
#region ============================================================
# MaPeriT.OF - FASE 6: NAVEGADORES WEB
# Copia artefactos de navegadores desde perfiles de usuario del disco objetivo
# ============================================================

function Invoke-Phase6-Browsers {
    param([string]$OutputPath)

    $phasePath = Join-Path $OutputPath $Script:OutputDirs.Browsers
    $phaseNum  = 6
    $collected = 0

    Write-PhaseHeader -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum]

    foreach ($user in Get-TargetUserPaths) {
        Write-StepHeader "6.x Navegadores - $($user.Name)"

        # --- Chromium (Chrome, Edge, Brave) ---
        foreach ($browser in @('Chrome','Edge','Brave')) {
            $profileBase = Join-Path $user.Root $Script:BrowserPaths[$browser]
            if (-not (Test-Path $profileBase)) { continue }

            $profiles = @("Default") + (Get-ChildItem $profileBase -Directory -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -match '^Profile \d+$' } | Select-Object -ExpandProperty Name)

            foreach ($profile in $profiles) {
                $profilePath = Join-Path $profileBase $profile
                if (-not (Test-Path $profilePath)) { continue }

                $destBase = Join-Path $phasePath "$($user.Name)\$browser\$profile"

                foreach ($file in $Script:ChromiumFiles) {
                    $src = Join-Path $profilePath $file
                    if (Test-Path $src) {
                        $dest = Join-Path $destBase $file
                        if (Copy-ForensicFile -SourcePath $src -DestPath $dest -Phase "FASE_6" -Method "browser_copy") {
                            $collected++
                        }
                    }
                }
            }
        }

        # --- Firefox ---
        $ffBase = Join-Path $user.Root $Script:BrowserPaths.Firefox
        if (Test-Path $ffBase) {
            $ffProfiles = Get-ChildItem $ffBase -Directory -ErrorAction SilentlyContinue
            foreach ($ffProf in $ffProfiles) {
                $destBase = Join-Path $phasePath "$($user.Name)\Firefox\$($ffProf.Name)"
                foreach ($file in $Script:FirefoxFiles) {
                    $src = Join-Path $ffProf.FullName $file
                    if (Test-Path $src) {
                        $dest = Join-Path $destBase $file
                        if (Copy-ForensicFile -SourcePath $src -DestPath $dest -Phase "FASE_6" -Method "browser_copy") {
                            $collected++
                        }
                    }
                }
            }
        }

        # --- Internet Explorer / WebCache ---
        $ieBase = Join-Path $user.Root $Script:BrowserPaths.IE
        if (Test-Path $ieBase) {
            $destIE = Join-Path $phasePath "$($user.Name)\IE"
            $n = Copy-ForensicDirectory -SourceDir $ieBase -DestDir $destIE -Phase "FASE_6" -Filter "*.dat" -Recurse
            if ($n -gt 0) { $collected += $n }
        }

        if ($collected -gt 0) {
            Write-ForensicLog -Phase "FASE_6" -Action "BROWSERS_USUARIO" -Target $user.Name -Result "OK" -Detail "$collected artefactos"
        }
    }

    Write-PhaseComplete -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum] -ItemsCollected $collected
    return $collected
}
#endregion ===== src\Phases\Phase6-Browsers.ps1 =====

#region ===== src\Phases\Phase7-Communications.ps1 =====
#region ============================================================
# MaPeriT.OF - FASE 7: COMUNICACIONES
# Copia artefactos de Outlook, Teams, Skype, Discord desde disco objetivo
# ============================================================

function Invoke-Phase7-Communications {
    param([string]$OutputPath)

    $phasePath = Join-Path $OutputPath $Script:OutputDirs.Communications
    $phaseNum  = 7
    $collected = 0

    Write-PhaseHeader -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum]

    foreach ($user in Get-TargetUserPaths) {
        Write-StepHeader "7.x Comunicaciones - $($user.Name)"

        foreach ($app in $Script:CommunicationPaths.Keys) {
            $srcPath = Join-Path $user.Root $Script:CommunicationPaths[$app]
            if (-not (Test-Path $srcPath)) { continue }

            $destPath = Join-Path $phasePath "$($user.Name)\$app"

            # PST/OST: respetar flag -CopyLargeFiles
            if ($app -in @('OutlookOST','OutlookPST')) {
                $extensions = @('*.ost','*.pst','*.nst')
                foreach ($ext in $extensions) {
                    $files = Get-ChildItem $srcPath -Filter $ext -ErrorAction SilentlyContinue
                    foreach ($f in $files) {
                        $sizeMB = [math]::Round($f.Length / 1MB, 1)
                        if ($sizeMB -gt $Script:Defaults.LargeFileThresholdMB -and -not $CopyLargeFiles) {
                            Write-ForensicLog -Phase "FASE_7" -Action "FICHERO_GRANDE_OMITIDO" `
                                -Target "$($user.Name)\$($f.Name)" -Result "WARNING" `
                                -Detail "$sizeMB MB - Use -CopyLargeFiles para incluir"
                            continue
                        }
                        $dest = Join-Path $destPath $f.Name
                        if (Copy-ForensicFile -SourcePath $f.FullName -DestPath $dest -Phase "FASE_7" -Method "outlook_copy") {
                            $collected++
                        }
                    }
                }
            } else {
                # Teams, Skype, Discord: copiar directorio de configuracion y logs
                $n = Copy-ForensicDirectory -SourceDir $srcPath -DestDir $destPath -Phase "FASE_7" `
                    -Filter "*.json" -Recurse -MaxSizeMB 50
                $n += Copy-ForensicDirectory -SourceDir $srcPath -DestDir $destPath -Phase "FASE_7" `
                    -Filter "*.db" -Recurse -MaxSizeMB 100
                $n += Copy-ForensicDirectory -SourceDir $srcPath -DestDir $destPath -Phase "FASE_7" `
                    -Filter "*.log" -Recurse -MaxSizeMB 20
                if ($n -gt 0) { $collected += $n }
            }
        }
    }

    Write-PhaseComplete -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum] -ItemsCollected $collected
    return $collected
}
#endregion ===== src\Phases\Phase7-Communications.ps1 =====

#region ===== src\Phases\Phase8-CloudStorage.ps1 =====
#region ============================================================
# MaPeriT.OF - FASE 8: ALMACENAMIENTO EN LA NUBE
# Copia logs y configuracion de OneDrive, Google Drive, Dropbox
# ============================================================

function Invoke-Phase8-CloudStorage {
    param([string]$OutputPath)

    $phasePath = Join-Path $OutputPath $Script:OutputDirs.CloudStorage
    $phaseNum  = 8
    $collected = 0

    Write-PhaseHeader -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum]

    foreach ($user in Get-TargetUserPaths) {
        Write-StepHeader "8.x Cloud Storage - $($user.Name)"

        foreach ($service in $Script:CloudPaths.Keys) {
            $srcPath = Join-Path $user.Root $Script:CloudPaths[$service]
            if (-not (Test-Path $srcPath)) { continue }

            $destPath = Join-Path $phasePath "$($user.Name)\$service"

            # Logs y configuracion (sin datos de usuario)
            $n = 0
            foreach ($ext in @('*.log','*.txt','*.json','*.db','*.sqlite','*.xml','*.ini','*.dat')) {
                $n += Copy-ForensicDirectory -SourceDir $srcPath -DestDir $destPath `
                    -Filter $ext -Phase "FASE_8" -Recurse -MaxSizeMB 50
            }
            if ($n -gt 0) {
                $collected += $n
                Write-ForensicLog -Phase "FASE_8" -Action "CLOUD_COPIADO" -Target "$($user.Name)\$service" -Result "OK" -Detail "$n ficheros"
            }
        }

        # OneDrive: tambien guardar la base de datos de sincronizacion si existe
        $odSyncDB = Join-Path $user.Root "AppData\Local\Microsoft\OneDrive\*.db"
        $dbFiles  = Get-Item $odSyncDB -ErrorAction SilentlyContinue
        foreach ($db in $dbFiles) {
            $dest = Join-Path $phasePath "$($user.Name)\OneDriveSync\$($db.Name)"
            if (Copy-ForensicFile -SourcePath $db.FullName -DestPath $dest -Phase "FASE_8") { $collected++ }
        }
    }

    Write-PhaseComplete -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum] -ItemsCollected $collected
    return $collected
}
#endregion ===== src\Phases\Phase8-CloudStorage.ps1 =====

#region ===== src\Phases\Phase9-USB.ps1 =====
#region ============================================================
# MaPeriT.OF - FASE 9: DISPOSITIVOS USB
# Lee USBSTOR, USB y MountedDevices desde hive SYSTEM del disco objetivo
# ============================================================

function Invoke-Phase9-USB {
    param([string]$OutputPath)

    $phasePath = Join-Path $OutputPath $Script:OutputDirs.USB
    $phaseNum  = 9
    $collected = 0

    Write-PhaseHeader -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum]

    $sysHivePath = Join-Path $Script:TargetRoot $Script:HiveFiles.SYSTEM
    $sysHive     = Mount-OfflineHive -HivePath $sysHivePath -Label "SYS9"

    if (-not $sysHive) {
        Write-ForensicLog -Phase "FASE_9" -Action "HIVE_NO_DISPONIBLE" -Target "SYSTEM" -Result "ERROR"
        Write-PhaseComplete -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum] -ItemsCollected 0
        return 0
    }

    try {
        $cs = Get-OfflineControlSet -SystemHiveRoot $sysHive

        # --- 9.1 USBSTOR (historial de dispositivos USB de almacenamiento) ---
        Write-StepHeader "9.1 USBSTOR - Dispositivos de Almacenamiento USB"
        try {
            $usbstorDevices = @()
            $usbstorKeys    = Get-OfflineRegChildren -HiveRoot $sysHive -SubKey "$cs\Enum\USBSTOR"
            foreach ($typeKey in $usbstorKeys) {
                $deviceKeys = Get-ChildItem $typeKey.PSPath -ErrorAction SilentlyContinue
                foreach ($devKey in $deviceKeys) {
                    $p = Get-ItemProperty $devKey.PSPath -ErrorAction SilentlyContinue
                    if ($p) {
                        $usbstorDevices += [PSCustomObject]@{
                            DeviceType       = $typeKey.PSChildName
                            SerialNumber     = $devKey.PSChildName
                            FriendlyName     = $p.FriendlyName
                            Manufacturer     = ($typeKey.PSChildName -split '&')[0]
                            Model            = ($typeKey.PSChildName -split '&')[1]
                            HardwareID       = ($p.HardwareID | Out-String).Trim()
                            Service          = $p.Service
                            ContainerID      = $p.ContainerID
                        }
                    }
                }
            }
            if ($usbstorDevices.Count -gt 0) {
                Export-CollectionToCSV -Data $usbstorDevices -OutputPath (Join-Path $phasePath "USBSTOR.csv") -Phase "FASE_9" -Description "Dispositivos USBSTOR"
                $collected++
            }
        } catch {
            Write-ForensicLog -Phase "FASE_9" -Action "USBSTOR_ERROR" -Target $_.Exception.Message -Result "ERROR"
        }

        # --- 9.2 USB (todos los dispositivos USB) ---
        Write-StepHeader "9.2 USB - Todos los Dispositivos USB"
        try {
            $usbDevices = @()
            $usbKeys    = Get-OfflineRegChildren -HiveRoot $sysHive -SubKey "$cs\Enum\USB"
            foreach ($vidKey in $usbKeys) {
                $pidKeys = Get-ChildItem $vidKey.PSPath -ErrorAction SilentlyContinue
                foreach ($pidKey in $pidKeys) {
                    $instanceKeys = Get-ChildItem $pidKey.PSPath -ErrorAction SilentlyContinue
                    foreach ($inst in $instanceKeys) {
                        $p = Get-ItemProperty $inst.PSPath -ErrorAction SilentlyContinue
                        if ($p) {
                            $usbDevices += [PSCustomObject]@{
                                VID_PID      = "$($vidKey.PSChildName)&$($pidKey.PSChildName)"
                                Instance     = $inst.PSChildName
                                FriendlyName = $p.FriendlyName
                                Manufacturer = $p.Mfg
                                Service      = $p.Service
                                ContainerID  = $p.ContainerID
                            }
                        }
                    }
                }
            }
            if ($usbDevices.Count -gt 0) {
                Export-CollectionToCSV -Data $usbDevices -OutputPath (Join-Path $phasePath "USB_Devices.csv") -Phase "FASE_9" -Description "Dispositivos USB"
                $collected++
            }
        } catch {
            Write-ForensicLog -Phase "FASE_9" -Action "USB_ERROR" -Target $_.Exception.Message -Result "WARNING"
        }

        # --- 9.3 MountedDevices (asignacion de letras de unidad) ---
        Write-StepHeader "9.3 MountedDevices"
        try {
            $md    = Get-OfflineRegProperty -HiveRoot $sysHive -SubKey "MountedDevices"
            if ($md) {
                $mdRows = $md.PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } |
                    ForEach-Object {
                        [PSCustomObject]@{
                            DriveLetter = $_.Name
                            DataSize    = if ($_.Value -is [byte[]]) { ([byte[]]$_.Value).Length } else { 0 }
                            DataHex     = if ($_.Value -is [byte[]]) { [System.BitConverter]::ToString([byte[]]$_.Value) -replace '-','' } else { $_.Value }
                        }
                    }
                Export-CollectionToCSV -Data $mdRows -OutputPath (Join-Path $phasePath "MountedDevices.csv") -Phase "FASE_9" -Description "MountedDevices"
                $collected++
            }
        } catch {
            Write-ForensicLog -Phase "FASE_9" -Action "MOUNTED_DEVICES_ERROR" -Target $_.Exception.Message -Result "WARNING"
        }

    } finally {
        Dismount-OfflineHive -RegistryPath $sysHive
    }

    # --- 9.4 SetupAPI log (evidencia cruda, para paridad con MaPeriT.SW) ---
    # Antes esta fase solo leia el registro (USBSTOR/USB/MountedDevices) pero no
    # preservaba setupapi.dev.log en si -- SW si lo copia y parsea en su Fase 9.
    # Se copia el fichero completo (cadena de custodia) y se extraen los eventos de
    # instalacion USB en streaming linea a linea, sin cargar el log completo en
    # memoria (puede acumular cientos de MB en equipos con muchos anos de uso).
    Write-StepHeader "9.4 SetupAPI Log"
    try {
        $setupApiSrc = Join-Path $Script:TargetRoot "Windows\INF\setupapi.dev.log"
        if (Test-Path $setupApiSrc) {
            $setupApiDest = Join-Path $phasePath "setupapi.dev.log"
            if (Copy-ForensicFile -SourcePath $setupApiSrc -DestPath $setupApiDest -Phase "FASE_9" -Method "setupapi_copy") {
                $collected++
            }

            $usbInstallEvents = @()
            $currentSection = ""
            $currentTimestamp = ""
            $fs = [System.IO.File]::Open($setupApiSrc, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete)
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
                            Timestamp   = $currentTimestamp
                            Seccion     = $currentSection.Trim('> []')
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
        } else {
            Write-ForensicLog -Phase "FASE_9" -Action "SETUPAPI_NO_ENCONTRADO" -Target $setupApiSrc -Result "WARNING"
        }
    } catch {
        Write-ForensicLog -Phase "FASE_9" -Action "SETUPAPI_ERROR" -Target $_.Exception.Message -Result "ERROR"
    }

    Write-PhaseComplete -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum] -ItemsCollected $collected
    return $collected
}
#endregion ===== src\Phases\Phase9-USB.ps1 =====

#region ===== src\Phases\Phase10-Persistence.ps1 =====
#region ============================================================
# MaPeriT.OF - FASE 10: MECANISMOS DE PERSISTENCIA
# Tareas programadas, servicios, Winlogon, DLL hijacking, etc.
# ============================================================

function Invoke-Phase10-Persistence {
    param([string]$OutputPath)

    $phasePath = Join-Path $OutputPath $Script:OutputDirs.Persistence
    $phaseNum  = 10
    $collected = 0

    Write-PhaseHeader -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum]

    $sftHivePath = Join-Path $Script:TargetRoot $Script:HiveFiles.SOFTWARE
    $sysHivePath = Join-Path $Script:TargetRoot $Script:HiveFiles.SYSTEM
    $sftHive     = Mount-OfflineHive -HivePath $sftHivePath -Label "SFT10"
    $sysHive     = Mount-OfflineHive -HivePath $sysHivePath -Label "SYS10"

    try {
        $cs = if ($sysHive) { Get-OfflineControlSet -SystemHiveRoot $sysHive } else { "ControlSet001" }

        # --- 10.1 Tareas programadas (XML en disco) ---
        Write-StepHeader "10.1 Tareas Programadas"
        try {
            $tasksSrc  = Join-Path $Script:TargetRoot $Script:TargetPaths.Tasks
            $tasksDest = Join-Path $phasePath "ScheduledTasks"
            if (Test-Path $tasksSrc) {
                $n = Copy-RobocopyDir -SourceDir $tasksSrc -DestDir $tasksDest -Filter "*" -Phase "FASE_10" -Recurse
                # Parsear XMLs para resumen
                $taskSummary = @()
                Get-ChildItem $tasksDest -Filter "*.xml" -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
                    try {
                        [xml]$xml = Get-Content $_.FullName -ErrorAction SilentlyContinue
                        $taskSummary += [PSCustomObject]@{
                            TaskName    = $_.BaseName
                            Author      = $xml.Task.RegistrationInfo.Author
                            Description = $xml.Task.RegistrationInfo.Description
                            Command     = $xml.Task.Actions.Exec.Command
                            Arguments   = $xml.Task.Actions.Exec.Arguments
                            Enabled     = $xml.Task.Settings.Enabled
                        }
                    } catch {}
                }
                if ($taskSummary.Count -gt 0) {
                    Export-CollectionToCSV -Data $taskSummary -OutputPath (Join-Path $phasePath "ScheduledTasksSummary.csv") -Phase "FASE_10" -Description "Resumen tareas"
                    $collected++
                }
                if ($n -gt 0) { $collected++ }
            } else {
                Write-ForensicLog -Phase "FASE_10" -Action "TASKS_NO_ENCONTRADO" -Target $tasksSrc -Result "WARNING"
            }
        } catch {
            Write-ForensicLog -Phase "FASE_10" -Action "TASKS_ERROR" -Target $_.Exception.Message -Result "ERROR"
        }

        # --- 10.2 Image File Execution Options (IFEO) - inyeccion de debugger ---
        Write-StepHeader "10.2 Image File Execution Options"
        if ($sftHive) {
            try {
                $ifeoKeys = Get-OfflineRegChildren -HiveRoot $sftHive -SubKey 'Microsoft\Windows NT\CurrentVersion\Image File Execution Options'
                $ifeoRows = @()
                foreach ($k in $ifeoKeys) {
                    $p = Get-ItemProperty $k.PSPath -ErrorAction SilentlyContinue
                    if ($p -and $p.Debugger) {
                        $ifeoRows += [PSCustomObject]@{ Executable=$k.PSChildName; Debugger=$p.Debugger; GlobalFlag=$p.GlobalFlag }
                    }
                }
                if ($ifeoRows.Count -gt 0) {
                    Export-CollectionToCSV -Data $ifeoRows -OutputPath (Join-Path $phasePath "IFEO_Debuggers.csv") -Phase "FASE_10" -Description "IFEO Debuggers"
                    $collected++
                }
            } catch {
                Write-ForensicLog -Phase "FASE_10" -Action "IFEO_ERROR" -Target $_.Exception.Message -Result "WARNING"
            }
        }

        # --- 10.3 AppInit_DLLs ---
        Write-StepHeader "10.3 AppInit_DLLs"
        if ($sftHive) {
            try {
                $appInit = Get-OfflineRegProperty -HiveRoot $sftHive `
                    -SubKey 'Microsoft\Windows NT\CurrentVersion\Windows' -Name "AppInit_DLLs"
                if ($appInit) {
                    [PSCustomObject]@{ AppInit_DLLs = $appInit } |
                        Export-Csv (Join-Path $phasePath "AppInit_DLLs.csv") -NoTypeInformation -Encoding UTF8 -Force
                    Write-ForensicLog -Phase "FASE_10" -Action "APPINIT_DLLS" -Target "$appInit" -Result "OK"
                    $collected++
                }
            } catch {
                Write-ForensicLog -Phase "FASE_10" -Action "APPINIT_ERROR" -Target $_.Exception.Message -Result "WARNING"
            }
        }

        # --- 10.4 LSA Providers ---
        Write-StepHeader "10.4 LSA Security Packages"
        if ($sysHive) {
            try {
                $lsa = Get-OfflineRegProperty -HiveRoot $sysHive -SubKey "$cs\Control\Lsa"
                if ($lsa) {
                    [PSCustomObject]@{
                        AuthenticationPackages = ($lsa.Authentication_Packages | Out-String).Trim()
                        SecurityPackages       = ($lsa.Security_Packages | Out-String).Trim()
                        NotificationPackages   = ($lsa.Notification_Packages | Out-String).Trim()
                    } | Export-Csv (Join-Path $phasePath "LSA_Providers.csv") -NoTypeInformation -Encoding UTF8 -Force
                    Write-ForensicLog -Phase "FASE_10" -Action "LSA_PROVIDERS" -Target "LSA" -Result "OK"
                    $collected++
                }
            } catch {
                Write-ForensicLog -Phase "FASE_10" -Action "LSA_ERROR" -Target $_.Exception.Message -Result "WARNING"
            }
        }

        # --- 10.5 BAM/DAM (Background Activity Monitor) ---
        Write-StepHeader "10.5 BAM - Background Activity Monitor"
        if ($sysHive) {
            try {
                $bamRoot = Get-OfflineRegChildren -HiveRoot $sysHive -SubKey "$cs\Services\bam\State\UserSettings"
                $bamRows = @()
                foreach ($sidKey in $bamRoot) {
                    $p = Get-ItemProperty $sidKey.PSPath -ErrorAction SilentlyContinue
                    if ($p) {
                        $p.PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' -and $_.Name -match '\\' } | ForEach-Object {
                            $bamRows += [PSCustomObject]@{ SID=$sidKey.PSChildName; Executable=$_.Name }
                        }
                    }
                }
                if ($bamRows.Count -gt 0) {
                    Export-CollectionToCSV -Data $bamRows -OutputPath (Join-Path $phasePath "BAM.csv") -Phase "FASE_10" -Description "BAM"
                    $collected++
                }
            } catch {
                Write-ForensicLog -Phase "FASE_10" -Action "BAM_ERROR" -Target $_.Exception.Message -Result "WARNING"
            }
        }

        # --- 10.6 Run keys de usuarios ---
        Write-StepHeader "10.6 Run Keys de Usuarios (NTUSER)"
        foreach ($user in Get-TargetUserPaths) {
            $ntuserPath = Join-Path $user.Root $Script:HiveFiles.NTUSER
            if (-not (Test-Path $ntuserPath)) { continue }
            $ntHive = Mount-OfflineHive -HivePath $ntuserPath -Label "NT10_$($user.Name)"
            if (-not $ntHive) { continue }
            try {
                foreach ($runKey in @('Software\Microsoft\Windows\CurrentVersion\Run','Software\Microsoft\Windows\CurrentVersion\RunOnce')) {
                    $vals = Get-OfflineRegProperty -HiveRoot $ntHive -SubKey $runKey
                    if ($vals) {
                        $vals.PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } |
                            ForEach-Object { [PSCustomObject]@{ Usuario=$user.Name; Key=$runKey; Name=$_.Name; Value=$_.Value } } |
                            Export-Csv (Join-Path $phasePath "Run_$($user.Name)_Persistence.csv") -NoTypeInformation -Encoding UTF8 -Append -Force
                        Write-ForensicLog -Phase "FASE_10" -Action "USER_RUN_PERSISTENCE" -Target $user.Name -Result "OK"
                        $collected++
                    }
                }
            } finally {
                Dismount-OfflineHive -RegistryPath $ntHive
            }
        }

    } finally {
        if ($sftHive) { Dismount-OfflineHive -RegistryPath $sftHive }
        if ($sysHive) { Dismount-OfflineHive -RegistryPath $sysHive }
    }

    Write-PhaseComplete -PhaseNumber $phaseNum -PhaseName $Script:PhaseNames[$phaseNum] -ItemsCollected $collected
    return $collected
}
#endregion ===== src\Phases\Phase10-Persistence.ps1 =====

#region ===== src\Report\ChainOfCustody.ps1 =====
#region ============================================================
# MaPeriT.OF - CADENA DE CUSTODIA
# Mismo formato que SW -> HashManifest.csv compatible con AN
# ============================================================

$Script:ChainOfCustody = [System.Collections.Generic.List[PSCustomObject]]::new()
$Script:EvidenceCounter = 0

function Add-ChainOfCustodyEntry {
    param(
        [string]$SourcePath,
        [string]$DestPath,
        [string]$Hash,
        [string]$Method   = "CopyItem",
        [long]$FileSize   = 0
    )
    $Script:EvidenceCounter++

    # Validar formato del hash antes de dejarlo constar en el acta de custodia (mismo
    # patron ya aplicado en MaPeriT.SW). Get-ForensicHash devuelve el texto literal
    # "ERROR_HASH" si el calculo falla; sin esta comprobacion ese texto quedaria escrito
    # en la columna SHA256 del manifiesto como si fuese un hash real, comprometiendo la
    # validez pericial de esa evidencia sin que nadie lo note.
    $isValidHash = $Hash -match '^[A-Fa-f0-9]{64}$'
    if (-not $isValidHash) {
        Write-ForensicLog -Action "HASH_INVALIDO_EN_CUSTODIA" -Target "$DestPath -> '$Hash'" -Result "ERROR" -Detail "El valor de hash no tiene formato SHA-256 valido (64 hex); revisar manualmente, puede que el fichero se haya eliminado o modificado tras la copia"
    }
    $hashField = if ($isValidHash) { $Hash } else { "INVALIDO:$Hash" }

    # Ruta relativa a la raiz de salida (anclada por prefijo, insensible a mayusculas),
    # para que -VerifyPath pueda reconstruir la ubicacion del fichero aunque la carpeta
    # de evidencias se haya movido o copiado a otra unidad para su presentacion judicial
    # -- DestPath por si solo queda "congelado" en la ruta absoluta del momento de la
    # adquisicion y deja de ser util si el perito mueve la carpeta despues.
    $relativePath = Get-RelativeEvidencePath -FullPath $DestPath -RootPath $Script:OFSession.OutputPath

    $entry = [PSCustomObject]@{
        EvidenceID        = "OF-{0:D5}" -f $Script:EvidenceCounter
        SourcePath        = $SourcePath
        DestPath          = $DestPath
        RelativePath      = $relativePath
        SHA256            = $hashField
        AcquisitionMethod = $Method
        Timestamp         = (Get-Date).ToString($Script:Defaults.DateFormat)
        FileSize          = $FileSize
    }
    $Script:ChainOfCustody.Add($entry)
}

function Export-HashManifest {
    param([string]$OutputPath, [string]$HMACKey = "")

    $manifestPath = Join-Path $OutputPath "HashManifest.csv"
    $Script:ChainOfCustody | Export-Csv -Path $manifestPath -NoTypeInformation -Encoding UTF8 -Force

    $hash = Get-ForensicHash -FilePath $manifestPath
    Write-ForensicLog -Action "HASH_MANIFEST_GENERADO" -Target "HashManifest.csv" -Result "OK" `
        -Detail "$($Script:ChainOfCustody.Count) evidencias | SHA256=$hash"

    if ($HMACKey) {
        Write-HMACSignature -ManifestPath $manifestPath -Key $HMACKey
    }

    return $manifestPath
}
#endregion ===== src\Report\ChainOfCustody.ps1 =====

#region ===== src\Report\ReportTemplates.ps1 =====
#region ============================================================
# MaPeriT.OF - PLANTILLAS HTML/CSS DEL INFORME
# Mismo diseño que MaPeriT.SW para coherencia visual.
# ============================================================

function Get-OFCss {
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
.sb-brand{padding:20px 18px 16px;border-bottom:1px solid rgba(255,255,255,.08)}
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
nav a:hover{color:#fff;border-left-color:#3b82f6;background:rgba(255,255,255,.04)}
.sb-footer{
  margin-top:auto;padding:14px 18px;border-top:1px solid rgba(255,255,255,.06);
  font-size:10px;color:#334155;line-height:1.6;
}

/* ── MAIN ── */
.main{margin-left:var(--sw);flex:1;min-width:0}

/* ── COVER ── */
.cover{
  background:linear-gradient(135deg,#0f172a 0%,#1e3a5f 50%,#0f172a 100%);
  color:#fff;padding:72px 64px;min-height:580px;
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
.stat-card.ok{border-top-color:var(--c-ok)}
.stat-card.warn{border-top-color:var(--c-medium)}
.stat-card.err{border-top-color:var(--c-critical)}
.sv{font-size:34px;font-weight:800;line-height:1;color:var(--c-accent)}
.stat-card.ok .sv{color:var(--c-ok)}
.stat-card.warn .sv{color:var(--c-medium)}
.stat-card.err .sv{color:var(--c-critical)}
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

/* ── PHASES TABLE ── */
.phases-table{width:100%;border-collapse:collapse;background:var(--c-surface);border-radius:var(--r);box-shadow:var(--sh);overflow:hidden}
.phases-table th{background:#1e293b;color:#e2e8f0;padding:10px 14px;font-size:12px;font-weight:600;text-align:left}
.phases-table td{padding:9px 14px;border-bottom:1px solid var(--c-border);font-size:13px}
.phases-table tr:last-child td{border-bottom:none}
.phases-table tr:hover td{background:#f8fafc}

/* ── BADGES ── */
.badge{display:inline-flex;align-items:center;padding:2px 9px;border-radius:4px;font-size:11px;font-weight:600;white-space:nowrap}
.b-ok{background:#dcfce7;color:#15803d}
.b-na{background:#f3f4f6;color:#6b7280}
.b-info{background:#dbeafe;color:#1d4ed8}

/* ── DIR TREE ── */
.dir-tree{
  font-family:'Cascadia Code','Fira Code','Courier New',monospace;
  font-size:12px;background:#f8fafc;
  border:1px solid var(--c-border);border-radius:var(--r);
  padding:16px 20px;line-height:1.9;color:var(--c-text);
  overflow-x:auto;white-space:pre;margin:10px 0 20px;
}
.dir-tree .folder{color:var(--c-accent);font-weight:600}
.dir-tree .file{color:var(--c-muted)}
.dir-tree .highlight-file{color:var(--c-critical);font-weight:600}

/* ── ALERTS ── */
p{margin:8px 0;text-align:justify}
.alert{padding:10px 14px;border-radius:var(--r);margin:10px 0;font-size:13px}
.alert-warn{background:#fffbeb;border:1px solid #fef3c7;color:#92400e}
.alert-info{background:#eff6ff;border:1px solid #dbeafe;color:#1d4ed8}
.alert-ok{background:#f0fdf4;border:1px solid #dcfce7;color:#15803d}
.alert-danger{background:#fef2f2;border:1px solid #fecaca;color:#991b1b}

/* ── SIGNATURE ── */
.sig-grid{display:grid;grid-template-columns:1fr 1fr;gap:48px;margin-top:60px;padding-top:36px;border-top:2px solid var(--c-border)}
.sig-item{text-align:center}
.sig-line{border-top:1px solid #94a3b8;margin-top:70px;padding-top:8px;font-size:13px}

/* ── FOOTER ── */
.report-footer{
  text-align:center;font-size:11px;color:var(--c-muted);
  border-top:1px solid var(--c-border);padding:20px 56px;
}

/* ── PRINT ── */
@media print{
  .sidebar{display:none}
  .main{margin-left:0}
  .content{padding:20px}
  .cover{background:#0f172a!important;-webkit-print-color-adjust:exact;print-color-adjust:exact;padding:40px}
  .cover h1{font-size:32px}
  .page-break{page-break-before:always}
  .info-table,.phases-table{box-shadow:none}
}
@page{size:A4;margin:1.5cm}
'@
}

function Get-OFJs {
    return @'
(function(){
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
#endregion ===== src\Report\ReportTemplates.ps1 =====

#region ===== src\Report\ReportGenerator.ps1 =====
#region ============================================================
# MaPeriT.OF - GENERADOR DEL INFORME PERICIAL HTML
# Mismo esquema visual que MaPeriT.SW
# ============================================================

function New-OFReport {
    param([string]$OutputPath, [string]$EvidenceDescription)

    $reportDir  = Join-Path $OutputPath $Script:OutputDirs.Report
    $reportFile = Join-Path $reportDir "InformePericial.html"

    $duration = if ($Script:OFSession.EndTime -and $Script:OFSession.StartTime) {
        ($Script:OFSession.EndTime - $Script:OFSession.StartTime).ToString('hh\:mm\:ss')
    } else { "N/A" }

    $totalSizeMB = [math]::Round($Script:OFSession.TotalSizeBytes / 1MB, 2)

    # BUG CONFIRMADO: CaseNumber/ExaminerName/TargetDisk/TargetComputer/TargetOS
    # se insertaban en el HTML sin HtmlEncode (a diferencia de EvidenceDescription,
    # que si lo tenia) - un caracter como &lt; &gt; &amp; " en cualquiera de estos
    # campos de texto libre del perito rompe la estructura del informe pericial.
    # Se codifican una sola vez aqui y se usan estas variables en todo el informe.
    $caseNumberEnc     = [System.Web.HttpUtility]::HtmlEncode($Script:OFSession.CaseNumber)
    $examinerNameEnc   = [System.Web.HttpUtility]::HtmlEncode($Script:OFSession.ExaminerName)
    $targetDiskEnc     = [System.Web.HttpUtility]::HtmlEncode($Script:OFSession.TargetDisk)
    $targetComputerEnc = [System.Web.HttpUtility]::HtmlEncode($Script:OFSession.TargetComputer)
    $targetOSEnc       = [System.Web.HttpUtility]::HtmlEncode($Script:OFSession.TargetOS)

    Write-ForensicLog -Action "GENERANDO_INFORME_OF" -Target $reportFile -Result "OK"

    $css = Get-OFCss
    $js  = Get-OFJs
    $html = [System.Text.StringBuilder]::new(200000)

    # ── HEAD ──────────────────────────────────────────────────────────────
    [void]$html.AppendLine(@"
<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Informe Forense Offline - Caso $caseNumberEnc</title>
<style>$css</style>
</head>
<body>
"@)

    # ── SIDEBAR ───────────────────────────────────────────────────────────
    [void]$html.AppendLine(@"
<nav class="sidebar">
  <div class="sb-brand">
    <div class="sb-brand-name">MaPeriT.OF</div>
    <div class="sb-brand-sub">Adquisicion Forense Offline v$($Script:TOOL_VERSION)</div>
  </div>
  <div class="sb-case">
    <strong>Caso</strong>$caseNumberEnc
    <strong style="margin-top:6px">Perito</strong>$examinerNameEnc
    <strong style="margin-top:6px">Disco objetivo</strong>$targetDiskEnc
  </div>
  <nav>
    <div class="sb-label">Informe</div>
    <a href="#cover">Portada</a>
    <a href="#sec1">1. Identificacion del Caso</a>
    <a href="#sec2">2. Metodologia</a>
    <a href="#sec3">3. Fases de Adquisicion</a>
    <a href="#sec4">4. Perfiles de Usuario</a>
    <a href="#sec5">5. Estadisticas</a>
    <a href="#sec6">6. Estructura de Salida</a>
    <div class="sb-label">Legal</div>
    <a href="#sec7">7. Aviso Legal</a>
  </nav>
  <div class="sb-footer">
    $((Get-Date).ToString('dd/MM/yyyy HH:mm'))<br>
    Duracion: $duration<br>
    $($Script:OFSession.TotalFiles) ficheros &middot; $totalSizeMB MB
  </div>
</nav>
<div class="main">
"@)

    # ── PORTADA ───────────────────────────────────────────────────────────
    [void]$html.AppendLine(@"
<div id="cover" class="cover" data-sec>
  <div class="cover-badge">Informe Pericial Informatico Forense Offline</div>
  <h1>MaPeriT.OF</h1>
  <h2>Adquisicion Forense Offline &mdash; Caso $caseNumberEnc</h2>
  <div class="cover-grid">
    <div class="cover-item"><label>Numero de Caso</label><span>$caseNumberEnc</span></div>
    <div class="cover-item"><label>Perito Informatico</label><span>$examinerNameEnc</span></div>
    <div class="cover-item"><label>Fecha de Adquisicion</label><span>$(Get-Date -Format 'dd/MM/yyyy')</span></div>
    <div class="cover-item"><label>Tipo de Adquisicion</label><span>Offline (disco montado)</span></div>
    <div class="cover-item"><label>Disco Objetivo</label><span>$targetDiskEnc</span></div>
    <div class="cover-item"><label>Equipo Objetivo</label><span>$targetComputerEnc</span></div>
    <div class="cover-item"><label>SO del Disco</label><span>$targetOSEnc</span></div>
    <div class="cover-item"><label>Duracion</label><span>$duration</span></div>
  </div>
  <div class="cover-hash">
    SHA-256 herramienta: $($Script:OFSession.ScriptHash)
  </div>
  <div class="confidential">Documento Confidencial &mdash; Cadena de Custodia de Evidencias Digitales</div>
</div>

<div class="content">
"@)

    # ── SEC 1: IDENTIFICACION DEL CASO ────────────────────────────────────
    [void]$html.AppendLine(@"
<div id="sec1" data-sec>
<div class="section-hdr"><div class="sec-num">1</div><h2>Identificacion del Caso</h2></div>
<table class="info-table">
  <tr><td>Numero de Caso</td><td><strong>$caseNumberEnc</strong></td></tr>
  <tr><td>Perito Informatico</td><td>$examinerNameEnc</td></tr>
  <tr><td>Fecha de Adquisicion</td><td>$(Get-Date -Format 'dd/MM/yyyy HH:mm:ss')</td></tr>
  <tr><td>Disco Objetivo</td><td>$targetDiskEnc</td></tr>
  <tr><td>SO del Disco Objetivo</td><td>$targetOSEnc</td></tr>
  <tr><td>Nombre del Equipo Objetivo</td><td>$targetComputerEnc</td></tr>
  <tr><td>Herramienta</td><td>$($Script:TOOL_FULL)</td></tr>
  <tr><td>Hash SHA-256 Herramienta</td><td style="font-family:monospace;font-size:12px;word-break:break-all">$($Script:OFSession.ScriptHash)</td></tr>
  <tr><td>Duracion Adquisicion</td><td>$duration</td></tr>
  <tr><td>Tipo de Adquisicion</td><td>Offline (disco montado en equipo forense)</td></tr>
  $(if ($EvidenceDescription) { "<tr><td>Descripcion del Encargo</td><td>$([System.Web.HttpUtility]::HtmlEncode($EvidenceDescription))</td></tr>" })
</table>
</div>
"@)

    # ── SEC 2: METODOLOGIA ────────────────────────────────────────────────
    [void]$html.AppendLine(@"
<div id="sec2" data-sec class="page-break">
<div class="section-hdr"><div class="sec-num">2</div><h2>Metodologia</h2></div>
<div class="alert alert-info">
  <strong>Adquisicion Offline:</strong> La presente adquisicion se ha realizado sobre un disco duro fisico
  montado en un equipo forense dedicado, sin arrancar el sistema operativo del disco objetivo.
  Este procedimiento garantiza que no se modifican los datos del disco objetivo durante la adquisicion.
</div>
<p>El presente analisis forense se ha realizado siguiendo las directrices establecidas en la
<strong>RFC 3227</strong> (Guidelines for Evidence Collection and Archiving) del IETF y la guia
<strong>NIST SP 800-86</strong> (Guide to Integrating Forensic Techniques into Incident Response).</p>
<p>En adquisicion offline no estan disponibles los datos volatiles (Fase 1: RAM, procesos activos,
conexiones de red). El resto de fases (2-10) se han ejecutado accediendo directamente a los ficheros
del disco objetivo.</p>
<p>El registro de Windows se ha accedido mediante carga offline de hives (SYSTEM, SOFTWARE, NTUSER.DAT)
usando <code>reg load</code>, sin modificar los hives originales. Todas las evidencias han sido
verificadas mediante hash SHA-256.</p>

<div class="sub-hdr">2.1 Herramienta Utilizada</div>
<table class="info-table">
  <tr><td>Herramienta</td><td><strong>$($Script:TOOL_FULL)</strong></td></tr>
  <tr><td>Hash SHA-256</td><td style="font-family:monospace;font-size:12px;word-break:break-all">$($Script:OFSession.ScriptHash)</td></tr>
  <tr><td>Normativa aplicada</td><td>RFC 3227 (IETF) &bull; NIST SP 800-86</td></tr>
</table>
</div>
"@)

    # ── SEC 3: FASES DE ADQUISICION ───────────────────────────────────────
    [void]$html.AppendLine(@"
<div id="sec3" data-sec class="page-break">
<div class="section-hdr"><div class="sec-num">3</div><h2>Fases de Adquisicion</h2></div>
<table class="phases-table">
<thead><tr><th style="width:60px">Fase</th><th>Descripcion</th><th style="width:160px">Estado</th></tr></thead>
<tbody>
<tr><td>Fase 1</td><td>Datos Volatiles</td><td><span class="badge b-na">N/A (offline)</span></td></tr>
"@)

    foreach ($ph in 2..10) {
        $name = $Script:PhaseNames[$ph]
        if ($name) {
            [void]$html.AppendLine("<tr><td>Fase $ph</td><td>$([System.Web.HttpUtility]::HtmlEncode($name))</td><td><span class='badge b-ok'>&#10003; Completada</span></td></tr>")
        }
    }

    [void]$html.AppendLine("</tbody></table></div>")

    # ── SEC 4: PERFILES DE USUARIO ────────────────────────────────────────
    [void]$html.AppendLine(@"
<div id="sec4" data-sec class="page-break">
<div class="section-hdr"><div class="sec-num">4</div><h2>Perfiles de Usuario Detectados</h2></div>
<p>Usuarios detectados en el disco objetivo mediante analisis de perfiles en <code>Users\</code>
y de los hives NTUSER.DAT. Total: <strong>$($Script:OFSession.ProcessedUsers.Count)</strong> perfil(es).</p>
<table class="info-table">
"@)

    foreach ($u in $Script:OFSession.ProcessedUsers) {
        [void]$html.AppendLine("<tr><td>Usuario</td><td>$([System.Web.HttpUtility]::HtmlEncode($u))</td></tr>")
    }

    [void]$html.AppendLine("</table></div>")

    # ── SEC 5: ESTADISTICAS ───────────────────────────────────────────────
    [void]$html.AppendLine(@"
<div id="sec5" data-sec class="page-break">
<div class="section-hdr"><div class="sec-num">5</div><h2>Estadisticas de Adquisicion</h2></div>
<div class="stat-grid">
  <div class="stat-card ok">
    <div class="sv">$($Script:OFSession.TotalFiles)</div>
    <div class="sl">Ficheros Recopilados</div>
  </div>
  <div class="stat-card">
    <div class="sv">$totalSizeMB MB</div>
    <div class="sl">Volumen Total</div>
  </div>
  <div class="stat-card">
    <div class="sv">$($Script:ChainOfCustody.Count)</div>
    <div class="sl">Entradas Cadena Custodia</div>
  </div>
  <div class="stat-card $(if($Script:OFSession.Errors -gt 0){'err'}else{'ok'})">
    <div class="sv">$($Script:OFSession.Errors)</div>
    <div class="sl">Errores</div>
  </div>
  <div class="stat-card $(if($Script:OFSession.Warnings -gt 0){'warn'}else{'ok'})">
    <div class="sv">$($Script:OFSession.Warnings)</div>
    <div class="sl">Advertencias</div>
  </div>
</div>
</div>
"@)

    # ── SEC 6: ESTRUCTURA DE SALIDA ───────────────────────────────────────
    $caseName = "MaPeriT_OF_$($Script:OFSession.CaseNumber)_$(Get-Date -Format 'yyyyMMdd_HHmmss')"
    [void]$html.AppendLine(@"
<div id="sec6" data-sec class="page-break">
<div class="section-hdr"><div class="sec-num">6</div><h2>Estructura de Salida</h2></div>
<p>OF genera una carpeta con la siguiente estructura, identica a la de MaPeriT.SW para garantizar
compatibilidad con MaPeriT.AN:</p>
<div class="dir-tree"><span class="folder">$caseName\</span>
&#9500;&#9472;&#9472; <span class="folder">01_Volatile\</span>         (nota: N/A en adquisicion offline)
&#9500;&#9472;&#9472; <span class="folder">02_SystemInfo\</span>
&#9500;&#9472;&#9472; <span class="folder">03_Filesystem\</span>
&#9474;   &#9500;&#9472;&#9472; <span class="folder">MFT\</span>
&#9474;   &#9492;&#9472;&#9472; <span class="folder">Prefetch\</span>
&#9500;&#9472;&#9472; <span class="folder">04_Registry\</span>
&#9474;   &#9492;&#9472;&#9472; <span class="folder">ExtractedValues\</span>
&#9500;&#9472;&#9472; <span class="folder">05_EventLogs\</span>
&#9500;&#9472;&#9472; <span class="folder">06_Browsers\</span>
&#9500;&#9472;&#9472; <span class="folder">07_Communications\</span>
&#9500;&#9472;&#9472; <span class="folder">08_CloudStorage\</span>
&#9500;&#9472;&#9472; <span class="folder">09_USB\</span>
&#9500;&#9472;&#9472; <span class="folder">10_Persistence\</span>
&#9500;&#9472;&#9472; <span class="folder">Analysis\</span>
&#9500;&#9472;&#9472; <span class="folder">Logs\</span>
&#9474;   &#9492;&#9472;&#9472; <span class="highlight-file">HashManifest.csv</span>
&#9492;&#9472;&#9472; <span class="folder">Report\</span>
    &#9492;&#9472;&#9472; <span class="highlight-file">InformePericial.html</span></div>
<div class="alert alert-info">MaPeriT.AN puede apuntar directamente a esta carpeta; la estructura es identica
a la generada por MaPeriT.SW. La Fase 1 incluye una nota explicativa para que AN no genere errores.</div>
</div>
"@)

    # ── SEC 7: AVISO LEGAL ────────────────────────────────────────────────
    $dateStr = (Get-Date).ToString("dd 'de' MMMM 'de' yyyy")
    [void]$html.AppendLine(@"
<div id="sec7" data-sec class="page-break">
<div class="section-hdr"><div class="sec-num">7</div><h2>Aviso Legal</h2></div>
<div class="alert alert-warn">
  <strong>AVISO LEGAL:</strong> El uso de esta herramienta debe estar autorizado por el propietario
  del equipo o por orden judicial. Todas las operaciones realizadas son de SOLO LECTURA sobre el
  disco objetivo. El uso no autorizado puede constituir un delito conforme a la legislacion vigente.
</div>
<p>Todas las evidencias han sido adquiridas siguiendo las directrices de la RFC 3227 y NIST SP 800-86,
manteniendo la cadena de custodia documentada y verificada mediante hashes SHA-256. La integridad
puede ser verificada en cualquier momento mediante el manifiesto incluido.</p>

<div class="sig-grid">
  <div class="sig-item">
    <div class="sig-line">En _______________________, a $dateStr<br><br>$examinerNameEnc<br><em>Perito Informatico</em></div>
  </div>
  <div class="sig-item">
    <div class="sig-line">Recibido: ___________________<br><br>&nbsp;<br><em>Firma y sello</em></div>
  </div>
</div>
</div>

</div><!-- /content -->

<div class="report-footer">
  Informe generado por <strong>$($Script:TOOL_FULL)</strong><br>
  Caso: $caseNumberEnc &bull; Perito: $examinerNameEnc &bull; Fecha: $(Get-Date -Format 'dd/MM/yyyy HH:mm')<br>
  Hash herramienta: <code style="font-size:10px">$($Script:OFSession.ScriptHash)</code>
</div>

</div><!-- /main -->
<script>$js</script>
</body>
</html>
"@)

    $html.ToString() | Out-File -FilePath $reportFile -Encoding UTF8 -Force
    Write-ForensicLog -Action "INFORME_OF_GENERADO" -Target "InformePericial.html" -Result "OK"
    return $reportFile
}
#endregion ===== src\Report\ReportGenerator.ps1 =====

#region ===== src\Main.ps1 =====
#region ============================================================
# MaPeriT.OF - PUNTO DE ENTRADA PRINCIPAL
# ============================================================

$ErrorActionPreference = "Continue"
$ProgressPreference    = "SilentlyContinue"

# Capturar ruta del script antes de cualquier cambio de contexto
$Script:OFSession.ScriptPath = if ($PSScriptRoot) { Join-Path $PSScriptRoot (Split-Path $MyInvocation.MyCommand.Path -Leaf) } `
                                else { $MyInvocation.MyCommand.Path }
if (-not (Test-Path $Script:OFSession.ScriptPath -ErrorAction SilentlyContinue)) {
    $Script:OFSession.ScriptPath = $MyInvocation.MyCommand.Path
}

# Unico punto de verdad de la version: Config.ps1 define TOOL_VERSION una sola vez,
# el banner solo tiene un marcador {VERSION} que se sustituye aqui.
$Script:Banner = $Script:Banner -replace '\{VERSION\}', $Script:TOOL_VERSION

# --- Modo verificacion (no toca ningun disco objetivo) ---
if (-not [string]::IsNullOrEmpty($VerifyPath)) {
    Write-Host ""
    Write-Host "  MaPeriT.OF - MODO VERIFICACION DE INTEGRIDAD" -ForegroundColor Cyan
    Write-Host "  =============================================" -ForegroundColor Cyan
    Write-Host ""

    if (-not (Test-Path $VerifyPath)) {
        Write-Host "  [-] Directorio no encontrado: $VerifyPath" -ForegroundColor Red
        exit 1
    }

    $manifestFile = Get-ChildItem -Path $VerifyPath -Filter "HashManifest.csv" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $manifestFile) {
        Write-Host "  [-] HashManifest.csv no encontrado en $VerifyPath" -ForegroundColor Red
        exit 1
    }

    Write-Host "  [*] Directorio:  $VerifyPath" -ForegroundColor White
    Write-Host "  [*] Manifiesto:  $($manifestFile.FullName)" -ForegroundColor White
    Write-Host ""

    $verifyResult = Test-HashManifest -ManifestPath $manifestFile.FullName -EvidenceRootPath $VerifyPath -ShowProgress

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
    exit 0
}

# --- Validar parametros obligatorios para modo adquisicion ---
if ([string]::IsNullOrEmpty($TargetDisk) -or [string]::IsNullOrEmpty($CaseNumber) -or [string]::IsNullOrEmpty($ExaminerName)) {
    Write-Host "  [-] ERROR: -TargetDisk, -CaseNumber y -ExaminerName son obligatorios para adquisicion." -ForegroundColor Red
    Write-Host "  Use -VerifyPath para verificar una adquisicion previa." -ForegroundColor Yellow
    exit 1
}

# --- Exclusion de rutas (-ExcludePaths) ---
if ($ExcludePaths -and $ExcludePaths.Count -gt 0) {
    $Script:ExcludePathPatterns = $ExcludePaths
    Write-Host "  [*] Exclusion de rutas activa ($($ExcludePaths.Count) patron(es)):" -ForegroundColor Yellow
    $ExcludePaths | ForEach-Object { Write-Host "      - $_" -ForegroundColor Yellow }
} else {
    $Script:ExcludePathPatterns = @()
}

# Mostrar banner
Write-Host $Script:Banner -ForegroundColor Cyan

# --- Inicializacion ---
try {
    $resolvedOutput = Initialize-OFSession `
        -TargetDisk          $TargetDisk `
        -CaseNumber          $CaseNumber `
        -ExaminerName        $ExaminerName `
        -OutputPath          $OutputPath `
        -EvidenceDescription $EvidenceDescription `
        -TargetUser          $TargetUser
} catch {
    Write-Host ""
    Write-Host "  [-] ERROR CRITICO: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ""
    exit 1
}

Show-OFBanner

# --- Ejecutar fases ---
$phaseResults = @{}

$allPhases = @(
    @{ Num=2;  Fn={ Invoke-Phase2-SystemInfo    -OutputPath $resolvedOutput } },
    @{ Num=3;  Fn={ Invoke-Phase3-Filesystem    -OutputPath $resolvedOutput } },
    @{ Num=4;  Fn={ Invoke-Phase4-Registry      -OutputPath $resolvedOutput } },
    @{ Num=5;  Fn={ Invoke-Phase5-EventLogs     -OutputPath $resolvedOutput } },
    @{ Num=6;  Fn={ Invoke-Phase6-Browsers      -OutputPath $resolvedOutput } },
    @{ Num=7;  Fn={ Invoke-Phase7-Communications -OutputPath $resolvedOutput } },
    @{ Num=8;  Fn={ Invoke-Phase8-CloudStorage  -OutputPath $resolvedOutput } },
    @{ Num=9;  Fn={ Invoke-Phase9-USB           -OutputPath $resolvedOutput } },
    @{ Num=10; Fn={ Invoke-Phase10-Persistence  -OutputPath $resolvedOutput } }
)

foreach ($phase in $allPhases) {
    if ($phase.Num -in $PhasesToSkip) {
        Write-Host ""
        Write-Host "  [~] Fase $($phase.Num) omitida (-SkipPhases)" -ForegroundColor DarkGray
        Write-ForensicLog -Phase "FASE_$($phase.Num)" -Action "FASE_OMITIDA" -Target "SkipPhases" -Result "SKIP"
        $phaseResults[$phase.Num] = 0
        continue
    }
    try {
        $phaseResults[$phase.Num] = & $phase.Fn
    } catch {
        Write-ForensicLog -Phase "FASE_$($phase.Num)" -Action "FASE_EXCEPCION" -Target $_.Exception.Message -Result "ERROR"
        $phaseResults[$phase.Num] = 0
    }
}

# --- Limpiar todos los hives cargados (seguridad ante excepciones) ---
Dismount-AllOfflineHives

# --- Generar manifiesto y firmar ---
$Script:OFSession.EndTime = Get-Date
Write-Host ""
Write-Host "  ═══════════════════════════════════════════════════════" -ForegroundColor Cyan
Write-Host "  GENERANDO MANIFIESTO Y FIRMANDO EVIDENCIAS..." -ForegroundColor Cyan

$manifestPath = Export-HashManifest -OutputPath $resolvedOutput -HMACKey $HMACKey

# --- Generar informe HTML ---
Write-Host "  GENERANDO INFORME HTML..." -ForegroundColor Cyan
$reportPath = New-OFReport -OutputPath $resolvedOutput -EvidenceDescription $EvidenceDescription

# --- Resumen final ---
$duration    = ($Script:OFSession.EndTime - $Script:OFSession.StartTime).ToString('hh\:mm\:ss')
$totalSizeMB = [math]::Round($Script:OFSession.TotalSizeBytes / 1MB, 2)

Write-Host ""
Write-Host "  =================================================================" -ForegroundColor Green
Write-Host "              ADQUISICION OFFLINE COMPLETADA                        " -ForegroundColor Green
Write-Host "  =================================================================" -ForegroundColor Green
Write-Host "  Caso              : $CaseNumber"                                   -ForegroundColor White
Write-Host "  Disco analizado   : $($Script:OFSession.TargetRoot)"               -ForegroundColor White
Write-Host "  SO objetivo       : $($Script:OFSession.TargetOS)"                 -ForegroundColor White
Write-Host "  Equipo objetivo   : $($Script:OFSession.TargetComputer)"           -ForegroundColor White
Write-Host "  Duracion          : $duration"                                     -ForegroundColor White
Write-Host "  Ficheros copiados : $($Script:OFSession.TotalFiles)"               -ForegroundColor White
Write-Host "  Tamano total      : $totalSizeMB MB"                               -ForegroundColor White
Write-Host "  Errores           : $($Script:OFSession.Errors)"                  -ForegroundColor $(if($Script:OFSession.Errors -gt 0){"Red"}else{"White"})
Write-Host "  Advertencias      : $($Script:OFSession.Warnings)"                -ForegroundColor $(if($Script:OFSession.Warnings -gt 0){"Yellow"}else{"White"})
Write-Host "  Salida            : $resolvedOutput"                               -ForegroundColor Cyan
Write-Host "  Manifiesto        : $manifestPath"                                 -ForegroundColor Cyan
Write-Host "  Informe           : $reportPath"                                   -ForegroundColor Cyan
Write-Host "  =================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Para analisis profundo ejecute MaPeriT.AN:" -ForegroundColor Yellow
Write-Host "  .\MaPeriT.AN.ps1 -EvidencePath `"$resolvedOutput`"" -ForegroundColor Yellow
Write-Host ""

Write-ForensicLog -Action "OF_COMPLETADO" -Target $resolvedOutput -Result "OK" `
    -Detail "Duracion=$duration Ficheros=$($Script:OFSession.TotalFiles) Tamano=${totalSizeMB}MB Errores=$($Script:OFSession.Errors)"
#endregion ===== src\Main.ps1 =====


