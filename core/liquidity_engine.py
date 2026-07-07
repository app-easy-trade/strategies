from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from brokers.common import OrderBook, OrderBookLevel


Side = Literal["buy", "sell"]


@dataclass(frozen=True)
class PriceImpact:
    side: Side
    notional_usd: float
    reference_price: float | None
    average_price: float | None
    impact_pct: float | None
    filled_notional_usd: float
    fillable: bool


def estimate_price_impact(
    orderbook: OrderBook,
    side: Side,
    notional_usd: float,
) -> PriceImpact:
    levels = orderbook.asks if side == "buy" else orderbook.bids
    reference = levels[0].price if levels else None
    if reference is None or notional_usd <= 0:
        return PriceImpact(side, notional_usd, reference, None, None, 0.0, False)

    remaining = notional_usd
    base_qty = 0.0
    spent = 0.0
    for level in levels:
        level_notional = level.price * level.size
        take_notional = min(remaining, level_notional)
        if take_notional <= 0:
            continue
        base_qty += take_notional / level.price
        spent += take_notional
        remaining -= take_notional
        if remaining <= 1e-9:
            break

    if base_qty <= 0:
        return PriceImpact(side, notional_usd, reference, None, None, spent, False)

    average_price = spent / base_qty
    if side == "buy":
        impact = (average_price / reference - 1) * 100
    else:
        impact = (reference / average_price - 1) * 100
    return PriceImpact(
        side=side,
        notional_usd=notional_usd,
        reference_price=reference,
        average_price=average_price,
        impact_pct=impact,
        filled_notional_usd=spent,
        fillable=remaining <= 1e-9,
    )


def estimate_book_impacts(orderbook: OrderBook, side: Side, notionals: list[float]) -> list[PriceImpact]:
    return [estimate_price_impact(orderbook, side, notional) for notional in notionals]

