import base64
import os
import tempfile
from contextlib import contextmanager
from typing import BinaryIO, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class EncryptedDeletedStorage:
    def __init__(self, deleted_dir: str, key_b64: str):
        self.deleted_dir = deleted_dir
        self.key = base64.b64decode(key_b64)
        if len(self.key) != 32:
            raise ValueError("DELETED_MEDIA_KEY_B64 must decode to 32 bytes (AES-256-GCM)")
        self.aes = AESGCM(self.key)

    def buffer_find(self, msg_id: int, chat_id: int) -> Optional[str]:
        return None

    async def buffer_save(self, message) -> Optional[str]:
        return None

    async def purge_buffer_ttl(self, now):
        return None

    async def deleted_put_from_buffer(self, src_path: str) -> Optional[str]:
        os.makedirs(self.deleted_dir, exist_ok=True)
        base = os.path.basename(src_path)
        enc_path = os.path.join(self.deleted_dir, base + ".enc")

        if os.path.exists(enc_path):
            return enc_path

        with open(src_path, "rb") as f:
            data = f.read()

        nonce = os.urandom(12)
        ct = self.aes.encrypt(nonce, data, None)

        with open(enc_path, "wb") as f:
            f.write(nonce + ct)

        return enc_path

    @contextmanager
    def deleted_open_for_upload(self, enc_path: str) -> BinaryIO:
        with open(enc_path, "rb") as f:
            blob = f.read()
        nonce, ct = blob[:12], blob[12:]
        data = self.aes.decrypt(nonce, ct, None)

        tmp = tempfile.NamedTemporaryFile("wb", delete=True)
        tmp.write(data)
        tmp.flush()
        tmp.seek(0)
        try:
            yield tmp
        finally:
            tmp.close()
