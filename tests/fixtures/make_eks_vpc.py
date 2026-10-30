#!/usr/bin/env python3
"""Generate tests/fixtures/eks-vpc.json: the offline payload used by the tests and the README.

Three subnets in one VPC, shaped like an EKS cluster that is about to run out of
pod addresses: a busy /24 pod subnet, a small /28 that is already critical, and a
quiet /20 with plenty of room.
"""

from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

SUBNETS = [
    {
        "SubnetId": "subnet-0a1b2c3d4e5f60001",
        "VpcId": "vpc-0a1b2c3d4e5f6000",
        "CidrBlock": "10.0.1.0/24",
        "AvailabilityZone": "eu-central-1a",
        "AvailableIpAddressCount": 51,
        "Tags": [{"Key": "Name", "Value": "eks-pods-1a"}, {"Key": "Environment", "Value": "prod"}],
    },
    {
        "SubnetId": "subnet-0a1b2c3d4e5f60002",
        "VpcId": "vpc-0a1b2c3d4e5f6000",
        "CidrBlock": "10.0.2.0/28",
        "AvailabilityZone": "eu-central-1b",
        "AvailableIpAddressCount": 1,
        "Tags": [{"Key": "Name", "Value": "public-1b"}, {"Key": "Environment", "Value": "prod"}],
    },
    {
        "SubnetId": "subnet-0a1b2c3d4e5f60003",
        "VpcId": "vpc-0a1b2c3d4e5f6000",
        "CidrBlock": "10.0.16.0/20",
        "AvailabilityZone": "eu-central-1c",
        "AvailableIpAddressCount": 4049,
        "Tags": [{"Key": "Name", "Value": "data-1c"}, {"Key": "Environment", "Value": "prod"}],
    },
]


def eni(number: int, subnet: str, **extra) -> dict:
    record = {
        "NetworkInterfaceId": f"eni-{number:017x}",
        "SubnetId": subnet,
        "InterfaceType": "interface",
        "Description": "",
        "Status": "in-use",
    }
    record.update(extra)
    return record


def main() -> None:
    enis: list[dict] = []
    number = 1

    pods = SUBNETS[0]["SubnetId"]
    # 12 nodes, 184 pod ENIs, the usual managed bits: 200 addresses gone.
    for _ in range(12):
        enis.append(eni(number, pods, Attachment={"InstanceId": f"i-{number:017x}"}))
        number += 1
    for _ in range(184):
        enis.append(eni(number, pods, Description=f"aws-K8S-i-{number:017x}"))
        number += 1
    enis.append(eni(number, pods, Description="Amazon EKS prod-cluster"))
    number += 1
    for name in ("app/prod-ingress/0a1b2c3d", "app/prod-ingress/0a1b2c3d", "net/prod-grpc/4d5e6f70"):
        enis.append(eni(number, pods, Description=f"ELB {name}"))
        number += 1

    public = SUBNETS[1]["SubnetId"]
    for nat in ("nat-0a1b2c3d4e5f6001", "nat-0a1b2c3d4e5f6002"):
        enis.append(eni(number, public, InterfaceType="nat_gateway", Description=f"Interface for NAT Gateway {nat}"))
        number += 1
    for _ in range(3):
        enis.append(eni(number, public, Description="ELB app/prod-ingress/0a1b2c3d"))
        number += 1
    for _ in range(5):
        enis.append(eni(number, public, Attachment={"InstanceId": f"i-{number:017x}"}))
        number += 1

    data = SUBNETS[2]["SubnetId"]
    for _ in range(26):
        enis.append(eni(number, data, Attachment={"InstanceId": f"i-{number:017x}"}))
        number += 1
    for _ in range(8):
        enis.append(eni(number, data, Description="RDSNetworkInterface"))
        number += 1
    for service in ("ecr.api", "ecr.dkr", "s3", "logs", "sts", "secretsmanager", "ssm", "ssmmessages"):
        enis.append(eni(number, data, InterfaceType="vpc_endpoint", Description=f"VPC Endpoint Interface vpce-{service}"))
        number += 1

    payload = {"region": "eu-central-1", "subnets": SUBNETS, "network_interfaces": enis}
    target = os.path.join(HERE, "eks-vpc.json")
    with open(target, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1)
        handle.write("\n")
    print(f"wrote {target}: {len(SUBNETS)} subnets, {len(enis)} ENIs")


if __name__ == "__main__":
    main()
