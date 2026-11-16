# -*- coding: utf-8 -*-
"""Motor de auditoría de seguridad de las contraseñas guardadas en el
navegador (sin Qt), para la sección 🌐 Navegadores.

Es AUDITORÍA, no un gestor de contraseñas: solo LEE las que Chrome/Edge ya
tienen guardadas (vía msys.browser_data.leer_passwords, que las descifra
con la clave del usuario actual) y las evalúa. No escribe, no cambia ni
mueve ninguna credencial.

Comprueba tres cosas:
1. Fortaleza (local, sin red): longitud, variedad de caracteres y patrones
   típicos (secuencias, años, «123456», «contraseña»...).
2. Reutilización (local): la misma contraseña en varios sitios (se comparan
   por hash en memoria, no se guarda ningún texto en claro).
3. Exposición en filtraciones conocidas, con *Have I Been Pwned – Pwned
   Passwords* por k-ANONIMATO: se calcula el SHA-1 en este equipo y SOLO se
   envían los 5 primeros caracteres del hash; la API devuelve los sufijos
   de ese prefijo y aquí se compara el resto. La contraseña (ni su hash
   completo) sale nunca del equipo. Requiere internet.

«Validez en webs» (que la contraseña funcione de verdad entrando en cada
sitio) NO se hace a propósito: sería prueba de credenciales contra
servicios de terceros (puede bloquear cuentas y viola sus términos). Lo que
sí se ofrece es comprobar que el SITIO sigue respondiendo (reutilizando el
comprobador de enlaces de favoritos.py)."""

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from msys.browser_data import PerfilNavegador, enumerar_perfiles, leer_passwords

# Niveles de fortaleza
MUY_DEBIL, DEBIL, ACEPTABLE, FUERTE = "Muy débil", "Débil", "Aceptable", "Fuerte"

# Exposición: nº de veces vista en filtraciones; -1 = no comprobado, 0 = limpia
NO_COMPROBADA = -1

_COMUNES = {
    "123456", "123456789", "12345678", "12345", "1234567", "password", "contraseña",
    "qwerty", "abc123", "111111", "123123", "000000", "iloveyou", "admin", "welcome",
    "monkey", "dragon", "1234567890", "qwertyuiop", "sunshine", "princess", "football",
}
_SECUENCIAS = ("0123456789", "abcdefghijklmnopqrstuvwxyz", "qwertyuiop", "asdfghjkl")


@dataclass
class FilaPassword:
    navegador: str
    perfil: str
    sitio: str
    usuario: str
    password: Optional[str]      # None = no se pudo descifrar
    fortaleza: str = ""
    puntos: int = 0
    reutilizada_en: int = 0      # nº de sitios distintos que comparten esta contraseña
    expuesta: int = NO_COMPROBADA
    motivos: str = ""


def perfiles_passwords() -> List[PerfilNavegador]:
    """Solo Chrome/Edge: Firefox no está soportado para descifrar."""
    return [p for p in enumerar_perfiles() if p.navegador in ("Chrome", "Edge")]


# ───────────────────────── Fortaleza (local) ─────────────────────────

