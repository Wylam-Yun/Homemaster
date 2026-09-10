# ALFWorld Benchmark Hanghan 环境踩坑复盘

> 日期：2026-09-08　机器：Hanghan（bld-G808P-V2，8x RTX 4090）
> 目标：在 Hanghan 上把 Homemaster 的 `benchmark-alfworld`（视觉评测 / AlfredThorEnv）跑通。

## ⚠️ 最关键的一条结论（先看这个）

**在这台机器上跑 Homemaster 的 ALFWorld，评测环境必须用 Python ≥ 3.10，绝不能用 3.9。**

本次折腾的源头就是一开始把 ALFWorld 环境建成了 **Python 3.9**（`alfworld39`），而 Homemaster 的
benchmark 源码用了 3.10+/3.11 语法。之前的服务器能跑通，正是因为它的 ALFWorld 环境是
`/data0/yuqiao/envs/hm_alfworld`（3.10+）。所以换机器时**装 ALFWorld 环境，Python 版本这一步
就必须 ≥3.10**；只要这一步对了，后面大半的 import 报错（`TypeGuard`/`StrEnum`/`datetime.UTC` 等）
根本不会出现，能省掉一大半排查时间。

## 背景：一句话病根

换服务器后，ALFWorld 评测环境被建成了 **Python 3.9**（`alfworld39`），而 Homemaster 主程序
**最低要求 Python 3.11**（`pyproject.toml: requires-python = ">=3.11,<3.14"`），且 benchmark 源码
用了大量 3.10+/3.11 语法。之前能跑通的服务器用的是 3.10+（`/data0/yuqiao/envs/hm_alfworld`），
这就是"换服务器就崩"的直接原因——不是数据、不是 THOR、不是目录，是 Python 版本墙。

---

## 坑 1：`ImportError: cannot import name TypeGuard from typing`（第一条表面报错）

### 症状与根因

`bash scripts/homemaster benchmark-alfworld --help` 报 `TypeGuard` 导入失败。
根因链：`scripts/homemaster` 的 benchmark 分支把 CLI 主进程解释器从 3.11 换成 `.runtime/alfworld-venv`
（3.9/3.10），又把 3.11 的 `site-packages` 塞进 `PYTHONPATH`，让旧解释器去读新库；新版 `typer`
依赖 `typing.TypeGuard`（3.10+ 才有）。

### 修法与教训

这不是数据/安装问题，而是 launcher 在两个 Python 版本间"拼环境"。`TypeGuard` 只是第一层，
修了后面还有 `StrEnum`、`datetime.UTC` 一路炸。

### Ref
- `scripts/homemaster`（benchmark 分支，第 18-44 行）
- 状态：**已修**（删除该 hack，见坑 3）

## 坑 2：连环版本语法墙（TypeGuard → StrEnum → datetime.UTC）

### 症状与根因

逐个改会依次撞：
- `from enum import StrEnum`（3.11+，见 `agent/context.py`）
- `from datetime import UTC`（3.11+，见 `benchmarking/locomo/runner.py`）
- `list | tuple` / `str | None`（PEP 604，3.10+，见 `benchmarking/alfworld/env_adapter.py`）

根因：benchmark 的 runner 会把整个 3.11-only 的 `homemaster` 包 import 进 CLI 进程，任何一个 3.9/3.10
解释器都跑不通；这是**阶梯式连锁**，不能靠"改一个符号"解决。

### 修法与教训

同一现象反复修不好 → 说明盯的不是病根。真正的病根是"benchmark in-process 直接 import 评测环境"，
而非某个具体 import。必须做 harness / 评测环境解耦。

### Ref
- `src/homemaster/agent/context.py`、`src/homemaster/benchmarking/locomo/runner.py`、
  `src/homemaster/benchmarking/alfworld/env_adapter.py`
- 状态：**绕过**（靠换 3.10 环境 + 解耦，不是逐个改符号）

## 坑 3：`scripts/homemaster` 的 benchmark 分支是人为 hack

### 症状与根因

`git log -- scripts/homemaster` 显示，那段"切解释器 + 塞 site-packages"是 8/20 两个 commit
（`cad3efe` / `f052a67`）临时加的，试图让 in-process benchmark 在 3.10 下勉强跑。

### 修法与教训

删除第 18-44 行 hack，CLI 主进程固定 3.11，`PYTHONPATH` 只加 `src/`。改后 `benchmark-alfworld --help` 恢复。

### Ref
- `scripts/homemaster`
- 状态：**已修**

## 坑 4：ALFWorld 环境版本墙（3.9 vs 3.10+）

### 症状与根因

Hanghan 上 alfworld 环境是 `alfworld39`（3.9）。Homemaster benchmark 源码用 3.10+ 语法，3.9 worker
一到 `adapter.reset()` 就 `TypeError: unsupported operand type(s) for |`。

### 修法与教训

新建 Python 3.10 环境 `alfworld310`，借 `vllm` 环境的 3.10.19 解释器 `python -m venv` 建（机器上
**没有 conda/uv 命令**，`miniconda3` 软链指向的路径已不存在）。把 ALFWorld 依赖装齐并对齐原 3.9 基准版本。

### Ref
- `/home/hanghan/.conda/envs/alfworld310`
- 状态：**已修**

## 坑 5：机器上没有 conda/uv/mamba，建不了新环境

### 症状与根因

`which conda uv mamba` 全空；`/home/hanghan/miniconda3` → `/data3/hanghan/miniconda3`（不存在）；
`/data3/hanghan/.conda` 只剩 `envs/` 和 `pkgs/`，conda 本体没了。系统只有 `/usr/bin/python3` = 3.8.10。

