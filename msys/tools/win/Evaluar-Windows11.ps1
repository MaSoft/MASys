#Requires -Version 5.1
<#
.SYNOPSIS
    Evalua si un equipo cumple los requisitos de Windows 11 y como funcionara segun el uso (ofimatica, moderado,
    diseño, intensivo). Solo lee: no instala ni cambia nada.

.DESCRIPTION
    Requisitos oficiales de Microsoft que se comprueban:
      - Procesador compatible de 64 bits, 2 nucleos o mas y 1 GHz o mas (modelo estimado por su nombre; los
        Xeon, Pentium, Celeron y Athlon salen como COMPROBAR: ver la lista oficial).
      - 4 GB de RAM.
      - Disco de 64 GB o mas.
      - Firmware UEFI con Secure Boot (que lo admita; activarlo es un ajuste de la BIOS).
      - TPM 2.0.
      - Grafica compatible con DirectX 12 con controlador WDDM 2.0 o posterior.
    Ademas: puntuacion por nivel de uso, cuellos de botella, tiempo estimado de actualizacion y recomendaciones.
    Pensado para ejecutarse en local o en muchos equipos (LogMeIn One2Many, Intune, GPO):
      - escribe el informe en la salida y al final una linea "W11DATOS:..." con todos los datos codificados;
      - guarda un CSV por equipo (Win11_EQUIPO_fecha.csv) en -Carpeta;
      - -UnirCarpeta junta esos CSV en uno (el ultimo de cada equipo);
      - -ImportarSalida lee las exportaciones de LogMeIn (texto, CSV o HTML) y saca un CSV con todos los equipos.
    Como administrador o SYSTEM se comprueban TPM y Secure Boot; sin permisos salen como COMPROBAR.

.EXAMPLE
    .\Evaluar-Windows11.ps1
    .\Evaluar-Windows11.ps1 -Carpeta \\servidor\IT$\Win11
    .\Evaluar-Windows11.ps1 -ImportarSalida .\informe_logmein.csv
#>

[CmdletBinding()]
param(
    [string]$Carpeta,
    [switch]$SinCsv,
    [ValidateRange(0, 500)][int]$MinimoLibreGB = 30,
    [string]$UnirCarpeta,
    [string[]]$ImportarSalida
)

$ErrorActionPreference = "Stop"
$CodificacionCsv = if ($PSVersionTable.PSVersion.Major -ge 6) { "utf8BOM" } else { "UTF8" }   # CSV con BOM en 5.1 y en 7: Excel abre bien las tildes

