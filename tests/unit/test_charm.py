# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
from unittest.mock import MagicMock, patch

import pytest
from charms.istio_ingress_k8s.v0.istio_ingress_route import (
    HTTPPathMatchType,
    IstioIngressRouteConfig,
    ProtocolType,
)
from ops import BlockedStatus
from ops.model import ActiveStatus, TooManyRelatedAppsError, WaitingStatus
from ops.testing import Harness

from charm import GRPC_RELATION_NAME, EnvoyOperator

MOCK_GRPC_DATA = {"name": "service-name", "port": "1234"}


@pytest.fixture
def harness(mocked_kubernetes_service_patch) -> Harness:
    """Harness populated with the Charm and with its KubernetesServicePatch patched."""
    harness = Harness(EnvoyOperator)
    harness.set_leader(True)
    harness.set_model_name("maybe-kubeflow")
    return harness


@pytest.fixture()
def mocked_kubernetes_service_patch(mocker):
    """Mocks the KubernetesServicePatch for the charm."""
    mocked_kubernetes_service_patch = mocker.patch(
        "charm.KubernetesServicePatch", lambda x, y: None
    )
    yield mocked_kubernetes_service_patch


class TestCharm:
    @pytest.mark.parametrize("tls_enabled, expected_port", [(False, 80), (True, 443)])
    def test_ambient_ingress_listener_port(self, harness: Harness, tls_enabled, expected_port):
        """Test that the ambient ingress listener uses the correct port based on TLS setting."""
        harness.begin()
        component = harness.charm.ambient_ingress.component
        component.ingress = MagicMock()
        component.ingress.tls_enabled = tls_enabled

        config = component._istio_ingress_route_config
        assert len(config.listeners) == 1
        assert config.listeners[0].port == expected_port
        assert config.listeners[0].protocol == ProtocolType.HTTP

    def test_log_forwarding(self, harness: Harness):
        with patch("charm.LogForwarder") as mock_logging:
            harness.begin()
            mock_logging.assert_called_once_with(charm=harness.charm)

    def test_not_leader(self, harness):
        """Test that the charm is not active when not leader."""
        harness.set_leader(False)
        harness.begin_with_initial_hooks()
        assert "Waiting for leadership" in harness.charm.leadership_gate.status.message
        assert not isinstance(harness.charm.model.unit.status, ActiveStatus)

    def test_no_grpc_relation(self, harness: Harness):
        """Test the grpc Component and charm are not active when no grpc relation is present."""
        harness.begin_with_initial_hooks()

        assert (
            "Missing relation with a k8s service info provider. Please add the missing relation."
            in harness.charm.grpc.status.message
        )
        assert isinstance(harness.charm.grpc.status, BlockedStatus)
        assert not isinstance(harness.charm.model.unit.status, ActiveStatus)

    def test_multiple_ingress_relations(self, harness: Harness):
        """Test that the charm becomes blocked if both ingress relations exist."""
        setup_grpc_relation(harness, "grpc-one", "8080")
        harness.add_relation("ingress", "istio-pilot")
        harness.add_relation("istio-ingress-route", "istio-ingress-k8s")
        harness.begin_with_initial_hooks()

        assert (
            "Both 'ingress' and 'istio-ingress-route' relations found. Please choose one."
            in harness.charm.model.unit.status.message
        )
        assert isinstance(harness.charm.model.unit.status, BlockedStatus)

    def test_multiple_istio_ingress_route_relations(self, harness: Harness):
        """Test that multiple istio-ingress-route relations do not block the charm."""
        harness.add_relation("istio-ingress-route", "istio-ingress-k8s")
        harness.add_relation("istio-ingress-route", "istio-ingress-k8s-2")
        harness.begin()

        # Inspecting the full list of relations per endpoint must not raise even
        # though there is more than one relation on the istio-ingress-route endpoint.
        status = harness.charm.istio_relations_conflict_detector.component.get_status()
        assert isinstance(status, ActiveStatus)

    def test_each_istio_ingress_route_relation_receives_config(self, harness: Harness):
        """Test that an HTTPRoute config is submitted to every istio-ingress-route relation."""
        harness.begin()

        rel_id_1 = harness.add_relation("istio-ingress-route", "istio-ingress-k8s")
        rel_id_2 = harness.add_relation("istio-ingress-route", "istio-ingress-k8s-2")

        # Use the real submit_config so we can inspect the databags; only force readiness.
        ingress = harness.charm.ambient_ingress.component.ingress
        ingress.is_ready = MagicMock(return_value=True)

        # Reconcile the charm so the component submits its config.
        harness.charm.on.install.emit()

        # Each relation's application databag should contain a valid config that
        # defines the envoy HTTPRoute, proving the lib handles every ingress.
        for rel_id in (rel_id_1, rel_id_2):
            app_data = harness.get_relation_data(rel_id, harness.charm.app.name)
            assert "config" in app_data

            config = IstioIngressRouteConfig.model_validate_json(app_data["config"])
            assert len(config.http_routes) == 1
            http_route = config.http_routes[0]
            assert http_route.matches[0].path.type == HTTPPathMatchType.PathPrefix
            assert http_route.matches[0].path.value == "/ml_metadata.MetadataStoreService/"
            assert http_route.backends[0].service == harness.charm.app.name
            assert http_route.backends[0].port == int(harness.charm.model.config["http-port"])
            assert http_route.listener.name == "http-80"

    def test_many_relations(self, harness: Harness):
        """Test the grpc component and charm are not active when >1 grpc relation is present."""

        setup_grpc_relation(harness, "grpc-one", "8080")
        setup_grpc_relation(harness, "grpc-two", "9090")
        # In order to avoid the charm going to Blocked
        setup_ingress_relation(harness)

        harness.begin_with_initial_hooks()

        with pytest.raises(TooManyRelatedAppsError) as error:
            harness.charm.grpc.get_status()

        assert "Too many remote applications on grpc (2 > 1)" in error.value.args
        assert not isinstance(harness.charm.model.unit.status, ActiveStatus)

    def test_with_grpc_relation(self, harness: Harness):
        """Test that the grpc Component is active when one grpc relation is present."""
        setup_grpc_relation(harness, "grpc-one", "8080")
        harness.begin_with_initial_hooks()

        assert isinstance(harness.charm.grpc.status, ActiveStatus)

    def test_grpc_with_empty_data(self, harness: Harness):
        """Test the grpc relation component returns WaitingStatus when data is missing."""
        # Arrange
        harness.begin()

        # Mock:
        # * leadership_gate to be active and executed
        harness.charm.leadership_gate.get_status = MagicMock(return_value=ActiveStatus())

        harness.charm.on.install.emit()

        # Add relation without data.
        harness.add_relation(relation_name=GRPC_RELATION_NAME, remote_app="other-app", app_data={})

        assert isinstance(harness.charm.grpc.get_status(), WaitingStatus)

    def test_grpc_relation_with_missing_data(self, harness: Harness):
        """Test the grpc relation component returns WaitingStatus when data is incomplete."""
        # Arrange
        harness.begin()

        # Mock:
        # * leadership_gate to be active and executed
        harness.charm.leadership_gate.get_status = MagicMock(return_value=ActiveStatus())

        harness.charm.on.install.emit()

        # Add relation without data.
        harness.add_relation(
            relation_name=GRPC_RELATION_NAME,
            remote_app="other-app",
            app_data={"name": "some-name"},
        )

        assert isinstance(harness.charm.grpc.component.get_status(), WaitingStatus)

    def test_with_ingress_relation(self, harness: Harness):
        """Test that the ingress_relation Component is active when an ingress is present."""
        # Set required grpc relation
        setup_grpc_relation(harness, "grpc-one", "8080")
        setup_ingress_relation(harness)

        harness.begin_with_initial_hooks()

        assert harness.charm.ingress_relation.status == ActiveStatus()

    def test_pebble_container(self, harness: Harness):
        """Test the pebble container is active when prerequisites are ready."""
        setup_grpc_relation(harness, "grpc-one", "8080")
        setup_ingress_relation(harness)

        harness.begin_with_initial_hooks()

        container = harness.model.unit.get_container("envoy")
        assert container.get_service("envoy")


def setup_ingress_relation(harness: Harness):
    rel_id = harness.add_relation(
        relation_name="ingress",
        remote_app="istio-pilot",
        app_data={"_supported_versions": "- v1"},
    )
    return rel_id


def setup_grpc_relation(harness: Harness, name: str, port: str):
    rel_id = harness.add_relation(
        relation_name=GRPC_RELATION_NAME,
        remote_app=name,
        app_data=MOCK_GRPC_DATA,
    )
    return rel_id
