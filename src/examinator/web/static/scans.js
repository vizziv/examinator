'use strict';
// Scans: split stacks of scans into submissions. One boundary at a time: the guessed
// first page of the next exam, large, with its neighbours; confirm it (V) or move it
// a page earlier (A) or later (D). The overview below shows every submission, doubtful
// ones first, and is where pages are dropped or a submission is started by hand.
(() => {
  const $ = id => document.getElementById(id);
  const main = $('scans'), msg = $('msg');
  let plan = null, k = 0;

  const say = text => { msg.textContent = text || ''; };
  const sheet = (i, w) => `${ROOT}/sheet/${encodeURIComponent(plan.id)}/${i}.jpg?o=${plan.sheet_o[i]}&w=${w}`;
  const reading = i => plan.readings[i] || '';
  const done = () => k >= plan.entries.length;
  const firstOf = e => (e.pages.length ? e.pages[0] : e.start);

  // --- adding scans and the roster ---
  $('up').addEventListener('change', async e => {
    for (const file of e.target.files) {
      say(`uploading ${file.name}…`);
      let res = null, body = {};
      try {
        res = await fetch(`${ROOT}/scans/upload?name=${encodeURIComponent(file.name)}`, {
          method: 'POST', body: file, headers: {'Content-Type': 'application/pdf', 'X-Token': meta('token')}});
        body = await res.json();
      } catch (err) { /* reported below */ }
      if (!res || !res.ok) { say(`${file.name}: ${body.error || 'failed'}`); return; }
      if (!body.new) flash(`${file.name}: already added`);
      location.hash = 'src=' + body.id;
    }
    location.reload();
  });
  $('roster').addEventListener('change', async e => {
    const file = e.target.files[0];
    if (!file) return;
    const r = await request('/scans/roster', {csv: await file.text()});
    if (!r.ok) { say(r.body.error || 'failed'); return; }
    location.reload();
  });
  const pick = $('src');
  if (!pick) return;

  // --- loading and editing the plan ---
  async function load(id, keep) {
    const r = await request(`/scans/${encodeURIComponent(id)}/plan`);
    if (!r.ok) { say('could not load'); return; }
    plan = r.body;
    if (!keep) k = plan.cursor ?? plan.entries.length;
    history.replaceState(null, '', '#src=' + id);
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
  // Keys come faster than replies: steps run one after another, each on the state the last left.
  let chain = Promise.resolve();
  const queued = fn => ctx => { chain = chain.then(() => fn(ctx)).catch(() => {}); return chain; };
  const register = (name, spec) => Actions.register(name, {...spec, run: queued(spec.run)});
  const edit = (op, data) => send('edit', {op, ...data});
  function nextOpen(from) {
    const E = plan.entries;
    for (let j = from + 1; j < E.length; j++) if (!E[j].confirmed) return j;
    for (let j = 0; j <= from && j < E.length; j++) if (!E[j].confirmed) return j;
    return E.length;
  }

  // --- the step-through ---
  const live = () => !!plan && !$('step').hidden;
  const at = () => live() && !done();
  register('split-confirm', {available: at, run: async () => {
    const r = await edit('confirm', {k});
    if (r && r.ok) { k = nextOpen(k); render(); }
  }});
  register('split-earlier', {available: at, run: () => edit('move', {k, delta: -1})});
  register('split-later', {available: at, run: () => edit('move', {k, delta: 1})});
  register('split-back', {available: () => live() && k > 0, run: () => { k = Math.min(k, plan.entries.length) - 1; render(); }});
  register('split-next', {available: at, run: () => { k += 1; render(); }});
  register('split-drop', {available: at, run: () => edit('drop', {sheet: firstOf(plan.entries[k])})});
  register('split-turn', {available: at, run: () => edit('turn', {k})});
  register('split-turn-source', {available: () => !!plan, run: () => edit('turn_source')});
  register('split-flip-source', {available: () => !!plan, run: () => edit('flip_source')});
  register('split-undo', {available: () => !!plan && plan.undo, run: () => send('undo', {})});
  register('split-guess', {available: () => !!plan, run: async () => {
    const r = await send('propose', {length: $('s-length').value});
    if (r && r.ok) { k = plan.cursor ?? plan.entries.length; render(); }
  }});
  register('split-check', {available: () => !!plan, run: async () => {
    say('reading landmarks…');
    const r = await send('propose', {check: true, length: $('s-length').value});
    if (!r || !r.ok) { say(''); return; }
    const job = await waitJob(r.body.job);
    say(job.state === 'done' ? '' : `check ${job.state}${job.error ? ': ' + job.error : ''}`);
    await load(plan.id);
  }});
  register('split-accept', {available: () => !!plan, run: async () => {
    const flagged = plan.entries.filter(e => e.doubts.length).length;
    const open = plan.entries.filter(e => !e.confirmed).length;
    if ((flagged || open) && !confirm(`${flagged} flagged, ${open} not confirmed. Write the submissions anyway?`)) return;
    let r = await send('accept', {});
    if (r && r.status === 409 && r.body.current && r.body.current.busy) {
      const ids = r.body.current.busy.join(', ');
      if (!confirm(`${ids} already ${r.body.current.busy.length > 1 ? 'have' : 'has'} grading work, and the split changes or removes ${r.body.current.busy.length > 1 ? 'them' : 'it'}. Rewrite anyway? The work stays but may no longer match the pages.`)) return;
      r = await send('accept', {force: true});
    }
    if (r && r.ok) {
      const x = r.body.report;
      say(`written: ${x.new.length} new, ${x.rewritten.length} rewritten, ${x.removed.length} removed, ${x.unchanged} unchanged`);
    }
  }});

  function figure(el, i, w, what) {
    const img = el.querySelector('img'), cap = el.querySelector('figcaption');
    el.hidden = i < 0 || i >= plan.n;
    if (el.hidden) return;
    img.src = sheet(i, w);
    const gone = plan.loose.includes(i);
    cap.replaceChildren(make('span', {textContent: `${what} · page ${i + 1}`}),
      reading(i) ? make('span', {className: 'tag accent', textContent: reading(i)}) : '',
      gone ? make('span', {className: 'tag', textContent: plan.dropped.includes(i) ? 'dropped' : 'in no exam'}) : '');
  }
  function preload(j) {
    const e = plan.entries[j];
    if (!e) return;
    const s = firstOf(e);
    for (const [i, w] of [[s, 1000], [s - 1, 360], [s + 1, 360]]) {
      if (i >= 0 && i < plan.n) { const im = new Image(); im.src = sheet(i, w); }
    }
  }
  function renderStep() {
    const E = plan.entries, finished = done();
    $('st-done').hidden = !finished;
    $('st-pages').hidden = finished;
    $('st-k').textContent = finished ? 'done' : `exam ${k + 1} of ${E.length}`;
    $('st-done-sum').textContent = finished ? `${E.length} exams` : '';
    const tags = [];
    let before = '';
    if (!finished) {
      const e = E[k], s = firstOf(e);
      tags.push(make('span', {className: e.confirmed ? 'tag good' : 'tag ask', textContent: e.confirmed ? 'confirmed' : 'guess'}));
      if (e.own) tags.push(make('span', {className: 'tag accent2', textContent: 'turned'}));
      figure($('st-a'), s - 1, 360, 'before');
      figure($('st-b'), s, 1000, 'first page?');
      figure($('st-c'), s + 1, 360, 'after');
      preload(k + 1);
    }
    const prev = finished ? E[E.length - 1] : E[k - 1];
    if (prev) before = `exam ${finished ? E.length : k}: ${prev.pages.length} of ${plan.length} pages${prev.doubts.length ? ' · ' + prev.doubts.join(', ') : ''}`;
    $('st-tags').replaceChildren(...tags);
    $('st-prev').textContent = before;
    $('st-prev').classList.toggle('warn', !!prev && prev.doubts.length > 0);
  }

  // --- the overview ---
  function thumb(i, w, buttons) {
    const fig = make('figure', {className: 'sh'},
      make('a', {href: sheet(i, 0), target: '_blank', rel: 'noopener'}, make('img', {src: sheet(i, w), alt: `page ${i + 1}`, loading: 'lazy'})),
      make('figcaption', {}, String(i + 1), reading(i) ? make('span', {className: 'dim', textContent: ' ' + reading(i)}) : ''));
    if (buttons.length) fig.append(make('span', {className: 'acts'}, ...buttons));
    return fig;
  }
  const button = (text, title, fn) => {
    const b = make('button', {type: 'button', className: 'quiet', textContent: text, title});
    b.addEventListener('click', fn);
    return b;
  };
  function nameCrop(e) {
    if (main.dataset.nameOffset === undefined) return '';
    const off = Number(main.dataset.nameOffset) + (view.cover || 0);      // cover sheets come first
    if (e.pages.length <= off) return '';
    const c = make('span', {className: 'crop'});
    c.dataset.src = sheet(e.pages[off], 1000);
    c.dataset.box = main.dataset.nameBox;
    drawCrop(c);
    return c;
  }
  function whoInput(e) {
    if (!e.id || e.state === 'new') return '';
    const input = make('input', {type: 'text', value: e.name || '', placeholder: 'who?', className: 'who'});
    input.setAttribute('list', 'people');
    input.addEventListener('change', async () => {
      const r = await request('/scans/who', {sub: e.id, text: input.value});
      if (!r.ok) flash(r.body.error || 'failed');
      else { input.value = r.body.who.name; input.classList.add('saved'); }
    });
    return input;
  }
  function renderRows() {
    const E = plan.entries;
    const order = E.map((e, j) => j);
    const look = j => (E[j].doubts.length || E[j].state === 'changed' ? 0 : 1);
    order.sort((a, b) => look(a) - look(b) || a - b);
    const names = {};
    for (const e of E) if (e.name) names[e.name] = (names[e.name] || 0) + 1;
    const rows = order.map(j => {
      const e = E[j];
      const head = make('div', {className: 'rhead'},
        button(`#${j + 1}`, 'step through from here', () => { k = j; render(); $('step').scrollIntoView({block: 'nearest'}); }),
        e.id ? make('code', {textContent: e.id}) : '',
        make('span', {className: 'tag ' + {made: 'good', changed: 'warn', new: ''}[e.state], textContent: {made: 'written', changed: 'changed', new: 'new'}[e.state]}),
        e.confirmed ? '' : make('span', {className: 'tag ask', textContent: 'guess'}),
        ...e.doubts.map(d => make('span', {className: 'tag bad', textContent: d})),
        e.own ? make('span', {className: 'tag accent2', textContent: 'turned'}) : '',
        e.name && names[e.name] > 1 ? make('span', {className: 'tag warn', textContent: 'name twice'}) : '',
        make('span', {className: 'grow'}),
        whoInput(e),
        j + 1 < E.length ? button('join next', 'this exam and the next are one', () => edit('join', {k: j})) : '');
      const sheets = make('div', {className: 'sheets'}, ...e.pages.map((i, n) => {
        const fig = thumb(i, n ? 140 : 360, [
          ...(n ? [button('✂', 'an exam starts here', () => edit('cut', {sheet: i}))] : []),
          button('✕', 'drop this page', () => edit('drop', {sheet: i}))]);
        if (!n) fig.classList.add('first');
        return fig;
      }), nameCrop(e));
      const row = make('div', {className: 'srow' + (look(j) ? '' : ' look')}, head, sheets);
      return row;
    });
    $('rows').replaceChildren(...rows);
    $('loose').hidden = !plan.loose.length;
    $('loose-sheets').replaceChildren(...plan.loose.map(i => thumb(i, 140, [
      plan.dropped.includes(i) ? button('restore', 'put the page back', () => edit('restore', {sheet: i}))
        : button('✂', 'an exam starts here', () => edit('cut', {sheet: i}))])));
  }

  function render() {
    for (const id of ['srcbar', 'step', 'over']) $(id).hidden = false;
    $('s-name').textContent = plan.name;
    $('s-info').textContent = `${plan.n} pages · ${plan.entries.length} exams${plan.checked ? ' · checked' : ''}`;
    $('s-orient').textContent = plan.orient;
    if (document.activeElement !== $('s-length')) $('s-length').value = plan.length;
    const E = plan.entries;
    const flagged = E.filter(e => e.doubts.length).length, open = E.filter(e => !e.confirmed).length;
    const towrite = E.filter(e => e.state !== 'made').length + plan.removed.length;
    $('s-sum').replaceChildren(
      flagged ? make('span', {className: 'tag bad', textContent: `${flagged} flagged`}) : '',
      open ? make('span', {className: 'tag ask', textContent: `${open} to confirm`}) : '',
      plan.removed.length ? make('span', {className: 'tag warn', textContent: `${plan.removed.length} to remove: ${plan.removed.join(', ')}`}) : '',
      make('span', {className: towrite ? 'tag' : 'tag good', textContent: towrite ? `${towrite} to write` : 'all written'}));
    renderStep();
    renderRows();
    Actions.hints();
  }

  pick.addEventListener('change', () => load(pick.value));
  const want = (location.hash.match(/src=([\w-]+)/) || [])[1];
  if (want && [...pick.options].some(o => o.value === want)) pick.value = want;
  load(pick.value);
})();
