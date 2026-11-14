'use strict';
// The filter page: one card per question, its facets as checkbox lists with live
// counts (choice) or a search box with matching snippets (text). The selection is
// the browser's focus (focus.js); the grid and next/previous follow it.
(() => {
  const host = document.getElementById('fx-questions');
  const countLink = document.getElementById('fx-count'), msg = document.getElementById('fx-msg');
  const LONG = 12, SNIPPETS = 60;
  let sel = Focus.load(), data = null;
  const lists = [];      // {fid, items: [{value, input, count, label}]}
  const texts = [];      // {fid, qid, input, out}
  const cards = [];      // {details, fids, summary, title}

  const numeric = (a, b) => a.localeCompare(b, undefined, {numeric: true});
  function sorted(values, order) {
    const rank = v => { const i = order.indexOf(v); return i < 0 ? order.length : i; };
    return values.sort((a, b) => rank(a) - rank(b) || numeric(a, b));
  }

  function changed() {
    Focus.save(sel);
    history.replaceState(null, '', location.pathname + Focus.query(sel));
    update();
  }

  function choiceFacet(f) {
    const col = data.values[f.id];
    const seen = new Set((Array.isArray(sel[f.id]) ? sel[f.id] : []));
    for (const vals of Object.values(col)) for (const v of vals) seen.add(v);
    for (const v of f.order) seen.add(v);       // a facet with known values (state, grade) lists them all
    const box = make('div', {className: 'facet'}, make('b', {textContent: f.label}));
    if (!seen.size) { box.append(make('span', {className: 'dim', textContent: ' —'})); return box; }
    const list = make('div', {className: 'fx-list'});
    const entry = {fid: f.id, items: []};
    for (const value of sorted([...seen], f.order)) {
      const input = make('input', {type: 'checkbox', checked: seen.has(value) && (sel[f.id] || []).includes(value)});
      input.addEventListener('change', () => {
        const now = new Set(Array.isArray(sel[f.id]) ? sel[f.id] : []);
        if (input.checked) now.add(value); else now.delete(value);
        if (now.size) sel[f.id] = [...now]; else delete sel[f.id];
        changed();
      });
      const count = make('span', {className: 'dim n'});
      const label = make('label', {title: value}, input, make('code', {textContent: f.shown[value] ?? value}), count);
      label.dataset.value = (f.shown[value] ?? value).toLowerCase();
      list.append(label);
      entry.items.push({value, input, count, label});
    }
    if (entry.items.length > LONG) {
      const find = make('input', {type: 'search', placeholder: 'find', size: 10});
      find.setAttribute('aria-label', `${f.label}: find`);
      find.addEventListener('input', () => {
        const q = find.value.trim().toLowerCase();
        for (const it of entry.items) it.label.hidden = !!q && !it.label.dataset.value.includes(q);
      });
      box.append(' ', find);
    }
    lists.push(entry);
    box.append(list);
    return box;
  }

  function textFacet(f, qid) {
    const input = make('input', {type: 'search', placeholder: 'search', value: typeof sel[f.id] === 'string' ? sel[f.id] : ''});
    input.setAttribute('aria-label', `${f.label}: search`);
    const out = make('div', {className: 'fx-snips'});
    input.addEventListener('input', () => {
      if (input.value.trim()) sel[f.id] = input.value; else delete sel[f.id];
      changed();
    });
    texts.push({fid: f.id, qid, input, out});
    return make('div', {className: 'facet wide'}, make('b', {textContent: f.label}), ' ', input, out);
  }

  function snippets(t, subs) {
    const q = t.input.value.trim().toLowerCase();
    t.out.replaceChildren();
    if (!q) return;
    const col = data.values[t.fid];
    let n = 0, more = 0;
    for (const s of subs) {
      if (!Focus.matches(data.values, sel, s, t.fid)) continue;
      for (const v of col[s] || []) {
        const [head, ...context] = v.split('\n');
        if (!head.toLowerCase().includes(q)) continue;
        if (n++ >= SNIPPETS) { more++; continue; }
        const a = make('a', {href: `${ROOT}/s/${encodeURIComponent(s)}/${encodeURIComponent(t.qid)}`, textContent: s});
        t.out.append(make('div', {className: 'snip'}, a,
          make('pre', {textContent: context.length ? context.join('\n') : head})));
      }
    }
    if (more) t.out.append(make('div', {className: 'dim', textContent: `+${more} more`}));
    if (!n) t.out.append(make('div', {className: 'dim', textContent: 'no match'}));
  }

  function build() {
    for (const q of data.questions) {
      const details = make('details', {className: 'fx-q'});
      const title = make('span', {textContent: q.title && q.title !== q.id ? `${q.id} · ${q.title}` : q.id});
      const summary = make('span', {className: 'tag'});
      details.append(make('summary', {}, title, ' ', summary));
      const groups = new Map();
      for (const f of q.facets) {
        if (!groups.has(f.group)) {
          const g = make('div', {className: 'fx-group'});
          if (f.group) g.append(make('div', {className: 'dim gname', textContent: f.group}));
          groups.set(f.group, g);
          details.append(g);
        }
        groups.get(f.group).append(f.kind === 'text' ? textFacet(f, q.id) : choiceFacet(f));
      }
      host.append(details);
      cards.push({details, summary, fids: q.facets.map(f => f.id)});
    }
  }

  function update() {
    const all = data.subs, table = data.values;
    const shown = Focus.focused(table, sel, all);
    const act = Focus.active(table, sel);
    countLink.textContent = `${shown.length} / ${all.length} submissions`;
    countLink.href = `${ROOT}/${Focus.query(sel)}`;
    document.getElementById('fx-link').href = location.pathname + Focus.query(sel);
    for (const e of lists) {
      const c = Focus.counts(table, sel, e.fid, all);
      for (const it of e.items) {
        it.input.checked = Array.isArray(sel[e.fid]) && sel[e.fid].includes(it.value);
        it.count.textContent = c[it.value] || 0;
        it.label.classList.toggle('zero', !c[it.value]);
      }
    }
    for (const t of texts) snippets(t, all);
    for (const c of cards) {
      const n = c.fids.filter(f => f in act).length;
      c.summary.textContent = n ? n : '';
      c.summary.hidden = !n;
    }
    try { sessionStorage.setItem('order', JSON.stringify(shown)); } catch (e) { /* private mode */ }
  }

  Actions.register('clear-filter', {available: () => !!data, run: () => {
    sel = {};
    for (const t of texts) t.input.value = '';
    changed();
  }});
  // The first search box in view: a text facet, or a long list's "find".
  Actions.register('search', {run: () => {
    const boxes = [...host.querySelectorAll('input[type=search]')].filter(i => i.offsetParent);
    const top = boxes.find(i => i.getBoundingClientRect().bottom > 40) || boxes[0];
    if (top) top.focus(); else flash("no search here: open a question");
  }});

  (async () => {
    const r = await Changes.get('/focus/values');
    if (!r.ok) { msg.textContent = r.body.error || 'failed to load'; return; }
    data = r.body;
    msg.textContent = '';
    build();
    const act = Focus.active(data.values, sel);
    for (const c of cards) c.details.open = c.fids.some(f => f in act);
    history.replaceState(null, '', location.pathname + Focus.query(sel));
    update();
  })();
})();
