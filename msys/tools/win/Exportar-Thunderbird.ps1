#Requires -Version 5.1
<#
.SYNOPSIS
    Diagnostico y exportacion de Thunderbird antes de pasar a Outlook: contactos, calendario, correo que solo esta en el
    equipo (carpetas locales y cuentas POP) y firmas. Se ejecuta en el equipo, con la sesion del usuario.

.DESCRIPTION
    Por cada perfil de Thunderbird del usuario:
      1. Cierra Thunderbird (los .sqlite quedan bloqueados si sigue abierto); pregunta antes salvo -SinConfirmar.
      2. Contactos: localiza abook*.sqlite, verifica el esquema real (no lo asume) y, si es el de Thunderbird 78 o
         posterior (tablas cards/properties), exporta a CSV. Si no coincide, muestra el esquema y lo deja pendiente.
      3. Calendario: cuenta los eventos de calendar-data\local.sqlite y, segun -Calendario, intenta una extraccion
         aproximada, lo marca para exportar a mano (.ics) o lo salta.
      4. Correo local: las carpetas de Mail\ (Carpetas locales y cuentas POP) solo existen en este equipo. Segun
         -CorreoLocal convierte cada carpeta mbox en mensajes .eml sueltos, que Outlook importa arrastrandolos a una
         carpeta. Las cuentas IMAP (ImapMail\) estan en el servidor: solo se informa de su tamaño.
      5. Firmas: lee prefs.js, desescapa el texto y deja un archivo por identidad (HTML o texto) para pegar en Outlook.
    No toca Outlook ni desinstala nada.
    Necesita sqlite3.exe (sqlite.org, «sqlite-tools-win-x64») junto al script para contactos y calendario; sin el,
    se exportan igualmente el correo local y las firmas.

.PARAMETER SoloDiagnostico
    No cierra Thunderbird ni escribe nada: solo informa de lo que hay (perfiles, tamaños, archivos).
#>

[CmdletBinding()]
param(
    [switch]$SoloDiagnostico,
    [ValidateSet("Preguntar", "Aproximado", "Manual", "Saltar")][string]$Calendario = "Preguntar",
    [ValidateSet("Preguntar", "Convertir", "NoConvertir")][string]$CorreoLocal = "Preguntar",
    [switch]$SinConfirmar,
    [string]$CarpetaSalida
)

# ============================================================
# CONFIGURACION
# ============================================================

$Equipo           = $env:COMPUTERNAME
$RutaSqlite3      = Join-Path $PSScriptRoot "sqlite3.exe"
if (-not $CarpetaSalida) { $CarpetaSalida = if ($PSScriptRoot) { Join-Path $PSScriptRoot "Resultados" } else { "C:\Temp\ExportarThunderbird" } }
$CarpetaSalida    = Join-Path $CarpetaSalida $Equipo
$CarpetaTemp      = Join-Path $CarpetaSalida "_temp_copias"
$FicheroLog       = Join-Path $CarpetaSalida "diagnostico_$(Get-Date -Format 'yyyyMMdd_HHmmss').log"

# Campos de contacto que se intentan mapear a columnas propias. Cualquier otra
# propiedad encontrada en el sqlite se vuelca igualmente, en "OtrosCampos", para
# no perder informacion que no estaba prevista aqui.
$CamposContactoConocidos = @(
    "PrimaryEmail", "SecondEmail", "FirstName", "LastName", "DisplayName",
    "WorkPhone", "HomePhone", "CellularNumber", "Company"
)

# ============================================================
# LOGGING
# ============================================================

function Write-Linea {
    param(
        [string]$Mensaje = "",
        [ValidateSet("INFO", "OK", "AVISO", "ERROR", "PREGUNTA", "TITULO")]
        [string]$Nivel = "INFO"
    )
    $linea = "{0} [{1}] {2}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Nivel, $Mensaje

    switch ($Nivel) {
        "OK"       { Write-Host $Mensaje -ForegroundColor Green }
        "AVISO"    { Write-Host $Mensaje -ForegroundColor Yellow }
        "ERROR"    { Write-Host $Mensaje -ForegroundColor Red }
        "PREGUNTA" { Write-Host $Mensaje -ForegroundColor Cyan }
        "TITULO"   { Write-Host ""; Write-Host $Mensaje -ForegroundColor Cyan; Write-Host ("-" * $Mensaje.Length) -ForegroundColor Cyan }
        default    { Write-Host $Mensaje }
    }

    if (-not $SoloDiagnostico) {
        try { Add-Content -Path $FicheroLog -Value $linea -Encoding UTF8 -ErrorAction Stop } catch { }
    }
}

# ============================================================
# UTILIDADES SQLITE
# ============================================================

