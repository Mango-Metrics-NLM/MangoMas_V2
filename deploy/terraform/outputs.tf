output "service_uri" {
  description = "Public URI of the deployed Cloud Run service."
  value       = google_cloud_run_v2_service.service.uri
}

output "artifact_registry_repo" {
  description = "Artifact Registry path images are pushed to (without image/tag)."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "runtime_service_account" {
  description = "Email of the Cloud Run runtime service account."
  value       = google_service_account.runtime.email
}

output "deployer_service_account" {
  description = "Email of the CI deployer service account (set as DEPLOY_SA in CI)."
  value       = google_service_account.deployer.email
}

output "workload_identity_provider" {
  description = "Full WIF provider resource name (set as WIF_PROVIDER in CI)."
  value       = google_iam_workload_identity_pool_provider.github.name
}
