'use strict';
// Exam data entries (<form class="entry">): save and revert through /exam/set and /exam/clear.
// Used on the exam page and in the question page's key panel. A save that outdates
// grades starts one background recheck; this page shows its progress, and the
// question page hears of it through the 'entry-saved' event.
(() => {
  const sub = document.getElementById('question')?.dataset.sub;

  function readEntry(form) {
    const kind = form.dataset.kind, el = form.querySelector('.ev'), text = el.value;
    if (kind === 'bool') return el.checked;
    if (form.dataset.nullable && text.trim() === '') return null;
    const nums = () => {
      const parts = text.split(/[\s,]+/).filter(Boolean);
      return parts.every(p => /^-?\d+$/.test(p)) ? parts.map(Number) : text;   // else the server explains
    };
    if (kind === 'region' || kind === 'pages' || kind === 'print_area') return nums();
    if (kind === 'int') return /^\s*-?\d+\s*$/.test(text) ? Number(text) : text;
    if (kind === 'number') return text.trim() !== '' && !isNaN(Number(text)) ? Number(text) : text;
    if (kind === 'choice') return text.split('\n').map(s => s.trim()).filter(Boolean);
    if (kind === 'map') {
      const out = {};
      for (const line of text.split('\n')) {
        if (!line.trim()) continue;
        const at = line.lastIndexOf('=');
        if (at < 0) return text;
        out[line.slice(0, at).trim()] = line.slice(at + 1).trim();
      }
      return out;
    }
    return text;
  }
  const revOf = form => (form.dataset.rev === '' ? null : Number(form.dataset.rev));
  // Each entry's own message line, where its changes say how they went.
  const say = (form, text) => Line(form.querySelector('.emsg')).say(text);

  // A refusal keeps the draft and takes the revision saved now: saving again overwrites knowingly.
  function send(form, clear) {
    const path = form.dataset.path;
    const data = clear ? {path, rev: revOf(form), sub} : {path, value: readEntry(form), rev: revOf(form), sub};
    return Changes.send(`override::${path}`, clear ? '/exam/clear' : '/exam/set', data, {
      line: Line(form.querySelector('.emsg')),
      redraw: cur => {
        form.dataset.rev = cur.rev ?? '';
        const now = cur.rev == null ? "the package's" : brief(cur.value);
        return !clear ? Changes.kept(now) : cur.rev == null ? "reverted elsewhere already" : `changed elsewhere to ${now}: revert again to drop it`;
      },
      done: b => saved(form, clear, b)});
  }
  function saved(form, clear, b) {
    form.dataset.rev = b.rev ?? '';
    form.classList.toggle('over', b.overridden);
    form.classList.remove('dirty');
    form.querySelector('[data-action=entry-revert]').hidden = !b.overridden;
    form.querySelector('.ebase').hidden = !b.overridden;
    if (clear) {
      const el = form.querySelector('.ev');
      if (form.dataset.kind === 'bool') el.checked = !!b.value; else el.value = b.text;
    }
    say(form, (clear ? "reverted" : "saved") + (b.job ? ` · ${b.checks.join(', ')} outdated` : ''));
    document.dispatchEvent(new CustomEvent('entry-saved', {detail: b}));
  }

  window.Entries = {send};
  Actions.register('entry-save', {run: ({el}) => send(el.closest('form.entry'), false)});
  Actions.register('entry-revert', {run: ({el}) => {
    if (confirm("Back to the package value, for all submissions?")) send(el.closest('form.entry'), true);
  }});
  // Cmd+S / Cmd+Enter save the entry being edited (the question page has its own save,
  // which defers to this one inside an entry).
  const inEntry = ctx => ctx.target?.closest?.('form.entry');
  for (const name of ['save', 'verify']) {
    if (!Actions.has(name)) Actions.register(name, {available: ctx => !!inEntry(ctx), run: ctx => send(inEntry(ctx), false)});
  }
  document.addEventListener('input', e => {
    const form = e.target.closest('form.entry');
    if (form) { form.classList.add('dirty'); say(form, ''); }
  });
  document.addEventListener('submit', e => { if (e.target.matches('form.entry')) e.preventDefault(); });
  addEventListener('beforeunload', e => { if (document.querySelector('form.entry.dirty')) e.preventDefault(); });

  // --- the exam page: progress of the recheck, copying the change list ---
  const panel = document.getElementById('changes');
  if (!panel) return;
  const $ = id => document.getElementById(id);

  async function follow(job, what) {
    const bar = $('ch-bar');
    bar.hidden = false;
    for (;;) {
      const r = await request('/jobs/' + encodeURIComponent(job));
      if (!r.ok) break;
      bar.max = r.body.total || 1;
      bar.value = r.body.done || 0;
      $('ch-text').textContent = `${what} ${r.body.done || 0}/${r.body.total || 0}`;
      if (r.body.state !== 'running') {
        const bad = r.body.outcomes || [];
        $('ch-fail').textContent = r.body.state === 'failed' ? `failed: ${r.body.error || ''}`
          : bad.length ? `${bad.length} not done: ` + bad.slice(0, 5).map(o => `${o.submission} ${o.step} ${o.status}`).join('; ') : '';
        break;
      }
      await new Promise(done => setTimeout(done, 600));
    }
    bar.hidden = true;
  }

  document.addEventListener('entry-saved', async e => {
    const b = e.detail;
    panel.hidden = false;
    $('ch-fail').textContent = '';
    for (const s of b.skipped || []) $('ch-fail').textContent += `not applied: ${s.path} (${s.reason}) `;
    if (!b.job) { $('ch-text').textContent = "no grades affected"; return; }
    $('ch-text').textContent = `${b.checks.join(', ')} outdated; recomputing`;
    await follow(b.job, `${b.checks.join(', ')} recomputed`);
  });
  // A scan's or a submission's own value: back to what applies to the others.
  Actions.register('scoped-revert', {run: ({el}) => {
    if (!confirm("Drop this own value?")) return;
    const {path, scope} = el.dataset;
    return Changes.send(`override:${scope}:${path}`, '/exam/clear', {path, scope, rev: el.dataset.rev === '' ? null : Number(el.dataset.rev)}, {
      line: Line(el.parentElement.querySelector('.emsg')),
      redraw: cur => {
        el.dataset.rev = cur.rev ?? '';
        return cur.rev == null ? "dropped elsewhere already" : `changed elsewhere to ${brief(cur.value)}: revert again to drop it`;
      },
      done: b => {
        el.closest('tr').remove();
        document.dispatchEvent(new CustomEvent('entry-saved', {detail: b}));
      }});
  }});
  Actions.register('copy-lines', {available: () => !!$('lines'), run: async () => {
    try { await navigator.clipboard.writeText($('lines').textContent); flash("copied", 'ok'); }
    catch (err) { getSelection().selectAllChildren($('lines')); }
  }});
})();
