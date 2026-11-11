"""Write the daily recap markdown from an inbox file."""

from __future__ import annotations

import argparse
from pathlib import Path

from nav.journal import decision_rows
from nav.review import write_recap


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inbox", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    before = args.inbox.read_text(encoding="utf-8")
    write_recap(args.out, decision_rows(args.inbox))
    if args.inbox.read_text(encoding="utf-8") != before:
        raise SystemExit("recap must not rewrite the inbox")
    print(args.out)


if __name__ == "__main__":
    main()
