#Requires -Version 5.1
<#
.SYNOPSIS
    Da o quita permisos de buzones de Exchange Online desde un CSV: Acceso total, Enviar como, Enviar en nombre de y
    permisos de calendario. Con -SoloInforme, lista los permisos actuales sin cambiar nada.

.DESCRIPTION
    Cada fila del CSV es un permiso: Buzon, Usuario, Permiso, Nivel (solo calendario) y Accion (Dar o Quitar).
      AccesoTotal     abrir y gestionar el buzon (aparece solo en Outlook salvo -SinMapeoAutomatico)
      EnviarComo      enviar como si fuera el buzon
      EnviarEnNombre  enviar "en nombre de" (el destinatario ve los dos nombres)
      Calendario      permiso sobre el calendario: Revisor, Autor, Editor, Propietario, Disponibilidad, Detalles
    La carpeta de calendario se busca por su tipo, no por el nombre (que cambia con el idioma: Calendario, Calendar…).

.EXAMPLE
    .\Gestionar-PermisosBuzon.ps1 -Csv .\permisos.csv -Simular
.EXAMPLE
    .\Gestionar-PermisosBuzon.ps1 -Csv .\buzones.csv -SoloInforme
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Csv,
    [switch]$SoloInforme,
    [switch]$SinMapeoAutomatico,
    [string]$TenantId,
    [switch]$Simular,
    [switch]$SinConfirmar,
    [string]$CarpetaResultados
)

$ErrorActionPreference = "Stop"
$CodificacionCsv = if ($PSVersionTable.PSVersion.Major -ge 6) { "utf8BOM" } else { "UTF8" }   # CSV con BOM en 5.1 y en 7: Excel abre bien las tildes
$NivelesCalendario = @{ "revisor" = "Reviewer"; "autor" = "Author"; "editor" = "Editor"; "propietario" = "Owner"
    "editorpublicacion" = "PublishingEditor"; "disponibilidad" = "AvailabilityOnly"; "detalles" = "LimitedDetails"; "ninguno" = "None" }

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

function Connect-ExchangeSeguro {
    <# Reutiliza la sesion de Exchange Online si hay una; con -TenantId se detiene si es de otro tenant. #>
    param([string]$TenantId)
    if (-not (Get-Command Connect-ExchangeOnline -ErrorAction SilentlyContinue)) {
        throw "Falta el modulo ExchangeOnlineManagement. Instalalo con: Install-Module ExchangeOnlineManagement -Scope CurrentUser"
    }
    $con = $null
    try { $con = Get-ConnectionInformation | Where-Object { $_.State -eq "Connected" } | Select-Object -First 1 } catch { }
    if ($con -and $TenantId -and $con.TenantID -ne $TenantId) { Disconnect-ExchangeOnline -Confirm:$false; $con = $null }
    if (-not $con) { Connect-ExchangeOnline -ShowBanner:$false; $con = Get-ConnectionInformation | Select-Object -First 1 }
    if (-not $con) { throw "No se pudo conectar a Exchange Online." }
    if ($TenantId -and $con.TenantID -ne $TenantId) { throw "Tenant incorrecto: la sesion es de $($con.TenantID) y se esperaba $TenantId. No se ha tocado nada." }
    return $con
}

$CacheCalendario = @{}
function Get-CarpetaCalendario ([string]$Buzon) {
    <# "buzon@x.com:\Calendario" con el nombre real de la carpeta (depende del idioma del buzon). #>
    if (-not $CacheCalendario.ContainsKey($Buzon)) {
        $f = Get-EXOMailboxFolderStatistics -Identity $Buzon -FolderScope Calendar | Where-Object { $_.FolderType -eq "Calendar" } | Select-Object -First 1
        if (-not $f) { throw "No se encuentra la carpeta de calendario de $Buzon" }
        $CacheCalendario[$Buzon] = "${Buzon}:\$($f.Name)"
    }
    return $CacheCalendario[$Buzon]
}

$inicio = Get-Date
if (-not $CarpetaResultados) { $CarpetaResultados = Join-Path $PSScriptRoot "Resultados" }
New-Item -ItemType Directory -Force -Path $CarpetaResultados | Out-Null
$Registro = Join-Path $CarpetaResultados ("{0}_{1:yyyyMMdd_HHmmss}.csv" -f $(if ($SoloInforme) { "InformePermisos" } else { "PermisosBuzon" }), $inicio)
$resultados = New-Object System.Collections.Generic.List[object]

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " PERMISOS DE BUZONES$(if ($SoloInforme) { ' (informe, solo lee)' })" -ForegroundColor Cyan
if ($Simular) { Write-Host " MODO SIMULACION: no se cambia nada" -ForegroundColor Magenta }
Write-Host "==============================================" -ForegroundColor Cyan

