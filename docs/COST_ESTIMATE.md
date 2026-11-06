# Monthly operating cost estimate (C0.07)

Prepared 2026-11-06 for Bill's agreement **before any paid data subscription starts** (Gate 0
condition G0-8). Prices come from public sources found on that date (listed at the end); verify
each on the vendor's own page before subscribing. No subscription has been started.

## Summary

| Stage | What runs | Estimated monthly cost |
|---|---|---|
| Now (Phase 1 demo) | App on your PC, sample data | **$0** |
| Phases 1.23 – 6 (paper trading on your PC) | Databento live + history, IBKR paper with futures data | **about $210 – $240** |
| + language AI (Phases 2 – 5) | AI supervisor, rulebook drafting, explanations | **+ about $60 – $250** |
| Phase 7+ (live, Chicago-area server) | everything above + server + backups | **about $350 – $750** |
| One-off / yearly | code-signing certificate | $200 – $600 per year |

## Line items

| Item | Needed from | Price found | Notes |
|---|---|---|---|
| Databento CME Globex **Standard** plan | Step 1.23 | **$199 / month** (rose from $179 on 2026-06-22) | Live CME, CBOT, NYMEX, COMEX data for the system, plus history. Standard covers personal, non-professional use without separate exchange licence fees (up to 2 devices). Plus ($1,750/mo) and Unlimited ($4,500/mo) are annual contracts and are not needed. Historical downloads outside the plan are usage-based. |
| Interactive Brokers market data (futures bundle, non-professional) | Step 1.23 | **about $10 / month**, reported waived with $30+ commissions per month | Needed for the IBKR paper account to show and fill against CME prices. IBKR paper accounts share the data subscriptions of the funded live account, which needs a minimum deposit. Professional status costs far more ($134.50 per exchange per month): answer the non-professional questionnaire accurately. |
| Language AI (Claude API) | Phase 2 onward | **$60 – $250 / month** | Supervisor reading state every 5 minutes in trading hours (~6,000 calls/month at about $0.01 – $0.03 each with a mid-tier model), plus signal explanations and Ask-the-Expert chat. One-off library processing in Phase 3 (transcripts, rulebooks) is extra: roughly $20 – $100 per 100 hours of video. A monthly cost meter and cap are built in (step 2.04). |
| Chicago-area server (near CME Aurora) | Phase 7 (before live money) | **$60 – $190 / month** | Trading-VPS providers list $49 – $190/month for Chicago machines with sub-millisecond advertised latency to CME. The final pick is measured, not taken from marketing. |
| Encrypted off-site backups | Phase 7 | $5 – $20 / month | Daily encrypted backups with a tested restore (step 7.09). |
| Prop-firm evaluation / data fees | Phase 7 | Firm-specific | Not included: depends on the firm and account size Bill chooses. Rithmic or Tradovate routing fees are usually bundled by the firm. |
| Code-signing certificate | When Bill wants the SmartScreen warning gone | $200 – $600 / year | Optional, see DECISIONS.md D-08. |
| Broker commissions | Live trading | Per trade | Built into every backtest and the fee filter (about $0.62 – $0.77 per micro contract per side). |

## What is free

GitHub private repository (on Bill's plan), CI minutes for a private repo within the plan's
allowance, NautilusTrader (open source), the desktop app, the sample data pack, the IBKR paper
account itself.

## Decision needed from Bill

1. Agree the paper-trading budget (about $210 – $240/month) before step 1.23 subscribes to
   Databento Standard.
2. Confirm **non-professional** market-data status with Databento and IBKR.
3. Agree a monthly cap for language-AI spend (suggested: $150) before Phase 2.

## Sources (as found 2026-11-06)

- Databento, "Updates to subscription pricing" and "Introducing new CME pricing plans"
  (databento.com/blog); Databento live data guide (databento.com/docs/portal/live-data).
- IBKR market data: dev.to guide "Interactive Brokers market data subscription: which one do I
  actually need in 2026"; NexusFi "Exchange data fees / market data costs"; Interactive Brokers
  Campus "Subscribing to data".
- Chicago VPS: QuantVPS pricing page; TradingVPS.io; NexusFi infrastructure listings.
- Claude API pricing: third-party summaries (Finout, MetaCTO); verify on
  platform.claude.com/docs/en/about-claude/pricing.

databento.com could not be opened directly from the build environment (network policy), so the
Databento figures above come from search results quoting Databento's own announcements; Bill
should confirm them on databento.com/pricing before subscribing.
