@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

:: Este script vive en INSTALL\ - todas las rutas de aqui en adelante son
:: relativas a la raiz del proyecto (donde esta multisys.py), no a esta carpeta.
cd /d "%~dp0.."

echo.
echo ============================================================
echo  MASys - Generador de instalador y version portable
echo  Requiere: Python 3.12+ de python.org, PyInstaller 6+, Inno Setup 6
echo ============================================================
echo.

:: --- Verificar que estamos en la carpeta correcta ---
if not exist "multisys.py" (
    echo [ERROR] Ejecuta este script desde la carpeta INSTALL de MASys.
    echo         La carpeta superior no contiene multisys.py.
    pause & exit /b 1
)
if not exist "INSTALL\MASys.spec" (
    echo [ERROR] No se encontro INSTALL\MASys.spec.
    pause & exit /b 1
)
if not exist "manual_MASys.html" (
    echo [ERROR] No se encontro manual_MASys.html.
    pause & exit /b 1
)

:: --- Version: unico punto de verdad en INSTALL\setup.iss - debe coincidir con msys\version.py ---
set ISS_VER=
set APP_VER=
for /f "tokens=3" %%a in ('findstr /b /c:"#define MyAppVersion" "INSTALL\setup.iss"') do set ISS_VER=%%~a
for /f "tokens=3" %%a in ('findstr /b /c:"APP_VERSION" "msys\version.py"') do set APP_VER=%%~a
if "!ISS_VER!"=="" (
    echo [ERROR] No se encontro MyAppVersion en INSTALL\setup.iss.
    pause & exit /b 1
)
if not "!ISS_VER!"=="!APP_VER!" (
    echo [ERROR] Version distinta: setup.iss !ISS_VER! / msys\version.py !APP_VER!
    pause & exit /b 1
)
echo [OK]   Version: !ISS_VER!

:: --- Detectar Python - como TENSIOMETRO: nunca el de Microsoft Store ---
set PYTHON_CMD=
where python >nul 2>&1
if !ERRORLEVEL! EQU 0 (
    python -c "import sys; exit(0 if sys.version_info >= (3,12) and 'WindowsApps' not in sys.executable else 1)" >nul 2>&1
    if !ERRORLEVEL! EQU 0 set "PYTHON_CMD=python"
)
if "!PYTHON_CMD!"=="" (
    where py >nul 2>&1
    if !ERRORLEVEL! EQU 0 (
        py -3 -c "import sys; exit(0 if sys.version_info >= (3,12) and 'WindowsApps' not in sys.executable else 1)" >nul 2>&1
        if !ERRORLEVEL! EQU 0 set "PYTHON_CMD=py -3"
    )
)
if "!PYTHON_CMD!"=="" (
    echo [ERROR] No se encontro Python 3.12 o superior de python.org.
    echo         El Python de Microsoft Store no sirve para compilar.
    pause & exit /b 1
)
echo [OK]   Python: !PYTHON_CMD!

:: --- Instalar dependencias del proyecto - requirements.txt, incluye Pillow para el icono ---
echo.
echo [INFO]  Instalando dependencias de requirements.txt...
!PYTHON_CMD! -m pip install -r requirements.txt --quiet
if !ERRORLEVEL! NEQ 0 (
    echo [ERROR] No se pudieron instalar las dependencias de requirements.txt.
    pause & exit /b 1
)
echo [OK]   Dependencias instaladas.

:: --- Verificar / instalar PyInstaller ---
!PYTHON_CMD! -c "import PyInstaller" >nul 2>&1
if !ERRORLEVEL! NEQ 0 (
    echo [INFO]  PyInstaller no encontrado. Instalando...
    !PYTHON_CMD! -m pip install "pyinstaller>=6.0" --quiet
    if !ERRORLEVEL! NEQ 0 (
        echo [ERROR] No se pudo instalar PyInstaller.
        pause & exit /b 1
    )
    echo [OK]   PyInstaller instalado.
) else (
    echo [OK]   PyInstaller disponible.
)

:: --- Buscar Inno Setup 6 ---
set ISCC=
if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist "C:\Program Files\Inno Setup 6\ISCC.exe"       set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"
if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"

