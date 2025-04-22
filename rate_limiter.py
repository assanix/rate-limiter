import time
import logging
from pathlib import Path

import redis.asyncio as redis
from fastapi import Request, HTTPException, Depends

from config import settings

logger = logging.getLogger(__name__)

redis_client = None
script_sha = None


async def setup_rate_limiter():
    """Initialize Redis and load Lua script."""
    global redis_client, script_sha

    if not settings.RATE_LIMIT_ENABLED:
        return

    try:
        redis_client = redis.from_url(
            settings.RATE_LIMIT_REDIS_URL,
            decode_responses=True
        )

        # Load Lua script
        script_path = Path(__file__).parent / "token_bucket.lua"
        with open(script_path, "r") as f:
            script = f.read()
            script_sha = await redis_client.script_load(script)
            logger.info(f"Lua script loaded. SHA: {script_sha}")

        logger.info("Rate limiter initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize rate limiter: {e}")
        redis_client = None


async def shutdown_rate_limiter():
    """Close Redis connection."""
    global redis_client
    if redis_client:
        await redis_client.close()
        redis_client = None


def get_client_id(request: Request) -> str:
    """Get client identifier based on configuration."""
    if settings.RATE_LIMIT_CLIENT_IDENTIFIER == "IP":
        return request.client.host
    elif settings.RATE_LIMIT_CLIENT_IDENTIFIER == "API_KEY":
        api_key = request.headers.get(settings.RATE_LIMIT_API_KEY_HEADER, "")
        return api_key if api_key else request.client.host
    return request.client.host


async def rate_limit(request: Request):
    """FastAPI dependency for rate limiting."""
    # Skip if disabled or Redis not available
    if not settings.RATE_LIMIT_ENABLED or not redis_client or not script_sha:
        return

    try:
        # Get client identifier
        client_id = get_client_id(request)
        key = f"ratelimit:{client_id}"
        now = time.time()

        # Execute rate limit check
        result = await redis_client.evalsha(
            script_sha,
            1,  # Number of keys
            key,
            now,
            settings.RATE_LIMIT_BUCKET_CAPACITY,
            settings.RATE_LIMIT_REFILL_RATE_PER_SECOND,
            1  # Consume 1 token
        )

        allowed, remaining, reset_after = result
        allowed = bool(int(allowed))

        if not allowed:
            # Add headers to the response
            headers = {"Retry-After": str(int(reset_after))}

            if settings.RATE_LIMIT_RESPONSE_HEADERS_ENABLED:
                headers.update({
                    "X-RateLimit-Limit": str(settings.RATE_LIMIT_BUCKET_CAPACITY),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(int(now + reset_after))
                })

            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded",
                headers=headers
            )

        # Add headers to request state for middleware to use
        if settings.RATE_LIMIT_RESPONSE_HEADERS_ENABLED:
            request.state.rate_limit_headers = {
                "X-RateLimit-Limit": str(settings.RATE_LIMIT_BUCKET_CAPACITY),
                "X-RateLimit-Remaining": str(int(remaining)),
                "X-RateLimit-Reset": str(int(now))
            }

    except redis.RedisError as e:
        logger.error(f"Redis error: {e}")
        if not settings.RATE_LIMIT_FAIL_OPEN:
            raise HTTPException(status_code=429, detail="Rate limiting unavailable")