# ============================================================ funciones
function Get-CompatibilidadCPU {
    <# SI / NO / COMPROBAR por el nombre del procesador (estimacion: la referencia es la lista oficial de Microsoft). #>
    param([string]$Nombre, [int]$Arquitectura = 9)
    $n = ($Nombre -replace '\s+', ' ').Trim()
    $r = { param($e, $d, $a = 0) [pscustomobject]@{ Estado = $e; Detalle = $d; Anio = $a } }
    # Año aproximado de lanzamiento por generacion: sirve para estimar la antiguedad del equipo
    $anioIntel = @{ 1 = 2009; 2 = 2011; 3 = 2012; 4 = 2013; 5 = 2015; 6 = 2015; 7 = 2016; 8 = 2017; 9 = 2018; 10 = 2019; 11 = 2020; 12 = 2021; 13 = 2022; 14 = 2023 }
    $anioRyzen = @{ 1 = 2017; 2 = 2018; 3 = 2019; 4 = 2020; 5 = 2020; 6 = 2022; 7 = 2022; 8 = 2024; 9 = 2024 }
    if ($Arquitectura -eq 12 -or $n -match 'Snapdragon|Qualcomm') { return & $r "SI" "ARM (Snapdragon)" }
    if ($n -match 'Intel') {
        if ($n -match 'Core(\(TM\))? Ultra') { return & $r "SI" "Intel Core Ultra" 2023 }
        if ($n -match 'Core(\(TM\))? [3579] \d{3}') { return & $r "SI" "Intel Core serie 1 o posterior" 2023 }
        if ($n -match '\bi3-N\d{3}') { return & $r "SI" "Intel Core i3 serie N" 2023 }
        $m = [regex]::Match($n, '\b(?:i[3579]|m[357])-(\d{3,5})')
        if ($m.Success) {
            $d = $m.Groups[1].Value
            # 5 cifras (10210U) y 4 cifras que empiezan por 1 (1065G7, 1135G7) = 10.ª generacion o posterior
            $gen = if ($d.Length -eq 5 -or ($d.Length -eq 4 -and $d.StartsWith("1"))) { [int]$d.Substring(0, 2) } elseif ($d.Length -eq 4) { [int]$d.Substring(0, 1) } else { 1 }
            $a = [int]$anioIntel[$gen]
            if ($gen -ge 8) { return & $r "SI" "Intel Core $gen.ª generación" $a }
            if ($gen -eq 7) { return & $r "NO" "Intel Core 7.ª generación (solo algún modelo concreto está admitido)" $a }
            return & $r "NO" "Intel Core $gen.ª generación (se necesita la 8.ª o posterior)" $a
        }
        if ($n -match 'Xeon|Pentium|Celeron|Atom') { return & $r "COMPROBAR" "Intel $($Matches[0]): comprobar el modelo en la lista oficial" }
        return & $r "COMPROBAR" "Intel: comprobar el modelo en la lista oficial"
    }
    if ($n -match 'AMD') {
        if ($n -match 'Ryzen.*\bAI\b|Ryzen Z\d') { return & $r "SI" "AMD Ryzen AI o Z" 2024 }
        $m = [regex]::Match($n, 'Ryzen.*?\b(\d)\d{3}[A-Z0-9]*\b')
        if ($m.Success) {
            $g = [int]$m.Groups[1].Value
            if ($g -ge 2) { return & $r "SI" "AMD Ryzen serie ${g}000" ([int]$anioRyzen[$g]) }
            return & $r "NO" "AMD Ryzen serie 1000 (se necesita la 2000 o posterior)" 2017
        }
        $m = [regex]::Match($n, 'EPYC\s+\d{3}(\d)')
        if ($m.Success) {
            if ([int]$m.Groups[1].Value -ge 2) { return & $r "SI" "AMD EPYC 2.ª generación o posterior" }
            return & $r "NO" "AMD EPYC 1.ª generación"
        }
        if ($n -match 'Athlon|Ryzen') { return & $r "COMPROBAR" "AMD: comprobar el modelo en la lista oficial" }
        if ($n -match '\b(A\d{1,2}|E\d|FX|Phenom|Opteron|Sempron|Turion)\b') { return & $r "NO" "AMD anterior a Ryzen 2000" }
        return & $r "COMPROBAR" "AMD: comprobar el modelo en la lista oficial"
    }
    return & $r "COMPROBAR" "Comprobar el modelo en la lista oficial"
}

function Get-VersionWDDM {
    <# La primera cifra de la version del controlador de pantalla es la version WDDM (20 = 2.0, 31 = 3.1; 7-10 = 1.0-1.3). #>
    param([string]$VersionControlador)
    $m = [regex]::Match([string]$VersionControlador, '^(\d+)\.')
    if (-not $m.Success) { return "" }
    $x = [int]$m.Groups[1].Value
    if ($x -ge 20 -and $x -le 99) { return "{0}.{1}" -f [math]::Floor($x / 10), ($x % 10) }
    if ($x -ge 7 -and $x -le 10) { return "1.{0}" -f ($x - 7) }
    return ""
}

function Get-PuntuacionUso {
    <# Puntuacion 0-100 de un nivel de uso: RAM 30, nucleos 25, espacio libre 25, antiguedad 20, mas bonus y penalizacion. #>
    param([double]$RAM, [int]$Nucleos, [double]$Antiguedad, [double]$Libre, [double]$RAMNecesaria, [int]$NucleosNecesarios,
        [double]$LibreNecesario, [double]$AntiguedadMaxima, [int]$Bonus = 0, [int]$Penalizacion = 0)
    $p = 0
    if ($RAM -ge $RAMNecesaria * 2) { $p += 30 } elseif ($RAM -ge $RAMNecesaria * 1.5) { $p += 25 } elseif ($RAM -ge $RAMNecesaria) { $p += 20 } elseif ($RAM -ge $RAMNecesaria * 0.75) { $p += 10 }
    if ($Nucleos -ge $NucleosNecesarios * 2) { $p += 25 } elseif ($Nucleos -ge $NucleosNecesarios * 1.5) { $p += 22 } elseif ($Nucleos -ge $NucleosNecesarios) { $p += 18 } elseif ($Nucleos -ge $NucleosNecesarios * 0.75) { $p += 12 } else { $p += 5 }
    if ($Libre -ge $LibreNecesario * 2) { $p += 25 } elseif ($Libre -ge $LibreNecesario * 1.5) { $p += 22 } elseif ($Libre -ge $LibreNecesario) { $p += 18 } elseif ($Libre -ge $LibreNecesario * 0.8) { $p += 12 } else { $p += 5 }
    if ($Antiguedad -le $AntiguedadMaxima * 0.5) { $p += 20 } elseif ($Antiguedad -le $AntiguedadMaxima * 0.75) { $p += 16 } elseif ($Antiguedad -le $AntiguedadMaxima) { $p += 12 } elseif ($Antiguedad -le $AntiguedadMaxima * 1.5) { $p += 6 }
    $p = [math]::Max($p + $Bonus - $Penalizacion, 0)
    return [int][math]::Min($p, 100)
}

