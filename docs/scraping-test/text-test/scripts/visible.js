(() => {
  // The page text a person can see (not clipped, hidden or folded away), for measuring open.js.
  const clipped = (el) => {
    const r = el.getBoundingClientRect();
    let l = r.left, t = r.top, rt = r.right, b = r.bottom;
    for (let p = el.parentElement; p && p !== document.documentElement; p = p.parentElement) {
      const s = getComputedStyle(p);
      const cx = /(hidden|clip|auto|scroll)/.test(s.overflowX), cy = /(hidden|clip|auto|scroll)/.test(s.overflowY);
      if (cx || cy) {
        const pr = p.getBoundingClientRect();
        if (cx) { l = Math.max(l, pr.left); rt = Math.min(rt, pr.right); }
        if (cy) { t = Math.max(t, pr.top); b = Math.min(b, pr.bottom); }
      }
    }
    return Math.min(rt, window.innerWidth) - Math.max(l, 0) < 2 || b - t < 2;
  };
  const seen = new Map();
  const out = [];
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = w.nextNode(); n; n = w.nextNode()) {
    const s = n.nodeValue.replace(/\s+/g, ' ').trim();
    if (!s) continue;
    const el = n.parentElement;
    if (!el || /^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE)$/.test(el.tagName)) continue;
    if (!seen.has(el)) {
      seen.set(el, el.checkVisibility({ opacityProperty: true, visibilityProperty: true }) && !clipped(el));
    }
    if (seen.get(el)) out.push(s);
  }
  return JSON.stringify({ visible: out.join('\n') });
})()
