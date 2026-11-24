/* Gráfico de informes (Fase 3): la matemática y el dibujo, sin tocar la pantalla.
 *
 * El servidor (`informes_serie.py`) entrega, por cada serie y cada DÍA, las vacas con lectura válida, los kg de esas vacas y los kg de toda la
 * leche medida. Acá se convierte eso en lo que se ve: la medida (kg por vaca, total, vacas), el intervalo (día, semana o mes), el resumen
 * dentro del intervalo (promedio, suma, mínimo o máximo), las estadísticas y el SVG. Está separado de la plantilla para poder probarlo con
 * Node (tests/js/informes_grafico.test.js): es justo la parte donde se cuelan los errores (la semana que cruza el fin de año, un día roto que
 * arrastra un promedio, un cero tomado por vacío).
 *
 * Todo con fechas AAAA-MM-DD y aritmética en UTC: nada depende de la zona horaria de la PC.
 */
(function (raiz, fabrica) {
  if (typeof module === 'object' && module.exports) module.exports = fabrica();
  else raiz.InformesGrafico = fabrica();
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  const DOW = ['dom', 'lun', 'mar', 'mié', 'jue', 'vie', 'sáb'];
  const MES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 'sep', 'oct', 'nov', 'dic'];
  const UNIDADES = {kg_vaca: ['kg/vaca/día', 1], kg_total: ['kg', 0], vacas: ['vacas', 0]};
  const NOMBRES = {kg_vaca: 'Producción por vaca', kg_total: 'Producción total', vacas: 'Vacas en ordeño'};
  const RESUMEN_NOMBRE = {avg: 'promedio', sum: 'suma', max: 'máximo', min: 'mínimo'};
  const INTERVALO_NOMBRE = {dia: 'día', semana: 'semana', mes: 'mes'};
  const COLORES = 8;
  const DIA = 86400000;
  const pad = n => String(n).padStart(2, '0');
  const aUTC = iso => { const p = iso.split('-'); return Date.UTC(+p[0], +p[1] - 1, +p[2], 12); };
  const aISO = ms => { const d = new Date(ms); return d.getUTCFullYear() + '-' + pad(d.getUTCMonth() + 1) + '-' + pad(d.getUTCDate()); };
  const dmy = iso => iso.slice(8, 10) + '/' + iso.slice(5, 7) + '/' + iso.slice(0, 4);

  /* El día, el lunes de su semana o el primero de su mes (como texto AAAA-MM-DD). */
  function claveIntervalo(iso, intervalo) {
    if (intervalo === 'mes') return iso.slice(0, 8) + '01';
    if (intervalo === 'semana') { const ms = aUTC(iso); return aISO(ms - ((new Date(ms).getUTCDay() + 6) % 7) * DIA); }
    return iso;
  }

  /* Un resumen de varios valores; los vacíos no cuentan, pero un cero es un dato. null si no hay ninguno. */
  function agregar(valores, resumen) {
    const v = valores.filter(x => typeof x === 'number' && isFinite(x));
    if (!v.length) return null;
    if (resumen === 'sum') return v.reduce((a, b) => a + b, 0);
    if (resumen === 'min') return Math.min.apply(null, v);
    if (resumen === 'max') return Math.max.apply(null, v);
    return v.reduce((a, b) => a + b, 0) / v.length;
  }

  /* El valor de una serie en el día i. Los kg por vaca son los kg de las lecturas válidas sobre las vacas con lectura válida; el total
     incluye también las no válidas (esa leche igual se ordeñó). Sin vacas válidas no hay kg por vaca: no se divide por cero. */
  function valorDiario(medida, s, i) {
    if (medida === 'kg_total') return s.kg[i] == null ? null : s.kg[i];
    if (medida === 'vacas') return s.vacas[i] == null ? null : s.vacas[i];
    const vacas = s.vacas[i];
    if (!(vacas > 0) || s.kg_valido[i] == null) return null;
    return s.kg_valido[i] / vacas;
  }

  /* La respuesta del servidor + {medida, resumen, intervalo} -> lo que se dibuja. Los días sin datos del principio y del final se recortan
     (un período «todo» de dos años no tiene que mostrar meses vacíos); un día a medio cargar no cuenta ni como punto ni dentro del promedio
     de su semana, y un hueco en el medio queda como hueco. */
  function armarModelo(datos, cfg) {
    const u = UNIDADES[cfg.medida] || UNIDADES.kg_vaca;
    const base = {claves: [], series: [], incompletos: [], hayDatos: false, unidad: u[0], decimales: u[1], medida: cfg.medida, resumen: cfg.resumen, intervalo: cfg.intervalo};
    const fechas = datos.fechas || [], series = datos.series || [];
    let i0 = -1, i1 = -1;
    for (let i = 0; i < fechas.length; i++) if (series.some(s => s.vacas[i] != null)) { if (i0 < 0) i0 = i; i1 = i; }
    if (i0 < 0) return base;
    const mal = {};
    (datos.incompletos || []).forEach(f => { mal[f] = true; });
    const claves = [], idx = {};
    for (let i = i0; i <= i1; i++) {
      const k = claveIntervalo(fechas[i], cfg.intervalo);
      if (!idx[k]) { idx[k] = []; claves.push(k); }
      idx[k].push(i);
    }
    base.claves = claves;
    base.hayDatos = true;
    base.incompletos = claves.map(k => cfg.intervalo === 'dia' && !!mal[k]);
    base.series = series.map((s, ci) => ({
      id: s.id, etiqueta: s.etiqueta, ci,
      vals: claves.map(k => agregar(idx[k].map(i => mal[fechas[i]] ? null : valorDiario(cfg.medida, s, i)), cfg.resumen))
    }));
    return base;
  }

  /* Último, promedio, mínimo, máximo y cambio (en %) de una serie ya armada. Desde cero no hay porcentaje: null. */
  function estadisticas(vals) {
    const v = vals.filter(x => typeof x === 'number' && isFinite(x));
    if (!v.length) return null;
    const primero = v[0], ultimo = v[v.length - 1];
    let mn = v[0], mx = v[0], suma = 0;
    v.forEach(x => { if (x < mn) mn = x; if (x > mx) mx = x; suma += x; });
    return {ultimo, promedio: suma / v.length, minimo: mn, maximo: mx, cambio: primero ? (ultimo - primero) / primero * 100 : null};
  }

  /* Un eje con números redondos que contiene [min, max]. */
  function escala(min, max, cuenta) {
    if (min === max) { min -= 1; max += 1; }
    const span = max - min, raw = span / cuenta, p = Math.pow(10, Math.floor(Math.log10(raw))), f = raw / p;
    const step = (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * p;
    return {lo: Math.floor(min / step + 1e-9) * step, hi: Math.ceil(max / step - 1e-9) * step, step};
  }

  function etiquetaX(clave, intervalo) {
    if (intervalo === 'mes') return MES[+clave.slice(5, 7) - 1] + ' ' + clave.slice(2, 4);
    return clave.slice(8, 10) + '/' + clave.slice(5, 7);
  }
  function etiquetaTip(clave, intervalo) {
    if (intervalo === 'mes') return MES[+clave.slice(5, 7) - 1] + ' ' + clave.slice(0, 4);
    const dia = DOW[new Date(aUTC(clave)).getUTCDay()] + ' ' + dmy(clave);
    return intervalo === 'semana' ? 'Semana del ' + dia : dia;
  }

  /* Tramos seguidos de datos de una serie: [[i, ...], ...]. Un hueco los corta. */
  function tramos(vals) {
    const out = [];
    let cur = null;
    vals.forEach((v, i) => {
      if (v == null) { cur = null; return; }
      if (!cur) { cur = []; out.push(cur); }
      cur.push(i);
    });
    return out;
  }

  /* El SVG. `o`: {ancho, forma: 'linea'|'area'|'barras', ocultas: {idSerie: true}, fmt(valor, decimales)}.
     Devuelve {svg, geom} o, si no hay nada que dibujar, {svg: '', geom: null, vacio: 'datos' | 'series'}. */
  function dibujar(M, o) {
    const ocultas = o.ocultas || {};
    const fmt = o.fmt || ((v, d) => String(+v.toFixed(d)));
    const vis = M.series.filter(s => !ocultas[s.id]);
    const n = M.claves.length;
    if (!n || !M.series.length) return {svg: '', geom: null, vacio: 'datos'};
    if (!vis.length) return {svg: '', geom: null, vacio: 'series'};
    let mn = Infinity, mx = -Infinity;
    vis.forEach(s => s.vals.forEach(v => { if (v != null) { if (v < mn) mn = v; if (v > mx) mx = v; } }));
    if (mn === Infinity) return {svg: '', geom: null, vacio: 'datos'};
    const forma = o.forma || 'linea';
    const W = Math.max(280, o.ancho || 700), H = W < 520 ? 250 : 360, m = {l: W < 520 ? 42 : 54, r: 16, t: 22, b: 30};
    const iw = W - m.l - m.r, ih = H - m.t - m.b;
    const sc = forma !== 'linea' ? escala(0, mx, 5) : (() => { const pd = (mx - mn) * 0.12 || 1; return escala(mn - pd, mx + pd, 5); })();
    const X = i => forma === 'barras' ? m.l + iw / n * (i + 0.5) : m.l + (n <= 1 ? iw / 2 : i * iw / (n - 1));
    const Y = v => m.t + ih - (v - sc.lo) / (sc.hi - sc.lo) * ih;
    const dec = sc.step >= 1 ? 0 : sc.step >= 0.1 ? 1 : 2;
    const color = s => 'var(--s' + (s.ci % COLORES + 1) + ')';
    let s = '<svg width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + ' ' + H + '" role="img" aria-label="Gráfico de ' + (NOMBRES[M.medida] || 'la producción') + '"><defs>';
    vis.forEach(se => {
      s += '<linearGradient id="g' + se.ci + '" x1="0" y1="0" x2="0" y2="1"><stop offset="0" style="stop-color:' + color(se) + ';stop-opacity:.38"/><stop offset="1" style="stop-color:' + color(se) + ';stop-opacity:0"/></linearGradient>';
    });
    s += '</defs>';
    for (let v = sc.lo; v <= sc.hi + sc.step / 2; v += sc.step) {
      const y = Y(v);
      s += '<line x1="' + m.l + '" x2="' + (W - m.r) + '" y1="' + y.toFixed(1) + '" y2="' + y.toFixed(1) + '" style="stroke:var(--line)" stroke-width="1"/>' +
        '<text x="' + (m.l - 8) + '" y="' + (y + 4).toFixed(1) + '" text-anchor="end" font-size="11" style="fill:var(--subtle)">' + fmt(v, dec) + '</text>';
    }
    s += '<text x="' + m.l + '" y="12" font-size="11" style="fill:var(--muted)">' + M.unidad + '</text>';
    const ticks = W < 520 ? 4 : 7, pasoX = Math.max(1, Math.round((n - 1) / (ticks - 1)));
    for (let i = 0; i < n; i += pasoX) {
      s += '<text x="' + X(i).toFixed(1) + '" y="' + (H - 9) + '" text-anchor="' + (i === 0 ? 'start' : 'middle') + '" font-size="11" style="fill:var(--subtle)">' + etiquetaX(M.claves[i], M.intervalo) + '</text>';
    }
    // Los días a medio cargar (solo en el intervalo diario): una franja tenue, para que el hueco se entienda.
    const ancho = Math.max(3, forma === 'barras' ? iw / n : iw / Math.max(1, n - 1));
    M.incompletos.forEach((mal, i) => {
      if (mal) s += '<rect class="inc" x="' + (X(i) - ancho / 2).toFixed(1) + '" y="' + m.t + '" width="' + ancho.toFixed(1) + '" height="' + ih + '" style="fill:var(--warn);opacity:.14"/>';
    });
    const k = vis.length;
    vis.forEach((se, vi) => {
      const col = color(se);
      if (forma === 'barras') {
        const bw = Math.max(1, iw / n * 0.8 / k);
        se.vals.forEach((v, i) => {
          if (v == null) return;
          const x = X(i) - bw * k / 2 + vi * bw, y = Y(v);
          s += '<rect class="fade" x="' + x.toFixed(1) + '" y="' + y.toFixed(1) + '" width="' + Math.max(1, bw - (bw > 4 ? 1 : 0)).toFixed(1) +
            '" height="' + Math.max(0, Y(sc.lo) - y).toFixed(1) + '" rx="' + (bw > 6 ? 2 : 0) + '" style="fill:' + col + '"/>';
        });
        return;
      }
      const ts = tramos(se.vals);
      const trazo = ts.map(t => t.map((i, j) => (j ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(se.vals[i]).toFixed(1)).join('')).join('');
      if (forma === 'area' || k === 1) {
        const area = ts.filter(t => t.length > 1).map(t => t.map((i, j) => (j ? 'L' : 'M') + X(i).toFixed(1) + ' ' + Y(se.vals[i]).toFixed(1)).join('') +
          'L' + X(t[t.length - 1]).toFixed(1) + ' ' + Y(sc.lo).toFixed(1) + 'L' + X(t[0]).toFixed(1) + ' ' + Y(sc.lo).toFixed(1) + 'Z').join('');
        if (area) s += '<path class="fade" d="' + area + '" fill="url(#g' + se.ci + ')" style="opacity:' + (forma === 'area' ? 1 : 0.55) + '"/>';
      }
      s += '<path class="draw" pathLength="1" d="' + trazo + '" fill="none" style="stroke:' + col + '" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>';
      ts.filter(t => t.length === 1).forEach(t => { s += '<circle class="fade" cx="' + X(t[0]).toFixed(1) + '" cy="' + Y(se.vals[t[0]]).toFixed(1) + '" r="2.6" style="fill:' + col + '"/>'; });
      let ult = -1;
      se.vals.forEach((v, i) => { if (v != null) ult = i; });
      if (ult >= 0) {
        s += '<circle class="fade" cx="' + X(ult).toFixed(1) + '" cy="' + Y(se.vals[ult]).toFixed(1) + '" r="4" style="fill:' + col + '"/>' +
          '<circle class="fade" cx="' + X(ult).toFixed(1) + '" cy="' + Y(se.vals[ult]).toFixed(1) + '" r="8" style="fill:' + col + ';opacity:.2"/>';
      }
    });
    s += '<line id="vl" y1="' + m.t + '" y2="' + (m.t + ih) + '" style="stroke:var(--line-2)" stroke-width="1" visibility="hidden"/><g id="dots"></g>' +
      '<rect id="hit" x="' + m.l + '" y="' + m.t + '" width="' + iw + '" height="' + ih + '" fill="transparent" style="cursor:crosshair;touch-action:pan-y"/></svg>';
    return {svg: s, geom: {W, H, m, iw, ih, n, forma, escala: sc, X, Y, vis}};
  }

  /* De la posición del puntero (en píxeles, desde el borde izquierdo del área del gráfico) al dato que le toca. */
  function indiceEn(x, g) {
    const i = g.forma === 'barras' ? Math.floor(x / (g.iw / g.n)) : Math.round(x / g.iw * (g.n - 1));
    return Math.max(0, Math.min(g.n - 1, i));
  }

  return {claveIntervalo, agregar, valorDiario, armarModelo, estadisticas, escala, etiquetaX, etiquetaTip, dibujar, indiceEn,
          UNIDADES, NOMBRES, RESUMEN_NOMBRE, INTERVALO_NOMBRE, COLORES};
});
