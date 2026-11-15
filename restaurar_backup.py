"""Restaura en SQL Server el backup más nuevo de cada tambo que haya en C:\\backupsdelpro.

    C:\\backupsdelpro\\laponderosa\\...   ->  base DDM
    C:\\backupsdelpro\\lamartina\\...     ->  base DDM_LAMARTINA
    C:\\backupsdelpro\\sanjose\\...       ->  base SanJose

Qué hace, por tambo:
  1. Busca el backup más nuevo y COMPLETO (un .zip/.7z, o las partes DelPro.N.bak sueltas). Uno recién tocado o cortado
     —Drive todavía lo está bajando— se salta y se usa el anterior.
  2. Lo extrae y une las partes en un solo .bak (en una carpeta de trabajo propia, que se limpia al terminar).
  3. Lo restaura sobre una base PARALELA (`<base>_NUEVA`), nunca encima de la actual.
  4. Le devuelve el acceso a `delpro_lectura` (se pierde en cada restore: ver CLAUDE.md) y comprueba que traiga animales.
  5. Recién ahí intercambia los nombres: la actual pasa a `<base>_ANT_<fecha>` (NO se borra) y la nueva queda como `<base>`.
  Si algo falla antes del intercambio, la base actual queda exactamente como estaba.

Se corre como Administrador (autenticación de Windows, sin contraseñas):

    python restaurar_backup.py --solo-ver            # muestra qué usaría, sin tocar nada
    python restaurar_backup.py                       # todos los tambos
    python restaurar_backup.py --tambo laponderosa
"""
import argparse
import datetime
import json
import ntpath
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

CARPETA_BACKUPS = r"C:\backupsdelpro"
INSTANCIA = r"localhost\DELPRO"
TAMBOS = {"laponderosa": "DDM", "lamartina": "DDM_LAMARTINA", "sanjose": "SanJose"}
ANTIGUEDAD_MIN_S = 120          # un archivo tocado hace menos de esto se considera «todavía bajando»
CARPETA_TRABAJO = "_trabajo"
REGISTRO = "restauraciones.json"
PROFUNDIDAD_MAX = 3
_NOMBRE_SQL = re.compile(r"^[A-Za-z0-9_]+$")


class RestauracionError(Exception):
    pass


# ---------------------------------------------------------------- búsqueda del backup

@dataclass
class Candidato:
    tipo: str                    # "zip" | "7z" | "bak"
    ruta: Path                   # el archivo comprimido, o la carpeta con las partes .bak
    fecha: datetime.datetime
    archivos: list = field(default_factory=list)
    completo: bool = True
    motivo: str = ""


def leer_manifiesto(texto):
    """Del DelPro.xml saca SOLO lo que hace falta. El resto (la licencia, el id del tambo) no se devuelve nunca."""
    try:
        raiz = ET.fromstring(texto)
        bruto = raiz.attrib.get("backupdate", "")
        return {"backupdate": datetime.datetime.strptime(bruto.strip(), "%Y%m%d %H%M%S"),
                "version": raiz.attrib.get("version"), "sqlserver": raiz.attrib.get("sqlserver")}
    except (ET.ParseError, ValueError, AttributeError):
        return None


def _mtime(ruta):
    return datetime.datetime.fromtimestamp(Path(ruta).stat().st_mtime)


def _numero_de_parte(ruta):
    m = re.search(r"\.(\d+)\.bak$", Path(ruta).name, re.IGNORECASE)
    return int(m.group(1)) if m else None


def partes_ordenadas(rutas):
    """DelPro.10.bak va después de DelPro.2.bak (orden numérico, no alfabético)."""
    return sorted(rutas, key=lambda p: (_numero_de_parte(p) is None, _numero_de_parte(p) or 0, Path(p).name))


def _fecha_zip(ruta):
    try:
        with zipfile.ZipFile(ruta) as z:
            for n in z.namelist():
                if n.replace("\\", "/").split("/")[-1].lower().endswith(".xml"):
                    m = leer_manifiesto(z.read(n).decode("utf-8", "replace"))
                    if m:
                        return m["backupdate"]
    except (zipfile.BadZipFile, OSError):
        pass
    return None


