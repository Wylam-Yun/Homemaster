# HomeMaster adapter patches

No upstream OpenCLI source file is intentionally modified. HomeMaster-specific behavior is isolated
in the adapter, which owns Playwright session access, policy validation, result normalization, and
external-state verification.

The generated page scripts under `browser/generated/opencli_1_8_7/` are deterministic HomeMaster
build outputs, not patched vendor files. HomeMaster retains one Playwright owner and does not start
OpenCLI's daemon, Extension bridge, profile takeover, tab lease, CLI process lifecycle, or cache under
`~/.opencli`. Safe typed actions remain HomeMaster implementations so policy checks and independent
DOM/file/network terminal-state verification cannot be bypassed. `browser_eval` is a separate gated
capability and is never an implicit action fallback.

The three article-extraction fixtures added from the matching Git commit only repair an omission in
the published npm package's test resources. Their contents are unchanged and do not alter runtime
behavior.
