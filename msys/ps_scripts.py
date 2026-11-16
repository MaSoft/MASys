# -*- coding: utf-8 -*-
"""Scripts de PowerShell embebidos usados por los collectors de MASys.
Extraido de multisys.py (Fase 1 de modularizacion)."""

PS_CHECK_SMBSHARE = r"if (Get-Command Get-SmbShare -ErrorAction SilentlyContinue) { 'true' | ConvertTo-Json } else { 'false' | ConvertTo-Json }"
# Comprueba el estado real de las dos fuentes que usa la pestaña Conexiones
# Remotas (auditoria de "Inicio de sesion" + canal operativo RDP), sin
# depender de parsear el texto localizado de auditpol: solo se compara el
# valor de la columna 5 del CSV contra las dos variantes conocidas de "sin
# auditoria" (es/en) — el resto de idiomas de Windows no se detecta bien y
# el boton por defecto asume "inactiva" (falso negativo, no falso positivo).
PS_CHECK_REMOTE_AUDIT_STATUS = r"""
try {
    $guid = '{0CCE9215-69AE-11D9-BED3-505054503030}'
    # No asumir en que indice de linea cae la fila de datos del CSV (auditpol
    # intercala lineas en blanco de forma no siempre predecible) — se busca
    # la linea que contiene el GUID, inequivoca, en vez de una posicion fija.
    $csvLines = @(auditpol /get /subcategory:"$guid" /r)
    $dataLine = $csvLines | Where-Object { $_ -like "*$guid*" } | Select-Object -First 1
    $fields = $dataLine -split ','
    $inclusionSetting = $fields[4].Trim()
    $logonOk = $inclusionSetting -notmatch '^(No Auditing|Sin auditor.a)$'
} catch { $logonOk = $false }
try {
    $chanInfo = wevtutil gl "Microsoft-Windows-TerminalServices-RemoteConnectionManager/Operational"
    $rdpOk = ($chanInfo | Select-String '^enabled:\s*true$') -ne $null
} catch { $rdpOk = $false }
@{ LogonOk = $logonOk; RdpOk = $rdpOk } | ConvertTo-Json
"""
PS_GET_SMB_SHARES = r"Get-SmbShare | Select-Object Name,Path | ConvertTo-Json -Depth 3"
PS_GET_SMB_ACCESS = r"""
Get-SmbShareAccess -Name '{name}' -ErrorAction SilentlyContinue | ForEach-Object {{
    [PSCustomObject]@{{
        AccountName = $_.AccountName
        AccessRight = $_.AccessRight.ToString()
        AccessControlType = $_.AccessControlType.ToString()
    }}
}} | ConvertTo-Json
"""
PS_GET_WMI_SHARES = r"Get-WmiObject -Class Win32_Share | Where-Object {$_.Type -eq 0} | Select Name,Path | ConvertTo-Json -Depth 3"
PS_GET_ACL_UNC = r"""
try {{
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8

    # -LiteralPath: con -Path una carpeta con corchetes en el nombre («Cont[2024]») se toma como comodín y
    # devuelve 0 permisos sin error. -ErrorAction Stop: sin él, Get-Acl no lanza y el catch no se usa nunca.
    $acl = Get-Acl -LiteralPath '{path}' -ErrorAction Stop
    $acl.Access | ForEach-Object {{
        $rights = $_.FileSystemRights.ToString()
        if ($rights -match '^\d+$') {{
            switch ([int64]$rights) {{
                268435456 {{ $rights = 'FullControl (GenericAll)' }}
                1179817 {{ $rights = 'ReadAndExecute, Synchronize' }}
                -1610612736 {{ $rights = 'FullControl (GenericAll)' }}
                default {{ $rights = "Code: $rights" }}
            }}
        }}
        [PSCustomObject]@{{
            IdentityReference = $_.IdentityReference.Value
            FileSystemRights = $rights
            AccessControlType = $_.AccessControlType.ToString()
            IsInherited = $_.IsInherited
        }}
    }} | ConvertTo-Json
}} catch {{
    # Tabla hash dentro de @( ): antes era un bloque de script y su JSON no traía IdentityReference='ERROR'.
    @(@{{ IdentityReference='ERROR'; FileSystemRights=$_.Exception.Message; AccessControlType='Deny'; IsInherited=$false }}) | ConvertTo-Json
}}
"""

PS_GET_NETWORK_DRIVES = r"""
Get-PSDrive -PSProvider FileSystem | Where-Object {$_.DisplayRoot -ne $null} | ForEach-Object {
    $letra = $_.Name + ':'
    $rutaRemota = $_.DisplayRoot -replace '/', '\'
    [PSCustomObject]@{
        Letra = $letra
        RutaRemota = $rutaRemota
        Descripcion = $_.Description
        Estado = 'Conectada'
    }
} | ConvertTo-Json
"""

PS_GET_NETWORK_DRIVES_WMI = r"""
Get-WmiObject -Class Win32_MappedLogicalDisk | ForEach-Object {
    [PSCustomObject]@{
        Letra = $_.DeviceID
        RutaRemota = $_.ProviderName
        Descripcion = $_.VolumeName
        Estado = 'Conectada'
    }
} | ConvertTo-Json
"""

# Enhanced network drives collection with extended info (v3.0) - SIMPLIFIED
PS_GET_NETWORK_DRIVES_ENHANCED = r"""
# Forzar codificación de salida a UTF-8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$drives = @()
$adapter = Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | Select-Object -First 1
$velocidadRed = "N/A"
if ($adapter -and $adapter.LinkSpeed) {
    $linkSpeedStr = $adapter.LinkSpeed.ToString()
    if ($linkSpeedStr -match '(\d+\.?\d*)\s*(Gbps|Mbps|Kbps)') {
        $velocidadRed = $matches[0]
    }
}

Get-PSDrive -PSProvider FileSystem | Where-Object {$_.DisplayRoot -ne $null} | ForEach-Object {
    $letra = $_.Name + ':'
    $rutaRemota = $_.DisplayRoot -replace '/', '\'

    # Espacio en disco usando Win32_LogicalDisk (funciona con unidades de red)
    $totalGB = "N/A"
    $usadoGB = "N/A"
    $libreGB = "N/A"
    $porcentaje = "N/A"
    try {
        $disk = Get-CimInstance -ClassName Win32_LogicalDisk -Filter "DeviceID='$letra'" -ErrorAction Stop
        if ($disk -and $disk.Size) {
            $totalGB = "$([math]::Round($disk.Size / 1GB, 2)) GB"
            $usedBytes = $disk.Size - $disk.FreeSpace
            $usadoGB = "$([math]::Round($usedBytes / 1GB, 2)) GB"
            $libreGB = "$([math]::Round($disk.FreeSpace / 1GB, 2)) GB"
            $pct = [math]::Round(($usedBytes / $disk.Size) * 100, 1)
            $porcentaje = "$pct%"
        }
    } catch {}

    # Ping
    $ping = "N/A"
    try {
        if ($rutaRemota -match '\\\\([^\\]+)') {
            $server = $matches[1]
            $result = Test-Connection -ComputerName $server -Count 1 -ErrorAction Stop
            $ping = "$($result.ResponseTime) ms"
        }
    } catch {}

    # Credencial y Version SMB
    $credencial = "N/A"
    $versionSMB = "N/A"
    try {
        if ($rutaRemota -match '\\\\([^\\]+)') {
            $server = $matches[1]
            $smbConn = Get-SmbConnection -ServerName $server -ErrorAction Stop | Select-Object -First 1
            if ($smbConn) {
                $versionSMB = "SMB $($smbConn.Dialect)"
                if ($smbConn.UserName) {
                    $credencial = $smbConn.UserName
                }
            }
        }
    } catch {}

    # Persistencia
    $persistente = "Temporal"
    try {
        if (Test-Path "HKCU:\Network\$($_.Name)") {
            $persistente = "Persistente"
        }
    } catch {}

    # Accesibilidad
    # Test-Path devuelve $false para una ruta inaccesible, no lanza
    # excepcion (solo la lanza en casos muy excepcionales de proveedor
    # invalido) — descartar el resultado con "$null = ..." hacia que esta
    # comprobacion nunca detectara una unidad de red realmente caida.
    $accesible = "Si"
    $estado = "Conectada"
    try {
        $existeRuta = Test-Path "$letra\" -ErrorAction Stop
        if (-not $existeRuta) {
            $accesible = "No"
            $estado = "Error"
        }
    } catch {
        $accesible = "No"
        $estado = "Error"
    }

    $drives += [PSCustomObject]@{
        Letra = $letra
        RutaRemota = $rutaRemota
        Descripcion = $_.Description
        Estado = $estado
        TotalGB = $totalGB
        UsadoGB = $usadoGB
        LibreGB = $libreGB
        PorcentajeUso = $porcentaje
        Credencial = $credencial
        Persistente = $persistente
        VersionSMB = $versionSMB
        Velocidad = $velocidadRed
        PingMS = $ping
        Accesible = $accesible
    }
}
$drives | ConvertTo-Json
"""

PS_GET_CREDENTIALS = r"""
try {
    # Forzar codificación de salida a UTF-8
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8

    $creds = cmdkey /list 2>&1
    $output = @()
    $currentTarget = $null
    $currentUser = $null
    $currentType = $null

    foreach ($line in $creds) {
        $lineStr = $line.ToString().Trim()

        # Buscar patrones en español e inglés
        if ($lineStr -match 'Destino:\s*(.+)' -or $lineStr -match 'Target:\s*(.+)') {
            # Si ya tenemos un destino anterior, guardarlo
            if ($currentTarget) {
                $output += [PSCustomObject]@{
                    Destino = $currentTarget
                    Usuario = if ($currentUser) { $currentUser } else { '(sin usuario)' }
                    Tipo = if ($currentType) { $currentType } else { 'Generic' }
                }
            }
            $currentTarget = $matches[1].Trim()
            $currentUser = $null
            $currentType = $null
        }
        elseif ($lineStr -match 'Tipo:\s*(.+)' -or $lineStr -match 'Type:\s*(.+)') {
            $currentType = $matches[1].Trim()
        }
        elseif ($lineStr -match 'Usuario:\s*(.+)' -or $lineStr -match 'User:\s*(.+)') {
            $currentUser = $matches[1].Trim()
        }
    }

    # Guardar el último destino si existe
    if ($currentTarget) {
        $output += [PSCustomObject]@{
            Destino = $currentTarget
            Usuario = if ($currentUser) { $currentUser } else { '(sin usuario)' }
            Tipo = if ($currentType) { $currentType } else { 'Generic' }
        }
    }

    # Convertir a JSON con UTF-8. "@() | ConvertTo-Json" (con pipe) no
    # emite nada por stdout con 0 elementos — confirmado empíricamente,
    # mismo bug ya corregido en la categoría Red esta sesión.
    if ($output.Count -eq 0) {
        Write-Output "[]"
    } elseif ($output.Count -eq 1) {
        @($output) | ConvertTo-Json
    } else {
        $output | ConvertTo-Json
    }
} catch {
    Write-Output "[]"
}
"""

# Get WiFi passwords (v3.0)
PS_GET_WIFI_PASSWORDS = r"""
try {
    # Forzar codificación de salida a UTF-8
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8

    $profiles = netsh wlan show profiles | Select-String -Pattern 'Perfil de todos los usuarios\s*:\s*(.+)' | ForEach-Object { $_.Matches.Groups[1].Value.Trim() }
    if (-not $profiles) {
        $profiles = netsh wlan show profiles | Select-String -Pattern 'All User Profile\s*:\s*(.+)' | ForEach-Object { $_.Matches.Groups[1].Value.Trim() }
    }

    $wifiList = @()
    $currentProfile = netsh wlan show interfaces | Select-String -Pattern 'SSID\s*:\s*(.+)' | ForEach-Object { $_.Matches.Groups[1].Value.Trim() }

    foreach ($profile in $profiles) {
        $profileInfo = netsh wlan show profile name="$profile" key=clear

        $password = "N/A"
        $auth = "N/A"
        $cipher = "N/A"
        $connectionType = "N/A"
        $connected = "No"

        foreach ($line in $profileInfo) {
            if ($line -match 'Contenido de la clave\s*:\s*(.+)' -or $line -match 'Key Content\s*:\s*(.+)') {
                $password = $matches[1].Trim()
            }
            if ($line -match 'Autenticaci.n\s*:\s*(.+)' -or $line -match 'Authentication\s*:\s*(.+)') {
                $auth = $matches[1].Trim()
            }
            if ($line -match 'Cifrado\s*:\s*(.+)' -or $line -match 'Cipher\s*:\s*(.+)') {
                $cipher = $matches[1].Trim()
            }
            if ($line -match 'Tipo de conexi.n\s*:\s*(.+)' -or $line -match 'Connection type\s*:\s*(.+)') {
                $connectionType = $matches[1].Trim()
            }
        }

        if ($currentProfile -eq $profile) {
            $connected = "Si"
        }

        $wifiList += [PSCustomObject]@{
            Nombre = $profile
            Contraseña = $password
            Autenticacion = $auth
            Cifrado = $cipher
            TipoConexion = $connectionType
            Conectado = $connected
        }
    }

    # "$wifiList | ConvertTo-Json" con 0 elementos no emite NADA por
    # stdout (el pipeline colapsa un array vacío antes de llegar a
    # ConvertTo-Json) — run_powershell_json interpreta esa salida vacía
    # como fallo (None), indistinguible de un error real. "[]" explícito
    # para 0 elementos evita esa ambigüedad (equipos sin WiFi guardada,
    # como uno de escritorio con solo Ethernet, son un resultado legítimo).
    if ($wifiList.Count -eq 0) {
        Write-Output "[]"
    } else {
        $wifiList | ConvertTo-Json
    }
} catch {
    Write-Output "[]"
}
"""

# Get Certificates (v3.0)
PS_GET_CERTIFICATES = r"""
try {
    # Forzar codificación de salida a UTF-8
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8

    $certs = @()

    # Certificados del usuario actual
    Get-ChildItem Cert:\CurrentUser\My -ErrorAction SilentlyContinue | ForEach-Object {
        $estado = "Valido"
        if ($_.NotAfter -lt (Get-Date)) {
            $estado = "Caducado"
        } elseif ($_.NotAfter -lt (Get-Date).AddDays(30)) {
            $estado = "Proxima caducidad"
        }

        # Determinar si tiene clave privada exportable
        $exportable = "No"
        if ($_.HasPrivateKey) {
            try {
                $cert = [System.Security.Cryptography.X509Certificates.X509Certificate2]$_
                if ($cert.PrivateKey) {
                    $exportable = "Si"
                }
            } catch {
                $exportable = "No (bloqueada)"
            }
        }

        $certs += [PSCustomObject]@{
            Asunto = $_.Subject
            Emisor = $_.Issuer
            ValidoDesde = $_.NotBefore.ToString("dd/MM/yyyy HH:mm:ss")
            ValidoHasta = $_.NotAfter.ToString("dd/MM/yyyy HH:mm:ss")
            HuellaDigital = $_.Thumbprint
            Uso = ($_.EnhancedKeyUsageList.FriendlyName -join ", ")
            Ubicacion = "CurrentUser\My"
            Estado = $estado
            Exportable = $exportable
            AlgoritmoFirma = $_.SignatureAlgorithm.FriendlyName
            Autofirmado = if ($_.Subject -eq $_.Issuer) { "Si" } else { "No" }
        }
    }

    # Certificados raíz del usuario
    Get-ChildItem Cert:\CurrentUser\Root -ErrorAction SilentlyContinue | ForEach-Object {
        $estado = "Valido"
        if ($_.NotAfter -lt (Get-Date)) {
            $estado = "Caducado"
        }

        $certs += [PSCustomObject]@{
            Asunto = $_.Subject
            Emisor = $_.Issuer
            ValidoDesde = $_.NotBefore.ToString("dd/MM/yyyy HH:mm:ss")
            ValidoHasta = $_.NotAfter.ToString("dd/MM/yyyy HH:mm:ss")
            HuellaDigital = $_.Thumbprint
            Uso = "Certificado raiz"
            Ubicacion = "CurrentUser\Root"
            Estado = $estado
            Exportable = "No"
            AlgoritmoFirma = $_.SignatureAlgorithm.FriendlyName
            Autofirmado = if ($_.Subject -eq $_.Issuer) { "Si" } else { "No" }
        }
    }

    # Certificados del sistema (requiere admin)
    try {
        Get-ChildItem Cert:\LocalMachine\My -ErrorAction Stop | ForEach-Object {
            $estado = "Valido"
            if ($_.NotAfter -lt (Get-Date)) {
                $estado = "Caducado"
            } elseif ($_.NotAfter -lt (Get-Date).AddDays(30)) {
                $estado = "Proxima caducidad"
            }

            # Determinar si tiene clave privada exportable
            $exportable = "No"
            if ($_.HasPrivateKey) {
                try {
                    $cert = [System.Security.Cryptography.X509Certificates.X509Certificate2]$_
                    if ($cert.PrivateKey) {
                        $exportable = "Si"
                    }
                } catch {
                    $exportable = "No (bloqueada)"
                }
            }

            $certs += [PSCustomObject]@{
                Asunto = $_.Subject
                Emisor = $_.Issuer
                ValidoDesde = $_.NotBefore.ToString("dd/MM/yyyy HH:mm:ss")
                ValidoHasta = $_.NotAfter.ToString("dd/MM/yyyy HH:mm:ss")
                HuellaDigital = $_.Thumbprint
                Uso = ($_.EnhancedKeyUsageList.FriendlyName -join ", ")
                Ubicacion = "LocalMachine\My"
                Estado = $estado
                Exportable = $exportable
                AlgoritmoFirma = $_.SignatureAlgorithm.FriendlyName
                Autofirmado = if ($_.Subject -eq $_.Issuer) { "Si" } else { "No" }
            }
        }
    } catch {}

    # Autoridades raíz de confianza de TODO EL EQUIPO (todos los usuarios,
    # no solo el actual) — el sitio clásico donde malware/adware planta una
    # CA raíz falsa para interceptar HTTPS a nivel de sistema. No estaba
    # cubierto (solo se miraba CurrentUser\Root), así que una CA maliciosa
    # instalada aquí habría sido completamente invisible para esta pestaña.
    try {
        Get-ChildItem Cert:\LocalMachine\Root -ErrorAction Stop | ForEach-Object {
            $estado = "Valido"
            if ($_.NotAfter -lt (Get-Date)) {
                $estado = "Caducado"
            }

            $certs += [PSCustomObject]@{
                Asunto = $_.Subject
                Emisor = $_.Issuer
                ValidoDesde = $_.NotBefore.ToString("dd/MM/yyyy HH:mm:ss")
                ValidoHasta = $_.NotAfter.ToString("dd/MM/yyyy HH:mm:ss")
                HuellaDigital = $_.Thumbprint
                Uso = "Certificado raiz (equipo)"
                Ubicacion = "LocalMachine\Root"
                Estado = $estado
                Exportable = "No"
                AlgoritmoFirma = $_.SignatureAlgorithm.FriendlyName
                Autofirmado = if ($_.Subject -eq $_.Issuer) { "Si" } else { "No" }
            }
        }
    } catch {}

    # "$certs | ConvertTo-Json" (con pipe) no emite nada por stdout con 0
    # certificados — mismo bug ya corregido en la categoría Red esta sesión.
    if ($certs.Count -eq 0) { Write-Output "[]" } else { $certs | ConvertTo-Json }
} catch {
    Write-Output "[]"
}
"""

# ---------------------------
# Nuevos comandos PowerShell para funcionalidades adicionales
# ---------------------------

PS_GET_ENVIRONMENT_VARS = r"""
$userVars = [Environment]::GetEnvironmentVariables('User').Keys
Get-ChildItem Env: | ForEach-Object {
    [PSCustomObject]@{
        Nombre = $_.Name
        Valor = $_.Value
        Ambito = if ($userVars -contains $_.Name) { "Usuario" } else { "Sistema" }
    }
} | ConvertTo-Json
"""

PS_GET_NET_CONNECTIONS = r"""
try {
    # Caché de procesos: una sola llamada a Get-Process para TODAS las
    # conexiones/endpoints (antes era una llamada por conexión — con
    # cientos de sockets TCP+UDP eso es mucho más lento).
    # BUG REAL encontrado y corregido: OwningProcess de Get-NetTCPConnection
    # /Get-NetUDPEndpoint es [UInt32], pero Process.Id es [Int32] — una
    # hashtable indexada por Int32 NUNCA encuentra una clave UInt32 aunque
    # el valor numérico sea idéntico (.NET compara tipo + valor). Todo
    # salía "N/A" hasta forzar [int] en ambos lados. Confirmado empíricamente.
    # SEGUNDO BUG encontrado al verificar el primero: "$pid" es una
    # variable automática de solo lectura de PowerShell (el PID del propio
    # powershell.exe) — reasignarla lanza "Cannot overwrite variable PID
    # because it is read-only", terminaba en el catch y devolvía [] SIEMPRE.
    # Renombrada a "$idProceso".
    $procesos = @{}
    $rutas = @{}
    foreach ($p in (Get-Process -ErrorAction SilentlyContinue)) {
        $procesos[[int]$p.Id] = $p.ProcessName
        # .Path es una propiedad diferida: el error (proceso protegido/de
        # sistema sin permiso para leer su ruta) salta AQUÍ, al acceder,
        # no al llamar Get-Process — por eso el try/catch es por proceso.
        try { $rutas[[int]$p.Id] = $p.Path } catch { $rutas[[int]$p.Id] = "" }
    }

    # Antes solo se listaban conexiones 'Established' — los puertos en
    # escucha (Listen) desaparecían por completo, y es la señal más
    # relevante para detectar un servicio/backdoor no autorizado esperando
    # conexiones entrantes (lo que un "netstat -an" sí muestra).
    $tcp = @(Get-NetTCPConnection -ErrorAction SilentlyContinue |
        Where-Object { $_.State -in @('Established', 'Listen') } | ForEach-Object {
        $idProceso = [int]$_.OwningProcess
        [PSCustomObject]@{
            Protocolo = "TCP"
            DireccionLocal = $_.LocalAddress + ":" + $_.LocalPort
            DireccionRemota = $_.RemoteAddress + ":" + $_.RemotePort
            Estado = if ($_.State -eq 'Established') { "Establecida" } else { "Escuchando" }
            Proceso = if ($procesos.ContainsKey($idProceso)) { $procesos[$idProceso] } else { "N/A" }
            RutaEjecutable = if ($rutas.ContainsKey($idProceso)) { $rutas[$idProceso] } else { "" }
            PID = $idProceso
        }
    })

    # UDP no tiene "conexión" (protocolo sin estado): se listan los
    # endpoints locales a la escucha, sin dirección remota — antes esta
    # sección ignoraba UDP por completo.
    $udp = @(Get-NetUDPEndpoint -ErrorAction SilentlyContinue | ForEach-Object {
        $idProceso = [int]$_.OwningProcess
        [PSCustomObject]@{
            Protocolo = "UDP"
            DireccionLocal = $_.LocalAddress + ":" + $_.LocalPort
            DireccionRemota = "N/A"
            Estado = "Escuchando"
            Proceso = if ($procesos.ContainsKey($idProceso)) { $procesos[$idProceso] } else { "N/A" }
            RutaEjecutable = if ($rutas.ContainsKey($idProceso)) { $rutas[$idProceso] } else { "" }
            PID = $idProceso
        }
    })

    # @(...) fuerza array incluso con 0/1 resultado, y el "[]" explícito
    # evita que 0 conexiones emita 0 bytes por stdout (run_powershell_json
    # interpretaría eso como fallo, no como "0 conexiones").
    $conexiones = @($tcp) + @($udp)
    if ($conexiones.Count -eq 0) { Write-Output "[]" } else { $conexiones | ConvertTo-Json }
} catch {
    Write-Output "[]"
}
"""

PS_GET_NETWORK_ADAPTERS = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$adapters = @()

try {
    # Obtener adaptadores de red
    # -IncludeHidden: sin este flag, Get-NetAdapter excluye adaptadores que
    # Windows marca como "ocultos" (algunos físicos deshabilitados y varios
    # virtuales/túnel legítimos), que antes desaparecían de esta pestaña sin
    # ninguna forma de volver a mostrarlos.
    $allAdapters = Get-NetAdapter -IncludeHidden -ErrorAction SilentlyContinue

    if (-not $allAdapters) {
        # Si Get-NetAdapter falla, usar WMI como alternativa
        $wmiAdapters = Get-WmiObject Win32_NetworkAdapter -Filter "NetEnabled=True" -ErrorAction SilentlyContinue
        foreach ($wmi in $wmiAdapters) {
            $adapters += [PSCustomObject]@{
                Nombre = $wmi.NetConnectionID
                Descripcion = $wmi.Description
                Estado = if ($wmi.NetConnectionStatus -eq 2) { 'Activo' } else { 'Inactivo' }
                Tipo = 'Desconocido'
                IP = 'N/A'
                MAC = $wmi.MACAddress
                ConfiguracionIP = 'N/A'
                Gateway = 'N/A'
                DNS = 'N/A'
                IPv6 = 'N/A'
                Velocidad = if ($wmi.Speed) { "$([math]::Round($wmi.Speed/1000000)) Mbps" } else { 'N/A' }
            }
        }
    } else {
        foreach ($adapter in $allAdapters) {
            try {
                $adapterName = if ($adapter.Name) { $adapter.Name } else { 'Sin nombre' }
                $interfaceIndex = $adapter.ifIndex

                # Estado del adaptador
                $estado = switch ($adapter.Status) {
                    'Up' { 'Activo' }
                    'Down' { 'Inactivo' }
                    'Disabled' { 'Deshabilitado' }
                    'Disconnected' { 'Desconectado' }
                    'NotPresent' { 'No presente' }
                    'Not Present' { 'No presente' }
                    'LowerLayerDown' { 'Capa inferior inactiva' }
                    'Testing' { 'En pruebas' }
                    'Dormant' { 'Latente' }
                    default { 'Desconocido' }
                }

                # Tipo de adaptador
                $desc = $adapter.InterfaceDescription
                # 5.4.0: «break» en cada caso. Sin él, switch -Wildcard devuelve TODOS los casos que
                # encajan («Radmin VPN Ethernet Adapter» → Ethernet y VPN a la vez) y el filtro de la
                # tabla no lo reconocía: el adaptador no se veía. Orden: lo más concreto primero.
                $tipo = switch -Wildcard ($desc) {
                    '*VPN*' { 'VPN'; break }
                    '*TAP*' { 'VPN (TAP)'; break }
                    '*Tunnel*' { 'Túnel/VPN'; break }
                    '*Wireless*' { 'WiFi'; break }
                    '*Wi-Fi*' { 'WiFi'; break }
                    '*802.11*' { 'WiFi'; break }
                    '*Bluetooth*' { 'Bluetooth'; break }
                    '*Virtual*' { 'Virtual'; break }
                    '*Ethernet*' { 'Ethernet'; break }
                    default { 'Otro' }
                }

                # MAC Address
                $mac = if ($adapter.MacAddress) { $adapter.MacAddress } else { 'N/A' }

                # Velocidad de enlace
                $velocidad = if ($adapter.LinkSpeed) { $adapter.LinkSpeed } else { 'N/A' }

                # Obtener configuración IP — se prefiere una IP real (no
                # APIPA), pero si esa es la ÚNICA que tiene el adaptador se
                # muestra igualmente marcada como tal: antes se descartaba
                # sin más y quedaba como 'N/A', ocultando justo la señal de
                # que el DHCP falló (un adaptador activo con 169.254.x.x es
                # un diagnóstico habitual, no un dato a esconder).
                $ipAddress = 'N/A'
                $prefixLength = ''
                $ipCompleta = 'N/A'
                try {
                    $todasIPv4 = Get-NetIPAddress -InterfaceIndex $interfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue
                    $ipConfig = $todasIPv4 | Where-Object { $_.IPAddress -notlike '169.254.*' } | Select-Object -First 1
                    $esApipa = $false
                    if (-not $ipConfig) {
                        $ipConfig = $todasIPv4 | Where-Object { $_.IPAddress -like '169.254.*' } | Select-Object -First 1
                        $esApipa = $true
                    }
                    if ($ipConfig) {
                        $ipAddress = $ipConfig.IPAddress
                        $prefixLength = $ipConfig.PrefixLength
                        $ipCompleta = "$ipAddress/$prefixLength"
                        if ($esApipa) { $ipCompleta = "$ipCompleta (APIPA — sin DHCP)" }
                    }
                } catch {}

                # DHCP habilitado o IP fija
                $dhcpHabilitado = 'Fija'
                try {
                    $netIPInterface = Get-NetIPInterface -InterfaceIndex $interfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue
                    if ($netIPInterface -and $netIPInterface.Dhcp -eq 'Enabled') {
                        $dhcpHabilitado = 'DHCP'
                    }
                } catch {}

                # Gateway
                $gateway = 'N/A'
                try {
                    $route = Get-NetRoute -InterfaceIndex $interfaceIndex -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
                             Select-Object -First 1
                    if ($route -and $route.NextHop) {
                        $gateway = $route.NextHop
                    }
                } catch {}

                # DNS Servers
                $dns = 'N/A'
                try {
                    $dnsServers = Get-DnsClientServerAddress -InterfaceIndex $interfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue
                    if ($dnsServers -and $dnsServers.ServerAddresses -and $dnsServers.ServerAddresses.Count -gt 0) {
                        $dns = $dnsServers.ServerAddresses -join ', '
                    }
                } catch {}

                # IPv6
                $ipv6 = 'N/A'
                try {
                    $ipv6Config = Get-NetIPAddress -InterfaceIndex $interfaceIndex -AddressFamily IPv6 -ErrorAction SilentlyContinue |
                                  Where-Object { $_.PrefixOrigin -ne 'WellKnown' -and $_.AddressState -eq 'Preferred' } |
                                  Select-Object -First 1
                    if ($ipv6Config) {
                        $ipv6 = $ipv6Config.IPAddress
                    }
                } catch {}

                $adapters += [PSCustomObject]@{
                    Nombre = $adapterName
                    Descripcion = if ($adapter.InterfaceDescription) { $adapter.InterfaceDescription } else { 'Sin descripción' }
                    Estado = $estado
                    Tipo = $tipo
                    IP = $ipCompleta
                    MAC = $mac
                    ConfiguracionIP = $dhcpHabilitado
                    Gateway = $gateway
                    DNS = $dns
                    IPv6 = $ipv6
                    Velocidad = $velocidad
                }
            } catch {
                # Si falla un adaptador individual, continuar con el siguiente
                continue
            }
        }
    }
} catch {
    # Si todo falla, devolver array vacío
}