### 修法与教训

借用现有 3.10+ 环境（`vllm`=3.10.19 / `sam3`=3.12 / `open_manus`=3.12）的解释器 + `python -m venv` 建 venv，
不依赖 conda。

### Ref
- 状态：**已修**

## 坑 6：`pip install -e .` 报 `FileNotFoundError: python`

### 症状与根因

ALFWorld `requirements.txt` 的 `textworld[pddl]` 会拉 `fast-downward-textworld`，其构建脚本在
build isolation 里调 `check_call(["python", ...])`，但 venv 里只有 `python3.10` 没有 `python` 命令。

### 修法与教训

两处：
1. venv 里 `ln -s` 补 `python`（注意：venv 的 `python3.10/python3` 本身就是软链指向 `python`，直接
   建 `python -> python3.10` 会循环，要指向真实二进制 `/home/hanghan/.conda/envs/vllm/bin/python3.10`）。
2. 更根本：`textworld` 不需要 `[pddl]` extra（pddl 的 fast-downward 只用于生成 `.tw-pddl` 数据，
   运行 benchmark 用预生成数据不需要），改 `pip install textworld`（不带 pddl）+ `pip install --no-deps alfworld`。

### Ref
- `/home/hanghan/weilin/ALFWorld/requirements.txt`、`setup.py`
- 状态：**已修**

## 坑 7：numpy 2.x / opencv 5 版本漂移

### 症状与根因

新 venv 里 `textworld` 无版本约束拉进 `numpy==2.2.6`、`opencv-python 5.0.0.93`；而原 3.9 基准是
`numpy 1.26.4` + `opencv 4.11.0.86`。numpy 2.x 与 torch 2.8 / 老代码兼容风险高。

### 修法与教训

强制对齐基准：`numpy==1.26.4`、`opencv-python==4.11.0.86`。torch 装的是 `2.8.0+cu128`（清华源自动
给了 CUDA 版，与原 3.9 的 cu128 一致）。

### Ref
- 状态：**已修**

## 坑 8：worker 健康校验拒绝 editable-install 的 alfworld

### 症状与根因

worker smoke 报 `worker imported ALFWorld from the asset checkout instead of its environment`。
`http_client.py::_verify_health` 强制要求 worker 里 `alfworld.__file__` **不在** asset checkout 下，
必须来自环境自己的 site-packages。我用 `--no-deps -e .` 装的 editable 直接指向 checkout，触发拒绝。

### 修法与教训

改非 editable 安装（`pip install --no-deps .`），alfworld 复制进 site-packages，校验通过。

### Ref
- `src/homemaster/benchmarking/alfworld/http_client.py::_verify_health`
- 状态：**已修**

## 坑 9（核心）：benchmark-alfworld 的 harness / 评测环境未解耦

### 症状与根因

`benchmark-alfworld` 走 `AlfworldBenchmarkRunner` → `_build_adapter` → `build_alfworld_batch_env`，
**in-process** import `alfworld`/`textworld`。CLI（3.11）import 不到只在 3.10 环境里的评测引擎，
报 `ModuleNotFoundError: textworld`。

### 修法与教训

harness（3.11 主进程）与评测环境（3.10）解耦：runner 改走 `AlfworldHttpEnvironment`（HTTP worker），
worker 用 `AlfworldHttpEnvironment.start()` spawn 3.10 子进程跑 THOR。视觉后端 tool 层只调
`go_to_target` / `manipulate_with_thor`，这俩 worker client 已实现；补了 `reset()`、`set_frame_dir()`。

### Ref
- `src/homemaster/benchmarking/alfworld/runner.py`（`_build_worker_adapter` / `run()` 分支）
- `src/homemaster/benchmarking/alfworld/http_client.py`（补 `reset` / `set_frame_dir`）
- `scripts/homemaster`（注入 `HOMEMASTER_ALFWORLD_PYTHON`）
- 状态：**已改，核心链路已跑通**（worker 拉起 + THOR reset + LLM 调起）

## 坑 10（未定位，待修）：Neo4j 初始密码命令失败

### 症状与根因

走完解耦后，完整 benchmark 在 `start_file_memory` → `managed_neo4j.start()` → `_initialize_new_database`
报：
```
ManagedNeo4jError: Neo4j initial password command failed: --verbose Enable verbose output
```
疑似 `neo4j-admin` 初始化密码命令的参数被错误解析（错误串里是 neo4j-admin 的 `--verbose` help 文本）。
这是 memory 层（Neo4j）独立问题，与 ALFWorld/worker 解耦无关，由 `config/homemaster.yaml` 的
`memory.enabled: true` 触发。

### 修法与教训

**尚未定位根因（Hanghan 进入维护，未继续 debug）。** 临时绕法：`memory.enabled: false` 可跳过，
让 ALFWorld 动作执行链路先跑通；Neo4j 作为独立问题单独修。

### Ref
- `src/homemaster/memory/managed_neo4j.py::_initialize_new_database`（约 330-345 行）
- 状态：**未定位，待修**

---

## 当前整体状态

| 项 | 状态 |
|---|---|
| 根因定位（版本墙 + 未解耦） | ✅ |
| Python 3.10 环境 `alfworld310` | ✅ 装齐并对齐基准 |
| `.runtime/alfworld-venv` 软链 | ✅ 指向 3.10 |
| `scripts/homemaster` hack | ✅ 已删 |
| harness ↔ 评测环境解耦 | ✅ 已改通，核心链路验证通过 |
| 完整 benchmark 端到端 | ❌ 被 Neo4j（坑 10）挡住 |
| 代码改动同步/commit | ❌ 改动目前只在 Hanghan 工作树，未 commit |

