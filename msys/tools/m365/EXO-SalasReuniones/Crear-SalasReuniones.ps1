#Requires -Version 5.1
<#
.SYNOPSIS
    Crea y configura salas de reuniones en Exchange Online desde un CSV: buzon de sala, reserva automatica, quien puede
    reservar, horario, zona horaria y lo que ven los demas en el calendario.

.DESCRIPTION
    Por cada sala del CSV (si ya existe, solo se reconfigura):
      1. Buzon de sala con su capacidad.
      2. Reserva automatica: acepta si esta libre, rechaza solapes, duracion minima y maxima, antelacion, reuniones periodicas.
      3. Quien puede reservar: todos o solo los usuarios de la columna Autorizados.
      4. Horario laboral y zona horaria (se comprueba que la zona existe y se muestra su nombre completo).
      5. Permiso del calendario para el resto de la organizacion (por defecto: ver asunto y lugar).
    Al final comprueba cada sala y muestra su configuracion. Hace en una sola ejecucion los seis pasos que antes iban por separado.

.EXAMPLE
    .\Crear-SalasReuniones.ps1 -Csv .\salas.csv -ZonaHoraria "GMT Standard Time" -Simular
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Csv,
    [ValidatePattern('^\d{2}:\d{2}$')][string]$HoraInicio = "08:00",
    [ValidatePattern('^\d{2}:\d{2}$')][string]$HoraFin = "20:00",
    [string]$ZonaHoraria = "Romance Standard Time",
    [ValidateRange(0, 1440)][int]$DuracionMinima = 30,
    [ValidateRange(0, 10080)][int]$DuracionMaxima = 0,
    [ValidateRange(1, 1080)][int]$DiasAntelacion = 365,
    [ValidateSet("Disponibilidad", "Detalles", "Revisor")][string]$VisibilidadCalendario = "Detalles",
    [switch]$NoPeriodicas,
    [switch]$PermitirSolapes,
    [string]$TenantId,
    [switch]$Simular,
    [switch]$SinConfirmar,
    [string]$CarpetaResultados
)

$ErrorActionPreference = "Stop"
$CodificacionCsv = if ($PSVersionTable.PSVersion.Major -ge 6) { "utf8BOM" } else { "UTF8" }   # CSV con BOM en 5.1 y en 7: Excel abre bien las tildes
$Visibilidad = @{ "Disponibilidad" = "AvailabilityOnly"; "Detalles" = "LimitedDetails"; "Revisor" = "Reviewer" }[$VisibilidadCalendario]

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

function Split-Lista ([string]$Texto) { return @($Texto -split '[|,]' | ForEach-Object { $_.Trim().ToLower() } | Where-Object { $_ }) }

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

function Get-CarpetaCalendario ([string]$Buzon) {
    <# Carpeta de calendario por su tipo (el nombre cambia con el idioma). #>
    $f = Get-EXOMailboxFolderStatistics -Identity $Buzon -FolderScope Calendar | Where-Object { $_.FolderType -eq "Calendar" } | Select-Object -First 1
    if (-not $f) { throw "No se encuentra la carpeta de calendario de $Buzon" }
    return "${Buzon}:\$($f.Name)"
}

$inicio = Get-Date
if (-not $CarpetaResultados) { $CarpetaResultados = Join-Path $PSScriptRoot "Resultados" }
New-Item -ItemType Directory -Force -Path $CarpetaResultados | Out-Null
$Registro = Join-Path $CarpetaResultados ("SalasReuniones_{0:yyyyMMdd_HHmmss}.csv" -f $inicio)
$resultados = New-Object System.Collections.Generic.List[object]
function Add-Resultado ($Sala, $Paso, $Estado, $Detalle = "") {
    $resultados.Add([PSCustomObject]@{ Fecha = (Get-Date -Format "dd/MM/yyyy HH:mm:ss"); Sala = $Sala; Paso = $Paso; Estado = $Estado; Detalle = $Detalle })
}

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " SALAS DE REUNIONES EN EXCHANGE ONLINE" -ForegroundColor Cyan
if ($Simular) { Write-Host " MODO SIMULACION: no se cambia nada" -ForegroundColor Magenta }
Write-Host "==============================================" -ForegroundColor Cyan

