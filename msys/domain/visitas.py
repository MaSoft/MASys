# -*- coding: utf-8 -*-
"""«Visitas»: foto del equipo cada vez que se termina «🔄 Sincronizar Todo» y
comparación automática con la visita anterior del MISMO equipo (programas
nuevos, quitados o actualizados, servicios, inicio, tareas, usuarios,
controladores, reglas de firewall, actualizaciones...).

Se guarda en INFORMES\\Visitas\\<equipo>\\visita_AAAAMMDD_HHMMSS.json: si MASys
va en un USB y se usa en varios equipos, cada uno tiene su historial.
No se guardan contraseñas ni datos de los usuarios: solo nombres, versiones y
estados. Se conservan las 60 últimas visitas de cada equipo.

Sustituye para este uso a «📸 Snapshots» (que sigue existiendo para comparar
dos archivos a mano): aquí lo volátil (procesos, conexiones, estado arrancado
o parado de cada servicio) no cuenta, para que la diferencia sea lo que de
verdad cambió entre una visita y otra."""

import json
import os
import socket
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

VERSION = 2
MAXIMO = 60

ANADIDO = "🟢 Nuevo"
QUITADO = "🔴 Ya no está"
CAMBIADO = "🟡 Cambiado"


def _t(v) -> str:
    return "" if v is None else str(v).strip()


# clave, etiqueta, atributo del modelo, id(fila), valor(fila). El id identifica el elemento
# entre visitas; si el valor cambia, es «Cambiado» (versión nueva, otro estado...).
MODULOS: List[Tuple[str, str, str, Callable, Callable]] = [
    ("software", "Programas", "software_model", lambda r: _t(r.nombre), lambda r: _t(r.version)),
    ("servicios", "Servicios", "services_model", lambda r: _t(r.name), lambda r: f"Inicio: {_t(r.startup_type)}"),
    ("inicio", "Programas de inicio", "startup_model", lambda r: f"{_t(r.nombre)} ({_t(r.ubicacion)})",
     lambda r: f"{_t(r.estado)} · {_t(r.comando)}"),
    ("tareas", "Tareas programadas", "tasks_model", lambda r: f"{_t(r.ruta)}{_t(r.nombre)}", lambda r: f"{_t(r.estado)} · {_t(r.accion)}"),
    ("usuarios", "Usuarios", "users_model", lambda r: _t(r.nombre), lambda r: f"{_t(r.estado)} · {_t(r.grupos)}"),
    ("controladores", "Controladores", "drivers_model", lambda r: _t(r.dispositivo), lambda r: _t(r.version)),
    ("firewall", "Reglas de firewall", "firewall_model", lambda r: _t(getattr(r, "nombre_interno", "") or r.nombre),
     lambda r: f"{_t(r.accion)} · {_t(r.direccion)} · {_t(r.puerto)} · {'activa' if _t(r.habilitada).lower() in ('sí', 'si', 'true', 'yes') else 'inactiva'}"),
    ("parches", "Actualizaciones instaladas", "installed_updates_model", lambda r: _t(r.kb), lambda r: ""),
    ("pendientes", "Actualizaciones pendientes", "pending_updates_model", lambda r: _t(r.titulo), lambda r: ""),
    ("certificados", "Certificados", "certs_model", lambda r: _t(r.huella_digital), lambda r: _t(r.asunto)),
    ("impresoras", "Impresoras", "printers_model", lambda r: _t(r.nombre), lambda r: _t(r.puerto)),
    ("variables", "Variables de entorno", "env_vars_model", lambda r: f"{_t(r.name)} ({_t(r.scope)})", lambda r: _t(r.value)),
    ("usb", "Dispositivos USB", "usb_model", lambda r: _t(getattr(r, "instance_id", "") or r.nombre), lambda r: _t(r.nombre)),
]
ETIQUETAS = {m[0]: m[1] for m in MODULOS}


@dataclass
class Cambio:
    modulo: str
    tipo: str
    elemento: str
    antes: str
    despues: str


def recoger(host) -> dict:
    """Foto de lo que hay cargado ahora en los modelos de la ventana."""
    datos: Dict[str, Dict[str, str]] = {}
    for clave, _etq, atributo, fid, fval in MODULOS:
        modelo = getattr(host, atributo, None)
        if modelo is None or not hasattr(modelo, "rows"):
            continue
        try:
            filas = modelo.rows()
        except Exception:
            continue
        if not filas:
            continue
        d = {}
        for r in filas:
            try:
                i = fid(r)
                if i:
                    d[i] = fval(r)
            except Exception:
                continue
        if d:
            datos[clave] = d
    return {"version": VERSION, "equipo": socket.gethostname(), "ts": datetime.now().isoformat(timespec="seconds"), "data": datos}


