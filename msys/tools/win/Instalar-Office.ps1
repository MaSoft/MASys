#Requires -Version 5.1
<#
.SYNOPSIS
    Instalacion desatendida de Microsoft 365 Apps / Office con la Herramienta de implementacion de Office (ODT):
    genera la configuracion, descarga setup.exe de Microsoft si falta (comprobando su firma) e instala sin preguntas.

.DESCRIPTION
    - Genera configuracion.xml con los parametros (producto, idioma, arquitectura, canal, aplicaciones excluidas, equipo
      compartido, quitar Office MSI antiguo) o usa un XML propio con -ConfiguracionXml.
    - setup.exe: el de la carpeta del script o, si no esta, lo descarga de officecdn.microsoft.com y solo lo usa si esta
      firmado por Microsoft.
    - Si ya hay un Office Click-to-Run de 32 bits, instala en 32 bits (mezclar da el error 30015-4).
    - Si el producto ya esta instalado no hace nada (salvo -Forzar).
    - Opcional: sin pantallas de primer arranque, perfil de Outlook automatico y experiencias conectadas desactivadas.
    Para LogMeIn, Intune o GPO usar -SinConfirmar (sin nadie delante no puede preguntar).
    Codigos de salida: 0 bien o ya instalado, 10 falta setup.exe o la configuracion, 20 error de setup.exe,
    30 sin permisos de administrador, 40 cancelado.

.EXAMPLE
    .\Instalar-Office.ps1 -Simular
    .\Instalar-Office.ps1 -Producto O365BusinessRetail -Excluir Groove,Lync,Publisher -SinPantallasIniciales -SinConfirmar
#>

[CmdletBinding()]
param(
    [string]$Producto = "O365BusinessRetail",
    [string[]]$Idiomas = @("es-es"),
    [ValidateSet("Auto", "64", "32")][string]$Arquitectura = "Auto",
    [ValidateSet("Current", "MonthlyEnterprise", "SemiAnnual", "SemiAnnualPreview", "PerpetualVL2021", "PerpetualVL2024")][string]$Canal = "Current",
    [string[]]$Excluir = @("Groove", "Lync"),
    [switch]$EquipoCompartido,
    [switch]$MantenerMSI,
    [string]$ConfiguracionXml,
    [switch]$SinPantallasIniciales,
    [switch]$PerfilOutlookAutomatico,
    [switch]$DesactivarExperienciasConectadas,
    [switch]$Forzar,
    [switch]$Simular,
    [switch]$SinConfirmar,
    [string]$CarpetaTrabajo = (Join-Path $env:SystemRoot "Temp\InstalarOffice")
)

$ErrorActionPreference = "Stop"
$UrlSetup = "https://officecdn.microsoft.com/pr/wsus/setup.exe"
$AppsValidas = @("Access", "Excel", "Groove", "Lync", "OneDrive", "OneNote", "Outlook", "OutlookForWindows", "PowerPoint", "Publisher", "Teams", "Word", "Bing")

# ============================================================ funciones
function Write-Log {
    param([string]$Mensaje, [string]$Nivel = "INFO")
    $linea = "{0} [{1}] {2}" -f (Get-Date -Format "dd/MM/yyyy HH:mm:ss"), $Nivel, $Mensaje
    $color = @{ INFO = "Gray"; OK = "Green"; AVISO = "Yellow"; ERROR = "Red"; SIM = "Magenta" }[$Nivel]
    Write-Host $linea -ForegroundColor $color
    try { Add-Content -LiteralPath $script:Log -Value $linea -Encoding UTF8 -ErrorAction Stop } catch { }
}

function New-XmlOffice {
    <# Configuracion de la ODT a partir de los parametros (texto XML). #>
    param([string]$Producto, [string[]]$Idiomas, [string]$Bits, [string]$Canal, [string[]]$Excluir, [switch]$EquipoCompartido, [switch]$MantenerMSI)
    $x = New-Object System.Text.StringBuilder
    [void]$x.AppendLine("<Configuration>")
    [void]$x.AppendLine("  <Add OfficeClientEdition=""$Bits"" Channel=""$Canal"">")
    [void]$x.AppendLine("    <Product ID=""$([System.Security.SecurityElement]::Escape($Producto))"">")
    foreach ($i in $Idiomas) { [void]$x.AppendLine("      <Language ID=""$([System.Security.SecurityElement]::Escape($i))"" />") }
    foreach ($e in $Excluir) { [void]$x.AppendLine("      <ExcludeApp ID=""$([System.Security.SecurityElement]::Escape($e))"" />") }
    [void]$x.AppendLine("    </Product>")
    [void]$x.AppendLine("  </Add>")
    if (-not $MantenerMSI) { [void]$x.AppendLine("  <RemoveMSI />") }
    [void]$x.AppendLine("  <Property Name=""SharedComputerLicensing"" Value=""$(if ($EquipoCompartido) { 1 } else { 0 })"" />")
    [void]$x.AppendLine("  <Property Name=""FORCEAPPSHUTDOWN"" Value=""TRUE"" />")
    [void]$x.AppendLine("  <Property Name=""AUTOACTIVATE"" Value=""1"" />")
    [void]$x.AppendLine("  <Updates Enabled=""TRUE"" />")
    [void]$x.AppendLine("  <Display Level=""None"" AcceptEULA=""TRUE"" />")
    [void]$x.AppendLine("  <Logging Level=""Standard"" Path=""%temp%\InstalarOffice"" />")
    [void]$x.Append("</Configuration>")
    return $x.ToString()
}

