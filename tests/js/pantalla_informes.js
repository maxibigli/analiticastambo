'use strict';
/* Corre el JavaScript REAL de templates/informes.html en Node, con el DOM mínimo de mini_dom.js, un reloj que se mueve a mano y una API
 * simulada (`fetch`). Así las pruebas de la pantalla ejecutan lo que hace de verdad (una respuesta tardía, un doble clic, el foco) en vez de
 * buscar texto en el código fuente.
 *
 *     const P = await cargar();
 *     P.api.cuando('PUT', /\/envio$/, () => ({envio: {...}}));     // lo último que se registra tiene prioridad
 *     P.click('#btn-prog'); await P.flush();
 *
 * Nada de esto toca la red, la base ni ningún canal: todo `fetch` lo atiende un manejador de mentira y lo que no tiene manejador falla.
 */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const {Documento, Evento} = require('./mini_dom.js');

const RAIZ = path.resolve(__dirname, '..', '..');
const HOY = Date.UTC(2026, 10, 24, 12, 0, 0);                    // mar 24/11/2026

/* Lo que la plantilla expone para las pruebas: se agrega al final del script, dentro de su misma función, sin tocar el archivo.
   Lo que no exista se deja pasar como undefined (así una prueba falla por su comportamiento y no por un nombre). */
const EXPORTAR = ['PG', 'DP', 'IA', 'CAMPOS', 'ENVIOS', 'LISTA', 'nuevoDoc', 'activar', 'docAct', 'abrirProg', 'guardarProg', 'probarProg', 'cargarRegistroProg',
  'traerDeDelpro', 'abrirDelpro', 'renderHead', 'renderFilters', 'renderImportado', 'renderProg', 'renderEnvio', 'limpiaDef', 'listaDe', 'condLimpia', 'tipoDe',
  'defVacia', 'foto', 'sucio', 'toast', 'ejecutar', 'abrirInforme', 'guardar', 'avisoAlGuardar', 'renderList', 'abrirComoNuevo', 'origenParaGuardar', 'refrescarLista', 'refrescarEnvios', 'cargarDelpro', 'renderDelpro', 'cerrar', 'aplicarTema', 'temaActual', 'imprimir', 'prepararImpresion'];

const CAMPOS_DE_EJEMPLO = {
  categorias: [
    {nombre: 'Generalidades de animal', campos: [
      {id: 'Number', etiqueta: 'Número del animal', tipo: 'txt', unidad: '', decimales: 0, verificado: true, agrupable: false, aviso: ''},
      {id: 'AnimalGroupNumber', etiqueta: 'Número del grupo', tipo: 'num', unidad: '', decimales: 0, verificado: true, agrupable: true, aviso: ''},
      {id: 'CurrentLactationNumber', etiqueta: 'Nº Lactación', tipo: 'num', unidad: '', decimales: 0, verificado: true, agrupable: true, aviso: ''},
      {id: 'CurrentDIM', etiqueta: 'Días en ordeño', tipo: 'num', unidad: '', decimales: 0, verificado: true, agrupable: false, aviso: ''}]},
    {nombre: 'Reproducción', campos: [
      {id: 'LastCalvingDate', etiqueta: 'Fecha del último parto', tipo: 'fecha', unidad: '', decimales: 0, verificado: true, agrupable: false, aviso: ''}]},
    {nombre: 'Estado', campos: [
      {id: 'InMilk', etiqueta: 'En ordeño', tipo: 'txt', unidad: '', decimales: 0, verificado: false, agrupable: true, aviso: '', valores: ['No', 'Sí']},
      {id: 'CurrentReproductionStatus', etiqueta: 'Estado de la reproducción', tipo: 'txt', unidad: '', decimales: 0, verificado: false, agrupable: true, aviso: '',
       valores: ['Abierta', 'Inseminada', 'Preñada', 'Seca']}]},
    {nombre: 'Ordeño', campos: [
      {id: 'SevenDayAverage', etiqueta: 'Promedio producción diaria de leche 7 d.', tipo: 'num', unidad: 'kg', decimales: 1, verificado: true, agrupable: false, aviso: ''}]},
  ],
  ia: true,
};
const CANALES = [
  {id: 'whatsapp', nombre: 'WhatsApp', disponible: true, configurado: true},
  {id: 'telegram', nombre: 'Telegram', disponible: true, configurado: true},
  {id: 'correo', nombre: 'Correo', disponible: true, configurado: true},
];
const RESULTADO_VACIO = {filas: [], columnas: [], claves_filas: [], grupos: [], agrupar: [], totales: null, truncado: false, detalle: true, segundos: 0.1, sql: ''};

const ok = cuerpo => ({__estado: 200, cuerpo});
const error = (estado, cuerpo) => ({__estado: estado, cuerpo});
function diferido() { let resolver, rechazar; const promesa = new Promise((a, b) => { resolver = a; rechazar = b; }); return {promesa, resolver, rechazar}; }

