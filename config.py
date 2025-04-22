from pydantic.v1 import BaseSettings


class Settings(BaseSettings):
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_CLIENT_IDENTIFIER: str = "IP"  # 'IP' or 'API_KEY'
    RATE_LIMIT_API_KEY_HEADER: str = "X-API-Key"
    RATE_LIMIT_BUCKET_CAPACITY: int = 60  # Maximum tokens
    RATE_LIMIT_REFILL_RATE_PER_SECOND: int = 10  # Tokens per second
    RATE_LIMIT_REDIS_URL: str = "redis://localhost:6379"
    RATE_LIMIT_FAIL_OPEN: bool = False  # If true, allow requests if Redis fails
    RATE_LIMIT_RESPONSE_HEADERS_ENABLED: bool = True

    class Config:
        env_file = ".env"


settings = Settings()