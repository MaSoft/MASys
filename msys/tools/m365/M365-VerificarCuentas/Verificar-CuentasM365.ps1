#Requires -Version 5.1
<#
.SYNOPSIS
    Comprueba cuentas de Microsoft 365: si existen, estan habilitadas, que licencias tienen y si tienen buzon. Solo lee.

.DESCRIPTION
    Para los UPN de un CSV (o para todas las cuentas del tenant con -Todos) cruza:
      1. Entra ID: existe, habilitada, tipo (miembro o invitado), fecha de alta.
      2. Licencias asignadas, con su nombre.
      3. Exchange Online (con -ConBuzon): si el buzon esta realmente creado y de que tipo, y su tamano con -ConTamano.
      4. Ultimo inicio de sesion (con -ConInicioSesion; requiere Entra ID P1/P2 o Business Premium).
    Da un veredicto por cuenta y lo exporta a CSV. No cambia nada.

.EXAMPLE
    .\Verificar-CuentasM365.ps1 -Csv .\cuentas.csv -TenantId 00000000-0000-0000-0000-000000000000 -ConBuzon
.EXAMPLE
    .\Verificar-CuentasM365.ps1 -Todos -ConBuzon -ConInicioSesion
#>

[CmdletBinding(DefaultParameterSetName = "Csv")]
param(
    [Parameter(Mandatory = $true, ParameterSetName = "Csv")][string]$Csv,
    [Parameter(Mandatory = $true, ParameterSetName = "Todos")][switch]$Todos,
    [Parameter(ParameterSetName = "Todos")][switch]$IncluirInvitados,
    [string]$TenantId,
    [switch]$ConBuzon,
    [switch]$ConTamano,
    [switch]$ConInicioSesion,
    [string]$CarpetaResultados
)

$ErrorActionPreference = "Stop"
$CodificacionCsv = if ($PSVersionTable.PSVersion.Major -ge 6) { "utf8BOM" } else { "UTF8" }   # CSV con BOM en 5.1 y en 7: Excel abre bien las tildes
$Scopes = @("User.Read.All", "Organization.Read.All")
if ($ConInicioSesion) { $Scopes += "AuditLog.Read.All" }
if ($ConTamano) { $ConBuzon = $true }

# ============================================================ utilidades comunes
function Write-Paso  ($t) { Write-Host "`n== $t" -ForegroundColor Cyan }
function Write-Ok    ($t) { Write-Host "  [OK]    $t" -ForegroundColor Green }
function Write-Aviso ($t) { Write-Host "  [AVISO] $t" -ForegroundColor Yellow }
function Write-Fallo ($t) { Write-Host "  [ERROR] $t" -ForegroundColor Red }

function Import-CsvFlexible {
    <# CSV con , o ; en UTF-8 o ANSI (Excel); comprueba las columnas obligatorias. #>
    param([string]$Ruta, [string[]]$Obligatorias)
    if (-not (Test-Path -LiteralPath $Ruta)) { throw "No se encuentra el CSV: $Ruta" }
    $bytes = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $Ruta).Path)
    try { $texto = (New-Object System.Text.UTF8Encoding($false, $true)).GetString($bytes) }
    catch { $texto = [System.Text.Encoding]::GetEncoding(1252).GetString($bytes) }
    $texto = $texto.TrimStart([char]0xFEFF)
    $cabecera = ($texto -split "`r?`n", 2)[0]
    $sep = if (($cabecera -split ';').Count -gt ($cabecera -split ',').Count) { ';' } else { ',' }
    $filas = @($texto | ConvertFrom-Csv -Delimiter $sep)
    if ($filas.Count -eq 0) { throw "El CSV no tiene filas: $Ruta" }
    $columnas = $filas[0].PSObject.Properties.Name | ForEach-Object { $_.Trim() }
    foreach ($c in $Obligatorias) {
        if ($columnas -notcontains $c) { throw "Falta la columna '$c' en el CSV (columnas: $($columnas -join ', '))" }
    }
    return $filas
}

function Get-Valor {
    param($Fila, [string]$Columna)
    $p = $Fila.PSObject.Properties | Where-Object { $_.Name.Trim() -eq $Columna } | Select-Object -First 1
    if ($p -and $null -ne $p.Value) { return ([string]$p.Value).Trim() }
    return ""
}

