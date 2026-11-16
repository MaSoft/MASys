#Requires -Version 5.1
<#
.SYNOPSIS
    Estado de los lotes de migracion de Exchange Online por cuenta: estado, elementos copiados y omitidos, datos y errores.
    Solo lee.

.DESCRIPTION
    Para los lotes indicados (o todos) muestra cada cuenta con su estado, elementos sincronizados y omitidos, datos
    transferidos, ultima sincronizacion y error. Resume por lote y en total y destaca las que necesitan intervencion.
    Con -Detalle consulta las estadisticas de cada usuario (mas lento, pero es la unica fuente del motivo del error y del
    recuento fiable de omitidos); con -VerOmitidos lista ademas cada elemento omitido (asunto, carpeta, tamano, motivo).

.EXAMPLE
    .\Ver-EstadoMigracionIMAP.ps1 -Lote "Migracion-Lote1","Migracion-Lote2" -Detalle -SoloProblemas
#>

[CmdletBinding()]
param(
    [string[]]$Lote,
    [switch]$Detalle,
    [switch]$SoloProblemas,
    [switch]$VerOmitidos,
    [string]$TenantId,
    [string]$CarpetaResultados
)

$ErrorActionPreference = "Stop"
$CodificacionCsv = if ($PSVersionTable.PSVersion.Major -ge 6) { "utf8BOM" } else { "UTF8" }   # CSV con BOM en 5.1 y en 7: Excel abre bien las tildes
if ($VerOmitidos) { $Detalle = $true }
$EstadosSinProblema = @("Synced", "Completed", "Syncing", "Queued", "Provisioning", "Validating", "Starting", "Completing", "Stopped")

# ============================================================ utilidades comunes
function Write-Paso  ($t) { Write-Host "`n== $t" -ForegroundColor Cyan }
function Write-Ok    ($t) { Write-Host "  [OK]    $t" -ForegroundColor Green }
function Write-Aviso ($t) { Write-Host "  [AVISO] $t" -ForegroundColor Yellow }
function Write-Fallo ($t) { Write-Host "  [ERROR] $t" -ForegroundColor Red }

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
    if ($TenantId -and $con.TenantID -ne $TenantId) { throw "Tenant incorrecto: la sesion es de $($con.TenantID) y se esperaba $TenantId." }
    return $con
}

function Get-Prop {
    <# Primer valor no vacio entre varias propiedades (cambian de nombre segun la version del modulo). #>
    param($Objeto, [string[]]$Nombres)
    foreach ($n in $Nombres) {
        $p = $Objeto.PSObject.Properties[$n]
        if ($p -and $null -ne $p.Value -and "$($p.Value)" -ne "") { return $p.Value }
    }
    return $null
}

function Get-Bytes {
    <# "1.234 GB (1,325,400,064 bytes)" -> 1325400064 #>
    param($Valor)
    if ($null -eq $Valor) { return [int64]0 }
    if ("$Valor" -match '\(([\d.,]+) bytes\)') { return [int64]($Matches[1] -replace '[.,]', '') }
    try { return [int64]$Valor } catch { return [int64]0 }
}

function ConvertTo-Numero($v) {
    <# Los contadores llegan como numero o como cadena ("1.234") segun la version: nunca debe romper el recorrido. #>
    $t = "$v" -replace '[^\d]', ''
    if ($t) { return [int64]$t } else { return [int64]0 }
}

function Format-Tamano([int64]$b) {
    if ($b -ge 1GB) { return "{0:N2} GB" -f ($b / 1GB) }
    if ($b -ge 1MB) { return "{0:N1} MB" -f ($b / 1MB) }
    if ($b -ge 1KB) { return "{0:N0} KB" -f ($b / 1KB) }
    return "$b B"
}

function Get-CorreoMigracion {
    <# En sesiones REST de Exchange Online EmailAddress llega vacio y la direccion esta en Identity. #>
    param($Usuario)
    $d = Get-Prop $Usuario @("EmailAddress", "MailboxEmailAddress")
    if (-not $d) { $d = "$($Usuario.Identity)" }
    return "$d"
}

# ============================================================ inicio
$inicio = Get-Date
if (-not $CarpetaResultados) { $CarpetaResultados = Join-Path $PSScriptRoot "Resultados" }
New-Item -ItemType Directory -Force -Path $CarpetaResultados | Out-Null
$RegCuentas = Join-Path $CarpetaResultados ("EstadoMigracion_{0:yyyyMMdd_HHmmss}.csv" -f $inicio)
$RegOmitidos = Join-Path $CarpetaResultados ("ElementosOmitidos_{0:yyyyMMdd_HHmmss}.csv" -f $inicio)
$filas = New-Object System.Collections.Generic.List[object]
$omitidos = New-Object System.Collections.Generic.List[object]

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " ESTADO DE LA MIGRACION (solo lee)" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

