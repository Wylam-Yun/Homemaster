# V3.5 Phase 4 Evidence

The versioned NDJSON worker protocol and artifact digest checks are present locally.
Protocol/worker isolation tests pass as part of the focused suite. Remote doctor and
worker fixture checks pass for worker identity, reset/act/close return codes, PNG
digest readback, stderr, and process cleanup. The general runtime and ALFWorld
worker bindings are installed in their separate environments; the remaining release
gap is a clean Linux sync/build run, not the IPC implementation.
