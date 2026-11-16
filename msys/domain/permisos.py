# -*- coding: utf-8 -*-
"""Motor de permisos de carpetas (sin Qt) para la sección 📂 Carpetas Compartidas.

Lo que hace: leer los permisos NTFS (propietario, herencia y cada entrada)
de un árbol de carpetas local o de red, guardarlos en una copia, restaurarlos
en el mismo servidor o en otro (con equivalencias de cuentas), analizarlos,
ver a qué tiene acceso una cuenta, cambiar el propietario y editarlos.

Decisiones:
- Se lee con la API de Windows (pywin32) y no con un PowerShell por carpeta
  como el análisis antiguo de la pestaña: es cientos de veces más rápido y
  admite rutas de más de 260 caracteres (prefijo \\\\?\\).
- Todo se guarda como SDDL completo (propietario, grupo y DACL con la marca
  de herencia de cada entrada). `icacls /save` no guarda el propietario: se
  ofrece solo como exportación adicional.
- Se comparan y restauran SOLO las entradas propias (las no heredadas), el
  propietario y si la herencia está cortada. Lo heredado se deriva de la
  carpeta de arriba: al escribir con SetNamedSecurityInfo Windows lo vuelve a
  calcular y lo propaga hacia dentro, igual que el Explorador.
- Archivos: por defecto solo los que tienen permisos propios (los demás
  heredan y no aportan nada a una copia); se puede pedir todos.
- Cuentas: los SID universales (Todos, Administradores, SYSTEM…) valen en
  cualquier equipo; los de dominio o de equipo (S-1-5-21-…) se traducen por
  NOMBRE al restaurar en otro servidor o dominio (tabla de equivalencias).
- Permisos de red (recursos compartidos SMB): con NetShareEnum/NetShareSetInfo
  (RPC sobre SMB), que no necesita WinRM para un servidor remoto.
- Nada escribe sin que el llamador haya hecho antes una copia del estado
  actual de lo que va a cambiar (`copia_previa`)."""

import getpass
import gzip
import json
import os
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, Iterable, List, Optional, Set, Tuple

import pywintypes
import win32api
import win32con
import win32file
import win32net
import win32security
import win32wnet

FORMATO = "MASYS-PERMISOS"
VERSION_FORMATO = 1
EXTENSION = ".msysperm"

# Qué archivos se leen además de las carpetas
ARCHIVOS_NINGUNO, ARCHIVOS_PROPIOS, ARCHIVOS_TODOS = 0, 1, 2
TEXTO_ARCHIVOS = ["Solo carpetas", "Carpetas y archivos con permisos propios", "Carpetas y todos los archivos"]

HILOS_LECTURA = 16
INFO = (win32security.OWNER_SECURITY_INFORMATION | win32security.GROUP_SECURITY_INFORMATION
        | win32security.DACL_SECURITY_INFORMATION)
_REVISION = win32security.SDDL_REVISION_1
_ACCESO_DENEGADO = 5

# ── Derechos ────────────────────────────────────────────────────────────────
FULL, MODIFY, READ_EXEC, READ, WRITE = 0x1F01FF, 0x1301BF, 0x1200A9, 0x120089, 0x100116
_GENERICOS = {0x10000000: FULL, 0x80000000: READ, 0x40000000: 0x120116, 0x20000000: 0x1200A0}
_CODIGOS = {"FA": FULL, "FR": READ, "FW": 0x120116, "FX": 0x1200A0,
            "GA": 0x10000000, "GR": 0x80000000, "GW": 0x40000000, "GX": 0x20000000,
            "RC": 0x20000, "SD": 0x10000, "WD": 0x40000, "WO": 0x80000,
            "CC": 0x1, "DC": 0x2, "LC": 0x4, "SW": 0x8, "RP": 0x10, "WP": 0x20, "DT": 0x40, "LO": 0x80, "CR": 0x100}
# Bits que permiten cambiar contenido o permisos
_BITS_ESCRITURA = 0x2 | 0x4 | 0x10 | 0x40 | 0x100 | 0x10000 | 0x40000 | 0x80000
_BITS_CONTROL = 0x40000 | 0x80000   # cambiar permisos / tomar posesión

NIVELES = [("Control total", FULL), ("Modificar", MODIFY), ("Lectura y ejecución", READ_EXEC),
           ("Lectura", READ), ("Escritura", WRITE)]
# Para editar: nombre → máscara
NIVELES_EDITABLES = NIVELES + [("Mostrar el contenido de la carpeta", READ_EXEC)]

# «Se aplica a» → banderas SDDL de herencia (orden igual que en Windows)
APLICA = [("Esta carpeta, subcarpetas y archivos", "OICI"), ("Esta carpeta y subcarpetas", "CI"),
          ("Esta carpeta y archivos", "OI"), ("Solo esta carpeta", ""),
          ("Solo subcarpetas y archivos", "OICIIO"), ("Solo subcarpetas", "CIIO"), ("Solo archivos", "OIIO")]

# ── Cuentas ────────────────────────────────────────────────────────────────
TODOS, ANONIMO, AUTENTICADOS, USUARIOS = "S-1-1-0", "S-1-5-7", "S-1-5-11", "S-1-5-32-545"
ADMINISTRADORES, SYSTEM, CREADOR, DERECHOS_PROPIETARIO = "S-1-5-32-544", "S-1-5-18", "S-1-3-0", "S-1-3-4"
RED, INTERACTIVO = "S-1-5-2", "S-1-5-4"
TRUSTED_INSTALLER = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
_ALIAS = {"WD": TODOS, "AN": ANONIMO, "AU": AUTENTICADOS, "BU": USUARIOS, "BA": ADMINISTRADORES, "SY": SYSTEM,
          "CO": CREADOR, "CG": "S-1-3-1", "OW": DERECHOS_PROPIETARIO, "NU": RED, "IU": INTERACTIVO, "SU": "S-1-5-6",
          "LS": "S-1-5-19", "NS": "S-1-5-20", "PU": "S-1-5-32-547", "AO": "S-1-5-32-548", "SO": "S-1-5-32-549",
          "PO": "S-1-5-32-550", "BO": "S-1-5-32-551", "RE": "S-1-5-32-552", "RU": "S-1-5-32-554",
          "RD": "S-1-5-32-555", "NO": "S-1-5-32-556", "MU": "S-1-5-32-558", "LU": "S-1-5-32-559",
          "IS": "S-1-5-32-568", "CY": "S-1-5-32-569", "ER": "S-1-5-32-573", "RM": "S-1-5-32-580",
          "HA": "S-1-5-32-578", "AC": "S-1-15-2-1", "RC": "S-1-5-12", "WR": "S-1-5-33", "PS": "S-1-5-10",
          "ED": "S-1-5-9", "LW": "S-1-16-4096", "ME": "S-1-16-8192", "HI": "S-1-16-12288", "SI": "S-1-16-16384"}
# RID de dominio que son «de administración» (Admins. del dominio, de empresa, de esquema)
_RID_ADMIN = {"512", "518", "519"}
TIPOS_CUENTA = {1: "Usuario", 2: "Grupo", 3: "Dominio", 4: "Grupo local", 5: "Grupo de Windows", 6: "Cuenta eliminada",
                7: "No válida", 8: "Desconocida", 9: "Equipo"}

_cache_alias: Dict[str, str] = {}
_cache_nombres: Dict[Tuple[str, str], Tuple[str, int]] = {}


def sid_canonico(sid: str) -> str:
    """«BA» → «S-1-5-32-544». Los alias relativos al dominio (DU, DA…) se
    resuelven con Windows; si no hay dominio se deja el alias tal cual."""
    if not sid:
        return ""
    s = sid.strip()
    if s.upper().startswith("S-"):
        return s.upper()
    a = s.upper()
    if a in _ALIAS:
        return _ALIAS[a]
    if a not in _cache_alias:
        try:
            _cache_alias[a] = win32security.ConvertSidToStringSid(win32security.ConvertStringSidToSid(a))
        except pywintypes.error:
            _cache_alias[a] = a
    return _cache_alias[a]


def es_universal(sid: str) -> bool:
    """SID que significa lo mismo en cualquier equipo (no hay que traducirlo)."""
    s = sid_canonico(sid)
    return not s.startswith("S-1-5-21-")


def es_administracion(sid: str) -> bool:
    s = sid_canonico(sid)
    return (s in (ADMINISTRADORES, SYSTEM, TRUSTED_INSTALLER, CREADOR, DERECHOS_PROPIETARIO)
            or (s.startswith("S-1-5-21-") and s.rsplit("-", 1)[-1] in _RID_ADMIN))


def nombre_de_sid(sid: str, servidor: Optional[str] = None) -> Tuple[str, int]:
    """(«DOMINIO\\cuenta», tipo) o («», 0) si la cuenta ya no existe."""
    s = sid_canonico(sid)
    clave = (servidor or "", s)
    if clave not in _cache_nombres:
        try:
            nombre, dominio, tipo = win32security.LookupAccountSid(servidor, win32security.ConvertStringSidToSid(s))
            _cache_nombres[clave] = (f"{dominio}\\{nombre}" if dominio else nombre, tipo)
        except pywintypes.error:
            _cache_nombres[clave] = ("", 0)
    return _cache_nombres[clave]


