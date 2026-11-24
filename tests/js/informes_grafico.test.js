// Pruebas del módulo del gráfico de informes (static/js/informes_grafico.js): la matemática que arma las series, los intervalos y las
// estadísticas, y el dibujo. Se corre con Node (lo lanza tests/test_informes_grafico_js.py).
//
//     node tests/js/informes_grafico.test.js
'use strict';
const assert = require('assert');
const G = require('../../static/js/informes_grafico.js');

let n = 0;
function prueba(nombre, fn) {
  try { fn(); n++; } catch (e) { console.error('FALLÓ: ' + nombre + '\n' + (e && e.stack || e)); process.exitCode = 1; }
}
const cerca = (a, b, eps) => assert.ok(Math.abs(a - b) <= (eps || 1e-9), a + ' no está cerca de ' + b);

// ---- intervalos -------------------------------------------------------------------------------------------------------------------------
prueba('claveIntervalo: día es el mismo día', () => assert.strictEqual(G.claveIntervalo('2026-07-30', 'dia'), '2026-07-30'));
prueba('claveIntervalo: la semana empieza el lunes', () => {
  assert.strictEqual(G.claveIntervalo('2026-07-27', 'semana'), '2026-07-27');   // lunes
  assert.strictEqual(G.claveIntervalo('2026-07-30', 'semana'), '2026-07-27');   // jueves
  assert.strictEqual(G.claveIntervalo('2026-08-02', 'semana'), '2026-07-27');   // domingo: es del lunes anterior
  assert.strictEqual(G.claveIntervalo('2026-08-03', 'semana'), '2026-08-03');
});
prueba('claveIntervalo: la semana cruza el fin de año', () => {
  assert.strictEqual(G.claveIntervalo('2026-01-01', 'semana'), '2025-12-29');   // jueves
  assert.strictEqual(G.claveIntervalo('2024-03-01', 'semana'), '2024-02-26');   // año bisiesto
});
prueba('claveIntervalo: el mes empieza el 1', () => {
  assert.strictEqual(G.claveIntervalo('2026-07-30', 'mes'), '2026-07-01');
  assert.strictEqual(G.claveIntervalo('2026-12-31', 'mes'), '2026-12-01');
});

// ---- agregar ----------------------------------------------------------------------------------------------------------------------------
prueba('agregar: los cuatro resúmenes', () => {
  const v = [10, 20, 60];
  cerca(G.agregar(v, 'avg'), 30); assert.strictEqual(G.agregar(v, 'sum'), 90);
  assert.strictEqual(G.agregar(v, 'min'), 10); assert.strictEqual(G.agregar(v, 'max'), 60);
});
prueba('agregar: ignora los vacíos y devuelve null si no hay nada', () => {
  cerca(G.agregar([null, 10, undefined, 20, NaN], 'avg'), 15);
  assert.strictEqual(G.agregar([null, null], 'sum'), null);
  assert.strictEqual(G.agregar([], 'avg'), null);
  cerca(G.agregar([0, 10], 'avg'), 5);                                           // un cero es un dato, no un vacío
});

// ---- valor de un día --------------------------------------------------------------------------------------------------------------------
const serie1 = {id: 'a', etiqueta: 'Rodeo 1', vacas: [100, 0, null, 50], kg_valido: [4000, 0, null, 2100], kg: [4100, 80, null, 2200]};
prueba('valorDiario: kg por vaca usa solo lo válido', () => {
  cerca(G.valorDiario('kg_vaca', serie1, 0), 40);
  cerca(G.valorDiario('kg_vaca', serie1, 3), 42);
});
prueba('valorDiario: sin vacas válidas no hay kg por vaca (no se divide por cero)', () => {
  assert.strictEqual(G.valorDiario('kg_vaca', serie1, 1), null);
  assert.strictEqual(G.valorDiario('kg_vaca', serie1, 2), null);
});
prueba('valorDiario: el total incluye toda la leche medida y las vacas son las válidas', () => {
  assert.strictEqual(G.valorDiario('kg_total', serie1, 0), 4100);
  assert.strictEqual(G.valorDiario('kg_total', serie1, 1), 80);
  assert.strictEqual(G.valorDiario('vacas', serie1, 0), 100);
  assert.strictEqual(G.valorDiario('kg_total', serie1, 2), null);
});

