import asyncio
import logging
import os
import pickle
import re
import sys
import shutil
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Union

from telethon import TelegramClient, events
from telethon.events import MessageDeleted, MessageEdited, NewMessage
from telethon.hints import Entity
from telethon.tl.custom import Message
from telethon.tl.functions import messages as msg_funcs
from telethon.tl import types
from telethon.errors import FileReferenceExpiredError, FileMigrateError

from telegram_logger.database import DbMessage, register_models
from telegram_logger.database.methods import (
    delete_expired_messages_from_db,
    get_message_ids_by_event,
    message_exists,
    save_message,
)
from telegram_logger.settings import settings
from telegram_logger.tg_types import ChatType
from telegram_logger.health import setup_healthcheck, beat_housekeeping


MEDIA_DIR = getattr(settings, "media_dir", "media")
MEDIA_DELETED_DIR = getattr(settings, "media_deleted_dir", "media_deleted")
MAX_LEN = 4096

client: TelegramClient
MY_ID: int


# =======================================================
# =============== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ===============

async def get_chat_type(event: NewMessage.Event) -> ChatType:
    if event.is_group:
        return ChatType.GROUP
    if event.is_channel:
        return ChatType.CHANNEL
    if event.is_private:
        return ChatType.BOT if (await event.get_sender()).bot else ChatType.USER
    return ChatType.UNKNOWN


def get_file_name(media) -> str:
    if not media:
        return "file.bin"
    if isinstance(media, (types.MessageMediaPhoto, types.Photo)):
        return "photo.jpg"
    if isinstance(media, (types.MessageMediaContact, types.Contact)):
        return "contact.vcf"
    doc = media if isinstance(media, types.Document) else getattr(media, "document", None)
    if isinstance(doc, types.Document):
        for attr in getattr(doc, "attributes", []):
            if isinstance(attr, types.DocumentAttributeFilename):
                return attr.file_name
            if isinstance(attr, types.DocumentAttributeVideo) and getattr(attr, "round_message", False):
                return "video_note.mp4"
        mime = getattr(doc, "mime_type", None)
        if mime:
            ext = mime.split("/")[-1]
            return f"file.{ext}"
    return "file.bin"


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w\-. ()\[\]{}@,+=]", "_", name)


async def _get_entity_name(entity_id: int) -> str:
    """
    Возвращает @username, title канала/чата или имя пользователя; безопасно для файловой системы.
    """
    try:
        entity = await client.get_entity(entity_id)
        if getattr(entity, "username", None):
            return entity.username
        if getattr(entity, "title", None):
            return _safe_name(entity.title)
        if getattr(entity, "first_name", None):
            name = entity.first_name
            if getattr(entity, "last_name", None):
                name += "_" + entity.last_name
            return _safe_name(name)
    except Exception as e:
        logging.warning(f"Failed to get entity name for {entity_id}: {e}")
    return str(entity_id)


def _canonical_prefix(msg_id: int, chat_id: int) -> str:
    return f"{chat_id}_{msg_id}_"

async def _friendly_filename(chat_id: int, fallback_name: str) -> str:
    chat_name = await _get_entity_name(chat_id)
    base_name = fallback_name
    parts = fallback_name.split("_", 2)
    if len(parts) >= 3 and parts[0].lstrip("-").isdigit() and parts[1].isdigit():
        base_name = parts[2]
    elif len(parts) >= 2 and parts[0].isdigit():
        base_name = parts[1]
    safe_name = _safe_name(base_name)
    if not safe_name:
        safe_name = "file.bin"
    return f"{chat_name}_{safe_name}"


def _find_media_file(base_dir: str, msg_id: int, chat_id: int) -> Optional[str]:
    """
    Ищет сохранённый файл по каноническому префиксу 'chatid_msgid_'.
    Для совместимости также ищет по старому префиксу 'msgid_'.
    Возвращает полный путь или None.
    """
    prefixes = (
        _canonical_prefix(msg_id, chat_id),
        f"{msg_id}_",
    )
    try:
        for name in os.listdir(base_dir):
            if not os.path.isfile(os.path.join(base_dir, name)):
                continue
            if any(name.startswith(prefix) for prefix in prefixes):
                return os.path.join(base_dir, name)
    except FileNotFoundError:
        return None
    return None


# =======================================================
# ============= УЛУЧШЕННАЯ ФУНКЦИЯ СКАЧИВАНИЯ ===========
# =======================================================
def _extract_media(message: Message):
    if not message:
        return None
    return message.media or getattr(message, "video_note", None)


