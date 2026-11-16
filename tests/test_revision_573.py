# -*- coding: utf-8 -*-
"""Revisión 5.7.3: casos reales que fallaban (se ejecutan sin tocar el sistema)."""
import os
import shutil
import tempfile
import unittest

from msys.core.utils import _existe_ejecutable, _is_eol_software, _csv_safe_cell


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


class SoftwareSinSoporteTest(unittest.TestCase):
    """«Microsoft Office Professional Plus 2013» no salía como sin soporte: se buscaba «office 2013» seguido."""

    def test_nombres_reales(self):
        sin_soporte = ["Microsoft Office Professional Plus 2016", "Microsoft Office Profesional Plus 2019 - es-es",
                       "Microsoft Office Standard 2013", "Microsoft SQL Server 2014 (64-bit)",
                       "Microsoft SQL Server 2016 LocalDB", "Microsoft Exchange Server 2019 Cumulative Update 14",
                       "Python 2.7.18"]
        con_soporte = ["Microsoft 365 Apps para empresas - es-es", "Microsoft Office LTSC Professional Plus 2021 - es-es",
                       "Microsoft Office 16 Click-to-Run Extensibility Component", "Microsoft SQL Server 2019 (64-bit)",
                       "Microsoft Visual C++ 2013 Redistributable (x64)", "LibreOffice 7.6.4.1", "OfficeSuite 2016 Free"]
        for n in sin_soporte:
            self.assertTrue(_is_eol_software(n), n)
        for n in con_soporte:
            self.assertFalse(_is_eol_software(n), n)


class CsvSeguroTest(unittest.TestCase):
    def test_formulas_neutralizadas(self):
        for v in ("=1+1", "+1", "-1", "@SUMA(A1)", "\t=1", "\r=1"):
            self.assertTrue(_csv_safe_cell(v).startswith("'"), repr(v))
        self.assertEqual(_csv_safe_cell("texto"), "texto")
        self.assertEqual(_csv_safe_cell(None), "")


if __name__ == "__main__":
    unittest.main()
