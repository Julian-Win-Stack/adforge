(() => {
  // Number every image (blue "I<n>") and every heading (red "T<n>") on the page, and return
  // what each number points at. Text between one heading and the next is that heading's
  // section, hidden text (closed accordions) included.
  // Each numbered picture also says how much of it a visitor sees without scrolling a box or
  // sliding a carousel (`vis`, 0-1; `shown` when at least 0.4) and which clipping box it sits
  // in (`box`, listed in `boxes`). Every video player is listed in `vids` (V<n>) with the
  // same facts, so a gallery's videos can be told from the rest of the page's.
  // Firecrawl lays the page out with a 100,000 px tall window while scripts run, and with a
  // normal one for the screenshot, so the marks are re-placed on every layout change, and a
  // mark is hidden while its element can't be seen.
  const MAX_SECTION = 6000, MAX_TOTAL = 250000;
  const doc = document.documentElement;
  const out = { url: location.href, vw: window.innerWidth, vh: window.innerHeight, imgs: [], secs: [], vids: [], boxes: [] };
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
  // Which ways a parent cuts off what overflows it (carousels, scroll strips).
  const clips = (p) => {
    const s = getComputedStyle(p);
    return { cx: /(hidden|clip|auto|scroll)/.test(s.overflowX), cy: /(hidden|clip|auto|scroll)/.test(s.overflowY) };
  };
  // The part of an element its clipping parents don't cut off.
  const unclipped = (el) => {
    const r = el.getBoundingClientRect();
    let l = r.left, t = r.top, rt = r.right, b = r.bottom;
    for (let p = el.parentElement; p && p !== doc; p = p.parentElement) {
      const { cx, cy } = clips(p);
      if (cx || cy) {
        const pr = p.getBoundingClientRect();
        if (cx) { l = Math.max(l, pr.left); rt = Math.min(rt, pr.right); }
        if (cy) { t = Math.max(t, pr.top); b = Math.min(b, pr.bottom); }
      }
    }
    return { r, l, t, rt, b };
  };
  // The part of an element not cut off by the window or a clipping parent.
  const visibleRect = (el) => {
    const { r, l: cl, t, rt: crt, b } = unclipped(el);
    const l = Math.max(cl, 0), rt = Math.min(crt, window.innerWidth);
    return { r, l, t, w: Math.max(0, rt - l), h: Math.max(0, b - t) };
  };
  const shown = (el) => (el.checkVisibility ? el.checkVisibility({ opacityProperty: true, visibilityProperty: true }) : true);
  // How much of an element a visitor sees without scrolling a box: 0 (cut off or hidden) to 1
  // (whole). The window is left out, since Firecrawl runs this in a very tall window and then
  // screenshots the whole page.
  const visibleShare = (el) => {
    if (!shown(el)) return 0;
    const { r, l, t, rt, b } = unclipped(el);
    if (r.width <= 0 || r.height <= 0) return 0;
    return Math.round(Math.max(0, rt - l) * Math.max(0, b - t) / (r.width * r.height) * 100) / 100;
  };
  // The clipping box an element sits in: the nearest clipping parent that actually cuts it
  // off, else (it is undisplayed, or wholly inside) the nearest clipping parent, else 0.
  // Each box gets an id the first time it is met.
  const boxIds = new Map();
  const boxOf = (el) => {
    const r = el.getBoundingClientRect();
    let box = null;
    for (let p = el.parentElement; p && p !== doc; p = p.parentElement) {
      const { cx, cy } = clips(p);
      if (!cx && !cy) continue;
      if (!box) box = p;
      const pr = p.getBoundingClientRect();
      if ((cx && (r.left < pr.left - 1 || r.right > pr.right + 1)) || (cy && (r.top < pr.top - 1 || r.bottom > pr.bottom + 1))) {
        box = p;
        break;
      }
    }
    if (!box) return 0;
    if (!boxIds.has(box)) {
      const pr = box.getBoundingClientRect();
      boxIds.set(box, boxIds.size + 1);
      out.boxes.push({
        id: boxIds.get(box), x: Math.round(pr.left + scrollX), y: Math.round(pr.top + scrollY),
        w: Math.round(pr.width), h: Math.round(pr.height), tag: box.tagName.toLowerCase(),
        cls: (typeof box.className === 'string' ? box.className : '').slice(0, 120),
        aria: (box.getAttribute('aria-label') || box.id || '').slice(0, 80),
      });
    }
    return boxIds.get(box);
  };
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

  // Some galleries show their photos as an element's background, not as a picture: those
  // are numbered too, in page order with the pictures.
  const backgroundOf = (el) => {
    // The browser always writes a background's link as url("..."), quotes escaped.
    const m = /url\("((?:[^"\\]|\\.)*)"\)/.exec(getComputedStyle(el).backgroundImage);
    if (!m || m[1].startsWith('data:')) return '';
    try { return new URL(m[1], location.href).href; } catch (e) { return ''; }  // a broken link
  };
  let n = 0;
  document.querySelectorAll('body *').forEach((el) => {
    const isImg = el.tagName === 'IMG';
    const background = isImg ? '' : backgroundOf(el);
    if (!isImg && !background) return;
    const a = el.closest('a[href]');
    const r = el.getBoundingClientRect();
    const rec = isImg ? {
      n: null, src: el.currentSrc || el.src || '',
      srcset: el.getAttribute('srcset') || el.getAttribute('data-srcset') || '',
      dataSrc: el.getAttribute('data-src') || el.getAttribute('data-original') || '',
      alt: (el.alt || '').slice(0, 200), nw: el.naturalWidth, nh: el.naturalHeight,
    } : {
      n: null, src: background, srcset: '', dataSrc: '',
      alt: (el.getAttribute('aria-label') || el.getAttribute('title') || '').slice(0, 200), nw: 0, nh: 0,
    };
    Object.assign(rec, {
      href: a ? a.href : '', x: Math.round(r.left + scrollX), y: Math.round(r.top + scrollY),
      w: Math.round(r.width), h: Math.round(r.height),
    });
    if (r.width >= 40 && r.height >= 40) {
      rec.n = ++n; add(el, 'I' + n, '#0050ff', true);
      rec.vis = visibleShare(el); rec.shown = rec.vis >= 0.4; rec.box = boxOf(el);
    }
    out.imgs.push(rec);
  });

  // Video players: the page's own videos and embedded players from the video sites shops use.
  const PLAYER = /youtube\.com\/embed|youtube-nocookie|youtu\.be|vimeo\.com\/video|player\.vimeo|wistia\.(com|net)|vidyard|tolstoy|videowise/i;
  const addVideo = (el, kind, src, poster) => {
    const r = el.getBoundingClientRect();
    const a = el.closest('a[href]');
    const vis = visibleShare(el);
    out.vids.push({
      n: out.vids.length + 1, kind, src, poster,
      x: Math.round(r.left + scrollX), y: Math.round(r.top + scrollY), w: Math.round(r.width), h: Math.round(r.height),
      vis, shown: vis >= 0.4 && r.width >= 40 && r.height >= 40, box: boxOf(el),
      href: a ? a.href : '', aria: (el.getAttribute('aria-label') || el.getAttribute('title') || '').slice(0, 120),
      autoplay: !!el.autoplay, loop: !!el.loop, muted: !!el.muted, controls: !!el.controls,
    });
  };
  const absolute = (link) => {
    try { return link ? new URL(link, location.href).href : ''; } catch (e) { return ''; }  // a broken link
  };
  document.querySelectorAll('video, iframe[src]').forEach((el) => {
    if (el.tagName === 'IFRAME') {
      if (PLAYER.test(el.src)) addVideo(el, 'iframe', el.src, '');
      return;
    }
    const source = el.querySelector('source[src]');
    const src = el.currentSrc || el.src || (source && source.src) || absolute(el.getAttribute('data-src'));
    addVideo(el, 'video', src, el.poster || absolute(el.getAttribute('data-poster')));
  });

  const HEAD = 'h1,h2,h3,h4,h5,h6,[role="heading"]';
  const SKIP = 'script,style,noscript,template,svg,#__marks';
  // A new piece starts at a real heading, at a fake heading (a short bold or large line on its
  // own), and once a piece passes SPLIT characters at the next block, so text glued under the
  // wrong heading stays small.
  const SPLIT = 1200;
  const baseFS = parseFloat(getComputedStyle(document.body || doc).fontSize) || 16;
  const BLOCKY = /^(block|flex|grid|list-item|table|table-cell|table-row|table-caption|flow-root)$/;
  const disp = new Map();
  const isBlock = (e) => { if (!disp.has(e)) disp.set(e, BLOCKY.test(getComputedStyle(e).display)); return disp.get(e); };
  const blockOf = (el) => { for (let e = el; e && e !== doc; e = e.parentElement) if (isBlock(e)) return e; return doc; };
  let cur = { i: 0, heading: '(no heading)', kind: 'start', text: '' };
  out.secs.push(cur);
  let total = 0, headEl = null, lastBlock = null;
  const start = (el, heading, kind) => {
    const r = el.getBoundingClientRect();
    cur = { i: out.secs.length, heading: heading.slice(0, 200), kind, text: '', y: Math.round(r.top + scrollY) };
    out.secs.push(cur);
    add(el, 'T' + cur.i, '#e00000', false);
  };
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
        start(node, text, 'heading');
      }
      continue;
    }
    if (headEl && headEl.contains(node)) continue;
    const t = node.nodeValue.replace(/\s+/g, ' ').trim();
    if (!t || total > MAX_TOTAL) continue;
    const blk = blockOf(node.parentElement);
    if (blk !== lastBlock) {
      lastBlock = blk;
      const whole = (blk.textContent || '').replace(/\s+/g, ' ').trim();
      const st = getComputedStyle(node.parentElement);
      const loud = parseInt(st.fontWeight, 10) >= 600 || parseFloat(st.fontSize) >= baseFS * 1.2;
      if (loud && whole.length >= 2 && whole.length <= 80 && whole.startsWith(t.slice(0, 20))
          && !blk.closest('a,button,nav,header,footer,[role="dialog"]')) {
        headEl = blk;
        start(blk, whole, 'bold');
        continue;
      }
      if (cur.text.length >= SPLIT) start(blk, '(continued) ' + out.secs[out.secs.length - 1].heading.replace(/^\(continued\) /, ''), 'split');
    }
    if (cur.text.length > MAX_SECTION) continue;
    cur.text += (cur.text ? ' ' : '') + t; total += t.length + 1;
  }

  place();
  window.addEventListener('resize', place, true);
  new ResizeObserver(place).observe(doc);
  setInterval(place, 300);
  return JSON.stringify(out);
})()
