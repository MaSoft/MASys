# -*- coding: utf-8 -*-
"""Empresas y usuarios de los clientes (4.13.0): catálogo común en multisys.db.

- Una empresa tiene varios usuarios (nombre, correo, teléfono). Empresa y nombre de usuario son obligatorios.
- Es un catálogo para ELEGIR: quien lo usa (agenda de Acceso remoto, correos…) guarda su propia copia de los datos,
  así que lo escrito a mano no se pierde si no se añade aquí, y borrar del catálogo no borra nada en otros sitios.
- Nombres únicos sin distinguir mayúsculas NI en las letras con tilde (columna «clave» = casefold de Python:
  el NOCASE de SQLite solo pliega ASCII y trataría «ASESORÍA» y «Asesoría» como distintas). Usuario: por empresa.

Sin Qt: la ventana está en msys/ui/clientes_dialog.py."""

import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from msys import config as msys_config
from msys.core.utils import _sqlite_connect

_RE_CORREO = re.compile(r"^[^@\s<>\"',;]+@[^@\s<>\"',;]+\.[^@\s<>\"',;]{2,}$")
_RE_TELEFONO = re.compile(r"^\+?[0-9][0-9 ().\-]{5,24}$")


@dataclass
class Empresa:
    id: int
    nombre: str
    notas: str = ""


@dataclass
class Usuario:
    id: int
    empresa_id: int
    nombre: str
    correo: str = ""
    telefono: str = ""
    notas: str = ""
    empresa: str = ""          # nombre de la empresa (solo lectura, para enseñarlo)


def limpiar(texto: str) -> str:
    return re.sub(r"\s+", " ", (texto or "").strip())


def clave(texto: str) -> str:
    return limpiar(texto).casefold()


def validar_correo(texto: str) -> str:
    """"" o el correo sin espacios; ValueError si no lo parece."""
    t = (texto or "").strip()
    if t and not _RE_CORREO.match(t):
        raise ValueError(f"«{texto}» no es un correo válido")
    return t


def validar_telefono(texto: str) -> str:
    t = limpiar(texto)
    if t and not _RE_TELEFONO.match(t):
        raise ValueError(f"«{texto}» no es un teléfono válido (números, espacios, + al principio)")
    return t


def errores_usuario(empresa: str, nombre: str, correo: str = "", telefono: str = "") -> List[str]:
    errores = []
    if not limpiar(empresa):
        errores.append("Falta la empresa")
    if not limpiar(nombre):
        errores.append("Falta el usuario")
    for validar, valor in ((validar_correo, correo), (validar_telefono, telefono)):
        try:
            validar(valor)
        except ValueError as e:
            errores.append(str(e))
    return errores


