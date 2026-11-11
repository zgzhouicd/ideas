"""Build falsification cards from labeled permits. Does not change rules mid-session."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from nav.journal import decision_rows
from nav.review import mark_false_open, outcomes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inbox", type=Path, required=True)
    parser.add_argument("--corrections", type=Path, required=True)
    parser.add_argument("--permit", required=True)
    args = parser.parse_args()
    notes = [note for note in outcomes(decision_rows(args.inbox)) if note["permit_id"] == args.permit]
    if not notes:
        raise SystemExit(f"missing permit {args.permit}")
    card = mark_false_open(args.corrections, notes[0])
    print(json.dumps(card, ensure_ascii=False))


if __name__ == "__main__":
    main()
