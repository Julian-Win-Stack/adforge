(() => {
  // Open closed accordions and "Details"-style tabs so their text is in the screenshot.
  // Skips menus, carts, search, dialogs and anything that would open a pop-up over the page.
  let n = 0;
  document.querySelectorAll('details:not([open])').forEach((d) => {
    if (!d.closest('header,nav,footer')) { d.open = true; n++; }
  });
  const BAD = /menu|cart|bag|basket|search|account|sign ?in|log ?in|country|region|language|currency|filter|sort|share|wish|close|size (guide|chart)|find (my|your) size|chat|help|store/i;
  document.querySelectorAll('[aria-expanded="false"]').forEach((b) => {
    if (b.closest('header,nav,footer,[role="dialog"],[aria-modal="true"],[role="menu"],[role="listbox"]')) return;
    const txt = (b.innerText || b.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
    if (!txt || txt.length > 60 || BAD.test(txt)) return;
    const href = b.getAttribute('href');
    if (b.tagName === 'A' && href && !href.startsWith('#')) return;
    if (b.getAttribute('aria-haspopup') && b.getAttribute('aria-haspopup') !== 'false') return;
    try { b.click(); n++; } catch (e) { /* ignore */ }
  });
  return JSON.stringify({ opened: n });
})()
