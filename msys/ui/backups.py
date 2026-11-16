# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Copias de Seguridad.

NO es una sección del árbol de navegación: es una operativa de acción
independiente, sin datos que consultar/exportar como el resto de
secciones (por eso vive detrás de un botón dedicado bajo el árbol —
abrir_copias_seguridad — y no de un nodo seleccionable, ver multisys.py).
Solo la operativa de generación real: tarjetas de acción, una por
categoría, cada una abre un BackupGeneratorDialog (msys/ui/
backup_generator_dialog.py) con un checklist personalizable — esta
ventana hace copias reales, no consulta nada.

Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).
Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QFrame, QScrollArea, QDialog, QInputDialog, QMessageBox,
)

from msys.ui.backup_generator_dialog import BackupGeneratorDialog, BackupComponente
from msys.ui.forensic_capture_dialog import ForensicCaptureDialog
from msys.ui.backup_history_dialog import BackupHistoryDialog
from msys.ui.backup_restore_dialog import BackupRestoreDialog
from msys.backup_generators import (
    COMPONENTES_REGISTRO, RegistryBackupThread, RegistryRestoreThread,
    COMPONENTES_CREDENCIALES, CredentialsBackupThread,
    componentes_navegadores, BrowserBackupThread,
    COMPONENTES_SISTEMA, SystemConfigBackupThread,
    COMPONENTES_CORREO, CorreoBackupThread,
    componentes_perfil_usuario, UserProfileBackupThread, UserProfileRestoreThread,
    COMPONENTES_NUBE, NubeBackupThread,
    COMPONENTES_MASYS, MASysConfigBackupThread, MASysConfigRestoreThread,
    sqlcmd_disponible, servidor_sql_local, listar_bases_datos_sql, SqlBackupThread,
)
from msys.collectors import detectar_sql_server

_SECCION_AUDITORIA_BACKUPS = "📀 Copias de Seguridad"

# (icono, título, descripción, disponible) — BitLocker y Licencias se dejan
# fuera a propósito: ya tienen su propia categoría/pestaña en MASys.
_CATALOGO_TARJETAS = [
    ("🗂️", "Registro de Windows",
     "HKLM\\SOFTWARE, HKLM\\SYSTEM y HKCU — configuración de programas, sistema y usuario.", True),
    ("🔑", "Credenciales y accesos",
     "Credential Manager (con sus claves DPAPI), perfiles WiFi con clave en claro, SSH, Git.", True),
    ("🌐", "Navegadores",
     "Marcadores, contraseñas, historial, cookies y sesiones — un perfil detectado a la vez.", True),
    ("⚙️", "Configuración del sistema",
     "Firewall, tareas programadas, servicios, variables de entorno, red, impresoras, directiva local, certificados.", True),
    ("📧", "Correo",
     "Configuración de cuentas Outlook y firmas (PST/OST masivo: usa 'Exportar a PST' en Cuentas de Correo).", True),
    ("👤", "Perfil de usuario",
     "Escritorio, Documentos, Imágenes, Vídeos, Música, Descargas, AppData\\Roaming.", True),
    ("☁️", "Nube",
     "Cuentas y carpetas vinculadas de OneDrive (resumen — los archivos ya los sincroniza OneDrive).", True),
    ("🔍", "Forense / auditoría",
     "Captura forense completa (MAPERIT), en caliente sobre este equipo o offline sobre un disco "
     "conectado sin arrancar — tú eliges el modo — consola propia elevada.", True),
    ("🧩", "Configuración de MASys",
     "Ajustes propios del programa: lista negra, vínculos winget, avisos validados, catálogo de scripts.", True),
    ("🗄️", "Bases de datos SQL Server",
     "BACKUP DATABASE (.bak) de las bases de datos de una instancia local, vía sqlcmd con "
     "autenticación de Windows. Requiere sqlcmd.exe instalado — sin restauración automática "
     "(RESTORE es tarea de un DBA en el momento, no un botón).", True),
]


