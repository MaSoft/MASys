# MASys 5.6: copia propia de _TEMP\SCRIPTS_2 (5d223d8). Único cambio: el bucle para también si existe el archivo
# de $env:MASYS_PARAR (botón «Parar» de MASys), para que el informe se genere igual que con Ctrl+C.
#Requires -Version 5.1
<#
.SYNOPSIS
    Vigila impresoras de red: ping, puerto de impresion, MAC (conflictos de IP o aparato cambiado) y la cola de
    impresion de este equipo. Informe HTML con caidas y disponibilidad, o foto rapida con -UnaVez.

.DESCRIPTION
    Impresoras: de un CSV (Nombre, IP y opcionalmente MAC y Puerto; plantilla_impresoras.csv) o, con -DesdeWindows,
    las impresoras instaladas en este equipo que usan un puerto TCP/IP.
    Cada -IntervaloSegundos, a la vez para todas:
      - ping y conexion al puerto de impresion (9100 por defecto; 515 LPD o 631 IPP si se indica);
      - MAC vista en la tabla ARP comparada con la esperada (otra MAC = conflicto de IP o aparato cambiado);
      - si esta instalada en este equipo: estado de la cola y trabajos con error o atascados.
    Al terminar (-Minutos o Ctrl+C) genera el informe. Solo lee: no reinicia colas ni borra trabajos.

.EXAMPLE
    .\Vigilar-Impresoras.ps1 -Impresoras .\impresoras.csv -Minutos 480
    .\Vigilar-Impresoras.ps1 -DesdeWindows -UnaVez
#>

[CmdletBinding()]
param(
    [string]$Impresoras,
    [switch]$DesdeWindows,
    [switch]$UnaVez,
    [ValidateRange(1, 10080)][int]$Minutos = 60,
    [ValidateRange(2, 600)][int]$IntervaloSegundos = 10,
    [ValidateRange(1, 30)][int]$FallosParaCaida = 2,
    [ValidateRange(1, 1440)][int]$MinutosTrabajoAtascado = 10,
    [string]$CarpetaResultados
)

$ErrorActionPreference = "Stop"
$CodificacionCsv = if ($PSVersionTable.PSVersion.Major -ge 6) { "utf8BOM" } else { "UTF8" }

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

# ============================================================ funciones de calculo (probadas sin red)
function ConvertTo-MacNormal {
    <# 00:11:22:aa:bb:cc, 00-11-22-AA-BB-CC o 001122aabbcc -> 00-11-22-AA-BB-CC. Vacio si no es una MAC. #>
    param([string]$Mac)
    $h = ([string]$Mac -replace '[^0-9A-Fa-f]', '').ToUpper()
    if ($h.Length -ne 12) { return "" }
    return (($h -split '(..)' | Where-Object { $_ }) -join '-')
}

function Get-Caidas {
    <# Rachas de $Minimo o mas comprobaciones fallidas seguidas -> caidas con Inicio y Fin. Muestras ordenadas: Momento, Ok. #>
    param([object[]]$Muestras, [int]$Minimo)
    $caidas = New-Object System.Collections.Generic.List[object]
    $racha = New-Object System.Collections.Generic.List[object]
    foreach ($m in @($Muestras) + @([pscustomobject]@{ Momento = [datetime]::MaxValue; Ok = $true })) {
        if (-not $m.Ok) { $racha.Add($m); continue }
        if ($racha.Count -ge $Minimo) { $caidas.Add([pscustomobject]@{ Inicio = $racha[0].Momento; Fin = $racha[$racha.Count - 1].Momento; Fallos = $racha.Count }) }
        $racha.Clear()
    }
    return , $caidas.ToArray()
}

