# clawd Project & Memory API Contract

本文档定义 clawd 项目（Projects）、会话归属（Session Project Binding）及专属记忆（Project Memory）的核心 HTTP API 规范。供面板（`apps/panel`）、TUI 及自动化集成消费。

---

## 1. 认证与作用域 (Authentication & Scoping)

- 所有接口均遵循核心 `APIServerAdapter` 的鉴权机制：请求头必须携带有效 Bearer Token (`Authorization: Bearer <API_SERVER_KEY>`)。适配器未提供 `_check_auth` 时拒绝（401），不得放行。
- JSON 正文必须是 object。数组、字符串或其他非 object 返回 `400` (`invalid_json`)。
- 当处于多 Profile 代理模式 (`gateway.multiplex_profiles`) 时，路径前缀支持 `/p/{profile}/api/...`，操作严格隔离于指定 Profile。
- 项目与会话状态持久化于 Profile 专属的主目录（`CLAWD_HOME` 或 `HERMES_HOME`）。

---

## 2. 项目管理接口 (Projects)

### 2.1 列出所有项目
- **路径**: `GET /api/projects`
- **查询参数**:
  - `include_archived` (boolean, 可选, 默认 false): 是否包含已归档项目。
- **响应状态**: `200 OK`
- **响应体**:
```json
{
  "projects": [
    {
      "id": "p_a1b2c3d4",
      "slug": "my-project",
      "name": "My Project",
      "description": "Optional description",
      "icon": "folder",
      "color": "#3b82f6",
      "primary_path": "/root/my-project",
      "folders": [
        {
          "path": "/root/my-project",
          "label": null,
          "is_primary": true,
          "added_at": 1730680000
        }
      ],
      "archived": false,
      "created_at": 1730680000,
      "session_count": 3
    }
  ]
}
```

### 2.2 创建项目
- **路径**: `POST /api/projects`
- **请求体**:
```json
{
  "name": "My Project",             // 必填，非空字符串
  "primary_path": "/root/my-proj",  // 必填，绝对路径。相对路径与 `~` 不解析。
  "create_directory": false,        // 可选，默认 false。仅显式为 true 时才创建不存在的目录。已存在的文件拒绝。
  "slug": "my-project",             // 可选，小写字母、数字、短横线，不提供则由名称派生
  "description": "...",             // 可选
  "icon": "folder",                 // 可选
  "color": "#3b82f6",               // 可选
  "board_slug": null,               // 可选
  "allow_duplicate_path": false     // 可选，默认 false，不允许绑定已有项目的相同主路径
}
```
- **响应状态**:
  - `201 Created`:
    ```json
    {
      "project": { ...完整项目对象... }
    }
    ```
  - `400 Bad Request`: 参数不合法，如未提供名称、名称为空、未提供目录、相对路径、路径是文件、目录不存在且未显式 `create_directory`、主目录已被绑定，或 JSON 不是 object。

### 2.3 获取单个项目详情
- **路径**: `GET /api/projects/{id}`
- **响应状态**:
  - `200 OK`: `{"project": { ... }}`
  - `404 Not Found`: 项目不存在。

### 2.4 更新项目详情 / 归档
- **路径**: `PATCH /api/projects/{id}`
- **请求体**: 可包含任意可更新字段：
```json
{
  "name": "New Name",
  "description": "New Desc",
  "icon": "star",
  "color": "#10b981",
  "board_slug": "...",
  "archived": true                  // 归档标记。归档不删除任何物理文件、记忆或会话。
}
```
- **响应状态**:
  - `200 OK`: `{"project": { ... }}`
  - `404 Not Found`: 项目不存在。

---

## 3. 会话与项目归属 (Sessions & Project Binding)

### 3.1 获取项目下的会话列表
- **路径**: `GET /api/projects/{id}/sessions`
- **响应状态**:
  - `200 OK`:
    ```json
    {
      "project_id": "p_a1b2c3d4",
      "sessions": [
        {
          "session_id": "sess_12345",
          "title": "Fix bug in parser",
          "created_at": 1730681000,
          "last_activity_at": 1730682000,
          "message_count": 8
        }
      ]
    }
    ```
  - `404 Not Found`: 项目不存在。

### 3.2 查询会话的项目归属
- **路径**: `GET /api/sessions/{session_id}/project`
- **响应状态**:
  - `200 OK`:
    ```json
    {
      "session_id": "sess_12345",
      "project_id": "p_a1b2c3d4",     // 若为普通会话无归属，则为 null
      "project": { ... }             // 若为普通会话则为 null，否则为简要或完整项目信息
    }
    ```

