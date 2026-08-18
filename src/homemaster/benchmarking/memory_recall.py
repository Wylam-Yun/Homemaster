"""Serial public-CLI benchmark for HomeMaster structured-memory recall."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import tempfile
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from homemaster.config import load_config
from homemaster.memory.managed_neo4j import ManagedNeo4jRuntime
from homemaster.memory.mindmemos_runtime import EmbeddedMindMemOS
from homemaster.memory.models import MEMORY_RECORD_ADAPTER

RecordKind = Literal["target", "near_distractor", "unrelated_distractor"]


@dataclass(frozen=True)
class BenchmarkRecord:
    index: int
    kind: RecordKind
    website: str
    page: str
    goal: str
    subject: str
    predicate: str
    value: dict[str, Any]
    source: str
    exact_query: str
    paraphrase_query: str
    contrast_query: str | None = None
    contrast_target_index: int | None = None

    @property
    def tool_record(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "memory_type": "fact",
            "subject": {"type": "object", "name": self.subject},
            "predicate": self.predicate,
            "value": self.value,
            "source": self.source,
        }


@dataclass(frozen=True)
class BenchmarkPaths:
    root: Path
    dataset: Path
    checkpoint: Path
    write_results: Path
    recall_results: Path
    routing_results: Path
    raw: Path
    summary: Path
    report: Path

    @classmethod
    def create(cls, base: Path, run_id: str) -> BenchmarkPaths:
        root = base.expanduser().resolve() / run_id
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(root, 0o700)
        raw = root / "raw"
        raw.mkdir(mode=0o700, exist_ok=True)
        os.chmod(raw, 0o700)
        return cls(
            root=root,
            dataset=root / "dataset.json",
            checkpoint=root / "checkpoint.json",
            write_results=root / "write-results.jsonl",
            recall_results=root / "recall-results.jsonl",
            routing_results=root / "routing-results.jsonl",
            raw=raw,
            summary=root / "summary.json",
            report=root / "report.md",
        )


@dataclass(frozen=True)
class CompletedCommand:
    returncode: int
    stdout: str
    stderr: str
    elapsed_seconds: float
    timed_out: bool


class CommandRunner(Protocol):
    def __call__(
        self,
        command: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout: float,
    ) -> CompletedCommand: ...


class WriteTerminalVerifier(Protocol):
    def __call__(self, job_id: str, record: BenchmarkRecord) -> dict[str, Any] | None: ...


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    suite: Literal["exact", "paraphrase", "distractor", "natural"]
    prompt: str
    expected_index: int


_WEBSITES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "星河商城",
        "订单与商品中心",
        (
            "按订单号搜索订单",
            "筛选待发货订单",
            "申请订单退款",
            "导出订单明细",
            "查找可用优惠券",
            "修改收货地址",
            "查看商品库存",
        ),
    ),
    (
        "云桥邮箱",
        "邮件工作台",
        (
            "按发件人搜索邮件",
            "筛选带附件邮件",
            "创建自动归档规则",
            "转发指定邮件",
            "下载全部附件",
            "标记邮件为待办",
            "查看已发送邮件",
        ),
    ),
    (
        "北辰文档",
        "文档空间",
        (
            "创建空白文档",
            "分享只读链接",
            "导出PDF文件",
            "恢复历史版本",
            "移动文档到文件夹",
            "添加文档评论",
            "搜索文档标题",
        ),
    ),
    (
        "灯塔工单",
        "工单控制台",
        (
            "按编号搜索工单",
            "把工单分派给成员",
            "升级高优先级工单",
            "关闭已解决工单",
            "筛选等待回复工单",
            "添加内部备注",
            "导出工单列表",
        ),
    ),
    (
        "青禾CRM",
        "客户工作台",
        (
            "搜索客户档案",
            "新建联系人",
            "更新商机阶段",
            "添加跟进记录",
            "筛选本周到期商机",
            "导出客户列表",
            "合并重复客户",
        ),
    ),
    (
        "天穹分析",
        "数据分析台",
        (
            "创建日期筛选器",
            "切换折线图视图",
            "导出CSV报表",
            "保存自定义看板",
            "筛选异常指标",
            "共享报表链接",
            "刷新数据集",
        ),
    ),
    (
        "松果人事",
        "员工服务台",
        (
            "搜索员工档案",
            "提交请假申请",
            "查看考勤异常",
            "发起入职流程",
            "下载工资单",
            "更新紧急联系人",
            "导出部门名单",
        ),
    ),
    (
        "银湾财务",
        "财务工作台",
        (
            "搜索电子发票",
            "提交报销单",
            "执行账单对账",
            "发起付款申请",
            "导出费用明细",
            "查看审批进度",
            "撤回草稿报销",
        ),
    ),
    (
        "远帆旅行",
        "行程中心",
        (
            "搜索指定航班",
            "筛选可取消酒店",
            "申请航班改签",
            "导出完整行程",
            "添加同行旅客",
            "选择酒店早餐",
            "查看退款进度",
        ),
    ),
    (
        "云峰平台",
        "云资源控制台",
        (
            "搜索计算实例",
            "查看实例日志",
            "创建告警规则",
            "打开访问密钥页面",
            "筛选停止实例",
            "导出资源清单",
            "查看操作审计",
        ),
    ),
)

_STEP_PATTERNS: tuple[tuple[str, ...], ...] = (
    (
        "点击顶部导航中的“{page}”",
        "点击“{goal}”入口",
        "填写完整查询条件",
        "点击“搜索”按钮",
        "点击唯一匹配结果的“查看详情”",
    ),
    (
        "打开“{page}”",
        "点击页面右上角的“筛选”",
        "选择“{goal}”对应条件",
        "点击“应用筛选”",
        "核对结果列表",
    ),
    (
        "进入“{page}”",
        "选择需要处理的目标记录",
        "点击“{goal}”",
        "核对弹窗中的目标信息",
        "点击“确认”",
    ),
    ("打开“{page}”", "点击“更多操作”", "选择“{goal}”", "设置输出范围", "点击“开始执行”"),
    ("进入“{page}”", "点击左侧的“工具”", "选择“{goal}”", "填写必要参数", "点击“保存并应用”"),
    ("打开“{page}”", "搜索并打开目标记录", "点击“编辑”", "完成“{goal}”所需修改", "点击“保存”"),
    ("进入“{page}”", "点击页面搜索框", "输入“{goal}”的关键字", "点击“查询”", "核对目标状态"),
)


def build_dataset(run_id: str) -> tuple[BenchmarkRecord, ...]:
    if not run_id.strip():
        raise ValueError("run_id must not be blank")
    records: list[BenchmarkRecord] = []
    index = 1
    for website, page, goals in _WEBSITES:
        target_indexes: list[int] = []
        for local_index, goal in enumerate(goals):
            current_index = index
            target_indexes.append(current_index)
            steps = [step.format(page=page, goal=goal) for step in _STEP_PATTERNS[local_index]]
            subject = _subject(run_id, current_index, website, goal)
            records.append(
                BenchmarkRecord(
                    index=current_index,
                    kind="target",
                    website=website,
                    page=page,
                    goal=goal,
                    subject=subject,
                    predicate="web_operation_steps",
                    value={
                        "site": website,
                        "page": page,
                        "goal": goal,
                        "steps": steps,
                        "expected_result": f"在{website}完成“{goal}”并看到目标结果",
                        "synthetic": True,
                    },
                    source="user_statement",
                    exact_query=f"{website}中“{goal}”的准确操作顺序",
                    paraphrase_query=f"我想在{website}完成{goal}，按钮应该依次怎么点",
                )
            )
            index += 1
        for target_index in target_indexes[:2]:
            target = records[target_index - 1]
            goal = f"通过快捷入口完成{target.goal}"
            steps = [
                f"打开{website}首页的“快捷操作”",
                f"选择“{target.goal}”快捷卡片",
                "先点击“预览结果”",
                "返回并补充快捷条件",
                "点击“立即执行”",
            ]
            current_index = index
            records.append(
                BenchmarkRecord(
                    index=current_index,
                    kind="near_distractor",
                    website=website,
                    page="快捷操作面板",
                    goal=goal,
                    subject=_subject(run_id, current_index, website, goal),
                    predicate="web_operation_steps",
                    value={
                        "site": website,
                        "page": "快捷操作面板",
                        "goal": goal,
                        "steps": steps,
                        "expected_result": f"通过快捷操作面板完成“{target.goal}”",
                        "synthetic": True,
                    },
                    source="user_statement",
                    exact_query=f"{website}中“{goal}”的准确操作顺序",
                    paraphrase_query=f"如何从{website}快捷入口执行{target.goal}",
                    contrast_query=(
                        f"不要使用快捷操作面板；从{target.page}完成“{target.goal}”时，"
                        "准确按钮顺序是什么"
                    ),
                    contrast_target_index=target_index,
                )
            )
            index += 1
        goal = "查看帮助中心版本信息"
        current_index = index
        records.append(
            BenchmarkRecord(
                index=current_index,
                kind="unrelated_distractor",
                website=website,
                page="帮助中心",
                goal=goal,
                subject=_subject(run_id, current_index, website, goal),
                predicate="web_operation_steps",
                value={
                    "site": website,
                    "page": "帮助中心",
                    "goal": goal,
                    "steps": ["点击头像", "点击“帮助中心”", "点击“关于”", "查看版本号"],
                    "expected_result": f"显示{website}的帮助中心版本信息",
                    "synthetic": True,
                },
                source="user_statement",
                exact_query=f"{website}中“{goal}”的准确操作顺序",
                paraphrase_query=f"怎样查看{website}帮助页面的版本",
            )
        )
        index += 1
    assert len(records) == 100
    return tuple(records)


def _subject(run_id: str, index: int, website: str, goal: str) -> str:
    return f"HM100::{run_id}::{index:04d}::{website}::{goal}"


def _jsonable_record(record: BenchmarkRecord) -> dict[str, Any]:
    return asdict(record)


def _dataset_hash(records: Sequence[BenchmarkRecord]) -> str:
    payload = json.dumps(
        [_jsonable_record(record) for record in records],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def _atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def _append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(value), ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _write_private_text(path: Path, text: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(text)
    os.chmod(path, 0o600)


def generate_run(*, base: Path, run_id: str) -> BenchmarkPaths:
    paths = BenchmarkPaths.create(base, run_id)
    if paths.dataset.exists() or paths.checkpoint.exists():
        raise FileExistsError(f"benchmark run already exists: {paths.root}")
    records = build_dataset(run_id)
    _atomic_write_json(
        paths.dataset,
        {"schema_version": 1, "run_id": run_id, "records": [_jsonable_record(r) for r in records]},
    )
    _atomic_write_json(
        paths.checkpoint,
        {
            "schema_version": 1,
            "run_id": run_id,
            "dataset_sha256": _dataset_hash(records),
            "confirmed": {},
            "last_state": "generated",
        },
    )
    return paths


def load_dataset(path: Path) -> tuple[BenchmarkRecord, ...]:
    document = json.loads(path.read_text(encoding="utf-8"))
    records = tuple(BenchmarkRecord(**item) for item in document["records"])
    if len(records) != 100:
        raise ValueError(f"expected 100 benchmark records, found {len(records)}")
    return records


def parse_stream_events(stdout: str) -> tuple[dict[str, Any], ...]:
    events: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and isinstance(value.get("type"), str):
            events.append(value)
    return tuple(events)


def build_write_prompt(record: BenchmarkRecord) -> str:
    payload = json.dumps(record.tool_record, ensure_ascii=False, separators=(",", ":"))
    return (
        "这是 HomeMaster 记忆召回基准的一条合成测试事实。请使用当前 user_statement evidence，"
        "只调用一次 mindmemos_add，把下面完整 JSON 作为 fact 保存。"
        "不要调用 mindmemos_search、mindmemos_update、mindmemos_delete、"
        "observe、机器人或浏览器工具。"
        "不要改写 subject、predicate、value 或 source。工具完成后只报告真实 status 和 memory_id。\n"
        f"{payload}"
    )


def run_command(
    command: Sequence[str], *, cwd: Path, env: Mapping[str, str], timeout: float
) -> CompletedCommand:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            list(command),
            cwd=cwd,
            env=dict(env),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        return CompletedCommand(
            returncode=124,
            stdout=_text(exc.stdout),
            stderr=_text(exc.stderr),
            elapsed_seconds=time.monotonic() - started,
            timed_out=True,
        )
    return CompletedCommand(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        elapsed_seconds=time.monotonic() - started,
        timed_out=False,
    )


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value


def _decoded_output(event: Mapping[str, Any]) -> dict[str, Any] | None:
    output = event.get("output")
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except json.JSONDecodeError:
            return None
    return dict(output) if isinstance(output, Mapping) else None


def _records_equal(actual: object, expected: Mapping[str, Any]) -> bool:
    if not isinstance(actual, Mapping):
        return False
    actual_subject = actual.get("subject")
    expected_subject = expected["subject"]
    if not isinstance(actual_subject, Mapping):
        return False
    return (
        actual.get("schema_version") == expected["schema_version"]
        and actual.get("memory_type") == expected["memory_type"]
        and actual_subject.get("type") == expected_subject["type"]
        and actual_subject.get("name") == expected_subject["name"]
        and actual.get("predicate") == expected["predicate"]
        and actual.get("value") == expected["value"]
        and actual.get("source") == expected["source"]
    )


def _inspect_write(
    completed: CompletedCommand,
    record: BenchmarkRecord,
    terminal_verifier: WriteTerminalVerifier,
) -> tuple[str, dict[str, Any] | None, str]:
    events = parse_stream_events(completed.stdout)
    started = any(
        event.get("type") == "tool_started" and event.get("tool_name") == "mindmemos_add"
        for event in events
    )
    completions = [
        event
        for event in events
        if event.get("type") == "tool_completed" and event.get("tool_name") == "mindmemos_add"
    ]
    if completions:
        receipt = _decoded_output(completions[-1])
        if receipt is not None:
            accepted = (
                completed.returncode == 0
                and receipt.get("success") is True
                and receipt.get("status") == "success"
                and receipt.get("domain_status") == "accepted"
                and receipt.get("verified_terminal_state") is False
                and receipt.get("backend_attempted") is True
                and isinstance(receipt.get("job_id"), str)
                and bool(str(receipt["job_id"]).strip())
                and "memory_id" not in receipt
            )
            if accepted:
                try:
                    terminal = terminal_verifier(str(receipt["job_id"]), record)
                except Exception as exc:
                    return (
                        "outcome_unknown",
                        receipt,
                        f"terminal verification failed: {type(exc).__name__}: {exc}",
                    )
                valid_terminal = bool(
                    terminal is not None
                    and terminal.get("job_id") == receipt["job_id"]
                    and terminal.get("status") == "completed"
                    and terminal.get("verified_terminal_state") is True
                    and isinstance(terminal.get("memory_id"), str)
                    and bool(str(terminal["memory_id"]).strip())
                    and _records_equal(terminal.get("record"), record.tool_record)
                )
                if valid_terminal:
                    return "confirmed", terminal, "confirmed post-exit raw terminal state"
                return "outcome_unknown", receipt, "accepted job has no matching terminal state"
            if receipt.get("backend_attempted") is False:
                return "safe_to_retry", receipt, "backend was not attempted"
            return "outcome_unknown", receipt, "acceptance receipt was incomplete or mismatched"
    if started:
        return (
            "outcome_unknown",
            None,
            "mindmemos_add started without confirmed terminal receipt",
        )
    return "safe_to_retry", None, "mindmemos_add did not start"


def write_run(
    *,
    paths: BenchmarkPaths,
    repo_root: Path,
    timeout_seconds: float,
    max_records: int | None = None,
    runner: CommandRunner = run_command,
    terminal_verifier: WriteTerminalVerifier | None = None,
) -> dict[str, Any]:
    records = load_dataset(paths.dataset)
    checkpoint = json.loads(paths.checkpoint.read_text(encoding="utf-8"))
    if checkpoint.get("dataset_sha256") != _dataset_hash(records):
        raise ValueError("dataset hash differs from checkpoint")
    confirmed: dict[str, str] = dict(checkpoint.get("confirmed", {}))
    attempted = 0
    newly_confirmed = 0
    state = "complete" if len(confirmed) == len(records) else "ready"
    for record in records:
        if str(record.index) in confirmed:
            continue
        if max_records is not None and attempted >= max_records:
            break
        attempted += 1
        command = [
            str(repo_root / ".venv/bin/python"),
            "-m",
            "homemaster.cli",
            "-p",
            build_write_prompt(record),
            "--output-format",
            "stream-json",
        ]
        environment = dict(os.environ)
        environment["PYTHONPATH"] = "src"
        completed = runner(command, cwd=repo_root, env=environment, timeout=timeout_seconds)
        _write_private_text(paths.raw / f"write-{record.index:04d}.stdout.jsonl", completed.stdout)
        _write_private_text(paths.raw / f"write-{record.index:04d}.stderr.log", completed.stderr)
        state, receipt, reason = _inspect_write(
            completed,
            record,
            terminal_verifier or _verify_accepted_write,
        )
        result = {
            "index": record.index,
            "subject": record.subject,
            "state": state,
            "reason": reason,
            "returncode": completed.returncode,
            "timed_out": completed.timed_out,
            "elapsed_seconds": completed.elapsed_seconds,
            "memory_id": receipt.get("memory_id") if receipt else None,
            "provider_calls": _provider_call_counts(completed.stderr),
        }
        _append_jsonl(paths.write_results, result)
        if state != "confirmed":
            checkpoint["last_state"] = state
            checkpoint["last_index"] = record.index
            _atomic_write_json(paths.checkpoint, checkpoint)
            break
        memory_id = str(receipt["memory_id"])
        confirmed[str(record.index)] = memory_id
        newly_confirmed += 1
        checkpoint.update(
            {"confirmed": confirmed, "last_state": "confirmed", "last_index": record.index}
        )
        _atomic_write_json(paths.checkpoint, checkpoint)
    if len(confirmed) == len(records):
        state = "complete"
        checkpoint["last_state"] = state
        _atomic_write_json(paths.checkpoint, checkpoint)
    return {
        "run_id": checkpoint["run_id"],
        "state": state,
        "attempted": attempted,
        "confirmed": newly_confirmed,
        "confirmed_total": len(confirmed),
        "remaining": len(records) - len(confirmed),
    }


def _verify_accepted_write(job_id: str, record: BenchmarkRecord) -> dict[str, Any] | None:
    config = load_config()
    job_log = config.memory.data_root / "mindmemos" / "add_jobs.jsonl"
    completed = None
    for event in _read_jsonl(job_log):
        if event.get("event") != "memory_add_job":
            continue
        payload = event.get("payload")
        if (
            isinstance(payload, Mapping)
            and payload.get("job_id") == job_id
            and payload.get("status") in {"completed", "failed"}
        ):
            completed = dict(payload)
    if completed is None or completed.get("status") != "completed":
        return None
    memory_id = completed.get("memory_id")
    if not isinstance(memory_id, str) or not memory_id:
        return None
    raw_record = asyncio.run(_read_raw_record(config, memory_id, job_id))
    if raw_record is None or not _records_equal(raw_record, record.tool_record):
        return None
    return {
        "job_id": job_id,
        "status": "completed",
        "memory_id": memory_id,
        "record": raw_record,
        "verified_terminal_state": True,
    }


async def _read_raw_record(config: Any, memory_id: str, job_id: str) -> dict[str, Any] | None:
    managed = (
        ManagedNeo4jRuntime(config.memory) if config.memory.neo4j.mode == "managed_local" else None
    )
    store = EmbeddedMindMemOS(config)
    try:
        if managed is not None:
            await managed.start()
        await store.start()
        if not store.available:
            raise RuntimeError(store.unavailable_cause or "MindMemOS is unavailable")
        from mindmemos.typing import MemoryRequestContext

        context = MemoryRequestContext(
            request_id=f"benchmark-verify-{job_id}",
            account_id="local",
            project_id="local",
            api_key_uuid="embedded-local",
            user_id="local",
            app_id="homemaster",
            session_id=None,
            agent_id="homemaster",
        )
        raw = await store.get_raw(memory_id, context)
        if raw is None or getattr(raw, "status", None) != "active":
            return None
        metadata = getattr(raw, "metadata", None)
        if not isinstance(metadata, Mapping):
            return None
        request = metadata.get("request_metadata")
        if not isinstance(request, Mapping):
            return None
        records = request.get("record_metadata")
        candidates = (
            records
            if isinstance(records, Sequence) and not isinstance(records, (str, bytes))
            else (request,)
        )
        for candidate in candidates:
            if not isinstance(candidate, Mapping) or not isinstance(
                candidate.get("record_json"), str
            ):
                continue
            parsed = MEMORY_RECORD_ADAPTER.validate_json(candidate["record_json"])
            return parsed.model_dump(mode="json")
        return None
    finally:
        await store.close()
        if managed is not None:
            await managed.close()


def _provider_call_counts(stderr: str) -> dict[str, int]:
    return {
        "chat": stderr.count("kind=chat"),
        "embedding": stderr.count("kind=embedding"),
        "litellm_completion": stderr.count("LiteLLM completion()"),
    }


def evaluation_cases(records: Sequence[BenchmarkRecord]) -> tuple[EvaluationCase, ...]:
    by_index = {record.index: record for record in records}
    cases: list[EvaluationCase] = []
    for record in records:
        cases.append(
            EvaluationCase(
                case_id=f"exact-{record.index:04d}",
                suite="exact",
                prompt=_forced_prompt(record.exact_query),
                expected_index=record.index,
            )
        )
    for record in records:
        if record.kind == "target":
            cases.append(
                EvaluationCase(
                    case_id=f"paraphrase-{record.index:04d}",
                    suite="paraphrase",
                    prompt=_forced_prompt(record.paraphrase_query),
                    expected_index=record.index,
                )
            )
    for record in records:
        if record.kind == "near_distractor":
            assert record.contrast_target_index is not None and record.contrast_query is not None
            target = by_index[record.contrast_target_index]
            cases.append(
                EvaluationCase(
                    case_id=f"distractor-{record.index:04d}",
                    suite="distractor",
                    prompt=_forced_prompt(record.contrast_query),
                    expected_index=target.index,
                )
            )
    for website, _, _ in _WEBSITES:
        samples = [r for r in records if r.website == website and r.kind == "target"][:3]
        for record in samples:
            cases.append(
                EvaluationCase(
                    case_id=f"natural-{record.index:04d}",
                    suite="natural",
                    prompt=f"我想在{record.website}{record.goal}，应该依次点什么？",
                    expected_index=record.index,
                )
            )
    assert Counter(case.suite for case in cases) == {
        "exact": 100,
        "paraphrase": 70,
        "distractor": 20,
        "natural": 30,
    }
    return tuple(cases)


def _forced_prompt(query: str) -> str:
    return (
        "这是记忆召回基准查询。请只调用一次 mindmemos_search，"
        "且参数 memory_type 必须准确设置为 fact，"
        f"查询内容：{query}。不要调用 observe、机器人或浏览器工具。"
        "根据返回记录报告完整、有序的 steps 和 memory_id，不要自行补充步骤。"
    )


def evaluate_search_events(
    events: Sequence[Mapping[str, Any]],
    *,
    expected: BenchmarkRecord,
    expected_memory_id: str,
) -> dict[str, Any]:
    tool_names = [
        str(event.get("tool_name"))
        for event in events
        if event.get("type") == "tool_started" and event.get("tool_name")
    ]
    ranked: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") != "tool_completed" or event.get("tool_name") != "mindmemos_search":
            continue
        output = _decoded_output(event)
        if output is None:
            continue
        values = output.get("records", [])
        if isinstance(values, list):
            ranked.extend(item for item in values if isinstance(item, dict))
    ids = [str(item.get("memory_id", "")) for item in ranked]
    rank = ids.index(expected_memory_id) + 1 if expected_memory_id in ids else None
    expected_subject = expected.subject
    subject_matches = [
        item
        for item in ranked
        if isinstance(item.get("record"), Mapping)
        and isinstance(item["record"].get("subject"), Mapping)
        and item["record"]["subject"].get("name") == expected_subject
    ]
    expected_items = [item for item in ranked if item.get("memory_id") == expected_memory_id]
    exact_record = bool(
        expected_items and _records_equal(expected_items[0].get("record"), expected.tool_record)
    )
    final_reply = ""
    for event in events:
        if event.get("type") == "result" and isinstance(event.get("final_reply"), str):
            final_reply = str(event["final_reply"])
    positions = [final_reply.find(step) for step in expected.value["steps"]]
    steps_in_order = all(position >= 0 for position in positions) and positions == sorted(positions)
    incorrect_environment_tool = any(
        name == "observe" or name.startswith(("robot_", "browser_")) for name in tool_names
    )
    return {
        "rank": rank,
        "recall_at_1": rank == 1,
        "recall_at_5": rank is not None and rank <= 5,
        "reciprocal_rank": 0.0 if rank is None else 1.0 / rank,
        "exact_record": exact_record,
        "exact_step_order": exact_record,
        "duplicate_subject": len(subject_matches) > 1,
        "returned_ids": ids,
        "called_tools": tool_names,
        "called_search_memories": "mindmemos_search" in tool_names,
        "incorrect_environment_tool": incorrect_environment_tool,
        "final_answer_steps_in_order": steps_in_order,
        "final_reply": final_reply,
    }


def evaluate_run(
    *,
    paths: BenchmarkPaths,
    repo_root: Path,
    timeout_seconds: float,
    max_cases: int | None = None,
    runner: CommandRunner = run_command,
) -> dict[str, Any]:
    records = load_dataset(paths.dataset)
    checkpoint = json.loads(paths.checkpoint.read_text(encoding="utf-8"))
    confirmed = {int(key): value for key, value in checkpoint.get("confirmed", {}).items()}
    if len(confirmed) != 100:
        raise ValueError("evaluation requires 100 confirmed writes")
    for path in (paths.recall_results, paths.routing_results):
        if path.exists():
            path.unlink()
    by_index = {record.index: record for record in records}
    executed = 0
    results: list[dict[str, Any]] = []
    for case in evaluation_cases(records):
        if max_cases is not None and executed >= max_cases:
            break
        executed += 1
        command = [
            str(repo_root / ".venv/bin/python"),
            "-m",
            "homemaster.cli",
            "-p",
            case.prompt,
            "--output-format",
            "stream-json",
        ]
        environment = dict(os.environ)
        environment["PYTHONPATH"] = "src"
        completed = runner(command, cwd=repo_root, env=environment, timeout=timeout_seconds)
        _write_private_text(paths.raw / f"query-{case.case_id}.stdout.jsonl", completed.stdout)
        _write_private_text(paths.raw / f"query-{case.case_id}.stderr.log", completed.stderr)
        expected = by_index[case.expected_index]
        score = evaluate_search_events(
            parse_stream_events(completed.stdout),
            expected=expected,
            expected_memory_id=str(confirmed[case.expected_index]),
        )
        result = {
            "case_id": case.case_id,
            "suite": case.suite,
            "expected_index": case.expected_index,
            "expected_subject": expected.subject,
            "expected_memory_id": confirmed[case.expected_index],
            "returncode": completed.returncode,
            "timed_out": completed.timed_out,
            "elapsed_seconds": completed.elapsed_seconds,
            **score,
        }
        target_path = paths.routing_results if case.suite == "natural" else paths.recall_results
        _append_jsonl(target_path, result)
        results.append(result)
    summary = summarize(paths=paths, results=results)
    _atomic_write_json(paths.summary, summary)
    _write_private_text(paths.report, _render_report(summary, results))
    return summary


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    values: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        if isinstance(value, dict):
            values.append(value)
    return values


def summarize(*, paths: BenchmarkPaths, results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    writes = [item for item in _read_jsonl(paths.write_results) if item.get("state") == "confirmed"]
    forced = [item for item in results if item.get("suite") != "natural"]
    natural = [item for item in results if item.get("suite") == "natural"]
    return {
        "write_success_rate": len(writes) / 100,
        "recall_at_1": _mean(forced, "recall_at_1"),
        "recall_at_5": _mean(forced, "recall_at_5"),
        "mean_reciprocal_rank": _mean(forced, "reciprocal_rank"),
        "exact_step_order_accuracy": _mean(forced, "exact_step_order"),
        "distractor_confusion_rate": 1.0
        - _mean([item for item in forced if item.get("suite") == "distractor"], "recall_at_1"),
        "duplicate_record_rate": _mean(forced, "duplicate_subject"),
        "natural_memory_routing_rate": _mean(natural, "called_search_memories"),
        "natural_final_answer_accuracy": _mean(natural, "final_answer_steps_in_order"),
        "incorrect_environment_routing_rate": _mean(natural, "incorrect_environment_tool"),
        "write_latency_p50_seconds": _percentile(writes, "elapsed_seconds", 0.50),
        "write_latency_p95_seconds": _percentile(writes, "elapsed_seconds", 0.95),
        "search_latency_p50_seconds": _percentile(results, "elapsed_seconds", 0.50),
        "search_latency_p95_seconds": _percentile(results, "elapsed_seconds", 0.95),
        "evaluated_cases": len(results),
        "failed_cases": [
            str(item["case_id"])
            for item in results
            if not item.get("recall_at_5") or not item.get("exact_record")
        ],
    }


def _mean(values: Sequence[Mapping[str, Any]], key: str) -> float:
    if not values:
        return 0.0
    return sum(
        float(bool(item.get(key))) if isinstance(item.get(key), bool) else float(item.get(key, 0.0))
        for item in values
    ) / len(values)


def _percentile(values: Sequence[Mapping[str, Any]], key: str, fraction: float) -> float:
    ordered = sorted(float(item.get(key, 0.0)) for item in values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]


def _render_report(summary: Mapping[str, Any], results: Sequence[Mapping[str, Any]]) -> str:
    lines = ["# HomeMaster Memory Recall Benchmark", "", "## Summary", ""]
    for key, value in summary.items():
        if key != "failed_cases":
            lines.append(f"- `{key}`: {value}")
    lines.extend(("", "## Failed or ambiguous instances", ""))
    failed = [
        item for item in results if not item.get("recall_at_5") or not item.get("exact_record")
    ]
    if not failed:
        lines.append("None.")
    else:
        for item in failed:
            lines.append(
                f"- `{item['case_id']}` expected `{item['expected_subject']}`; "
                f"rank={item.get('rank')}, tools={item.get('called_tools')}"
            )
    lines.append("")
    return "\n".join(lines)


def status_run(paths: BenchmarkPaths) -> dict[str, Any]:
    records = load_dataset(paths.dataset)
    checkpoint = json.loads(paths.checkpoint.read_text(encoding="utf-8"))
    confirmed = checkpoint.get("confirmed", {})
    counts = Counter(record.kind for record in records)
    return {
        "run_id": checkpoint["run_id"],
        "root": str(paths.root),
        "records": len(records),
        "distribution": dict(counts),
        "confirmed_total": len(confirmed),
        "remaining": len(records) - len(confirmed),
        "last_state": checkpoint.get("last_state"),
        "summary_exists": paths.summary.exists(),
        "cleanup_available": False,
    }


__all__ = [
    "BenchmarkPaths",
    "BenchmarkRecord",
    "CompletedCommand",
    "build_dataset",
    "build_write_prompt",
    "evaluate_run",
    "evaluate_search_events",
    "evaluation_cases",
    "generate_run",
    "load_dataset",
    "parse_stream_events",
    "status_run",
    "write_run",
]
