<#
.SINOPSIS
    Informe de SharePoint y OneDrive que tienen contenido compartido con personas
    externas. Genera un Excel listo para presentar.

.DESCRIPCION
    Solo lectura. No registra aplicaciones, no usa Microsoft Graph, no modifica
    permisos ni entra en el contenido de ningun sitio u OneDrive.

    Usa dos fuentes:
      1. Registro de auditoria de Microsoft Purview (modulo ExchangeOnlineManagement):
         quien compartio con externos, con quien, que y cuando. Cubre los ultimos
         180 dias (retencion estandar de Microsoft 365).
      2. Invitados externos registrados por sitio (Get-SPOExternalUser, modulo de
         SharePoint): sirve para ver sitios que compartieron fuera antes de esos
         180 dias. Puede no estar completa con invitados de Entra ID (B2B), por eso
         es un dato complementario.

    Excluye los sitios conectados a Teams y los sitios tecnicos de Microsoft
    (busqueda, catalogo de apps, redirecciones, portales antiguos, raiz de OneDrive).

.COMO SE USA
    1. Abre PowerShell (7 o Windows PowerShell 5.1, da igual) en la carpeta del script.
    2. Si Windows bloquea el script por no estar firmado:
           Set-ExecutionPolicy -Scope Process Bypass -Force
    3. Ejecuta:
           .\Informe-CompartidoExterno.ps1
    4. Te pedira:
         - El tenant (p.ej. "contoso") y confirmacion.
         - La carpeta donde guardar el Excel (Enter = carpeta Resultados junto al script).
         - Inicio de sesion de Microsoft DOS veces: una para SharePoint y otra para
           el registro de auditoria (Exchange Online). Misma cuenta.
    5. Al terminar tendras en la carpeta de salida:
         - Compartido_Externo_<fecha>.xlsx  -> el informe
         - Compartido_Externo_<fecha>.log   -> el registro de la ejecucion

    Parametros opcionales:
        -TenantAdminUrl contoso          (no pregunta el tenant)
        -Dias 90                         (revisar menos dias de auditoria; maximo 180)

.REQUISITOS
    - Cuenta con rol de Administrador global, o bien Administrador de SharePoint +
      permiso para consultar el registro de auditoria (rol "View-Only Audit Logs"
      o "Audit Logs" en Exchange Online / Purview).
    - El registro de auditoria debe estar activado en el tenant (lo esta por defecto).
    - Modulos (se instalan solos la primera vez, para el usuario actual):
        Microsoft.Online.SharePoint.PowerShell 16.0.27612.12000
          (la 16.0.27709.12000 tiene un fallo de inicio de sesion)
        ExchangeOnlineManagement
        ImportExcel (genera el .xlsx sin necesitar Excel instalado)

.CONFIDENCIALIDAD
    El Excel contiene nombres de archivos y con quien se compartieron, incluidos
    OneDrive personales. Tratalo como documento confidencial.
#>

param(
    [string]$TenantAdminUrl,
    [ValidateRange(1, 180)][int]$Dias = 180,
    # Uso interno: el script se llama a si mismo para cada fase.
    [ValidateSet('Principal', 'SPO', 'Auditoria')][string]$Fase = 'Principal',
    [string]$CarpetaTrabajo
)

$ErrorActionPreference = 'Stop'
$VersionModuloSPO = '16.0.27612.12000'

# ============================================================
# RELANZAR EN WINDOWS POWERSHELL 5.1 SI SE EJECUTA DESDE PS7
# ============================================================
# El modulo de SharePoint solo inicia sesion correctamente en Windows PowerShell 5.1.
if ($PSVersionTable.PSVersion.Major -ge 6) {
    Write-Host "Detectado PowerShell $($PSVersionTable.PSVersion). Relanzando en Windows PowerShell 5.1..." -ForegroundColor Yellow
    $argumentos = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath, '-Dias', $Dias)
    if ($TenantAdminUrl) { $argumentos += @('-TenantAdminUrl', $TenantAdminUrl) }
    & powershell.exe @argumentos
    exit $LASTEXITCODE
}