// ---- modelo -----------------------------------------------------------------------------------------------------------------------------
function datos(extra) {
  // 14 días desde el lunes 2026-07-06; dos series; los dos primeros días y el último sin datos
  const fechas = []; for (let i = 0; i < 14; i++) fechas.push('2026-07-' + String(6 + i).padStart(2, '0'));
  const vacio = () => new Array(14).fill(null);
  const a = {id: '1', etiqueta: 'Rodeo 1', vacas: vacio(), kg_valido: vacio(), kg: vacio()};
  const b = {id: '2', etiqueta: 'Rodeo 2', vacas: vacio(), kg_valido: vacio(), kg: vacio()};
  for (let i = 2; i < 13; i++) { a.vacas[i] = 100; a.kg_valido[i] = 4000; a.kg[i] = 4100; b.vacas[i] = 50; b.kg_valido[i] = 2000 + i * 10; b.kg[i] = 2100 + i * 10; }
  return Object.assign({fechas, series: [a, b], incompletos: []}, extra || {});
}
prueba('armarModelo: recorta los días sin datos del principio y del final', () => {
  const m = G.armarModelo(datos(), {medida: 'kg_vaca', resumen: 'avg', intervalo: 'dia'});
  assert.strictEqual(m.claves.length, 11);
  assert.strictEqual(m.claves[0], '2026-07-08');
  assert.strictEqual(m.claves[10], '2026-07-18');
  assert.strictEqual(m.hayDatos, true);
});
prueba('armarModelo: por día calcula kg por vaca de cada serie', () => {
  const m = G.armarModelo(datos(), {medida: 'kg_vaca', resumen: 'avg', intervalo: 'dia'});
  cerca(m.series[0].vals[0], 40); cerca(m.series[1].vals[0], (2000 + 2 * 10) / 50);
  assert.deepStrictEqual(m.series.map(s => s.etiqueta), ['Rodeo 1', 'Rodeo 2']);
  assert.deepStrictEqual(m.series.map(s => s.ci), [0, 1]);
  assert.deepStrictEqual([m.unidad, m.decimales], ['kg/vaca/día', 1]);
});
prueba('armarModelo: por semana junta los días de cada lunes a domingo', () => {
  const m = G.armarModelo(datos(), {medida: 'kg_total', resumen: 'sum', intervalo: 'semana'});
  assert.deepStrictEqual(m.claves, ['2026-07-06', '2026-07-13']);               // los días 8 a 12 son de la primera semana, 13 a 18 de la segunda
  assert.strictEqual(m.series[0].vals[0], 5 * 4100);                              // 8, 9, 10, 11, 12 de julio
  assert.strictEqual(m.series[0].vals[1], 6 * 4100);                              // 13 a 18 de julio
});
prueba('armarModelo: por mes y con otro resumen', () => {
  const m = G.armarModelo(datos(), {medida: 'vacas', resumen: 'max', intervalo: 'mes'});
  assert.deepStrictEqual(m.claves, ['2026-07-01']);
  assert.strictEqual(m.series[0].vals[0], 100);
  assert.deepStrictEqual([m.unidad, m.decimales], ['vacas', 0]);
});
prueba('armarModelo: un día incompleto no cuenta (ni en el día ni en el promedio de la semana)', () => {
  const d = datos({incompletos: ['2026-07-10']});
  d.series[0].kg_valido[4] = 100; d.series[0].kg[4] = 100; d.series[0].vacas[4] = 3;             // un día roto
  const dia = G.armarModelo(d, {medida: 'kg_vaca', resumen: 'avg', intervalo: 'dia'});
  assert.strictEqual(dia.series[0].vals[2], null);                                            // 2026-07-10 es el tercero (8, 9, 10)
  const sem = G.armarModelo(d, {medida: 'kg_vaca', resumen: 'avg', intervalo: 'semana'});
  cerca(sem.series[0].vals[0], 40);                                                           // el promedio no se arrastra por el día roto
  assert.deepStrictEqual(dia.incompletos, [false, false, true, false, false, false, false, false, false, false, false]);
});
prueba('armarModelo: sin ningún dato', () => {
  const m = G.armarModelo({fechas: ['2026-07-01', '2026-07-02'], series: [], incompletos: []}, {medida: 'kg_vaca', resumen: 'avg', intervalo: 'dia'});
  assert.strictEqual(m.hayDatos, false);
  assert.deepStrictEqual(m.claves, []);
});

