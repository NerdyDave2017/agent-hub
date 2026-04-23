"""
Domain layer — **no FastAPI**, no SQLAlchemy.

* **enums** — string enums shared by ORM models, Pydantic schemas, and services.
* **exceptions** — errors raised by `services.*` in the hub; HTTP mapping stays in `main.py`.
"""
