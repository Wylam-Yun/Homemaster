# HomeMaster V1.2 项目详细Review报告

## 一、执行摘要

### 项目概况
- **项目名称**: HomeMaster V1.2
- **项目类型**: LLM-first 家庭养老场景任务编排系统
- **技术架构**: Pipeline架构，7个Stage串行执行
- **核心技术**: Mimo LLM + BGE-M3 Embedding + BM25 检索
- **代码规模**: 36个核心模块，38个测试文件

### Review结论
项目已完成核心功能实现，测试结果符合预期。主要改进点集中在架构一致性、日志系统、错误处理等工程化方面。

## 二、详细问题分析

### 2.1 架构问题

#### 问题1: 调度过度中心化 + 缺乏统一 Stage 抽象

**现状分析**:

`task_runner.py` 承担了"大总管"角色：直接串联 Stage 02-06，同时管理路径、debug 资产、live/mock 分支、异常处理、结果汇总。项目中不存在通用的 `Stage` 抽象基类，`pipeline.py` 实际上是 Stage 01 合约冒烟测试的运行器，而非通用 pipeline 框架。

各 Stage 使用独立的 frozen dataclass 作为结果类型（`Stage01SmokeResult`、`MemoryRagResult`、`Stage04CaseResult` 等），没有共同基类。模块间的依赖注入通过 `typing.Protocol` 实现：
```python
# memory_rag.py
class MemoryQueryProvider(Protocol): ...
class MemoryEmbeddingProvider(Protocol): ...

# executor.py
class StepDecisionProvider(Protocol): ...

# frontdoor.py
class TaskUnderstandingProvider(Protocol): ...
```

`task_runner.py` 直接导入并串联所有 Stage：
```python
# task_runner.py 顶部导入
from homemaster.frontdoor import understand_task
from homemaster.memory_rag import run_memory_rag
from homemaster.orchestrator import generate_orchestration_plan
from homemaster.executor import execute_stage_05_plan
from homemaster.summary import generate_task_summary
# ... 在 run_homemaster_task() 中按顺序调用
```

当前的调度方式是手动串联，而非：
```python
for stage in stages:
    context = stage.execute(context)
```

**影响评估**:
- 一改模块接口，`task_runner.py` 就要跟着改；一加 Stage，也要改 `task_runner.py`
- 各 Stage 没有统一接口，日志、重试、trace 难以统一处理
- 无法通过配置动态加载或跳过 Stage
- `task_runner.py` 会越来越胖

**改进建议**:
1. 重新定位 `pipeline.py` 为真正的主 pipeline：定义完整 Stage 顺序 + 统一运行机制
2. `task_runner.py` 退化为"启动一次任务"的入口，不再承担编排职责
3. 定义统一的 `Stage` 协议或抽象基类，规范输入/输出契约
4. 使用 StageRegistry 注册所有 Stage，通过配置驱动执行顺序

#### 问题2: 模块间强耦合

**依赖关系图**（基于 `task_runner.py` 实际导入）:
```
task_runner.py (Stage 07)
    ├── contracts.py          (Pydantic 数据契约)
    ├── frontdoor.py          (Stage 02 - understand_task)
    ├── memory_rag.py         (Stage 03 - run_memory_rag)
    │   ├── memory_index.py   (BM25 + embedding cache)
    │   ├── memory_tokenizer.py (Jieba 分词)
    │   └── embedding_client.py (BGE-M3)
    ├── planning_context.py   (Stage 04 - build_planning_context)
    ├── orchestrator.py       (Stage 05 - generate_orchestration_plan)
    ├── executor.py           (Stage 05 - execute_stage_05_plan)
    ├── summary.py            (Stage 06 - generate_task_summary)
    ├── memory_commit.py      (Stage 06 - build_evidence_bundle)
    ├── stage_06.py           (Stage 06 - persist_stage_06_commit)
    ├── runtime.py            (配置加载)
    ├── token_budget.py       (max_tokens 策略)
    └── trace.py              (调试资产写入)
```

**问题点**:
- task_runner.py成为单点故障
- 任何模块接口变更都影响task_runner
- 难以独立测试各Stage

**改进建议**:
- StageRegistry + PipelineContext 解耦（短期，见任务1-2）
- ProviderFactory / SkillRegistry 管插件化（中期，见任务5-6）
- 定义清晰的 Stage 间数据契约（PipelineContext，见任务2）

#### 问题3: Stage01 命名和定位不合理

**现状分析**:

`pipeline.py` 中的 Stage 01 contract smoke 本质是诊断检查（验证 LLM 能否返回合法 JSON、schema 是否匹配），不是正式的业务 Stage。它的功能与 `doctor.py` 中的环境健康检查同类。

当前 `doctor.py` 已有：
- 环境检查（Python 版本、依赖）
- 配置检查（API config 是否存在）

但 contract smoke 被放在了 `pipeline.py` 中作为 "Stage 01"，导致：
- `pipeline.py` 文件名暗示是通用 pipeline 框架，实际只是诊断工具
- Stage 编号从 01 开始计数，但 01 不是业务流程的一部分
- 新人容易误解架构

**改进建议**:

将 Stage 01 contract smoke 并入 doctor 体系：
```bash
homemaster doctor --contract    # 替代 Stage 01 smoke
homemaster doctor --live        # LLM 连通性检查
homemaster doctor --embedding   # Embedding 服务检查
```

释放 `pipeline.py` 用于真正的 pipeline 编排框架。

#### 问题4: Stage05 职责过于混杂

**现状分析**:

Stage 05 当前混合了多个独立关注点，分布在多个文件中：

| 职责 | 文件 | 核心函数 |
|------|------|----------|
| 规划 | `orchestrator.py` | `generate_orchestration_plan()` |
| Step Decision | `skill_selector.py` | `generate_step_decision()` |
| Mock Skill 执行 | `executor.py` | `_run_mock_skill()` |
| 验证 | `verifier.py` | `verify_skill_result()` |
| 失败记录 | `executor.py` | `_append_failure()` |
| 恢复决策 | `recovery.py` | `generate_recovery_decision()` |

`executor.py` 的执行循环（lines 89-184）在一个函数中串联了 step decision → skill 执行 → verification → failure recording，但 recovery.py 的决策结果并未被消费。

**问题**:
- 规划、决策、执行、验证、恢复混在同一个 "Stage" 概念下
- 后续接入 VLN/VLA/VLM 时，需要独立替换验证和执行模块，当前结构不支持
- 难以单独测试某个子环节

**改进建议**:

拆分为独立的子 Stage：
```
PlanningStage       → orchestrator.py
StepDecisionStage   → skill_selector.py
SkillExecutionStage → executor.py (仅执行)
VerificationStage   → verifier.py (独立调用)
RecoveryStage       → recovery.py (闭环)
```

#### 问题5: Recovery 没有形成闭环

**现状分析**:

`recovery.py` 的 `generate_recovery_decision()` 仅生成决策建议（`retry_step`、`reobserve`、`retrieve_again`、`replan`、`ask_user`、`finish_failed`），但没有任何代码消费这个决策。

当前流程：
```
规划 → 执行 → 验证 → 失败记录 → 写回
```

