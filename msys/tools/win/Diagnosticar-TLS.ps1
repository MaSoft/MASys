#Requires -Version 5.1
<#
.SYNOPSIS
    Diagnostica por que un equipo no conecta por HTTPS con un servidor (TLS, certificado, proxy, .NET) y, con -Arreglar,
    activa TLS 1.2 en el cliente y en .NET Framework con copia para deshacer.

.DESCRIPTION
    Diagnostico (solo lee):
      - Windows, .NET Framework y PowerShell.
      - DNS, conexion al puerto y proxy del sistema para la URL.
      - Que versiones de TLS acepta el servidor desde este equipo (TLS 1.3, 1.2, 1.1 y 1.0, una a una), cifrado
        negociado y certificado: emisor, validez, nombre y cadena de confianza.
      - Configuracion de TLS del equipo: SCHANNEL (cliente), .NET Framework 4 y 2 (64 y 32 bits) y WinHTTP.
      - Conclusiones en lenguaje claro.
    -Arreglar (necesita administrador; con -Simular solo muestra los cambios):
      - TLS 1.2 de cliente activado en SCHANNEL.
      - .NET Framework 4 y 2 (64 y 32 bits, si estan instalados): SchUseStrongCrypto=1 y SystemDefaultTlsVersions=1.
      - En Windows anterior a 10: WinHTTP DefaultSecureProtocols = TLS 1.2.
      Nunca activa SSL, TLS 1.0 ni TLS 1.1. Guarda un .reg con los valores anteriores para deshacer.
      Hay que reiniciar el equipo (o al menos las aplicaciones) para que se aplique.

.EXAMPLE
    .\Diagnosticar-TLS.ps1 -Url https://api.ejemplo.com/login
    .\Diagnosticar-TLS.ps1 -Url https://api.ejemplo.com -Arreglar -Simular
#>

[CmdletBinding()]
param(
    [string]$Url,
    [switch]$Arreglar,
    [switch]$Simular,
    [switch]$SinConfirmar,
    [ValidateRange(1, 120)][int]$TiempoSegundos = 10,
    [string]$CarpetaResultados
)

$ErrorActionPreference = "Stop"

# ============================================================ utilidades comunes
function Write-Paso  ($t) { Write-Host "`n== $t" -ForegroundColor Cyan }
function Write-Ok    ($t) { Write-Host "  [OK]    $t" -ForegroundColor Green }
function Write-Aviso ($t) { Write-Host "  [AVISO] $t" -ForegroundColor Yellow }
function Write-Fallo ($t) { Write-Host "  [ERROR] $t" -ForegroundColor Red }
function Write-Sim   ($t) { Write-Host "  [SIM]   $t" -ForegroundColor Magenta }
function Write-Dato  ($t) { Write-Host "          $t" }

# ============================================================ funciones
function Get-VersionNetFramework {
    <# Version de .NET Framework 4.x a partir del valor Release del registro. #>
    param([int]$Release)
    $tabla = @(@(533320, "4.8.1"), @(528040, "4.8"), @(461808, "4.7.2"), @(461308, "4.7.1"), @(460798, "4.7"), @(394802, "4.6.2"),
        @(394254, "4.6.1"), @(393295, "4.6"), @(379893, "4.5.2"), @(378675, "4.5.1"), @(378389, "4.5"))
    foreach ($t in $tabla) { if ($Release -ge $t[0]) { return $t[1] } }
    return $(if ($Release) { "4.x ($Release)" } else { "" })
}

function Get-TextoEstadoProtocolo {
    <# Estado de un protocolo en SCHANNEL (cliente) a partir de Enabled y DisabledByDefault. #>
    param([string]$Protocolo, $Enabled, $DisabledByDefault, [int]$Build)
    if ($null -ne $Enabled -and [int]$Enabled -eq 0) { return "Desactivado (Enabled=0)" }
    if ($null -ne $DisabledByDefault -and [int]$DisabledByDefault -eq 1) { return "Desactivado por defecto (DisabledByDefault=1)" }
    if ($null -ne $Enabled -and [int]$Enabled -ne 0) { return "Activado" }
    switch ($Protocolo) {
        "TLS 1.2" { if ($Build -ge 9600) { return "Por defecto del sistema (activado)" } else { return "Por defecto del sistema (en Windows 7 / 2008 R2 está DESACTIVADO para el cliente)" } }
        "TLS 1.3" { if ($Build -ge 20348) { return "Por defecto del sistema (activado)" } else { return "No disponible en esta versión de Windows" } }
        default { return "Por defecto del sistema" }
    }
}

