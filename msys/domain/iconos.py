# -*- coding: utf-8 -*-
"""Motor (sin Qt) del apartado 🎨 Iconos.

- Bibliotecas de iconos: lee los grupos de iconos (RT_GROUP_ICON) de un .exe,
  .dll, .icl o .cpl y reconstruye cada uno como un archivo .ico COMPLETO (todos
  los tamaños y profundidades, bytes originales), sin pasar por HICON: así no
  se pierde calidad ni el tamaño de 256 px.
- Índice de un icono: el que piden los accesos directos y desktop.ini es la
  POSICIÓN del grupo en la enumeración de recursos (0, 1, 2...); el negativo
  (-ID) es el identificador del recurso. Se devuelven los dos.
- Reparación: cachés de iconos y miniaturas, accesos directos rotos.
- Configuración: iconos del escritorio, espaciado, distribución guardada,
  iconos superpuestos (overlays) e iconos personalizados de unidades.

Todo lo que escribe en el registro o borra archivos devuelve (ok, mensaje):
la interfaz decide qué contar al usuario y lo apunta en el historial de
reparaciones."""

import base64
import ctypes
import glob
import json
import os
import struct
import subprocess
import time
import winreg
from ctypes import wintypes
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from msys.core.utils import startupinfo_oculto

# ─────────────────────────────────────────────────────────────────
# Bibliotecas de iconos
# ─────────────────────────────────────────────────────────────────

RT_ICON = 3
RT_GROUP_ICON = 14
LOAD_LIBRARY_AS_DATAFILE = 0x00000002
LOAD_LIBRARY_AS_IMAGE_RESOURCE = 0x00000020

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.LoadLibraryExW.restype = wintypes.HMODULE
_k32.LoadLibraryExW.argtypes = [wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD]
_k32.FreeLibrary.argtypes = [wintypes.HMODULE]
_k32.FindResourceW.restype = wintypes.HANDLE
_k32.FindResourceW.argtypes = [wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p]
_k32.LoadResource.restype = wintypes.HANDLE
_k32.LoadResource.argtypes = [wintypes.HMODULE, wintypes.HANDLE]
_k32.LockResource.restype = ctypes.c_void_p
_k32.LockResource.argtypes = [wintypes.HANDLE]
_k32.SizeofResource.restype = wintypes.DWORD
_k32.SizeofResource.argtypes = [wintypes.HMODULE, wintypes.HANDLE]
_ENUMRESNAMEPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
_k32.EnumResourceNamesW.argtypes = [wintypes.HMODULE, ctypes.c_void_p, _ENUMRESNAMEPROC, ctypes.c_void_p]

# Bibliotecas de iconos de Windows más útiles (en System32), con su contenido
BIBLIOTECAS_SISTEMA = [
    ("imageres.dll", "Iconos modernos de Windows (carpetas, unidades, usuarios...)"),
    ("shell32.dll", "Iconos clásicos del Explorador"),
    ("ddores.dll", "Dispositivos y hardware"),
    ("netshell.dll", "Conexiones de red"),
    ("wmploc.dll", "Multimedia"),
    ("mmres.dll", "Sonido"),
    ("setupapi.dll", "Instalación de hardware"),
    ("pifmgr.dll", "Iconos antiguos de programas"),
    ("moricons.dll", "Iconos de programas MS-DOS"),
    ("compstui.dll", "Impresión"),
    ("ieframe.dll", "Internet Explorer"),
    ("accessibilitycpl.dll", "Accesibilidad"),
    ("dmdskres.dll", "Administración de discos"),
    ("mstscax.dll", "Escritorio remoto"),
    ("pnidui.dll", "Iconos de la barra de tareas de red"),
    ("sensorscpl.dll", "Sensores"),
    ("explorer.exe", "Explorador de Windows"),
]


