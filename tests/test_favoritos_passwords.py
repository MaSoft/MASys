# -*- coding: utf-8 -*-
"""Pruebas de los motores de Favoritos y Auditoría de contraseñas (4.3.1).
No tocan ningún navegador real ni la red: usan un «Bookmarks» sintético en
una carpeta temporal y simulan la respuesta de Have I Been Pwned.
python -m unittest discover -s tests -p "test_*.py" """
import hashlib
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from msys.domain import favoritos as fav  # noqa: E402
from msys.domain import passwords_auditoria as pa  # noqa: E402


class _Perfil:
    def __init__(self, ruta, navegador="Chrome"):
        self.navegador = navegador
        self.nombre_perfil = "Default"
        self.ruta = ruta
        self.carpeta_user_data = ruta


class FavoritosTest(unittest.TestCase):
    def _favs(self):
        return [
            fav.Favorito("bookmark_bar", "Trabajo", "Masoft", "https://masoft.es"),
            fav.Favorito("bookmark_bar", "Trabajo/Docs", "Python", "https://python.org"),
            fav.Favorito("other", "", "Suelto", "https://ejemplo.com/"),
        ]

    def test_export_import_netscape_conserva_carpetas(self):
        ruta = os.path.join(tempfile.mkdtemp(), "b.html")
        fav.exportar_netscape(self._favs(), ruta)
        imp = fav.importar_netscape(ruta)
        self.assertEqual(len(imp), 3)
        self.assertEqual(imp[0].carpeta, "Barra de marcadores/Trabajo")
        self.assertEqual(imp[1].carpeta, "Barra de marcadores/Trabajo/Docs")
        self.assertEqual(imp[2].titulo, "Suelto")

    def test_duplicados_normaliza_barra_final(self):
        favs = self._favs() + [fav.Favorito("other", "", "Dup", "https://ejemplo.com")]
        dup = fav.duplicados(favs)
        self.assertEqual(list(dup.keys()), ["https://ejemplo.com"])
        self.assertEqual(len(dup["https://ejemplo.com"]), 2)

    def test_escribir_y_releer_chromium_con_checksum(self):
        d = tempfile.mkdtemp()
        base = {"version": 1, "roots": {
            "bookmark_bar": {"type": "folder", "id": "1", "name": "Bookmarks bar", "children": []},
            "other": {"type": "folder", "id": "2", "name": "Other", "children": []},
            "synced": {"type": "folder", "id": "3", "name": "Mobile", "children": []}}}
        with open(os.path.join(d, "Bookmarks"), "w", encoding="utf-8") as f:
            json.dump(base, f)
        perfil = _Perfil(d)
        fav.escribir_chromium(perfil, self._favs(), respaldar=lambda r: (True, ""))
        with open(os.path.join(d, "Bookmarks"), encoding="utf-8") as f:
            data = json.load(f)
        # El checksum escrito es el que Chromium recalcularía
        self.assertEqual(data["checksum"], fav._checksum_chromium(data["roots"]))
        self.assertEqual(len(data["roots"]["bookmark_bar"]["children"]), 1)  # la carpeta Trabajo
        relei = fav.cargar(perfil)
        self.assertEqual([(f.raiz, f.carpeta, f.titulo) for f in relei],
                         [("bookmark_bar", "Trabajo", "Masoft"),
                          ("bookmark_bar", "Trabajo/Docs", "Python"),
                          ("other", "", "Suelto")])

    def test_escribir_exige_chromium(self):
        with self.assertRaises(ValueError):
            fav.escribir_chromium(_Perfil("x", navegador="Firefox"), [], respaldar=lambda r: (True, ""))

    def test_comprobar_enlace_esquema_no_web(self):
        self.assertEqual(fav.comprobar_enlace("javascript:void(0)")[0], fav.OK)
        self.assertEqual(fav.comprobar_enlace("ftp://x")[0], fav.SINRED)


class PasswordsTest(unittest.TestCase):
    def test_fortaleza_niveles(self):
        self.assertEqual(pa.evaluar_fortaleza("123456")[0], pa.MUY_DEBIL)
        self.assertEqual(pa.evaluar_fortaleza("")[0], pa.MUY_DEBIL)
        self.assertIn(pa.evaluar_fortaleza("Verano2021")[0], (pa.DEBIL, pa.ACEPTABLE))
        self.assertEqual(pa.evaluar_fortaleza("gH7$kLm2!qZ")[0], pa.FUERTE)

    def test_reutilizadas_por_hash(self):
        filas = [pa.FilaPassword("Chrome", "D", "a.com", "u", "Secreta1"),
                 pa.FilaPassword("Chrome", "D", "b.com", "u", "Secreta1"),
                 pa.FilaPassword("Chrome", "D", "c.com", "u", "Otra2")]
        pa.marcar_reutilizadas(filas)
        self.assertEqual([f.reutilizada_en for f in filas], [2, 2, 0])

    def test_exposicion_k_anonimato(self):
        pwd = "P@ssw0rd"
        sha1 = hashlib.sha1(pwd.encode()).hexdigest().upper()
        enviados = {}

        def fake(prefijo):
            enviados["prefijo"] = prefijo
            return {sha1[5:]: 1234, "0" * 35: 1}

        orig = pa._pwned_por_prefijo
        pa._pwned_por_prefijo = fake
        try:
            self.assertEqual(pa.comprobar_expuesta(pwd), 1234)
            self.assertEqual(enviados["prefijo"], sha1[:5])  # solo se envían 5 caracteres
            self.assertEqual(len(enviados["prefijo"]), 5)
        finally:
            pa._pwned_por_prefijo = orig

    def test_resumen_cuenta_bien(self):
        filas = [pa.FilaPassword("Chrome", "D", "a.com", "u", "123456"),
                 pa.FilaPassword("Chrome", "D", "b.com", "u", None)]
        for f in filas:
            f.fortaleza = pa.evaluar_fortaleza(f.password)[0]
        r = pa.resumen(filas)
        self.assertEqual(r["total"], 2)
        self.assertEqual(r["sin_descifrar"], 1)
        self.assertEqual(r["muy_debiles"], 1)


if __name__ == "__main__":
    unittest.main()
