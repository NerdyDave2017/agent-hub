#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# LocalStack **ready** hook — runs once SQS is up (`/etc/localstack/init/ready.d`).
#
# On the **host**, this file lives under `scripts/localstack-init/ready.d/` and is
# bind-mounted by `docker-compose.yml` to that container path.
#
# What we create
# --------------
# * **agent-hub-jobs-dlq** — dead-letter queue; messages that fail `maxReceiveCount`
#   times land here for inspection (see `docs/plan.md` DLQ semantics).
# * **agent-hub-jobs** — main work queue the hub will `SendMessage` to; points at
#   the DLQ via `RedrivePolicy` so poison pills do not retry forever.
#
# Idempotency
# -----------
# Safe to re-run: if a queue name already exists we skip creation so compose restarts
# do not fail the whole init stage.
# -----------------------------------------------------------------------------

set -euo pipefail

echo "[01-init-sqs] provisioning SQS queues..."

if ! awslocal sqs get-queue-url --queue-name agent-hub-jobs-dlq &>/dev/null; then
  awslocal sqs create-queue --queue-name agent-hub-jobs-dlq
  echo "[01-init-sqs] created agent-hub-jobs-dlq"
else
  echo "[01-init-sqs] agent-hub-jobs-dlq already exists"
fi

DLQ_URL="$(awslocal sqs get-queue-url --queue-name agent-hub-jobs-dlq --query QueueUrl --output text)"
DLQ_ARN="$(awslocal sqs get-queue-attributes --queue-url "$DLQ_URL" --attribute-names QueueArn --query Attributes.QueueArn --output text)"
export DLQ_ARN

if ! awslocal sqs get-queue-url --queue-name agent-hub-jobs &>/dev/null; then
  # AWS expects RedrivePolicy attribute to be a **string** whose contents are JSON.
  python3 <<'PY'
import json, os

arn = os.environ["DLQ_ARN"]
inner = json.dumps({"deadLetterTargetArn": arn, "maxReceiveCount": "5"})
attrs = {"RedrivePolicy": inner}
with open("/tmp/agent-hub-jobs-attrs.json", "w", encoding="utf-8") as f:
    json.dump(attrs, f)
PY
  awslocal sqs create-queue --queue-name agent-hub-jobs --attributes file:///tmp/agent-hub-jobs-attrs.json
  echo "[01-init-sqs] created agent-hub-jobs (redrive -> DLQ)"
else
  echo "[01-init-sqs] agent-hub-jobs already exists"
fi

echo "[01-init-sqs] done."
