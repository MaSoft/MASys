# -*- coding: utf-8 -*-
"""Utilidades generales: traduccion de permisos, helpers de registro de
Windows, ejecucion de PowerShell, exportacion CSV segura, deteccion de
software EOL/lista negra y sistema de checkpoints de analisis.
Extraido de multisys.py (Fase 1 de modularizacion)."""

import os
import re
import ctypes
import winreg
import subprocess
import json
import sqlite3
import tempfile
import locale
import contextlib
from datetime import datetime
from typing import List, Optional, Dict, Any, Tuple
from xml.sax.saxutils import escape as _escapar_xml

import win32crypt
from PyQt5.QtCore import QSettings, Qt
from PyQt5.QtWidgets import (
    QFileDialog, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
)

from msys import config as msys_config
from msys.config import logger, CHECKPOINT_FILE, DEFAULT_TIMEOUT
from msys.ps_scripts import PS_CHECK_SMBSHARE, PS_WINGET_PIN_LIST

# Traducción de permisos al español
PERMISOS_ES = {
    'Full': 'Control Total',
    'FullControl': 'Control Total',
    'FullControl (GenericAll)': 'Control Total',
    'Read': 'Lectura',
    'ReadAndExecute': 'Lectura y Ejecución',
    'ReadAndExecute, Synchronize': 'Lectura y Ejecución',
    'Write': 'Escritura',
    'Change': 'Modificar',
    'Modify': 'Modificar',
    'Modify, Synchronize': 'Modificar',
    'ListDirectory': 'Listar Contenido',
    'Delete': 'Eliminar'
}

TIPOS_ES = {
    'Allow': 'Permitir',
    'Deny': 'Denegar'
}

def traducir_permiso(permiso_en: str) -> str:
    """Traduce permiso de inglés a español."""
    if permiso_en in PERMISOS_ES:
        return PERMISOS_ES[permiso_en]
    for ing, esp in PERMISOS_ES.items():
        if ing in permiso_en:
            return permiso_en.replace(ing, esp)
    return permiso_en

def traducir_tipo(tipo_en: str) -> str:
    """Traduce tipo de acceso de inglés a español."""
    return TIPOS_ES.get(tipo_en, tipo_en)

_FILE_ATTRIBUTE_REPARSE_POINT = 0x400

def _es_reparse_point(path: str) -> bool:
    """Compartido por cualquier recorrido de disco que use os.walk/recursión
    propia: las junctions de NTFS (p. ej. 'C:\\Documents and Settings' ->
    'C:\\Users') no se detectan con followlinks=False, hay que comprobar el
    bit reparse point explícitamente o el recorrido puede colgarse en un
    bucle."""
    try:
        return bool(os.stat(path, follow_symlinks=False).st_file_attributes
                     & _FILE_ATTRIBUTE_REPARSE_POINT)
    except OSError:
        return False

def limpiar_destino_credential(destino: str) -> str:
    """Limpia y hace más legible el destino de una credencial."""
    if not destino:
        return destino

    # Eliminar prefijos comunes que ensucian la lectura
    prefijos = [
        "LegacyGeneric:target=",
        "MicrosoftAccount:target=",
        "Domain:target=",
        "DomainPassword:target=",
        "Generic:target=",
        "WindowsLive:target=",
    ]

    destino_limpio = destino
    for prefijo in prefijos:
        if destino_limpio.startswith(prefijo):
            destino_limpio = destino_limpio[len(prefijo):]
            break

    return destino_limpio

def clasificar_tipo_destino(destino: str, tipo: str) -> str:
    """Clasifica el tipo de destino para filtros."""
    destino_upper = destino.upper()

    if "TERMSRV/" in destino_upper or "MSTSC" in destino_upper:
        return "Escritorio Remoto"
    elif destino.startswith("\\\\") or "SMB" in destino_upper:
        return "Recurso de Red"
    elif "MICROSOFT" in destino_upper or "LIVE" in destino_upper or "ONEDRIVE" in destino_upper:
        return "Microsoft/Office365"
    elif "VIRTUALAPP" in destino_upper or "CLOUDPC" in destino_upper:
        return "Windows 365"
    elif any(x in destino_upper for x in ["GIT", "GITHUB", "GITLAB"]):
        return "Control de Versiones"
    elif "DOMAIN" in tipo.upper():
        return "Contraseña de Dominio"
    else:
        return "Genérico"

# ---------------------------
# Utilidades
# ---------------------------

