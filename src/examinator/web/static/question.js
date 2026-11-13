'use strict';
// The question page: the values on it (a draft), actions (buttons and keys, see actions.js),
// redrawing from the server, and the scanned pages beside them.
(() => {
  const main = $('question');
  const sub = main.dataset.sub, q = main.dataset.q;
  const base = `/s/${encodeURIComponent(sub)}/${encodeURIComponent(q)}`;
  const busy = $('busy');
  // A revision goes back exactly as the server drew it.
  const rev = el => el?.dataset.rev || null;

  // --- next / previous submission, following the grid's order (the filter, search and sort) ---
  // The order is read when the page is shown: the grid may have changed it since it was drawn.
  const ids = () => JSON.parse(main.dataset.ids);
  let order = ids(), at = order.indexOf(sub);
  whenShown(() => {
    try { sessionStorage.setItem('gridAt', JSON.stringify({sub, q})); } catch (e) { /* private mode */ }  // the grid's cursor
    order = session('order', null);
    if (!Array.isArray(order)) order = ids();
    at = order.indexOf(sub);
    $('pos').textContent = `${at < 0 ? '–' : at + 1}/${order.length}`;
  });
  function step(by) {     // the page `by` submissions on in the order, or undefined
    let next = order[at + by];
    if (at < 0) {         // not in the order: the nearest one in it, by submission id
      const rank = new Map(ids().map((s, i) => [s, i])), me = rank.get(sub);
      const side = order.filter(s => (by > 0 ? rank.get(s) > me : rank.get(s) < me))
        .sort((a, b) => rank.get(a) - rank.get(b));
      next = by > 0 ? side[0] : side[side.length - 1];
    }
    return next === undefined ? undefined : `${ROOT}/s/${encodeURIComponent(next)}/${encodeURIComponent(q)}`;
  }
  const canGo = by => step(by) !== undefined;
  function go(by) {
    if (canGo(by)) location.href = step(by);
  }

  // --- the filter: does this submission still match it (asked only while one is set) ---
  async function inFilter() {
    const sel = Focus.load();
    if (!Focus.size(sel)) return;
    const r = await request('/focus/values?sub=' + encodeURIComponent(sub));
    if (r.ok) $('unfiltered').hidden = Focus.matches(r.body.values, sel, sub);
  }

  // --- the draft: the values on the page that differ from what they were drawn with ---
  const inputs = () => [...$('ed-fields').querySelectorAll('[data-key]:is(input, textarea)')];
  const input = k => inputs().find(i => i.dataset.key === k);
  const changed = () => inputs().filter(isDraft);
  const dirty = () => changed().length > 0;
  const draftKey = `draft:${sub}:${q}`;
  let left = false;        // saved and leaving: what is typed went with the save
  function touched() {
    $('ed-fields').classList.toggle('dirty', dirty());
    $('ed-fields').querySelector('[data-action=discard]').hidden = !dirty();
    for (const el of inputs()) el.closest('.field').classList.toggle('dirty', isDraft(el));
    try {
      if (dirty()) sessionStorage.setItem(draftKey, JSON.stringify(changed().map(el => ({key: el.dataset.key, rev: rev(el), text: el.value}))));
      else sessionStorage.removeItem(draftKey);
    } catch (e) { /* private mode */ }
  }
  // Drafts kept over values that changed under them, in one message: what each is now.
  const line = Line('ed-msg');
  const label = el => (el.closest('.field')?.querySelector('.flabel') || el.closest('form')?.querySelector('.elabel'))?.textContent.trim();
  const keptText = kept => (kept.length ? Changes.kept(kept.map(k => `${brief(k.now)} (${label(k.el)})`).join(', '))
                                        : "shown as it is now");
  const sayKept = kept => { if (kept.length) line.say(keptText(kept), 'bad'); };
  // The draft left behind in this tab, back on top of the values drawn now.
  function adopt() {
    const kept = [];
    for (const d of [].concat(session(draftKey, []))) {
      const el = input(d.key);
      if (!el || d.text === el.defaultValue) continue;        // gone, or now the same: nothing is lost
      if (d.rev !== rev(el)) kept.push({el, now: el.defaultValue});
      el.value = d.text;
    }
    touched();
    sayKept(kept);
  }
  // Shown later than drawn (prerendered ahead, or back from the back/forward cache): drawn
  // again from the server, so what is drawn is what a press sends against.
  whenShown(late => { adopt(); if (late) refresh().then(sayKept); });
  addEventListener('pageshow', e => { if (e.persisted) refresh().then(sayKept); });
  document.addEventListener('input', e => { if (e.target.closest?.('#ed-fields')) touched(); });
  addEventListener('beforeunload', e => { if (!left && dirty()) e.preventDefault(); });

  // --- drawing again: this page fetched anew, everything the server drew replaced but the
  // drafts and the focus (keepDrafts). One at a time; a call while one is under way asks for
  // another after it, so the last drawing is never older than the last call. Answers the
  // drafts kept over a value that changed.
  let drawing = null, again = false;
  function refresh() {
    if (drawing) { again = true; return drawing; }
    drawing = (async () => {
      let kept = [];
      do { again = false; kept = (await redraw()) || kept; } while (again);
      drawing = null;
      return kept;
    })();
    return drawing;
  }
  const ident = el => el.id || [el.dataset.key, el.closest('[data-path]')?.dataset.path,
                                el.closest('[data-check]')?.dataset.check, el.className].join('|');
  async function redraw() {
    let doc;
    try {
      const res = await fetch(location.href, {headers: {Accept: 'text/html'}});
      if (!res.ok) return null;
      doc = new DOMParser().parseFromString(await res.text(), 'text/html');
    } catch (e) { return null; }
    const work = $('work'), fresh = doc.getElementById('work'), scan = $('scan'), pages = doc.getElementById('scan');
    if (!fresh || !pages) return null;
    for (const d of work.querySelectorAll('details[id]')) {
      const f = fresh.querySelector('#' + CSS.escape(d.id));
      if (f) f.open = d.open;
    }
    const kept = keepDrafts(work, fresh, ident), top = work.scrollTop;
    work.className = fresh.className;
    work.replaceChildren(...fresh.childNodes);
    work.scrollTop = top;
    // the scanned pages, when they changed (their URLs name their content), and what the editors read
    Object.assign(scan.dataset, pages.dataset);
    const sheets = s => [...s.querySelectorAll(':scope > .sheet')];
    const look = list => list.map(s => s.className + s.querySelector('img').getAttribute('src')).join();
    if (look(sheets(scan)) !== look(sheets(pages))) {
      sheets(scan).forEach(s => s.remove());
      scan.append(...sheets(pages));
      toFirst();
    }
    main.dataset.ids = doc.getElementById('question').dataset.ids;
    document.dispatchEvent(new Event('redrawn'));          // the region editor reads its data again
    drawCrops(work);
    Actions.hints(work);
    touched();
    ready();
    inFilter();
    return kept;
  }

  // What a background job started: "running…" while any runs, the outcome in the header, then drawn again.
  let running = 0;
  function watch(job) {
    if (!job) return;
    busy.hidden = !++running;
    waitJob(job).then(j => {
      busy.hidden = !--running;
      const bad = (j.outcomes || []).filter(o => o.status !== 'done' && o.status !== 'skipped');
      if (j.state !== 'done') flash(`${j.state}${j.error ? ': ' + j.error : ''}`);
      else if (bad.length) flash(bad.map(o => `${o.step}: ${o.status}${o.detail ? ' — ' + o.detail : ''}`).join('; '));
      refresh();
    });
  }

  // Every change to this page's rows (values, decisions) is about this submission's question:
  // one subject, so each is sent once the one before it was answered and drawn, against the
  // revisions drawn then. The values' changes say how they went on the form's line.
  function change(path, body, {values = false, leaving = false} = {}) {
    return Changes.send('rows', `${base}/${path}`, body, {
      line: values ? line : null, ends: leaving,
      redraw: async () => keptText(await refresh()),
      done: leaving ? () => { left = true; } : async b => { await refresh(); watch(b.job); }});
  }
  // A field's revision when the change is sent.
  const revAt = k => rev(input(k));
  // Save the values changed here (a person who sets a value verifies it); with `all`,
  // verify this question's other values too (`list`: 'waitingOther' for those read from
  // other questions). The answer, or null when something could not be saved or verified (a
  // conflict, a review marker), so V stays on this submission. `leaving`: the page is left
  // on the answer, so what the save started is not waited for (it runs on the server).
  function save(all, list = 'waiting', leaving = false) {
    const edited = changed().map(el => [el.dataset.key, el.value]);
    const verify = all ? JSON.parse($('parts').dataset[list] || '[]').filter(k => !edited.some(([e]) => e === k)) : [];
    if (!edited.length && !verify.length) return Promise.resolve({});
    return change('save', () => ({values: Object.fromEntries(edited.map(([k, text]) => [k, {text, rev: revAt(k)}])),
                                  verify: verify.map(k => ({key: k, rev: revAt(k)}))}), {values: true, leaving});
  }
  const waiting = () => JSON.parse($('parts').dataset.waiting || '[]').length > 0 || dirty();

  // A decision is made against every field on the page (those from other questions too), at
  // the revision drawn: a field changed since, or not shown here, refuses it.
  const box = check => $('parts').querySelector(`.decide[data-check="${CSS.escape(check)}"]`);
  const decide = (el, extra) => {
    const check = el.closest('.decide').dataset.check;
    return change('decide', () => ({check, ...extra, rev: rev(box(check)),
                                    seen: Object.fromEntries(inputs().map(i => [i.dataset.key, rev(i)]))}));
  };
  // Z / X / C / G grade the first open check (or the only one): its buttons carry those
  // actions, so they show the keys; the other checks' buttons keep the plain ones.
  const GRADES = {'v-t': 'grade-right', 'v-f': 'grade-wrong', 'v-b': 'grade-blank', 'num-set': 'grade-number'};
  function target(kind) {
    const boxes = [...document.querySelectorAll('#parts .decide')];
    const t = boxes.find(b => b.dataset.open) || (boxes.length === 1 ? boxes[0] : null);
    if (kind !== 'number') return t;
    return boxes.find(b => b.dataset.open && b.dataset.kind === 'number') || (t && t.dataset.kind === 'number' ? t : null);
  }
  const targetButton = (cls, kind) => target(kind)?.querySelector('button.' + cls);
  function ready() {
    for (const [cls, name] of Object.entries(GRADES)) {
      const b = targetButton(cls, cls === 'num-set' ? 'number' : null);
      if (b) b.dataset.action = name;
    }
    Actions.hints($('parts'));
    Actions.enable(document);
  }

  // --- the scanned pages: hideable and resizable (both remembered), opening at this question's first page ---
  const scan = $('scan'), split = $('split');
  function scanWidth(px) {
    if (px) setPref('scanWidth', Math.round(Math.min(innerWidth * 0.75, Math.max(200, px))));
    const w = pref('scanWidth', 0), at = scan.scrollTop / (scan.scrollHeight || 1);
    if (w) main.style.setProperty('--scan-w', w + 'px');
    if (px) scan.scrollTop = at * scan.scrollHeight;      // the same place on the pages at the new width
    drawCrops();
  }
  function showScan() {
    const hide = !!pref('hideScan', false);
    main.classList.toggle('no-scan', hide);
    $('scan-toggle').setAttribute('aria-pressed', String(!hide));
    scanWidth();
    toFirst();
  }
  let moved = false;
  function toFirst() {            // until the grader scrolls it themselves
    const first = scan.querySelector('.sheet.on');
    if (first && !moved) scan.scrollTop = first.offsetTop - ($('rg')?.offsetHeight || 0);
    // The pages in view load now, not lazily: a page rendered ahead loads nothing lazily until shown.
    const view = scan.getBoundingClientRect();
    for (const img of scan.querySelectorAll('img.scan[loading=lazy]')) {
      const r = img.getBoundingClientRect();
      if (r.bottom > view.top && r.top < view.bottom) img.loading = 'eager';
    }
  }
  for (const name of ['wheel', 'pointerdown', 'keydown']) scan.addEventListener(name, () => { moved = true; }, {passive: true});
  addEventListener('load', toFirst);
  showScan();
  split.addEventListener('pointerdown', e => {
    split.setPointerCapture(e.pointerId);
    split.classList.add('dragging');
    e.preventDefault();
  });
  split.addEventListener('pointermove', e => {
    if (split.classList.contains('dragging')) scanWidth(e.clientX - scan.getBoundingClientRect().left);
  });
  for (const name of ['pointerup', 'pointercancel']) split.addEventListener(name, () => split.classList.remove('dragging'));
  // Ctrl+scroll over the pages grows or shrinks their panel (over the work area: the crop scale).
  scan.addEventListener('wheel', e => {
    if (!e.ctrlKey) return;
    e.preventDefault();
    scanWidth(scan.getBoundingClientRect().width * Math.exp(-e.deltaY * 0.001));
  }, {passive: false});
  document.addEventListener('click', e => {
    for (const m of document.querySelectorAll('.fmenu[open]')) if (!m.contains(e.target)) m.open = false;
  });

  // Ctrl+scroll over the work area changes the crop scale (a pinch on a trackpad arrives the same way).
  $('work').addEventListener('wheel', e => {
    if (!e.ctrlKey) return;
    e.preventDefault();
    setCropScale(cropScale() * Math.exp(-e.deltaY * 0.001));
  }, {passive: false});
  document.addEventListener('toggle', e => { if (e.target.id === 'others') drawCrops(e.target); }, true);

  // --- actions: buttons (data-action) and keys trigger the same ones ---
  const entryOf = ctx => ctx.target?.closest?.('form.entry');
  const link = name => document.querySelector(`a[data-action="${name}"]`);
  const follow = name => ({available: () => !!link(name), run: () => link(name).click()});
  const hasFields = () => inputs().length > 0;
  const run = (path, body) => Changes.send(`run:${path}:${body.check || ''}`, `${base}/${path}`, body, {done: b => watch(b.job)});
  const actions = {
    'prev-student': {available: () => canGo(-1), run: () => go(-1)},
    'next-student': {available: () => canGo(1), run: () => go(1)},
    'prev-question': follow('prev-question'),
    'next-question': follow('next-question'),
    // Cmd+S and Cmd+Enter in an exam-data entry (the key panel) save that entry.
    save: {available: ctx => !!entryOf(ctx) || hasFields(),
           run: ctx => (entryOf(ctx) ? Entries.send(entryOf(ctx)) : save(false))},
    // Cmd/Ctrl+Enter: an entry saves; elsewhere save and verify everything here. Stays here.
    verify: {available: ctx => !!entryOf(ctx) || hasFields(),
             run: ctx => (entryOf(ctx) ? Entries.send(entryOf(ctx)) : save(true))},
    // V: verify everything here, then the next submission in the filter (as on Scans and Names: "yes, next").
    'verify-next': {available: () => canGo(1) || waiting(),
                    run: () => { const on = canGo(1); return save(true, 'waiting', on).then(b => { if (b && on) go(1); }); }},
    'grade-right': {available: () => !!targetButton('v-t'), run: ({el}) => decide(el || targetButton('v-t'), {value: true})},
    'grade-wrong': {available: () => !!targetButton('v-f'), run: ({el}) => decide(el || targetButton('v-f'), {value: false})},
    'grade-blank': {available: () => !!targetButton('v-b'), run: ({el}) => decide(el || targetButton('v-b'), {blank: true})},
    // G: to the number box (Enter there sets the grade); its set button sets it.
    'grade-number': {available: () => !!targetButton('num-set', 'number'),
                     run: ({el}) => (el ? setNumber(el) : target('number').querySelector('input').focus())},
    edit: {available: hasFields, run: () => inputs()[0].focus()},
    'verify-others': {run: () => save(true, 'waitingOther')},
    // Rare, so in each field's menu: verify or unverify that one value.
    'verify-one': {run: ({el}) => { const k = el.dataset.key; return change('save', () => ({values: {}, verify: [{key: k, rev: revAt(k)}]}), {values: true}); }},
    unverify: {run: ({el}) => { const k = el.dataset.key; return change('unverify', () => ({key: k, rev: revAt(k)}), {values: true}); }},
    'to-default': {run: ({el}) => { const k = el.dataset.key; return change('default', () => ({key: k, rev: revAt(k)}), {values: true}); }},
    restore: {run: ({el}) => {
      const k = el.dataset.key, restore = el.dataset.restore;
      return change('restore', () => ({key: k, restore, rev: revAt(k)}), {values: true});
    }},
    'toggle-scan': {run: () => { setPref('hideScan', !pref('hideScan', false)); showScan(); }},
    discard: {run: () => {
      try { sessionStorage.removeItem(draftKey); } catch (e) { /* private mode */ }
      left = true;
      location.reload();
    }},
    decide: {run: ({el}) => decide(el, {value: el.dataset.value === 'true'})},
    'decide-number': {run: ({el}) => setNumber(el)},
    'decide-blank': {run: ({el}) => decide(el, {blank: true})},
    clear: {run: ({el}) => {
      const check = el.closest('.decide').dataset.check;
      return change('clear', () => ({check, rev: rev(box(check))}));
    }},
    read: {available: ({el}) => !el || !el.disabled, run: () => {
      const all = !!$('read-all')?.checked;
      if (all && !confirm("Read again, replacing values a person set or verified?")) return null;
      return run('read', {include_protected: all});
    }},
    check: {run: ({el}) => run('check', {check: el.dataset.check})},
    // This submission's own pages (none, or the question's: drop them); beyond the exam's: its extra sheets.
    pages: {run: () => {
      const list = $('pg-list');
      const parts = list.value.split(/[\s,]+/).filter(Boolean);
      const pages = parts.every(p => /^\d+$/.test(p)) ? parts.map(Number) : list.value;    // else the server explains
      const clear = !parts.length || String(pages) === list.dataset.default;
      return override({path: list.dataset.path, scope: JSON.parse(scan.dataset.scopes).own, value: pages, clear, sub,
                       rev: () => rev($('pg-list')), take: cur => { $('pg-list').dataset.rev = cur.rev ?? ''; }},
                      {done: () => refresh()});
    }},
  };
  for (const [name, spec] of Object.entries(actions)) Actions.register(name, spec);
  // After the first frame (shown, not drawn ahead), the likeliest first: the browser renders them in turn.
  whenShown(() => requestAnimationFrame(() => setTimeout(() =>
    ahead([step(1), link('next-question')?.href, step(-1), link('prev-question')?.href]))));
  function setNumber(el) {
    const input = el.closest('.decide').querySelector('input');
    if (input.value.trim() === '') return input.focus();
    return decide(el, {value: Number(input.value)});
  }
  // Enter in a number box sets the grade.
  document.addEventListener('keydown', e => {
    if (e.key !== 'Enter' || e.isComposing || !e.target.matches?.('.decide input.num')) return;
    e.preventDefault();
    Actions.trigger('decide-number', {el: e.target, target: e.target});
  });
  document.addEventListener('submit', e => e.preventDefault());
  document.addEventListener('DOMContentLoaded', ready);
  ready();
  inFilter();

  // --- an exam-data edit (key panel, regions): this submission is recomputed first ---
  document.addEventListener('entry-saved', async e => {
    const job = e.detail.job;
    await refresh();
    if (!job) return;
    busy.hidden = !++running;
    await waitJob(job, j => j.done >= 1);
    busy.hidden = !--running;
    await refresh();
  });
})();
