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

## 三、改进实施计划（修订版）

> 修订结论：用户提出的方向整体合理，尤其是 **先让场景和 Memory 支持扩增**、**再统一五场景基线**、**只做最小 Stage 化**、**Recovery 最后单独做**。原计划不合理之处在于第一阶段塞入了 Recovery 闭环、Stage05 子拆分、Contract 全冻结、Prompt schema 自动生成、文件重构等高耦合改动，容易把 `task_runner.py` 瘦身这件核心事拖散。

> 新优先级：先冻结当前五场景快照，再把场景从代码硬编码变成可扩增测试资产，同时把每场景独立小 memory 改为共享 Memory Corpus + 场景 overlay/profile；之后把五场景基线切到 manifest 驱动，再搭 pipeline 骨架；先让 `task_runner.py` 从“大总管”变成“启动器”，再逐步做 runtime mode、最小日志、SkillRegistry、Prompt 外置、配置化、文件组织和 Recovery。

---

### P-1：场景与 Memory 扩增能力先行（阶段一前置）

核心目标：**先让新增场景和统一 Memory Corpus 有稳定入口，否则后续 pipeline、runtime mode、SkillRegistry、Recovery 都缺少可扩展验收面**。

**关键判断**:
- 不新增一个运行时业务 Stage 来“统一记忆”。Stage03 已经是 Memory RAG，新增 Stage02.5/Stage03.5 会让 pipeline 语义变乱。
- 统一记忆应该作为 **P-1 数据与测试基础设施** 现在就做：先建立共享 corpus，再让场景引用 corpus 的子集或 profile。
- 每场景一份 1-2 条 `memory.json` 只能测流程，不能测 RAG 的召回、排序、冲突、噪声、stale 和 negative evidence；因此不应继续作为主要扩增方式。

**小阶段拆分与验收计划**:

#### P-1.0：当前场景快照冻结

**要完成什么**:
- 在迁移 HomeWorld / Memory Profile 之前，先冻结现有 5 个 Stage07 场景的当前结果。
- 记录每个场景的：
  - utterance
  - expected_final_status
  - 实际 final_status
  - stage_statuses 摘要
  - debug asset 路径
  - deterministic/mock 与 live 的运行边界
- 这个快照只作为迁移前后对比依据，不等于最终 manifest 形态。

**验收计划**:
- [ ] 当前 5 个场景都能从现有 `scenario_runner.py` 跑出结果
- [ ] 快照中明确 `fetch_cup_retry`、`object_not_found`、`distractor_rejected` 现阶段仍是失败符合预期
- [ ] 迁移到 HomeWorld + Memory Profile 后，能逐项对比 final_status 和关键 stage_status
- [ ] 没有这个快照，不进入 P-1A/P-1E 迁移

#### P-1A：统一家庭环境 HomeWorld

**要完成什么**:
- 建立一套统一家庭环境，例如：
  ```text
  data/homes/elder_home_v1/world.json
  ```
- 覆盖固定房间、viewpoint、anchor、真实物体：
  - 房间：厨房、客厅、卧室、门口、书房、储物间
  - anchor：餐桌、操作台、药柜、边桌、茶几、沙发、床头柜、门口柜、书架、储物架
  - 物体：水杯、药盒、遥控器、眼镜、钥匙、纸巾、水瓶、书、手机、干扰物
- 明确 world 是“当前真实环境”，不是 memory；场景只能通过 overlay 改变可见物体或状态。

**验收计划**:
- [ ] `data/homes/elder_home_v1/world.json` 能被 JSON parser 读取
- [ ] 每个 furniture 的 `viewpoint_id` 都存在于 `viewpoints`
- [ ] 每个 viewpoint 的 `visible_object_ids` 都存在于 `objects`
- [ ] 至少 6 个房间、10 个 anchor、20 个真实物体
- [ ] 不依赖任何单一场景目录即可独立理解家庭环境

#### P-1B：统一 Memory Corpus

**要完成什么**:
- 建立统一 object memory corpus：
  ```text
  data/memory/elder_home_v1/object_memory_corpus.json
  ```
