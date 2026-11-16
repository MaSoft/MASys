# -*- coding: utf-8 -*-
"""Comprobar correo: forma, extracción de listas y la pregunta al servidor con un SMTP falso local (sin salir a Internet)."""
import os
import socketserver
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from msys.domain.correo import direcciones as ec  # noqa: E402

EXISTENTES = {"ana@example.com"}


def _handler(catch_all=False, grey=False):
    class H(socketserver.StreamRequestHandler):
        def handle(self):
            self.wfile.write(b"220 mx de prueba\r\n")
            for raw in self.rfile:
                line = raw.decode().strip()
                cmd = line.split(" ", 1)[0].upper()
                if cmd in ("EHLO", "HELO"):
                    self.wfile.write(b"250-hola\r\n250 SIZE 1000\r\n")
                elif cmd == "MAIL":
                    self.wfile.write(b"250 ok\r\n")
                elif cmd == "RCPT":
                    addr = line[line.index("<") + 1:line.rindex(">")].lower()
                    if grey:
                        self.wfile.write(b"451 4.7.1 vuelve luego\r\n")
                    elif catch_all or addr in EXISTENTES:
                        self.wfile.write(b"250 2.1.5 ok\r\n")
                    else:
                        self.wfile.write(b"550 5.1.1 user unknown\r\n")
                elif cmd == "DATA":
                    raise AssertionError("nunca se envía DATA")
                elif cmd == "QUIT":
                    self.wfile.write(b"221 adios\r\n")
                    return
                else:
                    self.wfile.write(b"250 ok\r\n")
    return H


def _serve(h):
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), h)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class EmailCheckTest(unittest.TestCase):
    def setUp(self):
        self._orig = ec.domain_mail
        ec.domain_mail = lambda d, lifetime=6: ec.DomainMail(True, True, ["127.0.0.1"], "")

    def tearDown(self):
        ec.domain_mail = self._orig

    def _checker(self, srv):
        return ec.Checker(True, helo="prueba.local", timeout=5, port=srv.server_address[1])

    def test_forma(self):
        self.assertTrue(ec.syntax("ana.perez+boletin@example.com")[0])
        self.assertTrue(ec.syntax("josé@ñandú.example")[0] is False)          # lo de antes de la @ sin acentos
        self.assertEqual("xn--and-6ma2c.example", ec.syntax("ana@ñandú.example")[2])
        for bad in ("ana", "ana@", "@example.com", "an..a@example.com", "ana@example", "ana@-ex.com", "a@b@c.com", "x" * 65 + "@example.com"):
            self.assertFalse(ec.syntax(bad)[0], bad)

    def test_extraer_lista(self):
        t = "Ana <ana@example.com>, soporte@example.net; ANA@example.com\nnada aquí\n\"x\",facturas@example.org."
        self.assertEqual(["ana@example.com", "soporte@example.net", "facturas@example.org"], ec.extract(t))
        p = os.path.join(tempfile.mkdtemp(), "l.csv")
        with open(p, "w", encoding="cp1252") as f:
            f.write("Nombre;Correo\nJosé;jose@example.com\n")
        self.assertEqual(["jose@example.com"], ec.read_list_file(p))

    def test_existe_y_no_existe(self):
        srv = _serve(_handler())
        try:
            c = self._checker(srv)
            r = c.check("ana@example.com")
            self.assertEqual(ec.EXISTS, r.verdict, r)
            self.assertIn("de grupo", c.check("info@example.com").flags)  # info@: aviso de grupo
            r = c.check("nadie@example.com")
            self.assertEqual(ec.MISSING, r.verdict, r)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_acepta_todo_y_lista_gris(self):
        srv = _serve(_handler(catch_all=True))
        try:
            r = self._checker(srv).check("ana@example.com")
            self.assertEqual(ec.UNKNOWN, r.verdict)
            self.assertIn("acepta todo", r.flags)
        finally:
            srv.shutdown()
            srv.server_close()
        srv = _serve(_handler(grey=True))
        try:
            r = self._checker(srv).check("ana@example.com")
            self.assertEqual(ec.UNKNOWN, r.verdict)
            self.assertIn("451", r.reason)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_sin_respuesta_y_sin_preguntar(self):
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
        s.close()
        c = ec.Checker(True, timeout=3, port=free)
        r = c.check("ana@example.com")
        self.assertEqual(ec.UNKNOWN, r.verdict)
        self.assertIn("puerto 25", r.reason)
        r = c.check("otra@example.com")                       # ya sabe que el puerto está cerrado: no lo reintenta
        self.assertIn("puerto 25", r.reason)
        r = ec.Checker(False).check("ana@mailinator.com")
        self.assertEqual(ec.UNKNOWN, r.verdict)
        self.assertIn("temporal", r.flags)

    def test_dominio_sin_correo(self):
        ec.domain_mail = lambda d, lifetime=6: ec.DomainMail(True, False, [], "El dominio dice que no recibe correo (MX nulo)")
        self.assertEqual(ec.INVALID, ec.Checker(True).check("ana@example.com").verdict)
        ec.domain_mail = lambda d, lifetime=6: ec.DomainMail(False, False, [], "El dominio no existe")
        self.assertEqual(ec.INVALID, ec.Checker(True).check("ana@example.com").verdict)
        ec.domain_mail = lambda d, lifetime=6: ec.DomainMail(True, False, [], "El DNS no responde (sin conexión o muy lento)")
        self.assertEqual(ec.UNKNOWN, ec.Checker(True).check("ana@example.com").verdict)

    def test_csv_y_historial(self):
        r = ec.EmailResult("ana@example.com", ec.EXISTS, "ok", [ec.Step("Forma", "ok", "Bien")], ["mx.example.com"], ["de grupo"])
        self.assertEqual(r.to_dict(), ec.EmailResult.from_dict(r.to_dict()).to_dict())
        p = os.path.join(tempfile.mkdtemp(), "r.csv")
        ec.write_csv(p, [r])
        with open(p, encoding="utf-8-sig") as f:
            self.assertIn("ana@example.com;existe;ok;de grupo;mx.example.com", f.read())


if __name__ == "__main__":
    unittest.main()
