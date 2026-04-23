# Incident triage agent — LangGraph + LiteLLM + Langfuse (see agents/incident-triage/README.md).
# Build from repository root: `docker build -f agents/incident-triage/Dockerfile .`

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir --upgrade pip

WORKDIR /app

COPY packages/agent-hub-core /app/packages/agent-hub-core
COPY agents/incident-triage /app/agents/incident-triage

WORKDIR /app/agents/incident-triage
RUN pip install --no-cache-dir -e /app/packages/agent-hub-core -e .

EXPOSE 8001

CMD ["uvicorn", "incident_triage.main:app", "--host", "0.0.0.0", "--port", "8001"]
