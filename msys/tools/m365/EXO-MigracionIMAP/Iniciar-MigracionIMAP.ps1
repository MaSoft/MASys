#Requires -Version 5.1
<#
.SYNOPSIS
    Lanza una migracion IMAP a Exchange Online (IONOS, Arsys, Google, cualquier servidor IMAP) desde un CSV.

.DESCRIPTION
    1. Comprueba que cada buzon destino existe en Exchange Online (los que no, se apartan y se avisan).
    2. Crea el origen de migracion (endpoint IMAP) si no existe, o reutiliza el que haya con ese nombre.
    3. Crea el lote con las cuentas validas y lo arranca. El CSV de Exchange se construye en memoria: las contrasenas
       del servidor antiguo no se escriben en disco.
    La sincronizacion sigue sola en segundo plano y se repite cada 24 h hasta completar el lote (Complete-MigrationBatch)
    el dia del cambio de MX. Ver el estado con la herramienta EXO-EstadoMigracionIMAP.

.EXAMPLE
    .\Iniciar-MigracionIMAP.ps1 -Csv .\cuentas_imap.csv -ServidorImap imap.ionos.es -NombreLote "Migracion-Lote1" -Simular
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Csv,
    [Parameter(Mandatory = $true)][string]$ServidorImap,
    [Parameter(Mandatory = $true)][ValidatePattern('^[A-Za-z0-9._ -]{1,64}$')][string]$NombreLote,
    [ValidateRange(1, 65535)][int]$Puerto = 993,
    [ValidateSet("Ssl", "Tls", "None")][string]$Seguridad = "Ssl",
    [string]$NombreOrigen,
    [ValidateRange(1, 100)][int]$ConexionesSimultaneas = 10,
    [string[]]$CarpetasExcluidas,
    [string[]]$Notificar,
    [switch]$NoIniciar,
    [string]$TenantId,
    [switch]$Simular,
    [switch]$SinConfirmar,
    [string]$CarpetaResultados
)

$ErrorActionPreference = "Stop"
$CodificacionCsv = if ($PSVersionTable.PSVersion.Major -ge 6) { "utf8BOM" } else { "UTF8" }   # CSV con BOM en 5.1 y en 7: Excel abre bien las tildes
if (-not $NombreOrigen) { $NombreOrigen = "IMAP-$ServidorImap" }

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
    if ($p -and $null -ne $p.Value) { return ([string]$p.Value) }
    return ""
}

function Connect-ExchangeSeguro {
    <# Reutiliza la sesion de Exchange Online si hay una (volver a conectar sobre una valida puede fallar con WAM).
       Con -TenantId, se detiene si la sesion es de otro tenant. #>
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
    <# CSV de migracion IMAP para New-MigrationBatch: campos entrecomillados (las contrasenas llevan simbolos y una coma
       desplazaria las columnas sin dar error) y UTF-8 sin BOM (el BOM se cuela en la primera cabecera). En memoria. #>
    param([object[]]$Cuentas)
    $q = { param($s) '"' + ($s -replace '"', '""') + '"' }
    $lineas = @('"EmailAddress","UserName","Password"')
    foreach ($c in $Cuentas) { $lineas += ((& $q $c.Correo), (& $q $c.Usuario), (& $q $c.Contrasena)) -join "," }
    return (New-Object System.Text.UTF8Encoding($false)).GetBytes(($lineas -join "`r`n") + "`r`n")
}

# ============================================================ inicio
$inicio = Get-Date
if (-not $CarpetaResultados) { $CarpetaResultados = Join-Path $PSScriptRoot "Resultados" }
New-Item -ItemType Directory -Force -Path $CarpetaResultados | Out-Null
$Registro = Join-Path $CarpetaResultados ("MigracionIMAP_{0}_{1:yyyyMMdd_HHmmss}.csv" -f ($NombreLote -replace '[^A-Za-z0-9_-]', '_'), $inicio)
$resultados = New-Object System.Collections.Generic.List[object]
function Add-Resultado ($Correo, $Estado, $Detalle = "") {
    $resultados.Add([PSCustomObject]@{ Fecha = (Get-Date -Format "dd/MM/yyyy HH:mm:ss"); Correo = $Correo; Estado = $Estado; Detalle = $Detalle })
}

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " MIGRACION IMAP A EXCHANGE ONLINE" -ForegroundColor Cyan
if ($Simular) { Write-Host " MODO SIMULACION: no se crea nada" -ForegroundColor Magenta }
Write-Host "==============================================" -ForegroundColor Cyan

