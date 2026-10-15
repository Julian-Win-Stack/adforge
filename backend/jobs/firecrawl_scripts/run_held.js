(async () => {
  // Some shops' speed scripts hold back every script after them when the browser says it
  // runs on Linux, as page-speed testers do and Firecrawl's browser does: those scripts are
  // left as type="text/lazyload" and never run, so the galleries they build stay hidden.
  // Run them in page order, as the shop runs them for its visitors.
  const held = [...document.querySelectorAll('script[type="text/lazyload"]')];
  if (!held.length) return JSON.stringify({ held: 0 });
  // A script runs as it is added, before the shop's watcher can hold it back again.
  // The page is usually ready before they run, so what they ask to run once it is ready or
  // loaded is noted, and run after them; the page's other scripts aren't run again.
  const late = [];
  const noting = (target) => {
    const add = target.addEventListener;
    target.addEventListener = function (type, handler, options) {
      if ((type === 'DOMContentLoaded' || type === 'load') && handler) late.push({ target, type, handler });
      return add.call(this, type, handler, options);
    };
  };
  noting(document);
  noting(window);
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
  delete document.addEventListener;
  delete window.addEventListener;
  for (const { target, type, handler } of late) {
    // Events the page hasn't had yet will still come, and run these handlers then.
    if (type === 'load' ? document.readyState !== 'complete' : document.readyState === 'loading') continue;
    try {
      const event = new Event(type);
      if (typeof handler === 'function') handler.call(target, event); else handler.handleEvent(event);
    } catch (e) { /* one shop script failing doesn't stop the rest */ }
  }
  return JSON.stringify({ held: held.length, failed });
})()
