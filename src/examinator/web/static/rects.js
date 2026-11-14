'use strict';
// Rects: editing a rectangle on scanned pages, the same everywhere: the question page's field
// regions and print area (regions.js) and the Names step's name and ID boxes (names.js).
// A rectangle is [page, left, top, right, bottom] in per-mille of the printed page (the print
// area: page 0, per-mille of the scan). The scan shows the printed page inside the print area
// `w` ([l, t, r, b], per-mille of the scan); so, as in pdf/layout.py `Layout.box`:
//   scan fraction x = (wl + (wr - wl) * v / 1000) / 1000, and back
//   v = (1000 * x - wl) * 1000 / (wr - wl).
// What is edited is drawn orange (--accent2), dashed while unsaved; context is primary and inert.
const Rects = (() => {
  const CORNERS = ['nw', 'ne', 'sw', 'se'];
  const fx = (v, w) => (w[0] + (w[2] - w[0]) * v / 1000) / 1000;
  const fy = (v, w) => (w[1] + (w[3] - w[1]) * v / 1000) / 1000;
  const back = (x, lo, hi) => (1000 * x - lo) * 1000 / (hi - lo);       // scan fraction -> per-mille of the printed page
  const clamp = v => Math.min(1000, Math.max(0, Math.round(v)));
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

  // moved by (dx, dy), kept on the page; resized by the handle `h` ('n', 'se', ...), kept above `min`
  function shift([p, l, t, r, b], dx, dy) {
    const mx = Math.round(Math.min(Math.max(dx, -l), 1000 - r)), my = Math.round(Math.min(Math.max(dy, -t), 1000 - b));
    return [p, l + mx, t + my, r + mx, b + my];
  }
  function resize([p, l, t, r, b], h, dx, dy, min = 5) {
    return [p, h.includes('w') ? Math.min(clamp(l + dx), r - min) : l,
               h.includes('n') ? Math.min(clamp(t + dy), b - min) : t,
               h.includes('e') ? Math.max(clamp(r + dx), l + min) : r,
               h.includes('s') ? Math.max(clamp(b + dy), t + min) : b];
  }
  // grips to resize by: corner handles (.rh) and, on the print area, edges (.pe)
  function grips(el, kinds, cls) {
    for (const h of kinds) {
      const g = make('i', {className: `${cls} ${h}`});
      g.dataset.h = h;
      el.append(g);
    }
  }

  // A box covers what a crop shows: the rectangle and the padding `pad` around it (the
  // translucent band); its inner frame, `.rin`, is the rectangle itself, labelled inside.
  function box(label, {sel, dirty, printed} = {}) {
    const inner = make('div', {className: 'rin'}, make('span', {className: 'rlabel', textContent: label}));
    const el = make('div', {className: `rbox${printed ? ' printed' : ''}${sel ? ' sel' : ''}${dirty ? ' dirty' : ''}`}, inner);
    if (sel) grips(inner, CORNERS, 'rh');
    return el;
  }
  // Position a box on its sheet for rectangle `d` through print area `w`.
  function place(el, d, w, pad) {
    const bw = fx(d[3] + pad, w) - fx(d[1] - pad, w), bh = fy(d[4] + pad, w) - fy(d[2] - pad, w);
    el.style.left = fx(d[1] - pad, w) * 100 + '%';
    el.style.top = fy(d[2] - pad, w) * 100 + '%';
    el.style.width = bw * 100 + '%';
    el.style.height = bh * 100 + '%';
    const px = (fx(d[1], w) - fx(d[1] - pad, w)) / bw * 100 + '%', py = (fy(d[2], w) - fy(d[2] - pad, w)) / bh * 100 + '%';
    el.firstElementChild.style.inset = `${py} ${px}`;
  }
  // The print area on a sheet: an outline; `edit`: with edges and corners to drag.
  function area([l, t, r, b], {dirty, edit} = {}) {
    const el = make('div', {className: `parea${dirty ? ' dirty' : ''}`},
                    make('span', {className: 'palabel', textContent: 'print area'}));
    Object.assign(el.style, {left: l / 10 + '%', top: t / 10 + '%', width: (r - l) / 10 + '%', height: (b - t) / 10 + '%'});
    if (edit) {
      grips(el, ['n', 's', 'e', 'w'], 'pe');
      grips(el, CORNERS, 'rh');
    }
    return el;
  }

  // The number boxes: `nums` are page, left, top, right, bottom (class `${prefix}-n`), then
  // width and height (`${prefix}-w`, `${prefix}-h`) that move the right and bottom edges.
  // They edit `get()` through `put(d)`; `changed(el)` redraws, except the box typed in.
  function numbers(prefix, get, put, changed) {
    const nums = [...document.querySelectorAll(`.${prefix}-n`)];
    const wh = [$(`${prefix}-w`), $(`${prefix}-h`)];
    nums.forEach((n, i) => n.addEventListener('input', () => {
      const d = get();
      if (!d || n.value === '') return;
      put(d.map((v, j) => j !== i ? v : i === 0 ? Math.max(1, Math.round(Number(n.value))) : clamp(Number(n.value))));
      changed(n);
    }));
    for (const [el, lo, hi] of [[wh[0], 1, 3], [wh[1], 2, 4]]) el.addEventListener('input', () => {
      const d = get();
      if (!d || el.value === '') return;
      const n = [...d];
      n[hi] = Math.min(1000, Math.max(n[lo] + 5, n[lo] + Math.round(Number(el.value))));
      put(n);
      changed(el);
    });
    // fill them from `d` (null: empty), except the one being typed in
    return (d, typing) => {
      nums.forEach((n, i) => { if (n !== typing) n.value = d ? d[i] : ''; });
      if (wh[0] !== typing) wh[0].value = d ? d[3] - d[1] : '';
      if (wh[1] !== typing) wh[1].value = d ? d[4] - d[2] : '';
    };
  }

  // Arrow keys move what `get()` returns by 1 (Shift: 10); with Alt they move the bottom-right
  // corner, keeping it at least `min()` big. Taken before the page's other keys (a handler on
  // document runs before Actions').
  function arrows(get, put, changed, min = () => 5) {
    document.addEventListener('keydown', e => {
      const d = get();
      if (!d) return;
      if (e.target.matches?.('input, textarea, select') || e.ctrlKey || e.metaKey) return;
      const step = e.shiftKey ? 10 : 1;
      const move = {ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step]}[e.key];
      if (!move) return;
      e.preventDefault();
      put(e.altKey ? resize(d, 'se', ...move, min()) : shift(d, ...move));
      changed();
    });
  }

  // Dragging on `el`: a press that `grab(e)` takes hold of ({d: the rectangle, kx, ky: pixels to
  // per-mille, min?}; else nothing happens) moves it, or by a handle (data-h) resizes it; each move
  // `put`s the new rectangle (`move(hold, dx, dy, e)` when the editor moves it its own way) and
  // calls `moved()`; letting go calls `dropped()`.
  function drag(el, {grab, move, put, moved, dropped}) {
    let hold = null;
    el.addEventListener('pointerdown', e => {
      hold = e.button === 0 ? grab(e) : null;
      if (!hold) return;
      Object.assign(hold, {h: e.target.dataset.h || '', x: e.clientX, y: e.clientY});
      el.setPointerCapture(e.pointerId);
      e.preventDefault();
    });
    el.addEventListener('pointermove', e => {
      if (!hold) return;
      const dx = (e.clientX - hold.x) * hold.kx, dy = (e.clientY - hold.y) * hold.ky;
      put(hold.h ? resize(hold.d, hold.h, dx, dy, hold.min) : move ? move(hold, dx, dy, e) : shift(hold.d, dx, dy));
      moved();
    });
    const end = () => { if (hold) { hold = null; dropped(); } };
    el.addEventListener('pointerup', end);
    el.addEventListener('pointercancel', end);
  }

  // What saving or dropping `what` ("region", "print area", "box") at a scope ('own', 'scan',
  // 'all') says, the same in every editor; `keeps`: what this submission keeps that wins over
  // the scope saved ("its own").
  const WHERE = {own: "this submission", scan: "this scan", all: "all submissions"};
  const said = (what, level, clear, keeps) =>
    (!clear ? `saved · ${WHERE[level]}` : level === 'all' ? "reverted · all submissions" : `${level === 'own' ? 'own' : "scan's"} ${what} dropped`)
    + (keeps ? ` · this submission keeps ${keeps}` : '');
  // Asked before dropping `what` ("region for x", "print area", "boxes") at a scope.
  const dropping = (level, what) => confirm({own: `Drop this submission's own ${what}?`,
                                              scan: `Drop this scan's ${what}, for every submission from it?`,
                                              all: `Back to the package's ${what}, for all submissions?`}[level]);

  return {back, same, shift, resize, box, place, area, numbers, arrows, drag, said, dropping};
})();
