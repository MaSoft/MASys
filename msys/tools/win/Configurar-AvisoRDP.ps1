#Requires -Version 5.1
<#
.SYNOPSIS
    Consulta, desactiva o restaura el aviso que muestra Escritorio remoto (mstsc) al abrir archivos .rdp
    (dialogo de advertencia de redireccion y consentimiento de la primera vez).

.DESCRIPTION
    Valores que se usan (los del script original de abril de 2026):
      - Equipo:   HKLM\Software\Policies\Microsoft\Windows NT\Terminal Services\Client
                  RedirectionWarningDialogVersion = 1
      - Usuario:  HKCU\Software\Microsoft\Terminal Server Client  RdpLaunchConsentAccepted = 1
    El valor de usuario se aplica a TODOS los usuarios con sesion iniciada (sus HKCU cargados), no solo al que ejecuta
    el script: lanzado como SYSTEM (LogMeIn, Intune) el HKCU propio no sirve de nada.
    -Accion Estado (por defecto) solo lee. Desactivar y Restaurar necesitan administrador, admiten -Simular, preguntan
    antes (salvo -SinConfirmar) y guardan un .reg con los valores anteriores para deshacer.

.EXAMPLE
    .\Configurar-AvisoRDP.ps1
    .\Configurar-AvisoRDP.ps1 -Accion Desactivar -SinConfirmar
#>

[CmdletBinding()]
param(
    [ValidateSet("Estado", "Desactivar", "Restaurar")][string]$Accion = "Estado",
    [switch]$Simular,
    [switch]$SinConfirmar,
    [string]$CarpetaResultados
)

$ErrorActionPreference = "Stop"
$ClaveEquipo = "HKEY_LOCAL_MACHINE\Software\Policies\Microsoft\Windows NT\Terminal Services\Client"
$ValorEquipo = "RedirectionWarningDialogVersion"
$SubclaveUsuario = "Software\Microsoft\Terminal Server Client"
$ValorUsuario = "RdpLaunchConsentAccepted"

# ============================================================ utilidades comunes
function Write-Paso  ($t) { Write-Host "`n== $t" -ForegroundColor Cyan }
function Write-Ok    ($t) { Write-Host "  [OK]    $t" -ForegroundColor Green }
function Write-Aviso ($t) { Write-Host "  [AVISO] $t" -ForegroundColor Yellow }
function Write-Fallo ($t) { Write-Host "  [ERROR] $t" -ForegroundColor Red }
function Write-Sim   ($t) { Write-Host "  [SIM]   $t" -ForegroundColor Magenta }

# ============================================================ funciones
function ConvertTo-TextoReg {
    <# .reg que deja los valores como estaban: dword anterior o "-" si no existia. Rutas HKEY_... completas. #>
    param([object[]]$Cambios)
    $l = New-Object System.Collections.Generic.List[string]
    $l.Add("Windows Registry Editor Version 5.00")
    foreach ($g in ($Cambios | Group-Object Clave)) {
        $l.Add("")
        $l.Add("[$($g.Name)]")
        foreach ($c in $g.Group) {
            if ($null -eq $c.Anterior) { $l.Add(('"{0}"=-' -f $c.Nombre)) }
            else { $l.Add(('"{0}"=dword:{1:x8}' -f $c.Nombre, [uint32]$c.Anterior)) }
        }
    }
    return ($l -join "`r`n") + "`r`n"
}

function Get-Valor {
    param([string]$Clave, [string]$Nombre)
    $p = Get-ItemProperty -LiteralPath "Registry::$Clave" -Name $Nombre -ErrorAction SilentlyContinue
    if ($p) { return $p.$Nombre }
    return $null
}

function Get-UsuariosCargados {
    <# HKCU de los usuarios con sesion iniciada: SID de usuario (S-1-5-21-...) con su hive cargado en HKEY_USERS. #>
    foreach ($k in (Get-ChildItem -LiteralPath "Registry::HKEY_USERS" -ErrorAction SilentlyContinue)) {
        $sid = $k.PSChildName
        if ($sid -notmatch '^S-1-5-21-[\d-]+$') { continue }
        $nombre = try { (New-Object System.Security.Principal.SecurityIdentifier($sid)).Translate([System.Security.Principal.NTAccount]).Value } catch { $sid }
        [pscustomobject]@{ Sid = $sid; Nombre = $nombre; Clave = "HKEY_USERS\$sid\$SubclaveUsuario" }
    }
}