[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

# ============================================================
# FUNCIONES COMUNES
# ============================================================
$script:ArchivoLog = $null
if ($CarpetaTrabajo) { $script:ArchivoLog = Join-Path $CarpetaTrabajo 'log.txt' }

function Write-Log {
    param([Parameter(Mandatory = $true)][string]$Mensaje,
          [ValidateSet('INFO', 'AVISO', 'ERROR')][string]$Nivel = 'INFO')
    $linea = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Nivel, $Mensaje
    if ($script:ArchivoLog) { Add-Content -Path $script:ArchivoLog -Value $linea -Encoding UTF8 }
    $color = switch ($Nivel) { 'AVISO' { 'Yellow' } 'ERROR' { 'Red' } default { 'Gray' } }
    Write-Host $linea -ForegroundColor $color
}

function Asegurar-Modulo {
    param([string]$Nombre, [string]$Version)
    $disponible = @(Get-Module -ListAvailable -Name $Nombre)
    if ($Version) { $disponible = @($disponible | Where-Object { $_.Version -eq [version]$Version }) }
    if ($disponible.Count -eq 0) {
        Write-Log "Instalando el modulo $Nombre $Version (solo la primera vez)..." 'AVISO'
        if (-not (Get-PackageProvider -ListAvailable -Name NuGet -ErrorAction SilentlyContinue)) {
            Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Scope CurrentUser -Force | Out-Null
        }
        $p = @{ Name = $Nombre; Scope = 'CurrentUser'; Force = $true; AllowClobber = $true }
        if ($Version) { $p.RequiredVersion = $Version }
        Install-Module @p
    }
    $i = @{ Name = $Nombre; DisableNameChecking = $true }
    if ($Version) { $i.RequiredVersion = $Version }
    Import-Module @i
}

function Obtener-Prop {
    param($Objeto, [string]$Nombre)
    if ($null -ne $Objeto -and $Objeto.PSObject.Properties.Name -contains $Nombre) { return $Objeto.$Nombre }
    return $null
}

function Clave-Url {
    param([string]$Url)
    if (-not $Url) { return '' }
    return $Url.Trim().TrimEnd('/').ToLowerInvariant()
}

function Fecha-Iso {
    param($Fecha)
    if ($null -eq $Fecha -or $Fecha -eq '') { return '' }
    return ([datetime]$Fecha).ToString('yyyy-MM-ddTHH:mm:ss', [Globalization.CultureInfo]::InvariantCulture)
}

function Leer-Fecha {
    param([string]$Texto)
    if (-not $Texto) { return $null }
    return [datetime]::ParseExact($Texto, 'yyyy-MM-ddTHH:mm:ss', [Globalization.CultureInfo]::InvariantCulture)
}

# ============================================================
# FASE 1 (proceso aparte): SHAREPOINT
# Lista de sitios y OneDrive + invitados externos registrados en cada uno.
# ============================================================
if ($Fase -eq 'SPO') {
    try {
        Asegurar-Modulo -Nombre 'Microsoft.Online.SharePoint.PowerShell' -Version $VersionModuloSPO
        Write-Log "Conectando a SharePoint ($TenantAdminUrl). Se abrira la ventana de inicio de sesion..."
        try { Connect-SPOService -Url $TenantAdminUrl }
        catch {
            if ($_.Exception.Message -match 'OAuth') {
                Connect-SPOService -Url $TenantAdminUrl -ModernAuth $true -AuthenticationUrl 'https://login.microsoftonline.com/organizations'
            }
            else { throw }
        }
        Write-Log 'Conectado a SharePoint. Obteniendo sitios y OneDrive...'

        $sitios = @(Get-SPOSite -Limit All -IncludePersonalSite $true)
        Write-Log "Sitios obtenidos: $($sitios.Count)"

        $tienePropTeams = $sitios.Count -gt 0 -and ($sitios[0].PSObject.Properties.Name -contains 'IsTeamsConnected')
        if (-not $tienePropTeams) {
            Write-Log "El modulo no indica que sitios estan conectados a Teams; los sitios de grupo con Teams no se podran excluir." 'AVISO'
        }
        $plantillasSistema = @('RedirectSite#*', 'SRCHCEN#*', 'POINTPUBLISHING*', 'APPCATALOG#*', 'SPSMSITEHOST#*')

        $listaSitios = foreach ($s in $sitios) {
            $excluido = ''
            if ($s.Template -like 'TEAMCHANNEL#*' -or (Obtener-Prop $s 'IsTeamsChannelConnected') -or ($tienePropTeams -and $s.IsTeamsConnected)) {
                $excluido = 'Teams'
            }
            else {
                foreach ($p in $plantillasSistema) { if ($s.Template -like $p) { $excluido = 'Sistema' } }
            }
            [pscustomobject]@{
                Url         = $s.Url
                Titulo      = $s.Title
                Tipo        = $(if ($s.Url -match '-my\.sharepoint\.com/personal/') { 'OneDrive' } else { 'SharePoint' })
                Plantilla   = $s.Template
                Propietario = $s.Owner
                Compartir   = [string]$s.SharingCapability
                Excluido    = $excluido
                Bloqueo     = [string](Obtener-Prop $s 'LockState')
                Revision    = ''
            }
        }
        $listaSitios = @($listaSitios)
        $incluidos = @($listaSitios | Where-Object { $_.Excluido -eq '' })
        Write-Log ("Incluidos: {0} | Excluidos Teams: {1} | Excluidos sistema: {2}" -f $incluidos.Count,
            @($listaSitios | Where-Object { $_.Excluido -eq 'Teams' }).Count,
            @($listaSitios | Where-Object { $_.Excluido -eq 'Sistema' }).Count)

        Write-Log 'Revisando invitados externos registrados en cada sitio (puede tardar unos minutos)...'
        $invitados = New-Object System.Collections.Generic.List[object]
        $errores = 0
        $n = 0
        foreach ($s in $incluidos) {
            $n++
            Write-Progress -Activity 'Invitados externos por sitio' -Status $s.Url -PercentComplete (100 * $n / [Math]::Max(1, $incluidos.Count))
            # Sitios bloqueados (normalmente OneDrive de usuarios dados de baja): nadie
            # puede entrar, ni el administrador. Se marcan y no se intentan leer.
            if ($s.Bloqueo -eq 'NoAccess') {
                $s.Revision = 'Bloqueado'
                continue
            }
            try {
                $pos = 0
                do {
                    $pagina = @(Get-SPOExternalUser -SiteUrl $s.Url -Position $pos -PageSize 50 -ErrorAction Stop)
                    foreach ($u in $pagina) {
                        $invitados.Add([pscustomobject]@{
                            SitioUrl     = $s.Url
                            Nombre       = Obtener-Prop $u 'DisplayName'
                            Correo       = Obtener-Prop $u 'Email'
                            AceptadoComo = Obtener-Prop $u 'AcceptedAs'
                            InvitadoPor  = Obtener-Prop $u 'InvitedBy'
                            FechaAlta    = Fecha-Iso (Obtener-Prop $u 'WhenCreated')
                        })
                    }
                    $pos += 50
                } while ($pagina.Count -eq 50)
                $s.Revision = 'OK'
            }
            catch {
                if ($_.Exception.Message -match 'bloqueado|blocked|locked') {
                    $s.Revision = 'Bloqueado'
                }
                else {
                    $errores++
                    $s.Revision = 'Error: ' + $_.Exception.Message
                    Write-Log "No se pudieron leer los invitados de $($s.Url): $($_.Exception.Message)" 'AVISO'
                }
            }
        }
        Write-Progress -Activity 'Invitados externos por sitio' -Completed
        $bloqueados = @($incluidos | Where-Object { $_.Revision -eq 'Bloqueado' }).Count
        Write-Log "Invitados externos registrados encontrados: $($invitados.Count) | Sitios bloqueados (usuarios de baja): $bloqueados | Sitios con error de lectura: $errores"

        $listaSitios | Export-Csv -Path (Join-Path $CarpetaTrabajo 'sitios.csv') -NoTypeInformation -Encoding UTF8
        $invitados | Export-Csv -Path (Join-Path $CarpetaTrabajo 'invitados.csv') -NoTypeInformation -Encoding UTF8
        Disconnect-SPOService -ErrorAction SilentlyContinue
        exit 0
    }
    catch {
        Write-Log "Fase SharePoint: $($_.Exception.Message)" 'ERROR'
        exit 1
    }
}

# ============================================================
# FASE 2 (proceso aparte): REGISTRO DE AUDITORIA
# Se ejecuta en un proceso distinto al de SharePoint porque ambos modulos cargan
# versiones distintas de la misma libreria de inicio de sesion y chocan.
# ============================================================
if ($Fase -eq 'Auditoria') {
    try {
        Asegurar-Modulo -Nombre 'ExchangeOnlineManagement'
        Write-Log 'Conectando a Exchange Online para leer el registro de auditoria. Se abrira la ventana de inicio de sesion...'
        # La ventana integrada de inicio de sesion se queda colgada a veces en Windows
        # PowerShell 5.1. Se usa el inicio de sesion con codigo: no depende de ninguna
        # ventana y admite MFA.
        Write-Host ''
        Write-Host '>>> INICIO DE SESION CON CODIGO (registro de auditoria) <<<' -ForegroundColor Cyan
        Write-Host '    1. Abre https://microsoft.com/devicelogin en tu navegador.' -ForegroundColor Cyan
        Write-Host '    2. Escribe el codigo que aparece justo debajo.' -ForegroundColor Cyan
        Write-Host '    3. Inicia sesion con tu cuenta de administrador y vuelve aqui.' -ForegroundColor Cyan
        Write-Host ''
        try { Connect-ExchangeOnline -ShowBanner:$false -Device }
        catch {
            Write-Log "Fallo el inicio de sesion con codigo ($($_.Exception.Message)). Reintentando por navegador..." 'AVISO'
            if ((Get-Command Connect-ExchangeOnline).Parameters.ContainsKey('DisableWAM')) {
                Connect-ExchangeOnline -ShowBanner:$false -DisableWAM
            }
            else { Connect-ExchangeOnline -ShowBanner:$false }
        }
        Write-Log 'Conectado a Exchange Online.'

        try {
            if (-not (Get-AdminAuditLogConfig).UnifiedAuditLogIngestionEnabled) {
                Write-Log 'El registro de auditoria parece DESACTIVADO en el tenant: puede no haber datos.' 'AVISO'
            }
        }
        catch { }

        # Dominios propios del tenant: cualquier correo fuera de ellos es externo.
        $dominios = @(Get-AcceptedDomain | ForEach-Object { $_.DomainName.ToString().ToLowerInvariant() })
        Write-Log "Dominios internos del tenant: $($dominios -join ', ')"

        function Normalizar-Cuenta {
            param([string]$Cuenta)
            if (-not $Cuenta) { return '' }
            $c = $Cuenta.Trim() -replace '^i:0#\.f\|membership\|', '' -replace '^urn:spo:guest#', ''
            if ($c -match '^(.+)_([^_@]+\.[^_@]+)#ext#@') { $c = "$($Matches[1])@$($Matches[2])" }
            return $c.ToLowerInvariant()
        }
        function Es-Externa {
            param([string]$Cuenta, [string]$TipoDestino)
            if ($TipoDestino -eq 'Guest') { return $true }
            if ($Cuenta -match '#ext#|urn:spo:guest') { return $true }
            $n = Normalizar-Cuenta $Cuenta
            if ($n -match '@([^@]+)$') { return -not ($dominios -contains $Matches[1]) }
            return $false
        }

        $acciones = @{
            'AnonymousLinkCreated'      = 'Enlace público creado'
            'AnonymousLinkUpdated'      = 'Enlace público modificado'
            'AnonymousLinkUsed'         = 'Enlace público usado'
            'SharingInvitationCreated'  = 'Invitación a externo'
            'SharingInvitationAccepted' = 'Externo aceptó invitación'
            'SharingSet'                = 'Permiso dado a externo'
            'AddedToSecureLink'         = 'Externo añadido a un enlace'
        }
        $operaciones = @($acciones.Keys)

        $fin = (Get-Date).ToUniversalTime()
        $inicio = $fin.AddDays(-$Dias)
        Write-Log ("Buscando en el registro de auditoria del {0:dd/MM/yyyy} al {1:dd/MM/yyyy} (por semanas)..." -f $inicio.ToLocalTime(), $fin.ToLocalTime())

        $vistos = @{}
        $eventos = New-Object System.Collections.Generic.List[object]
        $desde = $inicio
        while ($desde -lt $fin) {
            $hasta = $desde.AddDays(7)
            if ($hasta -gt $fin) { $hasta = $fin }
            Write-Progress -Activity 'Registro de auditoria' -Status ("{0:dd/MM/yyyy} - {1:dd/MM/yyyy}" -f $desde, $hasta) -PercentComplete (100 * ($desde - $inicio).TotalDays / $Dias)
            $sesion = [guid]::NewGuid().ToString()
            $leidos = 0
            while ($true) {
                $res = $null
                for ($intento = 1; $intento -le 3; $intento++) {
                    try {
                        $res = @(Search-UnifiedAuditLog -StartDate $desde -EndDate $hasta -Operations $operaciones -SessionId $sesion -SessionCommand ReturnLargeSet -ResultSize 5000 -ErrorAction Stop)
                        break
                    }
                    catch {
                        if ($intento -eq 3) { throw }
                        Write-Log "Error consultando la auditoria (intento $intento): $($_.Exception.Message). Reintentando..." 'AVISO'
                        Start-Sleep -Seconds (10 * $intento)
                    }
                }
                if (-not $res -or $res.Count -eq 0) { break }
                $leidos += $res.Count
                foreach ($r in $res) {
                    if ($vistos.ContainsKey($r.Identity)) { continue }
                    $vistos[$r.Identity] = $true
                    $eventos.Add($r)
                }
                $totalVentana = [int]$res[0].ResultCount
                if ($totalVentana -ge 50000) {
                    Write-Log ("La semana del {0:dd/MM/yyyy} tiene 50.000 eventos o mas: el resultado de esa semana puede estar incompleto." -f $desde) 'AVISO'
                }
                if ($leidos -ge $totalVentana) { break }
            }
            $desde = $hasta
        }
        Write-Progress -Activity 'Registro de auditoria' -Completed
        Write-Log "Eventos de comparticion encontrados (internos y externos): $($eventos.Count)"

        $filas = New-Object System.Collections.Generic.List[object]
        foreach ($e in $eventos) {
            try { $a = $e.AuditData | ConvertFrom-Json } catch { continue }
            $op = [string]$a.Operation
            $usuario = [string](Obtener-Prop $a 'UserId')
            $destino = [string](Obtener-Prop $a 'TargetUserOrGroupName')
            $tipoDestino = [string](Obtener-Prop $a 'TargetUserOrGroupType')
            $quien = ''; $conQuien = ''; $externo = $false

            switch -Wildcard ($op) {
                'AnonymousLink*' {
                    $externo = $true
                    $conQuien = 'Cualquiera con el enlace (público)'
                    if ($op -ne 'AnonymousLinkUsed') { $quien = Normalizar-Cuenta $usuario }
                }
                'SharingInvitationAccepted' {
                    $externo = Es-Externa $usuario ''
                    $conQuien = Normalizar-Cuenta $usuario
                }
                default {
                    $externo = Es-Externa $destino $tipoDestino
                    $quien = Normalizar-Cuenta $usuario
                    $conQuien = Normalizar-Cuenta $destino
                }
            }
            if (-not $externo) { continue }

            $dominio = ''
            if ($conQuien -match '@([^@]+)$') { $dominio = $Matches[1] }

            $permiso = ''
            $ed = [string](Obtener-Prop $a 'EventData')
            if ($ed -match '<(?:Type|PermissionsGranted|Role)>([^<]+)<') {
                $bruto = $Matches[1]
                $permiso = switch -Regex ($bruto) {
                    'View|Read'             { 'Lectura'; break }
                    'Edit|Contribute|Write' { 'Edición'; break }
                    'Review'                { 'Revisión'; break }
                    'Full'                  { 'Control total'; break }
                    default                 { $bruto }
                }
            }

            $tipoElemento = switch ([string](Obtener-Prop $a 'ItemType')) {
                'File'   { 'Archivo' }
                'Folder' { 'Carpeta' }
                'Web'    { 'Sitio completo' }
                'List'   { 'Biblioteca / lista' }
                default  { [string](Obtener-Prop $a 'ItemType') }
            }

            $fechaUtc = [datetime]::SpecifyKind([datetime]::Parse([string]$a.CreationTime, [Globalization.CultureInfo]::InvariantCulture), [DateTimeKind]::Utc)
            $filas.Add([pscustomobject]@{
                Fecha        = Fecha-Iso $fechaUtc.ToLocalTime()
                Accion       = $acciones[$op]
                SitioUrl     = [string](Obtener-Prop $a 'SiteUrl')
                Quien        = $quien
                ConQuien     = $conQuien
                Dominio      = $dominio
                Elemento     = [string](Obtener-Prop $a 'SourceFileName')
                TipoElemento = $tipoElemento
                Permiso      = $permiso
                Ruta         = [string](Obtener-Prop $a 'ObjectId')
            })
        }
        Write-Log "Eventos de comparticion con EXTERNOS: $($filas.Count)"
        $rutaAuditoria = Join-Path $CarpetaTrabajo 'auditoria.csv'
        if ($filas.Count -gt 0) { $filas | Export-Csv -Path $rutaAuditoria -NoTypeInformation -Encoding UTF8 }
        else { Set-Content -Path $rutaAuditoria -Value '' -Encoding UTF8 }  # sin eventos, pero la fase ha ido bien
        Disconnect-ExchangeOnline -Confirm:$false -ErrorAction SilentlyContinue
        exit 0
    }
    catch {
        Write-Log "Fase auditoria: $($_.Exception.Message)" 'ERROR'
        Disconnect-ExchangeOnline -Confirm:$false -ErrorAction SilentlyContinue
        exit 1
    }
}

# ============================================================
# FASE PRINCIPAL: PREGUNTAS, LANZAR FASES Y GENERAR EL EXCEL
# ============================================================
Write-Host ''
Write-Host '=== Informe de contenido compartido con externos (SharePoint y OneDrive, sin Teams) ==='
Write-Host ''

while ($true) {
    if ([string]::IsNullOrWhiteSpace($TenantAdminUrl)) {
        $TenantAdminUrl = Read-Host "Nombre del tenant (p.ej. 'contoso'), dominio contoso.onmicrosoft.com o URL https://contoso-admin.sharepoint.com"
    }
    $entrada = $TenantAdminUrl.Trim().TrimEnd('/')
    if ($entrada -match '(?:^|@|//)([a-zA-Z0-9-]+)\.onmicrosoft\.com$') { $TenantAdminUrl = "https://$($Matches[1])-admin.sharepoint.com" }
    elseif ($entrada -match '@') {
        Write-Host 'Con un correo de dominio propio no se puede deducir el tenant. Escribe el nombre del tenant.' -ForegroundColor Yellow
        $TenantAdminUrl = $null; continue
    }
    elseif ($entrada -match '^[a-zA-Z0-9-]+$') { $TenantAdminUrl = "https://$($entrada -replace '-admin$', '')-admin.sharepoint.com" }
    elseif ($entrada -match '^https://([a-zA-Z0-9-]+?)(-admin|-my)?\.sharepoint\.com') { $TenantAdminUrl = "https://$($Matches[1])-admin.sharepoint.com" }
    else {
        Write-Host "Entrada no valida: '$entrada'." -ForegroundColor Yellow
        $TenantAdminUrl = $null; continue
    }
    if ((Read-Host "Se conectara a $TenantAdminUrl . Correcto? (S/N)") -match '^[sSyY]') { break }
    $TenantAdminUrl = $null
}
$NombreTenant = ($TenantAdminUrl -replace '^https://', '' -replace '-admin\.sharepoint\.com$', '')

$CarpetaPorDefecto = Join-Path $PSScriptRoot 'Resultados'   # como el resto de herramientas
$CarpetaSalida = Read-Host "Carpeta donde guardar el Excel (Enter = $CarpetaPorDefecto)"
if ([string]::IsNullOrWhiteSpace($CarpetaSalida)) { $CarpetaSalida = $CarpetaPorDefecto }
$CarpetaSalida = $CarpetaSalida.Trim().Trim('"')
if (-not (Test-Path $CarpetaSalida)) { New-Item -Path $CarpetaSalida -ItemType Directory -Force | Out-Null }

$FechaEjecucion = Get-Date -Format 'yyyyMMdd_HHmmss'
$CarpetaTrabajo = Join-Path $env:TEMP "CompartidoExterno_$FechaEjecucion"
New-Item -Path $CarpetaTrabajo -ItemType Directory -Force | Out-Null
$script:ArchivoLog = Join-Path $CarpetaTrabajo 'log.txt'
$ArchivoExcel = Join-Path $CarpetaSalida "Compartido_Externo_$FechaEjecucion.xlsx"
$ArchivoLogFinal = Join-Path $CarpetaSalida "Compartido_Externo_$FechaEjecucion.log"

Write-Host ''
Write-Host 'Hay que iniciar sesion DOS veces con tu cuenta de administrador:' -ForegroundColor Cyan
Write-Host '  1. SharePoint: se abre la ventana de Microsoft.' -ForegroundColor Cyan
Write-Host '  2. Registro de auditoria: con un codigo en https://microsoft.com/devicelogin' -ForegroundColor Cyan
Write-Host ''
Write-Log "Inicio. Tenant: $TenantAdminUrl | Dias de auditoria: $Dias"

try {
    Asegurar-Modulo -Nombre 'ImportExcel'
    Add-Type -AssemblyName System.Drawing
}
catch {
    Write-Log "No se pudo preparar el modulo ImportExcel: $($_.Exception.Message)" 'ERROR'
    exit 1
}

$argsBase = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath, '-TenantAdminUrl', $TenantAdminUrl, '-CarpetaTrabajo', $CarpetaTrabajo, '-Dias', $Dias)

