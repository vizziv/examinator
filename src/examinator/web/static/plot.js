'use strict';
// Draws <figure class="plot" data-plot='{title, xlabel, ylabel, series}'> as inline SVG.
// Series styles: step, line, bars (x = edges or centres), points. Null x or y is skipped.
(() => {
  const NS = 'http://www.w3.org/2000/svg';
  const W = 300, H = 180, M = {l: 38, r: 8, t: 16, b: 26};

  function svg(tag, attrs, parent) {
    const el = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
    if (parent) parent.append(el);
    return el;
  }
  const num = v => typeof v === 'number' && isFinite(v);
  const fmt = v => String(+v.toPrecision(4));

  function ticks(lo, hi, n) {
    const raw = (hi - lo) / n, mag = 10 ** Math.floor(Math.log10(raw)), f = raw / mag;
    const step = (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * mag;
    const out = [];
    for (let v = Math.ceil(lo / step - 1e-9) * step; v <= hi + step * 1e-9; v += step) out.push(+v.toPrecision(12));
    return out;
  }

  function draw(fig) {
    let plot;
    try { plot = JSON.parse(fig.dataset.plot); } catch (e) { return; }
    const series = (Array.isArray(plot.series) ? plot.series : []).slice(0, 6)
      .filter(s => s && Array.isArray(s.x) && Array.isArray(s.y));
    fig.replaceChildren();
    if (plot.title) fig.append(make('figcaption', {textContent: plot.title}));
    if (series.length > 1) {
      const legend = make('div', {className: 'legend'});
      series.forEach((s, i) => legend.append(
        make('span', {}, make('i', {className: 'c' + (i + 1)}), String(s.name || ''))));
      fig.append(legend);
    }
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (const s of series) {
      for (const v of s.x) if (num(v)) { x0 = Math.min(x0, v); x1 = Math.max(x1, v); }
      for (const v of s.y) if (num(v)) { y0 = Math.min(y0, v); y1 = Math.max(y1, v); }
      if (s.style === 'bars' || s.style === 'step') { y0 = Math.min(y0, 0); y1 = Math.max(y1, 0); }
      if (s.style === 'bars' && s.x.length === s.y.length && s.x.length) { x0 -= 0.5; x1 += 0.5; }
    }
    if (!isFinite(x0) || !isFinite(y0)) return;
    if (x1 === x0) { x0 -= 0.5; x1 += 0.5; }
    if (y1 === y0) { y0 -= 0.5; y1 += 0.5; }
    const pad = (y1 - y0) * 0.04;
    if (y0 !== 0) y0 -= pad;
    y1 += pad;
    const X = v => M.l + (v - x0) / (x1 - x0) * (W - M.l - M.r);
    const Y = v => H - M.b - (v - y0) / (y1 - y0) * (H - M.t - M.b);
    const root = svg('svg', {width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: 'img',
                             'aria-label': plot.title || 'plot'});
    for (const v of ticks(y0, y1, 4)) {
      svg('line', {class: 'gridline', x1: M.l, x2: W - M.r, y1: Y(v), y2: Y(v)}, root);
      svg('text', {x: M.l - 4, y: Y(v) + 3, 'text-anchor': 'end'}, root).textContent = fmt(v);
    }
    for (const v of ticks(x0, x1, 4)) {
      svg('line', {class: 'axis', x1: X(v), x2: X(v), y1: H - M.b, y2: H - M.b + 3}, root);
      svg('text', {x: X(v), y: H - M.b + 13, 'text-anchor': 'middle'}, root).textContent = fmt(v);
    }
    svg('path', {class: 'axis', d: `M${M.l},${M.t}V${H - M.b}H${W - M.r}`}, root);
    if (plot.xlabel) svg('text', {x: W - M.r, y: H - 2, 'text-anchor': 'end'}, root).textContent = plot.xlabel;
    if (plot.ylabel) svg('text', {x: 2, y: 9}, root).textContent = plot.ylabel;

    series.forEach((s, i) => {
      const g = svg('g', {class: `ser ${s.style} c${i + 1}`}, root);
      const tip = (el, text) => { svg('title', {}, el).textContent = (s.name ? s.name + ': ' : '') + text; };
      if (s.style === 'bars') {
        const x = s.x.filter(num);
        const centred = x.length !== s.y.length + 1;
        const w = centred && x.length > 1 ? Math.min(...x.slice(1).map((v, k) => v - x[k])) * 0.8 : 1;
        s.y.forEach((y, k) => {
          if (!num(y)) return;
          const a = centred ? x[k] - w / 2 : x[k], b = centred ? x[k] + w / 2 : x[k + 1];
          if (!num(a) || !num(b)) return;
          const top = Math.min(Y(y), Y(0)), h = Math.abs(Y(y) - Y(0));
          tip(svg('rect', {x: X(a) + 0.5, y: top, width: Math.max(X(b) - X(a) - 1, 0.5), height: h}, g),
              `${fmt(centred ? x[k] : (a + b) / 2)}, ${fmt(y)}`);
        });
      } else if (s.style === 'points') {
        s.x.forEach((x, k) => {
          if (num(x) && num(s.y[k])) tip(svg('circle', {cx: X(x), cy: Y(s.y[k]), r: 2.5}, g), `${fmt(x)}, ${fmt(s.y[k])}`);
        });
      } else {
        let d = '', pen = false;
        s.x.forEach((x, k) => {
          const y = s.y[k];
          if (!num(x) || !num(y)) { pen = false; return; }
          if (!pen) d += `M${X(x).toFixed(1)},${Y(y).toFixed(1)}`;
          else if (s.style === 'step') d += `H${X(x).toFixed(1)}V${Y(y).toFixed(1)}`;
          else d += `L${X(x).toFixed(1)},${Y(y).toFixed(1)}`;
          pen = true;
        });
        svg('path', {d}, g);
      }
    });
    fig.append(root);
  }

  window.drawPlots = root => { for (const fig of (root || document).querySelectorAll('figure.plot')) draw(fig); };
  document.addEventListener('DOMContentLoaded', () => window.drawPlots());
})();