- P-1 首轮先建立最小可用 corpus，至少 30-40 条记忆；完整 RAG 压测目标再扩到 80+ 条，不阻塞 P1 架构改造。
- 首轮 corpus 必须覆盖：
  - confirmed/stale
  - high/medium/low confidence
  - 同一物体多历史位置
  - 新旧记忆冲突
  - cup/mug/bottle 等近似干扰类别
  - 用户习惯类位置记忆
- 记忆必须引用 HomeWorld 中稳定存在的 room/anchor/viewpoint；少量“历史位置已不存在”必须显式标记，不默认混入普通记忆。

**验收计划**:
- [ ] corpus 能被 `build_memory_documents()` 构建为 MemoryDocument
- [ ] corpus 中 `memory_id` 全局唯一
- [ ] corpus 中 90% 以上 memory 的 room/anchor/viewpoint 能在 HomeWorld 找到
- [ ] P-1 首轮至少包含 6 类目标物、4 类干扰物、6 条 stale memory、6 条多候选冲突 memory
- [ ] 后续扩展目标记录为 80+ 条 memory、8 类目标物、5 类干扰物、10 条 stale memory、10 条多候选冲突 memory
- [ ] 用 full corpus 跑一次 Stage03 单测时，top_k 返回不退化为空

#### P-1C：Scenario Manifest + Memory Profile

**要完成什么**:
- 建立数据驱动场景 manifest：
  ```text
  data/scenarios/catalog.json
  data/scenarios/<scenario_name>/scenario.json
  data/scenarios/<scenario_name>/memory_profile.json
  data/scenarios/<scenario_name>/world_overlay.json
  data/scenarios/<scenario_name>/failures.json
  ```
- 每个场景声明：
  - `home_id`
  - `utterance`
  - `expected_final_status`
  - `tags`
  - `runtime_modes`
  - `purpose`
  - `memory_profile`
- `memory_profile` 支持：
  - `full_corpus`
  - `include_memory_ids`
  - `exclude_memory_ids`
  - `runtime_negative_evidence`
- 增加最小 memory materializer：`object_memory_corpus.json + memory_profile.json -> 本次运行使用的 memory.json/object_memory payload`。
- 物化优先级固定为：
  1. `full_corpus: true` 时先选取整个 corpus；否则从 `include_memory_ids` 选取子集
  2. `exclude_memory_ids` 在 full/include 之后做删除
  3. `runtime_negative_evidence` 只注入本次 run 的 runtime-only evidence，不写回共享 corpus
  4. 生成的 base memory 继续走现有 Stage03 `memory_path`，避免在 P-1 改 `run_memory_rag()` 行为
- 保留每场景 `memory.json` 兼容路径，但新场景优先由 `memory_profile.json` 物化生成本次运行的 base memory。

**验收计划**:
- [ ] 新增一个普通成功场景只需要增加 scenario/profile/overlay/failures，不需要改 `scenario_runner.py`
- [ ] 新增一个 RAG 压测场景可以直接使用 `full_corpus`
- [ ] 新增一个定向场景可以用 `include_memory_ids` 从共享 corpus 选择子集
- [ ] memory materializer 对 full/include/exclude/negative evidence 都有单测
- [ ] Stage03 仍读取物化后的 `memory_path`，不要求理解 `memory_profile.json`
- [ ] catalog 中每个场景都能解析出 home、world overlay、memory profile 和 expected status

#### P-1D：场景与 Memory 校验器

**要完成什么**:
- 新增校验器，先作为开发/CI 工具，不进入业务 pipeline。
- 校验内容：
  - HomeWorld 引用完整性
  - corpus memory_id 唯一性
  - scenario/profile 引用的 memory_id 存在
  - overlay 引用的 object/anchor/viewpoint 存在
  - `expected_final_status` 只使用允许值
  - 用户指令中的目标物能被 aliases/object_category 覆盖
  - `failures.json` 使用受支持的最小失败规则
  - memory_profile 能成功物化为 Stage03 可读的 object_memory payload

**验收计划**:
- [ ] 校验器对合法数据返回 PASS
- [ ] 人为写错 memory_id 时返回 FAIL 且指出具体场景
- [ ] 人为写错 anchor/viewpoint 时返回 FAIL 且指出具体文件
- [ ] 人为写错 expected status 时返回 FAIL
- [ ] 人为写错 profile 物化规则时返回 FAIL 且指出具体 memory_id 或字段
- [ ] 校验器不调用 LLM、不调用 embedding，默认 CI 可跑

