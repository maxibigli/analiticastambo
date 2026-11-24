'use strict';
/* Pruebas de la pantalla de informes (templates/informes.html) que EJECUTAN su JavaScript, con el DOM mínimo de mini_dom.js y una API simulada
 * (pantalla_informes.js): una respuesta que llega tarde, un doble clic, el segundo toque de «Enviar ahora», el foco del teclado.
 * Se corre con Node y lo lanza tests/test_informes_pagina.py (clase TestPantallaEjecutada); imprime una línea «RESULTADOS {json}».
 *
 *     node tests/js/informes_pantalla.test.js
 *
 * Los nombres de informes, carpetas y textos son inventados: ninguno es de un tambo ni de DelPro.
 */
const assert = require('assert');
const {cargar} = require('./pantalla_informes.js');

const casos = [];
const caso = (nombre, fn) => casos.push([nombre, fn]);
/* Lo que crea la página vive en otro «realm» de JavaScript (su Array no es el de esta prueba): para compararlo con deepStrictEqual se pasa por JSON. */
const plano = x => JSON.parse(JSON.stringify(x));

const ENVIO_UNO = {dias: [0, 1], horas: ['08:00'], canales: ['correo'], formato: 'excel', activo: true, proximo_texto: 'lun 30/11/2026 08:00'};
const ENVIO_DOS = {dias: [2, 3], horas: ['09:30'], canales: ['telegram'], formato: 'texto', activo: true, proximo_texto: 'mié 25/11/2026 09:30'};
const CUERPO_PROG = (P, envio) => ({envio: envio || null, canales: P.CANALES, ultima_pasada_texto: 'mar 24/11/2026 11:59', ultima_pasada_hace_s: 60});

/* Un informe ya guardado, abierto en una pestaña. */
const docGuardado = (P, sid, nombre, extra) => P.t.nuevoDoc({sid, nombre, def: Object.assign(P.t.defVacia(), extra || {}), guardado: true});
/* La programación que el servidor tiene guardada para ese informe. */
const conEnvio = (P, sid, envio) => P.api.cuando('GET', new RegExp('^/api/informes/' + sid + '/envio$'), () => CUERPO_PROG(P, envio));
async function abrirProg(P, d) { P.t.activar(d.id); P.click('#btn-prog'); await P.flush(); }
const PUTS = P => P.api.de('PUT', /\/envio$/);
const PROBAR = P => P.api.de('POST', /\/envio\/probar$/);

/* ---------------------------------------------------------------------------------------------------------------------------------------- */
/* «Es uno de»: cómo se separa la lista (regresion #1, #3 y pantalla #1)                                                                    */
/* ---------------------------------------------------------------------------------------------------------------------------------------- */
caso('lista: un campo calculado separa con la regla de los números', async () => {
  const P = await cargar();
  const calc = {id: 'calc_1', etiqueta: 'Kg por día', formula: '[SevenDayAverage] / [CurrentDIM]', decimales: 2, unidad: ''};
  const d = P.t.nuevoDoc({nombre: 'Con calculado', def: Object.assign(P.t.defVacia(), {calculados: [calc], columnas: [{campo: 'Number'}, {campo: 'calc_1'}], condiciones: [{campo: 'calc_1', op: 'en', valor: '3; 4'}]})});
  P.t.activar(d.id);
  assert.deepStrictEqual(plano(P.t.limpiaDef(d.def).condiciones), [{campo: 'calc_1', op: 'en', valor: ['3', '4']}]);
  d.def.condiciones[0].valor = '0,5; 1';                                                      // la coma es la decimal: «0,5» es UN número
  assert.deepStrictEqual(plano(P.t.limpiaDef(d.def).condiciones[0].valor), ['0,5', '1']);
  P.click('#btn-run'); await P.flush();                                                      // y eso es lo que viaja a «Actualizar»
  assert.deepStrictEqual(P.api.de('POST', /\/ejecutar$/).pop().cuerpo.condiciones[0].valor, ['0,5', '1']);
});

caso('lista: al salir de «es uno de» en un campo calculado queda el primer valor entero', async () => {
  const P = await cargar();
  const calc = {id: 'calc_1', etiqueta: 'Kg por día', formula: '[SevenDayAverage] / [CurrentDIM]', decimales: 2, unidad: ''};
  const d = P.t.nuevoDoc({nombre: 'Con calculado', def: Object.assign(P.t.defVacia(), {calculados: [calc], columnas: [{campo: 'Number'}, {campo: 'calc_1'}], condiciones: [{campo: 'calc_1', op: 'en', valor: '0,5; 1'}]})});
  P.t.activar(d.id);
  P.cambiar('#filters [data-chg="co"][data-i="0"]', '=');
  assert.strictEqual(d.def.condiciones[0].op, '=');
  assert.strictEqual(d.def.condiciones[0].valor, '0,5');                                     // no «0»
});

caso('lista: en un campo entero la coma también separa; con decimales sigue siendo la coma decimal', async () => {
  const P = await cargar();
  const def = cond => Object.assign(P.t.defVacia(), {condiciones: [cond]});
  const lista = (campo, valor) => plano(P.t.limpiaDef(def({campo, op: 'en', valor})).condiciones[0].valor);
  assert.deepStrictEqual(lista('CurrentLactationNumber', '1,2,3'), ['1', '2', '3']);          // Nº Lactación: entero
  assert.deepStrictEqual(lista('AnimalGroupNumber', '1, 2 ; 3'), ['1', '2', '3']);
  assert.deepStrictEqual(lista('CurrentDIM', '100,200'), ['100', '200']);
  assert.deepStrictEqual(lista('SevenDayAverage', '38,5; 40'), ['38,5', '40']);               // un promedio tiene decimales
  assert.deepStrictEqual(lista('SevenDayAverage', '38,5, 40'), ['38,5', '40']);
  assert.deepStrictEqual(lista('SevenDayAverage', '38,5'), ['38,5']);                         // «38,5» sigue siendo UN número
  assert.deepStrictEqual(lista('Number', 'A1,A2'), ['A1', 'A2']);                              // un texto se separa con comas
});

