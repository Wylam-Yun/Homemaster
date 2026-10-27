# V3.8 实施计划审查记录

日期：2026-10-27

范围：对照 `mindmemos-native-lifecycle-spec.md` 审查实施计划的架构恢复、需求覆盖、路径、任务依赖和验收设计。本轮没有实施功能、运行真实存储故障注入或执行 RoboCasa；以下“已修订”只表示修订已写入计划。

## 审查方式

- 架构 reviewer：独立只读检查 Trace/Outbox、API/SDK 状态合同、跨库存储、Session 删除与任务顺序。
- 验收 reviewer：独立只读检查 spec 覆盖、R5/R20、样本顺序、隔离与污染规则、外部终态证据、测试路径与命令。
- 首轮完成后发起复审，原服务返回 403 余额不足；重新派出两位 reviewer 后完成复审。未把失败的复审请求记为审查通过。
- 主 agent 按下表逐项修订，并检查当前仓库路径、pytest 默认标记过滤、九个 R5 门映射和 Markdown 差异。
- 2026-10-27 对最终修订版再次并行复审：验收 reviewer 与架构 reviewer 均报告“阻断项：none”。验收复审确认 R20 scope 污染、live pytest 显式命令、路径/测试标注、历史版本 lineage 和 per-cell 验收已覆盖；架构复审确认 Trace/Outbox 原子性、跨库迟到写前置门、Archive intent 恢复、Skill 8 条恢复调度、operation/action reconciliation 与产品决策门已覆盖。
- 用户随后指出 trajectory compile 的用途和产物尚未定案，并询问 Session 硬删除的理由。用户进一步明确 Session DELETE 不是本轮重点；当前计划将它移出决策门与新增验收范围，保持既有本地管理行为。
- Compile 仍是独立产品决策，尚未选择维持现状、只读预览、人工编译写入或退役；Task 10 已改成先按 ADR 分支处理，不再默认冻结新写入。RoboCasa 不作为 ALFWorld compiler 的验证集。合成夹具调用 compiler 揭示 put 步骤遗漏 receptacle；样例已保存。最新文档修订尚未重新独立复审；没有运行真实 Qdrant/Neo4j fault injection 或 RoboCasa 验收。

## 发现与处置

| 发现 | 计划内处置 | 实施时必须取得的证据 |
|---|---|---|
| terminal Trace 与 Add intent 分开提交可能漏 Add | Task 4：同一 backend transaction；失败不返回 accepted；启动差集审计与幂等补建 | transaction 提交前后崩溃、磁盘/事务错误、重启后逐 Turn 对账 |
| 多主机 Outbox 仅有 SQLite 无共享协调 | 明确 SQLite 单主机、PostgreSQL 共享 Trace/Outbox；同一 Protocol 与事务合同 | 两 backend 一致性；跨进程 claim；多主机误配 readiness 拒绝 |
| Feedback/Dreaming 等只有 checkpoint，没有完整公开对账合同 | Task 2、5–9：稳定 submit/status/action-history、payload hash、typed unknown、sync/async SDK parity | 外部写成功但回执丢失后的状态查询与逐 action 原始读回 |
| Neo4j fencing 无法阻止迟到的 Qdrant 写覆盖后继结果 | Task 2 前置技术门：推荐不可变 version points + 事务性有效版本指针；所有读取入口验证可见性 | A 延迟、B 接管并更新、A 最后抵达后，所有读取/搜索仍见 B，archive 不复活 |
| Archive Snapshot 与 Dreaming intent 有丢失窗口 | Task 7：边界、Add IDs、Dreaming intent 同事务；启动差集恢复 | snapshot 后、intent 前崩溃，重启不漏且不重复 Dreaming |
| 第 8 条 Skill Trace 已落库但 Evolution job 未建时可能漏调 | Task 8：启动及 admission 扫描，确定顺序 claim 8 条并同事务建 batch/job intent | 精确崩溃点恢复；7/8/9/16 与并发 claim 逐条消费核对 |
| “外部读回导出”可能仍出自被测代码 | Task 11：停业务 Worker 后独立只读客户端直连原始 Qdrant/Neo4j/Skill Store | 原始查询、回执、版本/关系与逐 operation verdict |
| 最终读回若要求所有早期记忆保持 active，会误报合法更新 | 按不可变 operation/version、lineage 与最终 visibility 验证 | Feedback/Dreaming 后旧版本仍可追溯、当前版本正确，无未授权变更 |
| R5/R20 无效 attempt 已产生副作用时可能污染连续 scope | 两者统一：副作用存在或无法排除就判废整个 scope，从首 cell 用新空 scope 重跑 | 保留原 attempts/scope 与新运行证据，禁止删库伪装连续通过 |
| R5 表为 10 行，正文为 9 门 | SDK/package 改为 Gate 0，九个 R5 功能门逐项对应 spec | Gate 0 回执、九组独立 R5 与新 scope R20 |
| 最终上游 pin 在源码改动前就要求完成，顺序冲突 | Task 1 只锁起始基线，Task 12 Gate 0 冻结最终 revision/package | 不可变 commit、lock、wheel 元数据、源码外空 venv import |
| 默认 pytest 排除 live tests，可能空跑也误称通过 | 显式 live 命令；要求 selected > 0、必要用例零 skip/xfail | JUnit 与真实用例清单、返回码、独立原始终态 |
| 配置/测试路径错误或新建文件未标清 | 改用实际资源模板、profile modules 与 MindMemOS tests/api、tests/infra/db；标明待创建测试 | 对应 Task 创建测试并从正确仓库根目录执行 |
| trajectory compile 用途/产物尚未定案 | 计划曾把可能移除误写成已决定；现在作为产品决策门，并说明成功轨迹生成 Procedure、失败轨迹生成诊断，`verified` 不代表未来任务验证 | 用户决定保留/预览/移除后，按 ADR 调整 Task 10 文件清单、Task 12 R5 数量和外部验收、Task 13 文档审计 |
| Session delete 被扩大为产品决策 | 用户明确这不是本轮重点；现有本地会话删除保持不变，从决策门和新增任务范围中移除，不新增软删除或 MindMemOS 级联语义 | 只保留既有 Session API 回归；Memory CRUD 仍照原规格作为单独能力验证 |

用户最新反馈同步：Session DELETE 仅是本地会话管理，本轮不重设计它。上文有关将 Session 删除列为待决项的审查过程属于历史记录，当前以 spec 和实施计划的“保持现状、移出产品决策门”为准。最新范围调整仅做文档一致性检查，未重新独立复审。

## 未锁定项目

1. 隐式 Feedback 正式运行时刻/时区和 Server 部署、运维、认证、scope 映射。
2. trajectory compile/derived experience 采用何种产品分支。Session DELETE 根据用户最新反馈保持现状，不再作为实施前决策。
3. 跨库恢复协议的真实技术验证：推荐方案涉及上游存储与读取合同，需要先验证并记录 ADR；实现若改变 spec 的用户语义，须先更新 spec。

这些项目不影响本轮“写计划并派两位 agent 审查”的完成；依赖它们的实施/发布仍受计划中的决策和验证门约束。
