# -*- coding: utf-8 -*-
"""Diagnostico de solo lectura: compara cuantos ordeños del comodin (RP 0)
ve `sql_identificacion` (la consulta que da el 96.91% real) contra cuantos
ve `sql_rutina` (la que alimenta "Rutina de ordeño" y da 100% siempre).

Correr DESDE la carpeta delpro-analitica, con el mismo Python que usa
servidor.py:

    python diagnostico_identificacion.py lamartina_local 2026-08-19

No escribe nada en la base (solo SELECT). Imprime:
  - cuantas filas trae CADA consulta en total
  - cuantas de esas filas tienen BasicAnimal.Number = 0
  - si sql_rutina trae MENOS filas totales que sql_identificacion para el
    mismo dia, o si trae las mismas filas pero con MENOS Number=0, eso ya
    dice si el problema esta en el JOIN/WHERE de sql_rutina o en el codigo
    Python que arma las sesiones (rutina.analizar_dia).

Archivo temporal de diagnostico -- se puede borrar despues de usarlo.
"""
import sys

import db
import salas

if len(sys.argv) < 3:
    print("Uso: python diagnostico_identificacion.py <tambo> <fecha AAAA-MM-DD>")
    sys.exit(1)

tambo, fecha = sys.argv[1], sys.argv[2]
sala = salas.de(tambo)

print(f"--- {tambo} / {fecha} ---")

# 1) sql_identificacion: la consulta que da el numero "real" (96.91%)
data_id = db.run_query(sala.sql_identificacion(fecha, fecha), tambo=tambo)
cols = data_id["columns"]
for r in data_id["rows"]:
    print("sql_identificacion:", dict(zip(cols, r)))
if not data_id["rows"]:
    print("sql_identificacion: SIN FILAS para esta fecha")

# 2) sql_rutina: la consulta que alimenta "Rutina de ordeño" (analizar_dia)
data_rut = db.run_query(sala.sql_rutina(fecha), tambo=tambo, max_rows=200000)
cols2 = data_rut["columns"]
idx2 = {c: i for i, c in enumerate(cols2)}
total = len(data_rut["rows"])
comodin = sum(1 for r in data_rut["rows"] if r[idx2["rp"]] == 0)
sin_grupo = sum(1 for r in data_rut["rows"] if r[idx2["grupo"]] is None)
comodin_sin_grupo = sum(1 for r in data_rut["rows"]
                         if r[idx2["rp"]] == 0 and r[idx2["grupo"]] is None)
print(f"sql_rutina: total filas={total}  truncated={data_rut['truncated']}")
print(f"  rp == 0 (comodin): {comodin}")
print(f"  grupo IS NULL (sin rodeo): {sin_grupo}")
print(f"  rp == 0 Y grupo IS NULL: {comodin_sin_grupo}")

# 3) Conteo directo, bypaseando sql_rutina y sql_identificacion enteras.
data_raw = db.run_query(f"""
    SELECT COUNT(*) AS total,
           SUM(CASE WHEN b.Number = 0 THEN 1 ELSE 0 END) AS comodin
    FROM SessionMilkYield y
    JOIN SessionMilkYieldEx ex ON ex.OID = y.OID
    JOIN BasicAnimal b ON b.OID = y.BasicAnimal
    WHERE CAST(y.BeginTime AS date) = '{fecha}'
""", tambo=tambo)
print("conteo directo (sin pasar por ninguna funcion de la app):", data_raw["rows"])
