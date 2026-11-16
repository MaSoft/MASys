# -*- coding: utf-8 -*-
"""Diálogo "Gestionar Papelera de Reciclaje" — acción de Soporte de la
sección Contenido (ver msys/ui/soporte.py, _registro_soporte_por_seccion).
Diálogo autónomo (no es un mixin), mismo patrón que SFCDISMDialog: se
instancia bajo demanda. Dos hilos propios en msys/collectors.py:
RecycleBinListThread (listado, puede recorrer miles de elementos) y
RecycleBinMoveThread (mover a otra ubicación, puede tardar con carpetas
grandes o cruces de unidad). Restaurar/vaciar/eliminar permanentemente se
hacen en el hilo de UI directamente porque son operaciones COM rápidas
sobre pocos elementos.

Todas las confirmaciones pasan por self.parent()._confirmar_reparacion()
— la ventana estándar de MASys para cualquier acción de mantenimiento
sensible (multisys.py), no un QMessageBox.question() suelto — y se
auditan en self.parent().repair_audit_db, igual que el resto de acciones
de Soporte."""

import ctypes
import os

import pythoncom
import win32com.client

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTableWidget,
    QTableWidgetItem, QMessageBox, QAbstractItemView, QLineEdit, QProgressBar,
)

from msys.collectors import RecycleBinListThread, RecycleBinMoveThread
from msys.core.utils import dialogo_elegir_carpeta, preguntar_colision
from msys.widgets.delegates import _setup_header

SHERB_NOCONFIRMATION = 0x00000001
SHERB_NOPROGRESSUI = 0x00000002
SHERB_NOSOUND = 0x00000004


class RecycleBinDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("🗑️ Papelera de Reciclaje")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(820, 480)
        self._thread = None
        self._hilo_mover = None
        self._filas = []

        v = QVBoxLayout(self)

        self.lbl_estado = QLabel("Cargando contenido de la Papelera...")
        v.addWidget(self.lbl_estado)

        fila_filtro = QHBoxLayout()
        fila_filtro.addWidget(QLabel("🔍 Filtrar:"))
        self.txt_filtro = QLineEdit()
        self.txt_filtro.setPlaceholderText("Nombre o ubicación original...")
        self.txt_filtro.textChanged.connect(self._aplicar_filtro)
        fila_filtro.addWidget(self.txt_filtro, 1)
        v.addLayout(fila_filtro)

        self.tabla = QTableWidget(0, 4)
        self.tabla.setHorizontalHeaderLabels(["Nombre", "Ubicación original", "Tamaño", "Eliminado"])
        self.tabla.setEditTriggers(QTableWidget.NoEditTriggers)
        self.tabla.setSelectionBehavior(QTableWidget.SelectRows)
        self.tabla.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.setSortingEnabled(True)
        _setup_header(self.tabla, 4, stretch_col=1)
        v.addWidget(self.tabla, 1)

        botones = QHBoxLayout()
        self.btn_actualizar = QPushButton("🔄 Actualizar")
        self.btn_actualizar.clicked.connect(self._cargar)
        botones.addWidget(self.btn_actualizar)
        self.btn_restaurar = QPushButton("↩ Restaurar")
        self.btn_restaurar.clicked.connect(self._restaurar_seleccionados)
        botones.addWidget(self.btn_restaurar)
        self.btn_restaurar_otra = QPushButton("📂 Restaurar en otra ubicación...")
        self.btn_restaurar_otra.clicked.connect(self._restaurar_en_otra_ubicacion)
        botones.addWidget(self.btn_restaurar_otra)
        botones.addStretch()
        self.btn_eliminar_sel = QPushButton("✕ Eliminar seleccionados")
        self.btn_eliminar_sel.setStyleSheet("QPushButton { color: #dc2626; }")
        self.btn_eliminar_sel.clicked.connect(self._eliminar_seleccionados)
        botones.addWidget(self.btn_eliminar_sel)
        self.btn_vaciar = QPushButton("✕ Vaciar papelera")
        self.btn_vaciar.setStyleSheet(
            "QPushButton { background-color: #dc2626; color: white; padding: 5px 12px; }"
            "QPushButton:hover { background-color: #b91c1c; }")
        self.btn_vaciar.clicked.connect(self._vaciar_papelera)
        botones.addWidget(self.btn_vaciar)
        self.btn_cerrar = QPushButton("Cerrar")
        self.btn_cerrar.clicked.connect(self.accept)
        botones.addWidget(self.btn_cerrar)
        v.addLayout(botones)

        fila_progreso = QHBoxLayout()
        self.progreso = QProgressBar()
        self.progreso.setVisible(False)
        fila_progreso.addWidget(self.progreso, 1)
        self.btn_detener_mover = QPushButton("⏹ Detener")
        self.btn_detener_mover.setVisible(False)
        self.btn_detener_mover.clicked.connect(self._detener_mover)
        fila_progreso.addWidget(self.btn_detener_mover)
        v.addLayout(fila_progreso)

        QTimer.singleShot(0, self._cargar)

    # ── Carga ────────────────────────────────────────────────────────

    def _cargar(self):
        if self._thread is not None and self._thread.isRunning():
            return
        self.lbl_estado.setText("Cargando contenido de la Papelera...")
        self._set_botones_habilitados(False)
        self.tabla.setRowCount(0)

        self._thread = RecycleBinListThread()
        self._thread.finished_ok.connect(self._on_cargado)
        self._thread.finished_error.connect(self._on_error)
        self._thread.start()

    def _set_botones_habilitados(self, habilitados: bool):
        self.btn_actualizar.setEnabled(habilitados)
        self.btn_restaurar.setEnabled(habilitados)
        self.btn_restaurar_otra.setEnabled(habilitados)
        self.btn_eliminar_sel.setEnabled(habilitados)
        self.btn_vaciar.setEnabled(habilitados)

    def _aplicar_filtro(self, texto: str = None):
        texto = (self.txt_filtro.text() if texto is None else texto).strip().lower()
        for fila in range(self.tabla.rowCount()):
            if not texto:
                self.tabla.setRowHidden(fila, False)
                continue
            nombre = self.tabla.item(fila, 0).text().lower()
            ubicacion = self.tabla.item(fila, 1).text().lower()
            self.tabla.setRowHidden(fila, texto not in nombre and texto not in ubicacion)

    def _on_error(self, mensaje: str):
        self._set_botones_habilitados(True)
        self.lbl_estado.setText(f"❌ {mensaje}")
        self._avisar("Papelera de Reciclaje", mensaje, QMessageBox.Warning)

    def _on_cargado(self, filas: list):
        self._filas = filas
        self._set_botones_habilitados(True)

        self.tabla.setSortingEnabled(False)
        self.tabla.setRowCount(len(filas))
        for fila_idx, fila in enumerate(filas):
            icono = "📁" if fila["es_carpeta"] else "📄"
            item_nombre = QTableWidgetItem(f"{icono} {fila['nombre']}")
            item_nombre.setData(Qt.UserRole, fila)
            self.tabla.setItem(fila_idx, 0, item_nombre)
            self.tabla.setItem(fila_idx, 1, QTableWidgetItem(fila["ubicacion_original"]))
            self.tabla.setItem(fila_idx, 2, QTableWidgetItem(fila["tamano"]))
            self.tabla.setItem(fila_idx, 3, QTableWidgetItem(fila["fecha_eliminacion"]))
        self.tabla.setSortingEnabled(True)
        self._aplicar_filtro()

        if filas:
            self.lbl_estado.setText(f"✅ {len(filas)} elemento(s) en la Papelera")
        else:
            self.lbl_estado.setText("✅ La Papelera está vacía")

    # ── Selección ────────────────────────────────────────────────────

    def _filas_seleccionadas(self):
        filas = []
        for idx in self.tabla.selectionModel().selectedRows():
            item = self.tabla.item(idx.row(), 0)
            if item:
                filas.append(item.data(Qt.UserRole))
        return filas

    def _lista_cambios(self, seleccion, sufijo=""):
        cambios = [f"Restaurar «{f['nombre']}» → {f['ubicacion_original']}{sufijo}" for f in seleccion[:8]]
        if len(seleccion) > 8:
            cambios.append(f"… y {len(seleccion) - 8} más")
        return cambios

    def _originales_faltantes(self, seleccion) -> list:
        """Nombres cuya carpeta original ya no existe — Windows normalmente
        la recrea al restaurar, pero si eso falla para alguno, esta es la
        causa más probable; se avisa por adelantado en vez de dejar que el
        único síntoma sea un «no se encontró el verbo Restaurar» confuso."""
        return [f["nombre"] for f in seleccion
                if f["ubicacion_original"] and not os.path.isdir(f["ubicacion_original"])]

    def _texto_originales_faltantes(self, seleccion) -> str:
        faltantes = self._originales_faltantes(seleccion)
        if not faltantes:
            return ""
        lista = ", ".join(faltantes[:5]) + (f" y {len(faltantes) - 5} más" if len(faltantes) > 5 else "")
        return f"\n\n⚠️ La carpeta original ya no existe para: {lista}."

    def _validar_destino(self, seleccion, destino: str) -> str:
        """Devuelve un mensaje de error si 'destino' coincide con la propia
        ubicación original de algún elemento seleccionado — restaurar y
        mover al mismo sitio del que vino provocaría que la resolución de
        colisión 'sobrescribir' borrase el propio origen antes de moverlo.
        No se comprueba con os.access (poco fiable para ACLs reales en
        Windows); un fallo real de permisos se recoge igual como error de
        shutil.move al intentar mover."""
        destino_norm = os.path.normcase(os.path.abspath(destino))
        coincidencias = [
            f["nombre"] for f in seleccion
            if f["ubicacion_original"]
            and os.path.normcase(os.path.abspath(f["ubicacion_original"])) == destino_norm
        ]
        if not coincidencias:
            return ""
        lista = ", ".join(coincidencias[:5]) + (f" y {len(coincidencias) - 5} más" if len(coincidencias) > 5 else "")
        return (
            f"El destino elegido es la misma carpeta original de: {lista}.\n"
            "Usa «↩ Restaurar» en vez de «Restaurar en otra ubicación» para este caso."
        )

    def _auditar(self, accion: str, objetivo: str, exito: bool, detalle: str):
        audit_db = getattr(self.parent(), "repair_audit_db", None)
        if audit_db:
            audit_db.registrar(seccion="Contenido", accion=accion, objetivo=objetivo,
                                exito=exito, detalle=detalle)

    # ── Restaurar a la ubicación original ────────────────────────────

    def _ejecutar_restauracion(self, seleccion: list):
        """Restaura cada elemento de 'seleccion' a su ubicación original
        vía el verbo de shell 'Restaurar'. Devuelve (filas_restauradas,
        errores) — filas_restauradas son las que sí se movieron de verdad,
        para que quien llame (p. ej. mover a otra ubicación después)
        sepa exactamente sobre cuáles seguir operando."""
        rutas_objetivo = {f["ruta_papelera"] for f in seleccion}
        por_ruta = {f["ruta_papelera"]: f for f in seleccion}
        restauradas = []
        errores = []

        pythoncom.CoInitialize()
        try:
            shell = win32com.client.Dispatch("Shell.Application")
            papelera = shell.Namespace(10)
            for item in papelera.Items():
                try:
                    if item.Path not in rutas_objetivo:
                        continue
                    # InvokeVerb("restore") NO funciona: el nombre del verbo
                    # está localizado ('&Restaurar' en Windows en español,
                    # con mnemónico '&' incluido), no es el literal inglés
                    # "restore". Hay que recorrer item.Verbs(), quitar el
                    # '&' y buscar por coincidencia de texto (es/en), y
                    # ejecutar con .DoIt() — comprobado en vivo: InvokeVerb
                    # no lanzaba error pero tampoco restauraba nada.
                    restaurado = False
                    for verbo in item.Verbs():
                        nombre_verbo = (verbo.Name or "").replace("&", "").strip().lower()
                        if "restaur" in nombre_verbo or "restore" in nombre_verbo:
                            verbo.DoIt()
                            restaurado = True
                            break
                    if restaurado:
                        restauradas.append(por_ruta[item.Path])
                    else:
                        errores.append(f"{item.Name}: no se encontró el verbo 'Restaurar'")
                except Exception as e:
                    errores.append(f"{item.Name}: {e}")
        except Exception as e:
            errores.append(str(e))
        finally:
            pythoncom.CoUninitialize()

        return restauradas, errores

    def _restaurar_seleccionados(self):
        seleccion = self._filas_seleccionadas()
        if not seleccion:
            self._avisar("Papelera de Reciclaje", "Selecciona al menos un elemento.")
            return

        if not self.parent()._confirmar_reparacion(
            "Restaurar elementos de la Papelera",
            cambios=self._lista_cambios(seleccion),
            reversible=True,
            objetivo=f"{len(seleccion)} elemento(s)",
            explicacion=self._texto_originales_faltantes(seleccion) or None,
        ):
            return

        restauradas, errores = self._ejecutar_restauracion(seleccion)

        self._auditar(
            "Restaurar de la Papelera", f"{len(restauradas)} elemento(s)",
            exito=(bool(restauradas) and not errores),
            detalle="; ".join(errores) if errores else "OK",
        )

        if errores:
            self._avisar("Papelera de Reciclaje", "Algunos elementos no se pudieron restaurar:\n\n" + "\n".join(errores), QMessageBox.Warning)
        self.lbl_estado.setText(f"↩ {len(restauradas)} elemento(s) restaurado(s)")
        # El verbo de shell encola la operación — pequeño margen antes de
        # releer la Papelera para que ya se refleje.
        QTimer.singleShot(800, self._cargar)

    # ── Restaurar en otra ubicación ──────────────────────────────────

    def _restaurar_en_otra_ubicacion(self):
        seleccion = self._filas_seleccionadas()
        if not seleccion:
            self._avisar("Papelera de Reciclaje", "Selecciona al menos un elemento.")
            return

        destino = dialogo_elegir_carpeta(self, "Elegir carpeta de destino", "C:\\")
        if not destino:
            return

        error_destino = self._validar_destino(seleccion, destino)
        if error_destino:
            self._avisar("Papelera de Reciclaje", error_destino, QMessageBox.Warning)
            return

        explicacion = (
            "Windows solo sabe restaurar un elemento a su ubicación original. "
            "Para dejarlo en otro sitio, MASys lo restaura primero ahí y "
            "luego lo mueve a la carpeta elegida."
        ) + self._texto_originales_faltantes(seleccion)

        if not self.parent()._confirmar_reparacion(
            "Restaurar en otra ubicación",
            cambios=self._lista_cambios(seleccion, sufijo=" (temporalmente)") + [f"Mover todo a: {destino}"],
            reversible=True,
            objetivo=f"{len(seleccion)} elemento(s) → {destino}",
            explicacion=explicacion,
        ):
            return

        restauradas, errores = self._ejecutar_restauracion(seleccion)
        self.lbl_estado.setText(f"↩ Restaurando {len(restauradas)} elemento(s), moviendo a {destino}...")
        # Igual que en _restaurar_seleccionados: dar tiempo al shell antes
        # de intentar mover lo recién restaurado.
        QTimer.singleShot(800, lambda: self._resolver_colisiones_y_mover(restauradas, errores, destino))

    def _resolver_colisiones_y_mover(self, restauradas: list, errores_previos: list, destino: str):
        """Resuelve TODAS las colisiones de nombre antes de lanzar el hilo
        de movimiento: preguntar_colision es una ventana y no puede
        llamarse desde dentro de un QThread, así que aquí se decide (en el
        hilo de UI, rápido) y el hilo solo aplica lo ya decidido."""
        decisiones = {}
        accion_para_todos = None
        for fila in restauradas:
            destino_final = os.path.join(destino, fila["nombre"])
            if not os.path.exists(destino_final):
                continue
            accion = accion_para_todos
            if accion is None:
                accion, aplicar_a_todos = preguntar_colision(self, fila["nombre"])
                if accion is None:
                    # Cancelar el resto de conflictos: se omiten (quedan
                    # restaurados en origen, no se mueven), no se cancela
                    # lo que ya se decidió para elementos anteriores.
                    accion = "omitir"
                    accion_para_todos = "omitir"
                elif aplicar_a_todos:
                    accion_para_todos = accion
            decisiones[fila["nombre"]] = accion

        self._lanzar_hilo_mover(restauradas, errores_previos, destino, decisiones)

    def _lanzar_hilo_mover(self, restauradas: list, errores_previos: list, destino: str, decisiones: dict):
        if not restauradas:
            self._on_mover_terminado(0, 0, errores_previos, destino)
            return

        self._set_botones_habilitados(False)
        self.progreso.setRange(0, len(restauradas))
        self.progreso.setValue(0)
        self.progreso.setVisible(True)
        self.btn_detener_mover.setVisible(True)
        self.btn_detener_mover.setEnabled(True)

        self._hilo_mover = RecycleBinMoveThread(restauradas, destino, decisiones)
        self._hilo_mover.progreso.connect(self._on_progreso_mover)
        self._hilo_mover.finished_ok.connect(
            lambda movidas, omitidas, errores:
                self._on_mover_terminado(movidas, omitidas, errores_previos + errores, destino))
        self._hilo_mover.start()

    def _on_progreso_mover(self, actual: int, total: int, nombre: str):
        self.progreso.setValue(actual)
        self.lbl_estado.setText(f"📂 Moviendo {actual}/{total}: {nombre}")

    def _detener_mover(self):
        """El hilo solo comprueba la señal de parada ENTRE elementos, no a
        mitad de mover uno solo — si el elemento actual es una carpeta muy
        grande o cruza de unidad, Detener no corta esa copia ya en curso,
        solo evita empezar la siguiente."""
        if self._hilo_mover is not None:
            self._hilo_mover.request_stop()
            self.btn_detener_mover.setEnabled(False)
            self.lbl_estado.setText("⏹ Deteniendo tras el elemento en curso...")

    def _on_mover_terminado(self, movidas: int, omitidas: int, errores: list, destino: str):
        self.progreso.setVisible(False)
        self.btn_detener_mover.setVisible(False)
        self._set_botones_habilitados(True)
        self._hilo_mover = None

        self._auditar(
            "Restaurar de la Papelera a otra ubicación",
            f"{movidas} elemento(s) → {destino}" + (f" ({omitidas} omitido(s))" if omitidas else ""),
            exito=(movidas > 0 and not errores),
            detalle="; ".join(errores) if errores else "OK",
        )

        if errores:
            self._avisar("Papelera de Reciclaje", "Algunos elementos no se pudieron mover:\n\n" + "\n".join(errores), QMessageBox.Warning)
        sufijo_omitidas = f", {omitidas} omitido(s)" if omitidas else ""
        self.lbl_estado.setText(f"📂 {movidas} elemento(s) restaurado(s) en {destino}{sufijo_omitidas}")
        self._cargar()

    # ── Eliminar seleccionados permanentemente ────────────────────────

    def _eliminar_permanentemente_com(self, seleccion: list):
        """Mismo patrón que _ejecutar_restauracion pero invocando el verbo
        de shell 'Eliminar' en vez de 'Restaurar'. Aviso: a diferencia de
        SHEmptyRecycleBinW (que sí acepta SHERB_NOCONFIRMATION), este
        camino puede hacer que Windows muestre su propio diálogo nativo de
        confirmación — el mismo que aparece al pulsar Supr sobre un
        elemento ya dentro de la Papelera. Evitarlo del todo requeriría
        IFileOperation de bajo nivel (COM no automatizable vía Dispatch),
        no justificado para esta acción puntual sobre pocos elementos."""
        rutas_objetivo = {f["ruta_papelera"] for f in seleccion}
        eliminadas = []
        errores = []

        pythoncom.CoInitialize()
        try:
            shell = win32com.client.Dispatch("Shell.Application")
            papelera = shell.Namespace(10)
            for item in papelera.Items():
                try:
                    if item.Path not in rutas_objetivo:
                        continue
                    eliminado = False
                    for verbo in item.Verbs():
                        nombre_verbo = (verbo.Name or "").replace("&", "").strip().lower()
                        if "eliminar" in nombre_verbo or "delete" in nombre_verbo:
                            verbo.DoIt()
                            eliminado = True
                            break
                    if eliminado:
                        eliminadas.append(item.Name)
                    else:
                        errores.append(f"{item.Name}: no se encontró el verbo 'Eliminar'")
                except Exception as e:
                    errores.append(f"{item.Name}: {e}")
        except Exception as e:
            errores.append(str(e))
        finally:
            pythoncom.CoUninitialize()

        return eliminadas, errores

    def _eliminar_seleccionados(self):
        seleccion = self._filas_seleccionadas()
        if not seleccion:
            self._avisar("Papelera de Reciclaje", "Selecciona al menos un elemento.")
            return

        cambios = [f"Eliminar definitivamente «{f['nombre']}»" for f in seleccion[:8]]
        if len(seleccion) > 8:
            cambios.append(f"… y {len(seleccion) - 8} más")

        if not self.parent()._confirmar_reparacion(
            "Eliminar permanentemente de la Papelera",
            cambios=cambios,
            reversible=False,
            objetivo=f"{len(seleccion)} elemento(s)",
            explicacion=(
                "Esta acción no se puede deshacer: los archivos no podrán recuperarse. "
                "Windows puede mostrar además su propio aviso de confirmación al ejecutarla."
            ),
            es_alerta=True,
        ):
            return

        eliminadas, errores = self._eliminar_permanentemente_com(seleccion)

        self._auditar(
            "Eliminar permanentemente de la Papelera", f"{len(eliminadas)} elemento(s)",
            exito=(bool(eliminadas) and not errores),
            detalle="; ".join(errores) if errores else "OK",
        )

        if errores:
            self._avisar("Papelera de Reciclaje", "Algunos elementos no se pudieron eliminar:\n\n" + "\n".join(errores), QMessageBox.Warning)
        self.lbl_estado.setText(f"✕ {len(eliminadas)} elemento(s) eliminado(s) permanentemente")
        QTimer.singleShot(800, self._cargar)

    # ── Vaciar ───────────────────────────────────────────────────────

    def _vaciar_papelera(self):
        if not self._filas:
            self._avisar("Papelera de Reciclaje", "La Papelera ya está vacía.")
            return

        if not self.parent()._confirmar_reparacion(
            "Vaciar Papelera de Reciclaje",
            cambios=[f"Eliminar definitivamente los {len(self._filas)} elemento(s) de la Papelera"],
            reversible=False,
            objetivo="Papelera de Reciclaje completa",
            explicacion="Esta acción no se puede deshacer: los archivos no podrán recuperarse.",
            es_alerta=True,
        ):
            return

        resultado = ctypes.windll.shell32.SHEmptyRecycleBinW(
            None, None, SHERB_NOCONFIRMATION | SHERB_NOPROGRESSUI | SHERB_NOSOUND)

        exito = (resultado == 0)
        self._auditar("Vaciar Papelera de Reciclaje", f"{len(self._filas)} elemento(s)",
                       exito=exito, detalle="OK" if exito else f"Código de error: {resultado}")

        if not exito:
            self._avisar("Papelera de Reciclaje", f"No se pudo vaciar la Papelera (código {resultado}).", QMessageBox.Warning)
        self._cargar()

    # ── Cierre ───────────────────────────────────────────────────────

    def closeEvent(self, event):
        if self._thread is not None and self._thread.isRunning():
            self._thread.wait(3000)
        if self._hilo_mover is not None and self._hilo_mover.isRunning():
            self._hilo_mover.request_stop()
            self._hilo_mover.wait(5000)
        event.accept()
