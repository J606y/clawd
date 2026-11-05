<p align="center">
  <img src="assets/banner.png" alt="clawd" width="100%">
</p>

# clawd

This checkout's product name is **clawd**. It is not spelled clawed, and it is not the upstream product Hermes Agent.

clawd is derived from [Hermes Agent](https://github.com/NousResearch/hermes-agent) by [Nous Research](https://nousresearch.com). The source pin recorded in [`docs/clawd/source-map.md`](docs/clawd/source-map.md) is `c314285f077e91937c995d419b9fa40af9eb4981`. This README does not reassign copyright or license. `LICENSE` stays MIT, `Copyright (c) 2025 Nous Research`.

`assets/banner.png` is still the upstream artwork. It was not regenerated. There is no clawd website, installer host, or issue tracker declared in this checkout. Links below that use `hermes-agent.nousresearch.com` or `github.com/NousResearch/hermes-agent` are upstream Hermes Agent facts.

<p align="center">
  <a href="https://hermes-agent.nousresearch.com/docs/"><img src="https://img.shields.io/badge/Upstream%20docs-hermes--agent.nousresearch.com-FFD700?style=for-the-badge" alt="Upstream Hermes Agent documentation"></a>
  <a href="https://discord.gg/NousResearch"><img src="https://img.shields.io/badge/Discord-Nous%20Research-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Nous Research Discord"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License: MIT"></a>
  <a href="README.zh-CN.md"><img src="https://img.shields.io/badge/Lang-中文-red?style=for-the-badge" alt="中文"></a>
  <a href="README.ur-pk.md"><img src="https://img.shields.io/badge/Lang-اردو-green?style=for-the-badge" alt="اردو"></a>
  <a href="README.es.md"><img src="https://img.shields.io/badge/Lang-Español-orange?style=for-the-badge" alt="Español"></a>
</p>

## What you run

| Fact | Value |
| --- | --- |
| Product command | `clawd` |
| Checkout launcher | `./clawd` (does not replace a system `hermes` command) |
| Console script | `clawd = "clawd_cli.main:main"` in `pyproject.toml` |
| Web UI in this checkout | `clawd panel` |
| Data root | `CLAWD_HOME`, or `~/.clawd` when unset |

The shared CLI still has a compatibility script named `hermes`. Examples in this file use `clawd`. Packaging owns whether that compatibility script stays registered.

`clawd_cli/main.py` refuses `update`, `desktop`, `dashboard`, `gui`, and `webapp`. Those commands would restore the official UI or overwrite an existing Hermes Agent install. Official Desktop and the official web dashboard are not clawd products. This documentation pass did not execute the commands.

On startup the launcher calls `isolate_inherited_hermes_env()`. `CLAWD_HOME` is the only default. An inherited `HERMES_HOME` outside the clawd tree is remapped onto that root. It does not fall back to production `~/.hermes`. Internal code may still read the name `HERMES_HOME` after that mapping. That name is a compatibility id, not the product path. This README does not migrate existing data or secrets.

Windows upstream installers document `%LOCALAPPDATA%\hermes` and `~/.hermes`. Those are Hermes Agent paths. This checkout's resolved default is `~/.clawd` via `Path.home()`, not those paths. A Windows-specific branch was not executed here.

## Install

Do not install clawd with the upstream one-liner. That script installs Hermes Agent:

```bash
# Upstream Hermes Agent installer. Not this clawd checkout.
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
```

The PowerShell script at `https://hermes-agent.nousresearch.com/install.ps1` is the same upstream installer. This checkout does not publish a replacement installer URL.

From this checkout:

```bash
./clawd
```

If a later packaging install registers console scripts, the product command is `clawd`. This file does not claim that a wheel has been built.

## Use

These spellings dispatch to the shared CLI, except the refusals above. They were not run while writing this file.

```bash
clawd                 # terminal UI
clawd model           # choose provider and model
clawd tools           # configure tools
clawd config set      # set one config value
clawd config get      # print one config value
clawd gateway         # messaging gateway
clawd setup           # setup wizard
clawd doctor          # diagnostics
clawd panel           # bound web UI
```

`clawd update` is refused. Upstream Hermes Agent documents `hermes update`; that is not the clawd updater.

`clawd setup --portal` is the product spelling of the shared setup command. Nous Portal (`https://portal.nousresearch.com`) is a Nous Research service, not a clawd site. Tool Gateway details remain on the upstream page: <https://hermes-agent.nousresearch.com/docs/user-guide/features/tool-gateway>.

## Inherited behavior

The capabilities below come from Hermes Agent. They were not re-verified by a build or run in this pass. Desktop-only settings are not a clawd surface.

| Area | Inherited behavior |
| --- | --- |
| Terminal | TUI with multiline edit, slash completion, history, interrupt, streaming tool output |
| Messaging | Gateway process for Telegram, Discord, Slack, WhatsApp, Signal, and others |
| Learning loop | Skills, memory nudges, session search. [Honcho](https://github.com/plastic-labs/honcho) and [agentskills.io](https://agentskills.io) are third-party names |
| Schedule | Built-in cron with platform delivery |
| Delegation | Isolated subagents; Python tool RPC |
| Terminal backends | Upstream documents local, Docker, SSH, Modal, Daytona, Vercel Sandbox, plus plugins such as Singularity |

## CLI and messaging

Start the terminal UI with `clawd`, or run the gateway and message it from a connected platform. Slash commands are shared. Full lists in the upstream guides are written for Hermes Agent; substitute `clawd` for the product command, and do not substitute it into refused commands.

| Action | CLI | Messaging |
| --- | --- | --- |
| Start | `clawd` | `clawd gateway setup`, then `clawd gateway start` |
| New conversation | `/new` or `/reset` | `/new` or `/reset` |
| Model | `/model [provider:model]` | `/model [provider:model]` |
| Personality | `/personality [name]` | `/personality [name]` |
| Retry / undo | `/retry`, `/undo` | `/retry`, `/undo` |
| Context / usage | `/compress`, `/usage` | `/compress`, `/usage` |
| Skills | `/skills` | `/skills` |
| Interrupt | `Ctrl+C` | `/stop` |

Upstream command reference: <https://hermes-agent.nousresearch.com/docs/reference/cli-commands>. Upstream environment-variable reference: <https://hermes-agent.nousresearch.com/docs/reference/environment-variables>. Those pages still name Hermes Agent and `HERMES_*` variables. In this checkout the product data root is `CLAWD_HOME`.

## Migration examples

These are spellings of the shared CLI, not a migration that was run:

```bash
clawd plugins install agent-importers
clawd import-agent openclaw
clawd import-agent openclaw --dry-run
clawd claw migrate
```

Upstream docs describe skill imports landing under `~/.hermes/skills/openclaw-imports/`. Under clawd, the process data root after isolation is `CLAWD_HOME` or `~/.clawd`. This README does not claim a specific importer subdirectory was re-checked, and it does not point clawd at production Hermes data. OpenClaw, Claude Code, Codex, and Cursor are other products' names.

## Documentation

Guides at <https://hermes-agent.nousresearch.com/docs/> are upstream Hermes Agent documentation. They are not a clawd docs site. Contributor process for the upstream repository is still described in [CONTRIBUTING.md](CONTRIBUTING.md). No clawd GitHub repository is declared here.

## Community

- [Nous Research Discord](https://discord.gg/NousResearch) — their server, not a clawd server
- [agentskills.io](https://agentskills.io)
- [Upstream issues](https://github.com/NousResearch/hermes-agent/issues) — Hermes Agent tracker, not a clawd tracker
- [computer-use-linux](https://github.com/avifenesh/computer-use-linux) — third-party MCP server
- [HermesClaw](https://github.com/AaronWong1999/hermesclaw) — third-party WeChat bridge. The name is theirs, not this product

## License

MIT — see [LICENSE](LICENSE).

Copyright (c) 2025 Nous Research. Upstream Hermes Agent is built by Nous Research. This checkout's display name is clawd.
