# -*- coding: utf-8 -*-
"""Pruebas de los motores de los apartados nuevos (solo lectura: nada cambia el sistema).
python -m unittest discover -s tests -p "test_*.py" """
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from msys.domain import iconos as ic  # noqa: E402


class IconosTest(unittest.TestCase):
    def test_biblioteca_de_windows_con_todos_los_tamanos(self):
        grupos = ic.leer_biblioteca(ic.ruta_biblioteca("imageres.dll"))
        self.assertGreater(len(grupos), 100)
        self.assertEqual([g.indice for g in grupos[:5]], [0, 1, 2, 3, 4])  # el índice es la posición
        g = next(x for x in grupos if x.res_id == "3")
        self.assertEqual(g.referencia, "-3")
        self.assertEqual(g.tamano_max, 256)
        # El .ico reconstruido es válido: cabecera, nº de imágenes y tamaños
        _r, tipo, n = struct.unpack_from("<HHH", g.ico, 0)
        self.assertEqual((tipo, n), (1, len(g.tamanos)))
        self.assertEqual(ic.tamanos_ico(g.ico), g.tamanos)

    def test_ico_suelto_y_errores(self):
        g = ic.leer_biblioteca(ic.ruta_biblioteca("shell32.dll"))[3]
        ruta = os.path.join(tempfile.mkdtemp(), "x.ico")
        with open(ruta, "wb") as f:
            f.write(g.ico)
        uno = ic.leer_biblioteca(ruta)
        self.assertEqual(len(uno), 1)
        self.assertEqual(uno[0].tamanos, g.tamanos)
        with self.assertRaises(OSError):
            ic.leer_biblioteca(r"C:\no\existe.dll")
        texto = ruta.replace(".ico", ".txt")
        with open(texto, "w") as f:
            f.write("no es una biblioteca")
        with self.assertRaises(OSError):
            ic.leer_biblioteca(texto)

    def test_overlays_prioridad(self):
        ovs = [ic.Overlay(1, "  OneDrive1", "", "OneDrive", "", ""), ic.Overlay(2, " Dropbox", "", "Dropbox", "", ""),
               ic.Overlay(3, "Tortoise1Normal", "", "Tortoise", "", "")]
        nuevo = ic.nombre_priorizado("Tortoise1Normal", ovs)
        self.assertEqual(nuevo, "   Tortoise1Normal")
        self.assertEqual(sorted([o.nombre for o in ovs[:2]] + [nuevo])[0], nuevo)  # pasa a ser el primero

    def test_existe_busca_en_system32(self):
        self.assertTrue(ic._existe("msiexec.exe"))
        self.assertTrue(ic._existe("%SystemRoot%\\explorer.exe"))
        self.assertFalse(ic._existe(r"C:\no\existe.exe"))
        self.assertIsNone(ic._existe(r"\\servidor\recurso\a.exe"))  # red: no se comprueba

    def test_origen_icono_y_grupo(self):
        from msys.ui.iconos import grupo_por_indice, origen_icono
        a = ic.AccesoDirecto("x", "Escritorio", "x.lnk", r"C:\Windows\explorer.exe", "", "%SystemRoot%\\System32\\imageres.dll,-3", "")
        archivo, indice = origen_icono(a)
        self.assertTrue(archivo.lower().endswith("imageres.dll"))
        self.assertEqual(indice, -3)
        b = ic.AccesoDirecto("y", "Escritorio", "y.lnk", r"C:\Windows\explorer.exe", "", ",0", "")
        self.assertEqual(origen_icono(b), (r"C:\Windows\explorer.exe", 0))
        grupos = ic.leer_biblioteca(ic.ruta_biblioteca("imageres.dll"))
        self.assertEqual(grupo_por_indice(grupos, -3).res_id, "3")
        self.assertEqual(grupo_por_indice(grupos, 5).indice, 5)
        self.assertIsNone(grupo_por_indice(grupos, 99999))

    def test_lecturas_del_sistema(self):
        h, v = ic.espaciado_iconos()
        self.assertTrue(ic.ESPACIADO_MIN <= h <= 400 and ic.ESPACIADO_MIN <= v <= 400)
        self.assertEqual(set(ic.iconos_escritorio_visibles()), {c for c, _n, _o in ic.ICONOS_ESCRITORIO})
        for o in ic.listar_overlays():
            self.assertIn(o.estado, (ic.OVERLAY_ACTIVO, ic.OVERLAY_RIESGO, ic.OVERLAY_FUERA))


