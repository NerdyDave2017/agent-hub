# Local development orchestration (GitHub Actions are not used on your laptop).
# Prerequisites: Docker / Docker Compose, Terraform >= 1.5, curl.
#
# Typical first run:
#   make local-up
#   make local-provision
#   make local-apps-up
#
# Shell note: use `docker compose` (Compose v2), not legacy `docker-compose`.

CI_OIDC_DIR ?= infra/ci-oidc

.PHONY: help local-up local-down local-provision local-sync-env local-apps-up \
	local-apps-build local-agent-up local-logs local-terraform-destroy \
	local-wait-localstack local-print-env local-all terraform-backend-bootstrap \
	ci-oidc-github-provider-arn ci-oidc-init ci-oidc-outputs ci-oidc-apply

# Matches infra/*/backend.tf remote state settings (override if yours differ).
TF_STATE_BUCKET   ?= agent-hub-terraform-state
TF_LOCKS_TABLE    ?= agent-hub-terraform-locks
TF_BACKEND_REGION ?= us-east-1

help:
	@echo "agent-hub — local Makefile"
	@echo ""
	@echo "  make terraform-backend-bootstrap   Create S3 state bucket + DynamoDB lock table (AWS CLI; run once per account/region)"
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
	@echo ""
	@echo "GitHub Actions OIDC (AWS; use human/SSO credentials):"
	@echo "  make ci-oidc-github-provider-arn   Print existing IAM OIDC provider ARN for GitHub (token.actions.githubusercontent.com)"
	@echo "  make ci-oidc-init                  terraform init in $(CI_OIDC_DIR) (S3 backend)"
	@echo "  make ci-oidc-outputs               Print role ARN + OIDC provider ARN from Terraform state (GitHub secret: AWS_ROLE_TO_ASSUME = role)"
	@echo "  make ci-oidc-apply                 init + terraform apply in $(CI_OIDC_DIR) (interactive; creates/updates OIDC + IAM role)"

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

# One-time (per AWS account/region): S3 backend + DynamoDB state locking for infra/* roots.
# Requires: AWS CLI + credentials with s3:* and dynamodb:* on these resources.
terraform-backend-bootstrap:
	@echo "Bootstrapping Terraform backend: s3://$(TF_STATE_BUCKET) + DynamoDB $(TF_LOCKS_TABLE) ($(TF_BACKEND_REGION)) ..."
	@if aws s3api head-bucket --bucket "$(TF_STATE_BUCKET)" --region "$(TF_BACKEND_REGION)" 2>/dev/null; then \
		echo "S3 bucket already exists: $(TF_STATE_BUCKET)"; \
	else \
		echo "Creating S3 bucket $(TF_STATE_BUCKET) ..."; \
		if [ "$(TF_BACKEND_REGION)" = "us-east-1" ]; then \
			aws s3api create-bucket --bucket "$(TF_STATE_BUCKET)" --region "$(TF_BACKEND_REGION)"; \
		else \
			aws s3api create-bucket --bucket "$(TF_STATE_BUCKET)" --region "$(TF_BACKEND_REGION)" \
				--create-bucket-configuration LocationConstraint=$(TF_BACKEND_REGION); \
		fi; \
	fi
	@echo "Enabling versioning + encryption + public access block on $(TF_STATE_BUCKET) ..."
	@aws s3api put-bucket-versioning --bucket "$(TF_STATE_BUCKET)" \
		--versioning-configuration Status=Enabled --region "$(TF_BACKEND_REGION)"
	@aws s3api put-public-access-block --bucket "$(TF_STATE_BUCKET)" --region "$(TF_BACKEND_REGION)" \
		--public-access-block-configuration \
		BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
	@aws s3api put-bucket-encryption --bucket "$(TF_STATE_BUCKET)" --region "$(TF_BACKEND_REGION)" \
		--server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
	@if aws dynamodb describe-table --table-name "$(TF_LOCKS_TABLE)" --region "$(TF_BACKEND_REGION)" >/dev/null 2>&1; then \
		echo "DynamoDB table already exists: $(TF_LOCKS_TABLE)"; \
	else \
		echo "Creating DynamoDB table $(TF_LOCKS_TABLE) (primary key LockID) ..."; \
		aws dynamodb create-table --region "$(TF_BACKEND_REGION)" \
			--table-name "$(TF_LOCKS_TABLE)" \
			--billing-mode PAY_PER_REQUEST \
			--attribute-definitions AttributeName=LockID,AttributeType=S \
			--key-schema AttributeName=LockID,KeyType=HASH; \
		aws dynamodb wait table-exists --table-name "$(TF_LOCKS_TABLE)" --region "$(TF_BACKEND_REGION)"; \
	fi
	@echo "Done. Run terraform init in infra/vpc, infra/hub, etc."

# --- GitHub OIDC + Actions assume role (infra/ci-oidc) --------------------------------
# Requires: AWS CLI + credentials. Terraform needs S3 backend (make terraform-backend-bootstrap).
# Copy terraform.tfvars from $(CI_OIDC_DIR)/terraform.tfvars.example before first apply.

ci-oidc-github-provider-arn:
	@arns=$$(aws iam list-open-id-connect-providers --output text \
		--query 'OpenIDConnectProviderList[].Arn' 2>/dev/null | tr '\t' '\n' | grep 'token.actions.githubusercontent.com' || true); \
	if [ -z "$$arns" ]; then \
		echo "No IAM OIDC provider for GitHub in this account. Create it with:"; \
		echo "  make ci-oidc-apply"; \
		echo "(terraform.tfvars: create_github_oidc_provider = true unless you import an existing provider ARN)"; \
		exit 1; \
	fi; \
	echo "# Use as existing_github_oidc_provider_arn when the account already has GitHub OIDC:"; \
	echo "$$arns"; \
	cnt=$$(echo "$$arns" | grep -c . || true); \
	if [ "$$cnt" -gt 1 ]; then echo "# Warning: multiple matches; use the ARN that matches your account."; fi

ci-oidc-init:
	cd $(CI_OIDC_DIR) && terraform init -input=false

ci-oidc-outputs: ci-oidc-init
	@echo "# GitHub repository secret AWS_ROLE_TO_ASSUME (IAM role for Actions OIDC):"
	@cd $(CI_OIDC_DIR) && terraform output -raw github_actions_role_arn
	@echo ""
	@echo "# IAM OIDC identity provider ARN (from Terraform / same account as role):"
	@cd $(CI_OIDC_DIR) && terraform output -raw github_oidc_provider_arn
	@echo ""

ci-oidc-apply: ci-oidc-init
	cd $(CI_OIDC_DIR) && terraform apply
