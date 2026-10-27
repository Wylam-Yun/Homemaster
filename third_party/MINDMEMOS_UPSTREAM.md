# MindMemOS upstream pin (V3.8)

- Upstream repository: https://github.com/mindscale-noah/MindMemOS.git
- Development checkout: `../MindMemOS` (branch `feature/v3.8-native-lifecycle`)
- Audited baseline commit: `186db4a75122b1d8691933f280bec10191c82c28`
- V3.8 server-side implementation commit: `e0797b9` (branch
  `feature/v3.8-native-lifecycle` — operation ledger, idempotent add,
  skill traces, feedback windows, dreaming seeds). This pins the
  *server* the deployment must run; it is not a Python dependency.
- Client dependency: `mindmemos-sdk>=0.1.4` resolves from PyPI like any
  other package. HomeMaster only uses the generic envelope transport
  (`AsyncHttpTransport`, `post_envelope`/`get_envelope`, `ApiError`,
  `TransportError`); the V3.8 SDK additions (`FeedbackWindow`,
  `OperationStatus`, `skills/cloud.py`) are unused by HomeMaster, so no
  source override or git pin is required.

The vendored `third_party/MindMemOS/` copy was removed in V3.8: HomeMaster
no longer embeds the `mindmemos` server package. Runtime access goes
through `mindmemos_sdk` against a standalone MindMemOS HTTP server
(`memory.mindmemos.base_url`); the server checkout above is only needed to
run that service, and can equally come from the upstream remote once the
feature branch is merged and released.
