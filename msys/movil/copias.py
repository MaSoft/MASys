# -*- coding: utf-8 -*-
"""Copias de apps que manda MASysMov (1.2.3+): se reciben por trozos cifrados y se guardan en la carpeta elegida
(por defecto COPIAS_MOVIL junto a la app), una subcarpeta por móvil.

Formatos:
- MASysMov 1.2.4+: cada copia es una CARPETA con los .apk instalables tal cual («WhatsApp_2.24.apk» y, si la app va
  en piezas, «WhatsApp_2.24_split_….apk»), «masysmov_copia.json» y los datos .tar. Se manda archivo a archivo con
  nombre «Carpeta/archivo» y en el PC queda igual: <móvil>/<Carpeta>/<archivo>. Cada archivo lleva «sha» (SHA-256
  de su contenido) e «id» = SHA-256 de «nombre|sha» (id_parte): el mismo .apk en dos copias son dos archivos.
- MASysMov 1.2.3: un .zip por copia, id = SHA-256 del .zip (sin «sha»). Se sigue aceptando igual.

Protocolo (lado móvil: MASYSMOV/.../link/MasysLink.kt):
- /api/copias/lista (JSON cifrado como el resto): el móvil manda todas sus copias {id = SHA-256, nombre, tamano,
  pkg, app, version, creada, contenido}; el PC responde {"faltan": [{id, desde}]} con lo que no tiene completo y
  el byte desde el que seguir (envíos cortados).
- /api/copias/trozo: cuerpo = AES-GCM(trozo) con datos asociados «masys-v1|disp|/api/copias/trozo|id|desde»
  (un trozo no vale para otra copia ni otra posición). Cabeceras X-MASys-Copia y X-MASys-Desde.
  Si «desde» no es lo que ya hay → 409 (el móvil vuelve a preguntar). Al completar el tamaño se comprueba la
  SHA-256: si no coincide, se borra y se pide entera otra vez.

Nunca se ejecuta nada de lo recibido: solo se escriben archivos con nombre validado dentro de la carpeta."""

import hashlib
import os
import re
import threading
from typing import List, Optional

from msys import config as msys_config

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_NOMBRE = re.compile(r"^[A-Za-z0-9._-]{1,120}\.(zip|apk|apks|msysdatos)$")  # sin datos: .apk (1 pieza, 1.2.6) o .apks (varias, 1.2.7); 5.3.0: copia de Datos .msysdatos (MASysMov 1.3.2)
_CARPETA = re.compile(r"^[A-Za-z0-9._-]{1,120}$")
_ARCHIVO = re.compile(r"^[A-Za-z0-9._-]{1,160}\.(apk|json|tar)$")
CARPETA_POR_DEFECTO = "COPIAS_MOVIL"
MAX_COPIA = 64 * 1024 ** 3  # 64 GB: más es un error del móvil


class ConflictoTrozo(Exception):
    """El trozo no empieza donde termina lo recibido (respuesta 409)."""


def id_valido(id_: str) -> bool:
    return bool(id_) and bool(_HEX64.match(id_))


def nombre_valido(nombre: str) -> bool:
    """«Copia.zip» (1.2.3), «App_1.0.apk» suelto (1.2.6, sin datos) o «Carpeta/archivo.apk|.json|.tar» (1.2.4). Nunca rutas con «..», «\\» ni más niveles."""
    if not nombre or ".." in nombre or "\\" in nombre:
        return False
    partes = nombre.split("/")
    if len(partes) == 1:
        return bool(_NOMBRE.match(nombre))
    return len(partes) == 2 and bool(_CARPETA.match(partes[0])) and not partes[0].startswith(".") and bool(_ARCHIVO.match(partes[1]))


def id_parte(nombre: str, sha: str) -> str:
    """Id de un archivo de una copia en carpeta (CONTRATO con MasysLink.partId de MASysMov)."""
    return hashlib.sha256(f"{nombre}|{sha}".encode("utf-8")).hexdigest()


def carpeta_copia(nombre: str) -> str:
    """La copia a la que pertenece un archivo: «Carpeta» de «Carpeta/archivo.apk»; el .zip es su propia copia."""
    return nombre.split("/", 1)[0] if "/" in nombre else nombre


