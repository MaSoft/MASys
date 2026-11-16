# -*- coding: utf-8 -*-
"""Norma del usuario (olvidarla = motivo de denuncia): NUNCA el explorador nativo de Windows para elegir, abrir o guardar
archivos y carpetas, porque no sigue el tema. En MASys se usan los diálogos propios de msys/core/utils.py
(dialogo_abrir_archivo, dialogo_guardar_archivo, dialogo_elegir_carpeta). Esta prueba falla si vuelve a aparecer un
QFileDialog.get* estático fuera de comentarios (4.10.1: el último era Móvil › Copias › Carpeta…)."""
import glob
import os
import re
import unittest

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ESTATICO = re.compile(r"QFileDialog\.get(OpenFileNames?|SaveFileName|ExistingDirectory)\s*\(")


class SinExploradorNativoTest(unittest.TestCase):
    def test_ningun_qfiledialog_estatico(self):
        rutas = glob.glob(os.path.join(RAIZ, "msys", "**", "*.py"), recursive=True) + [os.path.join(RAIZ, "multisys.py"),
                                                                                       os.path.join(RAIZ, "agente.py")]
        malos = []
        for ruta in rutas:
            with open(ruta, encoding="utf-8", errors="replace") as f:
                for n, linea in enumerate(f, 1):
                    codigo = linea.split("#", 1)[0]
                    if ESTATICO.search(codigo) and "Sustituye" not in linea and "sustituye" not in linea:
                        malos.append(f"{os.path.relpath(ruta, RAIZ)}:{n}")
        self.assertEqual(malos, [], "Explorador nativo de Windows: usar los diálogos de msys/core/utils.py")


if __name__ == "__main__":
    unittest.main()
