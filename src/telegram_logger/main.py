import asyncio
import logging
import re
import tempfile
from pathlib import Path
from datetime import datetime, timezone

from telethon import TelegramClient, events
from telethon.errors import ChatForwardsRestrictedError

from telegram_logger.database import MessageRepository
from telegram_logger.handlers.edited_deleted import edited_deleted_handler
from telegram_logger.handlers.new_message import new_message_handler
from telegram_logger.health.beats import beat_housekeeping
from telegram_logger.health.healthcheck import setup_healthcheck
from telegram_logger.settings import settings
from telegram_logger.storage.encrypted_deleted import EncryptedDeletedStorage
from telegram_logger.storage.plaintext import PlaintextBufferStorage


client: TelegramClient


def utcnow():
    return datetime.now(timezone.utc)


async def save_restricted_msg(link: str, client: TelegramClient):
    chat_id = None
    msg_id = None
    if link.startswith("tg://"):
        parts = [int(v) for v in re.findall(r"\d+", link)]
        if len(parts) == 2:
            chat_id, msg_id = parts
    else:
        m = re.search(r"t\.me/c/(\d+)/(\d+)", link)
        if m:
            chat_id = int(f"-100{m.group(1)}")
            msg_id = int(m.group(2))
        else:
            parts = link.rstrip("/").split("/")
            if len(parts) >= 2:
                msg_id = int(parts[-1])
                chat_id = int(parts[-2]) if parts[-2].isdigit() else parts[-2]

    if chat_id is None or msg_id is None:
        logging.warning("Could not parse restricted link: %s", link)
        return

    try:
        msg = await client.get_messages(chat_id, ids=msg_id)
    except ValueError as exc:
        logging.warning("Could not resolve entity for restricted link %s: %s", link, exc)
        return
    except Exception:
        logging.exception("Failed to fetch restricted link %s", link)
        return

    if not msg:
        return
    if msg.media:
        try:
            await client.send_file("me", msg.media, caption=msg.text or "")
        except ChatForwardsRestrictedError:
            # Protected chats disallow forwarding media by reference.
            # Re-upload after downloading the payload.
            suffix = Path(getattr(getattr(msg, "file", None), "name", "") or "").suffix or ".bin"
            with tempfile.NamedTemporaryFile("wb", suffix=suffix, delete=True) as tmp:
                await client.download_media(msg.media, file=tmp.name)
                await client.send_file("me", tmp.name, caption=msg.text or "")    
    elif msg.text:
        await client.send_message("me", msg.text)


async def housekeeping_loop(db: MessageRepository, buffer_storage: PlaintextBufferStorage, ttl_hours: int):
    while True:
        beat_housekeeping()
        now = utcnow()
        try:
            await db.delete_expired_messages(now)
        except Exception:
            logging.exception("delete_expired_messages failed")
        try:
            await buffer_storage.purge_buffer_ttl(now, ttl_hours=ttl_hours)
        except Exception:
            logging.exception("purge_buffer_ttl failed")
        await asyncio.sleep(300)


async def run(client: TelegramClient):
    logging.basicConfig(level="INFO" if settings.debug_mode else "WARNING")
    setup_healthcheck()

    me = await client.get_me()
    my_id = me.id

    db = MessageRepository(settings.build_sqlite_url())
    await db.init()

    buffer_storage = PlaintextBufferStorage(
        client=client,
        media_dir=settings.media_dir,
        max_buffer_size=settings.max_buffer_file_size,
    )

    deleted_storage = None
    if settings.encrypt_deleted_media and settings.deleted_media_key_b64.get_secret_value():
        deleted_storage = EncryptedDeletedStorage(
            deleted_dir=settings.media_deleted_dir,
            key_b64=settings.deleted_media_key_b64.get_secret_value(),
        )

    client.add_event_handler(
        lambda e: new_message_handler(
            e,
            client,
            db,
            buffer_storage,
            settings,
            my_id,
            lambda link: save_restricted_msg(link, client),
        ),
        events.NewMessage(incoming=True, outgoing=settings.listen_outgoing_messages),
    )
    client.add_event_handler(
        lambda e: new_message_handler(
            e,
            client,
            db,
            buffer_storage,
            settings,
            my_id,
            lambda link: save_restricted_msg(link, client),
        ),
        events.MessageEdited(),
    )

    client.add_event_handler(
        lambda e: edited_deleted_handler(e, client, db, buffer_storage, deleted_storage, settings, my_id),
        events.MessageEdited(),
    )
    client.add_event_handler(
        lambda e: edited_deleted_handler(e, client, db, buffer_storage, deleted_storage, settings, my_id),
        events.MessageDeleted(),
    )
    client.add_event_handler(
        lambda e: edited_deleted_handler(e, client, db, buffer_storage, deleted_storage, settings, my_id)
    )

    await housekeeping_loop(db, buffer_storage, settings.media_buffer_ttl_hours)
