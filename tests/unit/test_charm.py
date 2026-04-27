# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
from unittest.mock import MagicMock, patch

import pytest
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
