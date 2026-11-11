"""SQLite bars. Seconds never land here."""

from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    symbol TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    bar_end TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL NOT NULL,
    vwap REAL NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (symbol, timeframe, bar_end)
);
"""


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def upsert_bar(self, symbol: str, timeframe: str, bar_end: str, ohlcv: tuple[float, float, float, float, float], vwap: float, source: str) -> None:
        self.conn.execute(
            """
            INSERT INTO bars(symbol, timeframe, bar_end, open, high, low, close, volume, vwap, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, timeframe, bar_end) DO UPDATE SET
              open=excluded.open, high=excluded.high, low=excluded.low,
              close=excluded.close, volume=excluded.volume, vwap=excluded.vwap, source=excluded.source
            """,
            (symbol, timeframe, bar_end, *ohlcv, vwap, source),
        )
        self.conn.commit()

    def bars(self, symbol: str, timeframe: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM bars WHERE symbol=? AND timeframe=? ORDER BY bar_end",
            (symbol, timeframe),
        ).fetchall()
        return [dict(row) for row in rows]
