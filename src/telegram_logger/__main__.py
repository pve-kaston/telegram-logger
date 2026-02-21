from telethon import TelegramClient

from telegram_logger.main import run
from telegram_logger.settings import settings


if __name__ == "__main__":
    client = TelegramClient(settings.session_name, settings.api_id, settings.api_hash.get_secret_value())
    with client:
        client.loop.run_until_complete(run(client))
