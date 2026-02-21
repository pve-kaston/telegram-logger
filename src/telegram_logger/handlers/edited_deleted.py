from __future__ import annotations

import os
import re

from telethon import events
from telethon.errors import FileMigrateError, FileReferenceExpiredError
from telethon.tl import types
from telethon.tl.functions import messages as msg_funcs


def _ids_from_event(event, limit: int) -> list[int]:
    if isinstance(event, events.MessageDeleted.Event):
        return event.deleted_ids[:limit]
    if isinstance(event, types.UpdateReadMessagesContents):
        return event.messages[:limit]
    if isinstance(event, events.MessageEdited.Event):
        return [event.message.id]
    return []


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w\-. ()\[\]{}@,+=]", "_", name or "") or "file.bin"


async def _friendly_filename(client, chat_id: int, fallback_name: str) -> str:
    try:
        entity = await client.get_entity(chat_id)
        chat_name = (
            getattr(entity, "username", None)
            or getattr(entity, "title", None)
            or "_".join(filter(None, [getattr(entity, "first_name", None), getattr(entity, "last_name", None)]))
            or str(chat_id)
        )
    except Exception:
        chat_name = str(chat_id)

    base_name = os.path.basename(fallback_name)
    parts = base_name.split("_", 2)
    if len(parts) >= 3 and parts[0].lstrip("-").isdigit() and parts[1].isdigit():
        base_name = parts[2]
    return f"{_safe_name(chat_name)}_{_safe_name(base_name)}"


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


async def _send_deleted_file(client, log_chat_id: int, file_path: str, caption: str, chat_id: int):
    filename = await _friendly_filename(client, chat_id, os.path.basename(file_path))
    with open(file_path, "rb") as fh:
        uploaded = await client.upload_file(fh)
    await client(
        msg_funcs.SendMediaRequest(
            peer=log_chat_id,
            media=types.InputMediaUploadedDocument(
                file=uploaded,
                mime_type="application/octet-stream",
                attributes=[types.DocumentAttributeFilename(file_name=filename)],
            ),
            message=caption,
        )
    )


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

        if isinstance(event, types.UpdateReadMessagesContents) and not row.self_destructing:
            continue

        if row.media:
            src = buffer_storage.buffer_find(row.id, row.chat_id)
            if not src:
                fresh = await _refetch_message(client, row.chat_id, row.id)
                if fresh and getattr(fresh, "media", None):
                    src = await buffer_storage.buffer_save(fresh)
            if not src:
                continue

            caption = row.msg_text or ""
            if deleted_storage:
                enc_path = await deleted_storage.deleted_put_from_buffer(src)
                if not enc_path:
                    continue
                with deleted_storage.deleted_open_for_upload(enc_path) as f:
                    tmp_path = getattr(f, "name", src)
                    await _send_deleted_file(client, settings.log_chat_id, tmp_path, caption, row.chat_id)
            else:
                os.makedirs(settings.media_deleted_dir, exist_ok=True)
                dst = os.path.join(settings.media_deleted_dir, os.path.basename(src))
                with open(src, "rb") as in_f, open(dst, "wb") as out_f:
                    out_f.write(in_f.read())
                await _send_deleted_file(client, settings.log_chat_id, dst, caption, row.chat_id)
        elif row.msg_text:
            await _safe_send(client, settings.log_chat_id, f"**Deleted message:**\n{row.msg_text}")
