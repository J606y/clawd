# 新对话项目绑定：已接入

父代理已在 src/react/pages/chat/chat-page.tsx 的 onSend 无 sessionId 分支接入 prepareCreatedSession（src/react/lib/project-send.ts）。

发送开始时冻结 getPendingNewChatProjectId()，顺序为：创建会话、绑定项目并核对回执 session_id/project_id、锁定模型、启动运行。普通模式不请求绑定。绑定或锁模型失败只清理刚创建且未发送的空会话，禁止 start 和选中该会话；清理失败同时报告两个错误及会话 ID。外层恢复草稿。已有会话发送和动画未改。

父代理复验：tests/project-panel.test.mjs 共 11 项通过，typecheck/build 退出 0；日志 artifacts/parent-check/{project-panel-final,panel-typecheck-final,panel-build-final}.log（clawd 工程根目录）。这些是函数与构建验证，不是浏览器或真实模型验收。

分叉、编辑重发及压缩继承由核心另一路处理，不从浏览器项目选择推断。该路返回前不得标记完成。
