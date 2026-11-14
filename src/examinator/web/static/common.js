'use strict';
// Shared by every page: token-carrying requests, job polling, preferences, crops.
const meta = name => document.querySelector(`meta[name="${name}"]`)?.content || '';
const ROOT = meta('root');

// Reading: a GET, answered as {status, ok, body}.
const request = path => Changes.get(path);

// A line where a person works (an entry's message, an editor's panel line, a step's status,
// the header): one message at a time, about one state of what is shown there. A change sent
// with this line owns it from the moment it goes: it clears what was there, shows '…' while
// the answer is slow, and leaves its outcome (nothing when accepted; its `done` may say more).
//   Line(element or id)               the same line for the same place
//   line.say(text or [nodes], tone)   tone 'bad': a problem (red); else plain
function Line(where) {
  const all = typeof where === 'string' ? Line.ids : Line.els;
  if (all.has(where)) return all.get(where);
  let owner = null;
  function say(what, tone, by = null) {
    const e = typeof where === 'string' ? document.getElementById(where) : where;
    owner = by;
    if (!e) return;
    e.classList.add('line');
    e.replaceChildren(...[].concat(what ?? '').filter(x => x !== ''));
    e.classList.toggle('bad', tone === 'bad' && e.childNodes.length > 0);
    e.hidden = !e.childNodes.length;
  }
  const line = {say, mine: by => owner === by};
  all.set(where, line);
  return line;
}
Line.ids = new Map();
Line.els = new WeakMap();

// Changes: the one way a page changes anything (DESIGN.md, Changes).
//   Changes.send(subject, path, body, {done(answer), redraw(current), line, ends, long})
// What a change says is fixed when it is pressed: the things it names (by identity, never by
// position) and the values the person gave. The revisions it is made against are read when it
// is sent: `body` is a function, called then, that reads them from what the page last drew.
// Changes to one subject go one at a time, in press order, each sent once the one before it
// was answered and its `done` has drawn the answer. A change that `ends` its subject (the
// page moves on) drops what was pressed behind it on that subject; so does one that does not
// go through, which says why at its `line` (else the header): the server's reason, then, for
// a stale one (409), what `redraw(current)` did with the draft, if it returns that.
//   Changes.query(path, body)      a POST that changes nothing (a preview)
//   Changes.busy()                 a long change (a model run, Accept) is under way
const Changes = (() => {
  const chains = new Map();     // subject -> {tail, n, over}
  let long = 0;
  async function call(path, data) {
    const init = data === undefined ? {} : {
      method: 'POST', body: JSON.stringify(data),
      headers: {'Content-Type': 'application/json', 'X-Token': meta('token')}};
    let res, body = {};
    try {
      res = await fetch(ROOT + path, init);
      body = await res.json();
    } catch (e) { /* network error (or, from a proxy, a non-JSON reply) */ }
    return {status: res ? res.status : 0, ok: !!res && res.ok, body: body || {}};
  }
  function busy(d) {
    long += d;
    if (typeof Actions !== 'undefined') Actions.enable(document);     // a top-level const, not on window
  }
  function send(subject, path, body, {done, redraw, line, ends, long: isLong} = {}) {
    const c = chains.get(subject) || {tail: Promise.resolve(), n: 0, over: false};
    chains.set(subject, c);
    c.n++;
    if (isLong) busy(1);
    const me = {}, at = line || Line('flash');
    const mine = c.tail.then(async () => {
      if (c.over) return null;
      at.say('', '', me);
      const slow = setTimeout(() => { if (at.mine(me)) at.say('…', '', me); }, 250);
      const r = await call(path, typeof body === 'function' ? body() : body);
      clearTimeout(slow);
      if (!r.ok) {
        c.over = true;
        const said = r.status === 409 && redraw ? await redraw(r.body.current || {}) : null;
        const why = r.status ? r.body.error || `failed (${r.status})` : "no connection: not saved";
        at.say([why, said].filter(Boolean).join(' · '), 'bad', me);
        return null;
      }
      if (at.mine(me)) at.say('');
      if (ends) c.over = true;
      if (done) await done(r.body);
      return r.body;
    }).catch(err => { c.over = true; console.error(err); at.say(`failed: ${(err && err.message) || err}`, 'bad', me); return null; })
      .finally(() => {
        if (isLong) busy(-1);
        if (--c.n === 0) c.over = false;
      });
    c.tail = mine;
    return mine;
  }
  return {send, query: (path, body) => call(path, body || {}), get: path => call(path), busy: () => long > 0,
          // a stale refusal over a draft: what is there now, and that the draft stays
          kept: now => `yours kept over ${now}: save again to overwrite`};
})();
// A value in a message: short.
const brief = v => {
  const s = typeof v === 'string' ? v : JSON.stringify(v) ?? '';
  return s === '' || s === 'null' ? 'none' : s.length > 60 ? s.slice(0, 60) + '…' : s;
};

