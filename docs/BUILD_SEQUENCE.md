# BUILD_SEQUENCE — Multi-Expert AI Trading System

Source of truth: docs/BUILD_PLAN.html (Tabs 1–6). This file is the builder's work order.

## Status protocol
- Record progress in TWO places after every step:
  1. The JSON inside `<script id="build-status">` in docs/BUILD_PLAN.html (edit only that block; keep it valid JSON).
  2. BUILD_STATUS.md (same IDs, human-readable).
- `steps["1.04"] = {"status":"done|in_progress|blocked","date":"YYYY-MM-DD HH:MM CT","proof":"test file + result, screenshot or report path","note":""}`
- `checks["C1.04"] = {"status":"built","date":"...","proof":"..."}` — set only after the Tab 3 item passes the phase cross-check. Never set Bill's "verified" tick.
- `gates["1"] = {"status":"awaiting_bill|passed","date":"..."}` — only Bill's green light sets "passed".
- Always update `updated` and `current_step`.

## Phase 0 — Project setup & governance

### Build steps
- [ ] **0.01** Create a private GitHub repository owned by Bill; protect the main branch (no direct pushes, CI must pass) _(Security)_
- [ ] **0.02** Copy BUILD_PLAN.html and BUILD_SEQUENCE.md into /docs; create BUILD_STATUS.md listing every step ID with status _(Tracking)_
- [ ] **0.03** Create DECISIONS.md recording symbols, broker route, hosting and the Tab 5 decisions _(Tracking)_
- [ ] **0.04** Write the locked holdout date range into DECISIONS.md before any historical data is downloaded _(Testing)_
- [ ] **0.05** Set up the Windows dev environment with pinned Python and Rust versions and a lock file _(Build)_
- [ ] **0.06** Secrets policy: .env files git-ignored, pre-commit secret scanner (gitleaks), no key ever in code or logs _(Security)_
- [ ] **0.07** CI pipeline: lint, type checks, unit tests and dependency vulnerability scan on every push _(Security)_

### Tab 3 checklist items to cross-check at the gate
- [ ] **C0.01** Build plan (this file) reviewed and green-lit
- [ ] **C0.02** Contract states Bill owns all code, models, data and documentation
- [ ] **C0.03** Milestone payments tied to checklist items shown working
- [ ] **C0.04** Databento account opened in Bill's name
- [ ] **C0.05** Interactive Brokers paper account opened in Bill's name
- [ ] **C0.06** Written automation approval from each prop firm — PROOF required emails on file
- [ ] **C0.07** Monthly operating cost estimate agreed
- [ ] **C0.08** Target metrics agreed: profit factor above 1.3, max drawdown inside prop-firm limit
- [ ] **C0.09** Holdout period dates written down before any training — PROOF required

> **GATE:** Bill confirms repo, decisions log and dev environment.

## Phase 1 — Foundation app (NautilusTrader)

