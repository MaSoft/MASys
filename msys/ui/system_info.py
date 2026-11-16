# -*- coding: utf-8 -*-
"""Mixin de ShareInspector: Pestaña Sistema: fichas de hardware/SO/BIOS, reinicio en BIOS/UEFI, exportar.
Extraido de multisys.py (Fase 2 de modularizacion — mixins de ShareInspector).

Como todos los mixins de msys/ui/, sus metodos operan sobre "self" de la
instancia real de ShareInspector (que hereda de este mixin junto a los
demas) — no son autonomos ni instanciables por si solos."""

import re
import csv
import subprocess
from datetime import datetime

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QMessageBox, QFormLayout, QFrame, QSizePolicy,
    QTreeWidget, QTreeWidgetItem,
)

from msys.config import logger
from msys.core.utils import ruta_guardado_predeterminada, dialogo_guardar_archivo, _SafeCsvWriter
from msys.widgets.delegates import _setup_header
from msys.collectors import SystemInfoCollector


class SistemaMixin:
    def crear_tab_system_info(self):
        """Crea la pestaña de Información del Sistema."""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setSpacing(8)
        layout.setContentsMargins(10, 10, 10, 10)

        # Barra de herramientas — mismo patrón que Variables de entorno/
        # Procesos/Servicios: hostname, búsqueda, hueco elástico y el botón
        # Actualizar a la derecha del todo (antes vivía suelto en una fila
        # aparte arriba, con el texto "Actualizar Información").
        actions_layout = QHBoxLayout()
        actions_layout.setSpacing(8)

        import socket
        hostname = socket.gethostname()
        lbl_hostname = QLabel(f"🖥️ {hostname}")
        lbl_hostname.setObjectName("hostnameLabel")
        actions_layout.addWidget(lbl_hostname)

        self.search_system = QLineEdit()
        self.search_system.setPlaceholderText("🔎 Buscar en información del sistema...")
        self.search_system.textChanged.connect(self.filter_system_tree)
        actions_layout.addWidget(self.search_system, 3)

        actions_layout.addStretch()

        self.btn_reiniciar_bios = QPushButton("🔌 Reiniciar en BIOS/UEFI")
        self.btn_reiniciar_bios.setObjectName("danger")
        self.btn_reiniciar_bios.setToolTip(
            "Reinicia el equipo directamente en la configuración de firmware "
            "(BIOS/UEFI). Sólo disponible en equipos con firmware UEFI."
        )
        self.btn_reiniciar_bios.clicked.connect(self._reiniciar_en_bios)
        actions_layout.addWidget(self.btn_reiniciar_bios)

        self.btn_refresh_system = QPushButton("🔄 Actualizar")
        self.btn_refresh_system.setObjectName("primary")
        self.btn_refresh_system.clicked.connect(self.refresh_system_info)
        actions_layout.addWidget(self.btn_refresh_system)

        layout.addLayout(actions_layout)

        # Árbol interno (no se muestra): sigue siendo la fuente de datos de
        # export_system_csv, que ya funcionaba bien recorriendo un árbol y
        # no necesita cambiar sólo porque la vista pase a fichas.
        self.system_tree = QTreeWidget(self)
        self.system_tree.setHeaderLabels(["Componente", "Valor"])

        # Vista de fichas (cards): una por categoría, en una rejilla con
        # scroll, para aprovechar el ancho de la pestaña en vez de una
        # tabla de 2 columnas comprimida a la izquierda.
        from PyQt5.QtWidgets import QScrollArea, QGridLayout
        self.system_cards_scroll = QScrollArea()
        self.system_cards_scroll.setWidgetResizable(True)
        self.system_cards_scroll.setFrameShape(QFrame.NoFrame)

        system_cards_container = QWidget()
        container_layout = QVBoxLayout(system_cards_container)
        container_layout.setContentsMargins(2, 2, 2, 2)
        container_layout.setSpacing(14)

        grid_widget = QWidget()
        self.system_cards_grid = QGridLayout(grid_widget)
        self.system_cards_grid.setSpacing(14)
        self.system_cards_grid.setContentsMargins(0, 0, 0, 0)
        container_layout.addWidget(grid_widget)

        # Evaluación de actualidad: resumen (antigüedad, RAM, margen de
        # ampliación, requisitos de Windows 11) construido en Python a
        # partir de datos YA recogidos arriba — no hace ninguna consulta
        # nueva. Se rellena en _construir_evaluacion_actualidad().
        # Nombre de objeto DISTINTO de "sysInfoCard" a propósito: no es una
        # ficha buscable/copiable más — _fichas_sistema()/_aplicar_filtro_
        # fichas_sistema() la ignoran así (si compartiera nombre, un texto
        # de búsqueda activo la ocultaría, porque no tiene la propiedad
        # 'texto_busqueda' que sí rellena _crear_ficha_sistema()).
        self.system_eval_frame = QFrame()
        self.system_eval_frame.setObjectName("sysEvalCard")
        self.system_eval_layout = QVBoxLayout(self.system_eval_frame)
        self.system_eval_layout.setContentsMargins(14, 12, 14, 12)
        self.system_eval_layout.setSpacing(6)
        self.system_eval_frame.setVisible(False)
        container_layout.addWidget(self.system_eval_frame)

        self.system_cards_scroll.setWidget(system_cards_container)

        layout.addWidget(self.system_cards_scroll)

        return tab

    def _crear_ficha_sistema(self, emoji, titulo, color_acento, filas, widget_extra=None):
        """Crea una ficha (card) para la pestaña Sistema: cabecera con icono
        y título, cuerpo con pares clave:valor, y opcionalmente un widget
        adicional (p. ej. la tabla de módulos de RAM). Cada elemento de
        'filas' es normalmente un par (etiqueta, valor) — una fila simple.
        Si es un par DE PARES, ((etq1, val1), (etq2, val2)), se renderizan
        los dos lado a lado en la misma fila (p. ej. Núcleos/Hilos)."""
        c = self._dash_colors()
        card = QFrame()
        card.setObjectName("sysInfoCard")
        # Que la ficha ocupe sólo el alto que necesita su contenido, nunca
        # más (evita el hueco en blanco dentro de la ficha).
        card.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        card.setStyleSheet(f"""
            QFrame#sysInfoCard {{
                background: {c['alerts_bg']};
                border: 1px solid {c['alerts_border']};
                border-top: 3px solid {color_acento};
                border-radius: 8px;
            }}
            QFrame#sysInfoCard QLabel {{ background: transparent; border: none; }}
        """)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(6)

        header = QLabel(f"{emoji}  {titulo}")
        header.setStyleSheet(f"font-size:11pt; font-weight:700; color:{c['title']};")
        lay.addWidget(header)

        texto_copiar = [titulo]
        texto_busqueda = [titulo.lower()]
        if filas:
            form = QFormLayout()
            form.setSpacing(4)
            form.setContentsMargins(0, 6, 0, 0)
            form.setLabelAlignment(Qt.AlignLeft)
            for fila in filas:
                if isinstance(fila[0], (tuple, list)):
                    # Fila combinada: dos pares etiqueta:valor lado a lado.
                    # form.addRow() acepta un QLayout directamente — nada de
                    # envolverlo en un QWidget intermedio, que no hereda el
                    # "background: transparent" que el stylesheet de la
                    # ficha sí aplica a QLabel, y se veía con un fondo propio
                    # distinto (más oscuro) al resto de la ficha.
                    fila_layout = QHBoxLayout()
                    fila_layout.setSpacing(18)
                    resumen_texto = []
                    for etiqueta, valor in fila:
                        valor = str(valor) if valor not in (None, "") else "N/A"
                        par_layout = QHBoxLayout()
                        par_layout.setSpacing(4)
                        lbl_k = QLabel(f"{etiqueta}:")
                        lbl_k.setStyleSheet(f"font-size:9pt; color:{c['muted']}; font-weight:600;")
                        lbl_v = QLabel(valor)
                        lbl_v.setStyleSheet(f"font-size:9pt; color:{c['title']};")
                        lbl_v.setTextInteractionFlags(Qt.TextSelectableByMouse)
                        par_layout.addWidget(lbl_k)
                        par_layout.addWidget(lbl_v)
                        fila_layout.addLayout(par_layout)
                        resumen_texto.append(f"{etiqueta}: {valor}")
                    fila_layout.addStretch()
                    form.addRow(fila_layout)
                    texto_copiar.append(f"  {'  '.join(resumen_texto)}")
                    texto_busqueda.append(" ".join(resumen_texto).lower())
                else:
                    etiqueta, valor = fila
                    valor = str(valor) if valor not in (None, "") else "N/A"
                    lbl_k = QLabel(f"{etiqueta}:")
                    lbl_k.setStyleSheet(f"font-size:9pt; color:{c['muted']}; font-weight:600;")
                    lbl_v = QLabel(valor)
                    lbl_v.setStyleSheet(f"font-size:9pt; color:{c['title']};")
                    lbl_v.setWordWrap(True)
                    lbl_v.setTextInteractionFlags(Qt.TextSelectableByMouse)
                    form.addRow(lbl_k, lbl_v)
                    texto_copiar.append(f"  {etiqueta}: {valor}")
                    texto_busqueda.append(f"{etiqueta} {valor}".lower())
            lay.addLayout(form)

        if widget_extra is not None:
            lay.addWidget(widget_extra)

        # Sin esto, si alguien fuerza la ficha a más alto que su contenido
        # (p. ej. igualar Equipo a Sistema Operativo), Qt reparte el hueco
        # sobrante metiéndolo entre el título y los datos o entre filas del
        # formulario. Con el stretch al final, ese hueco sobrante siempre
        # cae DEBAJO de todo el contenido, y el espaciado título-datos-filas
        # queda igual que en cualquier otra ficha, se estire o no.
        lay.addStretch()

        card.setProperty("texto_busqueda", " ".join(texto_busqueda))
        card.setProperty("texto_copiar", "\n".join(texto_copiar))
        card.setProperty("oculto_por_filtro", False)
        return card

    def _crear_tabla_ram_modulos(self, modulos):
        """Tabla compacta con los módulos de RAM (datos naturalmente
        repetitivos: encajan mejor en una tabla que en pares clave:valor)."""
        from PyQt5.QtWidgets import QTableWidget, QTableWidgetItem
        headers = ["Módulo", "Capacidad", "Velocidad", "Tipo", "Fabricante", "Ubicación"]
        tabla = QTableWidget(len(modulos), len(headers))
        tabla.setHorizontalHeaderLabels(headers)
        tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        tabla.setSelectionMode(QTableWidget.NoSelection)
        tabla.verticalHeader().setVisible(False)
        tabla.setAlternatingRowColors(True)
        tabla.setShowGrid(False)
        _setup_header(tabla, len(headers), stretch_col=5)
        for i, mod in enumerate(modulos):
            valores = [f"#{i + 1}", mod.get('Capacidad', 'N/A'), mod.get('Velocidad', 'N/A'),
                       mod.get('Tipo', 'N/A'), mod.get('Fabricante', 'N/A'), mod.get('Ubicacion', 'N/A')]
            for col, val in enumerate(valores):
                tabla.setItem(i, col, QTableWidgetItem(str(val)))

        # Nunca debe haber scroll interno en esta tabla: hay espacio de
        # sobra debajo en la pestaña, así que se calcula el alto real ya
        # renderizado (con el padding que aplica el estilo de la app a las
        # celdas) para mostrar TODAS las filas sin recortar ninguna.
        # verticalHeader().defaultSectionSize() no valía: es un valor
        # genérico que no tiene en cuenta ese padding, así que la tabla se
        # quedaba corta y aparecía una barra de scroll interna.
        tabla.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        tabla.resizeRowsToContents()
        alto_filas = sum(tabla.rowHeight(i) for i in range(tabla.rowCount()))
        alto_total = (tabla.horizontalHeader().height() + alto_filas
                      + 2 * tabla.frameWidth())
        tabla.setFixedHeight(alto_total)
        return tabla

    def _construir_fichas_sistema(self, data: dict):
        """Reconstruye la rejilla de fichas de la pestaña Sistema a partir
        de los mismos datos que rellenan el árbol interno.

        Solo hay una fila real en la rejilla grande, con dos columnas
        anchas: la izquierda (Equipo+SO arriba, RAM pegada debajo, todo en
        layouts anidados propios) y la derecha (Procesador, Placa Base y
        BIOS/Firmware pegadas en un único widget). Al no compartir rowSpan
        ni cálculo de altura entre columnas, cada lado se dimensiona solo
        con su propio contenido — Equipo se iguala a SO dejando que Qt lo
        resuelva (política Expanding en Equipo dentro de su propio
        QHBoxLayout con SO), no midiendo geometría a mano."""
        while self.system_cards_grid.count():
            item = self.system_cards_grid.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        fichas_iniciales = []   # Equipo, SO — fila superior, columnas 0 y 1
        fichas_columna3 = []    # Procesador, Placa Base, BIOS — pegadas en columna 2
        ficha_ram = None        # ancha (tabla incrustada), fila propia bajo Equipo/SO
        ficha_equipo = None
        ficha_so = None

        if 'Computer' in data:
            comp = data['Computer']
            tipo = comp.get('Tipo')
            chasis = comp.get('FormaChasis')
            filas_equipo = [
                ("Nombre", comp.get('Nombre')), ("Fabricante", comp.get('Fabricante')),
                ("Modelo", comp.get('Modelo')), ("Tipo", tipo),
            ]
            # Chasis es el código SMBIOS traducido a texto; Tipo es una
            # heurística combinando varias señales. En equipos normales
            # ambos coinciden textualmente ("Desktop"/"Desktop") y mostrar
            # los dos es redundante — solo se añade Chasis si aporta algo
            # distinto (portátiles, servidores rack, etc.).
            if not (tipo and chasis and tipo.strip().lower() == chasis.strip().lower()):
                filas_equipo.append(("Chasis", chasis))
            filas_equipo.append(("Nº Serie", comp.get('NumeroSerie')))
            filas_equipo.append(("Dominio", comp.get('Dominio')))
            ficha_equipo = self._crear_ficha_sistema("💻", "Equipo", "#2563eb", filas_equipo)
            fichas_iniciales.append(ficha_equipo)

        if 'OS' in data:
            os_data = data['OS']
            arquitectura = os_data.get('Arquitectura')
            nombre_os = os_data.get('Nombre') or 'N/A'
            # La arquitectura va pegada al nombre ("Windows 11 Pro (64
            # bits)") en vez de en su propia fila.
            nombre_completo = f"{nombre_os} ({arquitectura})" if arquitectura and arquitectura != 'N/A' else nombre_os
            filas_os = [
                ("Nombre", nombre_completo), ("Versión", os_data.get('Version')),
                ("Build", os_data.get('Build')), ("Instalación", os_data.get('Instalacion')),
            ]
            if os_data.get('Serial'):
                filas_os.append(("Serial", os_data.get('Serial')))
            if os_data.get('EstadoActivacion'):
                filas_os.append(("Activación", os_data.get('EstadoActivacion')))
            if os_data.get('CanalLicencia') and os_data.get('CanalLicencia') != 'N/A':
                filas_os.append(("Canal Licencia", os_data.get('CanalLicencia')))
            ficha_so = self._crear_ficha_sistema("🪟", "Sistema Operativo", "#7c3aed", filas_os)
            fichas_iniciales.append(ficha_so)

        if 'CPU' in data:
            cpu = data['CPU']
            filas_cpu = [
                ("Nombre", cpu.get('Nombre')), ("Fabricante", cpu.get('Fabricante')),
                (("Núcleos", cpu.get('Nucleos')), ("Hilos", cpu.get('Hilos'))),
                ("Velocidad", cpu.get('Velocidad')),
            ]
            info_cpu = self._estimar_generacion_cpu(cpu.get('Nombre') or '')
            if info_cpu and info_cpu.get('anio_aprox'):
                antiguedad_anios = datetime.now().year - info_cpu['anio_aprox']
                filas_cpu.append(("Antigüedad", f"~{antiguedad_anios} años ({info_cpu['etiqueta']}, ~{info_cpu['anio_aprox']})"))
            fichas_columna3.append(self._crear_ficha_sistema("⚙️", "Procesador", "#ea580c", filas_cpu))

        if 'RAM' in data:
            ram = data['RAM']
            modulos = ram.get('Modulos') or []
            tabla_ram = self._crear_tabla_ram_modulos(modulos) if modulos else None
            ranuras_totales = ram.get('RanurasTotales')
            ranuras_usadas = ram.get('RanurasUsadas')
            if ranuras_totales:
                fila_modulos_ranuras = (("Módulos", len(modulos)), ("Ranuras", f"{ranuras_totales} - Usadas: {ranuras_usadas}"))
            else:
                fila_modulos_ranuras = ("Módulos", len(modulos))
            filas_ram = [("Total", ram.get('Total')), fila_modulos_ranuras]
            ficha_ram = self._crear_ficha_sistema("💾", "Memoria RAM", "#16a34a",
                filas_ram, widget_extra=tabla_ram)

        if 'Motherboard' in data:
            mb = data['Motherboard']
            fichas_columna3.append(self._crear_ficha_sistema("🔧", "Placa Base", "#0891b2", [
                ("Fabricante", mb.get('Fabricante')), ("Modelo", mb.get('Modelo')),
                ("Versión", mb.get('Version')),
            ]))

        if 'Bios' in data:
            bios_data = data['Bios']
            # "Antigüedad" (basada en la fecha del firmware) se quitó de
            # aquí: es una aproximación peor que la que ya calculamos por
            # generación de CPU (ficha Procesador) y confundía mostrando
            # las dos con criterios distintos. Se deja solo la fecha en
            # bruto ("Últ. Actualización"), sin traducirla a una antigüedad.
            fichas_columna3.append(self._crear_ficha_sistema("🔌", "BIOS / Firmware", "#0f766e", [
                ("Fabricante", bios_data.get('Fabricante')), ("Nombre", bios_data.get('Nombre')),
                ("Versión", bios_data.get('Version')), ("Últ. Actualización", bios_data.get('Fecha')),
                ("Firmware", bios_data.get('Firmware')), ("Secure Boot", bios_data.get('SecureBoot')),
            ]))

        if not fichas_iniciales and ficha_ram is None and not fichas_columna3:
            vacio = QLabel("No hay datos. Pulsa 🔄 Actualizar.")
            vacio.setAlignment(Qt.AlignCenter)
            vacio.setStyleSheet("color:#6c757d; font-size:11pt; padding:40px;")
            self.system_cards_grid.addWidget(vacio, 0, 0)
            return

        max_cols = 3

        # Equipo igualado a SO dejando que Qt lo resuelva solo: Equipo pasa
        # a Expanding (SO se queda tal cual, tamaño de su propio contenido)
        # y ambas viven en un QHBoxLayout propio, sin ninguna otra ficha de
        # por medio. La altura de esa fila la marca SO (la más alta) y
        # Equipo se estira a rellenarla — sin leer geometría a mano ni
        # depender de cuándo termina de recalcular la rejilla grande.
        if ficha_equipo is not None:
            ficha_equipo.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)

        fila_equipo_so = QHBoxLayout()
        fila_equipo_so.setSpacing(14)
        for card in fichas_iniciales:
            fila_equipo_so.addWidget(card)

        # Columna izquierda: Equipo+SO arriba, RAM pegada justo debajo, en
        # un layout propio — inmune a lo que necesite la columna derecha.
        columna_izquierda = QWidget()
        col_izq_layout = QVBoxLayout(columna_izquierda)
        col_izq_layout.setContentsMargins(0, 0, 0, 0)
        col_izq_layout.setSpacing(14)
        if fichas_iniciales:
            col_izq_layout.addLayout(fila_equipo_so)
        if ficha_ram is not None:
            col_izq_layout.addWidget(ficha_ram)
        col_izq_layout.addStretch()

        ancho_izquierda = max(1, max_cols - 1)
        self.system_cards_grid.addWidget(columna_izquierda, 0, 0, 1, ancho_izquierda, Qt.AlignTop)

        # Columna derecha: Procesador, Placa Base y BIOS/Firmware pegadas en
        # un único widget, igual que antes — ahora en la única fila real de
        # la rejilla, sin rowSpan que contamine el cálculo de la izquierda.
        if fichas_columna3:
            combo = QWidget()
            combo_layout = QVBoxLayout(combo)
            combo_layout.setContentsMargins(0, 0, 0, 0)
            combo_layout.setSpacing(14)
            for card in fichas_columna3:
                combo_layout.addWidget(card)
            combo_layout.addStretch()
            self.system_cards_grid.addWidget(combo, 0, ancho_izquierda, 1, max_cols - ancho_izquierda, Qt.AlignTop)

        # El espacio sobrante se acumula en una fila fantasma al final, no
        # repartido entre las fichas.
        self.system_cards_grid.setRowStretch(1, 1)
        for col in range(max_cols):
            self.system_cards_grid.setColumnStretch(col, 1)

        self._aplicar_filtro_fichas_sistema()

    # Generación -> año aproximado de lanzamiento. Fuente: calendario público
    # de lanzamientos de Intel Core / AMD Ryzen — no es una fecha exacta de
    # ESTE procesador concreto, es el año en que salió esa generación/serie.
    _CPU_INTEL_ANIO_POR_GEN = {
        2: 2011, 3: 2012, 4: 2013, 5: 2015, 6: 2015, 7: 2016,
        8: 2017, 9: 2018, 10: 2019, 11: 2020, 12: 2021, 13: 2022, 14: 2023,
    }
    _CPU_AMD_ANIO_POR_SERIE = {
        1000: 2017, 2000: 2018, 3000: 2019, 4000: 2020,
        5000: 2020, 6000: 2022, 7000: 2022, 8000: 2023, 9000: 2024,
    }

    def _estimar_generacion_cpu(self, nombre_cpu: str):
        """Estima fabricante/generación/año aproximado de lanzamiento y si
        cumple el requisito de CPU de Windows 11, a partir del NOMBRE
        COMERCIAL del procesador (Win32_Processor.Name) — no hay una fecha
        de comercialización real accesible sin conexión a internet, que
        este programa no necesita para nada más y no va a empezar a
        requerir solo por esto. Es una estimación por patrón de nombre:
        cubre Intel Core i3/i5/i7/i9 y AMD Ryzen (la inmensa mayoría de
        equipos de empresa); no reconoce Xeon/Celeron/Pentium/APUs viejas
        ni SKUs raras — en ese caso devuelve None y no se afirma nada."""
        if not nombre_cpu:
            return None

        m = re.search(r'i[3579]-(\d{4,5})([A-Z]*\d*)', nombre_cpu)
        if m:
            digitos, sufijo = m.group(1), m.group(2)
            # Los portátiles Ice Lake/Tiger Lake (10ª/11ª gen) usan solo 4
            # dígitos con sufijo "G4/G7" — sin este caso especial, "1165G7"
            # se leería como generación 1 en vez de 11.
            es_serie_g_movil = bool(re.match(r'G\d', sufijo))
            generacion = int(digitos[:2]) if (len(digitos) == 5 or es_serie_g_movil) else int(digitos[:1])
            return {
                "fabricante": "Intel",
                "etiqueta": f"Intel Core, {generacion}ª generación",
                "anio_aprox": self._CPU_INTEL_ANIO_POR_GEN.get(generacion),
                "compatible_win11": generacion >= 8,
            }

        m = re.search(r'Ryzen\s+[3579]\s+(\d{4})', nombre_cpu, re.IGNORECASE)
        if m:
            serie = int(m.group(1)[0]) * 1000
            return {
                "fabricante": "AMD",
                "etiqueta": f"AMD Ryzen serie {serie}",
                "anio_aprox": self._CPU_AMD_ANIO_POR_SERIE.get(serie),
                "compatible_win11": serie >= 2000,
            }

        return None

    def _construir_evaluacion_actualidad(self, data: dict):
        """Resumen "¿este equipo está al día?" bajo las fichas, construido
        SOLO con datos que la propia pestaña Sistema ya recoge (nada de
        disco ni TPM — viven en Discos Duros/Seguridad, y no se duplican
        consultas entre pestañas). La antigüedad y la compatibilidad con
        Windows 11 se basan PRIMERO en la generación del procesador (mucho
        más fiable que la fecha del firmware, que solo indica cuándo se
        actualizó el BIOS por última vez — un equipo de 2015 con el BIOS
        actualizado en 2024 saldría como "2 años" si nos fiáramos solo de
        eso). Si el nombre de la CPU no se reconoce, se cae de vuelta a la
        fecha del firmware, dejando claro que es una aproximación peor."""
        bios = data.get('Bios', {})
        ram = data.get('RAM', {})
        cpu = data.get('CPU', {})

        lineas = []  # (icono, texto)
        info_cpu = self._estimar_generacion_cpu(cpu.get('Nombre') or '')

        if info_cpu and info_cpu.get('anio_aprox'):
            anio_cpu = info_cpu['anio_aprox']
            antiguedad_anios = datetime.now().year - anio_cpu
            detalle = f"{info_cpu['etiqueta']} (~{anio_cpu}, hace ~{antiguedad_anios} años)"
            if antiguedad_anios >= 7:
                lineas.append(("⚠️", f"{detalle} — candidato a renovación."))
            elif antiguedad_anios >= 4:
                lineas.append(("ℹ️", f"{detalle} — dentro de la vida útil habitual."))
            else:
                lineas.append(("✅", f"{detalle} — procesador relativamente reciente."))
        else:
            antiguedad_txt = bios.get('Antiguedad') or ''
            m = re.match(r'(\d+)', antiguedad_txt)
            if m:
                anios = int(m.group(1))
                aviso = " (procesador no identificado; esto solo refleja la fecha del firmware, puede no coincidir con la antigüedad real)"
                if anios >= 7:
                    lineas.append(("⚠️", f"Antigüedad estimada de {antiguedad_txt} según firmware{aviso}."))
                elif anios >= 4:
                    lineas.append(("ℹ️", f"Antigüedad estimada de {antiguedad_txt} según firmware{aviso}."))
                else:
                    lineas.append(("✅", f"Antigüedad estimada de {antiguedad_txt} según firmware{aviso}."))

        total_ram_txt = ram.get('Total') or ''
        m = re.match(r'([\d.,]+)', total_ram_txt)
        if m:
            gb = float(m.group(1).replace(',', '.'))
            if gb < 8:
                lineas.append(("⚠️", f"RAM total de {total_ram_txt} — por debajo de los 8 GB recomendados para uso general actual."))
            elif gb < 16:
                lineas.append(("ℹ️", f"RAM total de {total_ram_txt} — suficiente para uso general; justa para tareas exigentes."))
            else:
                lineas.append(("✅", f"RAM total de {total_ram_txt} — cómoda para el uso actual."))

        ranuras_totales = ram.get('RanurasTotales')
        ranuras_usadas = ram.get('RanurasUsadas')
        if ranuras_totales:
            if ranuras_usadas >= ranuras_totales:
                lineas.append(("⚠️", f"Todas las ranuras de RAM ocupadas ({ranuras_usadas}/{ranuras_totales}) — ampliar exigiría sustituir módulos existentes."))
            else:
                libres = ranuras_totales - ranuras_usadas
                lineas.append(("✅", f"{libres} ranura(s) de RAM libre(s) ({ranuras_usadas}/{ranuras_totales}) — hay margen de ampliación."))

        firmware = (bios.get('Firmware') or '').lower()
        secure_boot = bios.get('SecureBoot') or ''
        firmware_ok = 'uefi' in firmware and secure_boot == 'Activado'

        if info_cpu is not None:
            cpu_ok = info_cpu['compatible_win11']
            if cpu_ok and firmware_ok:
                lineas.append(("✅", "Cumple los requisitos de CPU y firmware (UEFI + Secure Boot) para Windows 11."))
            elif not cpu_ok:
                lineas.append(("❌", f"{info_cpu['etiqueta']} por debajo del mínimo de Windows 11 "
                                     "(se requiere Intel 8ª gen. / AMD Ryzen serie 2000 o superior) "
                                     "— no cumple, independientemente del firmware."))
            else:
                motivo = "firmware Legacy (no UEFI)" if 'legacy' in firmware else f"Secure Boot: {secure_boot or 'desconocido'}"
                lineas.append(("⚠️", f"CPU compatible con Windows 11, pero falta: {motivo}."))
        else:
            if 'legacy' in firmware:
                lineas.append(("⚠️", "Firmware Legacy (BIOS heredado) — no cumple el requisito de UEFI de Windows 11."))
            elif 'uefi' in firmware:
                if secure_boot == 'Activado':
                    lineas.append(("ℹ️", "Firmware UEFI con Secure Boot activado — cumple estos dos requisitos, pero no se pudo verificar la CPU."))
                else:
                    lineas.append(("ℹ️", f"Firmware UEFI, pero Secure Boot: {secure_boot or 'desconocido'} — y no se pudo verificar la CPU."))

        while self.system_eval_layout.count():
            item = self.system_eval_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        if not lineas:
            self.system_eval_frame.setVisible(False)
            return

        c = self._dash_colors()
        self.system_eval_frame.setStyleSheet(f"""
            QFrame#sysEvalCard {{
                background: {c['alerts_bg']};
                border: 1px solid {c['alerts_border']};
                border-top: 3px solid #d97706;
                border-radius: 8px;
            }}
            QFrame#sysEvalCard QLabel {{ background: transparent; border: none; }}
        """)

        titulo = QLabel("📋  Evaluación de actualidad")
        titulo.setStyleSheet(f"font-size:11pt; font-weight:700; color:{c['title']};")
        self.system_eval_layout.addWidget(titulo)

        for icono, texto in lineas:
            lbl = QLabel(f"{icono}  {texto}")
            lbl.setWordWrap(True)
            lbl.setStyleSheet(f"font-size:9pt; color:{c['title']};")
            self.system_eval_layout.addWidget(lbl)

        nota = QLabel(
            "Estimación orientativa a partir de los datos de esta pestaña "
            "(no incluye disco ni TPM, disponibles en otras secciones)."
        )
        nota.setWordWrap(True)
        nota.setStyleSheet(f"font-size:8pt; font-style:italic; color:{c['muted']}; margin-top:4px;")
        self.system_eval_layout.addWidget(nota)

        self.system_eval_frame.setVisible(True)

    def _fichas_sistema(self):
        """Devuelve todas las fichas de la pestaña Sistema, estén colocadas
        directamente en la rejilla o apiladas dentro del contenedor 'combo'
        que comparte fila con la ficha ancha de RAM."""
        if not hasattr(self, 'system_cards_scroll'):
            return []
        return self.system_cards_scroll.findChildren(QFrame, "sysInfoCard")

    def _aplicar_filtro_fichas_sistema(self):
        """Muestra/oculta fichas de Sistema según el texto de búsqueda."""
        texto = self.search_system.text().strip().lower()
        for card in self._fichas_sistema():
            coincide = not texto or texto in (card.property("texto_busqueda") or "")
            card.setVisible(coincide)
            card.setProperty("oculto_por_filtro", not coincide)

    def refresh_system_info(self):
        """Actualiza información del sistema."""
        if self.worker is not None and self.worker.isRunning():
            self.set_status("⏳ Ya hay una actualización en curso, espera a que termine...")
            return

        self.set_status("🔄 Recopilando información del sistema...")
        self.btn_refresh_system.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        self.worker = SystemInfoCollector()
        self.worker.progress.connect(self._on_progress)
        self.worker.finished_ok.connect(self._on_system_info_data)
        self.worker.finished_error.connect(self._on_system_info_error)
        self.worker.finished.connect(self._actualizar_barra_exportacion)
        self.worker.start()

    def _on_system_info_data(self, data: dict):
        """Procesa datos del sistema."""
        self._last_system_info = data or {}
        self.system_tree.clear()

        if not data or 'Error' in data:
            self.system_tree.addTopLevelItem(QTreeWidgetItem(['Error', data.get('Error', 'No se pudo obtener información')]))
            while self.system_cards_grid.count():
                item = self.system_cards_grid.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            err = QLabel(f"❌ {data.get('Error', 'No se pudo obtener información')}")
            err.setStyleSheet("color:#c0392b; font-size:11pt; padding:40px;")
            err.setAlignment(Qt.AlignCenter)
            self.system_cards_grid.addWidget(err, 0, 0)
            self.btn_refresh_system.setEnabled(True)
            self.progress_bar.setVisible(False)
            self.set_status("❌ Error al obtener información del sistema")
            return

        # Computer Info
        if 'Computer' in data:
            comp = data['Computer']
            comp_item = QTreeWidgetItem(['💻 Equipo', ''])
            comp_item.addChild(QTreeWidgetItem(['Nombre', comp.get('Nombre', 'N/A')]))
            comp_item.addChild(QTreeWidgetItem(['Fabricante', comp.get('Fabricante', 'N/A')]))
            comp_item.addChild(QTreeWidgetItem(['Modelo', comp.get('Modelo', 'N/A')]))
            comp_item.addChild(QTreeWidgetItem(['Tipo', comp.get('Tipo', 'N/A')]))
            comp_item.addChild(QTreeWidgetItem(['Chasis', comp.get('FormaChasis', 'N/A')]))
            comp_item.addChild(QTreeWidgetItem(['Nº Serie', comp.get('NumeroSerie', 'N/A')]))
            comp_item.addChild(QTreeWidgetItem(['Dominio', comp.get('Dominio', 'N/A')]))
            self.system_tree.addTopLevelItem(comp_item)
            comp_item.setExpanded(True)

        # OS Info
        if 'OS' in data:
            os_data = data['OS']
            os_item = QTreeWidgetItem(['🪟 Sistema Operativo', ''])
            os_item.addChild(QTreeWidgetItem(['Nombre', os_data.get('Nombre', 'N/A')]))
            os_item.addChild(QTreeWidgetItem(['Versión', os_data.get('Version', 'N/A')]))
            os_item.addChild(QTreeWidgetItem(['Build', os_data.get('Build', 'N/A')]))
            os_item.addChild(QTreeWidgetItem(['Arquitectura', os_data.get('Arquitectura', 'N/A')]))
            os_item.addChild(QTreeWidgetItem(['Instalación', os_data.get('Instalacion', 'N/A')]))
            if os_data.get('Serial'):
                os_item.addChild(QTreeWidgetItem(['Serial', os_data.get('Serial', 'N/A')]))
            if os_data.get('EstadoActivacion'):
                os_item.addChild(QTreeWidgetItem(['Activación', os_data.get('EstadoActivacion', 'N/A')]))
            if os_data.get('CanalLicencia') and os_data.get('CanalLicencia') != 'N/A':
                os_item.addChild(QTreeWidgetItem(['Canal Licencia', os_data.get('CanalLicencia', 'N/A')]))
            self.system_tree.addTopLevelItem(os_item)
            os_item.setExpanded(True)

        # CPU Info
        if 'CPU' in data:
            cpu = data['CPU']
            cpu_item = QTreeWidgetItem(['⚙️ Procesador', ''])
            cpu_item.addChild(QTreeWidgetItem(['Nombre', cpu.get('Nombre', 'N/A')]))
            cpu_item.addChild(QTreeWidgetItem(['Fabricante', cpu.get('Fabricante', 'N/A')]))
            cpu_item.addChild(QTreeWidgetItem(['Núcleos', str(cpu.get('Nucleos', 'N/A'))]))
            cpu_item.addChild(QTreeWidgetItem(['Hilos', str(cpu.get('Hilos', 'N/A'))]))
            cpu_item.addChild(QTreeWidgetItem(['Velocidad', cpu.get('Velocidad', 'N/A')]))
            self.system_tree.addTopLevelItem(cpu_item)
            cpu_item.setExpanded(True)

        # RAM Info
        if 'RAM' in data:
            ram = data['RAM']
            ram_item = QTreeWidgetItem(['💾 Memoria RAM', f"Total: {ram.get('Total', 'N/A')}"])
            if 'Modulos' in ram and ram['Modulos']:
                for idx, mod in enumerate(ram['Modulos'], 1):
                    mod_item = QTreeWidgetItem([f'Módulo {idx}', ''])
                    mod_item.addChild(QTreeWidgetItem(['Capacidad', mod.get('Capacidad', 'N/A')]))
                    mod_item.addChild(QTreeWidgetItem(['Velocidad', mod.get('Velocidad', 'N/A')]))
                    mod_item.addChild(QTreeWidgetItem(['Tipo', mod.get('Tipo', 'N/A')]))
                    mod_item.addChild(QTreeWidgetItem(['Fabricante', mod.get('Fabricante', 'N/A')]))
                    mod_item.addChild(QTreeWidgetItem(['Ubicación', mod.get('Ubicacion', 'N/A')]))
                    ram_item.addChild(mod_item)
            self.system_tree.addTopLevelItem(ram_item)
            ram_item.setExpanded(True)

        # Motherboard Info
        if 'Motherboard' in data:
            mb = data['Motherboard']
            mb_item = QTreeWidgetItem(['🔧 Placa Base', ''])
            mb_item.addChild(QTreeWidgetItem(['Fabricante', mb.get('Fabricante', 'N/A')]))
            mb_item.addChild(QTreeWidgetItem(['Modelo', mb.get('Modelo', 'N/A')]))
            mb_item.addChild(QTreeWidgetItem(['BIOS', mb.get('Version', 'N/A')]))
            self.system_tree.addTopLevelItem(mb_item)
            mb_item.setExpanded(True)

        # BIOS / Firmware Info
        if 'Bios' in data:
            bios_data = data['Bios']
            bios_item = QTreeWidgetItem(['🔌 BIOS / Firmware', ''])
            bios_item.addChild(QTreeWidgetItem(['Fabricante', bios_data.get('Fabricante', 'N/A')]))
            bios_item.addChild(QTreeWidgetItem(['Nombre', bios_data.get('Nombre', 'N/A')]))
            bios_item.addChild(QTreeWidgetItem(['Versión', bios_data.get('Version', 'N/A')]))
            bios_item.addChild(QTreeWidgetItem(['Última actualización', bios_data.get('Fecha', 'N/A')]))
            bios_item.addChild(QTreeWidgetItem(['Antigüedad', bios_data.get('Antiguedad', 'N/A')]))
            bios_item.addChild(QTreeWidgetItem(['Firmware', bios_data.get('Firmware', 'N/A')]))
            secure_boot_item = QTreeWidgetItem(['Secure Boot', bios_data.get('SecureBoot', 'N/A')])
            if bios_data.get('SecureBoot') == 'Desactivado':
                secure_boot_item.setForeground(1, QColor('#c0392b'))
            bios_item.addChild(secure_boot_item)
            self.system_tree.addTopLevelItem(bios_item)
            bios_item.setExpanded(True)

        # Nota: discos/particiones se muestran en Discos Duros, GPU en
        # Gráficas y Monitores, y adaptadores de red en Redes — no se
        # duplican aquí.

        # Escritorio Remoto (RDP)
        if 'EscritorioRemoto' in data:
            rdp = data['EscritorioRemoto']
            rdp_item = QTreeWidgetItem(['🖧 Escritorio Remoto (RDP)', rdp.get('Habilitado', 'N/A')])
            rdp_item.addChild(QTreeWidgetItem(['Habilitado', rdp.get('Habilitado', 'N/A')]))
            nla_item = QTreeWidgetItem(['NLA (autenticación a nivel de red)', rdp.get('NLA', 'N/A')])
            if 'INACTIVO' in (rdp.get('NLA', '') or ''):
                nla_item.setForeground(1, QColor('#c0392b'))
            rdp_item.addChild(nla_item)
            rdp_item.addChild(QTreeWidgetItem(['Puerto', rdp.get('Puerto', 'N/A')]))
            self.system_tree.addTopLevelItem(rdp_item)
            rdp_item.setExpanded(True)

        self._construir_fichas_sistema(data)
        self._construir_evaluacion_actualidad(data)

        self.btn_refresh_system.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("✅ Información del sistema obtenida")

    def _on_system_info_error(self, msg: str):
        """Maneja errores del sistema."""
        self.btn_refresh_system.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.set_status("❌ Error al obtener información del sistema")
        self._avisar("Error", f"No se pudo obtener información del sistema:\n\n{msg}", QMessageBox.Warning)

    def _reiniciar_en_bios(self):
        """Reinicia el equipo directamente en la configuración de firmware.

        Sólo UEFI admite el mecanismo nativo de Windows (shutdown /r /fw).
        En BIOS heredado (Legacy) no existe ese camino desde el sistema
        operativo, así que en su lugar se indica la tecla habitual del
        fabricante para entrar manualmente durante el arranque — varía
        de un equipo a otro y no se puede automatizar.
        """
        import winreg
        es_uefi = False
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 r"SYSTEM\CurrentControlSet\Control\SecureBoot\State"):
                es_uefi = True
        except OSError:
            es_uefi = False

        computer_data = getattr(self, '_last_system_info', {}).get('Computer', {})
        fabricante_raw = computer_data.get('Fabricante') or ''
        fabricante = fabricante_raw.strip().lower()

        if not es_uefi:
            teclas_por_fabricante = {
                "dell": "F2 (configuración) / F12 (menú de arranque)",
                "hp": "F10 (configuración) / Esc (menú de arranque)",
                "lenovo": "F1 o F2 (configuración) / F12 (menú de arranque)",
                "asus": "F2 o Supr (configuración) / F8 (menú de arranque)",
                "acer": "F2 (configuración) / F12 (menú de arranque)",
                "toshiba": "F2 (configuración) / F12 (menú de arranque)",
                "msi": "Supr (configuración) / F11 (menú de arranque)",
                "gigabyte": "Supr (configuración) / F12 (menú de arranque)",
                "samsung": "F2 (configuración) / Esc (menú de arranque)",
                "fujitsu": "F2 (configuración) / F12 (menú de arranque)",
            }
            tecla_sugerida = next((t for m, t in teclas_por_fabricante.items() if m in fabricante), None)
            if tecla_sugerida:
                texto_tecla = f"\n\nPara este equipo ({fabricante_raw}), la tecla habitual es: {tecla_sugerida}."
            else:
                texto_tecla = ("\n\nLa tecla varía según el fabricante: prueba Supr, F1, F2, F10, F12 "
                                "o Esc justo al arrancar.")
            continuar = self._confirmar_accion(
                "BIOS Heredada (Legacy)",
                "Este equipo usa firmware BIOS heredado (Legacy), que no permite arrancar "
                "directamente en la configuración desde Windows — sólo UEFI lo admite."
                + texto_tecla +
                "\n\n¿Quieres reiniciar el equipo ahora para pulsar la tecla manualmente?",
                icono=QMessageBox.Warning,
            )
            if not continuar:
                return
            try:
                subprocess.run(["shutdown", "/r", "/t", "0"], creationflags=subprocess.CREATE_NO_WINDOW)
            except Exception as e:
                self._avisar("Error", f"No se pudo reiniciar el equipo:\n\n{e}", icono=QMessageBox.Critical)
            return

        continuar = self._confirmar_accion(
            "Reiniciar en BIOS/UEFI",
            "El equipo se reiniciará INMEDIATAMENTE y arrancará directamente en la "
            "configuración de firmware (BIOS/UEFI).\n\n"
            "Guarda cualquier trabajo abierto antes de continuar.\n\n"
            "¿Deseas continuar?",
            icono=QMessageBox.Warning,
        )
        if not continuar:
            return
        try:
            subprocess.run(["shutdown", "/r", "/fw", "/t", "0"], creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception as e:
            self._avisar("Error", f"No se pudo reiniciar el equipo:\n\n{e}", icono=QMessageBox.Critical)

    def filter_system_tree(self):
        """Filtra las fichas de la pestaña Sistema por texto de búsqueda."""
        self._aplicar_filtro_fichas_sistema()

    def copy_system_selection(self):
        """Copia al portapapeles las fichas de Sistema actualmente visibles
        (respeta el filtro de búsqueda). Las fichas no tienen un concepto de
        'fila seleccionada' como una tabla, así que se copia todo lo visible."""
        bloques = []
        for card in self._fichas_sistema():
            if card.property("oculto_por_filtro"):
                continue
            texto = card.property("texto_copiar")
            if texto:
                bloques.append(texto)

        if not bloques:
            self._avisar("Copiar", "No hay información para copiar")
            return

        QApplication.clipboard().setText("\n\n".join(bloques))
        self.set_status(f"📋 {len(bloques)} ficha(s) copiada(s) al portapapeles")

    def export_system_csv(self):
        """Exporta información del sistema a CSV."""
        if self.system_tree.topLevelItemCount() == 0:
            self._avisar("Exportar", "No hay datos para exportar", QMessageBox.Warning)
            return

        filename = dialogo_guardar_archivo(
            self, "Guardar CSV", ruta_guardado_predeterminada("sistema.csv"), "CSV (*.csv)")
        if not filename:
            return

        try:
            with open(filename, 'w', newline='', encoding='utf-8-sig') as f:
                writer = _SafeCsvWriter(csv.writer(f, delimiter=';'))
                writer.writerow(['Categoría', 'Componente', 'Valor'])

                for i in range(self.system_tree.topLevelItemCount()):
                    top_item = self.system_tree.topLevelItem(i)
                    category = top_item.text(0)

                    # Write top-level item
                    writer.writerow([category, '', top_item.text(1)])

                    # Write children
                    for j in range(top_item.childCount()):
                        child = top_item.child(j)
                        writer.writerow([category, child.text(0), child.text(1)])

                        # Write grandchildren
                        for k in range(child.childCount()):
                            grandchild = child.child(k)
                            writer.writerow([category, f"  {grandchild.text(0)}", grandchild.text(1)])

            self.set_status(f"✅ Sistema exportado a {filename}")
            self._avisar("Exportar", f"Datos exportados correctamente a:\n{filename}")
        except Exception as e:
            logger.exception(f"Error al exportar sistema a CSV: {e}")
            self._avisar("Error", f"Error al exportar:\n{str(e)}", QMessageBox.Critical)

