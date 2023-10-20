# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import logging
from pathlib import Path

import aiohttp
import pytest
import requests
import yaml
from lightkube import Client
from lightkube.generic_resource import create_namespaced_resource
from lightkube.resources.core_v1 import Service

log = logging.getLogger(__name__)


METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())

APP_NAME = "envoy"
CHARM_ROOT = "."
PROMETHEUS = "prometheus-k8s"
GRAFANA = "grafana-k8s"
PROMETHEUS_SCRAPE = "prometheus-scrape-config-k8s"
MLMD = "mlmd"
ISTIO_PILOT = "istio-pilot"
ISTIO_GW = "istio-ingressgateway"


@pytest.fixture(scope="session")
def lightkube_client() -> Client:
    client = Client(field_manager=APP_NAME)
    return client


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test):
    await ops_test.model.deploy(MLMD, channel="latest/edge")
    charm = await ops_test.build_charm(".")
    image_path = METADATA["resources"]["oci-image"]["upstream-source"]
    resources = {"oci-image": image_path}
    await ops_test.model.deploy(charm, resources=resources)
    await ops_test.model.add_relation(APP_NAME, MLMD)
    await ops_test.model.wait_for_idle(status="active", raise_on_blocked=True, idle_period=30)

    relation = ops_test.model.relations[0]
    assert [app.entity_id for app in relation.applications] == [APP_NAME, MLMD]
    assert all([endpoint.name == "grpc" for endpoint in relation.endpoints])


@pytest.mark.abort_on_fail
async def test_virtual_service(ops_test, lightkube_client):
    await ops_test.model.deploy(
        ISTIO_PILOT,
        channel="latest/edge",
        config={"default-gateway": "kubeflow-gateway"},
        trust=True,
    )

    await ops_test.model.deploy(
        "istio-gateway",
        application_name=ISTIO_GW,
        channel="latest/edge",
        config={"kind": "ingress"},
        trust=True,
    )
    await ops_test.model.add_relation(f"{ISTIO_PILOT}:{ISTIO_PILOT}", f"{ISTIO_GW}:{ISTIO_PILOT}")
    await ops_test.model.add_relation(ISTIO_PILOT, APP_NAME)

    await ops_test.model.wait_for_idle(
        status="active",
        raise_on_blocked=False,
        raise_on_error=True,
        timeout=300,
    )

    # Verify that virtualService is as expected
    assert_virtualservice_exists(name=APP_NAME, namespace=ops_test.model.name)

    # Verify `/ml_metadata` endpoint is served
    regular_ingress_gateway_ip = await get_gateway_ip(namespace=ops_test.model.name)
    res_status, res_text = await fetch_response(f"http://{regular_ingress_gateway_ip}/ml_metadata")
    assert res_status != 404


@pytest.mark.abort_on_fail
async def test_deploy_with_prometheus_and_grafana(ops_test):
    scrape_config = {"scrape_interval": "30s"}
    await ops_test.model.deploy(PROMETHEUS, channel="latest/stable", trust=True)
    await ops_test.model.deploy(GRAFANA, channel="latest/stable", trust=True)
    await ops_test.model.deploy(
        PROMETHEUS_SCRAPE, channel="latest/stable", trust=True, config=scrape_config
    )
    await ops_test.model.add_relation(APP_NAME, PROMETHEUS_SCRAPE)
    await ops_test.model.add_relation(
        f"{PROMETHEUS}:metrics-endpoint", f"{PROMETHEUS_SCRAPE}:metrics-endpoint"
    )
    await ops_test.model.add_relation(
        f"{PROMETHEUS}:grafana-dashboard", f"{GRAFANA}:grafana-dashboard"
    )
    await ops_test.model.add_relation(APP_NAME, GRAFANA)
    await ops_test.model.add_relation(PROMETHEUS, APP_NAME)

    await ops_test.model.wait_for_idle(
        [APP_NAME, PROMETHEUS, GRAFANA, PROMETHEUS_SCRAPE], status="active"
    )


async def test_correct_observability_setup(ops_test):
    status = await ops_test.model.get_status()
    prometheus_unit_ip = status["applications"][PROMETHEUS]["units"][f"{PROMETHEUS}/0"]["address"]
    r = requests.get(
        f'http://{prometheus_unit_ip}:9090/api/v1/query?query=up{{juju_application="{APP_NAME}"}}'
    )
    response = json.loads(r.content.decode("utf-8"))
    assert response["status"] == "success"

    response_metric = response["data"]["result"][0]["metric"]
    assert response_metric["juju_application"] == APP_NAME
    assert response_metric["juju_charm"] == APP_NAME
    assert response_metric["juju_model"] == ops_test.model_name
    assert response_metric["juju_unit"] == f"{APP_NAME}/0"


def assert_virtualservice_exists(name: str, namespace: str):
    """Will raise a ApiError(404) if the virtualservice does not exist."""
    log.info(f"Asserting that  VirtualService '{name}' exists.")
    lightkube_client = Client()
    virtual_service_lightkube_resource = create_namespaced_resource(
        group="networking.istio.io",
        version="v1alpha3",
        kind="VirtualService",
        plural="virtualservices",
    )
    lightkube_client.get(virtual_service_lightkube_resource, name, namespace=namespace)
    log.info(f"VirtualService '{name}' exists.")


async def fetch_response(url, headers=None):
    """Fetch provided URL and return pair - status and text (int, string)."""
    result_status = 0
    result_text = ""
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            result_status = response.status
            result_text = await response.text()
    return result_status, str(result_text)


async def get_gateway_ip(
    namespace: str,
    service_name: str = "istio-ingressgateway-workload",
):
    log.info(f"Getting {service_name} ingress ip")
    lightkube_client = Client()
    service = lightkube_client.get(Service, service_name, namespace=namespace)
    gateway_ip = service.status.loadBalancer.ingress[0].ip
    return gateway_ip