function Get-TextoErroresCertificado {
    param([string]$Errores)
    if (-not $Errores -or $Errores -eq "None") { return "" }
    $t = @()
    if ($Errores -match "RemoteCertificateNameMismatch") { $t += "el nombre del certificado no coincide con el servidor" }
    if ($Errores -match "RemoteCertificateChainErrors") { $t += "la cadena no es de confianza (emisor desconocido, caducado o revocado)" }
    if ($Errores -match "RemoteCertificateNotAvailable") { $t += "el servidor no presentó certificado" }
    return ($t -join "; ")
}

function ConvertTo-TextoReg {
    <# .reg que deja los valores como estaban: dword anterior o "-" si no existia. #>
    param([object[]]$Cambios)
    $l = New-Object System.Collections.Generic.List[string]
    $l.Add("Windows Registry Editor Version 5.00")
    foreach ($g in ($Cambios | Group-Object Ruta)) {
        $l.Add("")
        $l.Add("[" + ($g.Name -replace '^HKLM:\\', 'HKEY_LOCAL_MACHINE\') + "]")
        foreach ($c in $g.Group) {
            if ($null -eq $c.Anterior) { $l.Add(('"{0}"=-' -f $c.Nombre)) }
            else { $l.Add(('"{0}"=dword:{1:x8}' -f $c.Nombre, [uint32]$c.Anterior)) }
        }
    }
    return ($l -join "`r`n") + "`r`n"
}

function Get-ValorRegistro {
    param([string]$Ruta, [string]$Nombre)
    $p = Get-ItemProperty -Path $Ruta -Name $Nombre -ErrorAction SilentlyContinue
    if ($p) { return $p.$Nombre }
    return $null
}

function Get-MensajeInterno {
    param($Excepcion)
    $e = $Excepcion
    $m = $e.Message
    while ($e.InnerException) { $e = $e.InnerException; $m = $e.Message }
    return $m
}

function Test-ProtocoloTLS {
    <# Negociacion TLS con un unico protocolo. Acepta cualquier certificado para poder inspeccionarlo, pero anota sus errores. #>
    param([string]$Servidor, [int]$Puerto, [string]$Protocolo, [int]$TiempoMs)
    $tcp = New-Object System.Net.Sockets.TcpClient
    $ssl = $null
    $script:erroresCertTLS = ""
    try {
        $ar = $tcp.BeginConnect($Servidor, $Puerto, $null, $null)
        if (-not $ar.AsyncWaitHandle.WaitOne($TiempoMs)) { throw "Tiempo de conexión agotado" }
        $tcp.EndConnect($ar)
        $tcp.ReceiveTimeout = $TiempoMs; $tcp.SendTimeout = $TiempoMs
        $cb = [System.Net.Security.RemoteCertificateValidationCallback] { param($s, $c, $ch, $e) $script:erroresCertTLS = [string]$e; return $true }
        $ssl = New-Object System.Net.Security.SslStream($tcp.GetStream(), $false, $cb)
        $ssl.AuthenticateAsClient($Servidor, $null, [System.Security.Authentication.SslProtocols]$Protocolo, $false)
        $cert = New-Object System.Security.Cryptography.X509Certificates.X509Certificate2($ssl.RemoteCertificate)
        return [pscustomobject]@{ Protocolo = $Protocolo; Acepta = $true; Negociado = [string]$ssl.SslProtocol
            Cifrado = "$($ssl.CipherAlgorithm) $($ssl.CipherStrength) bits" + $(switch ([int]$ssl.KeyExchangeAlgorithm) { 44550 { ", ECDHE" } 43522 { ", DHE" } 41984 { ", RSA" } default { "" } }); Certificado = $cert
            ErroresCert = $script:erroresCertTLS; Error = "" }
    } catch {
        return [pscustomobject]@{ Protocolo = $Protocolo; Acepta = $false; Negociado = ""; Cifrado = ""; Certificado = $null; ErroresCert = ""; Error = (Get-MensajeInterno $_.Exception) }
    } finally {
        if ($ssl) { $ssl.Dispose() }
        $tcp.Dispose()
    }
}

# ============================================================ inicio
$inicio = Get-Date
if (-not $CarpetaResultados) { $CarpetaResultados = Join-Path $PSScriptRoot "Resultados" }
New-Item -ItemType Directory -Force -Path $CarpetaResultados | Out-Null
$servidor = ""; $puerto = 443
if ($Url) {
    if ($Url -notmatch '^[a-z]+://') { $Url = "https://$Url" }
    try { $u = [uri]$Url; $servidor = $u.Host; $puerto = if ($u.IsDefaultPort -and $u.Scheme -eq "https") { 443 } else { $u.Port } }
    catch { Write-Fallo "URL no válida: $Url"; exit 1 }
}
$etiqueta = if ($servidor) { $servidor -replace '[^A-Za-z0-9.-]', '_' } else { "configuracion" }
$informe = Join-Path $CarpetaResultados ("TLS_{0}_{1}_{2:yyyyMMdd_HHmmss}.txt" -f $env:COMPUTERNAME, $etiqueta, $inicio)
try { Start-Transcript -LiteralPath $informe -UseMinimalHeader -ErrorAction Stop | Out-Null } catch { try { Start-Transcript -LiteralPath $informe | Out-Null } catch { } }
$conclusiones = New-Object System.Collections.Generic.List[string]
$esAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " DIAGNOSTICO TLS - $env:COMPUTERNAME$(if ($servidor) { " -> $servidor`:$puerto" })" -ForegroundColor Cyan
if ($Arreglar -and $Simular) { Write-Host " MODO SIMULACION: no se cambia nada" -ForegroundColor Magenta }
Write-Host "==============================================" -ForegroundColor Cyan

try {
    Write-Paso "Equipo"
    $os = Get-CimInstance -ClassName Win32_OperatingSystem
    $build = [int]$os.BuildNumber
    $release = Get-ValorRegistro "HKLM:\SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full" "Release"
    $net4 = Get-VersionNetFramework ([int]$release)
    Write-Dato "Windows: $($os.Caption) (build $build)"
    Write-Dato ".NET Framework: $(if ($net4) { $net4 } else { 'sin .NET Framework 4' })"
    Write-Dato "PowerShell: $($PSVersionTable.PSVersion) ($($PSVersionTable.PSEdition))"
    Write-Dato "Hora del equipo: $($inicio.ToString('dd/MM/yyyy HH:mm:ss')) ($([System.TimeZoneInfo]::Local.Id))"
    if ($build -lt 9600) { $conclusiones.Add("Windows anterior a 8.1 / 2012 R2: TLS 1.2 no está activado de serie para el cliente; -Arreglar lo activa.") }
    if ($net4 -match '^\d+\.\d+' -and [version]$Matches[0] -lt [version]"4.6") { $conclusiones.Add(".NET Framework $net4 es antiguo: conviene instalar .NET Framework 4.8.") }

    Write-Paso "Configuración de TLS del equipo"
    $raiz = "HKLM:\SYSTEM\CurrentControlSet\Control\SecurityProviders\SCHANNEL\Protocols"
    $estados = @{}
    foreach ($p in "SSL 3.0", "TLS 1.0", "TLS 1.1", "TLS 1.2", "TLS 1.3") {
        $e = Get-TextoEstadoProtocolo $p (Get-ValorRegistro "$raiz\$p\Client" "Enabled") (Get-ValorRegistro "$raiz\$p\Client" "DisabledByDefault") $build
        $estados[$p] = $e
        Write-Dato ("SCHANNEL cliente {0,-8} {1}" -f $p, $e)
    }
    if ($estados["TLS 1.2"] -match "Desactivado|DESACTIVADO") { $conclusiones.Add("TLS 1.2 está desactivado para el cliente en este equipo: casi ningún servicio actual funcionará. -Arreglar lo activa.") }
    $claves = @(
        @{ Nombre = ".NET 4 (64 bits)"; Ruta = "HKLM:\SOFTWARE\Microsoft\.NETFramework\v4.0.30319" },
        @{ Nombre = ".NET 4 (32 bits)"; Ruta = "HKLM:\SOFTWARE\WOW6432Node\Microsoft\.NETFramework\v4.0.30319" },
        @{ Nombre = ".NET 2/3.5 (64 bits)"; Ruta = "HKLM:\SOFTWARE\Microsoft\.NETFramework\v2.0.50727" },
        @{ Nombre = ".NET 2/3.5 (32 bits)"; Ruta = "HKLM:\SOFTWARE\WOW6432Node\Microsoft\.NETFramework\v2.0.50727" })
    $netSinFuerte = @()
    foreach ($c in $claves) {
        if (-not (Test-Path $c.Ruta)) { Write-Dato ("{0,-22} no instalado" -f $c.Nombre); continue }
        $f = Get-ValorRegistro $c.Ruta "SchUseStrongCrypto"; $s = Get-ValorRegistro $c.Ruta "SystemDefaultTlsVersions"
        Write-Dato ("{0,-22} SchUseStrongCrypto={1}  SystemDefaultTlsVersions={2}" -f $c.Nombre, $(if ($null -eq $f) { "(no)" } else { $f }), $(if ($null -eq $s) { "(no)" } else { $s }))
        if ($f -ne 1 -or $s -ne 1) { $netSinFuerte += $c.Nombre }
    }
    if ($netSinFuerte) { $conclusiones.Add("Recomendable: $($netSinFuerte -join ', ') sin SchUseStrongCrypto/SystemDefaultTlsVersions. Las aplicaciones compiladas para .NET anterior a 4.7 pueden intentar TLS 1.0 y fallar con servidores actuales; -Arreglar lo configura.") }
    $whttp = Get-ValorRegistro "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Internet Settings\WinHttp" "DefaultSecureProtocols"
    Write-Dato ("WinHTTP DefaultSecureProtocols: {0}" -f $(if ($null -eq $whttp) { "(por defecto del sistema)" } else { "0x{0:X}" -f [int]$whttp }))
    $psDefecto = [string][System.Net.ServicePointManager]::SecurityProtocol
    Write-Dato "Protocolos de esta sesión de PowerShell: $psDefecto"
    if ($PSVersionTable.PSEdition -eq "Desktop" -and $psDefecto -notmatch "Tls12|SystemDefault") { $conclusiones.Add("Windows PowerShell 5.1 no usa TLS 1.2 por defecto en este equipo (usa: $psDefecto): Invoke-WebRequest e Invoke-RestMethod fallarán con servidores modernos. -Arreglar lo corrige.") }

    if ($servidor) {
        Write-Paso "Conexión con $servidor`:$puerto"
        $ips = @()
        try { $ips = @([System.Net.Dns]::GetHostAddresses($servidor) | ForEach-Object { $_.IPAddressToString }); Write-Ok "DNS: $($ips -join ', ')" }
        catch { Write-Fallo "DNS: $(Get-MensajeInterno $_.Exception)"; $conclusiones.Add("El nombre $servidor no se resuelve: revisar DNS o la URL.") }
        $proxy = [System.Net.WebRequest]::GetSystemWebProxy().GetProxy([uri]$Url)
        if ($proxy -and $proxy.AbsoluteUri -ne ([uri]$Url).AbsoluteUri) { Write-Aviso "Proxy del sistema para esta URL: $proxy"; $conclusiones.Add("Hay un proxy configurado ($proxy): si el proxy inspecciona HTTPS, el certificado que se ve es el del proxy.") }
        else { Write-Ok "Sin proxy para esta URL" }
        $tcpOk = $false
        if ($ips) {
            $t = New-Object System.Net.Sockets.TcpClient
            try { $ar = $t.BeginConnect($servidor, $puerto, $null, $null); $tcpOk = $ar.AsyncWaitHandle.WaitOne($TiempoSegundos * 1000) -and $t.Connected } catch { } finally { $t.Dispose() }
            if ($tcpOk) { Write-Ok "Puerto $puerto abierto" } else { Write-Fallo "No se conecta al puerto $puerto"; $conclusiones.Add("No hay conexión al puerto $puerto de $servidor (firewall, proxy obligatorio o servidor caído). El problema no es de TLS.") }
        }

        if ($tcpOk) {
            Write-Paso "Versiones de TLS que acepta $servidor desde este equipo"
            $nombres = [System.Enum]::GetNames([System.Security.Authentication.SslProtocols])
            $pruebas = @()
            foreach ($p in "Tls13", "Tls12", "Tls11", "Tls") {
                if ($nombres -notcontains $p) { Write-Dato "$p no disponible en esta versión de .NET"; continue }
                $r = Test-ProtocoloTLS -Servidor $servidor -Puerto $puerto -Protocolo $p -TiempoMs ($TiempoSegundos * 1000)
                $pruebas += $r
                $texto = @{ Tls13 = "TLS 1.3"; Tls12 = "TLS 1.2"; Tls11 = "TLS 1.1"; Tls = "TLS 1.0" }[$p]
                if ($r.Acepta) { Write-Ok "$texto SI - $($r.Cifrado)" } else { Write-Dato "$texto no - $($r.Error)" }
            }
            $buenas = @($pruebas | Where-Object { $_.Acepta -and $_.Protocolo -in "Tls12", "Tls13" })
            $viejas = @($pruebas | Where-Object { $_.Acepta -and $_.Protocolo -in "Tls", "Tls11" })
            if (-not @($pruebas | Where-Object Acepta).Count) {
                $conclusiones.Add("No se ha podido negociar ninguna versión de TLS. Si TLS 1.2 está desactivado aquí, -Arreglar; si no, el servidor exige algo que este Windows no tiene (cifrados o TLS 1.3) o no es un servidor HTTPS.")
            } elseif (-not $buenas.Count) {
                $conclusiones.Add("El servidor solo acepta TLS 1.0/1.1 (obsoletos): el problema es del servidor; avisar al proveedor.")
            } elseif ($viejas.Count) {
                $conclusiones.Add("El servidor también acepta TLS 1.0/1.1 (obsoletos): no impide conectar, pero conviene que el proveedor los desactive.")
            }
            $conCert = $pruebas | Where-Object { $_.Certificado } | Select-Object -First 1
            if ($conCert) {
                $c = $conCert.Certificado
                Write-Paso "Certificado de $servidor"
                Write-Dato "Sujeto: $($c.Subject)"
                Write-Dato "Emisor: $($c.Issuer)"
                Write-Dato "Válido: $($c.NotBefore.ToString('dd/MM/yyyy')) - $($c.NotAfter.ToString('dd/MM/yyyy'))"
                $err = Get-TextoErroresCertificado $conCert.ErroresCert
                if ($err) { Write-Fallo "No es de confianza: $err"; $conclusiones.Add("Certificado de $servidor no válido para este equipo: $err.") } else { Write-Ok "Certificado de confianza" }
                if ($c.NotAfter -lt $inicio) { $conclusiones.Add("El certificado caducó el $($c.NotAfter.ToString('dd/MM/yyyy')): es del servidor.") }
                if ($c.NotBefore -gt $inicio) { $conclusiones.Add("El certificado empieza el $($c.NotBefore.ToString('dd/MM/yyyy')), después de la hora de este equipo: revisar la fecha y hora de Windows.") }
                elseif ($err -match "cadena" -and $c.NotAfter -ge $inicio) { $conclusiones.Add("Si el emisor es una entidad conocida, faltan certificados raíz o intermedios: actualizar Windows (los raíz se descargan con Windows Update) o revisar si un proxy o antivirus inspecciona HTTPS.") }
            }
        }
    }

    Write-Paso "Conclusiones"
    if ($conclusiones.Count) { $n = 0; foreach ($c in $conclusiones) { $n++; Write-Host "  $n. $c" -ForegroundColor Yellow } }
    else { Write-Ok "No se ha encontrado ningún problema de TLS en este equipo$(if ($servidor) { " ni con $servidor" })." }

    if ($Arreglar) {
        Write-Paso "Reparación: TLS 1.2 en el cliente y en .NET Framework"
        if (-not $esAdmin -and -not $Simular) { throw "Para -Arreglar hay que ejecutarlo como administrador." }
        $objetivo = New-Object System.Collections.Generic.List[object]
        foreach ($n in @(@("Enabled", 1), @("DisabledByDefault", 0))) { $objetivo.Add([pscustomobject]@{ Ruta = "$raiz\TLS 1.2\Client"; Nombre = $n[0]; Valor = $n[1]; Crear = $true }) }
        foreach ($c in $claves) {
            if (-not (Test-Path $c.Ruta)) { continue }   # no se crean claves de un .NET que no esta instalado
            foreach ($n in "SchUseStrongCrypto", "SystemDefaultTlsVersions") { $objetivo.Add([pscustomobject]@{ Ruta = $c.Ruta; Nombre = $n; Valor = 1; Crear = $false }) }
        }
        if ($build -lt 10240) {
            foreach ($r in "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Internet Settings\WinHttp", "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Internet Settings\WinHttp") {
                $objetivo.Add([pscustomobject]@{ Ruta = $r; Nombre = "DefaultSecureProtocols"; Valor = 0x800; Crear = $true })
            }
        }
        $cambios = @(foreach ($o in $objetivo) {
                $ant = Get-ValorRegistro $o.Ruta $o.Nombre
                if ($null -ne $ant -and [int]$ant -eq $o.Valor) { continue }
                [pscustomobject]@{ Ruta = $o.Ruta; Nombre = $o.Nombre; Valor = $o.Valor; Anterior = $ant; Crear = $o.Crear }
            })
        if (-not $cambios.Count) { Write-Ok "Ya estaba todo configurado: no hay nada que cambiar." }
        else {
            foreach ($c in $cambios) { Write-Dato ("{0}\{1}: {2} -> {3}" -f ($c.Ruta -replace '^HKLM:\\', ''), $c.Nombre, $(if ($null -eq $c.Anterior) { "(no existe)" } else { $c.Anterior }), $c.Valor) }
            if ($Simular) { Write-Sim "$($cambios.Count) valores se cambiarían. No se ha tocado nada." }
            else {
                $seguir = $SinConfirmar -or ((Read-Host "`n¿Aplicar estos $($cambios.Count) cambios en el registro? (S/N)") -match '^(s|si|sí)$')
                if (-not $seguir) { Write-Aviso "Cancelado: no se ha cambiado nada." }
                else {
                    $vuelta = Join-Path $CarpetaResultados ("TLS_vuelta_atras_{0}_{1:yyyyMMdd_HHmmss}.reg" -f $env:COMPUTERNAME, $inicio)
                    (ConvertTo-TextoReg $cambios) | Out-File -LiteralPath $vuelta -Encoding Unicode
                    Write-Ok "Copia para deshacer: $vuelta (doble clic o reg import)"
                    foreach ($c in $cambios) {
                        if (-not (Test-Path $c.Ruta)) { New-Item -Path $c.Ruta -Force | Out-Null }
                        New-ItemProperty -Path $c.Ruta -Name $c.Nombre -Value $c.Valor -PropertyType DWord -Force | Out-Null
                    }
                    Write-Ok "$($cambios.Count) valores cambiados. Reinicia el equipo (o al menos las aplicaciones afectadas) para que se apliquen."
                }
            }
        }
    }
}
catch {
    Write-Fallo $_.Exception.Message
}
finally {
    Write-Host ""
    try { Stop-Transcript | Out-Null } catch { }
    if (Test-Path -LiteralPath $informe) { Write-Host "  Informe: $informe" }
}