function Get-TextoViabilidad {
    param([int]$Puntuacion)
    if ($Puntuacion -ge 70) { return "RECOMENDADO" }
    if ($Puntuacion -ge 50) { return "ACEPTABLE" }
    return "NO RECOMENDADO"
}

function ConvertTo-LineaDatos {
    <# Todos los datos en una linea base64: sobrevive a cualquier exportacion de LogMeIn (texto, CSV, HTML). #>
    param($Datos)
    $json = $Datos | ConvertTo-Json -Compress -Depth 3
    return "W11DATOS:" + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
}

function ConvertFrom-SalidaW11 {
    <# Saca de un texto (exportacion de LogMeIn u otra) los datos de cada linea W11DATOS:. #>
    param([string]$Texto)
    foreach ($m in [regex]::Matches([string]$Texto, 'W11DATOS:([A-Za-z0-9+/=]{20,})')) {
        try { [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($m.Groups[1].Value)) | ConvertFrom-Json }
        catch { }
    }
}

function Select-UltimoPorEquipo {
    <# Se queda con la evaluacion mas reciente de cada equipo (Fecha DD/MM/AAAA y Hora HH:mm). #>
    param([object[]]$Filas)
    $clave = { param($f) $d = [datetime]::MinValue; [void][datetime]::TryParseExact("$($f.Fecha) $($f.Hora)", "dd/MM/yyyy HH:mm", $null, [Globalization.DateTimeStyles]::None, [ref]$d); $d }
    return @($Filas | Where-Object { $_.Equipo } | Group-Object { ([string]$_.Equipo).ToUpper() } | ForEach-Object {
            $_.Group | Sort-Object { & $clave $_ } -Descending | Select-Object -First 1 } | Sort-Object Equipo)
}

function Read-TextoArchivo {
    <# Lee un archivo de texto en UTF-8 o, si no lo es, en ANSI. #>
    param([string]$Ruta)
    $bytes = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $Ruta).Path)
    try { return (New-Object System.Text.UTF8Encoding($false, $true)).GetString($bytes).TrimStart([char]0xFEFF) }
    catch { return [System.Text.Encoding]::GetEncoding(1252).GetString($bytes) }
}

function Get-CarpetaSalida {
    param([string]$Carpeta)
    if (-not $Carpeta) { $Carpeta = if ($PSScriptRoot) { Join-Path $PSScriptRoot "Resultados" } else { (Get-Location).Path } }
    if (-not (Test-Path -LiteralPath $Carpeta)) { New-Item -ItemType Directory -Force -Path $Carpeta | Out-Null }
    return $Carpeta
}