class ClientesDB:
    def __init__(self, path: Optional[str] = None):
        self.path = path or os.path.join(msys_config.app_base_dir(), "multisys.db")
        with self._conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS empresas (
                id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL, clave TEXT NOT NULL UNIQUE,
                notas TEXT NOT NULL DEFAULT '', creado TEXT NOT NULL, modificado TEXT NOT NULL)""")
            c.execute("""CREATE TABLE IF NOT EXISTS empresa_usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT, empresa_id INTEGER NOT NULL, nombre TEXT NOT NULL,
                clave TEXT NOT NULL, correo TEXT NOT NULL DEFAULT '', telefono TEXT NOT NULL DEFAULT '',
                notas TEXT NOT NULL DEFAULT '', creado TEXT NOT NULL, modificado TEXT NOT NULL,
                UNIQUE (empresa_id, clave))""")

    def _conn(self):
        return _sqlite_connect(self.path)

    @staticmethod
    def _ahora() -> str:
        return datetime.now().isoformat(timespec="seconds")

    # ── Empresas ────────────────────────────────────────────────────────
    def empresas(self) -> List[Empresa]:
        with self._conn() as c:
            return [Empresa(*f) for f in c.execute(
                "SELECT id, nombre, notas FROM empresas ORDER BY nombre COLLATE NOCASE")]

    def empresa(self, nombre: str) -> Optional[Empresa]:
        with self._conn() as c:
            f = c.execute("SELECT id, nombre, notas FROM empresas WHERE clave = ?", (clave(nombre),)).fetchone()
        return Empresa(*f) if f else None

    def guardar_empresa(self, nombre: str, notas: str = "", empresa_id: Optional[int] = None) -> Empresa:
        """Alta (sin id) o cambio de nombre/notas. ValueError si falta o ya hay otra con ese nombre."""
        nombre = limpiar(nombre)
        if not nombre:
            raise ValueError("Falta el nombre de la empresa")
        otra = self.empresa(nombre)
        if otra is not None and otra.id != empresa_id:
            raise ValueError(f"Ya hay una empresa «{otra.nombre}»")
        ahora = self._ahora()
        with self._conn() as c:
            if empresa_id is None:
                cur = c.execute("INSERT INTO empresas (nombre, clave, notas, creado, modificado) VALUES (?, ?, ?, ?, ?)",
                                (nombre, clave(nombre), notas.strip(), ahora, ahora))
                empresa_id = cur.lastrowid
            else:
                c.execute("UPDATE empresas SET nombre = ?, clave = ?, notas = ?, modificado = ? WHERE id = ?",
                          (nombre, clave(nombre), notas.strip(), ahora, empresa_id))
        return Empresa(empresa_id, nombre, notas.strip())

    def eliminar_empresa(self, empresa_id: int) -> None:
        """Quita la empresa y sus usuarios del catálogo (los equipos de la agenda conservan su copia)."""
        with self._conn() as c:
            c.execute("DELETE FROM empresa_usuarios WHERE empresa_id = ?", (empresa_id,))
            c.execute("DELETE FROM empresas WHERE id = ?", (empresa_id,))

    # ── Usuarios ────────────────────────────────────────────────────────
    def usuarios(self, empresa_id: Optional[int] = None) -> List[Usuario]:
        sql = """SELECT u.id, u.empresa_id, u.nombre, u.correo, u.telefono, u.notas, e.nombre
                 FROM empresa_usuarios u JOIN empresas e ON e.id = u.empresa_id"""
        args = ()
        if empresa_id is not None:
            sql += " WHERE u.empresa_id = ?"
            args = (empresa_id,)
        sql += " ORDER BY e.nombre COLLATE NOCASE, u.nombre COLLATE NOCASE"
        with self._conn() as c:
            return [Usuario(*f) for f in c.execute(sql, args)]

    def usuario(self, empresa: str, nombre: str) -> Optional[Usuario]:
        e = self.empresa(empresa)
        if e is None:
            return None
        with self._conn() as c:
            f = c.execute("""SELECT id, empresa_id, nombre, correo, telefono, notas FROM empresa_usuarios
                             WHERE empresa_id = ? AND clave = ?""", (e.id, clave(nombre))).fetchone()
        return Usuario(*f, empresa=e.nombre) if f else None

    def guardar_usuario(self, u: Usuario) -> Usuario:
        """Alta (id 0) o cambio. ValueError con lo que falte o esté mal, o si ya hay otro igual en la empresa."""
        nombre = limpiar(u.nombre)
        correo, telefono = validar_correo(u.correo), validar_telefono(u.telefono)
        if not nombre:
            raise ValueError("Falta el nombre del usuario")
        with self._conn() as c:
            fila = c.execute("SELECT nombre FROM empresas WHERE id = ?", (u.empresa_id,)).fetchone()
            if not fila:
                raise ValueError("La empresa no existe")
            otro = c.execute("SELECT id FROM empresa_usuarios WHERE empresa_id = ? AND clave = ?",
                             (u.empresa_id, clave(nombre))).fetchone()
            if otro and otro[0] != u.id:
                raise ValueError(f"«{nombre}» ya está en {fila[0]}")
            ahora = self._ahora()
            if not u.id:
                cur = c.execute("""INSERT INTO empresa_usuarios (empresa_id, nombre, clave, correo, telefono, notas,
                                   creado, modificado) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                                (u.empresa_id, nombre, clave(nombre), correo, telefono, u.notas.strip(), ahora, ahora))
                nuevo_id = cur.lastrowid
            else:
                c.execute("""UPDATE empresa_usuarios SET empresa_id = ?, nombre = ?, clave = ?, correo = ?, telefono = ?,
                             notas = ?, modificado = ? WHERE id = ?""",
                          (u.empresa_id, nombre, clave(nombre), correo, telefono, u.notas.strip(), ahora, u.id))
                nuevo_id = u.id
        return Usuario(nuevo_id, u.empresa_id, nombre, correo, telefono, u.notas.strip(), fila[0])

    def eliminar_usuario(self, usuario_id: int) -> None:
        with self._conn() as c:
            c.execute("DELETE FROM empresa_usuarios WHERE id = ?", (usuario_id,))

    def asegurar(self, empresa: str, nombre: str, correo: str = "", telefono: str = "") -> Usuario:
        """Empresa y usuario en el catálogo (los crea si faltan; si existe el usuario, actualiza correo/teléfono
        solo con lo que venga relleno)."""
        e = self.empresa(empresa) or self.guardar_empresa(empresa)
        u = self.usuario(e.nombre, nombre)
        if u is None:
            return self.guardar_usuario(Usuario(0, e.id, nombre, correo, telefono))
        u.correo = correo.strip() or u.correo
        u.telefono = telefono.strip() or u.telefono
        return self.guardar_usuario(u)