function manejadoresBase() {
  const programacion = (cuerpo) => ({
    envio: cuerpo ? Object.assign({proximo_texto: 'mié 25/11/2026 08:00'}, cuerpo) : null, canales: CANALES,
    ultima_pasada_texto: 'mar 24/11/2026 11:59', ultima_pasada_hace_s: 60,
  });
  return [
    ['GET', /^\/api\/informes\/campos$/, () => CAMPOS_DE_EJEMPLO],
    ['GET', /^\/api\/informes$/, (c) => c.consulta.indexOf('archivados=1') >= 0 ? {informes: []} : ({informes: [
      {id: 1, nombre: 'Informe de ejemplo uno', favorito: false, n_columnas: 2, modificado: '2026-11-20T10:00:00'},
      {id: 2, nombre: 'Informe de ejemplo dos', favorito: false, n_columnas: 2, modificado: '2026-11-21T10:00:00'}]})],
    ['GET', /^\/api\/informes\/envios$/, () => ({envios: {}})],
    ['POST', /^\/api\/informes\/ejecutar$/, () => RESULTADO_VACIO],
    ['GET', /^\/api\/informes\/\d+\/envio$/, () => programacion(null)],
    ['GET', /^\/api\/informes\/\d+\/envio\/registro$/, () => ({registro: [], ultima_pasada_texto: 'mar 24/11/2026 11:59', ultima_pasada_hace_s: 60})],
    ['PUT', /^\/api\/informes\/\d+\/envio$/, (c) => programacion(c.cuerpo)],
    ['POST', /^\/api\/informes\/\d+\/envio\/probar$/, (c) => ({asunto: 'Asunto', texto: 'TEXTO DEL CORREO', animales: 3, bytes: 0, nombre_archivo: 'x.xlsx', enviado: c.cuerpo.enviar ? {} : undefined})],
  ];
}

