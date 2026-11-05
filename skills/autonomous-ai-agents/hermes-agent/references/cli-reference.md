# clawd CLI Reference

Live sources when anything looks stale: `clawd --help`, `clawd <command> --help`,
https://hermes-agent.nousresearch.com/docs/reference/cli-commands

### Global Flags

```
clawd [flags] [command]        (no subcommand = interactive chat)

  --version, -V             Show version
  -z, --oneshot PROMPT      One-shot: print ONLY the final response (for scripts/pipes)
  -m MODEL  --provider P    Model/provider override for this invocation
  -t, --toolsets LIST       Comma-separated toolsets for this invocation
  --resume, -r SESSION      Resume session by ID or title
  --continue, -c [NAME]     Resume by name, or most recent session
  --worktree, -w            Isolated git worktree mode (parallel agents)
  --skills, -s SKILL        Preload skills (comma-separate or repeat)
  --profile, -p NAME        Use a named profile
  --yolo                    Skip dangerous command approval
  --tui / --cli             Force the Ink TUI / classic REPL
  --ignore-rules            Skip AGENTS.md/SOUL.md/memory/skill injection
  --safe-mode               Disable ALL customizations (troubleshooting)
  --pass-session-id         Include session ID in system prompt
```

### Chat

```
clawd chat [flags]
  -q, --query TEXT          Single query, non-interactive
  --image PATH              Attach a local image to a single query
  -Q, --quiet               Suppress banner, spinner, tool previews
  --checkpoints             Enable filesystem checkpoints (/rollback)
  --max-turns N             Cap tool-calling iterations
  --source TAG              Session source tag (default: cli)
```
(plus the global flags above)

### Configuration

```
clawd setup [section]      Wizard (model|tts|terminal|gateway|tools|agent)
clawd model                Interactive model/provider picker
clawd fallback [add|remove|list]  Fallback provider chain
clawd config [show|edit|get|set|unset|path|env-path|check|migrate]
clawd login / logout       OAuth sign-in / clear stored auth
clawd doctor [--fix]       Check dependencies and config
clawd status [--full]      Component summary (--full: every section)
```

### Tools & Skills

```
clawd tools [list|enable NAME|disable NAME]   Per-platform toolsets (curses UI with no args)

clawd skills list|browse|search QUERY|inspect ID
clawd skills install ID    Hub identifier OR a direct https://…/SKILL.md URL
clawd skills config        Enable/disable skills per platform
clawd skills check|update|uninstall|publish PATH
clawd skills tap add REPO  Add a GitHub repo as a skill source
clawd bundles              Skill bundles (one /<name> alias loads several skills)
```

### MCP Servers

```
clawd mcp add NAME (--url or --command) | remove | list | test NAME
clawd mcp catalog | install NAME     Curated catalog install
clawd mcp configure NAME             Toggle tool selection
clawd mcp-serve                      Run clawd as an MCP server (mcp-serve plugin)
```
Details (transport, tool discovery, catalog): `references/native-mcp.md`.

### Gateway (Messaging Platforms)

```
clawd gateway run|install|start|stop|restart|status|setup
```

20+ platforms: Telegram, Discord, Slack, WhatsApp (Baileys + Business Cloud API), iMessage (Photon — `clawd photon setup`), Signal, Email, SMS, Matrix, Mattermost, Teams, LINE, SimpleX, ntfy, Google Chat, Home Assistant, DingTalk, Feishu, WeCom, Weixin, API Server, Webhooks. Open WebUI connects via the API Server adapter. Most adapters ship under `plugins/platforms/`.
Docs: https://hermes-agent.nousresearch.com/docs/user-guide/messaging/

### Sessions

```
clawd sessions list|browse|rename ID TITLE|delete ID|export OUT|prune|stats
```

### Cron / Webhooks

```
clawd cron list|create SCHED|edit ID|pause|resume|run ID|remove|status
    Schedules: '30m', 'every 2h', '0 9 * * *', ISO timestamp
clawd webhook subscribe NAME|list|remove NAME|test NAME
```
Webhook payloads/routes: `references/webhooks.md`.

### Profiles

```
clawd profile list|create NAME (--clone|--clone-all|--clone-from)|use|show|delete
clawd profile rename A B | alias NAME | export NAME | import FILE
clawd profile migrate-identity A B   Retry a completed rename's session/routing identity migration
```

### Credentials & Pools

```
clawd auth                 Interactive credential manager
clawd auth add [PROVIDER]  Add OAuth or API-key credential (nous, openai-codex, qwen-oauth, …)
clawd auth list|remove P IDX|reset PROVIDER|status
```
Multiple credentials per provider form a pool that rotates automatically and skips exhausted keys.

### Other

```
hermes desktop / gui        refused: clawd does not provide the official desktop app
hermes dashboard            refused: clawd does not provide the official web dashboard. Use `clawd panel`.
clawd proxy                OpenAI-compatible local proxy backed by an OAuth provider
clawd portal               Quick setup / sign in via Nous Portal
clawd kanban <verb>        Multi-agent work-queue board
clawd project              Named multi-folder workspaces (see references/projects-and-folders.md)
clawd skin list|use|set    Switch/tweak skins (see references/themes.md)
clawd pets <verb>          Pet mascots (see references/petdex.md)
clawd memory setup|status|off|reset   Memory provider
clawd secrets bitwarden|onepassword   External secret stores
clawd moa                  Mixture-of-Agents slots
clawd hooks / security / backup / import / checkpoints / console
clawd logs [-f] [errors]   View agent/error logs
clawd send                 One-off message through a gateway platform
clawd pairing / plugins / insights / journey / computer-use
clawd acp                  ACP server (IDE integration)
clawd completion bash|zsh|fish
hermes update               refused: clawd does not run the upstream Hermes update
clawd uninstall
```

Plugin- and provider-supplied subcommands (e.g. `clawd photon setup`) only appear once their plugin is installed/active. Importing from OpenClaw, Claude Code, Codex CLI or Cursor (`clawd import-agent <agent>`, plus the older `clawd claw migrate|cleanup`) comes from the `agent-importers` plugin; a fresh `clawd setup` that finds one of those agents offers to install it.

### Where to Find Things

| Looking for... | Location |
|---|---|
| Config options | `clawd config edit` · [Configuration docs](https://hermes-agent.nousresearch.com/docs/user-guide/configuration) |
| Tools / toolsets | `clawd tools list` · [Tools reference](https://hermes-agent.nousresearch.com/docs/reference/tools-reference) |
| Skills catalog | `clawd skills browse` · [Skills catalog](https://hermes-agent.nousresearch.com/docs/reference/skills-catalog) |
| Provider setup | `clawd model` · [Providers guide](https://hermes-agent.nousresearch.com/docs/integrations/providers) |
| Env variables | `clawd config env-path` · [Env vars reference](https://hermes-agent.nousresearch.com/docs/reference/environment-variables) |
| Gateway logs | `~/.hermes/logs/gateway.log` (or `hermes logs`) |
| Sessions | `clawd sessions browse` (reads state.db) |
