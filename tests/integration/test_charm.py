# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from pathlib import Path

import aiohttp
import pytest
import tenacity
import yaml
from charmed_kubeflow_chisme.testing import (
    GRAFANA_AGENT_APP,
    assert_alert_rules,
    assert_logging,
    assert_metrics_endpoint,
    deploy_and_assert_grafana_agent,
    get_alert_rules,
)
from charms_dependencies import ISTIO_GATEWAY, ISTIO_PILOT, MLMD
from lightkube import Client
from lightkube.generic_resource import create_namespaced_resource
from lightkube.resources.core_v1 import Service
from pytest_operator.plugin import OpsTest

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
CHARM_ROOT = "."
ENVOY_APP_NAME = METADATA["name"]
ISTIO_GATEWAY_APP_NAME = "istio-ingressgateway"


log = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def lightkube_client() -> Client:
    client = Client(field_manager=ENVOY_APP_NAME)
    return client


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test):
    await ops_test.model.deploy(MLMD.charm, channel=MLMD.channel, trust=MLMD.trust)
    charm = await ops_test.build_charm(CHARM_ROOT)
    image_path = METADATA["resources"]["oci-image"]["upstream-source"]
    resources = {"oci-image": image_path}
    await ops_test.model.deploy(charm, resources=resources, trust=True)
    await ops_test.model.integrate(ENVOY_APP_NAME, MLMD.charm)
    await ops_test.model.wait_for_idle(
        apps=[ENVOY_APP_NAME, MLMD.charm], status="active", raise_on_blocked=False, idle_period=30
    )

    relation = ops_test.model.relations[0]
    assert [app.entity_id for app in relation.applications] == [
        ENVOY_APP_NAME,
        MLMD.charm,
    ]
    assert all([endpoint.name in ("grpc", "k8s-service-info") for endpoint in relation.endpoints])

    # Deploying grafana-agent-k8s and add all relations
    await deploy_and_assert_grafana_agent(
        ops_test.model, ENVOY_APP_NAME, metrics=True, dashboard=True, logging=True
    )


@pytest.mark.abort_on_fail
async def test_virtual_service(ops_test, lightkube_client):
    await ops_test.model.deploy(
        entity_url=ISTIO_PILOT.charm,
        channel=ISTIO_PILOT.channel,
        config=ISTIO_PILOT.config,
        trust=ISTIO_PILOT.trust,
    )
    await ops_test.model.deploy(
        entity_url=ISTIO_GATEWAY.charm,
        application_name=ISTIO_GATEWAY_APP_NAME,
        channel=ISTIO_GATEWAY.channel,
        config=ISTIO_GATEWAY.config,
        trust=ISTIO_GATEWAY.trust,
    )

    await ops_test.model.integrate(
        ISTIO_PILOT.charm,
        ISTIO_GATEWAY_APP_NAME,
    )
    await ops_test.model.integrate(f"{ISTIO_PILOT.charm}:ingress", f"{ENVOY_APP_NAME}:ingress")

    await ops_test.model.wait_for_idle(
        apps=[ENVOY_APP_NAME, MLMD.charm, ISTIO_PILOT.charm, ISTIO_GATEWAY_APP_NAME],
        status="active",
        raise_on_blocked=False,
        raise_on_error=True,
        timeout=300,
    )

    # Verify that virtualService is as expected
    assert_virtualservice_exists(
        name=ENVOY_APP_NAME,
        namespace=ops_test.model.name,
        lightkube_client=lightkube_client,
    )

    # Verify `/ml_metadata` endpoint is served
    await assert_metadata_endpoint_is_served(ops_test, lightkube_client=lightkube_client)

    # commenting out due to https://github.com/canonical/envoy-operator/issues/106
    # await assert_grpc_web_protocol_responds(ops_test, lightkube_client=lightkube_client)


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


# Commenting out due to https://github.com/canonical/envoy-operator/issues/106
# @tenacity.retry(
#     stop=tenacity.stop_after_delay(10),
#     wait=tenacity.wait_fixed(2),
#     reraise=True,
# )
# async def assert_grpc_web_protocol_responds(ops_test, lightkube_client):
#     """Asserts that the /ml_metadata endpoint responds 200 to the grpc-web protocol."""
#     regular_ingress_gateway_ip = await get_gateway_ip(
#         namespace=ops_test.model.name, lightkube_client=lightkube_client
#     )
#     log.info("regular_ingress_gateway_ip: %s", regular_ingress_gateway_ip)
#     headers = {"Content-Type": "application/grpc-web-text"}
#     res_status, res_headers = await fetch_response(
#         f"http://{regular_ingress_gateway_ip}/ml_metadata", headers
#     )
#     assert res_status == 200
#     log.info("Endpoint /ml_metadata serves grpc-web protocol.")


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


async def test_alert_rules(ops_test: OpsTest):
    """Test check charm alert rules and rules defined in relation data bag."""
    app = ops_test.model.applications[ENVOY_APP_NAME]
    alert_rules = get_alert_rules()
    log.info("found alert_rules: %s", alert_rules)
    await assert_alert_rules(app, alert_rules)


async def test_metrics_enpoint(ops_test: OpsTest):
    """Test metrics_endpoints are defined in relation data bag and their accessibility.
    This function gets all the metrics_endpoints from the relation data bag, checks if
    they are available from the grafana-agent-k8s charm and finally compares them with the
    ones provided to the function.
    """
    app = ops_test.model.applications[ENVOY_APP_NAME]
    await assert_metrics_endpoint(app, metrics_port=9901, metrics_path="/stats/prometheus")


async def test_logging(ops_test: OpsTest):
    """Test logging is defined in relation data bag."""
    app = ops_test.model.applications[GRAFANA_AGENT_APP]
    await assert_logging(app)
