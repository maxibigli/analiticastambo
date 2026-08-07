# -*- coding: utf-8 -*-
"""SenseHub / Allflex: collares de actividad y rumia (celo y salud).

QUÉ ES. Un segundo sistema, independiente de DelPro, que mide actividad y rumia
con collares y de ahí saca dos cosas: el CELO (índice de celo, ventana de
servicio) y la SALUD (índice de salud y alertas de "distress"). Este módulo es
el cliente para leerlo; el cruce contra DDM va aparte.

DÓNDE VIVE EL DATO, y por qué no hay una base a la que conectarse. El dato está
en el CONTROLADOR, un equipo en la red del tambo (un servidor Java —WildFly/
Undertow— que sirve la app web, la API REST y tiene su base adentro, sin
exponer). Allflex no publica el motor ni abre un puerto de base: la interfaz
soportada es la API REST, y eso es "la base" para nosotros. En la PC del tambo
no queda nada: el `SenseHub Tools` que se instala ahí es un Chromium empaquetado
más un servicio que baja dos reportes.

DOS CANALES, Y NO DAN LO MISMO:

  * `exportar_*`  — el export de TERCEROS (`/rest/api/thirdparty/export`). Es el
    que suele estar ya habilitado y no necesita usuario del tambo. **Trae SOLO
    LA LISTA DE ALERTA, no el rodeo**: medido en el tambo de Bernardo, 13-14
    vacas por respuesta contra 686 collares. Sirve para "quién está marcado
    hoy"; NO para la serie histórica de todo el rodeo.
  * `animales()`, `alertas()`, `eventos_de()` — la API que usa la aplicación
    web. Da todo el rodeo, pero pide login.

DOS CLAVES DE IDENTIDAD, NO UNA. Es el error fácil de este sistema:

    animalName / cowNumber / entityNumber   el RP que ve el tambo ("4116")
    animalId / entityId                     el id interno de SenseHub (1320)

El export de terceros habla en RP; los endpoints por animal piden el id interno.
`indice_por_rp()` arma el mapa una sola vez para no mezclarlos.

`isActivityAlert` NO ES UNA ALERTA DE SALUD, aunque el nombre lo sugiera.
Medido sobre las 329 fichas que quedaron en el caché de los dos tambos: las 329
tienen `isActivityAlert=True` y `isRuminationAlert=False`, sin una excepción. Un
indicador de salud no da 100% en todo el rodeo: es una bandera de CAPACIDAD del
collar (si reporta actividad / rumia). La salud real está en las alertas con
`alertType="Distress"` y en el índice de salud.

CÓMO SE PRUEBA SIN RED. El parseo está separado del transporte a propósito:
cada `parse_*` recibe el JSON ya decodificado. Así se puede probar contra
respuestas reales guardadas, que es como se escribió este módulo — sin llegar a
ningún controlador. `Controlador` solo agrega HTTP y sesión.

CREDENCIALES. Nunca en el código ni en el repo: salen de variables de entorno
(`SENSEHUB_USER_<TAMBO>` / `SENSEHUB_PWD_<TAMBO>`), igual criterio que
`tambos.py`. El export de terceros usa el usuario de fábrica del equipo, que no
es un secreto del tambo pero también es configurable.
"""
from __future__ import annotations

import base64
import datetime
import json
import os
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT_S = 20
# Usuario de fábrica del canal de terceros. Viene así en el `WebAppService.exe`
# de Allflex; no es un secreto del tambo, pero se deja configurable porque un
# controlador puede tenerlo cambiado (le pasa a La Martina, que rechaza este).
USUARIO_TERCEROS = ("ThirdParty", "ThirdParty")

# Lo que devuelve `alertas()`: son los DOS únicos valores de alertType vistos.
ALERTA_SALUD = "Distress"       # la vaca: caída de rumia/actividad
ALERTA_EQUIPO = "Maintenance"   # el collar: sin asignar, sin datos, etc.


class SenseHubError(RuntimeError):
    """Falla al hablar con el controlador. Se distingue de un error de parseo
    para que el consumidor sepa si el problema es de red/credenciales o de que
    el equipo devolvió algo que no entendemos."""


def variable_usuario(tambo: str) -> str:
    return f"SENSEHUB_USER_{tambo.upper()}"


def variable_password(tambo: str) -> str:
    return f"SENSEHUB_PWD_{tambo.upper()}"


