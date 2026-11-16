# -*- coding: utf-8 -*-
"""Configuracion global de MASys: constantes, deteccion del directorio
base de la app, config de pestanas visibles y categorias del menu lateral.
Extraido de multisys.py (Fase 1 de modularizacion).

IMPORTANTE sobre ALTERNATE_CREDENTIALS: es un global mutable que se lee
desde msys.core.utils.wrap_with_credentials() y se escribe desde el dialogo
de credenciales alternativas en ShareInspector (multisys.py). Por eso ambos
sitios deben acceder vía el modulo (``msys_config.ALTERNATE_CREDENTIALS``),
nunca con ``from msys.config import ALTERNATE_CREDENTIALS`` + ``global``:
eso crearia una copia local por modulo y romperia la sincronizacion.
"""

import os
import sys
import json
import tempfile
import logging

logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuracion de checkpoints
CHECKPOINT_FILE = os.path.join(tempfile.gettempdir(), "MASys_checkpoint.pkl")
CHECKPOINT_INTERVAL = 50  # Guardar cada N carpetas procesadas

DEFAULT_TIMEOUT = 30

# Credenciales alternativas (None = usar credenciales actuales del usuario)
ALTERNATE_CREDENTIALS = None  # Tupla (usuario, contraseña) o None


def app_base_dir() -> str:
    """Directorio base real de la aplicacion: el del .exe si esta congelada
    con PyInstaller, o la raiz del proyecto en modo desarrollo. No usar
    __file__ de este modulo tal cual: apuntaria a msys/, no a la raiz del
    proyecto donde viven multisys.db, buscacompartidos_config.json e
    INFORMES. La raiz es siempre dos niveles por encima de este fichero
    (msys/config.py -> msys/ -> raiz), independientemente de si el modulo
    se importa como __main__, como submodulo o desde una consola."""
    if getattr(sys, 'frozen', False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ruta_recurso(*partes: str) -> str:
    """Fichero que va DENTRO del programa (no dato del usuario). En el .exe de
    PyInstaller onedir esos ficheros quedan en _internal\\ (sys._MEIPASS), no
    junto al .exe; en desarrollo, en la raíz del proyecto."""
    candidatos = [os.path.join(app_base_dir(), *partes)]
    if hasattr(sys, "_MEIPASS"):
        candidatos.insert(0, os.path.join(sys._MEIPASS, *partes))
    for ruta in candidatos:
        if os.path.exists(ruta):
            return ruta
    return candidatos[-1]


# ---------------------------
# Configuration file management
# ---------------------------
CONFIG_FILE = os.path.join(app_base_dir(), 'buscacompartidos_config.json')

DEFAULT_TAB_CONFIG = {
    "tabs": [
        {"name": "📊 Dashboard", "visible": True},
        {"name": "💻 Sistema", "visible": True},
        {"name": "🎨 Iconos", "visible": True},
        {"name": "🔗 Extensiones y programas", "visible": True},
        {"name": "🧩 Características de Windows", "visible": True},
        {"name": "🩺 Estabilidad", "visible": True},
        {"name": "📂 Carpetas Compartidas", "visible": True},
        {"name": "💾 Unidades Remotas", "visible": True},
        {"name": "🔑 Credenciales", "visible": True},
        {"name": "📶 WiFi", "visible": True},
        {"name": "🔐 Certificados", "visible": True},
        {"name": "🌐 Variables Entorno", "visible": True},
        {"name": "⚙️ Procesos", "visible": True},
        {"name": "🚀 Programas de Inicio", "visible": True},
        {"name": "🛠️ Servicios", "visible": True},
        {"name": "🌐 Conexiones Red", "visible": True},
        {"name": "📦 Software Instalado", "visible": True},
        {"name": "⏰ Tareas Programadas", "visible": True},
        {"name": "🖨️ Impresoras", "visible": True},
        {"name": "🔥 Firewall", "visible": True},
        {"name": "📧 Cuentas Correo", "visible": True},
        {"name": "👥 Usuarios", "visible": True},
        {"name": "🔒 Conexiones Remotas", "visible": True},
        {"name": "👤 Sesiones Activas", "visible": True},
        {"name": "🔵 Bluetooth", "visible": True},
        {"name": "📜 Historial PS", "visible": True},
        {"name": "⚠️ Eventos Sistema", "visible": True},
        {"name": "🏢 Active Directory", "visible": True},
        {"name": "🏛️ Políticas de Grupo", "visible": True},
        {"name": "🔌 Dispositivos USB", "visible": True},
        {"name": "🖥️ Gráficas y Monitores", "visible": True},
        {"name": "🔄 Actualizaciones", "visible": True},
        {"name": "🖥️ Adm. Dispositivos", "visible": True},
        {"name": "📁 Contenido", "visible": True},
        {"name": "🌐 Redes", "visible": True},
        {"name": "🛡️ Seguridad", "visible": True},
        {"name": "💽 Discos Duros", "visible": True},
        {"name": "🔑 Licencias", "visible": True},
        {"name": "📦 Gestor de Paquetes", "visible": True},
        {"name": "🔍 Red Local", "visible": True},
        {"name": "🧪 Herramientas de red", "visible": True},
        {"name": "🗄️ SQL Server", "visible": True},
        {"name": "🧳 Inventario de migración", "visible": True}
    ]
}

# ---------------------------
# Categorías del menú lateral
# ---------------------------
# "📊 Dashboard" queda fuera de este mapa a propósito: se trata como
# elemento fijo sin categoría, siempre arriba del árbol.
CATEGORY_ORDER = [
    "sistema", "disco", "red", "usuarios", "hardware", "administracion",
]

CATEGORIES = {
    "sistema":        {"label": "💻 Sistema"},
    "disco":          {"label": "💾 Disco y Archivos"},
    "red":            {"label": "🌐 Red"},
    "usuarios":       {"label": "👥 Usuarios y Seguridad"},
    "hardware":       {"label": "🖨️ Hardware y Periféricos"},
    "administracion": {"label": "🛠️ Administración"},
}

SECTION_CATEGORY = {
    "💻 Sistema": "sistema",
    "🗃️ Registro de Windows": "sistema",
    "🎨 Iconos": "sistema",
    "🔗 Extensiones y programas": "sistema",
    "🧩 Características de Windows": "sistema",
    "🩺 Estabilidad": "sistema",
    "⚙️ Procesos": "sistema",
    "🚀 Programas de Inicio": "sistema",
    "🛠️ Servicios": "sistema",
    "🔄 Actualizaciones": "sistema",
    "🌐 Variables Entorno": "sistema",
    "⚠️ Eventos Sistema": "sistema",

    "📂 Carpetas Compartidas": "disco",
    "💾 Unidades Remotas": "disco",
    "📁 Contenido": "disco",
    "💽 Discos Duros": "disco",

    "📶 WiFi": "red",
    "🌐 Conexiones Red": "red",
    "🔥 Firewall": "red",
    "🔒 Conexiones Remotas": "red",
    "🌐 Redes": "red",
    "🔍 Red Local": "red",
    "🧪 Herramientas de red": "red",

    "👥 Usuarios": "usuarios",
    "🔑 Credenciales": "usuarios",
    "🔐 Certificados": "usuarios",
    "📧 Cuentas Correo": "usuarios",
    "☁️ Navegadores y Nube": "usuarios",
    "🏢 Active Directory": "usuarios",
    "🏛️ Políticas de Grupo": "usuarios",
    "👤 Sesiones Activas": "usuarios",
    "🛡️ Seguridad": "usuarios",

    "🖥️ Adm. Dispositivos": "hardware",
    "🖨️ Impresoras": "hardware",
    "🔌 Dispositivos USB": "hardware",
    "🖥️ Gráficas y Monitores": "hardware",
    "🔵 Bluetooth": "hardware",

    "📦 Software Instalado": "administracion",
    "⏰ Tareas Programadas": "administracion",
    "📜 Historial PS": "administracion",
    "🔑 Licencias": "administracion",
    "📦 Gestor de Paquetes": "administracion",
    "🗄️ SQL Server": "administracion",
    "🧳 Inventario de migración": "administracion",
}


def load_tab_config():
    """Carga la configuración de pestañas desde el archivo JSON."""
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        else:
            return DEFAULT_TAB_CONFIG.copy()
    except Exception as e:
        logger.error(f"Error cargando configuración: {e}")
        return DEFAULT_TAB_CONFIG.copy()


def save_tab_config(config):
    """Guarda la configuración de pestañas en el archivo JSON."""
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        logger.info(f"Configuración guardada en {CONFIG_FILE}")
        return True
    except Exception as e:
        logger.error(f"Error guardando configuración: {e}")
        return False
