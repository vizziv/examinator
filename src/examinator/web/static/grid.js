'use strict';
// The grid is rendered once; the filter (focus.js, set on the filter page), search and
// sorting happen here. The visible ids, in order, go to sessionStorage so the question
// page's next/previous and the Read page follow them.
(() => {
  const body = document.querySelector('#grid tbody');
  const rows = [...body.rows];
  const text = document.getElementById('f-text');
  const sum = $('filter-sum'), clearFilter = document.querySelector('[data-action=clear-filter]');
  const saved = session('grid', {});
  let sort = saved.sort || null;      // {index, dir}
  let sel = {}, data = null;
  text.value = saved.text || '';

  // A column's sort key: a cell's or the total's `data-sort` (a number; '' none), else its text.
  function key(row, index, kind) {
    const td = row.cells[index];
    if (kind === 'text') return td.textContent.trim().toLowerCase();
    return td.dataset.sort === '' ? null : Number(td.dataset.sort);
  }

  function summary() {
    const n = Focus.size(sel);
    clearFilter.hidden = !n;
    sum.classList.toggle('on', !!n);
    if (!n) { sum.textContent = 'filter'; sum.title = 'the filter: open the filter page'; return; }
    const labels = {};
    for (const q of (data ? data.questions : [])) for (const f of q.facets) labels[f.id] = `${q.id} ${f.label}`;
    const todo = new Set(toggles.filter(isOn).map(b => b.dataset.facet));
    const parts = Object.entries(sel).map(([fid, want]) =>
      `${labels[fid] || fid}: ${todo.has(fid) ? 'to do' : Array.isArray(want) ? want.join(' | ') : `“${want}”`}`);
    sum.textContent = `filter (${n}): ${parts.join(' · ')}`;
    sum.title = parts.join('\n');
  }

  // The header lines up with the centred table; id and name stick on sideways scrolling.
  function measure() {
    const wrap = document.getElementById('grid-wrap'), first = document.querySelector('#grid thead th');
    document.body.style.setProperty('--page-max', wrap.offsetWidth + 'px');
    if (first) document.getElementById('grid').style.setProperty('--name-left', first.offsetWidth + 'px');
  }

  // A column's "to do" toggle is the filter on its state facet: exactly the states that do
  // not count yet. It shows on whenever the filter says that, however it was set.
  const toggles = [...document.querySelectorAll('#grid thead button.todo')];
  const todoOf = b => b.dataset.todo.split(' ');
  function isOn(b) {
    const want = sel[b.dataset.facet], todo = todoOf(b);
    return Array.isArray(want) && want.length === todo.length && todo.every(v => want.includes(v));
  }
  function marks() {
    for (const b of toggles) {
      const on = isOn(b);
      b.classList.toggle('on', on);
      b.setAttribute('aria-pressed', on);
    }
  }

  function apply() {
    const q = text.value.trim().toLowerCase();
    const inFilter = data && Focus.size(sel) ? new Set(Focus.focused(data.values, sel, data.subs)) : null;
    const visible = [];
    for (const row of rows) {
      const show = (!inFilter || inFilter.has(row.dataset.id)) &&
        (!q || row.cells[0].textContent.toLowerCase().includes(q) || row.cells[1].textContent.toLowerCase().includes(q) ||
         (row.cells[1].dataset.student || '').toLowerCase().includes(q));
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
    summary();
    marks();
    measure();
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
  Actions.register('clear-filter', {available: () => Focus.size(sel) > 0, run: () => { sel = {}; Focus.save(sel); apply(); }});
  Actions.register('search', {run: () => { text.focus(); text.select(); }});
  document.querySelector('#grid thead').addEventListener('click', e => {
    const b = e.target.closest('button.todo');
    if (b) {
      if (isOn(b)) delete sel[b.dataset.facet]; else sel[b.dataset.facet] = todoOf(b);
      Focus.save(sel);
      load().then(apply);
      return;
    }
    const th = e.target.closest('th');
    if (!th) return;
    const index = th.cellIndex;
    sort = sort && sort.index === index ? (sort.dir === 'asc' ? {index, dir: 'desc'} : null) : {index, dir: 'asc'};
    if (!sort) body.append(...rows);
    apply();
  });
  // Follow the filter set elsewhere (another tab, or the filter page and back).
  async function load() {
    if (Focus.size(sel) && !data) {
      // Until the values arrive, the server's rendering (if a link carried the filter) stands.
      const r = await Changes.get('/focus/values');
      if (r.ok) data = r.body; else flash('filter: ' + (r.body.error || 'failed to load'));
    }
  }
  async function refresh() {
    sel = Focus.load();
    await load();
    apply();
  }
  // --- a keyboard cursor: the arrows or W A S D move it, Enter or V opens the cell; coming
  // back (Q on the question page) it starts on the cell you came from ---
  let cur = null;
  const shown = () => [...body.rows].filter(r => !r.hidden);
  const cellsOf = row => [...row.querySelectorAll('td.cell')];
  function place(td) {
    cur?.classList.remove('cur');
    cur = td || null;
    ahead([cur?.querySelector('a')?.href], '#grid td.c a');       // the cell Enter opens, and one hovered
    if (!cur) return;
    cur.classList.add('cur');
    cur.scrollIntoView({block: 'nearest', inline: 'nearest'});
  }
  function move(dr, dc) {
    const list = shown();
    if (!cur || cur.closest('tr').hidden) return place(cellsOf(list[0])[0]);
    const row = cur.closest('tr'), c = cellsOf(row).indexOf(cur);
    const next = cellsOf(list[Math.max(0, Math.min(list.length - 1, list.indexOf(row) + dr))]);
    place(next[Math.max(0, Math.min(next.length - 1, c + dc))]);
  }
  const step = (dr, dc) => ({available: () => shown().some(r => cellsOf(r).length), run: () => move(dr, dc), repeat: true});
  Actions.register('cell-left', step(0, -1));
  Actions.register('cell-right', step(0, 1));
  Actions.register('cell-up', step(-1, 0));
  Actions.register('cell-down', step(1, 0));
  Actions.register('cell-open', {available: () => !!cur && !cur.closest('tr').hidden, run: () => cur.querySelector('a').click()});
  const came = session('gridAt', null);
  const cameRow = came && [...body.rows].find(r => r.dataset.id === came.sub);
  place(cameRow && [...cameRow.querySelectorAll('td.c')].find(td => td.dataset.q === came.q));

  // The legend closes on a click elsewhere.
  const legend = document.querySelector('details.legend');
  document.addEventListener('click', e => { if (legend?.open && !legend.contains(e.target)) legend.open = false; });
  addEventListener('storage', e => { if (e.key === 'focus') refresh(); });
  addEventListener('pageshow', e => { if (e.persisted) refresh(); });
  // The table's width changes after the first measurement too (late layout, zoom, a search).
  if (window.ResizeObserver) {
    const watch = new ResizeObserver(measure);
    watch.observe(document.getElementById('grid-wrap'));
    const first = document.querySelector('#grid thead th');
    if (first) watch.observe(first);
  }
  measure();
  refresh().then(() => { if (location.search) history.replaceState(null, '', location.pathname); });
})();
