#Requires -Version 5.1
<#
.SYNOPSIS
    Recupera los correos grandes (LargeItem) que una migracion ha dejado fuera: sube el limite de tamano de los buzones
    y fuerza una nueva sincronizacion.

.DESCRIPTION
    Los buzones nuevos de Exchange Online solo aceptan mensajes de hasta 35 MB; los correos mas grandes del servidor
    antiguo se omiten como LargeItem. El limite se puede subir hasta 150 MB.
      1. Toma las cuentas de los lotes indicados o de un CSV y muestra su limite actual.
      2. Sube MaxReceiveSize y MaxSendSize al tamano indicado (solo si el actual es menor).
      3. Espera a que el cambio se propague (sincronizar antes reintentaria contra el limite viejo).
      4. Lanza una sincronizacion de cada cuenta que este en un lote de migracion.
    Los correos de mas de 150 MB no se pueden migrar por esta via: hay que sacarlos a mano del servidor antiguo.

.EXAMPLE
    .\Ampliar-LimiteCorreosGrandes.ps1 -Lote "Migracion-Lote1" -Simular
#>

[CmdletBinding(DefaultParameterSetName = "Lote")]
param(
    [Parameter(Mandatory = $true, ParameterSetName = "Lote")][string[]]$Lote,
    [Parameter(Mandatory = $true, ParameterSetName = "Csv")][string]$Csv,
    [ValidateRange(36, 150)][int]$TamanoMB = 150,
    [ValidateRange(0, 120)][int]$MinutosEspera = 15,
    [switch]$NoSincronizar,
    [string]$TenantId,
    [switch]$Simular,
    [switch]$SinConfirmar,
    [string]$CarpetaResultados
)

$ErrorActionPreference = "Stop"
$CodificacionCsv = if ($PSVersionTable.PSVersion.Major -ge 6) { "utf8BOM" } else { "UTF8" }   # CSV con BOM en 5.1 y en 7: Excel abre bien las tildes

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

function Get-MB {
    <# "35 MB (36,700,160 bytes)" -> 35. "Unlimited" -> [int]::MaxValue (no comparable: nunca se baja). #>
    param($Valor)
    if ("$Valor" -match 'Unlimited|Ilimitado') { return [int]::MaxValue }
    if ("$Valor" -match '\(([\d.,]+) bytes\)') { return [int]([int64]($Matches[1] -replace '[.,]', '') / 1MB) }
    return 0
}

$inicio = Get-Date
if (-not $CarpetaResultados) { $CarpetaResultados = Join-Path $PSScriptRoot "Resultados" }
New-Item -ItemType Directory -Force -Path $CarpetaResultados | Out-Null
$Registro = Join-Path $CarpetaResultados ("CorreosGrandes_{0:yyyyMMdd_HHmmss}.csv" -f $inicio)
$resultados = New-Object System.Collections.Generic.List[object]
function Add-Resultado ($Correo, $Accion, $Estado, $Detalle = "") {
    $resultados.Add([PSCustomObject]@{ Fecha = (Get-Date -Format "dd/MM/yyyy HH:mm:ss"); Correo = $Correo; Accion = $Accion; Estado = $Estado; Detalle = $Detalle })
}

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " CORREOS GRANDES DE LA MIGRACION (hasta $TamanoMB MB)" -ForegroundColor Cyan
if ($Simular) { Write-Host " MODO SIMULACION: no se cambia nada" -ForegroundColor Magenta }
Write-Host "==============================================" -ForegroundColor Cyan

