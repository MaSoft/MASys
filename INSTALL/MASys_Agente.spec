# -*- mode: python ; coding: utf-8 -*-
# MASys · Agente de analisis (4.7.0) - especificacion PyInstaller (UN SOLO .exe, onefile)
# Genera: INSTALL\dist\MASys_Agente_v<version>.exe  (se envia al cliente; solo lee)
# Uso (lo hace build_install.bat):
#   python -m PyInstaller INSTALL\MASys_Agente.spec --distpath INSTALL\dist --workpath INSTALL\build --noconfirm
#
# Version: la de msys\version.py (unico punto de verdad; setup.iss la iguala).
import os
import re

from PyQt5.QtCore import QLibraryInfo

PROJECT_ROOT = os.path.abspath(os.path.join(SPECPATH, '..'))
ICONO = os.path.join(SPECPATH, 'masys.ico')   # lo genera create_icon.py

with open(os.path.join(PROJECT_ROOT, 'msys', 'version.py'), encoding='utf-8') as f:
    VERSION = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', f.read()).group(1)

_qt_translations_dir = QLibraryInfo.location(QLibraryInfo.TranslationsPath)
_qt_translations_datas = [
    (os.path.join(_qt_translations_dir, nombre), 'PyQt5/Qt5/translations')
    for nombre in ('qtbase_es.qm', 'qt_es.qm')
    if os.path.isfile(os.path.join(_qt_translations_dir, nombre))
]

a = Analysis(
    [os.path.join(PROJECT_ROOT, 'agente.py')],
    pathex=[PROJECT_ROOT],
    binaries=[],
    # 5.5.0: registro de fabricantes del IEEE (Red local también se lee en el agente)
    datas=_qt_translations_datas + [(os.path.join(PROJECT_ROOT, 'msys', 'datos', 'oui_ieee.tsv.gz'), 'msys/datos')],
    hiddenimports=['win32timezone'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'pytest', '_pytest', 'openpyxl', 'PIL', 'qrcode'],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=f'MASys_Agente_v{VERSION}',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # sin UPX: menos falsos positivos de antivirus
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICONO,
    uac_admin=True,     # casi todas las lecturas necesitan administrador
)
