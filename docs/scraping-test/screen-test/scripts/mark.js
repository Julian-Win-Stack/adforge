(() => {
  // Number every image (blue "I<n>") and every heading (red "T<n>") on the page, and return
  // what each number points at. Text between one heading and the next is that heading's
  // section, hidden text (closed accordions) included.
  // Firecrawl lays the page out with a 100,000 px tall window while scripts run, and with a
  // normal one for the screenshot, so the marks are re-placed on every layout change, and a
  // mark is hidden while its element can't be seen.
  const MAX_SECTION = 6000, MAX_TOTAL = 250000;
  const doc = document.documentElement;
  const out = { url: location.href, vw: window.innerWidth, vh: window.innerHeight, imgs: [], secs: [] };
  const layer = document.createElement('div');
  layer.id = '__marks';
  layer.style.cssText = 'position:absolute;left:0;top:0;width:0;height:0;z-index:2147483647;pointer-events:none;';
  doc.appendChild(layer);
  const items = [];
  const add = (el, label, color, isImage) => {
    const t = document.createElement('div');
    t.textContent = label;
    t.style.cssText = `position:absolute;background:${color};color:#fff;font:bold 28px/1 Arial,sans-serif;padding:3px 6px;white-space:nowrap;display:none;`;
    layer.appendChild(t);
    let b = null;
    if (isImage) {
      b = document.createElement('div');
      b.style.cssText = `position:absolute;border:4px solid ${color};box-sizing:border-box;display:none;`;
      layer.appendChild(b);
    }
    items.push({ el, t, b, isImage });
  };
  // The part of an element not cut off by the window or a clipping parent (carousels).
  const visibleRect = (el) => {
    const r = el.getBoundingClientRect();
    let l = r.left, t = r.top, rt = r.right, b = r.bottom;
    for (let p = el.parentElement; p && p !== doc; p = p.parentElement) {
      const s = getComputedStyle(p);
      const cx = /(hidden|clip|auto|scroll)/.test(s.overflowX), cy = /(hidden|clip|auto|scroll)/.test(s.overflowY);
      if (cx || cy) {
        const pr = p.getBoundingClientRect();
        if (cx) { l = Math.max(l, pr.left); rt = Math.min(rt, pr.right); }
        if (cy) { t = Math.max(t, pr.top); b = Math.min(b, pr.bottom); }
      }
    }
    l = Math.max(l, 0); rt = Math.min(rt, window.innerWidth);
    return { r, l, t, w: Math.max(0, rt - l), h: Math.max(0, b - t) };
  };
  const shown = (el) => (el.checkVisibility ? el.checkVisibility({ opacityProperty: true, visibilityProperty: true }) : true);
  const place = () => {
    for (const it of items) {
      const v = visibleRect(it.el);
      const area = v.r.width * v.r.height;
      const ok = it.isImage
        ? shown(it.el) && v.r.width >= 40 && v.r.height >= 40 && (v.w * v.h) / area >= 0.4
        : shown(it.el) && v.r.width > 0 && v.r.height > 0 && v.w > 0;
      if (!ok) { it.t.style.display = 'none'; if (it.b) it.b.style.display = 'none'; continue; }
      const x = v.l + scrollX, y = v.t + scrollY;
      it.t.style.display = 'block';
      it.t.style.left = `${Math.max(0, it.isImage ? x : x - 4)}px`;
      it.t.style.top = `${Math.max(0, it.isImage ? y : y - 34)}px`;
      if (it.b) Object.assign(it.b.style, { display: 'block', left: `${x}px`, top: `${y}px`, width: `${v.w}px`, height: `${v.h}px` });
    }
  };

  let n = 0;
  document.querySelectorAll('img').forEach((img) => {
    const a = img.closest('a[href]');
    const r = img.getBoundingClientRect();
    const rec = {
      n: null, src: img.currentSrc || img.src || '',
      srcset: img.getAttribute('srcset') || img.getAttribute('data-srcset') || '',
      dataSrc: img.getAttribute('data-src') || img.getAttribute('data-original') || '',
      alt: (img.alt || '').slice(0, 200), nw: img.naturalWidth, nh: img.naturalHeight,
      href: a ? a.href : '', x: Math.round(r.left + scrollX), y: Math.round(r.top + scrollY),
      w: Math.round(r.width), h: Math.round(r.height),
    };
    if (r.width >= 40 && r.height >= 40) { rec.n = ++n; add(img, 'I' + n, '#0050ff', true); }
    out.imgs.push(rec);
  });

  const HEAD = 'h1,h2,h3,h4,h5,h6,[role="heading"]';
  const SKIP = 'script,style,noscript,template,svg,#__marks';
  let cur = { i: 0, heading: '(no heading)', text: '' };
  out.secs.push(cur);
  let total = 0, headEl = null;
  // FILTER_REJECT skips the element and everything inside it.
  const walker = document.createTreeWalker(document.body || doc, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, {
    acceptNode: (nd) => (nd.nodeType === 1 && nd.matches(SKIP) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT),
  });
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (node.nodeType === 1) {
      if (node.matches(HEAD) && !(headEl && headEl.contains(node))) {
        const text = (node.innerText || node.textContent || '').replace(/\s+/g, ' ').trim();
        if (!text) continue;
        headEl = node;
        const r = node.getBoundingClientRect();
        cur = { i: out.secs.length, heading: text.slice(0, 200), text: '', y: Math.round(r.top + scrollY) };
        out.secs.push(cur);
        add(node, 'T' + cur.i, '#e00000', false);
      }
      continue;
    }
    if (headEl && headEl.contains(node)) continue;
    const t = node.nodeValue.replace(/\s+/g, ' ').trim();
    if (!t || total > MAX_TOTAL || cur.text.length > MAX_SECTION) continue;
    cur.text += (cur.text ? ' ' : '') + t; total += t.length + 1;
  }

  place();
  window.addEventListener('resize', place, true);
  new ResizeObserver(place).observe(doc);
  setInterval(place, 300);
  return JSON.stringify(out);
})()