function Invoke-Sqlite {
    <#
        Ejecuta un script SQL (puede incluir comandos de punto, .mode etc.)
        contra un fichero sqlite y devuelve la salida como texto.
    #>
    param(
        [string]$RutaBaseDatos,
        [string]$Script
    )
    $temporalSql = [System.IO.Path]::GetTempFileName()
    try {
        Set-Content -Path $temporalSql -Value $Script -Encoding ASCII
        $salida = & $RutaSqlite3 $RutaBaseDatos ".read `"$temporalSql`"" 2>&1
        return ($salida -join "`n")
    }
    finally {
        Remove-Item -Path $temporalSql -Force -ErrorAction SilentlyContinue
    }
}

function Get-TablasSqlite {
    param([string]$RutaBaseDatos)
    $salida = Invoke-Sqlite -RutaBaseDatos $RutaBaseDatos -Script ".tables"
    # .tables separa por espacios/columnas, no por saltos de linea
    return ($salida -split '\s+' | Where-Object { $_ -ne "" })
}

function Get-ColumnasSqlite {
    param([string]$RutaBaseDatos, [string]$Tabla)
    $script = @"
.mode csv
.headers on
SELECT name FROM pragma_table_info('$Tabla');
"@
    $salida = Invoke-Sqlite -RutaBaseDatos $RutaBaseDatos -Script $script
    if (-not $salida) { return @() }
    return ($salida -split "`n" | Select-Object -Skip 1 | Where-Object { $_ -ne "" })
}

# ============================================================
# CIERRE FORZADO DE THUNDERBIRD
# ============================================================

function Stop-ThunderbirdSiEstaAbierto {
    $procesos = Get-Process -Name "thunderbird" -ErrorAction SilentlyContinue
    if (-not $procesos) {
        Write-Linea "Thunderbird no esta en ejecucion." "OK"
        return
    }

    Write-Linea "Thunderbird esta abierto. Cerrando para poder leer los .sqlite..." "AVISO"
    $procesos | Stop-Process -Force -ErrorAction SilentlyContinue

    # Los .sqlite pueden tardar un instante en liberarse tras matar el proceso.
    for ($intento = 1; $intento -le 5; $intento++) {
        Start-Sleep -Seconds 2
        if (-not (Get-Process -Name "thunderbird" -ErrorAction SilentlyContinue)) {
            Write-Linea "Thunderbird cerrado." "OK"
            return
        }
    }
    Write-Linea "Thunderbird no se ha cerrado tras varios intentos. Los .sqlite pueden salir bloqueados." "ERROR"
}

# ============================================================
# LOCALIZAR PERFILES
# ============================================================

function Get-PerfilesThunderbird {
    $rutaIni = Join-Path $env:APPDATA "Thunderbird\profiles.ini"
    if (-not (Test-Path $rutaIni)) {
        return @()
    }

    $carpetaBase = Split-Path $rutaIni -Parent
    $perfiles    = @()
    $actual      = $null

    foreach ($linea in (Get-Content -Path $rutaIni -Encoding UTF8)) {
        $l = $linea.Trim()

        if ($l -match '^\[(.+)\]$') {
            if ($actual -and $actual.Path) { $perfiles += $actual }
            $actual = [ordered]@{ Seccion = $Matches[1]; Path = $null; IsRelative = $true; Nombre = $null }
            continue
        }
        if (-not $actual) { continue }

        if ($l -match '^Path=(.*)$')           { $actual.Path       = $Matches[1] }
        elseif ($l -match '^IsRelative=(\d)$') { $actual.IsRelative = ($Matches[1] -eq '1') }
        elseif ($l -match '^Name=(.*)$')       { $actual.Nombre     = $Matches[1] }
    }
    if ($actual -and $actual.Path) { $perfiles += $actual }

    foreach ($p in $perfiles) {
        if ($p.IsRelative) {
            $p.RutaAbsoluta = Join-Path $carpetaBase $p.Path
        }
        else {
            $p.RutaAbsoluta = $p.Path
        }
    }

    return $perfiles
}

function Get-VersionThunderbird {
    param([string]$RutaPerfil)
    $compat = Join-Path $RutaPerfil "compatibility.ini"
    if (-not (Test-Path $compat)) { return $null }

    $linea = Get-Content -Path $compat -Encoding UTF8 | Where-Object { $_ -match '^LastVersion=' }
    if (-not $linea) { return $null }

    $valor = ($linea -split '=', 2)[1]
    return ($valor -split '/')[0]
}

# ============================================================
# CONTACTOS
# ============================================================

