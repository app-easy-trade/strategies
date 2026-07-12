# Trading readiness

## Current status

The project is ready for research backtesting and live-data paper trading.

Ready:

- multi-exchange futures scanner;
- sanity filter for incompatible contracts;
- execution-aware history collection with order book depth;
- backtest with fees, slippage, funding events, TP1, dynamic exit, stop and max holding;
- executable spread guard before entry;
- portfolio margin model in backtest: reserved margin, adverse buffer,
  total account usage, max open trades and max total notional;
- detailed trade statistics and skipped-signal reasons;
- paper trading loop without real orders;
- Telegram daily reports and autonomous command listener;
- Telegram notifications for paper trade open/close.

## Allowed next step

The next safe step is read-only/testnet integration:

1. Keep collecting enriched history.
2. Run paper trading with Telegram notifications.
3. Add authenticated read-only checks for balances, margin and positions.
4. Add exchange testnet/demo order executor.
5. Test one-leg failure handling on testnet.

## Not ready for real money yet

Real trading should not be enabled until these are implemented and tested:

- authenticated read-only account checks for live balances, margin and positions;
- real/testnet order executor;
- order id tracking;
- partial fill handling;
- cancel/replace logic;
- one-leg emergency close;
- per-exchange min size, tick size and step size validation;
- kill switch;
- max daily loss and max open exposure;
- persistent live trade state recovery after restart.

## Practical rule

The current system can decide which paper trades should be opened. It should not
send real orders yet.
