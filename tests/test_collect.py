from __future__ import annotations

import json
import os

import pytest

from aws_subnet_ip_exhaustion.collect import CollectError, load_payload, parse_tag_filters

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "eks-vpc.json")


def test_tag_filters_become_ec2_filters():
    assert parse_tag_filters(["Environment=prod", "Team=platform"]) == [
        {"Name": "tag:Environment", "Values": ["prod"]},
        {"Name": "tag:Team", "Values": ["platform"]},
    ]


@pytest.mark.parametrize("value", ["Environment", "=prod", "Environment=", ""])
def test_a_tag_without_a_key_and_value_is_rejected(value):
    with pytest.raises(CollectError, match="KEY=VALUE"):
        parse_tag_filters([value])


def test_loading_the_fixture_payload():
    payload = load_payload(FIXTURE)
    assert payload["region"] == "eu-central-1"
    assert len(payload["subnets"]) == 3
    assert len(payload["network_interfaces"]) == 252


def test_network_interfaces_default_to_empty(tmp_path):
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"subnets": [{"SubnetId": "subnet-a", "CidrBlock": "10.0.0.0/24"}]}), encoding="utf-8")
    assert load_payload(str(path))["network_interfaces"] == []


@pytest.mark.parametrize(
    "content,message",
    [
        ("not json at all", "not valid JSON"),
        ('["a list"]', "expected an object"),
        ('{"subnets": {}}', "must be a list"),
        ('{"subnets": [{"CidrBlock": "10.0.0.0/24"}]}', "SubnetId"),
        ('{"subnets": [], "network_interfaces": {}}', "must be a list"),
    ],
)
def test_a_malformed_payload_is_rejected_with_a_readable_message(tmp_path, content, message):
    path = tmp_path / "p.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(CollectError, match=message):
        load_payload(str(path))
