'use strict';
// Shared by every page: token-carrying requests, job polling, preferences, crops.
const meta = name => document.querySelector(`meta[name="${name}"]`)?.content || '';
const ROOT = meta('root');

// Reading: a GET, answered as {status, ok, body}.
const request = path => Changes.get(path);

// A line where a person works (an entry's message, an editor's panel line, a step's status):
// one message at a time, about one state of what is shown there. A change sent with this
// line owns it from the moment it goes: it clears what was there (about the state it is
// replacing), shows '…' while the answer is slow, and leaves its outcome: nothing when
// accepted (its `done` may say more), else why not. Pages say messages about their own
// state here too; the next change made here replaces them.
//   Line(element or id)               the same line for the same place
//   line.say(text or [nodes], tone)   tone 'bad': a problem (red); else plain
function Line(where) {
  if (Line.all.has(where)) return Line.all.get(where);
  const el = () => (typeof where === 'string' ? document.getElementById(where) : where);
  let owner = null;
  function say(what, tone, by = null) {
    const e = el();
    owner = by;
    if (!e) return;
    e.classList.add('line');
    e.replaceChildren(...[].concat(what ?? '').filter(x => x !== ''));
    e.classList.toggle('bad', tone === 'bad' && e.childNodes.length > 0);
    e.hidden = !e.childNodes.length;
  }
  const line = {say, mine: by => owner === by};
  Line.all.set(where, line);
  return line;
}
Line.all = new Map();

// Changes: the one way a page changes anything (DESIGN.md, Changes). A change is a finished
// statement, a route and a body naming the revision the page drew (`rev`, or each item's),
// copied when it is sent so that nothing computed later can reach it. Changes to one subject
// go one at a time, in press order, and each expects the revision the one before it left:
// one made on a revision this run of changes already passed through (pressed before the last
// answer was drawn) is sent on the newest, as successive edits of one thing. A change that
// `ends` its subject (naming a submission, verify-and-next: the page moves on) drops what
// was pressed behind it on that subject: those presses were about a state the person never
// saw answered. A change that does not go through drops what was queued behind it and says,
// in one message at its `line` (else in the header), why, what the page shows now and
// whether what was typed was kept:
//   refused as stale (409)     the page is redrawn from `current`, what is there now; the
//                              redraw keeps the person's draft and returns the message
//                              (`Changes.kept(now)`), else "changed elsewhere: shown as it is now"
//   refused as invalid (400)   the server's reason; nothing is redrawn, the draft stays
//   no connection              "no connection: not saved"
//   Changes.send(subject, path, body, {done(answer), redraw(current), line, ends, long})
//   Changes.query(path, body)      a POST that changes nothing (a preview)
//   Changes.redraw = fn            the page's redraw, for changes that name none (default: reload)
//   Changes.busy()                 a long change (a model run, Accept) is under way
const Changes = (() => {
  const chains = new Map();     // subject -> {tail, n, revs: what this run passed through, now, over}
  let long = 0;
  async function call(path, data) {
    const init = data === undefined ? {} : {
      method: 'POST', body: JSON.stringify(data),
      headers: {'Content-Type': 'application/json', 'X-Token': meta('token')}};
    let res, body = {};
    try {
      res = await fetch(ROOT + path, init);
      body = await res.json();
    } catch (e) { /* network error or a non-JSON reply */ }
    return {status: res ? res.status : 0, ok: !!res && res.ok, body: body || {}};
  }
  // Why a change did not go through, in one message; a stale one redraws first.
  async function refused(r, redraw) {
    const cur = r.body.current || {};
    if (r.status !== 409) return r.status ? r.body.error || "failed" : "no connection: not saved";
    const said = await (redraw || api.redraw || (() => location.reload()))(cur);
    if (cur.running) return "already running";
    return typeof said === 'string' && said ? said
      : `changed elsewhere${cur.who ? ' (' + cur.who + ')' : ''}: shown as it is now`;
  }
  function busy(d) {
    long += d;
    if (typeof Actions !== 'undefined') Actions.enable(document);     // a top-level const, not on window
  }
  function send(subject, path, body, {done, redraw, line, ends, long: isLong} = {}) {
    body = JSON.parse(JSON.stringify(body));
    const c = chains.get(subject) || {tail: Promise.resolve(), n: 0, revs: new Set(), over: false};
    chains.set(subject, c);
    c.n++;
    if (isLong) busy(1);
    const me = {};
    const tell = text => (line ? line.say(text, 'bad', me) : flash(text));
    const mine = c.tail.then(async () => {
      if (c.over) return null;
      const one = 'rev' in body;
      if (one && c.revs.has(body.rev) && 'now' in c) body.rev = c.now;
      if (one) c.revs.add(body.rev);
      let slow = 0;
      if (line) {
        line.say('', '', me);
        slow = setTimeout(() => { if (line.mine(me)) line.say('…', '', me); }, 250);
      }
      const r = await call(path, body);
      clearTimeout(slow);
      if (!r.ok) { c.over = true; tell(await refused(r, redraw)); return null; }
      if (line && line.mine(me)) line.say('');
      if (one && 'rev' in r.body) { c.now = r.body.rev; c.revs.add(c.now); }
      if (ends) c.over = true;
      if (done) await done(r.body);
      return r.body;
    }).catch(err => { c.over = true; console.error(err); tell(`failed: ${(err && err.message) || err}`); return null; })
      .finally(() => {
        if (isLong) busy(-1);
        if (--c.n === 0) { c.revs.clear(); delete c.now; c.over = false; }
      });
    c.tail = mine;
    return mine;
  }
  const query = (path, body) => call(path, body || {});
  // A stale refusal over a draft: what is there now, and that the draft stays.
  const kept = now => `changed elsewhere to ${now} · yours kept: save again to overwrite`;
  const api = {send, query, kept, get: path => call(path), redraw: null, busy: () => long > 0};
  return api;
})();
// A value in a message: short.
const brief = v => {
  const s = typeof v === 'string' ? v : JSON.stringify(v) ?? '';
  return s === '' || s === 'null' ? 'none' : s.length > 60 ? s.slice(0, 60) + '…' : s;
};