`executor.py` 在验证失败时直接标记失败并退出循环：
```python
# executor.py lines 170-172
_mark_subtask_failed(state, subtask.id)
state.task_status = "failed"
break
```

**应有的闭环流程**:
```
规划 → 执行 → 验证 → 失败 → Recovery决策 → 重新检索/重规划/追问 → 再执行
```

**改进建议**:
1. 在 executor 循环中集成 recovery 决策
2. 根据 `RecoveryDecision.action` 分发到对应处理：
   - `retry_step` → 重试当前 step
   - `retrieve_again` → 回到 Stage 03 重新检索
   - `replan` → 回到 Stage 05 重新规划
   - `ask_user` → 中断并请求用户输入
3. 设置最大恢复次数防止无限循环

#### 问题6: live/mock 边界不清晰

**现状分析**:

`task_runner.py` 中 `live_models=True` 并不等于全链路 live。实际边界：

| 组件 | live_models=True | live_models=False |
|------|-----------------|-------------------|
| Stage 02 TaskCard | Mimo LLM (live) | 关键词匹配 (deterministic) |
| Stage 03 Query | Mimo LLM (live) | StaticMemoryQueryProvider |
| Stage 03 Embedding | BGE-M3 (live) | KeywordEmbeddingProvider (4维硬编码向量) |
| Stage 05 Plan | Mimo LLM (live) | _deterministic_plan() |
| Stage 05 Step Decision | LiveStepDecisionProvider (仅 smoke 一次) | StaticScenarioDecisionProvider |
| Stage 05 Execution | 始终 mock skills | 始终 mock skills |
| Stage 05 Verification | 始终 mock | 始终 mock |
| Stage 06 Summary | Mimo LLM (live) | 硬编码 TaskSummary |

关键问题：
- `live_models=True` 时，实际执行循环仍使用 `StaticScenarioDecisionProvider`，`LiveStepDecisionProvider` 仅用于第一个 subtask 的 smoke 测试
- CLI 强制 `--mock-skills`，不支持真实技能执行
- `_model_boundary()` 中 `real_robot`、`real_vla`、`real_vlm` 均为 `"not_integrated"`

**改进建议**:

定义清晰的 runtime mode 配置：
```yaml
# configs/runtime_mode.yaml
modes:
  task_understanding: live | deterministic
  memory_query: live | static
  embedding: live | keyword
  planning: live | deterministic
  step_decision: live | static
  skills: mock | robot
  verification: mock | vlm
```

### 2.2 Prompt 管理

#### 问题7: Prompt 没有外置

**现状分析**:

所有 LLM prompt 以 f-string 形式内嵌在各模块代码中：

| 模块 | 函数 | 行号 |
|------|------|------|
| `frontdoor.py` | `build_task_understanding_prompt()` | 279-321 |
| `memory_rag.py` | `build_memory_retrieval_query_prompt()` | 163-209 |
| `orchestrator.py` | `build_orchestration_prompt()` | 53-102 |
| `skill_selector.py` | `build_step_decision_prompt()` | 54-119 |
| `recovery.py` | `build_recovery_prompt()` | 51-93 |
| `summary.py` | `build_task_summary_prompt()` | 46-98 |

每个 prompt 都是中文大段 f-string，内嵌 JSON schema、判断规则、边界约束。各模块还有独立的 `*_RETRY_INSTRUCTION` 常量。

**问题**:
- prompt 修改需要改 Python 代码，代码 diff 很乱
- 无法独立做 prompt 版本管理和 A/B 测试
- prompt 之间有共性模式（如 JSON schema 嵌入、重试指令），但无法复用
- 非工程人员难以参与 prompt 调优

**改进建议**:

将 prompt 外置为模板文件：
```
prompts/
  task_understanding.md
  memory_query.md
  orchestration_plan.md
  step_decision.md
  recovery_decision.md
  task_summary.md
  _retry_instruction.md    # 共享片段
  _json_schema_header.md   # 共享片段
```

代码中通过 prompt loader 加载：
```python
# prompt_loader.py
def load_prompt(name: str, **kwargs) -> str:
    template = (PROMPT_DIR / f"{name}.md").read_text()
    return template.format(**kwargs)
```

### 2.3 硬编码与配置化

#### 问题8: 硬编码过多

**现状分析**:

项目中大量配置以常量或魔法数字形式散布在代码中：

**路径硬编码**（`runtime.py` lines 10-19）:
```python
LLM_CASE_ROOT = REPO_ROOT / "tests" / "homemaster" / "llm_cases"
TEST_RESULTS_ROOT = REPO_ROOT / "plan" / "V1.2" / "test_results"
```

**绝对路径泄漏**（`doctor.py` line 89）:
```python
suggestion="Use /Users/wylam/Documents/workspace/HomeMaster/.venv/bin/python"
```

**token 预算硬编码**（`token_budget.py` lines 21-29）:
```python
INITIAL_MAX_TOKENS = {
    "stage_01_smoke": 4096,
    "stage_02_task_card": 4096,
    "stage_05_orchestration": 16384,
    # ...
}
```

**检索评分阈值**（`memory_rag.py` lines 773-810）:
- target_category 匹配 +0.2，target_alias 匹配 +0.2，location 匹配 +0.15
- RRF k=60

**Grounding hint 映射**（`grounding.py` lines 19-28）:
```python
ROOM_HINTS = {"kitchen": ("厨房", "kitchen"), "living_room": ("客厅", ...)}
ANCHOR_HINTS = {"table": ("桌", "桌子", "餐桌", ...), ...}
```

**确定性分支中的中文关键词匹配**（`task_runner.py` lines 605-628）:
```python
target = "药盒" if "药" in utterance else "水杯" if "杯" in utterance else "unknown_object"
```

**改进建议**:

将可配置项外置：
```
configs/
  pipeline.yaml      # Stage 顺序、启用/禁用
  runtime.yaml       # 路径、debug 输出位置
  providers.yaml     # 模型 provider、API keys
  grounding.yaml     # room/anchor hints、阈值
  token_budget.yaml  # 各 Stage 的 max_tokens
  scoring.yaml       # 检索评分权重、RRF 参数
```

### 2.4 代码组织

#### 问题9: 文件组织过于扁平

**现状分析**:

全部 36 个 Python 模块平铺在 `src/homemaster/` 下，无任何子包：
```
__init__.py, cli.py, contracts.py, doctor.py, embedding_client.py,
execution_state.py, executor.py, fact_memory.py, failure_log.py,
frontdoor.py, grounding.py, interactive_shell.py, llm_client.py,
memory_commit.py, memory_index.py, memory_rag.py, memory_tokenizer.py,
orchestration_validator.py, orchestrator.py, pipeline.py,
planning_context.py, recovery.py, runtime.py, runtime_memory_store.py,
scenario_runner.py, skill_registry.py, skill_selector.py, stage_04.py,
stage_05.py, stage_06.py, summary.py, task_record.py, task_runner.py,
token_budget.py, trace.py, verifier.py
```

**问题**:
- 36 个文件平铺，职责边界不清晰
- CLI 入口、Stage 逻辑、基础设施、数据契约混在一起
- 随着功能增长会越来越难导航

