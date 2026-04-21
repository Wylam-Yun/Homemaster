#!/usr/bin/env bash
set -euo pipefail

if [ ! -f "config/nvidia_api_config.json" ]; then
  echo "missing config/nvidia_api_config.json" >&2
  exit 1
fi

RUN_DIR="artifacts/api_runs/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN_DIR"

cat > "$RUN_DIR/scenario_list.tsv" <<'EOF'
check_medicine_success	去桌子那边看看药盒是不是还在。
check_medicine_stale_recover	去桌子那边看看药盒是不是还在。
fetch_cup_retry	去厨房找水杯，然后拿给我
object_not_found	去厨房找水杯，然后拿给我
distractor_rejected	去厨房找水杯，然后拿给我
EOF

printf "scenario\tinstruction\texit_code\tfinal_status\tllm_error_count\tllm_fallback_count\tstdout_log\ttrace_jsonl\tstarted_at\tended_at\n" > "$RUN_DIR/scenario_run_summary.tsv"

while IFS=$'\t' read -r scenario instruction; do
  [ -z "$scenario" ] && continue
  stdout_log="$RUN_DIR/${scenario}.stdout.log"
  trace_jsonl="$RUN_DIR/${scenario}.trace.jsonl"
  started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  set +e
  PYTHONPATH=src .venv/bin/python -m task_brain.cli run \
    --scenario "$scenario" \
    --instruction "$instruction" \
    --trace-jsonl "$trace_jsonl" \
    >"$stdout_log" 2>&1
  exit_code=$?
  set -e

  ended_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  final_status="$(grep -E '^final_status: ' "$stdout_log" | head -n1 | sed 's/^final_status: //')"
  [ -z "$final_status" ] && final_status="unknown"

  if [ -f "$trace_jsonl" ]; then
    llm_error_count="$(grep -c '"event": "llm_planner_error"' "$trace_jsonl" || true)"
    llm_fallback_count="$(grep -c '"event": "llm_planner_fallback"' "$trace_jsonl" || true)"
  else
    llm_error_count=0
    llm_fallback_count=0
  fi

  printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
    "$scenario" "$instruction" "$exit_code" "$final_status" "$llm_error_count" "$llm_fallback_count" \
    "$stdout_log" "$trace_jsonl" "$started_at" "$ended_at" \
    >> "$RUN_DIR/scenario_run_summary.tsv"
done < "$RUN_DIR/scenario_list.tsv"

# live_api 测试仍走环境变量（pytest live marker）
if [ -z "${NVIDIA_API_KEY:-}" ]; then
  echo "NVIDIA_API_KEY is not set (required for live_api pytest)" >&2
  exit 1
fi

printf "attempt\texit_code\tstarted_at\tended_at\tlog_path\n" > "$RUN_DIR/live_api_attempts.tsv"
live_ok=0
for i in 1 2 3; do
  started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  log="$RUN_DIR/live_api_attempt_${i}.log"

  set +e
  PYTHONPATH=src .venv/bin/pytest tests/test_kimi_provider.py -m live_api -q >"$log" 2>&1
  code=$?
  set -e

  ended_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf "%s\t%s\t%s\t%s\t%s\n" "$i" "$code" "$started_at" "$ended_at" "$log" >> "$RUN_DIR/live_api_attempts.tsv"

  if [ "$code" -eq 0 ]; then
    live_ok=1
    break
  fi
  sleep 2
done

echo "RUN_DIR=$RUN_DIR"
cat "$RUN_DIR/scenario_run_summary.tsv"
cat "$RUN_DIR/live_api_attempts.tsv"

if [ "$live_ok" -ne 1 ]; then
  echo "live_api failed after 3 attempts" >&2
  exit 1
fi
