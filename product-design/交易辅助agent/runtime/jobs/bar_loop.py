"""Decision-bar entry point. Replay and the synthetic day share nav.bar_loop."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from nav.bar_loop import run_minutes
from nav.config import AppConfig
from nav.journal import Journal
from nav.synthetic import synthetic_minutes, warmup_as_bars, warmup_bars


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one synthetic RTH day through the decision loop")
    parser.add_argument("--day", default="2026-11-11")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    config = AppConfig.load(args.root)
    day = datetime.fromisoformat(args.day).replace(tzinfo=ZoneInfo("America/New_York"))
    symbols = [item["symbol"] for item in config.watchlist]
    minutes: dict[str, list] = {}
    for row in synthetic_minutes(day, symbols):
        minutes.setdefault(row["symbol"], []).append(row)
    prior = warmup_as_bars(warmup_bars(day, symbols))
    inbox = args.root / "inbox" / f"{args.day}.jsonl"
    journal = Journal(inbox, args.root / "outbox" / f"{args.day}.jsonl")
    run_minutes(config, minutes, journal, prior_decision=prior)
    print(inbox)


if __name__ == "__main__":
    main()
