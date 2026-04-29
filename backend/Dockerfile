# Hub image — installs shared kernel + FastAPI app (see docs/plan.md).
# Build from repository root: `docker build -f backend/Dockerfile .`

FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN pip install --no-cache-dir --upgrade pip

WORKDIR /app

COPY packages/agent-hub-core /app/packages/agent-hub-core
COPY backend /app/backend

WORKDIR /app/backend
RUN pip install --no-cache-dir -e /app/packages/agent-hub-core -e .

EXPOSE 8000

# Run migrations against the same DB URL as the app, then start the API.
CMD ["sh", "-c", "alembic -c /app/packages/agent-hub-core/alembic.ini upgrade head && exec uvicorn main:app --host 0.0.0.0 --port 8000"]