if "!ISCC!"=="" (
    echo [ERROR] Inno Setup 6 no encontrado en las rutas habituales.
    echo         Descargalo desde: https://jrsoftware.org/isdl.php
    echo         Instala y vuelve a ejecutar este script.
    pause & exit /b 1
)
echo [OK]   Inno Setup: !ISCC!

:: --- Servidor de RustDesk (4.12.0): ya compilado; no se compila aqui ---
if not exist "SERVIDOR_RUSTDESK\bin\hbbs.exe" (
    echo [ERROR] Falta SERVIDOR_RUSTDESK\bin\hbbs.exe.
    echo         Ejecuta antes SERVIDOR_RUSTDESK\compilar_servidor.bat ^(Rust + Build Tools^).
    pause & exit /b 1
)
if not exist "SERVIDOR_RUSTDESK\bin\hbbr.exe" (
    echo [ERROR] Falta SERVIDOR_RUSTDESK\bin\hbbr.exe.
    echo         Ejecuta antes SERVIDOR_RUSTDESK\compilar_servidor.bat ^(Rust + Build Tools^).
    pause & exit /b 1
)
echo [OK]   Servidor de RustDesk: SERVIDOR_RUSTDESK\bin

:: --- Cerrar MASys compilado si esta abierto - como MAOSI - y limpiar builds anteriores ---
echo.
echo [INFO]  Limpiando builds anteriores...
taskkill /IM MASys.exe /F >nul 2>&1
if exist "INSTALL\build\MASys" rmdir /s /q "INSTALL\build\MASys"
if exist "INSTALL\dist\MASys"  rmdir /s /q "INSTALL\dist\MASys"
if exist "INSTALL\dist\MASys_Portable_v!ISS_VER!"     rmdir /s /q "INSTALL\dist\MASys_Portable_v!ISS_VER!"
if exist "INSTALL\dist\MASys_Portable_v!ISS_VER!.zip" del /q "INSTALL\dist\MASys_Portable_v!ISS_VER!.zip"
if exist "INSTALL\dist\MASys_Agente_v!ISS_VER!.exe"   del /q "INSTALL\dist\MASys_Agente_v!ISS_VER!.exe"
if exist "INSTALL\dist\MASys" (
    echo [ERROR] No se pudo borrar INSTALL\dist\MASys: MASys.exe sigue abierto.
    echo         Cierralo y vuelve a ejecutar este script.
    pause & exit /b 1
)
echo [OK]   Limpieza completada.

:: --- Icono: INSTALL\masys.ico, mismo dibujo que la ventana - msys\ui\icono_app.py ---
echo.
echo [INFO]  Generando icono...
!PYTHON_CMD! INSTALL\create_icon.py
if !ERRORLEVEL! NEQ 0 (
    echo [ERROR] No se pudo generar INSTALL\masys.ico.
    pause & exit /b 1
)
if not exist "INSTALL\masys.ico" (
    echo [ERROR] INSTALL\masys.ico no fue generado.
    pause & exit /b 1
)
echo [OK]   Icono generado: INSTALL\masys.ico

:: --- PyInstaller ---
echo.
echo [INFO]  Compilando aplicacion con PyInstaller...
echo         Esto puede tardar 2-5 minutos.
echo.
!PYTHON_CMD! -m PyInstaller INSTALL\MASys.spec --distpath INSTALL\dist --workpath INSTALL\build --noconfirm
if !ERRORLEVEL! NEQ 0 (
    echo.
    echo [ERROR] PyInstaller fallo. Revisa los mensajes anteriores.
    pause & exit /b 1
)

if not exist "INSTALL\dist\MASys\MASys.exe" (
    echo [ERROR] INSTALL\dist\MASys\MASys.exe no fue generado.
    echo         Comprueba que MASys.spec apunta al nombre correcto.
    pause & exit /b 1
)
echo [OK]   Compilacion completada: INSTALL\dist\MASys\MASys.exe

:: --- Agente de analisis: un solo .exe con la version en el nombre (se envia al cliente) ---
echo.
echo [INFO]  Compilando el agente de analisis...
!PYTHON_CMD! -m PyInstaller INSTALL\MASys_Agente.spec --distpath INSTALL\dist --workpath INSTALL\build --noconfirm
if !ERRORLEVEL! NEQ 0 (
    echo.
    echo [ERROR] PyInstaller fallo al compilar el agente. Revisa los mensajes anteriores.
    pause & exit /b 1
)
if not exist "INSTALL\dist\MASys_Agente_v!ISS_VER!.exe" (
    echo [ERROR] INSTALL\dist\MASys_Agente_v!ISS_VER!.exe no fue generado.
    pause & exit /b 1
)
echo [OK]   Agente: INSTALL\dist\MASys_Agente_v!ISS_VER!.exe

