# HomeMaster V3.2：可复现核心与 ALFWorld 解耦

状态：待书面审阅
日期：2026-09-02
目标分支：`main`

## 1. 版本目标

V3.2 先把 HomeMaster 收敛成可从干净 Git checkout 复现的通用 Agent，再把
ALFWorld benchmark 实现迁到 `/home/haodong2/weilin/red_bird/alfworld`。两个阶段独立
提交、独立验收，禁止交叉实施。

本目录包含两份规范：

- [`repository-hygiene-spec.md`](repository-hygiene-spec.md)：清理、路径可移植、环境复现、
  安全和安装边界。
- [`alfworld-extraction-spec.md`](alfworld-extraction-spec.md)：通用应用扩展接口和 ALFWorld
  跨仓库迁移边界。

## 2. 已锁定决策

1. HomeMaster 以后只维护 `main`；旧功能分支不再作为长期发布线。
2. 必须先通过仓库清理与环境复现验收，之后才能开始 ALFWorld 外迁。
3. 记忆系统是 HomeMaster 核心能力，不支持无记忆产品模式。
4. 浏览器是可选能力；通用 Agent 安装不强制下载 Playwright Chromium。
5. 记忆部署固定为 HomeMaster 管理的本地 Neo4j；不提供 external 远程模式，也不能关闭记忆。
6. HomeMaster 保留唯一的 CLI、Web 和飞书 Gateway 入口。
7. ALFWorld 仓库依赖 HomeMaster 的稳定接口；HomeMaster 不依赖、不导入、不判断
   ALFWorld。
8. 用户只编辑 `config/homemaster.yaml`。仓库保留去敏模板，但 setup 自动生成真实配置，
   README 不要求用户手工复制模板，也不把某个编辑器命令写成复现步骤。
9. V3.2 离线包不包含 ALFWorld、ALFWorld 数据集、AI2-THOR 或历史运行 trace。

## 3. 实施顺序和门禁

```text
R0 保护基线与用户改动
  -> R1 安全、垃圾和硬编码清理
  -> R2 在线/离线环境复现验收
  -> R3 HomeMaster 通用 application/environment 接口
  -> R4 ALFWorld 实现迁入 ALFWorld 仓库
  -> R5 删除 HomeMaster 的 ALFWorld 专名和兼容分支
  -> R6 两仓库独立构建与真实终态验收
```

- R0–R2 由清理 spec 管辖。
- R3–R6 由 ALFWorld 外迁 spec 管辖。
- 任一阶段的黑盒门失败，后续阶段不得开始。
- 每个提交只属于一个阶段，不能用一次大提交同时做清理和迁移。

## 4. 统一依赖方向

```text
homemaster_benchmark（ALFWorld 仓库）
    ├── ALFWorld / AI2-THOR
    └── HomeMaster 稳定 application/environment API

HomeMaster
    ├── Agent Runtime
    ├── 必选 Memory Runtime
    ├── 通用工具与应用组合
    ├── 可选 Browser Runtime
    ├── Web
    └── 飞书 Gateway
```

禁止出现反向边：

```text
HomeMaster -> alfworld
HomeMaster -> homemaster_benchmark
```

## 5. V3.2 完成定义

只有同时满足以下条件，V3.2 才算完成：

- 干净 checkout 可以按文档复现完整记忆版 HomeMaster。
- `./scripts/setup.sh` 不安装浏览器；`--with-browser` 才安装并验证 Chromium。
- setup 不继承另一台机器的 `.venv`、`.runtime` 或绝对路径。
- `doctor --json` 不泄露任何凭据。
- hkust4 与 HPC2_Outside 分别通过干净目录验收。
- HomeMaster 可在完全没有 ALFWorld 包的环境中安装、启动和运行。
- ALFWorld benchmark 可从自己的仓库安装并运行真实 episode。
- ALFWorld Web/飞书运行仍通过 HomeMaster 唯一入口，没有复制的 server 或 Gateway。
- 两个仓库的用户指南、架构文档、README 和 CHANGELOG 与代码一致。

## 6. 当前工作区保护

编写本规范时，hkust4 的 HomeMaster 工作区已有 9 个不属于本规范的 `plan/*.md` 删除。
这些用户改动不得被 V3.2 spec 提交、清理脚本或后续实现隐式恢复、提交或删除。
