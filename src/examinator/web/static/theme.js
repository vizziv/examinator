'use strict';
// Loaded synchronously in <head> so the saved theme and accents apply before first paint.
// Per browser (localStorage): 'theme' = "system" (default) | "light" | "dark";
// 'accent' and 'accent2' = "#rrggbb" (absent: the stylesheet's defaults).
const Theme = (() => {
  const DEFAULTS = {accent: '#187367', accent2: '#5b5fc7'};
  const read = (key, fallback) => {
    try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch (e) { return fallback; }
  };
  const dark = matchMedia('(prefers-color-scheme: dark)');

  function apply() {
    const html = document.documentElement;
    const mode = read('theme', 'system');
    html.dataset.theme = mode === 'dark' || (mode !== 'light' && dark.matches) ? 'dark' : 'light';
    for (const key of Object.keys(DEFAULTS)) {
      const value = read(key, null);
      if (typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value)) html.style.setProperty('--' + key, value);
      else html.style.removeProperty('--' + key);
    }
  }
  apply();
  dark.addEventListener('change', apply);
  addEventListener('storage', e => { if (['theme', ...Object.keys(DEFAULTS)].includes(e.key)) apply(); });
  return {apply, DEFAULTS};
})();
