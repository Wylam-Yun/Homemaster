# scripts/ — 脚本索引

本目录分两组：**现役入口**（日常使用、随当前代码演进）与**版本验收归档**（对应某一
版本里程碑的一次性验收脚本，保留为不可变证据）。

## 现役入口

- `homemaster` — 仓库 launcher：锁定 `config/homemaster.yaml` 与 `.runtime/venv`，
  逐项缺失即 fail closed；`benchmark-alfworld*` 子命令额外校验 `.runtime/alfworld`
  绑定并导出 `ALFWORLD_DATA`，最后 `exec python -m homemaster.cli`。
- `setup.sh` — 薄 shim，转发到 `homemaster setup --runtime-root <repo>/.runtime`；
  支持 `--with-browser` 与 `--offline <bundle>`。
- `setup-alfworld.sh` — 把 ALFWorld worker 的 Python、源码与数据绑定到
  `.runtime/alfworld-venv`/`.runtime/alfworld`。
- `build_runtime_bundle.sh` — `./scripts/build_runtime_bundle.sh --output PATH
  [--with-browser]` 生成离线运行时 bundle（源码或 wheel）。
- `download_runtime_assets.py` — 下载并安装 `config/runtime-assets.lock.json`
  锁定的运行时资产；薄封装，实现在 `homemaster.setup_runtime`，优先解析本仓库
  `src/`。
- `setup_memory_runtime.py` — 把单台服务器的 Java、Neo4j、Python 与 memory root
  绑定到本 checkout；`check` 子命令由 launcher 在每次启动前运行。
- `export_service_tool_specs.py` — 从 HomeMaster 自有默认 Registry 重新生成
  service tool 元数据。
- `guard_no_legacy_terms.py` — 扫描已跟踪文件中的历史遗留术语；默认强制模式
  发现违规退出 1，`--report-only` 只报告。
- `build_opencli_browser_vendor.mjs` — 从 vendored OpenCLI 1.8.7 dist 重新生成
  `src/homemaster/browser/generated/opencli_1_8_7/` 下的浏览器辅助 JS。
- `memory_recall_benchmark.py` — 100 条合成记忆的召回基准
  （`generate`/`overnight` 子命令），支持 checkpoint/resume。

## 版本验收归档

以下脚本各对应一个版本里程碑的验收门，已交付后保留为历史证据，不随日常流程运行。

- `verify_v34_permissions.py` — V3.4 家庭资源权限验收：外部黑盒门 + 证据输出
  （仅 `process` backend，其余明确 unsupported）。
- `verify_v34_thor.py` — V3.4 真机 THOR 门：固定 trial 的 reset/read 稳定性、
  跨 scene 隔离、真实 nav+take 与独立 inventory 回读。
- `v34_approval_server.py` — V3.4 轻量验收 server：真实 HTTP/WS 包 file store，
  供 Task 9 verifier 以外部进程驱动、kill/restart。
- `v34_alfworld_demo.py` — V3.4 真实 ALFWorld trial 上的交互式审批演示。
- `verify_v35_architecture.py` — V3.5 确定性架构与打包审计，独立于 pytest 的
  静态门，live run 前最后一道。
- `verify_v35_release.py` — V3.5 发布边界验收：从干净构建产物复核。
- `verify_v35_gateway_smoke_live.py` — V3.5 live 门：全部配置的 Gateway smoke
  trial 经真实 THOR worker 跑通并逐条落证据。
- `verify_v35_worker_use_live.py` — V3.5 黑盒：隔离 worker 上 Oracle-grounded
  take/use 终态。
- `verify_v35_taskset_live.py` — V3.5 双子任务 taskset 跑通并逐 subtask 落
  黑盒证据。
- `verify_v35_taskset_worker_live.py` — V3.5 黑盒：隔离 taskset worker 的
  reset/set_task/action/close。
- `verify_v35_harness_failure_live.py` — V3.5 黑盒：隔离 worker 真实失败路径。
- `verify_v35_sigint_live.py` — V3.5 黑盒：action 与 close 两个边界的真实
  SIGINT 验收。
- `v19_release/` — V1.9 移植基线：`capture_baseline.py`（§24.2 行为基线）、
  `capture_environment_identity.py`（候选环境身份）、
  `validate_upstream_port_manifest.py`（对照不可变 Git 源字节校验
  `plan/V1.9/upstream-port-manifest.json`）。
- `v20/` — V2.0 验收：`verify_skill_installation.py` 跑 Skill 安装的外部
  终态门。
- `ops_monitor_agent/materialize_trajectory.py` — V3.0：为一次 Ops Monitor
  Agent run 生成私有确定性证据 bundle。
