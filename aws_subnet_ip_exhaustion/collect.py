"""Pull subnets and their ENIs from the EC2 API.

``boto3`` is imported inside the function on purpose: the analyser, the reports
and the whole test suite work on plain dicts, so nothing AWS-shaped is needed to
run them.
"""

from __future__ import annotations

import json

#: Only the fields the analyser reads are kept, so fixtures stay small.
_SUBNET_FIELDS = (
    "SubnetId",
    "VpcId",
    "CidrBlock",
    "AvailabilityZone",
    "AvailableIpAddressCount",
    "Tags",
)
_ENI_FIELDS = (
    "NetworkInterfaceId",
    "SubnetId",
    "InterfaceType",
    "Description",
    "Status",
    "Attachment",
)


class CollectError(Exception):
    """Raised when a payload cannot be collected or loaded."""


def parse_tag_filters(values: list[str]) -> list[dict]:
    """Turn ``Key=Value`` strings into EC2 tag filters."""
    filters = []
    for value in values:
        if "=" not in value:
            raise CollectError(f"--tag expects KEY=VALUE, got {value!r}")
        key, _, val = value.partition("=")
        key, val = key.strip(), val.strip()
        if not key or not val:
            raise CollectError(f"--tag expects KEY=VALUE, got {value!r}")
        filters.append({"Name": f"tag:{key}", "Values": [val]})
    return filters


def _trim(record: dict, fields: tuple[str, ...]) -> dict:
    return {field: record[field] for field in fields if field in record}


def collect(
    region: str | None = None,
    vpc_ids: list[str] | None = None,
    subnet_ids: list[str] | None = None,
    tags: list[str] | None = None,
    profile: str | None = None,
) -> dict:
    """Return ``{region, subnets, network_interfaces}`` for the selected scope."""
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise CollectError("boto3 is required for live collection; install it or use --from-json") from exc

    session = boto3.Session(profile_name=profile, region_name=region)
    ec2 = session.client("ec2")

    filters = parse_tag_filters(tags or [])
    if vpc_ids:
        filters.append({"Name": "vpc-id", "Values": list(vpc_ids)})

    subnet_kwargs: dict = {}
    if filters:
        subnet_kwargs["Filters"] = filters
    if subnet_ids:
        subnet_kwargs["SubnetIds"] = list(subnet_ids)

    try:
        subnets: list[dict] = []
        for page in ec2.get_paginator("describe_subnets").paginate(**subnet_kwargs):
            subnets.extend(_trim(s, _SUBNET_FIELDS) for s in page["Subnets"])

        selected = [s["SubnetId"] for s in subnets]
        enis: list[dict] = []
        # describe_network_interfaces caps a filter at a sane number of values,
        # so the subnet ids go in in batches rather than one giant filter.
        for batch in (selected[i : i + 100] for i in range(0, len(selected), 100)):
            eni_filters = [{"Name": "subnet-id", "Values": batch}]
            pages = ec2.get_paginator("describe_network_interfaces").paginate(Filters=eni_filters)
            for page in pages:
                enis.extend(_trim(e, _ENI_FIELDS) for e in page["NetworkInterfaces"])
    except (BotoCoreError, ClientError) as exc:
        raise CollectError(f"EC2 API call failed: {exc}") from exc

    return {
        "region": region or session.region_name or "",
        "subnets": subnets,
        "network_interfaces": enis,
    }


def load_payload(path: str) -> dict:
    """Load a saved payload for an offline run."""
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except OSError as exc:
        raise CollectError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CollectError(f"{path}: not valid JSON: {exc.msg} (line {exc.lineno})") from exc

    if not isinstance(payload, dict) or "subnets" not in payload:
        raise CollectError(f"{path}: expected an object with a 'subnets' list")
    if not isinstance(payload["subnets"], list):
        raise CollectError(f"{path}: 'subnets' must be a list")
    for subnet in payload["subnets"]:
        missing = {"SubnetId", "CidrBlock"} - set(subnet or {})
        if missing:
            raise CollectError(f"{path}: a subnet is missing {', '.join(sorted(missing))}")
    payload.setdefault("network_interfaces", [])
    if not isinstance(payload["network_interfaces"], list):
        raise CollectError(f"{path}: 'network_interfaces' must be a list")
    return payload
