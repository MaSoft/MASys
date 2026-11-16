#Requires -Version 5.1
<#
.SYNOPSIS
    Informe de accesos a un equipo Windows desde el registro de Seguridad: inicios y cierres de sesion, fallos,
    escritorio remoto, bloqueos y cambios en cuentas de usuario. Informe HTML autonomo (sin internet) y CSV.
    Solo lee.

.DESCRIPTION
    Lee los eventos 4624, 4625, 4634, 4647, 4648, 4672, 4778, 4779 y 4800-4803 (accesos) y 4720, 4722-4726, 4732,
    4733, 4738, 4740, 4767 y 4781 (gestion de cuentas) del registro de Seguridad del equipo, de sus copias archivadas
    (Archive-Security-*.evtx) o de archivos .evtx exportados de otro equipo (-Evtx).
    El informe HTML funciona sin internet: tabla con filtros, orden por columna, registros por pagina, exportar CSV y
    "Generar resumen" de lo filtrado; resumen por usuario, IP y equipo de origen; gestion de cuentas; y una pestaña
    del equipo con servicios de acceso remoto, sesiones abiertas, zona horaria, sincronizacion de la hora y hasta
    cuando llega el registro (retencion).
    Las cuentas del sistema (SYSTEM, servicios, DWM, UMFD, cuentas de equipo) se excluyen salvo -IncluirSistema.
    Necesita administrador para leer el registro de Seguridad del equipo.

.EXAMPLE
    .\Auditar-Accesos.ps1
    .\Auditar-Accesos.ps1 -Dias 30 -SinCierres
    .\Auditar-Accesos.ps1 -Evtx .\Security_SERVIDOR.evtx
#>

[CmdletBinding()]
param(
    [ValidateRange(0, 3650)][int]$Dias = 0,
    [switch]$IncluirSistema,
    [switch]$SinCierres,
    [switch]$SinArchivados,
    [string[]]$Evtx,
    [ValidateRange(0, 10000000)][int]$MaxEventos = 0,
    [string]$CarpetaResultados
)

$ErrorActionPreference = "Stop"
$CodificacionCsv = if ($PSVersionTable.PSVersion.Major -ge 6) { "utf8BOM" } else { "UTF8" }   # CSV con BOM en 5.1 y en 7: Excel abre bien las tildes

# ============================================================ funciones
function Write-Paso  ($t) { Write-Host "`n== $t" -ForegroundColor Cyan }
function Write-Ok    ($t) { Write-Host "  [OK]    $t" -ForegroundColor Green }
function Write-Aviso ($t) { Write-Host "  [AVISO] $t" -ForegroundColor Yellow }
function Write-Fallo ($t) { Write-Host "  [ERROR] $t" -ForegroundColor Red }

function Get-UbicacionRed {
    <# Local, Interna (redes privadas), Externa o Desconocida. #>
    param([string]$IP)
    if (-not $IP -or $IP -eq "-" -or $IP -match '^(127\.|::1$|0\.0\.0\.0$)') { return "Local" }
    $ip4 = $IP -replace '^::ffff:', ''
    $dir = $null
    if (-not [System.Net.IPAddress]::TryParse($ip4, [ref]$dir)) { return "Desconocida" }
    $b = $dir.GetAddressBytes()
    if ($dir.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork) {
        if ($b[0] -eq 10 -or ($b[0] -eq 172 -and $b[1] -ge 16 -and $b[1] -le 31) -or ($b[0] -eq 192 -and $b[1] -eq 168) -or ($b[0] -eq 169 -and $b[1] -eq 254)) { return "Interna" }
        if ($b[0] -eq 100 -and $b[1] -ge 64 -and $b[1] -le 127) { return "Interna (VPN o CGNAT)" }   # 100.64.0.0/10: Tailscale y similares
        return "Externa"
    }
    if (($b[0] -band 0xFE) -eq 0xFC -or ($b[0] -eq 0xFE -and ($b[1] -band 0xC0) -eq 0x80)) { return "Interna" }   # fc00::/7 y fe80::/10
    return "Externa"
}

function Get-TextoTipoInicio {
    param([string]$Tipo)
    switch ($Tipo) {
        "2" { "Consola" } "3" { "Red (carpetas compartidas)" } "4" { "Tarea programada" } "5" { "Servicio" }
        "7" { "Desbloqueo" } "8" { "Red con contraseña en claro" } "9" { "Credenciales nuevas (runas)" }
        "10" { "Escritorio remoto" } "11" { "Consola (credenciales en caché)" } "12" { "Escritorio remoto (caché)" }
        "13" { "Desbloqueo (caché)" } "" { "" }
        default { "Tipo $Tipo" }
    }
}

function Get-TextoMotivoFallo {
    <# Motivo del 4625 a partir de SubStatus (o Status). #>
    param([string]$SubStatus, [string]$Status)
    $c = if ($SubStatus -and $SubStatus -ne "0x0") { $SubStatus } else { $Status }
    switch -Regex ([string]$c) {
        '(?i)0xc000006a' { "Contraseña incorrecta" }
        '(?i)0xc0000064' { "El usuario no existe" }
        '(?i)0xc0000234' { "Cuenta bloqueada" }
        '(?i)0xc0000072' { "Cuenta deshabilitada" }
        '(?i)0xc000006f' { "Fuera del horario permitido" }
        '(?i)0xc0000070' { "Equipo no permitido para la cuenta" }
        '(?i)0xc0000071' { "Contraseña caducada" }
        '(?i)0xc0000193' { "Cuenta caducada" }
        '(?i)0xc0000224' { "Debe cambiar la contraseña" }
        '(?i)0xc000015b' { "Tipo de inicio de sesión no concedido" }
        '(?i)0xc0000133' { "Hora del equipo y del controlador desfasadas" }
        '(?i)0xc000006d' { "Usuario o contraseña incorrectos" }
        '(?i)0xc0000413' { "Autenticación restringida (firewall de autenticación)" }
        default { if ($c) { "Código $c" } else { "" } }
    }
}