async function cargar(opciones) {
  opciones = opciones || {};
  /* INFORMES_HTML = otra plantilla (una copia con un cambio a propósito): sirve para comprobar que las pruebas de verdad detectan el error que dicen detectar. */
  const html = fs.readFileSync(process.env.INFORMES_HTML || path.join(RAIZ, 'templates', 'informes.html'), 'utf8');
  const cuerpo = html.slice(html.indexOf('<body>') + 6, html.indexOf('<script src='));
  const ini = html.indexOf('<script>\n(function(){') + '<script>\n'.length;
  let codigo = html.slice(ini, html.indexOf('</script>', ini))
    .replace('{{ tambo|tojson }}', '"ponderosa"').replace('{{ nombre_tambo|tojson }}', '"La Ponderosa"').replace('{{ embebido|tojson }}', 'false');
  if (/\{\{|\{%/.test(codigo)) throw new Error('Quedó una marca de Jinja en el script de la plantilla');
  const exportar = '\nwindow.__t = {' + EXPORTAR.map(n => n + ': typeof ' + n + " === 'undefined' ? undefined : " + n).join(', ') +
    ", get docs() { return docs; }, get activeId() { return activeId; }};\n";
  codigo = codigo.replace(/\}\)\(\);\s*$/, () => exportar + '})();');

  const doc = new Documento();
  doc.body.innerHTML = cuerpo;

  /* reloj y temporizadores a mano */
  const reloj = {ahora: HOY};
  class FechaFalsa extends Date {
    constructor(...a) { if (a.length) super(...a); else super(reloj.ahora); }
    static now() { return reloj.ahora; }
  }
  let seq = 0, timers = [];
  const setTimeoutFalso = (fn, ms) => { const t = {id: ++seq, a: reloj.ahora + (ms || 0), fn}; timers.push(t); return t.id; };
  const clearTimeoutFalso = id => { timers = timers.filter(t => t.id !== id); };
  function avanzar(ms) {
    const hasta = reloj.ahora + ms;
    for (;;) {
      const sig = timers.filter(t => t.a <= hasta).sort((a, b) => a.a - b.a || a.id - b.id)[0];
      if (!sig) break;
      timers = timers.filter(t => t !== sig);
      reloj.ahora = Math.max(reloj.ahora, sig.a);
      sig.fn();
    }
    reloj.ahora = hasta;
  }

  /* la API simulada */
  const llamadas = [], manejadores = manejadoresBase().map(([metodo, patron, fn]) => ({metodo, patron, fn}));
  const api = {
    llamadas,
    cuando(metodo, patron, fn) { manejadores.unshift({metodo, patron, fn}); },
    de(metodo, patron) { return llamadas.filter(l => l.metodo === metodo && patron.test(l.ruta)); },
  };
  function fetchFalso(url, op) {
    op = op || {};
    const u = new URL(url, 'http://lactia.invalido');
    const metodo = (op.method || 'GET').toUpperCase();
    const ll = {metodo, ruta: u.pathname, consulta: u.search, cuerpo: op.body ? JSON.parse(op.body) : null};
    llamadas.push(ll);
    const m = manejadores.find(h => h.metodo === metodo && h.patron.test(u.pathname));
    if (!m) { ll.sinManejador = true; return Promise.reject(new Error('Sin manejador para ' + metodo + ' ' + u.pathname)); }
    return Promise.resolve(m.fn(ll)).then(r => {
      const estado = r && r.__estado ? r.__estado : 200, cuerpoJson = r && r.__estado ? r.cuerpo : r;
      return {ok: estado < 400, status: estado, headers: {get: () => null}, json: async () => cuerpoJson, blob: async () => ({})};
    });
  }

  /* Lo que la ventana guarda: sus oyentes (para dispararlos a mano, como `beforeprint`), las impresiones pedidas y el almacenamiento local. */
  const oyentesVentana = {}, impresiones = [], datosAlmacen = Object.assign({}, opciones.almacen || {});
  const disparar = tipo => (oyentesVentana[tipo] || []).slice().forEach(fn => fn({type: tipo}));
  const almacen = opciones.almacenRoto
    ? {getItem() { throw new Error('sin almacenamiento'); }, setItem() { throw new Error('sin almacenamiento'); }}
    : {getItem: k => (k in datosAlmacen ? datosAlmacen[k] : null), setItem: (k, v) => { datosAlmacen[k] = String(v); }};
  const ventana = {
    document: doc, console, fetch: fetchFalso, Date: FechaFalsa, URL, setTimeout: setTimeoutFalso, clearTimeout: clearTimeoutFalso,
    addEventListener(tipo, fn) { (oyentesVentana[tipo] = oyentesVentana[tipo] || []).push(fn); }, removeEventListener() {},
    localStorage: almacen,
    /* Como el navegador: avisa `beforeprint`, imprime con lo que haya en pantalla en ese momento y avisa `afterprint`. */
    print() {
      disparar('beforeprint');
      impresiones.push({tema: doc.documentElement.getAttribute('data-theme'), encabezado: (doc.querySelector('#phead') || {textContent: ''}).textContent});
      disparar('afterprint');
    },
  };
  ventana.window = ventana; ventana.self = ventana;
  vm.createContext(ventana);
  vm.runInContext(fs.readFileSync(path.join(RAIZ, 'static', 'js', 'informes_grafico.js'), 'utf8'), ventana, {filename: 'informes_grafico.js'});
  vm.runInContext(codigo, ventana, {filename: 'informes.html (script)'});

  const sel = x => typeof x === 'string' ? doc.querySelector(x) : x;
  const P = {
    doc, api, ventana, reloj, avanzar, diferido, ok, error, CANALES, CAMPOS_DE_EJEMPLO, RESULTADO_VACIO, disparar, impresiones, almacen: datosAlmacen,
    t: ventana.__t,
    $: s => doc.querySelector(s),
    $$: s => doc.querySelectorAll(s),
    texto: s => { const e = sel(s); if (!e) throw new Error('No existe ' + s); return e.textContent; },
    /* Espera a que terminen las promesas pendientes (las respuestas ya resueltas); las que esperan un `diferido` siguen esperando. */
    async flush() { for (let i = 0; i < 12; i++) await new Promise(r => setImmediate(r)); },
    /* Un clic de verdad: el elemento tiene que existir y estar habilitado; el mouse deja el foco en el botón. */
    click(s) {
      const e = sel(s);
      if (!e) throw new Error('No existe ' + s);
      if (e.disabled) return false;
      e.focus();
      e.dispatchEvent(new Evento('click', e));
      return true;
    },
    /* Cambiar el valor de un campo y avisar con «change» (que es lo que hace el navegador al soltar el selector o salir del campo). */
    cambiar(s, valor) {
      const e = sel(s);
      if (!e) throw new Error('No existe ' + s);
      e.focus();
      if (e.type === 'checkbox' || e.type === 'radio') e.checked = !!valor; else e.value = valor;
      e.dispatchEvent(new Evento('change', e));
    },
    /* El texto que tendría el foco: para decir en una prueba «el foco quedó en el selector de campo de la fila 0». */
    foco() { const a = doc.activeElement; return a === doc.body ? 'body' : a.tag + (a.id ? '#' + a.id : '') + [...Object.keys(a.attrs)].filter(k => /^data-/.test(k)).map(k => '[' + k + '="' + a.attrs[k] + '"]').join(''); },
  };
  await P.flush();
  return P;
}

module.exports = {cargar, ok, error, diferido, CANALES, CAMPOS_DE_EJEMPLO};