try {
    $filas = Import-CsvFlexible -Ruta $Csv -Obligatorias @("Buzon")
    $con = Connect-ExchangeSeguro -TenantId $TenantId
    Write-Ok "Exchange Online: $($con.UserPrincipalName) (tenant $($con.TenantID))"

    # ------------------------------------------------------------ informe
    if ($SoloInforme) {
        foreach ($b in ($filas | ForEach-Object { (Get-Valor $_ "Buzon").ToLower() } | Where-Object { $_ } | Select-Object -Unique)) {
            Write-Paso $b
            try {
                $m = Get-EXOMailbox -Identity $b -Properties GrantSendOnBehalfTo, RecipientTypeDetails
                foreach ($p in (Get-EXOMailboxPermission -Identity $b | Where-Object { $_.User -notmatch '^NT AUTHORITY|^S-1-5' -and $_.AccessRights -match 'FullAccess' })) {
                    $resultados.Add([PSCustomObject]@{ Buzon = $b; Tipo = "$($m.RecipientTypeDetails)"; Usuario = "$($p.User)"; Permiso = "AccesoTotal"; Nivel = "" })
                }
                foreach ($p in (Get-EXORecipientPermission -Identity $b | Where-Object { $_.Trustee -notmatch '^NT AUTHORITY' })) {
                    $resultados.Add([PSCustomObject]@{ Buzon = $b; Tipo = "$($m.RecipientTypeDetails)"; Usuario = "$($p.Trustee)"; Permiso = "EnviarComo"; Nivel = "" })
                }
                foreach ($u in @($m.GrantSendOnBehalfTo)) {
                    $resultados.Add([PSCustomObject]@{ Buzon = $b; Tipo = "$($m.RecipientTypeDetails)"; Usuario = "$u"; Permiso = "EnviarEnNombre"; Nivel = "" })
                }
                foreach ($p in (Get-EXOMailboxFolderPermission -Identity (Get-CarpetaCalendario $b))) {
                    $resultados.Add([PSCustomObject]@{ Buzon = $b; Tipo = "$($m.RecipientTypeDetails)"; Usuario = "$($p.User)"; Permiso = "Calendario"; Nivel = ($p.AccessRights -join ",") })
                }
                $resultados | Where-Object Buzon -eq $b | ForEach-Object { Write-Host ("  {0,-15} {1,-40} {2}" -f $_.Permiso, $_.Usuario, $_.Nivel) }
            } catch { Write-Fallo $_.Exception.Message }
        }
        return
    }

    # ------------------------------------------------------------ cambios
    Write-Paso "Comprobaciones"
    $tipos = @{ "accesototal" = "AccesoTotal"; "fullaccess" = "AccesoTotal"; "enviarcomo" = "EnviarComo"; "sendas" = "EnviarComo"
        "enviarennombre" = "EnviarEnNombre"; "sendonbehalf" = "EnviarEnNombre"; "calendario" = "Calendario"; "calendar" = "Calendario" }
    $cambios = @(foreach ($f in $filas) {
            $b = (Get-Valor $f "Buzon").ToLower(); $u = (Get-Valor $f "Usuario").ToLower()
            $p = $tipos[((Get-Valor $f "Permiso") -replace '\s', '').ToLower()]
            $acc = (Get-Valor $f "Accion").ToLower(); if (-not $acc) { $acc = "dar" }
            if (-not $b -or -not $u) { continue }
            if (-not $p) { Write-Aviso "Permiso no reconocido en '$b' / '$u': $(Get-Valor $f 'Permiso')"; continue }
            if ($acc -notin "dar", "quitar") { Write-Aviso "Accion no reconocida (Dar o Quitar) en '$b' / '$u': $acc"; continue }
            $nivel = ""
            if ($p -eq "Calendario" -and $acc -eq "dar") {
                $nivel = $NivelesCalendario[((Get-Valor $f "Nivel") -replace '\s', '').ToLower()]
                if (-not $nivel) { Write-Aviso "Nivel de calendario no valido en '$b' / '$u' (Revisor, Autor, Editor, Propietario, Disponibilidad, Detalles)"; continue }
            }
            [PSCustomObject]@{ Buzon = $b; Usuario = $u; Permiso = $p; Nivel = $nivel; Accion = $acc }
        })
    if ($cambios.Count -eq 0) { throw "No hay ninguna fila valida (columnas: Buzon, Usuario, Permiso, Nivel, Accion)." }
    foreach ($x in ($cambios | ForEach-Object { $_.Buzon; $_.Usuario } | Select-Object -Unique)) {
        if (-not (Get-EXORecipient -Identity $x -ErrorAction SilentlyContinue)) { throw "No existe en el tenant: $x" }
    }
    Write-Ok "$($cambios.Count) permisos a aplicar; buzones y usuarios comprobados"

    if (-not $Simular -and -not $SinConfirmar) {
        $r = Read-Host "`nSe van a aplicar $($cambios.Count) cambios de permisos. ¿Continuar? (S/N)"
        if ($r -notmatch '^(s|si|sí)$') { Write-Aviso "Cancelado: no se ha cambiado nada."; return }
    }

    Write-Paso "Permisos"
    foreach ($c in $cambios) {
        $txt = "{0,-6} {1,-15} {2} -> {3}{4}" -f $c.Accion.ToUpper(), $c.Permiso, $c.Usuario, $c.Buzon, $(if ($c.Nivel) { " ($($c.Nivel))" } else { "" })
        if ($Simular) { Write-Sim $txt; $resultados.Add([PSCustomObject]@{ Fecha = (Get-Date -Format "dd/MM/yyyy HH:mm:ss"); Buzon = $c.Buzon; Usuario = $c.Usuario; Permiso = $c.Permiso; Nivel = $c.Nivel; Accion = $c.Accion; Estado = "SIMULADO"; Detalle = "" }); continue }
        $estado = "OK"; $det = ""
        try {
            switch ($c.Permiso) {
                "AccesoTotal" {
                    if ($c.Accion -eq "dar") { Add-MailboxPermission -Identity $c.Buzon -User $c.Usuario -AccessRights FullAccess -InheritanceType All -AutoMapping (-not $SinMapeoAutomatico) | Out-Null }
                    else { Remove-MailboxPermission -Identity $c.Buzon -User $c.Usuario -AccessRights FullAccess -InheritanceType All -Confirm:$false }
                }
                "EnviarComo" {
                    if ($c.Accion -eq "dar") { Add-RecipientPermission -Identity $c.Buzon -Trustee $c.Usuario -AccessRights SendAs -Confirm:$false | Out-Null }
                    else { Remove-RecipientPermission -Identity $c.Buzon -Trustee $c.Usuario -AccessRights SendAs -Confirm:$false }
                }
                "EnviarEnNombre" {
                    $op = if ($c.Accion -eq "dar") { "Add" } else { "Remove" }
                    Set-Mailbox -Identity $c.Buzon -GrantSendOnBehalfTo @{ $op = $c.Usuario }
                }
                "Calendario" {
                    $carpeta = Get-CarpetaCalendario $c.Buzon
                    $actual = Get-EXOMailboxFolderPermission -Identity $carpeta -User $c.Usuario -ErrorAction SilentlyContinue
                    if ($c.Accion -eq "quitar") {
                        if ($actual) { Remove-MailboxFolderPermission -Identity $carpeta -User $c.Usuario -Confirm:$false } else { $estado = "NO TENIA" }
                    } elseif ($actual) { Set-MailboxFolderPermission -Identity $carpeta -User $c.Usuario -AccessRights $c.Nivel | Out-Null; $det = "Cambiado desde $($actual.AccessRights -join ',')" }
                    else { Add-MailboxFolderPermission -Identity $carpeta -User $c.Usuario -AccessRights $c.Nivel | Out-Null }
                }
            }
            Write-Ok $txt
        } catch {
            $m = $_.Exception.Message
            if ($m -match "already exists|ya existe") { $estado = "YA TENIA"; Write-Aviso "$txt (ya lo tenia)" }
            elseif ($m -match "can't remove|isn't present|no existe|not found|couldn't be found") { $estado = "NO TENIA"; Write-Aviso "$txt (no lo tenia)" }
            else { $estado = "ERROR"; $det = $m; Write-Fallo "$txt $m" }
        }
        $resultados.Add([PSCustomObject]@{ Fecha = (Get-Date -Format "dd/MM/yyyy HH:mm:ss"); Buzon = $c.Buzon; Usuario = $c.Usuario; Permiso = $c.Permiso; Nivel = $c.Nivel; Accion = $c.Accion; Estado = $estado; Detalle = $det })
    }
}
catch {
    Write-Fallo $_.Exception.Message
}
finally {
    if ($resultados.Count -gt 0) {
        $resultados | Export-Csv -LiteralPath $Registro -NoTypeInformation -Encoding $CodificacionCsv -Delimiter ";"
        Write-Paso "Resumen"
        if (-not $SoloInforme) { $resultados | Group-Object Estado | ForEach-Object { Write-Host ("  {0,-10} {1}" -f $_.Name, $_.Count) } }
        Write-Host "  Resultados: $Registro"
        if (-not $SoloInforme -and -not $Simular) { Write-Host "  Los permisos pueden tardar hasta 60 minutos en verse en Outlook." -ForegroundColor DarkGray }
    }
}
