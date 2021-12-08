# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from pathlib import Path

import pytest
import yaml

log = logging.getLogger(__name__)


METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())

APP_NAME = "envoy"
CHARM_ROOT = "."


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test):
    charm = await ops_test.build_charm(".")
    await ops_test.model.deploy(charm)

    # Currently this charm enters a transient blocked state waiting for an oci container. Once this
    # is fixed, add raise_on_blocked=True to the following line. See
    # https://github.com/canonical/envoy-operator/issues/7
    await ops_test.model.wait_for_idle(status="active")


async def test_grpc_relation(ops_test):
    envoy_status = ops_test.model.applications[APP_NAME].units[0].workload_status
    assert envoy_status == "active"

    await ops_test.model.deploy("mlmd")
    await ops_test.model.add_relation(APP_NAME, "mlmd")
    await ops_test.model.wait_for_idle(status="active")

    relation = ops_test.model.relations[0]
    assert [app.entity_id for app in relation.applications] == [APP_NAME, "mlmd"]
    assert all([endpoint.name == "grpc" for endpoint in relation.endpoints])
