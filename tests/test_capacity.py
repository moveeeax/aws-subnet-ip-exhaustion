from __future__ import annotations

import pytest

from aws_subnet_ip_exhaustion.capacity import (
    RESERVED_PER_SUBNET,
    classify_eni,
    count_consumers,
    subnet_total_addresses,
    usable_addresses,
    utilisation_pct,
)


@pytest.mark.parametrize(
    "cidr,total,usable",
    [
        ("10.0.0.0/24", 256, 251),
        ("10.0.0.0/20", 4096, 4091),
        ("10.0.0.0/28", 16, 11),
    ],
)
def test_cidr_arithmetic_subtracts_the_five_reserved_addresses(cidr, total, usable):
    assert subnet_total_addresses(cidr) == total
    assert usable_addresses(cidr) == usable
    assert total - usable == RESERVED_PER_SUBNET


def test_utilisation_matches_the_acceptance_number():
    assert utilisation_pct(200, 251) == 79.7


def test_utilisation_of_a_subnet_with_no_usable_space_is_full():
    assert utilisation_pct(0, 0) == 100.0


@pytest.mark.parametrize(
    "eni,expected",
    [
        ({"InterfaceType": "nat_gateway"}, "nat_gateway"),
        ({"InterfaceType": "vpc_endpoint"}, "vpc_endpoint"),
        ({"InterfaceType": "network_load_balancer"}, "load_balancer"),
        ({"InterfaceType": "lambda"}, "lambda"),
        ({"InterfaceType": "branch"}, "eks_pod"),
        ({"InterfaceType": "transit_gateway"}, "transit_gateway"),
    ],
)
def test_managed_interface_types_map_straight_to_a_consumer(eni, expected):
    assert classify_eni(eni) == expected


@pytest.mark.parametrize(
    "description,expected",
    [
        ("aws-K8S-i-0123456789abcdef0", "eks_pod"),
        ("Amazon EKS prod-cluster", "eks_control_plane"),
        ("ELB app/prod-ingress/0a1b2c3d", "load_balancer"),
        ("Interface for NAT Gateway nat-0a1b2c3d", "nat_gateway"),
        ("RDSNetworkInterface", "rds"),
        ("VPC Endpoint Interface vpce-0a1b2c3d", "vpc_endpoint"),
    ],
)
def test_plain_interfaces_are_classified_from_the_description(description, expected):
    assert classify_eni({"InterfaceType": "interface", "Description": description}) == expected


def test_an_attached_interface_without_a_known_description_is_an_instance():
    assert classify_eni({"InterfaceType": "interface", "Attachment": {"InstanceId": "i-0a1b"}}) == "instance"


def test_an_unattached_unknown_interface_is_other():
    assert classify_eni({"InterfaceType": "interface", "Description": "something custom"}) == "other"


def test_consumer_counts_use_a_stable_order_and_skip_empty_classes():
    counts = count_consumers(
        [
            {"InterfaceType": "nat_gateway"},
            {"InterfaceType": "interface", "Description": "aws-K8S-i-1"},
            {"InterfaceType": "interface", "Description": "aws-K8S-i-2"},
            {"InterfaceType": "interface", "Attachment": {"InstanceId": "i-1"}},
        ]
    )
    assert counts == {"instance": 1, "eks_pod": 2, "nat_gateway": 1}
    assert list(counts) == ["instance", "eks_pod", "nat_gateway"]
