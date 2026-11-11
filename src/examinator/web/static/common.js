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

function flash(text) {
  const el = document.getElementById('flash');
  if (!el) return;
  el.textContent = text || '';
  clearTimeout(flash.timer);
  if (text) flash.timer = setTimeout(() => { el.textContent = ''; }, 8000);
}

function make(tag, props, ...children) {
  const el = document.createElement(tag);
  Object.assign(el, props || {});
  el.append(...children);
  return el;
}

// A crop is a window onto the cached full-page image: <span class="crop" data-src data-box="l,t,r,b">,
// as wide as the grader's crop size (or its own data-width).
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
  const width = Number(crop.dataset.width) || pref('cropWidth', 360);
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
addEventListener('resize', sizeHeader);
if (window.ResizeObserver) new ResizeObserver(sizeHeader).observe(document.documentElement);
// A range's filled part (WebKit has no progress pseudo-element): --fill on the input.
function fillRange(el) {
  const min = Number(el.min || 0), max = Number(el.max || 100);
  el.style.setProperty('--fill', ((Number(el.value) - min) / (max - min || 1)) * 100 + '%');
}
document.addEventListener('input', e => { if (e.target.matches?.('input[type=range]')) fillRange(e.target); });
document.addEventListener('DOMContentLoaded', () => {
  sizeHeader();
  document.querySelectorAll('input[type=range]').forEach(fillRange);
  drawCrops();
  const size = document.getElementById('crop-size');
  if (size) {
    size.value = pref('cropWidth', 360);
    fillRange(size);
    size.addEventListener('input', () => { setPref('cropWidth', Number(size.value)); drawCrops(); });
  }
});
