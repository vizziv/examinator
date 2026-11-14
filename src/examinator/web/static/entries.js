'use strict';
// Exam data entries (<form class="entry">): save and revert through /exam/set and /exam/clear.
// Used on the exam page and in the question page's key panel. A save that outdates
// grades starts one background recompute; this page shows its progress, and the
// question page hears of it through the 'entry-saved' event.
(() => {
  const sub = $('question')?.dataset.sub;

  function readEntry(form) {
    const kind = form.dataset.kind, el = form.querySelector('.ev'), text = el.value;
    if (kind === 'bool') return el.checked;
    if (form.dataset.nullable && text.trim() === '') return null;
    const nums = () => {
      const parts = text.split(/[\s,]+/).filter(Boolean);
      return parts.every(p => /^-?\d+$/.test(p)) ? parts.map(Number) : text;   // else the server explains
    };
    if (kind === 'region' || kind === 'print_area') return nums();
    if (kind === 'int') return /^\s*-?\d+\s*$/.test(text) ? Number(text) : text;
    if (kind === 'number') return text.trim() !== '' && !isNaN(Number(text)) ? Number(text) : text;
    if (kind === 'choice') return text.split('\n').map(s => s.trim()).filter(Boolean);
    return text;
  }
  // The entry for a path as drawn now (the question page draws its key panel again after a change).
  const formOf = path => document.querySelector(`form.entry[data-path="${CSS.escape(path)}"]`);
  const lineOf = path => Line(formOf(path).querySelector('.emsg'));

  // What was typed is kept over the answer if it changed since the press.
  const shown = el => (el.type === 'checkbox' ? el.checked : el.value);
  function send(form, clear) {
    const path = form.dataset.path, typed = shown(form.querySelector('.ev'));
    return override({path, value: clear ? undefined : readEntry(form), clear, sub,
                     rev: () => formOf(path).dataset.rev || null, take: cur => { formOf(path).dataset.rev = cur.rev ?? ''; }},
                    {line: lineOf(path), done: b => saved(formOf(path), clear, b, typed)});
  }
  function saved(form, clear, b, typed) {
    form.dataset.rev = b.rev ?? '';
    form.classList.toggle('over', b.overridden);
    form.querySelector('[data-action=entry-revert]').hidden = !b.overridden;
    form.querySelector('.ebase').hidden = !b.overridden;
    const el = form.querySelector('.ev'), same = shown(el) === typed;
    if (form.dataset.kind === 'bool') { el.defaultChecked = !!b.value; if (same) el.checked = !!b.value; }
    else { el.defaultValue = b.text; if (same) el.value = b.text; }
    form.classList.toggle('dirty', isDraft(el));
    lineOf(form.dataset.path).say((clear ? "reverted" : "saved") + (b.job ? ` · ${b.checks.join(', ')} outdated` : ''));
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
  const mark = () => document.querySelectorAll('form.entry').forEach(f => f.classList.toggle('dirty', isDraft(f.querySelector('.ev'))));
  document.addEventListener('input', mark);
  document.addEventListener('redrawn', mark);
  document.addEventListener('submit', e => { if (e.target.matches('form.entry')) e.preventDefault(); });
  addEventListener('beforeunload', e => { if ([...document.querySelectorAll('form.entry .ev')].some(isDraft)) e.preventDefault(); });

  // --- the exam page: progress of the recompute, copying the change list ---
  const panel = $('changes');
  if (!panel) return;

  const line = Line('ch-text'), bar = $('ch-bar');
  document.addEventListener('entry-saved', e => {
    const b = e.detail;
    panel.hidden = false;
    $('ch-skipped').textContent = (b.skipped || []).map(s => `not applied: ${s.path} (${s.reason})`).join(' ');
    if (!b.job) { line.say("no grades affected"); return; }
    bar.hidden = false;
    Changes.watch(b.job, {line, each: j => { bar.max = j.total || 1; bar.value = j.done || 0; },
                          done: () => { bar.hidden = true; return `${b.checks.join(', ')} recomputed`; }});
  });
  // A scan's or a submission's own value: back to what applies to the others.
  Actions.register('scoped-revert', {run: ({el}) => {
    if (!confirm("Drop this own value?")) return;
    const {path, scope} = el.dataset;
    return override({path, scope, clear: true, rev: () => el.dataset.rev || null, take: cur => { el.dataset.rev = cur.rev ?? ''; }},
                    {line: Line(el.parentElement.querySelector('.emsg')), done: b => {
                      el.closest('tr').remove();
                      document.dispatchEvent(new CustomEvent('entry-saved', {detail: b}));
                    }});
  }});
  Actions.register('copy-lines', {available: () => !!$('lines'), run: async () => {
    try { await navigator.clipboard.writeText($('lines').textContent); flash("copied", 'ok'); }
    catch (err) { getSelection().selectAllChildren($('lines')); }
  }});
})();