try {
    $con = Connect-ExchangeSeguro -TenantId $TenantId
    Write-Ok "Exchange Online: $($con.UserPrincipalName) (tenant $($con.TenantID))"

    Write-Paso "Cuentas"
    $correos = if ($Csv) {
        Import-CsvFlexible -Ruta $Csv -Obligatorias @("Correo") | ForEach-Object { (Get-Valor $_ "Correo").ToLower() } | Where-Object { $_ }
    } else {
        foreach ($l in $Lote) {
            foreach ($u in @(Get-MigrationUser -BatchId $l -ResultSize Unlimited)) {
                # En sesiones REST EmailAddress llega vacio y la direccion esta en Identity
                $d = if ($u.EmailAddress) { "$($u.EmailAddress)" } else { "$($u.Identity)" }
                $d.ToLower()
            }
        }
    }
    $correos = @($correos | Select-Object -Unique)
    if ($correos.Count -eq 0) { throw "No hay cuentas." }

    $subir = New-Object System.Collections.Generic.List[string]
    foreach ($c in $correos) {
        $m = Get-EXOMailbox -Identity $c -Properties MaxReceiveSize, MaxSendSize -ErrorAction SilentlyContinue
        if (-not $m) { Write-Fallo "$c sin buzon"; Add-Resultado $c "LIMITE" "SIN BUZON"; continue }
        $rec = Get-MB $m.MaxReceiveSize; $envio = Get-MB $m.MaxSendSize
        $txt = "{0,-42} recibir {1} / enviar {2}" -f $c, $(if ($rec -eq [int]::MaxValue) { "ilimitado" } else { "$rec MB" }), $(if ($envio -eq [int]::MaxValue) { "ilimitado" } else { "$envio MB" })
        if ($rec -ge $TamanoMB -and $envio -ge $TamanoMB) { Write-Ok "$txt (ya basta)"; Add-Resultado $c "LIMITE" "YA BASTABA" "$rec MB" }
        else { Write-Host "  [SUBIR] $txt" -ForegroundColor Yellow; $subir.Add($c) }
    }

    if ($Simular) {
        foreach ($c in $subir) { Add-Resultado $c "LIMITE" "SIMULADO" "$TamanoMB MB" }
        Write-Sim "Se subiria el limite a $TamanoMB MB en $($subir.Count) buzones$(if (-not $NoSincronizar) { ", se esperaria $MinutosEspera min y se sincronizarian" })"
        return
    }
    if (-not $SinConfirmar) {
        $r = Read-Host "`nSe va a subir el limite a $TamanoMB MB en $($subir.Count) buzones$(if (-not $NoSincronizar) { ' y relanzar su sincronizacion' }). ¿Continuar? (S/N)"
        if ($r -notmatch '^(s|si|sí)$') { Write-Aviso "Cancelado: no se ha cambiado nada."; return }
    }

    Write-Paso "Subir el limite"
    foreach ($c in $subir) {
        try {
            Set-Mailbox -Identity $c -MaxReceiveSize "$($TamanoMB)MB" -MaxSendSize "$($TamanoMB)MB"
            Write-Ok "$c -> $TamanoMB MB"; Add-Resultado $c "LIMITE" "OK" "$TamanoMB MB"
        } catch { Write-Fallo "$c $($_.Exception.Message)"; Add-Resultado $c "LIMITE" "ERROR" $_.Exception.Message }
    }

    if ($NoSincronizar) { return }
    if ($subir.Count -and $MinutosEspera) {
        Write-Paso "Esperando $MinutosEspera minutos a que el cambio se propague"
        for ($i = $MinutosEspera; $i -gt 0; $i--) { Write-Host "  ... $i min" -ForegroundColor DarkGray; Start-Sleep -Seconds 60 }
    }
    Write-Paso "Relanzar la sincronizacion"
    foreach ($c in $correos) {
        if (-not (Get-MigrationUser -Identity $c -ErrorAction SilentlyContinue)) { Add-Resultado $c "SINCRONIZAR" "NO ESTA EN UN LOTE"; continue }
        try { Start-MigrationUser -Identity $c -Confirm:$false; Write-Ok "$c sincronizando"; Add-Resultado $c "SINCRONIZAR" "OK" }
        catch { Write-Aviso "$c : $($_.Exception.Message)"; Add-Resultado $c "SINCRONIZAR" "ERROR" $_.Exception.Message }
    }
    if (-not @($resultados | Where-Object { $_.Accion -eq "SINCRONIZAR" -and $_.Estado -eq "OK" }).Count) {
        Write-Aviso "No arranco ninguna sincronizacion. Alternativa: parar y arrancar el lote (Stop-MigrationBatch / Start-MigrationBatch) o esperar a la sincronizacion de cada 24 h."
    }
}
catch {
    Write-Fallo $_.Exception.Message
    Add-Resultado "" "GENERAL" "ERROR" $_.Exception.Message
}
finally {
    if ($resultados.Count -gt 0) { $resultados | Export-Csv -LiteralPath $Registro -NoTypeInformation -Encoding $CodificacionCsv -Delimiter ";" }
    Write-Paso "Resumen"
    $resultados | Group-Object Accion, Estado | ForEach-Object { Write-Host ("  {0,-34} {1}" -f $_.Name, $_.Count) }
    if ($resultados.Count -gt 0) { Write-Host "  Resultados: $Registro" }
    Write-Host "  Comprueba despues los omitidos con: ..\EXO-EstadoMigracionIMAP\Ver-EstadoMigracionIMAP.ps1 -VerOmitidos" -ForegroundColor Yellow
}