if ($adapters.Count -eq 0) {
    # "@() | ConvertTo-Json" (con pipe) NO emite nada por stdout — el
    # pipeline enumera el array vacío a 0 objetos antes de que
    # ConvertTo-Json reciba ninguno. Confirmado empíricamente: solo
    # "ConvertTo-Json -InputObject @()" o un literal "[]" preservan la
    # estructura vacía como JSON válido.
    Write-Output "[]"
} elseif ($adapters.Count -eq 1) {
    @($adapters) | ConvertTo-Json
} else {
    $adapters | ConvertTo-Json
}
"""

PS_GET_INSTALLED_SOFTWARE = r"""
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8

    $keys = @(
        'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\Software\Wow6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )
    $seen = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    $result = [System.Collections.Generic.List[object]]::new()
    foreach ($key in $keys) {
        try {
            Get-ItemProperty $key -ErrorAction SilentlyContinue |
            Where-Object { $_.DisplayName -and $seen.Add([string]$_.DisplayName) } |
            ForEach-Object {
                $fecha = 'N/A'
                if ($_.InstallDate) {
                    try { $fecha = [datetime]::ParseExact([string]$_.InstallDate,'yyyyMMdd',$null).ToString('dd/MM/yyyy') }
                    catch { $fecha = [string]$_.InstallDate }
                }
                $result.Add([PSCustomObject]@{
                    Nombre             = [string]$_.DisplayName
                    Version            = if ($_.DisplayVersion) { [string]$_.DisplayVersion } else { 'N/A' }
                    Editor             = if ($_.Publisher) { [string]$_.Publisher } else { 'N/A' }
                    FechaInstalacion   = $fecha
                    CarpetaInstalacion = if ($_.InstallLocation) { [string]$_.InstallLocation } else { 'N/A' }
                    UninstallString    = if ($_.UninstallString) { [string]$_.UninstallString } else { '' }
                    SystemComponent    = ($_.SystemComponent -eq 1)
                })
                if ($result.Count -ge 5000) { break }
            }
        } catch {}
        if ($result.Count -ge 5000) { break }
    }
    if ($result.Count -gt 0) { $result | ConvertTo-Json -Depth 2 -Compress } else { '[]' }
} catch { '[]' }
"""

PS_GET_SCHEDULED_TASKS = r"""
try {
    # SIN el 'Where-Object State -ne Disabled' que había antes: una tarea
    # deshabilitada también es relevante para un técnico (p. ej. malware
    # que deshabilitó una tarea legítima, o una tarea maliciosa que se
    # reactivará más tarde) — se filtra en la UI con un checkbox, no se
    # oculta por defecto en el origen de datos.
    $tareas = @(Get-ScheduledTask | ForEach-Object {
        # Por objeto, no por nombre: -TaskName admite comodines y una tarea con corchetes en el nombre recibía la
        # última ejecución y el resultado de OTRA (o ninguno). Mismo dato y algo más rápido (comprobado).
        $info = $_ | Get-ScheduledTaskInfo -ErrorAction SilentlyContinue
        $proximaEjecucion = "N/A"
        if ($info.NextRunTime) {
            $proximaEjecucion = $info.NextRunTime.ToString("dd/MM/yyyy HH:mm:ss")
        }

        # Códigos de LastTaskResult que NO son un error real (0x0004130x) —
        # antes se mostraban todos como "Error: <código>", incluidas
        # tareas simplemente en ejecución o que aún no se habían lanzado.
        $resultado = $info.LastTaskResult
        $ultimoResultado = switch ($resultado) {
            0 { "Exitoso" }
            267009 { "Ejecutándose" }
            267011 { "Aún no se ha ejecutado" }
            267014 { "Terminada por el usuario" }
            $null { "N/A" }
            default { "Error: $resultado" }
        }

        $acciones = @()
        $ejecutablePrincipal = ""
        foreach ($a in $_.Actions) {
            if ($a.Execute) {
                if (-not $ejecutablePrincipal) { $ejecutablePrincipal = $a.Execute }
                $acciones += ("$($a.Execute) $($a.Arguments)").Trim()
            }
        }

        # Fecha de registro de la tarea (RegistrationInfo.Date) — no todas
        # la tienen (muchas tareas de Microsoft la traen vacía), pero cuando
        # existe es una señal útil: una tarea creada hace muy pocos días es
        # más sospechosa que una "oculta" de toda la vida.
        $fechaCreacion = "N/A"
        if ($_.RegistrationInfo -and $_.RegistrationInfo.Date) {
            try { $fechaCreacion = ([datetime]$_.RegistrationInfo.Date).ToString("dd/MM/yyyy") } catch {}
        }

        # Desencadenadores (Triggers): se traen en crudo (clase CIM, hora,
        # bitmask de días, repetición) y se traducen a español en Python
        # (msys/models/rows.py), igual que ya se hace con los perfiles de
        # firewall — así el valor original queda disponible sin tocar por si
        # hiciera falta para algo más adelante.
        $disparadores = @()
        foreach ($tr in $_.Triggers) {
            try {
                $clase = $tr.CimClass.CimClassName
                $horaStr = $null
                if ($tr.StartBoundary) {
                    try { $horaStr = ([datetime]$tr.StartBoundary).ToString("HH:mm") } catch {}
                }
                $fechaUnica = $null
                if ($clase -eq "MSFT_TaskTimeTrigger" -and $tr.StartBoundary) {
                    try { $fechaUnica = ([datetime]$tr.StartBoundary).ToString("dd/MM/yyyy") } catch {}
                }
                $diasBitmask = $null
                if ($null -ne $tr.DaysOfWeek) { $diasBitmask = [int]$tr.DaysOfWeek }
                $repiteCadaMin = $null
                $repiteDuranteMin = $null
                if ($tr.Repetition) {
                    if ($tr.Repetition.Interval) {
                        try { $repiteCadaMin = [int]([System.Xml.XmlConvert]::ToTimeSpan($tr.Repetition.Interval).TotalMinutes) } catch {}
                    }
                    if ($tr.Repetition.Duration) {
                        try { $repiteDuranteMin = [int]([System.Xml.XmlConvert]::ToTimeSpan($tr.Repetition.Duration).TotalMinutes) } catch {}
                    }
                }
                $disparadores += [PSCustomObject]@{
                    Clase = $clase
                    Habilitado = if ($null -ne $tr.Enabled) { [bool]$tr.Enabled } else { $true }
                    Hora = $horaStr
                    FechaUnica = $fechaUnica
                    DiasSemanaBitmask = $diasBitmask
                    RepiteCadaMin = $repiteCadaMin
                    RepiteDuranteMin = $repiteDuranteMin
                }
            } catch {}
        }

        [PSCustomObject]@{
            Nombre = $_.TaskName
            Ruta = $_.TaskPath
            Estado = switch ($_.State) {
                "Ready" { "Lista" }
                "Running" { "Ejecutando" }
                "Disabled" { "Deshabilitada" }
                default { $_.State }
            }
            ProximaEjecucion = $proximaEjecucion
            UltimaEjecucion = if ($info.LastRunTime) { $info.LastRunTime.ToString("dd/MM/yyyy HH:mm:ss") } else { "N/A" }
            UltimoResultado = $ultimoResultado
            EjecutaComo = $_.Principal.UserId
            Oculta = [bool]$_.Settings.Hidden
            Autor = $_.Author
            Accion = ($acciones -join " | ")
            EjecutablePrincipal = $ejecutablePrincipal
            FechaCreacion = $fechaCreacion
            Disparadores = $disparadores
        }
    })
    if ($tareas.Count -eq 0) { Write-Output "[]" } else { $tareas | ConvertTo-Json -Depth 4 }
} catch {
    Write-Output "[]"
}
"""

PS_GET_PRINTERS = r"""
try {
    $impresoras = @(Get-Printer | ForEach-Object {
        [PSCustomObject]@{
            Nombre = $_.Name
            Tipo = switch ($_.Type) {
                "Local" { "Local" }
                "Connection" { "Red" }
                default { $_.Type }
            }
            Puerto = $_.PortName
            Estado = switch ($_.PrinterStatus) {
                "Normal" { "Normal" }
                "Offline" { "Desconectada" }
                "Error" { "Error" }
                "Paused" { "Pausada" }
                default { $_.PrinterStatus }
            }
            Compartida = if ($_.Shared) { "Si" } else { "No" }
            Predeterminada = if ($_.Name -eq (Get-WmiObject -Class Win32_Printer -Filter "Default=True" -ErrorAction SilentlyContinue).Name) { "Si" } else { "No" }
        }
    })
    if ($impresoras.Count -eq 0) { Write-Output "[]" } else { $impresoras | ConvertTo-Json }
} catch {
    # Fallback para versiones antiguas de Windows
    $impresoras = @(Get-WmiObject -Class Win32_Printer | ForEach-Object {
        [PSCustomObject]@{
            Nombre = $_.Name
            Tipo = if ($_.Network) { "Red" } else { "Local" }
            Puerto = $_.PortName
            Estado = if ($_.WorkOffline) { "Desconectada" } else { "Normal" }
            Compartida = if ($_.Shared) { "Si" } else { "No" }
            Predeterminada = if ($_.Default) { "Si" } else { "No" }
        }
    })
    if ($impresoras.Count -eq 0) { Write-Output "[]" } else { $impresoras | ConvertTo-Json }
}
"""

PS_GET_FIREWALL_RULES = r"""
# BUG CORREGIDO: la versión anterior parseaba el texto de
# "netsh advfirewall firewall show rule name=all" buscando literales en
# inglés ("Rule Name:", "Direction:", "Action:", "Protocol:", "Profiles:")
# pero netsh devuelve ese texto LOCALIZADO según el idioma de Windows — en
# español son "Nombre de regla:", "Dirección:", "Acción:", "Protocolo:",
# "Perfiles:". El regex nunca coincidía, así que esta sección devolvía
# SIEMPRE 0 reglas en cualquier Windows no-inglés, pese a haber cientos de
# reglas reales. Get-NetFirewallRule usa nombres de propiedad .NET, no
# texto localizado, así que funciona igual en cualquier idioma.
$salida = @{ Reglas = @(); Perfiles = @() }
try {
    $reglas = Get-NetFirewallRule -ErrorAction Stop

    $filtrosPuerto = @{}
    foreach ($f in (Get-NetFirewallPortFilter -ErrorAction SilentlyContinue)) {
        $filtrosPuerto[$f.InstanceID] = $f
    }
    # Programa (.exe) al que se restringe la regla, si tiene uno — antes no
    # se mostraba, y saber que una regla "Any/Any Entrada Permitir" está
    # atada a un ejecutable concreto en una carpeta rara es mucho más
    # accionable que solo el nombre de la regla.
    $filtrosPrograma = @{}
    foreach ($f in (Get-NetFirewallApplicationFilter -ErrorAction SilentlyContinue)) {
        $filtrosPrograma[$f.InstanceID] = $f
    }

    # 5.4.0: sin el tope de 500 (de 649 reglas, 149 no salían nunca: ni en la tabla, ni en los
    # hallazgos, ni en los informes). Los filtros ya se leen de una vez arriba: el coste es pequeño.
    $resultado = foreach ($r in ($reglas | Select-Object -First 5000)) {
        $filtro = $filtrosPuerto[$r.InstanceID]
        $protocolo = if ($filtro -and $filtro.Protocol -and $filtro.Protocol -ne 'Any') { $filtro.Protocol } else { 'N/A' }
        $puertoTexto = if ($filtro) { ($filtro.LocalPort -join ',') } else { '' }
        $puerto = if ($puertoTexto -and $puertoTexto -ne 'Any') { $puertoTexto } else { 'N/A' }
        $filtroPrograma = $filtrosPrograma[$r.InstanceID]
        $programa = if ($filtroPrograma -and $filtroPrograma.Program -and $filtroPrograma.Program -ne 'Any') {
            $filtroPrograma.Program
        } else { 'N/A' }

        [PSCustomObject]@{
            Nombre = $r.DisplayName
            Direccion = if ($r.Direction -eq 'Inbound') { 'Entrada' } else { 'Salida' }
            Accion = if ($r.Action -eq 'Allow') { 'Permitir' } else { 'Bloquear' }
            Protocolo = $protocolo
            Puerto = $puerto
            Perfil = [string]$r.Profile
            Programa = $programa
            Habilitada = if ($r.Enabled -eq 'True' -or $r.Enabled -eq $true) { 'Sí' } else { 'No' }
            NombreInterno = $r.Name
            # Group con prefijo "@" es una cadena de recurso indirecta que
            # apunta a una DLL del propio Windows (p. ej. "@FirewallAPI.dll,
            # -12345") — así se registran SIEMPRE las reglas de Windows.
            # Reglas de terceros (AnyDesk, Chrome, AOMEI...) usan texto
            # plano o van sin Group. Verificado empíricamente: 513/640
            # reglas reales de este equipo llevan ese prefijo.
            EsSistema = [bool]($r.Group -like '@*')
        }
    }

    $salida.Reglas = @($resultado)
} catch {}

# Estado del Firewall por perfil (Dominio/Privado/Público) — el control más
# importante de todos: si un perfil está desactivado, ninguna regla de ese
# perfil importa. Antes esta pestaña no lo comprobaba en absoluto.
try {
    $salida.Perfiles = @(Get-NetFirewallProfile -ErrorAction SilentlyContinue | ForEach-Object {
        [PSCustomObject]@{
            Nombre = [string]$_.Name
            Habilitado = [bool]($_.Enabled -eq 'True' -or $_.Enabled -eq $true)
        }
    })
} catch {}

$salida | ConvertTo-Json -Depth 4
"""

# Comprueba el estado LOCAL (TCP y UDP) de un puerto + qué reglas de
# firewall lo mencionan. Espera que la variable $puerto YA esté definida
# ANTES de este bloque (Python antepone "$puerto = <entero validado>\n" —
# nunca .format()/interpolación de plantilla aquí: el script tiene muchos
# bloques { } propios de PowerShell que chocarían con placeholders al
# estilo str.format(), y el puerto ya llega validado como int en Python,
# así que no hace falta el mecanismo de escapado de run_powershell_json_params).
PS_COMPROBAR_PUERTO = r"""
$resultado = @{ TCP = $null; UDP = $null; ReglasFirewall = @() }

try {
    $procesos = @{}
    foreach ($p in (Get-Process -ErrorAction SilentlyContinue)) { $procesos[[int]$p.Id] = $p.ProcessName }

    $tcpConn = Get-NetTCPConnection -LocalPort $puerto -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($tcpConn) {
        $idProceso = [int]$tcpConn.OwningProcess
        $resultado.TCP = [PSCustomObject]@{
            Escuchando = $true
            Proceso = if ($procesos.ContainsKey($idProceso)) { $procesos[$idProceso] } else { "N/A" }
            PID = $idProceso
        }
    } else {
        $resultado.TCP = [PSCustomObject]@{ Escuchando = $false }
    }

    $udpConn = Get-NetUDPEndpoint -LocalPort $puerto -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($udpConn) {
        $idProceso = [int]$udpConn.OwningProcess
        $resultado.UDP = [PSCustomObject]@{
            Escuchando = $true
            Proceso = if ($procesos.ContainsKey($idProceso)) { $procesos[$idProceso] } else { "N/A" }
            PID = $idProceso
        }
    } else {
        $resultado.UDP = [PSCustomObject]@{ Escuchando = $false }
    }
} catch {}

try {
    $filtrosCoincidentes = Get-NetFirewallPortFilter -ErrorAction SilentlyContinue | Where-Object {
        ("$($_.LocalPort)" -eq "$puerto")
    }
    $idsCoincidentes = @($filtrosCoincidentes | Select-Object -ExpandProperty InstanceID)
    if ($idsCoincidentes.Count -gt 0) {
        $reglas = Get-NetFirewallRule -ErrorAction SilentlyContinue | Where-Object { $_.InstanceID -in $idsCoincidentes }
        $resultado.ReglasFirewall = @($reglas | ForEach-Object {
            [PSCustomObject]@{
                Nombre = $_.DisplayName
                Direccion = if ($_.Direction -eq 'Inbound') { 'Entrada' } else { 'Salida' }
                Accion = if ($_.Action -eq 'Allow') { 'Permitir' } else { 'Bloquear' }
                Habilitada = if ($_.Enabled -eq 'True' -or $_.Enabled -eq $true) { 'Sí' } else { 'No' }
            }
        })
    }
} catch {}

$resultado | ConvertTo-Json -Depth 4
"""

# REMOVED - Browser Passwords (can't decrypt actual passwords)
# PS_GET_BROWSER_PASSWORDS = r"""
# [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
# $OutputEncoding = [System.Text.Encoding]::UTF8
#
# $passwords = @()
#
# $chromePath = "$env:LOCALAPPDATA\Google\Chrome\User Data\Default\Login Data"
# if (Test-Path $chromePath) {
#     $passwords += [PSCustomObject]@{
#         Navegador = 'Chrome'
#         URL = 'Base de datos encontrada'
#         Usuario = 'Ver en chrome://settings/passwords'
#         Contrasena = '***'
#         Nota = 'Archivo: ' + $chromePath
#     }
# }
#
# $edgePath = "$env:LOCALAPPDATA\Microsoft\Edge\User Data\Default\Login Data"
# if (Test-Path $edgePath) {
#     $passwords += [PSCustomObject]@{
#         Navegador = 'Edge'
#         URL = 'Base de datos encontrada'
#         Usuario = 'Ver en edge://settings/passwords'
#         Contrasena = '***'
#         Nota = 'Archivo: ' + $edgePath
#     }
# }
#
# if ($passwords.Count -eq 0) {
#     $passwords += [PSCustomObject]@{
#         Navegador = 'Ninguno'
#         URL = 'N/A'
#         Usuario = 'N/A'
#         Contrasena = 'N/A'
#         Nota = 'No se encontraron bases de datos de navegadores'
#     }
# }
#
# $passwords | ConvertTo-Json
# """

PS_GET_EMAIL_ACCOUNTS = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$accounts = @()

# ============================================
# 1. OUTLOOK - Cuentas de correo configuradas
# ============================================
$outlookVersions = @('16.0', '15.0', '14.0')

foreach ($version in $outlookVersions) {
    $basePath = "HKCU:\Software\Microsoft\Office\$version\Outlook\Profiles"
    if (-not (Test-Path $basePath)) { continue }

    $profiles = Get-ChildItem $basePath -ErrorAction SilentlyContinue
    foreach ($profile in $profiles) {
        $accountPath = "$($profile.PSPath)\9375CFF0413111d3B88A00104B2A6676"
        if (-not (Test-Path $accountPath)) { continue }

        Get-ChildItem $accountPath -ErrorAction SilentlyContinue | ForEach-Object {
            $props = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
            $email = $props.'Account Name'
            $displayName = $props.'Display Name'

            if (-not $email) { return }

            # Skip Outlook address books (Libreta de direcciones)
            if ($displayName -match 'Outlook Address Book|Libreta de direcciones|Address Book') {
                return
            }

            # Skip if email doesn't contain @
            if ($email -notmatch '@') {
                return
            }

            # Determine account type - ORDEN MEJORADO DE DETECCIÓN
            $tipo = 'Desconocido'
            $server = 'N/A'
            $clienteName = 'Outlook'

            # Determinar versión de Outlook
            if ($version -eq '16.0') { $clienteName = 'Outlook 2016/2019/365' }
            elseif ($version -eq '15.0') { $clienteName = 'Outlook 2013' }
            elseif ($version -eq '14.0') { $clienteName = 'Outlook 2010' }

            # Obtener todas las propiedades para análisis
            $serviceName = $props.'Service Name'
            $serviceUID = $props.'Service UID'
            $miniUID = $props.'Mini UID'
            $accountType = $props.'Account Type'
            $pop3Server = $props.'POP3 Server'
            $imapServer = $props.'IMAP Server'
            $smtpServer = $props.'SMTP Server'
            $exchangeServer = $props.'Exchange Server'

            # Detalle completo por protocolo, para "Ver configuración" y
            # los detectores de Soporte (autenticación básica, sin SSL...)
            $imapPort = if ($props.'IMAP Port') { $props.'IMAP Port' } else { 0 }
            $imapSSL = [bool]($props.'IMAP Use SSL')
            $popPort = if ($props.'POP3 Port') { $props.'POP3 Port' } else { 0 }
            $popSSL = [bool]($props.'POP3 Use SSL')
            $smtpPort = if ($props.'SMTP Port') { $props.'SMTP Port' } else { 0 }
            $smtpSSL = [bool]($props.'SMTP Use SSL') -or [bool]($props.'SMTP Secure Connection')

            # PRIORIDAD 1: servidor IMAP/POP3 explícito — señal directa y
            # fiable del protocolo real, tiene que ir ANTES que cualquier
            # heurística de "esto huele a Exchange". FIX: antes "Mini UID"
            # (prioridad 3 original) se comprobaba primero, y el asistente
            # moderno de cuentas de Outlook asigna Mini UID a CUALQUIER
            # cuenta (también IMAP) — no solo a Exchange/Office 365. Una
            # cuenta Gmail real, con IMAP Server=imap.gmail.com explícito,
            # se detectaba como "Exchange (Office 365)" solo por tener
            # Mini UID, sin haber llegado nunca a mirar el IMAP Server.
            if ($imapServer) {
                $tipo = 'IMAP'
                $server = $imapServer
            }
            elseif ($pop3Server) {
                $tipo = 'POP3'
                $server = $pop3Server
            }
            # PRIORIDAD 2: Detectar Exchange por Service Name (más confiable)
            # "msems" indica Exchange con Modern Auth (Office 365)
            elseif ($serviceName -match 'msems|MSEMS') {
                $tipo = 'Exchange (Office 365 Modern Auth)'
                $server = if ($smtpServer) { $smtpServer }
                         elseif ($exchangeServer) { $exchangeServer }
                         else { 'outlook.office365.com' }
            }
            # PRIORIDAD 3: Exchange Server tradicional (MAPI)
            elseif ($exchangeServer -or $props.PSObject.Properties.Name -contains 'Exchange Server') {
                $tipo = 'Exchange (MAPI)'
                $server = if ($exchangeServer) { $exchangeServer } else { 'Exchange Server' }
            }
            # PRIORIDAD 4: Mini UID presente (y nada de lo anterior aplicó)
            # indica cuenta moderna de Office 365/Exchange sin servidor
            # explícito en el registro.
            elseif ($miniUID) {
                $tipo = 'Exchange (Office 365)'
                $server = if ($smtpServer) { $smtpServer } else { 'outlook.office365.com' }
            }
            # PRIORIDAD 5: Service Name "UNICODENB" puede ser Exchange
            elseif ($serviceName -match 'UNICODENB') {
                # Verificar si tiene propiedades de Exchange
                if ($props.PSObject.Properties.Name -match 'Exchange|MAPI') {
                    $tipo = 'Exchange'
                    $server = if ($exchangeServer) { $exchangeServer }
                             elseif ($smtpServer) { $smtpServer }
                             else { 'Exchange Server' }
                } else {
                    $tipo = 'MAPI/Exchange'
                    $server = if ($smtpServer) { $smtpServer } else { 'N/A' }
                }
            }
            # PRIORIDAD 6: EAS (Exchange ActiveSync)
            elseif ($props.PSObject.Properties.Name -match 'EAS|ActiveSync') {
                $tipo = 'Exchange ActiveSync'
                $server = if ($smtpServer) { $smtpServer } else { 'outlook.office365.com' }
            }
            # PRIORIDAD 7: Inferir por SMTP server
            elseif ($smtpServer -match 'outlook\.office365|smtp\.office365|smtp-mail\.outlook') {
                $tipo = 'Exchange (Office 365)'
                $server = $smtpServer
            }
            # PRIORIDAD 8: Inferir por dominio de email
            elseif ($email -match '@(outlook|hotmail|live)\.') {
                $tipo = 'Outlook.com (IMAP)'
                $server = 'outlook.office365.com'
            }
            # PRIORIDAD 9: Si hay SMTP, intentar inferir
            elseif ($smtpServer) {
                $server = $smtpServer
                if ($smtpServer -match 'exchange') {
                    $tipo = 'Exchange'
                } elseif ($smtpServer -match 'gmail') {
                    $tipo = 'IMAP (Gmail)'
                } else {
                    $tipo = 'SMTP/IMAP'
                }
            }
            # DEFAULT: Si no se pudo determinar
            else {
                $tipo = 'No determinado'
                $server = 'N/A'
            }

            $accounts += [PSCustomObject]@{
                Cliente = $clienteName
                Cuenta = $email
                Servidor = $server
                Tipo = $tipo
                ImapHost = if ($imapServer) { $imapServer } else { "" }
                ImapPort = $imapPort
                ImapSSL = $imapSSL
                PopHost = if ($pop3Server) { $pop3Server } else { "" }
                PopPort = $popPort
                PopSSL = $popSSL
                SmtpHost = if ($smtpServer) { $smtpServer } else { "" }
                SmtpPort = $smtpPort
                SmtpSSL = $smtpSSL
            }
        }
    }
}

# ============================================
# 2. THUNDERBIRD - Cuentas de correo
# ============================================
$thunderbirdPath = "$env:APPDATA\Thunderbird\Profiles"
if (Test-Path $thunderbirdPath) {
    $profiles = Get-ChildItem $thunderbirdPath -Directory -ErrorAction SilentlyContinue
    foreach ($profile in $profiles) {
        $prefsFile = Join-Path $profile.FullName "prefs.js"
        if (Test-Path $prefsFile) {
            $prefsContent = Get-Content $prefsFile -Raw -ErrorAction SilentlyContinue

            # Buscar servidores configurados
            $serverMatches = [regex]::Matches($prefsContent, 'user_pref\("mail\.server\.([^"]+)\.type",\s*"([^"]+)"\)')
            $serverIds = @{}

            foreach ($match in $serverMatches) {
                $serverId = $match.Groups[1].Value
                $serverType = $match.Groups[2].Value
                $serverIds[$serverId] = $serverType
            }

            # Para cada servidor, obtener email y configuración
            foreach ($serverId in $serverIds.Keys) {
                $serverType = $serverIds[$serverId]

                # Buscar email (userName)
                $emailMatch = [regex]::Match($prefsContent, "user_pref\(`"mail\.server\.$serverId\.userName`",\s*`"([^`"]+)`"\)")
                if (-not $emailMatch.Success) { continue }
                $email = $emailMatch.Groups[1].Value

                if ($email -notmatch '@') { continue }

                # Buscar hostname
                $hostnameMatch = [regex]::Match($prefsContent, "user_pref\(`"mail\.server\.$serverId\.hostname`",\s*`"([^`"]+)`"\)")
                $server = if ($hostnameMatch.Success) { $hostnameMatch.Groups[1].Value } else { 'N/A' }

                # Determinar tipo de cuenta
                $tipo = switch ($serverType) {
                    'imap' { 'IMAP' }
                    'pop3' { 'POP3' }
                    'nntp' { 'NNTP (News)' }
                    'none' { 'Local' }
                    default { $serverType.ToUpper() }
                }

                $accounts += [PSCustomObject]@{
                    Cliente = 'Thunderbird'
                    Cuenta = $email
                    Servidor = $server
                    Tipo = $tipo
                    ImapHost = if ($tipo -eq 'IMAP') { $server } else { "" }
                    ImapPort = 0
                    ImapSSL = $false
                    PopHost = if ($tipo -eq 'POP3') { $server } else { "" }
                    PopPort = 0
                    PopSSL = $false
                    SmtpHost = ""
                    SmtpPort = 0
                    SmtpSSL = $false
                }
            }
        }
    }
}

