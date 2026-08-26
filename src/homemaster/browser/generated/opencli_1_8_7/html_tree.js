(() => {
  
function compoundInfoOf(el) {
  if (!el || !el.tagName) return null;
  const tag = el.tagName;
  const LABEL_CAP = 80;
  const OPTS_CAP = 50;
  if (tag === 'INPUT') {
    const type = (el.getAttribute('type') || 'text').toLowerCase();
    const FORMATS = {
      'date': 'YYYY-MM-DD',
      'time': 'HH:MM',
      'datetime-local': 'YYYY-MM-DDTHH:MM',
      'month': 'YYYY-MM',
      'week': 'YYYY-W##',
    };
    if (FORMATS[type]) {
      const info = {
        control: type,
        format: FORMATS[type],
        current: (el.value == null ? '' : String(el.value)),
      };
      const min = el.getAttribute('min');
      if (min) info.min = min;
      const max = el.getAttribute('max');
      if (max) info.max = max;
      return info;
    }
    if (type === 'file') {
      const info = {
        control: 'file',
        multiple: !!el.multiple,
        current: [],
      };
      const accept = el.getAttribute('accept');
      if (accept) info.accept = accept;
      try {
        if (el.files && el.files.length) {
          for (let i = 0; i < el.files.length; i++) {
            const name = (el.files[i].name || '').slice(0, LABEL_CAP);
            info.current.push(name);
          }
        }
      } catch (_) {}
      return info;
    }
    return null;
  }
  if (tag === 'SELECT') {
    const multiple = !!el.multiple;
    const options = [];
    const selectedLabels = [];
    let total = 0;
    try {
      const opts = el.options || [];
      total = opts.length;
      // Walk ALL options so `current` reflects selections that sit beyond the
      // serialization cap. Only the first OPTS_CAP entries get pushed into
      // options[]; anything past the cap still contributes to selectedLabels
      // so agents see the true current state of big dropdowns.
      for (let i = 0; i < opts.length; i++) {
        const o = opts[i];
        const labelRaw = (o.label != null && o.label !== '') ? o.label : (o.text || '');
        const label = String(labelRaw).trim().slice(0, LABEL_CAP);
        if (i < OPTS_CAP) {
          const entry = { label: label, value: o.value, selected: !!o.selected };
          if (o.disabled) entry.disabled = true;
          options.push(entry);
        }
        if (o.selected) selectedLabels.push(label);
      }
    } catch (_) {}
    return {
      control: 'select',
      multiple: multiple,
      current: multiple ? selectedLabels : (selectedLabels[0] || ''),
      options: options,
      options_total: total,
    };
  }
  return null;
}

  const selector = null;
  const maxDepth = 8;
  const maxChildren = 100;
  const maxText = 500;
  let matches;
  if (selector) {
    try { matches = document.querySelectorAll(selector); }
    catch (e) {
      return { selector: selector, invalidSelector: true, reason: (e && e.message) || String(e) };
    }
  } else {
    matches = [document.documentElement];
  }
  const matched = matches.length;
  const root = matches[0] || null;
  const trunc = { depth: false, children_dropped: 0, text_truncated: 0 };
  function serialize(el, depth) {
    if (!el || el.nodeType !== 1) return null;
    const attrs = {};
    for (const a of el.attributes) attrs[a.name] = a.value;
    let text = '';
    for (const n of el.childNodes) {
      if (n.nodeType === 3) text += n.nodeValue;
    }
    text = text.replace(/\s+/g, ' ').trim();
    if (maxText !== null && text.length > maxText) {
      text = text.slice(0, maxText);
      trunc.text_truncated++;
    }
    const children = [];
    if (maxDepth === null || depth < maxDepth) {
      const childEls = [];
      for (const n of el.childNodes) if (n.nodeType === 1) childEls.push(n);
      const keep = maxChildren === null ? childEls.length : Math.min(childEls.length, maxChildren);
      for (let i = 0; i < keep; i++) {
        const child = serialize(childEls[i], depth + 1);
        if (child) children.push(child);
      }
      if (maxChildren !== null && childEls.length > maxChildren) {
        trunc.children_dropped += childEls.length - maxChildren;
      }
    } else {
      // Budget hit: we're at max depth. Count any element children we would have visited.
      for (const n of el.childNodes) if (n.nodeType === 1) { trunc.depth = true; break; }
    }
    const node = { tag: el.tagName.toLowerCase(), attrs, text, children };
    const compound = compoundInfoOf(el);
    if (compound) node.compound = compound;
    return node;
  }
  const tree = root ? serialize(root, 0) : null;
  const truncatedOut = {};
  if (trunc.depth) truncatedOut.depth = true;
  if (trunc.children_dropped > 0) truncatedOut.children_dropped = trunc.children_dropped;
  if (trunc.text_truncated > 0) truncatedOut.text_truncated = trunc.text_truncated;
  const envelope = { selector: selector, matched: matched, tree: tree };
  if (Object.keys(truncatedOut).length > 0) envelope.truncated = truncatedOut;
  return envelope;
})()