function Export-Contactos {
    param([string]$RutaPerfil, [string]$CarpetaSalidaPerfil)

    $ficherosAbook = Get-ChildItem -Path $RutaPerfil -Filter "abook*.sqlite" -File -ErrorAction SilentlyContinue
    if (-not $ficherosAbook) {
        Write-Linea "No se encontro ningun abook*.sqlite en este perfil." "AVISO"
        return
    }

    foreach ($fichero in $ficherosAbook) {
        Write-Linea "Agenda encontrada: $($fichero.Name)" "INFO"

        if ($SoloDiagnostico) {
            Write-Linea "  [SOLO DIAGNOSTICO] no se abre ni se copia." "AVISO"
            continue
        }

        $copiaLocal = Join-Path $CarpetaTemp $fichero.Name
        Copy-Item -Path $fichero.FullName -Destination $copiaLocal -Force

        $tablas = Get-TablasSqlite -RutaBaseDatos $copiaLocal
        Write-Linea "  Tablas encontradas: $($tablas -join ', ')" "INFO"

        if ($tablas -notcontains "cards" -or $tablas -notcontains "properties") {
            Write-Linea "  El esquema NO coincide con el patron esperado (cards/properties)." "ERROR"
            Write-Linea "  Esquema completo, para revision manual:" "AVISO"
            Write-Host (Invoke-Sqlite -RutaBaseDatos $copiaLocal -Script ".schema")
            $rutaAviso = Join-Path $CarpetaSalidaPerfil "$($fichero.BaseName)_PENDIENTE_REVISION_MANUAL.txt"
            Invoke-Sqlite -RutaBaseDatos $copiaLocal -Script ".schema" | Set-Content -Path $rutaAviso -Encoding UTF8
            continue
        }

        # Verificacion de columnas reales antes de dar por buena la consulta.
        $columnasProperties = Get-ColumnasSqlite -RutaBaseDatos $copiaLocal -Tabla "properties"
        $faltan = @("card", "name", "value") | Where-Object { $columnasProperties -notcontains $_ }
        if ($faltan) {
            Write-Linea "  La tabla 'properties' no tiene las columnas esperadas (faltan: $($faltan -join ', '))." "ERROR"
            Write-Linea "  Columnas reales: $($columnasProperties -join ', ')" "AVISO"
            continue
        }

        Write-Linea "  Esquema verificado. Extrayendo..." "OK"

        $script = @"
.mode csv
.headers on
SELECT card AS Card, name AS Nombre, value AS Valor FROM properties;
"@
        $salidaCsv = Invoke-Sqlite -RutaBaseDatos $copiaLocal -Script $script
        if (-not $salidaCsv) {
            Write-Linea "  La consulta no devolvio filas (agenda vacia o sin propiedades)." "AVISO"
            continue
        }

        $filas = $salidaCsv | ConvertFrom-Csv

        # Pivote: de (card, nombre, valor) por fila a una fila por tarjeta.
        $tarjetas = [ordered]@{}
        foreach ($fila in $filas) {
            if (-not $tarjetas.Contains($fila.Card)) {
                $tarjetas[$fila.Card] = @{}
            }
            $tarjetas[$fila.Card][$fila.Nombre] = $fila.Valor
        }

        $filasSalida = foreach ($uid in $tarjetas.Keys) {
            $propiedades = $tarjetas[$uid]
            $obj = [ordered]@{ Uid = $uid }
            foreach ($campo in $CamposContactoConocidos) {
                $obj[$campo] = $propiedades[$campo]
            }
            $otros = $propiedades.Keys | Where-Object { $CamposContactoConocidos -notcontains $_ }
            $obj["OtrosCampos"] = ($otros | ForEach-Object { "$_=$($propiedades[$_])" }) -join "; "
            [PSCustomObject]$obj
        }

        $rutaCsv = Join-Path $CarpetaSalidaPerfil "$($fichero.BaseName)_contactos.csv"
        $filasSalida | Export-Csv -Path $rutaCsv -NoTypeInformation -Encoding UTF8

        Write-Linea "  $($filasSalida.Count) contactos exportados -> $rutaCsv" "OK"
    }
}

# ============================================================
# CALENDARIO
# ============================================================