# ============================================
# 3. RESULTADO FINAL
# ============================================

# Eliminar duplicados basándose en la combinación de Cuenta + Tipo
# Mantener la primera ocurrencia (generalmente la más específica)
$uniqueAccounts = @()
$seen = @{}

foreach ($account in $accounts) {
    $key = "$($account.Cuenta)|$($account.Tipo)"

    if (-not $seen.ContainsKey($key)) {
        $uniqueAccounts += $account
        $seen[$key] = $true
    }
}

# "@() | ConvertTo-Json" (con pipe) no emite nada por stdout con 0
# elementos — mismo patrón ya establecido en el resto del proyecto.
if ($uniqueAccounts.Count -eq 0) {
    Write-Output "[]"
} elseif ($uniqueAccounts.Count -eq 1) {
    @($uniqueAccounts) | ConvertTo-Json
} else {
    $uniqueAccounts | ConvertTo-Json
}
"""

# Extraído de PS_GET_EMAIL_ACCOUNTS (secciones "3. FIREFOX", "4. CHROME/EDGE"
# y "5.6 OneDrive" del script original) — esto es sobre navegadores y
# almacenamiento en la nube, no sobre correo, así que vive en su propia
# pestaña "☁️ Navegadores y Nube", no mezclado con Outlook/Thunderbird.
PS_GET_BROWSER_CLOUD_ACCOUNTS = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$accounts = @()

# ============================================
# 1. FIREFOX - Cuentas guardadas en el navegador
# ============================================
$firefoxPath = "$env:APPDATA\Mozilla\Firefox\Profiles"
if (Test-Path $firefoxPath) {
    $profiles = Get-ChildItem $firefoxPath -Directory -ErrorAction SilentlyContinue
    foreach ($profile in $profiles) {
        # Buscar en logins.json (contraseñas guardadas)
        $loginsFile = Join-Path $profile.FullName "logins.json"
        if (Test-Path $loginsFile) {
            try {
                $loginsData = Get-Content $loginsFile -Raw | ConvertFrom-Json -ErrorAction SilentlyContinue
                if ($loginsData.logins) {
                    foreach ($login in $loginsData.logins) {
                        $hostname = $login.hostname
                        $username = $login.username

                        # Filtrar solo si parece un email
                        if ($username -match '@') {
                            $tipo = 'Login guardado (Firefox)'

                            # Intentar detectar el tipo de servicio
                            if ($hostname -match 'outlook|office365') { $tipo = 'Outlook.com (Firefox)' }
                            elseif ($hostname -match 'gmail|google') { $tipo = 'Gmail (Firefox)' }
                            elseif ($hostname -match 'yahoo') { $tipo = 'Yahoo (Firefox)' }

                            $accounts += [PSCustomObject]@{
                                Cliente = 'Firefox'
                                Cuenta = $username
                                Servidor = $hostname
                                Tipo = $tipo
                            }
                        }
                    }
                }
            } catch {}
        }
    }
}

# ============================================
# 2. CHROME / EDGE - Cuentas en navegadores Chromium
# ============================================
$chromiumBrowsers = @(
    @{Name='Chrome'; FullName='Google Chrome'; BasePath="$env:LOCALAPPDATA\Google\Chrome\User Data"},
    @{Name='Edge'; FullName='Microsoft Edge'; BasePath="$env:LOCALAPPDATA\Microsoft\Edge\User Data"}
)

foreach ($browser in $chromiumBrowsers) {
    $userDataPath = $browser.BasePath
    if (-not (Test-Path $userDataPath)) { continue }

    $browserShortName = $browser.Name
    $browserFullName = $browser.FullName

    # Buscar TODOS los perfiles (Default, Profile 1, Profile 2, etc.)
    $profilePaths = @()

    # Agregar Default si existe
    $defaultPath = Join-Path $userDataPath "Default"
    if (Test-Path $defaultPath) {
        $profilePaths += @{Path=$defaultPath; Name='Default'}
    }

    # Buscar Profile 1, Profile 2, Profile 3, etc.
    Get-ChildItem $userDataPath -Directory -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '^Profile \d+$' } | ForEach-Object {
        $profilePaths += @{Path=$_.FullName; Name=$_.Name}
    }

    # Procesar cada perfil encontrado
    foreach ($profile in $profilePaths) {
        $prefsPath = Join-Path $profile.Path "Preferences"
        $localStatePath = Join-Path $profile.Path "Local State"

        if (Test-Path $prefsPath) {
            try {
                $prefs = Get-Content $prefsPath -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json -ErrorAction SilentlyContinue
                if (-not $prefs) { continue }

                $profileName = $profile.Name

                # Método 1: account_info (Chrome/Edge cuenta sincronizada)
                if ($prefs.account_info) {
                    $prefs.account_info.PSObject.Properties | ForEach-Object {
                        $accountData = $_.Value
                        if ($accountData.email) {
                            $tipo = "Sincronizado - $profileName ($browserShortName)"

                            $accounts += [PSCustomObject]@{
                                Cliente = $browserShortName
                                Cuenta = $accountData.email
                                Servidor = 'Cuenta Google/Microsoft'
                                Tipo = $tipo
                            }
                        }
                    }
                }

                # Método 2: signin (cuentas de inicio de sesión)
                if ($prefs.signin) {
                    if ($prefs.signin.allowed_on_next_startup) {
                        $emailMatch = [regex]::Match($prefs.signin.allowed_on_next_startup.ToString(), '[^@]+@[^@]+\.[^@]+')
                        if ($emailMatch.Success) {
                            $tipo = "Sign-in - $profileName ($browserShortName)"

                            $accounts += [PSCustomObject]@{
                                Cliente = $browserShortName
                                Cuenta = $emailMatch.Value
                                Servidor = 'Navegador'
                                Tipo = $tipo
                            }
                        }
                    }
                }

                # Método 3: profile info (información del perfil)
                if ($prefs.profile) {
                    $profileInfo = $prefs.profile
                    # Algunas versiones guardan el email aquí
                    if ($profileInfo.user_name -and $profileInfo.user_name -match '@') {
                        $tipo = "Perfil - $profileName ($browserShortName)"

                        $accounts += [PSCustomObject]@{
                            Cliente = $browserShortName
                            Cuenta = $profileInfo.user_name
                            Servidor = 'Perfil del navegador'
                            Tipo = $tipo
                        }
                    }
                    if ($profileInfo.email -and $profileInfo.email -match '@') {
                        $tipo = "Perfil - $profileName ($browserShortName)"

                        $accounts += [PSCustomObject]@{
                            Cliente = $browserShortName
                            Cuenta = $profileInfo.email
                            Servidor = 'Perfil del navegador'
                            Tipo = $tipo
                        }
                    }
                }

                # Método 4: Buscar en autofill/passwords hint
                if ($prefs.autofill -and $prefs.autofill.profile_enabled) {
                    # Algunos navegadores guardan emails en autofill
                    if ($prefs.autofill.profiles) {
                        $prefs.autofill.profiles.PSObject.Properties | ForEach-Object {
                            $profileData = $_.Value
                            if ($profileData.email -and $profileData.email -match '@') {
                                $tipo = "Autofill - $profileName ($browserShortName)"

                                $accounts += [PSCustomObject]@{
                                    Cliente = $browserShortName
                                    Cuenta = $profileData.email
                                    Servidor = 'Autocompletado'
                                    Tipo = $tipo
                                }
                            }
                        }
                    }
                }
            } catch {}
        }
    }

    # Buscar en Local State (nivel de navegador, no de perfil)
    $localStatePath = Join-Path $userDataPath "Local State"
    if (Test-Path $localStatePath) {
        try {
            $localState = Get-Content $localStatePath -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json -ErrorAction SilentlyContinue
            if ($localState -and $localState.profile) {
                if ($localState.profile.info_cache) {
                    $localState.profile.info_cache.PSObject.Properties | ForEach-Object {
                        $profileData = $_.Value
                        if ($profileData.user_name -and $profileData.user_name -match '@') {
                            $tipo = "Local State ($browserShortName)"

                            $accounts += [PSCustomObject]@{
                                Cliente = $browserShortName
                                Cuenta = $profileData.user_name
                                Servidor = 'Navegador'
                                Tipo = $tipo
                            }
                        }
                        if ($profileData.gaia_name -and $profileData.gaia_name -match '@') {
                            $tipo = "Google Account ($browserShortName)"

                            $accounts += [PSCustomObject]@{
                                Cliente = $browserShortName
                                Cuenta = $profileData.gaia_name
                                Servidor = 'Google Account'
                                Tipo = $tipo
                            }
                        }
                    }
                }
            }
        } catch {}
    }
}

# ============================================
# 3. ONEDRIVE - Cuenta(s) de almacenamiento en la nube asociada(s)
# ============================================
# FIX: antes solo se comprobaban Business1 y Personal a mano — un usuario
# con varias cuentas profesionales (Business2, Business3...) se quedaba
# sin detectar salvo la primera. Se enumeran TODAS las subclaves reales.
$oneDriveAccountsPath = 'HKCU:\Software\Microsoft\OneDrive\Accounts'
if (Test-Path $oneDriveAccountsPath) {
    Get-ChildItem $oneDriveAccountsPath -ErrorAction SilentlyContinue |
        Where-Object { $_.PSChildName -match '^(Personal|Business\d*)$' } |
        ForEach-Object {
            $props = Get-ItemProperty $_.PSPath -ErrorAction SilentlyContinue
            $email = $props.UserEmail
            if (-not $email) { $email = $props.UserFolder }

            if ($email -and $email -match '@') {
                $accountType = if ($_.PSChildName -match '^Business') { 'OneDrive for Business' } else { 'OneDrive Personal' }

                $accounts += [PSCustomObject]@{
                    Cliente = "$accountType (Microsoft 365)"
                    Cuenta = $email
                    Servidor = 'OneDrive/SharePoint'
                    Tipo = 'Cuenta OneDrive'
                }
            }
        }
}

# ============================================
# 3b. DROPBOX - Cuenta(s) asociada(s)
# ============================================
# Formato de info.json documentado públicamente y estable entre versiones:
# {"personal": {"email": "...", "path": "..."}, "business": {...}} (una,
# ninguna o ambas claves presentes según qué tipo(s) de cuenta haya).
$dropboxInfoPath = "$env:APPDATA\Dropbox\info.json"
if (Test-Path $dropboxInfoPath) {
    try {
        $dbInfo = Get-Content $dropboxInfoPath -Raw | ConvertFrom-Json
        foreach ($tipoCuenta in @('personal', 'business')) {
            $cuentaInfo = $dbInfo.$tipoCuenta
            if ($cuentaInfo -and $cuentaInfo.email) {
                $accounts += [PSCustomObject]@{
                    Cliente = "Dropbox $($tipoCuenta.Substring(0,1).ToUpper() + $tipoCuenta.Substring(1))"
                    Cuenta = $cuentaInfo.email
                    Servidor = 'Dropbox'
                    Tipo = 'Cuenta Dropbox'
                }
            }
        }
    } catch {}
}

# ============================================
# 3c. GOOGLE DRIVE for Desktop
# ============================================
# El email de la cuenta vive en un SQLite/LevelDB interno sin formato
# público estable entre versiones — a diferencia de OneDrive/Dropbox, NO
# se intenta extraer aquí (arriesgado inventar un parser de un formato
# propietario sin poder verificarlo contra una instalación real). Solo se
# informa de que está instalado; el email queda pendiente de revisión manual.
$googleDrivePath = "$env:LOCALAPPDATA\Google\DriveFS"
if (Test-Path $googleDrivePath) {
    $accounts += [PSCustomObject]@{
        Cliente = 'Google Drive'
        Cuenta = '(instalado — revisar cuenta manualmente)'
        Servidor = 'Google Drive'
        Tipo = 'Cuenta Google Drive'
    }
}

# ============================================
# 4. RESULTADO FINAL
# ============================================
# FIX: la clave de deduplicación era Cuenta+Tipo — pero el mismo email, en
# el mismo navegador, se detecta por varios métodos distintos (account_info
# "Sincronizado", Local State, signin, autofill...) cada uno con un Tipo
# distinto, así que la MISMA cuenta salía repetida 2-3 veces en la tabla
# (comprobado en vivo: una cuenta de Gmail y otra de Outlook
# aparecían duplicados). Ahora la clave es Cliente+Cuenta: como los
# métodos más fiables (account_info) se añaden ANTES que los secundarios
# (Local State, signin, autofill), se queda con esa primera detección y
# descarta el resto sin perder ninguna cuenta real distinta.
$uniqueAccounts = @()
$seen = @{}

foreach ($account in $accounts) {
    $key = "$($account.Cliente)|$($account.Cuenta)"
    if (-not $seen.ContainsKey($key)) {
        $uniqueAccounts += $account
        $seen[$key] = $true
    }
}

if ($uniqueAccounts.Count -eq 0) {
    Write-Output "[]"
} elseif ($uniqueAccounts.Count -eq 1) {
    @($uniqueAccounts) | ConvertTo-Json
} else {
    $uniqueAccounts | ConvertTo-Json
}
"""

# ELIMINADO 2026-07-30 (era PS_GET_OFFICE_LICENSE_ACCOUNTS): escaneaba
# varias zonas del registro de Office (Identities/SignIn/ClickToRun
# Config/UserInfo/Licensing) en busca de CUALQUIER texto con forma de
# email y lo etiquetaba directamente como "Licencia Office 365" — daba
# falsos positivos reales y confirmados: dos cuentas de Gmail añadidas a
# Outlook por IMAP (clave de registro literal
# '..._tp_google_imap_OAuth2', es decir de TERCEROS) salían como
# licencias de Office. Sustituido en PS_GET_LICENSE_INFO por una consulta
# a SoftwareLicensingProduct (Software Protection Platform) filtrada al
# ApplicationID de Office — la misma API oficial que ya se usaba para
# Windows, no una heurística de texto.

PS_GET_OUTLOOK_ACCOUNTS = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# Portado de GestCorreov4.ps1 (pestana "Exportar a PST"). Se ejecuta como
# proceso PowerShell independiente, igual que el resto de collectors.py,
# en vez de win32com directo en el proceso de la app PyQt5. Outlook.Application
# es un servidor COM de apartamento unico (STA): AddStore/Session.Accounts
# puede colgarse indefinidamente si el proceso Outlook.exe no esta en un
# estado normal (p.ej. con un dialogo de seguridad "Object Model Guard"
# pendiente en pantalla) — usar un proceso PowerShell aparte no evita ese
# escenario (comprobado: un Start-Job de PowerShell puro, sin Python ni Qt,
# se cuelga igual si Outlook.exe no responde), pero mantiene la app
# principal aislada de ese bloqueo y consistente con el resto del codigo.
try {
    $outlook = New-Object -ComObject Outlook.Application
    $namespace = $outlook.GetNamespace("MAPI")
    $cuentas = @()
    foreach ($acc in $outlook.Session.Accounts) {
        $tipo = switch ($acc.AccountType) {
            0 { "Exchange" }
            1 { "IMAP" }
            2 { "POP3" }
            3 { "HTTP" }
            5 { "Exchange (EAS)" }
            default { "Desconocido" }
        }
        $tieneCarpeta = $false
        try {
            $nombreStore = $acc.DeliveryStore.DisplayName
            foreach ($carpeta in $namespace.Folders) {
                if ($carpeta.Name -eq $nombreStore) { $tieneCarpeta = $true; break }
            }
        } catch {}
        $cuentas += [PSCustomObject]@{
            Nombre = $acc.DisplayName
            Tipo = $tipo
            TieneCarpeta = $tieneCarpeta
        }
    }
    if ($cuentas.Count -eq 0) {
        Write-Output "[]"
    } elseif ($cuentas.Count -eq 1) {
        @($cuentas) | ConvertTo-Json
    } else {
        $cuentas | ConvertTo-Json
    }
} catch {
    [PSCustomObject]@{ Error = $_.Exception.Message } | ConvertTo-Json
}
"""

PS_EXPORT_OUTLOOK_ACCOUNT_PST = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$nombreCuenta = '{nombre_cuenta}'
$pstPath = '{pst_path}'
$modo = '{modo}'

function Salir-Resultado($ok, $mensaje) {{
    [PSCustomObject]@{{ Ok = $ok; Mensaje = $mensaje }} | ConvertTo-Json -Compress
    exit 0
}}

try {{
    $outlook = New-Object -ComObject Outlook.Application
    $namespace = $outlook.GetNamespace("MAPI")

    # -LiteralPath: con -Path, los corchetes del nombre (p. ej. «Copia [1].pst») son
    # comodines y el PST recién creado se daba por no creado.
    if ($modo -eq 'sobrescribir' -and (Test-Path -LiteralPath $pstPath)) {{
        Remove-Item -LiteralPath $pstPath -Force -ErrorAction Stop
    }}

    $pstDir = Split-Path $pstPath -Parent
    if (!(Test-Path -LiteralPath $pstDir)) {{ New-Item -ItemType Directory -Path $pstDir -Force | Out-Null }}

    if (!(Test-Path -LiteralPath $pstPath)) {{
        $namespace.AddStore($pstPath)
        Start-Sleep -Seconds 1
        if (!(Test-Path -LiteralPath $pstPath)) {{ Salir-Resultado $false "No se pudo crear el archivo PST." }}
    }}

    $pstStore = $null
    foreach ($store in $namespace.Stores) {{
        try {{ if ($store.FilePath -eq $pstPath) {{ $pstStore = $store; break }} }} catch {{}}
    }}
    if ($null -eq $pstStore) {{
        $namespace.AddStore($pstPath)
        Start-Sleep -Seconds 1
        foreach ($store in $namespace.Stores) {{
            try {{ if ($store.FilePath -eq $pstPath) {{ $pstStore = $store; break }} }} catch {{}}
        }}
    }}
    if ($null -eq $pstStore) {{ Salir-Resultado $false "No se pudo acceder al PST tras crearlo." }}

    $pstRoot = $pstStore.GetRootFolder()

    $cuentaCom = $null
    foreach ($acc in $outlook.Session.Accounts) {{
        if ($acc.DisplayName -eq $nombreCuenta) {{ $cuentaCom = $acc; break }}
    }}
    if ($null -eq $cuentaCom) {{
        try {{ $namespace.RemoveStore($pstRoot) }} catch {{}}
        Salir-Resultado $false "No se encontro la cuenta '$nombreCuenta'."
    }}

    $carpetaCuenta = $null
    $nombreStore = $cuentaCom.DeliveryStore.DisplayName
    foreach ($carpeta in $namespace.Folders) {{
        if ($carpeta.Name -eq $nombreStore) {{ $carpetaCuenta = $carpeta; break }}
    }}
    if ($null -eq $carpetaCuenta) {{
        try {{ $namespace.RemoveStore($pstRoot) }} catch {{}}
        Salir-Resultado $false "No se encontro la carpeta raiz de la cuenta."
    }}

    foreach ($sub in @($carpetaCuenta.Folders)) {{
        try {{ $sub.CopyTo($pstRoot) | Out-Null }} catch {{}}
    }}

    try {{ $namespace.RemoveStore($pstRoot) }} catch {{}}
    Salir-Resultado $true $pstPath
}} catch {{
    Salir-Resultado $false $_.Exception.Message
}}
"""

PS_GET_OUTLOOK_ACCOUNT_DATAFILE = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$nombreCuenta = '{nombre_cuenta}'

try {{
    $outlook = New-Object -ComObject Outlook.Application
    $namespace = $outlook.GetNamespace("MAPI")

    $cuentaCom = $null
    foreach ($acc in $outlook.Session.Accounts) {{
        if ($acc.DisplayName -eq $nombreCuenta) {{ $cuentaCom = $acc; break }}
    }}
    if ($null -eq $cuentaCom) {{
        [PSCustomObject]@{{ Ok = $false; Mensaje = "No se encontro la cuenta." }} | ConvertTo-Json -Compress
        exit 0
    }}

    $nombreStore = $cuentaCom.DeliveryStore.DisplayName
    $ruta = $null
    foreach ($store in $namespace.Stores) {{
        try {{ if ($store.DisplayName -eq $nombreStore) {{ $ruta = $store.FilePath; break }} }} catch {{}}
    }}
    if ([string]::IsNullOrEmpty($ruta)) {{
        [PSCustomObject]@{{ Ok = $false; Mensaje = "Esta cuenta no tiene un archivo de datos local (.ost/.pst) accesible." }} | ConvertTo-Json -Compress
    }} else {{
        [PSCustomObject]@{{ Ok = $true; Mensaje = $ruta }} | ConvertTo-Json -Compress
    }}
}} catch {{
    [PSCustomObject]@{{ Ok = $false; Mensaje = $_.Exception.Message }} | ConvertTo-Json -Compress
}}
"""

PS_GET_USB_DEVICES = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# 5.4.0: todo lo CONECTADO por USB (Id USB\ o USBSTOR\, solo presentes), no solo la clase «USB»
# (concentradores y controladora): antes no salían el disco USB, el teclado, el ratón ni la cámara.
# Los concentradores y la controladora van al final; un dispositivo con problema lo dice.
$dispositivosUsb = @(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
    Where-Object { $_.InstanceId -like 'USB\*' -or $_.InstanceId -like 'USBSTOR\*' } |
    Sort-Object @{ Expression = { if ($_.Class -eq 'USB') { 1 } else { 0 } } }, FriendlyName |
    ForEach-Object {
    $clase = if ($_.Class) { [string]$_.Class } else { 'Otro' }
    [PSCustomObject]@{
        Nombre = if ($_.FriendlyName) { "$($_.FriendlyName) [$clase]" } else { "Dispositivo USB [$clase]" }
        Fabricante = if ($_.Manufacturer) { $_.Manufacturer } else { 'Desconocido' }
        Estado = if ($_.Status -eq 'OK') { 'Conectado' } else { "Con problema ($($_.Status))" }
        InstanceId = $_.InstanceId
    }
})
if ($dispositivosUsb.Count -eq 0) { Write-Output "[]" } else { $dispositivosUsb | ConvertTo-Json }
"""

PS_GET_MONITORS = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$graficas = @()
try {
    Get-CimInstance -ClassName Win32_VideoController -ErrorAction SilentlyContinue | ForEach-Object {
        $vram = if ($_.AdapterRAM -gt 0) { "$([math]::Round($_.AdapterRAM / 1GB, 2)) GB" } else { 'N/A' }
        $resolucion = if ($_.CurrentHorizontalResolution -and $_.CurrentVerticalResolution) {
            "$($_.CurrentHorizontalResolution) x $($_.CurrentVerticalResolution)"
        } else { 'N/A' }
        $graficas += [PSCustomObject]@{
            Nombre = $_.Name
            RAM = $vram
            Driver = if ($_.DriverVersion) { $_.DriverVersion } else { 'N/A' }
            Resolucion = $resolucion
        }
    }
} catch {}

$monitors = @()

try {
    Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorID -ErrorAction Stop | ForEach-Object {
        $manufacturer = [System.Text.Encoding]::ASCII.GetString($_.ManufacturerName -ne 0).Trim()
        $name = [System.Text.Encoding]::ASCII.GetString($_.UserFriendlyName -ne 0).Trim()
        $serial = [System.Text.Encoding]::ASCII.GetString($_.SerialNumberID -ne 0).Trim()

        $tipoConexion = 'Desconocido'
        $instanceName = $_.InstanceName

        # Get connection type from WmiMonitorConnectionParams
        try {
            $connParams = Get-CimInstance -Namespace root/wmi -ClassName WmiMonitorConnectionParams -ErrorAction SilentlyContinue |
                Where-Object { $_.InstanceName -eq $instanceName } | Select-Object -First 1

            if ($connParams -and $connParams.VideoOutputTechnology) {
                $tech = $connParams.VideoOutputTechnology
                $tipoConexion = switch ($tech) {
                    -1 { 'VGA' }
                    0 { 'VGA' }
                    4 { 'DVI' }
                    5 { 'DVI' }
                    10 { 'HDMI' }
                    11 { 'HDMI' }
                    12 { 'DisplayPort' }
                    13 { 'DisplayPort' }
                    15 { 'DisplayPort' }
                    16 { 'USB Type-C' }
                    17 { 'Thunderbolt' }
                    default { 'Digital' }
                }
            }
        } catch {}

        $monitors += [PSCustomObject]@{
            Nombre = if ($name) { $name } else { 'Monitor' }
            Fabricante = if ($manufacturer) { $manufacturer } else { 'Desconocido' }
            Serial = if ($serial) { $serial } else { 'N/A' }
            TipoConexion = $tipoConexion
        }
    }
} catch {
    $monitors += [PSCustomObject]@{
        Nombre = 'Error'
        Fabricante = 'N/A'
        Serial = 'N/A'
        TipoConexion = 'N/A'
    }
}

if ($monitors.Count -eq 0) {
    $monitors += [PSCustomObject]@{
        Nombre = 'No detectado'
        Fabricante = 'N/A'
        Serial = 'N/A'
        TipoConexion = 'N/A'
    }
}

