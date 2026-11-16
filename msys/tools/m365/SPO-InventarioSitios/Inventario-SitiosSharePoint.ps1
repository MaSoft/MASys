#Requires -Version 5.1
<#
.SYNOPSIS
    Inventario de todos los sitios de SharePoint Online de un tenant (comunicacion, equipo, grupos de Microsoft 365 y
    Teams, clasicos y, si se quiere, los OneDrive personales) a CSV. Solo lee.

.DESCRIPTION
    Se conecta a la administracion de SharePoint, saca todos los sitios con sus datos clave (titulo, tipo, propietario,
    almacenamiento, uso compartido externo, ultima actividad, bloqueo) y los clasifica por tipo.
    El modulo Microsoft.Online.SharePoint.PowerShell solo funciona sobre el motor de Windows PowerShell 5.1: desde
    PowerShell 7 se carga con -UseWindowsPowerShell, y si falta se instala desde powershell.exe (la ruta de modulos
    de 5.1 es distinta de la de 7).

.EXAMPLE
    .\Inventario-SitiosSharePoint.ps1 -Tenant contoso
#>

[CmdletBinding()]
param(
    [string]$Tenant,
    [switch]$SinOneDrive,
    [string]$CarpetaResultados
)

$ErrorActionPreference = "Stop"
$CodificacionCsv = if ($PSVersionTable.PSVersion.Major -ge 6) { "utf8BOM" } else { "UTF8" }   # CSV con BOM en 5.1 y en 7: Excel abre bien las tildes

function Write-Paso  ($t) { Write-Host "`n== $t" -ForegroundColor Cyan }
function Write-Ok    ($t) { Write-Host "  [OK]    $t" -ForegroundColor Green }
function Write-Aviso ($t) { Write-Host "  [AVISO] $t" -ForegroundColor Yellow }
function Write-Fallo ($t) { Write-Host "  [ERROR] $t" -ForegroundColor Red }

function Resolve-UrlAdministracion {
    <# Acepta 'contoso', 'contoso.onmicrosoft.com', 'admin@contoso.onmicrosoft.com' o 'https://contoso-admin.sharepoint.com'. #>
    param([string]$Entrada)
    $e = $Entrada.Trim().TrimEnd('/')
    if ($e -match '(?:^|@|//)([a-zA-Z0-9-]+)\.onmicrosoft\.com$') { return "https://$($Matches[1])-admin.sharepoint.com" }
    if ($e -match '^https://([a-zA-Z0-9-]+?)(-admin|-my)?\.sharepoint\.com') { return "https://$($Matches[1])-admin.sharepoint.com" }
    if ($e -match '^[a-zA-Z0-9-]+$') { return "https://$($e -replace '-admin$', '')-admin.sharepoint.com" }
    throw "No se reconoce el tenant '$Entrada'. Usa el nombre (contoso), el dominio contoso.onmicrosoft.com o la URL de administracion."
}

function Import-ModuloSharePoint {
    $mod = "Microsoft.Online.SharePoint.PowerShell"
    if ($PSVersionTable.PSVersion.Major -ge 6) {
        # PS7 lee la carpeta de modulos de 5.1 pero 5.1 no lee la de 7: se comprueba e instala desde powershell.exe
        $hay = & powershell.exe -NoProfile -NonInteractive -Command "[bool](Get-Module -ListAvailable -Name $mod)"
        if ($hay -notmatch "True") {
            Write-Aviso "Instalando el modulo $mod para Windows PowerShell 5.1..."
            & powershell.exe -NoProfile -NonInteractive -Command "if (-not (Get-PackageProvider -Name NuGet -ErrorAction SilentlyContinue)) { Install-PackageProvider -Name NuGet -Force | Out-Null }; Install-Module -Name $mod -Scope CurrentUser -Force -AllowClobber"
            if ($LASTEXITCODE -ne 0) { throw "No se pudo instalar $mod. Abre 'Windows PowerShell' (no pwsh) y ejecuta: Install-Module $mod -Scope CurrentUser" }
        }
        Import-Module $mod -UseWindowsPowerShell -WarningAction SilentlyContinue
    } else {
        if (-not (Get-Module -ListAvailable -Name $mod)) {
            Write-Aviso "Instalando el modulo $mod..."
            Install-Module -Name $mod -Scope CurrentUser -Force -AllowClobber
        }
        Import-Module $mod -DisableNameChecking
    }
}