@dataclass
class IconoGrupo:
    indice: int            # posición (lo que piden accesos directos y desktop.ini)
    res_id: str            # identificador del recurso: número o nombre
    ico: bytes             # archivo .ico completo
    tamanos: List[Tuple[int, int, int]] = field(default_factory=list)  # (ancho, alto, bits)

    @property
    def tamano_max(self) -> int:
        return max((w for w, _h, _b in self.tamanos), default=0)

    @property
    def referencia(self) -> str:
        """'-ID' si el recurso es numérico (lo que se ve en el registro: imageres.dll,-3)."""
        return f"-{self.res_id}" if self.res_id.isdigit() else self.res_id


def ruta_biblioteca(nombre: str) -> str:
    sistema = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", nombre)
    if os.path.exists(sistema):
        return sistema
    return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), nombre)


def _recurso(hmod, tipo: int, nombre) -> Optional[bytes]:
    h = _k32.FindResourceW(hmod, nombre, ctypes.c_void_p(tipo))
    if not h:
        return None
    hglob = _k32.LoadResource(hmod, h)
    if not hglob:
        return None
    ptr = _k32.LockResource(hglob)
    n = _k32.SizeofResource(hmod, h)
    if not ptr or not n:
        return None
    return ctypes.string_at(ptr, n)


def _ico_desde_grupo(hmod, grupo: bytes) -> Tuple[bytes, List[Tuple[int, int, int]]]:
    """GRPICONDIR (con entradas de 14 bytes que apuntan a recursos RT_ICON)
    -> archivo .ico (entradas de 16 bytes con desplazamiento + imágenes)."""
    _res, tipo, n = struct.unpack_from("<HHH", grupo, 0)
    entradas, imagenes, tamanos = [], [], []
    for i in range(n):
        off = 6 + i * 14
        if off + 14 > len(grupo):
            break
        w, h, colores, reservado, planos, bits, _tam, icon_id = struct.unpack_from("<BBBBHHIH", grupo, off)
        datos = _recurso(hmod, RT_ICON, ctypes.c_void_p(icon_id))
        if not datos:
            continue
        entradas.append((w, h, colores, reservado, planos, bits))
        imagenes.append(datos)
        ancho = w or 256
        alto = h or 256
        if datos[:8] == b"\x89PNG\r\n\x1a\n" and len(datos) >= 24:
            ancho, alto = struct.unpack(">II", datos[16:24])
        tamanos.append((ancho, alto, bits))
    cabecera = struct.pack("<HHH", 0, 1, len(imagenes))
    desplazamiento = 6 + 16 * len(imagenes)
    directorio = b""
    for (w, h, colores, reservado, planos, bits), datos in zip(entradas, imagenes):
        directorio += struct.pack("<BBBBHHII", w, h, colores, reservado, planos, bits, len(datos), desplazamiento)
        desplazamiento += len(datos)
    return cabecera + directorio + b"".join(imagenes), tamanos


def tamanos_ico(ico: bytes) -> List[Tuple[int, int, int]]:
    """Tamaños de un archivo .ico ya construido."""
    if len(ico) < 6:
        return []
    _r, tipo, n = struct.unpack_from("<HHH", ico, 0)
    out = []
    for i in range(n):
        off = 6 + i * 16
        if off + 16 > len(ico):
            break
        w, h, _c, _r2, _p, bits, tam, pos = struct.unpack_from("<BBBBHHII", ico, off)
        datos = ico[pos:pos + 24]
        ancho, alto = w or 256, h or 256
        if datos[:8] == b"\x89PNG\r\n\x1a\n" and len(datos) >= 24:
            ancho, alto = struct.unpack(">II", datos[16:24])
        out.append((ancho, alto, bits))
    return out


