#Requires -Version 5.1
<#
.SYNOPSIS
    Alta masiva de cuentas en un tenant de Microsoft 365 a partir de un CSV.

.DESCRIPTION
    Por cada fila del CSV:
      - Si la cuenta ya existe, no la toca (solo le asigna la licencia si le falta).
      - Si no existe, la crea con la contrasena del CSV o una generada (16 caracteres).
      - Fija la ubicacion de uso (obligatoria para licenciar) y asigna la licencia.
      - Opcionalmente espera a que exista el buzon y le anade el alias @<tenant>.onmicrosoft.com.
    Antes de cambiar nada comprueba el tenant, los permisos y que haya licencias libres suficientes.
    Todo queda en un CSV de resultados (incluidas las contrasenas generadas).

.PARAMETER Csv
    Ruta del CSV. Columnas: UPN (obligatoria), Nombre, Contrasena, Licencia, Departamento, Puesto.
    Separador , o ; (se detecta solo). Ver plantilla_cuentas.csv.

.PARAMETER TenantId
    Id del tenant esperado. Si la sesion es de otro tenant, el script se detiene sin tocar nada.

.PARAMETER Licencia
    Licencia por defecto (SkuPartNumber, p. ej. O365_BUSINESS_PREMIUM, o el SkuId). La columna Licencia del CSV
    manda sobre esta. Sin licencia: las cuentas se crean sin licenciar.

.PARAMETER UbicacionUso
    Codigo de pais de dos letras (por defecto ES).

.PARAMETER CambiarContrasenaAlEntrar
    Obliga a cambiar la contrasena en el primer inicio de sesion (por defecto no).

.PARAMETER AliasOnMicrosoft
    Espera a que se cree el buzon y le anade el alias @<tenant>.onmicrosoft.com (necesita Exchange Online).

.PARAMETER Simular
    Recorre todo y dice lo que haria, sin crear ni cambiar nada.

.PARAMETER SinConfirmar
    No pregunta antes de aplicar los cambios.

.EXAMPLE
    .\Crear-CuentasM365.ps1 -Csv .\cuentas.csv -TenantId 00000000-0000-0000-0000-000000000000 -Licencia O365_BUSINESS_PREMIUM -Simular

.NOTES
    Masoft.es - Herramientas universales M365. Requiere los modulos Microsoft.Graph.Authentication,
    Microsoft.Graph.Users e Identity.DirectoryManagement; ExchangeOnlineManagement solo con -AliasOnMicrosoft.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Csv,
    [string]$TenantId,
    [string]$Licencia,
    [ValidatePattern('^[A-Za-z]{2}$')][string]$UbicacionUso = "ES",
    [switch]$CambiarContrasenaAlEntrar,
    [switch]$AliasOnMicrosoft,
    [ValidateRange(1, 60)][int]$MinutosEsperaBuzon = 20,
    [switch]$Simular,
    [switch]$SinConfirmar,
    [string]$CarpetaResultados
)

$ErrorActionPreference = "Stop"
$CodificacionCsv = if ($PSVersionTable.PSVersion.Major -ge 6) { "utf8BOM" } else { "UTF8" }   # CSV con BOM en 5.1 y en 7: Excel abre bien las tildes
$Scopes = @("User.ReadWrite.All", "Directory.ReadWrite.All", "Organization.Read.All")

# ============================================================ utilidades comunes
function Write-Paso  ($t) { Write-Host "`n== $t" -ForegroundColor Cyan }
function Write-Ok    ($t) { Write-Host "  [OK]    $t" -ForegroundColor Green }
function Write-Aviso ($t) { Write-Host "  [AVISO] $t" -ForegroundColor Yellow }
function Write-Fallo ($t) { Write-Host "  [ERROR] $t" -ForegroundColor Red }
function Write-Sim   ($t) { Write-Host "  [SIM]   $t" -ForegroundColor Magenta }

function Import-CsvFlexible {
    <# Importa un CSV con , o ; (Excel en espanol guarda con ;), en UTF-8 o ANSI (Excel «CSV delimitado» guarda en
       ANSI y las tildes saldrian mal), y comprueba las columnas obligatorias. #>
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
    $columnas = $filas[0].PSObject.Properties.Name | ForEach-Object { $_.Trim().TrimStart([char]0xFEFF) }
    foreach ($c in $Obligatorias) {
        if ($columnas -notcontains $c) { throw "Falta la columna '$c' en el CSV (columnas: $($columnas -join ', '))" }
    }
    return $filas
}