**改进建议**:

按职责拆分子包：
```
homemaster/
  cli/              # CLI 入口 (cli.py, interactive_shell.py)
  doctor/           # 诊断检查 (doctor.py, pipeline.py→contract_smoke.py)
  runtime/          # 运行时配置 (runtime.py, token_budget.py, runtime_memory_store.py)
  pipeline/         # Pipeline 框架 (新 pipeline.py, stage registry)
  stages/           # 各 Stage 实现
    task_understanding/
    memory_retrieval/
    grounding/
    orchestration/
    execution/
    summary/
  providers/        # API 客户端 (llm_client.py, embedding_client.py)
  contracts/        # Pydantic 模型 (contracts.py)
  skills/           # 技能注册 (skill_registry.py)
  trace/            # 日志和追踪 (trace.py, task_record.py)
```

### 2.6 日志与可观测性

#### 问题10: 日志系统缺失，trace 覆盖不足

**代码搜索结果**:
```bash
grep -r "import logging" src/homemaster/
# 结果: 无匹配

grep -r "print(" src/homemaster/
# 结果: 无匹配（CLI输出使用 typer.echo()）
```

**当前状态**:
- 全项目无标准 `logging` 使用
- 项目有自定义 trace 系统（`trace.py`），通过 `append_jsonl_event()` 和 `write_json()` 记录结构化数据，每个 Stage 写入 debug assets（input.json、expected.json、actual.json、result.md）
- CLI 输出统一使用 `typer.echo()`，无直接 `print()` 调用
- trace 系统包含 `sanitize_for_log()` 用于密钥脱敏
- 但 trace 系统仅记录结构化结果，不支持日志级别（DEBUG/INFO/WARNING/ERROR），不支持运行时诊断

**影响**:
1. 运行时故障排查依赖异常栈，缺少中间过程日志
2. 无法按级别控制输出粒度
3. 无法实现日志聚合和分析
4. trace 系统覆盖了"做了什么"，但缺少"为什么失败"的上下文

**后续接入真实机器人后，必须统一记录的信息**:
- 每个 Stage 的输入/输出摘要
- 每个 Stage 的耗时
- 失败原因和异常链
- LLM raw response 摘要（截断后的关键内容）
- JSON 解析失败的原始文本
- Embedding 是否成功、retrieval_mode（hybrid/bm25_only）
- Grounding reasons（为什么选择/排除某个 anchor）
- Skill decision reasons（为什么选择 navigation/operation）
- Verification reasons（通过/失败的判断依据）
- Memory commit reasons（写回了什么、为什么）

**改进方案**:

```python
# src/homemaster/logger.py
import logging
import json
from typing import Any, Dict
from pathlib import Path

class HomeMasterLogger:
    """统一的日志管理器"""
    
    def __init__(self, name: str, level: str = "INFO"):
        self.logger = logging.getLogger(name)
        self._setup_handlers(level)
    
    def _setup_handlers(self, level: str):
        # 控制台输出
        console_handler = logging.StreamHandler()
        console_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        console_handler.setFormatter(console_formatter)
        
        # 文件输出（按日期轮转）
        file_handler = logging.handlers.TimedRotatingFileHandler(
            'logs/homemaster.log',
            when='midnight',
            interval=1,
            backupCount=30
        )
        file_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s'
        )
        file_handler.setFormatter(file_formatter)
        
        self.logger.addHandler(console_handler)
        self.logger.addHandler(file_handler)
        self.logger.setLevel(getattr(logging, level))
    
    def log_stage_start(self, stage: str, context: Dict[str, Any]):
        """记录Stage开始"""
        self.logger.info(f"Stage {stage} started", extra={"context": context})
    
    def log_stage_complete(self, stage: str, result: Any, duration: float):
        """记录Stage完成"""
        self.logger.info(
            f"Stage {stage} completed in {duration:.2f}s",
            extra={"result": result}
        )
    
    def log_llm_call(self, provider: str, model: str, prompt_size: int):
        """记录LLM调用"""
        self.logger.debug(
            f"LLM call to {provider}/{model}",
            extra={"prompt_size": prompt_size}
        )
    
    def log_error(self, stage: str, error: Exception, context: Dict[str, Any]):
        """记录错误"""
        self.logger.error(
            f"Error in stage {stage}: {str(error)}",
            exc_info=True,
            extra={"context": context}
        )
```

### 2.7 错误处理机制

#### 问题11: LLM调用缺少网络级重试和退避机制

**当前实现**:

`llm_client.py` 的 `complete_json()` 方法实现了 API key 轮换，每个 key 尝试一次：
```python
# llm_client.py - complete_json()
for key_index, api_key in enumerate(self._provider.api_keys, start=1):
    try:
        response = self._send_prompt(prompt, api_key=api_key, ...)
    except httpx.RequestError as exc:
        errors.append(f"key#{key_index}:network_error:{type(exc).__name__}")
        continue
    if response.status_code >= 400:
        errors.append(f"key#{key_index}:http_{response.status_code}:...")
        continue
    # ... 解析并返回
# 所有key耗尽后抛出 LLMProviderNetworkError 或 LLMProviderResponseError
```

调用层（如 `pipeline.py`、`orchestrator.py`）有 schema 校验级别的重试：
```python
# token_budget.py
MAX_LLM_ATTEMPTS = 3

# pipeline.py / orchestrator.py 中的调用模式
for attempt_index in range(1, MAX_LLM_ATTEMPTS + 1):
    try:
        response = llm_client.complete_json(prompt, max_tokens=...)
        # 校验 Pydantic schema
        break
    except (LLMClientError, ValidationError):
        # 重试，调整 max_tokens
        continue
```

**问题**:
- key 轮换只是换 key 重试，同一个 key 的瞬时网络故障不会重试
- 没有指数退避（exponential backoff）
- 没有熔断机制
- 上层重试针对的是 schema 校验失败，不是网络层故障

**改进方案**:
```python
from tenacity import retry, stop_after_attempt, wait_exponential

class RawJsonLLMClient:
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(httpx.RequestError),
    )
    def _send_prompt_with_retry(self, prompt: str, *, api_key: str, **kwargs):
        """带指数退避的单次 API 调用"""
        return self._send_prompt(prompt, api_key=api_key, **kwargs)
    
    def complete_json(self, prompt: str, **kwargs) -> LLMJsonResponse:
        """主调用入口，key 轮换 + 每个 key 带重试"""
        errors = []
        for key_index, api_key in enumerate(self._provider.api_keys, start=1):
            try:
                response = self._send_prompt_with_retry(prompt, api_key=api_key, **kwargs)
                # ... 解析响应
            except httpx.RequestError as exc:
                errors.append((key_index, exc))
                continue
        
        raise LLMProviderNetworkError(
            error_type="provider_network_error",
            message="all configured API keys failed: " + "; ".join(str(e) for _, e in errors),
        )
```

#### 问题12: Embedding失败导致整个Stage中断，缺少降级策略

