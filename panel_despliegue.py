# -*- coding: utf-8 -*-
"""Panel de despliegue de LactIA. Se corre EN SERVER-DELPRO (por RDP); no es parte de la web.

Reemplaza dos cosas que se hacían a mano:
  1. Cargar las variables de entorno (un `setx` por cada una).
  2. Actualizar / iniciar / parar LactIA, el poller del gateway IoT y el túnel de Cloudflare.

POR QUÉ UNA APP DE ESCRITORIO APARTE, Y NO UNA PÁGINA DE LACTIA: tiene que poder parar y
levantar LactIA sin llevarse puesta, y maneja secretos y procesos de producción -- una página
alcanzable por el túnel de Cloudflare sería una puerta que no hace falta abrir. Esto no escucha
en ningún puerto.

LA REGLA QUE ORDENA TODO EL ARCHIVO: nunca confiar en que "el comando anduvo". Cada acción
verifica el estado real después (puerto, procesos, valor leído de vuelta) -- es la lección de
`actualizar.bat` sin Administrador, que falla en silencio y deja corriendo el código viejo.

Las variables van al registro de Windows, igual que `setx`, pero escribiendo directo: `setx`
trunca a 1024 caracteres y se confunde con las comillas. Y como `setx` NO llega a los procesos
que ya existen, al iniciar LactIA se le pasan las variables FRESCAS leídas del registro
(`Entorno.para_hijo`) en vez de las que hereda el panel de cuando se abrió.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import queue
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from typing import Callable, NamedTuple

try:
    import winreg
except ImportError:  # solo existe en Windows; el panel no tiene sentido en otro lado
    winreg = None

CARPETA = os.path.dirname(os.path.abspath(__file__))
RUTA_AJUSTES = os.path.join(CARPETA, "panel_despliegue.json")
CARPETA_LOGS = os.path.join(CARPETA, "logs_panel")

CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


class ErrorPanel(Exception):
    """Un fallo esperable (falta un archivo, no hay permiso...) con un mensaje listo para mostrar."""


# ---------------------------------------------------------------------------
# Ajustes del panel (solo rutas y opciones: NUNCA secretos, ese archivo no es privado)
# ---------------------------------------------------------------------------
AJUSTES_DEFECTO = {
    "python": "",                    # vacío = el intérprete con que se abrió el panel
    "puerto": 5310,
    "script_lactia": "servidor.py",
    "script_iot": "iot_lavado.py",
    "rama": "master",
    "cloudflared_modo": "auto",      # auto | proceso | servicio
    "cloudflared_exe": "",
    "cloudflared_args": "tunnel run",
    "cloudflared_servicio": "cloudflared",
    "url_publica": "",               # vacío = LACTIA_URL_PUBLICA o https://www.analiticastambo.com
    "variables_extra": [],           # nombres que el tambo agregó a mano (para mostrarlas e inyectarlas)
}


def cargar_ajustes(ruta: str = RUTA_AJUSTES) -> dict:
    aj = {k: (list(v) if isinstance(v, list) else v) for k, v in AJUSTES_DEFECTO.items()}
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            guardado = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return aj
    if isinstance(guardado, dict):
        for k, defecto in AJUSTES_DEFECTO.items():
            if k in guardado and isinstance(guardado[k], type(defecto)):
                aj[k] = guardado[k]
    return aj


def guardar_ajustes(aj: dict, ruta: str = RUTA_AJUSTES) -> None:
    limpio = {k: aj[k] for k in AJUSTES_DEFECTO if k in aj}
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(limpio, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Catálogo de variables que lee la app
# ---------------------------------------------------------------------------
class Var(NamedTuple):
    nombre: str          # nombre exacto, o prefijo si `prefijo` es True
    grupo: str
    secreta: bool
    ayuda: str
    prefijo: bool = False


# Hay una prueba (tests/test_panel_despliegue.py) que recorre el código y falla si alguna
# variable que la app lee no está acá: este catálogo no se puede desactualizar en silencio.
CATALOGO: tuple[Var, ...] = (
    Var("DELPRO_PWD_", "Base DelPro", True,
        "Contraseña del usuario de lectura de la base DDM. Termina con el id del tambo "
        "(DELPRO_PWD_PONDEROSA, DELPRO_PWD_LAMARTINA...).", True),
    Var("DELPRO_HOST", "Servidor LactIA", False,
        "0.0.0.0 = visible desde la red del tambo; 127.0.0.1 = solo esta PC. Sin valor, "
        "LactIA arranca en 0.0.0.0."),
    Var("DELPRO_PORT", "Servidor LactIA", False, "Puerto de LactIA. Sin valor, 5310."),
    Var("LACTIA_URL_PUBLICA", "Servidor LactIA", False,
        "Dirección pública (con ella se valida la firma del webhook de WhatsApp). "
        "Sin valor, https://www.analiticastambo.com."),
    Var("LACTIA_URL", "Precalentar", False, "Dirección que usa precalentar.py. Sin valor, la local."),
    Var("LACTIA_TAMBO", "Precalentar", False, "Tambo que precalienta precalentar.py. Sin valor, ponderosa."),
    Var("ANTHROPIC_API_KEY", "Inteligencia artificial", True, "Clave de la API de Anthropic (agente y consultas)."),
    Var("FFMPEG_PATH", "Cámaras", False, "Ruta de ffmpeg para las cámaras. Sin valor se busca en el PATH."),
    Var("PODAL_CAM_", "Cámaras", True,
        "Fuente de una cámara podal (URL RTSP, que puede traer usuario y clave). "
        "PODAL_CAM_POSICION_<TAMBO> / PODAL_CAM_MARCHA_<TAMBO>.", True),
    Var("MILKY_USUARIO", "Caudalímetro (Milky)", False, "Usuario de la API de Milky (Controllac)."),
    Var("MILKY_PASSWORD", "Caudalímetro (Milky)", True, "Contraseña de la API de Milky."),
    Var("LASER_USUARIO", "La Serenísima", False, "Usuario del portal de La Serenísima."),
    Var("LASER_PASSWORD", "La Serenísima", True, "Contraseña del portal de La Serenísima."),
    Var("HASTEN_USUARIO", "Haasten (mixer)", False, "Usuario de Haasten (se escribe con UNA 'a')."),
    Var("HASTEN_PASSWORD", "Haasten (mixer)", True, "Contraseña de Haasten."),
    Var("SENSEHUB_USER_", "SenseHub (collares)", False, "Usuario del controlador. SENSEHUB_USER_<TAMBO>.", True),
    Var("SENSEHUB_PWD_", "SenseHub (collares)", True, "Contraseña del controlador. SENSEHUB_PWD_<TAMBO>.", True),
    Var("UPS_SNMP_COMMUNITY", "UPS (SNMP)", True,
        "Clave de lectura SNMP («community») de la tarjeta de red de la UPS; por defecto «public». La usa medir_ups.py."),
    Var("UPS_INGESTA_TOKEN", "UPS (SNMP)", True,
        "Clave con la que el agente de la PC del tambo (ups_agente.py) entrega las lecturas de la UPS a LactIA. "
        "Mínimo 20 caracteres; tiene que ser la misma en las dos puntas."),
    Var("TELEGRAM_BOT_TOKEN", "Telegram", True, "Token del bot de Telegram."),
    Var("TELEGRAM_CHAT_ID", "Telegram", False, "Id del chat que recibe los avisos."),
    Var("SMTP_HOST", "Correo", False, "Servidor SMTP (ej. smtp.gmail.com)."),
    Var("SMTP_PORT", "Correo", False, "Puerto SMTP (ej. 587)."),
    Var("SMTP_USUARIO", "Correo", False, "Cuenta desde la que se envía."),
    Var("SMTP_PASSWORD", "Correo", True, "Contraseña de aplicación de esa cuenta."),
    Var("SMTP_DESTINO", "Correo", False, "Dirección que recibe los avisos."),
    Var("TWILIO_ACCOUNT_SID", "WhatsApp (Twilio)", False, "Account SID de Twilio."),
    Var("TWILIO_AUTH_TOKEN", "WhatsApp (Twilio)", True, "Auth token de Twilio."),
    Var("TWILIO_WHATSAPP_FROM", "WhatsApp (Twilio)", False, "Número de origen, ej. whatsapp:+14155238886."),
    Var("WHATSAPP_TELEFONO", "WhatsApp (Twilio)", False, "Teléfono que recibe los avisos, ej. +549341XXXXXXX."),
    Var("TUNNEL_TOKEN", "Cloudflare", True,
        "Solo si el túnel se corre con token. Si usa config.yml, dejarlo vacío. "
        "cloudflared la lee solo del entorno."),
)


def _norm(nombre: str) -> str:
    return nombre.upper()          # los nombres de variable en Windows no distinguen mayúsculas


def en_catalogo(nombre: str) -> Var | None:
    """La entrada del catálogo a la que pertenece `nombre` (exacta, o por prefijo)."""
    n = _norm(nombre)
    for v in CATALOGO:
        if not v.prefijo and _norm(v.nombre) == n:
            return v
    for v in CATALOGO:
        if v.prefijo and n.startswith(_norm(v.nombre)) and len(n) > len(v.nombre):
            return v
    return None


RE_NOMBRE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _expandir(valor: str, env: dict[str, str]) -> str:
    """Resuelve los %VAR% de un valor REG_EXPAND_SZ contra el entorno que se le va a PASAR al
    proceso hijo (no contra el del panel, que puede ser otro). Lo que no existe queda tal cual,
    como hace Windows."""
    return re.sub(r"%([^%]+)%", lambda m: env.get(_norm(m.group(1)), m.group(0)), valor)


def validar_nombre(nombre: str) -> None:
    if not RE_NOMBRE.match(nombre or ""):
        raise ErrorPanel("El nombre solo puede tener letras, números y guión bajo, y no empezar con número.")


def validar_valor(valor: str) -> None:
    if "\x00" in valor:
        raise ErrorPanel("El valor tiene un carácter nulo.")
    if "\n" in valor or "\r" in valor:
        raise ErrorPanel("El valor tiene un salto de línea (¿se pegó con un Enter de más?). "
                         "No lo corrijo solo para no cambiarte una contraseña sin avisar.")


# ---------------------------------------------------------------------------
# Variables de entorno: un "alcance" = una clave del registro
# ---------------------------------------------------------------------------
class Registro:
    """Un alcance de variables de entorno de Windows (Usuario o Equipo)."""

    def __init__(self, raiz, subclave: str, etiqueta: str):
        self.raiz, self.subclave, self.etiqueta = raiz, subclave, etiqueta

    def leer(self) -> dict[str, tuple[str, int]]:
        """{nombre: (valor, tipo)}. Solo textos; el resto de los tipos no son variables de entorno."""
        res: dict[str, tuple[str, int]] = {}
        try:
            with winreg.OpenKey(self.raiz, self.subclave, 0, winreg.KEY_READ) as k:
                i = 0
                while True:
                    try:
                        nombre, valor, tipo = winreg.EnumValue(k, i)
                    except OSError:
                        break
                    i += 1
                    if tipo in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) and isinstance(valor, str):
                        res[nombre] = (valor, tipo)
        except FileNotFoundError:
            pass
        return res

    def _buscar(self, nombre: str):
        for n, (v, t) in self.leer().items():
            if _norm(n) == _norm(nombre):
                return n, v, t
        return None

    def valor(self, nombre: str) -> str | None:
        previo = self._buscar(nombre)
        return previo[1] if previo else None

    def escribir(self, nombre: str, valor: str) -> None:
        validar_nombre(nombre)
        validar_valor(valor)
        previo = self._buscar(nombre)
        # REG_SZ salvo que ya fuera REG_EXPAND_SZ y el valor siga usando %...%: con REG_EXPAND_SZ
        # un '%' suelto dentro de una contraseña se "expandiría" y la cambiaría sin avisar.
        tipo = winreg.REG_SZ
        if previo and previo[2] == winreg.REG_EXPAND_SZ and "%" in valor:
            tipo = winreg.REG_EXPAND_SZ
        nombre_real = previo[0] if previo else nombre
        try:
            with winreg.CreateKeyEx(self.raiz, self.subclave, 0, winreg.KEY_SET_VALUE | winreg.KEY_READ) as k:
                winreg.SetValueEx(k, nombre_real, 0, tipo, valor)
        except PermissionError as e:
            raise ErrorPanel(f"Sin permiso para escribir en «{self.etiqueta}». "
                             "Abrí el panel como Administrador.") from e
        if self.valor(nombre) != valor:           # leerlo de vuelta: no asumir que se guardó
            raise ErrorPanel(f"Se escribió «{nombre}» pero al leerlo de vuelta no coincide.")

    def borrar(self, nombre: str) -> bool:
        previo = self._buscar(nombre)
        if not previo:
            return False
        try:
            with winreg.OpenKey(self.raiz, self.subclave, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, previo[0])
        except PermissionError as e:
            raise ErrorPanel(f"Sin permiso para borrar de «{self.etiqueta}». "
                             "Abrí el panel como Administrador.") from e
        return True


def difundir_cambio() -> None:
    """Avisa a Windows que cambió el entorno, para que los procesos NUEVOS lo vean sin cerrar sesión."""
    try:
        resultado = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000,
                                                 ctypes.byref(resultado))
    except Exception:  # noqa: BLE001 -- es cortesía: si falla, igual quedó guardado
        pass


class Entorno:
    """Las variables de los dos alcances de Windows. Si una está en los dos, GANA la de Usuario."""

    def __init__(self, usuario: Registro | None = None, equipo: Registro | None = None):
        self.usuario = usuario or Registro(winreg.HKEY_CURRENT_USER, "Environment", "Usuario")
        self.equipo = equipo or Registro(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment", "Equipo")

    def _registro(self, alcance: str) -> Registro:
        if alcance not in ("usuario", "equipo"):
            raise ErrorPanel(f"Alcance desconocido: {alcance}")
        return self.usuario if alcance == "usuario" else self.equipo

    def lectura(self, extras=()) -> list[dict]:
        """Una fila por variable GESTIONADA: las del catálogo (estén o no definidas), las que
        calzan con un prefijo del catálogo y las que el tambo agregó a mano."""
        u, e = self.usuario.leer(), self.equipo.leer()
        por_nombre: dict[str, dict] = {}

        def fila(nombre: str) -> dict:
            return por_nombre.setdefault(_norm(nombre), {
                "nombre": nombre, "usuario": None, "equipo": None, "var": en_catalogo(nombre)})

        for v in CATALOGO:
            if not v.prefijo:
                fila(v.nombre)
        for nombre in list(extras):
            fila(nombre)
        for alcance, datos in (("usuario", u), ("equipo", e)):
            for nombre, (valor, _tipo) in datos.items():
                if en_catalogo(nombre) or _norm(nombre) in por_nombre:
                    fila(nombre)[alcance] = valor
        for f in por_nombre.values():
            f["efectivo"] = f["usuario"] if f["usuario"] is not None else f["equipo"]
            f["en_conflicto"] = (f["usuario"] is not None and f["equipo"] is not None
                                 and f["usuario"] != f["equipo"])
        # En el orden del catálogo (cada grupo junto, usuario antes que contraseña), no alfabético:
        # el alfabeto mezcla los grupos y manda "Cámaras" al final por la tilde.
        orden = {id(v): i for i, v in enumerate(CATALOGO)}
        return sorted(por_nombre.values(),
                      key=lambda f: (orden.get(id(f["var"]), 999), _norm(f["nombre"])))

    def guardar(self, nombre: str, valor: str, alcance: str, quitar_del_otro: bool = False) -> None:
        self._registro(alcance).escribir(nombre, valor)
        if quitar_del_otro:
            self._registro("equipo" if alcance == "usuario" else "usuario").borrar(nombre)
        difundir_cambio()

    def borrar(self, nombre: str, alcance: str) -> bool:
        ok = self._registro(alcance).borrar(nombre)
        if ok:
            difundir_cambio()
        return ok

    def para_hijo(self, extras=(), base: dict | None = None) -> dict[str, str]:
        """Entorno para un proceso que se va a lanzar: el del panel, pisado con las variables
        GESTIONADAS leídas del registro AHORA. Se pisan solo esas -- no PATH, TEMP, etc. -- porque
        el PATH de Usuario se SUMA al del Equipo en Windows, y copiarlo tal cual reemplazaría uno
        por el otro y rompería la búsqueda de `git` y `python`."""
        env = {_norm(k): v for k, v in (os.environ if base is None else base).items()}
        gestionadas = {_norm(x) for x in extras}
        for datos in (self.equipo.leer(), self.usuario.leer()):       # Usuario va último: gana
            for nombre, (valor, tipo) in datos.items():
                if en_catalogo(nombre) or _norm(nombre) in gestionadas:
                    env[_norm(nombre)] = _expandir(valor, env) if tipo == winreg.REG_EXPAND_SZ else valor
        return env


# ---------------------------------------------------------------------------
# Procesos y despliegue (sin pantalla: se prueba sin tkinter)
# ---------------------------------------------------------------------------
def es_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def _ps(script: str, timeout: int = 40) -> str:
    """Corre PowerShell y devuelve su salida como texto UTF-8 (la consola de Windows trae
    cp1252 y rompe las tildes y los emojis)."""
    r = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command",
         "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + script],
        capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)
    return r.stdout.decode("utf-8", "replace").strip()


PS_PROCESOS = r"""
$p = Get-CimInstance Win32_Process |
  Where-Object { $_.Name -in 'python.exe','pythonw.exe','cloudflared.exe' } |
  ForEach-Object { [pscustomobject]@{
      pid = [int]$_.ProcessId; ppid = [int]$_.ParentProcessId; nombre = [string]$_.Name
      cmd = [string]$_.CommandLine
      inicio = $(if ($_.CreationDate) { $_.CreationDate.ToString('s') } else { '' }) } }
