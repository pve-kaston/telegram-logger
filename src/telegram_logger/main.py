import asyncio
import logging
from datetime import datetime, timezone

from telethon import TelegramClient, events

from telegram_logger.database import MessageRepository
from telegram_logger.handlers.edited_deleted import edited_deleted_handler
from telegram_logger.handlers.new_message import new_message_handler
from telegram_logger.handlers.restricted_saver import save_restricted_msg
from telegram_logger.health.beats import beat_housekeeping
from telegram_logger.health.healthcheck import setup_healthcheck
from telegram_logger.settings import settings
from telegram_logger.storage.encrypted_deleted import EncryptedDeletedStorage
from telegram_logger.storage.plaintext import PlaintextBufferStorage


client: TelegramClient


def utcnow():
    return datetime.now(timezone.utc)


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
            lambda link: save_restricted_msg(link, client, buffer_storage, settings.log_chat_id),
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
            lambda link: save_restricted_msg(link, client, buffer_storage, settings.log_chat_id),
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