function Get-EstadoImpresora {
    <# Estado legible de una comprobacion: ping, puerto y MAC. #>
    param([bool]$Ping, [bool]$Puerto, [string]$MacEsperada, [string]$MacVista)
    if ($MacEsperada -and $MacVista -and $MacEsperada -ne $MacVista) { return "MAC DISTINTA" }
    if ($Puerto) { return "OK" }
    if ($Ping) { return "SIN PUERTO" }
    return "SIN RESPUESTA"
}

# ============================================================ lista de impresoras
$inicio = Get-Date
if (-not $CarpetaResultados) { $CarpetaResultados = Join-Path $PSScriptRoot "Resultados" }
$lista = New-Object System.Collections.Generic.List[object]
try {
    if ($Impresoras) {
        foreach ($f in (Import-CsvFlexible -Ruta $Impresoras -Obligatorias @("Nombre", "IP"))) {
            $ip = Get-Valor $f "IP"
            if (-not $ip) { continue }
            if (@($lista | Where-Object { $_.IP -eq $ip }).Count) { Write-Aviso "La IP $ip está repetida en el CSV: se usa solo la primera fila."; continue }
            $pto = Get-Valor $f "Puerto"
            $lista.Add([pscustomobject]@{ Nombre = Get-Valor $f "Nombre"; IP = $ip; Mac = ConvertTo-MacNormal (Get-Valor $f "MAC"); Puerto = $(if ($pto -match '^\d+$') { [int]$pto } else { 9100 }); Cola = "" })
        }
    }
    if ($DesdeWindows) {
        $puertos = @{}
        foreach ($p in (Get-PrinterPort -ErrorAction SilentlyContinue)) { if ($p.PrinterHostAddress) { $puertos[$p.Name] = $p } }
        foreach ($i in (Get-Printer -ErrorAction SilentlyContinue)) {
            $p = $puertos[$i.PortName]
            if (-not $p) { continue }
            $num = if ($p.PortNumber) { [int]$p.PortNumber } else { 9100 }
            if (-not @($lista | Where-Object { $_.IP -eq $p.PrinterHostAddress }).Count) { $lista.Add([pscustomobject]@{ Nombre = $i.Name; IP = $p.PrinterHostAddress; Mac = ""; Puerto = $num; Cola = $i.Name }) }
            else { foreach ($x in ($lista | Where-Object { $_.IP -eq $p.PrinterHostAddress -and -not $_.Cola })) { $x.Cola = $i.Name } }
        }
    }
    if (-not $lista.Count) { throw $(if ($DesdeWindows -and -not $Impresoras) { "Este equipo no tiene impresoras instaladas con puerto TCP/IP (las compartidas desde un servidor no cuentan): indica -Impresoras con un CSV." } else { "No hay impresoras que vigilar: indica -Impresoras con un CSV (Nombre, IP) o usa -DesdeWindows." }) }
} catch { Write-Fallo $_.Exception.Message; exit 1 }
# Cola instalada en este equipo con la misma IP (para revisar su estado y sus trabajos)
try {
    $colas = @{}
    foreach ($p in (Get-PrinterPort -ErrorAction SilentlyContinue)) { if ($p.PrinterHostAddress) { $colas[$p.Name] = $p.PrinterHostAddress } }
    foreach ($i in (Get-Printer -ErrorAction SilentlyContinue)) { foreach ($x in ($lista | Where-Object { -not $_.Cola -and $_.IP -eq $colas[$i.PortName] })) { $x.Cola = $i.Name } }
} catch { }

