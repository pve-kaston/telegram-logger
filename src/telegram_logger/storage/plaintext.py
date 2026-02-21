import os
from datetime import datetime, timedelta, timezone
from typing import Optional


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


class PlaintextBufferStorage:
    def __init__(self, client, media_dir: str, max_buffer_size: int):
        self.client = client
        self.media_dir = media_dir
        self.max_buffer_size = max_buffer_size

    def buffer_find(self, msg_id: int, chat_id: int) -> Optional[str]:
        return find_by_prefix(self.media_dir, msg_id, chat_id)

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
        path = os.path.join(self.media_dir, f"{canonical_prefix(message.id, chat_id)}file.bin")
        if self.buffer_find(message.id, chat_id):
            return None

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
            except Exception:
                continue
