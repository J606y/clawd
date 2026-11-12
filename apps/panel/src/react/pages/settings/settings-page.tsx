import { useNavigate, useParams } from "react-router";
import { isSettingsTab, SETTINGS_TABS, type SettingsTab } from "@/routes";
import { AccountTab } from "./account-tab";
import { ConfigTab } from "./config-tab";
import { JobsTab } from "./jobs-tab";
import { LogsTab } from "./logs-tab";
import { McpTab } from "./mcp-tab";
import { ModelsTab } from "./models-tab";
import { PluginsTab } from "./plugins-tab";
import { SkillsTab } from "./skills-tab";
import { UpdateTab } from "./update-tab";
import { UsageTab } from "./usage-tab";
import { WebhooksTab } from "./webhooks-tab";
import "./settings.css";

function TabBody({ tab }: { tab: SettingsTab }) {
  switch (tab) {
    case "config":
      return <ConfigTab />;
    case "jobs":
      return <JobsTab />;
    case "skills":
      return <SkillsTab />;
    case "usage":
      return <UsageTab />;
    case "logs":
      return <LogsTab />;
    case "models":
      return <ModelsTab />;
    case "mcp":
      return <McpTab />;
    case "plugins":
      return <PluginsTab />;
    case "webhooks":
      return <WebhooksTab />;
    case "update":
      return <UpdateTab />;
    case "account":
      return <AccountTab />;
  }
}

export function SettingsPage() {
  const { tab } = useParams();
  const navigate = useNavigate();
  // 未知子路径显示配置，不改写 hash。与契约里的替换跳转冲突时以旧版为准。
  const shown: SettingsTab = isSettingsTab(tab) ? tab : "config";
  return (
    <section className="settings-hub" aria-label="设置">
      <div className="tabs" role="tablist" aria-label="设置">
        {SETTINGS_TABS.map((item) => (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={shown === item.id}
            onClick={() => navigate(`/settings/${item.id}`)}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div className="body">
        <div className="pane">
          <div className="page">
            <TabBody key={shown} tab={shown} />
          </div>
        </div>
      </div>
    </section>
  );
}
