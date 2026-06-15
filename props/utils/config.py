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
    # Email push for the morning recommended slate (free, optional). For Gmail use
    # an App Password as smtp_password. Empty smtp_user/password = no email sent.
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_to: str = ""          # recipient; defaults to smtp_user when empty
    # Discord slash-command bot (optional, separate service — props/bot/). From
    # the Discord developer portal: app Public Key, Bot Token, Application ID.
    discord_public_key: str = ""
    discord_bot_token: str = ""
    discord_app_id: str = ""

settings = Settings()
