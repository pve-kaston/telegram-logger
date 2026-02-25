import asyncio
import logging
from datetime import datetime, timezone
from typing import Awaitable, Callable

from telethon import TelegramClient, events

from telegram_logger.database import MessageRepository
from telegram_logger.handlers.edited_deleted import edited_deleted_handler
from telegram_logger.handlers.new_message import new_message_handler
from telegram_logger.handlers.restricted_saver import (
    maybe_handle_restricted_link,
    save_restricted_msg,
)
from telegram_logger.health.beats import beat_housekeeping
from telegram_logger.health.healthcheck import setup_healthcheck
from telegram_logger.settings import get_settings
from telegram_logger.storage.encrypted_deleted import EncryptedDeletedStorage
from telegram_logger.storage.plaintext import PlaintextBufferStorage

settings = get_settings()

client: TelegramClient
logger = logging.getLogger(__name__)

logging.getLogger("aiosqlite").setLevel(logging.WARNING)
logging.getLogger("telethon").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)


def _safe_event_handler(
    name: str, handler: Callable[[object], Awaitable[None]]
) -> Callable[[object], Awaitable[None]]:
    async def _wrapped(event):
        try:
            await handler(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Unhandled exception in handler=%s event=%s",
                name,
                type(event).__name__,
            )

    return _wrapped


def utcnow():
    return datetime.now(timezone.utc)


async def housekeeping_loop(
    db: MessageRepository, buffer_storage: PlaintextBufferStorage, ttl_hours: int
):
    while True:
        beat_housekeeping()
        now = utcnow()
        logger.debug("Running housekeeping tick at %s", now.isoformat())
        try:
            await db.delete_expired_messages(now)
        except Exception:
            logger.exception("delete_expired_messages failed")
        try:
            await buffer_storage.purge_buffer_ttl(now, ttl_hours=ttl_hours)
        except Exception:
            logger.exception("purge_buffer_ttl failed")
        logger.info("Housekeeping finished")
        await asyncio.sleep(300)


async def run(client: TelegramClient):
    log_level = logging.DEBUG if settings.debug_mode else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    logger.info("Starting telegram-logger with debug_mode=%s", settings.debug_mode)
    setup_healthcheck()

    me = await client.get_me()
    logger.debug("Authenticated as user id=%s", getattr(me, "id", None))
    my_id = me.id

    db = MessageRepository(settings.build_sqlite_url())
    await db.init()
    logger.info("Database initialized at %s", settings.sqlite_db_file)

    buffer_storage = PlaintextBufferStorage(
        client=client,
        media_dir=settings.media_dir,
        max_buffer_size=settings.max_buffer_file_size,
    )

    deleted_storage = None
    if (
        settings.encrypt_deleted_media
        and settings.deleted_media_key_b64.get_secret_value()
    ):
        deleted_storage = EncryptedDeletedStorage(
            deleted_dir=settings.media_deleted_dir,
            key_b64=settings.deleted_media_key_b64.get_secret_value(),
        )
        logger.info("Encrypted deleted media storage is enabled")

    logger.info("Registering Telegram event handlers")

    async def _on_new_or_edited_message(e):
        await new_message_handler(
            e,
            client,
            db,
            buffer_storage,
            settings,
            my_id,
            lambda link: save_restricted_msg(
                link, client, buffer_storage, settings.log_chat_id
            ),
        )

    async def _on_edited_or_deleted(e):
        await edited_deleted_handler(
            e, client, db, buffer_storage, deleted_storage, settings, my_id
        )

    client.add_event_handler(
        _safe_event_handler(
            "new_message_handler:NewMessage", _on_new_or_edited_message
        ),
        events.NewMessage(incoming=True, outgoing=settings.listen_outgoing_messages),
    )
    client.add_event_handler(
        _safe_event_handler(
            "new_message_handler:MessageEdited", _on_new_or_edited_message
        ),
        events.MessageEdited(incoming=True, outgoing=settings.listen_outgoing_messages),
    )

    client.add_event_handler(
        _safe_event_handler(
            "edited_deleted_handler:MessageEdited", _on_edited_or_deleted
        ),
        events.MessageEdited(),
    )
    client.add_event_handler(
        _safe_event_handler(
            "edited_deleted_handler:MessageDeleted", _on_edited_or_deleted
        ),
        events.MessageDeleted(),
    )
    client.add_event_handler(
        _safe_event_handler("edited_deleted_handler:RawUpdate", _on_edited_or_deleted)
    )

    if not settings.listen_outgoing_messages:

        async def _on_outgoing_new_message(e):
            await maybe_handle_restricted_link(
                e,
                settings,
                my_id,
                lambda link: save_restricted_msg(
                    link, client, buffer_storage, settings.log_chat_id
                ),
            )

        client.add_event_handler(
            _safe_event_handler(
                "maybe_handle_restricted_link:OutgoingNewMessage",
                _on_outgoing_new_message,
            ),
            events.NewMessage(outgoing=True),
        )
    logger.info(
        "Housekeeping loop started with media_buffer_ttl_hours=%s",
        settings.media_buffer_ttl_hours,
    )
    await housekeeping_loop(db, buffer_storage, settings.media_buffer_ttl_hours)