function Test-CuentaSistema {
    param([string]$Usuario, [string]$Dominio, [string]$TipoInicio)
    if ($TipoInicio -eq "5") { return $true }
    if (-not $Usuario -or $Usuario -eq "-") { return $true }
    if ($Usuario -match '^(SYSTEM|SISTEMA|LOCAL SERVICE|NETWORK SERVICE|SERVICIO LOCAL|SERVICIO DE RED|ANONYMOUS LOGON|INICIO DE SESIÓN ANÓNIMO)$') { return $true }
    if ($Usuario -match '^(DWM|UMFD)-\d+$' -or $Usuario.EndsWith('$')) { return $true }
    if ($Usuario -match '^[0-9A-Fa-f]{8}(-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}$') { return $true }   # cuentas virtuales de Windows con nombre GUID
    if ($Dominio -match '^(NT AUTHORITY|NT SERVICE|Window Manager|Font Driver Host|AUTORIDAD NT)$') { return $true }
    return $false
}

function ConvertTo-FilaAcceso {
    <# Un evento de acceso -> objeto con los datos en español; $null si es de una cuenta del sistema y se excluyen. #>
    param([int]$Id, [datetime]$Fecha, [hashtable]$Datos, [switch]$IncluirSistema)
    $d = $Datos
    $usuario = @($d.TargetUserName, $d.AccountName, $d.SubjectUserName) | Where-Object { $_ -and $_ -ne "-" } | Select-Object -First 1
    $dominio = @($d.TargetDomainName, $d.AccountDomain, $d.SubjectDomainName) | Where-Object { $_ -and $_ -ne "-" } | Select-Object -First 1
    $tipo = [string]$d.LogonType
    $ip = @($d.IpAddress, $d.ClientAddress) | Where-Object { $_ -and $_ -ne "-" } | Select-Object -First 1
    if (-not $ip) { $ip = "" }
    $origen = @($d.WorkstationName, $d.ClientName) | Where-Object { $_ -and $_ -ne "-" } | Select-Object -First 1
    $resultado = "Correcto"; $acceso = Get-TextoTipoInicio $tipo; $detalle = ""
    switch ($Id) {
        4624 { $evento = "Inicio de sesión"; $detalle = "Autenticación: $($d.AuthenticationPackageName)" }
        4625 { $evento = "Inicio fallido"; $resultado = "Fallido"; $detalle = Get-TextoMotivoFallo $d.SubStatus $d.Status }
        4634 { $evento = "Cierre de sesión" }
        4647 { $evento = "Cierre de sesión (por el usuario)" }
        4648 { $evento = "Credenciales explícitas"; $usuario = "$($d.SubjectUserName) -> $($d.TargetUserName)"; $detalle = "Servidor: $($d.TargetServerName)" }
        4672 { $evento = "Inicio con privilegios de administrador"; $acceso = "" }
        4778 { $evento = "Reconexión de escritorio remoto"; $acceso = "Escritorio remoto"; $detalle = "Sesión: $($d.SessionName)" }
        4779 { $evento = "Desconexión de escritorio remoto"; $acceso = "Escritorio remoto"; $detalle = "Sesión: $($d.SessionName)" }
        4800 { $evento = "Equipo bloqueado"; $acceso = "Consola" }
        4801 { $evento = "Equipo desbloqueado"; $acceso = "Consola" }
        4802 { $evento = "Protector de pantalla activado"; $acceso = "Consola" }
        4803 { $evento = "Protector de pantalla desactivado"; $acceso = "Consola" }
        default { $evento = "Evento $Id" }
    }
    $usuarioCuenta = @($d.TargetUserName, $d.AccountName, $d.SubjectUserName) | Where-Object { $_ -and $_ -ne "-" } | Select-Object -First 1
    if (-not $IncluirSistema) {
        if (Test-CuentaSistema $usuarioCuenta $dominio $tipo) { return $null }
        # 4648 lanzado por el propio equipo o un servicio (el que usa las credenciales es del sistema)
        if ($Id -eq 4648 -and (Test-CuentaSistema $d.SubjectUserName $d.SubjectDomainName "")) { return $null }
    }
    [pscustomobject]@{
        Fecha = $Fecha.ToString("dd/MM/yyyy"); Hora = $Fecha.ToString("HH:mm:ss"); Usuario = [string]$usuario; Dominio = [string]$dominio
        Evento = $evento; Resultado = $resultado; Acceso = $acceso; IP = [string]$ip; Ubicacion = Get-UbicacionRed $ip
        EquipoOrigen = [string]$origen; Proceso = [string]$(if ($d.ProcessName -and $d.ProcessName -ne "-") { $d.ProcessName } else { "" })
        Detalle = $detalle; Id = $Id; Orden = $Fecha.ToString("yyyy-MM-ddTHH:mm:ss"); FechaUTC = $Fecha.ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss")
    }
}

function Resolve-NombreSid {
    param([string]$Sid)
    if (-not $Sid -or $Sid -notmatch '^S-1-') { return $Sid }
    try { return (New-Object System.Security.Principal.SecurityIdentifier($Sid)).Translate([System.Security.Principal.NTAccount]).Value } catch { return $Sid }
}

