from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StrategyConfig:
    min_spread_pct: float = 2.0
    max_spread_pct: float = 0.0
    min_volume_usd: float = 400_000.0
    liquidity_check_usd: float = 1_000.0
    max_slippage_pct: float = 0.2
    min_zscore: float = 1.0
    min_velocity_pct_per_minute: float = 0.0
    min_net_expected_profit_usd: float = 0.0
    require_known_funding: bool = False
    max_funding_against_usd: float = 0.0
    rolling_window: int = 20
    position_notional_usd: float = 200.0
    leverage: float = 5.0
    available_margin_usd: float = 200.0
    max_margin_usage_pct: float = 70.0
    required_adverse_buffer_pct: float = 5.0
    maker_fee_pct: float = 0.02
    taker_fee_pct: float = 0.055
    slippage_pct: float = 0.03
    limit_order_timeout_seconds: int = 5
    market_fallback_enabled: bool = True
    one_leg_timeout_seconds: int = 5
    tp1_close_fraction: float = 0.5
    tp1_retrace_fraction: float = 0.5
    final_exit_spread_pct: float = 0.5
    dynamic_exit_enabled: bool = True
    dynamic_exit_rebound_pct: float = 1.0
    dynamic_exit_min_profit_pct: float = 0.2
    max_adverse_spread_pct: float = 5.0
    max_holding_seconds: int = 86_400
    funding_interval_hours: float = 8.0
    funding_exit_enabled: bool = True
    funding_bad_exit_window_seconds: int = 900
    funding_good_hold_window_seconds: int = 900
    funding_good_hold_min_edge_usd: float = 0.0
    allowed_market_modes: str = "futures_futures,spot_futures"


def load_strategy_config(path: Path | None) -> StrategyConfig:
    if path is None:
        return StrategyConfig()
    values: dict[str, object] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, raw_value = [part.strip() for part in line.split(":", 1)]
        if not key:
            continue
        values[key] = parse_scalar(raw_value)
    allowed = set(StrategyConfig.__dataclass_fields__)
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValueError(f"Unknown config keys: {', '.join(unknown)}")
    return StrategyConfig(**values)


def parse_scalar(value: str) -> object:
    if value.lower() in {"true", "false"}:
        return value.lower() == "true"
    try:
        if any(char in value for char in ".eE"):
            return float(value)
        return int(value)
    except ValueError:
        return value.strip('"').strip("'")
