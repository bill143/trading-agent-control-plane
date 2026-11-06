"""Self-contained HTML backtest report (step 1.14). No external assets; light and dark themes."""

from __future__ import annotations

import html
from typing import Any

_CSS = """
:root{--bg:#f6f7f9;--card:#fff;--ink:#14181f;--mut:#5b6472;--line:#e3e6eb;--acc:#0b6bcb;
--good:#137a3f;--bad:#b42318;--warn:#a15c00;--grid:#eef0f3}
@media (prefers-color-scheme:dark){:root{--bg:#0f1318;--card:#171c23;--ink:#e8ecf1;--mut:#9aa4b2;
--line:#2a313b;--acc:#5aa9ff;--good:#4cc38a;--bad:#ff7b72;--warn:#e3b341;--grid:#222a33}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 10px}
.mut{color:var(--mut)}.banner{background:var(--warn);color:#fff;padding:8px 12px;border-radius:8px;
font-weight:600;margin:12px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px}
.k{font-size:12px;color:var(--mut)}.v{font-size:20px;font-weight:650;font-variant-numeric:tabular-nums}
.good{color:var(--good)}.bad{color:var(--bad)}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);
border-radius:10px;overflow:hidden;font-variant-numeric:tabular-nums}
th,td{padding:7px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}th{font-size:12px;color:var(--mut);font-weight:600}
.scroll{overflow-x:auto}svg{width:100%;height:auto;display:block}
"""


def _fmt(v: Any, money: bool = False, pct: bool = False, digits: int = 2) -> str:
    if v is None:
        return "-"
    try:
        x = float(v)
    except (TypeError, ValueError):
        return html.escape(str(v))
    if pct:
        return f"{x * 100:.1f}%"
    if money:
        sign = "-" if x < 0 else ""
        return f"{sign}${abs(x):,.2f}"
    return f"{x:,.{digits}f}"


def _cls(v: Any) -> str:
    try:
        return "good" if float(v) > 0 else "bad" if float(v) < 0 else ""
    except (TypeError, ValueError):
        return ""


def _curve_svg(curve: list[list[Any]] | list[tuple[str, float]], start: float) -> str:
    pts = [start] + [float(p[1]) for p in curve]
    if len(pts) < 2:
        return '<p class="mut">No closed trades.</p>'
    w, h, pad = 1000, 260, 30
    lo, hi = min(pts), max(pts)
    span = (hi - lo) or 1.0
    xs = [pad + i * (w - 2 * pad) / (len(pts) - 1) for i in range(len(pts))]
    ys = [h - pad - (p - lo) / span * (h - 2 * pad) for p in pts]
    path = " ".join(
        f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}"
        for i, (x, y) in enumerate(zip(xs, ys, strict=True))
    )
    base_y = h - pad - (start - lo) / span * (h - 2 * pad)
    return (
        f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Equity curve">'
        f'<line x1="{pad}" x2="{w - pad}" y1="{base_y:.1f}" y2="{base_y:.1f}" '
        'stroke="var(--line)" stroke-dasharray="4 4"/>'
        f'<path d="{path}" fill="none" stroke="var(--acc)" stroke-width="2"/>'
        f'<text x="{pad}" y="16" fill="var(--mut)" font-size="12">{_fmt(hi, money=True)}</text>'
        f'<text x="{pad}" y="{h - 8}" fill="var(--mut)" font-size="12">{_fmt(lo, money=True)}</text>'
        "</svg>"
    )


def _metric_cards(m: dict[str, Any]) -> str:
    cards = [
        ("Net P&L", _fmt(m["net_pnl"], money=True), _cls(m["net_pnl"])),
        ("Trades", _fmt(m["trades"], digits=0), ""),
        ("Win rate", _fmt(m["win_rate"], pct=True), ""),
        ("Profit factor", _fmt(m["profit_factor"]), ""),
        ("Expectancy / trade", _fmt(m["expectancy"], money=True), _cls(m["expectancy"])),
        ("Average win", _fmt(m["avg_win"], money=True), "good"),
        ("Average loss", _fmt(m["avg_loss"], money=True), "bad"),
        ("Max drawdown", _fmt(m["max_drawdown"], money=True), "bad"),
        (
            "Sharpe (annualized)"
            if m["trading_days"] >= 20
            else f"Sharpe ({m['trading_days']} days: too few to judge)",
            _fmt(m["sharpe"]),
            "",
        ),
        ("Commissions", _fmt(m["commissions"], money=True), ""),
    ]
    return (
        '<div class="grid">'
        + "".join(
            f'<div class="card"><div class="k">{k}</div><div class="v {c}">{v}</div></div>'
            for k, v, c in cards
        )
        + "</div>"
    )


