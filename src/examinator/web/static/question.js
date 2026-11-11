'use strict';
// The question page: drafts, actions, live refresh, keyboard, and the scan window.
(() => {
  const main = document.getElementById('question');
  const sub = main.dataset.sub, q = main.dataset.q;
  const base = `/s/${encodeURIComponent(sub)}/${encodeURIComponent(q)}`;
  const busy = document.getElementById('busy');
  const $ = id => document.getElementById(id);
  const revOf = el => (el.dataset.rev === '' ? null : Number(el.dataset.rev));

  // --- next / previous student, following the grid's order (the focus, search and sort) ---
  const ids = JSON.parse(main.dataset.ids);
  let order = session('order', null);
  if (!Array.isArray(order)) order = ids;
  const at = order.indexOf(sub);
  $('pos').textContent = `${at < 0 ? '–' : at + 1}/${order.length}`;
  function go(step) {
    let next = order[at + step];
    if (at < 0) {         // not in the order: the nearest one in it, by submission id
      const rank = new Map(ids.map((s, i) => [s, i])), me = rank.get(sub);
      const side = order.filter(s => (step > 0 ? rank.get(s) > me : rank.get(s) < me))
        .sort((a, b) => rank.get(a) - rank.get(b));
      next = step > 0 ? side[0] : side[side.length - 1];
    }
    if (next !== undefined) location.href = `${ROOT}/s/${encodeURIComponent(next)}/${encodeURIComponent(q)}`;
  }

  // --- the focus: does this submission still match it; "like this" sets one ---
  (async () => {
    const r = await request('/focus/values?sub=' + encodeURIComponent(sub));
    if (!r.ok) return;
    const sel = Focus.load();
    $('unfocused').hidden = !Focus.size(sel) || Focus.matches(r.body.values, sel, sub);
    const like = $('like'), mine = r.body.questions.find(x => x.id === q);
    for (const f of mine ? mine.facets : []) {
      if (f.kind !== 'choice') continue;
      for (const v of (r.body.values[f.id] || {})[sub] || []) {
        const a = make('a', {href: `${ROOT}/${Focus.query({[f.id]: [v]})}`, title: `every submission with ${f.label} = ${v}`},
          `${f.label} `, make('code', {textContent: v}));
        like.append(a);
      }
    }
    if (like.childElementCount) { like.prepend(make('span', {className: 'dim', textContent: 'like this:'})); like.hidden = false; }
  })();

  // --- drafts: an editor is a <form class="draft" data-kind data-mode data-rev> ---
  const editors = () => [...document.querySelectorAll('form.draft')];
  const draftKey = (ed, rev) => `draft:${sub}:${q}:${ed.dataset.kind}:${rev === undefined ? ed.dataset.rev : rev}`;
  function read(ed) {
    if (ed.dataset.mode !== 'form') return ed.querySelector('textarea').value;
    const body = {};
    for (const input of ed.querySelectorAll('[data-label]')) {
      body[input.dataset.label] = input.value.trim() === '' ? null : input.value;
    }
    return body;
  }
  function fill(ed, body) {
    if (ed.dataset.mode !== 'form') { ed.querySelector('textarea').value = body; return; }
    for (const input of ed.querySelectorAll('[data-label]')) input.value = body[input.dataset.label] ?? '';
  }
  const dirty = ed => JSON.stringify(read(ed)) !== ed.dataset.orig;
  function touched(ed) {
    const d = dirty(ed);
    ed.classList.toggle('dirty', d);
    try {
      if (d) sessionStorage.setItem(draftKey(ed), JSON.stringify(read(ed)));
      else sessionStorage.removeItem(draftKey(ed));
    } catch (e) { /* private mode */ }
  }
  function adopt(ed) {
    ed.dataset.orig = JSON.stringify(read(ed));
    // A draft left behind, possibly written against an older revision: bring it back
    // with the revision it was made against, so saving it cannot overwrite newer work.
    const prefix = draftKey(ed, '');
    try {
      for (let i = 0; i < sessionStorage.length; i++) {
        const key = sessionStorage.key(i);
        if (!key.startsWith(prefix)) continue;
        const rev = key.slice(prefix.length);
        fill(ed, JSON.parse(sessionStorage.getItem(key)));
        if (rev !== ed.dataset.rev) { ed.dataset.rev = rev; ed.querySelector('.notice').hidden = false; }
        ed.classList.toggle('dirty', dirty(ed));
        if (ed.closest('details')) ed.closest('details').open = true;
        break;
      }
    } catch (e) { /* private mode */ }
  }
  editors().forEach(adopt);
  document.addEventListener('input', e => {
    const ed = e.target.closest('form.draft');
    if (ed) touched(ed);
  });
  addEventListener('beforeunload', e => { if (editors().some(dirty)) e.preventDefault(); });

  // --- live refresh: re-read this page and swap what the server marks data-live ---
  async function refresh() {
    let doc;
    try {
      const res = await fetch(location.href, {headers: {Accept: 'text/html'}});
      if (!res.ok) return;
      doc = new DOMParser().parseFromString(await res.text(), 'text/html');
    } catch (e) { return; }
    for (const fresh of doc.querySelectorAll('[data-live]')) {
      const old = $(fresh.id);
      if (!old) continue;
      const node = document.importNode(fresh, true);
      if (old.tagName === 'DETAILS') node.open = old.open;
      old.replaceWith(node);
      drawCrops(node);
      window.drawPlots(node);
    }
    for (const fresh of doc.querySelectorAll('form.draft')) {
      const old = $(fresh.id);
      if (!old || fresh.dataset.rev === old.dataset.rev) continue;
      if (dirty(old)) { old.querySelector('.notice').hidden = false; continue; }
      const node = document.importNode(fresh, true);
      old.replaceWith(node);
      node.dataset.orig = JSON.stringify(read(node));
      drawCrops(node);
    }
  }

  async function settle(body) {          // wait for the background job an action started
    await refresh();
    if (!body.job) return;
    busy.hidden = false;
    const job = await waitJob(body.job);
    busy.hidden = true;
    const bad = (job.outcomes || []).filter(o => o.status !== 'done');
    if (job.state === 'failed') flash(`failed: ${job.error || ''}`);
    else if (bad.length) flash(bad.map(o => `${o.stage}: ${o.status}${o.detail ? ' — ' + o.detail : ''}`).join('; '));
    await refresh();
  }

  async function act(path, data) {
    flash('');
    const r = await request(`${base}/${path}`, data);
    if (r.status === 409) { flash("changed elsewhere"); await refresh(); return false; }
    if (!r.ok) { flash(r.body.error || "failed"); return false; }
    await settle(r.body);
    return true;
  }

  async function save(ed, verify) {
    if (!ed) return;
    flash('');
    const body = read(ed);
    const r = await request(`${base}/save`, {kind: ed.dataset.kind, body, rev: revOf(ed), verify});
    if (r.status === 409) { ed.querySelector('.notice').hidden = false; return; }   // the draft stays
    if (!r.ok) { flash(r.body.error || "failed"); return; }
    try { sessionStorage.removeItem(draftKey(ed)); } catch (e) { /* private mode */ }
    ed.dataset.rev = r.body.rev;
    ed.dataset.orig = JSON.stringify(body);
    ed.classList.remove('dirty');
    ed.querySelector('.notice').hidden = true;
    await settle(r.body);
  }

  const parseEd = () => $('ed-parse');
  const editorFor = el => el.closest('form.draft') || parseEd();
  const decide = (el, extra) => {
    const box = el.closest('.decide');
    // What the decision is made against: the revisions on screen, not the newest ones.
    const seen = JSON.parse($('parts').dataset.seen);
    for (const kind of Object.keys(seen)) {
      const ed = $('ed-' + kind);
      if (ed) seen[kind] = revOf(ed);
    }
    return act('decide', {part: box.dataset.part, rev: revOf(box), seen, ...extra});
  };
  const variantRev = () => revOf($('variant'));

  const actions = {
    prev: () => go(-1),
    next: () => go(1),
    save: el => save(editorFor(el), false),
    verify: el => save(editorFor(el), true),
    unverify: el => act('verify', {kind: el.dataset.kind, rev: revOf(el), verified: false}),
    discard: el => {
      const ed = el.closest('form.draft');
      try { sessionStorage.removeItem(draftKey(ed)); } catch (e) { /* private mode */ }
      ed.dataset.orig = JSON.stringify(read(ed));
      location.reload();
    },
    decide: el => decide(el, {value: el.dataset.value === 'true'}),
    'decide-number': el => {
      const input = el.closest('.decide').querySelector('input');
      if (input.value.trim() === '') return input.focus();
      return decide(el, {value: Number(input.value)});
    },
    'decide-blank': el => decide(el, {blank: true}),
    undecide: el => {
      const box = el.closest('.decide');
      return act('undecide', {part: box.dataset.part, rev: revOf(box)});
    },
    'variant-choose': () => {
      const v = $('variant-pick').value;
      return v ? act('variant', {variant: v, rev: variantRev()}) : $('variant-pick').focus();
    },
    'variant-clear': () => act('variant', {variant: null, rev: variantRev()}),
    'variant-verify': () => act('verify', {kind: 'variant', rev: variantRev()}),
    cue: el => {
      const input = el.closest('.cue').querySelector('input');
      return act('cue', {cue: input.dataset.cue, reading: input.value, rev: variantRev()});
    },
    run: el => (el.dataset.confirm && !confirm(`Replace ${el.dataset.confirm}?`)) ? null : act('run', {stage: el.dataset.stage, onward: !!el.dataset.onward,
                           reasoning: !!el.dataset.model && !!$('run-reasoning')?.checked,
                           params: $('run-params').value}),
    pages: async () => { if (await act('pages', {pages: $('pg-list').value, note: $('pg-note').value})) location.reload(); },
    layout: async () => {
      const r = await request(`/s/${encodeURIComponent(sub)}/layout`,
                              {cover_pages: $('lo-cover').value, scan_window: $('lo-window').value});
      if (r.ok) location.reload(); else flash(r.body.error || "failed");
    },
    restore: el => act('restore', {rev: Number(el.dataset.rev), expect: revOf(parseEd())}),
  };
  document.addEventListener('click', e => {
    const el = e.target.closest('[data-act]');
    if (el && actions[el.dataset.act]) { e.preventDefault(); actions[el.dataset.act](el); }
  });
  document.addEventListener('submit', e => e.preventDefault());

  // --- an exam-data edit (key panel, regions): this submission is rechecked first ---
  document.addEventListener('entry-saved', async e => {
    const b = e.detail;
    await refresh();
    if (!b.job) return;
    busy.hidden = false;
    for (;;) {
      const r = await request('/jobs/' + encodeURIComponent(b.job));
      if (!r.ok || r.body.state !== 'running' || r.body.done >= b.questions.length) break;
      await new Promise(done => setTimeout(done, 400));
    }
    busy.hidden = true;
    await refresh();
  });

  // --- the scan window follows this page ---
  const channel = new BroadcastChannel('examinator-scan');
  const announce = () => channel.postMessage({
    sub, q, name: main.dataset.name, pages: JSON.parse(main.dataset.pages), count: Number(main.dataset.count)});
  channel.addEventListener('message', e => { if (e.data && e.data.hello) announce(); });
  announce();

  // --- keyboard ---
  document.addEventListener('keydown', e => {
    const typing = e.target.matches('input, textarea, select');
    const mod = e.ctrlKey || e.metaKey;
    const done = fn => { e.preventDefault(); fn(); };
    if (e.altKey && !mod && e.code === 'KeyJ') return done(() => go(1));
    if (e.altKey && !mod && e.code === 'KeyK') return done(() => go(-1));
    if (mod && e.key === 'Enter') return done(() => save(editorFor(e.target), true));
    if (mod && e.key.toLowerCase() === 's') return done(() => save(editorFor(e.target), false));
    if (e.key === 'Escape' && typing) return e.target.blur();
    if (typing || mod || e.altKey) return;
    const keys = {
      j: () => go(1), ']': () => go(1), k: () => go(-1), '[': () => go(-1),
      h: () => $('prev-q')?.click(), l: () => $('next-q')?.click(),
      v: () => save(parseEd(), true), s: () => save(parseEd(), false),
      e: () => parseEd()?.querySelector('input:not([readonly]), textarea:not([readonly])')?.focus(),
      p: () => $('scan-link').click(), u: () => $('home').click(),
    };
    if (keys[e.key]) done(keys[e.key]);
  });
})();
