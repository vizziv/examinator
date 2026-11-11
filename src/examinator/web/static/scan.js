'use strict';
// The scan window: shows whatever student and question the question page is on.
(() => {
  const root = document.querySelector('meta[name="root"]').content;
  const channel = new BroadcastChannel('examinator-scan');
  const all = document.getElementById('all');
  let last = null;

  function show() {
    if (!last) return;
    document.getElementById('who').textContent = last.name || last.sub;
    document.getElementById('what').textContent = last.q;
    document.title = `${last.sub} · ${last.q}`;
    const pages = all.checked ? Array.from({length: last.count}, (_, i) => i) : last.pages;
    const out = document.getElementById('pages');
    out.replaceChildren();
    for (const index of pages) {
      const img = new Image();
      img.alt = `scanned page ${index + 1}`;
      img.src = `${root}/page/${encodeURIComponent(last.sub)}/${Number(index)}.jpg`;
      out.append(img);
    }
    scrollTo(0, 0);
  }
  channel.addEventListener('message', e => {
    if (e.data && e.data.sub) { last = e.data; show(); }
  });
  all.addEventListener('change', show);
  channel.postMessage({hello: true});
})();
