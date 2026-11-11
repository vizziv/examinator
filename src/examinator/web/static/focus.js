'use strict';
// The focus: a selection {facetId: [values] | "search"} narrowing the submissions.
// Mirrors core/focus.py: OR within one facet's values, AND across facets; counts
// for one facet are over submissions matching every other facet. Kept per browser
// (localStorage) and expressible in the URL (?focus=...) so a filter can be shared.
const Focus = (() => {
  const firstLine = v => v.split('\n', 1)[0].toLowerCase();

  function active(table, sel) {
    const out = {};
    for (const [fid, want] of Object.entries(sel || {})) {
      if (!(fid in table)) continue;
      if (typeof want === 'string') { if (want.trim()) out[fid] = want.trim().toLowerCase(); }
      else if (Array.isArray(want) && want.length) out[fid] = want.map(String);
    }
    return out;
  }
  function hit(values, want) {
    if (typeof want === 'string') return values.some(v => firstLine(v).includes(want));
    return values.some(v => want.includes(v));
  }
  function matches(table, sel, sub, skip) {
    for (const [fid, want] of Object.entries(active(table, sel))) {
      if (fid !== skip && !hit(table[fid][sub] || [], want)) return false;
    }
    return true;
  }
  const focused = (table, sel, subs) => subs.filter(s => matches(table, sel, s));
  function counts(table, sel, fid, subs) {
    const out = {}, col = table[fid] || {};
    for (const s of subs) {
      if (!matches(table, sel, s, fid)) continue;
      for (const v of col[s] || []) out[v] = (out[v] || 0) + 1;
    }
    return out;
  }

  // --- storage: the browser's focus, adopted from the URL when a link carries one ---
  function clean(sel) {
    const out = {};
    if (!sel || typeof sel !== 'object' || Array.isArray(sel)) return out;
    for (const [fid, want] of Object.entries(sel)) {
      if (typeof want === 'string' ? want.trim() : Array.isArray(want) && want.length) {
        out[fid] = typeof want === 'string' ? want : want.map(String);
      }
    }
    return out;
  }
  function fromUrl() {
    const text = new URLSearchParams(location.search).get('focus');
    if (text === null) return null;
    try { return clean(JSON.parse(text)); } catch (e) { return {}; }
  }
  function load() {
    const given = fromUrl();
    if (given !== null) { save(given); return given; }
    try { return clean(JSON.parse(localStorage.getItem('focus'))); } catch (e) { return {}; }
  }
  function save(sel) {
    try { localStorage.setItem('focus', JSON.stringify(clean(sel))); } catch (e) { /* private mode */ }
  }
  function query(sel) {
    const c = clean(sel);
    return Object.keys(c).length ? '?focus=' + encodeURIComponent(JSON.stringify(c)) : '';
  }
  const size = sel => Object.keys(clean(sel)).length;

  return {active, matches, focused, counts, clean, fromUrl, load, save, query, size};
})();
if (typeof module !== 'undefined') module.exports = Focus;