def resolver_cuenta(nombre: str, servidor: Optional[str] = None) -> Tuple[str, str, int]:
    """Nombre escrito por el usuario → (SID, «DOMINIO\\cuenta», tipo). ValueError si no existe."""
    n = (nombre or "").strip()
    if not n:
        raise ValueError("Escribe el nombre de una cuenta o grupo.")
    if n.upper().startswith("S-1-"):
        nom, tipo = nombre_de_sid(n, servidor)
        return sid_canonico(n), nom or n.upper(), tipo
    try:
        sid, dominio, tipo = win32security.LookupAccountName(servidor, n)
    except pywintypes.error:
        donde = f" en {servidor.lstrip(chr(92))}" if servidor else ""
        raise ValueError(f"No existe la cuenta o grupo «{n}»{donde}.")
    cuenta = n.split("\\")[-1]
    nom, _t = nombre_de_sid(win32security.ConvertSidToStringSid(sid), servidor)
    return win32security.ConvertSidToStringSid(sid), nom or (f"{dominio}\\{cuenta}" if dominio else cuenta), tipo


# ── SDDL ────────────────────────────────────────────────────────────────────

def mascara(derechos: str) -> int:
    """«FA», «0x1301bf» o «CCDCLC…» → máscara numérica."""
    d = (derechos or "").strip()
    if not d:
        return 0
    if d.lower().startswith("0x"):
        return int(d, 16)
    if d.isdigit():
        return int(d)
    total = 0
    for i in range(0, len(d), 2):
        total |= _CODIGOS.get(d[i:i + 2].upper(), 0)
    return total


def mascara_efectiva(m: int) -> int:
    """Traduce los derechos genéricos (GA, GR…) a los de archivo."""
    for bit, equivalente in _GENERICOS.items():
        if m & bit:
            m = (m & ~bit) | equivalente
    return m


def nombre_nivel(m: int) -> str:
    m = mascara_efectiva(m)
    if not m:
        return "Ninguno"
    # El Explorador no pone «Sincronizar» al DENEGAR (Escritura denegada = 0x116): se nombra igual
    m |= 0x100000
    for nombre, valor in NIVELES:
        if m == valor:
            return nombre
    if (m & READ) == READ and (m & WRITE) == WRITE and (m & MODIFY) != MODIFY:
        return "Lectura y escritura" + (" (+ especiales)" if m & ~(READ | WRITE) else "")
    for nombre, valor in NIVELES:
        if (m & valor) == valor:
            return f"{nombre} (+ especiales)"
    return "Especiales"


def texto_aplica(banderas: str) -> str:
    b = _banderas(banderas)
    clave = "".join(x for x in ("OI", "CI", "IO") if x in b)
    texto = next((t for t, f in APLICA if f == clave), "Solo esta carpeta")
    return texto + (" (solo un nivel)" if "NP" in b else "")


def _banderas(texto: str) -> Set[str]:
    return {texto[i:i + 2].upper() for i in range(0, len(texto or ""), 2)}


@dataclass
class Entrada:
    """Una entrada de permiso (ACE) tal como va en el SDDL."""
    tipo: str            # A = permitir, D = denegar (OA, AU, ML… se conservan tal cual)
    banderas: str        # OICIID…
    derechos: str        # FA, 0x1301bf…
    sid: str             # canónico (S-1-…)
    resto: Tuple[str, ...] = ()  # GUID de objeto, GUID heredado y condición (casi siempre vacíos)

    @property
    def heredada(self) -> bool:
        return "ID" in _banderas(self.banderas)

    @property
    def solo_herencia(self) -> bool:
        return "IO" in _banderas(self.banderas)

    @property
    def permitir(self) -> bool:
        return self.tipo.upper() in ("A", "OA", "XA", "ZA")

    @property
    def denegar(self) -> bool:
        return self.tipo.upper() in ("D", "OD", "XD")

    @property
    def mascara(self) -> int:
        return mascara(self.derechos)

    def texto(self, sid: Optional[str] = None) -> str:
        partes = [self.tipo, self.banderas, self.derechos] + list(self.resto[:2] or ("", "")) + [sid or self.sid]
        partes += list(self.resto[2:])
        return "(" + ";".join(partes) + ")"

    def clave(self) -> Tuple:
        """Para comparar sin importar cómo se escribió el derecho («FA» = «0x1f01ff»)."""
        b = "".join(sorted(_banderas(self.banderas) - {"ID"}))
        return (self.tipo.upper(), b, mascara_efectiva(self.mascara), sid_canonico(self.sid), self.resto)

    def sin_herencia(self) -> "Entrada":
        """La misma entrada como propia (al cortar la herencia copiando lo heredado)."""
        b = "".join(x for x in sorted(_banderas(self.banderas)) if x != "ID")
        return Entrada(self.tipo, b, self.derechos, self.sid, self.resto)


@dataclass
class Descriptor:
    propietario: str = ""
    grupo: str = ""
    protegida: bool = False        # herencia cortada en esta carpeta (D:P)
    banderas_dacl: str = ""        # P, AI, AR…
    entradas: List[Entrada] = field(default_factory=list)
    sin_dacl: bool = False         # D:NO_ACCESS_CONTROL o sin DACL = acceso total para todos

    @classmethod
    def desde_sddl(cls, sddl: str) -> "Descriptor":
        partes = _secciones_sddl(sddl or "")
        d = cls(propietario=sid_canonico(partes.get("O", "")), grupo=sid_canonico(partes.get("G", "")))
        dacl = partes.get("D")
        if dacl is None:
            d.sin_dacl = True
            return d
        corte = dacl.find("(")
        banderas = dacl if corte < 0 else dacl[:corte]
        d.banderas_dacl = banderas
        d.protegida = "P" in re.sub(r"NO_ACCESS_CONTROL", "", banderas.upper()).replace("AI", "").replace("AR", "")
        if "NO_ACCESS_CONTROL" in banderas.upper():
            d.sin_dacl = True
        for cuerpo in re.findall(r"\(([^()]*)\)", dacl):
            c = cuerpo.split(";")
            if len(c) < 6:
                continue
            d.entradas.append(Entrada(c[0], c[1], c[2], sid_canonico(c[5]), tuple([c[3], c[4]] + c[6:])))
        return d

    @property
    def propias(self) -> List[Entrada]:
        return [e for e in self.entradas if not e.heredada]

    @property
    def heredadas(self) -> List[Entrada]:
        return [e for e in self.entradas if e.heredada]

    @property
    def tiene_propios(self) -> bool:
        return self.protegida or bool(self.propias) or self.sin_dacl

    def sddl(self, solo_propias: bool = False, mapa: Optional[Callable[[str], str]] = None) -> str:
        """SDDL; con mapa(sid) → sid nuevo («» = se quita la entrada)."""
        traducir = mapa or (lambda s: s)
        texto = ""
        if self.propietario:
            texto += "O:" + (traducir(self.propietario) or ADMINISTRADORES)
        if self.grupo:
            g = traducir(self.grupo)
            if g:
                texto += "G:" + g
        if self.sin_dacl:
            return texto + "D:NO_ACCESS_CONTROL"
        flags = "P" if self.protegida else ""
        flags += "".join(x for x in ("AI", "AR") if x in (self.banderas_dacl or "").upper())
        texto += "D:" + flags
        for e in (self.propias if solo_propias else self.entradas):
            s = traducir(e.sid)
            if s:
                texto += e.texto(s)
        return texto

    def traducido(self, mapa: Callable[[str], str]) -> "Descriptor":
        return Descriptor.desde_sddl(self.sddl(mapa=mapa))

    def clave(self) -> Tuple:
        """Lo que se compara al restaurar: propietario, herencia y entradas propias."""
        return (sid_canonico(self.propietario), self.protegida, self.sin_dacl, tuple(e.clave() for e in self.propias))


def _secciones_sddl(sddl: str) -> Dict[str, str]:
    """Parte «O:…G:…D:…S:…» respetando los paréntesis."""
    marcas, nivel = [], 0
    for i, ch in enumerate(sddl):
        if ch == "(":
            nivel += 1
        elif ch == ")":
            nivel -= 1
        elif nivel == 0 and ch in "OGDS" and i + 1 < len(sddl) and sddl[i + 1] == ":":
            marcas.append((i, ch))
    partes = {}
    for n, (i, ch) in enumerate(marcas):
        fin = marcas[n + 1][0] if n + 1 < len(marcas) else len(sddl)
        partes[ch] = sddl[i + 2:fin]
    return partes


# ── Rutas, equipo y privilegios ─────────────────────────────────────────────

def ruta_larga(ruta: str) -> str:
    """Prefijo \\\\?\\ para pasar de 260 caracteres (también en rutas de red)."""
    r = (ruta or "").replace("/", "\\")
    if r.startswith("\\\\?\\"):
        return r
    if r.startswith("\\\\"):
        return "\\\\?\\UNC\\" + r[2:]
    r = os.path.abspath(r)
    return "\\\\?\\" + r