#### P-1E：迁移基线与候选任务

**要完成什么**:
- 先迁移现有 5 个基线场景到 HomeWorld + Memory Profile。
- 再把 30 个候选任务从“每场景独立 memory”改为 profile 引用；候选任务明确标记为 `draft/candidate`，不直接进入基线矩阵。
- 让 `failures.json` 不再只是预留文件，先支持最小失败规则：
  - `force_no_object`
  - `expected_failure_reason`
  - 后续可扩展 `drop_object`、`wrong_object_visible`、`verification_fail_once`
- 禁止继续在 `task_runner.py` / executor 中按 scenario name 写失败注入分支。

**验收计划**:
- [ ] 现有 5 个场景迁移后运行结果不变
- [ ] 30 个候选任务不再复制独立小 memory，改用 profile 引用 corpus，并标记为 draft/candidate
- [ ] 新增一个找不到物体场景可以通过 `failures.json` 触发 `force_no_object`，不需要在 `task_runner.py` 写场景名判断
- [ ] `task_runner.py` / executor 不再出现 `object_not_found`、`distractor_rejected` 这类按场景名触发失败注入的判断
- [ ] `tests/homemaster/test_scenario_runner.py` 不再断言“只能有五个场景”，改为断言“基线五场景都存在”

**不做什么**:
- 不改 Stage02-06 行为。
- 不做 pipeline Stage 化。
- 不做完整 Recovery。
- 不把所有 mock skill 行为一次性配置化，只先把当前按场景名硬编码的失败注入挪到场景数据。
- 不把共享 Memory Corpus 写进 runtime memory；runtime memory 仍然由 Stage06 按 run 隔离写入，避免污染基线。

**P-1 总体验收门槛**:
- [ ] P-1.0 当前五场景快照已冻结
- [ ] HomeWorld、Memory Corpus、Scenario Manifest 三者边界清晰
- [ ] 场景不再以复制 1-2 条 memory 作为主要扩增方式
- [ ] 至少一个场景使用 full corpus，至少一个场景使用 include_memory_ids 子集
- [ ] memory_profile 能物化为 Stage03 兼容的 base memory
- [ ] 五场景基线结果不变
- [ ] 校验器 PASS 后再允许进入 P0

---

### P0：统一五场景测试基线

核心目标：**在场景支持扩增之后，把“五场景不能被改坏”从代码常量固化为 manifest 驱动的回归基线**。

**要完成什么**:
- 固化 5 个 Stage07 场景作为回归基线：
  - `check_medicine_success`：成功，符合预期
  - `check_medicine_stale_recover`：成功，符合预期
  - `fetch_cup_retry`：失败，符合预期，用于覆盖重试/失败路径
  - `object_not_found`：失败，符合预期，用于覆盖找不到物体
  - `distractor_rejected`：失败，符合预期，用于覆盖干扰物排除
- 明确两套基线：
  - deterministic/mock 基线：默认 CI 可跑，不依赖 Mimo/BGE-M3
  - live 基线：需要 `HOMEMASTER_RUN_LIVE_LLM=1` 和 `HOMEMASTER_RUN_LIVE_EMBEDDING=1`
- 把当前 `report/2026-04-29-stage07-live-5-scenarios-report.md` 和 `tests/homemaster/test_stage_07_scenarios_live.py` 对齐为验收依据。

**不做什么**:
- 不改变 Stage 行为。
- 不改 prompt。
- 不引入 Recovery 新逻辑。
- 不搬文件。

**验收标准**:
- [ ] 5 场景的预期状态来自 catalog/manifest，而不是散落在代码里
- [ ] deterministic/mock 路径能在本地稳定跑通
- [ ] live 路径仍保持 opt-in，不进入默认测试
- [ ] 后续每个阶段结束都先跑这套基线

---

### P1：最小 Stage 化：PipelineContext + StageRegistry + task_runner 瘦身