Write-Log 'PASO 1 de 3: SharePoint (sitios, OneDrive e invitados externos)'
& powershell.exe @($argsBase + @('-Fase', 'SPO'))
if ($LASTEXITCODE -ne 0 -or -not (Test-Path (Join-Path $CarpetaTrabajo 'sitios.csv'))) {
    Write-Log 'La fase de SharePoint ha fallado; no se puede generar el informe. Revisa los mensajes anteriores.' 'ERROR'
    Copy-Item $script:ArchivoLog $ArchivoLogFinal -Force -ErrorAction SilentlyContinue
    exit 1
}

Write-Log 'PASO 2 de 3: registro de auditoria (Exchange Online)'
& powershell.exe @($argsBase + @('-Fase', 'Auditoria'))
$auditoriaOk = ($LASTEXITCODE -eq 0 -and (Test-Path (Join-Path $CarpetaTrabajo 'auditoria.csv')))
if (-not $auditoriaOk) {
    Write-Log 'La fase de auditoria ha fallado. El informe se generara SOLO con los invitados registrados por sitio.' 'AVISO'
}

Write-Log 'PASO 3 de 3: generando el Excel'

# ------------------------------------------------------------
# Carga y cruce de datos
# ------------------------------------------------------------
$sitios = @(Import-Csv (Join-Path $CarpetaTrabajo 'sitios.csv') -Encoding UTF8)
$invitados = @()
if (Test-Path (Join-Path $CarpetaTrabajo 'invitados.csv')) { $invitados = @(Import-Csv (Join-Path $CarpetaTrabajo 'invitados.csv') -Encoding UTF8) }
$auditoria = @()
if ($auditoriaOk) { $auditoria = @(Import-Csv (Join-Path $CarpetaTrabajo 'auditoria.csv') -Encoding UTF8) }

