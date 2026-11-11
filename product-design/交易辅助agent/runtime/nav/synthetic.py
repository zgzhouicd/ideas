"""Synthetic RTH day used by the runnable loop and the replay check."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from nav.features import Bar

ET = ZoneInfo("America/New_York")


def _minute_ends(day: datetime) -> list[datetime]:
    cursor = day.replace(hour=9, minute=31, second=0, microsecond=0)
    end = day.replace(hour=16, minute=0, second=0, microsecond=0)
    stamps = []
    while cursor <= end:
        stamps.append(cursor)
        cursor += timedelta(minutes=1)
    return stamps


def _ohlc(price: float, volume: float, end: datetime) -> dict:
    return {
        "symbol": "",
        "bar_end": end.isoformat(),
        "open": round(price - 0.04, 4),
        "high": round(price + 0.05, 4),
        "low": round(price - 0.06, 4),
        "close": round(price, 4),
        "volume": volume,
        "vwap": round(price, 4),
        "timeframe": "1m",
        "source": "synthetic",
    }


def synthetic_minutes(day: datetime, symbols: list[str]) -> list[dict]:
    rows = []
    for end in _minute_ends(day):
        elapsed = int((end - day.replace(hour=9, minute=30, second=0, microsecond=0)).total_seconds() // 60)
        spike = elapsed in range(58, 64)
        for symbol in [*symbols, "SPY"]:
            price = 100.25 if symbol == "NVDA" and spike else 100.0
            volume = 4000 if symbol == "NVDA" and elapsed in range(61, 64) else 1000
            row = _ohlc(price, volume, end)
            row["symbol"] = symbol
            rows.append(row)
    return rows


def warmup_bars(day: datetime, symbols: list[str], count: int = 40) -> list[dict]:
    rows = []
    end = (day - timedelta(days=1)).replace(hour=16, minute=0, second=0, microsecond=0)
    for offset in range(count, 0, -1):
        stamp = end - timedelta(minutes=3 * offset)
        for symbol in [*symbols, "SPY"]:
            rows.append({
                "symbol": symbol,
                "bar_end": stamp.isoformat(),
                "open": 100.0,
                "high": 100.1,
                "low": 99.9,
                "close": 100.0,
                "volume": 3000,
                "vwap": 100.0,
                "timeframe": "decision",
                "source": "synthetic",
            })
    return rows


def warmup_as_bars(rows: list[dict]) -> dict[str, list[Bar]]:
    grouped: dict[str, list[Bar]] = {}
    for row in rows:
        if row.get("timeframe") != "decision":
            continue
        grouped.setdefault(row["symbol"], []).append(
            Bar(row["bar_end"], row["open"], row["high"], row["low"], row["close"], row["volume"], row["vwap"])
        )
    return grouped
