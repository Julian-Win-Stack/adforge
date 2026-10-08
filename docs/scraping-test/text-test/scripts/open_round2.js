(async () => {
  // Round 2 opener (not chosen, see results.md "Decision"). Round 1 is open.js.
  // Open closed accordions, tabs and "Read more" buttons so their text is in the screenshot.
  // Many sites keep only one section open at a time (opening "How to use" closes "Details"), so
  // after opening each section a static copy of its content is left in the page; the copy stays
  // when the next click closes the original. Copies of sections that are still open at the end
  // replace their originals. Skips menus, carts, search, dialogs, reviews, shipping and returns.
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const T0 = Date.now(), BUDGET = 40000, WAIT = 450;
  const SKIP = 'header,nav,footer,[role="dialog"],[aria-modal="true"],[role="menu"],[role="menubar"],[role="listbox"],[role="combobox"],[data-adf-copy]';
  const BAD = /menu|cart|bag|basket|search|account|sign ?in|log ?in|country|region|language|currency|filter|sort|share|wish|close|size (guide|chart)|find (my|your) size|chat|help|store|add to|buy|checkout|subscribe|notify|review|write|cookie|shipping|delivery|return|accessib|zoom|(see|view) all|compar|save \d|[$£€]\s?\d|\d% off|image|slide|photo|video|^display .{20}|\(\d[\d.,]*k?\)$/i;
  const MORE = /^\+?\s*(read|see|show|view) more\s*[.…+]*$|^more (details|info)$|^(read|see|show) (the )?full (description|details)$/i;
  const CLS = /accordion|collaps|toggle|expand|disclosure|drawer|tab/i;

  const vis = (el) => el.getClientRects().length > 0 &&
    (!el.checkVisibility || el.checkVisibility({ opacityProperty: true, visibilityProperty: true }));
  const label = (b) => (b.innerText || b.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim();
  const realLink = (b) => {
    const h = (b.tagName === 'A' && b.getAttribute('href') || '').trim();
    return !!h && !/^(#|javascript:)/i.test(h);
  };
  // Carousels, photo and video galleries, reviews and buy options: their buttons open players or
  // change the purchase choice.
  const ZONE = /carousel|slider|swiper|slick|splide|glide|gallery|lightbox|modal|popup|ugc|review|purchase|subscri|autodeliver/i;
  const inZone = (b) => {
    for (let p = b; p && p !== document.body; p = p.parentElement) if (ZONE.test(String(p.className))) return true;
    return false;
  };
  const done = new WeakSet();
  const okToggle = (b) => {
    if (done.has(b) || !vis(b) || b.closest(SKIP) || b.closest('label,[role="radiogroup"],[role="radio"]') || b.disabled) return false;
    if (b.closest('summary')) return false;  // <details> boxes are opened directly; a click would close them
    const txt = label(b);
    if (!txt || txt.length > 120 || BAD.test(txt) || realLink(b)) return false;
    const hp = b.getAttribute('aria-haspopup');
    return (!hp || hp === 'false') && !inZone(b);
  };
  // A header with no ARIA whose hidden content follows it (accordions built from plain divs).
  const plainHeader = (b) => {
    if (b.hasAttribute('aria-expanded') || b.children.length > 3) return false;
    const txt = label(b);
    if (txt.length < 2 || txt.length > 60) return false;
    if (!CLS.test(`${b.className} ${b.parentElement && b.parentElement.className}`)) return false;
    if (getComputedStyle(b).cursor !== 'pointer') return false;
    const nx = b.nextElementSibling;
    return !!nx && (!vis(nx) || nx.getBoundingClientRect().height < 2) && nx.textContent.trim().length > 30;
  };
  const candidates = (scope) => [...scope.querySelectorAll(
    '[aria-expanded="false"],[role="tab"][aria-selected="false"],button,a,[role="button"],div,span,h2,h3,h4,dt,li')]
    .filter((b) => {
      if (b.matches('[aria-expanded="false"],[role="tab"][aria-selected="false"]')) return okToggle(b);
      if (b.hasAttribute('aria-expanded') || b.hasAttribute('aria-selected')) return false;
      if (b.matches('button,a,[role="button"]') && MORE.test(label(b))) return okToggle(b);
      return b.matches('div,span,h2,h3,h4,dt,li,button') && plainHeader(b) && okToggle(b);
    });

  // Copy a section as it looks now: every part keeps the display it has while open.
  const freeze = (panel, title) => {
    const copy = panel.cloneNode(true);
    const a = [panel, ...panel.querySelectorAll('*')], c = [copy, ...copy.querySelectorAll('*')];
    for (let i = 0; i < a.length && i < c.length; i++) {
      const s = getComputedStyle(a[i]);
      if (s.display !== 'none') c[i].style.setProperty('display', s.display, 'important');
      c[i].style.setProperty('visibility', 'visible', 'important');
      c[i].removeAttribute('id'); c[i].removeAttribute('hidden');
      if (c[i].getAttribute('aria-hidden') === 'true') c[i].removeAttribute('aria-hidden');
    }
    for (const p of ['height', 'max-height', 'overflow', 'opacity', 'transform', 'clip-path']) copy.style.removeProperty(p);
    copy.style.setProperty('height', 'auto', 'important');
    copy.style.setProperty('max-height', 'none', 'important');
    copy.style.setProperty('overflow', 'visible', 'important');
    copy.style.setProperty('opacity', '1', 'important');
    copy.setAttribute('data-adf-copy', '1');
    if (title) {
      const h = document.createElement('div');
      h.textContent = title;
      h.style.cssText = 'font-weight:700;margin:12px 0 4px;';
      copy.prepend(h);
    }
    panel.after(copy);
    return copy;
  };

  // The content a click revealed: the aria-controls target, or the parts of the nearest parent
  // that grew taller.
  const heights = (b) => {
    const out = [];
    for (let p = b.parentElement, i = 0; p && p !== document.body && i < 8; p = p.parentElement, i++) {
      out.push([p, p.getBoundingClientRect().height, new Map([...p.children].map((k) => [k, k.getBoundingClientRect().height]))]);
    }
    return out;
  };
  const revealed = (b, before) => {
    const id = b.getAttribute('aria-controls');
    const t = id && document.getElementById(id);
    if (t && vis(t) && !t.contains(b) && t.innerText.trim()) return [t];
    for (const [p, h, kids] of before) {
      if (p.getBoundingClientRect().height - h < 10) continue;
      return [...p.children].filter((k) => !k.contains(b) && vis(k) && k.innerText.trim() &&
        k.getBoundingClientRect().height - (kids.get(k) || 0) >= 10);
    }
    return [];
  };
  // The content of a section that is open already (so a later click closing it doesn't lose it).
  const openPanel = (b) => {
    const id = b.getAttribute('aria-controls');
    const t = id && document.getElementById(id);
    if (t) return vis(t) && t.innerText.trim() ? t : null;
    for (const n of [b.nextElementSibling, b.parentElement && b.parentElement.nextElementSibling]) {
      if (n && !n.contains(b) && vis(n) && n.innerText.trim().length > 20) return n;
    }
    return null;
  };

  let opened = 0, copied = 0, dupes = 0;
  const pairs = [], clicked = [];
  const settle = (list) => {
    // A section still open shows its text twice (itself and its copy). Hide the original, not
    // the copy: some sites fold a section again when the window is resized for the screenshot.
    for (const [panel, copy, h] of list) {
      const ph = panel.isConnected && vis(panel) ? panel.getBoundingClientRect().height : 0;
      if (copy.isConnected && h > 10 && ph >= 0.8 * h) {
        panel.style.setProperty('display', 'none', 'important'); dupes++;
      }
    }
  };
  const keep = (panel, title, list) => {
    if (panel.closest('[data-adf-copy]')) return;
    const h = panel.getBoundingClientRect().height;
    list.push([panel, freeze(panel, title), h]); copied++;
  };

  // Pop-ups (sign-in, sign-up, image zoom) cover the page in the screenshot and block clicks.
  // Hide any that is on screen: a dialog, or a fixed layer over 40% of the window. Never hide
  // something holding the page's main heading or a lot of text (a site built as one fixed layer).
  const blockers = () => {
    const out = new Set();
    for (const [x, y] of [[0.5, 0.5], [0.25, 0.3], [0.75, 0.7]]) {
      for (let e = document.elementFromPoint(innerWidth * x, innerHeight * y); e && e !== document.body; e = e.parentElement) {
        if (getComputedStyle(e).position !== 'fixed') continue;
        const r = e.getBoundingClientRect();
        if (r.width * r.height > 0.4 * innerWidth * innerHeight) out.add(e);
      }
    }
    document.querySelectorAll('[aria-modal="true"],[role="dialog"],dialog[open]').forEach((e) => {
      const r = e.getBoundingClientRect();
      if (vis(e) && r.width * r.height > 0.05 * innerWidth * innerHeight &&
          r.bottom > 0 && r.top < innerHeight && r.right > 0 && r.left < innerWidth) out.add(e);
    });
    const dialog = (e) => e.matches('[aria-modal="true"],[role="dialog"],dialog') || !!e.querySelector('[aria-modal="true"],[role="dialog"],dialog');
    return [...out].filter((e) => !e.closest('[data-adf-copy]') && !e.querySelector('h1') && (dialog(e) || e.innerText.length < 3000));
  };
  let hidden = 0;
  const unblock = (list = blockers()) => {
    for (const e of list) { e.style.setProperty('display', 'none', 'important'); hidden++; }
    if (list.length) {
      for (const e of [document.documentElement, document.body]) {
        if (getComputedStyle(e).overflowY === 'hidden') e.style.setProperty('overflow', 'visible', 'important');
      }
    }
    return list.length;
  };
  // Pop-ups already open (sign-in offers): press their close button first, since some sites
  // ignore clicks on the page while one is open; hide what's left.
  for (const e of blockers()) {
    const x = [...e.querySelectorAll('button,[role="button"],a')].find((c) =>
      /close|dismiss|no thanks/i.test(`${c.getAttribute('aria-label') || ''} ${c.innerText || ''}`) || /^[×✕✖xX]$/.test((c.innerText || '').trim()));
    if (x) { try { x.click(); } catch (err) { /* ignore */ } }
  }
  await sleep(400);
  unblock();

  const handle = async (b, depth, list) => {
    if (Date.now() - T0 > BUDGET) return;
    done.add(b);
    const before = heights(b);
    const name = label(b).slice(0, 40);
    try { b.click(); } catch (e) { return; }
    opened++; clicked.push(name);
    // Wait until the opening animation is over: the parents' heights stop changing.
    let last = '';
    for (let t = 0; t < 2000; t += 150) {
      await sleep(150);
      const now = before.map(([p]) => Math.round(p.getBoundingClientRect().height)).join();
      if (t >= WAIT && now === last) break;
      last = now;
    }
    // The click opened a drawer or pop-up (full description, full ingredients): leave a copy of
    // its text under the button, then hide it.
    const layers = blockers();
    if (layers.length) {
      for (const e of layers) {
        const box = e.matches('[role="dialog"],[aria-modal="true"],dialog') ? e : e.querySelector('[role="dialog"],[aria-modal="true"],dialog') || e;
        const n = box.innerText.trim().length;
        // Not photo viewers, review pop-ups or sign-in offers.
        if (n < 30 || n > 20000 || box.querySelectorAll('img').length > 5 ||
            ZONE.test(`${box.className} ${box.id} ${box.getAttribute('aria-label') || ''}`) || BAD.test(box.innerText.slice(0, 80))) continue;
        const at = b.closest('div,section,li') || b;
        const copy = freeze(box, name);
        copy.querySelectorAll('*').forEach((c) => { if (/fixed|sticky/.test(c.style.position)) c.style.position = 'static'; });
        for (const p of ['position', 'top', 'left', 'right', 'bottom', 'inset', 'width', 'max-width', 'transform']) copy.style.removeProperty(p);
        copy.style.setProperty('position', 'static', 'important');
        copy.style.setProperty('width', 'auto', 'important');
        copy.style.setProperty('transform', 'none', 'important');
        at.after(copy); copied++;
        copy.querySelectorAll('button,[role="button"]').forEach((c) => c.remove());
      }
      unblock(layers);
      return;
    }
    const panels = revealed(b, before);
    const inner = [];
    if (depth < 2) {
      for (const p of panels) for (const c of candidates(p)) await handle(c, depth + 1, inner);
      settle(inner);
    }
    const tab = b.getAttribute('role') === 'tab' || b.hasAttribute('aria-selected');
    for (const p of panels) if (p.isConnected && vis(p) && p.getBoundingClientRect().height > 10) keep(p, tab ? label(b) : '', list);
  };

  document.querySelectorAll('details:not([open])').forEach((d) => {
    if (!d.closest('header,nav,footer')) { d.removeAttribute('name'); d.open = true; opened++; }
  });
  // Sections open from the start, which a later click in the same group would close.
  document.querySelectorAll('[aria-expanded="true"],[role="tab"][aria-selected="true"]').forEach((b) => {
    if (!vis(b) || b.closest(SKIP) || BAD.test(label(b))) return;
    const p = openPanel(b);
    if (p && !p.closest('[data-adf-copy]')) keep(p, b.getAttribute('role') === 'tab' ? label(b) : '', pairs);
  });
  for (let round = 0; round < 3; round++) {
    // Later rounds catch toggles that appeared after earlier clicks. A button the site redrew
    // after its click is a new element with the same label: clicking it again would close it.
    const list = candidates(document).filter((b) => round === 0 || !clicked.includes(label(b).slice(0, 40)));
    if (!list.length) break;
    for (const b of list) await handle(b, 0, pairs);
  }
  await sleep(WAIT);
  unblock();
  settle(pairs);
  window.scrollTo(0, 0);
  return JSON.stringify({ opened, copied, replaced: dupes, hidden, ms: Date.now() - T0, clicked });
})()