$mapaSitios = @{}
foreach ($s in $sitios) { $mapaSitios[(Clave-Url $s.Url)] = $s }
$incluidos = @($sitios | Where-Object { $_.Excluido -eq '' })

$textoCompartir = @{
    'Disabled'                        = 'Desactivado (solo internos)'
    'ExistingExternalUserSharingOnly' = 'Solo invitados ya existentes'
    'ExternalUserSharingOnly'         = 'Invitados nuevos y existentes'
    'ExternalUserAndGuestSharing'     = 'Cualquiera (enlaces públicos)'
}
function Nombre-Sitio($s) {
    if ($s.Titulo) { return $s.Titulo }
    return ($s.Url.TrimEnd('/') -split '/')[-1]
}

$ETIQ_PUBLICO = 'Cualquiera con el enlace (público)'
$EST_PUB = 'Con enlaces públicos'
$EST_FUERA = 'Comparte con externos'
$EST_ANT = 'Solo invitados antiguos'

# Eventos de auditoria agrupados por sitio incluido
$eventosPorSitio = @{}
$eventosTeams = 0
$eventosOtros = 0
$detalle = New-Object System.Collections.Generic.List[object]
foreach ($ev in $auditoria) {
    $clave = Clave-Url $ev.SitioUrl
    $sitio = $mapaSitios[$clave]
    if (-not $sitio) { $eventosOtros++; continue }
    if ($sitio.Excluido -eq 'Teams') { $eventosTeams++; continue }
    if ($sitio.Excluido -ne '') { $eventosOtros++; continue }
    if (-not $eventosPorSitio.ContainsKey($clave)) { $eventosPorSitio[$clave] = New-Object System.Collections.Generic.List[object] }
    $eventosPorSitio[$clave].Add($ev)
    $detalle.Add([pscustomobject][ordered]@{
        'Fecha'              = Leer-Fecha $ev.Fecha
        'Acción'             = $ev.Accion
        'Tipo'               = $sitio.Tipo
        'Sitio / OneDrive'   = Nombre-Sitio $sitio
        'Quién compartió'    = $ev.Quien
        'Con quién'          = $ev.ConQuien
        'Dominio externo'    = $ev.Dominio
        'Elemento'           = $ev.Elemento
        'Tipo de elemento'   = $ev.TipoElemento
        'Permiso'            = $ev.Permiso
        'Ruta'               = $ev.Ruta
    })
}
$detalle = @($detalle | Sort-Object Fecha -Descending)

