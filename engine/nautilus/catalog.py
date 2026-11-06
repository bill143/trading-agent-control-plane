"""Write and read the sample data pack as a NautilusTrader ``ParquetDataCatalog``.

Licensed Databento data (step 1.23) lands in the same catalog format, so the engine reads demo
and real data the same way.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Final

from nautilus_trader.model.data import QuoteTick, TradeTick
from nautilus_trader.model.enums import AggressorSide
from nautilus_trader.model.identifiers import TradeId
from nautilus_trader.model.instruments import FuturesContract
from nautilus_trader.model.objects import Price, Quantity
from nautilus_trader.persistence.catalog import ParquetDataCatalog

from engine import sample_data
from engine.events import EventCalendar
from engine.instruments import contract
from engine.nautilus.instruments import to_nautilus

MANIFEST: Final = "manifest.json"
EVENTS: Final = "events.json"
CATALOG: Final = "catalog"


@dataclass(frozen=True)
class PackInfo:
    path: Path
    version: int
    seed: int
    digest: str
    label: str
    symbols: list[str]
    contracts: list[tuple[str, int, int]]  # (root, year, month)
    roots: list[str]
    dates: list[date]
    quote_count: int
    trade_count: int

    @property
    def catalog(self) -> ParquetDataCatalog:
        return ParquetDataCatalog(str(self.path / CATALOG))

    @property
    def events(self) -> EventCalendar:
        return EventCalendar.load(self.path / EVENTS)

    def instruments(self) -> list[FuturesContract]:
        return [to_nautilus(contract(root, year, month)) for root, year, month in self.contracts]


def _ticks(day: sample_data.ContractDay, inst: FuturesContract) -> tuple[list[Any], list[Any]]:
    prec = inst.price_precision
    q = day.quotes
    quotes = [
        QuoteTick(
            inst.id,
            Price(float(b), prec),
            Price(float(a), prec),
            Quantity.from_int(int(bs)),
            Quantity.from_int(int(asz)),
            int(ts),
            int(ts),
        )
        for ts, b, a, bs, asz in zip(q.ts_ns, q.bid, q.ask, q.bid_size, q.ask_size, strict=True)
    ]
    t = day.trades
    sym = day.contract.symbol
    trades = [
        TradeTick(
            inst.id,
            Price(float(p), prec),
            Quantity.from_int(int(sz)),
            AggressorSide.BUYER if buy else AggressorSide.SELLER,
            TradeId(f"{sym}-{day.session.trade_date:%Y%m%d}-{i}"),
            int(ts),
            int(ts),
        )
        for i, (ts, p, sz, buy) in enumerate(
            zip(t.ts_ns, t.price, t.size, t.buyer_aggressor, strict=True)
        )
    ]
    return quotes, trades


def write_pack(pack: sample_data.SamplePack, path: Path) -> PackInfo:
    """Write ``pack`` to ``path`` (replacing any previous pack there) and return its info."""
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    catalog = ParquetDataCatalog(str(path / CATALOG))
    instruments = {c.symbol: to_nautilus(c) for c in pack.contracts()}
    catalog.write_data(list(instruments.values()))
    n_quotes = n_trades = 0
    for day in pack.days:
        quotes, trades = _ticks(day, instruments[day.contract.symbol])
        catalog.write_data(quotes)
        catalog.write_data(trades)
        n_quotes += len(quotes)
        n_trades += len(trades)
    EventCalendar(pack.events).save(path / EVENTS)
    manifest = {
        "version": pack.version,
        "seed": pack.seed,
        "digest": pack.digest(),
        "label": pack.label,
        "symbols": sorted(instruments),
        "contracts": sorted([c.spec.root, c.year, c.month] for c in pack.contracts()),
        "roots": list(pack.roots),
        "dates": [d.isoformat() for d in pack.dates],
        "quote_count": n_quotes,
        "trade_count": n_trades,
        **pack.meta,
    }
    (path / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return read_pack(path)


def read_pack(path: Path) -> PackInfo:
    m = json.loads((path / MANIFEST).read_text(encoding="utf-8"))
    return PackInfo(
        path=path,
        version=int(m["version"]),
        seed=int(m["seed"]),
        digest=str(m["digest"]),
        label=str(m["label"]),
        symbols=list(m["symbols"]),
        contracts=[(str(r), int(y), int(mo)) for r, y, mo in m["contracts"]],
        roots=list(m["roots"]),
        dates=[date.fromisoformat(d) for d in m["dates"]],
        quote_count=int(m["quote_count"]),
        trade_count=int(m["trade_count"]),
    )


def ensure_pack(path: Path, seed: int = sample_data.DEFAULT_SEED) -> PackInfo:
    """Return the pack at ``path``, generating it first if it is missing, stale or modified."""
    expected = sample_data.generate(seed=seed)
    try:
        info = read_pack(path)
        if info.version == sample_data.PACK_VERSION and info.digest == expected.digest():
            return info
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
        pass
    return write_pack(expected, path)
