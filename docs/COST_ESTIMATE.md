# Operating cost plan (C0.07)

**Approved by Bill on 2026-11-06 as a phased plan** (not a flat monthly amount):

* Phases 1 – 5: Databento **pay-as-you-go only**, starting with the $125 free credits. No
  subscription.
* The Databento **Standard plan ($199/month) starts only at Phase 6** (paper trading).
* **No Interactive Brokers market-data subscription.** Databento is the price source.
* **Nothing paid is downloaded without Bill's sign-off on the exact quoted number.**

Prices were found on 2026-11-06 (sources at the end). Verify on each vendor's own page
before paying.

## Cost by phase

| Phase | What runs | Monthly | One-off |
|---|---|---|---|
| 1 (now) | Desktop app on Bill's PC, synthetic sample data | **$0** | — |
| 1.23 – 5 | Databento pay-as-you-go for history; IBKR paper account for order routing | **$0 subscription** | Historical data per the quote below; the first $125 is covered by Databento's free credits |
| 2 – 5 | Language AI (supervisor, rulebooks, explanations) | **$60 – $250**, capped in the app (step 2.04) | Library processing about $20 – $100 per 100 hours of video |
| 6 | Databento Standard plan (live CME data for 60 trading days of paper) | **$199** | — |
| 7+ | Chicago-area server and backups, plus everything above | **+ $65 – $210** | Prop-firm fees (firm-specific) |
| 9 | Code-signing certificate (deferred to Phase 9, see DECISIONS.md D-10) | — | $200 – $600 per year |

## Historical tick and depth data (pay-as-you-go)

The spec needs 3+ years of tick and order-book history for training, walk-forward tests and the
locked holdout (DECISIONS.md section B). Databento bills history per gigabyte (CME from about
$0.50/GB; heavier data types cost more per GB).

**How the exact price is obtained:** in the app, open Settings > Connections and press
**Get price quote**. The app asks Databento, through its free metadata service, what each request
below would cost and how large it is. Nothing is downloaded and nothing is charged. The quote is
saved (`state/data_plan_quote.json`) and written to the audit log. A paid download is a later,
separate step that runs only after Bill approves the quoted total.

### Download plan (`engine/data_plan.py`)

All requests use dataset GLBX.MDP3 (CME Globex), with each contract as its **volume-based
continuous front month** (for example `MES.v.0`). That way only the contract actually being
traded each day is paid for, not every listed expiry.

| Tier | Data | Contracts | Dates | Purpose |
|---|---|---|---|---|
| A | Contract definitions | all 8 | 2022-11-01 → 2026-10-30 | Tick sizes, expiries, roll mapping |
| A | 1-minute bars (OHLCV) | all 8 | 2022-11-01 → 2026-10-30 | ATR, levels, regime features |
| A | Daily statistics (settlement, OI) | all 8 | 2022-11-01 → 2026-10-30 | Prior-day levels, roll checks |
| B | Every trade + best bid/ask at that moment (TBBO) | all 8 | 2022-11-01 → 2025-10-31 | Tape, VWAP, slippage model: development window |
| B | Same, **locked holdout** | all 8 | 2025-11-03 → 2026-10-30 | Final one-time test only; never used for training |
| B | Every top-of-book change (MBP-1) | MES MCL MGC M6E 10Y | 2022-11-01 → 2025-10-31 | Book lean, spread, queue position: development window |
| B | Same, **locked holdout** | MES MCL MGC M6E 10Y | 2025-11-03 → 2026-10-30 | Final one-time test only |
| C | 10-level depth (MBP-10) | MES MNQ | 2024-11-01 → 2025-10-31 | Order-flow expert (Phase 4): last 12 months of development |

"All 8" = MES, MCL, MGC, M6E, 10Y (Phase 1 set) + MNQ, MYM, MBT (prop-firm list).

### Rough size and price before the quote (for planning only)

These assume about $0.50/GB and typical front-month micro-contract activity. **The quote button
gives the binding number.**

