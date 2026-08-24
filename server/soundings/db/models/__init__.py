from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    metadata = MetaData()


metadata = Base.metadata

from soundings.db.models import (  # noqa: E402, F401
    answer_cache,
    cache,
    catalogue,
    corpus,
    data,
    geography,
)
from soundings.db.models.observation import Observation  # noqa: E402, F401
