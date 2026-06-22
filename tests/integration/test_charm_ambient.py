# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from pathlib import Path

import lightkube
import pytest
import tenacity
import yaml
from charmed_kubeflow_chisme.testing import (
    GRAFANA_AGENT_APP,
    ISTIO_INGRESS_K8S_APP,
    ISTIO_INGRESS_ROUTE_ENDPOINT,
    assert_alert_rules,
    assert_logging,
    assert_metrics_endpoint,
    assert_security_context,
    deploy_and_assert_grafana_agent,
    deploy_and_integrate_service_mesh_charms,
    generate_container_securitycontext_map,
    get_alert_rules,
    get_pod_names,
)
from charms_dependencies import MLMD
from lightkube import Client
from lightkube.generic_resource import create_namespaced_resource
from lightkube.resources.core_v1 import Service
from pyease_grpc import RpcSession, RpcUri
from pytest_operator.plugin import OpsTest

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
CONTAINERS_SECURITY_CONTEXT_MAP = generate_container_securitycontext_map(METADATA)
CHARM_ROOT = "."
ENVOY_APP_NAME = METADATA["name"]
HTTP_PATH = "/ml_metadata.MetadataStoreService/GetExecutionTypes"
HEADERS = {"Content-Type": "application/grpc-web+proto"}
INGRESS_K8S_SERVICE = "istio-ingress-k8s-istio"
MLMD_PROTO_PATH = Path("tests/integration/data/metadata_store_service.proto")

# A second istio-ingress-k8s instance used to verify multiple-ingress support.
SECOND_INGRESS_APP = "istio-ingress-k8s-alt"
INGRESS_CHANNEL = "2/stable"
# Name of the HTTPRoute submitted by envoy (see AmbientMeshRequirerComponent).
INGRESS_ROUTE_NAME = "http-ingress"
# Gateway listener section for cleartext HTTP on port 80.
HTTP_SECTION_NAME = "http-80"
# Path matched by the envoy HTTPRoute.
INGRESS_ROUTE_PATH = "/ml_metadata.MetadataStoreService/"
# Gateway API generic resources, resolved at runtime via lightkube.
HTTPROUTE_RESOURCE = create_namespaced_resource(
    "gateway.networking.k8s.io", "v1", "HTTPRoute", "httproutes"
)
GATEWAY_RESOURCE = create_namespaced_resource(
    "gateway.networking.k8s.io", "v1", "Gateway", "gateways"
)
RETRY_120_SECONDS = tenacity.Retrying(
    stop=tenacity.stop_after_delay(120),
    wait=tenacity.wait_fixed(2),
    reraise=True,
)


log = logging.getLogger(__name__)
web_grpc_session = RpcSession.from_file(MLMD_PROTO_PATH.as_posix())


@pytest.fixture(scope="session")
def lightkube_client() -> Client:
    client = Client(field_manager=ENVOY_APP_NAME)
    return client


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test, request):
    entity_url = (
        await ops_test.build_charm(CHARM_ROOT)
        if not (entity_url := request.config.getoption("--charm-path"))
        else entity_url
    )
    image_path = METADATA["resources"]["oci-image"]["upstream-source"]
    resources = {"oci-image": image_path}

    await ops_test.model.deploy(entity_url, resources=resources, trust=True)
    await ops_test.model.deploy(MLMD.charm, channel=MLMD.channel, trust=MLMD.trust)
    await ops_test.model.integrate(ENVOY_APP_NAME, MLMD.charm)

    # Deploying grafana-agent-k8s and add all relations
    await deploy_and_assert_grafana_agent(
        ops_test.model, ENVOY_APP_NAME, metrics=True, dashboard=True, logging=True
    )

    await deploy_and_integrate_service_mesh_charms(app=ENVOY_APP_NAME, model=ops_test.model)

    await ops_test.model.wait_for_idle(
        apps=[ENVOY_APP_NAME, MLMD.charm],
        status="active",
        raise_on_blocked=False,
        idle_period=30,
    )


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


async def assert_web_grpc_mlmd(ops_test: OpsTest, lightkube_client: lightkube.Client):
    """Verify the web-grpc envoy connection to mlmd through the ingress gateway."""
    # Get the gateway IP
    service = lightkube_client.get(Service, INGRESS_K8S_SERVICE, namespace=ops_test.model.name)
    gateway_ip = service.status.loadBalancer.ingress[0].ip

    uri = RpcUri(
        base_url=f"{gateway_ip}:80",
        package="ml_metadata",
        service="MetadataStoreService",
        method="GetExecutionTypes",
    )

    response = web_grpc_session.request(uri, {})
    assert response.response.status_code == 200