def carpeta_movil(nombre_movil: str) -> str:
    """Nombre de subcarpeta seguro para un móvil («Redmi Note 13» → «Redmi_Note_13»)."""
    limpio = re.sub(r"[^A-Za-z0-9._-]+", "_", nombre_movil or "").strip("._") or "Movil"
    return limpio[:60]


class Copias:
    def __init__(self, almacen):
        self.almacen = almacen
        self._lock = threading.Lock()

    # ── carpeta ──────────────────────────────────────────────
    def carpeta(self) -> str:
        return self.almacen.ajuste("copias_carpeta") or os.path.join(msys_config.app_base_dir(), CARPETA_POR_DEFECTO)

    def fijar_carpeta(self, ruta: str):
        self.almacen.fijar_ajuste("copias_carpeta", ruta)

    def carpeta_de(self, dispositivo: str) -> str:
        nombre = next((d["nombre"] for d in self.almacen.dispositivos() if d["id"] == dispositivo), dispositivo)
        return os.path.join(self.carpeta(), carpeta_movil(nombre))

    def _rutas(self, dispositivo: str, nombre: str):
        """(carpeta donde va, ruta final, ruta mientras llega). «Carpeta/archivo» va dentro de su subcarpeta."""
        final = os.path.join(self.carpeta_de(dispositivo), *nombre.split("/"))
        return os.path.dirname(final), final, final + ".part"

    # ── lista ────────────────────────────────────────────────
    def lista(self, dispositivo: str, datos: dict) -> dict:
        copias = datos.get("copias") if isinstance(datos, dict) else None
        if not isinstance(copias, list):
            raise ValueError("Lista de copias no válida")
        faltan = []
        for c in copias[:5000]:
            if not isinstance(c, dict):
                continue
            id_, nombre = str(c.get("id", "")), str(c.get("nombre", ""))
            try:
                tamano = int(c.get("tamano", -1))
            except (TypeError, ValueError):
                continue
            if not id_valido(id_) or not nombre_valido(nombre) or not 0 < tamano <= MAX_COPIA:
                continue
            # 1.2.4: «sha» = contenido e id = id_parte(nombre, sha). Sin «sha» (1.2.3) el id ES la huella del .zip
            sha = str(c.get("sha", "") or id_)
            if not id_valido(sha) or (sha != id_ and id_parte(nombre, sha) != id_):
                continue
            if "/" in nombre and sha == id_:
                continue  # un archivo de carpeta siempre lleva su id_parte
            self.almacen.guardar_copia(dispositivo, id_, nombre, tamano, str(c.get("pkg", ""))[:150], str(c.get("app", ""))[:120],
                                       str(c.get("version", ""))[:40], int(c.get("creada") or 0), str(c.get("contenido", ""))[:60], sha)
            fila = self.almacen.copia(dispositivo, id_)
            _b, final, parte = self._rutas(dispositivo, nombre)
            ruta_ok = fila and fila["completa"] and fila["ruta"] and os.path.isfile(fila["ruta"])
            if ruta_ok:
                continue
            if fila and fila["completa"]:
                self.almacen.marcar_copia(dispositivo, id_, completa=False, ruta="")  # la borraron en el PC: otra vez
            desde = os.path.getsize(parte) if os.path.isfile(parte) else 0
            if desde > tamano:
                os.remove(parte)
                desde = 0
            faltan.append({"id": id_, "desde": desde})
        return {"ok": True, "faltan": faltan}

    # ── trozos ───────────────────────────────────────────────
    def trozo(self, dispositivo: str, id_: str, desde: int, datos: bytes) -> dict:
        if not id_valido(id_):
            raise ValueError("Copia no válida")
        fila = self.almacen.copia(dispositivo, id_)
        if not fila:
            raise ValueError("Copia no anunciada en la lista")
        if fila["completa"] and fila["ruta"] and os.path.isfile(fila["ruta"]):
            return {"ok": True, "completa": True, "recibido": fila["tamano"]}
        base, final, parte = self._rutas(dispositivo, fila["nombre"])
        with self._lock:
            os.makedirs(base, exist_ok=True)
            actual = os.path.getsize(parte) if os.path.isfile(parte) else 0
            if desde != actual:
                raise ConflictoTrozo(f"Se esperaba el byte {actual} y llegó {desde}")
            if actual + len(datos) > fila["tamano"]:
                if os.path.isfile(parte):
                    os.remove(parte)
                return {"ok": False, "error": "La copia es más grande de lo anunciado"}
            with open(parte, "ab") as f:
                f.write(datos)
            recibido = actual + len(datos)
            self.almacen.marcar_copia(dispositivo, id_, recibidos=recibido)
            if recibido < fila["tamano"]:
                return {"ok": True, "completa": False, "recibido": recibido}
            if sha256_archivo(parte) != (fila.get("sha") or id_):
                os.remove(parte)
                self.almacen.marcar_copia(dispositivo, id_, recibidos=0)
                return {"ok": False, "error": "La copia llegó dañada: se volverá a enviar entera"}
            destino = libre(final)
            os.replace(parte, destino)
            self.almacen.marcar_copia(dispositivo, id_, completa=True, ruta=destino)
            return {"ok": True, "completa": True, "recibido": recibido}