### Build steps
- [ ] **1.01** Install NautilusTrader (pinned version) and run a sample backtest _(Build)_
- [ ] **1.02** Create project layout: engine/, risk/, strategies/, api/, app/, tests/, docs/ _(Build)_
- [ ] **1.03** Bundle a sample data pack so the app runs in demo mode with no accounts _(Build)_
- [ ] **1.04** Define instruments MES, MCL, MGC, M6E, 10Y (active) and MNQ, MYM, MBT (configured, off) _(Engine)_
- [ ] **1.05** Session calendar: CME hours, holidays, shortened sessions, daylight-saving switches, contract rollover dates _(Engine)_
- [ ] **1.06** Run one symbol engine per contract inside a single node _(Engine)_
- [ ] **1.07** Live state tracker per symbol: price, spread, book lean, ATR, VWAP, prior-day/overnight levels, position, P&L, contracts used _(Engine)_
- [ ] **1.08** Daily fee filter: symbol qualifies only if target ticks × tick value ≥ 2× round-trip fees _(Risk)_
- [ ] **1.09** Portfolio risk manager: buying power with 30–50% buffer, margin, daily loss, per-trade loss, per-symbol and total quotas, correlation groups, max 3 open positions, news blackout _(Risk)_
- [ ] **1.10** Lock risk settings: read-only to every AI process; only the Owner role can change them, each change written to the audit log _(Security)_
- [ ] **1.11** Kill switch: manual button plus automatic triggers on breach, data-feed failure and broker disconnect _(Risk)_
- [ ] **1.12** Order safety: bracket orders, duplicate-order protection, reconcile open positions with broker on every restart _(Risk)_
- [ ] **1.13** Starter strategy running on every symbol end to end _(Engine)_
- [ ] **1.14** Backtest runner with results report _(Testing)_
- [ ] **1.15** Control API between engine and apps: encrypted connection, authenticated, read endpoints plus start/stop/mode/kill commands _(Security)_
- [ ] **1.16** Users and roles: Owner (full control), Approver (approve trades and kill switch only), Viewer (read-only) _(Users)_
- [ ] **1.17** Login with password plus two-factor code, session timeout, lockout after failed attempts _(Users)_
- [ ] **1.18** Encrypted secrets vault for API keys and broker logins (Windows protected storage locally, cloud secret manager on server) _(Security)_
- [ ] **1.19** Append-only audit log: every login, command, setting change and order _(Security)_
- [ ] **1.20** Windows desktop app: dashboard, symbol tiles, positions, P&L, start/stop, kill switch, backtest viewer, settings, demo-mode banner _(App)_
- [ ] **1.21** Signed Windows installer with desktop icon; auto-update off unless Bill turns it on _(App)_
- [ ] **1.22** Automated tests for risk manager, order handling, kill switch and restart reconciliation _(Testing)_
- [ ] **1.23** Connect Databento and Interactive Brokers paper once Bill supplies keys _(Integrations)_
- [ ] **1.24** Builder installs and clicks through the app, records a walkthrough video, updates BUILD_STATUS.md _(Tracking)_

### Tab 3 checklist items to cross-check at the gate
- [ ] **C1.01** NautilusTrader installed in Bill's repository with a clean project layout
- [ ] **C1.02** Windows desktop app installs with an icon and opens with a double-click
- [ ] **C1.03** Demo mode runs on sample data with no keys connected
- [ ] **C1.04** Symbol engines running together in one system
- [ ] **C1.05** Live status per symbol: price, spread, ATR, VWAP, prior-day and overnight levels, position, P&L, contracts used
- [ ] **C1.06** Daily fee filter (target ticks × tick value ≥ 2× round-trip fees) calculated per symbol
- [ ] **C1.07** Portfolio risk manager: buying power with buffer, margin, daily loss, per-trade loss, quotas
- [ ] **C1.08** Cap on positions moving in the same direction; max 3 open portfolio-wide
- [ ] **C1.09** No new entries within X minutes of scheduled news
- [ ] **C1.10** Starter strategy runs on every symbol end to end
- [ ] **C1.11** Backtests over historical data with a results report
- [ ] **C1.12** Manual kill switch flattens everything — PROOF required demo on paper
- [ ] **C1.13** Automatic kill on breach, data failure and broker disconnect — PROOF required demo each
- [ ] **C1.14** Duplicate-order protection — PROOF required same signal twice, one order
- [ ] **C1.15** Restart reads open positions before acting
- [ ] **C1.16** API keys stored in an encrypted vault, never in code
- [ ] **C1.17** Automated tests for risk layer and order handling passing
- [ ] **C1.18** Databento and IBKR paper connected once keys are supplied

> **GATE:** Bill installs the Windows app, clicks through demo mode and green-lights Phase 2.

## Phase 2 — Repo extensions

### Build steps
- [ ] **2.01** Integrate hftbacktest for queue-position and latency simulation _(Testing)_
- [ ] **2.02** Fill-realism report: ideal fills vs. queue-modeled fills per symbol _(Testing)_
- [ ] **2.03** Integrate async_rithmic and test on a Rithmic test account _(Integrations)_
- [ ] **2.04** Language-AI provider connection: key in vault, usage and monthly cost meter, timeout and outage alert _(Integrations)_
- [ ] **2.05** AI supervisor service (TradingAgents pattern), read-only first, reading live state every 1–5 minutes _(AI)_
- [ ] **2.06** Supervisor actions: shrink quota, pause symbol, trigger kill switch; prove it cannot loosen any limit _(Security)_

