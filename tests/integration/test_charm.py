# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import logging

import aiohttp
import pytest
import requests
import tenacity
from lightkube import Client
from lightkube.generic_resource import create_namespaced_resource
from lightkube.resources.core_v1 import Service

from . import constants

log = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def lightkube_client() -> Client:
    client = Client(field_manager=constants.ENVOY_APP_NAME)
    return client


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test):
    await ops_test.model.deploy(
        constants.MLMD, channel=constants.MLMD_CHANNEL, trust=constants.MLMD_TRUST
    )
    charm = await ops_test.build_charm(constants.CHARM_ROOT)
    image_path = constants.METADATA["resources"]["oci-image"]["upstream-source"]
    resources = {"oci-image": image_path}
    await ops_test.model.deploy(charm, resources=resources)
    await ops_test.model.add_relation(constants.ENVOY_APP_NAME, constants.MLMD)
    await ops_test.model.wait_for_idle(
        apps=[constants.MLMD], status="active", raise_on_blocked=False, idle_period=30
    )
    await ops_test.model.wait_for_idle(
        apps=[constants.ENVOY_APP_NAME], status="blocked", raise_on_blocked=False, idle_period=30
    )

    relation = ops_test.model.relations[0]
    assert [app.entity_id for app in relation.applications] == [
        constants.ENVOY_APP_NAME,
        constants.MLMD,
    ]
    assert all([endpoint.name == "grpc" for endpoint in relation.endpoints])


@pytest.mark.abort_on_fail
async def test_virtual_service(ops_test, lightkube_client):
    await ops_test.model.deploy(
        entity_url=constants.ISTIO_PILOT,
        channel=constants.ISTIO_OPERATORS_CHANNEL,
        config=constants.ISTIO_PILOT_CONFIG,
        trust=constants.ISTIO_PILOT_TRUST,
    )
    await ops_test.model.deploy(
        entity_url=constants.ISTIO_GATEWAY,
        application_name=constants.ISTIO_GATEWAY_APP_NAME,
        channel=constants.ISTIO_OPERATORS_CHANNEL,
        config=constants.ISTIO_GATEWAY_CONFIG,
        trust=constants.ISTIO_GATEWAY_TRUST,
    )

    await ops_test.model.add_relation(
        constants.ISTIO_PILOT,
        constants.ISTIO_GATEWAY_APP_NAME,
    )
    await ops_test.model.add_relation(constants.ISTIO_PILOT, constants.ENVOY_APP_NAME)

    await ops_test.model.wait_for_idle(
        status="active",
        raise_on_blocked=False,
        raise_on_error=True,
        timeout=300,
    )

    # Verify that virtualService is as expected
    assert_virtualservice_exists(
        name=constants.ENVOY_APP_NAME,
        namespace=ops_test.model.name,
        lightkube_client=lightkube_client,
    )

    # Verify `/ml_metadata` endpoint is served
    await assert_metadata_endpoint_is_served(ops_test, lightkube_client=lightkube_client)

    await assert_grpc_web_protocol_responds(ops_test, lightkube_client=lightkube_client)


@pytest.mark.abort_on_fail
async def test_deploy_with_prometheus_and_grafana(ops_test):
    # Deploy and relate prometheus
    await ops_test.model.deploy(
        constants.PROMETHEUS_K8S,
        channel=constants.PROMETHEUS_K8S_CHANNEL,
        trust=constants.PROMETHEUS_K8S_TRUST,
    )
    await ops_test.model.deploy(
        constants.GRAFANA_K8S,
        channel=constants.GRAFANA_K8S_CHANNEL,
        trust=constants.GRAFANA_K8S_TRUST,
    )
    await ops_test.model.deploy(
        constants.PROMETHEUS_SCRAPE_K8S,
        channel=constants.PROMETHEUS_SCRAPE_K8S_CHANNEL,
        config=constants.PROMETHEUS_SCRAPE_CONFIG,
    )

    await ops_test.model.add_relation(
        f"{constants.PROMETHEUS_K8S}:grafana-dashboard",
        f"{constants.GRAFANA_K8S}:grafana-dashboard",
    )
    await ops_test.model.add_relation(
        f"{constants.PROMETHEUS_K8S}:metrics-endpoint",
        f"{constants.PROMETHEUS_SCRAPE_K8S}:metrics-endpoint",
    )

    await ops_test.model.add_relation(constants.ENVOY_APP_NAME, constants.GRAFANA_K8S)
    await ops_test.model.add_relation(constants.PROMETHEUS_K8S, constants.ENVOY_APP_NAME)

    await ops_test.model.wait_for_idle(
        [
            constants.ENVOY_APP_NAME,
            constants.PROMETHEUS_K8S,
            constants.GRAFANA_K8S,
            constants.PROMETHEUS_SCRAPE_K8S,
        ],
        status="active",
    )


