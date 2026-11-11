"""Session gate, calendar blackout, and the 10:00 regime lock."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def bar_clock(bar_end: datetime, sessions: dict[str, Any]) -> datetime:
    tz = ZoneInfo(sessions.get("timezone", "America/New_York"))
    if bar_end.tzinfo is None:
        return bar_end.replace(tzinfo=tz)
    return bar_end.astimezone(tz)


def in_window(bar_end: datetime, start: str, end: str, sessions: dict[str, Any]) -> bool:
    local = bar_clock(bar_end, sessions)
    stamp = local.time()
    return _parse_hhmm(start) <= stamp < _parse_hhmm(end)


def calendar_blocks(bar_end: datetime, calendar: dict[str, Any], sessions: dict[str, Any]) -> bool:
    local = bar_clock(bar_end, sessions)
    for window in calendar.get("windows") or []:
        start = datetime.fromisoformat(window["start"])
        end = datetime.fromisoformat(window["end"])
        if start.tzinfo is None:
            start = start.replace(tzinfo=local.tzinfo)
        if end.tzinfo is None:
            end = end.replace(tzinfo=local.tzinfo)
        if start <= local < end:
            return True
    return False


def close_time(bar_end: datetime, sessions: dict[str, Any]) -> time:
    local = bar_clock(bar_end, sessions)
    early = set(sessions.get("early_close_dates") or [])
    if local.date().isoformat() in early:
        return _parse_hhmm(sessions.get("early_close_time", "13:00"))
    return _parse_hhmm(sessions.get("rth_close", "16:00"))


def session_gate(bar_end: datetime, regime: str, sessions: dict[str, Any], calendar: dict[str, Any]) -> str:
    """Return allow_new or block_new. Hold and reduce permits ignore this."""
    if calendar_blocks(bar_end, calendar, sessions):
        return "block_new"
    local = bar_clock(bar_end, sessions)
    stamp = local.time()
    close_at = close_time(bar_end, sessions)
    stop_delta_min = 30
    stop_total = close_at.hour * 60 + close_at.minute - stop_delta_min
    stop_at = time(stop_total // 60, stop_total % 60)
    if stamp >= stop_at:
        return "block_new"
    if stamp < _parse_hhmm(sessions.get("block_open_until", "10:00")):
        return "block_new"
    if _parse_hhmm(sessions.get("lunch_start", "11:30")) <= stamp < _parse_hhmm(sessions.get("lunch_end", "14:00")):
        return "block_new"
    if _parse_hhmm(sessions.get("lunch_end", "14:00")) <= stamp < stop_at and regime != "TREND_UP":
        return "block_new"
    return "allow_new"


def lock_regime(prev: str | None, bar_end: datetime, spy_above: bool | None, spy_vol_ratio: float | None, spy_ok: bool, sessions: dict[str, Any]) -> str:
    """Lock on the decision bar that ends at 10:00. Later flips need SPY through VWAP with volume."""
    if not spy_ok or spy_above is None:
        return "incomplete"
    local = bar_clock(bar_end, sessions)
    vol_ok = spy_vol_ratio is not None and spy_vol_ratio >= 1.2
    ten = _parse_hhmm("10:00")
    if local.time() < ten:
        return "RANGE"
    if local.time() == ten:
        return "TREND_UP" if spy_above and vol_ok else "RANGE"
    if vol_ok and spy_above:
        return "TREND_UP"
    if vol_ok and not spy_above:
        return "TREND_DOWN"
    return prev or "RANGE"
