from __future__ import annotations

from .config import StrategyConfig
from .models import ExecutionDecision, SpreadFeatures


def evaluate_execution(features: SpreadFeatures, config: StrategyConfig) -> ExecutionDecision:
    snapshot = features.snapshot
    estimated_slippage = 4 * config.position_notional_usd * config.slippage_pct / 100

    if config.slippage_pct > config.max_slippage_pct:
        return ExecutionDecision(
            passed=False,
            reason="slippage_above_max",
            entry_mode="none",
            estimated_slippage_usd=estimated_slippage,
            one_leg_timeout_seconds=config.one_leg_timeout_seconds,
        )
    if snapshot.min_quote_volume_24h < config.liquidity_check_usd:
        return ExecutionDecision(
            passed=False,
            reason="liquidity_below_execution_min",
            entry_mode="none",
            estimated_slippage_usd=estimated_slippage,
            one_leg_timeout_seconds=config.one_leg_timeout_seconds,
        )
    if config.limit_order_timeout_seconds <= 0 and not config.market_fallback_enabled:
        return ExecutionDecision(
            passed=False,
            reason="no_executable_entry_mode",
            entry_mode="none",
            estimated_slippage_usd=estimated_slippage,
            one_leg_timeout_seconds=config.one_leg_timeout_seconds,
        )

    entry_mode = "limit_then_market" if config.market_fallback_enabled else "limit_only"
    return ExecutionDecision(
        passed=True,
        reason="execution_ok",
        entry_mode=entry_mode,
        estimated_slippage_usd=estimated_slippage,
        one_leg_timeout_seconds=config.one_leg_timeout_seconds,
    )

