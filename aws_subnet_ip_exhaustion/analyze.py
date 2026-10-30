"""Capacity, forecast and severity for every subnet in a collected payload.

The analyser never talks to AWS. It takes the plain dicts the collector (or
``--from-json``) produces, so every rule in here is testable against a fixture.
"""

from __future__ import annotations

from dataclasses import dataclass

from .capacity import (
    count_consumers,
    subnet_total_addresses,
    usable_addresses,
    utilisation_pct,
)
from .history import parse_timestamp

SEVERITY_ORDER = {"critical": 0, "warn": 1, "ok": 2}

#: A rate slower than this is noise, not a trend: one address a month.
MIN_RATE_PER_DAY = 0.03


@dataclass(frozen=True)
class Thresholds:
    """Everything that turns numbers into a severity."""

    warn_pct: float = 75.0
    critical_pct: float = 90.0
    horizon_days: float = 30.0
    trend_window_days: float = 30.0


def _name_from_tags(tags: list[dict] | None) -> str:
    for tag in tags or []:
        if tag.get("Key") == "Name":
            return str(tag.get("Value") or "")
    return ""


def forecast(points: list[tuple[float, float]], free_now: float) -> tuple[float | None, float | None]:
    """Fit a linear trend over ``(day, free)`` points.

    Returns ``(rate_per_day, days_to_exhaustion)`` where the rate is positive
    when addresses are being consumed. Fewer than two distinct points in time,
    or a flat or recovering trend, gives ``(None, None)`` — a forecast from a
    single sample would be a fabricated number, and reporting ``null`` is the
    whole point of keeping a history.
    """
    if len(points) < 2:
        return None, None

    days = [p[0] for p in points]
    span = max(days) - min(days)
    if span <= 0:
        return None, None

    free = [p[1] for p in points]
    n = float(len(points))
    mean_day = sum(days) / n
    mean_free = sum(free) / n
    variance = sum((d - mean_day) ** 2 for d in days)
    if variance <= 0:
        return None, None

    covariance = sum((d - mean_day) * (f - mean_free) for d, f in zip(days, free, strict=True))
    slope = covariance / variance
    rate = -slope
    if rate < MIN_RATE_PER_DAY:
        return None, None
    if free_now <= 0:
        return round(rate, 2), 0.0
    return round(rate, 2), round(free_now / rate, 1)


def _points_for_subnet(records: list[dict], window_days: float) -> list[tuple[float, float]]:
    """Convert history records into ``(day, free)`` points inside the window."""
    if not records:
        return []
    newest = parse_timestamp(str(records[-1]["timestamp"]))
    points: list[tuple[float, float]] = []
    for record in records:
        age_days = (newest - parse_timestamp(str(record["timestamp"]))).total_seconds() / 86400.0
        if age_days > window_days:
            continue
        points.append((-age_days, float(record["free"])))
    points.sort()
    return points


def severity_for(
    utilisation: float,
    days_to_exhaustion: float | None,
    extra_reasons: list[str],
    thresholds: Thresholds,
) -> tuple[str, list[str]]:
    """Classify one subnet and return the reasons behind the classification."""
    reasons = list(extra_reasons)
    level = "ok"

    if utilisation >= thresholds.critical_pct:
        level = "critical"
        reasons.append(f"utilisation {utilisation}% at or above critical {thresholds.critical_pct}%")
    elif utilisation >= thresholds.warn_pct:
        level = "warn"
        reasons.append(f"utilisation {utilisation}% at or above warn {thresholds.warn_pct}%")

    if days_to_exhaustion is not None and days_to_exhaustion <= thresholds.horizon_days:
        level = "critical"
        reasons.append(
            f"projected exhaustion in {days_to_exhaustion} days, inside the {thresholds.horizon_days}-day horizon"
        )

    if reasons and level == "ok":
        level = "warn"
    return level, reasons


def analyse(
    payload: dict,
    history_by_subnet: dict[str, list[dict]] | None = None,
    thresholds: Thresholds | None = None,
    now: str | None = None,
) -> dict:
    """Turn a collected payload into the full report structure."""
    thresholds = thresholds or Thresholds()
    history_by_subnet = history_by_subnet or {}

    enis_by_subnet: dict[str, list[dict]] = {}
    for eni in payload.get("network_interfaces", []):
        subnet_id = eni.get("SubnetId")
        if subnet_id:
            enis_by_subnet.setdefault(subnet_id, []).append(eni)

    subnets = []
    for raw in payload.get("subnets", []):
        subnet_id = raw["SubnetId"]
        cidr = raw["CidrBlock"]
        enis = enis_by_subnet.get(subnet_id, [])
        total = subnet_total_addresses(cidr)
        usable = usable_addresses(cidr)
        in_use = len(enis)
        free = usable - in_use
        utilisation = utilisation_pct(in_use, usable)

        records = list(history_by_subnet.get(subnet_id, []))
        if now is not None:
            records = records + [
                {"timestamp": now, "subnet_id": subnet_id, "free": free, "in_use": in_use, "usable": usable}
            ]
        points = _points_for_subnet(records, thresholds.trend_window_days)
        rate, days_left = forecast(points, float(free))

        extra_reasons = []
        reported = raw.get("AvailableIpAddressCount")
        if reported is not None and int(reported) != free:
            extra_reasons.append(f"AWS reports {int(reported)} free, ENI accounting says {free}")

        level, reasons = severity_for(utilisation, days_left, extra_reasons, thresholds)
        subnets.append(
            {
                "subnet_id": subnet_id,
                "name": _name_from_tags(raw.get("Tags")),
                "vpc_id": raw.get("VpcId", ""),
                "availability_zone": raw.get("AvailabilityZone", ""),
                "cidr": cidr,
                "total": total,
                "reserved": total - usable,
                "usable": usable,
                "in_use": in_use,
                "free": free,
                "utilisation_pct": utilisation,
                "aws_reported_free": int(reported) if reported is not None else None,
                "consumers": count_consumers(enis),
                "snapshots": len(points),
                "rate_per_day": rate,
                "days_to_exhaustion": days_left,
                "severity": level,
                "reasons": reasons,
            }
        )

    subnets.sort(key=lambda s: (SEVERITY_ORDER[s["severity"]], -s["utilisation_pct"], s["subnet_id"]))
    counts = {"critical": 0, "warn": 0, "ok": 0}
    for subnet in subnets:
        counts[subnet["severity"]] += 1

    return {
        "region": payload.get("region", ""),
        "generated_at": now or "",
        "thresholds": {
            "warn_pct": thresholds.warn_pct,
            "critical_pct": thresholds.critical_pct,
            "horizon_days": thresholds.horizon_days,
            "trend_window_days": thresholds.trend_window_days,
        },
        "summary": {"subnets": len(subnets), **counts},
        "subnets": subnets,
    }


def worst_severity(report: dict) -> str:
    """The highest severity present in a report."""
    if report["summary"]["critical"]:
        return "critical"
    if report["summary"]["warn"]:
        return "warn"
    return "ok"
