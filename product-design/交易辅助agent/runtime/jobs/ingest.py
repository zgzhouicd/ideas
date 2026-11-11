"""Offline second-bucket check. This command does not connect to a market data feed."""

from __future__ import annotations

from nav.ingest import SecondBucket


def main() -> None:
    print("ingest stays offline; seal buckets from a recording. See nav.ingest.SecondBucket.")


if __name__ == "__main__":
    main()
    _ = SecondBucket
