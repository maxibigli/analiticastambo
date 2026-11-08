"""SIMULACIÓN de despachos de leche cargados en DelPro, con la forma que tenían en San José.

Es solo para pruebas y demostraciones: genera datos FALSOS a partir de los kilos reales de sala de un
tambo y nunca se escribe en la base de DelPro ni se usa en la app. Sirve para probar el modo "camión
desde DelPro" del Balance de leche (tambos sin caudalímetro de Milky o que no entregan a La Serenísima)
mientras ningún tambo tenga despachos recientes cargados.

Cómo eran los de San José (copia local `san_jose`, jul-oct 2024, medido con `medir_despachos_delpro.py`):
  * 2 despachos por día, turno "Tarde" (≈57 % del día) y "Noche" (≈43 %), a una usina ("San Francisco").
  * Litros que se tipean del remito: de 6.000 a 9.100 por despacho; temperatura ~4 °C.
  * Los campos de calidad que informa la usina (grasa, proteína, RCS...) vienen en 0 = sin dato.
  * Leche para uso interno: un registro por día, de 720 a 800 L (≈ 5 % de lo producido), de a 20 L.

No hay una medición real de la relación kg de sala -> litros despachados en San José (sus despachos
terminan antes de que la sala registre producción), así que `factor_real` es un SUPUESTO de la
simulación: con el mismo factor que se usa en el Balance, un día normal da ≈ 0 %.
"""
import datetime
import random

import despachos_delpro

TARDE, NOCHE = 0.57, 0.43


def simular_despachos(kg_por_dia: dict, factor_real: float = 1.02, uso_pct: float = 0.05, ruido: float = 0.004,
                      perdidas: dict = None, corridas: dict = None, semilla: int = 7):
    """-> (cargas, uso_interno), con la misma forma que `despachos_delpro.obtener_cargas` y `.uso_interno`.

    kg_por_dia  {"AAAA-MM-DD": kg de sala}.
    factor_real litros que salen de la sala por kg de DelPro (supuesto de la simulación).
    perdidas    {"AAAA-MM-DD": -6.0}: ese día se despachó 6 % menos de lo que salió de la sala.
    corridas    {"AAAA-MM-DD": 4500.0}: esos litros se cargaron en el remito del día SIGUIENTE.
    """
    rng = random.Random(semilla)
    perdidas, corridas = perdidas or {}, corridas or {}
    despachado, uso = {}, {}
    for fecha in sorted(kg_por_dia):
        litros = kg_por_dia[fecha] * factor_real
        u = round(litros * uso_pct / 20) * 20          # de a 20 L, como 720 / 800 en San José
        uso[fecha] = float(u)
        d = (litros - u) * (1 + rng.gauss(0, ruido)) * (1 + perdidas.get(fecha, 0.0) / 100)
        despachado[fecha] = d
    for fecha, litros in corridas.items():
        siguiente = (datetime.date.fromisoformat(fecha) + datetime.timedelta(days=1)).isoformat()
        despachado[fecha] -= litros
        despachado[siguiente] = despachado.get(siguiente, 0.0) + litros

    cargas, remito = [], 0
    for fecha in sorted(despachado):
        dia = datetime.date.fromisoformat(fecha)
        tarde = round(despachado[fecha] * TARDE)
        for turno, litros, hora in (("Tarde", tarde, 16), ("Noche", round(despachado[fecha]) - tarde, 16)):
            remito += 1
            cargas.append(despachos_delpro.carga_desde_fila({
                "fecha": datetime.datetime(dia.year, dia.month, dia.day, hora, 20 + (turno == "Noche")),
                "remito": remito, "tanque": None, "camion": turno, "usina": "San Francisco",
                "litros": float(litros), "temperatura": 4.0 + rng.choice((0.0, 0.0, 0.2)),
                "grasa": 0.0, "proteina": 0.0, "rcs": 0, "dcc": 0, "cbt": 0, "mun": 0.0,
                "acidez": 0.0, "sangre": 0, "inhibidores": 0}))
    uso_interno = [{"fecha": despachos_delpro._fecha_ar(datetime.datetime.fromisoformat(f + "T08:50:00")),
                    "litros": u, "uso": 0, "uso_label": "Uso 0"} for f, u in sorted(uso.items())]
    return cargas, uso_interno
