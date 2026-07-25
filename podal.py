# -*- coding: utf-8 -*-
"""Problemas podales (renguera) vía cámaras en la salida de la rotativa.

Arquitectura (ver `config_podal.py` y `podal_vision.py` para el detalle de
cada parte):

1. Dos cámaras miran el mismo corredor de salida: "posicion" (confirma el
   instante de paso) y "marcha" (dan las métricas de marcha).
2. Cuando `ServicioCamaras` detecta una pasada completa, calcula el score con
   `podal_vision` y busca DE QUÉ VACA se trata cruzando el instante del
   evento con `MilkConfirmTime` de DelPro (`resolver_rp`) -- la identidad NO
   sale de reconocer la vaca por imagen, sale del dato que DelPro ya tiene.
3. El resultado se guarda en una base SQLite LOCAL (`podal.db`), separada de
   DDM: la base DDM de DelPro es de solo lectura para esta app (ver
   `db.py`), así que no hay forma de escribirle los scores aunque quisiéramos.
   `podal.db` es estado propio de esta instalación (no va al repo, ver
   `.gitignore`), igual que `usuarios.json` o `alertas_canales.json`.

Todo el score es un heurístico v1 sin calibrar con casos reales de este
tambo (no hay cámaras instaladas todavía) -- ver la nota completa en
`podal_vision.py` antes de tomar los números como diagnóstico.
"""
from __future__ import annotations

import datetime
import json
import os
import sqlite3
import threading
import time

import config_podal
import db
import podal_vision

try:
    import cv2
except ImportError:
    cv2 = None

_DB_PATH = os.path.join(os.path.dirname(__file__), "podal.db")
_db_lock = threading.Lock()

_TS_FMT = "%Y-%m-%d %H:%M:%S"

DIAS_RECIENTE_DEFECTO = 14   # ventana "reciente" para el promedio de alerta
DIAS_REFERENCIA_DEFECTO = 60  # historial total a considerar (reciente + previo)
UMBRAL_ALERTA = 3.0          # score (1-5) a partir del cual se marca la vaca
TOP_ALERTAS = 15


