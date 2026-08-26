(() => {
  const sel = null;
  let root = null;
  if (sel) {
    try { root = document.querySelector(sel); }
    catch (e) {
      return { invalidSelector: true, reason: (e && e.message) || String(e) };
    }
    if (!root) return { notFound: true };
  } else {
    root = document.querySelector('main') || document.querySelector('article') || document.body || document.documentElement;
  }
  if (!root) return { notFound: true };
  const clone = root.cloneNode(true);
  const drop = [
    'script', 'style', 'noscript', 'template',
    'nav', 'header', 'footer', 'aside',
    'iframe', 'svg', 'canvas',
    'form', 'button', 'input', 'select', 'textarea',
    '[role="navigation"]', '[role="banner"]', '[role="contentinfo"]', '[role="complementary"]',
    '[aria-hidden="true"]',
  ];
  for (const q of drop) {
    for (const n of clone.querySelectorAll(q)) n.remove();
  }
  // Also strip event-handler and style attributes that bloat markdown output.
  const walker = document.createTreeWalker(clone, NodeFilter.SHOW_ELEMENT);
  let n = walker.currentNode;
  while (n) {
    if (n.nodeType === 1) {
      const el = n;
      for (const a of [...el.attributes]) {
        if (a.name.startsWith('on') || a.name === 'style' || a.name.startsWith('data-')) el.removeAttribute(a.name);
      }
    }
    n = walker.nextNode();
  }
  return { ok: true, url: location.href, title: document.title || '', html: clone.outerHTML || '' };
})()
