"""Append-only inbox, local telegram outbox, and ack lines."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from nav.config import Rules
from nav.state import Decision, Snap


def _dump(record: dict) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True)


class Journal:
    def __init__(self, inbox: Path, outbox: Path | None = None):
        self.inbox = inbox
        self.outbox = outbox or inbox.with_name("outbox.jsonl")
        self.inbox.parent.mkdir(parents=True, exist_ok=True)
        self.outbox.parent.mkdir(parents=True, exist_ok=True)
        self._acked = self._load_acks()

    def _load_acks(self) -> set[str]:
        found: set[str] = set()
        if not self.inbox.exists():
            return found
        for line in self.inbox.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("kind") == "telegram_ack":
                found.add(row["permit_id"])
        return found

    def write_decision(self, decision: Decision, snap: Snap, rules: Rules, text: str, rule_version: str) -> dict:
        if decision.label_changed and not decision.quarantine:
            decision.book.event_n += 1
            day = snap.bar_end.date().isoformat().replace("-", "")
            decision.permit_id = f"p-{day}-{snap.symbol}-{decision.book.event_n}"
        record = {
            "kind": "decision",
            "permit_id": decision.permit_id,
            "symbol": snap.symbol,
            "bar_end": snap.bar_end.isoformat(),
            "rule_version": rule_version,
            "quarantine": decision.quarantine,
            "state": decision.book.state,
            "label": decision.book.label,
            "kill_px": decision.book.kill_px,
            "bars_left": decision.book.bars_left,
            "position_marked": snap.position_marked,
            "position_mark_ignored": decision.position_mark_ignored,
            "channel": decision.channel,
            "setup_read": decision.setup_read,
            "conflicts": decision.conflicts,
            "correction_ids": decision.correction_ids,
            "mfe_bps": decision.book.mfe_bps,
            "mae_bps": decision.book.mae_bps,
            "hit_kill": decision.book.hit_kill,
            "text": {"decision": text, "warning": ""},
            "emitted_telegram": False,
            "book": asdict(decision.book),
        }
        with self.inbox.open("a", encoding="utf-8") as handle:
            handle.write(_dump(record) + "\n")
        if decision.label_changed and not decision.quarantine and decision.permit_id and decision.permit_id not in self._acked:
            self._send(decision, snap, text)
        return record

    def _send(self, decision: Decision, snap: Snap, text: str) -> None:
        body = {
            "permit_id": decision.permit_id,
            "symbol": snap.symbol,
            "bar_end": snap.bar_end.isoformat(),
            "label": decision.book.label,
            "kill_px": decision.book.kill_px,
            "bars_left": decision.book.bars_left,
            "setup_read": decision.setup_read,
            "conflicts": decision.conflicts or ["无冲突"],
            "text": text,
        }
        with self.outbox.open("a", encoding="utf-8") as handle:
            handle.write(_dump(body) + "\n")
        ack = {"kind": "telegram_ack", "permit_id": decision.permit_id}
        with self.inbox.open("a", encoding="utf-8") as handle:
            handle.write(_dump(ack) + "\n")
        self._acked.add(decision.permit_id)

    def write_warning(self, symbol: str, bar_end: datetime, text: str, late: bool = False) -> None:
        record = {"kind": "warning", "symbol": symbol, "bar_end": bar_end.isoformat(), "text": text, "late": late}
        with self.inbox.open("a", encoding="utf-8") as handle:
            handle.write(_dump(record) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def decision_rows(path: Path) -> list[dict]:
    return [row for row in read_jsonl(path) if row.get("kind") == "decision"]


def restore_label(path: Path, symbol: str) -> str | None:
    rows = [row for row in decision_rows(path) if row["symbol"] == symbol and not row.get("quarantine")]
    if not rows:
        return None
    return rows[-1]["label"]