// ---- estadísticas -----------------------------------------------------------------------------------------------------------------------
prueba('estadisticas: último, promedio, mínimo, máximo y cambio del período', () => {
  const e = G.estadisticas([null, 40, 44, null, 42, 50, null]);
  assert.strictEqual(e.ultimo, 50); cerca(e.promedio, 44); assert.strictEqual(e.minimo, 40); assert.strictEqual(e.maximo, 50); cerca(e.cambio, 25);
});
prueba('estadisticas: sin datos es null y con un solo dato el cambio es 0', () => {
  assert.strictEqual(G.estadisticas([null, null]), null);
  assert.strictEqual(G.estadisticas([30]).cambio, 0);
  assert.strictEqual(G.estadisticas([0, 10]).cambio, null);                                       // desde cero no hay porcentaje
});

// ---- escala y etiquetas -----------------------------------------------------------------------------------------------------------------
prueba('escala: números redondos que contienen el rango', () => {
  const s = G.escala(0, 47, 5);
  assert.ok(s.lo <= 0 && s.hi >= 47, JSON.stringify(s));
  assert.ok([1, 2, 5, 10].includes(s.step / Math.pow(10, Math.floor(Math.log10(s.step)))), JSON.stringify(s));
  const t = G.escala(37.2, 43.9, 5);
  assert.ok(t.lo <= 37.2 && t.hi >= 43.9);
});
prueba('escala: todos los valores iguales no se rompe', () => {
  const s = G.escala(30, 30, 5);
  assert.ok(s.hi > s.lo && isFinite(s.step) && s.step > 0);
});
prueba('etiquetas de fecha', () => {
  assert.strictEqual(G.etiquetaX('2026-07-09', 'dia'), '09/07');
  assert.strictEqual(G.etiquetaX('2026-07-06', 'semana'), '06/07');
  assert.strictEqual(G.etiquetaX('2026-07-01', 'mes'), 'jul 26');
  assert.strictEqual(G.etiquetaTip('2026-07-30', 'dia'), 'jue 30/07/2026');
  assert.strictEqual(G.etiquetaTip('2026-07-27', 'semana'), 'Semana del lun 27/07/2026');
  assert.strictEqual(G.etiquetaTip('2026-07-01', 'mes'), 'jul 2026');
});

