'use strict';
// Names: who wrote each submission. One at a time: its name and ID boxes large (or, with no
// boxes, its first page), beside the best proposal from the roster and why. V verifies the
// proposal and goes to the next unverified submission, W / S show the other proposals, A / D
// the other submissions. The picker under it filters the roster as you type (name, ID or
// email); Enter takes the highlighted person and moves on. Nothing is named until a person
// says so. Unverified and doubtful submissions come first. Each identity field (a name, an ID)
// is an ordinary field: under its crop its value, which can be corrected (Enter) or verified
// (/s/<sub>/@identity/save, as on a question page). "boxes" draws their regions on the page
// (rects.js, as regions are drawn on the question page), saved for this submission or for
// all as exam data (/exam/set, /exam/clear: listed on the Exam data page, revertible there).
// Also the Roster step's table: everyone on it and the submission they are named for.
// The step is shown by scans.js (the 'tab' event).
(() => {
  const main = $('scans');
  if (!main || !$('names')) return;
  let view = null, order = [], k = 0, ci = 0;
  let want = null;        // a submission to show once loaded (a link from Split or the roster: #sub=)
  let taken = {};         // roster key -> the submissions picked for that person (derived by the server)

  const live = () => !$('names').hidden && !!view;
  const row = () => (view && k < order.length ? view.byId[order[k]] : null);
  const cand = () => { const r = row(); return r && r.candidates.length ? r.candidates[ci % r.candidates.length] : null; };
  // How sure a proposal is: both agree is information, one of the two waits on a person, the rest are problems.
  const DOUBT = {agree: '', id: 'unv', email: 'unv', name: 'unv', disagree: 'bad', tie: 'bad', nobody: 'bad',
    unreadable: 'bad', empty: 'bad', unread: ''};
  const verified = r => !!r.who && !r.who.recheck;

  // --- the roster, searched in the browser: sent once with the page ---
  const fold = t => (t || '').normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/['’`]/g, '');
  const ROSTER = JSON.parse(main.dataset.roster || '[]').map(p => ({...p,
    words: fold(p.name).split(/[^\p{L}\p{N}]+/u).filter(Boolean), ids: [fold(p.id), fold(p.email)].filter(Boolean),
    hay: fold(`${p.name} ${p.id} ${p.email}`)}));
  // People matching every word typed, best first: a word starting a name part, the ID or the
  // email counts more than one found inside them; equal matches in roster order. Who is already
  // named for another submission is shown, never used to rank.
  function search(text, limit) {
    const q = fold(text).split(/[\s,;<>]+/).filter(Boolean);
    if (!q.length) return limit ? [] : ROSTER;
    const hits = [];
    for (const [i, p] of ROSTER.entries()) {
      let score = 0;
      for (const t of q) {
        if (p.words.some(w => w.startsWith(t)) || p.ids.some(x => x.startsWith(t))) score += 2;
        else if (p.hay.includes(t)) score += 1;
        else { score = -1; break; }
      }
      if (score > 0) hits.push([score, i, p]);
    }
    if (!limit) return hits.map(h => h[2]);
    hits.sort((a, b) => b[0] - a[0] || a[1] - b[1]);
    return hits.slice(0, limit).map(h => h[2]);
  }
  const elsewhere = (key, sub) => (taken[key] || []).filter(s => s !== sub);

  // `keep`: keep the box drafts (only the saved boxes are taken); `retake`: take the saved boxes as drafts too.
  async function load(keep, retake) {
    const r = await request('/scans/names');
    if (!r.ok) { flash('could not load names'); return; }
    view = r.body;
    view.byId = Object.fromEntries(view.rows.map(x => [x.sub, x]));
    taken = view.taken;
    // The order is fixed when first seen, so verifying does not reshuffle what is ahead.
    const known = new Set(order);
    // New submissions join the end: those with a proposal (V takes it) before those needing typing.
    const fresh = view.rows.filter(x => !known.has(x.sub));
    const typed = x => (x.candidates.length ? 0 : 1);
    order = order.filter(s => view.byId[s]).concat(fresh.sort((a, b) => typed(a) - typed(b)).map(x => x.sub));
    if (k >= order.length || !known.size) k = first();
    if (retake) bx.touched = false;
    for (const c of view.columns) if (!Actions.has(`names-box-add-${c.name}`)) addAction(c.name);
    if (want) jump();
    render();
    if (!$('roster-box').hidden) renderRoster();
  }
  const first = () => nextOpen(order, -1, s => !verified(view.byId[s]));
  function jump() {
    const j = order.indexOf(want);
    want = null;
    if (j >= 0) go(j);
  }
  function go(j) { k = Math.max(0, Math.min(j, order.length)); ci = 0; pick.value = ''; hi = 0; render(); }

  // Naming is said about what is shown, when the key is pressed (or the item clicked): the
  // submission, its pick's revision as seen, and one of {key} (a roster person), {text}
  // (someone typed in), {restore} (undo) or nothing (unname). A statement about a revision
  // that has since changed (Enter twice, a double click) does nothing.
  const about = what => { const r = row(); return r ? {sub: r.sub, rev: r.rev, ...what} : null; };
  async function name(said, next = true) {
    const r = view.byId[said.sub];
    if (!r || r.rev !== said.rev) return;
    const here = row() === r, before = r.who ? r.who.name : '', was = r.rev;
    const res = await request('/scans/who', said);
    if (!res.ok) { flash(res.status === 409 ? 'changed elsewhere' : res.body.error || 'failed'); await load(); return; }
    const now = res.body.who;
    report(said.sub, now.name, before, was == null ? {sub: said.sub, rev: now.rev} : {sub: said.sub, rev: now.rev, restore: was});
    await load();
    if (next && here && row() && row().sub === said.sub) go(nextOpen(order, k, s => !verified(view.byId[s])));
  }
  // One line says what was done, with an undo: itself a statement, made now.
  function report(sub, who, before, undoing) {
    const undo = make('button', {type: 'button', className: 'quiet', textContent: 'undo', title: before ? `back to ${before}` : 'unname it'});
    undo.addEventListener('click', () => S.enqueue(async () => {
      $('w-said').replaceChildren();
      await name(undoing, false);
      go(order.indexOf(sub));
    }));
    $('w-said').replaceChildren(`${sub} → ${who || 'nobody'} · `, undo);
  }

  const S = steps(live, () => { if (view) render(); });
  S.register('names-verify', {available: () => !!cand(), bind: () => about({key: cand().key}), run: said => name(said)});
  S.register('names-down', {available: () => !!row() && row().candidates.length > 1, run: () => { ci += 1; render(); }});
  S.register('names-up', {available: () => !!row() && row().candidates.length > 1,
    run: () => { ci += row().candidates.length - 1; render(); }});
  S.register('names-skip', {available: () => k < order.length, run: () => go(k + 1)});
  S.register('names-back', {available: () => k > 0, run: () => go(k - 1)});
  S.register('names-type', {available: () => !!row(), run: () => { pick.focus(); pick.select(); }});
  S.register('names-read', {available: () => !!main.dataset.model && !!view && view.boxed, long: true, run: async () => {
    const r = await request('/scans/names/read', {});
    if (!r.ok) { flash(r.body.error || 'failed'); return; }
    $('msg').hidden = !r.body.n;
    $('msg').textContent = r.body.n ? `reading ${r.body.n}…` : '';
    const job = await waitJob(r.body.job);
    const failed = (job.outcomes || []).length;
    $('msg').textContent = job.state !== 'done' ? `read names ${job.state}` : failed ? `${failed} failed` : '';
    $('msg').hidden = !$('msg').textContent;
    order = [];         // new readings: doubtful ones first again
    await load();
  }});

  // --- the picker: the proposals while empty, else the roster people matching what is typed ---
  const pick = $('w-pick'), list = $('w-list');
  const SHOWN = 8;
  let items = [], hi = 0;
  function choices() {
    const r = row(), text = pick.value.trim();
    if (!r) return [];
    if (!text) return r.candidates.map(c => ({key: c.key, name: c.name, id: c.id, why: c.why, taken: c.taken}));
    const found = search(text, SHOWN).map(p => ({key: p.key, name: p.name,
      id: p.id, email: p.email, taken: elsewhere(p.key, r.sub)[0] || null}));
    return found.length ? found : [{free: text}];       // someone not on the roster: kept as typed
  }
  function renderList() {
    const open = document.activeElement === pick && !!row();
    items = open ? choices() : [];
    if (!pick.value.trim() && items.length) hi = ci % items.length;
    hi = Math.min(hi, Math.max(0, items.length - 1));
    list.hidden = !items.length;
    pick.setAttribute('aria-expanded', String(!list.hidden));
    list.replaceChildren(...items.map((x, j) => {
      const li = make('li', {role: 'option', className: (j === hi ? 'on' : '') + (x.taken ? ' taken' : '')},
        ...(x.free ? [make('span', {className: 'dim', textContent: 'not on the roster: '}), make('b', {textContent: x.free})]
          : [make('b', {textContent: x.name}), make('span', {className: 'mono', textContent: x.id}),
             x.why ? make('span', {className: 'dim', textContent: x.why}) : '',
             // still pickable: picking flags both submissions "name twice"
             x.taken ? tag('bad', `already ${x.taken}`, 'already named for that submission') : '']));
      li.setAttribute('aria-selected', String(j === hi));
      li.addEventListener('mousedown', e => { e.preventDefault(); choose(j); });     // the box keeps the focus
      return li;
    }));
  }
  function choose(j) {
    const x = items[j], said = x && about(x.free ? {text: x.free} : {key: x.key});
    if (said) S.enqueue(() => name(said));
  }
  pick.addEventListener('input', () => { hi = 0; renderList(); });
  pick.addEventListener('focus', renderList);
  pick.addEventListener('blur', () => { list.hidden = true; pick.setAttribute('aria-expanded', 'false'); });
  pick.addEventListener('keydown', e => {
    if (e.isComposing) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      if (!items.length) return;
      hi = (hi + (e.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length;
      if (pick.value.trim()) renderList();
      else { ci = hi; render(); }            // over the proposals: the one shown large follows
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (!e.repeat) choose(hi);           // held down: once
    } else if (e.key === 'Escape' && pick.value) {
      e.preventDefault();                    // first Escape clears, the next leaves the box
      pick.value = '';
      hi = 0;
      renderList();
    }
  });

  main.addEventListener('tab', e => {
    if (e.detail === 'names') load();
    if (e.detail === 'roster') { if (view) renderRoster(); else load(); }
  });
  addEventListener('hashchange', () => {
    const m = location.hash.match(/sub=([^&]+)/);
    if (!m) return;
    want = decodeURIComponent(m[1]);
    if (view) jump();
  });
  want = (location.hash.match(/sub=([^&]+)/) || [])[1] || null;
  if (want) want = decodeURIComponent(want);

  // --- the identity fields' boxes, drawn on the page shown ---
  // One box per roster column (view.columns): its region for every submission ('value', the
  // exam's override '<path>' or the package's 'base'; null: not placed), or this submission's
  // own (row.own). Saved like a region on the question page: for this submission or for all.
  const bx = {edit: false, drafts: {}, touched: false, sel: ''};
  const col = name => view.columns.find(c => c.name === name);
  const saved = (r, name) => (r && r.own[name] ? r.own[name].value : col(name).value);
  const savedAll = r => Object.fromEntries(view.columns.map(c => [c.name, saved(r, c.name)]));
  const boxDirty = name => !Rects.same(bx.drafts[name], saved(row(), name));
  const bxDirty = () => !!row() && view.columns.some(c => boxDirty(c.name));
  const placed = () => view.columns.filter(c => bx.drafts[c.name]).map(c => c.name);
  const cur = () => (bx.edit && bx.drafts[bx.sel] ? bx.drafts[bx.sel] : null);
  const put = d => { bx.drafts[bx.sel] = d; bx.touched = true; };
  const say = (text, bad) => { $('wb-msg').textContent = text || ''; $('wb-msg').classList.toggle('notice', !!bad); };
  // The drafts follow the submission shown until something is edited.
  function syncBoxes() {
    if (!bx.touched || !row()) bx.drafts = Object.fromEntries(Object.entries(savedAll(row())).map(([k, v]) => [k, v && [...v]]));
    if (!bx.drafts[bx.sel]) bx.sel = placed()[0] || '';
  }
  const page = (sub, index) => `${ROOT}/page/${encodeURIComponent(sub)}/${index}.jpg`;
  function drawBoxes() {
    const r = row(), sheet = $('wb-sheet');
    for (const el of sheet.querySelectorAll('.rbox, .parea')) el.remove();
    if (!r || !bx.edit) return;
    const p = cur() ? cur()[0] : 1;
    const img = sheet.querySelector('img'), src = page(r.sub, p - 1);
    if (img.getAttribute('src') !== src) img.src = src;
    sheet.append(Rects.area(r.print_area));          // context: where the printed page is on this scan
    for (const name of placed()) {
      const d = bx.drafts[name];
      if (d[0] !== p) continue;
      const el = Rects.box(name + (r.own[name] ? ' (own)' : ''), {sel: name === bx.sel, dirty: boxDirty(name)});
      el.dataset.name = name;
      Rects.place(el, d, r.print_area, view.pad);
      sheet.append(el);
    }
    $('wb-save').classList.toggle('dirty', bxDirty());
    Actions.enable($('wb'));
  }
  const fill = Rects.numbers('wb', cur, put, el => { fill(cur(), el); drawBoxes(); });
  function renderBoxes() {
    $('w-edit').hidden = !bx.edit;
    $('w-look').hidden = bx.edit;
    $('w-boxes-on').setAttribute('aria-pressed', String(bx.edit));
    if (!bx.edit) return;
    syncBoxes();
    const sel = $('wb-pick'), names = placed();
    sel.replaceChildren(...names.map(n => make('option', {value: n, textContent: n})));
    sel.value = bx.sel;
    sel.hidden = !names.length;
    for (const el of document.querySelectorAll('.wb-n, #wb-w, #wb-h')) el.closest('label').hidden = !names.length;
    for (const b of $('wb-add').querySelectorAll('button')) b.hidden = !!bx.drafts[b.dataset.col];
    fill(cur());
    drawBoxes();
  }
  function select(name) { bx.sel = name; say(''); renderBoxes(); }
  $('wb-pick').addEventListener('change', () => select($('wb-pick').value));
  // "+ <column>": a box for a column with none, somewhere visible on the first page.
  function addAction(name) {
    const action = `names-box-add-${name}`;
    S.register(action, {available: () => bx.edit && !!row() && !bx.drafts[name], run: () => {
      const y = 100 + 120 * view.columns.findIndex(c => c.name === name);
      bx.drafts[name] = [1, 100, y, 700, y + 80];
      bx.touched = true;
      select(name);
      say('new box: drag it onto the box on the page');
    }});
    const b = make('button', {type: 'button', textContent: `+ ${name}`, title: `a box where the ${name} is written`});
    Object.assign(b.dataset, {action, col: name});
    $('wb-add').append(b);
  }
  S.register('names-boxes', {available: () => !!row(), run: () => { bx.edit = !bx.edit; say(''); render(); }});
  S.register('names-box-remove', {available: () => bx.edit && !!cur(), run: () => {
    bx.drafts[bx.sel] = null;
    bx.touched = true;
    select(placed()[0] || '');
    say('removed: save for all');
  }});
  S.register('names-box-undo', {available: () => bx.edit && bxDirty(), run: () => { bx.touched = false; say(''); renderBoxes(); }});
  // What saving the drafts changes, one override set or cleared per box edited here (a box
  // that differs from what this submission shows): for all submissions, against every
  // submission's region (the package's again: no override); for this one, against what it shows.
  function changes(level) {
    const r = row(), out = [], scope = level === 'own' ? `submission:${r.sub}` : '';
    for (const c of view.columns) {
      const d = bx.drafts[c.name];
      if (!boxDirty(c.name)) continue;
      if (level === 'own') {
        if (!d) continue;
        const own = r.own[c.name];
        out.push(Rects.same(d, c.value) && own ? {path: c.path, scope, clear: true, rev: own.rev}
          : {path: c.path, scope, value: d, rev: own ? own.rev : null});
      } else {
        if (Rects.same(d, c.value)) continue;
        out.push(Rects.same(d, c.base) ? {path: c.path, scope, clear: true, rev: c.rev} : {path: c.path, scope, value: d, rev: c.rev});
      }
    }
    return out.filter(x => !x.clear || x.rev != null);
  }
  const WHERE = {own: 'this submission', all: 'all submissions'};
  async function saveBoxes(list, what) {
    say('…');
    const sub = row().sub;
    for (const c of list) {
      const res = await request(c.clear ? '/exam/clear' : '/exam/set',
        c.clear ? {path: c.path, scope: c.scope, rev: c.rev} : {path: c.path, scope: c.scope, value: c.value, rev: c.rev});
      if (res.status === 409) {        // keep the drafts, take what is saved now: saving again overwrites knowingly
        const now = res.body.current || {};
        await load();
        say(`changed elsewhere${now.who ? ' by ' + now.who : ''}; save again to overwrite`, true);
        return;
      }
      if (!res.ok) { say(res.body.error || 'failed', true); await load(); return; }
    }
    const drafts = bx.drafts;
    await load(false, true);
    const shadowed = list.length && !list[0].scope && view.columns.some(c => view.byId[sub].own[c.name] && drafts[c.name]
      && !Rects.same(drafts[c.name], view.byId[sub].own[c.name].value));
    say(`${what}${shadowed ? ' · this submission keeps its own' : ''}${list.length && main.dataset.model ? ' · read names to use them' : ''}`);
  }
  S.register('names-box-save-own', {available: () => bx.edit && changes('own').length > 0,
    run: () => saveBoxes(changes('own'), `saved · ${WHERE.own}`)});
  S.register('names-box-save', {available: () => bx.edit && !!row() && changes('all').length > 0,
    run: () => saveBoxes(changes('all'), `saved · ${WHERE.all}`)});
  S.register('names-box-drop-own', {available: () => bx.edit && !!row() && !!row().own[bx.sel], run: () => {
    if (!confirm(`Drop this submission's own box for ${bx.sel}?`)) return;
    return saveBoxes([{path: col(bx.sel).path, scope: `submission:${row().sub}`, clear: true, rev: row().own[bx.sel].rev}], 'own box dropped');
  }});
  S.register('names-box-revert', {available: () => bx.edit && view.columns.some(c => c.rev != null), run: () => {
    if (!confirm("Back to the boxes the exam declares, for all submissions?")) return;
    return saveBoxes(view.columns.filter(c => c.rev != null).map(c => ({path: c.path, scope: '', clear: true, rev: c.rev})), 'reverted · all submissions');
  }});
  Rects.arrows(() => live() && cur(), put, () => { fill(cur()); drawBoxes(); });
  // dragging: inside a box moves it, a corner resizes it; a click on another box selects it
  let drag = null;
  const sheetEl = $('wb-sheet');
  sheetEl.addEventListener('pointerdown', e => {
    const el = e.target.closest('.rbox');
    if (e.button !== 0 || !el || !bx.edit) return;
    if (el.dataset.name !== bx.sel) { select(el.dataset.name); return; }
    const q = sheetEl.getBoundingClientRect(), [wl, wt, wr, wb] = row().print_area;
    drag = {h: e.target.dataset.h || '', x: e.clientX, y: e.clientY, d: [...cur()],
            kx: 1e6 / (q.width * (wr - wl)), ky: 1e6 / (q.height * (wb - wt))};     // pixels -> per-mille of the printed page
    sheetEl.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  sheetEl.addEventListener('pointermove', e => {
    if (!drag) return;
    const dx = (e.clientX - drag.x) * drag.kx, dy = (e.clientY - drag.y) * drag.ky;
    put(drag.h ? Rects.resize(drag.d, drag.h, dx, dy) : Rects.shift(drag.d, dx, dy));
    const el = sheetEl.querySelector('.rbox.sel');
    if (el) Rects.place(el, cur(), row().print_area, view.pad);
    fill(cur());
  });
  const drop = () => { if (drag) { drag = null; drawBoxes(); } };
  sheetEl.addEventListener('pointerup', drop);
  sheetEl.addEventListener('pointercancel', drop);
  addEventListener('beforeunload', e => { if (bx.edit && bxDirty()) e.preventDefault(); });

  // --- an identity field's value: corrected or verified like any field's ---
  async function saveValue(r, name, body) {
    if ($('w-crops').contains(document.activeElement)) document.activeElement.blur();
    const res = await request(`/s/${encodeURIComponent(r.sub)}/@identity/save`, body);
    if (!res.ok) flash(res.body.error || 'failed');
    await load();
  }
  function valueBox(r, name) {
    const v = r.values[name], stale = r.stale.includes(name);
    const input = make('input', {type: 'text', className: 'mono', value: v ? v.text : '', spellcheck: false,
      placeholder: stale && main.dataset.model ? 'not read yet' : '', title: 'Enter: save (verified by you)'});
    input.setAttribute('aria-label', name);
    input.addEventListener('keydown', e => {
      if (e.key !== 'Enter' || e.isComposing) return;
      e.preventDefault();
      if (e.repeat || (v && input.value === v.text)) return;
      S.enqueue(() => saveValue(r, name, {values: {[`@identity/${name}`]: {text: input.value, rev: v ? v.rev : null}}}));
    });
    const out = [make('span', {className: 'dim', textContent: name + ' '}), input];
    if (v && !v.verified && !v.marker) {
      const ok = make('button', {type: 'button', className: 'quiet', textContent: '✓', title: 'verify this value'});
      ok.addEventListener('click', () => S.enqueue(() => saveValue(r, name, {verify: [{key: `@identity/${name}`, rev: v.rev}]})));
      out.push(' ', tag('unv', 'unverified'), ok);
    }
    if (stale && v && v.verified !== 'person') out.push(' ', tag('', 'read again'));
    return out;
  }

  // --- drawing the step ---
  let whole = !!pref('namesWhole', false);      // the whole first page, not only its top
  $('w-whole').addEventListener('click', () => { whole = !whole; setPref('namesWhole', whole); renderStep(); });
  function crop(sub, c) {
    const el = make('span', {className: 'crop'});
    // no wider than the panel, no taller than about 170 px (a page is about 1.3 times as high as wide)
    const [l, t, r, b] = c.box.split(',').map(Number), room = $('w-step').clientWidth - 24;
    const width = Math.min(620, room > 0 ? room : 620, 170 * (r - l) / Math.max(0.01, (b - t) * 1.3));
    Object.assign(el.dataset, {src: page(sub, c.index), box: c.box, width});
    drawCrop(el);
    return el;
  }
  // With no boxes nothing is read: unverified is all there is to say.
  const doubtTag = r => (r.who && r.who.recheck ? tag('unv', 'pages changed', 'named before its pages changed: confirm again')
    : r.who ? tag('', 'verified') : !r.crops.length ? tag('unv', 'unverified') : tag(DOUBT[r.doubt], view.doubts[r.doubt]));
  const whoTags = r => (!r.who ? [] : [r.who.twice ? tag('bad', 'name twice') : '',
    !r.who.person && view.roster ? tag('', 'not on the roster') : '']);

  function renderStep() {
    const r = row(), done = !r;
    $('w-done').hidden = !done || !order.length;
    $('w-main').hidden = done;
    $('w-sub').textContent = done ? (order.length ? 'done' : 'no submissions yet') : r.sub;
    $('w-pos').textContent = done ? '' : `${k + 1} of ${order.length}`;
    $('w-tags').replaceChildren(...(done ? [] : [doubtTag(r), ...whoTags(r)]));
    if (done) return;
    // the crops and their values: keep a value being typed in when nothing about it changed
    const typing = document.activeElement && $('w-crops').contains(document.activeElement);
    if (!typing || $('w-crops').dataset.sub !== r.sub) {
      $('w-crops').dataset.sub = r.sub;
      $('w-crops').replaceChildren(...r.crops.map(c => make('figure', {}, crop(r.sub, c),
        make('figcaption', {}, ...valueBox(r, c.name)))));
    }
    // The page: with no boxes, always (its top, or whole); with boxes, on request.
    const boxes = r.crops.length > 0, img = $('w-page').querySelector('img'), src = page(r.sub, 0);
    $('w-page').hidden = boxes && !whole;
    $('w-page').classList.toggle('whole', whole);
    if (!$('w-page').hidden && img.getAttribute('src') !== src) img.src = src;
    $('w-whole').hidden = false;
    $('w-whole').textContent = boxes ? (whole ? 'hide page' : 'page') : (whole ? 'top only' : 'whole page');
    // The big name is what V takes: the proposal shown, else the verified name.
    const c = cand(), named = r.who, shown = c || named;
    $('w-name').replaceChildren(shown ? shown.name : make('span', {className: 'dim', textContent: 'no proposal'}));
    $('w-id').textContent = shown ? shown.id : '';
    const why = [];
    if (c) {
      const n = r.candidates.length;
      why.push(`proposal ${ci % n + 1}/${n}: ${c.why}`);
      if (c.taken) why.push(make('span', {className: 'bad', textContent: ` (already ${c.taken})`}));
    } else if (!named) why.push('no proposal: type the name or ID');
    $('w-why').replaceChildren(...why);
    $('w-other').replaceChildren(named ? make('span', {}, make('span', {className: 'dim', textContent: 'verified: '}),
      make('b', {textContent: `${named.name}${named.id ? ' · ' + named.id : ''}`})) : '');
    renderList();
    const next = view.byId[order[k + 1]];
    if (next) for (const x of next.crops.length ? next.crops : [{index: 0}]) { const im = new Image(); im.src = page(next.sub, x.index); }
  }

  function renderRows() {
    const rows = order.map((s, j) => {
      const r = view.byId[s], c = r.candidates[0];
      const open = make('button', {type: 'button', className: 'quiet', textContent: s, title: 'step to it'});
      open.addEventListener('click', () => { go(j); $('w-step').scrollIntoView({block: 'nearest'}); });
      const read = r.crops.map(x => r.read[x.name]).filter(Boolean).join(' · ');
      return make('tr', {className: j === k ? 'here' : ''},
        make('td', {}, open), make('td', {}, doubtTag(r)),
        make('td', {className: 'mono dim', textContent: read}),
        make('td', {}, r.who ? make('b', {textContent: `${r.who.name}${r.who.id ? ' · ' + r.who.id : ''}`})
          : c ? make('span', {className: 'dim', textContent: `${c.name} · ${c.id}?`}) : ''),
        make('td', {}, ...whoTags(r)));
    });
    $('w-rows').tBodies[0].replaceChildren(...rows);
    $('w-nobody').hidden = !view.nobody.length;
    $('w-nobody-list').textContent = view.nobody.join(' · ');
  }

  function render() {
    const left = view.left, n = view.rows.length, fields = view.boxed;
    const counts = {}, stale = view.rows.filter(r => r.stale.length).length;
    for (const r of view.rows) if (!verified(r)) counts[r.doubt] = (counts[r.doubt] || 0) + 1;
    $('w-sum').replaceChildren(!n ? tag('', 'no submissions yet') : left ? tag('unv', `${left} unverified`) : tag('', `all ${n} verified`),
      ...['disagree', 'tie', 'nobody', 'unreadable', 'empty'].filter(d => counts[d]).map(d => tag(DOUBT[d], `${counts[d]} ${view.doubts[d]}`)),
      stale ? tag('', `${stale} to read`) : '');
    $('w-roster').textContent = view.roster ? `roster: ${view.roster}` : 'no roster yet';
    main.querySelector('[data-action="names-read"]').hidden = !fields;
    $('w-noboxes').hidden = !!fields;
    $('w-step').hidden = $('w-all').hidden = !n;
    renderBoxes();
    renderStep();
    renderRows();
    main.dispatchEvent(new CustomEvent('names', {detail: {n, left, unread: stale, proposal: !!cand(), fields}}));
    Actions.hints();
    Actions.enable(main);
  }

  // --- the Roster step's table: everyone on the roster, and who is named for which submission ---
  const filter = $('ro-filter');
  function subLink(sub) {
    const a = make('a', {href: '#sub=' + encodeURIComponent(sub), textContent: sub, title: 'open on Names'});
    a.addEventListener('click', ev => {
      ev.preventDefault();
      history.replaceState(null, '', a.getAttribute('href'));
      dispatchEvent(new HashChangeEvent('hashchange'));
    });
    return a;
  }
  function renderRoster() {
    if (!filter) return;
    const shown = search(filter.value);
    const matched = ROSTER.filter(p => taken[p.key]).length;
    $('ro-sum').replaceChildren(tag('', `${ROSTER.length} people`), ' ', tag('', `${matched} matched`), ' ',
      ROSTER.length - matched ? tag('unv', `${ROSTER.length - matched} unmatched`) : '',
      shown.length < ROSTER.length ? make('span', {className: 'dim', textContent: ` · ${shown.length} shown`}) : '');
    $('ro-rows').tBodies[0].replaceChildren(...shown.map(p => {
      const subs = taken[p.key] || [];
      return make('tr', {}, make('td', {textContent: p.name}), make('td', {className: 'mono', textContent: p.id}),
        make('td', {className: 'dim', textContent: p.email}),
        make('td', {}, ...(subs.length ? subs.flatMap((s, j) => (j ? [' ', subLink(s)] : [subLink(s)]))
          : [make('span', {className: 'dim', textContent: 'unmatched'})]),
          subs.length > 1 ? tag('bad', 'twice') : ''));
    }));
  }
  if (filter) filter.addEventListener('input', renderRoster);

  load();     // for the strip's count, before the step is opened
})();
