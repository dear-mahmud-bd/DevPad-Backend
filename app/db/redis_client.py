"""
app/db/redis_client.py

Redis connection using the async redis-py client.

Used for:
  - Note caching (key: note:{mongo_id}, TTL: CACHE_TTL seconds)
  - Rate limiting (future)

Usage:
  from app.db.redis_client import get_redis
  redis = await get_redis()
  await redis.set("note:abc123", json_str, ex=3600)
  value = await redis.get("note:abc123")
"""
import redis.asyncio as aioredis

from app.core.config import get_settings

settings = get_settings()

_redis: aioredis.Redis = None


async def get_redis() -> aioredis.Redis:
    """Returns the shared Redis connection (created on first call)."""
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,   # returns str, not bytes
        )
    return _redis


async def close_redis():
    """Called on application shutdown."""
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


# ── Cache key helpers ───────────────────────────────────────────
# Centralised here so no string formatting is scattered in services.

def note_cache_key(note_id: str) -> str:
    return f"note:{note_id}"