def _is_gif(message: Message) -> bool:
    media = _extract_media(message)
    doc = media if isinstance(media, types.Document) else getattr(media, "document", None)
    if not isinstance(doc, types.Document):
        return False
    mime = getattr(doc, "mime_type", "") or ""
    if mime.lower() == "image/gif":
        return True
    for attr in getattr(doc, "attributes", []):
        if isinstance(attr, types.DocumentAttributeAnimated):
            return True
    return False

async def save_media_as_file(msg: Message, retries: int = 3):
    """
    Скачиавет медиа и сохраняет файл как:
    {msg.chat_id}_{msg.id}_{chat_name}_{original_name}
    """
    media = _extract_media(msg)
    if not msg or not media:
        return

    try:
        fsize = getattr(getattr(msg, "file", None), "size", None)
    except Exception:
        fsize = None

    if fsize is not None and fsize > settings.max_buffer_file_size:
        logging.warning(f"Skip buffering large file: {fsize} > {settings.max_buffer_file_size}")
        return

    os.makedirs(MEDIA_DIR, exist_ok=True)
    file_name = get_file_name(media)

    # Канонический префикс нужен для поиска, остальная часть — человекочитаемая
    combined_name = f"{_canonical_prefix(msg.id, msg.chat_id or 0)}{await _friendly_filename(msg.chat_id or 0, file_name)}"
    file_path = os.path.join(MEDIA_DIR, combined_name)

    # Если уже есть файл с таким префиксом — не дублируем
    existing = _find_media_file(MEDIA_DIR, msg.id, msg.chat_id or 0)
    if existing:
        return

    for attempt in range(1, retries + 1):
        try:
            await client.download_media(media, file_path)
            logging.info(f"Downloaded media (attempt {attempt}) for msg {msg.id}")
            return
        except (FileReferenceExpiredError, FileMigrateError):
            logging.warning(f"Media fetch attempt {attempt} failed (expired or migrated) for msg {msg.id}")
            try:
                msg = await client.get_messages(msg.chat_id, ids=msg.id)
            except Exception as e:
                logging.warning(f"Failed to refresh message {msg.id}: {e}")
        except Exception as e:
            logging.warning(f"Download attempt {attempt} failed for msg {msg.id}: {e}")
        await asyncio.sleep(2)

    logging.warning(f"All {retries} download attempts failed for msg {msg.id}")


# =======================================================
# =================== ОСНОВНЫЕ ХЕНДЛЕРЫ =================
# =======================================================

def get_sender_id(message) -> int:
    from_id = 0
    if isinstance(message.peer_id, types.PeerUser):
        from_id = MY_ID if message.out else message.peer_id.user_id
    elif isinstance(message.peer_id, (types.PeerChannel, types.PeerChat)):
        if isinstance(message.from_id, types.PeerUser):
            from_id = message.from_id.user_id
        if isinstance(message.from_id, types.PeerChannel):
            from_id = message.from_id.channel_id
    return from_id


async def new_message_handler(event: Union[NewMessage.Event, MessageEdited.Event]):
    chat_id = event.chat_id or 0
    from_id = get_sender_id(event.message)
    msg_id = event.message.id

    if from_id in settings.ignored_ids or chat_id in settings.ignored_ids:
        return

    edited_at = None
    noforwards = getattr(event.chat, "noforwards", False)
    self_destructing = bool(getattr(event.message.media, "ttl_seconds", None))

    media = _extract_media(event.message)
    if media:
        await save_media_as_file(event.message)
        if (
            event.out
            and settings.delete_sent_gifs_from_saved
            and chat_id == MY_ID
            and _is_gif(event.message)
        ):
            try:
                await client.delete_messages(chat_id, [msg_id])
            except Exception as exc:
                logging.warning("Failed to delete sent GIF %s/%s: %s", chat_id, msg_id, exc)

    if isinstance(event, MessageEdited.Event):
        edited_at = datetime.now(timezone.utc)

    if not await message_exists(msg_id, chat_id):
        media_blob = pickle.dumps(media) if media else None
        try:
            await save_message(
                msg_id=msg_id,
                from_id=from_id,
                chat_id=chat_id,
                type=(await get_chat_type(event)).value,
                msg_text=event.message.text,
                media=media_blob,
                noforwards=noforwards,
                self_destructing=self_destructing,
                created_at=datetime.now(timezone.utc),
                edited_at=edited_at,
            )
        except Exception as exc:
            logging.error("Failed to persist message %s/%s: %s", chat_id, msg_id, exc)


