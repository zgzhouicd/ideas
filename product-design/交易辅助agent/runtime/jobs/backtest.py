"""import, replay, and diff. Replay calls the same bar loop as the session runner."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from nav.backtest import diff_paths, import_csv, replay
from nav.config import AppConfig
from nav.store import Store


def main() -> None:
    parser = argparse.ArgumentParser(description="Historical replay")
    sub = parser.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import")
    imp.add_argument("--csv", type=Path, required=True)
    imp.add_argument("--sqlite", type=Path, required=True)
    run = sub.add_parser("replay")
    run.add_argument("--day", required=True)
    run.add_argument("--sqlite", type=Path, required=True)
    run.add_argument("--out", type=Path, required=True)
    differ = sub.add_parser("diff")
    differ.add_argument("--live", type=Path, required=True)
    differ.add_argument("--sim", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.cmd == "import":
        store = Store(args.sqlite)
        count = import_csv(store, args.csv)
        store.close()
        print(count)
        return
    if args.cmd == "replay":
        config = AppConfig.load(root)
        store = Store(args.sqlite)
        path = replay(config, store, args.day, args.out)
        store.close()
        print(path)
        return
    gaps = diff_paths(args.live, args.sim)
    print(json.dumps(gaps, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