@{ Graficas = $graficas; Monitores = $monitors } | ConvertTo-Json -Depth 4
"""

PS_GET_SYSTEM_INFO = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$systemInfo = @{}

try {
    # Computer Info
    $cs = Get-CimInstance -ClassName Win32_ComputerSystem
    $os = Get-CimInstance -ClassName Win32_OperatingSystem
    $bios = Get-CimInstance -ClassName Win32_BIOS
    $enclosure = Get-CimInstance -ClassName Win32_SystemEnclosure -ErrorAction SilentlyContinue
    $tieneBateria = [bool](Get-CimInstance -ClassName Win32_Battery -ErrorAction SilentlyContinue)

    # --- Forma del chasis (texto legible del código ChassisTypes) ---
    $chassisMap = @{
        3='Desktop'; 4='Desktop (bajo perfil)'; 5='Pizza Box'; 6='Torre'; 7='Torre'
        8='Portátil'; 9='Portátil'; 10='Notebook'; 11='Portátil (manual)'; 12='Docking Station'
        13='All-in-One'; 14='Sub-Notebook'; 15='Space-saving'; 16='Lunch Box'; 17='Servidor (Torre)'
        21='Peripheral Chassis'; 23='Servidor (Rack)'; 24='Sealed-case PC'; 28='Mini Tower'
        30='Tablet'; 31='Convertible'; 32='Detachable'; 34='SFF (Slim)'; 35='SFF'; 36='Mini PC'
    }
    $chassisTexto = 'N/A'
    if ($enclosure -and $enclosure.ChassisTypes) {
        $primerTipo = [int]($enclosure.ChassisTypes | Select-Object -First 1)
        $chassisTexto = if ($chassisMap.ContainsKey($primerTipo)) { $chassisMap[$primerTipo] } else { "Otro (código $primerTipo)" }
    }

    # --- Heurística de Tipo de equipo (PC / Portátil / NUC / MiniPC / Servidor / VM) ---
    $modeloTexto = "$($cs.Manufacturer) $($cs.Model)"
    $esVM = $modeloTexto -match 'VMware|VirtualBox|Virtual Machine|KVM|QEMU|Xen|Hyper-V'
    $esServidor = ($os.ProductType -ne 1) -or ($cs.PCSystemType -in 4, 5, 7)
    $esPortatil = $tieneBateria -or ($cs.PCSystemType -eq 2) -or
                  ($enclosure -and ($enclosure.ChassisTypes | Where-Object { $_ -in 8, 9, 10, 11, 14, 30, 31, 32 }))

    if ($esVM) { $tipoEquipo = 'Máquina Virtual' }
    elseif ($esServidor) { $tipoEquipo = 'Servidor' }
    elseif ($esPortatil) { $tipoEquipo = 'Portátil' }
    elseif ($modeloTexto -match 'NUC') { $tipoEquipo = 'NUC' }
    elseif ($modeloTexto -match 'Mini\s?PC|Stick PC' -or ($enclosure -and ($enclosure.ChassisTypes -contains 36))) { $tipoEquipo = 'MiniPC' }
    elseif ($cs.PCSystemType -eq 3) { $tipoEquipo = 'Workstation' }
    else { $tipoEquipo = 'Desktop' }

    $systemInfo['Computer'] = @{
        Nombre = $env:COMPUTERNAME
        Fabricante = $cs.Manufacturer
        Modelo = $cs.Model
        Tipo = $tipoEquipo
        FormaChasis = $chassisTexto
        NumeroSerie = if ($bios.SerialNumber) { $bios.SerialNumber.Trim() } else { 'N/A' }
        Dominio = if ($cs.PartOfDomain) { $cs.Domain } else { 'WORKGROUP' }
    }

    # OS Info
    # Get-CimInstance (a diferencia del Get-WmiObject antiguo) ya entrega
    # InstallDate como [datetime] real; volver a convertirlo con
    # ManagementDateTimeConverter lanzaba excepción SIEMPRE y por eso el
    # dato aparecía como N/A en todos los equipos.
    if ($os.InstallDate -is [datetime]) {
        $installDateStr = $os.InstallDate.ToString('dd/MM/yyyy')
    } else {
        try {
            $installDateStr = ([Management.ManagementDateTimeConverter]::ToDateTime($os.InstallDate)).ToString('dd/MM/yyyy')
        } catch {
            $installDateStr = 'N/A'
        }
    }

    # Licencia: ProductKeyID es un GUID interno, no la clave de Windows.
    # PartialProductKey (últimos 5 caracteres) es lo único que expone la
    # API oficial desde Windows 8 (lo mismo que "slmgr /dli"). La clave
    # completa de 25 caracteres SÍ existe en equipos con clave física
    # (OEM/Retail antiguos): se decodifica del blob DigitalProductId del
    # registro con el algoritmo estándar (BCDFGHJKMPQRTVWXY2346789, base
    # 24). En licencia digital (entitlement ligado a hardware/cuenta, sin
    # clave física) ese blob decodifica a un valor placeholder reconocible
    # (todo el mismo carácter, p.ej. "BBBBB-BBBBB-..."): no es un fallo de
    # la decodificación, es que ahí no hay ninguna clave real que mostrar.
    function Get-FullProductKey {
        try {
            $map = 'BCDFGHJKMPQRTVWXY2346789'
            $id = (Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -Name DigitalProductId -ErrorAction Stop).DigitalProductId
            $keyOffset = 52
            # 5.7.3: división ENTERA (antes «/» decimal redondeada) y, en Windows 8 o posterior, la «N» que se inserta
            # tras el último dígito calculado. Sin ella la clave mostrada era errónea (comprobado: la de este equipo salía
            # sin «N», imposible en una clave de Windows 8+, y con el primer carácter de más).
            $esWin8Mas = [math]::Truncate([int]$id[66] / 6) -band 1
            $id[66] = ([int]$id[66] -band 0xF7) -bor (($esWin8Mas -band 2) * 4)
            $clave = ''
            $ultimo = 0
            for ($i = 24; $i -ge 0; $i--) {
                $actual = 0
                for ($j = 14; $j -ge 0; $j--) {
                    $actual = $actual * 256 + [int]$id[$j + $keyOffset]
                    $id[$j + $keyOffset] = [byte][math]::Truncate($actual / 24)
                    $actual = $actual % 24
                }
                $clave = $map[$actual] + $clave
                $ultimo = $actual
            }
            if ($esWin8Mas -eq 1) {
                $clave = $clave.Substring(1, $ultimo) + 'N' + $clave.Substring($ultimo + 1)
            }
            $clave = $clave.Substring(0, 5) + '-' + $clave.Substring(5, 5) + '-' + $clave.Substring(10, 5) + '-' +
                     $clave.Substring(15, 5) + '-' + $clave.Substring(20, 5)
            # Placeholder de licencia digital: todo el mismo carácter, sin contar la «N» insertada (que no es del alfabeto)
            if ((($clave -replace '-', '') -replace 'N', '') -match '^(.)\1*$') { return $null }
            return $clave
        } catch { return $null }
    }

    $serialParcial = 'N/A'
    $licEstado = 'N/A'
    $licCanal = 'N/A'
    try {
        $licProduct = Get-CimInstance -ClassName SoftwareLicensingProduct -ErrorAction SilentlyContinue |
            Where-Object { $_.LicenseStatus -eq 1 -and $_.Name -like 'Windows*' } | Select-Object -First 1
        if (-not $licProduct) {
            $licProduct = Get-CimInstance -ClassName SoftwareLicensingProduct -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -like 'Windows*' -and $_.ApplicationId -eq '55c92734-d682-4d71-983e-d6ec3f16059f' } |
                Select-Object -First 1
        }
        if ($licProduct) {
            $claveCompleta = Get-FullProductKey
            $serialParcial = if ($claveCompleta) {
                $claveCompleta
            } elseif ($licProduct.PartialProductKey) {
                $licProduct.PartialProductKey
            } else {
                'N/A (licencia digital)'
            }
            $estadosLicencia = @{
                0='No activado'; 1='Activado'; 2='Gracia (fuera de línea)'; 3='Gracia (fuera de tolerancia)'
                4='Gracia (no genuino)'; 5='Notificación (no activado)'; 6='Gracia extendida'
            }
            $codigoEstado = [int]$licProduct.LicenseStatus
            $licEstado = if ($estadosLicencia.ContainsKey($codigoEstado)) { $estadosLicencia[$codigoEstado] } else { 'Desconocido' }
            $licCanal = if ($licProduct.ProductKeyChannel) { $licProduct.ProductKeyChannel } else { 'N/A' }
        }
    } catch {}

    $systemInfo['OS'] = @{
        Nombre = $os.Caption
        Version = $os.Version
        Build = $os.BuildNumber
        Arquitectura = $os.OSArchitecture
        Instalacion = $installDateStr
        Serial = $serialParcial
        EstadoActivacion = $licEstado
        CanalLicencia = $licCanal
    }

    # CPU Info
    $cpu = Get-CimInstance -ClassName Win32_Processor | Select-Object -First 1
    $systemInfo['CPU'] = @{
        Nombre = $cpu.Name.Trim()
        Fabricante = $cpu.Manufacturer
        Nucleos = $cpu.NumberOfCores
        Hilos = $cpu.NumberOfLogicalProcessors
        Velocidad = "$([math]::Round($cpu.MaxClockSpeed / 1000, 2)) GHz"
    }

    # RAM Info
    $totalRAM = [math]::Round($cs.TotalPhysicalMemory / 1GB, 2)
    $modulosRAM = @(Get-CimInstance -ClassName Win32_PhysicalMemory)
    # Ranuras totales de la placa (no solo las ocupadas) — señal real de
    # margen de ampliación, distinta del nº de módulos instalados.
    $ranurasTotales = $null
    try {
        $memArray = Get-CimInstance -ClassName Win32_PhysicalMemoryArray -ErrorAction Stop | Select-Object -First 1
        if ($memArray -and $memArray.MemoryDevices) { $ranurasTotales = [int]$memArray.MemoryDevices }
    } catch {}
    $systemInfo['RAM'] = @{
        Total = "$totalRAM GB"
        RanurasUsadas = $modulosRAM.Count
        RanurasTotales = $ranurasTotales
        Modulos = @()
    }

    $modulosRAM | ForEach-Object {
        $systemInfo['RAM']['Modulos'] += @{
            Capacidad = "$([math]::Round($_.Capacity / 1GB, 2)) GB"
            Velocidad = "$($_.Speed) MHz"
            Fabricante = if ($_.Manufacturer) { $_.Manufacturer.Trim() } else { 'Desconocido' }
            Tipo = switch ($_.SMBIOSMemoryType) {
                20 { 'DDR' }
                21 { 'DDR2' }
                24 { 'DDR3' }
                26 { 'DDR4' }
                34 { 'DDR5' }
                default { 'Desconocido' }
            }
            Ubicacion = $_.DeviceLocator
        }
    }

    # Motherboard Info
    $mb = Get-CimInstance -ClassName Win32_BaseBoard
    # SMBIOSBIOSVersion no es fiable en todos los OEM: en algunos Lenovo
    # devuelve el mismo texto que el modelo de placa, o el mismo texto que
    # el nombre/ID del propio BIOS (p.ej. "M1AKT59A" en Nombre Y en
    # Versión) — en ese caso no hay una versión de firmware distinta que
    # mostrar, así que se deja en N/A en vez de repetir un dato ya visible
    # en otra fila.
    $biosVersionTexto = 'N/A'
    if ($bios.SMBIOSBIOSVersion) {
        $biosVersionTexto = $bios.SMBIOSBIOSVersion.Trim()
        $coincideConPlaca = $mb.Product -and ($biosVersionTexto -ieq $mb.Product.Trim())
        $coincideConNombre = $bios.Name -and ($biosVersionTexto -ieq $bios.Name.Trim())
        if ($coincideConPlaca -or $coincideConNombre) {
            $biosVersionTexto = 'N/A'
        }
    }
    # Placa Base tiene su PROPIA versión (revisión física de la placa,
    # Win32_BaseBoard.Version) — antes se mostraba aquí la versión del BIOS
    # (duplicado exacto de la ficha BIOS/Firmware). Muchos fabricantes
    # dejan este campo vacío o a '0'; en ese caso no se muestra nada mejor
    # que repetir el dato del BIOS.
    $mbVersionTexto = 'N/A'
    if ($mb.Version -and $mb.Version.Trim() -and $mb.Version.Trim() -ne '0') {
        $mbVersionTexto = $mb.Version.Trim()
    }
    $systemInfo['Motherboard'] = @{
        Fabricante = $mb.Manufacturer
        Modelo = $mb.Product
        Version = $mbVersionTexto
    }

    # BIOS / Firmware Info
    # Fecha de lanzamiento: mismo cuidado que con InstallDate del SO, CIM
    # ya entrega [datetime] real y no hay que volver a convertirlo.
    if ($bios.ReleaseDate -is [datetime]) {
        $biosFechaStr = $bios.ReleaseDate.ToString('dd/MM/yyyy')
        $biosFechaDT = $bios.ReleaseDate
    } else {
        try {
            $biosFechaDT = [Management.ManagementDateTimeConverter]::ToDateTime($bios.ReleaseDate)
            $biosFechaStr = $biosFechaDT.ToString('dd/MM/yyyy')
        } catch {
            $biosFechaStr = 'N/A'
            $biosFechaDT = $null
        }
    }
    $biosAntiguedad = 'N/A'
    if ($biosFechaDT) {
        $anios = [math]::Floor(((Get-Date) - $biosFechaDT).Days / 365.25)
        $biosAntiguedad = if ($anios -le 0) { "Menos de 1 año" } else { "$anios año(s)" }
    }

    # UEFI vs Legacy: la clave de registro SecureBoot\State sólo existe en
    # firmware UEFI (con Secure Boot activado o no). Confirm-SecureBootUEFI
    # sólo se consulta si ya sabemos que es UEFI, porque en Legacy lanza
    # PlatformNotSupportedException siempre.
    $firmwareTipo = 'Legacy (BIOS heredado)'
    $secureBootTexto = 'No aplica (BIOS Legacy)'
    if (Test-Path 'HKLM:\SYSTEM\CurrentControlSet\Control\SecureBoot\State') {
        $firmwareTipo = 'UEFI'
        try {
            $secureBootActivo = Confirm-SecureBootUEFI -ErrorAction Stop
            $secureBootTexto = if ($secureBootActivo) { 'Activado' } else { 'Desactivado' }
        } catch {
            $secureBootTexto = 'Desconocido'
        }
    }

    $systemInfo['Bios'] = @{
        Fabricante = $bios.Manufacturer
        Nombre = $bios.Name
        Version = $biosVersionTexto
        Fecha = $biosFechaStr
        Antiguedad = $biosAntiguedad
        Firmware = $firmwareTipo
        SecureBoot = $secureBootTexto
    }

    # Nota: discos, particiones y GPU se recopilan en sus propias pestañas
    # (Discos Duros / Gráficas y Monitores) y los adaptadores de red en la
    # pestaña Redes — aquí sólo va lo que es propiamente "información del
    # sistema", para no duplicar datos ni desincronizarlos entre pestañas.

    # Escritorio Remoto (RDP): habilitado, NLA y puerto
    try {
        $rdpDeny = (Get-ItemProperty -Path 'HKLM:\System\CurrentControlSet\Control\Terminal Server' -Name fDenyTSConnections -ErrorAction SilentlyContinue).fDenyTSConnections
        $rdpHabilitado = if ($null -ne $rdpDeny) { $rdpDeny -eq 0 } else { $false }
        $nla = (Get-ItemProperty -Path 'HKLM:\System\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp' -Name UserAuthentication -ErrorAction SilentlyContinue).UserAuthentication
        $nlaActivo = if ($null -ne $nla) { $nla -eq 1 } else { $false }
        $rdpPuerto = (Get-ItemProperty -Path 'HKLM:\System\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp' -Name PortNumber -ErrorAction SilentlyContinue).PortNumber
        $systemInfo['EscritorioRemoto'] = @{
            Habilitado = if ($rdpHabilitado) { 'Sí' } else { 'No' }
            NLA = if ($rdpHabilitado) { if ($nlaActivo) { 'Activo' } else { 'INACTIVO (riesgo)' } } else { 'N/A' }
            Puerto = if ($rdpPuerto) { [string]$rdpPuerto } else { '3389 (por defecto)' }
        }
    } catch {
        $systemInfo['EscritorioRemoto'] = @{ Habilitado = 'N/A'; NLA = 'N/A'; Puerto = 'N/A' }
    }

} catch {
    $systemInfo['Error'] = $_.Exception.Message
}

$systemInfo | ConvertTo-Json -Depth 10
"""

PS_GET_INSTALLED_UPDATES = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

# Se combinan DOS fuentes, deduplicadas por KB (o por título si no hay KB):
# Get-HotFix (Win32_QuickFixEngineering) es rápido pero NO lista la mayoría
# de las actualizaciones acumulativas modernas de Windows 10/11 — se
# registran vía Component-Based Servicing, no vía el mecanismo clásico de
# hotfix. Se completa con la misma API de Windows Update que se usa para
# las pendientes, ahora con IsInstalled=1, que sí las ve todas.
$instaladas = [ordered]@{}

try {
    Get-HotFix | ForEach-Object {
        $kb = $_.HotFixID
        $instaladas[$kb] = @{
            KB = $kb
            Descripcion = $_.Description
            Instalacion = if ($_.InstalledOn) { $_.InstalledOn.ToString('dd/MM/yyyy') } else { 'N/A' }
            Usuario = if ($_.InstalledBy) { $_.InstalledBy } else { 'Sistema' }
        }
    }
} catch {}

try {
    $session = New-Object -ComObject Microsoft.Update.Session
    $searcher = $session.CreateUpdateSearcher()
    $result = $searcher.Search("IsInstalled=1 and Type='Software'")
    foreach ($u in $result.Updates) {
        $kb = 'N/A'
        foreach ($b in $u.BundledUpdates) {
            if ($b.KBArticleIDs.Count -gt 0) { $kb = "KB$($b.KBArticleIDs[0])"; break }
        }
        if ($kb -eq 'N/A' -and $u.KBArticleIDs.Count -gt 0) { $kb = "KB$($u.KBArticleIDs[0])" }
        $fecha = if ($u.LastDeploymentChangeTime) { $u.LastDeploymentChangeTime.ToString('dd/MM/yyyy') } else { 'N/A' }
        $clave = if ($kb -ne 'N/A') { $kb } else { "T:$($u.Title)" }
        if (-not $instaladas.Contains($clave)) {
            $instaladas[$clave] = @{
                KB = $kb
                Descripcion = $u.Title
                Instalacion = $fecha
                Usuario = 'Sistema'
            }
        }
    }
} catch {}

$lista = @($instaladas.Values)
if ($lista.Count -eq 0) {
    $lista = @(@{ KB = 'Error'; Descripcion = 'No se pudieron obtener actualizaciones instaladas'; Instalacion = 'N/A'; Usuario = 'N/A' })
}
# ConvertTo-Json colapsa un array de 1 elemento a un objeto suelto (no lo
# envuelve en '[' ']'): sin este ajuste, el lado Python vería un dict en vez
# de una lista de 1 dict y fallaría al iterarlo campo a campo.
$json = $lista | ConvertTo-Json -Depth 5
if ($lista.Count -eq 1) { $json = "[$json]" }
$json
"""

PS_GET_REBOOT_PENDING = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
# Una actualización ya instalada que espera reinicio para completarse no
# aparece como "pendiente" (IsInstalled ya es 1) ni destaca en "instaladas"
# — es un estado propio a nivel de sistema, no por actualización. Se
# comprueban las dos claves de registro que lo marcan (Component Based
# Servicing, la más común desde Windows 8+, y el mecanismo clásico de
# Windows Update).
try {
    $pendiente = $false
    if (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending') { $pendiente = $true }
    if (Test-Path 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\WindowsUpdate\Auto Update\RebootRequired') { $pendiente = $true }
    if ($pendiente) { 'true' } else { 'false' }
} catch { 'false' }
"""

PS_GET_PENDING_UPDATES = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$pendientes = @()
$fallo_software = $null
$fallo_driver = $null

function ConvertirActualizacion($update, $tipoTexto) {
    $criticidad = switch ($update.MsrcSeverity) {
        'Critical' { 'Crítica' }
        'Important' { 'Importante' }
        'Moderate' { 'Moderada' }
        'Low' { 'Baja' }
        default {
            if ($update.AutoSelectOnWebSites) { 'Recomendada' }
            else { 'Opcional' }
        }
    }

    $kb = 'N/A'
    foreach ($bundledUpdate in $update.BundledUpdates) {
        if ($bundledUpdate.KBArticleIDs.Count -gt 0) {
            $kb = "KB$($bundledUpdate.KBArticleIDs[0])"
            break
        }
    }
    if ($kb -eq 'N/A' -and $update.KBArticleIDs.Count -gt 0) {
        $kb = "KB$($update.KBArticleIDs[0])"
    }

    $sizeMB = if ($update.MaxDownloadSize -gt 0) {
        "$([math]::Round($update.MaxDownloadSize / 1MB, 2)) MB"
    } else {
        'N/A'
    }

    $fecha = if ($update.LastDeploymentChangeTime) {
        $update.LastDeploymentChangeTime.ToString('dd/MM/yyyy')
    } else {
        'N/A'
    }

    return @{
        KB = $kb
        Titulo = $update.Title
        Tamano = $sizeMB
        Criticidad = $criticidad
        Fecha = $fecha
        Tipo = $tipoTexto
        UpdateID = $update.Identity.UpdateID
    }
}

$session = New-Object -ComObject Microsoft.Update.Session
$searcher = $session.CreateUpdateSearcher()

# Dos búsquedas separadas, no "Type='Software' or Type='Driver'" en una
# sola: el motor de criterios de Windows Update Agent rechaza esa
# combinación con OR entre paréntesis (HRESULT 0x80240032, criterio
# inválido) aunque cada tipo por separado sí es válido. Además, así un
# fallo buscando drivers no se lleva por delante lo de software, y
# viceversa.
try {
    $resultSoftware = $searcher.Search("IsInstalled=0 and Type='Software'")
    foreach ($update in $resultSoftware.Updates) {
        $pendientes += ConvertirActualizacion $update 'Software'
    }
} catch {
    $fallo_software = $_.Exception.Message
}

try {
    $resultDriver = $searcher.Search("IsInstalled=0 and Type='Driver'")
    foreach ($update in $resultDriver.Updates) {
        $pendientes += ConvertirActualizacion $update 'Controlador'
    }
} catch {
    $fallo_driver = $_.Exception.Message
}

if ($fallo_software -and $fallo_driver) {
    $pendientes = @(@{
        KB = 'Error'
        Titulo = "No se pudieron obtener actualizaciones pendientes: $fallo_software"
        Tamano = 'N/A'
        Criticidad = 'N/A'
        Fecha = 'N/A'
        Tipo = 'N/A'
        UpdateID = ''
    })
}

# A diferencia de Instaladas, 0 pendientes es un resultado legítimo y
# habitual (equipo al día) — no un fallo, así que NO se sustituye por una
# fila de relleno. Pero ConvertTo-Json sobre un array vacío no emite "[]",
# emite NADA (cadena vacía), que run_powershell_json interpretaría como
# fallo/timeout igual que si el proceso no hubiera devuelto nada — así que
# aquí sí hace falta forzar el "[]" explícito para no confundir "0 de
# verdad" con "la búsqueda falló".
if ($pendientes.Count -eq 0) {
    $json = "[]"
} elseif ($pendientes.Count -eq 1) {
    $json = "[" + ($pendientes | ConvertTo-Json -Depth 5) + "]"
} else {
    $json = $pendientes | ConvertTo-Json -Depth 5
}
$json
"""

PS_GET_DRIVERS = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

$drivers = @()

# Dispositivos con problema real (driver roto, deshabilitado, en conflicto...)
# vía Win32_PnPEntity.ConfigManagerErrorCode — Win32_PnPSignedDriver por sí
# solo NO detecta esto: solo lista controladores que SÍ existen, así que un
# dispositivo con el controlador roto o sin instalar ("dispositivo
# desconocido" en el Administrador de dispositivos) era invisible del todo.
# Clave de cruce: DeviceID coincide entre las dos clases WMI.
$problemas = @{}
try {
    Get-CimInstance -ClassName Win32_PnPEntity -Filter "ConfigManagerErrorCode != 0" -ErrorAction SilentlyContinue | ForEach-Object {
        $codigo = $_.ConfigManagerErrorCode
        $desc = switch ($codigo) {
            1  { 'Dispositivo no configurado correctamente' }
            3  { 'Controlador dañado' }
            10 { 'El dispositivo no puede iniciarse' }
            12 { 'No hay suficientes recursos libres' }
            14 { 'Requiere reiniciar el equipo' }
            16 { 'No se pueden identificar todos los recursos' }
            18 { 'Hay que reinstalar los controladores' }
            19 { 'Configuración del registro dañada' }
            21 { 'Windows está quitando el dispositivo' }
            22 { 'Dispositivo deshabilitado' }
            24 { 'Dispositivo no presente o le faltan controladores' }
            28 { 'Los controladores no están instalados' }
            29 { 'Deshabilitado por el firmware' }
            31 { 'No funciona correctamente' }
            32 { 'Controlador deshabilitado' }
            33 { 'No se puede determinar qué recursos necesita' }
            34 { 'No se puede determinar la configuración' }
            35 { 'El firmware del sistema no incluye suficiente información' }
            36 { 'Recibe una IRQ que usa otro dispositivo' }
            37 { 'El controlador no pudo inicializarse' }
            38 { 'Controlador anterior aún en memoria' }
            39 { 'Controlador dañado o ausente' }
            40 { 'Información del registro dañada' }
            41 { 'Controlador cargado pero no encuentra el dispositivo' }
            42 { 'Dispositivo duplicado' }
            43 { 'Windows detuvo el dispositivo por errores' }
            44 { 'Una aplicación o servicio detuvo el dispositivo' }
            45 { 'Dispositivo desconectado actualmente' }
            46 { 'Dispositivo no accesible (Windows apagándose)' }
            47 { 'Preparado para extracción segura' }
            48 { 'Controlador bloqueado por incompatibilidad' }
            49 { 'El sistema no puede iniciar más dispositivos' }
            52 { 'Controlador no firmado digitalmente' }
            default { "Código de error $codigo" }
        }
        $problemas[$_.DeviceID.ToUpperInvariant()] = @{
            Desc = $desc
            Nombre = $_.Name
            Fabricante = $_.Manufacturer
            Clase = $_.PNPClass
        }
    }
} catch {}

$idsConDriver = New-Object System.Collections.Generic.HashSet[string]

$clasesEs = @{
    'System'='Sistema'; 'USB'='USB'; 'Net'='Red'; 'NetTrans'='Red (transporte)'
    'NetClient'='Red (cliente)'; 'NetService'='Red (servicio)'; 'Display'='Gráficos'
    'MEDIA'='Audio'; 'AudioEndpoint'='Punto de audio'; 'HIDClass'='Dispositivos de entrada (HID)'
    'Mouse'='Ratón'; 'Keyboard'='Teclado'; 'DiskDrive'='Unidades de disco'
    'CDROM'='Unidades de CD/DVD'; 'Volume'='Volumen'; 'VolumeSnapshot'='Instantánea de volumen'
    'SCSIAdapter'='Adaptador SCSI/RAID'; 'Bluetooth'='Bluetooth'; 'Image'='Escáneres y cámaras'
    'Printer'='Impresoras'; 'PRINTQUEUE'='Cola de impresión'; 'Monitor'='Monitores'
    'Ports'='Puertos (COM y LPT)'; 'Battery'='Batería'; 'Processor'='Procesador'
    'Computer'='Equipo'; 'SmartCardReader'='Lector de tarjetas inteligentes'
    'SecurityDevices'='Dispositivos de seguridad'; 'SoftwareDevice'='Dispositivo de software'
    'SoftwareComponent'='Componente de software'; 'WPD'='Dispositivo portátil'
    'Firmware'='Firmware'; 'Sensor'='Sensores'; 'Biometric'='Biometría'
    'Modem'='Módem'; 'Multifunction'='Multifunción'; 'Extension'='Extensión'
    'SDHost'='Controlador de tarjetas SD'; 'ScmVolume'='Volumen SCM'; '36FBE'='Dispositivo genérico'
    'DXGKrnl'='Núcleo de gráficos'; '1394'='FireWire (1394)'; 'AVC'='Vídeo AV/C'
    'InfraRed'='Infrarrojos'; 'PCMCIA'='PCMCIA'; 'LegacyDriver'='Controlador heredado'
    'AUDIOPROCESSINGOBJECT'='Audio (procesamiento)'; 'HDC'='Controlador de host (HDC)'
    'CAMERA'='Cámaras'
}

try {
    Get-CimInstance -ClassName Win32_PnPSignedDriver | ForEach-Object {
        $driver = $_

        # Get-CimInstance (a diferencia del Get-WmiObject antiguo) ya entrega
        # DriverDate como [datetime] real — volver a convertirlo con
        # ManagementDateTimeConverter lanzaba excepción SIEMPRE (igual que
        # pasaba con InstallDate/BIOS ReleaseDate en Sistema) y por eso Fecha
        # y, con ella, Antigüedad salían 'N/A' en todas las filas.
        $driverDateStr = 'N/A'
        if ($driver.DriverDate) {
            if ($driver.DriverDate -is [datetime]) {
                $driverDateStr = $driver.DriverDate.ToString('dd/MM/yyyy')
            } else {
                try {
                    $driverDateStr = ([Management.ManagementDateTimeConverter]::ToDateTime($driver.DriverDate)).ToString('dd/MM/yyyy')
                } catch { $driverDateStr = 'N/A' }
            }
        }

        $firmado = if ($driver.IsSigned) { 'Sí' } else { 'No' }
        $claseOriginal = if ($driver.DeviceClass) { $driver.DeviceClass } else { '' }
        $clase = if ($claseOriginal -and $clasesEs.ContainsKey($claseOriginal)) { $clasesEs[$claseOriginal] }
                 elseif ($claseOriginal) { $claseOriginal }
                 else { 'Desconocido' }

        $idKey = if ($driver.DeviceID) { $driver.DeviceID.ToUpperInvariant() } else { '' }
        if ($idKey) { $idsConDriver.Add($idKey) | Out-Null }
        $problema = if ($idKey -and $problemas.ContainsKey($idKey)) { $problemas[$idKey].Desc } else { '' }

        $drivers += [PSCustomObject]@{
            Dispositivo = if ($driver.DeviceName) { $driver.DeviceName } else { 'Desconocido' }
            Fabricante = if ($driver.Manufacturer) { $driver.Manufacturer } else { 'Desconocido' }
            Version = if ($driver.DriverVersion) { $driver.DriverVersion } else { 'N/A' }
            Fecha = $driverDateStr
            Clase = $clase
            Firmado = $firmado
            Estado = 'Instalado'
            Proveedor = if ($driver.DriverProviderName) { $driver.DriverProviderName } else { 'Desconocido' }
            Problema = $problema
            DeviceID = if ($driver.DeviceID) { $driver.DeviceID } else { '' }
        }
    }
} catch {
    $drivers += [PSCustomObject]@{
        Dispositivo = 'Error'
        Fabricante = 'No se pudieron obtener controladores'
        Version = 'N/A'
        Fecha = 'N/A'
        Clase = 'N/A'
        Firmado = 'N/A'
        Estado = 'N/A'
        Proveedor = 'N/A'
        Problema = ''
        DeviceID = ''
    }
}

# Dispositivos con problema que Win32_PnPSignedDriver ni siquiera lista
# (sin ningún controlador instalado — antes invisibles del todo).
foreach ($idKey in $problemas.Keys) {
    if (-not $idsConDriver.Contains($idKey)) {
        $info = $problemas[$idKey]
        $claseInfo = if ($info.Clase -and $clasesEs.ContainsKey($info.Clase)) { $clasesEs[$info.Clase] }
                     elseif ($info.Clase) { $info.Clase }
                     else { 'Desconocido' }
        $drivers += [PSCustomObject]@{
            Dispositivo = if ($info.Nombre) { $info.Nombre } else { 'Desconocido' }
            Fabricante = if ($info.Fabricante) { $info.Fabricante } else { 'Desconocido' }
            Version = 'N/A'
            Fecha = 'N/A'
            Clase = $claseInfo
            Firmado = 'N/A'
            Estado = 'Sin controlador'
            Proveedor = 'N/A'
            Problema = $info.Desc
            DeviceID = $idKey
        }
    }
}

$json = $drivers | ConvertTo-Json -Depth 3
if ($drivers.Count -eq 1) { $json = "[$json]" }
$json
"""

