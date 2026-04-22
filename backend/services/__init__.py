"""
Domain **services** — all database access used by HTTP routers (and callable internally).

Routers in `apis/` should stay thin: parse/validate HTTP, call these modules, map ORM rows
to Pydantic response models. Services raise **`domain.exceptions`**; `main.py` maps those to
HTTP responses. Later, **access guards** (e.g. who may call job mutations) can wrap or live
alongside these functions.

Layout
------
* `tenants_service` — tenant registry CRUD and `require_tenant` guard.
* `agents_service` — agents scoped to a tenant.
* `jobs_service` — durable jobs + optional SQS publish (also used when other flows enqueue work).
"""
