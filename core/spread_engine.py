from __future__ import annotations

from collections import deque
from math import sqrt

from .config import StrategyConfig
from .models import SpreadFeatures, SpreadSnapshot


class SpreadFeatureEngine:
    def __init__(self, config: StrategyConfig) -> None:
        self.config = config
        self._spreads: dict[tuple[str, str, str, str, str], deque[float]] = {}
        self._previous: dict[tuple[str, str, str, str, str], SpreadSnapshot] = {}
        self._previous_velocity: dict[tuple[str, str, str, str, str], float] = {}

    def update(self, snapshot: SpreadSnapshot) -> SpreadFeatures:
        key = snapshot.key
        history = self._spreads.setdefault(key, deque(maxlen=self.config.rolling_window))
        mean = average(history) if history else None
        std = sample_std(history) if len(history) >= 2 else None
        zscore = None
        if mean is not None and std is not None and std > 0:
            zscore = (snapshot.spread_pct - mean) / std

        previous = self._previous.get(key)
        velocity = None
        acceleration = None
        if previous is not None:
            minutes = (snapshot.timestamp - previous.timestamp).total_seconds() / 60
            if minutes > 0:
                velocity = (snapshot.spread_pct - previous.spread_pct) / minutes
                previous_velocity = self._previous_velocity.get(key)
                if previous_velocity is not None:
                    acceleration = (velocity - previous_velocity) / minutes
                self._previous_velocity[key] = velocity

        history.append(snapshot.spread_pct)
        self._previous[key] = snapshot
        return SpreadFeatures(
            snapshot=snapshot,
            rolling_mean_pct=mean,
            rolling_std_pct=std,
            zscore=zscore,
            velocity_pct_per_minute=velocity,
            acceleration_pct_per_minute2=acceleration,
            category=spread_category(snapshot.spread_pct),
        )


def spread_category(spread_pct: float) -> str:
    if spread_pct < 1:
        return "below_range"
    if spread_pct < 2:
        return "stale"
    if spread_pct < 3:
        return "weak"
    if spread_pct < 5:
        return "medium"
    if spread_pct <= 10:
        return "active"
    return "extreme"


def average(values: deque[float]) -> float:
    return sum(values) / len(values)


def sample_std(values: deque[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = average(values)
    variance = sum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return sqrt(variance)

