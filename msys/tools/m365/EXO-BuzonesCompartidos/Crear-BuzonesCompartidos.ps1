#Requires -Version 5.1
<#
.SYNOPSIS
    Crea buzones compartidos en Exchange Online desde un CSV y les da los permisos iniciales (Acceso total y Enviar como).

.DESCRIPTION
    Por cada fila:
      - Si el buzon ya existe, no lo vuelve a crear (solo revisa los permisos).
      - Si no, lo crea con su direccion y nombre.
      - Da Acceso total (con o sin que aparezca solo en Outlook) y Enviar como a los usuarios de las columnas
        AccesoTotal y EnviarComo (varios separados por | o ,).
      - Activa que lo enviado como el buzon se guarde tambien en los Elementos enviados del buzon compartido.
    Los buzones compartidos no necesitan licencia (hasta 50 GB).

.EXAMPLE
    .\Crear-BuzonesCompartidos.ps1 -Csv .\compartidos.csv -TenantId 00000000-0000-0000-0000-000000000000 -Simular
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Csv,
    [switch]$SinMapeoAutomatico,
    [switch]$SinCopiaEnviados,
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

function Split-Lista ([string]$Texto) {
    <# "a@x.com | b@x.com, c@x.com" -> lista sin vacios (en un CSV con ; las comas internas son seguras) #>
    return @($Texto -split '[|,]' | ForEach-Object { $_.Trim().ToLower() } | Where-Object { $_ })
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

$inicio = Get-Date
if (-not $CarpetaResultados) { $CarpetaResultados = Join-Path $PSScriptRoot "Resultados" }
New-Item -ItemType Directory -Force -Path $CarpetaResultados | Out-Null
$Registro = Join-Path $CarpetaResultados ("BuzonesCompartidos_{0:yyyyMMdd_HHmmss}.csv" -f $inicio)
$resultados = New-Object System.Collections.Generic.List[object]
function Add-Resultado ($Buzon, $Accion, $Estado, $Detalle = "") {
    $resultados.Add([PSCustomObject]@{ Fecha = (Get-Date -Format "dd/MM/yyyy HH:mm:ss"); Buzon = $Buzon; Accion = $Accion; Estado = $Estado; Detalle = $Detalle })
}

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " BUZONES COMPARTIDOS EN EXCHANGE ONLINE" -ForegroundColor Cyan
if ($Simular) { Write-Host " MODO SIMULACION: no se cambia nada" -ForegroundColor Magenta }
Write-Host "==============================================" -ForegroundColor Cyan

try {
    Write-Paso "Comprobaciones"
    $vistos = @{}
    $buzones = @(foreach ($f in (Import-CsvFlexible -Ruta $Csv -Obligatorias @("Correo"))) {
            $c = (Get-Valor $f "Correo").ToLower()
            if ($c -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') { if ($c) { Write-Aviso "Correo no valido, se omite: $c" }; continue }
            if ($vistos.ContainsKey($c)) { continue }
            $vistos[$c] = $true
            $n = Get-Valor $f "Nombre"; if (-not $n) { $n = $c.Split("@")[0] }
            [PSCustomObject]@{ Correo = $c; Nombre = $n; AccesoTotal = (Split-Lista (Get-Valor $f "AccesoTotal")); EnviarComo = (Split-Lista (Get-Valor $f "EnviarComo")) }
        })
    if ($buzones.Count -eq 0) { throw "No hay ningun buzon valido en el CSV." }
    Write-Ok "$($buzones.Count) buzones en el CSV"

    $con = Connect-ExchangeSeguro -TenantId $TenantId
    Write-Ok "Exchange Online: $($con.UserPrincipalName) (tenant $($con.TenantID))"
    $aceptados = @(Get-AcceptedDomain | ForEach-Object { "$($_.DomainName)".ToLower() })
    foreach ($d in ($buzones | ForEach-Object { $_.Correo.Split("@")[1] } | Select-Object -Unique)) {
        if ($aceptados -notcontains $d) { throw "El dominio '$d' no esta aceptado en el tenant." }
    }
    # Los usuarios a los que se da permiso deben existir antes de tocar nada
    $faltan = @($buzones | ForEach-Object { $_.AccesoTotal + $_.EnviarComo } | Select-Object -Unique |
            Where-Object { -not (Get-EXORecipient -Identity $_ -ErrorAction SilentlyContinue) })
    if ($faltan.Count) { throw "Estos usuarios de los permisos no existen en el tenant: $($faltan -join ', ')" }
    Write-Ok "Usuarios de los permisos comprobados"

    if (-not $Simular -and -not $SinConfirmar) {
        $r = Read-Host "`nSe van a crear o revisar $($buzones.Count) buzones compartidos con sus permisos. ¿Continuar? (S/N)"
        if ($r -notmatch '^(s|si|sí)$') { Write-Aviso "Cancelado: no se ha cambiado nada."; return }
    }

    foreach ($b in $buzones) {
        Write-Paso $b.Correo
        try {
            $m = Get-EXOMailbox -Identity $b.Correo -Properties RecipientTypeDetails -ErrorAction SilentlyContinue
            if ($m) {
                if ($m.RecipientTypeDetails -ne "SharedMailbox") { Write-Aviso "Ya existe pero es $($m.RecipientTypeDetails), no compartido: se dan los permisos igualmente" }
                else { Write-Ok "Ya existia" }
                Add-Resultado $b.Correo "CREAR" "YA EXISTIA" "$($m.RecipientTypeDetails)"
            } elseif ($Simular) {
                Write-Sim "Se crearia '$($b.Nombre)'"; Add-Resultado $b.Correo "CREAR" "SIMULADO"
            } else {
                $alias = ($b.Correo.Split("@")[0] -replace '[^A-Za-z0-9._-]', '')
                New-Mailbox -Shared -Name $b.Nombre -DisplayName $b.Nombre -Alias $alias -PrimarySmtpAddress $b.Correo | Out-Null
                Write-Ok "Creado '$($b.Nombre)'"; Add-Resultado $b.Correo "CREAR" "OK"
                # Recien creado tarda unos segundos en aceptar permisos
                $limite = (Get-Date).AddMinutes(3)
                while (-not (Get-EXOMailbox -Identity $b.Correo -ErrorAction SilentlyContinue) -and (Get-Date) -lt $limite) { Start-Sleep -Seconds 10 }
            }
            if (-not $Simular -and -not $SinCopiaEnviados) {
                Set-Mailbox -Identity $b.Correo -MessageCopyForSentAsEnabled $true -MessageCopyForSendOnBehalfEnabled $true
            }
            foreach ($u in $b.AccesoTotal) {
                if ($Simular) { Write-Sim "Acceso total para $u"; continue }
                for ($i = 1; $i -le 3; $i++) {
                    try { Add-MailboxPermission -Identity $b.Correo -User $u -AccessRights FullAccess -InheritanceType All -AutoMapping (-not $SinMapeoAutomatico) | Out-Null; break }
                    catch { if ($i -eq 3) { throw }; Start-Sleep -Seconds 15 }
                }
                Write-Ok "Acceso total: $u"; Add-Resultado $b.Correo "ACCESO TOTAL" "OK" $u
            }
            foreach ($u in $b.EnviarComo) {
                if ($Simular) { Write-Sim "Enviar como para $u"; continue }
                try { Add-RecipientPermission -Identity $b.Correo -Trustee $u -AccessRights SendAs -Confirm:$false | Out-Null }
                catch { if ($_.Exception.Message -notmatch "already") { throw } }
                Write-Ok "Enviar como: $u"; Add-Resultado $b.Correo "ENVIAR COMO" "OK" $u
            }
        } catch {
            Write-Fallo $_.Exception.Message
            Add-Resultado $b.Correo "CREAR/PERMISOS" "ERROR" $_.Exception.Message
        }
    }
}
catch {
    Write-Fallo $_.Exception.Message
    Add-Resultado "" "GENERAL" "ERROR" $_.Exception.Message
}
finally {
    if ($resultados.Count -gt 0) { $resultados | Export-Csv -LiteralPath $Registro -NoTypeInformation -Encoding $CodificacionCsv -Delimiter ";" }
    Write-Paso "Resumen"
    $resultados | Group-Object Accion, Estado | ForEach-Object { Write-Host ("  {0,-30} {1}" -f $_.Name, $_.Count) }
    if ($resultados.Count -gt 0) { Write-Host "  Resultados: $Registro" }
    Write-Host "  Los permisos pueden tardar hasta 60 minutos en verse en Outlook." -ForegroundColor DarkGray
}