def leer_biblioteca(ruta: str, parar: Optional[Callable[[], bool]] = None) -> List[IconoGrupo]:
    """Todos los grupos de iconos de un .exe/.dll/.icl/.cpl, o el único de un .ico.
    Lanza OSError con un mensaje claro si el archivo no se puede abrir."""
    if not os.path.isfile(ruta):
        raise OSError(f"No existe el archivo: {ruta}")
    if ruta.lower().endswith(".ico"):
        with open(ruta, "rb") as f:
            datos = f.read()
        return [IconoGrupo(0, "1", datos, tamanos_ico(datos))]

    hmod = _k32.LoadLibraryExW(ruta, None, LOAD_LIBRARY_AS_DATAFILE | LOAD_LIBRARY_AS_IMAGE_RESOURCE)
    if not hmod:
        err = ctypes.get_last_error()
        raise OSError(f"No se pudo abrir {os.path.basename(ruta)} como biblioteca de recursos (error {err})")
    nombres = []

    @_ENUMRESNAMEPROC
    def _cb(_hmod, _tipo, nombre, _param):
        if nombre is not None and nombre < 0x10000:
            nombres.append(int(nombre))
        elif nombre:
            nombres.append(ctypes.wstring_at(nombre))
        return True

    try:
        _k32.EnumResourceNamesW(hmod, ctypes.c_void_p(RT_GROUP_ICON), _cb, None)
        out = []
        for i, nombre in enumerate(nombres):
            if parar and parar():
                break
            arg = ctypes.c_void_p(nombre) if isinstance(nombre, int) else ctypes.c_wchar_p(nombre)
            grupo = _recurso(hmod, RT_GROUP_ICON, arg)
            if not grupo or len(grupo) < 6:
                continue
            ico, tamanos = _ico_desde_grupo(hmod, grupo)
            if tamanos:
                out.append(IconoGrupo(i, str(nombre), ico, tamanos))
        return out
    finally:
        _k32.FreeLibrary(hmod)


def nombre_seguro(texto: str) -> str:
    limpio = "".join(c if c.isalnum() or c in " ._-()" else "_" for c in texto).strip()
    return limpio or "icono"


# ─────────────────────────────────────────────────────────────────
# Explorador y cachés
# ─────────────────────────────────────────────────────────────────

def _ejecutar(argv: List[str], timeout: int = 30) -> Tuple[bool, str]:
    try:
        r = subprocess.run(argv, capture_output=True, timeout=timeout, startupinfo=startupinfo_oculto(),
                           creationflags=subprocess.CREATE_NO_WINDOW)
        salida = (r.stdout or b"").decode("cp850", errors="replace") + (r.stderr or b"").decode("cp850", errors="replace")
        return r.returncode == 0, salida.strip()
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)


def cerrar_explorador() -> None:
    _ejecutar(["taskkill", "/f", "/im", "explorer.exe"], timeout=20)
    # Esperar a que suelte los archivos (máx. ~5 s)
    for _ in range(25):
        ok, salida = _ejecutar(["tasklist", "/fi", "IMAGENAME eq explorer.exe", "/nh"], timeout=10)
        if "explorer.exe" not in salida.lower():
            break
        time.sleep(0.2)


def abrir_explorador() -> None:
    try:
        subprocess.Popen([os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "explorer.exe")],
                         creationflags=subprocess.DETACHED_PROCESS)
    except OSError:
        pass


def reiniciar_explorador() -> None:
    cerrar_explorador()
    abrir_explorador()


def _archivos_cache(miniaturas: bool) -> List[str]:
    local = os.environ.get("LOCALAPPDATA", "")
    carpeta = os.path.join(local, "Microsoft", "Windows", "Explorer")
    if miniaturas:
        return glob.glob(os.path.join(carpeta, "thumbcache_*.db"))
    archivos = glob.glob(os.path.join(carpeta, "iconcache_*.db"))
    antiguo = os.path.join(local, "IconCache.db")
    if os.path.exists(antiguo):
        archivos.append(antiguo)
    return archivos


def tamano_cache(miniaturas: bool) -> Tuple[int, int]:
    """(nº de archivos, bytes) de la caché."""
    archivos = _archivos_cache(miniaturas)
    total = 0
    for a in archivos:
        try:
            total += os.path.getsize(a)
        except OSError:
            pass
    return len(archivos), total


