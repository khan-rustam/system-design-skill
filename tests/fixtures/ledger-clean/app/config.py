from pydantic import HttpUrl, PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All required settings have no default: the process refuses to start without them."""

    model_config = SettingsConfigDict(env_prefix="LEDGER_")

    database_url: PostgresDsn
    db_pool_max: int = 10

    jwt_public_key: str
    jwt_issuer: str
    jwt_audience: str = "ledger"

    event_bus_url: HttpUrl
    event_bus_token: SecretStr

    bank_api_url: HttpUrl
    bank_api_token: SecretStr
    payout_minimum_minor: int = 1_000


settings = Settings()
