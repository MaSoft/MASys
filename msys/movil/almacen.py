# -*- coding: utf-8 -*-
"""Móviles enlazados, sus informes (histórico) y los encargos pendientes, en
movil.db junto a la app. La clave de cada móvil y el contenido de cada informe
se guardan protegidos con DPAPI (solo los abre este usuario de Windows en este
equipo): los informes llevan qué apps hay y a qué dominios se conectan."""

import contextlib
import json
import os
import sqlite3
import threading
import time
import uuid
from typing import List, Optional

from msys import config as msys_config
from msys.movil import cripto

ESQUEMA = """
CREATE TABLE IF NOT EXISTS dispositivos (
    id TEXT PRIMARY KEY, nombre TEXT NOT NULL, modelo TEXT NOT NULL DEFAULT '',
    clave BLOB, origen TEXT NOT NULL, alta INTEGER NOT NULL, ultimo INTEGER NOT NULL DEFAULT 0,
    version_app TEXT NOT NULL DEFAULT '', ip TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS informes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, dispositivo TEXT NOT NULL, recibido INTEGER NOT NULL,
    generado INTEGER NOT NULL, origen TEXT NOT NULL, datos BLOB NOT NULL);
CREATE INDEX IF NOT EXISTS ix_informes ON informes (dispositivo, generado);
CREATE TABLE IF NOT EXISTS encargos (
    id TEXT PRIMARY KEY, dispositivo TEXT NOT NULL, creado INTEGER NOT NULL, tipo TEXT NOT NULL,
    datos TEXT NOT NULL, estado TEXT NOT NULL, enviado INTEGER NOT NULL DEFAULT 0,
    hecho INTEGER NOT NULL DEFAULT 0, resultado TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS copias (
    dispositivo TEXT NOT NULL, id TEXT NOT NULL, nombre TEXT NOT NULL, tamano INTEGER NOT NULL, pkg TEXT NOT NULL DEFAULT '',
    app TEXT NOT NULL DEFAULT '', version TEXT NOT NULL DEFAULT '', creada INTEGER NOT NULL DEFAULT 0,
    contenido TEXT NOT NULL DEFAULT '', recibidos INTEGER NOT NULL DEFAULT 0, completa INTEGER NOT NULL DEFAULT 0,
    ruta TEXT NOT NULL DEFAULT '', anunciada INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (dispositivo, id));
CREATE TABLE IF NOT EXISTS ajustes (clave TEXT PRIMARY KEY, valor TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS datos_personales (
    dispositivo TEXT PRIMARY KEY, recibido INTEGER NOT NULL, nivel TEXT NOT NULL, datos BLOB NOT NULL);
"""

PENDIENTE, ENVIADO, HECHO = "Pendiente", "Enviado al móvil", "Hecho"

# 5.3.0: «Puerto desde fuera» ya no es de un móvil. El pedido se guarda SIN móvil (dispositivo = '') y se lo queda el
# primer móvil emparejado que envíe su informe. Si contesta «sin datos móviles» vuelve a pendiente (con el intento
# anotado) y ese móvil no lo vuelve a coger en REINTENTO_MS; si se lo lleva y no contesta en CADUCA_MS, vuelve a pendiente.
SIN_MOVIL = ""
CADUCA_MS = 10 * 60 * 1000
REINTENTO_MS = 30 * 60 * 1000

# Datos personales que se piden al móvil (lo elige el técnico en la ventana de Móviles)
NIVEL_NO, NIVEL_RECUENTOS, NIVEL_COMPLETO = "no", "recuentos", "completo"
NIVELES = (NIVEL_NO, NIVEL_RECUENTOS, NIVEL_COMPLETO)
CLAVE_NIVEL = "datos_personales"


def sin_datos_moviles(resultado: dict) -> bool:
    """El móvil no pudo hacer la prueba porque no tiene datos móviles (MasysLink.checkPorts: red «ninguna»)."""
    return isinstance(resultado, dict) and resultado.get("red") == "ninguna" and bool(resultado.get("error"))


def ahora_ms() -> int:
    return int(time.time() * 1000)


