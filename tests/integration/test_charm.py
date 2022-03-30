# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from pathlib import Path

import pytest
import yaml
import requests
import json

log = logging.getLogger(__name__)


METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())

APP_NAME = "envoy"
CHARM_ROOT = "."
PROMETHEUS = "prometheus-k8s"
GRAFANA = "grafana-k8s"
PROMETHEUS_SCRAPE = "prometheus-scrape-config-k8s"


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test):
    await ops_test.model.deploy("mlmd")
    charm = await ops_test.build_charm(".")
    image_path = METADATA["resources"]["oci-image"]["upstream-source"]
    resources = {"oci-image": image_path}
    await ops_test.model.deploy(charm, resources=resources)
    await ops_test.model.add_relation(APP_NAME, "mlmd")
    await ops_test.model.wait_for_idle(status="active", raise_on_blocked=True)

    envoy_status = ops_test.model.applications[APP_NAME].units[0].workload_status
    assert envoy_status == "active"
    relation = ops_test.model.relations[0]
    assert [app.entity_id for app in relation.applications] == [APP_NAME, "mlmd"]
    assert all([endpoint.name == "grpc" for endpoint in relation.endpoints])


async def test_deploy_with_prometheus_and_grafana(ops_test):
    scrape_config = {"scrape_interval": "30s"}
    await ops_test.model.deploy(PROMETHEUS, channel="latest/beta")
    await ops_test.model.deploy(GRAFANA, channel="latest/beta")
    await ops_test.model.deploy(
        PROMETHEUS_SCRAPE, channel="latest/beta", config=scrape_config
    )
    await ops_test.model.add_relation(APP_NAME, PROMETHEUS_SCRAPE)
    await ops_test.model.add_relation(PROMETHEUS, PROMETHEUS_SCRAPE)
    await ops_test.model.add_relation(PROMETHEUS, GRAFANA)
    await ops_test.model.add_relation(APP_NAME, GRAFANA)
    await ops_test.model.add_relation(PROMETHEUS, APP_NAME)

    await ops_test.model.wait_for_idle(
        [APP_NAME, PROMETHEUS, GRAFANA, PROMETHEUS_SCRAPE], status="active"
    )


async def test_correct_observability_setup(ops_test):
    status = await ops_test.model.get_status()
    prometheus_unit_ip = status["applications"][PROMETHEUS]["units"][f"{PROMETHEUS}/0"][
        "address"
    ]
    r = requests.get(
        f'http://{prometheus_unit_ip}:9090/api/v1/query?query=up{{juju_application="{APP_NAME}"}}'
    )
    response = json.loads(r.content.decode("utf-8"))
    assert response["status"] == "success"
    assert len(response["data"]["result"]) == len(
        ops_test.model.applications[APP_NAME].units
    )

    response_metric = response["data"]["result"][0]["metric"]
    assert response_metric["juju_application"] == APP_NAME
    assert response_metric["juju_charm"] == APP_NAME
    assert response_metric["juju_model"] == ops_test.model_name
    assert response_metric["juju_unit"] == f"{APP_NAME}/0"
