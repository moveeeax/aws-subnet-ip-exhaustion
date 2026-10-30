"""Command line entry point.

Exit codes: ``0`` clean, ``1`` findings at or above ``--fail-on``, ``2`` the tool
could not do its job (bad arguments, unreadable history, failed API call).
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .analyze import SEVERITY_ORDER, Thresholds, analyse, worst_severity
from .collect import CollectError, collect, load_payload
from .history import HistoryError, append, group_by_subnet, load, now_iso, snapshot_from_subnet
from .report import render

EXIT_OK = 0
EXIT_FINDINGS = 1
EXIT_ERROR = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aws-subnet-ip-exhaustion",
        description=(
            "Report real IP utilisation per VPC subnet and project when each one runs out, "
            "counting the ENIs that actually consume the addresses."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    scope = parser.add_argument_group("scope")
    scope.add_argument("--region", help="AWS region (defaults to the session region)")
    scope.add_argument("--profile", help="AWS profile name")
    scope.add_argument(
        "--vpc-id", action="append", default=[], metavar="VPC", help="limit to a VPC (repeatable)"
    )
    scope.add_argument(
        "--subnet-id", action="append", default=[], metavar="SUBNET", help="limit to a subnet (repeatable)"
    )
    scope.add_argument(
        "--tag", action="append", default=[], metavar="KEY=VALUE", help="subnet tag filter (repeatable)"
    )
    scope.add_argument(
        "--from-json", metavar="PATH", help="analyse a saved payload instead of calling AWS"
    )

    trend = parser.add_argument_group("history and forecast")
    trend.add_argument(
        "--history", metavar="PATH", help="newline-delimited JSON history file to read and append to"
    )
    trend.add_argument(
        "--no-write", action="store_true", help="read the history file but do not append this run"
    )
    trend.add_argument(
        "--trend-window",
        type=float,
        default=30.0,
        metavar="DAYS",
        help="only fit snapshots from the last DAYS days (default 30)",
    )
    trend.add_argument(
        "--horizon-days",
        type=float,
        default=30.0,
        metavar="DAYS",
        help="a projection landing inside DAYS days is critical (default 30)",
    )

    limits = parser.add_argument_group("thresholds")
    limits.add_argument(
        "--warn-pct", type=float, default=75.0, metavar="PCT", help="warn at this utilisation (default 75)"
    )
    limits.add_argument(
        "--critical-pct",
        type=float,
        default=90.0,
        metavar="PCT",
        help="critical at this utilisation (default 90)",
    )

    out = parser.add_argument_group("output")
    out.add_argument(
        "--format", choices=("text", "json", "markdown"), default="text", help="output format (default text)"
    )
    out.add_argument(
        "--fail-on",
        choices=("warn", "critical", "none"),
        default="none",
        help="exit 1 when anything reaches this severity (default none)",
    )
    out.add_argument(
        "--save-payload", metavar="PATH", help="write the collected payload for later --from-json runs"
    )
    return parser


def _exit_code(report: dict, fail_on: str) -> int:
    if fail_on == "none":
        return EXIT_OK
    if SEVERITY_ORDER[worst_severity(report)] <= SEVERITY_ORDER[fail_on]:
        return EXIT_FINDINGS
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.warn_pct > args.critical_pct:
        print("error: --warn-pct must not exceed --critical-pct", file=sys.stderr)
        return EXIT_ERROR

    try:
        if args.from_json:
            payload = load_payload(args.from_json)
        else:
            payload = collect(
                region=args.region,
                vpc_ids=args.vpc_id,
                subnet_ids=args.subnet_id,
                tags=args.tag,
                profile=args.profile,
            )
    except CollectError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    history_by_subnet: dict[str, list[dict]] = {}
    try:
        if args.history:
            history_by_subnet = group_by_subnet(load(args.history))
    except HistoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    now = now_iso()
    report = analyse(
        payload,
        history_by_subnet=history_by_subnet,
        thresholds=Thresholds(
            warn_pct=args.warn_pct,
            critical_pct=args.critical_pct,
            horizon_days=args.horizon_days,
            trend_window_days=args.trend_window,
        ),
        now=now,
    )

    try:
        if args.history and not args.no_write:
            append(args.history, [snapshot_from_subnet(s, now) for s in report["subnets"]])
        if args.save_payload:
            with open(args.save_payload, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, default=str)
    except (HistoryError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    sys.stdout.write(render(report, args.format))
    return _exit_code(report, args.fail_on)


def run() -> None:  # pragma: no cover - console-script shim
    sys.exit(main())


if __name__ == "__main__":  # pragma: no cover
    run()
