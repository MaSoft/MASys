#Requires -Version 5.1
<#
.SYNOPSIS
    Invita usuarios externos (invitados B2B) a un tenant de Microsoft 365 desde un CSV, opcionalmente a un grupo,
    y puede convertirlos despues de Invitado a Miembro.

.DESCRIPTION
    Por cada correo del CSV:
      - Si ya existe una cuenta con ese correo en el tenant, no la invita de nuevo.
      - Si no, envia la invitacion (con o sin correo, con mensaje propio y pagina de destino).
      - Opcional: lo anade a un grupo (de Microsoft 365 o de seguridad) para darle acceso a un sitio, Teams, etc.
      - Con -ConvertirEnMiembro (en una segunda pasada, cuando ya han aceptado) cambia su tipo a Miembro.
    Los invitados aparecen en el tenant aunque no hayan aceptado todavia, asi que ya se les puede dar acceso.

.EXAMPLE
    .\Invitar-InvitadosB2B.ps1 -Csv .\invitados.csv -TenantId 00000000-0000-0000-0000-000000000000 -Grupo "Proyecto externo" -Simular
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Csv,
    [string]$TenantId,
    [string]$Grupo,
    [string]$Mensaje,
    [switch]$SinCorreo,
    [string]$UrlRedireccion = "https://myapplications.microsoft.com",
    [switch]$ConvertirEnMiembro,
    [switch]$Simular,
    [switch]$SinConfirmar,
    [string]$CarpetaResultados
)

$ErrorActionPreference = "Stop"
$CodificacionCsv = if ($PSVersionTable.PSVersion.Major -ge 6) { "utf8BOM" } else { "UTF8" }   # CSV con BOM en 5.1 y en 7: Excel abre bien las tildes
$Scopes = @("User.Invite.All", "User.Read.All")
if ($ConvertirEnMiembro) { $Scopes += "User.ReadWrite.All" }
# Los permisos de grupos se anaden al leer el CSV (el grupo puede venir tambien por fila)

# ============================================================ utilidades comunes
function Write-Paso  ($t) { Write-Host "`n== $t" -ForegroundColor Cyan }
function Write-Ok    ($t) { Write-Host "  [OK]    $t" -ForegroundColor Green }
function Write-Aviso ($t) { Write-Host "  [AVISO] $t" -ForegroundColor Yellow }
function Write-Fallo ($t) { Write-Host "  [ERROR] $t" -ForegroundColor Red }
function Write-Sim   ($t) { Write-Host "  [SIM]   $t" -ForegroundColor Magenta }

function Import-CsvFlexible {
    <# CSV con , o ; en UTF-8 o ANSI (Excel); comprueba las columnas obligatorias. #>
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
    $columnas = $filas[0].PSObject.Properties.Name | ForEach-Object { $_.Trim() }
    foreach ($c in $Obligatorias) {
        if ($columnas -notcontains $c) { throw "Falta la columna '$c' en el CSV (columnas: $($columnas -join ', '))" }
    }
    return $filas
}

function Get-Valor {
    param($Fila, [string]$Columna)
    $p = $Fila.PSObject.Properties | Where-Object { $_.Name.Trim() -eq $Columna } | Select-Object -First 1
    if ($p -and $null -ne $p.Value) { return ([string]$p.Value).Trim() }
    return ""
}