// Exam data at a scope (an override): set `value`, or with `clear` drop it, at `path` for
// `scope` ('' every submission, 'source:<id>', 'submission:<id>'). `rev()` reads the
// revision the page last drew for it, when the change is sent; `take(current)` takes what is
// saved now, after a stale refusal, so that saving again overwrites knowingly. `sub`: the
// submission whose recompute goes first.
function override({path, scope = '', value, clear, sub, rev, take}, {line, done}) {
  return Changes.send(`override:${scope}:${path}`, clear ? '/exam/clear' : '/exam/set',
    () => ({path, scope, sub, rev: rev(), ...(clear ? {} : {value})}),
    {line, done, redraw: cur => { take(cur); return clear ? `now ${brief(cur.value)}` : Changes.kept(brief(cur.value)); }});
}

// A background job, polled until it ends: its last state ({state: 'done' | 'failed' |
// 'lost', ...}; 'lost' when the server no longer knows it). `each(job)` sees every poll;
// returning true stops waiting.
async function waitJob(id, each) {
  for (;;) {
    const r = await request('/jobs/' + encodeURIComponent(id));
    const job = r.ok ? r.body : {state: 'lost', error: r.body.error || "no connection"};
    if ((each && each(job) === true) || job.state !== 'running') return job;
    await new Promise(go => setTimeout(go, 600));
  }
}

// What the server drew, drawn again: `fresh` takes the place of what is under `old`, but the
// inputs under `old` that hold a draft (a value other than the one they were drawn with) or
// the focus stay, each in place of its counterpart in `fresh` (the same `ident`), taking its
// drawing: the value and revision the server has now, so a draft is a draft over that. An
// input is one record: what it was drawn with and the revision of its row (the nearest
// `data-rev`) always change together. Answers the drafts kept over a row that changed:
// [{el, now: the value drawn now}].
const isDraft = el => (/^(checkbox|radio)$/.test(el.type) ? el.checked !== el.defaultChecked : el.value !== el.defaultValue);
function keepDrafts(old, fresh, ident) {
  const kept = [], there = new Map([...fresh.querySelectorAll('input, textarea')].map(i => [ident(i), i]));
  for (const el of old.querySelectorAll('input, textarea')) {
    const to = there.get(ident(el)), draft = isDraft(el);
    if (!to || (!draft && el !== document.activeElement)) continue;
    const rev = () => el.closest('[data-rev]')?.dataset.rev;
    const before = rev();
    for (const {name, value} of [...to.attributes]) if (name !== 'value') el.setAttribute(name, value);
    if (/^(checkbox|radio)$/.test(el.type)) {
      el.defaultChecked = to.defaultChecked;
      if (!draft) el.checked = to.checked;
    } else {
      el.defaultValue = to.defaultValue;
      if (!draft) el.value = to.defaultValue;
    }
    to.replaceWith(el);
    if (draft && rev() !== before) kept.push({el, now: to.defaultValue});
  }
  return kept;
}

function pref(key, fallback) {
  try { return JSON.parse(localStorage.getItem(key)) ?? fallback; } catch (e) { return fallback; }
}
function setPref(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* private mode */ }
}
function session(key, fallback) {
  try { return JSON.parse(sessionStorage.getItem(key)) ?? fallback; } catch (e) { return fallback; }
}

// A message in the header: a problem (red) stays until the next one there (a change sent
// without a line of its own clears it); with tone 'ok' ("copied") it goes after a while.
function flash(text, tone) {
  const line = Line('flash'), me = {};
  line.say(text, tone === 'ok' ? '' : 'bad', me);
  if (tone === 'ok') setTimeout(() => { if (line.mine(me)) line.say(''); }, 8000);
}

// Pages rendered ahead. A key that moves to another page loads it again (one subject per
// page), so the pages a key can reach from here are prerendered where the browser can
// (Chromium's speculation rules; elsewhere nothing happens): `urls` at once, `hover` (a
// selector of links) on hovering. A new list replaces the last one.
function ahead(urls, hover) {
  if (!HTMLScriptElement.supports?.('speculationrules')) return;
  const rules = [{source: 'list', urls: [...new Set(urls.filter(Boolean))], eagerness: 'immediate'}];
  if (hover) rules.push({where: {selector_matches: hover}, eagerness: 'moderate'});
  document.getElementById('ahead')?.remove();
  const s = document.createElement('script');
  s.type = 'speculationrules';
  s.id = 'ahead';
  s.textContent = JSON.stringify({prerender: rules});
  document.head.append(s);
}
// A prerendered page runs before it is shown, and may never be: what it writes to state
// shared with other pages (sessionStorage, a draft, the grid's cursor) waits until it is.
// `fn(late)`: `late` when the page was shown later than it began to load (it was rendered
// ahead, even if shown before its scripts ran), so that it draws what is there now.
function whenShown(fn) {
  const late = () => (performance.getEntriesByType?.('navigation')[0]?.activationStart || 0) > 0;
  if (document.prerendering) document.addEventListener('prerenderingchange', () => fn(late()), {once: true});
  else fn(late());
}

