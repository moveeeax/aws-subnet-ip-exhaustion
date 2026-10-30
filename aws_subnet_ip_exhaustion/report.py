"""Rendering: a text table for a terminal, JSON for pipelines, Markdown for PRs."""

from __future__ import annotations

import json

_SEVERITY_LABEL = {"critical": "CRITICAL", "warn": "WARN", "ok": "ok"}
_HEADERS = ("SEVERITY", "SUBNET", "CIDR", "AZ", "USED/USABLE", "FREE", "UTIL%", "DAYS LEFT", "TOP CONSUMER")

#: Past this, the projection is arithmetic rather than information. The exact
#: number stays in the JSON output; the table just stops pretending.
_DAYS_DISPLAY_CAP = 999


def _days(subnet: dict) -> str:
    value = subnet["days_to_exhaustion"]
    if value is None:
        return "-" if subnet["snapshots"] >= 2 else "n/a"
    if value > _DAYS_DISPLAY_CAP:
        return f">{_DAYS_DISPLAY_CAP}"
    return f"{value:g}"


def _top_consumer(subnet: dict) -> str:
    consumers = subnet["consumers"]
    if not consumers:
        return "-"
    name, count = max(consumers.items(), key=lambda kv: kv[1])
    return f"{name} {count}"


def _row(subnet: dict) -> tuple[str, ...]:
    return (
        _SEVERITY_LABEL[subnet["severity"]],
        subnet["name"] or subnet["subnet_id"],
        subnet["cidr"],
        subnet["availability_zone"],
        f"{subnet['in_use']}/{subnet['usable']}",
        str(subnet["free"]),
        f"{subnet['utilisation_pct']:.1f}",
        _days(subnet),
        _top_consumer(subnet),
    )


def render_text(report: dict) -> str:
    rows = [_row(s) for s in report["subnets"]]
    if not rows:
        return "No subnets matched.\n"

    widths = [len(h) for h in _HEADERS]
    for row in rows:
        widths = [max(w, len(cell)) for w, cell in zip(widths, row, strict=True)]

    lines = ["  ".join(h.ljust(w) for h, w in zip(_HEADERS, widths, strict=True)).rstrip()]
    lines.append("  ".join("-" * w for w in widths))
    for row in rows:
        lines.append("  ".join(cell.ljust(w) for cell, w in zip(row, widths, strict=True)).rstrip())

    summary = report["summary"]
    lines.append("")
    lines.append(
        f"{summary['subnets']} subnet(s): {summary['critical']} critical, "
        f"{summary['warn']} warn, {summary['ok']} ok"
    )

    flagged = [s for s in report["subnets"] if s["severity"] != "ok"]
    if flagged:
        lines.append("")
        for subnet in flagged:
            label = subnet["name"] or subnet["subnet_id"]
            for reason in subnet["reasons"]:
                lines.append(f"  {label}: {reason}")
    return "\n".join(lines) + "\n"


def render_markdown(report: dict) -> str:
    summary = report["summary"]
    lines = ["## Subnet IP exhaustion", ""]
    lines.append(
        f"`{summary['critical']}` critical, `{summary['warn']}` warn, `{summary['ok']}` ok "
        f"across {summary['subnets']} subnet(s)"
        + (f" in `{report['region']}`" if report["region"] else "")
        + "."
    )
    lines.append("")
    if not report["subnets"]:
        lines.append("No subnets matched.")
        return "\n".join(lines) + "\n"

    lines.append("| Severity | Subnet | CIDR | Used/Usable | Free | Util% | Days left | Top consumer |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for subnet in report["subnets"]:
        lines.append(
            "| {sev} | `{name}` | `{cidr}` | {used}/{usable} | {free} | {util:.1f} | {days} | {top} |".format(
                sev=_SEVERITY_LABEL[subnet["severity"]],
                name=subnet["name"] or subnet["subnet_id"],
                cidr=subnet["cidr"],
                used=subnet["in_use"],
                usable=subnet["usable"],
                free=subnet["free"],
                util=subnet["utilisation_pct"],
                days=_days(subnet),
                top=_top_consumer(subnet),
            )
        )

    flagged = [s for s in report["subnets"] if s["severity"] != "ok"]
    if flagged:
        lines.append("")
        for subnet in flagged:
            label = subnet["name"] or subnet["subnet_id"]
            for reason in subnet["reasons"]:
                lines.append(f"- `{label}`: {reason}")
    return "\n".join(lines) + "\n"


def render_json(report: dict) -> str:
    return json.dumps(report, indent=2, sort_keys=False) + "\n"


def render(report: dict, fmt: str) -> str:
    if fmt == "json":
        return render_json(report)
    if fmt == "markdown":
        return render_markdown(report)
    return render_text(report)
