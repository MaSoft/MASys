#Requires -Version 5.1
<#
.SYNOPSIS
    Lee de un buzon las respuestas a un correo (por ejemplo, el de un envio masivo) y las pasa a un Excel acumulativo:
    datos pedidos en lineas numeradas, revision manual, respuestas automaticas, rebotes y quien no ha contestado.

.DESCRIPTION
    Busca en una carpeta del buzon (la bandeja de entrada por defecto) los correos recibidos desde una fecha cuyo
    asunto contiene el texto indicado. Por cada uno:
      - Respuesta automatica (fuera de la oficina...) o rebote: hoja "Automáticas".
      - Con -Campos (p. ej. Hotel, Usuario, Equipo) saca los datos de las lineas numeradas de la respuesta
        (1. -> primer campo, 2. -> segundo...) o de lineas "Campo: valor". Varios bloques 1., 2., 3. en un mismo
        correo dan una fila por bloque. Todos los campos = COMPLETO; alguno = PARCIAL; ninguno = hoja "Revisión manual".
      - Con -Destinatarios (el CSV del envio o el registro de Enviar-CorreoMasivo) la hoja "Pendientes" lista a quien
        no ha contestado.
    El Excel es acumulativo: cada ejecucion añade solo los correos nuevos y conserva las filas anteriores (tambien lo
    corregido a mano). Solo lee: no marca como leido, no mueve ni borra correos.
    Inicio de sesion como tu (Mail.Read; Mail.Read.Shared para otro buzon). Para tareas programadas, con el
    certificado de una App Registration (-ClientId y -CertificateThumbprint): ningun secreto en el script.

.EXAMPLE
    .\Leer-RespuestasCorreo.ps1 -Asunto "Migración de correo" -Desde 08/03/2026 -Campos Hotel,Usuario,Equipo -Destinatarios .\lista.csv
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Asunto,
    [string]$Buzon,
    [string]$Desde,
    [string[]]$Campos,
    [string]$Destinatarios,
    [string]$CarpetaCorreo = "inbox",
    [string]$Excel,
    [string]$TenantId,
    [string]$ClientId,
    [string]$CertificateThumbprint,
    [string]$CarpetaResultados
)

$ErrorActionPreference = "Stop"

# ============================================================ utilidades comunes
function Write-Paso  ($t) { Write-Host "`n== $t" -ForegroundColor Cyan }
function Write-Ok    ($t) { Write-Host "  [OK]    $t" -ForegroundColor Green }
function Write-Aviso ($t) { Write-Host "  [AVISO] $t" -ForegroundColor Yellow }
function Write-Fallo ($t) { Write-Host "  [ERROR] $t" -ForegroundColor Red }

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
        throw "Tenant incorrecto: la sesion es de $($ctx.TenantId) y se esperaba $TenantId. No se ha leido nada."
    }
    $faltan = @($Scopes | Where-Object { $ctx.Scopes -notcontains $_ })
    if ($faltan.Count -gt 0) { throw "La sesion no tiene estos permisos: $($faltan -join ', ')" }
    return $ctx
}

# ============================================================ funciones propias
function Connect-GraphCertificado {
    <# Acceso de aplicacion (permiso Mail.Read de tipo Aplicacion) con un certificado instalado en el equipo. #>
    param([string]$ClientId, [string]$CertificateThumbprint, [string]$TenantId)
    if (-not (Get-Module -ListAvailable -Name Microsoft.Graph.Authentication)) {
        throw "Falta el modulo Microsoft.Graph. Instalalo con: Install-Module Microsoft.Graph -Scope CurrentUser"
    }
    Connect-MgGraph -ClientId $ClientId -CertificateThumbprint $CertificateThumbprint -TenantId $TenantId -NoWelcome | Out-Null
    $ctx = Get-MgContext
    if (-not $ctx -or $ctx.TenantId -ne $TenantId) { throw "No se pudo iniciar sesion con el certificado en el tenant $TenantId." }
    return $ctx
}

