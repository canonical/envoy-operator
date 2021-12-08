#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import logging
from datetime import timedelta

from envoy_data_plane.envoy.api import v2 as api
from envoy_data_plane.envoy.config.bootstrap import v2 as bs
from envoy_data_plane.envoy.config.filter.network.http_connection_manager import (
    v2 as hcm,
)
from ops.charm import CharmBase
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus
from serialized_data_interface import (
    NoCompatibleVersions,
    NoVersionsListed,
    get_interfaces,
)

import stringcase
from oci_image import OCIImageResource, OCIImageResourceError


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
    allowed_methods = [
        "GET",
        "PUT",
        "DELETE",
        "POST",
        "OPTIONS",
    ]

    allowed_headers = [
        "cache-control",
        "content-transfer-encoding",
        "content-type",
        "grpc-timeout",
        "keep-alive",
        "user-agent",
        "x-accept-content-transfer-encoding",
        "x-accept-response-streaming",
        "x-grpc-web",
        "x-user-agent",
    ]

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
            allow_methods=",".join(allowed_methods),
            allow_headers=",".join(allowed_headers),
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
        address=api.core.Address(
            socket_address=api.core.SocketAddress(
                address="0.0.0.0",
                port_value=port,
            )
        ),
        filter_chains=[api.listener.FilterChain(filters=[filter])],
    )


class CheckFailed(Exception):
    """ Raise this exception if one of the checks in main fails. """

    def __init__(self, msg: str, status_type=None):
        super().__init__()

        self.msg = str(msg)
        self.status_type = status_type
        self.status = status_type(self.msg)


class Operator(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)

        self.log = logging.getLogger()

        if not self.model.unit.is_leader():
            self.log.info("Not a leader, skipping set_pod_spec")
            self.model.unit.status = ActiveStatus()
            return

        self.image = OCIImageResource(self, "oci-image")

        try:
            self.interfaces = get_interfaces(self)
        except NoVersionsListed as err:
            self.model.unit.status = WaitingStatus(str(err))
            return
        except NoCompatibleVersions as err:
            self.model.unit.status = BlockedStatus(str(err))
            return
        else:
            self.model.unit.status = ActiveStatus()

        self.framework.observe(self.on.install, self.set_pod_spec)
        self.framework.observe(self.on.upgrade_charm, self.set_pod_spec)
        self.framework.observe(self.on.config_changed, self.set_pod_spec)
        self.framework.observe(self.on["grpc"].relation_changed, self.set_pod_spec)

        self.framework.observe(self.on["grpc-web"].relation_changed, self.set_pod_spec)

    def set_pod_spec(self, event):
        try:
            self._check_leader()

            interfaces = self._get_interfaces()

            image_details = self._check_image_details()

            upstreams = self._check_grpc(interfaces)

        except CheckFailed as check_failed:
            self.model.unit.status = check_failed.status
            return

        self._send_info(interfaces)

        admin = bs.Admin(
            access_log_path="/tmp/admin_access.log",
            address=api.core.Address(
                socket_address=api.core.SocketAddress(
                    address="0.0.0.0",
                    port_value=self.model.config["admin-port"],
                ),
            ),
        )

        resources = bs.BootstrapStaticResources(
            listeners=[
                get_listener(
                    cluster=upstream["service"],
                    port=int(self.model.config["http-port"]),
                )
                for upstream in upstreams
            ],
            clusters=[
                get_cluster(service=u["service"], port=int(u["port"]))
                for u in upstreams
            ],
        )

        config = {
            "admin": admin.to_dict(casing=stringcase.snakecase),
            "static_resources": resources.to_dict(casing=stringcase.snakecase),
        }

        self.model.unit.status = MaintenanceStatus("Setting pod spec")
        self.model.pod.set_spec(
            {
                "version": 3,
                "containers": [
                    {
                        "name": "envoy",
                        "command": ["/usr/local/bin/envoy", "-c"],
                        "args": [
                            "/envoy/envoy.json",
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

    def _send_info(self, interfaces):
        if self.interfaces["grpc-web"]:
            self.interfaces["grpc-web"].send_data(
                {
                    "service-host": self.model.app.name,
                    "service-port": self.model.config["http-port"],
                }
            )

    def _check_leader(self):
        if not self.unit.is_leader():
            # We can't do anything useful when not the leader, so do nothing.
            raise CheckFailed("Waiting for leadership", WaitingStatus)

    def _get_interfaces(self):
        try:
            interfaces = get_interfaces(self)
        except NoVersionsListed as err:
            raise CheckFailed(err, WaitingStatus)
        except NoCompatibleVersions as err:
            raise CheckFailed(err, BlockedStatus)
        return interfaces

    def _check_image_details(self):
        try:
            image_details = self.image.fetch()
        except OCIImageResourceError as e:
            raise CheckFailed(f"{e.status.message}", e.status_type)
        return image_details

    def _check_grpc(self, interfaces):
        upstreams = interfaces["grpc"]
        if not upstreams:
            raise CheckFailed("No upstream gRPC services.", BlockedStatus)

        upstreams = list(upstreams.get_data().values())
        if not all(u.get("service") for u in upstreams):
            raise CheckFailed(
                "Waiting for upstream gRPC connection information.", WaitingStatus
            )
        return upstreams


if __name__ == "__main__":
    main(Operator)
