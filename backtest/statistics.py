from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BacktestStats:
    trades: int
    wins: int
    losses: int
    win_rate_pct: float
    gross_profit_usd: float
    gross_loss_usd: float
    net_profit_usd: float
    profit_factor: float | None
    max_drawdown_usd: float
    average_pnl_usd: float
    average_holding_seconds: float


def calculate_stats(pnls: list[float], holding_seconds: list[float]) -> BacktestStats:
    wins = [value for value in pnls if value > 0]
    losses = [value for value in pnls if value < 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0

    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)

    trades = len(pnls)
    return BacktestStats(
        trades=trades,
        wins=len(wins),
        losses=len(losses),
        win_rate_pct=(len(wins) / trades * 100) if trades else 0.0,
        gross_profit_usd=gross_profit,
        gross_loss_usd=gross_loss,
        net_profit_usd=sum(pnls),
        profit_factor=(gross_profit / gross_loss) if gross_loss else None,
        max_drawdown_usd=max_drawdown,
        average_pnl_usd=(sum(pnls) / trades) if trades else 0.0,
        average_holding_seconds=(sum(holding_seconds) / trades) if trades else 0.0,
    )

