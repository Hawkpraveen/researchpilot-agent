import os
from dataclasses import dataclass
from functools import lru_cache

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    log_level: str


@lru_cache
def get_settings() -> Settings:
    load_dotenv()
    host = os.getenv("HOST", "127.0.0.1").strip()
    log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    try:
        port = int(os.getenv("PORT", "8000"))
    except ValueError as error:
        raise ValueError("PORT must be an integer between 1 and 65535.") from error

    if not host:
        raise ValueError("HOST must not be empty.")
    if not 1 <= port <= 65535:
        raise ValueError("PORT must be an integer between 1 and 65535.")
    if log_level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
        raise ValueError("LOG_LEVEL must be a supported Python logging level.")

    return Settings(host=host, port=port, log_level=log_level)
