#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

RUNTIME_ROOT="${HOMEMASTER_RUNTIME_ROOT:-var/homemaster/runs}"
DEBUG_ROOT="${HOMEMASTER_DEBUG_ROOT:-tests/homemaster/llm_cases}"
LIVE_FLAG="${HOMEMASTER_LIVE_FLAG:---live-models}"

declare -A UTTERANCES=(
  [check_medicine_success]="去厨房看看药盒是不是还在。"
  [check_medicine_stale_recover]="去桌子那边看看药盒是不是还在。"
  [fetch_cup_retry]="去厨房找水杯，然后拿给我"
  [object_not_found]="去厨房找水杯，然后拿给我"
  [distractor_rejected]="去厨房找水杯，然后拿给我"
)

for scenario in \
  check_medicine_success \
  check_medicine_stale_recover \
  fetch_cup_retry \
  object_not_found \
  distractor_rejected
do
  run_id="stage07-${scenario}"
  echo "==> ${scenario}"
  PYTHONPATH=src .venv/bin/python -m homemaster.cli run \
    --utterance "${UTTERANCES[$scenario]}" \
    --scenario "$scenario" \
    --run-id "$run_id" \
    --runtime-memory-root "$RUNTIME_ROOT" \
    --debug-root "$DEBUG_ROOT" \
    "$LIVE_FLAG" \
    --mock-skills
done
