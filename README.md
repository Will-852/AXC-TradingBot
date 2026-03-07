# OpenClaw

Local AI-powered crypto trading monitor. Runs entirely on your machine — your keys never leave your computer.

## What It Does

- **Dashboard** — Real-time market data, indicators, and agent status in your browser
- **9 AI Agents** — Analyze markets, generate signals, manage risk (Claude API)
- **Telegram Bot** — Mobile notifications and remote control (optional)
- **Auto Trading** — Aster DEX / Binance execution (optional, macOS only)

## Quick Start

### macOS

1. Download the [latest release](https://github.com/Will-852/openclaw/releases/latest) or clone:
   ```
   git clone https://github.com/Will-852/openclaw ~/.openclaw
   ```
2. Double-click **`start.command`** to launch

That's it. The launcher installs dependencies automatically and opens the Dashboard.

### Windows

1. Download the [latest release](https://github.com/Will-852/openclaw/releases/latest) and extract to `%USERPROFILE%\.openclaw\`
2. Double-click **`start.bat`** to launch

Requires [Python 3.11+](https://www.python.org/downloads/) — check **"Add python.exe to PATH"** during install.

## First Run Setup

On first launch, the app will ask you to set up your API key:

1. Get a `PROXY_API_KEY` from [console.anthropic.com](https://console.anthropic.com) or your proxy provider
2. The launcher creates `secrets/.env` for you — just paste your key when prompted

| Key | Purpose | Required |
|-----|---------|----------|
| `PROXY_API_KEY` | AI analysis | Yes |
| `PROXY_BASE_URL` | AI endpoint (default: `https://tao.plus7.plus/v1`) | Yes |
| `ASTER_API_KEY/SECRET` | Exchange trading | For trading |
| `TELEGRAM_BOT_TOKEN` | Mobile notifications | Optional |
| `BINANCE_API_KEY/SECRET` | Binance market data | Optional |
| `VOYAGE_API_KEY` | AI memory search | Optional |

## Platform Support

| Feature | macOS | Windows |
|---------|-------|---------|
| Dashboard | Yes | Yes |
| AI Analysis | Yes | Yes |
| Telegram Bot | Yes | Yes |
| Auto Scanner | Yes | No |
| Auto Trading | Yes | No |
| LaunchAgent scheduling | Yes | No |

## Requirements

- Python 3.11+
- ~100 MB disk space
- Internet connection (API calls)

## Project Structure

```
~/.openclaw/
├── start.command       # macOS launcher (double-click)
├── start.bat           # Windows launcher (double-click)
├── scripts/            # Core scripts (dashboard, scanner, agents)
├── config/             # Settings (params.py, modes/)
├── agents/             # 9 AI agents with personality files
├── canvas/             # Dashboard web UI
├── secrets/.env        # Your API keys (never committed)
└── docs/               # Documentation
```

## Security

- All processing runs locally
- API keys stored only in `secrets/.env` (gitignored)
- No telemetry, no data collection
- Trading requires explicit opt-in with exchange API keys

## License

Private. Do not redistribute without permission.