**当前代码**:
```python
# memory_rag.py - run_memory_rag() 中的检索流程
bm25_hits = bm25_index.search(query.query_text, top_k=query.top_k)

# Dense检索 —— 无 try/except，失败直接向上抛出
document_vectors_list = cache.get_or_embed_documents(
    documents, ..., embed_texts=embedding_provider.embed_texts,
)
query_vector = embedding_provider.embed_texts([query.query_text])[0]
dense_hits = _dense_hits(documents, document_vectors, query_vector, top_k=query.top_k)
memory_result = _fuse_hits(documents, bm25_hits, dense_hits, ...)
```

```python
# run_stage_03_case() 捕获并包装异常
try:
    return run_memory_rag(task_card, ...)
except (LLMClientError, EmbeddingClientError) as exc:
    raise MemoryRagError(
        error_type=getattr(exc, "error_type", type(exc).__name__),
        message=str(exc),
    ) from exc
```

**问题**:
- Embedding 服务不可用时，整个 Stage 03 直接失败，即使 BM25 已经返回了有效结果
- 没有 BM25-only 的降级路径
- 用户无法感知是 embedding 失败还是其他原因导致的 Stage 失败

**改进方案**:
```python
def run_memory_rag(...):
    bm25_hits = bm25_index.search(query.query_text, top_k=query.top_k)
    
    retrieval_mode = "hybrid"
    try:
        document_vectors_list = cache.get_or_embed_documents(...)
        query_vector = embedding_provider.embed_texts([query.query_text])[0]
        dense_hits = _dense_hits(documents, document_vectors, query_vector, ...)
    except EmbeddingClientError as e:
        logger.warning(f"Embedding service failed, falling back to BM25 only: {e}")
        retrieval_mode = "bm25_only"
        dense_hits = {}
    
    memory_result = _fuse_hits(documents, bm25_hits, dense_hits, ...)
    # 在结果中标记检索模式
```

### 2.8 代码质量问题

#### 问题13: API客户端代码重复

**重复代码分析**:

`llm_client.py` 和 `embedding_client.py` 中存在高度相似的 key 轮换和错误处理结构：

```python
# 两个文件中几乎相同的 key 轮换 + 错误收集模式
for key_index, api_key in enumerate(self._provider.api_keys, start=1):
    started = time.perf_counter()
    try:
        response = ...  # 发送请求
    except httpx.RequestError as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        attempts.append({"key_index": key_index, "status": "network_error", "elapsed_ms": elapsed_ms})
        errors.append(f"key#{key_index}:network_error:{type(exc).__name__}")
        continue
    elapsed_ms = (time.perf_counter() - started) * 1000
    attempts.append({"key_index": key_index, "status_code": response.status_code, "elapsed_ms": elapsed_ms})
    if response.status_code >= 400:
        errors.append(f"key#{key_index}:http_{response.status_code}:...")
        continue
    # ... 解析响应

# 两个文件末尾相同的错误抛出逻辑
if any("network_error" in item for item in errors):
    raise XxxProviderNetworkError(...)
raise XxxProviderResponseError(...)
```

两个文件各自有 `_extract_error_message()` 函数，逻辑相似但实现略有不同：
- `llm_client.py` 版本使用 `_sanitize_error_text()` 压缩空白并截断到 240 字符
- `embedding_client.py` 版本直接 `[:200]` 截断

注：protocol 检测逻辑（`_normalize_protocol`）仅在 `runtime.py` 中实现，不存在重复。

**改进方案**:
```python
# base_api_client.py
class BaseAPIClient(ABC):
    """API客户端基类，统一 key 轮换和错误处理"""
    
    def __init__(self, provider: ProviderConfig, *, client: httpx.Client | None = None):
        self._provider = provider
        self._client = client or httpx.Client(timeout=60)
    
    def _call_with_key_rotation(self, call_fn):
        """统一的 key 轮换 + 错误收集逻辑"""
        errors: list[str] = []
        attempts: list[dict[str, Any]] = []
        for key_index, api_key in enumerate(self._provider.api_keys, start=1):
            started = time.perf_counter()
            try:
                response = call_fn(api_key)
            except httpx.RequestError as exc:
                elapsed_ms = (time.perf_counter() - started) * 1000
                attempts.append({"key_index": key_index, "status": "network_error", "elapsed_ms": elapsed_ms})
                errors.append(f"key#{key_index}:network_error:{type(exc).__name__}")
                continue
            elapsed_ms = (time.perf_counter() - started) * 1000
            attempts.append({"key_index": key_index, "status_code": response.status_code, "elapsed_ms": elapsed_ms})
            if response.status_code >= 400:
                errors.append(f"key#{key_index}:http_{response.status_code}:{self._extract_error(response)}")
                continue
            return response, attempts, elapsed_ms
        return None, attempts, errors
    
    @abstractmethod
    def _extract_error(self, response: httpx.Response) -> str: ...

# llm_client.py
class RawJsonLLMClient(BaseAPIClient):
    def complete_json(self, prompt: str, **kwargs) -> LLMJsonResponse:
        # 使用基类的 key 轮换逻辑
        pass

# embedding_client.py
class BGEEmbeddingClient(BaseAPIClient):
    def embed_texts(self, texts: Sequence[str]) -> EmbeddingResponse:
        # 使用基类的 key 轮换逻辑
        pass
```

#### 问题14: 命名不一致

**不一致的命名示例**:

文件命名混合了编号式和功能式：
- 编号式：`stage_04.py`, `stage_05.py`, `stage_06.py`
- 功能式：`pipeline.py`（Stage 01）, `frontdoor.py`（Stage 02）, `memory_rag.py`（Stage 03）, `task_runner.py`（Stage 07）

类命名前缀不统一：
- 带 Stage 编号前缀：`Stage01SmokeResult`, `Stage04CaseResult`, `Stage05OrchestrationCaseResult`, `Stage05ExecutionResult`, `Stage06CaseResult`
- 不带前缀：`TaskUnderstandingResult`（Stage 02）, `MemoryRagResult`（Stage 03）, `HomeMasterRunResult`（Stage 07）

函数命名风格各异：
- `run_stage_01_contract_smoke()`, `understand_task()`, `run_memory_rag()`, `run_stage_04_case()`, `generate_orchestration_plan()`, `execute_stage_05_plan()`, `run_homemaster_task()`

**建议的命名规范**:
```python
# 文件命名：统一使用功能式命名
task_understanding.py  # 替代 frontdoor.py
memory_retrieval.py    # 替代 memory_rag.py
grounding.py           # 保持
orchestration.py       # 替代 orchestrator.py
execution.py           # 替代 executor.py
summarization.py       # 替代 summary.py

# 类命名：统一不带 Stage 编号前缀，使用功能名 + 后缀
class TaskUnderstandingResult:   # 已有，保持
class MemoryRagResult:           # 已有，保持
class GroundingCaseResult:       # 替代 Stage04CaseResult
class OrchestrationCaseResult:   # 替代 Stage05OrchestrationCaseResult
class ExecutionResult:           # 替代 Stage05ExecutionResult
class SummaryCaseResult:         # 替代 Stage06CaseResult

# 函数命名：统一使用 run_xxx() 或 execute_xxx() 模式
run_task_understanding()         # 替代 understand_task()
run_memory_rag()                 # 已有，保持
run_grounding()                  # 替代 run_stage_04_case()
run_orchestration()              # 替代 generate_orchestration_plan()
run_execution()                  # 替代 execute_stage_05_plan()
run_summary()                    # 替代 generate_task_summary()
```

