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