def buscar(carpeta_tambo, ahora):
    """Todos los backups que hay en la carpeta de un tambo, completos o no, sin elegir todavía."""
    carpeta_tambo = Path(carpeta_tambo)
    candidatos, grupos = [], {}
    if not carpeta_tambo.is_dir():
        return []
    base = len(carpeta_tambo.parts)
    for p in carpeta_tambo.rglob("*"):
        if not p.is_file() or CARPETA_TRABAJO in p.parts or len(p.parts) - base > PROFUNDIDAD_MAX:
            continue
        ext = p.suffix.lower()
        if ext in (".zip", ".7z"):
            candidatos.append(_candidato_comprimido(p, ahora))
        elif ext == ".bak":
            grupos.setdefault(p.parent, []).append(p)
    for carpeta, partes in grupos.items():
        candidatos.append(_candidato_partes(carpeta, partes, ahora))
    return candidatos


def _joven(rutas, ahora):
    return any(ahora.timestamp() - Path(r).stat().st_mtime < ANTIGUEDAD_MIN_S for r in rutas)


def _candidato_comprimido(p, ahora):
    tipo = p.suffix.lower().lstrip(".")
    c = Candidato(tipo, p, (_fecha_zip(p) if tipo == "zip" else None) or _mtime(p), [p])
    if _joven([p], ahora):
        c.completo, c.motivo = False, "se modificó hace pocos minutos (¿todavía se está bajando?)"
    elif tipo == "zip" and not _zip_sano(p):
        c.completo, c.motivo = False, "el .zip está cortado o dañado"
    return c


def _zip_sano(p):
    try:
        return zipfile.is_zipfile(p) and bool(zipfile.ZipFile(p).namelist())
    except (zipfile.BadZipFile, OSError):
        return False


def _candidato_partes(carpeta, partes, ahora):
    xmls = sorted(Path(carpeta).glob("*.xml"))
    fecha = None
    for x in xmls:
        m = leer_manifiesto(x.read_text("utf-8", "replace"))
        if m:
            fecha = m["backupdate"]
            break
    fecha = fecha or max(_mtime(p) for p in partes)
    c = Candidato("bak", Path(carpeta), fecha, partes_ordenadas(partes))
    numeros = [_numero_de_parte(p) for p in c.archivos if _numero_de_parte(p) is not None]
    if numeros and numeros != list(range(1, len(numeros) + 1)):
        c.completo, c.motivo = False, f"falta una parte (hay {numeros})"
    elif _joven(c.archivos, ahora):
        c.completo, c.motivo = False, "se modificó hace pocos minutos (¿todavía se está bajando?)"
    return c


def elegir_ultimo(candidatos, ahora=None):
    """(el más nuevo COMPLETO, los incompletos que se saltaron)."""
    ok = sorted((c for c in candidatos if c.completo), key=lambda c: c.fecha, reverse=True)
    saltados = [c for c in candidatos if not c.completo]
    return (ok[0] if ok else None), saltados


# ---------------------------------------------------------------- extracción

def _buscar_7z():
    for p in (shutil.which("7z"), r"C:\Program Files\7-Zip\7z.exe", r"C:\Program Files (x86)\7-Zip\7z.exe"):
        if p and os.path.exists(p):
            return p
    return None


