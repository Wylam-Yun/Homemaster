# HomeMaster Web 只读记忆管理页设计

日期：2026-08-24  
状态：已与用户确认

## 1. 背景

HomeMaster 当前的 React Web Console 只提供对话、会话恢复、实时事件、工具展示、审批和 artifact 查看。浏览器通过同一个 loopback-only FastAPI 服务访问 `/api/sessions`、`/api/events` 等接口，尚无记忆页面或记忆读取接口。

长期记忆由应用进程拥有的 `EmbeddedMindMemOS` 管理。原始 `MemoryView` 包含正文、类型、状态、时间、`session_id` 和版本关系等信息，但现有公开 `get()` 只返回 active 搜索投影，会丢失 `session_id`，且底层 reader 默认单页 50 条，不适合直接作为管理页数据源。

2026-08-24 对当前 `local` project 的只读核对结果为 101 条 active、17 条 archived，共 118 条。该数字只是设计时观测值；页面必须动态读取，不能硬编码。

## 2. 目标

首版增加一个中文、纯只读的“记忆管理”页面：

- 动态展示“生效中的记忆”“已归档的记忆”“记忆总数”“来源会话数”；
- 分别浏览生效中和已归档记忆；
- 以记忆自身的 authoritative `session_id` 分组，同一 session 的记忆只进入同一组；
- 使用该 session 第一条用户消息作为分组标题，并显示短 session ID 和记忆数量；
- 支持会话分组折叠、关键词搜索、记忆类型筛选和记忆详情查看；
- 展示归档原因和已有版本历史；
- 与现有对话页面共用左侧导航，并让历史会话列表可折叠；
- 保留未来“管理操作”和“记忆入库审核”的清晰扩展边界。

## 3. 非目标

首版不提供：

- 新增、编辑、删除、归档或恢复记忆；
- 待审核状态、审核队列或审核操作；
- 新的 Agent 工具调用，尤其不调用 `mindmemos_add`；
- 前端直接访问 Qdrant、Neo4j 或本地文件；
- 新的独立 Web 服务、远程认证方案或非 loopback 网络暴露；
- 服务端分页、虚拟列表、复杂缓存或增量推送。

## 4. 关键决策

### 4.1 复用现有本地 Web 服务

“Web API”指现有 Web Console 使用的同一个 FastAPI adapter，不是新建远程服务。新增读取接口与 `/api/sessions` 同源，并继续受 `serve` 入口的 loopback-only 绑定约束。纯 CLI 模式不启动 Web server，也就不暴露这些接口。

远端服务器上的 Web Console 仍通过 SSH tunnel 访问。首版不改变当前“无应用层认证、只允许 loopback”的安全边界。

### 4.2 新增只读 MemoryManagementService

新增应用层 `MemoryManagementService`，专门把 MindMemOS 原始记忆投影成浏览器需要的稳定 DTO。Web adapter 只依赖这个服务，不直接访问 MindMemOS 私有字段，也不直接访问数据库。

服务职责：

- 从 authoritative tenant/project scope 构造 MindMemOS request context；
- 遍历底层 cursor，完整读取 active 与 archived `MemoryView`；
- 保留 `session_id`，解析安全的结构化 record，并提取归档原因；
- 通过现有 SessionManager 的只读会话存储读取第一条用户消息；
- 生成统计、稳定排序、分组标题和详情 DTO；
- 读取准确 memory ID 的既有版本历史；
- 对损坏或缺失数据做单条降级，不让一条坏记录击穿整页。

`EmbeddedMindMemOS` 应新增正式、只读的管理查询方法，由该类内部使用其 reader。任何上层代码都不得读取 `_reader`、`_qdrant` 或 `_neo4j` 私有属性。

### 4.3 数据真理源

列表、状态、正文、类型、`session_id`、时间和归档 metadata 以 Qdrant 中的 raw memory 为准。版本关系继续通过 MindMemOS 已有 history 能力读取。Neo4j 不作为另一套页面统计来源，避免把两个存储的独立计数拼成一个表面一致的结果。

## 5. 读取模型与排序

