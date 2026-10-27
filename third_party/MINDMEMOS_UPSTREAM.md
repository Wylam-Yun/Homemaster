# MindMemOS upstream pin (V3.8)

- Upstream repository: https://github.com/mindscale-noah/MindMemOS.git
- Development checkout: `../MindMemOS` (branch `feature/v3.8-native-lifecycle`)
- Audited baseline commit: `186db4a75122b1d8691933f280bec10191c82c28`
- V3.8 implementation commit: `e0797b9` (branch
  `feature/v3.8-native-lifecycle` — operation ledger, idempotent add,
  skill traces, feedback windows, dreaming seeds), mirrored by the
  `mindmemos-sdk` source override in `pyproject.toml`
  (`tool.uv.sources`).

The vendored `third_party/MindMemOS/` copy was removed in V3.8: HomeMaster no
longer embeds the `mindmemos` server package. Runtime access goes through
`mindmemos_sdk` against a standalone MindMemOS HTTP server
(`memory.mindmemos.base_url`). Before release, repoint the source override at
`{ git = "<upstream>", rev = "<pinned commit>" }` or the published
`mindmemos-sdk` release that contains the V3.8 endpoints.
