"""Revisa, SOLO LECTURA, si los tambos cargan los despachos de leche en DelPro.

Menú de DelPro "Leche > Despachos de leche": "Despachos de leche" (tabla MilkShipmentBase,
un registro por retiro del camión: fecha, turno, usina, litros, temperatura) y "Leche para uso
interno" (MilkForInternalUsage, litros por día que no salen en el camión).

Sirve para decidir si esos despachos pueden ser el "camión" del Balance de leche en tambos sin
caudalímetro de Milky o que no entregan a La Serenísima. Corre donde la app llega a DelPro
(SERVER-DELPRO). No imprime credenciales.

    python medir_despachos_delpro.py                    # todos los tambos configurados
    python medir_despachos_delpro.py ponderosa san_jose # solo esos

Por cada tambo muestra: cuántos despachos y usos internos hay, desde y hasta cuándo, litros por
mes, y, si hay despachos recientes, el cociente litros despachados / kg de sala por día (mismos
kg y mismo criterio de día cerrado que el Balance de leche).

OJO: MilkShipmentBase no tiene columna de rebaño. Si la base la comparten varios tambos (La
Ponderosa comparte DDM con otros dos), los despachos pueden incluir leche de los otros.
"""
import datetime
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import balance_leche  # noqa: E402
import db  # noqa: E402
import tambos  # noqa: E402

MESES = 6          # cuántos meses hacia atrás se listan
DIAS_RATIO = 120   # ventana (hasta el último despacho) para cruzar con la sala


def _consulta(sql, tambo):
    return db.run_query(sql, tambo=tambo)["rows"]


def _por_mes(tabla, col_fecha, col_litros, tambo):
    rows = _consulta(f"""
        SELECT FORMAT({col_fecha}, 'yyyy-MM') AS mes, COUNT(*) AS n, SUM({col_litros}) AS litros
        FROM {tabla} WHERE GCRecord IS NULL
        GROUP BY FORMAT({col_fecha}, 'yyyy-MM') ORDER BY 1 DESC
    """, tambo)
    return rows[:MESES]


def _resumen_tabla(tambo, tabla, col_fecha, col_litros):
    try:
        n, desde, hasta = _consulta(
            f"SELECT COUNT(*), MIN({col_fecha}), MAX({col_fecha}) FROM {tabla} WHERE GCRecord IS NULL", tambo)[0]
    except Exception as e:  # tabla ausente en esta base, o sin acceso
        print(f"  {tabla}: no se pudo leer ({str(e)[:90]})")
        return None
    if not n:
        print(f"  {tabla}: VACÍA (el tambo no carga esto en DelPro)")
        return None
    print(f"  {tabla}: {n} registros, del {str(desde)[:10]} al {str(hasta)[:10]}")
    for mes, cant, litros in sorted(_por_mes(tabla, col_fecha, col_litros, tambo)):
        print(f"      {mes}: {cant:4d} registros  {float(litros or 0):12,.0f} L")
    return str(hasta)[:10]


def _ratio_con_sala(tambo, hasta_iso):
    hasta = datetime.date.fromisoformat(hasta_iso)
    desde = hasta - datetime.timedelta(days=DIAS_RATIO)
    kg = {f["fecha"]: f for f in balance_leche.filas_kg(
        db.run_query(balance_leche.sql_kg_diarios(desde, hasta), tambo=tambo))}
    desp = {str(f)[:10]: float(l or 0) for f, l in _consulta(f"""
        SELECT CAST(DeliveryDate AS date), SUM(AmountOfMilk) FROM MilkShipmentBase
        WHERE GCRecord IS NULL AND DeliveryDate >= '{desde.isoformat()}' AND DeliveryDate < '{(hasta + datetime.timedelta(days=1)).isoformat()}'
        GROUP BY CAST(DeliveryDate AS date)""", tambo)}
    vacas = [v["vacas"] for v in kg.values() if v["vacas"] > 0]
    if not vacas:
        print("  Cruce con la sala: no hay producción de sala en ese período.")
        return
    if st.median(vacas) < 50:
        print(f"  Cruce con la sala: en ese período DelPro tiene solo {st.median(vacas):.0f} vacas con leche por día "
              "(mediana): la sala todavía no registraba la producción, no se puede medir el cociente.")
        return
    piso = balance_leche.PISO_VACAS * st.median(vacas)
    dias = [d for d in sorted(set(kg) & set(desp)) if kg[d]["vacas"] >= piso and kg[d]["vacas"] > 0]
    print(f"  Cruce con la sala ({desde} a {hasta}): {len(desp)} días con despacho, {len(kg)} con producción, "
          f"{len(dias)} comparables (día cerrado = {piso:.0f}+ vacas, mediana {st.median(vacas):.0f}).")
    if len(dias) < 3:
        print("  Muy pocos días en común para medir el cociente.")
        return
    cociente = [desp[d] / kg[d]["kg"] for d in dias]
    tot_l = sum(desp[d] for d in dias)
    tot_k = sum(kg[d]["kg"] for d in dias)
    print(f"  Litros despachados / kg de sala: acumulado {tot_l / tot_k:.4f} · día a día mediana {st.median(cociente):.4f} "
          f"desvío {st.pstdev(cociente):.4f} (min {min(cociente):.3f}, max {max(cociente):.3f})")
    for w in (3, 7):
        if len(dias) >= w:
            rs = [sum(desp[d] for d in dias[i:i + w]) / sum(kg[d]["kg"] for d in dias[i:i + w])
                  for i in range(len(dias) - w + 1)]
            print(f"      ventana {w} días: mediana {st.median(rs):.4f} desvío {st.pstdev(rs):.4f}")
    print("  (referencia: Milky / kg de sala = 1,02 en La Ponderosa; densidad típica 0,971)")


def main():
    ids = sys.argv[1:] or [t["id"] for t in tambos.lista()]
    for tid in ids:
        if not tambos.existe(tid):
            print(f"\n=== {tid}: no existe en tambos.py")
            continue
        print(f"\n=== {tid} ({tambos.nombre_de(tid)}) ===")
        try:
            ultimo = _resumen_tabla(tid, "MilkShipmentBase", "DeliveryDate", "AmountOfMilk")
            _resumen_tabla(tid, "MilkForInternalUsage", "EventDate", "InternalAmountOfMilk")
            if ultimo:
                _ratio_con_sala(tid, ultimo)
        except Exception as e:
            print(f"  No se pudo revisar este tambo: {str(e)[:160]}")


if __name__ == "__main__":
    main()
