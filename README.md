# MASys

Herramienta de técnico IT para Windows: reúne en una sola ventana las tareas de soporte de un equipo (inventario de hardware y sistema, servicios, procesos, red, usuarios, credenciales, seguridad, software, copias de seguridad y reparaciones). 44 secciones en un árbol lateral por categorías, con datos reales del equipo en tablas ordenables y las acciones de soporte de cada sección.

**Software libre y gratuito desarrollado por [Masoft.es](https://masoft.es)** · Licencia [MIT](LICENSE) · [Página de MASys](https://masoft.es/masys.html)

Este repositorio contiene la **versión de escritorio** (5.7.3). La app Android complementaria, MASysMov, no está incluida.

## Qué incluye

- **Sistema, disco, red, usuarios y seguridad, hardware y administración:** inventario, servicios, procesos, programas de inicio, características, firewall, controladores, carpetas compartidas con sus permisos, credenciales, navegadores, cuentas de correo (con visor PST/OST sin Outlook) y más.
- **Soporte contextual:** cada sección ofrece sus acciones de soporte y una base de conocimiento con fichas de problemas habituales que llevan a la sección donde se resuelven.
- **Acceso remoto:** agenda de equipos de clientes por empresa y usuario, conexión con RustDesk, Asistencia rápida y Escritorio remoto, servidor de RustDesk propio y agente de análisis remoto con informes cifrados.
- **Correo a los clientes:** cuentas SMTP, Gmail y Microsoft 365 (Graph), plantillas e historial.
- **Informes y copias:** exportación CSV/HTML/PDF/Excel, instantáneas para comparar y generadores de copias de seguridad.

El detalle completo está en [manual_MASys.html](manual_MASys.html) y [ficha_MASys.html](ficha_MASys.html) (descárgalos y ábrelos en el navegador).

## Requisitos

| | |
|---|---|
| Sistema | Windows 10 u 11 |
| Python | 3.12 (64 bits) con `requirements.txt`, solo para ejecutarlo desde el código |
| Permisos | Administrador para los cambios (servicios, firewall, controladores, permisos de carpetas...); para consultar no hace falta |

## Ejecutar desde el código

```powershell
py -3.12 -m pip install -r requirements.txt
py -3.12 multisys.py
```

## Compilar

La carpeta `INSTALL` contiene la compilación con PyInstaller (instalador Inno Setup, versión portable y ejecutable único): `INSTALL\build_install.bat`. La versión sale de `msys\version.py`.

El servidor de RustDesk (`SERVIDOR_RUSTDESK`) se compila aparte con `compilar_servidor.bat`; ver [SERVIDOR_RUSTDESK/FUENTE.txt](SERVIDOR_RUSTDESK/FUENTE.txt).

## Pruebas

```powershell
py -3.12 -m pytest tests -q
```

## Componentes de terceros

Ver [TERCEROS.md](TERCEROS.md). El servidor de RustDesk es AGPL-3.0 y conserva esa licencia.

## Aviso legal

MASys es una herramienta de técnico: úsala solo en equipos propios o sobre los que tengas autorización. Algunas acciones (servicios, firewall, controladores, permisos, limpiezas) cambian el sistema; MASys envía a la Papelera lo que borra, pero revisa cada acción antes de aplicarla.

## Licencia

Código publicado bajo licencia [MIT](LICENSE): puedes usarlo, modificarlo y redistribuirlo manteniendo el aviso de copyright. Se entrega «tal cual», sin garantía de ningún tipo. Los componentes de terceros conservan sus propias licencias.

© 2026 Masoft.es
