export const SETTINGS_TABS = [
  { id: "config", label: "配置" },
  { id: "jobs", label: "定时任务" },
  { id: "skills", label: "技能" },
  { id: "usage", label: "用量" },
  { id: "logs", label: "日志" },
  { id: "models", label: "模型" },
  { id: "mcp", label: "MCP" },
  { id: "plugins", label: "插件与配置档" },
  { id: "webhooks", label: "Webhooks" },
  { id: "update", label: "更新" },
] as const;

export type SettingsTab = (typeof SETTINGS_TABS)[number]["id"];

export function isSettingsTab(value: string | undefined): value is SettingsTab {
  return SETTINGS_TABS.some((tab) => tab.id === value);
}

export function settingsTabLabel(tab: SettingsTab): string {
  return SETTINGS_TABS.find((item) => item.id === tab)?.label ?? "配置";
}