function Initialize-ImportExcel {
    if (-not (Get-Module -ListAvailable -Name ImportExcel)) {
        Write-Aviso "Instalando el modulo ImportExcel para el usuario actual (genera el .xlsx sin tener Excel)..."
        Install-Module ImportExcel -Scope CurrentUser -Force -AllowClobber
    }
    Import-Module ImportExcel -ErrorAction Stop
}

function ConvertTo-Fecha {
    param([string]$Texto)
    $d = [datetime]::MinValue
    foreach ($f in "dd/MM/yyyy", "d/M/yyyy", "yyyy-MM-dd") {
        if ([datetime]::TryParseExact($Texto.Trim(), $f, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::None, [ref]$d)) { return $d }
    }
    throw "Fecha no valida: '$Texto' (usa DD/MM/AAAA)"
}

function ConvertTo-FechaLocal {
    <# receivedDateTime llega como texto ISO en UTC o, segun la version de PowerShell, ya convertido a fecha. #>
    param($Valor)
    if ($Valor -is [datetime]) { $d = $Valor }
    else {
        $d = [datetime]::Parse([string]$Valor, [Globalization.CultureInfo]::InvariantCulture,
            [Globalization.DateTimeStyles]::AssumeUniversal -bor [Globalization.DateTimeStyles]::AdjustToUniversal)
        $d = [datetime]::SpecifyKind($d, [DateTimeKind]::Utc)
    }
    if ($d.Kind -eq [DateTimeKind]::Utc) { return $d.ToLocalTime() }
    return $d
}

function Remove-Tildes {
    param([string]$Texto)
    $n = $Texto.Normalize([Text.NormalizationForm]::FormD)
    return -join ($n.ToCharArray() | Where-Object { [Globalization.CharUnicodeInfo]::GetUnicodeCategory($_) -ne [Globalization.UnicodeCategory]::NonSpacingMark })
}

function ConvertTo-PatronEtiqueta {
    <# Patron que reconoce el nombre de un campo con o sin tildes y en mayusculas o minusculas. #>
    param([string]$Campo)
    $p = ""
    foreach ($ch in (Remove-Tildes $Campo.Trim()).ToLower().ToCharArray()) {
        switch ([string]$ch) {
            "a" { $p += "[aáàä]" } "e" { $p += "[eéèë]" } "i" { $p += "[iíìï]" }
            "o" { $p += "[oóòö]" } "u" { $p += "[uúùü]" } "n" { $p += "[nñ]" }
            " " { $p += "\s+" }
            default { $p += [regex]::Escape([string]$ch) }
        }
    }
    return $p
}

function Get-ValorEtiquetado {
    <# "Hotel: X", "Hotel donde te encuentras: X", "Equipo - X" -> X. Vacio si la linea no empieza por el campo. #>
    param([string]$Linea, [string]$Campo)
    $m = [regex]::Match($Linea, "(?i)^\s*" + (ConvertTo-PatronEtiqueta $Campo) + "\b[^:=>\r\n]{0,40}?(?:[:=>]|\s-\s)\s*(.+)$")
    if ($m.Success) { return $m.Groups[1].Value.Trim() }
    return ""
}

function Get-TipoAutomatica {
    <# "Rebote", "Respuesta automática" o "" si la ha escrito una persona. #>
    param([string]$Asunto, [string]$Texto, [string]$Remitente)
    if ($Remitente -match '(?i)^(postmaster|mailer-daemon|microsoftexchange[0-9a-f]*)@' -or
        $Asunto -match '(?i)^\s*(undeliverable|undelivered|no se puede entregar|no entregado|non remis|delivery status notification|mail delivery failed|returned mail|delivery has failed)') { return "Rebote" }
    if ($Asunto -match '(?i)(respuesta\s+autom[aá]tica|automatic\s+reply|auto[\-\s]?reply|fuera\s+de\s+(la\s+)?oficina|out\s+of\s+(the\s+)?office|^\s*(oof|ooo)\b)') { return "Respuesta automática" }
    $inicio = (@(([string]$Texto).Trim() -split "`n") | Select-Object -First 3) -join " "
    if ($inicio -match '(?i)(estoy\s+fuera\s+de\s+la\s+oficina|i\s+am\s+(currently\s+)?(out\s+of|away\s+from)|no\s+estoy\s+disponible|estar[eé]\s+(de\s+vacaciones|ausente)|this\s+is\s+an?\s+auto(matic)?(ally)?\s*(generated\s+)?(reply|response|message))') { return "Respuesta automática" }
    return ""
}

