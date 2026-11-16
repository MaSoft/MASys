# -*- coding: utf-8 -*-
r"""📧 Correo (4.9.0): la operativa de MACorreo adaptada a MASys. COPIA PROPIA: los cambios no se propagan en
ningún sentido entre MASys y MACorreo (LAUNCHER\SCRIPTS\MACorreo). Sin Qt.

- direcciones: ¿existe esta dirección? (forma, dominio y buzón con RCPT TO, sin DATA: no se envía nada)
- dominio: salud del correo de un dominio (MX, SPF, DMARC, DKIM, MTA-STS, TLS-RPT, BIMI, PTR, SMTP, listas negras)
- informe: informe HTML del dominio
- pst: visor de PST y OST sin Outlook (libpff, LGPL)
- servidores + proveedores: prueba de IMAP, POP3 y SMTP y detección del proveedor
- outlook_cfg: perfiles, reglas, firmas, copia de la configuración y alta de cuentas con un PRF
- ajustes: ajustes e historial en multisys.db
"""
