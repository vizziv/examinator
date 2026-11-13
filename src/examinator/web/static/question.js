'use strict';
// The question page: the values on it (a draft), actions (buttons and keys, see actions.js),
// live refresh, and the scanned pages beside them.
(() => {
  const main = $('question');
  const sub = main.dataset.sub, q = main.dataset.q;
  const base = `/s/${encodeURIComponent(sub)}/${encodeURIComponent(q)}`;
  const busy = $('busy');
  const revOf = el => (el.dataset.rev === '' ? null : Number(el.dataset.rev));

  // --- next / previous submission, following the grid's order (the filter, search and sort) ---
  try { sessionStorage.setItem('gridAt', JSON.stringify({sub, q})); } catch (e) { /* private mode */ }  // the grid's cursor
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
  function canGo(step) {
    if (at >= 0) return order[at + step] !== undefined;
    const rank = new Map(ids.map((s, i) => [s, i])), me = rank.get(sub);
    return order.some(s => (step > 0 ? rank.get(s) > me : rank.get(s) < me));
  }

  // --- the filter: does this submission still match it (asked only while one is set) ---
  async function inFilter() {
    const sel = Focus.load();
    if (!Focus.size(sel)) return;
    const r = await request('/focus/values?sub=' + encodeURIComponent(sub));
    if (r.ok) $('unfiltered').hidden = Focus.matches(r.body.values, sel, sub);
  }

  // --- the draft: every value on the page, <input|textarea data-key data-rev> in form#ed-fields ---
  const form = () => $('ed-fields');
  const inputs = () => [...form().querySelectorAll('[data-key]:is(input, textarea)')];
  const draftKey = () => `draft:${sub}:${q}`;
  function snapshot() {
    for (const el of inputs()) el.dataset.orig = el.value;
  }
  const changed = () => inputs().filter(el => el.value !== el.dataset.orig);
  const dirty = () => changed().length > 0;
  function touched() {
    form().classList.toggle('dirty', dirty());
    for (const el of inputs()) el.closest('.field').classList.toggle('dirty', el.value !== el.dataset.orig);
    try {
      if (dirty()) {
        sessionStorage.setItem(draftKey(), JSON.stringify(changed().map(el => ({key: el.dataset.key, rev: el.dataset.rev,
                                                                                text: el.value}))));
      } else sessionStorage.removeItem(draftKey());
    } catch (e) { /* private mode */ }
  }
  // A draft left behind, possibly written against older values: bring it back with the
  // revisions it was made against, so saving it cannot overwrite newer work.
  function adopt() {
    snapshot();
    let saved = null;
    try { saved = JSON.parse(sessionStorage.getItem(draftKey())); } catch (e) { /* private mode */ }
    for (const d of Array.isArray(saved) ? saved : []) {
      const el = inputs().find(i => i.dataset.key === d.key);
      if (!el) continue;
      el.value = d.text;
      if (String(d.rev) !== el.dataset.rev) { el.dataset.rev = d.rev; form().querySelector('.notice').hidden = false; }
    }
    touched();
  }
  adopt();
  document.addEventListener('input', e => { if (e.target.closest?.('#ed-fields')) touched(); });
  addEventListener('beforeunload', e => { if (dirty()) e.preventDefault(); });

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
      Actions.hints(node);
    }
    const fresh = doc.getElementById('ed-fields'), old = form();
    if (fresh && old) {
      const revs = () => inputs().map(i => `${i.dataset.key}@${i.dataset.rev}`).join();
      const freshRevs = [...fresh.querySelectorAll('[data-key]:is(input, textarea)')]
        .map(i => `${i.dataset.key}@${i.dataset.rev}`).join();
      if (freshRevs !== revs()) {
        if (dirty()) old.querySelector('.notice').hidden = false;
        else {
          const node = document.importNode(fresh, true);
          const others = node.querySelector('#others');
          if (others) others.open = !!old.querySelector('#others')?.open;
          old.replaceWith(node);
          snapshot();
          drawCrops(node);
          Actions.hints(node);
        }
      }
    }
    ready();
    inFilter();
  }

  async function settle(body) {          // wait for the background job an action started
    await refresh();
    if (!body.job) return;
    busy.hidden = false;
    const job = await waitJob(body.job);
    busy.hidden = true;
    const bad = (job.outcomes || []).filter(o => o.status !== 'done' && o.status !== 'skipped');
    if (job.state === 'failed') flash(`failed: ${job.error || ''}`);
    else if (bad.length) flash(bad.map(o => `${o.step}: ${o.status}${o.detail ? ' — ' + o.detail : ''}`).join('; '));
    await refresh();
  }

  // Every change here is about this page's one subject (this submission's question) as
  // drawn, sent through Changes, one chain per row; a refusal redraws the page.
  Changes.redraw = () => refresh();
  const act = (row, path, data) => Changes.send(row, `${base}/${path}`, data, {done: settle});

  // Set this submission's own value of the exam data at `el.dataset.path` (null: drop it),
  // then reload. A refusal takes the revision saved now: setting again overwrites knowingly.
  function own(el, value) {
    const path = el.dataset.path, scope = JSON.parse($('scan').dataset.scopes).own;
    return Changes.send(`override:${scope}:${path}`, value === null ? '/exam/clear' : '/exam/set',
      {path, scope, value, rev: revOf(el), sub},
      {done: () => location.reload(), redraw: cur => { el.dataset.rev = cur.rev ?? ''; }});
  }

  // Save the values changed here (a person who sets a value verifies it); with `all`,
  // verify this question's other values too (`list`: 'waitingOther' for those read from
  // other questions). The answer, or null when something could not be saved or verified (a
  // conflict, a review marker), so V stays on this submission.
  function save(all, list = 'waiting') {
    flash('');
    const values = {}, edited = changed();
    for (const el of edited) values[el.dataset.key] = {text: el.value, rev: revOf(el)};
    const verify = all ? JSON.parse($('parts').dataset[list] || '[]')
      .filter(k => !(k in values))
      .map(k => ({key: k, rev: revOf(inputs().find(i => i.dataset.key === k))})) : [];
    if (!edited.length && !verify.length) return Promise.resolve({});
    const sent = edited.map(el => [el, el.value]);
    return Changes.send('values', `${base}/save`, {values, verify}, {
      redraw: () => { form().querySelector('.notice').hidden = false; refresh(); },
      done: async b => {
        try { sessionStorage.removeItem(draftKey()); } catch (e) { /* private mode */ }
        for (const [el, text] of sent) {
          if (el.value === text) el.dataset.orig = text;
          if (b.revs && el.dataset.key in b.revs) el.dataset.rev = b.revs[el.dataset.key];
        }
        touched();
        form().querySelector('.notice').hidden = true;
        await settle(b);
      }});
  }
  const waiting = () => JSON.parse($('parts').dataset.waiting || '[]').length > 0 || dirty();

  const decide = (el, extra) => {
    const box = el.closest('.decide');
    // What the decision is made against: every field on the page (those from other questions
    // too) at the revision its input shows, null where it shows no value; never the newest
    // ones a refresh learned of while a draft kept the form. A field the server counts on the
    // page but this page does not show is missing here, so the server refuses the decision.
    const seen = {};
    for (const i of inputs()) seen[i.dataset.key] = revOf(i);
    return act(`decision:${box.dataset.check}`, 'decide', {check: box.dataset.check, rev: revOf(box), seen, ...extra});
  };
  // Z / X / C / G grade the first open check (or the only one): its buttons carry those
  // actions, so they show the keys; the other checks' buttons keep the plain ones.
  const GRADES = {'v-t': 'grade-right', 'v-f': 'grade-wrong', 'v-b': 'grade-blank', 'num-set': 'grade-number'};
  function target(kind) {
    const boxes = [...document.querySelectorAll('#parts .decide')];
    const t = boxes.find(b => b.dataset.open) || (boxes.length === 1 ? boxes[0] : null);
    if (kind !== 'number') return t;
    return boxes.find(b => b.dataset.open && b.dataset.kind === 'number') || (t && t.dataset.kind === 'number' ? t : null);
  }
  const targetButton = (cls, kind) => target(kind)?.querySelector('button.' + cls);
  function ready() {
    for (const [cls, name] of Object.entries(GRADES)) {
      const b = targetButton(cls, cls === 'num-set' ? 'number' : null);
      if (b) b.dataset.action = name;
    }
    Actions.hints($('parts'));
    Actions.enable(document);
  }

  // --- the scanned pages: hideable and resizable (both remembered), opening at this question's first page ---
  const scan = $('scan'), split = $('split');
  function scanWidth(px) {
    if (px) setPref('scanWidth', Math.round(Math.min(innerWidth * 0.75, Math.max(200, px))));
    const w = pref('scanWidth', 0), at = scan.scrollTop / (scan.scrollHeight || 1);
    if (w) main.style.setProperty('--scan-w', w + 'px');
    if (px) scan.scrollTop = at * scan.scrollHeight;      // the same place on the pages at the new width
    drawCrops();
  }
  function showScan() {
    const hide = !!pref('hideScan', false);
    main.classList.toggle('no-scan', hide);
    $('scan-toggle').setAttribute('aria-pressed', String(!hide));
    scanWidth();
    toFirst();
  }
  let moved = false;
  function toFirst() {            // until the grader scrolls it themselves
    const first = scan.querySelector('.sheet.on');
    if (first && !moved) scan.scrollTop = first.offsetTop - ($('rg')?.offsetHeight || 0);
  }
  for (const name of ['wheel', 'pointerdown', 'keydown']) scan.addEventListener(name, () => { moved = true; }, {passive: true});
  addEventListener('load', toFirst);
  showScan();
  split.addEventListener('pointerdown', e => {
    split.setPointerCapture(e.pointerId);
    split.classList.add('dragging');
    e.preventDefault();
  });
  split.addEventListener('pointermove', e => {
    if (split.classList.contains('dragging')) scanWidth(e.clientX - scan.getBoundingClientRect().left);
  });
  for (const name of ['pointerup', 'pointercancel']) split.addEventListener(name, () => split.classList.remove('dragging'));
  // Ctrl+scroll over the pages grows or shrinks their panel (over the work area: the crop scale).
  scan.addEventListener('wheel', e => {
    if (!e.ctrlKey) return;
    e.preventDefault();
    scanWidth(scan.getBoundingClientRect().width * Math.exp(-e.deltaY * 0.001));
  }, {passive: false});
  document.addEventListener('click', e => {
    for (const m of document.querySelectorAll('.fmenu[open]')) if (!m.contains(e.target)) m.open = false;
  });

  // Ctrl+scroll over the work area changes the crop scale (a pinch on a trackpad arrives the same way).
  $('work').addEventListener('wheel', e => {
    if (!e.ctrlKey) return;
    e.preventDefault();
    setCropScale(cropScale() * Math.exp(-e.deltaY * 0.001));
  }, {passive: false});
  $('others')?.addEventListener('toggle', () => drawCrops($('others')));

  // --- actions: buttons (data-action) and keys trigger the same ones ---
  const entryOf = ctx => ctx.target?.closest?.('form.entry');
  const link = name => document.querySelector(`a[data-action="${name}"]`);
  const follow = name => ({available: () => !!link(name), run: () => link(name).click()});
  const hasFields = () => inputs().length > 0;
  const actions = {
    'prev-student': {available: () => canGo(-1), run: () => go(-1)},
    'next-student': {available: () => canGo(1), run: () => go(1)},
    'prev-question': follow('prev-question'),
    'next-question': follow('next-question'),
    // Cmd+S and Cmd+Enter in an exam-data entry (the key panel) save that entry.
    save: {available: ctx => !!entryOf(ctx) || hasFields(),
           run: ctx => (entryOf(ctx) ? Entries.send(entryOf(ctx)) : save(false))},
    // Cmd/Ctrl+Enter: an entry saves; elsewhere save and verify everything here. Stays here.
    verify: {available: ctx => !!entryOf(ctx) || hasFields(),
             run: ctx => (entryOf(ctx) ? Entries.send(entryOf(ctx)) : save(true))},
    // V: verify everything here, then the next submission in the filter (as on Scans and Names: "yes, next").
    'verify-next': {available: () => canGo(1) || waiting(),
                    run: () => save(true).then(b => { if (b && canGo(1)) go(1); })},
    'grade-right': {available: () => !!targetButton('v-t'), run: ({el}) => decide(el || targetButton('v-t'), {value: true})},
    'grade-wrong': {available: () => !!targetButton('v-f'), run: ({el}) => decide(el || targetButton('v-f'), {value: false})},
    'grade-blank': {available: () => !!targetButton('v-b'), run: ({el}) => decide(el || targetButton('v-b'), {blank: true})},
    // G: to the number box (Enter there sets the grade); its set button sets it.
    'grade-number': {available: () => !!targetButton('num-set', 'number'),
                     run: ({el}) => (el ? setNumber(el) : target('number').querySelector('input').focus())},
    edit: {available: hasFields, run: () => inputs()[0].focus()},
    'verify-others': {run: () => save(true, 'waitingOther')},
    // Rare, so in each field's menu: verify or unverify that one value.
    'verify-one': {run: ({el}) => act('values', 'save', {values: {}, verify: [{key: el.dataset.key, rev: revOf(el)}]})},
    unverify: {run: ({el}) => act('values', 'unverify', {key: el.dataset.key, rev: revOf(el)})},
    'to-default': {run: ({el}) => act('values', 'default', {key: el.dataset.key, rev: revOf(el)})},
    'toggle-scan': {run: () => { setPref('hideScan', !pref('hideScan', false)); showScan(); }},
    discard: {run: () => {
      try { sessionStorage.removeItem(draftKey()); } catch (e) { /* private mode */ }
      snapshot();
      location.reload();
    }},
    decide: {run: ({el}) => decide(el, {value: el.dataset.value === 'true'})},
    'decide-number': {run: ({el}) => setNumber(el)},
    'decide-blank': {run: ({el}) => decide(el, {blank: true})},
    clear: {run: ({el}) => {
      const box = el.closest('.decide');
      return act(`decision:${box.dataset.check}`, 'clear', {check: box.dataset.check, rev: revOf(box)});
    }},
    read: {available: ({el}) => !el || !el.disabled, run: ({el}) => {
      const all = !!$('read-all')?.checked, button = el || document.querySelector('[data-action="read"]');
      if (all && !confirm("Read again, replacing values a person set or verified?")) return null;
      return act('read', 'read', {include_protected: all, rev: revOf(button)});
    }},
    check: {run: ({el}) => act(`check:${el.dataset.check}`, 'check', {check: el.dataset.check, rev: revOf(el)})},
    // This submission's own pages (none, or the question's: drop them); beyond the exam's: its extra sheets.
    pages: {run: () => {
      const list = $('pg-list');
      const parts = list.value.split(/[\s,]+/).filter(Boolean);
      const pages = parts.every(p => /^\d+$/.test(p)) ? parts.map(Number) : list.value;    // else the server explains
      const drop = !parts.length || String(pages) === list.dataset.default;
      return own(list, drop ? null : pages);
    }},
    restore: {run: ({el}) => {
      const field = inputs().find(i => i.dataset.key === el.dataset.key);
      return act('values', 'restore', {key: el.dataset.key, restore: Number(el.dataset.restore), rev: field ? revOf(field) : null});
    }},
  };
  for (const [name, spec] of Object.entries(actions)) Actions.register(name, spec);
  function setNumber(el) {
    const input = el.closest('.decide').querySelector('input');
    if (input.value.trim() === '') return input.focus();
    return decide(el, {value: Number(input.value)});
  }
  // Enter in a number box sets the grade.
  document.addEventListener('keydown', e => {
    if (e.key !== 'Enter' || e.isComposing || !e.target.matches?.('.decide input.num')) return;
    e.preventDefault();
    Actions.trigger('decide-number', {el: e.target, target: e.target});
  });
  document.addEventListener('submit', e => e.preventDefault());
  document.addEventListener('DOMContentLoaded', ready);
  ready();
  inFilter();

  // --- an exam-data edit (key panel, regions): this submission is recomputed first ---
  document.addEventListener('entry-saved', async e => {
    const b = e.detail;
    await refresh();
    if (!b.job) return;
    busy.hidden = false;
    for (;;) {
      const r = await request('/jobs/' + encodeURIComponent(b.job));
      if (!r.ok || r.body.state !== 'running' || r.body.done >= 1) break;
      await new Promise(done => setTimeout(done, 400));
    }
    busy.hidden = true;
    await refresh();
  });
})();
