variable "project_id" {
  description = "GCP project id the Cloud Run service and supporting resources live in."
  type        = string
}

variable "region" {
  description = "GCP region for Cloud Run, Artifact Registry, and the WIF pool."
  type        = string
  default     = "us-central1"
}

variable "service_name" {
  description = "Cloud Run service name (also used to derive resource names)."
  type        = string
  default     = "mangomas"
}

variable "image" {
  description = <<-EOT
    Fully-qualified container image to deploy, e.g.
    us-central1-docker.pkg.dev/PROJECT/REPO/mangomas:TAG. The deploy workflow
    builds and pushes this image, then passes the tag in via -var.
  EOT
  type        = string
}

variable "artifact_repository_id" {
  description = "Artifact Registry repository id (Docker format) that stores the image."
  type        = string
  default     = "mangomas"
}

variable "github_repo" {
  description = <<-EOT
    GitHub repository in OWNER/REPO form allowed to impersonate the deploy
    service account via Workload Identity Federation. Only OIDC tokens whose
    `repository` claim matches are accepted.
  EOT
  type        = string
}

variable "env" {
  description = <<-EOT
    Plain (non-secret) MANGOMAS_* environment variables injected into the Cloud
    Run container. Secrets must be referenced via Secret Manager + the app's
    secrets seam, never placed here. See deploy/README.md for the contract.
  EOT
  type        = map(string)
  default     = {}
}

variable "min_instances" {
  description = "Minimum Cloud Run instances (0 scales to zero when idle)."
  type        = number
  default     = 0
}

variable "max_instances" {
  description = "Maximum Cloud Run instances (autoscaling ceiling)."
  type        = number
  default     = 4
}

variable "container_port" {
  description = "Container port the service listens on (matches the Dockerfile $PORT)."
  type        = number
  default     = 8000
}

variable "cpu" {
  description = "CPU allocation per Cloud Run instance."
  type        = string
  default     = "1"
}

variable "memory" {
  description = "Memory allocation per Cloud Run instance."
  type        = string
  default     = "512Mi"
}

variable "allow_unauthenticated" {
  description = <<-EOT
    When true, grants roles/run.invoker to allUsers (public service). Default
    false keeps the service private (IAM-gated) — recommended for production.
  EOT
  type        = bool
  default     = false
}
