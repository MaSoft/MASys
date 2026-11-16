# -*- coding: utf-8 -*-
"""Ventana «👥 Empresas» (5.0.0): catálogo de empresas y usuarios de los clientes (msys/domain/clientes.py).

Árbol Empresa › Usuario con correo, teléfono y notas; búsqueda; alta, edición y baja. Quitar algo del catálogo no
toca la agenda de Acceso remoto: cada equipo guarda su propia copia de la empresa y el usuario."""

from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QApplication, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from msys.domain import clientes as cl
from msys.ui import piezas
from msys.ui.conocimiento_dialog import _IconosCabecera

_ROL_TIPO = Qt.UserRole           # "empresa" / "usuario"
_ROL_ID = Qt.UserRole + 1
_ROL_BUSCAR = Qt.UserRole + 2
COLUMNAS = ["Nombre", "Correo", "Teléfono", "Notas"]


def _caja(padre, texto: str, icono=QMessageBox.Information, titulo: str = "Empresas") -> QMessageBox:
    caja = QMessageBox(padre)
    caja.setWindowFlags(caja.windowFlags() & ~Qt.WindowContextHelpButtonHint)
    caja.setWindowTitle(titulo)
    caja.setText(texto)
    caja.setIcon(icono)
    return caja


class FichaDialog(QDialog):
    """Alta o edición de una empresa (usuario=None) o de un usuario."""

    def __init__(self, padre, db: cl.ClientesDB, empresa: Optional[cl.Empresa] = None,
                 usuario: Optional[cl.Usuario] = None, es_usuario: bool = False, empresa_inicial: str = ""):
        super().__init__(padre)
        self.db, self.empresa_obj, self.usuario_obj = db, empresa, usuario
        self.es_usuario = es_usuario or usuario is not None
        self.resultado = None
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        editar = usuario is not None or empresa is not None
        if self.es_usuario:
            self.setWindowTitle("Editar usuario" if editar else "Nuevo usuario")
        else:
            self.setWindowTitle("Editar empresa" if editar else "Nueva empresa")
        self.setMinimumWidth(440)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(6)

        def campo(titulo, w):
            lay.addWidget(QLabel(titulo))
            lay.addWidget(w)
            return w

        if self.es_usuario:
            self.ed_empresa = campo("Empresa", piezas.combo())
            empresas = db.empresas()
            for e in empresas:
                self.ed_empresa.addItem(e.nombre, e.id)
            actual = usuario.empresa if usuario else empresa_inicial
            if actual:
                self.ed_empresa.setCurrentIndex(max(self.ed_empresa.findText(actual), 0))
            self.ed_nombre = campo("Usuario", QLineEdit(usuario.nombre if usuario else ""))
            self.ed_correo = campo("Correo", QLineEdit(usuario.correo if usuario else ""))
            self.ed_correo.setPlaceholderText("Opcional")
            self.ed_telefono = campo("Teléfono", QLineEdit(usuario.telefono if usuario else ""))
            self.ed_telefono.setPlaceholderText("Opcional")
        else:
            self.ed_nombre = campo("Empresa", QLineEdit(empresa.nombre if empresa else ""))
        notas = (usuario.notas if usuario else empresa.notas if empresa else "")
        self.ed_notas = campo("Notas", QPlainTextEdit(notas))
        self.ed_notas.setFixedHeight(70)
        guardar = piezas.boton("💾 Guardar", "primary", self.guardar)
        cancelar = piezas.boton("Cancelar", "ghost", self.reject)
        lay.addLayout(piezas.fila(None, cancelar, guardar, estirar=False))
        self.ed_nombre.setFocus()

    def guardar(self):
        try:
            if self.es_usuario:
                if self.ed_empresa.currentData() is None:
                    raise ValueError("Crea antes la empresa")
                u = cl.Usuario(self.usuario_obj.id if self.usuario_obj else 0, self.ed_empresa.currentData(),
                               self.ed_nombre.text(), self.ed_correo.text(), self.ed_telefono.text(),
                               self.ed_notas.toPlainText())
                self.resultado = self.db.guardar_usuario(u)
            else:
                self.resultado = self.db.guardar_empresa(self.ed_nombre.text(), self.ed_notas.toPlainText(),
                                                         self.empresa_obj.id if self.empresa_obj else None)
        except ValueError as e:
            _caja(self, str(e), QMessageBox.Warning).exec_()
            return
        self.accept()


