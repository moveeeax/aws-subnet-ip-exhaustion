from __future__ import annotations

import json

from aws_subnet_ip_exhaustion.analyze import analyse
from aws_subnet_ip_exhaustion.report import render, render_markdown, render_text

from .conftest import eni, make_payload


def report_with_everything() -> dict:
    payload = make_payload("10.0.1.0/24", 200, subnet_id="subnet-0warm")
    payload["subnets"].append(
        {
            "SubnetId": "subnet-0full",
            "VpcId": "vpc-0demo",
            "CidrBlock": "10.0.2.0/28",
            "AvailabilityZone": "eu-central-1b",
            "Tags": [{"Key": "Name", "Value": "public-1b"}],
        }
    )
    payload["network_interfaces"] += [eni(i, "subnet-0full", interface_type="nat_gateway") for i in range(10)]
    return analyse(payload)


def test_text_table_is_aligned_and_sorted():
    out = render_text(report_with_everything())
    lines = out.splitlines()
    assert lines[0].startswith("SEVERITY")
    assert lines[2].startswith("CRITICAL")
    assert lines[3].startswith("WARN")

    # Every row starts its CIDR column exactly under the header.
    cidr_column = lines[0].index("CIDR")
    assert lines[2][cidr_column:].startswith("10.0.2.0/28")
    assert lines[3][cidr_column:].startswith("10.0.1.0/24")
    assert set(lines[1]) == {"-", " "}
    assert "2 subnet(s): 1 critical, 1 warn, 0 ok" in out


def test_reasons_are_listed_under_the_table():
    out = render_text(report_with_everything())
    assert "public-1b: utilisation 90.9% at or above critical 90.0%" in out


def test_days_left_distinguishes_no_history_from_no_trend():
    payload = make_payload("10.0.3.0/24", 200)
    assert "n/a" in render_text(analyse(payload))

    history = {"subnet-0aaa": [{"timestamp": "2026-10-20T09:00:00Z", "subnet_id": "subnet-0aaa", "free": 51}]}
    flat = analyse(payload, history_by_subnet=history, now="2026-10-30T09:00:00Z")
    assert flat["subnets"][0]["days_to_exhaustion"] is None
    assert flat["subnets"][0]["snapshots"] == 2
    row = render_text(flat).splitlines()[2]
    assert row.split()[-3] == "-"


def test_a_forecast_decades_out_is_not_printed_as_a_precise_number():
    payload = make_payload("10.0.4.0/20", 42)
    history = {"subnet-0aaa": [{"timestamp": "2026-09-30T09:00:00Z", "subnet_id": "subnet-0aaa", "free": 4061}]}
    report = analyse(payload, history_by_subnet=history, now="2026-10-30T09:00:00Z")
    subnet = report["subnets"][0]
    assert subnet["days_to_exhaustion"] > 999
    assert ">999" in render_text(report)
    assert ">999" in render_markdown(report)


def test_markdown_escapes_nothing_it_does_not_need_to():
    out = render_markdown(report_with_everything())
    assert out.count("|") > 10
    assert "`10.0.2.0/28`" in out
    assert "- `public-1b`:" in out


def test_an_empty_report_renders_in_every_format():
    empty = analyse({"region": "eu-central-1", "subnets": []})
    assert render(empty, "text") == "No subnets matched.\n"
    assert "No subnets matched." in render(empty, "markdown")
    assert json.loads(render(empty, "json"))["summary"]["subnets"] == 0


def test_unknown_format_falls_back_to_text():
    assert render(report_with_everything(), "something-else").startswith("SEVERITY")
