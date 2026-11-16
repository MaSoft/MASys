# -*- coding: utf-8 -*-
"""5.7.0 — Catálogo de las herramientas de Microsoft 365 / Exchange Online / SharePoint Online.

msys/tools/m365/<Carpeta>/ = COPIA PROPIA de _TEMP\\SCRIPTS_2 (5d223d8): script, guía y plantillas CSV. Trabajan contra
el INQUILINO de Microsoft 365 del cliente (no contra este equipo) y piden iniciar sesión de forma interactiva, así que MASys
no las ejecuta solo: prepara una carpeta de trabajo con las plantillas y abre una consola de PowerShell VISIBLE allí, con
la ayuda y los ejemplos de la herramienta. Las que cambian algo tienen -Simular (y casi todas -TenantId como guarda)."""

import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import List

from msys import config as msys_config

CATEGORIAS = {"M365": "👥 Microsoft 365 (cuentas y correo)", "EXO": "📧 Exchange Online", "SPO": "🗂️ SharePoint Online"}


@dataclass
class Herramienta:
    carpeta: str          # nombre de la carpeta (p. ej. «EXO-MigracionIMAP»)
    script: str           # ruta completa del .ps1
    guia: str             # ruta completa de la guía HTML ('' si no hay)
    resumen: str
    ejemplo: str
    cambia: bool          # tiene -Simular (crea, cambia o envía algo)

    @property
    def categoria(self) -> str:
        return CATEGORIAS.get(self.carpeta.split("-")[0], "Otras")

    @property
    def nombre(self) -> str:
        return os.path.basename(self.script)[:-4]


def carpeta_base() -> str:
    return msys_config.ruta_recurso("msys", "tools", "m365")


def _bloque(texto: str, etiqueta: str) -> str:
    m = re.search(r"\." + etiqueta + r"\s*\n(.*?)(?:\n\s*\.[A-Z]+\b|#>)", texto, re.S)
    return " ".join(m.group(1).split()) if m else ""


def catalogo() -> List[Herramienta]:
    base = carpeta_base()
    res = []
    if not os.path.isdir(base):
        return res
    for carpeta in sorted(os.listdir(base)):
        ruta = os.path.join(base, carpeta)
        scripts = [f for f in os.listdir(ruta) if f.lower().endswith(".ps1")] if os.path.isdir(ruta) else []
        if not scripts:
            continue
        script = os.path.join(ruta, scripts[0])
        with open(script, encoding="utf-8-sig", errors="replace") as f:
            texto = f.read()
        # (Informe-CompartidoExterno usa .SINOPSIS en vez de .SYNOPSIS)
        resumen = _bloque(texto, "SYNOPSIS") or _bloque(texto, "SINOPSIS") or _bloque(texto, "DESCRIPTION") or carpeta
        ejemplo = ""
        m = re.search(r"\.EXAMPLE\s*\n\s*(.+)", texto)
        if m:
            ejemplo = m.group(1).strip()
        guias = [g for g in os.listdir(ruta) if g.startswith("Guia_") and g.endswith(".html")]
        res.append(Herramienta(carpeta, script, os.path.join(ruta, guias[0]) if guias else "", resumen, ejemplo,
                               bool(re.search(r"\[switch\]\$Simular", texto))))
    return res


def carpeta_trabajo(h: Herramienta) -> str:
    """INFORMES\\M365\\<Carpeta> con las plantillas CSV (sin pisar las que ya haya rellenado el técnico)."""
    destino = os.path.join(msys_config.app_base_dir(), "INFORMES", "M365", h.carpeta)
    os.makedirs(destino, exist_ok=True)
    origen = os.path.dirname(h.script)
    for f in os.listdir(origen):
        if f.lower().startswith("plantilla_") and f.lower().endswith(".csv") and not os.path.exists(os.path.join(destino, f)):
            shutil.copy2(os.path.join(origen, f), destino)
    return destino


def orden_ejemplo(h: Herramienta) -> str:
    """El ejemplo de la ayuda, con la ruta real del script en vez de «.\\Script.ps1»."""
    if not h.ejemplo:
        return f"& '{h.script}'"
    return re.sub(r"^\.\\[^\s]+\.ps1", lambda _m: f"& '{h.script}'", h.ejemplo)


def abrir_consola(h: Herramienta) -> str:
    """Consola de PowerShell VISIBLE en la carpeta de trabajo, con la ayuda y los ejemplos. Devuelve la carpeta."""
    trabajo = carpeta_trabajo(h)
    ruta_ps = h.script.replace("'", "''")
    orden = (f"Set-Location -LiteralPath '{trabajo.replace(chr(39), chr(39) * 2)}'; "
             f"$Host.UI.RawUI.WindowTitle = 'MASys · {h.carpeta}'; "
             f"Write-Host 'Herramienta: {h.carpeta}' -ForegroundColor Cyan; "
             f"Write-Host 'Script: {ruta_ps}' -ForegroundColor Cyan; "
             "Write-Host 'Plantillas y resultados en esta carpeta. Prueba primero con -Simular.' -ForegroundColor Yellow; "
             f"Get-Help -Name '{ruta_ps}' -Examples")
    subprocess.Popen(["powershell", "-NoExit", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", orden],
                     cwd=trabajo, creationflags=subprocess.CREATE_NEW_CONSOLE)
    return trabajo