### 2.5 扩展性设计缺陷

> 核心需求回顾：可扩展性好、模块化、易管理、方便加东西跟删东西。以下问题直接影响这个目标。

#### 问题15: Skill 名称是 Literal 硬编码，加一个技能要改 5 个文件

**现状分析**:

`SkillManifest.name` 使用 `Literal["navigation", "operation", "verification"]`，这个 Literal 类型在 `contracts.py` 和 `skill_registry.py` 中被多处重复定义：

```python
# contracts.py 中引用 skill Literal 的位置：
StepDecision.selected_skill:        Literal["navigation", "operation"]              # line 222
ModuleExecutionResult.skill:        Literal["navigation", "operation", "verification"]  # line 245
SubtaskRuntimeState.last_skill:     Literal["navigation", "operation", "verification"]  # line 273
FailureRecord.skill:                Literal["navigation", "operation", "verification"]  # line 346

# skill_registry.py:
SkillManifest.name:                 Literal["navigation", "operation", "verification"]  # line 15
```

**加一个新 Skill（如 `greeting`）需要同时修改**:
1. `contracts.py` 中 4 个 Literal 类型
2. `skill_registry.py` 中 1 个 Literal 类型 + `get_stage_05_skill_manifests()` 函数 + `validate_skill_input()` 函数
3. `executor.py` 中 `_run_mock_skill()` 和 `_validate_operation_preconditions()`
4. 所有 LLM prompt 中提到 "selected_skill only navigation or operation" 的地方
5. `grounding.py` 中可能的 anchor hint 映射

**影响**: 这是最严重的扩展性瓶颈——Skill 是未来接真实机器人时最频繁需要新增的东西（VLN、VLA、VLM 验证、语音交互等），但当前架构下加一个 Skill 的改动面堪比改核心框架。

**改进建议**:

1. 将 Skill 名称从 `Literal` 改为 `str` + 注册验证：
```python
# skill_registry.py
class SkillManifest(BaseModel):
    name: str  # 不用 Literal，改为注册时验证
    description: str
    selectable_by_mimo: bool
    input_schema: dict[str, Any] = Field(default_factory=dict)

SKILL_REGISTRY: dict[str, SkillManifest] = {}

def register_skill(manifest: SkillManifest):
    SKILL_REGISTRY[manifest.name] = manifest

def validate_skill_name(name: str) -> bool:
    return name in SKILL_REGISTRY
```

2. Skill 通过配置文件或插件注册，不需要改 `contracts.py`
3. `executor.py` 通过 SkillManifest 的 `input_schema` 驱动执行，不需要为每个 Skill 写专用分支

#### 问题16: Contract/Schema 演化无策略，改一个字段爆破半条 pipeline

**现状分析**:

`contracts.py` 中 24 个 Pydantic 模型存在深度的交叉引用链：

```
PlanningContext → TaskCard + MemoryRetrievalQuery + MemoryRetrievalResult + GroundedMemoryTarget + MemoryRetrievalHit
                    (5 个模型聚合)

MemoryCommitPlan → ObjectMemoryUpdate + FactMemoryWrite + TaskRecord
                     → EvidenceRef (被 5 个模型引用)

ExecutionState → SubtaskRuntimeState → VerificationResult
                  (3 层深)
```

所有模型使用 `extra="forbid"`，这意味着：
- 给 `TaskCard` 加一个必填字段 → 破坏 `PlanningContext`、`TaskRecord`、所有构造 TaskCard 的 Stage
- 给 `EvidenceRef` 加一个必填字段 → 破坏 5 个下游模型
- 给 `VerificationResult` 加一个必填字段 → 破坏 `SubtaskRuntimeState`、`ExecutionState`、`FailureRecord`

虽然加 **optional + default** 字段对 Pydantic 反序列化是安全的，但 LLM prompt 中内嵌了完整的 JSON schema，prompt 不会自动包含新字段，导致 LLM 输出的 JSON 仍然不含新字段，触发校验失败。

**影响**: 任何需要扩展 Contract 的场景（新任务类型、新 Skill、新证据类型、机器人反馈字段）都会导致大范围的联动修改。

**改进建议**:

1. **Contract 版本化**: 在 `ContractModel` 基类中增加 `schema_version: str = "1.0"` 字段，支持按版本校验
2. **新字段必须 optional + default**: 制定规则：新增字段一律 `Optional` + `default`，旧字段不改不删
3. **Prompt schema 与 Contract 同步机制**: Prompt 外置后，用 Contract 的 `model_json_schema()` 自动生成 schema 片段，避免手动维护
4. **Stage 间边界校验**: 每个 Stage 入口校验上游输出是否满足当前 Stage 的输入 Contract，而非假定上游输出一定合法

#### 问题17: State 传递设计不完整，缺少统一的 PipelineContext

**现状分析**:

当前 State 传递方式：各 Stage 函数接收上一个 Stage 的 Pydantic 输出作为参数：

```python
# task_runner.py - run_homemaster_task()
task_card: TaskCard | None = None                    # Stage 02 输出
planning_context: PlanningContext | None = None      # Stage 04 输出
orchestration_plan: OrchestrationPlan | None = None   # Stage 05 输出
execution_result: Stage05ExecutionResult | None = None  # Stage 05 执行输出
memory_commit: dict[str, Any] | None = None           # ⚠️ 不是 Pydantic 模型！
```

**问题**:
- `memory_commit` 是 `dict[str, Any]`，打破了整条 pipeline 的类型安全链
- Recovery 闭环需要 State **回退**到 Stage 03（重新检索），当前的线性传递模式不支持
- 没有区分"共享上下文"（task_id、user utterance、runtime config）和"Stage 特有输出"
- 缺少 **PipelineContext** 概念——一个累积所有已完成 Stage 输出的容器
- 新加 Stage 时，需要手动在 `run_homemaster_task()` 中增加中间变量和传参逻辑

**改进建议**:

设计 `PipelineContext`：
```python
class PipelineContext:
    """不可变的 pipeline 上下文，每个 Stage 产出新版本"""
    task_id: str
    utterance: str
    runtime_config: RuntimeConfig
    
    # Stage 输出——None 表示尚未执行
    task_card: TaskCard | None = None
    memory_result: MemoryRetrievalResult | None = None
    planning_context: PlanningContext | None = None
    orchestration_plan: OrchestrationPlan | None = None
    execution_result: Stage05ExecutionResult | None = None
    evidence_bundle: EvidenceBundle | None = None
    summary: TaskSummary | None = None
    memory_commit_plan: MemoryCommitPlan | None = None  # 修正为 Pydantic
    
    def with_stage_result(self, **updates) -> PipelineContext:
        """产出新的 PipelineContext，类似 immutable update"""
        return PipelineContext(**{**self.__dict__, **updates})
```

Recovery 闭环通过 `PipelineContext` 支持回退：
```python
# Recovery retrieve_again → 回退到 Stage 03
new_context = context.with_stage_result(memory_result=None)  # 清除旧结果
new_result = run_memory_rag(new_context)
context = new_context.with_stage_result(memory_result=new_result)
```

