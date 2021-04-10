#!/usr/bin/env python3

import logging
import json
from datetime import timedelta

from ops.charm import CharmBase
from ops.main import main
from ops.model import ActiveStatus, MaintenanceStatus, BlockedStatus

from oci_image import OCIImageResource, OCIImageResourceError
from envoy_data_plane.envoy.config.bootstrap import v2 as bs
from envoy_data_plane.envoy.api import v2 as api
from envoy_data_plane.envoy.config.filter.network.http_connection_manager import (
    v2 as hcm,
)


def get_cluster(service: str, port: int):
    return api.Cluster(
        name=service,
        connect_timeout=timedelta(seconds=30),
        type=api.ClusterDiscoveryType.LOGICAL_DNS,
        lb_policy=api.ClusterLbPolicy.ROUND_ROBIN,
        hosts=[
            api.core.Address(
                socket_address=api.core.SocketAddress(
                    address=service,
                    port_value=port,
                )
            )
        ],
    )


def get_listener(cluster: str, port: int):
    virtual_host = api.route.VirtualHost(
        name="local_service",
        domains=["*"],
        routes=[
            api.route.Route(
                match=api.route.RouteMatch(prefix="/"),
                route=api.route.RouteAction(
                    cluster=cluster,
                    max_grpc_timeout=timedelta(seconds=60),
                ),
            )
        ],
        cors=api.route.CorsPolicy(
            allow_origin=["*"],
            allow_methods=[
                "GET",
                "PUT",
                "DELETE",
                "POST",
                "OPTIONS",
            ],
            allow_headers="keep-alive",
            max_age="1728000",
            expose_headers="grpc-status,grpc-message",
        ),
    )

    filter = api.listener.Filter(
        name="envoy.http_connection_manager",
        config=hcm.HttpConnectionManager(
            codec_type=hcm.HttpConnectionManagerCodecType.AUTO,
            stat_prefix="ingress_http",
            route_config=api.RouteConfiguration(
                name="local_route",
                virtual_hosts=[virtual_host],
            ),
            http_filters=[
                hcm.HttpFilter(name="envoy.grpc_web"),
                hcm.HttpFilter(name="envoy.cors"),
                hcm.HttpFilter(name="envoy.router"),
            ],
        ),
    )
    return api.Listener(
        name="listener-0",
        address=api.core.SocketAddress(
            address="0.0.0.0",
            port_value=port,
        ),
        filter_chains=[api.listener.FilterChain(filters=[filter])],
    )


class Operator(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)

        self.log = logging.getLogger()

        if not self.model.unit.is_leader():
            self.log.info("Not a leader, skipping set_pod_spec")
            self.model.unit.status = ActiveStatus()
            return

        self.image = OCIImageResource(self, "oci-image")

        self.framework.observe(self.on.install, self.set_pod_spec)
        self.framework.observe(self.on.upgrade_charm, self.set_pod_spec)
        self.framework.observe(self.on.config_changed, self.set_pod_spec)
        self.framework.observe(self.on["grpc"].relation_changed, self.set_pod_spec)

        self.framework.observe(self.on["grpc-web"].relation_joined, self.send_info)

    def send_info(self, event):
        event.relation.data[self.unit]["service"] = self.model.app.name
        event.relation.data[self.unit]["port"] = self.model.config["http-port"]

    def set_pod_spec(self, event):
        try:
            image_details = self.image.fetch()
        except OCIImageResourceError as e:
            self.model.unit.status = e.status
            self.log.info(e)
            return

        upstreams = [rel.data[rel.app] for rel in self.model.relations["grpc"]]

        if not upstreams:
            self.model.unit.status = BlockedStatus("No upstream GRPC services.")
            return

        admin = bs.Admin(
            access_log_path="/tmp/admin_access.log",
            address=api.core.SocketAddress(
                address="0.0.0.0",
                port_value=self.model.config["admin-port"],
            ),
        )

        resources = bs.BootstrapStaticResources(
            listeners=[
                get_listener(
                    cluster=upstream["service"],
                    port=self.model.config["http-port"],
                )
                for upstream in upstreams
            ],
            clusters=[get_cluster(**u) for u in upstreams],
        )

        config = {"admin": admin.to_dict(), "static_resources": resources.to_dict()}

        self.model.unit.status = MaintenanceStatus("Setting pod spec")
        self.model.pod.set_spec(
            {
                "version": 3,
                "containers": [
                    {
                        "name": "envoy",
                        "command": ["/usr/local/bin/envoy", "-c"],
                        "args": [
                            "/envoy/envoy.yaml",
                        ],
                        "imageDetails": image_details,
                        "ports": [
                            {
                                "name": "admin",
                                "containerPort": int(self.model.config["admin-port"]),
                            },
                            {
                                "name": "http",
                                "containerPort": int(self.model.config["http-port"]),
                            },
                        ],
                        "volumeConfig": [
                            {
                                "name": "config",
                                "mountPath": "/envoy",
                                "files": [
                                    {
                                        "path": "envoy.json",
                                        "content": json.dumps(config),
                                    }
                                ],
                            }
                        ],
                    }
                ],
            },
        )
        self.model.unit.status = ActiveStatus()


if __name__ == "__main__":
    main(Operator)
