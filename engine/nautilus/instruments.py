"""Build NautilusTrader ``FuturesContract`` instruments from our contract definitions."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Final

from nautilus_trader.model.currencies import USD
from nautilus_trader.model.enums import AssetClass as NAssetClass
from nautilus_trader.model.identifiers import InstrumentId, Symbol, Venue
from nautilus_trader.model.instruments import FuturesContract
from nautilus_trader.model.objects import Price, Quantity

from engine.calendar import to_unix_ns
from engine.instruments import AssetClass, Contract

#: Databento's CME Globex dataset venue; all CME Group contracts trade on Globex.
GLOBEX: Final = Venue("GLBX")

_ASSET_CLASS: Final = {
    AssetClass.EQUITY_INDEX: NAssetClass.INDEX,
    AssetClass.ENERGY: NAssetClass.COMMODITY,
    AssetClass.METALS: NAssetClass.COMMODITY,
    AssetClass.FX: NAssetClass.FX,
    AssetClass.RATES: NAssetClass.DEBT,
    AssetClass.CRYPTO: NAssetClass.CRYPTOCURRENCY,
}


def instrument_id(c: Contract, venue: Venue = GLOBEX) -> InstrumentId:
    return InstrumentId(Symbol(c.symbol), venue)


def margin_ratio(c: Contract) -> Decimal:
    """Estimated initial margin as a fraction of notional at the reference price (simulation)."""
    notional = c.spec.reference_price * c.spec.multiplier
    return (c.spec.est_initial_margin / notional).quantize(Decimal("0.0001"))


def to_nautilus(c: Contract, venue: Venue = GLOBEX) -> FuturesContract:
    spec = c.spec
    expiry: datetime = c.expiration
    activation = expiry - timedelta(days=400)
    ratio = margin_ratio(c)
    return FuturesContract(
        instrument_id=instrument_id(c, venue),
        raw_symbol=Symbol(c.symbol),
        asset_class=_ASSET_CLASS[spec.asset_class],
        exchange=spec.exchange,
        currency=USD,
        price_precision=spec.price_precision,
        price_increment=Price(spec.tick_size, spec.price_precision),
        multiplier=Quantity.from_str(str(spec.multiplier)),
        lot_size=Quantity.from_int(1),
        underlying=spec.root,
        activation_ns=to_unix_ns(activation),
        expiration_ns=to_unix_ns(expiry),
        ts_event=to_unix_ns(activation),
        ts_init=to_unix_ns(activation),
        margin_init=ratio,
        margin_maint=ratio,
    )
