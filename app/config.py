from __future__ import annotations

import os
from dataclasses import dataclass


def _read_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {value!r}") from exc


def _read_int_alias(name: str, legacy_name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        value = os.getenv(legacy_name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(
            f"{name} or {legacy_name} must be an integer, got {value!r}"
        ) from exc


def _read_ids(name: str) -> set[int]:
    raw = os.getenv(name, "")
    ids: set[int] = set()
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        ids.add(int(chunk))
    return ids


def _read_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.lower() not in {"0", "false", "no", "off"}


@dataclass(frozen=True)
class Settings:
    environment: str
    database_url: str
    session_secret: str
    public_base_url: str
    registration_code_ttl_seconds: int
    smtp_host: str | None
    smtp_port: int
    smtp_username: str | None
    smtp_password: str | None
    smtp_from_email: str
    bitrix_webhook_url: str
    telegram_bot_token: str | None
    telegram_admin_ids: set[int]
    direct_reward_rub: int
    second_level_reward_rub: int
    debug_login_enabled: bool
    debug_agent_email: str
    debug_agent_name: str
    debug_agent_password: str


def load_settings() -> Settings:
    database_url = os.getenv(
        "PRAVBURO_AGENTS_DATABASE_URL",
        "sqlite:///./pravburo_agents.db",
    )
    public_base_url = os.getenv(
        "PRAVBURO_AGENTS_PUBLIC_BASE_URL",
        "http://localhost:8020",
    ).rstrip("/")
    return Settings(
        environment=os.getenv("PRAVBURO_AGENTS_ENV", "development"),
        database_url=database_url,
        session_secret=os.getenv(
            "PRAVBURO_AGENTS_SESSION_SECRET",
            "dev-only-change-me",
        ),
        public_base_url=public_base_url,
        registration_code_ttl_seconds=_read_int(
            "PRAVBURO_AGENTS_REGISTRATION_CODE_TTL_SECONDS",
            900,
        ),
        smtp_host=os.getenv("PRAVBURO_AGENTS_SMTP_HOST"),
        smtp_port=_read_int("PRAVBURO_AGENTS_SMTP_PORT", 587),
        smtp_username=os.getenv("PRAVBURO_AGENTS_SMTP_USERNAME"),
        smtp_password=os.getenv("PRAVBURO_AGENTS_SMTP_PASSWORD"),
        smtp_from_email=os.getenv(
            "PRAVBURO_AGENTS_SMTP_FROM_EMAIL",
            "no-reply@pravburo.local",
        ),
        bitrix_webhook_url=os.getenv(
            "PRAVBURO_AGENTS_BITRIX_WEBHOOK_URL",
            "https://prav-buro.bitrix24.ru/rest/24/pa1x5irnfpbcnh27/",
        ).rstrip("/"),
        telegram_bot_token=os.getenv("PRAVBURO_AGENTS_TELEGRAM_BOT_TOKEN"),
        telegram_admin_ids=_read_ids("PRAVBURO_AGENTS_TELEGRAM_ADMIN_IDS"),
        direct_reward_rub=_read_int_alias(
            "PRAVBURO_AGENTS_FIRST_LEVEL_REWARD_RUB",
            "PRAVBURO_AGENTS_DIRECT_REWARD_RUB",
            13000,
        ),
        second_level_reward_rub=_read_int(
            "PRAVBURO_AGENTS_SECOND_LEVEL_REWARD_RUB",
            5000,
        ),
        debug_login_enabled=_read_bool(
            "PRAVBURO_AGENTS_DEBUG_LOGIN_ENABLED",
            False,
        ),
        debug_agent_email=os.getenv(
            "PRAVBURO_AGENTS_DEBUG_AGENT_EMAIL",
            "test-agent@example.ru",
        ),
        debug_agent_name=os.getenv(
            "PRAVBURO_AGENTS_DEBUG_AGENT_NAME",
            "Тестовый агент",
        ),
        debug_agent_password=os.getenv(
            "PRAVBURO_AGENTS_DEBUG_AGENT_PASSWORD",
            "test12345",
        ),
    )


settings = load_settings()