function Get-DatosRespuesta {
    <#
        Saca los campos de una respuesta (texto plano). Emite un objeto por bloque 1., 2., 3. con Valores, Encontrados,
        Nota y Texto. Si no hay ningun dato, un solo objeto con Encontrados = 0 y el texto de la respuesta.
    #>
    param([string]$Texto, [string[]]$Campos)
    $lineas = @(([string]$Texto -replace "`r", "") -split "`n" | ForEach-Object { $_.Trim().Normalize([Text.NormalizationForm]::FormC) } | Where-Object { $_ })
    # Se corta en la cabecera del correo original citado y se saltan las lineas citadas con ">"
    $corte = '(?i)^(-{2,}\s*(mensaje original|original message)|_{3,}|(de|from)\s*:.*@|enviado\s*:|sent\s*:|el\s.{4,120}escribi[oó]|on\s.{4,120}wrote)'
    $propias = New-Object System.Collections.Generic.List[string]
    foreach ($l in $lineas) {
        if ($l -match $corte) { break }
        if ($l -match '^>') { continue }
        $propias.Add($l)
    }
    if ($propias.Count -eq 0) { foreach ($l in $lineas) { if ($l -notmatch '^>') { $propias.Add($l) } } }
    $recorta = { param($t) if ($t.Length -gt 1000) { $t.Substring(0, 1000) + "..." } else { $t } }

    $Campos = @($Campos | Where-Object { $_ -and $_.Trim() } | ForEach-Object { $_.Trim() })
    if ($Campos.Count -eq 0) {
        return [pscustomobject]@{ Valores = [ordered]@{}; Encontrados = 0; Nota = ""; Texto = (& $recorta ($propias -join " | ")) }
    }
    # Primero las etiquetas mas largas: "Nombre del equipo" antes que "Nombre"
    $porLongitud = @($Campos | Sort-Object { $_.Length } -Descending)

    # Un bloque nuevo empieza cuando vuelve a aparecer "1." (varios equipos en el mismo correo)
    $bloques = New-Object System.Collections.Generic.List[object]
    $actual = New-Object System.Collections.Generic.List[string]
    $vistoUno = $false
    foreach ($l in $propias) {
        if ($l -match '^\s*1\s*[\.\)\-:º]') {
            if ($vistoUno -and $actual.Count) { $bloques.Add($actual.ToArray()); $actual = New-Object System.Collections.Generic.List[string] }
            $vistoUno = $true
        }
        $actual.Add($l)
    }
    if ($actual.Count) { $bloques.Add($actual.ToArray()) }

    $salida = New-Object System.Collections.Generic.List[object]
    $anterior = $null
    foreach ($b in $bloques) {
        $valores = [ordered]@{}
        foreach ($c in $Campos) { $valores[$c] = "" }
        foreach ($l in $b) {
            $m = [regex]::Match($l, '^\s*(\d{1,2})\s*[\.\)\-:º]+\s*(.*)$')
            $t = if ($m.Success) { $m.Groups[2].Value.Trim() } else { $l }
            $asignado = $false
            foreach ($c in $porLongitud) {
                $v = Get-ValorEtiquetado $t $c
                if ($v) { if (-not $valores[$c]) { $valores[$c] = $v.TrimEnd('.').Trim() }; $asignado = $true; break }
            }
            if (-not $asignado -and $m.Success -and $t) {
                $n = [int]$m.Groups[1].Value
                if ($n -ge 1 -and $n -le $Campos.Count -and -not $valores[$Campos[$n - 1]]) {
                    # "3. Nombre del equipo: PC01": se quita la pregunta copiada si menciona el campo
                    $q = [regex]::Match($t, '^(.{1,50}?)(?:[:=>]|\s-\s)\s*(.+)$')
                    if ($q.Success -and (Remove-Tildes $q.Groups[1].Value).ToLower().Contains((Remove-Tildes $Campos[$n - 1]).ToLower())) { $t = $q.Groups[2].Value }
                    $valores[$Campos[$n - 1]] = $t.TrimEnd('.').Trim()
                }
            }
        }
        $encontrados = @($Campos | Where-Object { $valores[$_] }).Count
        if ($encontrados -eq 0) { continue }
        # En el 2.º bloque y siguientes se suele omitir lo que no cambia (el hotel): se hereda y se indica
        $heredados = @()
        if ($anterior) {
            foreach ($c in $Campos) { if (-not $valores[$c] -and $anterior[$c]) { $valores[$c] = $anterior[$c]; $heredados += $c } }
        }
        $nota = if ($heredados) { "Heredado del bloque anterior: $($heredados -join ', ')" } else { "" }
        $salida.Add([pscustomobject]@{ Valores = $valores; Encontrados = $encontrados; Nota = $nota; Texto = (& $recorta ($b -join " | ")) })
        $anterior = $valores
    }
    if ($salida.Count -eq 0) {
        $vacios = [ordered]@{}
        foreach ($c in $Campos) { $vacios[$c] = "" }
        return [pscustomobject]@{ Valores = $vacios; Encontrados = 0; Nota = ""; Texto = (& $recorta ($propias -join " | ")) }
    }
    return $salida.ToArray()
}

