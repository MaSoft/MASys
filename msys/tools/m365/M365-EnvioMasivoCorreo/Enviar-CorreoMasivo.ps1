#Requires -Version 5.1
<#
.SYNOPSIS
    Envio masivo de correos individuales y personalizados por Microsoft Graph (una copia por destinatario, sin CCO).

.DESCRIPTION
    Lee una plantilla HTML con marcas {Columna} y un CSV de destinatarios (columna Correo y las que quieras), y envia
    a cada uno su correo con sus datos. Inicia sesion como tu (permiso delegado Mail.Send): no hace falta App
    Registration ni secretos. Puede enviar como un buzon compartido si tienes "Enviar como" en el.
    Pausa entre envios, reintentos ante saturacion (429), envio de prueba, reanudacion saltando los ya enviados
    y registro CSV.

.EXAMPLE
    .\Enviar-CorreoMasivo.ps1 -Destinatarios .\lista.csv -Plantilla .\aviso.html -Asunto "Aviso para {Nombre}" -Prueba yo@empresa.com
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Destinatarios,
    [Parameter(Mandatory = $true)][string]$Plantilla,
    [Parameter(Mandatory = $true)][string]$Asunto,
    [string]$Remitente,
    [string[]]$Adjuntos,
    [ValidateRange(0, 120)][int]$PausaSegundos = 2,
    [string]$Prueba,
    [string]$OmitirEnviadosDe,
    [switch]$NoGuardarEnEnviados,
    [string]$TenantId,
    [switch]$Simular,
    [switch]$SinConfirmar,
    [string]$CarpetaResultados
)

$ErrorActionPreference = "Stop"
$CodificacionCsv = if ($PSVersionTable.PSVersion.Major -ge 6) { "utf8BOM" } else { "UTF8" }   # CSV con BOM en 5.1 y en 7: Excel abre bien las tildes

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

function Read-TextoArchivo {
    <# Lee un archivo de texto en UTF-8 o, si no lo es, en ANSI. #>
    param([string]$Ruta)
    $bytes = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $Ruta).Path)
    try { return (New-Object System.Text.UTF8Encoding($false, $true)).GetString($bytes).TrimStart([char]0xFEFF) }
    catch { return [System.Text.Encoding]::GetEncoding(1252).GetString($bytes) }
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
        throw "Tenant incorrecto: la sesion es de $($ctx.TenantId) y se esperaba $TenantId. No se ha enviado nada."
    }
    $faltan = @($Scopes | Where-Object { $ctx.Scopes -notcontains $_ })
    if ($faltan.Count -gt 0) { throw "La sesion no tiene estos permisos: $($faltan -join ', ')" }
    return $ctx
}

function Expand-Plantilla {
    <# Sustituye {Columna} por el valor de la fila (escapado para HTML si $Html). #>
    param([string]$Texto, $Fila, [string[]]$Columnas, [switch]$Html)
    foreach ($c in $Columnas) {
        $v = Get-Valor $Fila $c
        if ($Html) { $v = [System.Net.WebUtility]::HtmlEncode($v) }
        $Texto = $Texto.Replace("{$c}", $v)
    }
    return $Texto
}

# ============================================================ inicio
$inicio = Get-Date
if (-not $CarpetaResultados) { $CarpetaResultados = Join-Path $PSScriptRoot "Resultados" }
New-Item -ItemType Directory -Force -Path $CarpetaResultados | Out-Null
$Registro = Join-Path $CarpetaResultados ("EnvioMasivo_{0:yyyyMMdd_HHmmss}.csv" -f $inicio)
$resultados = New-Object System.Collections.Generic.List[object]
function Add-Resultado ($Correo, $Estado, $Detalle = "") {
    $resultados.Add([PSCustomObject]@{ Fecha = (Get-Date -Format "dd/MM/yyyy HH:mm:ss"); Correo = $Correo; Estado = $Estado; Detalle = $Detalle })
}

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " ENVIO MASIVO DE CORREO (Microsoft Graph)" -ForegroundColor Cyan
if ($Simular) { Write-Host " MODO SIMULACION: no se envia nada" -ForegroundColor Magenta }
Write-Host "==============================================" -ForegroundColor Cyan

