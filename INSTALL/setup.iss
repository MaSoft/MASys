; ============================================================
; MASys - Instalador Inno Setup 6
;
; Empaqueta la salida de PyInstaller (onedir: MASys.exe + _internal\) en un unico instalador.
; El equipo destino NO necesita Python: el runtime, PyQt5 y las dependencias van en _internal\.
; Se instala por usuario, sin permisos de administrador, en %LOCALAPPDATA%\Programs\MASys.
; Los datos (multisys.db, movil.db, INFORMES\, BACKUPS\, SCRIPTS\) viven junto al programa,
; por eso la carpeta tiene que ser escribible por el usuario: no se usa Program Files.
; MASys.exe SI pide administrador al abrirse (uac_admin en MASys.spec): por eso [Run] usa shellexec.
;
; Compilar: INSTALL\build_install.bat (icono, PyInstaller con INSTALL\MASys.spec, portable y este script)
;
; Despliegue silencioso (GPO o scripts de administracion):
;   MASys_Setup_v<version>.exe /SILENT
;       progreso visible, sin preguntas
;   MASys_Setup_v<version>.exe /VERYSILENT /SUPPRESSMSGBOXES
;       sin ninguna ventana; si hay una instalacion anterior se actualiza encima (conserva los datos)
;   Anadir /DIR="D:\MASys" para otra carpeta (tiene que ser escribible por el usuario).
;   Anadir /TASKS="desktopicon" para crear el acceso directo en el escritorio.
;   Se instala para el usuario que ejecuta el instalador (PrivilegesRequired=lowest).
; ============================================================

; -- Version: unico punto de verdad del instalador. build_install.bat comprueba que coincide
;    con msys\version.py antes de compilar.
#define MyAppName      "MASys"
#define MyAppVersion   "5.7.2"
#define MyAppPublisher "Masoft.es"
#define MyAppExeName   "MASys.exe"