### Tab 3 checklist items to cross-check at the gate
- [ ] **C2.01** hftbacktest integrated: queue position and latency simulation
- [ ] **C2.02** async_rithmic connection working on a test account
- [ ] **C2.03** AI supervisor built on the TradingAgents pattern, reading state every 1–5 minutes
- [ ] **C2.04** Supervisor can pause symbols, move quota and trigger kill switch, and cannot loosen limits — PROOF required show it blocked

> **GATE:** Bill reviews each add-on inside the app.

## Phase 3 — Knowledge library & Training Center

### Build steps
- [ ] **3.01** Storage: file storage, metadata database and a search index per expert _(Build)_
- [ ] **3.02** Upload service: resumable large files, bulk and folder, phone upload, duplicate detection, file-type checks, malware scan _(Security)_
- [ ] **3.03** Link import: YouTube/Vimeo/course links, web page snapshots _(AI)_
- [ ] **3.04** Processing workers: transcripts with timestamps and speakers, chart frame capture, OCR, document extraction, chunking _(AI)_
- [ ] **3.05** Status pipeline: Uploaded → Processing → Needs review → Approved → Active → Retired / Failed (with reason) _(App)_
- [ ] **3.06** Library screen: tags, quality rating, assign to experts, search that jumps to page or timestamp _(App)_
- [ ] **3.07** Rulebook drafting with a citation on every rule; conflicting rules side by side; approve/edit/reject _(AI)_
- [ ] **3.08** Code each approved rule as a measurable detector; flag the rest 'discretionary only' _(AI)_
- [ ] **3.09** Chart annotation tool for setups, entry, stop, target _(App)_
- [ ] **3.10** Auto-labeling over history with win/loss outcomes, plus an automated look-ahead check _(Testing)_
- [ ] **3.11** Example count per setup with the 300+ target _(App)_
- [ ] **3.12** Strategy ingestion verdict: five gates, Add / Reject / Needs more data card _(AI)_
- [ ] **3.13** Training & Integrations Center screen: training runs, connection status lights _(App)_
- [ ] **3.14** Storage, processing cost and audit views; retire and delete flows _(Security)_

### Tab 3 checklist items to cross-check at the gate
- [ ] **C3.01** Upload works for MP3, WAV, M4A, MP4, MOV, PDF, scanned PDF, DOCX, PPTX, EPUB, TXT, CSV, XLSX, PNG, JPG
- [ ] **C3.02** YouTube/Vimeo/course links import and transcribe
- [ ] **C3.03** Web links snapshotted at upload
- [ ] **C3.04** Bulk, folder and phone upload
- [ ] **C3.05** Duplicate files detected
- [ ] **C3.06** 5 GB+ video uploads and resumes after a dropped connection
- [ ] **C3.07** Transcripts with timestamps and speaker labels — PROOF required open one
- [ ] **C3.08** Scanned PDFs OCR'd and searchable
- [ ] **C3.09** Chart frames from video captured and linked
- [ ] **C3.10** Status pipeline with failure reasons
- [ ] **C3.11** Sources assignable to one or more experts; tags, quality rating, search, filters
- [ ] **C3.12** Search result jumps to exact page or timestamp
- [ ] **C3.13** Retire/delete removes a source from the next training run
- [ ] **C3.14** Audit log of every upload, edit and deletion
- [ ] **C3.15** AI drafts a rulebook per expert with a citation on every rule
- [ ] **C3.16** Conflicting rules flagged side by side
- [ ] **C3.17** Approve / edit / reject each rule
- [ ] **C3.18** Each approved rule has a coded test or is marked discretionary — PROOF required rule-to-code list
- [ ] **C3.19** Chart annotation tool for setups, entry, stop, target
- [ ] **C3.20** Auto-labeling records win/loss over history
- [ ] **C3.21** Example count per setup shown (300+ target)
- [ ] **C3.22** Video strategy ingestion produces an Add / Reject / Needs-more-data verdict

> **GATE:** Bill uploads real material and approves a rulebook.

## Phase 4 — Expert AIs

