#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

from charmed_kubeflow_chisme.components import (
    CharmReconciler,
    LeadershipGateComponent,
    SdiRelationBroadcasterComponent,
    SdiRelationDataReceiverComponent,
)
from charmed_kubeflow_chisme.components.pebble_component import LazyContainerFileTemplate
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.observability_libs.v1.kubernetes_service_patch import KubernetesServicePatch
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from lightkube.models.core_v1 import ServicePort
from ops.charm import CharmBase
from ops.main import main

from components.config_generation import GenerateEnvoyConfig, GenerateEnvoyConfigInputs
from components.pebble import EnvoyPebbleService, EnvoyPebbleServiceInputs

ENVOY_CONFIG_FILE_PATH = "/envoy/envoy.json"
METRICS_PATH = "/stats/prometheus"


class EnvoyOperator(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)

        self.charm_reconciler = CharmReconciler(self)

        self.leadership_gate = self.charm_reconciler.add(
            component=LeadershipGateComponent(
                charm=self,
                name="leadership-gate",
            )
        )

        # TODO before ckf 1.9: Change this from SDI to the service-info lib
        #  https://github.com/canonical/envoy-operator/issues/76
        self.grpc = self.charm_reconciler.add(
            component=SdiRelationDataReceiverComponent(
                charm=self,
                name="relation:grpc",
                relation_name="grpc",
            ),
            depends_on=[self.leadership_gate],
        )

        # Should this Component block the charm if it is not available?
        # It is a requirement of the Kubeflow bundle, not the envoy charm.
        # Envoy doesn't  NEED this relation, but Charmed Kubeflow using this for
        # kfp does need that relation.
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
            depends_on=[self.leadership_gate],
        )

        self.envoy_config_generator = self.charm_reconciler.add(
            GenerateEnvoyConfig(
                charm=self,
                name="envoy_config_generator",
                inputs_getter=lambda: GenerateEnvoyConfigInputs(
                    admin_port=int(self.config["admin-port"]),
                    http_port=int(self.config["http-port"]),
                    # TODO: Do these work properly?  They're not deferred
                    upstream_service=self.grpc.component.get_data()["service"],
                    upstream_port=self.grpc.component.get_data()["port"],
                ),
            ),
            depends_on=[self.grpc],
        )

        self.envoy_pebble_container = self.charm_reconciler.add(
            component=EnvoyPebbleService(
                charm=self,
                name="envoy-component",
                service_name="envoy",
                container_name="envoy",
                files_to_push=[
                    LazyContainerFileTemplate(
                        destination_path=ENVOY_CONFIG_FILE_PATH,
                        source_template=self.envoy_config_generator.component.get_config,
                    )
                ],
                inputs_getter=lambda: EnvoyPebbleServiceInputs(config_path=ENVOY_CONFIG_FILE_PATH),
            ),
            depends_on=[self.envoy_config_generator],
        )

        self.charm_reconciler.install_default_event_handlers()

        admin_port = ServicePort(int(self.model.config["admin-port"]), name="admin")
        http_port = ServicePort(int(self.model.config["http-port"]), name="http")
        self.service_patcher = KubernetesServicePatch(
            self, [admin_port, http_port], service_name=f"{self.model.app.name}"
        )

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
            relation_name="grafana-dashboards",
        )


if __name__ == "__main__":
    main(EnvoyOperator)