function Invoke-Vuelta {
    <# Una comprobacion de todas las impresoras a la vez. #>
    param([datetime]$Momento)
    $tareas = foreach ($i in $lista) {
        $png = New-Object System.Net.NetworkInformation.Ping
        $tcp = New-Object System.Net.Sockets.TcpClient
        [pscustomobject]@{ I = $i; Png = $png; P = $png.SendPingAsync($i.IP, 1500); Tcp = $tcp; T = $tcp.ConnectAsync($i.IP, $i.Puerto) }
    }
    try { [void][System.Threading.Tasks.Task]::WaitAll([System.Threading.Tasks.Task[]]@($tareas | ForEach-Object { $_.P; $_.T }), 3000) } catch { }
    $vecinos = @{}
    try { foreach ($n in (Get-NetNeighbor -AddressFamily IPv4 -ErrorAction SilentlyContinue)) { $mn = ConvertTo-MacNormal $n.LinkLayerAddress; if ($mn -and $mn -ne "00-00-00-00-00-00" -and [string]$n.State -notin "Unreachable", "Incomplete") { $vecinos[$n.IPAddress] = $mn } } } catch { }
    foreach ($t in $tareas) {
        $ping = $t.P.Status -eq "RanToCompletion" -and $t.P.Result.Status -eq "Success"
        $puerto = $t.T.Status -eq "RanToCompletion" -and $t.Tcp.Connected
        $mac = [string]$vecinos[$t.I.IP]
        $cola = ""; $trabajos = ""
        if ($t.I.Cola) {
            try {
                $pr = Get-Printer -Name $t.I.Cola -ErrorAction Stop
                $cola = [string]$pr.PrinterStatus
                $js = @(Get-PrintJob -PrinterName $t.I.Cola -ErrorAction SilentlyContinue)
                $malos = @($js | Where-Object { [string]$_.JobStatus -match 'Error|Blocked|Offline|PaperOut|UserIntervention' -or ($_.SubmittedTime -and ($Momento - $_.SubmittedTime).TotalMinutes -ge $MinutosTrabajoAtascado) })
                $trabajos = "$($js.Count)" + $(if ($malos.Count) { " ($($malos.Count) con error o atascados)" } else { "" })
            } catch { $cola = "No se pudo leer" }
        }
        [pscustomobject]@{ Momento = $Momento; Nombre = $t.I.Nombre; IP = $t.I.IP; Ping = $ping; Puerto = $puerto; Mac = $mac
            Estado = Get-EstadoImpresora $ping $puerto $t.I.Mac $mac; MacEsperada = $t.I.Mac; Cola = $cola; Trabajos = $trabajos; Ms = $(if ($ping) { [int]$t.P.Result.RoundtripTime } else { "" }) }
        $t.Png.Dispose(); $t.Tcp.Dispose()
    }
}

$spooler = (Get-Service -Name Spooler -ErrorAction SilentlyContinue).Status

# ============================================================ foto rapida
if ($UnaVez) {
    Write-Paso "Impresoras ($(Get-Date -Format 'dd/MM/yyyy HH:mm:ss')) · servicio Cola de impresión: $spooler"
    foreach ($r in (Invoke-Vuelta (Get-Date))) {
        $txt = "{0,-28} {1,-15} {2,-14} ping {3,-3} puerto {4,-3} MAC {5}{6}{7}" -f $r.Nombre, $r.IP, $r.Estado, $(if ($r.Ping) { "sí" } else { "no" }), $(if ($r.Puerto) { "sí" } else { "no" }),
            $(if ($r.Mac) { $r.Mac } else { "-" }), $(if ($r.MacEsperada -and $r.Mac -ne $r.MacEsperada) { " (esperada $($r.MacEsperada))" } else { "" }), $(if ($r.Cola) { " · cola: $($r.Cola), trabajos: $($r.Trabajos)" } else { "" })
        if ($r.Estado -eq "OK") { Write-Ok $txt } else { Write-Fallo $txt }
    }
    exit 0
}