caso('lista: al salir de «es uno de» en un campo entero queda el primer valor', async () => {
  const P = await cargar();
  const d = P.t.nuevoDoc({nombre: 'Lactancias', def: Object.assign(P.t.defVacia(), {condiciones: [{campo: 'CurrentLactationNumber', op: 'en', valor: '1,2,3'}]})});
  P.t.activar(d.id);
  P.cambiar('#filters [data-chg="co"][data-i="0"]', '=');
  assert.strictEqual(d.def.condiciones[0].valor, '1');
});

/* ---------------------------------------------------------------------------------------------------------------------------------------- */
/* Programar el envío                                                                                                                       */
/* ---------------------------------------------------------------------------------------------------------------------------------------- */
caso('programar: una respuesta tardía de «Guardar» no pinta ni escribe el cuadro de otro informe', async () => {
  const P = await cargar();
  const d1 = docGuardado(P, 1, 'Informe uno'), d2 = docGuardado(P, 2, 'Informe dos');
  conEnvio(P, 1, ENVIO_UNO); conEnvio(P, 2, ENVIO_DOS);
  const tarde = P.diferido();
  P.api.cuando('PUT', /^\/api\/informes\/1\/envio$/, () => tarde.promesa);
  await abrirProg(P, d1);
  P.click('#prog-guardar'); await P.flush();                                                 // el PUT del informe 1 queda esperando
  P.click('#prog-cerrar');
  await abrirProg(P, d2);
  assert.strictEqual(P.t.PG.sid, 2);
  tarde.resolver(CUERPO_PROG(P, ENVIO_UNO)); await P.flush();                                 // ...y contesta cuando el cuadro ya es del informe 2
  assert.deepStrictEqual(plano(P.t.PG.dias), [2, 3]);
  assert.ok(P.texto('#prog-estado').includes('mié 25/11/2026 09:30'), P.texto('#prog-estado'));
  assert.strictEqual(P.texto('#prog-msj'), '');                                              // el «Guardado» de otro informe no se muestra acá
  P.avanzar(1000);
  P.click('#prog-guardar'); await P.flush();                                                 // un «Guardar» posterior escribe lo del informe 2
  const ultimo = PUTS(P).pop();
  assert.strictEqual(ultimo.ruta, '/api/informes/2/envio');
  assert.deepStrictEqual([ultimo.cuerpo.dias, ultimo.cuerpo.horas, ultimo.cuerpo.canales, ultimo.cuerpo.formato], [[2, 3], ['09:30'], ['telegram'], 'texto']);
});

caso('programar: un error tardío del guardado de otro informe no se muestra', async () => {
  const P = await cargar();
  const d1 = docGuardado(P, 1, 'Informe uno'), d2 = docGuardado(P, 2, 'Informe dos');
  conEnvio(P, 1, ENVIO_UNO); conEnvio(P, 2, ENVIO_DOS);
  const tarde = P.diferido();
  P.api.cuando('PUT', /^\/api\/informes\/1\/envio$/, () => tarde.promesa);
  await abrirProg(P, d1);
  P.click('#prog-guardar'); await P.flush();
  P.click('#prog-cerrar');
  await abrirProg(P, d2);
  tarde.resolver(P.error(400, {error: 'Elegí al menos un canal.'})); await P.flush();
  assert.strictEqual(P.texto('#prog-msj'), '');
});

caso('programar: el registro que llega tarde no se mezcla con el de otro informe', async () => {
  const P = await cargar();
  const d1 = docGuardado(P, 1, 'Informe uno'), d2 = docGuardado(P, 2, 'Informe dos');
  const reg1 = P.diferido();
  P.api.cuando('GET', /^\/api\/informes\/1\/envio\/registro$/, () => reg1.promesa);
  P.api.cuando('GET', /^\/api\/informes\/2\/envio\/registro$/, () => ({registro: [{slot_texto: 'dom 22/11/2026 08:00', canal: 'telegram', estado: 'enviado', detalle: ''}], ultima_pasada_texto: 'x', ultima_pasada_hace_s: 60}));
  await abrirProg(P, d1);                                                                    // el registro del informe 1 queda esperando
  P.click('#prog-cerrar');
  await abrirProg(P, d2);
  assert.ok(P.texto('#prog-reg-cuerpo').includes('dom 22/11/2026 08:00'));
  reg1.resolver({registro: [{slot_texto: 'lun 23/11/2026 08:00', canal: 'correo', estado: 'enviado', detalle: ''}], ultima_pasada_texto: 'x', ultima_pasada_hace_s: 60}); await P.flush();
  assert.ok(!P.texto('#prog-reg-cuerpo').includes('lun 23/11/2026'), P.texto('#prog-reg-cuerpo'));
  assert.ok(P.texto('#prog-reg-cuerpo').includes('dom 22/11/2026 08:00'));
});

