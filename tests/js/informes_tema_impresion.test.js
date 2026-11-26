'use strict';
/* Pruebas del tema claro y de la impresión de la pantalla de informes (templates/informes.html) que EJECUTAN su JavaScript, con el DOM mínimo de mini_dom.js y una API simulada
 * (pantalla_informes.js). Se corre con Node y lo lanza tests/test_informes_tema_impresion.py (clase TestEjecutada); imprime una línea «RESULTADOS {json}».
 *
 *     node tests/js/informes_tema_impresion.test.js
 *
 * Los nombres de informes son inventados: ninguno es de un tambo ni de DelPro.
 */
const assert = require('assert');
const {cargar} = require('./pantalla_informes.js');

const casos = [];
const caso = (nombre, fn) => casos.push([nombre, fn]);
const plano = x => JSON.parse(JSON.stringify(x));

const CLAVE = 'lactia-tema';                                    // la de toda la aplicación: la escribe el botón del encabezado (templates/index.html)
const TEMA = P => P.doc.documentElement.getAttribute('data-theme');
/* Lo que pasa cuando se toca el botón del tema en la app: ella lo guarda y el navegador avisa con `storage` a las demás pantallas (también a la del marco). */
function elegirEnLaApp(P, valor) {
  P.almacen[CLAVE] = valor;
  P.disparar('storage', {key: CLAVE, newValue: valor});
}
const RES = {
  columnas: [{id: 'Number', alias: 'c0', etiqueta: 'Número del animal', tipo: 'txt', unidad: '', decimales: 0, verificado: true, resumen: 'none'},
             {id: 'CurrentDIM', alias: 'c1', etiqueta: 'Días en ordeño', tipo: 'num', unidad: '', decimales: 0, verificado: true, resumen: 'none'}],
  filas: [['3250', 120], ['4300', 150]], claves_filas: null, grupos: [], agrupar: [], totales: null, truncado: false, detalle: true, segundos: 0.4, sql: '',
};
/* Un informe abierto, con su resultado ya calculado. */
function informeCalculado(P, extra) {
  const d = P.t.nuevoDoc(Object.assign({nombre: 'Vacas para revisar', def: Object.assign(P.t.defVacia(), {condiciones: [{campo: 'CurrentDIM', op: '>=', valor: 100}]}),
    res: RES, estado: 'ok', stale: false, calculado: new P.ventana.Date()}, extra || {}));
  P.t.activar(d.id);
  return d;
}

/* ---------------------------------------------------------------------------------------------------------------------------------------- */
/* El tema                                                                                                                                  */
/* ---------------------------------------------------------------------------------------------------------------------------------------- */
caso('tema: sin nada elegido arranca oscuro, como la app, y la pantalla no tiene botón propio', async () => {
  const P = await cargar();
  assert.strictEqual(TEMA(P), 'dark');
  assert.ok(P.$('#btn-tema') === null, 'quedó un botón de tema en la pantalla');     // el tema se elige en un solo lugar: el encabezado de la app
});

caso('tema: se toma el que eligió la app', async () => {
  const P = await cargar({almacen: {[CLAVE]: 'light'}});
  assert.strictEqual(TEMA(P), 'light');
});

caso('tema: la elección vieja que guardaba esta pantalla ya no cuenta', async () => {
  const P = await cargar({almacen: {'lactia.informes.tema': 'light'}});
  assert.strictEqual(TEMA(P), 'dark');
});

caso('tema: cambiarlo en la app se ve acá sin recargar', async () => {
  const P = await cargar();
  elegirEnLaApp(P, 'light');
  assert.strictEqual(TEMA(P), 'light');
  elegirEnLaApp(P, 'dark');
  assert.strictEqual(TEMA(P), 'dark');
});

caso('tema: si se borra lo guardado, vuelve al oscuro', async () => {
  const P = await cargar({almacen: {[CLAVE]: 'light'}});
  delete P.almacen[CLAVE];
  P.disparar('storage', {key: null, newValue: null});                                  // `localStorage.clear()`
  assert.strictEqual(TEMA(P), 'dark');
});

caso('tema: un cambio de otra cosa guardada no lo toca', async () => {
  const P = await cargar();
  P.almacen[CLAVE] = 'light';
  P.disparar('storage', {key: 'otra-cosa', newValue: 'x'});
  assert.strictEqual(TEMA(P), 'dark');
});