# ============================================================ vigilancia
New-Item -ItemType Directory -Force -Path $CarpetaResultados | Out-Null
$base = "Impresoras_{0}_{1:yyyyMMdd_HHmmss}" -f $env:COMPUTERNAME, $inicio
$rutaCsv = Join-Path $CarpetaResultados "$base.csv"
$muestras = New-Object System.Collections.Generic.List[object]
$anterior = @{}
$inv = [Globalization.CultureInfo]::InvariantCulture
$sw = New-Object System.IO.StreamWriter($rutaCsv, $false, (New-Object System.Text.UTF8Encoding($true)))
$sw.WriteLine("Fecha;Hora;Impresora;IP;Estado;Ping;Puerto;MAC;Cola;Trabajos;ms")
Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " VIGILANCIA DE IMPRESORAS - $($lista.Count) impresoras, $Minutos min, cada $IntervaloSegundos s" -ForegroundColor Cyan
Write-Host " Ctrl+C termina antes y genera el informe. Solo se muestran los cambios de estado." -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan
try {
    $fin = $inicio.AddMinutes($Minutos)
    while ((Get-Date) -lt $fin -and -not ($env:MASYS_PARAR -and (Test-Path -LiteralPath $env:MASYS_PARAR))) {
        $t0 = Get-Date
        foreach ($r in (Invoke-Vuelta $t0)) {
            $muestras.Add($r)
            $sw.WriteLine(("{0};{1};{2};{3};{4};{5};{6};{7};{8};{9};{10}" -f $t0.ToString("dd/MM/yyyy", $inv), $t0.ToString("HH:mm:ss", $inv), ($r.Nombre -replace ';', ','), $r.IP, $r.Estado,
                    $(if ($r.Ping) { "SI" } else { "NO" }), $(if ($r.Puerto) { "SI" } else { "NO" }), $r.Mac, $r.Cola, $r.Trabajos, $r.Ms))
            if ($anterior[$r.IP] -ne $r.Estado) {
                $txt = "{0} {1} ({2}): {3}" -f $t0.ToString("HH:mm:ss"), $r.Nombre, $r.IP, $r.Estado
                if ($r.Estado -eq "OK") { Write-Ok $txt } else { Write-Fallo $txt }
                $anterior[$r.IP] = $r.Estado
            }
        }
        $sw.Flush()
        $espera = $IntervaloSegundos * 1000 - ((Get-Date) - $t0).TotalMilliseconds
        if ($espera -gt 0) { Start-Sleep -Milliseconds ([int]$espera) }
    }
}
finally {
    $sw.Dispose()
    $finReal = Get-Date
    $H = { param($t) [System.Net.WebUtility]::HtmlEncode([string]$t) }
    $durT = [math]::Max(1, ($finReal - $inicio).TotalSeconds)
    $filasSvg = New-Object System.Collections.Generic.List[string]
    $resumen = New-Object System.Collections.Generic.List[object]
    $caidasTodas = New-Object System.Collections.Generic.List[object]
    $n = 0
    foreach ($i in $lista) {
        $ms = @($muestras | Where-Object { $_.IP -eq $i.IP } | Sort-Object Momento)
        if (-not $ms.Count) { continue }
        $caidas = Get-Caidas -Muestras @($ms | ForEach-Object { [pscustomobject]@{ Momento = $_.Momento; Ok = ($_.Estado -eq "OK") } }) -Minimo $FallosParaCaida
        foreach ($c in $caidas) {
            $estados = @($ms | Where-Object { $_.Momento -ge $c.Inicio -and $_.Momento -le $c.Fin } | ForEach-Object { $_.Estado } | Sort-Object -Unique)
            $caidasTodas.Add([pscustomobject]@{ Nombre = $i.Nombre; IP = $i.IP; Inicio = $c.Inicio; Fin = $c.Fin; Estado = ($estados -join ", ") })
        }
        $macs = @($ms | Where-Object { $_.Mac } | ForEach-Object { $_.Mac } | Sort-Object -Unique)
        $resumen.Add([pscustomobject]@{ Nombre = $i.Nombre; IP = $i.IP; Disponible = [math]::Round(100 * @($ms | Where-Object { $_.Estado -eq "OK" }).Count / $ms.Count, 2); Caidas = $caidas.Count
                Macs = ($macs -join ", "); MacEsperada = $i.Mac; Cola = (@($ms | Where-Object { $_.Cola } | ForEach-Object { $_.Cola } | Sort-Object -Unique) -join ", ")
                Trabajos = (@($ms | Where-Object { $_.Trabajos -match 'error' } | Select-Object -Last 1 | ForEach-Object { $_.Trabajos }) -join "") })
        $y = 10 + $n * 26
        $filasSvg.Add(('<text x="0" y="{0}" font-size="11" fill="#555">{1}</text><rect x="0" y="{2}" width="1000" height="10" fill="#bbf7d0"/>' -f ($y + 9), (& $H "$($i.Nombre) ($($i.IP))"), ($y + 12)))
        foreach ($c in $caidas) {
            $x = 1000 * ($c.Inicio - $inicio).TotalSeconds / $durT; $w = [math]::Max(2, 1000 * (($c.Fin - $c.Inicio).TotalSeconds + $IntervaloSegundos) / $durT)
            $filasSvg.Add(([string]::Format($inv, '<rect x="{0:0.##}" y="{1}" width="{2:0.##}" height="10" fill="#dc2626"/>', $x, $y + 12, $w)))
        }
        $n++
    }
    $alto = 20 + $n * 26
    $marcas = (0..4 | ForEach-Object { $t = $inicio.AddSeconds($durT * $_ / 4); ('<text x="{0}" y="{1}" font-size="11" fill="#555" text-anchor="{2}">{3}</text>' -f (250 * $_), ($alto + 4), $(if ($_ -eq 0) { "start" } elseif ($_ -eq 4) { "end" } else { "middle" }), $t.ToString("dd/MM HH:mm")) }) -join ""
    $conclusiones = New-Object System.Collections.Generic.List[string]
    foreach ($r in $resumen) {
        if ($r.MacEsperada -and $r.Macs -and $r.Macs -ne $r.MacEsperada) { $conclusiones.Add("$($r.Nombre): en su IP responde otra MAC ($($r.Macs), se esperaba $($r.MacEsperada)): conflicto de IP o aparato cambiado. Reservar la IP en el DHCP.") }
        elseif (($r.Macs -split ', ').Count -gt 1) { $conclusiones.Add("$($r.Nombre): su IP ha respondido con varias MAC ($($r.Macs)): conflicto de IP. Reservar la IP en el DHCP.") }
        if ($r.Caidas) { $conclusiones.Add("$($r.Nombre): $($r.Caidas) caídas, disponible el $($r.Disponible) % del tiempo.") }
        if ($r.Trabajos) { $conclusiones.Add("$($r.Nombre): trabajos con error o atascados en la cola de este equipo ($($r.Trabajos)).") }
    }
    if (@($caidasTodas | Where-Object { $_.Estado -match "SIN PUERTO" }).Count) { $conclusiones.Add("Alguna impresora respondía a ping pero no al puerto de impresión: la impresora está encendida pero su servicio de impresión no atiende (reiniciarla o revisar su firmware).") }
    if ([string]$spooler -ne "Running") { $conclusiones.Add("El servicio Cola de impresión de este equipo no está en marcha ($spooler).") }
    if (-not $conclusiones.Count) { $conclusiones.Add("Todas las impresoras han respondido todo el periodo ($([math]::Round($durT / 60)) min).") }
    $filasRes = ($resumen | ForEach-Object { "<tr><td>$(& $H $_.Nombre)</td><td>$($_.IP)</td><td$(if ($_.Disponible -lt 100) { ' class="mal"' })>$($_.Disponible)</td><td>$($_.Caidas)</td><td>$(& $H $_.Macs)</td><td>$(& $H $_.MacEsperada)</td><td>$(& $H $_.Cola)</td></tr>" }) -join "`n"
    $filasCa = ($caidasTodas | Sort-Object Inicio | ForEach-Object { "<tr><td>$(& $H $_.Nombre)</td><td>$($_.IP)</td><td>$($_.Inicio.ToString('dd/MM/yyyy'))</td><td>$($_.Inicio.ToString('HH:mm:ss'))</td><td>$([math]::Round(($_.Fin - $_.Inicio).TotalSeconds + $IntervaloSegundos))</td><td>$(& $H $_.Estado)</td></tr>" }) -join "`n"
    $html = @"
<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Impresoras - $(& $H $env:COMPUTERNAME)</title>
<style>
:root{--primary:#1a3a5c;--bg:#f7f9fc;--card:#fff;--text:#1a1a2e;--border:#dde3ea;--bad:#dc2626}
*{box-sizing:border-box}body{margin:0;font-family:"Segoe UI",Arial,sans-serif;background:var(--bg);color:var(--text);font-size:14px}
header{background:linear-gradient(135deg,#0f2440,#1a3a5c 60%,#2c5f8a);color:#fff;padding:22px 28px}header h1{margin:0;font-weight:300;font-size:26px}header p{margin:6px 0 0;opacity:.85}
main{padding:20px 28px;max-width:1300px}h2{color:var(--primary);border-bottom:2px solid var(--primary);padding-bottom:4px;font-weight:500;font-size:18px;margin-top:26px}
.card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:14px}svg{width:100%;height:auto}
.tabla{overflow:auto;background:var(--card);border:1px solid var(--border);border-radius:8px}table{border-collapse:collapse;width:100%}
th{background:var(--primary);color:#fff;text-align:left;padding:8px;font-weight:600;white-space:nowrap}td{padding:6px 8px;border-top:1px solid var(--border)}tr:nth-child(even) td{background:#f8fafc}
.mal{color:var(--bad);font-weight:600}ul li{margin:6px 0}@media (max-width:700px){main,header{padding:14px}}
</style></head><body>
<header><h1>Vigilancia de impresoras</h1><p>$(& $H $env:COMPUTERNAME) · $($inicio.ToString('dd/MM/yyyy HH:mm:ss')) - $($finReal.ToString('HH:mm:ss')) · cada $IntervaloSegundos s · $($lista.Count) impresoras</p></header>
<main>
<h2>Conclusiones</h2><div class="card"><ul>$(($conclusiones | ForEach-Object { "<li>$(& $H $_)</li>" }) -join '')</ul></div>
<h2>Línea de tiempo (rojo = caída)</h2><div class="card"><svg viewBox="0 0 1000 $($alto + 8)" preserveAspectRatio="none">$($filasSvg -join '')$marcas</svg></div>
<h2>Impresoras</h2><div class="tabla"><table><tr><th>Impresora</th><th>IP</th><th>Disponible %</th><th>Caídas</th><th>MAC vista</th><th>MAC esperada</th><th>Cola en este equipo</th></tr>$filasRes</table></div>
<h2>Caídas</h2>$(if ($caidasTodas.Count) { "<div class=""tabla""><table><tr><th>Impresora</th><th>IP</th><th>Fecha</th><th>Hora</th><th>Segundos</th><th>Estado</th></tr>$filasCa</table></div>" } else { "<div class=""card"">Ninguna.</div>" })
<p style="color:#555;font-size:12px">Estados: OK (responde al puerto de impresión), SIN PUERTO (responde a ping pero no al puerto), SIN RESPUESTA, MAC DISTINTA (en su IP responde otro aparato). Una caída son $FallosParaCaida o más comprobaciones seguidas que no son OK.</p>
</main></body></html>
"@
    $rutaHtml = Join-Path $CarpetaResultados "$base.html"
    [System.IO.File]::WriteAllText($rutaHtml, $html, (New-Object System.Text.UTF8Encoding($true)))
    Write-Paso "Resumen"
    foreach ($c in $conclusiones) { Write-Host "  * $c" -ForegroundColor Yellow }
    Write-Ok "Informe: $rutaHtml"
    Write-Ok "Datos:   $rutaCsv"
}