PS_GET_AD_INFO = r"""
# Tres fallos distintos, tres mensajes distintos — antes el catch unico
# mostraba SIEMPRE "Instalar RSAT" pasara lo que pasara (incl. con RSAT ya
# instalado y funcionando, p.ej. equipo en WORKGROUP sin controlador de
# dominio que consultar). Eso hacia reinstalar RSAT sin necesidad, sin ver
# nunca el error real de PowerShell.
function Emitir-ErrorAD([string]$nombre, [string]$detalle) {
    @([PSCustomObject]@{
        Tipo = "Error"
        Nombre = $nombre
        SamAccountName = "N/A"
        Habilitado = "N/A"
        UltimaConexion = "N/A"
        DistinguishedName = $detalle
    }) | ConvertTo-Json
}

if (-not (Get-Module -ListAvailable -Name ActiveDirectory)) {
    Emitir-ErrorAD "RSAT no instalado" "Las herramientas RSAT de Active Directory no estan instaladas en este equipo. Pulsa 'Instalar RSAT' arriba."
    return
}

try {
    Import-Module ActiveDirectory -ErrorAction Stop
} catch {
    Emitir-ErrorAD "No se pudo cargar el modulo Active Directory" "RSAT esta instalado pero el modulo no cargo: $($_.Exception.Message)"
    return
}

try {
    $users = Get-ADUser -Filter * -Properties LastLogonDate, Enabled -ResultSetSize 100 -ErrorAction Stop | ForEach-Object {
        [PSCustomObject]@{
            Tipo = "Usuario"
            Nombre = $_.Name
            SamAccountName = $_.SamAccountName
            Habilitado = if ($_.Enabled) { "Si" } else { "No" }
            UltimaConexion = if ($_.LastLogonDate) { $_.LastLogonDate.ToString("dd/MM/yyyy HH:mm:ss") } else { "Nunca" }
            DistinguishedName = $_.DistinguishedName
        }
    }

    $users | ConvertTo-Json
} catch {
    Emitir-ErrorAD "No se pudo consultar Active Directory" "El modulo RSAT esta cargado correctamente, pero la consulta fallo: $($_.Exception.Message). Comprueba que el equipo este unido a un dominio y tenga conectividad con un controlador de dominio."
}
"""

# Políticas de Grupo (GPO de dominio + políticas Intune/MDM) — a diferencia
# de PS_GET_AD_INFO, esto NO depende de RSAT en ningún punto: dsregcmd y
# gpresult son binarios nativos de Windows, siempre presentes. Se parsea el
# XML de gpresult (/x), NUNCA el texto de gpresult /r: el texto sale
# localizado al idioma del sistema (comprobado en un Windows en español —
# "CONFIGURACIÓN DE EQUIPO", etc.), mientras que los nombres de elemento del
# XML (GPO, SecurityGroup, ExtensionStatus...) son fijos pase lo que pase.
PS_GET_GROUP_POLICY_INFO = r"""
$gp = @{}

# --- Estado de union (dominio / Entra ID / hibrido) via dsregcmd ---
# Los NOMBRES DE CLAVE de dsregcmd /status (AzureAdJoined, DomainJoined...)
# son fijos en ingles independientemente del idioma del sistema — solo los
# titulos de sección entre "+---+" cambian de idioma, no las claves.
try {
    $dsregTexto = (& dsregcmd /status 2>&1 | Out-String)
    function Obtener-ValorDsreg([string]$texto, [string]$clave) {
        $m = [regex]::Match($texto, "$clave\s*:\s*(.+)")
        if ($m.Success) { return $m.Groups[1].Value.Trim() }
        return 'N/A'
    }
    $gp.EstadoUnion = @{
        AzureAdJoined = Obtener-ValorDsreg $dsregTexto 'AzureAdJoined'
        DomainJoined = Obtener-ValorDsreg $dsregTexto 'DomainJoined'
        EnterpriseJoined = Obtener-ValorDsreg $dsregTexto 'EnterpriseJoined'
        TenantName = Obtener-ValorDsreg $dsregTexto 'TenantName'
        TenantId = Obtener-ValorDsreg $dsregTexto 'TenantId'
        DeviceId = Obtener-ValorDsreg $dsregTexto 'DeviceId'
        Dominio = try { (Get-CimInstance -ClassName Win32_ComputerSystem -ErrorAction Stop).Domain } catch { 'N/A' }
    }
} catch {
    $gp.EstadoUnion = @{
        AzureAdJoined = 'N/A'; DomainJoined = 'N/A'; EnterpriseJoined = 'N/A'
        TenantName = 'N/A'; TenantId = 'N/A'; DeviceId = 'N/A'; Dominio = 'N/A'
    }
}

# --- GPO aplicadas (gpresult /x -> XML, RSOP) ---
# gpresult SIEMPRE funciona, incluso sin dominio (muestra al menos la
# Directiva de grupo local) — tiene valor incluso en un equipo workgroup,
# por eso nunca se omite esta sección por completo.
function Resumen-RsopResultado($nodo) {
    if (-not $nodo) {
        return @{ Nombre = 'N/A'; Dominio = 'N/A'; OU = 'N/A'; UltimaActualizacion = 'N/A'; GPOs = @(); GruposSeguridad = 0 }
    }
    $gpos = @($nodo.GPO | ForEach-Object {
        @{ Nombre = $_.Name; Habilitada = ($_.Enabled -eq 'true'); AccesoDenegado = ($_.AccessDenied -eq 'true') }
    })
    $ultimaActualizacion = 'N/A'
    try {
        $tiempos = @($nodo.ExtensionStatus | ForEach-Object { try { [datetime]$_.EndTime } catch { $null } } | Where-Object { $_ })
        if ($tiempos.Count -gt 0) {
            $ultimaActualizacion = ($tiempos | Sort-Object -Descending | Select-Object -First 1).ToString('dd/MM/yyyy HH:mm')
        }
    } catch {}
    return @{
        Nombre = $nodo.Name
        Dominio = $nodo.Domain
        OU = $nodo.SOM
        UltimaActualizacion = $ultimaActualizacion
        GPOs = $gpos
        GruposSeguridad = @($nodo.SecurityGroup).Count
    }
}

try {
    # Ruta CORTA a propósito: gpresult /x rechaza rutas de más de 127
    # caracteres para el fichero de salida (comprobado — falla con "El
    # valor de la opción... no puede tener más de 127 caracteres" con una
    # ruta normal de %TEMP% de usuario ya de por sí larga).
    $tmpGpXml = Join-Path $env:TEMP "msys_gp_$([guid]::NewGuid().ToString('N').Substring(0,8)).xml"
    $salidaGpresult = & gpresult /x $tmpGpXml /f 2>&1
    if ((Test-Path $tmpGpXml)) {
        [xml]$xmlGp = Get-Content $tmpGpXml -Raw
        Remove-Item $tmpGpXml -Force -ErrorAction SilentlyContinue
        $gp.GPOEquipo = Resumen-RsopResultado $xmlGp.Rsop.ComputerResults
        $gp.GPOUsuario = Resumen-RsopResultado $xmlGp.Rsop.UserResults
        $gp.GPOError = ''
    } else {
        $gp.GPOEquipo = Resumen-RsopResultado $null
        $gp.GPOUsuario = Resumen-RsopResultado $null
        $gp.GPOError = "gpresult no generó el informe: $salidaGpresult"
    }
} catch {
    $gp.GPOEquipo = Resumen-RsopResultado $null
    $gp.GPOUsuario = Resumen-RsopResultado $null
    $gp.GPOError = "No se pudo ejecutar gpresult: $($_.Exception.Message)"
}

# --- Políticas Intune / MDM ---
# HKLM:\SOFTWARE\Microsoft\Enrollments tiene SIEMPRE decenas de subclaves
# en cualquier Windows 10/11, aunque el equipo NO esté gestionado — son
# "autoridades" locales internas del propio Windows (Local Authority,
# Cloud Authority, Deploy Authority...), NO inscripciones MDM reales.
# Contar solo la presencia de esa clave habría dado un falso positivo en
# TODOS los equipos (comprobado). Una inscripción MDM/Intune real se
# reconoce por ProviderID = 'MS DM Server' CON un UPN (correo) relleno.
# No se intenta volcar el detalle de políticas Intune aplicadas (CSPs):
# la API para eso (WMI Bridge, namespace root\cimv2\mdm\dmmap) no es
# estable ni está documentada de forma fiable para uso general — mejor
# no fingir un dato que no se puede dar con garantías.
try {
    $inscripciones = Get-ChildItem 'HKLM:\SOFTWARE\Microsoft\Enrollments' -ErrorAction SilentlyContinue | ForEach-Object {
        Get-ItemProperty -Path $_.PSPath -ErrorAction SilentlyContinue
    }
    $inscripcionReal = $inscripciones | Where-Object { $_.ProviderID -eq 'MS DM Server' -and $_.UPN } | Select-Object -First 1
    if ($inscripcionReal) {
        $gp.Intune = @{
            Inscrito = $true
            Cuenta = $inscripcionReal.UPN
            ServidorAdministracion = if ($inscripcionReal.DiscoveryServiceFullURL) { $inscripcionReal.DiscoveryServiceFullURL } else { 'N/A' }
        }
    } else {
        $gp.Intune = @{ Inscrito = $false; Cuenta = 'N/A'; ServidorAdministracion = 'N/A' }
    }
} catch {
    $gp.Intune = @{ Inscrito = $false; Cuenta = 'N/A'; ServidorAdministracion = 'N/A' }
}

$gp | ConvertTo-Json -Depth 6
"""

# Programas de inicio: registro (Run de HKLM 64/32 bits y HKCU) + carpetas
# de inicio (usuario actual y todos los usuarios). El estado Habilitado/
# Deshabilitado se lee de StartupApproved — la MISMA clave binaria que usa
# el Administrador de tareas de Windows (comprobado su formato contra
# entradas reales de este equipo): si no hay entrada de aprobación, la
# entrada está habilitada por defecto (nunca se ha tocado).
PS_GET_STARTUP_PROGRAMS = r"""
$items = @()

function Get-EstadoAprobacion($subclavePath, $nombreValor) {
    try {
        $val = (Get-ItemProperty -Path $subclavePath -Name $nombreValor -ErrorAction Stop).$nombreValor
        if ($val -and $val.Length -ge 1) {
            if ($val[0] -eq 3) { return 'Deshabilitado' }
            else { return 'Habilitado' }
        }
    } catch {}
    return 'Habilitado'
}

# Resuelve el ejecutable real de un valor de registro tipo "Run" (puede
# venir entre comillas, sin comillas sin argumentos, o sin comillas CON
# argumentos). Probar primero el valor COMPLETO tal cual (sin comillas)
# es imprescindible: cortar por el primer espacio partía en dos rutas
# como "C:\Program Files\Foo\Foo.exe" (sin argumentos) — comprobado que
# daba falso "no existe" en varias entradas reales de este equipo.
function Resolver-RutaEjecutable([string]$comando) {
    if ([string]::IsNullOrWhiteSpace($comando)) { return $null }
    $c = $comando.Trim()

    if ($c.StartsWith('"')) {
        $fin = $c.IndexOf('"', 1)
        if ($fin -gt 0) { return $c.Substring(1, $fin - 1) }
    }

    if (Test-Path -LiteralPath $c -PathType Leaf -ErrorAction SilentlyContinue) { return $c }

    $trozos = $c -split ' '
    $acumulado = ''
    for ($i = 0; $i -lt $trozos.Length; $i++) {
        $acumulado = if ($i -eq 0) { $trozos[0] } else { "$acumulado $($trozos[$i])" }
        if (Test-Path -LiteralPath $acumulado -PathType Leaf -ErrorAction SilentlyContinue) { return $acumulado }
    }

    return $trozos[0]
}

# "No existe" solo se afirma cuando SÍ parece una ruta completa (empieza
# por letra de unidad o \\) y aun así no se encuentra — un comando corto
# tipo "pythonw" (resoluble vía PATH, sin ruta completa en el valor) NUNCA
# se marca como huérfano: no hay forma fiable de comprobarlo y un falso
# positivo aquí es peor que no detectarlo.
function Get-ExisteFichero([string]$comando) {
    $ruta = Resolver-RutaEjecutable $comando
    if (-not $ruta) { return $true }
    if (Test-Path -LiteralPath $ruta -PathType Leaf -ErrorAction SilentlyContinue) { return $true }
    if ($ruta -notmatch '^[A-Za-z]:\\' -and $ruta -notmatch '^\\\\') { return $true }
    return $false
}

$fuentesRegistro = @(
    @{ Ruta = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run'; Aprobacion = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run'; Ubicacion = 'Registro (todos los usuarios)'; Hive = 'HKLM'; ClaveOrigen = 'Run' },
    @{ Ruta = 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Run'; Aprobacion = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run32'; Ubicacion = 'Registro (todos los usuarios, 32 bits)'; Hive = 'HKLM'; ClaveOrigen = 'Run32' },
    @{ Ruta = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run'; Aprobacion = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run'; Ubicacion = 'Registro (usuario actual)'; Hive = 'HKCU'; ClaveOrigen = 'Run' }
)

foreach ($fuente in $fuentesRegistro) {
    try {
        $props = Get-ItemProperty -Path $fuente.Ruta -ErrorAction Stop
        $props.PSObject.Properties | Where-Object { $_.Name -notmatch '^PS' } | ForEach-Object {
            $comandoActual = [string]$_.Value
            $items += @{
                Nombre = $_.Name
                Comando = $comandoActual
                Ubicacion = $fuente.Ubicacion
                Hive = $fuente.Hive
                ClaveOrigen = $fuente.ClaveOrigen
                ClaveValor = $_.Name
                Estado = Get-EstadoAprobacion $fuente.Aprobacion $_.Name
                Existe = Get-ExisteFichero $comandoActual
            }
        }
    } catch {}
}

$carpetas = @(
    @{ Ruta = [Environment]::GetFolderPath('Startup'); Ubicacion = 'Carpeta de inicio (usuario actual)' },
    @{ Ruta = [Environment]::GetFolderPath('CommonStartup'); Ubicacion = 'Carpeta de inicio (todos los usuarios)' }
)
# La aprobación de carpeta de inicio SIEMPRE vive en HKCU, aunque el .lnk
# esté físicamente en la carpeta común de Todos los Usuarios — así lo
# gestiona el propio Explorador de Windows.
$aprobacionCarpeta = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\StartupFolder'
foreach ($carpeta in $carpetas) {
    if (Test-Path $carpeta.Ruta) {
        Get-ChildItem -Path $carpeta.Ruta -File -ErrorAction SilentlyContinue | ForEach-Object {
            $items += @{
                Nombre = $_.BaseName
                Comando = $_.FullName
                Ubicacion = $carpeta.Ubicacion
                Hive = 'HKCU'
                ClaveOrigen = 'StartupFolder'
                ClaveValor = $_.Name
                Estado = Get-EstadoAprobacion $aprobacionCarpeta $_.Name
                Existe = $true  # recién enumerado del disco, siempre existe en este instante
            }
        }
    }
}

@($items) | ConvertTo-Json -Depth 3
"""

PS_GET_USERS = r"""
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8

    $usuarios = @()

    # Pre-construir caché de grupos (mucho más rápido que iterar por usuario)
    $gruposCache = @{}
    try {
        Get-LocalGroup -ErrorAction SilentlyContinue | ForEach-Object {
            $nombreGrupo = $_.Name
            try {
                $miembros = Get-LocalGroupMember -Group $nombreGrupo -ErrorAction SilentlyContinue
                foreach ($m in $miembros) {
                    # 5.4.0: por SID, no por ObjectClass -eq 'User' (en Windows en español
                    # es «Usuario»: ningún usuario salía en ningún grupo y MASys no veía
                    # ni un solo administrador). El SID tampoco depende del nombre.
                    $sidKey = [string]$m.SID.Value
                    if ($sidKey) {
                        if (-not $gruposCache.ContainsKey($sidKey)) { $gruposCache[$sidKey] = @() }
                        $gruposCache[$sidKey] += $nombreGrupo
                    }
                }
            } catch {}
        }
    } catch {}

    $nombresistema = @('Administrador','Administrator','Invitado','Guest','DefaultAccount','WDAGUtilityAccount','ASPNET')

    # Una cuenta LOCAL (SAM) puede estar vinculada a una cuenta Microsoft
    # (o de Entra ID) para el inicio de sesión — Windows deja un registro
    # cacheado de esa vinculación con el email/UPN real usado, aunque el
    # inicio de sesión del día a día sea con PIN/Windows Hello. Esto es
    # independiente de PasswordRequired (que solo indica si la DIRECTIVA
    # permite contraseña en blanco, no si la cuenta tiene o no una
    # contraseña real configurada).
    function Obtener-CuentaVinculada($sid) {
        try {
            $cacheKey = Get-ChildItem "HKLM:\SOFTWARE\Microsoft\IdentityStore\Cache\$sid\IdentityCache" -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($cacheKey) {
                $email = (Get-ItemProperty -Path $cacheKey.PSPath -ErrorAction SilentlyContinue).UserName
                if ($email) { return $email }
            }
        } catch {}
        return $null
    }

    # 1. USUARIOS LOCALES
    $usuariosLocales = Get-LocalUser -ErrorAction SilentlyContinue | ForEach-Object {
        $user = $_
        $tipo = if ($nombresistema -contains $user.Name) { "Sistema (Local)" } else { "Local" }
        $estado = if ($user.Enabled) { "Activo" } else { "Inactivo" }
        $ultimaConexion = if ($user.LastLogon) { $user.LastLogon.ToString("dd/MM/yyyy HH:mm:ss") } else { "Nunca" }
        $fechaCreacion = if ($user.PasswordLastSet) { $user.PasswordLastSet.ToString("dd/MM/yyyy HH:mm:ss") } else { "N/A" }

        $nomKey = [string]$user.SID.Value
        $gruposStr = if ($gruposCache.ContainsKey($nomKey) -and $gruposCache[$nomKey].Count -gt 0) {
            ($gruposCache[$nomKey] -join ", ")
        } else { "Ninguno" }

        $emailVinculado = Obtener-CuentaVinculada $user.SID.Value
        $cuentaVinculada = if ($emailVinculado) { "Microsoft ($emailVinculado)" } else { "Local" }

        [PSCustomObject]@{
            Nombre = $user.Name
            NombreCompleto = if ($user.FullName) { $user.FullName } else { "" }
            Tipo = $tipo
            Dominio = $env:COMPUTERNAME
            Estado = $estado
            UltimaConexion = $ultimaConexion
            FechaCreacion = $fechaCreacion
            Descripcion = if ($user.Description) { $user.Description } else { "" }
            Grupos = $gruposStr
            SID = $user.SID.Value
            PasswordExpires = if ($user.PasswordExpires) { "Sí" } else { "No" }
            PasswordRequired = if ($user.PasswordRequired) { "Sí" } else { "No" }
            CuentaVinculada = $cuentaVinculada
        }
    }

    $usuarios += $usuariosLocales

    # 2. USUARIOS DE DOMINIO Y ENTRA ID (AZURE AD)
    # IMPORTANTE: se ejecuta SIEMPRE, no solo si $esDominio. PartOfDomain
    # es $false en un equipo unido SOLO a Entra ID (Azure AD Joined) —
    # el caso más común hoy en entornos M365 sin AD local — por lo que
    # filtrar por $esDominio dejaba esta sección entera sin ejecutarse
    # nunca en esos equipos, y por tanto sin detectar jamás cuentas de
    # Entra ID con perfil local.
    try {
        $perfilesUsuario = Get-WmiObject -Class Win32_UserProfile -ErrorAction SilentlyContinue | Where-Object {
            -not $_.Special -and $_.LocalPath -notmatch 'systemprofile'
        }

        foreach ($perfil in $perfilesUsuario) {
            try {
                $sid = $perfil.SID
                # Las cuentas de Entra ID (Azure AD) con perfil local usan
                # SID sintéticas con prefijo S-1-12-1-, que NO se resuelven
                # vía Translate([NTAccount]) (lanzan excepción) al no ser
                # cuentas de Windows/AD clásicas.
                $esEntraId = $sid -like 'S-1-12-1-*'

                $nombreUsuario = $null
                $dom = $null
                $tipoPerfil = $null
                $descripcionPerfil = $null

                if ($esEntraId) {
                    $tipoPerfil = "Entra ID (Azure AD)"
                    $dom = "Entra ID (Azure AD)"
                    $nombreUsuario = Split-Path $perfil.LocalPath -Leaf
                    $descripcionPerfil = "Cuenta de Entra ID (Azure AD) con perfil local"
                    # Intentar recuperar el UPN real cacheado por Windows
                    # para esta SID (mismo mecanismo que usan las
                    # herramientas de diagnóstico de identidad de Windows).
                    $upn = Obtener-CuentaVinculada $sid
                    if ($upn) { $nombreUsuario = $upn }
                } else {
                    try {
                        $usuario = (New-Object System.Security.Principal.SecurityIdentifier($sid)).Translate([System.Security.Principal.NTAccount]).Value
                        if ($usuario -match '\\') {
                            $partes = $usuario -split '\\'
                            $dom = $partes[0]
                            $nombreUsuario = $partes[1]
                            $tipoPerfil = "Dominio"
                            $descripcionPerfil = "Usuario de dominio con perfil local"
                        }
                    } catch {
                        # SID no resoluble (p. ej. controlador de dominio
                        # inaccesible en este momento): registrar igual con
                        # el nombre de carpeta del perfil en vez de
                        # descartarlo silenciosamente, como hacía antes.
                        $dom = "Dominio (no resuelto)"
                        $nombreUsuario = Split-Path $perfil.LocalPath -Leaf
                        $tipoPerfil = "Dominio (no resuelto)"
                        $descripcionPerfil = "SID de dominio no resoluble en este momento"
                    }
                }

                if ($nombreUsuario) {
                    # Verificar si ya existe en la lista (evita duplicar
                    # cuentas locales cuyo perfil también aparece aquí)
                    $existe = $usuarios | Where-Object { $_.Nombre -eq $nombreUsuario -and $_.Dominio -eq $dom }

                    if (-not $existe) {
                        $ultimaConexionPerfil = if ($perfil.LastUseTime) {
                            [Management.ManagementDateTimeConverter]::ToDateTime($perfil.LastUseTime).ToString("dd/MM/yyyy HH:mm:ss")
                        } else {
                            "N/A"
                        }

                        $usuarioObj = [PSCustomObject]@{
                            Nombre = $nombreUsuario
                            NombreCompleto = ""
                            Tipo = $tipoPerfil
                            Dominio = $dom
                            Estado = "Activo"
                            UltimaConexion = $ultimaConexionPerfil
                            FechaCreacion = "N/A"
                            Descripcion = $descripcionPerfil
                            Grupos = "N/A"
                            SID = $sid
                            PasswordExpires = "N/A"
                            PasswordRequired = "N/A"
                            CuentaVinculada = $tipoPerfil
                        }

                        $usuarios += $usuarioObj
                    }
                }
            } catch {
                # Ignorar errores al procesar perfiles individuales
            }
        }
    } catch {
        # No se pudieron obtener usuarios de dominio/Entra ID
    }

    # 3. USUARIOS DEL SISTEMA (CUENTAS ESPECIALES)
    try {
        # Cuentas de sistema conocidas
        $cuentasSistema = @(
            @{Nombre="SYSTEM"; Descripcion="Cuenta del sistema local"; SID="S-1-5-18"},
            @{Nombre="LOCAL SERVICE"; Descripcion="Cuenta de servicio local"; SID="S-1-5-19"},
            @{Nombre="NETWORK SERVICE"; Descripcion="Cuenta de servicio de red"; SID="S-1-5-20"}
        )

        foreach ($cuenta in $cuentasSistema) {
            $usuarios += [PSCustomObject]@{
                Nombre = $cuenta.Nombre
                NombreCompleto = ""
                Tipo = "Sistema"
                Dominio = "NT AUTHORITY"
                Estado = "Activo"
                UltimaConexion = "N/A"
                FechaCreacion = "N/A"
                Descripcion = $cuenta.Descripcion
                Grupos = "N/A"
                SID = $cuenta.SID
                PasswordExpires = "N/A"
                PasswordRequired = "N/A"
                CuentaVinculada = "Sistema"
            }
        }
    } catch {
        # Ignorar si no se pueden añadir cuentas de sistema
    }

    $usuarios | ConvertTo-Json -Depth 3

} catch {
    @([PSCustomObject]@{
        Nombre = "Error"
        NombreCompleto = "No se pudieron obtener usuarios"
        Tipo = "Error"
        Dominio = "N/A"
        Estado = "N/A"
        UltimaConexion = "N/A"
        FechaCreacion = "N/A"
        Descripcion = $_.Exception.Message
        Grupos = "N/A"
        SID = "N/A"
        PasswordExpires = "N/A"
        PasswordRequired = "N/A"
    }) | ConvertTo-Json
}
"""

