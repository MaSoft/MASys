# -*- mode: python ; coding: utf-8 -*-
# MASys - especificacion PyInstaller (modo onedir)
# Genera: INSTALL\dist\MASys\MASys.exe + _internal\ (runtime de Python, PyQt5 y dependencias)
# Uso (lo hace build_install.bat):
#   python -m PyInstaller INSTALL\MASys.spec --distpath INSTALL\dist --workpath INSTALL\build --noconfirm
#
# Vive en INSTALL\: las rutas se anclan a la raiz del proyecto con SPECPATH (lo inyecta PyInstaller).
# El punto de entrada sigue siendo multisys.py: el LAUNCHER de produccion lo arranca con ese nombre.
import glob
import os

from PyQt5.QtCore import QLibraryInfo

PROJECT_ROOT = os.path.abspath(os.path.join(SPECPATH, '..'))
ICONO = os.path.join(SPECPATH, 'masys.ico')   # lo genera create_icon.py en el paso 2

# Traduccion base de Qt al espanol (Aceptar, Cancelar, Si, No... de QMessageBox y
# QDialogButtonBox). _cargar_traduccion_qt() de multisys.py la busca en _MEIPASS\PyQt5\Qt5\translations.
_qt_translations_dir = QLibraryInfo.location(QLibraryInfo.TranslationsPath)
_qt_translations_datas = [
    (os.path.join(_qt_translations_dir, nombre), 'PyQt5/Qt5/translations')
    for nombre in ('qtbase_es.qm', 'qt_es.qm')
    if os.path.isfile(os.path.join(_qt_translations_dir, nombre))
]

a = Analysis(
    [os.path.join(PROJECT_ROOT, 'multisys.py')],
    pathex=[PROJECT_ROOT],
    binaries=[],
    datas=_qt_translations_datas + [
        # Captura forense: msys_config.ruta_recurso() los busca primero en _MEIPASS (_internal\).
        # Uno a uno, no la carpeta: alli tambien hay copias .bak que no deben ir al programa.
        (os.path.join(PROJECT_ROOT, 'msys', 'tools', 'MaPeriT.SW.ps1'), 'msys/tools'),
        (os.path.join(PROJECT_ROOT, 'msys', 'tools', 'MaPeriT.OF.ps1'), 'msys/tools'),
        # 5.5.0: registro de fabricantes del IEEE para Red local (msys/models/rows.py, _oui_ieee)
        (os.path.join(PROJECT_ROOT, 'msys', 'datos', 'oui_ieee.tsv.gz'), 'msys/datos'),
    ] + [
        # 5.6.0: herramientas de diagnóstico de Windows (copia propia de _TEMP\SCRIPTS_2) y sus guías; sin .bak
        (f, 'msys/tools/win') for f in sorted(glob.glob(os.path.join(PROJECT_ROOT, 'msys', 'tools', 'win', '*.ps1')))
        + sorted(glob.glob(os.path.join(PROJECT_ROOT, 'msys', 'tools', 'win', 'Guia_*.html')))
        + [os.path.join(PROJECT_ROOT, 'msys', 'tools', 'win', n) for n in ('sqlite3.exe', 'SQLITE_FUENTE.txt')]
    ] + [
        # 5.7.0: herramientas de Microsoft 365 (copia propia de _TEMP\SCRIPTS_2), cada una en su carpeta
        (f, 'msys/tools/m365/' + os.path.basename(os.path.dirname(f)))
        for patron in ('*.ps1', 'Guia_*.html', 'plantilla_*.csv')
        for f in sorted(glob.glob(os.path.join(PROJECT_ROOT, 'msys', 'tools', 'm365', '*', patron)))
    ],
    hiddenimports=[
        # pywintypes lo necesita en ejecucion (Servicios) y PyInstaller no lo detecta solo.
        'win32timezone',
        # 4.9.0 Visor PST/OST: pypff (libpff, LGPL) es un solo .pyd que msys/domain/correo/pst.py importa
        # dentro de las funciones, así que PyInstaller no lo ve solo
        'pypff',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Solo lo que seguro no se usa. NO excluir distutils/setuptools/email/http/urllib
    # (rompen el analisis en 3.12 o el arranque del exe).
    excludes=['tkinter', 'pytest', '_pytest'],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MASys',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,          # sin UPX: menos falsos positivos de antivirus
    console=False,      # app de ventana, sin consola
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICONO,
    uac_admin=True,     # como el .spec anterior de MultiSYS: casi todas las secciones necesitan administrador
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='MASys',
)
