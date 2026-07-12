from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any
from urllib.parse import urlencode

from .common import ExchangeAPIError, hmac_sha256, http_get_json_any, to_float


@dataclass(frozen=True)
class AccountSnapshot:
    exchange: str
    account_type: str
    equity_usd: float | None
    available_balance_usd: float | None
    wallet_balance_usd: float | None
    margin_balance_usd: float | None
    initial_margin_usd: float | None
    maintenance_margin_usd: float | None
    unrealized_pnl_usd: float | None
    margin_usage_pct: float | None
    positions: list["PositionSnapshot"]


@dataclass(frozen=True)
class PositionSnapshot:
    exchange: str
    symbol: str
    side: str
    size: float
    entry_price: float | None
    mark_price: float | None
    notional_usd: float | None
    leverage: float | None
    unrealized_pnl_usd: float | None
    liquidation_price: float | None


def fetch_account_snapshot(
    exchange: str,
    api_key: str,
    api_secret: str,
    passphrase: str | None = None,
    testnet: bool = False,
    timeout: float = 10.0,
) -> AccountSnapshot:
    name = exchange.lower().strip()
    if name == "bybit":
        return fetch_bybit_account(api_key, api_secret, testnet=testnet, timeout=timeout)
    if name == "binance":
        return fetch_binance_account(api_key, api_secret, testnet=testnet, timeout=timeout)
    raise ExchangeAPIError(f"Read-only account check is not implemented for {exchange}")


def fetch_bybit_account(
    api_key: str,
    api_secret: str,
    testnet: bool = False,
    timeout: float = 10.0,
) -> AccountSnapshot:
    base_url = "https://api-testnet.bybit.com" if testnet else "https://api.bybit.com"
    wallet = bybit_signed_get(
        base_url,
        "/v5/account/wallet-balance",
        {"accountType": "UNIFIED"},
        api_key,
        api_secret,
        timeout,
    )
    positions_payload = bybit_signed_get(
        base_url,
        "/v5/position/list",
        {"category": "linear", "settleCoin": "USDT"},
        api_key,
        api_secret,
        timeout,
    )
    wallet_result = first_dict(wallet.get("result", {}).get("list"))
    coin = first_dict(wallet_result.get("coin") if wallet_result else None)
    positions = [
        bybit_position(item)
        for item in positions_payload.get("result", {}).get("list", [])
        if isinstance(item, dict) and abs(to_float(item.get("size")) or 0.0) > 0
    ]
    equity = to_float(wallet_result.get("totalEquity")) if wallet_result else None
    initial_margin = to_float(wallet_result.get("totalInitialMargin")) if wallet_result else None
    return AccountSnapshot(
        exchange="bybit",
        account_type="UNIFIED",
        equity_usd=equity,
        available_balance_usd=to_float(wallet_result.get("totalAvailableBalance")) if wallet_result else None,
        wallet_balance_usd=to_float(coin.get("walletBalance")) if coin else None,
        margin_balance_usd=to_float(wallet_result.get("totalMarginBalance")) if wallet_result else None,
        initial_margin_usd=initial_margin,
        maintenance_margin_usd=to_float(wallet_result.get("totalMaintenanceMargin")) if wallet_result else None,
        unrealized_pnl_usd=to_float(wallet_result.get("totalPerpUPL")) if wallet_result else None,
        margin_usage_pct=usage_pct(initial_margin, equity),
        positions=positions,
    )


