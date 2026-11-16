#Requires -Version 5.1
<#
.SYNOPSIS
    Cambia la direccion principal de correo de buzones de Exchange Online a partir de un CSV, conservando la antigua
    como alias, con CSV de vuelta atras automatico.

.DESCRIPTION
    Por cada fila (Antiguo, Nuevo, Nombre opcional):
      - Localiza el buzon por su direccion principal con -Filter (con -Identity, un grupo o contacto con el mismo nombre
        da «coincide con varias entradas»). Si ya tiene la direccion nueva, lo da por hecho.
      - Comprueba que el dominio nuevo esta aceptado en el tenant y que la direccion nueva no la usa otro destinatario.
      - Pone la nueva como principal (SMTP:) y la antigua como alias (smtp:), conservando el resto de alias, X500, etc.
      - Opcional: cambia el nombre para mostrar.
    El inicio de sesion (UPN) NO se cambia. Antes de aplicar nada guarda un CSV de vuelta atras que se le puede pasar al
    mismo script para deshacer.

.EXAMPLE
    .\Cambiar-DominioCorreo.ps1 -Csv .\cambio.csv -TenantId 00000000-0000-0000-0000-000000000000 -Simular
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Csv,
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

function Get-NuevasDirecciones {
    <# Lista final: SMTP:nueva, smtp:antigua y el resto de direcciones (alias, X500, SIP…) sin la principal actual.
       Se compara sin distinguir mayusculas para no duplicar una direccion que ya estaba como alias. #>
    param([string[]]$Actuales, [string]$Nueva, [string]$Antigua)
    $lista = New-Object System.Collections.Generic.List[string]
    $lista.Add("SMTP:$Nueva")
    if ($Antigua -ne $Nueva) { $lista.Add("smtp:$Antigua") }
    foreach ($d in $Actuales) {
        if ($d -clike "SMTP:*") { continue }                      # la principal actual (ya entra como alias si es la antigua)
        if (@($lista | Where-Object { $_ -eq $d }).Count) { continue }
        $lista.Add($d)
    }
    return , $lista.ToArray()
}

$inicio = Get-Date
if (-not $CarpetaResultados) { $CarpetaResultados = Join-Path $PSScriptRoot "Resultados" }
New-Item -ItemType Directory -Force -Path $CarpetaResultados | Out-Null
$Registro = Join-Path $CarpetaResultados ("CambioDominio_{0:yyyyMMdd_HHmmss}.csv" -f $inicio)
$Vuelta = Join-Path $CarpetaResultados ("VueltaAtras_{0:yyyyMMdd_HHmmss}.csv" -f $inicio)
$resultados = New-Object System.Collections.Generic.List[object]
function Add-Resultado ($Antiguo, $Nuevo, $Estado, $Detalle = "") {
    $resultados.Add([PSCustomObject]@{ Fecha = (Get-Date -Format "dd/MM/yyyy HH:mm:ss"); Antiguo = $Antiguo; Nuevo = $Nuevo; Estado = $Estado; Detalle = $Detalle })
}

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " CAMBIO DE DIRECCION PRINCIPAL DE CORREO" -ForegroundColor Cyan
if ($Simular) { Write-Host " MODO SIMULACION: no se cambia nada" -ForegroundColor Magenta }
Write-Host "==============================================" -ForegroundColor Cyan