class AsociacionesTest(unittest.TestCase):
    def test_extensiones_habituales(self):
        from msys.domain import asociaciones as a
        txt = a.consultar(".txt")
        self.assertEqual(txt.estado, a.ESTADO_OK)
        self.assertTrue(txt.programa)
        desconocida = a.consultar(".msys_no_existe_123")
        self.assertEqual(desconocida.estado, a.ESTADO_SIN)
        self.assertEqual(desconocida.programa, "")
        http = a.consultar("http", protocolo=True)
        self.assertEqual(http.estado, a.ESTADO_OK)

    def test_listado_y_agrupado(self):
        from msys.domain import asociaciones as a
        ex = a.listar_extensiones()
        self.assertGreater(len(ex), 100)
        self.assertTrue(all(x.clave.startswith(".") for x in ex))
        r = a.resumen(ex)
        self.assertEqual(r["total"], len(ex))
        for programa, _exe, lista in a.por_programa(ex):
            self.assertTrue(programa)
            self.assertNotIn("OpenWith", programa)  # las rotas no cuentan como programa
            self.assertTrue(lista)
        self.assertFalse(any(x.programa.startswith("@{") for x in ex))  # nombres de la Tienda resueltos

    def test_abrir_con_valida_la_extension(self):
        from msys.domain import asociaciones as a
        self.assertFalse(a.abrir_con(r"..\malo")[0])
        self.assertFalse(a.abrir_con("txt")[0])


class CaracteristicasTest(unittest.TestCase):
    def test_nombres_en_espanol(self):
        from msys.domain import caracteristicas as c
        self.assertEqual(c.nombre_capacidad("OpenSSH.Client~~~~0.0.1.0"), ("Cliente OpenSSH", c.CAT_RED))
        self.assertEqual(c.nombre_capacidad("Rsat.Dns.Tools~~~~0.0.1.0")[0], "RSAT: DNS")
        self.assertEqual(c.nombre_capacidad("Language.Speech~~~es-ES~0.0.1.0"), ("Idioma es-ES: reconocimiento de voz", c.CAT_IDIOMAS))
        self.assertEqual(c.nombre_capacidad("Language.Fonts.Jpan~~~und-JPAN~0.0.1.0")[1], c.CAT_IDIOMAS)
        self.assertEqual(c.nombre_capacidad("Microsoft.Windows.Wifi.Client.Intel.Netwtw10~~~~0.0.1.0")[1], c.CAT_CONTROLADORES)
        self.assertEqual(c.categoria_caracteristica("Microsoft-Hyper-V-All"), c.CAT_VIRTUAL)
        self.assertEqual(c.categoria_caracteristica("SMB1Protocol"), c.CAT_HEREDADO)

    def test_avisos_solo_si_esta_activada(self):
        from msys.domain import caracteristicas as c
        self.assertTrue(c.aviso_de("SMB1Protocol-Client", True))
        self.assertEqual(c.aviso_de("SMB1Protocol-Client", False), "")
        self.assertEqual(c.aviso_de("NetFx3", True), "")

    def test_plan_de_perfil(self):
        from msys.domain import caracteristicas as c
        C = c.Caracteristica
        lista = [C(c.TIPO_CARACTERISTICA, "SMB1Protocol", "SMB1", c.ACTIVADA, c.CAT_HEREDADO),
                 C(c.TIPO_CARACTERISTICA, "TelnetClient", "Telnet", c.DESACTIVADA, c.CAT_RED),
                 C(c.TIPO_CAPACIDAD, "OpenSSH.Client~~~~0.0.1.0", "Cliente OpenSSH", c.DESACTIVADA, c.CAT_RED),
                 C(c.TIPO_CAPACIDAD, "Rsat.Dns.Tools~~~~0.0.1.0", "RSAT: DNS", c.ACTIVADA, c.CAT_ADMIN)]
        endurecer = next(p for p in c.PERFILES if "Endurecer" in p.nombre)
        cambios, ya, faltan = c.plan_perfil(endurecer, lista)
        self.assertEqual([(x.tecnico, a) for x, a in cambios], [("SMB1Protocol", False)])
        self.assertIn("Telnet", ya)
        self.assertIn("TFTP", faltan)
        soporte = next(p for p in c.PERFILES if "soporte" in p.nombre)
        cambios, ya, _f = c.plan_perfil(soporte, lista)
        self.assertIn(("OpenSSH.Client~~~~0.0.1.0", True), [(x.tecnico, a) for x, a in cambios])  # capacidad por su nombre base
        self.assertIn("RSAT: DNS", ya)

    def test_origen_netfx3(self):
        from msys.domain import caracteristicas as c
        carpeta = tempfile.mkdtemp()
        self.assertFalse(c.origen_netfx3_valido(carpeta))
        open(os.path.join(carpeta, "microsoft-windows-netfx3-ondemand-package~31bf3856ad364e35~amd64~~.cab"), "w").close()
        self.assertTrue(c.origen_netfx3_valido(carpeta))


