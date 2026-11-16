# -*- coding: utf-8 -*-
"""Dashboard › Resumen por módulo: 5 módulos a la vista, REDONDOS y GRANDES (ficha_modulo): con solo 5 por página
cada ficha aprovecha el espacio — círculo grande (medidor con % o cifra), valor grande, nombre, todo el detalle y
etiqueta de estado en color (4.2.5, petición: «quiero que justifique el que esten 5 por pagina»), en una fila.
A la derecha ▲ (5 anteriores), la posición y ▼ (5 siguientes). Sin scroll y en el orden de siempre.
Petición del usuario (05/10/2026): «5 modulos (COMO SIEMPRE REDONDOS) en la ventana y unos botones arriba y abajo
para pasar a los siguiente cinco o los anteriores [...] para no usar scroll»."""
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from msys.widgets.gauge import CircularGaugeWidget

POR_PAGINA = 5
OK, AVISO, ERROR, INFO = "ok", "aviso", "error", "info"
_COLOR_NIVEL = {OK: "#2e9d5b", AVISO: "#d68910", ERROR: "#d64541", INFO: "#5d6b7a"}
CIRCULO = 116          # diámetro del círculo de cada ficha (antes 66)
ALTO_FICHA = 330       # todas igual de altas, aunque unas tengan más detalle que otras


@dataclass
class Modulo:
    emoji: str
    titulo: str
    valor: str
    detalle: List[str] = field(default_factory=list)
    color: str = "#455a64"
    progreso: Optional[float] = None      # 0-100: medidor circular; None: cifra en círculo
    seccion: str = ""
    nivel: str = OK
    estado: str = ""

    @property
    def subtitulo(self) -> str:
        return "\n".join(x for x in self.detalle if x)


def ficha_modulo(m: Modulo, c: dict, oscuro: bool) -> QWidget:
    """Ficha grande de un módulo: franja de su color, círculo grande, valor, nombre, detalle y estado."""
    fondo, borde = ("#232634", "#353a4c") if oscuro else ("#ffffff", "#e3e7ee")
    f = QFrame()
    f.setObjectName("fichaModulo")
    f.setMinimumHeight(ALTO_FICHA)
    f.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
    # Borde de 1 px igual en los cuatro lados: con «border-top» de color y esquinas redondeadas, Qt pintaba trozos
    # curvos del color en las esquinas. La franja de color va dentro, como una barra propia.
    f.setStyleSheet(f"""QFrame#fichaModulo {{ background:{fondo}; border:1px solid {borde}; border-radius:12px; }}
                        QFrame#fichaModulo QLabel {{ background:transparent; border:none; }}
                        QFrame#fichaFranja {{ background:{m.color}; border:none; border-radius:2px; }}""")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(12, 8, 12, 12)
    lay.setSpacing(6)
    franja = QFrame()
    franja.setObjectName("fichaFranja")
    franja.setFixedHeight(4)
    lay.addWidget(franja)
    lay.addSpacing(4)
    if m.progreso is not None:
        v = CircularGaugeWidget(color=m.color, track_color=c['gauge_track'], text_color=c['gauge_text'], suffix="%")
        v.setFixedSize(CIRCULO, CIRCULO)
        v.set_value(m.progreso)
    else:
        v = QLabel(str(m.valor))
        v.setFixedSize(CIRCULO, CIRCULO)
        v.setAlignment(Qt.AlignCenter)
        n = len(str(m.valor))
        pt = 30 if n <= 2 else (24 if n <= 3 else (19 if n <= 5 else 15))
        v.setStyleSheet(f"QLabel {{ background:{m.color}; color:white; border-radius:{CIRCULO // 2}px; "
                        f"font-size:{pt}pt; font-weight:800; }}")
    lay.addWidget(v, 0, Qt.AlignHCenter)
    if m.progreso is not None:
        val = QLabel(str(m.valor))
        val.setAlignment(Qt.AlignHCenter)
        val.setStyleSheet(f"font-size:15pt; font-weight:800; color:{m.color};")
        lay.addWidget(val)
    t = QLabel(f"{m.emoji}  {m.titulo}")
    t.setAlignment(Qt.AlignHCenter)
    t.setWordWrap(True)
    t.setStyleSheet(f"font-size:11pt; font-weight:700; color:{c['tile_title']};")
    lay.addWidget(t)
    lineas = [x for x in m.detalle if x]
    if len(lineas) > 4:     # como mucho 4 líneas: lo que sobra se une a la última con « · »
        lineas = lineas[:3] + [" · ".join(lineas[3:])]
    for linea in lineas:
        d = QLabel(linea)
        d.setAlignment(Qt.AlignHCenter)
        d.setWordWrap(True)
        d.setStyleSheet(f"font-size:9pt; color:{c['tile_subtitle']};")
        d.setToolTip(linea)
        lay.addWidget(d)
    lay.addStretch(1)
    if m.estado:
        col = _COLOR_NIVEL.get(m.nivel, _COLOR_NIVEL[INFO])
        e = QLabel(m.estado)
        e.setAlignment(Qt.AlignCenter)
        e.setStyleSheet(f"QLabel {{ color:{col}; border:1px solid {col}; border-radius:11px; padding:3px 12px; "
                        f"font-size:9pt; font-weight:700; }}")
        lay.addWidget(e, 0, Qt.AlignHCenter)
    return f


