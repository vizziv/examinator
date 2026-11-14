'use strict';
// Region editing on the question page's scanned pages, in one of two modes.
// 'regions': field regions as rectangles to drag (across pages too) and resize, saved as
// this submission's own region or as every submission's.
// 'print': the print area, edited with the same controls (no page), saved for this
// submission, for every submission from the same scan file, or for all.
// Both are exam data at a scope (/exam/set, /exam/clear with `scope`).
// Neither changes a value. What is edited is drawn orange; the other, primary, for context.
// The rectangles, their coordinates, dragging, the scope buttons and their wording are
// rects.js's (as on the Names step's boxes); the region boxes follow the print area's draft.
(() => {
  const scan = document.getElementById('scan');
  const on = document.getElementById('rg-on');
  if (!scan || !on) return;
  const sub = $('question').dataset.sub;
  // Each region, and the print area, at each scope (`levels` in app.py): `own`, `scan`, `all`
  // (what each scope has saved, null if nothing; `<scope>_rev` its revision), `base` (the
  // package's), `value` (the one in force); `draft`, what is edited. Read again whenever the
  // page is drawn again: a draft stays.
  const regions = JSON.parse(scan.dataset.regions);
  const pa = JSON.parse(scan.dataset.print);
  let scopes = JSON.parse(scan.dataset.scopes);     // own, scan, all: each scope as the server names it
  pa.draft = [...pa.value];
  document.addEventListener('redrawn', () => {
    const now = new Map(JSON.parse(scan.dataset.regions).map(r => [r.path, r]));
    for (const r of regions) {
      if (!now.has(r.path)) continue;
      const kept = dirty(r) ? r.draft : null;
      Object.assign(r, now.get(r.path));
      r.draft = kept || (r.value && [...r.value]);
    }
    const kept = paDirty() ? pa.draft : null;
    Object.assign(pa, JSON.parse(scan.dataset.print));
    pa.draft = kept || [...pa.value];
    scopes = JSON.parse(scan.dataset.scopes);
    inputs();
    draw();
  });
  const pad = Number(scan.dataset.pad);
  const first = Number(scan.dataset.first);
  const pick = $('rg-pick'), bar = $('rg');
  for (const r of regions) r.draft = r.value && [...r.value];
  let mode = '', sel = null;       // mode: '', 'regions' or 'print'
  const pm = () => mode === 'print';

  const {same, back} = Rects;
  const dirty = r => !same(r.draft, r.value);
  const paDirty = () => !same(pa.draft, pa.value);
  // the rectangle being edited, as [page, left, top, right, bottom]: the selected region, or the print area (page 0)
  const cur = () => pm() ? [0, ...pa.draft] : sel && sel.draft;
  const put = d => { if (pm()) pa.draft = d.slice(1); else sel.draft = d; };
  const least = () => (pm() ? 50 : 5);           // the smallest rectangle
  const line = Line('rg-msg'), say = (text, tone) => line.say(text, tone);
  const button = action => bar.querySelector(`[data-action="${action}"]`);

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
    button('region-save-own').classList.toggle('dirty', !!sel && dirty(sel));
    button('print-save-own').classList.toggle('dirty', paDirty());
    Actions.enable(bar);
  }

  // The number boxes from what is edited (except the one being typed in), and which controls show.
  function inputs(typing) {
    fill(cur(), typing);
    pick.hidden = $('rg-p').hidden = bar.querySelector('[data-scopes=region]').hidden = pm();
    bar.querySelector('[data-scopes=print]').hidden = !pm();
    button('region-clear-own').hidden = !sel || sel.own == null;
    button('region-clear-all').hidden = !sel || sel.all == null;
    button('print-save-scan').hidden = !scopes.scan;
    for (const level of ['own', 'scan', 'all']) button(`print-clear-${level}`).hidden = pa[level] == null;
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
    if (!sheet(sel.draft[0])) say(`page ${sel.draft[0]} not scanned`, 'bad');
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
    Actions.enable(bar);
  }

  // Save what is edited (`x`: a region, or the print area) at one scope ('own', 'scan', 'all'),
  // or (`clear`) drop that scope's; x then has its new levels (the page is drawn again too: the
  // crops beside the fields move). A refusal keeps the draft and takes what is saved now, so
  // that saving again overwrites knowingly. Says how it went, and what this submission keeps
  // that wins over the scope saved.
  function saveAt(x, what, level, clear) {
    const key = level + '_rev', saved = x.draft;
    if (clear && !Rects.dropping(level, what)) return;
    return override({path: x.path, scope: scopes[level], value: clear ? undefined : [...x.draft], clear, sub, rev: () => x[key] ?? null,
                     take: cur => { x[key] = cur.rev ?? null; x[level] = cur.value ?? null; inputs(); }},
                    {line, done: b => {
                      const before = x.value;
                      Object.assign(x, b.levels);
                      if (x === pa || !same(before, x.value) || !dirty(x)) x.draft = x.value && [...x.value];
                      const keeps = !clear && !same(x.value, saved) ? (x.own != null ? 'its own' : "its scan's") : '';
                      say(Rects.said(x === pa ? 'print area' : 'region', level, clear, keeps));
                      inputs();
                      draw();
                      document.dispatchEvent(new CustomEvent('entry-saved', {detail: b}));
                    }});
  }

  Actions.register('regions', {available: () => regions.length > 0, run: () => toggle('regions')});
  Actions.register('print-area', {run: () => toggle('print')});
  pick.addEventListener('change', () => select(pick.value, true));
  const fill = Rects.numbers('rg', cur, put, el => { inputs(el); draw(); });
  // Saving takes an unsaved change: never what this submission shows as it is (it may be its own).
  const regionOn = () => mode === 'regions' && !!sel;
  const editing = {region: () => regionOn() && !!sel.draft && dirty(sel), print: () => pm() && paDirty()};
  const target = {region: () => sel, print: () => pa};
  const what = {region: () => `region for ${sel.label}`, print: () => 'print area'};
  for (const a of ['region', 'print']) {
    const live = a === 'region' ? regionOn : pm;
    for (const level of ['own', 'scan', 'all']) {
      Actions.register(`${a}-save-${level}`, {available: () => editing[a]() && (level !== 'scan' || !!scopes.scan),
                                              run: () => saveAt(target[a](), what[a](), level, false)});
      Actions.register(`${a}-clear-${level}`, {available: () => live() && target[a]()[level] != null,
                                               run: () => saveAt(target[a](), what[a](), level, true)});
    }
    Actions.register(`${a}-undo`, {available: live, run: () => {
      const x = target[a]();
      x.draft = x.value && [...x.value];
      say('');
      inputs();
      draw();
    }});
  }
  Actions.enable(bar);

  // --- dragging: inside moves what is edited (a region onto another page too), a handle or edge resizes it ---
  Rects.drag(scan, {
    grab: e => {
      if (!mode) return null;
      if (pm()) {
        const area = e.target.closest('.parea');
        if (!area) return null;
        const rect = area.parentElement.getBoundingClientRect();
        return {d: cur(), kx: 1000 / rect.width, ky: 1000 / rect.height, min: least()};  // pixels -> per-mille of the scan
      }
      const box = e.target.closest('.rbox');
      if (!box) return null;
      if (box.dataset.path !== sel?.path) { select(box.dataset.path); return null; }
      const rect = box.parentElement.getBoundingClientRect();
      const [, l, t] = sel.draft, [wl, wt, wr, wb] = pa.draft;
      return {d: [...sel.draft], min: least(),
              // where in the region it was taken hold of, in per-mille of the printed page
              ox: back((e.clientX - rect.left) / rect.width, wl, wr) - l, oy: back((e.clientY - rect.top) / rect.height, wt, wb) - t,
              // pixels -> per-mille of the printed page
              kx: 1e6 / (rect.width * (wr - wl)), ky: 1e6 / (rect.height * (wb - wt))};
    },
    // a region goes to the page under the pointer (the one it is on, when between pages)
    move: (hold, dx, dy, e) => {
      if (pm()) return Rects.shift(hold.d, dx, dy);
      const [, l, t, r, b] = hold.d, [wl, wt, wr, wb] = pa.draft;
      const over = [...scan.querySelectorAll('.sheet')].find(s => {
        const q = s.getBoundingClientRect();
        return e.clientY >= q.top && e.clientY < q.bottom;
      }) || sheet(sel.draft[0]);
      const page = Math.min(Number(over.dataset.index) + 1, Number(scan.dataset.pages)), q = over.getBoundingClientRect();
      const nl = Math.min(Math.max(back((e.clientX - q.left) / q.width, wl, wr) - hold.ox, 0), 1000 - (r - l));
      const nt = Math.min(Math.max(back((e.clientY - q.top) / q.height, wt, wb) - hold.oy, 0), 1000 - (b - t));
      return [page, nl, nt, nl + r - l, nt + b - t].map(Math.round);
    },
    put: d => {
      const box = !pm() && d[0] === sel.draft[0] && scan.querySelector('.rbox.sel');
      put(d);
      if (box) place(box, d); else draw();      // the print area moves every box with it
    },
    moved: () => inputs(),
    dropped: draw,
  });

  // --- keyboard: arrows move by 1 (Shift: 10); with Alt they move the bottom-right corner ---
  Rects.arrows(() => mode && cur(), put, () => { inputs(); draw(); }, least);
  addEventListener('beforeunload', e => { if (regions.some(dirty) || paDirty()) e.preventDefault(); });
})();
