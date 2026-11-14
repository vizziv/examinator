'use strict';
// The Read page: what the options would read, shown live; one button reads it.
(() => {
  const form = $('rd-form'), line = Line('rd-msg');
  const start = $('rd-start'), stop = $('rd-stop');
  const order = session('order', null);
  let timer = null, planning = null, toRead = 0, running = false, batch = null;     // batch: the one shown, by id
  $('rd-grid-n').textContent = order ? order.length : 0;
  if (!order) form.querySelector('[name=scope][value=grid]').disabled = true;

  const checked = name => [...form.querySelectorAll(`[name=${name}]:checked`)].map(i => i.value);
  const flag = name => form.querySelector(`[name=${name}]`).checked;
  const selection = () => ({submissions: form.scope.value === 'grid' ? order : null, questions: checked('question'),
                            missing_only: flag('missing_only'), include_protected: flag('include_protected')});

  function row(table, cells, head) {
    const tr = table.insertRow();
    for (const c of cells) {
      const cell = make(head ? 'th' : 'td', {});
      cell.append(c);
      tr.append(cell);
    }
  }
  const ready = () => { start.disabled = running || !toRead; };

  // What the current options would read: asked again shortly after every change.
  const NAMES = {read: "will read", skip_protected: "skipped, protected", skip_present: "skipped, present", blocked: "blocked"};
  async function plan() {
    const mine = planning = {};
    const r = await Changes.query('/read/plan', selection());
    if (mine !== planning) return;          // a newer change asked again
    const counts = $('rd-counts'), table = $('rd-groups');
    counts.replaceChildren();
    table.replaceChildren();
    toRead = r.ok ? r.body.counts.read : 0;
    ready();
    if (!r.ok) { counts.textContent = r.body.error || "failed"; return; }
    for (const [action, n] of Object.entries(r.body.counts)) {
      counts.append(make('span', {}, make('b', {textContent: n}), ` ${NAMES[action]} `));
    }
    row(table, ['', 'question', 'n', 'why', 'submissions'], true);
    for (const g of r.body.groups) {
      const list = make('details', {}, make('summary', {textContent: g.submissions.slice(0, 3).join(', ') + (g.n > 3 ? ' …' : '')}),
                        g.submissions.join(', ') + (g.n > g.submissions.length ? ` … (${g.n})` : ''));
      row(table, [NAMES[g.action], g.question, String(g.n), g.reason, list]);
    }
  }
  function later() { clearTimeout(timer); timer = setTimeout(plan, 250); }
  form.addEventListener('input', later);
  Actions.register('toggle-questions', {run: () => {
    const boxes = [...form.querySelectorAll('[name=question]')], on = boxes.some(b => !b.checked);
    boxes.forEach(b => { b.checked = on; });
    later();
  }});

  // Start is a run, refused while a batch runs; Stop, a change to the batch shown (its id).
  Actions.register('read-start', {available: () => !start.disabled, run: () => {
    if (flag('include_protected') && !confirm("Read again, replacing values a person set or verified?")) return;
    return Changes.send('batch', '/read/start', {...selection(), parallel: Number(form.parallel.value)}, {line, done: poll});
  }});
  Actions.register('read-stop', {available: () => !stop.disabled, run: () => Changes.send('batch', '/read/stop', () => ({rev: batch}), {line, done: poll})});

  // The batch's progress, polled while it runs (one poll at a time).
  let next = 0;
  async function poll() {
    clearTimeout(next);
    const r = await Changes.get('/read/status');
    const s = r.body;
    if (!r.ok || s.state === 'idle') return;
    $('rd-progress').hidden = false;
    const bar = $('rd-bar');
    bar.max = Math.max(s.total, 1);
    bar.value = s.done;
    $('rd-prog').textContent =
      `${s.done} / ${s.total} · ${s.state}${s.current ? ' · ' + s.current : ''}${s.failed ? ` · ${s.failed} failed` : ''}` +
      `${s.asked ? ` · fields read ${s.got} of ${s.asked}` : ''}${s.error ? ' · ' + s.error : ''}`;
    const table = $('rd-failures');
    table.replaceChildren();
    for (const f of s.failures) {
      const link = make('a', {textContent: f.submission,
                              href: `${ROOT}/s/${encodeURIComponent(f.submission)}/${encodeURIComponent(f.question)}`});
      row(table, [link, f.question, f.step, f.status, f.detail]);
    }
    const was = running;
    batch = s.id;
    running = s.state === 'running';
    stop.disabled = !running;
    ready();
    clearTimeout(next);
    if (running) next = setTimeout(poll, 1000);
    else if (was) plan();                   // what is left to read now
  }
  plan();
  poll();
})();