PS_GET_ACTIVE_SESSIONS = r"""
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
    $result = @()

    # Sesiones interactivas via quser
    # FIX: dividir por "2+ espacios" (-split '\s{2,}') asume que TODAS las
    # columnas están presentes. Cuando una sesión está DESCONECTADA,
    # SESSIONNAME sale vacío en la salida real de quser — esto desplaza
    # todas las columnas siguientes una posición, mezclando ID/Estado/
    # Inactividad/Inicio entre sí (comprobado con una salida simulada de
    # sesión desconectada).
    #
    # Un primer intento de arreglo detectaba las columnas por POSICIÓN,
    # localizando el texto de la cabecera (SESSIONNAME/ID/STATE...) — pero
    # en este equipo la cabecera real sale en español CON LA CODIFICACIÓN
    # ROTA ("SESIÓN" ilegible al capturar la salida de query.exe), así que
    # ninguno de los fragmentos esperados coincidía. En vez de intentar
    # traducir/adivinar la cabecera, se parsea por TOKENS usando el ID
    # (SIEMPRE numérico, en cualquier idioma) como ancla: si el primer
    # token tras el usuario es puramente numérico, no hay SESSIONNAME
    # (sesión desconectada) y ese token ES el ID; si no, es el nombre de
    # sesión y el ID viene justo después. No depende de leer ni traducir
    # ninguna palabra de la cabecera.
    try {
        $quserOut = & query user 2>$null
        if ($quserOut -and $quserOut.Count -gt 1) {
            foreach ($lineaOriginal in ($quserOut | Select-Object -Skip 1)) {
                if ($lineaOriginal.Trim() -eq '') { continue }
                $tokens = ($lineaOriginal -replace '^>', ' ').Trim() -split '\s+'
                if ($tokens.Count -lt 4) { continue }

                $usuario = $tokens[0]
                $idx = 1
                if ($tokens[$idx] -match '^\d+$') {
                    $sesion = ''
                } else {
                    $sesion = $tokens[$idx]
                    $idx++
                }
                if ($idx -ge $tokens.Count) { continue }
                $idVal = $tokens[$idx]; $idx++
                $estado = if ($idx -lt $tokens.Count) { $tokens[$idx] } else { '' }; $idx++
                $idle   = if ($idx -lt $tokens.Count) { $tokens[$idx] } else { '' }; $idx++
                $logon  = if ($idx -lt $tokens.Count) { ($tokens[$idx..($tokens.Count - 1)] -join ' ') } else { '' }

                $result += [PSCustomObject]@{
                    Usuario        = $usuario
                    Sesion         = if ($sesion) { $sesion } else { 'N/A' }
                    ID             = $idVal
                    Estado         = $estado
                    TiempoInactivo = if ($idle) { $idle } else { 'N/A' }
                    Inicio         = if ($logon) { $logon } else { 'N/A' }
                    Tipo           = 'Interactiva'
                }
            }
        }
    } catch {}

    # Sesiones de red via WMI
    try {
        $wmiSessions = Get-WmiObject Win32_LogonSession -ErrorAction SilentlyContinue |
            Where-Object { $_.LogonType -in @(3,10) }
        foreach ($s in $wmiSessions) {
            $assoc = Get-WmiObject -Query "ASSOCIATORS OF {Win32_LogonSession.LogonId='$($s.LogonId)'} WHERE AssocClass=Win32_LoggedOnUser" -ErrorAction SilentlyContinue
            $userStr = if ($assoc) { ($assoc | ForEach-Object { ($_.Antecedent -split '"')[1] }) -join ', ' } else { 'N/A' }
            if ($userStr -match 'SYSTEM|LOCAL SERVICE|NETWORK SERVICE') { continue }
            $inicio = if ($s.StartTime) {
                try { [Management.ManagementDateTimeConverter]::ToDateTime($s.StartTime).ToString('dd/MM/yyyy HH:mm:ss') }
                catch { 'N/A' }
            } else { 'N/A' }
            $result += [PSCustomObject]@{
                Usuario        = $userStr
                Sesion         = "WMI-$($s.LogonId)"
                ID             = $s.LogonId
                Estado         = 'Activa'
                TiempoInactivo = 'N/A'
                Inicio         = $inicio
                Tipo           = if ($s.LogonType -eq 10) { 'RDP' } else { 'Red' }
            }
        }
    } catch {}

    if ($result.Count -gt 0) { $result | ConvertTo-Json -Depth 3 } else { '[]' }
} catch { '[]' }
"""

PS_GET_RDP_CONNECTION_HISTORY = r"""
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8

    # Evento 1149 (Microsoft-Windows-TerminalServices-RemoteConnectionManager
    # /Operational): "autenticación de usuario correcta" en una conexión RDP,
    # con la IP de origen real. Se leen las Properties[] ESTRUCTURADAS del
    # evento (Usuario/Dominio/IP), NO el texto del mensaje renderizado — el
    # mensaje sale localizado al idioma del sistema y parsearlo por texto
    # rompería en un Windows en otro idioma.
    $eventos = Get-WinEvent -LogName "Microsoft-Windows-TerminalServices-RemoteConnectionManager/Operational" `
        -FilterXPath "*[System[(EventID=1149)]]" -MaxEvents 100 -ErrorAction SilentlyContinue

    $resultados = @()
    foreach ($ev in $eventos) {
        $props = $ev.Properties
        if ($props.Count -lt 3) { continue }
        $resultados += [PSCustomObject]@{
            Usuario = [string]$props[0].Value
            Dominio = [string]$props[1].Value
            IP      = [string]$props[2].Value
            Fecha   = $ev.TimeCreated.ToString('dd/MM/yyyy HH:mm:ss')
        }
    }

    if ($resultados.Count -eq 0) { Write-Output "[]" }
    elseif ($resultados.Count -eq 1) { @($resultados) | ConvertTo-Json }
    else { $resultados | ConvertTo-Json }
} catch { Write-Output "[]" }
"""

PS_GET_BLUETOOTH = r"""
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8

    $bt = @()

    # Traduccion best-effort del Status de Config Manager (Get-PnpDevice y
    # Win32_PnPEntity comparten en gran parte el mismo vocabulario en
    # ingles fijo, no localizado) — lo no mapeado se deja tal cual, mejor
    # mostrar el original que una traduccion inventada.
    function Traducir-EstadoDispositivo($estado) {
        switch ($estado) {
            'OK'         { 'Correcto' }
            'Error'      { 'Error' }
            'Degraded'   { 'Degradado' }
            'Unknown'    { 'Desconocido' }
            'Starting'   { 'Iniciando' }
            'Stopping'   { 'Deteniendo' }
            'Stressed'   { 'Sobrecargado' }
            'NonRecover' { 'Error no recuperable' }
            'No Contact' { 'Sin contacto' }
            'Lost Comm'  { 'Comunicación perdida' }
            'Pred Fail'  { 'Fallo previsto' }
            'Service'    { 'En mantenimiento' }
            default      { $estado }
        }
    }

    # Dispositivos Bluetooth por clase PnP
    try {
        $btDevs = Get-PnpDevice -Class Bluetooth -ErrorAction SilentlyContinue
        foreach ($d in $btDevs) {
            $bt += [PSCustomObject]@{
                Nombre      = if ($d.FriendlyName) { $d.FriendlyName } else { $d.Name }
                Fabricante  = if ($d.Manufacturer) { $d.Manufacturer } else { 'N/A' }
                Estado      = Traducir-EstadoDispositivo $d.Status
                Clase       = 'Bluetooth'
                InstanceId  = $d.InstanceId
            }
        }
    } catch {}

    # También buscar por descripción en otros adaptadores
    try {
        $btWmi = Get-WmiObject Win32_PnPEntity -ErrorAction SilentlyContinue |
            Where-Object { $_.Description -like '*Bluetooth*' -or $_.Name -like '*Bluetooth*' }
        foreach ($d in $btWmi) {
            $yaExiste = $bt | Where-Object { $_.InstanceId -eq $d.DeviceID }
            if (-not $yaExiste) {
                $bt += [PSCustomObject]@{
                    Nombre     = $d.Name
                    Fabricante = if ($d.Manufacturer) { $d.Manufacturer } else { 'N/A' }
                    Estado     = Traducir-EstadoDispositivo $d.Status
                    Clase      = 'Bluetooth (WMI)'
                    InstanceId = $d.DeviceID
                }
            }
        }
    } catch {}

    if ($bt.Count -gt 0) { $bt | ConvertTo-Json -Depth 2 } else { '[]' }
} catch { '[]' }
"""

PS_GET_SYSTEM_EVENTS = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$fechaInicio = (Get-Date).AddDays(-DIAS_PLACEHOLDER)
$result = [System.Collections.Generic.List[object]]::new()

function Agregar-Evento($e, $nivel) {
    $msg = if ($e.Message) {
        $m = $e.Message -replace '\r?\n', ' '
        if ($m.Length -gt 500) { $m.Substring(0, 500) } else { $m }
    } else { '' }
    $result.Add([PSCustomObject]@{
        Fecha    = $e.TimeCreated.ToString('dd/MM/yyyy HH:mm:ss')
        Nivel    = $nivel
        Log      = $e.LogName
        Fuente   = $e.ProviderName
        IdEvento = [string]$e.Id
        Mensaje  = $msg
    })
}

# Security se procesa APARTE, con su propio cupo (300) que no compite con
# los 800 de Application/System: si se metiera en el mismo bucle con el
# mismo tope compartido, en un equipo con mucho ruido en esos dos logs (el
# cupo de 800 se agota solo con ellos) el checkbox de Security no llegaba
# a aportar nada aunque estuviera marcado, porque el bucle cortaba antes
# de procesarlo — se comprobó en un equipo real con ese problema.
if ('INCLUIR_SECURITY_PLACEHOLDER' -eq 'true') {
    try {
        # El log de Seguridad NO usa Nivel (Crítico/Error/Advertencia) para
        # sus eventos de auditoría — Level=0 ("Información") en TODOS,
        # éxito o fallo — usa Keywords. Audit Failure (intentos de acceso
        # denegados, inicios de sesión fallidos...) es lo que interesa
        # aquí; Audit Success sería tan ruidoso como sacar los eventos
        # informativos de Application/System sin filtrar.
        $evsSec = Get-WinEvent -FilterHashtable @{
            LogName   = 'Security'
            StartTime = $fechaInicio
            Keywords  = 4503599627370496  # Audit Failure
        } -MaxEvents 300 -ErrorAction SilentlyContinue
        foreach ($e in $evsSec) { Agregar-Evento $e 'Advertencia' }
    } catch {}
}

foreach ($logName in @('Application', 'System')) {
    try {
        # Level=1,2,3 directamente en el filtro del servidor — antes se
        # pedían los 600 eventos MÁS RECIENTES de CUALQUIER nivel y se
        # filtraba por nivel después: en un log ruidoso de eventos
        # informativos, un Crítico/Error/Advertencia algo más antiguo
        # podía quedar fuera del cupo de 600 antes de llegar a filtrarlo,
        # aunque estuviera dentro del rango de días pedido. Filtrando por
        # nivel en el propio Get-WinEvent, los 600 ya son solo de los
        # niveles que interesan.
        $evs = Get-WinEvent -FilterHashtable @{
            LogName   = $logName
            StartTime = $fechaInicio
            Level     = 1,2,3
        } -MaxEvents 600 -ErrorAction SilentlyContinue
        if (-not $evs) { continue }
        foreach ($e in $evs) {
            $nivel = switch ($e.Level) {
                1 { 'Critico' } 2 { 'Error' } 3 { 'Advertencia' }
                default { if ($e.LevelDisplayName) { $e.LevelDisplayName } else { [string]$e.Level } }
            }
            Agregar-Evento $e $nivel
            if ($result.Count -ge 1100) { break }
        }
    } catch {}
    if ($result.Count -ge 1100) { break }
}

# Sin 'catch' envolvente: antes CUALQUIER fallo (incluso uno real, no solo
# "sin eventos") se convertía en '[]', indistinguible de un resultado
# legítimo de 0 eventos — el mismo bug ya corregido en Actualizaciones y
# Controladores, aquí además ocultado dos veces (aquí Y en el lado Python).
# Si algo revienta de verdad ahora, PowerShell devuelve un error real y
# run_powershell_json lo detecta como fallo, no como "0 encontrados".
$lista = @($result)
if ($lista.Count -eq 0) {
    "[]"
} elseif ($lista.Count -eq 1) {
    "[" + ($lista | ConvertTo-Json -Depth 2 -Compress) + "]"
} else {
    $lista | ConvertTo-Json -Depth 2 -Compress
}
"""

# ---------------------------
# Secciones nuevas: Seguridad, Discos Duros, Copias de Seguridad, Licencias
# ---------------------------
PS_GET_SECURITY_INFO = r"""
$sec = @{}
try {
    $defender = Get-MpComputerStatus -ErrorAction SilentlyContinue
    $sec.Defender = @{
        Habilitado = if ($defender.AntivirusEnabled) { 'Sí' } else { 'No' }
        ProteccionTiempoReal = if ($defender.RealTimeProtectionEnabled) { 'Activa' } else { 'INACTIVA' }
        UltimaActualizacion = if ($defender.AntivirusSignatureLastUpdated) { $defender.AntivirusSignatureLastUpdated.ToString('dd/MM/yyyy HH:mm') } else { 'N/A' }
    }
} catch {
    $sec.Defender = @{ Habilitado = 'N/A'; ProteccionTiempoReal = 'N/A'; UltimaActualizacion = 'N/A' }
}

try {
    $bitlocker = Get-BitLockerVolume -ErrorAction SilentlyContinue
    $sec.BitLocker = @($bitlocker | ForEach-Object { @{ Unidad = $_.MountPoint; Estado = $_.ProtectionStatus.ToString() } })
} catch {
    $sec.BitLocker = @()
}

try {
    $secureBoot = Confirm-SecureBootUEFI -ErrorAction SilentlyContinue
    $sec.SecureBoot = if ($secureBoot) { 'Habilitado' } else { 'Deshabilitado' }
} catch {
    $sec.SecureBoot = 'No disponible (BIOS heredado o sin soporte)'
}

try {
    $tpm = Get-Tpm -ErrorAction SilentlyContinue
    if ($tpm -and $tpm.TpmPresent) {
        $sec.TPM = if ($tpm.TpmReady) { 'Presente y listo' } else { 'Presente pero no listo' }
    } else {
        $sec.TPM = 'No presente'
    }
} catch {
    $sec.TPM = 'N/A'
}

try {
    # PSCustomObject y no tabla hash: Sort-Object -Property no sabe leer las claves de una tabla hash y con
    # -Unique las daba todas por iguales (comprobado: de 35 puertos en escucha distintos quedaba UNO). Sin el tope
    # de 100 conexiones, que cortaba la lista antes de quitar los repetidos (IPv4 + IPv6 del mismo puerto).
    $ports = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
        $proc = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
        [PSCustomObject]@{ Puerto = [int]$_.LocalPort; Direccion = $_.LocalAddress; Proceso = if ($proc) { $proc.ProcessName } else { 'N/A' } }
    }
    $sec.Puertos = @($ports | Sort-Object -Property Puerto -Unique)
} catch {
    $sec.Puertos = @()
}

# Escritorio Remoto (RDP): habilitado, NLA y puerto — misma consulta que
# la pestaña Sistema (registro, no duplica ninguna llamada costosa), pero
# la ALERTA de Soporte vive aquí en Seguridad, no en Sistema.
try {
    $rdpDeny = (Get-ItemProperty -Path 'HKLM:\System\CurrentControlSet\Control\Terminal Server' -Name fDenyTSConnections -ErrorAction SilentlyContinue).fDenyTSConnections
    $rdpHabilitado = if ($null -ne $rdpDeny) { $rdpDeny -eq 0 } else { $false }
    $nla = (Get-ItemProperty -Path 'HKLM:\System\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp' -Name UserAuthentication -ErrorAction SilentlyContinue).UserAuthentication
    $nlaActivo = if ($null -ne $nla) { $nla -eq 1 } else { $false }
    $rdpPuerto = (Get-ItemProperty -Path 'HKLM:\System\CurrentControlSet\Control\Terminal Server\WinStations\RDP-Tcp' -Name PortNumber -ErrorAction SilentlyContinue).PortNumber
    $sec.EscritorioRemoto = @{
        Habilitado = if ($rdpHabilitado) { 'Sí' } else { 'No' }
        NLA = if ($rdpHabilitado) { if ($nlaActivo) { 'Activo' } else { 'INACTIVO (riesgo)' } } else { 'N/A' }
        Puerto = if ($rdpPuerto) { [string]$rdpPuerto } else { '3389 (por defecto)' }
    }
} catch {
    $sec.EscritorioRemoto = @{ Habilitado = 'N/A'; NLA = 'N/A'; Puerto = 'N/A' }
}

# Antivirus REAL (no asumir que siempre es Defender): SecurityCenter2 lista
# CUALQUIER producto registrado (Defender o de terceros — Norton/McAfee/
# Kaspersky/Bitdefender...). El estado detallado (activo/inactivo) de
# Defender ya se saca de forma fiable arriba con Get-MpComputerStatus; para
# productos de terceros NO se intenta decodificar el bit de productState
# (es un formato poco documentado y no hay forma de verificarlo en este
# equipo, que solo tiene Defender) — solo se informa de que están
# registrados en el Centro de seguridad de Windows.
try {
    $avProductos = Get-CimInstance -Namespace root\SecurityCenter2 -ClassName AntiVirusProduct -ErrorAction SilentlyContinue
    $sec.Antivirus = @($avProductos | ForEach-Object {
        $esDefender = $_.displayName -match 'Windows Defender|Microsoft Defender'
        @{
            Nombre = $_.displayName
            Estado = if ($esDefender) { $sec.Defender.ProteccionTiempoReal } else { 'Registrado en el Centro de seguridad de Windows' }
        }
    })
} catch {
    $sec.Antivirus = @()
}

# Firewall: resumen de los 3 perfiles — la pestaña Firewall ya tiene el
# detalle completo de reglas, esto es solo una referencia rápida aquí.
try {
    $perfiles = Get-NetFirewallProfile -ErrorAction SilentlyContinue
    $sec.Firewall = @{}
    foreach ($p in $perfiles) {
        $sec.Firewall[$p.Name] = if ($p.Enabled) { 'Activo' } else { 'INACTIVO' }
    }
} catch {
    $sec.Firewall = @{}
}

# Endurecimiento (hardening): 5 comprobaciones clásicas agrupadas en una
# sola ficha — UAC, SMBv1 (vector de WannaCry), PowerShell Remoting (WinRM),
# AutoRun/AutoPlay de unidades extraíbles, y si la cuenta Administrador
# integrada (SID terminado en -500, independientemente de si está
# renombrada) sigue habilitada.
try {
    $uac = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System' -Name EnableLUA -ErrorAction SilentlyContinue).EnableLUA
    $uacTexto = if ($null -eq $uac) { 'N/A' } elseif ($uac -eq 1) { 'Activo' } else { 'INACTIVO (riesgo)' }
} catch { $uacTexto = 'N/A' }

try {
    $smb1 = (Get-SmbServerConfiguration -ErrorAction SilentlyContinue).EnableSMB1Protocol
    $smb1Texto = if ($null -eq $smb1) { 'N/A' } elseif ($smb1) { 'HABILITADO (riesgo — vector de WannaCry)' } else { 'Deshabilitado' }
} catch { $smb1Texto = 'N/A' }

try {
    $winrm = Get-Service WinRM -ErrorAction SilentlyContinue
    $winrmTexto = if ($winrm) { "$($winrm.Status) ($($winrm.StartType))" } else { 'N/A' }
} catch { $winrmTexto = 'N/A' }

try {
    $autorun = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer' -Name NoDriveTypeAutoRun -ErrorAction SilentlyContinue).NoDriveTypeAutoRun
    # 255 = deshabilitado en todas las unidades; sin valor = comportamiento
    # por defecto de Windows (no necesariamente vulnerable, pero sin
    # endurecer explícitamente).
    $autorunTexto = if ($null -eq $autorun) { 'Por defecto (sin endurecer)' } elseif ($autorun -eq 255) { 'Deshabilitado' } else { "Parcial (valor: $autorun)" }
} catch { $autorunTexto = 'N/A' }

try {
    $adminLocal = Get-LocalUser -ErrorAction SilentlyContinue | Where-Object { $_.SID -like '*-500' }
    $adminTexto = if ($adminLocal) { "$($adminLocal.Name) — $(if ($adminLocal.Enabled) { 'HABILITADA (riesgo)' } else { 'Deshabilitada' })" } else { 'N/A' }
} catch { $adminTexto = 'N/A' }

# Cuenta Invitado integrada (SID terminado en -501) — mismo criterio que
# la cuenta Administrador, independientemente del nombre/idioma.
try {
    $guestLocal = Get-LocalUser -ErrorAction SilentlyContinue | Where-Object { $_.SID -like '*-501' }
    $guestTexto = if ($guestLocal) { "$($guestLocal.Name) — $(if ($guestLocal.Enabled) { 'HABILITADA (riesgo)' } else { 'Deshabilitada' })" } else { 'N/A' }
} catch { $guestTexto = 'N/A' }

# LSA Protection (RunAsPPL): protege LSASS como proceso protegido —
# mitigación real frente a herramientas de robo de credenciales en
# memoria (Mimikatz y similares). 1 = activo con bloqueo UEFI, 2 = activo
# sin bloqueo UEFI, 0/sin valor = inactivo.
try {
    $lsa = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Lsa' -Name RunAsPPL -ErrorAction SilentlyContinue).RunAsPPL
    $lsaTexto = if ($null -eq $lsa -or $lsa -eq 0) { 'INACTIVO (riesgo)' } elseif ($lsa -eq 1) { 'Activo (con bloqueo UEFI)' } else { 'Activo' }
} catch { $lsaTexto = 'N/A' }

# Credential Guard: aislamiento de credenciales en un contenedor virtualizado
# (VBS) — requiere Windows Enterprise/Education + hardware compatible, así
# que "no activo" es habitual e informativo, no necesariamente un fallo de
# configuración en equipos Pro/Home.
try {
    $dg = Get-CimInstance -ClassName Win32_DeviceGuard -Namespace root\Microsoft\Windows\DeviceGuard -ErrorAction SilentlyContinue
    $cgActivo = $dg -and $dg.SecurityServicesRunning -contains 1
    $credGuardTexto = if ($cgActivo) { 'Activo' } else { 'No activo (requiere Windows Enterprise/Education + hardware compatible)' }
} catch { $credGuardTexto = 'N/A' }

# Protector de pantalla con contraseña — comprobación clásica (registro de
# usuario), no cubre el mecanismo más moderno de "bloqueo tras inactividad"
# de Configuración > Cuentas > Opciones de inicio de sesión, que Windows no
# expone de forma fiable por registro/WMI.
try {
    $ssActivo = (Get-ItemProperty 'HKCU:\Control Panel\Desktop' -Name ScreenSaveActive -ErrorAction SilentlyContinue).ScreenSaveActive
    $ssSeguro = (Get-ItemProperty 'HKCU:\Control Panel\Desktop' -Name ScreenSaverIsSecure -ErrorAction SilentlyContinue).ScreenSaverIsSecure
    $ssTimeout = (Get-ItemProperty 'HKCU:\Control Panel\Desktop' -Name ScreenSaveTimeOut -ErrorAction SilentlyContinue).ScreenSaveTimeOut
    if ($ssActivo -eq '1' -and $ssSeguro -eq '1') {
        $minutos = if ($ssTimeout) { [math]::Round([int]$ssTimeout / 60) } else { 0 }
        $protectorTexto = "Activo con contraseña (espera: $minutos min)"
    } elseif ($ssActivo -eq '1') {
        $protectorTexto = 'Activo SIN contraseña (riesgo)'
    } else {
        $protectorTexto = 'Inactivo (riesgo)'
    }
} catch { $protectorTexto = 'N/A' }

$sec.Endurecimiento = @{
    UAC = $uacTexto
    SMBv1 = $smb1Texto
    PSRemoting = $winrmTexto
    AutoRun = $autorunTexto
    CuentaAdmin = $adminTexto
    CuentaInvitado = $guestTexto
    LSAProtection = $lsaTexto
    CredentialGuard = $credGuardTexto
    ProtectorPantalla = $protectorTexto
}

# Política de contraseñas local — secedit exporta a un INI con claves FIJAS
# en inglés (MinimumPasswordLength/PasswordComplexity/...) independientes
# del idioma del sistema, a diferencia de 'net accounts' que devuelve texto
# localizado poco fiable de parsear.
try {
    $tmpSecPol = Join-Path $env:TEMP "msys_secpol_$([guid]::NewGuid().ToString('N')).cfg"
    secedit /export /cfg $tmpSecPol /areas SECURITYPOLICY | Out-Null
    $contenidoSecPol = Get-Content $tmpSecPol -ErrorAction SilentlyContinue
    Remove-Item $tmpSecPol -Force -ErrorAction SilentlyContinue

    function Get-ValorSecPol($contenido, [string]$clave) {
        $linea = $contenido | Where-Object { $_ -match "^\s*$clave\s*=" } | Select-Object -First 1
        if ($linea) { return ($linea -split '=')[1].Trim() }
        return $null
    }

    $longMin = Get-ValorSecPol $contenidoSecPol 'MinimumPasswordLength'
    $complejidad = Get-ValorSecPol $contenidoSecPol 'PasswordComplexity'
    $bloqueoTras = Get-ValorSecPol $contenidoSecPol 'LockoutBadCount'
    $caducidad = Get-ValorSecPol $contenidoSecPol 'MaximumPasswordAge'

    $sec.PoliticaContrasenas = @{
        LongitudMinima = if ($null -ne $longMin) { "$longMin caracteres" + $(if ([int]$longMin -eq 0) { ' (riesgo — sin mínimo)' } else { '' }) } else { 'N/A' }
        Complejidad = if ($null -ne $complejidad) { if ($complejidad -eq '1') { 'Exigida' } else { 'NO exigida (riesgo)' } } else { 'N/A' }
        BloqueoTrasIntentos = if ($null -ne $bloqueoTras) { if ($bloqueoTras -eq '0') { 'Sin límite (riesgo)' } else { "$bloqueoTras intento(s)" } } else { 'N/A' }
        CaducidadDias = if ($null -ne $caducidad) { "$caducidad días" } else { 'N/A' }
    }
} catch {
    $sec.PoliticaContrasenas = @{ LongitudMinima = 'N/A'; Complejidad = 'N/A'; BloqueoTrasIntentos = 'N/A'; CaducidadDias = 'N/A' }
}

# Archivo hosts: contenido completo + qué líneas ACTIVAS (no comentario,
# no vacías) hay más allá del localhost estándar de Windows. Cualquier
# entrada añadida más allá de eso es, como mínimo, digna de revisión —
# es el vector clásico para redirigir dominios de antivirus/actualizaciones
# a un servidor falso o a 127.0.0.1 (bloqueo silencioso).
try {
    $hostsPath = Join-Path $env:WINDIR 'System32\drivers\etc\hosts'
    $hostsLineas = @(Get-Content -Path $hostsPath -ErrorAction Stop)
    $lineasActivas = @($hostsLineas | Where-Object { $_.Trim() -and -not $_.Trim().StartsWith('#') })
    $patronesEstandar = @('^127\.0\.0\.1\s+localhost\s*$', '^::1\s+localhost\s*$')
    $entradasAnadidas = @($lineasActivas | Where-Object {
        $linea = $_.Trim()
        -not ($patronesEstandar | Where-Object { $linea -match $_ })
    })
    try { $hostsModificado = (Get-Item $hostsPath -ErrorAction Stop).LastWriteTime.ToString('dd/MM/yyyy HH:mm') } catch { $hostsModificado = 'N/A' }
    $sec.ArchivoHosts = @{
        Ruta = $hostsPath
        TotalLineas = $hostsLineas.Count
        LineasActivas = $lineasActivas.Count
        EntradasAnadidas = @($entradasAnadidas)
        Sospechoso = ($entradasAnadidas.Count -gt 0)
        UltimaModificacion = $hostsModificado
        ContenidoCompleto = ($hostsLineas -join "`n")
    }
} catch {
    $sec.ArchivoHosts = @{
        Ruta = 'N/A'; TotalLineas = 0; LineasActivas = 0; EntradasAnadidas = @()
        Sospechoso = $false; UltimaModificacion = 'N/A'; ContenidoCompleto = ''
    }
}

