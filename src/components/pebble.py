import dataclasses

from charmed_kubeflow_chisme.components import PebbleServiceComponent
from ops.pebble import Layer


@dataclasses.dataclass
class EnvoyPebbleServiceInputs:
    """Defines the required inputs for EnvoyPebbleService."""

    config_path: str


class EnvoyPebbleService(PebbleServiceComponent):
    def get_layer(self) -> Layer:
        """Pebble configuration layer for Envoy."""
        config_path = self._inputs_getter().config_path

        layer = Layer(
            {
                "services": {
                    self.service_name: {
                        "override": "replace",
                        "summary": "envoy service",
                        "startup": "enabled",
                        "command": ("/usr/local/bin/envoy" " -c" f" {config_path}"),
                    }
                }
            }
        )

        return layer
