'use strict';
// Scans: from stacks of scans to named submissions, in the steps of the strip at the top:
// 1 add scans, 2 split them into submissions, 3 the roster, 4 names (names.js), then Read.
// The line under the strip says what to do next.
//
// Split: one first page at a time, large, with its neighbours; verify the submission (V: it
// is these pages, and nothing guessed after it moves it) or move its first page a page
// earlier (A) or later (D). Clicking any small page shows it large the same way;
// on a page that is not a first page, A and D look around and Shift+V starts a submission
// there. The page shown can be marked extra (kept, not a printed page) or have a missing
// page marked before it, and moved one place in the pile (the plan is one ordered list of
// the scan's pages; neighbours are neighbours in it). The overview below shows every
// submission, doubtful ones first, each page with the printed page it is.
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

  // The line under the strip: how the last change on the step shown went (a change there replaces it).
  const line = Line('flow-msg'), say = (text, tone) => line.say(text, tone);
  const sheet = (i, w) => `${ROOT}/sheet/${encodeURIComponent(plan.id)}/${i}.jpg?o=${plan.turn[i]}&w=${w}`;
  const near = (i, d) => plan.order[plan.order.indexOf(i) + d] ?? -1;     // a neighbour in the pile, else -1
  const reading = i => plan.readings[i] || '';
  const done = () => peek === null && k >= plan.entries.length;
  const open = e => !e.verified;
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

  // --- the steps: one shown at a time; the strip counts what is left in each ---
  const tabs = [...document.querySelectorAll('[data-tab]')];
  const panel = name => $('step-' + name);
  function tab(name) {
    if (!panel(name)) name = 'add';
    current = name;
    for (const b of tabs) b.setAttribute('aria-selected', String(b.dataset.tab === name));
    for (const b of tabs) if (panel(b.dataset.tab)) panel(b.dataset.tab).hidden = b.dataset.tab !== name;
    setPref('scansTab', name);
    if (linked() && linked() !== name) history.replaceState(null, '', location.pathname);   // a link to another step: followed
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
      $('flow-n-split').textContent = `(${[open ? `${open} unverified` : '', write ? `${write} to write` : ''].filter(Boolean).join(' · ') || 'done'})`;
    }
    $('flow-n-names').textContent = names && names.n ? `(${names.left ? `${names.left} unverified` : 'done'})` : '';
    const off = tabs.find(b => b.dataset.tab === 'names');
    off.classList.toggle('off', !made);
    off.title = made ? 'who wrote each submission' : 'Names needs submissions';
    $('flow-next').textContent = nextThing(open, write);
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
      if (mine.open) return `next: verify ${plural(mine.open, 'submission')}`;
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
    if (!names.proposal && names.named) return 'named; next: D';
    if (!names.proposal) return MODEL || !fields ? 'type the name or ID' : 'no model: type names';
    return 'is this who wrote it?';
  }

  // --- adding scans ---
  $('add-file').addEventListener('change', async e => {
    for (const file of e.target.files) {
      say(`uploading ${file.name}…`);
      let res = null, body = {};
      try {
        res = await fetch(`${ROOT}/scans/upload?name=${encodeURIComponent(file.name)}`, {
          method: 'POST', body: file, headers: {'Content-Type': 'application/pdf', 'X-Token': meta('token')}});
        body = await res.json();
      } catch (err) { /* reported below */ }
      if (!res || !res.ok) { say(`${file.name}: ${body.error || 'failed'}`, 'bad'); return; }
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
      const cell = $('add-n-' + id);
      if (cell) cell.replaceChildren(c.open ? tag('unv', `${c.open} unverified`) : '', ' ',
                                     c.write ? tag('unv', `${c.write} to write`) : tag('', 'all written'));
    }
  }

  // --- the roster: pasted or loaded, read by the server as it is typed (nothing saved) so a
  // wrong paste shows at once: how many people, the first few rows, the lines it could not read ---
  const text = $('ro-text');
  let timer = 0, asked = 0, rosterOk = false;
  async function preview() {
    const mine = ++asked;
    const value = text.value;
    if (!value.trim()) { showRoster(null); return; }
    const r = await Changes.query('/scans/roster/preview', {text: value});
    if (mine === asked) showRoster(r.ok ? r.body : {error: r.body.error || 'failed'});
  }
  function showRoster(b) {
    const ok = rosterOk = !!b && !b.error && b.people > 0 && !b.problems.length;
    $('ro-save').disabled = !ok;
    const bad = b && !b.error ? b.problems.length + b.more : 0;
    $('ro-read').textContent = !b ? '' : b.error || `${b.people} people${bad ? `, ${bad} lines unclear` : ''}`;
    $('ro-read').className = ok || !b ? 'dim' : 'bad';
    const sample = $('ro-sample'), problems = $('ro-problems');
    sample.hidden = !b || !b.sample || !b.sample.length;
    sample.replaceChildren(...(b && b.sample ? b.sample : []).map(p => make('tr', {},
      make('td', {textContent: p.name}), make('td', {className: 'mono', textContent: p.id}),
      make('td', {textContent: p.email}))));
    if (b && b.people > (b.sample || []).length) sample.append(make('tr', {}, make('td', {textContent: '…'})));
    problems.hidden = !b || !b.problems || !b.problems.length;
    problems.replaceChildren(...(b && b.problems ? b.problems : []).map(p => make('li', {textContent: p})));
  }
  text.addEventListener('input', () => { clearTimeout(timer); timer = setTimeout(preview, 250); });
  $('ro-file').addEventListener('change', async e => {
    const file = e.target.files[0];
    if (!file) return;
    text.value = await file.text();
    preview();
  });
  // Saving replaces the roster the page was drawn with (its revision on the button). Refused
  // as stale, the text stays and the roster saved now is what saving again replaces.
  Actions.register('roster-save', {available: () => rosterOk, run: () => {
    const typed = text.value, save = $('ro-save');
    return Changes.send('roster', '/scans/roster', () => ({text: typed, rev: save.dataset.rev || null}), {
      line, done: () => location.reload(),
      redraw: cur => { save.dataset.rev = cur.rev ?? ''; return Changes.kept(cur.people === 1 ? "1 person" : `${cur.people || 0} people`); }});
  }});

  // A link to a submission on Names (#sub=) or a scan on Split (#src=) opens that step, also when it is the link followed last.
  const linked = () => (/sub=/.test(location.hash) ? 'names' : /src=/.test(location.hash) ? 'split' : null);
  addEventListener('hashchange', () => { if (linked()) tab(linked()); });
  document.addEventListener('click', e => {
    const a = e.target.closest?.('a[href^="#sub="], a[href^="#src="]');
    if (!a) return;
    e.preventDefault();
    history.replaceState(null, '', a.getAttribute('href'));
    dispatchEvent(new HashChangeEvent('hashchange'));
  });
  const pick = $('sp-src');
  if (!pick) {
    tab(linked() || pref('scansTab', 'add'));
    return;
  }

  // --- loading and editing the plan (the server says whose each page is and what it counts as) ---
  // The cursor: after a person's own edit it stays where they are working; after any other
  // redraw from the server's plan (load, undo, guess again, read pages, a 409) it is the plan's (`adopt`).
  const revs = {};       // scan -> the revision of its plan last drawn
  function adopt(p) {
    if (!plan || plan.id !== p.id) say('');
    plan = p;
    revs[p.id] = p.rev;
    k = p.cursor ?? p.entries.length;
    peek = null;
  }
  async function load(id) {
    const r = await Changes.get(`/scans/${encodeURIComponent(id)}/plan`);
    if (!r.ok) { say("could not load the scan", 'bad'); return; }
    adopt(r.body);
    if (current === 'split') history.replaceState(null, '', '#src=' + id);
    render();
  }
  // Every edit names the pages it is about (a submission by its first page), as shown when
  // the key was pressed; Changes sends them in order, each against the plan last drawn.
  function send(path, data, then, long) {
    const id = plan.id, mine = b => b && b.id === id && plan.id === id;
    return Changes.send(`plan:${id}`, `/scans/${encodeURIComponent(id)}/${path}`, () => ({...data, rev: revs[id]}), {
      long, line,
      redraw: cur => { if (cur.id) revs[cur.id] = cur.rev; if (mine(cur)) { adopt(cur); render(); } },
      done: async b => {
        if (b.plan) revs[id] = b.plan.rev;
        if (mine(b.plan)) { plan = b.plan; render(); }
        if (then) await then(b);
      }});
  }
  Actions.gate('split', () => !!plan && current === 'split' && !Changes.busy());
  const register = Actions.register;
  const edit = (op, data, then) => send('edit', {op, ...data}, then);

  // --- the step-through ---
  const at = () => !done() && peek === null;                     // on a submission's first page
  const owner = i => plan.owner[i] ?? -1;                       // its submission, or -1: dropped
  const focus = () => (peek !== null ? peek : plan.entries[k].start); // the page shown large
  // Show page `i` large: as its submission's first page if it is one, else on its own.
  function show(i) {
    if (i < 0 || i >= plan.n) return;
    const j = plan.entries.findIndex(e => e.start === i);
    if (j >= 0) { k = j; peek = null; } else { peek = i; if (owner(i) >= 0) k = owner(i); }
    moved();
  }
  // Verified: on to the next unverified first page, unless the person has moved on already.
  const first = () => plan.entries[k].start;                   // submission k, as edits name it
  register('split-verify', {available: at, run: () => {
    const was = first();
    return edit('confirm', {sheet: was}, () => {
      if (peek === null && plan.entries[k]?.start === was) { k = nextOpen(plan.entries, k, open); moved(); }
    });
  }});
  // On a first page, A and D move it (edits); on another page they look around (moves: they repeat).
  const looking = () => peek !== null;
  register('split-earlier', {available: () => !done() && (peek === null || near(peek, -1) >= 0), repeat: looking,
    run: () => (peek === null ? edit('move', {sheet: first(), delta: -1}) : show(near(peek, -1)))});
  register('split-later', {available: () => !done() && (peek === null || near(peek, 1) >= 0), repeat: looking,
    run: () => (peek === null ? edit('move', {sheet: first(), delta: 1}) : show(near(peek, 1)))});
  register('split-back', {available: () => (peek !== null ? owner(peek) >= 0 : k > 0), repeat: true, run: () => {
    if (peek !== null) peek = null; else k = Math.min(k, plan.entries.length) - 1;
    moved();
  }});
  register('split-next', {available: () => !done() && k + 1 < plan.entries.length, repeat: true,
    run: () => { peek = null; k += 1; moved(); }});
  register('split-cut', {available: () => peek !== null && !plan.dropped.includes(peek),
    run: () => edit('cut', {sheet: peek})});
  register('split-drop', {available: () => !done() && !plan.dropped.includes(focus()),
    run: () => edit('drop', {sheet: focus()})});
  // The page shown, in its submission: extra or counted, a missing page before it, its place in the pile.
  const inSub = () => !done() && owner(focus()) >= 0;
  const marked = i => plan.missing.includes(i);
  const place = d => plan.kept[plan.kept.indexOf(focus()) + d] !== undefined;   // a move steps over dropped pages
  register('split-extra', {available: inSub, run: () => edit('extra', {sheet: focus()})});
  register('split-missing', {available: inSub, run: () => edit('missing', {sheet: focus()})});
  register('split-unmissing', {available: () => inSub() && marked(focus()), run: () => edit('unmissing', {sheet: focus()})});
  const shift = d => ({available: () => inSub() && place(d), run: () => {
    const i = focus();
    return edit('shift', {sheet: i, delta: d}, () => show(i));
  }});
  register('split-shift-earlier', shift(-1));
  register('split-shift-later', shift(1));
  // Turning, a quarter turn clockwise more for each page: the page shown, its submission's, or (a button only) the scan's.
  const whole = b => { adopt(b.plan); render(); };     // the answer replaces the plan as a whole, not an edit of the person's
  const turned = () => say(LANDMARKS && MODEL ? 'turned: read pages again' : 'turned');
  register('split-turn-page', {available: () => !done(), run: () => edit('turn_page', {sheet: focus()}, turned)});
  register('split-turn', {available: () => !done() && (peek === null || owner(peek) >= 0), run: () => edit('turn', {sheet: first()}, turned)});
  register('split-turn-source', {run: () => edit('turn_source', {}, turned)});
  register('split-undo', {available: ctx => plan.undo && !Actions.typing(ctx.target), run: () => send('undo', {}, whole)});
  register('split-guess', {run: () => send('propose', {length: $('sp-length').value}, whole)});
  // Reading the scan's pages: a run, after which the plan is drawn again.
  register('split-read', {available: () => MODEL && LANDMARKS, run: () => {
    const id = plan.id;
    return Changes.run(`read:${id}`, `/scans/${encodeURIComponent(id)}/read`, {length: $('sp-length').value},
      {line, done: async () => { if (plan.id === id) await load(id); return 'pages read'; }});
  }});
  // Accept, having said what it would disturb: written submissions it rewrites or removes that
  // a person said something about. The person agrees to exactly those (`allow`).
  const SAID = {values: 'verified values', decisions: 'decisions', name: 'a name', regions: 'own regions'};
  const AFTER = {rewrite: 'Rewritten: the name, decisions and verified values stay; what was read and own regions go.',
                 remove: 'Removed: the submission and all of it go.'};
  register('split-accept', {run: () => {
    const flagged = plan.entries.filter(e => e.doubts.length).length;
    const unverified = plan.entries.filter(open).length;
    if ((flagged || unverified) && !confirm(`${flagged} flagged, ${unverified} unverified. Write the submissions anyway?`)) return;
    const busy = plan.busy;
    const said = busy.map(b => `${b.id} (${b.said.map(w => SAID[w]).join(', ')}): ${b.change === 'remove' ? 'removed' : 'rewritten'}`);
    const after = [...new Set(busy.map(b => b.change))].map(c => AFTER[c]);
    if (busy.length && !confirm([...said, '', ...after, 'Accept anyway?'].join('\n'))) return;
    return send('accept', {allow: busy.map(b => b.id)}, b => {
      const x = b.report;
      made += x.new.length - x.removed.length;
      say(`written: ${x.new.length} new, ${x.rewritten.length} rewritten, ${x.removed.length} removed, ${x.unchanged} unchanged`);
      render();
    }, true);
  }});

  // What sheet `i` is in its submission: 'p3' or 'extra', and the pages marked missing just before it.
  const slotTags = i => {
    const s = plan.slot[i];
    return s ? [...s.before.map(p => tag('bad', `missing p${p}`, 'a printed page not scanned, before this one')),
                tag(s.label === 'extra' ? 'unv' : '', s.label, s.label === 'extra' ? 'kept, not a printed page' : 'the printed page it counts as')] : [];
  };
  function figure(el, i, w, what) {
    const img = el.querySelector('img'), cap = el.querySelector('figcaption');
    el.hidden = i < 0 || i >= plan.n;
    if (el.hidden) return;
    img.src = sheet(i, w);
    cap.replaceChildren(make('span', {textContent: `${what} · page ${i + 1}`}), ...slotTags(i),
      reading(i) ? tag('', reading(i), 'what the page says it is') : '',
      plan.dropped.includes(i) ? tag('', 'dropped') : '');
  }
  function preload(j) {
    const e = plan.entries[j];
    if (!e) return;
    const s = e.start;
    for (const [i, w] of [[s, LARGE], [near(s, -1), MID], [near(s, 1), MID]]) {
      if (i >= 0) { const im = new Image(); im.src = sheet(i, w); }
    }
  }
  // One submission's badges, the same wherever it is shown.
  const STATE = {made: ['', 'written', 'on disk as planned'],
                 changed: ['unv', 'to rewrite', 'on disk, but the plan has changed since: Accept rewrites it'],
                 new: ['unv', 'to write', 'not on disk yet: Accept writes it']};
  function badges(e) {
    const [cls, text, title] = STATE[e.state];
    return [e.verified ? tag('', 'verified', 'a person said it is these pages') : tag('unv', 'unverified', 'nobody has verified these pages'),
            tag(cls, text, title), ...e.doubts.map(d => tag('bad', d)),
            e.pages.some(i => plan.turn[i]) ? tag('', 'turned', 'some of its pages are turned') : ''];
  }
  // The neighbours in the step view: a click looks at that page.
  for (const [id, d] of [['sp-a', -1], ['sp-c', 1]]) {
    $(id).addEventListener('click', () => { if (plan && !done()) show(near(focus(), d)); });
  }
  function renderStep() {
    const E = plan.entries, finished = done();
    $('sp-done').hidden = !finished;
    $('sp-pages').hidden = finished;
    const mine = peek === null ? -1 : owner(peek);
    $('sp-k').textContent = finished ? 'done' : peek === null ? `submission ${k + 1} of ${E.length}`
      : mine < 0 ? 'in no submission' : `submission ${mine + 1} of ${E.length} · its sheet ${E[mine].pages.indexOf(peek) + 1}`;
    const towrite = counts[plan.id].write;
    $('sp-done-sum').textContent = !finished ? '' : towrite ? `Accept writes them (${towrite} to write).` : 'All written.';
    const tags = [];
    if (!finished) {
      const s = focus();
      if (peek === null) tags.push(...badges(E[k]));
      else tags.push(tag('', 'not a first page'));
      figure($('sp-a'), near(s, -1), MID, 'before');
      figure($('sp-b'), s, LARGE, peek === null ? 'first page?' : 'this page');
      figure($('sp-c'), near(s, 1), MID, 'after');
      $('sp-b').querySelector('figcaption').append(make('a', {href: sheet(s, 0), target: '_blank', rel: 'noopener', textContent: 'full size'}));
      if (peek === null) preload(k + 1);
    }
    const prev = finished ? E[E.length - 1] : peek !== null ? null : E[k - 1];
    $('sp-tags').replaceChildren(...tags);
    $('sp-prev').textContent = prev ? `submission ${finished ? E.length : k}: ${prev.pages.length} of ${plan.length} pages${prev.doubts.length ? ' · ' + prev.doubts.join(', ') : ''}` : '';
    $('sp-prev').classList.toggle('bad', !!prev && prev.doubts.length > 0);
  }

  // --- the overview: drawn when the plan changes; its controls are actions on the sheet they carry ---
  const sheetOf = ctx => Number(ctx.el.dataset.sheet);
  for (const [name, op] of [['join', 'join'], ['cut', 'cut'], ['drop', 'drop'], ['restore', 'restore'], ['unmissing', 'unmissing']]) {
    register(`split-row-${name}`, {run: ctx => edit(op, {sheet: sheetOf(ctx)})});
  }
  // A small page (or a submission's number): a click shows it large in the step view above.
  register('split-row-show', {run: ctx => { show(sheetOf(ctx)); $('sp-step').scrollIntoView({block: 'nearest'}); }});
  const act = (name, sheet, text, title, cls = 'quiet') => {
    const b = make('button', {type: 'button', className: cls, textContent: text, title});
    Object.assign(b.dataset, {action: `split-row-${name}`, sheet, hint: 'none'});
    return b;
  };
  function thumb(i, w, buttons, label) {
    const open = act('show', i, '', 'show this page large', 'pg');
    open.append(make('img', {src: sheet(i, w), alt: `page ${i + 1}`, loading: 'lazy'}));
    const fig = make('figure', {className: 'sh' + (label === 'extra' ? ' extra' : '')}, open,
      make('figcaption', {}, String(i + 1), label ? make('b', {textContent: ' ' + label}) : '',
           reading(i) ? make('span', {className: 'dim', textContent: ' ' + reading(i)}) : ''));
    if (buttons.length) fig.append(make('span', {className: 'acts'}, ...buttons));
    return fig;
  }
  // A page the plan says was not scanned; one marked before a sheet can be unmarked.
  function gap(p, before) {
    return make('figure', {className: 'sh gap'}, make('span', {textContent: `missing p${p}`}),
      before === undefined ? '' : make('span', {className: 'acts'}, act('unmissing', before, '✕', 'not missing after all')));
  }
  // The submission's name and ID boxes, cut from the sheets that are their printed pages,
  // inside the scan's print area.
  const idboxes = JSON.parse(main.dataset.idboxes || '[]');
  function nameCrops(e) {
    return (plan.idboxes || idboxes).map(b => {
      const at = e.lay.find(([, p]) => p === b.offset + 1);
      if (!at || at[0] === null) return '';
      const c = make('span', {className: 'crop', title: b.name});
      Object.assign(c.dataset, {src: sheet(at[0], LARGE), box: b.box, width: 240});
      drawCrop(c);
      return c;
    });
  }
  // The name, read-only: it is given on the Names step, which the link opens on this submission.
  const nameLink = e => (!e.id || e.state === 'new' ? '' : make('a', {href: '#sub=' + encodeURIComponent(e.id),
    className: e.name ? '' : 'dim', textContent: e.name || 'no name', title: 'open on Names'}));
  function renderRows() {
    const E = plan.entries;
    const order = E.map((e, j) => j);
    const fine = j => (E[j].doubts.length ? -E[j].doubts.length : E[j].verified ? 1 : 0);   // doubtful first, most first; then unverified
    order.sort((a, b) => fine(a) - fine(b) || a - b);
    const twice = {};
    for (const e of E) if (e.name) twice[e.name] = (twice[e.name] || 0) + 1;
    const rows = order.map(j => {
      const e = E[j];
      const head = make('div', {className: 'rhead'}, act('show', e.start, `#${j + 1}`, 'step through from here'),
        e.id ? make('code', {textContent: e.id}) : '',
        ...badges(e),
        e.name && twice[e.name] > 1 ? tag('bad', 'name twice') : '',
        make('span', {className: 'grow'}),
        nameLink(e),
        j + 1 < E.length ? act('join', e.start, 'join with next', 'this submission and the next are one') : '');
      const sheets = make('div', {className: 'sheets'}, ...e.lay.map(([i, p], x) => {
        if (i === null) return gap(p, (e.lay.slice(x).find(([s]) => s !== null) || [])[0]);
        const n = e.pages.indexOf(i);
        const fig = thumb(i, n ? SMALL : MID, [...(n ? [act('cut', i, '✂', 'new submission here')] : []),
                                               act('drop', i, '✕', 'drop page')], p ? `p${p}` : 'extra');
        if (!n) fig.classList.add('first');
        return fig;
      }), ...nameCrops(e));
      const row = make('div', {className: 'srow' + (fine(j) ? '' : ' look')}, head, sheets);
      row.dataset.entry = j;
      return row;
    });
    $('sp-rows').replaceChildren(...rows);
    $('sp-loose').hidden = !plan.dropped.length;
    $('sp-loose-sheets').replaceChildren(...plan.dropped.map(i => thumb(i, SMALL, [act('restore', i, 'put back', 'put the page back')])));
  }
  // The row of the submission shown, marked.
  function here() {
    const j = done() || (peek !== null && owner(peek) < 0) ? -1 : k;
    $('sp-rows').querySelector('.srow.here')?.classList.remove('here');
    $('sp-rows').querySelector(`.srow[data-entry="${j}"]`)?.classList.add('here');
  }
  // A cursor move: the step drawn again and its row marked; nothing else changed.
  function moved() {
    renderStep();
    here();
    strip();
    Actions.enable($('sp-step'));
  }

  function render() {
    const E = plan.entries;
    // A page being looked at that has become a first page (or gone) is no longer just looked at.
    if (peek !== null) {
      const j = E.findIndex(e => e.start === peek);
      if (j >= 0) { k = j; peek = null; } else if (peek >= plan.n) peek = null;
      else if (owner(peek) >= 0) k = owner(peek);
    }
    // Never "done" while a first page is unverified (an undo or a join can shorten the list).
    if (peek === null && k >= E.length && E.some(open)) k = plan.cursor ?? nextOpen(E, -1, open);
    counts[plan.id] = plan.counts;
    const info = [`${plan.n} pages`, plural(E.length, 'submission')];
    if (plan.checked) info.push('pages read');
    $('sp-info').replaceChildren(info.join(' · '), ' ',
      plan.failed ? tag('bad', `${plural(plan.failed, 'page')} not read: model failed`) : '');
    if (document.activeElement !== $('sp-length')) $('sp-length').value = plan.length;
    const flagged = E.filter(e => e.doubts.length).length, unverified = E.filter(open).length;
    const towrite = counts[plan.id].write;
    $('sp-sum').replaceChildren(
      flagged ? tag('bad', `${flagged} flagged`) : '',
      unverified ? tag('unv', `${unverified} unverified`) : '',
      plan.removed.length ? tag('unv', `${plan.removed.length} to remove: ${plan.removed.join(', ')}`) : '',
      towrite ? tag('unv', `${towrite} to write`) : tag('', 'all written'));
    renderStep();
    renderRows();
    here();
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
  addEventListener('hashchange', () => {
    const id = (location.hash.match(/src=([\w-]+)/) || [])[1];
    if (ids.includes(id) && id !== plan?.id) { pick.value = id; load(id); }
  });
  scanCounts();
  tab(linked() || pref('scansTab', 'split'));
  load(pick.value);
})();