function Export-Calendario {
    param([string]$RutaPerfil, [string]$CarpetaSalidaPerfil)

    $rutaCalendario = Join-Path $RutaPerfil "calendar-data\local.sqlite"
    if (-not (Test-Path $rutaCalendario)) {
        Write-Linea "No hay calendar-data\local.sqlite en este perfil (sin calendario local)." "INFO"
        return
    }

    if ($SoloDiagnostico) {
        $tamanoMB = [math]::Round((Get-Item $rutaCalendario).Length / 1MB, 2)
        Write-Linea "Calendario encontrado ($tamanoMB MB). [SOLO DIAGNOSTICO] no se abre." "AVISO"
        return
    }

    $copiaLocal = Join-Path $CarpetaTemp "local_calendario.sqlite"
    Copy-Item -Path $rutaCalendario -Destination $copiaLocal -Force

    $tablas = Get-TablasSqlite -RutaBaseDatos $copiaLocal
    if ($tablas -notcontains "cal_events") {
        Write-Linea "El calendario no tiene la tabla 'cal_events' esperada. Tablas reales: $($tablas -join ', ')" "ERROR"
        return
    }

    $conteo = Invoke-Sqlite -RutaBaseDatos $copiaLocal -Script "SELECT COUNT(*) FROM cal_events;"
    Write-Linea "Eventos encontrados en cal_events: $conteo" "INFO"

    $columnas = Get-ColumnasSqlite -RutaBaseDatos $copiaLocal -Tabla "cal_events"
    Write-Linea "Columnas reales de cal_events: $($columnas -join ', ')" "INFO"

    if ([int]$conteo -eq 0) {
        Write-Linea "Sin eventos. No hace falta decidir nada aqui." "OK"
        return
    }

    Write-Linea "" 
    Write-Linea "Decide como proceder con este calendario:" "PREGUNTA"
    Write-Linea "  [A] Intentar extraccion automatica (best-effort, columnas title/event_start/event_end)" "PREGUNTA"
    Write-Linea "  [M] Marcar para exportacion MANUAL desde Thunderbird (File > Export Calendar)" "PREGUNTA"
    Write-Linea "  [S] Saltar (no relevante para esta cuenta)" "PREGUNTA"
    $respuesta = switch ($Calendario) { "Aproximado" { "A" } "Manual" { "M" } "Saltar" { "S" } default { Read-Host "Opcion (A/M/S)" } }

    switch ($respuesta.ToUpper()) {
        "A" {
            $faltan = @("title", "event_start", "event_end") | Where-Object { $columnas -notcontains $_ }
            if ($faltan) {
                Write-Linea "No estan las columnas necesarias para el best-effort (faltan: $($faltan -join ', '))." "ERROR"
                Write-Linea "Se marca como pendiente de revision manual." "AVISO"
                "Pendiente de revision manual - columnas reales: $($columnas -join ', ')" |
                    Set-Content -Path (Join-Path $CarpetaSalidaPerfil "calendario_PENDIENTE.txt") -Encoding UTF8
                return
            }
            try {
                $script = @"
.mode csv
.headers on
SELECT title AS Titulo, event_start AS Inicio, event_end AS Fin FROM cal_events;
"@
                $salida = Invoke-Sqlite -RutaBaseDatos $copiaLocal -Script $script
                $rutaCsv = Join-Path $CarpetaSalidaPerfil "calendario_BEST_EFFORT.csv"
                $salida | Set-Content -Path $rutaCsv -Encoding UTF8
                Write-Linea "Extraccion best-effort guardada en $rutaCsv. VERIFICA los datos antes de usarlos: los timestamps de cal_events suelen venir en microsegundos epoch, no en fecha legible." "AVISO"
            }
            catch {
                Write-Linea "Fallo la extraccion best-effort: $($_.Exception.Message)" "ERROR"
            }
        }
        "M" {
            "Exportacion manual pendiente: Thunderbird > boton derecho sobre el calendario > Exportar calendario > .ics" |
                Set-Content -Path (Join-Path $CarpetaSalidaPerfil "calendario_PENDIENTE_MANUAL.txt") -Encoding UTF8
            Write-Linea "Marcado como pendiente de exportacion manual." "OK"
        }
        default {
            Write-Linea "Calendario omitido para esta cuenta." "INFO"
        }
    }
}

# ============================================================
# UTILIDADES DE CONVERSION (firmas y correo local)
# ============================================================

function ConvertFrom-JsString {
    <#
        Desescapa una cadena tal como aparece en prefs.js (formato JS):
        \n, \r, \t, \" y \\ . Recorrido caracter a caracter para no
        desescapar dos veces por orden de sustitucion (un regex encadenado
        se equivoca si el texto original ya contiene una barra invertida).
    #>
    param([string]$Valor)

    $v = $Valor.Trim()
    if ($v.Length -ge 2 -and $v.StartsWith('"') -and $v.EndsWith('"')) {
        $v = $v.Substring(1, $v.Length - 2)
    }

    $sb = New-Object System.Text.StringBuilder
    $i = 0
    while ($i -lt $v.Length) {
        if ($v[$i] -eq '\' -and ($i + 1) -lt $v.Length) {
            switch ($v[$i + 1]) {
                'n'       { [void]$sb.Append("`n"); $i += 2; continue }
                'r'       { [void]$sb.Append("`r"); $i += 2; continue }
                't'       { [void]$sb.Append("`t"); $i += 2; continue }
                '"'       { [void]$sb.Append('"');  $i += 2; continue }
                '\'       { [void]$sb.Append('\');  $i += 2; continue }
                default   { [void]$sb.Append($v[$i]); $i += 1; continue }
            }
        }
        [void]$sb.Append($v[$i])
        $i += 1
    }
    return $sb.ToString()
}

