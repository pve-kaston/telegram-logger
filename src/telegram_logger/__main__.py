import asyncio
import logging
import os
import pickle
import re
import sys
import shutil
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Union

from telethon import TelegramClient, events
from telethon.events import MessageDeleted, MessageEdited, NewMessage
from telethon.hints import Entity
from telethon.tl.custom import Message
from telethon.tl.functions import messages as msg_funcs
from telethon.tl import types

from telegram_logger.database import DbMessage, register_models
from telegram_logger.database.methods import (
    delete_expired_messages_from_db,
    get_message_ids_by_event,
    message_exists,
    save_message,
)
from telegram_logger.settings import settings
from telegram_logger.tg_types import ChatType

# ====== Конфиг (можно переопределить в settings.py) ======
MEDIA_DIR = getattr(settings, "media_dir", "media")
MEDIA_DELETED_DIR = getattr(settings, "media_deleted_dir", "media_deleted")
MEDIA_BUFFER_TTL_HOURS = int(getattr(settings, "media_buffer_ttl_hours", 24))
MAX_BUFFER_FILE_SIZE = int(getattr(settings, "max_buffer_file_size", 200 * 1024 * 1024))

client: TelegramClient
MY_ID: int


async def get_chat_type(event: NewMessage.Event) -> ChatType:
    if event.is_group:
        return ChatType.GROUP
    if event.is_channel:
        return ChatType.CHANNEL
    if event.is_private:
        return ChatType.BOT if (await event.get_sender()).bot else ChatType.USER
    return ChatType.UNKNOWN


def get_file_name(media) -> str:
    """
    Имя файла для медиа (Document/Photo/Contact). Если не удаётся — по MIME или 'file.bin'.
    """
    if not media:
        return "file.bin"

    if isinstance(media, (types.MessageMediaPhoto, types.Photo)):
        return "photo.jpg"

    if isinstance(media, (types.MessageMediaContact, types.Contact)):
        return "contact.vcf"

    # Достаём Document независимо от контейнера
    doc = None
    if isinstance(media, types.MessageMediaDocument):
        doc = media.document
    elif isinstance(media, types.Document):
        doc = media
    else:
        doc = getattr(media, "document", None)

    if isinstance(doc, types.Document):
        # 1) Имя из атрибутов
        try:
            for attr in getattr(doc, "attributes", []) or []:
                if isinstance(attr, types.DocumentAttributeFilename) and getattr(attr, "file_name", None):
                    return attr.file_name
        except Exception:
            pass

        # 2) По MIME
        mime = getattr(doc, "mime_type", None)
        if mime:
            if mime == "audio/ogg":
                return "audio.ogg"
            if mime == "video/mp4":
                return "video.mp4"
            try:
                ext = mime.split("/")[-1].strip().lower()
                if ext and all(c.isalnum() or c in {"-", "+"} for c in ext):
                    return f"file.{ext}"
            except Exception:
                pass

    return "file.bin"


def _safe_name(name: str) -> str:
    return re.sub(r"[^\w\-. ()\[\]{}@,+=]", "_", name)


def _compose_media_path(base_dir: str, msg_id: int, chat_id: int, file_name: str) -> str:
    return os.path.join(base_dir, f"{msg_id}_{chat_id}_{_safe_name(file_name)}")


async def save_media_as_file(msg: Message):
    """
    Скачиваем любое медиа в буфер MEDIA_DIR (если не слишком большое).
    """
    if not msg or not msg.media:
        return

    try:
        fsize = getattr(getattr(msg, "file", None), "size", None)
    except Exception:
        fsize = None

    if fsize is not None and fsize > MAX_BUFFER_FILE_SIZE:
        logging.warning(f"Skip buffering large file: {fsize} > {MAX_BUFFER_FILE_SIZE}")
        return

    os.makedirs(MEDIA_DIR, exist_ok=True)
    file_name = get_file_name(msg.media)
    file_path = _compose_media_path(MEDIA_DIR, msg.id, msg.chat_id, file_name)

    if os.path.exists(file_path):
        return

    try:
        await client.download_media(msg.media, file_path)
    except Exception as e:
        logging.exception(f"Failed to download media for msg {msg.id} in chat {msg.chat_id}: {e}")


