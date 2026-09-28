# Optional ALFWorld Doctor Checks

## Context

ALFWorld is an optional benchmark capability. The base HomeMaster agent,
memory, browser, and gateway runtime must install and pass health checks without
an ALFWorld checkout or worker environment. ALFWorld remains an isolated,
explicitly installed capability through `scripts/setup-alfworld.sh`.

## Decision

Keep one `doctor` command and add an explicit `--alfworld` opt-in:

```bash
scripts/homemaster doctor --json
scripts/homemaster doctor --alfworld --json
```

The default command runs only generic checks. The opt-in command runs the
generic checks plus the ALFWorld binding/import and isolated worker IPC checks.

The benchmark commands continue to fail closed when the ALFWorld binding is
missing and point to `scripts/setup-alfworld.sh`; benchmark behavior is not
silently changed by this doctor option.

The misleading `pyproject.toml` `alfworld` extra is removed. It only exposed
`pyyaml`, which is already a base dependency and does not install the isolated
ALFWorld worker environment. The supported ALFWorld installation path remains
the dedicated setup script and `config/alfworld/requirements.lock`.

## Interface and behavior

- `run_doctor(*, live=False, alfworld=False)` controls inclusion of optional
  checks.
- `doctor --alfworld` is valid with both text and JSON output.
- `doctor --json` without `--alfworld` must not read or require
  `.runtime/alfworld-binding.json`.
- `doctor --alfworld --json` reports `alfworld_binding` and
  `alfworld_worker_ipc`; missing or invalid bindings return a non-zero CLI
  status.
- Interactive shell and internal doctor callers retain generic behavior by
  using the default `alfworld=False`.

## Verification

Tests must cover the external boundary at the CLI/report level:

1. With no ALFWorld binding, generic doctor has no ALFWorld checks and exits
   successfully when the generic runtime is valid.
2. With no ALFWorld binding, opt-in doctor includes the two ALFWorld checks and
   exits non-zero with the setup instruction.
3. With a valid binding, opt-in doctor runs the real binding and worker checks;
   existing worker IPC assertions remain unchanged.
4. Benchmark launch still rejects a missing binding independently of doctor.
5. The optional dependency table no longer advertises the non-installing
   `alfworld` extra, and setup documentation shows the two-step base/optional
   flow.

The implementation must preserve the existing generic doctor checks and must
not import or initialize ALFWorld from the base runtime path.