$invitadosPorSitio = @{}
foreach ($i in $invitados) {
    $clave = Clave-Url $i.SitioUrl
    if (-not $invitadosPorSitio.ContainsKey($clave)) { $invitadosPorSitio[$clave] = 0 }
    $invitadosPorSitio[$clave]++
}

# Hoja por sitio
$filasSitios = foreach ($s in $incluidos) {
    $clave = Clave-Url $s.Url
    $evs = @(); if ($eventosPorSitio.ContainsKey($clave)) { $evs = $eventosPorSitio[$clave].ToArray() }
    $nInv = 0; if ($invitadosPorSitio.ContainsKey($clave)) { $nInv = $invitadosPorSitio[$clave] }
    if ($evs.Count -eq 0 -and $nInv -eq 0) { continue }

    $publicos = @($evs | Where-Object { $_.Accion -like 'Enlace público*' }).Count
    $personas = @($evs | Where-Object { $_.ConQuien -and $_.ConQuien -ne $ETIQ_PUBLICO } | Select-Object -ExpandProperty ConQuien -Unique).Count
    $ultimo = $null
    if ($evs.Count -gt 0) { $ultimo = ($evs | ForEach-Object { Leer-Fecha $_.Fecha } | Sort-Object -Descending | Select-Object -First 1) }
    $estado = if ($publicos -gt 0) { $EST_PUB } elseif ($evs.Count -gt 0) { $EST_FUERA } else { $EST_ANT }
    $compartir = $textoCompartir[$s.Compartir]; if (-not $compartir) { $compartir = $s.Compartir }

    [pscustomobject][ordered]@{
        'Tipo'                                 = $s.Tipo
        'Sitio / OneDrive'                     = Nombre-Sitio $s
        'Propietario'                          = $s.Propietario
        'Política para compartir'              = $compartir
        "Veces compartido fuera ($Dias d)"     = $evs.Count
        "Personas externas ($Dias d)"          = $personas
        "Enlaces públicos ($Dias d)"           = $publicos
        'Último compartido fuera'              = $ultimo
        'Invitados registrados'                = $nInv
        'Estado'                               = $estado
        'Enlace'                               = $s.Url
    }
}
$filasSitios = @($filasSitios | Sort-Object @{ Expression = { $_."Veces compartido fuera ($Dias d)" }; Descending = $true }, @{ Expression = { $_.'Invitados registrados' }; Descending = $true })