function Get-Valor {
    <# Valor de una columna (vacio si no existe), sin espacios. #>
    param($Fila, [string]$Columna)
    $p = $Fila.PSObject.Properties | Where-Object { $_.Name.Trim().TrimStart([char]0xFEFF) -eq $Columna } | Select-Object -First 1
    if ($p -and $null -ne $p.Value) { return ([string]$p.Value).Trim() }
    return ""
}

function New-ContrasenaSegura {
    <# 16 caracteres con minuscula, mayuscula, digito y simbolo. RNG criptografico, valido en PowerShell 5.1 y 7
       (RandomNumberGenerator.GetInt32 no existe en .NET Framework). Sin caracteres confundibles ni que rompan un CSV. #>
    param([int]$Longitud = 16)
    $grupos = @("abcdefghijkmnopqrstuvwxyz", "ABCDEFGHJKLMNPQRSTUVWXYZ", "23456789", "!#%*+-=?@")
    $todos = -join $grupos
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    function Get-Aleatorio([int]$Max) {
        $b = New-Object byte[] 4
        do { $rng.GetBytes($b); $n = [BitConverter]::ToUInt32($b, 0) } while ($n -ge ([uint32]::MaxValue - ([uint32]::MaxValue % $Max)))
        return [int]($n % $Max)
    }
    $c = New-Object System.Collections.Generic.List[char]
    foreach ($g in $grupos) { $c.Add($g[(Get-Aleatorio $g.Length)]) }
    while ($c.Count -lt $Longitud) { $c.Add($todos[(Get-Aleatorio $todos.Length)]) }
    for ($i = $c.Count - 1; $i -gt 0; $i--) { $j = Get-Aleatorio ($i + 1); $t = $c[$i]; $c[$i] = $c[$j]; $c[$j] = $t }
    $rng.Dispose()
    return -join $c
}

function Connect-GraphSeguro {
    <# Reutiliza la sesion si es del tenant y tiene los permisos; si no, conecta. Se detiene si el tenant no es el esperado. #>
    param([string[]]$Scopes, [string]$TenantId)
    if (-not (Get-Module -ListAvailable -Name Microsoft.Graph.Authentication)) {
        throw "Falta el modulo Microsoft.Graph. Instalalo con: Install-Module Microsoft.Graph -Scope CurrentUser"
    }
    $ctx = Get-MgContext
    $faltan = if ($ctx) { @($Scopes | Where-Object { $ctx.Scopes -notcontains $_ }) } else { $Scopes }
    if (-not $ctx -or $faltan.Count -gt 0 -or ($TenantId -and $ctx.TenantId -ne $TenantId)) {
        $p = @{ Scopes = $Scopes; NoWelcome = $true }
        if ($TenantId) { $p.TenantId = $TenantId }
        Connect-MgGraph @p | Out-Null
        $ctx = Get-MgContext
    }
    if (-not $ctx) { throw "No se pudo iniciar sesion en Microsoft Graph." }
    if ($TenantId -and $ctx.TenantId -ne $TenantId) {
        throw "Tenant incorrecto: la sesion es de $($ctx.TenantId) y se esperaba $TenantId. No se ha tocado nada."
    }
    $faltan = @($Scopes | Where-Object { $ctx.Scopes -notcontains $_ })
    if ($faltan.Count -gt 0) { throw "La sesion no tiene estos permisos: $($faltan -join ', ')" }
    return $ctx
}

function Get-UsuarioGraph {
    <# Usuario por UPN o $null. NO usar $u[0]: MicrosoftGraphUser tiene indexador propio y devuelve $null sin error. #>
    param([string]$Upn)
    $f = "userPrincipalName eq '$($Upn.Replace("'", "''"))'"
    $u = Get-MgUser -Filter $f -Property Id, UserPrincipalName, DisplayName, UsageLocation, AssignedLicenses -ErrorAction Stop
    if ($u) { return ($u | Select-Object -First 1) }
    return $null
}