def normalizar_raiz(ruta: str) -> str:
    r = (ruta or "").strip().strip('"').replace("/", "\\")
    if len(r) == 2 and r[1] == ":":
        r += "\\"
    if len(r) > 3 and r.endswith("\\") and not r.startswith("\\\\"):
        r = r.rstrip("\\")
    if r.startswith("\\\\"):
        r = r.rstrip("\\")
    return r


def ruta_unc(ruta: str) -> str:
    """Una unidad de red (H:\\…) → \\\\servidor\\recurso\\…; lo demás igual."""
    r = normalizar_raiz(ruta)
    if r.startswith("\\\\") or len(r) < 2 or r[1] != ":":
        return r
    try:
        unc = win32wnet.WNetGetUniversalName(r, 1)
        return unc.rstrip("\\") if unc else r
    except pywintypes.error:
        return r


def servidor_de(ruta: str) -> Optional[str]:
    """«\\\\SERVIDOR» si la ruta es de red; None si es de este equipo."""
    r = ruta_unc(ruta)
    if r.startswith("\\\\"):
        nombre = r[2:].split("\\", 1)[0]
        if nombre and nombre.upper() not in (equipo_local().upper(), "LOCALHOST", "127.0.0.1", "."):
            return "\\\\" + nombre
    return None


def equipo_local() -> str:
    try:
        return win32api.GetComputerName()
    except pywintypes.error:
        return os.environ.get("COMPUTERNAME", "")


def equipo_de(ruta: str) -> str:
    s = servidor_de(ruta)
    return s[2:] if s else equipo_local()


def is_admin_actual() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def activar_privilegios() -> List[str]:
    """Copia de seguridad, restauración y tomar posesión: dejan leer y escribir
    permisos aunque la carpeta no dé acceso a los administradores. Solo se
    consiguen si MASys corre como administrador. Devuelve los activados."""
    activados = []
    try:
        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(),
                                               win32security.TOKEN_ADJUST_PRIVILEGES | win32security.TOKEN_QUERY)
    except pywintypes.error:
        return activados
    try:
        for nombre in (win32security.SE_BACKUP_NAME, win32security.SE_RESTORE_NAME,
                       win32security.SE_TAKE_OWNERSHIP_NAME):
            try:
                luid = win32security.LookupPrivilegeValue(None, nombre)
                win32security.AdjustTokenPrivileges(token, False, [(luid, win32security.SE_PRIVILEGE_ENABLED)])
                if win32api.GetLastError() == 0:
                    activados.append(nombre)
            except pywintypes.error:
                pass
    finally:
        win32api.CloseHandle(token)
    return activados


def conectar_con_credenciales(ruta: str, usuario: str, clave: str) -> str:
    """Abre la sesión con el servidor de una ruta de red con otro usuario (como
    «net use \\\\servidor\\IPC$»). Devuelve «» si fue bien o el motivo."""
    s = servidor_de(ruta)
    if not s or not usuario:
        return ""
    try:
        win32wnet.WNetAddConnection2(0, None, s + "\\IPC$", None, usuario, clave)
        return ""
    except pywintypes.error as e:
        if e.winerror == 1219:
            return (f"Ya hay una conexión abierta con {s[2:]} con otro usuario. Ciérrala antes "
                    f"(net use {s}\\IPC$ /delete) o usa el mismo usuario.")
        return f"No se pudo conectar con {s[2:]}: {e.strerror}"


def _abrir_con_privilegio(ruta: str, acceso: int):
    return win32file.CreateFile(ruta_larga(ruta), acceso,
                                win32file.FILE_SHARE_READ | win32file.FILE_SHARE_WRITE | win32file.FILE_SHARE_DELETE,
                                None, win32file.OPEN_EXISTING, win32file.FILE_FLAG_BACKUP_SEMANTICS, None)


def leer_sddl(ruta: str) -> str:
    """SDDL de una carpeta o archivo. Si la carpeta niega el acceso incluso a los
    administradores, se reintenta con el privilegio de copia de seguridad."""
    try:
        sd = win32security.GetNamedSecurityInfo(ruta_larga(ruta), win32security.SE_FILE_OBJECT, INFO)
    except pywintypes.error as e:
        if e.winerror != _ACCESO_DENEGADO:
            raise
        h = _abrir_con_privilegio(ruta, win32con.READ_CONTROL)
        try:
            sd = win32security.GetSecurityInfo(h, win32security.SE_FILE_OBJECT, INFO)
        finally:
            h.Close()
    return win32security.ConvertSecurityDescriptorToStringSecurityDescriptor(sd, _REVISION, INFO)


# ── Lectura de un árbol ─────────────────────────────────────────────────────

@dataclass
class Elemento:
    rel: str               # ruta relativa a la raíz («» = la raíz)
    carpeta: bool
    sddl: str = ""
    error: str = ""
    _desc: Optional[Descriptor] = field(default=None, repr=False, compare=False)

    @property
    def descriptor(self) -> Descriptor:
        if self._desc is None:
            self._desc = Descriptor.desde_sddl(self.sddl)
        return self._desc

    @property
    def nivel(self) -> int:
        return 0 if not self.rel else self.rel.count("\\") + 1


@dataclass
class Recurso:
    """Recurso compartido (SMB) cuya carpeta está dentro de lo leído."""
    nombre: str
    ruta_local: str
    rel: str
    sddl: str = ""
    descripcion: str = ""

    @property
    def descriptor(self) -> Descriptor:
        # Un recurso sin descriptor da Control total a Todos
        return Descriptor.desde_sddl(self.sddl) if self.sddl else Descriptor(sin_dacl=True)


@dataclass
class Lectura:
    """Resultado de leer un árbol (también es el contenido de una copia)."""
    raiz: str
    equipo: str
    fecha: str
    usuario: str
    archivos: int = ARCHIVOS_PROPIOS
    profundidad: int = 99
    ocultas: bool = True
    elementos: List[Elemento] = field(default_factory=list)
    cuentas: Dict[str, Tuple[str, int]] = field(default_factory=dict)
    recursos: List[Recurso] = field(default_factory=list)
    sin_listar: List[str] = field(default_factory=list)
    aviso_recursos: str = ""
    detenida: bool = False
    parcial: bool = False          # copia previa: solo lo que se iba a cambiar
    origen: str = ""               # copia previa: qué acción la provocó

    def ruta(self, rel: str) -> str:
        return self.raiz if not rel else os.path.join(self.raiz, rel)

    def por_rel(self) -> Dict[str, Elemento]:
        return {e.rel.lower(): e for e in self.elementos}

    def nombre(self, sid: str) -> str:
        s = sid_canonico(sid)
        n = self.cuentas.get(s, ("", 0))[0]
        return n or (s if not s.startswith("S-1-5-21-") else f"Cuenta eliminada ({s})")

    def tipo(self, sid: str) -> int:
        return self.cuentas.get(sid_canonico(sid), ("", 0))[1]

    def huerfana(self, sid: str) -> bool:
        s = sid_canonico(sid)
        return s.startswith("S-1-5-21-") and not self.cuentas.get(s, ("", 0))[0]


def _atributos(entrada) -> int:
    try:
        return entrada.stat(follow_symlinks=False).st_file_attributes
    except OSError:
        return 0