class BackupsMixin:
    def abrir_copias_seguridad(self):
        """Punto de entrada del botón dedicado bajo el árbol (multisys.py) —
        ventana propia e independiente, mismo patrón que abrir_snapshots()/
        abrir_analisis_seguridad() (ReportingMixin): un QDialog que no pasa
        por content_stack ni por _current_section."""
        dlg = QDialog(self)
        dlg.setWindowTitle("💿 Copias de Seguridad")
        dlg.setWindowFlags(dlg.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.crear_tab_backups())

        dlg.resize(1200, 800)
        dlg.exec_()

    def _ver_historial_backups(self):
        BackupHistoryDialog(self).exec_()

    def crear_tab_backups(self):
        """Construye el contenido de Copias de Seguridad. Se llama de
        nuevo (widget fresco) cada vez que se abre la ventana — no se
        mantiene una instancia persistente porque no es una sección."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        c_cabecera = self._dash_colors() if hasattr(self, "_dash_colors") else {"title": "#1a2634"}
        cabecera_generar = QHBoxLayout()
        col_titulo = QVBoxLayout()
        col_titulo.setSpacing(2)
        lbl_generar_titulo = QLabel("💾 Generar copia de seguridad")
        lbl_generar_titulo.setStyleSheet(
            f"font-size: 12pt; font-weight: 600; color: {c_cabecera['title']};")
        col_titulo.addWidget(lbl_generar_titulo)
        lbl_generar_sub = QLabel(
            "Cada tarjeta hace una copia REAL (no una comprobación). Por defecto se guarda en "
            "BACKUPS\\<categoría>\\<fecha_hora> dentro de la carpeta de MASys — puedes elegir "
            "otra carpeta al iniciar cada copia. Tú decides qué incluir en cada una.")
        lbl_generar_sub.setObjectName("hintLabel")
        lbl_generar_sub.setWordWrap(True)
        col_titulo.addWidget(lbl_generar_sub)
        cabecera_generar.addLayout(col_titulo, 1)

        btn_historial = QPushButton("📋 Historial")
        btn_historial.setToolTip("Ver copias de seguridad ya generadas: fecha, categoría, usuario y destino")
        btn_historial.clicked.connect(self._ver_historial_backups)
        cabecera_generar.addWidget(btn_historial, 0, Qt.AlignTop)
        layout.addLayout(cabecera_generar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        contenedor = QWidget()
        self._backup_cards_grid = QGridLayout(contenedor)
        self._backup_cards_grid.setSpacing(10)
        scroll.setWidget(contenedor)
        layout.addWidget(scroll, 1)

        self._construir_tarjetas_backup()

        return tab

    # ── Sección 2: tarjetas de generadores ──────────────────────────────

    def _construir_tarjetas_backup(self, columnas: int = 3):
        callbacks = {
            "Registro de Windows": self._generar_backup_registro,
            "Credenciales y accesos": self._generar_backup_credenciales,
            "Navegadores": self._generar_backup_navegadores,
            "Configuración del sistema": self._generar_backup_sistema,
            "Correo": self._generar_backup_correo,
            "Perfil de usuario": self._generar_backup_perfil_usuario,
            "Nube": self._generar_backup_nube,
            "Forense / auditoría": self._abrir_captura_forense,
            "Configuración de MASys": self._generar_backup_masys,
            "Bases de datos SQL Server": self._generar_backup_sql,
        }
        # Forense/auditoría se queda fuera a propósito: es una captura
        # pericial de un instante concreto (evidencia), no un ajuste de
        # configuración con un "estado anterior" al que volver — "restaurar"
        # no tiene sentido conceptual ahí.
        callbacks_restaurar = {
            "Registro de Windows": self._restaurar_backup_registro,
            "Credenciales y accesos": self._restaurar_backup_credenciales,
            "Navegadores": self._restaurar_backup_navegadores,
            "Configuración del sistema": self._restaurar_backup_sistema,
            "Correo": self._restaurar_backup_correo,
            "Perfil de usuario": self._restaurar_backup_perfil_usuario,
            "Nube": self._restaurar_backup_nube,
            "Configuración de MASys": self._restaurar_backup_masys,
        }
        for i, (icono, titulo, descripcion, disponible) in enumerate(_CATALOGO_TARJETAS):
            tarjeta = self._crear_tarjeta_backup(
                icono, titulo, descripcion, disponible,
                callbacks.get(titulo), callbacks_restaurar.get(titulo))
            self._backup_cards_grid.addWidget(tarjeta, i // columnas, i % columnas)

    def _crear_tarjeta_backup(self, icono, titulo, descripcion, disponible, callback, callback_restaurar=None):
        c = self._dash_colors() if hasattr(self, "_dash_colors") else {
            "title": "#1a2634", "muted": "#8a94a6",
            "alerts_bg": "#f7f9fc", "alerts_border": "#e3e7ee",
        }
        accent = "#2979FF" if disponible else (c.get("alerts_border") or "#dde3ea")

        card = QFrame()
        card.setObjectName("backupCard")
        card.setStyleSheet(
            f"QFrame#backupCard {{ background: {c['alerts_bg']}; border: 1px solid {c['alerts_border']}; "
            f"border-top: 3px solid {accent}; border-radius: 8px; }}")
        card.setMinimumHeight(150)

        v = QVBoxLayout(card)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(6)

        fila_icono = QHBoxLayout()
        lbl_icono = QLabel(icono)
        lbl_icono.setStyleSheet("font-size: 20pt; background: transparent; border: none;")
        fila_icono.addWidget(lbl_icono)
        fila_icono.addStretch()
        if not disponible:
            lbl_proximamente = QLabel("Próximamente")
            lbl_proximamente.setStyleSheet(
                f"color: {c['muted']}; font-size: 8pt; font-weight: 600; background: transparent; border: none;")
            fila_icono.addWidget(lbl_proximamente)
        v.addLayout(fila_icono)

        lbl_titulo = QLabel(titulo)
        lbl_titulo.setWordWrap(True)
        lbl_titulo.setStyleSheet(
            f"font-size: 11pt; font-weight: 600; color: {c['title']}; background: transparent; border: none;")
        v.addWidget(lbl_titulo)

        lbl_desc = QLabel(descripcion)
        lbl_desc.setWordWrap(True)
        lbl_desc.setStyleSheet(f"color: {c['muted']}; font-size: 9pt; background: transparent; border: none;")
        v.addWidget(lbl_desc)
        v.addStretch()

        fila_botones = QHBoxLayout()
        fila_botones.setSpacing(6)
        btn = QPushButton("💾 Generar copia" if disponible else "Próximamente")
        btn.setEnabled(disponible and callback is not None)
        if disponible and callback is not None:
            btn.setObjectName("primary")
            btn.clicked.connect(callback)
        fila_botones.addWidget(btn, 1)

        if disponible and callback_restaurar is not None:
            btn_restaurar = QPushButton("♻️ Restaurar")
            btn_restaurar.setToolTip("Restaurar esta categoría desde una copia de seguridad anterior")
            btn_restaurar.clicked.connect(callback_restaurar)
            fila_botones.addWidget(btn_restaurar)

        v.addLayout(fila_botones)

        return card

    # ── Generadores activos ──────────────────────────────────────────────

    def _generar_backup_registro(self):
        componentes = [BackupComponente(clave, etiqueta) for clave, etiqueta in COMPONENTES_REGISTRO]
        dialog = BackupGeneratorDialog(
            self, "Registro de Windows", "🗂️",
            "Exporta las ramas del registro elegidas a ficheros .reg independientes, "
            "listos para volver a importar con doble clic.",
            "RegistroWindows", componentes,
            lambda destino, claves: RegistryBackupThread(destino, claves),
            _SECCION_AUDITORIA_BACKUPS,
        )
        dialog.exec_()

    def _generar_backup_credenciales(self):
        componentes = [BackupComponente(clave, etiqueta) for clave, etiqueta in COMPONENTES_CREDENCIALES]
        dialog = BackupGeneratorDialog(
            self, "Credenciales y accesos", "🔑",
            "Copia credenciales y accesos guardados en este equipo. Contiene secretos sensibles "
            "(contraseñas WiFi en claro, tokens de Git) — guarda el resultado en un lugar seguro.",
            "CredencialesYAccesos", componentes,
            lambda destino, claves: CredentialsBackupThread(destino, claves),
            _SECCION_AUDITORIA_BACKUPS,
        )
        dialog.exec_()

    def _generar_backup_navegadores(self):
        perfiles, pares = componentes_navegadores()
        if not perfiles:
            self._avisar("Navegadores", "No se ha detectado ningún perfil de Chrome, Edge o Firefox en este equipo.")
            return
        componentes = [BackupComponente(clave, etiqueta) for clave, etiqueta in pares]
        dialog = BackupGeneratorDialog(
            self, "Navegadores", "🌐",
            "Copia marcadores, contraseñas, historial, cookies y sesiones del perfil de navegador "
            "elegido. Si el navegador está en ejecución, la copia es best-effort — ciérralo antes "
            "para garantizar consistencia.",
            "Navegadores", componentes,
            lambda destino, claves: BrowserBackupThread(destino, [p for p in perfiles if p.ruta in claves]),
            _SECCION_AUDITORIA_BACKUPS,
        )
        dialog.exec_()

    def _generar_backup_sistema(self):
        componentes = [BackupComponente(clave, etiqueta) for clave, etiqueta in COMPONENTES_SISTEMA]
        dialog = BackupGeneratorDialog(
            self, "Configuración del sistema", "⚙️",
            "Firewall y variables de entorno se pueden reimportar directamente; servicios e "
            "impresoras son una fotografía documental (no un backup con un clic).",
            "ConfiguracionSistema", componentes,
            lambda destino, claves: SystemConfigBackupThread(destino, claves),
            _SECCION_AUDITORIA_BACKUPS,
        )
        dialog.exec_()

    def _generar_backup_correo(self):
        componentes = [BackupComponente(clave, etiqueta) for clave, etiqueta in COMPONENTES_CORREO]
        dialog = BackupGeneratorDialog(
            self, "Correo", "📧",
            "Configuración de cuentas Outlook y firmas. Para exportar buzones completos a PST, "
            "usa '📤 Exportar esta cuenta a PST' en el menú contextual de Cuentas de Correo — "
            "puede tardar hasta 30 min por cuenta, no se automatiza aquí en lote.",
            "Correo", componentes,
            lambda destino, claves: CorreoBackupThread(destino, claves),
            _SECCION_AUDITORIA_BACKUPS,
        )
        dialog.exec_()

    def _generar_backup_perfil_usuario(self):
        carpetas, pares = componentes_perfil_usuario()
        if not carpetas:
            self._avisar("Perfil de usuario", "No se ha podido resolver ninguna carpeta de perfil en este equipo.")
            return
        componentes = [BackupComponente(clave, etiqueta, marcado) for clave, etiqueta, marcado in pares]
        dialog = BackupGeneratorDialog(
            self, "Perfil de usuario", "👤",
            "Copia completa de las carpetas de perfil elegidas. Puede tardar bastante y ocupar "
            "mucho espacio según el contenido — revisa qué marcas antes de iniciar.",
            "PerfilUsuario", componentes,
            lambda destino, claves: UserProfileBackupThread(
                destino, {n: r for n, r in carpetas.items() if n in claves}),
            _SECCION_AUDITORIA_BACKUPS,
        )
        dialog.exec_()

    def _generar_backup_nube(self):
        componentes = [BackupComponente(clave, etiqueta) for clave, etiqueta in COMPONENTES_NUBE]
        dialog = BackupGeneratorDialog(
            self, "Nube", "☁️",
            "No copia los archivos sincronizados (eso ya lo hace OneDrive) — solo un resumen de "
            "qué cuentas están vinculadas y a qué carpeta local, útil para reconfigurar tras una reinstalación.",
            "Nube", componentes,
            lambda destino, claves: NubeBackupThread(destino, claves),
            _SECCION_AUDITORIA_BACKUPS,
        )
        dialog.exec_()

    def _generar_backup_masys(self):
        componentes = [BackupComponente(clave, etiqueta) for clave, etiqueta in COMPONENTES_MASYS]
        dialog = BackupGeneratorDialog(
            self, "Configuración de MASys", "🧩",
            "Ajustes propios de la aplicación — útil antes de reinstalar el equipo del técnico o "
            "migrar MASys a otro puesto.",
            "ConfiguracionMASys", componentes,
            lambda destino, claves: MASysConfigBackupThread(destino, claves),
            _SECCION_AUDITORIA_BACKUPS,
        )
        dialog.exec_()

    def _generar_backup_sql(self):
        """A diferencia del resto de generadores (todo precalculado), aquí
        hay DOS pasos de validación en tiempo de uso antes de poder mostrar
        el checklist: 1) sqlcmd.exe presente, 2) la instancia elegida
        responde y tiene bases de datos de usuario. Mismo criterio que
        _generar_backup_navegadores (avisar y salir si no hay nada que
        ofrecer), solo que aquí hace falta resolver la instancia primero."""
        sqlcmd_path = sqlcmd_disponible()
        if not sqlcmd_path:
            self._avisar(
                "Bases de datos SQL Server",
                "No se encontró 'sqlcmd' en este equipo. Instala las herramientas de línea de "
                "comandos de SQL Server (vienen con SQL Server, SSMS, o el paquete 'sqlcmd' de "
                "Microsoft) para poder generar esta copia.",
                QMessageBox.Warning)
            return

        instancias = [r for r in detectar_sql_server() if r.tipo == "Motor de base de datos"]
        if not instancias:
            self._avisar(
                "Bases de datos SQL Server",
                "No se detectó ninguna instancia de motor de SQL Server en este equipo "
                "(ver pestaña 🗄️ SQL Server).", QMessageBox.Warning)
            return

        if len(instancias) == 1:
            instancia = instancias[0]
        else:
            opciones = [f"{r.instancia}  —  {r.edicion} (v{r.version})" for r in instancias]
            texto, ok = QInputDialog.getItem(
                self, "Bases de datos SQL Server",
                "Varias instancias detectadas — elige con cuál trabajar:",
                opciones, 0, False)
            if not ok:
                return
            instancia = instancias[opciones.index(texto)]

        if instancia.estado_servicio != "running":
            self._avisar(
                "Bases de datos SQL Server",
                f"El servicio de la instancia «{instancia.instancia}» no está en ejecución "
                "(inícialo desde la pestaña 🗄️ SQL Server antes de hacer la copia).",
                QMessageBox.Warning)
            return

        servidor = servidor_sql_local(instancia.instancia)
        self.set_status(f"🔄 Consultando bases de datos de «{instancia.instancia}»...")
        ok, nombres_bd, error = listar_bases_datos_sql(sqlcmd_path, servidor)
        self.set_status("")
        if not ok:
            self._avisar(
                "Bases de datos SQL Server",
                f"No se pudo conectar con la instancia «{instancia.instancia}»:\n\n{error}",
                QMessageBox.Warning)
            return
        if not nombres_bd:
            self._avisar(
                "Bases de datos SQL Server",
                f"La instancia «{instancia.instancia}» no tiene bases de datos de usuario "
                "(solo las del sistema: master/model/msdb/tempdb).", QMessageBox.Warning)
            return

        componentes = [BackupComponente(nombre, nombre) for nombre in nombres_bd]
        dialog = BackupGeneratorDialog(
            self, f"Bases de datos SQL Server — {instancia.instancia}", "🗄️",
            "BACKUP DATABASE completo de cada base marcada, vía sqlcmd con autenticación de "
            "Windows. El propio motor SQL Server escribe el .bak: la CUENTA DEL SERVICIO "
            "necesita permiso de escritura en el destino, no la cuenta con la que se ejecuta MASys.",
            "BasesDatosSqlServer", componentes,
            lambda destino, claves: SqlBackupThread(destino, claves, sqlcmd_path, servidor),
            _SECCION_AUDITORIA_BACKUPS,
        )
        dialog.exec_()

    def _abrir_captura_forense(self):
        dialog = ForensicCaptureDialog(self)
        dialog.exec_()

    # ── Restauradores: AUTO (MASys sí escribe) ────────────────────────
    # Reversiones directas y sin ambigüedad de su propio backup — ver
    # cabecera "RESTAURACIÓN" en msys/backup_generators.py para el porqué
    # de que solo estas 3 categorías tengan restauración automática.

    def _restaurar_backup_registro(self):
        componentes = [BackupComponente(clave, etiqueta) for clave, etiqueta in COMPONENTES_REGISTRO]
        dialog = BackupRestoreDialog(
            self, "Registro de Windows", "🗂️",
            "RegistroWindows", componentes, _SECCION_AUDITORIA_BACKUPS,
            restaurador=lambda carpeta, claves: RegistryRestoreThread(carpeta, claves),
        )
        dialog.exec_()

    def _restaurar_backup_perfil_usuario(self):
        carpetas, pares = componentes_perfil_usuario()
        if not carpetas:
            self._avisar("Perfil de usuario", "No se ha podido resolver ninguna carpeta de perfil en este equipo.")
            return
        componentes = [BackupComponente(clave, etiqueta, marcado) for clave, etiqueta, marcado in pares]
        dialog = BackupRestoreDialog(
            self, "Perfil de usuario", "👤",
            "PerfilUsuario", componentes, _SECCION_AUDITORIA_BACKUPS,
            restaurador=lambda carpeta, claves: UserProfileRestoreThread(carpeta, carpetas, claves),
        )
        dialog.exec_()

    def _restaurar_backup_masys(self):
        componentes = [BackupComponente(clave, etiqueta) for clave, etiqueta in COMPONENTES_MASYS]
        dialog = BackupRestoreDialog(
            self, "Configuración de MASys", "🧩",
            "ConfiguracionMASys", componentes, _SECCION_AUDITORIA_BACKUPS,
            restaurador=lambda carpeta, claves: MASysConfigRestoreThread(carpeta, claves),
        )
        dialog.exec_()

    # ── Restauradores: GUIADO (MASys NO escribe nada por su cuenta) ───
    # Categorías de mayor riesgo (credenciales DPAPI, navegadores con
    # perfiles en uso, directiva de seguridad local, cuentas de Outlook) —
    # se abre la carpeta del backup elegido y se explica qué copiar y
    # dónde, para que el técnico lo haga con conocimiento de causa. No se
    # registra en el Historial: la app no cambió nada por sí sola.

    def _restaurar_backup_credenciales(self):
        componentes = [BackupComponente(clave, etiqueta) for clave, etiqueta in COMPONENTES_CREDENCIALES]
        guia = (
            "Restauración GUIADA — MASys no sobrescribe credenciales automáticamente "
            "(son secretos protegidos con DPAPI: un error aquí puede dejar credenciales "
            "ilegibles). Con la carpeta del backup abierta:\n\n"
            "• CredentialManager\\Credentials_Roaming  →  copiar a:\n"
            "    %APPDATA%\\Microsoft\\Credentials\n"
            "• CredentialManager\\Credentials_Local  →  copiar a:\n"
            "    %LOCALAPPDATA%\\Microsoft\\Credentials\n"
            "• CredentialManager\\Protect_DPAPI_Keys  →  copiar a:\n"
            "    %APPDATA%\\Microsoft\\Protect\n"
            "  (los tres juntos, del MISMO usuario — sin las claves DPAPI la bóveda no se "
            "puede descifrar. Cierra sesión y vuelve a iniciarla después.)\n\n"
            "• WiFi\\*.xml  →  importar uno a uno:\n"
            "    netsh wlan add profile filename=\"archivo.xml\"\n\n"
            "• SSH  →  copiar el contenido a: %USERPROFILE%\\.ssh\n\n"
            "• .git-credentials  →  copiar a: %USERPROFILE%\\.git-credentials"
        )
        dialog = BackupRestoreDialog(
            self, "Credenciales y accesos", "🔑",
            "CredencialesYAccesos", componentes, _SECCION_AUDITORIA_BACKUPS,
            guia_manual=guia,
        )
        dialog.exec_()

    def _restaurar_backup_navegadores(self):
        perfiles, pares = componentes_navegadores()
        componentes = [BackupComponente(clave, etiqueta) for clave, etiqueta in pares]
        lista_perfiles = "\n".join(f"  • {p.navegador} — {p.nombre_perfil}: {p.ruta}" for p in perfiles) or "  (ninguno detectado ahora mismo)"
        guia = (
            "Restauración GUIADA — MASys no sobrescribe perfiles de navegador automáticamente "
            "(si el navegador está abierto, escribir sobre sus ficheros puede corromper el "
            "perfil). Pasos:\n\n"
            "1. Cierra el navegador por completo (comprueba que no quede en la bandeja del "
            "sistema).\n"
            "2. Dentro de la carpeta del backup, cada subcarpeta '<Navegador>_<Perfil>' "
            "contiene los ficheros ya nombrados igual que en el perfil real (Bookmarks, "
            "Login Data, History, Cookies, Preferences...) salvo 'Network_Cookies', que va "
            "dentro de la subcarpeta 'Network\\' del perfil.\n"
            "3. Copia cada fichero a la carpeta del perfil correspondiente:\n\n"
            f"{lista_perfiles}"
        )
        dialog = BackupRestoreDialog(
            self, "Navegadores", "🌐",
            "Navegadores", componentes, _SECCION_AUDITORIA_BACKUPS,
            guia_manual=guia,
        )
        dialog.exec_()

    def _restaurar_backup_sistema(self):
        componentes = [BackupComponente(clave, etiqueta) for clave, etiqueta in COMPONENTES_SISTEMA]
        guia = (
            "Restauración GUIADA — cada elemento se reaplica con su propia herramienta nativa "
            "de Windows; 'Servicios' e 'Impresoras' son solo documentales (no se recrean con "
            "un comando):\n\n"
            "• Firewall.wfw  →  netsh advfirewall import \"Firewall.wfw\"\n"
            "  ⚠️ REEMPLAZA TODAS las reglas actuales, no las combina — revisa antes.\n\n"
            "• Tareas programadas (una .xml por tarea)  →  por cada una:\n"
            "    schtasks /create /xml \"archivo.xml\" /tn \"NombreDeLaTarea\"\n\n"
            "• Variables_Usuario.reg / Variables_Sistema.reg  →  reg import \"archivo.reg\"\n"
            "  (Variables_Sistema.reg necesita una consola elevada; cierra sesión después "
            "para que se recarguen en el Explorador.)\n\n"
            "• DirectivaSeguridadLocal.inf  →  (elevado)\n"
            "    secedit /configure /db %TEMP%\\restaurar.sdb /cfg \"DirectivaSeguridadLocal.inf\" /overwrite\n"
            "  ⚠️ Puede afectar a la política de contraseñas/bloqueo de cuenta — revisa el "
            "contenido del .inf antes de aplicarlo.\n\n"
            "• Certificados (.cer)  →  certutil -addstore \"MY\" \"archivo.cer\"\n\n"
            "• Servicios / Impresoras / Red.txt  →  documentales (referencia para "
            "reconfigurar a mano, no se reimportan con un comando)."
        )
        dialog = BackupRestoreDialog(
            self, "Configuración del sistema", "⚙️",
            "ConfiguracionSistema", componentes, _SECCION_AUDITORIA_BACKUPS,
            guia_manual=guia,
        )
        dialog.exec_()

    def _restaurar_backup_correo(self):
        componentes = [BackupComponente(clave, etiqueta) for clave, etiqueta in COMPONENTES_CORREO]
        guia = (
            "Restauración GUIADA — dentro de la subcarpeta 'Outlook' del backup:\n\n"
            "• OutlookConfig_<versión>.reg  →  reg import \"OutlookConfig_<versión>.reg\"\n"
            "  (rama HKCU, no necesita consola elevada; cierra Outlook antes de importar "
            "y ábrelo después para que recargue perfiles/cuentas/reglas.)\n\n"
            "• Carpeta/zip de firmas  →  copiar el contenido a:\n"
            "    %APPDATA%\\Microsoft\\Signatures\n\n"
            "Para buzones completos (PST/OST), esto NO los incluye — usa "
            "'📤 Exportar esta cuenta a PST' en 📧 Cuentas de Correo para eso, en ambos "
            "sentidos (exportar/reimportar)."
        )
        dialog = BackupRestoreDialog(
            self, "Correo", "📧",
            "Correo", componentes, _SECCION_AUDITORIA_BACKUPS,
            guia_manual=guia,
        )
        dialog.exec_()

    def _restaurar_backup_nube(self):
        componentes = [BackupComponente(clave, etiqueta) for clave, etiqueta in COMPONENTES_NUBE]
        guia = (
            "Esta categoría es solo un resumen informativo (qué cuentas de OneDrive estaban "
            "vinculadas y a qué carpeta local) — no copia archivos sincronizados, porque eso "
            "ya lo gestiona OneDrive por sí mismo.\n\n"
            "No hay nada que 'restaurar' automáticamente: abre 'OneDrive_Cuentas.txt' en la "
            "carpeta del backup como referencia y vuelve a vincular cada cuenta manualmente "
            "desde la app de OneDrive (Configuración > Cuenta > Agregar una cuenta), apuntando "
            "a la misma carpeta local que se indica ahí."
        )
        dialog = BackupRestoreDialog(
            self, "Nube", "☁️",
            "Nube", componentes, _SECCION_AUDITORIA_BACKUPS,
            guia_manual=guia,
        )
        dialog.exec_()
