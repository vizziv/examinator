'use strict';
// Shared by every page: token-carrying requests, job polling, preferences, crops.
const meta = name => document.querySelector(`meta[name="${name}"]`)?.content || '';
const ROOT = meta('root');

async function request(path, data) {
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

// Steps that change server state, from keys and buttons alike: keys come faster than
// replies, so they run one after another. A long step (a model job, Accept) makes every
// step unavailable until it ends, so keys pressed meanwhile are dropped, not replayed
// against a moved cursor. Errors are flashed.
// A step about what the person is looking at says so when the key is pressed: `bind(ctx)`
// returns the statement (its subject and the revision seen: {sub, rev, ...}), or null for
// nothing to do, and `run` gets that statement however late it runs. The server refuses a
// statement whose revision is no longer current (409), so a second press of the same key
// before the page moved on does nothing.
function steps(live, changed) {
  let chain = Promise.resolve(), busy = 0;
  const oops = err => { console.error(err); flash(`failed: ${(err && err.message) || err}`); };
  const enqueue = (fn, ctx) => (chain = chain.then(() => fn(ctx)).catch(oops));
  async function long(fn, ctx) {
    busy++;
    changed();
    try { await enqueue(fn, ctx); } finally { busy--; changed(); }
  }
  const register = (name, spec) => Actions.register(name, {
    available: ctx => live() && !busy && (!spec.available || spec.available(ctx)),
    run: ctx => {
      const said = spec.bind ? spec.bind(ctx) : ctx;
      if (said == null) return;
      return spec.long ? long(spec.run, said) : enqueue(spec.run, said);
    }});
  return {register, enqueue, busy: () => busy > 0};
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
  if (!img.naturalWidth || r <= l || b <= t) return;
  let width = Number(crop.dataset.width);
  if (!width) {
    width = (r - l) * 1000 * cropScale();
    const room = crop.closest('.fields');
    if (room) width = Math.min(width, Math.max(160, (room.clientWidth - 4) * (crop.closest('.field.multiline') ? 0.5 : 1)));
  }
  const k = width / ((r - l) * img.naturalWidth);
  img.style.width = img.naturalWidth * k + 'px';
  img.style.marginLeft = -l * img.naturalWidth * k + 'px';
  img.style.marginTop = -t * img.naturalHeight * k + 'px';
  crop.style.width = width + 'px';
  crop.style.height = (b - t) * img.naturalHeight * k + 'px';
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
