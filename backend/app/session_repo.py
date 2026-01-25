import time
from typing import Any, Dict, List, Optional

from app.db import get_db


async def create_session(*, session_id: str, customer_id: str, env_key: str) -> None:
    now = time.time()
    db = get_db()
    await db["call_sessions"].insert_one(
        {
            "session_id": session_id,
            "customer_id": customer_id,
            "env_key": env_key,
            "created_at": now,
            "updated_at": now,
            "ended": False,
            "verified_identity": False,
            "verification_attempts": 0,
        }
    )


async def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    db = get_db()
    return await db["call_sessions"].find_one({"session_id": session_id}, {"_id": 0})


async def touch_session(session_id: str, *, ended: Optional[bool] = None) -> None:
    db = get_db()
    update: Dict[str, Any] = {"updated_at": time.time()}
    if ended is not None:
        update["ended"] = ended
    await db["call_sessions"].update_one({"session_id": session_id}, {"$set": update})


async def set_verification(session_id: str, *, verified_identity: bool, verification_attempts: int) -> None:
    db = get_db()
    await db["call_sessions"].update_one(
        {"session_id": session_id},
        {"$set": {"verified_identity": bool(verified_identity), "verification_attempts": int(verification_attempts), "updated_at": time.time()}},
    )


async def append_turn(
    *,
    session_id: str,
    ts: float,
    user_transcript: Optional[str],
    agent_response: Optional[str],
    tool_calls: List[Dict[str, Any]],
) -> None:
    db = get_db()
    await db["call_turns"].insert_one(
        {
            "session_id": session_id,
            "ts": ts,
            "user_transcript": user_transcript,
            "agent_response": agent_response,
            "tool_calls": tool_calls,
        }
    )
    await touch_session(session_id)


async def list_sessions() -> List[Dict[str, Any]]:
    db = get_db()
    cursor = db["call_sessions"].find({}, {"_id": 0}).sort("updated_at", -1)
    return [doc async for doc in cursor]


async def get_turns(session_id: str) -> List[Dict[str, Any]]:
    db = get_db()
    cursor = db["call_turns"].find({"session_id": session_id}, {"_id": 0}).sort("ts", 1)
    return [doc async for doc in cursor]
