# -*- coding: utf-8 -*-
"""Servidor de RustDesk en este PC (4.12.0): hbbs.exe y hbbr.exe compilados por nosotros desde rustdesk-server
1.1.16 con SERVIDOR_RUSTDESK\\masys.patch (ver FUENTE.txt). Con el parche, el servidor no toca la configuración
del RustDesk del usuario: su clave (id_ed25519/.pub) y su base de datos (db_v2.sqlite3) van en el directorio de
trabajo, que aquí es SERVIDOR_RUSTDESK\\datos.

- Arrancar/parar: procesos sin ventana con datos\\ como directorio de trabajo, o las tareas programadas si están
  creadas (arrancan con Windows como SYSTEM, sin sesión iniciada: una por programa, porque las acciones de una
  misma tarea se ejecutan una tras otra y hbbs no termina nunca).
- Firewall: reglas por programa y puerto, solo redes privadas y de dominio (el router reenvía a este PC).
- Equipos dados de alta: tabla «peer» de db_v2.sqlite3, solo lectura (src/peer.rs y src/database.rs del servidor).

Sin Qt: la ventana está en msys/ui/acceso_remoto_dialog.py (ServidorPropioDialog)."""

import json
import os
import socket
import sqlite3
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import psutil

from msys import config as msys_config
from msys.domain import acceso_remoto as ar

CARPETA = "SERVIDOR_RUSTDESK"
PROGRAMAS = ("hbbs", "hbbr")
# (programa, protocolo, puertos): 21115 prueba de red, 21116 identificación (TCP y UDP), 21117 retransmisión
REGLAS = [("hbbs", "TCP", f"{ar.PUERTO_NAT},{ar.PUERTO_ID}"), ("hbbs", "UDP", str(ar.PUERTO_ID)),
          ("hbbr", "TCP", str(ar.PUERTO_RELAY))]
PUERTOS = (ar.PUERTO_NAT, ar.PUERTO_ID, ar.PUERTO_RELAY)
NOMBRE = "MASys servidor RustDesk"


def nombre_tarea(programa: str) -> str:
    return f"{NOMBRE} ({programa})"


def nombre_regla(programa: str, protocolo: str) -> str:
    return f"{NOMBRE} ({programa} {protocolo})"


@dataclass
class Estado:
    hbbs: bool
    hbbr: bool
    tareas: bool = False
    firewall: bool = False

    @property
    def en_marcha(self) -> bool:
        return self.hbbs and self.hbbr

    @property
    def texto(self) -> str:
        if self.en_marcha:
            return "En marcha"
        if self.hbbs or self.hbbr:
            return f"Solo {'hbbs' if self.hbbs else 'hbbr'} en marcha"
        return "Parado"


@dataclass
class EquipoServidor:
    id: str
    alta: str      # «DD/MM/YYYY HH:MM» en hora local
    ip: str


def _sin_ventana():
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    si.wShowWindow = 0
    return si


def _ejecutar(argv: List[str], timeout: float = 30) -> Tuple[int, str]:
    try:
        r = subprocess.run(argv, capture_output=True, timeout=timeout, startupinfo=_sin_ventana(),
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.SubprocessError) as e:
        return -1, str(e)
    salida = (r.stdout or b"") + (r.stderr or b"")
    return r.returncode, salida.decode("cp850", errors="replace").strip()


def _ps_texto(valor: str) -> str:
    """Cadena literal de PowerShell entre comillas simples (las simples se doblan)."""
    if "\n" in valor or "\r" in valor:
        raise ValueError("Ruta no válida")
    return "'" + valor.replace("'", "''") + "'"


