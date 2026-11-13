'use strict';
// Region editing on the question page's scanned pages: field regions as rectangles to
// drag (across pages too) and resize, saved as this submission's own region or as
// every submission's (an exam-wide override, /exam/set). Either changes no value: read again.
//
// A region is [page, left, top, right, bottom] in per-mille of the printed page.
// The scan shows the printed page inside the submission's print area (per-mille
// of the scan), after its cover pages; so, as in pdf/layout.py `Layout.box`:
//   scan fraction x = (wl + (wr - wl) * v / 1000) / 1000, and back
//   v = (1000 * x - wl) * 1000 / (wr - wl).
// The print area is drawn on every sheet and edited like a region (its edges and corners);
// the region boxes follow its draft. It is saved for this submission, for every
// submission from the same scan file, or for all (/s/{sub}/print-area).
(() => {
  const scan = document.getElementById('scan');
  const on = document.getElementById('rg-on');
  if (!scan || !on) return;
  const $ = id => document.getElementById(id);
  const sub = document.getElementById('question').dataset.sub;
  const regions = JSON.parse(scan.dataset.regions);
  const PA = '#print-area';
  const pa = JSON.parse(scan.dataset.print);   // own, scan, all (each saved level), window: the one in use
  pa.draft = [...pa.window];
  const cover = Number(scan.dataset.cover), pad = Number(scan.dataset.pad);
  const first = Number(scan.dataset.first);
  const nums = [...document.querySelectorAll('.rg-n')];
  const pick = $('rg-pick'), msg = $('rg-msg');
  for (const r of regions) r.draft = r.region && [...r.region];
  let sel = null, paSel = false, drag = null;

  // per-mille of the printed page -> fraction of the scan, through a print area (the draft's by default)
  const fx = (v, w = pa.draft) => (w[0] + (w[2] - w[0]) * v / 1000) / 1000;
  const fy = (v, w = pa.draft) => (w[1] + (w[3] - w[1]) * v / 1000) / 1000;
  const clamp = v => Math.min(1000, Math.max(0, Math.round(v)));
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const dirty = r => !same(r.draft, r.region);
  const paDirty = () => !same(pa.draft, pa.window);
  // what the number boxes edit, as [page, left, top, right, bottom] (the print area has no page)
  const cur = () => paSel ? [0, ...pa.draft] : sel && sel.draft;
  const put = d => { if (paSel) pa.draft = d.slice(1); else sel.draft = d; };
  const say = (text, bad) => { msg.textContent = text; msg.classList.toggle('notice', !!bad); };

  const sheet = page => scan.querySelector(`.sheet[data-index="${cover + page - 1}"]`);

  // A box covers what a crop shows: the region and the padding around it (the translucent
  // band). Its inner frame, `.rin`, is the region itself.
  function place(box, d) {
    const w = fx(d[3] + pad) - fx(d[1] - pad), h = fy(d[4] + pad) - fy(d[2] - pad);
    box.style.left = fx(d[1] - pad) * 100 + '%';
    box.style.top = fy(d[2] - pad) * 100 + '%';
    box.style.width = w * 100 + '%';
    box.style.height = h * 100 + '%';
    const px = (fx(d[1]) - fx(d[1] - pad)) / w * 100 + '%', py = (fy(d[2]) - fy(d[2] - pad)) / h * 100 + '%';
    box.firstElementChild.style.inset = `${py} ${px}`;
  }

  // The print area on a sheet: an outline with edges and corners to drag.
  function printArea() {
    const [l, t, r, b] = pa.draft;
    const el = make('div', {className: `parea${paSel ? ' sel' : ''}${paDirty() ? ' dirty' : ''}`},
                    make('span', {className: 'plabel', textContent: 'print area'}));
    Object.assign(el.style, {left: l / 10 + '%', top: t / 10 + '%', width: (r - l) / 10 + '%', height: (b - t) / 10 + '%'});
    for (const h of ['n', 's', 'e', 'w', 'nw', 'ne', 'sw', 'se']) {
      const edge = make('i', {className: 'pe ' + h, title: 'print area'});
      edge.dataset.pe = h;
      el.append(edge);
    }
    return el;
  }

  function draw() {
    for (const el of scan.querySelectorAll('.rbox, .parea')) el.remove();
    if (!scan.classList.contains('editing')) return;
    for (const s of scan.querySelectorAll('.sheet')) s.append(printArea());
    for (const r of regions) {
      if (!r.draft) continue;
      const s = sheet(r.draft[0]);
      if (!s) continue;
      const inner = make('div', {className: 'rin'}, make('span', {className: 'rlabel', textContent: r.label}));
      const box = make('div', {className: `rbox${r.printed ? ' printed' : ''}${r === sel ? ' sel' : ''}${dirty(r) ? ' dirty' : ''}`}, inner);
      box.dataset.path = r.path;
      if (r === sel) for (const h of ['nw', 'ne', 'sw', 'se']) {
        const handle = make('i', {className: 'rh ' + h});
        handle.dataset.h = h;
        inner.append(handle);
      }
      place(box, r.draft);
      s.append(box);
    }
    pick.options[pick.selectedIndex]?.classList.toggle('dirty', paSel ? paDirty() : !!sel && dirty(sel));
    $('rg-save').classList.toggle('dirty', !!sel && dirty(sel));
    $('pa-save').classList.toggle('dirty', paSel && paDirty());
    Actions.enable($('rg'));
  }

  // The number boxes from the selection (except the one being typed in), and which buttons show.
  function inputs(typing) {
    const d = cur();
    nums.forEach((n, i) => { if (n !== typing) n.value = d ? d[i] : ''; });
    if ($('rg-w') !== typing) $('rg-w').value = d ? d[3] - d[1] : '';
    if ($('rg-h') !== typing) $('rg-h').value = d ? d[4] - d[2] : '';
    $('rg-p').hidden = paSel;
    $('rg-region-btns').hidden = paSel;
    $('rg-pa-btns').hidden = !paSel;
    $('rg-clear-own').hidden = !sel || !sel.own;
    $('rg-clear-all').hidden = !sel || !sel.stored;
    $('pa-save-scan').hidden = !pa.source;
    $('pa-clear-own').hidden = !pa.own;
    $('pa-clear-scan').hidden = !pa.scan;
  }

  const paFrom = () => pa.own ? "this submission's" : pa.scan ? "this scan's" : "all submissions'";

  function select(path, reveal) {
    if (path === PA) {
      sel = null;
      paSel = true;
      pick.value = PA;
      say(`print area · ${paFrom()}`);
      inputs();
      draw();
      return;
    }
    sel = regions.find(r => r.path === path) || null;
    if (!sel) return;
    paSel = false;
    pick.value = sel.path;
    if (!sel.draft) {
      sel.draft = [first, 100, 100, 900, 200];   // a field without a region: start somewhere visible
      say("new region");
    } else say(sel.stored ? "edited" : '');
    inputs();
    draw();
    if (reveal) scan.querySelector('.rbox.sel')?.scrollIntoView({block: 'nearest', behavior: 'smooth'});
    if (!sheet(sel.draft[0])) say(`page ${sel.draft[0]} not scanned`, true);
  }

  // The crops on this page show the saved region, with the display padding.
  function recrop(r) {
    for (const crop of document.querySelectorAll('.crop[data-region]')) {
      if (crop.dataset.region !== r.path || !r.region) continue;
      const [page, l, t, rt, b] = r.region, w = pa.window;      // the saved print area, as the server crops
      const box = [fx(l - pad, w), fy(t - pad, w), fx(rt + pad, w), fy(b + pad, w)].map(v => Math.min(1, Math.max(0, v)));
      const src = `${ROOT}/page/${encodeURIComponent(sub)}/${cover + page - 1}.jpg`;
      if (crop.dataset.src !== src) { crop.dataset.src = src; crop.replaceChildren(); }
      crop.dataset.box = box.map(v => v.toFixed(4)).join(',');
      drawCrop(crop);
    }
  }

  // `own`: this submission's region, else every submission's. `clear`: drop it.
  async function save(own, clear) {
    const r = sel;
    if (!r) return;
    if (clear && !confirm(own ? `Drop this submission's own region for ${r.label}?`
                              : `Back to the package's region for ${r.label}, for all submissions?`)) return;
    say('…');
    const res = own
      ? await request(`/s/${encodeURIComponent(sub)}/${encodeURIComponent(document.getElementById('question').dataset.q)}/region`,
                      {label: r.label, value: clear ? null : r.draft, rev: r.own_rev ?? null})
      : await request(clear ? '/exam/clear' : '/exam/set', {path: r.path, value: r.draft, rev: r.rev ?? null, sub});
    if (res.status === 409) {    // keep the draft; saving again overwrites knowingly
      const cur = res.body.current || {};
      if (own) r.own_rev = undefined; else r.rev = cur.rev ?? null;
      say(`changed elsewhere${cur.who ? ' by ' + cur.who : ''}${cur.value ? ': ' + JSON.stringify(cur.value) : ''}; reload`, true);
      return;
    }
    if (!res.ok) { say(res.body.error || "failed", true); return; }
    const shadowed = !own && r.own;      // every submission's changed; this one keeps its own
    if (own) {
      r.own_rev = res.body.rev;
      r.own = res.body.own;
    } else {
      r.rev = res.body.rev;
      r.stored = res.body.overridden;
    }
    if (!shadowed) {
      r.region = res.body.value;
      if (clear || !dirty(r)) r.draft = r.region && [...r.region];
    }
    say((clear ? (own ? "own region dropped" : "reverted · all submissions") : own ? "saved · this submission" : "saved · all submissions")
        + (shadowed ? " · this submission keeps its own" : " · read again to use it"));
    inputs();
    draw();
    if (!shadowed) recrop(r);
    if (own) res.body.reread = [r.label];
    document.dispatchEvent(new CustomEvent('entry-saved', {detail: res.body}));
  }

  // The print area at one level: 'own', 'scan' (every submission from this scan file) or
  // 'all' (the workspace). `clear`: drop that level. The crops beside the fields are redrawn.
  async function savePrint(level, clear) {
    const where = {own: "this submission", scan: "this scan", all: "all submissions"}[level];
    if (clear && !confirm(level === 'own' ? "Drop this submission's own print area?"
                                          : "Drop this scan's print area, for every submission from it?")) return;
    // a level below the one saved still wins for this submission
    const shadowed = !clear && ((level === 'scan' && pa.own) || (level === 'all' && (pa.own || pa.scan)));
    say('…');
    const body = {level, value: clear ? null : pa.draft};
    if (level !== 'all') body.rev = (level === 'own' ? pa.own_rev : pa.scan_rev) ?? null;
    const res = await request(`/s/${encodeURIComponent(sub)}/print-area`, body);
    if (res.status === 409) { say("changed elsewhere; reload", true); return; }
    if (!res.ok) { say(res.body.error || "failed", true); return; }
    const before = pa.window;
    Object.assign(pa, res.body.print_area);
    pa.draft = [...pa.window];
    for (const crop of document.querySelectorAll('.crop[data-region]')) {
      const box = res.body.boxes[crop.dataset.region];
      if (box && crop.dataset.box !== box) { crop.dataset.box = box; drawCrop(crop); }
    }
    const moved = !same(before, pa.window);
    say(`print area ${clear ? 'dropped' : 'saved'} · ${where}`
        + (shadowed ? ` · this submission keeps ${pa.own ? 'its own' : "its scan's"}` : '')
        + (moved ? " · read again to use it" : ''));
    inputs();
    draw();
    if (moved) document.dispatchEvent(new CustomEvent('entry-saved', {detail: {reread: ['print area']}}));
  }

  Actions.register('regions', {run: () => {
    const editing = scan.classList.toggle('editing');
    on.setAttribute('aria-pressed', editing);
    $('rg-panel').hidden = !editing;
    if (editing && !sel && !paSel) select(pick.value); else draw();
    Actions.enable($('rg'));
  }});
  pick.addEventListener('change', () => select(pick.value, true));
  nums.forEach((n, i) => n.addEventListener('input', () => {
    const d = cur();
    if (!d || n.value === '') return;
    put(d.map((v, j) => j !== i ? v : i === 0 ? Math.max(1, Math.round(Number(n.value))) : clamp(Number(n.value))));
    inputs(n);
    draw();
  }));
  // width and height move the right and bottom edges
  for (const [id, lo, hi] of [['rg-w', 1, 3], ['rg-h', 2, 4]]) $(id).addEventListener('input', e => {
    const d = cur();
    if (!d || e.target.value === '') return;
    const n = [...d];
    n[hi] = Math.min(1000, Math.max(n[lo] + 5, n[lo] + Math.round(Number(e.target.value))));
    put(n);
    inputs(e.target);
    draw();
  });
  Actions.register('print-save-own', {available: () => paSel, run: () => savePrint('own', false)});
  Actions.register('print-save-scan', {available: () => paSel && !!pa.source, run: () => savePrint('scan', false)});
  Actions.register('print-save-all', {available: () => paSel, run: () => savePrint('all', false)});
  Actions.register('print-clear-own', {available: () => paSel && !!pa.own, run: () => savePrint('own', true)});
  Actions.register('print-clear-scan', {available: () => paSel && !!pa.scan, run: () => savePrint('scan', true)});
  Actions.register('print-undo', {available: () => paSel, run: () => {
    pa.draft = [...pa.window];
    say('');
    inputs();
    draw();
  }});
  Actions.register('region-save-own', {available: () => !!sel && !!sel.draft, run: () => save(true, false)});
  Actions.register('region-save-all', {available: () => !!sel && !!sel.draft, run: () => save(false, false)});
  Actions.register('region-clear-own', {available: () => !!sel && !!sel.own, run: () => save(true, true)});
  Actions.register('region-clear-all', {available: () => !!sel && !!sel.stored, run: () => save(false, true)});
  Actions.register('region-undo', {available: () => !!sel, run: () => {
    sel.draft = sel.region && [...sel.region];
    say('');
    inputs();
    draw();
  }});

  // --- dragging: the body moves the region (onto another page too), a corner handle resizes it ---
  const back = (x, lo, hi) => (1000 * x - lo) * 1000 / (hi - lo);       // scan fraction -> per-mille of the printed page
  scan.addEventListener('pointerdown', e => {
    if (e.button !== 0) return;
    const edge = e.target.closest('.pe');
    if (edge) {       // the print area: taking hold of an edge or corner selects it, and drags at once
      const rect = edge.closest('.sheet').getBoundingClientRect();
      if (!paSel) select(PA);
      drag = {pa: true, h: edge.dataset.pe, x: e.clientX, y: e.clientY, d: [...pa.draft],
              kx: 1000 / rect.width, ky: 1000 / rect.height};   // pixels -> per-mille of the scan
      scan.setPointerCapture(e.pointerId);
      e.preventDefault();
      return;
    }
    const box = e.target.closest('.rbox');
    if (!box) return;
    if (box.dataset.path !== sel?.path) { select(box.dataset.path); return; }
    const rect = box.parentElement.getBoundingClientRect();
    const [, l, t] = sel.draft, [wl, wt, wr, wb] = pa.draft;
    drag = {h: e.target.dataset.h || '', x: e.clientX, y: e.clientY, d: [...sel.draft],
            // where in the region it was taken hold of, in per-mille of the printed page
            ox: back((e.clientX - rect.left) / rect.width, wl, wr) - l, oy: back((e.clientY - rect.top) / rect.height, wt, wb) - t,
            // pixels -> per-mille of the printed page
            kx: 1e6 / (rect.width * (wr - wl)), ky: 1e6 / (rect.height * (wb - wt))};
    scan.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  scan.addEventListener('pointermove', e => {
    if (!drag) return;
    const dx = (e.clientX - drag.x) * drag.kx, dy = (e.clientY - drag.y) * drag.ky;
    if (drag.pa) {
      const [l, t, r, b] = drag.d, h = drag.h, min = 50;
      pa.draft = [h.includes('w') ? Math.min(clamp(l + dx), r - min) : l,
                  h.includes('n') ? Math.min(clamp(t + dy), b - min) : t,
                  h.includes('e') ? Math.max(clamp(r + dx), l + min) : r,
                  h.includes('s') ? Math.max(clamp(b + dy), t + min) : b];
      inputs();
      draw();
      return;
    }
    const [p, l, t, r, b] = drag.d, [wl, wt, wr, wb] = pa.draft;
    let d;
    if (!drag.h) {
      // the page under the pointer (the one it is on, when between pages)
      const over = [...scan.querySelectorAll('.sheet')].find(s => {
        const q = s.getBoundingClientRect();
        return e.clientY >= q.top && e.clientY < q.bottom;
      }) || sheet(sel.draft[0]);
      const page = Math.max(1, Number(over.dataset.index) - cover + 1), q = over.getBoundingClientRect();
      const w = r - l, h = b - t;
      const nl = Math.min(Math.max(back((e.clientX - q.left) / q.width, wl, wr) - drag.ox, 0), 1000 - w);
      const nt = Math.min(Math.max(back((e.clientY - q.top) / q.height, wt, wb) - drag.oy, 0), 1000 - h);
      d = [page, nl, nt, nl + w, nt + h].map(Math.round);
    } else {
      const h = drag.h, min = 5;
      d = [p, h.includes('w') ? Math.min(clamp(l + dx), r - min) : l,
              h.includes('n') ? Math.min(clamp(t + dy), b - min) : t,
              h.includes('e') ? Math.max(clamp(r + dx), l + min) : r,
              h.includes('s') ? Math.max(clamp(b + dy), t + min) : b];
    }
    const turned = d[0] !== sel.draft[0];
    sel.draft = d;
    const box = scan.querySelector('.rbox.sel');
    if (turned) draw(); else if (box) place(box, d);
    inputs();
  });
  const drop = () => { if (drag) { drag = null; draw(); } };
  scan.addEventListener('pointerup', drop);
  scan.addEventListener('pointercancel', drop);

  // --- keyboard: arrows move by 1 (Shift: 10); with Alt they move the bottom-right corner ---
  document.addEventListener('keydown', e => {
    if (!sel || !sel.draft || !scan.classList.contains('editing')) return;
    if (e.target.matches?.('input, textarea, select') || e.ctrlKey || e.metaKey) return;
    const step = e.shiftKey ? 10 : 1;
    const move = {ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step]}[e.key];
    if (!move) return;
    e.preventDefault();
    const [p, l, t, r, b] = sel.draft;
    const [dx, dy] = move;
    sel.draft = e.altKey
      ? [p, l, t, Math.max(clamp(r + dx), l + 5), Math.max(clamp(b + dy), t + 5)]
      : [p, l + Math.min(Math.max(dx, -l), 1000 - r), t + Math.min(Math.max(dy, -t), 1000 - b),
         r + Math.min(Math.max(dx, -l), 1000 - r), b + Math.min(Math.max(dy, -t), 1000 - b)];
    inputs();
    draw();
  });
  addEventListener('beforeunload', e => { if (regions.some(dirty) || paDirty()) e.preventDefault(); });
})();