caso('programar: al abrir el cuadro de otro informe se vacían el registro y el mensaje', async () => {
  const P = await cargar();
  const d1 = docGuardado(P, 1, 'Informe uno'), d2 = docGuardado(P, 2, 'Informe dos');
  conEnvio(P, 1, ENVIO_UNO);
  P.api.cuando('GET', /^\/api\/informes\/1\/envio\/registro$/, () => ({registro: [{slot_texto: 'lun 23/11/2026 08:00', canal: 'correo', estado: 'enviado', detalle: ''}], ultima_pasada_texto: 'x', ultima_pasada_hace_s: 60}));
  await abrirProg(P, d1);
  P.click('#prog-guardar'); await P.flush();
  assert.ok(P.texto('#prog-reg-cuerpo').includes('lun 23/11/2026'));
  assert.ok(P.texto('#prog-msj').includes('Guardado'));
  P.click('#prog-cerrar');
  const reg2 = P.diferido();
  P.api.cuando('GET', /^\/api\/informes\/2\/envio\/registro$/, () => reg2.promesa);
  await abrirProg(P, d2);                                                                    // el registro del informe 2 todavía no llegó
  assert.strictEqual(P.texto('#prog-reg-cuerpo'), '');
  assert.strictEqual(P.texto('#prog-msj'), '');
});

caso('programar: «Enviar ahora» manda el formato del formulario y el primer toque dice qué va a salir', async () => {
  const P = await cargar();
  const d1 = docGuardado(P, 1, 'Informe uno');
  conEnvio(P, 1, Object.assign({}, ENVIO_UNO, {canales: ['correo', 'telegram']}));
  P.api.cuando('POST', /\/envio\/probar$/, c => ({asunto: 'a', texto: 'TEXTO DEL CORREO', textos: {correo: 'TEXTO DEL CORREO', telegram: 'TEXTO DE TELEGRAM', whatsapp: 'TEXTO DE WHATSAPP'},
    animales: 3, bytes: 0, enviado: c.cuerpo.enviar ? {correo: 'ok', telegram: 'ok'} : {}}));
  await abrirProg(P, d1);
  P.cambiar('input[name="prog-fmt"][value="texto"]', true);                                   // en el formulario eligió «solo el resumen», sin guardarlo todavía
  P.click('#prog-enviar');                                                                   // primer toque: solo avisa
  assert.strictEqual(PROBAR(P).length, 0);
  const aviso = P.texto('#prog-msj');
  assert.ok(/solo el resumen en texto/.test(aviso) && /Correo/.test(aviso) && /Telegram/.test(aviso), aviso);
  P.avanzar(800);
  P.click('#prog-enviar'); await P.flush();                                                  // segundo toque: manda de verdad
  assert.deepStrictEqual(PROBAR(P).pop().cuerpo, {enviar: true, canales: ['correo', 'telegram'], formato: 'texto'});
});

caso('programar: «Ver cómo queda» también manda el formato y no dice que usa lo guardado', async () => {
  const P = await cargar();
  const d1 = docGuardado(P, 1, 'Informe uno');
  conEnvio(P, 1, ENVIO_UNO);
  P.api.cuando('POST', /\/envio\/probar$/, () => ({asunto: 'a', texto: 'TEXTO DEL CORREO', textos: {correo: 'TEXTO DEL CORREO', telegram: 'T', whatsapp: 'W'}, animales: 3, bytes: 0}));
  await abrirProg(P, d1);
  P.cambiar('input[name="prog-fmt"][value="texto"]', true);
  P.click('#prog-ver'); await P.flush();
  assert.strictEqual(PROBAR(P).pop().cuerpo.formato, 'texto');
  assert.ok(!/usa lo que está guardado/.test(P.texto('#prog-msj')), P.texto('#prog-msj'));
});

caso('programar: se muestra el texto de cada canal tildado, rotulado, y sin «textos» el del correo', async () => {
  const P = await cargar();
  const d1 = docGuardado(P, 1, 'Informe uno');
  conEnvio(P, 1, Object.assign({}, ENVIO_UNO, {canales: ['correo', 'telegram']}));
  const textos = {correo: 'TEXTO DEL CORREO', telegram: 'TEXTO DE TELEGRAM', whatsapp: 'TEXTO DE WHATSAPP'};
  P.api.cuando('POST', /\/envio\/probar$/, c => ({asunto: 'a', texto: textos.correo, textos, animales: 3, bytes: 0, enviado: c.cuerpo.enviar ? {correo: 'ok', telegram: 'ok'} : {}}));
  await abrirProg(P, d1);
  P.click('#prog-ver'); await P.flush();                                                     // «Ver cómo queda»
  let pre = P.texto('#prog-prev');
  assert.ok(pre.includes('TEXTO DEL CORREO') && pre.includes('TEXTO DE TELEGRAM'), pre);
  assert.ok(!pre.includes('TEXTO DE WHATSAPP'), pre);                                          // WhatsApp no está tildado
  assert.ok(pre.indexOf('Correo') < pre.indexOf('TEXTO DEL CORREO') && pre.indexOf('Telegram') < pre.indexOf('TEXTO DE TELEGRAM'), pre);   // cada texto lleva el nombre de su canal
  P.click('#prog-enviar'); P.avanzar(800); P.click('#prog-enviar'); await P.flush();           // el resultado de «Enviar ahora»
  pre = P.texto('#prog-prev');
  assert.ok(pre.includes('TEXTO DE TELEGRAM') && !pre.includes('TEXTO DE WHATSAPP'), pre);
  P.avanzar(1000);
  P.api.cuando('POST', /\/envio\/probar$/, () => ({asunto: 'a', texto: 'SOLO EL DEL CORREO', animales: 3, bytes: 0}));       // un servidor sin «textos»
  P.click('#prog-ver'); await P.flush();
  assert.ok(P.texto('#prog-prev').includes('SOLO EL DEL CORREO'), P.texto('#prog-prev'));
});

