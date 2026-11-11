"""Loop, replay, writer, ingest, and recap."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from nav.backtest import diff_paths, import_rows, replay
from nav.bar_loop import run_minutes
from nav.config import AppConfig, Rules
from nav.ingest import SecondBucket
from nav.journal import Journal, decision_rows
from nav.review import load_corrections, mark_false_open, outcomes, write_recap
from nav.state import Book, apply_snap
from nav.store import Store
from nav.synthetic import synthetic_minutes, warmup_as_bars, warmup_bars
from nav.text import accept_model_text, render_card
from tests.test_fixtures import at, rules, snap

ET = ZoneInfo("America/New_York")


def test_synthetic_day_is_stable(tmp_path: Path):
    config = AppConfig.load()
    day = datetime(2026, 11, 11, tzinfo=ET)
    symbols = [item["symbol"] for item in config.watchlist]
    minutes: dict[str, list] = {}
    for row in synthetic_minutes(day, symbols):
        minutes.setdefault(row["symbol"], []).append(row)
    prior = warmup_as_bars(warmup_bars(day, symbols))
    first = tmp_path / "a.jsonl"
    second = tmp_path / "b.jsonl"
    run_minutes(config, minutes, Journal(first, tmp_path / "a-out.jsonl"), prior_decision=prior)
    run_minutes(config, minutes, Journal(second, tmp_path / "b-out.jsonl"), prior_decision=prior)
    left = [(row["symbol"], row["bar_end"], row["label"], row["state"]) for row in decision_rows(first)]
    right = [(row["symbol"], row["bar_end"], row["label"], row["state"]) for row in decision_rows(second)]
    assert left == right
    assert any(row["label"] == "允许开" for row in decision_rows(first))
    assert all(row["text"]["warning"] == "" for row in decision_rows(first))
    text = decision_rows(first)[0]["text"]["decision"]
    assert "买入" not in text and "卖出" not in text


def test_replay_twice_and_diff(tmp_path: Path):
    config = AppConfig.load()
    day = datetime(2026, 11, 11, tzinfo=ET)
    symbols = [item["symbol"] for item in config.watchlist]
    store = Store(tmp_path / "market.sqlite")
    import_rows(store, synthetic_minutes(day, symbols) + warmup_bars(day, symbols))
    one = replay(config, store, "2026-11-11", tmp_path / "r1")
    two = replay(config, store, "2026-11-11", tmp_path / "r2")
    assert diff_paths(one, two) == []
    summary = (tmp_path / "r1" / "summary.md").read_text(encoding="utf-8")
    assert "permits_per_day:" in summary
    assert "false_open:" in summary
    assert "mae_p50:" in summary
    manifest = (tmp_path / "r1" / "manifest.yaml").read_text(encoding="utf-8")
    assert "bar_source: 1m" in manifest
    store.close()


def test_writer_rejects_bad_payloads():
    cfg = rules()
    book = Book("NVDA")
    apply_snap(book, snap(minute=30, vol_ratio=1.0), cfg)
    decision = apply_snap(book, snap(), cfg)
    item = snap()
    card = render_card(decision, item, cfg)
    assert decision.book.label in card
    assert accept_model_text("not-json", decision, item, cfg) == card
    mismatch = json.dumps({"label": "不操作", "setup_read": decision.setup_read, "conflicts_cited": [], "correction_ids": [], "warning": "", "decision": "不操作。结论是 reclaim_ok。"})
    assert accept_model_text(mismatch, decision, item, cfg) == card
    extra = json.dumps({
        "label": decision.book.label,
        "setup_read": decision.setup_read,
        "conflicts_cited": decision.conflicts,
        "correction_ids": [],
        "warning": "",
        "decision": f"{decision.book.label}。结论是 {decision.setup_read}。目标价 999。",
    })
    assert accept_model_text(extra, decision, item, cfg) == card
    ok = json.dumps({
        "label": decision.book.label,
        "setup_read": decision.setup_read,
        "conflicts_cited": decision.conflicts,
        "correction_ids": [],
        "warning": "",
        "decision": card,
    })
    assert accept_model_text(ok, decision, item, cfg) == card


def test_missing_seconds_and_no_duplicate_send(tmp_path: Path):
    bucket = SecondBucket(incomplete_seconds=5)
    start = at(10, 0)
    end = start + timedelta(minutes=1)
    for offset in range(60):
        if offset < 8:
            continue
        bucket.add(start + timedelta(seconds=offset), 100.0, 1)
    sealed = bucket.seal(start, end)
    assert sealed["incomplete"] is True
    assert sealed["missing_seconds"] == 8

    cfg = rules()
    book = Book("NVDA")
    inbox = tmp_path / "inbox.jsonl"
    outbox = tmp_path / "out.jsonl"
    journal = Journal(inbox, outbox)
    first = snap(minute=30, vol_ratio=1.0)
    second = snap()
    apply_snap(book, first, cfg)
    opened = apply_snap(book, second, cfg)
    from nav.text import render_card
    journal.write_decision(opened, second, cfg, render_card(opened, second, cfg), cfg.version)
    assert len(outbox.read_text(encoding="utf-8").splitlines()) == 1

    from nav.bar_loop import Loop
    restored = Loop(AppConfig.load(), Journal(inbox, tmp_path / "out2.jsonl"))
    restored.restore()
    assert restored.books["NVDA"].label == "允许开"
    follow = apply_snap(
        restored.books["NVDA"],
        snap(minute=36, high=opened.book.ref_high, close=opened.book.kill_px + 0.2, vol_ratio=1.0),
        cfg,
    )
    restored.journal.write_decision(follow, snap(minute=36), cfg, "不操作。结论是 no_setup。", cfg.version)
    assert follow.book.label == "允许开"
    assert follow.label_changed is False
    assert not (tmp_path / "out2.jsonl").exists()


def test_c1_card_suppresses_next_open():
    cfg = rules()
    book = Book("NVDA")
    decision = None
    for item in (snap(minute=30, vol_ratio=1.0, correction_ids=["c-20261111-1"]), snap(correction_ids=["c-20261111-1"])):
        decision = apply_snap(book, item, cfg)
    assert decision.book.state == "ARMED"
    assert decision.book.label == "不操作"
    assert "c-20261111-1" in decision.correction_ids
    text = render_card(decision, snap(correction_ids=["c-20261111-1"]), cfg)
    assert "c-20261111-1" in text


def test_c2_other_symbol_card_does_not_block():
    decision = None
    book = Book("NVDA")
    cfg = rules()
    for item in (snap(minute=30, vol_ratio=1.0), snap()):
        decision = apply_snap(book, item, cfg)
    assert decision.book.label == "允许开"


def test_c3_loaded_rules_ignore_later_file_edits(tmp_path: Path):
    loaded = Rules.load(AppConfig.load().paths["rules"])
    assert loaded.template.vol_ratio_min == 1.3
    book = Book("NVDA")
    apply_snap(book, snap(minute=30, vol_ratio=1.0), loaded)
    decision = apply_snap(book, snap(vol_ratio=1.4), loaded)
    assert decision.book.label == "允许开"


def test_recap_does_not_touch_permits(tmp_path: Path):
    config = AppConfig.load()
    day = datetime(2026, 11, 11, tzinfo=ET)
    symbols = [item["symbol"] for item in config.watchlist]
    minutes: dict[str, list] = {}
    for row in synthetic_minutes(day, symbols):
        minutes.setdefault(row["symbol"], []).append(row)
    inbox = tmp_path / "day.jsonl"
    run_minutes(config, minutes, Journal(inbox, tmp_path / "out.jsonl"), prior_decision=warmup_as_bars(warmup_bars(day, symbols)))
    before = inbox.read_text(encoding="utf-8")
    notes = outcomes(decision_rows(inbox))
    opened = [note for note in notes if note["permit_id"]]
    assert opened
    card = mark_false_open(tmp_path / "corrections.jsonl", opened[0])
    assert load_corrections(tmp_path / "corrections.jsonl")[0]["id"] == card["id"]
    write_recap(tmp_path / "recap.md", decision_rows(inbox), [{"permit_id": opened[0]["permit_id"], "verdict": "false_open"}])
    assert inbox.read_text(encoding="utf-8") == before
    assert "不改许可" in (tmp_path / "recap.md").read_text(encoding="utf-8")