def credenciales_de(tambo: str) -> tuple:
    """(usuario, contraseña) del tambo, o (None, None) si no están seteadas.
    Solo del entorno: a diferencia de la conexión a DDM, acá no se admite
    guardarlas en un archivo."""
    return (os.environ.get(variable_usuario(tambo)),
            os.environ.get(variable_password(tambo)))


# --- Parseo (probable sin red) ----------------------------------------------

def _epoch(v):
    """Los timestamps del controlador son epoch en SEGUNDOS. None si no hay."""
    if v in (None, "", 0):
        return None
    try:
        return datetime.datetime.fromtimestamp(int(v))
    except (ValueError, OSError, OverflowError):
        return None


def _result(payload):
    """Todas las respuestas vienen envueltas en {"result": ...}."""
    if not isinstance(payload, dict) or "result" not in payload:
        raise ValueError(f"Respuesta sin 'result': {str(payload)[:120]}")
    return payload["result"]


def parse_health_index(payload: dict) -> dict:
    """`exportDataType=healthIndex`. OJO: son las vacas ALERTADAS, no el rodeo.

    Devuelve {"calculado": datetime|None, "vacas": [{rp, indice}]}."""
    r = _result(payload)
    return {
        "calculado": _epoch(r.get("calculationDateTime")),
        "vacas": [{"rp": str(v["cowNumber"]), "indice": v.get("healthIndex")}
                  for v in (r.get("values") or []) if v.get("cowNumber") is not None],
    }


def parse_system_heats(payload: dict) -> dict:
    """`exportDataType=systemHeats`: los celos detectados por el sistema.

    `ventana_inicio` es cuándo ABRE la ventana de servicio y `ventana_valor` su
    duración declarada por el equipo; el consumidor decide si le suma horas."""
    r = _result(payload)
    return {
        "calculado": _epoch(r.get("calculationDateTime")),
        "vacas": [{
            "rp": str(v["cowNumber"]),
            "indice_pico": v.get("peakHeatIndex"),
            "indice_actual": v.get("currentHeatIndex"),
            "ventana_inicio": _epoch(v.get("breedingWindowStartDateTime")),
            "ventana_valor": v.get("breedingWindowValue"),
        } for v in (r.get("values") or []) if v.get("cowNumber") is not None],
    }


def parse_animal(payload: dict) -> dict:
    """Ficha de `/rest/api/animals/{animalId}/details`.

    `activa_actividad`/`activa_rumia` son los antiguos `isActivityAlert` /
    `isRuminationAlert`: se renombran a propósito porque el nombre original
    hace creer que son alertas de salud y NO lo son (ver el encabezado)."""
    r = _result(payload)
    return {
        "id": r.get("animalId"),
        "rp": str(r["animalName"]) if r.get("animalName") is not None else None,
        "grupo": r.get("groupName"),
        "grupo_id": r.get("groupId"),
        "estado": r.get("status"),
        "descartada": bool(r.get("isCulled")),
        "activa_actividad": bool(r.get("isActivityAlert")),
        "activa_rumia": bool(r.get("isRuminationAlert")),
    }


def parse_lactancia(payload: dict) -> dict:
    """La parte reproductiva de la ficha (viene en otra respuesta)."""
    r = _result(payload)
    return {
        "estado": r.get("lactationStatus"),
        "dias_en_leche": r.get("dim"),
        "lactancia": r.get("lactationNumber"),
        "n_servicio": r.get("breedingNumber"),
        "resultado_tacto": r.get("pregnancyCheckResult"),
        "inicio": _epoch(r.get("startDate")),
        "servicio": _epoch(r.get("breedingDate")),
        "secado": _epoch(r.get("dryOffDate")),
        "parto_esperado": _epoch(r.get("expectedCalvingDate")),
    }


def parse_alertas(payload: dict) -> list:
    """Alertas del tambo. `entityType` dice de qué es cada una: "Cow" son las
    de salud (`Distress`) y "Tag" las del collar (`Maintenance`). El RP viene
    en `entityNumber` y el id interno en `entityId` — los dos, que es lo que
    permite cruzar sin mantener un mapa aparte."""
    r = _result(payload)
    filas = []
    for clave in ("alerts", "farmAlerts", "maintenanceCalls", "distressCalls"):
        for a in (r.get(clave) or []) if isinstance(r, dict) else []:
            if not isinstance(a, dict) or not a.get("alertType"):
                continue
            filas.append({
                "tipo": a.get("alertType"),
                "detalle": a.get("alertLocalization"),
                "de": a.get("entityType"),
                "rp": str(a["entityNumber"]) if a.get("entityNumber") is not None else None,
                "id": a.get("entityId"),
                "fecha": _epoch(a.get("alertDateTime")),
                "es_salud": a.get("alertType") == ALERTA_SALUD,
            })
    return filas