function Connect-GraphSeguro {
    param([string[]]$Scopes, [string]$TenantId)
    if (-not (Get-Module -ListAvailable -Name Microsoft.Graph.Authentication)) {
        throw "Falta el modulo Microsoft.Graph. Instalalo con: Install-Module Microsoft.Graph -Scope CurrentUser"
    }
    $ctx = Get-MgContext
    $faltan = if ($ctx) { @($Scopes | Where-Object { $ctx.Scopes -notcontains $_ }) } else { $Scopes }
    if (-not $ctx -or $faltan.Count -gt 0 -or ($TenantId -and $ctx.TenantId -ne $TenantId)) {
        $p = @{ Scopes = $Scopes; NoWelcome = $true }
        if ($TenantId) { $p.TenantId = $TenantId }
        Connect-MgGraph @p | Out-Null
        $ctx = Get-MgContext
    }
    if (-not $ctx) { throw "No se pudo iniciar sesion en Microsoft Graph." }
    if ($TenantId -and $ctx.TenantId -ne $TenantId) {
        throw "Tenant incorrecto: la sesion es de $($ctx.TenantId) y se esperaba $TenantId."
    }
    $faltan = @($Scopes | Where-Object { $ctx.Scopes -notcontains $_ })
    if ($faltan.Count -gt 0) { throw "La sesion no tiene estos permisos: $($faltan -join ', ')" }
    return $ctx
}

function Connect-ExchangeSeguro {
    <# Reutiliza la sesion de Exchange Online si hay una: volver a conectar sobre una valida puede fallar (WAM). #>
    if (-not (Get-Command Connect-ExchangeOnline -ErrorAction SilentlyContinue)) {
        throw "Falta el modulo ExchangeOnlineManagement. Instalalo con: Install-Module ExchangeOnlineManagement -Scope CurrentUser"
    }
    $con = $null
    try { $con = Get-ConnectionInformation | Where-Object { $_.State -eq "Connected" } | Select-Object -First 1 } catch { }
    if (-not $con) { Connect-ExchangeOnline -ShowBanner:$false; $con = Get-ConnectionInformation | Select-Object -First 1 }
    return $con
}

# ============================================================ inicio
$inicio = Get-Date
if (-not $CarpetaResultados) { $CarpetaResultados = Join-Path $PSScriptRoot "Resultados" }
New-Item -ItemType Directory -Force -Path $CarpetaResultados | Out-Null
$Registro = Join-Path $CarpetaResultados ("VerificarCuentas_{0:yyyyMMdd_HHmmss}.csv" -f $inicio)
$filas = New-Object System.Collections.Generic.List[object]

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " VERIFICAR CUENTAS DE MICROSOFT 365 (solo lee)" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

