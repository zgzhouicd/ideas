"""Replay the same bar loop from stored 1-minute bars."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import yaml

from nav.bar_loop import run_minutes
from nav.config import AppConfig
from nav.features import Bar
from nav.journal import Journal, decision_rows
from nav.store import Store


def import_rows(store: Store, rows: list[dict]) -> int:
    for row in rows:
        store.upsert_bar(
            row["symbol"],
            row.get("timeframe", "1m"),
            row["bar_end"],
            (float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"]), float(row["volume"])),
            float(row.get("vwap") or row["close"]),
            row.get("source", "external"),
        )
    return len(rows)


def import_csv(store: Store, path: Path) -> int:
    import csv
    with path.open(encoding="utf-8") as handle:
        return import_rows(store, list(csv.DictReader(handle)))


def _split(store: Store, day: str, symbols: list[str]) -> tuple[dict[str, list[dict]], dict[str, list[Bar]]]:
    minutes: dict[str, list[dict]] = {}
    prior: dict[str, list[Bar]] = {}
    for symbol in [*symbols, "SPY"]:
        one = store.bars(symbol, "1m")
        minutes[symbol] = [row for row in one if row["bar_end"].startswith(day)]
        prior[symbol] = [
            Bar(row["bar_end"], row["open"], row["high"], row["low"], row["close"], row["volume"], row["vwap"])
            for row in store.bars(symbol, "decision")
            if row["bar_end"] < day
        ]
    return minutes, prior


def replay(config: AppConfig, store: Store, day: str, out_dir: Path, cards: list[dict] | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    symbols = [item["symbol"] for item in config.watchlist]
    minutes, prior = _split(store, day, symbols)
    journal = Journal(out_dir / "sim.jsonl", out_dir / "outbox.jsonl")
    run_minutes(config, minutes, journal, cards=cards or [], prior_decision=prior)
    manifest = {
        "rule_version": config.rules.version,
        "bar_source": config.rules.bar_source,
        "day": day,
        "files": config.manifest_files(),
    }
    (out_dir / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    rows = decision_rows(journal.inbox)
    (out_dir / "summary.md").write_text(render_summary(rows, config, day), encoding="utf-8")
    return journal.inbox


def render_summary(rows: list[dict], config: AppConfig, day: str) -> str:
    permits = 0
    prev = {}
    maes: list[float] = []
    false_open = 0
    quarantined = 0
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_symbol[row["symbol"]].append(row)
        if row.get("quarantine"):
            quarantined += 1
        label = row["label"]
        if label == "允许开" and prev.get(row["symbol"]) != "允许开":
            permits += 1
        prev[row["symbol"]] = label
    window = config.rules.false_open_bars
    limit = config.rules.false_open_mae_bps
    for series in by_symbol.values():
        for index, row in enumerate(series):
            if row["label"] != "允许开":
                continue
            if index and series[index - 1]["label"] == "允许开":
                continue
            sample = series[index:index + window + 1]
            mae_values = [item["mae_bps"] for item in sample if item.get("mae_bps") is not None]
            mae = max(mae_values) if mae_values else 0.0
            maes.append(mae)
            if mae >= limit and any(item.get("hit_kill") for item in sample):
                false_open += 1
    maes_sorted = sorted(maes)
    p50 = maes_sorted[len(maes_sorted) // 2] if maes_sorted else 0.0
    rate = (quarantined / len(rows)) if rows else 0.0
    return "\n".join([
        f"# {day} · {config.rules.version}",
        "",
        f"- permits_per_day: {permits}",
        f"- false_open: {false_open}",
        f"- mae_p50: {p50:.2f}",
        f"- quarantine_rate: {rate:.4f}",
        f"- bars: {len(rows)}",
        "",
    ])


def diff_paths(live: Path, sim: Path) -> list[dict]:
    left = {(row["symbol"], row["bar_end"]): row["label"] for row in decision_rows(live)}
    right = {(row["symbol"], row["bar_end"]): row["label"] for row in decision_rows(sim)}
    gaps = []
    for key in sorted(set(left) | set(right)):
        if left.get(key) != right.get(key):
            gaps.append({"symbol": key[0], "bar_end": key[1], "live": left.get(key), "sim": right.get(key)})
    return gaps


def labels_with_facts(sim_rows: list[dict], labels: list[dict]) -> list[dict]:
    by_permit = {row.get("permit_id"): row for row in sim_rows}
    found = []
    for label in labels:
        row = by_permit.get(label["permit_id"])
        if row:
            found.append({"label": label, "fact": {"close": row.get("book", {}).get("entry_close"), "conflicts": row.get("conflicts"), "setup_read": row.get("setup_read")}})
    return found