class HerramientasRedTest(unittest.TestCase):
    def test_destino_validado(self):
        from msys.domain import consultas_red as c
        self.assertEqual(c.limpiar_destino("https://www.masoft.es:8443/ruta"), "www.masoft.es")
        self.assertEqual(c.limpiar_destino(" 192.168.1.1 "), "192.168.1.1")
        self.assertEqual(c.limpiar_destino("[::1]"), "::1")
        for malo in ("-t google.com", "a b", "x;dir", "", "--help"):
            with self.assertRaises(ValueError):
                c.limpiar_destino(malo)

    def test_puertos(self):
        from msys.ui.herramientas_red import leer_puertos
        self.assertEqual(leer_puertos("80 HTTP, 443,8080 Panel web"), [(80, "HTTP"), (443, ""), (8080, "Panel web")])
        for malo in ("", "0", "70000", "http"):
            with self.assertRaises(ValueError):
                leer_puertos(malo)

    def test_informe_tecnico_y_cliente(self):
        from msys.osi.informe import generar_html
        from msys.osi.models import ERROR, OK, ResultadoPrueba
        rs = [ResultadoPrueba(3, "Ping", ERROR, "Sin respuesta de x"), ResultadoPrueba(4, "Puerto 443 (HTTPS)", OK, "Abierto", 40.0),
              ResultadoPrueba(7, "DNS vía Sistema (automático)", OK, "1.2.3.4")]
        tec = generar_html("x.com", "2026-10-03T10:00:00", rs, modo="tecnico")
        cli = generar_html("x.com", "2026-10-03T10:00:00", rs, modo="cliente")
        self.assertIn("03/10/2026 10:00:00", tec)
        self.assertIn("DNS vía Sistema", tec)
        self.assertNotIn("DNS vía Sistema", cli)  # lo técnico que está bien no sale al cliente
        self.assertIn("Sin respuesta de red", cli)
        self.assertIn("MASys", tec)
        self.assertNotIn("MAOSI", tec)


