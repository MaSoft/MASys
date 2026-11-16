# -*- coding: utf-8 -*-
"""Registro de auditoría único para todas las acciones de reparación de
MASys, sea cual sea la pestaña/sección que las ejecute. Antes de esto,
cada sección tenía como mucho sus propias "notas" sueltas (EnvVarNotesDB,
ProcessNotesDB, ServiceNotesDB) — pensadas para anotaciones manuales del
técnico, no para dejar constancia automática de qué cambió el propio
programa. Con MASys evolucionando a herramienta de "técnico in situ"
que no solo audita sino que repara, hace falta un historial centralizado:
qué se intentó, cuándo, sobre qué, con qué resultado — para poder mostrar
o exportar "qué se hizo en este equipo hoy" sin ir pestaña por pestaña."""

import os
from datetime import datetime
from typing import List, Tuple

from msys import config as msys_config
from msys.core.utils import _sqlite_connect


class RepairAuditDB:
    """Historial de acciones de reparación, persistido en multisys.db
    (misma base que el resto de NotesDB de la aplicación)."""

    def __init__(self):
        base_dir = msys_config.app_base_dir()
        self.path = os.path.join(base_dir, "multisys.db")
        self._init_schema()

    def _conn(self):
        return _sqlite_connect(self.path)

    def _init_schema(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS historial_reparaciones (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    seccion TEXT NOT NULL,
                    accion TEXT NOT NULL,
                    objetivo TEXT NOT NULL,
                    exito INTEGER NOT NULL,
                    detalle TEXT NOT NULL DEFAULT '',
                    backup_realizado INTEGER NOT NULL DEFAULT 0,
                    backup_ruta TEXT NOT NULL DEFAULT '',
                    usuario TEXT NOT NULL DEFAULT ''
                )
            """)
            # Migraciones aditivas: bases de datos creadas antes de que
            # existieran estas columnas no las tienen todavía.
            columnas = {fila[1] for fila in conn.execute("PRAGMA table_info(historial_reparaciones)")}
            if "usuario" not in columnas:
                conn.execute("ALTER TABLE historial_reparaciones ADD COLUMN usuario TEXT NOT NULL DEFAULT ''")
            if "tipo_operacion" not in columnas:
                # Default 'BACKUP': todas las filas existentes de la sección
                # de Copias de Seguridad, hasta ahora, eran copias (no había
                # forma de restaurar todavía) — ver backup_restore_dialog.py.
                # El resto de secciones de la app (35+ llamadas a registrar()
                # fuera de Copias de Seguridad) nunca leen esta columna, así
                # que el valor por defecto ahí es irrelevante.
                conn.execute("ALTER TABLE historial_reparaciones ADD COLUMN tipo_operacion TEXT NOT NULL DEFAULT 'BACKUP'")

    def registrar(self, seccion: str, accion: str, objetivo: str, exito: bool,
                  detalle: str = "", backup_realizado: bool = False,
                  backup_ruta: str = "", usuario: str = None,
                  tipo_operacion: str = "BACKUP") -> None:
        """Añade una entrada al historial. Se llama SIEMPRE tras intentar
        una reparación, tanto si tuvo éxito como si falló — el historial
        debe reflejar lo que se intentó, no solo lo que funcionó.

        usuario: por defecto el usuario de Windows que tiene abierto
        MASys ahora mismo (quien realmente ejecutó la acción), no hace
        falta pasarlo salvo que el llamador ya conozca uno más preciso.

        tipo_operacion: solo tiene sentido en la sección 📀 Copias de
        Seguridad ('BACKUP' o 'RESTORE', ver backup_restore_dialog.py); el
        resto de secciones lo dejan en su valor por defecto sin que importe."""
        if usuario is None:
            usuario = os.environ.get("USERNAME", "")
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO historial_reparaciones "
                "(timestamp, seccion, accion, objetivo, exito, detalle, backup_realizado, backup_ruta, usuario, tipo_operacion) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (datetime.now().isoformat(timespec='seconds'), seccion, accion, objetivo,
                 1 if exito else 0, detalle, 1 if backup_realizado else 0, backup_ruta, usuario, tipo_operacion),
            )

    def obtener_historial(self, limite: int = 500) -> List[Tuple]:
        """Devuelve las entradas más recientes primero: (timestamp, seccion,
        accion, objetivo, exito, detalle, backup_realizado, backup_ruta,
        usuario)."""
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT timestamp, seccion, accion, objetivo, exito, detalle, "
                "backup_realizado, backup_ruta, usuario FROM historial_reparaciones "
                "ORDER BY id DESC LIMIT ?",
                (limite,),
            )
            return cur.fetchall()

    def obtener_historial_seccion(self, seccion: str, limite: int = 1000) -> List[Tuple]:
        """Como obtener_historial() pero filtrado a una sola sección, para
        listados especializados (p. ej. 📀 Historial de copias de
        seguridad): (timestamp, accion, usuario, objetivo, exito, tipo_operacion)."""
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT timestamp, accion, usuario, objetivo, exito, tipo_operacion FROM historial_reparaciones "
                "WHERE seccion = ? ORDER BY id DESC LIMIT ?",
                (seccion, limite),
            )
            return cur.fetchall()

    def limpiar_historial(self) -> None:
        """Vacía el historial. Acción explícita del usuario únicamente —
        nunca se llama automáticamente."""
        with self._conn() as conn:
            conn.execute("DELETE FROM historial_reparaciones")