def leer_arbol(raiz: str, profundidad: int = 99, archivos: int = ARCHIVOS_PROPIOS, ocultas: bool = True,
               progreso: Optional[Callable[[int, str], None]] = None,
               parar: Optional[Callable[[], bool]] = None, solo: Optional[Iterable[str]] = None) -> Lectura:
    """Lee los permisos de la raíz y de todo lo que hay dentro.
    solo = lista de rutas relativas: lee únicamente esas (copia previa)."""
    raiz = normalizar_raiz(raiz)
    if not raiz:
        raise ValueError("Indica una carpeta.")
    if not os.path.isdir(ruta_larga(raiz)):
        raise ValueError(f"La carpeta no existe o no es accesible:\n{raiz}")
    activar_privilegios()
    lec = Lectura(raiz=raiz, equipo=equipo_de(raiz), fecha=datetime.now().isoformat(timespec="seconds"),
                  usuario=_usuario_actual(), archivos=archivos, profundidad=profundidad, ocultas=ocultas)

    def leer(rel: str, carpeta: bool) -> Elemento:
        try:
            return Elemento(rel, carpeta, leer_sddl(lec.ruta(rel)))
        except pywintypes.error as e:
            return Elemento(rel, carpeta, "", e.strerror or str(e))

    if solo is not None:
        lec.parcial = True
        for rel in solo:
            ruta = lec.ruta(rel)
            if os.path.exists(ruta_larga(ruta)):
                lec.elementos.append(leer(rel, os.path.isdir(ruta_larga(ruta))))
        _resolver_cuentas(lec)
        return lec

    # 1) Recorrer el árbol (rápido: solo nombres y atributos)
    pendientes: List[Tuple[str, bool]] = []
    pila = [("", 0)]
    while pila:
        if parar and parar():
            lec.detenida = True
            break
        rel, nivel = pila.pop()
        pendientes.append((rel, True))
        if progreso and len(pendientes) % 2000 == 0:
            progreso(len(pendientes), f"Buscando carpetas y archivos… {rel}")
        try:
            with os.scandir(ruta_larga(lec.ruta(rel))) as it:
                hijos = list(it)
        except OSError:
            lec.sin_listar.append(rel)
            continue
        subcarpetas, ficheros = [], []
        for h in hijos:
            attrs = _atributos(h)
            if not ocultas and attrs & win32con.FILE_ATTRIBUTE_HIDDEN:
                continue
            hijo_rel = h.name if not rel else rel + "\\" + h.name
            try:
                es_carpeta = h.is_dir(follow_symlinks=False)
            except OSError:
                es_carpeta = False
            if es_carpeta:
                if nivel + 1 >= profundidad:
                    continue
                if attrs & win32con.FILE_ATTRIBUTE_REPARSE_POINT:
                    # Unión o vínculo: se apuntan sus permisos pero no se entra (evita bucles)
                    ficheros.append((hijo_rel, True))
                else:
                    subcarpetas.append((hijo_rel, nivel + 1))
            elif archivos != ARCHIVOS_NINGUNO:
                ficheros.append((hijo_rel, False))
        pendientes.extend(sorted(ficheros, key=lambda x: x[0].lower()))
        pila.extend(sorted(subcarpetas, key=lambda x: x[0].lower(), reverse=True))

    # 2) Leer los permisos en paralelo (en un servidor de red casi todo es esperar)
    def util(el: Elemento) -> bool:
        return el.carpeta or archivos == ARCHIVOS_TODOS or bool(el.error) or el.descriptor.tiene_propios

    with ThreadPoolExecutor(max_workers=HILOS_LECTURA) as pool:
        for inicio in range(0, len(pendientes), 1000):
            if parar and parar():
                lec.detenida = True
                break
            bloque = pendientes[inicio:inicio + 1000]
            lec.elementos.extend(el for el in pool.map(lambda x: leer(*x), bloque) if util(el))
            if progreso:
                progreso(len(lec.elementos), f"Leyendo permisos: {min(inicio + 1000, len(pendientes))} de {len(pendientes)}")
    if progreso:
        progreso(len(lec.elementos), "Buscando los nombres de las cuentas…")
    _resolver_cuentas(lec)
    lec.recursos, lec.aviso_recursos = leer_recursos(raiz)
    return lec


def _usuario_actual() -> str:
    try:
        return win32api.GetUserNameEx(win32con.NameSamCompatible)  # DOMINIO\usuario
    except Exception:
        return getpass.getuser()


def _resolver_cuentas(lec: Lectura) -> None:
    servidor = servidor_de(lec.raiz)
    sids = set()
    for e in lec.elementos:
        if e.sddl:
            d = e.descriptor
            sids.add(d.propietario)
            sids.update(x.sid for x in d.entradas)
    for r in lec.recursos:
        sids.update(x.sid for x in r.descriptor.entradas)
    for s in sids:
        if s and s not in lec.cuentas:
            lec.cuentas[s] = nombre_de_sid(s, servidor)


# ── Recursos compartidos (SMB) ──────────────────────────────────────────────

def ruta_local_en_servidor(raiz: str) -> Tuple[Optional[str], str]:
    """(servidor, ruta de la raíz vista desde el propio servidor). Para
    \\\\SRV\\Datos\\Contab devuelve (\\\\SRV, D:\\Datos\\Contab)."""
    unc = ruta_unc(raiz)
    if not unc.startswith("\\\\"):
        return None, os.path.abspath(unc)
    partes = unc[2:].split("\\")
    servidor = servidor_de(unc)
    if len(partes) < 2:
        raise ValueError("Ruta de red incompleta: falta el recurso (\\\\servidor\\recurso).")
    info = win32net.NetShareGetInfo(servidor, partes[1], 2)
    base = info.get("path") or ""
    if not base:
        raise ValueError(f"«{partes[1]}» no es una carpeta compartida.")
    return servidor, os.path.join(base, *partes[2:]) if len(partes) > 2 else base


def _dentro(ruta: str, base: str) -> Optional[str]:
    r, b = os.path.normcase(ruta.rstrip("\\")), os.path.normcase(base.rstrip("\\"))
    if r == b:
        return ""
    if r.startswith(b + "\\") or (b.endswith(":") and r.startswith(b + "\\")):
        return ruta.rstrip("\\")[len(base.rstrip("\\")) + 1:]
    return None


def leer_recursos(raiz: str) -> Tuple[List[Recurso], str]:
    """Recursos compartidos (no administrativos) cuya carpeta está dentro de la raíz."""
    try:
        servidor, base = ruta_local_en_servidor(raiz)
        recursos, reanudar = [], 0
        while True:
            filas, _total, reanudar = win32net.NetShareEnum(servidor, 502, reanudar)
            for f in filas:
                if (f.get("type") or 0) != 0:   # solo carpetas (no impresoras, IPC$ ni C$…)
                    continue
                rel = _dentro(f.get("path") or "", base)
                if rel is None:
                    continue
                sd = f.get("security_descriptor")
                sddl = (win32security.ConvertSecurityDescriptorToStringSecurityDescriptor(
                    sd, _REVISION, win32security.DACL_SECURITY_INFORMATION) if sd else "")
                recursos.append(Recurso(f.get("netname") or "", f.get("path") or "", rel, sddl, f.get("remark") or ""))
            if not reanudar:
                break
        return recursos, ""
    except pywintypes.error as e:
        return [], f"No se pudieron leer los permisos de red de los recursos compartidos: {e.strerror}"
    except ValueError as e:
        return [], str(e)


def aplicar_recurso(servidor: Optional[str], nombre: str, sddl: str) -> None:
    info = win32net.NetShareGetInfo(servidor, nombre, 502)
    info["security_descriptor"] = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(sddl, _REVISION)
    win32net.NetShareSetInfo(servidor, nombre, 502, info)


def crear_recurso(servidor: Optional[str], nombre: str, ruta_local: str, sddl: str, descripcion: str = "") -> None:
    win32net.NetShareAdd(servidor, 502, {
        "netname": nombre, "type": 0, "remark": descripcion, "permissions": 0, "max_uses": -1, "current_uses": 0,
        "path": ruta_local, "passwd": None, "reserved": 0,
        "security_descriptor": win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(sddl, _REVISION)})


# ── Copias (.msysperm) ──────────────────────────────────────────────────────

def carpeta_copias() -> str:
    from msys.core.utils import get_informes_folder
    c = os.path.join(get_informes_folder(), "PERMISOS")
    os.makedirs(c, exist_ok=True)
    return c


def nombre_copia(lec: Lectura, prefijo: str = "Permisos") -> str:
    base = os.path.basename(lec.raiz.rstrip("\\")) or lec.raiz.replace(":", "").replace("\\", "")
    limpio = re.sub(r'[<>:"/\\|?*\s]+', "_", f"{prefijo}_{lec.equipo}_{base}").strip("_")
    return f"{limpio}_{datetime.now().strftime('%Y%m%d_%H%M%S')}{EXTENSION}"


def guardar(lec: Lectura, ruta: str) -> str:
    datos = {
        "formato": FORMATO, "version": VERSION_FORMATO, "raiz": lec.raiz, "equipo": lec.equipo, "fecha": lec.fecha,
        "usuario": lec.usuario, "archivos": lec.archivos, "profundidad": lec.profundidad, "ocultas": lec.ocultas,
        "parcial": lec.parcial, "origen": lec.origen, "detenida": lec.detenida, "sin_listar": lec.sin_listar,
        "elementos": [[e.rel, 1 if e.carpeta else 0, e.sddl, e.error] for e in lec.elementos],
        "cuentas": {s: list(v) for s, v in lec.cuentas.items()},
        "recursos": [[r.nombre, r.ruta_local, r.rel, r.sddl, r.descripcion] for r in lec.recursos],
    }
    temporal = ruta + ".tmp"
    with gzip.open(temporal, "wt", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False)
    os.replace(temporal, ruta)
    return ruta


def cargar(ruta: str) -> Lectura:
    try:
        with gzip.open(ruta, "rt", encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError) as e:
        raise ValueError(f"No es una copia de permisos de MASys o está dañada:\n{ruta}\n\n{e}")
    if d.get("formato") != FORMATO:
        raise ValueError(f"No es una copia de permisos de MASys:\n{ruta}")
    if int(d.get("version", 0)) > VERSION_FORMATO:
        raise ValueError("La copia se hizo con una versión más nueva de MASys: actualiza MASys para abrirla.")
    lec = Lectura(raiz=d["raiz"], equipo=d.get("equipo", ""), fecha=d.get("fecha", ""), usuario=d.get("usuario", ""),
                  archivos=int(d.get("archivos", ARCHIVOS_PROPIOS)), profundidad=int(d.get("profundidad", 99)),
                  ocultas=bool(d.get("ocultas", True)), parcial=bool(d.get("parcial")), origen=d.get("origen", ""),
                  detenida=bool(d.get("detenida")), sin_listar=list(d.get("sin_listar", [])))
    lec.elementos = [Elemento(r, bool(c), s, err) for r, c, s, err in d.get("elementos", [])]
    lec.cuentas = {s: (v[0], int(v[1])) for s, v in d.get("cuentas", {}).items()}
    lec.recursos = [Recurso(*r) for r in d.get("recursos", [])]
    return lec


