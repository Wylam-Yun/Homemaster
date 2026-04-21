# Memory-Grounded Embodied Task Brain

HomeMaster 的 Task Brain CLI MVP。

## 快速开始（推荐直接复制执行）

```bash
cd /Users/wylam/Documents/workspace/HomeMaster
uv venv --python 3.11
uv pip install --python .venv/bin/python ".[dev]"
```

## 先确认环境可用

```bash
cd /Users/wylam/Documents/workspace/HomeMaster
PYTHONPATH=src .venv/bin/python -c "import task_brain; print(task_brain.__version__)"
.venv/bin/pytest -q
.venv/bin/ruff check .
```

## 单场景运行（稳定写法）

说明：默认都用 `PYTHONPATH=src .venv/bin/python -m task_brain.cli ...`，避免 `No module named task_brain.cli`。

```bash
cd /Users/wylam/Documents/workspace/HomeMaster
PYTHONPATH=src .venv/bin/python -m task_brain.cli run \
  --scenario check_medicine_success \
  --instruction "去桌子那边看看药盒是不是还在。"
```

核心场景：

```bash
cd /Users/wylam/Documents/workspace/HomeMaster
PYTHONPATH=src .venv/bin/python -m task_brain.cli run --scenario check_medicine_success --instruction "去桌子那边看看药盒是不是还在。"
PYTHONPATH=src .venv/bin/python -m task_brain.cli run --scenario check_medicine_stale_recover --instruction "去桌子那边看看药盒是不是还在。"
PYTHONPATH=src .venv/bin/python -m task_brain.cli run --scenario fetch_cup_retry --instruction "去厨房找水杯，然后拿给我"
```

Hardening 场景（预期失败但可解释）：

```bash
cd /Users/wylam/Documents/workspace/HomeMaster
PYTHONPATH=src .venv/bin/python -m task_brain.cli run --scenario object_not_found --instruction "去厨房找水杯，然后拿给我"
PYTHONPATH=src .venv/bin/python -m task_brain.cli run --scenario distractor_rejected --instruction "去厨房找水杯，然后拿给我"
```

## API 场景批量测试 + 运行记录（推荐）

先配置 API 文件（默认从这个文件读取）：

```bash
cd /Users/wylam/Documents/workspace/HomeMaster
# 直接编辑这个文件即可
vim config/nvidia_api_config.json
```

`config/nvidia_api_config.json` 的结构（3 组 provider，可不同 base_url/model/key）：

```json
{
  "providers": [
    {
      "name": "provider_group_1",
      "base_url": "<NVIDIA_BASE_URL_1>",
      "model": "<NVIDIA_MODEL_1>",
      "api_keys": ["<NVIDIA_API_KEY_1>"]
    },
    {
      "name": "provider_group_2",
      "base_url": "<NVIDIA_BASE_URL_2>",
      "model": "<NVIDIA_MODEL_2>",
      "api_keys": ["<NVIDIA_API_KEY_2>"]
    },
    {
      "name": "provider_group_3",
      "base_url": "<NVIDIA_BASE_URL_3>",
      "model": "<NVIDIA_MODEL_3>",
      "api_keys": ["<NVIDIA_API_KEY_3>"]
    }
  ]
}
```

按顺序 fallback：group1 -> group2 -> group3（每组内部也支持多 key 顺序重试）。

把下面脚本保存后运行，会自动记录每个场景的 `stdout`、`trace jsonl` 和汇总表：

```bash
cd /Users/wylam/Documents/workspace/HomeMaster

cat > run_api_scenarios.sh <<'BASH'
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
BASH

chmod +x run_api_scenarios.sh
bash run_api_scenarios.sh
```

## 测试命令（按验收顺序）

```bash
cd /Users/wylam/Documents/workspace/HomeMaster
.venv/bin/pytest tests/test_stage_acceptance_matrix.py tests/test_llm_prompt_contract.py tests/test_cli_phase_b_visibility.py -q
.venv/bin/pytest tests/test_kimi_provider.py tests/test_phase_b_memory_planner.py -q
.venv/bin/pytest -q
.venv/bin/ruff check .
```

## Prompt 说明（API 调用）

Prompt 已经实现，不需要你额外手写：

- 规划 Prompt 生成：`src/task_brain/planner.py` 的 `LLMHighLevelPlanner._build_prompt(...)`
- API 调用消息体：`src/task_brain/llm.py` 的 `KimiPlanProvider.generate_plan(...)`（system + user）
- API key 读取：`src/task_brain/llm.py` 默认 `from_config_file(...)`，按 `providers` 顺序 fallback

## 常见问题

- `No module named task_brain.cli`：
  - 用 README 里的稳定写法：`PYTHONPATH=src .venv/bin/python -m task_brain.cli ...`
- `zsh: parse error near ')'`：
  - 不要直接粘贴半截多行循环命令。
  - 用上面的 `run_api_scenarios.sh` 文件方式执行。
