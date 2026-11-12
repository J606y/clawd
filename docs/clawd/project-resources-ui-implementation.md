# 项目档案 / 指示 / 记忆界面

父代理已在 `ProjectHome` 渲染 `ProjectResources`。本路只新增独立组件和契约客户端，没有改 `chat-page.tsx`，没有跑测试、构建、提交或部署。

## 新增文件

此前都不存在，没有做字节备份。

- `/root/hermes-workspace/clawd/apps/panel/src/react/pages/projects/project-resources.tsx`
- `/root/hermes-workspace/clawd/apps/panel/src/react/pages/projects/project-resources.css`
- `/root/hermes-workspace/clawd/apps/panel/src/react/lib/project-resources.ts`
- `/root/hermes-workspace/clawd/apps/panel/tests/project-resources.test.mjs`
- `/root/hermes-workspace/clawd/apps/panel/tests/project-resources-browser.mjs`
- `/root/hermes-workspace/clawd/apps/panel/tests/project-resources-fixture.html`

目标机：`list_servers` 确认 `beqCprBT` = HUAWEI CLOUD SG SERVER。只通过 moss `run` / `write_file` 写入。

## 导出

```ts
export function ProjectResources(props: {
  projectId: string;
  projectName: string;
  archived: boolean;
}): JSX.Element
```

组件自己渲染三胶囊「新增档案」「新增指示」「查看记忆」和弹层。样式只使用 `.project-resources-*`。对话行、`+ 新增对话`、出字动画不在本组件里。

## 接线

父代理已接入即可，导入应为：

```tsx
import { ProjectResources } from "@/pages/projects/project-resources";
```

或相对 `pages/chat/chat-page.tsx` 的 `../projects/project-resources`。不要再渲染旧的档案文本表单、记忆编辑表单，也不要为这三项调用 `projectsApi.memory`。CSS 由组件自己 import，不必改 `shell.css`。

`tests/project-resources.test.mjs` 会被 `tests/*.test.mjs` 自动收录。浏览器用例不在 `scripts/test-gates.mjs` 的硬编码列表里；本路不能改那个既有文件。要进 browser 门禁时，由父代理把 `tests/project-resources-browser.mjs` 加进该列表。

## 未执行的测试命令

在新加坡机的面板目录执行，本阶段没有跑：

```bash
cd /root/hermes-workspace/clawd/apps/panel
node --test --test-concurrency=1 tests/project-resources.test.mjs
node tests/project-resources-browser.mjs
```

Node 用例覆盖 schema、空成功拒绝、`project_id` 不一致、null 时间、同名不覆盖、PUT 后回读、Abort、multipart 字段名 `file`。浏览器用例覆盖 390×844、44px 触控、半屏/高弹层、顶层 `dialog`、失败态不显示空档案、记忆详情返回、迟到响应不污染另一个项目。

## 接口约定

前缀沿 `API_BASE`（`panel-api/`）和 `request()`。Cookie `credentials: "same-origin"`。不传 session，不读 token，不调用 `/memory`。

- `GET api/projects/{id}/resources`
- `PUT api/projects/{id}/resources/instructions`，JSON `{ content }`
- `POST api/projects/{id}/resources/files`，multipart 字段名 `file`。不用 `request()`，因为它会强制 `Content-Type: application/json`。
- `GET api/projects/{id}/resources/files/{name}` 用同源 fetch 取 blob，再走现有 `pageSave`。不把 API 地址放进 `<a href>`。

Unix 秒。`updated_at == null` 显示「更新时间未知」，不编造。空对象、只有 `{ success: true }`、缺字段、`project_id` 不符都拒绝，不当成空档案或已保存。指示要回读一致才关闭；失败留草稿。同名上传在本地拦住，服务端 409 也只保留错误，不改已有行。

切换项目、关闭重开、卸载都会作废 operation ticket 并 abort。上传完成时若 ticket 或 `project_id` 已不是当前项目，不显示成功。

源码树里没有搜到该 resources 路由的 Python 实现。核心接口未落地前，界面请求会失败并显示错误，不会假装尚无档案。
