# browser-window-guard

已退役为阻断未登记浏览器工具的钩子。不再改写 `browser_exec`，不再计算自己的 task id，不再打开 `PANEL_LIVE_TASK_DB` 或本目录的影子库。

任务页登记、45 秒人工租约和关页由浏览器工具自己的 profile 登记簿负责。面板在父代理调用 `clawd_server.browser_native.install` 之后读取同一份登记。不要再安装或比对旧 Store。
