# -*- coding: utf-8 -*-
"""Diálogo de resultados para el buscador de archivos duplicados grandes
— diálogo autónomo (no es un mixin), se instancia bajo demanda desde
SoporteMixin._soporte_buscar_duplicados, con su propio hilo
(DuplicateFilesFinderThread).

Deliberadamente NO borra nada automáticamente: el hilo solo propone
grupos de archivos idénticos byte a byte (confirmados por hash, no solo
por tamaño), y aquí el técnico marca con checkbox qué copias concretas
enviar a la papelera — todas las casillas empiezan DESmarcadas, nunca se
preselecciona "todas menos la primera" porque cuál es la copia a
conservar es una decisión que depende de la ruta (backup vs original,
etc.), no algo que este diálogo pueda decidir por el técnico."""

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QProgressBar, QTreeWidget,
    QTreeWidgetItem, QPushButton, QMessageBox,
)

from msys.collectors import DuplicateFilesFinderThread, ContentTrashThread, _tam_legible


class DuplicateFilesDialog(QDialog):
    def __init__(self, raiz: str, min_size_bytes: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Archivos duplicados grandes — {raiz}")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(760, 520)
        self._raiz = raiz
        self._min_size = min_size_bytes
        self._thread = None
        self._trash_thread = None
        self.grupos = []

        layout = QVBoxLayout(self)

        info = QLabel(
            f"Buscando archivos duplicados de {_tam_legible(min_size_bytes)} o más en {raiz}\n"
            "Se comparan por tamaño y luego por contenido real (hash) antes de proponer "
            "nada — revisa y marca tú qué copias enviar a la papelera; nada se borra "
            "automáticamente."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        self.lbl_estado = QLabel("🔄 Iniciando búsqueda...")
        self.lbl_estado.setObjectName("fieldLabel")
        layout.addWidget(self.lbl_estado)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # indeterminado durante la búsqueda
        layout.addWidget(self.progress)

        self.arbol = QTreeWidget()
        self.arbol.setHeaderLabels(["Archivo", "Tamaño"])
        self.arbol.setColumnWidth(0, 560)
        layout.addWidget(self.arbol, 1)

        botones = QHBoxLayout()
        self.btn_detener = QPushButton("⏹ Detener búsqueda")
        self.btn_detener.clicked.connect(self._detener_busqueda)
        botones.addWidget(self.btn_detener)
        botones.addStretch()
        self.btn_enviar = QPushButton("✕ Enviar seleccionados a la papelera")
        self.btn_enviar.setEnabled(False)
        self.btn_enviar.clicked.connect(self._enviar_seleccionados)
        botones.addWidget(self.btn_enviar)
        self.btn_cerrar = QPushButton("Cerrar")
        self.btn_cerrar.clicked.connect(self.accept)
        botones.addWidget(self.btn_cerrar)
        layout.addLayout(botones)

        QTimer.singleShot(0, self._iniciar)

    def _iniciar(self):
        self._thread = DuplicateFilesFinderThread(self._raiz, self._min_size)
        self._thread.progreso.connect(self._on_progreso)
        self._thread.finished_ok.connect(self._on_finalizado)
        self._thread.finished_error.connect(self._on_error)
        self._thread.start()

    def _on_progreso(self, mensaje: str):
        self.lbl_estado.setText(f"🔄 {mensaje}")

    def _detener_busqueda(self):
        if self._thread is not None and self._thread.isRunning():
            self._thread.request_stop()
            self.btn_detener.setEnabled(False)

    def _on_finalizado(self, grupos: list):
        self.grupos = grupos
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        self.btn_detener.setEnabled(False)
        self.btn_detener.setVisible(False)

        self.arbol.clear()
        if not grupos:
            self.lbl_estado.setText("✅ No se encontraron duplicados confirmados")
            return

        for grupo in grupos:
            nodo = QTreeWidgetItem(self.arbol)
            nodo.setText(0, f"{len(grupo['rutas'])} copias idénticas")
            nodo.setText(1, _tam_legible(grupo["tamano"]))
            nodo.setExpanded(True)
            for ruta in grupo["rutas"]:
                hijo = QTreeWidgetItem(nodo)
                hijo.setFlags(hijo.flags() | Qt.ItemIsUserCheckable)
                hijo.setCheckState(0, Qt.Unchecked)
                hijo.setText(0, ruta)
                hijo.setText(1, _tam_legible(grupo["tamano"]))

        # Espacio recuperable si de cada grupo se deja solo UNA copia.
        total_recuperable = sum(g["tamano"] * (len(g["rutas"]) - 1) for g in grupos)
        self.lbl_estado.setText(
            f"✅ {len(grupos)} grupo(s) de duplicados — hasta {_tam_legible(total_recuperable)} "
            "recuperables si dejas solo una copia de cada grupo")
        self.btn_enviar.setEnabled(True)

    def _on_error(self, mensaje: str):
        self.progress.setRange(0, 100)
        self.lbl_estado.setText(f"❌ {mensaje}")
        self.btn_detener.setEnabled(False)

    def _rutas_seleccionadas(self) -> list:
        rutas = []
        for i in range(self.arbol.topLevelItemCount()):
            nodo = self.arbol.topLevelItem(i)
            for j in range(nodo.childCount()):
                hijo = nodo.child(j)
                if hijo.checkState(0) == Qt.Checked:
                    rutas.append(hijo.text(0))
        return rutas

    def _enviar_seleccionados(self):
        rutas = self._rutas_seleccionadas()
        if not rutas:
            self._avisar("Duplicados", "No has marcado ningún archivo.")
            return

        cambios = [f"Enviar «{r}» a la papelera de reciclaje" for r in rutas[:8]]
        if len(rutas) > 8:
            cambios.append(f"… y {len(rutas) - 8} más")

        if not self.parent()._confirmar_reparacion(
            "Enviar duplicados a la papelera",
            cambios=cambios,
            reversible=True,
            objetivo=f"{len(rutas)} archivo(s)",
        ):
            return

        self.btn_enviar.setEnabled(False)
        self.progress.setRange(0, len(rutas))
        self.progress.setValue(0)

        self._trash_thread = ContentTrashThread(rutas)
        self._trash_thread.progreso.connect(lambda actual, total, nombre: self.progress.setValue(actual))
        self._trash_thread.finished_ok.connect(self._on_enviado_papelera)
        self._trash_thread.start()

    def _on_enviado_papelera(self, enviadas: list, errores: list):
        enviadas_set = set(enviadas)
        for i in reversed(range(self.arbol.topLevelItemCount())):
            nodo = self.arbol.topLevelItem(i)
            for j in reversed(range(nodo.childCount())):
                hijo = nodo.child(j)
                if hijo.text(0) in enviadas_set:
                    nodo.removeChild(hijo)
            if nodo.childCount() <= 1:
                self.arbol.takeTopLevelItem(i)

        audit_db = getattr(self.parent(), "repair_audit_db", None)
        if audit_db:
            audit_db.registrar(
                seccion="💽 Discos Duros", accion="Enviar duplicados a la papelera",
                objetivo=f"{len(enviadas)} archivo(s)",
                exito=(bool(enviadas) and not errores),
                detalle="; ".join(errores) if errores else "OK",
            )

        if errores:
            self._avisar("Duplicados", "Algunos archivos no se pudieron enviar a la papelera:\n\n" + "\n".join(errores), QMessageBox.Warning)
        self.lbl_estado.setText(f"✅ {len(enviadas)} archivo(s) enviado(s) a la papelera")
        self.btn_enviar.setEnabled(True)

    def closeEvent(self, event):
        if self._thread is not None and self._thread.isRunning():
            self._thread.request_stop()
            self._thread.wait(5000)
        if self._trash_thread is not None and self._trash_thread.isRunning():
            self._trash_thread.wait(5000)
        event.accept()
