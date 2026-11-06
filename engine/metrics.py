"""Performance metrics for backtest, paper and live results (step 1.14; reused in Phase 6).

All P&L figures are net of commissions. Definitions:
* profit factor = gross profit / gross loss (``None`` with no losing trades)
* expectancy    = average net P&L per trade
* max drawdown  = largest peak-to-trough fall of the closed-trade equity curve, in USD and as a
  share of the peak equity
* Sharpe        = mean / standard deviation of daily net P&L as a return on starting equity,
  annualized by sqrt(252); ``None`` with fewer than 2 trading days
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

ZERO = Decimal(0)


@dataclass(frozen=True)
class TradeRecord:
    root: str
    symbol: str
    side: str  # "long" / "short"
    qty: int
    opened: datetime
    closed: datetime
    entry: float
    exit: float
    pnl: Decimal  # net of commissions
    commission: Decimal
    trade_date: date

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "symbol": self.symbol,
            "side": self.side,
            "qty": self.qty,
            "opened": self.opened.isoformat(),
            "closed": self.closed.isoformat(),
            "entry": self.entry,
            "exit": self.exit,
            "pnl": str(self.pnl),
            "commission": str(self.commission),
            "trade_date": self.trade_date.isoformat(),
        }


@dataclass(frozen=True)
class Metrics:
    trades: int
    wins: int
    losses: int
    net_pnl: Decimal
    gross_profit: Decimal
    gross_loss: Decimal
    commissions: Decimal
    win_rate: float | None
    avg_win: Decimal | None
    avg_loss: Decimal | None
    profit_factor: float | None
    expectancy: Decimal | None
    max_drawdown: Decimal
    max_drawdown_pct: float
    sharpe: float | None
    trading_days: int
    equity_curve: list[tuple[str, float]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k, v in self.__dict__.items():
            if isinstance(v, Decimal):
                out[k] = str(v.quantize(Decimal("0.01")))
            elif isinstance(v, float):
                out[k] = round(v, 4)
            else:
                out[k] = v
        return out


def compute(trades: Iterable[TradeRecord], starting_equity: Decimal) -> Metrics:
    ts = sorted(trades, key=lambda t: t.closed)
    wins = [t.pnl for t in ts if t.pnl > 0]
    losses = [t.pnl for t in ts if t.pnl <= 0]
    gross_profit = sum(wins, ZERO)
    gross_loss = -sum(losses, ZERO)
    net = gross_profit - gross_loss
    n = len(ts)

    equity = starting_equity
    peak = equity
    max_dd = ZERO
    max_dd_pct = 0.0
    curve: list[tuple[str, float]] = []
    for t in ts:
        equity += t.pnl
        curve.append((t.closed.isoformat(), float(equity)))
        peak = max(peak, equity)
        dd = peak - equity
        if dd > max_dd:
            max_dd = dd
            max_dd_pct = float(dd / peak) if peak > 0 else 0.0

    daily: dict[date, Decimal] = defaultdict(lambda: ZERO)
    for t in ts:
        daily[t.trade_date] += t.pnl
    sharpe: float | None = None
    if len(daily) >= 2 and starting_equity > 0:
        rets = [float(v / starting_equity) for v in daily.values()]
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        sd = math.sqrt(var)
        sharpe = (mean / sd) * math.sqrt(252) if sd > 0 else None

    return Metrics(
        trades=n,
        wins=len(wins),
        losses=len(losses),
        net_pnl=net,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        commissions=sum((t.commission for t in ts), ZERO),
        win_rate=len(wins) / n if n else None,
        avg_win=gross_profit / len(wins) if wins else None,
        avg_loss=-gross_loss / len(losses) if losses else None,
        profit_factor=float(gross_profit / gross_loss) if gross_loss > 0 else None,
        expectancy=net / n if n else None,
        max_drawdown=max_dd,
        max_drawdown_pct=max_dd_pct,
        sharpe=sharpe,
        trading_days=len(daily),
        equity_curve=curve,
    )


def by_symbol(trades: Iterable[TradeRecord], starting_equity: Decimal) -> dict[str, Metrics]:
    groups: dict[str, list[TradeRecord]] = defaultdict(list)
    for t in trades:
        groups[t.root].append(t)
    return {root: compute(ts, starting_equity) for root, ts in sorted(groups.items())}
