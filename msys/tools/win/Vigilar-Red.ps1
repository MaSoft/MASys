# MASys 5.6: copia propia de _TEMP\SCRIPTS_2 (5d223d8). Único cambio: el bucle para también si existe el archivo
# de $env:MASYS_PARAR (botón «Parar» de MASys), para que el informe se genere igual que con Ctrl+C.
#Requires -Version 5.1
<#
.SYNOPSIS
    Vigila la conexion de un equipo durante un tiempo (pings cada segundo a la puerta de enlace, al DNS y a Internet,
    y consultas DNS) y genera un informe con cada microcorte y donde esta el problema: red local, Internet o DNS.

.DESCRIPTION
    Cada -IntervaloSegundos hace ping a la vez a: puerta de enlace, servidor DNS del equipo, 1.1.1.1 y 8.8.8.8 (y a
    -Destinos si se indican). Cada 10 s resuelve un nombre consultando directamente al DNS del equipo.
    Al terminar (o al pulsar Ctrl+C) agrupa los fallos en cortes y diagnostica cada uno:
      - cae tambien la puerta de enlace  -> red local: cable, Wi-Fi, switch o tarjeta de red de este equipo;
      - solo Internet                    -> router, fibra o proveedor;
      - solo DNS                         -> servidor DNS.
    El informe HTML (sin internet) incluye la linea de tiempo, perdida y latencia por destino, señal Wi-Fi,
    eventos de desconexion del adaptador y su ahorro de energia. Solo lee: no cambia la configuracion de red.

.EXAMPLE
    .\Vigilar-Red.ps1 -Minutos 60
    .\Vigilar-Red.ps1 -Minutos 480 -Destinos 192.168.1.10,servidor.empresa.local
#>

[CmdletBinding()]
param(
    [ValidateRange(1, 10080)][int]$Minutos = 60,
    [ValidateRange(1, 60)][int]$IntervaloSegundos = 1,
    [string[]]$Destinos,
    [ValidateRange(1, 30)][int]$FallosParaCorte = 2,
    [string]$NombrePruebaDns = "www.microsoft.com",
    [string]$CarpetaResultados
)

$ErrorActionPreference = "Stop"
$CodificacionCsv = if ($PSVersionTable.PSVersion.Major -ge 6) { "utf8BOM" } else { "UTF8" }

# ============================================================ utilidades comunes
function Write-Paso  ($t) { Write-Host "`n== $t" -ForegroundColor Cyan }
function Write-Ok    ($t) { Write-Host "  [OK]    $t" -ForegroundColor Green }
function Write-Aviso ($t) { Write-Host "  [AVISO] $t" -ForegroundColor Yellow }
function Write-Fallo ($t) { Write-Host "  [ERROR] $t" -ForegroundColor Red }

# ============================================================ funciones de calculo (probadas sin red)
function Get-CortesDestino {
    <#
        Fallos seguidos de un destino ($FallosMinimos o mas) -> cortes con Inicio y Fin (momento del ultimo fallo).
        Muestras: objetos con Momento (datetime) y Ok (bool), ordenadas por fecha.
    #>
    param([object[]]$Muestras, [int]$FallosMinimos)
    $cortes = New-Object System.Collections.Generic.List[object]
    $racha = New-Object System.Collections.Generic.List[object]
    foreach ($m in @($Muestras) + @([pscustomobject]@{ Momento = [datetime]::MaxValue; Ok = $true })) {
        if (-not $m.Ok) { $racha.Add($m); continue }
        if ($racha.Count -ge $FallosMinimos) { $cortes.Add([pscustomobject]@{ Inicio = $racha[0].Momento; Fin = $racha[$racha.Count - 1].Momento; Fallos = $racha.Count }) }
        $racha.Clear()
    }
    return , $cortes.ToArray()
}

