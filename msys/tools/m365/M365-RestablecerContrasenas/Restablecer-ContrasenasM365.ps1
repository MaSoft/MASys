#Requires -Version 5.1
<#
.SYNOPSIS
    Restablece la contrasena de una lista de cuentas de Microsoft 365 (CSV) en una sola pasada.

.DESCRIPTION
    Por cada UPN del CSV fija la contrasena de la columna Contrasena o una nueva de 16 caracteres, opcionalmente
    obliga a cambiarla al entrar y cierra las sesiones abiertas. Nunca toca la cuenta con la que se ha iniciado sesion.
    Comprueba antes el tenant y que la sesion tenga el permiso de escritura de contrasenas (desde 2023 Graph exige
    User-PasswordProfile.ReadWrite.All: User.ReadWrite.All ya no basta).

.PARAMETER Csv
    CSV con la columna UPN y, opcional, Contrasena. Separador , o ; .

.PARAMETER TenantId
    Tenant esperado: si la sesion es de otro, se detiene sin tocar nada.

.PARAMETER CambiarAlEntrar
    Obliga a cambiar la contrasena en el siguiente inicio de sesion.

.PARAMETER RevocarSesiones
    Cierra las sesiones abiertas de cada cuenta (Outlook, movil y navegadores piden entrar de nuevo).

.EXAMPLE
    .\Restablecer-ContrasenasM365.ps1 -Csv .\cuentas.csv -TenantId 00000000-0000-0000-0000-000000000000 -Simular
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Csv,
    [string]$TenantId,
    [switch]$CambiarAlEntrar,
    [switch]$RevocarSesiones,
    [switch]$Simular,
    [switch]$SinConfirmar,
    [string]$CarpetaResultados
)

$ErrorActionPreference = "Stop"
$CodificacionCsv = if ($PSVersionTable.PSVersion.Major -ge 6) { "utf8BOM" } else { "UTF8" }   # CSV con BOM en 5.1 y en 7: Excel abre bien las tildes
$Scopes = @("User.Read.All", "User-PasswordProfile.ReadWrite.All")
if ($RevocarSesiones) { $Scopes += "User.RevokeSessions.All" }

# ============================================================ utilidades comunes
function Write-Paso  ($t) { Write-Host "`n== $t" -ForegroundColor Cyan }
function Write-Ok    ($t) { Write-Host "  [OK]    $t" -ForegroundColor Green }
function Write-Aviso ($t) { Write-Host "  [AVISO] $t" -ForegroundColor Yellow }
function Write-Fallo ($t) { Write-Host "  [ERROR] $t" -ForegroundColor Red }
function Write-Sim   ($t) { Write-Host "  [SIM]   $t" -ForegroundColor Magenta }

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

function New-ContrasenaSegura {
    <# 16 caracteres con minuscula, mayuscula, digito y simbolo; RNG criptografico valido en PowerShell 5.1 y 7. #>
    param([int]$Longitud = 16)
    $grupos = @("abcdefghijkmnopqrstuvwxyz", "ABCDEFGHJKLMNPQRSTUVWXYZ", "23456789", "!#%*+-=?@")
    $todos = -join $grupos
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    function Get-Aleatorio([int]$Max) {
        $b = New-Object byte[] 4
        do { $rng.GetBytes($b); $n = [BitConverter]::ToUInt32($b, 0) } while ($n -ge ([uint32]::MaxValue - ([uint32]::MaxValue % $Max)))
        return [int]($n % $Max)
    }
    $c = New-Object System.Collections.Generic.List[char]
    foreach ($g in $grupos) { $c.Add($g[(Get-Aleatorio $g.Length)]) }
    while ($c.Count -lt $Longitud) { $c.Add($todos[(Get-Aleatorio $todos.Length)]) }
    for ($i = $c.Count - 1; $i -gt 0; $i--) { $j = Get-Aleatorio ($i + 1); $t = $c[$i]; $c[$i] = $c[$j]; $c[$j] = $t }
    $rng.Dispose()
    return -join $c
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
        throw "Tenant incorrecto: la sesion es de $($ctx.TenantId) y se esperaba $TenantId. No se ha tocado nada."
    }
    $faltan = @($Scopes | Where-Object { $ctx.Scopes -notcontains $_ })
    if ($faltan.Count -gt 0) { throw "La sesion no tiene estos permisos: $($faltan -join ', ')" }
    return $ctx
}

function Invoke-GraphReintento {
    <# Llamada a Graph con reintentos ante 429 y errores transitorios; 400 y 403 no se reintentan (no se arreglan solos). #>
    param([string]$Metodo, [string]$Uri, $Cuerpo)
    for ($i = 1; $i -le 5; $i++) {
        try {
            $p = @{ Method = $Metodo; Uri = $Uri }
            if ($null -ne $Cuerpo) { $p.Body = ($Cuerpo | ConvertTo-Json -Depth 6); $p.ContentType = "application/json" }
            return Invoke-MgGraphRequest @p
        } catch {
            $m = $_.Exception.Message
            if ($i -eq 5 -or $m -match "\b(400|401|403|404)\b|Forbidden|BadRequest|NotFound|password") { throw }
            Start-Sleep -Seconds (3 * $i)
        }
    }
}

