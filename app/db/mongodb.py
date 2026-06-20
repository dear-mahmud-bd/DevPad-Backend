"""
app/db/mongodb.py

Async MongoDB client using Motor (async wrapper around PyMongo).

Collections returned here are used directly in services:
  from app.db.mongodb import get_notes_collection
  notes_col = await get_notes_collection()
  await notes_col.insert_one(doc)

Motor operations are non-blocking — they yield to the event loop
while waiting for MongoDB, just like asyncpg does for Postgres.
"""
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.core.config import get_settings

settings = get_settings()

# Module-level client — created once on startup, reused for all requests.
# Motor manages its own connection pool internally.
_client: AsyncIOMotorClient = None
_db: AsyncIOMotorDatabase = None


def get_mongo_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(settings.mongodb_url)
    return _client


def get_mongo_db() -> AsyncIOMotorDatabase:
    global _db
    if _db is None:
        _db = get_mongo_client()[settings.mongodb_db_name]
    return _db


# ── Collection accessors ────────────────────────────────────────
# One function per collection keeps naming consistent across the codebase.

def get_notes_collection():
    return get_mongo_db()["notes"]


def get_activity_logs_collection():
    return get_mongo_db()["activity_logs"]


async def close_mongo_connection():
    """Called on application shutdown to cleanly close the connection pool."""
    global _client
    if _client is not None:
        _client.close()
        _client = None


async def create_mongo_indexes():
    """
    Creates indexes that don't exist yet. Safe to call on every startup
    (MongoDB is idempotent about index creation).

    Indexes are critical for query performance — without them, every
    find() is a full collection scan.
    """
    db = get_mongo_db()

    # notes: most queries filter by user_id and is_deleted
    await db["notes"].create_index([("user_id", 1), ("is_deleted", 1)])
    # notes: TTL index — MongoDB auto-deletes documents when auto_delete_at passes
    await db["notes"].create_index(
        [("auto_delete_at", 1)],
        expireAfterSeconds=0,   # 0 = delete exactly at the timestamp value
        sparse=True,            # only index docs where auto_delete_at exists
    )
    # notes: public token lookup
    await db["notes"].create_index([("public_token", 1)], sparse=True)

    # activity_logs: most queries filter by user_id + timestamp
    await db["activity_logs"].create_index([("user_id", 1), ("timestamp", -1)])
    await db["activity_logs"].create_index([("resource_id", 1), ("timestamp", -1)])