@pytest.mark.abort_on_fail
async def test_web_grpc_mlmd(ops_test: OpsTest, lightkube_client: lightkube.Client):
    """Test the web-grpc envoy connection to mlmd before the second ingress."""
    await assert_web_grpc_mlmd(ops_test, lightkube_client)


@pytest.mark.parametrize("container_name", list(CONTAINERS_SECURITY_CONTEXT_MAP.keys()))
async def test_container_security_context(
    ops_test: OpsTest,
    lightkube_client: lightkube.Client,
    container_name: str,
):
    """Test container security context is correctly set.

    Verify that container spec defines the security context with correct
    user ID and group ID.
    """
    pod_name = get_pod_names(ops_test.model.name, ENVOY_APP_NAME)[0]
    assert_security_context(
        lightkube_client,
        pod_name,
        container_name,
        CONTAINERS_SECURITY_CONTEXT_MAP,
        ops_test.model.name,
    )


@pytest.mark.abort_on_fail
async def test_deploy_and_relate_second_ingress(ops_test: OpsTest):
    """Deploy a second istio-ingress-k8s and relate it to envoy.

    envoy must accept more than one istio-ingress-route relation without
    erroring, so it should remain active after the second ingress is related.
    """
    await ops_test.model.deploy(
        ISTIO_INGRESS_K8S_APP,
        application_name=SECOND_INGRESS_APP,
        channel=INGRESS_CHANNEL,
        trust=True,
    )
    await ops_test.model.wait_for_idle(
        [SECOND_INGRESS_APP],
        raise_on_blocked=False,
        raise_on_error=False,
        wait_for_active=True,
        timeout=60 * 15,
    )

    await ops_test.model.integrate(
        f"{SECOND_INGRESS_APP}:{ISTIO_INGRESS_ROUTE_ENDPOINT}",
        f"{ENVOY_APP_NAME}:{ISTIO_INGRESS_ROUTE_ENDPOINT}",
    )
    await ops_test.model.wait_for_idle(
        [ENVOY_APP_NAME, SECOND_INGRESS_APP],
        status="active",
        raise_on_blocked=False,
        raise_on_error=False,
        timeout=60 * 10,
        idle_period=30,
    )

    assert ops_test.model.applications[ENVOY_APP_NAME].units[0].workload_status == "active"


async def test_httproute_attached_to_second_gateway(ops_test: OpsTest, lightkube_client: Client):
    """Verify the HTTPRoute for the second ingress is created and bound to its Gateway.

    The istio-ingress-k8s charm names each route
    ``{source_app}-{route_name}-httproute-{section}-{ingress_app}`` and binds it to a
    Gateway named after the ingress application via ``parentRefs``. We assert that the
    route created for the second ingress is attached to the *second* Gateway (not the
    first) and routes the envoy path to the envoy backend.
    """
    namespace = ops_test.model.name

    expected_route_name = (
        f"{ENVOY_APP_NAME}-{INGRESS_ROUTE_NAME}-httproute-{HTTP_SECTION_NAME}-{SECOND_INGRESS_APP}"
    )

    # The second Gateway should exist, named after the second ingress application.
    lightkube_client.get(GATEWAY_RESOURCE, name=SECOND_INGRESS_APP, namespace=namespace)

    # Retry to give the ingress charm time to reconcile the HTTPRoute resources.
    httproute = None
    for attempt in RETRY_120_SECONDS:
        with attempt:
            httproute = lightkube_client.get(
                HTTPROUTE_RESOURCE, name=expected_route_name, namespace=namespace
            )

    parent_refs = httproute.spec["parentRefs"]
    assert len(parent_refs) == 1
    # The route must be attached to the SECOND gateway, not the first.
    assert parent_refs[0]["name"] == SECOND_INGRESS_APP
    assert parent_refs[0]["sectionName"] == HTTP_SECTION_NAME

    # And it must route the envoy path to the envoy backend.
    rule = httproute.spec["rules"][0]
    assert rule["matches"][0]["path"]["value"] == INGRESS_ROUTE_PATH
    assert rule["backendRefs"][0]["name"] == ENVOY_APP_NAME


@pytest.mark.abort_on_fail
async def test_web_grpc_mlmd_after_second_ingress(
    ops_test: OpsTest, lightkube_client: lightkube.Client
):
    """Test the web-grpc envoy connection to mlmd after the second ingress."""
    await assert_web_grpc_mlmd(ops_test, lightkube_client)
