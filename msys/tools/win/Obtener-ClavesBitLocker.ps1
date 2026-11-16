#Requires -Version 5.1
<#
.SYNOPSIS
    Estado de BitLocker de cada unidad del equipo y sus contraseñas de recuperacion. Solo lee: nunca activa,
    suspende ni cambia BitLocker.

.DESCRIPTION
    Lee el estado con la clase WMI Win32_EncryptableVolume, que no depende del idioma de Windows (la salida de
    manage-bde si). Por cada unidad con letra: tipo (sistema, datos, extraible), proteccion (activada, suspendida,
    desactivada), cifrado y porcentaje, metodo, protectores (TPM, PIN, contraseña de recuperacion...) y las
    contraseñas de recuperacion de 48 digitos.
    Pensado para ejecutarse en local o en muchos equipos a la vez (LogMeIn One2Many, Intune, GPO): escribe una linea
    por unidad en la salida y un CSV por equipo (BitLocker_EQUIPO_fecha.csv), de forma que todos pueden dejarlo en la
    misma carpeta compartida sin pisarse. Con -UnirCarpeta junta esos CSV en uno (el ultimo de cada equipo).
    Necesita administrador o SYSTEM (salvo -UnirCarpeta).
    Codigos de salida: 0 bien, 1 sin permisos o sin BitLocker, 2 no se pudo guardar el CSV.

.EXAMPLE
    .\Obtener-ClavesBitLocker.ps1
    .\Obtener-ClavesBitLocker.ps1 -Carpeta \\servidor\it$\BitLocker
    .\Obtener-ClavesBitLocker.ps1 -UnirCarpeta \\servidor\it$\BitLocker
#>

[CmdletBinding()]
param(
    [string]$Carpeta,
    [switch]$SoloEstado,
    [switch]$SinCsv,
    [string]$UnirCarpeta
)

$ErrorActionPreference = "Stop"
$CodificacionCsv = if ($PSVersionTable.PSVersion.Major -ge 6) { "utf8BOM" } else { "UTF8" }   # CSV con BOM en 5.1 y en 7: Excel abre bien las tildes

# ============================================================ funciones
function Get-TextoProtector {
    param([int]$Tipo)
    switch ($Tipo) {
        1 { "TPM" } 2 { "Clave externa (USB)" } 3 { "Contraseña de recuperación" } 4 { "TPM y PIN" }
        5 { "TPM y clave de inicio" } 6 { "TPM, PIN y clave de inicio" } 7 { "Clave pública" } 8 { "Contraseña" }
        9 { "TPM y certificado" } 10 { "Cuenta o grupo de Active Directory" }
        default { "Tipo $Tipo" }
    }
}

function Get-TextoMetodo {
    param([int]$Metodo)
    switch ($Metodo) {
        0 { "" } 1 { "AES 128 con difusor" } 2 { "AES 256 con difusor" } 3 { "AES 128" } 4 { "AES 256" }
        5 { "Por hardware" } 6 { "XTS-AES 128" } 7 { "XTS-AES 256" }
        default { "Método $Metodo" }
    }
}

function Get-TextoEstadoBitLocker {
    <# Proteccion y cifrado en texto a partir de ProtectionStatus, ConversionStatus, porcentaje y numero de protectores. #>
    param([int]$Proteccion, [int]$Conversion, [double]$Porcentaje, [int]$NumProtectores)
    $pc = [math]::Round($Porcentaje)
    $cifrado = switch ($Conversion) {
        0 { "Sin cifrar" } 1 { "Cifrada" } 2 { "Cifrando ($pc %)" } 3 { "Descifrando ($pc % cifrado)" }
        4 { "Cifrado en pausa ($pc %)" } 5 { "Descifrado en pausa ($pc % cifrado)" }
        default { "Desconocido" }
    }
    $prot = if ($Proteccion -eq 1) { "Activada" }
    elseif ($Proteccion -eq 2) { "Desconocida (unidad bloqueada)" }
    elseif ($Conversion -eq 0) { "Desactivada" }
    elseif ($NumProtectores -eq 0) { "Desactivada (cifrada sin protectores)" }
    elseif ($Conversion -eq 1) { "Suspendida" }
    else { "Desactivada" }
    return [pscustomobject]@{ Proteccion = $prot; Cifrado = $cifrado }
}

