"""Build the risk manager's :class:`PortfolioView` from NautilusTrader's portfolio and cache."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from nautilus_trader.model.currencies import USD

from engine.context import EngineContext
from engine.nautilus.instruments import GLOBEX
from risk.manager import AccountView, PortfolioView, PositionView, Side


def root_of(symbol: str) -> str:
    """``MESZ6`` -> ``MES``; ``10YX6`` -> ``10Y`` (root + month code + year digit)."""
    return symbol[:-2]


def _money(m: Any) -> Decimal:
    return Decimal(str(m.as_decimal())) if m is not None else Decimal(0)


def portfolio_view(component: Any, ctx: EngineContext) -> PortfolioView:
    """``component`` is any Nautilus actor/strategy (gives ``portfolio`` and ``cache``)."""
    portfolio, cache = component.portfolio, component.cache
    account = portfolio.account(GLOBEX)
    balance = _money(account.balance_total(USD)) if account is not None else Decimal(0)
    positions: list[PositionView] = []
    margin_used = Decimal(0)
    unrealized_total = Decimal(0)
    for pos in cache.positions_open():
        root = root_of(pos.instrument_id.symbol.value)
        qty = int(pos.quantity.as_double())
        unreal = _money(portfolio.unrealized_pnl(pos.instrument_id))
        unrealized_total += unreal
        positions.append(
            PositionView(
                root=root,
                side=Side.LONG if pos.is_long else Side.SHORT,
                qty=qty,
                avg_price=Decimal(str(pos.avg_px_open)),
                unrealized=unreal,
            )
        )
        margin_used += ctx.risk.margin(root) * qty
    with ctx.store.lock:
        pending = tuple(ctx.pending.values())
        trackers = [ctx.store.tracker(r) for r in ctx.store.roots]
        realized = sum((Decimal(str(t.realized_today)) for t in trackers), Decimal(0))
        used = {t.root: t.contracts_used for t in trackers}
    return PortfolioView(
        account=AccountView(equity=balance + unrealized_total, margin_used=margin_used),
        positions=tuple(positions),
        pending=pending,
        realized_today=realized,
        contracts_used=used,
    )
