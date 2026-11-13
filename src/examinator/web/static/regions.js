'use strict';
// Region editing on the question page's scanned pages, in one of two modes.
// 'regions': field regions as rectangles to drag (across pages too) and resize, saved as
// this submission's own region or as every submission's.
// 'print': the print area, edited with the same controls (no page), saved for this
// submission, for every submission from the same scan file, or for all.
// Both are exam data at a scope (/exam/set, /exam/clear with `scope`).
// Neither changes a value: read again. What is edited is drawn orange; the other, primary, for context.
// The rectangles, their coordinates and controls are rects.js's; the region boxes follow the
// print area's draft.
(() => {
  const scan = document.getElementById('scan');
  const on = document.getElementById('rg-on');
  if (!scan || !on) return;
  const $ = id => document.getElementById(id);
  const sub = document.getElementById('question').dataset.sub;
  // Each region, and the print area: `own`, `scan`, `all` (what each scope has saved, null if
  // nothing; `<scope>_rev` its revision), `base` (the package's), `value` (the one in force).
  const regions = JSON.parse(scan.dataset.regions);
  const pa = JSON.parse(scan.dataset.print);
  const scopes = JSON.parse(scan.dataset.scopes);   // own, scan, all: each scope as the server names it
  pa.draft = [...pa.value];
  const pad = Number(scan.dataset.pad);
  const first = Number(scan.dataset.first);
  const pick = $('rg-pick'), msg = $('rg-msg');
  for (const r of regions) r.draft = r.value && [...r.value];
  let mode = '', sel = null, drag = null;       // mode: '', 'regions' or 'print'
  const pm = () => mode === 'print';

  const {same, back, shift} = Rects;
  const dirty = r => !same(r.draft, r.value);
  const paDirty = () => !same(pa.draft, pa.value);
  // the rectangle being edited, as [page, left, top, right, bottom]: the selected region, or the print area (page 0)
  const cur = () => pm() ? [0, ...pa.draft] : sel && sel.draft;
  const put = d => { if (pm()) pa.draft = d.slice(1); else sel.draft = d; };
  const least = () => (pm() ? 50 : 5);           // the smallest rectangle
  const resize = (d, h, dx, dy) => Rects.resize(d, h, dx, dy, least());
  const say = (text, bad) => { msg.textContent = text; msg.classList.toggle('notice', !!bad); };

  const sheet = page => scan.querySelector(`.sheet[data-index="${page - 1}"]`);
  const place = (box, d) => Rects.place(box, d, pa.draft, pad);

  function draw() {
    for (const el of scan.querySelectorAll('.rbox, .parea')) el.remove();
    if (!mode) return;
    for (const s of scan.querySelectorAll('.sheet')) s.append(Rects.area(pa.draft, {dirty: paDirty(), edit: pm()}));
    for (const r of regions) {
      if (!r.draft) continue;
      const s = sheet(r.draft[0]);
      if (!s) continue;
      const box = Rects.box(r.label, {printed: r.printed, sel: mode === 'regions' && r === sel, dirty: dirty(r)});
      box.dataset.path = r.path;
      place(box, r.draft);
      s.append(box);
    }
    pick.options[pick.selectedIndex]?.classList.toggle('dirty', !!sel && dirty(sel));
    $('rg-save').classList.toggle('dirty', !!sel && dirty(sel));
    $('pa-save').classList.toggle('dirty', paDirty());
    Actions.enable($('rg'));
  }

  // The number boxes from what is edited (except the one being typed in), and which controls show.
  function inputs(typing) {
    fill(cur(), typing);
    pick.hidden = $('rg-p').hidden = $('rg-region-btns').hidden = pm();
    $('rg-pa-btns').hidden = !pm();
    $('rg-clear-own').hidden = !sel || sel.own == null;
    $('rg-clear-all').hidden = !sel || sel.all == null;
    $('pa-save-scan').hidden = !scopes.scan;
    $('pa-clear-own').hidden = pa.own == null;
    $('pa-clear-scan').hidden = pa.scan == null;
    $('pa-clear-all').hidden = pa.all == null;
  }


  function select(path, reveal) {
    sel = regions.find(r => r.path === path) || null;
    if (!sel) return;
    pick.value = sel.path;
    if (!sel.draft) {
      sel.draft = [first, 100, 100, 900, 200];   // a field without a region: start somewhere visible
      say("new region");
    } else say(sel.all != null ? "edited" : '');
    inputs();
    draw();
    if (reveal) scan.querySelector('.rbox.sel')?.scrollIntoView({block: 'nearest', behavior: 'smooth'});
    if (!sheet(sel.draft[0])) say(`page ${sel.draft[0]} not scanned`, true);
  }

  // Editing mode `m` on, or off when it is on already.
  function toggle(m) {
    mode = mode === m ? '' : m;
    scan.classList.toggle('editing', !!mode);     // either mode
    scan.classList.toggle('rg-regions', mode === 'regions');
    scan.classList.toggle('rg-print', pm());
    on.setAttribute('aria-pressed', mode === 'regions');
    $('pa-on').setAttribute('aria-pressed', pm());
    $('rg-panel').hidden = !mode;
    if (mode === 'regions') select(sel ? sel.path : pick.value);
    else {
      say('');
      inputs();
      draw();
    }
    Actions.enable($('rg'));
  }

  // The crops on this page where the server now crops them (`boxes`: by region path, the
  // scanned page and the box on it).
  function recrop(boxes) {
    for (const crop of document.querySelectorAll('.crop[data-region]')) {
      const b = boxes[crop.dataset.region];
      if (!b) continue;
      const src = `${ROOT}/page/${encodeURIComponent(sub)}/${b.index}.jpg`;
      if (crop.dataset.src === src && crop.dataset.box === b.box) continue;
      if (crop.dataset.src !== src) { crop.dataset.src = src; crop.replaceChildren(); }
      crop.dataset.box = b.box;
      drawCrop(crop);
    }
  }

  const WHERE = {own: "this submission", scan: "this scan", all: "all submissions"};

  // Save what is edited (`x`: a region, or the print area) at one scope ('own', 'scan', 'all'),
  // or (`clear`) drop that scope's. A conflict keeps the draft and takes the revision saved now,
  // so that saving again overwrites knowingly. The answer, or null; x has its new levels.
  async function saveAt(x, level, clear) {
    say('…');
    const res = await request(clear ? '/exam/clear' : '/exam/set',
                              {path: x.path, scope: scopes[level], value: clear ? null : x.draft, rev: x[level + '_rev'] ?? null, sub});
    if (res.status === 409) {
      const cur = res.body.current || {};
      x[level + '_rev'] = cur.rev ?? null;
      say(`changed elsewhere${cur.who ? ' by ' + cur.who : ''}${cur.value ? ': ' + JSON.stringify(cur.value) : ''}; save again to overwrite`, true);
      return null;
    }
    if (!res.ok) { say(res.body.error || "failed", true); return null; }
    Object.assign(x, res.body.levels);
    return res.body;
  }

  // The selected region at one scope ('own' or 'all'); `clear`: drop it there.
  async function save(level, clear) {
    const r = sel;
    if (!r) return;
    if (clear && !confirm(level === 'own' ? `Drop this submission's own region for ${r.label}?`
                                          : `Back to the package's region for ${r.label}, for all submissions?`)) return;
    const before = r.value, saved = r.draft;
    const b = await saveAt(r, level, clear);
    if (!b) return;
    const moved = !same(before, r.value), shadowed = !clear && !same(r.value, saved);  // a narrower scope still wins here
    if (moved || !dirty(r)) r.draft = r.value && [...r.value];
    say((clear ? (level === 'own' ? "own region dropped" : "reverted · all submissions") : `saved · ${WHERE[level]}`)
        + (shadowed ? " · this submission keeps its own" : moved ? " · read again to use it" : ''));
    inputs();
    draw();
    recrop(b.boxes || {});
    document.dispatchEvent(new CustomEvent('entry-saved', {detail: {...b, reread: moved ? [r.label] : []}}));
  }

  // The print area at one scope ('own', 'scan': every submission from this scan file, 'all');
  // `clear`: drop it there. The crops beside the fields are redrawn.
  async function savePrint(level, clear) {
    if (clear && !confirm({own: "Drop this submission's own print area?",
                           scan: "Drop this scan's print area, for every submission from it?",
                           all: "Back to the package's print area, for all submissions?"}[level])) return;
    const before = pa.value, saved = pa.draft;
    const b = await saveAt(pa, level, clear);
    if (!b) return;
    pa.draft = [...pa.value];
    recrop(b.boxes || {});
    const moved = !same(before, pa.value), shadowed = !clear && !same(pa.value, saved);
    say(`print area ${clear ? 'dropped' : 'saved'} · ${WHERE[level]}`
        + (shadowed ? ` · this submission keeps ${pa.own != null ? 'its own' : "its scan's"}` : '')
        + (moved ? " · read again to use it" : ''));
    inputs();
    draw();
    if (moved) document.dispatchEvent(new CustomEvent('entry-saved', {detail: {reread: ['print area']}}));
  }

  Actions.register('regions', {available: () => regions.length > 0, run: () => toggle('regions')});
  Actions.register('print-area', {run: () => toggle('print')});
  pick.addEventListener('change', () => select(pick.value, true));
  const fill = Rects.numbers('rg', cur, put, el => { inputs(el); draw(); });
  // Saving takes an unsaved change: never what this submission shows as it is (it may be its own).
  Actions.register('print-save-own', {available: () => pm() && paDirty(), run: () => savePrint('own', false)});
  Actions.register('print-save-scan', {available: () => pm() && paDirty() && !!scopes.scan, run: () => savePrint('scan', false)});
  Actions.register('print-save-all', {available: () => pm() && paDirty(), run: () => savePrint('all', false)});
  Actions.register('print-clear-own', {available: () => pm() && pa.own != null, run: () => savePrint('own', true)});
  Actions.register('print-clear-scan', {available: () => pm() && pa.scan != null, run: () => savePrint('scan', true)});
  Actions.register('print-clear-all', {available: () => pm() && pa.all != null, run: () => savePrint('all', true)});
  Actions.register('print-undo', {available: pm, run: () => {
    pa.draft = [...pa.value];
    say('');
    inputs();
    draw();
  }});
  const regionOn = () => mode === 'regions' && !!sel;
  Actions.register('region-save-own', {available: () => regionOn() && !!sel.draft && dirty(sel), run: () => save('own', false)});
  Actions.register('region-save-all', {available: () => regionOn() && !!sel.draft && dirty(sel), run: () => save('all', false)});
  Actions.register('region-clear-own', {available: () => regionOn() && sel.own != null, run: () => save('own', true)});
  Actions.register('region-clear-all', {available: () => regionOn() && sel.all != null, run: () => save('all', true)});
  Actions.register('region-undo', {available: regionOn, run: () => {
    sel.draft = sel.value && [...sel.value];
    say('');
    inputs();
    draw();
  }});
  Actions.enable($('rg'));

  // --- dragging: inside moves what is edited (a region onto another page too), a handle or edge resizes it ---
  scan.addEventListener('pointerdown', e => {
    if (e.button !== 0 || !mode) return;
    const h = e.target.dataset.h || '';
    if (pm()) {
      const area = e.target.closest('.parea');
      if (!area) return;
      const rect = area.parentElement.getBoundingClientRect();
      drag = {h, x: e.clientX, y: e.clientY, d: cur(), kx: 1000 / rect.width, ky: 1000 / rect.height};  // pixels -> per-mille of the scan
    } else {
      const box = e.target.closest('.rbox');
      if (!box) return;
      if (box.dataset.path !== sel?.path) { select(box.dataset.path); return; }
      const rect = box.parentElement.getBoundingClientRect();
      const [, l, t] = sel.draft, [wl, wt, wr, wb] = pa.draft;
      drag = {h, x: e.clientX, y: e.clientY, d: [...sel.draft],
              // where in the region it was taken hold of, in per-mille of the printed page
              ox: back((e.clientX - rect.left) / rect.width, wl, wr) - l, oy: back((e.clientY - rect.top) / rect.height, wt, wb) - t,
              // pixels -> per-mille of the printed page
              kx: 1e6 / (rect.width * (wr - wl)), ky: 1e6 / (rect.height * (wb - wt))};
    }
    scan.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  scan.addEventListener('pointermove', e => {
    if (!drag) return;
    const dx = (e.clientX - drag.x) * drag.kx, dy = (e.clientY - drag.y) * drag.ky;
    let d;
    if (drag.h) d = resize(drag.d, drag.h, dx, dy);
    else if (pm()) d = shift(drag.d, dx, dy);
    else {
      // the page under the pointer (the one it is on, when between pages)
      const [, l, t, r, b] = drag.d, [wl, wt, wr, wb] = pa.draft;
      const over = [...scan.querySelectorAll('.sheet')].find(s => {
        const q = s.getBoundingClientRect();
        return e.clientY >= q.top && e.clientY < q.bottom;
      }) || sheet(sel.draft[0]);
      const page = Math.min(Number(over.dataset.index) + 1, Number(scan.dataset.pages)), q = over.getBoundingClientRect();
      const w = r - l, h = b - t;
      const nl = Math.min(Math.max(back((e.clientX - q.left) / q.width, wl, wr) - drag.ox, 0), 1000 - w);
      const nt = Math.min(Math.max(back((e.clientY - q.top) / q.height, wt, wb) - drag.oy, 0), 1000 - h);
      d = [page, nl, nt, nl + w, nt + h].map(Math.round);
    }
    const box = !pm() && d[0] === sel.draft[0] && scan.querySelector('.rbox.sel');
    put(d);
    if (box) place(box, d); else draw();      // the print area moves every box with it
    inputs();
  });
  const drop = () => { if (drag) { drag = null; draw(); } };
  scan.addEventListener('pointerup', drop);
  scan.addEventListener('pointercancel', drop);

  // --- keyboard: arrows move by 1 (Shift: 10); with Alt they move the bottom-right corner ---
  Rects.arrows(() => mode && cur(), put, () => { inputs(); draw(); }, least);
  addEventListener('beforeunload', e => { if (regions.some(dirty) || paDirty()) e.preventDefault(); });
})();