def resumen_copia(ruta: str) -> Dict[str, object]:
    """Datos para la lista de copias (sin montar los objetos)."""
    with gzip.open(ruta, "rt", encoding="utf-8") as f:
        d = json.load(f)
    if d.get("formato") != FORMATO:
        raise ValueError("No es una copia de permisos de MASys")
    return {"ruta": ruta, "fecha": d.get("fecha", ""), "equipo": d.get("equipo", ""), "raiz": d.get("raiz", ""),
            "elementos": len(d.get("elementos", [])), "recursos": len(d.get("recursos", [])),
            "parcial": bool(d.get("parcial")), "origen": d.get("origen", ""), "usuario": d.get("usuario", ""),
            "detenida": bool(d.get("detenida"))}


def exportar_icacls(raiz: str, destino: str) -> Tuple[bool, str]:
    """Copia adicional en el formato de icacls /save (sin propietario), para
    quien quiera restaurarla con icacls /restore en un equipo sin MASys."""
    _ok, salida = _icacls([normalizar_raiz(raiz), "/save", destino, "/t", "/c", "/q"])
    return os.path.isfile(destino), salida


# ── Diferencias ─────────────────────────────────────────────────────────────

def describir_entrada(e: Entrada, nombre: Callable[[str], str]) -> str:
    tipo = "Permitir" if e.permitir else ("Denegar" if e.denegar else e.tipo)
    return f"{tipo} {nombre_nivel(e.mascara)} a {nombre(e.sid)} · {texto_aplica(e.banderas)}"


def diferencias(antes: Descriptor, despues: Descriptor, nombre: Callable[[str], str]) -> List[str]:
    cambios = []
    if sid_canonico(antes.propietario) != sid_canonico(despues.propietario):
        cambios.append(f"Propietario: {nombre(antes.propietario)} → {nombre(despues.propietario)}")
    if antes.protegida != despues.protegida:
        cambios.append("Herencia: " + ("se corta" if despues.protegida else "vuelve a heredar de la carpeta de arriba"))
    if antes.sin_dacl != despues.sin_dacl:
        cambios.append("Sin permisos (acceso total)" if despues.sin_dacl else "Vuelve a tener permisos")
    a = {e.clave(): e for e in antes.propias}
    b = {e.clave(): e for e in despues.propias}
    cambios += [f"✕ Quitar: {describir_entrada(e, nombre)}" for k, e in a.items() if k not in b]
    cambios += [f"+ Poner: {describir_entrada(e, nombre)}" for k, e in b.items() if k not in a]
    if not cambios and [e.clave() for e in antes.propias] != [e.clave() for e in despues.propias]:
        cambios.append("Mismo contenido en otro orden")
    return cambios


@dataclass
class Diferencia:
    rel: str
    carpeta: bool
    estado: str               # Cambia / Nueva / Desaparece / Igual / No existe en el destino / Solo en el destino / Error
    detalle: List[str] = field(default_factory=list)
    objetivo: Optional[Descriptor] = None   # lo que se escribirá al restaurar
    aplicable: bool = False


CAMBIA, NUEVA, DESAPARECE, IGUAL = "Cambia", "Nueva", "Desaparece", "Igual"
NO_EXISTE, SOLO_DESTINO, ERROR = "No existe en el destino", "Solo en el destino", "No se pudo leer"


def comparar(a: Lectura, b: Lectura) -> List[Diferencia]:
    """Qué cambió de la copia a a la b (las dos de la misma carpeta)."""
    nombre = lambda s: b.nombre(s) if b.cuentas.get(sid_canonico(s), ("", 0))[0] else a.nombre(s)  # noqa: E731
    ea, eb = a.por_rel(), b.por_rel()
    salida = []
    for clave in sorted(set(ea) | set(eb), key=lambda k: (k.count("\\"), k)):
        x, y = ea.get(clave), eb.get(clave)
        if x and not y:
            salida.append(Diferencia(x.rel, x.carpeta, DESAPARECE))
        elif y and not x:
            salida.append(Diferencia(y.rel, y.carpeta, NUEVA, [describir_entrada(e, nombre) for e in y.descriptor.propias]))
        elif x.error or y.error:
            if x.error != y.error:
                salida.append(Diferencia(y.rel, y.carpeta, ERROR, [y.error or x.error]))
        elif x.descriptor.clave() != y.descriptor.clave():
            salida.append(Diferencia(y.rel, y.carpeta, CAMBIA, diferencias(x.descriptor, y.descriptor, nombre)))
    return salida


# ── Equivalencias de cuentas (restaurar en otro servidor o dominio) ─────────

@dataclass
class Equivalencia:
    sid_origen: str
    nombre_origen: str
    tipo: int
    sid_destino: str           # «» = se quita esa entrada al restaurar
    nombre_destino: str
    estado: str


EQ_IGUAL, EQ_TRADUCIDA, EQ_NO_ENCONTRADA, EQ_ELIMINADA, EQ_UNIVERSAL = (
    "La misma cuenta", "Equivalente en el destino", "No existe en el destino: se quitará",
    "Eliminada en el origen: se deja el SID", "Cuenta de Windows (vale en cualquier equipo)")


def sids_usados(lec: Lectura) -> Set[str]:
    sids = set()
    for e in lec.elementos:
        if e.sddl:
            sids.add(e.descriptor.propietario)
            sids.update(x.sid for x in e.descriptor.propias)
    for r in lec.recursos:
        sids.update(x.sid for x in r.descriptor.entradas)
    return {s for s in sids if s}


def dominios_de(lec: Lectura) -> List[str]:
    """Dominios (o nombres de equipo, en cuentas locales) que hay que traducir."""
    vistos = []
    for s in sorted(sids_usados(lec)):
        if es_universal(s):
            continue
        n = lec.cuentas.get(s, ("", 0))[0]
        if "\\" in n:
            d = n.split("\\", 1)[0]
            if d.upper() not in (x.upper() for x in vistos):
                vistos.append(d)
    return vistos


def proponer_equivalencias(lec: Lectura, raiz_destino: str, dominios: Dict[str, str]) -> List[Equivalencia]:
    """Para cada cuenta de la copia busca la del destino por nombre, cambiando
    el dominio según dominios {origen: destino} (sin distinguir mayúsculas)."""
    servidor = servidor_de(raiz_destino)
    mapa_dom = {k.upper(): v for k, v in (dominios or {}).items()}
    salida = []
    for s in sorted(sids_usados(lec), key=lambda x: lec.nombre(x).upper()):
        nombre, tipo = lec.cuentas.get(s, ("", 0))
        if es_universal(s):
            salida.append(Equivalencia(s, lec.nombre(s), tipo, s, lec.nombre(s), EQ_UNIVERSAL))
            continue
        if not nombre:
            salida.append(Equivalencia(s, lec.nombre(s), tipo, s, s, EQ_ELIMINADA))
            continue
        dom, _, cuenta = nombre.rpartition("\\")
        destino = f"{mapa_dom.get(dom.upper(), dom)}\\{cuenta}" if dom else cuenta
        salida.append(buscar_equivalencia(s, nombre, tipo, destino, servidor))
    return salida


def buscar_equivalencia(sid: str, nombre: str, tipo: int, destino: str, servidor: Optional[str]) -> Equivalencia:
    if not destino.strip():
        return Equivalencia(sid, nombre, tipo, "", "", EQ_NO_ENCONTRADA)
    try:
        sid2, nombre2, _t = resolver_cuenta(destino, servidor)
    except ValueError:
        return Equivalencia(sid, nombre, tipo, "", destino, EQ_NO_ENCONTRADA)
    return Equivalencia(sid, nombre, tipo, sid2, nombre2, EQ_IGUAL if sid2 == sid_canonico(sid) else EQ_TRADUCIDA)


def mapa_de(equivalencias: Iterable[Equivalencia]) -> Callable[[str], str]:
    tabla = {sid_canonico(e.sid_origen): e.sid_destino for e in equivalencias}
    return lambda s: tabla.get(sid_canonico(s), sid_canonico(s))


# ── Restaurar ───────────────────────────────────────────────────────────────

def _en_rama(rel: str, rama: str) -> bool:
    if not rama:
        return True
    r, b = rel.lower(), rama.lower().strip("\\")
    return r == b or r.startswith(b + "\\")


