# Dreaming Unbounded Wait Design

Date: 2026-08-20
Status: Approved design pending implementation

## Outcome

HomeMaster must not cancel an in-progress Dreaming consolidation because a fixed wall-clock duration elapsed.
Once Dreaming starts, the owning finalization worker waits until the pipeline returns a real result, raises a real
provider/pipeline error, receives an explicit external cancellation, or the process terminates.

This removes the current 300-second `DreamingCoordinator` timeout. It does not invent a larger timeout and does not
reinterpret a timeout as success.

## Evidence and motivation

The ten-episode ALFWorld run `alfworld-v18-10-hkust4-20260820-155826` reached the current 300-second boundary twice.
Both failures occurred while the Dreaming action-planning model response was still being read. HomeMaster cancelled
the coroutine and left the batch pending. A later startup recovery returned `no_action`, but those fast recovery paths
did not repeat the full chat analysis. A wall-clock cancellation therefore makes the local terminal state ambiguous:
the external provider may still have completed work while HomeMaster records a failed attempt.

## Scope

In scope:

- remove the HomeMaster-owned wall-clock timeout around `mindmemos.dream(...)`;
- preserve durable pending/inflight Dreaming state and startup recovery;
- preserve structured started/completed/no-action/failed events and measured duration;
- make tests prove that work lasting beyond the old boundary is awaited rather than cancelled;
- update user, architecture, configuration, README, and changelog documentation where the behavior is visible.

Out of scope:

- changing normal Agent, ALFWorld episode, Provider, embedding, Neo4j, or tool timeouts;
- detaching Dreaming into a new background service or introducing another queue;
- changing the eight-memory Dreaming threshold;
- changing MindMemOS consolidation prompts, safety filters, or action policy;
- retrying provider errors that are returned independently of the removed HomeMaster timeout.

## Runtime design

`DreamingCoordinator.retry_pending()` continues to claim one authoritative batch and emit
`memory.dreaming.started`. It then directly awaits the existing `mindmemos.dream(...)` call without entering
`asyncio.timeout(...)`.

The coordinator keeps its current terminal handling:

- a successful pipeline result records `actions` or `no_action`, completes the claimed batch, and advances the
  watermark;
- a real pipeline/provider exception records `failed`, releases the inflight claim back to pending, and propagates
  the failure to session finalization;
- explicit task cancellation is not converted into success and remains governed by the application/process owner;
- process termination leaves the durable pending state available for startup recovery.

No `dreaming_timeout_seconds` configuration field is added. Infinite waiting is the product behavior, not a hidden
deployment variant. This also avoids maintaining multiple Dreaming modes that differ only in whether they can create
an ambiguous cancellation terminal.

## Operational behavior

Session finalization remains stored-first: the Add record and extracted active memories are committed before
Dreaming. A long Dreaming call can therefore delay completion of that finalization job and graceful shutdown draining,
but it cannot erase the already-stored memories. Operators must use explicit process/task cancellation when they
intend to stop the wait; elapsed time alone is not treated as authority to cancel.

Structured events retain the actual elapsed duration so unusually slow calls remain observable. Logs and metrics may
alert on long duration, but alerts do not mutate or cancel the job.

## Verification

Tests must establish independent behavior at the coordinator boundary:

1. A controlled Dreaming double remains pending past a short interval that would have triggered the old timeout, is
   then released, and completes without cancellation.
2. A real exception from `mindmemos.dream(...)` still records a failed attempt and leaves the batch recoverable.
3. Explicit caller cancellation is not swallowed or converted into `no_action`.
4. Successful `actions` and `no_action` paths retain their existing state transitions and event payloads.
5. Composition constructs the coordinator without a timeout policy, and an audit test prevents production code from
   reintroducing the old `asyncio.timeout` wrapper at this boundary.

Focused tests cover `tests/homemaster/experience/test_dreaming_state.py`, composition/session-finalization integration,
configuration compatibility, and documentation examples. Static checks include Ruff, compileall, and diff checks.

The external terminal gate is a controlled end-to-end finalization run whose Dreaming provider is held longer than a
small historical test boundary and then released. The test must verify the caller is still waiting before release and
that the durable Dreaming state reaches `pending=false` with the expected completed watermark afterward.

## Security note

The investigated third-party DEBUG log contains raw provider credentials and request payloads. That is a separate
security defect: exposed credentials must be rotated and sensitive LiteLLM request logging disabled. The timeout
change must not copy secrets into tests, fixtures, documentation, or changelog entries.