try {
    Write-Paso "Conexion"
    $ctx = Connect-GraphSeguro -Scopes $Scopes -TenantId $TenantId
    Write-Ok "Tenant $($ctx.TenantId) - cuenta $($ctx.Account)"
    $skuNombre = @{}
    foreach ($s in (Get-MgSubscribedSku -All)) { $skuNombre[[string]$s.SkuId] = $s.SkuPartNumber }
    if ($ConBuzon) {
        $con = Connect-ExchangeSeguro
        Write-Ok "Exchange Online: $($con.UserPrincipalName)"
    }

    $props = @("Id", "UserPrincipalName", "DisplayName", "AccountEnabled", "UserType", "CreatedDateTime", "AssignedLicenses", "Mail")
    if ($ConInicioSesion) { $props += "SignInActivity" }

    Write-Paso "Cuentas"
    if ($Todos) {
        $usuarios = @(Get-MgUser -All -Property $props |
                Where-Object { $IncluirInvitados -or $_.UserType -ne "Guest" })
        $lista = $usuarios | ForEach-Object { [PSCustomObject]@{ UPN = $_.UserPrincipalName; Usuario = $_ } }
    } else {
        $vistos = @{}
        $lista = foreach ($f in (Import-CsvFlexible -Ruta $Csv -Obligatorias @("UPN"))) {
            $upn = (Get-Valor $f "UPN").ToLower()
            if (-not $upn -or $vistos.ContainsKey($upn)) { continue }
            $vistos[$upn] = $true
            $u = $null
            try {
                # NO usar $u[0]: MicrosoftGraphUser tiene indexador propio y devuelve $null sin error
                $u = Get-MgUser -Filter "userPrincipalName eq '$($upn.Replace("'", "''"))'" -Property $props | Select-Object -First 1
                if (-not $u) { $u = Get-MgUser -Filter "mail eq '$($upn.Replace("'", "''"))'" -Property $props | Select-Object -First 1 }
            } catch { Write-Aviso "$upn : $($_.Exception.Message)" }
            [PSCustomObject]@{ UPN = $upn; Usuario = $u }
        }
    }
    $lista = @($lista)
    Write-Ok "$($lista.Count) cuentas a revisar"

    $n = 0
    foreach ($e in $lista) {
        $n++
        Write-Progress -Activity "Verificando cuentas" -Status $e.UPN -PercentComplete (100 * $n / [math]::Max(1, $lista.Count))
        $u = $e.Usuario
        $fila = [ordered]@{ UPN = $e.UPN; Existe = "No"; Nombre = ""; Habilitada = ""; Tipo = ""; Licencias = ""; Buzon = "no comprobado"
            TamanoBuzon = ""; UltimoInicioSesion = ""; Alta = ""; Veredicto = "NO EXISTE" }
        if ($u) {
            $fila.Existe = "Si"; $fila.Nombre = $u.DisplayName; $fila.Tipo = $(if ($u.UserType -eq "Guest") { "Invitado" } else { "Miembro" })
            $fila.Habilitada = $(if ($u.AccountEnabled) { "Si" } else { "No" })
            $fila.Licencias = (@($u.AssignedLicenses | ForEach-Object { $skuNombre[[string]$_.SkuId] }) -join ", ")
            if ($u.CreatedDateTime) { $fila.Alta = ([datetime]$u.CreatedDateTime).ToLocalTime().ToString("dd/MM/yyyy") }
            if ($ConInicioSesion -and $u.SignInActivity -and $u.SignInActivity.LastSignInDateTime) {
                $fila.UltimoInicioSesion = ([datetime]$u.SignInActivity.LastSignInDateTime).ToLocalTime().ToString("dd/MM/yyyy HH:mm")
            }
            if ($ConBuzon) {
                $mbx = Get-EXOMailbox -Identity $u.Id -Properties RecipientTypeDetails -ErrorAction SilentlyContinue
                if ($mbx) {
                    $fila.Buzon = switch ($mbx.RecipientTypeDetails) {
                        "UserMailbox" { "Usuario" } "SharedMailbox" { "Compartido" } "RoomMailbox" { "Sala" } "EquipmentMailbox" { "Equipo" } default { [string]$mbx.RecipientTypeDetails }
                    }
                    if ($ConTamano) {
                        $st = Get-EXOMailboxStatistics -Identity $u.Id -ErrorAction SilentlyContinue
                        if ($st) { $fila.TamanoBuzon = ([string]$st.TotalItemSize) -replace '\s*\(.*$', '' }
                    }
                } else { $fila.Buzon = "Sin buzon" }
            }
            $fila.Veredicto = if (-not $u.AccountEnabled) { "DESHABILITADA" }
            elseif ($fila.Buzon -eq "Sin buzon" -and -not $fila.Licencias) { "SIN LICENCIA NI BUZON" }
            elseif ($fila.Buzon -eq "Sin buzon") { "LICENCIA SIN BUZON (esperar o revisar)" }
            elseif (-not $fila.Licencias -and $fila.Buzon -notin "Compartido", "Sala", "Equipo") { "SIN LICENCIA" }
            else { "OK" }
        }
        $filas.Add([PSCustomObject]$fila)
        $color = if ($fila.Veredicto -eq "OK") { "Green" } elseif ($fila.Existe -eq "No") { "Red" } else { "Yellow" }
        Write-Host ("  {0,-45} {1}" -f $e.UPN, $fila.Veredicto) -ForegroundColor $color
    }
    Write-Progress -Activity "Verificando cuentas" -Completed
}
catch {
    Write-Fallo $_.Exception.Message
}
finally {
    if ($filas.Count -gt 0) {
        $filas | Export-Csv -LiteralPath $Registro -NoTypeInformation -Encoding $CodificacionCsv -Delimiter ";"
        Write-Paso "Resumen"
        $filas | Group-Object Veredicto | Sort-Object Count -Descending | ForEach-Object { Write-Host ("  {0,-42} {1}" -f $_.Name, $_.Count) }
        Write-Host "  Resultados: $Registro"
    }
}