function ConvertTo-FilaGestion {
    param([int]$Id, [datetime]$Fecha, [hashtable]$Datos)
    $d = $Datos
    $cuenta = [string]$(if ($d.TargetUserName) { $d.TargetUserName } else { $d.SamAccountName })
    $detalle = ""
    switch ($Id) {
        4720 { $op = "Cuenta creada"; $detalle = "Nombre: $($d.DisplayName)" }
        4722 { $op = "Cuenta habilitada" }
        4723 { $op = "Cambio de contraseña (por el usuario)" }
        4724 { $op = "Contraseña restablecida" }
        4725 { $op = "Cuenta deshabilitada" }
        4726 { $op = "Cuenta eliminada" }
        4732 { $op = "Añadida a un grupo local"; $detalle = "Grupo: $cuenta"; $cuenta = Resolve-NombreSid $(if ($d.MemberName -and $d.MemberName -ne "-") { $d.MemberName } else { $d.MemberSid }) }
        4733 { $op = "Quitada de un grupo local"; $detalle = "Grupo: $cuenta"; $cuenta = Resolve-NombreSid $(if ($d.MemberName -and $d.MemberName -ne "-") { $d.MemberName } else { $d.MemberSid }) }
        4738 { $op = "Cuenta modificada"; if ($d.PasswordLastSet -and $d.PasswordLastSet -ne "-" -and $d.PasswordLastSet -ne "%%1794") { $detalle = "Contraseña cambiada" } }
        4740 { $op = "Cuenta bloqueada"; $detalle = "Desde: $($d.TargetDomainName)" }
        4767 { $op = "Cuenta desbloqueada" }
        4781 { $op = "Cuenta renombrada"; $cuenta = "$($d.OldTargetUserName) -> $($d.NewTargetUserName)" }
        default { $op = "Evento $Id" }
    }
    [pscustomobject]@{
        Fecha = $Fecha.ToString("dd/MM/yyyy"); Hora = $Fecha.ToString("HH:mm:ss"); Operacion = $op; Cuenta = $cuenta
        Por = [string]$d.SubjectUserName; Detalle = $detalle; Id = $Id; Orden = $Fecha.ToString("yyyy-MM-ddTHH:mm:ss")
    }
}

