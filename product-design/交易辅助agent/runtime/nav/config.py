"""Load the yaml the development spec treats as the locked runtime inputs."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]


def read_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data or {}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass
class TemplateRule:
    name: str
    enabled: bool
    start_after: str = "10:00"
    confirm_closes: int = 2
    vol_ratio_min: float = 1.3
    invalid_vol_ratio: float = 1.2
    expire_bars: int = 6
    cooldown_bars: int = 3


@dataclass
class Rules:
    version: str
    bar_source: str
    template: TemplateRule
    stale_ms: int
    incomplete_seconds: int
    spread_bps_max: float
    max_new_open_per_day: int
    in_play_max: int
    false_open_bars: int
    false_open_mae_bps: float
    features: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None

    @classmethod
    def load(cls, path: Path) -> "Rules":
        raw = read_yaml(path)
        tpl_raw = raw["templates"]["TPL_VWAP_RECLAIM"]
        gates = raw["gates"]
        ev = raw["eval"]
        return cls(
            version=raw["version"],
            bar_source=raw["bar_source"],
            template=TemplateRule(name="TPL_VWAP_RECLAIM", **tpl_raw),
            stale_ms=int(gates["stale_ms"]),
            incomplete_seconds=int(gates["incomplete_seconds"]),
            spread_bps_max=float(gates["spread_bps_max"]),
            max_new_open_per_day=int(gates["max_new_open_per_day"]),
            in_play_max=int(gates["in_play_max"]),
            false_open_bars=int(ev["false_open_bars"]),
            false_open_mae_bps=float(ev["false_open_mae_bps"]),
            features=dict(raw.get("features") or {}),
            path=path,
        )


@dataclass
class AppConfig:
    root: Path
    rules: Rules
    clocks: dict[str, Any]
    sessions: dict[str, Any]
    calendar: dict[str, Any]
    watchlist: list[dict[str, Any]]
    llm: dict[str, Any]
    paths: dict[str, Path]

    @classmethod
    def load(cls, root: Path | None = None) -> "AppConfig":
        root = root or ROOT
        cfg = root / "config"
        rules = Rules.load(cfg / "rules.yaml")
        watch = read_yaml(cfg / "watchlist.yaml")
        symbols = [s for s in watch.get("symbols", []) if s.get("enabled", True)]
        cap = int(watch.get("in_play_max", rules.in_play_max))
        return cls(
            root=root,
            rules=rules,
            clocks=read_yaml(cfg / "clocks.yaml"),
            sessions=read_yaml(cfg / "sessions.yaml"),
            calendar=read_yaml(cfg / "calendar.yaml"),
            watchlist=symbols[:cap],
            llm=read_yaml(cfg / "llm.yaml"),
            paths={
                "rules": cfg / "rules.yaml",
                "clocks": cfg / "clocks.yaml",
                "sessions": cfg / "sessions.yaml",
                "calendar": cfg / "calendar.yaml",
                "watchlist": cfg / "watchlist.yaml",
                "llm": cfg / "llm.yaml",
                "card": root / "prompts" / "card.txt",
                "positions": root / "state" / "positions.yaml",
            },
        )

    def manifest_files(self) -> dict[str, str]:
        keys = ("rules", "clocks", "sessions", "calendar", "watchlist")
        return {name: file_sha256(self.paths[name]) for name in keys}


def load_positions(path: Path) -> dict[str, bool]:
    raw = read_yaml(path)
    marked = raw.get("positions") or {}
    return {symbol: bool(flag) for symbol, flag in marked.items()}