caso('programar: un doble clic en «Pausar envío» no se deshace solo, aunque el servidor conteste enseguida', async () => {
  const P = await cargar();
  const d1 = docGuardado(P, 1, 'Informe uno');
  conEnvio(P, 1, ENVIO_UNO);
  await abrirProg(P, d1);
  P.click('#prog-pausa'); await P.flush();                                                   // el servidor contesta antes del segundo clic
  assert.strictEqual(P.texto('#prog-pausa'), 'Reanudar envío');
  P.avanzar(150);
  P.click('#prog-pausa'); await P.flush();                                                   // el segundo clic del doble clic
  assert.strictEqual(PUTS(P).length, 1);
  assert.strictEqual(PUTS(P)[0].cuerpo.activo, false);
  assert.strictEqual(P.t.PG.datos.envio.activo, false);
  P.avanzar(800);                                                                            // pasado un instante, tocar otra vez es OTRA decisión: reanuda
  P.click('#prog-pausa'); await P.flush();
  assert.strictEqual(PUTS(P).length, 2);
  assert.strictEqual(PUTS(P)[1].cuerpo.activo, true);
});

caso('programar: mientras se guarda, «Guardar» y «Pausar» quedan quietos y dicen «Guardando…»', async () => {
  const P = await cargar();
  const d1 = docGuardado(P, 1, 'Informe uno');
  conEnvio(P, 1, ENVIO_UNO);
  await abrirProg(P, d1);
  const dif = P.diferido();
  P.api.cuando('PUT', /\/envio$/, () => dif.promesa);
  P.click('#prog-pausa'); await P.flush();
  assert.ok(P.$('#prog-pausa').disabled && P.$('#prog-guardar').disabled);
  assert.strictEqual(P.texto('#prog-pausa'), 'Guardando…');
  assert.strictEqual(P.texto('#prog-guardar'), 'Guardando…');
  P.click('#prog-guardar'); P.click('#prog-pausa'); await P.flush();                         // más toques mientras tanto
  assert.strictEqual(PUTS(P).length, 1);
  P.t.guardarProg(true, 'Guardado.'); await P.flush();                                       // ni siquiera llamando a la función
  assert.strictEqual(PUTS(P).length, 1);
  dif.resolver(CUERPO_PROG(P, Object.assign({}, ENVIO_UNO, {activo: false}))); await P.flush();
  P.avanzar(800);
  assert.strictEqual(P.texto('#prog-pausa'), 'Reanudar envío');
  assert.ok(!P.$('#prog-pausa').disabled && !P.$('#prog-guardar').disabled);
});

caso('programar: la vista previa dice «Armando…» y no «Mandando…»; el envío sí dice «Mandando…»', async () => {
  const P = await cargar();
  const d1 = docGuardado(P, 1, 'Informe uno');
  conEnvio(P, 1, ENVIO_UNO);
  await abrirProg(P, d1);
  const dif = P.diferido();
  P.api.cuando('POST', /\/envio\/probar$/, () => dif.promesa);
  P.click('#prog-ver'); await P.flush();
  assert.strictEqual(P.texto('#prog-ver'), 'Armando…');
  assert.ok(P.$('#prog-ver').disabled);
  assert.strictEqual(P.texto('#prog-enviar'), 'Enviar ahora');                                // no se está mandando nada
  assert.ok(P.$('#prog-enviar').disabled);
  dif.resolver({asunto: 'a', texto: 'T', animales: 1, bytes: 0}); await P.flush();
  assert.strictEqual(P.texto('#prog-ver'), 'Ver cómo queda');
  assert.ok(!P.$('#prog-enviar').disabled && !P.$('#prog-ver').disabled);
  const dif2 = P.diferido();
  P.api.cuando('POST', /\/envio\/probar$/, () => dif2.promesa);
  P.click('#prog-enviar'); P.avanzar(800); P.click('#prog-enviar'); await P.flush();
  assert.strictEqual(P.texto('#prog-enviar'), 'Mandando…');
  assert.strictEqual(P.texto('#prog-ver'), 'Ver cómo queda');
  assert.ok(P.$('#prog-ver').disabled);
});

caso('programar: el aviso de fechas fijas mira lo guardado y no lo que está en pantalla sin guardar', async () => {
  const P = await cargar();
  const conFecha = {condiciones: [{campo: 'LastCalvingDate', op: '>=', valor: '2026-10-01'}]};
  const d1 = docGuardado(P, 1, 'Con fecha guardada', conFecha);
  d1.def.condiciones = [];                                                                   // la quitó en pantalla, sin guardar: lo que se manda sigue teniéndola
  await abrirProg(P, d1);
  assert.ok(P.texto('#prog-nota').includes('no se corren solas'), P.texto('#prog-nota'));
  P.click('#prog-cerrar');
  const d2 = docGuardado(P, 2, 'Sin fecha guardada');
  d2.def.condiciones = [{campo: 'LastCalvingDate', op: '>=', valor: '2026-10-01'}];          // la agregó en pantalla, sin guardar: lo que se manda no la tiene
  await abrirProg(P, d2);
  assert.ok(!P.texto('#prog-nota').includes('no se corren solas'), P.texto('#prog-nota'));
});

caso('programar: en un informe sin guardar el botón sigue activo y al tocarlo explica por qué no', async () => {
  const P = await cargar();
  const nuevo = P.t.nuevoDoc({});
  P.t.activar(nuevo.id);
  const b = P.$('#btn-prog');
  assert.ok(!b.disabled, 'un botón deshabilitado no explica nada');
  assert.strictEqual(b.getAttribute('aria-disabled'), 'true');
  P.click('#btn-prog'); await P.flush();
  assert.strictEqual(P.texto('#toast'), 'Guardá el informe para poder programar su envío.');
  assert.ok(!P.$('#toast').hidden);
  assert.ok(!P.$('#prog').open, 'el cuadro no se abre para un informe sin guardar');
  P.t.activar(docGuardado(P, 1, 'Informe uno').id);
  assert.notStrictEqual(P.$('#btn-prog').getAttribute('aria-disabled'), 'true');
});

