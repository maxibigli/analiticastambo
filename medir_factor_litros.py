"""Mide, SOLO LECTURA, cuántos litros de Milky salen por cada kg de DelPro.

Sirve para fijar el factor kg -> litros del menú "Balance de leche". Corre donde
la app llega a DelPro y a Milky (SERVER-DELPRO). No imprime credenciales.

    python medir_factor_litros.py                      # desde 17/10/2026 hasta hoy
    python medir_factor_litros.py 2026-10-17 2026-11-07 [tambo]

Qué mira:
  * DelPro: AnimalDaily.TotalYield por día de ordeñe (identificadas + leche sin
    identificar, Number = 0, que también sale por el tanque).
  * Milky: litros de TODAS las cargas del día (venta + guachera), porque todas
    salen del mismo tanque.
  * Se excluye el día en curso (parcial).

El cociente Milky/DelPro mezcla la conversión real kg->litros con las pérdidas
reales (leche que no pasa por el tanque, retenida, descartada). Por eso se mira
día por día, en ventanas móviles y el desfase de día entre ordeñe y carga.
"""
import datetime
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cicla  # noqa: E402
import db  # noqa: E402
from tambos import DEFAULT_TAMBO  # noqa: E402

hoy = datetime.date.today()
desde = datetime.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else datetime.date(2026, 10, 17)
hasta = datetime.date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else hoy
tambo = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_TAMBO

r = db.run_query(f"""
    SELECT CAST(ad.Date AS date) AS fecha,
           SUM(CASE WHEN b.Number > 0 THEN ad.TotalYield ELSE 0 END) AS kg_ident,
           SUM(CASE WHEN b.Number = 0 THEN ad.TotalYield ELSE 0 END) AS kg_desc,
           COUNT(DISTINCT CASE WHEN b.Number > 0 THEN b.OID END) AS vacas
    FROM AnimalDaily ad JOIN BasicAnimal b ON b.OID = ad.BasicAnimal
    WHERE ad.GCRecord IS NULL AND ad.IsYieldValid = 1 AND ad.TotalYield > 0
      AND ad.Date >= '{(desde - datetime.timedelta(days=1)).isoformat()}'
      AND ad.Date <= '{(hasta + datetime.timedelta(days=1)).isoformat()}'
    GROUP BY ad.Date ORDER BY ad.Date
""", tambo=tambo)
delpro = {}
for fecha, ki, kd, v in r["rows"]:
    ki, kd = float(ki or 0), float(kd or 0)
    delpro[str(fecha)[:10]] = {"kg": ki + kd, "kg_desc": kd, "vacas": v}

cargas, incompleto = cicla.obtener_cargas(desde, hasta, *cicla.credenciales())
print(f"Tambo DelPro: {tambo} | cargas Milky: {len(cargas)} (incompleto={incompleto}) | {desde} a {hasta}")
mk = {}
for c in cargas:
    dd, mm, aa = c["fecha"].split(" ")[0].split("/")
    m = mk.setdefault(f"{aa}-{mm}-{dd}", {"venta": 0.0, "otros": 0.0})
    m["venta" if c.get("es_venta") else "otros"] += c["lts_cicla"] or 0

dias = [d for d in sorted(set(delpro) & set(mk)) if d != hoy.isoformat()]
print("\nfecha       kg_DelPro  kg_sin_ident  Milky_venta  Milky_otros  Milky_total   L/kg  vacas")
filas = []
for d in dias:
    p, m = delpro[d], mk[d]
    tot = m["venta"] + m["otros"]
    filas.append((d, p["kg"], tot))
    print(f"{d}  {p['kg']:9.0f}  {p['kg_desc']:11.0f}  {m['venta']:11.0f}  {m['otros']:11.0f}  {tot:11.0f}  {tot / p['kg']:5.3f}  {p['vacas']}")

print("\nDías en DelPro sin Milky:", [d for d in sorted(delpro) if d not in mk and desde.isoformat() <= d <= hasta.isoformat()])
print("Días en Milky sin DelPro:", [d for d in sorted(mk) if d not in delpro])
if len(filas) < 3:
    sys.exit("Muy pocos días en común para medir.")

vals = [t / k for _, k, t in filas]
print(f"\nDÍA A DÍA ({len(vals)} días): mediana {st.median(vals):.4f}  media {st.mean(vals):.4f}  "
      f"desvío {st.pstdev(vals):.4f}  min {min(vals):.3f}  max {max(vals):.3f}")
print(f"ACUMULADO: {sum(t for *_, t in filas):,.0f} L / {sum(k for _, k, _ in filas):,.0f} kg = "
      f"{sum(t for *_, t in filas) / sum(k for _, k, _ in filas):.4f} L/kg")
for w in (3, 7):
    if len(filas) >= w:
        rs = [sum(f[2] for f in filas[i:i + w]) / sum(f[1] for f in filas[i:i + w])
              for i in range(len(filas) - w + 1)]
        print(f"ventana {w} días: mediana {st.median(rs):.4f}  desvío {st.pstdev(rs):.4f}  min {min(rs):.3f}  max {max(rs):.3f}")


def corr(a, b):
    ma, mb = st.mean(a), st.mean(b)
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (len(a) * st.pstdev(a) * st.pstdev(b))


for lag in (-1, 0, 1):
    a, b = [], []
    for d, k, _ in filas:
        d2 = (datetime.date.fromisoformat(d) + datetime.timedelta(days=lag)).isoformat()
        if d2 in mk and d2 != hoy.isoformat():
            a.append(k)
            b.append(mk[d2]["venta"] + mk[d2]["otros"])
    if len(a) >= 3:
        print(f"correlación DelPro(d) vs Milky(d{lag:+d}): {corr(a, b):.3f} (n={len(a)})")
