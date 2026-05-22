"""Charms dependencies for tests."""

from charmed_kubeflow_chisme.testing import CharmSpec

MLMD = CharmSpec(charm="mlmd", channel="ckf-1.10/edge", trust=True)
ISTIO_GATEWAY = CharmSpec(
    charm="istio-gateway", channel="1.28/edge", config={"kind": "ingress"}, trust=True
)
ISTIO_PILOT = CharmSpec(
    charm="istio-pilot",
    channel="1.28/edge",
    config={"default-gateway": "kubeflow-gateway"},
    trust=True,
)
