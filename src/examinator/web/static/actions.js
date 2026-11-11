'use strict';
// Actions: named things a page can do. A button and a key are two ways to trigger one.
//
//   Actions.register(name, {run(ctx), available(ctx)?, label?})
//   <button data-action="verify">     a click triggers the action (ctx.el is the button)
//   the key map {combo: name}          a key triggers it (ctx.el is null)
//
// `available` decides for both, so a key and its button always agree. The key map is
// data: a preset (left or right hand) plus the grader's own rebindings, per browser in
// localStorage 'keys' = {preset, single, custom: {name: [combo, ...]}}. Each control's
// <kbd> hint is rendered from the active map, so a hint cannot disagree with its key.
//
// Combos: a lower-case character ("a", "/") or a key name ("ArrowLeft", "Escape",
// "Enter"), optionally prefixed "Mod+" (Cmd on a Mac, Ctrl elsewhere) and/or "Alt+".
// Keys without a modifier ("single keys") are ignored while typing in a field, on
// auto-repeat, during composition and with Shift; Escape is the exception (it leaves
// a field). Combos with Mod work while typing.
const Actions = (() => {
  const MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

  // Every action that can carry a key, for the settings page; pages supply `run`.
  const CATALOG = [
    ['prev-student', "previous student"], ['next-student', "next student"],
    ['prev-question', "previous question"], ['next-question', "next question"],
    ['verify', "verify (saves edits)"], ['edit', "edit: first field"], ['save', "save"],
    ['grid', "grid"], ['scan', "scan window"], ['regions', "regions on/off"],
    ['search', "search"], ['leave-field', "leave field"], ['run-preview', "runs: preview"],
    // The scans page has its own group: the same keys mean "earlier / later / confirm" there.
    ['split-confirm', "scans: confirm first page, next", 'scans'], ['split-earlier', "scans: a page earlier", 'scans'],
    ['split-later', "scans: a page later", 'scans'], ['split-back', "scans: previous boundary", 'scans'],
    ['split-next', "scans: next boundary", 'scans'], ['split-drop', "scans: drop this page", 'scans'],
    ['split-turn', "scans: turn this exam 180°", 'scans'], ['split-turn-source', "scans: turn whole scan 90°", 'scans'],
    // Names (the scans page's other tab): the same shapes again; one tab's keys work at a time.
    ['who-confirm', "names: this person, next", 'names'], ['who-prev', "names: previous proposal", 'names'],
    ['who-next', "names: next proposal", 'names'], ['who-back', "names: previous exam", 'names'],
    ['who-skip', "names: next exam", 'names'], ['who-search', "names: type a name or ID", 'names'],
  ];
  const LEFT = {
    'prev-student': ['a', 'ArrowLeft'], 'next-student': ['d', 'ArrowRight'],
    'prev-question': ['w'], 'next-question': ['s'],
    verify: ['v', 'Mod+Enter'], edit: ['e'], save: ['Mod+s'],
    grid: ['q'], scan: ['f'], search: ['/'], 'leave-field': ['Escape'],
    'split-confirm': ['v'], 'split-earlier': ['a', 'ArrowLeft'], 'split-later': ['d', 'ArrowRight'],
    'split-back': ['w'], 'split-next': ['s'], 'split-drop': ['x'],
    'split-turn': ['r'], 'split-turn-source': ['Shift+r'],
    'who-confirm': ['v'], 'who-prev': ['a', 'ArrowLeft'], 'who-next': ['d', 'ArrowRight'],
    'who-back': ['w'], 'who-skip': ['s'], 'who-search': ['e'],
  };
  // The same shapes for the right hand: students and questions on I J K L as on W A S D;
  // grid on the outer top key (P for Q), edit on the inner top key (U for E), scan on the
  // inner stretch (H for F), verify below the index finger (N for V); on the scans page
  // drop is M (for X) and turning is U (for R).
  const MIRROR = {a: 'j', d: 'l', w: 'i', s: 'k', q: 'p', e: 'u', f: 'h', v: 'n', x: 'm', r: 'u'};
  const mirror = c => MIRROR[c] || (c.startsWith('Shift+') && MIRROR[c.slice(6)] ? 'Shift+' + MIRROR[c.slice(6)] : c);
  const RIGHT = Object.fromEntries(Object.entries(LEFT).map(
    ([name, combos]) => [name, combos.map(mirror)]));
  // Actions in different groups never share a page, so they may share keys.
  const group = name => (CATALOG.find(c => c[0] === name) || [])[2] || '';
  const PRESETS = {left: LEFT, right: RIGHT};

  const registry = new Map();

  // --- settings ---
  function settings() {
    let s = null;
    try { s = JSON.parse(localStorage.getItem('keys')); } catch (e) { /* private mode */ }
    s = s && typeof s === 'object' ? s : {};
    return {preset: s.preset === 'right' ? 'right' : 'left', single: s.single !== false,
            custom: s.custom && typeof s.custom === 'object' ? s.custom : {}};
  }
  function saveSettings(s) {
    try { localStorage.setItem('keys', JSON.stringify(s)); } catch (e) { /* private mode */ }
    hints();
  }
  function bindings(name, s) {
    s = s || settings();
    const own = s.custom[name];
    return Array.isArray(own) ? own.map(String) : (PRESETS[s.preset][name] || []);
  }
  // combo -> the actions bound to it (at most one per group)
  function keymap(s) {
    s = s || settings();
    const map = {};
    for (const name of new Set([...Object.keys(PRESETS[s.preset]), ...Object.keys(s.custom)])) {
      for (const c of bindings(name, s)) {
        const names = map[c] || (map[c] = []);
        if (!names.some(n => group(n) === group(name))) names.push(name);
      }
    }
    return map;
  }
  // Bind one combo to an action, taking it away from whatever had it in the same group.
  function rebind(name, combo) {
    const s = settings();
    for (const [other, acts] of Object.entries(keymap(s))) {
      for (const act of acts) {
        if (other === combo && act !== name && group(act) === group(name)) {
          s.custom[act] = bindings(act, s).filter(c => c !== combo);
        }
      }
    }
    s.custom[name] = combo ? [combo] : [];
    saveSettings(s);
  }

  // --- combos ---
  const isSingle = combo => !/^(Mod|Alt)\+/.test(combo);
  function comboOf(e) {
    let key = e.key;
    if (!key || key === 'Dead' || ['Shift', 'Control', 'Meta', 'Alt', 'CapsLock'].includes(key)) return null;
    if (e.altKey && /^Key[A-Z]$/.test(e.code)) key = e.code.slice(3);    // Option+letter on a Mac
    const letter = key.length === 1 && /[a-z]/i.test(key);
    if (key.length === 1) key = key.toLowerCase();
    if (key === ' ') key = 'Space';
    return (e.metaKey || e.ctrlKey ? 'Mod+' : '') + (e.altKey ? 'Alt+' : '') +
      (e.shiftKey && (letter || key.length > 1) ? 'Shift+' : '') + key;
  }
  const NAMES = {ArrowLeft: '←', ArrowRight: '→', ArrowUp: '↑', ArrowDown: '↓', Escape: 'Esc',
                 Enter: '↵', Space: 'Space', Backspace: '⌫', Tab: 'Tab'};
  function display(combo) {
    const parts = combo.split('+'), key = parts.pop() || '+';
    const mods = parts.map(m => (m === 'Mod' ? (MAC ? '⌘' : 'Ctrl+') : m === 'Alt' ? (MAC ? '⌥' : 'Alt+') : MAC ? '⇧' : 'Shift+'));
    return mods.join('') + (NAMES[key] || key.toUpperCase());
  }

  const typing = el => !!el?.closest?.('input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"], [role="combobox"], [role="slider"], [role="listbox"]');

  // --- registry ---
  function register(name, spec) { registry.set(name, spec); }
  const has = name => registry.has(name);
  function trigger(name, ctx) {
    const a = registry.get(name);
    ctx = {source: 'code', el: null, event: null, target: document.activeElement, ...ctx};
    if (!a || (a.available && !a.available(ctx))) return false;
    a.run(ctx);
    return true;
  }
  const available = (name, ctx) => {
    const a = registry.get(name);
    return !!a && (!a.available || !!a.available({source: 'code', el: null, target: document.activeElement, ...ctx}));
  };

  // A key: listened for on window, after the page's own handlers (a handler that
  // takes the key, like arrows while editing regions, calls preventDefault).
  addEventListener('keydown', e => {
    if (e.defaultPrevented || e.isComposing || e.keyCode === 229) return;
    const combo = comboOf(e);
    if (!combo) return;
    const s = settings();
    const ctx = {source: 'key', el: null, event: e, target: e.target};
    const name = (keymap(s)[combo] || []).find(n => registry.has(n) && (!registry.get(n).available || registry.get(n).available(ctx)));
    if (!name) return;
    if (isSingle(combo) && combo !== 'Escape') {
      if (!s.single || e.repeat || typing(e.target)) return;
    }
    const a = registry.get(name);
    e.preventDefault();
    a.run(ctx);
  });

  // A button (not a link: links keep their own behaviour, so a modified click opens a tab).
  document.addEventListener('click', e => {
    const el = e.target.closest?.('[data-action]');
    if (!el || el.tagName === 'A' || !registry.has(el.dataset.action)) return;
    e.preventDefault();
    trigger(el.dataset.action, {source: 'button', el, event: e, target: el});
  });

  // Hints: each [data-action] control gets a <kbd> with its first active key, and its
  // title lists them all. data-hint="none" keeps a control without a <kbd>.
  function hints(root) {
    const s = settings();
    for (const el of (root || document).querySelectorAll('[data-action]')) {
      const combos = bindings(el.dataset.action, s).filter(c => s.single || !isSingle(c) || c === 'Escape');
      if (el.dataset.baseTitle === undefined) el.dataset.baseTitle = el.getAttribute('title') || '';
      const base = el.dataset.baseTitle;
      const keys = combos.map(display).join(', ');
      if (base || keys) el.title = [base, keys].filter(Boolean).join(' · ');
      else el.removeAttribute('title');
      let kbd = el.querySelector(':scope > kbd.hint');
      if (el.dataset.hint === 'none' || !combos.length) { kbd?.remove(); continue; }
      if (!kbd) {
        kbd = document.createElement('kbd');
        const before = el.dataset.hint === 'before';
        kbd.className = before ? 'hint before' : 'hint';
        if (before) el.prepend(kbd); else el.append(kbd);
      }
      kbd.textContent = display(combos[0]);
    }
  }
  document.addEventListener('DOMContentLoaded', () => hints());
  addEventListener('storage', e => { if (e.key === 'keys') hints(); });

  // Actions every page has.
  register('leave-field', {available: ctx => typing(ctx.target), run: ctx => ctx.target.blur()});
  register('grid', {
    available: () => !!document.getElementById('home') && !document.getElementById('grid'),
    run: () => document.getElementById('home').click()});

  return {register, has, trigger, available, hints, settings, saveSettings, bindings, keymap, rebind,
          comboOf, display, isSingle, typing, CATALOG, PRESETS};
})();
