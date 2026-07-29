# V2.2 ALFWorld Gateway 实施计划评审处置

## 评审结论

- Reviewer verdict：`FIX`
- 评审次数：1（正式计划唯一一次评审）
- 处置：7 项全部采纳；按项目规则不追加计划复审。

## 逐项处置

1. **lock source 与运行时 checkout 身份不一致 — 采纳。**
   - Gateway 改为执行 `uv.lock` 安装的 upstream 精确 commit。
   - ignored ALFWorld checkout 只提供 data/config assets，不进入 `sys.path`。
   - wheel 门核对 PEP 610 direct-url、实际 import origin、关键源码 digest，并用同一 wheel source 做真实 reset/frame/close。

2. **最后一个普通 iteration 后可能无 observe — 采纳。**
   - observation-required action 一旦触达 backend，进入独立、有界 observation grace。
   - grace 覆盖协议重试、observe 重试和一次图片后 Provider 消费；不授权普通预算耗尽后的新 action。
   - 增加最后 iteration、连续失败、resume 近预算边界测试。

3. **single-session owner 非原子且 frozen binding 冲突 — 采纳。**
   - mutable owner 独立为 application-owned `AlfworldSessionOwner`，用 `asyncio.Lock` 原子 claim/seal。
   - owner 保持到 application close；首 run 普通失败不转让已变化 episode。
   - 增加两个 session `asyncio.gather()` 竞态黑盒。

4. **snapshot 只验存回、未验真实首次消费 — 采纳。**
   - 增加 observe snapshot → crash → runtime rebuild → 首个 transport request 携图的完整边界测试。
   - Provider request 失败时保留图片；真实 response 成功提交后才清未消费 id，后续 snapshot 才剥离。

5. **Registry 收窄会误伤其他入口 — 采纳。**
   - one-shot/shell/dry-run 显式选择 `local_robot`，保持原能力。
   - 普通 Gateway 选择 common-only；ALFWorld/Coworker 各自显式选择。
   - 每个 live caller 增加 Provider request 边界工具名单测试。

6. **ALFWorld metadata 可能给另一来源背书 — 采纳。**
   - 不用 distribution version 给 local checkout 背书。
   - 代码身份由实际 import origin、PEP 610 commit、锁定文件 digest 和真实外部终态同源证明。

7. **外部契约 UNVERIFIED 标记不完整 — 采纳。**
   - 计划已统一标记 `alfworld[full]` resolve/install、`AlfredThorEnv` 构造、trial pin runtime 字段、Xvfb 探活、固定 demo episode、Mimo forced tool choice 和飞书接收侧图片重读。
   - 只有各自真机返回码与外部终态通过后才解除。
