"""One decision pass over a closed bar, shared by live and replay."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from nav.config import AppConfig, Rules, load_positions
from nav.features import Bar, apply_environment, compute_features
from nav.journal import Journal, decision_rows
from nav.session import lock_regime, session_gate
from nav.state import Book, Decision, Snap, apply_snap, copy_book, resolve_cluster
from nav.text import accept_model_text, render_card


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def decision_ends(minute_ends: list[datetime], open_at: datetime, minutes: int) -> list[datetime]:
    ends = []
    for stamp in minute_ends:
        elapsed = int((stamp - open_at).total_seconds() // 60)
        if elapsed > 0 and elapsed % minutes == 0:
            ends.append(stamp)
    return ends


def aggregate(minute_bars: list[dict], end: datetime, minutes: int) -> dict | None:
    start = end - timedelta(minutes=minutes)
    chosen = [bar for bar in minute_bars if start < parse_time(bar["bar_end"]) <= end]
    if len(chosen) != minutes or any(bar["volume"] <= 0 for bar in chosen):
        return None
    return {
        "bar_end": end.isoformat(),
        "open": chosen[0]["open"],
        "high": max(bar["high"] for bar in chosen),
        "low": min(bar["low"] for bar in chosen),
        "close": chosen[-1]["close"],
        "volume": sum(bar["volume"] for bar in chosen),
    }


def session_vwap(minute_bars: list[dict], end: datetime, open_at: datetime) -> float:
    chosen = [bar for bar in minute_bars if open_at < parse_time(bar["bar_end"]) <= end]
    num = 0.0
    den = 0.0
    for bar in chosen:
        typical = (bar["high"] + bar["low"] + bar["close"]) / 3
        num += typical * bar["volume"]
        den += bar["volume"]
    if den == 0:
        return chosen[-1]["close"] if chosen else 0.0
    return num / den


class Loop:
    def __init__(self, config: AppConfig, journal: Journal, model_text=None):
        self.config = config
        self.journal = journal
        self.model_text = model_text
        self.books: dict[str, Book] = {}
        self.opens_today = 0
        self.regime = "RANGE"

    def restore(self) -> None:
        latest: dict[str, dict] = {}
        for row in decision_rows(self.journal.inbox):
            if row.get("quarantine"):
                continue
            latest[row["symbol"]] = row
        for symbol, row in latest.items():
            self.books[symbol] = Book(**row["book"])
        self.opens_today = sum(book.open_permits for book in self.books.values())

    def run_decision_bar(
        self,
        bar_end: datetime,
        packed: dict[str, dict | None],
        histories: dict[str, list[Bar]],
        spy_hist: list[Bar],
        positions: dict[str, bool],
        cards: list[dict],
        spy_ok: bool,
    ) -> list[Decision]:
        rules = self.config.rules
        spy_bar = spy_hist[-1] if spy_hist else None
        spy_above = None if spy_bar is None else spy_bar.close > spy_bar.vwap
        spy_vol = None
        if len(spy_hist) > 1 and spy_bar is not None:
            base = spy_hist[-21:-1] if len(spy_hist) > 21 else spy_hist[:-1]
            avg = sum(bar.volume for bar in base) / len(base)
            spy_vol = spy_bar.volume / avg if avg else None
        self.regime = lock_regime(self.regime, bar_end, spy_above, spy_vol, spy_ok and spy_bar is not None, self.config.sessions)
        gate = session_gate(bar_end, self.regime, self.config.sessions, self.config.calendar)
        if self.regime == "incomplete":
            gate = "block_new"
        snaps: list[Snap] = []
        symbols = sorted(packed)
        for symbol in symbols:
            payload = packed[symbol]
            quarantine = payload is None
            bar = histories[symbol][-1] if histories[symbol] else None
            if quarantine or bar is None:
                snap = Snap(
                    symbol=symbol, bar_end=bar_end, close=0, high=0, low=0, vwap=0, volume=0,
                    vol_ratio=None, close_above_vwap=False, quarantine=True,
                    position_marked=positions.get(symbol, False), session_gate=gate, regime=self.regime,
                )
            else:
                feats = compute_features(histories[symbol], spy_bar, rules.features)
                change = None
                if spy_bar is not None and histories[symbol]:
                    stock_open = histories[symbol][0].open
                    spy_open = spy_hist[0].open
                    if stock_open and spy_open:
                        change = ((bar.close / stock_open) - (spy_bar.close / spy_open)) * 10000
                conflicts = apply_environment(feats, self.regime, change, float(rules.features.get("vs_spy_bps", 30)))
                active = [
                    card["id"] for card in cards
                    if card["symbol"] == symbol and card.get("effect") == "suppress_session" and card["id"]
                    and (card.get("expires") is None or bar_end.isoformat() < card["expires"])
                ]
                snap = Snap(
                    symbol=symbol,
                    bar_end=bar_end,
                    close=bar.close,
                    high=bar.high,
                    low=bar.low,
                    vwap=bar.vwap,
                    volume=bar.volume,
                    vol_ratio=feats["vol_ratio"],
                    close_above_vwap=bar.close > bar.vwap,
                    conflicts=conflicts,
                    momentum_ok=feats["momentum_ok"],
                    position_marked=positions.get(symbol, False),
                    session_gate=gate,
                    regime=self.regime,
                    cluster_id=_cluster(self.config.watchlist, symbol),
                    correction_ids=active[:3],
                    features=feats,
                )
            snaps.append(snap)
        prior = {snap.symbol: copy_book(self.books.setdefault(snap.symbol, Book(snap.symbol))) for snap in snaps}
        paired = []
        cap_hit = self.opens_today >= rules.max_new_open_per_day
        for snap in snaps:
            paired.append((snap, apply_snap(self.books[snap.symbol], snap, rules, daily_cap_hit=cap_hit)))
        resolved = resolve_cluster(paired, prior)
        self.opens_today = sum(book.open_permits for book in self.books.values())
        for snap, decision in zip(snaps, resolved):
            raw = self.model_text(decision, snap) if self.model_text and not decision.quarantine else None
            text = accept_model_text(raw, decision, snap, rules) if self.model_text else render_card(decision, snap, rules)
            self.journal.write_decision(decision, snap, rules, text, rules.version)
        return resolved


def _cluster(watchlist: list[dict], symbol: str) -> str | None:
    for item in watchlist:
        if item["symbol"] == symbol:
            return item.get("cluster")
    return None


def run_minutes(
    config: AppConfig,
    minute_bars: dict[str, list[dict]],
    journal: Journal,
    positions: dict[str, bool] | None = None,
    cards: list[dict] | None = None,
    prior_decision: dict[str, list[Bar]] | None = None,
    model_text=None,
) -> Loop:
    """Aggregate 1-minute bars into decision bars and run the shared loop."""
    minutes = int(config.clocks["decision_minutes"])
    positions = positions if positions is not None else load_positions(config.paths["positions"])
    cards = cards or []
    symbols = [item["symbol"] for item in config.watchlist]
    sample_day = parse_time(next(iter(minute_bars.values()))[0]["bar_end"])
    open_at = sample_day.replace(hour=9, minute=30, second=0, microsecond=0)
    loop = Loop(config, journal, model_text=model_text)
    histories: dict[str, list[Bar]] = {symbol: list((prior_decision or {}).get(symbol, [])) for symbol in symbols}
    spy_hist: list[Bar] = list((prior_decision or {}).get("SPY", []))
    by_end: dict[datetime, dict[str, dict | None]] = defaultdict(dict)
    ends: set[datetime] = set()
    for symbol in [*symbols, "SPY"]:
        bars = minute_bars.get(symbol, [])
        stamps = [parse_time(bar["bar_end"]) for bar in bars]
        for end in decision_ends(stamps, open_at, minutes):
            ends.add(end)
            agg = aggregate(bars, end, minutes)
            if agg is None:
                by_end[end][symbol] = None
                continue
            vwap = session_vwap(bars, end, open_at)
            agg["vwap"] = vwap
            by_end[end][symbol] = agg
    for end in sorted(ends):
        spy_payload = by_end[end].get("SPY")
        spy_ok = spy_payload is not None
        if spy_ok:
            spy_hist.append(Bar(spy_payload["bar_end"], spy_payload["open"], spy_payload["high"], spy_payload["low"], spy_payload["close"], spy_payload["volume"], spy_payload["vwap"]))
        packed = {}
        for symbol in symbols:
            payload = by_end[end].get(symbol)
            packed[symbol] = payload
            if payload is not None:
                histories[symbol].append(Bar(payload["bar_end"], payload["open"], payload["high"], payload["low"], payload["close"], payload["volume"], payload["vwap"]))
        loop.run_decision_bar(end, packed, histories, spy_hist, positions, cards, spy_ok)
    return loop


def book_dict(book: Book) -> dict:
    return asdict(book)


def load_rules(root: Path | None = None) -> Rules:
    return AppConfig.load(root).rules