# ============================================================ preparacion
$inicio = Get-Date
if (-not $CarpetaResultados) { $CarpetaResultados = Join-Path $PSScriptRoot "Resultados" }
New-Item -ItemType Directory -Force -Path $CarpetaResultados | Out-Null
$Registro = Join-Path $CarpetaResultados ("RestablecerContrasenas_{0:yyyyMMdd_HHmmss}.csv" -f $inicio)
$resultados = New-Object System.Collections.Generic.List[object]
function Add-Resultado ($Upn, $Estado, $Detalle = "", $Contrasena = "") {
    $resultados.Add([PSCustomObject]@{ Fecha = (Get-Date -Format "dd/MM/yyyy HH:mm:ss"); UPN = $Upn; Estado = $Estado
            Contrasena = $Contrasena; Detalle = $Detalle })
}

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " RESTABLECER CONTRASENAS EN MICROSOFT 365" -ForegroundColor Cyan
if ($Simular) { Write-Host " MODO SIMULACION: no se cambia nada" -ForegroundColor Magenta }
Write-Host "==============================================" -ForegroundColor Cyan

try {
    Write-Paso "Comprobaciones"
    $vistos = @{}
    $cuentas = @(foreach ($f in (Import-CsvFlexible -Ruta $Csv -Obligatorias @("UPN"))) {
            $upn = (Get-Valor $f "UPN").ToLower()
            if (-not $upn -or $vistos.ContainsKey($upn)) { continue }
            $vistos[$upn] = $true
            [PSCustomObject]@{ UPN = $upn; Contrasena = (Get-Valor $f "Contrasena") }
        })
    if ($cuentas.Count -eq 0) { throw "No hay ninguna cuenta en el CSV." }
    Write-Ok "$($cuentas.Count) cuentas en el CSV"

    $ctx = Connect-GraphSeguro -Scopes $Scopes -TenantId $TenantId
    Write-Ok "Tenant $($ctx.TenantId) - cuenta $($ctx.Account)"
    if (-not $TenantId) { Write-Aviso "No se ha indicado -TenantId: comprueba que el tenant es el correcto." }

    if (-not $Simular -and -not $SinConfirmar) {
        $extra = if ($RevocarSesiones) { " y cerrar sus sesiones" } else { "" }
        $r = Read-Host "`nSe va a cambiar la contrasena de $($cuentas.Count) cuentas$extra. ¿Continuar? (S/N)"
        if ($r -notmatch '^(s|si|sí)$') { Write-Aviso "Cancelado: no se ha cambiado nada."; return }
    }

    Write-Paso "Contrasenas"
    foreach ($c in $cuentas) {
        $txt = "{0,-45}" -f $c.UPN
        try {
            if ($c.UPN -eq ([string]$ctx.Account).ToLower()) {
                Write-Aviso "$txt es la cuenta con la que has iniciado sesion: no se toca"
                Add-Resultado $c.UPN "OMITIDA" "Cuenta de la propia sesion"; continue
            }
            $u = Invoke-GraphReintento -Metodo GET -Uri "/v1.0/users/$([uri]::EscapeDataString($c.UPN))?`$select=id,userPrincipalName,accountEnabled"
            if (-not $u) { throw "No existe" }
            if ($Simular) { Write-Sim "$txt se restableceria$(if (-not $u.accountEnabled) { ' (la cuenta esta deshabilitada)' })"; Add-Resultado $c.UPN "SIMULADO"; continue }
            $pwd = if ($c.Contrasena) { $c.Contrasena } else { New-ContrasenaSegura }
            Invoke-GraphReintento -Metodo PATCH -Uri "/v1.0/users/$($u.id)" -Cuerpo @{
                passwordProfile = @{ password = $pwd; forceChangePasswordNextSignIn = [bool]$CambiarAlEntrar }
            } | Out-Null
            $det = ""
            if ($RevocarSesiones) {
                Invoke-GraphReintento -Metodo POST -Uri "/v1.0/users/$($u.id)/revokeSignInSessions" | Out-Null
                $det = "Sesiones cerradas"
            }
            if (-not $u.accountEnabled) { $det = ($det + " (cuenta deshabilitada)").Trim() }
            Write-Ok "$txt restablecida $det"
            Add-Resultado $c.UPN "OK" $det $pwd
        } catch {
            $m = $_.Exception.Message
            if ($m -match "404|NotFound|does not exist") { $m = "La cuenta no existe en el tenant" }
            elseif ($m -match "403|Forbidden|Authorization") { $m = "Sin permiso (para cuentas con rol de administrador hace falta Administrador de autenticacion privilegiada)" }
            Write-Fallo "$txt $m"
            Add-Resultado $c.UPN "ERROR" $m
        }
    }
}
catch {
    Write-Fallo $_.Exception.Message
    Add-Resultado "" "ERROR" $_.Exception.Message
}
finally {
    if ($resultados.Count -gt 0) { $resultados | Export-Csv -LiteralPath $Registro -NoTypeInformation -Encoding $CodificacionCsv -Delimiter ";" }
    Write-Paso "Resumen"
    Write-Host "  Restablecidas: $(@($resultados | Where-Object Estado -eq 'OK').Count)   Errores: $(@($resultados | Where-Object Estado -eq 'ERROR').Count)   Omitidas: $(@($resultados | Where-Object Estado -eq 'OMITIDA').Count)"
    if ($resultados.Count -gt 0) {
        Write-Host "  Resultados: $Registro"
        if (@($resultados | Where-Object Contrasena).Count) { Write-Aviso "El CSV de resultados contiene contrasenas: guardalo en sitio seguro y borralo despues." }
    }
}
