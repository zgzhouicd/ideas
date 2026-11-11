"""Decision-bar features. The state machine consumes the same failure flags."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class Bar:
    bar_end: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append(value * k + out[-1] * (1 - k))
    return out


def rsi(closes: list[float], period: int) -> float | None:
    if len(closes) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for prev, cur in zip(closes, closes[1:]):
        delta = cur - prev
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def macd(closes: list[float], fast: int, slow: int, signal: int) -> tuple[float | None, float | None, float | None]:
    if len(closes) < slow + signal:
        return None, None, None
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    line = [a - b for a, b in zip(ema_fast, ema_slow)]
    sig = _ema(line, signal)
    hist = line[-1] - sig[-1]
    prev_hist = line[-2] - sig[-2] if len(line) > 1 else hist
    cross = None
    if (line[-2] - sig[-2]) <= 0 < (line[-1] - sig[-1]):
        cross = "bull"
    elif (line[-2] - sig[-2]) >= 0 > (line[-1] - sig[-1]):
        cross = "bear"
    return hist, prev_hist, cross


def bar_shape(bar: Bar, wick_reject: float) -> str:
    span = max(bar.high - bar.low, 1e-9)
    body = abs(bar.close - bar.open)
    upper = bar.high - max(bar.open, bar.close)
    lower = min(bar.open, bar.close) - bar.low
    if body / span < 0.2:
        return "doji"
    if body / span >= 0.55:
        return "trend"
    if upper / span >= wick_reject and bar.close < bar.open:
        return "rejection_up"
    if lower / span >= wick_reject and bar.close > bar.open:
        return "rejection_down"
    return "mixed"


def vwap_side(close: float, vwap: float, at_bps: float) -> str:
    if vwap == 0:
        return "at"
    bps = (close - vwap) / vwap * 10000
    if abs(bps) <= at_bps:
        return "at"
    return "above" if bps > 0 else "below"


def compute_features(history: list[Bar], spy: Bar | None, params: dict[str, Any]) -> dict[str, Any]:
    """history includes the current closed decision bar as the last item."""
    bar = history[-1]
    min_bars = int(params.get("min_bars", 35))
    at_bps = float(params.get("at_vwap_bps", 5))
    stretched = float(params.get("stretched_bps", 80))
    wick = float(params.get("wick_reject", 0.45))
    bps = ((bar.close - bar.vwap) / bar.vwap * 10000) if bar.vwap else 0.0
    side = vwap_side(bar.close, bar.vwap, at_bps)
    vols = [b.volume for b in history]
    vol_ratio = None
    if len(vols) > 1:
        base = vols[-21:-1] if len(vols) > 21 else vols[:-1]
        avg = sum(base) / len(base)
        vol_ratio = (bar.volume / avg) if avg else None
    recent = history[-3:]
    above_count = sum(1 for b in recent if b.close > b.vwap)
    ratios: list[float] = []
    for i, item in enumerate(history):
        prev = history[max(0, i - 20):i]
        if not prev:
            ratios.append(1.0)
            continue
        avg = sum(p.volume for p in prev) / len(prev)
        ratios.append(item.volume / avg if avg else 1.0)
    last3 = ratios[-3:]
    ups = sum(1 for a, b in zip(last3, last3[1:]) if b > a * 1.1)
    downs = sum(1 for a, b in zip(last3, last3[1:]) if b < a * 0.9)
    if ups >= 2:
        vol_seq = "expanding"
    elif downs >= 2:
        vol_seq = "contracting"
    else:
        vol_seq = "flat"
    momentum_ok = len(history) >= min_bars
    rsi_now = rsi([b.close for b in history], int(params.get("rsi_period", 14))) if momentum_ok else None
    rsi_prev = rsi([b.close for b in history[:-1]], int(params.get("rsi_period", 14))) if momentum_ok and len(history) > min_bars else None
    hist, prev_hist, cross = (None, None, None)
    if momentum_ok:
        hist, prev_hist, cross = macd(
            [b.close for b in history],
            int(params.get("macd_fast", 12)),
            int(params.get("macd_slow", 26)),
            int(params.get("macd_signal", 9)),
        )
    zone = "neutral"
    if rsi_now is not None:
        if rsi_now < float(params.get("rsi_oversold", 30)):
            zone = "oversold"
        elif rsi_now > float(params.get("rsi_overbought", 70)):
            zone = "overbought"
    slope = "flat"
    if rsi_now is not None and rsi_prev is not None:
        eps = float(params.get("rsi_slope_eps", 1))
        if rsi_now - rsi_prev > eps:
            slope = "rising"
        elif rsi_now - rsi_prev < -eps:
            slope = "falling"
    bearish_flip = bool(
        cross == "bear"
        or (rsi_prev is not None and rsi_now is not None and rsi_prev > 70 and rsi_now <= 70)
        or bar_shape(bar, wick) == "rejection_up"
    )
    bullish_flip = bool(
        cross == "bull"
        or (rsi_prev is not None and rsi_now is not None and rsi_prev < 30 and rsi_now >= 30)
        or bar_shape(bar, wick) == "rejection_down"
    )
    divergence = _divergence(history, params) if momentum_ok else "none"
    session_high = max(b.high for b in history)
    session_low = min(b.low for b in history)
    span = session_high - session_low
    if span <= 0:
        session_pos = "mid"
    else:
        pos = (bar.close - session_low) / span
        session_pos = "low" if pos < 0.33 else "high" if pos > 0.67 else "mid"
    spy_side = vwap_side(spy.close, spy.vwap, at_bps) if spy else None
    conflicts: list[str] = []
    if abs(bps) > stretched:
        conflicts.append("stretched")
    if bearish_flip and side in ("above", "at"):
        conflicts.append("bearish_flip")
    if divergence == "bearish":
        conflicts.append("bearish_divergence")
    if vol_seq == "contracting":
        conflicts.append("vol_contracting")
    if divergence == "ambiguous":
        conflicts.append("divergence_ambiguous")
    if not momentum_ok or rsi_now is None or hist is None:
        conflicts.append("momentum_unavailable")
    return {
        "vwap_bps": round(bps, 2),
        "vwap_side": side,
        "extension": "stretched" if abs(bps) > stretched else "normal",
        "session_pos": session_pos,
        "bar_shape": bar_shape(bar, wick),
        "above_vwap_count": above_count,
        "vol_seq": vol_seq,
        "vol_ratio": None if vol_ratio is None else round(vol_ratio, 4),
        "rsi_14": None if rsi_now is None else round(rsi_now, 2),
        "rsi_zone": zone if rsi_now is not None else None,
        "rsi_slope": slope if rsi_now is not None else None,
        "macd_hist": None if hist is None else round(hist, 6),
        "macd_hist_side": None if hist is None else ("pos" if hist > 0 else "neg"),
        "macd_hist_slope": None if hist is None or prev_hist is None else ("rising" if hist > prev_hist else "falling"),
        "macd_cross": cross or "none",
        "bearish_flip": bearish_flip,
        "bullish_flip": bullish_flip,
        "divergence": divergence,
        "spy_vwap_side": spy_side,
        "close_above_vwap": bar.close > bar.vwap,
        "conflicts": conflicts,
        "momentum_ok": "momentum_unavailable" not in conflicts,
    }


def _divergence(history: list[Bar], params: dict[str, Any]) -> str:
    lookback = int(params.get("divergence_lookback", 10))
    gap = float(params.get("divergence_rsi_gap", 5))
    window = history[-lookback:]
    if len(window) < 3:
        return "none"
    closes = [b.close for b in history]
    period = int(params.get("rsi_period", 14))
    rsis = []
    for i in range(len(history)):
        rsis.append(rsi(closes[: i + 1], period))
    cur = window[-1]
    cur_rsi = rsis[-1]
    if cur_rsi is None:
        return "none"
    bearish = False
    bullish = False
    hi = max(b.high for b in window)
    lo = min(b.low for b in window)
    if cur.high == hi:
        for offset, older in enumerate(window[:-1]):
            older_rsi = rsis[len(history) - len(window) + offset]
            if older_rsi is None:
                continue
            if older.high >= cur.high * 0.998 and older_rsi >= cur_rsi + gap:
                bearish = True
                break
    if cur.low == lo:
        for offset, older in enumerate(window[:-1]):
            older_rsi = rsis[len(history) - len(window) + offset]
            if older_rsi is None:
                continue
            if older.low <= cur.low * 1.002 and older_rsi <= cur_rsi - gap:
                bullish = True
                break
    if bearish and bullish:
        return "ambiguous"
    if bearish:
        return "bearish"
    if bullish:
        return "bullish"
    return "none"


def apply_environment(features: dict[str, Any], regime: str, change_bps: float | None, vs_spy_bps: float) -> list[str]:
    conflicts = list(features["conflicts"])
    if regime == "TREND_UP" and features.get("spy_vwap_side") == "below":
        conflicts.append("spy_against")
    if change_bps is not None and change_bps < -vs_spy_bps:
        conflicts.append("lagging")
    return conflicts