def parse_eventos(payload: dict) -> list:
    """Eventos de `/rest/api/animals/{animalId}/events`: la línea de tiempo del
    animal (servicios, tactos, partos, secados, cambios de grupo, bajas...).

    `startDateTime` es cuándo PASÓ y `reportingDateTime` cuándo se CARGÓ. No
    son lo mismo y la diferencia importa: un evento cargado tarde no es un
    evento que ocurrió tarde."""
    r = _result(payload)
    crudos = r.get("events") if isinstance(r, dict) else r
    filas = []
    for e in (crudos or []):
        if not isinstance(e, dict) or not e.get("type"):
            continue
        filas.append({
            "tipo": e.get("type"),
            "evento_id": e.get("eventId"),
            "animal_id": e.get("animalId"),
            "rp": str(e["animalName"]) if e.get("animalName") is not None else None,
            "lactancia": e.get("lactationNumber"),
            "dias_en_leche": e.get("daysInLactation"),
            "edad_dias": e.get("ageInDays"),
            "fecha": _epoch(e.get("startDateTime")),
            "cargado": _epoch(e.get("reportingDateTime")),
        })
    return filas


def parse_estado_sistema(payload: dict) -> dict:
    """Salud de la INSTALACIÓN, no del rodeo. Es el primer lugar a mirar cuando
    no llegan datos: dice si el canal de terceros está prendido y si está
    sincronizando, que es justo lo que falla en La Martina (15 meses de 401 sin
    que nadie lo viera). `*_duration` son los segundos desde la última
    sincronización: si crecen sin parar, el canal está caído aunque el equipo
    responda."""
    r = _result(payload)
    return {
        "estado": r.get("systemState"),
        "terceros_activo": r.get("isThirdPartyMode"),
        "terceros_con_alerta": r.get("isThirdPartyConnectivityAlert"),
        "terceros_ultima_sync_seg": r.get("thirdPartyLastSyncDuration"),
        "nube_con_alerta": r.get("isCloudConnectivityAlert"),
        "nube_ultima_sync_seg": r.get("cloudLastUpdateDuration"),
        "backup_con_alerta": r.get("isBackupAlert"),
        "backup_ultimo_seg": r.get("lastBackupDuration"),
    }


def indice_por_rp(animales: list) -> dict:
    """{rp: animalId} — el mapa que hace falta para pasar del canal de terceros
    (habla en RP) a los endpoints por animal (piden el id interno).

    Si dos animales comparten RP se queda con el NO descartado: en SenseHub el
    número se puede reutilizar cuando la vaca original se fue."""
    mapa = {}
    for a in animales:
        rp = a.get("rp")
        if not rp:
            continue
        previo = mapa.get(rp)
        if previo is None or (previo.get("descartada") and not a.get("descartada")):
            mapa[rp] = a
    return {rp: a["id"] for rp, a in mapa.items()}


# --- Transporte --------------------------------------------------------------

