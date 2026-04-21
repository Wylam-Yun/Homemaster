#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="/Users/wylam/Documents/workspace/HomeMaster"
cd "$ROOT_DIR"

RUN_DIR="artifacts/api_runs/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$RUN_DIR"

REAL_CONFIG_PATH="$ROOT_DIR/config/nvidia_api_config.json"
OFFLINE_CONFIG_PATH="$RUN_DIR/nvidia_api_config.offline.json"

log() {
  printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*"
}

mark() {
  printf '[%s] [%s] %s\n' "$(date +%H:%M:%S)" "$1" "$2"
}

# 新的进度显示函数
progress() {
    local status=$1
    local current=$2
    local total=$3
    local desc=$4

    case "$status" in
        start)
            printf "\r[%s] ► %s..." "$(date +%H:%M:%S)" "$desc"
            ;;
        update)
            printf "\r[%s] ► %s... (%d/%d)" "$(date +%H:%M:%S)" "$desc" "$current" "$total"
            ;;
        done)
            printf "\r[%s] ✓ %s%-30s\n" "$(date +%H:%M:%S)" "$desc" " "
            ;;
        fail)
            printf "\r[%s] ✗ %s%-30s\n" "$(date +%H:%M:%S)" "$desc" " "
            ;;
    esac
}

on_interrupt() {
  mark FAIL "Run interrupted. Partial artifacts saved at: $RUN_DIR"
}
trap on_interrupt INT TERM

printf "run_dir\t%s\n" "$RUN_DIR" > "$RUN_DIR/run_meta.tsv"
printf "started_at_utc\t%s\n" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$RUN_DIR/run_meta.tsv"
printf "python\t%s\n" "$(python -V 2>&1)" >> "$RUN_DIR/run_meta.tsv"

overall_code=0

log "Run started"
mark INFO "RUN_DIR=$RUN_DIR"

printf "suite\texit_code\tlog_path\n" > "$RUN_DIR/test_suites.tsv"

# Step 0: API probe matrix (real config)
progress start 0 5 "Step 0/5: Probing API groups/protocols"
set +e
ROOT_DIR="$ROOT_DIR" RUN_DIR="$RUN_DIR" .venv/bin/python - <<'PY' > "$RUN_DIR/api_probe.log" 2>&1
import json
import os
from pathlib import Path
import httpx

root = Path(os.environ["ROOT_DIR"])
run_dir = Path(os.environ["RUN_DIR"])
cfg_path = root / "config" / "nvidia_api_config.json"
out_path = run_dir / "api_probe.tsv"

rows = [("group", "name", "protocol", "status", "base_url", "model", "message")]

try:
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
except Exception as exc:
    rows.append(("-", "-", "-", "EXC", str(cfg_path), "-", f"config_read_error:{exc}"))
    with out_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write("\t".join(row) + "\n")
    raise

providers = cfg.get("providers", [])

