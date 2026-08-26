"""HomeMaster owner adapter for vendored OpenCLI page-representation algorithms."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homemaster.browser.opencli_scripts import load_opencli_script


class OpenCLIPageAdapter:
    """Execute pure generated algorithms inside one HomeMaster-owned Playwright page."""

    def __init__(self, page: Any) -> None:
        self._page = page

    async def dom_snapshot(self, *, interactive_only: bool) -> str:
        name = "dom_snapshot_interactive.js" if interactive_only else "dom_snapshot_full.js"
        value = await self._page.evaluate(load_opencli_script(name))
        return str(value)

    async def form_state(self) -> Mapping[str, object]:
        value = await self._page.evaluate(load_opencli_script("form_state.js"))
        return dict(value) if isinstance(value, Mapping) else {"value": value}

    async def cleaned_html(self, root: Any | None = None) -> Mapping[str, object]:
        if root is None:
            value = await self._page.evaluate(load_opencli_script("extract_html.js"))
        else:
            value = await root.evaluate(
                """el => {
                  const clone = el.cloneNode(true);
                  const drop = [
                    'script','style','noscript','template','nav','header','footer','aside',
                    'iframe','svg','canvas','form','button','input','select','textarea',
                    '[role=navigation]','[role=banner]','[role=contentinfo]',
                    '[role=complementary]','[aria-hidden=true]'
                  ];
                  for (const q of drop) for (const node of clone.querySelectorAll(q)) node.remove();
                  const walker = document.createTreeWalker(clone, NodeFilter.SHOW_ELEMENT);
                  let node = walker.currentNode;
                  while (node) {
                    for (const attr of [...node.attributes]) {
                      if (attr.name.startsWith('on') || attr.name === 'style' ||
                          attr.name.startsWith('data-')) node.removeAttribute(attr.name);
                    }
                    node = walker.nextNode();
                  }
                  return {
                    ok:true, url:location.href, title:document.title || '',
                    html:clone.outerHTML || ''
                  };
                }"""
            )
        return dict(value) if isinstance(value, Mapping) else {"value": value}

    async def html_tree(
        self,
        root: Any | None = None,
        *,
        depth: int = 8,
        children_max: int = 100,
        text_max: int = 500,
    ) -> Mapping[str, object]:
        if root is None and (depth, children_max, text_max) == (8, 100, 500):
            value = await self._page.evaluate(load_opencli_script("html_tree.js"))
            return dict(value) if isinstance(value, Mapping) else {"value": value}
        root = root or await self._page.query_selector("html")
        if root is None:
            return {"matched": 0, "tree": None}
        value = await root.evaluate(
            """(root, opts) => {
              const trunc = {depth:false, children_dropped:0, text_truncated:0};
              function compound(el) {
                const tag = el.tagName.toLowerCase();
                const type = (el.getAttribute('type') || '').toLowerCase();
                if (tag === 'select') return {
                  control:'select', multiple:Boolean(el.multiple),
                  current:Array.from(el.selectedOptions).map(o => o.label || o.textContent || ''),
                  options:Array.from(el.options).slice(0, 50).map(o => ({
                    label:(o.label || o.textContent || '').trim(), value:o.value,
                    selected:Boolean(o.selected), disabled:Boolean(o.disabled)
                  })), options_total:el.options.length
                };
                const temporal = ['date','time','datetime-local','month','week'];
                if (tag === 'input' && temporal.includes(type)) {
                  return {
                    control:type, current:String(el.value || ''),
                    min:el.min || '', max:el.max || ''
                  };
                }
                if (tag === 'input' && type === 'file') return {
                  control:'file', multiple:Boolean(el.multiple), accept:el.accept || '',
                  current:Array.from(el.files || []).map(file => file.name)
                };
                return null;
              }
              function serialize(el, level) {
                if (!el || el.nodeType !== 1) return null;
                const attrs = {};
                for (const attr of el.attributes) attrs[attr.name] = attr.value;
                let text = Array.from(el.childNodes).filter(n => n.nodeType === 3)
                  .map(n => n.nodeValue || '').join('').replace(/\\s+/g, ' ').trim();
                if (text.length > opts.textMax) {
                  text = text.slice(0, opts.textMax); trunc.text_truncated++;
                }
                const children = [];
                const childElements = Array.from(el.children);
                if (level < opts.depth) {
                  for (const child of childElements.slice(0, opts.childrenMax)) {
                    const item = serialize(child, level + 1); if (item) children.push(item);
                  }
                  trunc.children_dropped += Math.max(0, childElements.length - opts.childrenMax);
                } else if (childElements.length) trunc.depth = true;
                const out = {tag:el.tagName.toLowerCase(), attrs, text, children};
                const info = compound(el); if (info) out.compound = info;
                return out;
              }
              const truncated = {};
              const tree = serialize(root, 0);
              if (trunc.depth) truncated.depth = true;
              if (trunc.children_dropped) truncated.children_dropped = trunc.children_dropped;
              if (trunc.text_truncated) truncated.text_truncated = trunc.text_truncated;
              const out = {selector:null, matched:1, tree};
              if (Object.keys(truncated).length) out.truncated = truncated;
              return out;
            }""",
            {"depth": depth, "childrenMax": children_max, "textMax": text_max},
        )
        return dict(value) if isinstance(value, Mapping) else {"value": value}

    async def ax_snapshot(self, *, interactive_only: bool = False, root: Any | None = None) -> str:
        if root is not None:
            return str(
                await root.evaluate(
                    """(root, interactiveOnly) => {
                      const interactive = new Set([
                        'button','link','textbox','searchbox','checkbox','radio',
                        'combobox','listbox','menuitem','option','switch','tab'
                      ]);
                      const implicit = (el) => {
                        const tag = el.tagName.toLowerCase();
                        const type = (el.getAttribute('type') || '').toLowerCase();
                        if (tag === 'button') return 'button';
                        if (tag === 'a' && el.hasAttribute('href')) return 'link';
                        if (tag === 'textarea') return 'textbox';
                        if (tag === 'select') return 'combobox';
                        if (tag === 'input' && type === 'checkbox') return 'checkbox';
                        if (tag === 'input' && type === 'radio') return 'radio';
                        if (tag === 'input') return 'textbox';
                        return 'generic';
                      };
                      const nameOf = (el) => {
                        const labelled = (el.getAttribute('aria-labelledby') || '')
                          .split(/\\s+/).filter(Boolean)
                          .map(id => document.getElementById(id))
                          .filter(Boolean).map(x => x.innerText || x.textContent || '')
                          .join(' ').trim();
                        const label = el.labels
                          ? Array.from(el.labels)
                            .map(x => x.innerText || x.textContent || '').join(' ')
                          : '';
                        return (
                          el.getAttribute('aria-label') || labelled || label ||
                          el.getAttribute('placeholder') || el.innerText || el.textContent || ''
                        ).trim();
                      };
                      const lines = ['source: ax', '---'];
                      const render = (el, depth) => {
                        if (!el || el.nodeType !== 1) return;
                        const style = getComputedStyle(el);
                        if (style.display === 'none' || style.visibility === 'hidden') return;
                        const role = el.getAttribute('role') || implicit(el);
                        const name = nameOf(el);
                        const value = 'value' in el ? String(el.value || '') : '';
                        if (!interactiveOnly || interactive.has(role) || name || value) {
                          const indent = '  '.repeat(depth);
                          const details = [role];
                          if (name) details.push(JSON.stringify(name.slice(0, 200)));
                          if (value && value !== name) {
                            details.push(`value=${JSON.stringify(value.slice(0, 200))}`);
                          }
                          lines.push(`${indent}- ${details.join(' ')}`);
                        }
                        for (const child of el.children) render(child, depth + 1);
                      };
                      render(root, 0);
                      return lines.join('\\n');
                    }""",
                    bool(interactive_only),
                )
            )
        session = await self._page.context.new_cdp_session(self._page)
        try:
            payload = await session.send("Accessibility.getFullAXTree")
        finally:
            await session.detach()
        nodes = payload.get("nodes", [])
        by_id = {node.get("nodeId"): node for node in nodes if node.get("nodeId")}
        child_ids = {child for node in nodes for child in node.get("childIds", [])}
        roots = [node for node in nodes if node.get("nodeId") not in child_ids]
        lines = ["source: ax", "---"]
        interactive = {
            "button",
            "link",
            "textbox",
            "searchbox",
            "checkbox",
            "radio",
            "combobox",
            "listbox",
            "menuitem",
            "option",
            "slider",
            "spinbutton",
            "switch",
            "tab",
            "treeitem",
        }

        def value(node: Mapping[str, object], key: str) -> str:
            raw = node.get(key)
            if isinstance(raw, Mapping):
                raw = raw.get("value", "")
            return str(raw or "").strip()

        def render(node: Mapping[str, object], depth: int) -> None:
            role = value(node, "role") or "generic"
            name = value(node, "name")
            node_value = value(node, "value")
            ignored = bool(node.get("ignored"))
            show = not ignored and (
                role in interactive or (not interactive_only and (name or node_value))
            )
            if show:
                parts = [role]
                if name:
                    parts.append(json_quote(name[:200]))
                if node_value and node_value != name and role != "password":
                    parts.append(f"value={json_quote(node_value[:200])}")
                lines.append(f"{'  ' * min(depth, 50)}{' '.join(parts)}")
            for child_id in node.get("childIds", []):
                child = by_id.get(child_id)
                if child is not None:
                    render(child, depth if ignored else depth + 1)

        for root in roots[:10]:
            render(root, 0)
        lines.extend(("---", f"nodes: {len(nodes)}"))
        return "\n".join(lines)


def json_quote(value: str) -> str:
    import json

    return json.dumps(value, ensure_ascii=False)


__all__ = ["OpenCLIPageAdapter"]
