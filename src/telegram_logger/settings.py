from pathlib import Path
from typing import Final

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    api_id: int
    api_hash: SecretStr
    session_name: str = "db/user.session"

    log_chat_id: int
    ignored_ids: set[int] = Field(default_factory=set)
    listen_outgoing_messages: bool = True

    buffer_all_media: bool = False
    max_buffer_file_size: int = 200 * 1024 * 1024
    media_dir: str = "media"
    media_deleted_dir: str = "media_deleted"
    media_buffer_ttl_hours: int = 24

    encrypt_deleted_media: bool = True
    deleted_media_key_b64: SecretStr = SecretStr("")

    max_deleted_messages_per_event: int = 5

    save_edited_messages: bool = False
    delete_sent_gifs_from_saved: bool = True
    delete_sent_stickers_from_saved: bool = True

    sqlite_db_file: Path = Path("db/messages.db")
    persist_time_in_days_bot: int = 1
    persist_time_in_days_user: int = 1
    persist_time_in_days_channel: int = 1
    persist_time_in_days_group: int = 1

    health_path: str = "/health"
    health_port: int = 8080
    health_error_window_secs: int = 300
    health_housekeeping_stale_secs: int = 900

    debug_mode: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    def build_sqlite_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.sqlite_db_file}"


settings: Final[Settings] = Settings()