def _limit_ids(ids: List[int], limit: int) -> List[int]:
    if limit <= 0:
        return ids
    return ids[:limit]


async def load_messages_from_event(event) -> List[DbMessage]:
    ids: List[int] = []
    if isinstance(event, MessageDeleted.Event):
        ids = _limit_ids(event.deleted_ids, settings.max_deleted_messages_per_event)
    elif isinstance(event, types.UpdateReadMessagesContents):
        ids = _limit_ids(event.messages, settings.rate_limit_num_messages)
    elif isinstance(event, MessageEdited.Event):
        ids = [event.message.id]
    db_results = await get_message_ids_by_event(event, ids)
    messages = []
    for db_result in db_results:
        if isinstance(event, types.UpdateReadMessagesContents) and not db_result.self_destructing:
            continue
        messages.append(db_result)
    return messages


async def create_mention(entity_id, chat_msg_id: Optional[int] = None) -> str:
    msg_id = 1 if chat_msg_id is None else chat_msg_id
    if entity_id == 0:
        return "Unknown"
    try:
        entity: Entity = await client.get_entity(entity_id)
        if isinstance(entity, (types.Channel, types.Chat)):
            name = entity.title
            chat_id = str(entity_id).replace("-100", "")
            mention = f"[{name}](t.me/c/{chat_id}/{msg_id})"
        else:
            if getattr(entity, "first_name", None):
                name = entity.first_name + " " + (entity.last_name or "")
                mention = f"[{name}](tg://user?id={entity.id})"
            elif getattr(entity, "username", None):
                mention = f"[@{entity.username}](t.me/{entity.username})"
            else:
                mention = str(entity.id)
    except Exception:
        mention = str(entity_id)
    return mention


async def safe_send_message(chat_id: int, text: str):
    if not text or len(text) > MAX_LEN:
        logging.warning("Skipped sending message: too long")
        return
    try:
        await client.send_message(chat_id, text)
    except Exception as e:
        logging.warning(f"Failed to send message to {chat_id}: {e}")


async def _copy_to_deleted_dir(msg_id: int, chat_id: int, media) -> Optional[str]:
    """
    Ищем источник по префиксу 'chatid_msgid_' в MEDIA_DIR,
    копируем в MEDIA_DELETED_DIR с тем же именем.
    """
    os.makedirs(MEDIA_DELETED_DIR, exist_ok=True)
    src = _find_media_file(MEDIA_DIR, msg_id, chat_id)
    if not src:
        return None
    dst = os.path.join(MEDIA_DELETED_DIR, os.path.basename(src))
    try:
        shutil.copy2(src, dst)
        return dst
    except Exception as e:
        logging.warning(f"Failed to copy deleted media to '{dst}': {e}")
        return None


# =======================================================
# ============ ОБРАБОТКА РЕДАКТИРОВАНИЙ/УДАЛЕНИЙ =========
# =======================================================

