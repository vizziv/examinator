'use strict';
// Names: who wrote each submission. One at a time, its name and ID boxes large beside the
// best proposal from the roster and why; V verifies it and goes to the next unverified
// submission, W / S show the other proposals, A / D the other submissions, and typing a
// name picks from the roster. Nothing is named until a person says so. Unverified and
// doubtful submissions come first. The step is shown by scans.js (the 'tab' event).
(() => {
  const main = $('scans');
  if (!main || !$('names')) return;
  let view = null, order = [], k = 0, ci = 0;
  let want = null;        // a submission to show once loaded (a link from Split: #sub=)

  const live = () => !$('names').hidden && !!view;
  const row = () => (view && k < order.length ? view.byId[order[k]] : null);
  const cand = () => { const r = row(); return r && r.candidates.length ? r.candidates[ci % r.candidates.length] : null; };
  // How sure a proposal is: both agree is information, one of the two waits on a person, the rest are problems.
  const DOUBT = {agree: '', id: 'unv', name: 'unv', disagree: 'bad', nobody: 'bad', empty: 'bad', unread: ''};
  const verified = r => !!r.who;

  async function load() {
    const r = await request('/scans/names');
    if (!r.ok) { flash('could not load names'); return; }
    view = r.body;
    view.byId = Object.fromEntries(view.rows.map(x => [x.sub, x]));
    // The order is fixed when first seen, so verifying does not reshuffle what is ahead.
    const known = new Set(order);
    // New submissions join the end: those with a proposal (V takes it) before those needing typing.
    const fresh = view.rows.filter(x => !known.has(x.sub));
    const typed = x => (x.candidates.length ? 0 : 1);
    order = order.filter(s => view.byId[s]).concat(fresh.sort((a, b) => typed(a) - typed(b)).map(x => x.sub));
    if (k >= order.length || !known.size) k = first();
    if (want) jump();
    render();
  }
  const first = () => nextOpen(order, -1, s => !verified(view.byId[s]));
  function jump() {
    const j = order.indexOf(want);
    want = null;
    if (j >= 0) go(j);
  }
  function go(j) { k = Math.max(0, Math.min(j, order.length)); ci = 0; $('w-pick').value = ''; render(); }

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
  // Typing starts from what was read ("Name, ID"), so Enter keeps the ID.
  S.register('names-type', {available: () => !!row(), run: () => {
    const r = row(), pick = $('w-pick');
    if (!pick.value) {
      const by = role => view.fields.filter(f => f.roster === role).map(f => r.read[f.name]).filter(Boolean).join(' ');
      pick.value = [by('name'), by('id')].filter(Boolean).join(', ');
    }
    pick.focus();
    pick.select();
  }});
  S.register('names-read', {available: () => !!main.dataset.model, long: true, run: async () => {
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
  $('w-pick').addEventListener('keydown', e => {
    if (e.key !== 'Enter' || e.isComposing) return;
    e.preventDefault();
    const text = $('w-pick').value.trim();
    if (text) { $('w-pick').blur(); S.enqueue(() => name(text)); }
  });
  main.addEventListener('tab', e => { if (e.detail === 'names') load(); });
  addEventListener('hashchange', () => {
    const m = location.hash.match(/sub=([^&]+)/);
    if (!m) return;
    want = decodeURIComponent(m[1]);
    if (view) jump();
  });
  want = (location.hash.match(/sub=([^&]+)/) || [])[1] || null;
  if (want) want = decodeURIComponent(want);

  // --- drawing ---
  const page = (sub, index) => `${ROOT}/page/${encodeURIComponent(sub)}/${index}.jpg`;
  function crop(sub, c) {
    const el = make('span', {className: 'crop'});
    const room = $('w-step').clientWidth - 24;       // no wider than the panel
    Object.assign(el.dataset, {src: page(sub, c.index), box: c.box, width: room > 0 ? Math.min(620, room) : 620});
    drawCrop(el);
    return el;
  }
  const doubtTag = r => (r.who ? tag('', 'verified') : tag(DOUBT[r.doubt], view.doubts[r.doubt]));

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
    // The big name is what V takes: the proposal shown, else the verified name.
    const c = cand(), named = r.who, shown = c || named;
    $('w-name').replaceChildren(shown ? shown.name : make('span', {className: 'dim', textContent: 'no proposal'}));
    $('w-id').textContent = shown ? shown.id : '';
    const why = [];
    if (c) {
      const n = r.candidates.length;
      why.push(`proposal ${ci % n + 1}/${n}: ${c.why}`);
      if (c.taken) why.push(make('span', {className: 'bad', textContent: ` (already ${c.taken})`}));
    } else if (!named) why.push('no proposal: type the name, or go to the next');
    $('w-why').replaceChildren(...why);
    $('w-other').replaceChildren(named ? make('span', {}, make('span', {className: 'dim', textContent: 'verified: '}),
      make('b', {textContent: `${named.name}${named.id ? ' · ' + named.id : ''}`})) : '');
    const next = view.byId[order[k + 1]];
    if (next) for (const x of next.crops) { const im = new Image(); im.src = page(next.sub, x.index); }
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
    const left = view.left, n = view.rows.length;
    const counts = {};
    for (const r of view.rows) if (!r.who) counts[r.doubt] = (counts[r.doubt] || 0) + 1;
    $('w-sum').replaceChildren(!n ? tag('', 'no submissions yet') : left ? tag('unv', `${left} unverified`) : tag('', `all ${n} verified`),
      ...['disagree', 'nobody', 'empty', 'unread'].filter(d => counts[d]).map(d => tag(DOUBT[d], `${counts[d]} ${view.doubts[d]}`)));
    $('w-roster').textContent = view.roster ? `roster: ${view.roster}` : 'no roster yet';
    $('w-step').hidden = $('w-all').hidden = !n;
    renderStep();
    renderRows();
    main.dispatchEvent(new CustomEvent('names', {detail: {n, left, unread: counts.unread || 0, proposal: !!cand()}}));
    Actions.hints();
    Actions.enable(main);
  }

  load();     // for the strip's count, before the step is opened
})();
