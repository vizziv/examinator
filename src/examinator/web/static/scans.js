'use strict';
// Scans: from stacks of scans to named submissions, in the steps of the strip at the top:
// 1 add scans, 2 split them into submissions, 3 the roster, 4 names (names.js), then Read.
// The line under the strip says what to do next.
//
// Split: one first page at a time, large, with its neighbours; verify it (V) or move it
// a page earlier (A) or later (D). Clicking any small page shows it large the same way;
// on a page that is not a first page, A and D look around and Shift+V starts a submission
// there. The overview below shows every submission, doubtful ones first.
(() => {
  const main = $('scans');
  if (!main) return;
  const [SMALL, MID, LARGE] = JSON.parse(main.dataset.widths);
  const counts = JSON.parse(main.dataset.counts);     // per scan {open, write}; the one shown is kept live
  const MODEL = !!main.dataset.model, LANDMARKS = !!main.dataset.landmarks;
  const PEOPLE = Number(main.dataset.people);
  let made = Number(main.dataset.made);
  let plan = null, k = 0;
  let peek = null;        // a page shown large that is not a first page, else null
  let names = null;       // from names.js: {n, left, unread, proposal}
  let current = null;     // the step shown

  const msg = $('msg');
  const say = text => { msg.textContent = text || ''; msg.hidden = !text; };
  const sheet = (i, w) => `${ROOT}/sheet/${encodeURIComponent(plan.id)}/${i}.jpg?o=${plan.sheet_o[i]}&w=${w}`;
  const reading = i => plan.readings[i] || '';
  const done = () => peek === null && k >= plan.entries.length;
  const firstOf = e => (e.pages.length ? e.pages[0] : e.start);
  const open = e => !e.verified;
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

  // --- the steps: one shown at a time; the strip counts what is left in each ---
  const tabs = [...document.querySelectorAll('[data-tab]')];
  const PANEL = {add: 'add', split: 'split', roster: 'roster-box', names: 'names'};
  function tab(name) {
    if (!$(PANEL[name])) name = 'add';
    current = name;
    for (const b of tabs) b.setAttribute('aria-selected', String(b.dataset.tab === name));
    for (const [t, id] of Object.entries(PANEL)) if ($(id)) $(id).hidden = t !== name;
    setPref('scansTab', name);
    if (name === 'split' && plan) history.replaceState(null, '', '#src=' + plan.id);
    say('');
    main.dispatchEvent(new CustomEvent('tab', {detail: name}));
    if (name === 'roster') ($('ro-filter') || text).focus();
    strip();
    Actions.hints();
    Actions.enable(main);
  }
  for (const b of tabs) b.addEventListener('click', () => tab(b.dataset.tab));
  main.addEventListener('names', e => {
    names = e.detail;
    if (names.n) made = Math.max(made, names.n);
    strip();
  });

  function strip() {
    const all = Object.values(counts);
    const open = all.reduce((a, c) => a + c.open, 0), write = all.reduce((a, c) => a + c.write, 0);
    if (all.length) {
      $('n-split').textContent = `(${[open ? `${open} unverified` : '', write ? `${write} to write` : ''].filter(Boolean).join(' · ') || 'done'})`;
    }
    $('n-names').textContent = names && names.n ? `(${names.left ? `${names.left} unverified` : 'done'})` : '';
    const off = tabs.find(b => b.dataset.tab === 'names');
    off.classList.toggle('off', !made);
    off.title = made ? 'who wrote each submission' : 'Names needs submissions';
    $('next').textContent = nextThing(open, write);
  }
  function nextThing(open, write) {
    const after = () => (!PEOPLE ? 'next: add a roster' : made ? 'next: Names' : 'next: Split');
    if (current === 'add') return !Object.keys(counts).length ?'next: Add scans' : open || write ? 'next: Split' : after();
    if (current === 'roster') return !PEOPLE ? 'paste the roster or load a file' : after();
    if (current === 'split') {
      if (!plan) return '';
      if (peek !== null) return 'not a first page';
      if (!done() && open && plan.entries[k] && !plan.entries[k].verified) return 'is this the first page of a submission?';
      const mine = counts[plan.id];
      if (mine.open) return `next: verify ${plural(mine.open, 'first page')}`;
      if (mine.write) return `next: Accept (writes ${plural(mine.write, 'submission')})`;
      if (open || write) return 'next: the other scans';
      return after();
    }
    if (!made) return 'no submissions yet: accept a split first';
    if (!names) return '';
    if (!names.left) return 'every submission is verified; next: Read';
    const fields = names.fields > 0;
    if (names.unread && fields && MODEL) return 'next: read the name boxes';
    if (!PEOPLE) return 'next: add a roster';
    if (!names.proposal) return MODEL || !fields ? 'type the name or ID' : 'no model: type names';
    return 'is this who wrote it?';
  }

  // --- adding scans ---
  $('up').addEventListener('change', async e => {
    for (const file of e.target.files) {
      say(`uploading ${file.name}…`);
      let res = null, body = {};
      try {
        res = await fetch(`${ROOT}/scans/upload?name=${encodeURIComponent(file.name)}`, {
          method: 'POST', body: file, headers: {'Content-Type': 'application/pdf', 'X-Token': meta('token')}});
        body = await res.json();
      } catch (err) { /* reported below */ }
      if (!res || !res.ok) { say(''); flash(`${file.name}: ${body.error || 'failed'}`); return; }
      if (!body.new) flash(`${file.name}: already added`, 'ok');
      location.hash = 'src=' + body.id;
    }
    setPref('scansTab', 'split');
    location.reload();
  });
  for (const b of document.querySelectorAll('[data-src]')) {
    b.addEventListener('click', () => { pick.value = b.dataset.src; load(pick.value); tab('split'); });
  }
  function scanCounts() {
    for (const [id, c] of Object.entries(counts)) {
      const cell = $('sc-' + id);
      if (cell) cell.replaceChildren(c.open ? tag('unv', `${c.open} unverified`) : '', ' ',
                                     c.write ? tag('unv', `${c.write} to write`) : tag('', 'all written'));
    }
  }

  // --- the roster: pasted or loaded, read by the server as it is typed (nothing saved) so a
  // wrong paste shows at once: how many people, the first few rows, the lines it could not read ---
  const text = $('roster-text');
  let timer = 0, asked = 0;
  async function preview() {
    const mine = ++asked;
    const value = text.value;
    if (!value.trim()) { showRoster(null); return; }
    const r = await request('/scans/roster', {text: value, dry: true});
    if (mine === asked) showRoster(r.ok ? r.body : {error: r.body.error || 'failed'});
  }
  function showRoster(b) {
    const ok = !!b && !b.error && b.people > 0 && !b.problems.length;
    $('roster-save').disabled = !ok;
    const bad = b && !b.error ? b.problems.length + b.more : 0;
    $('roster-read').textContent = !b ? '' : b.error || `${b.people} people${bad ? `, ${bad} lines unclear` : ''}`;
    $('roster-read').className = ok || !b ? 'dim' : 'bad';
    const sample = $('roster-sample'), problems = $('roster-problems');
    sample.hidden = !b || !b.sample || !b.sample.length;
    sample.replaceChildren(...(b && b.sample ? b.sample : []).map(p => make('tr', {},
      make('td', {textContent: p.name}), make('td', {className: 'mono', textContent: p.id}),
      make('td', {textContent: p.email}))));
    if (b && b.people > (b.sample || []).length) sample.append(make('tr', {}, make('td', {textContent: '…'})));
    problems.hidden = !b || !b.problems || !b.problems.length;
    problems.replaceChildren(...(b && b.problems ? b.problems : []).map(p => make('li', {textContent: p})));
  }
  text.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(preview, 250); });
  $('roster').addEventListener('change', async e => {
    const file = e.target.files[0];
    if (!file) return;
    text.value = await file.text();
    preview();
  });
  $('roster-save').addEventListener('click', async () => {
    const r = await request('/scans/roster', {text: text.value});
    if (!r.ok) { showRoster({error: r.body.error || 'failed'}); return; }
    location.reload();
  });

  addEventListener('hashchange', () => { if (/sub=/.test(location.hash)) tab('names'); });
  const pick = $('src');
  if (!pick) {
    tab(/sub=/.test(location.hash) ? 'names' : pref('scansTab', 'add'));
    return;
  }

  // --- loading and editing the plan ---
  const countsOf = p => ({open: p.entries.filter(open).length,
                          write: p.entries.filter(e => e.state !== 'made').length + p.removed.length});
  async function load(id, keep) {
    const r = await request(`/scans/${encodeURIComponent(id)}/plan`);
    if (!r.ok) { flash('could not load the scan'); return; }
    if (!keep || !plan || plan.id !== r.body.id) { say(''); k = r.body.cursor ?? r.body.entries.length; peek = null; }
    plan = r.body;
    if (current === 'split') history.replaceState(null, '', '#src=' + id);
    render();
  }
  async function send(path, data) {
    const r = await request(`/scans/${encodeURIComponent(plan.id)}/${path}`, {rev: plan.rev, ...data});
    if (!r.ok) {
      if (r.status !== 409 || !r.body.current) { flash(r.body.error || 'failed'); if (r.status === 409) await load(plan.id, true); }
      return r;
    }
    if (r.body.plan) { plan = r.body.plan; render(); }
    return r;
  }
  const S = steps(() => !!plan && current === 'split', () => { if (plan) render(); });
  const edit = (op, data) => send('edit', {op, ...data});

  // --- the step-through ---
  const at = () => !done() && peek === null;                     // on a submission's first page
  const owner = i => plan.entries.findIndex(e => e.pages.includes(i));
  const focus = () => (peek !== null ? peek : firstOf(plan.entries[k])); // the page shown large
  // Show page `i` large: as its submission's first page if it is one, else on its own.
  function show(i) {
    if (i < 0 || i >= plan.n) return;
    const j = plan.entries.findIndex(e => firstOf(e) === i);
    if (j >= 0) { k = j; peek = null; } else { peek = i; if (owner(i) >= 0) k = owner(i); }
    render();
  }
  S.register('split-verify', {available: at, run: async () => {
    const r = await edit('confirm', {k});
    if (r && r.ok) { k = nextOpen(plan.entries, k, open); render(); }
  }});
  S.register('split-earlier', {available: () => !done() && (peek === null || peek > 0),
    run: () => (peek === null ? edit('move', {k, delta: -1}) : show(peek - 1))});
  S.register('split-later', {available: () => !done() && (peek === null || peek < plan.n - 1),
    run: () => (peek === null ? edit('move', {k, delta: 1}) : show(peek + 1))});
  S.register('split-back', {available: () => (peek !== null ? owner(peek) >= 0 : k > 0), run: () => {
    if (peek !== null) peek = null; else k = Math.min(k, plan.entries.length) - 1;
    render();
  }});
  S.register('split-next', {available: () => !done() && k + 1 < plan.entries.length,
    run: () => { peek = null; k += 1; render(); }});
  S.register('split-cut', {available: () => peek !== null && !plan.dropped.includes(peek),
    run: () => edit('cut', {sheet: peek})});
  S.register('split-drop', {available: () => !done() && !plan.dropped.includes(focus()),
    run: () => edit('drop', {sheet: focus()})});
  S.register('split-turn', {available: () => !done() && (peek === null || owner(peek) >= 0), run: () => edit('turn', {k})});
  S.register('split-turn-source', {run: async () => {
    const r = await edit('turn_source');
    if (r && r.ok) say(LANDMARKS && MODEL ? 'turned: read pages again' : 'turned');
  }});
  S.register('split-mirror', {run: () => edit('flip_source')});
  S.register('split-undo', {available: ctx => plan.undo && !Actions.typing(ctx.target), run: () => send('undo', {})});
  S.register('split-guess', {run: async () => {
    const r = await send('propose', {length: $('s-length').value});
    if (r && r.ok) { k = plan.cursor ?? plan.entries.length; peek = null; render(); }
  }});
  S.register('split-read', {available: () => MODEL && LANDMARKS, long: true, run: async () => {
    say('reading pages…');
    const r = await send('propose', {check: true, length: $('s-length').value});
    if (!r || !r.ok) { say(''); return; }
    const job = await waitJob(r.body.job);
    say(job.state === 'done' ? '' : `read pages ${job.state}${job.error ? ': ' + job.error : ''}`);
    await load(plan.id);
  }});
  S.register('split-accept', {long: true, run: async () => {
    const flagged = plan.entries.filter(e => e.doubts.length).length;
    const unverified = plan.entries.filter(open).length;
    if ((flagged || unverified) && !confirm(`${flagged} flagged, ${unverified} unverified. Write the submissions anyway?`)) return;
    let r = await send('accept', {});
    if (r && r.status === 409 && r.body.current && r.body.current.busy) {
      const busy = r.body.current.busy, many = busy.length > 1;
      if (!confirm(`${busy.join(', ')} already ${many ? 'have' : 'has'} grading work, and the split changes or removes ${many ? 'them' : 'it'}. Rewrite anyway? The work stays but may no longer match the pages.`)) return;
      r = await send('accept', {force: true});
    }
    if (r && r.ok) {
      const x = r.body.report;
      made += x.new.length - x.removed.length;
      say(`written: ${x.new.length} new, ${x.rewritten.length} rewritten, ${x.removed.length} removed, ${x.unchanged} unchanged`);
      render();
    }
  }});

  function figure(el, i, w, what) {
    const img = el.querySelector('img'), cap = el.querySelector('figcaption');
    el.hidden = i < 0 || i >= plan.n;
    if (el.hidden) return;
    img.src = sheet(i, w);
    const gone = plan.loose.includes(i);
    cap.replaceChildren(make('span', {textContent: `${what} · page ${i + 1}`}),
      reading(i) ? tag('', reading(i), 'what the page says it is') : '',
      gone ? tag('', plan.dropped.includes(i) ? 'dropped' : 'in no submission') : '');
  }
  function preload(j) {
    const e = plan.entries[j];
    if (!e) return;
    const s = firstOf(e);
    for (const [i, w] of [[s, LARGE], [s - 1, MID], [s + 1, MID]]) {
      if (i >= 0 && i < plan.n) { const im = new Image(); im.src = sheet(i, w); }
    }
  }
  // One submission's badges, the same wherever it is shown.
  const STATE = {made: ['', 'written', 'on disk as planned'],
                 changed: ['unv', 'to rewrite', 'on disk, but the plan has changed since: Accept rewrites it'],
                 new: ['unv', 'to write', 'not on disk yet: Accept writes it']};
  function badges(e) {
    const [cls, text, title] = STATE[e.state];
    return [e.verified ? tag('', 'verified', 'its first page is verified') : tag('unv', 'unverified', 'its first page is a guess'),
            tag(cls, text, title), ...e.doubts.map(d => tag('bad', d)), e.own ? tag('', 'turned') : ''];
  }
  // The neighbours in the step view: a click looks at that page.
  for (const [id, d] of [['st-a', -1], ['st-c', 1]]) {
    $(id).addEventListener('click', () => { if (plan && !done()) show(focus() + d); });
  }
  function renderStep() {
    const E = plan.entries, finished = done();
    $('st-done').hidden = !finished;
    $('st-pages').hidden = finished;
    const mine = peek === null ? -1 : owner(peek);
    $('st-k').textContent = finished ? 'done' : peek === null ? `submission ${k + 1} of ${E.length}`
      : mine < 0 ? 'in no submission' : `submission ${mine + 1} of ${E.length} · its page ${E[mine].pages.indexOf(peek) + 1}`;
    const towrite = counts[plan.id].write;
    $('st-done-sum').textContent = !finished ? '' : towrite ? `Accept writes them (${towrite} to write).` : 'All written.';
    const tags = [];
    if (!finished) {
      const s = focus();
      if (peek === null) tags.push(...badges(E[k]));
      else tags.push(tag('', 'not a first page'));
      figure($('st-a'), s - 1, MID, 'before');
      figure($('st-b'), s, LARGE, peek === null ? 'first page?' : 'this page');
      figure($('st-c'), s + 1, MID, 'after');
      $('st-b').querySelector('figcaption').append(make('a', {href: sheet(s, 0), target: '_blank', rel: 'noopener', textContent: 'full size'}));
      if (peek === null) preload(k + 1);
    }
    const prev = finished ? E[E.length - 1] : peek !== null ? null : E[k - 1];
    $('st-tags').replaceChildren(...tags);
    $('st-prev').textContent = prev ? `submission ${finished ? E.length : k}: ${prev.pages.length} of ${plan.length} pages${prev.doubts.length ? ' · ' + prev.doubts.join(', ') : ''}` : '';
    $('st-prev').classList.toggle('bad', !!prev && prev.doubts.length > 0);
  }

  // --- the overview ---
  // A plan-changing button in a row: through the same queue as the keys.
  const button = (text, title, fn) => {
    const b = make('button', {type: 'button', className: 'quiet', textContent: text, title});
    b.addEventListener('click', () => { if (!S.busy()) S.enqueue(fn); });
    return b;
  };
  function thumb(i, w, buttons) {
    const fig = make('figure', {className: 'sh'},
      opener(i, make('img', {src: sheet(i, w), alt: `page ${i + 1}`, loading: 'lazy'})),
      make('figcaption', {}, String(i + 1), reading(i) ? make('span', {className: 'dim', textContent: ' ' + reading(i)}) : ''));
    if (buttons.length) fig.append(make('span', {className: 'acts'}, ...buttons));
    return fig;
  }
  // A small page: a click shows it large in the step view above.
  function opener(i, img) {
    const b = make('button', {type: 'button', className: 'pg', title: 'show this page large'}, img);
    b.addEventListener('click', () => { show(i); $('step').scrollIntoView({block: 'nearest'}); });
    return b;
  }
  // The submission's name and ID boxes, cut from the sheets as planned (cover sheets come first),
  // inside the scan's print area.
  const idboxes = JSON.parse(main.dataset.idboxes || '[]');
  function nameCrops(e) {
    return (plan.idboxes || idboxes).map(b => {
      const off = b.offset + (plan.cover || 0);
      if (e.pages.length <= off) return '';
      const c = make('span', {className: 'crop', title: b.name});
      Object.assign(c.dataset, {src: sheet(e.pages[off], LARGE), box: b.box, width: 240});
      drawCrop(c);
      return c;
    });
  }
  // The name, read-only: it is given on the Names step, which the link opens on this submission.
  function nameLink(e) {
    if (!e.id || e.state === 'new') return '';
    const a = make('a', {href: '#sub=' + encodeURIComponent(e.id), className: e.name ? '' : 'dim',
                         textContent: e.name || 'no name', title: 'open on Names'});
    a.addEventListener('click', ev => {      // also when the link is the one followed last
      ev.preventDefault();
      history.replaceState(null, '', a.getAttribute('href'));
      dispatchEvent(new HashChangeEvent('hashchange'));
    });
    return a;
  }
  function renderRows() {
    const E = plan.entries;
    const order = E.map((e, j) => j);
    const fine = j => (E[j].doubts.length || !E[j].verified ? 0 : 1);
    const here = done() || (peek !== null && owner(peek) < 0) ? -1 : k;
    order.sort((a, b) => fine(a) - fine(b) || a - b);
    const twice = {};
    for (const e of E) if (e.name) twice[e.name] = (twice[e.name] || 0) + 1;
    const rows = order.map(j => {
      const e = E[j];
      const go = make('button', {type: 'button', className: 'quiet', textContent: `#${j + 1}`, title: 'step through from here'});
      go.addEventListener('click', () => { k = j; peek = null; render(); $('step').scrollIntoView({block: 'nearest'}); });
      const head = make('div', {className: 'rhead'}, go,
        e.id ? make('code', {textContent: e.id}) : '',
        ...badges(e),
        e.name && twice[e.name] > 1 ? tag('bad', 'name twice') : '',
        make('span', {className: 'grow'}),
        nameLink(e),
        j + 1 < E.length ? button('join with next', 'this submission and the next are one', () => edit('join', {k: j})) : '');
      const sheets = make('div', {className: 'sheets'}, ...e.pages.map((i, n) => {
        const fig = thumb(i, n ? SMALL : MID, [
          ...(n ? [button('✂', 'new submission here', () => edit('cut', {sheet: i}))] : []),
          button('✕', 'drop page', () => edit('drop', {sheet: i}))]);
        if (!n) fig.classList.add('first');
        return fig;
      }), ...nameCrops(e));
      return make('div', {className: 'srow' + (fine(j) ? '' : ' look') + (j === here ? ' here' : '')}, head, sheets);
    });
    $('rows').replaceChildren(...rows);
    $('loose').hidden = !plan.loose.length;
    $('loose-sheets').replaceChildren(...plan.loose.map(i => thumb(i, SMALL, [
      plan.dropped.includes(i) ? button('put back', 'put the page back', () => edit('restore', {sheet: i}))
        : button('✂', 'new submission here', () => edit('cut', {sheet: i}))])));
  }

  function render() {
    const E = plan.entries;
    // A page being looked at that has become a first page (or gone) is no longer just looked at.
    if (peek !== null) {
      const j = E.findIndex(e => firstOf(e) === peek);
      if (j >= 0) { k = j; peek = null; } else if (peek >= plan.n) peek = null;
      else if (owner(peek) >= 0) k = owner(peek);
    }
    // Never "done" while a first page is unverified (an undo or a join can shorten the list).
    if (peek === null && k >= E.length && E.some(open)) k = plan.cursor ?? nextOpen(E, -1, open);
    counts[plan.id] = countsOf(plan);
    const info = [`${plan.n} pages`, plural(E.length, 'submission')];
    if (plan.checked) info.push('pages read');
    $('s-info').replaceChildren(info.join(' · '), ' ',
      plan.failed ? tag('bad', `${plural(plan.failed, 'page')} not read: model failed`) : '');
    $('s-orient').textContent = plan.orient;
    if (document.activeElement !== $('s-length')) $('s-length').value = plan.length;
    const flagged = E.filter(e => e.doubts.length).length, unverified = E.filter(open).length;
    const towrite = counts[plan.id].write;
    $('s-sum').replaceChildren(
      flagged ? tag('bad', `${flagged} flagged`) : '',
      unverified ? tag('unv', `${unverified} unverified`) : '',
      plan.removed.length ? tag('unv', `${plan.removed.length} to remove: ${plan.removed.join(', ')}`) : '',
      towrite ? tag('unv', `${towrite} to write`) : tag('', 'all written'));
    renderStep();
    renderRows();
    scanCounts();
    strip();
    Actions.hints();
    Actions.enable(main);
  }

  pick.addEventListener('change', () => load(pick.value));
  // the scan in the link, else the first with work left, else the first
  const want = (location.hash.match(/src=([\w-]+)/) || [])[1];
  const ids = [...pick.options].map(o => o.value);
  pick.value = ids.includes(want) ? want : ids.find(id => counts[id] && (counts[id].open || counts[id].write)) || ids[0];
  scanCounts();
  tab(/sub=/.test(location.hash) ? 'names' : pref('scansTab', 'split'));
  load(pick.value);
})();
