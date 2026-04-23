"""
Run the worker as a module.

From the **repository root** (after ``uv sync``):

    uv run python -m worker

Shared settings, DB, and SQS envelope types come from **``agent-hub-core``**
(``import agent_hub_core...``). Optional: set ``DATABASE_URL`` / ``SQS_QUEUE_URL`` / AWS vars
via ``backend/.env`` (auto-loaded when present) or your shell — see README.
"""

from __future__ import annotations

import asyncio

from worker.main import run


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