#### 问题18: ExecutionState 不变性不一致

**现状分析**:

`execution_state.py` 的 helper 函数使用 `model_copy(deep=True)` 产出新副本（不变性风格），但 `executor.py` 直接 `state.xxx = yyy` 修改字段（可变性风格）：

```python
# execution_state.py - 不可变风格
def mark_subtask_verified(state, ...) -> ExecutionState:
    new_state = state.model_copy(deep=True)  # 产出副本
    new_state.subtasks[idx] = ...
    return new_state

# executor.py - 可变风格（直接修改）
state.last_skill_call = decision.model_dump(...)   # line 139
state.last_skill_result = skill_result              # line 140
state.last_verification_result = verification       # line 146
state.negative_evidence.extend(...)                  # line 350
```

`ContractModel` 基类没有设置 `frozen=True`，所以 Pydantic 允许直接赋值修改。

**影响**: 不变性风格便于调试、重试、回滚；可变性风格不利于追踪 State 变化。两者混用导致无法确定 State 在任意时刻的真实值——是 helper 返回的新副本还是被直接修改的旧对象？

**改进建议**:

1. 统一为不可变风格：`ContractModel` 设置 `frozen=True`，所有修改通过 `model_copy(deep=True)` + 返回新实例
2. 或统一为可变风格：删除 `execution_state.py` 的 `model_copy` 模式，直接修改 + 日志记录变更点
3. 无论哪种，必须一致——混用是最差的选项

#### 问题19: Public API 边界未定义

**现状分析**:

项目没有 `__all__` 导出声明，没有 `_` 前缀标记内部模块，没有 API 稳定性分级。外部系统（机器人控制、监控面板、第三方 Skill）无法知道哪些接口可以安全依赖。

`recovery.py` 是典型案例——它有完整的公开 API（`generate_recovery_decision`、`RecoveryDecisionGenerationResult`），但 **pipeline 内没有任何模块导入它**。它只被测试使用。这是一个"已公开但未被消费"的 API，外部系统如果依赖它，会面临随时可能被删除的风险。

**影响**: 接真实机器人时，需要一个稳定的 Robot Integration API。当前没有定义这个边界——机器人控制器应该调用什么、接收什么、可以依赖什么，全部未定。

**改进建议**:

1. 每个 `__init__.py` 定义 `__all__`，明确公开接口
2. 内部模块使用 `_` 前缀（如 `_execution_state_helpers.py`）
3. 定义 Robot Integration API：
```python
# homemaster/robot_api.py  (stable public API)
class RobotTaskRequest:
    utterance: str
    context: dict[str, Any]

class RobotTaskResponse:
    action: str
    params: dict[str, Any]
    status: str
    evidence: dict[str, Any]
```
4. 标注 API 稳定性级别：`stable`（不可随意改）、`experimental`（可改但需通知）、`internal`（不保证）

#### 问题20: 降级框架不系统，只有 BM25 一个降级场景

**现状分析**:

Review 中问题 12 覆盖了 BM25-only 降级，但这只是众多降级场景之一。其他关键场景完全未设计：

| 降级场景 | 当前处理 | 应有处理 |
|---------|---------|---------|
| Embedding 不可用 | 整个 Stage 03 报错 | BM25-only 降级 |
| LLM 不可用 | 所有 LLM Stage 报错 | 确定性 fallback（关键词匹配、模板规划） |
| Memory RAG 全失败 | Stage 03 报错 | 空 memory context + 继续规划 |
| VLM 验证不可用 | 无替代 | 无验证继续执行 + 降级标记 |
| 机器人连接断开 | `not_integrated` | 模拟执行 + 延迟重连 |

**应有的最小可行 pipeline**:
```
全功能模式:  Stage02(live) → Stage03(hybrid) → Stage04 → Stage05(live) → Stage06(live)
降级模式1:  Stage02(keyword) → Stage03(bm25_only) → Stage04 → Stage05(deterministic) → Stage06(template)
降级模式2:  Stage02(keyword) → Stage03(skip) → Stage04(no_memory) → Stage05(deterministic) → Stage06(template)
紧急模式:   Stage02(keyword) → Stage03(skip) → Stage04(no_memory) → Stage05(hardcoded) → Stage06(skip)
```

**改进建议**:

为每个 Stage 定义降级输出（fallback result），确保 pipeline 不会因为单个服务不可用而完全中断：
```python
class StageDegradationPolicy:
    stage_name: str
    fallback: Callable[[], StageResult]  # 该 Stage 的降级输出
    condition: Callable[Exception, bool]  # 哪些异常触发降级
    notify: bool  # 是否在结果中标记降级
```

#### Stage 07测试情况说明

**实际测试结果**（用户确认）:
- ✅ check_medicine_success - 成功（符合预期）
- ✅ check_medicine_stale_recover - 成功（符合预期）  
- ❌ fetch_cup_retry - 失败（符合预期，测试重试机制）
- ❌ object_not_found - 失败（符合预期，测试找不到物体）
- ❌ distractor_rejected - 失败（符合预期，测试干扰物排除）

**结论**: 测试结果完全符合设计预期，不需要"修复"

#### Dense Embedding Score为0的问题

**观察到的现象**:
```json
{
  "hits": [
    {
      "memory_id": "cup_kitchen_table",
      "bm25_score": 0.85,
      "dense_score": 0.0,  // <-- 问题
      "final_score": 0.34
    }
  ]
}
```

**可能原因**:
1. Embedding服务返回了全零向量
2. 查询向量与文档向量正交
3. Embedding缓存中存储了错误数据

**调查方案**:
```python
# 添加embedding质量检查
def check_embedding_quality(self, vector: List[float]) -> bool:
    """检查embedding向量质量"""
    # 检查是否全零
    if all(v == 0 for v in vector):
        self.logger.error("Received all-zero embedding vector")
        return False
    
    # 检查向量范数
    norm = sum(v**2 for v in vector) ** 0.5
    if norm < 0.1:
        self.logger.warning(f"Embedding vector norm too small: {norm}")
        return False
    
    return True
```

## 三、改进实施计划

> 优先级原则：短期聚焦 **StageRegistry + PipelineContext 解耦**，中期推进 **ProviderFactory / SkillRegistry 管插件化**。不引入 DI 容器或事件总线——用最轻的手段先达到可增删的目标。

---

### 短期（P0）：让 pipeline 能加能删能跳过

核心目标：**改一个 Stage 不改 task_runner，加一个 Stage 不改核心代码，跳过/重跑一个 Stage 只改配置**。

#### 任务1: Stage 协议 + StageRegistry

**要完成什么**:
- 定义 `Stage` Protocol：`execute(context: PipelineContext) -> PipelineContext`
- 实现 `StageRegistry`：按名称注册 Stage，通过配置文件决定执行顺序和启用/禁用
- 将 Stage 01 contract smoke 移入 `homemaster doctor --contract`
- `pipeline.py` 重新定位为主编排器，`task_runner.py` 退化为任务启动入口

