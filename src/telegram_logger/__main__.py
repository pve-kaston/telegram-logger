import asyncio
from telethon import TelegramClient

from telegram_logger.main import run
from telegram_logger.settings import settings

def ensure_directories() -> None:
    dirs = {
        settings.session_file.parent,
        settings.sqlite_db_file.parent,
        settings.media_dir,
        settings.media_deleted_dir,
    }

    for path in dirs:
        path.mkdir(parents=True, exist_ok=True)

async def main():
    ensure_directories()
    async with TelegramClient(
        settings.session_file,
        settings.api_id,
        settings.api_hash.get_secret_value(),
    ) as client:
        await run(client)


if __name__ == "__main__":
    asyncio.run(main())