function Join-Incidencias {
    <# Junta los cortes de varios destinos que se solapan (o distan menos de $Margen s) en incidencias. #>
    param([object[]]$Cortes, [int]$Margen)
    $inc = New-Object System.Collections.Generic.List[object]
    foreach ($c in ($Cortes | Sort-Object Inicio)) {
        $u = if ($inc.Count) { $inc[$inc.Count - 1] } else { $null }
        if ($u -and $c.Inicio -le $u.Fin.AddSeconds($Margen)) {
            if ($c.Fin -gt $u.Fin) { $u.Fin = $c.Fin }
            [void]$u.Tipos.Add($c.Tipo); [void]$u.Destinos.Add($c.Destino)
        } else {
            $n = [pscustomobject]@{ Inicio = $c.Inicio; Fin = $c.Fin; Tipos = (New-Object 'System.Collections.Generic.HashSet[string]'); Destinos = (New-Object 'System.Collections.Generic.HashSet[string]') }
            [void]$n.Tipos.Add($c.Tipo); [void]$n.Destinos.Add($c.Destino)
            $inc.Add($n)
        }
    }
    return , $inc.ToArray()
}

function Get-DiagnosticoIncidencia {
    <# Donde esta el problema segun que tipos de destino fallaron a la vez. #>
    param([string[]]$Tipos, [int]$DestinosInternet, [int]$InternetCaidos)
    if ($Tipos -contains "Puerta de enlace") { return "Red local: cable, Wi-Fi, switch o tarjeta de red de este equipo" }
    if ($Tipos -contains "Internet" -and $InternetCaidos -ge [math]::Max(1, $DestinosInternet)) { return "Internet: router, fibra o proveedor (la red local respondía)" }
    if ($Tipos -contains "Internet") { return "Solo un destino de Internet: problema de ese destino, no de la conexión" }
    if ($Tipos -contains "Consulta DNS" -or $Tipos -contains "Servidor DNS") { return "DNS: el servidor DNS no respondía (la conexión funcionaba)" }
    return "Destino propio sin respuesta (" + ($Tipos -join ", ") + ")"
}

function Get-SenalWifi {
    <# Señal en % de la salida de «netsh wlan show interfaces» (español o inglés). Vacio si no hay Wi-Fi. #>
    param([string[]]$Lineas)
    foreach ($l in $Lineas) { if ($l -match '^\s*(Se.al|Signal)\s*:\s*(\d+)\s*%') { return [int]$Matches[2] } }
    return $null
}

function Get-Percentil {
    param([double[]]$Valores, [double]$P)
    if (-not $Valores -or $Valores.Count -eq 0) { return 0 }
    $o = [double[]]($Valores | Sort-Object)
    $i = [math]::Min($o.Count - 1, [math]::Max(0, [math]::Ceiling($P / 100 * $o.Count) - 1))
    return [math]::Round($o[$i], 1)
}

# ============================================================ preparacion
$inicio = Get-Date
if (-not $CarpetaResultados) { $CarpetaResultados = Join-Path $PSScriptRoot "Resultados" }
New-Item -ItemType Directory -Force -Path $CarpetaResultados | Out-Null
$base = "Red_{0}_{1:yyyyMMdd_HHmmss}" -f $env:COMPUTERNAME, $inicio
$rutaCsv = Join-Path $CarpetaResultados "$base.csv"
$muestras = New-Object System.Collections.Generic.List[object]
$senales = New-Object System.Collections.Generic.List[object]
$inv = [Globalization.CultureInfo]::InvariantCulture

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " VIGILANCIA DE RED - $env:COMPUTERNAME" -ForegroundColor Cyan
Write-Host " $Minutos min, cada $IntervaloSegundos s. Ctrl+C termina antes y genera el informe." -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