caso('tema: un valor guardado que no es un tema se ignora', async () => {
  for (const raro of ['azul', '', '<script>', 'LIGHT ']) {
    const P = await cargar({almacen: {[CLAVE]: raro}});
    assert.strictEqual(TEMA(P), 'dark', JSON.stringify(raro));
  }
});

caso('tema: sin almacenamiento (navegación privada) queda oscuro, sin errores', async () => {
  const P = await cargar({almacenRoto: true});
  assert.strictEqual(TEMA(P), 'dark');
  P.disparar('storage', {key: CLAVE, newValue: 'light'});
  assert.strictEqual(TEMA(P), 'dark');
});

caso('tema: cambiarlo no marca el informe como «sin guardar» ni lo vuelve a calcular', async () => {
  const P = await cargar();
  const d = informeCalculado(P);
  d.base = P.t.foto(d);
  const antes = P.api.llamadas.length;
  elegirEnLaApp(P, 'light');
  await P.flush();
  assert.strictEqual(P.t.sucio(d), false);
  assert.strictEqual(P.api.llamadas.length, antes);
});

/* ---------------------------------------------------------------------------------------------------------------------------------------- */
/* Imprimir                                                                                                                                 */
/* ---------------------------------------------------------------------------------------------------------------------------------------- */
caso('imprimir: el informe tiene el botón y la lista no', async () => {
  const P = await cargar();
  assert.strictEqual(P.$('#pg-list [data-act="imprimir"]'), null);
  assert.ok(P.$('#pg-rep [data-act="imprimir"]'));
});

caso('imprimir: sin resultado calculado avisa qué hacer y no abre la impresión', async () => {
  const P = await cargar();
  const d = P.t.nuevoDoc({nombre: 'Sin calcular'});
  P.t.activar(d.id);
  P.click('#pg-rep [data-act="imprimir"]');
  assert.strictEqual(P.impresiones.length, 0);
  assert.ok(/Actualizar/.test(P.texto('#toast')), P.texto('#toast'));
});

caso('imprimir: con el informe calculado abre la impresión una sola vez', async () => {
  const P = await cargar();
  informeCalculado(P);
  P.click('#pg-rep [data-act="imprimir"]');
  assert.strictEqual(P.impresiones.length, 1);
});

caso('imprimir: el encabezado dice el nombre, el tambo, cuándo se calculó y las condiciones', async () => {
  const P = await cargar();
  informeCalculado(P);
  P.click('#pg-rep [data-act="imprimir"]');
  const h = P.impresiones[0].encabezado;
  assert.ok(h.indexOf('Vacas para revisar') >= 0, h);
  assert.ok(h.indexOf('La Ponderosa') >= 0, h);
  assert.ok(/Calculado el 24\/11\/2026/.test(h), h);
  assert.ok(/Días en ordeño ≥ 100/.test(h), h);                                       // las condiciones con el nombre del campo, no con su id
  assert.ok(h.indexOf('CurrentDIM') < 0, h);
});

caso('imprimir: el encabezado nombra el orden, la agrupación y los animales descartados', async () => {
  const P = await cargar();
  informeCalculado(P, {def: Object.assign(P.t.defVacia(), {columnas: [{campo: 'Number'}, {campo: 'CurrentDIM'}], orden: {campo: 'CurrentDIM', dir: -1}, agrupar_por: ['CurrentLactationNumber'], incluir_descartados: true})});
  P.click('#pg-rep [data-act="imprimir"]');
  const h = P.impresiones[0].encabezado;
  assert.ok(/Ordenado por Días en ordeño \(de mayor a menor\)/.test(h), h);
  assert.ok(/Agrupado por Nº Lactación/.test(h), h);
  assert.ok(/descartados/i.test(h), h);
});

caso('imprimir: lo que viene de un informe pasa como texto y no como HTML', async () => {
  const P = await cargar();
  informeCalculado(P, {nombre: '<img src=x onerror=alert(1)>'});
  P.click('#pg-rep [data-act="imprimir"]');
  assert.ok(P.impresiones[0].encabezado.indexOf('<img src=x onerror=alert(1)>') >= 0);       // el texto literal se ve en la hoja
  assert.strictEqual(P.$('#phead img'), null);                                                  // y no se creó ningún elemento
});

