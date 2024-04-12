# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
"""Constants module including constants used in tests."""
from pathlib import Path

import yaml

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
CHARM_ROOT = "."
ENVOY_APP_NAME = "envoy"

MLMD = "mlmd"
MLMD_CHANNEL = "latest/edge"
MLMD_TRUST = False

ISTIO_OPERATORS_CHANNEL = "latest/edge"
ISTIO_PILOT = "istio-pilot"
ISTIO_PILOT_TRUST = True
ISTIO_PILOT_CONFIG = {"default-gateway": "kubeflow-gateway"}
ISTIO_GATEWAY = "istio-gateway"
ISTIO_GATEWAY_APP_NAME = "istio-ingressgateway"
ISTIO_GATEWAY_TRUST = True
ISTIO_GATEWAY_CONFIG = {"kind": "ingress"}

PROMETHEUS_K8S = "prometheus-k8s"
PROMETHEUS_K8S_CHANNEL = "1.0/stable"
PROMETHEUS_K8S_TRUST = True
GRAFANA_K8S = "grafana-k8s"
GRAFANA_K8S_CHANNEL = "1.0/stable"
GRAFANA_K8S_TRUST = True
PROMETHEUS_SCRAPE_K8S = "prometheus-scrape-config-k8s"
PROMETHEUS_SCRAPE_K8S_CHANNEL = "1.0/stable"
PROMETHEUS_SCRAPE_CONFIG = {"scrape_interval": "30s"}
