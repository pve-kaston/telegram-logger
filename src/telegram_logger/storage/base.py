from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import BinaryIO, Optional, Protocol


class MediaStorage(Protocol):
    async def buffer_save(self, message) -> Optional[str]: ...

    def buffer_find(self, msg_id: int, chat_id: int) -> Optional[str]: ...

    async def deleted_put_from_buffer(self, src_path: str) -> Optional[str]: ...

    def deleted_open_for_upload(self, enc_path: str) -> BinaryIO: ...

    async def purge_buffer_ttl(self, now: datetime) -> None: ...


@dataclass(frozen=True)
class StoredDeletedMedia:
    enc_path: str
    sha256_hex: str