def planificar(copia: Lectura, raiz_destino: str, equivalencias: Iterable[Equivalencia] = (), rama: str = "",
               progreso=None, parar=None) -> Tuple[List[Diferencia], Lectura]:
    """Compara la copia (ya traducida con las equivalencias) con lo que hay ahora
    en el destino. Devuelve (diferencias, lectura del destino)."""
    mapa = mapa_de(equivalencias)
    rama = rama.strip("\\")
    base = raiz_destino if not rama else os.path.join(normalizar_raiz(raiz_destino), rama)
    if copia.parcial:
        rels = [e.rel for e in copia.elementos if _en_rama(e.rel, rama)]
        actual = leer_arbol(raiz_destino, solo=rels)
    else:
        prof = max(1, copia.profundidad - (rama.count("\\") + 1 if rama else 0))
        sub = leer_arbol(base, profundidad=prof, archivos=copia.archivos, ocultas=copia.ocultas,
                         progreso=progreso, parar=parar)
        actual = sub
        if rama:   # pasar las rutas a relativas a la raíz del destino
            for e in sub.elementos:
                e.rel = rama if not e.rel else rama + "\\" + e.rel
            sub.raiz = normalizar_raiz(raiz_destino)
            sub.sin_listar = [rama if not r else rama + "\\" + r for r in sub.sin_listar]
    nombre = lambda s: actual.nombre(s) if actual.cuentas.get(sid_canonico(s), ("", 0))[0] else copia.nombre(s)  # noqa: E731
    de_copia = {e.rel.lower(): e for e in copia.elementos if _en_rama(e.rel, rama)}
    de_destino = actual.por_rel()
    salida = []
    for clave in sorted(set(de_copia) | set(de_destino), key=lambda k: (k.count("\\") if k else -1, k)):
        c, d = de_copia.get(clave), de_destino.get(clave)
        if c and not d:
            salida.append(Diferencia(c.rel, c.carpeta, NO_EXISTE))
            continue
        if d and not c:
            if d.descriptor.tiene_propios and not copia.parcial:
                salida.append(Diferencia(d.rel, d.carpeta, SOLO_DESTINO,
                                         ["No estaba en la copia: no se toca"]))
            continue
        if c.error or not c.sddl:
            salida.append(Diferencia(c.rel, c.carpeta, ERROR, [f"En la copia: {c.error or 'sin permisos guardados'}"]))
            continue
        if d.error:
            salida.append(Diferencia(d.rel, d.carpeta, ERROR, [f"En el destino: {d.error}"]))
            continue
        objetivo = c.descriptor.traducido(mapa)
        if objetivo.clave() == d.descriptor.clave():
            continue
        salida.append(Diferencia(d.rel, d.carpeta, CAMBIA, diferencias(d.descriptor, objetivo, nombre), objetivo, True))
    return salida, actual


def escribir(ruta: str, desc: Descriptor, propietario: bool = True, permisos: bool = True) -> str:
    """Escribe propietario y/o entradas propias + herencia. Windows recalcula lo
    heredado y lo propaga hacia dentro. Devuelve «» o un aviso (no error)."""
    activar_privilegios()
    sd = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(desc.sddl(solo_propias=True), _REVISION)
    info, dueno, dacl = 0, None, None
    if propietario and desc.propietario:
        info |= win32security.OWNER_SECURITY_INFORMATION
        dueno = sd.GetSecurityDescriptorOwner()
    if permisos:
        info |= win32security.DACL_SECURITY_INFORMATION
        info |= (win32security.PROTECTED_DACL_SECURITY_INFORMATION if desc.protegida
                 else win32security.UNPROTECTED_DACL_SECURITY_INFORMATION)
        dacl = None if desc.sin_dacl else sd.GetSecurityDescriptorDacl()
    if not info:
        return ""
    try:
        win32security.SetNamedSecurityInfo(ruta_larga(ruta), win32security.SE_FILE_OBJECT, info, dueno, None, dacl, None)
        return ""
    except pywintypes.error as e:
        if e.winerror != _ACCESO_DENEGADO:
            raise
    # La carpeta no deja ni a los administradores: se escribe con el privilegio
    # de restauración, que no propaga lo heredado hacia dentro.
    acceso = win32con.WRITE_DAC | win32con.WRITE_OWNER
    h = _abrir_con_privilegio(ruta, acceso)
    try:
        win32security.SetSecurityInfo(h, win32security.SE_FILE_OBJECT, info, dueno, None, dacl, None)
    finally:
        h.Close()
    return "Escrito con el privilegio de restauración (la carpeta no daba acceso): lo de dentro no se ha recalculado"


def copia_previa(raiz: str, rels: Iterable[str], origen: str, carpeta: Optional[str] = None,
                 recursos: Iterable[str] = ()) -> str:
    """Guarda el estado ACTUAL de lo que se va a cambiar (y de esos recursos
    compartidos, por nombre). Devuelve la ruta de la copia."""
    lec = leer_arbol(raiz, solo=list(dict.fromkeys(rels)))
    lec.origen = origen
    lec.recursos = recursos_actuales(raiz, recursos) if recursos else []
    _resolver_cuentas(lec)
    destino = os.path.join(carpeta or carpeta_copias(), nombre_copia(lec, "Antes"))
    return guardar(lec, destino)


def aplicar(raiz_destino: str, diferencias_: Iterable[Diferencia], progreso=None, parar=None) -> List[Tuple[str, bool, str]]:
    """Escribe, de arriba abajo, las diferencias aplicables. [(rel, ok, mensaje)]."""
    pendientes = sorted((d for d in diferencias_ if d.aplicable and d.objetivo is not None),
                        key=lambda d: (d.rel.count("\\") if d.rel else -1, d.rel.lower()))
    resultado = []
    for n, d in enumerate(pendientes, 1):
        if parar and parar():
            break
        if progreso:
            progreso(n, d.rel or "(carpeta principal)")
        ruta = os.path.join(normalizar_raiz(raiz_destino), d.rel) if d.rel else normalizar_raiz(raiz_destino)
        try:
            resultado.append((d.rel, True, escribir(ruta, d.objetivo)))
        except pywintypes.error as e:
            resultado.append((d.rel, False, e.strerror or str(e)))
    return resultado


@dataclass
class CambioRecurso:
    nombre: str
    estado: str                # Cambia / No existe en el destino / Igual
    detalle: List[str]
    sddl: str
    ruta_local: str = ""       # para crearlo si falta
    descripcion: str = ""


_NO_EXISTE_RECURSO = 2310   # NERR_NetNameNotFound


def _sddl_recurso(servidor: Optional[str], nombre: str) -> Tuple[str, str]:
    """(SDDL de red, carpeta) de un recurso compartido; ("", "") si no existe."""
    try:
        info = win32net.NetShareGetInfo(servidor, nombre, 502)
    except pywintypes.error as e:
        if e.winerror == _NO_EXISTE_RECURSO:
            return "", ""
        raise
    sd = info.get("security_descriptor")
    sddl = (win32security.ConvertSecurityDescriptorToStringSecurityDescriptor(sd, _REVISION, win32security.DACL_SECURITY_INFORMATION)
            if sd else "D:NO_ACCESS_CONTROL")
    return sddl, info.get("path") or ""


def planificar_recursos(copia: Lectura, raiz_destino: str, equivalencias: Iterable[Equivalencia] = ()) -> Tuple[List[CambioRecurso], str]:
    """Recursos compartidos de la copia frente a los del servidor de destino (por nombre)."""
    if not copia.recursos:
        return [], ""
    mapa = mapa_de(equivalencias)
    try:
        servidor, base = ruta_local_en_servidor(raiz_destino)
    except (pywintypes.error, ValueError) as e:
        return [], f"No se pudo saber dónde está la carpeta en el servidor: {getattr(e, 'strerror', '') or e}"
    salida, avisos = [], []
    for r in copia.recursos:
        objetivo = r.descriptor.traducido(mapa) if r.sddl else Descriptor(sin_dacl=True)
        sddl = objetivo.sddl()
        ruta = os.path.join(base, r.rel) if r.rel else base
        try:
            actual, carpeta = _sddl_recurso(servidor, r.nombre)
        except pywintypes.error as e:
            avisos.append(f"{r.nombre}: {e.strerror}")
            continue
        if not carpeta and not actual:
            salida.append(CambioRecurso(r.nombre, NO_EXISTE, [f"Se puede crear apuntando a {ruta}"], sddl, ruta, r.descripcion))
            continue
        detalle = diferencias(Descriptor.desde_sddl(actual), objetivo, copia.nombre)
        if os.path.normcase(carpeta.rstrip("\\")) != os.path.normcase(ruta.rstrip("\\")):
            detalle.append(f"Ojo: en el destino apunta a {carpeta} (no se cambia)")
        if Descriptor.desde_sddl(actual).clave() != objetivo.clave():
            salida.append(CambioRecurso(r.nombre, CAMBIA, detalle, sddl, carpeta, r.descripcion))
    return salida, "\n".join(avisos)


def recursos_actuales(raiz_destino: str, nombres: Iterable[str]) -> List[Recurso]:
    """Estado actual de esos recursos (para la copia previa)."""
    servidor, _base = ruta_local_en_servidor(raiz_destino)
    salida = []
    for n in nombres:
        sddl, carpeta = _sddl_recurso(servidor, n)
        if carpeta or sddl:
            salida.append(Recurso(n, carpeta, "", sddl))
    return salida


