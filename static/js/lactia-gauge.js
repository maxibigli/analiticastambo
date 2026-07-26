/* ============================================================
   LACTIA — Vúmetro y fuentes de datos
   Vanilla JS, sin dependencias, sin build. Expone window.Lactia
   ------------------------------------------------------------
   El vúmetro NO sabe de dónde viene el dato. Se le pasa un
   número por .set() y listo. El transporte se resuelve aparte,
   en Lactia.source.*, y se puede cambiar sin tocar la vista.
   ============================================================ */

(function (global) {
  'use strict';

  var NS = 'http://www.w3.org/2000/svg';

  function el(tag, attrs) {
    var n = document.createElementNS(NS, tag);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    return n;
  }

  function clamp(v, a, b) { return v < a ? a : v > b ? b : v; }

  function cssVar(node, name, fallback) {
    var v = getComputedStyle(node).getPropertyValue(name).trim();
    return v || fallback;
  }

  /* ----------------------------------------------------------
     Gauge — vúmetro de aguja con escala segmentada
     ---------------------------------------------------------- */

  function Gauge(target, opts) {
    opts = opts || {};
    this.root = typeof target === 'string' ? document.querySelector(target) : target;
    if (!this.root) throw new Error('Lactia.gauge: no se encontró el contenedor ' + target);

    this.min       = opts.min !== undefined ? opts.min : 0;
    this.max       = opts.max !== undefined ? opts.max : 100;
    this.decimals  = opts.decimals !== undefined ? opts.decimals : 1;
    this.unit      = opts.unit || '';
    this.label     = opts.label || '';
    this.segments  = opts.segments || 44;
    this.sweep     = opts.sweep || 240;
    this.peakHold  = opts.peakHold !== false;
    this.peakDecay = opts.peakDecay || 0.006;   // fracción de escala por frame
    this.warnAt    = opts.warnAt !== undefined ? opts.warnAt : 0.78;
    this.dangerAt  = opts.dangerAt !== undefined ? opts.dangerAt : 0.90;
    this.midAt     = opts.midAt !== undefined ? opts.midAt : 0.50;
    this.onZone    = opts.onZone || null;

    this.value = this.min;
    this.peak  = this.min;
    this.zone  = 'low';
    this._raf  = null;

    this._build();
    this._read();
    this.set(opts.value !== undefined ? opts.value : this.min);
  }

  Gauge.prototype._read = function () {
    var r = this.root;
    this.colors = {
      low:    cssVar(r, '--lac-inst-zone-low', '#0072CE'),
      mid:    cssVar(r, '--lac-inst-zone-mid', '#4FC3E8'),
      high:   cssVar(r, '--lac-inst-zone-high', '#F2A900'),
      over:   cssVar(r, '--lac-inst-zone-over', '#E4002B'),
      off:    cssVar(r, '--lac-inst-off', '#0B3A55'),
      offW:   cssVar(r, '--lac-inst-off-warn', '#3A2E12'),
      offD:   cssVar(r, '--lac-inst-off-danger', '#3A1420'),
      well:   cssVar(r, '--lac-inst-well', '#001B2B'),
      muted:  cssVar(r, '--lac-inst-fg-muted', '#5B8CA8')
    };
  };

  Gauge.prototype._build = function () {
    var cx = 150, cy = 150, ro = 120, ri = 100;
    this._geo = { cx: cx, cy: cy, ro: ro, ri: ri };

    var svg = el('svg', {
      viewBox: '0 0 300 212', width: '100%',
      role: 'img', 'aria-label': this.label || 'Vúmetro'
    });

    svg.appendChild(el('circle', { cx: cx, cy: cy, r: 122, style: 'fill:var(--lac-inst-well)' }));

    var g = el('g', {});
    svg.appendChild(g);
    this._ticks = [];
    for (var i = 0; i < this.segments; i++) {
      var t = i / (this.segments - 1);
      var rad = this._angle(t);
      var line = el('line', {
        x1: (cx + ri * Math.cos(rad)).toFixed(1),
        y1: (cy + ri * Math.sin(rad)).toFixed(1),
        x2: (cx + ro * Math.cos(rad)).toFixed(1),
        y2: (cy + ro * Math.sin(rad)).toFixed(1),
        'stroke-width': 5, 'stroke-linecap': 'round'
      });
      g.appendChild(line);
      this._ticks.push(line);
    }

    var lblStyle = 'fill:var(--lac-inst-fg-muted);font-family:var(--lac-font-data);font-size:11px';
    this._lblMin = el('text', { x: 42, y: 196, style: lblStyle });
    this._lblMax = el('text', { x: 232, y: 196, style: lblStyle });
    this._lblMin.textContent = this.min;
    this._lblMax.textContent = this.max;
    svg.appendChild(this._lblMin);
    svg.appendChild(this._lblMax);

    this._peakMark = el('line', { 'stroke-width': 3, 'stroke-linecap': 'round', opacity: this.peakHold ? 1 : 0 });
    svg.appendChild(this._peakMark);

    this._needle = el('line', { x1: cx, y1: cy, x2: cx, y2: 45, 'stroke-width': 3, 'stroke-linecap': 'round' });
    svg.appendChild(this._needle);
    svg.appendChild(el('circle', { cx: cx, cy: cy, r: 9, style: 'fill:var(--lac-inst-zone-low)' }));
    svg.appendChild(el('circle', { cx: cx, cy: cy, r: 3.5, style: 'fill:var(--lac-inst-well)' }));

    this.root.appendChild(svg);
    this.svg = svg;
  };

  Gauge.prototype._angle = function (t) {
    var start = -90 - this.sweep / 2;
    return (start + this.sweep * t) * Math.PI / 180;
  };

  Gauge.prototype._colorAt = function (t) {
    if (t > this.dangerAt) return this.colors.over;
    if (t > this.warnAt)   return this.colors.high;
    if (t > this.midAt)    return this.colors.mid;
    return this.colors.low;
  };

  Gauge.prototype._offAt = function (t) {
    if (t > this.dangerAt) return this.colors.offD;
    if (t > this.warnAt)   return this.colors.offW;
    return this.colors.off;
  };

  Gauge.prototype._zoneAt = function (t) {
    if (t > this.dangerAt) return 'over';
    if (t > this.warnAt)   return 'high';
    if (t < 0.08)          return 'idle';
    return 'normal';
  };

  /* Único punto de entrada de datos. */
  Gauge.prototype.set = function (raw) {
    var v = Number(raw);
    if (!isFinite(v)) return this;
    this.value = clamp(v, this.min, this.max);

    var t = (this.value - this.min) / (this.max - this.min);

    if (this.peakHold) {
      var pt = (this.peak - this.min) / (this.max - this.min);
      if (t > pt) this.peak = this.value;
    }

    var geo = this._geo, rad = this._angle(t), col = this._colorAt(t);
    this._needle.setAttribute('x2', (geo.cx + 108 * Math.cos(rad)).toFixed(1));
    this._needle.setAttribute('y2', (geo.cy + 108 * Math.sin(rad)).toFixed(1));
    this._needle.setAttribute('stroke', col);

    for (var i = 0; i < this._ticks.length; i++) {
      var ti = i / (this._ticks.length - 1);
      this._ticks[i].setAttribute('stroke', ti <= t ? this._colorAt(ti) : this._offAt(ti));
    }

    if (this.peakHold) {
      var ptn = (this.peak - this.min) / (this.max - this.min);
      var prad = this._angle(ptn);
      this._peakMark.setAttribute('x1', (geo.cx + (geo.ri - 6) * Math.cos(prad)).toFixed(1));
      this._peakMark.setAttribute('y1', (geo.cy + (geo.ri - 6) * Math.sin(prad)).toFixed(1));
      this._peakMark.setAttribute('x2', (geo.cx + (geo.ro + 6) * Math.cos(prad)).toFixed(1));
      this._peakMark.setAttribute('y2', (geo.cy + (geo.ro + 6) * Math.sin(prad)).toFixed(1));
      this._peakMark.setAttribute('stroke', this._colorAt(ptn));
      this.peak = Math.max(this.value, this.peak - this.peakDecay * (this.max - this.min));
    }

    this.svg.setAttribute('aria-valuenow', this.value.toFixed(this.decimals));

    var z = this._zoneAt(t);
    if (z !== this.zone) { this.zone = z; if (this.onZone) this.onZone(z, this.value); }

    if (this._mirrors) {
      for (var m = 0; m < this._mirrors.length; m++) this._mirrors[m](this.value, t, this);
    }
    return this;
  };

  Gauge.prototype.text = function () {
    return this.value.toFixed(this.decimals);
  };

  /* Enganchar un readout numérico, una barra, lo que sea. */
  Gauge.prototype.mirror = function (fn) {
    (this._mirrors = this._mirrors || []).push(fn);
    return this;
  };

  Gauge.prototype.resetPeak = function () { this.peak = this.min; return this; };

  /* ----------------------------------------------------------
     LevelBar — barra segmentada horizontal
     ---------------------------------------------------------- */

  function LevelBar(target, opts) {
    opts = opts || {};
    this.root = typeof target === 'string' ? document.querySelector(target) : target;
    if (!this.root) throw new Error('Lactia.levelBar: no se encontró el contenedor');
    this.root.classList.add('lac-levelbar');
    this.n = opts.segments || 28;
    this.warnAt = opts.warnAt !== undefined ? opts.warnAt : 0.78;
    this.dangerAt = opts.dangerAt !== undefined ? opts.dangerAt : 0.90;
    this.midAt = opts.midAt !== undefined ? opts.midAt : 0.50;
    this.root.innerHTML = '';
    this.cells = [];
    for (var i = 0; i < this.n; i++) {
      var c = document.createElement('i');
      this.root.appendChild(c);
      this.cells.push(c);
    }
    this._read();
  }

  LevelBar.prototype._read = Gauge.prototype._read;
  LevelBar.prototype._colorAt = Gauge.prototype._colorAt;
  LevelBar.prototype._offAt = Gauge.prototype._offAt;

  /* Recibe una fracción 0..1 */
  LevelBar.prototype.setRatio = function (t) {
    t = clamp(Number(t) || 0, 0, 1);
    for (var i = 0; i < this.n; i++) {
      var ti = (i + 1) / this.n;
      this.cells[i].style.background = ti <= t ? this._colorAt(ti) : this._offAt(ti);
    }
    return this;
  };

  /* ----------------------------------------------------------
     Fuentes de datos — todas devuelven { on, stop }
     El día que definas el transporte, cambiás una línea.
     ---------------------------------------------------------- */

  function emitter() {
    var subs = [];
    return {
      _emit: function (v) { for (var i = 0; i < subs.length; i++) subs[i](v); },
      on: function (fn) { subs.push(fn); return this; },
      stop: function () { subs.length = 0; }
    };
  }

  var source = {

    /* WebSocket. Node-RED: nodo "websocket out" en /ws/lactia */
    websocket: function (url, opts) {
      opts = opts || {};
      var pick = opts.pick || function (msg) { return msg.value; };
      var e = emitter(), ws, closed = false, retry = opts.retry !== false;

      function connect() {
        ws = new WebSocket(url);
        ws.onmessage = function (ev) {
          var data;
          try { data = JSON.parse(ev.data); } catch (_) { data = ev.data; }
          var v = pick(data);
          if (v !== undefined && v !== null) e._emit(v);
        };
        ws.onclose = function () { if (!closed && retry) setTimeout(connect, 2000); };
        ws.onerror = function () { try { ws.close(); } catch (_) {} };
      }
      connect();

      var stop = e.stop;
      e.stop = function () { closed = true; try { ws.close(); } catch (_) {} stop(); };
      return e;
    },

    /* MQTT sobre WebSocket. Requiere mqtt.js cargado aparte.
       Mosquitto con listener 9001 + protocol websockets. */
    mqtt: function (brokerUrl, topic, opts) {
      opts = opts || {};
      if (!global.mqtt) throw new Error('Lactia.source.mqtt: falta cargar mqtt.js');
      var pick = opts.pick || function (msg) { return msg.value; };
      var e = emitter();
      var client = global.mqtt.connect(brokerUrl, opts.connect || {});
      client.on('connect', function () { client.subscribe(topic); });
      client.on('message', function (_t, payload) {
        var data;
        try { data = JSON.parse(payload.toString()); } catch (_) { data = payload.toString(); }
        var v = pick(data);
        if (v !== undefined && v !== null) e._emit(v);
      });
      var stop = e.stop;
      e.stop = function () { try { client.end(); } catch (_) {} stop(); };
      return e;
    },

    /* Polling REST. El más simple para arrancar. */
    poll: function (url, ms, opts) {
      opts = opts || {};
      var pick = opts.pick || function (msg) { return msg.value; };
      var e = emitter();
      var id = setInterval(function () {
        fetch(url, { cache: 'no-store' })
          .then(function (r) { return r.json(); })
          .then(function (d) { var v = pick(d); if (v !== undefined && v !== null) e._emit(v); })
          .catch(function () {});
      }, ms || 1000);
      var stop = e.stop;
      e.stop = function () { clearInterval(id); stop(); };
      return e;
    },

    /* Señal sintética para maquetar sin hardware. */
    demo: function (opts) {
      opts = opts || {};
      var min = opts.min || 0, max = opts.max || 100, ms = opts.interval || 60;
      var e = emitter(), phase = 0;
      var id = setInterval(function () {
        phase += 0.07;
        var mid = (min + max) / 2, amp = (max - min) * 0.34;
        var v = mid + amp * Math.sin(phase) + amp * 0.3 * Math.sin(phase * 2.7)
                    + (Math.random() - 0.5) * (max - min) * 0.06;
        e._emit(clamp(v, min, max));
      }, ms);
      var stop = e.stop;
      e.stop = function () { clearInterval(id); stop(); };
      return e;
    }
  };

  global.Lactia = {
    gauge:    function (t, o) { return new Gauge(t, o); },
    levelBar: function (t, o) { return new LevelBar(t, o); },
    source:   source,
    Gauge:    Gauge,
    LevelBar: LevelBar
  };

})(window);