def extraer(archivo, destino, buscar_7z=_buscar_7z):
    """Extrae solo los .bak y el .xml, ignorando carpetas: un nombre raro dentro del .zip no puede escribir afuera."""
    archivo, destino = Path(archivo), Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    if archivo.suffix.lower() == ".zip":
        with zipfile.ZipFile(archivo) as z:
            for info in z.infolist():
                nombre = info.filename.replace("\\", "/").split("/")[-1]
                if not nombre or Path(nombre).suffix.lower() not in (".bak", ".xml"):
                    continue
                with z.open(info) as origen, open(destino / nombre, "wb") as salida:
                    shutil.copyfileobj(origen, salida, 8 * 1024 * 1024)
        return
    exe = buscar_7z()
    if not exe:
        raise RestauracionError("El backup está en .7z y no encuentro 7-Zip. Instalá 7-Zip (7-zip.org) en esta PC.")
    r = subprocess.run([exe, "x", "-y", f"-o{destino}", str(archivo), "*.bak", "*.xml", "-r"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RestauracionError(f"7-Zip no pudo extraer {archivo.name}: {r.stdout[-300:]}")


def unir_partes(partes, destino, bloque=8 * 1024 * 1024):
    """Concatena las partes (lo que hacía `copy /b`) sin cargarlas en memoria. Devuelve los bytes escritos."""
    total = 0
    with open(destino, "wb") as salida:
        for p in partes_ordenadas(partes):
            with open(p, "rb") as f:
                while True:
                    dato = f.read(bloque)
                    if not dato:
                        break
                    salida.write(dato)
                    total += len(dato)
    return total


def preparar_bak(partes, destino):
    """Un solo .bak se usa como está; varias partes se unen en `destino`."""
    partes = partes_ordenadas(partes)
    if len(partes) == 1:
        return Path(partes[0])
    unir_partes(partes, destino)
    return Path(destino)


# ---------------------------------------------------------------- SQL

def _nombre(n):
    if not _NOMBRE_SQL.match(n):
        raise RestauracionError(f"Nombre de base no permitido: {n!r}")
    return n


def sql_restore(base_nueva, bak, filas, dir_datos, dir_log, sello):
    """RESTORE a una base nueva, con archivos de nombre nuevo (si no, chocarían con los de la base actual)."""
    base_nueva = _nombre(base_nueva)
    moves, n_datos = [], 0
    for fila in filas:
        logico, tipo = fila[0], fila[2]          # RESTORE FILELISTONLY devuelve ~22 columnas: solo estas dos importan
        if tipo.upper() == "L":
            destino = ntpath.join(dir_log, f"{base_nueva}_{sello}.ldf")
        else:
            n_datos += 1
            sufijo = ".mdf" if n_datos == 1 else f"_{n_datos}.ndf"
            destino = ntpath.join(dir_datos, f"{base_nueva}_{sello}{sufijo}")
        moves.append(f"MOVE N'{logico.replace(chr(39), chr(39) * 2)}' TO N'{destino}'")
    desde = ", ".join(f"DISK = N'{str(b)}'" for b in bak)
    return f"RESTORE DATABASE [{base_nueva}] FROM {desde} WITH {', '.join(moves)}, STATS = 10;"


def sql_usuario_lectura(base):
    base = _nombre(base)
    return (f"USE [{base}];\n"
            "IF EXISTS (SELECT 1 FROM sys.server_principals WHERE name = 'delpro_lectura')\nBEGIN\n"
            "  IF EXISTS (SELECT 1 FROM sys.database_principals WHERE name = 'delpro_lectura')\n"
            "    ALTER USER delpro_lectura WITH LOGIN = delpro_lectura;\n"
            "  ELSE\n    CREATE USER delpro_lectura FOR LOGIN delpro_lectura;\n"
            "  ALTER ROLE db_datareader ADD MEMBER delpro_lectura;\nEND")


def dar_acceso_lectura(base, ejecutar):
    """Corre solo el script del usuario de solo lectura sobre `base` (idempotente: sirve si el acceso se perdió)."""
    ejecutar(sql_usuario_lectura(base))


def bases_anteriores(base, ejecutar):
    """Las bases `<base>_ANT_*` que dejaron las restauraciones anteriores (no se borran solas: ocupan disco)."""
    base = _nombre(base)
    prefijo = f"{base}_ANT_"
    filas = ejecutar(f"SELECT name FROM sys.databases WHERE name LIKE N'{base}\\_ANT\\_%' ESCAPE '\\';")
    return sorted(f[0] for f in filas if f and f[0].startswith(prefijo))


INTENTOS_LOGIN = 4
ESPERA_LOGIN_S = 20
_LOGIN_FALLIDO = ("Login timeout expired", "Unable to complete login process", "Timeout error [258]")


def sqlcmd(instancia, correr=subprocess.run, esperar=None):
    """Ejecuta SQL con `sqlcmd -E` (autenticación de Windows) y devuelve las filas como listas de texto.

    Si el LOGIN no entró (la instancia, con poca RAM, tarda en atender justo después de crear el .bak de 2 GB), la
    sentencia no llegó a correr y se reintenta. Un error de SQL, en cambio, NO se reintenta: la sentencia sí corrió
    y repetir un RESTORE o un cambio de nombre podría hacer daño."""
    import time
    esperar = esperar or time.sleep

    def ejecutar(sql, base="master"):
        with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False, encoding="utf-8") as f:
            f.write("SET NOCOUNT ON;\n" + sql + "\n")
            ruta = f.name
        try:
            for intento in range(1, INTENTOS_LOGIN + 1):
                r = correr(["sqlcmd", "-S", instancia, "-E", "-d", base, "-b", "-h", "-1", "-W", "-s", "|",
                            "-f", "65001", "-l", "60", "-i", ruta],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                salida = r.stdout.decode("utf-8", "replace")
                if r.returncode == 0:
                    return [l.split("|") for l in salida.splitlines() if l.strip() and not l.startswith("Msg ")]
                if not any(m in salida for m in _LOGIN_FALLIDO) or intento == INTENTOS_LOGIN:
                    break
                esperar(ESPERA_LOGIN_S)
        finally:
            os.unlink(ruta)
        raise RestauracionError("SQL Server rechazó la operación:\n" + salida.strip()[-800:])
    return ejecutar


# ---------------------------------------------------------------- registro de lo restaurado

def _leer_registro(carpeta):
    try:
        return json.loads((Path(carpeta) / REGISTRO).read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _guardar_registro(carpeta, tambo, fecha, origen, ahora):
    reg = _leer_registro(carpeta)
    reg[tambo] = {"backupdate": fecha.isoformat(), "restaurado_en": ahora.isoformat(timespec="seconds"),
                  "origen": origen}
    (Path(carpeta) / REGISTRO).write_text(json.dumps(reg, indent=2, ensure_ascii=False), "utf-8")


# ---------------------------------------------------------------- orquestación

def resumen_carpeta(carpeta_tambo, tambo, ahora=None, forzar=False):
    """Qué hay en la carpeta de un tambo, sin tocar nada: {"estado": sin_backup|al_dia|nuevo, "backupdate", "nombre",
    "restaurado", "saltados"}. La misma regla de «al día» que usa `restaurar_tambo`. Una carpeta inexistente es sin_backup."""
    ahora = ahora or datetime.datetime.now()
    elegido, saltados = elegir_ultimo(buscar(carpeta_tambo, ahora))
    r = {"estado": "sin_backup", "backupdate": None, "nombre": None, "restaurado": None,
         "saltados": [f"{c.ruta.name}: {c.motivo}" for c in saltados]}
    if not elegido:
        return r
    r["backupdate"] = elegido.fecha
    r["nombre"] = elegido.ruta.name
    previo = _leer_registro(carpeta_tambo).get(tambo, {}).get("backupdate")
    r["restaurado"] = previo
    al_dia = bool(previo) and not forzar and datetime.datetime.fromisoformat(previo) >= elegido.fecha
    r["estado"] = "al_dia" if al_dia else "nuevo"
    return r


def restaurar_tambo(tambo, base, carpeta, ejecutar, ahora=None, forzar=False, solo_ver=False,
                    buscar_7z=_buscar_7z, log=print, esperar=None, carpeta_propia=False):
    """Restaura el backup más nuevo de `tambo` como la base `base`. Devuelve {"estado": ...}.

    Con `carpeta_propia=True`, `carpeta` ES la carpeta de los backups de ese tambo (no se le agrega `/<tambo>`), y el
    registro de lo restaurado y la carpeta de trabajo quedan ahí. Es lo que usa el panel de despliegue.

    estados: sin_backup · al_dia · solo_ver · restaurado (cualquier falla es RestauracionError, con la base actual intacta)
    """
    ahora = ahora or datetime.datetime.now()
    carpeta = Path(carpeta)
    carpeta_tambo = carpeta if carpeta_propia else carpeta / tambo
    registro = carpeta_tambo if carpeta_propia else carpeta
    elegido, saltados = elegir_ultimo(buscar(carpeta_tambo, ahora))
    for c in saltados:
        log(f"  [{tambo}] se salta {c.ruta.name}: {c.motivo}")
    if not elegido:
        log(f"  [{tambo}] no hay ningún backup completo en {carpeta_tambo}")
        return {"estado": "sin_backup"}
    que = elegido.ruta.name if elegido.tipo != "bak" else f"{len(elegido.archivos)} parte(s) .bak en {elegido.ruta.name}"
    log(f"  [{tambo}] más nuevo: {que} (backup del {elegido.fecha:%d/%m/%Y %H:%M})")

    previo = _leer_registro(registro).get(tambo, {}).get("backupdate")
    if previo and not forzar and datetime.datetime.fromisoformat(previo) >= elegido.fecha:
        log(f"  [{tambo}] ya está restaurado ese backup o uno más nuevo ({previo[:16]}); nada que hacer")
        return {"estado": "al_dia", "backupdate": elegido.fecha}
    if solo_ver:
        log(f"  [{tambo}] (solo ver) se restauraría como la base [{base}]")
        return {"estado": "solo_ver", "backupdate": elegido.fecha}

    _nombre(base)
    nueva = f"{base}_NUEVA"
    sello = ahora.strftime("%Y%m%d_%H%M%S")      # la hora de ESTA corrida: el mismo backup restaurado dos veces no choca
    existentes = {f[0] for f in ejecutar(
        f"SELECT name FROM sys.databases WHERE name IN (N'{base}', N'{nueva}');")}
    if nueva in existentes:
        raise RestauracionError(
            f"Quedó una base [{nueva}] de una corrida anterior que no terminó. No la borro sola: revisala y, si "
            f"está de más, eliminala a mano (DROP DATABASE [{nueva}];) y volvé a correr.")

    trabajo = carpeta_tambo / CARPETA_TRABAJO
    try:
        if trabajo.exists():
            shutil.rmtree(trabajo)
        trabajo.mkdir(parents=True)
        if elegido.tipo == "bak":
            partes = elegido.archivos
        else:
            log(f"  [{tambo}] extrayendo…")
            extraer(elegido.ruta, trabajo / "extraido", buscar_7z)
            partes = list((trabajo / "extraido").glob("*.bak"))
            if not partes:
                raise RestauracionError(f"{elegido.ruta.name} no trae ningún .bak adentro")
        log(f"  [{tambo}] preparando el .bak…")
        bak = preparar_bak(partes, trabajo / "DelPro_full.bak")

        log(f"  [{tambo}] verificando el backup…")
        ejecutar(f"RESTORE VERIFYONLY FROM DISK = N'{bak}';")
        filas = [f for f in ejecutar(f"RESTORE FILELISTONLY FROM DISK = N'{bak}';") if len(f) >= 3]
        rutas = ejecutar("SELECT CAST(SERVERPROPERTY('InstanceDefaultDataPath') AS nvarchar(260)), "
                         "CAST(SERVERPROPERTY('InstanceDefaultLogPath') AS nvarchar(260));")[0]
        log(f"  [{tambo}] restaurando en [{nueva}] (puede tardar varios minutos)…")
        try:
            # AUTO_CLOSE se apaga en el MISMO lote: las bases de DelPro lo traen activado y, justo después del restore,
            # la conexión siguiente se cuelga 2 minutos ("could not be exclusively locked", error 5030).
            ejecutar(sql_restore(nueva, [bak], filas, rutas[0], rutas[1], sello)
                     + f"\nALTER DATABASE [{nueva}] SET AUTO_CLOSE OFF;")
            ejecutar(sql_usuario_lectura(nueva))
            animales = int(ejecutar(f"SELECT COUNT(*) FROM [{nueva}].dbo.BasicAnimal;")[0][0])
            if animales <= 0:
                raise RestauracionError(f"La base restaurada no trae animales (BasicAnimal vacía): no se usa. "
                                        f"La base [{base}] actual queda como estaba.")
        except Exception:
            _descartar_si_quedo(nueva, ejecutar, log)
            raise

        _intercambiar(base, nueva, base in existentes, ahora, ejecutar, log, esperar)
        _guardar_registro(registro, tambo, elegido.fecha, elegido.ruta.name, ahora)
        log(f"  [{tambo}] LISTO: [{base}] ahora tiene el backup del {elegido.fecha:%d/%m/%Y %H:%M} ({animales} animales)")
        return {"estado": "restaurado", "backupdate": elegido.fecha, "animales": animales}
    finally:
        shutil.rmtree(trabajo, ignore_errors=True)


def _descartar_si_quedo(nueva, ejecutar, log):
    """Una base `_NUEVA` a medio restaurar la creó ESTA corrida y no sirve para nada: se saca para no trabar la próxima."""
    try:
        hay = ejecutar(f"SELECT name FROM sys.databases WHERE name = N'{nueva}';")
        if any(f and f[0] == nueva for f in hay):
            ejecutar(f"DROP DATABASE [{_nombre(nueva)}];")
            log(f"  se sacó la base incompleta [{nueva}] (la creó esta misma corrida)")
    except RestauracionError as e:
        log(f"  OJO: no pude sacar la base incompleta [{nueva}]: {e}")


INTENTOS_RENOMBRAR = 3
ESPERA_RENOMBRAR_S = 30


def _renombrar(sql, ejecutar, esperar, log):
    """Un cambio de nombre pide acceso exclusivo a la base, y justo después de un restore tareas internas de SQL Server
    la tienen tomada un rato: falla con el 5030 tras esperar 2 a 5 minutos (medido). Como no llegó a cambiar nada, se
    reintenta. Cualquier otro error no se reintenta."""
    for intento in range(1, INTENTOS_RENOMBRAR + 1):
        try:
            return ejecutar(sql)
        except RestauracionError as e:
            if "5030" not in str(e) or intento == INTENTOS_RENOMBRAR:
                raise
            log(f"  la base todavía está ocupada (intento {intento} de {INTENTOS_RENOMBRAR}); espero y reintento…")
            esperar(ESPERA_RENOMBRAR_S)


def _intercambiar(base, nueva, hay_actual, ahora, ejecutar, log, esperar=None):
    import time
    esperar = esperar or time.sleep
    if not hay_actual:
        _renombrar(f"ALTER DATABASE [{nueva}] MODIFY NAME = [{base}];", ejecutar, esperar, log)
        return
    anterior = f"{base}_ANT_{ahora:%Y%m%d_%H%M}"
    ejecutar(f"ALTER DATABASE [{base}] SET SINGLE_USER WITH ROLLBACK IMMEDIATE;")
    try:
        ejecutar(f"ALTER DATABASE [{base}] MODIFY NAME = [{anterior}];")
    except RestauracionError:
        ejecutar(f"ALTER DATABASE [{base}] SET MULTI_USER;")
        raise
    ejecutar(f"ALTER DATABASE [{anterior}] SET MULTI_USER;")
    try:
        _renombrar(f"ALTER DATABASE [{nueva}] MODIFY NAME = [{base}];", ejecutar, esperar, log)
    except RestauracionError:
        log(f"  FALLÓ el último cambio de nombre: se devuelve [{anterior}] a [{base}]")
        ejecutar(f"ALTER DATABASE [{anterior}] MODIFY NAME = [{base}];")
        raise
    log(f"  la base anterior quedó como [{anterior}] (no se borra)")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Restaura el backup más nuevo de cada tambo desde C:\\backupsdelpro.")
    ap.add_argument("--carpeta", default=CARPETA_BACKUPS)
    ap.add_argument("--instancia", default=INSTANCIA)
    ap.add_argument("--tambo", choices=sorted(TAMBOS), help="uno solo; sin esto, todos")
    ap.add_argument("--base", help="nombre de la base destino (solo con --tambo; para pruebas)")
    ap.add_argument("--solo-ver", action="store_true", help="muestra qué usaría, sin tocar nada")
    ap.add_argument("--forzar", action="store_true", help="restaura aunque ese backup ya esté restaurado")
    a = ap.parse_args(argv)
    if a.base and not a.tambo:
        ap.error("--base necesita --tambo")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    ejecutar = sqlcmd(a.instancia)
    errores = 0
    for tambo in ([a.tambo] if a.tambo else sorted(TAMBOS)):
        print(f"== {tambo}")
        try:
            restaurar_tambo(tambo, a.base or TAMBOS[tambo], a.carpeta, ejecutar, forzar=a.forzar, solo_ver=a.solo_ver)
        except RestauracionError as e:
            errores += 1
            print(f"  ERROR: {e}")
    return 1 if errores else 0


if __name__ == "__main__":
    sys.exit(main())
