from pathlib import Path
from typing import Final

from pydantic import SecretStr, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    api_id: int
    api_hash: SecretStr
    session_name: str = "db/user.session"

    log_chat_id: int
    ignored_ids: set[int] = Field(default_factory=set)
    
    listen_outgoing_messages: bool = True
    save_edited_messages: bool = False
    delete_sent_gifs_from_saved: bool = True
    delete_sent_stickers_from_saved: bool = True

    file_password: SecretStr = "super secret password"
    max_in_memory_file_size: int = 5 * 1024 * 1024

    media_buffer_ttl_hours: int = 24
    max_buffer_file_size: int = 200 * 1024 * 1024

    sqlite_db_file: Path = "db/messages.db"
    persist_time_in_days_bot: int = 1
    persist_time_in_days_user: int = 1
    persist_time_in_days_channel: int = 1
    persist_time_in_days_group: int = 1

    debug_mode: bool = False
    rate_limit_num_messages: int = 5
    max_deleted_messages_per_event: int = 0
    @property
    def RATE_LIMIT_NUM_MESSAGES(self):
        return self.rate_limit_num_messages


    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    def build_sqlite_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.sqlite_db_file}"


settings: Final[Settings] = Settings()
