# MindMemOS vendored copy (V3.8)

- Upstream repository: https://github.com/mindscale-noah/MindMemOS.git
- Vendored snapshot: `a9faf16` on `feature/v3.8-native-lifecycle`
  (V3.8 server-side implementation `e0797b9` — operation ledger,
  idempotent add, skill traces, feedback windows, dreaming seeds —
  plus the HPC3-gateway fixes `494ec7d`, `4f70208`, `bbcb14d`,
  `a9faf16` landed during the hkust4 live-run validation).
- Extracted via `git archive` — tracked files only, no git metadata.
  This snapshot is the authoritative copy of the server we deploy;
  upstream is read-only reference from here on.
- Deployment: standalone MindMemOS HTTP server
  (`memory.mindmemos.base_url`), e.g. on hkust4
  `/data1/haodong2/weilin/red_bird/MindMemOS` (editable venv). The
  server can be re-provisioned from this vendored tree
  (`src/mindmemos`) without cloning upstream.
- Client dependency: `mindmemos-sdk>=0.1.4` resolves from PyPI.
  HomeMaster only uses the generic envelope transport
  (`AsyncHttpTransport`, `post_envelope`/`get_envelope`, `ApiError`,
  `TransportError`); the V3.8 SDK additions are unused by HomeMaster,
  so no source override or git pin is required. The vendored
  `src/mindmemos_sdk` is kept for parity with the server tree, not as
  the dependency source.
