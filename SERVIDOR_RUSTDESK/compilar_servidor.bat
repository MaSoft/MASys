@echo off
setlocal EnableExtensions EnableDelayedExpansion
:: ============================================================================
::  MASys - Compilar el servidor de RustDesk (hbbs.exe y hbbr.exe) desde el codigo
::  Origen y cambios: ver FUENTE.txt. Resultado: bin\hbbs.exe y bin\hbbr.exe
::  Requiere: Git, Rust (rustup) y Visual Studio Build Tools 2022 con C++
::  No borra nada: reutiliza fuente\ si ya existe (solo comprueba el commit).
:: ============================================================================
set "AQUI=%~dp0"
set "FUENTE=%AQUI%fuente"
set "ETIQUETA=1.1.16"
set "COMMIT=73523b31cfd25d77dee862e6fc9f5e1fb5e485ef"
set "RUST=1.90"
set "DESTINO=x86_64-pc-windows-msvc"
set "PATH=%USERPROFILE%\.cargo\bin;%PATH%"

where git >nul 2>&1 || (echo [ERROR] No se encuentra Git. & goto :fallo)
where rustup >nul 2>&1 || (echo [ERROR] No se encuentra rustup ^(Rust^). Instalar: winget install Rustlang.Rustup & goto :fallo)

echo [INFO]  Rust %RUST% para %DESTINO%...
rustup toolchain install %RUST% --profile minimal --target %DESTINO% || (echo [ERROR] No se pudo preparar Rust %RUST%. & goto :fallo)

if not exist "%FUENTE%\.git" (
    echo [INFO]  Descargando rustdesk-server %ETIQUETA%...
    git clone --quiet --branch %ETIQUETA% --depth 1 https://github.com/rustdesk/rustdesk-server.git "%FUENTE%" || (echo [ERROR] No se pudo descargar el codigo. & goto :fallo)
)
for /f %%c in ('git -C "%FUENTE%" rev-parse HEAD') do set "ACTUAL=%%c"
if /i not "!ACTUAL!"=="%COMMIT%" (
    echo [ERROR] fuente\ esta en el commit !ACTUAL! y se esperaba %COMMIT%.
    echo         Mueve o renombra fuente\ y vuelve a lanzar este archivo.
    goto :fallo
)
git -C "%FUENTE%" submodule update --init --depth 1 || (echo [ERROR] No se pudo descargar hbb_common. & goto :fallo)

git -C "%FUENTE%" apply --reverse --check "%AQUI%masys.patch" >nul 2>&1
if errorlevel 1 (
    echo [INFO]  Aplicando masys.patch...
    git -C "%FUENTE%" apply "%AQUI%masys.patch" || (echo [ERROR] El parche no se aplica sobre este codigo. & goto :fallo)
) else (
    echo [OK]    masys.patch ya estaba aplicado.
)

echo [INFO]  Compilando ^(varios minutos la primera vez^)...
cargo +%RUST% build --release --locked --all-features --target %DESTINO% --manifest-path "%FUENTE%\Cargo.toml" || (echo [ERROR] La compilacion fallo. & goto :fallo)

if not exist "%AQUI%bin" mkdir "%AQUI%bin"
for %%p in (hbbs hbbr) do (
    copy /y "%FUENTE%\target\%DESTINO%\release\%%p.exe" "%AQUI%bin\%%p.exe" >nul || (echo [ERROR] No se pudo copiar %%p.exe. & goto :fallo)
    echo [OK]    bin\%%p.exe
)
echo.
echo [OK]    Servidor compilado. Actualiza las huellas SHA-256 de FUENTE.txt si han cambiado.
endlocal
exit /b 0

:fallo
endlocal
exit /b 1
