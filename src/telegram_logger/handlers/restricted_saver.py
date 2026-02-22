import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Optional

from telethon.errors import ChatForwardsRestrictedError

TG_RE_1 = re.compile(r"(?:https:\/\/)?t\.me\/(?:c\/)?[\d\w]+\/[\d]+")
TG_RE_2 = re.compile(r"tg:\/\/openmessage\?user_id=\d+&message_id=\d+")


def parse_restricted_link(link: str) -> tuple[Optional[int | str], Optional[int]]:
    chat_id: Optional[int | str] = None
    msg_id: Optional[int] = None

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
            if len(parts) >= 2 and parts[-1].isdigit():
                msg_id = int(parts[-1])
                chat_id = int(parts[-2]) if parts[-2].isdigit() else parts[-2]

    return chat_id, msg_id


async def save_restricted_msg(link: str, client, buffer_storage, target_chat_id: int) -> None:
    chat_id, msg_id = parse_restricted_link(link)
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
        local_path = None
        try:
            local_path = await buffer_storage.buffer_save(msg)
        except Exception:
            logging.exception("Failed to persist restricted media to buffer for link %s", link)
        if not local_path:
            local_path = buffer_storage.buffer_find(msg.id, getattr(msg, "chat_id", None) or chat_id or 0)

        try:
            await client.send_file(target_chat_id, msg.media, caption=msg.text or "")
        except ChatForwardsRestrictedError:
            if local_path and os.path.exists(local_path):
                await client.send_file(target_chat_id, local_path, caption=msg.text or "")
            else:
                suffix = Path(getattr(getattr(msg, "file", None), "name", "") or "").suffix or ".bin"
                with tempfile.NamedTemporaryFile("wb", suffix=suffix, delete=True) as tmp:
                    await client.download_media(msg.media, file=tmp.name)
                    await client.send_file(target_chat_id, tmp.name, caption=msg.text or "")
    elif msg.text:
        await client.send_message(target_chat_id, msg.text)


async def maybe_handle_restricted_link(event, settings, my_id, save_fn):
    if event.chat_id != settings.log_chat_id:
        return False
    if not event.message or not event.message.text:
        return False
    sender_id = getattr(getattr(event.message, "sender_id", None), "user_id", None) or getattr(
        event.message, "sender_id", None
    )
    if sender_id != my_id:
        return False

    text = event.message.text.strip()
    links = TG_RE_1.findall(text) or TG_RE_2.findall(text)
    if not links:
        return False

    for link in links:
        await save_fn(link)
    return True
