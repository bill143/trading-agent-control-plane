# Dashboard walkthrough (step 1.24)

Drives the real app end to end in Chromium, pinned to the engine's own certificate exactly as the
Windows window (Edge WebView2) is: first-run Owner setup, two-factor enrollment, sign-in, start
the demo, live symbol tiles, risk decisions, manual kill switch and reset, an automatic kill
switch from a simulated data-feed failure, a backtest report, risk settings and the audit log.

```
TACP_HOME=/tmp/tacp uv run python -m app.main --server-only --port 8765 &
PIN=$(uv run python -c "from api.tls import spki_pin; from pathlib import Path; print(spki_pin(Path('/tmp/tacp/state/tls/api-cert.pem').read_bytes()))")
cd tests/e2e && npm install && npx playwright install chromium
node walkthrough.js https://127.0.0.1:8765/ "$PIN" ./out video
```
Screenshots and a `.webm` video land in `./out`. CI runs this on every push (job "UI walkthrough").
