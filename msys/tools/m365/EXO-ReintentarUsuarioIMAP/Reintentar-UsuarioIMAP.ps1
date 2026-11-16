#Requires -Version 5.1
<#
.SYNOPSIS
    Relanza la migracion IMAP de una cuenta que ha fallado (normalmente por la contrasena) con credenciales nuevas.

.DESCRIPTION
    Una cuenta de un lote IMAP guarda la contrasena con la que se creo el lote y no hay forma de cambiarla. Este script:
      1. Muestra el estado y el error actuales de la cuenta.
      2. Averigua el origen IMAP leyendo el lote al que pertenece (no se supone ni se escribe a mano).
      3. La saca de su lote y espera a que desaparezca.
      4. Crea un lote nuevo de una sola cuenta con la contrasena correcta y lo arranca.
    Si el buzon destino ya tenia elementos copiados, Exchange compara y solo copia lo que falta.
    La contrasena se pide por pantalla (oculta) si no se pasa, y el CSV del lote se construye en memoria.

.EXAMPLE
    .\Reintentar-UsuarioIMAP.ps1 -Correo logistica@empresa.com
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidatePattern('^[^@\s]+@[^@\s]+\.[^@\s]+$')][string]$Correo,
    [string]$UsuarioImap,
    [System.Security.SecureString]$ContrasenaImap,
    [string]$NombreLote,
    [string]$TenantId,
    [switch]$Simular,
    [switch]$SinConfirmar
)

$ErrorActionPreference = "Stop"
$Correo = $Correo.ToLower()
if (-not $UsuarioImap) { $UsuarioImap = $Correo }
if (-not $NombreLote) { $NombreLote = "Reintento-{0}-{1:yyyyMMdd-HHmm}" -f ($Correo.Split("@")[0] -replace '[^A-Za-z0-9._-]', ''), (Get-Date) }

function Write-Paso  ($t) { Write-Host "`n== $t" -ForegroundColor Cyan }
function Write-Ok    ($t) { Write-Host "  [OK]    $t" -ForegroundColor Green }
function Write-Aviso ($t) { Write-Host "  [AVISO] $t" -ForegroundColor Yellow }
function Write-Fallo ($t) { Write-Host "  [ERROR] $t" -ForegroundColor Red }
function Write-Sim   ($t) { Write-Host "  [SIM]   $t" -ForegroundColor Magenta }

function Connect-ExchangeSeguro {
    <# Reutiliza la sesion de Exchange Online si hay una; con -TenantId se detiene si es de otro tenant. #>
    param([string]$TenantId)
    if (-not (Get-Command Connect-ExchangeOnline -ErrorAction SilentlyContinue)) {
        throw "Falta el modulo ExchangeOnlineManagement. Instalalo con: Install-Module ExchangeOnlineManagement -Scope CurrentUser"
    }
    $con = $null
    try { $con = Get-ConnectionInformation | Where-Object { $_.State -eq "Connected" } | Select-Object -First 1 } catch { }
    if ($con -and $TenantId -and $con.TenantID -ne $TenantId) { Disconnect-ExchangeOnline -Confirm:$false; $con = $null }
    if (-not $con) { Connect-ExchangeOnline -ShowBanner:$false; $con = Get-ConnectionInformation | Select-Object -First 1 }
    if (-not $con) { throw "No se pudo conectar a Exchange Online." }
    if ($TenantId -and $con.TenantID -ne $TenantId) { throw "Tenant incorrecto: la sesion es de $($con.TenantID) y se esperaba $TenantId. No se ha tocado nada." }
    return $con
}

function ConvertTo-CsvExchange {
    <# CSV de migracion IMAP en memoria: campos entrecomillados y UTF-8 sin BOM. #>
    param([object[]]$Cuentas)
    $q = { param($s) '"' + ($s -replace '"', '""') + '"' }
    $lineas = @('"EmailAddress","UserName","Password"')
    foreach ($c in $Cuentas) { $lineas += ((& $q $c.Correo), (& $q $c.Usuario), (& $q $c.Contrasena)) -join "," }
    return (New-Object System.Text.UTF8Encoding($false)).GetBytes(($lineas -join "`r`n") + "`r`n")
}

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " REINTENTAR LA MIGRACION IMAP DE UNA CUENTA" -ForegroundColor Cyan
if ($Simular) { Write-Host " MODO SIMULACION: no se cambia nada" -ForegroundColor Magenta }
Write-Host "==============================================" -ForegroundColor Cyan

