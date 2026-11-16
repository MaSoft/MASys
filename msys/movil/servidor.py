# -*- coding: utf-8 -*-
"""Servidor del enlace con MASysMov (solo red local, todo cifrado).

- El PC escucha en el puerto 47880 mientras MASys está abierto y el enlace
  está activado (sección 📱 Móvil Android).
- Emparejar: el PC enseña un QR con una clave nueva de 32 bytes, un código de
  emparejado de un solo uso (10 minutos), sus IP y el puerto. El móvil lo lee y
  llama a /api/pair con el mensaje cifrado con esa clave: desde ese momento el
  PC solo acepta mensajes de ese móvil que vengan cifrados con su clave.
- /api/informe: el móvil manda su informe completo; el PC responde con los
  encargos pendientes (p. ej. comprobar un puerto desde fuera con datos móviles).
- /api/encargo: el móvil devuelve el resultado de un encargo.
- /api/copias/lista y /api/copias/trozo (4.2.7): copias de apps del móvil por trozos cifrados (ver copias.py).

El servidor nunca ejecuta nada que venga del móvil: solo guarda datos."""

import base64
import ipaddress
import json
import logging
import os
import secrets
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Dict, List, Optional

from msys.core.utils import startupinfo_oculto
from msys.movil import cripto
from msys.movil.almacen import NIVEL_NO, Almacen
from msys.movil.copias import ConflictoTrozo, Copias, id_valido

log = logging.getLogger(__name__)

PUERTO = 47880
MAX_CUERPO = 40 * 1024 * 1024
PREFIJO_DATOS = "datos-"  # encargo de datos personales (5.3.0): no se guarda, se pide en cada envío
DURACION_EMPAREJADO_S = 600
REGLA_FIREWALL = "MASys - enlace con el movil"
REGLA_FIREWALL_ANTERIOR = "MultiSYS - enlace con el movil"  # creada antes de 4.1.7: sigue valiendo
_ID_VALIDO = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")


def ips_locales() -> List[str]:
    """IPv4 privadas de este equipo (las que el móvil puede alcanzar por Wi-Fi), las de Ethernet/Wi-Fi primero."""
    out = []
    try:
        import psutil
        estados = psutil.net_if_stats()
        for nombre, direcciones in psutil.net_if_addrs().items():
            if not estados.get(nombre) or not estados[nombre].isup:
                continue
            virtual = any(p in nombre.lower() for p in ("vethernet", "vmware", "virtualbox", "loopback", "vpn", "tailscale",
                                                        "zerotier", "radmin", "wsl", "hyper-v", "warp"))
            for d in direcciones:
                if d.family != socket.AF_INET:
                    continue
                ip = ipaddress.ip_address(d.address)
                if ip.is_private and not ip.is_loopback and not ip.is_link_local:
                    out.append((1 if virtual else 0, d.address))
    except Exception:
        pass
    return [ip for _p, ip in sorted(out)] or [socket.gethostbyname(socket.gethostname())]


