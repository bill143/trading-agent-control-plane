# DECISIONS

Decision log for the Multi-Expert AI Trading System. Source of truth for the plan is
`docs/BUILD_PLAN.html` (Tabs 1–6) and `docs/BUILD_SEQUENCE.md`. Every departure from the plan is
logged here (section D). Entries are append-only: a changed decision gets a new entry that
supersedes the old one; the old one is never edited.

Status values: **Decided** (from the plan or Bill), **Proposed** (needs Bill's agreement),
**Requires Bill** (an account, contract or action only Bill can take).

---

## A. Core decisions (step 0.03)

| ID | Topic | Decision | Status | Source |
|---|---|---|---|---|
| A-01 | Foundation | NautilusTrader `1.231.0` (nautechsystems/nautilus_trader, LGPL-3.0) is the core engine: data, strategies, risk, portfolio, orders, backtesting and live trading. Other repos are added only in Phase 2, one milestone each, after Bill's Phase 1 green light. | Decided | Tab 1, chat |
| A-02 | Phase 1 symbols (active) | MES, MCL, MGC, M6E, 10Y (micro 10-year yield). Chosen across asset classes so risk is spread, not stacked. | Decided | Tab 5 |
| A-03 | Configured, off | MNQ, MYM, MBT are defined but disabled in Phase 1. They are the prop-firm list (with MGC) used by the intraday experts from Phase 4, with targets large enough to clear fees. | Decided | Tab 5 |
| A-04 | Correlation groups | MNQ + MYM (+ MES when an index position is open) count as one US-equity-index exposure in the risk manager. | Decided | Tab 5 |
| A-05 | Fee filter | A symbol qualifies for a strategy only if target ticks × tick value ≥ 2× round-trip fees, recalculated daily. | Decided | Tab 2, step 1.08 |
| A-06 | Broker route | Interactive Brokers paper for Phases 1–6 (adapter built into Nautilus). Phase 7 prop accounts: Rithmic via async_rithmic if the firm supports it, otherwise a custom Tradovate adapter (budgeted). | Decided | Tab 5 |
| A-07 | Market data | Databento (adapter built into Nautilus), CME Globex `GLBX.MDP3`. Licence must permit automated use; professional vs. non-professional status confirmed by Bill. | Requires Bill (account, C0.04) | Tab 5 |
| A-08 | Holding time | The multi-expert intraday system is the product. The 1–2 tick scalper is one strategy in the library and must pass the same tests, starting with hftbacktest fill realism. | Decided | Tab 5 |
| A-09 | Hosting | Engine runs on Bill's Windows PC through Phase 6; a Chicago-area server (near CME Aurora, IL) before Phase 7. Desktop and phone apps are remote controls; the phone never runs the engine. | Decided | Tab 1, Tab 5 |
| A-10 | Platforms | Windows desktop first; Mac after it is proven; one shared codebase for iPhone and Android (Phase 8), private distribution only. | Decided | chat, Tab 1 |
| A-11 | AI roles | Fast numeric models make trade decisions. Language AIs read material, explain, supervise and chat; they never sit in the order path. No AI process can change risk settings. | Decided | Tab 4 |
| A-12 | V1 AI count | 10 components: 7 core experts + regime detector + coordinator + one language-AI service playing the five language roles. | Decided | Tab 4 |
| A-13 | Proof ladder | Backtest → walk-forward → locked holdout → 60 trading days of paper → live with approval on 1 contract → fully automatic. | Decided | Tab 1 |
| A-14 | Target metrics | Profit factor above 1.3; max drawdown inside the prop-firm limit; 200+ test trades per expert before live weight. | Decided (Bill accepted C0.08, 2026-11-06) | Tab 5 |
| A-15 | Time handling | All times stored in exchange time (`America/Chicago`) with automatic daylight-saving handling and tests on the switch dates. Status dates are recorded in CT. | Decided | Tab 5 |
| A-16 | Toolchain | Python `3.13.14` (`.python-version`), Rust `1.97.1` (`rust-toolchain.toml`, matches Nautilus 1.231.0), uv with `uv.lock`. Windows x64 wheels exist for every pinned package. | Decided | step 0.05 |

## B. Locked holdout period (step 0.04)

Written and committed **before any historical market data has been downloaded**. The git commit
that introduces this section is the proof (C0.09). These dates may not be changed after any
training run starts; a change requires a new entry, Bill's written approval, and the old holdout
is then treated as "seen" and may never be reused as a holdout.

| Window | Start (CT session date) | End (CT session date) | Use |
|---|---|---|---|
| Development | 2022-11-01 | 2025-10-31 | Training, labeling, walk-forward, tuning. Three years. |
| **Locked holdout** | **2025-11-03** | **2026-10-30** | One final test per expert/strategy version. Never used for training, tuning, feature selection or threshold setting. |
| Paper | first session after Phase 6 starts | +60 trading days | Forward test on live data (step 6.07). |

Rules:
1. Holdout data is stored separately (`data/holdout/`, never committed) and every read is
   written to the audit log (step 6.03).
2. Walk-forward folds are drawn from the development window only.
3. A strategy or expert that is changed after seeing its holdout result needs a new, later
   holdout or goes straight to paper with that fact recorded here.

## B2. Gate 0 green light and conditions (Bill, 2026-11-06)

Gate 0 passed with these conditions, in force until Bill lifts them:

| ID | Condition | Effect on the build |
|---|---|---|
| G0-1 | Bill is locked out of GitHub (support case open); the repo cannot be made private and PR #1 cannot be merged yet. | Step 0.01 stays blocked. Phase 1 work continues on `claude/phase-0-setup`, stacked on PR #1. Nothing is merged. |
| G0-2 | Until Bill confirms the repo is private: no API keys, passwords or account details in any commit. | Enforced by the secrets policy, pre-commit gitleaks and CI history scan. Account IDs, usernames and broker account numbers are treated as secrets too. |
| G0-3 | Sample data only until the repo is private. | No licensed market data is downloaded or committed. Demo mode runs on the generated sample pack (step 1.03). |
| G0-4 | C0.01, C0.08, C0.09 accepted by Bill. | Recorded as built in the status block. |
| G0-5 | C0.02 / C0.03 not needed: no paid developer. | Left unmarked; recorded here as not applicable. |
| G0-6 | C0.04 / C0.05 (Databento, IBKR paper) are being opened by Bill. | Needed by step 1.23; that step blocks until then. |
| G0-7 | C0.06 (prop-firm approval) needed before Phase 7. | Phase 7 gate depends on it. |
| G0-8 | C0.07: the monthly cost estimate goes to Bill before any paid data subscription starts. | Delivered as `docs/COST_ESTIMATE.md`; approved as a phased plan on 2026-11-06 (D-11). |

## C. Tab 5 items: requirement and where it is handled

| Item | Requirement / decision | Status | Handled in |
|---|---|---|---|
| Which contracts to trade | See A-02 – A-04 | Decided | 1.04, 1.09 |
| Which broker | See A-06 | Decided | 1.23, 2.03, 7.01 |
| How long trades are held | See A-08 | Decided | 2.01, 2.02 |
| Where the engine runs | See A-09 | Decided | 7.06 |
| Prop-firm automation rules | Written confirmation from each firm that the exact setup is allowed, before live | Requires Bill (C0.06) | 7.01 |
| Overfitting | Walk-forward plus a locked holdout chosen before training | Decided (section B) | 6.02, 6.03 |
| Look-ahead bias | Every feature documented as using only data available at that moment | Decided | 3.10, 6.05 |
| Costs and slippage | Every backtest includes real commissions and modeled slippage | Decided | 1.14, 6.01 |
| Correlated positions | Correlated symbols treated as one exposure | Decided (A-04) | 1.09 |
| Minimum sample size | No expert goes live with fewer than 200 test trades | Decided (A-14) | C4.09 |
| Model drift | Live monitoring per expert, drift alerts, retraining schedule | Decided | 6.06 |
| Paper vs. live gap | Live with approval on 1 contract; compare slippage to paper before scaling | Decided | 7.04, 7.10 |
| Realistic expectations | Profit factor above 1.3, drawdown inside prop limits | Decided (A-14) | C0.08 |
| Speed | Numeric models decide; language AIs explain and supervise | Decided (A-11) | 4.x, 2.05 |
| Power or internet loss | Stops and targets rest at the broker as bracket orders; server before live | Decided | 1.12, 7.06 |
| Time zones and DST | Exchange time with automatic DST and switch-date tests | Decided (A-15) | 1.05 |
| Contract rollover and holidays | Automatic rollover and holiday calendar | Decided | 1.05 |
| Updates during market hours | Deploy only when flat and outside trading hours, rollback ready | Decided | 7.07 |
| Data volume | Storage plan and cost in the monthly estimate | Requires Bill (C0.07) | 3.01 |
| Language-AI provider dependency | Trading continues safely without it; outage raises an alert, never a trade | Decided | 2.04 |
| Market data licensing | Named vendor, monthly cost, licence permitting automated use | Requires Bill (A-07) | 1.23 |
| Course material rights | Personal use only; never resold or redistributed | Decided | 3.x |
| Selling signals / managing money | Futures attorney before offering anything to others | Requires Bill | — |
| Open-source licence (LGPL-3.0) | Private use is fine; attorney review before any commercial release | Decided | 9.06 |
| IP and ownership | Contract states Bill owns all code, models, data and documentation | Not needed: no paid developer (Bill, 2026-11-06) | — |
| Payment tied to proof | Milestone payments released only when checklist items are shown working | Not needed: no paid developer (Bill, 2026-11-06) | — |
| Ongoing running costs | Monthly cost estimate agreed | Requires Bill (C0.07) | — |
| Tax records | Journal exports usable by Bill's accountant | Decided | 9.04 |
| Labeling effort | Labeling plan with hours estimated and owner | Decided | 3.09 – 3.11 |
| Human override | Live-with-approval mode; phone kill switch behind two-factor login | Decided | 7.05, 8.03, 8.05 |
| Approval fatigue | Written rule for moving from approval to automatic, based on agreed metrics | Decided | 7.11 |
| Who watches when Bill can't | Automatic halts on every failure; default to flat and stopped when unsure | Decided | 1.11 |

## D. Departures from the plan

| ID | Date (CT) | Departure | Reason |
|---|---|---|---|
| D-01 | 2026-11-05 | One bootstrap commit (README, .gitignore) was pushed directly to `main`. | The repository was empty, so no branch existed to protect or to open a pull request against. All later work goes through pull requests. |
| D-02 | 2026-11-05 | Added `tools/` (build-status tracker) alongside the step 1.02 layout. | Keeps the HTML status block and BUILD_STATUS.md in sync and enforces the gate rules mechanically. |
| D-03 | 2026-11-05 | Repository `bill143/trading-agent-control-plane` is currently **public**; step 0.01 requires private. | Changing visibility and branch protection needs the owner's GitHub settings. Step 0.01 stays blocked until Bill makes it private and applies `docs/github/BRANCH_PROTECTION.md`. |
| D-04 | 2026-11-06 | `fsspec` overridden from 2026.2.0 (pinned by NautilusTrader 1.231.0) to the patched release (`[tool.uv] override-dependencies`). | CVE-2026-104851 / GHSA-27vj-qcqg-25rc (code execution via `ReferenceFileSystem`). Full test suite, including the Nautilus backtest, passes on the override. Remove when Nautilus ships the fix. |
| D-05 | 2026-11-06 | NautilusTrader's compiled modules have no type stubs; mypy treats them as untyped. | All risk, API and state logic is written in typed pure-Python modules; only `engine/nautilus/` and `strategies/` touch Nautilus types. A test enforces that `risk/` and `api/` never import Nautilus. |
| D-06 | 2026-11-06 | The demo sample pack is **synthetic**: generated deterministically (seed 20261102) on the real CME session calendar with real contract symbols, tick sizes and roll schedule, for trade dates 2026-11-02 .. 2026-11-06. Every screen and report labels it "SAMPLE DATA - synthetic". | Gate 0 condition G0-3 (sample data only) and no data licence yet. The same NautilusTrader catalog format takes licensed Databento data in step 1.23. Scheduled events in the pack follow real release patterns (EIA Wednesdays, claims Thursdays, payrolls first Friday) but are not a live calendar feed. |
| D-07 | 2026-11-06 | Desktop app = the engine + control API in one local process, shown in a native window (Edge WebView2 via pywebview). The window loads the dashboard over HTTPS from 127.0.0.1 and trusts only the engine's own certificate (SPKI pin passed to WebView2). The dashboard is plain HTML/CSS/JS (no build step, no third-party scripts). | One codebase serves the Windows window now, the Mac window later (WebKit) and the same API serves the phone apps (Phase 8). Chromium-based UI tests drive exactly what Bill sees. |
| D-08 | 2026-11-06 | The Windows installer is built and tested in CI but is **unsigned** until a code-signing certificate exists. CI signs automatically once `CODESIGN_PFX_BASE64` / `CODESIGN_PFX_PASSWORD` repository secrets are added. | Buying a code-signing certificate (OV or EV, roughly $200-$600 per year) is a payment only Bill can make. Unsigned installers show a Windows SmartScreen warning ("More info" -> "Run anyway"). |
| D-09 | 2026-11-06 | No auto-updater is shipped: auto-update is off. Updating = running a newer installer (settings, vault, users and history are kept in %LOCALAPPDATA%\TradingSystem). | Step 1.21: "auto-update off unless Bill turns it on". Turning it on needs a private release channel; built when Bill asks for it. |
| D-10 | 2026-11-06 | **Code signing deferred to Phase 9** (supersedes the timing in D-08). The unsigned installer is accepted for Bill's own use; Windows SmartScreen shows "Windows protected your PC" on first run (More info -> Run anyway). CI keeps the signing step ready for when the certificate exists. | Bill's decision 2026-11-06. |
| D-11 | 2026-11-06 | **Cost plan approved as phased (C0.07)**: Phases 1-5 Databento pay-as-you-go only (start with the $125 free credits, no subscription); Databento Standard ($199/month) starts at Phase 6 paper trading; **no IBKR market-data subscription**: Databento is the only price source, IBKR is order routing only. Supersedes the flat estimate and the IBKR data line in A-07 / the first COST_ESTIMATE.md. | Bill's decision 2026-11-06. Consequence logged in docs/COST_ESTIMATE.md: IBKR's paper simulator may fill on delayed IBKR prices; Phase 6 measures paper fills against Databento before live money. |
| D-12 | 2026-11-06 | Historical data plan fixed in `engine/data_plan.py` (tiers A/B/C, continuous front months, development window + separately stored holdout). The app gets the **exact** price from Databento's free cost endpoint (Settings > Connections > Get price quote). **No paid data is downloaded without Bill's written sign-off on the quoted total**, recorded here as a new entry. | Bill's decision 2026-11-06. The quote module has no download code; a test enforces that. |
| D-13 | 2026-11-06 | Every backtest metric produced on the synthetic sample pack is labeled **"Pipeline test only, not a result"** (reports, app, status records). | Bill's decision 2026-11-06: the 1.07/1.14 profit factor of 1.07 is from synthetic data and says nothing about real performance. |
| D-14 | 2026-11-06 | `pip-system-certs` (a Windows-only dependency of the `databento` client) is excluded through a uv override. | It patches Python's `ssl` module for the whole process at start-up, silently replacing certificate verification. CI caught it: certificate-pinned TLS tests failed on Windows. The app's TLS behaviour stays explicit; Databento's client works with the standard certificate bundle. |