def fetch_binance_account(
    api_key: str,
    api_secret: str,
    testnet: bool = False,
    timeout: float = 10.0,
) -> AccountSnapshot:
    base_url = "https://testnet.binancefuture.com" if testnet else "https://fapi.binance.com"
    account = binance_signed_get(base_url, "/fapi/v2/account", {}, api_key, api_secret, timeout)
    positions = [
        binance_position(item)
        for item in account.get("positions", [])
        if isinstance(item, dict) and abs(to_float(item.get("positionAmt")) or 0.0) > 0
    ]
    wallet = to_float(account.get("totalWalletBalance"))
    margin = to_float(account.get("totalMarginBalance"))
    initial_margin = to_float(account.get("totalInitialMargin"))
    equity = margin or wallet
    return AccountSnapshot(
        exchange="binance",
        account_type="USD-M futures",
        equity_usd=equity,
        available_balance_usd=to_float(account.get("availableBalance")),
        wallet_balance_usd=wallet,
        margin_balance_usd=margin,
        initial_margin_usd=initial_margin,
        maintenance_margin_usd=to_float(account.get("totalMaintMargin")),
        unrealized_pnl_usd=to_float(account.get("totalUnrealizedProfit")),
        margin_usage_pct=usage_pct(initial_margin, equity),
        positions=positions,
    )


def bybit_signed_get(
    base_url: str,
    path: str,
    params: dict[str, Any],
    api_key: str,
    api_secret: str,
    timeout: float,
) -> dict[str, Any]:
    timestamp = str(int(time.time() * 1000))
    recv_window = "5000"
    query = urlencode({key: value for key, value in params.items() if value is not None})
    signature = hmac_sha256(api_secret, timestamp + api_key + recv_window + query)
    payload, _ = http_get_json_any(
        base_url,
        path,
        params,
        timeout=timeout,
        headers={
            "X-BAPI-API-KEY": api_key,
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": recv_window,
            "X-BAPI-SIGN": signature,
        },
    )
    if not isinstance(payload, dict) or payload.get("retCode") != 0:
        raise ExchangeAPIError(f"Bybit account request failed: {str(payload)[:400]}")
    return payload


def binance_signed_get(
    base_url: str,
    path: str,
    params: dict[str, Any],
    api_key: str,
    api_secret: str,
    timeout: float,
) -> dict[str, Any]:
    signed_params = dict(params)
    signed_params["timestamp"] = int(time.time() * 1000)
    query = urlencode(signed_params)
    signed_params["signature"] = hmac_sha256(api_secret, query)
    payload, _ = http_get_json_any(
        base_url,
        path,
        signed_params,
        timeout=timeout,
        headers={"X-MBX-APIKEY": api_key},
    )
    if not isinstance(payload, dict):
        raise ExchangeAPIError(f"Binance account request returned non-object: {str(payload)[:400]}")
    return payload


def bybit_position(item: dict[str, Any]) -> PositionSnapshot:
    return PositionSnapshot(
        exchange="bybit",
        symbol=str(item.get("symbol") or ""),
        side=str(item.get("side") or ""),
        size=to_float(item.get("size")) or 0.0,
        entry_price=to_float(item.get("avgPrice")),
        mark_price=to_float(item.get("markPrice")),
        notional_usd=to_float(item.get("positionValue")),
        leverage=to_float(item.get("leverage")),
        unrealized_pnl_usd=to_float(item.get("unrealisedPnl")),
        liquidation_price=to_float(item.get("liqPrice")),
    )


def binance_position(item: dict[str, Any]) -> PositionSnapshot:
    size = to_float(item.get("positionAmt")) or 0.0
    return PositionSnapshot(
        exchange="binance",
        symbol=str(item.get("symbol") or ""),
        side="Buy" if size > 0 else "Sell",
        size=abs(size),
        entry_price=to_float(item.get("entryPrice")),
        mark_price=to_float(item.get("markPrice")),
        notional_usd=abs(to_float(item.get("notional")) or 0.0),
        leverage=to_float(item.get("leverage")),
        unrealized_pnl_usd=to_float(item.get("unrealizedProfit")),
        liquidation_price=to_float(item.get("liquidationPrice")),
    )


def first_dict(value: Any) -> dict[str, Any] | None:
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return None


def usage_pct(used: float | None, equity: float | None) -> float | None:
    if used is None or equity is None or equity <= 0:
        return None
    return used / equity * 100
