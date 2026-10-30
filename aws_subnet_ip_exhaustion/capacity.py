"""Subnet CIDR arithmetic and ENI consumer classification.

AWS reserves five addresses in every subnet: the network address, the VPC router
(.1), the Amazon-provided DNS (.2), a future-use address (.3) and the broadcast
address. ``AvailableIpAddressCount`` already accounts for them, but it tells you
nothing about the size of the hole you are in, so the arithmetic is done here.
"""

from __future__ import annotations

import ipaddress

RESERVED_PER_SUBNET = 5

#: Interface types that map directly onto a consumer, no description needed.
_TYPE_MAP = {
    "nat_gateway": "nat_gateway",
    "natgateway": "nat_gateway",
    "vpc_endpoint": "vpc_endpoint",
    "vpcendpoint": "vpc_endpoint",
    "network_load_balancer": "load_balancer",
    "gateway_load_balancer": "load_balancer",
    "gateway_load_balancer_endpoint": "load_balancer",
    "lambda": "lambda",
    "transit_gateway": "transit_gateway",
    "efa": "instance",
    "branch": "eks_pod",
    "trunk": "eks_pod",
    "global_accelerator_managed": "load_balancer",
    "api_gateway_managed": "api_gateway",
}

#: Description prefixes for ENIs that AWS reports as a plain ``interface``.
_DESCRIPTION_PREFIXES = (
    ("aws-k8s-", "eks_pod"),
    ("amazon eks", "eks_control_plane"),
    ("elb app/", "load_balancer"),
    ("elb net/", "load_balancer"),
    ("elb ", "load_balancer"),
    ("interface for nat gateway", "nat_gateway"),
    ("vpc endpoint interface", "vpc_endpoint"),
    ("rdsnetworkinterface", "rds"),
    ("aws lambda vpc eni", "lambda"),
    ("arn:aws:ecs", "ecs_task"),
    ("efa", "instance"),
    ("amazon elasticache", "elasticache"),
    ("directoryservice", "directory_service"),
)

#: Stable column order for consumer breakdowns, so reports do not reshuffle.
CONSUMER_ORDER = (
    "instance",
    "eks_pod",
    "eks_control_plane",
    "nat_gateway",
    "vpc_endpoint",
    "load_balancer",
    "lambda",
    "rds",
    "ecs_task",
    "elasticache",
    "transit_gateway",
    "api_gateway",
    "directory_service",
    "other",
)


def subnet_total_addresses(cidr: str) -> int:
    """Return the raw address count of a CIDR block, reserved addresses included."""
    return ipaddress.ip_network(cidr, strict=False).num_addresses


def usable_addresses(cidr: str) -> int:
    """Return the addresses an ENI can actually take in this subnet."""
    return max(subnet_total_addresses(cidr) - RESERVED_PER_SUBNET, 0)


def classify_eni(eni: dict) -> str:
    """Map one ENI onto the thing that is really consuming the address.

    ``InterfaceType`` answers this for managed ENIs. For ``interface`` (the
    default) AWS hides the consumer in the description, which is why a bare
    instance count always disagrees with the address count on an EKS cluster.
    """
    itype = (eni.get("InterfaceType") or "interface").strip().lower().replace("-", "_")
    if itype in _TYPE_MAP:
        return _TYPE_MAP[itype]

    description = (eni.get("Description") or "").strip().lower()
    for prefix, consumer in _DESCRIPTION_PREFIXES:
        if description.startswith(prefix):
            return consumer

    attachment = eni.get("Attachment") or {}
    if attachment.get("InstanceId"):
        return "instance"
    if attachment.get("InstanceOwnerId", "").startswith("amazon-"):
        return "other"
    return "other"


def count_consumers(enis: list[dict]) -> dict[str, int]:
    """Count ENIs per consumer class, dropping classes with nothing in them."""
    counts: dict[str, int] = {}
    for eni in enis:
        consumer = classify_eni(eni)
        counts[consumer] = counts.get(consumer, 0) + 1
    return {name: counts[name] for name in CONSUMER_ORDER if name in counts}


def utilisation_pct(in_use: int, usable: int) -> float:
    """Utilisation as a percentage of usable addresses, one decimal place."""
    if usable <= 0:
        return 100.0
    return round(in_use / usable * 100, 1)