class _Manejador(BaseHTTPRequestHandler):
    server_version = "MASys"
    sys_version = ""

    def log_message(self, formato, *args):  # sin ruido en la consola
        log.debug("movil %s - %s", self.client_address[0], formato % args)

    def _responder(self, codigo: int, cuerpo: bytes = b"", tipo: str = "application/octet-stream"):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        if cuerpo:
            self.wfile.write(cuerpo)

    def do_GET(self):
        if self.path == "/api/hola":
            self._responder(200, json.dumps({"app": "MASys", "nombre": socket.gethostname()}).encode(), "application/json")
        else:
            self._responder(404)

    def do_POST(self):
        srv: "ServidorMovil" = self.server.dueno  # type: ignore[attr-defined]
        try:
            largo = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            largo = -1
        if largo <= 0 or largo > MAX_CUERPO:
            self._responder(413 if largo > MAX_CUERPO else 400)
            return
        cuerpo = self.rfile.read(largo)
        dispositivo = (self.headers.get("X-MASys-Device") or "").strip()
        if not dispositivo or len(dispositivo) > 100 or not set(dispositivo) <= _ID_VALIDO:
            self._responder(400)
            return
        ruta = self.path.split("?", 1)[0]
        try:
            if ruta == "/api/pair":
                respuesta, clave = srv._emparejar(dispositivo, self.headers.get("X-MASys-Pair") or "", cuerpo, self.client_address[0])
            elif ruta in ("/api/informe", "/api/encargo", "/api/copias/lista"):
                clave = srv.almacen.clave(dispositivo)
                if clave is None:
                    self._responder(401)
                    return
                datos = cripto.json_abierto(clave, cuerpo, cripto.aad(dispositivo, ruta))
                if ruta == "/api/informe":
                    respuesta = srv._informe(dispositivo, datos, self.client_address[0])
                elif ruta == "/api/encargo":
                    respuesta = srv._encargo(dispositivo, datos)
                else:
                    respuesta = srv.copias.lista(dispositivo, datos)
                    srv._aviso_copias(dispositivo)
            elif ruta == "/api/copias/trozo":
                clave = srv.almacen.clave(dispositivo)
                if clave is None:
                    self._responder(401)
                    return
                id_ = (self.headers.get("X-MASys-Copia") or "").strip()
                desde = int(self.headers.get("X-MASys-Desde") or "-1")
                if not id_valido(id_) or desde < 0:
                    raise ValueError("Cabeceras de copia no válidas")
                datos = cripto.abrir(clave, cuerpo, cripto.aad(dispositivo, f"{ruta}|{id_}|{desde}"))
                respuesta = srv.copias.trozo(dispositivo, id_, desde, datos)
                if respuesta.get("completa"):
                    srv._aviso_copias(dispositivo)
            else:
                self._responder(404)
                return
        except cripto.ErrorCifrado:
            self._responder(401)
            return
        except PermissionError:
            self._responder(403)
            return
        except ConflictoTrozo as e:
            log.info("Trozo fuera de orden: %s", e)
            self._responder(409)
            return
        except (ValueError, KeyError, TypeError) as e:
            log.warning("Petición del móvil no válida: %s", e)
            self._responder(400)
            return
        except Exception:
            log.exception("Error atendiendo al móvil")
            self._responder(500)
            return
        self._responder(200, cripto.json_sellado(clave, respuesta, cripto.aad(dispositivo, ruta + "|resp")))