def reconstruir_cache(miniaturas: bool) -> Tuple[bool, str]:
    """Cierra el Explorador, borra la caché (iconos o miniaturas) y lo vuelve a
    abrir: Windows la rehace al mostrar cada icono. Los archivos que otro
    programa tenga abiertos se cuentan y se dicen, no se fuerzan."""
    if not miniaturas:
        # Pide a Windows que vacíe su caché en memoria antes de borrar los archivos
        _ejecutar([os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "ie4uinit.exe"), "-show"], timeout=30)
    cerrar_explorador()
    borrados, bloqueados = 0, []
    try:
        for a in _archivos_cache(miniaturas):
            try:
                os.remove(a)
                borrados += 1
            except OSError:
                bloqueados.append(os.path.basename(a))
    finally:
        abrir_explorador()
    que = "miniaturas" if miniaturas else "iconos"
    if bloqueados:
        return borrados > 0, (f"Caché de {que}: {borrados} archivo(s) borrados; {len(bloqueados)} en uso por otro programa "
                              f"({', '.join(bloqueados[:4])}). Reinicia el equipo y repítelo si los iconos siguen mal.")
    return True, f"Caché de {que} reconstruida: {borrados} archivo(s) borrados. Windows la rehace sola al mostrar cada icono."


# ─────────────────────────────────────────────────────────────────
# Accesos directos
# ─────────────────────────────────────────────────────────────────

ESTADO_ROTO = "Destino no encontrado"
ESTADO_ICONO = "Icono no encontrado"
ESTADO_OK = "Correcto"
ESTADO_RED = "Ruta de red (no comprobada)"
ESTADO_ESPECIAL = "Acceso especial (aplicación de la Tienda o instalador)"


@dataclass
class AccesoDirecto:
    nombre: str
    ubicacion: str          # Escritorio, Menú Inicio...
    ruta: str               # ruta del .lnk
    destino: str
    argumentos: str
    icono: str              # "archivo,índice"
    estado: str