@contextmanager
def retrieve_media_as_file(msg_id: int, chat_id: int, media, _noforwards_or_ttl: bool):
    """
    Даём файловый объект из буфера (MEDIA_DIR), если он там есть.
    """
    file_name = get_file_name(media)
    file_path = _compose_media_path(MEDIA_DIR, msg_id, chat_id, file_name)
    if os.path.exists(file_path):
        f = open(file_path, "rb")
        try:
            yield f, os.path.basename(file_path)
        finally:
            f.close()
    else:
        yield None, None


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
    chat_id = event.chat_id
    from_id = get_sender_id(event.message)
    msg_id = event.message.id

    # Команды из лог-чата (как в исходнике)
    if (
        chat_id == settings.log_chat_id
        and from_id == MY_ID
        and event.message.text
        and (
            re.match(r"^(https://)?t\.me/(?:c/)?\w+/\d+", event.message.text)
            or re.match(r"^tg://openmessage\?user_id=\d+&message_id=\d+", event.message.text)
        )
    ):
        msg_links = re.findall(r"(?:https://)?t\.me/(?:c/)?\w+/\d+", event.message.text)
        if not msg_links:
            msg_links = re.findall(r"tg://openmessage\?user_id=\d+&message_id=\d+", event.message.text)
        if msg_links:
            for msg_link in msg_links:
                await save_restricted_msg(msg_link)
            return

    if from_id in settings.ignored_ids or chat_id in settings.ignored_ids:
        return

    edited_at = None
    noforwards = False
    self_destructing = False

    try:
        noforwards = event.chat.noforwards is True  # type: ignore[attr-defined]
    except AttributeError:
        noforwards = getattr(event.message, "noforwards", False)

    try:
        if event.message.media and getattr(event.message.media, "ttl_seconds", None):
            self_destructing = True
    except AttributeError:
        pass

    # Буферим ЛЮБОЕ медиа
    if event.message.media:
        await save_media_as_file(event.message)

    if isinstance(event, MessageEdited.Event):
        edited_at = datetime.now(timezone.utc)

    # Сохраняем запись в БД, если её ещё нет
    if not await message_exists(msg_id):
        media_blob = pickle.dumps(event.message.media) if event.message.media else None
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


async def load_messages_from_event(event) -> List[DbMessage]:
    ids: List[int] = []
    if isinstance(event, MessageDeleted.Event):
        ids = event.deleted_ids[: settings.rate_limit_num_messages]
    elif isinstance(event, types.UpdateReadMessagesContents):
        ids = event.messages[: settings.rate_limit_num_messages]
    elif isinstance(event, MessageEdited.Event):
        ids = [event.message.id]

    db_results: List[DbMessage] = await get_message_ids_by_event(event, ids)
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
                is_pm = chat_msg_id is not None
                name = (entity.first_name + " " if entity.first_name else "") + (
                    entity.last_name if entity.last_name else ""
                )
                mention = f"[{name}](tg://user?id={entity.id})" + (" #pm" if is_pm else "")
            elif getattr(entity, "username", None):
                mention = f"[@{entity.username}](t.me/{entity.username})"
            elif getattr(entity, "phone", None):
                mention = entity.phone
            else:
                mention = str(entity.id)
    except Exception as e:
        logging.warning(e)
        mention = str(entity_id)
    return mention


async def _copy_to_deleted_dir(msg_id: int, chat_id: int, media) -> Optional[str]:
    """
    Копирует файл из MEDIA_DIR в MEDIA_DELETED_DIR. Возвращает путь к копии или None.
    """
    os.makedirs(MEDIA_DELETED_DIR, exist_ok=True)
    fname = get_file_name(media)
    src = _compose_media_path(MEDIA_DIR, msg_id, chat_id, fname)
    dst = _compose_media_path(MEDIA_DELETED_DIR, msg_id, chat_id, fname)
    if not os.path.exists(src):
        return None
    try:
        shutil.copy2(src, dst)
        return dst
    except Exception as e:
        logging.warning(f"Failed to copy deleted media to '{dst}': {e}")
        return None