class MigracionTest(unittest.TestCase):
    def _inv(self, equipo, elementos, export=None):
        from dataclasses import asdict
        from msys.domain import migracion as m
        return {"app": "MASys", "formato": m.FORMATO, "version": 1, "equipo": equipo, "usuario": "u", "fecha": "03/10/2026 10:00",
                "elementos": [asdict(e) for e in elementos], "winget_export": export}

    def test_guardar_cargar_y_comparar(self):
        from msys.domain import migracion as m
        E = m.Elemento
        viejo = self._inv("VIEJO", [E(m.C_PROGRAMAS, "VLC", "3.0", "Instalar", "prog:vlc"),
                                    E(m.C_PROGRAMAS, "WinRAR", "7", "Instalar", "prog:winrar"),
                                    E(m.C_ASOC, "Abrir .pdf con", "Adobe Acrobat", "Elegir", "asoc:.pdf"),
                                    E(m.C_EQUIPO, "Hardware", "i5", "Informativo")])
        nuevo = self._inv("NUEVO", [E(m.C_PROGRAMAS, "VLC", "3.1", "Instalar", "prog:vlc"),
                                    E(m.C_ASOC, "Abrir .pdf con", "Microsoft Edge", "Elegir", "asoc:.pdf")])
        ruta = os.path.join(tempfile.mkdtemp(), "v.msysinv")
        m.guardar(viejo, ruta)
        cargado = m.cargar(ruta)
        estados = {e.elemento: est for e, est, _d in m.comparar(cargado, nuevo)}
        self.assertEqual(estados, {"VLC": m.PRESENTE, "WinRAR": m.FALTA, "Abrir .pdf con": m.DISTINTO, "Hardware": m.INFORMATIVO})
        with open(ruta, "w", encoding="utf-8") as f:
            f.write('{"otra": 1}')
        with self.assertRaises(ValueError):
            m.cargar(ruta)

    def test_lista_para_winget_conserva_la_fuente(self):
        import json
        from msys.domain import migracion as m
        export = {"$schema": "x", "Sources": [{"SourceDetails": {"Name": "winget"},
                                               "Packages": [{"PackageIdentifier": "VideoLAN.VLC"}, {"PackageIdentifier": "7zip.7zip"}]}]}
        self.assertEqual(m.ids_winget(export), ["7zip.7zip", "VideoLAN.VLC"])
        ruta = os.path.join(tempfile.mkdtemp(), "i.json")
        n = m.import_winget(self._inv("V", [], export), ["videolan.vlc"], ruta)
        self.assertEqual(n, 1)
        with open(ruta, encoding="utf-8") as f:
            d = json.load(f)
        self.assertEqual(d["Sources"][0]["SourceDetails"]["Name"], "winget")
        self.assertEqual([p["PackageIdentifier"] for p in d["Sources"][0]["Packages"]], ["VideoLAN.VLC"])

    def test_lista_de_comprobacion_html(self):
        from msys.domain import migracion as m
        inv = self._inv("VIEJO", [m.Elemento(m.C_CORREO, "a@b.es", "<script>", "Añadir", "correo:a@b.es", True)])
        h = m.informe_html(inv)
        self.assertIn("&lt;script&gt;", h)        # todo escapado
        self.assertEqual(h.count("<script>"), 1)    # solo el del buscador (como MIGRACION.PS1)
        self.assertIn('<span class="badge-no">IMPORTANTE</span>', h)  # importante resaltado
        self.assertIn("INFORME DE MIGRACIÓN - VIEJO", h)
        self.assertIn("<h2>CORREO (1)</h2>", h)
        self.assertIn("Las contraseñas NO se recopilan", h)   # aviso del apartado, como el script
        comp = m.comparar(inv, self._inv("NUEVO", []))
        self.assertIn("1 faltan", m.informe_html(inv, comp, "NUEVO"))

    def test_lista_con_casillas_como_antes(self):
        """Botón de arriba: la lista de comprobación de antes (casillas, escapado, importantes y comparación)."""
        from msys.domain import migracion as m
        inv = self._inv("VIEJO", [m.Elemento(m.C_CORREO, "a@b.es", "<script>", "Añadir", "correo:a@b.es", True)])
        h = m.lista_html(inv)
        self.assertIn("☐", h)
        self.assertIn("&lt;script&gt;", h)
        self.assertNotIn("<script>", h)
        self.assertIn('class="imp"', h)
        self.assertIn("Lista de comprobación", h)
        comp = m.comparar(inv, self._inv("NUEVO", []))
        self.assertIn("1 faltan", m.lista_html(inv, comp, "NUEVO"))

    def test_favoritos_y_tamano(self):
        from msys.domain import migracion as m
        marcadores = {"roots": {"bookmark_bar": {"type": "folder", "children": [{"type": "url"}, {"type": "folder", "children": [{"type": "url"}]}]},
                                "other": {"type": "folder", "children": [{"type": "url"}]}}}
        self.assertEqual(m._contar_favoritos(marcadores), 3)
        carpeta = tempfile.mkdtemp()
        os.makedirs(os.path.join(carpeta, "sub"))
        for nombre, tam in (("a.bin", 100), (os.path.join("sub", "b.bin"), 50)):
            with open(os.path.join(carpeta, nombre), "wb") as f:
                f.write(b"x" * tam)
        self.assertEqual(m.tamano_carpeta(carpeta), (150, 2, True))


def _informe_ejemplo():
    import json
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "datos", "informe_movil_ejemplo.json"), encoding="utf-8") as f:
        return json.load(f)


