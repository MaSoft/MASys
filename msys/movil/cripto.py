# -*- coding: utf-8 -*-
"""Cifrado del enlace MASys ↔ MASysMov (igual que MAgenda/MASatAutonomo, con
otro prefijo de datos asociados para que una clave de un enlace no valga en otro).

- Mensajes: AES-256-GCM, nonce de 12 bytes + cifrado + etiqueta de 128 bits.
  Datos asociados: "masys-v1|<deviceId>|<ruta>" (petición) y "...|resp" (respuesta).
  El contenido va comprimido con gzip ANTES de cifrar.
- Archivo exportado (.msysmov): b"MSYM1" + sal(16) + iteraciones(4, big-endian) +
  mensaje cifrado con la clave PBKDF2-HMAC-SHA256(contraseña, sal, iteraciones)
  y datos asociados "masys-v1|archivo".

El lado Android está en MASYSMOV/ANDROID/.../link/LinkCrypto.kt: cualquier
cambio aquí hay que hacerlo allí (hay un vector de prueba común en los tests)."""

import gzip
import hashlib
import json
import os
import struct

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE = 12
MAGIA_ARCHIVO = b"MSYM1"
ITERACIONES = 210_000
EXTENSION = ".msysmov"


class ErrorCifrado(Exception):
    """Clave o contraseña incorrecta, o datos alterados."""


def aad(dispositivo: str, ruta: str) -> bytes:
    return f"masys-v1|{dispositivo}|{ruta}".encode("utf-8")


def sellar(clave: bytes, datos: bytes, datos_asociados: bytes) -> bytes:
    nonce = os.urandom(NONCE)
    return nonce + AESGCM(clave).encrypt(nonce, datos, datos_asociados)


def abrir(clave: bytes, blob: bytes, datos_asociados: bytes) -> bytes:
    if len(blob) < NONCE + 16:
        raise ErrorCifrado("Mensaje demasiado corto")
    try:
        return AESGCM(clave).decrypt(blob[:NONCE], blob[NONCE:], datos_asociados)
    except InvalidTag as e:
        raise ErrorCifrado("Clave incorrecta o datos alterados") from e


def json_sellado(clave: bytes, obj, datos_asociados: bytes) -> bytes:
    return sellar(clave, gzip.compress(json.dumps(obj, ensure_ascii=False).encode("utf-8")), datos_asociados)


def json_abierto(clave: bytes, blob: bytes, datos_asociados: bytes, limite: int = 200 * 1024 * 1024):
    datos = abrir(clave, blob, datos_asociados)
    try:
        plano = gzip.decompress(datos)
    except OSError as e:
        raise ErrorCifrado("Contenido no comprimido como se esperaba") from e
    if len(plano) > limite:
        raise ErrorCifrado("Contenido demasiado grande")
    return json.loads(plano.decode("utf-8"))


def clave_de_contrasena(contrasena: str, sal: bytes, iteraciones: int = ITERACIONES) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", contrasena.encode("utf-8"), sal, iteraciones, dklen=32)


def leer_archivo(ruta: str, contrasena: str):
    """Abre un informe exportado desde MASysMov. ErrorCifrado si la contraseña no vale."""
    with open(ruta, "rb") as f:
        datos = f.read()
    if datos[:5] != MAGIA_ARCHIVO or len(datos) < 5 + 16 + 4 + NONCE + 16:
        raise ValueError("No es un informe exportado desde MASysMov")
    sal = datos[5:21]
    (iteraciones,) = struct.unpack(">i", datos[21:25])
    if not 10_000 <= iteraciones <= 5_000_000:
        raise ValueError("Archivo dañado (iteraciones fuera de rango)")
    return json_abierto(clave_de_contrasena(contrasena, sal, iteraciones), datos[25:], b"masys-v1|archivo")


def escribir_archivo(ruta: str, obj, contrasena: str, iteraciones: int = ITERACIONES):
    """Lo mismo que hace el móvil (para pruebas y para reexportar)."""
    sal = os.urandom(16)
    blob = json_sellado(clave_de_contrasena(contrasena, sal, iteraciones), obj, b"masys-v1|archivo")
    with open(ruta, "wb") as f:
        f.write(MAGIA_ARCHIVO + sal + struct.pack(">i", iteraciones) + blob)


# ── Protección local de lo guardado en el PC (DPAPI del usuario de Windows) ──
def proteger(datos: bytes) -> bytes:
    import win32crypt
    return win32crypt.CryptProtectData(datos, "MASys movil", None, None, None, 0)


def desproteger(datos: bytes) -> bytes:
    import win32crypt
    return win32crypt.CryptUnprotectData(datos, None, None, None, 0)[1]
