from __future__ import annotations

import json
import os

import pytest

from aws_subnet_ip_exhaustion.cli import main

from .conftest import make_payload

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
PAYLOAD = os.path.join(FIXTURES, "eks-vpc.json")
HISTORY_LOSING = os.path.join(FIXTURES, "history-losing.ndjson")
HISTORY_BROKEN = os.path.join(FIXTURES, "history-broken.ndjson")

POD_SUBNET = "subnet-0a1b2c3d4e5f60001"


def run(capsys, *argv: str) -> tuple[int, str, str]:
    code = main(["--from-json", PAYLOAD, *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def quiet_payload(tmp_path) -> str:
    path = tmp_path / "quiet.json"
    path.write_text(json.dumps(make_payload("10.9.0.0/20", 12)), encoding="utf-8")
    return str(path)


def test_offline_run_reproduces_the_whole_report(capsys):
    code, out, err = run(capsys)
    assert code == 0
    assert err == ""
    assert "CRITICAL" in out
    assert "eks-pods-1a" in out
    assert "79.7" in out
    assert "3 subnet(s): 1 critical, 1 warn, 1 ok" in out


def test_json_output_carries_the_acceptance_numbers(capsys):
    code, out, _ = run(capsys, "--format", "json")
    assert code == 0
    report = json.loads(out)
    assert report["summary"] == {"subnets": 3, "critical": 1, "warn": 1, "ok": 1}

    pods = next(s for s in report["subnets"] if s["subnet_id"] == POD_SUBNET)
    assert (pods["usable"], pods["in_use"], pods["free"]) == (251, 200, 51)
    assert pods["utilisation_pct"] == 79.7
    assert pods["severity"] == "warn"
    assert pods["days_to_exhaustion"] is None
    assert pods["consumers"]["eks_pod"] == 184


def test_markdown_output_is_a_table(capsys):
    code, out, _ = run(capsys, "--format", "markdown")
    assert code == 0
    assert out.startswith("## Subnet IP exhaustion")
    assert "| Severity | Subnet | CIDR |" in out
    assert "`10.0.2.0/28`" in out


@pytest.mark.parametrize("fail_on,expected", [("none", 0), ("warn", 1), ("critical", 1)])
def test_fail_on_sets_the_exit_code(capsys, fail_on, expected):
    code, _, _ = run(capsys, "--fail-on", fail_on, "--format", "json")
    assert code == expected


@pytest.mark.parametrize("fail_on,expected", [("none", 0), ("critical", 0), ("warn", 0)])
def test_a_clean_account_exits_zero_whatever_fail_on_says(capsys, tmp_path, fail_on, expected):
    code = main(["--from-json", quiet_payload(tmp_path), "--fail-on", fail_on, "--format", "json"])
    capsys.readouterr()
    assert code == expected


def test_a_warn_only_account_does_not_trip_fail_on_critical(capsys, tmp_path):
    path = tmp_path / "warm.json"
    path.write_text(json.dumps(make_payload("10.8.0.0/24", 200)), encoding="utf-8")
    assert main(["--from-json", str(path), "--fail-on", "critical", "--format", "json"]) == 0
    assert main(["--from-json", str(path), "--fail-on", "warn", "--format", "json"]) == 1
    capsys.readouterr()


def test_history_turns_into_a_projection_and_escalates(capsys):
    code, out, _ = run(capsys, "--history", HISTORY_LOSING, "--no-write", "--trend-window", "3650", "--format", "json")
    assert code == 0
    pods = next(s for s in json.loads(out)["subnets"] if s["subnet_id"] == POD_SUBNET)
    assert pods["snapshots"] == 4
    assert pods["rate_per_day"] > 0
    assert pods["days_to_exhaustion"] is not None
    assert pods["severity"] == "critical"
    assert any("projected exhaustion" in reason for reason in pods["reasons"])


def test_each_run_appends_one_snapshot_per_subnet(capsys, tmp_path):
    path = tmp_path / "h.ndjson"
    assert run(capsys, "--history", str(path), "--format", "json")[0] == 0
    assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 3
    assert run(capsys, "--history", str(path), "--format", "json")[0] == 0
    assert len(path.read_text(encoding="utf-8").strip().splitlines()) == 6


def test_no_write_leaves_the_history_untouched(capsys, tmp_path):
    path = tmp_path / "h.ndjson"
    run(capsys, "--history", str(path), "--no-write")
    assert not path.exists()


def test_an_unreadable_history_file_exits_two_with_a_message(capsys):
    code, out, err = run(capsys, "--history", HISTORY_BROKEN)
    assert code == 2
    assert out == ""
    assert err.startswith("error: ")
    assert "Traceback" not in err


def test_a_history_path_that_is_a_directory_exits_two(capsys, tmp_path):
    code, _, err = run(capsys, "--history", str(tmp_path))
    assert code == 2
    assert err.startswith("error: ")


def test_a_missing_payload_exits_two(capsys, tmp_path):
    code = main(["--from-json", str(tmp_path / "gone.json")])
    err = capsys.readouterr().err
    assert code == 2
    assert "cannot read" in err


def test_a_malformed_payload_exits_two(capsys, tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"subnets": [{"SubnetId": "subnet-a"}]}', encoding="utf-8")
    code = main(["--from-json", str(path)])
    err = capsys.readouterr().err
    assert code == 2
    assert "CidrBlock" in err


def test_contradictory_thresholds_exit_two(capsys):
    code, _, err = run(capsys, "--warn-pct", "95", "--critical-pct", "90")
    assert code == 2
    assert "--warn-pct" in err


def test_save_payload_round_trips(capsys, tmp_path):
    saved = tmp_path / "saved.json"
    assert run(capsys, "--save-payload", str(saved), "--format", "json")[0] == 0
    assert main(["--from-json", str(saved), "--format", "json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["summary"]["subnets"] == 3


def test_an_empty_scope_says_so_instead_of_printing_an_empty_table(capsys, tmp_path):
    path = tmp_path / "none.json"
    path.write_text(json.dumps({"region": "eu-central-1", "subnets": []}), encoding="utf-8")
    assert main(["--from-json", str(path)]) == 0
    assert "No subnets matched." in capsys.readouterr().out
