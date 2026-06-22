locals {
  # Derived, non-hard-coded resource names. Everything keys off the inputs so a
  # second environment is a new tfvars file, not a fork of this module.
  runtime_sa_id = "${var.service_name}-run"
  deploy_sa_id  = "${var.service_name}-deployer"
  wif_pool_id   = "${var.service_name}-gh-pool"
  wif_prov_id   = "${var.service_name}-gh-provider"

  # Least-privilege project roles for the Cloud Run runtime identity. Iterated
  # via for_each so adding a capability is a one-line list edit.
  runtime_roles = toset([
    "roles/secretmanager.secretAccessor", # GCP Secret Manager seam
    "roles/cloudtrace.agent",             # Cloud Trace span export
    "roles/cloudsql.client",              # Cloud SQL / Postgres storage
    "roles/aiplatform.user",              # Vertex AI LLM / embeddings
  ])

  # Project-level roles the CI deployer needs to build, push, and roll out a
  # revision. ``iam.serviceAccountUser`` is deliberately NOT here — it is granted
  # on the runtime SA only (below) so the deployer can impersonate that one
  # account, not every service account in the project.
  deployer_roles = toset([
    "roles/run.admin",               # deploy Cloud Run revisions
    "roles/artifactregistry.writer", # push images
  ])
}

# ── Artifact Registry ─────────────────────────────────────────────────────────

resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = var.artifact_repository_id
  format        = "DOCKER"
  description   = "Container images for the ${var.service_name} Cloud Run service."
}

# ── Runtime identity ──────────────────────────────────────────────────────────

resource "google_service_account" "runtime" {
  account_id   = local.runtime_sa_id
  display_name = "${var.service_name} Cloud Run runtime"
}

resource "google_project_iam_member" "runtime_roles" {
  for_each = local.runtime_roles

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.runtime.email}"
}

# ── Cloud Run service ─────────────────────────────────────────────────────────

resource "google_cloud_run_v2_service" "service" {
  name     = var.service_name
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  template {
    service_account = google_service_account.runtime.email

    scaling {
      min_instance_count = var.min_instances
      max_instance_count = var.max_instances
    }

    containers {
      image = var.image

      ports {
        container_port = var.container_port
      }

      resources {
        limits = {
          cpu    = var.cpu
          memory = var.memory
        }
      }

      # Plain MANGOMAS_* config injected from the env map. Secrets are resolved
      # at runtime via Secret Manager, never materialised here.
      dynamic "env" {
        for_each = var.env
        content {
          name  = env.key
          value = env.value
        }
      }

      startup_probe {
        http_get {
          path = "/healthz"
          port = var.container_port
        }
      }

      liveness_probe {
        http_get {
          path = "/healthz"
          port = var.container_port
        }
      }
    }
  }

  # The image tag changes every deploy; ignore drift so `terraform apply` from
  # the workflow is the single source of truth for the deployed revision.
  lifecycle {
    ignore_changes = [client, client_version]
  }
}

resource "google_cloud_run_v2_service_iam_member" "invoker" {
  count = var.allow_unauthenticated ? 1 : 0

  location = google_cloud_run_v2_service.service.location
  name     = google_cloud_run_v2_service.service.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ── Workload Identity Federation (keyless GitHub Actions auth) ─────────────────

resource "google_service_account" "deployer" {
  account_id   = local.deploy_sa_id
  display_name = "${var.service_name} CI deployer"
}

resource "google_project_iam_member" "deployer_roles" {
  for_each = local.deployer_roles

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.deployer.email}"
}

# Least-privilege: let the deployer act as the runtime SA only (scoped to that
# one account) rather than at the project level.
resource "google_service_account_iam_member" "deployer_act_as_runtime" {
  service_account_id = google_service_account.runtime.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.deployer.email}"
}

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = local.wif_pool_id
  display_name              = "${var.service_name} GitHub pool"
  description               = "Trust pool for GitHub Actions OIDC tokens."
}

resource "google_iam_workload_identity_pool_provider" "github" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = local.wif_prov_id
  display_name                       = "GitHub OIDC"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }

  # Only tokens from the named repository are accepted.
  attribute_condition = "assertion.repository == \"${var.github_repo}\""

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

# Let workflows from var.github_repo impersonate the deployer SA.
resource "google_service_account_iam_member" "deployer_wif" {
  service_account_id = google_service_account.deployer.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
}
