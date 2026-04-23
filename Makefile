# Local development orchestration (GitHub Actions are not used on your laptop).
# Prerequisites: Docker / Docker Compose, Terraform >= 1.5, curl.
#
# Typical first run:
#   make local-up
#   make local-provision
#   make local-apps-up
#
# Shell note: use `docker compose` (Compose v2), not legacy `docker-compose`.

.PHONY: help local-up local-down local-provision local-sync-env local-apps-up \
	local-apps-build local-agent-up local-logs local-terraform-destroy \
	local-wait-localstack local-print-env local-all

help:
	@echo "agent-hub — local Makefile"
	@echo ""
	@echo "  make local-up              Postgres + LocalStack (background)"
	@echo "  make local-provision       Wait for LocalStack, terraform apply, write localstack.auto.env"
	@echo "  make local-apps-up         Hub + worker (requires localstack.auto.env — run provision first)"
	@echo "  make local-apps-build      Rebuild hub + worker images then up (with env file)"
	@echo "  make local-agent-up        Incident triage agent on :8001"
	@echo "  make local-print-env       Print shell exports for host-run uvicorn (copy/paste)"
	@echo "  make local-down            Stop all compose services"
	@echo "  make local-terraform-destroy  terraform destroy in infra/localstack (reset emulated AWS)"
	@echo "  make local-all             local-up + local-provision + local-apps-build (first-time batteries included)"

LOCALSTACK_HEALTH_URL ?= http://127.0.0.1:4566/_localstack/health
TF_DIR := infra/localstack

local-all: local-up local-provision local-apps-build

local-up:
	docker compose up -d postgres localstack

local-down:
	docker compose down

local-wait-localstack:
	@echo "Waiting for LocalStack at $(LOCALSTACK_HEALTH_URL) ..."
	@attempts=0; \
	while [ $$attempts -lt 60 ]; do \
		if curl -sf "$(LOCALSTACK_HEALTH_URL)" >/dev/null 2>&1; then \
			echo "LocalStack is healthy."; \
			exit 0; \
		fi; \
		attempts=$$((attempts + 1)); \
		sleep 2; \
	done; \
	echo "LocalStack did not become healthy in time."; \
	exit 1

local-provision: local-wait-localstack
	cd $(TF_DIR) && terraform init -input=false
	cd $(TF_DIR) && terraform apply -auto-approve -input=false
	$(MAKE) local-sync-env
	@echo ""
	@echo "Provisioned. Next: make local-apps-up   (or: docker compose --env-file localstack.auto.env up -d hub worker)"

local-sync-env:
	@test -d $(TF_DIR)/.terraform || (echo "Run \"make local-provision\" first (no .terraform yet)."; exit 1)
	@queue=$$(cd $(TF_DIR) && terraform output -raw sqs_queue_url); \
	dlq=$$(cd $(TF_DIR) && terraform output -raw sqs_dlq_url); \
	{ printf 'SQS_QUEUE_URL=%s\n' "$$queue"; printf 'SQS_DLQ_URL=%s\n' "$$dlq"; printf 'AWS_REGION=us-east-1\n'; } > localstack.auto.env
	@echo "Wrote localstack.auto.env (gitignored) with queue URLs from Terraform."

local-apps-up:
	@test -f localstack.auto.env || (echo "Missing localstack.auto.env — run: make local-provision"; exit 1)
	docker compose --env-file localstack.auto.env up -d hub worker

local-apps-build:
	@test -f localstack.auto.env || (echo "Missing localstack.auto.env — run: make local-provision"; exit 1)
	docker compose --env-file localstack.auto.env up -d --build hub worker

local-agent-up:
	docker compose up -d incident-triage

local-print-env:
	@test -f localstack.auto.env || (echo "Missing localstack.auto.env — run: make local-provision"; exit 1)
	@echo "# Paste into your shell for uv run hub/worker on the host (use 127.0.0.1 for LocalStack):"
	@echo "export AWS_REGION=us-east-1"
	@echo "export AWS_ENDPOINT_URL=http://127.0.0.1:4566"
	@echo "export AWS_ACCESS_KEY_ID=test"
	@echo "export AWS_SECRET_ACCESS_KEY=test"
	@grep '^SQS_' localstack.auto.env | sed 's/^/export /'

local-logs:
	docker compose logs -f hub worker localstack postgres

local-terraform-destroy: local-wait-localstack
	cd $(TF_DIR) && terraform destroy -auto-approve -input=false
	@rm -f localstack.auto.env
	@echo "Removed localstack.auto.env (if present). Queues/IAM/secrets cleared in LocalStack state."
