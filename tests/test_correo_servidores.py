# -*- coding: utf-8 -*-
"""Pruebas de la lógica sin interfaz: proveedores, prueba de servidores (con servidores falsos locales), PRF y configuración.
Copia de las pruebas de MACorreo adaptada a MASys (4.9.0)."""
import os
import socket
import socketserver
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from msys.domain.correo import outlook_cfg as outlook  # noqa: E402
from msys.domain.correo import proveedores as prov  # noqa: E402
from msys.domain.correo import servidores as mailtest  # noqa: E402
from msys.domain.correo.ajustes import CorreoDB  # noqa: E402

USER, PASS = "ana@example.com", 'clave "con" comillas'


# ------------------------------------------------------------------ servidores falsos (texto plano, solo lo que usan las librerías)
class _Imap(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b"* OK IMAP de prueba listo\r\n")
        for raw in self.rfile:
            line = raw.decode().strip()
            tag, _, rest = line.partition(" ")
            cmd = rest.split(" ", 1)[0].upper()
            if cmd == "CAPABILITY":
                self.wfile.write(b"* CAPABILITY IMAP4rev1 AUTH=PLAIN\r\n" + f"{tag} OK hecho\r\n".encode())
            elif cmd == "LOGIN":
                ok = rest.endswith('"clave \\"con\\" comillas"')
                self.wfile.write(f"{tag} {'OK bienvenido' if ok else 'NO credenciales'}\r\n".encode())
            elif cmd == "LOGOUT":
                self.wfile.write(b"* BYE\r\n" + f"{tag} OK adios\r\n".encode())
                return
            else:
                self.wfile.write(f"{tag} BAD no\r\n".encode())


class _Pop(socketserver.StreamRequestHandler):
    def handle(self):
        self.wfile.write(b"+OK POP de prueba\r\n")
        for raw in self.rfile:
            line = raw.decode().strip()
            cmd, _, arg = line.partition(" ")
            cmd = cmd.upper()
            if cmd == "CAPA":
                self.wfile.write(b"+OK\r\nUSER\r\n.\r\n")
            elif cmd == "USER":
                self.wfile.write(b"+OK\r\n")
            elif cmd == "PASS":
                self.wfile.write(b"+OK dentro\r\n" if arg == PASS else b"-ERR mal\r\n")
            elif cmd == "QUIT":
                self.wfile.write(b"+OK\r\n")
                return


class _Smtp(socketserver.StreamRequestHandler):
    def handle(self):
        import base64
        self.wfile.write(b"220 smtp de prueba\r\n")
        for raw in self.rfile:
            line = raw.decode().strip()
            cmd = line.split(" ", 1)[0].upper()
            if cmd == "EHLO":
                self.wfile.write(b"250-hola\r\n250 AUTH PLAIN LOGIN\r\n")
            elif cmd == "AUTH":
                parts = line.split(" ")
                creds = base64.b64decode(parts[2]).split(b"\0") if len(parts) > 2 else []
                ok = len(creds) == 3 and creds[2].decode() == PASS
                self.wfile.write(b"235 ok\r\n" if ok else b"535 mal\r\n")
            elif cmd == "QUIT":
                self.wfile.write(b"221 adios\r\n")
                return
            else:
                self.wfile.write(b"502 no\r\n")


