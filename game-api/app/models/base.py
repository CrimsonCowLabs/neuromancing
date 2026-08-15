from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

from ..config import get_settings

# Everything game-api owns lives in the "game" Postgres schema.
metadata_obj = MetaData(schema=get_settings().db_schema)


class Base(DeclarativeBase):
    metadata = metadata_obj