def sha256_archivo(ruta: str) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as f:
        for bloque in iter(lambda: f.read(1024 * 1024), b""):
            h.update(bloque)
    return h.hexdigest()


def libre(ruta: str) -> str:
    """Si ya hay un archivo con ese nombre (otra copia distinta), «nombre (2).zip»."""
    if not os.path.exists(ruta):
        return ruta
    base, ext = os.path.splitext(ruta)
    i = 2
    while os.path.exists(f"{base} ({i}){ext}"):
        i += 1
    return f"{base} ({i}){ext}"


def estado(fila: dict) -> str:
    if fila["completa"]:
        return "Recibida" if fila.get("ruta") and os.path.isfile(fila["ruta"]) else "Borrada en el PC"
    if fila["recibidos"]:
        return f"Recibiendo {fila['recibidos'] * 100 // max(1, fila['tamano'])} %"
    return "Pendiente"


def agrupar(filas: List[dict]) -> List[dict]:
    # Una fila por copia (4.2.8): los archivos de una copia en carpeta juntos; un .zip es su propia copia
    grupos = {}
    for f in filas:
        grupos.setdefault(carpeta_copia(f["nombre"]), []).append(f)
    out = []
    for nombre, fs in grupos.items():
        primero = fs[0]
        es_carpeta = "/" in primero["nombre"]
        apks = sum(1 for f in fs if f["nombre"].lower().endswith(".apk"))
        estados = [estado(f) for f in fs]
        if all(e == "Recibida" for e in estados):
            est = "Recibida"
        elif any(e == "Borrada en el PC" for e in estados):
            est = "Borrada en el PC"
        elif any(f["recibidos"] or f["completa"] for f in fs):
            total = sum(f["tamano"] for f in fs)
            llegado = sum(f["tamano"] if f["completa"] else f["recibidos"] for f in fs)
            est = f"Recibiendo {llegado * 100 // max(1, total)} %"
        else:
            est = "Pendiente"
        if es_carpeta:
            r = next((f["ruta"] for f in fs if f.get("ruta")), "")
            ruta = os.path.dirname(r) if r else ""
        else:
            ruta = primero.get("ruta") or ""
        out.append(dict(nombre=nombre, app=primero["app"] or primero["pkg"], version=primero["version"], creada=primero["creada"],
                        tamano=sum(f["tamano"] for f in fs), contenido=primero["contenido"], estado=est, ruta=ruta, archivos=len(fs),
                        instalable=(f"{apks} .apk" if es_carpeta else "1 .apk" if nombre.lower().endswith(".apk") else "Varias piezas (.apks)" if nombre.lower().endswith(".apks") else "No (copia de Datos)" if nombre.lower().endswith(".msysdatos") else "Formato antiguo (.zip)")))
    return sorted(out, key=lambda x: -(x["creada"] or 0))


def resumen(filas: List[dict]) -> Optional[str]:
    if not filas:
        return None
    ok = sum(1 for f in filas if f["completa"])
    return f"{ok} de {len(filas)} copias en el PC"