for i, p in enumerate(providers, start=1):
    base = str(p.get("base_url", "")).rstrip("/")
    model = str(p.get("model", ""))
    keys = p.get("api_keys", [])
    key = keys[0] if isinstance(keys, list) and keys else ""
    name = str(p.get("name", f"provider_group_{i}"))

    tests = [
        (
            "openai",
            f"{base}/v1/chat/completions",
            {"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            {
                "model": model,
                "messages": [{"role": "user", "content": '{"ping":"pong"}'}],
                "temperature": 0,
                "max_tokens": 16,
            },
        ),
        (
            "anthropic",
            f"{base}/v1/messages",
            {
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            {
                "model": model,
                "messages": [{"role": "user", "content": 'Return JSON only: {"ping":"pong"}'}],
                "temperature": 0,
                "max_tokens": 16,
            },
        ),
    ]

    with httpx.Client(timeout=15.0) as client:
        for protocol, url, headers, payload in tests:
            try:
                r = client.post(url, headers=headers, json=payload)
                msg = (r.text or "").replace("\n", " ")[:140]
                rows.append((str(i), name, protocol, str(r.status_code), base, model, msg))
            except Exception as exc:
                rows.append((str(i), name, protocol, "EXC", base, model, str(exc)[:140]))

with out_path.open("w", encoding="utf-8") as f:
    for row in rows:
        f.write("\t".join(row) + "\n")

print(f"api_probe_tsv={out_path}")
for row in rows[1:]:
    print(f"group={row[0]} protocol={row[2]} status={row[3]} model={row[5]} msg={row[6]}")
PY
api_probe_code=$?
set -e

if [ "$api_probe_code" -eq 0 ]; then
  mark PASS "API probe finished (see $RUN_DIR/api_probe.tsv)"
else
  mark FAIL "API probe failed (see $RUN_DIR/api_probe.log)"
  overall_code=1
fi
printf "api_probe\t%s\t%s\n" "$api_probe_code" "$RUN_DIR/api_probe.log" >> "$RUN_DIR/test_suites.tsv"

# Step 1: full pytest excluding live_api, with offline llm config, file-by-file progress
mark INFO "Step 1/5: Running pytest (not live_api) file-by-file with offline LLM config..."
cat > "$OFFLINE_CONFIG_PATH" <<'EOF'
{
  "providers": [
    {
      "name": "offline_placeholder",
      "base_url": "<OFFLINE_BASE_URL>",
      "model": "<OFFLINE_MODEL>",
      "api_keys": ["<OFFLINE_API_KEY>"]
    }
  ]
}
EOF

while IFS= read -r test_file; do
  suite_name="$(basename "$test_file")"
  suite_log="$RUN_DIR/pytest_${suite_name}.log"
  mark INFO "Pytest file start: $suite_name"

  set +e
  if [ "$suite_name" = "test_kimi_provider.py" ]; then
    PYTHONPATH=src .venv/bin/pytest -q -m "not live_api" "$test_file" 2>&1 | tee "$suite_log"
  else
    TASK_BRAIN_API_CONFIG_PATH="$OFFLINE_CONFIG_PATH" \
      PYTHONPATH=src .venv/bin/pytest -q -m "not live_api" "$test_file" 2>&1 | tee "$suite_log"
  fi
  code=${PIPESTATUS[0]}
  set -e

  printf "%s\t%s\t%s\n" "$suite_name" "$code" "$suite_log" >> "$RUN_DIR/test_suites.tsv"
  if [ "$code" -eq 0 ]; then
    mark PASS "Pytest file passed: $suite_name"
  else
    mark FAIL "Pytest file failed: $suite_name (log: $suite_log)"
    overall_code=1
  fi
done < <(find tests -maxdepth 1 -name 'test_*.py' | sort)

# Step 2: Scenario CLI matrix (real config)
mark INFO "Step 2/5: Running CLI scenario matrix with real config..."
cat > "$RUN_DIR/scenario_list.tsv" <<'EOF'
scenario	instruction	expected_status
check_medicine_success	去桌子那边看看药盒是不是还在。	success
check_medicine_stale_recover	去桌子那边看看药盒是不是还在。	success
fetch_cup_retry	去厨房找水杯，然后拿给我	success
object_not_found	去厨房找水杯，然后拿给我	failed
distractor_rejected	去厨房找水杯，然后拿给我	failed
EOF

printf "scenario\tinstruction\texit_code\tfinal_status\texpected_status\tstatus_match\tllm_error_count\tllm_fallback_count\tstdout_log\ttrace_jsonl\tstarted_at\tended_at\n" > "$RUN_DIR/scenario_run_summary.tsv"

while IFS=$'\t' read -r scenario instruction expected_status; do
  if [ "$scenario" = "scenario" ]; then
    continue
  fi

  mark INFO "Scenario start: $scenario (expected=$expected_status)"
  started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  stdout_log="$RUN_DIR/${scenario}.stdout.log"
  trace_jsonl="$RUN_DIR/${scenario}.trace.jsonl"

  set +e
  PYTHONPATH=src .venv/bin/python -m task_brain.cli run \
    --scenario "$scenario" \
    --instruction "$instruction" \
    --trace-jsonl "$trace_jsonl" > "$stdout_log" 2>&1
  code=$?
  set -e

  if [ "$code" -eq 0 ]; then
    final_status="success"
  else
    final_status="failed"
  fi

  if [ "$final_status" = "$expected_status" ]; then
    status_match="yes"
    mark PASS "Scenario $scenario => $final_status (matched expectation)"
  else
    status_match="no"
    mark FAIL "Scenario $scenario => $final_status (expected $expected_status). log=$stdout_log"
    overall_code=1
  fi

  llm_error_count=0
  llm_fallback_count=0
  if [ -f "$trace_jsonl" ]; then
    llm_error_count="$(grep -c 'llm_planner_error' "$trace_jsonl" || true)"
    llm_fallback_count="$(grep -c 'llm_planner_fallback' "$trace_jsonl" || true)"
  fi

  mark INFO "Scenario $scenario diagnostics: llm_error=$llm_error_count, llm_fallback=$llm_fallback_count"
  if [ -f "$trace_jsonl" ]; then
    rg -n 'call_llm_planner|llm_planner_error|llm_planner_fallback' "$trace_jsonl" | sed 's/^/[TRACE] /' || true
  fi

  ended_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
    "$scenario" "$instruction" "$code" "$final_status" "$expected_status" "$status_match" \
    "$llm_error_count" "$llm_fallback_count" "$stdout_log" "$trace_jsonl" "$started_at" "$ended_at" \
    >> "$RUN_DIR/scenario_run_summary.tsv"
done < "$RUN_DIR/scenario_list.tsv"

# Step 3: live_api retry (max 3)
mark INFO "Step 3/5: Running live_api test (max 3 attempts)..."
NVIDIA_API_KEY_FROM_CONFIG="$(PYTHONPATH=src .venv/bin/python - <<'PY'
import json
from pathlib import Path
cfg = json.loads(Path('config/nvidia_api_config.json').read_text(encoding='utf-8'))
providers = cfg.get('providers', [])
key = ''
if len(providers) >= 3:
    keys = providers[2].get('api_keys', [])
    if keys:
        key = keys[0]
print(key)
PY
)"

