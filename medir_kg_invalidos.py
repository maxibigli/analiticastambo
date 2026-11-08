"""Mide, SOLO LECTURA, cuántos kilos de DelPro quedan afuera del Balance de leche.

Responde: ¿la diferencia de ~8 % entre DelPro (kg x 0,971 por densidad) y lo que miden
Milky y el comprador sale de ordeños que DelPro registra pero el Balance descarta?
Corre donde la app llega a DelPro y a Milky (SERVER-DELPRO). No imprime credenciales.

    python medir_kg_invalidos.py                      # desde 17/10/2026 hasta hoy
    python medir_kg_invalidos.py 2026-10-17 2026-11-07 [tambo]

Qué separa, por día de ordeñe (AnimalDaily, TotalYield > 0):
  * kg_ident    vacas identificadas con lectura válida     (entra al Balance)
  * kg_desc     leche sin identificar (Number = 0), válida  (entra al Balance)
  * kg_invalido lectura NO válida (IsYieldValid distinto de 1) (el Balance lo descarta)
  * n_inval     cuántas filas de ese tipo hay

Después compara contra los litros de Milky del mismo día y calcula el cociente con y sin
los kg inválidos. Si al sumarlos el cociente se acerca a 0,971 (densidad ~1,03 kg/L), el
faltante de DelPro está en esas lecturas. Si no se mueve, la causa es otra.
Solo cuentan los días cerrados (mismo criterio que balance_leche.PISO_VACAS).
"""
import datetime
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import balance_leche  # noqa: E402
import cicla  # noqa: E402
import db  # noqa: E402
from tambos import DEFAULT_TAMBO  # noqa: E402


def sql_desglose(desde: datetime.date, hasta: datetime.date) -> str:
    una = (desde - datetime.timedelta(days=1)).isoformat()
    otra = (hasta + datetime.timedelta(days=1)).isoformat()
    return f"""
    SELECT CAST(ad.Date AS date) AS fecha,
           SUM(CASE WHEN ad.IsYieldValid = 1 AND b.Number > 0 THEN ad.TotalYield ELSE 0 END) AS kg_ident,
           SUM(CASE WHEN ad.IsYieldValid = 1 AND b.Number = 0 THEN ad.TotalYield ELSE 0 END) AS kg_desc,
           SUM(CASE WHEN ISNULL(ad.IsYieldValid, 0) <> 1 THEN ad.TotalYield ELSE 0 END) AS kg_invalido,
           SUM(CASE WHEN ISNULL(ad.IsYieldValid, 0) <> 1 THEN 1 ELSE 0 END) AS n_inval,
           COUNT(DISTINCT CASE WHEN ad.IsYieldValid = 1 AND b.Number > 0 THEN b.OID END) AS vacas
    FROM AnimalDaily ad JOIN BasicAnimal b ON b.OID = ad.BasicAnimal
    WHERE ad.GCRecord IS NULL AND ad.TotalYield > 0
      AND ad.Date >= '{una}' AND ad.Date <= '{otra}'
    GROUP BY ad.Date ORDER BY ad.Date
    OPTION (MAXDOP 1, MAX_GRANT_PERCENT = 15)
    """


def leer_desglose(desde, hasta, tambo):
    r = db.run_query(sql_desglose(desde, hasta), tambo=tambo)
    out = {}
    for fecha, ki, kd, kv, n, v in r["rows"]:
        out[str(fecha)[:10]] = {"kg_ident": float(ki or 0), "kg_desc": float(kd or 0),
                                "kg_inval": float(kv or 0), "n_inval": int(n or 0), "vacas": int(v or 0)}
    return out


def main():
    hoy = datetime.date.today()
    desde = datetime.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else datetime.date(2026, 10, 17)
    hasta = datetime.date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else hoy
    tambo = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_TAMBO

    delpro = leer_desglose(desde, hasta, tambo)
    cargas, incompleto = cicla.obtener_cargas(desde, hasta, *cicla.credenciales())
    print(f"Tambo DelPro: {tambo} | cargas Milky: {len(cargas)} (incompleto={incompleto}) | {desde} a {hasta}")
    mk = {}
    for c in cargas:
        dd, mm, aa = c["fecha"].split(" ")[0].split("/")
        k = f"{aa}-{mm}-{dd}"
        mk[k] = mk.get(k, 0.0) + (c["lts_cicla"] or 0)

    vacas = [v["vacas"] for v in delpro.values() if v["vacas"] > 0]
    piso = balance_leche.PISO_VACAS * st.median(vacas) if vacas else 0
    print(f"Día cerrado = al menos {piso:.0f} vacas ({balance_leche.PISO_VACAS:.0%} de la mediana).")
    dias = [d for d in sorted(set(delpro) & set(mk))
            if delpro[d]["vacas"] >= piso and delpro[d]["vacas"] > 0 and desde.isoformat() <= d <= hasta.isoformat()]
    if len(dias) < 3:
        sys.exit("Muy pocos días en común para medir.")

    print("\nfecha       kg_ident  kg_desc  kg_inval  n_inval  Milky_L   L/kg(Balance)  L/kg(con inval.)")
    filas = []
    for d in dias:
        p, m = delpro[d], mk[d]
        base = p["kg_ident"] + p["kg_desc"]
        todo = base + p["kg_inval"]
        filas.append((d, base, todo, m, p))
        print(f"{d}  {p['kg_ident']:8.0f} {p['kg_desc']:8.0f} {p['kg_inval']:9.0f} {p['n_inval']:8d} {m:8.0f}"
              f"  {m / base:12.3f}  {m / todo:14.3f}")

    kb = sum(f[1] for f in filas)
    kt = sum(f[2] for f in filas)
    lm = sum(f[3] for f in filas)
    ki = sum(f[4]["kg_inval"] for f in filas)
    kd = sum(f[4]["kg_desc"] for f in filas)
    ni = sum(f[4]["n_inval"] for f in filas)
    print(f"\n{len(filas)} días cerrados.")
    print(f"Kg que entran al Balance:        {kb:12,.0f}   (de ellos sin identificar: {kd:,.0f} = {kd / kb:.2%})")
    print(f"Kg con lectura NO válida:         {ki:12,.0f}   ({ni} filas) = {ki / kb:.2%} sobre lo que entra")
    print(f"Litros de Milky:                  {lm:12,.0f}")
    print(f"L/kg actual (lo que usa el Balance): {lm / kb:.4f}")
    print(f"L/kg sumando los inválidos:          {lm / kt:.4f}")
    print(f"Referencia por densidad (1/1,03):    {1 / 1.03:.4f}")
    esperados = lm / balance_leche.FACTOR_DENSIDAD_TIPICO  # kg que corresponden a esos litros por densidad
    faltante = esperados - kb
    print(f"\nKg esperados por densidad para los litros de Milky: {esperados:,.0f}")
    print(f"Faltante de DelPro: {faltante:,.0f} kg ({faltante / esperados:.2%} de lo esperado).")
    if faltante > 0:
        print(f"Los kg inválidos explican {min(ki / faltante, 1):.0%} de ese faltante.")


if __name__ == "__main__":
    main()
