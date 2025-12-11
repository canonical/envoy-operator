# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from pathlib import Path

import lightkube
import pytest
import yaml
from charmed_kubeflow_chisme.testing import (
    GRAFANA_AGENT_APP,
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
from pyease_grpc import RpcSession, RpcUri
from pytest_operator.plugin import OpsTest

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
CONTAINERS_SECURITY_CONTEXT_MAP = generate_container_securitycontext_map(METADATA)
CHARM_ROOT = "."
ENVOY_APP_NAME = METADATA["name"]
ISTIO_GATEWAY_APP_NAME = "istio-ingressgateway"
HTTP_PATH = "/ml_metadata.MetadataStoreService/GetExecutionTypes"
HEADERS = {"Content-Type": "application/grpc-web+proto"}
INGRESS_IP = "10.64.140.43"
MLMD_PROTO_PATH = Path("tests/integration/data/metadata_store_service.proto")


log = logging.getLogger(__name__)
web_grpc_session = RpcSession.from_file(MLMD_PROTO_PATH.as_posix())


@pytest.fixture(scope="session")
def lightkube_client() -> Client:
    client = Client(field_manager=ENVOY_APP_NAME)
    return client


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test):
    charm = await ops_test.build_charm(CHARM_ROOT)
    image_path = METADATA["resources"]["oci-image"]["upstream-source"]
    resources = {"oci-image": image_path}

    await ops_test.model.deploy(charm, resources=resources, trust=True)
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


async def test_web_grpc_mlmd():
    log.info("Everything's cool")
    uri = RpcUri(
        base_url=f"{INGRESS_IP}:80",
        package="ml_metadata",
        service="MetadataStoreService",
        method="GetExecutionTypes",
    )

    response = web_grpc_session.request(uri, {})
    assert response.response.status_code == 200


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