def is_admin() -> bool:
    """Verifica si el proceso tiene permisos de administrador."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception as e:
        logger.error(f"Error comprobando permisos de administrador: {e}")
        return False

def get_downloads_folder() -> str:
    """Devuelve la carpeta de Descargas real del usuario de Windows (respeta
    reubicaciones vía OneDrive u otras, leyendo el registro), con fallback a
    ~\\Downloads si el registro no está disponible."""
    try:
        import winreg
        guid_descargas = "{374DE290-123F-4565-9164-39C4925E467B}"
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
        ) as key:
            ruta, _ = winreg.QueryValueEx(key, guid_descargas)
            ruta = os.path.expandvars(ruta)
            if os.path.isdir(ruta):
                return ruta
    except Exception as e:
        logger.warning(f"No se pudo leer la carpeta de Descargas del registro: {e}")
    return os.path.join(os.path.expanduser("~"), "Downloads")

_CARPETAS_PERFIL_VALOR_REGISTRO = {
    # Las 5 clásicas se guardan por NOMBRE (no GUID) en esta misma clave
    # "Shell Folders" — confirmado con 'reg query' en esta máquina: solo
    # Descargas (añadida en Windows 7, sin nombre legacy) usa su GUID de
    # KNOWNFOLDERID; Documentos/Escritorio/Imágenes/Vídeos/Música son
    # "Personal"/"Desktop"/"My Pictures"/"My Video"/"My Music". Una
    # primera versión que probó GUID para las 5 fallaba en silencio
    # (FileNotFoundError capturado) y solo encontraba Descargas.
    "Documentos": "Personal",
    "Escritorio": "Desktop",
    "Imágenes": "My Pictures",
    "Videos": "My Video",
    "Música": "My Music",
    "Descargas": "{374DE290-123F-4565-9164-39C4925E467B}",
}

def get_user_content_folders() -> Dict[str, str]:
    """Mismas carpetas de perfil que agrega PS_GET_USER_CONTENT (ver
    msys/ps_scripts.py) — vía registro (Shell Folders), igual que
    get_downloads_folder(), para que 'Búsqueda avanzada' de Contenido
    busque sobre las mismas rutas que ya muestra el inventario y respete
    reubicaciones (OneDrive, unidad D: separada, etc.) en vez de asumir
    ~\\Documents fijo."""
    carpetas: Dict[str, str] = {}
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
        ) as key:
            for nombre, valor_registro in _CARPETAS_PERFIL_VALOR_REGISTRO.items():
                try:
                    ruta, _ = winreg.QueryValueEx(key, valor_registro)
                    ruta = os.path.expandvars(ruta)
                    if os.path.isdir(ruta):
                        carpetas[nombre] = ruta
                except (FileNotFoundError, OSError):
                    continue
    except Exception as e:
        logger.warning(f"No se pudieron leer las carpetas de perfil del registro: {e}")

    appdata = os.environ.get("APPDATA")
    if appdata and os.path.isdir(appdata):
        carpetas["AppData\\Roaming"] = appdata
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata and os.path.isdir(localappdata):
        carpetas["AppData\\Local"] = localappdata

    return carpetas

def startupinfo_oculto() -> subprocess.STARTUPINFO:
    """STARTUPINFO para lanzar un ejecutable de consola (reg.exe, pnputil...)
    sin que asome su ventana — usar junto con
    creationflags=subprocess.CREATE_NO_WINDOW. Helper independiente de self
    (a diferencia de ShareInspector._get_startupinfo) para poder usarse
    desde clases de dominio (VarManager...) o QThread que no son la ventana
    principal. Solo importa en la app empaquetada (sin consola propia): en
    modo desarrollo, lanzada desde una terminal, los procesos hijos ya
    heredan esa consola y no se nota la falta de esto."""
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = subprocess.SW_HIDE
    return si

OUTLOOK_VERSIONS_CONOCIDAS = ["16.0", "15.0", "14.0", "12.0", "11.0", "10.0", "9.0"]


def detectar_versiones_outlook_instaladas() -> List[str]:
    """Versiones de Office/Outlook con un perfil de correo configurado en
    este perfil de usuario (HKCU\\...\\Office\\{ver}\\Outlook\\Profiles
    existe). Portado de GestCorreov4.ps1 (Get-OutlookVersions)."""
    encontradas = []
    for ver in OUTLOOK_VERSIONS_CONOCIDAS:
        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                f"Software\\Microsoft\\Office\\{ver}\\Outlook\\Profiles",
            ):
                encontradas.append(ver)
        except FileNotFoundError:
            continue
        except OSError:
            continue
    return encontradas


def get_informes_folder() -> str:
    """Carpeta INFORMES junto al script/ejecutable: destino por defecto de
    todos los CSV, informes HTML/PDF y resultados exportados (antes era la
    carpeta de Descargas del sistema). Se crea si no existe."""
    base_dir = msys_config.app_base_dir()
    carpeta = os.path.join(base_dir, "INFORMES")
    try:
        os.makedirs(carpeta, exist_ok=True)
    except Exception as e:
        logger.warning(f"No se pudo crear la carpeta INFORMES ({carpeta}): {e}")
        return base_dir
    return carpeta

def ruta_guardado_predeterminada(nombre_archivo: str) -> str:
    """Compone la ruta por defecto que se ofrece en los diálogos de guardado:
    carpeta INFORMES junto al script + nombre de archivo sugerido."""
    return os.path.join(get_informes_folder(), nombre_archivo)

@contextlib.contextmanager
def _sqlite_connect(path: str):
    """Sustituye a 'sqlite3.connect(path)' como valor de retorno de _conn()
    en TODAS las *NotesDB/*LogDB/*WhitelistDB/RepairAuditDB de la app — sin
    tocar ninguno de sus 'with self._conn() as conn:' existentes.

    Bug real que resuelve: 'with conn:' (conn = objeto sqlite3.Connection)
    gestiona la transacción (commit/rollback) pero NO cierra la conexión —
    es un comportamiento documentado de Python, no un descuido de esta app,
    pero aquí se traducía en una conexión nueva sin cerrar por cada llamada
    a cualquier método de estas clases. Con refrescos frecuentes (el
    detector de Soporte llama a is_whitelisted() una vez por fila en CADA
    actualización de la pestaña), las conexiones sin cerrar se acumulaban
    hasta que SQLite en Windows devolvía 'database is locked' — y como eso
    saltaba dentro de un slot de Qt (conectado a worker.finished), la
    excepción sin capturar abortaba el proceso entero sin traceback."""
    conn = sqlite3.connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def _quitar_boton_ayuda(dialogo) -> None:
    dialogo.setWindowFlags(dialogo.windowFlags() & ~Qt.WindowContextHelpButtonHint)

def dialogo_guardar_archivo(parent, titulo: str, ruta_inicial: str, filtro: str) -> str:
    """Sustituye a QFileDialog.getSaveFileName(..., options=DontUseNativeDialog):
    mismo resultado (ruta elegida o "" si se cancela) pero sin el botón '?' de
    ayuda en la barra de título — el método estático no da opción de quitarlo
    porque construye y muestra la ventana de una sola vez, a diferencia de
    cualquier otro QDialog de la app, donde sí se puede quitar antes de mostrar."""
    dialogo = QFileDialog(parent, titulo, ruta_inicial, filtro)
    _quitar_boton_ayuda(dialogo)
    dialogo.setOption(QFileDialog.DontUseNativeDialog, True)
    dialogo.setAcceptMode(QFileDialog.AcceptSave)
    dialogo.setFileMode(QFileDialog.AnyFile)
    if dialogo.exec_() != QDialog.Accepted:
        return ""
    archivos = dialogo.selectedFiles()
    return archivos[0] if archivos else ""

def dialogo_abrir_archivo(parent, titulo: str, ruta_inicial: str, filtro: str) -> str:
    """Misma idea que dialogo_guardar_archivo pero para abrir un archivo
    existente (sustituye a QFileDialog.getOpenFileName con DontUseNativeDialog)."""
    dialogo = QFileDialog(parent, titulo, ruta_inicial, filtro)
    _quitar_boton_ayuda(dialogo)
    dialogo.setOption(QFileDialog.DontUseNativeDialog, True)
    dialogo.setFileMode(QFileDialog.ExistingFile)
    dialogo.setAcceptMode(QFileDialog.AcceptOpen)
    if dialogo.exec_() != QDialog.Accepted:
        return ""
    archivos = dialogo.selectedFiles()
    return archivos[0] if archivos else ""

def dialogo_elegir_carpeta(parent, titulo: str, ruta_inicial: str) -> str:
    """Misma idea que dialogo_guardar_archivo pero para elegir una carpeta
    (sustituye a QFileDialog.getExistingDirectory con DontUseNativeDialog)."""
    dialogo = QFileDialog(parent, titulo, ruta_inicial)
    _quitar_boton_ayuda(dialogo)
    dialogo.setOption(QFileDialog.DontUseNativeDialog, True)
    dialogo.setOption(QFileDialog.ShowDirsOnly, True)
    dialogo.setFileMode(QFileDialog.Directory)
    if dialogo.exec_() != QDialog.Accepted:
        return ""
    archivos = dialogo.selectedFiles()
    return archivos[0] if archivos else ""

def ruta_destino_sin_colision(carpeta_destino: str, nombre: str) -> str:
    """Añade ' (2)', ' (3)'... al nombre si ya existe en carpeta_destino.
    Compartida entre los diálogos que ofrecen 'Renombrar' ante colisión
    (RecycleBinDialog, ContenidoMixin) y sus hilos de movimiento/copia
    (aplican la elección ya resuelta, sin volver a preguntar nada — no
    puede haber UI dentro de un QThread)."""
    candidato = os.path.join(carpeta_destino, nombre)
    if not os.path.exists(candidato):
        return candidato
    base, ext = os.path.splitext(nombre)
    contador = 2
    while True:
        candidato = os.path.join(carpeta_destino, f"{base} ({contador}){ext}")
        if not os.path.exists(candidato):
            return candidato
        contador += 1

def preguntar_colision(parent, nombre: str) -> Tuple[Optional[str], bool]:
    """Ya existe «nombre» en la carpeta de destino: pregunta qué hacer en
    vez de renombrar/sobrescribir en silencio. Devuelve (accion,
    aplicar_a_todos) con accion en {'sobrescribir', 'renombrar', 'omitir'},
    o (None, False) si el usuario cancela el resto de la operación.

    Compartido entre RecycleBinDialog ('Restaurar en otra ubicación') y
    ContenidoMixin ('Copiar/Mover seleccionados a...') — misma decisión,
    mismo diálogo, en vez de duplicar la ventana en cada sitio."""
    dialog = QDialog(parent)
    dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
    dialog.setWindowTitle("Ya existe en destino")
    dialog.setMinimumWidth(440)
    v = QVBoxLayout(dialog)
    v.addWidget(QLabel(f"Ya existe «{nombre}» en la carpeta de destino.\n¿Qué quieres hacer?"))

    chk_todos = QCheckBox("Aplicar esta misma acción al resto de conflictos")
    v.addWidget(chk_todos)

    botones = QHBoxLayout()
    btn_sobrescribir = QPushButton("Sobrescribir")
    btn_renombrar = QPushButton("Renombrar (conservar ambos)")
    btn_omitir = QPushButton("Omitir")
    btn_cancelar = QPushButton("Cancelar")
    for boton in (btn_sobrescribir, btn_renombrar, btn_omitir, btn_cancelar):
        botones.addWidget(boton)
    v.addLayout(botones)

    eleccion = {}

    def _elegir(valor):
        eleccion["accion"] = valor
        dialog.accept()

    btn_sobrescribir.clicked.connect(lambda: _elegir("sobrescribir"))
    btn_renombrar.clicked.connect(lambda: _elegir("renombrar"))
    btn_omitir.clicked.connect(lambda: _elegir("omitir"))
    btn_cancelar.clicked.connect(dialog.reject)

    if dialog.exec_() != QDialog.Accepted or "accion" not in eleccion:
        return None, False
    return eleccion["accion"], chk_todos.isChecked()

def render_html_a_pdf(html: str, path: str) -> None:
    """Convierte HTML a PDF con QTextDocument + QPdfWriter.

    Deliberadamente NO se usa QPrinter: en Windows, construir un QPrinter
    consulta la impresora predeterminada del sistema, y si esa impresora es
    de red o está desconectada, el propio Windows muestra el diálogo modal
    'Esperando a la conexión de la impresora' aunque el destino final sea un
    PDF. QPdfWriter escribe el PDF directamente, sin tocar el spooler.
    """
    from PyQt5.QtGui import QTextDocument, QPdfWriter, QPageLayout
    from PyQt5.QtCore import QMarginsF, QSizeF

    doc = QTextDocument()
    if '<div class="cat-block">' not in html:
        doc.setHtml(html)

    writer = QPdfWriter(path)
    writer.setResolution(96)
    writer.setPageSize(QPdfWriter.A4)
    writer.setPageMargins(QMarginsF(15, 15, 15, 15), QPageLayout.Millimeter)

    # Ajusta el documento al tamaño real de página (en píxeles, a la
    # resolución del writer) para que los saltos de página caigan entre
    # filas de tabla y no las corten.
    page_rect = writer.pageLayout().paintRectPixels(writer.resolution())
    marca = '<div class="cat-block">'
    if marca not in html:
        doc.setPageSize(QSizeF(page_rect.size()))
        doc.print_(writer)
        return
    # 5.4.2: informes con varias secciones (cada una empieza en página nueva): un QTextDocument por
    # sección. Maquetar una tabla enorme cuesta más que proporcional; medido 1,7 veces más rápido con el
    # mismo PDF, y entre sección y sección la ventana procesa sus eventos (antes quedaba congelada).
    import re as _re
    from PyQt5.QtCore import QRectF
    from PyQt5.QtGui import QPainter
    from PyQt5.QtWidgets import QApplication
    i = html.index(">", html.index("<body")) + 1
    cabeza, cuerpo = html[:i], html[i:]
    trozos = cuerpo.split(marca)
    cabeza_sin_salto = _re.sub(r"page-break-before:\s*always;?", "", cabeza)
    partes = [cabeza + trozos[0] + "</body></html>"] + [cabeza_sin_salto + marca + t for t in trozos[1:]]
    tam = QSizeF(page_rect.size())
    pintor = QPainter(writer)
    primera = True
    try:
        for parte in partes:
            d = QTextDocument()
            d.setHtml(parte)
            d.setPageSize(tam)
            for n in range(d.pageCount()):
                if not primera:
                    writer.newPage()
                primera = False
                pintor.save()
                pintor.translate(0, -n * tam.height())
                d.drawContents(pintor, QRectF(0, n * tam.height(), tam.width(), tam.height()))
                pintor.restore()
            app = QApplication.instance()
            if app is not None:
                app.processEvents()
    finally:
        pintor.end()

# ---------------------------
# Registro de Windows — constantes y helpers compartidos
# ---------------------------

# (constante winreg, nombre completo para "reg export"/regedit, orden de
# escaneo por defecto). El orden importa: HKCU/HKLM primero (los más útiles
# y rápidos), HKCR al final porque es una vista fusionada de
# HKLM\SOFTWARE\Classes + HKCU\SOFTWARE\Classes y solapa con ambos.
REG_HIVES = {
    "HKCU": (winreg.HKEY_CURRENT_USER, "HKEY_CURRENT_USER"),
    "HKLM": (winreg.HKEY_LOCAL_MACHINE, "HKEY_LOCAL_MACHINE"),
    "HKU":  (winreg.HKEY_USERS, "HKEY_USERS"),
    "HKCC": (winreg.HKEY_CURRENT_CONFIG, "HKEY_CURRENT_CONFIG"),
    "HKCR": (winreg.HKEY_CLASSES_ROOT, "HKEY_CLASSES_ROOT"),
}

REG_TYPES = {
    winreg.REG_NONE: "REG_NONE",
    winreg.REG_SZ: "REG_SZ",
    winreg.REG_EXPAND_SZ: "REG_EXPAND_SZ",
    winreg.REG_BINARY: "REG_BINARY",
    winreg.REG_DWORD: "REG_DWORD",
    winreg.REG_DWORD_BIG_ENDIAN: "REG_DWORD_BE",
    winreg.REG_LINK: "REG_LINK",
    winreg.REG_MULTI_SZ: "REG_MULTI_SZ",
    winreg.REG_QWORD: "REG_QWORD",
}

# Primer nivel de cada hive que NO se puede eliminar desde esta herramienta
# (fila roja): borrar cualquiera de estas ramas completas deja Windows
# inarrancable o corrompe el perfil de usuario. Comparación case-insensitive
# sobre el primer segmento de la ruta.
_REG_BLOQUEADO_PRIMER_NIVEL = {
    "HKLM": {"system", "software", "hardware", "sam", "security",
             "bcd00000000", "components", "drivers"},
    "HKCU": {"software"},
    "HKCR": set(),
    "HKU": set(),
    "HKCC": set(),
}

# Prefijos de ruta (case-insensitive, dentro del hive) considerados
# críticos/de sistema: eliminables, pero con aviso reforzado (fila naranja).
_REG_CRITICO_PREFIJOS = {
    "HKLM": [
        r"system\currentcontrolset\services",
        r"system\currentcontrolset\control",
        r"software\microsoft\windows nt\currentversion",
        r"software\microsoft\windows\currentversion\run",
        r"software\microsoft\windows\currentversion\runonce",
        r"software\classes\clsid",
    ],
    "HKCU": [
        r"software\microsoft\windows\currentversion\run",
        r"software\microsoft\windows\currentversion\runonce",
    ],
    "HKCR": [r"clsid"],
    "HKU": [],
    "HKCC": [],
}


def clasificar_riesgo_registro(hive_abbr: str, subkey: str) -> Optional[str]:
    """'bloqueada' (rojo, no eliminable desde la app), 'critica' (naranja,
    eliminable con aviso reforzado) o None (sin riesgo especial) para una
    ruta de registro. subkey puede ser '' (la raíz del hive misma, siempre
    'bloqueada')."""
    if not subkey:
        return "bloqueada"
    subkey_low = subkey.lower()
    primer_nivel = subkey_low.split("\\", 1)[0]
    # El bloqueo rojo solo aplica a la rama de primer nivel EXACTA (p. ej.
    # "HKLM\SOFTWARE" a secas): comparar solo por prefijo bloquearía
    # también todo lo que cuelga debajo (prácticamente todo el registro de
    # aplicaciones), que es justo lo que la lista de críticas necesita
    # poder marcar como eliminable-con-aviso en vez de intocable.
    if subkey_low == primer_nivel and primer_nivel in _REG_BLOQUEADO_PRIMER_NIVEL.get(hive_abbr, set()):
        return "bloqueada"
    for prefijo in _REG_CRITICO_PREFIJOS.get(hive_abbr, []):
        if subkey_low == prefijo or subkey_low.startswith(prefijo + "\\"):
            return "critica"
    return None


def format_reg_data(data, reg_type) -> str:
    """Representación legible de un valor de registro según su tipo."""
    if data is None:
        return ""
    if reg_type == winreg.REG_BINARY:
        return data.hex(" ").upper() if isinstance(data, bytes) else str(data)
    if reg_type == winreg.REG_DWORD:
        return f"{data} (0x{data:08X})"
    if reg_type == winreg.REG_QWORD:
        return f"{data} (0x{data:016X})"
    if reg_type == winreg.REG_MULTI_SZ:
        return "; ".join(data) if isinstance(data, list) else str(data)
    if isinstance(data, bytes):
        return data.decode("utf-8", errors="replace")
    return str(data)


def _normalizar_ruta_nt(cadena: str) -> str:
    """Traduce prefijos de la notación de espacio de nombres NT — que
    Windows usa en el ImagePath de servicios/drivers, p. ej.
    '\\SystemRoot\\System32\\drivers\\afd.sys' — a una ruta Win32 normal.
    Sin esto, os.path.exists() busca esa ruta relativa a la unidad actual
    del proceso (nunca C:\\Windows si MASys corre desde otra unidad) y
    TODOS los drivers del sistema salen como 'inexistentes'."""
    if cadena.lower().startswith("\\systemroot\\"):
        resto = cadena[len("\\systemroot\\"):]
        return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), resto)
    if cadena.startswith("\\??\\"):
        return cadena[4:]
    return cadena


def _rutas_candidatas_ejecutable(cadena: str) -> List[str]:
    """Extrae posibles rutas de fichero de un valor tipo 'línea de comando'
    (UninstallString, ImagePath, Run...). El formato no está estandarizado
    (con/sin comillas, con/sin argumentos), así que se devuelven varias
    candidatas — basta con que UNA exista para no considerar la entrada
    rota (prioriza evitar falsos positivos sobre detectar todo)."""
    cadena = cadena.strip()
    if not cadena:
        return []
    candidatas = set()
    if cadena.startswith('"'):
        fin = cadena.find('"', 1)
        candidatas.add(cadena[1:fin] if fin != -1 else cadena[1:])
    sin_comillas = cadena.strip('"')
    candidatas.add(sin_comillas)
    # Sin comillas, Windows (CreateProcess) prueba la línea cortada en cada espacio: «C:\Program Files\X\setup.exe -l0x0a»
    # es C:\Program Files\X\setup.exe con un argumento. Antes solo se probaba hasta el PRIMER espacio («C:\Program») y el
    # desinstalador, que existe, salía como entrada huérfana para borrar.
    partes = sin_comillas.split(" ")
    for i in range(1, len(partes)):
        candidatas.add(" ".join(partes[:i]))
    return [os.path.expandvars(_normalizar_ruta_nt(c)) for c in candidatas if c]


def _existe_ejecutable(cadena: str) -> bool:
    """True si NO se puede demostrar que la referencia está rota. Solo
    devuelve False cuando se encuentra una ruta absoluta candidata y esa
    ruta no existe en disco; nombres sueltos (resueltos por el PATH del
    sistema) o referencias no verificables (p.ej. 'msiexec /X{GUID}') no
    se marcan como huérfanas."""
    if not cadena or not cadena.strip():
        return True
    if "msiexec" in cadena.lower() and "{" in cadena:
        return True
    alguna_absoluta = False
    for candidata in _rutas_candidatas_ejecutable(cadena):
        es_absoluta = os.path.isabs(candidata) or (len(candidata) > 1 and candidata[1] == ":")
        if not es_absoluta:
            continue
        alguna_absoluta = True
        # isfile y no exists: un trozo de la línea puede ser una CARPETA que existe («C:\Program Files» de
        # «C:\Program Files (x86)\Borrado\x.exe /S») y daría por bueno un ejecutable que ya no está.
        if os.path.isfile(candidata):
            return True
    return not alguna_absoluta


def wrap_with_credentials(ps_code: str) -> str:
    """Envuelve código PowerShell para usar credenciales alternativas si están configuradas."""
    # Acceso vía msys_config (no "from msys.config import ALTERNATE_CREDENTIALS"):
    # el dialogo de credenciales en ShareInspector muta el atributo del módulo
    # msys.config directamente; una copia local aquí no vería esos cambios.
    if msys_config.ALTERNATE_CREDENTIALS is None:
        # Sin credenciales alternativas, ejecutar normalmente
        return ps_code

    username, password = msys_config.ALTERNATE_CREDENTIALS

    # Escapar usuario y contraseña para PowerShell: van dentro de comillas
    # SIMPLES ('...'), donde el ÚNICO caracter especial es la propia comilla
    # simple (se escapa doblándola). Backtick y $ NO se interpretan ahí —
    # escaparlos igualmente (como se hacía antes) insertaba caracteres de
    # más en cuanto el usuario/contraseña real contuviera uno de los dos,
    # corrompiendo la credencial usada para autenticar.
    username_escaped = username.replace("'", "''")
    password_escaped = password.replace("'", "''")

    # Crear wrapper que establece credenciales y ejecuta el comando
    wrapped = f"""
$username = '{username_escaped}'
$password = ConvertTo-SecureString '{password_escaped}' -AsPlainText -Force
$cred = New-Object System.Management.Automation.PSCredential ($username, $password)

# Ejecutar comando con credenciales
Invoke-Command -Credential $cred -ScriptBlock {{
    {ps_code}
}} -ComputerName localhost
"""
    return wrapped

def _redactar_secretos(texto: str) -> str:
    """Sustituye la contraseña de las credenciales alternativas (si hay
    alguna configurada) por '***' en cualquier texto antes de registrarlo.
    Un error de PowerShell a veces repite en stderr la línea de código
    fuente donde ocurrió — si el fallo ocurre dentro del wrapper de
    wrap_with_credentials(), esa línea contiene la contraseña en texto
    plano (ConvertTo-SecureString '...')."""
    creds = msys_config.ALTERNATE_CREDENTIALS
    if not creds:
        return texto
    _username, password = creds
    if password:
        texto = texto.replace(password, "***")
    return texto

def run_powershell_json(ps_code: str, timeout: int = DEFAULT_TIMEOUT) -> Optional[object]:
    """Ejecuta PowerShell con ConvertTo-Json y devuelve el objeto Python."""
    # Force UTF-8 encoding in PowerShell output
    ps_code_with_bom = "$OutputEncoding = [System.Text.Encoding]::UTF8; [Console]::OutputEncoding = [System.Text.Encoding]::UTF8; " + ps_code

    # Aplicar credenciales si están configuradas
    ps_code_final = wrap_with_credentials(ps_code_with_bom)

    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_code_final]
    raw = None

    try:
        # Evitar ventanas de consola en Windows
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE

        result = subprocess.run(
            cmd, capture_output=True, text=False,
            timeout=timeout, check=False,
            startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW
        )

        # Decodificar con UTF-8 con BOM
        if result.returncode == 0 and result.stdout:
            raw = result.stdout.decode('utf-8-sig', errors='replace')
        elif result.returncode != 0:
            stderr_txt = _redactar_secretos((result.stderr or b'').decode('utf-8-sig', errors='replace').strip())
            logger.error(f"PowerShell devolvió código {result.returncode}: {stderr_txt[:500]}")

        if raw is None or not raw.strip():
            return None

        raw = raw.strip()

        # Busca el inicio del JSON. Se priorizan '[' y '{' (inicio estructural real
        # de ConvertTo-Json) sobre una comilla suelta, que puede pertenecer a un
        # WARNING/mensaje previo de PowerShell y truncar el JSON real si se usa
        # como ancla.
        indices = [i for i in [raw.find("["), raw.find("{")] if i != -1]
        if not indices:
            quote_idx = raw.find('"')
            indices = [quote_idx] if quote_idx != -1 else []
        start_idx = min(indices) if indices else -1

        if start_idx > 0:
            raw = raw[start_idx:]
        elif start_idx == -1:
            try:
                return json.loads(raw)
            except json.JSONDecodeError as e:
                logger.error(f"Error parseando JSON (sin delimitador reconocible): {e}")
                return None

        return json.loads(raw)

    except subprocess.TimeoutExpired:
        logger.error(f"Timeout ejecutando PowerShell después de {timeout}s")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Error parseando JSON: {e}")
        return None
    except Exception as e:
        logger.error(f"Error inesperado: {e}")
        return None

def _mensaje_error_powershell(texto: str) -> str:
    """5.4.0: deja solo el mensaje de un error de PowerShell, sin el volcado técnico que lo sigue
    («En línea: 1 Carácter: …», la línea de código, CategoryInfo, FullyQualifiedErrorId), que es lo
    que se enseñaba al técnico en los avisos. Si no reconoce el formato, devuelve el texto tal cual."""
    if not texto:
        return texto
    lineas = texto.replace("\r\n", "\n").split("\n")
    utiles = []
    for linea in lineas:
        limpia = linea.strip()
        if re.match(r"^(En l.nea:|At line:|\+ |\+$|CategoryInfo|FullyQualifiedErrorId)", limpia):
            break
        if limpia:
            utiles.append(limpia)
    return " ".join(utiles) if utiles else texto


def run_powershell_command(ps_code: str, timeout: int = 20) -> Tuple[bool, str]:
    """Ejecuta un comando PowerShell que no devuelve datos (una acción:
    Set-NetFirewallRule, Enable/Disable-NetAdapter...). Devuelve
    (éxito, mensaje_error). No usa ConvertTo-Json — para eso está
    run_powershell_json."""
    # wrap_with_credentials va DENTRO del $ErrorActionPreference (no al
    # revés): si hay credenciales alternativas, todo se envuelve en un
    # Invoke-Command con su propio runspace, que no hereda la preferencia
    # de la sesión que lo invoca. Antes esta función no aplicaba las
    # credenciales alternativas en absoluto (a diferencia de
    # run_powershell_json) — las acciones de escritura (firewall, auditoría,
    # etc.) se ejecutaban siempre como el usuario actual aunque el técnico
    # hubiera configurado 'Ejecutar como otro usuario'.
    ps_code_final = wrap_with_credentials("$ErrorActionPreference = 'Stop'; " + ps_code)
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_code_final]
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        result = subprocess.run(
            cmd, capture_output=True, text=False, timeout=timeout,
            check=False, startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode == 0:
            return True, ""
        stderr_txt = _redactar_secretos((result.stderr or b'').decode('utf-8-sig', errors='replace').strip())
        return False, _mensaje_error_powershell(stderr_txt) or f"Código de salida {result.returncode}"
    except subprocess.TimeoutExpired:
        return False, f"Tiempo agotado tras {timeout}s"
    except Exception as e:
        return False, str(e)

def is_smbshare_available() -> bool:
    """Comprueba si el módulo SMBShare está disponible."""
    out = run_powershell_json(PS_CHECK_SMBSHARE)
    return out == "true" or out is True or out == True

def winget_esta_bloqueado(id_paquete: str) -> bool:
    """True si el paquete tiene algún anclaje winget (pin) activo. Usado
    por Software Instalado (vínculo winget manual) para decidir si su menú
    contextual ofrece 'Bloquear' o 'Desbloquear' — a diferencia de Gestor
    de Paquetes, esa pestaña no mantiene su propia tabla de estado, así que
    se consulta bajo demanda al abrir el menú."""
    if not id_paquete:
        return False
    data = run_powershell_json(PS_WINGET_PIN_LIST, timeout=30)
    ids = (data or {}).get("Ids", [])
    return id_paquete in ids

def escape_powershell_string(s: str) -> str:
    """Escapa comillas simples para prevenir inyección de comandos."""
    return s.replace("'", "''")

# ---------------------------
# Ejecución segura con datos variables (para reparaciones)
# ---------------------------
# Los bugs de inyección encontrados en la revisión de seguridad (Credenciales,
# Carpetas Compartidas) salieron todos de construir el string de PowerShell a
# mano e interpolar datos variables sin pasar por escape_powershell_string.
# Estas dos funciones son la forma recomendada de construir un comando con
# datos variables a partir de ahora: la plantilla debe envolver cada
# placeholder entre comillas SIMPLES — p. ej. "Get-Service -Name '{nombre}'" —
# y aquí se escapa automáticamente cada valor antes de sustituirlo, para no
# depender de que cada sitio nuevo se acuerde de escapar a mano.

def run_powershell_json_params(plantilla: str, timeout: int = DEFAULT_TIMEOUT, **valores) -> Optional[object]:
    """Como run_powershell_json, pero cada valor en **valores se escapa
    automáticamente (comillas simples dobladas) antes de sustituirse en
    'plantilla' con .format(). Los placeholders literales de PowerShell
    ({{ }} de un scriptblock, @{{ }} de un hashtable) deben escribirse
    doblados, igual que en un f-string normal."""
    valores_escapados = {k: escape_powershell_string(str(v)) for k, v in valores.items()}
    return run_powershell_json(plantilla.format(**valores_escapados), timeout=timeout)

def run_powershell_command_params(plantilla: str, timeout: int = 20, **valores) -> Tuple[bool, str]:
    """Equivalente a run_powershell_json_params, para comandos de acción
    (run_powershell_command) en vez de consultas que devuelven datos."""
    valores_escapados = {k: escape_powershell_string(str(v)) for k, v in valores.items()}
    return run_powershell_command(plantilla.format(**valores_escapados), timeout=timeout)

def run_system_command(argv: List[str], timeout: int = 20, intentar_utf8: bool = False) -> Tuple[bool, str]:
    """Ejecuta un ejecutable real del sistema directamente (cmdkey, sc,
    reg, wevtutil, auditpol...) con argumentos como lista, SIN pasar por
    powershell -Command — evita por construcción cualquier inyección vía
    escapado incompleto, porque no hay ningún intérprete de comandos
    parseando los argumentos (Windows los recibe como argv real). Preferir
    esto sobre run_powershell_command_params siempre que la acción tenga
    un ejecutable directo disponible (como se hizo en Credenciales tras la
    revisión de seguridad).

    intentar_utf8=True: decodifica probando UTF-8 primero, con fallback a
    la codificación de página de códigos por defecto si los bytes no son
    UTF-8 válido. netsh.exe (WiFi) emite UTF-8 real en máquinas con la
    consola en ese modo (frecuente en Windows 10/11 recientes), pero el
    decode por defecto de Python asume ANSI y corrompe cualquier tilde/eñe
    (comprobado en vivo: 'Autenticación' -> 'AutenticaciÃ³n', y un SSID
    real con 'ñ' igual de corrompido, rompiendo además la comparación con
    el mismo SSID leído por otra vía que sí fuerza UTF-8). NO es el
    comportamiento por defecto para no arriesgar los demás usos ya
    existentes (cmdkey, sc, reg...) sin auditarlos uno a uno."""
    try:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = subprocess.SW_HIDE
        result = subprocess.run(
            argv, capture_output=True, text=False, timeout=timeout,
            check=False, startupinfo=startupinfo, creationflags=subprocess.CREATE_NO_WINDOW,
        )

        def _decodificar(datos: bytes) -> str:
            if intentar_utf8:
                try:
                    return datos.decode("utf-8")
                except UnicodeDecodeError:
                    pass
            return datos.decode(locale.getpreferredencoding(False), errors="replace")

        if result.returncode == 0:
            return True, _decodificar(result.stdout).strip()
        salida_error = _decodificar(result.stderr or result.stdout or b"").strip()
        return False, _redactar_secretos(salida_error) or f"Código de salida {result.returncode}"
    except subprocess.TimeoutExpired:
        return False, f"Tiempo agotado tras {timeout}s"
    except Exception as e:
        return False, str(e)

# ---------------------------
# WiFi: gestión de perfiles guardados (netsh wlan) — todo vía
# run_system_command (argv real), nunca interpolando el SSID en un string
# de PowerShell, mismo criterio de seguridad que motivó run_system_command.
# ---------------------------

def wifi_olvidar_perfil(nombre: str) -> Tuple[bool, str]:
    """netsh wlan delete profile — borra el perfil guardado (con su
    contraseña) de este equipo. No afecta a otros equipos ni al router."""
    return run_system_command(["netsh", "wlan", "delete", "profile", f"name={nombre}"], intentar_utf8=True)

def wifi_bloquear_red(nombre: str) -> Tuple[bool, str]:
    """netsh wlan add filter permission=block — impide que Windows se
    conecte a este SSID (a mano o automáticamente) aunque no exista ya
    ningún perfil guardado para ella. Independiente de wifi_olvidar_perfil:
    se puede bloquear una red sin haber tenido nunca un perfil, y se puede
    olvidar un perfil sin bloquear la red."""
    return run_system_command([
        "netsh", "wlan", "add", "filter", "permission=block",
        f"ssid={nombre}", "networktype=infrastructure",
    ], intentar_utf8=True)

def wifi_desbloquear_red(nombre: str) -> Tuple[bool, str]:
    """netsh wlan delete filter permission=block — quita el bloqueo
    aplicado por wifi_bloquear_red."""
    return run_system_command([
        "netsh", "wlan", "delete", "filter", "permission=block",
        f"ssid={nombre}", "networktype=infrastructure",
    ], intentar_utf8=True)

def wifi_red_esta_bloqueada(nombre: str) -> bool:
    """Comprueba si 'nombre' ya tiene un filtro de bloqueo de usuario
    activo, parseando 'netsh wlan show filters'. Se busca solo por
    'SSID: "..."' (literal en cualquier idioma de Windows, a diferencia de
    las etiquetas de sección que sí están localizadas) para no depender
    de en qué idioma esté el sistema."""
    ok, salida = run_system_command(["netsh", "wlan", "show", "filters"], intentar_utf8=True)
    if not ok:
        return False
    for ssid_encontrado in re.findall(r'SSID:\s*"([^"]*)"', salida):
        if ssid_encontrado == nombre:
            return True
    return False

def wifi_exportar_perfil(nombre: str, carpeta_destino: str) -> Tuple[bool, str]:
    """netsh wlan export profile — vuelca el perfil COMPLETO (con la
    contraseña en claro, key=clear) a un .xml en carpeta_destino, listo
    para 'netsh wlan add profile filename=...' en otro equipo Windows sin
    teclear nada a mano."""
    return run_system_command([
        "netsh", "wlan", "export", "profile", f"name={nombre}",
        "key=clear", f"folder={carpeta_destino}",
    ], intentar_utf8=True)

# ---------------------------
# WiFi: detectar redes cercanas y conectar/desconectar (netsh wlan) —
# complementa a la gestión de perfiles guardados de arriba.
# ---------------------------

_PERFIL_WLAN_XML_ABIERTO = """<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
    <name>{ssid}</name>
    <SSIDConfig>
        <SSID>
            <name>{ssid}</name>
        </SSID>
    </SSIDConfig>
    <connectionType>ESS</connectionType>
    <connectionMode>auto</connectionMode>
    <MSM>
        <security>
            <authEncryption>
                <authentication>open</authentication>
                <encryption>none</encryption>
                <useOneX>false</useOneX>
            </authEncryption>
        </security>
    </MSM>
</WLANProfile>"""

_PERFIL_WLAN_XML_CON_CLAVE = """<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
    <name>{ssid}</name>
    <SSIDConfig>
        <SSID>
            <name>{ssid}</name>
        </SSID>
    </SSIDConfig>
    <connectionType>ESS</connectionType>
    <connectionMode>auto</connectionMode>
    <MSM>
        <security>
            <authEncryption>
                <authentication>{authentication}</authentication>
                <encryption>{encryption}</encryption>
                <useOneX>false</useOneX>
            </authEncryption>
            <sharedKey>
                <keyType>{key_type}</keyType>
                <protected>false</protected>
                <keyMaterial>{password}</keyMaterial>
            </sharedKey>
        </security>
    </MSM>
</WLANProfile>"""

def wifi_mapear_seguridad_a_perfil(autenticacion: str) -> Tuple[Optional[str], Optional[str]]:
    """(authentication, encryption) para el XML de perfil WLAN según lo
    que reporta 'netsh wlan show networks' — (None, None) si no es un
    tipo soportado con un simple perfil PSK: una red Enterprise/802.1X
    necesita certificado o credenciales de dominio, no solo una clave, y
    crear ese perfil está fuera de alcance aquí."""
    a = (autenticacion or "").strip().lower()
    if a in ("open", "abierta", "ninguna", "none", ""):
        return "open", "none"
    if "wpa3" in a:
        return "WPA3SAE", "AES"
    if "wpa2" in a:
        return "WPA2PSK", "AES"
    if "wpa" in a:
        return "WPAPSK", "TKIP"
    if "wep" in a:
        return "open", "WEP"
    return None, None

def wifi_obtener_interfaz() -> Optional[str]:
    """Nombre de la interfaz WLAN (p. ej. 'Wi-Fi'), leído de 'netsh wlan
    show interfaces' — necesario para connect/disconnect. Si hay varias
    interfaces WiFi, se usa la primera (caso raro en la práctica)."""
    ok, salida = run_system_command(["netsh", "wlan", "show", "interfaces"], intentar_utf8=True)
    if not ok:
        return None
    m = re.search(r'^\s*(?:Nombre|Name)\s*:\s*(.+)$', salida, re.MULTILINE)
    return m.group(1).strip() if m else None

def wifi_estado_conexion_actual() -> Optional[Dict[str, str]]:
    """{'ssid': ..., 'interfaz': ...} si hay una WiFi conectada ahora
    mismo, o None si no. Se usa para el aviso de "Desconectar" — antes de
    cortarla hay que saber a cuál."""
    ok, salida = run_system_command(["netsh", "wlan", "show", "interfaces"], intentar_utf8=True)
    if not ok:
        return None
    m_estado = re.search(r'^\s*(?:Estado|State)\s*:\s*(.+)$', salida, re.MULTILINE)
    if not m_estado:
        return None
    estado_texto = m_estado.group(1).strip().lower()
    esta_conectada = "conectad" in estado_texto or "connected" in estado_texto
    if not esta_conectada:
        return None
    m_interfaz = re.search(r'^\s*(?:Nombre|Name)\s*:\s*(.+)$', salida, re.MULTILINE)
    m_ssid = re.search(r'^\s*SSID\s*:\s*(.+)$', salida, re.MULTILINE)
    if not m_interfaz or not m_ssid:
        return None
    return {"ssid": m_ssid.group(1).strip(), "interfaz": m_interfaz.group(1).strip()}

def wifi_escanear_redes_cercanas() -> List[Dict[str, Any]]:
    """netsh wlan show networks mode=bssid — escaneo de solo lectura de
    redes visibles ahora mismo. Devuelve [{ssid, autenticacion, cifrado,
    senal}], con 'senal' tomada del primer BSSID de cada SSID (varios
    puntos de acceso con el mismo SSID casi siempre reportan una señal
    similar; no hace falta desglosarlos para elegir a cuál conectar)."""
    ok, salida = run_system_command(
        ["netsh", "wlan", "show", "networks", "mode=bssid"], timeout=30, intentar_utf8=True)
    if not ok:
        return []

    redes = []
    bloques = re.split(r'\r?\n(?=SSID \d+\s*:)', salida)
    for bloque in bloques:
        m_ssid = re.search(r'^SSID \d+\s*:\s*(.*)$', bloque, re.MULTILINE)
        if not m_ssid:
            continue
        ssid = m_ssid.group(1).strip()
        if not ssid:
            continue
        # netsh.exe (a diferencia de las rutas que pasan por
        # run_powershell_json) no fuerza UTF-8, y en este equipo su salida
        # ya viene en UTF-8 real — run_system_command la decodifica con la
        # codificación de página de códigos por defecto (cp1252), así que
        # una tilde (2 bytes UTF-8) se corrompe en DOS caracteres ("ó" ->
        # "Ã³"). En vez de adivinar la codificación real (varía según el
        # equipo), el carácter/es acentuado se comodín con .{1,2}.
        m_auth = re.search(r'(?:Autenticaci.{1,2}n|Authentication)\s*:\s*(.+)', bloque)
        m_cifrado = re.search(r'(?:Cifrado|Encryption)\s*:\s*(.+)', bloque)
        m_senal = re.search(r'(?:Se.{1,2}al|Signal)\s*:\s*(\d+)\s*%', bloque)
        redes.append({
            "ssid": ssid,
            "autenticacion": m_auth.group(1).strip() if m_auth else "N/A",
            "cifrado": m_cifrado.group(1).strip() if m_cifrado else "N/A",
            "senal": int(m_senal.group(1)) if m_senal else 0,
        })
    return redes

def wifi_conectar_a_perfil_existente(nombre: str, interfaz: str) -> Tuple[bool, str]:
    return run_system_command([
        "netsh", "wlan", "connect", f"name={nombre}", f"ssid={nombre}", f"interface={interfaz}",
    ], intentar_utf8=True)

def wifi_crear_perfil_y_conectar(ssid: str, password: str, autenticacion: str, interfaz: str) -> Tuple[bool, str]:
    """Para una red SIN perfil guardado todavía: genera un perfil WLAN
    (XML temporal), lo importa y conecta. Solo soporta seguridad con clave
    compartida (abierta/WEP/WPA/WPA2/WPA3-Personal) — ver
    wifi_mapear_seguridad_a_perfil para lo que queda fuera (Enterprise)."""
    authentication, encryption = wifi_mapear_seguridad_a_perfil(autenticacion)
    if authentication is None:
        return False, ("Tipo de seguridad no soportado para crear un perfil nuevo (red "
                        "Enterprise/802.1X: necesita certificado o credenciales de dominio, "
                        "no solo una clave). Conéctate desde la configuración de Windows.")

    ssid_xml = _escapar_xml(ssid)
    if encryption == "none":
        xml_perfil = _PERFIL_WLAN_XML_ABIERTO.format(ssid=ssid_xml)
    else:
        # WEP usa clave de red (networkKey); WPA/WPA2/WPA3 personal, frase de paso (passPhrase).
        xml_perfil = _PERFIL_WLAN_XML_CON_CLAVE.format(
            ssid=ssid_xml, authentication=authentication, encryption=encryption,
            key_type="networkKey" if encryption == "WEP" else "passPhrase",
            password=_escapar_xml(password))

    archivo_temp = None
    try:
        # utf-8-sig (con BOM): netsh wlan add profile ha rechazado en la
        # práctica ficheros UTF-8 sin BOM como "perfil corrupto" en
        # algunos equipos — el BOM es la variante que documentan como
        # fiable en todas las versiones de Windows.
        with tempfile.NamedTemporaryFile(
                mode="w", suffix=".xml", delete=False, encoding="utf-8-sig") as f:
            f.write(xml_perfil)
            archivo_temp = f.name

        ok, mensaje = run_system_command([
            "netsh", "wlan", "add", "profile", f"filename={archivo_temp}", f"interface={interfaz}",
        ], intentar_utf8=True)
        if not ok:
            return False, mensaje

        return wifi_conectar_a_perfil_existente(ssid, interfaz)
    finally:
        if archivo_temp and os.path.exists(archivo_temp):
            try:
                os.remove(archivo_temp)
            except OSError:
                pass

def wifi_desconectar(interfaz: str) -> Tuple[bool, str]:
    return run_system_command(["netsh", "wlan", "disconnect", f"interface={interfaz}"], intentar_utf8=True)

def _csv_safe_cell(value) -> str:
    """Antepone un apóstrofo a valores que empiecen por =, +, - o @ para
    evitar 'CSV/Formula Injection': si el CSV exportado se abre en Excel,
    esos caracteres iniciales disparan la evaluación de una fórmula/DDE."""
    s = "" if value is None else str(value)
    if s[:1] in ("=", "+", "-", "@"):
        return "'" + s
    return s

def _csv_safe_row(row):
    """Aplica _csv_safe_cell a cada celda de una fila antes de escribirla."""
    return [_csv_safe_cell(cell) for cell in row]

class _SafeCsvWriter:
    """Envoltura de csv.writer que sanitiza cada fila con _csv_safe_row antes
    de escribirla, protegiendo todos los exports CSV de la aplicación contra
    inyección de fórmulas en Excel sin tener que tocar cada writerow()."""
    def __init__(self, real_writer):
        self._w = real_writer

    def writerow(self, row):
        return self._w.writerow(_csv_safe_row(row))

    def writerows(self, rows):
        return self._w.writerows(_csv_safe_row(r) for r in rows)

# ---------------------------
# Software EOL / lista negra configurable
# ---------------------------
EOL_SOFTWARE_PATTERNS = [
    "windows xp", "windows vista", "windows 7", "windows 8 ", "windows 8.0",
    "windows server 2008", "windows server 2012",
    "office 2007", "office 2010", "office 2013",
    "sql server 2008", "sql server 2012",
    "adobe flash", "microsoft silverlight",
    "java 6", "java 7", "java se 6", "java se 7",
    "python 2.7", "php 5.",
]

def _is_eol_software(nombre: str) -> bool:
    """Comprueba si un nombre de software coincide con versiones sin soporte conocidas."""
    n = (nombre or "").lower()
    return any(p in n for p in EOL_SOFTWARE_PATTERNS)

def _get_software_blacklist() -> list:
    """Lee la lista negra de software configurada por el usuario (QSettings)."""
    settings = QSettings("Masoft", "MASys")
    raw = settings.value("software_blacklist", "")
    return [s.strip().lower() for s in raw.split(",") if s.strip()] if raw else []

def _is_blacklisted_software(nombre: str, blacklist: list) -> bool:
    n = (nombre or "").lower()
    return any(b in n for b in blacklist)

def _software_estado(nombre: str, blacklist: list, system_component: bool = False) -> str:
    """Devuelve el estado de riesgo de un software: OK / EOL / No autorizado.
    Si es un SystemComponent del registro (SystemComponent=1 — runtimes,
    subcomponentes de drivers, etc. que Programas y características oculta
    por defecto), se antepone la etiqueta 🧩 para diferenciarlo del software
    que el usuario instaló a propósito, sin perder el aviso de riesgo si
    también está en la lista negra o es EOL."""
    if _is_blacklisted_software(nombre, blacklist):
        base = "🚫 No autorizado"
    elif _is_eol_software(nombre):
        base = "⚠️ Sin soporte (EOL)"
    else:
        base = "✅ OK"
    if system_component:
        return "🧩 Componente del sistema" if base == "✅ OK" else f"🧩 Componente ({base})"
    return base

# ---------------------------
# Vínculo manual software instalado -> paquete winget (para actualizaciones)
# ---------------------------
# 'winget upgrade' relaciona por su cuenta la mayoría de software conocido,
# pero falla con nombres genéricos, apps internas o instaladores que no
# coinciden con el catálogo — este vínculo manual (menú contextual de
# Software Instalado, 'Vincular con winget') fija el Id exacto una vez, sin
# depender de que winget vuelva a adivinarlo correctamente en el futuro.

def _get_software_winget_links() -> dict:
    """Vínculos nombre_software (en minúsculas) -> Id de paquete winget."""
    settings = QSettings("Masoft", "MASys")
    raw = settings.value("software_winget_links", "")
    if not raw:
        return {}
    try:
        datos = json.loads(raw)
        return datos if isinstance(datos, dict) else {}
    except (TypeError, ValueError):
        return {}

def _set_software_winget_link(nombre: str, winget_id: str) -> None:
    links = _get_software_winget_links()
    links[(nombre or "").strip().lower()] = winget_id
    QSettings("Masoft", "MASys").setValue("software_winget_links", json.dumps(links))

def _quitar_software_winget_link(nombre: str) -> None:
    links = _get_software_winget_links()
    links.pop((nombre or "").strip().lower(), None)
    QSettings("Masoft", "MASys").setValue("software_winget_links", json.dumps(links))

def _software_winget_id(nombre: str, links: Optional[dict] = None) -> Optional[str]:
    links = links if links is not None else _get_software_winget_links()
    return links.get((nombre or "").strip().lower()) or None

# ---------------------------
# Avisos de Soporte validados (silenciar un aviso concreto sin tocar el
# problema real que lo origina)
# ---------------------------
# Cada aviso detectado en Soporte (msys/ui/soporte.py, _detectar_soporte_*)
# lleva una CLAVE ESTABLE propia (ver comentario en cada detector) — validar
# un aviso lo guarda aquí por esa clave, y _actualizar_boton_soporte ya no
# lo vuelve a mostrar hasta que se desvalide desde "📋 Avisos validados".
# Se guarda también la etiqueta/sección/fecha en el momento de validar
# (snapshot) para poder listarlos sin tener que re-ejecutar cada detector.

def _get_avisos_soporte_validados() -> dict:
    """clave -> {'etiqueta': str, 'seccion': str, 'fecha': str}."""
    settings = QSettings("Masoft", "MASys")
    raw = settings.value("avisos_soporte_validados", "")
    if not raw:
        return {}
    try:
        datos = json.loads(raw)
        return datos if isinstance(datos, dict) else {}
    except (TypeError, ValueError):
        return {}

def _marcar_aviso_soporte_validado(clave: str, etiqueta: str, seccion: str) -> None:
    validados = _get_avisos_soporte_validados()
    validados[clave] = {
        "etiqueta": etiqueta, "seccion": seccion,
        "fecha": datetime.now().strftime("%d/%m/%Y %H:%M"),
    }
    QSettings("Masoft", "MASys").setValue("avisos_soporte_validados", json.dumps(validados))

def _quitar_aviso_soporte_validado(clave: str) -> None:
    validados = _get_avisos_soporte_validados()
    validados.pop(clave, None)
    QSettings("Masoft", "MASys").setValue("avisos_soporte_validados", json.dumps(validados))

# ---------------------------
# Sistema de Checkpoints
# ---------------------------
# El checkpoint se serializa en JSON (nunca pickle: pickle.load() ejecuta
# codigo arbitrario via __reduce__/__setstate__ si el fichero es controlado
# por un atacante, y CHECKPOINT_FILE vive en %TEMP% con nombre fijo y
# predecible — cualquier proceso con el mismo usuario de Windows podria
# plantar ahi un pickle malicioso, y check_for_checkpoint() lo cargaria solo
# al arrancar MASys, antes de mostrar ningun dialogo de confirmacion).
# Ademas se protege con DPAPI (CryptProtectData, ligado al perfil del
# usuario de Windows actual): un fichero corrupto o no producido por DPAPI
# para ese mismo usuario falla al descifrar de forma verificable, y se
# trata como "no hay checkpoint" en vez de intentar interpretarlo.

def checkpoint_exists() -> bool:
    """Verifica si existe un archivo de checkpoint."""
    return os.path.exists(CHECKPOINT_FILE)

def _leer_checkpoint_json() -> Optional[Dict[str, Any]]:
    """Lee y descifra (DPAPI) + deserializa (JSON) el checkpoint. None si no
    existe, esta corrupto, o no fue protegido para el usuario actual."""
    if not checkpoint_exists():
        return None
    try:
        with open(CHECKPOINT_FILE, 'rb') as f:
            protegido = f.read()
        _descr, payload = win32crypt.CryptUnprotectData(protegido, None, None, None, 0)
        return json.loads(payload.decode('utf-8'))
    except Exception as e:
        logger.error(f"Error al leer/descifrar checkpoint: {e}")
        return None

def get_checkpoint_info() -> Optional[Dict[str, Any]]:
    """Obtiene información básica del checkpoint sin cargarlo completamente."""
    data = _leer_checkpoint_json()
    if not data:
        return None
    return {
        'timestamp': data.get('timestamp'),
        'mode': data.get('mode'),
        'target': data.get('target'),
        'total_folders': data.get('total_folders', 0),
        'processed_count': data.get('processed_count', 0),
        'results_count': len(data.get('results', []))
    }

def save_checkpoint(data: Dict[str, Any]) -> bool:
    """Guarda un checkpoint en disco: JSON cifrado/autenticado con DPAPI."""
    try:
        data['timestamp'] = datetime.now().isoformat()
        payload = json.dumps(data, ensure_ascii=False).encode('utf-8')
        protegido = win32crypt.CryptProtectData(payload, "MASys checkpoint", None, None, None, 0)
        with open(CHECKPOINT_FILE, 'wb') as f:
            f.write(protegido)
        logger.info(f"Checkpoint guardado: {data.get('processed_count', 0)} carpetas procesadas")
        return True
    except Exception as e:
        logger.error(f"Error al guardar checkpoint: {e}")
        return False

def load_checkpoint() -> Optional[Dict[str, Any]]:
    """Carga un checkpoint desde disco."""
    data = _leer_checkpoint_json()
    if data:
        logger.info(f"Checkpoint cargado: {data.get('processed_count', 0)} carpetas ya procesadas")
    return data

def delete_checkpoint() -> bool:
    """Elimina el archivo de checkpoint."""
    try:
        if checkpoint_exists():
            os.remove(CHECKPOINT_FILE)
            logger.info("Checkpoint eliminado")
        return True
    except Exception as e:
        logger.error(f"Error al eliminar checkpoint: {e}")
        return False