function ConvertTo-EmlDesdeMbox {
    <#
        Divide un fichero mbox (una carpeta de Thunderbird Local Folders)
        en mensajes .eml sueltos. Lee como ISO-8859-1 (mapeo 1:1 con los
        bytes originales) para no arriesgar la integridad de adjuntos ni
        de texto en cualquier codificacion real, y escribe de vuelta con
        la misma codificacion.

        LIMITACION CONOCIDA: si el cuerpo de algun mensaje tenia una linea
        que empezaba literalmente por "From " en el texto original, mbox
        la escapa anteponiendo ">" al guardarla. Este conversor no revierte
        ese escapado (es ambiguo sin conocer la profundidad de cita real):
        puede quedar un ">" de mas en esas lineas concretas del cuerpo.
    #>
    param(
        [string]$RutaMbox,
        [string]$CarpetaDestino
    )

    if (-not (Test-Path $RutaMbox)) { return 0 }
    if ((Get-Item $RutaMbox).Length -eq 0) { return 0 }

    $bytes    = [System.IO.File]::ReadAllBytes($RutaMbox)
    $enc      = [System.Text.Encoding]::GetEncoding("ISO-8859-1")
    $contenido = $enc.GetString($bytes)

    # Separador real de mensaje: linea que empieza por "From " al inicio
    # de linea, sin ">" delante (las escapadas del cuerpo si lo llevan).
    $coincidencias = [regex]::Matches($contenido, '(?m)^From .*$')
    if ($coincidencias.Count -eq 0) { return 0 }

    if (-not (Test-Path $CarpetaDestino)) {
        New-Item -ItemType Directory -Force -Path $CarpetaDestino | Out-Null
    }

    $numeroMensaje = 0

    for ($i = 0; $i -lt $coincidencias.Count; $i++) {
        $inicio = $coincidencias[$i].Index
        $fin    = if (($i + 1) -lt $coincidencias.Count) { $coincidencias[$i + 1].Index } else { $contenido.Length }
        $bloque = $contenido.Substring($inicio, $fin - $inicio)

        # Se quita la propia linea "From ..." (separador de mbox: no es
        # parte del mensaje RFC822, Outlook/Exchange no la esperan).
        $saltoLinea = $bloque.IndexOf("`n")
        $mensaje = if ($saltoLinea -ge 0) { $bloque.Substring($saltoLinea + 1) } else { "" }
        if ([string]::IsNullOrWhiteSpace($mensaje)) { continue }

        $numeroMensaje++

        # Asunto/fecha solo para nombrar el fichero. El contenido se
        # escribe integro, no se reprocesa.
        $asunto = "sin_asunto"
        $mAsunto = [regex]::Match($mensaje, '(?im)^Subject:\s*(.*)$')
        if ($mAsunto.Success -and $mAsunto.Groups[1].Value.Trim()) {
            $limpio = [regex]::Replace($mAsunto.Groups[1].Value.Trim(), '[\\/:*?"<>|\r\n\t]', '_')
            $limpio = [regex]::Replace($limpio, '\s+', ' ').Trim()
            if ($limpio.Length -gt 60) { $limpio = $limpio.Substring(0, 60) }
            if ($limpio) { $asunto = $limpio }
        }

        $fecha = "sin_fecha"
        $mFecha = [regex]::Match($mensaje, '(?im)^Date:\s*(.*)$')
        if ($mFecha.Success) {
            try   { $fecha = ([datetime]::Parse($mFecha.Groups[1].Value.Trim())).ToString("yyyy-MM-dd") }
            catch { $fecha = "sin_fecha" }
        }

        $nombreFichero = "{0}_{1}_{2:D5}.eml" -f $fecha, $asunto, $numeroMensaje
        $rutaFichero   = Join-Path $CarpetaDestino $nombreFichero

        [System.IO.File]::WriteAllText($rutaFichero, $mensaje, $enc)
    }

    return $numeroMensaje
}

# ============================================================
# LOCAL FOLDERS
# ============================================================