function Connect-GraphSeguro {
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

function Get-GrupoGraph {
    <# Grupo por Id o por nombre exacto; error si no existe o si hay varios con ese nombre. #>
    param([string]$Nombre)
    if ($Nombre -match '^[0-9a-fA-F-]{36}$') { return Invoke-MgGraphRequest -Method GET -Uri "/v1.0/groups/$Nombre`?`$select=id,displayName" }
    $r = Invoke-MgGraphRequest -Method GET -Uri ("/v1.0/groups?`$select=id,displayName&`$filter=displayName eq '{0}'" -f [uri]::EscapeDataString($Nombre.Replace("'", "''")))
    $g = @($r.value)
    if ($g.Count -eq 0) { throw "No existe ningun grupo llamado '$Nombre'." }
    if ($g.Count -gt 1) { throw "Hay $($g.Count) grupos llamados '$Nombre': indica su Id." }
    return $g[0]
}

# ============================================================ inicio
$inicio = Get-Date
if (-not $CarpetaResultados) { $CarpetaResultados = Join-Path $PSScriptRoot "Resultados" }
New-Item -ItemType Directory -Force -Path $CarpetaResultados | Out-Null
$Registro = Join-Path $CarpetaResultados ("InvitadosB2B_{0:yyyyMMdd_HHmmss}.csv" -f $inicio)
$resultados = New-Object System.Collections.Generic.List[object]
function Add-Resultado ($Correo, $Accion, $Estado, $Detalle = "") {
    $resultados.Add([PSCustomObject]@{ Fecha = (Get-Date -Format "dd/MM/yyyy HH:mm:ss"); Correo = $Correo; Accion = $Accion; Estado = $Estado; Detalle = $Detalle })
}

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " INVITADOS B2B EN MICROSOFT 365" -ForegroundColor Cyan
if ($Simular) { Write-Host " MODO SIMULACION: no se cambia nada" -ForegroundColor Magenta }
Write-Host "==============================================" -ForegroundColor Cyan

try {
    Write-Paso "Comprobaciones"
    $vistos = @{}
    $invitados = @(foreach ($f in (Import-CsvFlexible -Ruta $Csv -Obligatorias @("Correo"))) {
            $correo = (Get-Valor $f "Correo").ToLower()
            if ($correo -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') { if ($correo) { Write-Aviso "Correo no valido, se omite: $correo" }; continue }
            if ($vistos.ContainsKey($correo)) { continue }
            $vistos[$correo] = $true
            $g = Get-Valor $f "Grupo"; if (-not $g) { $g = $Grupo }
            [PSCustomObject]@{ Correo = $correo; Nombre = (Get-Valor $f "Nombre"); Grupo = $g }
        })
    if ($invitados.Count -eq 0) { throw "No hay ningun correo valido en el CSV." }
    Write-Ok "$($invitados.Count) invitados en el CSV"
    if (@($invitados | Where-Object Grupo).Count) { $Scopes += "GroupMember.ReadWrite.All", "Group.Read.All" }

    $ctx = Connect-GraphSeguro -Scopes $Scopes -TenantId $TenantId
    Write-Ok "Tenant $($ctx.TenantId) - cuenta $($ctx.Account)"
    $grupos = @{}
    foreach ($n in ($invitados | Where-Object Grupo | Select-Object -ExpandProperty Grupo -Unique)) {
        $grupos[$n] = Get-GrupoGraph $n
        Write-Ok "Grupo: $($grupos[$n].displayName)"
    }

    if (-not $Simular -and -not $SinConfirmar) {
        $que = if ($ConvertirEnMiembro) { "convertir en Miembro" } else { "invitar" }
        $r = Read-Host "`nSe va a $que a $($invitados.Count) personas. ¿Continuar? (S/N)"
        if ($r -notmatch '^(s|si|sí)$') { Write-Aviso "Cancelado: no se ha cambiado nada."; return }
    }

    Write-Paso $(if ($ConvertirEnMiembro) { "Convertir en Miembro" } else { "Invitaciones" })
    foreach ($i in $invitados) {
        $txt = "{0,-40}" -f $i.Correo
        try {
            $q = [uri]::EscapeDataString($i.Correo.Replace("'", "''"))
            $existe = @((Invoke-MgGraphRequest -Method GET -Uri "/v1.0/users?`$select=id,userType,externalUserState,displayName&`$filter=mail eq '$q'").value) | Select-Object -First 1
            if ($ConvertirEnMiembro) {
                if (-not $existe) { Write-Fallo "$txt no esta en el tenant (invitalo primero)"; Add-Resultado $i.Correo "CONVERTIR" "ERROR" "No existe"; continue }
                if ($existe.userType -eq "Member") { Write-Aviso "$txt ya es Miembro"; Add-Resultado $i.Correo "CONVERTIR" "YA ERA MIEMBRO"; continue }
                if ($existe.externalUserState -eq "PendingAcceptance") { Write-Aviso "$txt aun no ha aceptado la invitacion: se convierte igualmente" }
                if ($Simular) { Write-Sim "$txt pasaria a Miembro"; Add-Resultado $i.Correo "CONVERTIR" "SIMULADO"; continue }
                Invoke-MgGraphRequest -Method PATCH -Uri "/v1.0/users/$($existe.id)" -Body (@{ userType = "Member" } | ConvertTo-Json) -ContentType "application/json" | Out-Null
                Write-Ok "$txt ahora es Miembro"; Add-Resultado $i.Correo "CONVERTIR" "OK"
                continue
            }
            $id = $null
            if ($existe) {
                Write-Aviso "$txt ya estaba en el tenant ($($existe.userType)): no se reinvita"
                Add-Resultado $i.Correo "INVITAR" "YA EXISTIA" $existe.userType
                $id = $existe.id
            } elseif ($Simular) {
                Write-Sim "$txt se invitaria$(if ($SinCorreo) { ' (sin correo)' })"
                Add-Resultado $i.Correo "INVITAR" "SIMULADO"
            } else {
                $cuerpo = @{ invitedUserEmailAddress = $i.Correo; inviteRedirectUrl = $UrlRedireccion; sendInvitationMessage = (-not $SinCorreo) }
                if ($i.Nombre) { $cuerpo.invitedUserDisplayName = $i.Nombre }
                if ($Mensaje) { $cuerpo.invitedUserMessageInfo = @{ customizedMessageBody = $Mensaje } }
                $inv = Invoke-MgGraphRequest -Method POST -Uri "/v1.0/invitations" -Body ($cuerpo | ConvertTo-Json -Depth 4) -ContentType "application/json"
                $id = $inv.invitedUser.id
                Write-Ok "$txt invitado"
                Add-Resultado $i.Correo "INVITAR" "OK" $(if ($SinCorreo) { "Sin correo. Enlace: $($inv.inviteRedeemUrl)" } else { "Correo enviado" })
            }
            if ($i.Grupo) {
                $g = $grupos[$i.Grupo]
                if ($Simular -or -not $id) { Write-Sim "$txt se anadiria al grupo $($g.displayName)"; continue }
                try {
                    $ref = @{ "@odata.id" = "https://graph.microsoft.com/v1.0/directoryObjects/$id" } | ConvertTo-Json
                    Invoke-MgGraphRequest -Method POST -Uri "/v1.0/groups/$($g.id)/members/`$ref" -Body $ref -ContentType "application/json" | Out-Null
                    Write-Ok "$txt anadido a $($g.displayName)"; Add-Resultado $i.Correo "GRUPO" "OK" $g.displayName
                } catch {
                    if ($_.Exception.Message -match "already exist") { Add-Resultado $i.Correo "GRUPO" "YA ESTABA" $g.displayName }
                    else { throw }
                }
            }
        } catch {
            Write-Fallo "$txt $($_.Exception.Message)"
            Add-Resultado $i.Correo "INVITAR/GRUPO" "ERROR" $_.Exception.Message
        }
    }
}
catch {
    Write-Fallo $_.Exception.Message
    Add-Resultado "" "GENERAL" "ERROR" $_.Exception.Message
}
finally {
    if ($resultados.Count -gt 0) { $resultados | Export-Csv -LiteralPath $Registro -NoTypeInformation -Encoding $CodificacionCsv -Delimiter ";" }
    Write-Paso "Resumen"
    $resultados | Group-Object Accion, Estado | ForEach-Object { Write-Host ("  {0,-34} {1}" -f $_.Name, $_.Count) }
    if ($resultados.Count -gt 0) { Write-Host "  Resultados: $Registro" }
}
