import os
from typing import Optional
from urllib.parse import urlparse

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

_client: Optional[AsyncIOMotorClient] = None


def _get_mongodb_uri() -> str:
    uri = os.environ.get("MONGODB_URI")
    if not uri:
        raise RuntimeError("MONGODB_URI is not set")
    return uri


def _get_db_name() -> str:
    name = os.environ.get("MONGODB_DB_NAME") or os.environ.get("MONGODB_DATABASE")
    if not name:
        uri = os.environ.get("MONGODB_URI") or ""
        try:
            parsed = urlparse(uri)
            path = (parsed.path or "").lstrip("/")
            if path:
                name = path.split("/", 1)[0]
        except Exception:
            name = None
    if not name:
        name = "bank_abc_voice_agent"
    return name


def get_mongo_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(_get_mongodb_uri(), appname=os.environ.get("MONGODB_APP_NAME") or "voice-agent")
    return _client


def get_db() -> AsyncIOMotorDatabase:
    return get_mongo_client()[_get_db_name()]


async def init_db() -> None:
    db = get_db()
    await db["configs"].create_index("env_key", unique=True)
    await db["call_sessions"].create_index("session_id", unique=True)
    await db["call_sessions"].create_index([("updated_at", -1)])
    await db["call_turns"].create_index([("session_id", 1), ("ts", 1)])
