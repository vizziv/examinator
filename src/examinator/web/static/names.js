'use strict';
// Names: who wrote each exam. One exam at a time, its name and ID boxes large beside the
// best proposal from the roster and why; V takes it and goes to the next unnamed exam,
// A / D show the other proposals, typing searches the roster. Nothing is named until a
// person says so. Unnamed and doubtful exams come first.
(() => {
  const $ = id => document.getElementById(id);
  if (!$('names')) return;
  const tabs = [...document.querySelectorAll('[data-tab]')];
  let view = null, order = [], k = 0, ci = 0;

  // --- tabs: Split and Names; the other tab's keys are off ---
  function tab(name) {
    for (const b of tabs) b.setAttribute('aria-selected', String(b.dataset.tab === name));
    $('split').hidden = name !== 'split';
    $('names').hidden = name !== 'names';
    $('src').hidden = name !== 'split';
    setPref('scansTab', name);
    if (name === 'names') load().catch(oops); else Actions.hints();
  }
  for (const b of tabs) b.addEventListener('click', () => tab(b.dataset.tab));

  const live = () => !$('names').hidden && !!view;
  const row = () => (view && k < order.length ? view.byId[order[k]] : null);
  const cand = () => { const r = row(); return r && r.candidates.length ? r.candidates[ci % r.candidates.length] : null; };
  const DOUBT = {agree: 'good', id: 'accent', name: 'accent', disagree: 'bad', nobody: 'bad', unread: ''};

  async function load() {
    const r = await request('/scans/names');
    if (!r.ok) { flash('could not load names'); return; }
    view = r.body;
    view.byId = Object.fromEntries(view.rows.map(x => [x.sub, x]));
    // The order is fixed when first seen, so confirming does not reshuffle what is ahead.
    const known = new Set(order);
    order = order.filter(s => view.byId[s]).concat(view.rows.map(x => x.sub).filter(s => !known.has(s)));
    if (k >= order.length || !known.size) k = nextOpen(-1);
    render();
  }
  function nextOpen(from) {
    for (let j = from + 1; j < order.length; j++) if (!view.byId[order[j]].who) return j;
    for (let j = 0; j <= from && j < order.length; j++) if (!view.byId[order[j]].who) return j;
    return order.length;
  }
  function go(j) { k = Math.max(0, Math.min(j, order.length)); ci = 0; $('w-pick').value = ''; render(); }

  async function name(text) {
    const r = row();
    if (!r) return;
    const res = await request('/scans/who', {sub: r.sub, text, rev: r.rev});
    if (!res.ok) { flash(res.body.error || 'failed'); await load(); return; }
    await load();
    go(nextOpen(k));
  }

  // Keys come faster than replies: one step at a time, each on the state the last left.
  let chain = Promise.resolve();
  const oops = err => { flash('something went wrong; reload the page'); console.error(err); };
  const register = (n, spec) => Actions.register(n, {
    ...spec, available: ctx => live() && (!spec.available || spec.available(ctx)),
    run: ctx => { chain = chain.then(() => spec.run(ctx)).catch(oops); return chain; }});
  register('who-confirm', {available: () => !!cand(), run: () => name(cand().key)});
  register('who-next', {available: () => !!row() && row().candidates.length > 1, run: () => { ci += 1; render(); }});
  register('who-prev', {available: () => !!row() && row().candidates.length > 1,
    run: () => { ci += row().candidates.length - 1; render(); }});
  register('who-skip', {available: () => k < order.length, run: () => go(k + 1)});
  register('who-back', {available: () => k > 0, run: () => go(k - 1)});
  register('who-search', {available: () => !!row(), run: () => { $('w-pick').focus(); $('w-pick').select(); }});
  register('names-read', {run: async () => {
    const r = await request('/scans/names/read', {});
    if (!r.ok) { flash(r.body.error || 'failed'); return; }
    $('msg').textContent = r.body.n ? `reading ${r.body.n}…` : '';
    const job = await waitJob(r.body.job);
    $('msg').textContent = job.state !== 'done' ? `read ${job.state}` : job.outcomes && job.outcomes.length ? `${job.outcomes.length} not read` : '';
    order = [];         // new readings: doubtful ones first again
    await load();
  }});
  $('w-pick').addEventListener('keydown', e => {
    if (e.key !== 'Enter' || e.isComposing) return;
    e.preventDefault();
    const text = $('w-pick').value.trim();
    if (text) { $('w-pick').blur(); chain = chain.then(() => name(text)).catch(oops); }
  });

  // --- drawing ---
  const page = (sub, index) => `${ROOT}/page/${encodeURIComponent(sub)}/${index}.jpg`;
  function crop(sub, c, width) {
    const el = make('span', {className: 'crop'});
    Object.assign(el.dataset, {src: page(sub, c.index), box: c.box, width});
    drawCrop(el);
    return el;
  }
  const tag = (cls, text, title) => make('span', {className: 'tag ' + cls, textContent: text, title: title || ''});
  function doubtTag(r) {
    return r.who ? tag('good', 'named') : tag(DOUBT[r.doubt], view.doubts[r.doubt]);
  }

  function renderStep() {
    const r = row(), done = !r;
    $('w-done').hidden = !done;
    $('w-main').hidden = done;
    $('w-sub').textContent = done ? 'done' : r.sub;
    $('w-pos').textContent = done ? '' : `${k + 1} of ${order.length}`;
    $('w-tags').replaceChildren(...(done ? [] : [doubtTag(r), r.who && r.who.twice ? tag('warn', 'name twice') : '',
      r.failed.length ? tag('bad', 'read failed', r.failed.join(', ')) : '']));
    if (done) return;
    $('w-crops').replaceChildren(...r.crops.map(c => {
      const read = r.read[c.name];
      return make('figure', {}, crop(r.sub, c, 620), make('figcaption', {},
        make('span', {className: 'dim', textContent: c.name + ' '}),
        make('span', {className: read ? 'mono' : 'dim', textContent: read == null ? (r.read.hasOwnProperty(c.name) ? 'not read' : '—') : read})));
    }));
    const c = cand();
    const named = r.who;
    $('w-name').replaceChildren(named ? make('span', {textContent: named.name}) : c ? make('span', {textContent: c.name}) : make('span', {className: 'dim', textContent: 'no proposal'}));
    $('w-name').className = named && (!c || c.key === named.key) ? 'named' : '';
    $('w-id').textContent = named ? named.id : c ? c.id : '';
    const why = [];
    if (named) why.push(make('span', {className: 'tag good', textContent: 'named'}));
    if (c) {
      if (named && c.key !== named.key) why.push(make('span', {textContent: ` proposal: ${c.name} · ${c.id} · `}));
      why.push(make('span', {textContent: c.why}));
      if (c.taken) why.push(' ', tag('warn', `name twice: ${c.taken}`, 'already named for another exam'));
      if (r.candidates.length > 1) why.push(make('span', {className: 'dim', textContent: ` · ${ci % r.candidates.length + 1} of ${r.candidates.length}`}));
    }
    $('w-why').replaceChildren(...why);
    const next = view.byId[order[k + 1]];
    if (next) for (const x of next.crops) { const im = new Image(); im.src = page(next.sub, x.index); }
  }

  function renderRows() {
    const rows = order.map((s, j) => {
      const r = view.byId[s], c = r.candidates[0];
      const open = make('button', {type: 'button', className: 'quiet', textContent: s, title: 'step to it'});
      open.addEventListener('click', () => { go(j); $('w-step').scrollIntoView({block: 'nearest'}); });
      const read = r.crops.map(x => r.read[x.name]).filter(Boolean).join(' · ');
      const tr = make('tr', {className: j === k ? 'here' : ''},
        make('td', {}, open), make('td', {}, doubtTag(r)),
        make('td', {className: 'mono dim', textContent: read}),
        make('td', {}, r.who ? make('b', {textContent: `${r.who.name}${r.who.id ? ' · ' + r.who.id : ''}`})
          : c ? make('span', {className: 'dim', textContent: `${c.name} · ${c.id}?`}) : ''),
        make('td', {}, r.who && r.who.twice ? tag('warn', 'name twice') : ''));
      return tr;
    });
    $('w-rows').replaceChildren(...rows);
    $('w-nobody').hidden = !view.nobody.length;
    $('w-nobody-list').textContent = view.nobody.join(' · ');
  }

  function render() {
    const left = view.left, n = view.rows.length;
    $('names-left').textContent = left ? `${left} left` : '';
    const counts = {};
    for (const r of view.rows) if (!r.who) counts[r.doubt] = (counts[r.doubt] || 0) + 1;
    $('w-sum').replaceChildren(make('span', {className: left ? 'tag ask' : 'tag good', textContent: left ? `${left} of ${n} to name` : `all ${n} named`}),
      ...['disagree', 'nobody', 'unread'].filter(d => counts[d]).map(d => tag(DOUBT[d], `${counts[d]} ${view.doubts[d]}`)));
    $('w-roster').textContent = view.roster ? `roster: ${view.roster}` : 'no roster yet';
    renderStep();
    renderRows();
    Actions.hints();
  }

  tab(pref('scansTab', 'split') === 'names' ? 'names' : 'split');
  // the count on the tab, before it is opened
  if ($('names').hidden) request('/scans/names').then(r => { if (r.ok && r.body.left) $('names-left').textContent = `${r.body.left} left`; });
})();
