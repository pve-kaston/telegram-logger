#!/usr/bin/env python3
import os
import sys
import asyncio
from pathlib import Path

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

from telethon import TelegramClient

def _require_env(name: str) -> str:
    v = os.getenv(name)
    if not v:
        print(f"Missing required env var: {name}", file=sys.stderr)
        sys.exit(2)
    return v


def main() -> None:
    api_id_raw = _require_env("API_ID")
    api_hash = _require_env("API_HASH")
    session_file = os.getenv("SESSION_FILE", "user.session")

    try:
        api_id = int(api_id_raw)
    except ValueError:
        print("API_ID must be an integer", file=sys.stderr)
        sys.exit(2)

    session_path = Path(session_file).expanduser().resolve()
    session_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Session will be saved to: {session_path}")

    session_name = str(session_path)
    if session_name.endswith(".session"):
        session_name = session_name[: -len(".session")]

    with TelegramClient(session_name, api_id, api_hash) as client:
        client.connect()
        if not client.is_user_authorized():
            print("Not authorized yet. Starting interactive login...")
            client.start()  # will ask for phone/code/2FA password
        else:
            print("Already authorized, session exists.")

    created = Path(session_name + ".session").resolve()
    if created != session_path:
        try:
            if session_path.exists():
                pass
            else:
                created.replace(session_path)
                created = session_path
        except Exception as e:
            print(f"WARNING: Could not rename session file: {e}", file=sys.stderr)

    print(f"Done. Created session file: {created}")


if __name__ == "__main__":
    main()