def carpeta(base: str, equipo: Optional[str] = None) -> str:
    nombre = "".join(c for c in (equipo or socket.gethostname()) if c.isalnum() or c in "-_.") or "equipo"
    return os.path.join(base, "Visitas", nombre)


def listar(base: str, equipo: Optional[str] = None) -> List[str]:
    """Rutas de las visitas guardadas, de la más antigua a la más reciente."""
    c = carpeta(base, equipo)
    if not os.path.isdir(c):
        return []
    return [os.path.join(c, f) for f in sorted(os.listdir(c)) if f.startswith("visita_") and f.endswith(".json")]


def cargar(ruta: str) -> Optional[dict]:
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            v = json.load(f)
        return v if isinstance(v, dict) and isinstance(v.get("data"), dict) else None
    except (OSError, ValueError):
        return None


def guardar(base: str, foto: dict) -> Tuple[Optional[str], Optional[dict]]:
    """Guarda la foto. Devuelve (ruta, visita anterior). Si no cambió nada desde la
    anterior no se guarda otra igual (ruta None) y la anterior sigue siendo la de referencia."""
    rutas = listar(base, foto.get("equipo"))
    anterior = None
    for r in reversed(rutas):
        anterior = cargar(r)
        if anterior is not None:
            break
    if anterior is not None and anterior.get("data") == foto.get("data"):
        return None, anterior
    c = carpeta(base, foto.get("equipo"))
    os.makedirs(c, exist_ok=True)
    ts = foto.get("ts", datetime.now().isoformat(timespec="seconds")).replace("-", "").replace(":", "").replace("T", "_")
    ruta = os.path.join(c, f"visita_{ts}.json")
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(foto, f, ensure_ascii=False, indent=1)
    for viejo in listar(base, foto.get("equipo"))[:-MAXIMO]:
        try:
            os.remove(viejo)
        except OSError:
            pass
    return ruta, anterior


def _como_dict(v) -> Dict[str, str]:
    """Las fotos de «📸 Snapshots» (versión 1) son listas «id|valor»: también se pueden comparar."""
    if isinstance(v, dict):
        return {str(k): _t(x) for k, x in v.items()}
    if isinstance(v, list):
        res = {}
        for s in v:
            partes = str(s).split("|", 1)
            res[partes[0]] = partes[1] if len(partes) > 1 else ""
        return res
    return {}


def comparar(antes: dict, despues: dict) -> List[Cambio]:
    """Diferencias de la visita «antes» a la «después». Solo se comparan los módulos
    que tienen datos en las dos (si una sección no se cargó, no se dan sus elementos por quitados)."""
    a_datos, d_datos = antes.get("data", {}), despues.get("data", {})
    res: List[Cambio] = []
    vistos = set()
    for clave in [m[0] for m in MODULOS] + sorted(set(a_datos) | set(d_datos)):
        if clave in vistos or clave not in a_datos or clave not in d_datos:
            continue
        vistos.add(clave)
        etq = ETIQUETAS.get(clave, clave)
        a, d = _como_dict(a_datos[clave]), _como_dict(d_datos[clave])
        for i in sorted(set(d) - set(a), key=str.lower):
            res.append(Cambio(etq, ANADIDO, i, "", d[i]))
        for i in sorted(set(a) - set(d), key=str.lower):
            res.append(Cambio(etq, QUITADO, i, a[i], ""))
        for i in sorted(set(a) & set(d), key=str.lower):
            if a[i] != d[i]:
                res.append(Cambio(etq, CAMBIADO, i, a[i], d[i]))
    return res


def resumen(cambios: List[Cambio]) -> str:
    """«Programas: +3 −1 ~2 · Servicios: +1» (orden de MODULOS)."""
    partes = []
    for etq in [m[1] for m in MODULOS]:
        l = [c for c in cambios if c.modulo == etq]
        if not l:
            continue
        n = {t: sum(1 for c in l if c.tipo == t) for t in (ANADIDO, QUITADO, CAMBIADO)}
        trozos = ([f"+{n[ANADIDO]}"] if n[ANADIDO] else []) + ([f"−{n[QUITADO]}"] if n[QUITADO] else []) + \
                 ([f"{n[CAMBIADO]} cambiados"] if n[CAMBIADO] else [])
        partes.append(f"{etq}: {' '.join(trozos)}")
    return " · ".join(partes)


def fecha_legible(foto: Optional[dict]) -> str:
    try:
        d = datetime.fromisoformat((foto or {}).get("ts", ""))
        return d.strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
        return "?"
