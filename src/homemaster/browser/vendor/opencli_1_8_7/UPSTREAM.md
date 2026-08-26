# OpenCLI browser vendor provenance

- Package: `@jackwener/opencli`
- Version: `1.8.7`
- Repository: `https://github.com/jackwener/opencli`
- Git tag/commit: `v1.8.7` / `87b60a36590c3e2a466c37266c3348d73d7f68fe`
- Source path: `/data1/haodong2/.nvm/versions/node/v22.22.1/lib/node_modules/@jackwener/opencli`
- Copied on: `2026-08-25`
- Node used for source/version verification: `v22.22.1`
- License: Apache-2.0 (see `LICENSE`)
- Runtime boundary: HomeMaster may use browser algorithms through its adapter; the OpenCLI daemon,
  Extension, existing-profile takeover, tab lease, CLI owner, and Node process lifecycle are not
  imported or started by HomeMaster.

The vendor tree mirrors the relative paths of the complete ESM dependency closure reached from the
selected browser modules and their 27 upstream browser test files. It also retains each production
Node dependency with package metadata and license. The npm 1.8.7 tarball omits three files read by
`article-extract.e2e.test.js`; their unmodified copies come from the matching Git tag/commit under
`src/browser/__fixtures__/article-extract/` and are locked in `SHA256SUMS`.

Selected browser capabilities include DOM/AX snapshots, semantic find and target recovery, compound
controls, HTML/form/article extraction, input actions, tabs/dialog/download coordination, console and
network observation, page analysis, shape helpers, errors, and the supporting test/fixture closure.
Files under this directory are not edited in place. HomeMaster behavior differences belong in
`opencli_adapter.py` or the Playwright owner and are recorded in `PATCHES.md`.

Verification on 2026-08-26 used Node `v22.23.2`, Corepack pnpm `10.15.0`, Vitest `4.1.0`, and jsdom
`29.0.2` in an isolated `/tmp` installation. All 27 browser test files and all 406 tests passed. The
installed remote package remained `@jackwener/opencli@1.8.7`; its package and license hashes matched
the locked values.