/* ---------------------------------------------------------------------------------------------------------------------------------------- */
/* Traer de DelPro                                                                                                                          */
/* ---------------------------------------------------------------------------------------------------------------------------------------- */
const fila = (oid, estado, n, extra) => Object.assign({oid, nombre: 'Informe de ejemplo ' + oid, carpeta: 'Carpeta de ejemplo', nivel: 'Animal', estado, n_diferencias: n,
  columnas: {traidas: 2, total: 2}, filtro_sin_traer: false}, extra || {});
const LISTA_DELPRO = {disponible: true, cuenta: {completo: 3, parcial: 1, no_disponible: 1}, faltantes: [], informes: [
  fila(1, 'completo', 0), fila(2, 'completo', 3), fila(3, 'completo', 1), fila(4, 'parcial', 5), fila(5, 'no_disponible', 0, {motivo: 'No se puede traer.', columnas: {traidas: 0, total: 0}})]};
const INFORME_DELPRO = {nombre: 'Informe de ejemplo A', carpeta: 'Carpeta de ejemplo', descripcion: '', estado: 'completo', definicion: {columnas: [{campo: 'Number'}], condiciones: []},
  diferencias: [{gravedad: 'info', texto: 'Para tener en cuenta.', detalle: []}]};

caso('traer: el resumen cuenta con la misma regla que las filas (completo con avisos es «casi igual»)', async () => {
  const P = await cargar();
  P.api.cuando('GET', /^\/api\/informes\/delpro$/, () => LISTA_DELPRO);
  P.click('#btn-delpro'); await P.flush();
  assert.strictEqual(P.texto('#dp-resumen'), '5 informes de tu tambo en DelPro: 1 completo, 2 casi iguales (con algún aviso), 1 con diferencias, 1 no se puede traer todavía.');
  P.api.cuando('GET', /^\/api\/informes\/delpro$/, () => ({disponible: true, cuenta: {completo: 0, parcial: 0, no_disponible: 0}, faltantes: [], informes: [fila(1, 'completo', 2), fila(2, 'completo', 1)]}));
  P.click('#dp-refrescar'); await P.flush();
  assert.strictEqual(P.texto('#dp-resumen'), '2 informes de tu tambo en DelPro: 0 completos, 2 casi iguales (con algún aviso), 0 con diferencias, 0 no se pueden traer todavía.');
});

caso('traer: si el cuadro ya no está abierto cuando llega el informe, queda en una pestaña de fondo', async () => {
  const P = await cargar();
  P.api.cuando('GET', /^\/api\/informes\/delpro$/, () => LISTA_DELPRO);
  const dif = P.diferido();
  P.api.cuando('GET', /^\/api\/informes\/delpro\/1$/, () => dif.promesa);
  P.click('#btn-delpro'); await P.flush();
  P.click('[data-act="dptraer"][data-oid="1"]'); await P.flush();                           // el informe se está trayendo...
  P.click('#dp-cerrar');                                                                     // ...y el dueño cierra el cuadro y se pone a mirar otro informe
  const d1 = docGuardado(P, 1, 'Informe uno');
  P.t.activar(d1.id);
  dif.resolver(INFORME_DELPRO); await P.flush();
  assert.strictEqual(P.t.activeId, d1.id, 'la pantalla le saltó al informe traído');
  assert.strictEqual(P.t.docs.length, 2);
  const traido = P.t.docs[1];
  assert.strictEqual(traido.nombre, 'Informe de ejemplo A');
  assert.ok(traido.importado && traido.sid === null);                                         // sigue siendo un informe nuevo, sin guardar
  assert.ok(!P.$('#toast').hidden && /Informe de ejemplo A/.test(P.texto('#toast')) && /pestaña nueva/.test(P.texto('#toast')) && /sin guardar/.test(P.texto('#toast')), P.texto('#toast'));
  assert.ok(!P.doc.activeElement.closest('#importado'), 'el foco no debería irse a un aviso que no está a la vista');
});

caso('traer: con el cuadro abierto el informe se abre y el foco queda en su aviso', async () => {
  const P = await cargar();
  P.api.cuando('GET', /^\/api\/informes\/delpro$/, () => LISTA_DELPRO);
  P.api.cuando('GET', /^\/api\/informes\/delpro\/1$/, () => INFORME_DELPRO);
  P.click('#btn-delpro'); await P.flush();
  P.click('[data-act="dptraer"][data-oid="1"]'); await P.flush();
  assert.strictEqual(P.t.docs.length, 1);
  assert.strictEqual(P.t.activeId, P.t.docs[0].id);
  assert.ok(!P.$('#delpro').open);
  assert.ok(P.doc.activeElement.closest('.imp'), 'el foco tiene que quedar en el aviso: ' + P.foco());
});

/* ---------------------------------------------------------------------------------------------------------------------------------------- */
/* El foco del teclado (pantalla #8)                                                                                                        */
/* ---------------------------------------------------------------------------------------------------------------------------------------- */
caso('foco: al cambiar el campo de una condición el foco sigue en el mismo selector', async () => {
  const P = await cargar();
  const d = P.t.nuevoDoc({nombre: 'Con condición', def: Object.assign(P.t.defVacia(), {condiciones: [{campo: 'CurrentDIM', op: '>=', valor: '100'}]})});
  P.t.activar(d.id);
  P.cambiar('#filters [data-chg="ck"][data-i="0"]', 'SevenDayAverage');
  assert.strictEqual(d.def.condiciones[0].campo, 'SevenDayAverage');
  assert.strictEqual(P.foco(), 'select[data-chg="ck"][data-i="0"]');
});

caso('foco: «+ Condición» pasa el foco al campo de la fila nueva', async () => {
  const P = await cargar();
  const d = P.t.nuevoDoc({nombre: 'Con condición', def: Object.assign(P.t.defVacia(), {condiciones: [{campo: 'CurrentDIM', op: '>=', valor: '100'}]})});
  P.t.activar(d.id);
  P.click('[data-act="agregarcond"]');
  assert.strictEqual(d.def.condiciones.length, 2);
  assert.strictEqual(P.foco(), 'select[data-chg="ck"][data-i="1"]');
});

