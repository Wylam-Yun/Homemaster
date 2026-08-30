# Retire V1.9 ALFWorld Release Certification

## Decision

Retire the V1.8 ten-trial regression inventory and the V1.9 four-trial release-certification
layer. Keep the current ALFWorld benchmark runner, Gateway, taskset runner, trial-manifest
contract, and external environment verification.

The old release layer is not a separate ALFWorld runtime. It pins a historical ten-trial source,
derives four release trials from it, forces one release profile, and verifies a version-specific
report bundle. Keeping only part of that chain would leave scripts or manifests that can no
longer pass their own source-provenance checks.

## Changes

1. Delete the V1.8 ten-trial and V1.9 derived release manifests.
2. Delete the V1.9 ALFWorld release builder, runner, qualification, verifier, and dedicated tests.
3. Give the current live Gateway test a one-entry smoke manifest under `tests/fixtures/alfworld/`.
4. Remove the retired manifest from baseline capture and require environment-identity callers to
   pass an ALFWorld trial manifest explicitly.
5. Preserve historical plans, reports, hashes, and execution state under `plan/V1.8`, `plan/V1.9`,
   and `docs/reports` as immutable records.

## Invariants

- `homemaster benchmark-alfworld` remains configurable through `--trial-manifest`.
- ALFWorld Gateway still requires an explicit deployment-owned `trial_manifest`.
- `alfworld-trial-selection-v1` validation and dataset byte/scene/goal verification remain.
- No active code or test references the retired v18/v19 manifest paths or release schemas.

## Verification

- Run the generic trial-selection and Gateway tests, collecting the live test even when its
  external environment is unavailable.
- Run the remaining V1.9 utility and frozen-baseline tests.
- Run the cleanup guard, Ruff, compileall, `git diff --check`, and full non-live collection.
- Search active code, tests, scripts, config, and current documentation for retired identifiers.

## Outcome

- Active-reference and deleted-module import searches return no matches outside preserved history.
- The one-entry smoke manifest passed the product loader against the real cached ALFWorld dataset,
  including trial bytes, `FloorPlan10`, goal identity, SaltShaker, and Drawer.
- Full collection succeeds with 1,426 tests. The affected non-live ALFWorld/V1.9 utility gate
  passes with 207 tests and four explicit deselections: one live test plus the already-recorded
  frozen upstream-port count assertion and two inherited ALFWorld runner call-count assertions.
- Cleanup guard (13 tests), focused Ruff, compileall, and `git diff --check` pass.