async def edited_deleted_handler(event):
    # ----- РЕДАКТИРОВАНИЕ ТЕКСТА -----
    if isinstance(event, MessageEdited.Event):
        messages = await load_messages_from_event(event)
        if not messages:
            return
        for message in messages:
            if message.from_id in settings.ignored_ids or message.chat_id in settings.ignored_ids:
                continue
            has_media = bool(message.media)
            if has_media:
                continue
            old_text = (message.msg_text or "").strip()
            new_text = (getattr(event.message, "text", None) or "").strip()
            if old_text == new_text or (not old_text and not new_text):
                continue
            mention_sender = await create_mention(message.from_id)
            mention_chat = await create_mention(message.chat_id, message.id)
            log_text = (
                f"**✏ Edited text message from:** {mention_sender}\n"
                f"in {mention_chat}\n"
                f"**Before:**\n```{old_text}```\n"
                f"**After:**\n```{new_text}```"
            )
            await safe_send_message(settings.log_chat_id, log_text)
        return

    # ----- УДАЛЕНИЕ / SELF-DESTRUCT -----
    if not isinstance(event, (MessageDeleted.Event, types.UpdateReadMessagesContents)):
        return

    messages = await load_messages_from_event(event)
    if not messages:
        return

    for message in messages:
        if message.from_id in settings.ignored_ids or message.chat_id in settings.ignored_ids:
            continue

        media = pickle.loads(message.media) if message.media else None

        if media:
            copied_path = await _copy_to_deleted_dir(message.id, message.chat_id, media)
            if not copied_path:
                logging.info(f"Deleted media not found in buffer for msg {message.id} — trying to redownload")
                try:
                    fresh_msg = await client.get_messages(message.chat_id, ids=message.id)
                    if not fresh_msg or not getattr(fresh_msg, "media", None):
                        logging.info(f"Cannot refetch deleted message {message.id} — message already gone from Telegram")
                        continue
                    await save_media_as_file(fresh_msg)
                    copied_path = await _copy_to_deleted_dir(message.id, message.chat_id, fresh_msg.media)
                except Exception as e:
                    logging.warning(f"Redownload before deletion failed for msg {message.id}: {e}")
            if not copied_path:
                continue
            try:
                caption = (message.msg_text or "").strip()
                if caption:
                    caption = f"**Deleted media caption:**\n{caption}"
                filename = await _friendly_filename(message.chat_id, os.path.basename(copied_path))
                with open(copied_path, "rb") as fh:
                    uploaded = await client.upload_file(fh)
                await client(
                    msg_funcs.SendMediaRequest(
                        peer=settings.log_chat_id,
                        media=types.InputMediaUploadedDocument(
                            file=uploaded,
                            mime_type="application/octet-stream",
                            attributes=[types.DocumentAttributeFilename(file_name=filename)],
                        ),
                        message=caption,
                    )
                )
            except Exception as e:
                logging.exception(f"Failed to send deleted media {copied_path} to log chat: {e}")
            continue

        # Текстовые удалённые
        mention_sender = await create_mention(message.from_id)
        mention_chat = await create_mention(message.chat_id, message.id)
        header = "**Deleted message from:** " + mention_sender + "\n"
        text = header + f"in {mention_chat}\n"
        if message.msg_text:
            text += "**Message:**\n" + message.msg_text
        if text.strip():
            await safe_send_message(settings.log_chat_id, text)

    logging.info(f"Processed deletion/self-destruct event, items: {len(messages)}")


# =======================================================
# ============== HOUSEKEEPING И ИНИЦИАЛИЗАЦИЯ ===========
# =======================================================

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def purge_expired_media_files():
    ttl = timedelta(hours=settings.media_buffer_ttl_hours)
    if not os.path.isdir(MEDIA_DIR):
        return
    removed = 0
    for name in os.listdir(MEDIA_DIR):
        path = os.path.join(MEDIA_DIR, name)
        if not os.path.isfile(path):
            continue
        try:
            mtime = os.path.getmtime(path)
            if datetime.fromtimestamp(mtime, tz=timezone.utc) < (_utcnow() - ttl):
                os.remove(path)
                removed += 1
        except Exception as e:
            logging.warning(f"Failed to remove expired media file {path}: {e}")
    if removed:
        logging.info(f"Purged {removed} expired media file(s)")


async def housekeeping_loop():
    while True:
        beat_housekeeping()
        now = _utcnow()
        try:
            await delete_expired_messages_from_db(current_time=now)
        except Exception as e:
            logging.warning(f"delete_expired_messages_from_db failed: {e}")
        try:
            await purge_expired_media_files()
        except Exception as e:
            logging.warning(f"purge_expired_media_files failed: {e}")
        await asyncio.sleep(300)


async def init():
    global MY_ID
    os.makedirs("db", exist_ok=True)
    os.makedirs(MEDIA_DIR, exist_ok=True)
    os.makedirs(MEDIA_DELETED_DIR, exist_ok=True)
    await register_models()
    logging.basicConfig(level="INFO" if settings.debug_mode else "WARNING")
    settings.ignored_ids.add(settings.log_chat_id)
    MY_ID = (await client.get_me()).id
    setup_healthcheck()
    settings.ignored_ids.add(settings.log_chat_id)

    client.add_event_handler(
        new_message_handler, events.NewMessage(incoming=True, outgoing=settings.listen_outgoing_messages)
    )
    client.add_event_handler(new_message_handler, events.MessageEdited())
    client.add_event_handler(edited_deleted_handler, events.MessageEdited())
    client.add_event_handler(edited_deleted_handler, events.MessageDeleted())
    client.add_event_handler(edited_deleted_handler)  # raw UpdateReadMessagesContents
    await housekeeping_loop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    client = TelegramClient(
        settings.session_name,
        settings.api_id,
        settings.api_hash.get_secret_value(),
    )
    with client:
        client.loop.run_until_complete(init())