async def test_correct_observability_setup(ops_test):
    status = await ops_test.model.get_status()
    prometheus_unit_ip = status["applications"][constants.PROMETHEUS_K8S]["units"][
        f"{constants.PROMETHEUS_K8S}/0"
    ]["address"]
    r = requests.get(
        f'http://{prometheus_unit_ip}:9090/api/v1/query?query=up{{juju_application="{constants.ENVOY_APP_NAME}"}}'  # noqa
    )
    response = json.loads(r.content.decode("utf-8"))
    assert response["status"] == "success"

    response_metric = response["data"]["result"][0]["metric"]
    assert response_metric["juju_application"] == constants.ENVOY_APP_NAME
    assert response_metric["juju_charm"] == constants.ENVOY_APP_NAME
    assert response_metric["juju_model"] == ops_test.model_name
    assert response_metric["juju_unit"] == f"{constants.ENVOY_APP_NAME}/0"


def assert_virtualservice_exists(name: str, namespace: str, lightkube_client):
    """Will raise a ApiError(404) if the virtualservice does not exist."""
    log.info(f"Asserting that  VirtualService '{name}' exists.")
    virtual_service_lightkube_resource = create_namespaced_resource(
        group="networking.istio.io",
        version="v1alpha3",
        kind="VirtualService",
        plural="virtualservices",
    )
    lightkube_client.get(virtual_service_lightkube_resource, name, namespace=namespace)
    log.info(f"VirtualService '{name}' exists.")


@tenacity.retry(
    stop=tenacity.stop_after_delay(10),
    wait=tenacity.wait_fixed(2),
    reraise=True,
)
async def assert_metadata_endpoint_is_served(ops_test, lightkube_client):
    """Asserts that the /ml_metadata endpoint is served (does not return a 404).

    This does not have to return 200.
    """

    regular_ingress_gateway_ip = await get_gateway_ip(
        namespace=ops_test.model.name, lightkube_client=lightkube_client
    )
    res_status, res_text = await fetch_response(f"http://{regular_ingress_gateway_ip}/ml_metadata")
    assert res_status != 404
    log.info("Endpoint /ml_metadata is reachable.")


@tenacity.retry(
    stop=tenacity.stop_after_delay(10),
    wait=tenacity.wait_fixed(2),
    reraise=True,
)
async def assert_grpc_web_protocol_responds(ops_test, lightkube_client):
    """Asserts that the /ml_metadata endpoint responds 200 to the grpc-web protocol."""
    regular_ingress_gateway_ip = await get_gateway_ip(
        namespace=ops_test.model.name, lightkube_client=lightkube_client
    )
    log.info("regular_ingress_gateway_ip: %s", regular_ingress_gateway_ip)
    headers = {"Content-Type": "application/grpc-web-text"}
    res_status, res_headers = await fetch_response(
        f"http://{regular_ingress_gateway_ip}/ml_metadata", headers
    )
    assert res_status == 200
    log.info("Endpoint /ml_metadata serves grpc-web protocol.")


async def fetch_response(url, headers=None):
    """Fetch provided URL and return pair - status and text (int, string)."""

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as response:
            result_status = response.status
            result_text = await response.text()
            result_headers = response.headers
    if headers is None:
        return result_status, str(result_text)
    else:
        return result_status, result_headers


async def get_gateway_ip(
    namespace: str, lightkube_client, service_name: str = "istio-ingressgateway-workload"
):
    log.info(f"Getting {service_name} ingress ip")
    service = lightkube_client.get(Service, service_name, namespace=namespace)
    gateway_ip = service.status.loadBalancer.ingress[0].ip
    return gateway_ip