核心目标：**先搭骨架，不重写器官**。现有 `task_runner.py` 在 `run_homemaster_task()` 中直接串联 Stage02-06、路径、debug asset、live/mock 分支和结果汇总；第一轮只把这条串联逻辑搬进 pipeline 骨架，Stage 内部实现尽量不动。

**小阶段拆分与验收计划**:

#### P1A：PipelineContext 最小模型

**要完成什么**:
- 新增最小 `PipelineContext`，承载：
  - run 信息：`run_id`、`scenario`、`utterance`
  - 路径：home/world/memory/runtime/debug/results
  - runtime 边界：`live_models`、`mock_skills`、provider 名称
  - Stage 输出：`task_card`、`memory_result`、`planning_context`、`orchestration_plan`、`execution_result`、`evidence_bundle`、`task_summary`、`memory_commit`
  - `stage_statuses`、`model_boundary`、`paths`
- 提供 `with_updates()` 或等价方法，先支持线性累积，不要求 Recovery 回退。
- 提供从现有 `run_homemaster_task()` 参数构建 context 的 helper。

**验收计划**:
- [ ] 单测能构造最小 context
- [ ] context 能记录 stage output 和 stage status
- [ ] context 可转换为现有 `HomeMasterRunResult` 所需字段
- [ ] 不改任何 Stage02-06 业务函数

#### P1B：Stage Protocol + StageRegistry

**要完成什么**:
- 定义最小 `Stage` Protocol：`name` + `execute(context) -> context`
- 定义 `StageRegistry`，支持按名称注册和按顺序取出 Stage。
- 先使用代码注册固定顺序，不急着上 `pipeline.yaml`。

**验收计划**:
- [ ] registry 能注册 Stage02-06 五个名字
- [ ] registry 能按默认顺序返回 Stage
- [ ] 重复注册、未知 stage 有明确错误
- [ ] 不要求动态启停，不引入复杂配置

#### P1C：Stage02-06 Adapter 化

**要完成什么**:
- 为现有函数加 adapter，不重写内部逻辑：
  - Stage02 adapter 调 `understand_task()` 或 deterministic task card
  - Stage03 adapter 调 `run_memory_rag()`
  - Stage04 adapter 调 `build_planning_context()`
  - Stage05 adapter 调 `generate_orchestration_plan()` + `execute_stage_05_plan()`
  - Stage06 adapter 调 summary + memory commit
- adapter 只负责从 context 取输入、写回输出和状态。

**验收计划**:
- [ ] 每个 adapter 有 focused 单测，能消费/更新 context
- [ ] adapter 调用的底层函数仍是现有函数
- [ ] Stage05 先保持 plan+execute 合并，不在本阶段拆子 Stage
- [ ] adapter 失败时能把 stage 名称写入错误状态

#### P1D：task_runner 瘦身

**要完成什么**:
- `run_homemaster_task()` 保持公开签名兼容，但职责降为：
  - 校验输入
  - 初始化 `PipelineContext`
  - 调用 pipeline
  - 将 context 转回 `HomeMasterRunResult`
  - 写 Stage07 assets
- `task_runner.py` 中不再手写 Stage02-06 的线性大流程。

**验收计划**:
- [ ] `tests/homemaster/test_task_runner.py` 通过
- [ ] `HomeMasterRunResult.to_dict()` 输出字段与现有兼容
- [ ] debug asset 路径与现有结构兼容
- [ ] 非 live 路径不调用真实 LLM/embedding

#### P1E：P1 回归门槛

**要完成什么**:
- 对 P1A-D 做整体回归。
- 跑五场景基线和一两个 profile/corpus 新场景 smoke。

**验收计划**:
- [ ] 五场景 baseline final_status 不变
- [ ] `stage_statuses` 字段结构不变或有兼容映射
- [ ] runtime memory 仍按 run 隔离
- [ ] 新 pipeline 路径和旧入口 `run_homemaster_task()` 行为一致
- [ ] P1 完成后先进入 runtime_mode P2

**关键取舍**:
- 不要求第一轮做到“task_runner.py 不直接导入任何具体 Stage”。短期可以通过 adapter 过渡，目标是先把主流程从函数体里抽出去。
- `pipeline.py` 当前承载 Stage01 smoke。为降低测试 churn，第一轮可新增 `pipeline_runtime.py` 或 `pipeline_core.py` 承载新骨架；`pipeline.py` 的命名清理放到 P8 文件组织阶段处理。
- `PipelineContext` 先服务线性执行，不要求一开始支持 Recovery 回退。

