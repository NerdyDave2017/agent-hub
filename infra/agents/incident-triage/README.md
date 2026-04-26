# Incident triage agent — Terraform root (`infra/agents/incident-triage`)

Creates the **ECR repository** for the incident-triage image and, in **non-production** environments, an optional **staging App Runner** service using `modules/app-runner`, per [`docs/terraform-infra-instructions.md`](../../../docs/terraform-infra-instructions.md).

Staging uses `worker` remote state output `agent_instance_policy_json` for the App Runner instance role policy.

## Dependencies

Remote state: **`vpc`**, **`rds`**, **`secrets`**, **`hub`**, **`worker`**.