if ($null -eq $p) { '[]' } else { ConvertTo-Json -InputObject @($p) -Compress }
"""


def es_script(cmd: str, script: str) -> bool:
    """¿La línea de comandos corre `script`? Se exige que el nombre esté entero: «servidor.py»
    no debe calzar con «dummy_servidor.py»."""
    return re.search(r'(?:^|[\s"\\/])' + re.escape(script) + r'(?:["\s]|$)', cmd or "") is not None


def puerto_abierto(puerto: int, host: str = "127.0.0.1", espera: float = 0.6) -> bool:
    try:
        with socket.create_connection((host, puerto), timeout=espera):
            return True
    except OSError:
        return False


def http_codigo(url: str, timeout: float = 4) -> tuple[int | None, str]:
    """(código HTTP, detalle). Sin código = no se pudo ni conectar."""
    req = urllib.request.Request(url, headers={"User-Agent": "LactIA-panel"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, ""
    except urllib.error.HTTPError as e:
        return e.code, ""
    except (urllib.error.URLError, OSError) as e:
        return None, str(getattr(e, "reason", e))


def _python_por_defecto() -> str:
    exe = sys.executable
    if os.path.basename(exe).lower() == "pythonw.exe":     # pythonw no tiene consola: el log sería ciego
        alt = os.path.join(os.path.dirname(exe), "python.exe")
        if os.path.isfile(alt):
            return alt
    return exe


def _hash_archivo(ruta: str) -> str:
    try:
        with open(ruta, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return ""


def cola_de_log(ruta: str, lineas: int = 25) -> str:
    try:
        with open(ruta, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 6000))
            texto = f.read().decode("utf-8", "replace")
    except OSError:
        return ""
    return "\n".join(texto.splitlines()[-lineas:])


class Despliegue:
    """Todo lo que el panel hace sobre el servidor. `log` recibe cada línea; `confirmar` recibe
    una pregunta y devuelve True/False (la pantalla lo resuelve con un cuadro de diálogo)."""

    def __init__(self, ajustes: dict, entorno: Entorno, log: Callable[[str], None] = print,
                 confirmar: Callable[[str], bool] = lambda m: True, carpeta: str = CARPETA,
                 carpeta_logs: str | None = None):
        self.aj, self.entorno, self.log, self.confirmar = ajustes, entorno, log, confirmar
        self.carpeta = carpeta
        self.carpeta_logs = carpeta_logs or os.path.join(carpeta, "logs_panel")

    # -- piezas ------------------------------------------------------------
    def _python(self) -> str:
        return self.aj.get("python") or _python_por_defecto()

    def _ruta_log(self, nombre: str) -> str:
        return os.path.join(self.carpeta_logs, nombre)

    def _entorno_hijo(self) -> dict[str, str]:
        env = self.entorno.para_hijo(self.aj.get("variables_extra", []))
        env.setdefault("DELPRO_HOST", "0.0.0.0")           # igual que iniciar.bat
        env.setdefault("DELPRO_PORT", str(self.aj["puerto"]))
        # Con la salida a un archivo Python usa cp1252: un emoji en un print tumbaría el servidor.
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        return env

    def listar_procesos(self) -> list[dict]:
        try:
            datos = json.loads(_ps(PS_PROCESOS) or "[]")
        except (ValueError, subprocess.TimeoutExpired) as e:
            raise ErrorPanel(f"No pude listar los procesos: {e}") from e
        yo = os.getpid()
        return [p for p in datos if p.get("pid") != yo]

    def _del_script(self, script: str, procesos=None) -> list[dict]:
        procesos = self.listar_procesos() if procesos is None else procesos
        return [p for p in procesos if p["nombre"].lower() != "cloudflared.exe" and es_script(p.get("cmd"), script)]

    def _de_cloudflared(self, procesos=None) -> list[dict]:
        procesos = self.listar_procesos() if procesos is None else procesos
        return [p for p in procesos if p["nombre"].lower() == "cloudflared.exe"]

    def _matar(self, procesos: list[dict], que: str) -> bool:
        """Cierra los procesos y VERIFICA que no queden. Devuelve True si quedó todo cerrado."""
        for p in procesos:
            self.log(f"  cerrando {que}: PID {p['pid']} ({p['nombre']})")
            subprocess.run(["taskkill", "/PID", str(p["pid"]), "/T", "/F"],
                           capture_output=True, creationflags=CREATE_NO_WINDOW)
        pids = {p["pid"] for p in procesos}
        for _ in range(12):
            time.sleep(0.5)
            if not [p for p in self.listar_procesos() if p["pid"] in pids]:
                return True
        self.log(f"ERROR: siguen vivos: {sorted(pids)}. ¿El panel está abierto como Administrador?")
        return False

    # -- estado ------------------------------------------------------------
    def estado(self) -> dict:
        procesos = self.listar_procesos()
        lac = self._del_script(self.aj["script_lactia"], procesos)
        iot = self._del_script(self.aj["script_iot"], procesos)
        cf = self._de_cloudflared(procesos)
        codigo, _ = http_codigo(f"http://127.0.0.1:{self.aj['puerto']}/login", 3) if lac else (None, "")
        return {
            "lactia": {"procesos": lac, "puerto": puerto_abierto(self.aj["puerto"]), "http": codigo},
            "iot": {"procesos": iot},
            "cloudflared": {"procesos": cf, "servicio": self._servicio_estado()},
            "admin": es_admin(),
        }

    # -- LactIA e IoT (mismo mecanismo: un script de Python) ----------------
    def _iniciar_script(self, que: str, script: str, archivo_log: str, esperar_puerto: int | None) -> bool:
        ya = self._del_script(script)
        if ya:
            self.log(f"{que} ya está corriendo (PID {', '.join(str(p['pid']) for p in ya)}). No inicio otro.")
            return True
        ruta = os.path.join(self.carpeta, script)
        if not os.path.isfile(ruta):
            raise ErrorPanel(f"No existe {ruta}.")
        os.makedirs(self.carpeta_logs, exist_ok=True)
        ruta_log = self._ruta_log(archivo_log)
        py = self._python()
        self.log(f"Iniciando {que}: {py} {script}  (log: {ruta_log})")
        with open(ruta_log, "ab") as f:
            f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} iniciado por el panel =====\n".encode())
            f.flush()
            proc = subprocess.Popen([py, script], cwd=self.carpeta, env=self._entorno_hijo(),
                                    stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT,
                                    creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)
        limite = time.time() + (90 if esperar_puerto else 5)
        while time.time() < limite:
            time.sleep(0.5)
            if proc.poll() is not None:
                self.log(f"ERROR: {que} se cerró enseguida (código {proc.returncode}). Últimas líneas del log:")
                self.log(cola_de_log(ruta_log) or "  (el log está vacío)")
                return False
            if esperar_puerto and puerto_abierto(esperar_puerto):
                codigo, _ = http_codigo(f"http://127.0.0.1:{esperar_puerto}/login", 5)
                self.log(f"OK: {que} escuchando en el puerto {esperar_puerto} (HTTP {codigo}). PID {proc.pid}.")
                return True
        if esperar_puerto:
            self.log(f"ERROR: {que} no abrió el puerto {esperar_puerto} en 90 s. Últimas líneas del log:")
            self.log(cola_de_log(ruta_log) or "  (el log está vacío)")
            return False
        self.log(f"OK: {que} sigue corriendo (PID {proc.pid}).")
        return True

    def _parar_script(self, que: str, script: str) -> bool:
        procs = self._del_script(script)
        if not procs:
            self.log(f"{que} ya estaba parado.")
            return True
        ok = self._matar(procs, que)
        if ok:
            self.log(f"OK: {que} parado.")
        return ok

    def iniciar_lactia(self) -> bool:
        return self._iniciar_script("LactIA", self.aj["script_lactia"], "lactia.log", self.aj["puerto"])

    def parar_lactia(self) -> bool:
        ok = self._parar_script("LactIA", self.aj["script_lactia"])
        if ok and puerto_abierto(self.aj["puerto"]):
            self.log(f"AVISO: el puerto {self.aj['puerto']} sigue ocupado por OTRO programa (no es LactIA).")
        return ok

    def reiniciar_lactia(self) -> bool:
        return self.parar_lactia() and self.iniciar_lactia()

    def iniciar_iot(self) -> bool:
        return self._iniciar_script("Poller IoT", self.aj["script_iot"], "iot.log", None)

    def parar_iot(self) -> bool:
        return self._parar_script("Poller IoT", self.aj["script_iot"])

    def reiniciar_iot(self) -> bool:
        return self.parar_iot() and self.iniciar_iot()

    # -- actualizar ---------------------------------------------------------
    def _correr(self, cmd: list[str], timeout: int) -> int:
        self.log("$ " + " ".join(cmd))
        env = dict(os.environ)
        env["GIT_TERMINAL_PROMPT"] = "0"       # que falle en vez de quedarse esperando un usuario
        p = subprocess.Popen(cmd, cwd=self.carpeta, env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=CREATE_NO_WINDOW)
        vencio = threading.Event()

        def cortar():
            vencio.set()
            p.kill()
        timer = threading.Timer(timeout, cortar)
        timer.start()
        try:
            for linea in iter(p.stdout.readline, b""):
                self.log("  " + linea.decode("utf-8", "replace").rstrip())
            p.wait()
        finally:
            timer.cancel()
            p.stdout.close()
        if vencio.is_set():
            self.log(f"ERROR: se cortó por pasar de {timeout} s.")
            return -1
        return p.returncode

    def actualizar(self) -> bool:
        """Lo mismo que actualizar.bat -- fetch, reset --hard, pip -- y reinicia lo que estaba corriendo."""
        git = shutil.which("git")
        if not git:
            raise ErrorPanel("No encuentro git en el PATH.")
        rama = self.aj["rama"]
        propio_antes = _hash_archivo(os.path.abspath(__file__))

        sucios = subprocess.run([git, "status", "--short", "--untracked-files=no"], cwd=self.carpeta,
                                capture_output=True, creationflags=CREATE_NO_WINDOW
                                ).stdout.decode("utf-8", "replace").strip()
        if sucios:
            self.log("Hay cambios locales en archivos del repo; el reset los descarta:")
            self.log(sucios)
            if not self.confirmar("Hay cambios locales sin commitear en el servidor (están en el log).\n\n"
                                  "Actualizar los DESCARTA. ¿Seguir?"):
                self.log("Cancelado: no se tocó nada.")
                return False

        if self._correr([git, "fetch", "origin"], 180) != 0:
            self.log("ERROR: git fetch falló (¿sin internet o pide iniciar sesión en GitHub?). No se tocó nada.")
            return False
        self._correr([git, "log", "--oneline", f"HEAD..origin/{rama}"], 30)
        if self._correr([git, "reset", "--hard", f"origin/{rama}"], 60) != 0:
            self.log("ERROR: git reset falló.")
            return False
        if self._correr([self._python(), "-m", "pip", "install", "-q", "-r", "requirements.txt"], 900) != 0:
            self.log("AVISO: pip falló. Si el código nuevo usa una librería nueva, LactIA puede no arrancar.")

        habia_iot = bool(self._del_script(self.aj["script_iot"]))
        ok = self.reiniciar_lactia()
        if habia_iot:                      # lo que no estaba corriendo no se arranca solo
            ok = self.reiniciar_iot() and ok
        if _hash_archivo(os.path.abspath(__file__)) != propio_antes:
            self.log("AVISO: el propio panel cambió con esta actualización. Cerralo y volvelo a abrir.")
        self.log("Actualización terminada." if ok else "Actualización terminada CON ERRORES (ver arriba).")
        return ok

    # -- Cloudflare ---------------------------------------------------------
    def _servicio_estado(self) -> str:
        nombre = self.aj["cloudflared_servicio"]
        if not re.match(r"^[\w .\-]+$", nombre or ""):
            return ""
        try:
            return _ps(f"(Get-Service -Name '{nombre}' -ErrorAction SilentlyContinue).Status", 15)
        except subprocess.TimeoutExpired:
            return ""

    def modo_cloudflared(self) -> str:
        modo = self.aj["cloudflared_modo"]
        if modo in ("proceso", "servicio"):
            return modo
        # auto: manda lo que está pasando, no lo que existe. En SERVER-DELPRO el servicio de
        # Windows EXISTE pero está detenido (se reiniciaba en bucle) y el túnel corre como un
        # proceso suelto: decidir por "existe" hacía que Parar detuviera un servicio ya parado,
        # dijera OK y dejara el túnel andando.
        return "servicio" if self._servicio_estado().lower() == "running" else "proceso"

    def _exe_cloudflared(self) -> str:
        candidatos = [self.aj.get("cloudflared_exe"), shutil.which("cloudflared"),
                      r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
                      r"C:\Program Files\cloudflared\cloudflared.exe", r"C:\cloudflared\cloudflared.exe",
                      os.path.expandvars(r"%USERPROFILE%\cloudflared.exe")]
        for c in candidatos:
            if c and os.path.isfile(c):
                return c
        raise ErrorPanel("No encuentro cloudflared.exe. Indicá la ruta en Ajustes (o, si lo corrés como "
                         "servicio de Windows, poné el modo «servicio»).")

    def _esperar_servicio(self, estado: str) -> bool:
        for _ in range(40):
            if self._servicio_estado().lower() == estado.lower():
                return True
            time.sleep(0.5)
        return False

    def iniciar_cloudflared(self) -> bool:
        modo = self.modo_cloudflared()
        if self._de_cloudflared():
            self.log("Cloudflare ya está corriendo. No inicio otro conector.")
            return True
        if modo == "servicio":
            nombre = self.aj["cloudflared_servicio"]
            self.log(f"Iniciando el servicio de Windows «{nombre}»")
            _ps(f"Start-Service -Name '{nombre}'", 60)
            ok = self._esperar_servicio("Running")
            self.log("OK: servicio en marcha." if ok else
                     "ERROR: el servicio no quedó en marcha. Si ya falló antes, probá el modo «proceso» en Ajustes.")
            return ok
        if self._servicio_estado().lower() == "running":
            self.log("AVISO: el servicio cloudflared está en marcha: iniciar otro proceso deja DOS conectores.")
        exe = self._exe_cloudflared()
        os.makedirs(self.carpeta_logs, exist_ok=True)
        ruta_log = self._ruta_log("cloudflared.log")
        cmd = [exe] + shlex.split(self.aj["cloudflared_args"], posix=False)
        self.log("Iniciando Cloudflare: " + " ".join(cmd))
        with open(ruta_log, "ab") as f:
            f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} iniciado por el panel =====\n".encode())
            f.flush()
            proc = subprocess.Popen(cmd, cwd=os.path.dirname(exe), env=self._entorno_hijo(),
                                    stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT,
                                    creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP)
        time.sleep(4)
        if proc.poll() is not None:
            self.log(f"ERROR: cloudflared se cerró enseguida (código {proc.returncode}). Últimas líneas del log:")
            self.log(cola_de_log(ruta_log) or "  (el log está vacío)")
            return False
        self.log(f"OK: cloudflared corriendo (PID {proc.pid}). Probando el acceso público...")
        return self.verificar_publico(espera=40)

    def parar_cloudflared(self) -> bool:
        """Para el túnel sea como corra: si el servicio de Windows está en marcha lo detiene (matarlo
        como proceso haría que Windows lo vuelva a levantar), y después cierra cualquier proceso que
        quede, que es el caso del túnel corrido a mano. Termina verificando que no quede ninguno."""
        servicio_andaba = self._servicio_estado().lower() == "running"
        if servicio_andaba:
            nombre = self.aj["cloudflared_servicio"]
            self.log(f"Parando el servicio de Windows «{nombre}»")
            _ps(f"Stop-Service -Name '{nombre}' -Force", 60)
            if not self._esperar_servicio("Stopped"):
                self.log("ERROR: el servicio no se detuvo (¿Administrador?).")
                return False
        procs = self._de_cloudflared()
        if procs and not self._matar(procs, "Cloudflare"):
            return False
        if self._de_cloudflared():
            self.log("ERROR: cloudflared sigue corriendo.")
            return False
        self.log("OK: Cloudflare parado." if (servicio_andaba or procs) else "Cloudflare ya estaba parado.")
        return True

    def reiniciar_cloudflared(self) -> bool:
        return self.parar_cloudflared() and self.iniciar_cloudflared()

    def url_publica(self) -> str:
        url = self.aj.get("url_publica") or self.entorno.usuario.valor("LACTIA_URL_PUBLICA") \
            or self.entorno.equipo.valor("LACTIA_URL_PUBLICA") or "https://www.analiticastambo.com"
        return url.rstrip("/")

    def verificar_publico(self, espera: int = 0) -> bool:
        """Pide /login por la dirección pública. Con `espera`, reintenta ese tiempo (el túnel tarda en subir)."""
        url = self.url_publica() + "/login"
        limite = time.time() + espera
        while True:
            codigo, detalle = http_codigo(url, 10)
            if codigo in (200, 302):
                self.log(f"OK: {url} responde {codigo}. El acceso público anda.")
                return True
            if time.time() >= limite:
                break
            time.sleep(3)
        if codigo is None:
            self.log(f"ERROR: no se pudo conectar a {url}: {detalle}")
        elif codigo in (502, 503, 504):
            self.log(f"AVISO: Cloudflare responde {codigo}: el túnel está, pero LactIA no contesta (¿está iniciada?).")
        elif codigo in (530, 1033, 1016):
            self.log(f"ERROR: Cloudflare responde {codigo}: el túnel está caído.")
        else:
            self.log(f"AVISO: {url} respondió {codigo}.")
        return False


# ---------------------------------------------------------------------------
# Pantalla
# ---------------------------------------------------------------------------
def reabrir_como_admin() -> bool:
    params = " ".join(f'"{a}"' for a in [os.path.abspath(__file__)] + sys.argv[1:])
    try:
        return ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, CARPETA, 1) > 32
    except Exception:  # noqa: BLE001
        return False


class Panel:
    def __init__(self, ajustes: dict | None = None, entorno: Entorno | None = None, visible: bool = True):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.aj = ajustes if ajustes is not None else cargar_ajustes()
        self.entorno = entorno or Entorno()
        self.cola: queue.Queue = queue.Queue()
        self.ocupado = False
        self.cambio_pendiente = False
        self.mostrar_secretos = False
        self.d = Despliegue(self.aj, self.entorno, log=self._log, confirmar=self._confirmar_desde_hilo)

        self.raiz = tk.Tk()
        self.raiz.title("LactIA — Panel de despliegue")
        self.raiz.geometry("980x700")
        self.raiz.minsize(820, 560)
        if not visible:
            self.raiz.withdraw()
        self._id_bombear = self._id_sondeo = None
        self.raiz.protocol("WM_DELETE_WINDOW", self.cerrar)
        self._armar()
        self._refrescar_variables()
        self._id_bombear = self.raiz.after(100, self._bombear)
        self._id_sondeo = self.raiz.after(300, self._sondear)

    def cerrar(self) -> None:
        """Cancela lo que quedó programado antes de destruir la ventana (si no, Tk intenta correr
        esos callbacks sobre una ventana que ya no existe)."""
        for ident in (self._id_bombear, self._id_sondeo):
            if ident:
                try:
                    self.raiz.after_cancel(ident)
                except Exception:  # noqa: BLE001
                    pass
        self.raiz.destroy()

    # -- hilos ----------------------------------------------------------------
    def _log(self, texto: str) -> None:
        self.cola.put(("log", texto))

    def _confirmar_desde_hilo(self, mensaje: str) -> bool:
        resultado, listo = [False], threading.Event()
        self.cola.put(("confirmar", mensaje, resultado, listo))
        listo.wait()
        return resultado[0]

    def _bombear(self) -> None:
        from tkinter import messagebox
        try:
            while True:
                item = self.cola.get_nowait()
                if item[0] == "log":
                    self.txt.configure(state="normal")
                    self.txt.insert("end", item[1] + "\n")
                    self.txt.see("end")
                    self.txt.configure(state="disabled")
                elif item[0] == "confirmar":
                    item[2][0] = messagebox.askyesno("Confirmar", item[1], parent=self.raiz)
                    item[3].set()
                elif item[0] == "estado":
                    self._pintar_estado(item[1])
                elif item[0] == "fin":
                    self.ocupado = False
                    self._botones(True)
                    self._sondear(forzar=True)
        except queue.Empty:
            pass
        self._id_bombear = self.raiz.after(100, self._bombear)

    def _botones(self, habilitar: bool) -> None:
        for b in self.botones_accion:
            b.configure(state="normal" if habilitar else "disabled")

    def correr(self, titulo: str, fn: Callable[[], object]) -> None:
        if self.ocupado:
            return
        self.ocupado = True
        self._botones(False)
        self._log(f"\n=== {titulo} ({time.strftime('%H:%M:%S')}) ===")

        def trabajo():
            try:
                fn()
            except ErrorPanel as e:
                self._log(f"ERROR: {e}")
            except Exception:  # noqa: BLE001
                self._log("ERROR inesperado:\n" + traceback.format_exc())
            finally:
                self.cola.put(("fin",))
        threading.Thread(target=trabajo, daemon=True).start()

    def _sondear(self, forzar: bool = False) -> None:
        def trabajo():
            try:
                self.cola.put(("estado", self.d.estado()))
            except Exception as e:  # noqa: BLE001
                self.cola.put(("estado", {"error": str(e)}))
        if not self.ocupado or forzar:
            threading.Thread(target=trabajo, daemon=True).start()
        if not forzar:
            self._id_sondeo = self.raiz.after(10000, self._sondear)

    # -- armado -------------------------------------------------------------------
    def _armar(self) -> None:
        tk, ttk = self.tk, self.ttk
        self.aviso_admin = tk.Label(self.raiz, text="", fg="white", bg="#b03a2e", anchor="w", justify="left",
                                    padx=10, pady=4, wraplength=940)
        self.aviso_admin.pack(fill="x")
        self.cuaderno = ttk.Notebook(self.raiz)
        self.cuaderno.pack(fill="both", expand=True, padx=8, pady=8)
        self._armar_variables(ttk.Frame(self.cuaderno))
        self._armar_servidor(ttk.Frame(self.cuaderno))

    def _armar_variables(self, marco) -> None:
        tk, ttk = self.tk, self.ttk
        self.cuaderno.add(marco, text="  Variables de entorno  ")
        barra = ttk.Frame(marco)
        barra.pack(fill="x", padx=8, pady=(8, 4))
        ttk.Button(barra, text="Editar…", command=self._editar_sel).pack(side="left")
        ttk.Button(barra, text="Agregar…", command=lambda: self._dialogo_variable(None)).pack(side="left", padx=6)
        ttk.Button(barra, text="Borrar…", command=self._borrar_sel).pack(side="left")
        ttk.Button(barra, text="Refrescar", command=self._refrescar_variables).pack(side="left", padx=6)
        self.var_mostrar = tk.BooleanVar(value=False)
        ttk.Checkbutton(barra, text="Mostrar secretos", variable=self.var_mostrar,
                        command=self._alternar_secretos).pack(side="right")

        cols = ("variable", "valor", "alcance", "grupo")
        self.tabla = ttk.Treeview(marco, columns=cols, show="headings", selectmode="browse")
        for c, t, a in (("variable", "Variable", 230), ("valor", "Valor", 290),
                        ("alcance", "Dónde está", 135), ("grupo", "Grupo", 190)):
            self.tabla.heading(c, text=t)
            self.tabla.column(c, width=a, anchor="w")
        self.tabla.tag_configure("conflicto", background="#fdebd0")
        self.tabla.tag_configure("vacia", foreground="#8a8a8a")
        self.tabla.pack(fill="both", expand=True, padx=8, pady=4)
        self.tabla.bind("<Double-1>", lambda e: self._editar_sel())
        self.lbl_ayuda = ttk.Label(marco, text="Doble clic para editar. Naranja = la variable está en Usuario y en "
                                   "Equipo con valores distintos (gana la de Usuario).", wraplength=900)
        self.lbl_ayuda.pack(fill="x", padx=8)
        self.marco_reinicio = tk.Frame(marco, bg="#fcf3cf")
        tk.Label(self.marco_reinicio, bg="#fcf3cf", anchor="w", padx=8,
                 text="Cambiaste variables. LactIA, si ya estaba corriendo, sigue con las anteriores hasta reiniciarla."
                 ).pack(side="left", pady=6)
        ttk.Button(self.marco_reinicio, text="Reiniciar LactIA ahora",
                   command=lambda: self.correr("Reiniciar LactIA", self.d.reiniciar_lactia)).pack(side="right", padx=8)

    def _armar_servidor(self, marco) -> None:
        tk, ttk = self.tk, self.ttk
        self.cuaderno.add(marco, text="  Servidor  ")
        self.botones_accion = []
        self.est = {}
        for clave, titulo, ini, par, rei in (
                ("lactia", "LactIA (servidor web)", self.d.iniciar_lactia, self.d.parar_lactia, self.d.reiniciar_lactia),
                ("iot", "Poller del gateway IoT", self.d.iniciar_iot, self.d.parar_iot, self.d.reiniciar_iot),
                ("cloudflared", "Cloudflare (acceso desde internet)", self.d.iniciar_cloudflared,
                 self.d.parar_cloudflared, self.d.reiniciar_cloudflared)):
            caja = ttk.LabelFrame(marco, text=titulo)
            caja.pack(fill="x", padx=8, pady=5)
            punto = tk.Label(caja, text="●", font=("Segoe UI", 16), fg="#999999")
            punto.pack(side="left", padx=(10, 6), pady=6)
            texto = ttk.Label(caja, text="consultando…")
            texto.pack(side="left", fill="x", expand=True)
            for etiqueta, fn in (("Reiniciar", rei), ("Parar", par), ("Iniciar", ini)):
                b = ttk.Button(caja, text=etiqueta,
                               command=lambda f=fn, t=f"{etiqueta} {titulo}": self.correr(t, f))
                b.pack(side="right", padx=4, pady=6)
                self.botones_accion.append(b)
            self.est[clave] = (punto, texto)
        pie = ttk.Frame(marco)
        pie.pack(fill="x", padx=8, pady=6)
        b = ttk.Button(pie, text="Actualizar LactIA (git + reiniciar)",
                       command=lambda: self.correr("Actualizar LactIA", self.d.actualizar))
        b.pack(side="left")
        self.botones_accion.append(b)
        b2 = ttk.Button(pie, text="Verificar acceso público",
                        command=lambda: self.correr("Verificar acceso público", self.d.verificar_publico))
        b2.pack(side="left", padx=6)
        self.botones_accion.append(b2)
        ttk.Button(pie, text="Ajustes…", command=self._dialogo_ajustes).pack(side="right")
        ttk.Button(pie, text="Limpiar log", command=self._limpiar_log).pack(side="right", padx=6)
        from tkinter import scrolledtext
        self.txt = scrolledtext.ScrolledText(marco, height=14, state="disabled", font=("Consolas", 9),
                                             background="#111820", foreground="#d6e2f0", insertbackground="#d6e2f0")
        self.txt.pack(fill="both", expand=True, padx=8, pady=(0, 8))

    def _limpiar_log(self) -> None:
        self.txt.configure(state="normal")
        self.txt.delete("1.0", "end")
        self.txt.configure(state="disabled")

    # -- estado ----------------------------------------------------------------------
    def _pintar_estado(self, e: dict) -> None:
        if "error" in e:
            self.aviso_admin.configure(text=f"No pude consultar el estado: {e['error']}")
            self.aviso_admin.pack(fill="x", before=self.cuaderno)
            return
        if e.get("admin"):
            self.aviso_admin.pack_forget()
        else:
            self.aviso_admin.configure(
                text="No estás como Administrador: no se pueden guardar variables de Equipo ni cerrar procesos "
                     "de otro usuario. Cerrá el panel y abrilo con «Ejecutar como administrador».")
            self.aviso_admin.pack(fill="x", before=self.cuaderno)

        def hora(p):
            return p.get("inicio", "")[11:16]
        verde, rojo, ambar = "#2e9e5b", "#c0392b", "#d68910"

        lac = e["lactia"]
        if lac["procesos"]:
            pids = ", ".join(str(p["pid"]) for p in lac["procesos"])
            ok = lac["puerto"] and lac["http"] in (200, 302)
            txt = f"Corriendo · PID {pids} · desde {hora(lac['procesos'][0])} · " + \
                  (f"responde HTTP {lac['http']}" if ok else "pero NO responde en el puerto (¿arrancando?)")
            self._poner("lactia", verde if ok else ambar, txt)
        elif lac["puerto"]:
            self._poner("lactia", ambar, f"Parado, pero el puerto {self.aj['puerto']} lo usa OTRO programa")
        else:
            self._poner("lactia", rojo, "Parado")

        iot = e["iot"]["procesos"]
        self._poner("iot", verde if iot else rojo,
                    f"Corriendo · PID {', '.join(str(p['pid']) for p in iot)} · desde {hora(iot[0])}" if iot else "Parado")

        cf = e["cloudflared"]
        servicio = cf["servicio"]
        if cf["procesos"]:
            pids = ", ".join(str(p["pid"]) for p in cf["procesos"])
            if servicio.lower() == "running":
                self._poner("cloudflared", verde, f"Corriendo como servicio de Windows · PID {pids}")
            else:
                nota = f" (el servicio de Windows existe pero está {servicio})" if servicio else ""
                self._poner("cloudflared", verde, f"Corriendo como proceso suelto · PID {pids}{nota}")
        else:
            self._poner("cloudflared", rojo, "Parado" + (f" · servicio de Windows: {servicio}" if servicio else ""))

    def _poner(self, clave: str, color: str, texto: str) -> None:
        punto, lbl = self.est[clave]
        punto.configure(fg=color)
        lbl.configure(text=texto)

    # -- variables ----------------------------------------------------------------------
    def _alternar_secretos(self) -> None:
        self.mostrar_secretos = self.var_mostrar.get()
        self._refrescar_variables()

    def _refrescar_variables(self) -> None:
        self.tabla.delete(*self.tabla.get_children())
        for f in self.entorno.lectura(self.aj.get("variables_extra", [])):
            v = f["var"]
            secreta = bool(v and v.secreta)
            valor = f["efectivo"]
            if valor is None:
                mostrado, donde, tags = "— sin definir —", "", ("vacia",)
            else:
                mostrado = "•" * 8 if (secreta and not self.mostrar_secretos) else valor
                donde = "Usuario + Equipo" if f["usuario"] is not None and f["equipo"] is not None else \
                        ("Usuario" if f["usuario"] is not None else "Equipo")
                tags = ("conflicto",) if f["en_conflicto"] else ()
            self.tabla.insert("", "end", iid=f["nombre"], values=(
                f["nombre"], mostrado, donde, v.grupo if v else "Agregadas a mano"), tags=tags)

    def _seleccion(self) -> str | None:
        sel = self.tabla.selection()
        return sel[0] if sel else None

    def _editar_sel(self) -> None:
        nombre = self._seleccion()
        if nombre:
            self._dialogo_variable(nombre)

    def _borrar_sel(self) -> None:
        from tkinter import messagebox
        nombre = self._seleccion()
        if not nombre:
            return
        fila = next((f for f in self.entorno.lectura(self.aj.get("variables_extra", []))
                     if _norm(f["nombre"]) == _norm(nombre)), None)
        if not fila or fila["efectivo"] is None:
            messagebox.showinfo("Borrar", "Esa variable no tiene valor guardado.", parent=self.raiz)
            return
        donde = [a for a in ("usuario", "equipo") if fila[a] is not None]
        if not messagebox.askyesno("Borrar", f"¿Borrar «{nombre}» de {' y '.join(d.capitalize() for d in donde)}?\n\n"
                                   "Se pierde el valor guardado.", parent=self.raiz):
            return
        try:
            for a in donde:
                self.entorno.borrar(nombre, a)
        except ErrorPanel as e:
            messagebox.showerror("No se pudo borrar", str(e), parent=self.raiz)
            return
        self._avisar_cambio(f"Variable {nombre} borrada.")

    def _avisar_cambio(self, texto: str) -> None:
        self._log(texto)
        self._refrescar_variables()
        if not self.cambio_pendiente:
            self.cambio_pendiente = True
            self.marco_reinicio.pack(fill="x", padx=8, pady=6)

    def _dialogo_variable(self, nombre: str | None) -> None:
        from tkinter import messagebox
        tk, ttk = self.tk, self.ttk
        extras = self.aj.get("variables_extra", [])
        fila = next((f for f in self.entorno.lectura(extras) if nombre and _norm(f["nombre"]) == _norm(nombre)), None)
        v = fila["var"] if fila else None
        win = tk.Toplevel(self.raiz)
        win.title("Variable de entorno")
        win.transient(self.raiz)
        win.grab_set()
        win.columnconfigure(1, weight=1)

        ttk.Label(win, text="Nombre").grid(row=0, column=0, sticky="w", padx=10, pady=(10, 2))
        if nombre:
            var_nombre = tk.StringVar(value=nombre)
            ttk.Label(win, text=nombre, font=("Segoe UI", 10, "bold")).grid(row=0, column=1, sticky="w", pady=(10, 2))
        else:
            var_nombre = tk.StringVar()
            combo = ttk.Combobox(win, textvariable=var_nombre, width=40,
                                 values=[x.nombre + ("<TAMBO>" if x.prefijo else "") for x in CATALOGO])
            combo.grid(row=0, column=1, sticky="we", padx=(0, 10), pady=(10, 2))
        if v:
            ttk.Label(win, text=v.ayuda, wraplength=460, foreground="#666666").grid(
                row=1, column=0, columnspan=2, sticky="w", padx=10, pady=2)

        ttk.Label(win, text="Valor").grid(row=2, column=0, sticky="w", padx=10, pady=6)
        secreta = bool(v and v.secreta) or not nombre
        var_valor = tk.StringVar(value=(fila["efectivo"] or "") if fila else "")
        entrada = ttk.Entry(win, textvariable=var_valor, width=52, show="•" if secreta else "")
        entrada.grid(row=2, column=1, sticky="we", padx=(0, 10), pady=6)
        var_ver = tk.BooleanVar(value=False)
        ttk.Checkbutton(win, text="Mostrar", variable=var_ver,
                        command=lambda: entrada.configure(show="" if var_ver.get() else ("•" if secreta else ""))
                        ).grid(row=3, column=1, sticky="w")

        ttk.Label(win, text="Guardar en").grid(row=4, column=0, sticky="w", padx=10, pady=6)
        por_defecto = "usuario" if (fila and fila["usuario"] is not None) else "equipo"
        var_alc = tk.StringVar(value=por_defecto)
        marco_a = ttk.Frame(win)
        marco_a.grid(row=4, column=1, sticky="w")
        ttk.Radiobutton(marco_a, text="Equipo (lo ven todos los usuarios y tareas)", variable=var_alc,
                        value="equipo").pack(anchor="w")
        ttk.Radiobutton(marco_a, text="Usuario (solo este usuario de Windows)", variable=var_alc,
                        value="usuario").pack(anchor="w")
        var_quitar = tk.BooleanVar(value=False)
        chk = ttk.Checkbutton(win, text="Quitar el valor del otro alcance (si no, el de Usuario pisa al de Equipo)",
                              variable=var_quitar)
        otro_con_valor = fila and ((fila["usuario"] is not None) != (fila["equipo"] is not None) or fila["en_conflicto"])
        if otro_con_valor:
            chk.grid(row=5, column=0, columnspan=2, sticky="w", padx=10, pady=4)
            var_quitar.set(True)

        def guardar():
            n = var_nombre.get().strip().replace("<TAMBO>", "")
            valor = var_valor.get()
            try:
                validar_nombre(n)
                validar_valor(valor)
                if valor != valor.strip() and not messagebox.askyesno(
                        "Espacios", "El valor empieza o termina con un espacio. ¿Es a propósito?", parent=win):
                    return
                if valor == "":
                    raise ErrorPanel("El valor está vacío. Para quitar la variable usá «Borrar…».")
                self.entorno.guardar(n, valor, var_alc.get(),
                                     quitar_del_otro=bool(otro_con_valor and var_quitar.get()))
                if not en_catalogo(n) and _norm(n) not in {_norm(x) for x in extras}:
                    self.aj.setdefault("variables_extra", []).append(n)
                    guardar_ajustes(self.aj)
            except ErrorPanel as e:
                messagebox.showerror("No se pudo guardar", str(e), parent=win)
                return
            win.destroy()
            self._avisar_cambio(f"Variable {n} guardada en {var_alc.get().capitalize()}.")

        botones = ttk.Frame(win)
        botones.grid(row=6, column=0, columnspan=2, sticky="e", padx=10, pady=10)
        ttk.Button(botones, text="Cancelar", command=win.destroy).pack(side="right", padx=4)
        ttk.Button(botones, text="Guardar", command=guardar).pack(side="right")
        entrada.focus_set()

    # -- ajustes ---------------------------------------------------------------------------
    def _dialogo_ajustes(self) -> None:
        from tkinter import messagebox
        tk, ttk = self.tk, self.ttk
        win = tk.Toplevel(self.raiz)
        win.title("Ajustes del panel")
        win.transient(self.raiz)
        win.grab_set()
        win.columnconfigure(1, weight=1)
        campos = (("python", "Python (vacío = el del panel)"), ("puerto", "Puerto de LactIA"),
                  ("rama", "Rama de git"), ("cloudflared_modo", "Cloudflare: modo (auto / proceso / servicio)"),
                  ("cloudflared_exe", "Cloudflare: ruta de cloudflared.exe"),
                  ("cloudflared_args", "Cloudflare: argumentos (sin secretos: el token va en TUNNEL_TOKEN)"),
                  ("cloudflared_servicio", "Cloudflare: nombre del servicio de Windows"),
                  ("url_publica", "Dirección pública (vacío = la de siempre)"))
        vars_ = {}
        for i, (clave, etiqueta) in enumerate(campos):
            ttk.Label(win, text=etiqueta).grid(row=i, column=0, sticky="w", padx=10, pady=4)
            vars_[clave] = tk.StringVar(value=str(self.aj[clave]))
            ttk.Entry(win, textvariable=vars_[clave], width=58).grid(row=i, column=1, sticky="we", padx=(0, 10))

        def guardar():
            try:
                puerto = int(vars_["puerto"].get())
                if not 1 <= puerto <= 65535:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Ajustes", "El puerto tiene que ser un número entre 1 y 65535.", parent=win)
                return
            if vars_["cloudflared_modo"].get() not in ("auto", "proceso", "servicio"):
                messagebox.showerror("Ajustes", "El modo de Cloudflare es auto, proceso o servicio.", parent=win)
                return
            for clave, var in vars_.items():
                self.aj[clave] = puerto if clave == "puerto" else var.get().strip()
            guardar_ajustes(self.aj)
            win.destroy()
            self._log("Ajustes guardados.")
        b = ttk.Frame(win)
        b.grid(row=len(campos), column=0, columnspan=2, sticky="e", padx=10, pady=10)
        ttk.Button(b, text="Cancelar", command=win.destroy).pack(side="right", padx=4)
        ttk.Button(b, text="Guardar", command=guardar).pack(side="right")

    def correr_ventana(self) -> None:
        self.raiz.mainloop()


def main() -> int:
    if winreg is None:
        print("Este panel es solo para Windows.")
        return 1
    if not es_admin() and "--sin-admin" not in sys.argv:
        if reabrir_como_admin():
            return 0                    # sigue en la ventana elevada
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)    # sin esto, con escala de pantalla el texto sale borroso
    except Exception:  # noqa: BLE001
        pass
    Panel().correr_ventana()
    return 0


if __name__ == "__main__":
    sys.exit(main())
