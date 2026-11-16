# -*- coding: utf-8 -*-
r"""Outlook clásico: perfiles (registro), configuración (.reg y firmas) y alta de cuentas con un archivo PRF (exportar a PST
ya lo tenía MASys: «📤 Exportar a PST»). Es la lógica de GestCorreov4.ps1 con estos arreglos:

- Outlook se cierra pidiéndoselo (como al pulsar Salir); solo se fuerza si el usuario lo acepta después.
- Cada versión de Outlook va a su propio .reg (la v4 las escribía en el mismo archivo y se pisaban).
- El PRF se deja en %TEMP%\MASys_correo (la v4 lo borraba a los 10 s, a veces antes de que Outlook lo leyera). No lleva
  contraseña.

El Outlook nuevo (la app de la Tienda) no tiene COM ni perfiles: aquí solo sirve el clásico.
"""
import ctypes
import datetime as _dt
import logging
import os
import re
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass


log = logging.getLogger("msys.correo.outlook")
VERSIONS = ["16.0", "15.0", "14.0", "12.0", "11.0", "10.0", "9.0"]
_NO_WINDOW = 0x08000000   # CREATE_NO_WINDOW


class OutlookError(Exception):
    pass


@dataclass
class Profile:
    name: str
    version: str

    @property
    def key(self) -> str:
        return rf"HKEY_CURRENT_USER\Software\Microsoft\Office\{self.version}\Outlook\Profiles\{self.name}"

    def label(self) -> str:
        return f"{self.name} (Outlook {self.version})"


# ======================================================================== registro y proceso
def _hkcu_exists(sub: str) -> bool:
    import winreg
    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub))
        return True
    except OSError:
        return False


def _subkeys(sub: str) -> list[str]:
    import winreg
    out = []
    try:
        k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub)
    except OSError:
        return out
    with k:
        i = 0
        while True:
            try:
                out.append(winreg.EnumKey(k, i))
            except OSError:
                break
            i += 1
    return out


def versions() -> list[str]:
    return [v for v in VERSIONS if _hkcu_exists(rf"Software\Microsoft\Office\{v}\Outlook\Profiles")]


def profiles() -> list[Profile]:
    return [Profile(n, v) for v in versions() for n in _subkeys(rf"Software\Microsoft\Office\{v}\Outlook\Profiles")]


def default_profile() -> str:
    import winreg
    for v in versions():
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Microsoft\Office\{v}\Outlook") as k:
                return str(winreg.QueryValueEx(k, "DefaultProfile")[0])
        except OSError:
            continue
    return ""


def rules_keys(version: str) -> list[str]:
    """Reglas guardadas en el registro (Outlook antiguos; los actuales las guardan en el buzón)."""
    base = rf"Software\Microsoft\Office\{version}\Outlook\Rules"
    return [rf"HKEY_CURRENT_USER\{base}\{n}" for n in _subkeys(base)] if _hkcu_exists(base) else []


def signatures_dir() -> str:
    return os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Signatures")


def signatures() -> list[str]:
    d = signatures_dir()
    if not os.path.isdir(d):
        return []
    return sorted(os.path.splitext(f)[0] for f in os.listdir(d) if f.lower().endswith(".htm"))


def exe_path() -> str:
    import winreg
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\OUTLOOK.EXE") as k:
                p = str(winreg.QueryValueEx(k, "")[0]).strip('"')
                if os.path.isfile(p):
                    return p
        except OSError:
            pass
    pf, pf86 = os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    for base in (pf, pf86):
        for sub in (r"Microsoft Office\root\Office16", r"Microsoft Office\Office16", r"Microsoft Office\root\Office15", r"Microsoft Office\Office15"):
            p = os.path.join(base, sub, "OUTLOOK.EXE")
            if os.path.isfile(p):
                return p
    return ""


def classic_available() -> bool:
    return bool(exe_path())


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def running() -> bool:
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq OUTLOOK.EXE", "/NH", "/FO", "CSV"], capture_output=True, text=True,
                             timeout=10, creationflags=_NO_WINDOW).stdout
        return "outlook.exe" in out.lower()
    except (OSError, subprocess.SubprocessError):
        return False


def close(wait: float = 15) -> bool:
    """Pide a Outlook que se cierre como al pulsar Salir (guarda lo pendiente). True si ya no está abierto."""
    if not running():
        return True
    try:
        import pythoncom
        import win32com.client
        pythoncom.CoInitialize()
        try:
            win32com.client.GetActiveObject("Outlook.Application").Quit()
        finally:
            pythoncom.CoUninitialize()
    except Exception as e:  # noqa: BLE001 - sin objeto activo: se prueba cerrando la ventana
        log.info("Quit por COM: %s", e)
        subprocess.run(["taskkill", "/IM", "OUTLOOK.EXE"], capture_output=True, timeout=10, creationflags=_NO_WINDOW)
    end = time.time() + wait
    while time.time() < end:
        if not running():
            return True
        time.sleep(0.5)
    return not running()