def carpetas_accesos() -> List[Tuple[str, str]]:
    """(etiqueta, carpeta) donde Windows guarda los accesos directos visibles."""
    perfil = os.environ.get("USERPROFILE", "")
    appdata = os.environ.get("APPDATA", "")
    public = os.environ.get("PUBLIC", r"C:\Users\Public")
    programdata = os.environ.get("ProgramData", r"C:\ProgramData")
    escritorio = os.path.join(perfil, "Desktop")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
            escritorio = os.path.expandvars(winreg.QueryValueEx(k, "Desktop")[0])
    except OSError:
        pass
    return [
        ("Escritorio", escritorio),
        ("Escritorio público", os.path.join(public, "Desktop")),
        ("Menú Inicio", os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs")),
        ("Menú Inicio (todos)", os.path.join(programdata, r"Microsoft\Windows\Start Menu\Programs")),
        ("Barra de tareas", os.path.join(appdata, r"Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar")),
        ("Inicio rápido", os.path.join(appdata, r"Microsoft\Internet Explorer\Quick Launch")),
    ]


def _existe(ruta: str) -> Optional[bool]:
    """True/False, o None si no se puede comprobar sin riesgo (ruta de red).
    Un nombre sin carpeta ("msiexec.exe") se busca donde lo buscaría Windows."""
    ruta = os.path.expandvars(ruta.strip().strip('"'))
    if not ruta:
        return None
    if ruta.startswith("\\\\"):
        return None
    if not os.path.dirname(ruta):
        import shutil
        raiz = os.environ.get("SystemRoot", r"C:\Windows")
        return bool(shutil.which(ruta)) or any(
            os.path.exists(os.path.join(c, ruta)) for c in (os.path.join(raiz, "System32"), raiz))
    return os.path.exists(ruta)


def escanear_accesos(parar: Optional[Callable[[], bool]] = None) -> List[AccesoDirecto]:
    """Lee todos los .lnk de las carpetas de carpetas_accesos(). Necesita COM
    inicializado en el hilo que lo llama (pythoncom.CoInitialize)."""
    import win32com.client
    shell = win32com.client.Dispatch("WScript.Shell")
    out, vistos = [], set()
    for etiqueta, carpeta in carpetas_accesos():
        if not os.path.isdir(carpeta):
            continue
        recursivo = etiqueta.startswith("Menú Inicio")
        patron = os.path.join(carpeta, "**", "*.lnk") if recursivo else os.path.join(carpeta, "*.lnk")
        for lnk in glob.glob(patron, recursive=recursivo):
            if parar and parar():
                return out
            clave = os.path.normcase(lnk)
            if clave in vistos:
                continue
            vistos.add(clave)
            try:
                sc = shell.CreateShortcut(lnk)
                destino, args, icono = sc.TargetPath or "", sc.Arguments or "", sc.IconLocation or ""
            except Exception:
                continue
            if not destino:
                estado = ESTADO_ESPECIAL
            else:
                existe = _existe(destino)
                if existe is None:
                    estado = ESTADO_RED
                elif not existe:
                    estado = ESTADO_ROTO
                else:
                    archivo_icono = icono.rsplit(",", 1)[0] if icono else ""
                    estado = ESTADO_ICONO if archivo_icono and _existe(archivo_icono) is False else ESTADO_OK
            out.append(AccesoDirecto(os.path.splitext(os.path.basename(lnk))[0], etiqueta, lnk, destino, args, icono, estado))
    return out


def enviar_a_papelera(ruta: str) -> Tuple[bool, str]:
    """Mueve un archivo a la Papelera de reciclaje (se puede recuperar). Mismo
    mecanismo (send2trash) que el resto de MASys."""
    try:
        from send2trash import send2trash
        send2trash(ruta)
        return True, ""
    except Exception as e:
        return False, str(e)


# ─────────────────────────────────────────────────────────────────
# Iconos del escritorio, espaciado y distribución
# ─────────────────────────────────────────────────────────────────

ICONOS_ESCRITORIO = [
    ("{20D04FE0-3AEA-1069-A2D8-08002B30309D}", "Este equipo", True),
    ("{59031a47-3f72-44a7-89c5-5595fe6b30ee}", "Archivos del usuario", True),
    ("{F02C1A0D-BE21-4350-88B0-7367FC96EF3C}", "Red", True),
    ("{645FF040-5081-101B-9F08-00AA002F954E}", "Papelera de reciclaje", False),
    ("{5399E694-6CE5-4D6C-8FCE-1D8870FDCBA0}", "Panel de control", True),
]  # (CLSID, nombre, oculto si no hay valor)
_K_OCULTOS = r"Software\Microsoft\Windows\CurrentVersion\Explorer\HideDesktopIcons\NewStartPanel"
_K_METRICAS = r"Control Panel\Desktop\WindowMetrics"
_K_DISTRIBUCION = r"Software\Microsoft\Windows\Shell\Bags\1\Desktop"


def iconos_escritorio_visibles() -> Dict[str, bool]:
    out = {}
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _K_OCULTOS)
    except OSError:
        k = None
    for clsid, _nombre, oculto_por_defecto in ICONOS_ESCRITORIO:
        oculto = oculto_por_defecto
        if k is not None:
            try:
                oculto = bool(winreg.QueryValueEx(k, clsid)[0])
            except OSError:
                pass
        out[clsid] = not oculto
    if k is not None:
        winreg.CloseKey(k)
    return out


def _avisar_cambio_shell():
    try:
        SHCNE_ASSOCCHANGED, SHCNF_IDLIST = 0x08000000, 0
        ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, SHCNF_IDLIST, None, None)
    except Exception:
        pass


def fijar_icono_escritorio(clsid: str, visible: bool) -> Tuple[bool, str]:
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _K_OCULTOS, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, clsid, 0, winreg.REG_DWORD, 0 if visible else 1)
        _avisar_cambio_shell()
        return True, ""
    except OSError as e:
        return False, str(e)


def espaciado_iconos() -> Tuple[int, int]:
    """(horizontal, vertical) en píxeles. En el registro va en twips negativos (-15 × px)."""
    def leer(nombre):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _K_METRICAS) as k:
                return max(1, round(-int(winreg.QueryValueEx(k, nombre)[0]) / 15))
        except (OSError, ValueError):
            return 75
    return leer("IconSpacing"), leer("IconVerticalSpacing")