async def edited_deleted_handler(event):
    """
    Логика:
    - MessageEdited: логируем ТОЛЬКО текстовые сообщения (без медиа): показываем "Before" и "After".
    - MessageDeleted / UpdateReadMessagesContents:
        * если у сообщения было медиа — копируем файл в MEDIA_DELETED и отправляем ТОЛЬКО файл (без текста);
        * если медиа не было, но был текст — отправляем текст в лог-чат.
    """
    # ====== Обработка РЕДАКТИРОВАНИЯ текста ======
    if isinstance(event, MessageEdited.Event):
        messages: List[DbMessage] = await load_messages_from_event(event)
        if not messages:
            return

        for message in messages:
            if message.from_id in settings.ignored_ids or message.chat_id in settings.ignored_ids:
                continue

            # только текстовые: если было медиа — пропускаем (не логируем капшены)
            has_media = bool(message.media)
            if has_media:
                continue

            old_text = (message.msg_text or "").strip()
            new_text = (getattr(event.message, "text", None) or "").strip()

            # если текста нет или не изменился — пропускаем
            if old_text == new_text or (not old_text and not new_text):
                continue

            try:
                mention_sender = await create_mention(message.from_id)
                mention_chat = await create_mention(message.chat_id, message.id)
                log_text = (
                    f"**✏ Edited text message from:** {mention_sender}\n"
                    f"in {mention_chat}\n"
                    f"**Before:**\n```{old_text}```\n"
                    f"**After:**\n```{new_text}```"
                )
                await client.send_message(settings.log_chat_id, log_text)
            except Exception as e:
                logging.exception(f"Failed to send edited text to log chat: {e}")

        return  # не проваливаемся в обработку удаления

    # ====== Обработка УДАЛЕНИЯ / SELF-DESTRUCT ======
    if not isinstance(event, (MessageDeleted.Event, types.UpdateReadMessagesContents)):
        return

    messages: List[DbMessage] = await load_messages_from_event(event)
    if not messages:
        return

    for message in messages:
        if message.from_id in settings.ignored_ids or message.chat_id in settings.ignored_ids:
            continue

        media = pickle.loads(message.media) if message.media else None

        if media:
            # Удалённое медиа: копируем в MEDIA_DELETED и отправляем файл без текста
            copied_path = await _copy_to_deleted_dir(message.id, message.chat_id, media)
            if not copied_path:
                logging.info(f"Deleted media not found in buffer for msg {message.id} (chat {message.chat_id})")
                continue
            try:
                with open(copied_path, "rb") as fh:
                    uploaded = await client.upload_file(fh)
                await client(
                    msg_funcs.SendMediaRequest(
                        peer=settings.log_chat_id,
                        media=types.InputMediaUploadedDocument(
                            file=uploaded,
                            mime_type="application/octet-stream",
                            attributes=[types.DocumentAttributeFilename(file_name=os.path.basename(copied_path))],
                        ),
                        message="",  # без текста
                    )
                )
            except Exception as e:
                logging.exception(f"Failed to send deleted media {copied_path} to log chat: {e}")
        else:
            # Удалённый текст без медиа: отправляем текст
            try:
                mention_sender = await create_mention(message.from_id)
                mention_chat = await create_mention(message.chat_id, message.id)
                if isinstance(event, types.UpdateReadMessagesContents):
                    header = f"**Deleted #selfdestructing message from:** {mention_sender}\n"
                else:
                    header = f"**Deleted message from:** {mention_sender}\n"
                text = header + f"in {mention_chat}\n"
            except Exception:
                text = "**Deleted message (text)**\n"

            if message.msg_text:
                text += "**Message:**\n" + message.msg_text

            if text.strip():
                try:
                    await client.send_message(settings.log_chat_id, text)
                except Exception as e:
                    logging.exception(f"Failed to send deleted text to log chat: {e}")

    logging.info(f"Processed deletion/self-destruct event, items: {len(messages)}")


async def save_restricted_msg(link: str):
    # Заглушка под парсинг t.me-ссылок, если понадобится
    logging.info(f"save_restricted_msg called with link={link} (stub)")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _file_is_expired(path: str, ttl: timedelta) -> bool:
    try:
        mtime = os.path.getmtime(path)
        modified = datetime.fromtimestamp(mtime, tz=timezone.utc)
        return modified < (_utcnow() - ttl)
    except Exception:
        return True


async def purge_expired_media_files():
    """
    Удаляем файлы из MEDIA_DIR старше TTL. MEDIA_DELETED_DIR не трогаем (архив удалённых).
    """
    ttl = timedelta(hours=MEDIA_BUFFER_TTL_HOURS)
    if not os.path.isdir(MEDIA_DIR):
        return
    removed = 0
    for name in os.listdir(MEDIA_DIR):
        path = os.path.join(MEDIA_DIR, name)
        if not os.path.isfile(path):
            continue
        if _file_is_expired(path, ttl):
            try:
                os.remove(path)
                removed += 1
            except Exception as e:
                logging.warning(f"Failed to remove expired media file {path}: {e}")
    if removed:
        logging.info(f"Purged {removed} expired media file(s) from '{MEDIA_DIR}'")


async def housekeeping_loop() -> None:
    """
    Каждые 5 минут:
    - чистим просроченные записи в БД,
    - удаляем протухший буфер из MEDIA_DIR.
    """
    while True:
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


async def init() -> None:
    global MY_ID
    os.makedirs("db", exist_ok=True)
    os.makedirs(MEDIA_DIR, exist_ok=True)
    os.makedirs(MEDIA_DELETED_DIR, exist_ok=True)
    await register_models()
    logging.basicConfig(level="INFO" if settings.debug_mode else "WARNING")
    settings.ignored_ids.add(settings.log_chat_id)
    MY_ID = (await client.get_me()).id

    # New/Edited для первичного сохранения и буферизации
    client.add_event_handler(
        new_message_handler, events.NewMessage(incoming=True, outgoing=settings.listen_outgoing_messages)
    )
    client.add_event_handler(new_message_handler, events.MessageEdited())

    # Удаления/редактирование (редакт. логируем только текст)
    client.add_event_handler(edited_deleted_handler, events.MessageEdited())
    client.add_event_handler(edited_deleted_handler, events.MessageDeleted())
    client.add_event_handler(edited_deleted_handler)  # для raw UpdateReadMessagesContents

    # Фоновый хаускипинг (вечная петля)
    await housekeeping_loop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    # ВАЖНО: создать client в глобальной области, чтобы его видели хендлеры
    client = TelegramClient(
        settings.session_name,
        settings.api_id,
        settings.api_hash.get_secret_value(),
    )
    with client:
        client.loop.run_until_complete(init())
