# MASys 5.6: copia propia de _TEMP\SCRIPTS_2 (5d223d8). Único cambio: el bucle para también si existe el archivo
# de $env:MASYS_PARAR (botón «Parar» de MASys), para que el informe se genere igual que con Ctrl+C.
#Requires -Version 5.1
<#
.SYNOPSIS
    Monitor de rendimiento de un equipo Windows: captura CPU, RAM, disco, red y los procesos que mas consumen durante
    dias, y genera un informe HTML con los momentos de saturacion y los procesos culpables.

.DESCRIPTION
    Modos (uno por ejecucion):
      -Instalar     Copia el script a %ProgramData%\MonitorRendimiento y crea una tarea programada (SYSTEM, al arrancar
                    el equipo, se reinicia si se cae) que captura sin que se vea nada. Necesita administrador.
      -Capturar     Captura en primer plano (con -DuracionMinutos) o como lo lanza la tarea. Contadores por CIM (no
                    dependen del idioma de Windows). El % de CPU de cada proceso es el real entre dos muestras (no el
                    tiempo acumulado desde que arranco). Un archivo por dia; borra los de mas de -DiasConservar dias.
      -Analizar     Lee los datos y genera un informe HTML autonomo: grafica, estadisticas, episodios de saturacion con
                    los procesos que mas consumian en cada uno, ranking de procesos y recomendaciones.
      -Estado       Tarea, ultima muestra y espacio ocupado.
      -Desinstalar  Quita la tarea (los datos se conservan salvo -BorrarDatos).

.EXAMPLE
    .\Monitor-Rendimiento.ps1 -Instalar
    .\Monitor-Rendimiento.ps1 -Analizar
    .\Monitor-Rendimiento.ps1 -Capturar -DuracionMinutos 30 -IntervaloSegundos 5
#>

[CmdletBinding(DefaultParameterSetName = "Estado")]
param(
    [Parameter(ParameterSetName = "Instalar", Mandatory = $true)][switch]$Instalar,
    [Parameter(ParameterSetName = "Capturar", Mandatory = $true)][switch]$Capturar,
    [Parameter(ParameterSetName = "Analizar", Mandatory = $true)][switch]$Analizar,
    [Parameter(ParameterSetName = "Estado")][switch]$Estado,
    [Parameter(ParameterSetName = "Desinstalar", Mandatory = $true)][switch]$Desinstalar,
    [Parameter(ParameterSetName = "Instalar")][Parameter(ParameterSetName = "Capturar")][ValidateRange(2, 3600)][int]$IntervaloSegundos = 15,
    [Parameter(ParameterSetName = "Instalar")][Parameter(ParameterSetName = "Capturar")][ValidateRange(1, 365)][int]$DiasConservar = 14,
    [Parameter(ParameterSetName = "Instalar")][Parameter(ParameterSetName = "Capturar")][ValidateRange(3, 50)][int]$ProcesosPorMuestra = 10,
    [Parameter(ParameterSetName = "Capturar")][ValidateRange(0, 100000)][int]$DuracionMinutos = 0,
    [Parameter(ParameterSetName = "Analizar")][string]$Desde,
    [Parameter(ParameterSetName = "Analizar")][string]$Hasta,
    [Parameter(ParameterSetName = "Analizar")][ValidateRange(50, 100)][int]$UmbralCPU = 90,
    [Parameter(ParameterSetName = "Analizar")][ValidateRange(50, 100)][int]$UmbralRAM = 90,
    [Parameter(ParameterSetName = "Analizar")][ValidateRange(1, 100)][int]$UmbralColaDisco = 5,
    [Parameter(ParameterSetName = "Analizar")][string]$CarpetaResultados,
    [Parameter(ParameterSetName = "Desinstalar")][switch]$BorrarDatos,
    [Parameter(ParameterSetName = "Instalar")][Parameter(ParameterSetName = "Desinstalar")][switch]$Simular,
    [Parameter(ParameterSetName = "Instalar")][Parameter(ParameterSetName = "Desinstalar")][switch]$SinConfirmar,
    [string]$CarpetaDatos
)

$ErrorActionPreference = "Stop"
$NombreTarea = "MonitorRendimiento"
$CarpetaPrograma = Join-Path $env:ProgramData "MonitorRendimiento"
if (-not $CarpetaDatos) { $CarpetaDatos = Join-Path $CarpetaPrograma "Datos" }

# ============================================================ utilidades comunes
function Write-Paso  ($t) { Write-Host "`n== $t" -ForegroundColor Cyan }
function Write-Ok    ($t) { Write-Host "  [OK]    $t" -ForegroundColor Green }
function Write-Aviso ($t) { Write-Host "  [AVISO] $t" -ForegroundColor Yellow }
function Write-Fallo ($t) { Write-Host "  [ERROR] $t" -ForegroundColor Red }
function Write-Sim   ($t) { Write-Host "  [SIM]   $t" -ForegroundColor Magenta }

