import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from telethon.tl import types

logger = logging.getLogger(__name__)

def canonical_prefix(msg_id: int, chat_id: int) -> str:
    return f"{chat_id}_{msg_id}_"


def find_by_prefix(base_dir: str, msg_id: int, chat_id: int) -> Optional[str]:
    prefixes = (canonical_prefix(msg_id, chat_id), f"{msg_id}_")
    try:
        for name in os.listdir(base_dir):
            path = os.path.join(base_dir, name)
            if os.path.isfile(path) and any(name.startswith(p) for p in prefixes):
                return path
    except FileNotFoundError:
        return None
    return None


def _safe_name(name: str) -> str:
    safe = re.sub(r"[^\w\-. ()\[\]{}@,+=]", "_", name or "")
    return safe or "file.bin"


def _guess_filename_from_media(media) -> str:
    if not media:
        return "file.bin"
    if isinstance(media, (types.MessageMediaPhoto, types.Photo)):
        return "photo.jpg"
    if isinstance(media, (types.MessageMediaContact, types.Contact)):
        return "contact.vcf"

    doc = media if isinstance(media, types.Document) else getattr(media, "document", None)
    if isinstance(doc, types.Document):
        for attr in getattr(doc, "attributes", []):
            if isinstance(attr, types.DocumentAttributeFilename) and getattr(
                attr, "file_name", None
            ):
                return _safe_name(attr.file_name)
            if isinstance(attr, types.DocumentAttributeVideo) and getattr(
                attr, "round_message", False
            ):
                return "video_note.mp4"

        mime = getattr(doc, "mime_type", None)
        if mime and "/" in mime:
            return f"file.{mime.split('/')[-1]}"

    return "file.bin"


class PlaintextBufferStorage:
    def __init__(self, client, media_dir: str, max_buffer_size: int):
        self.client = client
        self.media_dir = media_dir
        self.max_buffer_size = max_buffer_size

    def buffer_find(self, msg_id: int, chat_id: int) -> Optional[str]:
        return find_by_prefix(self.media_dir, msg_id, chat_id)

    async def _friendly_name(self, chat_id: int, base_file_name: str) -> str:
        try:
            entity = await self.client.get_entity(chat_id)
            chat_name = (
                getattr(entity, "username", None)
                or getattr(entity, "title", None)
                or "_".join(
                    filter(
                        None,
                        [getattr(entity, "first_name", None), getattr(entity, "last_name", None)],
                    )
                )
                or str(chat_id)
            )
        except Exception:
            chat_name = str(chat_id)
        return f"{_safe_name(chat_name)}_{_safe_name(base_file_name)}"

    async def buffer_save(self, message) -> Optional[str]:
        media = message.media or getattr(message, "video_note", None)
        if not media:
            return None

        os.makedirs(self.media_dir, exist_ok=True)
        try:
            size = getattr(getattr(message, "file", None), "size", None)
        except Exception:
            size = None
        if size is not None and size > self.max_buffer_size:
            return None

        chat_id = message.chat_id or 0
        if self.buffer_find(message.id, chat_id):
            return None

        original_name = _guess_filename_from_media(media)
        human_name = await self._friendly_name(chat_id, original_name)
        path = os.path.join(self.media_dir, f"{canonical_prefix(message.id, chat_id)}{human_name}")

        await self.client.download_media(media, path)
        return path

    async def purge_buffer_ttl(self, now: datetime, ttl_hours: int = 6) -> None:
        ttl = timedelta(hours=ttl_hours)
        if not os.path.isdir(self.media_dir):
            return
        for name in os.listdir(self.media_dir):
            path = os.path.join(self.media_dir, name)
            if not os.path.isfile(path):
                continue
            try:
                mtime = os.path.getmtime(path)
                if datetime.fromtimestamp(mtime, tz=timezone.utc) < (now - ttl):
                    os.remove(path)
            except Exception as e:
                logger.warning("Failed to purge file %s: %s", path, e)
                continue
