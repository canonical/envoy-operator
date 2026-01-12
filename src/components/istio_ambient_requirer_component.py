import logging

from charmed_kubeflow_chisme.components import Component
from charmed_kubeflow_chisme.exceptions import GenericCharmRuntimeError
from charms.istio_beacon_k8s.v0.service_mesh import ServiceMeshConsumer, UnitPolicy
from charms.istio_ingress_k8s.v0.istio_ingress_route import (
    BackendRef,
    HTTPPathMatch,
    HTTPRoute,
    HTTPRouteMatch,
    IstioIngressRouteConfig,
    IstioIngressRouteRequirer,
    Listener,
    ProtocolType,
)
from ops import ActiveStatus, StatusBase

SDI_RELATION = "ingress"
ISTIO_RELATION = "istio-ingress-route"

logger = logging.getLogger(__name__)


class AmbientMeshRequirerComponent(Component):
    """Component to manage the relations to Istio ambient."""

    def __init__(
        self,
        *args,
        path_prefix: str = "/ml_metadata.MetadataStoreService/",
        relation_name: str = "istio-ingress-route",
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        self.path_prefix = path_prefix

        self.ingress = IstioIngressRouteRequirer(
            self._charm,
            relation_name=relation_name,
        )

        self._mesh = ServiceMeshConsumer(
            self._charm, policies=[UnitPolicy(relation="metrics-endpoint")]
        )

        self._events_to_observe = [self.ingress.on.ready]

    def get_status(self) -> StatusBase:
        return ActiveStatus()

    def _configure_app_leader(self, event):
        if self.ingress.is_ready():
            try:
                self.ingress.submit_config(self._istio_ingress_route_config)
            except Exception as e:
                raise GenericCharmRuntimeError(f"Failed to submit ingress config: {e}")
        else:
            logger.debug("Ambient ingress relation not ready, skipping config submission.")

    @property
    def _istio_ingress_route_config(self) -> IstioIngressRouteConfig:
        http_listener = Listener(port=80, protocol=ProtocolType.HTTP)

        return IstioIngressRouteConfig(
            model=self.model.name,
            listeners=[http_listener],
            http_routes=[
                HTTPRoute(
                    name="http-ingress",
                    listener=http_listener,
                    matches=[HTTPRouteMatch(path=HTTPPathMatch(value=self.path_prefix))],
                    backends=[
                        BackendRef(
                            service=self.model.app.name,
                            port=int(self.model.config["http-port"]),
                        )
                    ],
                )
            ],
        )
