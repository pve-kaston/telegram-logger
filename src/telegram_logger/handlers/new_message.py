from __future__ import annotations

import pickle
from datetime import datetime, timezone

from telethon.events import MessageEdited
from telethon.tl import types

from telegram_logger.handlers.restricted_saver import maybe_handle_restricted_link
from telegram_logger.tg_types import ChatType


def _extract_media(message):
    return message.media or getattr(message, "video_note", None)


def _sender_id(message, my_id: int) -> int:
    if isinstance(message.peer_id, types.PeerUser):
        return my_id if message.out else message.peer_id.user_id
    if isinstance(message.from_id, types.PeerUser):
        return message.from_id.user_id
    if isinstance(message.from_id, types.PeerChannel):
        return message.from_id.channel_id
    return 0


async def _chat_type(event) -> ChatType:
    if event.is_group:
        return ChatType.GROUP
    if event.is_channel:
        return ChatType.CHANNEL
    if event.is_private:
        sender = await event.get_sender()
        return ChatType.BOT if getattr(sender, "bot", False) else ChatType.USER
    return ChatType.UNKNOWN


async def _noop_save_restricted(_link: str) -> None:
    return None

async def new_message_handler(event, client, db, buffer_storage, settings, my_id, save_restricted_fn=None):
    if save_restricted_fn is None:
        save_restricted_fn = _noop_save_restricted
    if await maybe_handle_restricted_link(event, settings, my_id, save_restricted_fn):
        return

    chat_id = event.chat_id or 0
    from_id = _sender_id(event.message, my_id)

    if from_id in settings.ignored_ids or chat_id in settings.ignored_ids:
        return

    if event.is_private and event.chat_id == my_id:
        return

    noforwards = bool(getattr(getattr(event, "chat", None), "noforwards", False) or event.message.noforwards)
    self_destructing = bool(getattr(getattr(event.message, "media", None), "ttl_seconds", None))
    media = _extract_media(event.message)

    if media and (self_destructing or noforwards or settings.buffer_all_media):
        await buffer_storage.buffer_save(event.message)

    if await db.message_exists(event.message.id, chat_id):
        return

    await db.save_message(
        id=event.message.id,
        from_id=from_id,
        chat_id=chat_id,
        type=(await _chat_type(event)).value,
        msg_text=event.message.text,
        media=pickle.dumps(media) if media else None,
        noforwards=noforwards,
        self_destructing=self_destructing,
        created_at=datetime.now(timezone.utc),
        edited_at=datetime.now(timezone.utc) if isinstance(event, MessageEdited.Event) else None,
    )
