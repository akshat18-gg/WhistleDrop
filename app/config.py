from functools import lru_cache

from cryptography.fernet import Fernet
from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REQUIREMENTS = {
    "jwt_secret": "at least 32 characters",
    "case_code_secret": "at least 32 characters",
    "encryption_key": "a Fernet key (.env.example shows how to make one)",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    jwt_secret: str = Field(min_length=32)
    case_code_secret: str = Field(min_length=32)
    encryption_key: str
    database_url: str = "sqlite:///./whistledrop.db"
    evidence_dir: str = "./evidence"

    @field_validator("encryption_key")
    @classmethod
    def must_be_a_fernet_key(cls, value: str) -> str:
        Fernet(value)  # raises ValueError if it isn't 32 bytes of base64
        return value


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        names = sorted({str(error["loc"][0]) for error in exc.errors()})
        problems = "\n".join(f"  {name.upper()} has to be {REQUIREMENTS[name]}" for name in names)
        raise SystemExit(f"Refusing to start. Copy .env.example to .env and fill it in.\n{problems}") from None
