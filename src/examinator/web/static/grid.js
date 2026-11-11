'use strict';
// The grid is rendered once; the focus (focus.js, set on the filter page), search and
// sorting happen here. The visible ids, in order, go to sessionStorage so the question
// page's next/previous and the Runs page follow them.
(() => {
  const body = document.querySelector('#grid tbody');
  const rows = [...body.rows];
  const text = document.getElementById('f-text');
  const sum = document.getElementById('focus-sum'), clearFocus = document.querySelector('[data-action=clear-focus]');
  const saved = session('grid', {});
  let sort = saved.sort || null;      // {index, dir}
  let sel = {}, data = null;
  text.value = saved.text || '';

  function key(row, index, kind) {
    const td = row.cells[index];
    if (kind === 'num') return Number(td.dataset.n);
    if (kind === 'cell') {
      const v = td.dataset.v;
      return v === '' ? null : v === 't' ? 1 : v === 'f' ? 0 : Number(v);
    }
    return td.textContent.trim().toLowerCase();
  }

  function summary() {
    const n = Focus.size(sel);
    clearFocus.hidden = !n;
    sum.classList.toggle('on', !!n);
    if (!n) { sum.textContent = 'filter'; sum.title = 'the focus: open the filter page'; return; }
    const labels = {};
    for (const q of (data ? data.questions : [])) for (const f of q.facets) labels[f.id] = `${q.id} ${f.label}`;
    const parts = Object.entries(sel).map(([fid, want]) =>
      `${labels[fid] || fid}: ${Array.isArray(want) ? want.join(' | ') : `“${want}”`}`);
    sum.textContent = `focus (${n}): ${parts.join(' · ')}`;
    sum.title = parts.join('\n');
  }

  function apply() {
    const q = text.value.trim().toLowerCase();
    const inFocus = data && Focus.size(sel) ? new Set(Focus.focused(data.values, sel, data.subs)) : null;
    const visible = [];
    for (const row of rows) {
      const show = (!inFocus || inFocus.has(row.dataset.id)) &&
        (!q || row.cells[0].textContent.toLowerCase().includes(q) || row.cells[1].textContent.toLowerCase().includes(q));
      row.hidden = !show;
      if (show) visible.push(row);
    }
    const heads = document.querySelectorAll('#grid thead th');
    heads.forEach(th => th.removeAttribute('data-dir'));
    if (sort && heads[sort.index]) {
      const th = heads[sort.index], kind = th.dataset.sort;
      th.dataset.dir = sort.dir;
      const sign = sort.dir === 'asc' ? 1 : -1;
      visible.sort((a, b) => {
        const x = key(a, sort.index, kind), y = key(b, sort.index, kind);
        if (x === null || y === null) return (x === null) - (y === null);     // no value: last
        return (x < y ? -1 : x > y ? 1 : 0) * sign;
      });
      body.append(...visible);
    }
    document.getElementById('shown').textContent = `${visible.length} / ${rows.length}`;
    for (const cell of document.querySelectorAll('#grid tfoot td.tally')) {
      const n = visible.filter(r => r.querySelector(`td.c[data-col="${CSS.escape(cell.dataset.col)}"][data-c]`)).length;
      cell.textContent = n;
    }
    summary();
    try {
      sessionStorage.setItem('order', JSON.stringify(visible.map(r => r.dataset.id)));
      sessionStorage.setItem('grid', JSON.stringify({text: text.value, sort}));
    } catch (e) { /* private mode */ }
  }

  text.addEventListener('input', apply);
  Actions.register('clear-search', {run: () => {
    text.value = '';
    sort = null;
    body.append(...rows);
    apply();
  }});
  Actions.register('clear-focus', {available: () => Focus.size(sel) > 0, run: () => { sel = {}; Focus.save(sel); apply(); }});
  Actions.register('search', {run: () => { text.focus(); text.select(); }});
  document.querySelector('#grid thead').addEventListener('click', e => {
    const th = e.target.closest('th');
    if (!th) return;
    const index = th.cellIndex;
    sort = sort && sort.index === index ? (sort.dir === 'asc' ? {index, dir: 'desc'} : null) : {index, dir: 'asc'};
    if (!sort) body.append(...rows);
    apply();
  });
  // Follow the focus set elsewhere (another tab, or the filter page and back).
  async function refresh() {
    sel = Focus.load();
    if (Focus.size(sel) && !data) {
      // Until the values arrive, the server's rendering (if a link carried the focus) stands.
      const r = await request('/focus/values');
      if (r.ok) data = r.body; else flash('focus: ' + (r.body.error || 'failed to load'));
    }
    apply();
  }
  // The legend closes on a click elsewhere.
  const legend = document.querySelector('details.legend');
  document.addEventListener('click', e => { if (legend?.open && !legend.contains(e.target)) legend.open = false; });
  addEventListener('storage', e => { if (e.key === 'focus') refresh(); });
  addEventListener('pageshow', e => { if (e.persisted) refresh(); });
  refresh().then(() => { if (location.search) history.replaceState(null, '', location.pathname); });
})();