ESPACIADO_MIN, ESPACIADO_MAX, ESPACIADO_DEFECTO = 32, 182, 75


def fijar_espaciado(horizontal: int, vertical: int) -> Tuple[bool, str]:
    h = max(ESPACIADO_MIN, min(ESPACIADO_MAX, int(horizontal)))
    v = max(ESPACIADO_MIN, min(ESPACIADO_MAX, int(vertical)))
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _K_METRICAS, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "IconSpacing", 0, winreg.REG_SZ, str(-15 * h))
            winreg.SetValueEx(k, "IconVerticalSpacing", 0, winreg.REG_SZ, str(-15 * v))
        SPI_ICONHORIZONTALSPACING, SPI_ICONVERTICALSPACING = 0x000D, 0x0018
        flags = 0x01 | 0x02  # SPIF_UPDATEINIFILE | SPIF_SENDCHANGE
        ctypes.windll.user32.SystemParametersInfoW(SPI_ICONHORIZONTALSPACING, h, None, flags)
        ctypes.windll.user32.SystemParametersInfoW(SPI_ICONVERTICALSPACING, v, None, flags)
        return True, ""
    except OSError as e:
        return False, str(e)


def _tipo_a_json(tipo, valor):
    if tipo in (winreg.REG_BINARY, winreg.REG_NONE):
        return {"tipo": tipo, "b64": base64.b64encode(valor or b"").decode()}
    return {"tipo": tipo, "valor": valor}


def leer_distribucion() -> Dict[str, dict]:
    """Valores de la vista del escritorio (posiciones, tamaño de iconos, organizar automáticamente)."""
    out = {}
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _K_DISTRIBUCION) as k:
        i = 0
        while True:
            try:
                nombre, valor, tipo = winreg.EnumValue(k, i)
            except OSError:
                break
            out[nombre] = _tipo_a_json(tipo, valor)
            i += 1
    return out


def guardar_distribucion(ruta: str, nombre: str) -> Tuple[bool, str]:
    try:
        datos = {"app": "MASys", "tipo": "distribucion_escritorio", "nombre": nombre,
                 "creado": time.strftime("%d/%m/%Y %H:%M"), "equipo": os.environ.get("COMPUTERNAME", ""),
                 "valores": leer_distribucion()}
        if "IconLayouts" not in datos["valores"]:
            return False, "Windows todavía no ha guardado la posición de los iconos de este escritorio"
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=1)
        return True, ""
    except OSError as e:
        return False, str(e)


def leer_archivo_distribucion(ruta: str) -> dict:
    with open(ruta, "r", encoding="utf-8") as f:
        datos = json.load(f)
    if datos.get("tipo") != "distribucion_escritorio" or not isinstance(datos.get("valores"), dict):
        raise ValueError("No es una distribución de escritorio de MASys")
    return datos


def restaurar_distribucion(ruta: str) -> Tuple[bool, str]:
    """El Explorador guarda las posiciones al cerrarse, así que hay que
    cerrarlo ANTES de escribirlas o las machacaría con las actuales."""
    try:
        datos = leer_archivo_distribucion(ruta)
    except (OSError, ValueError) as e:
        return False, str(e)
    cerrar_explorador()
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _K_DISTRIBUCION, 0, winreg.KEY_SET_VALUE) as k:
            for nombre, v in datos["valores"].items():
                tipo = int(v.get("tipo", winreg.REG_BINARY))
                valor = base64.b64decode(v["b64"]) if "b64" in v else v.get("valor")
                winreg.SetValueEx(k, nombre, 0, tipo, valor)
        return True, ""
    except (OSError, ValueError, TypeError) as e:
        return False, str(e)
    finally:
        abrir_explorador()


# ─────────────────────────────────────────────────────────────────
# Iconos superpuestos (overlays)
# ─────────────────────────────────────────────────────────────────

