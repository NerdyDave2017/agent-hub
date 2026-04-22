"""FastAPI dependencies shared by routers."""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db

DbSession = Annotated[AsyncSession, Depends(get_db)]
