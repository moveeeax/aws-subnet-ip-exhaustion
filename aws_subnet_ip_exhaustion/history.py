"""Newline-delimited JSON history of per-subnet snapshots.

One line per subnet per run. Append-only on purpose: the file stays readable with
``tail`` and ``jq``, and a corrupted tail never destroys older snapshots.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime


class HistoryError(Exception):
    """Raised when a history file exists but cannot be used."""


def parse_timestamp(value: str) -> datetime:
    """Parse an ISO-8601 timestamp, treating a trailing ``Z`` as UTC."""
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def load(path: str) -> list[dict]:
    """Read every snapshot from ``path``; a missing file is an empty history."""
    if not os.path.exists(path):
        return []
    snapshots: list[dict] = []
    try:
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError as exc:
        raise HistoryError(f"cannot read history file {path}: {exc}") from exc

    for number, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise HistoryError(f"{path}:{number}: not valid JSON: {exc.msg}") from exc
        if not isinstance(record, dict):
            raise HistoryError(f"{path}:{number}: expected a JSON object")
        missing = {"timestamp", "subnet_id", "free"} - set(record)
        if missing:
            raise HistoryError(f"{path}:{number}: missing field(s): {', '.join(sorted(missing))}")
        try:
            parse_timestamp(str(record["timestamp"]))
        except ValueError as exc:
            raise HistoryError(f"{path}:{number}: bad timestamp: {exc}") from exc
        snapshots.append(record)
    return snapshots


def group_by_subnet(snapshots: list[dict]) -> dict[str, list[dict]]:
    """Bucket snapshots per subnet id, oldest first."""
    grouped: dict[str, list[dict]] = {}
    for record in snapshots:
        grouped.setdefault(str(record["subnet_id"]), []).append(record)
    for records in grouped.values():
        records.sort(key=lambda r: parse_timestamp(str(r["timestamp"])))
    return grouped


def snapshot_from_subnet(subnet: dict, timestamp: str) -> dict:
    """Build the history record for one analysed subnet."""
    return {
        "timestamp": timestamp,
        "subnet_id": subnet["subnet_id"],
        "free": subnet["free"],
        "in_use": subnet["in_use"],
        "usable": subnet["usable"],
    }


def append(path: str, records: list[dict]) -> None:
    """Append records to the history file, creating parent directories."""
    if not records:
        return
    parent = os.path.dirname(os.path.abspath(path))
    try:
        os.makedirs(parent, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError as exc:
        raise HistoryError(f"cannot write history file {path}: {exc}") from exc


def now_iso() -> str:
    """Current UTC time, seconds precision, with an explicit offset."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()
