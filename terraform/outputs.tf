output "app_name" {
  value = juju_application.envoy.name
}

output "provides" {
  value = {
    grafana_dashboard = "grafana-dashboard",
    metrics_endpoint  = "metrics-endpoint",
    provide_cmr_mesh  = "provide-cmr-mesh"
  }
}

output "requires" {
  value = {
    grpc                = "grpc",
    ingress             = "ingress",
    istio_ingress_route = "istio-ingress-route",
    logging             = "logging",
    require_cmr_mesh    = "require-cmr-mesh",
    service_mesh        = "service-mesh"
  }
}
