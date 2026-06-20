"""
app/core/config.py

Single source of truth for all configuration.
Pydantic Settings reads from environment variables (and .env file in dev).
Every part of the app imports from here — never use os.getenv() directly.
"""
from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Application ─────────────────────────────────────────────
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    debug: bool = False
    instance_id: str = "api1"        # injected per-container by docker-compose

    # ── PostgreSQL ───────────────────────────────────────────────
    database_url: str
    database_async_url: str

    # ── MongoDB ──────────────────────────────────────────────────
    mongodb_url: str
    mongodb_db_name: str = "devpad_db"

    # ── Redis ────────────────────────────────────────────────────
    redis_url: str = "redis://redis:6379/0"
    cache_ttl: int = 3600

    # ── Kafka ────────────────────────────────────────────────────
    kafka_bootstrap_servers: str = "kafka1:9092,kafka2:9093,kafka3:9094"
    kafka_topic: str = "devpad_events"
    kafka_consumer_group: str = "devpad_consumer_group"

    # ── Elasticsearch ────────────────────────────────────────────
    elasticsearch_url: str = "http://elasticsearch:9200"
    elasticsearch_index: str = "devpad_notes"

    # ── JWT ──────────────────────────────────────────────────────
    secret_key: str
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # ── Email ────────────────────────────────────────────────────
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "noreply@devpad.local"
    smtp_tls: bool = False

    # ── Business Rules ───────────────────────────────────────────
    trash_auto_delete_days: int = 15

    # ── Roles ────────────────────────────────────────────────────
    super_admin_emails: str = ""     # comma-separated

    @property
    def super_admin_email_list(self) -> List[str]:
        """Parse comma-separated super admin emails into a list."""
        return [e.strip() for e in self.super_admin_emails.split(",") if e.strip()]

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"

    @property
    def kafka_brokers_list(self) -> List[str]:
        return [b.strip() for b in self.kafka_bootstrap_servers.split(",")]


@lru_cache()
def get_settings() -> Settings:
    """
    Returns a cached Settings instance.
    lru_cache ensures we only read the .env file once per process.
    Usage: from app.core.config import get_settings; settings = get_settings()
    """
    return Settings()
