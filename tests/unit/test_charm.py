# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import json

import pytest
import yaml
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.testing import Harness

from charm import Operator


@pytest.fixture
def harness():
    return Harness(Operator)


def test_not_leader(harness):
    harness.begin_with_initial_hooks()
    assert harness.charm.model.unit.status == WaitingStatus("Waiting for leadership")


def test_missing_image(harness):
    harness.set_leader(True)
    harness.begin_with_initial_hooks()
    assert harness.charm.model.unit.status == BlockedStatus("Missing resource: oci-image")


def test_no_relation(harness):
    harness.set_leader(True)
    add_oci_image(harness)

    harness.begin_with_initial_hooks()

    assert harness.charm.model.unit.status == BlockedStatus("No upstream gRPC services.")


def test_many_relations(harness):
    harness.set_leader(True)
    add_oci_image(harness)

    setup_grpc_relation(harness, "grpc-one", "8080")
    setup_grpc_relation(harness, "grpc-two", "9090")
    # In order to avoid the charm going to Blocked
    setup_ingress_relation(harness)
    harness.begin_with_initial_hooks()

    pod_spec, _ = harness.get_pod_spec()

    expected = yaml.safe_load(open("tests/unit/many_relations.yaml"))

    c = pod_spec["containers"][0]["volumeConfig"][0]["files"][0]["content"]
    assert json.loads(c) == expected

    assert harness.charm.model.unit.status == ActiveStatus("")


def test_with_ingress_relation(harness):
    harness.set_leader(True)
    add_oci_image(harness)

    # Set required grpc relation
    setup_grpc_relation(harness, "grpc-one", "8080")

    setup_ingress_relation(harness)

    harness.begin_with_initial_hooks()

    assert isinstance(harness.charm.model.unit.status, ActiveStatus)


# Helper functions
def add_oci_image(harness: Harness):
    harness.add_oci_resource(
        "oci-image",
        {
            "registrypath": "ci-test",
            "username": "",
            "password": "",
        },
    )


def setup_ingress_relation(harness: Harness):
    rel_id = harness.add_relation("ingress", "istio-pilot")
    harness.add_relation_unit(rel_id, "istio-pilot/0")
    harness.update_relation_data(
        rel_id,
        "istio-pilot",
        {"_supported_versions": "- v1"},
    )
    return rel_id


def setup_grpc_relation(harness: Harness, name: str, port: str):
    rel_id = harness.add_relation("grpc", name)
    harness.add_relation_unit(rel_id, f"{name}/0")
    harness.update_relation_data(
        rel_id,
        name,
        {
            "_supported_versions": "- v1",
            "data": yaml.dump({"service": name, "port": port}),
        },
    )
    return rel_id
