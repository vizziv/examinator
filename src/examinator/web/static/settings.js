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
  // Accents as OKLCH hue + chroma; the lightness is per theme and role (theme.js).
  for (const box of document.querySelectorAll('.picker')) {
    const key = box.dataset.key, two = key === 'accent2';
    const hue = box.querySelector('.pk-h'), vivid = box.querySelector('.pk-c'), quick = box.querySelector('.pk-quick');
    const L = {light: Theme.ROLES.light[key][1], dark: Theme.ROLES.dark[key][1]};
    const text = {light: Theme.ROLES.light[two ? 'accent2-text' : 'link'][1], dark: Theme.ROLES.dark[two ? 'accent2-text' : 'link'][1]};
    const quicks = [];
    for (let h = 0; h < 360; h += 30) {
      const b = make('button', {type: 'button', className: 'pk-q', title: `hue ${h}`});
      b.setAttribute('aria-label', `hue ${h}`);
      b.addEventListener('click', () => { hue.value = h; changed(); });
      quick.append(b);
      quicks.push([b, h]);
    }
    function paint() {
      const {h, c} = Theme.accent(key);
      hue.value = h;
      vivid.value = c;
      const stops = n => Array.from({length: n + 1}, (_, i) => i / n);
      hue.style.background = `linear-gradient(to right, ${stops(12).map(f => Theme.color(L.light, c, f * 360)).join(', ')})`;
      vivid.style.background = `linear-gradient(to right, ${stops(8).map(f => Theme.color(L.light, f * Theme.MAX_C, h)).join(', ')})`;
      for (const [b, qh] of quicks) b.style.background = Theme.color(L.light, c, qh);
      for (const theme of ['light', 'dark']) {
        const chip = box.querySelector('.pk-' + theme);
        chip.style.color = Theme.color(text[theme], c, h);
        chip.firstElementChild.style.background = Theme.color(L[theme], c, h);
      }
    }
    function changed() {
      setPref(key, {h: Number(hue.value), c: Number(vivid.value)});
      Theme.apply();
      paint();
    }
    hue.addEventListener('input', changed);
    vivid.addEventListener('input', changed);
    box.querySelector('.pk-reset').addEventListener('click', () => {
      try { localStorage.removeItem(key); } catch (err) { /* private mode */ }
      Theme.apply();
      paint();
    });
    addEventListener('storage', e => { if (e.key === key) paint(); });
    paint();
  }

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
