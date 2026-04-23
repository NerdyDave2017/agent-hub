# Worker image — shared kernel + SQS consumer only; **no** Alembic (`response.md` Phase 7).
# Build from repository root: `docker build -f worker/Dockerfile .`

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir --upgrade pip

WORKDIR /app

COPY packages/agent-hub-core /app/packages/agent-hub-core
COPY worker /app/worker

WORKDIR /app/worker
RUN pip install --no-cache-dir -e /app/packages/agent-hub-core -e .

CMD ["python", "-m", "worker"]