def render_html(r: dict[str, Any]) -> str:
    m = r["metrics"]
    rows = "".join(
        f"<tr><td>{html.escape(root)}</td><td>{s['trades']}</td><td>{_fmt(s['win_rate'], pct=True)}"
        f"</td><td>{_fmt(s['profit_factor'])}</td><td class='{_cls(s['net_pnl'])}'>"
        f"{_fmt(s['net_pnl'], money=True)}</td><td>{_fmt(s['expectancy'], money=True)}</td>"
        f"<td>{_fmt(s['max_drawdown'], money=True)}</td><td>{_fmt(s['commissions'], money=True)}"
        "</td></tr>"
        for root, s in r["per_symbol"].items()
    )
    refusals = (
        "".join(f"<tr><td>{html.escape(k)}</td><td>{v}</td></tr>" for k, v in r["refusals"].items())
        or "<tr><td>none</td><td>0</td></tr>"
    )
    trades = "".join(
        f"<tr><td>{html.escape(t['closed'][:19].replace('T', ' '))}</td><td>{html.escape(t['symbol'])}"
        f"</td><td>{t['side']}</td><td>{t['qty']}</td><td>{t['entry']}</td><td>{t['exit']}</td>"
        f"<td class='{_cls(t['pnl'])}'>{_fmt(t['pnl'], money=True)}</td></tr>"
        for t in r["trades"][-200:]
    )
    label = r.get("result_label", "")
    banner = (
        f'<div class="banner"><b>{html.escape(label.upper())}.</b> '
        f"{html.escape(r['data_label'])}. These numbers only show that the pipeline runs end "
        "to end; they say nothing about how any strategy would perform.</div>"
        if label
        else ""
    )
    heading = f"Backtest: {html.escape(label.lower())}" if label else "Backtest results"
    ks = r["kill_switch"]
    kill = (
        "tripped: " + "; ".join(t["reason"] for t in ks["trips"])
        if ks["tripped"]
        else "not tripped"
    )
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Backtest {html.escape(r["run_id"])}</title><style>{_CSS}</style></head><body><main>
<h1>{heading}</h1>
<div class="mut">Run {html.escape(r["run_id"])} &middot; sessions {html.escape(", ".join(r["dates"]))}
&middot; {r["seconds"]} s &middot; data digest {html.escape(r["data_digest"][:12])}</div>
{banner}
{_metric_cards(m)}
{"<p class='mut'><b>" + html.escape(label) + "</b> (synthetic sample data).</p>" if label else ""}
<h2>Equity (closed trades, net of fees)</h2>
<div class="card">{_curve_svg(m["equity_curve"], float(r["starting_equity"]))}</div>
<h2>By symbol</h2><div class="scroll"><table><tr><th>Symbol</th><th>Trades</th><th>Win rate</th>
<th>Profit factor</th><th>Net P&amp;L</th><th>Expectancy</th><th>Max DD</th><th>Fees</th></tr>
{rows}</table></div>
<h2>Risk manager</h2><div class="grid">
<div class="card"><div class="k">Entries approved</div><div class="v">{r["decisions"]["approved"]}</div></div>
<div class="card"><div class="k">Entries refused</div><div class="v">{r["decisions"]["refused"]}</div></div>
<div class="card"><div class="k">Kill switch</div><div class="v" style="font-size:15px">{html.escape(kill)}</div></div>
</div>
<h2>Refusals by rule</h2><div class="scroll"><table><tr><th>Rule</th><th>Count</th></tr>{refusals}</table></div>
<h2>Trades (last 200)</h2><div class="scroll"><table><tr><th>Closed (CT)</th><th>Contract</th>
<th>Side</th><th>Qty</th><th>Entry</th><th>Exit</th><th>Net P&amp;L</th></tr>{trades}</table></div>
<p class="mut">Starting equity {_fmt(r["starting_equity"], money=True)}, ending equity
{_fmt(r["ending_equity"], money=True)}. P&amp;L is net of per-contract commissions; market
entries and stops include modeled slippage. The starter strategy has no claimed edge.</p>
</main></body></html>"""