def force_close() -> bool:
    subprocess.run(["taskkill", "/F", "/IM", "OUTLOOK.EXE"], capture_output=True, timeout=10, creationflags=_NO_WINDOW)
    time.sleep(1.5)
    return not running()


def safe_name(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", s).strip() or "cuenta"


# ======================================================================== configuración
def reg_export(key: str, path: str) -> tuple[bool, str]:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    try:
        r = subprocess.run(["reg", "export", key, path, "/y"], capture_output=True, text=True, timeout=60, creationflags=_NO_WINDOW)
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)
    return r.returncode == 0, (r.stderr or r.stdout).strip()


def version_suffix(version: str, all_versions: list[str]) -> str:
    """Solo se añade la versión al nombre si hay más de una (así no se pisan)."""
    return f"_Outlook{version.split('.')[0]}" if len(all_versions) > 1 else ""


def export_full_config(folder: str) -> list[str]:
    """Copia de toda la configuración de Outlook (HKCU\\...\\Outlook), un .reg por versión. Devuelve los archivos creados."""
    vs = [v for v in versions() if _hkcu_exists(rf"Software\Microsoft\Office\{v}\Outlook")]
    if not vs:
        raise OutlookError("No se encontró ninguna versión de Outlook")
    ts = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = []
    for v in vs:
        f = os.path.join(folder, f"OutlookConfig{version_suffix(v, vs)}_{ts}.reg")
        ok, err = reg_export(rf"HKEY_CURRENT_USER\Software\Microsoft\Office\{v}\Outlook", f)
        if not ok:
            raise OutlookError(f"Outlook {v}: {err or 'reg export falló'}")
        out.append(f)
    return out


def zip_signatures(names: list[str] | None, path: str) -> int:
    """Firmas al ZIP: todas (names=None) o las elegidas, con sus archivos (.htm/.rtf/.txt) y su carpeta de imágenes."""
    d = signatures_dir()
    if not os.path.isdir(d):
        raise OutlookError("No hay carpeta de firmas")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    n = 0
    target = os.path.normcase(os.path.abspath(path))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(d):
            for f in files:
                full = os.path.join(root, f)
                if os.path.normcase(os.path.abspath(full)) == target:
                    continue        # el propio ZIP, si se guarda dentro de la carpeta de firmas
                rel = os.path.relpath(full, d)
                top = rel.split(os.sep)[0]
                stem = os.path.splitext(top)[0]
                if names is not None and not any(stem == s or top == f"{s}_files" or top == f"{s}_archivos" for s in names):
                    continue
                z.write(full, rel)
                n += 1
    return n


# ======================================================================== alta de cuenta (PRF)
def build_prf(profile: str, kind: str, display: str, email: str, user: str, in_host: str, in_port: int, in_ssl: bool,
              out_host: str, out_port: int, out_ssl: bool, smtp_auth: bool = True) -> str:
    kind = kind.upper()
    s_in, s_out, auth = int(bool(in_ssl)), int(bool(out_ssl)), int(bool(smtp_auth))
    return f"""[General]
ProfileName={profile}
DefaultProfile=TRUE
OverwriteProfile=FALSE
ModifyExistingProfile=TRUE

[{profile}]

[ServiceList]
ServiceCount=1
Service1=Cuenta

[Cuenta]
ServiceName=Internet E-mail
AccountName={email}
DisplayName={display}
EmailAddress={email}
ReplyToEmailAddress={email}
UserName={user}
AccountType={kind}
IMAPServer={in_host}
IMAPPort={in_port}
IMAPSecureConnection={s_in}
POPServer={in_host}
POPPort={in_port}
POPSecureConnection={s_in}
SMTPServer={out_host}
SMTPPort={out_port}
SMTPUseAuth={auth}
SMTPSecureConnection={s_out}
"""


def launch_with_prf(prf_text: str) -> str:
    """Escribe el PRF en %TEMP%\\MASys_correo y arranca Outlook con /importprf (Outlook tiene que estar cerrado). Devuelve la ruta."""
    exe = exe_path()
    if not exe:
        raise OutlookError("No se encontró Outlook clásico (OUTLOOK.EXE)")
    carpeta = os.path.join(tempfile.gettempdir(), "MASys_correo")
    os.makedirs(carpeta, exist_ok=True)
    path = os.path.join(carpeta, f"cuenta_{_dt.datetime.now():%Y%m%d_%H%M%S}.prf")
    with open(path, "w", encoding="utf-8") as f:
        f.write(prf_text)
    subprocess.Popen([exe, "/importprf", path], creationflags=_NO_WINDOW)
    return path