class ListaModulos(QWidget):
    """5 tarjetas redondas en fila + columna derecha con ▲, «6–10 de 27» y ▼."""

    def __init__(self, fabrica: Callable[[Modulo], QWidget], colores: Callable[[], dict], parent=None):
        super().__init__(parent)
        self._fabrica, self._colores = fabrica, colores
        self._modulos: List[Modulo] = []
        self._inicio = 0
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self._fila = QGridLayout()
        self._fila.setSpacing(10)
        self._fila.setContentsMargins(0, 2, 0, 2)
        for c in range(POR_PAGINA):
            self._fila.setColumnStretch(c, 1)
        lay.addLayout(self._fila, 1)
        der = QVBoxLayout()
        der.setSpacing(6)
        self.btn_subir = QPushButton("▲")
        self.btn_bajar = QPushButton("▼")
        for b, tip in ((self.btn_subir, "5 módulos anteriores"), (self.btn_bajar, "5 módulos siguientes")):
            b.setFixedSize(40, 36)
            b.setToolTip(tip)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet("QPushButton { font-size: 12pt; min-height: 36px; max-height: 36px; padding: 0; }")
        self.btn_subir.clicked.connect(lambda: self.mover(-POR_PAGINA))
        self.btn_bajar.clicked.connect(lambda: self.mover(POR_PAGINA))
        self.lbl_pos = QLabel("")
        self.lbl_pos.setAlignment(Qt.AlignCenter)
        self.lbl_pos.setFixedWidth(40)
        self.lbl_pos.setWordWrap(True)
        der.addWidget(self.btn_subir)
        der.addStretch(1)
        der.addWidget(self.lbl_pos)
        der.addStretch(1)
        der.addWidget(self.btn_bajar)
        lay.addLayout(der)

    def set_modulos(self, modulos: List[Modulo]):
        self._modulos = list(modulos)
        if self._inicio >= len(self._modulos):
            self._inicio = 0
        self._pintar()

    def modulos(self) -> List[Modulo]:
        return list(self._modulos)

    def mover(self, delta: int):
        nuevo = self._inicio + delta
        if 0 <= nuevo < len(self._modulos):
            self._inicio = nuevo
            self._pintar()

    def repintar(self):
        self._pintar()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._igualar_altos()

    def _igualar_altos(self):
        """Las 5 fichas, del alto de la que más contenido tiene a su ancho real: nada se monta sobre el círculo."""
        fichas = [f for f in getattr(self, "_fichas", []) if f.parent() is not None]
        if not fichas:
            return
        ancho = max(80, (self._fila.geometry().width() - self._fila.spacing() * (POR_PAGINA - 1)) // POR_PAGINA)
        alto = ALTO_FICHA
        for f in fichas:
            lay = f.layout()
            if lay is not None:
                alto = max(alto, lay.totalHeightForWidth(ancho) if lay.hasHeightForWidth() else lay.totalSizeHint().height())
        for f in fichas:
            if f.height() != alto or f.minimumHeight() != alto:
                f.setFixedHeight(alto)

    def _pintar(self):
        while self._fila.count():
            it = self._fila.takeAt(0)
            w = it.widget()
            if w is not None:
                w.hide()            # fuera ya: deleteLater tarda y se verían las 5 de antes encima
                w.setParent(None)
                w.deleteLater()
        c = self._colores()
        visibles = self._modulos[self._inicio:self._inicio + POR_PAGINA]
        self._fichas = []
        for i, m in enumerate(visibles):
            w = self._fabrica(m)
            self._fichas.append(w)
            self._fila.addWidget(w, 0, i, Qt.AlignTop)
        QTimer.singleShot(0, self._igualar_altos)
        for i in range(len(visibles), POR_PAGINA):      # huecos: las 5 columnas no cambian de ancho
            self._fila.addWidget(QWidget(), 0, i)
        if not self._modulos:
            vacio = QLabel("No hay datos cargados.\n\nUsa 🔄 Sincronizar Todo para cargar todos los módulos,\nluego pulsa 🔄 Actualizar.")
            vacio.setStyleSheet("font-size:12pt; color:#6c757d; padding:40px;")
            vacio.setAlignment(Qt.AlignCenter)
            self._fila.addWidget(vacio, 0, 0, 1, POR_PAGINA)
        n = len(self._modulos)
        self.btn_subir.setEnabled(self._inicio > 0)
        self.btn_bajar.setEnabled(self._inicio + POR_PAGINA < n)
        fin = min(n, self._inicio + POR_PAGINA)
        self.lbl_pos.setText(f"{self._inicio + 1}–{fin}\nde {n}" if n else "")
        self.lbl_pos.setStyleSheet(f"font-size:8pt; color:{c['muted']};")
