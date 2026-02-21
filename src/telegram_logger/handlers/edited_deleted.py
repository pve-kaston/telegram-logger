from __future__ import annotations

import os
import pickle
import shutil

from telethon import events
from telethon.errors import FileMigrateError, FileReferenceExpiredError
from telethon.tl import types


def _ids_from_event(event, limit: int) -> list[int]:
    if isinstance(event, events.MessageDeleted.Event):
        return event.deleted_ids[:limit]
    if isinstance(event, types.UpdateReadMessagesContents):
        return event.messages[:limit]
    if isinstance(event, events.MessageEdited.Event):
        return [event.message.id]
    return []


async def _safe_send(client, chat_id: int, text: str, limit: int = 4096):
    if not text:
        return
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    await client.send_message(chat_id, text)


async def _refetch_message(client, chat_id: int, msg_id: int):
    try:
        return await client.get_messages(chat_id, ids=msg_id)
    except (FileReferenceExpiredError, FileMigrateError):
        return await client.get_messages(chat_id, ids=msg_id)


async def edited_deleted_handler(event, client, db, buffer_storage, deleted_storage, settings, my_id):
    if isinstance(event, events.MessageEdited.Event):
        if not settings.save_edited_messages:
            return
        ids = [event.message.id]
        rows = await db.get_messages_by_event(event.chat_id, ids)
        for row in rows:
            if row.media:
                continue
            old_text = (row.msg_text or "").strip()
            new_text = (event.message.text or "").strip()
            if old_text != new_text:
                await _safe_send(
                    client,
                    settings.log_chat_id,
                    f"**✏ Edited text message**\n**Before:**\n```{old_text}```\n**After:**\n```{new_text}```",
                )
        return

    if not isinstance(event, (events.MessageDeleted.Event, types.UpdateReadMessagesContents)):
        return

    ids = _ids_from_event(event, settings.max_deleted_messages_per_event)
    rows = await db.get_messages_by_event(getattr(event, "chat_id", None), ids)

    for row in rows:
        if row.from_id in settings.ignored_ids or row.chat_id in settings.ignored_ids:
            continue

        if row.media:
            src = buffer_storage.buffer_find(row.id, row.chat_id)
            if not src:
                fresh = await _refetch_message(client, row.chat_id, row.id)
                if fresh and getattr(fresh, "media", None):
                    src = await buffer_storage.buffer_save(fresh)
            if not src:
                continue

            if deleted_storage:
                enc_path = await deleted_storage.deleted_put_from_buffer(src)
                if not enc_path:
                    continue
                with deleted_storage.deleted_open_for_upload(enc_path) as f:
                    await client.send_file(settings.log_chat_id, f, caption=row.msg_text or "")
            else:
                os.makedirs(settings.media_deleted_dir, exist_ok=True)
                dst = os.path.join(settings.media_deleted_dir, os.path.basename(src))
                shutil.copy2(src, dst)
                await client.send_file(settings.log_chat_id, dst, caption=row.msg_text or "")
        elif row.msg_text:
            await _safe_send(client, settings.log_chat_id, f"**Deleted message:**\n{row.msg_text}")
