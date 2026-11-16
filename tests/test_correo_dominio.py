# -*- coding: utf-8 -*-
"""Dominios: SPF, DMARC, DKIM, listas negras e informe, con un DNS falso (sin red)."""
import base64
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from msys.domain.correo import dominio as dc  # noqa: E402
from msys.domain.correo import informe as report  # noqa: E402


def _rsa_txt(bits: int) -> str:
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    if bits < 1024:     # la librería no genera claves tan cortas: se monta una pública con un módulo de ese tamaño
        k = rsa.RSAPublicNumbers(65537, (1 << (bits - 1)) + 1).public_key()
    else:
        k = rsa.generate_private_key(public_exponent=65537, key_size=bits).public_key()
    return "v=DKIM1; k=rsa; p=" + base64.b64encode(k.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)).decode()


class FakeDns:
    def __init__(self, txt=None, a=None):
        self.t = txt or {}
        self.a = a or {}

    def txt(self, name):
        return list(self.t.get(name, []))

    def query(self, name, rtype):
        if rtype == "A" and name in self.a:
            class R:
                def __init__(self, v):
                    self.v = v

                def to_text(self):
                    return self.v
            return [R(self.a[name])]
        return []


class DomainTest(unittest.TestCase):
    def setUp(self):
        self._txt, self._q = dc.txt, dc.query

    def tearDown(self):
        dc.txt, dc.query = self._txt, self._q

    def use(self, fake):
        dc.txt, dc.query = fake.txt, fake.query

    def test_spf(self):
        self.use(FakeDns({"example.com": ["v=spf1 include:_spf.example.net -all", "otra cosa"],
                          "_spf.example.net": ["v=spf1 ip4:192.0.2.0/24 include:x.example.org ~all"],
                          "x.example.org": ["v=spf1 a mx -all"]}))
        c = dc.check_spf("example.com")
        self.assertEqual("ok", c.level, c)
        self.assertIn("Gasta 4 consultas", " ".join(t for _l, t in c.items))
        self.use(FakeDns({"example.com": ["v=spf1 +all"]}))
        self.assertEqual("bad", dc.check_spf("example.com").level)
        self.use(FakeDns({"example.com": ["v=spf1 -all", "v=spf1 ~all"]}))
        self.assertIn("no vale ninguno", dc.check_spf("example.com").summary)
        self.use(FakeDns({}))
        self.assertEqual("bad", dc.check_spf("example.com").level)
        # redirect: el «all» lo pone el registro al que apunta
        self.use(FakeDns({"example.com": ["v=spf1 redirect=_spf.example.net"], "_spf.example.net": ["v=spf1 ip4:192.0.2.1 ~all"]}))
        c = dc.check_spf("example.com")
        self.assertEqual("ok", c.level, c)
        # más de 10 consultas
        chain = {"example.com": ["v=spf1 " + " ".join(f"include:i{n}.example.net" for n in range(11)) + " -all"]}
        chain.update({f"i{n}.example.net": ["v=spf1 -all"] for n in range(11)})
        self.use(FakeDns(chain))
        self.assertEqual("bad", dc.check_spf("example.com").level)

    def test_dmarc(self):
        self.use(FakeDns({"_dmarc.example.com": ["v=DMARC1; p=reject; rua=mailto:dmarc@example.com"]}))
        self.assertEqual("ok", dc.check_dmarc("example.com").level)
        self.use(FakeDns({"_dmarc.example.com": ["v=DMARC1; p=none"]}))
        c = dc.check_dmarc("example.com")
        self.assertEqual("warn", c.level)
        self.assertIn("Sin rua", " ".join(t for _l, t in c.items))
        self.use(FakeDns({}))
        self.assertEqual("bad", dc.check_dmarc("example.com").level)

    def test_dkim(self):
        k2048, k512 = _rsa_txt(2048), _rsa_txt(512)
        self.assertEqual(2048, dc.dkim_key_bits(k2048))
        self.assertEqual(0, dc.dkim_key_bits("v=DKIM1; p="))
        self.use(FakeDns({"selector1._domainkey.example.com": [k2048], "propio._domainkey.example.com": [k512]}))
        c = dc.check_dkim("example.com", ["propio"])
        self.assertEqual("warn", c.level)                   # una de 512 bits
        self.assertEqual(2, len(c.items))
        self.use(FakeDns({"google._domainkey.example.com": [k2048]}))
        self.assertEqual("ok", dc.check_dkim("example.com").level)
        self.use(FakeDns({}))
        self.assertEqual("info", dc.check_dkim("example.com").level)

    def test_listas_negras(self):
        listed = {"2.0.0.192.bl.spamcop.net": "127.0.0.2", "2.0.0.192.zen.spamhaus.org": "127.255.255.254"}
        orig = dc.ips_of
        dc.ips_of = lambda h: ["192.0.0.2"]
        dc._dnsbl_orig = dc._dnsbl
        dc._dnsbl = lambda name: listed.get(name)
        try:
            c = dc.check_blacklists("example.com", [(10, "mx.example.com")])
            self.assertEqual("bad", c.level)
            txt = " ".join(t for _l, t in c.items)
            self.assertIn("SpamCop", txt)
            self.assertIn("Spamhaus ZEN: no responde", txt)    # 127.255.255.254 = no contesta, no es «está en la lista»
        finally:
            dc.ips_of = orig
            dc._dnsbl = dc._dnsbl_orig

    def test_normalizar_e_informe(self):
        self.assertEqual("example.com", dc.normalize_domain(" Ana@Example.com "))
        self.assertEqual("example.com", dc.normalize_domain("https://example.com/contacto"))
        self.assertEqual("xn--and-6ma2c.example", dc.normalize_domain("ñandú.example"))
        rep = dc.Report("example.com", "27/10/2026 18:00", [dc.Check("spf", "SPF", "bad", "No <b>tiene</b>", ["v=spf1 <x>"], "Añádelo", [("bad", "a&b")])])
        h = report.domain_html(rep)
        self.assertIn("No &lt;b&gt;tiene&lt;/b&gt;", h)
        self.assertIn("a&amp;b", h)
        self.assertIn("Lo primero que revisar", h)
        self.assertEqual(rep.to_dict(), dc.Report.from_dict(rep.to_dict()).to_dict())


if __name__ == "__main__":
    unittest.main()