class ServidorMovil:
    def __init__(self, almacen: Almacen, al_recibir: Optional[Callable[[str, int], None]] = None,
                 al_emparejar: Optional[Callable[[str, str], None]] = None,
                 al_resultado: Optional[Callable[[str, str], None]] = None, puerto: int = PUERTO,
                 al_copia: Optional[Callable[[str], None]] = None):
        self.almacen = almacen
        self.copias = Copias(almacen)
        self.al_copia = al_copia
        self.puerto = puerto
        self.al_recibir, self.al_emparejar, self.al_resultado = al_recibir, al_emparejar, al_resultado
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._hilo: Optional[threading.Thread] = None
        self._sesiones: Dict[str, tuple] = {}  # pairId -> (clave, caduca)
        self._lock = threading.Lock()

    # ── marcha ───────────────────────────────────────────────
    @property
    def activo(self) -> bool:
        return self._httpd is not None

    def arrancar(self):
        if self._httpd:
            return
        httpd = ThreadingHTTPServer(("0.0.0.0", self.puerto), _Manejador)
        httpd.daemon_threads = True
        httpd.dueno = self  # type: ignore[attr-defined]
        self._httpd = httpd
        self._hilo = threading.Thread(target=httpd.serve_forever, name="MASys-movil", daemon=True)
        self._hilo.start()

    def parar(self):
        httpd, self._httpd = self._httpd, None
        if httpd:
            httpd.shutdown()
            httpd.server_close()

    # ── emparejado ───────────────────────────────────────────
    def nueva_sesion(self) -> dict:
        """Datos del QR. La clave nueva solo vale para el primer móvil que la use en 10 minutos."""
        clave = secrets.token_bytes(32)
        par = secrets.token_urlsafe(12)
        with self._lock:
            ahora = time.time()
            self._sesiones = {k: v for k, v in self._sesiones.items() if v[1] > ahora}
            self._sesiones[par] = (clave, ahora + DURACION_EMPAREJADO_S)
        return {"t": "masys-pair", "v": 1, "key": base64.b64encode(clave).decode(), "pair": par,
                "port": self.puerto, "ips": ips_locales(), "name": socket.gethostname()}

    def _emparejar(self, dispositivo: str, par: str, cuerpo: bytes, ip: str):
        with self._lock:
            sesion = self._sesiones.get(par)
            if not sesion or sesion[1] < time.time():
                raise PermissionError("Código de emparejado caducado o desconocido")
            clave = sesion[0]
        datos = cripto.json_abierto(clave, cuerpo, cripto.aad(dispositivo, "/api/pair"))
        with self._lock:
            self._sesiones.pop(par, None)  # un solo uso
        nombre = str(datos.get("deviceName") or "Móvil")[:80]
        self.almacen.guardar_dispositivo(dispositivo, nombre, "wifi", clave=clave, modelo=nombre,
                                         version_app=str(datos.get("appVersion") or "")[:40], ip=ip)
        if self.al_emparejar:
            self.al_emparejar(dispositivo, nombre)
        return {"ok": True, "name": socket.gethostname()}, clave

    # ── informes y encargos ──────────────────────────────────
    def _informe(self, dispositivo: str, informe: dict, ip: str) -> dict:
        if not isinstance(informe, dict) or informe.get("formato") != "masysmov_informe":
            raise ValueError("No es un informe de MASysMov")
        id_ = self.almacen.guardar_informe(dispositivo, informe, "wifi", ip=ip)
        if self.al_recibir:
            self.al_recibir(dispositivo, id_)
        encargos = self.almacen.encargos_para(dispositivo)
        # Copias de apps: si el móvil lo tiene activado, en cada envío se le piden las que falten (sin guardar encargo)
        if self.almacen.copias_auto(dispositivo) and not any(e.get("tipo") == "copias" for e in encargos):
            encargos.append({"id": f"copias-auto-{int(time.time())}", "tipo": "copias"})
        # 5.3.0: contactos, llamadas y SMS con el nivel que eligió el técnico (sin guardar encargo; «no» = no se piden)
        nivel = self.almacen.nivel_datos()
        if nivel != NIVEL_NO:
            encargos.append({"id": f"{PREFIJO_DATOS}{int(time.time())}", "tipo": "datos", "nivel": nivel})
        return {"ok": True, "name": socket.gethostname(), "encargos": encargos}

    def _aviso_copias(self, dispositivo: str):
        if self.al_copia:
            try:
                self.al_copia(dispositivo)
            except Exception:
                log.exception("Aviso de copia")

    def _encargo(self, dispositivo: str, datos: dict) -> dict:
        if not isinstance(datos, dict) or not isinstance(datos.get("id"), str) or not isinstance(datos.get("resultado"), dict):
            raise ValueError("Resultado de encargo no válido")
        if datos["id"].startswith(PREFIJO_DATOS):
            res = datos["resultado"]
            # Si el técnico pasó a «No recibir» mientras tanto, no se guarda nada
            if res.get("error") or self.almacen.nivel_datos() == NIVEL_NO:
                return {"ok": True}
            self.almacen.guardar_datos_personales(dispositivo, res)
            if self.al_resultado:
                self.al_resultado(dispositivo, datos["id"])
            return {"ok": True}
        ok = self.almacen.resultado_encargo(dispositivo, datos["id"], datos["resultado"])
        if ok and self.al_resultado:
            self.al_resultado(dispositivo, datos["id"])
        return {"ok": ok}


# ── firewall de Windows ─────────────────────────────────────────────
def regla_firewall_existe() -> bool:
    for nombre in (REGLA_FIREWALL, REGLA_FIREWALL_ANTERIOR):
        try:
            r = subprocess.run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={nombre}"], capture_output=True,
                               timeout=15, startupinfo=startupinfo_oculto(), creationflags=subprocess.CREATE_NO_WINDOW)
            if r.returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            continue
    return False


def crear_regla_firewall(puerto: int = PUERTO) -> tuple:
    """Permite el puerto SOLO en redes privadas y de dominio (nunca en las públicas)."""
    try:
        r = subprocess.run(["netsh", "advfirewall", "firewall", "add", "rule", f"name={REGLA_FIREWALL}", "dir=in", "action=allow",
                            "protocol=TCP", f"localport={puerto}", "profile=private,domain"], capture_output=True, timeout=20,
                           startupinfo=startupinfo_oculto(), creationflags=subprocess.CREATE_NO_WINDOW)
        if r.returncode == 0:
            return True, ""
        return False, (r.stdout or b"").decode("cp850", errors="replace").strip() or "netsh devolvió un error"
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
