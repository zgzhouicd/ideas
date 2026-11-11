"""MFE/MAE notes, falsification cards, and the daily recap. Recap does not change permits."""

from __future__ import annotations

import json
from pathlib import Path

from nav.journal import decision_rows


def append_correction(path: Path, card: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(card, ensure_ascii=False, sort_keys=True) + "\n")


def load_corrections(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def outcomes(rows: list[dict], horizon: int = 6) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["symbol"], []).append(row)
    notes = []
    for symbol, series in grouped.items():
        for index, row in enumerate(series):
            if row["label"] != "允许开":
                continue
            if index and series[index - 1]["label"] == "允许开":
                continue
            window = series[index:index + horizon + 1]
            mae = [item["mae_bps"] for item in window if item.get("mae_bps") is not None]
            mfe = [item["mfe_bps"] for item in window if item.get("mfe_bps") is not None]
            notes.append({
                "symbol": symbol,
                "permit_id": row.get("permit_id"),
                "bar_end": row["bar_end"],
                "mfe_bps": max(mfe) if mfe else None,
                "mae_bps": max(mae) if mae else None,
                "hit_kill": any(item.get("hit_kill") for item in window),
                "conflicts": row.get("conflicts") or [],
                "setup_read": row.get("setup_read"),
            })
    return notes


def mark_false_open(corrections: Path, note: dict, reason: str = "false_break") -> dict:
    card = {
        "id": f"c-{note['bar_end'][:10].replace('-', '')}-{note['symbol']}",
        "symbol": note["symbol"],
        "template": "TPL_VWAP_RECLAIM",
        "effect": "suppress_session",
        "reason": reason,
        "fact_digest": {"setup_read": note["setup_read"], "conflicts": note["conflicts"]},
        "expires": None,
    }
    append_correction(corrections, card)
    return card


def write_recap(path: Path, rows: list[dict], labels: list[dict] | None = None) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    labels = labels or []
    by_id = {item["permit_id"]: item for item in labels}
    lines = ["# 收盘复盘", "", "| 时间 | 标的 | 许可 | MFE | MAE | 标注 |", "| --- | --- | --- | --- | --- | --- |"]
    for note in outcomes(rows):
        verdict = by_id.get(note["permit_id"], {}).get("verdict", "")
        lines.append(
            f"| {note['bar_end']} | {note['symbol']} | {note['permit_id']} | {note['mfe_bps']} | {note['mae_bps']} | {verdict} |"
        )
    lines.append("")
    lines.append("复盘稿只汇总 jsonl，不改许可。")
    text = "\n".join(lines) + "\n"
    path.write_text(text, encoding="utf-8")
    return text


def decision_snapshot(path: Path) -> list[dict]:
    return decision_rows(path)