caso('imprimir: un resultado desactualizado lo dice en la hoja', async () => {
  const P = await cargar();
  informeCalculado(P, {stale: true});
  P.click('#pg-rep [data-act="imprimir"]');
  assert.ok(/desactualizado|Actualizar/i.test(P.impresiones[0].encabezado), P.impresiones[0].encabezado);
});

caso('imprimir: un informe traído de DelPro con diferencias que cambian los animales lo dice en la hoja', async () => {
  const P = await cargar();
  informeCalculado(P, {importado: {titulo: 'Traído de DelPro', grupos: [{clase: 'contenido', titulo: 'Cambia qué animales aparecen', items: ['x']}]}});
  P.click('#pg-rep [data-act="imprimir"]');
  assert.ok(/DelPro/.test(P.impresiones[0].encabezado) && /diferencias/.test(P.impresiones[0].encabezado), P.impresiones[0].encabezado);
});

caso('imprimir: se imprime siempre en claro y después vuelve al tema de la pantalla', async () => {
  const P = await cargar();
  informeCalculado(P);
  assert.strictEqual(TEMA(P), 'dark');
  P.click('#pg-rep [data-act="imprimir"]');
  assert.strictEqual(P.impresiones[0].tema, 'light');                                  // mientras se imprime
  assert.strictEqual(TEMA(P), 'dark');                                                 // y al terminar vuelve
  assert.strictEqual(P.almacen[CLAVE], undefined);                                     // imprimir no cambia la preferencia guardada
});

caso('imprimir: con el tema claro elegido se queda en claro', async () => {
  const P = await cargar({almacen: {[CLAVE]: 'light'}});
  informeCalculado(P);
  P.click('#pg-rep [data-act="imprimir"]');
  assert.strictEqual(P.impresiones[0].tema, 'light');
  assert.strictEqual(TEMA(P), 'light');
});

caso('imprimir: Ctrl+P (sin el botón) también imprime en claro y con el encabezado', async () => {
  const P = await cargar();
  informeCalculado(P);
  P.disparar('beforeprint');
  assert.strictEqual(TEMA(P), 'light');
  assert.ok(P.$('#phead').textContent.indexOf('Vacas para revisar') >= 0);
  P.disparar('afterprint');
  assert.strictEqual(TEMA(P), 'dark');
});

caso('imprimir: un afterprint que llega dos veces no pisa el tema que se eligió en la app', async () => {
  const P = await cargar();
  informeCalculado(P);
  P.disparar('beforeprint'); P.disparar('afterprint');
  elegirEnLaApp(P, 'light');
  P.disparar('afterprint');
  assert.strictEqual(TEMA(P), 'light');
});

caso('imprimir: un cambio de tema que llega mientras se imprime no oscurece la hoja y queda al terminar', async () => {
  const P = await cargar({almacen: {[CLAVE]: 'light'}});
  informeCalculado(P);
  P.disparar('beforeprint');
  elegirEnLaApp(P, 'dark');                                                            // otra pestaña de la app cambia el tema
  assert.strictEqual(TEMA(P), 'light');                                                // la hoja sigue en claro
  P.disparar('afterprint');
  assert.strictEqual(TEMA(P), 'dark');
});

caso('imprimir: desde la lista de informes (Ctrl+P) no inventa un informe', async () => {
  const P = await cargar();
  P.disparar('beforeprint');
  assert.ok(P.$('#phead').textContent.indexOf('undefined') < 0);
  P.disparar('afterprint');
});

caso('imprimir: en el gráfico, sin datos calculados avisa; con el informe sin calcular también', async () => {
  const P = await cargar();
  const d = informeCalculado(P);
  d.graficoAbierto = true;
  P.t.activar('g:' + d.id);
  assert.ok(P.$('#pg-graf [data-act="imprimir"]'));
  P.click('#pg-graf [data-act="imprimir"]');
  assert.strictEqual(P.impresiones.length, 0);
  assert.ok(/Actualizar/.test(P.texto('#toast')), P.texto('#toast'));
});

async function main() {
  const resultados = [];
  for (const [nombre, fn] of casos) {
    try { await fn(); resultados.push({nombre, ok: true}); }
    catch (e) { resultados.push({nombre, ok: false, error: String((e && e.stack) || e).slice(0, 1500)}); }
  }
  console.log('RESULTADOS ' + JSON.stringify(resultados));
  process.exit(resultados.every(r => r.ok) ? 0 : 1);
}
main();