function Revisar-LocalFolders {
    param([string]$RutaPerfil, [string]$CarpetaSalidaPerfil, [string]$RutaCuenta)

    $rutaLocal = $RutaCuenta
    $nombreCuenta = Split-Path $RutaCuenta -Leaf
    if (-not (Test-Path $rutaLocal)) { return }

    # Cada carpeta (Inbox, Trash, o cualquier carpeta personalizada) es un
    # fichero mbox sin extension. Las subcarpetas viven en "<nombre>.sbd".
    # Se descartan los .msf (indice, no es mbox) y .dat/.log auxiliares.
    $ficherosMbox = Get-ChildItem -Path $rutaLocal -File -Recurse -ErrorAction SilentlyContinue |
                    Where-Object { $_.Extension -notin @(".msf", ".dat", ".log") }

    $tamanoTotal = ($ficherosMbox | Measure-Object -Property Length -Sum).Sum
    $tamanoMB = [math]::Round($tamanoTotal / 1MB, 2)

    Write-Linea "Correo local '$nombreCuenta': $($ficherosMbox.Count) carpeta(s) mbox, $tamanoMB MB en total." "AVISO"
    foreach ($f in ($ficherosMbox | Where-Object { $_.Length -gt 10KB } | Sort-Object Length -Descending)) {
        $mb = [math]::Round($f.Length / 1MB, 2)
        Write-Linea "  $($f.Name): $mb MB" "INFO"
    }

    if (-not $tamanoTotal) {
        Write-Linea "Carpetas vacías: no hay nada que exportar." "OK"
        return
    }

    if ($SoloDiagnostico) {
        Write-Linea "[SOLO DIAGNOSTICO] no se convierte nada." "AVISO"
        return
    }

    Write-Linea ""
    Write-Linea "Este correo solo esta en este equipo (no en ningun servidor): si no se exporta, se pierde al quitar Thunderbird." "PREGUNTA"
    $respuesta = switch ($CorreoLocal) { "Convertir" { "S" } "NoConvertir" { "N" } default { Read-Host "Convertir '$nombreCuenta' a .eml sueltos para poder importarlos despues? (S/N)" } }
    if ($respuesta -notmatch '^[Ss]') {
        Write-Linea "No convertido. Queda constancia en el log de que existia ($tamanoMB MB)." "AVISO"
        return
    }

    $carpetaEml    = Join-Path (Join-Path $CarpetaSalidaPerfil "Correo_EML") $nombreCuenta
    $totalMensajes = 0

    foreach ($fichero in ($ficherosMbox | Where-Object { $_.Length -gt 0 })) {
        # Ruta relativa dentro de Local Folders (incluye las .sbd de
        # subcarpetas), para no mezclar en un mismo sitio mensajes de
        # carpetas distintas.
        $relativa       = $fichero.FullName.Substring($rutaLocal.Length).TrimStart('\')
        $carpetaPadre   = Split-Path $relativa -Parent
        $destinoCarpeta = if ($carpetaPadre) { Join-Path $carpetaEml $carpetaPadre } else { $carpetaEml }
        $destinoCarpeta = Join-Path $destinoCarpeta $fichero.Name

        $convertidos = ConvertTo-EmlDesdeMbox -RutaMbox $fichero.FullName -CarpetaDestino $destinoCarpeta
        if ($convertidos -gt 0) {
            Write-Linea "  $($fichero.Name): $convertidos mensaje(s) -> $destinoCarpeta" "OK"
            $totalMensajes += $convertidos
        }
    }

    Write-Linea "Total convertido: $totalMensajes mensajes en $carpetaEml" "OK"
    Write-Linea "Para importarlos: en Outlook, arrastrar los .eml de cada carpeta a la carpeta del buzon que corresponda." "INFO"
    Write-Linea "AVISO: lineas de cuerpo que empezaban literalmente por 'From ' pueden llevar un '>' de mas (escapado mbox no revertido, ver cabecera de la funcion)." "AVISO"
}

# ============================================================
# FIRMAS
# ============================================================

function Export-Firmas {
    <#
        Parsea de verdad las lineas mail.identity.idN.* de prefs.js:
        desescapa la cadena JS (\n, \r, \", \\) y separa segun
        htmlSigFormat si el contenido es HTML o texto plano, dejando un
        fichero por identidad listo para copiar/pegar en Outlook
        (Archivo > Opciones > Correo > Firmas). No hay importacion
        automatica de firma en Outlook: es un paso manual de todos modos,
        pero al menos el contenido ya esta limpio y legible.
    #>
    param([string]$RutaPerfil, [string]$CarpetaSalidaPerfil)

    $prefsJs = Join-Path $RutaPerfil "prefs.js"
    if (-not (Test-Path $prefsJs)) { return }

    $identidades = @{}

    foreach ($linea in (Get-Content -Path $prefsJs -Encoding UTF8)) {
        $m = [regex]::Match($linea, '^user_pref\("mail\.identity\.(id\d+)\.(htmlSigText|sig_file|htmlSigFormat)",\s*(.*)\);\s*$')
        if (-not $m.Success) { continue }

        $idIdentidad = $m.Groups[1].Value
        $propiedad   = $m.Groups[2].Value
        $valorCrudo  = $m.Groups[3].Value

        if (-not $identidades.ContainsKey($idIdentidad)) { $identidades[$idIdentidad] = @{} }

        if ($propiedad -eq "htmlSigFormat") {
            $identidades[$idIdentidad][$propiedad] = ($valorCrudo.Trim() -eq "true")
        }
        else {
            $identidades[$idIdentidad][$propiedad] = ConvertFrom-JsString -Valor $valorCrudo
        }
    }

    if ($identidades.Count -eq 0) {
        Write-Linea "No se encontraron firmas configuradas en prefs.js." "INFO"
        return
    }

    if ($SoloDiagnostico) {
        Write-Linea "Firmas detectadas para $($identidades.Count) identidad(es). [SOLO DIAGNOSTICO] no se escribe nada." "AVISO"
        return
    }

    foreach ($idIdentidad in $identidades.Keys) {
        $datos = $identidades[$idIdentidad]
        $texto = $datos["htmlSigText"]

        if ([string]::IsNullOrWhiteSpace($texto)) {
            if ($datos.ContainsKey("sig_file") -and -not [string]::IsNullOrWhiteSpace($datos["sig_file"])) {
                Write-Linea "Identidad ${idIdentidad}: usa fichero de firma externo ($($datos['sig_file'])), no una firma en texto. Copialo a mano." "AVISO"
            }
            continue
        }

        $esHtml    = if ($datos.ContainsKey("htmlSigFormat")) { [bool]$datos["htmlSigFormat"] } else { $false }
        $extension = if ($esHtml) { "html" } else { "txt" }
        $rutaFirma = Join-Path $CarpetaSalidaPerfil "firma_$idIdentidad.$extension"

        if ($esHtml) {
            # Envuelto en HTML minimo para poder abrirlo directamente en el navegador/Word y copiar con formato.
            Set-Content -Path $rutaFirma -Value "<html><body>$texto</body></html>" -Encoding UTF8
        }
        else {
            Set-Content -Path $rutaFirma -Value $texto -Encoding UTF8
        }

        Write-Linea "Firma de ${idIdentidad} exportada (formato $(if ($esHtml) { 'HTML' } else { 'texto plano' })) -> $rutaFirma" "OK"
    }
}

# ============================================================
# INICIO
# ============================================================

Write-Linea "============================================" "TITULO"
Write-Linea " EXPORTACION DE THUNDERBIRD - $Equipo"
if ($SoloDiagnostico) {
    Write-Linea "*** MODO SOLO DIAGNOSTICO - no se cierra Thunderbird ni se escribe nada ***" "AVISO"
}
Write-Linea "============================================" "TITULO"

$haySqlite = Test-Path $RutaSqlite3
if (-not $haySqlite) {
    Write-Linea "No se encuentra sqlite3.exe junto al script: se omiten contactos y calendario (correo local y firmas si se exportan)." "AVISO"
    Write-Linea "Descarga «sqlite-tools-win-x64» de sqlite.org y deja sqlite3.exe junto al script para exportarlos." "AVISO"
}

# Deteccion de Thunderbird instalado (registro, ambas ramas por si es 32/64 bits)
$rutasRegistro = @(
    "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
)
$instalado = Get-ItemProperty -Path $rutasRegistro -ErrorAction SilentlyContinue |
             Where-Object { $_.DisplayName -like "*Thunderbird*" }

if (-not $instalado -and -not (Test-Path (Join-Path $env:APPDATA "Thunderbird\profiles.ini"))) {
    Write-Linea "Thunderbird no aparece instalado ni hay perfiles de este usuario. Nada que exportar aqui." "AVISO"
    exit 0
}

if (-not $SoloDiagnostico) {
    if (-not (Test-Path $CarpetaSalida)) { New-Item -ItemType Directory -Force -Path $CarpetaSalida | Out-Null }
    if (-not (Test-Path $CarpetaTemp))   { New-Item -ItemType Directory -Force -Path $CarpetaTemp   | Out-Null }
}

if (-not $SoloDiagnostico) {
    if ((Get-Process -Name "thunderbird" -ErrorAction SilentlyContinue) -and -not $SinConfirmar) {
        if ((Read-Host "Thunderbird esta abierto y hay que cerrarlo. ¿Cerrarlo ahora? (S/N)") -notmatch '^(s|si|sí)$') { Write-Linea "Cancelado: cierra Thunderbird y vuelve a lanzar." "AVISO"; exit 1 }
    }
    Stop-ThunderbirdSiEstaAbierto
}

$perfiles = Get-PerfilesThunderbird
if (-not $perfiles) {
    Write-Linea "No se encontro profiles.ini o no tiene perfiles validos." "ERROR"
    exit 1
}

Write-Linea "Perfiles encontrados: $($perfiles.Count)" "INFO"

foreach ($perfil in $perfiles) {
    Write-Linea "============================================" "TITULO"
    Write-Linea " PERFIL: $($perfil.Nombre)  [$($perfil.RutaAbsoluta)]"
    Write-Linea "============================================" "TITULO"

    if (-not (Test-Path $perfil.RutaAbsoluta)) {
        Write-Linea "La ruta del perfil no existe en disco. Omitido." "ERROR"
        continue
    }

    $version = Get-VersionThunderbird -RutaPerfil $perfil.RutaAbsoluta
    if ($version) {
        Write-Linea "Version detectada: $version" "INFO"
        # Comparacion simple del numero mayor. Version 78+ usa SQLite; anterior, Mork (.mab).
        $versionMayor = 0
        [void][int]::TryParse(($version -split '\.')[0], [ref]$versionMayor)
        if ($versionMayor -gt 0 -and $versionMayor -lt 78) {
            Write-Linea "Version anterior a 78: la agenda probablemente esta en formato Mork (.mab), no SQLite." "AVISO"
            Write-Linea "Este script no procesa Mork. Requiere herramienta distinta si se confirma el formato." "AVISO"
        }
    }
    else {
        Write-Linea "No se pudo determinar la version (sin compatibility.ini). Se continua con cautela." "AVISO"
    }

    $carpetaSalidaPerfil = if ($SoloDiagnostico) { $null } else { Join-Path $CarpetaSalida $perfil.Nombre }
    if ($carpetaSalidaPerfil -and -not (Test-Path $carpetaSalidaPerfil)) {
        New-Item -ItemType Directory -Force -Path $carpetaSalidaPerfil | Out-Null
    }

    Write-Linea "" 
    Write-Linea "--- Contactos ---" "TITULO"
    if ($haySqlite) { Export-Contactos -RutaPerfil $perfil.RutaAbsoluta -CarpetaSalidaPerfil $carpetaSalidaPerfil } else { Write-Linea "Omitido (sin sqlite3.exe)." "AVISO" }

    Write-Linea ""
    Write-Linea "--- Calendario ---" "TITULO"
    if ($haySqlite) { Export-Calendario -RutaPerfil $perfil.RutaAbsoluta -CarpetaSalidaPerfil $carpetaSalidaPerfil } else { Write-Linea "Omitido (sin sqlite3.exe)." "AVISO" }

    Write-Linea ""
    Write-Linea "--- Correo local (Carpetas locales y cuentas POP) ---" "TITULO"
    $cuentasLocales = @(Get-ChildItem -Path (Join-Path $perfil.RutaAbsoluta "Mail") -Directory -ErrorAction SilentlyContinue)
    if (-not $cuentasLocales.Count) { Write-Linea "Sin correo local en este perfil." "INFO" }
    foreach ($cuenta in $cuentasLocales) { Revisar-LocalFolders -RutaPerfil $perfil.RutaAbsoluta -CarpetaSalidaPerfil $carpetaSalidaPerfil -RutaCuenta $cuenta.FullName }
    foreach ($imap in @(Get-ChildItem -Path (Join-Path $perfil.RutaAbsoluta "ImapMail") -Directory -ErrorAction SilentlyContinue)) {
        $mbImap = [math]::Round(((Get-ChildItem -Path $imap.FullName -File -Recurse -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum) / 1MB, 1)
        Write-Linea "Cuenta IMAP '$($imap.Name)': $mbImap MB en caché; el correo está en el servidor (no hace falta exportarlo)." "INFO"
    }

    Write-Linea ""
    Write-Linea "--- Firmas ---" "TITULO"
    Export-Firmas -RutaPerfil $perfil.RutaAbsoluta -CarpetaSalidaPerfil $carpetaSalidaPerfil
}

Write-Linea ""
Write-Linea "============================================" "TITULO"
Write-Linea " FIN DEL DIAGNOSTICO - $Equipo"
if (-not $SoloDiagnostico) {
    Write-Linea " Salida en: $CarpetaSalida"
}
Write-Linea "============================================" "TITULO"
Write-Linea ""
Write-Linea "Siguiente paso en este equipo: configurar la cuenta en Outlook (Exchange) y" "INFO"
Write-Linea "validar envio/recepcion real ANTES de desinstalar Thunderbird/LibreOffice." "INFO"
