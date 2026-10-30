from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aws_subnet_ip_exhaustion.analyze import Thresholds, analyse, forecast, worst_severity

from .conftest import eni, make_payload

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def history(subnet_id: str, points: list[tuple[float, int]]) -> dict[str, list[dict]]:
    """``points`` are ``(days_ago, free)`` pairs, newest last after sorting."""
    return {
        subnet_id: [
            {"timestamp": iso(days_ago), "subnet_id": subnet_id, "free": free, "in_use": 0, "usable": 251}
            for days_ago, free in sorted(points, reverse=True)
        ]
    }


def only(report: dict) -> dict:
    assert len(report["subnets"]) == 1
    return report["subnets"][0]


def test_a_slash24_with_200_enis_reports_the_acceptance_numbers(slash24_200):
    subnet = only(analyse(slash24_200))
    assert subnet["total"] == 256
    assert subnet["reserved"] == 5
    assert subnet["usable"] == 251
    assert subnet["in_use"] == 200
    assert subnet["free"] == 51
    assert subnet["utilisation_pct"] == 79.7
    assert subnet["severity"] == "warn"
    assert subnet["days_to_exhaustion"] is None


def test_losing_a_hundred_addresses_in_ten_days_projects_five_days_and_escalates(slash24_200):
    report = analyse(
        slash24_200,
        history_by_subnet=history("subnet-0aaa", [(10, 151)]),
        now=NOW.isoformat(),
    )
    subnet = only(report)
    assert subnet["snapshots"] == 2
    assert subnet["rate_per_day"] == pytest.approx(10.0, abs=0.1)
    assert subnet["days_to_exhaustion"] == pytest.approx(5.1, abs=0.3)
    # Utilisation alone would only be a warn; the projection is what escalates it.
    assert subnet["utilisation_pct"] < 90.0
    assert subnet["severity"] == "critical"
    assert any("projected exhaustion" in reason for reason in subnet["reasons"])


def test_a_flat_history_reports_null_instead_of_a_fabricated_number(slash24_200):
    report = analyse(
        slash24_200,
        history_by_subnet=history("subnet-0aaa", [(20, 51), (10, 51)]),
        now=NOW.isoformat(),
    )
    subnet = only(report)
    assert subnet["snapshots"] == 3
    assert subnet["days_to_exhaustion"] is None
    assert subnet["rate_per_day"] is None
    assert subnet["severity"] == "warn"


def test_a_single_snapshot_reports_null(slash24_200):
    subnet = only(analyse(slash24_200, now=NOW.isoformat()))
    assert subnet["snapshots"] == 1
    assert subnet["days_to_exhaustion"] is None


def test_a_recovering_subnet_gets_no_forecast(slash24_200):
    report = analyse(
        slash24_200,
        history_by_subnet=history("subnet-0aaa", [(10, 20)]),
        now=NOW.isoformat(),
    )
    assert only(report)["days_to_exhaustion"] is None


def test_snapshots_outside_the_trend_window_are_ignored(slash24_200):
    report = analyse(
        slash24_200,
        history_by_subnet=history("subnet-0aaa", [(400, 251), (10, 151)]),
        thresholds=Thresholds(trend_window_days=30.0),
        now=NOW.isoformat(),
    )
    subnet = only(report)
    assert subnet["snapshots"] == 2
    assert subnet["rate_per_day"] == pytest.approx(10.0, abs=0.1)


def test_utilisation_above_the_critical_threshold_is_critical_on_its_own():
    payload = make_payload("10.0.2.0/28", 10, subnet_id="subnet-0bbb")
    subnet = only(analyse(payload))
    assert subnet["usable"] == 11
    assert subnet["utilisation_pct"] == 90.9
    assert subnet["severity"] == "critical"


def test_a_quiet_subnet_is_ok():
    payload = make_payload("10.0.3.0/20", 40, subnet_id="subnet-0ccc")
    subnet = only(analyse(payload))
    assert subnet["utilisation_pct"] < 1.1
    assert subnet["severity"] == "ok"
    assert subnet["reasons"] == []


def test_a_mismatch_with_the_aws_reported_count_is_surfaced():
    payload = make_payload("10.0.4.0/24", 200, subnet_id="subnet-0ddd", AvailableIpAddressCount=40)
    subnet = only(analyse(payload))
    assert subnet["aws_reported_free"] == 40
    assert any("AWS reports 40 free" in reason for reason in subnet["reasons"])


def test_consumers_are_broken_down_per_subnet():
    payload = make_payload("10.0.5.0/24", 3, subnet_id="subnet-0eee")
    payload["network_interfaces"] += [
        eni(90, "subnet-0eee", interface_type="nat_gateway"),
        eni(91, "subnet-0eee", description="aws-K8S-i-0123"),
        eni(92, "subnet-0eee", description="ELB app/prod/0a1b"),
    ]
    subnet = only(analyse(payload))
    assert subnet["in_use"] == 6
    assert subnet["consumers"] == {"instance": 3, "eks_pod": 1, "nat_gateway": 1, "load_balancer": 1}


def test_report_is_sorted_by_severity_then_utilisation():
    payload = make_payload("10.0.6.0/20", 10, subnet_id="subnet-0quiet")
    busy = {
        "SubnetId": "subnet-0busy",
        "VpcId": "vpc-0demo",
        "CidrBlock": "10.0.7.0/28",
        "AvailabilityZone": "eu-central-1b",
        "Tags": [],
    }
    warm = {
        "SubnetId": "subnet-0warm",
        "VpcId": "vpc-0demo",
        "CidrBlock": "10.0.8.0/24",
        "AvailabilityZone": "eu-central-1c",
        "Tags": [],
    }
    payload["subnets"] += [busy, warm]
    payload["network_interfaces"] += [eni(i, "subnet-0busy") for i in range(10)]
    payload["network_interfaces"] += [eni(i, "subnet-0warm") for i in range(200)]

    report = analyse(payload)
    assert [s["subnet_id"] for s in report["subnets"]] == ["subnet-0busy", "subnet-0warm", "subnet-0quiet"]
    assert report["summary"] == {"subnets": 3, "critical": 1, "warn": 1, "ok": 1}
    assert worst_severity(report) == "critical"


def test_forecast_needs_two_distinct_moments_in_time():
    assert forecast([], 51.0) == (None, None)
    assert forecast([(0.0, 51.0)], 51.0) == (None, None)
    assert forecast([(0.0, 60.0), (0.0, 51.0)], 51.0) == (None, None)


def test_forecast_of_an_already_full_subnet_is_zero_days():
    rate, days = forecast([(-10.0, 20.0), (0.0, 0.0)], 0.0)
    assert rate == pytest.approx(2.0, abs=0.01)
    assert days == 0.0
