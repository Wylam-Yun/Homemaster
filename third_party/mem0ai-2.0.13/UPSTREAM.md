# Vendored mem0ai 2.0.13

HomeMaster distributes the complete `mem0` Python runtime from the proven PyPI wheel
`mem0ai-2.0.13-py3-none-any.whl` under Apache-2.0.

- Wheel SHA-256: `dff29057329370243d88bfccd367deba41c2fb1652f63225a23068cbdd1bc066`
- Source archive SHA-256: `a81446d760ebd30fbfe356b4e3f8e95a1a567dd05e74494071dc7a2340acdcc3`
- Runtime comparison: all 146 `mem0` files in the wheel and source archive are byte-identical.
- Local modification: `mem0/__init__.py` falls back to the locked `2.0.13` version when the intentionally removed
  `mem0ai` distribution metadata is absent. The manifest records both upstream and distributed hashes.

Regenerate the vendored tree from the locked wheel with:

```bash
uv run python scripts/vendor_mem0.py /path/to/mem0ai-2.0.13-py3-none-any.whl
```

Do not format or hand-edit files under `mem0/`. HomeMaster-specific behavior belongs under
`src/homemaster/memory`.
