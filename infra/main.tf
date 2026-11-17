terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

variable "project_id" {
  type        = string
  description = "GCP Project ID hosting the Healthcare PA ADK stack."
}

variable "region" {
  type        = string
  default     = "us-central1"
  description = "Primary GCP region for Vertex AI Agent Engine and Firestore."
}

resource "google_project_service" "required_apis" {
  for_each = toset([
    "aiplatform.googleapis.com",
    "dlp.googleapis.com",
    "firestore.googleapis.com",
    "secretmanager.googleapis.com",
  ])
  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}

resource "google_service_account" "pa_agent_sa" {
  account_id   = "healthcare-pa-adk-sa"
  display_name = "Healthcare PA ADK Runtime Service Account"
}

resource "google_project_iam_member" "pa_agent_roles" {
  for_each = toset([
    "roles/aiplatform.user",
    "roles/datastore.user",
    "roles/dlp.user",
    "roles/secretmanager.secretAccessor",
  ])
  project = var.project_id
  role    = each.key
  member  = "serviceAccount:${google_service_account.pa_agent_sa.email}"
}

resource "google_secret_manager_secret" "gemini_api_key" {
  secret_id = "GEMINI_API_KEY"
  replication {
    auto {}
  }
  depends_on = [google_project_service.required_apis]
}

resource "google_firestore_database" "pa_sessions" {
  name        = "pa-sessions"
  location_id = var.region
  type        = "FIRESTORE_NATIVE"
  depends_on  = [google_project_service.required_apis]
}
