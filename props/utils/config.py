from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    railway_database_url: str = ""
    odds_api_key: str = ""
    discord_webhook_url: str = ""
    log_level: str = "INFO"
    # Residential proxy for the PrizePicks scrape so it can run on GitHub Actions
    # (PrizePicks blocks datacenter IPs). Format: http://user:pass@host:port.
    # Empty = scrape direct (works only from a residential IP, e.g. the Mac).
    prizepicks_proxy: str = ""

settings = Settings()