K_OVERLAYS = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\ShellIconOverlayIdentifiers"
# Documentación de IShellIconOverlayIdentifier: 15 plazas en total, 4 reservadas para el sistema
PLAZAS_OVERLAY = 15
PLAZAS_RESERVADAS = 4

OVERLAY_ACTIVO = "Activo"
OVERLAY_RIESGO = "Puede quedar fuera"
OVERLAY_FUERA = "Sin plaza (no se ve)"


@dataclass
class Overlay:
    posicion: int
    nombre: str           # nombre de la subclave (con sus espacios iniciales)
    clsid: str
    programa: str
    dll: str
    estado: str


def _leer_clsid(clsid: str) -> Tuple[str, str]:
    nombre, dll = "", ""
    for vista in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"CLSID\{clsid}", 0, winreg.KEY_READ | vista) as k:
                nombre = nombre or (winreg.QueryValueEx(k, "")[0] or "")
            with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"CLSID\{clsid}\InprocServer32", 0, winreg.KEY_READ | vista) as k:
                dll = dll or os.path.expandvars(winreg.QueryValueEx(k, "")[0] or "")
        except OSError:
            continue
    return nombre, dll


def _programa_de(dll: str, nombre: str) -> str:
    texto = f"{dll} {nombre}".lower()
    for clave, programa in (("onedrive", "OneDrive"), ("dropbox", "Dropbox"), ("tortoisegit", "TortoiseGit"),
                            ("tortoisesvn", "TortoiseSVN"), ("tortoise", "Tortoise"), ("googledrive", "Google Drive"),
                            ("google drive", "Google Drive"), ("drivefs", "Google Drive"), ("box", "Box"),
                            ("nextcloud", "Nextcloud"), ("owncloud", "ownCloud"), ("mega", "MEGA"), ("pcloud", "pCloud"),
                            ("icloud", "iCloud"), ("sharing", "Windows"), ("offline", "Windows"),
                            ("enhancedstorage", "Windows"), ("system32", "Windows")):
        if clave in texto:
            return programa
    return os.path.splitext(os.path.basename(dll))[0] if dll else "Desconocido"


def listar_overlays() -> List[Overlay]:
    """En el orden en que Windows los carga (alfabético por el nombre de la
    subclave, por eso los programas ponen espacios delante para colarse)."""
    nombres = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, K_OVERLAYS, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            i = 0
            while True:
                try:
                    nombres.append(winreg.EnumKey(k, i))
                except OSError:
                    break
                i += 1
    except OSError:
        return []
    out = []
    libres = PLAZAS_OVERLAY - PLAZAS_RESERVADAS
    for pos, nombre in enumerate(sorted(nombres), start=1):
        clsid = ""
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"{K_OVERLAYS}\{nombre}", 0,
                                winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
                clsid = winreg.QueryValueEx(k, "")[0] or ""
        except OSError:
            pass
        nombre_clsid, dll = _leer_clsid(clsid) if clsid else ("", "")
        estado = OVERLAY_ACTIVO if pos <= libres else OVERLAY_RIESGO if pos <= PLAZAS_OVERLAY else OVERLAY_FUERA
        out.append(Overlay(pos, nombre, clsid, _programa_de(dll, nombre + " " + nombre_clsid), dll, estado))
    return out


def exportar_clave_reg(clave_completa: str, destino: str) -> Tuple[bool, str]:
    """Copia de seguridad de una clave con reg.exe (vista de 64 bits)."""
    os.makedirs(os.path.dirname(destino), exist_ok=True)
    ok, salida = _ejecutar(["reg", "export", clave_completa, destino, "/y", "/reg:64"], timeout=30)
    return ok, salida


def _copiar_valores(origen, destino):
    i = 0
    while True:
        try:
            nombre, valor, tipo = winreg.EnumValue(origen, i)
        except OSError:
            break
        winreg.SetValueEx(destino, nombre, 0, tipo, valor)
        i += 1


