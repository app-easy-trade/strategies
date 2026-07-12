from __future__ import annotations

from .config import StrategyConfig
from .models import RiskDecision, SpreadFeatures


def evaluate_risk(features: SpreadFeatures, config: StrategyConfig) -> RiskDecision:
    if config.leverage <= 0:
        return decision(False, "invalid_leverage", 0.0, 999.0, 0.0)

    margin_required = 2 * config.position_notional_usd / config.leverage
    adverse_buffer = config.position_notional_usd * config.required_adverse_buffer_pct / 100
    total_required = margin_required + adverse_buffer
    usage_pct = total_required / config.available_margin_usd * 100 if config.available_margin_usd > 0 else 999.0

    if total_required > config.available_margin_usd:
        return decision(False, "insufficient_margin_buffer", margin_required, usage_pct, adverse_buffer)
    if usage_pct > config.max_margin_usage_pct:
        return decision(False, "margin_usage_above_limit", margin_required, usage_pct, adverse_buffer)
    return decision(True, "risk_ok", margin_required, usage_pct, adverse_buffer)


def decision(
    passed: bool,
    reason: str,
    margin_required: float,
    usage_pct: float,
    adverse_buffer: float,
) -> RiskDecision:
    return RiskDecision(
        passed=passed,
        reason=reason,
        margin_required_usd=margin_required,
        margin_usage_pct=usage_pct,
        adverse_buffer_usd=adverse_buffer,
    )