function Get-TextoEstado {
    param($ValorEquipo, [object[]]$ValoresUsuario)
    $eq = if ($ValorEquipo -eq 1) { "desactivado en el equipo (directiva)" } elseif ($null -eq $ValorEquipo) { "activo (sin directiva)" } else { "directiva con valor $ValorEquipo" }
    $us = @($ValoresUsuario | Where-Object { $_ -eq 1 }).Count
    return "Aviso de redirección: $eq · consentimiento aceptado en $us de $(@($ValoresUsuario).Count) usuarios con sesión"
}

# ============================================================ inicio
try {
    $usuarios = @(Get-UsuariosCargados)
    Write-Paso "Estado actual"
    $vEq = Get-Valor $ClaveEquipo $ValorEquipo
    Write-Host ("  Equipo: {0} = {1}" -f $ValorEquipo, $(if ($null -eq $vEq) { "(no existe)" } else { $vEq }))
    $vUs = foreach ($u in $usuarios) { $v = Get-Valor $u.Clave $ValorUsuario; Write-Host ("  {0}: {1} = {2}" -f $u.Nombre, $ValorUsuario, $(if ($null -eq $v) { "(no existe)" } else { $v })); $v }
    if (-not $usuarios.Count) { Write-Aviso "No hay ningún usuario con sesión iniciada: el valor de usuario se aplicará cuando se lance con la sesión abierta." }
    Write-Ok (Get-TextoEstado $vEq @($vUs))
    if ($Accion -eq "Estado") { exit 0 }

    $esAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $esAdmin -and -not $Simular) { throw "Para $Accion hay que ejecutarlo como administrador." }
    $objetivos = @([pscustomobject]@{ Clave = $ClaveEquipo; Nombre = $ValorEquipo; Quien = "Equipo" }) +
        @($usuarios | ForEach-Object { [pscustomobject]@{ Clave = $_.Clave; Nombre = $ValorUsuario; Quien = $_.Nombre } })
    $cambios = @(foreach ($o in $objetivos) {
            $ant = Get-Valor $o.Clave $o.Nombre
            if ($Accion -eq "Desactivar" -and $ant -eq 1) { continue }
            if ($Accion -eq "Restaurar" -and $null -eq $ant) { continue }
            [pscustomobject]@{ Clave = $o.Clave; Nombre = $o.Nombre; Quien = $o.Quien; Anterior = $ant }
        })
    Write-Paso "$Accion"
    if (-not $cambios.Count) { Write-Ok "No hay nada que cambiar."; exit 0 }
    foreach ($c in $cambios) { Write-Host ("  {0}: {1} {2}" -f $c.Quien, $c.Nombre, $(if ($Accion -eq "Desactivar") { "-> 1" } else { "se elimina" })) }
    if ($Simular) { Write-Sim "$($cambios.Count) valores se cambiarían. No se ha tocado nada."; exit 0 }
    if (-not $SinConfirmar) {
        if (-not [Environment]::UserInteractive -or [Security.Principal.WindowsIdentity]::GetCurrent().IsSystem) { throw "Sin nadie delante no se puede preguntar: usa -SinConfirmar." }
        if ((Read-Host "¿Aplicar? (S/N)") -notmatch '^(s|si|sí)$') { Write-Aviso "Cancelado."; exit 0 }
    }
    if (-not $CarpetaResultados) { $CarpetaResultados = if ($PSScriptRoot) { Join-Path $PSScriptRoot "Resultados" } else { Join-Path $env:SystemRoot "Temp" } }
    New-Item -ItemType Directory -Force -Path $CarpetaResultados | Out-Null
    $vuelta = Join-Path $CarpetaResultados ("AvisoRDP_vuelta_atras_{0}_{1:yyyyMMdd_HHmmss}.reg" -f $env:COMPUTERNAME, (Get-Date))
    (ConvertTo-TextoReg $cambios) | Out-File -LiteralPath $vuelta -Encoding Unicode
    Write-Ok "Copia para deshacer: $vuelta"
    foreach ($c in $cambios) {
        $ruta = "Registry::$($c.Clave)"
        if ($Accion -eq "Desactivar") {
            if (-not (Test-Path -LiteralPath $ruta)) { New-Item -Path $ruta -Force | Out-Null }
            New-ItemProperty -LiteralPath $ruta -Name $c.Nombre -Value 1 -PropertyType DWord -Force | Out-Null
        } else {
            Remove-ItemProperty -LiteralPath $ruta -Name $c.Nombre -ErrorAction SilentlyContinue
        }
    }
    Write-Ok "$($cambios.Count) valores cambiados. Se aplica al abrir de nuevo Escritorio remoto."
    exit 0
} catch {
    Write-Fallo $_.Exception.Message
    exit 1
}
