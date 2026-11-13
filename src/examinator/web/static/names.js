'use strict';
// Names: who wrote each submission. One at a time: its name and ID boxes large (or, with no
// boxes, its first page), beside the best proposal from the roster and why. V verifies the
// proposal and goes to the next unverified submission, W / S show the other proposals, A / D
// the other submissions. The picker under it filters the roster as you type (name, ID or
// email); Enter takes the highlighted person and moves on. Nothing is named until a person
// says so. Unverified and doubtful submissions come first. "boxes" draws the name and ID
// boxes on the page, for every submission (rects.js, as regions are drawn on the question
// page). Also the Roster step's table: everyone on it and the submission they are named for.
// The step is shown by scans.js (the 'tab' event).
(() => {
  const main = $('scans');
  if (!main || !$('names')) return;
  let view = null, order = [], k = 0, ci = 0;
  let want = null;        // a submission to show once loaded (a link from Split or the roster: #sub=)
  let taken = {};         // roster key -> the submissions it is picked for

  const live = () => !$('names').hidden && !!view;
  const row = () => (view && k < order.length ? view.byId[order[k]] : null);
  const cand = () => { const r = row(); return r && r.candidates.length ? r.candidates[ci % r.candidates.length] : null; };
  // How sure a proposal is: both agree is information, one of the two waits on a person, the rest are problems.
  const DOUBT = {agree: '', id: 'unv', name: 'unv', disagree: 'bad', nobody: 'bad', empty: 'bad', unread: ''};
  const verified = r => !!r.who;

  // --- the roster, searched in the browser: sent once with the page ---
  const fold = t => (t || '').normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().replace(/['’`]/g, '');
  const ROSTER = JSON.parse(main.dataset.roster || '[]').map(p => ({...p,
    words: fold(p.name).split(/[^\p{L}\p{N}]+/u).filter(Boolean), ids: [fold(p.id), fold(p.email)].filter(Boolean),
    hay: fold(`${p.name} ${p.id} ${p.email}`)}));
  // People matching every word typed, best first: a word starting a name part, the ID or the
  // email counts more than one found inside them.
  function search(text, limit) {
    const q = fold(text).split(/[\s,;<>]+/).filter(Boolean);
    if (!q.length) return limit ? [] : ROSTER;
    const hits = [];
    for (const p of ROSTER) {
      let score = 0;
      for (const t of q) {
        if (p.words.some(w => w.startsWith(t)) || p.ids.some(x => x.startsWith(t))) score += 2;
        else if (p.hay.includes(t)) score += 1;
        else { score = -1; break; }
      }
      if (score > 0) hits.push([score, p]);
    }
    if (!limit) return hits.map(h => h[1]);
    hits.sort((a, b) => b[0] - a[0] || a[1].name.localeCompare(b[1].name));
    return hits.slice(0, limit).map(h => h[1]);
  }
  const elsewhere = (key, sub) => (taken[key] || []).filter(s => s !== sub);

  async function load() {
    const r = await request('/scans/names');
    if (!r.ok) { flash('could not load names'); return; }
    view = r.body;
    view.byId = Object.fromEntries(view.rows.map(x => [x.sub, x]));
    taken = {};
    for (const x of view.rows) if (x.who && x.who.key) (taken[x.who.key] = taken[x.who.key] || []).push(x.sub);
    // The order is fixed when first seen, so verifying does not reshuffle what is ahead.
    const known = new Set(order);
    // New submissions join the end: those with a proposal (V takes it) before those needing typing.
    const fresh = view.rows.filter(x => !known.has(x.sub));
    const typed = x => (x.candidates.length ? 0 : 1);
    order = order.filter(s => view.byId[s]).concat(fresh.sort((a, b) => typed(a) - typed(b)).map(x => x.sub));
    if (k >= order.length || !known.size) k = first();
    if (!bx.edit || !bxDirty()) setBoxes(view.boxes);
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

  // Name the submission shown; afterwards one line says what was done, with an undo.
  async function name(text) {
    const r = row();
    if (!r) return;
    const before = r.who ? r.who.key || r.who.name : '';
    const res = await request('/scans/who', {sub: r.sub, text, rev: r.rev});
    if (!res.ok) { flash(res.body.error || 'failed'); await load(); return; }
    said(r.sub, res.body.who.name, before);
    await load();
    go(nextOpen(order, k, s => !verified(view.byId[s])));
  }
  function said(sub, who, before) {
    const undo = make('button', {type: 'button', className: 'quiet', textContent: 'undo', title: before ? `back to ${before}` : 'unname it'});
    undo.addEventListener('click', () => S.enqueue(async () => {
      const r = view.byId[sub];
      const res = await request('/scans/who', {sub, text: before, rev: r ? r.rev : null});
      if (!res.ok) flash(res.body.error || 'failed');
      $('w-said').replaceChildren();
      await load();
      go(order.indexOf(sub));
    }));
    $('w-said').replaceChildren(`${sub} → ${who || 'nobody'} · `, undo);
  }

  const S = steps(live, () => { if (view) render(); });
  S.register('names-verify', {available: () => !!cand(), run: () => name(cand().key)});
  S.register('names-down', {available: () => !!row() && row().candidates.length > 1, run: () => { ci += 1; render(); }});
  S.register('names-up', {available: () => !!row() && row().candidates.length > 1,
    run: () => { ci += row().candidates.length - 1; render(); }});
  S.register('names-skip', {available: () => k < order.length, run: () => go(k + 1)});
  S.register('names-back', {available: () => k > 0, run: () => go(k - 1)});
  S.register('names-type', {available: () => !!row(), run: () => { pick.focus(); pick.select(); }});
  S.register('names-read', {available: () => !!main.dataset.model && view.fields.length > 0, long: true, run: async () => {
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
    const found = search(text, SHOWN).map(p => ({key: p.key, name: p.name, id: p.id, email: p.email,
                                                 taken: elsewhere(p.key, r.sub)[0] || null}));
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
             x.taken ? make('span', {className: 'dim', textContent: `→ ${x.taken}`, title: 'already named for that submission'}) : '']));
      li.setAttribute('aria-selected', String(j === hi));
      li.addEventListener('mousedown', e => { e.preventDefault(); choose(j); });     // the box keeps the focus
      return li;
    }));
  }
  function choose(j) {
    const x = items[j];
    if (!x) return;
    S.enqueue(() => name(x.free || x.key));
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
      choose(hi);
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

  // --- the name and ID boxes, drawn on the page shown (for every submission) ---
  const bx = {edit: false, saved: [], drafts: [], rev: null, own: false, declared: 0, sel: 0};
  const clone = b => b.map(x => ({...x, region: [...x.region]}));
  const bxDirty = () => !Rects.same(bx.drafts, bx.saved);
  const boxDirty = x => !Rects.same(x, bx.saved.find(s => s.name === x.name));
  const curBox = () => bx.drafts[bx.sel] || null;
  const cur = () => (bx.edit && curBox() ? curBox().region : null);
  const put = d => { curBox().region = d; };
  const say = (text, bad) => { $('wb-msg').textContent = text || ''; $('wb-msg').classList.toggle('notice', !!bad); };
  function setBoxes(b) {
    Object.assign(bx, {saved: clone(b.boxes), drafts: clone(b.boxes), rev: b.rev, own: b.own, declared: b.declared});
    bx.sel = Math.min(bx.sel, Math.max(0, bx.drafts.length - 1));
  }
  const page = (sub, index) => `${ROOT}/page/${encodeURIComponent(sub)}/${index}.jpg`;
  function drawBoxes() {
    const r = row(), sheet = $('wb-sheet');
    for (const el of sheet.querySelectorAll('.rbox, .parea')) el.remove();
    if (!r || !bx.edit) return;
    const p = curBox() ? curBox().region[0] : 1;
    const img = sheet.querySelector('img'), src = page(r.sub, r.cover + p - 1);
    if (img.getAttribute('src') !== src) img.src = src;
    sheet.append(Rects.area(r.window));          // context: where the printed page is on this scan
    bx.drafts.forEach((x, i) => {
      if (x.region[0] !== p) return;
      const el = Rects.box(x.name, {sel: i === bx.sel, dirty: boxDirty(x)});
      el.dataset.i = i;
      Rects.place(el, x.region, r.window, view.pad);
      sheet.append(el);
    });
    $('wb-save').classList.toggle('dirty', bxDirty());
    Actions.enable($('wb'));
  }
  const fill = Rects.numbers('wb', cur, put, el => { fill(cur(), el); drawBoxes(); });
  function renderBoxes() {
    $('w-edit').hidden = !bx.edit;
    $('w-look').hidden = bx.edit;
    $('w-boxes-on').setAttribute('aria-pressed', String(bx.edit));
    if (!bx.edit) return;
    const sel = $('wb-pick');
    sel.replaceChildren(...bx.drafts.map((x, i) => make('option', {value: i, textContent: x.name + (x.roster ? '' : ' (not compared)')})));
    sel.value = bx.sel;
    sel.hidden = !bx.drafts.length;
    for (const el of document.querySelectorAll('.wb-n, #wb-w, #wb-h')) el.closest('label').hidden = !bx.drafts.length;
    fill(cur());
    drawBoxes();
  }
  function select(i) { bx.sel = i; say(''); renderBoxes(); }
  $('wb-pick').addEventListener('change', () => select(Number($('wb-pick').value)));
  function add(roster, label, region) {
    let n = label, j = 2;
    while (bx.drafts.some(x => x.name === n)) n = `${label} ${j++}`;
    bx.drafts.push({name: n, roster, region});
    select(bx.drafts.length - 1);
    say('new box: drag it onto the box on the page');
  }
  const has = roster => bx.drafts.some(x => x.roster === roster);
  S.register('names-boxes', {available: () => !!row(), run: () => { bx.edit = !bx.edit; say(''); render(); }});
  S.register('names-box-add-name', {available: () => bx.edit && !has('name'), run: () => add('name', 'name', [1, 100, 100, 700, 180])});
  S.register('names-box-add-id', {available: () => bx.edit && !has('id'), run: () => add('id', 'id', [1, 100, 220, 500, 300])});
  S.register('names-box-remove', {available: () => bx.edit && !!curBox(), run: () => {
    bx.drafts.splice(bx.sel, 1);
    select(Math.max(0, bx.sel - 1));
  }});
  S.register('names-box-undo', {available: () => bx.edit && bxDirty(), run: () => { bx.drafts = clone(bx.saved); select(Math.min(bx.sel, bx.drafts.length - 1)); }});
  async function saveBoxes(boxes) {
    say('…');
    const res = await request('/scans/boxes', {boxes, rev: bx.rev});
    if (res.status === 409) {        // keep the drafts; saving again overwrites knowingly
      bx.rev = (res.body.current || {}).rev ?? null;
      say('changed elsewhere; save again to overwrite', true);
      return;
    }
    if (!res.ok) { say(res.body.error || 'failed', true); return; }
    setBoxes(res.body);
    order = [];         // the readings no longer apply: read again, doubtful ones first again
    await load();
    say(`${boxes ? 'saved' : 'reverted'} · all submissions${res.body.boxes.length && main.dataset.model ? ' · read names to use them' : ''}`);
  }
  S.register('names-box-save', {available: () => bx.edit && bxDirty(),
    run: () => saveBoxes(bx.drafts.map(({name, roster, region, ask}) => ({name, roster, region, ...(ask ? {ask} : {})})))});
  S.register('names-box-revert', {available: () => bx.edit && bx.own, run: () => {
    if (!confirm(bx.declared ? "Back to the boxes the exam declares, for all submissions?" : "Remove the boxes, for all submissions?")) return;
    return saveBoxes(null);
  }});
  Rects.arrows(() => live() && cur(), put, () => { fill(cur()); drawBoxes(); });
  // dragging: inside a box moves it, a corner resizes it; a click on another box selects it
  let drag = null;
  const sheetEl = $('wb-sheet');
  sheetEl.addEventListener('pointerdown', e => {
    const el = e.target.closest('.rbox');
    if (e.button !== 0 || !el || !bx.edit) return;
    const i = Number(el.dataset.i);
    if (i !== bx.sel) { select(i); return; }
    const q = sheetEl.getBoundingClientRect(), [wl, wt, wr, wb] = row().window;
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
    if (el) Rects.place(el, cur(), row().window, view.pad);
    fill(cur());
  });
  const drop = () => { if (drag) { drag = null; drawBoxes(); } };
  sheetEl.addEventListener('pointerup', drop);
  sheetEl.addEventListener('pointercancel', drop);
  addEventListener('beforeunload', e => { if (bx.edit && bxDirty()) e.preventDefault(); });

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
  const doubtTag = r => (r.who ? tag('', 'verified') : !view.fields.length ? tag('unv', 'unverified')
    : tag(DOUBT[r.doubt], view.doubts[r.doubt]));

  function renderStep() {
    const r = row(), done = !r;
    $('w-done').hidden = !done || !order.length;
    $('w-main').hidden = done;
    $('w-sub').textContent = done ? (order.length ? 'done' : 'no submissions yet') : r.sub;
    $('w-pos').textContent = done ? '' : `${k + 1} of ${order.length}`;
    $('w-tags').replaceChildren(...(done ? [] : [doubtTag(r), r.who && r.who.twice ? tag('bad', 'name twice') : '',
      r.failed.length ? tag('bad', 'read failed', r.failed.join(', ')) : '']));
    if (done) return;
    $('w-crops').replaceChildren(...r.crops.map(c => {
      const read = r.read[c.name];
      const known = Object.prototype.hasOwnProperty.call(r.read, c.name);
      return make('figure', {}, crop(r.sub, c), make('figcaption', {},
        make('span', {className: 'dim', textContent: c.name + ' '}),
        make('span', {className: read ? 'mono' : 'dim', textContent: read == null ? (known ? 'empty' : 'not read yet') : read})));
    }));
    // The page: with no boxes, always (its top, or whole); with boxes, on request.
    const boxes = r.crops.length > 0, img = $('w-page').querySelector('img'), src = page(r.sub, r.cover);
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
    if (next) for (const x of next.crops.length ? next.crops : [{index: next.cover}]) { const im = new Image(); im.src = page(next.sub, x.index); }
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
        make('td', {}, r.who && r.who.twice ? tag('bad', 'name twice') : ''));
    });
    $('w-rows').tBodies[0].replaceChildren(...rows);
    $('w-nobody').hidden = !view.nobody.length;
    $('w-nobody-list').textContent = view.nobody.join(' · ');
  }

  function render() {
    const left = view.left, n = view.rows.length, fields = view.fields.length;
    const counts = {};
    for (const r of view.rows) if (!r.who) counts[r.doubt] = (counts[r.doubt] || 0) + 1;
    $('w-sum').replaceChildren(!n ? tag('', 'no submissions yet') : left ? tag('unv', `${left} unverified`) : tag('', `all ${n} verified`),
      ...['disagree', 'nobody', 'empty', 'unread'].filter(d => counts[d] && (fields || d !== 'unread')).map(d => tag(DOUBT[d], `${counts[d]} ${view.doubts[d]}`)));
    $('w-roster').textContent = view.roster ? `roster: ${view.roster}` : 'no roster yet';
    main.querySelector('[data-action="names-read"]').hidden = !fields;
    $('w-noboxes').hidden = !!fields;
    $('w-step').hidden = $('w-all').hidden = !n;
    renderBoxes();
    renderStep();
    renderRows();
    main.dispatchEvent(new CustomEvent('names', {detail: {n, left, unread: fields ? counts.unread || 0 : 0, proposal: !!cand(), fields}}));
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
