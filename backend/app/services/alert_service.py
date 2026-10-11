from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

DISCLAIMER = (
    "This is a HeatShield decision-support assessment using demo or integrated data — "
    "not an official government emergency warning. Follow IMD, state disaster management, "
    "and local authorities for official alerts."
)


class AlertService:
    def build_alerts(self, assessments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        alerts: list[dict[str, Any]] = []
        for a in assessments:
            level = a.get("riskLevel")
            if level not in ("high", "critical"):
                continue
            loc = a["location"]
            factors = a.get("contributingFactors") or []
            reason = ", ".join(f["label"] for f in factors[:3]) or "Elevated composite risk score"
            scenario = a.get("scenario")
            if scenario == "flood_glof":
                title = "Elevated flood-related risk (HeatShield assessment)"
            else:
                title = "Elevated extreme heat risk (HeatShield assessment)"

            alerts.append(
                {
                    "id": str(uuid4()),
                    "locationId": loc["id"],
                    "locationName": loc["name"],
                    "severity": "critical" if level == "critical" else "high",
                    "title": title,
                    "reason": reason,
                    "potentialExposure": f"Demo exposure indices for {loc['name']} — see location panel.",
                    "recommended": (a.get("recommendedActions") or [""])[0],
                    "disclaimer": DISCLAIMER,
                    "createdAt": datetime.now(timezone.utc).isoformat(),
                    "simulationActive": bool(a.get("simulationActive")),
                }
            )
        return alerts
