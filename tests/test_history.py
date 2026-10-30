from __future__ import annotations

import json

import pytest

from aws_subnet_ip_exhaustion.history import (
    HistoryError,
    append,
    group_by_subnet,
    load,
    parse_timestamp,
    snapshot_from_subnet,
)


def test_a_missing_history_file_is_an_empty_history(tmp_path):
    assert load(str(tmp_path / "nope.ndjson")) == []


def test_round_trip_through_the_file(tmp_path):
    path = str(tmp_path / "h.ndjson")
    append(path, [{"timestamp": "2026-10-20T12:00:00+00:00", "subnet_id": "subnet-a", "free": 150, "in_use": 101, "usable": 251}])
    append(path, [{"timestamp": "2026-10-30T12:00:00+00:00", "subnet_id": "subnet-a", "free": 51, "in_use": 200, "usable": 251}])
    records = load(path)
    assert [r["free"] for r in records] == [150, 51]


def test_history_is_created_in_a_missing_directory(tmp_path):
    path = str(tmp_path / "nested" / "deeper" / "h.ndjson")
    append(path, [{"timestamp": "2026-10-30T12:00:00+00:00", "subnet_id": "subnet-a", "free": 1}])
    assert len(load(path)) == 1


def test_grouping_sorts_each_subnet_oldest_first():
    grouped = group_by_subnet(
        [
            {"timestamp": "2026-10-30T12:00:00Z", "subnet_id": "subnet-a", "free": 51},
            {"timestamp": "2026-10-10T12:00:00Z", "subnet_id": "subnet-a", "free": 251},
            {"timestamp": "2026-10-20T12:00:00Z", "subnet_id": "subnet-b", "free": 7},
        ]
    )
    assert [r["free"] for r in grouped["subnet-a"]] == [251, 51]
    assert list(grouped) == ["subnet-a", "subnet-b"]


def test_blank_lines_are_skipped(tmp_path):
    path = tmp_path / "h.ndjson"
    path.write_text('\n{"timestamp": "2026-10-30T12:00:00Z", "subnet_id": "s", "free": 1}\n\n', encoding="utf-8")
    assert len(load(str(path))) == 1


@pytest.mark.parametrize(
    "line",
    [
        "{not json}",
        '["a list"]',
        '{"subnet_id": "s", "free": 1}',
        '{"timestamp": "yesterday", "subnet_id": "s", "free": 1}',
    ],
)
def test_a_broken_history_line_raises_history_error(tmp_path, line):
    path = tmp_path / "h.ndjson"
    path.write_text(line + "\n", encoding="utf-8")
    with pytest.raises(HistoryError):
        load(str(path))


def test_a_trailing_z_is_read_as_utc():
    assert parse_timestamp("2026-10-30T12:00:00Z") == parse_timestamp("2026-10-30T12:00:00+00:00")


def test_a_naive_timestamp_is_assumed_utc():
    assert parse_timestamp("2026-10-30T12:00:00").utcoffset().total_seconds() == 0


def test_snapshot_keeps_only_what_the_forecast_needs():
    record = snapshot_from_subnet(
        {"subnet_id": "subnet-a", "free": 51, "in_use": 200, "usable": 251, "consumers": {"instance": 200}},
        "2026-10-30T12:00:00+00:00",
    )
    assert record == {
        "timestamp": "2026-10-30T12:00:00+00:00",
        "subnet_id": "subnet-a",
        "free": 51,
        "in_use": 200,
        "usable": 251,
    }
    assert json.loads(json.dumps(record)) == record