function ConvertTo-JsonCadena {
    <# Cadena JSON segura dentro de <script> (sin </script>, sin saltos ni controles). #>
    param([string]$Texto)
    $t = ([string]$Texto).Replace('\', '\\').Replace('"', '\"').Replace("`r", '\r').Replace("`n", '\n').Replace("`t", '\t')
    $u = [string][char]92 + 'u'   # barra invertida y u, construidas aparte (escape JSON de caracteres)
    $t = $t.Replace('<', $u + '003c').Replace('>', $u + '003e').Replace('&', $u + '0026').Replace([string][char]0x2028, $u + '2028').Replace([string][char]0x2029, $u + '2029')
    return '"' + ([regex]::Replace($t, '[\x00-\x1f]', '')) + '"'
}

function ConvertTo-JsonFilas {
    <# Filas como arrays JSON (mas ligero que objetos) con las columnas indicadas. #>
    param([object[]]$Filas, [string[]]$Columnas)
    $sb = New-Object System.Text.StringBuilder
    [void]$sb.Append('[')
    $primera = $true
    foreach ($f in $Filas) {
        if (-not $primera) { [void]$sb.Append(',') }
        $primera = $false
        [void]$sb.Append('[')
        [void]$sb.Append((($Columnas | ForEach-Object { ConvertTo-JsonCadena ([string]$f.$_) }) -join ','))
        [void]$sb.Append(']')
    }
    [void]$sb.Append(']')
    return $sb.ToString()
}

function Get-ConsultaXPath {
    <# XPath de los eventos pedidos y, si Dias > 0, de los ultimos N dias. #>
    param([int[]]$Ids, [int]$Dias)
    $condicion = "(" + (($Ids | ForEach-Object { "EventID=$_" }) -join " or ") + ")"
    if ($Dias -gt 0) { $condicion += " and TimeCreated[timediff(@SystemTime) <= $([int64]$Dias * 86400000)]" }
    return "*[System[$condicion]]"
}

function Read-Eventos {
    <#
        Lee con EventLogReader, que no compone el mensaje de cada evento (Get-WinEvent si lo hace y es unas 35 veces
        mas lento con el registro de Seguridad). Devuelve Id, Fecha, RecordId y los datos con nombre del EventData.
    #>
    param([string]$Ruta, [switch]$EsArchivo, [int[]]$Ids, [int]$Dias, [int]$Max)
    $tipo = if ($EsArchivo) { [System.Diagnostics.Eventing.Reader.PathType]::FilePath } else { [System.Diagnostics.Eventing.Reader.PathType]::LogName }
    $consulta = New-Object System.Diagnostics.Eventing.Reader.EventLogQuery($Ruta, $tipo, (Get-ConsultaXPath -Ids $Ids -Dias $Dias))
    $consulta.ReverseDirection = $true   # primero los mas recientes: con -MaxEventos se quedan los ultimos
    $lector = New-Object System.Diagnostics.Eventing.Reader.EventLogReader($consulta)
    $salida = New-Object System.Collections.Generic.List[object]
    try {
        while ($null -ne ($ev = $lector.ReadEvent())) {
            try {
                $h = @{}
                $x = [xml]$ev.ToXml()
                foreach ($n in @($x.Event.EventData.Data)) { if ($n -and $n.Name) { $h[$n.Name] = [string]$n.'#text' } }
                $salida.Add([pscustomobject]@{ Id = [int]$ev.Id; Fecha = [datetime]$ev.TimeCreated; RecordId = $ev.RecordId; Datos = $h })
            } finally { $ev.Dispose() }
            if ($Max -gt 0 -and $salida.Count -ge $Max) { break }
            if ($salida.Count % 5000 -eq 0) { Write-Host "    $($salida.Count) eventos..." -ForegroundColor DarkGray }
        }
    } finally { $lector.Dispose() }
    return , $salida
}

function Get-PlantillaInforme {
    <# Informe HTML autonomo: sin librerias ni internet. Los datos se sustituyen en __META__, __AVISOS__, __ACCESOS__ y __GESTION__. #>
    return @'
<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITULO__</title>
<style>
:root{--primary:#1a3a5c;--primary-light:#2c5f8a;--accent:#1d4ed8;--bg:#f7f9fc;--card:#fff;--text:#1a1a2e;--muted:#555;--border:#dde3ea;--ok:#16a34a;--warn:#ca8a04;--bad:#dc2626}
*{box-sizing:border-box}body{margin:0;font-family:"Segoe UI",Arial,sans-serif;background:var(--bg);color:var(--text);font-size:14px}
header{background:linear-gradient(135deg,#0f2440,#1a3a5c 60%,#2c5f8a);color:#fff;padding:22px 28px}header h1{margin:0;font-weight:300;font-size:26px}header p{margin:6px 0 0;opacity:.85}
nav.tabs{display:flex;flex-wrap:wrap;gap:4px;padding:0 28px;background:var(--primary)}nav.tabs button{background:none;border:0;color:#cfe0f5;padding:12px 16px;cursor:pointer;font-size:14px;border-bottom:3px solid transparent}nav.tabs button.on{color:#fff;border-bottom-color:#60a5fa}
main{padding:20px 28px}.tab{display:none}.tab.on{display:block}
.kpis{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:12px;margin-bottom:16px}.kpi{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:12px 14px}.kpi b{display:block;font-size:22px;color:var(--primary)}.kpi span{color:var(--muted);font-size:12px}
.filtros{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:12px;display:flex;flex-wrap:wrap;gap:10px;align-items:flex-end;margin-bottom:12px}.filtros label{display:flex;flex-direction:column;font-size:12px;color:var(--muted);gap:3px}
.filtros input,.filtros select{padding:6px 8px;border:1px solid var(--border);border-radius:5px;font-size:13px;max-width:240px}
button.b{background:var(--accent);color:#fff;border:0;border-radius:5px;padding:7px 12px;cursor:pointer;font-size:13px}button.b.s{background:#64748b}button.b:disabled{opacity:.45;cursor:default}
.tabla{overflow:auto;max-height:75vh;background:var(--card);border:1px solid var(--border);border-radius:8px}table{border-collapse:collapse;width:100%}
th{background:var(--primary);color:#fff;text-align:left;padding:8px;font-weight:600;cursor:pointer;white-space:nowrap;position:sticky;top:0}td{padding:6px 8px;border-top:1px solid var(--border);vertical-align:top}
tr:nth-child(even) td{background:#f8fafc}tr:hover td{background:#eef4fb}.Fallido{color:var(--bad);font-weight:600}.Externa{color:var(--bad);font-weight:600}
.pag{display:flex;gap:10px;align-items:center;margin:10px 0;flex-wrap:wrap;color:var(--muted)}
.aviso{background:#fef9c3;border-left:4px solid var(--warn);padding:10px 12px;margin-bottom:8px;border-radius:4px}
#resumenFiltro{white-space:pre-wrap;background:var(--card);border:1px solid var(--border);border-radius:8px;padding:12px;margin:10px 0;display:none;font-family:Consolas,monospace;font-size:12.5px}
dl.info{display:grid;grid-template-columns:220px 1fr;gap:8px 14px;background:var(--card);border:1px solid var(--border);border-radius:8px;padding:14px}dl.info dt{color:var(--muted)}dl.info dd{margin:0;white-space:pre-wrap;font-family:inherit}
h2{color:var(--primary);border-bottom:2px solid var(--primary);padding-bottom:4px;font-weight:500;font-size:18px}
@media (max-width:700px){main,header{padding:14px}nav.tabs{padding:0 8px}dl.info{grid-template-columns:1fr}}
@media print{nav.tabs,.filtros,.pag{display:none}.tab{display:block}.tabla{max-height:none}}
</style></head><body>
<header><h1>Auditoría de accesos</h1><p id="sub"></p></header>
<nav class="tabs"><button data-t="accesos" class="on">Accesos</button><button data-t="resumen">Resumen</button><button data-t="gestion">Gestión de cuentas</button><button data-t="equipo">Equipo y fechas</button></nav>
<main>
<section id="accesos" class="tab on">
 <div id="avisos"></div>
 <div class="kpis" id="kpis"></div>
 <div class="filtros">
  <label>Buscar<input id="fTexto" placeholder="usuario, IP, equipo..."></label>
  <label>Evento<select id="fEvento"></select></label>
  <label>Resultado<select id="fResultado"></select></label>
  <label>Acceso<select id="fAcceso"></select></label>
  <label>Ubicación<select id="fUbicacion"></select></label>
  <label>Desde<input type="date" id="fDesde"></label>
  <label>Hasta<input type="date" id="fHasta"></label>
  <label>Registros por página<select id="fPorPagina"><option>25</option><option selected>50</option><option>100</option><option>250</option><option>500</option><option>1000</option><option value="0">Todos</option></select></label>
  <button class="b s" id="bLimpiar">Limpiar filtros</button>
  <button class="b" id="bCsv">Exportar CSV</button>
  <button class="b" id="bResumen">Generar resumen</button>
 </div>
 <div id="resumenFiltro"></div>
 <div class="pag" id="pag"></div>
 <div class="tabla"><table id="tA"><thead></thead><tbody></tbody></table></div>
</section>
<section id="resumen" class="tab"><h2>Por usuario y tipo de acceso</h2><div class="tabla"><table id="tRU"></table></div><h2>Por IP</h2><div class="tabla"><table id="tRI"></table></div><h2>Por equipo de origen</h2><div class="tabla"><table id="tRE"></table></div></section>
<section id="gestion" class="tab"><div class="filtros"><label>Buscar<input id="gTexto" placeholder="cuenta, operación..."></label></div><div class="tabla"><table id="tG"><thead></thead><tbody></tbody></table></div></section>
<section id="equipo" class="tab"><dl class="info" id="info"></dl></section>
</main>
<script>
const META = __META__, AVISOS = __AVISOS__, A = __ACCESOS__, G = __GESTION__;
const CA = ["Fecha","Hora","Usuario","Dominio","Evento","Resultado","Acceso","IP","Ubicación","Equipo origen","Proceso","Detalle","Id"];
const CG = ["Fecha","Hora","Operación","Cuenta","Por","Detalle","Id"];
const OA = 13;
const $ = id => document.getElementById(id);
const esc = s => String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const FILTROS = ['fTexto','fEvento','fResultado','fAcceso','fUbicacion','fDesde','fHasta'];
let vista = A.slice(), pagina = 0, orden = {col: 0, desc: true};

function opciones(sel, i){ const v=[...new Set(A.map(r=>r[i]).filter(x=>x))].sort(); sel.innerHTML='<option value="">(todos)</option>'+v.map(x=>`<option>${esc(x)}</option>`).join(''); }
function filtrar(){
  const t=$('fTexto').value.toLowerCase(), ev=$('fEvento').value, re=$('fResultado').value, ac=$('fAcceso').value, ub=$('fUbicacion').value, de=$('fDesde').value, ha=$('fHasta').value;
  vista = A.filter(r => (!ev||r[4]===ev)&&(!re||r[5]===re)&&(!ac||r[6]===ac)&&(!ub||r[8]===ub)&&(!de||r[OA].slice(0,10)>=de)&&(!ha||r[OA].slice(0,10)<=ha)&&(!t||r.slice(0,12).join(' ').toLowerCase().includes(t)));
  ordenar(); pagina=0; pintar();
}
function ordenar(){ const k = orden.col<=1 ? OA : orden.col; vista.sort((a,b)=>{ let x=a[k], y=b[k]; if(k===12){x=+x;y=+y;} return (x<y?-1:x>y?1:0)*(orden.desc?-1:1); }); }
function irA(p){ pagina=p; pintar(); }
function pintar(){
  const pp=+$('fPorPagina').value, total=vista.length, paginas = pp ? Math.max(1,Math.ceil(total/pp)) : 1;
  if(pagina>=paginas) pagina=paginas-1; if(pagina<0) pagina=0;
  const filas = pp ? vista.slice(pagina*pp,(pagina+1)*pp) : vista;
  $('tA').tBodies[0].innerHTML = filas.map(r=>'<tr>'+r.slice(0,13).map((v,i)=>`<td${i===5||i===8?' class="'+esc(v)+'"':''}>${esc(v)}</td>`).join('')+'</tr>').join('') || '<tr><td colspan="13">Ningún registro con estos filtros.</td></tr>';
  $('pag').innerHTML = `<button class="b s" ${pagina<=0?'disabled':''} onclick="irA(pagina-1)">&#9664; Anterior</button><span>Página ${pagina+1} de ${paginas} · ${total.toLocaleString('es-ES')} de ${A.length.toLocaleString('es-ES')} registros</span><button class="b s" ${pagina>=paginas-1?'disabled':''} onclick="irA(pagina+1)">Siguiente &#9654;</button>`;
}
function cabecera(tabla, cols, alPulsar){ tabla.tHead.innerHTML='<tr>'+cols.map((c,i)=>`<th data-i="${i}">${esc(c)}</th>`).join('')+'</tr>'; if(alPulsar) tabla.tHead.onclick=e=>{const i=e.target.dataset.i; if(i!==undefined) alPulsar(+i);}; }
function csv(cols, filas){ const q=v=>'"'+String(v).replace(/"/g,'""')+'"'; return '\ufeff'+[cols.map(q).join(';'),...filas.map(r=>r.slice(0,cols.length).map(q).join(';'))].join('\r\n'); }
function descargar(nombre, texto, tipo){ const a=document.createElement('a'); a.href=URL.createObjectURL(new Blob([texto],{type:tipo})); a.download=nombre; document.body.appendChild(a); a.click(); a.remove(); }
function contar(filas, i){ const m=new Map(); filas.forEach(r=>{const k=r[i]||'(vacío)'; m.set(k,(m.get(k)||0)+1);}); return [...m.entries()].sort((a,b)=>b[1]-a[1]); }
function fmt(s){ return s.slice(8,10)+'/'+s.slice(5,7)+'/'+s.slice(0,4)+' '+s.slice(11,16); }
function resumenTexto(){
  const v=vista, ok=v.filter(r=>r[5]==='Correcto').length, ko=v.filter(r=>r[5]==='Fallido').length;
  const l=[`RESUMEN DE LO FILTRADO - ${META['Equipo']||''} - ${new Date().toLocaleString('es-ES')}`, '', `Registros: ${v.length}`, `Correctos: ${ok}`, `Fallidos: ${ko}`+(v.length?` (${(ko*100/v.length).toFixed(1)} %)`:''), `Desde fuera (IP externa): ${v.filter(r=>r[8]==='Externa').length}`];
  if(v.length){ let mn=v[0][OA], mx=v[0][OA]; v.forEach(r=>{ if(r[OA]<mn) mn=r[OA]; if(r[OA]>mx) mx=r[OA]; }); l.push(`Periodo: ${fmt(mn)} - ${fmt(mx)}`); }
  const bloque=(t,arr,n)=>{ if(!arr.length) return; l.push('',t); arr.slice(0,n).forEach(([k,c])=>l.push(`  ${String(c).padStart(7)}  ${k}`)); };
  const fall=v.filter(r=>r[5]==='Fallido');
  bloque('Por tipo de evento:', contar(v,4), 50); bloque('Por tipo de acceso:', contar(v.filter(r=>r[6]),6), 50);
  bloque('Usuarios con más registros:', contar(v,2), 15); bloque('Usuarios con más fallos:', contar(fall,2), 15);
  bloque('Motivos de los fallos:', contar(fall,11), 15); bloque('IP externas:', contar(v.filter(r=>r[8]==='Externa'),7), 20);
  return l.join('\n');
}
function tablaResumen(tabla, i, filtro){
  const m=new Map(); A.filter(filtro).forEach(r=>{ const k=r[i]+'\u0001'+(r[6]||'-'); let e=m.get(k); if(!e){e={k:r[i],a:r[6]||'-',n:0,f:0,p:r[OA],u:r[OA]}; m.set(k,e);} e.n++; if(r[5]==='Fallido') e.f++; if(r[OA]<e.p) e.p=r[OA]; if(r[OA]>e.u) e.u=r[OA]; });
  const f=[...m.values()].sort((a,b)=>b.n-a.n);
  tabla.innerHTML='<thead><tr><th>'+['Nombre','Acceso','Registros','Fallidos','Primero','Último'].join('</th><th>')+'</th></tr></thead><tbody>'+(f.map(e=>`<tr><td>${esc(e.k)}</td><td>${esc(e.a)}</td><td>${e.n}</td><td${e.f?' class="Fallido"':''}>${e.f}</td><td>${fmt(e.p)}</td><td>${fmt(e.u)}</td></tr>`).join('')||'<tr><td colspan="6">Sin datos.</td></tr>')+'</tbody>';
}
function pintarGestion(){ const t=$('gTexto').value.toLowerCase(); const f=G.filter(r=>!t||r.slice(0,7).join(' ').toLowerCase().includes(t)); $('tG').tBodies[0].innerHTML=f.map(r=>'<tr>'+r.slice(0,7).map(v=>`<td>${esc(v)}</td>`).join('')+'</tr>').join('') || '<tr><td colspan="7">Sin cambios en cuentas en el periodo.</td></tr>'; }

document.querySelectorAll('nav.tabs button').forEach(b=>b.onclick=()=>{ document.querySelectorAll('nav.tabs button,.tab').forEach(x=>x.classList.remove('on')); b.classList.add('on'); $(b.dataset.t).classList.add('on'); });
$('sub').textContent = `${META['Equipo']||''} · generado ${META['Generado']||''} · ${META['Periodo']||''}`;
$('avisos').innerHTML = AVISOS.map(a=>`<div class="aviso">${esc(a)}</div>`).join('');
const K=[['Registros',A.length],['Correctos',A.filter(r=>r[5]==='Correcto').length],['Fallidos',A.filter(r=>r[5]==='Fallido').length],['Desde fuera',A.filter(r=>r[8]==='Externa').length],['Escritorio remoto',A.filter(r=>r[6]==='Escritorio remoto').length],['Usuarios',new Set(A.map(r=>r[2])).size],['Cambios de cuentas',G.length]];
$('kpis').innerHTML=K.map(([t,n])=>`<div class="kpi"><b>${n.toLocaleString('es-ES')}</b><span>${t}</span></div>`).join('');
opciones($('fEvento'),4); opciones($('fResultado'),5); opciones($('fAcceso'),6); opciones($('fUbicacion'),8);
cabecera($('tA'), CA, i=>{ orden = {col:i, desc: orden.col===i ? !orden.desc : false}; ordenar(); pintar(); });
cabecera($('tG'), CG);
FILTROS.forEach(id=>$(id).addEventListener(id==='fTexto'?'input':'change', filtrar));
$('fPorPagina').onchange=()=>irA(0);
$('bLimpiar').onclick=()=>{ FILTROS.forEach(id=>$(id).value=''); filtrar(); };
$('bCsv').onclick=()=>descargar(`accesos_filtrados_${new Date().toISOString().slice(0,10)}.csv`, csv(CA, vista), 'text/csv;charset=utf-8');
$('bResumen').onclick=()=>{ const t=resumenTexto(), d=$('resumenFiltro'); d.style.display='block'; d.innerHTML=esc(t)+'\n\n<button class="b" id="bResTxt">Descargar resumen (.txt)</button>'; $('bResTxt').onclick=()=>descargar(`resumen_accesos_${new Date().toISOString().slice(0,10)}.txt`, '\ufeff'+t, 'text/plain;charset=utf-8'); };
$('gTexto').addEventListener('input', pintarGestion);
tablaResumen($('tRU'),2,()=>true); tablaResumen($('tRI'),7,r=>r[7]); tablaResumen($('tRE'),9,r=>r[9]);
$('info').innerHTML=Object.entries(META).map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('');
filtrar(); pintarGestion();
</script></body></html>
'@
}

# ============================================================ inicio
$inicio = Get-Date
if (-not $CarpetaResultados) { $CarpetaResultados = Join-Path $PSScriptRoot "Resultados" }
$equipo = $env:COMPUTERNAME
$sello = $inicio.ToString("yyyyMMdd_HHmmss")
$idsAcceso = @(4624, 4625, 4634, 4647, 4648, 4672, 4778, 4779, 4800, 4801, 4802, 4803)
if ($SinCierres) { $idsAcceso = @($idsAcceso | Where-Object { $_ -notin 4634, 4647 }) }
$idsGestion = @(4720, 4722, 4723, 4724, 4725, 4726, 4732, 4733, 4738, 4740, 4767, 4781)
$avisos = New-Object System.Collections.Generic.List[string]

Write-Host ""
Write-Host "==============================================" -ForegroundColor Cyan
Write-Host " AUDITORIA DE ACCESOS - $equipo" -ForegroundColor Cyan
Write-Host " Solo lectura del registro de Seguridad" -ForegroundColor Cyan
Write-Host "==============================================" -ForegroundColor Cyan

try {
    $esAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $Evtx -and -not $esAdmin) { throw "Para leer el registro de Seguridad de este equipo hay que ejecutarlo como administrador." }
    New-Item -ItemType Directory -Force -Path $CarpetaResultados | Out-Null

    Write-Paso "Origen de los eventos"
    $origenes = New-Object System.Collections.Generic.List[object]
    if ($Evtx) {
        foreach ($e in $Evtx) {
            if (-not (Test-Path -LiteralPath $e)) { throw "No se encuentra el archivo: $e" }
            $origenes.Add([pscustomobject]@{ Nombre = Split-Path $e -Leaf; Ruta = (Resolve-Path -LiteralPath $e).Path; EsArchivo = $true })
        }
        $equipo = [System.IO.Path]::GetFileNameWithoutExtension($Evtx[0]) + $(if ($Evtx.Count -gt 1) { " y $($Evtx.Count - 1) más" } else { "" })
    } else {
        $origenes.Add([pscustomobject]@{ Nombre = "Registro de Seguridad"; Ruta = "Security"; EsArchivo = $false })
        if (-not $SinArchivados) {
            $archivados = @(Get-ChildItem -Path "$env:SystemRoot\System32\winevt\Logs" -Filter "Archive-Security*.evtx" -ErrorAction SilentlyContinue)
            $totalGB = [math]::Round((($archivados | Measure-Object Length -Sum).Sum) / 1GB, 1)
            # Un archivado se escribe al llenarse el registro: su fecha de modificacion es la de su evento mas reciente
            if ($Dias -gt 0) { $archivados = @($archivados | Where-Object { $_.LastWriteTime -ge $inicio.AddDays(-$Dias) }) }
            $usarGB = [math]::Round((($archivados | Measure-Object Length -Sum).Sum) / 1GB, 1)
            if ($totalGB -gt 0) { $avisos.Add("Hay registros de Seguridad archivados que ocupan $totalGB GB en $env:SystemRoot\System32\winevt\Logs (el registro está en modo «archivar al llenarse», que nunca borra): conviene revisarlos.") }
            if ($usarGB -gt 2) { Write-Aviso "Se van a leer $($archivados.Count) registros archivados ($usarGB GB): puede tardar mucho. Usa -Dias o -SinArchivados para acotar." }
            foreach ($a in $archivados | Sort-Object LastWriteTime -Descending) {
                $origenes.Add([pscustomobject]@{ Nombre = $a.Name; Ruta = $a.FullName; EsArchivo = $true })
            }
        }
    }
    foreach ($o in $origenes) { Write-Ok $o.Nombre }

    Write-Paso "Lectura de eventos"
    $accesos = New-Object System.Collections.Generic.List[object]
    $gestion = New-Object System.Collections.Generic.List[object]
    $excluidos = 0; $leidos = 0; $vistos = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach ($o in $origenes) {
        foreach ($grupo in @(@{ Ids = $idsAcceso; Tipo = "A" }, @{ Ids = $idsGestion; Tipo = "G" })) {
            $evs = Read-Eventos -Ruta $o.Ruta -EsArchivo:$o.EsArchivo -Ids $grupo.Ids -Dias $Dias -Max $MaxEventos
            foreach ($ev in $evs) {
                # Un archivado puede repetir eventos del registro actual: se descartan por su numero de registro
                if (-not $vistos.Add("$($ev.RecordId)|$($ev.Id)|$($ev.Fecha.Ticks)")) { continue }
                $leidos++
                if ($grupo.Tipo -eq "G") { $gestion.Add((ConvertTo-FilaGestion -Id $ev.Id -Fecha $ev.Fecha -Datos $ev.Datos)); continue }
                $fila = ConvertTo-FilaAcceso -Id $ev.Id -Fecha $ev.Fecha -Datos $ev.Datos -IncluirSistema:$IncluirSistema
                if ($fila) { $accesos.Add($fila) } else { $excluidos++ }
            }
            Write-Ok ("{0}: {1} eventos de {2}" -f $o.Nombre, $evs.Count, $(if ($grupo.Tipo -eq "A") { "acceso" } else { "gestión de cuentas" }))
        }
    }
    $accesosOrd = @($accesos | Sort-Object Orden -Descending)
    $gestionOrd = @($gestion | Sort-Object Orden -Descending)
    Write-Ok "$($accesosOrd.Count) accesos y $($gestionOrd.Count) cambios de cuentas$(if ($excluidos) { " ($excluidos de cuentas del sistema excluidos)" })"

    Write-Paso "Comprobaciones del equipo y de las fechas"
    $zona = [System.TimeZoneInfo]::Local
    $desfase = $zona.GetUtcOffset($inicio)
    $textoDesfase = "UTC{0}{1:hh\:mm}" -f $(if ($desfase -lt [timespan]::Zero) { "-" } else { "+" }), $desfase.Duration()
    $equipoInfo = [ordered]@{
        "Equipo" = $env:COMPUTERNAME; "Generado" = $inicio.ToString("dd/MM/yyyy HH:mm:ss")
        "Zona horaria" = "$($zona.DisplayName) ($textoDesfase, horario de verano: $(if ($zona.IsDaylightSavingTime($inicio)) { 'sí' } else { 'no' }))"
        "Hora UTC al generar" = $inicio.ToUniversalTime().ToString("dd/MM/yyyy HH:mm:ss")
    }
    if (-not $Evtx) {
        try {
            $fuente = ((& w32tm /query /source 2>$null) | Select-Object -First 1)
            $equipoInfo["Origen de la hora"] = [string]$fuente
            if ($fuente -match 'CMOS|Free-running|libre') { $avisos.Add("La hora del equipo no se sincroniza con ningún servidor: las fechas pueden no ser exactas.") }
        } catch { }
        try {
            $reg = Get-WinEvent -ListLog Security
            $masAntiguo = (Get-WinEvent -LogName Security -MaxEvents 1 -Oldest).TimeCreated
            $cubre = [math]::Round(($inicio - $masAntiguo).TotalDays, 1)
            $equipoInfo["Registro de Seguridad"] = "{0} MB de {1} MB máximo, modo {2}, {3} eventos" -f [math]::Round($reg.FileSize / 1MB, 1), [math]::Round($reg.MaximumSizeInBytes / 1MB), $reg.LogMode, $reg.RecordCount
            $equipoInfo["Evento más antiguo"] = "$($masAntiguo.ToString('dd/MM/yyyy HH:mm')) (el registro cubre $cubre días)"
            $hayArchivados = @($origenes | Where-Object { $_.EsArchivo }).Count -gt 0
            if ($hayArchivados) { $equipoInfo["Registros archivados leídos"] = "$(@($origenes | Where-Object { $_.EsArchivo }).Count) (guardan lo anterior al registro actual)" }
            elseif ($Dias -gt 0 -and $cubre -lt $Dias) { $avisos.Add("Se han pedido $Dias días pero el registro solo llega a $cubre días: lo anterior se ha sobrescrito. Para más historial, aumentar el tamaño máximo del registro de Seguridad.") }
            elseif ($cubre -lt 7) { $avisos.Add("El registro de Seguridad solo cubre $cubre días: conviene aumentar su tamaño máximo (Visor de eventos > Seguridad > Propiedades).") }
        } catch { $avisos.Add("No se pudo leer la retención del registro: $($_.Exception.Message)") }
        $rdp = (Get-ItemProperty -Path "HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server" -Name fDenyTSConnections -ErrorAction SilentlyContinue).fDenyTSConnections
        $servicios = foreach ($s in @(@("TermService", "Escritorio remoto"), @("WinRM", "Administración remota (WinRM)"), @("sshd", "Servidor SSH"))) {
            $svc = Get-Service -Name $s[0] -ErrorAction SilentlyContinue
            if ($svc) { "$($s[1]): $(if ($svc.Status -eq 'Running') { 'en marcha' } else { 'parado' })" }
        }
        $equipoInfo["Escritorio remoto"] = if ($rdp -eq 0) { "Permitido" } elseif ($rdp -eq 1) { "No permitido" } else { "Desconocido" }
        $equipoInfo["Servicios de acceso remoto"] = (@($servicios) -join " · ")
        try { $equipoInfo["Sesiones abiertas"] = ((& quser 2>$null) -join "`n") } catch { }
    } else {
        $equipoInfo["Archivos analizados"] = ($Evtx | ForEach-Object { Split-Path $_ -Leaf }) -join ", "
    }
    $futuros = @($accesosOrd | Where-Object { $_.Orden -gt $inicio.AddMinutes(5).ToString("yyyy-MM-ddTHH:mm:ss") }).Count
    if ($futuros) { $avisos.Add("$futuros eventos tienen fecha posterior a ahora: la hora del equipo estuvo mal en algún momento.") }
    if (-not @($accesosOrd | Where-Object { $_.Id -eq 4624 }).Count -and -not $Evtx) { $avisos.Add("No hay ningún inicio de sesión (4624): puede que la auditoría de inicio de sesión esté desactivada (directiva Auditar inicio de sesión).") }
    if ($Dias -gt 0) { $equipoInfo["Periodo"] = "Últimos $Dias días" } else { $equipoInfo["Periodo"] = "Todo lo disponible" }
    if ($accesosOrd.Count) { $equipoInfo["Primer y último acceso"] = "$($accesosOrd[-1].Fecha) $($accesosOrd[-1].Hora) - $($accesosOrd[0].Fecha) $($accesosOrd[0].Hora)" }
    foreach ($a in $avisos) { Write-Aviso $a }

    Write-Paso "Informe"
    $base = "Accesos_{0}_{1}" -f ($equipo -replace '[^A-Za-z0-9_-]', '_'), $sello
    $csvA = Join-Path $CarpetaResultados "$base.csv"
    $csvG = Join-Path $CarpetaResultados (($base -replace '^Accesos', 'GestionCuentas') + ".csv")
    $accesosOrd | Select-Object Fecha, Hora, Usuario, Dominio, Evento, Resultado, Acceso, IP, Ubicacion, EquipoOrigen, Proceso, Detalle, Id, FechaUTC |
        Export-Csv -LiteralPath $csvA -Delimiter ";" -NoTypeInformation -Encoding $CodificacionCsv
    if ($gestionOrd.Count) { $gestionOrd | Select-Object Fecha, Hora, Operacion, Cuenta, Por, Detalle, Id | Export-Csv -LiteralPath $csvG -Delimiter ";" -NoTypeInformation -Encoding $CodificacionCsv }

    $colsA = @("Fecha", "Hora", "Usuario", "Dominio", "Evento", "Resultado", "Acceso", "IP", "Ubicacion", "EquipoOrigen", "Proceso", "Detalle", "Id", "Orden")
    $colsG = @("Fecha", "Hora", "Operacion", "Cuenta", "Por", "Detalle", "Id", "Orden")
    $meta = "{" + ((@($equipoInfo.Keys | ForEach-Object { (ConvertTo-JsonCadena $_) + ":" + (ConvertTo-JsonCadena $equipoInfo[$_]) })) -join ",") + "}"
    $avisosJson = "[" + ((@($avisos | ForEach-Object { ConvertTo-JsonCadena $_ })) -join ",") + "]"
    $plantilla = Get-PlantillaInforme
    $html = $plantilla.Replace("__TITULO__", [System.Net.WebUtility]::HtmlEncode("Accesos - $equipo - $($inicio.ToString('dd/MM/yyyy HH:mm'))")).
        Replace("__META__", $meta).Replace("__AVISOS__", $avisosJson).
        Replace("__ACCESOS__", (ConvertTo-JsonFilas $accesosOrd $colsA)).Replace("__GESTION__", (ConvertTo-JsonFilas $gestionOrd $colsG))
    $rutaHtml = Join-Path $CarpetaResultados "$base.html"
    [System.IO.File]::WriteAllText($rutaHtml, $html, (New-Object System.Text.UTF8Encoding($true)))
    Write-Ok $rutaHtml
    Write-Ok $csvA
    if ($gestionOrd.Count) { Write-Ok $csvG }
}
catch {
    Write-Fallo $_.Exception.Message
}
finally {
    Write-Paso "Resumen"
    if ($accesosOrd) {
        Write-Host ("  Accesos: {0} (correctos {1}, fallidos {2}, desde fuera {3})" -f $accesosOrd.Count, @($accesosOrd | Where-Object Resultado -eq "Correcto").Count,
            @($accesosOrd | Where-Object Resultado -eq "Fallido").Count, @($accesosOrd | Where-Object Ubicacion -eq "Externa").Count)
    }
    Write-Host ("  Tiempo: {0:mm\:ss}" -f ((Get-Date) - $inicio))
}