$texto = $null
try {
    $con = Connect-ExchangeSeguro -TenantId $TenantId
    Write-Ok "Exchange Online: $($con.UserPrincipalName) (tenant $($con.TenantID))"

    Write-Paso "Estado actual de $Correo"
    $mu = Get-MigrationUser -Identity $Correo -ErrorAction SilentlyContinue
    if (-not $mu) { throw "$Correo no esta en ningun lote de migracion. Para migrarla por primera vez usa Iniciar-MigracionIMAP.ps1." }
    $lote = Get-MigrationBatch -Identity "$($mu.BatchId)"
    Write-Host "  Lote:    $($lote.Identity)"
    Write-Host "  Estado:  $($mu.Status)"
    if ($mu.ErrorSummary) { Write-Host "  Error:   $(($mu.ErrorSummary -replace "\s*`r?`n\s*", ' '))" -ForegroundColor Yellow }
    try {
        $st = Get-MigrationUserStatistics -Identity $Correo
        Write-Host "  Copiados hasta ahora: $($st.SyncedItemCount)   Omitidos: $($st.SkippedItemCount)"
    } catch { }
    # El origen no se ve en la cuenta: se lee del lote al que pertenece
    $origen = "$($lote.SourceEndpoint)"
    if (-not $origen) { throw "No se pudo averiguar el origen IMAP del lote $($lote.Identity)." }
    Write-Ok "Origen del lote: $origen"
    if (-not (Get-MigrationEndpoint -Identity $origen -ErrorAction SilentlyContinue)) { throw "El origen '$origen' ya no existe." }
    if ("$($mu.Status)" -in "Synced", "Syncing", "Completed") { Write-Aviso "La cuenta no esta en fallo ($($mu.Status)): reintentarla solo tiene sentido si la contrasena ha cambiado." }

    if ($Simular) {
        Write-Sim "Se sacaria $Correo del lote $($lote.Identity) y se crearia el lote '$NombreLote' con el origen $origen"
        return
    }
    if (-not $ContrasenaImap) { $ContrasenaImap = Read-Host "Contrasena IMAP correcta de $UsuarioImap" -AsSecureString }
    $texto = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR([System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($ContrasenaImap))
    if (-not $texto) { throw "No se ha indicado la contrasena." }
    if (-not $SinConfirmar) {
        $r = Read-Host "`nSe va a sacar $Correo de '$($lote.Identity)' y relanzarla en '$NombreLote'. ¿Continuar? (S/N)"
        if ($r -notmatch '^(s|si|sí)$') { Write-Aviso "Cancelado: no se ha cambiado nada."; return }
    }

    Write-Paso "Relanzar"
    Remove-MigrationUser -Identity $Correo -Confirm:$false
    $limite = (Get-Date).AddMinutes(10)
    while (Get-MigrationUser -Identity $Correo -ErrorAction SilentlyContinue) {
        if ((Get-Date) -gt $limite) { throw "La cuenta sigue en el lote antiguo tras 10 minutos. Vuelve a lanzar el script mas tarde." }
        Write-Host "  ... esperando a que salga del lote antiguo" -ForegroundColor DarkGray
        Start-Sleep -Seconds 20
    }
    Write-Ok "Sacada del lote $($lote.Identity)"
    $csv = ConvertTo-CsvExchange @([PSCustomObject]@{ Correo = $Correo; Usuario = $UsuarioImap; Contrasena = $texto })
    New-MigrationBatch -Name $NombreLote -SourceEndpoint $origen -CSVData $csv -AutoStart | Out-Null
    Write-Ok "Lote '$NombreLote' creado y arrancado"
    Write-Host "`n  Sigue el estado con: ..\EXO-EstadoMigracionIMAP\Ver-EstadoMigracionIMAP.ps1 -Lote '$NombreLote' -Detalle" -ForegroundColor Yellow
    Write-Host "  El dia del cambio de MX completa tambien este lote: Complete-MigrationBatch -Identity '$NombreLote'" -ForegroundColor Yellow
}
catch {
    Write-Fallo $_.Exception.Message
}
finally {
    $texto = $null
}
