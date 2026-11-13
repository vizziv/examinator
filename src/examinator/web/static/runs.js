'use strict';
// The Read page: what the options would read, shown live; one button reads it.
(() => {
  const form = document.getElementById('sel'), msg = document.getElementById('msg');
  const start = document.getElementById('start'), stop = document.getElementById('stop');
  const order = session('order', null);
  let timer = null, planning = null, toRead = 0, running = false;
  document.getElementById('grid-n').textContent = order ? order.length : 0;
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
  const NAMES = {run: "will read", skip_protected: "skipped, protected", skip_present: "skipped, present", blocked: "blocked"};
  async function plan() {
    const mine = planning = {};
    const r = await request('/runs/plan', selection());
    if (mine !== planning) return;          // a newer change asked again
    const counts = document.getElementById('counts'), table = document.getElementById('groups');
    counts.replaceChildren();
    table.replaceChildren();
    toRead = r.ok ? r.body.counts.run : 0;
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

  Actions.register('run-start', {available: () => !start.disabled, run: async () => {
    msg.textContent = '';
    if (flag('include_protected') && !confirm("Read again, replacing values a person set or verified?")) return;
    const r = await request('/runs/start', {...selection(), parallel: Number(form.parallel.value)});
    if (!r.ok) { msg.textContent = r.body.error || "failed"; return; }
    poll();
  }});
  Actions.register('run-stop', {available: () => !stop.disabled, run: () => request('/runs/stop', {})});

  async function poll() {
    const r = await request('/runs/status');
    const s = r.body;
    if (!r.ok || s.state === 'idle') return;
    document.getElementById('progress').hidden = false;
    const bar = document.getElementById('bar');
    bar.max = Math.max(s.total, 1);
    bar.value = s.done;
    document.getElementById('prog').textContent =
      `${s.done} / ${s.total} · ${s.state}${s.current ? ' · ' + s.current : ''}${s.failed ? ` · ${s.failed} not done` : ''}${s.error ? ' · ' + s.error : ''}`;
    const table = document.getElementById('failures');
    table.replaceChildren();
    for (const f of s.failures) {
      const link = make('a', {textContent: f.submission,
                              href: `${ROOT}/s/${encodeURIComponent(f.submission)}/${encodeURIComponent(f.question)}`});
      row(table, [link, f.question, f.step, f.status, f.detail]);
    }
    const was = running;
    running = s.state === 'running';
    stop.disabled = !running;
    ready();
    if (running) setTimeout(poll, 1000);
    else if (was) plan();                   // what is left to read now
  }
  plan();
  poll();
})();