try {
    Write-Paso "Comprobaciones"
    # Zona horaria: se valida contra las de Windows. Ojo: "Atlantic Standard Time" es UTC-4 (Canada), NO Canarias.
    try { $tz = [System.TimeZoneInfo]::FindSystemTimeZoneById($ZonaHoraria) }
    catch { throw "La zona horaria '$ZonaHoraria' no existe. Ejemplos: 'Romance Standard Time' (Madrid), 'GMT Standard Time' (Canarias, Lisboa, Londres)." }
    Write-Ok "Zona horaria: $ZonaHoraria = $($tz.DisplayName)"
    if ([datetime]::ParseExact($HoraFin, "HH:mm", $null) -le [datetime]::ParseExact($HoraInicio, "HH:mm", $null)) { throw "La hora de fin debe ser posterior a la de inicio." }
    if ($DuracionMaxima -gt 0 -and $DuracionMaxima -lt $DuracionMinima) { throw "La duracion maxima es menor que la minima." }

    $vistos = @{}
    $salas = @(foreach ($f in (Import-CsvFlexible -Ruta $Csv -Obligatorias @("Correo", "Nombre"))) {
            $c = (Get-Valor $f "Correo").ToLower()
            if ($c -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$' -or $vistos.ContainsKey($c)) { continue }
            $vistos[$c] = $true
            $cap = 0; [void][int]::TryParse((Get-Valor $f "Capacidad"), [ref]$cap)
            [PSCustomObject]@{ Correo = $c; Nombre = (Get-Valor $f "Nombre"); Capacidad = $cap; Autorizados = (Split-Lista (Get-Valor $f "Autorizados")) }
        })
    if ($salas.Count -eq 0) { throw "No hay ninguna sala valida en el CSV." }
    Write-Ok "$($salas.Count) salas en el CSV"

    $con = Connect-ExchangeSeguro -TenantId $TenantId
    Write-Ok "Exchange Online: $($con.UserPrincipalName) (tenant $($con.TenantID))"
    $faltan = @($salas | ForEach-Object { $_.Autorizados } | Select-Object -Unique | Where-Object { -not (Get-EXORecipient -Identity $_ -ErrorAction SilentlyContinue) })
    if ($faltan.Count) { throw "Estos usuarios autorizados no existen en el tenant: $($faltan -join ', ')" }

    Write-Host ""
    Write-Host "  Configuracion para todas: $HoraInicio-$HoraFin, minimo $DuracionMinima min, maximo $(if ($DuracionMaxima) { "$DuracionMaxima min" } else { 'sin limite' }), $DiasAntelacion dias de antelacion,"
    Write-Host "  periodicas $(if ($NoPeriodicas) { 'NO' } else { 'si' }), solapes $(if ($PermitirSolapes) { 'SI' } else { 'no' }), el resto ve: $VisibilidadCalendario"
    if (-not $Simular -and -not $SinConfirmar) {
        $r = Read-Host "`nSe van a crear o reconfigurar $($salas.Count) salas. ¿Continuar? (S/N)"
        if ($r -notmatch '^(s|si|sí)$') { Write-Aviso "Cancelado: no se ha cambiado nada."; return }
    }

    foreach ($s in $salas) {
        Write-Paso "$($s.Nombre) <$($s.Correo)>"
        try {
            $m = Get-EXOMailbox -Identity $s.Correo -Properties RecipientTypeDetails -ErrorAction SilentlyContinue
            if ($m -and $m.RecipientTypeDetails -ne "RoomMailbox") { throw "La direccion ya es de un buzon de tipo $($m.RecipientTypeDetails), no de sala." }
            if ($Simular) {
                Write-Sim $(if ($m) { "ya existe: se reconfiguraria" } else { "se crearia con capacidad $($s.Capacidad)" })
                Write-Sim "reservan: $(if ($s.Autorizados) { $s.Autorizados -join ', ' } else { 'todos' })"
                Add-Resultado $s.Correo "TODO" "SIMULADO"; continue
            }
            if (-not $m) {
                $alias = ($s.Correo.Split("@")[0] -replace '[^A-Za-z0-9._-]', '')
                $p = @{ Room = $true; Name = $s.Nombre; DisplayName = $s.Nombre; Alias = $alias; PrimarySmtpAddress = $s.Correo }
                if ($s.Capacidad -gt 0) { $p.ResourceCapacity = $s.Capacidad }
                New-Mailbox @p | Out-Null
                Write-Ok "Sala creada"; Add-Resultado $s.Correo "CREAR" "OK"
                $limite = (Get-Date).AddMinutes(3)
                while (-not (Get-EXOMailbox -Identity $s.Correo -ErrorAction SilentlyContinue) -and (Get-Date) -lt $limite) { Start-Sleep -Seconds 10 }
                Start-Sleep -Seconds 20   # la configuracion de calendario tarda algo mas en estar disponible
            } else {
                if ($s.Capacidad -gt 0) { Set-Mailbox -Identity $s.Correo -ResourceCapacity $s.Capacidad }
                Write-Ok "Ya existia: se reconfigura"; Add-Resultado $s.Correo "CREAR" "YA EXISTIA"
            }

            $cp = @{ Identity = $s.Correo; AutomateProcessing = "AutoAccept"; AllowConflicts = [bool]$PermitirSolapes
                BookingWindowInDays = $DiasAntelacion; MinimumDurationInMinutes = $DuracionMinima; MaximumDurationInMinutes = $DuracionMaxima
                AllowRecurringMeetings = (-not $NoPeriodicas); DeleteSubject = $false; AddOrganizerToSubject = $false; RemovePrivateProperty = $false }
            if ($s.Autorizados.Count) { $cp.AllBookInPolicy = $false; $cp.BookInPolicy = $s.Autorizados } else { $cp.AllBookInPolicy = $true }
            Set-CalendarProcessing @cp
            Write-Ok "Reserva automatica; reservan: $(if ($s.Autorizados) { "$($s.Autorizados.Count) usuarios" } else { 'todos' })"
            Add-Resultado $s.Correo "RESERVA" "OK"

            Set-MailboxCalendarConfiguration -Identity $s.Correo -WorkingHoursTimeZone $ZonaHoraria -WorkingHoursStartTime "$($HoraInicio):00" -WorkingHoursEndTime "$($HoraFin):00"
            Write-Ok "Horario $HoraInicio-$HoraFin ($ZonaHoraria)"; Add-Resultado $s.Correo "HORARIO" "OK"

            Set-MailboxFolderPermission -Identity (Get-CarpetaCalendario $s.Correo) -User Default -AccessRights $Visibilidad | Out-Null
            Write-Ok "El resto de la organizacion ve: $VisibilidadCalendario"; Add-Resultado $s.Correo "VISIBILIDAD" "OK" $Visibilidad

            # Comprobacion final
            $v = Get-CalendarProcessing -Identity $s.Correo
            $h = Get-MailboxCalendarConfiguration -Identity $s.Correo
            $ok = $v.AutomateProcessing -eq "AutoAccept" -and $h.WorkingHoursTimeZone -eq $ZonaHoraria
            if ($ok) { Write-Ok "Comprobado" } else { Write-Aviso "Comprobacion: AutomateProcessing=$($v.AutomateProcessing), zona=$($h.WorkingHoursTimeZone)" }
            Add-Resultado $s.Correo "COMPROBACION" $(if ($ok) { "OK" } else { "REVISAR" })
        } catch {
            Write-Fallo $_.Exception.Message
            Add-Resultado $s.Correo "CONFIGURAR" "ERROR" $_.Exception.Message
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
    $resultados | Group-Object Paso, Estado | ForEach-Object { Write-Host ("  {0,-26} {1}" -f $_.Name, $_.Count) }
    if ($resultados.Count -gt 0) { Write-Host "  Resultados: $Registro" }
    Write-Host "  Las salas pueden tardar unas horas en aparecer en el buscador de salas de Outlook." -ForegroundColor DarkGray
}
