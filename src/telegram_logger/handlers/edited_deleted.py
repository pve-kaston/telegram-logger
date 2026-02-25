from __future__ import annotations

import logging
import os
import re
from contextlib import suppress

from telethon import events
from telethon.errors import FileMigrateError, FileReferenceExpiredError
from telethon.hints import Entity
from telethon.tl import types

logger = logging.getLogger(__name__)


def _escape_md_label(text: str) -> str:
    value = (text or "").strip()
    for ch in ("\\", "[", "]", "(", ")", "_", "*", "`"):
        value = value.replace(ch, f"\\{ch}")
    return value


def _remove_file_quietly(path: str) -> None:
    if path:
        with suppress(FileNotFoundError):
            os.remove(path)


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
            or "_".join(
                filter(
                    None,
                    [
                        getattr(entity, "first_name", None),
                        getattr(entity, "last_name", None),
                    ],
                )
            )
            or str(chat_id)
        )
    except Exception:
        chat_name = str(chat_id)

    base_name = os.path.basename(fallback_name)
    parts = base_name.split("_", 2)
    if len(parts) >= 3 and parts[0].lstrip("-").isdigit() and parts[1].isdigit():
        base_name = parts[2]
    return f"{_safe_name(chat_name)}_{_safe_name(base_name)}"


async def _create_mention(
    client, entity_id: int, chat_msg_id: int | None = None
) -> str:
    msg_id = 1 if chat_msg_id is None else chat_msg_id
    if entity_id == 0:
        return "Unknown"

    try:
        entity: Entity = await client.get_entity(entity_id)

        if isinstance(entity, (types.Channel, types.Chat)):
            title = (getattr(entity, "title", None) or f"Chat {entity_id}").strip()
            title = _escape_md_label(title)

            username = (getattr(entity, "username", None) or "").strip()
            if username:
                return f"[{title}](https://t.me/{username})"

            chat_id = str(entity_id).replace("-100", "")
            return f"[{title}](https://t.me/c/{chat_id}/{msg_id})"

        first = (getattr(entity, "first_name", None) or "").strip()
        last = (getattr(entity, "last_name", None) or "").strip()
        username = (getattr(entity, "username", None) or "").strip()

        if first:
            full_name = f"{first} {last}".strip()
            full_name = _escape_md_label(full_name)
            return f"[{full_name}](tg://user?id={entity.id})"

        if username:
            uname = _escape_md_label(username)
            return f"[@{uname}](https://t.me/{username})"

        ent_id = getattr(entity, "id", entity_id)
        if isinstance(ent_id, int) and ent_id > 0:
            return f"[User {ent_id}](tg://user?id={ent_id})"
        return str(ent_id)

    except Exception:
        if isinstance(entity_id, int) and entity_id > 0:
            return f"[User {entity_id}](tg://user?id={entity_id})"
        return str(entity_id)


async def _safe_send(client, chat_id: int, text: str, limit: int = 4096):
    if not text:
        return
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    await client.send_message(chat_id, text, parse_mode="md", link_preview=False)


async def _refetch_message(
    client, chat_id: int, msg_id: int, listen_outgoing_messages: bool
):
    if not listen_outgoing_messages:
        return None

    try:
        return await client.get_messages(chat_id, ids=msg_id)
    except (FileReferenceExpiredError, FileMigrateError):
        return await client.get_messages(chat_id, ids=msg_id)


async def _send_deleted_file(
    client,
    log_chat_id: int,
    file_path: str,
    caption: str,
    chat_id: int,
    display_name: str | None = None,
):
    name_for_caption = display_name or os.path.basename(file_path)
    filename = await _friendly_filename(client, chat_id, name_for_caption)
    await client.send_file(
        log_chat_id,
        file_path,
        caption=caption,
        parse_mode="md",
        attributes=[types.DocumentAttributeFilename(file_name=filename)],
        force_document=True,
        link_preview=False,
    )