def aplicar_recursos(raiz_destino: str, cambios: Iterable[CambioRecurso], crear: bool) -> List[Tuple[str, bool, str]]:
    servidor, _base = ruta_local_en_servidor(raiz_destino)
    resultado = []
    for c in cambios:
        try:
            if c.estado == CAMBIA:
                aplicar_recurso(servidor, c.nombre, c.sddl)
                resultado.append((c.nombre, True, "Permisos de red restaurados"))
            elif c.estado == NO_EXISTE and crear:
                if not os.path.isdir(ruta_larga(os.path.join(raiz_destino, os.path.relpath(c.ruta_local, _base))
                                                if servidor else c.ruta_local)):
                    resultado.append((c.nombre, False, f"La carpeta {c.ruta_local} no existe en el destino"))
                    continue
                crear_recurso(servidor, c.nombre, c.ruta_local, c.sddl, c.descripcion)
                resultado.append((c.nombre, True, f"Creado en {c.ruta_local}"))
        except pywintypes.error as e:
            resultado.append((c.nombre, False, e.strerror or str(e)))
    return resultado


# ── Análisis ────────────────────────────────────────────────────────────────

CRITICO, AVISO, INFO_ = "Crítico", "Aviso", "Información"


@dataclass
class Hallazgo:
    nivel: str
    tipo: str
    rel: str
    cuenta: str
    detalle: str
    que_hacer: str


def analizar(lec: Lectura) -> List[Hallazgo]:
    h: List[Hallazgo] = []
    nombre = lec.nombre
    usuarios_dominio = lambda s: s.startswith("S-1-5-21-") and s.endswith("-513")  # noqa: E731
    duenos = {e.rel.lower(): sid_canonico(e.descriptor.propietario) for e in lec.elementos if e.carpeta and e.sddl}

    def dueno_padre(e: Elemento) -> str:
        return duenos.get(e.rel.rpartition("\\")[0].lower(), "") if e.rel else ""

    for e in lec.elementos:
        donde = e.rel or "(carpeta principal)"
        if e.error:
            h.append(Hallazgo(AVISO, "No se pudieron leer los permisos", donde, "", e.error,
                              "Tomar posesión con 👤 Propietario y volver a leer"))
            continue
        d = e.descriptor
        if d.sin_dacl:
            h.append(Hallazgo(CRITICO, "Sin permisos: acceso total para todos", donde, "", "La carpeta no tiene lista de permisos",
                              "Editar los permisos y dejar solo los grupos que deben entrar"))
            continue
        if lec.huerfana(d.propietario):
            h.append(Hallazgo(AVISO, "Propietario eliminado", donde, nombre(d.propietario),
                              "El propietario es una cuenta que ya no existe", "Cambiar el propietario a Administradores"))
        elif e.carpeta and lec.tipo(d.propietario) == 1 and dueno_padre(e) != sid_canonico(d.propietario):
            # Solo donde empieza (lo de dentro con el mismo dueño no se repite)
            h.append(Hallazgo(INFO_, "Propietario: un usuario", donde, nombre(d.propietario),
                              "El propietario puede cambiar los permisos aunque no los tenga (también lo de dentro con el mismo dueño)",
                              "Cambiar el propietario a Administradores"))
        if d.protegida:
            h.append(Hallazgo(INFO_, "Herencia cortada", donde, "", "No recibe los permisos de la carpeta de arriba",
                              "Comprobar que es a propósito"))
        if (d.protegida or d.propias) and e.carpeta and not d.entradas:
            h.append(Hallazgo(AVISO, "Nadie tiene acceso", donde, "", "La lista de permisos está vacía",
                              "Editar los permisos o tomar posesión"))
        elif d.protegida and e.carpeta and not any(x.permitir and not x.solo_herencia and es_administracion(x.sid)
                                                    and x.sid not in (CREADOR, DERECHOS_PROPIETARIO) for x in d.entradas):
            h.append(Hallazgo(AVISO, "Los administradores no tienen acceso", donde, "",
                              "Ni Administradores ni SYSTEM aparecen en los permisos", "Añadir Administradores con Control total"))
        for x in d.propias:
            cuenta = nombre(x.sid)
            m = mascara_efectiva(x.mascara)
            if lec.huerfana(x.sid):
                h.append(Hallazgo(AVISO, "Permiso de una cuenta eliminada", donde, cuenta, describir_entrada(x, nombre),
                                  "Quitar la entrada"))
                continue
            if x.denegar:
                h.append(Hallazgo(AVISO, "Entrada de denegar", donde, cuenta, describir_entrada(x, nombre),
                                  "Mejor no dar el permiso que denegarlo: denegar gana a cualquier permiso"))
                continue
            if not x.permitir:
                continue
            if x.sid in (TODOS, ANONIMO) and m & _BITS_ESCRITURA:
                h.append(Hallazgo(CRITICO, "Todos pueden escribir", donde, cuenta, describir_entrada(x, nombre),
                                  "Cambiar Todos por el grupo de usuarios que debe entrar"))
            elif (x.sid in (AUTENTICADOS, USUARIOS) or usuarios_dominio(x.sid)) and m & _BITS_ESCRITURA:
                h.append(Hallazgo(AVISO, "Todos los usuarios pueden escribir", donde, cuenta, describir_entrada(x, nombre),
                                  "Limitarlo al grupo que lo necesita"))
            elif m & _BITS_CONTROL and not es_administracion(x.sid) and not x.solo_herencia:
                h.append(Hallazgo(AVISO, "Puede cambiar los permisos", donde, cuenta, describir_entrada(x, nombre),
                                  "Dar Modificar en vez de Control total"))
            if lec.tipo(x.sid) == 1:
                h.append(Hallazgo(INFO_, "Permiso directo a un usuario", donde, cuenta, describir_entrada(x, nombre),
                                  "Mejor dar los permisos a un grupo y meter al usuario en él"))
    for rel in lec.sin_listar:
        h.append(Hallazgo(AVISO, "No se pudo ver lo que hay dentro", rel or "(carpeta principal)", "",
                          "Sin permiso para listar el contenido", "Tomar posesión con 👤 Propietario y volver a leer"))
    por_rel = lec.por_rel()
    for r in lec.recursos:
        rd = r.descriptor
        abre = rd.sin_dacl or any(x.permitir and x.sid in (TODOS, AUTENTICADOS) and mascara_efectiva(x.mascara) & _BITS_ESCRITURA
                                  for x in rd.entradas)
        if not abre:
            continue
        el = por_rel.get(r.rel.lower())
        ntfs_abre = el is not None and el.sddl and any(
            x.permitir and x.sid in (TODOS, ANONIMO) and mascara_efectiva(x.mascara) & _BITS_ESCRITURA and not x.solo_herencia
            for x in el.descriptor.entradas)
        if ntfs_abre:
            h.append(Hallazgo(CRITICO, "Todos pueden escribir por la red", r.rel or "(carpeta principal)", f"\\\\{lec.equipo}\\{r.nombre}",
                              "El recurso compartido y los permisos de la carpeta dejan escribir a Todos",
                              "Quitar Todos de los permisos de la carpeta"))
        else:
            h.append(Hallazgo(INFO_, "Recurso compartido abierto a todos", r.rel or "(carpeta principal)",
                              f"\\\\{lec.equipo}\\{r.nombre}", "Por red da escritura a todos; lo limitan los permisos de la carpeta",
                              "Normal si los permisos de la carpeta están bien"))
    orden = {CRITICO: 0, AVISO: 1, INFO_: 2}
    h.sort(key=lambda x: (orden.get(x.nivel, 3), x.tipo, x.rel.lower()))
    return h


# ── Acceso de una cuenta ────────────────────────────────────────────────────

def grupos_de(nombre: str, raiz: str) -> Tuple[str, str, Set[str], List[str]]:
    """(SID, nombre completo, SID de la cuenta y de todos sus grupos, avisos)."""
    servidor = servidor_de(raiz)
    sid, completo, tipo = resolver_cuenta(nombre, servidor)
    sids = {sid, TODOS}
    avisos = []
    if sid not in (ANONIMO,):
        sids.add(AUTENTICADOS)
    sids.add(RED if servidor else INTERACTIVO)
    dominio, _, cuenta = completo.rpartition("\\")
    if tipo == 1:
        try:
            for g in win32net.NetUserGetLocalGroups(servidor, completo, 1):   # 1 = también los indirectos
                try:
                    sids.add(resolver_cuenta(g, servidor)[0])
                except ValueError:
                    pass
        except pywintypes.error as e:
            avisos.append(f"No se pudieron leer sus grupos locales: {e.strerror}")
        es_de_dominio = dominio and dominio.upper() != (servidor[2:] if servidor else equipo_local()).upper()
        if es_de_dominio:
            globales, aviso = _grupos_dominio(completo)
            sids |= globales
            if aviso:
                avisos.append(aviso)
    elif tipo in (2, 4, 5):
        avisos.append("Es un grupo: se ve lo que tiene el grupo, no lo que tiene cada usuario por otros grupos.")
    return sid, completo, sids, avisos


