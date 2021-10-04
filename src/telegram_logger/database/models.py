from datetime import datetime
from typing import Annotated, TypeAlias

from sqlalchemy import BigInteger, Index, Integer, func
from sqlalchemy.ext.asyncio import (
    AsyncAttrs,
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, registry

from telegram_logger.settings import Settings

Int16: TypeAlias = Annotated[int, 16]
Int64: TypeAlias = Annotated[int, 64]


class Base(AsyncAttrs, DeclarativeBase):
    registry = registry(type_annotation_map={Int16: Integer, Int64: BigInteger})


class DbMessage(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, unique=True)
    from_id: Mapped[Int64] = mapped_column()
    chat_id: Mapped[Int64] = mapped_column()
    type: Mapped[int] = mapped_column()
    msg_text: Mapped[str] = mapped_column()
    media: Mapped[bytes] = mapped_column(nullable=True)
    noforwards: Mapped[bool] = mapped_column(default=False)
    self_destructing: Mapped[bool] = mapped_column(default=False)

    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    edited_at: Mapped[datetime] = mapped_column(nullable=True)

    __table_args__ = (Index("messages_created_index", created_at.desc()),)


async def register_models() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


engine: AsyncEngine = create_async_engine(url=Settings().build_sqlite_url())
async_session = async_sessionmaker(bind=engine, expire_on_commit=False)