def fecha_local(texto_utc: str) -> str:
    """created_at de SQLite (UTC, «AAAA-MM-DD HH:MM:SS») → «DD/MM/YYYY HH:MM» en hora local."""
    try:
        d = datetime.strptime(texto_utc[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).astimezone()
        return d.strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
        return texto_utc or ""


class ServidorLocal:
    def __init__(self, base: Optional[str] = None):
        self.base = base or os.path.join(msys_config.app_base_dir(), CARPETA)

    # ── Rutas ───────────────────────────────────────────────────────────
    @property
    def carpeta_bin(self) -> str:
        return os.path.join(self.base, "bin")

    @property
    def carpeta_datos(self) -> str:
        return os.path.join(self.base, "datos")

    def programa(self, nombre: str) -> str:
        return os.path.join(self.carpeta_bin, f"{nombre}.exe")

    def faltan(self) -> List[str]:
        return [f"{p}.exe" for p in PROGRAMAS if not os.path.isfile(self.programa(p))]

    # ── Procesos ────────────────────────────────────────────────────────
    def procesos(self) -> Dict[str, List[psutil.Process]]:
        """Los hbbs/hbbr que se ejecutan desde NUESTRA carpeta bin (nunca otros servidores del equipo)."""
        nuestros = {os.path.normcase(os.path.abspath(self.programa(p))): p for p in PROGRAMAS}
        encontrados: Dict[str, List[psutil.Process]] = {p: [] for p in PROGRAMAS}
        for proc in psutil.process_iter(["name", "exe"]):
            try:
                if (proc.info.get("name") or "").lower() not in ("hbbs.exe", "hbbr.exe"):
                    continue
                ruta = proc.info.get("exe") or proc.exe()
            except (psutil.Error, OSError):
                continue
            programa = nuestros.get(os.path.normcase(os.path.abspath(ruta or "")))
            if programa:
                encontrados[programa].append(proc)
        return encontrados

    def estado(self, con_sistema: bool = True) -> Estado:
        p = self.procesos()
        e = Estado(bool(p["hbbs"]), bool(p["hbbr"]))
        if con_sistema:
            e.tareas = self.tareas_existen()
            e.firewall = self.reglas_existen()
        return e

    def puertos_ocupados(self) -> List[Tuple[int, str]]:
        """(puerto, programa) de los puertos del servidor que ya usa OTRO programa."""
        nuestros = {pr.pid for lista in self.procesos().values() for pr in lista}
        ocupados = []
        try:
            conexiones = psutil.net_connections(kind="inet")
        except (psutil.Error, OSError):
            return []
        for c in conexiones:
            if not c.laddr or c.laddr.port not in PUERTOS or c.pid in nuestros:
                continue
            if c.type == socket.SOCK_STREAM and c.status != psutil.CONN_LISTEN:   # TCP que no escucha: saliente
                continue
            try:
                quien = psutil.Process(c.pid).name() if c.pid else "otro programa"
            except psutil.Error:
                quien = "otro programa"
            if (c.laddr.port, quien) not in ocupados:
                ocupados.append((c.laddr.port, quien))
        return sorted(ocupados)

    def arrancar(self, espera: float = 4.0) -> None:
        """RuntimeError con el motivo si no se puede. Con las tareas creadas se arrancan ellas (como SYSTEM)."""
        faltan = self.faltan()
        if faltan:
            raise RuntimeError(f"Falta {', '.join(faltan)} en {self.carpeta_bin}")
        actuales = self.procesos()
        ocupados = [f"{puerto} ({quien})" for puerto, quien in self.puertos_ocupados()]
        if ocupados and not any(actuales.values()):
            raise RuntimeError("Estos puertos ya los usa otro programa: " + ", ".join(ocupados))
        os.makedirs(self.carpeta_datos, exist_ok=True)
        if self.tareas_existen():
            for p in PROGRAMAS:
                if not actuales[p]:
                    rc, salida = _ejecutar(["schtasks", "/Run", "/TN", nombre_tarea(p)])
                    if rc != 0:
                        raise RuntimeError(f"No se pudo arrancar la tarea de {p}: {salida}")
        else:
            for p in PROGRAMAS:
                if actuales[p]:
                    continue
                with open(os.path.join(self.carpeta_datos, f"{p}.log"), "wb") as log:
                    subprocess.Popen([self.programa(p)], cwd=self.carpeta_datos, stdout=log, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, close_fds=True,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
                                     | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        fin = time.time() + espera
        while time.time() < fin:
            # hbbs escribe la clave al arrancar: hasta que existe, «Generar programa» no tendría con qué
            if self.estado(con_sistema=False).en_marcha and self.clave_publica():
                return
            time.sleep(0.3)
        if self.estado(con_sistema=False).en_marcha:
            return
        e = self.estado(con_sistema=False)
        if not e.en_marcha:
            raise RuntimeError(f"El servidor no se quedó en marcha ({e.texto.lower()}). " + self.ultimo_error())

    def parar(self, espera: float = 5.0) -> None:
        if self.tareas_existen():
            for p in PROGRAMAS:
                _ejecutar(["schtasks", "/End", "/TN", nombre_tarea(p)])
        procesos = [pr for lista in self.procesos().values() for pr in lista]
        for pr in procesos:
            try:
                pr.terminate()
            except psutil.Error:
                pass
        _vivos, quedan = psutil.wait_procs(procesos, timeout=espera)
        for pr in quedan:
            try:
                pr.kill()
            except psutil.Error:
                pass
        if any(self.procesos().values()):
            raise RuntimeError("No se pudo parar el servidor (¿lo arrancó otro usuario sin permisos de administrador?)")

    def ultimo_error(self) -> str:
        """Última línea con ERROR de los registros (lo arrancado desde MASys; la tarea programada no deja registro)."""
        for p in PROGRAMAS:
            ruta = os.path.join(self.carpeta_datos, f"{p}.log")
            try:
                with open(ruta, encoding="utf-8", errors="replace") as f:
                    lineas = [x.strip() for x in f.readlines()[-40:] if "ERROR" in x or "Error" in x]
            except OSError:
                continue
            if lineas:
                return f"{p}: {lineas[-1][-200:]}"
        return ""

    # ── Clave y equipos ─────────────────────────────────────────────────
    def clave_publica(self) -> str:
        ruta = os.path.join(self.carpeta_datos, ar.ARCHIVO_CLAVE)
        if not os.path.isfile(ruta):
            return ""
        try:
            return ar.leer_clave_archivo(ruta)
        except ValueError:
            return ""

    def equipos(self) -> List[EquipoServidor]:
        """Equipos dados de alta en el servidor (solo lectura; hbbs puede estar escribiendo a la vez)."""
        ruta = os.path.join(self.carpeta_datos, "db_v2.sqlite3")
        if not os.path.isfile(ruta):
            return []
        uri = "file:" + ruta.replace("\\", "/") + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=5) as c:
            filas = c.execute("SELECT id, created_at, info FROM peer ORDER BY created_at DESC").fetchall()
        resultado = []
        for id_, creado, info in filas:
            try:
                ip = str(json.loads(info or "{}").get("ip", ""))
            except (TypeError, ValueError):
                ip = ""
            resultado.append(EquipoServidor(str(id_), fecha_local(str(creado or "")), ip.replace("::ffff:", "")))
        return resultado

    # ── Arranque con Windows (tareas programadas como SYSTEM) ───────────
    def tareas_existen(self) -> bool:
        return all(_ejecutar(["schtasks", "/Query", "/TN", nombre_tarea(p)], 15)[0] == 0 for p in PROGRAMAS)

    def crear_tareas(self) -> Tuple[bool, str]:
        faltan = self.faltan()
        if faltan:
            return False, f"Falta {', '.join(faltan)}"
        os.makedirs(self.carpeta_datos, exist_ok=True)
        partes = ["$t = New-ScheduledTaskTrigger -AtStartup",
                  "$u = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest",
                  "$c = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 "
                  "-RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
                  "-StartWhenAvailable -MultipleInstances IgnoreNew"]
        for p in PROGRAMAS:
            partes.append(f"$a = New-ScheduledTaskAction -Execute {_ps_texto(self.programa(p))} "
                          f"-WorkingDirectory {_ps_texto(self.carpeta_datos)}")
            partes.append(f"Register-ScheduledTask -TaskName {_ps_texto(nombre_tarea(p))} -Action $a -Trigger $t "
                          f"-Principal $u -Settings $c -Force | Out-Null")
        rc, salida = _ejecutar(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                "-Command", "$ErrorActionPreference = 'Stop'; " + "; ".join(partes)], 60)
        if rc == 0 and self.tareas_existen():
            return True, ""
        return False, salida or "PowerShell devolvió un error (¿MASys sin permisos de administrador?)"

    def quitar_tareas(self) -> Tuple[bool, str]:
        errores = []
        for p in PROGRAMAS:
            if _ejecutar(["schtasks", "/Query", "/TN", nombre_tarea(p)], 15)[0] != 0:
                continue
            rc, salida = _ejecutar(["schtasks", "/Delete", "/TN", nombre_tarea(p), "/F"])
            if rc != 0:
                errores.append(salida)
        return (not errores), "; ".join(errores)

    # ── Firewall ────────────────────────────────────────────────────────
    def reglas_existen(self) -> bool:
        return all(_ejecutar(["netsh", "advfirewall", "firewall", "show", "rule", f"name={nombre_regla(p, proto)}"],
                             15)[0] == 0 for p, proto, _pu in REGLAS)

    def crear_reglas(self) -> Tuple[bool, str]:
        """Por programa y puerto, solo redes privadas y de dominio. Se rehacen (por si cambió la carpeta)."""
        errores = []
        for p, proto, puertos in REGLAS:
            nombre = nombre_regla(p, proto)
            _ejecutar(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={nombre}"], 15)
            rc, salida = _ejecutar(["netsh", "advfirewall", "firewall", "add", "rule", f"name={nombre}", "dir=in",
                                    "action=allow", f"program={self.programa(p)}", f"protocol={proto}",
                                    f"localport={puertos}", "profile=private,domain"], 20)
            if rc != 0:
                errores.append(f"{nombre}: {salida or 'netsh devolvió un error'}")
        return (not errores), "; ".join(errores)

    def quitar_reglas(self) -> Tuple[bool, str]:
        errores = []
        for p, proto, _pu in REGLAS:
            nombre = nombre_regla(p, proto)
            if _ejecutar(["netsh", "advfirewall", "firewall", "show", "rule", f"name={nombre}"], 15)[0] != 0:
                continue
            rc, salida = _ejecutar(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={nombre}"], 15)
            if rc != 0:
                errores.append(salida)
        return (not errores), "; ".join(errores)


def equipos_sin_avisar(equipos: List[EquipoServidor], agenda_ids, avisados) -> List[EquipoServidor]:
    """Equipos del servidor que no están en la agenda (ID sin espacios) ni se avisaron ya (5.0.0)."""
    en_agenda = {"".join(str(x).split()) for x in agenda_ids if x}
    ya = {str(x) for x in avisados}
    return [q for q in equipos if q.id not in en_agenda and q.id not in ya]