try {
    Write-Paso "Comprobaciones"
    $patron = '^[^@\s]+@[^@\s]+\.[^@\s]+$'
    $cambios = @(foreach ($f in (Import-CsvFlexible -Ruta $Csv -Obligatorias @("Antiguo", "Nuevo"))) {
            $a = (Get-Valor $f "Antiguo").ToLower(); $n = (Get-Valor $f "Nuevo").ToLower()
            if (-not $a -and -not $n) { continue }
            if ($a -notmatch $patron -or $n -notmatch $patron) { Write-Aviso "Fila no valida, se omite: '$a' -> '$n'"; Add-Resultado $a $n "OMITIDA" "Direccion no valida"; continue }
            [PSCustomObject]@{ Antiguo = $a; Nuevo = $n; Nombre = (Get-Valor $f "Nombre") }
        })
    $dupl = @($cambios | Group-Object Nuevo | Where-Object Count -gt 1)
    if ($dupl.Count) { throw "Direcciones nuevas repetidas en el CSV: $(($dupl.Name) -join ', ')" }
    if ($cambios.Count -eq 0) { throw "No hay cambios validos en el CSV." }
    Write-Ok "$($cambios.Count) cambios en el CSV"

    $con = Connect-ExchangeSeguro -TenantId $TenantId
    Write-Ok "Exchange Online: $($con.UserPrincipalName) (tenant $($con.TenantID))"
    if (-not $TenantId) { Write-Aviso "No se ha indicado -TenantId: comprueba que el tenant es el correcto." }
    $aceptados = @(Get-AcceptedDomain | ForEach-Object { "$($_.DomainName)".ToLower() })
    foreach ($d in ($cambios | ForEach-Object { $_.Nuevo.Split("@")[1] } | Select-Object -Unique)) {
        if ($aceptados -notcontains $d) { throw "El dominio '$d' no esta aceptado en el tenant (dominios: $($aceptados -join ', '))." }
        Write-Ok "Dominio aceptado: $d"
    }

    # Se localizan todos antes de cambiar nada: el CSV de vuelta atras se guarda completo antes del primer cambio
    Write-Paso "Buzones"
    $plan = New-Object System.Collections.Generic.List[object]
    foreach ($c in $cambios) {
        $txt = "{0} -> {1}" -f $c.Antiguo, $c.Nuevo
        $m = @(Get-EXOMailbox -Filter "PrimarySmtpAddress -eq '$($c.Antiguo)'" -Properties EmailAddresses, DisplayName -ErrorAction SilentlyContinue)
        $hecho = $false
        if ($m.Count -eq 0) {
            $m = @(Get-EXOMailbox -Filter "PrimarySmtpAddress -eq '$($c.Nuevo)'" -Properties EmailAddresses, DisplayName -ErrorAction SilentlyContinue)
            $hecho = $m.Count -eq 1
        }
        if ($m.Count -eq 0) { Write-Fallo "$txt : no hay ningun buzon con esa direccion principal"; Add-Resultado $c.Antiguo $c.Nuevo "NO ENCONTRADO"; continue }
        if ($m.Count -gt 1) { Write-Fallo "$txt : $($m.Count) buzones coinciden, revisar a mano"; Add-Resultado $c.Antiguo $c.Nuevo "ERROR" "Varios buzones"; continue }
        $mbx = $m[0]
        if ($hecho) { Write-Ok "$txt (ya hecho)"; Add-Resultado $c.Antiguo $c.Nuevo "YA HECHO"; continue }
        $otro = @(Get-EXORecipient -Filter "EmailAddresses -eq 'smtp:$($c.Nuevo)'" -ErrorAction SilentlyContinue | Where-Object { $_.ExternalDirectoryObjectId -ne $mbx.ExternalDirectoryObjectId })
        if ($otro.Count) { Write-Fallo "$txt : la direccion nueva ya la usa $($otro[0].DisplayName) ($($otro[0].RecipientTypeDetails))"; Add-Resultado $c.Antiguo $c.Nuevo "ERROR" "Direccion nueva ocupada por $($otro[0].PrimarySmtpAddress)"; continue }
        $plan.Add([PSCustomObject]@{ Cambio = $c; Buzon = $mbx })
        Write-Host "  [CAMBIAR] $txt  ($($mbx.DisplayName)$(if ($c.Nombre -and $c.Nombre -ne $mbx.DisplayName) { " -> $($c.Nombre)" }))" -ForegroundColor Yellow
    }
    if ($plan.Count -eq 0) { Write-Aviso "No hay nada que cambiar."; return }

    $plan | ForEach-Object { [PSCustomObject]@{ Antiguo = $_.Cambio.Nuevo; Nuevo = $_.Cambio.Antiguo; Nombre = $_.Buzon.DisplayName } } |
        Export-Csv -LiteralPath $Vuelta -NoTypeInformation -Encoding $CodificacionCsv -Delimiter ";"
    Write-Ok "CSV de vuelta atras guardado: $Vuelta"

    if ($Simular) { foreach ($p in $plan) { Add-Resultado $p.Cambio.Antiguo $p.Cambio.Nuevo "SIMULADO" }; Write-Sim "Se cambiarian $($plan.Count) buzones"; return }
    if (-not $SinConfirmar) {
        $r = Read-Host "`nSe va a cambiar la direccion principal de $($plan.Count) buzones. ¿Continuar? (S/N)"
        if ($r -notmatch '^(s|si|sí)$') { Write-Aviso "Cancelado: no se ha cambiado nada."; return }
    }

    Write-Paso "Cambios"
    foreach ($p in $plan) {
        $c = $p.Cambio; $mbx = $p.Buzon
        $txt = "{0} -> {1}" -f $c.Antiguo, $c.Nuevo
        try {
            $nuevas = Get-NuevasDirecciones -Actuales @($mbx.EmailAddresses) -Nueva $c.Nuevo -Antigua $c.Antiguo
            $param = @{ Identity = "$($mbx.ExternalDirectoryObjectId)"; EmailAddresses = $nuevas }
            if ($c.Nombre -and $c.Nombre -ne $mbx.DisplayName) { $param.DisplayName = $c.Nombre }
            Set-Mailbox @param
            Write-Ok $txt
            Add-Resultado $c.Antiguo $c.Nuevo "OK" "Alias antiguo conservado$(if ($param.DisplayName) { "; nombre: $($c.Nombre)" })"
        } catch { Write-Fallo "$txt $($_.Exception.Message)"; Add-Resultado $c.Antiguo $c.Nuevo "ERROR" $_.Exception.Message }
    }
}
catch {
    Write-Fallo $_.Exception.Message
    Add-Resultado "" "" "ERROR" $_.Exception.Message
}
finally {
    if ($resultados.Count -gt 0) { $resultados | Export-Csv -LiteralPath $Registro -NoTypeInformation -Encoding $CodificacionCsv -Delimiter ";" }
    Write-Paso "Resumen"
    $resultados | Group-Object Estado | ForEach-Object { Write-Host ("  {0,-14} {1}" -f $_.Name, $_.Count) }
    if ($resultados.Count -gt 0) { Write-Host "  Resultados: $Registro" }
    if (Test-Path -LiteralPath $Vuelta) { Write-Host "  Para deshacer: .\Cambiar-DominioCorreo.ps1 -Csv `"$Vuelta`"" -ForegroundColor Yellow }
}
