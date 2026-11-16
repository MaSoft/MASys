# -*- coding: utf-8 -*-
"""Revisión 5.7.3: casos reales que fallaban (se ejecutan sin tocar el sistema)."""
import os
import shutil
import tempfile
import unittest

from msys.core.utils import _existe_ejecutable


class ExisteEjecutableTest(unittest.TestCase):
    """Registro de Windows › entradas huérfanas: una línea SIN comillas con espacios y argumentos
    («C:\\Program Files\\X\\setup.exe /S») se daba por inexistente y la entrada salía para borrar."""

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="masys prueba ")
        self.carpeta = os.path.join(self.base, "Program Files (x86)", "Mi Programa")
        os.makedirs(self.carpeta)
        self.exe = os.path.join(self.carpeta, "setup.exe")
        with open(self.exe, "wb") as f:
            f.write(b"MZ")

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_sin_comillas_con_argumentos_existe(self):
        self.assertTrue(_existe_ejecutable(self.exe))
        self.assertTrue(_existe_ejecutable(self.exe + " /S"))
        self.assertTrue(_existe_ejecutable(self.exe + " -runfromtemp -l0x040a"))
        self.assertTrue(_existe_ejecutable(f'"{self.exe}" /S'))

    def test_borrado_sigue_detectandose(self):
        os.remove(self.exe)
        self.assertFalse(_existe_ejecutable(self.exe + " /S"))
        self.assertFalse(_existe_ejecutable(f'"{self.exe}" /S'))

    def test_una_carpeta_que_existe_no_cuenta_como_ejecutable(self):
        # Antes bastaba con que el trozo hasta el primer espacio existiera, aunque fuera una carpeta.
        os.remove(self.exe)
        self.assertFalse(_existe_ejecutable(self.carpeta + " /S"))
        self.assertFalse(_existe_ejecutable(os.path.join(self.base, "Program Files (x86)", "Borrado", "x.exe") + " /S"))

    def test_referencias_no_comprobables_no_se_marcan(self):
        self.assertTrue(_existe_ejecutable("msiexec.exe /X{12345678-1234-1234-1234-123456789012}"))
        self.assertTrue(_existe_ejecutable("notepad.exe"))
        self.assertTrue(_existe_ejecutable(""))


if __name__ == "__main__":
    unittest.main()