$inicio = Get-Date
if (-not $CarpetaResultados) { $CarpetaResultados = Join-Path $PSScriptRoot "Resultados" }
New-Item -ItemType Directory -Force -Path $CarpetaResultados | Out-Null

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " INVENTARIO DE SITIOS DE SHAREPOINT (solo lee)" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

$conectado = $false
try {
    if (-not $Tenant) { $Tenant = Read-Host "Tenant (p. ej. 'contoso', contoso.onmicrosoft.com o https://contoso-admin.sharepoint.com)" }
    $url = Resolve-UrlAdministracion $Tenant
    $nombreTenant = ($url -replace '^https://', '' -replace '-admin\.sharepoint\.com$', '')
    $Registro = Join-Path $CarpetaResultados ("InventarioSharePoint_{0}_{1:yyyyMMdd_HHmmss}.csv" -f $nombreTenant, $inicio)

    Write-Paso "Conexion"
    Import-ModuloSharePoint
    Write-Ok "Modulo cargado"
    Connect-SPOService -Url $url -ModernAuth $true -AuthenticationUrl "https://login.microsoftonline.com/organizations"
    $conectado = $true
    Write-Ok "Conectado a $url"

    Write-Paso "Sitios (puede tardar varios minutos en tenants grandes)"
    $sitios = @(Get-SPOSite -Limit All -IncludePersonalSite (-not $SinOneDrive) -Detailed)
    Write-Ok "$($sitios.Count) sitios"

    $filas = foreach ($s in $sitios) {
        $tipo = if ($s.Url -match '-my\.sharepoint\.com/personal/') { "OneDrive personal" }
        elseif ($s.IsTeamsConnected) { "Teams" }
        elseif ("$($s.Template)" -like "GROUP#0*") { "Sitio de grupo (Microsoft 365)" }
        elseif ("$($s.Template)" -like "SITEPAGEPUBLISHING#0*") { "Sitio de comunicacion" }
        elseif ("$($s.Template)" -like "STS#3*") { "Sitio de equipo (sin grupo)" }
        elseif ("$($s.Template)" -like "STS#0*") { "Sitio de equipo (clasico)" }
        elseif ("$($s.Template)" -match "^(SRCHCEN|SPSMSITEHOST|APPCATALOG|REDIRECTSITE|POINTPUBLISHING|EHS|SPSPERS)") { "Tecnico de Microsoft" }
        else { "Otro" }
        $ultima = if ($s.LastContentModifiedDate -and ([datetime]$s.LastContentModifiedDate).Year -gt 2000) { ([datetime]$s.LastContentModifiedDate).ToString("dd/MM/yyyy") } else { "" }
        [PSCustomObject]@{
            Url = $s.Url; Titulo = $s.Title; Tipo = $tipo; Plantilla = "$($s.Template)"
            Propietario = "$($s.Owner)" -replace '^i:0#\.f\|membership\|', ''
            AlmacenamientoMB = $s.StorageUsageCurrent; CuotaMB = $s.StorageQuota
            CompartirExterno = switch ("$($s.SharingCapability)") {
                "Disabled" { "No" } "ExistingExternalUserSharingOnly" { "Solo invitados existentes" }
                "ExternalUserSharingOnly" { "Invitados" } "ExternalUserAndGuestSharing" { "Cualquiera con el enlace" } default { "$($s.SharingCapability)" }
            }
            UltimaActividad = $ultima; Bloqueo = "$($s.LockState)"; GroupId = "$($s.GroupId)"
            Nota = $(if ($tipo -in "Teams", "Sitio de grupo (Microsoft 365)") { "El propietario real es el del grupo de Microsoft 365" } else { "" })
        }
    }
    $filas = @($filas | Sort-Object Tipo, Url)
    $filas | Export-Csv -LiteralPath $Registro -NoTypeInformation -Encoding $CodificacionCsv -Delimiter ";"

    Write-Paso "Resumen"
    $filas | Group-Object Tipo | Sort-Object Count -Descending | ForEach-Object {
        $mb = ($_.Group | Measure-Object AlmacenamientoMB -Sum).Sum
        Write-Host ("  {0,-34} {1,5} sitios  {2,10:N1} GB" -f $_.Name, $_.Count, ($mb / 1024))
    }
    $abiertos = @($filas | Where-Object CompartirExterno -eq "Cualquiera con el enlace").Count
    if ($abiertos) { Write-Aviso "$abiertos sitios permiten compartir con «cualquiera con el enlace» (sin iniciar sesion)." }
    Write-Host "  Resultados: $Registro"
}
catch {
    Write-Fallo $_.Exception.Message
}
finally {
    if ($conectado) { try { Disconnect-SPOService } catch { } }
}