function Copy-Fila {
    param($Origen)
    $f = [ordered]@{}
    foreach ($k in $Origen.Keys) { $f[$k] = $Origen[$k] }
    return $f
}

function Invoke-GraphGet {
    param([string]$Uri)
    for ($i = 1; $i -le 4; $i++) {
        try { return Invoke-MgGraphRequest -Method GET -Uri $Uri -Headers @{ Prefer = 'outlook.body-content-type="text"' } }
        catch {
            $m = $_.Exception.Message
            if ($i -eq 4 -or $m -notmatch "429|TooManyRequests|ServiceUnavailable|503|504|timed out") { throw }
            Write-Aviso "Exchange pide esperar (saturacion): reintento $i en $(30 * $i) s"
            Start-Sleep -Seconds (30 * $i)
        }
    }
}

# ============================================================ inicio
$HOJA_RESUMEN = "Resumen"; $HOJA_RESP = "Respuestas"; $HOJA_MANUAL = "Revisión manual"
$HOJA_AUTO = "Automáticas"; $HOJA_PEND = "Pendientes"

if (-not $CarpetaResultados) { $CarpetaResultados = Join-Path $PSScriptRoot "Resultados" }
$CarpetaResultados = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($CarpetaResultados)
New-Item -ItemType Directory -Force -Path $CarpetaResultados | Out-Null
if (-not $Excel) {
    $base = ((Remove-Tildes $Asunto) -replace '[^A-Za-z0-9]+', '_').Trim('_')
    if ($base.Length -gt 40) { $base = $base.Substring(0, 40) }
    if (-not $base) { $base = "correo" }
    $Excel = Join-Path $CarpetaResultados "Respuestas_$base.xlsx"
}
$Excel = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($Excel)
$tmp = $null
$Campos = @($Campos | ForEach-Object { $_ -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$nuevasR = New-Object System.Collections.Generic.List[object]
$nuevasM = New-Object System.Collections.Generic.List[object]
$nuevasA = New-Object System.Collections.Generic.List[object]
$pendientes = @()
$escrito = $false

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " RESPUESTAS A UN CORREO -> EXCEL (Microsoft Graph)" -ForegroundColor Cyan
Write-Host " Solo lectura: no se marca, mueve ni borra nada" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

try {
    Write-Paso "Comprobaciones"
    $fechaDesde = if ($Desde) { ConvertTo-Fecha $Desde } else { (Get-Date).Date.AddDays(-30) }
    Write-Ok "Asunto que contenga: «$Asunto», recibidos desde el $($fechaDesde.ToString('dd/MM/yyyy'))"
    if ($Campos.Count) { Write-Ok "Campos: $($Campos -join ', ')" }
    $nombresReservados = @("Fecha", "Hora", "Remitente", "Dominio", "Estado", "Nota", "Asunto", "Texto", "ID_Mensaje", "Tipo")
    $choque = @($Campos | Where-Object { $nombresReservados -contains $_ })
    if ($choque.Count) { throw "Estos nombres de campo ya son columnas del Excel; cambialos: $($choque -join ', ')" }

    $lista = @()
    if ($Destinatarios) {
        $filas = Import-CsvFlexible -Ruta $Destinatarios -Obligatorias @("Correo")
        $conEstado = @($filas[0].PSObject.Properties.Name | ForEach-Object { $_.Trim() }) -contains "Estado"
        # El registro de Enviar-CorreoMasivo trae Estado: solo cuentan los enviados de verdad
        $lista = @($filas | Where-Object { -not $conEstado -or (Get-Valor $_ "Estado") -eq "OK" } |
                ForEach-Object { (Get-Valor $_ "Correo").ToLower() } | Where-Object { $_ -match '^[^@\s]+@[^@\s]+\.[^@\s]+$' } | Select-Object -Unique)
        Write-Ok "$($lista.Count) destinatarios para la hoja de pendientes$(if ($conEstado) { ' (solo los de Estado OK del registro)' })"
    }

    Initialize-ImportExcel
    $previas = @{ R = @(); M = @(); A = @() }
    if (Test-Path -LiteralPath $Excel) {
        try { $fs = [System.IO.File]::Open($Excel, 'Open', 'ReadWrite', 'None'); $fs.Close() }
        catch { throw "El Excel esta abierto o bloqueado: cierralo y vuelve a lanzar ($Excel)" }
        $hojas = @(Get-ExcelSheetInfo -Path $Excel | ForEach-Object { $_.Name })
        if ($hojas -contains $HOJA_RESP) { $previas.R = @(Import-Excel -Path $Excel -WorksheetName $HOJA_RESP) }
        if ($hojas -contains $HOJA_MANUAL) { $previas.M = @(Import-Excel -Path $Excel -WorksheetName $HOJA_MANUAL) }
        if ($hojas -contains $HOJA_AUTO) { $previas.A = @(Import-Excel -Path $Excel -WorksheetName $HOJA_AUTO) }
        $muestra = @($previas.R + $previas.M) | Select-Object -First 1
        if ($muestra) {
            $colsPrevias = @($muestra.PSObject.Properties.Name)
            $distintas = @($Campos | Where-Object { $colsPrevias -notcontains $_ }) + @($colsPrevias | Where-Object { $nombresReservados -notcontains $_ -and $Campos -notcontains $_ })
            if ($distintas.Count) { throw "El Excel existente tiene otros campos ($($distintas -join ', ')). Usa los mismos -Campos o indica otro -Excel." }
        }
        Write-Ok "Excel existente: $($previas.R.Count + $previas.M.Count + $previas.A.Count) filas anteriores se conservan"
    }
    $ids = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach ($f in @($previas.R + $previas.M + $previas.A)) { if ($f.ID_Mensaje) { [void]$ids.Add([string]$f.ID_Mensaje) } }

    if ($ClientId -or $CertificateThumbprint) {
        if (-not ($ClientId -and $CertificateThumbprint -and $TenantId -and $Buzon)) { throw "Para el acceso con certificado hacen falta -ClientId, -CertificateThumbprint, -TenantId y -Buzon." }
        $ctx = Connect-GraphCertificado -ClientId $ClientId -CertificateThumbprint $CertificateThumbprint -TenantId $TenantId
        Write-Ok "Sesion de aplicacion en el tenant $($ctx.TenantId)"
    } else {
        $ctx = Connect-GraphSeguro -Scopes @("Mail.Read", "User.Read") -TenantId $TenantId
        if (-not $Buzon) { $Buzon = $ctx.Account }
        if ($Buzon -ne $ctx.Account) {
            $ctx = Connect-GraphSeguro -Scopes @("Mail.Read", "Mail.Read.Shared", "User.Read") -TenantId $TenantId
            Write-Aviso "Se leera el buzon ${Buzon}: tu cuenta necesita Acceso total en ese buzon."
        }
        Write-Ok "Sesion de $($ctx.Account)"
    }

    Write-Paso "Lectura del buzon $Buzon (carpeta $CarpetaCorreo)"
    $filtro = [uri]::EscapeDataString("receivedDateTime ge " + $fechaDesde.ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ"))
    $uri = "/v1.0/users/$([uri]::EscapeDataString($Buzon))/mailFolders/$([uri]::EscapeDataString($CarpetaCorreo))/messages" +
        "?`$filter=$filtro&`$orderby=receivedDateTime asc&`$top=50&`$select=id,subject,from,receivedDateTime,body,bodyPreview"
    $asuntoNorm = (Remove-Tildes $Asunto).ToLower()
    $leidos = 0; $coinciden = 0; $yaEstaban = 0
    while ($uri) {
        $r = Invoke-GraphGet $uri
        foreach ($msg in @($r.value)) {
            $leidos++
            $asuntoMsg = [string]$msg.subject
            if (-not (Remove-Tildes $asuntoMsg).ToLower().Contains($asuntoNorm)) { continue }
            $coinciden++
            if ($ids.Contains([string]$msg.id)) { $yaEstaban++; continue }
            $de = ([string]$msg.from.emailAddress.address).ToLower()
            if ($de -eq $Buzon.ToLower()) { continue }
            $fecha = ConvertTo-FechaLocal $msg.receivedDateTime
            $dominio = if ($de -match '@(.+)$') { $Matches[1] } else { "" }
            $texto = [string]$msg.body.content
            if (-not $texto.Trim()) { $texto = [string]$msg.bodyPreview }
            $comun = [ordered]@{ Fecha = $fecha.ToString("dd/MM/yyyy"); Hora = $fecha.ToString("HH:mm"); Remitente = $de; Dominio = $dominio }

            $tipo = Get-TipoAutomatica -Asunto $asuntoMsg -Texto $texto -Remitente $de
            if ($tipo) {
                $fila = Copy-Fila $comun
                $prev = [string]$msg.bodyPreview
                if ($prev.Length -gt 500) { $prev = $prev.Substring(0, 500) + "..." }
                $fila.Tipo = $tipo; $fila.Asunto = $asuntoMsg; $fila.Texto = $prev; $fila.ID_Mensaje = [string]$msg.id
                $nuevasA.Add([pscustomobject]$fila)
                Write-Host "  [$($tipo.ToUpper())] $de" -ForegroundColor Magenta
                continue
            }
            foreach ($d in @(Get-DatosRespuesta -Texto $texto -Campos $Campos)) {
                $fila = Copy-Fila $comun
                foreach ($c in $Campos) { $fila[$c] = $d.Valores[$c] }
                $estado = if (-not $Campos.Count) { "RECIBIDA" } elseif ($d.Encontrados -eq $Campos.Count) { "COMPLETO" } elseif ($d.Encontrados -gt 0) { "PARCIAL" } else { "SIN DATOS" }
                $fila.Estado = $estado; $fila.Nota = $d.Nota; $fila.Asunto = $asuntoMsg; $fila.Texto = $d.Texto; $fila.ID_Mensaje = [string]$msg.id
                if ($estado -eq "SIN DATOS") { $nuevasM.Add([pscustomobject]$fila); Write-Host "  [REVISAR]  $de" -ForegroundColor Red }
                else {
                    $nuevasR.Add([pscustomobject]$fila)
                    $color = if ($estado -eq "PARCIAL") { "Yellow" } else { "Green" }
                    $resumenCampos = if ($Campos.Count) { " -> " + (($Campos | ForEach-Object { "${_}: $($fila[$_])" }) -join " | ") } else { "" }
                    Write-Host "  [$estado] $de$resumenCampos" -ForegroundColor $color
                }
            }
        }
        $uri = $r['@odata.nextLink']
    }
    Write-Ok "$leidos correos revisados; $coinciden con el asunto ($yaEstaban ya estaban en el Excel)"

    $todasR = @($previas.R) + $nuevasR.ToArray(); $todasM = @($previas.M) + $nuevasM.ToArray(); $todasA = @($previas.A) + $nuevasA.ToArray()
    if ($lista.Count) {
        $contestaron = New-Object 'System.Collections.Generic.HashSet[string]'
        foreach ($f in @($todasR + $todasM + $todasA)) { if ($f.Remitente) { [void]$contestaron.Add(([string]$f.Remitente).ToLower()) } }
        $pendientes = @($lista | Where-Object { -not $contestaron.Contains($_) } | ForEach-Object {
                [pscustomobject]@{ Correo = $_; Dominio = ($_ -split '@')[1]; Estado = "SIN RESPUESTA" } })
    }
    if (($todasR.Count + $todasM.Count + $todasA.Count + $pendientes.Count) -eq 0) { Write-Aviso "Todavia no hay respuestas: no se crea el Excel."; return }

    Write-Paso "Excel"
    $dominios = @(@($todasR + $todasM + $todasA | ForEach-Object { $_.Dominio }) + @($pendientes | ForEach-Object { $_.Dominio }) | Where-Object { $_ } | Sort-Object -Unique)
    $contar = { param($filas, $dom, $estado) @($filas | Where-Object { (-not $dom -or $_.Dominio -eq $dom) -and (-not $estado -or $_.Estado -eq $estado) }).Count }
    $resumen = @(foreach ($dom in @($dominios) + @("")) {
            [pscustomobject][ordered]@{
                Dominio            = if ($dom) { $dom } else { "TOTAL" }
                Respuestas         = & $contar $todasR $dom ""
                Completas          = & $contar $todasR $dom "COMPLETO"
                Parciales          = & $contar $todasR $dom "PARCIAL"
                "Revisión manual"  = & $contar $todasM $dom ""
                "Automáticas"      = & $contar $todasA $dom ""
                Pendientes         = & $contar $pendientes $dom ""
            } })
    # Se escribe en un archivo temporal y despues se sustituye: si algo falla, el Excel anterior queda intacto
    $tmp = Join-Path $CarpetaResultados ("~tmp_{0}.xlsx" -f [guid]::NewGuid().ToString("N"))
    $p = @{ Path = $tmp; AutoSize = $true; FreezeTopRow = $true; BoldTopRow = $true; NoNumberConversion = "*" }   # sin convertir "0123" en 123
    $resumen | Export-Excel @p -WorksheetName $HOJA_RESUMEN -TableName "Resumen" -TableStyle Medium6
    if ($todasR.Count) { $todasR | Export-Excel @p -WorksheetName $HOJA_RESP -TableName "Respuestas" -TableStyle Medium2 }
    if ($todasM.Count) { $todasM | Export-Excel @p -WorksheetName $HOJA_MANUAL -TableName "RevisionManual" -TableStyle Medium3 }
    if ($todasA.Count) { $todasA | Export-Excel @p -WorksheetName $HOJA_AUTO -TableName "Automaticas" -TableStyle Medium4 }
    if ($pendientes.Count) { $pendientes | Export-Excel @p -WorksheetName $HOJA_PEND -TableName "Pendientes" -TableStyle Medium7 }
    $pkg = Open-ExcelPackage -Path $tmp
    try {
        foreach ($ws in $pkg.Workbook.Worksheets) {
            if (-not $ws.Dimension) { continue }
            for ($c = 1; $c -le $ws.Dimension.End.Column; $c++) { if ($ws.Column($c).Width -gt 60) { $ws.Column($c).Width = 60 } }
        }
    } finally { Close-ExcelPackage $pkg }
    Move-Item -LiteralPath $tmp -Destination $Excel -Force
    $escrito = $true
    Write-Ok $Excel
}
catch {
    Write-Fallo $_.Exception.Message
    if ($tmp -and (Test-Path -LiteralPath $tmp)) { Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue }
}
finally {
    Write-Paso "Resumen"
    Write-Host "  Nuevas en esta ejecucion: $($nuevasR.Count) respuestas, $($nuevasM.Count) a revisar, $($nuevasA.Count) automaticas o rebotes"
    if ($Destinatarios) { Write-Host "  Sin respuesta: $($pendientes.Count)" }
    if ($escrito) { Write-Host "  Excel: $Excel" }
}
