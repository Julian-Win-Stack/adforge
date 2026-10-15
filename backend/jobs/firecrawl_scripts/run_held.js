(async () => {
  // Some shops' speed scripts hold back every script after them when the browser says it
  // runs on Linux, as page-speed testers do and Firecrawl's browser does: those scripts are
  // left as type="text/lazyload" and never run, so the galleries they build stay hidden.
  // Run them in page order, as the shop runs them for its visitors.
  const held = [...document.querySelectorAll('script[type="text/lazyload"]')];
  if (!held.length) return JSON.stringify({ held: 0 });
  // A script runs as it is added, before the shop's watcher can hold it back again.
  let failed = 0;
  for (const old of held) {
    const s = document.createElement('script');
    for (const { name, value } of old.attributes) if (name !== 'type' && name !== 'data-src') s.setAttribute(name, value);
    const src = old.getAttribute('data-src');
    if (!src) {
      s.textContent = old.textContent;
      old.replaceWith(s);
      continue;
    }
    s.src = src;
    const loaded = new Promise((done) => {
      s.onload = () => done(true);
      s.onerror = () => done(false);
      setTimeout(() => done(false), 5000);
    });
    old.replaceWith(s);
    if (!(await loaded)) failed++;
  }
  // Scripts that wait for the page to be ready were held past that moment.
  document.dispatchEvent(new Event('DOMContentLoaded', { bubbles: true }));
  window.dispatchEvent(new Event('load'));
  return JSON.stringify({ held: held.length, failed });
})()