Write-Paso "Conexión del equipo"
$ruta = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -ErrorAction SilentlyContinue | Sort-Object { $_.RouteMetric + $_.InterfaceMetric } | Select-Object -First 1
$puerta = if ($ruta) { $ruta.NextHop } else { $null }
$adaptador = if ($ruta) { Get-NetAdapter -InterfaceIndex $ruta.InterfaceIndex -ErrorAction SilentlyContinue } else { $null }
$dnsServ = if ($ruta) { @((Get-DnsClientServerAddress -InterfaceIndex $ruta.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue).ServerAddresses) | Select-Object -First 1 } else { $null }
$infoEquipo = [ordered]@{ "Equipo" = $env:COMPUTERNAME; "Inicio" = $inicio.ToString("dd/MM/yyyy HH:mm:ss") }
if ($adaptador) {
    $infoEquipo["Adaptador"] = "$($adaptador.Name) - $($adaptador.InterfaceDescription)"
    $infoEquipo["Velocidad"] = [string]$adaptador.LinkSpeed
    $infoEquipo["Controlador"] = "$($adaptador.DriverVersion) del $(if ($adaptador.DriverDate) { ([datetime]$adaptador.DriverDate).ToString('dd/MM/yyyy') })"
    try {
        $ahorro = Get-NetAdapterPowerManagement -Name $adaptador.Name -ErrorAction Stop
        $infoEquipo["Ahorro de energía"] = "Permitir que el equipo apague el dispositivo: $(if ([string]$ahorro.AllowComputerToTurnOffDevice -match 'Enabled') { 'SÍ (puede provocar cortes)' } else { 'no' })"
    } catch { }
}
$infoEquipo["Puerta de enlace"] = [string]$puerta
$infoEquipo["Servidor DNS"] = [string]$dnsServ
$wifi = @()
try { $wifi = @(& netsh wlan show interfaces 2>$null) } catch { }
$esWifi = $null -ne (Get-SenalWifi $wifi)
if ($esWifi) {
    foreach ($l in $wifi) {
        if ($l -match '^\s*SSID\s*:\s*(.+)$') { $infoEquipo["Wi-Fi (SSID)"] = $Matches[1].Trim() }
        if ($l -match '^\s*BSSID\s*:\s*(.+)$') { $infoEquipo["Punto de acceso (BSSID)"] = $Matches[1].Trim() }
        if ($l -match '^\s*(Canal|Channel)\s*:\s*(.+)$') { $infoEquipo["Canal"] = $Matches[2].Trim() }
        if ($l -match '^\s*(Tipo de radio|Radio type)\s*:\s*(.+)$') { $infoEquipo["Tipo de radio"] = $Matches[2].Trim() }
    }
}
foreach ($k in $infoEquipo.Keys) { Write-Host ("  {0,-26} {1}" -f $k, $infoEquipo[$k]) }

$objetivos = New-Object System.Collections.Generic.List[object]
if ($puerta -and $puerta -ne "0.0.0.0") { $objetivos.Add([pscustomobject]@{ Destino = $puerta; Tipo = "Puerta de enlace" }) } else { Write-Aviso "No se encuentra la puerta de enlace: ¿el equipo tiene red?" }
if ($dnsServ -and $dnsServ -ne $puerta) { $objetivos.Add([pscustomobject]@{ Destino = $dnsServ; Tipo = "Servidor DNS" }) }
foreach ($d in "1.1.1.1", "8.8.8.8") { $objetivos.Add([pscustomobject]@{ Destino = $d; Tipo = "Internet" }) }
foreach ($d in @($Destinos | ForEach-Object { $_ -split ',' })) { if ($d.Trim()) { $objetivos.Add([pscustomobject]@{ Destino = $d.Trim(); Tipo = "Propio" }) } }