// ---- dibujo -----------------------------------------------------------------------------------------------------------------------------
const fmt = (v, d) => v.toFixed(d || 0);
function modelo(forma) {
  const m = G.armarModelo(datos(), {medida: 'kg_vaca', resumen: 'avg', intervalo: 'dia'});
  m.forma = forma;
  return m;
}
prueba('dibujar: una línea por serie visible', () => {
  const r = G.dibujar(modelo('linea'), {ancho: 700, forma: 'linea', ocultas: {}, fmt});
  assert.ok(r.svg.startsWith('<svg'));
  assert.strictEqual((r.svg.match(/class="draw"/g) || []).length, 2);
  assert.ok(r.geom && r.geom.n === 11);
});
prueba('dibujar: una serie oculta no se dibuja', () => {
  const r = G.dibujar(modelo('linea'), {ancho: 700, forma: 'linea', ocultas: {'2': true}, fmt});
  assert.strictEqual((r.svg.match(/class="draw"/g) || []).length, 1);
});
prueba('dibujar: todas ocultas o sin datos devuelve el motivo y no un dibujo', () => {
  assert.strictEqual(G.dibujar(modelo('linea'), {ancho: 700, forma: 'linea', ocultas: {'1': true, '2': true}, fmt}).vacio, 'series');
  const sin = G.armarModelo({fechas: ['2026-07-01'], series: [], incompletos: []}, {medida: 'kg_vaca', resumen: 'avg', intervalo: 'dia'});
  assert.strictEqual(G.dibujar(sin, {ancho: 700, forma: 'linea', ocultas: {}, fmt}).vacio, 'datos');
});
prueba('dibujar: barras, una por dato', () => {
  const r = G.dibujar(modelo('barras'), {ancho: 700, forma: 'barras', ocultas: {}, fmt});
  assert.strictEqual((r.svg.match(/<rect class="fade"/g) || []).length, 22);   // 11 días x 2 series
});
prueba('dibujar: un hueco corta la línea en dos tramos', () => {
  const d = datos(); d.series[0].vacas[6] = null; d.series[0].kg_valido[6] = null; d.series[0].kg[6] = null;
  const m = G.armarModelo(d, {medida: 'kg_vaca', resumen: 'avg', intervalo: 'dia'});
  const r = G.dibujar(m, {ancho: 700, forma: 'linea', ocultas: {'2': true}, fmt});
  const trazo = r.svg.match(/<path class="draw"[^>]* d="([^"]+)"/)[1];
  assert.strictEqual((trazo.match(/M/g) || []).length, 2);
});
prueba('dibujar: el área y la línea empiezan en cero, la línea sola se ajusta al rango', () => {
  const area = G.dibujar(modelo('area'), {ancho: 700, forma: 'area', ocultas: {}, fmt});
  const linea = G.dibujar(modelo('linea'), {ancho: 700, forma: 'linea', ocultas: {}, fmt});
  assert.ok(area.geom.escala.lo === 0);
  assert.ok(linea.geom.escala.lo > 0);
});
prueba('dibujar: marca los días incompletos solo en el intervalo diario', () => {
  const d = datos({incompletos: ['2026-07-10']});
  const dia = G.dibujar(G.armarModelo(d, {medida: 'kg_vaca', resumen: 'avg', intervalo: 'dia'}), {ancho: 700, forma: 'linea', ocultas: {}, fmt});
  assert.strictEqual((dia.svg.match(/class="inc"/g) || []).length, 1);
  const sem = G.dibujar(G.armarModelo(d, {medida: 'kg_vaca', resumen: 'avg', intervalo: 'semana'}), {ancho: 700, forma: 'linea', ocultas: {}, fmt});
  assert.strictEqual((sem.svg.match(/class="inc"/g) || []).length, 0);
});
prueba('indiceEn: de la posición del puntero al dato más cercano', () => {
  const r = G.dibujar(modelo('linea'), {ancho: 700, forma: 'linea', ocultas: {}, fmt});
  assert.strictEqual(G.indiceEn(-20, r.geom), 0);
  assert.strictEqual(G.indiceEn(r.geom.iw + 50, r.geom), 10);
  assert.strictEqual(G.indiceEn(r.geom.iw / 2, r.geom), 5);
  const b = G.dibujar(modelo('barras'), {ancho: 700, forma: 'barras', ocultas: {}, fmt});
  assert.strictEqual(G.indiceEn(1, b.geom), 0);
});
prueba('el dibujo no mete texto del usuario sin escapar (las etiquetas de los ejes son fechas y números)', () => {
  const d = datos(); d.series[0].etiqueta = '<img src=x onerror=alert(1)>';
  const r = G.dibujar(G.armarModelo(d, {medida: 'kg_vaca', resumen: 'avg', intervalo: 'dia'}), {ancho: 700, forma: 'linea', ocultas: {}, fmt});
  assert.ok(r.svg.indexOf('<img') < 0);
});

console.log('OK ' + n + ' pruebas del gráfico');
