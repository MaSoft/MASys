# -*- coding: utf-8 -*-
"""Ajustes e historial del correo (4.9.0) en multisys.db, como el resto de MASys. Nunca se guardan contraseñas.

Ajustes (clave → texto): pausa (s entre dos preguntas al mismo servidor), espera (s), helo, remitente,
temporales_extra (dominios, uno por línea), thunderbird ("1"/"0"), buzon ("1"/"0") y las últimas carpetas usadas.
Historial: comprobaciones sueltas de direcciones («direccion») y de dominios («dominio»), con su resultado en JSON."""
import json
import os
import socket
import time
from typing import List, Optional

from msys import config as msys_config
from msys.core.utils import _sqlite_connect
from msys.domain.correo import direcciones as dirs

HISTORIAL_MAX = 500   # por tipo; lo más antiguo se descarta
POR_DEFECTO = {"pausa": "3", "espera": "12", "helo": "", "remitente": "", "temporales_extra": "", "thunderbird": "1",
               "buzon": "1"}


class CorreoDB:
    def __init__(self, path: Optional[str] = None):
        self.path = path or os.path.join(msys_config.app_base_dir(), "multisys.db")
        with self._conn() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS correo_ajustes (clave TEXT PRIMARY KEY, valor TEXT)")
            conn.execute("""CREATE TABLE IF NOT EXISTS correo_historial (
                                id INTEGER PRIMARY KEY AUTOINCREMENT, tipo TEXT NOT NULL, objetivo TEXT NOT NULL,
                                resumen TEXT NOT NULL, datos TEXT NOT NULL, ts INTEGER NOT NULL)""")
            conn.execute("CREATE INDEX IF NOT EXISTS correo_historial_tipo ON correo_historial(tipo, ts)")

    def _conn(self):
        return _sqlite_connect(self.path)

    # ── ajustes ─────────────────────────────────────────────────────────────
    def ajuste(self, clave: str, defecto: Optional[str] = None) -> str:
        with self._conn() as conn:
            fila = conn.execute("SELECT valor FROM correo_ajustes WHERE clave = ?", (clave,)).fetchone()
        if fila is not None and fila[0] is not None:
            return fila[0]
        return POR_DEFECTO.get(clave, "") if defecto is None else defecto

    def guardar_ajuste(self, clave: str, valor) -> None:
        with self._conn() as conn:
            conn.execute("INSERT OR REPLACE INTO correo_ajustes (clave, valor) VALUES (?, ?)", (clave, str(valor)))

    def numero(self, clave: str) -> float:
        try:
            return float(self.ajuste(clave))
        except ValueError:
            return float(POR_DEFECTO[clave])

    def si(self, clave: str) -> bool:
        return self.ajuste(clave) == "1"

    def comprobador(self, preguntar_buzon: bool) -> dirs.Checker:
        extra = {x.strip().lower() for x in self.ajuste("temporales_extra").replace(",", "\n").splitlines() if x.strip()}
        return dirs.Checker(probe_mailbox=preguntar_buzon, helo=self.ajuste("helo") or socket.getfqdn(),
                            mail_from=self.ajuste("remitente"), timeout=self.numero("espera"), extra_disposable=extra)

    # ── historial ───────────────────────────────────────────────────────────
    def anadir_historial(self, tipo: str, objetivo: str, resumen: str, datos) -> int:
        with self._conn() as conn:
            cur = conn.execute("INSERT INTO correo_historial (tipo, objetivo, resumen, datos, ts) VALUES (?, ?, ?, ?, ?)",
                               (tipo, objetivo, resumen, json.dumps(datos, ensure_ascii=False), int(time.time() * 1000)))
            conn.execute("""DELETE FROM correo_historial WHERE tipo = ? AND id NOT IN
                            (SELECT id FROM correo_historial WHERE tipo = ? ORDER BY ts DESC LIMIT ?)""",
                         (tipo, tipo, HISTORIAL_MAX))
            return cur.lastrowid

    def historial(self, tipo: str, limite: int = 200) -> List[dict]:
        with self._conn() as conn:
            filas = conn.execute("""SELECT id, objetivo, resumen, datos, ts FROM correo_historial WHERE tipo = ?
                                    ORDER BY ts DESC LIMIT ?""", (tipo, limite)).fetchall()
        out = []
        for id_, objetivo, resumen, datos, ts in filas:
            try:
                d = json.loads(datos)
            except ValueError:
                d = None
            out.append({"id": id_, "objetivo": objetivo, "resumen": resumen, "datos": d, "ts": ts})
        return out

    def quitar_historial(self, ids: List[int]) -> None:
        if ids:
            with self._conn() as conn:
                conn.executemany("DELETE FROM correo_historial WHERE id = ?", [(i,) for i in ids])