| Tier | Rough size | Rough price | Needed by |
|---|---|---|---|
| A | under 1 GB | **under $5** (inside the free credits) | Phase 3 labeling |
| B (TBBO, all 8, 4 years incl. holdout) | ~100 – 150 GB | ~$50 – $100 | Phase 3 – 4 |
| B (MBP-1, 5 contracts, 4 years incl. holdout) | ~350 – 500 GB | ~$175 – $350 | Phase 4 |
| C (MBP-10, MES + MNQ, 12 months) | ~500 – 800 GB | ~$250 – $500 | Phase 4 order-flow expert |
| **Total A + B** | | **~$230 – $455** (less $125 credits) | |
| **Total A + B + C** | | **~$480 – $955** (less $125 credits) | |

Downloads can be split by tier and phase. Tier A fits inside the free credits. Tiers B and C are
bought only when the phase that uses them starts, each after Bill approves its quoted price.
Databento lets a paid batch request be downloaded again free for 30 days; after that, the local
copy in the data catalog is the record.

One point for Bill to weigh when the quote arrives: Databento's Standard plan also includes
historical data under its flat fee. If the quoted A + B (+ C) total is well above $199, starting
the Standard plan for one month to download history could cost less than pay-as-you-go. That
would change the "Standard only at Phase 6" decision, so it stays Bill's call.

## Line items

| Item | When | Price found | Notes |
|---|---|---|---|
| Databento pay-as-you-go history | Phases 1.23 – 5 | per quote (above); $125 free credits on a new account | Usage-based; billed by uncompressed size. Exact price from the in-app quote. |
| Databento **Standard** plan | **Phase 6** | **$199 / month** (rose from $179 on 2026-06-22) | Live CME, CBOT, NYMEX, COMEX data. Personal, non-professional use without separate exchange licence fees (up to 2 devices). Plus ($1,750) and Unlimited ($4,500) are annual contracts and are not needed. |
| Interactive Brokers paper account | Step 1.23 onward | **$0** (no market-data subscription, per Bill) | Order routing only. Note: without an IBKR data subscription, IBKR's paper simulator may fill against delayed IBKR prices. The engine itself always decides on Databento prices, and Phase 6 compares IBKR paper fills with Databento to measure that gap before any live money. |
| Language AI (Claude API) | Phase 2 onward | $60 – $250 / month | Mid-tier model about $3 / $15 per million input / output tokens; small model about $1 / $5. Monthly cost meter and cap built in (step 2.04). |
| Chicago-area server | Phase 7 | $60 – $190 / month | Trading-VPS providers list $49 – $190/month near CME Aurora. Chosen by measured latency. |
| Encrypted off-site backups | Phase 7 | $5 – $20 / month | Step 7.09. |
| Code-signing certificate | **Phase 9** | $200 – $600 / year | Deferred (DECISIONS.md D-10). The unsigned installer is accepted for Bill's own use. |
| Broker commissions | Paper (simulated) / live | per trade | About $0.62 – $0.77 per micro contract per side; in every backtest and the fee filter. |

## Free

NautilusTrader (open source), the desktop app, the sample data pack, the IBKR paper account, the
Databento cost quotes.

## Sources (as found 2026-11-06)

- Databento: "Updates to subscription pricing", "Introducing new CME pricing plans"
  (databento.com/blog); "Usage-based pricing and credits" FAQ; historical data and futures pages;
  `metadata.get_cost` API reference (free cost estimate).
- Third-party reviews of Databento (QuantVPS, QuantBrain) for the $125 new-account credits.
- Chicago VPS: QuantVPS pricing page; TradingVPS.io; NexusFi infrastructure listings.
- Claude API pricing: third-party summaries; verify on platform.claude.com/docs/en/about-claude/pricing.

databento.com could not be opened directly from the build environment (network policy), so the
Databento figures come from search results quoting Databento's own pages. The in-app quote reads
prices from Databento directly and is the number that counts.
