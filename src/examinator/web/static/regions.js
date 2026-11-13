'use strict';
// Region editing on the question page's scanned pages, in one of two modes.
// 'regions': field regions as rectangles to drag (across pages too) and resize, saved as
// this submission's own region or as every submission's (an exam-wide override, /exam/set).
// 'print': the print area, edited with the same controls (no page), saved for this
// submission, for every submission from the same scan file, or for all (/s/{sub}/print-area).
// Neither changes a value: read again. What is edited is drawn orange; the other, primary, for context.
// The rectangles, their coordinates and controls are rects.js's; the region boxes follow the
// print area's draft.
(() => {
  const scan = document.getElementById('scan');
  const on = document.getElementById('rg-on');
  if (!scan || !on) return;
  const $ = id => document.getElementById(id);
  const sub = document.getElementById('question').dataset.sub;
  const regions = JSON.parse(scan.dataset.regions);
  const pa = JSON.parse(scan.dataset.print);   // own, scan, all (each saved level), window: the one in use
  pa.draft = [...pa.window];
  const cover = Number(scan.dataset.cover), pad = Number(scan.dataset.pad);
  const first = Number(scan.dataset.first);
  const pick = $('rg-pick'), msg = $('rg-msg');
  for (const r of regions) r.draft = r.region && [...r.region];
  let mode = '', sel = null, drag = null;       // mode: '', 'regions' or 'print'
  const pm = () => mode === 'print';

  const {fx, fy, same, back, shift} = Rects;
  const dirty = r => !same(r.draft, r.region);
  const paDirty = () => !same(pa.draft, pa.window);
  // the rectangle being edited, as [page, left, top, right, bottom]: the selected region, or the print area (page 0)
  const cur = () => pm() ? [0, ...pa.draft] : sel && sel.draft;
  const put = d => { if (pm()) pa.draft = d.slice(1); else sel.draft = d; };
  const least = () => (pm() ? 50 : 5);           // the smallest rectangle
  const resize = (d, h, dx, dy) => Rects.resize(d, h, dx, dy, least());
  const say = (text, bad) => { msg.textContent = text; msg.classList.toggle('notice', !!bad); };

  const sheet = page => scan.querySelector(`.sheet[data-index="${cover + page - 1}"]`);
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
    $('rg-clear-own').hidden = !sel || !sel.own;
    $('rg-clear-all').hidden = !sel || !sel.stored;
    $('pa-save-scan').hidden = !pa.source;
    $('pa-clear-own').hidden = !pa.own;
    $('pa-clear-scan').hidden = !pa.scan;
  }


  function select(path, reveal) {
    sel = regions.find(r => r.path === path) || null;
    if (!sel) return;
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

  Actions.register('regions', {available: () => regions.length > 0, run: () => toggle('regions')});
  Actions.register('print-area', {run: () => toggle('print')});
  pick.addEventListener('change', () => select(pick.value, true));
  const fill = Rects.numbers('rg', cur, put, el => { inputs(el); draw(); });
  Actions.register('print-save-own', {available: pm, run: () => savePrint('own', false)});
  Actions.register('print-save-scan', {available: () => pm() && !!pa.source, run: () => savePrint('scan', false)});
  Actions.register('print-save-all', {available: pm, run: () => savePrint('all', false)});
  Actions.register('print-clear-own', {available: () => pm() && !!pa.own, run: () => savePrint('own', true)});
  Actions.register('print-clear-scan', {available: () => pm() && !!pa.scan, run: () => savePrint('scan', true)});
  Actions.register('print-undo', {available: pm, run: () => {
    pa.draft = [...pa.window];
    say('');
    inputs();
    draw();
  }});
  const regionOn = () => mode === 'regions' && !!sel;
  Actions.register('region-save-own', {available: () => regionOn() && !!sel.draft, run: () => save(true, false)});
  Actions.register('region-save-all', {available: () => regionOn() && !!sel.draft, run: () => save(false, false)});
  Actions.register('region-clear-own', {available: () => regionOn() && !!sel.own, run: () => save(true, true)});
  Actions.register('region-clear-all', {available: () => regionOn() && !!sel.stored, run: () => save(false, true)});
  Actions.register('region-undo', {available: regionOn, run: () => {
    sel.draft = sel.region && [...sel.region];
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
      const page = Math.max(1, Number(over.dataset.index) - cover + 1), q = over.getBoundingClientRect();
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
