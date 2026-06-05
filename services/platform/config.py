from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    ENVIRONMENT: Literal["development", "staging", "production"] = "development"
    DEBUG: bool = False

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://pharmpilot:pharmpilot_dev@localhost:5432/pharmpilot"
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10

    # Redis
    REDIS_URL: str = "redis://:redis_dev@localhost:6379/0"

    # Kafka
    KAFKA_BOOTSTRAP_SERVERS: str = "localhost:9092"
    KAFKA_CONSUMER_GROUP: str = "pharmpilot-core"

    # Security
    SECRET_KEY: str = "CHANGE_IN_PRODUCTION_USE_SECRETS_MANAGER"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    ALGORITHM: str = "HS256"

    # Biometric
    BIOMETRIC_ENCRYPTION_KEY: str = "CHANGE_IN_PRODUCTION"
    FAISS_INDEX_PATH: str = "/app/data/biometric_index"
    ARCFACE_MODEL_PATH: str = "/app/models/arcface_r100.onnx"
    BIOMETRIC_HIGH_CONFIDENCE_THRESHOLD: float = 0.95
    BIOMETRIC_PROBABLE_THRESHOLD: float = 0.80

    # Audio
    WHISPER_MODEL_COUNTER: str = "medium.en"
    WHISPER_MODEL_COUNSELING: str = "large-v3"
    NOISE_CANCELLATION_ENABLED: bool = True
    AUDIO_RETENTION_DAYS: int = 90

    # Drug Database
    DRUG_DB_VENDOR: str = "fdb"
    FDB_API_KEY: str = ""
    MEDSPAN_API_KEY: str = ""

    # ── Identity & localization ───────────────────────────────────────────────
    # Default deployment is Iranian: national code (کد ملی) primary identifier,
    # Jalali display calendar, Persian locale. Set to "american" to switch the
    # primary identifier to SSN and use NCPDP/US insurance by default.
    DEFAULT_IDENTITY_SYSTEM: Literal["iranian", "american"] = "iranian"
    DEFAULT_LOCALE: str = "fa-IR"

    # Iranian insurance / social-security adapters. Empty credentials → sandbox.
    IRANIAN_INSURERS_ENABLED: list[str] = ["salamat", "tamin", "armed_forces", "supplementary"]
    IRAN_SALAMAT_URL: str = ""
    IRAN_SALAMAT_API_KEY: str = ""
    IRAN_TAMIN_URL: str = ""
    IRAN_TAMIN_API_KEY: str = ""
    IRAN_ARMED_FORCES_URL: str = ""
    IRAN_ARMED_FORCES_API_KEY: str = ""
    IRAN_SUPPLEMENTARY_URL: str = ""
    IRAN_SUPPLEMENTARY_API_KEY: str = ""

    # Biometric customer archival (loyalty + medical-safety recognition)
    BIOMETRIC_ARCHIVE_ENABLED: bool = True
    BIOMETRIC_RECOGNITION_THRESHOLD: float = 0.86  # cosine sim for returning-customer match

    # Surescripts
    SURESCRIPTS_SENDER_ID: str = ""
    SURESCRIPTS_PASSWORD: str = ""
    SURESCRIPTS_ENVIRONMENT: str = "test"

    # Clinical Brain
    OPENAI_API_KEY: str = ""        # For LLM consultation synthesis
    ANTHROPIC_API_KEY: str = ""     # Primary LLM for clinical reasoning
    CLINICAL_LLM_MODEL: str = "claude-opus-4-8"

    # Inventory
    MCKESSON_API_KEY: str = ""
    CARDINAL_API_KEY: str = ""
    AMERISOURCE_API_KEY: str = ""

    # Allowed origins for CORS
    ALLOWED_ORIGINS: list[str] = [
        "http://localhost:3001",
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
        "http://localhost:4173",  # vite preview
    ]

    # Qdrant vector store
    QDRANT_URL: str = "http://localhost:6334"

    # Monitoring
    SENTRY_DSN: str = ""
    OTEL_EXPORTER_OTLP_ENDPOINT: str = "http://localhost:4317"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
