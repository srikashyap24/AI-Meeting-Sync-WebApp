"""Settings. Every secret comes from the environment (or a local .env), never from code."""
import logging
import secrets
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger("meetsync")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./meetsync.db"
    secret_key: str = ""  # signs JWTs
    encryption_key: str = ""  # Fernet key for OAuth tokens at rest
    frontend_url: str = "http://localhost:5173"
    access_token_minutes: int = 60 * 24
    pbkdf2_iterations: int = 600_000

    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = "http://localhost:8000/api/oauth/google/callback"

    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    microsoft_tenant: str = "common"
    microsoft_redirect_uri: str = "http://localhost:8000/api/oauth/microsoft/callback"

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5-5"

    # Used by "Add to my calendar" (natural-language events). Needs a chat model with tool calling.
    openrouter_api_key: str = ""
    openrouter_model: str = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free,nvidia/nemotron-3-ultra-550b-a55b:free,nvidia/nemotron-3.5-lightning:free,google/gemma-4-31b-it:free,google/gemma-4-26b-a4b-it:free,liquid/lfm-2.5-2.6b:free"  # comma-separated; tried in order
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    @model_validator(mode="after")
    def _ephemeral_secrets(self):
        # Fail safe rather than ship a guessable default: without configured secrets we
        # generate random ones for this process (sessions and stored tokens won't survive a restart).
        if not self.secret_key:
            log.warning("SECRET_KEY not set; using an ephemeral key. Logins reset on restart.")
            self.secret_key = secrets.token_urlsafe(48)
        if not self.encryption_key:
            log.warning("ENCRYPTION_KEY not set; using an ephemeral key. Stored OAuth tokens become unreadable on restart.")
            self.encryption_key = "ephemeral:" + secrets.token_urlsafe(32)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
