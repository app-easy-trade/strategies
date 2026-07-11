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
    median_pnl_usd: float
    pnl_p10_usd: float
    pnl_p90_usd: float
    best_trade_usd: float
    worst_trade_usd: float
    average_win_usd: float
    average_loss_usd: float
    payoff_ratio: float | None
    expectancy_usd: float
    max_consecutive_wins: int
    max_consecutive_losses: int
    average_holding_seconds: float
    median_holding_seconds: float


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
    average_win = (sum(wins) / len(wins)) if wins else 0.0
    average_loss = (sum(losses) / len(losses)) if losses else 0.0
    win_rate = (len(wins) / trades) if trades else 0.0
    loss_rate = (len(losses) / trades) if trades else 0.0
    return BacktestStats(
        trades=trades,
        wins=len(wins),
        losses=len(losses),
        win_rate_pct=win_rate * 100,
        gross_profit_usd=gross_profit,
        gross_loss_usd=gross_loss,
        net_profit_usd=sum(pnls),
        profit_factor=(gross_profit / gross_loss) if gross_loss else None,
        max_drawdown_usd=max_drawdown,
        average_pnl_usd=(sum(pnls) / trades) if trades else 0.0,
        median_pnl_usd=percentile(pnls, 50),
        pnl_p10_usd=percentile(pnls, 10),
        pnl_p90_usd=percentile(pnls, 90),
        best_trade_usd=max(pnls) if pnls else 0.0,
        worst_trade_usd=min(pnls) if pnls else 0.0,
        average_win_usd=average_win,
        average_loss_usd=average_loss,
        payoff_ratio=(average_win / abs(average_loss)) if average_loss else None,
        expectancy_usd=(win_rate * average_win) + (loss_rate * average_loss),
        max_consecutive_wins=max_consecutive(pnls, positive=True),
        max_consecutive_losses=max_consecutive(pnls, positive=False),
        average_holding_seconds=(sum(holding_seconds) / trades) if trades else 0.0,
        median_holding_seconds=percentile(holding_seconds, 50),
    )


def percentile(values: list[float], percentile_value: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * percentile_value / 100
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    weight = rank - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def max_consecutive(values: list[float], positive: bool) -> int:
    best = 0
    current = 0
    for value in values:
        matched = value > 0 if positive else value < 0
        if matched:
            current += 1
            best = max(best, current)
        else:
            current = 0
    return best

