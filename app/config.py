"""Application configuration loaded from environment variables."""

from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    app_name: str = "FactoryOps"
    app_env: str = "development"
    debug: bool = True
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    log_level: str = "INFO"
    timezone: str = "Africa/Lagos"

    # AI Provider: Meterless Runtime
    ai_provider: str = "meterless"
    meterless_base_url: str = "http://127.0.0.1:8088/v1"
    meterless_model: str = "meterless/auto"
    meterless_api_key: Optional[str] = None
    meterless_runtime_token: Optional[str] = None

    # Zetaris Data Federation (JDBC & REST)
    zetaris_user: Optional[str] = None
    zetaris_username: Optional[str] = None
    zetaris_password: Optional[str] = None
    zetaris_jdbc_url: str = "jdbc:zetaris:lightning@localhost:10000"
    zetaris_jdbc_driver_class: str = "com.zetaris.lightning.jdbc.LightningDriver"
    zetaris_jdbc_driver_jar: str = "jdbc/ndp-jdbc-driver-2.4.3.1-7eff043-driver.jar"
    zetaris_query_timeout_seconds: int = 30
    zetaris_rest_url: str = "http://127.0.0.1:8888"
    java_home: Optional[str] = None

    # PostgreSQL database connection string
    database_url: str

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