管理查询循环调用 MindMemOS reader 的 cursor API，直到 cursor 为空。不能依赖默认 50 条，也不能写死当前 118 条。

只读 snapshot 包含 active 和 archived 两类。统计定义：

- `active_count`：状态为 active 的记忆数量；
- `archived_count`：状态为 archived 的记忆数量；
- `total_count`：上述两者之和；
- `session_group_count`：非空 `session_id` 的去重数量，“未关联会话”不计入来源会话数。

分组规则：

1. 只按 raw memory 的 `session_id` 精确分组，不按正文相似度推断；
2. `session_id` 为空的记录进入唯一的“未关联会话”组；
3. 分组标题使用该 session 第一条 `role=user` 消息的完整文本；
4. 找不到会话或没有用户消息时使用“会话 `<short-id>`”；
5. 无 session 的组标题固定为“未关联会话”；
6. 分组按组内最新更新时间倒序；组内记忆也按更新时间倒序，memory ID 作为确定性次排序键。

## 6. HTTP 接口

### 6.1 记忆快照

`GET /api/memories`

成功响应示意：

```json
{
  "stats": {
    "active_count": 101,
    "archived_count": 17,
    "total_count": 118,
    "session_group_count": 42
  },
  "groups": [
    {
      "session_id": "session-id",
      "title": "第一条用户消息原文",
      "active_count": 3,
      "archived_count": 1,
      "memories": []
    }
  ]
}
```

每条 memory DTO 只包含白名单字段：

- `memory_id`
- `content`
- `memory_type` 与前端可显示的中文类型标签
- `status`
- `session_id`
- `created_at`
- `updated_at`
- `archived_at`
- `archive_reason`
- 校验成功后的安全 `record`，或结构状态标记
- `has_history`

不返回向量、原始嵌套 metadata、provider 配置、数据库路径、凭据、内部 evidence ref 或损坏的原始 `record_json`。

### 6.2 版本历史

`GET /api/memories/{memory_id}/history`

接口按当前 tenant/project scope 校验准确 ID，返回按新到旧排列的 active/archived 版本。未知、wrong-scope 或不可见 ID 统一返回稳定的 404，不泄露其他 scope 是否存在该 ID。

### 6.3 错误

错误继续使用现有稳定 JSON 形状：`code`、`message`、`retryable`。

- memory 未启用或 runtime 尚未 ready：503 `memory_unavailable`；
- snapshot 读取失败：503 `memory_read_failed`；
- history ID 不存在或不属于当前 scope：404 `memory_not_found`。

接口失败不得伪装成成功的零条结果。

## 7. 前端信息架构

现有 App 增加顶层视图状态：`conversation | memories`。

左侧栏从上到下：

1. 品牌区；
2. “新建对话”；
3. “对话”和“记忆管理”导航，“记忆管理”旁显示最新 active 数量；
4. 可折叠的“历史会话”分区；
5. loopback 状态说明。

折叠历史会话只隐藏列表，不改变当前 session、连接或对话状态。折叠状态保存在浏览器 localStorage。移动端继续使用现有 side sheet。

## 8. 记忆页面

页面结构：

- 标题“记忆管理”和手动“刷新”按钮；
- 四个中文统计项：生效中的记忆、已归档的记忆、记忆总数、来源会话数；
- “生效中”“已归档”两个标签页，标签带数量；
- 关键词搜索框和记忆类型筛选；
- 按来源 session 展示的可折叠分组；
- 点击 memory 后打开只读详情面板；
- 详情面板按需请求并展示版本历史。

搜索在首版对已加载 snapshot 做客户端过滤，匹配：

- 记忆正文；
- 分组标题；
- 完整或短 memory ID；
- 完整或短 session ID。

类型筛选保留 MindMemOS 原生类型集合。已知类型使用中文标签，未知合法类型显示原值，不能因为前端枚举较窄而丢弃记录。

分组交互：

- 标题显示第一条用户消息、短 session ID、当前标签页下的记忆数量；
- 最近的少量分组默认展开，其余默认折叠；
- 用户可以独立展开或折叠每组；
- 搜索或筛选命中时自动展开包含结果的分组；
- 清空筛选后恢复用户此前的手动折叠状态。