caso('foco: cerrar el aviso del informe traído deja el foco en «Ver el aviso» o, si no queda franja, en el nombre', async () => {
  const P = await cargar();
  const aviso = grupos => ({cerrado: false, titulo: 'Traído de DelPro', sub: '«Informe de ejemplo A»', chip: ['Casi igual', 'ok'], grupos});
  const sinContenido = P.t.nuevoDoc({nombre: 'Traído uno', importado: aviso([{clase: 'info', titulo: 'Para tener en cuenta', items: ['Algo menor.']}])});
  P.t.activar(sinContenido.id);
  P.click('[data-act="cerrarimp"]');
  assert.strictEqual(P.foco(), 'input#rname');                                                // no queda franja: el foco va al nombre del informe
  const conContenido = P.t.nuevoDoc({nombre: 'Traído dos', importado: aviso([{clase: 'contenido', titulo: 'Cambia qué animales aparecen', items: ['Falta un filtro.']}])});
  P.t.activar(conContenido.id);
  P.click('[data-act="cerrarimp"]');
  assert.strictEqual(P.foco(), 'button[data-act="abririmp"]');                                // queda la franja: el foco va a «Ver el aviso»
});

/* ---------------------------------------------------------------------------------------------------------------------------------------- */
/* Un campo de lista cerrada («sí/no», el estado de la reproducción): se elige de una lista en vez de escribirlo (tanda 1)                   */
/* ---------------------------------------------------------------------------------------------------------------------------------------- */
const opcionesDe = (P, sel) => P.$$(sel + ' option').map(o => o.textContent);
const conCondicion = (P, cond) => { const d = P.t.nuevoDoc({nombre: 'Con lista', def: Object.assign(P.t.defVacia(), {condiciones: [cond]})}); P.t.activar(d.id); return d; };

caso('lista cerrada: «es» sobre un sí/no se elige de un desplegable con «No» y «Sí»', async () => {
  const P = await cargar();
  conCondicion(P, {campo: 'InMilk', op: 'es', valor: 'Sí'});
  const sel = '#filters select[data-chg="cv"][data-i="0"]';
  assert.ok(P.$(sel), 'tiene que ser un desplegable, no un campo de texto');
  assert.deepStrictEqual(opcionesDe(P, sel), ['No', 'Sí']);
  assert.strictEqual(P.$(sel).value, 'Sí');
});

caso('lista cerrada: elegir otro valor lo guarda en la condición', async () => {
  const P = await cargar();
  const d = conCondicion(P, {campo: 'InMilk', op: 'es', valor: 'Sí'});
  P.cambiar('#filters select[data-chg="cv"][data-i="0"]', 'No');
  assert.strictEqual(d.def.condiciones[0].valor, 'No');
  assert.deepStrictEqual(plano(P.t.limpiaDef(d.def).condiciones), [{campo: 'InMilk', op: 'es', valor: 'No'}]);
});

caso('lista cerrada: al elegir un sí/no como campo la condición arranca en «es Sí» (y no queda vacía, que se ignoraría)', async () => {
  const P = await cargar();
  const d = conCondicion(P, {campo: 'CurrentDIM', op: '>=', valor: '100'});
  P.cambiar('#filters [data-chg="ck"][data-i="0"]', 'InMilk');
  assert.strictEqual(d.def.condiciones[0].op, 'es');
  assert.strictEqual(d.def.condiciones[0].valor, 'Sí');
  assert.strictEqual(P.$('#filters select[data-chg="cv"][data-i="0"]').value, 'Sí');
  assert.deepStrictEqual(plano(P.t.limpiaDef(d.def).condiciones), [{campo: 'InMilk', op: 'es', valor: 'Sí'}]);          // lo que se ve es lo que viaja
});

caso('lista cerrada: el estado de la reproducción arranca en su primer valor', async () => {
  const P = await cargar();
  const d = conCondicion(P, {campo: 'CurrentDIM', op: '>=', valor: '100'});
  P.cambiar('#filters [data-chg="ck"][data-i="0"]', 'CurrentReproductionStatus');
  assert.deepStrictEqual([d.def.condiciones[0].op, d.def.condiciones[0].valor], ['es', 'Abierta']);
  assert.deepStrictEqual(opcionesDe(P, '#filters select[data-chg="cv"][data-i="0"]'), ['Abierta', 'Inseminada', 'Preñada', 'Seca']);
});

caso('lista cerrada: una condición vieja con un valor que no está en la lista se corrige al mostrarla', async () => {
  const P = await cargar();
  const d = conCondicion(P, {campo: 'InMilk', op: 'es', valor: ''});
  assert.strictEqual(d.def.condiciones[0].valor, 'Sí');
  assert.strictEqual(P.$('#filters select[data-chg="cv"][data-i="0"]').value, 'Sí');
});

caso('lista cerrada: «contiene» y «es uno de» siguen siendo texto libre; volver a «es» vuelve al desplegable', async () => {
  const P = await cargar();
  const d = conCondicion(P, {campo: 'CurrentReproductionStatus', op: 'es', valor: 'Seca'});
  P.cambiar('#filters [data-chg="co"][data-i="0"]', 'contiene');
  assert.ok(!P.$('#filters select[data-chg="cv"]'), 'contiene se escribe');
  assert.ok(P.$('#filters input[data-chg="cv"]'));
  P.cambiar('#filters [data-chg="co"][data-i="0"]', 'es');
  assert.ok(P.$('#filters select[data-chg="cv"][data-i="0"]'));
  assert.ok(['Abierta', 'Inseminada', 'Preñada', 'Seca'].indexOf(d.def.condiciones[0].valor) >= 0, d.def.condiciones[0].valor);
});

