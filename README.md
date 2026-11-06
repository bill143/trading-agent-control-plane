# Multi-Expert AI Trading System

Private build for Bill. A team of specialist trading AIs, a coordinator that trades only when
proven experts agree, and a fixed risk layer no AI can change, built on
[NautilusTrader](https://github.com/nautechsystems/nautilus_trader).

| What | Where |
|---|---|
| Build plan (open in a browser: summary, features, checklist, AI design, open items, sequence) | [`docs/BUILD_PLAN.html`](docs/BUILD_PLAN.html) |
| Builder work order | [`docs/BUILD_SEQUENCE.md`](docs/BUILD_SEQUENCE.md) |
| Live build progress | [`BUILD_STATUS.md`](BUILD_STATUS.md) |
| Decisions and departures | [`DECISIONS.md`](DECISIONS.md) |
| Secrets policy | [`docs/SECURITY.md`](docs/SECURITY.md) |

## Developer setup (Windows)
```powershell
git clone https://github.com/bill143/trading-agent-control-plane
cd trading-agent-control-plane
powershell -ExecutionPolicy Bypass -File scripts\setup-windows.ps1
```
Pins: Python 3.13.14, Rust 1.97.1, every package locked in `uv.lock`.

## Everyday commands
```
uv run pytest                               # tests
uv run ruff check . ; uv run mypy           # lint, types
uv run python -m tools.build_status next    # next build step
```
