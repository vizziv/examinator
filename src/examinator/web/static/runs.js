'use strict';
(() => {
  const form = document.getElementById('sel'), msg = document.getElementById('msg');
  const start = document.getElementById('start'), stop = document.getElementById('stop');
  const order = session('order', null);
  let token = null, timer = null;
  document.getElementById('grid-n').textContent = order ? order.length : 0;
  if (!order) form.querySelector('[name=scope][value=grid]').disabled = true;

  const checked = name => [...form.querySelectorAll(`[name=${name}]:checked`)].map(i => i.value);
  const flag = name => form.querySelector(`[name=${name}]`).checked;

  function row(table, cells, head) {
    const tr = table.insertRow();
    for (const c of cells) {
      const cell = make(head ? 'th' : 'td', {});
      cell.append(c);
      tr.append(cell);
    }
  }

  function stale() { token = null; start.disabled = true; }
  form.addEventListener('input', stale);
  document.getElementById('q-toggle').addEventListener('click', () => {
    const boxes = [...form.querySelectorAll('[name=question]')], on = boxes.some(b => !b.checked);
    boxes.forEach(b => { b.checked = on; });
    stale();
  });

  document.getElementById('preview').addEventListener('click', async () => {
    msg.textContent = '';
    const r = await request('/runs/preview', {
      submissions: form.scope.value === 'grid' ? order : null,
      questions: checked('question'), stages: checked('stage'),
      missing_only: flag('missing_only'), include_protected: flag('include_protected')});
    if (!r.ok) { msg.textContent = r.body.error || "failed"; return; }
    token = r.body.plan;
    const names = {run: "will run", skip_protected: "skipped, protected", skip_present: "skipped, present", blocked: "blocked"};
    const counts = document.getElementById('counts');
    counts.replaceChildren();
    for (const [action, n] of Object.entries(r.body.counts)) {
      counts.append(make('span', {}, make('b', {textContent: n}), ' ' + names[action]));
    }
    const table = document.getElementById('groups');
    table.replaceChildren();
    row(table, ['', 'stage', 'question', 'n', 'why', 'submissions'], true);
    for (const g of r.body.groups) {
      const list = make('details', {}, make('summary', {textContent: g.submissions.slice(0, 3).join(', ') + (g.n > 3 ? ' …' : '')}),
                        g.submissions.join(', ') + (g.n > g.submissions.length ? ` … (${g.n})` : ''));
      row(table, [names[g.action], g.stage, g.question, String(g.n), g.reason, list]);
    }
    document.getElementById('plan').hidden = false;
    start.disabled = r.body.counts.run === 0;
  });

  start.addEventListener('click', async () => {
    if (!token) return;
    const r = await request('/runs/start', {plan: token, parallel: Number(form.parallel.value), reasoning: flag('reasoning')});
    if (!r.ok) { msg.textContent = r.body.error || "failed"; stale(); return; }
    stale();
    poll();
  });
  stop.addEventListener('click', () => request('/runs/stop', {}));

  async function poll() {
    clearTimeout(timer);
    const r = await request('/runs/status');
    const s = r.body;
    if (!r.ok || s.state === 'idle') return;
    document.getElementById('progress').hidden = false;
    const bar = document.getElementById('bar');
    bar.max = Math.max(s.total, 1);
    bar.value = s.done;
    const state = s.state === 'stale' ? "refused: the preview was out of date" : s.state;
    document.getElementById('prog').textContent =
      `${s.done} / ${s.total} · ${state}${s.current ? ' · ' + s.current : ''}${s.failed ? ` · ${s.failed} not done` : ''}${s.error ? ' · ' + s.error : ''}`;
    const table = document.getElementById('failures');
    table.replaceChildren();
    for (const f of s.failures) {
      const link = make('a', {textContent: f.submission,
                              href: `${ROOT}/s/${encodeURIComponent(f.submission)}/${encodeURIComponent(f.question)}`});
      row(table, [link, f.question, f.stage, f.status, f.detail]);
    }
    const running = s.state === 'running';
    stop.disabled = !running;
    if (running) timer = setTimeout(poll, 1000);
  }
  poll();
})();
