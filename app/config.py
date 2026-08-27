from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://gmaps:gmaps@localhost:5432/gmaps"
    redis_url: str = "redis://localhost:6379/0"
    gmaps_scraper_base_url: str = "http://localhost:8080"

    api_key: str = ""

    # Comma-separated list of origins allowed to call this API from a
    # browser (e.g. "https://dashboard.example.com"). Empty = no cross-origin
    # browser access at all; "*" = any origin (fine for local dev only).
    allowed_origins: str = ""

    poll_interval_seconds: int = 10
    poll_timeout_buffer_seconds: int = 120
    cleanup_engine_job: bool = True

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


settings = Settings()