详情面板显示完整正文、中文类型、状态、memory ID、来源 session、创建/更新时间、归档时间、归档原因、安全结构化字段和版本历史。首版页面任何位置都不得出现新增、编辑、删除、归档或恢复按钮。

## 9. 状态与降级

- 首次加载显示 skeleton 或明确加载态，不能先闪现“0 条”；
- 刷新时保留当前 snapshot，完成后原子替换；
- 刷新失败时保留旧内容并显示可重试错误；
- 空库显示“还没有记忆”，四项统计为真实零值；
- 某个 session 不可读时只降级该组标题；
- `session_id` 缺失时进入“未关联会话”；
- `record_json` 缺失表示普通记忆；
- `record_json` 存在但校验失败时仍显示正文，详情标记“结构信息异常”，不回显损坏 JSON；
- history 读取失败只影响详情中的历史区域，不清空主列表；
- memory runtime 不可用时显示“记忆服务暂不可用”，对话页面继续工作。

## 10. 安全与只读保证

- 两个新接口只接受 GET；
- 不注册任何 memory mutation route；
- 不调用 `mindmemos_add`、update、delete、feedback 或 dreaming；
- 使用 Web local operator 的 authoritative tenant `local`，不接受浏览器提交 tenant/project；
- 所有 DTO 使用显式字段白名单；
- 服务继续只绑定 loopback；
- 真实验收在请求前后对 memory data root 做完整文件树 metadata/hash 对比，必须证明零写入。

## 11. 测试

### 11.1 后端

- cursor 超过 50 条时完整遍历且无重复；
- active、archived、total 和 distinct session 统计正确；
- 同一 session 精确归为一组，无 session 归入专组；
- 第一条用户消息标题和 fallback 正确；
- known/unknown memory type 均保留；
- structured、vanilla 和损坏 record 分别正确投影；
- archive reason、时间和 history 顺序正确；
- unavailable、read failure、unknown/wrong-scope ID 返回稳定错误；
- ASGI 集成测试锁定 `/api/memories` 与 history JSON；
- 负向测试证明不存在 memory POST/PUT/PATCH/DELETE route。

### 11.2 前端

- 四项统计和所有关键标签均为中文；
- sidebar 顶层导航和历史会话折叠正确；
- active/archived 标签切换正确；
- session 分组、默认折叠、手动折叠和筛选自动展开正确；
- 搜索与类型筛选覆盖正文、标题和 ID；
- 详情与历史 loading/error/success 正确；
- memory service 失败不影响 conversation view；
- 页面没有 mutation controls；
- HTTP client 对新接口和稳定错误做契约测试。

### 11.3 验收命令与真实 smoke

- Python Web 和 memory 相关测试；
- `npm test`；
- `npm run typecheck`；
- `npm run build`；
- 当前 local runtime 的只读 smoke 动态核对观测到的 101/17/118；
- 请求前后 memory data root 完整树对比零变化；
- 检查 stderr 无 traceback 或迟到异常。

当前 101/17/118 只用于本次真实环境 smoke，不写进产品逻辑或固定单元测试。

## 12. 未来扩展

未来的“管理操作”和“记忆入库审核”应在独立设计中增加 mutation/review service、权限、审计、待审核状态和确认流程。首版通过稳定 DTO、顶层记忆导航和独立 MemoryManagementService 预留边界，但不展示空的“待审核”入口，也不提前引入审核状态。

## 13. 预计改动范围

- `src/homemaster/memory/`：只读管理服务和 MindMemOS raw list public boundary；
- `src/homemaster/web/app.py`、`schemas.py`、`serve.py`：服务注入和两个 GET route；
- `tests/homemaster/web/` 与 memory tests：只读契约、隔离和零 mutation；
- `web/src/api/http.ts`：只读 memory DTO/client；
- `web/src/App.tsx` 和新增 memory components：导航、页面、分组、详情；
- `web/src/styles.css` 或 memory scoped styles：响应式布局；
- 前端测试与生产 static build；
- Web Console 用户指南。

不进行无关 Runtime、Agent、工具注册表或数据库架构重构。