const $ = id => document.getElementById(id);
function make(tag, props, ...children) {
  const el = document.createElement(tag);
  Object.assign(el, props || {});
  el.append(...children);
  return el;
}
// A pill: cls '' (information, finished), 'unv' (waits on a person) or 'bad' (a problem).
const tag = (cls, text, title) => make('span', {className: 'tag ' + (cls || ''), textContent: text, title: title || ''});
// The first index after `from` (wrapping around) whose item is open, else the list's length.
function nextOpen(list, from, open) {
  for (let j = from + 1; j < list.length; j++) if (open(list[j])) return j;
  for (let j = 0; j <= from && j < list.length; j++) if (open(list[j])) return j;
  return list.length;
}

// A crop is a window onto the cached full-page image: <span class="crop" data-src data-box="l,t,r,b">.
// It is drawn at the region's own size times the grader's crop scale (1 shows a page 1000 px
// wide), or at its own data-width, and never shrunk to fit. In the fields panel a crop wider
// than the panel scrolls sideways inside a box the panel's width, and a multi-line value's crop
// sits beside the value only when both fit (the value keeps TEXT_MIN), else above it.
const CROP_SCALE = 1.2, CROP_MIN = 0.4, CROP_MAX = 3, TEXT_MIN = 16;   // em
const cropScale = () => Math.min(CROP_MAX, Math.max(CROP_MIN, Number(pref('cropScale', CROP_SCALE)) || CROP_SCALE));
function setCropScale(value) {
  setPref('cropScale', Math.min(CROP_MAX, Math.max(CROP_MIN, value)));
  showCropScale();
  drawCrops();
}
function showCropScale() {
  const size = document.getElementById('crop-size'), n = document.getElementById('crop-n');
  if (size) { size.value = cropScale(); fillRange(size); }
  if (n) n.textContent = cropScale().toFixed(2).replace(/0$/, '') + '×';
}
function drawCrop(crop) {
  const [l, t, r, b] = crop.dataset.box.split(',').map(Number);
  let view = crop.firstElementChild, img = view?.firstElementChild;
  if (!img) {
    view = document.createElement('span');
    img = new Image();
    img.alt = '';
    img.addEventListener('load', () => drawCrop(crop));
    img.src = crop.dataset.src;
    view.append(img);
    crop.append(view);
  }
  if (r <= l || b <= t) return;
  const width = Number(crop.dataset.width) || (r - l) * 1000 * cropScale();
  // Until the image is here, its box takes a letter page's proportions, so the page is
  // laid out once and the picture fills in (drawn again when it loads).
  const w = img.naturalWidth || 17, h = img.naturalWidth ? img.naturalHeight : 22;
  const k = width / ((r - l) * w);
  img.style.width = w * k + 'px';
  img.style.marginLeft = -l * w * k + 'px';
  img.style.marginTop = -t * h * k + 'px';
  view.style.width = width + 'px';
  view.style.height = (b - t) * h * k + 'px';
  const box = width + crop.offsetWidth - crop.clientWidth, room = crop.closest('.fields')?.clientWidth;
  const field = crop.closest('.field.multiline');
  if (field && room) {
    const style = getComputedStyle(field);
    field.classList.toggle('above', box + parseFloat(style.columnGap) + TEXT_MIN * parseFloat(style.fontSize) > room);
  }
  crop.style.maxWidth = room ? room + 'px' : '';
}
function drawCrops(root) {
  for (const crop of (root || document).querySelectorAll('.crop')) drawCrop(crop);
}

// The header spans the window, not the page, so it stays put when a wide page scrolls sideways.
function sizeHeader() { document.documentElement.style.setProperty('--vw', document.documentElement.clientWidth + 'px'); }
addEventListener('resize', sizeHeader);
if (window.ResizeObserver) new ResizeObserver(sizeHeader).observe(document.documentElement);
// A range's filled part (WebKit has no progress pseudo-element): --fill on the input.
function fillRange(el) {
  const min = Number(el.min || 0), max = Number(el.max || 100);
  el.style.setProperty('--fill', ((Number(el.value) - min) / (max - min || 1)) * 100 + '%');
}
document.addEventListener('input', e => { if (e.target.matches?.('input[type=range]')) fillRange(e.target); });
// A choice made in a select gives the keys back (single keys are off while a field has focus).
document.addEventListener('change', e => { if (e.target.matches?.('select')) e.target.blur(); });
document.addEventListener('DOMContentLoaded', () => {
  sizeHeader();
  document.querySelectorAll('input[type=range]').forEach(fillRange);
  drawCrops();
  const size = document.getElementById('crop-size');
  if (size) {
    showCropScale();
    size.addEventListener('input', () => setCropScale(Number(size.value)));
  }
});
