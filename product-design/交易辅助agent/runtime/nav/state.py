"""Permit state machine. Invalidation and expiry are decided once per bar."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime

from nav.config import Rules

OPEN_STATES = ("TRIGGERED", "HOLD")
EXIT_STATES = ("INVALID", "EXPIRED")


@dataclass
class Book:
    symbol: str
    state: str = "IDLE"
    label: str = "不操作"
    kill_px: float | None = None
    bars_left: int | None = None
    ref_high: float | None = None
    last_above: bool = False
    cooldown_wait: int = 0
    entry_close: float | None = None
    extreme_high: float | None = None
    extreme_low: float | None = None
    mfe_bps: float | None = None
    mae_bps: float | None = None
    bars_in_trade: int = 0
    hit_kill: bool = False
    permit_n: int = 0
    open_permits: int = 0
    event_n: int = 0


@dataclass
class Snap:
    symbol: str
    bar_end: datetime
    close: float
    high: float
    low: float
    vwap: float
    volume: float
    vol_ratio: float | None
    close_above_vwap: bool
    conflicts: list[str] = field(default_factory=list)
    momentum_ok: bool = True
    position_marked: bool = False
    session_gate: str = "allow_new"
    regime: str = "TREND_UP"
    cluster_id: str | None = None
    delay_tier: str = "ok"
    spread_ok: bool = True
    quarantine: bool = False
    correction_ids: list[str] = field(default_factory=list)
    features: dict = field(default_factory=dict)


@dataclass
class Decision:
    book: Book
    setup_read: str
    conflicts: list[str]
    correction_ids: list[str]
    structure_failed: bool
    structure_expired: bool
    quarantine: bool
    label_changed: bool
    position_mark_ignored: bool
    permit_id: str | None
    channel: str


def label_for(state: str, position_marked: bool) -> str:
    if state in ("IDLE", "ARMED"):
        return "不操作"
    if state == "TRIGGERED":
        return "允许开"
    if state == "HOLD":
        return "持有"
    if state in EXIT_STATES:
        return "减仓许可" if position_marked else "结构失效"
    raise ValueError(state)


def _bps(entry: float, high: float, low: float) -> tuple[float, float]:
    mfe = (high - entry) / entry * 10000
    mae = (entry - low) / entry * 10000
    return mfe, mae


def _conflicts(snap: Snap) -> list[str]:
    found = list(snap.conflicts)
    if not snap.momentum_ok and "momentum_unavailable" not in found:
        found.append("momentum_unavailable")
    return found


def structure_flags(book: Book, snap: Snap, rules: Rules) -> tuple[bool, bool]:
    """Shared by setup_read and the transition. Trigger bar itself cannot expire."""
    if book.state not in OPEN_STATES or book.kill_px is None or book.bars_left is None:
        return False, False
    vol = snap.vol_ratio or 0.0
    failed = snap.close < book.kill_px and vol >= rules.template.invalid_vol_ratio
    higher = snap.high > (book.ref_high if book.ref_high is not None else snap.high)
    bars_left = rules.template.expire_bars if higher else book.bars_left - 1
    expired = bars_left <= 0 and not failed
    return failed, expired


def setup_read(book: Book, snap: Snap, rules: Rules, conflicts: list[str], failed: bool, expired: bool) -> str:
    if book.state in OPEN_STATES and (failed or expired):
        return "failure"
    two = snap.close_above_vwap and book.last_above
    vol_ok = snap.vol_ratio is not None and snap.vol_ratio >= rules.template.vol_ratio_min
    if two and vol_ok and not conflicts:
        return "reclaim_ok"
    if two and vol_ok and conflicts:
        return "reclaim_conflict"
    return "no_setup"


def _trade_stats(book: Book, snap: Snap) -> None:
    if book.entry_close is None:
        return
    book.extreme_high = snap.high if book.extreme_high is None else max(book.extreme_high, snap.high)
    book.extreme_low = snap.low if book.extreme_low is None else min(book.extreme_low, snap.low)
    book.mfe_bps, book.mae_bps = _bps(book.entry_close, book.extreme_high, book.extreme_low)
    if book.kill_px is not None and snap.close < book.kill_px:
        book.hit_kill = True


def _enter_exit(book: Book, snap: Snap, rules: Rules, state: str) -> None:
    book.state = state
    book.cooldown_wait = rules.template.cooldown_bars
    _trade_stats(book, snap)


def _clear_trade(book: Book) -> None:
    book.kill_px = None
    book.bars_left = None
    book.ref_high = None
    book.entry_close = None
    book.extreme_high = None
    book.extreme_low = None
    book.bars_in_trade = 0
    book.hit_kill = False


def _enter_open(book: Book, snap: Snap, rules: Rules, state: str) -> None:
    book.state = state
    book.kill_px = snap.vwap
    book.ref_high = snap.high
    book.bars_left = rules.template.expire_bars
    book.entry_close = snap.close
    book.extreme_high = snap.high
    book.extreme_low = snap.low
    book.mfe_bps, book.mae_bps = _bps(snap.close, snap.high, snap.low)
    book.bars_in_trade = 0
    book.hit_kill = False
    book.cooldown_wait = 0
    if state == "TRIGGERED":
        book.permit_n += 1
        book.open_permits += 1


def apply_snap(
    book: Book,
    snap: Snap,
    rules: Rules,
    *,
    cluster_blocked: bool = False,
    daily_cap_hit: bool = False,
) -> Decision:
    prior_label = book.label
    conflicts = _conflicts(snap)
    failed, expired = structure_flags(book, snap, rules)
    read = setup_read(book, snap, rules, conflicts, failed, expired)
    ignored = False
    quarantine = snap.quarantine or not snap.spread_ok
    used_cards: list[str] = []

    if quarantine:
        book.label = label_for(book.state, book.state in EXIT_STATES and snap.position_marked or (book.state == "HOLD"))
        # Keep the previous label exactly. Position marking does not apply on a bad bar.
        book.label = prior_label
        return _pack(book, snap, read, conflicts, [], failed, expired, True, prior_label, False, rules)

    if book.state in EXIT_STATES:
        if book.cooldown_wait > 0:
            book.cooldown_wait -= 1
            book.last_above = snap.close_above_vwap
            book.label = label_for(book.state, snap.position_marked)
            return _pack(book, snap, read, conflicts, [], False, False, False, prior_label, False, rules)
        book.state = "IDLE"
        _clear_trade(book)

    if book.state in OPEN_STATES:
        higher = snap.high > (book.ref_high if book.ref_high is not None else snap.high)
        if higher and book.ref_high is not None:
            book.ref_high = snap.high
            book.bars_left = rules.template.expire_bars
        elif book.bars_left is not None:
            book.bars_left -= 1
        book.bars_in_trade += 1
        _trade_stats(book, snap)
        if failed:
            _enter_exit(book, snap, rules, "INVALID")
        elif expired:
            _enter_exit(book, snap, rules, "EXPIRED")
        elif snap.position_marked or book.state == "HOLD":
            if snap.position_marked:
                book.state = "HOLD"
            elif book.state == "HOLD" and not snap.position_marked:
                book.state = "TRIGGERED" if read == "reclaim_ok" else "ARMED"
        book.last_above = snap.close_above_vwap
        book.label = label_for(book.state, snap.position_marked if book.state in EXIT_STATES else book.state == "HOLD" or (book.state == "TRIGGERED" and False))
        if book.state in EXIT_STATES:
            book.label = label_for(book.state, snap.position_marked)
        elif book.state == "HOLD":
            book.label = "持有"
        elif book.state == "TRIGGERED":
            book.label = "允许开"
        else:
            book.label = "不操作"
        return _pack(book, snap, "failure" if book.state in EXIT_STATES else read, conflicts, [], failed, expired, False, prior_label, False, rules)

    # Flat book: IDLE or ARMED.
    wants_open = (
        rules.template.enabled
        and read == "reclaim_ok"
        and snap.session_gate == "allow_new"
        and snap.delay_tier == "ok"
        and snap.regime != "incomplete"
        and not cluster_blocked
        and not daily_cap_hit
        and not snap.position_marked
    )
    if snap.correction_ids and wants_open:
        wants_open = False
        used_cards = list(snap.correction_ids)
    if snap.position_marked:
        if snap.close_above_vwap and not failed:
            _enter_open(book, snap, rules, "HOLD")
            book.permit_n = book.permit_n  # hold is not a new open permit
        else:
            ignored = True
            book.state = "IDLE"
    elif wants_open:
        _enter_open(book, snap, rules, "TRIGGERED")
    elif snap.close_above_vwap or read == "reclaim_conflict" or cluster_blocked or used_cards or snap.session_gate == "block_new" and read in ("reclaim_ok", "reclaim_conflict"):
        book.state = "ARMED"
    else:
        book.state = "IDLE"
    book.last_above = snap.close_above_vwap
    if book.state == "HOLD":
        book.label = "持有"
    elif book.state == "TRIGGERED":
        book.label = "允许开"
    else:
        book.label = "不操作"
        if book.state == "IDLE":
            book.kill_px = None
            book.bars_left = None
    return _pack(book, snap, read, conflicts, used_cards, False, False, False, prior_label, ignored, rules)


def _pack(book, snap, read, conflicts, cards, failed, expired, quarantine, prior_label, ignored, rules) -> Decision:
    marked_exit = book.state in EXIT_STATES and snap.position_marked
    channel = "position_watch" if book.state == "HOLD" or marked_exit else "open_scan"
    return Decision(
        book=book,
        setup_read=read,
        conflicts=conflicts,
        correction_ids=cards,
        structure_failed=failed,
        structure_expired=expired,
        quarantine=quarantine,
        label_changed=book.label != prior_label,
        position_mark_ignored=ignored,
        permit_id=None,
        channel=channel,
    )


def resolve_cluster(decisions: list[tuple[Snap, Decision]], prior: dict[str, Book]) -> list[Decision]:
    """One primary per cluster. Incumbent TRIGGERED/HOLD stays; otherwise larger vol_ratio, then symbol."""
    groups: dict[str, list[tuple[Snap, Decision]]] = {}
    for snap, decision in decisions:
        if snap.cluster_id and decision.book.state in ("TRIGGERED", "HOLD"):
            groups.setdefault(snap.cluster_id, []).append((snap, decision))
    demote: set[str] = set()
    for members in groups.values():
        if len(members) < 2:
            continue

        def rank(item: tuple[Snap, Decision]) -> tuple:
            snap, decision = item
            prev = prior[snap.symbol]
            incumbent = 0 if prev.state in ("TRIGGERED", "HOLD") else 1
            vol = snap.vol_ratio or 0.0
            return (incumbent, -vol, snap.symbol)

        ordered = sorted(members, key=rank)
        for snap, _decision in ordered[1:]:
            if prior[snap.symbol].state not in ("TRIGGERED", "HOLD"):
                demote.add(snap.symbol)
    results = []
    for snap, decision in decisions:
        if snap.symbol in demote and decision.book.state == "TRIGGERED":
            decision.book.state = "ARMED"
            decision.book.label = "不操作"
            decision.book.kill_px = None
            decision.book.bars_left = None
            decision.book.entry_close = None
            decision.book.open_permits = max(0, decision.book.open_permits - 1)
            decision.book.permit_n = max(0, decision.book.permit_n - 1)
            decision.label_changed = decision.book.label != prior[snap.symbol].label
            decision.permit_id = None
            decision.channel = "open_scan"
        results.append(decision)
    return results


def copy_book(book: Book) -> Book:
    return replace(book)