# Hoja de personas externas (auditoria)
$externos = @($detalle | Where-Object { $_.'Con quién' -and $_.'Con quién' -ne $ETIQ_PUBLICO } | Group-Object 'Con quién' | ForEach-Object {
    $g = @($_.Group)
    $sitiosG = @($g | Select-Object -ExpandProperty 'Sitio / OneDrive' -Unique)
    $txtSitios = ($sitiosG -join '; ')
    if ($txtSitios.Length -gt 300) { $txtSitios = $txtSitios.Substring(0, 297) + '...' }
    [pscustomobject][ordered]@{
        'Persona externa'      = $_.Name
        'Dominio / empresa'    = $g[0].'Dominio externo'
        'Nº sitios / OneDrive' = $sitiosG.Count
        'Veces'                = $g.Count
        'Primera vez'          = ($g | Sort-Object Fecha | Select-Object -First 1).Fecha
        'Última vez'           = ($g | Sort-Object Fecha -Descending | Select-Object -First 1).Fecha
        'Sitios / OneDrive'    = $txtSitios
    }
} | Sort-Object Veces -Descending)

# Hoja de invitados registrados
$filasInvitados = @(foreach ($i in $invitados) {
    $s = $mapaSitios[(Clave-Url $i.SitioUrl)]
    if (-not $s -or $s.Excluido -ne '') { continue }
    [pscustomobject][ordered]@{
        'Tipo'             = $s.Tipo
        'Sitio / OneDrive' = Nombre-Sitio $s
        'Invitado'         = $i.Nombre
        'Correo'           = $i.Correo
        'Aceptado como'    = $i.AceptadoComo
        'Invitado por'     = $i.InvitadoPor
        'Fecha de alta'    = Leer-Fecha $i.FechaAlta
        'Enlace'           = $s.Url
    }
})

# ------------------------------------------------------------
# Escritura del Excel
# ------------------------------------------------------------
$HOJA_SITIOS = 'Sitios con externos'
$HOJA_DETALLE = "Detalle ($Dias días)"
$HOJA_EXTERNOS = "Externos ($Dias días)"
$HOJA_INVITADOS = 'Invitados registrados'
$AZUL = [System.Drawing.Color]::FromArgb(31, 78, 120)
$GRIS = [System.Drawing.Color]::FromArgb(89, 89, 89)

function Sin-Datos([string]$Texto) { , @([pscustomobject]@{ 'Aviso' = $Texto }) }

function Exportar-Hoja {
    param($Paquete, $Datos, [string]$Hoja, [string]$Tabla, [string]$SiVacio)
    if (-not $Datos -or @($Datos).Count -eq 0) { $Datos = Sin-Datos $SiVacio }
    $p = @{ WorksheetName = $Hoja; TableName = $Tabla; TableStyle = 'Light9'; AutoSize = $true; FreezeTopRow = $true; PassThru = $true }
    if ($Paquete) { return ($Datos | Export-Excel -ExcelPackage $Paquete @p) }
    return ($Datos | Export-Excel -Path $ArchivoExcel @p)
}

function Formatear-Hoja {
    param($Hoja, [string[]]$ColFecha = @(), [string[]]$ColEnlace = @())
    if (-not $Hoja.Dimension) { return }
    $uc = $Hoja.Dimension.End.Column; $uf = $Hoja.Dimension.End.Row
    $todo = $Hoja.Cells[1, 1, $uf, $uc]
    $todo.Style.Font.Name = 'Arial'; $todo.Style.Font.Size = 10
    $cab = $Hoja.Cells[1, 1, 1, $uc]
    $cab.Style.Fill.PatternType = [OfficeOpenXml.Style.ExcelFillStyle]::Solid
    $cab.Style.Fill.BackgroundColor.SetColor($AZUL)
    $cab.Style.Font.Color.SetColor([System.Drawing.Color]::White)
    $cab.Style.Font.Bold = $true
    $cab.Style.WrapText = $true
    $cab.Style.VerticalAlignment = [OfficeOpenXml.Style.ExcelVerticalAlignment]::Center
    $Hoja.Row(1).Height = 32
    for ($c = 1; $c -le $uc; $c++) {
        $nombre = [string]$Hoja.Cells[1, $c].Value
        if ($Hoja.Column($c).Width -gt 60) { $Hoja.Column($c).Width = 60 }
        if ($Hoja.Column($c).Width -lt 12) { $Hoja.Column($c).Width = 12 }
        if ($ColFecha -contains $nombre -and $uf -ge 2) {
            $Hoja.Cells[2, $c, $uf, $c].Style.Numberformat.Format = 'dd/mm/yyyy hh:mm'
            $Hoja.Column($c).Width = 17
        }
        if ($ColEnlace -contains $nombre) {
            for ($r = 2; $r -le $uf; $r++) {
                $v = [string]$Hoja.Cells[$r, $c].Value
                if ($v -match '^https?://') {
                    try {
                        $Hoja.Cells[$r, $c].Hyperlink = New-Object System.Uri($v)
                        $Hoja.Cells[$r, $c].Style.Font.Color.SetColor([System.Drawing.Color]::FromArgb(5, 99, 193))
                        $Hoja.Cells[$r, $c].Style.Font.UnderLine = $true
                    }
                    catch { }
                }
            }
        }
    }
}

