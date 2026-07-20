# Real-Time LLM Observable Demo Plan Review Disposition

## Status

Plan review completed on 2026-07-19. All ten findings are accepted and the
design and implementation plan are corrected before implementation begins.
No second plan review will be started.

## Disposition

| ID | Severity | Disposition | Locked correction |
|---|---|---|---|
| H1 | P0 | Accepted | Replace `sanitize_for_log` free-text detection with a reject-only boundary that checks in-memory configured secrets, known credential/JWT/PEM/signed-URL/sk patterns, and high-entropy tokens; add realistic non-logging tests. |
| H2 | P0 | Accepted | Extend the independent verifier to reconstruct exact normal/anomaly external end states, job return codes, causal linkage, rollback ordering, and grep stdout from raw artifacts; add one-fact mutation tests. |
| H3 | P0 | Accepted | Use the real Planner statuses `pending`, `in_progress`, `completed`, `blocked`, `cancelled`, and `uncertain`; reject nonexistent `failed`. |
| H4 | P1 | Accepted | Store `assistant.reply` immediately as `intermediate`; reducer reclassifies it to `terminal` or `premature` only after authoritative runtime completion and business state. |
| H5 | P1 | Accepted | Persist and verify a key-free provider identity artifact with Mimo, `mimo-v2.5`, HTTPS, expected host, nonsecret config fingerprint, no loopback, and no generated override; fresh provider response/status remains required. |
| H6 | P1 | Accepted | Enumerate every real stable EpisodeError code and safe wrapper family, give each a Chinese label/recovery class, audit coverage, and trigger every safety-relevant code in the controlled gate. |
| H7 | P1 | Accepted | Add server-produced `tool_label_zh` and closed `tool_kind` to v2 action/result events; observer renders `model-output-kind` and never infers business meaning. |
| H8 | P1 | Accepted | Persist recorder UTC/monotonic origins, first-packet/FFmpeg time relationship, event monotonic offsets, settle margin, calculated MP4 offsets, source IDs, and duration bounds. |
| H9 | P1 | Accepted | Create and atomically update `attempt_manifest.json` immediately after run allocation; typed failures carry the path and shell prints it in both success and failure cases. |
| H10 | P2 | Accepted | Pin active Planner item and next focus above the bounded nonactive list; add a 12-item no-scroll screenshot assertion. |

## Implementation Gate

Implementation may begin only after these corrected documents and the matching
CHANGELOG entry are committed. Final acceptance still requires two fresh,
continuous, independently verified real Mimo videos: normal and
post_change_anomaly.