caso('lista cerrada: un campo de texto común sigue siendo un campo de texto', async () => {
  const P = await cargar();
  conCondicion(P, {campo: 'Number', op: 'es', valor: '1234'});
  assert.ok(P.$('#filters input[data-chg="cv"][data-i="0"]'));
  assert.ok(!P.$('#filters select[data-chg="cv"]'));
});

/* ---------------------------------------------------------------------------------------------------------------------------------------- */
/* El aviso de un informe traído de DelPro sobrevive al guardado (completitud #1)                                                           */
/* ---------------------------------------------------------------------------------------------------------------------------------------- */
const AVISOS_DELPRO = ['No se trajo la condición «Días en ordeño > 30»: el informe puede mostrar más animales.', 'En DelPro incluía también a los animales descartados.'];
const traidoDeDelpro = (P, extra) => P.t.abrirComoNuevo('Informe de ejemplo traído', {columnas: [{campo: 'Number'}], condiciones: []},
  Object.assign({titulo: 'Traído de DelPro', sub: '«Informe de ejemplo» · Carpeta', cerrado: false, chip: ['Parcial', 'warn'],
    grupos: [{clase: 'contenido', titulo: 'Cambia qué animales aparecen', items: AVISOS_DELPRO.map(texto => ({texto, detalle: ['un detalle']}))},
             {clase: 'info', titulo: 'Para tener en cuenta', items: [{texto: 'Algo menor.', detalle: []}]}],
    alGuardar: 'Ojo: no trae todo lo que tenía en DelPro, así que puede mostrar animales de más o de menos.'}, extra || {}));

caso('origen: al guardar un informe traído con diferencias viaja la nota con las diferencias que cambian los animales (y nada más)', async () => {
  const P = await cargar();
  const d = traidoDeDelpro(P);
  P.api.cuando('POST', /^\/api\/informes$/, () => ({id: 7, nombre: 'Informe de ejemplo traído'}));
  await P.t.guardar(d, false);
  const cuerpo = P.api.de('POST', /^\/api\/informes$/).pop().cuerpo;
  assert.deepStrictEqual(plano(cuerpo.origen), {titulo: 'Traído de DelPro', sub: '«Informe de ejemplo» · Carpeta', avisos: AVISOS_DELPRO});
  assert.ok(!('origen' in cuerpo.definicion), 'la nota no es parte de la definición');
});

caso('origen: guardar de nuevo un informe ya guardado manda la misma nota; «Guardar como…» también', async () => {
  const P = await cargar();
  const d = traidoDeDelpro(P);
  P.api.cuando('POST', /^\/api\/informes$/, () => ({id: 7, nombre: 'Informe de ejemplo traído'}));
  P.api.cuando('PUT', /^\/api\/informes\/7$/, () => ({id: 7, nombre: 'Informe de ejemplo traído'}));
  await P.t.guardar(d, false);
  await P.t.guardar(d, false);
  assert.deepStrictEqual(plano(P.api.de('PUT', /^\/api\/informes\/7$/).pop().cuerpo.origen.avisos), AVISOS_DELPRO);
  await P.t.guardar(d, true);
  assert.deepStrictEqual(plano(P.api.de('POST', /^\/api\/informes$/).pop().cuerpo.origen.avisos), AVISOS_DELPRO);
});

caso('origen: sin diferencias que cambien los animales no se manda ninguna nota (informe a mano, completo o con diferencias de presentación)', async () => {
  const P = await cargar();
  P.api.cuando('POST', /^\/api\/informes$/, () => ({id: 9, nombre: 'Uno'}));
  const aMano = P.t.nuevoDoc({nombre: 'A mano', def: Object.assign(P.t.defVacia(), {columnas: [{campo: 'Number'}]})});
  await P.t.guardar(aMano, false);
  const completo = traidoDeDelpro(P, {grupos: [], chip: ['Completo', 'ok']});
  await P.t.guardar(completo, false);
  const solo_info = traidoDeDelpro(P, {grupos: [{clase: 'info', titulo: 'Para tener en cuenta', items: [{texto: 'Algo menor.', detalle: []}]}]});
  await P.t.guardar(solo_info, false);
  for (const llamada of P.api.de('POST', /^\/api\/informes$/)) assert.ok(!('origen' in llamada.cuerpo), JSON.stringify(llamada.cuerpo));
});

