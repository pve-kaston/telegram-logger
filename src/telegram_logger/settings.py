import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    data_root: Path = Path(os.getenv("DATA_ROOT", Path.cwd() / "src/data"))
    api_id: int
    api_hash: SecretStr

    log_chat_id: int
    ignored_ids: set[int] = Field(default_factory=set)
    listen_outgoing_messages: bool = True

    buffer_all_media: bool = True
    max_buffer_file_size: int = 100 * 1024 * 1024
    media_buffer_ttl_hours: int = 24

    encrypt_deleted_media: bool = False
    deleted_media_key_b64: SecretStr = SecretStr("")

    max_deleted_messages_per_event: int = 100

    save_edited_messages: bool = True
    delete_sent_gifs_from_saved: bool = True
    delete_sent_stickers_from_saved: bool = True

    persist_time_in_days_bot: int = 7
    persist_time_in_days_user: int = 7
    persist_time_in_days_channel: int = 7
    persist_time_in_days_group: int = 7

    health_path: str = "/health"
    health_port: int = 8080
    health_error_window_secs: int = 120
    health_housekeeping_stale_secs: int = 600

    debug_mode: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @computed_field
    @property
    def session_file(self) -> Path:
        return self.data_root / "db/user.session"

    @computed_field
    @property
    def media_dir(self) -> Path:
        return self.data_root / "media"

    @computed_field
    @property
    def media_deleted_dir(self) -> Path:
        return self.data_root / "media_deleted"

    @computed_field
    @property
    def sqlite_db_file(self) -> Path:
        return self.data_root / "db/messages.db"

    def build_sqlite_url(self) -> str:
        return f"sqlite+aiosqlite:///{self.sqlite_db_file}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
