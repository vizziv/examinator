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
    if (kind === 'region' || kind === 'pages') return nums();
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
  const say = (form, text, bad) => {
    const m = form.querySelector('.emsg');
    m.textContent = text;
    m.classList.toggle('notice', !!bad);
  };
  const short = v => { const s = JSON.stringify(v) ?? ''; return s.length > 80 ? s.slice(0, 80) + '…' : s; };

  async function send(form, clear) {
    const path = form.dataset.path;
    const data = clear ? {path, rev: revOf(form), sub} : {path, value: readEntry(form), rev: revOf(form), sub};
    say(form, '…');
    const r = await request(clear ? '/exam/clear' : '/exam/set', data);
    if (r.status === 409) {
      // Keep the draft; take the newer revision so that saving again overwrites it knowingly.
      const cur = r.body.current || {};
      form.dataset.rev = cur.rev ?? '';
      say(form, cur.rev == null ? "changed elsewhere: reverted; save again to overwrite"
                                : `changed elsewhere by ${cur.who}: ${short(cur.value)}; save again to overwrite`, true);
      return;
    }
    if (!r.ok) { say(form, r.body.error || "failed", true); return; }
    const b = r.body;
    form.dataset.rev = b.rev ?? '';
    form.classList.toggle('over', b.overridden);
    form.classList.remove('dirty');
    form.querySelector('[data-action=entry-revert]').hidden = !b.overridden;
    form.querySelector('.ebase').hidden = !b.overridden;
    if (clear) {
      const el = form.querySelector('.ev');
      if (form.dataset.kind === 'bool') el.checked = !!b.value; else el.value = b.text;
    }
    say(form, (clear ? "reverted" : "saved") + (b.job ? ` · ${b.checks.join(', ')} outdated` : '')
                  + ((b.reread || []).length ? " · read again to use it" : ''));
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
    const reread = (b.reread || []).length ? ` · how ${b.reread.join(', ')} is read changed: read again on its question page` : '';
    if (!b.job) { $('ch-text').textContent = "no grades affected" + reread; return; }
    $('ch-text').textContent = `${b.checks.join(', ')} outdated; recomputing` + reread;
    await follow(b.job, `${b.checks.join(', ')} recomputed`);
  });
  Actions.register('copy-lines', {available: () => !!$('lines'), run: async () => {
    try { await navigator.clipboard.writeText($('lines').textContent); flash("copied", 'ok'); }
    catch (err) { getSelection().selectAllChildren($('lines')); }
  }});
})();
