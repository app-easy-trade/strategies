from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from time import perf_counter
from typing import Any, Iterable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


JsonObject = dict[str, Any]
JsonPayload = JsonObject | list[Any]


class ExchangeAPIError(RuntimeError):
    """Raised when an exchange response cannot be used by the collector."""


@dataclass(frozen=True)
class Fees:
    maker: float | None = None
    taker: float | None = None


@dataclass(frozen=True)
class OrderBookLevel:
    price: float
    size: float


@dataclass(frozen=True)
class OrderBook:
    bids: list[OrderBookLevel]
    asks: list[OrderBookLevel]
    timestamp_ms: int | None = None


@dataclass(frozen=True)
class MarketSnapshot:
    exchange: str
    symbol: str
    bid: float | None
    ask: float | None
    last_price: float | None
    mark_price: float | None
    index_price: float | None
    funding_rate: float | None
    next_funding_time_ms: int | None
    open_interest: float | None
    volume_24h: float | None
    quote_volume_24h: float | None
    fees: Fees
    latency_ms: float
    timestamp_ms: int | None
    orderbook: OrderBook
    raw: JsonObject


def http_get_json(
    base_url: str,
    path: str,
    params: Mapping[str, Any] | None = None,
    timeout: float = 10.0,
    headers: Mapping[str, str] | None = None,
) -> tuple[JsonObject, float]:
    payload, latency_ms = http_get_json_any(base_url, path, params, timeout, headers)
    if not isinstance(payload, dict):
        raise ExchangeAPIError(f"GET {base_url.rstrip('/')}/{path.lstrip('/')} returned non-object JSON")
    return payload, latency_ms


def http_get_json_any(
    base_url: str,
    path: str,
    params: Mapping[str, Any] | None = None,
    timeout: float = 10.0,
    headers: Mapping[str, str] | None = None,
) -> tuple[JsonPayload, float]:
    query = urlencode(
        {key: value for key, value in (params or {}).items() if value is not None}
    )
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    if query:
        url = f"{url}?{query}"

    request_headers = {
        "Accept": "application/json",
        "User-Agent": "spread-arbitrage-input/0.1",
    }
    if headers:
        request_headers.update(headers)

    request = Request(url, headers=request_headers)
    started = perf_counter()
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise ExchangeAPIError(
            f"GET {url} failed with HTTP {exc.code}: {body[:400]}"
        ) from exc
    except URLError as exc:
        raise ExchangeAPIError(f"GET {url} failed: {exc.reason}") from exc

    latency_ms = (perf_counter() - started) * 1000
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ExchangeAPIError(f"GET {url} returned invalid JSON") from exc

    if not isinstance(payload, dict) and not isinstance(payload, list):
        raise ExchangeAPIError(f"GET {url} returned unsupported JSON payload")
    return payload, latency_ms


def http_post_json(
    base_url: str,
    path: str,
    payload: Mapping[str, Any] | None = None,
    timeout: float = 10.0,
    headers: Mapping[str, str] | None = None,
) -> tuple[JsonPayload, float]:
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    request_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "spread-arbitrage-input/0.1",
    }
    if headers:
        request_headers.update(headers)

    body = json.dumps(payload or {}).encode("utf-8")
    request = Request(url, data=body, headers=request_headers, method="POST")
    started = perf_counter()
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise ExchangeAPIError(
            f"POST {url} failed with HTTP {exc.code}: {error_body[:400]}"
        ) from exc
    except URLError as exc:
        raise ExchangeAPIError(f"POST {url} failed: {exc.reason}") from exc

    latency_ms = (perf_counter() - started) * 1000
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ExchangeAPIError(f"POST {url} returned invalid JSON") from exc

    if not isinstance(data, dict) and not isinstance(data, list):
        raise ExchangeAPIError(f"POST {url} returned unsupported JSON payload")
    return data, latency_ms


def ensure(condition: bool, message: str, payload: Any | None = None) -> None:
    if not condition:
        suffix = ""
        if payload is not None:
            suffix = f": {str(payload)[:400]}"
        raise ExchangeAPIError(f"{message}{suffix}")


def first(items: Sequence[Any] | None) -> Any | None:
    if not items:
        return None
    return items[0]


def to_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def orderbook_from_rows(
    bids: Iterable[Sequence[Any]] | None,
    asks: Iterable[Sequence[Any]] | None,
    timestamp_ms: int | None = None,
) -> OrderBook:
    return OrderBook(
        bids=_levels_from_rows(bids),
        asks=_levels_from_rows(asks),
        timestamp_ms=timestamp_ms,
    )


def snapshot_to_dict(snapshot: MarketSnapshot) -> JsonObject:
    return asdict(snapshot)


def print_snapshot(snapshot: MarketSnapshot) -> None:
    print(json.dumps(snapshot_to_dict(snapshot), ensure_ascii=False, indent=2))


def _levels_from_rows(rows: Iterable[Sequence[Any]] | None) -> list[OrderBookLevel]:
    levels: list[OrderBookLevel] = []
    if not rows:
        return levels

    for row in rows:
        if len(row) < 2:
            continue
        price = to_float(row[0])
        size = to_float(row[1])
        if price is None or size is None:
            continue
        levels.append(OrderBookLevel(price=price, size=size))
    return levels
