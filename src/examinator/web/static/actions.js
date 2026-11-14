'use strict';
// Actions: named things a page can do. A button and a key are two ways to trigger one.
//
//   Actions.register(name, {run(ctx), available(ctx)?, repeat?})
//   Actions.gate(group, test)         every action named '<group>-…' waits on test() too (a tab shown, nothing long running)
//   <button data-action="verify">     a click triggers the action (ctx.el is the button)
//   the key map {combo: name}          a key triggers it (ctx.el is null)
//
// `available` decides for both, so a key and its button always agree. The key map is
// data: the defaults (KEYS) plus the grader's own rebindings, per browser in
// localStorage 'keys' = {single, custom: {name: [combo, ...]}}. Each control's
// <kbd> hint is rendered from the active map, so a hint cannot disagree with its key.
//
// Combos: a lower-case character ("a", "/") or a key name ("ArrowLeft", "Escape",
// "Enter"), optionally prefixed "Mod+" (Cmd on a Mac, Ctrl elsewhere), "Alt+" and/or
// "Shift+" ("Shift+v"). Combos without Mod or Alt ("single keys", Shift ones included)
// are ignored while typing in a field and during composition; Escape is the exception (it
// leaves a field). Combos with Mod work while typing.
//
// A key held down acts once: its auto-repeat never triggers a change, and never reaches the
// browser either (a held Cmd+S, or a held E that focused a field and would type into it).
// Moves (a cursor, a page looked at) opt in to repeating: `repeat: true`, or a function of
// ctx. A held Enter or Space never presses a control again. A click acts on what was under
// the pointer when its double click began: the second click of a double click that lands on
// something drawn since the first (a list redrawn by the first click's answer) is dropped.
const Actions = (() => {
  const MAC = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

  // Every action that can carry a key, for the settings page; pages supply `run`.
  // The third element is the group: one page's keys; groups never share a page, so they may share keys.
  const CATALOG = [
    ['prev-student', "previous submission"], ['next-student', "next submission"],
    ['prev-question', "previous question"], ['next-question', "next question"],
    ['verify-next', "verify, next submission"], ['verify', "verify (saves edits)"],
    ['grade-right', "grade: correct"], ['grade-wrong', "grade: incorrect"], ['grade-blank', "grade: blank"],
    ['grade-number', "grade: number box"],
    ['edit', "edit: first field"], ['save', "save"],
    ['grid', "grid"], ['regions', "regions on/off"], ['print-area', "print area on/off"],
    ['search', "search"], ['leave-field', "leave field"],
    ['cell-left', "grid: cell left", 'grid'], ['cell-right', "grid: cell right", 'grid'],
    ['cell-up', "grid: cell up", 'grid'], ['cell-down', "grid: cell down", 'grid'],
    ['cell-open', "grid: open cell", 'grid'],
    // Split (the scans page): first pages are a horizontal strip, submissions go up and down.
    ['split-verify', "split: verify the submission, next", 'split'], ['split-earlier', "split: a page earlier", 'split'],
    ['split-later', "split: a page later", 'split'], ['split-back', "split: previous submission", 'split'],
    ['split-next', "split: next submission", 'split'], ['split-drop', "split: drop this page", 'split'],
    ['split-cut', "split: new submission here", 'split'],
    ['split-extra', "split: page extra or counted", 'split'], ['split-missing', "split: missing page before this", 'split'],
    ['split-unmissing', "split: not missing", 'split'],
    ['split-shift-earlier', "split: move page earlier", 'split'], ['split-shift-later', "split: move page later", 'split'],
    ['split-turn-page', "split: turn page 90°", 'split'], ['split-turn', "split: turn submission 90°", 'split'],
    ['split-turn-source', "split: turn whole scan 90°", 'split'],
    ['split-undo', "split: undo", 'split'],
    ['split-guess', "split: guess again", 'split'], ['split-read', "split: read pages", 'split'],
    ['split-accept', "split: accept", 'split'],
    // Names (the scans page's other tab): submissions left and right, proposals up and down.
    ['names-verify', "names: verify, next", 'names'], ['names-back', "names: previous submission", 'names'],
    ['names-skip', "names: next submission", 'names'], ['names-up', "names: previous proposal", 'names'],
    ['names-down', "names: next proposal", 'names'], ['names-type', "names: type name", 'names'],
    ['names-read', "names: read name boxes", 'names'], ['names-boxes', "names: draw the name boxes", 'names'],
  ];
  const KEYS = {
    'prev-student': ['a', 'ArrowLeft'], 'next-student': ['d', 'ArrowRight'],
    'prev-question': ['w'], 'next-question': ['s'],
    'verify-next': ['v'], verify: ['Mod+Enter'], edit: ['e'], save: ['Mod+s'],
    'grade-right': ['c'], 'grade-wrong': ['x'], 'grade-blank': ['z'], 'grade-number': ['g'],
    grid: ['q'], search: ['/'], 'leave-field': ['Escape'],
    'cell-left': ['a', 'ArrowLeft'], 'cell-right': ['d', 'ArrowRight'], 'cell-up': ['w', 'ArrowUp'],
    'cell-down': ['s', 'ArrowDown'], 'cell-open': ['v', 'Enter'],
    'split-verify': ['v'], 'split-earlier': ['a', 'ArrowLeft'], 'split-later': ['d', 'ArrowRight'],
    'split-back': ['w'], 'split-next': ['s'], 'split-drop': ['x'],
    'split-cut': ['Shift+v'], 'split-extra': ['e'], 'split-missing': ['g'], 'split-unmissing': ['Shift+g'],
    'split-shift-earlier': ['Shift+a'], 'split-shift-later': ['Shift+d'], 'split-turn-page': ['r'], 'split-turn': ['Shift+r'], 'split-undo': ['Mod+z'],
    'names-verify': ['v'], 'names-back': ['a', 'ArrowLeft'], 'names-skip': ['d', 'ArrowRight'],
    'names-up': ['w', 'ArrowUp'], 'names-down': ['s', 'ArrowDown'], 'names-type': ['e'],
    'names-boxes': ['b'],
  };
  const group = name => (CATALOG.find(c => c[0] === name) || [])[2] || '';

  const registry = new Map();

  // --- settings ---
  function settings() {
    let s = null;
    try { s = JSON.parse(localStorage.getItem('keys')); } catch (e) { /* private mode */ }
    s = s && typeof s === 'object' ? s : {};
    return {single: s.single !== false, custom: s.custom && typeof s.custom === 'object' ? s.custom : {}};
  }
  function saveSettings(s) {
    try { localStorage.setItem('keys', JSON.stringify(s)); } catch (e) { /* private mode */ }
    hints();
  }
  function bindings(name, s) {
    s = s || settings();
    const own = s.custom[name];
    return Array.isArray(own) ? own.map(String) : (KEYS[name] || []);
  }
  // combo -> the actions bound to it (at most one per group)
  function keymap(s) {
    s = s || settings();
    const map = {};
    for (const name of new Set([...Object.keys(KEYS), ...Object.keys(s.custom)])) {
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
  const gates = new Map();       // group -> test
  function register(name, spec) { registry.set(name, spec); }
  const gate = (group, test) => gates.set(group, test);
  const has = name => registry.has(name);
  // Whether an action can run now: its group's gate, then its own `available`.
  function can(name, ctx) {
    const a = registry.get(name), g = gates.get(name.split('-')[0]);
    return !!a && (!g || !!g()) && (!a.available || !!a.available(ctx));
  }
  function trigger(name, ctx) {
    ctx = {el: null, event: null, target: document.activeElement, ...ctx};
    if (!can(name, ctx)) return false;
    registry.get(name).run(ctx);
    return true;
  }

  // Held keys and stale clicks, before any page handler sees them.
  const control = el => el?.closest?.('button, [data-action], [role="option"], [role="tab"], input[type="checkbox"], input[type="radio"]');
  const drop = e => { e.preventDefault(); e.stopImmediatePropagation(); };
  addEventListener('keydown', e => {
    // Enter in a one-line field takes it (a grade, a name): held, it does not again
    if (e.repeat && ((e.key === 'Enter' && (control(e.target) || e.target.matches?.('input'))) || (e.key === ' ' && control(e.target)))) drop(e);
  }, true);
  let first = null, stale = false;        // what the first click of this run of clicks pressed
  addEventListener('mousedown', e => {
    const el = control(e.target);
    if (e.detail <= 1) first = el;
    stale = e.detail > 1 && !!el && el !== first;
    if (stale) drop(e);
  }, true);
  addEventListener('click', e => { if (stale && e.detail > 1) drop(e); }, true);

  // A key: listened for on window, after the page's own handlers (a handler that
  // takes the key, like arrows while editing regions, calls preventDefault). A key pressed
  // before this page is first on screen is dropped: a page rendered ahead takes keys as
  // soon as it is shown, a frame before it is drawn, and a key acts on what is drawn.
  let drawn = false;
  whenShown(() => requestAnimationFrame(() => setTimeout(() => { drawn = true; })));
  let held = null;          // {combo, name}: the key that triggered an action and is still down
  addEventListener('keyup', () => { held = null; });
  addEventListener('blur', () => { held = null; });
  addEventListener('keydown', e => {
    if (!drawn) return e.preventDefault();
    if (e.defaultPrevented || e.isComposing || e.keyCode === 229) return;
    const combo = comboOf(e);
    if (!combo) return;
    const ctx = {el: null, event: e, target: e.target};
    if (e.repeat) {
      if (!held || held.combo !== combo) return;      // held since before this page, or typing: the browser's
      e.preventDefault();
      const a = registry.get(held.name);
      const again = typeof a.repeat === 'function' ? a.repeat(ctx) : a.repeat;
      if (again && can(held.name, ctx)) a.run(ctx);
      return;
    }
    held = null;
    const s = settings();
    const name = (keymap(s)[combo] || []).find(n => can(n, ctx));
    if (!name) return;
    if (isSingle(combo) && combo !== 'Escape') {
      if (!s.single || typing(e.target)) return;
    }
    const a = registry.get(name);
    e.preventDefault();
    held = {combo, name};
    a.run(ctx);
  });

  // A button (not a link: links keep their own behaviour, so a modified click opens a tab).
  document.addEventListener('click', e => {
    const el = e.target.closest?.('[data-action]');
    if (!el || el.tagName === 'A' || !registry.has(el.dataset.action)) return;
    e.preventDefault();
    trigger(el.dataset.action, {el, event: e, target: el});
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
  // Buttons under `root` whose action cannot run now are disabled, so a key that would do
  // nothing looks it. For pages that call this whenever their state changes.
  function enable(root) {
    for (const el of (root || document).querySelectorAll('button[data-action]')) {
      if (registry.has(el.dataset.action)) el.disabled = !can(el.dataset.action, {el, target: el});
    }
  }
  document.addEventListener('DOMContentLoaded', () => hints());
  addEventListener('storage', e => { if (e.key === 'keys') hints(); });

  // Actions every page has.
  register('leave-field', {available: ctx => typing(ctx.target), run: ctx => ctx.target.blur()});
  register('grid', {
    available: () => !!$('home') && !$('grid'),
    run: () => $('home').click()});

  return {register, gate, has, trigger, hints, enable, settings, saveSettings, bindings, rebind,
          comboOf, display, typing, CATALOG};
})();
