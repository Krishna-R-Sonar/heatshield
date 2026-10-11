from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass
class RiskThresholds:
    level_low_max: float
    level_moderate_max: float
    level_high_max: float
    flood: dict[str, Any]
    heat: dict[str, Any]

    @classmethod
    def load(cls, path: Path | None = None) -> RiskThresholds:
        if path is None:
            path = Path(__file__).parent / "thresholds.yaml"
        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        levels = raw["risk_levels"]
        return cls(
            level_low_max=float(levels["low_max"]),
            level_moderate_max=float(levels["moderate_max"]),
            level_high_max=float(levels["high_max"]),
            flood=raw["flood"],
            heat=raw["heat"],
        )

    def score_to_level(self, score: float) -> str:
        if score <= self.level_low_max:
            return "low"
        if score <= self.level_moderate_max:
            return "moderate"
        if score <= self.level_high_max:
            return "high"
        return "critical"


DEFAULT_THRESHOLDS = RiskThresholds.load()
