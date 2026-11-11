"""In-memory second buckets. Nothing here opens a socket."""

from __future__ import annotations

from datetime import datetime, timedelta


class SecondBucket:
    def __init__(self, incomplete_seconds: int = 5):
        self.incomplete_seconds = incomplete_seconds
        self.ticks: dict[datetime, tuple[float, float]] = {}

    def add(self, stamp: datetime, price: float, size: float) -> None:
        self.ticks[stamp.replace(microsecond=0)] = (price, size)

    def seal(self, start: datetime, end: datetime) -> dict:
        expected = []
        cursor = start
        while cursor < end:
            expected.append(cursor)
            cursor += timedelta(seconds=1)
        present = [stamp for stamp in expected if stamp in self.ticks]
        missing = len(expected) - len(present)
        incomplete = missing > self.incomplete_seconds or not present
        if incomplete:
            return {"bar_end": end.isoformat(), "incomplete": True, "missing_seconds": missing}
        prices = [self.ticks[stamp][0] for stamp in present]
        volume = sum(self.ticks[stamp][1] for stamp in present)
        return {
            "bar_end": end.isoformat(),
            "incomplete": False,
            "missing_seconds": missing,
            "open": prices[0],
            "high": max(prices),
            "low": min(prices),
            "close": prices[-1],
            "volume": volume,
        }
