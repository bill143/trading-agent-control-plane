# Install Trading System on your Windows PC (no GitHub sign-in needed)

Open **Windows PowerShell** (Start menu → type "PowerShell" → Windows PowerShell), paste this
whole block, press Enter, and wait (first run: about 10–15 minutes):

```powershell
$ErrorActionPreference = 'Stop'; $ProgressPreference = 'SilentlyContinue'
$src = Join-Path $env:USERPROFILE 'TradingSystem-src'
$zip = Join-Path $env:TEMP 'TradingSystem-src.zip'
Invoke-WebRequest -UseBasicParsing -OutFile $zip -Uri 'https://github.com/bill143/trading-agent-control-plane/archive/refs/heads/claude/phase-0-setup.zip'
if (Test-Path $src) { Remove-Item $src -Recurse -Force }
Expand-Archive -Path $zip -DestinationPath $src
$root = (Get-ChildItem $src -Directory | Select-Object -First 1).FullName
powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'scripts\install-windows.ps1')
```

What it does: downloads the current source code from the public repository as a zip file (no
Git or GitHub account needed), then runs `scripts\install-windows.ps1`, which installs the
pinned Python and packages for your user only, builds the app, installs it to
`%LOCALAPPDATA%\Programs\TradingSystem`, puts a **Trading System** icon on your desktop and in
the Start menu, and opens it. No administrator rights are needed. Running the same block again
later updates the app and keeps your users, settings, vault and history.

## First start

1. Create your Owner account and scan the QR code with an authenticator app.
2. Sign in with your password and the 6-digit code.
3. **Settings → Connections**: choose each connection, paste the value into the field and press
   **Save to vault**:
   - Databento API key
   - Interactive Brokers paper username
   - Interactive Brokers paper password

   Values go straight into the encrypted vault on your PC (protected by Windows). They are never
   shown again, never written to logs and never leave your computer except to the service they
   belong to.
4. **Settings → Connections → Get price quote**: Databento's free quote for the historical-data
   plan. Nothing is downloaded or charged.

## If Windows shows a warning

The app is not code-signed yet (deferred to Phase 9). If "Windows protected your PC" appears,
choose **More info → Run anyway**.