def _grupos_dominio(completo: str) -> Tuple[Set[str], str]:
    """Grupos del dominio, incluidos los anidados (tokenGroups de Active Directory)."""
    try:
        import win32com.client
        dn = win32security.TranslateName(completo, win32con.NameSamCompatible, win32con.NameFullyQualifiedDN)
        objeto = win32com.client.GetObject("LDAP://" + dn.replace("/", "\\/"))
        objeto.GetInfoEx(["tokenGroups"], 0)
        sids = set()
        for crudo in objeto.Get("tokenGroups"):
            sids.add(win32security.ConvertSidToStringSid(pywintypes.SID(bytes(crudo))))
        return sids, ""
    except Exception as e:  # sin dominio a la vista, sin permisos de lectura en AD…
        try:
            dominio, _, cuenta = completo.rpartition("\\")
            dc = win32net.NetGetAnyDCName(None, dominio)
            sids = set()
            for g, _attr in win32net.NetUserGetGroups(dc, cuenta):
                try:
                    sids.add(resolver_cuenta(f"{dominio}\\{g}")[0])
                except ValueError:
                    pass
            return sids, "Grupos del dominio sin los anidados (no se pudo consultar Active Directory)."
        except Exception:
            return set(), f"No se pudieron leer los grupos del dominio: {e}"


def acceso_efectivo(desc: Descriptor, sids: Set[str], es_propietario: bool) -> Tuple[int, List[str], List[str]]:
    """(máscara que le queda, SID que lo dan, SID que lo deniegan). Orden de
    Windows: se recorre la lista y lo denegado antes de permitirse no se da."""
    if desc.sin_dacl:
        return FULL, [TODOS], []
    dado, negado, por, contra = 0, 0, [], []
    hay_derechos_propietario = any(x.sid == DERECHOS_PROPIETARIO for x in desc.entradas if not x.solo_herencia)
    for x in desc.entradas:
        if x.solo_herencia:
            continue
        aplica = x.sid in sids or (x.sid == DERECHOS_PROPIETARIO and es_propietario)
        if not aplica:
            continue
        m = mascara_efectiva(x.mascara)
        if x.denegar:
            nuevo = m & ~dado
            if nuevo:
                negado |= nuevo
                contra.append(x.sid)
        elif x.permitir:
            nuevo = m & ~negado & ~dado
            if nuevo:
                dado |= nuevo
                por.append(x.sid)
    if es_propietario and not hay_derechos_propietario:
        dado |= 0x20000 | 0x40000   # el propietario siempre puede ver y cambiar los permisos
    return dado, list(dict.fromkeys(por)), list(dict.fromkeys(contra))


@dataclass
class Acceso:
    rel: str
    carpeta: bool
    nivel: str
    por: str
    denegado: str
    red: str
    efectivo: str
    mascara: int


def acceso_de_cuenta(lec: Lectura, sid: str, sids: Set[str], solo_cambios: bool = True) -> List[Acceso]:
    """Qué puede hacer la cuenta en cada carpeta leída. solo_cambios: solo
    donde cambia respecto a la carpeta de arriba (más la principal)."""
    recursos = sorted(lec.recursos, key=lambda r: -len(r.rel))
    salida, de_carpeta = [], {}
    for e in sorted(lec.elementos, key=lambda x: (x.nivel, x.rel.lower())):
        padre = None if not e.rel else e.rel.rpartition("\\")[0].lower()
        if e.error or not e.sddl:
            salida.append(Acceso(e.rel, e.carpeta, "No se pudo leer", "", "", "", "", 0))
            continue
        d = e.descriptor
        m, por, contra = acceso_efectivo(d, sids, sid_canonico(d.propietario) == sid_canonico(sid))
        red, m_final = "", m
        rec = next((r for r in recursos if _en_rama(e.rel, r.rel)), None)
        if rec is not None:
            mr, _p, _c = acceso_efectivo(rec.descriptor, sids, False)
            red = f"{rec.nombre}: {nombre_nivel(mr)}"
            m_final = m & mascara_efectiva(mr)
        clave = (m, m_final)
        if e.carpeta:
            de_carpeta[e.rel.lower()] = clave
        if solo_cambios and padre is not None and de_carpeta.get(padre) == clave:
            continue
        salida.append(Acceso(e.rel, e.carpeta, nombre_nivel(m), ", ".join(lec.nombre(s) for s in por),
                             ", ".join(lec.nombre(s) for s in contra), red, nombre_nivel(m_final), m_final))
    return salida


# ── Editar ──────────────────────────────────────────────────────────────────

def leer_descriptor(ruta: str) -> Descriptor:
    return Descriptor.desde_sddl(leer_sddl(ruta))


def ordenar_canonico(entradas: List[Entrada]) -> List[Entrada]:
    """Orden de Windows para las propias: primero denegar, luego permitir."""
    return [e for e in entradas if e.denegar] + [e for e in entradas if not e.denegar]


def con_entrada_nueva(desc: Descriptor, sid: str, permitir: bool, m: int, banderas: str) -> Descriptor:
    nuevo = Descriptor.desde_sddl(desc.sddl())
    propias = nuevo.propias + [Entrada("A" if permitir else "D", banderas, f"0x{m:x}", sid_canonico(sid))]
    nuevo.entradas = ordenar_canonico(propias) + nuevo.heredadas
    return nuevo


def sin_entradas(desc: Descriptor, claves: Set[Tuple]) -> Descriptor:
    nuevo = Descriptor.desde_sddl(desc.sddl())
    nuevo.entradas = [e for e in nuevo.propias if e.clave() not in claves] + nuevo.heredadas
    return nuevo


def con_herencia(desc: Descriptor, heredar: bool, copiar_heredados: bool = True) -> Descriptor:
    nuevo = Descriptor.desde_sddl(desc.sddl())
    if heredar:
        nuevo.protegida = False
        nuevo.banderas_dacl = (nuevo.banderas_dacl or "").replace("P", "")
    else:
        nuevo.protegida = True
        copiadas = [e.sin_herencia() for e in nuevo.heredadas] if copiar_heredados else []
        claves = {e.clave() for e in nuevo.propias}
        nuevo.entradas = ordenar_canonico(nuevo.propias + [e for e in copiadas if e.clave() not in claves])
    return nuevo


def con_propietario(desc: Descriptor, sid: str) -> Descriptor:
    nuevo = Descriptor.desde_sddl(desc.sddl())
    nuevo.propietario = sid_canonico(sid)
    return nuevo


def cambiar_propietario(raiz: str, rels: Iterable[str], sid: str, progreso=None, parar=None) -> List[Tuple[str, bool, str]]:
    resultado = []
    for n, rel in enumerate(rels, 1):
        if parar and parar():
            break
        if progreso:
            progreso(n, rel or "(carpeta principal)")
        ruta = os.path.join(normalizar_raiz(raiz), rel) if rel else normalizar_raiz(raiz)
        try:
            d = Descriptor(propietario=sid_canonico(sid))
            resultado.append((rel, True, escribir(ruta, d, propietario=True, permisos=False)))
        except pywintypes.error as e:
            resultado.append((rel, False, e.strerror or str(e)))
    return resultado


def _icacls(argumentos: List[str], limite: int = 7200) -> Tuple[bool, str]:
    try:
        r = subprocess.run(["icacls"] + argumentos, capture_output=True, timeout=limite,
                           creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    salida = ((r.stdout or b"") + (r.stderr or b"")).decode("cp850", errors="replace").strip()
    return r.returncode == 0, salida


def cambiar_propietario_rama(ruta: str, sid: str) -> Tuple[bool, str]:
    """Propietario de la carpeta y de TODO lo que tiene dentro (también los
    archivos que no se leyeron). Con *SID: vale para cuentas locales del servidor."""
    activar_privilegios()
    return _icacls([normalizar_raiz(ruta), "/setowner", "*" + sid_canonico(sid), "/t", "/c", "/q"])


def restablecer_herencia_dentro(ruta: str) -> Tuple[bool, str]:
    """Todo lo que hay DENTRO de la carpeta pierde sus permisos propios y pasa
    a heredar de ella (como «Reemplazar todas las entradas de permisos de
    objetos secundarios» del Explorador). La carpeta en sí no cambia."""
    return _icacls([os.path.join(normalizar_raiz(ruta), "*"), "/reset", "/t", "/c", "/q"])


def quitar_cuenta_de_rama(lec: Lectura, rel_base: str, sid: str) -> List[Tuple[str, Descriptor]]:
    """[(rel, descriptor nuevo)] de cada elemento de la rama con entradas propias de la cuenta."""
    s = sid_canonico(sid)
    salida = []
    for e in sorted(lec.elementos, key=lambda x: (x.nivel, x.rel.lower())):
        if e.error or not e.sddl or not _en_rama(e.rel, rel_base):
            continue
        claves = {x.clave() for x in e.descriptor.propias if x.sid == s}
        if claves:
            salida.append((e.rel, sin_entradas(e.descriptor, claves)))
    return salida