# ============================================================ modos de recogida
if ($UnirCarpeta -or $ImportarSalida) {
    try {
        if ($UnirCarpeta) {
            if (-not (Test-Path -LiteralPath $UnirCarpeta)) { throw "No existe la carpeta: $UnirCarpeta" }
            $archivos = @(Get-ChildItem -LiteralPath $UnirCarpeta -Filter "Win11_*.csv" | Where-Object { $_.Name -notmatch '^Win11_(TODOS|IMPORTADO)' })
            $filas = @(foreach ($a in $archivos) { Import-Csv -LiteralPath $a.FullName -Delimiter ";" -Encoding UTF8 })
            $destino = Join-Path $UnirCarpeta "Win11_TODOS.csv"
        } else {
            # Archivos o carpetas; con powershell -File la lista llega como un texto separado por comas
            $entradas = @(foreach ($e in $ImportarSalida) { if (Test-Path -LiteralPath $e) { $e } else { $e -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ } } })
            $archivos = @(foreach ($e in $entradas) {
                    if (-not (Test-Path -LiteralPath $e)) { throw "No se encuentra: $e" }
                    if (Test-Path -LiteralPath $e -PathType Container) { Get-ChildItem -LiteralPath $e -File | Where-Object { $_.Extension -match '^\.(csv|txt|log|html?)$' -and $_.Name -notlike "Win11_*" } | ForEach-Object { $_.FullName } }
                    else { $e }
                })
            $filas = @(foreach ($a in $archivos) { ConvertFrom-SalidaW11 (Read-TextoArchivo $a) })
            $destino = Join-Path (Get-CarpetaSalida $Carpeta) ("Win11_IMPORTADO_{0:yyyyMMdd_HHmmss}.csv" -f (Get-Date))
        }
        $ultimos = @(Select-UltimoPorEquipo $filas)   # @(): con un solo equipo, .Count de PSCustomObject es nulo en 5.1
        if (-not $ultimos.Count) { Write-Output "No se ha encontrado ninguna evaluacion en $($archivos.Count) archivos."; exit 1 }
        $ultimos | Export-Csv -LiteralPath $destino -Delimiter ";" -NoTypeInformation -Encoding $CodificacionCsv
        Write-Output "$($ultimos.Count) equipos ($($filas.Count) evaluaciones en $($archivos.Count) archivos; la mas reciente de cada equipo) -> $destino"
        foreach ($e in @("SI", "NO", "COMPROBAR")) { Write-Output ("  Cumple requisitos {0}: {1}" -f $e, @($ultimos | Where-Object { $_.CumpleRequisitos -eq $e }).Count) }
        Write-Output ("  Ya con Windows 11: {0}" -f @($ultimos | Where-Object { $_.Veredicto -like "YA TIENE*" }).Count)
        exit 0
    } catch { Write-Output "ERROR: $($_.Exception.Message)"; exit 2 }
}

# ============================================================ recogida de datos del equipo
$equipo = $env:COMPUTERNAME
$ahora = Get-Date
$esAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
$avisos = New-Object System.Collections.Generic.List[string]