### Build steps
- [ ] **4.01** Expert template: library, rulebook, feature calculator, model, standard signal output _(AI)_
- [ ] **4.02** Standard signal schema with a validator that rejects malformed signals _(AI)_
- [ ] **4.03** Build expert: Order flow / tape _(AI)_
- [ ] **4.04** Build expert: Auction / volume profile _(AI)_
- [ ] **4.05** Build expert: Price action _(AI)_
- [ ] **4.06** Build expert: Trend following _(AI)_
- [ ] **4.07** Build expert: Mean reversion _(AI)_
- [ ] **4.08** Build expert: Breakout / momentum (ORB) _(AI)_
- [ ] **4.09** Build expert: Event-driven (filter) _(AI)_
- [ ] **4.10** Model registry: version, training date, source list, one-click rollback _(AI)_
- [ ] **4.11** Confidence calibration chart per expert _(Testing)_
- [ ] **4.12** On/off and paper-only switches per expert _(App)_
- [ ] **4.13** Approved Strategy Library with Candidate → Testing → Approved → Active → Retired _(App)_
- [ ] **4.14** Ask the Expert chat with citations _(AI)_

### Tab 3 checklist items to cross-check at the gate
- [ ] **C4.01** Expert has its own library, rulebook and feature calculator
- [ ] **C4.02** Outputs the exact standard signal format — PROOF required sample JSON from live data
- [ ] **C4.03** Plain-English reasoning with cited sources on every signal
- [ ] **C4.04** Version history with source list and one-click rollback
- [ ] **C4.05** Backtest and walk-forward report by instrument, session and regime — PROOF required
- [ ] **C4.06** Locked holdout result — PROOF required holdout dates stated before training
- [ ] **C4.07** Confidence calibration chart
- [ ] **C4.08** On/off and paper-only switches
- [ ] **C4.09** 200+ test trades behind each expert
- [ ] **C4.10** Ask-the-expert chat answers with citations
- [ ] **C4.11** Approved Strategy Library screen with status per strategy

> **GATE:** Bill sees all 7 experts reading the live chart with test reports.

## Phase 5 — Regime, coordinator & supervisor

### Build steps
- [ ] **5.01** Regime detector with live confidence _(AI)_
- [ ] **5.02** Coordinator weighting experts by regime and proven accuracy _(AI)_
- [ ] **5.03** Overlap grouping so structure experts count as one vote _(AI)_
- [ ] **5.04** Adjustable trade threshold in settings _(App)_
- [ ] **5.05** Decision log including no-trades, with every vote and weight _(Tracking)_
- [ ] **5.06** Weekly re-weighting job using completed trades only, logged _(AI)_
- [ ] **5.07** Live view: chart, regime, each expert's read, coordinator decision _(App)_
- [ ] **5.08** Prove the coordinator cannot change risk settings _(Security)_

### Tab 3 checklist items to cross-check at the gate
- [ ] **C5.01** Regime shown live with confidence
- [ ] **C5.02** Coordinator weights experts by regime — PROOF required weight table per regime
- [ ] **C5.03** Overlapping experts grouped as one vote
- [ ] **C5.04** Adjustable trade threshold
- [ ] **C5.05** Every decision logged, including no-trades, with votes and weights
- [ ] **C5.06** Weekly re-weighting runs and is logged
- [ ] **C5.07** Coordinator cannot change risk settings — PROOF required show it blocked

> **GATE:** Bill watches coordinator decisions on a live chart.

## Phase 6 — Evaluation lab & paper trading

### Build steps
- [ ] **6.01** Tick-level backtester with real commissions and slippage _(Testing)_
- [ ] **6.02** Walk-forward runner _(Testing)_
- [ ] **6.03** Holdout data partition locked; access logged _(Security)_
- [ ] **6.04** Reports and side-by-side version comparison _(Testing)_
- [ ] **6.05** Look-ahead audit document per feature _(Testing)_
- [ ] **6.06** Drift monitoring and alerts per expert _(AI)_
- [ ] **6.07** Start 60-day paper run with automatic weekly report _(Testing)_

### Tab 3 checklist items to cross-check at the gate
- [ ] **C6.01** Tick-level backtester with commissions and slippage
- [ ] **C6.02** Walk-forward testing
- [ ] **C6.03** Full report set: profit factor, expectancy, drawdown, Sharpe, win rate, avg win/loss, trade count
- [ ] **C6.04** Side-by-side version comparison
- [ ] **C6.05** Each feature uses only data available at that moment — PROOF required developer explanation
- [ ] **C6.06** 60 trading days of paper trading — PROOF required paper results report
- [ ] **C6.07** Live drift alert per expert

> **GATE:** 60 trading days of paper results meet the agreed targets.