function Test-Administrador {
    return ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

# ============================================================ funciones de calculo (probadas sin equipo)
function Get-PorcentajeCPUProceso {
    <# % de CPU de un proceso entre dos muestras, sobre el total de nucleos logicos. #>
    param([double]$MsAntes, [double]$MsAhora, [double]$MsTranscurridos, [int]$Nucleos)
    if ($MsTranscurridos -le 0 -or $Nucleos -le 0 -or $MsAhora -lt $MsAntes) { return 0 }
    return [math]::Min([math]::Round(($MsAhora - $MsAntes) / ($MsTranscurridos * $Nucleos) * 100), 100)
}

function Get-EpisodiosSaturacion {
    <#
        Agrupa las muestras saturadas seguidas en episodios. Muestras: objetos con Momento (datetime), CPU, RAM, Cola.
        Dos muestras saturadas separadas como mucho por $Hueco segundos pertenecen al mismo episodio.
    #>
    param([object[]]$Muestras, [int]$UmbralCPU, [int]$UmbralRAM, [int]$UmbralCola, [int]$Hueco)
    $episodios = New-Object System.Collections.Generic.List[object]
    $actual = $null
    foreach ($m in $Muestras) {
        $motivos = @()
        if ($m.CPU -ge $UmbralCPU) { $motivos += "CPU" }
        if ($m.RAM -ge $UmbralRAM) { $motivos += "RAM" }
        if ($m.Cola -ge $UmbralCola) { $motivos += "Disco" }
        if (-not $motivos) { continue }
        if ($actual -and ($m.Momento - $actual.Fin).TotalSeconds -le $Hueco) {
            $actual.Fin = $m.Momento; $actual.Muestras++
            foreach ($x in $motivos) { [void]$actual.Motivos.Add($x) }
            if ($m.CPU -gt $actual.CPUMax) { $actual.CPUMax = $m.CPU }
            if ($m.RAM -gt $actual.RAMMax) { $actual.RAMMax = $m.RAM }
            if ($m.Cola -gt $actual.ColaMax) { $actual.ColaMax = $m.Cola }
        } else {
            $actual = [pscustomobject]@{ Inicio = $m.Momento; Fin = $m.Momento; Muestras = 1; CPUMax = $m.CPU; RAMMax = $m.RAM; ColaMax = $m.Cola
                Motivos = (New-Object 'System.Collections.Generic.HashSet[string]') }
            foreach ($x in $motivos) { [void]$actual.Motivos.Add($x) }
            $episodios.Add($actual)
        }
    }
    return , $episodios.ToArray()
}

function ConvertTo-SvgSerie {
    <# Puntos de una polilinea SVG (x de 0 a $Ancho, y de $Alto a 0 para valores 0-100), promediando en $Ancho grupos. #>
    param([double[]]$Valores, [int]$Ancho, [int]$Alto)
    if (-not $Valores -or $Valores.Count -eq 0) { return "" }
    $grupos = [math]::Min($Ancho, $Valores.Count)
    $puntos = New-Object System.Collections.Generic.List[string]
    $inv = [Globalization.CultureInfo]::InvariantCulture
    for ($g = 0; $g -lt $grupos; $g++) {
        $a = [int][math]::Floor($g * $Valores.Count / $grupos); $b = [int][math]::Floor(($g + 1) * $Valores.Count / $grupos) - 1
        if ($b -lt $a) { $b = $a }
        $s = 0.0; for ($i = $a; $i -le $b; $i++) { $s += $Valores[$i] }
        $v = [math]::Max(0, [math]::Min(100, $s / ($b - $a + 1)))
        $x = if ($grupos -eq 1) { 0 } else { $g * $Ancho / ($grupos - 1) }
        $puntos.Add(([string]::Format($inv, "{0:0.#},{1:0.#}", $x, $Alto - $v * $Alto / 100)))
    }
    return ($puntos -join " ")
}

function Get-Recomendaciones {
    <# Recomendaciones a partir de las estadisticas del periodo. #>
    param([double]$CPUMedia, [double]$CPUP95, [double]$RAMMedia, [double]$RAMP95, [double]$ColaP95, [int]$Episodios, [double]$RAMTotalGB, [string]$TipoDisco, [object[]]$Culpables)
    $r = New-Object System.Collections.Generic.List[string]
    if ($RAMP95 -ge 90) { $r.Add("La RAM está casi llena buena parte del tiempo (percentil 95: $RAMP95 %): ampliar memoria$(if ($RAMTotalGB) { " (tiene $RAMTotalGB GB)" }) o reducir programas abiertos.") }
    elseif ($RAMMedia -ge 75) { $r.Add("Uso medio de RAM alto ($RAMMedia %): vigilar; con más programas abiertos se quedará corto.") }
    if ($CPUP95 -ge 90) { $r.Add("El procesador va al límite con frecuencia (percentil 95: $CPUP95 %): revisar los procesos culpables; si son de trabajo normal, el equipo se ha quedado corto.") }
    if ($ColaP95 -ge 2) { $r.Add("El disco tiene cola de espera frecuente (percentil 95: $ColaP95)$(if ($TipoDisco -eq 'HDD') { ': es un disco mecánico, cambiar a SSD es lo que más se notará' } else { ': revisar procesos con mucha escritura (antivirus, copias, indexación)' }).") }
    elseif ($TipoDisco -eq "HDD") { $r.Add("El disco del sistema es mecánico (HDD): cambiarlo a SSD mejora mucho el arranque y la respuesta.") }
    foreach ($c in @($Culpables | Select-Object -First 3)) { $r.Add("«$($c.Proceso)» aparece en $($c.Episodios) episodios de saturación con un $($c.CPUSaturacion) % de CPU medio en ellos: comprobar si es necesario, si está actualizado o si tiene un problema.") }
    if (-not $r.Count -and $Episodios -eq 0) { $r.Add("No se han visto saturaciones en el periodo: el rendimiento del equipo es suficiente para el uso medido. Si el usuario nota lentitud, mirar red, perfil de usuario o programas concretos.") }
    return , $r.ToArray()
}

function Get-Percentil {
    param([double[]]$Valores, [double]$P)
    if (-not $Valores -or $Valores.Count -eq 0) { return 0 }
    $o = [double[]]($Valores | Sort-Object)
    $i = [math]::Min($o.Count - 1, [math]::Max(0, [math]::Ceiling($P / 100 * $o.Count) - 1))
    return [math]::Round($o[$i], 1)
}

function ConvertTo-Html {
    param([string]$Texto)
    return [System.Net.WebUtility]::HtmlEncode([string]$Texto)
}

# ============================================================ captura
function Get-DatosEquipo {
    $cs = Get-CimInstance Win32_ComputerSystem; $os = Get-CimInstance Win32_OperatingSystem
    $cpu = @(Get-CimInstance Win32_Processor)[0]
    $tipo = "Desconocido"
    try {
        $part = Get-Partition -DriveLetter $env:SystemDrive.TrimEnd(':') | Select-Object -First 1
        $pd = Get-PhysicalDisk | Where-Object { $_.DeviceId -eq [string]$part.DiskNumber } | Select-Object -First 1
        if ($pd) { $tipo = if ([string]$pd.MediaType -eq "SSD" -or [string]$pd.BusType -eq "NVMe") { "SSD" } elseif ([string]$pd.MediaType -eq "HDD") { "HDD" } else { "Desconocido" } }
    } catch { }
    return [ordered]@{
        Equipo = $env:COMPUTERNAME; Modelo = "$($cs.Manufacturer) $($cs.Model)"; Sistema = "$($os.Caption) (build $($os.BuildNumber))"
        Procesador = ([string]$cpu.Name).Trim(); Nucleos = [int]$cs.NumberOfLogicalProcessors; RAM_GB = [math]::Round([double]$cs.TotalPhysicalMemory / 1GB)
        Disco = $tipo; Intervalo = $IntervaloSegundos; Desde = (Get-Date).ToString("dd/MM/yyyy HH:mm")
    }
}

function Add-Linea {
    <# Añade una linea a un CSV diario, creandolo con cabecera y BOM (Excel) si no existe. #>
    param([string]$Ruta, [string]$Cabecera, [string]$Linea)
    if (-not (Test-Path -LiteralPath $Ruta)) { [System.IO.File]::WriteAllText($Ruta, $Cabecera + "`r`n", (New-Object System.Text.UTF8Encoding($true))) }
    [System.IO.File]::AppendAllText($Ruta, $Linea, (New-Object System.Text.UTF8Encoding($false)))
}

function Invoke-Captura {
    New-Item -ItemType Directory -Force -Path $CarpetaDatos | Out-Null
    $equipo = Get-DatosEquipo
    $equipo | ConvertTo-Json | Out-File -LiteralPath (Join-Path $CarpetaDatos "Equipo.json") -Encoding UTF8
    $nucleos = [math]::Max(1, $equipo.Nucleos)
    $cabM = "Fecha;Hora;CPU;RAM;RAM_Libre_MB;ColaDisco;Disco_Lect_KBs;Disco_Escr_KBs;Red_Env_KBs;Red_Rec_KBs;Procesos"
    $cabP = "Fecha;Hora;Proceso;PID;CPU;RAM_MB"
    $fin = if ($DuracionMinutos -gt 0) { (Get-Date).AddMinutes($DuracionMinutos) } else { [datetime]::MaxValue }
    $anterior = @{}; $tAnterior = $null; $ultimaLimpieza = [datetime]::MinValue; $inv = [Globalization.CultureInfo]::InvariantCulture
    Write-Host "Capturando cada $IntervaloSegundos s en $CarpetaDatos$(if ($DuracionMinutos) { " durante $DuracionMinutos min" }). Ctrl+C para parar."
    while ((Get-Date) -lt $fin -and -not ($env:MASYS_PARAR -and (Test-Path -LiteralPath $env:MASYS_PARAR))) {
        $t0 = Get-Date
        try {
            $os = Get-CimInstance Win32_OperatingSystem
            $cpu = [int](@(Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -Filter "Name='_Total'")[0].PercentProcessorTime)
            $libreMB = [math]::Round([double]$os.FreePhysicalMemory / 1KB)
            $ram = [int][math]::Round(100 - 100 * [double]$os.FreePhysicalMemory / [double]$os.TotalVisibleMemorySize)
            $d = @(Get-CimInstance Win32_PerfFormattedData_PerfDisk_PhysicalDisk -Filter "Name='_Total'")[0]
            $red = @(Get-CimInstance Win32_PerfFormattedData_Tcpip_NetworkInterface -ErrorAction SilentlyContinue)
            $env_ = [math]::Round(([double]($red | Measure-Object BytesSentPersec -Sum).Sum) / 1KB)
            $rec = [math]::Round(([double]($red | Measure-Object BytesReceivedPersec -Sum).Sum) / 1KB)

            # % de CPU real de cada proceso: diferencia de tiempo de CPU entre esta muestra y la anterior
            $procs = @(Get-Process | Where-Object { $_.Id -gt 4 })
            $actual = @{}
            $filas = foreach ($p in $procs) {
                $ms = $null
                try { $ms = $p.TotalProcessorTime.TotalMilliseconds } catch { }
                if ($null -ne $ms) { $actual["$($p.Id)|$($p.ProcessName)"] = $ms }
                $pc = 0
                if ($tAnterior -and $null -ne $ms -and $anterior.ContainsKey("$($p.Id)|$($p.ProcessName)")) {
                    $pc = Get-PorcentajeCPUProceso $anterior["$($p.Id)|$($p.ProcessName)"] $ms ($t0 - $tAnterior).TotalMilliseconds $nucleos
                }
                [pscustomobject]@{ Nombre = $p.ProcessName; Id = $p.Id; CPU = $pc; RAM = [math]::Round($p.WorkingSet64 / 1MB) }
            }
            $anterior = $actual; $primera = -not $tAnterior; $tAnterior = $t0
            $fecha = $t0.ToString("dd/MM/yyyy", $inv); $hora = $t0.ToString("HH:mm:ss", $inv); $dia = $t0.ToString("yyyyMMdd", $inv)
            if (-not $primera) {
                $lineaM = "{0};{1};{2};{3};{4};{5};{6};{7};{8};{9};{10}`r`n" -f $fecha, $hora, $cpu, $ram, $libreMB, [math]::Round([double]$d.CurrentDiskQueueLength),
                    [math]::Round([double]$d.DiskReadBytesPersec / 1KB), [math]::Round([double]$d.DiskWriteBytesPersec / 1KB), $env_, $rec, $procs.Count
                Add-Linea (Join-Path $CarpetaDatos "Muestras_$dia.csv") $cabM $lineaM
                $top = @($filas | Sort-Object CPU -Descending | Select-Object -First $ProcesosPorMuestra) + @($filas | Sort-Object RAM -Descending | Select-Object -First $ProcesosPorMuestra)
                $sb = New-Object System.Text.StringBuilder
                foreach ($f in ($top | Sort-Object Id -Unique)) { [void]$sb.AppendFormat("{0};{1};{2};{3};{4};{5}`r`n", $fecha, $hora, ($f.Nombre -replace ';', ','), $f.Id, $f.CPU, $f.RAM) }
                Add-Linea (Join-Path $CarpetaDatos "Procesos_$dia.csv") $cabP $sb.ToString()
            }
            if (($t0 - $ultimaLimpieza).TotalHours -ge 6) {
                $ultimaLimpieza = $t0
                Get-ChildItem -LiteralPath $CarpetaDatos -Filter "*_*.csv" | Where-Object { $_.Name -match '^(Muestras|Procesos)_\d{8}\.csv$' -and $_.LastWriteTime -lt $t0.AddDays(-$DiasConservar) } | Remove-Item -Force -ErrorAction SilentlyContinue
            }
            if ($DuracionMinutos) { Write-Host ("`r{0}  CPU {1,3} %  RAM {2,3} %  procesos {3}   " -f $hora, $cpu, $ram, $procs.Count) -NoNewline }
        } catch {
            try { "[$((Get-Date).ToString('dd/MM/yyyy HH:mm:ss'))] $($_.Exception.Message)" | Out-File -LiteralPath (Join-Path $CarpetaDatos "errores.log") -Append -Encoding UTF8 } catch { }
        }
        $espera = $IntervaloSegundos * 1000 - ((Get-Date) - $t0).TotalMilliseconds
        if ($espera -gt 0) { Start-Sleep -Milliseconds ([int]$espera) }
    }
    if ($DuracionMinutos) { Write-Host "" ; Write-Ok "Captura terminada: $CarpetaDatos" }
}

# ============================================================ analisis
function Invoke-Analisis {
    if (-not (Test-Path -LiteralPath $CarpetaDatos)) { throw "No hay datos en $CarpetaDatos. Instala el monitor (-Instalar) o captura (-Capturar) primero, o indica -CarpetaDatos." }
    $inv = [Globalization.CultureInfo]::InvariantCulture
    $desdeF = if ($Desde) { [datetime]::ParseExact($Desde, "dd/MM/yyyy", $inv) } else { [datetime]::MinValue }
    $hastaF = if ($Hasta) { [datetime]::ParseExact($Hasta, "dd/MM/yyyy", $inv).AddDays(1) } else { [datetime]::MaxValue }
    $dias = @(Get-ChildItem -LiteralPath $CarpetaDatos -Filter "Muestras_*.csv" | Where-Object { $_.Name -match '^Muestras_(\d{8})\.csv$' } |
            Where-Object { $d = [datetime]::ParseExact($Matches[1], "yyyyMMdd", $inv); $d -ge $desdeF.Date -and $d -lt $hastaF } | Sort-Object Name)
    if (-not $dias.Count) { throw "No hay muestras en el periodo indicado." }
    $equipo = $null
    $rutaEq = Join-Path $CarpetaDatos "Equipo.json"
    if (Test-Path -LiteralPath $rutaEq) { $equipo = Get-Content -LiteralPath $rutaEq -Raw -Encoding UTF8 | ConvertFrom-Json }
    $intervalo = if ($equipo -and $equipo.Intervalo) { [int]$equipo.Intervalo } else { 15 }

    Write-Paso "Lectura de $($dias.Count) días de datos"
    $muestras = New-Object System.Collections.Generic.List[object]
    foreach ($a in $dias) {
        foreach ($l in [System.IO.File]::ReadLines($a.FullName)) {
            $c = $l.TrimStart([char]0xFEFF).Split(';')
            if ($c.Count -lt 11 -or $c[0] -eq "Fecha") { continue }
            $m = [datetime]::MinValue
            if (-not [datetime]::TryParseExact("$($c[0]) $($c[1])", "dd/MM/yyyy HH:mm:ss", $inv, [Globalization.DateTimeStyles]::None, [ref]$m)) { continue }
            if ($m -lt $desdeF -or $m -ge $hastaF) { continue }
            $muestras.Add([pscustomobject]@{ Momento = $m; Clave = "$($c[0]) $($c[1])"; CPU = [int]$c[2]; RAM = [int]$c[3]; LibreMB = [int]$c[4]; Cola = [int]$c[5]; Lect = [int]$c[6]; Escr = [int]$c[7]; Env = [int]$c[8]; Rec = [int]$c[9] })
        }
    }
    if (-not $muestras.Count) { throw "No hay muestras válidas en el periodo." }
    Write-Ok "$($muestras.Count) muestras: $($muestras[0].Momento.ToString('dd/MM/yyyy HH:mm')) - $($muestras[$muestras.Count - 1].Momento.ToString('dd/MM/yyyy HH:mm'))"

    $episodios = Get-EpisodiosSaturacion -Muestras $muestras.ToArray() -UmbralCPU $UmbralCPU -UmbralRAM $UmbralRAM -UmbralCola $UmbralColaDisco -Hueco ($intervalo * 3)
    # Momento -> episodio, para repartir los procesos
    $deEpisodio = @{}
    $j = 0
    foreach ($s in $muestras) {   # una sola pasada: muestras y episodios estan ordenados por fecha
        while ($j -lt $episodios.Count -and $s.Momento -gt $episodios[$j].Fin) { $j++ }
        if ($j -lt $episodios.Count -and $s.Momento -ge $episodios[$j].Inicio) { $deEpisodio[$s.Clave] = $j + 1 }
    }

    Write-Paso "Procesos"
    $proc = @{}; $porEpisodio = @{}
    # Varias instancias con el mismo nombre (svchost, chrome) en una muestra se suman como un solo proceso
    $sumar = {
        param($clave, $porNombre)
        foreach ($nom in $porNombre.Keys) {
            $cpu = $porNombre[$nom][0]; $ram = $porNombre[$nom][1]
            $e = $proc[$nom]
            if (-not $e) { $e = [pscustomobject]@{ Proceso = $nom; CPUSuma = 0; CPUMax = 0; RAMSuma = 0; RAMMax = 0; Veces = 0; EnSaturacion = 0; CPUSat = 0; Episodios = (New-Object 'System.Collections.Generic.HashSet[int]') }; $proc[$nom] = $e }
            $e.CPUSuma += $cpu; $e.RAMSuma += $ram; $e.Veces++
            if ($cpu -gt $e.CPUMax) { $e.CPUMax = $cpu }; if ($ram -gt $e.RAMMax) { $e.RAMMax = $ram }
            if ($deEpisodio.ContainsKey($clave)) {
                $ep = $deEpisodio[$clave]
                $e.EnSaturacion++; $e.CPUSat += $cpu; [void]$e.Episodios.Add($ep)
                if (-not $porEpisodio.ContainsKey($ep)) { $porEpisodio[$ep] = @{} }
                $porEpisodio[$ep][$nom] = [int]$porEpisodio[$ep][$nom] + $cpu
            }
        }
    }
    foreach ($a in $dias) {
        $rutaP = Join-Path $CarpetaDatos ($a.Name -replace '^Muestras', 'Procesos')
        if (-not (Test-Path -LiteralPath $rutaP)) { continue }
        $claveActual = $null; $porNombre = @{}
        foreach ($l in [System.IO.File]::ReadLines($rutaP)) {
            $c = $l.TrimStart([char]0xFEFF).Split(';')
            if ($c.Count -lt 6 -or $c[0] -eq "Fecha") { continue }
            $clave = "$($c[0]) $($c[1])"
            if ($clave -ne $claveActual) { if ($claveActual) { & $sumar $claveActual $porNombre }; $claveActual = $clave; $porNombre = @{} }
            $x = $porNombre[$c[2]]
            if (-not $x) { $x = @(0, 0); $porNombre[$c[2]] = $x }
            $x[0] += [int]$c[4]; $x[1] += [int]$c[5]
        }
        if ($claveActual) { & $sumar $claveActual $porNombre }
    }
    $total = $muestras.Count
    $ranking = @($proc.Values | ForEach-Object {
            [pscustomobject]@{ Proceso = $_.Proceso; CPUMedia = [math]::Round($_.CPUSuma / $total, 1); CPUMax = $_.CPUMax; RAMMedia = [math]::Round($_.RAMSuma / [math]::Max(1, $_.Veces)); RAMMax = $_.RAMMax
                Presencia = [math]::Round(100 * $_.Veces / $total); Episodios = $_.Episodios.Count; CPUSaturacion = if ($_.EnSaturacion) { [math]::Round($_.CPUSat / $_.EnSaturacion, 1) } else { 0 } } })
    $culpables = @($ranking | Where-Object { $_.Episodios -gt 0 -and $_.CPUSaturacion -ge 5 } | Sort-Object @{ e = { $_.Episodios * $_.CPUSaturacion } } -Descending)
    Write-Ok "$($ranking.Count) procesos distintos; $($episodios.Count) episodios de saturación"

    $cpuV = [double[]]($muestras | ForEach-Object { $_.CPU }); $ramV = [double[]]($muestras | ForEach-Object { $_.RAM }); $colaV = [double[]]($muestras | ForEach-Object { $_.Cola })
    $est = [ordered]@{
        CPUMedia = [math]::Round(($cpuV | Measure-Object -Average).Average, 1); CPUP95 = Get-Percentil $cpuV 95; CPUMax = ($cpuV | Measure-Object -Maximum).Maximum
        RAMMedia = [math]::Round(($ramV | Measure-Object -Average).Average, 1); RAMP95 = Get-Percentil $ramV 95; RAMMax = ($ramV | Measure-Object -Maximum).Maximum
        ColaP95 = Get-Percentil $colaV 95; ColaMax = ($colaV | Measure-Object -Maximum).Maximum
        LibreMin = ($muestras | Measure-Object LibreMB -Minimum).Minimum
        Saturadas = @($muestras | Where-Object { $_.CPU -ge $UmbralCPU -or $_.RAM -ge $UmbralRAM -or $_.Cola -ge $UmbralColaDisco }).Count
    }
    $recom = Get-Recomendaciones $est.CPUMedia $est.CPUP95 $est.RAMMedia $est.RAMP95 $est.ColaP95 $episodios.Count $(if ($equipo) { [double]$equipo.RAM_GB } else { 0 }) $(if ($equipo) { [string]$equipo.Disco } else { "" }) $culpables

    # ---------- HTML
    $H = { param($t) ConvertTo-Html $t }
    $svgCPU = ConvertTo-SvgSerie $cpuV 1000 200; $svgRAM = ConvertTo-SvgSerie $ramV 1000 200
    $ini = $muestras[0].Momento; $finM = $muestras[$total - 1].Momento; $durT = [math]::Max(1, ($finM - $ini).TotalSeconds)
    $bandas = ($episodios | ForEach-Object {
            $x = 1000 * ($_.Inicio - $ini).TotalSeconds / $durT; $w = [math]::Max(2, 1000 * ($_.Fin - $_.Inicio).TotalSeconds / $durT)
            [string]::Format([Globalization.CultureInfo]::InvariantCulture, '<rect x="{0:0.#}" y="0" width="{1:0.#}" height="200" fill="#fecaca" />', $x, $w) }) -join ""
    $marcas = (0..4 | ForEach-Object { $t = $ini.AddSeconds($durT * $_ / 4); [string]::Format([Globalization.CultureInfo]::InvariantCulture, '<text x="{0}" y="216" font-size="11" fill="#555" text-anchor="{1}">{2}</text>', 250 * $_, $(if ($_ -eq 0) { "start" } elseif ($_ -eq 4) { "end" } else { "middle" }), $t.ToString("dd/MM HH:mm")) }) -join ""
    $filasEp = ($episodios | ForEach-Object -Begin { $i = 0 } -Process {
            $i++
            $top = if ($porEpisodio.ContainsKey($i)) { ($porEpisodio[$i].GetEnumerator() | Sort-Object Value -Descending | Select-Object -First 3 | ForEach-Object { $_.Key }) -join ", " } else { "" }
            $dur = $_.Fin - $_.Inicio
            "<tr><td>$($_.Inicio.ToString('dd/MM/yyyy'))</td><td>$($_.Inicio.ToString('HH:mm:ss'))</td><td>$([math]::Max(1, [math]::Round(($dur.TotalSeconds + $intervalo) / 60)))</td><td>$(& $H (($_.Motivos | Sort-Object) -join ', '))</td><td>$($_.CPUMax)</td><td>$($_.RAMMax)</td><td>$($_.ColaMax)</td><td>$(& $H $top)</td></tr>" }) -join "`n"
    $filaRank = { param($r) "<tr><td>$(& $H $r.Proceso)</td><td>$($r.CPUMedia)</td><td>$($r.CPUMax)</td><td>$($r.RAMMedia)</td><td>$($r.RAMMax)</td><td>$($r.Presencia)</td><td>$($r.Episodios)</td><td>$($r.CPUSaturacion)</td></tr>" }
    $filasCPU = ($ranking | Sort-Object CPUMedia -Descending | Select-Object -First 25 | ForEach-Object { & $filaRank $_ }) -join "`n"
    $filasRAM = ($ranking | Sort-Object RAMMax -Descending | Select-Object -First 25 | ForEach-Object { & $filaRank $_ }) -join "`n"
    $filasCul = ($culpables | Select-Object -First 25 | ForEach-Object { & $filaRank $_ }) -join "`n"
    $cabRank = "<tr><th>Proceso</th><th>CPU media %</th><th>CPU máx. %</th><th>RAM media MB</th><th>RAM máx. MB</th><th>Presente %</th><th>Episodios</th><th>CPU en saturación %</th></tr>"
    $info = if ($equipo) { ($equipo.PSObject.Properties | ForEach-Object { "<dt>$(& $H $_.Name)</dt><dd>$(& $H $_.Value)</dd>" }) -join "" } else { "<dt>Equipo</dt><dd>(sin Equipo.json)</dd>" }
    $kpi = { param($v, $t) "<div class=""kpi""><b>$v</b><span>$t</span></div>" }
    $nombreEq = if ($equipo) { $equipo.Equipo } else { "equipo" }
    $html = @"
<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Rendimiento - $(& $H $nombreEq)</title>
<style>
:root{--primary:#1a3a5c;--accent:#1d4ed8;--bg:#f7f9fc;--card:#fff;--text:#1a1a2e;--muted:#555;--border:#dde3ea;--bad:#dc2626;--warn:#ca8a04}
*{box-sizing:border-box}body{margin:0;font-family:"Segoe UI",Arial,sans-serif;background:var(--bg);color:var(--text);font-size:14px}
header{background:linear-gradient(135deg,#0f2440,#1a3a5c 60%,#2c5f8a);color:#fff;padding:22px 28px}header h1{margin:0;font-weight:300;font-size:26px}header p{margin:6px 0 0;opacity:.85}
main{padding:20px 28px;max-width:1300px}h2{color:var(--primary);border-bottom:2px solid var(--primary);padding-bottom:4px;font-weight:500;font-size:18px;margin-top:28px}
.kpis{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px}.kpi{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:12px 14px}.kpi b{display:block;font-size:22px;color:var(--primary)}.kpi span{color:var(--muted);font-size:12px}
.card{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:14px}svg{width:100%;height:auto}
.leyenda span{display:inline-block;margin-right:16px;font-size:12px}.leyenda i{display:inline-block;width:14px;height:3px;vertical-align:middle;margin-right:5px}
.tabla{overflow:auto;background:var(--card);border:1px solid var(--border);border-radius:8px}table{border-collapse:collapse;width:100%}
th{background:var(--primary);color:#fff;text-align:left;padding:8px;font-weight:600;cursor:pointer;white-space:nowrap}td{padding:6px 8px;border-top:1px solid var(--border)}tr:nth-child(even) td{background:#f8fafc}
ul.rec li{margin:6px 0}.aviso{background:#fef9c3;border-left:4px solid var(--warn);padding:10px 12px;border-radius:4px}
dl.info{display:grid;grid-template-columns:160px 1fr;gap:6px 14px}dl.info dt{color:var(--muted)}dl.info dd{margin:0}
@media (max-width:700px){main,header{padding:14px}dl.info{grid-template-columns:1fr}}
</style></head><body>
<header><h1>Informe de rendimiento</h1><p>$(& $H $nombreEq) · $($ini.ToString('dd/MM/yyyy HH:mm')) - $($finM.ToString('dd/MM/yyyy HH:mm')) · $total muestras cada $intervalo s · generado $((Get-Date).ToString('dd/MM/yyyy HH:mm'))</p></header>
<main>
<div class="kpis">$(& $kpi "$($est.CPUMedia) %" "CPU media") $(& $kpi "$($est.CPUP95) %" "CPU percentil 95") $(& $kpi "$($est.RAMMedia) %" "RAM media") $(& $kpi "$($est.RAMP95) %" "RAM percentil 95") $(& $kpi "$($est.LibreMin) MB" "RAM libre mínima") $(& $kpi $est.ColaP95 "Cola de disco p95") $(& $kpi $episodios.Count "Episodios de saturación") $(& $kpi "$([math]::Round(100 * $est.Saturadas / $total, 1)) %" "Tiempo saturado")</div>
<h2>Recomendaciones</h2><div class="card"><ul class="rec">$(($recom | ForEach-Object { "<li>$(& $H $_)</li>" }) -join '')</ul></div>
<h2>CPU y RAM en el periodo</h2><div class="card"><div class="leyenda"><span><i style="background:#1d4ed8"></i>CPU %</span><span><i style="background:#16a34a"></i>RAM %</span><span><i style="background:#fecaca;height:10px"></i>Saturación</span></div>
<svg viewBox="0 0 1000 222" preserveAspectRatio="none" role="img" aria-label="CPU y RAM">$bandas<line x1="0" y1="20" x2="1000" y2="20" stroke="#fca5a5" stroke-dasharray="4 4"/><line x1="0" y1="100" x2="1000" y2="100" stroke="#e5e7eb"/><polyline fill="none" stroke="#16a34a" stroke-width="1.5" points="$svgRAM"/><polyline fill="none" stroke="#1d4ed8" stroke-width="1.5" points="$svgCPU"/>$marcas</svg></div>
<h2>Episodios de saturación (CPU ≥ $UmbralCPU %, RAM ≥ $UmbralRAM % o cola de disco ≥ $UmbralColaDisco)</h2>
$(if ($episodios.Count) { "<div class=""tabla""><table class=""ord""><thead><tr><th>Fecha</th><th>Hora</th><th>Minutos</th><th>Motivo</th><th>CPU máx. %</th><th>RAM máx. %</th><th>Cola máx.</th><th>Procesos con más CPU</th></tr></thead><tbody>$filasEp</tbody></table></div>" } else { "<div class=""card"">Ninguno en el periodo.</div>" })
<h2>Procesos culpables (presentes en las saturaciones)</h2>
$(if ($culpables.Count) { "<div class=""tabla""><table class=""ord""><thead>$cabRank</thead><tbody>$filasCul</tbody></table></div>" } else { "<div class=""card"">Ninguno.</div>" })
<h2>Procesos con más CPU (media del periodo)</h2><div class="tabla"><table class="ord"><thead>$cabRank</thead><tbody>$filasCPU</tbody></table></div>
<h2>Procesos con más RAM</h2><div class="tabla"><table class="ord"><thead>$cabRank</thead><tbody>$filasRAM</tbody></table></div>
<h2>Equipo</h2><div class="card"><dl class="info">$info</dl></div>
<p style="color:#555;font-size:12px">CPU de un proceso: % del total de núcleos entre dos muestras. Presente %: en qué parte de las muestras estaba entre los que más consumían. Pulsa una cabecera para ordenar.</p>
</main>
<script>
document.querySelectorAll('table.ord').forEach(function(t){ t.tHead.addEventListener('click', function(e){ var th=e.target.closest('th'); if(!th) return; var i=Array.prototype.indexOf.call(th.parentNode.children, th), b=t.tBodies[0], filas=Array.prototype.slice.call(b.rows), asc=th.dataset.asc!=='1'; th.dataset.asc=asc?'1':'0';
 filas.sort(function(x,y){ var a=x.cells[i].textContent, c=y.cells[i].textContent, na=parseFloat(a), nc=parseFloat(c); var r=(!isNaN(na)&&!isNaN(nc))?na-nc:a.localeCompare(c,'es'); return asc?r:-r; }); filas.forEach(function(f){ b.appendChild(f); }); }); });
</script></body></html>
"@
    if (-not $CarpetaResultados) { $CarpetaResultados = Join-Path $PSScriptRoot "Resultados" }
    New-Item -ItemType Directory -Force -Path $CarpetaResultados | Out-Null
    $ruta = Join-Path $CarpetaResultados ("Rendimiento_{0}_{1:yyyyMMdd_HHmmss}.html" -f ($nombreEq -replace '[^A-Za-z0-9_-]', '_'), (Get-Date))
    [System.IO.File]::WriteAllText($ruta, $html, (New-Object System.Text.UTF8Encoding($true)))
    Write-Paso "Resumen"
    Write-Host ("  CPU media {0} % (p95 {1} %) · RAM media {2} % (p95 {3} %) · {4} episodios de saturación" -f $est.CPUMedia, $est.CPUP95, $est.RAMMedia, $est.RAMP95, $episodios.Count)
    foreach ($r in $recom) { Write-Host "  * $r" -ForegroundColor Yellow }
    Write-Ok "Informe: $ruta"
}

# ============================================================ instalar, estado, desinstalar
function Confirmar ($Texto) { return $SinConfirmar -or ((Read-Host "$Texto (S/N)") -match '^(s|si|sí)$') }

function Invoke-Instalar {
    if (-not (Test-Administrador) -and -not $Simular) { throw "Para instalar hay que ejecutarlo como administrador." }
    $destino = Join-Path $CarpetaPrograma "Monitor-Rendimiento.ps1"
    $argumentos = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$destino`" -Capturar -IntervaloSegundos $IntervaloSegundos -DiasConservar $DiasConservar -ProcesosPorMuestra $ProcesosPorMuestra -CarpetaDatos `"$CarpetaDatos`""
    Write-Paso "Instalación"
    Write-Host "  Script:  $destino"
    Write-Host "  Datos:   $CarpetaDatos (se borran los de más de $DiasConservar días)"
    Write-Host "  Tarea:   $NombreTarea (SYSTEM, al arrancar, cada $IntervaloSegundos s)"
    $mb = [math]::Round(86400 / $IntervaloSegundos * (90 + 2 * $ProcesosPorMuestra * 40) / 1MB * $DiasConservar)
    Write-Host "  Espacio aproximado: $mb MB como máximo"
    if ($Simular) { Write-Sim "No se instala nada."; return }
    if (-not (Confirmar "¿Instalar el monitor?")) { Write-Aviso "Cancelado."; return }
    New-Item -ItemType Directory -Force -Path $CarpetaPrograma, $CarpetaDatos | Out-Null
    Copy-Item -LiteralPath $PSCommandPath -Destination $destino -Force
    $accion = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argumentos
    $disparador = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $ajustes = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $NombreTarea -Action $accion -Trigger $disparador -Principal $principal -Settings $ajustes -Description "Captura de rendimiento (Monitor-Rendimiento.ps1). Datos en $CarpetaDatos" -Force | Out-Null
    Start-ScheduledTask -TaskName $NombreTarea
    Start-Sleep -Seconds 3
    Write-Ok "Instalado y en marcha. Deja pasar 1-3 días de uso normal y lanza: .\Monitor-Rendimiento.ps1 -Analizar"
}

function Invoke-Estado {
    Write-Paso "Estado del monitor"
    $t = Get-ScheduledTask -TaskName $NombreTarea -ErrorAction SilentlyContinue
    if ($t) {
        $i = Get-ScheduledTaskInfo -TaskName $NombreTarea
        Write-Ok "Tarea $NombreTarea`: $($t.State) · última ejecución $($i.LastRunTime) · resultado $($i.LastTaskResult)"
    } else { Write-Aviso "La tarea $NombreTarea no está instalada." }
    if (Test-Path -LiteralPath $CarpetaDatos) {
        $a = @(Get-ChildItem -LiteralPath $CarpetaDatos -Filter "*.csv")
        $ult = $a | Where-Object { $_.Name -like "Muestras_*" } | Sort-Object Name | Select-Object -Last 1
        $mb = [math]::Round((($a | Measure-Object Length -Sum).Sum) / 1MB, 1)
        Write-Ok "Datos: $($a.Count) archivos, $mb MB en $CarpetaDatos"
        if ($ult) {
            $linea = [System.IO.File]::ReadLines($ult.FullName) | Select-Object -Last 1
            Write-Ok "Última muestra: $($linea -replace '^([^;]+);([^;]+);.*$', '$1 $2')"
            if ($ult.LastWriteTime -lt (Get-Date).AddMinutes(-10)) { Write-Aviso "Hace más de 10 minutos que no se captura nada: revisa la tarea y $CarpetaDatos\errores.log" }
        }
    } else { Write-Aviso "No hay datos en $CarpetaDatos." }
}

function Invoke-Desinstalar {
    if (-not (Test-Administrador) -and -not $Simular) { throw "Para desinstalar hay que ejecutarlo como administrador." }
    Write-Paso "Desinstalación"
    Write-Host "  Se quita la tarea $NombreTarea y el script de $CarpetaPrograma$(if ($BorrarDatos) { ' y TODOS los datos' } else { '; los datos se conservan' })."
    if ($Simular) { Write-Sim "No se quita nada."; return }
    if (-not (Confirmar "¿Desinstalar?")) { Write-Aviso "Cancelado."; return }
    if (Get-ScheduledTask -TaskName $NombreTarea -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $NombreTarea -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $NombreTarea -Confirm:$false
    }
    Remove-Item -LiteralPath (Join-Path $CarpetaPrograma "Monitor-Rendimiento.ps1") -Force -ErrorAction SilentlyContinue
    if ($BorrarDatos) { Remove-Item -LiteralPath $CarpetaDatos -Recurse -Force -ErrorAction SilentlyContinue }
    Write-Ok "Desinstalado$(if (-not $BorrarDatos) { ". Datos en $CarpetaDatos" })."
}

# ============================================================ inicio
try {
    switch ($PSCmdlet.ParameterSetName) {
        "Instalar" { Invoke-Instalar }
        "Capturar" { Invoke-Captura }
        "Analizar" { Invoke-Analisis }
        "Desinstalar" { Invoke-Desinstalar }
        default { Invoke-Estado }
    }
} catch {
    Write-Fallo $_.Exception.Message
    exit 1
}
