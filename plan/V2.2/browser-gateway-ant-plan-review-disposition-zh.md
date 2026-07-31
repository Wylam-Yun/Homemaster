# Browser Gateway 计划评审处理

日期：2026-07-29。评审只执行一次，reviewer 未修改文件。

1. Browser profile 暴露高权限通用工具：owner 明确不采纳裁剪；browser 必须保留
   Home 通用工具，新浏览器工具只是新增。风险由部署 owner 接受。
2. final origin/redirect：部分采纳。实现导航后 final origin 核对，并让配置 start URL
   偏离时失败；不扩展为完整 popup 安全工程。
3. 真实飞书门：部分采纳。不修改已工作的 Feishu Channel；保留既有全链回归，并验证
   browser wrapper 从 ChannelBridge request 到 Gateway MEDIA/final。展示时由 owner
   从真飞书触发。
4. Ant 只是 Mock UI：采纳。所有代码、文档和汇报明确称 Mock UI 演示。
5. JSON 附件严格解析：不采纳。owner 锁定正文只传变更单 URL，模型用既有通用工具读；
   browser 代码不读取附件。
6. browser prompt：采纳。新增独立内容文件，不修改 Home/ALFWorld/Coworker prompt；
   精确内容和测试纳入实现。
7. Ant 部署与异常清理：采纳可复现启动/健康门和 browser run 清理测试；HomeMaster
   不拥有或重启 Ant server。

## Owner 评审后修订

2026-07-29 owner 锁定：只增加一个通用变更单执行 Skill，不增加领域 Skill；具体 SOP
必须从飞书正文中的变更单动态读取。GT 必须覆盖当前票完整正常和异常回滚轨迹。为支持
逐 step 截图回填，新增一个通用 `browser_backfill` clipboard-image 工具，不修改飞书
Channel。该 owner 决策已写回正式计划，不追加第二次计划评审。
