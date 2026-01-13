import logging

from charmed_kubeflow_chisme.components import Component
from ops import ActiveStatus, BlockedStatus, StatusBase

SDI_RELATION = "ingress"
ISTIO_RELATION = "istio-ingress-route"

logger = logging.getLogger(__name__)


class IstioRelationsConflictDetector(Component):
    """Component that puts the charm to blocked state if both mesh and SDI relations are found."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def get_status(self) -> StatusBase:
        sdi_relation = self._charm.model.get_relation(SDI_RELATION)
        istio_relation = self._charm.model.get_relation(ISTIO_RELATION)

        if sdi_relation and istio_relation:
            logger.warn(
                f"Both SDI relation '{SDI_RELATION}` and Istio relation '{ISTIO_RELATION}' found."
                f"Please only relate to one of the relations, and not both at the same time"
            )
            return BlockedStatus(
                f"Both '{SDI_RELATION}' and '{ISTIO_RELATION}' relations found. Please choose one."
            )

        return ActiveStatus()
