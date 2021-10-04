import logging
from datetime import datetime, timedelta
from typing import List, Union

from sqlalchemy import and_, delete, or_, select
from telethon.events import MessageDeleted, MessageEdited
from telethon.tl.types import UpdateReadMessagesContents

from telegram_logger.database import DbMessage, async_session
from telegram_logger.settings import settings
from telegram_logger.tg_types import ChatType


async def message_exists(msg_id: int) -> bool:
    async with async_session() as session:
        query = select(DbMessage.id).where(DbMessage.id == msg_id)
        return bool((await session.execute(query)).scalar())


async def save_message(
    msg_id: int,
    from_id: int,
    chat_id: int,
    type: int,
    msg_text: str,
    media: bytes,
    noforwards: bool,
    self_destructing: bool,
    created_at: datetime,
    edited_at: datetime,
) -> None:
    message = DbMessage(
        id=msg_id,
        from_id=from_id,
        chat_id=chat_id,
        type=type,
        msg_text=msg_text,
        media=media,
        noforwards=noforwards,
        self_destructing=self_destructing,
        created_at=created_at,
        edited_at=edited_at,
    )

    async with async_session() as session:
        session.add(message)
        await session.commit()


async def get_message_ids_by_event(
    event: Union[MessageDeleted.Event, MessageEdited.Event, UpdateReadMessagesContents],
    ids: List[int],
) -> List[DbMessage]:
    if hasattr(event, "chat_id") and event.chat_id:
        where_clause = (DbMessage.chat_id == event.chat_id, DbMessage.id.in_(ids))
    else:
        where_clause = (DbMessage.chat_id.notlike("-100%"), DbMessage.id.in_(ids))

    async with async_session() as session:
        query = (
            select(
                DbMessage.id,
                DbMessage.from_id,
                DbMessage.chat_id,
                DbMessage.msg_text,
                DbMessage.media,
                DbMessage.noforwards,
                DbMessage.self_destructing,
                DbMessage.created_at,
            )
            .where(*where_clause)  # apply the where clause
            .order_by(DbMessage.edited_at.desc())  # order by edited time
            .distinct(DbMessage.chat_id, DbMessage.id)  # group by chat id and id
            .order_by(DbMessage.created_at.asc())  # order by created time
        )

        return (await session.execute(query)).all()


async def delete_expired_messages_from_db(current_time: datetime) -> None:
    # calculate the expiry times for different chat types
    time_user = current_time - timedelta(days=settings.persist_time_in_days_user)
    time_channel = current_time - timedelta(days=settings.persist_time_in_days_channel)
    time_group = current_time - timedelta(days=settings.persist_time_in_days_group)
    time_bot = current_time - timedelta(days=settings.persist_time_in_days_bot)
    time_unknown = current_time - timedelta(days=settings.persist_time_in_days_group)

    where_clause = or_(
        and_(DbMessage.type == ChatType.USER.value, DbMessage.created_at < time_user),
        and_(DbMessage.type == ChatType.CHANNEL.value, DbMessage.created_at < time_channel),
        and_(DbMessage.type == ChatType.GROUP.value, DbMessage.created_at < time_group),
        and_(DbMessage.type == ChatType.BOT.value, DbMessage.created_at < time_bot),
        and_(DbMessage.type == ChatType.UNKNOWN.value, DbMessage.created_at < time_unknown),
    )

    async with async_session() as session:
        result = await session.execute(delete(DbMessage).where(where_clause))
        await session.commit()

        rowcount = result.rowcount
        if rowcount > 0:
            logging.info(f"deleted {rowcount} expired messages from db")
