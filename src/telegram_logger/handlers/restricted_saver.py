import re

TG_RE_1 = re.compile(r"(?:https:\/\/)?t\.me\/(?:c\/)?[\d\w]+\/[\d]+")
TG_RE_2 = re.compile(r"tg:\/\/openmessage\?user_id=\d+&message_id=\d+")


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