## Phase 7 — Prop-firm execution, server & live

### Build steps
- [ ] **7.01** Prop-firm route: Rithmic or custom Tradovate adapter, matching the firm's written approval _(Integrations)_
- [ ] **7.02** Prop-firm rules per account: trailing drawdown, daily limit, consistency, contract limits, hours, flat-by time _(Risk)_
- [ ] **7.03** Multi-account copy with per-account limits _(Risk)_
- [ ] **7.04** Slippage recorded on every fill _(Testing)_
- [ ] **7.05** All four modes; approval requests expire as 'rejected' if unanswered _(Risk)_
- [ ] **7.06** Provision a Chicago-area server: hardened OS, firewall, private VPN access only, security patches outside trading hours _(Security)_
- [ ] **7.07** Deployment pipeline: deploy only when flat and outside trading hours, one-step rollback _(Security)_
- [ ] **7.08** Uptime monitoring and phone alerts _(Ops)_
- [ ] **7.09** Daily encrypted backups and a tested restore _(Security)_
- [ ] **7.10** Live with approval on 1 contract _(Risk)_
- [ ] **7.11** Unlock criteria for fully automatic recorded in DECISIONS.md _(Tracking)_

### Tab 3 checklist items to cross-check at the gate
- [ ] **C7.01** Tradovate and/or Rithmic connection live on paper
- [ ] **C7.02** Bracket orders (entry, stop, target) sent together
- [ ] **C7.03** Prop-firm rules per account: trailing drawdown, daily limit, consistency, hours, flat-by time
- [ ] **C7.04** Multi-account copy with per-account limits
- [ ] **C7.05** Slippage recorded on every fill
- [ ] **C7.06** All four modes work: Research, Paper, Live with approval, Fully automatic
- [ ] **C7.07** Live with approval on 1 contract
- [ ] **C7.08** Fully automatic unlocked only after approval stage meets targets

> **GATE:** Live with approval on 1 contract meets targets; Bill unlocks automatic.

## Phase 8 — Mac & phone apps

### Build steps
- [ ] **8.01** Mac desktop app connected to the engine _(App)_
- [ ] **8.02** iPhone and Android app from one shared codebase: status, P&L, regime, alerts _(App)_
- [ ] **8.03** Phone security: two-factor login, biometric unlock, registered devices, remote revoke _(Users)_
- [ ] **8.04** Push notifications for trades, breaches and failures _(App)_
- [ ] **8.05** Approve/reject and kill switch from phone, each action audit-logged _(Security)_
- [ ] **8.06** Private distribution (TestFlight / internal testing), not public app stores _(App)_

### Tab 3 checklist items to cross-check at the gate
- [ ] **C8.01** Mac desktop app connects to the engine
- [ ] **C8.02** iPhone app: status, approvals, alerts, kill switch
- [ ] **C8.03** Android app: status, approvals, alerts, kill switch
- [ ] **C8.04** Phone login uses two-factor authentication
- [ ] **C8.05** Phone alerts for trades, breaches and system failures

> **GATE:** Bill runs the apps on his own devices.

## Phase 9 — Hardening & handover

### Build steps
- [ ] **9.01** Security review: dependency scan, secrets audit, access review, penetration checklist _(Security)_
- [ ] **9.02** Disaster recovery drill: kill server, restore from backup, reconcile positions _(Security)_
- [ ] **9.03** Operations and incident runbooks _(Docs)_
- [ ] **9.04** Trade journal export for accountant _(App)_
- [ ] **9.05** Full walkthrough recording _(Docs)_
- [ ] **9.06** Handover: repo, data licenses and all accounts confirmed in Bill's name _(Docs)_

### Tab 3 checklist items to cross-check at the gate
- [ ] **C9.01** Engine runs on a server near Chicago, not a laptop
- [ ] **C9.02** Daily automatic backups and a tested restore
- [ ] **C9.03** Uptime monitoring with alerts
- [ ] **C9.04** Trade journal exports for your accountant
- [ ] **C9.05** Full source code in a repository Bill owns
- [ ] **C9.06** Setup and operations documentation
- [ ] **C9.07** Data licenses and all accounts in Bill's name
- [ ] **C9.08** Walkthrough recording of the whole system

> **GATE:** Bill signs off handover.

