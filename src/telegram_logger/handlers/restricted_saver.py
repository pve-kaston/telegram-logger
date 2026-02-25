import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, urlparse

from telethon.errors import ChatForwardsRestrictedError

logger = logging.getLogger(__name__)

TG_RE_HTTP = re.compile(r"https?://t\.me/(?:c/\d+/\d+|[\w\d_]+/\d+)")
TG_RE_TG = re.compile(r"tg://(?:openmessage|privatepost)\?[^\s]+")


def _to_int(value: str | None) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_restricted_link(link: str) -> tuple[Optional[int | str], Optional[int]]:
    """Return (chat_id, msg_id) from supported Telegram links."""
    chat_id: Optional[int | str] = None
    msg_id: Optional[int] = None

    # tg:// links
    if link.startswith("tg://"):
        parsed = urlparse(link)
        q = parse_qs(parsed.query)

        # tg://openmessage?user_id=...&message_id=...
        chat_id = _to_int(q.get("user_id", [None])[0])
        msg_id = _to_int(q.get("message_id", [None])[0])

        # tg://openmessage?chat_id=...&message_id=...
        if chat_id is None:
            chat_id = _to_int(q.get("chat_id", [None])[0])

        # tg://privatepost?channel=...&post=...
        if chat_id is None:
            channel = _to_int(q.get("channel", [None])[0])
            if channel is not None:
                chat_id = int(f"-100{channel}")
        if msg_id is None:
            msg_id = _to_int(q.get("post", [None])[0])

        # fallback: pick two numbers from whole link
        if chat_id is None or msg_id is None:
            nums = [int(x) for x in re.findall(r"\d+", link)]
            if len(nums) == 2:
                chat_id, msg_id = nums

        return chat_id, msg_id

    # https://t.me/c/<id>/<msg>
    m = re.search(r"t\.me/c/(\d+)/(\d+)", link)
    if m:
        return int(f"-100{m.group(1)}"), int(m.group(2))

    # https://t.me/<username>/<msg>
    parts = link.rstrip("/").split("/")
    if len(parts) >= 2 and parts[-1].isdigit():
        msg_id = int(parts[-1])
        chat_id = int(parts[-2]) if parts[-2].isdigit() else parts[-2]

    return chat_id, msg_id


async def save_restricted_msg(
    link: str, client, buffer_storage, target_chat_id: int
) -> None:
    logger.debug("Processing restricted link: %s", link)
    chat_id, msg_id = parse_restricted_link(link)
    if chat_id is None or msg_id is None:
        logger.warning("Cannot parse link: %s", link)
        return

    try:
        msg = await client.get_messages(chat_id, ids=msg_id)
    except ValueError as exc:
        logger.warning("Cannot resolve entity for link %s: %s", link, exc)
        return
    except Exception:
        logger.exception("Failed to fetch message by link: %s", link)
        return

    if not msg:
        logger.warning("Message not found by link: %s", link)
        return

    if msg.media:
        local_path = None
        try:
            local_path = await buffer_storage.buffer_save(msg)
        except Exception:
            logger.exception("Failed to buffer media for link: %s", link)

        if not local_path:
            local_path = buffer_storage.buffer_find(
                msg.id, getattr(msg, "chat_id", None) or chat_id or 0
            )

        try:
            await client.send_file(target_chat_id, msg.media, caption=msg.text or "")
            logger.info(
                "Saved restricted media by link=%s to chat_id=%s", link, target_chat_id
            )
            return
        except ChatForwardsRestrictedError:
            pass

        if local_path and os.path.exists(local_path):
            await client.send_file(target_chat_id, local_path, caption=msg.text or "")
            logger.info(
                "Saved restricted media from buffer by link=%s to chat_id=%s",
                link,
                target_chat_id,
            )
            return

        suffix = (
            Path(getattr(getattr(msg, "file", None), "name", "") or "").suffix or ".bin"
        )
        with tempfile.NamedTemporaryFile("wb", suffix=suffix, delete=True) as tmp:
            await client.download_media(msg.media, file=tmp.name)
            await client.send_file(target_chat_id, tmp.name, caption=msg.text or "")
            logger.info(
                "Saved restricted media via fallback download by link=%s to chat_id=%s",
                link,
                target_chat_id,
            )
        return

    if msg.text:
        await client.send_message(target_chat_id, msg.text)
        logger.info(
            "Saved restricted text by link=%s to chat_id=%s", link, target_chat_id
        )


async def maybe_handle_restricted_link(event, settings, my_id, save_fn):
    """Handle links only in log chat and only for own outgoing messages."""
    if event.chat_id != settings.log_chat_id:
        return False
    if not event.message or not event.message.text:
        return False

    sender_id = getattr(
        getattr(event.message, "sender_id", None), "user_id", None
    ) or getattr(event.message, "sender_id", None)
    is_my_message = bool(getattr(event.message, "out", False)) or sender_id == my_id
    if not is_my_message:
        return False

    text = event.message.text.strip()
    links = TG_RE_HTTP.findall(text) or TG_RE_TG.findall(text)
    if not links:
        return False

    for link in links:
        await save_fn(link)
    return True
