"""Application configuration."""
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql://user:password@localhost:5432/dbname"

    # Auth
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 24

    # Google Maps
    google_maps_api_key: str = ""

    # Stripe
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    stripe_price_1: str = ""  # 1 credit = 5€
    stripe_price_5: str = ""  # 5 credits = 20€
    stripe_price_10: str = ""  # 10 credits = 40€
    stripe_price_batch_10: str = ""  # 10 credits (batch) = 20€

    # App
    app_name: str = "MileTrack"
    free_tier_km_limit: float = 300.0
    max_km_per_day: float = 350.0

    class Config:
        env_file = "/opt/km-saas/.env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