def evaluar_fortaleza(pwd: str) -> tuple:
    """(nivel, puntos 0-100, motivos) de una contraseña. Todo local."""
    if pwd is None:
        return "", 0, ""
    if pwd == "":
        return MUY_DEBIL, 0, "Vacía"
    motivos: List[str] = []
    puntos = 0
    n = len(pwd)
    puntos += min(n * 4, 40)
    if n < 8:
        motivos.append("menos de 8 caracteres")
    clases = sum(bool(re.search(p, pwd)) for p in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    puntos += clases * 12
    if clases <= 2:
        motivos.append("poca variedad de caracteres")
    bajo = pwd.lower()
    if bajo in _COMUNES:
        puntos = min(puntos, 5)
        motivos.append("está entre las más usadas")
    if re.fullmatch(r"\d+", pwd):
        puntos -= 15
        motivos.append("solo números")
    if re.search(r"(19|20)\d{2}", pwd):
        puntos -= 12
        motivos.append("contiene un año")
    if re.fullmatch(r"[A-Za-zÁÉÍÓÚáéíóúñÑ]+\d{0,4}[!.*]?", pwd):
        puntos -= 10
        motivos.append("palabra + números (patrón común)")
    for seq in _SECUENCIAS:
        if any(bajo[i:i + 4] in seq or bajo[i:i + 4] in seq[::-1] for i in range(len(bajo) - 3)):
            puntos -= 10
            motivos.append("contiene una secuencia")
            break
    if re.search(r"(.)\1\1", pwd):
        puntos -= 8
        motivos.append("caracteres repetidos")
    puntos = max(0, min(100, puntos))
    if puntos < 30:
        nivel = MUY_DEBIL
    elif puntos < 50:
        nivel = DEBIL
    elif puntos < 75:
        nivel = ACEPTABLE
    else:
        nivel = FUERTE
    return nivel, puntos, ", ".join(dict.fromkeys(motivos))


# ───────────────────────── Carga y análisis ──────────────────────────

def analizar(perfil: PerfilNavegador) -> List[FilaPassword]:
    """Lee las contraseñas del perfil y las evalúa en local (fortaleza +
    reutilización). La exposición se deja para comprobar_expuestas()."""
    filas: List[FilaPassword] = []
    for r in leer_passwords(perfil):
        nivel, puntos, motivos = evaluar_fortaleza(r.password)
        filas.append(FilaPassword(
            navegador=r.navegador, perfil=r.perfil, sitio=r.sitio, usuario=r.usuario,
            password=r.password, fortaleza=nivel, puntos=puntos, motivos=motivos))
    marcar_reutilizadas(filas)
    return filas


def marcar_reutilizadas(filas: List[FilaPassword]):
    """Cuenta en cuántos sitios DISTINTOS se repite cada contraseña (por
    hash en memoria; nunca se guarda el texto)."""
    sitios_por_hash: Dict[str, set] = {}
    for f in filas:
        if f.password:
            h = hashlib.sha256(f.password.encode("utf-8")).hexdigest()
            sitios_por_hash.setdefault(h, set()).add(f.sitio)
    for f in filas:
        if f.password:
            h = hashlib.sha256(f.password.encode("utf-8")).hexdigest()
            compartido = len(sitios_por_hash.get(h, set()))
            f.reutilizada_en = compartido if compartido > 1 else 0


# ─────────────── Exposición en filtraciones (HIBP, k-anonimato) ───────

def _pwned_por_prefijo(prefijo: str) -> Dict[str, int]:
    """Sufijos (35 car.) y su nº de apariciones para un prefijo de 5."""
    import requests
    r = requests.get(f"https://api.pwnedpasswords.com/range/{prefijo}", timeout=12,
                     headers={"User-Agent": "MASys", "Add-Padding": "true"})
    r.raise_for_status()
    salida: Dict[str, int] = {}
    for linea in r.text.splitlines():
        if ":" in linea:
            suf, cuenta = linea.split(":", 1)
            try:
                salida[suf.strip().upper()] = int(cuenta.strip())
            except ValueError:
                continue
    return salida


def comprobar_expuesta(pwd: str, cache: Optional[Dict[str, Dict[str, int]]] = None) -> int:
    """Nº de filtraciones conocidas en que aparece esta contraseña (0 =
    ninguna). Solo se envían 5 caracteres del hash."""
    if not pwd:
        return 0
    sha1 = hashlib.sha1(pwd.encode("utf-8")).hexdigest().upper()
    prefijo, sufijo = sha1[:5], sha1[5:]
    if cache is not None and prefijo in cache:
        tabla = cache[prefijo]
    else:
        tabla = _pwned_por_prefijo(prefijo)
        if cache is not None:
            cache[prefijo] = tabla
    return tabla.get(sufijo, 0)


def comprobar_expuestas(filas: List[FilaPassword], progreso: Optional[Callable[[int, int], None]] = None,
                        debe_parar: Optional[Callable[[], bool]] = None, hilos: int = 8) -> List[FilaPassword]:
    """Rellena 'expuesta' de cada fila por k-anonimato, cacheando por
    prefijo para no repetir peticiones. Devuelve la misma lista."""
    cache: Dict[str, Dict[str, int]] = {}
    total = len(filas)
    hechos = [0]

    def _uno(f: FilaPassword):
        if debe_parar and debe_parar():
            return
        try:
            f.expuesta = comprobar_expuesta(f.password, cache) if f.password else 0
        except Exception:
            f.expuesta = NO_COMPROBADA
        hechos[0] += 1
        if progreso:
            progreso(hechos[0], total)

    with ThreadPoolExecutor(max_workers=max(1, hilos)) as ex:
        list(ex.map(_uno, filas))
    return filas


def resumen(filas: List[FilaPassword]) -> dict:
    return {
        "total": len(filas),
        "muy_debiles": sum(1 for f in filas if f.fortaleza == MUY_DEBIL),
        "debiles": sum(1 for f in filas if f.fortaleza in (MUY_DEBIL, DEBIL)),
        "reutilizadas": sum(1 for f in filas if f.reutilizada_en),
        "expuestas": sum(1 for f in filas if f.expuesta > 0),
        "sin_descifrar": sum(1 for f in filas if f.password is None),
    }
