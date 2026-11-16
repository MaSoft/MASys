#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MASys - empaqueta la version portable (sin instalador).

Reutiliza la MISMA salida de PyInstaller que el instalador (INSTALL/dist/MASys),
asi que no hace falta un segundo build: copia esa carpeta, le anade SCRIPTS\\
(catalogo de scripts de ejemplo), el manual, la ficha y un LEEME, y genera el .zip.
Se invoca desde build_install.bat despues del paso de PyInstaller.

La version portable no escribe nada fuera de su propia carpeta: msys/config.py
app_base_dir() devuelve la carpeta del propio .exe, y ahi viven multisys.db,
movil.db, buscacompartidos_config.json, INFORMES\\, BACKUPS\\ y SCRIPTS\\.
"""

from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DIST = RAIZ / "INSTALL" / "dist"
ORIGEN = DIST / "MASys"

LEEME_PORTABLE = """MASys - version portable
=======================

No requiere instalacion. Copia esta carpeta completa donde quieras (USB,
carpeta de red, otro equipo) y ejecuta MASys.exe con doble clic. Windows
pedira permiso de administrador: casi todas las secciones lo necesitan.

Todo lo que guarda MASys vive DENTRO de esta misma carpeta, nunca en
%LOCALAPPDATA%:
  - multisys.db                   (historial, perfiles, reparaciones)
  - movil.db                      (moviles enlazados, protegido con DPAPI)
  - buscacompartidos_config.json  (secciones visibles y ajustes)
  - INFORMES\\, BACKUPS\\           (informes y copias de configuracion)
  - SCRIPTS\\                      (catalogo de scripts propios)
  - SERVIDOR_RUSTDESK\\datos\\       (servidor de RustDesk: su clave privada y equipos)

movil.db esta protegido con la cuenta de Windows que lo creo: en otro
equipo o con otro usuario hay que volver a enlazar los moviles.
Los informes de INFORMES\\ contienen datos reales del equipo: no dejes
la carpeta en sitios compartidos.

Manual: manual_MASys.html (incluido en esta misma carpeta)

Desarrollado por Masoft.es.
"""


def leer_version() -> str:
    texto = (RAIZ / "INSTALL" / "setup.iss").read_text(encoding="utf-8-sig")
    m = re.search(r'#define MyAppVersion\s+"([^"]+)"', texto)
    if not m:
        raise RuntimeError("No se pudo leer MyAppVersion de INSTALL/setup.iss")
    return m.group(1)


def main() -> None:
    if not (ORIGEN / "MASys.exe").exists():
        print(f"[ERROR] No existe {ORIGEN / 'MASys.exe'}. Ejecuta PyInstaller antes que este script.")
        sys.exit(1)

    version = leer_version()
    destino = DIST / f"MASys_Portable_v{version}"
    if destino.exists():
        shutil.rmtree(destino)

    shutil.copytree(ORIGEN, destino)
    shutil.copytree(RAIZ / "SCRIPTS", destino / "SCRIPTS",
                    ignore=shutil.ignore_patterns("*.bak"))
    shutil.copy2(RAIZ / "manual_MASys.html", destino / "manual_MASys.html")
    # Licencia LGPL de libpff (visor PST/OST, 4.9.0): tiene que ir con el programa
    shutil.copytree(RAIZ / "licencias", destino / "licencias")
    ficha = RAIZ / "ficha_MASys.html"
    if ficha.exists():
        shutil.copy2(ficha, destino / "ficha_MASys.html")
    agente = DIST / f"MASys_Agente_v{version}.exe"
    if agente.exists():
        (destino / "AGENTE").mkdir(exist_ok=True)
        shutil.copy2(agente, destino / "AGENTE" / agente.name)
    # Servidor de RustDesk (4.12.0): programas, licencia AGPL, origen y parche; nunca datos\ ni fuente\
    servidor = RAIZ / "SERVIDOR_RUSTDESK"
    (destino / "SERVIDOR_RUSTDESK" / "bin").mkdir(parents=True, exist_ok=True)
    for programa in ("hbbs.exe", "hbbr.exe"):
        shutil.copy2(servidor / "bin" / programa, destino / "SERVIDOR_RUSTDESK" / "bin" / programa)
    for archivo in ("LICENSE", "FUENTE.txt", "masys.patch"):
        shutil.copy2(servidor / archivo, destino / "SERVIDOR_RUSTDESK" / archivo)
    (destino / "LEEME_PORTABLE.txt").write_text(LEEME_PORTABLE, encoding="utf-8")

    zip_creado = shutil.make_archive(str(destino), "zip", root_dir=destino)

    print(f"[OK]   Portable generado: {destino}")
    print(f"[OK]   Zip generado: {zip_creado}")


if __name__ == "__main__":
    main()
