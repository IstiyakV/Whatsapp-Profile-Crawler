from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    port: int = 3030
    default_country_code: str = "+880"
    headless: bool = False

    min_delay_ms: int = 2500
    max_delay_ms: int = 6000
    min_cooldown_ms: int = 3000
    max_cooldown_ms: int = 9000
    max_retries: int = 2

    db_path: str = "data/crawler.db"
    session_dir: str = "sessions"
    images_dir: str = "images"
    logs_dir: str = "logs"
    uploads_dir: str = "uploads"

    @property
    def root(self) -> Path:
        return Path.cwd()

    def path(self, value: str) -> Path:
        return (self.root / value).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()


def ensure_runtime_dirs() -> None:
    settings = get_settings()
    for path in [
        settings.path(settings.db_path).parent,
        settings.path(settings.session_dir),
        settings.path(settings.images_dir),
        settings.path(settings.images_dir) / "debug",
        settings.path(settings.logs_dir),
        settings.path(settings.uploads_dir),
    ]:
        path.mkdir(parents=True, exist_ok=True)
