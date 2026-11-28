# -*- coding: utf-8 -*-
"""Vigilat: lo que tiene que sobrevivir a un reinicio. Vive en la misma base que el Vigilante anterior
(`vigilante.db`, fuera de git), en tablas propias `vt_*`, así el dedup y los avisos retenidos que ya existían siguen
valiendo al actualizar.

    vt_aviso   cada cosa que Vigilat detectó y decidió avisar (con su escalera de confirmación)
    vt_envio   un renglón por persona y canal: pendiente, enviado, fallido…, con su llave para confirmar
    vt_evento  la historia de cada aviso, en orden (lo mismo que se anota en la Bitácora)
"""
import datetime
import json
import secrets

import vigilante_estado

ABIERTOS = ("pendiente", "sin_confirmar")          # un aviso que todavía se puede confirmar
_CAMPOS_AVISO = ("creado", "regla_id", "regla_nombre", "tipo", "prioridad", "texto", "audio", "urgente",
                 "requiere_conf", "espera_min", "escalera", "nivel", "vence", "estado")
_EDITABLES = ("nivel", "vence", "estado", "bitacora_id")


def _iso(momento: datetime.datetime) -> str:
    return momento.isoformat(timespec="seconds")


class Estado(vigilante_estado.Estado):
    def __init__(self, ruta: str | None = None):
        super().__init__(ruta or vigilante_estado.RUTA_DB)
        with self._con() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS vt_aviso (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, creado TEXT NOT NULL, regla_id TEXT NOT NULL,
                    regla_nombre TEXT NOT NULL, tipo TEXT NOT NULL, prioridad INTEGER NOT NULL, texto TEXT NOT NULL,
                    audio TEXT NOT NULL, urgente INTEGER NOT NULL, requiere_conf INTEGER NOT NULL,
                    espera_min INTEGER NOT NULL, escalera TEXT NOT NULL, nivel INTEGER NOT NULL, vence TEXT,
                    estado TEXT NOT NULL, confirmado_por TEXT, confirmado_en TEXT, confirmado_via TEXT,
                    bitacora_id INTEGER);
                CREATE TABLE IF NOT EXISTS vt_envio (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, aviso_id INTEGER NOT NULL, persona_id TEXT NOT NULL,
                    persona TEXT NOT NULL, canal TEXT NOT NULL, nivel INTEGER NOT NULL, token TEXT NOT NULL UNIQUE,
                    creado TEXT NOT NULL, estado TEXT NOT NULL, intentos INTEGER NOT NULL DEFAULT 0, proximo TEXT,
                    enviado_en TEXT, lote TEXT, exento INTEGER NOT NULL DEFAULT 0, error TEXT);
                CREATE TABLE IF NOT EXISTS vt_evento (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, aviso_id INTEGER NOT NULL, cuando TEXT NOT NULL,
                    texto TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS ix_vt_envio_estado ON vt_envio(estado);
                CREATE INDEX IF NOT EXISTS ix_vt_envio_aviso ON vt_envio(aviso_id);
                CREATE INDEX IF NOT EXISTS ix_vt_aviso_creado ON vt_aviso(creado);
            """)

    def _filas(self, sql: str, args=()) -> list:
        with self._con() as c:
            cur = c.execute(sql, args)
            nombres = [d[0] for d in cur.description]
            return [dict(zip(nombres, f)) for f in cur.fetchall()]

    # ----------------------------------------------------------------- clave/valor (estado de cada regla)
    def leer_valor(self, clave: str):
        with self._con() as c:
            f = c.execute("SELECT valor FROM kv WHERE clave = ?", (clave,)).fetchone()
        if not f:
            return None
        try:
            return json.loads(f[0])
        except ValueError:
            return None

    def guardar_valor(self, clave: str, valor) -> None:
        with self._con() as c:
            c.execute("INSERT OR REPLACE INTO kv (clave, valor) VALUES (?, ?)", (clave, json.dumps(valor)))

    # ----------------------------------------------------------------- avisos
    def crear_aviso(self, datos: dict, claves=()) -> int:
        """El aviso y sus claves de dedup entran en UNA transacción: o quedan los dos, o ninguno."""
        fila = {**datos, "escalera": json.dumps(datos["escalera"], ensure_ascii=False)}
        with self._con() as c:
            cur = c.execute(f"INSERT INTO vt_aviso ({', '.join(_CAMPOS_AVISO)}) VALUES "
                            f"({', '.join('?' * len(_CAMPOS_AVISO))})", [fila[k] for k in _CAMPOS_AVISO])
            for clave in claves:
                c.execute("INSERT OR IGNORE INTO avisos (clave, cuando) VALUES (?, ?)", (clave, fila["creado"]))
            return cur.lastrowid

    def aviso(self, aviso_id: int) -> dict | None:
        filas = self._filas("SELECT * FROM vt_aviso WHERE id = ?", (aviso_id,))
        if not filas:
            return None
        filas[0]["escalera"] = json.loads(filas[0]["escalera"])
        return filas[0]

    def editar_aviso(self, aviso_id: int, **campos) -> None:
        if not campos or any(k not in _EDITABLES for k in campos):
            raise ValueError(f"Campos no editables: {sorted(campos)}")
        with self._con() as c:
            c.execute(f"UPDATE vt_aviso SET {', '.join(f'{k} = ?' for k in campos)} WHERE id = ?",
                      [*campos.values(), aviso_id])

    def esperando_confirmacion(self) -> list:
        ids = [f["id"] for f in self._filas("SELECT id FROM vt_aviso WHERE estado = 'pendiente' ORDER BY id")]
        return [self.aviso(i) for i in ids]

    def avisos_abiertos_de_regla(self, regla_id: str) -> list:
        return self._filas("SELECT id FROM vt_aviso WHERE regla_id = ? AND estado IN ('pendiente','sin_confirmar') "
                           "ORDER BY id", (regla_id,))

    def confirmar(self, aviso_id: int, quien: str, via: str, ahora: datetime.datetime) -> bool:
        """Marca confirmado un aviso que lo esperaba y anula lo que faltaba mandar de él. `False` si no existe o ya no
        espera confirmación (la primera confirmación es la que vale)."""
        with self._con() as c:
            n = c.execute("UPDATE vt_aviso SET estado = 'confirmado', confirmado_por = ?, confirmado_en = ?, "
                          "confirmado_via = ?, vence = NULL WHERE id = ? AND estado IN ('pendiente','sin_confirmar')",
                          (quien, _iso(ahora), via, aviso_id)).rowcount
            if n:
                c.execute("UPDATE vt_envio SET estado = 'anulado' WHERE aviso_id = ? AND estado = 'pendiente'",
                          (aviso_id,))
        return bool(n)

    def resolver_solo(self, aviso_id: int) -> bool:
        """El problema se arregló sin que nadie confirmara (volvieron los datos): se deja de escalar."""
        with self._con() as c:
            n = c.execute("UPDATE vt_aviso SET estado = 'resuelto', vence = NULL WHERE id = ? AND estado IN "
                          "('pendiente','sin_confirmar')", (aviso_id,)).rowcount
            if n:
                c.execute("UPDATE vt_envio SET estado = 'anulado' WHERE aviso_id = ? AND estado = 'pendiente'",
                          (aviso_id,))
        return bool(n)

    # ----------------------------------------------------------------- envíos
    def agregar_envio(self, aviso_id: int, persona_id: str, persona: str, canal: str, nivel: int, exento: bool,
                      ahora: datetime.datetime, estado: str = "pendiente", error: str | None = None) -> int:
        with self._con() as c:
            return c.execute(
                "INSERT INTO vt_envio (aviso_id, persona_id, persona, canal, nivel, token, creado, estado, exento, error)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (aviso_id, persona_id, persona, canal, nivel, secrets.token_urlsafe(16), _iso(ahora), estado,
                 int(exento), error)).lastrowid

    def envios_pendientes(self, ahora: datetime.datetime) -> list:
        """Lo que toca mandar ahora, con los datos de su aviso, en orden de prioridad."""
        return self._filas(
            "SELECT e.*, a.prioridad, a.urgente, a.texto, a.requiere_conf, a.estado AS estado_aviso, a.espera_min, "
            "a.nivel AS nivel_aviso, a.vence FROM vt_envio e JOIN vt_aviso a ON a.id = e.aviso_id "
            "WHERE e.estado = 'pendiente' AND (e.proximo IS NULL OR e.proximo <= ?) "
            "ORDER BY a.prioridad, a.id, e.id", (_iso(ahora),))

    def envios_de(self, aviso_id: int) -> list:
        return self._filas("SELECT * FROM vt_envio WHERE aviso_id = ? ORDER BY id", (aviso_id,))

    def marcar_envio(self, envio_id: int, estado: str, ahora: datetime.datetime | None = None, lote: str | None = None,
                     error: str | None = None, proximo: datetime.datetime | None = None, intentos: int | None = None):
        """Solo cambia un envío que sigue pendiente: si mientras se mandaba alguien confirmó el aviso (quedó
        «anulado») y el mensaje igual salió, se anota como enviado; cualquier otra marca no lo resucita."""
        permitido = "('pendiente','anulado')" if estado == "enviado" else "('pendiente')"
        with self._con() as c:
            c.execute(f"UPDATE vt_envio SET estado = ?, enviado_en = ?, lote = COALESCE(?, lote), error = ?, proximo = ?,"
                      f" intentos = COALESCE(?, intentos) WHERE id = ? AND estado IN {permitido}",
                      (estado, _iso(ahora) if ahora and estado == "enviado" else None, lote, error,
                       _iso(proximo) if proximo else None, intentos, envio_id))

    def envio_por_token(self, token: str) -> dict | None:
        filas = self._filas("SELECT * FROM vt_envio WHERE token = ?", (token,))
        return filas[0] if filas else None

    def mensajes_del_dia(self, persona_id: str, dia: datetime.date) -> int:
        """Cuántos mensajes (de los que cuentan para el máximo) ya recibió esa persona en el día."""
        with self._con() as c:
            return c.execute("SELECT COUNT(DISTINCT lote) FROM vt_envio WHERE persona_id = ? AND estado = 'enviado' "
                             "AND exento = 0 AND substr(enviado_en, 1, 10) = ?",
                             (persona_id, dia.isoformat())).fetchone()[0]

    def pendientes_de_persona(self, persona_id: str) -> list:
        """Los avisos que esperan confirmación y le llegaron a esa persona, del más nuevo al más viejo."""
        return [f["aviso_id"] for f in self._filas(
            "SELECT DISTINCT e.aviso_id FROM vt_envio e JOIN vt_aviso a ON a.id = e.aviso_id WHERE e.persona_id = ? "
            "AND e.estado = 'enviado' AND a.estado IN ('pendiente','sin_confirmar') ORDER BY e.aviso_id DESC",
            (persona_id,))]

    # ----------------------------------------------------------------- historia
    def evento(self, aviso_id: int, texto: str, ahora: datetime.datetime) -> None:
        with self._con() as c:
            c.execute("INSERT INTO vt_evento (aviso_id, cuando, texto) VALUES (?, ?, ?)", (aviso_id, _iso(ahora), texto))

    def eventos_de(self, aviso_id: int) -> list:
        return self._filas("SELECT cuando, texto FROM vt_evento WHERE aviso_id = ? ORDER BY id", (aviso_id,))

    def historial(self, desde: datetime.date, hasta: datetime.date, limite: int = 500) -> list:
        """Los avisos creados entre dos fechas (inclusive), del más nuevo al más viejo, con sus envíos y su historia."""
        ids = [f["id"] for f in self._filas(
            "SELECT id FROM vt_aviso WHERE substr(creado, 1, 10) BETWEEN ? AND ? ORDER BY id DESC LIMIT ?",
            (desde.isoformat(), hasta.isoformat(), limite))]
        salida = []
        for i in ids:
            a = self.aviso(i)
            a["envios"] = self.envios_de(i)
            a["eventos"] = self.eventos_de(i)
            salida.append(a)
        return salida