async function waitJob(id) {
  for (;;) {
    const r = await request('/jobs/' + encodeURIComponent(id));
    if (!r.ok || r.body.state !== 'running') return r.body;
    await new Promise(done => setTimeout(done, 600));
  }
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

// A message in the header: a problem (red) unless the tone is 'ok' ("saved", "copied").
function flash(text, tone) {
  const el = document.getElementById('flash');
  if (!el) return;
  el.textContent = text || '';
  el.className = tone === 'ok' ? 'ok' : '';
  clearTimeout(flash.timer);
  if (text) flash.timer = setTimeout(() => { el.textContent = ''; }, 8000);
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
function whenShown(fn) {
  if (document.prerendering) document.addEventListener('prerenderingchange', fn, {once: true});
  else fn();
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
// Its size follows the region's own size times the grader's crop scale (1 shows a page 1000 px
// wide), no wider than the fields panel (half of it beside a multi-line value); or its own data-width.
const CROP_SCALE = 1.2, CROP_MIN = 0.4, CROP_MAX = 3;
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
  let img = crop.firstElementChild;
  if (!img) {
    img = new Image();
    img.alt = '';
    img.addEventListener('load', () => drawCrop(crop));
    img.src = crop.dataset.src;
    crop.append(img);
  }
  if (r <= l || b <= t) return;
  let width = Number(crop.dataset.width);
  if (!width) {
    width = (r - l) * 1000 * cropScale();
    const room = crop.closest('.fields');
    if (room) width = Math.min(width, Math.max(160, (room.clientWidth - 4) * (crop.closest('.field.multiline') ? 0.5 : 1)));
  }
  // Until the image is here, its box takes a letter page's proportions, so the page is
  // laid out once and the picture fills in (drawn again when it loads).
  const w = img.naturalWidth || 17, h = img.naturalWidth ? img.naturalHeight : 22;
  const k = width / ((r - l) * w);
  img.style.width = w * k + 'px';
  img.style.marginLeft = -l * w * k + 'px';
  img.style.marginTop = -t * h * k + 'px';
  crop.style.width = width + 'px';
  crop.style.height = (b - t) * h * k + 'px';
}
function drawCrops(root) {
  for (const crop of (root || document).querySelectorAll('.crop')) drawCrop(crop);
}

// The header spans the window, not the page, so it stays put when a wide page scrolls sideways.
function sizeHeader() { document.documentElement.style.setProperty('--vw', document.documentElement.clientWidth + 'px'); }
addEventListener('resize', () => { sizeHeader(); drawCrops(); });
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
