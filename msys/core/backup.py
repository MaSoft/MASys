# -*- coding: utf-8 -*-
"""Backup genérico previo a una reparación. Complementa los backups ya
específicos que existen (exportar una rama de registro en Registro de
Windows, exportar HKCU/HKLM Environment en VarManager.export_reg_backup):
esto cubre el caso más simple y más común de "voy a sobrescribir/borrar un
fichero de configuración real, guarda una copia primero".

Regla de diseño para cualquier acción de reparación nueva: si lo que se va
a modificar vive en un fichero, pasa por respaldar_archivo() antes de
escribir. Si vive en el registro o en la config de un servicio, usa el
mecanismo de backup específico de esa sección (ya establecido) — pero
registra el resultado igual en RepairAuditDB."""

import os
import shutil
from datetime import datetime
from typing import Tuple

from msys.core.utils import get_informes_folder


def respaldar_archivo(ruta: str) -> Tuple[bool, str]:
    """Copia 'ruta' a INFORMES\\BACKUPS_REPARACION\\ con timestamp antes de
    modificarla. Devuelve (éxito, ruta_del_backup_o_mensaje_de_error).
    Si 'ruta' no existe (p. ej. se va a CREAR un fichero nuevo, no
    sobrescribir uno existente), no es un error: no hay nada que
    respaldar, se devuelve (True, "") para no bloquear la reparación."""
    if not ruta or not os.path.isfile(ruta):
        return True, ""

    try:
        carpeta_backups = os.path.join(get_informes_folder(), "BACKUPS_REPARACION")
        os.makedirs(carpeta_backups, exist_ok=True)

        nombre = os.path.basename(ruta)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        destino = os.path.join(carpeta_backups, f"{timestamp}_{nombre}")

        shutil.copy2(ruta, destino)
        return True, destino
    except Exception as e:
        return False, str(e)