function Get-OfficeInstalado {
    <# Office Click-to-Run instalado: productos, plataforma y version (del registro de 64 bits). #>
    try {
        $hklm = [Microsoft.Win32.RegistryKey]::OpenBaseKey("LocalMachine", "Registry64")
        $k = $hklm.OpenSubKey("SOFTWARE\Microsoft\Office\ClickToRun\Configuration")
        if (-not $k) { return $null }
        $r = [pscustomobject]@{ Productos = @(([string]$k.GetValue("ProductReleaseIds")) -split ',' | Where-Object { $_ }); Plataforma = [string]$k.GetValue("Platform"); Version = [string]$k.GetValue("VersionToReport") }
        $k.Close()
        return $r
    } catch { return $null }
}

function Get-Bits {
    <# Arquitectura a instalar: la pedida, salvo que ya haya Office C2R de 32 bits (no se pueden mezclar). #>
    param([string]$Pedida, [string]$Existente, [bool]$Sistema64)
    if ($Existente -eq "x86") { return "32" }
    if ($Existente -eq "x64") { return "64" }
    if ($Pedida -eq "Auto") { return $(if ($Sistema64) { "64" } else { "32" }) }
    if (-not $Sistema64) { return "32" }
    return $Pedida
}

function Test-FirmaMicrosoft {
    param([string]$Ruta)
    $f = Get-AuthenticodeSignature -LiteralPath $Ruta
    return ($f.Status -eq "Valid" -and $f.SignerCertificate.Subject -match "O=Microsoft Corporation")
}

# ============================================================ inicio
New-Item -ItemType Directory -Force -Path $CarpetaTrabajo | Out-Null
$script:Log = Join-Path $CarpetaTrabajo ("InstalarOffice_{0:yyyyMMdd_HHmmss}.log" -f (Get-Date))
Write-Log "=== Instalación de Office en $env:COMPUTERNAME$(if ($Simular) { ' (SIMULACIÓN: no se instala nada)' }) ==="

$esAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $esAdmin -and -not $Simular) { Write-Log "Hay que ejecutarlo como administrador (o como SYSTEM)." "ERROR"; exit 30 }
# Con powershell -File las listas llegan como un texto separado por comas
$Excluir = @($Excluir | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$Idiomas = @($Idiomas | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$malas = @($Excluir | Where-Object { $AppsValidas -notcontains $_ })
if ($malas) { Write-Log "Aplicaciones a excluir no válidas: $($malas -join ', '). Válidas: $($AppsValidas -join ', ')" "ERROR"; exit 10 }

$actual = Get-OfficeInstalado
if ($actual) { Write-Log "Office instalado: $($actual.Productos -join ', ') · $($actual.Plataforma) · versión $($actual.Version)" }
else { Write-Log "No hay Office Click-to-Run instalado." }
if ($actual -and $actual.Productos -contains $Producto -and -not $Forzar) {
    Write-Log "$Producto ya está instalado: no se hace nada (usa -Forzar para reinstalar o cambiar la configuración)." "OK"
    exit 0
}
$otros = @($actual.Productos | Where-Object { $_ -and $_ -ne $Producto })
if ($otros) { Write-Log "Ya hay otro producto de Office ($($otros -join ', ')): $Producto se añadirá junto a él, no lo sustituye. Para quitarlo, desinstálalo antes o usa un XML propio con <Remove>." "AVISO" }

# Configuracion
if ($ConfiguracionXml) {
    if (-not (Test-Path -LiteralPath $ConfiguracionXml)) { Write-Log "No se encuentra $ConfiguracionXml" "ERROR"; exit 10 }
    $xml = Get-Content -LiteralPath $ConfiguracionXml -Raw
    try { [void][xml]$xml } catch { Write-Log "El XML no es válido: $($_.Exception.Message)" "ERROR"; exit 10 }
    if ($actual -and $actual.Plataforma -eq "x86" -and $xml -match 'OfficeClientEdition="64"') {
        Write-Log "Hay Office de 32 bits instalado: se cambia OfficeClientEdition a 32 (mezclar da el error 30015-4)." "AVISO"
        $xml = $xml -replace 'OfficeClientEdition="64"', 'OfficeClientEdition="32"'
    }
} else {
    $bits = Get-Bits -Pedida $Arquitectura -Existente $(if ($actual) { $actual.Plataforma } else { "" }) -Sistema64 ([Environment]::Is64BitOperatingSystem)
    if ($Arquitectura -ne "Auto" -and $bits -ne $Arquitectura) { Write-Log "Se instalará en $bits bits en lugar de $Arquitectura (ya hay Office de $bits bits o el sistema es de 32 bits)." "AVISO" }
    $xml = New-XmlOffice -Producto $Producto -Idiomas $Idiomas -Bits $bits -Canal $Canal -Excluir $Excluir -EquipoCompartido:$EquipoCompartido -MantenerMSI:$MantenerMSI
}
$rutaXml = Join-Path $CarpetaTrabajo "configuracion.xml"
[System.IO.File]::WriteAllText($rutaXml, $xml, (New-Object System.Text.UTF8Encoding($false)))
Write-Log "Configuración ($rutaXml):"
foreach ($l in ($xml -split "`r?`n")) { Write-Host "    $l" -ForegroundColor DarkGray }

$politicas = @()
if ($SinPantallasIniciales) {
    $politicas += @{ Ruta = "HKLM:\SOFTWARE\Policies\Microsoft\office\16.0\common\general"; Nombre = "shownfirstrunoptin"; Valor = 1 }
    $politicas += @{ Ruta = "HKLM:\SOFTWARE\Policies\Microsoft\office\16.0\firstrun"; Nombre = "bootedrtm"; Valor = 1 }
    $politicas += @{ Ruta = "HKLM:\SOFTWARE\Policies\Microsoft\office\16.0\firstrun"; Nombre = "disablemovie"; Valor = 1 }
}
if ($PerfilOutlookAutomatico) { $politicas += @{ Ruta = "HKLM:\SOFTWARE\Policies\Microsoft\office\16.0\outlook\autodiscover"; Nombre = "zeroconfigexchange"; Valor = 1 } }
if ($DesactivarExperienciasConectadas) { $politicas += @{ Ruta = "HKLM:\SOFTWARE\Policies\Microsoft\office\16.0\common\privacy"; Nombre = "disconnectedstate"; Valor = 2 } }
foreach ($p in $politicas) { Write-Log "Directiva: $($p.Ruta -replace '^HKLM:\\SOFTWARE\\Policies\\Microsoft\\office\\16.0\\', '')\$($p.Nombre) = $($p.Valor)" }

if ($Simular) { Write-Log "Simulación: no se descarga ni se instala nada." "SIM"; exit 0 }
if (-not $SinConfirmar) {
    if (-not [Environment]::UserInteractive -or [Security.Principal.WindowsIdentity]::GetCurrent().IsSystem) { Write-Log "Sin nadie delante no se puede preguntar: usa -SinConfirmar." "ERROR"; exit 40 }
    if ((Read-Host "¿Instalar Office con esta configuración? Se cerrarán Word, Excel, Outlook... abiertos (S/N)") -notmatch '^(s|si|sí)$') { Write-Log "Cancelado." "AVISO"; exit 40 }
}

# setup.exe
$setup = Join-Path $PSScriptRoot "setup.exe"
if (-not $PSScriptRoot -or -not (Test-Path -LiteralPath $setup)) {
    $setup = Join-Path $CarpetaTrabajo "setup.exe"
    Write-Log "Descargando la Herramienta de implementación de Office de $UrlSetup"
    try {
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        (New-Object System.Net.WebClient).DownloadFile($UrlSetup, $setup)
    } catch { Write-Log "No se pudo descargar setup.exe: $($_.Exception.Message). Déjalo junto al script." "ERROR"; exit 10 }
}
if (-not (Test-FirmaMicrosoft $setup)) { Write-Log "$setup no está firmado por Microsoft: no se ejecuta." "ERROR"; exit 10 }
Write-Log "setup.exe firmado por Microsoft (versión $((Get-Item -LiteralPath $setup).VersionInfo.FileVersion))" "OK"

foreach ($p in $politicas) {
    try {
        if (-not (Test-Path $p.Ruta)) { New-Item -Path $p.Ruta -Force | Out-Null }
        New-ItemProperty -Path $p.Ruta -Name $p.Nombre -Value $p.Valor -PropertyType DWord -Force | Out-Null
    } catch { Write-Log "Directiva $($p.Nombre) no aplicada: $($_.Exception.Message)" "AVISO" }
}

Write-Log "Instalando (puede tardar 10-30 minutos; descarga desde la CDN de Microsoft)..."
try { $proc = Start-Process -FilePath $setup -ArgumentList "/configure", "`"$rutaXml`"" -Wait -PassThru -NoNewWindow }
catch { Write-Log "No se pudo lanzar setup.exe: $($_.Exception.Message)" "ERROR"; exit 20 }
if ($proc.ExitCode -ne 0) { Write-Log "setup.exe devolvió $($proc.ExitCode). Detalle en $env:TEMP\InstalarOffice" "ERROR"; exit 20 }

$tras = Get-OfficeInstalado
if ($tras -and $tras.Productos -contains $Producto) { Write-Log "Instalado: $($tras.Productos -join ', ') · $($tras.Plataforma) · versión $($tras.Version)" "OK" }
else { Write-Log "setup.exe terminó bien pero no aparece $Producto en el registro: revisar el registro de la ODT." "AVISO" }
Write-Log "Registro de este script: $script:Log"
exit 0
