import asyncio
from telethon import TelegramClient
from pathlib import Path

from telegram_logger.main import run
from telegram_logger.settings import settings

Path(settings.session_name).parent.mkdir(parents=True, exist_ok=True)


async def main():
    async with TelegramClient(
        settings.session_name,
        settings.api_id,
        settings.api_hash.get_secret_value(),
    ) as client:
        await run(client)


if __name__ == "__main__":
    asyncio.run(main())