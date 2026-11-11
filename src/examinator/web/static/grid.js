'use strict';
// The grid is rendered once; filtering, search and sorting happen here. The visible
// ids, in order, go to sessionStorage so the question page's next/previous follows them.
(() => {
  const body = document.querySelector('#grid tbody');
  const rows = [...body.rows];
  const f = {text: document.getElementById('f-text'), col: document.getElementById('f-col'),
             state: document.getElementById('f-state'), value: document.getElementById('f-value')};
  let sort = null;      // {index, dir}

  // Numeric values present in the data join the value filter.
  const numbers = new Set();
  for (const td of body.querySelectorAll('td.c')) {
    if (td.dataset.v && !'tf'.includes(td.dataset.v)) numbers.add(td.dataset.v);
  }
  for (const v of [...numbers].sort((a, b) => a - b)) f.value.append(make('option', {value: v, textContent: v}));

  const saved = session('grid', {});
  for (const k of Object.keys(f)) if (saved[k]) f[k].value = saved[k];
  sort = saved.sort || null;

  function cellMatches(td) {
    if (f.state.value && td.dataset.s !== f.state.value) return false;
    const v = f.value.value;
    if (!v) return true;
    if (v === 'blank') return !!td.dataset.b;
    if (v === 'none') return td.dataset.v === '';
    return td.dataset.v === v && !td.dataset.b;
  }

  function key(row, index, kind) {
    const td = row.cells[index];
    if (kind === 'num') return Number(td.dataset.n);
    if (kind === 'cell') {
      const v = td.dataset.v;
      return v === '' ? null : v === 't' ? 1 : v === 'f' ? 0 : Number(v);
    }
    return td.textContent.trim().toLowerCase();
  }

  function apply() {
    const text = f.text.value.trim().toLowerCase();
    const cellFilter = f.state.value || f.value.value;
    const visible = [];
    for (const row of rows) {
      let show = !text || row.cells[0].textContent.toLowerCase().includes(text) ||
                 row.cells[1].textContent.toLowerCase().includes(text);
      if (show && cellFilter) {
        show = [...row.querySelectorAll('td.c')].some(
          td => (!f.col.value || td.dataset.col === f.col.value) && cellMatches(td));
      }
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
    try {
      sessionStorage.setItem('order', JSON.stringify(visible.map(r => r.dataset.id)));
      sessionStorage.setItem('grid', JSON.stringify(
        {text: f.text.value, col: f.col.value, state: f.state.value, value: f.value.value, sort}));
    } catch (e) { /* private mode */ }
  }

  for (const el of Object.values(f)) el.addEventListener('input', apply);
  document.getElementById('f-clear').addEventListener('click', () => {
    for (const el of Object.values(f)) el.value = '';
    sort = null;
    body.append(...rows);
    apply();
  });
  document.querySelector('#grid thead').addEventListener('click', e => {
    const th = e.target.closest('th');
    if (!th) return;
    const index = th.cellIndex;
    sort = sort && sort.index === index ? (sort.dir === 'asc' ? {index, dir: 'desc'} : null) : {index, dir: 'asc'};
    if (!sort) body.append(...rows);
    apply();
  });
  document.addEventListener('keydown', e => {
    if (e.key === '/' && !e.target.matches('input, select, textarea')) { e.preventDefault(); f.text.focus(); }
    else if (e.key === 'Escape' && e.target === f.text) f.text.blur();
  });
  apply();
})();
