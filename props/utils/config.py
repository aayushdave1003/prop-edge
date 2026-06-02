from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    railway_database_url: str = ""
    odds_api_key: str = ""
    discord_webhook_url: str = ""
    log_level: str = "INFO"

settings = Settings()
