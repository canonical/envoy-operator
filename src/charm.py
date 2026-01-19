#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

from pathlib import Path

from charmed_kubeflow_chisme.components import (
    CharmReconciler,
    LeadershipGateComponent,
    SdiRelationBroadcasterComponent,
)
from charmed_kubeflow_chisme.components.pebble_component import (
    LazyContainerFileTemplate,
)
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.loki_k8s.v1.loki_push_api import LogForwarder
from charms.observability_libs.v1.kubernetes_service_patch import KubernetesServicePatch
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from lightkube.models.core_v1 import ServicePort
from ops import main
from ops.charm import CharmBase

from components.istio_ambient_requirer_component import AmbientMeshRequirerComponent
from components.istio_relations_conflict_detector import (
    IstioRelationsConflictDetector,
)
from components.k8s_service_info_requirer_component import (
    K8sServiceInfoRequirerComponent,
)
from components.pebble import EnvoyPebbleService, EnvoyPebbleServiceInputs

ENVOY_CONFIG_FILE_SOURCE_PATH = "src/templates/envoy-config.yaml.j2"
GRPC_RELATION_NAME = "grpc"
METRICS_PATH = "/stats/prometheus"


class EnvoyOperator(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)

        # Storage
        self._container_name = next(iter(self.meta.containers))
        _container_meta = self.meta.containers[self._container_name]
        _storage_name = next(iter(_container_meta.mounts))
        self._storage_path = Path(_container_meta.mounts[_storage_name].location)

        self.charm_reconciler = CharmReconciler(self)

        self.leadership_gate = self.charm_reconciler.add(
            component=LeadershipGateComponent(
                charm=self,
                name="leadership-gate",
            )
        )

        self.grpc = self.charm_reconciler.add(
            component=K8sServiceInfoRequirerComponent(
                charm=self,
                relation_name=GRPC_RELATION_NAME,
            ),
            depends_on=[self.leadership_gate],
        )

        # Ensure that ambient and SDI Istio are not related at the same time
        self.istio_relations_conflict_detector = self.charm_reconciler.add(
            IstioRelationsConflictDetector(charm=self, name="istio-relations-conflict-detector")
        )

        # Should this Component block the charm if it is not available?  Ingress is a requirement
        # of this charm deployed in the Kubeflow bundle not of Envoy itself (see
        # https://github.com/canonical/envoy-operator/issues/61 for more details).
        # The Envoy workload deployed here doesn't NEED this relation to function, but since this
        # charm is designed specifically to implement Envoy for KFP's metadata handling,
        # ingress is needed by KFP in Charmed Kubeflow.
        self.ingress_relation = self.charm_reconciler.add(
            component=SdiRelationBroadcasterComponent(
                charm=self,
                name="relation:ingress",
                relation_name="ingress",
                data_to_send={
                    "prefix": "/ml_metadata",
                    "rewrite": "/ml_metadata",
                    "service": self.model.app.name,
                    "port": int(self.model.config["http-port"]),
                },
            ),
            depends_on=[self.leadership_gate, self.istio_relations_conflict_detector],
        )

        self.charm_reconciler.add(
            AmbientMeshRequirerComponent(charm=self, name="ambient-ingress-requirer"),
            depends_on=[self.leadership_gate, self.istio_relations_conflict_detector],
        )

        self.envoy_pebble_container = self.charm_reconciler.add(
            component=EnvoyPebbleService(
                charm=self,
                name="envoy-component",
                service_name="envoy",
                container_name=self._container_name,
                files_to_push=[
                    LazyContainerFileTemplate(
                        destination_path=self._storage_path / "envoy-config.yaml",
                        source_template_path=ENVOY_CONFIG_FILE_SOURCE_PATH,
                        context=lambda: {
                            "admin_port": self.config["admin-port"],
                            "http_port": self.config["http-port"],
                            "upstream_service": self.grpc.component.get_service_info().name,
                            "upstream_port": self.grpc.component.get_service_info().port,
                        },
                    )
                ],
                inputs_getter=lambda: EnvoyPebbleServiceInputs(
                    config_path=self._storage_path / "envoy-config.yaml"
                ),
            ),
            depends_on=[self.grpc],
        )

        self.charm_reconciler.install_default_event_handlers()

        admin_port = ServicePort(int(self.model.config["admin-port"]), name="admin")
        http_port = ServicePort(int(self.model.config["http-port"]), name="http")
        self.service_patcher = KubernetesServicePatch(self, [admin_port, http_port])

        self.prometheus_provider = MetricsEndpointProvider(
            charm=self,
            jobs=[
                {
                    "job_name": "envoy_operator_metrics",
                    "metrics_path": METRICS_PATH,
                    "static_configs": [{"targets": ["*:{}".format(self.config["admin-port"])]}],
                }
            ],
        )

        self.dashboard_provider = GrafanaDashboardProvider(
            self,
            relation_name="grafana-dashboard",
        )
        self._logging = LogForwarder(charm=self)


if __name__ == "__main__":
    main(EnvoyOperator)