### 3.3 显式绑定 / 移动会话归属
- **路径**: `POST /api/sessions/{session_id}/project`
- **请求体**:
```json
{
  "project_id": "p_a1b2c3d4"        // 目标项目ID。若传入 null 或空字符串，表示解除项目归属转为普通会话
}
```
- **约束与行为**:
  - **会话必须存在**: 目标会话在当前 profile 的 state store 中不存在时拒绝，返回 `404` (`session_not_found`)。不能验证会话存储时拒绝（`503`），不得当作空闲。
  - **活动会话保护**: 若会话正在执行中（核心 `require_idle` 拒绝：turn lease、compression lock、`session_busy` / `unknown_execution`），拒绝移动，返回 `409 Conflict` (`session_busy`)。未知错误 fail closed，不按空闲放行。
  - **非跨库原子**: 空闲检查与项目绑定写在 state 写锁回调内串行，防止检查后立刻开工的窗口；这不是 projects.db 与 state.db 的单一提交，不宣称原子。
  - **历史不变性**: 会话的历史消息和过去的交互不重写，历史内容不自动提升或提炼到新项目记忆中。
  - **下一步生效**: 绑定完成后，下一次回合或该会话的后续交互将自动加载新项目的记忆与上下文。子代理只在同一 profile 内继承项目 ID；跨 profile 不搬父项目 ID。
- **响应状态**:
  - `200 OK`: `{"success": true, "session_id": "...", "project_id": "..."}`
  - `404 Not Found`: 会话不存在或目标项目不存在。
  - `409 Conflict`: `{"error": "session_busy", "message": "Cannot change project of an active session"}`
  - `503 Service Unavailable`: 无法核实会话空闲或会话存储不可用（fail closed）。

---

## 4. 项目专属记忆管理 (Project Memory)

项目记忆由所属项目 ID 隔离，物理存储于 `CLAWD_HOME/projects/<id>/memory/` 目录。
普通聊天只使用全局记忆 (`$HERMES_HOME/memories/`)，项目模式同时加载全局记忆与本项目专属记忆。

### 4.1 读取项目记忆
- **路径**: `GET /api/projects/{id}/memory`
- **响应状态**:
  - `200 OK`:
    ```json
    {
      "project_id": "p_a1b2c3d4",
      "memory": {
        "entries": [
          "Repo uses Python 3.14 with asyncio.",
          "Tests are run via scripts/run_tests.sh."
        ],
        "usage": "72/2,200 chars",
        "char_count": 72,
        "char_limit": 2200
      }
    }
    ```
  - `404 Not Found`: 项目不存在。

### 4.2 受控更新项目记忆
- **路径**: `POST /api/projects/{id}/memory`
- **请求体**: 遵循与通用 `memory_tool` 相同的高安全契约，支持原子操作批处理：
```json
{
  "operations": [
    {
      "action": "add",
      "content": "New architecture guideline."
    },
    {
      "action": "remove",
      "old_text": "Outdated convention."
    },
    {
      "action": "patch",
      "old_text": "v1 API",
      "content": "v2 API"
    }
  ]
}
```
- **安全门禁与规则**:
  - 必须进行内容扫描（阻止 prompt 注入、密钥外泄等 threat patterns）。
  - 原子写入与文件排他锁保证并发安全。
  - 容量超限检测与友好提示。
- **响应状态**:
  - `200 OK`:
    ```json
    {
      "success": true,
      "message": "Applied 1 operation(s).",
      "usage": "110/2,200 chars",
      "entry_count": 2
    }
    ```
  - `400 Bad Request`: 操作参数错误、威胁检测拦截或容量超限。
  - `404 Not Found`: 项目不存在。

---

## 5. 旧文件夹迁移方案与回滚 (Legacy Folders Migration)

- **只读分析与显式映射**: 旧版前端或配置中若存在没有路径的文件夹（folders），系统严禁猜测绑定或擅自操作真实代码库。
- **显式迁移接口**: 仅当用户明确提供 `(folder_id, primary_path)` 的映射确认后，才将文件夹升级为绑定目录的项目。
- **回滚保障**: 任何由文件夹升级为项目的数据操作均保留在迁移元表或日志中，项目归档或解绑不执行任何破坏性删除，保证会话与历史可全量追溯。
