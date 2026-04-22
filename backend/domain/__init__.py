"""
Domain layer — **no FastAPI**, no SQLAlchemy.

* **`domain.enums`** — string enums shared by ORM models, Pydantic schemas, and services.
* **`domain.exceptions`** — errors raised by `services.*`; HTTP status mapping lives in `main.py`.
"""
