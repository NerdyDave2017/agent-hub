#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# LocalStack ready hook — **SQS queues are created by Terraform** (see Makefile
# `local-provision`). This script stays so the bind-mounted `ready.d/` directory
# remains valid; it performs no AWS calls.
# -----------------------------------------------------------------------------

set -euo pipefail

echo "[01-init-sqs] skipped — use: make local-provision (Terraform owns SQS + IAM + secrets)."