class Almacen:
    def __init__(self, ruta: Optional[str] = None, proteger=cripto.proteger, desproteger=cripto.desproteger):
        self.ruta = ruta or os.path.join(msys_config.app_base_dir(), "movil.db")
        self._proteger, self._desproteger = proteger, desproteger
        self._lock = threading.RLock()
        with self._con() as c:
            c.executescript(ESQUEMA)
            # 4.2.7: traer las copias de apps en cada envío (migración aditiva: los datos de antes no se tocan)
            columnas = {r["name"] for r in c.execute("PRAGMA table_info(dispositivos)")}
            if "copias_auto" not in columnas:
                c.execute("ALTER TABLE dispositivos ADD COLUMN copias_auto INTEGER NOT NULL DEFAULT 1")
            # 4.2.8: copias en carpeta con .apk (MASysMov 1.2.4): huella del contenido aparte del id
            if "sha" not in {r["name"] for r in c.execute("PRAGMA table_info(copias)")}:
                c.execute("ALTER TABLE copias ADD COLUMN sha TEXT NOT NULL DEFAULT ''")
            # 5.3.0: cada móvil de una empresa y un usuario. Se guarda el NOMBRE (como la agenda de Acceso remoto): el
            # catálogo 👥 Empresas y usuarios ayuda a elegirlo, pero quitar algo de él no deja móviles colgando. '' = sin asignar
            if "empresa" not in columnas:
                c.execute("ALTER TABLE dispositivos ADD COLUMN empresa TEXT NOT NULL DEFAULT ''")
            if "usuario" not in columnas:
                c.execute("ALTER TABLE dispositivos ADD COLUMN usuario TEXT NOT NULL DEFAULT ''")
            # 5.3.0: intentos de un pedido de puertos (móviles que lo cogieron sin datos móviles)
            if "intentos" not in {r["name"] for r in c.execute("PRAGMA table_info(encargos)")}:
                c.execute("ALTER TABLE encargos ADD COLUMN intentos TEXT NOT NULL DEFAULT ''")

    @contextlib.contextmanager
    def _con(self):
        # Una conexión por operación y SIEMPRE cerrada ('with sqlite3.connect' no la cierra)
        with self._lock:
            c = sqlite3.connect(self.ruta, timeout=15)
            c.row_factory = sqlite3.Row
            try:
                yield c
                c.commit()
            except Exception:
                c.rollback()
                raise
            finally:
                c.close()

    # ── dispositivos ─────────────────────────────────────────
    def guardar_dispositivo(self, id_: str, nombre: str, origen: str, clave: Optional[bytes] = None, modelo: str = "",
                            version_app: str = "", ip: str = ""):
        cifrada = self._proteger(clave) if clave else None
        with self._con() as c:
            c.execute("""INSERT INTO dispositivos (id, nombre, modelo, clave, origen, alta, version_app, ip) VALUES (?,?,?,?,?,?,?,?)
                         ON CONFLICT(id) DO UPDATE SET nombre=excluded.nombre,
                           modelo=CASE WHEN excluded.modelo != '' THEN excluded.modelo ELSE dispositivos.modelo END,
                           clave=COALESCE(excluded.clave, dispositivos.clave),
                           origen=CASE WHEN dispositivos.origen = 'wifi' THEN 'wifi' ELSE excluded.origen END,
                           version_app=CASE WHEN excluded.version_app != '' THEN excluded.version_app ELSE dispositivos.version_app END,
                           ip=CASE WHEN excluded.ip != '' THEN excluded.ip ELSE dispositivos.ip END""",
                      (id_, nombre, modelo, cifrada, origen, ahora_ms(), version_app, ip))

    def clave(self, id_: str) -> Optional[bytes]:
        with self._con() as c:
            r = c.execute("SELECT clave FROM dispositivos WHERE id = ?", (id_,)).fetchone()
        if not r or not r["clave"]:
            return None
        try:
            return self._desproteger(bytes(r["clave"]))
        except Exception:
            return None

    def dispositivos(self) -> List[dict]:
        with self._con() as c:
            filas = c.execute("""SELECT d.id, d.nombre, d.modelo, d.origen, d.alta, d.ultimo, d.version_app, d.ip, d.clave IS NOT NULL AS enlazado,
                                        d.empresa, d.usuario,
                                        (SELECT COUNT(*) FROM informes i WHERE i.dispositivo = d.id) AS informes
                                 FROM dispositivos d ORDER BY d.ultimo DESC, d.nombre""").fetchall()
        return [dict(f) for f in filas]

    def asignar(self, id_: str, empresa: str, usuario: str):
        """5.3.0: empresa y usuario del móvil (los dos vacíos = «Sin asignar»; si hay uno, tienen que estar los dos)."""
        empresa, usuario = " ".join((empresa or "").split())[:120], " ".join((usuario or "").split())[:120]
        if bool(empresa) != bool(usuario):
            raise ValueError("Empresa y usuario van juntos")
        with self._con() as c:
            c.execute("UPDATE dispositivos SET empresa = ?, usuario = ? WHERE id = ?", (empresa, usuario, id_))

    def renombrar(self, id_: str, nombre: str):
        with self._con() as c:
            c.execute("UPDATE dispositivos SET nombre = ? WHERE id = ?", (nombre.strip()[:80], id_))

    def quitar_dispositivo(self, id_: str):
        with self._con() as c:
            c.execute("DELETE FROM informes WHERE dispositivo = ?", (id_,))
            # Los pedidos de puertos son de todos (5.3.0): los hechos se quedan en la lista; uno a medias vuelve a pendiente
            c.execute("UPDATE encargos SET dispositivo = ?, estado = ?, enviado = 0 WHERE dispositivo = ? AND tipo = 'puerto' AND estado != ?",
                      (SIN_MOVIL, PENDIENTE, id_, HECHO))
            c.execute("DELETE FROM encargos WHERE dispositivo = ? AND tipo != 'puerto'", (id_,))
            c.execute("DELETE FROM datos_personales WHERE dispositivo = ?", (id_,))
            c.execute("DELETE FROM copias WHERE dispositivo = ?", (id_,))  # los .zip del disco NO se borran
            c.execute("DELETE FROM dispositivos WHERE id = ?", (id_,))

    def desenlazar(self, id_: str):
        """Olvida la clave (deja de aceptar envíos) pero conserva los informes."""
        with self._con() as c:
            c.execute("UPDATE dispositivos SET clave = NULL, origen = 'archivo' WHERE id = ?", (id_,))

    # ── informes ─────────────────────────────────────────────
    def guardar_informe(self, dispositivo: str, informe: dict, origen: str, ip: str = "") -> int:
        disp = informe.get("dispositivo") or {}
        generado = int(informe.get("generado") or ahora_ms())
        datos = self._proteger(json.dumps(informe, ensure_ascii=False).encode("utf-8"))
        with self._con() as c:
            # El mismo informe importado dos veces (mismo móvil y misma fecha) no se duplica
            if c.execute("SELECT 1 FROM informes WHERE dispositivo = ? AND generado = ?", (dispositivo, generado)).fetchone():
                return 0
            cur = c.execute("INSERT INTO informes (dispositivo, recibido, generado, origen, datos) VALUES (?,?,?,?,?)",
                            (dispositivo, ahora_ms(), generado, origen, datos))
            c.execute("UPDATE dispositivos SET ultimo = MAX(ultimo, ?), modelo = CASE WHEN ? != '' THEN ? ELSE modelo END, "
                      "version_app = CASE WHEN ? != '' THEN ? ELSE version_app END, ip = CASE WHEN ? != '' THEN ? ELSE ip END WHERE id = ?",
                      (generado, disp.get("modelo", ""), disp.get("modelo", ""), informe.get("app", ""), informe.get("app", ""), ip, ip, dispositivo))
            return cur.lastrowid

    def informes(self, dispositivo: str, limite: int = 120) -> List[dict]:
        """Los más recientes primero, ya descifrados: [{id, recibido, generado, origen, informe}]."""
        with self._con() as c:
            filas = c.execute("SELECT id, recibido, generado, origen, datos FROM informes WHERE dispositivo = ? ORDER BY generado DESC LIMIT ?",
                              (dispositivo, limite)).fetchall()
        out = []
        for f in filas:
            try:
                informe = json.loads(self._desproteger(bytes(f["datos"])).decode("utf-8"))
            except Exception:
                continue
            out.append({"id": f["id"], "recibido": f["recibido"], "generado": f["generado"], "origen": f["origen"], "informe": informe})
        return out

    def quitar_informe(self, id_: int):
        with self._con() as c:
            c.execute("DELETE FROM informes WHERE id = ?", (id_,))

    # ── encargos (lo que el PC pide al móvil) ─────────────────
    def nuevo_encargo(self, dispositivo: str, tipo: str, datos: dict) -> str:
        id_ = str(uuid.uuid4())
        with self._con() as c:
            c.execute("INSERT INTO encargos (id, dispositivo, creado, tipo, datos, estado) VALUES (?,?,?,?,?,?)",
                      (id_, dispositivo, ahora_ms(), tipo, json.dumps(datos, ensure_ascii=False), PENDIENTE))
        return id_

    def liberar_caducados(self, ahora: Optional[int] = None) -> int:
        """Pedidos de puertos que un móvil se llevó y no contestó en CADUCA_MS: vuelven a pendiente para otro móvil."""
        limite = (ahora or ahora_ms()) - CADUCA_MS
        with self._con() as c:
            cur = c.execute("UPDATE encargos SET dispositivo = ?, estado = ?, enviado = 0 WHERE tipo = 'puerto' AND estado = ? "
                            "AND enviado > 0 AND enviado < ?", (SIN_MOVIL, PENDIENTE, ENVIADO, limite))
            return cur.rowcount

    def _coger_puertos(self, c, dispositivo: str, ahora: int):
        """El móvil que envía se queda los pedidos de puertos pendientes (si no los acaba de intentar sin datos móviles)."""
        filas = c.execute("SELECT id, intentos FROM encargos WHERE dispositivo = ? AND tipo = 'puerto' AND estado = ? ORDER BY creado",
                          (SIN_MOVIL, PENDIENTE)).fetchall()
        for f in filas:
            intentos = json.loads(f["intentos"]) if f["intentos"] else []
            if any(i.get("dispositivo") == dispositivo and ahora - int(i.get("fecha") or 0) < REINTENTO_MS for i in intentos):
                continue
            # Asignación atómica: si otro móvil lo cogió en el mismo instante, no cambia ninguna fila
            c.execute("UPDATE encargos SET dispositivo = ? WHERE id = ? AND dispositivo = ? AND estado = ?",
                      (dispositivo, f["id"], SIN_MOVIL, PENDIENTE))

    def encargos_para(self, dispositivo: str) -> List[dict]:
        """Pendientes y ya enviados sin respuesta (se reenvían por si el móvil no llegó a hacerlos)."""
        self.liberar_caducados()
        with self._con() as c:
            self._coger_puertos(c, dispositivo, ahora_ms())
            filas = c.execute("SELECT id, tipo, datos FROM encargos WHERE dispositivo = ? AND estado != ? ORDER BY creado",
                              (dispositivo, HECHO)).fetchall()
            c.execute("UPDATE encargos SET estado = ?, enviado = ? WHERE dispositivo = ? AND estado = ?",
                      (ENVIADO, ahora_ms(), dispositivo, PENDIENTE))
        return [{"id": f["id"], "tipo": f["tipo"], **json.loads(f["datos"])} for f in filas]

    def resultado_encargo(self, dispositivo: str, id_: str, resultado: dict) -> bool:
        with self._con() as c:
            if sin_datos_moviles(resultado):
                f = c.execute("SELECT intentos FROM encargos WHERE id = ? AND dispositivo = ? AND tipo = 'puerto'", (id_, dispositivo)).fetchone()
                if f is not None:
                    intentos = json.loads(f["intentos"]) if f["intentos"] else []
                    intentos = [i for i in intentos if i.get("dispositivo") != dispositivo][-19:]
                    intentos.append({"dispositivo": dispositivo, "fecha": ahora_ms(), "error": str(resultado.get("error"))[:200]})
                    c.execute("UPDATE encargos SET dispositivo = ?, estado = ?, enviado = 0, intentos = ? WHERE id = ?",
                              (SIN_MOVIL, PENDIENTE, json.dumps(intentos, ensure_ascii=False), id_))
                    return True
            cur = c.execute("UPDATE encargos SET estado = ?, hecho = ?, resultado = ? WHERE id = ? AND dispositivo = ?",
                            (HECHO, ahora_ms(), json.dumps(resultado, ensure_ascii=False), id_, dispositivo))
            return cur.rowcount > 0

    def encargos(self, dispositivo: Optional[str] = None) -> List[dict]:
        sql = "SELECT * FROM encargos" + (" WHERE dispositivo = ?" if dispositivo else "") + " ORDER BY creado DESC LIMIT 200"
        with self._con() as c:
            filas = c.execute(sql, (dispositivo,) if dispositivo else ()).fetchall()
        out = []
        for f in filas:
            d = dict(f)
            d["datos"] = json.loads(d["datos"] or "{}")
            d["resultado"] = json.loads(d["resultado"]) if d["resultado"] else None
            d["intentos"] = json.loads(d["intentos"]) if d.get("intentos") else []
            out.append(d)
        return out

    def encargos_puerto(self) -> List[dict]:
        """5.3.0: todos los pedidos de «Puerto desde fuera», de todos los móviles, los más recientes primero."""
        self.liberar_caducados()
        with self._con() as c:
            filas = c.execute("SELECT * FROM encargos WHERE tipo = 'puerto' ORDER BY creado DESC LIMIT 500").fetchall()
        out = []
        for f in filas:
            d = dict(f)
            d["datos"] = json.loads(d["datos"] or "{}")
            d["resultado"] = json.loads(d["resultado"]) if d["resultado"] else None
            d["intentos"] = json.loads(d["intentos"]) if d.get("intentos") else []
            out.append(d)
        return out

    def quitar_encargo(self, id_: str):
        with self._con() as c:
            c.execute("DELETE FROM encargos WHERE id = ?", (id_,))

    # ── ajustes ──────────────────────────────────────────────
    def ajuste(self, clave: str) -> Optional[str]:
        with self._con() as c:
            r = c.execute("SELECT valor FROM ajustes WHERE clave = ?", (clave,)).fetchone()
        return r["valor"] if r else None

    def fijar_ajuste(self, clave: str, valor: str):
        with self._con() as c:
            c.execute("INSERT INTO ajustes (clave, valor) VALUES (?, ?) ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor", (clave, valor))

    # ── datos personales (5.3.0) ─────────────────────────────
    def nivel_datos(self) -> str:
        v = self.ajuste(CLAVE_NIVEL)
        return v if v in NIVELES else NIVEL_NO

    def fijar_nivel_datos(self, nivel: str):
        if nivel not in NIVELES:
            raise ValueError(f"Nivel no válido: {nivel}")
        self.fijar_ajuste(CLAVE_NIVEL, nivel)

    def guardar_datos_personales(self, dispositivo: str, datos: dict):
        """El último envío de contactos, llamadas y SMS del móvil (sustituye al anterior), protegido con DPAPI."""
        nivel = datos.get("nivel") if datos.get("nivel") in NIVELES else NIVEL_RECUENTOS
        cuerpo = self._proteger(json.dumps(datos, ensure_ascii=False).encode("utf-8"))
        with self._con() as c:
            c.execute("""INSERT INTO datos_personales (dispositivo, recibido, nivel, datos) VALUES (?,?,?,?)
                         ON CONFLICT(dispositivo) DO UPDATE SET recibido = excluded.recibido, nivel = excluded.nivel, datos = excluded.datos""",
                      (dispositivo, ahora_ms(), nivel, cuerpo))

    def datos_personales(self, dispositivo: str) -> Optional[dict]:
        with self._con() as c:
            r = c.execute("SELECT recibido, datos FROM datos_personales WHERE dispositivo = ?", (dispositivo,)).fetchone()
        if not r:
            return None
        try:
            d = json.loads(self._desproteger(bytes(r["datos"])).decode("utf-8"))
        except Exception:
            return None
        d["recibido"] = r["recibido"]
        return d

    # ── copias de apps (4.2.7) ───────────────────────────────
    def copias_auto(self, dispositivo: str) -> bool:
        with self._con() as c:
            r = c.execute("SELECT copias_auto FROM dispositivos WHERE id = ?", (dispositivo,)).fetchone()
        return bool(r and r["copias_auto"])

    def fijar_copias_auto(self, dispositivo: str, si: bool):
        with self._con() as c:
            c.execute("UPDATE dispositivos SET copias_auto = ? WHERE id = ?", (1 if si else 0, dispositivo))

    def guardar_copia(self, dispositivo, id_, nombre, tamano, pkg, app, version, creada, contenido, sha=""):
        with self._con() as c:
            c.execute("""INSERT INTO copias (dispositivo, id, nombre, tamano, pkg, app, version, creada, contenido, anunciada, sha)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?)
                         ON CONFLICT(dispositivo, id) DO UPDATE SET anunciada = excluded.anunciada""",
                      (dispositivo, id_, nombre, tamano, pkg, app, version, creada, contenido, ahora_ms(), sha))

    def copia(self, dispositivo: str, id_: str) -> Optional[dict]:
        with self._con() as c:
            r = c.execute("SELECT * FROM copias WHERE dispositivo = ? AND id = ?", (dispositivo, id_)).fetchone()
        return dict(r) if r else None

    def marcar_copia(self, dispositivo: str, id_: str, recibidos: Optional[int] = None, completa: Optional[bool] = None,
                     ruta: Optional[str] = None):
        cambios, valores = [], []
        if recibidos is not None:
            cambios.append("recibidos = ?"); valores.append(recibidos)
        if completa is not None:
            cambios.append("completa = ?"); valores.append(1 if completa else 0)
        if ruta is not None:
            cambios.append("ruta = ?"); valores.append(ruta)
        if not cambios:
            return
        with self._con() as c:
            c.execute(f"UPDATE copias SET {', '.join(cambios)} WHERE dispositivo = ? AND id = ?", (*valores, dispositivo, id_))

    def copias(self, dispositivo: str) -> List[dict]:
        with self._con() as c:
            filas = c.execute("SELECT * FROM copias WHERE dispositivo = ? ORDER BY creada DESC", (dispositivo,)).fetchall()
        return [dict(f) for f in filas]
