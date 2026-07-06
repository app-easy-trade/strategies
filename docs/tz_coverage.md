# TZ Coverage Status

## Done

- Live spread scanner for perpetual markets: Bybit, Bitget, OKX, MEXC,
  Binance, Hyperliquid, Aster.
- Append-only spread history collector.
- CSV backtest runner.
- Core spread features: rolling mean, rolling std, z-score, velocity, acceleration.
- Spread categories: stale, weak, medium, active, extreme.
- Rule-based signal engine with explainable rejection reasons.
- Funding edge calculation when both funding rates are present.
- Binance USD-M futures connector.
- Hyperliquid perps connector.
- Aster V3 futures connector.
- Risk checks for leverage, available margin, margin usage and adverse spread buffer.
- Execution checks for liquidity threshold, limit timeout and market fallback mode.
- Partial TP1, final spread target, max adverse spread and max holding time.
- Trade CSV and skipped-signal CSV exports.
- Basic statistics: win rate, profit factor, drawdown, net PnL, holding time.
- Optimizer over spread ranges, TP1 retrace, adverse spread stops and leverage.
- Backtest quality report from trades/skipped CSV exports.
- Telegram daily control report with API status, top signals, funding summary,
  optional backtest report and explicit execution status.

## Partial

- Funding data coverage: Bybit, Bitget, MEXC, Binance, Aster and Hyperliquid
  return funding in scanner rows. OKX funding is enriched for the top liquid
  scanner rows because OKX requires per-instrument funding requests.
- Liquidity model: currently uses volume/liquidity thresholds. Full order book depth
  and price-impact simulation are not stored in history yet.
- Execution model: validates the policy and charges slippage, but does not yet replay
  order book events, partial fills or one-leg emergency close events.
- TradingView: planned as charting and visual analytics over exchange API data, not
  as the primary market data source.
- Backtest history: collector accumulates new history from launch time. It does not
  yet download deep historical candles/order books from exchanges.

## Not Done Yet

- Spot markets.
- Spot-to-perpetual mode.
- Full database storage with SQLite/PostgreSQL.
- Full optimizer over volume and funding settings.
- Paper/forward trading loop.
- Real trading execution.
- Authenticated balance/margin checks in Daily Control Bot.
- ML probability model for spread convergence.
- Full order-flow/order-book analytics.
