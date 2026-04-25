# HomeMaster LLM Debug Cases

这个目录保存 HomeMaster V1.2 每轮 LLM 测试的输入、预期和实际结果，方便人工 debug。

每个 case 使用固定结构：

```text
stage_xx/<case_name>/
  input.json
  expected.json
  actual.json
  result.md
```

约定：

- `input.json` 保存阶段入口输入和必要上下文，不保存 API key。
- `expected.json` 保存关键预期字段和通过条件。
- `actual.json` 保存 Mimo 返回的结构化输出和程序裁剪后的关键执行结果。
- `result.md` 保存通过/失败结论、失败原因、对应日志路径和 debug 备注。
- 真实 LLM 默认使用 `config/api_config.json` 中的 `Mimo / mimo-v2-pro`；如果该文件不存在，当前代码会兼容回退到旧 provider config。
- 规则通过、fake provider 通过、schema 单测通过都不算阶段通过；必须真实调用 LLM 并通过阶段校验。
