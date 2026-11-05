<div dir="rtl">

<p align="center">
  <img src="assets/banner.png" alt="clawd" width="100%">
</p>

# clawd

اس چیک آؤٹ کا پروڈکٹ نام **clawd** ہے۔ یہ clawed نہیں، اور upstream پروڈکٹ Hermes Agent بھی نہیں۔

clawd، [Nous Research](https://nousresearch.com) کے [Hermes Agent](https://github.com/NousResearch/hermes-agent) سے مشتق ہے۔ [`docs/clawd/source-map.md`](docs/clawd/source-map.md) میں درج ماخذ پن `c314285f077e91937c995d419b9fa40af9eb4981` ہے۔ یہ فائل کاپی رائٹ یا لائسنس منتقل نہیں کرتی۔ `LICENSE` اب بھی MIT ہے، `Copyright (c) 2025 Nous Research`۔

`assets/banner.png` اب بھی upstream تصویر ہے۔ اسے دوبارہ نہیں بنایا گیا۔ اس چیک آؤٹ میں clawd کی ویب سائٹ، انسٹالر ہوسٹ، یا مسئلہ ٹریکر کا اعلان نہیں۔ نیچے `hermes-agent.nousresearch.com` یا `github.com/NousResearch/hermes-agent` والے لنک upstream Hermes Agent کے حقائق ہیں۔

<p align="center">
  <a href="https://hermes-agent.nousresearch.com/docs/"><img src="https://img.shields.io/badge/Upstream%20docs-hermes--agent.nousresearch.com-FFD700?style=for-the-badge" alt="Upstream Hermes Agent docs"></a>
  <a href="https://discord.gg/NousResearch"><img src="https://img.shields.io/badge/Discord-Nous%20Research-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Nous Research Discord"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License: MIT"></a>
  <a href="README.md"><img src="https://img.shields.io/badge/Lang-English-lightgrey?style=for-the-badge" alt="English"></a>
  <a href="README.zh-CN.md"><img src="https://img.shields.io/badge/Lang-中文-red?style=for-the-badge" alt="中文"></a>
  <a href="README.es.md"><img src="https://img.shields.io/badge/Lang-Español-orange?style=for-the-badge" alt="Español"></a>
</p>

## چلانے کا راستہ

| حقیقت | قدر |
| --- | --- |
| پروڈکٹ کمانڈ | `clawd` |
| چیک آؤٹ لانچر | `./clawd`（سسٹم کی `hermes` کمانڈ کو تبدیل نہیں کرتا） |
| کنسول اسکرپٹ | `pyproject.toml` میں `clawd = "clawd_cli.main:main"` |
| اس چیک آؤٹ کا ویب انٹرفیس | `clawd panel` |
| ڈیٹا روٹ | `CLAWD_HOME`؛ نہ ہونے پر `~/.clawd` |

مشترکہ CLI پر `hermes` نام کا compatibility script اب بھی رجسٹرڈ ہے۔ اس فائل کی مثالیں `clawd` استعمال کرتی ہیں۔ وہ اسکرپٹ رہے یا نہیں، یہ پیکجنگ کا فیصلہ ہے۔

`clawd_cli/main.py` ان ذیلی کمانڈز کو مسترد کرتا ہے: `update`، `desktop`، `dashboard`، `gui`، `webapp`۔ یہ راستہ سرکاری UI واپس لا سکتا ہے یا موجودہ Hermes Agent انسٹال کو اوور رائٹ کر سکتا ہے۔ سرکاری Desktop اور سرکاری ویب ڈیش بورڈ clawd کے پروڈکٹ نہیں۔ اس دستاویز کو لکھتے ہوئے کمانڈز نہیں چلائیں۔

لانچر شروع میں `isolate_inherited_hermes_env()` بلا ہے گا۔ ڈیفالٹ صرف `CLAWD_HOME` ہے۔ clawd درخت سے باہر وراثت میں ملا `HERMES_HOME` اسی روٹ پر map ہو جاتا ہے۔ یہ پروڈکشن `~/.hermes` پر نہیں گرتا۔ map کے بعد اندرونی کوڈ `HERMES_HOME` نام پڑھ سکتا ہے۔ یہ مطابقتی شناخت ہے، پروڈکٹ پاتھ نہیں۔ یہ فائل پرانا ڈیٹا یا سیکرٹ منتقل نہیں کرتی۔

upstream ونڈوز انسٹالر `%LOCALAPPDATA%\hermes` اور `~/.hermes` لکھتے ہیں۔ یہ Hermes Agent کے پاتھ ہیں۔ اس چیک آؤٹ کا کوڈ ڈیفالٹ `Path.home() / .clawd` ہے۔ یہاں ونڈوز برانچ نہیں چلائی گئی۔

## انسٹال

clawd کو upstream کی ایک لائن سے انسٹال نہ کریں۔ وہ اسکرپٹ Hermes Agent انسٹال کرتا ہے:

<div dir="ltr">

```bash
# Upstream Hermes Agent installer. Not this clawd checkout.
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
```

</div>

`https://hermes-agent.nousresearch.com/install.ps1` بھی وہی upstream انسٹالر ہے۔ اس چیک آؤٹ کی اپنی clawd انسٹال URL نہیں۔

اس چیک آؤٹ سے:

<div dir="ltr">

```bash
./clawd
```

</div>

اگر بعد کی پیکجنگ کنسول اسکرپٹ رجسٹر کرے، پروڈکٹ کمانڈ `clawd` ہے۔ یہ فائل wheel بننے کا دعویٰ نہیں کرتی۔

## استعمال

اوپر مسترد ذیلی کمانڈز کے علاوہ یہ شکلیں مشترکہ CLI کو جاتی ہیں۔ لکھتے وقت نہیں چلائیں۔

<div dir="ltr">

```bash
clawd                 # terminal UI
clawd model           # provider and model
clawd tools           # tools
clawd config set      # one config value
clawd config get      # print one config value
clawd gateway         # messaging gateway
clawd setup           # setup wizard
clawd doctor          # diagnostics
clawd panel           # bound web UI
```

</div>

`clawd update` مسترد ہے۔ upstream Hermes Agent کی دستاویز میں `hermes update` clawd کا اپڈیٹر نہیں۔

`clawd setup --portal` مشترکہ سیٹ اپ کمانڈ کی پروڈکٹ شکل ہے۔ Nous Portal (`https://portal.nousresearch.com`) Nous Research کی سروس ہے، clawd سائٹ نہیں۔ Tool Gateway کی تفصیل upstream صفحے پر ہے: <https://hermes-agent.nousresearch.com/docs/user-guide/features/tool-gateway>۔

## وراثت میں ملا رویہ

نیچے کا جدول upstream Hermes Agent سے ہے۔ اس پاس میں بلڈ یا رن سے دوبارہ تصدیق نہیں ہوئی۔ صرف Desktop والی سیٹنگز clawd کی سطح نہیں۔

| پہلو | وراثتی رویہ |
| --- | --- |
| ٹرمینل | TUI: کثیر سطری تدوین، سلیش تکمیل، تاریخ، رکاوٹ، سٹریمنگ ٹول آؤٹ پٹ |
| پیغامات | Telegram، Discord، Slack، WhatsApp، Signal وغیرہ کے لیے گیٹ وے |
| سیکھنے کا حلقہ | skills، memory، session search۔ [Honcho](https://github.com/plastic-labs/honcho) اور [agentskills.io](https://agentskills.io) تیسرے فریق کے نام ہیں |
| شیڈول | بلٹ ان cron |
| تفویض | الگ subagents؛ Python tool RPC |
| ٹرمینل بیک اینڈ | upstream دستاویز: local، Docker، SSH، Modal، Daytona، Vercel Sandbox، اور Singularity جیسے پلگ ان |

## CLI اور پیغام رسانی

ٹرمینل UI `clawd` سے شروع کریں، یا گیٹ وے چلا کر جڑی ہوئی پلیٹ فارم سے پیغام بھیجیں۔ سلیش کمانڈز مشترک ہیں۔ upstream گائیڈز Hermes Agent کے لیے لکھی گئی ہیں؛ پروڈکٹ کمانڈ `clawd` رکھیں، مسترد ذیلی کمانڈز نہ بدلیں۔

<div dir="ltr">

| Action | CLI | Messaging |
| --- | --- | --- |
| Start | `clawd` | `clawd gateway setup`, then `clawd gateway start` |
| New | `/new` or `/reset` | `/new` or `/reset` |
| Model | `/model [provider:model]` | `/model [provider:model]` |
| Personality | `/personality [name]` | `/personality [name]` |
| Retry / undo | `/retry`, `/undo` | `/retry`, `/undo` |
| Context | `/compress`, `/usage` | `/compress`, `/usage` |
| Skills | `/skills` | `/skills` |
| Interrupt | `Ctrl+C` | `/stop` |

</div>

upstream کمانڈ حوالہ: <https://hermes-agent.nousresearch.com/docs/reference/cli-commands>۔ upstream ماحولیاتی متغیر حوالہ: <https://hermes-agent.nousresearch.com/docs/reference/environment-variables>۔ وہ صفحات اب بھی Hermes Agent اور `HERMES_*` لکھتے ہیں۔ اس چیک آؤٹ کا پروڈکٹ ڈیٹا روٹ `CLAWD_HOME` ہے۔

## منتقلی کی مثالیں

یہ مشترکہ CLI کی شکلیں ہیں، چلائی گئی منتقلی نہیں:

<div dir="ltr">

```bash
clawd plugins install agent-importers
clawd import-agent openclaw
clawd import-agent openclaw --dry-run
clawd claw migrate
```

</div>

upstream دستاویز skills کی درآمد `~/.hermes/skills/openclaw-imports/` میں لکھتی ہے۔ clawd میں تنہائی کے بعد پروسیس ڈیٹا روٹ `CLAWD_HOME` یا `~/.clawd` ہے۔ یہ فائل درآمد کار کی کسی مخصوص ذیلی ڈائریکٹری کی دوبارہ جانچ کا دعویٰ نہیں کرتی، اور clawd کو پروڈکشن Hermes ڈیٹا کی طرف نہیں لے جاتی۔ OpenClaw، Claude Code، Codex اور Cursor دوسرے پروڈکٹس کے نام ہیں۔

## دستاویزات

<https://hermes-agent.nousresearch.com/docs/> upstream Hermes Agent کی دستاویزات ہیں، clawd کا docs سائٹ نہیں۔ upstream مخزن کا تعاون [CONTRIBUTING.md](CONTRIBUTING.md) میں ہے۔ یہاں clawd کا GitHub مخزن اعلان نہیں کیا گیا۔

## کمیونٹی

- [Nous Research Discord](https://discord.gg/NousResearch) — ان کا سرور، clawd کا سرور نہیں
- [agentskills.io](https://agentskills.io)
- [upstream مسائل](https://github.com/NousResearch/hermes-agent/issues) — Hermes Agent کا ٹریکر، clawd کا نہیں
- [computer-use-linux](https://github.com/avifenesh/computer-use-linux) — تیسرے فریق کا MCP سرور
- [HermesClaw](https://github.com/AaronWong1999/hermesclaw) — تیسرے فریق کا WeChat پل۔ یہ نام ان کے پروجیکٹ کا ہے، اس پروڈکٹ کا نہیں

## لائسنس

MIT — [LICENSE](LICENSE) دیکھیں۔

Copyright (c) 2025 Nous Research۔ upstream Hermes Agent، Nous Research نے بنایا۔ اس چیک آؤٹ کا ظاہری نام clawd ہے۔

</div>
