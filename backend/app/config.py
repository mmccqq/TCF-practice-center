"""Settings, read from the environment (see .env.example).

Nothing secret is defaulted to a usable value: SECRET_KEY has a dev-only
placeholder that the app refuses to start with when ENV=production.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List

# Anchor relative paths to the backend package, not the process's working
# directory: "sqlite:///./tcf.db" would otherwise point at a different file
# depending on where you launched uvicorn or seed.py from, silently giving
# you two databases.
BACKEND_DIR = Path(__file__).resolve().parent.parent

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "development"
    database_url: str = f"sqlite:///{BACKEND_DIR / 'tcf.db'}"

    # HS256 signing key for session JWTs. Generate with:
    #   python -c "import secrets; print(secrets.token_urlsafe(48))"
    secret_key: str = "dev-only-insecure-key-change-me"
    access_token_days: int = 14

    # Google OAuth: leave blank and the Google button reports "not configured"
    # instead of breaking the rest of the app.
    google_client_id: str = ""
    google_client_secret: str = ""

    frontend_origin: str = "http://localhost:5173"
    backend_origin: str = "http://localhost:8000"

    @property
    def cors_origins(self) -> List[str]:
        return [self.frontend_origin]

    @property
    def google_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    # normalise a relative sqlite path the same way, in case .env supplies one
    prefix = "sqlite:///./"
    if s.database_url.startswith(prefix):
        s.database_url = f"sqlite:///{BACKEND_DIR / s.database_url[len(prefix):]}"
    if s.env == "production" and s.secret_key.startswith("dev-only"):
        raise RuntimeError("SECRET_KEY must be set when ENV=production")
    return s