**P1 总体验收门槛**:
- [ ] P1A-E 全部达标
- [ ] `run_homemaster_task()` 不再手写 Stage02-06 的线性大流程
- [ ] Stage02-06 通过 registry/adapter 顺序执行
- [ ] 五场景基线状态不变
- [ ] `tests/homemaster/test_task_runner.py` 和 Stage07 场景测试通过

---

### P2：明确 live/mock runtime_mode

核心目标：**把 `live_models=True` 这种总开关拆成清晰边界**。

**要完成什么**:
- 新增最小 `RuntimeMode` 数据结构，先从代码默认值开始，不急着完整 YAML 化。
- 明确每个组件的模式：
  ```yaml
  task_understanding: live | deterministic
  memory_query: live | static
  embedding: live | keyword
  planning: live | deterministic
  step_decision: live | static
  skills: mock | robot
  verification: mock | vlm
  summary: live | deterministic
  memory_commit: programmatic
  ```
- 保留 `live_models` / `mock_skills` 作为兼容入口，内部映射到 `RuntimeMode`。
- 修正当前边界表达不准的问题：`live_models=True` 时 Stage05 执行循环仍主要是 mock/static，不能在报告里被误读为全链路 live。

**验收标准**:
- [ ] `model_boundary` 来源于 `RuntimeMode`，不是散落的字符串拼接
- [ ] CLI/测试仍可继续传 `live_models`、`mock_skills`
- [ ] Stage07 result 中能看出每个组件真实模式
- [ ] 5 场景基线状态不变

---

### P3：加最小日志

核心目标：**借 Stage 骨架和 RuntimeMode 补上可观测性，但不做重型日志平台**。

**要完成什么**:
- 引入标准 `logging`，新增最小 logger 配置。
- 在 pipeline 层统一记录：
  - stage start / complete / error
  - stage 耗时
  - stage 输入/输出摘要
  - `run_id`、`scenario`、`runtime_mode`
- 保留现有 `trace.py` 的 JSONL/debug assets；logging 只补充运行时诊断，不替代 trace。

**不做什么**:
- 不做异步日志、日志轮转、metrics 平台。
- 不把所有模块内部 print/echo 重构一遍；当前 CLI 已主要使用 `typer.echo()`，不是首要问题。

**验收标准**:
- [ ] 每个 Stage 进入/退出/异常都有日志
- [ ] 日志中含 `run_id`、`stage_name` 和 `runtime_mode`
- [ ] 出错时能看到异常类型、message 和 stage
- [ ] 5 场景基线状态不变

---

### P4：Embedding 失败降级为 BM25-only（建议从原第 8 点提前）

核心目标：**这是小改动、高收益的可靠性修复，不应等到大配置化或文件重构之后**。

**要完成什么**:
- 在 `memory_rag.py` 中保留 BM25 结果。
- Dense embedding/cache/query embedding 任一步失败时，降级为 BM25-only。
- 在 `MemoryRetrievalResult.index_snapshot` 或结果摘要中标记：
  - `retrieval_mode: "hybrid" | "bm25_only"`
  - `degraded: true | false`
  - `degradation_reason`
- 日志记录 embedding 失败原因，但不泄漏密钥。

**不做什么**:
- 不一次性设计所有 Stage 的 fallback framework。
- 不把 LLM 不可用、VLM 不可用、机器人断连全部纳入本阶段。

**验收标准**:
- [ ] 构造 embedding provider 抛错的单测，Stage03 仍返回 BM25-only 结果
- [ ] 正常 embedding 路径仍为 hybrid
- [ ] Stage03 debug asset 中能看到 `retrieval_mode`
- [ ] 5 场景基线状态不变

---

### P5：SkillRegistry 注册化

核心目标：**先解决 Skill 新增时改动面过大的问题，但不急着做完整插件系统**。

**要完成什么**:
- 将 `SkillManifest.name` 从 `Literal["navigation", "operation", "verification"]` 改为 `str` + 注册验证。
- 引入注册式 `SkillRegistry`：
  - manifest 注册
  - input validator 注册
  - mock handler 注册
