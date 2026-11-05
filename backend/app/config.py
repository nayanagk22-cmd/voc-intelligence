"""Application settings, loaded from environment variables (see .env.example)."""

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

TABLE_NAME = "enriched_unified_data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- OpenAI -------------------------------------------------------------
    openai_api_key: SecretStr = SecretStr("")
    openai_model_fast: str = "gpt-4o-mini"  # router, planner, SQL generation
    openai_model_synth: str = "gpt-4o-mini"  # final narrative (set to gpt-4o for richer prose)
    openai_timeout_s: float = 45.0

    # --- Database: admin connection (seeding only) ---------------------------
    postgres_host: str = "db"
    postgres_port: int = 5432
    postgres_db: str = "voc"
    postgres_user: str = "voc_admin"
    postgres_password: SecretStr = SecretStr("change_me")

    # --- Database: read-only role used for every analytical query -----------
    readonly_db_user: str = "voc_reader"
    readonly_db_password: SecretStr = SecretStr("change_me_reader")

    # --- Seeding --------------------------------------------------------------
    # Path to the .xlsx or .csv file. If it does not exist, the first
    # .xlsx/.csv found in data_dir is used.
    data_file: str = "/data/enriched_unified_data.xlsx"
    data_dir: str = "/data"
    force_reseed: bool = False

    # --- Workflow guardrails ----------------------------------------------------
    max_sql_attempts: int = Field(default=3, ge=1, le=5)
    sql_statement_timeout_ms: int = 10_000
    sql_row_limit: int = 200
    max_question_chars: int = 1_000

    # --- API ------------------------------------------------------------------
    cors_origins: str = "http://localhost:3000"
    log_level: str = "INFO"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