function Invoke-Volumen {
    <# Llama a un metodo de Win32_EncryptableVolume y falla si no devuelve 0. #>
    param($Volumen, [string]$Metodo, [hashtable]$Argumentos = @{})
    $r = Invoke-CimMethod -InputObject $Volumen -MethodName $Metodo -Arguments $Argumentos
    if ($r.ReturnValue -ne 0) { throw ("{0} devolvio 0x{1:X8}" -f $Metodo, [uint32]$r.ReturnValue) }
    return $r
}

function Join-CsvBitLocker {
    <# Junta los BitLocker_*.csv de una carpeta: se queda con el mas reciente de cada equipo. #>
    param([string]$Ruta)
    if (-not (Test-Path -LiteralPath $Ruta)) { throw "No existe la carpeta: $Ruta" }
    $archivos = @(Get-ChildItem -LiteralPath $Ruta -Filter "BitLocker_*.csv" | Where-Object { $_.Name -ne "BitLocker_TODOS.csv" } | Sort-Object LastWriteTime -Descending)
    $vistos = @{}
    $filas = New-Object System.Collections.Generic.List[object]
    foreach ($a in $archivos) {
        $datos = @(Import-Csv -LiteralPath $a.FullName -Delimiter ";" -Encoding UTF8)
        if (-not $datos.Count -or -not $datos[0].Equipo) { continue }
        $eq = $datos[0].Equipo.ToUpper()
        if ($vistos.ContainsKey($eq)) { continue }
        $vistos[$eq] = $true
        foreach ($d in $datos) { $filas.Add($d) }
    }
    return [pscustomobject]@{ Archivos = $archivos.Count; Equipos = $vistos.Count; Filas = $filas.ToArray() }
}

# ============================================================ unir CSV de muchos equipos
if ($UnirCarpeta) {
    try {
        $u = Join-CsvBitLocker -Ruta $UnirCarpeta
        if (-not $u.Filas.Count) { Write-Output "No hay ningun BitLocker_*.csv con datos en $UnirCarpeta"; exit 1 }
        $destino = Join-Path $UnirCarpeta "BitLocker_TODOS.csv"
        $u.Filas | Sort-Object Equipo, Unidad | Export-Csv -LiteralPath $destino -Delimiter ";" -NoTypeInformation -Encoding $CodificacionCsv
        Write-Output "$($u.Equipos) equipos ($($u.Archivos) archivos, el mas reciente de cada uno) -> $destino"
        exit 0
    } catch { Write-Output "ERROR: $($_.Exception.Message)"; exit 2 }
}

# ============================================================ inicio
$equipo = $env:COMPUTERNAME
$ahora = Get-Date
$esAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $esAdmin) { Write-Output "$equipo ERROR: hay que ejecutarlo como administrador (o como SYSTEM)."; exit 1 }

if (-not $Carpeta -and $PSScriptRoot) { $Carpeta = Join-Path $PSScriptRoot "Resultados" }
try { $serie = [string](Get-CimInstance -ClassName Win32_BIOS).SerialNumber } catch { $serie = "" }
try {
    $volumenes = @(Get-CimInstance -Namespace "root/cimv2/Security/MicrosoftVolumeEncryption" -ClassName Win32_EncryptableVolume)
} catch {
    Write-Output "$equipo BitLocker no disponible (edicion de Windows sin BitLocker o componente no instalado): $($_.Exception.Message)"
    exit 1
}

