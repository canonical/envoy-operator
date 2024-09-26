output "app_name" {
  value = juju_application.envoy.name
}

output "provides" {
  value = {
    metrics_endpoint  = "metrics-endpoint",
    grafana_dashboard = "grafana-dashboard"
  }
}

output "requires" {
  value = {
    grpc    = "grpc",
    ingress = "ingress",
    logging = "logging"
  }
}
