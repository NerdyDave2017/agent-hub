"""Single DeclarativeBase for every table in this app."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Subclass this for each model class so they all share one metadata catalog."""
