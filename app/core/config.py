from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import computed_field

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore"
    )

    PROJECT_NAME: str = "Picasso API"
    ENVIRONMENT: str = "development"
    API_V1_PREFIX: str = "/api/v1"

    # Railway's Postgres plugin injects a single DATABASE_URL rather than the
    # discrete POSTGRES_* parts below; when it's set, it takes priority. Local
    # dev (.env) keeps using the discrete vars, so both stay optional here and
    # async_database_url/sync_database_url below pick whichever is available.
    DATABASE_URL: str | None = None

    POSTGRES_SERVER: str | None = None
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str | None = None
    POSTGRES_PASSWORD: str | None = None
    POSTGRES_DB: str | None = None

    SECRET_KEY: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15

    # Artwork image storage. Everything here is optional: the app boots with none of it set and
    # the image upload endpoints answer 503. STORAGE_BACKEND picks the provider for NEW uploads;
    # unset means "whichever provider is fully configured". A typo fails at startup on purpose.
    STORAGE_BACKEND: Literal["cloudinary", "r2", "s3"] | None = None

    CLOUDINARY_CLOUD_NAME: str | None = None
    CLOUDINARY_API_KEY: str | None = None
    CLOUDINARY_API_SECRET: str | None = None

    # Generic S3-compatible store. Endpoint and region are optional overrides (AWS needs neither
    # endpoint nor, usually, more than a region; MinIO and others need the endpoint).
    S3_BUCKET: str | None = None
    S3_ACCESS_KEY_ID: str | None = None
    S3_SECRET_ACCESS_KEY: str | None = None
    S3_PUBLIC_BASE_URL: str | None = None
    S3_ENDPOINT_URL: str | None = None
    S3_REGION: str | None = None

    @property
    def cloudinary_configured(self) -> bool:
        return all((self.CLOUDINARY_CLOUD_NAME, self.CLOUDINARY_API_KEY, self.CLOUDINARY_API_SECRET))

    @property
    def s3_configured(self) -> bool:
        return all(
            (self.S3_BUCKET, self.S3_ACCESS_KEY_ID, self.S3_SECRET_ACCESS_KEY, self.S3_PUBLIC_BASE_URL)
        )

    # Cloudflare R2 (all five required).
    R2_ACCOUNT_ID: str | None = None
    R2_ACCESS_KEY_ID: str | None = None
    R2_SECRET_ACCESS_KEY: str | None = None
    R2_BUCKET: str | None = None
    R2_PUBLIC_BASE_URL: str | None = None

    @property
    def r2_configured(self) -> bool:
        return all(
            (
                self.R2_ACCOUNT_ID,
                self.R2_ACCESS_KEY_ID,
                self.R2_SECRET_ACCESS_KEY,
                self.R2_BUCKET,
                self.R2_PUBLIC_BASE_URL,
            )
        )

    # Refresh-token cookie must be Secure everywhere except local dev (PRD §3.9)
    @computed_field
    @property
    def cookie_secure(self) -> bool:
        return self.ENVIRONMENT != "development"

    # This dynamically builds the connection string SQLAlchemy needs
    @computed_field
    @property
    def async_database_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    @computed_field
    @property
    def sync_database_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)
        return f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

settings = Settings()