if [ -n "$NVIDIA_API_KEY_FROM_CONFIG" ]; then
  export NVIDIA_API_KEY="$NVIDIA_API_KEY_FROM_CONFIG"
  mark INFO "NVIDIA_API_KEY loaded from provider_group_3"
else
  mark FAIL "No key found in provider_group_3; live_api likely to fail"
fi

printf "attempt\texit_code\tstarted_at\tended_at\tlog_path\n" > "$RUN_DIR/live_api_attempts.tsv"
live_ok=0
for i in 1 2 3; do
  mark INFO "live_api attempt $i/3"
  started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  log_file="$RUN_DIR/live_api_attempt_${i}.log"

  set +e
  PYTHONPATH=src .venv/bin/pytest tests/test_kimi_provider.py -m live_api -q 2>&1 | tee "$log_file"
  code=${PIPESTATUS[0]}
  set -e

  ended_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf "%s\t%s\t%s\t%s\t%s\n" "$i" "$code" "$started_at" "$ended_at" "$log_file" >> "$RUN_DIR/live_api_attempts.tsv"

  if [ "$code" -eq 0 ]; then
    mark PASS "live_api succeeded on attempt $i"
    live_ok=1
    break
  fi

  mark FAIL "live_api attempt $i failed (log: $log_file)"
  sleep 2
done

if [ "$live_ok" -ne 1 ]; then
  mark FAIL "live_api failed after 3 attempts"
  overall_code=1
fi

# Step 4: Ruff
mark INFO "Step 4/5: Running ruff check..."
set +e
.venv/bin/ruff check . 2>&1 | tee "$RUN_DIR/ruff.log"
ruff_code=${PIPESTATUS[0]}
set -e
printf "ruff\t%s\t%s\n" "$ruff_code" "$RUN_DIR/ruff.log" >> "$RUN_DIR/test_suites.tsv"

if [ "$ruff_code" -eq 0 ]; then
  mark PASS "ruff check passed"
else
  mark FAIL "ruff check failed (log: $RUN_DIR/ruff.log)"
  overall_code=1
fi

printf "ended_at_utc\t%s\n" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$RUN_DIR/run_meta.tsv"
printf "overall_code\t%s\n" "$overall_code" >> "$RUN_DIR/run_meta.tsv"

log "Run finished"
mark INFO "Artifacts directory: $RUN_DIR"

printf "\n=== api_probe.tsv ===\n"
cat "$RUN_DIR/api_probe.tsv" || true
printf "\n=== scenario_run_summary.tsv ===\n"
cat "$RUN_DIR/scenario_run_summary.tsv"
printf "\n=== live_api_attempts.tsv ===\n"
cat "$RUN_DIR/live_api_attempts.tsv"
printf "\n=== run_meta.tsv ===\n"
cat "$RUN_DIR/run_meta.tsv"

if [ "$overall_code" -eq 0 ]; then
  mark PASS "All checks passed"
else
  mark FAIL "Some checks failed. See logs under $RUN_DIR"
fi

exit "$overall_code"
