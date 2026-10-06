from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    jwt_secret: str = Field(min_length=32)
    case_code_secret: str = Field(min_length=32)
    database_url: str = "sqlite:///./whistledrop.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