# Indicios de software/licencias pirata: NO es un veredicto (puede haber
# falsos positivos — un técnico pudo instalar/probar algo y desinstalarlo
# mal, o un antivirus de terceros con nombre parecido), es una lista de
# señales a revisar manualmente. Se combinan varias fuentes independientes
# porque cada una por separado da bastantes falsos negativos.
try {
    $indicios = @()
    $nombresConocidos = @(
        'kmspico', 'kmsauto', 'kmself', 'kmselah', 'microsoft toolkit', 'mtk by',
        're-loader', 'reloader', 'hwidgen', 'w10digitalactivation', 'chew-wga',
        'removewat', 'windowsloader', '1activator', 'aact', 'vnkg', 'massgrave',
        'kms_vl_all', 'odin activator', 'ratiborus'
    )

    # 1) Servidor KMS personalizado configurado. Un Windows retail/OEM
    #    normal NUNCA tiene esta clave — KMSPico/KMSAuto la crean para
    #    apuntar a su propio "servidor" KMS falso (a menudo en localhost).
    try {
        $kmsHost = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\SoftwareProtectionPlatform' -Name KeyManagementServiceName -ErrorAction SilentlyContinue).KeyManagementServiceName
        if ($kmsHost) {
            if ($kmsHost -match '^127\.') {
                $indicios += "Servidor KMS configurado apunta a localhost ($kmsHost) - tecnica tipica de KMSPico/KMSAuto"
            } else {
                $indicios += "Servidor KMS personalizado configurado: $kmsHost (no es el valor por defecto de Microsoft)"
            }
        }
    } catch {}

    # 2) Procesos en ejecución con nombre de activador conocido.
    try {
        $procs = Get-Process -ErrorAction SilentlyContinue | Where-Object {
            $nombre = $_.ProcessName.ToLower()
            @($nombresConocidos | Where-Object { $nombre -like "*$_*" }).Count -gt 0
        }
        foreach ($p in $procs) {
            $indicios += "Proceso en ejecucion con nombre de activador conocido: $($p.ProcessName)"
        }
    } catch {}

    # 3) Tareas programadas cuyo nombre o acción (ejecutable/argumentos)
    #    coincide con un activador conocido — KMSPico instala una tarea
    #    programada para reactivar periódicamente.
    try {
        $tareas = Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {
            $nombreTarea = $_.TaskName.ToLower()
            $coincideNombre = @($nombresConocidos | Where-Object { $nombreTarea -like "*$_*" }).Count -gt 0
            $coincideAccion = $false
            foreach ($accion in $_.Actions) {
                $exe = ("$($accion.Execute) $($accion.Arguments)").ToLower()
                if (@($nombresConocidos | Where-Object { $exe -like "*$_*" }).Count -gt 0) { $coincideAccion = $true }
            }
            $coincideNombre -or $coincideAccion
        }
        foreach ($t in $tareas) {
            $indicios += "Tarea programada sospechosa: $($t.TaskName) (ruta: $($t.TaskPath))"
        }
    } catch {}

    # 4) Programas instalados (Uninstall del registro, 32 y 64 bits, todo
    #    usuario y máquina) con nombre de activador conocido.
    try {
        $rutasUninstall = @(
            'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
            'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
            'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
        )
        $instalados = Get-ItemProperty -Path $rutasUninstall -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName }
        foreach ($prog in $instalados) {
            $nombre = $prog.DisplayName.ToLower()
            if (@($nombresConocidos | Where-Object { $nombre -like "*$_*" }).Count -gt 0) {
                $indicios += "Programa instalado con nombre de activador conocido: $($prog.DisplayName)"
            }
        }
    } catch {}

    # 5) Estado de activación de Windows — informativo por sí solo, pero
    #    corrobora los indicios anteriores si el canal es KMS con host
    #    inusual.
    $estadoLicencia = 'N/A'
    $canalLicencia = 'N/A'
    try {
        $lic = Get-CimInstance -ClassName SoftwareLicensingProduct -ErrorAction SilentlyContinue |
            Where-Object { $_.PartialProductKey -and $_.ApplicationId -eq '55c92734-d682-4d71-983e-d6ec3f16059f' } |
            Select-Object -First 1
        if ($lic) {
            $estadoLicencia = switch ([int]$lic.LicenseStatus) {
                0 { 'Sin licencia' }
                1 { 'Con licencia (activado)' }
                2 { 'Periodo de gracia inicial' }
                3 { 'Periodo de gracia ampliado' }
                4 { 'Modo no genuino' }
                5 { 'Periodo de notificacion' }
                6 { 'Periodo de gracia extendido' }
                default { "Desconocido ($($lic.LicenseStatus))" }
            }
            $canalLicencia = if ($lic.Description) { $lic.Description } else { 'N/A' }
        }
    } catch {}

    $sec.SoftwarePirata = @{
        Indicios = @($indicios)
        TieneIndicios = ($indicios.Count -gt 0)
        EstadoLicenciaWindows = $estadoLicencia
        CanalActivacion = $canalLicencia
    }
} catch {
    $sec.SoftwarePirata = @{ Indicios = @(); TieneIndicios = $false; EstadoLicenciaWindows = 'N/A'; CanalActivacion = 'N/A' }
}

$sec | ConvertTo-Json -Depth 5
"""

PS_GET_DISK_HEALTH = r"""
$result = @{ Discos = @(); SaludDisco = @(); Particiones = @() }

try {
    Get-PhysicalDisk | ForEach-Object {
        $disk = $_
        $tipo = switch ($disk.MediaType) {
            'SSD' { 'SSD' }
            'HDD' { 'HDD' }
            'SCM' { 'Storage Class Memory' }
            default { $disk.MediaType }
        }
        $bus = switch ($disk.BusType) {
            'NVMe' { 'NVMe' }
            'SATA' { 'SATA' }
            'SAS' { 'SAS' }
            'USB' { 'USB' }
            'RAID' { 'RAID' }
            default { $disk.BusType }
        }
        $tamano = "$([math]::Round($disk.Size / 1GB, 1)) GB"

        # Get-StorageReliabilityCounter da telemetria S.M.A.R.T. real
        # (temperatura, desgaste SSD, horas de encendido, errores no
        # corregidos) que HealthStatus por si solo no explica — pero en
        # discos SATA/AHCI "normales" (sin Storage Spaces ni NVMe con
        # soporte completo) el proveedor a menudo no expone estos campos:
        # cada valor cae a 'N/A' individualmente en vez de descartar toda
        # la fila, para no perder los que si esten disponibles.
        $rel = $null
        try { $rel = $disk | Get-StorageReliabilityCounter -ErrorAction SilentlyContinue } catch {}

        $temperatura = if ($rel -and $rel.Temperature) { "$($rel.Temperature) °C" } else { 'N/A' }
        $desgaste = if ($rel -and $null -ne $rel.Wear) { "$($rel.Wear)%" } else { 'N/A' }
        $horasEncendido = if ($rel -and $rel.PowerOnHours) { "{0:N0} h" -f $rel.PowerOnHours } else { 'N/A' }
        if ($rel) {
            $sinCorregir = [int]([int64]$rel.ReadErrorsUncorrected + [int64]$rel.WriteErrorsUncorrected)
            $errores = if ($sinCorregir -gt 0) { "$sinCorregir sin corregir" } else { '0' }
        } else {
            $errores = 'N/A'
        }

        $result.Discos += [PSCustomObject]@{
            Disco = "Disco $($disk.DeviceId)"
            Modelo = $disk.FriendlyName
            Tipo = $tipo
            Bus = $bus
            Tamano = $tamano
            Serial = if ($disk.SerialNumber) { $disk.SerialNumber.Trim() } else { 'N/A' }
        }
        $result.SaludDisco += [PSCustomObject]@{
            Disco = "Disco $($disk.DeviceId)"
            Modelo = $disk.FriendlyName
            TipoMedio = $disk.MediaType
            EstadoSalud = $disk.HealthStatus
            EstadoOperativo = $disk.OperationalStatus
            Tamano = $tamano
            Temperatura = $temperatura
            Desgaste = $desgaste
            HorasEncendido = $horasEncendido
            Errores = $errores
        }
    }
} catch {}

$discoPorLetra = @{}
try {
    Get-Partition -ErrorAction SilentlyContinue | Where-Object { $_.DriveLetter } | ForEach-Object {
        $discoPorLetra[[string]$_.DriveLetter] = $_.DiskNumber
    }
} catch {}

try {
    Get-Volume | Where-Object { $_.DriveLetter -and $_.DriveType -eq 'Fixed' } | ForEach-Object {
        $letra = [string]$_.DriveLetter
        $numDisco = if ($discoPorLetra.ContainsKey($letra)) { $discoPorLetra[$letra] } else { $null }
        $result.Particiones += [PSCustomObject]@{
            Disco = if ($null -ne $numDisco) { "Disco $numDisco" } else { 'N/A' }
            Letra = "$($letra):"
            Etiqueta = if ($_.FileSystemLabel) { $_.FileSystemLabel } else { 'Sin etiqueta' }
            Sistema = $_.FileSystem
            Tamano = "$([math]::Round($_.Size / 1GB, 2)) GB"
            Usado = "$([math]::Round(($_.Size - $_.SizeRemaining) / 1GB, 2)) GB"
            Libre = "$([math]::Round($_.SizeRemaining / 1GB, 2)) GB"
            Porcentaje = "$([math]::Round((($_.Size - $_.SizeRemaining) / $_.Size) * 100, 1))%"
        }
    }
} catch {}

$result | ConvertTo-Json -Depth 4
"""

# Puerto de Start-DiskCleanup de Mantenimientov2.ps1 (I:\OneDrive\ZONA
# TRABAJO\LAUNCHER\SCRIPTS\MANTENIMIENTO V2), simplificado para MASys:
# valores fijos en vez de $config, SIN DeepClean/ResetBase de WinSxS
# (irreversible, fuera de alcance de una limpieza automática), y SIN el
# escaneo de "carpetas grandes" (la pestaña Contenido de MASys ya lo
# hace mejor y de forma interactiva). Devuelve {Categoria: BytesLiberados}
# — la papelera de reciclaje se vacía aparte, en Python (misma llamada
# SHEmptyRecycleBinW que ya usa RecycleBinDialog), no aquí.
PS_LIMPIEZA_SISTEMA = r"""
$ErrorActionPreference = 'SilentlyContinue'
$resultado = @{}

function Add-Categoria([string]$Nombre, [long]$Bytes) {
    if ($Bytes -gt 0) {
        if ($resultado.ContainsKey($Nombre)) { $resultado[$Nombre] += $Bytes }
        else { $resultado[$Nombre] = $Bytes }
    }
}

function Remove-ItemSafely([string]$Path) {
    if (-not (Test-Path $Path)) { return [long]0 }
    try {
        $medido = Get-ChildItem -Path $Path -Recurse -ErrorAction SilentlyContinue |
                  Measure-Object -Property Length -Sum -ErrorAction SilentlyContinue
        $tam = if ($medido.Sum) { $medido.Sum } else { 0 }
        Remove-Item -Path $Path -Recurse -Force -ErrorAction Stop
        return [long]$tam
    } catch {
        return [long]0
    }
}

# 1. Carpetas de sistema
$carpetasSistema = @(
    $env:TEMP,
    "$env:SystemRoot\Temp",
    "$env:ProgramData\Package Cache",
    "$env:USERPROFILE\AppData\Local\Microsoft\Windows\Temporary Internet Files",
    "$env:windir\LiveKernelReports",
    "$env:windir\GetCurrent",
    "$env:windir\~BT",
    "$env:windir\~WS",
    "$env:windir\SoftwareDistribution\Download"
)
$bytesCarpetasSistema = [long]0
foreach ($carpeta in $carpetasSistema) {
    if (Test-Path $carpeta) { $bytesCarpetasSistema += (Remove-ItemSafely "$carpeta\*") }
}
Add-Categoria "Carpetas temporales del sistema" $bytesCarpetasSistema

# 2. Temporales de usuario + cachés de navegadores/apps Electron, TODOS los perfiles
$bytesTempUsuario = [long]0
$bytesCaches = [long]0
$cachesRelativas = @(
    "AppData\Local\Google\Chrome\User Data\Default\Cache",
    "AppData\Local\Google\Chrome\User Data\Default\Code Cache",
    "AppData\Local\Google\Chrome\User Data\Default\GPUCache",
    "AppData\Local\Mozilla\Firefox\Profiles\*\cache2",
    "AppData\Local\Microsoft\Edge\User Data\Default\Cache",
    "AppData\Local\Microsoft\Edge\User Data\Default\Code Cache",
    "AppData\Local\Microsoft\Edge\User Data\Default\GPUCache",
    "AppData\Roaming\Microsoft\Teams\Cache",
    "AppData\Roaming\Microsoft\Teams\GPUCache",
    "AppData\Roaming\Microsoft\Teams\Service Worker\CacheStorage",
    "AppData\Local\Packages\MSTeams_*\LocalCache\Microsoft\MSTeams\EBWebView\Default\Cache",
    "AppData\Roaming\discord\Cache",
    "AppData\Roaming\discord\Code Cache",
    "AppData\Roaming\discord\GPUCache",
    "AppData\Roaming\Slack\Cache",
    "AppData\Roaming\Slack\Code Cache",
    "AppData\Roaming\Slack\GPUCache"
)
Get-ChildItem -Path "$env:SystemDrive\Users" -Directory -ErrorAction SilentlyContinue | ForEach-Object {
    foreach ($sub in @("AppData\Local\Temp", "AppData\LocalLow\Temp")) {
        $uTemp = Join-Path $_.FullName $sub
        if (Test-Path $uTemp) { $bytesTempUsuario += (Remove-ItemSafely "$uTemp\*") }
    }
    foreach ($rel in $cachesRelativas) {
        foreach ($cachePath in (Resolve-Path -Path (Join-Path $_.FullName $rel) -ErrorAction SilentlyContinue).Path) {
            if ($cachePath -and (Test-Path $cachePath)) { $bytesCaches += (Remove-ItemSafely "$cachePath\*") }
        }
    }
}
Add-Categoria "Temporales de usuario (todos los perfiles)" $bytesTempUsuario
Add-Categoria "Cachés de navegadores y apps (todos los perfiles)" $bytesCaches

# 3. Hibernación — solo si hiberfil.sys existe de verdad (si ya estaba
# desactivada, no hay nada que "liberar" y no se debe aparentar que sí).
$hiberfil = "$env:SystemDrive\hiberfil.sys"
if (Test-Path $hiberfil -Force) {
    $tamHiber = (Get-Item $hiberfil -Force -ErrorAction SilentlyContinue).Length
    powercfg /h off
    Add-Categoria "Hibernación desactivada" $tamHiber
}

# 4. Prefetch — solo si el disco de sistema es SSD (en HDD acelera el
# arranque de programas; vaciarlo ahí es contraproducente).
try {
    $sysDiskNum = (Get-Partition -DriveLetter $env:SystemDrive.TrimEnd(':') -ErrorAction SilentlyContinue).DiskNumber
    $sysDiskType = (Get-PhysicalDisk | Where-Object DeviceId -eq $sysDiskNum).MediaType
    if ($sysDiskType -eq "SSD") {
        Add-Categoria "Prefetch (disco SSD)" (Remove-ItemSafely "$env:SystemRoot\Prefetch\*")
    }
} catch {}

# 5. Windows.old
Add-Categoria "Windows.old" (Remove-ItemSafely "$env:SystemDrive\Windows.old")

# 6. DriverStore: versiones antiguas duplicadas. Sin /force: pnputil se
# niega solo a borrar un paquete si está en uso, así que nunca rompe un
# driver activo — solo libera versiones ya superadas.
try {
    $repoPath = "$env:SystemRoot\System32\DriverStore\FileRepository"
    $repoAntes = if (Test-Path $repoPath) {
        (Get-ChildItem -Path $repoPath -Recurse -File -ErrorAction SilentlyContinue |
         Measure-Object -Property Length -Sum -ErrorAction SilentlyContinue).Sum
    } else { 0 }

    # pnputil etiqueta sus campos según el idioma de Windows (ES/EN).
    $enumOut = & pnputil /enum-drivers 2>&1 | Out-String
    $entradas = @()
    foreach ($bloque in ($enumOut -split "(?:\r?\n){2,}")) {
        if ($bloque -match "(?:Published Name|Nombre publicado)\s*:\s*(oem\d+\.inf)") {
            $publicado = $Matches[1]
            $original = if ($bloque -match "(?:Original Name|Nombre original)\s*:\s*(\S+)") { $Matches[1] } else { $null }
            $version = if ($bloque -match "(?:Driver Version|Versi[oó]n de controlador)\s*:\s*(\S+)") { $Matches[1] } else { $null }
            if ($original -and $version) {
                $entradas += [PSCustomObject]@{ Publicado = $publicado; Original = $original; Version = $version }
            }
        }
    }
    foreach ($grupo in ($entradas | Group-Object Original | Where-Object Count -gt 1)) {
        $ordenado = $grupo.Group | Sort-Object { [version]($_.Version -replace '[^\d\.]', '') } -Descending -ErrorAction SilentlyContinue
        if (-not $ordenado) { $ordenado = $grupo.Group }
        foreach ($antiguo in ($ordenado | Select-Object -Skip 1)) {
            # SIN /uninstall: según la ayuda de pnputil, «/uninstall: desinstalar el paquete de controladores de los
            # dispositivos que lo usan» — quitaba el controlador a un dispositivo que lo estuviera usando (p. ej. una
            # versión anterior elegida a propósito). Sin él, pnputil se niega si el paquete está en uso.
            & pnputil /delete-driver $antiguo.Publicado 2>&1 | Out-Null
        }
    }

    if (Test-Path $repoPath) {
        $repoDespues = (Get-ChildItem -Path $repoPath -Recurse -File -ErrorAction SilentlyContinue |
                        Measure-Object -Property Length -Sum -ErrorAction SilentlyContinue).Sum
        Add-Categoria "DriverStore (versiones antiguas)" ([math]::Max(0, $repoAntes - $repoDespues))
    }
} catch {}

# 7. Caché de Delivery Optimization (reparto P2P de actualizaciones/apps) —
# el cmdlet no informa de bytes liberados, solo se marca si se ejecutó.
try {
    if (Get-Command Delete-DeliveryOptimizationCache -ErrorAction SilentlyContinue) {
        Delete-DeliveryOptimizationCache -Force -ErrorAction Stop
        $resultado["Delivery Optimization (caché limpiada)"] = 0
    }
} catch {}

# 8. Logs archivados de CBS (Windows\Logs\CBS\CbsPersist_*.cab/.log) de
# más de 180 días — NUNCA el CBS.log activo (DISM/SFC escriben en él).
try {
    $cbsPath = "$env:SystemRoot\Logs\CBS"
    if (Test-Path $cbsPath) {
        $cbsCutoff = (Get-Date).AddDays(-180)
        $bytesCbs = [long]0
        Get-ChildItem -Path $cbsPath -Filter "CbsPersist_*" -File -ErrorAction SilentlyContinue |
            Where-Object { $_.LastWriteTime -lt $cbsCutoff } | ForEach-Object {
                $bytesCbs += $_.Length
                Remove-Item -Path $_.FullName -Force -ErrorAction SilentlyContinue
            }
        Add-Categoria "Logs CBS archivados (+180 días)" $bytesCbs
    }
} catch {}

