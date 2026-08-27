# Coworker Subsystem Retirement Plan

Date: 2026-08-27
Status: complete
Owner decision: retire the complete Coworker/Case02 subsystem. V3.1 Browser Gateway is the supported browser workflow.

## Decision

Considered options:

1. Retire only the five restricted browser tools. This leaves a nonfunctional Coworker runtime and is rejected.
2. Migrate Coworker to V3.1 browser tools. This duplicates the Browser Gateway workflow and preserves a benchmark that is no longer required; rejected.
3. Keep Coworker as an archived but importable optional extra. This retains dependency, profile, packaging, and test costs; rejected.
4. Retire the complete active subsystem while preserving immutable historical records. This is selected.

The upstream decision is that HomeMaster has one supported browser execution architecture: Browser Gateway/Web Console with the V3.1 browser registry. The old fixed-route, `data-bid` Case02 environment is not a second browser mode.

## Removal Boundary

Delete active Coworker resources:

- `src/homemaster/benchmarking/coworker_demo/`, `src/homemaster/adapters/coworker_entry.py`, and `src/homemaster/cli/coworker_router.py`;
- `apps/case02_openenv/`, `data/coworker_demo/`, `scripts/coworker_demo/`, and Coworker-specific V1.9 release commands;
- Coworker configuration, optional dependency, package data, pytest path/marker, profile and permission entries;
- Coworker-only tests and active user/architecture documentation.

Preserve historical evidence without presenting it as an active capability:

- existing `CHANGELOG.md` entries;
- `docs/pitfalls.md` incident records;
- `docs/reports/2026-07-19-realtime-llm-coworker-acceptance.md`;
- historical plans/specifications and locked V1.9 baseline artifacts.

## Implementation

1. Remove Coworker imports and branches from the common registry, application backend binding, CLI composition, permissions, event projection tests, observation-profile tests, ownership audits, cleanup guards, and release metadata collectors.
2. Delete the closed implementation/resource/test directories and standalone release scripts.
3. Remove active README, user-guide, architecture, dependency, package-data, and test-marker claims.
4. Add a changelog entry explaining that V3.1 Browser Gateway supersedes the retired fixed-route benchmark.
5. Update the live session handoff with the new supported boundary.

## Verification

The retirement is complete only when all gates pass:

1. Repository audit: no non-historical runtime, config, package, script, or test path imports `coworker_demo`, `case02_openenv`, or accepts a Coworker profile.
2. Package audit: wheel metadata has no `coworker` extra and the wheel contains no Coworker/Case02 modules or skills.
3. Runtime return-code gate: CLI help and Browser composition commands return success; an attempted `build_tool_registry(environment="coworker")` is rejected as unsupported.
4. V3.1 registry gate: default Browser session exposes the exact 27 safe tools, excludes `observe` and unauthorized `browser_eval`, and closes the run-owned session.
5. Regression gate: focused registry, application, CLI, permission, packaging, and V3.1 Browser tests pass; full non-live tests are run when feasible.

No historical Coworker run is reclassified or deleted by this change.

## Verification Results

- Active runtime/config/script/test/documentation audit: no Coworker or Case02 references outside the intentional
  rejection test and preserved historical evidence.
- Focused regression gate: 93 passed, with the pre-existing frozen upstream-port count assertion deselected.
- Package gate: clean sdist and wheel built; wheel exposes `browser`, binds Playwright to that extra, and contains no
  Coworker/Case02 path.
- Fresh-install gate: the wheel's `[browser]` extra installed 132 packages into an empty Python 3.11 environment;
  Playwright 1.62.0 imported from outside the checkout and installed CLI help returned success.
- Runtime gate: installed code exposed the exact 27 safe V3.1 Browser tools, excluded `observe` and unauthorized
  `browser_eval`, rejected the retired Coworker environment, and closed the run-owned session.
- Repository gates: focused Ruff and `git diff --check` pass. No commit was requested or created.