class Controlador:
    """Cliente HTTP del controlador de un tambo.

    `ip` es la del equipo en la red del tambo — CAMBIA: los dos tambos mirados
    tenían una IP distinta en el caché de 2025 que la de hoy, así que sale de la
    configuración y no de una constante.
    """

    def __init__(self, ip: str, tambo: str = "", timeout: int = TIMEOUT_S):
        if not ip:
            raise ValueError("Falta la IP del controlador SenseHub")
        self.base = f"http://{ip}".rstrip("/")
        self.tambo = tambo
        self.timeout = timeout
        self._token = None

    # -- crudo --
    def _pedir(self, ruta: str, params: dict | None = None,
               cuerpo: dict | None = None, con_token: bool = True) -> dict:
        url = self.base + ruta
        if params:
            url += "?" + urllib.parse.urlencode(params)
        datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
        req = urllib.request.Request(url, data=datos, method="POST" if datos else "GET")
        req.add_header("Accept", "application/json")
        if datos:
            req.add_header("Content-Type", "application/json")
        if con_token and self._token:
            req.add_header("Authorization", self._token)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            # 401 es EL error de este sistema y merece un mensaje propio: en La
            # Martina el servicio lleva 15 meses fallando así, con el usuario de
            # fábrica rechazado por el controlador.
            if e.code == 401:
                raise SenseHubError(
                    f"El controlador rechazó las credenciales (401) en {ruta}. "
                    f"Si es el canal de terceros, hay que habilitarlo en el equipo; "
                    f"si es la API, revisar {variable_usuario(self.tambo)} / "
                    f"{variable_password(self.tambo)}.") from e
            raise SenseHubError(f"HTTP {e.code} en {ruta}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SenseHubError(f"No se pudo llegar al controlador ({self.base}): {e}") from e
        except ValueError as e:
            raise SenseHubError(f"El controlador respondió algo que no es JSON en {ruta}") from e

    def ping(self) -> bool:
        try:
            self._pedir("/rest/api/server/ping", con_token=False)
            return True
        except SenseHubError:
            return False

    def login(self, usuario: str | None = None, password: str | None = None) -> None:
        """Autentica contra la API completa. Sin credenciales explícitas usa las
        del entorno del tambo."""
        if usuario is None or password is None:
            usuario, password = credenciales_de(self.tambo)
        if not usuario or not password:
            raise SenseHubError(
                f"Faltan credenciales de SenseHub: seteá {variable_usuario(self.tambo)} y "
                f"{variable_password(self.tambo)} (nunca en el código ni en el repo).")
        r = self._pedir("/rest/api/v4/auth/login",
                        cuerpo={"userName": usuario, "password": password}, con_token=False)
        res = r.get("result") or {}
        self._token = res.get("token") or res.get("accessToken")
        if not self._token:
            raise SenseHubError("El login no devolvió token; ¿cambió la versión de la API?")

    # -- canal de terceros (no necesita login del tambo) --
    def _export(self, tipo: str, usuario=None) -> dict:
        u, p = usuario or USUARIO_TERCEROS
        # El canal de terceros usa Basic con el usuario del equipo, no el token.
        cred = base64.b64encode(f"{u}:{p}".encode()).decode()
        url = f"{self.base}/rest/api/thirdparty/export?exportDataType={tipo}"
        req = urllib.request.Request(url)
        req.add_header("Authorization", "Basic " + cred)
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise SenseHubError(
                    "El controlador rechazó el usuario del canal de terceros (401). "
                    "Hay que habilitar la integración de terceros en el equipo — es "
                    "exactamente lo que le pasa a La Martina.") from e
            raise SenseHubError(f"HTTP {e.code} en el export {tipo}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise SenseHubError(f"No se pudo llegar al controlador ({self.base}): {e}") from e

    def exportar_salud(self, usuario=None) -> dict:
        """Índice de salud de las vacas ALERTADAS (no del rodeo)."""
        return parse_health_index(self._export("healthIndex", usuario))

    def exportar_celos(self, usuario=None) -> dict:
        return parse_system_heats(self._export("systemHeats", usuario))

    # -- API completa (necesita login) --
    def animales(self) -> list:
        """Todo el rodeo. `/rest/api/animals` devuelve la lista; la ficha de
        cada uno se pide aparte con `animal()`."""
        r = self._pedir("/rest/api/animals")
        crudos = _result(r)
        if isinstance(crudos, dict):
            crudos = crudos.get("animals") or crudos.get("values") or []
        return [parse_animal({"result": a}) for a in crudos if isinstance(a, dict)]

    def animal(self, animal_id: int) -> dict:
        return parse_animal(self._pedir(f"/rest/api/animals/{int(animal_id)}/details"))

    def eventos_de(self, animal_id: int) -> list:
        return parse_eventos(self._pedir(f"/rest/api/animals/{int(animal_id)}/events"))

    def alertas(self) -> list:
        """Alertas del tambo, salud y equipo juntas. Filtrar por `es_salud`."""
        return parse_alertas(self._pedir("/rest/api/alerts/farm"))

    def estado_sistema(self) -> dict:
        """Si el canal de terceros está prendido y sincronizando. Conviene
        mirarlo ANTES de concluir que un tambo no tiene datos."""
        return parse_estado_sistema(self._pedir("/rest/api/alerts/system"))

    def cambios_desde(self, desde: datetime.datetime) -> dict:
        """Sync incremental: los cambios desde un momento dado. Es EL endpoint
        para espejar sin machacar el equipo — la propia app web lo llama miles
        de veces con `lastUpdateTime`. Se devuelve crudo a propósito: todavía no
        se vio una respuesta real completa como para fijar un parseo."""
        return self._pedir("/rest/api/v3/server/sync",
                           params={"lastUpdateTime": int(desde.timestamp())})
