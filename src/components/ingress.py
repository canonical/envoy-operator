import dataclasses
import logging
from typing import Optional

from charmed_kubeflow_chisme.components import Component
from ops import ActiveStatus, BlockedStatus, StatusBase


@dataclasses.dataclass
class IngressRelationWarnIfMissingInputs:
    """Defines the required inputs for IngressRelationWarnIfMissing."""

    interface: Optional[dict]


class IngressRelationWarnIfMissing(Component):
    """Component that logs a warning if we have no Ingress relation established."""

    def __init__(self, *args, **kwargs):
        """Component that logs a warning if we have no Ingress relation established."""
        super().__init__(*args, **kwargs)
        # Attach a logger.  Use this instead of the global one to make mocking easier
        self.logger = logging.getLogger(__name__)

    def get_status(self) -> StatusBase:
        """Always Active unless it cannot get inputs."""
        try:
            inputs = self._inputs_getter()
            if not inputs.interface:
                self.logger.warning(
                    "No ingress relation established.  To be used by KFP in Charmed Kubeflow, this"
                    " charm requires an Ingress relation."
                )
                return ActiveStatus("Active but no ingress relation established")

            return ActiveStatus()
        except Exception as e:
            self.logger.error(f"Error getting inputs for IngressRelationWarnIfMissing: {e}")
            return BlockedStatus("Error getting inputs.  See logs")