try {
    $con = Connect-ExchangeSeguro -TenantId $TenantId
    Write-Ok "Exchange Online: $($con.UserPrincipalName) (tenant $($con.TenantID))"

    $lotes = if ($Lote) { foreach ($l in $Lote) { Get-MigrationBatch -Identity $l } } else { @(Get-MigrationBatch) }
    $lotes = @($lotes)
    if ($lotes.Count -eq 0) { throw "No hay lotes de migracion$(if ($Lote) { ' con esos nombres' })." }

    foreach ($b in $lotes) {
        $nombre = "$($b.Identity)"
        Write-Paso "Lote $nombre"
        $usuarios = @(Get-MigrationUser -BatchId $nombre -ResultSize Unlimited)
        $n = 0
        foreach ($u in $usuarios) {
            $n++
            $correo = Get-CorreoMigracion $u
            Write-Progress -Activity "Lote $nombre" -Status $correo -PercentComplete (100 * $n / [math]::Max(1, $usuarios.Count))
            $sinc = (ConvertTo-Numero (Get-Prop $u @("SyncedItemCount", "ItemsSynced")))
            $omit = (ConvertTo-Numero (Get-Prop $u @("SkippedItemCount", "ItemsSkipped")))
            $bytes = [int64]0
            $err = "$(Get-Prop $u @('ErrorSummary'))"
            $ultima = Get-Prop $u @("LastSuccessfulSyncTime", "LastSyncTime")
            if ($Detalle) {
                try {
                    $p = @{ Identity = $correo }
                    if ($VerOmitidos) { $p.IncludeSkippedItems = $true }
                    $st = Get-MigrationUserStatistics @p
                    $sinc = (ConvertTo-Numero (Get-Prop $st @("SyncedItemCount", "ItemsSynced")))
                    $omit = (ConvertTo-Numero (Get-Prop $st @("SkippedItemCount", "ItemsSkipped")))
                    $bytes = Get-Bytes (Get-Prop $st @("BytesTransferred", "EstimatedTotalTransferSize"))
                    $e2 = "$(Get-Prop $st @('Error', 'ErrorSummary'))"
                    if ($e2) { $err = $e2 }
                    $u2 = Get-Prop $st @("LastSuccessfulSyncTime"); if ($u2) { $ultima = $u2 }
                    if ($VerOmitidos) {
                        foreach ($s in @($st.SkippedItems)) {
                            $omitidos.Add([PSCustomObject]@{ Lote = $nombre; Correo = $correo; Tipo = "$(Get-Prop $s @('Kind'))"
                                    Carpeta = "$(Get-Prop $s @('FolderName'))"; Asunto = "$(Get-Prop $s @('Subject'))"
                                    Fecha = "$(Get-Prop $s @('DateReceived', 'DateSent'))"; Tamano = (Format-Tamano (Get-Bytes (Get-Prop $s @('MessageSize'))))
                                    Motivo = ("$(Get-Prop $s @('Failure', 'ScoringClassifications'))" -replace "\s*`r?`n\s*", " ") })
                        }
                    }
                } catch { Write-Aviso "$correo : sin estadisticas ($($_.Exception.Message))" }
            }
            $estado = "$($u.Status)"
            $problema = ($estado -notin $EstadosSinProblema) -or $omit -gt 0
            $f = [PSCustomObject]@{
                Lote = $nombre; Correo = $correo; Estado = $estado; Sincronizados = $sinc; Omitidos = $omit
                Datos = $(if ($Detalle) { Format-Tamano $bytes } else { "" })
                UltimaSincronizacion = $(if ($ultima) { ([datetime]$ultima).ToLocalTime().ToString("dd/MM/yyyy HH:mm") } else { "" })
                Revisar = $(if ($problema) { "SI" } else { "" })
                Error = ($err -replace "\s*`r?`n\s*", " ")
            }
            $filas.Add($f)
            if ($SoloProblemas -and -not $problema) { continue }
            $color = if ($estado -eq "Failed") { "Red" } elseif ($problema) { "Yellow" } else { "Green" }
            $linea = "  {0,-42} {1,-11} copiados {2,7}  omitidos {3,5}  {4}" -f $correo, $estado, $sinc, $omit, $f.UltimaSincronizacion
            Write-Host $linea -ForegroundColor $color
            if ($f.Error) { Write-Host ("      " + $(if ($f.Error.Length -gt 180) { $f.Error.Substring(0, 180) + "..." } else { $f.Error })) -ForegroundColor DarkYellow }
        }
        Write-Progress -Activity "Lote $nombre" -Completed
        # El estado del lote llega como numero en sesiones REST: se resume a partir de sus cuentas
        $resumen = ($filas | Where-Object Lote -eq $nombre | Group-Object Estado | ForEach-Object { "$($_.Name): $($_.Count)" }) -join "  |  "
        Write-Host "  Total $($usuarios.Count) cuentas  ->  $resumen" -ForegroundColor Cyan
    }
}
catch {
    Write-Fallo $_.Exception.Message
}
finally {
    if ($filas.Count -gt 0) {
        $filas | Export-Csv -LiteralPath $RegCuentas -NoTypeInformation -Encoding $CodificacionCsv -Delimiter ";"
        Write-Paso "Resumen general"
        Write-Host "  Cuentas: $($filas.Count)   Sincronizadas: $(@($filas | Where-Object { $_.Estado -in 'Synced','Completed' }).Count)   Con fallo: $(@($filas | Where-Object Estado -eq 'Failed').Count)   Con omitidos: $(@($filas | Where-Object { $_.Omitidos -gt 0 }).Count)"
        Write-Host "  Detalle por cuenta: $RegCuentas"
        if (-not $Detalle) { Write-Aviso "Para ver el motivo exacto de los errores y los omitidos reales, repite con -Detalle." }
    }
    if ($omitidos.Count -gt 0) {
        $omitidos | Export-Csv -LiteralPath $RegOmitidos -NoTypeInformation -Encoding $CodificacionCsv -Delimiter ";"
        Write-Host "  Elementos omitidos ($($omitidos.Count)): $RegOmitidos"
    }
}
