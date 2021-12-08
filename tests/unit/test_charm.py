# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import json

import pytest
import yaml
from ops.model import ActiveStatus, BlockedStatus
from ops.testing import Harness

from charm import Operator


@pytest.fixture
def harness():
    return Harness(Operator)


def test_not_leader(harness):
    harness.begin_with_initial_hooks()
    assert harness.charm.model.unit.status == ActiveStatus("")


def test_missing_image(harness):
    harness.set_leader(True)
    harness.begin_with_initial_hooks()
    assert harness.charm.model.unit.status == BlockedStatus(
        "Missing resource: oci-image"
    )


def test_no_relation(harness):
    harness.set_leader(True)
    harness.add_oci_resource(
        "oci-image",
        {
            "registrypath": "ci-test",
            "username": "",
            "password": "",
        },
    )
    harness.begin_with_initial_hooks()

    assert harness.charm.model.unit.status == BlockedStatus(
        "No upstream gRPC services."
    )


def test_many_relations(harness):
    harness.set_leader(True)
    harness.add_oci_resource(
        "oci-image",
        {
            "registrypath": "ci-test",
            "username": "",
            "password": "",
        },
    )
    rel_id1 = harness.add_relation("grpc", "grpc-one")
    harness.add_relation_unit(rel_id1, "grpc-one/0")
    harness.update_relation_data(
        rel_id1,
        "grpc-one",
        {
            "_supported_versions": "- v1",
            "data": yaml.dump({"service": "grpc-one", "port": "8080"}),
        },
    )

    rel_id2 = harness.add_relation("grpc", "grpc-two")
    harness.add_relation_unit(rel_id2, "grpc-two/0")
    harness.update_relation_data(
        rel_id2,
        "grpc-two",
        {
            "_supported_versions": "- v1",
            "data": yaml.dump({"service": "grpc-two", "port": "9090"}),
        },
    )
    harness.begin_with_initial_hooks()

    pod_spec, _ = harness.get_pod_spec()

    expected = yaml.safe_load(open("tests/unit/many_relations.yaml"))

    c = pod_spec["containers"][0]["volumeConfig"][0]["files"][0]["content"]
    assert json.loads(c) == expected

    assert harness.charm.model.unit.status == ActiveStatus("")
