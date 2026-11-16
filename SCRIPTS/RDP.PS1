# =============================================================================
# rdp_fix_abril2026.ps1
# Deshabilita el nuevo dialogo de advertencia RDP introducido en abril 2026
# (KB5083769 / KB5082052) - CVE-2026-26151
# Requiere ejecutarse con privilegios de administrador
# =============================================================================

# Auto-relanzar con Bypass si la politica de ejecucion lo requiere
if ($MyInvocation.ScriptName -ne "" -and (Get-ExecutionPolicy) -ne "Bypass") {
    powershell.exe -ExecutionPolicy Bypass -File $MyInvocation.MyCommand.Path
    exit
}

$ErrorActionPreference = "SilentlyContinue"

# --- HKLM: afecta a todos los usuarios del equipo ---
$keyMachine = "HKLM:\Software\Policies\Microsoft\Windows NT\Terminal Services\Client"
if (-not (Test-Path $keyMachine)) {
    New-Item -Path $keyMachine -Force | Out-Null
}
Set-ItemProperty -Path $keyMachine -Name "RedirectionWarningDialogVersion" -Value 1 -Type DWord | Out-Null

# --- HKCU: suprime el aviso educativo de primera vez para el usuario actual ---
$keyUser = "HKCU:\Software\Microsoft\Terminal Server Client"
if (-not (Test-Path $keyUser)) {
    New-Item -Path $keyUser -Force | Out-Null
}
Set-ItemProperty -Path $keyUser -Name "RdpLaunchConsentAccepted" -Value 1 -Type DWord | Out-Null