def renombrar_overlay(nombre_actual: str, nombre_nuevo: str) -> Tuple[bool, str]:
    """Renombra la subclave (copia valores + borra la antigua). Solo valores:
    las subclaves de overlays no tienen subclaves propias."""
    if not nombre_nuevo or nombre_nuevo == nombre_actual:
        return False, "Nombre no válido"
    acceso = winreg.KEY_ALL_ACCESS | winreg.KEY_WOW64_64KEY
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, K_OVERLAYS, 0, acceso) as raiz:
            try:
                winreg.OpenKey(raiz, nombre_nuevo).Close()
                return False, f"Ya existe una entrada llamada «{nombre_nuevo.strip()}» con esos espacios"
            except OSError:
                pass
            with winreg.OpenKey(raiz, nombre_actual, 0, acceso) as viejo, \
                    winreg.CreateKeyEx(raiz, nombre_nuevo, 0, acceso) as nuevo:
                _copiar_valores(viejo, nuevo)
            winreg.DeleteKeyEx(raiz, nombre_actual, winreg.KEY_WOW64_64KEY, 0)
        return True, ""
    except PermissionError:
        return False, "Hace falta ejecutar MASys como administrador"
    except OSError as e:
        return False, str(e)


def nombre_priorizado(nombre: str, overlays: List[Overlay]) -> str:
    """Nombre con un espacio inicial más que el que más espacios tenga ahora,
    para que pase a ser el primero de la lista."""
    max_espacios = max((len(o.nombre) - len(o.nombre.lstrip(" ")) for o in overlays), default=0)
    return " " * (max_espacios + 1) + nombre.lstrip(" ")


# ─────────────────────────────────────────────────────────────────
# Iconos personalizados de unidades
# ─────────────────────────────────────────────────────────────────

K_UNIDADES = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\DriveIcons"


def iconos_unidades() -> List[Tuple[str, str, str]]:
    """(unidad, icono, etiqueta) de las unidades con icono o nombre personalizado."""
    out = []
    try:
        raiz = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, K_UNIDADES, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
    except OSError:
        return out
    with raiz:
        i = 0
        while True:
            try:
                letra = winreg.EnumKey(raiz, i)
            except OSError:
                break
            i += 1
            icono = etiqueta = ""
            for sub, destino in (("DefaultIcon", "icono"), ("DefaultLabel", "etiqueta")):
                try:
                    with winreg.OpenKey(raiz, rf"{letra}\{sub}") as k:
                        valor = winreg.QueryValueEx(k, "")[0] or ""
                    if destino == "icono":
                        icono = valor
                    else:
                        etiqueta = valor
                except OSError:
                    pass
            out.append((letra, icono, etiqueta))
    return out


def _borrar_arbol(raiz, ruta: str):
    with winreg.OpenKey(raiz, ruta, 0, winreg.KEY_ALL_ACCESS | winreg.KEY_WOW64_64KEY) as k:
        while True:
            try:
                sub = winreg.EnumKey(k, 0)
            except OSError:
                break
            _borrar_arbol(raiz, rf"{ruta}\{sub}")
    winreg.DeleteKeyEx(raiz, ruta, winreg.KEY_WOW64_64KEY, 0)


def quitar_icono_unidad(letra: str) -> Tuple[bool, str]:
    try:
        _borrar_arbol(winreg.HKEY_LOCAL_MACHINE, rf"{K_UNIDADES}\{letra}")
        _avisar_cambio_shell()
        return True, ""
    except PermissionError:
        return False, "Hace falta ejecutar MASys como administrador"
    except OSError as e:
        return False, str(e)


def fijar_icono_unidad(letra: str, icono: str) -> Tuple[bool, str]:
    letra = letra.strip().rstrip(":\\").upper()[:1]
    if not letra.isalpha():
        return False, "Letra de unidad no válida"
    try:
        with winreg.CreateKeyEx(winreg.HKEY_LOCAL_MACHINE, rf"{K_UNIDADES}\{letra}\DefaultIcon", 0,
                                winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY) as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, icono)
        _avisar_cambio_shell()
        return True, ""
    except PermissionError:
        return False, "Hace falta ejecutar MASys como administrador"
    except OSError as e:
        return False, str(e)
