"""Step 1.01: NautilusTrader is installed at the pinned version and runs a sample backtest."""

from __future__ import annotations

import tomllib
from decimal import Decimal
from pathlib import Path

import nautilus_trader
import numpy as np
import pandas as pd
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.examples.strategies.ema_cross_bracket import (
    EMACrossBracket,
    EMACrossBracketConfig,
)
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import BarType, QuoteTick
from nautilus_trader.model.enums import AccountType, OmsType
from nautilus_trader.model.identifiers import TraderId, Venue
from nautilus_trader.model.objects import Money, Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider

ROOT = Path(__file__).resolve().parent.parent
PINNED = "1.231.0"


def test_pinned_version_installed_and_locked() -> None:
    assert nautilus_trader.__version__ == PINNED
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert f"nautilus-trader=={PINNED}" in [d.replace("_", "-") for d in deps["dependencies"]]
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    versions = {p["name"]: p["version"] for p in lock["package"]}
    assert versions["nautilus-trader"] == PINNED


def test_sample_backtest_runs_and_trades() -> None:
    instrument = TestInstrumentProvider.es_future(2026, 12)
    engine = BacktestEngine(
        BacktestEngineConfig(trader_id=TraderId("SMOKE-001"), logging=LoggingConfig("ERROR"))
    )
    venue = Venue("GLBX")
    engine.add_venue(
        venue,
        oms_type=OmsType.NETTING,
        account_type=AccountType.MARGIN,
        base_currency=USD,
        starting_balances=[Money(100_000, USD)],
    )
    engine.add_instrument(instrument)

    rng = np.random.default_rng(7)
    start = pd.Timestamp("2026-11-02 14:30", tz="UTC").value
    mids = 6000.0 + np.cumsum(rng.choice([-0.25, 0.0, 0.25], size=12_000))
    ticks = [
        QuoteTick(
            instrument.id,
            instrument.make_price(mid - 0.25),
            instrument.make_price(mid + 0.25),
            Quantity.from_int(10),
            Quantity.from_int(10),
            start + i * 1_000_000_000,
            start + i * 1_000_000_000,
        )
        for i, mid in enumerate(mids)
    ]
    engine.add_data(ticks)
    engine.add_strategy(
        EMACrossBracket(
            EMACrossBracketConfig(
                instrument_id=instrument.id,
                bar_type=BarType.from_str(f"{instrument.id}-1-MINUTE-MID-INTERNAL"),
                trade_size=Decimal(1),
            )
        )
    )
    try:
        engine.run()
        fills = engine.trader.generate_order_fills_report()
        assert len(fills) > 0, "sample backtest produced no fills"
        balance = engine.portfolio.account(venue).balance_total(USD)
        assert balance is not None
    finally:
        engine.dispose()
