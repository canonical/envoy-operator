import dataclasses
import json
import logging
from datetime import timedelta

from betterproto import Casing
from charmed_kubeflow_chisme.components import Component
from envoy_data_plane.envoy.api import v2 as api
from envoy_data_plane.envoy.config.bootstrap import v2 as bs
from envoy_data_plane.envoy.config.filter.network.http_connection_manager import v2 as hcm
from ops import ActiveStatus, BlockedStatus, StatusBase

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class GenerateEnvoyConfigInputs:
    """Defines the required inputs for GenerateEnvoyConfig."""

    admin_port: int
    http_port: int
    upstream_service: str
    upstream_port: int


class GenerateEnvoyConfig(Component):
    """Component to generate envoy's configuration file."""

    def _configure_app_leader(self, event):
        """'Configures' this component.

        This Component does not actually do anything when the Charm refreshes, but instead
        is used to generate a config file given some inputs on demand via the `.get_config()`
        method.  As a small validation step, we execute our _inputs_getter() here just to
        raise an error if anything is malformed.
        """
        self._inputs_getter()

    def get_config(self):
        """Generate envoy configuration."""
        inputs = self._inputs_getter()

        admin = bs.Admin(
            access_log_path="/tmp/admin_access.log",
            address=api.core.Address(
                socket_address=api.core.SocketAddress(
                    address="0.0.0.0",
                    port_value=inputs.admin_port,
                ),
            ),
        )

        resources = bs.BootstrapStaticResources(
            listeners=[
                get_listener(
                    cluster=inputs.upstream_service,
                    port=inputs.http_port,
                )
            ],
            clusters=[get_cluster(service=inputs.upstream_service, port=inputs.upstream_port)],
        )

        config = {
            "admin": admin.to_dict(casing=Casing.SNAKE),
            "static_resources": resources.to_dict(casing=Casing.SNAKE),
        }

        return json.dumps(config)

    def get_status(self) -> StatusBase:
        """Returns the status of this Component.

        This Component doesn't actually do anything on charm reconcile.  We consider
        it active if its _inputs_getter() does not fail to execute.
        """
        try:
            self._inputs_getter()
            return ActiveStatus()
        except Exception as e:
            logger.error(f"Failed to process inputs.  Got Exception: {e}")
            return BlockedStatus("Failed to process inputs.  See logs")


def get_cluster(service: str, port: int):
    return api.Cluster(
        name=service,
        connect_timeout=timedelta(seconds=30),
        type=api.ClusterDiscoveryType.LOGICAL_DNS,
        http2_protocol_options=api.core.Http2ProtocolOptions(
            stream_error_on_invalid_http_messaging=False
        ),
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
        "custom-header-1",
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
            expose_headers="grpc-status,grpc-message,custom-header-1",
        ),
    )

    filter_listener = api.listener.Filter(
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
        name="listener_0",
        address=api.core.Address(
            socket_address=api.core.SocketAddress(
                address="0.0.0.0",
                port_value=port,
            )
        ),
        filter_chains=[api.listener.FilterChain(filters=[filter_listener])],
    )