async def edited_deleted_handler(
    event, client, db, buffer_storage, deleted_storage, settings, my_id
):
    if isinstance(event, events.MessageEdited.Event):
        if not settings.save_edited_messages:
            logger.debug("Edited message processing disabled")
            return
        ids = [event.message.id]
        rows = await db.get_messages_by_event(event.chat_id, ids)
        for row in rows:
            if row.media:
                continue
            old_text = (row.msg_text or "").strip()
            new_text = (event.message.text or "").strip()
            if old_text != new_text:
                mention_sender = await _create_mention(client, row.from_id)
                mention_chat = await _create_mention(client, row.chat_id, row.id)
                await _safe_send(
                    client,
                    settings.log_chat_id,
                    f"**✏ Edited text message from:** {mention_sender}\n"
                    f"in {mention_chat}\n"
                    f"**Before:**\n```{old_text}```\n"
                    f"**After:**\n```{new_text}```",
                )
        return

    if not isinstance(
        event, (events.MessageDeleted.Event, types.UpdateReadMessagesContents)
    ):
        return

    if (
        isinstance(event, types.UpdateReadMessagesContents)
        and not settings.process_self_destruct_media
    ):
        logger.info(
            "Skipping TTL/self-destruct event processing because PROCESS_SELF_DESTRUCT_MEDIA is disabled"
        )
        return

    if (
        isinstance(event, types.UpdateReadMessagesContents)
        and not settings.process_self_destruct_media
    ):
        return

    ids = _ids_from_event(event, settings.max_deleted_messages_per_event)
    logger.debug(
        "Processing deletion-related event=%s ids_count=%s",
        type(event).__name__,
        len(ids),
    )
    rows = await db.get_messages_by_event(getattr(event, "chat_id", None), ids)

    for row in rows:
        if row.from_id in settings.ignored_ids or row.chat_id in settings.ignored_ids:
            logger.debug(
                "Skipping row id=%s chat_id=%s due to ignored_ids", row.id, row.chat_id
            )
            continue

        if (
            isinstance(event, types.UpdateReadMessagesContents)
            and not row.self_destructing
        ):
            logger.debug("Skipping non-self-destruct row id=%s for TTL event", row.id)
            continue

        mention_sender = await _create_mention(client, row.from_id)
        mention_chat = await _create_mention(client, row.chat_id, row.id)

        if row.media:
            src = buffer_storage.buffer_find(row.id, row.chat_id)
            if not src:
                fresh = await _refetch_message(
                    client,
                    row.chat_id,
                    row.id,
                    settings.listen_outgoing_messages,
                )
                if fresh and getattr(fresh, "media", None):
                    src = await buffer_storage.buffer_save(fresh)
            if not src:
                logger.info(
                    "Media for deleted message id=%s chat_id=%s not found in buffer",
                    row.id,
                    row.chat_id,
                )
                continue

            header = f"**Deleted message from:** {mention_sender}\nin {mention_chat}\n"
            body = (row.msg_text or "").strip()
            caption = header + (f"**Message:**\n{body}" if body else "")

            if deleted_storage:
                enc_path = await deleted_storage.deleted_put_from_buffer(src)
                if not enc_path:
                    logger.error(
                        "Failed to encrypt deleted media id=%s chat_id=%s",
                        row.id,
                        row.chat_id,
                    )
                    continue

                try:
                    with deleted_storage.deleted_open_for_upload(enc_path) as f:
                        tmp_path = getattr(f, "name", src)
                        await _send_deleted_file(
                            client,
                            settings.log_chat_id,
                            tmp_path,
                            caption,
                            row.chat_id,
                            display_name=os.path.basename(src),
                        )
                except Exception as e:
                    logger.exception(
                        "Failed to upload encrypted deleted media id=%s chat_id=%s enc_path=%s: %s",
                        row.id,
                        row.chat_id,
                        enc_path,
                        e,
                    )
                    continue
                else:
                    _remove_file_quietly(src)
            else:
                try:
                    await _send_deleted_file(
                        client,
                        settings.log_chat_id,
                        src,
                        caption,
                        row.chat_id,
                    )
                except Exception as e:
                    logger.exception(
                        "Failed to upload deleted media id=%s chat_id=%s path=%s: %s",
                        row.id,
                        row.chat_id,
                        src,
                        e,
                    )
                    continue
                else:
                    _remove_file_quietly(src)
            logger.info(
                "Processed deleted media message id=%s chat_id=%s", row.id, row.chat_id
            )
        elif row.msg_text:
            await _safe_send(
                client,
                settings.log_chat_id,
                f"**Deleted message from:** {mention_sender}\n"
                f"in {mention_chat}\n"
                f"**Message:**\n{row.msg_text}",
            )
            logger.info(
                "Processed deleted text message id=%s chat_id=%s", row.id, row.chat_id
            )