$os = Get-CimInstance -ClassName Win32_OperatingSystem
if ($os.ProductType -ne 1) { Write-Output "$equipo es un servidor ($($os.Caption)): esta evaluacion no aplica."; exit 0 }
$cs = Get-CimInstance -ClassName Win32_ComputerSystem
$cpus = @(Get-CimInstance -ClassName Win32_Processor)
$cpu = $cpus[0]
$nucleos = [int](($cpus | Measure-Object -Property NumberOfCores -Sum).Sum)
$hilos = [int](($cpus | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum)
$bios = Get-CimInstance -ClassName Win32_BIOS

$modulos = @(Get-CimInstance -ClassName Win32_PhysicalMemory -ErrorAction SilentlyContinue)
$ramBytes = [double](($modulos | Measure-Object -Property Capacity -Sum).Sum)
if (-not $ramBytes) { $ramBytes = [double]$cs.TotalPhysicalMemory }
$ramGB = [math]::Round($ramBytes / 1GB)
$tipoRAM = "Desconocido"
if ($modulos.Count) {
    $t = [int]$modulos[0].SMBIOSMemoryType
    if (-not $t) { $t = [int]$modulos[0].MemoryType }
    $tipoRAM = switch ($t) { 20 { "DDR" } 21 { "DDR2" } 24 { "DDR3" } 26 { "DDR4" } 29 { "LPDDR3" } 30 { "LPDDR4" } 34 { "DDR5" } 35 { "LPDDR5" } default { "Desconocido" } }
}

$cv = Get-ItemProperty -Path "HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion" -ErrorAction SilentlyContinue
$build = [int]$os.BuildNumber
$versionSO = if ($cv.DisplayVersion) { $cv.DisplayVersion } elseif ($cv.ReleaseId) { $cv.ReleaseId } else { "" }
$buildCompleta = if ($null -ne $cv.UBR) { "$build.$($cv.UBR)" } else { "$build" }
$esWin11 = $build -ge 22000
$nombreSO = if ($esWin11) { $os.Caption -replace 'Windows 10', 'Windows 11' } else { $os.Caption }   # Win32_OperatingSystem a veces dice "10" en el 11

$fechaBios = $bios.ReleaseDate
$cpuInfo = Get-CompatibilidadCPU -Nombre $cpu.Name -Arquitectura ([int]$cpu.Architecture)
# Antiguedad aproximada: año del procesador si se conoce; si no, lo mas antiguo entre la BIOS y la instalacion de Windows
# (la BIOS se actualiza y Windows se reinstala, asi que las dos fechas solo dan un minimo)
if ($cpuInfo.Anio) { $antiguedad = [math]::Max($ahora.Year - $cpuInfo.Anio, 0); $origenAntiguedad = "procesador de $($cpuInfo.Anio)" }
else {
    $fechas = @($fechaBios, $os.InstallDate | Where-Object { $_ })
    $masAntigua = ($fechas | Measure-Object -Minimum).Minimum
    $antiguedad = if ($masAntigua) { [math]::Round(($ahora - $masAntigua).TotalDays / 365, 1) } else { 0 }
    $origenAntiguedad = "como mínimo, por la fecha de la BIOS o de la instalación"
}

# Disco del sistema
$letra = $env:SystemDrive
$vol = Get-CimInstance -ClassName Win32_LogicalDisk -Filter "DeviceID='$letra'"
$libreGB = [math]::Round([double]$vol.FreeSpace / 1GB)
$discoBytes = [double]$vol.Size
$tipoDisco = "Desconocido"
try {
    $part = Get-Partition -DriveLetter $letra.TrimEnd(':') -ErrorAction Stop | Select-Object -First 1
    $disco = Get-Disk -Number $part.DiskNumber -ErrorAction Stop
    if ([double]$disco.Size -gt $discoBytes) { $discoBytes = [double]$disco.Size }
    $pd = Get-PhysicalDisk -ErrorAction Stop | Where-Object { $_.DeviceId -eq [string]$part.DiskNumber } | Select-Object -First 1
    if ($pd) {
        if ([string]$pd.MediaType -eq "SSD" -or [string]$pd.BusType -eq "NVMe") { $tipoDisco = "SSD" }
        elseif ([string]$pd.MediaType -eq "HDD") { $tipoDisco = "HDD" }
    }
    if ($tipoDisco -eq "Desconocido" -and $disco.FriendlyName -match 'SSD|NVMe') { $tipoDisco = "SSD" }
} catch { $avisos.Add("No se pudo saber el tipo de disco: $($_.Exception.Message)") }
$discoGB = [math]::Round($discoBytes / 1GB)

# Firmware, Secure Boot y TPM
$pe = 0
try {
    # GetFirmwareType (Windows 8 o posterior): 1 = BIOS heredada, 2 = UEFI
    if (-not ("W11Firmware.Api" -as [type])) { Add-Type -Namespace W11Firmware -Name Api -MemberDefinition '[DllImport("kernel32.dll")] public static extern bool GetFirmwareType(ref uint tipo);' }
    $tipoFw = [uint32]0
    if ([W11Firmware.Api]::GetFirmwareType([ref]$tipoFw)) { $pe = [int]$tipoFw }
} catch { }
if ($pe -notin 1, 2) { $pe = (Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control" -Name PEFirmwareType -ErrorAction SilentlyContinue).PEFirmwareType }
$uefi = if ($pe -eq 2) { "SI" } elseif ($pe -eq 1) { "NO" } else { "COMPROBAR" }
$secureBoot = "Desconocido"; $sbCapaz = $uefi
if ($uefi -eq "SI") {
    try { $secureBoot = if (Confirm-SecureBootUEFI) { "Activado" } else { "Desactivado" } }
    catch [System.PlatformNotSupportedException] { $secureBoot = "No admitido"; $sbCapaz = "NO" }
    catch {
        $v = (Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\SecureBoot\State" -Name UEFISecureBootEnabled -ErrorAction SilentlyContinue).UEFISecureBootEnabled
        $secureBoot = if ($v -eq 1) { "Activado" } elseif ($v -eq 0) { "Desactivado" } else { "Desconocido" }
    }
} elseif ($uefi -eq "NO") { $secureBoot = "No (BIOS heredada)" }

$tpm = "COMPROBAR (sin permisos de administrador)"; $reqTPM = "COMPROBAR"
if ($esAdmin) {
    try {
        $t = Get-CimInstance -Namespace "root/cimv2/Security/MicrosoftTpm" -ClassName Win32_Tpm -ErrorAction Stop
        if (-not $t) { $tpm = "No tiene"; $reqTPM = "NO" }
        else {
            $spec = ([string]$t.SpecVersion -split ',')[0].Trim()
            $activo = $t.IsEnabled_InitialValue -and $t.IsActivated_InitialValue
            $tpm = "$spec" + $(if (-not $activo) { " (desactivado en la BIOS)" } else { "" })
            $reqTPM = if ($spec -like "2.*" -and $activo) { "SI" } else { "NO" }
        }
    } catch { $tpm = "No tiene o no se puede leer"; $reqTPM = "NO"; $avisos.Add("TPM: $($_.Exception.Message)") }
}

# Grafica: el mejor controlador WDDM de los adaptadores reales
$graficas = @(Get-CimInstance -ClassName Win32_VideoController | Where-Object { $_.Name -notmatch 'Remote|Mirror|Citrix|Hyper-V|DisplayLink|Virtual|Parsec|Meta|Idd Device' })
$nombresGrafica = ($graficas | ForEach-Object { $_.Name }) -join ", "
$wddm = ""
foreach ($g in $graficas) {
    if ($g.Name -match 'Basic Display|Básico') { continue }
    $w = Get-VersionWDDM $g.DriverVersion
    if ($w -and (-not $wddm -or [version]$w -gt [version]$wddm)) { $wddm = $w }
}
$reqGrafica = if (-not $wddm) { "COMPROBAR" } elseif ([version]$wddm -ge [version]"2.0") { "SI" } else { "NO" }
$dedicada = @($graficas | Where-Object { [double]$_.AdapterRAM -gt 2GB -and $_.Name -notmatch 'Intel.*(UHD|HD|Iris)|Radeon.*(Vega|Graphics)' }).Count -gt 0

# ============================================================ requisitos
$reqCPU = $cpuInfo.Estado
$detCPU = $cpuInfo.Detalle
if ($nucleos -lt 2 -or [int]$cpu.MaxClockSpeed -lt 1000 -or ([int]$cpu.DataWidth -ne 64 -and [int]$cpu.Architecture -ne 12)) {
    $reqCPU = "NO"; $detCPU += " (necesita 64 bits, 2 núcleos y 1 GHz)"
}
$reqRAM = if ($ramBytes -ge 3.8GB) { "SI" } else { "NO" }
$reqDisco = if ($discoBytes -ge 60GB) { "SI" } else { "NO" }   # 64 GB del fabricante = 59,6 GB de Windows
$reqFirmware = $sbCapaz

$requisitos = [ordered]@{
    "Procesador" = $reqCPU; "RAM 4 GB" = $reqRAM; "Disco 64 GB" = $reqDisco
    "UEFI y Secure Boot" = $reqFirmware; "TPM 2.0" = $reqTPM; "Gráfica WDDM 2.0" = $reqGrafica
}
$noCumple = New-Object System.Collections.Generic.List[string]
if ($reqCPU -ne "SI") { $noCumple.Add("Procesador: $($cpu.Name.Trim()) - $detCPU") }
if ($reqRAM -eq "NO") { $noCumple.Add("RAM: $ramGB GB (mínimo 4 GB)") }
if ($reqDisco -eq "NO") { $noCumple.Add("Disco: $discoGB GB (mínimo 64 GB)") }
if ($reqFirmware -eq "NO") { $noCumple.Add("Firmware: $(if ($uefi -eq 'NO') { 'BIOS heredada (se necesita UEFI)' } else { 'Secure Boot no admitido' })") }
elseif ($reqFirmware -eq "COMPROBAR") { $noCumple.Add("Firmware: no se pudo saber si es UEFI") }
if ($reqTPM -ne "SI") { $noCumple.Add("TPM: $tpm") }
if ($reqGrafica -ne "SI") { $noCumple.Add("Gráfica: $(if ($wddm) { "WDDM $wddm (se necesita 2.0)" } else { 'sin controlador reconocido' })") }
$estados = @($requisitos.Values)
$cumple = if ($estados -contains "NO") { "NO" } elseif ($estados -contains "COMPROBAR") { "COMPROBAR" } else { "SI" }

# ============================================================ evaluacion por uso
$bonus = (@{ "SSD" = 10 }[$tipoDisco] -as [int]) + (@{ "DDR5" = 8; "LPDDR5" = 8; "DDR4" = 5; "LPDDR4" = 5; "DDR2" = -5; "DDR" = -10 }[$tipoRAM] -as [int])
$esHDD = $tipoDisco -eq "HDD"
$usos = [ordered]@{
    "Ofimática"  = Get-PuntuacionUso $ramGB $nucleos $antiguedad $libreGB 6 2 100 7 $bonus $(if ($esHDD) { 15 } else { 0 })
    "Moderado"   = Get-PuntuacionUso $ramGB $nucleos $antiguedad $libreGB 8 4 150 6 $bonus $(if ($esHDD) { 25 } else { 0 })
    "Diseño"     = [math]::Max((Get-PuntuacionUso $ramGB $nucleos $antiguedad $libreGB 16 6 200 4 $bonus $(if ($esHDD) { 30 } else { 0 })) - $(if ($dedicada) { 0 } else { 25 }), 0)
    "Intensivo"  = Get-PuntuacionUso $ramGB $nucleos $antiguedad $libreGB 16 6 250 5 $bonus $(if ($esHDD) { 40 } else { 0 })
}
$cuellos = New-Object System.Collections.Generic.List[string]
if ($ramGB -lt 8) { $cuellos.Add("RAM escasa para uso actual ($ramGB GB)") }
if ($nucleos -lt 4) { $cuellos.Add("Procesador con pocos núcleos ($nucleos)") }
if ($esHDD) { $cuellos.Add("Disco duro mecánico (HDD): limita mucho el rendimiento") }
if ($tipoRAM -in "DDR3", "DDR2", "DDR") { $cuellos.Add("RAM $tipoRAM antigua") }
if ($antiguedad -gt 5) { $cuellos.Add("Equipo antiguo (unos $antiguedad años: $origenAntiguedad)") }
if ($libreGB -lt $MinimoLibreGB) { $cuellos.Add("Poco espacio libre en ${letra} $libreGB GB (se pide $MinimoLibreGB GB para actualizar)") }

$minutos = 45 + $(if ($esHDD) { 90 } else { 0 }) + $(if ($ramGB -lt 8) { 30 } else { 0 }) + $(if ($antiguedad -gt 4) { 30 } else { 0 }) + $(if ($nucleos -lt 4) { 45 } else { 0 })
$mejor = ($usos.Values | Measure-Object -Maximum).Maximum
$veredicto = if ($esWin11) { "YA TIENE WINDOWS 11" }
elseif ($cumple -eq "NO") { "NO CUMPLE LOS REQUISITOS DE WINDOWS 11" }
elseif ($cumple -eq "COMPROBAR") { "REVISAR: falta confirmar algún requisito" }
elseif ($mejor -ge 70) { "ACTUALIZACIÓN RECOMENDADA" }
elseif ($mejor -ge 50) { "ACTUALIZACIÓN VIABLE CON RESERVAS" }
else { "CUMPLE PERO IRÁ LENTO: mejorar el hardware antes" }

$recomendaciones = New-Object System.Collections.Generic.List[string]
if ($uefi -eq "NO") { $recomendaciones.Add("Pasar a UEFI si la placa lo admite: convertir el disco con mbr2gpt /convert /allowFullOS y cambiar la BIOS a UEFI") }
if ($tpm -like "*desactivado en la BIOS*") { $recomendaciones.Add("Activar el TPM en la BIOS (Intel PTT / AMD fTPM)") }
if ($esHDD) { $recomendaciones.Add("Cambiar el disco a SSD (lo que más se nota)") }
if ($ramGB -lt 16) { $recomendaciones.Add("Ampliar la RAM a 16 GB") }
if ($tipoRAM -eq "DDR3") { $recomendaciones.Add("Plataforma DDR3: valorar renovar el equipo") }
if ($nucleos -lt 4) { $recomendaciones.Add("Procesador de 4 núcleos o más") }
if ($libreGB -lt $MinimoLibreGB) { $recomendaciones.Add("Liberar espacio en $letra antes de actualizar") }
if (-not $esWin11 -and $build -lt 22000) { $avisos.Add("Windows 10 dejó de recibir soporte el 14/10/2025") }
if (-not $esAdmin) { $avisos.Add("Sin permisos de administrador: TPM y Secure Boot pueden no ser exactos") }
if ($esWin11 -and $cumple -eq "NO") { $avisos.Add("Windows 11 instalado en un equipo que no cumple los requisitos: Microsoft no garantiza que reciba actualizaciones") }

# ============================================================ informe
$datos = [ordered]@{
    Equipo = $equipo; NumeroSerie = [string]$bios.SerialNumber; Fabricante = [string]$cs.Manufacturer; Modelo = [string]$cs.Model
    Fecha = $ahora.ToString("dd/MM/yyyy"); Hora = $ahora.ToString("HH:mm")
    SistemaOperativo = $nombreSO; Version = $versionSO; Build = $buildCompleta
    Procesador = ([string]$cpu.Name).Trim(); Nucleos = $nucleos; Hilos = $hilos; MHz = [int]$cpu.MaxClockSpeed
    RAM_GB = $ramGB; TipoRAM = $tipoRAM; Disco = $tipoDisco; Disco_GB = $discoGB; Libre_GB = $libreGB
    Grafica = $nombresGrafica; WDDM = $wddm; UEFI = $uefi; SecureBoot = $secureBoot; TPM = $tpm
    FechaBIOS = if ($fechaBios) { $fechaBios.ToString("dd/MM/yyyy") } else { "" }; Antiguedad_Anios = $antiguedad
    Req_Procesador = $reqCPU; Req_RAM = $reqRAM; Req_Disco = $reqDisco; Req_UEFI_SecureBoot = $reqFirmware; Req_TPM = $reqTPM; Req_Grafica = $reqGrafica
    CumpleRequisitos = $cumple; NoCumple = ($noCumple -join " | ")
    Punt_Ofimatica = $usos["Ofimática"]; Punt_Moderado = $usos["Moderado"]; Punt_Diseno = $usos["Diseño"]; Punt_Intensivo = $usos["Intensivo"]
    CuellosBotella = ($cuellos -join " | "); MinutosActualizacion = "$minutos-$($minutos + 30)"
    Veredicto = $veredicto; Recomendaciones = ($recomendaciones -join " | "); Avisos = ($avisos -join " | ")
}

Write-Output "=== EVALUACIÓN WINDOWS 11: $equipo ($($datos.Fecha) $($datos.Hora)) ==="
Write-Output "Equipo:      $($datos.Fabricante) $($datos.Modelo) - n.º de serie $($datos.NumeroSerie)"
Write-Output "Sistema:     $nombreSO $versionSO (build $buildCompleta)"
Write-Output "Procesador:  $($datos.Procesador) - $nucleos núcleos / $hilos hilos - $($datos.MHz) MHz"
Write-Output "RAM:         $ramGB GB $tipoRAM"
Write-Output "Disco:       $tipoDisco de $discoGB GB - libres en ${letra} $libreGB GB"
Write-Output "Gráfica:     $nombresGrafica$(if ($wddm) { " (WDDM $wddm)" })"
Write-Output "Firmware:    UEFI $uefi - Secure Boot $secureBoot - TPM $tpm - BIOS del $($datos.FechaBIOS)"
Write-Output "Antigüedad:  unos $antiguedad años ($origenAntiguedad)"
Write-Output ""
Write-Output "--- Requisitos oficiales ---"
foreach ($k in $requisitos.Keys) { Write-Output ("  {0,-20} {1}" -f $k, $requisitos[$k]) }
Write-Output "  CUMPLE TODOS:        $cumple"
foreach ($x in $noCumple) { Write-Output "  - $x" }
Write-Output ""
Write-Output "--- Rendimiento esperado por uso (orientativo) ---"
foreach ($k in $usos.Keys) { Write-Output ("  {0,-12} {1,3}/100  {2}" -f $k, $usos[$k], (Get-TextoViabilidad $usos[$k])) }
if ($cuellos.Count) { Write-Output "  Cuellos de botella:"; foreach ($x in $cuellos) { Write-Output "    - $x" } }
if (-not $esWin11) { Write-Output "  Tiempo estimado de actualización: $minutos-$($minutos + 30) minutos" }
Write-Output ""
Write-Output "VEREDICTO: $veredicto"
foreach ($x in $recomendaciones) { Write-Output "  * $x" }
foreach ($x in $avisos) { Write-Output "  (aviso) $x" }
Write-Output ""
Write-Output (ConvertTo-LineaDatos $datos)

if ($SinCsv) { exit 0 }
try {
    $dir = Get-CarpetaSalida $Carpeta
    $csv = Join-Path $dir ("Win11_{0}_{1:yyyyMMdd_HHmmss}.csv" -f $equipo, $ahora)
    [pscustomobject]$datos | Export-Csv -LiteralPath $csv -Delimiter ";" -NoTypeInformation -Encoding $CodificacionCsv
    Write-Output "CSV: $csv"
    exit 0
} catch {
    Write-Output "ERROR al guardar el CSV: $($_.Exception.Message)"
    exit 2
}