try {
    Write-Paso "Comprobaciones"
    $filas = Import-CsvFlexible -Ruta $Destinatarios -Obligatorias @("Correo")
    $columnas = @($filas[0].PSObject.Properties.Name | ForEach-Object { $_.Trim() })
    if (-not (Test-Path -LiteralPath $Plantilla)) { throw "No se encuentra la plantilla: $Plantilla" }
    $html = Read-TextoArchivo $Plantilla
    if ($html -notmatch '<html|<body|<p|<div|<br') { $html = "<div>" + ([System.Net.WebUtility]::HtmlEncode($html) -replace "`r?`n", "<br>") + "</div>" }

    # Marcas que no son columnas del CSV: se detiene para no enviar "{Nombre}" literal a nadie
    $marcas = @([regex]::Matches($html + " " + $Asunto, '\{([A-Za-z0-9_áéíóúñÁÉÍÓÚÑ ]+)\}') | ForEach-Object { $_.Groups[1].Value } | Select-Object -Unique)
    $sinColumna = @($marcas | Where-Object { $columnas -notcontains $_ })
    if ($sinColumna.Count) { throw "La plantilla o el asunto usan marcas que no son columnas del CSV: {$($sinColumna -join '}, {')}. Columnas: $($columnas -join ', ')" }
    Write-Ok "Plantilla con $($marcas.Count) marcas: $(if ($marcas) { ($marcas | ForEach-Object { "{$_}" }) -join ' ' } else { 'ninguna' })"

    $vistos = @{}
    $omitir = @{}
    if ($OmitirEnviadosDe) {
        foreach ($f in (Import-CsvFlexible -Ruta $OmitirEnviadosDe -Obligatorias @("Correo", "Estado"))) {
            if ((Get-Valor $f "Estado") -eq "OK") { $omitir[(Get-Valor $f "Correo").ToLower()] = $true }
        }
        Write-Ok "$($omitir.Count) correos ya enviados en $OmitirEnviadosDe se saltaran"
    }
    $lista = @(foreach ($f in $filas) {
            $c = (Get-Valor $f "Correo").ToLower()
            if ($c -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') { if ($c) { Write-Aviso "Correo no valido, se omite: $c" }; continue }
            if ($vistos.ContainsKey($c) -or $omitir.ContainsKey($c)) { continue }
            $vistos[$c] = $true
            [PSCustomObject]@{ Correo = $c; Fila = $f }
        })
    if ($lista.Count -eq 0) { throw "No queda ningun destinatario al que enviar." }
    Write-Ok "$($lista.Count) destinatarios"

    $adj = @()
    $total = 0
    foreach ($a in $Adjuntos) {
        if (-not (Test-Path -LiteralPath $a)) { throw "No se encuentra el adjunto: $a" }
        $b = [System.IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $a).Path)
        $total += $b.Length
        $adj += @{ "@odata.type" = "#microsoft.graph.fileAttachment"; name = [System.IO.Path]::GetFileName($a)
            contentType = "application/octet-stream"; contentBytes = [Convert]::ToBase64String($b) }
    }
    # sendMail admite unos 4 MB por peticion (el adjunto en base64 ocupa un tercio mas)
    if ($total -gt 2.9MB) { throw "Los adjuntos suman $([math]::Round($total / 1MB, 1)) MB: el maximo por correo con este metodo es unos 3 MB. Comparte el archivo con un enlace de OneDrive o SharePoint." }
    if ($adj.Count) { Write-Ok "$($adj.Count) adjuntos ($([math]::Round($total / 1KB)) KB)" }

    $ctx = Connect-GraphSeguro -Scopes @("Mail.Send", "User.Read") -TenantId $TenantId
    if (-not $Remitente) { $Remitente = $ctx.Account }
    if ($Remitente -ne $ctx.Account) {
        $ctx = Connect-GraphSeguro -Scopes @("Mail.Send", "Mail.Send.Shared", "User.Read") -TenantId $TenantId
        Write-Aviso "Se enviara como ${Remitente}: tu cuenta necesita el permiso «Enviar como» en ese buzon."
    }
    Write-Ok "Remitente: $Remitente (sesion de $($ctx.Account))"

    function Send-Uno ($Para, $Fila) {
        $mensaje = @{
            subject = (Expand-Plantilla $Asunto $Fila $columnas)
            body = @{ contentType = "HTML"; content = (Expand-Plantilla $html $Fila $columnas -Html) }
            toRecipients = @(@{ emailAddress = @{ address = $Para } })
        }
        if ($adj.Count) { $mensaje.attachments = $adj }
        $cuerpo = @{ message = $mensaje; saveToSentItems = (-not $NoGuardarEnEnviados) } | ConvertTo-Json -Depth 8
        for ($i = 1; $i -le 4; $i++) {
            try {
                Invoke-MgGraphRequest -Method POST -Uri "/v1.0/users/$([uri]::EscapeDataString($Remitente))/sendMail" -Body $cuerpo -ContentType "application/json; charset=utf-8" | Out-Null
                return
            } catch {
                $m = $_.Exception.Message
                if ($i -eq 4 -or $m -notmatch "429|TooManyRequests|ServiceUnavailable|503|504|timed out") { throw }
                Write-Aviso "Exchange pide esperar (saturacion): reintento $i en $(30 * $i) s"
                Start-Sleep -Seconds (30 * $i)
            }
        }
    }

    if ($Prueba) {
        Write-Paso "Envio de prueba a $Prueba (con los datos de $($lista[0].Correo))"
        if ($Simular) { Write-Sim "se enviaria la prueba" } else { Send-Uno $Prueba $lista[0].Fila; Write-Ok "Prueba enviada: revisala antes del envio real" }
        Add-Resultado $Prueba $(if ($Simular) { "SIMULADO" } else { "PRUEBA" }) "Datos de $($lista[0].Correo)"
        return
    }

    if (-not $Simular -and -not $SinConfirmar) {
        $mins = [math]::Ceiling($lista.Count * ($PausaSegundos + 1) / 60)
        $r = Read-Host "`nSe van a enviar $($lista.Count) correos desde $Remitente (unos $mins min). ¿Continuar? (S/N)"
        if ($r -notmatch '^(s|si|sí)$') { Write-Aviso "Cancelado: no se ha enviado nada."; return }
    }

    Write-Paso "Envio"
    $n = 0
    foreach ($d in $lista) {
        $n++
        $txt = "[{0}/{1}] {2}" -f $n, $lista.Count, $d.Correo
        if ($Simular) { Write-Sim "$txt - asunto: $(Expand-Plantilla $Asunto $d.Fila $columnas)"; Add-Resultado $d.Correo "SIMULADO"; continue }
        try {
            Send-Uno $d.Correo $d.Fila
            Write-Ok $txt
            Add-Resultado $d.Correo "OK"
        } catch {
            Write-Fallo "$txt $($_.Exception.Message)"
            Add-Resultado $d.Correo "ERROR" $_.Exception.Message
        }
        if ($n -lt $lista.Count -and $PausaSegundos) { Start-Sleep -Seconds $PausaSegundos }
    }
}
catch {
    Write-Fallo $_.Exception.Message
    Add-Resultado "" "ERROR" $_.Exception.Message
}
finally {
    if ($resultados.Count -gt 0) { $resultados | Export-Csv -LiteralPath $Registro -NoTypeInformation -Encoding $CodificacionCsv -Delimiter ";" }
    Write-Paso "Resumen"
    Write-Host "  Enviados: $(@($resultados | Where-Object Estado -eq 'OK').Count)   Errores: $(@($resultados | Where-Object Estado -eq 'ERROR').Count)"
    if ($resultados.Count -gt 0) {
        Write-Host "  Resultados: $Registro"
        if (@($resultados | Where-Object Estado -eq 'ERROR').Count) { Write-Aviso "Para reenviar solo los que fallaron: -OmitirEnviadosDe `"$Registro`"" }
    }
}