try {
    Write-Paso "Comprobaciones"
    $vistos = @{}
    $cuentas = @(foreach ($f in (Import-CsvFlexible -Ruta $Csv -Obligatorias @("Correo", "ContrasenaImap"))) {
            $correo = (Get-Valor $f "Correo").Trim().ToLower()
            if (-not $correo) { continue }
            if ($vistos.ContainsKey($correo)) { Write-Aviso "Correo repetido en el CSV, se usa la primera fila: $correo"; continue }
            $vistos[$correo] = $true
            $usuario = (Get-Valor $f "UsuarioImap").Trim(); if (-not $usuario) { $usuario = $correo }
            $pwd = Get-Valor $f "ContrasenaImap"   # sin Trim: un espacio puede ser parte de la contrasena
            if (-not $pwd) { Write-Aviso "$correo sin contrasena IMAP: se aparta"; Add-Resultado $correo "SIN CONTRASENA"; continue }
            [PSCustomObject]@{ Correo = $correo; Usuario = $usuario; Contrasena = $pwd }
        })
    if ($cuentas.Count -eq 0) { throw "No hay ninguna cuenta con contrasena en el CSV." }
    Write-Ok "$($cuentas.Count) cuentas con contrasena en el CSV"

    $con = Connect-ExchangeSeguro -TenantId $TenantId
    Write-Ok "Exchange Online: $($con.UserPrincipalName) (tenant $($con.TenantID))"
    if (-not $TenantId) { Write-Aviso "No se ha indicado -TenantId: comprueba que el tenant es el correcto." }

    # Un buzon que no existe hace fallar su fila del lote: se aparta antes
    Write-Paso "Buzones destino"
    $validas = New-Object System.Collections.Generic.List[object]
    foreach ($c in $cuentas) {
        $m = Get-EXOMailbox -Identity $c.Correo -ErrorAction SilentlyContinue
        if ($m) { $validas.Add($c); Write-Ok $c.Correo }
        else { Write-Fallo "$($c.Correo): no tiene buzon en Exchange Online (crealo y licencialo antes)"; Add-Resultado $c.Correo "SIN BUZON" }
    }
    if ($validas.Count -eq 0) { throw "Ninguna cuenta tiene buzon destino." }

    Write-Paso "Lote y origen"
    if (Get-MigrationBatch -Identity $NombreLote -ErrorAction SilentlyContinue) { throw "Ya existe un lote llamado '$NombreLote'. Usa otro nombre." }
    $origen = Get-MigrationEndpoint -Identity $NombreOrigen -ErrorAction SilentlyContinue
    if ($origen) {
        Write-Ok "Se reutiliza el origen '$NombreOrigen' ($($origen.RemoteServer):$($origen.Port))"
        if ($origen.RemoteServer -ne $ServidorImap) { Write-Aviso "El origen '$NombreOrigen' apunta a $($origen.RemoteServer), no a $ServidorImap" }
    } else {
        Write-Ok "Se creara el origen '$NombreOrigen' -> $ServidorImap`:$Puerto ($Seguridad), $ConexionesSimultaneas conexiones"
        try {
            Test-MigrationServerAvailability -IMAP -RemoteServer $ServidorImap -Port $Puerto -Security $Seguridad | Out-Null
            Write-Ok "El servidor IMAP responde"
        } catch { Write-Aviso "No se pudo comprobar el servidor IMAP: $($_.Exception.Message)" }
    }

    if ($Simular) {
        foreach ($c in $validas) { Add-Resultado $c.Correo "SIMULADO" }
        Write-Sim "Se crearia el lote '$NombreLote' con $($validas.Count) cuentas$(if (-not $NoIniciar) { ' y se arrancaria' })"
        return
    }
    if (-not $SinConfirmar) {
        $r = Read-Host "`nSe va a crear el lote '$NombreLote' con $($validas.Count) cuentas. ¿Continuar? (S/N)"
        if ($r -notmatch '^(s|si|sí)$') { Write-Aviso "Cancelado: no se ha creado nada."; return }
    }

    if (-not $origen) {
        # Los servidores IMAP compartidos (IONOS, Arsys) cortan si se abren muchas conexiones a la vez: se limitan
        $origen = New-MigrationEndpoint -IMAP -Name $NombreOrigen -RemoteServer $ServidorImap -Port $Puerto -Security $Seguridad `
            -MaxConcurrentMigrations $ConexionesSimultaneas -MaxConcurrentIncrementalSyncs ([math]::Max(1, [math]::Floor($ConexionesSimultaneas / 2)))
        Write-Ok "Origen '$NombreOrigen' creado"
    }
    $p = @{ Name = $NombreLote; SourceEndpoint = $origen.Identity; CSVData = (ConvertTo-CsvExchange $validas) }
    if (-not $NoIniciar) { $p.AutoStart = $true }
    if ($Notificar) { $p.NotificationEmails = $Notificar }
    if ($CarpetasExcluidas) { $p.ExcludeFolders = $CarpetasExcluidas }
    $lote = New-MigrationBatch @p
    Write-Ok "Lote '$NombreLote' creado$(if (-not $NoIniciar) { ' y arrancado' }) ($($validas.Count) cuentas)"
    foreach ($c in $validas) { Add-Resultado $c.Correo "EN LOTE" $NombreLote }

    Write-Host ""
    Write-Host "  Siguientes pasos:" -ForegroundColor Yellow
    Write-Host "   - Ver el estado:   ..\EXO-EstadoMigracionIMAP\Ver-EstadoMigracionIMAP.ps1 -Lote '$NombreLote'"
    Write-Host "   - Dia del cambio de MX, tras la ultima sincronizacion:   Complete-MigrationBatch -Identity '$NombreLote'"
}
catch {
    Write-Fallo $_.Exception.Message
    Add-Resultado "" "ERROR" $_.Exception.Message
}
finally {
    if ($resultados.Count -gt 0) { $resultados | Export-Csv -LiteralPath $Registro -NoTypeInformation -Encoding $CodificacionCsv -Delimiter ";" }
    Write-Paso "Resumen"
    $resultados | Group-Object Estado | ForEach-Object { Write-Host ("  {0,-16} {1}" -f $_.Name, $_.Count) }
    if ($resultados.Count -gt 0) { Write-Host "  Resultados (sin contrasenas): $Registro" }
    Write-Aviso "El CSV de entrada tiene las contrasenas del servidor antiguo: borralo cuando termine la migracion."
}
