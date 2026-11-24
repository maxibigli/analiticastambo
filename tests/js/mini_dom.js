'use strict';
/* Un DOM mínimo, solo para correr el JavaScript REAL de templates/informes.html con Node sin un navegador.
 *
 * No pretende ser un navegador: tiene lo que la pantalla de informes usa (elementos con atributos, hijos, innerHTML, eventos que burbujean,
 * selectores simples, foco, diálogos) y se comporta como el navegador en lo que importa para las pruebas:
 *   - un elemento deshabilitado no recibe el clic ni el foco;
 *   - reescribir `innerHTML` de un contenedor saca del documento a sus hijos, y si uno tenía el foco, el foco vuelve al <body> (el mismo
 *     problema que sufre un teclado o un lector de pantalla cuando la pantalla se redibuja);
 *   - un elemento dentro de algo `hidden` o de un <dialog> cerrado no se puede enfocar.
 * Selectores: etiqueta, #id, .clase, [atributo], [atributo="valor"], combinados, con el combinador de descendiente (espacio) y comas.
 */

const VACIOS = new Set(['input', 'br', 'hr', 'img', 'meta', 'link', 'source']);
const ENTIDADES = {amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'", nbsp: ' '};
const decodificar = s => String(s).replace(/&(amp|lt|gt|quot|#39|nbsp);/g, (m, k) => ENTIDADES[k]);
const aKebab = s => s.replace(/[A-Z]/g, c => '-' + c.toLowerCase());

class Texto {
  constructor(texto) { this.texto = texto; this.padre = null; }
  get textContent() { return this.texto; }
}

class Evento {
  constructor(tipo, target, extra) {
    this.type = tipo; this.target = target; this.defaultPrevented = false; this._detenido = false;
    Object.assign(this, extra || {});
  }
  preventDefault() { this.defaultPrevented = true; }
  stopPropagation() { this._detenido = true; }
}

/* ---------- selectores ---------- */
function compuesto(s) {
  const c = {tag: null, id: null, clases: [], attrs: []};
  const re = /([a-zA-Z][a-zA-Z0-9-]*)|#([\w-]+)|\.([\w-]+)|\[([\w-]+)(?:=(?:"([^"]*)"|'([^']*)'|([^\]]*)))?\]/y;
  let m, pos = 0;
  while (pos < s.length) {
    re.lastIndex = pos;
    m = re.exec(s);
    if (!m) throw new Error('Selector no soportado: ' + s);
    pos = re.lastIndex;
    if (m[1]) c.tag = m[1].toLowerCase();
    else if (m[2]) c.id = m[2];
    else if (m[3]) c.clases.push(m[3]);
    else c.attrs.push({nombre: m[4], valor: m[5] !== undefined ? m[5] : (m[6] !== undefined ? m[6] : m[7])});
  }
  return c;
}
function partirPor(s, ch) {
  const partes = []; let actual = '', corchetes = 0, comillas = null;
  for (const c of s) {
    if (comillas) { actual += c; if (c === comillas) comillas = null; continue; }
    if (c === '"' || c === "'") { comillas = c; actual += c; continue; }
    if (c === '[') corchetes++;
    if (c === ']') corchetes--;
    if (corchetes === 0 && (ch === ' ' ? /\s/.test(c) : c === ch)) { if (actual.trim()) partes.push(actual.trim()); actual = ''; continue; }
    actual += c;
  }
  if (actual.trim()) partes.push(actual.trim());
  return partes;
}
const cacheSel = new Map();
function parsearSelector(sel) {
  if (!cacheSel.has(sel)) cacheSel.set(sel, partirPor(sel, ',').map(p => partirPor(p, ' ').map(compuesto)));
  return cacheSel.get(sel);
}
function coincideCompuesto(el, c) {
  if (c.tag && el.tag !== c.tag) return false;
  if (c.id && el.id !== c.id) return false;
  const clases = (el.className || '').split(/\s+/);
  if (c.clases.some(k => clases.indexOf(k) < 0)) return false;
  return c.attrs.every(a => el.hasAttribute(a.nombre) && (a.valor === undefined || el.getAttribute(a.nombre) === a.valor));
}
function coincideCadena(el, cadena) {
  if (!coincideCompuesto(el, cadena[cadena.length - 1])) return false;
  let i = cadena.length - 2, p = el.padre;
  while (i >= 0 && p instanceof Elemento) {
    if (coincideCompuesto(p, cadena[i])) i--;
    p = p.padre;
  }
  return i < 0;
}

/* ---------- elementos ---------- */
class Elemento {
  constructor(tag, doc) {
    this.tag = String(tag).toLowerCase(); this.tagName = this.tag.toUpperCase(); this.doc = doc;
    this.attrs = {}; this.hijos = []; this.padre = null; this.oyentes = {}; this._props = {}; this.style = {}; this._html = '';
    this.scrollTop = 0; this.offsetWidth = 0; this.clientWidth = 0;
    this.dataset = new Proxy({}, {
      get: (_, k) => this.attrs['data-' + aKebab(String(k))],
      set: (_, k, v) => { this.attrs['data-' + aKebab(String(k))] = String(v); return true; },
      has: (_, k) => ('data-' + aKebab(String(k))) in this.attrs,
    });
    const el = this;
    this.classList = {
      add: (...ks) => { const s = new Set(el.className.split(/\s+/).filter(Boolean)); ks.forEach(k => s.add(k)); el.className = [...s].join(' '); },
      remove: (...ks) => { el.className = el.className.split(/\s+/).filter(k => k && ks.indexOf(k) < 0).join(' '); },
      contains: k => el.className.split(/\s+/).indexOf(k) >= 0,
      toggle: (k, forzar) => { const hay = el.classList.contains(k), poner = forzar === undefined ? !hay : !!forzar; (poner ? el.classList.add : el.classList.remove)(k); return poner; },
    };
  }
  /* atributos */
  getAttribute(n) { return n in this.attrs ? this.attrs[n] : null; }
  setAttribute(n, v) { this.attrs[n] = String(v); }
  hasAttribute(n) { return n in this.attrs; }
  removeAttribute(n) { delete this.attrs[n]; }
  /* árbol */
  _agregar(h) { if (h.padre) h.padre._sacar(h); h.padre = this; this.hijos.push(h); return h; }
  _sacar(h) { const i = this.hijos.indexOf(h); if (i >= 0) this.hijos.splice(i, 1); h.padre = null; }
  appendChild(h) { return this._agregar(h); }
  remove() { if (this.padre) this.padre._sacar(this); }
  _quitarHijos() {
    for (const h of this.hijos.slice()) {
      h.padre = null;
      const a = this.doc.activeElement;
      if (a && (a === h || (h instanceof Elemento && h.contiene(a)))) this.doc.activeElement = this.doc.body;
    }
    this.hijos = [];
  }
  contiene(otro) { for (let p = otro; p; p = p.padre) if (p === this) return true; return false; }
  get elementos() { const out = []; (function rec(e) { for (const h of e.hijos) if (h instanceof Elemento) { out.push(h); rec(h); } })(this); return out; }
  querySelectorAll(sel) { const cs = parsearSelector(sel); return this.elementos.filter(e => cs.some(c => coincideCadena(e, c))); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  matches(sel) { return parsearSelector(sel).some(c => coincideCadena(this, c)); }
  closest(sel) { for (let e = this; e instanceof Elemento; e = e.padre) if (e.matches(sel)) return e; return null; }
  /* contenido */
  get textContent() { return this.hijos.map(h => h.textContent).join(''); }
  set textContent(t) { this._quitarHijos(); this._html = ''; if (String(t) !== '') this._agregar(new Texto(String(t))); }
  get innerHTML() { return this._html; }
  set innerHTML(h) {
    this._quitarHijos(); this._html = String(h);
    for (const n of parsear(this._html, this.doc)) this._agregar(n);
  }
  /* valores */
  get value() {
    if (this.tag === 'select') {
      const ops = this.querySelectorAll('option');
      const marcada = ops.filter(o => o._props.selected !== undefined ? o._props.selected : o.hasAttribute('selected'))[0] || ops[0];
      return marcada ? (marcada.hasAttribute('value') ? marcada.getAttribute('value') : marcada.textContent) : '';
    }
    if ('value' in this._props) return this._props.value;
    if (this.tag === 'textarea') return this.textContent;
    return this.hasAttribute('value') ? this.getAttribute('value') : '';
  }
  set value(v) {
    if (this.tag === 'select') {
      this.querySelectorAll('option').forEach(o => { o._props.selected = (o.hasAttribute('value') ? o.getAttribute('value') : o.textContent) === String(v); });
    } else this._props.value = String(v);
  }
  get checked() { return 'checked' in this._props ? this._props.checked : this.hasAttribute('checked'); }
  set checked(v) { this._props.checked = !!v; }
  /* eventos y foco */
  addEventListener(tipo, fn) { (this.oyentes[tipo] = this.oyentes[tipo] || []).push(fn); }
  removeEventListener(tipo, fn) { this.oyentes[tipo] = (this.oyentes[tipo] || []).filter(f => f !== fn); }
  dispatchEvent(ev) {
    const camino = []; for (let e = this; e; e = e.padre) camino.push(e);
    camino.push(this.doc);
    for (const nodo of camino) {
      if (!(nodo.oyentes && nodo.oyentes[ev.type])) continue;
      ev.currentTarget = nodo;
      for (const fn of nodo.oyentes[ev.type].slice()) fn.call(nodo, ev);
      if (ev._detenido) break;
    }
    return !ev.defaultPrevented;
  }
  click() { if (this.disabled) return; this.dispatchEvent(new Evento('click', this)); }
  get enfocable() {
    if (this.disabled) return false;
    if (!/^(button|input|select|textarea|a|summary)$/.test(this.tag) && this.getAttribute('tabindex') === null) return false;
    let raiz = this;
    for (let e = this; e; e = e.padre) {
      raiz = e;
      if (e instanceof Elemento && (e.hidden || (e.tag === 'dialog' && !e.open))) return false;
    }
    return raiz === this.doc.raiz;                      // un elemento que ya no está en el documento no se enfoca
  }
  focus() { if (this.enfocable) this.doc.activeElement = this; }
  blur() { if (this.doc.activeElement === this) this.doc.activeElement = this.doc.body; }
  showModal() { this.setAttribute('open', ''); }
  close() { this.removeAttribute('open'); }
  getBoundingClientRect() { return {left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0}; }
  setSelectionRange() {}
}
for (const [prop, attr] of [['id', 'id'], ['className', 'class'], ['title', 'title'], ['name', 'name'], ['type', 'type'], ['href', 'href'], ['download', 'download'], ['placeholder', 'placeholder']]) {
  Object.defineProperty(Elemento.prototype, prop, {get() { return this.getAttribute(attr) || ''; }, set(v) { this.setAttribute(attr, v); }});
}
for (const prop of ['hidden', 'disabled', 'open']) {
  Object.defineProperty(Elemento.prototype, prop, {get() { return this.hasAttribute(prop); }, set(v) { if (v) this.setAttribute(prop, ''); else this.removeAttribute(prop); }});
}

/* ---------- el HTML escrito con innerHTML ---------- */
const RE_ETIQUETA = /<!--[\s\S]*?-->|<\/([A-Za-z][\w-]*)\s*>|<([A-Za-z][\w-]*)((?:\s+[^\s"'>\/=]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s"'>]+))?)*)\s*(\/?)>|([^<]+|<)/g;
const RE_ATRIBUTO = /([^\s"'>\/=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+)))?/g;
function parsear(html, doc) {
  const raiz = new Elemento('div', doc), pila = [raiz];
  RE_ETIQUETA.lastIndex = 0;
  let m;
  while ((m = RE_ETIQUETA.exec(html))) {
    if (m[0].startsWith('<!--')) continue;
    const tope = pila[pila.length - 1];
    if (m[1]) {
      const tag = m[1].toLowerCase();
      for (let i = pila.length - 1; i > 0; i--) if (pila[i].tag === tag) { pila.length = i; break; }
    } else if (m[2]) {
      const el = new Elemento(m[2], doc);
      let a;
      RE_ATRIBUTO.lastIndex = 0;
      while ((a = RE_ATRIBUTO.exec(m[3] || ''))) el.attrs[a[1].toLowerCase()] = decodificar(a[2] !== undefined ? a[2] : a[3] !== undefined ? a[3] : a[4] !== undefined ? a[4] : '');
      tope._agregar(el);
      if (!VACIOS.has(el.tag) && !m[4]) pila.push(el);
    } else tope._agregar(new Texto(decodificar(m[5])));
  }
  const hijos = raiz.hijos.slice();
  hijos.forEach(h => { h.padre = null; });
  return hijos;
}

/* ---------- el documento ---------- */
class Documento {
  constructor() {
    this.raiz = new Elemento('html', this); this.body = new Elemento('body', this);
    this.raiz._agregar(this.body);
    this.documentElement = this.raiz; this.activeElement = this.body; this.oyentes = {};
  }
  createElement(tag) { return new Elemento(tag, this); }
  querySelector(sel) { return this.raiz.querySelector(sel); }
  querySelectorAll(sel) { return this.raiz.querySelectorAll(sel); }
  addEventListener(tipo, fn) { (this.oyentes[tipo] = this.oyentes[tipo] || []).push(fn); }
  removeEventListener(tipo, fn) { this.oyentes[tipo] = (this.oyentes[tipo] || []).filter(f => f !== fn); }
}

module.exports = {Documento, Elemento, Evento, Texto, parsear};
