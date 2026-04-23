# Incident triage agent (incremental)

**Current step:** FastAPI app + **settings** loaded at startup (`pydantic-settings`), JSON logging via `agent-hub-core`, `GET /health`, `GET /api/v1/meta` (non-secret config only).

## Run

From repo root:

```bash
uv sync
uv run --package agent-hub-incident-triage uvicorn incident_triage.main:app --reload --port 8001
```

Or: `docker compose up incident-triage`.

## Env

| Variable | Default |
| --- | --- |
| `APP_NAME` | `incident-triage` |
| `API_V1_PREFIX` | `/api/v1` |
| `HOST` | `0.0.0.0` |
| `PORT` | `8001` |
| `OPENAI_API_KEY` | _(empty)_ — required for real classify; optional `OPENAI_SECRET_ARN` loads from Secrets Manager |
| `CLASSIFY_MODEL` | `gpt-4o-mini` |
| `SLACK_BOT_TOKEN` | _(empty)_ — `xoxb-…` for Slack alerts; optional `SLACK_SECRET_ARN` |
| `SLACK_OPS_CHANNEL` | `#ops-alerts` |
| `GMAIL_CREDENTIALS` | _(empty)_ — JSON OAuth user (`refresh_token`, `client_id`, `client_secret`, optional `token`); or load via `GMAIL_SECRET_ARN` |
| `GMAIL_USER_ID` | `me` |
| `GMAIL_MARK_READ` | `true` — remove `UNREAD` after graph reaches `mark_read` |
| `GMAIL_POLL_INTERVAL_SECONDS` | `0` — set `> 0` to poll unread mail on that interval |

Files loaded (if present): `.env` at repo root, then `agents/incident-triage/.env`.
