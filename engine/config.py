"""Engine settings. Secrets and connection strings come from env only (hard rule 5)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./interval.db"
    admin_token: str = "dev-admin-token"  # override in any non-local run
    world_paused: bool = False  # kill switch: WORLD_PAUSED=1
    seed_path: str = "world/seed/world.yaml"
    heartbeat_enabled: bool = False  # HEARTBEAT_ENABLED=1 in compose
    heartbeat_first_delay_seconds: int = 20
    llm_aggregate_daily_quota: int = 2000  # quota guard threshold base (FR-4.4)


settings = Settings()
