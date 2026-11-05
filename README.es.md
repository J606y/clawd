<p align="center">
  <img src="assets/banner.png" alt="clawd" width="100%">
</p>

# clawd

El nombre del producto en este checkout es **clawd**. No se escribe clawed, y no es el producto upstream Hermes Agent.

clawd deriva de [Hermes Agent](https://github.com/NousResearch/hermes-agent), de [Nous Research](https://nousresearch.com). El pin de origen registrado en [`docs/clawd/source-map.md`](docs/clawd/source-map.md) es `c314285f077e91937c995d419b9fa40af9eb4981`. Este archivo no reasigna copyright ni licencia. `LICENSE` sigue siendo MIT, `Copyright (c) 2025 Nous Research`.

`assets/banner.png` sigue siendo la imagen upstream. No se regeneró. Este checkout no declara un sitio, un host de instalación ni un gestor de incidencias de clawd. Los enlaces a `hermes-agent.nousresearch.com` o `github.com/NousResearch/hermes-agent` son hechos del Hermes Agent upstream.

<p align="center">
  <a href="https://hermes-agent.nousresearch.com/docs/"><img src="https://img.shields.io/badge/Upstream%20docs-hermes--agent.nousresearch.com-FFD700?style=for-the-badge" alt="Documentación upstream de Hermes Agent"></a>
  <a href="https://discord.gg/NousResearch"><img src="https://img.shields.io/badge/Discord-Nous%20Research-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Discord de Nous Research"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/Licencia-MIT-green?style=for-the-badge" alt="Licencia: MIT"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/Lang-English-blue?style=for-the-badge" alt="English"></a>
  <a href="README.zh-CN.md"><img src="https://img.shields.io/badge/Lang-中文-red?style=for-the-badge" alt="中文"></a>
  <a href="README.ur-pk.md"><img src="https://img.shields.io/badge/Lang-اردو-green?style=for-the-badge" alt="اردو"></a>
</p>

## Qué se ejecuta

| Hecho | Valor |
| --- | --- |
| Comando del producto | `clawd` |
| Lanzador del checkout | `./clawd` (no sustituye un comando `hermes` del sistema) |
| Script de consola | `clawd = "clawd_cli.main:main"` en `pyproject.toml` |
| Interfaz web de este checkout | `clawd panel` |
| Raíz de datos | `CLAWD_HOME`, o `~/.clawd` si no está definida |

El CLI compartido sigue registrando un script de compatibilidad llamado `hermes`. Los ejemplos de este archivo usan `clawd`. El empaquetado decide si ese script de compatibilidad permanece.

`clawd_cli/main.py` rechaza `update`, `desktop`, `dashboard`, `gui` y `webapp`. Esas rutas restaurarían la UI oficial o sobrescribirían una instalación existente de Hermes Agent. El Desktop oficial y el panel web oficial no son productos de clawd. Esta pasada de documentación no ejecutó los comandos.

Al arrancar, el lanzador llama a `isolate_inherited_hermes_env()`. El único valor predeterminado es `CLAWD_HOME`. Un `HERMES_HOME` heredado fuera del árbol de clawd se reescribe a esa raíz. No cae en el `~/.hermes` de producción. El código interno puede seguir leyendo el nombre `HERMES_HOME` después de ese mapeo. Es un identificador de compatibilidad, no la ruta del producto. Este archivo no migra datos ni secretos existentes.

Los instaladores upstream de Windows documentan `%LOCALAPPDATA%\hermes` y `~/.hermes`. Son rutas de Hermes Agent. El valor predeterminado de este checkout es `Path.home() / .clawd`. No se ejecutó una rama de Windows.

## Instalación

No instales clawd con el one-liner upstream. Ese script instala Hermes Agent:

```bash
# Instalador upstream de Hermes Agent. No es este checkout de clawd.
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
```

`https://hermes-agent.nousresearch.com/install.ps1` es el mismo instalador upstream. Este checkout no publica otra URL de instalación de clawd.

Desde este checkout:

```bash
./clawd
```

Si una instalación de empaquetado posterior registra scripts de consola, el comando del producto es `clawd`. Este archivo no afirma que se haya construido un wheel.

## Uso

Estas formas se despachan al CLI compartido, salvo los rechazos de arriba. No se ejecutaron al escribir este archivo.

```bash
clawd                 # interfaz de terminal
clawd model           # proveedor y modelo
clawd tools           # herramientas
clawd config set      # un valor de configuración
clawd config get      # imprimir un valor
clawd gateway         # gateway de mensajería
clawd setup           # asistente de configuración
clawd doctor          # diagnóstico
clawd panel           # interfaz web vinculada
```

`clawd update` está rechazado. Hermes Agent upstream documenta `hermes update`; eso no es el actualizador de clawd.

`clawd setup --portal` es la forma del producto para el comando de configuración compartido. Nous Portal (`https://portal.nousresearch.com`) es un servicio de Nous Research, no un sitio de clawd. El detalle del Tool Gateway sigue en la página upstream: <https://hermes-agent.nousresearch.com/docs/user-guide/features/tool-gateway>.

## Comportamiento heredado

La tabla viene de Hermes Agent. No se revalidó con una build ni una ejecución en esta pasada. Los ajustes exclusivos de Desktop no son una superficie de clawd.

| Área | Comportamiento heredado |
| --- | --- |
| Terminal | TUI con edición multilínea, autocompletado, historial, interrupción y salida de herramientas en streaming |
| Mensajería | Proceso gateway para Telegram, Discord, Slack, WhatsApp, Signal y otros |
| Bucle de aprendizaje | Skills, memoria y búsqueda de sesiones. [Honcho](https://github.com/plastic-labs/honcho) y [agentskills.io](https://agentskills.io) son nombres de terceros |
| Agenda | cron integrado con entrega a plataformas |
| Delegación | Subagentes aislados; RPC de herramientas en Python |
| Backends de terminal | El upstream documenta local, Docker, SSH, Modal, Daytona, Vercel Sandbox y plugins como Singularity |

## CLI y mensajería

Arranca la TUI con `clawd`, o arranca el gateway y escribe desde una plataforma conectada. Los comandos de barra se comparten. Las guías upstream están escritas para Hermes Agent; sustituye el comando del producto por `clawd`, y no sustituyas los comandos rechazados.

| Acción | CLI | Mensajería |
| --- | --- | --- |
| Empezar | `clawd` | `clawd gateway setup`, luego `clawd gateway start` |
| Conversación nueva | `/new` o `/reset` | `/new` o `/reset` |
| Modelo | `/model [proveedor:modelo]` | `/model [proveedor:modelo]` |
| Personalidad | `/personality [nombre]` | `/personality [nombre]` |
| Reintentar / deshacer | `/retry`, `/undo` | `/retry`, `/undo` |
| Contexto / uso | `/compress`, `/usage` | `/compress`, `/usage` |
| Skills | `/skills` | `/skills` |
| Interrumpir | `Ctrl+C` | `/stop` |

Referencia upstream de comandos: <https://hermes-agent.nousresearch.com/docs/reference/cli-commands>. Referencia upstream de variables: <https://hermes-agent.nousresearch.com/docs/reference/environment-variables>. Esas páginas siguen nombrando Hermes Agent y variables `HERMES_*`. En este checkout la raíz de datos del producto es `CLAWD_HOME`.

## Ejemplos de migración

Son formas del CLI compartido, no una migración ejecutada:

```bash
clawd plugins install agent-importers
clawd import-agent openclaw
clawd import-agent openclaw --dry-run
clawd claw migrate
```

La documentación upstream describe importaciones de skills en `~/.hermes/skills/openclaw-imports/`. Bajo clawd, la raíz de datos del proceso tras el aislamiento es `CLAWD_HOME` o `~/.clawd`. Este archivo no afirma haber revisado un subdirectorio concreto del importador, ni apunta clawd a datos de producción de Hermes. OpenClaw, Claude Code, Codex y Cursor son nombres de otros productos.

## Documentación

Las guías en <https://hermes-agent.nousresearch.com/docs/> son documentación upstream de Hermes Agent, no un sitio de docs de clawd. El proceso de contribución del repositorio upstream sigue descrito en [CONTRIBUTING.es.md](CONTRIBUTING.es.md). Aquí no se declara un repositorio GitHub de clawd.

## Comunidad

- [Discord de Nous Research](https://discord.gg/NousResearch) — su servidor, no un servidor de clawd
- [agentskills.io](https://agentskills.io)
- [Incidencias upstream](https://github.com/NousResearch/hermes-agent/issues) — gestor de Hermes Agent, no de clawd
- [computer-use-linux](https://github.com/avifenesh/computer-use-linux) — servidor MCP de terceros
- [HermesClaw](https://github.com/AaronWong1999/hermesclaw) — puente WeChat de terceros. El nombre es de ese proyecto, no de este producto

## Licencia

MIT — ver [LICENSE](LICENSE).

Copyright (c) 2025 Nous Research. Hermes Agent upstream está creado por Nous Research. El nombre visible de este checkout es clawd.