def _serve(handler):
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class MailTestTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.servers = {k: _serve(h) for k, h in (("IMAP", _Imap), ("POP3", _Pop), ("SMTP", _Smtp))}

    @classmethod
    def tearDownClass(cls):
        for s in cls.servers.values():
            s.shutdown()
            s.server_close()

    def port(self, k):
        return self.servers[k].server_address[1]

    def test_ok_y_credenciales(self):
        for proto in ("IMAP", "POP3", "SMTP"):
            r = mailtest.test_server(proto, "127.0.0.1", self.port(proto), False, USER, PASS, timeout=5)
            self.assertEqual(mailtest.OK, r.status, (proto, r))
            self.assertIsNone(r.cert_ok)       # sin cifrado: no aplica
            r = mailtest.test_server(proto, "127.0.0.1", self.port(proto), False, USER, "otra", timeout=5)
            self.assertEqual(mailtest.CREDS, r.status, (proto, r))
            r = mailtest.test_server(proto, "127.0.0.1", self.port(proto), False, timeout=5)
            self.assertEqual(mailtest.OK_BANNER, r.status, (proto, r))

    def test_errores_de_red(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
        s.close()
        self.assertEqual("Sin conexión", mailtest.test_server("IMAP", "127.0.0.1", free, False, timeout=3).status)
        self.assertEqual(mailtest.NA, mailtest.test_server("POP3", "", 0, True).status)
        self.assertEqual("Servidor desconocido", mailtest.test_server("SMTP", "no-existe.invalid", 25, False, timeout=3).status)


class ProvidersTest(unittest.TestCase):
    def test_lista_de_la_v4(self):
        self.assertEqual(40, len(prov.PROVIDERS))
        g = prov.BY_NAME["Gmail"]
        self.assertEqual(("imap.gmail.com", 993, True, "smtp.gmail.com", 587, False), (g.imap, g.imap_port, g.imap_ssl, g.smtp, g.smtp_port, g.smtp_ssl))
        self.assertEqual(0, prov.BY_NAME["iCloud"].pop_port)

    def test_reconocer(self):
        self.assertEqual(["Outlook.com"], prov.by_domain("hotmail.es"))
        self.assertEqual(["Movistar"], prov.by_domain("movistar.es"))
        self.assertEqual([], prov.by_domain("example.com"))
        self.assertEqual(["Gmail"], prov.by_mx(["aspmx.l.google.com.", "alt1.aspmx.l.google.com"]))
        self.assertEqual(["Outlook.com"], prov.by_mx(["example-com.mail.protection.outlook.com"]))
        self.assertEqual([], prov.by_mx(["mx.example.com"]))
        self.assertTrue(mailtest.valid_email("a.b@example.com"))
        self.assertFalse(mailtest.valid_email("a@b"))


class OutlookTest(unittest.TestCase):
    def test_prf(self):
        t = outlook.build_prf("Outlook", "imap", "Ana", USER, USER, "imap.example.com", 993, True, "smtp.example.com", 587, False)
        for line in ("ProfileName=Outlook", "AccountType=IMAP", "IMAPServer=imap.example.com", "IMAPPort=993", "IMAPSecureConnection=1",
                     "SMTPPort=587", "SMTPSecureConnection=0", "SMTPUseAuth=1", "EmailAddress=ana@example.com"):
            self.assertIn(line, t)
        self.assertNotIn("Password", t)       # nunca va la contraseña

    def test_nombres(self):
        self.assertEqual("a_b_c", outlook.safe_name("a/b:c"))
        self.assertEqual("", outlook.version_suffix("16.0", ["16.0"]))
        self.assertEqual("_Outlook15", outlook.version_suffix("15.0", ["16.0", "15.0"]))

    def test_firmas_zip(self):
        import zipfile
        d = tempfile.mkdtemp()
        for f in ("Corta.htm", "Corta.txt", "Larga.htm"):
            open(os.path.join(d, f), "w").close()
        os.makedirs(os.path.join(d, "Corta_archivos"))
        open(os.path.join(d, "Corta_archivos", "logo.png"), "w").close()
        orig = outlook.signatures_dir
        outlook.signatures_dir = lambda: d
        try:
            self.assertEqual(["Corta", "Larga"], outlook.signatures())
            z = os.path.join(d, "out", "f.zip")
            self.assertEqual(3, outlook.zip_signatures(["Corta"], z))
            names = set(zipfile.ZipFile(z).namelist())
            self.assertEqual({"Corta.htm", "Corta.txt", "Corta_archivos/logo.png"}, {n.replace("\\", "/") for n in names})
            # todas: los 4 archivos de las firmas + f.zip de antes; nunca el propio ZIP que se está escribiendo
            n = outlook.zip_signatures(None, os.path.join(d, "todo.zip"))
            names = {x.replace("\\", "/") for x in zipfile.ZipFile(os.path.join(d, "todo.zip")).namelist()}
            self.assertNotIn("todo.zip", names)
            self.assertEqual(5, n)
        finally:
            outlook.signatures_dir = orig


class CorreoDBTest(unittest.TestCase):
    def test_ajustes_e_historial(self):
        db = CorreoDB(os.path.join(tempfile.mkdtemp(), "multisys.db"))
        self.assertEqual(3.0, db.numero("pausa"))          # por defecto
        self.assertTrue(db.si("thunderbird"))
        db.guardar_ajuste("pausa", 7)
        db.guardar_ajuste("thunderbird", "0")
        db.guardar_ajuste("temporales_extra", "basura.example\notra.example")
        self.assertEqual(7.0, db.numero("pausa"))
        self.assertFalse(db.si("thunderbird"))
        chk = db.comprobador(False)
        self.assertIn("otra.example", chk.disposable)
        self.assertFalse(chk.probe_mailbox)
        for i in range(3):
            db.anadir_historial("dominio", f"d{i}.example.com", "0/0/9", {"i": i})
            time.sleep(0.002)
        h = db.historial("dominio")
        self.assertEqual(["d2.example.com", "d1.example.com", "d0.example.com"], [x["objetivo"] for x in h])
        self.assertEqual({"i": 2}, h[0]["datos"])
        db.quitar_historial([h[0]["id"]])
        self.assertEqual(2, len(db.historial("dominio")))
        self.assertEqual([], db.historial("direccion"))


if __name__ == "__main__":
    unittest.main()