class ClientesDialog(QDialog):
    def __init__(self, padre, db: Optional[cl.ClientesDB] = None):
        super().__init__(padre)
        self.db = db if db is not None else cl.ClientesDB()
        self.setWindowTitle("👥 Empresas y usuarios")
        self.setWindowFlags((self.windowFlags() | Qt.WindowMinMaxButtonsHint) & ~Qt.WindowContextHelpButtonHint)
        self.setMinimumSize(900, 440)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 16, 18, 14)
        lay.setSpacing(10)
        lay.addWidget(piezas.nota("Tu lista de empresas y usuarios para elegir al rellenar los equipos (y en otros "
                                  "apartados). Quitar algo de aquí no cambia la agenda de Acceso remoto."))

        self.busqueda = QLineEdit()
        self.busqueda.setPlaceholderText("Buscar empresa o usuario")
        self.busqueda.setClearButtonEnabled(True)
        self.busqueda.textChanged.connect(lambda _t: self._filtrar())
        self.btn_empresa = piezas.boton("＋ Empresa", "primary", self.nueva_empresa)
        self.btn_usuario = piezas.boton("＋ Usuario", "primary", self.nuevo_usuario)
        self.btn_editar = piezas.boton("✏️ Editar", "ghost", self.editar)
        self.btn_eliminar = piezas.boton("✕ Quitar", "ghost", self.eliminar)
        self.lbl_recuento = QLabel("")
        self.lbl_recuento.setObjectName("countLabel")
        barra = QHBoxLayout()
        barra.setSpacing(8)
        barra.addWidget(self.busqueda, 2)
        for b in (self.btn_empresa, self.btn_usuario, self.btn_editar, self.btn_eliminar):
            barra.addWidget(b)
        barra.addStretch(1)
        barra.addWidget(self.lbl_recuento)
        lay.addLayout(barra)

        self.arbol = QTreeWidget()
        self.arbol.setColumnCount(len(COLUMNAS))
        self.arbol.setHeaderLabels(COLUMNAS)
        self.arbol.setUniformRowHeights(True)
        self.arbol.setAlternatingRowColors(True)
        self.arbol.setIndentation(16)
        self.arbol.setSortingEnabled(True)
        self.arbol.sortByColumn(0, Qt.AscendingOrder)
        cab = self.arbol.header()
        cab.setStretchLastSection(True)
        cab.setSectionResizeMode(QHeaderView.Interactive)
        self.arbol.itemDoubleClicked.connect(lambda _i, _c: self.editar())
        self.arbol.currentItemChanged.connect(lambda *_a: self._botones())
        self._iconos_cab = _IconosCabecera(self.arbol)
        lay.addWidget(self.arbol, 1)
        cerrar = piezas.boton("Cerrar", "ghost", self.accept)
        lay.addLayout(piezas.fila(None, cerrar, estirar=False))

        pantalla = padre.screen() if padre is not None and hasattr(padre, "screen") else QApplication.primaryScreen()
        zona = pantalla.availableGeometry() if pantalla else None
        self.resize(min(980, zona.width() - 40) if zona else 980, min(600, zona.height() - 60) if zona else 600)
        self.recargar()

    def _confirmar(self, texto: str) -> bool:
        caja = _caja(self, texto, QMessageBox.Question)
        si = caja.addButton("Sí", QMessageBox.YesRole)
        caja.addButton("No", QMessageBox.NoRole)
        caja.exec_()
        return caja.clickedButton() is si

    def _aviso(self, texto: str, icono=QMessageBox.Information):
        _caja(self, texto, icono).exec_()

    def recargar(self, seleccionar=None):
        try:
            empresas, usuarios = self.db.empresas(), self.db.usuarios()
        except Exception as e:
            empresas, usuarios = [], []
            self._aviso(f"No se pudo leer la lista: {e}", QMessageBox.Warning)
        self._empresas = {e.id: e for e in empresas}
        self._usuarios = {u.id: u for u in usuarios}
        self.arbol.setSortingEnabled(False)
        self.arbol.clear()
        nodos = {}
        item_sel = None
        for e in empresas:
            n = nodos[e.id] = QTreeWidgetItem(self.arbol, [e.nombre, "", "", e.notas.replace("\n", " ")])
            n.setData(0, _ROL_TIPO, "empresa")
            n.setData(0, _ROL_ID, e.id)
            n.setData(0, _ROL_BUSCAR, f"{e.nombre} {e.notas}".lower())
            f = n.font(0)
            f.setBold(True)
            n.setFont(0, f)
            if seleccionar == ("empresa", e.id):
                item_sel = n
        for u in usuarios:
            padre = nodos.get(u.empresa_id)
            if padre is None:
                continue
            h = QTreeWidgetItem(padre, [u.nombre, u.correo, u.telefono, u.notas.replace("\n", " ")])
            h.setData(0, _ROL_TIPO, "usuario")
            h.setData(0, _ROL_ID, u.id)
            h.setData(0, _ROL_BUSCAR, f"{u.nombre} {u.correo} {u.telefono} {u.notas} {u.empresa}".lower())
            for c in range(len(COLUMNAS)):
                h.setToolTip(c, h.text(c))
            if seleccionar == ("usuario", u.id):
                item_sel = h
        for e_id, n in nodos.items():
            n.setText(0, f"{self._empresas[e_id].nombre}  ({n.childCount()})")
            n.setExpanded(True)
        self.arbol.setSortingEnabled(True)
        for c in range(1, len(COLUMNAS) - 1):
            self.arbol.resizeColumnToContents(c)
            self.arbol.setColumnWidth(c, max(self.arbol.columnWidth(c) + 14, 110))
        self.arbol.setColumnWidth(0, max(260, self.arbol.columnWidth(0)))
        if item_sel is not None:
            self.arbol.setCurrentItem(item_sel)
        self._filtrar()

    def _filtrar(self):
        texto = self.busqueda.text().strip().lower()
        visibles = 0
        for i in range(self.arbol.topLevelItemCount()):
            n = self.arbol.topLevelItem(i)
            de_empresa = not texto or texto in (n.data(0, _ROL_BUSCAR) or "")
            hijos = 0
            for j in range(n.childCount()):
                h = n.child(j)
                ver = de_empresa or texto in (h.data(0, _ROL_BUSCAR) or "")
                h.setHidden(not ver)
                hijos += ver
            n.setHidden(not (de_empresa or hijos))
            visibles += hijos
            if texto and hijos:
                n.setExpanded(True)
        total_e, total_u = len(self._empresas), len(self._usuarios)
        self.lbl_recuento.setText(f"{total_e} empresa{'s' if total_e != 1 else ''} · "
                                  f"{total_u} usuario{'s' if total_u != 1 else ''}")
        self._botones()

    def _seleccion(self):
        item = self.arbol.currentItem()
        if item is None:
            return None, None
        tipo, ident = item.data(0, _ROL_TIPO), item.data(0, _ROL_ID)
        return tipo, (self._empresas.get(ident) if tipo == "empresa" else self._usuarios.get(ident))

    def _botones(self):
        """Siempre activos: el estilo de MASys no distingue los botones desactivados. Cada uno explica qué falta."""

    def _empresa_actual(self) -> str:
        tipo, obj = self._seleccion()
        if obj is None:
            return ""
        return obj.nombre if tipo == "empresa" else obj.empresa

    def nueva_empresa(self):
        dlg = FichaDialog(self, self.db)
        if dlg.exec_() == QDialog.Accepted and dlg.resultado:
            self.recargar(("empresa", dlg.resultado.id))

    def nuevo_usuario(self):
        if not self._empresas:
            self._aviso("Crea antes la empresa con «＋ Empresa».")
            return
        dlg = FichaDialog(self, self.db, es_usuario=True, empresa_inicial=self._empresa_actual())
        if dlg.exec_() == QDialog.Accepted and dlg.resultado:
            self.recargar(("usuario", dlg.resultado.id))

    def editar(self):
        tipo, obj = self._seleccion()
        if obj is None:
            self._aviso("Elige en la lista la empresa o el usuario.")
            return
        dlg = FichaDialog(self, self.db, empresa=obj if tipo == "empresa" else None,
                          usuario=obj if tipo == "usuario" else None)
        if dlg.exec_() == QDialog.Accepted and dlg.resultado:
            self.recargar((tipo, dlg.resultado.id))

    def eliminar(self):
        tipo, obj = self._seleccion()
        if obj is None:
            self._aviso("Elige en la lista la empresa o el usuario.")
            return
        if tipo == "empresa":
            n = sum(1 for u in self._usuarios.values() if u.empresa_id == obj.id)
            texto = (f"¿Quitar «{obj.nombre}»" + (f" y sus {n} usuarios" if n > 1 else " y su usuario" if n else "")
                     + " de la lista? Los equipos de la agenda conservan sus datos.")
        else:
            texto = f"¿Quitar a «{obj.nombre}» ({obj.empresa}) de la lista? Los equipos de la agenda conservan sus datos."
        if not self._confirmar(texto):
            return
        try:
            (self.db.eliminar_empresa if tipo == "empresa" else self.db.eliminar_usuario)(obj.id)
        except Exception as e:
            self._aviso(f"No se pudo quitar: {e}", QMessageBox.Warning)
            return
        self.recargar()
