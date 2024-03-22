# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import pytest
import yaml
from ops import BlockedStatus
from ops.model import ActiveStatus
from ops.testing import Harness

from charm import EnvoyOperator


@pytest.fixture
def harness(mocked_kubernetes_service_patch):
    """Harness populated with the Charm and with its KubernetesServicePatch patched."""
    return Harness(EnvoyOperator)


@pytest.fixture()
def mocked_kubernetes_service_patch(mocker):
    """Mocks the KubernetesServicePatch for the charm."""
    mocked_kubernetes_service_patch = mocker.patch(
        "charm.KubernetesServicePatch", lambda x, y: None
    )
    yield mocked_kubernetes_service_patch


class TestCharm:

    def test_not_leader(self, harness):
        """Test that the charm is not active when not leader."""
        harness.begin_with_initial_hooks()
        assert "Waiting for leadership" in harness.charm.leadership_gate.status.message
        assert not isinstance(harness.charm.model.unit.status, ActiveStatus)

    def test_no_grpc_relation(self, harness):
        """Test the grpc Component and charm are not active when no grpc relation is present."""
        harness.set_leader(True)
        harness.begin_with_initial_hooks()

        assert (
            "Expected data from exactly 1 related applications - got 0"
            in harness.charm.grpc.status.message
        )
        assert isinstance(harness.charm.grpc.status, BlockedStatus)
        assert not isinstance(harness.charm.model.unit.status, ActiveStatus)

    def test_many_relations(self, harness):
        """Test the grpc component and charm are not active when >1 grpc relation is present."""
        harness.set_leader(True)

        setup_grpc_relation(harness, "grpc-one", "8080")
        setup_grpc_relation(harness, "grpc-two", "9090")
        # In order to avoid the charm going to Blocked
        setup_ingress_relation(harness)
        harness.begin_with_initial_hooks()

        assert (
            "Expected data from at most 1 related applications - got 2"
            in harness.charm.grpc.status.message
        )
        assert isinstance(harness.charm.grpc.status, BlockedStatus)
        assert not isinstance(harness.charm.model.unit.status, ActiveStatus)

    def test_with_grpc_relation(self, harness):
        """Test that the grpc Component is active when one grpc relation is present."""
        harness.set_leader(True)
        setup_grpc_relation(harness, "grpc-one", "8080")
        harness.begin_with_initial_hooks()

        assert isinstance(harness.charm.grpc.status, ActiveStatus)

    def test_with_ingress_relation(self, harness):
        """Test that the ingress_relation Component is active when an ingress is present."""
        harness.set_leader(True)
        # Set required grpc relation
        setup_grpc_relation(harness, "grpc-one", "8080")
        setup_ingress_relation(harness)

        harness.begin_with_initial_hooks()

        assert harness.charm.ingress_relation.status == ActiveStatus()

    def test_warning_if_ingress_missing(self, harness, mocker):
        """Test that we emit a warning if we do not have an ingress established."""
        harness.set_leader(True)
        setup_grpc_relation(harness, "grpc-one", "8080")

        harness.begin()

        # Mock the ingress warning logger so we can check if it was called
        mock_logger = mocker.MagicMock()
        harness.charm.ingress_relation_warn_if_missing.component.logger = mock_logger

        # Do something that will reconcile the charm
        harness.charm.on.config_changed.emit()

        # Assert that we've logged the missing ingress relation
        assert "No ingress relation established" in mock_logger.warning.call_args[0][0]

    def test_no_warning_if_ingress_is_established(self, harness, mocker):
        """Test that we emit a warning if we do not have an ingress established."""
        harness.set_leader(True)
        setup_grpc_relation(harness, "grpc-one", "8080")
        setup_ingress_relation(harness)

        harness.begin()

        # Mock the ingress warning logger so we can check if it was called
        mock_logger = mocker.MagicMock()
        harness.charm.ingress_relation_warn_if_missing.component.logger = mock_logger

        # Do something that will reconcile the charm
        harness.charm.on.config_changed.emit()

        # Assert that we haven't logged anything
        assert mock_logger.warning.call_args is None

    def test_envoy_config_generator(self, harness):
        """Test the envoy_config_generator Component is active when prerequisites are ready."""
        harness.set_leader(True)
        setup_grpc_relation(harness, "grpc-one", "8080")
        setup_ingress_relation(harness)

        harness.begin_with_initial_hooks()

        assert harness.charm.envoy_config_generator.status == ActiveStatus()

    def test_envoy_config_generator_if_grpc_get_data_returns_empty_dict(self, harness):
        """Test the envoy_config_generator Component raises an error if grpc data is not ready."""
        harness.set_leader(True)
        setup_grpc_relation(harness, "grpc-one", "8080")
        setup_ingress_relation(harness)

        # Let the charm start up with the grpc relation
        harness.begin_with_initial_hooks()

        # Now mock out the grpc relation's get_data to return an empty dict
        harness.charm.grpc.component.get_data = lambda: {}

        assert isinstance(harness.charm.envoy_config_generator.status, BlockedStatus)

        # Do something to trigger a config changed to update the charm status
        harness.update_config({"admin-port": "8082"})
        assert not isinstance(harness.charm.model.unit.status, ActiveStatus)

    def test_pebble_container(self, harness):
        """Test the pebble container is active when prerequisites are ready."""
        harness.set_leader(True)
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
        relation_name="grpc",
        remote_app=name,
        app_data={
            "_supported_versions": "- v1",
            "data": yaml.dump({"service": name, "port": port}),
        },
    )
    return rel_id
