from __future__ import annotations

from .config import StrategyConfig
from .execution_engine import evaluate_execution
from .funding_engine import estimate_funding
from .models import SpreadFeatures, TradeSignal
from .risk_engine import evaluate_risk


def evaluate_signal(features: SpreadFeatures, config: StrategyConfig) -> TradeSignal:
    snapshot = features.snapshot
    funding = estimate_funding(snapshot, config)
    risk = evaluate_risk(features, config)
    execution = evaluate_execution(features, config)
    costs = estimated_round_trip_cost_usd(config)
    executable_spread = snapshot.executable_spread_pct_for(config.position_notional_usd)
    gross_edge = executable_spread / 100 * config.position_notional_usd
    funding_edge = funding.expected_edge_usd
    net_expected = gross_edge + funding_edge - costs

    checks = [
        (snapshot.min_quote_volume_24h >= config.min_volume_usd, "volume_below_min"),
        (snapshot.min_quote_volume_24h >= config.liquidity_check_usd, "liquidity_below_min"),
        (risk.passed, risk.reason),
        (execution.passed, execution.reason),
        (passes_market_mode(snapshot.market_mode, config), "market_mode_not_allowed"),
        (snapshot.spread_pct > config.final_exit_spread_pct, "spread_below_final_exit"),
        (snapshot.spread_pct >= config.min_spread_pct, "spread_below_min"),
        (passes_max_spread(snapshot.spread_pct, config), "spread_above_max"),
        (passes_zscore(features, config), "zscore_below_min"),
        (passes_velocity(features, config), "velocity_below_min"),
        (passes_funding(funding.expected_edge_usd, funding.edge_rate, config), "funding_filter_failed"),
        (net_expected >= config.min_net_expected_profit_usd, "net_expected_profit_below_min"),
    ]
    for passed, reason in checks:
        if not passed:
            return TradeSignal(
                features=features,
                should_enter=False,
                reason=reason,
                net_expected_profit_usd=net_expected,
                gross_edge_usd=gross_edge,
                estimated_cost_usd=costs,
                funding_edge_usd=funding_edge,
            )

    return TradeSignal(
        features=features,
        should_enter=True,
        reason="enter",
        net_expected_profit_usd=net_expected,
        gross_edge_usd=gross_edge,
        estimated_cost_usd=costs,
        funding_edge_usd=funding_edge,
    )


def passes_zscore(features: SpreadFeatures, config: StrategyConfig) -> bool:
    if config.min_zscore <= 0:
        return True
    if features.zscore is None:
        return False
    return features.zscore >= config.min_zscore


def passes_velocity(features: SpreadFeatures, config: StrategyConfig) -> bool:
    if config.min_velocity_pct_per_minute <= 0:
        return True
    if features.velocity_pct_per_minute is None:
        return False
    return features.velocity_pct_per_minute >= config.min_velocity_pct_per_minute


def passes_market_mode(market_mode: str, config: StrategyConfig) -> bool:
    allowed = {item.strip() for item in config.allowed_market_modes.split(",") if item.strip()}
    return market_mode in allowed


def passes_max_spread(spread_pct: float, config: StrategyConfig) -> bool:
    if config.max_spread_pct <= 0:
        return True
    return spread_pct <= config.max_spread_pct


def passes_funding(
    funding_edge_usd: float,
    funding_edge_rate: float | None,
    config: StrategyConfig,
) -> bool:
    if config.require_known_funding and funding_edge_rate is None:
        return False
    return funding_edge_usd >= -abs(config.max_funding_against_usd)


def estimated_round_trip_cost_usd(config: StrategyConfig) -> float:
    fee_pct = 2 * (config.maker_fee_pct + config.taker_fee_pct)
    slippage_pct = 4 * config.slippage_pct
    return config.position_notional_usd * (fee_pct + slippage_pct) / 100