try {
    if (Test-Path $ArchivoExcel) { Remove-Item $ArchivoExcel -Force }

    $pkg = Exportar-Hoja $null $filasSitios $HOJA_SITIOS 'SitiosConExternos' 'Ningún SharePoint ni OneDrive tiene contenido compartido con externos.'
    $pkg = Exportar-Hoja $pkg $detalle $HOJA_DETALLE 'Detalle' $(if ($auditoriaOk) { "No hay comparticiones con externos en los últimos $Dias días." } else { 'No se pudo leer el registro de auditoría (ver log).' })
    $pkg = Exportar-Hoja $pkg $externos $HOJA_EXTERNOS 'Externos' $(if ($auditoriaOk) { "No hay personas externas en los últimos $Dias días." } else { 'No se pudo leer el registro de auditoría (ver log).' })
    $pkg = Exportar-Hoja $pkg $filasInvitados $HOJA_INVITADOS 'Invitados' 'No hay invitados externos registrados en los sitios revisados.'

    $wsS = $pkg.Workbook.Worksheets[$HOJA_SITIOS]
    Formatear-Hoja $wsS -ColFecha @('Último compartido fuera') -ColEnlace @('Enlace')
    if ($filasSitios.Count -gt 0) {
        $colores = @{ $EST_PUB = 'FFC7CE'; $EST_FUERA = 'FFEB9C'; $EST_ANT = 'DDEBF7' }
        for ($r = 2; $r -le $wsS.Dimension.End.Row; $r++) {
            $v = [string]$wsS.Cells[$r, 10].Value
            if ($colores.ContainsKey($v)) {
                $wsS.Cells[$r, 10].Style.Fill.PatternType = [OfficeOpenXml.Style.ExcelFillStyle]::Solid
                $wsS.Cells[$r, 10].Style.Fill.BackgroundColor.SetColor([System.Drawing.ColorTranslator]::FromHtml('#' + $colores[$v]))
            }
        }
    }
    Formatear-Hoja $pkg.Workbook.Worksheets[$HOJA_DETALLE] -ColFecha @('Fecha') -ColEnlace @('Ruta')
    Formatear-Hoja $pkg.Workbook.Worksheets[$HOJA_EXTERNOS] -ColFecha @('Primera vez', 'Última vez')
    Formatear-Hoja $pkg.Workbook.Worksheets[$HOJA_INVITADOS] -ColFecha @('Fecha de alta') -ColEnlace @('Enlace')

    # ---------------- Hoja Resumen ----------------
    $R = $pkg.Workbook.Worksheets.Add('Resumen')
    $pkg.Workbook.Worksheets.MoveToStart('Resumen')
    $R.View.ShowGridLines = $false
    $R.Cells.Style.Font.Name = 'Arial'
    $R.Column(1).Width = 2; $R.Column(2).Width = 58
    foreach ($c in 3..6) { $R.Column($c).Width = 16 }

    function Celda {
        param([string]$Dir, $Valor, [switch]$Negrita, [int]$Tam = 10, $Color, [string]$Formato, [switch]$Formula)
        $x = $R.Cells[$Dir]
        if ($Formula) { $x.Formula = $Valor } else { $x.Value = $Valor }
        $x.Style.Font.Name = 'Arial'; $x.Style.Font.Size = $Tam; $x.Style.Font.Bold = [bool]$Negrita
        if ($Color) { $x.Style.Font.Color.SetColor($Color) }
        if ($Formato) { $x.Style.Numberformat.Format = $Formato }
    }
    function Cabecera {
        param([int]$Fila, [string[]]$Textos)
        for ($i = 0; $i -lt $Textos.Count; $i++) {
            $x = $R.Cells[$Fila, (2 + $i)]
            $x.Value = $Textos[$i]
            $x.Style.Font.Bold = $true; $x.Style.Font.Size = 10
            $x.Style.Font.Color.SetColor([System.Drawing.Color]::White)
            $x.Style.Fill.PatternType = [OfficeOpenXml.Style.ExcelFillStyle]::Solid
            $x.Style.Fill.BackgroundColor.SetColor($AZUL)
            if ($i -gt 0) { $x.Style.HorizontalAlignment = [OfficeOpenXml.Style.ExcelHorizontalAlignment]::Center }
        }
    }
    function Linea {
        param([int]$Fila, [string]$Texto, [object[]]$Valores, [switch]$Negrita)
        $R.Cells[$Fila, 2].Value = $Texto
        $R.Cells[$Fila, 2].Style.Font.Bold = [bool]$Negrita
        for ($i = 0; $i -lt $Valores.Count; $i++) {
            $x = $R.Cells[$Fila, (3 + $i)]
            $v = $Valores[$i]
            if ($v -is [string] -and $v.StartsWith('=')) { $x.Formula = $v.Substring(1) } else { $x.Value = $v }
            $x.Style.Numberformat.Format = '#,##0'
            $x.Style.Font.Bold = [bool]$Negrita
        }
        $borde = $R.Cells[$Fila, 2, $Fila, (2 + $Valores.Count)].Style.Border.Bottom
        $borde.Style = [OfficeOpenXml.Style.ExcelBorderStyle]::Thin
        $borde.Color.SetColor([System.Drawing.Color]::FromArgb(191, 191, 191))
    }
    function Contar {
        param([string]$Tipo, [string]$Estado)
        return ('=COUNTIFS(''{0}''!$A:$A,"{1}",''{0}''!$J:$J,"{2}")' -f $HOJA_SITIOS, $Tipo, $Estado)
    }

    $hoy = Get-Date
    Celda 'B2' "Contenido compartido con externos — $NombreTenant" -Negrita -Tam 14 -Color $AZUL
    Celda 'B3' 'SharePoint y OneDrive, sin Teams ni sitios técnicos. Informe de solo lectura: no se ha modificado nada.' -Tam 9 -Color $GRIS
    Celda 'B5' 'Fecha del informe' -Negrita; Celda 'C5' $hoy -Formato 'dd/mm/yyyy'
    Celda 'B6' 'Periodo del registro de auditoría' -Negrita
    Celda 'C6' ('{0:dd/MM/yyyy} – {1:dd/MM/yyyy}' -f $hoy.AddDays(-$Dias), $hoy)
    if (-not $auditoriaOk) { Celda 'E6' 'No se pudo leer: ver log' -Negrita -Color ([System.Drawing.Color]::FromArgb(192, 0, 0)) }

    $nSP = @($incluidos | Where-Object { $_.Tipo -eq 'SharePoint' }).Count
    $nOD = @($incluidos | Where-Object { $_.Tipo -eq 'OneDrive' }).Count
    Cabecera 8 @('Sitios revisados', 'SharePoint', 'OneDrive', 'Total')
    Linea 9 'Revisados' @($nSP, $nOD, '=C9+D9') -Negrita
    Linea 10 "Con enlaces públicos ($Dias días)" @((Contar 'SharePoint' $EST_PUB), (Contar 'OneDrive' $EST_PUB), '=C10+D10')
    Linea 11 "Han compartido con externos ($Dias días)" @((Contar 'SharePoint' $EST_FUERA), (Contar 'OneDrive' $EST_FUERA), '=C11+D11')
    Linea 12 'Solo tienen invitados de antes' @((Contar 'SharePoint' $EST_ANT), (Contar 'OneDrive' $EST_ANT), '=C12+D12')
    Linea 13 'Sin nada compartido fuera' @('=C9-C10-C11-C12', '=D9-D10-D11-D12', '=C13+D13')
    Linea 14 'Bloqueados: no se pudieron ver sus invitados (normalmente usuarios de baja)' @(@($incluidos | Where-Object { $_.Tipo -eq 'SharePoint' -and $_.Revision -eq 'Bloqueado' }).Count, @($incluidos | Where-Object { $_.Tipo -eq 'OneDrive' -and $_.Revision -eq 'Bloqueado' }).Count, '=C14+D14')
    Linea 15 'Sitios de Teams excluidos (no están en el informe)' @(@($sitios | Where-Object { $_.Excluido -eq 'Teams' }).Count)

    Cabecera 17 @("Qué ha pasado en $Dias días", 'Nº')
    $fila = 18
    foreach ($acc in @('Invitación a externo', 'Externo aceptó invitación', 'Permiso dado a externo', 'Externo añadido a un enlace', 'Enlace público creado', 'Enlace público modificado', 'Enlace público usado')) {
        Linea $fila $acc @(('=COUNTIF(''{0}''!$B:$B,"{1}")' -f $HOJA_DETALLE, $acc))
        $fila++
    }
    Linea $fila 'Personas externas distintas' @($externos.Count) -Negrita; $fila++
    Linea $fila 'Eventos en sitios de Teams o no incluidos (no se muestran)' @($eventosTeams + $eventosOtros); $fila++

    $fila++
    Cabecera $fila @('Top 10 sitios / OneDrive que más comparten fuera', 'Tipo', 'Veces', 'Externos'); $fila++
    $top = @($filasSitios | Where-Object { $_."Veces compartido fuera ($Dias d)" -gt 0 } | Select-Object -First 10)
    if ($top.Count -eq 0) { Celda "B$fila" 'Sin comparticiones con externos en el periodo.' -Tam 9 -Color $GRIS; $fila++ }
    foreach ($t in $top) {
        Linea $fila $t.'Sitio / OneDrive' @($t.Tipo, $t."Veces compartido fuera ($Dias d)", $t."Personas externas ($Dias d)")
        $R.Cells[$fila, 3].Style.HorizontalAlignment = [OfficeOpenXml.Style.ExcelHorizontalAlignment]::Center
        $fila++
    }

    $fila++
    Cabecera $fila @('Top 10 empresas / dominios externos', 'Personas', 'Veces'); $fila++
    $topDom = @($externos | Where-Object { $_.'Dominio / empresa' } | Group-Object 'Dominio / empresa' | ForEach-Object {
        [pscustomobject]@{ Dominio = $_.Name; Personas = $_.Count; Veces = ($_.Group | Measure-Object Veces -Sum).Sum }
    } | Sort-Object Veces -Descending | Select-Object -First 10)
    if ($topDom.Count -eq 0) { Celda "B$fila" 'Sin datos en el periodo.' -Tam 9 -Color $GRIS; $fila++ }
    foreach ($d in $topDom) { Linea $fila $d.Dominio @($d.Personas, $d.Veces); $fila++ }

    $fila++
    Celda "B$fila" 'Cómo leer este Excel' -Negrita -Tam 11 -Color $AZUL; $fila++
    $notas = @(
        "• '$HOJA_SITIOS': cada SharePoint u OneDrive que ha compartido algo fuera. Rojo = enlaces públicos; amarillo = compartido con externos en los últimos $Dias días; azul = solo tiene invitados registrados de antes.",
        "• '$HOJA_DETALLE': cada vez que alguien compartió con un externo: quién, con quién, qué archivo o carpeta y cuándo (registro de auditoría de Microsoft 365).",
        "• '$HOJA_EXTERNOS': cada persona externa que ha recibido algo y en cuántos sitios u OneDrive.",
        "• '$HOJA_INVITADOS': invitados externos dados de alta en cada sitio, aunque se invitaran hace más de $Dias días. Puede no estar completa con invitados de Entra ID.",
        "• 'Bloqueados': sitios u OneDrive cerrados por Microsoft 365, normalmente de usuarios dados de baja. No se pueden ver sus invitados, pero lo que compartieron en los últimos $Dias días sí aparece en el detalle.",
        "• El registro de auditoría estándar de Microsoft 365 solo guarda $Dias días como máximo: lo compartido antes solo aparece si dejó invitados registrados.",
        '• Documento confidencial: contiene nombres de archivos y de personas, incluidos OneDrive personales.'
    )
    foreach ($t in $notas) {
        $x = $R.Cells[$fila, 2]
        $x.Value = $t; $x.Style.Font.Size = 9; $x.Style.WrapText = $true
        $x.Style.VerticalAlignment = [OfficeOpenXml.Style.ExcelVerticalAlignment]::Top
        $R.Cells[$fila, 2, $fila, 6].Merge = $true
        $R.Row($fila).Height = 30
        $fila++
    }

    $pkg.Workbook.Worksheets['Resumen'].View.TabSelected = $true
    foreach ($w in $pkg.Workbook.Worksheets) { if ($w.Name -ne 'Resumen') { $w.View.TabSelected = $false } }
    $pkg.Workbook.View.ActiveTab = 0
    try { $pkg.Workbook.Calculate() } catch { }
    Close-ExcelPackage $pkg
    Write-Log "Excel generado: $ArchivoExcel"
}
catch {
    Write-Log "Error generando el Excel: $($_.Exception.Message)" 'ERROR'
    Copy-Item $script:ArchivoLog $ArchivoLogFinal -Force -ErrorAction SilentlyContinue
    exit 1
}

Write-Host ''
Write-Host '=== Resumen ===' -ForegroundColor Cyan
Write-Host ("SharePoint y OneDrive revisados:          {0}" -f $incluidos.Count)
Write-Host ("Con algo compartido fuera:                {0}" -f $filasSitios.Count)
Write-Host ("Comparticiones con externos ({0} dias):   {1}" -f $Dias, $detalle.Count)
Write-Host ("Personas externas distintas ({0} dias):   {1}" -f $Dias, $externos.Count)
Write-Host ''
Write-Log 'Fin del proceso.'
Copy-Item $script:ArchivoLog $ArchivoLogFinal -Force -ErrorAction SilentlyContinue
Remove-Item $CarpetaTrabajo -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "Excel: $ArchivoExcel" -ForegroundColor Green