caso('origen: el aviso de «Guardado» ya no dice «más animales» (los descartados muestran menos)', async () => {
  const P = await cargar();
  const d = traidoDeDelpro(P, {alGuardar: undefined});
  assert.strictEqual(P.t.avisoAlGuardar(d), '');                                              // sin texto no hay aviso
  const fuente = require('fs').readFileSync(require('path').join(__dirname, '..', '..', 'templates', 'informes.html'), 'utf8');
  assert.ok(/AL_GUARDAR_DELPRO = 'Ojo: no trae todo lo que tenía en DelPro, así que puede mostrar animales de más o de menos/.test(fuente));
  assert.ok(!/AL_GUARDAR_DELPRO = [^\n]*puede mostrar más animales\./.test(fuente));          // (la etiqueta «filtro sin traer: puede mostrar más animales» de la lista SÍ es cierta: solo sale con un filtro)
});

caso('origen: al reabrir el informe guardado vuelve el aviso, minimizado, con «Ver el aviso» y el botón de quitarlo', async () => {
  const P = await cargar();
  const nota = {titulo: 'Traído de DelPro', sub: '«Informe de ejemplo»', avisos: AVISOS_DELPRO};
  P.api.cuando('GET', /^\/api\/informes\/7$/, () => ({id: 7, nombre: 'Informe de ejemplo traído', favorito: false, definicion: {columnas: [{campo: 'Number'}]}, origen: nota}));
  await P.t.abrirInforme(7); await P.flush();
  const d = P.t.docAct();
  assert.strictEqual(d.sid, 7);
  assert.ok(d.importado && d.importado.cerrado, 'el aviso vuelve minimizado');
  assert.ok(/Ver el aviso/.test(P.texto('#importado')), P.texto('#importado'));
  assert.ok(/Traído de DelPro/.test(P.texto('#importado')));
  P.click('[data-act="abririmp"]');
  for (const a of AVISOS_DELPRO) assert.ok(P.texto('#importado').indexOf(a) >= 0, a);
  assert.ok(P.$('#importado [data-act="quitarimp"]'), 'se puede dar por revisado');
  assert.ok(!P.t.sucio(d), 'abrirlo no lo deja «sin guardar»');
});

caso('origen: un informe guardado sin nota se abre sin aviso', async () => {
  const P = await cargar();
  P.api.cuando('GET', /^\/api\/informes\/8$/, () => ({id: 8, nombre: 'A mano', favorito: false, definicion: {columnas: [{campo: 'Number'}]}, origen: null}));
  await P.t.abrirInforme(8); await P.flush();
  assert.ok(!P.t.docAct().importado);
  assert.strictEqual(P.texto('#importado'), '');
});

caso('origen: «Quitar este aviso» lo borra del servidor (origen null) y de la pantalla', async () => {
  const P = await cargar();
  const nota = {titulo: 'Traído de DelPro', sub: '', avisos: AVISOS_DELPRO};
  P.api.cuando('GET', /^\/api\/informes\/7$/, () => ({id: 7, nombre: 'Informe de ejemplo traído', favorito: false, definicion: {columnas: [{campo: 'Number'}]}, origen: nota}));
  P.api.cuando('PUT', /^\/api\/informes\/7$/, () => ({id: 7, nombre: 'Informe de ejemplo traído', origen: null}));
  await P.t.abrirInforme(7); await P.flush();
  P.click('[data-act="abririmp"]');
  P.click('[data-act="quitarimp"]'); await P.flush();
  const put = P.api.de('PUT', /^\/api\/informes\/7$/).pop();
  assert.deepStrictEqual(plano(put.cuerpo), {origen: null});                                  // solo la nota: no toca el nombre ni la definición
  assert.ok(!P.t.docAct().importado);
  assert.strictEqual(P.texto('#importado'), '');
});

caso('origen: si no se pudo quitar, el aviso queda y se dice por qué', async () => {
  const P = await cargar();
  P.api.cuando('GET', /^\/api\/informes\/7$/, () => ({id: 7, nombre: 'Uno', favorito: false, definicion: {columnas: [{campo: 'Number'}]}, origen: {titulo: 'Traído de DelPro', sub: '', avisos: AVISOS_DELPRO}}));
  P.api.cuando('PUT', /^\/api\/informes\/7$/, () => P.error(500, {error: 'No se pudo guardar.'}));
  await P.t.abrirInforme(7); await P.flush();
  P.click('[data-act="abririmp"]');
  P.click('[data-act="quitarimp"]'); await P.flush();
  assert.ok(P.t.docAct().importado, 'el aviso sigue');
});

caso('origen: la lista marca los informes traídos con diferencias', async () => {
  const P = await cargar();
  P.api.cuando('GET', /^\/api\/informes$/, () => ({informes: [
    {id: 1, nombre: 'Con aviso', favorito: false, n_columnas: 2, modificado: '2026-11-20T10:00:00', con_aviso: true},
    {id: 2, nombre: 'Sin aviso', favorito: false, n_columnas: 2, modificado: '2026-11-21T10:00:00', con_aviso: false}]}));
  await P.t.refrescarLista();
  P.t.renderList();
  const filas = P.$$('#ltable tbody tr');
  assert.strictEqual(filas.length, 2);
  assert.ok(/traído de DelPro/.test(filas[0].textContent), filas[0].textContent);
  assert.ok(!/traído de DelPro/.test(filas[1].textContent), filas[1].textContent);
});

caso('origen: «Programar envío» avisa que el mensaje va a decir que viene de DelPro con diferencias; sin nota no dice nada', async () => {
  const P = await cargar();
  const nota = {titulo: 'Traído de DelPro', sub: '', avisos: AVISOS_DELPRO};
  P.api.cuando('GET', /^\/api\/informes\/7$/, () => ({id: 7, nombre: 'Informe de ejemplo traído', favorito: false, definicion: {columnas: [{campo: 'Number'}]}, origen: nota}));
  P.api.cuando('GET', /^\/api\/informes\/8$/, () => ({id: 8, nombre: 'A mano', favorito: false, definicion: {columnas: [{campo: 'Number'}]}, origen: null}));
  await P.t.abrirInforme(7); await P.flush();
  P.click('#btn-prog'); await P.flush();
  assert.ok(/se trajo de DelPro con diferencias/.test(P.texto('#prog-nota')), P.texto('#prog-nota'));
  P.click('#prog-cerrar');
  await P.t.abrirInforme(8); await P.flush();
  P.click('#btn-prog'); await P.flush();
  assert.ok(!/DelPro/.test(P.texto('#prog-nota')), P.texto('#prog-nota'));
});

/* ---------------------------------------------------------------------------------------------------------------------------------------- */
(async () => {
  const resultados = [];
  for (const [nombre, fn] of casos) {
    try { await fn(); resultados.push({nombre, ok: true}); }
    catch (e) { resultados.push({nombre, ok: false, error: String((e && e.stack) || e).split('\n').slice(0, 6).join('\n')}); }
  }
  console.log('RESULTADOS ' + JSON.stringify(resultados));
  process.exitCode = resultados.every(r => r.ok) ? 0 : 1;
})();
