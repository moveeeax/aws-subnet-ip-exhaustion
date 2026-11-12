from __future__ import annotations

import pytest

FIXTURES = "tests/fixtures"


def eni(
    index: int,
    subnet_id: str,
    interface_type: str = "interface",
    description: str = "",
    instance: bool = True,
    secondaries: int | None = None,
    prefixes: int = 0,
) -> dict:
    """One ENI. ``secondaries`` adds a ``PrivateIpAddresses`` list (primary plus
    that many), ``prefixes`` adds delegated /28s; without either the record looks
    like a payload saved before addresses were collected."""
    record: dict = {
        "NetworkInterfaceId": f"eni-{subnet_id[-4:]}{index:04d}",
        "SubnetId": subnet_id,
        "InterfaceType": interface_type,
        "Description": description,
        "Status": "in-use",
    }
    if instance and interface_type == "interface" and not description:
        record["Attachment"] = {"InstanceId": f"i-{index:08x}"}
    if secondaries is not None:
        record["PrivateIpAddresses"] = [
            {"PrivateIpAddress": f"10.{index // 256 % 256}.{index % 256}.{n + 4}", "Primary": n == 0}
            for n in range(secondaries + 1)
        ]
    if prefixes:
        record["Ipv4Prefixes"] = [{"Ipv4Prefix": f"10.200.{index % 256}.{16 * n}/28"} for n in range(prefixes)]
    return record


def make_payload(cidr: str, eni_count: int, subnet_id: str = "subnet-0aaa", **subnet_extra) -> dict:
    """A payload with one subnet holding ``eni_count`` plain instance ENIs."""
    subnet = {
        "SubnetId": subnet_id,
        "VpcId": "vpc-0demo",
        "CidrBlock": cidr,
        "AvailabilityZone": "eu-central-1a",
        "Tags": [{"Key": "Name", "Value": "demo"}],
    }
    subnet.update(subnet_extra)
    return {
        "region": "eu-central-1",
        "subnets": [subnet],
        "network_interfaces": [eni(i, subnet_id) for i in range(eni_count)],
    }


@pytest.fixture
def slash24_200() -> dict:
    """The acceptance case: a /24 with 200 ENIs in it."""
    return make_payload("10.0.1.0/24", 200)
