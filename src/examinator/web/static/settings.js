'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const wins = [...document.querySelectorAll('.win')], cover = $('cover');
  const img = $('pv-img'), rect = $('pv-rect');
  const sub = $('pv-sub'), page = $('pv-page');
  const msg = $('msg');

  // --- workspace defaults: scan layout and its preview ---
  function preview() {
    const [l, t, r, b] = wins.map(w => Number(w.value) / 10);
    rect.style.left = l + '%';
    rect.style.top = t + '%';
    rect.style.width = Math.max(r - l, 0) + '%';
    rect.style.height = Math.max(b - t, 0) + '%';
  }
  function load() {
    if (!sub.value) return;
    const index = Number(cover.value) + Number(page.value) - 1;
    img.src = `${ROOT}/page/${encodeURIComponent(sub.value)}/${index}.jpg`;
  }
  for (const w of wins) w.addEventListener('input', preview);
  for (const el of [sub, page, cover]) el.addEventListener('input', load);
  Actions.register('save-defaults', {run: async () => {
    const r = await request('/settings', {cover_pages: Number(cover.value), scan_window: wins.map(w => Number(w.value))});
    msg.textContent = r.ok ? "saved" : r.body.error || "failed";
  }});
  const size = $('crop-size'), n = $('crop-n');
  const shown = () => { n.textContent = size.value + ' px'; };
  size.addEventListener('input', shown);
  document.addEventListener('DOMContentLoaded', shown);
  preview();
  load();

  // --- appearance (this browser): theme and the two accents, applied at once ---
  const theme = $('theme');
  theme.value = pref('theme', 'system');
  theme.addEventListener('change', () => { setPref('theme', theme.value); Theme.apply(); });
  for (const input of document.querySelectorAll('input[type=color][data-key]')) {
    const key = input.dataset.key;
    input.value = pref(key, Theme.DEFAULTS[key]);
    input.addEventListener('input', () => { setPref(key, input.value); Theme.apply(); });
  }
  document.addEventListener('click', e => {
    const b = e.target.closest('[data-reset]');
    if (!b) return;
    const key = b.dataset.reset;
    try { localStorage.removeItem(key); } catch (err) { /* private mode */ }
    $(key).value = Theme.DEFAULTS[key];
    Theme.apply();
  });

  // --- keyboard (this browser): single keys on/off, layout preset, own bindings ---
  const single = $('k-single'), preset = $('k-preset'), table = $('keymap').tBodies[0];
  let listening = null;      // the action waiting for a key
  function render() {
    const s = Actions.settings();
    single.checked = s.single;
    preset.value = s.preset;
    table.replaceChildren();
    for (const [name, label] of Actions.CATALOG) {
      const own = Array.isArray(s.custom[name]);
      const keys = make('td', {className: 'keys'});
      if (listening === name) keys.append(make('span', {className: 'dim', textContent: 'press a key… (Esc cancels)'}));
      else {
        const combos = Actions.bindings(name, s);
        for (const c of combos) keys.append(make('kbd', {textContent: Actions.display(c)}));
        if (!combos.length) keys.append(make('span', {className: 'dim', textContent: '—'}));
      }
      const set = make('button', {type: 'button', className: 'quiet', textContent: 'set'});
      set.addEventListener('click', () => { listening = name; render(); });
      const none = make('button', {type: 'button', className: 'quiet', textContent: 'none', title: 'no key'});
      none.addEventListener('click', () => { Actions.rebind(name, null); render(); });
      const tr = make('tr', {}, make('td', {textContent: label}), keys,
                      make('td', {}, set, none, own ? make('span', {className: 'tag accent2', textContent: 'own'}) : ''));
      if (listening === name) tr.className = 'listening';
      table.append(tr);
    }
  }
  // While listening, the next key is the binding (captured before any page action).
  addEventListener('keydown', e => {
    if (!listening) return;
    const combo = Actions.comboOf(e);
    if (!combo) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    if (combo !== 'Escape') Actions.rebind(listening, combo);
    listening = null;
    render();
  }, true);
  single.addEventListener('change', () => { const s = Actions.settings(); s.single = single.checked; Actions.saveSettings(s); });
  preset.addEventListener('change', () => {
    const s = Actions.settings();
    s.preset = preset.value;
    Actions.saveSettings(s);
    render();
  });
  $('k-reset').addEventListener('click', () => {
    const s = Actions.settings();
    s.custom = {};
    Actions.saveSettings(s);
    render();
  });
  addEventListener('storage', e => { if (e.key === 'keys') render(); });
  render();
})();
