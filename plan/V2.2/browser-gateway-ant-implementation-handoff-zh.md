# Browser Gateway 与 Ant Mock 实施交接

## 当前状态

- 日期：2026-07-29
- 状态：实现、文档、确定性 Ant 外部门、真实 Provider 外部门和最终代码评审处置已完成。
- 正式计划：`plan/V2.2/browser-gateway-ant-implementation-plan-zh.md`
- 验证报告：`docs/reports/2026-07-29-browser-gateway-ant-verification.md`
- 明日目标：从飞书发送含变更单地址的自然语言要求，模型操作 Ant Mock，每个重要动作
  `observe` 发图，并用 `browser_backfill` 回填和确认。

## 环境关键事实

- HomeMaster：`/home/haodong2/weilin/red_bird/Homemaster`
- 独立 Ant Mock：`http://127.0.0.1:8002/dashboard/automation`
- ignored `config/homemaster.yaml` 的 `browser_gateway` 已指向 8002。
- Ant 进程：npm PID 1074500，UtooPack PID 1074534。
- 现有 ALFWorld Gateway PID 812568，未停止、未替换；其飞书连接仍属于当前部署。

## 展示启动

一个 Gateway 进程只能选择一个环境。受控停止现有 ALFWorld Gateway 后，用同一真实配置启动：

```bash
cd /home/haodong2/weilin/red_bird/Homemaster
PYTHONPATH=src .venv/bin/python -m homemaster.cli --gateway --browser \
  --config config/homemaster.yaml
```

不要同时启动两个使用同一飞书 app 的 Gateway。飞书消息只需给自然语言要求和可读取的票据
地址；不要求 JSON 附件。Browser prompt 和唯一通用 Skill 均不包含当前票 SOP。

## 尚待部署步骤

1. 展示前受控切换 Gateway mode，并从真实飞书发一条 canary 变更单消息。
2. 在飞书端逐张确认重要动作图和回填确认图；这一步是部署展示，不属于当前离线替换操作。

Browser 绑定不配置 `60/60` 等数值预算，而是令工具迭代不设上限，避免长票据被默认 12 次
截断。GT 的完整正常和异常回滚分支是冻结规范；当前 Ant UI 实际跑通正常实现段，其余保持
`UNVERIFIED`。
