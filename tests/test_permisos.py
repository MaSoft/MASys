# -*- coding: utf-8 -*-
"""Pruebas del motor de permisos (msys/domain/permisos.py).

Solo tocan carpetas temporales que crea la propia prueba (y que borra al
terminar): nunca permisos ni recursos compartidos reales del equipo."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from msys.domain import permisos as p  # noqa: E402

SID_X = "S-1-5-21-111-222-333-1105"     # cuenta de dominio inventada (no existe)
SID_Y = "S-1-5-21-444-555-666-2201"


class SddlTest(unittest.TestCase):
    def test_partes_y_alias(self):
        d = p.Descriptor.desde_sddl("O:BAG:SYD:PAI(A;OICI;FA;;;SY)(A;OICIID;0x1301bf;;;BU)(D;;FW;;;%s)" % SID_X)
        self.assertEqual(d.propietario, p.ADMINISTRADORES)
        self.assertTrue(d.protegida)
        self.assertEqual(len(d.entradas), 3)
        self.assertEqual([e.sid for e in d.propias], [p.SYSTEM, SID_X])
        self.assertEqual(p.nombre_nivel(d.heredadas[0].mascara), "Modificar")
        self.assertTrue(d.propias[1].denegar)

    def test_misma_clave_con_otra_escritura(self):
        a = p.Descriptor.desde_sddl("O:BAD:(A;OICI;FA;;;BA)")
        b = p.Descriptor.desde_sddl("O:S-1-5-32-544D:(A;CIOI;0x1f01ff;;;S-1-5-32-544)(A;OICIID;FR;;;WD)")
        self.assertEqual(a.clave(), b.clave())   # lo heredado no cuenta

    def test_niveles_y_aplica(self):
        self.assertEqual(p.nombre_nivel(p.mascara("FA")), "Control total")
        self.assertEqual(p.nombre_nivel(p.mascara("GA")), "Control total")
        self.assertEqual(p.nombre_nivel(0x1200a9), "Lectura y ejecución")
        self.assertEqual(p.texto_aplica("OICI"), "Esta carpeta, subcarpetas y archivos")
        self.assertEqual(p.texto_aplica("CIIO"), "Solo subcarpetas")
        self.assertEqual(p.texto_aplica(""), "Solo esta carpeta")

    def test_traducir_cuentas(self):
        d = p.Descriptor.desde_sddl("O:%sD:(A;OICI;FA;;;%s)(A;OICI;FR;;;WD)" % (SID_X, SID_X))
        t = d.traducido(lambda s: SID_Y if s == SID_X else s)
        self.assertEqual(t.propietario, SID_Y)
        self.assertEqual(t.propias[0].sid, SID_Y)
        q = d.traducido(lambda s: "" if s == SID_X else s)   # sin equivalente: se quita y el propietario pasa a Administradores
        self.assertEqual(q.propietario, p.ADMINISTRADORES)
        self.assertEqual([e.sid for e in q.propias], [p.TODOS])

    def test_acceso_efectivo_denegar_gana(self):
        # 0x116 = «Escritura» denegada tal como la pone el Explorador (sin Sincronizar)
        d = p.Descriptor.desde_sddl("O:BAD:(D;;0x116;;;%s)(A;;FA;;;BU)" % SID_X)
        m, por, contra = p.acceso_efectivo(d, {SID_X, p.USUARIOS, p.TODOS}, False)
        self.assertFalse(m & 0x2)                     # no puede escribir
        self.assertTrue((m & p.READ) == p.READ)       # sí leer
        self.assertEqual(contra, [SID_X])

    def test_analisis(self):
        lec = p.Lectura(raiz="C:\\X", equipo="PC", fecha="", usuario="")
        lec.elementos = [p.Elemento("", True, "O:BAD:P(A;OICI;FA;;;BA)(A;OICI;0x1301bf;;;WD)"),
                         p.Elemento("Huerfana", True, "O:%sD:P(A;OICI;FA;;;%s)" % (SID_X, SID_X)),
                         p.Elemento("Rota", True, "", "Acceso denegado.")]
        lec.cuentas = {p.ADMINISTRADORES: ("BUILTIN\\Administradores", 4), p.TODOS: ("Todos", 5), SID_X: ("", 0)}
        tipos = {h.tipo for h in p.analizar(lec)}
        self.assertIn("Todos pueden escribir", tipos)
        self.assertIn("Propietario eliminado", tipos)
        self.assertIn("Permiso de una cuenta eliminada", tipos)
        self.assertIn("Los administradores no tienen acceso", tipos)
        self.assertIn("No se pudieron leer los permisos", tipos)


class ArbolRealTest(unittest.TestCase):
    """Carpeta temporal de verdad: leer, guardar, cambiar, planificar y restaurar."""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="msys_perm_")
        for c in ("A", "A\\A1", "B"):
            os.makedirs(os.path.join(self.base, c))
        with open(os.path.join(self.base, "A", "dato.txt"), "w") as f:
            f.write("x")

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def _ruta(self, rel):
        return os.path.join(self.base, rel)

    def test_ciclo_completo(self):
        lec = p.leer_arbol(self.base)
        rels = sorted(e.rel for e in lec.elementos)
        self.assertEqual(rels, ["", "A", "A\\A1", "B"])       # el archivo hereda: no se guarda
        copia = os.path.join(self.base, "..", os.path.basename(self.base) + p.EXTENSION)
        try:
            p.guardar(lec, copia)
            cargada = p.cargar(copia)
            self.assertEqual(len(cargada.elementos), 4)
            # Se cambia: Usuarios con Modificar en A (se propaga a A1) y herencia cortada en B
            d = p.leer_descriptor(self._ruta("A"))
            p.escribir(self._ruta("A"), p.con_entrada_nueva(d, p.USUARIOS, True, p.MODIFY, "OICI"))
            p.escribir(self._ruta("B"), p.con_herencia(p.leer_descriptor(self._ruta("B")), False, True))
            a1 = p.leer_descriptor(self._ruta("A\\A1"))
            self.assertTrue(any(e.sid == p.USUARIOS and e.heredada for e in a1.entradas))   # Windows lo propagó
            # La comparación ve los dos cambios y nada más
            difs, _actual = p.planificar(cargada, self.base)
            self.assertEqual(sorted(x.rel for x in difs if x.aplicable), ["A", "B"])
            # Restaurar deja todo como en la copia (también lo heredado de A1)
            res = p.aplicar(self.base, difs)
            self.assertTrue(all(ok for _r, ok, _m in res), res)
            difs2, _ = p.planificar(cargada, self.base)
            self.assertEqual([x for x in difs2 if x.aplicable], [])
            a1 = p.leer_descriptor(self._ruta("A\\A1"))
            self.assertFalse(any(e.sid == p.USUARIOS for e in a1.entradas))
            self.assertFalse(p.leer_descriptor(self._ruta("B")).protegida)
        finally:
            if os.path.exists(copia):
                os.remove(copia)

    def test_archivo_con_permisos_propios_y_rama(self):
        archivo = self._ruta("A\\dato.txt")
        p.escribir(archivo, p.con_entrada_nueva(p.leer_descriptor(archivo), p.USUARIOS, True, p.READ, ""))
        lec = p.leer_arbol(self.base)
        self.assertIn("A\\dato.txt", [e.rel for e in lec.elementos])
        self.assertNotIn("A\\dato.txt", [e.rel for e in p.leer_arbol(self.base, archivos=p.ARCHIVOS_NINGUNO).elementos])
        # Quitar la cuenta de toda la rama A
        cambios = p.quitar_cuenta_de_rama(lec, "A", p.USUARIOS)
        self.assertEqual([r for r, _d in cambios], ["A\\dato.txt"])
        # Restaurar solo la rama B no toca A
        difs, _ = p.planificar(lec, self.base, rama="B")
        self.assertTrue(all(x.rel.startswith("B") for x in difs))

    def test_copia_previa_y_propietario(self):
        ruta = p.copia_previa(self.base, ["A", "B"], "prueba", carpeta=self.base)
        try:
            prev = p.cargar(ruta)
            self.assertTrue(prev.parcial)
            self.assertEqual(sorted(e.rel for e in prev.elementos), ["A", "B"])
        finally:
            os.remove(ruta)
        if p.is_admin_actual():
            res = p.cambiar_propietario(self.base, ["B"], p.ADMINISTRADORES)
            self.assertTrue(res[0][1], res)
            self.assertEqual(p.leer_descriptor(self._ruta("B")).propietario, p.ADMINISTRADORES)

    def test_acceso_de_cuenta_solo_cambios(self):
        d = p.leer_descriptor(self._ruta("B"))
        p.escribir(self._ruta("B"), p.con_entrada_nueva(d, p.USUARIOS, True, p.MODIFY, "OICI"))
        lec = p.leer_arbol(self.base)
        sid, _n, sids, _av = p.grupos_de("Usuarios", self.base)
        self.assertIn(p.USUARIOS, sids)
        filas = {f.rel: f for f in p.acceso_de_cuenta(lec, sid, sids)}
        self.assertEqual(filas["B"].nivel, "Modificar")
        self.assertEqual(filas[""].nivel, "Ninguno")
        self.assertNotIn("A\\A1", filas)      # igual que A: no se repite
        self.assertIn("A\\A1", {f.rel for f in p.acceso_de_cuenta(lec, sid, sids, solo_cambios=False)})

    def test_ruta_larga(self):
        largo = self.base
        while len(largo) < 300:
            largo = os.path.join(largo, "carpeta_con_nombre_largo_para_probar")
        os.makedirs(p.ruta_larga(largo))
        lec = p.leer_arbol(self.base)
        self.assertTrue(any(len(lec.ruta(e.rel)) > 260 and not e.error for e in lec.elementos))
        shutil.rmtree(p.ruta_larga(self.base), ignore_errors=True)
        os.makedirs(self.base, exist_ok=True)

    def test_recursos_de_red_solo_lectura(self):
        # Lee los recursos compartidos reales SIN cambiar nada
        rec, aviso = p.leer_recursos("C:\\")
        self.assertIsInstance(rec, list)
        self.assertIsInstance(aviso, str)


if __name__ == "__main__":
    unittest.main()