**预期效果**:
- task_runner.py 不再直接导入具体 Stage 模块，只从 Registry 取 Stage 实例
- 新增 Stage 只需写一个实现 Stage Protocol 的类 + 在配置中加一行
- 跳过 Stage 只需在配置中设 `enabled: false`
- `homemaster doctor --contract` 替代 Stage 01 smoke，释放 `pipeline.py` 做编排

**验收标准**:
- [ ] 所有 Stage 实现 `Stage` Protocol
- [ ] task_runner.py 不直接导入任何具体 Stage
- [ ] 通过 `pipeline.yaml` 可动态启用/禁用/排序 Stage
- [ ] `homemaster doctor --contract` 可独立运行
- [ ] PipelineContext 支持不可变累积 + Recovery 回退
- [ ] Contract 演化规则有文档记录

#### 任务2: PipelineContext + Recovery 回退

**要完成什么**:
- 定义 `PipelineContext`：不可变上下文，每个 Stage 产出新版本（`context.with_result(stage_name=xxx)`）
- 修正 `memory_commit` 从 `dict[str, Any]` 为 `MemoryCommitPlan` Pydantic 模型
- Recovery 闭环：executor 循环消费 RecoveryDecision，根据 action 回退：
  - `retry_step` → 重试当前 step
  - `retrieve_again` → 清除 `context.memory_result`，重跑 Stage 03
  - `replan` → 清除 `context.orchestration_plan`，重跑 Stage 05 规划
  - `ask_user` → 中断并请求用户输入
- 设置最大恢复次数（默认 3）

**预期效果**:
- Stage 间数据传递全部走 PipelineContext，不再有零散中间变量
- Recovery 形成闭环：失败 → 决策 → 回退 → 重跑，不再"记录失败就退出"
- Recovery 回退通过 `with_result()` 清除旧结果实现，无需改 Stage 内部逻辑

**验收标准**:
- [ ] PipelineContext 不可变，每个 Stage 产出新版本
- [ ] `memory_commit` 类型为 `MemoryCommitPlan`，不再是 `dict[str, Any]`
- [ ] RecoveryDecision 被 executor 消费，闭环运行
- [ ] `retrieve_again` 可重跑 Stage 03 并更新 context
- [ ] 最大恢复次数为 3

#### 任务3: Stage05 拆分为独立子 Stage

**要完成什么**:
- 将 Stage05 拆为 5 个子 Stage：PlanningStage、StepDecisionStage、SkillExecutionStage、VerificationStage、RecoveryStage
- 每个子 Stage 实现 `Stage` Protocol，可独立测试和替换

**预期效果**:
- 接 VLN/VLA/VLM 时只需替换对应子 Stage，不动其他
- 验证可独立开关（跳过 VerificationStage = 不验证直接继续）
- Recovery 独立可控（关闭 RecoveryStage = 验证失败直接终止）

**验收标准**:
- [ ] 5 个子 Stage 各自实现 Stage Protocol，可独立单元测试
- [ ] 替换 VerificationStage 为 mock/vlm 不影响其他子 Stage
- [ ] 跳过 RecoveryStage 时验证失败直接终止

#### 任务4: Contract 演化规则 + ExecutionState 不变性

**要完成什么**:
- Contract 演化规则：新字段一律 `Optional + default`，旧字段不改不删
- Prompt 外置后用 `model_json_schema()` 自动生成 schema 片段，与 Contract 同步
- `ContractModel` 统一设置 `frozen=True`，所有修改走 `model_copy(deep=True)`
- 删除 executor.py 中直接赋值修改 ExecutionState 的代码

**预期效果**:
- 给 TaskCard 加 optional 字段不影响下游 Stage
- Prompt schema 与 Contract 自动同步，不再手动维护
- ExecutionState 不变性一致，便于调试、重试、回滚

**验收标准**:
- [ ] ContractModel 基类设 `frozen=True`
- [ ] executor.py 无直接赋值修改 ExecutionState
- [ ] 新增 optional 字段到任意 Contract 模型，下游 Stage 不需要改代码
- [ ] Prompt schema 从 `model_json_schema()` 自动生成

---

### 中期（P1）：插件化，加 Skill/Provider 不改核心代码

核心目标：**新增 Skill 或 Provider 只需写一个实现 + 注册，核心代码零改动**。

#### 任务5: SkillRegistry 插件化

**要完成什么**:
- SkillManifest.name 从 `Literal` 改为 `str` + 注册时验证
- 实现 SkillRegistry：配置文件或代码注册 Skill，不需要改 `contracts.py`
- executor.py 通过 SkillManifest 的 `input_schema` 驱动执行，不写 per-skill 分支
- 每个 SkillManifest 带 `handler: Callable` 字段，executor 调用 handler

**预期效果**:
- 加一个 `greeting` Skill：只需写 SkillManifest + handler，注册到 SkillRegistry，改 0 行核心代码
- 加一个 `vlm_verify` Skill：同理，替换 VerificationStage 的 handler 即可
- contracts.py 中不再有 Skill 名称 Literal

**验收标准**:
- [ ] SkillManifest.name 为 `str`，不再有 Literal
- [ ] 新增 Skill 不需要修改 contracts.py、skill_registry.py、executor.py
- [ ] executor.py 不含 per-skill switch 分支，通过 handler 统一调用
- [ ] Skill 可通过配置文件注册


#### 任务6: ProviderFactory + runtime mode 配置

**要完成什么**:
- 实现 ProviderFactory：根据配置创建 LLM/Embedding Provider，不再在 task_runner.py 中硬编码 `if live_models` 分支
- 定义 runtime mode 配置（`configs/runtime_mode.yaml`），每个组件独立指定模式：
  ```yaml
  task_understanding: live | deterministic
  memory_query: live | static
  embedding: live | keyword
  planning: live | deterministic
  step_decision: live | static
  skills: mock | robot
  verification: mock | vlm
  ```
- Provider 生命周期（初始化、warmup、teardown）纳入 ProviderFactory 管理
- 移除 doctor.py 中的绝对路径 `/Users/wylam/.../.venv/bin/python`

**预期效果**:
- 切换 LLM Provider 只改配置，不改代码
- 每个 Stage 可独立设 live/mock 模式，不再受全局 `live_models` 控制
- Provider 初始化/清理统一管理，不再散落在各 Stage

**验收标准**:
- [ ] ProviderFactory 根据配置创建 Provider，task_runner.py 无 `if live_models` 分支
- [ ] runtime_mode.yaml 可独立控制每个组件的模式
- [ ] 新增 LLM Provider 只需加配置 + 实现 Protocol，不改 Stage 代码
- [ ] 无绝对路径泄漏

#### 任务7: Prompt 外置 + 配置化硬编码

**要完成什么**:
- 创建 `prompts/` 目录，将 6 个模块的 prompt 迁移为 .md 模板
- 实现 prompt loader：`load_prompt(name, **kwargs)` 加载模板 + 渲染 + 自动注入 JSON schema
- 抽取共享片段：`_retry_instruction.md`、`_json_schema_header.md`
- 创建 `configs/` 下的 YAML 配置文件（pipeline.yaml、runtime.yaml、providers.yaml、grounding.yaml、token_budget.yaml、scoring.yaml）