# ============================================================ preparacion
$inicio = Get-Date
if (-not $CarpetaResultados) { $CarpetaResultados = Join-Path $PSScriptRoot "Resultados" }
New-Item -ItemType Directory -Force -Path $CarpetaResultados | Out-Null
$Registro = Join-Path $CarpetaResultados ("CrearCuentas_{0:yyyyMMdd_HHmmss}.csv" -f $inicio)
$resultados = New-Object System.Collections.Generic.List[object]
function Add-Resultado ($Upn, $Accion, $Estado, $Detalle = "", $Contrasena = "") {
    $resultados.Add([PSCustomObject]@{ Fecha = (Get-Date -Format "dd/MM/yyyy HH:mm:ss"); UPN = $Upn; Accion = $Accion
            Estado = $Estado; Contrasena = $Contrasena; Detalle = $Detalle })
}

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " ALTA DE CUENTAS EN MICROSOFT 365" -ForegroundColor Cyan
if ($Simular) { Write-Host " MODO SIMULACION: no se cambia nada" -ForegroundColor Magenta }
Write-Host "==============================================" -ForegroundColor Cyan

try {
    Write-Paso "Comprobaciones"
    $filas = Import-CsvFlexible -Ruta $Csv -Obligatorias @("UPN")
    $vistos = @{}
    $cuentas = foreach ($f in $filas) {
        $upn = (Get-Valor $f "UPN").ToLower()
        if (-not $upn) { continue }
        if ($upn -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') { Write-Aviso "UPN no valido, se omite: $upn"; continue }
        if ($vistos.ContainsKey($upn)) { Write-Aviso "UPN repetido en el CSV, se usa la primera fila: $upn"; continue }
        $vistos[$upn] = $true
        [PSCustomObject]@{
            UPN = $upn; Nombre = (Get-Valor $f "Nombre"); Contrasena = (Get-Valor $f "Contrasena")
            Licencia = (Get-Valor $f "Licencia"); Departamento = (Get-Valor $f "Departamento"); Puesto = (Get-Valor $f "Puesto")
        }
    }
    $cuentas = @($cuentas)
    if ($cuentas.Count -eq 0) { throw "No hay ninguna cuenta valida en el CSV." }
    Write-Ok "$($cuentas.Count) cuentas en el CSV"

    $ctx = Connect-GraphSeguro -Scopes $Scopes -TenantId $TenantId
    $org = Get-MgOrganization | Select-Object -First 1
    Write-Ok "Tenant: $($org.DisplayName) ($($ctx.TenantId)) - cuenta $($ctx.Account)"
    if (-not $TenantId) { Write-Aviso "No se ha indicado -TenantId: comprueba que el tenant de arriba es el correcto." }

    # Licencias: se resuelven por SkuPartNumber o SkuId y se comprueba que haya libres para todo el lote
    $skus = @(Get-MgSubscribedSku -All)
    $porNombre = @{}
    foreach ($s in $skus) { $porNombre[$s.SkuPartNumber.ToUpper()] = $s; $porNombre[([string]$s.SkuId).ToUpper()] = $s }
    $necesarias = @{}
    foreach ($c in $cuentas) {
        if (-not $c.Licencia) { $c.Licencia = $Licencia }
        if ($c.Licencia) {
            $s = $porNombre[$c.Licencia.ToUpper()]
            if (-not $s) {
                $disp = ($skus | ForEach-Object { "$($_.SkuPartNumber) (libres: $($_.PrepaidUnits.Enabled - $_.ConsumedUnits))" }) -join "`n    "
                throw "La licencia '$($c.Licencia)' no existe en este tenant. Disponibles:`n    $disp"
            }
            $c | Add-Member -NotePropertyName Sku -NotePropertyValue $s
            $necesarias[$s.SkuPartNumber] = 1 + [int]$necesarias[$s.SkuPartNumber]
        }
    }
    foreach ($nombre in $necesarias.Keys) {
        $s = $porNombre[$nombre.ToUpper()]
        $libres = $s.PrepaidUnits.Enabled - $s.ConsumedUnits
        if ($libres -lt $necesarias[$nombre]) {
            Write-Aviso "$nombre : libres $libres, el CSV pide hasta $($necesarias[$nombre]) (las cuentas que ya la tengan no gastan)."
        } else { Write-Ok "$nombre : libres $libres, necesarias como mucho $($necesarias[$nombre])" }
    }

    if ($AliasOnMicrosoft) {
        $dominioOnMs = ($org.VerifiedDomains | Where-Object { $_.Name -like "*.onmicrosoft.com" -and $_.Name -notlike "*.mail.onmicrosoft.com" } | Select-Object -First 1).Name
        if (-not $dominioOnMs) { throw "No se encuentra el dominio .onmicrosoft.com del tenant." }
        if (-not (Get-Command Connect-ExchangeOnline -ErrorAction SilentlyContinue)) {
            throw "Falta el modulo ExchangeOnlineManagement (necesario para -AliasOnMicrosoft)."
        }
        $conEXO = $false
        try { $conEXO = [bool](Get-ConnectionInformation | Where-Object { $_.State -eq "Connected" }) } catch { }
        if (-not $conEXO) { Connect-ExchangeOnline -ShowBanner:$false }
        Write-Ok "Exchange Online conectado; alias en @$dominioOnMs"
    }

    if (-not $Simular -and -not $SinConfirmar) {
        $r = Read-Host "`nSe van a crear o licenciar hasta $($cuentas.Count) cuentas en '$($org.DisplayName)'. ¿Continuar? (S/N)"
        if ($r -notmatch '^(s|si|sí)$') { Write-Aviso "Cancelado: no se ha cambiado nada."; return }
    }

    # ============================================================ alta y licencias
    Write-Paso "Cuentas"
    foreach ($c in $cuentas) {
        $txt = "{0,-45}" -f $c.UPN
        try {
            $u = Get-UsuarioGraph $c.UPN
            if ($u) {
                Write-Aviso "$txt ya existe: no se cambia (solo la licencia si le falta)"
                Add-Resultado $c.UPN "CREAR" "YA EXISTIA"
            } elseif ($Simular) {
                Write-Sim "$txt se crearia"
                Add-Resultado $c.UPN "CREAR" "SIMULADO"
            } else {
                $pwd = if ($c.Contrasena) { $c.Contrasena } else { New-ContrasenaSegura }
                $nombre = if ($c.Nombre) { $c.Nombre } else { $c.UPN.Split("@")[0] }
                $nick = ($c.UPN.Split("@")[0] -replace "[^A-Za-z0-9._-]", "")
                $body = @{
                    accountEnabled = $true; displayName = $nombre; mailNickname = $nick; userPrincipalName = $c.UPN
                    usageLocation = $UbicacionUso.ToUpper()
                    passwordProfile = @{ password = $pwd; forceChangePasswordNextSignIn = [bool]$CambiarContrasenaAlEntrar }
                }
                if ($c.Departamento) { $body.department = $c.Departamento }
                if ($c.Puesto) { $body.jobTitle = $c.Puesto }
                # SDK 2.x: New-MgUser con -BodyParameter (los parametros sueltos fallan con passwordProfile)
                $u = New-MgUser -BodyParameter $body
                Write-Ok "$txt creada"
                Add-Resultado $c.UPN "CREAR" "OK" "Id $($u.Id)" $pwd
            }

            if (-not $c.Sku) { continue }
            if ($u -and ($u.AssignedLicenses.SkuId -contains $c.Sku.SkuId)) { Add-Resultado $c.UPN "LICENCIA" "YA TENIA" $c.Sku.SkuPartNumber; continue }
            if ($Simular) { Write-Sim "$txt recibiria $($c.Sku.SkuPartNumber)"; continue }
            if ($u -and -not $u.UsageLocation) { Update-MgUser -UserId $u.Id -UsageLocation $UbicacionUso.ToUpper() }
            $cuerpo = @{ addLicenses = @(@{ skuId = $c.Sku.SkuId; disabledPlans = @() }); removeLicenses = @() } | ConvertTo-Json -Depth 5
            $hecho = $false
            for ($i = 1; $i -le 5 -and -not $hecho; $i++) {
                try {
                    # Recien creada, la cuenta tarda unos segundos en replicar: se reintenta
                    Invoke-MgGraphRequest -Method POST -Uri "/v1.0/users/$($u.Id)/assignLicense" -Body $cuerpo -ContentType "application/json" | Out-Null
                    $hecho = $true
                } catch {
                    if ($i -eq 5 -or $_.Exception.Message -match "403|Forbidden") { throw }
                    Start-Sleep -Seconds (5 * $i)
                }
            }
            Write-Ok "$txt licencia $($c.Sku.SkuPartNumber)"
            Add-Resultado $c.UPN "LICENCIA" "OK" $c.Sku.SkuPartNumber
        } catch {
            Write-Fallo "$txt $($_.Exception.Message)"
            Add-Resultado $c.UPN "CREAR/LICENCIA" "ERROR" $_.Exception.Message
        }
    }

    # ============================================================ alias onmicrosoft
    if ($AliasOnMicrosoft) {
        Write-Paso "Alias @$dominioOnMs (esperando a que existan los buzones, hasta $MinutosEsperaBuzon min)"
        $pendientes = [System.Collections.Generic.List[object]]::new()
        foreach ($c in $cuentas) { if ($resultados | Where-Object { $_.UPN -eq $c.UPN -and $_.Estado -ne "ERROR" }) { $pendientes.Add($c) } }
        $limite = (Get-Date).AddMinutes($MinutosEsperaBuzon)
        while ($pendientes.Count -gt 0) {
            foreach ($c in @($pendientes)) {
                $alias = "$($c.UPN.Split('@')[0])@$dominioOnMs"
                $mbx = Get-EXOMailbox -Identity $c.UPN -Properties EmailAddresses -ErrorAction SilentlyContinue
                if (-not $mbx) { continue }
                $pendientes.Remove($c) | Out-Null
                if ($mbx.EmailAddresses -contains "smtp:$alias") { Add-Resultado $c.UPN "ALIAS" "YA TENIA" $alias; continue }
                if ($Simular) { Write-Sim "$($c.UPN) recibiria el alias $alias"; continue }
                try {
                    Set-Mailbox -Identity $c.UPN -EmailAddresses @{ Add = "smtp:$alias" }
                    Write-Ok "$($c.UPN) alias $alias"
                    Add-Resultado $c.UPN "ALIAS" "OK" $alias
                } catch { Write-Fallo "$($c.UPN) alias: $($_.Exception.Message)"; Add-Resultado $c.UPN "ALIAS" "ERROR" $_.Exception.Message }
            }
            if ($pendientes.Count -eq 0) { break }
            if ((Get-Date) -gt $limite -or $Simular) {
                foreach ($c in $pendientes) { Write-Aviso "$($c.UPN): sin buzon todavia (no se pone el alias)"; Add-Resultado $c.UPN "ALIAS" "SIN BUZON" }
                break
            }
            Write-Host "  ... $($pendientes.Count) buzones aun sin crear; nueva comprobacion en 60 s" -ForegroundColor DarkGray
            Start-Sleep -Seconds 60
        }
    }
}
catch {
    Write-Fallo $_.Exception.Message
    Add-Resultado "" "GENERAL" "ERROR" $_.Exception.Message
}
finally {
    if ($resultados.Count -gt 0) { $resultados | Export-Csv -LiteralPath $Registro -NoTypeInformation -Encoding $CodificacionCsv -Delimiter ";" }
    $err = @($resultados | Where-Object Estado -eq "ERROR").Count
    Write-Paso "Resumen"
    Write-Host "  Creadas: $(@($resultados | Where-Object { $_.Accion -eq 'CREAR' -and $_.Estado -eq 'OK' }).Count)   Ya existian: $(@($resultados | Where-Object Estado -eq 'YA EXISTIA').Count)   Licencias: $(@($resultados | Where-Object { $_.Accion -eq 'LICENCIA' -and $_.Estado -eq 'OK' }).Count)   Errores: $err"
    if ($resultados.Count -gt 0) {
        Write-Host "  Resultados: $Registro"
        if (@($resultados | Where-Object Contrasena).Count -gt 0) { Write-Aviso "El CSV de resultados contiene contrasenas: guardalo en sitio seguro y borralo cuando ya no haga falta." }
    }
}
