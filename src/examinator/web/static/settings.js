'use strict';
(() => {
  const wins = [...document.querySelectorAll('.win')], cover = document.getElementById('cover');
  const img = document.getElementById('pv-img'), rect = document.getElementById('pv-rect');
  const sub = document.getElementById('pv-sub'), page = document.getElementById('pv-page');
  const msg = document.getElementById('msg');

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
  document.getElementById('save').addEventListener('click', async () => {
    const r = await request('/settings', {cover_pages: Number(cover.value), scan_window: wins.map(w => Number(w.value))});
    msg.textContent = r.ok ? "saved" : r.body.error || "failed";
  });
  const size = document.getElementById('crop-size'), n = document.getElementById('crop-n');
  const shown = () => { n.textContent = size.value + ' px'; };
  size.addEventListener('input', shown);
  document.addEventListener('DOMContentLoaded', shown);
  preview();
  load();
})();
