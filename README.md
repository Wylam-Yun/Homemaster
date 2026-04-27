# HomeMaster V1.2

LLM-first task brain for HomeMaster.

当前主入口是新的 `homemaster` 链路：任务理解、记忆检索、可靠记忆判定、高层编排、mock skill 执行、任务总结和记忆写回。

> 现在的 Stage07 会真实调用 Mimo 和 BGE-M3；navigation / operation / verification 仍是 mock skill，还没有接真实机器人、VLA、VLM。

## 环境配置

```bash
cd /Users/wylam/Documents/workspace/HomeMaster
PYTHONPATH=src .venv/bin/python -c "import homemaster; print(homemaster.__version__)"
```

如果迁移到新机器或新目录，按下面顺序配置：

```bash
cd <HomeMaster 项目目录>

# 推荐使用 uv 创建项目内虚拟环境
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python ".[dev]"

# Stage03 RAG 依赖
uv pip install --python .venv/bin/python "bm25s>=0.2" "jieba>=0.42"

# 验证包能导入
PYTHONPATH=src .venv/bin/python -c "import homemaster, bm25s, jieba; print(homemaster.__version__)"
```

如果机器上没有 `uv`，先安装：

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

API 配置默认从 `config/api_config.json` 读取；如果没有，会兼容读取旧配置文件。不要把真实 key 提交进 git。

配置文件需要包含两个 provider：

- Mimo：用于任务理解、检索 query、编排、总结。
- BGE-M3：用于 `/v1/embeddings` 生成向量。

配置好之后，用 `doctor --live` 检查，不要先直接跑场景。

## 体检

```bash
cd /Users/wylam/Documents/workspace/HomeMaster
PYTHONPATH=src .venv/bin/python -m homemaster.cli doctor --live
```

`doctor --live` 会检查：

- 本地依赖和导入
- API 配置是否可读
- Mimo 最小 JSON 调用
- BGE-M3 `/v1/embeddings` 调用
- runtime memory 目录是否可写

## 跑一个真实场景

水杯场景：

```bash
cd /Users/wylam/Documents/workspace/HomeMaster

PYTHONPATH=src .venv/bin/python -m homemaster.cli run \
  --utterance "去厨房找水杯，然后拿给我" \
  --scenario fetch_cup_retry \
  --run-id live-fetch-cup-001 \
  --runtime-memory-root var/homemaster/runs \
  --debug-root tests/homemaster/llm_cases \
  --live-models \
  --mock-skills
```

药盒场景：

```bash
PYTHONPATH=src .venv/bin/python -m homemaster.cli run \
  --utterance "去厨房看看药盒是不是还在。" \
  --scenario check_medicine_success \
  --run-id live-check-medicine-001 \
  --runtime-memory-root var/homemaster/runs \
  --debug-root tests/homemaster/llm_cases \
  --live-models \
  --mock-skills
```

看结果：

```bash
open tests/homemaster/llm_cases/stage_07/live-fetch-cup-001/result.md
open var/homemaster/runs/live-fetch-cup-001/memory
```

## 跑 5 个验收场景

```bash
cd /Users/wylam/Documents/workspace/HomeMaster
HOMEMASTER_LIVE_FLAG=--live-models ./scripts/run_homemaster_scenarios.sh
```

5 个场景：

- `check_medicine_success`
- `check_medicine_stale_recover`
- `fetch_cup_retry`
- `object_not_found`
- `distractor_rejected`

pytest 版 live matrix：

```bash
HOMEMASTER_RUN_LIVE_LLM=1 HOMEMASTER_RUN_LIVE_EMBEDDING=1 \
PYTHONPATH=src .venv/bin/pytest -q \
tests/homemaster/test_stage_07_scenarios_live.py -m live_api
```

验收矩阵：

```text
plan/V1.2/test_results/stage_07/acceptance_matrix.json
plan/V1.2/test_results/stage_07/scenario_summary.md
```

## 构造新场景

场景目录放在：

```text
data/scenarios/<scenario_name>/
  world.json
  memory.json
  failures.json
```

最快方式：

```bash
cp -R data/scenarios/fetch_cup_retry data/scenarios/my_new_case
```

然后修改：

- `world.json`：真实世界里有哪些房间、观察点、家具锚点、物体。
- `memory.json`：机器人记得目标物可能在哪里。
- `failures.json`：当前主要预留给失败规则；Stage07 里实际失败还由 mock skill 场景逻辑控制。

关键 ID 必须对齐：

- `memory.json.object_memory[].anchor.viewpoint_id` 必须存在于 `world.json.viewpoints`
- `memory.json.object_memory[].anchor.anchor_id` 必须存在于 `world.json.furniture`
- `world.json.furniture[].viewpoint_id` 要和对应 viewpoint 对得上
- 目标物的 `aliases` 或 `object_category` 要能匹配用户指令

临时跑新场景：

```bash
PYTHONPATH=src .venv/bin/python -m homemaster.cli run \
  --utterance "你的用户指令" \
  --scenario my_new_case \
  --run-id live-my-new-case-001 \
  --runtime-memory-root var/homemaster/runs \
  --debug-root tests/homemaster/llm_cases \
  --live-models \
  --mock-skills
```

加入 5 场景矩阵时，更新：

```text
src/homemaster/scenario_runner.py
```

把新场景加入 `STAGE_07_SCENARIOS` 和 `EXPECTED_FINAL_STATUS`。

## 当前边界

- 真实：Mimo、BGE-M3。
- 程序：Stage04 可靠记忆判定、Stage06 记忆写回。
- 模拟：navigation、operation、verification skill。
- 旧 `task_brain` 链路已从当前工程入口中清理；当前只维护 `homemaster` 主链。