**预期效果**:
- 修改 prompt 不需要改 Python 代码，代码 diff 干净
- 非工程人员可参与 prompt 调优
- 阈值、权重、路径全部可配置，不再需要改源码调参数

**验收标准**:
- [ ] 所有 prompt 在 `prompts/` 目录下为 .md 文件
- [ ] 修改 prompt 不需要改 Python 代码
- [ ] 共享片段可复用
- [ ] 阈值、权重、token 预算通过 YAML 配置
- [ ] live/mock 边界通过 runtime_mode.yaml 控制

#### 任务8: 降级框架

**要完成什么**:
- 为每个 Stage 定义 `fallback` 函数：当核心 Provider 不可用时，产出降级输出
- 定义最小可行 pipeline：
  - 全功能：Stage02(live) → Stage03(hybrid) → Stage04 → Stage05(live) → Stage06(live)
  - LLM 降级：Stage02(keyword) → Stage03(bm25_only) → Stage04 → Stage05(deterministic) → Stage06(template)
  - 紧急模式：Stage02(keyword) → Stage03(skip) → Stage04(no_memory) → Stage05(hardcoded) → Stage06(skip)
- Embedding 不可用时 Stage03 降级为 BM25-only，结果中标记 `retrieval_mode`
- LLM 不可用时各 LLM Stage 降级为确定性 fallback

**预期效果**:
- 单个服务不可用不会导致整个 pipeline 崩溃
- 用户可感知系统在降级模式下运行（结果中含 `degradation_flags`）

**验收标准**:
- [ ] 每个 Stage 有 documented fallback 行为
- [ ] Embedding 不可用时 Stage03 降级为 BM25-only，不报错中断
- [ ] LLM 不可用时 pipeline 可走降级模式完成任务
- [ ] 结果中包含 `degradation_flags` 标记降级组件

---

### 中期延续（P1+）：代码组织与质量

#### 任务9: 文件组织重构

**要完成什么**:
- 按职责拆分子包（cli/、doctor/、runtime/、pipeline/、stages/、providers/、contracts/、skills/、trace/）
- 每个 `__init__.py` 定义 `__all__`，标记公开 API
- 定义 Robot Integration API 边界（`robot_api.py`）

**预期效果**:
- 36 文件不再平铺，每个子包职责单一可导航
- 外部集成只需看 `__all__` 中标记的 stable API

**验收标准**:
- [ ] 无 36 文件平铺，按职责拆入子包
- [ ] 每个子包 `__init__.py` 有 `__all__`
- [ ] 所有测试通过（import 路径更新后）
- [ ] Robot Integration API 有明确的 stable/experimental/internal 标注

#### 任务10: API 客户端去重 + 命名统一

**要完成什么**:
- 抽取 `BaseAPIClient`：统一 key 轮换 + 错误收集，LLM/Embedding Client 继承
- 统一 `_extract_error_message()`
- 文件名统一功能式（`task_understanding.py` 替代 `frontdoor.py`）
- 类名去掉 Stage 编号前缀，函数名统一 `run_xxx()` 模式

**预期效果**:
- key 轮换逻辑只写一次，新增 Provider 自动继承
- 代码 diff 更易读

**验收标准**:
- [ ] BaseAPIClient 封装 key 轮换 + 错误收集，两个客户端继承
- [ ] `_extract_error_message()` 统一实现
- [ ] 文件名、类名、函数名遵循统一命名规范

---

### 长期（P2）：可观测性与持续优化

#### 任务11: 日志 + trace 系统

**要完成什么**:
- 建立 Python 标准 logging，支持 DEBUG/INFO/WARNING/ERROR 级别
- Stage 统一接口的 before/after/on_error hooks 自动记录：输入摘要、输出摘要、耗时、失败原因
- 记录关键决策 reason：Grounding/Skill/Verification 为什么选 X、Memory commit 为什么写回
- 保留现有 trace.py 的结构化 JSONL，logging 补充中间过程诊断

**预期效果**:
- 运行时故障可从日志追溯为什么失败，不再只看异常栈
- 接真实机器人后，每个决策环节有可查的 reason log

**验收标准**:
- [ ] 所有 Stage 执行有 before/after 日志
- [ ] LLM raw response 摘要、JSON 解析失败有 DEBUG 级别日志
- [ ] Embedding 成功/失败、retrieval_mode 有记录
- [ ] Grounding/Skill/Verification 决策原因有记录
- [ ] 支持日志级别动态调整

#### 任务12: 性能优化 + 监控

- Memory 索引增量更新
- LLM 调用并发优化（多 key 并行尝试）
- Embedding cache TTL
- Metrics 收集 + 健康检查接口
## 四、风险评估

### 技术风险

| 风险项 | 概率 | 影响 | 缓解措施 |
|--------|------|------|----------|
| Pipeline重构引入bug | 中 | 高 | 充分测试，灰度发布 |
| 日志系统性能影响 | 低 | 中 | 异步日志，采样率控制 |
| 依赖升级不兼容 | 低 | 中 | 锁定版本，充分测试 |

### 业务风险

| 风险项 | 概率 | 影响 | 缓解措施 |
|--------|------|------|----------|
| 改动影响现有功能 | 低 | 高 | 保持接口兼容，回归测试 |
| 性能下降 | 低 | 中 | 性能基准测试，监控告警 |

## 五、度量指标

### 代码质量指标

- 测试覆盖率：目标 > 70%
- 代码重复率：目标 < 5%
- 圈复杂度：目标 < 10
- 技术债务：持续下降

### 运行时指标

- Stage平均执行时间
- LLM调用成功率
- 错误恢复成功率
- 日志完整性

### 改进效果指标

- 故障定位时间：减少50%
- 代码修改影响范围：减少30%
- 新功能开发效率：提升20%

## 六、总结

HomeMaster V1.2 项目在功能实现上已经达到预期目标，核心 pipeline 运行正常，测试结果符合设计。从"可扩展、模块化、易管理、方便增删"的核心需求视角，主要改进空间在于：

1. **架构重组**：task_runner 调度过度中心化 → 建立 Pipeline 框架 + Stage 抽象 + PipelineContext；Stage05 拆分为独立子 Stage；Stage01 并入 doctor；Recovery 形成闭环
2. **扩展性设计**：Skill Literal 硬编码是最大扩展瓶颈 → 注册式 Skill 机制；Contract 演化无策略 → Optional+default 规则；Public API 边界未定义 → `__all__` + Robot API；降级框架不系统 → 每个 Stage 定义 fallback
3. **配置化**：prompt 外置为模板文件；硬编码配置化；live/mock 通过 runtime mode 配置
4. **代码组织**：36 文件平铺 → 按职责拆子包；命名规范统一；ExecutionState 不变性一致
5. **可观测性**：建立标准 logging，补充 trace 覆盖（决策原因、降级标记、失败上下文）
6. **错误处理**：LLM 网络级退避重试；Embedding BM25-only 降级；API 客户端抽取基类

建议按五阶段计划推进：架构重组（含 Skill 注册 + PipelineContext）→ 配置化 + Prompt 外置 → 日志系统 → 错误处理 + 降级框架 + 代码质量 → 持续优化。