:: --- Version portable: misma compilacion + SCRIPTS, manual, ficha y LEEME, en carpeta y .zip ---
echo.
echo [INFO]  Generando version portable...
!PYTHON_CMD! INSTALL\build_portable.py
if !ERRORLEVEL! NEQ 0 (
    echo.
    echo [ERROR] No se pudo generar la version portable.
    pause & exit /b 1
)
if not exist "INSTALL\dist\MASys_Portable_v!ISS_VER!.zip" (
    echo [ERROR] No se encontro INSTALL\dist\MASys_Portable_v!ISS_VER!.zip
    pause & exit /b 1
)


:: --- Formatos del estandar Masoft (12/11/2026, modelo MAScrShot): exe unico y portable ---
:: --- PyInstaller: exe unico (los de versiones anteriores se conservan en dist\unico) ---
echo.
echo [INFO]  Compilando el exe unico (onefile)...
if exist "INSTALL\build_unico" rmdir /s /q "INSTALL\build_unico"
if exist "INSTALL\dist\unico\MASys_v!ISS_VER!.exe" del /q "INSTALL\dist\unico\MASys_v!ISS_VER!.exe"
!PYTHON_CMD! -m PyInstaller INSTALL\MASys_unico.spec --distpath INSTALL\dist\unico --workpath INSTALL\build_unico --noconfirm
if !ERRORLEVEL! NEQ 0 (
    echo [ERROR] PyInstaller fallo con el exe unico. Revisa los mensajes anteriores.
    pause & exit /b 1
)
if not exist "INSTALL\dist\unico\MASys_v!ISS_VER!.exe" (
    echo [ERROR] INSTALL\dist\unico\MASys_v!ISS_VER!.exe no fue generado.
    pause & exit /b 1
)
echo [OK]   Exe unico: INSTALL\dist\unico\MASys_v!ISS_VER!.exe

:: --- Inno Setup ---
echo.
echo [INFO]  Generando instalador con Inno Setup...
"!ISCC!" INSTALL\setup.iss
if !ERRORLEVEL! NEQ 0 (
    echo.
    echo [ERROR] Inno Setup fallo al compilar el instalador.
    echo         Revisa los mensajes anteriores o abre INSTALL\setup.iss en Inno Setup IDE.
    pause & exit /b 1
)

:: --- Verificar que el instalador se genero ---
if not exist "INSTALL\dist\MASys_Setup_v!ISS_VER!.exe" (
    echo.
    echo [ERROR] Inno Setup termino sin error pero no se encontro el instalador en INSTALL\dist\
    echo         Revisa OutputDir y OutputBaseFilename en setup.iss.
    pause & exit /b 1
)

:: --- Resultado ---
echo.
echo ============================================================
echo  INSTALADOR Y PORTABLE GENERADOS CORRECTAMENTE
echo ============================================================
for %%f in ("INSTALL\dist\MASys_Setup_v!ISS_VER!.exe") do echo  Instalador : %%~nxf - %%~zf bytes
for %%f in ("INSTALL\dist\MASys_Portable_v!ISS_VER!.zip") do echo  Portable   : %%~nxf - %%~zf bytes
for %%f in ("INSTALL\dist\MASys_Agente_v!ISS_VER!.exe") do echo  Agente     : %%~nxf - %%~zf bytes
for %%f in ("INSTALL\dist\MASys_Setup_v!ISS_VER!.exe") do echo  Ruta       : %%~dpf
echo.
echo  Contenido instalado en el equipo destino:
echo    {app}\MASys.exe           - Aplicacion principal, pide administrador al abrirse
echo    {app}\_internal\          - Runtime de Python, PyQt5 y librerias
echo    {app}\SCRIPTS\            - Catalogo de scripts, no se sobrescribe al actualizar
echo    {app}\AGENTE\             - Agente de analisis para enviar a los clientes
echo    multisys.db, movil.db, INFORMES\, BACKUPS\ - se crean al usarla, nunca se borran
echo ============================================================
echo.
pause
