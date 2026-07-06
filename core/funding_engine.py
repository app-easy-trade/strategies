from __future__ import annotations

from .config import StrategyConfig
from .models import FundingEstimate, SpreadSnapshot


def estimate_funding(snapshot: SpreadSnapshot, config: StrategyConfig) -> FundingEstimate:
    if snapshot.long_funding_rate is None or snapshot.short_funding_rate is None:
        return FundingEstimate(
            long_rate=snapshot.long_funding_rate,
            short_rate=snapshot.short_funding_rate,
            edge_rate=None,
            expected_edge_usd=0.0,
            status="unknown",
        )

    edge_rate = snapshot.short_funding_rate - snapshot.long_funding_rate
    expected_edge_usd = edge_rate * config.position_notional_usd
    if edge_rate > 0:
        status = "favorable"
    elif edge_rate < 0:
        status = "against"
    else:
        status = "flat"
    return FundingEstimate(
        long_rate=snapshot.long_funding_rate,
        short_rate=snapshot.short_funding_rate,
        edge_rate=edge_rate,
        expected_edge_usd=expected_edge_usd,
        status=status,
    )

