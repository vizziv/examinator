'use strict';
// Loaded synchronously in <head> so the saved theme and accents apply before first paint.
// Per browser (localStorage):
//   'theme'               "system" (default) | "light" | "dark"
//   'accent', 'accent2'   {h, c}: an OKLCH hue (degrees) and chroma. Lightness is not
//                         chosen: each theme derives it per role (fill, link, ring…),
//                         so one choice reads well on light and on dark surfaces.
// Each role's colour is set on <html> as a custom property; chroma is capped to what
// sRGB can show at that lightness and hue, so no colour clips. Without oklch() support
// the same colours are set as hex.
const Theme = (() => {
  const DEFAULTS = {accent: {h: 194, c: 0.11}, accent2: {h: 68, c: 0.15}};
  const MAX_C = 0.25;
  // Lightness per role and theme. The soft and line shades derive in CSS (color-mix).
  const ROLES = {
    light: {accent: ['accent', 0.52], 'accent-hover': ['accent', 0.45], link: ['accent', 0.47], ring: ['accent', 0.55],
            accent2: ['accent2', 0.70], 'accent2-text': ['accent2', 0.50]},
    dark: {accent: ['accent', 0.56], 'accent-hover': ['accent', 0.62], link: ['accent', 0.78], ring: ['accent', 0.74],
           accent2: ['accent2', 0.74], 'accent2-text': ['accent2', 0.82]},
  };

  // --- OKLCH -> sRGB (Björn Ottosson's OKLab) ---
  function linear(L, c, h) {
    const a = c * Math.cos(h * Math.PI / 180), b = c * Math.sin(h * Math.PI / 180);
    const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
    const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
    const s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3;
    return [4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
            -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
            -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s];
  }
  const inGamut = rgb => rgb.every(v => v >= -1e-4 && v <= 1 + 1e-4);
  function maxChroma(L, h) {
    let lo = 0, hi = 0.4;
    for (let i = 0; i < 24; i++) { const mid = (lo + hi) / 2; if (inGamut(linear(L, mid, h))) lo = mid; else hi = mid; }
    return lo * 0.98;
  }
  function hex(L, c, h) {
    const g = v => { v = Math.min(1, Math.max(0, v)); return v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055; };
    return '#' + linear(L, c, h).map(v => Math.round(g(v) * 255).toString(16).padStart(2, '0')).join('');
  }
  const OKLCH = typeof CSS !== 'undefined' && CSS.supports && CSS.supports('color', 'oklch(0.5 0.1 200)');
  function color(L, c, h) {
    c = Math.min(c, maxChroma(L, h));
    return OKLCH ? `oklch(${L} ${c.toFixed(4)} ${h})` : hex(L, c, h);
  }

  // --- storage ---
  function read(key, fallback) {
    try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch (e) { return fallback; }
  }
  function accent(key) {
    const v = read(key, null);       // an older hex value (a string) is dropped: the default applies
    if (!v || typeof v !== 'object' || !isFinite(v.h) || !isFinite(v.c)) return {...DEFAULTS[key]};
    return {h: ((Number(v.h) % 360) + 360) % 360, c: Math.min(MAX_C, Math.max(0, Number(v.c)))};
  }

  const dark = matchMedia('(prefers-color-scheme: dark)');
  function apply() {
    const html = document.documentElement;
    const mode = read('theme', 'system');
    const theme = mode === 'dark' || (mode !== 'light' && dark.matches) ? 'dark' : 'light';
    html.dataset.theme = theme;
    const picks = {accent: accent('accent'), accent2: accent('accent2')};
    for (const [role, [key, L]] of Object.entries(ROLES[theme])) {
      html.style.setProperty('--' + role, color(L, picks[key].c, picks[key].h));
    }
  }
  apply();
  dark.addEventListener('change', apply);
  addEventListener('storage', e => { if (['theme', 'accent', 'accent2'].includes(e.key)) apply(); });
  return {apply, accent, color, maxChroma, DEFAULTS, ROLES, MAX_C};
})();