$filas = New-Object System.Collections.Generic.List[object]
foreach ($v in @($volumenes | Where-Object { $_.DriveLetter } | Sort-Object DriveLetter)) {
    $fila = [ordered]@{
        Equipo = $equipo; NumeroSerie = $serie; Fecha = $ahora.ToString("dd/MM/yyyy"); Hora = $ahora.ToString("HH:mm")
        Unidad = $v.DriveLetter
        Tipo = switch ([string]$v.VolumeType) { "0" { "Sistema" } "1" { "Datos" } "2" { "Extraíble" } default { "" } }
        Proteccion = ""; Cifrado = ""; Metodo = ""; Protectores = ""; IdProtector = ""; ClaveRecuperacion = ""; Nota = ""
    }
    try {
        $ids = @((Invoke-Volumen $v "GetKeyProtectors" @{ KeyProtectorType = [uint32]0 }).VolumeKeyProtectorID | Where-Object { $_ })
        $tipos = [ordered]@{}
        foreach ($id in $ids) { $tipos[$id] = [int](Invoke-Volumen $v "GetKeyProtectorType" @{ VolumeKeyProtectorID = $id }).KeyProtectorType }
        $conv = Invoke-Volumen $v "GetConversionStatus"
        $prot = [int](Invoke-Volumen $v "GetProtectionStatus").ProtectionStatus
        $estado = Get-TextoEstadoBitLocker -Proteccion $prot -Conversion ([int]$conv.ConversionStatus) -Porcentaje ([double]$conv.EncryptionPercentage) -NumProtectores $ids.Count
        $fila.Proteccion = $estado.Proteccion
        $fila.Cifrado = $estado.Cifrado
        try { $fila.Metodo = Get-TextoMetodo ([int](Invoke-Volumen $v "GetEncryptionMethod").EncryptionMethod) } catch { }
        $fila.Protectores = (@($tipos.Values | ForEach-Object { Get-TextoProtector $_ } | Select-Object -Unique) -join ", ")

        $recuperacion = @($tipos.Keys | Where-Object { $tipos[$_] -eq 3 })
        if (-not $recuperacion.Count) {
            if ([int]$conv.ConversionStatus -ne 0) { $fila.Nota = "Cifrada sin contraseña de recuperación" }
            $filas.Add([pscustomobject]$fila)
            continue
        }
        foreach ($id in $recuperacion) {
            $f = [ordered]@{}
            foreach ($k in $fila.Keys) { $f[$k] = $fila[$k] }
            $f.IdProtector = $id
            if ($SoloEstado) { $f.ClaveRecuperacion = "(no se muestra: -SoloEstado)" }
            else {
                try { $f.ClaveRecuperacion = [string](Invoke-Volumen $v "GetKeyProtectorNumericalPassword" @{ VolumeKeyProtectorID = $id }).NumericalPassword }
                catch { $f.Nota = "No se pudo leer la clave: $($_.Exception.Message)" }
            }
            if ($recuperacion.Count -gt 1) { $f.Nota = (@($f.Nota, "Tiene $($recuperacion.Count) contraseñas de recuperación") | Where-Object { $_ }) -join ". " }
            $filas.Add([pscustomobject]$f)
        }
    } catch {
        $fila.Nota = "No se pudo leer: $($_.Exception.Message)"
        $filas.Add([pscustomobject]$fila)
    }
}

if (-not $filas.Count) { Write-Output "$equipo no tiene unidades con letra que admitan BitLocker" }
foreach ($f in $filas) {
    $cabecera = "$($f.Equipo) $($f.Unidad)" + $(if ($f.Tipo) { " [$($f.Tipo)]" } else { "" })
    $partes = @($cabecera, "Protección: $($f.Proteccion)", $f.Cifrado, $f.Metodo)
    if ($f.Protectores) { $partes += "Protectores: $($f.Protectores)" }
    if ($f.ClaveRecuperacion) { $partes += "Clave: $($f.ClaveRecuperacion) (ID $($f.IdProtector))" }
    if ($f.Nota) { $partes += $f.Nota }
    Write-Output ((@($partes | Where-Object { $_ })) -join " | ")
}

if ($SinCsv -or -not $Carpeta -or -not $filas.Count) { exit 0 }
try {
    if (-not (Test-Path -LiteralPath $Carpeta)) { New-Item -ItemType Directory -Force -Path $Carpeta | Out-Null }
    # Con -SoloEstado otro prefijo: -UnirCarpeta no debe tomar un CSV sin claves como el ultimo del equipo
    $prefijo = if ($SoloEstado) { "BitLockerEstado" } else { "BitLocker" }
    $csv = Join-Path $Carpeta ("{0}_{1}_{2:yyyyMMdd_HHmmss}.csv" -f $prefijo, $equipo, $ahora)
    $filas | Export-Csv -LiteralPath $csv -Delimiter ";" -NoTypeInformation -Encoding $CodificacionCsv
    Write-Output "$equipo CSV: $csv"
    exit 0
} catch {
    Write-Output "$equipo ERROR al guardar el CSV en ${Carpeta}: $($_.Exception.Message)"
    exit 2
}