- `executor.py` 通过 registry 找 handler，逐步减少 `_run_mock_skill()` 中的 per-skill 分支。
- 先保留已有 `navigation`、`operation`、`verification` 行为不变。

**关键取舍**:
- 不要求一轮内把所有 `contracts.py` 中的 Skill Literal 全删干净；可以先保证 registry 是唯一来源，再分步放宽 contract。
- 不要求 Skill 立刻从 YAML 动态加载。先代码注册，后续配置化再搬。

**验收标准**:
- [ ] 新增一个测试用 mock skill 时，不需要改 executor 主循环
- [ ] 现有 navigation/operation/verification 行为不变
- [ ] prompt 中展示的 skill manifest 来自 registry
- [ ] 5 场景基线状态不变

---

### P6：Prompt 外置，只搬不改

核心目标：**把 prompt 从 Python f-string 搬出去，但保持文本语义完全不变**。

**要完成什么**:
- 新增 `prompts/` 目录。
- 迁移 6 类 prompt：
  - task understanding
  - memory query
  - orchestration plan
  - step decision
  - recovery decision
  - task summary
- 实现最小 `prompt_loader`，只负责加载模板和渲染变量。
- 增加 prompt 快照测试，确保搬迁前后生成文本一致或只有可解释的缩进/换行差异。

**不做什么**:
- 不改 prompt 内容。
- 不在本阶段抽共享片段。
- 不在本阶段自动注入 `model_json_schema()`；这会改变 prompt 结构，应该单独评估。
- 不在本阶段做 A/B 测试或 prompt 调优。

**验收标准**:
- [ ] 修改 prompt 不需要改 Python 代码
- [ ] 原 prompt builder 的公开函数仍可用，内部改为读模板
- [ ] prompt 快照测试覆盖 6 类 prompt
- [ ] 5 场景基线状态不变

---

### P7：硬编码配置化

核心目标：**把确实会调参的硬编码移到配置，避免把所有常量都过度工程化**。

**优先配置化对象**:
- token budget：`token_budget.py`
- retrieval scoring：metadata 权重、RRF 参数、top_k 上限
- grounding hints：room/anchor hints
- runtime/debug/report 路径
- runtime mode 默认值

**暂不优先配置化对象**:
- Pydantic contract 字段
- 测试 fixture 的固定值
- 为了测试稳定性存在的 deterministic 关键词规则

**验收标准**:
- [ ] 默认配置缺失时仍使用代码内安全默认值
- [ ] 调整 token/scoring/grounding 不需要改源码
- [ ] 配置读取错误有明确错误信息
- [ ] 5 场景基线状态不变

---

### P8：文件组织和命名统一

核心目标：**等行为和 pipeline 骨架稳定后，再做 import 路径和命名整理**。

**要完成什么**:
- 按职责拆子包：
  - `cli/`
  - `doctor/`
  - `runtime/`
  - `pipeline/`
  - `stages/`
  - `providers/`
  - `skills/`
  - `trace/`
- 处理 `pipeline.py` 当前是 Stage01 smoke 的命名问题：
  - Stage01 contract smoke 并入 doctor 或迁到 `doctor/contract_smoke.py`
  - 新 pipeline 骨架统一放入 `pipeline/`
- 旧 import 提供兼容 shim，避免一次性打爆测试。
- 命名统一采用功能式命名，Stage 编号只作为报告/测试标签，不作为模块职责边界。

**验收标准**:
- [ ] import 兼容层存在，旧测试不需要一次性大改
- [ ] 新代码使用新子包路径
- [ ] `pipeline.py` 命名歧义被消除
- [ ] 5 场景基线状态不变

---

### P9：Recovery 最后单独做

核心目标：**Recovery 依赖 pipeline 可回退/重跑，不应在骨架稳定前硬塞进 executor**。

**前置条件**:
- `PipelineContext` 已稳定承载 Stage 输出。
- StageRegistry 已能按名称重跑 Stage。
- runtime_mode 已能表达降级和 mock/live 边界。
- 日志能记录 recovery 尝试链路。

