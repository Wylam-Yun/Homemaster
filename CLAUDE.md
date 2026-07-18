# HomeMaster Agent Rules

## ALFWorld 外部执行纪律

- 把“已发出动作”和“外部世界已完成动作”分开。任何 THOR 功能都必须同时通过返回状态门和独立外部终态黑盒门；mock、内部 result、trace 或模型反馈不能代替外部终态。
- 导航成功必须由同一个返回 event 证明：外部返回成功、requested/actual pose 一致、准确 objectId 的 `metadata.visible=true`、准确 objectId 的正面积 bbox，以及交付图片与该 event 的 RGB 像素一致。
- Put 成功必须证明：外部返回成功、准确对象离开完整 inventory、`isPickedUp=false`、准确目标在对象 parent membership 中、准确对象在目标 child membership 中。返回与终态矛盾或读取缺失时立即停止为不确定，不得重试。
- 携带物移动成功时，只要求准确 held ID、完整 inventory 不变、准确对象仍存在、`isPickedUp=true` 和实际 pose 匹配。不得要求 `parentReceptacles` 或 `receptacleObjectIds` 不变；THOR 会在携带物经过 receptacle 时更新这些字段。
- 移动失败后，只有完整动作状态和实际 pose 都与动作前一致才可继续。Put 失败后，只有完整动作状态不变才可继续；完整状态至少包含 held ID、完整 inventory、准确对象 parent tuple、准确目标 child tuple、对象存在与 `isPickedUp`。
- 每个发给 THOR 的请求都计一个 backend action，包括 `GetReachablePositions` 等 query。请求前检查候选数、backend action 数和 wall-clock 三预算，预算到达后不得再发 N+1 请求。
- 从确定性 scene snapshot 只解析一次准确对象和目标；显式实例 miss 不得类型级 fallback。候选集合、顺序和 hash 在 context 创建时锁定，重试期间不得重新解析或重算目标。
- 真环境验收按 target/instance 独立 reset、独立断言，禁止用 best/any 或全局聚合掩盖失败。更换 ALFWorld、ai2thor 或 Unity 运行时版本后，重新执行 runtime contract 与逐实例 characterization。
- Helper 自测必须审计真实 CLI/handler 接线；新 validator 存在或 isolated fixture 通过不算接线完成。case/run verifier 必须逐 case 回读 raw setup artifacts 并独立重算，禁止只信 worker 自报计数。
- 共享 schema 迁移时，把同一份 committed payload 直接喂给每个真实 consumer；禁止在 synthetic fixture 中补回生产 payload 已删除的字段。目标 mutation 必须核对具体拒绝原因，不能把其他缺字段异常算 PASS。
- 跨 producer/consumer 的 synthetic shared-schema fixture 必须由真实 producer 生成，或从 producer 的实际 coverage rule 重算并逐 ID 审计；禁止手工补出真实生产路径不会发布的行或字段。
- 外部 transaction 完成后的下游派生、序列化或汇总失败，仍必须保留已完成的 raw refs、逐动作 rows、返回码和真实 action count；不得退化成零计数最小错误。
- 在仓库外临时目录运行 Ruff 等项目工具时，显式传入仓库真配置；临时默认配置的 PASS 不得作为项目门。同步后必须在正式仓库路径复跑。
- 多命令验收脚本必须 fail-fast 或逐命令断言返回码；禁止用最后一条成功命令的 exit 0 掩盖前序 lint、format、测试或外部验证失败。
- Gate-only missing/sentinel ID 必须在任何真实 object/snapshot/oracle lookup 前分流：闭式验证其规范名称、冻结序号、真实集合中不存在和全部派生 binding；普通 ID 不得借此绕过真实 authority。
- 真实 Gate 失败修复后，先用修复字节离线重放该失败 run 的完整不可变 raw artifact；synthetic 自测转绿不能替代这条正交回归。
- 缺失错误和 terminal 分类必须基于最终锁定的 target/anchor/context 状态：先执行全部高优先级正常解析（如 direct snapshot 覆盖 parent anchor），再判断失败；禁止把中间候选缺失提前永久写入 issues。
- 不得用 containment/parent 关系或动作意图推导相机可见性。关系从动作前 raw state 独立重算，授权只取该动作精确返回 event 的 `visible=true` 与正 bbox；把所有允许 outcome 预先闭式冻结，再由返回 event 选择，independent verifier 必须用 raw artifacts 重新选择并覆盖每个 outcome。
