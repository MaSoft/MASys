# -*- coding: utf-8 -*-
"""Visor PST/OST. Las pruebas con archivos reales usan los PST/OST públicos de prueba de pst-extractor y java-libpst
(no se guardan en el repositorio): carpeta en la variable MASYS_PST_TESTDATA (o MACORREO_PST_TESTDATA); sin ella se saltan."""
import email
import email.policy
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from msys.domain.correo import pst as pv  # noqa: E402

DATA = os.environ.get("MASYS_PST_TESTDATA") or os.environ.get("MACORREO_PST_TESTDATA", "")


class SinArchivoTest(unittest.TestCase):
    def test_rtf(self):
        rtf = r"{\rtf1\ansi{\fonttbl{\f0 Arial;}}{\colortbl;\red0\green0\blue0;}Hola\par Se\'f1or \{x\}\par\par\par Fin}"
        self.assertEqual("Hola\nSeñor {x}\n\nFin", pv.rtf_to_text(rtf))

    def test_html_seguro(self):
        m = pv.Message("a", "b", None, [], [], "", '<p onclick="x()">Hola</p><script>alert(1)</script><img src="https://rastreo.example/p.gif">'
                       '<iframe src="https://example.com"></iframe>', "", [])
        h = pv.html_for_view(m)
        for bad in ("<script", "onclick", "rastreo.example", "<iframe"):
            self.assertNotIn(bad, h)
        self.assertIn("Hola", h)
        self.assertIn("&lt;b&gt;", pv.html_for_view(pv.Message("", "", None, [], [], "", "", "<b>texto</b>", [])))

    def test_nombres(self):
        self.assertEqual("a_b_c.txt", pv.safe_file('a/b:c.txt'))
        d = tempfile.mkdtemp()
        p = os.path.join(d, "x.txt")
        open(p, "w").close()
        self.assertEqual(os.path.join(d, "x (2).txt"), pv.unique_path(p))
        self.assertTrue(pv.Attachment(0, "factura.pdf.exe", 1).risky)
        self.assertFalse(pv.Attachment(0, "factura.pdf", 1).risky)
        with self.assertRaises(pv.PstError):
            pv.PstFile(os.path.join(d, "no-existe.pst"))


@unittest.skipUnless(DATA and os.path.isdir(DATA), "sin archivos de prueba (MASYS_PST_TESTDATA)")
class ConArchivoTest(unittest.TestCase):
    def _first(self, n):
        if n.count:
            return n
        for c in n.children:
            r = self._first(c)
            if r:
                return r
        return None

    def test_ost_completo(self):
        f = pv.PstFile(os.path.join(DATA, "pstextractortest@outlook.com.ost").replace("\\", "/"))   # con «/»: se normaliza
        try:
            self.assertEqual("OST", f.kind)
            t = f.tree()
            self.assertEqual(183, t.total())
            n = self._first(t)
            m = f.message(n.path, 0)
            self.assertTrue(m.subject.startswith("Invitation"))
            self.assertEqual(["pstextractortest@outlook.com"], m.to)
            self.assertEqual("invite.ics", m.attachments[0].name)
            self.assertEqual(m.attachments[0].size, len(f.attachment_bytes(n.path, 0, 0)))
            e = email.message_from_bytes(f.to_eml(n.path, 0), policy=email.policy.default)
            self.assertTrue(e["Subject"].startswith("Invitation"))
            self.assertEqual(["invite.ics"], [p.get_filename() for p in e.iter_attachments()])
            self.assertGreaterEqual(len(f.search("meeting")), 1)
            out = tempfile.mkdtemp()
            ok, errors = f.export_folder(n.path, out)
            self.assertEqual((1, []), (ok, errors))
        finally:
            f.close()

    def test_pst_y_cabeceras_codificadas(self):
        f = pv.PstFile(os.path.join(DATA, "enron.pst"))
        try:
            self.assertEqual(71, f.tree().total())
        finally:
            f.close()
        f = pv.PstFile(os.path.join(DATA, "example-2013.ost"))
        try:
            n = self._first(f.tree())
            self.assertEqual(["Arc Test <arc.test1@apogeephysicians.com>"], f.message(n.path, 0).to)   # =?utf-8?B?…?= decodificado
        finally:
            f.close()


if __name__ == "__main__":
    unittest.main()
