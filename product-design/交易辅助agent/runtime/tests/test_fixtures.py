"""P0 fixtures F1–F10 and Q1–Q3."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from nav.bar_loop import aggregate
from nav.config import AppConfig
from nav.state import Book, Snap, apply_snap, copy_book, resolve_cluster

ET = ZoneInfo("America/New_York")
ROOT = AppConfig.load().root


def rules():
    return AppConfig.load(ROOT).rules


def at(hour: int, minute: int) -> datetime:
    return datetime(2026, 11, 11, hour, minute, tzinfo=ET)


def snap(symbol="NVDA", hour=10, minute=33, **kw) -> Snap:
    base = dict(
        symbol=symbol, bar_end=at(hour, minute), close=142.0, high=142.4, low=141.7,
        vwap=141.5, volume=2000, vol_ratio=1.42, close_above_vwap=True, momentum_ok=True,
        session_gate="allow_new", regime="TREND_UP", delay_tier="ok", spread_ok=True,
    )
    base.update(kw)
    return Snap(**base)


def run(snaps: list[Snap], book: Book | None = None):
    book = book or Book(snaps[0].symbol)
    decisions = []
    for item in snaps:
        decisions.append(apply_snap(book, item, rules()))
    return decisions


def test_f1_two_closes_trigger():
    decisions = run([snap(minute=30, vol_ratio=1.0), snap(minute=33, vol_ratio=1.42)])
    assert decisions[-1].book.state == "TRIGGERED"
    assert decisions[-1].book.label == "允许开"
    assert decisions[-1].setup_read == "reclaim_ok"


def test_f1_repeat_is_identical():
    left = run([snap(minute=30, vol_ratio=1.0), snap()])
    right = run([snap(minute=30, vol_ratio=1.0), snap()])
    assert [(d.book.state, d.book.label, d.setup_read) for d in left] == [(d.book.state, d.book.label, d.setup_read) for d in right]


def test_f1b_conflict_stays_armed():
    decision = run([snap(minute=30), snap(conflicts=["bearish_flip"])])[-1]
    assert decision.book.state == "ARMED"
    assert decision.book.label == "不操作"
    assert decision.setup_read == "reclaim_conflict"


def test_f2_one_close_is_armed():
    decision = run([snap(minute=30)])[-1]
    assert decision.book.state == "ARMED"
    assert decision.book.label == "不操作"


def test_f3_lunch_blocks_new_open():
    decision = run([snap(minute=30, session_gate="block_new"), snap(session_gate="block_new")])[-1]
    assert decision.book.state == "ARMED"
    assert decision.book.label == "不操作"


def test_f4_mark_becomes_hold():
    book = Book("NVDA")
    apply_snap(book, snap(minute=30, vol_ratio=1.0), rules())
    apply_snap(book, snap(), rules())
    decision = apply_snap(book, snap(minute=36, position_marked=True), rules())
    assert decision.book.state == "HOLD"
    assert decision.book.label == "持有"


def test_f5_hold_breaks_kill():
    book = Book("NVDA")
    apply_snap(book, snap(minute=30, vol_ratio=1.0), rules())
    first = apply_snap(book, snap(), rules())
    kill = first.book.kill_px
    apply_snap(book, snap(minute=36, position_marked=True), rules())
    decision = apply_snap(book, snap(minute=39, position_marked=True, close=kill - 0.4, high=kill, low=kill - 0.6, close_above_vwap=False, vol_ratio=1.3), rules())
    assert decision.book.state == "INVALID"
    assert decision.book.label == "减仓许可"


def test_f6_expires_without_higher_high():
    book = Book("NVDA")
    apply_snap(book, snap(minute=30, vol_ratio=1.0), rules())
    opened = apply_snap(book, snap(), rules())
    assert opened.book.label == "允许开"
    high = opened.book.ref_high
    last = None
    for offset in range(6):
        minute = 36 + offset * 3
        hour = 10 + minute // 60
        minute = minute % 60
        last = apply_snap(book, snap(hour=hour, minute=minute, high=high, close=opened.book.kill_px + 0.2, vol_ratio=1.0), rules())
    assert last.book.state == "EXPIRED"
    assert last.book.label == "结构失效"


def test_f7_cluster_keeps_one_primary():
    cfg = rules()
    a = snap(symbol="AMD", vol_ratio=1.8, cluster_id="semi")
    b = snap(symbol="NVDA", vol_ratio=1.4, cluster_id="semi")
    prior_a, prior_b = Book("AMD"), Book("NVDA")
    prior = {"AMD": copy_book(prior_a), "NVDA": copy_book(prior_b)}
    apply_snap(prior_a, snap(symbol="AMD", minute=30, vol_ratio=1.0, cluster_id="semi"), cfg)
    apply_snap(prior_b, snap(symbol="NVDA", minute=30, vol_ratio=1.0, cluster_id="semi"), cfg)
    da = apply_snap(prior_a, a, cfg)
    db = apply_snap(prior_b, b, cfg)
    resolved = resolve_cluster([(a, da), (b, db)], {"AMD": Book("AMD", last_above=True), "NVDA": Book("NVDA", last_above=True)})
    labels = {item.book.symbol: item.book.label for item in resolved}
    assert labels["AMD"] == "允许开"
    assert labels["NVDA"] == "不操作"
    assert prior == prior


def test_f8_below_vwap_idle():
    decision = run([snap(close_above_vwap=False, close=140, high=140.4, low=139.8, vwap=141)])[-1]
    assert decision.book.state == "IDLE"
    assert decision.book.label == "不操作"


def test_f9_cooldown_returns_to_idle_once():
    book = Book("NVDA")
    apply_snap(book, snap(minute=30, vol_ratio=1.0), rules())
    opened = apply_snap(book, snap(), rules())
    high = opened.book.ref_high
    last = None
    sends = []
    for offset in range(6):
        minute = 36 + offset * 3
        last = apply_snap(book, snap(hour=10, minute=minute % 60, high=high, close=opened.book.kill_px + 0.2, vol_ratio=1.0), rules())
        sends.append(last.label_changed)
    assert last.book.label == "结构失效"
    assert sends[-1] is True
    held = []
    for offset in range(3):
        step = apply_snap(book, snap(hour=11, minute=offset * 3, close_above_vwap=False, close=140, high=140, low=139, vwap=141, vol_ratio=0.8), rules())
        held.append((step.book.label, step.label_changed))
    assert held == [("结构失效", False), ("结构失效", False), ("结构失效", False)]
    done = apply_snap(book, snap(hour=11, minute=9, close_above_vwap=False, close=140, high=140, low=139, vwap=141, vol_ratio=0.8), rules())
    assert done.book.state == "IDLE"
    assert done.book.label == "不操作"
    assert done.label_changed is True


def test_f10_mark_below_vwap_is_ignored():
    decision = run([snap(close_above_vwap=False, close=140, high=140.2, low=139.6, vwap=141, position_marked=True)])[-1]
    assert decision.book.state == "IDLE"
    assert decision.book.label == "不操作"
    assert decision.position_mark_ignored is True


def test_short_history_cannot_open():
    decision = run([snap(minute=30, momentum_ok=False), snap(momentum_ok=False)])[-1]
    assert decision.book.label != "允许开"
    assert "momentum_unavailable" in decision.conflicts


def test_q1_quarantine_keeps_permit():
    book = Book("NVDA")
    apply_snap(book, snap(minute=30, vol_ratio=1.0), rules())
    apply_snap(book, snap(), rules())
    decision = apply_snap(book, snap(minute=36, quarantine=True), rules())
    assert decision.quarantine is True
    assert decision.book.label == "允许开"
    assert decision.label_changed is False


def test_q1b_one_minute_gap_is_incomplete():
    end = at(10, 33)
    bars = [
        {"bar_end": at(10, 31).isoformat(), "open": 1, "high": 2, "low": 1, "close": 1, "volume": 10},
        {"bar_end": at(10, 32).isoformat(), "open": 1, "high": 2, "low": 1, "close": 1, "volume": 10},
    ]
    assert aggregate(bars, end, 3) is None


def test_q2_stale_does_not_open():
    decision = run([snap(minute=30, delay_tier="stale"), snap(delay_tier="stale")])[-1]
    assert decision.book.label != "允许开"


def test_q3_crossed_book_quarantines():
    decision = run([snap(minute=30), snap(minute=33, spread_ok=False)])[-1]
    assert decision.quarantine is True


def test_hold_survives_lunch_gate():
    book = Book("NVDA")
    apply_snap(book, snap(minute=30, vol_ratio=1.0), rules())
    apply_snap(book, snap(), rules())
    apply_snap(book, snap(minute=36, position_marked=True), rules())
    decision = apply_snap(book, snap(hour=12, minute=0, position_marked=True, session_gate="block_new"), rules())
    assert decision.book.state == "HOLD"
    assert decision.book.label == "持有"
