# Componentes de terceros

| Componente | Dónde | Licencia | Origen |
|---|---|---|---|
| Servidor de RustDesk (hbbs/hbbr), parche de MASys | `SERVIDOR_RUSTDESK/` (`masys.patch`) | AGPL-3.0 (ver `SERVIDOR_RUSTDESK/LICENSE`) | https://github.com/rustdesk/rustdesk-server |
| libpff-python (visor PST/OST) | dependencia de Python | LGPL-3.0-or-later (ver `licencias/libpff`) | https://github.com/libyal/libpff |
| SQLite (consola `sqlite3.exe`) | `msys/tools/win/sqlite3.exe` | Dominio público | https://www.sqlite.org/copyright.html |
| Base de fabricantes de tarjetas de red (OUI) | `msys/datos/oui_ieee.tsv.gz` | Registro público del IEEE | https://standards-oui.ieee.org/ |
| MaPeriT.SW / MaPeriT.OF | `msys/tools/` | MIT (Masoft.es) | https://github.com/MaSoft/MAPerit |

El resto de dependencias de Python (PyQt5, pywin32, psutil, requests, dnspython, cryptography, qrcode, Send2Trash, Pillow, openpyxl) se instalan con `requirements.txt` y no se incluyen en este repositorio. PyQt5 es GPL-3.0 o comercial: si redistribuyes MASys compilado, cumple la licencia de PyQt5.
