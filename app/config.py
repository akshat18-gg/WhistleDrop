from functools import lru_cache

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    jwt_secret: str = Field(min_length=32)
    case_code_secret: str = Field(min_length=32)
    database_url: str = "sqlite:///./whistledrop.db"


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        names = sorted({str(error["loc"][0]).upper() for error in exc.errors()})
        raise SystemExit(
            f"Refusing to start: {', '.join(names)} must be set and at least 32 characters long. "
            "Copy .env.example to .env and fill it in."
        ) from None