[Setup]
; GUID fijo de la aplicacion: NO cambiarlo entre versiones (las actualizaciones se instalan encima)
AppId={{5C2084B0-0258-4ECF-8F91-1B57DCCA6B0E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoProductName={#MyAppName}
VersionInfoDescription=MASys - herramienta de tecnico IT para Windows
PrivilegesRequired=lowest
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes
; Salida en INSTALL\dist (rutas relativas a este archivo)
OutputDir=dist
OutputBaseFilename=MASys_Setup_v{#MyAppVersion}
; Lo genera create_icon.py (paso 2 de build_install.bat) desde msys\ui\icono_app.py
SetupIconFile=masys.ico
; Icono real de la app en Aplicaciones instaladas (el embebido en MASys.exe)
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear acceso directo en el escritorio"; GroupDescription: "Accesos directos:"; Flags: unchecked

[InstallDelete]
; Compilacion anterior: se sustituye entera para no mezclar librerias de dos versiones
Type: filesandordirs; Name: "{app}\_internal"

[Files]
; Aplicacion compilada (salida de PyInstaller onedir): MASys.exe + _internal\
Source: "dist\MASys\*";          DestDir: "{app}";         Flags: ignoreversion recursesubdirs createallsubdirs
; Catalogo de scripts de ejemplo: solo si no existe, para no pisar los scripts que anada el usuario
Source: "..\SCRIPTS\*";          DestDir: "{app}\SCRIPTS"; Excludes: "*.bak"; Flags: onlyifdoesntexist uninsneveruninstall recursesubdirs createallsubdirs
Source: "..\manual_MASys.html";  DestDir: "{app}";         Flags: ignoreversion
; Licencia LGPL de libpff (visor PST/OST de Cuentas Correo, 4.9.0): tiene que ir con el programa
Source: "..\licencias\libpff\*"; DestDir: "{app}\licencias\libpff"; Flags: ignoreversion
; Agente de analisis (4.7.0): un solo .exe que el tecnico envia al cliente (no se ejecuta aqui)
Source: "dist\MASys_Agente_v{#MyAppVersion}.exe"; DestDir: "{app}\AGENTE"; Flags: ignoreversion
; Servidor de RustDesk (4.12.0): hbbs/hbbr compilados con el parche de MASys + licencia AGPL, origen y parche
; (compilar_servidor.bat los deja en SERVIDOR_RUSTDESK\bin). Sus datos (datos\: clave privada y equipos) los crea
; MASys al arrancarlo y NUNCA se instalan ni se borran.
Source: "..\SERVIDOR_RUSTDESK\bin\hbbs.exe";   DestDir: "{app}\SERVIDOR_RUSTDESK\bin"; Flags: ignoreversion
Source: "..\SERVIDOR_RUSTDESK\bin\hbbr.exe";   DestDir: "{app}\SERVIDOR_RUSTDESK\bin"; Flags: ignoreversion
Source: "..\SERVIDOR_RUSTDESK\LICENSE";         DestDir: "{app}\SERVIDOR_RUSTDESK"; Flags: ignoreversion
Source: "..\SERVIDOR_RUSTDESK\FUENTE.txt";      DestDir: "{app}\SERVIDOR_RUSTDESK"; Flags: ignoreversion
Source: "..\SERVIDOR_RUSTDESK\masys.patch";     DestDir: "{app}\SERVIDOR_RUSTDESK"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Comment: "Herramienta de tecnico IT para Windows"
Name: "{autoprograms}\Manual de {#MyAppName}"; Filename: "{app}\manual_MASys.html"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
; shellexec: MASys.exe pide administrador; sin shellexec Windows no muestra el aviso y no se abre (error 740)
Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Description: "Abrir {#MyAppName}"; Flags: nowait postinstall skipifsilent shellexec

[UninstallDelete]
; Solo programa y temporales. Los datos (multisys.db, movil.db, buscacompartidos_config.json,
; INFORMES\, BACKUPS\, SCRIPTS\, COPIAS_MOVIL\, SERVIDOR_RUSTDESK\datos\) NUNCA se borran: la carpeta del
; programa se queda con ellos. Las tareas programadas y reglas de firewall del servidor de RustDesk se quitan desde
; MASys (Servidor propio) ANTES de desinstalar: el desinstalador no tiene permisos de administrador.
Type: filesandordirs; Name: "{app}\_internal"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: dirifempty;     Name: "{app}\AGENTE"
Type: dirifempty;     Name: "{app}\SERVIDOR_RUSTDESK\bin"
Type: dirifempty;     Name: "{app}\SERVIDOR_RUSTDESK"
Type: filesandordirs; Name: "{app}\licencias"
Type: dirifempty;     Name: "{app}"

[Code]
// Como MAOSI: si MASys sigue abierto, Windows bloquea MASys.exe y _internal\ y la instalacion
// o desinstalacion se salta esos ficheros sin avisar. MASys corre como administrador y este
// instalador no, asi que taskkill puede no poder cerrarlo: entonces se pide cerrarlo a mano.
function MASysSigueAbierto(): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec(ExpandConstant('{cmd}'),
    '/C tasklist /FI "IMAGENAME eq {#MyAppExeName}" /NH | find /I "{#MyAppExeName}"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
end;

function CerrarMASys(): Boolean;
var
  ResultCode: Integer;
begin
  Result := True;
  Exec(ExpandConstant('{sys}\taskkill.exe'), '/IM {#MyAppExeName} /F', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  while MASysSigueAbierto() do
  begin
    if SuppressibleMsgBox('MASys esta abierto con permisos de administrador y no se puede cerrar desde aqui.' + #13#10 + #13#10 +
         'Cierralo y pulsa Reintentar, o Cancelar para salir.',
         mbError, MB_RETRYCANCEL, IDCANCEL) = IDCANCEL then
    begin
      Result := False;
      Exit;
    end;
  end;
end;

// Servidor de RustDesk (4.12.0): si hbbs o hbbr estan en marcha (desde MASys o como tarea de SYSTEM),
// sus .exe estan bloqueados y este instalador, sin administrador, no puede pararlos.
function ServidorSigueAbierto(): Boolean;
var
  ResultCode: Integer;
begin
  Result := (Exec(ExpandConstant('{cmd}'), '/C tasklist /FI "IMAGENAME eq hbbs.exe" /NH | find /I "hbbs.exe"',
               '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0))
         or (Exec(ExpandConstant('{cmd}'), '/C tasklist /FI "IMAGENAME eq hbbr.exe" /NH | find /I "hbbr.exe"',
               '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and (ResultCode = 0));
end;

function ServidorParado(): Boolean;
begin
  Result := True;
  while ServidorSigueAbierto() do
  begin
    if SuppressibleMsgBox('El servidor de RustDesk (hbbs/hbbr) esta en marcha y bloquea sus archivos.' + #13#10 + #13#10 +
         'Abre MASys > Acceso remoto > Servidor propio y pulsa Parar (y, si vas a desinstalar, desmarca ' +
         'Arrancar con Windows y quita las reglas del firewall). Despues pulsa Reintentar, o Cancelar para salir.',
         mbError, MB_RETRYCANCEL, IDCANCEL) = IDCANCEL then
    begin
      Result := False;
      Exit;
    end;
  end;
end;

function InitializeSetup(): Boolean;
begin
  Result := ServidorParado() and CerrarMASys();
end;

function InitializeUninstall(): Boolean;
begin
  Result := ServidorParado() and CerrarMASys();
end;
