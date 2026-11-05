// Synthetic MCP list for the path-wrap regression. Not a live config.

export const SECRET_ENV = "SYNTHETIC_ENV_SECRET_7k2m";
export const SECRET_HEADER = "SYNTHETIC_HEADER_SECRET_9p4q";

export const LONG_COMMAND = "/usr/local/lib/hermes-agent/venv/bin/python" + "/segment".repeat(24);
export const SPACED_COMMAND = "chrome-devtools-mcp";
export const SPACED_ARGS = ["--browserUrl", "http://127.0.0.1:9222", "--label", "synthetic channel with spaces"];
export const LONG_URL = "http://127.0.0.1:9222/devtools/browser/" + "a1b2c3d4e5".repeat(24);
export const SHORT_COMMAND = "npx";
export const SHORT_ARGS = ["-y", "bing-webmaster-mcp"];
export const ENV_KEYS = ["BING_API_KEY_FILE", "BING_SITE_URL", "INDEXNOW_KEY_FILE"];
export const HEADER_KEYS = ["Authorization"];

export function commandTarget(server) {
  if (server.transport === "http") return server.url || "—";
  const command = [server.command, ...server.args].filter((item) => !!item).join(" ");
  return command || "—";
}

export function apiPayload() {
  return {
    data: [
      {
        name: "aux_ac",
        transport: "stdio",
        command: LONG_COMMAND,
        args: [],
        url: null,
        auth: null,
        enabled: true,
        toolAllowlist: null,
        envKeys: [],
        headerKeys: [],
        editable: true,
        env: { PATH_SECRET: SECRET_ENV },
      },
      {
        name: "chrome-devtools",
        transport: "stdio",
        command: SPACED_COMMAND,
        args: SPACED_ARGS,
        url: null,
        auth: null,
        enabled: true,
        toolAllowlist: null,
        envKeys: [],
        headerKeys: [],
        editable: true,
      },
      {
        name: "long-http",
        transport: "http",
        command: null,
        args: [],
        url: LONG_URL,
        auth: null,
        enabled: false,
        toolAllowlist: null,
        envKeys: [],
        headerKeys: HEADER_KEYS,
        editable: true,
        headers: { Authorization: SECRET_HEADER },
      },
      {
        name: "bing",
        transport: "stdio",
        command: SHORT_COMMAND,
        args: SHORT_ARGS,
        url: null,
        auth: null,
        enabled: true,
        toolAllowlist: null,
        envKeys: ENV_KEYS,
        headerKeys: [],
        editable: true,
        env: { BING_API_KEY_FILE: SECRET_ENV },
      },
    ],
  };
}

export function expectedTargets() {
  return Object.fromEntries(apiPayload().data.map((server) => [server.name, commandTarget(server)]));
}
