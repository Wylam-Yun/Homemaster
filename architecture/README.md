# architecture/ — 当前架构约束材料

本目录放**当前生效的架构约束文档**，与正式架构文档目录 [docs/architecture/](../docs/architecture/) 分工：

- [home-master-layer-map.md](home-master-layer-map.md) — 分层架构简图：已交付的依赖方向、
  实现位置与外部验收边界（V3.5 架构审计的入口）。
- [interview/](interview/) — 一次性面试材料（讲解提纲、交互式架构图 HTML、源码调研报告），
  非长期维护的架构文档。

正式的子系统架构文档（Runtime、记忆、权限、ALFWorld Harness、浏览器）在
[docs/architecture/](../docs/architecture/)；`plan/` 下的版本档案与验收证据见
[plan/README.md](../plan/README.md)。

## 类型检查（mypy，可选）

- 运行：`uv run mypy` 或 `.venv/bin/python -m mypy`（dev group 已随默认
  `uv sync` 安装）。配置在 `pyproject.toml` 的 `[tool.mypy]`：src layout
  （`packages = ["homemaster"]` + `mypy_path = "src"`），`ignore_missing_imports`，
  `check_untyped_defs`，`warn_unused_ignores`，不开 strict。
- 排除：`src/agentscope`、`third_party`、`browser/vendor|generated` 不检查；
  外部边界摩擦集中的子树（`alfworld.benchmark.*`、`memory.*`、`browser.*`、
  `tools.browser.*`）通过 `[[tool.mypy.overrides]]` `ignore_errors` 暂时豁免。
- 当前基线：**293 errors / 67 files**（chore/repo-audit-remediation 时点），
  主要集中在 `arg-type`（`str` vs `Literal` 边界）与 `union-attr`（Optional
  未窄化）两类。
- 收紧计划：按模块逐个删除 overrides 中的 pattern，先清 `arg-type`/`union-attr`
  两大类，再逐子系统提高严格度；strict 为逐模块目标而非全局开关。
