from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SpreadSnapshot:
    timestamp: datetime
    base: str
    pair: str
    long_exchange: str
    short_exchange: str
    long_symbol: str
    short_symbol: str
    long_ask: float
    short_bid: float
    spread_pct: float
    min_quote_volume_24h: float
    long_funding_rate: float | None = None
    short_funding_rate: float | None = None
    market_mode: str = "futures_futures"
    executable_spread_100_pct: float | None = None
    executable_spread_200_pct: float | None = None
    executable_spread_500_pct: float | None = None
    executable_spread_1000_pct: float | None = None
    long_depth_usd_100: float | None = None
    short_depth_usd_100: float | None = None
    long_depth_usd_200: float | None = None
    short_depth_usd_200: float | None = None
    long_depth_usd_1000: float | None = None
    short_depth_usd_1000: float | None = None

    @property
    def key(self) -> tuple[str, str, str, str, str]:
        return (
            self.base,
            self.long_exchange,
            self.short_exchange,
            self.long_symbol,
            self.short_symbol,
        )

    def executable_spread_pct_for(self, notional_usd: float) -> float:
        if notional_usd <= 100 and self.executable_spread_100_pct is not None:
            return self.executable_spread_100_pct
        if notional_usd <= 200 and self.executable_spread_200_pct is not None:
            return self.executable_spread_200_pct
        if notional_usd <= 500 and self.executable_spread_500_pct is not None:
            return self.executable_spread_500_pct
        if self.executable_spread_1000_pct is not None:
            return self.executable_spread_1000_pct
        return self.spread_pct


@dataclass(frozen=True)
class SpreadFeatures:
    snapshot: SpreadSnapshot
    rolling_mean_pct: float | None
    rolling_std_pct: float | None
    zscore: float | None
    velocity_pct_per_minute: float | None
    acceleration_pct_per_minute2: float | None
    category: str


@dataclass(frozen=True)
class TradeSignal:
    features: SpreadFeatures
    should_enter: bool
    reason: str
    net_expected_profit_usd: float
    gross_edge_usd: float
    estimated_cost_usd: float
    funding_edge_usd: float


@dataclass(frozen=True)
class FundingEstimate:
    long_rate: float | None
    short_rate: float | None
    edge_rate: float | None
    expected_edge_usd: float
    status: str


@dataclass(frozen=True)
class RiskDecision:
    passed: bool
    reason: str
    margin_required_usd: float
    margin_usage_pct: float
    adverse_buffer_usd: float


@dataclass(frozen=True)
class ExecutionDecision:
    passed: bool
    reason: str
    entry_mode: str
    estimated_slippage_usd: float
    one_leg_timeout_seconds: int