# ============================================================ vigilancia
$fin = $inicio.AddMinutes($Minutos)
$sw = New-Object System.IO.StreamWriter($rutaCsv, $false, (New-Object System.Text.UTF8Encoding($true)))
$sw.WriteLine("Fecha;Hora;Destino;Tipo;Resultado;ms")
$ultimaDns = [datetime]::MinValue; $ultimaSenal = [datetime]::MinValue; $vueltas = 0; $fallosTotales = 0
try {
    Write-Paso "Vigilando hasta las $($fin.ToString('HH:mm:ss'))"
    while ((Get-Date) -lt $fin -and -not ($env:MASYS_PARAR -and (Test-Path -LiteralPath $env:MASYS_PARAR))) {
        $t0 = Get-Date
        $tareas = foreach ($o in $objetivos) { $p = New-Object System.Net.NetworkInformation.Ping; [pscustomobject]@{ O = $o; P = $p; T = $p.SendPingAsync($o.Destino, 1000) } }
        try { [void][System.Threading.Tasks.Task]::WaitAll([System.Threading.Tasks.Task[]]@($tareas | ForEach-Object { $_.T }), 3000) } catch { }
        foreach ($t in $tareas) {
            $ok = $false; $ms = ""
            if ($t.T.Status -eq "RanToCompletion" -and $t.T.Result.Status -eq "Success") { $ok = $true; $ms = [int]$t.T.Result.RoundtripTime }
            $muestras.Add([pscustomobject]@{ Momento = $t0; Destino = $t.O.Destino; Tipo = $t.O.Tipo; Ok = $ok; Ms = $ms })
            $sw.WriteLine(("{0};{1};{2};{3};{4};{5}" -f $t0.ToString("dd/MM/yyyy", $inv), $t0.ToString("HH:mm:ss", $inv), $t.O.Destino, $t.O.Tipo, $(if ($ok) { "OK" } else { "SIN RESPUESTA" }), $ms))
            if (-not $ok) { $fallosTotales++ }
            $t.P.Dispose()
        }
        if ($dnsServ -and ($t0 - $ultimaDns).TotalSeconds -ge 10) {
            $ultimaDns = $t0
            $okD = $false; $msD = ""
            $c = [System.Diagnostics.Stopwatch]::StartNew()
            try { [void](Resolve-DnsName -Name $NombrePruebaDns -Server $dnsServ -Type A -DnsOnly -QuickTimeout -ErrorAction Stop); $okD = $true; $msD = [int]$c.ElapsedMilliseconds } catch { }
            $muestras.Add([pscustomobject]@{ Momento = $t0; Destino = "$NombrePruebaDns @ $dnsServ"; Tipo = "Consulta DNS"; Ok = $okD; Ms = $msD })
            $sw.WriteLine(("{0};{1};{2};{3};{4};{5}" -f $t0.ToString("dd/MM/yyyy", $inv), $t0.ToString("HH:mm:ss", $inv), "$NombrePruebaDns @ $dnsServ", "Consulta DNS", $(if ($okD) { "OK" } else { "SIN RESPUESTA" }), $msD))
        }
        if ($esWifi -and ($t0 - $ultimaSenal).TotalSeconds -ge 30) {
            $ultimaSenal = $t0
            try { $s = Get-SenalWifi @(& netsh wlan show interfaces 2>$null); if ($null -ne $s) { $senales.Add([pscustomobject]@{ Momento = $t0; Senal = $s }) } } catch { }
        }
        $vueltas++
        if ($vueltas % 10 -eq 0) { $sw.Flush(); Write-Host ("`r  {0}  muestras {1}  sin respuesta {2}   " -f $t0.ToString("HH:mm:ss"), $vueltas, $fallosTotales) -NoNewline }
        $espera = $IntervaloSegundos * 1000 - ((Get-Date) - $t0).TotalMilliseconds
        if ($espera -gt 0) { Start-Sleep -Milliseconds ([int]$espera) }
    }
}
finally {
    $sw.Dispose()
    Write-Host ""
    # ============================================================ informe (tambien tras Ctrl+C)
    $finReal = Get-Date
    $cortes = New-Object System.Collections.Generic.List[object]
    $stats = foreach ($g in ($muestras | Group-Object Destino)) {
        $ms = @($g.Group | Sort-Object Momento)
        foreach ($c in (Get-CortesDestino -Muestras $ms -FallosMinimos $(if ($ms[0].Tipo -eq "Consulta DNS") { 1 } else { $FallosParaCorte }))) {
            $cortes.Add([pscustomobject]@{ Inicio = $c.Inicio; Fin = $c.Fin; Destino = $g.Name; Tipo = $ms[0].Tipo })
        }
        $lat = [double[]]@($ms | Where-Object Ok | ForEach-Object { [double]$_.Ms })
        [pscustomobject]@{ Destino = $g.Name; Tipo = $ms[0].Tipo; Muestras = $ms.Count; Perdidas = @($ms | Where-Object { -not $_.Ok }).Count
            PerdidaPct = [math]::Round(100 * @($ms | Where-Object { -not $_.Ok }).Count / [math]::Max(1, $ms.Count), 2)
            Media = if ($lat.Count) { [math]::Round(($lat | Measure-Object -Average).Average, 1) } else { 0 }; P95 = Get-Percentil $lat 95; Max = if ($lat.Count) { ($lat | Measure-Object -Maximum).Maximum } else { 0 } }
    }
    $ordenTipo = @{ "Puerta de enlace" = 1; "Servidor DNS" = 2; "Internet" = 3; "Consulta DNS" = 4; "Propio" = 5 }
    $stats = @($stats | Sort-Object { $ordenTipo[$_.Tipo] }, Destino)
    $nInternet = @($objetivos | Where-Object Tipo -eq "Internet").Count
    $incidencias = @(Join-Incidencias -Cortes $cortes.ToArray() -Margen ([math]::Max(3, $IntervaloSegundos * 2)) | ForEach-Object {
            $caidosInternet = @($_.Destinos | Where-Object { $d = $_; @($objetivos | Where-Object { $_.Destino -eq $d -and $_.Tipo -eq "Internet" }).Count }).Count
            $_ | Add-Member -NotePropertyName Diagnostico -NotePropertyValue (Get-DiagnosticoIncidencia -Tipos @($_.Tipos) -DestinosInternet $nInternet -InternetCaidos $caidosInternet) -PassThru })
    $eventos = @()
    try {
        $eventos = @(Get-WinEvent -FilterHashtable @{ LogName = "Microsoft-Windows-WLAN-AutoConfig/Operational"; Id = 8001, 8003; StartTime = $inicio } -ErrorAction SilentlyContinue) +
            @(Get-WinEvent -FilterHashtable @{ LogName = "Microsoft-Windows-NetworkProfile/Operational"; Id = 10000, 10001; StartTime = $inicio } -ErrorAction SilentlyContinue) +
            @(Get-WinEvent -FilterHashtable @{ LogName = "System"; StartTime = $inicio } -ErrorAction SilentlyContinue | Where-Object { $_.ProviderName -match '^(e1|e2|Netwtw|Netwbw|rt|Realtek|Intel|NDIS|Tcpip|Dhcp|b57|bcm|athw|Marvell|Killer|mrvl|RTL)' })
    } catch { }
    $eventos = @($eventos | Sort-Object TimeCreated)

    $H = { param($t) [System.Net.WebUtility]::HtmlEncode([string]$t) }
    $durT = [math]::Max(1, ($finReal - $inicio).TotalSeconds)
    $X = { param($m) [string]::Format($inv, "{0:0.##}", 1000 * ($m - $inicio).TotalSeconds / $durT) }
    $filasSvg = New-Object System.Collections.Generic.List[string]
    $fila = 0
    foreach ($s in $stats) {
        $y = 10 + $fila * 26
        $filasSvg.Add(('<text x="0" y="{0}" font-size="11" fill="#555">{1}</text><rect x="0" y="{2}" width="1000" height="10" fill="#bbf7d0"/>' -f ($y + 9), (& $H "$($s.Tipo): $($s.Destino)"), ($y + 12)))
        foreach ($c in ($cortes | Where-Object Destino -eq $s.Destino)) {
            $w = [math]::Max(2, 1000 * (($c.Fin - $c.Inicio).TotalSeconds + $IntervaloSegundos) / $durT)
            $filasSvg.Add(([string]::Format($inv, '<rect x="{0}" y="{1}" width="{2:0.##}" height="10" fill="#dc2626"/>', (& $X $c.Inicio), $y + 12, $w)))
        }
        $fila++
    }
    $altoSvg = 20 + $fila * 26
    $marcas = (0..4 | ForEach-Object { $t = $inicio.AddSeconds($durT * $_ / 4); ('<text x="{0}" y="{1}" font-size="11" fill="#555" text-anchor="{2}">{3}</text>' -f (250 * $_), ($altoSvg + 4), $(if ($_ -eq 0) { "start" } elseif ($_ -eq 4) { "end" } else { "middle" }), $t.ToString("dd/MM HH:mm:ss")) }) -join ""
    $filasInc = ($incidencias | ForEach-Object { $dur = [math]::Round(($_.Fin - $_.Inicio).TotalSeconds + $IntervaloSegundos)
            "<tr><td>$($_.Inicio.ToString('dd/MM/yyyy'))</td><td>$($_.Inicio.ToString('HH:mm:ss'))</td><td>$dur</td><td>$(& $H (($_.Destinos | Sort-Object) -join ', '))</td><td>$(& $H $_.Diagnostico)</td></tr>" }) -join "`n"
    $filasSt = ($stats | ForEach-Object { "<tr><td>$(& $H $_.Tipo)</td><td>$(& $H $_.Destino)</td><td>$($_.Muestras)</td><td>$($_.Perdidas)</td><td$(if ($_.PerdidaPct -ge 1) { ' class="mal"' })>$($_.PerdidaPct)</td><td>$($_.Media)</td><td>$($_.P95)</td><td>$($_.Max)</td></tr>" }) -join "`n"
    $filasEv = ($eventos | Select-Object -First 300 | ForEach-Object { "<tr><td>$($_.TimeCreated.ToString('dd/MM/yyyy HH:mm:ss'))</td><td>$(& $H $_.ProviderName)</td><td>$($_.Id)</td><td>$(& $H (([string]$_.Message -split "`n")[0]))</td></tr>" }) -join "`n"
    $txtSenal = if ($senales.Count) { "mínima $(($senales | Measure-Object Senal -Minimum).Minimum) %, media $([math]::Round(($senales | Measure-Object Senal -Average).Average)) % ($($senales.Count) lecturas)" } else { "" }
    if ($txtSenal) { $infoEquipo["Señal Wi-Fi"] = $txtSenal }
    $infoEquipo["Fin"] = $finReal.ToString("dd/MM/yyyy HH:mm:ss")
    $conclusiones = New-Object System.Collections.Generic.List[string]
    $porDiag = $incidencias | Group-Object Diagnostico | Sort-Object Count -Descending
    if (-not $incidencias.Count) { $conclusiones.Add("No ha habido ningún corte en el periodo ($([math]::Round($durT / 60)) min).") }
    foreach ($g in $porDiag) { $conclusiones.Add("$($g.Count) cortes de tipo «$($g.Name)».") }
    if ($senales.Count -and ($senales | Measure-Object Senal -Minimum).Minimum -lt 50) { $conclusiones.Add("La señal Wi-Fi ha bajado del 50 %: acercar el punto de acceso, cambiar de canal o usar cable.") }
    if ($infoEquipo["Ahorro de energía"] -match "SÍ") { $conclusiones.Add("La tarjeta de red puede apagarse para ahorrar energía: desactivarlo (Administrador de dispositivos > tarjeta > Administración de energía) es lo primero si los cortes son de red local.") }
    if (@($eventos | Where-Object { $_.Id -in 8003, 10001 }).Count) { $conclusiones.Add("Windows registró desconexiones del adaptador durante el periodo (ver eventos).") }

    $html = @"
<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Red - $(& $H $env:COMPUTERNAME)</title>
<style>
:root{--primary:#1a3a5c;--bg:#f7f9fc;--card:#fff;--text:#1a1a2e;--muted:#555;--border:#dde3ea;--bad:#dc2626}
*{box-sizing:border-box}body{margin:0;font-family:"Segoe UI",Arial,sans-serif;background:var(--bg);color:var(--text);font-size:14px}
header{background:linear-gradient(135deg,#0f2440,#1a3a5c 60%,#2c5f8a);color:#fff;padding:22px 28px}header h1{margin:0;font-weight:300;font-size:26px}header p{margin:6px 0 0;opacity:.85}
main{padding:20px 28px;max-width:1300px}h2{color:var(--primary);border-bottom:2px solid var(--primary);padding-bottom:4px;font-weight:500;font-size:18px;margin-top:26px}
.card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:14px}svg{width:100%;height:auto}
.tabla{overflow:auto;background:var(--card);border:1px solid var(--border);border-radius:8px}table{border-collapse:collapse;width:100%}
th{background:var(--primary);color:#fff;text-align:left;padding:8px;font-weight:600;white-space:nowrap}td{padding:6px 8px;border-top:1px solid var(--border);vertical-align:top}tr:nth-child(even) td{background:#f8fafc}
.mal{color:var(--bad);font-weight:600}ul li{margin:6px 0}dl.info{display:grid;grid-template-columns:200px 1fr;gap:6px 14px}dl.info dt{color:var(--muted)}dl.info dd{margin:0}
@media (max-width:700px){main,header{padding:14px}dl.info{grid-template-columns:1fr}}
</style></head><body>
<header><h1>Vigilancia de red</h1><p>$(& $H $env:COMPUTERNAME) · $($inicio.ToString('dd/MM/yyyy HH:mm:ss')) - $($finReal.ToString('HH:mm:ss')) · cada $IntervaloSegundos s · $($incidencias.Count) cortes</p></header>
<main>
<h2>Conclusiones</h2><div class="card"><ul>$(($conclusiones | ForEach-Object { "<li>$(& $H $_)</li>" }) -join '')</ul></div>
<h2>Línea de tiempo (rojo = sin respuesta)</h2><div class="card"><svg viewBox="0 0 1000 $($altoSvg + 8)" preserveAspectRatio="none">$($filasSvg -join '')$marcas</svg></div>
<h2>Cortes</h2>$(if ($incidencias.Count) { "<div class=""tabla""><table><tr><th>Fecha</th><th>Hora</th><th>Segundos</th><th>Destinos sin respuesta</th><th>Dónde está el problema</th></tr>$filasInc</table></div>" } else { "<div class=""card"">Ninguno.</div>" })
<h2>Destinos</h2><div class="tabla"><table><tr><th>Tipo</th><th>Destino</th><th>Pruebas</th><th>Sin respuesta</th><th>Pérdida %</th><th>Latencia media ms</th><th>p95 ms</th><th>Máx. ms</th></tr>$filasSt</table></div>
<h2>Eventos de red de Windows en el periodo</h2>$(if ($eventos.Count) { "<div class=""tabla""><table><tr><th>Fecha</th><th>Origen</th><th>Id</th><th>Mensaje</th></tr>$filasEv</table></div>" } else { "<div class=""card"">Ninguno.</div>" })
<h2>Equipo y conexión</h2><div class="card"><dl class="info">$(($infoEquipo.Keys | ForEach-Object { "<dt>$(& $H $_)</dt><dd>$(& $H $infoEquipo[$_])</dd>" }) -join '')</dl></div>
<p style="color:#555;font-size:12px">Un corte son $FallosParaCorte o más pruebas seguidas sin respuesta de un destino (una sola en la consulta DNS). Las pruebas se hacen a la vez a todos los destinos, así que se puede saber si cayó la red local o solo Internet.</p>
</main></body></html>
"@
    $rutaHtml = Join-Path $CarpetaResultados "$base.html"
    [System.IO.File]::WriteAllText($rutaHtml, $html, (New-Object System.Text.UTF8Encoding($true)))
    Write-Paso "Resumen"
    foreach ($c in $conclusiones) { Write-Host "  * $c" -ForegroundColor $(if ($incidencias.Count) { "Yellow" } else { "Green" }) }
    Write-Ok "Informe: $rutaHtml"
    Write-Ok "Datos:   $rutaCsv"
}