$resultado | ConvertTo-Json -Depth 3
"""

PS_GET_BACKUP_STATUS = r"""
$b = @{}
try {
    $restorePoints = Get-ComputerRestorePoint -ErrorAction SilentlyContinue | Sort-Object CreationTime -Descending
    $b.PuntosRestauracion = @($restorePoints | Select-Object -First 10 | ForEach-Object {
        @{ Fecha = $_.CreationTime; Descripcion = $_.Description }
    })
} catch {
    $b.PuntosRestauracion = @()
}
try {
    $shadows = Get-CimInstance -ClassName Win32_ShadowCopy -ErrorAction SilentlyContinue
    $b.CopiasSombra = if ($shadows) { @($shadows).Count } else { 0 }
    $ultima = $shadows | Sort-Object InstallDate -Descending | Select-Object -First 1
    $b.UltimaCopiaSombra = if ($ultima) { $ultima.InstallDate } else { 'N/A' }
} catch {
    $b.CopiasSombra = 0
    $b.UltimaCopiaSombra = 'N/A'
}
$b | ConvertTo-Json -Depth 4
"""

PS_GET_LICENSE_INFO = r"""
try {
    # SoftwareLicensingProduct (Software Protection Platform) es el ÚNICO
    # registro central de licencias que mantiene Windows -- y solo cubre
    # productos activados con la tecnología de Microsoft: Windows y Office/
    # Microsoft 365 (por eso la pestaña se limita a estos dos; el software
    # de terceros -- Adobe, antivirus, etc. -- no tiene un registro
    # equivalente accesible de forma fiable, cada fabricante guarda su
    # licencia a su manera).
    #
    # Enumeración oficial de LicenseStatus (SL_ACTIVATION_STATUS de
    # Microsoft) -- el switch anterior tenía las etiquetas desplazadas a
    # partir del valor 3 (el 5, que es "Notification"/necesita reactivar,
    # se mostraba como "Periodo de gracia extendido"; el valor 6 ni
    # siquiera estaba contemplado, caía a "Desconocido"). Corregido aquí,
    # reutilizado para Windows y para Office (misma API, mismo enum).
    function Obtener-EstadoLicencia([int]$status) {
        switch ($status) {
            0 { 'Sin licencia' }
            1 { 'Activado' }
            2 { 'Periodo de gracia inicial' }
            3 { 'Periodo de gracia (fuera de tolerancia)' }
            4 { 'Periodo de gracia (no genuino)' }
            5 { 'Notificación (requiere reactivar)' }
            6 { 'Periodo de gracia extendido' }
            default { 'Desconocido' }
        }
    }

    # PartialProductKey (últimos 5 caracteres) es lo único que expone la
    # API oficial desde Windows 8. La clave completa de 25 caracteres SI
    # existe en equipos con clave física (OEM/Retail antiguos): se
    # decodifica del blob DigitalProductId del registro con el algoritmo
    # estándar (BCDFGHJKMPQRTVWXY2346789, base 24). En licencia digital
    # (entitlement ligado a hardware/cuenta, sin clave física) ese blob
    # decodifica a un valor placeholder reconocible (todo el mismo
    # carácter): no es un fallo de la decodificación, es que ahí no hay
    # ninguna clave real que mostrar. Solo aplica a Windows -- Office no
    # guarda su clave en DigitalProductId de la misma forma.
    function Get-FullProductKey {
        try {
            $map = 'BCDFGHJKMPQRTVWXY2346789'
            $id = (Get-ItemProperty -Path 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -Name DigitalProductId -ErrorAction Stop).DigitalProductId
            $keyOffset = 52
            # 5.7.3: división ENTERA (antes «/» decimal redondeada) y, en Windows 8 o posterior, la «N» que se inserta
            # tras el último dígito calculado. Sin ella la clave mostrada era errónea (comprobado: la de este equipo salía
            # sin «N», imposible en una clave de Windows 8+, y con el primer carácter de más).
            $esWin8Mas = [math]::Truncate([int]$id[66] / 6) -band 1
            $id[66] = ([int]$id[66] -band 0xF7) -bor (($esWin8Mas -band 2) * 4)
            $clave = ''
            $ultimo = 0
            for ($i = 24; $i -ge 0; $i--) {
                $actual = 0
                for ($j = 14; $j -ge 0; $j--) {
                    $actual = $actual * 256 + [int]$id[$j + $keyOffset]
                    $id[$j + $keyOffset] = [byte][math]::Truncate($actual / 24)
                    $actual = $actual % 24
                }
                $clave = $map[$actual] + $clave
                $ultimo = $actual
            }
            if ($esWin8Mas -eq 1) {
                $clave = $clave.Substring(1, $ultimo) + 'N' + $clave.Substring($ultimo + 1)
            }
            $clave = $clave.Substring(0, 5) + '-' + $clave.Substring(5, 5) + '-' + $clave.Substring(10, 5) + '-' +
                     $clave.Substring(15, 5) + '-' + $clave.Substring(20, 5)
            # Placeholder de licencia digital: todo el mismo carácter, sin contar la «N» insertada (que no es del alfabeto)
            if ((($clave -replace '-', '') -replace 'N', '') -match '^(.)\1*$') { return $null }
            return $clave
        } catch { return $null }
    }

    # 5.4.0: UNA sola consulta y filtrada EN ORIGEN (WQL). Antes se pedía la clase entera dos veces
    # (Windows y Office) y se filtraba después: 51 s en el equipo de pruebas, más que el límite de
    # 45 s, así que Licencias salía SIEMPRE «N/A». Filtrada: 2 s.
    $productos = @(Get-CimInstance -ClassName SoftwareLicensingProduct -Filter "PartialProductKey IS NOT NULL" -ErrorAction SilentlyContinue)
    $lic = $productos | Where-Object { $_.Name -like 'Windows*' } | Select-Object -First 1
    $claveCompleta = Get-FullProductKey
    $clave = if ($claveCompleta) {
        $claveCompleta
    } elseif ($lic.PartialProductKey) {
        $lic.PartialProductKey
    } else {
        'N/A (licencia digital)'
    }

    # --- Office / Microsoft 365 ---
    # MISMA API que Windows (Software Protection Platform) -- fiable
    # porque es el propio motor de activación de Windows, NO texto de
    # correo encontrado por casualidad en varias zonas del registro de
    # Office (así funcionaba antes: cualquier cuenta de Gmail añadida a
    # Outlook por IMAP salía como "licencia Office 365" -- comprobado en
    # vivo, con clave de registro literal 'tp_google_imap_OAuth2', es
    # decir, una cuenta de correo de TERCEROS, no una licencia).
    # ApplicationID 0ff1ce15-a989-479d-af46-f275c6370663 es el GUID fijo
    # de Office para SPP (no cambia entre versiones/idiomas).
    $licOffice = $productos |
        Where-Object { $_.ApplicationID -eq '0ff1ce15-a989-479d-af46-f275c6370663' } |
        Select-Object -First 1

    # SKU real instalado (ej. "O365HomePremRetail") -- complementa el
    # nombre de SoftwareLicensingProduct, que a veces incluye el sufijo
    # interno del canal (ej. "..._Grace") en vez del nombre comercial.
    $skuOffice = 'N/A'
    try {
        $ctr = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Office\ClickToRun\Configuration' -ErrorAction SilentlyContinue
        if ($ctr -and $ctr.ProductReleaseIds) { $skuOffice = ($ctr.ProductReleaseIds -split ',')[0] }
    } catch {}

    # Cuenta REAL a la que está vinculada la licencia -- NO se busca un
    # email "por ahí" (eso es justo lo que se quitó, ver comentario más
    # arriba). Office mantiene un mapeo dedicado y explícito para esto:
    # el Id de cuenta conectada actualmente (ConnectedOneAuthAccountId) se
    # busca en LicenseIdToEmailMapping, una tabla que el propio Office
    # rellena para saber a qué cuenta corresponde cada Id de licencia.
    # Comprobado en vivo: dos cuentas Gmail conectadas como correo (ver
    # Identities) NO aparecen aquí -- solo la cuenta de licencia real.
    $cuentaOffice = 'N/A'
    $fechaGracia = 'N/A'
    try {
        $connectedId = (Get-ItemProperty 'HKCU:\Software\Microsoft\Office\16.0\Common\Identity' -ErrorAction SilentlyContinue).ConnectedOneAuthAccountId
        if ($connectedId) {
            $mapaEmail = Get-ItemProperty 'HKCU:\Software\Microsoft\Office\16.0\Common\Licensing\LicensingNext\LicenseIdToEmailMapping' -ErrorAction SilentlyContinue
            if ($mapaEmail -and $mapaEmail.$connectedId) { $cuentaOffice = $mapaEmail.$connectedId }
        }
    } catch {}
    try {
        # Fecha en la que empezó el periodo de gracia/notificación actual
        # -- solo informativo, no crítico si no se encuentra (varía según
        # el GUID interno del SKU, que sí es estable dentro del mismo
        # equipo pero no se puede predecir de antemano).
        $graceProps = Get-ItemProperty 'HKCU:\Software\Microsoft\Office\16.0\Common\Licensing\LicensingExperience\GraceExperience' -ErrorAction SilentlyContinue
        if ($graceProps) {
            $primerValor = $graceProps.PSObject.Properties | Where-Object { $_.Name -notlike 'PS*' } | Select-Object -First 1
            if ($primerValor) { $fechaGracia = $primerValor.Value }
        }
    } catch {}

    $office = if ($licOffice) {
        @{
            Detectado = $true
            Producto = $licOffice.Name
            Estado = Obtener-EstadoLicencia([int]$licOffice.LicenseStatus)
            Sku = $skuOffice
            ClaveParcial = if ($licOffice.PartialProductKey) { $licOffice.PartialProductKey } else { 'N/A' }
            Cuenta = $cuentaOffice
            DesdeFecha = $fechaGracia
        }
    } else {
        @{ Detectado = $false; Producto = 'N/A'; Estado = 'N/A'; Sku = $skuOffice; ClaveParcial = 'N/A'; Cuenta = 'N/A'; DesdeFecha = 'N/A' }
    }

    @{
        Producto = $lic.Name
        Estado = Obtener-EstadoLicencia([int]$lic.LicenseStatus)
        Clave = $clave
        Descripcion = $lic.Description
        Office = $office
    } | ConvertTo-Json -Depth 4
} catch {
    @{
        Producto = 'N/A'; Estado = 'N/A'; Clave = 'N/A'; Descripcion = 'N/A'
        Office = @{ Detectado = $false; Producto = 'N/A'; Estado = 'N/A'; Sku = 'N/A'; ClaveParcial = 'N/A'; Cuenta = 'N/A'; DesdeFecha = 'N/A' }
    } | ConvertTo-Json -Depth 4
}
"""

PS_GET_WINGET_UPGRADES = r"""
try {
    $wingetPath = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $wingetPath) {
        @{ Disponible = $false; Paquetes = @() } | ConvertTo-Json -Depth 3
    } else {
        # --- Anclajes (winget pin) — mismo problema de localización y mismo
        # fix por alineación que la tabla de actualizaciones de más abajo:
        # solo se necesita el Id de cada paquete anclado para marcar
        # 'Bloqueado' en la tabla; no importa el tipo de anclaje (Blocking/
        # Pinning/Gating) para ese fin -- 🔒 Bloquear del Gestor de Paquetes
        # siempre crea un anclaje 'Blocking', pero si alguien ancló algo
        # manualmente por CLI con otro tipo, también debe verse como
        # bloqueado aquí (más correcto que ignorarlo).
        $pinIds = @()
        try {
            $rawPin = winget pin list 2>&1 | Out-String
            $pinLines = $rawPin -split "`r?`n" | Where-Object { $_.Trim() -ne '' }
            $pinDashIdx = -1
            for ($i = 0; $i -lt $pinLines.Count; $i++) {
                if ($pinLines[$i] -match '^-{5,}\s*$') { $pinDashIdx = $i; break }
            }
            if ($pinDashIdx -ge 1) {
                $pinHeader = $pinLines[$pinDashIdx - 1]
                $pinColStarts = @(0) + (
                    [regex]::Matches($pinHeader, '(?<=\s{2,})\S') | ForEach-Object { $_.Index }
                )
                if ($pinColStarts.Count -ge 2) {
                    $pinNameEnd = $pinColStarts[1]
                    $pinIdEnd = if ($pinColStarts.Count -ge 3) { $pinColStarts[2] } else { $pinHeader.Length }
                    for ($i = $pinDashIdx + 1; $i -lt $pinLines.Count; $i++) {
                        $line = $pinLines[$i]
                        if ($line.Length -lt $pinNameEnd) { continue }
                        $finCol = [Math]::Min($pinIdEnd, $line.Length)
                        $idAnclado = $line.Substring($pinNameEnd, $finCol - $pinNameEnd).Trim()
                        if ($idAnclado) { $pinIds += $idAnclado }
                    }
                }
            }
        } catch {}

        # --include-pinned: SIN esto, winget oculta por completo cualquier
        # paquete anclado (comprobado en vivo) -- necesitamos que la fila
        # SIGA apareciendo (con su candado) para poder desbloquearla desde
        # aquí; qué paquetes quedan fuera de un 'actualizar todo' lo decide
        # MASys filtrando por 'Bloqueado', no winget ocultándolos.
        $raw = winget upgrade --include-pinned --accept-source-agreements 2>&1 | Out-String
        $lines = $raw -split "`r?`n" | Where-Object { $_.Trim() -ne '' }
        # La salida de winget está LOCALIZADA según el idioma de Windows del
        # técnico ('Name/Id/Version/Available' en inglés, 'Nombre/Id/Versión/
        # Disponible' en español...) — comprobado en vivo en esta máquina
        # (Windows en español): la cabecera real es 'Nombre  Id  Versión
        # Disponible  Origen', así que el 'match' contra texto en inglés de
        # más abajo NUNCA encontraba la cabecera y esta consulta devolvía
        # SIEMPRE 0 actualizaciones sin avisar de nada, aunque hubiera
        # decenas disponibles (21 en esta máquina en el momento del fix).
        # Fix: localizar los límites de columna por ALINEACIÓN (la línea de
        # guiones que winget siempre imprime bajo la cabecera + huecos de
        # 2+ espacios entre columnas), no por el texto de cabecera — así
        # funciona en cualquier idioma.
        #
        # --include-pinned IMPRIME DOS TABLAS SEPARADAS cuando hay algún
        # paquete con anclaje 'Blocking': la tabla normal, y una segunda
        # tabla aparte (con su propia cabecera/guiones) solo para los
        # bloqueados, precedida de un aviso tipo "N paquetes tienen un pin
        # que debe quitarse..." (comprobado en vivo -- si solo se procesa
        # la PRIMERA tabla, los paquetes bloqueados nunca aparecen y
        # 'Bloqueado' se queda siempre en $false). Por eso se recorren
        # TODAS las tablas (todas las líneas de guiones), no solo la
        # primera.
        $dashIndices = @()
        for ($i = 0; $i -lt $lines.Count; $i++) {
            if ($lines[$i] -match '^-{5,}\s*$') { $dashIndices += $i }
        }
        $paquetes = @()
        for ($t = 0; $t -lt $dashIndices.Count; $t++) {
            $dashIdx = $dashIndices[$t]
            if ($dashIdx -lt 1) { continue }
            $header = $lines[$dashIdx - 1]
            $finBloque = if ($t + 1 -lt $dashIndices.Count) { $dashIndices[$t + 1] - 1 } else { $lines.Count }
            $colStarts = @(0) + (
                [regex]::Matches($header, '(?<=\s{2,})\S') | ForEach-Object { $_.Index }
            )
            # Solo se necesitan garantizadas las columnas Nombre/Id/Versión
            # (posiciones 0/1/2) -- Nombre e Id pueden contener texto largo
            # que empuja el resto bien a la derecha, así que su hueco
            # SIEMPRE es de 2+ espacios. La cola (Versión actual/Disponible/
            # Origen) NO se corta por posición: en la tabla de paquetes
            # anclados (normalmente 1-2 filas) los valores son cortos y
            # quedan separados por un solo espacio, sin hueco de 2+ que
            # detectar (comprobado en vivo: "8.17.13 9.9.2      winget" —
            # ni cabecera ni fila dan un límite fiable ahí). En su lugar,
            # la cola se tokeniza por espacios y se toma desde el FINAL:
            # último token = Origen, penúltimo = Disponible, todo lo
            # anterior = Versión actual (que sí puede llevar un espacio,
            # ej. "ad 9.0.14" en AnyDesk — por eso se toma desde el final,
            # no desde el principio).
            if ($colStarts.Count -lt 3) { continue }
            $nameEnd = $colStarts[1]
            $idEnd = $colStarts[2]
            for ($i = $dashIdx + 1; $i -lt $finBloque; $i++) {
                $line = $lines[$i]
                # Líneas de resumen/aviso entre tablas ('N upgrades
                # available' / 'N actualizaciones disponibles.' / 'N
                # paquetes tienen un pin que debe quitarse...') — todas
                # empiezan por un número en cualquier idioma, criterio
                # genérico en vez de un texto fijo.
                if ($line -match '^\d+\s' -or $line.Trim() -eq '' -or $line.Length -le $idEnd) { continue }
                $nombre = $line.Substring(0, $nameEnd).Trim()
                $id = $line.Substring($nameEnd, $idEnd - $nameEnd).Trim()
                $cola = $line.Substring($idEnd).Trim()
                $partes = @($cola -split '\s+')
                if ($partes.Count -lt 3) { continue }
                $origen = $partes[$partes.Count - 1]
                $disp = $partes[$partes.Count - 2]
                $ver = ($partes[0..($partes.Count - 3)] -join ' ')
                if ($nombre -and $id) {
                    $paquetes += @{ Nombre = $nombre; Id = $id; VersionActual = $ver; VersionDisponible = $disp; Origen = $origen; Bloqueado = ($pinIds -contains $id) }
                }
            }
        }
        @{ Disponible = $true; Paquetes = $paquetes } | ConvertTo-Json -Depth 4
    }
} catch {
    @{ Disponible = $false; Paquetes = @() } | ConvertTo-Json -Depth 3
}
"""

# Bloquear/Desbloquear actualización de un paquete concreto — 🔒/🔓 del
# menú contextual de Gestor de Paquetes. '--blocking' es DELIBERADO: un
# anclaje normal (sin ese flag) solo excluye al paquete de 'winget upgrade
# --all', pero SIGUE dejando pasar una actualización explícita por --id;
# --blocking impide las dos vías hasta que se quite el anclaje (comprobado
# en vivo). {id} lo sustituye run_powershell_command_params (escapado
# automático de comillas simples).
PS_WINGET_PIN_ADD = "winget pin add --id '{id}' --blocking --accept-source-agreements"
PS_WINGET_PIN_REMOVE = "winget pin remove --id '{id}'"

# Lista de Ids con algún anclaje activo — versión standalone del mismo
# parseo ya usado dentro de PS_GET_WINGET_UPGRADES (ver ese comentario para
# el porqué de la detección de columnas por alineación). Se usa desde
# Software Instalado (vínculo winget manual) para saber si el paquete
# vinculado está bloqueado SIN tener que consultar todas las
# actualizaciones disponibles — no se reutiliza/compone con
# PS_GET_WINGET_UPGRADES a propósito: cada script embebido de este fichero
# es autocontenido, así un cambio en uno no puede romper al otro por
# sorpresa.
PS_WINGET_PIN_LIST = r"""
try {
    $rawPin = winget pin list 2>&1 | Out-String
    $pinLines = $rawPin -split "`r?`n" | Where-Object { $_.Trim() -ne '' }
    $pinDashIdx = -1
    for ($i = 0; $i -lt $pinLines.Count; $i++) {
        if ($pinLines[$i] -match '^-{5,}\s*$') { $pinDashIdx = $i; break }
    }
    $pinIds = @()
    if ($pinDashIdx -ge 1) {
        $pinHeader = $pinLines[$pinDashIdx - 1]
        $pinColStarts = @(0) + (
            [regex]::Matches($pinHeader, '(?<=\s{2,})\S') | ForEach-Object { $_.Index }
        )
        if ($pinColStarts.Count -ge 2) {
            $pinNameEnd = $pinColStarts[1]
            $pinIdEnd = if ($pinColStarts.Count -ge 3) { $pinColStarts[2] } else { $pinHeader.Length }
            for ($i = $pinDashIdx + 1; $i -lt $pinLines.Count; $i++) {
                $line = $pinLines[$i]
                if ($line.Length -lt $pinNameEnd) { continue }
                $finCol = [Math]::Min($pinIdEnd, $line.Length)
                $idAnclado = $line.Substring($pinNameEnd, $finCol - $pinNameEnd).Trim()
                if ($idAnclado) { $pinIds += $idAnclado }
            }
        }
    }
    @{ Ids = $pinIds } | ConvertTo-Json -Depth 2
} catch {
    @{ Ids = @() } | ConvertTo-Json -Depth 2
}
"""

# {query} lo sustituye run_powershell_json_params (escapado automático de
# comillas simples) — por eso los literales @{{ }} de PowerShell van
# doblados aquí, igual que en cualquier otra plantilla de
# run_powershell_json_params/run_powershell_command_params de este fichero.
# Mismo fix de localización que PS_GET_WINGET_UPGRADES (ver su comentario):
# columnas detectadas por alineación (guiones + huecos de 2+ espacios), no
# por texto de cabecera en inglés — 'Coincidencia'/'Origen' (o 'Match'/
# 'Source' en inglés) se ignoran, solo hace falta el Id para poder vincular
# el software con 'winget upgrade --id'.
PS_WINGET_SEARCH = r"""
try {{
    $wingetPath = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $wingetPath) {{
        @{{ Disponible = $false; Paquetes = @() }} | ConvertTo-Json -Depth 3
    }} else {{
        $raw = winget search '{query}' --accept-source-agreements 2>&1 | Out-String
        $lines = $raw -split "`r?`n" | Where-Object {{ $_.Trim() -ne '' }}
        $dashIdx = -1
        for ($i = 0; $i -lt $lines.Count; $i++) {{
            if ($lines[$i] -match '^-{{5,}}\s*$') {{ $dashIdx = $i; break }}
        }}
        $paquetes = @()
        if ($dashIdx -ge 1) {{
            $header = $lines[$dashIdx - 1]
            $colStarts = @(0) + (
                [regex]::Matches($header, '(?<=\s{{2,}})\S') | ForEach-Object {{ $_.Index }}
            )
            if ($colStarts.Count -ge 3) {{
                $idStart = $colStarts[1]
                $verStart = $colStarts[2]
                for ($i = $dashIdx + 1; $i -lt $lines.Count; $i++) {{
                    $line = $lines[$i]
                    if ($line.Trim() -eq '' -or $line.Length -lt $verStart) {{ continue }}
                    $nombre = $line.Substring(0, $idStart).Trim()
                    $id = $line.Substring($idStart, $verStart - $idStart).Trim()
                    $version = ($line.Substring($verStart).Trim() -split '\s+')[0]
                    if ($nombre -and $id) {{
                        $paquetes += @{{ Nombre = $nombre; Id = $id; Version = $version }}
                    }}
                }}
            }}
        }}
        @{{ Disponible = $true; Paquetes = $paquetes }} | ConvertTo-Json -Depth 3
    }}
}} catch {{
    @{{ Disponible = $false; Paquetes = @() }} | ConvertTo-Json -Depth 3
}}
"""

PS_GET_LOCAL_NETWORK_DEVICES = r"""
try {
    # BUG REAL: leer solo Get-NetNeighbor (la caché ARP) se queda corto —
    # esa caché solo contiene equipos con los que YA ha habido tráfico
    # reciente. Dispositivos silenciosos (cámaras IP, impresoras, etc. que
    # nunca han iniciado comunicación con este equipo) no aparecían nunca,
    # aunque estuvieran encendidos y conectados. Se hace un barrido de
    # ping en paralelo (async, no uno a uno) por la propia subred antes de
    # leer la tabla, para forzar una respuesta ARP y que queden
    # registrados. Confirmado empíricamente: sin el barrido, 3 cámaras IP
    # reales de la red del usuario no aparecían; con él, sí.
    $subredes = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -notlike '169.254.*' -and $_.IPAddress -ne '127.0.0.1' -and $_.PrefixLength -ge 24 } |
        Select-Object -ExpandProperty IPAddress -Unique)

    $pings = @()
    foreach ($ip in $subredes) {
        $partes = $ip -split '\.'
        $prefijo = "$($partes[0]).$($partes[1]).$($partes[2])."
        for ($i = 1; $i -le 254; $i++) {
            $destino = "$prefijo$i"
            $p = [System.Net.NetworkInformation.Ping]::new()
            $pings += [PSCustomObject]@{ Ping = $p; Task = $p.SendPingAsync($destino, 300) }
        }
    }
    if ($pings.Count -gt 0) {
        try { [System.Threading.Tasks.Task]::WaitAll(@($pings.Task), 4000) | Out-Null } catch {}
        foreach ($p in $pings) { $p.Ping.Dispose() }
    }

    $traduccionEstado = @{
        'Reachable' = 'Alcanzable'; 'Stale' = 'Obsoleto'; 'Permanent' = 'Permanente'
        'Delay' = 'Retraso'; 'Probe' = 'Verificando'; 'Incomplete' = 'Incompleto'
        'Unreachable' = 'Inalcanzable'
    }

    # @(...) fuerza array incluso con 0/1 resultado, y "[]" explícito evita
    # que "@() | ConvertTo-Json" (con pipe) emita 0 bytes por stdout con 0
    # dispositivos — run_powershell_json lo interpretaría como fallo, no
    # como "0 dispositivos" (confirmado empíricamente, mismo patrón ya
    # corregido en otras secciones de Red esta sesión).
    $dispositivos = @(Get-NetNeighbor -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.State -ne 'Unreachable' -and $_.IPAddress -notlike '224.*' -and $_.IPAddress -notlike '255.*' -and $_.LinkLayerAddress -ne '00-00-00-00-00-00' } |
        ForEach-Object {
            $adapter = Get-NetAdapter -InterfaceIndex $_.ifIndex -ErrorAction SilentlyContinue
            $estadoTexto = $_.State.ToString()
            [PSCustomObject]@{
                IP = $_.IPAddress
                MAC = $_.LinkLayerAddress
                Estado = if ($traduccionEstado.ContainsKey($estadoTexto)) { $traduccionEstado[$estadoTexto] } else { $estadoTexto }
                Interfaz = if ($adapter) { $adapter.Name } else { 'N/A' }
            }
        } | Sort-Object -Property IP -Unique)
    if ($dispositivos.Count -eq 0) { Write-Output "[]" } else { $dispositivos | ConvertTo-Json -Depth 2 }
} catch {
    Write-Output "[]"
}
"""

PS_GET_PS_HISTORY = r"""
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
    $histFile = "$env:APPDATA\Microsoft\Windows\PowerShell\PSReadLine\ConsoleHost_history.txt"
    if (-not (Test-Path $histFile)) { Write-Output '[]'; exit }
    $lines = Get-Content $histFile -Encoding UTF8 -ErrorAction Stop | Where-Object { $_.Trim() -ne '' }
    $result = @()
    $num = 1
    foreach ($line in $lines) {
        $result += [PSCustomObject]@{ Numero = [string]$num; Comando = $line.Trim() }
        $num++
    }
    # $result vacío = "null" al convertir, no "[]" — mismo bug ya corregido
    # varias veces esta sesión (Impresoras, USB, dispositivos ocultos...).
    if ($result.Count -eq 0) { Write-Output '[]' } else { $result | ConvertTo-Json -Depth 2 }
} catch { Write-Output '[]' }
"""

PS_GET_PS_HISTORY_EVENTOS = r"""
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8
    # Registro de bloques de script (Script Block Logging) — a diferencia
    # del historial de PSReadLine (ConsoleHost_history.txt, que NO guarda
    # cuándo se ejecutó cada línea), este evento SÍ trae fecha/hora real,
    # y además captura lo que se ejecutó de verdad (scripts, no solo lo
    # tecleado a mano en una consola interactiva). No siempre está
    # disponible (depende de que el registro tenga entradas — Windows lo
    # activa por defecto en versiones recientes, pero no es universal).
    $eventos = @(Get-WinEvent -FilterHashtable @{LogName='Microsoft-Windows-PowerShell/Operational'; Id=4104} -MaxEvents 500 -ErrorAction Stop | ForEach-Object {
        $xml = [xml]$_.ToXml()
        $datos = @{}
        foreach ($d in $xml.Event.EventData.Data) { $datos[$d.Name] = $d.'#text' }
        [PSCustomObject]@{
            FechaHora = $_.TimeCreated.ToString('dd/MM/yyyy HH:mm:ss')
            Comando = $datos['ScriptBlockText']
        }
    })
    if ($eventos.Count -eq 0) { Write-Output '[]' } else { $eventos | ConvertTo-Json -Depth 2 }
} catch { Write-Output '[]' }
"""

PS_GET_REMOTE_CONNECTIONS = r"""
try {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
    $OutputEncoding = [System.Text.Encoding]::UTF8

    $resultados = @()
    $fechaInicio = (Get-Date).AddDays(-30)

    # --- Fuente 1: Registro de Seguridad (logon tipos 3=Red, 10=RDP interactivo) ---
    try {
        $filtro = @{ LogName='Security'; Id=@(4624,4625); StartTime=$fechaInicio }
        $eventos = Get-WinEvent -FilterHashtable $filtro -MaxEvents 1000 -ErrorAction SilentlyContinue

        foreach ($ev in $eventos) {
            try {
                $props = $ev.Properties
                $tipoLogon = [int]($props[8].Value)
                if ($tipoLogon -notin @(3, 10)) { continue }

                $usuario    = [string]$props[5].Value
                $dominio    = [string]$props[6].Value
                $ip         = [string]$props[18].Value
                $workstation= [string]$props[11].Value
                $proceso    = [string]$props[9].Value

                if ($usuario -in @('-','','ANONYMOUS LOGON')) { continue }
                if ($tipoLogon -eq 3 -and $ip -in @('-','::1','127.0.0.1','')) { continue }

                $nombreUser = if ($dominio -and $dominio -notin @('-','NT AUTHORITY','NT-AUTORITÄT','')) {
                    "$dominio\$usuario"
                } else { $usuario }
                $ipStr = if ($ip -in @('-','')) { 'Local' } else { $ip }
                $tipo = if ($tipoLogon -eq 10) { 'RDP' } else { 'Red/SMB' }
                $resultado = if ($ev.Id -eq 4624) { 'Exito' } else { 'Fallo' }

                $resultados += [PSCustomObject]@{
                    Fecha       = $ev.TimeCreated.ToString('dd/MM/yyyy HH:mm:ss')
                    Usuario     = $nombreUser
                    IPOrigen    = $ipStr
                    TipoAcceso  = $tipo
                    Resultado   = $resultado
                    Workstation = if ($workstation -in @('-','')) { 'N/A' } else { $workstation }
                    Proceso     = $proceso
                    IdEvento    = [string]$ev.Id
                }
            } catch {}
        }
    } catch {}

    # --- Fuente 2: TerminalServices-RemoteConnectionManager (evento 1149 = RDP autenticado) ---
    try {
        $filtroRdp = @{
            LogName='Microsoft-Windows-TerminalServices-RemoteConnectionManager/Operational'
            Id=1149
            StartTime=$fechaInicio
        }
        $eventosRdp = Get-WinEvent -FilterHashtable $filtroRdp -MaxEvents 500 -ErrorAction SilentlyContinue

        foreach ($ev in $eventosRdp) {
            try {
                $props = $ev.Properties
                $usuario = [string]$props[0].Value
                $dominio = [string]$props[1].Value
                $ip      = [string]$props[2].Value

                $nombreUser = if ($dominio -and $dominio -ne '') { "$dominio\$usuario" } else { $usuario }
                $ipStr = if ($ip -in @('','::1','127.0.0.1')) { 'Local' } else { $ip }

                $resultados += [PSCustomObject]@{
                    Fecha       = $ev.TimeCreated.ToString('dd/MM/yyyy HH:mm:ss')
                    Usuario     = $nombreUser
                    IPOrigen    = $ipStr
                    TipoAcceso  = 'RDP (Auth)'
                    Resultado   = 'Exito'
                    Workstation = 'N/A'
                    Proceso     = 'TerminalServices'
                    IdEvento    = '1149'
                }
            } catch {}
        }
    } catch {}

    # Ordenar por fecha descendente y emitir JSON — OJO: se ordena parseando
    # 'Fecha' (texto 'dd/MM/yyyy HH:mm:ss') de vuelta a [datetime], NUNCA
    # como texto plano: con el día por delante, 'Sort-Object Fecha' compara
    # alfabéticamente y '05/07/2026' queda antes que '28/06/2026' aunque sea
    # posterior — la vista por defecto (antes de tocar ninguna cabecera)
    # mostraba eventos recientes por debajo de otros más antiguos en cuanto
    # la ventana de 30 días cruzaba un mes.
    $resultados = $resultados | Sort-Object {
        [datetime]::ParseExact($_.Fecha, 'dd/MM/yyyy HH:mm:ss', [System.Globalization.CultureInfo]::InvariantCulture)
    } -Descending
    if ($resultados.Count -gt 0) {
        $resultados | ConvertTo-Json -Depth 3
    } else {
        '[]'
    }
} catch {
    @([PSCustomObject]@{
        Fecha=''; Usuario='Error'; IPOrigen='N/A'; TipoAcceso='Error'
        Resultado='Error'; Workstation='N/A'; Proceso=$_.Exception.Message; IdEvento='0'
    }) | ConvertTo-Json
}
"""

# Comprueba lo que YA se sabe sobre la memoria sin necesidad de reiniciar:
# errores de hardware ya detectados (WHEA-Logger, últimos 90 días) y el
# resultado de la última vez que se ejecutó el Diagnóstico de memoria de
# Windows (mdsched.exe), si se hizo alguna vez. Una prueba completa nueva
# SOLO puede hacerse reiniciando (Windows necesita control exclusivo de la
# RAM) — eso lo lanza el propio mdsched.exe, con su diálogo nativo, no algo
# que se intente automatizar aquí.
PS_CHECK_MEMORY_DIAGNOSTICS = r"""
try {
    $errores = @()
    try {
        $eventosWhea = Get-WinEvent -FilterHashtable @{
            LogName = "System"; ProviderName = "Microsoft-Windows-WHEA-Logger"
            StartTime = (Get-Date).AddDays(-90)
        } -MaxEvents 20 -ErrorAction SilentlyContinue
        foreach ($ev in $eventosWhea) {
            $errores += @{ Fecha = $ev.TimeCreated.ToString("dd/MM/yyyy HH:mm:ss") }
        }
    } catch {}

    $ultimoDiagnostico = $null
    try {
        $evDiag = Get-WinEvent -FilterHashtable @{
            LogName = "System"; ProviderName = "Microsoft-Windows-MemoryDiagnostics-Results"
        } -MaxEvents 1 -ErrorAction SilentlyContinue
        if ($evDiag) {
            $ultimoDiagnostico = @{
                Fecha = $evDiag.TimeCreated.ToString("dd/MM/yyyy HH:mm:ss")
                Mensaje = $evDiag.Message
            }
        }
    } catch {}

    @{ ErroresHardware = $errores; UltimoDiagnostico = $ultimoDiagnostico } | ConvertTo-Json -Depth 5
} catch {
    @{ ErroresHardware = @(); UltimoDiagnostico = $null } | ConvertTo-Json
}
"""

# Ping estructurado (Test-Connection) para un host de Red Local — Python
# antepone '$ip = "..."' antes de este bloque (mismo patrón que
# PS_COMPROBAR_PUERTO), nunca .format() sobre todo el script: evitaría
# colisionar con la sintaxis de bloques { } propia de PowerShell.
PS_PING_HOST = r"""
try {
    $resultados = Test-Connection -ComputerName $ip -Count 4 -ErrorAction Stop
    $tiempos = @($resultados | ForEach-Object { $_.ResponseTime })
    $stats = $tiempos | Measure-Object -Minimum -Maximum -Average
    @{
        Alcanzable = $true
        Enviados = 4
        Recibidos = $resultados.Count
        TiempoMinimo = [int]$stats.Minimum
        TiempoMaximo = [int]$stats.Maximum
        TiempoMedio = [math]::Round($stats.Average, 1)
    } | ConvertTo-Json
} catch {
    @{ Alcanzable = $false; Enviados = 4; Recibidos = 0; Error = $_.Exception.Message } | ConvertTo-Json
}
"""

# ---------------------------
# Copias de seguridad — Configuración del sistema
# (msys/backup_generators.py, categoría "Configuración del sistema")
# ---------------------------

# {destino} lo sustituye run_powershell_json_params (escapado automático) —
# literales @{{ }} doblados, igual que el resto de plantillas de este
# fichero que usan esa función.
PS_BACKUP_TAREAS_PROGRAMADAS = r"""
$ErrorActionPreference = 'SilentlyContinue'
$destino = '{destino}'
$total = 0
$fallidas = 0
foreach ($t in (Get-ScheduledTask)) {{
    try {{
        $xml = Export-ScheduledTask -TaskName $t.TaskName -TaskPath $t.TaskPath
        $carpeta = ($t.TaskPath.Trim('\')) -replace '[\\/:*?"<>|]', '_'
        $nombre = $t.TaskName -replace '[\\/:*?"<>|]', '_'
        $archivo = if ($carpeta) {{ "$carpeta`_$nombre.xml" }} else {{ "$nombre.xml" }}
        $xml | Out-File -LiteralPath (Join-Path $destino $archivo) -Encoding UTF8
        $total++
    }} catch {{
        $fallidas++
    }}
}}
@{{ Total = $total; Fallidas = $fallidas }} | ConvertTo-Json
"""

# Solo METADATOS de configuración (nombre, inicio, cuenta, ruta) — no es un
# backup restaurable con un solo comando (no existe un 'sc import' directo),
# es una fotografía documental para poder reconfigurar servicios a mano
# tras una reinstalación sabiendo cómo estaban antes.
PS_BACKUP_SERVICIOS = r"""
try {{
    Get-CimInstance Win32_Service |
        Select-Object Name, DisplayName, StartMode, State, StartName, PathName |
        Export-Csv -LiteralPath '{destino}\Servicios.csv' -NoTypeInformation -Encoding UTF8
    @{{ Ok = $true }} | ConvertTo-Json
}} catch {{
    @{{ Ok = $false; Error = $_.Exception.Message }} | ConvertTo-Json
}}
"""

# Mismo criterio que Servicios: fotografía documental (nombre, puerto,
# controlador, compartición), no reinstala drivers ni recrea la cola.
PS_BACKUP_IMPRESORAS = r"""
try {{
    Get-Printer |
        Select-Object Name, DriverName, PortName, Shared, Published, Type |
        Export-Csv -LiteralPath '{destino}\Impresoras.csv' -NoTypeInformation -Encoding UTF8
    @{{ Ok = $true }} | ConvertTo-Json
}} catch {{
    @{{ Ok = $false; Error = $_.Exception.Message }} | ConvertTo-Json
}}
"""

# Solo el certificado PÚBLICO (.cer) — exportar la clave privada exigiría
# contraseña por certificado (PFX interactivo), fuera de alcance de un
# backup por lotes sin supervisión.
PS_BACKUP_CERTIFICADOS = r"""
$ErrorActionPreference = 'SilentlyContinue'
$destino = '{destino}'
$total = 0
foreach ($cert in (Get-ChildItem Cert:\CurrentUser\My)) {{
    try {{
        $nombre = ($cert.Subject -replace '[\\/:*?"<>|,=]', '_')
        if ($nombre.Length -gt 80) {{ $nombre = $nombre.Substring(0, 80) }}
        $archivo = Join-Path $destino "$nombre`_$($cert.Thumbprint.Substring(0,8)).cer"
        Export-Certificate -Cert $cert -FilePath $archivo -Type CERT | Out-Null
        $total++
    }} catch {{}}
}}
@{{ Total = $total }} | ConvertTo-Json
"""

