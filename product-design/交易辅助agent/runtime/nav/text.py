"""Fixed-sentence card and the P1 output check. Empty model means the card."""

from __future__ import annotations

import json
import re
from pathlib import Path

from nav.config import ROOT, Rules
from nav.state import Decision, Snap

BANNED = ("买入", "卖出", "追", "市价", "股")


def load_card(path: Path | None = None) -> dict[str, str]:
    path = path or (ROOT / "prompts" / "card.txt")
    templates: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        templates[key.strip()] = value.strip().strip('"')
    return templates


def render_card(decision: Decision, snap: Snap, rules: Rules, templates: dict[str, str] | None = None) -> str:
    templates = templates or load_card()
    if decision.quarantine:
        return templates["quarantine"]
    values = {
        "label": decision.book.label,
        "setup_read": decision.setup_read,
        "close": _num(snap.close),
        "vwap": _num(snap.vwap),
        "vol_ratio": "" if snap.vol_ratio is None else _num(snap.vol_ratio),
        "kill_px": "" if decision.book.kill_px is None else _num(decision.book.kill_px),
        "bars_left": "" if decision.book.bars_left is None else str(decision.book.bars_left),
        "invalid_vol_ratio": _num(rules.template.invalid_vol_ratio),
        "correction_id": decision.correction_ids[0] if decision.correction_ids else "",
    }
    if decision.correction_ids and decision.book.label == "不操作":
        key = "suppressed"
    elif decision.book.state == "TRIGGERED":
        key = "trigger"
    elif decision.book.state == "HOLD":
        key = "hold"
    elif decision.book.state in ("INVALID", "EXPIRED"):
        key = "exit"
    else:
        key = "idle"
    return templates[key].format(**values)


def _num(value: float) -> str:
    return f"{value:.2f}"


def allowed_numbers(snap: Snap, rules: Rules, correction_ids: list[str], book=None) -> set[str]:
    raw = [
        snap.close, snap.high, snap.low, snap.vwap, snap.volume,
        snap.vol_ratio, rules.template.vol_ratio_min, rules.template.invalid_vol_ratio,
        rules.template.expire_bars, rules.template.cooldown_bars,
    ]
    if book is not None:
        raw.extend([book.kill_px, book.bars_left, book.mfe_bps, book.mae_bps])
    allowed: set[str] = set()
    for value in raw:
        if value is None:
            continue
        if isinstance(value, float):
            allowed.add(f"{value:.2f}")
            allowed.add(f"{value:.4f}".rstrip("0").rstrip("."))
            allowed.add(str(int(value)) if value == int(value) else "")
        else:
            allowed.add(str(value))
    for cid in correction_ids:
        allowed.update(re.findall(r"\d+(?:\.\d+)?", cid))
    allowed.discard("")
    return allowed


def validate_model_output(payload: dict, decision: Decision, snap: Snap, rules: Rules) -> str | None:
    """Return the decision sentence, or None when the fixed card must be used."""
    if payload.get("label") != decision.book.label:
        return None
    if payload.get("setup_read") != decision.setup_read:
        return None
    cited = set(payload.get("conflicts_cited") or [])
    if cited != set(decision.conflicts):
        return None
    if set(payload.get("correction_ids") or []) - set(snap.correction_ids):
        return None
    if payload.get("warning"):
        return None
    text = str(payload.get("decision") or "")
    if any(word in text for word in BANNED):
        return None
    allowed = allowed_numbers(snap, rules, list(snap.correction_ids), decision.book)
    for number in re.findall(r"\d+(?:\.\d+)?", text):
        if number not in allowed and f"{float(number):.2f}" not in allowed:
            return None
    if decision.book.label not in text or decision.setup_read not in text:
        return None
    return text


def accept_model_text(raw: str | None, decision: Decision, snap: Snap, rules: Rules) -> str:
    if not raw:
        return render_card(decision, snap, rules)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return render_card(decision, snap, rules)
    checked = validate_model_output(payload, decision, snap, rules)
    if checked is None:
        return render_card(decision, snap, rules)
    return checked
