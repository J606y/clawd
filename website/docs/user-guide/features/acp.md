---
sidebar_position: 11
title: "ACP Host Integration"
description: "Use clawd inside ACP-compatible editors and collaboration platforms"
---

# ACP Host Integration

Python dependency commands on this page use a
[PM-prepared source checkout](../../reference/package-management.md#developer-workflow).
After a dependency change, reactivate the checkout and restart clawd.

clawd can run as an ACP server, letting ACP-compatible hosts talk to
clawd over stdio. Editors can render:

- chat messages
- tool activity
- file diffs
- terminal commands
- approval prompts
- streamed thinking / response chunks

Other hosts can use the same protocol to route collaboration events into
clawd. ACP is a good fit when you want clawd to keep its existing identity,
provider setup, memory, skills, and tools while another application owns the
conversation transport.

## What clawd exposes in ACP mode

clawd runs with a curated `hermes-acp` toolset designed for editor workflows. It includes:

- file tools: `read_file`, `write_file`, `patch`, `search_files`
- terminal tools: `terminal`, `process`
- web/browser tools
- memory, todo, session search
- skills
- execute_code and delegate_task
- vision

It intentionally excludes things that do not fit typical editor UX, such as messaging delivery and cronjob management.

The toolset resolves the same way as on the messaging gateway for the same
platform config. That includes the extras the gateway adds on top of the
list, such as enabled plugin toolsets, so ACP sessions get those too.
`platform_toolsets.acp` replaces the `hermes-acp` default, and
`agent.disabled_toolsets` removes toolsets from every ACP session. MCP
servers from `mcp_servers` follow the same rules too. By default ACP gets
every enabled server. If you list server names in `platform_toolsets.acp`,
only those servers are included, and `no_mcp` drops them all. `clawd tools`
has no ACP entry, so edit `config.yaml` directly:

```yaml
platform_toolsets:
  acp: [file, web, skills, github]   # only the github MCP server
agent:
  disabled_toolsets: [code_execution]
```

MCP servers that the editor sends with `session/new` are separate. The
client asks for them per session, and they are always added.

## Editor-provided MCP servers

On gateways advertising `acp-session-mcp-v1`, editor `mcpServers` belong to the
canonical session, not the ACP viewer process. The gateway uses separate registry
namespaces even when two editors supply the same server name. Closing an editor
does not cancel another session's MCP execution.

Discovery is eager and bounded before the first model turn. The selected tool
schemas remain frozen for the conversation; server tool-list changes require a
new session. MCP transports are released after each execution, without stopping
sibling sessions or the shared MCP loop.

The gateway keeps the editor transport specification (including command arguments,
environment values and HTTP headers) only in memory. Durable session policy holds
a fingerprint and the selected schemas, not a copy of those credentials. After a
gateway restart, the editor must supply the same `mcpServers` on load/resume.
Missing credentials refuse execution; a changed specification or secret refuses
reattachment rather than silently changing the session. Start a new session when
changing the server configuration. Commands and paths run on the gateway host.

## Installation

Install clawd normally, then add the ACP extra from the install checkout:

```bash
cd ~/.hermes/hermes-agent && python -c "import pm; pm.sync_venv(['acp'], explicit=True)"
```

This installs the `agent-client-protocol` dependency and enables:

- `clawd acp`
- `hermes-acp`
- `python -m acp_adapter`

## Launching the ACP server

Any of the following starts clawd in ACP mode:

```bash
clawd acp
```

```bash
hermes-acp
```

```bash
python -m acp_adapter
```

clawd logs to stderr so stdout remains reserved for ACP JSON-RPC traffic.

For non-interactive checks:

```bash
clawd acp --version
clawd acp --check
```

### Browser tools (optional)

Browser tools (`browser_exec` and the CDP tools `browser_cdp`,
`browser_dialog`, `browser_vault`) need a local Chromium, which isn't part of
the Python wheel. Install it with:

```bash
clawd acp --setup-browser           # interactive (prompts before ~400 MB download)
clawd acp --setup-browser --yes     # accept the download non-interactively
```

This is the standalone command. The terminal-auth flow (`clawd acp --setup`) also offers the browser bootstrap as a follow-up question after model selection, so most users never need to run `--setup-browser` directly.

What it does:

- Installs clawd's pinned Chromium (the `chromium` package) through the package manager; no Node.js or npm involved
- clawd launches that Chromium itself when `browser_exec` first needs a browser

The bootstrap is idempotent — re-running it is fast and skips work that's already done.

## Host setup

### Buzz channels (relay bridge)

[Buzz](https://github.com/block/buzz) is a Nostr-based collaboration platform
for people and agents. Its `buzz-acp` harness connects Buzz channels to any ACP
agent over stdio:

```text
Buzz relay <-- WebSocket --> buzz-acp <-- ACP over stdio --> clawd
```

This is a transport integration, not a second clawd installation. The
subprocess launched by `buzz-acp` uses the same clawd configuration,
credentials, memory, skills, and state as `clawd` on that host.

(This is distinct from [Buzz Desktop's managed runtime](#buzz-desktop), which
spawns clawd locally as a preset harness. The relay bridge is for joining Buzz
*channels* as an agent identity, typically on a server.)

Prerequisites:

- Complete the ACP installation and `clawd acp --check` above.
- Build `buzz-acp` and the `buzz` CLI from the
  [Buzz repository](https://github.com/block/buzz)
  (`cargo build --release -p buzz-acp`).
- Mint a dedicated Nostr keypair for clawd (`buzz-admin generate-key`) and
  register it as a relay member (`buzz-admin add-member`). Every agent needs
  its own identity — do not reuse a human keypair.
- Add that identity to the intended Buzz channels.

Start a bridge with:

```bash
export BUZZ_RELAY_URL="wss://community.example.com"
export BUZZ_PRIVATE_KEY="..."
export BUZZ_API_TOKEN="..."
export BUZZ_ACP_AGENT_COMMAND="clawd"
export BUZZ_ACP_AGENT_ARGS="acp"

buzz-acp
```

`BUZZ_API_TOKEN` is needed only when the relay enforces token authentication.
Do not commit or paste the private key or API token.

For a persistent server deployment, run `buzz-acp` under a service manager as
the same operating-system user that owns the intended clawd home. Setup,
key generation, channel discovery, and per-agent options are documented in the
[buzz-acp README](https://github.com/block/buzz/tree/main/crates/buzz-acp).

The bridge discovers every Buzz channel where the clawd identity is a member
and automatically subscribes when it is added to another channel. Buzz channel
membership therefore remains the access boundary; clawd does not need a
separate channel list in its own configuration.

To expose clawd ACP activity in the owner's Buzz Desktop, add:

```bash
export BUZZ_ACP_RELAY_OBSERVER="true"
```

This publishes encrypted kind `24200` observer frames addressed to the agent's
owner (Buzz's NIP-AO). Desktop renders the live lifecycle, tool, response, and
usage stream in the agent's **Activity log**. The relay treats these frames as
ephemeral, so Desktop must be online before the turn starts; its local observer
archive is the durable owner-side history.

Headless bridges answer ACP permission requests themselves because no editor
is present to show approval dialogs — see
[Keep Buzz agents owner-only](#keep-buzz-agents-owner-only). Treat the bridge
as privileged automation: use a dedicated operating-system account, restrict
which Buzz users can prompt the agent (`buzz-acp` supports an owner-only
respond gate via `BUZZ_ACP_AGENT_OWNER`), and grant membership only in channels
where clawd is expected to work.

### VS Code

Install the [ACP Client](https://marketplace.visualstudio.com/items?itemName=formulahendry.acp-client) extension.

To connect:

1. Open the ACP Client panel from the Activity Bar.
2. Select **clawd** from the built-in agent list.
3. Connect and start chatting.

If you want to define clawd manually, add it through VS Code settings under `acp.agents`:

```json
{
  "acp.agents": {
    "clawd": {
      "command": "clawd",
      "args": ["acp"]
    }
  }
}
```

### Zed

Configure clawd as a custom agent server in Zed settings:

1. Open the Agent Panel.
2. Add a custom agent server with the following configuration:

```json
{
  "agent_servers": {
    "hermes-agent": {
      "type": "custom",
      "command": "clawd",
      "args": ["acp"]
    }
  }
}
```

3. Start a new clawd external-agent thread.

Prerequisites:

- Configure clawd provider credentials first with `clawd model`, or set them in `~/.clawd/.env` / `~/.clawd/config.yaml`.

### JetBrains

Use an ACP-compatible plugin and point it at `clawd acp` or `hermes-acp`.

### Buzz Desktop

[Buzz](https://github.com/block/buzz) ships clawd as a preset runtime.
With clawd installed the normal way, Buzz discovers it automatically —
open **Settings → Runtimes** and clawd appears under your runtimes.

If discovery fails (older installs), make sure the ACP launcher resolves on a
login-shell PATH:

```bash
command -v hermes-acp || command -v clawd
```

Recent installs write both `clawd` and `hermes-acp` launchers into
`~/.local/bin`; running `hermes update` adds the `hermes-acp` launcher to
older installs. As a manual fallback, configure Buzz's agent command as
`clawd` with args `["acp"]`.

#### Model picker

Buzz Desktop (v0.5.1+) renders clawd's full model menu in the agent's runtime
settings. The list comes from clawd itself over ACP: it shows every model
from providers you have authenticated in clawd (the same inventory behind
`clawd model` and the `/model` command), so a model missing from the menu
means its provider has no credentials configured on the clawd side.

Entry IDs take the form `provider:model` (e.g. `openrouter:z-ai/glm-5.1`), or
`custom:<name>:<model>` for custom OpenAI-compatible endpoints defined in
`config.yaml`. Picking a model applies to that agent's session; it does not
change your Hermes-wide default — use `clawd model` for that.

#### Keep Buzz agents owner-only

Buzz creates every agent with **Who can talk to this agent** set to `Owner only`.
Leave it there when the runtime is clawd.

Two behaviors combine on this path. The `hermes-acp` toolset includes `terminal`
and `execute_code`, and Buzz's ACP bridge answers clawd's permission requests
itself with `allow_once` rather than surfacing them. A clawd agent in Buzz
therefore runs shell commands on the host without prompting. I asked one to run
`rm -rf` against a scratch directory and it deleted it, no prompt anywhere.

Selecting `Anyone` hands that same shell access to every author who can reach
the channel. Buzz does not warn when you pick it.

`approvals.mode: manual` does not help: clawd raises the permission request,
but Buzz auto-approves it and the command still runs. To take the shell away,
narrow the toolset instead: set `platform_toolsets.acp` to a list without
`terminal` and `code_execution`, or add them to `agent.disabled_toolsets`.
Even an empty `platform_toolsets.acp: []` still adds enabled plugin
toolsets, so name any plugin toolset you want gone in
`agent.disabled_toolsets`.

`!shutdown` from the owner stops the agent in any mode, and Buzz ignores that
command from everyone else.

## Configuration and credentials

ACP mode uses the same clawd configuration as the CLI:

- `~/.clawd/.env`
- `~/.clawd/config.yaml`
- `~/.clawd/skills/`
- `~/.clawd/state.db`

Provider resolution uses clawd's normal runtime resolver, so ACP inherits the currently configured provider and credentials. clawd also advertises a terminal auth method (`--setup`) for first-run ACP clients; this opens clawd's interactive model/provider setup.

## Host integration

Older ACP hosts may still set this variable on the clawd subprocess they
spawn. It is not user configuration — do not set it by hand in `.env` or
`config.yaml`.

| Variable | Value | Effect |
|----------|-------|--------|
| `HERMES_ACP_SKIP_CONFIGURED_MCP` | `1` | **No effect.** clawd no longer reads it. `clawd acp` / `hermes-acp` runs as a viewer of the gateway daemon and never starts the `config.yaml` MCP servers itself, so there is no ACP-side startup to skip. |

The ACP process does no MCP discovery. The gateway daemon starts the
`mcp_servers` from its own profile's `config.yaml` once at gateway boot, and ACP
sessions get those servers through the `platform_toolsets.acp` rules
[above](#what-hermes-exposes-in-acp-mode). Setting the variable on the ACP
subprocess or on the gateway does not change that. Use `platform_toolsets.acp`
or `no_mcp` to keep configured servers out of ACP sessions.

The editor's `session/new` `mcpServers` are passed to the gateway and attached
to that canonical session (see
[Editor-provided MCP servers](#editor-provided-mcp-servers)), whether or not
the variable is set.

## Session behavior

ACP sessions are tracked by the ACP adapter's in-memory session manager while the server is running.

Each session stores:

- session ID
- working directory
- selected model
- current conversation history
- cancel event

Conversations are persisted to clawd's session database and can be listed, loaded,
resumed, or forked after the ACP server restarts. Opening a new session without a
prompt keeps it in memory only: model-discovery probes do not create empty history
rows. A nonempty fork is persisted immediately, and existing session metadata can
still be updated even when its current history is empty.

Existing empty rows from older versions are not automatically deleted. An open ACP
row does not prove its client has disconnected. After closing the relevant editor
sessions, inspect unwanted rows with `clawd sessions show <id>` and remove only
confirmed unwanted sessions with `clawd sessions delete <id>`.

## Working directory behavior

ACP sessions bind the editor's cwd to the clawd task ID so file and terminal tools run relative to the editor workspace, not the server process cwd.

## Approvals

Dangerous terminal commands can be routed back to the editor as approval prompts. ACP approval options are simpler than the CLI flow:

- allow once
- allow always
- deny

Whether you actually see a prompt is up to the host. A host is free to answer the
request programmatically instead of showing it to you, in which case these
options exist on the wire but never reach a human. Buzz Desktop does this, so
treat that path as unattended execution regardless of your `approvals` setting.

On timeout or error, the approval bridge denies the request. The wait is
`approvals.timeout` from `config.yaml` (default 300 s), the same knob the CLI and
gateway prompts use — raise it if your editor keeps approval cards open longer.

### Session-scoped edit auto-approval

ACP exposes a third tier between *allow once* and *allow always*: **Allow for session**. Picking it from the editor's permission prompt records the approval inside the current ACP session only — every subsequent matching command in that session goes through without prompting, but a new ACP session (or restarting the editor) resets the slate and re-prompts the first time.

| Option | Editor label | Scope | Persisted across restarts |
|---|---|---|---|
| `allow_once` | Allow once | This one tool call | No |
| `allow_session` | Allow for session | All matching calls in this ACP session | No — cleared when the session ends |
| `allow_always` | Allow always | All future sessions | Yes (written to the clawd permanent allowlist) |
| `deny` | Deny | This one tool call | No |

`allow_session` is the right default for an editor workflow where you trust an agent for the duration of a task but don't want to grant a long-lived allowlist entry. The safety trade-off is straightforward: the broader the scope, the less the editor will interrupt you, and the more damage a misbehaving agent (or prompt injection) can do before you notice. Start with `allow_once` for unfamiliar commands; promote to `allow_session` once you've seen the agent run the same pattern correctly a few times; reserve `allow_always` for truly idempotent commands you trust forever (e.g. `git status`).

The ACP bridge maps these options onto clawd's internal approval semantics — `allow_always` writes a permanent allowlist entry the same way the CLI does, while `allow_session` only affects the in-process approval cache for the current ACP session.

## Troubleshooting

### ACP agent does not appear in the editor

Check:

- For manual/local development, verify the host command points to `clawd acp`.
- clawd is installed and on your PATH.
- The ACP extra is installed (`cd ~/.hermes/hermes-agent && python -c "import pm; pm.sync_venv(['acp'], explicit=True)"`).

### ACP starts but immediately errors

Try these checks:

```bash
clawd acp --version
clawd acp --check
clawd doctor
clawd status
```

### Missing credentials

ACP mode uses clawd's existing provider setup. Configure credentials with:

```bash
clawd model
```

or by editing `~/.clawd/.env`. The terminal auth flow (`clawd acp --setup`) can also trigger the interactive provider/model setup.

## See also

- [Buzz ACP harness](https://github.com/block/buzz/tree/main/crates/buzz-acp)
- [ACP Internals](../../developer-guide/acp-internals.md)
- [Provider Runtime Resolution](../../developer-guide/provider-runtime.md)
- [Tools Runtime](../../developer-guide/tools-runtime.md)