def _conectar() -> sqlite3.Connection:
    con = sqlite3.connect(_DB_PATH, check_same_thread=False)
    con.execute("""
        CREATE TABLE IF NOT EXISTS lecturas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tambo TEXT NOT NULL,
            ts TEXT NOT NULL,
            rp INTEGER,
            plaza INTEGER,
            resuelto INTEGER NOT NULL,
            score REAL NOT NULL,
            curvatura_prom REAL,
            curvatura_max REAL,
            oscilacion_vertical REAL,
            motivos TEXT,
            creado_en TEXT NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS ix_lecturas_tambo_rp ON lecturas(tambo, rp)")
    return con


def guardar_lectura(tambo: str, ts: datetime.datetime, resultado: dict,
                     rp: int | None, plaza: int | None) -> None:
    """Guarda una pasada ya puntuada (`resultado` = lo que devuelve
    `podal_vision.score_renguera`)."""
    with _db_lock:
        con = _conectar()
        try:
            con.execute(
                "INSERT INTO lecturas (tambo, ts, rp, plaza, resuelto, score, "
                "curvatura_prom, curvatura_max, oscilacion_vertical, motivos, creado_en) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (tambo, ts.strftime(_TS_FMT), rp, plaza, 1 if rp is not None else 0,
                 resultado["score"], resultado.get("curvatura_prom"),
                 resultado.get("curvatura_max"), resultado.get("oscilacion_vertical"),
                 json.dumps(resultado.get("motivos") or []),
                 datetime.datetime.now().strftime(_TS_FMT)))
            con.commit()
        finally:
            con.close()


def historial(tambo: str, rp: int | None = None, dias: int = DIAS_REFERENCIA_DEFECTO) -> list:
    """Lecturas guardadas del tambo (opcionalmente de un solo RP), más
    recientes primero, dentro de la ventana de días pedida."""
    desde = (datetime.datetime.now() - datetime.timedelta(days=dias)).strftime(_TS_FMT)
    with _db_lock:
        con = _conectar()
        try:
            if rp is not None:
                cur = con.execute(
                    "SELECT ts, rp, plaza, score, motivos FROM lecturas "
                    "WHERE tambo=? AND rp=? AND ts >= ? ORDER BY ts DESC",
                    (tambo, rp, desde))
            else:
                cur = con.execute(
                    "SELECT ts, rp, plaza, score, motivos FROM lecturas "
                    "WHERE tambo=? AND ts >= ? ORDER BY ts DESC",
                    (tambo, desde))
            filas = cur.fetchall()
        finally:
            con.close()
    return [{"ts": f[0], "rp": f[1], "plaza": f[2], "score": f[3],
             "motivos": json.loads(f[4] or "[]")} for f in filas]


def calcular_alertas(tambo: str, dias_reciente: int = DIAS_RECIENTE_DEFECTO,
                      dias_referencia: int = DIAS_REFERENCIA_DEFECTO,
                      umbral: float = UMBRAL_ALERTA, top: int = TOP_ALERTAS) -> list:
    """Agrupa el historial por vaca: promedio de score reciente vs. su propio
    historial previo. Se marca alerta si el promedio reciente supera el
    umbral, o si empeoró fuerte respecto de su referencia -- mismo criterio
    de "contra su propio historial" que usa `salud.calcular_atencion_v2`."""
    filas = historial(tambo, dias=dias_referencia)
    corte = datetime.datetime.now() - datetime.timedelta(days=dias_reciente)
    por_rp: dict = {}
    for f in filas:
        if f["rp"] is None:
            continue
        por_rp.setdefault(f["rp"], []).append(f)

    fichas = []
    for rp, lista in por_rp.items():
        recientes = [f["score"] for f in lista
                     if datetime.datetime.strptime(f["ts"], _TS_FMT) >= corte]
        previas = [f["score"] for f in lista
                   if datetime.datetime.strptime(f["ts"], _TS_FMT) < corte]
        if not recientes:
            continue
        prom_reciente = sum(recientes) / len(recientes)
        prom_previo = sum(previas) / len(previas) if previas else None
        tendencia = round(prom_reciente - prom_previo, 1) if prom_previo is not None else None
        empeora = tendencia is not None and tendencia >= 0.7
        if prom_reciente < umbral and not empeora:
            continue
        motivos = lista[0]["motivos"] if lista else []
        fichas.append({
            "rp": rp, "score_reciente": round(prom_reciente, 1),
            "score_referencia": round(prom_previo, 1) if prom_previo is not None else None,
            "tendencia": tendencia, "n_lecturas": len(recientes), "motivos": motivos,
        })
    fichas.sort(key=lambda f: -f["score_reciente"])
    return fichas[:top]


def resolver_rp(tambo: str, ts: datetime.datetime, tolerancia_seg: int) -> dict | None:
    """Busca en DelPro qué vaca terminó de ordeñarse más cerca del instante
    `ts` (evento detectado por la cámara), dentro de la tolerancia dada. La
    identidad sale de `CMSMilkYield.MilkConfirmTime` (el momento en que el
    equipo confirma que terminó el ordeño de esa vaca, justo antes de que
    salga caminando) cruzado con `MilkingDeviceVisit`/`BasicAnimal` -- NO de
    reconocer la vaca por imagen."""
    desde = (ts - datetime.timedelta(seconds=tolerancia_seg)).strftime(_TS_FMT)
    hasta = (ts + datetime.timedelta(seconds=tolerancia_seg)).strftime(_TS_FMT)
    ts_txt = ts.strftime(_TS_FMT)
    sql = f"""
        SELECT TOP 1 b.Number AS rp, v.Place AS plaza,
               ABS(DATEDIFF(second, y.MilkConfirmTime, '{ts_txt}')) AS diff_seg
        FROM CMSMilkYield y
        JOIN MilkingDeviceVisit v ON v.OID = y.MilkingDeviceVisit
        JOIN BasicAnimal b ON b.OID = v.Animal
        WHERE y.MilkConfirmTime BETWEEN '{desde}' AND '{hasta}'
        ORDER BY diff_seg ASC
        OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 10)
    """
    data = db.run_query(sql, tambo=tambo)
    if not data["rows"]:
        return None
    idx = {c: i for i, c in enumerate(data["columns"])}
    row = data["rows"][0]
    return {"rp": row[idx["rp"]], "plaza": row[idx["plaza"]], "diff_seg": row[idx["diff_seg"]]}


# --- Captura en vivo ---------------------------------------------------------
# Un hilo por tambo, arrancado a pedido (no hay cámaras instaladas todavía en
# ningún tambo, así que por defecto no se arranca nada -- ver config_podal).

class _CapturaTambo:
    def __init__(self, tambo: str, cfg: dict):
        self.tambo = tambo
        self.cfg = cfg
        self._detener = threading.Event()
        self._hilo = None
        self.conectada_marcha = False
        self.conectada_posicion = False
        self.ultimo_error = None

    def iniciar(self):
        if self._hilo and self._hilo.is_alive():
            return
        self._detener.clear()
        self._hilo = threading.Thread(target=self._loop, daemon=True)
        self._hilo.start()

    def detener(self):
        self._detener.set()
        if self._hilo:
            self._hilo.join(timeout=5)

    def activa(self) -> bool:
        return bool(self._hilo and self._hilo.is_alive())

    def _loop(self):
        if cv2 is None:
            self.ultimo_error = "Falta opencv-python-headless (ver requirements.txt)."
            return
        cap_marcha = cv2.VideoCapture(self.cfg["fuente_marcha"])
        cap_posicion = (cv2.VideoCapture(self.cfg["fuente_posicion"])
                        if self.cfg.get("fuente_posicion") else None)
        self.conectada_marcha = cap_marcha.isOpened()
        self.conectada_posicion = cap_posicion.isOpened() if cap_posicion else None
        if not self.conectada_marcha:
            self.ultimo_error = f"No se pudo abrir la cámara de marcha ({self.cfg['fuente_marcha']})."
            return

        detector = podal_vision.DetectorPresencia()
        intervalo = 1.0 / config_podal.FPS_PROCESAMIENTO
        buffer_frames: list = []
        ausentes_seguidos = 0
        presente_posicion = False

        try:
            while not self._detener.is_set():
                t0 = time.time()
                ok, frame = cap_marcha.read()
                if not ok:
                    self.conectada_marcha = False
                    break
                if cap_posicion is not None:
                    ok_p, frame_p = cap_posicion.read()
                    if ok_p:
                        presente_posicion, _, _ = detector.procesar(frame_p)

                presente, _, _ = detector.procesar(frame)
                # Si hay cámara de posición, se exige que confirme el paso
                # (reduce falsos positivos de la cámara de marcha, ej. una
                # persona cruzando el corredor).
                cuenta = presente and (cap_posicion is None or presente_posicion)
                if cuenta:
                    buffer_frames.append(frame)
                    ausentes_seguidos = 0
                else:
                    ausentes_seguidos += 1
                    if buffer_frames and ausentes_seguidos >= config_podal.FRAMES_AUSENCIA_FIN_PASADA:
                        self._procesar_pasada(buffer_frames)
                        buffer_frames = []

                espera = intervalo - (time.time() - t0)
                if espera > 0:
                    time.sleep(espera)
        finally:
            cap_marcha.release()
            if cap_posicion is not None:
                cap_posicion.release()

    def _procesar_pasada(self, frames: list):
        try:
            metricas = podal_vision.procesar_secuencia_marcha(frames)
            if metricas is None:
                return
            resultado = podal_vision.score_renguera(metricas)
            ts_evento = datetime.datetime.now()
            resuelto = resolver_rp(self.tambo, ts_evento, self.cfg["tolerancia_seg"])
            guardar_lectura(
                self.tambo, ts_evento, resultado,
                rp=resuelto["rp"] if resuelto else None,
                plaza=resuelto["plaza"] if resuelto else None)
        except Exception as exc:  # noqa: BLE001
            self.ultimo_error = str(exc)


_capturas: dict[str, _CapturaTambo] = {}
_capturas_lock = threading.Lock()


def iniciar(tambo: str) -> dict:
    cfg = config_podal.config_de(tambo)
    if not cfg["habilitado"]:
        return {"ok": False, "mensaje": "El tambo no tiene cámaras configuradas (ver config_podal.py)."}
    with _capturas_lock:
        captura = _capturas.get(tambo)
        if captura is None or not captura.activa():
            captura = _CapturaTambo(tambo, cfg)
            _capturas[tambo] = captura
        captura.iniciar()
    return {"ok": True}


def detener(tambo: str) -> dict:
    with _capturas_lock:
        captura = _capturas.get(tambo)
    if captura:
        captura.detener()
    return {"ok": True}


def estado(tambo: str) -> dict:
    cfg = config_podal.config_de(tambo)
    with _capturas_lock:
        captura = _capturas.get(tambo)
    return {
        "habilitado": cfg["habilitado"],
        "activa": bool(captura and captura.activa()),
        "camara_marcha_conectada": bool(captura and captura.conectada_marcha),
        "camara_posicion_conectada": bool(captura and captura.conectada_posicion),
        "ultimo_error": captura.ultimo_error if captura else None,
    }
