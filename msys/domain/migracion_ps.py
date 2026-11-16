# -*- coding: utf-8 -*-
"""Parte del inventario de migración que se lee con PowerShell (CIM, cmdlets de
red, impresoras, tareas, BitLocker, idiomas...), en UNA sola ejecución que
devuelve un JSON con un apartado por bloque. Cada bloque va en su propio
try/catch: si uno falla (p. ej. BitLocker sin administrador) el resto sigue.

Basado en LAUNCHER\\SCRIPTS\\MIGRACION\\MIGRACION.PS1 (v3.0), ampliado."""

PS_INVENTARIO = r"""
$ErrorActionPreference = 'SilentlyContinue'
$r = [ordered]@{}

try {
    $cs = Get-CimInstance Win32_ComputerSystem; $os = Get-CimInstance Win32_OperatingSystem
    $bios = Get-CimInstance Win32_BIOS; $cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
    $cv = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
    $r.equipo = @{
        nombre = $env:COMPUTERNAME; dominio = $cs.Domain; enDominio = [bool]$cs.PartOfDomain
        fabricante = $cs.Manufacturer; modelo = $cs.Model; serie = $bios.SerialNumber
        so = $os.Caption; version = $cv.DisplayVersion; build = "$($os.BuildNumber).$($cv.UBR)"
        arquitectura = $os.OSArchitecture; cpu = $cpu.Name.Trim()
        ramGB = [math]::Round($cs.TotalPhysicalMemory / 1GB, 1); usuario = $cs.UserName
    }
} catch {}

try {
    $ds = (dsregcmd /status) -join "`n"
    $r.equipo.entra = ($ds -match 'AzureAdJoined\s*:\s*YES')
    $r.equipo.entraHibrido = ($ds -match 'DomainJoined\s*:\s*YES') -and ($ds -match 'AzureAdJoined\s*:\s*YES')
    if ($ds -match 'TenantName\s*:\s*(.+)') { $r.equipo.inquilino = $matches[1].Trim() }
} catch {}

try {
    $lic = Get-CimInstance SoftwareLicensingProduct -Filter "PartialProductKey IS NOT NULL AND Name LIKE 'Windows%'" | Select-Object -First 1
    $r.licencia = @{ canal = [string]$lic.ProductKeyChannel; estado = [int]$lic.LicenseStatus; descripcion = [string]$lic.Description }
} catch {}

try {
    $r.red = @(Get-NetIPConfiguration | Where-Object { $_.NetAdapter.Status -eq 'Up' } | ForEach-Object {
        $ifx = $_.InterfaceIndex
        $ip4 = Get-NetIPInterface -InterfaceIndex $ifx -AddressFamily IPv4
        @{
            nombre = $_.InterfaceAlias; descripcion = $_.InterfaceDescription; mac = $_.NetAdapter.MacAddress
            ip = @($_.IPv4Address | ForEach-Object { "$($_.IPAddress)/$($_.PrefixLength)" }) -join ', '
            puerta = @($_.IPv4DefaultGateway | ForEach-Object { $_.NextHop }) -join ', '
            dns = @($_.DNSServer | Where-Object { $_.AddressFamily -eq 2 } | ForEach-Object { $_.ServerAddresses }) -join ', '
            dhcp = [string]$ip4.Dhcp
        }
    })
} catch {}

try {
    $is = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Internet Settings'
    $r.proxy = @{ activo = [bool]$is.ProxyEnable; servidor = [string]$is.ProxyServer; excepciones = [string]$is.ProxyOverride; pac = [string]$is.AutoConfigURL }
} catch {}

try { $r.vpn = @(Get-VpnConnection | ForEach-Object { @{ nombre = $_.Name; servidor = $_.ServerAddress; tipo = [string]$_.TunnelType; autenticacion = [string]($_.AuthenticationMethod -join ',') } }) } catch {}
try { $r.vpnTodos = @(Get-VpnConnection -AllUserConnection | ForEach-Object { @{ nombre = $_.Name; servidor = $_.ServerAddress; tipo = [string]$_.TunnelType; autenticacion = [string]($_.AuthenticationMethod -join ',') } }) } catch {}

try {
    $r.volumenes = @(Get-Volume | Where-Object { $_.DriveLetter } | ForEach-Object {
        @{ letra = [string]$_.DriveLetter; etiqueta = $_.FileSystemLabel; sistema = $_.FileSystem; tipo = [string]$_.DriveType
           totalGB = [math]::Round($_.Size / 1GB, 1); libreGB = [math]::Round($_.SizeRemaining / 1GB, 1) }
    })
} catch {}
try { $r.bitlocker = @(Get-BitLockerVolume | ForEach-Object { @{ letra = $_.MountPoint; estado = [string]$_.ProtectionStatus; cifrado = [string]$_.VolumeStatus; protectores = (@($_.KeyProtector | ForEach-Object { [string]$_.KeyProtectorType }) -join ', ') } }) } catch {}

try {
    $unidades = @{}
    Get-CimInstance Win32_MappedLogicalDisk | ForEach-Object { $unidades[$_.DeviceID] = $_.ProviderName }
    Get-ChildItem 'HKCU:\Network' | ForEach-Object { $l = "$($_.PSChildName.ToUpper()):"; if (-not $unidades[$l]) { $unidades[$l] = (Get-ItemProperty $_.PSPath).RemotePath } }
    $r.unidadesRed = @($unidades.GetEnumerator() | Sort-Object Name | ForEach-Object { @{ letra = $_.Name; ruta = [string]$_.Value } })
} catch {}
try { $r.compartidas = @(Get-SmbShare | Where-Object { $_.Name -notlike '*$' } | ForEach-Object { @{ nombre = $_.Name; ruta = $_.Path; descripcion = $_.Description } }) } catch {}

try {
    $puertos = @{}; Get-PrinterPort | ForEach-Object { $puertos[$_.Name] = [string]$_.PrinterHostAddress }
    $pred = (Get-CimInstance Win32_Printer | Where-Object Default).Name
    $r.impresoras = @(Get-Printer | ForEach-Object {
        @{ nombre = $_.Name; driver = $_.DriverName; puerto = $_.PortName; ip = $puertos[$_.PortName]; tipo = [string]$_.Type
           compartida = [bool]$_.Shared; servidor = [string]$_.ComputerName; predeterminada = ($_.Name -eq $pred) }
    })
} catch {}

try {
    $r.tareas = @(Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '\Microsoft\*' -and $_.State -ne 'Disabled' } | ForEach-Object {
        $a = $_.Actions | Select-Object -First 1
        @{ nombre = $_.TaskName; ruta = $_.TaskPath; accion = "$($a.Execute) $($a.Arguments)".Trim(); autor = [string]$_.Author; usuario = [string]$_.Principal.UserId }
    })
} catch {}

try {
    $r.usuarios = @(Get-LocalUser | ForEach-Object {
        @{ nombre = $_.Name; completo = $_.FullName; activo = [bool]$_.Enabled; ultimo = if ($_.LastLogon) { $_.LastLogon.ToString('dd/MM/yyyy HH:mm') } else { '' } }
    })
    $admins = @(Get-LocalGroupMember -SID 'S-1-5-32-544' | ForEach-Object { $_.Name })
    $r.administradores = $admins
} catch {}

try {
    $r.perfiles = @(Get-ChildItem 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList' | Where-Object { $_.PSChildName -match '^S-1-(5-21|12-1)-' } | ForEach-Object {
        $p = Get-ItemProperty $_.PSPath; $cuenta = ''
        try { $cuenta = (New-Object System.Security.Principal.SecurityIdentifier($_.PSChildName)).Translate([System.Security.Principal.NTAccount]).Value } catch {}
        @{ sid = $_.PSChildName; ruta = $p.ProfileImagePath; cuenta = $cuenta }
    })
} catch {}

try {
    $r.certificados = @(Get-ChildItem Cert:\CurrentUser\My | ForEach-Object {
        @{ sujeto = $_.Subject; emisor = $_.Issuer; caduca = $_.NotAfter.ToString('dd/MM/yyyy'); huella = $_.Thumbprint
           clavePrivada = [bool]$_.HasPrivateKey; exportable = [bool]($_.HasPrivateKey -and $_.PrivateKey -and $_.PrivateKey.CspKeyContainerInfo.Exportable) }
    })
} catch {}

try { $r.idiomas = @(Get-WinUserLanguageList | ForEach-Object { @{ idioma = $_.LanguageTag; nombre = $_.LocalizedName; teclados = (@($_.InputMethodTips) -join ', ') } }) } catch {}
try { $tz = Get-TimeZone; $r.zonaHoraria = "$($tz.Id) ($($tz.DisplayName))" } catch {}

try {
    $wmon = @(Get-CimInstance -Namespace root\wmi -ClassName WmiMonitorID)
    $r.monitores = @($wmon | ForEach-Object {
        @{ fabricante = (-join ($_.ManufacturerName | Where-Object { $_ } | ForEach-Object { [char]$_ }))
           modelo = (-join ($_.UserFriendlyName | Where-Object { $_ } | ForEach-Object { [char]$_ }))
           serie = (-join ($_.SerialNumberID | Where-Object { $_ } | ForEach-Object { [char]$_ })) }
    })
} catch {}

try {
    $r.usb = @(Get-PnpDevice -Status OK | Where-Object {
        $_.InstanceId -like 'USB\*' -and $_.FriendlyName -and $_.FriendlyName.Length -gt 3 -and
        $_.Class -notin @('USB', 'System', 'Processor', 'Computer', 'DiskDrive', 'Volume', 'Net', 'Monitor', 'Display', 'Printer', 'PrintQueue', 'HIDClass', 'SoftwareDevice') -and
        $_.FriendlyName -notmatch 'Host Controller|Root Hub|Generic|Composite|Billboard|estándar|Standard|Dispositivo de entrada'
    } | Sort-Object FriendlyName -Unique | ForEach-Object { @{ nombre = $_.FriendlyName; clase = [string]$_.Class; fabricante = [string]$_.Manufacturer } })
} catch {}

$r | ConvertTo-Json -Depth 5 -Compress
"""
