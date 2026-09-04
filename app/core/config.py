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