import time
from typing import Any, Dict, List, Optional
from starlette.concurrency import run_in_threadpool
from app.db import get_supabase_client


async def create_session(*, session_id: str, customer_id: str, env_key: str) -> None:
    now = time.time()
    db = get_supabase_client()
    data = {
        "session_id": session_id,
        "customer_id": customer_id,
        "env_key": env_key,
        "created_at": now,
        "updated_at": now,
        "ended": False,
        "verified_identity": False,
        "verification_attempts": 0,
    }
    await run_in_threadpool(lambda: db.table("call_sessions").insert(data).execute())


async def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    db = get_supabase_client()
    # Using execute() to get response
    resp = await run_in_threadpool(lambda: db.table("call_sessions").select("*").eq("session_id", session_id).execute())
    if resp.data and len(resp.data) > 0:
        return resp.data[0]
    return None


async def touch_session(session_id: str, *, ended: Optional[bool] = None) -> None:
    db = get_supabase_client()
    update: Dict[str, Any] = {"updated_at": time.time()}
    if ended is not None:
        update["ended"] = ended
    await run_in_threadpool(lambda: db.table("call_sessions").update(update).eq("session_id", session_id).execute())


async def set_verification(session_id: str, *, verified_identity: bool, verification_attempts: int) -> None:
    db = get_supabase_client()
    update = {
        "verified_identity": bool(verified_identity),
        "verification_attempts": int(verification_attempts),
        "updated_at": time.time(),
    }
    await run_in_threadpool(lambda: db.table("call_sessions").update(update).eq("session_id", session_id).execute())


async def set_customer_id(session_id: str, *, customer_id: str) -> None:
    db = get_supabase_client()
    update = {
        "customer_id": str(customer_id),
        "updated_at": time.time(),
    }
    await run_in_threadpool(lambda: db.table("call_sessions").update(update).eq("session_id", session_id).execute())


async def append_turn(
    *,
    session_id: str,
    ts: float,
    user_transcript: Optional[str],
    agent_response: Optional[str],
    tool_calls: List[Dict[str, Any]],
) -> None:
    db = get_supabase_client()
    data = {
        "session_id": session_id,
        "ts": ts,
        "user_transcript": user_transcript,
        "agent_response": agent_response,
        "tool_calls": tool_calls,
    }
    await run_in_threadpool(lambda: db.table("call_turns").insert(data).execute())
    await touch_session(session_id)


async def list_sessions() -> List[Dict[str, Any]]:
    db = get_supabase_client()
    resp = await run_in_threadpool(lambda: db.table("call_sessions").select("*").order("updated_at", desc=True).execute())
    return resp.data


async def get_turns(session_id: str) -> List[Dict[str, Any]]:
    db = get_supabase_client()
    resp = await run_in_threadpool(lambda: db.table("call_turns").select("*").eq("session_id", session_id).order("ts", desc=False).execute())
    return resp.data

async def get_turn_count(session_id: str) -> int:
    db = get_supabase_client()
    resp = await run_in_threadpool(lambda: db.table("call_turns").select("*", count="exact", head=True).eq("session_id", session_id).execute())
    return resp.count or 0