**要完成什么**:
- 让 `RecoveryDecision` 真正被消费。
- 根据 action 分发：
  - `retry_step`：重试当前 step
  - `retrieve_again`：回到 Stage03 重新检索
  - `replan`：回到 Stage05 重新规划
  - `ask_user`：中断并返回澄清请求
  - `finish_failed`：终止并写入失败证据
- 增加最大恢复次数，默认 3。
- 将 `fetch_cup_retry` 从“失败符合预期”的基线，单独升级为 Recovery 验收用例，而不是在前面阶段偷偷改变它。

**验收标准**:
- [ ] Recovery 成功/失败都有结构化日志和 trace
- [ ] `retry_step` 不会无限循环
- [ ] `retrieve_again` 和 `replan` 能通过 PipelineContext 清理旧结果并重跑
- [ ] 旧五场景基线先保持不变；Recovery 用例通过单独 acceptance 更新

---

### 不合理点与调整结论

1. **原计划把 Recovery 放太早**：不合理。Recovery 需要可重跑的 pipeline 和可回退的 context，否则会被硬塞进 `executor.py`，进一步加重大总管问题。
2. **原计划把 Stage05 一次拆成 5 个子 Stage 放在 P0**：偏激进。第一轮只需要 adapter 化；子拆分等 SkillRegistry/runtime_mode 稳定后再做。
3. **Prompt 外置时自动注入 schema**：不建议同阶段做。用户要求“只搬不改”，自动 schema 会改变 prompt 内容和模型行为。
4. **文件组织重构不应靠前**：合理后置。当前测试和 import 边界多，先搬文件会制造大量噪音 diff。
5. **Embedding BM25-only 不应太晚**：建议提前到 P4。它改动面小，能显著提升 Stage03 稳定性，也能被日志/runtime_mode 清楚标记。
6. **Stage01 并入 doctor 是对的，但不是第一优先级**：除非它阻塞新 pipeline 文件命名，否则放到 P8 文件组织阶段更稳。
7. **Contract 全 frozen 风险较高**：ExecutionState 当前有大量可变更新，直接冻结会引发连锁改动；先建立 PipelineContext 边界，再单独治理状态不变性。
8. **统一 Memory 不应做成新的运行时 Stage**：这是 Stage03 RAG 的数据前提，应在 P-1 建共享 Memory Corpus 和 scenario memory profile；否则会增加 pipeline 概念负担，且仍然测不到真实 RAG 压力。

## 四、风险评估

### 技术风险

| 风险项 | 概率 | 影响 | 缓解措施 |
|--------|------|------|----------|
| Pipeline重构引入bug | 中 | 高 | 充分测试，灰度发布 |
| 最小日志产生噪声或性能影响 | 低 | 中 | 默认 INFO 级别，只记录摘要，不记录大 payload |
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

1. **执行顺序重排**：先冻结当前五场景快照，再让场景和 Memory Corpus 支持扩增，再把五场景基线切到 manifest 驱动，然后做最小 PipelineContext + StageRegistry，把 `task_runner.py` 从“大总管”收敛为任务启动器。
2. **架构重组降风险**：第一阶段只做 Stage adapter 和 pipeline 骨架，不同时推进 Stage05 子拆分、Recovery 闭环、Contract 全冻结或文件大搬迁。
3. **运行边界清晰化**：用 `RuntimeMode` 明确 live/mock/static/programmatic 边界，避免 `live_models=True` 被误解为全链路 live。
4. **扩展性设计渐进推进**：SkillRegistry 注册化先解决 Skill 新增改动面，Prompt 外置坚持“只搬不改”，配置化只处理确实会调参的硬编码。
5. **可靠性修复提前**：Embedding 失败降级 BM25-only 建议提前做，因为改动小、收益高，且可被 runtime_mode 和日志清晰标记。
6. **Recovery 后置**：Recovery 需要可重跑的 pipeline、可回退的 context 和日志链路，最后作为独立阶段实施。

建议按以下顺序推进：当前五场景快照冻结 → 场景与 Memory 扩增能力 → manifest 驱动五场景基线 → 最小 Stage 化 → runtime_mode → 最小日志 → Embedding BM25-only 降级 → SkillRegistry → Prompt 外置 → 配置化 → 文件组织/命名 → Recovery。