class MovilTest(unittest.TestCase):
    def test_mensajes_y_archivo(self):
        from msys.movil import cripto
        clave = os.urandom(32)
        blob = cripto.json_sellado(clave, {"a": "ñ"}, cripto.aad("d1", "/api/informe"))
        self.assertEqual(cripto.json_abierto(clave, blob, cripto.aad("d1", "/api/informe")), {"a": "ñ"})
        with self.assertRaises(cripto.ErrorCifrado):
            cripto.json_abierto(clave, blob, cripto.aad("d2", "/api/informe"))  # otro móvil: no vale
        ruta = os.path.join(tempfile.mkdtemp(), "x.msysmov")
        cripto.escribir_archivo(ruta, _informe_ejemplo(), "secreto123", iteraciones=20_000)
        self.assertEqual(cripto.leer_archivo(ruta, "secreto123")["formato"], "masysmov_informe")
        with self.assertRaises(cripto.ErrorCifrado):
            cripto.leer_archivo(ruta, "mala")

    def test_vector_comun_con_el_movil(self):
        """El mismo vector está en LinkCryptoTest.kt de MASysMov: si cambia uno, cambia el otro."""
        import gzip
        from msys.movil import cripto
        clave = bytes(range(32))
        nonce = bytes(range(100, 112))
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        blob = nonce + AESGCM(clave).encrypt(nonce, b"hola MASys", cripto.aad("movil-1", "/api/informe"))
        self.assertEqual(blob.hex(), VECTOR_HEX)
        self.assertEqual(cripto.abrir(clave, bytes.fromhex(VECTOR_HEX), cripto.aad("movil-1", "/api/informe")), b"hola MASys")
        self.assertEqual(cripto.clave_de_contrasena("secreto", b"0123456789abcdef", 1000).hex(), VECTOR_PBKDF2_HEX)

    def test_servidor_extremo_a_extremo(self):
        """Un móvil simulado empareja, envía su informe, recibe un encargo y devuelve el resultado."""
        import base64
        import json
        import urllib.request
        from msys.movil import cripto, servidor
        from msys.movil.almacen import HECHO, Almacen
        alm = Almacen(os.path.join(tempfile.mkdtemp(), "m.db"))
        recibidos = []
        srv = servidor.ServidorMovil(alm, al_recibir=lambda d, i: recibidos.append((d, i)), puerto=0)
        srv.puerto = 0
        srv.arrancar()
        puerto = srv._httpd.server_address[1]
        try:
            qr = srv.nueva_sesion()
            clave = base64.b64decode(qr["key"])

            def post(ruta, obj, extra=None, k=clave, disp="movil-1"):
                req = urllib.request.Request(f"http://127.0.0.1:{puerto}{ruta}", method="POST",
                                             data=cripto.json_sellado(k, obj, cripto.aad(disp, ruta)),
                                             headers=dict({"X-MASys-Device": disp}, **(extra or {})))
                with urllib.request.urlopen(req, timeout=10) as r:
                    return cripto.json_abierto(k, r.read(), cripto.aad(disp, ruta + "|resp"))
            self.assertTrue(post("/api/pair", {"deviceId": "movil-1", "deviceName": "Pixel 7"}, {"X-MASys-Pair": qr["pair"]})["ok"])
            with self.assertRaises(urllib.error.HTTPError):  # el código de emparejado es de un solo uso
                post("/api/pair", {"deviceId": "movil-2"}, {"X-MASys-Pair": qr["pair"]}, disp="movil-2")
            alm.nuevo_encargo("movil-1", "puerto", {"host": "", "puertos": [3389]})
            r = post("/api/informe", _informe_ejemplo())
            # 4.2.7: además del de puertos, el de copias de apps (activado por defecto, sin guardarse)
            self.assertEqual([e["tipo"] for e in r["encargos"]], ["puerto", "copias"])
            self.assertEqual(recibidos[0][0], "movil-1")
            self.assertEqual(len(alm.informes("movil-1")), 1)
            self.assertTrue(post("/api/encargo", {"id": r["encargos"][0]["id"], "resultado": {"puertos": [{"puerto": 3389, "estado": "Cerrado"}]}})["ok"])
            self.assertEqual(alm.encargos("movil-1")[0]["estado"], HECHO)
            with self.assertRaises(urllib.error.HTTPError):  # con otra clave: rechazado
                post("/api/informe", _informe_ejemplo(), k=os.urandom(32))
        finally:
            srv.parar()

    def test_copias_por_trozos(self):
        """Copias de apps (4.2.7): lista, trozos cifrados con su posición, 409 fuera de orden, reanudar y SHA-256."""
        import base64
        import hashlib
        import urllib.error
        import urllib.request
        from msys.movil import cripto, servidor
        from msys.movil.almacen import Almacen
        tmp = tempfile.mkdtemp()
        alm = Almacen(os.path.join(tmp, "m.db"))
        avisos = []
        srv = servidor.ServidorMovil(alm, puerto=0, al_copia=avisos.append)
        srv.copias.fijar_carpeta(os.path.join(tmp, "COPIAS"))
        srv.arrancar()
        puerto = srv._httpd.server_address[1]
        try:
            qr = srv.nueva_sesion()
            clave = base64.b64decode(qr["key"])

            def req(ruta, cuerpo, extra=None):
                r = urllib.request.Request(f"http://127.0.0.1:{puerto}{ruta}", method="POST", data=cuerpo,
                                           headers=dict({"X-MASys-Device": "movil-1"}, **(extra or {})))
                with urllib.request.urlopen(r, timeout=10) as x:
                    return cripto.json_abierto(clave, x.read(), cripto.aad("movil-1", ruta + "|resp"))

            def post(ruta, obj, extra=None):
                return req(ruta, cripto.json_sellado(clave, obj, cripto.aad("movil-1", ruta)), extra)

            def trozo(id_, desde, datos, k=clave):
                ruta = "/api/copias/trozo"
                return req(ruta, cripto.sellar(k, datos, cripto.aad("movil-1", f"{ruta}|{id_}|{desde}")),
                           {"X-MASys-Copia": id_, "X-MASys-Desde": str(desde)})

            post("/api/pair", {"deviceId": "movil-1", "deviceName": "Redmi Note 13"}, {"X-MASys-Pair": qr["pair"]})
            datos = os.urandom(9 * 1024 * 1024 + 123)
            sha = hashlib.sha256(datos).hexdigest()
            copia = {"id": sha, "nombre": "WhatsApp_2.24_20261008_1530.zip", "tamano": len(datos), "pkg": "com.whatsapp",
                     "app": "WhatsApp", "version": "2.24", "creada": 1, "contenido": "Con datos"}
            malas = [dict(copia, id="x"), dict(copia, id="a" * 64, nombre="../../malo.zip"), dict(copia, id="b" * 64, tamano=-1)]
            self.assertEqual(post("/api/copias/lista", {"copias": [copia] + malas})["faltan"], [{"id": sha, "desde": 0}])
            T = 4 * 1024 * 1024
            self.assertFalse(trozo(sha, 0, datos[:T])["completa"])
            with self.assertRaises(urllib.error.HTTPError) as e:  # fuera de orden
                trozo(sha, 0, datos[:T])
            self.assertEqual(e.exception.code, 409)
            with self.assertRaises(urllib.error.HTTPError) as e:  # el trozo cifrado para otra posición no vale
                ruta = "/api/copias/trozo"
                req(ruta, cripto.sellar(clave, datos[T:2 * T], cripto.aad("movil-1", f"{ruta}|{sha}|0")), {"X-MASys-Copia": sha, "X-MASys-Desde": str(T)})
            self.assertEqual(e.exception.code, 401)
            # Se corta y se reanuda donde se quedó
            self.assertEqual(post("/api/copias/lista", {"copias": [copia]})["faltan"], [{"id": sha, "desde": T}])
            trozo(sha, T, datos[T:2 * T])
            r = trozo(sha, 2 * T, datos[2 * T:])
            self.assertTrue(r["completa"])
            final = os.path.join(tmp, "COPIAS", "Redmi_Note_13", copia["nombre"])
            with open(final, "rb") as f:
                self.assertEqual(f.read(), datos)
            self.assertEqual(post("/api/copias/lista", {"copias": [copia]})["faltan"], [])
            self.assertTrue(avisos)
            # Llega dañada (SHA distinta): se borra y se pide entera otra vez
            otra = dict(copia, id=hashlib.sha256(b"otra").hexdigest(), nombre="Otra_1_20261008_1600.zip", tamano=10)
            post("/api/copias/lista", {"copias": [otra]})
            r = trozo(otra["id"], 0, b"0123456789")
            self.assertFalse(r["ok"])
            self.assertEqual(post("/api/copias/lista", {"copias": [otra]})["faltan"], [{"id": otra["id"], "desde": 0}])
            # Borrada a mano en el PC: se vuelve a pedir
            os.remove(final)
            self.assertEqual(post("/api/copias/lista", {"copias": [copia]})["faltan"], [{"id": sha, "desde": 0}])

            # 4.2.8 / MASysMov 1.2.4: copia en carpeta, archivo a archivo; el mismo .apk en dos copias son dos archivos
            from msys.movil import copias as cp
            apk = os.urandom(5000)
            sha_apk = hashlib.sha256(apk).hexdigest()
            partes = []
            for carpeta in ("WhatsApp_2.24_20261008_1530", "WhatsApp_2.24_20261009_0900"):
                nombre = f"{carpeta}/WhatsApp_2.24.apk"
                partes.append(dict(copia, id=cp.id_parte(nombre, sha_apk), sha=sha_apk, nombre=nombre, tamano=len(apk)))
            malas = [dict(partes[0], id=sha_apk),
                     dict(partes[0], id="c" * 64),
                     dict(partes[0], nombre="a/b/WhatsApp.apk", id=cp.id_parte("a/b/WhatsApp.apk", sha_apk)),
                     dict(partes[0], nombre="WhatsApp/x.exe", id=cp.id_parte("WhatsApp/x.exe", sha_apk))]
            faltan = post("/api/copias/lista", {"copias": partes + malas})["faltan"]
            self.assertEqual(faltan, [{"id": p["id"], "desde": 0} for p in partes])
            for p in partes:
                self.assertTrue(trozo(p["id"], 0, apk)["completa"])
                with open(os.path.join(tmp, "COPIAS", "Redmi_Note_13", *p["nombre"].split("/")), "rb") as f:
                    self.assertEqual(f.read(), apk)
            self.assertEqual(post("/api/copias/lista", {"copias": partes})["faltan"], [])
            grupos = cp.agrupar(alm.copias("movil-1"))
            carpeta = next(g for g in grupos if g["nombre"] == "WhatsApp_2.24_20261009_0900")
            self.assertEqual((carpeta["estado"], carpeta["instalable"]), ("Recibida", "1 .apk"))
            self.assertTrue(os.path.isdir(carpeta["ruta"]))
        finally:
            srv.parar()

    def test_id_parte_vector_comun_con_el_movil(self):
        # Mismo vector que EspacioTest.idDeArchivoDeCopiaVectorComunConElPc (MASysMov 1.2.4)
        from msys.movil import copias as cp
        self.assertEqual(cp.id_parte("WhatsApp_2.24_20261008_1530/WhatsApp_2.24.apk", "a" * 64),
                         "cdf79c284c7eb320cb19e07a0c79d541e1ef691fdbaa43ee94e8bbbf0c2dee9a")
        self.assertTrue(cp.nombre_valido("WhatsApp_2.24_20261008_1530/WhatsApp_2.24_split_config.arm64_v8a.apk"))
        self.assertTrue(cp.nombre_valido("Copia_vieja.zip"))
        self.assertTrue(cp.nombre_valido("WhatsApp_2.24.apk"))  # MASysMov 1.2.6: copia sin datos = solo el .apk principal
        fila = dict(nombre="WhatsApp_2.24.apk", app="WhatsApp", pkg="com.whatsapp", version="2.24", creada=1, tamano=5,
                    contenido="Sin datos", completa=False, recibidos=0, ruta="")
        self.assertEqual(cp.agrupar([fila])[0]["instalable"], "1 .apk")
        self.assertTrue(cp.nombre_valido("WhatsApp_2.24.apks"))  # MASysMov 1.2.7: app en piezas en un archivo
        self.assertEqual(cp.agrupar([dict(fila, nombre="WhatsApp_2.24.apks")])[0]["instalable"], "Varias piezas (.apks)")
        for malo in ("../x.apk", "a\\b.apk", ".oculta/x.apk", "a/b/c.apk", "a/x.exe", "x y.apk", "x.exe"):
            self.assertFalse(cp.nombre_valido(malo), malo)
        self.assertEqual(cp.carpeta_copia("A_1/b.apk"), "A_1")
        self.assertEqual(cp.carpeta_copia("Vieja.zip"), "Vieja.zip")

    def test_bloques_nuevos_masysmov_124(self):
        from msys.movil import analisis as an
        inf = _informe_ejemplo()
        # Informe antiguo: todo vacío, sin errores (salvo consumo de datos y velocidad, que ya venían y desde 5.3.0 se ven)
        self.assertTrue(all(not v for k, v in an.extra(inf).items() if k not in ("trafico", "red_velocidad")))
        inf.update({
            "bateriaExtra": {"tension": 4012, "tecnologia": "Li-ion", "gastoHabitualPorHora": 4.5, "horasRestantes": 11.2,
                             "historial": [[1759900000000, 80, 0], [1759903600000, 76, 0]]},
            "bateriaApps": {"totalMah": 900, "apps": [{"pkg": "com.whatsapp", "mah": 120.4, "pct": 13.4}]},
            "dnsPrivado": {"modo": "hostname", "servidor": "dns.adguard.com", "texto": "AdGuard", "activo": True},
            "rastreadores": {"dias": 7, "cortar": False, "consultas": 340, "cortadas": 0,
                             "servicios": [{"servicio": "AppLovin", "consultas": 300, "cortadas": 0, "apps": 2, "seCorta": True}],
                             "apps": [{"pkg": "com.linterna", "consultas": 300, "cortadas": 0, "servicios": 1}]},
            "procesos": {"cpuTotal": 23.5, "procesos": 410, "termico": "Normal",
                         "apps": [{"pkg": "com.whatsapp", "nombre": "WhatsApp", "memoriaKb": 250000, "cpu": 2.5, "procesos": 3}]},
            "bluetooth": {"activo": True, "dispositivos": [{"nombre": "Buds", "tipo": "Audio", "conectado": True}]},
            "espacio": {"temporales": 3 * 1024 ** 3, "liberado": {"total": 5 * 1024 ** 3, "ultimo": 1024 ** 3, "fecha": 1759900000000},
                        "apps": [{"pkg": "com.whatsapp", "app": 1, "datos": 2, "cache": 3, "total": 6}],
                        "sinUsar": [{"pkg": f"com.a{i}", "ultimoUso": 0, "bytes": 1000} for i in range(4)],
                        "copias": {"numero": 1, "lista": [{"app": "WhatsApp", "version": "2.24", "creada": 1759900000000, "bytes": 9, "apks": 2}]},
                        "eliminados": {"numero": 2, "bytes": 10, "dias": 7},
                        "recorrido": {"carpetas": [{"ruta": "DCIM", "bytes": 10, "archivos": 3}], "tipos": [{"tipo": "Vídeos", "bytes": 9, "archivos": 1}],
                                      "grandes": [{"ruta": "DCIM/Camera/v.mp4", "bytes": 9, "modificado": 1759900000000, "tipo": "Vídeos"}],
                                      "mensajeria": [{"app": "WhatsApp", "carpeta": "Vídeos", "ruta": "Android/media/x", "bytes": 5, "archivos": 1}]}},
        })
        ex = an.extra(inf)
        # Las tablas de MASysMov 1.3.2 (5.3.0) las prueba tests/test_moviles.py con su propio informe de ejemplo
        nuevas_132 = {"bat_salud", "hardware", "red_sim", "red_wifi", "red_cercanas", "red_guardadas", "red_bt", "pruebas"}
        self.assertTrue(all(ex[k] for k, _p, _s, _c in an.EXTRA if k not in nuevas_132),
                        [k for k, v in ex.items() if not v and k not in nuevas_132])
        self.assertEqual(ex["esp_grandes"][0][:2], ["v.mp4", "DCIM/Camera"])
        self.assertEqual(ex["esp_copias"][0][-1], "2 .apk")
        self.assertEqual(ex["bat_hist"][0][2], "76 %")  # lo más reciente arriba
        datos = [d for d, _v, _s in an.ficha(inf)]
        for d in ("DNS privado", "Bluetooth", "Procesos", "Temporales de las apps", "Liberado con MASysMov", "Batería (detalle)"):
            self.assertIn(d, datos)
        titulos = [t for _s, t, _d in an.hallazgos(inf)]
        for t in ("Rastreadores sin cortar", "Muchos temporales", "4 apps sin usar en 30 días"):
            self.assertIn(t, titulos)
        html = an.informe_html("Móvil", inf)
        self.assertIn("Rastreadores · Servicios", html)
        self.assertIn("AppLovin", html)

    def test_vector_trozo_comun_con_el_movil(self):
        """Mismo vector que EspacioTest.trozoDeCopiaVectorComunConElPc (MASysMov)."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from msys.movil import cripto
        clave = bytes(range(32))
        nonce = bytes(100 + i for i in range(12))
        aad = cripto.aad("movil-1", "/api/copias/trozo|abc123|4194304")
        blob = nonce + AESGCM(clave).encrypt(nonce, b"trozo de copia", aad)
        self.assertEqual(blob.hex(), "6465666768696a6b6c6d6e6f3c69b11c16c932fb1e013098b30448c7fc2a88e5dac36e6ef08855229022")
        self.assertEqual(cripto.abrir(clave, blob, aad), b"trozo de copia")

    def test_analisis(self):
        from msys.movil import analisis as an
        inf = _informe_ejemplo()
        self.assertEqual(an.rastreador("ads.applovin.com"), "AppLovin")
        self.assertEqual(an.rastreador("applovin.com.evil.net"), "")
        titulos = [t for _s, t, _d in an.hallazgos(inf)]
        self.assertIn("Parche de seguridad antiguo", titulos)
        self.assertTrue(any("fuera de una tienda" in t for t in titulos))
        linterna = next(a for a in an.filas_apps(inf) if a["pkg"] == "com.linterna.gratis")
        self.assertIn("fuera de tienda", linterna["notas"])
        self.assertIn("Android antiguo", linterna["notas"])
        self.assertFalse(any(a["pkg"] == "com.android.chrome" for a in an.filas_apps(inf)))  # las del sistema no
        anterior = dict(inf, apps=[a for a in inf["apps"] if a["pkg"] != "com.whatsapp"])
        self.assertIn(("App nueva", "WhatsApp"), [(t, a) for t, a, _d in an.cambios(inf, anterior)])
        h = an.informe_html("Pixel <b>", inf)
        self.assertIn("Pixel &lt;b&gt;", h)


VECTOR_HEX = "6465666768696a6b6c6d6e6f2074b20759a417cd4711dff9f98962d3dc499a6e60f58f47bb9a"
VECTOR_PBKDF2_HEX = "22bd5b51a8a2d99cecdc8e9f3eab2992b5a03b9a9cbd073c257c795f40fb67f4"


if __name__ == "__main__":
    unittest.main()
